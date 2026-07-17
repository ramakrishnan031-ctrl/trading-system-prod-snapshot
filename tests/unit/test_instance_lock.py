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


def _holder_process(lock_port: int) -> subprocess.Popen:
    """Spawn a REAL second process that takes the lock and holds it until killed.
    Returns once the child has reported the outcome on stdout."""
    src = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(_REPO_ROOT)!r})
        from utils.instance_lock import acquire_instance_lock
        ok, reason = acquire_instance_lock(lock_port={lock_port})
        print("ACQUIRED" if ok else "REFUSED " + reason, flush=True)
        if not ok:
            sys.exit(2)
        time.sleep(120)
    """)
    proc = subprocess.Popen(
        [sys.executable, "-c", src],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    line = proc.stdout.readline().strip()
    assert line == "ACQUIRED", f"holder child failed to acquire: {line!r}"
    return proc


def _reap(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()


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
        holder.kill()
        holder.wait(timeout=10)
        for stream in (holder.stdout, holder.stderr):
            stream.close()

        ok, reason = acquire_instance_lock(lock_port=_PORT_A)
        assert ok is True, f"legitimate restart blocked after a crash: {reason}"

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
        if _LOCK_FILE.exists():
            try:
                _LOCK_FILE.unlink()
            except OSError:
                pass

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
        ok, reason = check_port_available(59999)
        assert ok is True
        assert reason == ""

    def test_occupied_port_returns_false(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 59998))
        server.listen(1)
        try:
            ok, reason = check_port_available(59998)
            assert ok is False
            assert "59998" in reason
            assert "already in use" in reason
        finally:
            server.close()

    def test_checks_correct_host(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 59997))
        server.listen(1)
        try:
            ok, _ = check_port_available(59997, "127.0.0.1")
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
        ok2, _ = check_port_available(59996)
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
