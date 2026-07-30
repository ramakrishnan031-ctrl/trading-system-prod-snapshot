"""Tests for utils/instance_lock.py -- single-instance enforcement.

X5 (17-Jul-2026). Two instances trading one book double every order; a guard that
wrongly refuses a start is its own outage (main() exits 1, and the unit restarts
on exit 1 -> a restart loop). So BOTH properties are tested here, and both are
tested ACROSS PROCESSES, because that is the only thing production ever faces:

  P1  a second concurrent instance is REFUSED
  P2  a legitimate restart WORKS -- after a clean exit AND after a crash that
      never got to release anything

The old suite tested neither. It drove one process, and test_fails_when_same_pid_alive
asserted that a lock file naming ANY live PID must block the start -- writing
pytest's own PID to prove it. That is the PID-recycling outage written down as a
requirement, so it is inverted below, deliberately.
"""
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import utils.instance_lock as il
from utils.instance_lock import (
    _LOCK_FILE,
    _pid_is_alive,
    acquire_instance_lock,
    check_port_available,
    release_instance_lock,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Ports private to this module, so a stray 5001 listener cannot colour results.
_PORT_A = 59321
_PORT_B = 59322
# 27-Jul-2026: the port-availability probes below used bare 59996-59999, and 59996
# COLLIDED with tests/unit/test_phase17_batch3.py, which binds+listens on it. Moved
# into this module's private range — the class, not just the one instance.
_PORT_FREE = 59323      # must stay UNBOUND for the whole run
_PORT_BUSY1 = 59324
_PORT_BUSY2 = 59325
_PORT_SEQ = 59326


@pytest.fixture(autouse=True)
def _isolate_lock_file(tmp_path, monkeypatch):
    """27-Jul-2026 — give every test its OWN lock path.

    ``_LOCK_FILE`` is a machine-global constant (``/tmp`` or ``%TEMP%``), shared by
    every test here AND by tests/unit/test_phase17_batch3.py, which takes the same
    lock. This module isolated its PORTS (above) and left the AUTHORITATIVE layer-1
    file global — so concurrent or closely-spaced runs contended on it.

    Redirecting the path weakens nothing: P1 and P2 are properties of CONTENTION ON
    WHATEVER PATH IS CONFIGURED, and every participant (this process and the spawned
    holder children, which are told the path explicitly) reads the same patched value.
    Precedent: tests/conftest.py::_isolate_real_sentinels does exactly this for
    sentinels.
    """
    p = tmp_path / "trading-system.lock"
    monkeypatch.setattr(il, "_LOCK_FILE", p)
    monkeypatch.setitem(globals(), "_LOCK_FILE", p)   # this module imported it by value
    yield


def _holder_process(lock_port: int) -> subprocess.Popen:
    """Spawn a REAL second process that takes the lock and holds it until released.
    Returns once the child has reported the outcome on stdout.

    27-Jul-2026 — the child used to ``time.sleep(120)``. It is a bare Popen with no
    job object and no atexit, so ANY run that failed to reap it left a live process
    holding the machine-global lock for up to two minutes — into the NEXT pytest
    invocation, which was then refused by a live PID that differed every run. It now
    blocks on stdin instead, so it dies the moment this process closes the pipe or
    dies itself, on both platforms. It still holds the lock for exactly as long as
    the test needs it, so P1/P2 are tested identically.
    """
    src = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(_REPO_ROOT)!r})
        import utils.instance_lock as il
        from pathlib import Path
        il._LOCK_FILE = Path({str(_LOCK_FILE)!r})   # share the parent's isolated path
        ok, reason = il.acquire_instance_lock(lock_port={lock_port})
        print("ACQUIRED" if ok else "REFUSED " + reason, flush=True)
        if not ok:
            sys.exit(2)
        sys.stdin.read()   # blocks until the parent closes stdin or dies
    """)
    proc = subprocess.Popen(
        [sys.executable, "-c", src],
        stdin=subprocess.PIPE,           # REQUIRED: without a pipe the child inherits
        stdout=subprocess.PIPE,          # pytest's stdin and read() returns instantly
        stderr=subprocess.PIPE, text=True,
    )
    try:
        line = proc.stdout.readline().strip()
        assert line == "ACQUIRED", f"holder child failed to acquire: {line!r}"
    except BaseException:
        # The assert used to fire BEFORE `proc` was bound in the caller, so the
        # caller's `finally: _reap(holder)` never ran and the child leaked.
        _reap(proc)
        raise
    return proc


def _reap(proc: subprocess.Popen) -> None:
    if proc.stdin is not None:
        try:
            proc.stdin.close()       # EOF -> the child exits on its own
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


class TestSingleInstanceAcrossProcesses:
    """The two properties X5 exists for."""

    def setup_method(self):
        release_instance_lock()   # drop anything a prior test left holding

    def teardown_method(self):
        release_instance_lock()

    def test_p1_second_concurrent_instance_is_refused(self):
        # A real second process, exactly as a double-start would look.
        holder = _holder_process(_PORT_A)
        try:
            ok, reason = acquire_instance_lock(lock_port=_PORT_A)
            assert ok is False, "TWO INSTANCES: the second start was allowed"
            assert "Another instance" in reason
            assert str(holder.pid) in reason, "the refusal must name the holder"
        finally:
            _reap(holder)

    def test_p2_restart_after_crash_is_not_blocked(self):
        # The crash path: SIGKILL/TerminateProcess, so release_instance_lock()
        # never runs and the lock file is left behind naming a PID. The kernel
        # drops the lock with the process, so the restart must still work.
        # RED-on-old: the old guard kept the file and consulted the PID, so it
        # refused whenever that PID had been recycled -- and refusing means the
        # unit restart-loops.
        holder = _holder_process(_PORT_A)
        holder.kill()          # deliberately KILL, not close stdin: this is the crash path
        holder.wait(timeout=10)
        for stream in (holder.stdin, holder.stdout, holder.stderr):
            if stream is not None:
                stream.close()

        ok, reason = acquire_instance_lock(lock_port=_PORT_A)
        assert ok is True, f"legitimate restart blocked after a crash: {reason}"

    def test_holder_child_exits_when_its_parent_goes_away(self):
        """27-Jul-2026 — pins the fix for the flake this file used to cause.

        The holder child must not outlive the process that spawned it. It used to
        ``time.sleep(120)``, so an unreaped child held the machine-global lock for
        two minutes and refused the NEXT pytest invocation -- naming a live PID that
        differed every run, which is exactly what the failure looked like.

        RED on the old code for a real reason: with ``time.sleep(120)`` and no stdin
        pipe there is nothing to close, and the child ignores EOF, so the wait below
        raises TimeoutExpired.
        """
        holder = _holder_process(_PORT_A)
        try:
            assert holder.poll() is None, "child should still be alive holding the lock"
            holder.stdin.close()            # exactly what the parent's death does
            holder.wait(timeout=10)         # must exit ON ITS OWN -- not be killed
            assert holder.poll() is not None, "child outlived its parent's stdin"
            # and the lock it held must now be free for a legitimate restart
            ok, reason = acquire_instance_lock(lock_port=_PORT_A)
            assert ok is True, f"lock not released by the departed child: {reason}"
        finally:
            _reap(holder)

    def test_p2_restart_after_clean_exit_works(self):
        ok, _ = acquire_instance_lock(lock_port=_PORT_A)
        assert ok is True
        release_instance_lock()
        ok, reason = acquire_instance_lock(lock_port=_PORT_A)
        assert ok is True, f"restart blocked after a clean exit: {reason}"

    def test_p2_live_but_unrelated_pid_in_file_does_not_block(self):
        # The PID-recycling outage, made deterministic: pytest's own PID is
        # unquestionably alive and unquestionably not a trading instance.
        # RED-on-old: _pid_is_alive() said True, so the start was refused.
        _LOCK_FILE.write_text(str(os.getpid()))
        assert _pid_is_alive(os.getpid())
        ok, reason = acquire_instance_lock(lock_port=_PORT_A)
        assert ok is True, f"a stale PID blocked a legitimate start: {reason}"

    def test_lock_is_not_advisory_only_between_the_two_layers(self):
        # The file lock must refuse the second start on its own, before the port
        # is even considered -- otherwise a different lock_port would let two
        # instances share a book.
        holder = _holder_process(_PORT_A)
        try:
            ok, reason = acquire_instance_lock(lock_port=_PORT_B)   # different port
            assert ok is False, "TWO INSTANCES: a different lock port bypassed the guard"
            assert "Another instance" in reason
        finally:
            _reap(holder)


class TestLockSocket:
    def setup_method(self):
        release_instance_lock()

    def teardown_method(self):
        release_instance_lock()

    def test_lock_socket_does_not_set_so_reuseaddr(self):
        # Measured 17-Jul: a second bind of a LISTENING 127.0.0.1 port is refused
        # on Linux with or without SO_REUSEADDR (EADDRINUSE), but SUCCEEDS on
        # Windows when it is set. It bought nothing on prod and defeated the
        # guard on the dev box, so it is gone.
        # Asserted on the live socket, not on the source text: whether the option
        # is SET is the property, and grepping the source would pass on a comment.
        import utils.instance_lock as il
        ok, _ = acquire_instance_lock(lock_port=_PORT_A)
        assert ok is True
        assert il._lock_socket is not None
        opt = il._lock_socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
        assert not opt, "SO_REUSEADDR is set on the lock socket"

    def test_port_conflict_releases_the_file_lock(self):
        # Refusing on the port must not leave layer 1 held: the next start would
        # then be refused for the wrong reason, forever.
        import utils.instance_lock as il
        squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        squatter.bind(("127.0.0.1", _PORT_B))
        squatter.listen(1)
        try:
            ok, reason = acquire_instance_lock(lock_port=_PORT_B)
            assert ok is False and "already in use" in reason
            assert il._lock_fd is None, "file lock still held after a refused start"
        finally:
            squatter.close()
        ok, _ = acquire_instance_lock(lock_port=_PORT_A)   # unrelated port now works
        assert ok is True


class TestAcquireInstanceLock:
    """Lock-file bookkeeping. The PID it records is informational: it names the
    holder in the operator message, and nothing branches on it."""

    def setup_method(self):
        release_instance_lock()
        # 27-Jul-2026: the unlink that used to live here is GONE. instance_lock.py's
        # own docstring says the lock file "is never unlinked" -- unlinking a path a
        # holder still has open lets the next start lock a FRESH inode and run
        # alongside it, which is the false-PASS direction. test_release_leaves_the_
        # file_in_place asserts that invariant; this setup was breaking it. The
        # per-test tmp path (see _isolate_lock_file) makes the unlink unnecessary:
        # every test already starts with a path that does not exist.

    def teardown_method(self):
        release_instance_lock()

    def test_acquires_when_no_lock_exists(self):
        ok, reason = acquire_instance_lock(lock_port=_PORT_A)
        assert ok is True
        assert reason == ""
        assert _LOCK_FILE.exists()
        assert int(_LOCK_FILE.read_text().strip()) == os.getpid()

    def test_succeeds_when_stale_lock_dead_pid(self):
        _LOCK_FILE.write_text("999999999")
        ok, reason = acquire_instance_lock(lock_port=_PORT_A)
        assert ok is True
        assert reason == ""
        assert int(_LOCK_FILE.read_text().strip()) == os.getpid()

    def test_succeeds_when_lock_has_garbage(self):
        _LOCK_FILE.write_text("not_a_pid")
        ok, reason = acquire_instance_lock(lock_port=_PORT_A)
        assert ok is True
        assert reason == ""

    def test_release_leaves_the_file_in_place(self):
        # Inverted deliberately (was: release unlinks the file). The lock lives
        # on the inode via an open fd, so unlinking a path another instance holds
        # would let the next start lock a FRESH inode and run alongside it. The
        # file is a 64-byte nameplate; the lock is the guard.
        ok, _ = acquire_instance_lock(lock_port=_PORT_A)
        assert ok is True
        release_instance_lock()
        assert _LOCK_FILE.exists()
        ok, _ = acquire_instance_lock(lock_port=_PORT_A)   # and it is reclaimable
        assert ok is True

    def test_release_does_not_disturb_a_file_it_does_not_hold(self):
        _LOCK_FILE.write_text("12345")
        release_instance_lock()
        assert _LOCK_FILE.exists()
        assert _LOCK_FILE.read_text().strip() == "12345"

    def test_release_silent_when_no_lock(self):
        release_instance_lock()  # should not raise

    def test_release_is_idempotent(self):
        acquire_instance_lock(lock_port=_PORT_A)
        release_instance_lock()
        release_instance_lock()  # must not raise or double-close


class TestCheckPortAvailable:
    """GUARD 1: Port conflict detection before binding."""

    def test_available_port_returns_true(self):
        ok, reason = check_port_available(_PORT_FREE)
        assert ok is True
        assert reason == ""

    def test_occupied_port_returns_false(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", _PORT_BUSY1))
        server.listen(1)
        try:
            ok, reason = check_port_available(_PORT_BUSY1)
            assert ok is False
            assert str(_PORT_BUSY1) in reason
            assert "already in use" in reason
        finally:
            server.close()

    def test_checks_correct_host(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", _PORT_BUSY2))
        server.listen(1)
        try:
            ok, _ = check_port_available(_PORT_BUSY2, "127.0.0.1")
            assert ok is False
        finally:
            server.close()


class TestIntegrationMainGuards:
    """Verify both guards integrate correctly with main.py startup flow."""

    def setup_method(self):
        release_instance_lock()

    def teardown_method(self):
        release_instance_lock()

    def test_lock_then_port_check_sequence(self):
        ok1, _ = acquire_instance_lock(lock_port=_PORT_A)
        assert ok1 is True
        ok2, _ = check_port_available(_PORT_SEQ)
        assert ok2 is True
        release_instance_lock()

    def test_lock_prevents_second_acquire(self):
        """Second acquire fails while first holds the lock (same process)."""
        ok1, _ = acquire_instance_lock(lock_port=_PORT_A)
        assert ok1 is True
        ok2, reason = acquire_instance_lock(lock_port=_PORT_A)
        assert ok2 is False
        assert "Another instance" in reason
        release_instance_lock()
