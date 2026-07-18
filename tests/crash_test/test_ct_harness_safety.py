"""
tests/crash_test/test_ct_harness_safety.py — the CT harness must be scratch-safe.

The crash tests are destructive by design (they corrupt state, cancel trades, reset
capital). Before 18-Jul-2026 the harness hardcoded the LIVE database as the module
constant ``DB_PATH`` and ``get_db_connection()`` defaulted to WRITABLE — so the
destructive statements ran against production data (reports/crash_test/cleanup_log.jsonl
records real live writes on 05-Jun and 07-Jun 2026).

These tests pin the guard that makes that impossible. The most important one is
``test_live_db_untouched_by_harness_suite``: whatever else changes, the live DB must
come out byte-identical.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.crash_test.ct_utils import (  # noqa: E402
    LIVE_ANALYTICS_DB_PATH,
    LIVE_DB_PATH,
    LiveDatabaseRefused,
    SCRATCH_DIR,
    assert_not_live_db,
    get_db_connection,
    make_scratch_db,
    scratch_db_path,
)


# ── the deleted ambiguous name ───────────────────────────────────────────────

def test_old_ambiguous_db_path_constant_is_gone():
    """``DB_PATH`` (the live DB, handed to writable openers) must no longer exist.

    RED-on-old: the pre-change ct_utils defined DB_PATH = <live trading_system.db>.
    """
    import tests.crash_test.ct_utils as ct

    assert not hasattr(ct, "DB_PATH"), (
        "ct_utils.DB_PATH still exists — the ambiguous live-DB constant is the hazard"
    )
    assert ct.LIVE_DB_PATH.name == "trading_system.db"


def test_import_acquires_no_writable_live_handle():
    """Importing the harness must not open the live DB at all (writable or otherwise).

    Run in a SUBPROCESS: a fresh interpreter is the only honest way to observe
    import-time behaviour, and it keeps importlib.reload out of this process (a
    reload would rebind LiveDatabaseRefused and break every other test's
    ``pytest.raises`` by class identity).
    """
    import subprocess
    import textwrap

    root = Path(__file__).resolve().parent.parent.parent
    script = textwrap.dedent(
        """
        import os, sqlite3, sys
        sys.path.insert(0, sys.argv[1])
        opened = []
        real = sqlite3.connect
        sqlite3.connect = lambda t, *a, **k: (opened.append(str(t)), real(t, *a, **k))[1]
        import tests.crash_test.ct_utils as ct
        sqlite3.connect = real
        live = os.path.normcase(str(ct.LIVE_DB_PATH))
        bad = [o for o in opened if os.path.normcase(o).startswith(live)]
        print("BAD:" + repr(bad))
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        capture_output=True, text=True, cwd=str(root),
    )
    assert out.returncode == 0, f"subprocess failed: {out.stderr}"
    assert "BAD:[]" in out.stdout, f"import opened the live DB: {out.stdout} {out.stderr}"


# ── the hard guard, and the foot-guns it must survive ────────────────────────

def test_guard_refuses_live_db_absolute():
    with pytest.raises(LiveDatabaseRefused):
        assert_not_live_db(LIVE_DB_PATH)


def test_guard_refuses_live_analytics_db():
    with pytest.raises(LiveDatabaseRefused):
        assert_not_live_db(LIVE_ANALYTICS_DB_PATH)


def test_guard_refuses_live_db_via_relative_path(monkeypatch):
    """A relative path that resolves onto the live DB must still be refused."""
    monkeypatch.chdir(LIVE_DB_PATH.parent.parent)
    with pytest.raises(LiveDatabaseRefused):
        assert_not_live_db(Path("data_store") / "trading_system.db")


def test_guard_refuses_live_db_via_dotdot_path():
    weird = LIVE_DB_PATH.parent / ".." / "data_store" / "trading_system.db"
    with pytest.raises(LiveDatabaseRefused):
        assert_not_live_db(weird)


def test_guard_refuses_live_wal_and_shm_siblings():
    """Writing the -wal/-shm sidecars corrupts the live DB just as surely."""
    for suffix in ("-wal", "-shm"):
        with pytest.raises(LiveDatabaseRefused):
            assert_not_live_db(Path(str(LIVE_DB_PATH) + suffix))


def test_guard_refuses_live_db_via_symlink(tmp_path):
    """A symlink pointing at the live DB must be resolved and refused."""
    if not LIVE_DB_PATH.exists():
        pytest.skip("no live DB on this host")
    link = tmp_path / "sneaky.db"
    try:
        link.symlink_to(LIVE_DB_PATH)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host (needs privilege on Windows)")
    with pytest.raises(LiveDatabaseRefused):
        assert_not_live_db(link)


def test_guard_refuses_live_db_via_hardlink():
    """A HARDLINK is the nastier variant — realpath does NOT resolve it, so only the
    samefile() check catches it. Runs where symlinks need privilege (e.g. Windows/NTFS).

    The link is created in SCRATCH_DIR, i.e. on the SAME volume as the live DB
    (os.link cannot cross filesystems, and pytest's tmp_path is often on another one).
    Creating a second name for the live DB's inode does not modify its contents, and
    unlinking that name afterwards removes only the extra name.
    """
    if not LIVE_DB_PATH.exists():
        pytest.skip("no live DB on this host")
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    link = SCRATCH_DIR / "hardlink_probe.db"
    link.unlink(missing_ok=True)
    try:
        os.link(LIVE_DB_PATH, link)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("hardlink creation not supported on this host/filesystem")
    try:
        with pytest.raises(LiveDatabaseRefused):
            assert_not_live_db(link)
    finally:
        link.unlink(missing_ok=True)


