"""
tests/unit/test_config_snapshotter.py — W0 (daily-report redesign foundation).

Covers core/config_snapshotter.py + the config_snapshots schema (v41):
  * canonical JSON is key-order stable → hash is stable;
  * snapshot_config idempotency: same config twice → 1 row; changed → 2 rows;
  * config_json round-trips (json.loads back to an equal dict);
  * a real load_all() config snapshots to exactly one row for the day (the
    "one startup writes exactly one snapshot" integration case + the real proof).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.config_loader import load_all
from core.config_snapshotter import (
    config_to_canonical_json,
    hash_config_json,
    snapshot_config,
)
from core.state_store import EXPECTED_SCHEMA_VERSION, StateStore

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _REPO_ROOT / "config"


def _fresh_store(tmp_path) -> StateStore:
    return StateStore(tmp_path / "cfgsnap.db")


def _count(store: StateStore) -> int:
    row = store.fetch_one("SELECT COUNT(*) AS n FROM config_snapshots")
    return int(row["n"])


# ── schema (v41, pure addition) ───────────────────────────────────────────────

def test_schema_is_v41_with_config_snapshots_table(tmp_path):
    store = _fresh_store(tmp_path)
    try:
        assert EXPECTED_SCHEMA_VERSION == 41
        assert store.get_schema_version() == 41
        tbl = store.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='config_snapshots'")
        assert tbl is not None
        # index on snapshot_date present (report's date-range query path)
        idx = store.fetch_all("PRAGMA index_list('config_snapshots')")
        assert any(r["name"] == "idx_config_snapshots_date" for r in idx)
    finally:
        store.close()


# ── canonical json + hash (pure) ──────────────────────────────────────────────

def test_canonical_json_is_key_order_stable():
    a = {"b": 1, "a": {"y": 2, "x": 3}}
    b = {"a": {"x": 3, "y": 2}, "b": 1}   # same content, different insertion order
    assert config_to_canonical_json(a) == config_to_canonical_json(b)
    assert hash_config_json(config_to_canonical_json(a)) == \
           hash_config_json(config_to_canonical_json(b))


def test_hash_is_deterministic_and_changes_with_content():
    j1 = config_to_canonical_json({"k": 1})
    j2 = config_to_canonical_json({"k": 2})
    assert hash_config_json(j1) == hash_config_json(config_to_canonical_json({"k": 1}))
    assert hash_config_json(j1) != hash_config_json(j2)
    assert len(hash_config_json(j1)) == 64   # sha256 hex


def test_config_json_round_trips():
    import json
    cfg = {"system": {"trade_type": "INTRADAY"}, "n": 3, "flag": True, "opt": None}
    stored = config_to_canonical_json(cfg)
    assert json.loads(stored) == cfg


# ── idempotency (dict-driven, the work-order matrix) ──────────────────────────

def _snap(store, cfg, *, date="2026-07-01", ts="2026-07-01T08:15:00+05:30"):
    return snapshot_config(
        store, cfg,
        snapshot_date=date, snapshot_ts=ts,
        account_id="LFL836", mode="PAPER", trade_type="INTRADAY",
    )


def test_same_config_twice_writes_one_row(tmp_path):
    store = _fresh_store(tmp_path)
    try:
        cfg = {"a": 1, "b": {"c": 2}}
        first = _snap(store, cfg)
        second = _snap(store, cfg)   # identical → idempotent skip
        assert first is not None
        assert second is None
        assert _count(store) == 1
    finally:
        store.close()


def test_changed_config_same_day_writes_second_row(tmp_path):
    store = _fresh_store(tmp_path)
    try:
        first = _snap(store, {"a": 1})
        second = _snap(store, {"a": 2}, ts="2026-07-01T09:30:00+05:30")  # config changed
        assert first is not None and second is not None
        assert first != second
        assert _count(store) == 2
        rows = store.fetch_all(
            "SELECT config_hash FROM config_snapshots WHERE snapshot_date='2026-07-01'")
        assert len({r["config_hash"] for r in rows}) == 2   # two distinct hashes
    finally:
        store.close()


def test_same_hash_different_day_writes_row_per_day(tmp_path):
    store = _fresh_store(tmp_path)
    try:
        _snap(store, {"a": 1}, date="2026-07-01")
        _snap(store, {"a": 1}, date="2026-07-02")   # same config, next day → new row
        assert _count(store) == 2
    finally:
        store.close()


def test_stored_row_columns_are_populated(tmp_path):
    store = _fresh_store(tmp_path)
    try:
        import json
        cfg = {"system": {"trade_type": "INTRADAY"}}
        new_id = _snap(store, cfg)
        row = store.fetch_one(
            "SELECT * FROM config_snapshots WHERE snapshot_id=?", (new_id,))
        assert row["snapshot_date"] == "2026-07-01"
        assert row["mode"] == "PAPER"
        assert row["account_id"] == "LFL836"
        assert row["trade_type"] == "INTRADAY"
        assert row["config_hash"] == hash_config_json(config_to_canonical_json(cfg))
        assert json.loads(row["config_json"]) == cfg   # round-trips from the DB
    finally:
        store.close()


def test_bad_app_config_type_raises(tmp_path):
    store = _fresh_store(tmp_path)
    try:
        with pytest.raises(TypeError):
            _snap(store, 12345)   # not a dict, not an AppConfig
    finally:
        store.close()


# ── integration: a real load_all() startup writes exactly one snapshot ────────

@pytest.mark.skipif(not _CONFIG_DIR.is_dir(), reason="repo config/ dir not present")
def test_real_load_all_snapshots_one_row(tmp_path):
    import json
    app_config = load_all(_CONFIG_DIR)
    store = _fresh_store(tmp_path)
    try:
        new_id = snapshot_config(
            store, app_config,
            snapshot_date="2026-07-01",
            snapshot_ts="2026-07-01T08:15:00+05:30",
            account_id="LFL836", mode="LIVE",
            trade_type=app_config.system.trade_type,
        )
        assert new_id is not None
        assert _count(store) == 1

        row = store.fetch_one(
            "SELECT * FROM config_snapshots WHERE snapshot_id=?", (new_id,))
        # config_json is parseable and carries the FULL resolved config
        parsed = json.loads(row["config_json"])
        assert isinstance(parsed, dict)
        for key in ("system", "broker_costs", "scoring", "file_hashes"):
            assert key in parsed, f"expected {key!r} in the resolved-config snapshot"
        assert parsed["system"]["trade_type"] == app_config.system.trade_type
        assert row["mode"] == "LIVE"

        # a re-run with the identical config is an idempotent no-op
        again = snapshot_config(
            store, app_config,
            snapshot_date="2026-07-01",
            snapshot_ts="2026-07-01T08:16:00+05:30",
            account_id="LFL836", mode="LIVE",
            trade_type=app_config.system.trade_type,
        )
        assert again is None
        assert _count(store) == 1
    finally:
        store.close()
