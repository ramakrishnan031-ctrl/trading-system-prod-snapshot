"""Tests for scripts/check_vm_state.py -- the `--reset` flag is GONE (ledger #8b).

Rama's ruling R5(a), 02-Aug-2026, implemented 03-Aug: remove `--reset` entirely,
preserving every read-only diagnostic.

WHY A LOUD BLOCK AND NOT A SILENT DELETION -- this is the property under test.
The flag's worst documented behaviour was that it PRINTED "Kill switch RESET to
INACTIVE" while changing nothing on a running service (`is_active()` reads
in-memory state). Simply deleting the branch would have left that trap intact in
a new form: the operator types `--reset`, sees the kill state print, gets exit 0,
and reads success where nothing happened. Typing a flag is a DELIBERATE act, and
the fail-fast/degrade discriminator says a deliberate act gets a BLOCK. So the
flag must exit NON-ZERO and name the sanctioned path.

  T1  `--reset` is REFUSED (non-zero exit) and points at the sanctioned tools
  T2  it is refused in ANY argv position -- the old code only checked argv[1]
  T3  it MUTATES NOTHING (the killed row survives byte-identical)
  T4  the read-only diagnostics still work -- the other half of R5(a)
  T5  the destructive SQL is gone from the SOURCE, so it cannot be re-added quietly

⛔ These tests drive the script as a SUBPROCESS, because it has no `__main__`
guard (importing it would EXECUTE it). The repo's test-side-effect guards are
IN-PROCESS and therefore do NOT cover a subprocess, so every run below pins
`DB_PATH` to a pytest tmp_path and T0 asserts the live DB path is never used.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_vm_state.py"

KILLED_ROW = ("SOFT_KILL", "circuit_breaker_force_close_15:15", "system")


def _make_db(path: Path) -> None:
    """A minimal DB shaped like production's, carrying an ACTIVE kill."""
    c = sqlite3.connect(str(path))
    c.execute(
        "CREATE TABLE kill_switch_state "
        "(id INTEGER PRIMARY KEY, state TEXT, reason TEXT, triggered_by TEXT)"
    )
    c.execute("INSERT INTO kill_switch_state VALUES (1,?,?,?)", KILLED_ROW)
    c.execute(
        "CREATE TABLE signals (signal_id TEXT, symbol TEXT, status TEXT, "
        "received_at TEXT, triggered_at TEXT)"
    )
    c.execute(
        "CREATE TABLE trades (trade_id TEXT, symbol TEXT, status TEXT, "
        "net_pnl REAL, exit_time TEXT, entry_time TEXT, exit_reason TEXT)"
    )
    c.execute(
        "CREATE TABLE orders (order_id TEXT, trade_id TEXT, order_type TEXT, status TEXT)"
    )
    c.commit()
    c.close()


def _kill_row(path: Path):
    c = sqlite3.connect(str(path))
    try:
        return c.execute(
            "SELECT state, reason, triggered_by FROM kill_switch_state WHERE id=1"
        ).fetchone()
    finally:
        c.close()


def _run(db: Path, *args):
    env = dict(os.environ, DB_PATH=str(db))
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "trading_system.db"
    _make_db(p)
    # T0 -- anti-footgun: the fixture must never hand back the real DB.
    assert "data_store" not in str(p), "test DB must not live in data_store/"
    assert _kill_row(p) == KILLED_ROW
    return p


def test_t1_reset_is_refused_and_names_the_sanctioned_path(db):
    r = _run(db, "--reset")
    assert r.returncode != 0, "a deliberate --reset must BLOCK, not silently no-op"
    blob = r.stdout + r.stderr
    assert "--reset" in blob and "REMOVED" in blob
    # It must redirect, not merely refuse -- both modes, since resume.sh is VM-only.
    assert "deploy/resume.sh" in blob
    assert "scripts/clear_kill_switch.py" in blob


def test_t2_reset_is_refused_in_any_argv_position(db):
    """The removed code matched only argv[1]; the guard must be wider than that."""
    for args in (("--reset",), ("--health", "--reset"), ("--symbol=ABC", "--reset")):
        r = _run(db, *args)
        assert r.returncode != 0, f"--reset not caught in position: {args}"


def test_t3_reset_mutates_nothing(db):
    before = _kill_row(db)
    _run(db, "--reset")
    _run(db, "--health", "--reset")
    assert _kill_row(db) == before == KILLED_ROW, (
        "the kill row changed -- on the OLD code this is exactly what --reset did"
    )


def test_t4_readonly_diagnostics_survive(db):
    """R5(a) removed one flag; it must not have cost the diagnostics."""
    for flag in ("--health", "--signals", "--trades", "--positions", "--orders"):
        r = _run(db, flag)
        assert r.returncode == 0, f"{flag} broke: {r.stderr}"
        assert "Kill switch: state=SOFT_KILL" in r.stdout
    # ...and the no-argument form still reports.
    r = _run(db)
    assert r.returncode == 0
    assert "Kill switch: state=SOFT_KILL" in r.stdout


def test_t5_destructive_sql_is_absent_from_source():
    """A structural pin: the branch cannot be reintroduced without failing here."""
    src = SCRIPT.read_text(encoding="utf-8")
    body = src.split('"""', 2)[2]  # skip the module docstring, which QUOTES the old SQL
    assert "UPDATE kill_switch_state" not in body
    assert "RESET to INACTIVE" not in body
    assert "manual reset via script" not in body
