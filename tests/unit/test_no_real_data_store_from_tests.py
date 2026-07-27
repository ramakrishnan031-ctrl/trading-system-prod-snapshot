"""tests/unit/test_no_real_data_store_from_tests.py

27-Jul-2026. The isolation sweep found `data_store/` OPEN: a test could open the
real database. MEASURED on the PC -- data_store/trading_system.db had mtime
27-Jul 14:54, written by that day's own test runs.

⭐ THE REASON IT MATTERS IS NOT THE PC. On the PC that file is scratch. ON THE VM
the same relative path is the LIVE trading database -- real trades, the capital
ledger -- and the suite HAS run on the VM before, which is why
`_isolate_real_sentinels` exists at all.

Guard: tests/conftest.py::_block_real_data_store, at `sqlite3.connect` rather than
at any wrapper, because 10+ modules call it.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_REAL = (_ROOT / "data_store").resolve()
_BLOCKED = "BLOCKED sqlite open of the REAL data_store"


def test_opening_the_real_live_db_is_blocked():
    with pytest.raises(RuntimeError, match=_BLOCKED):
        sqlite3.connect(str(_REAL / "trading_system.db"))


def test_the_relative_path_form_is_blocked_too():
    """Production code opens it as a bare relative path, which is how a test picks
    it up by accident in the first place."""
    with pytest.raises(RuntimeError, match=_BLOCKED):
        sqlite3.connect("data_store/trading_system.db")


def test_the_read_only_URI_form_is_blocked():
    """A ro open still creates -shm/-wal sidecars next to a WAL database, so
    'just reading' the live DB is not side-effect free."""
    with pytest.raises(RuntimeError, match=_BLOCKED):
        sqlite3.connect("file:data_store/trading_system.db?mode=ro", uri=True)


def test_the_analytics_sibling_is_blocked():
    with pytest.raises(RuntimeError, match=_BLOCKED):
        sqlite3.connect(str(_REAL / "analytics.db"))


def test_a_subdirectory_of_data_store_is_blocked():
    """T2-style throwaway stores live under data_store/<subdir>/. They belong in
    tmp_path; the guard says so rather than trusting the naming."""
    with pytest.raises(RuntimeError, match=_BLOCKED):
        sqlite3.connect(str(_REAL / "t2_proof_x" / "t2_proof.db"))


# ── it must not block legitimate use ──────────────────────────────────────────

def test_tmp_path_still_works(tmp_path):
    c = sqlite3.connect(str(tmp_path / "scratch.db"))
    c.execute("CREATE TABLE t (a INTEGER)")
    c.close()


def test_in_memory_still_works():
    sqlite3.connect(":memory:").close()


def test_a_real_StateStore_on_tmp_path_still_builds(tmp_path):
    """The whole suite builds StateStores on tmp_path; if the guard broke that it
    would be worse than the hole it closes."""
    from core.state_store import StateStore
    s = StateStore(tmp_path / "t.db")
    try:
        assert s.fetch_one("SELECT 1 AS n")["n"] == 1
    finally:
        s.close()


def test_the_opt_in_fixture_actually_opens_it(allow_real_data_store, tmp_path):
    """B4: a legitimate need opts in EXPLICITLY, in the test signature, rather than
    via a hidden exclusion list. Proven by opening a path under the real
    data_store that this test creates and removes itself."""
    p = _REAL / "_guard_optin_probe.db"
    try:
        sqlite3.connect(str(p)).close()
        assert p.exists()
    finally:
        for f in (p, Path(str(p) + "-wal"), Path(str(p) + "-shm")):
            if f.exists():
                f.unlink()
