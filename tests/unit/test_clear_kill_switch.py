"""Tests for scripts/clear_kill_switch.py (FIX-188b systemd-friendly resume)."""
from __future__ import annotations

from pathlib import Path

from capital.kill_switch import KillState, KillSwitch
from core.events import EventBus
from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist
from scripts.clear_kill_switch import main

_LOG = get_logger("test_clear_ks")


def _seed(db_path: Path, state: KillState) -> None:
    store = StateStore(db_path)
    ks = KillSwitch(store, EventBus(), _LOG)
    if state != KillState.INACTIVE:
        ks._persist_state(state, f"test {state.value}", now_ist(), "test")
    store.close()


def _state(db_path: Path) -> str:
    store = StateStore(db_path)
    try:
        return KillSwitch(store, EventBus(), _LOG).status()["state"]
    finally:
        store.close()


def test_clears_soft_kill(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, KillState.SOFT_KILL)
    assert main(["--db", str(db)]) == 0
    assert _state(db) == "INACTIVE"


def test_already_inactive_is_noop(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, KillState.INACTIVE)
    assert main(["--db", str(db)]) == 0
    assert _state(db) == "INACTIVE"


def test_hard_kill_refused_without_force(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, KillState.HARD_KILL)
    assert main(["--db", str(db)]) == 1
    assert _state(db) == "HARD_KILL"  # untouched


def test_hard_kill_cleared_with_force(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, KillState.HARD_KILL)
    assert main(["--db", str(db), "--force"]) == 0
    assert _state(db) == "INACTIVE"


def test_dry_run_does_not_clear(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, KillState.SOFT_KILL)
    assert main(["--db", str(db), "--dry-run"]) == 0
    assert _state(db) == "SOFT_KILL"  # unchanged


def test_missing_db_returns_1(tmp_path):
    assert main(["--db", str(tmp_path / "nope.db")]) == 1