def test_guard_refuses_env_override_pointing_at_live(monkeypatch):
    """CT_SCRATCH_DIR is an override the harness supports — it must not be a bypass.

    Patches the resolved SCRATCH_DIR (what the env var produces) rather than
    reloading the module, so LiveDatabaseRefused keeps its class identity.
    """
    import tests.crash_test.ct_utils as ct

    monkeypatch.setattr(ct, "SCRATCH_DIR", LIVE_DB_PATH.parent)
    with pytest.raises(LiveDatabaseRefused):
        ct.scratch_db_path("trading_system.db")


def test_env_override_is_actually_read(monkeypatch):
    """Confirm CT_SCRATCH_DIR really is the override route the test above simulates."""
    import subprocess

    root = Path(__file__).resolve().parent.parent.parent
    env = dict(os.environ, CT_SCRATCH_DIR=str(root / "data_store" / "ct_env_probe"))
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]);"
         "import tests.crash_test.ct_utils as ct; print(ct.SCRATCH_DIR)", str(root)],
        capture_output=True, text=True, cwd=str(root), env=env,
    )
    assert out.returncode == 0, out.stderr
    assert "ct_env_probe" in out.stdout, out.stdout


def test_get_db_connection_writable_refuses_live():
    """The default-writable opener — the actual historical hazard — must refuse."""
    with pytest.raises(LiveDatabaseRefused):
        get_db_connection(readonly=False, db_path=LIVE_DB_PATH)


def test_guard_allows_a_scratch_path():
    p = scratch_db_path("guard_ok.db")
    assert assert_not_live_db(p) == p


# ── capability preserved: the CTs can still be destructive, against scratch ──

def test_harness_can_do_destructive_work_on_scratch():
    """A representative destructive operation must still work — on scratch."""
    p = make_scratch_db("ct_destructive.db")
    assert p.exists() and p.parent == SCRATCH_DIR

    conn = get_db_connection(readonly=False, db_path=p)
    try:
        # Full NOT NULL set + the FK parent — the scratch DB carries the REAL
        # core/schema.sql (constraints included), which is precisely what keeps the
        # destructive tests meaningful.
        conn.execute(
            "INSERT INTO signals (signal_id, symbol, scanner, strategy, triggered_at, "
            " received_at, expires_at, status, fingerprint, fingerprint_date) "
            "VALUES ('sig-x','RELIANCE','gap_go_long','gap_go_long',"
            " '2026-07-18T10:00:00+05:30','2026-07-18T10:00:00+05:30',"
            " '2026-07-18T10:01:00+05:30','PASSED','fp-x','2026-07-18')"
        )
        conn.execute(
            "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, "
            " qty_planned, entry_target_price, sl_initial, tgt_initial, margin_reserved, "
            " risk_amount, created_at, status, order_protocol, updated_at) "
            "VALUES ('ct-x','sig-x','RELIANCE','LONG','gap_go_long',1,100.0,99.0,102.0,"
            " 100.0,1.0,'2026-07-18T10:00:00+05:30','OPEN','CO_PLUS_TGT',"
            " '2026-07-18T10:00:00+05:30')"
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM trades WHERE trade_id='ct-x'").fetchone()[0] == 1
        # the destructive statement cleanup.py used to run against LIVE:
        conn.execute("UPDATE trades SET status='CANCELLED' WHERE status='OPEN'")
        conn.commit()
        assert conn.execute(
            "SELECT status FROM trades WHERE trade_id='ct-x'"
        ).fetchone()[0] == "CANCELLED"
    finally:
        conn.close()


def test_readonly_live_access_still_allowed():
    """Read-only inspection of the live system is the harness's legitimate use."""
    if not LIVE_DB_PATH.exists():
        pytest.skip("no live DB on this host")
    conn = get_db_connection(readonly=True)
    try:
        conn.execute("SELECT 1").fetchone()
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE ct_should_never_exist (x INT)")
    finally:
        conn.close()


# ── the assertion that matters most ──────────────────────────────────────────

def _fingerprint(p: Path):
    if not p.exists():
        return None
    st = p.stat()
    return (st.st_size, st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())


def test_live_db_untouched_by_harness_suite():
    """LIVE-DB UNTOUCHED PROOF: exercising the harness leaves the live DBs byte-identical."""
    before = {p: _fingerprint(p) for p in (LIVE_DB_PATH, LIVE_ANALYTICS_DB_PATH)}

    make_scratch_db("ct_untouched.db")
    conn = get_db_connection(readonly=False, db_path=scratch_db_path("ct_untouched.db"))
    conn.execute("UPDATE trades SET status='CANCELLED'")
    conn.commit()
    conn.close()
    for bad in (LIVE_DB_PATH, LIVE_ANALYTICS_DB_PATH, Path(str(LIVE_DB_PATH) + "-wal")):
        with pytest.raises(LiveDatabaseRefused):
            assert_not_live_db(bad)

    after = {p: _fingerprint(p) for p in (LIVE_DB_PATH, LIVE_ANALYTICS_DB_PATH)}
    for p in before:
        assert before[p] == after[p], f"LIVE DB WAS MODIFIED BY THE HARNESS: {p}"
