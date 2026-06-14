"""Tests for O5: scripts/db_retention.py (DB-level row retention)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.state_store import StateStore
from scripts.db_retention import DEFAULT_RETENTION, run_retention

LOG = logging.getLogger("test_db_retention")
REF_DATE = "2026-06-14"  # reference "today" for deterministic cutoffs


@pytest.fixture()
def store(tmp_path: Path) -> StateStore:
    return StateStore(db_path=tmp_path / "test.db")


def _days_ago(n: int) -> str:
    return (datetime.strptime(REF_DATE, "%Y-%m-%d") - timedelta(days=n)).strftime(
        "%Y-%m-%d"
    )


def _insert_candle(store, date_str: str, token: int) -> None:
    ts = f"{date_str}T09:30:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO candles
               (symbol, instrument_token, ts, interval_sec, open, high, low,
                close, volume)
               VALUES ('TEST', ?, ?, 60, 100, 101, 99, 100.5, 1000)""",
            (token, ts),
        )


def _insert_fm_ledger(store, date_str: str) -> None:
    ts = f"{date_str}T10:00:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO fm_ledger
               (ts, entry_type, amount, bucket, balance_before, balance_after)
               VALUES (?, 'INIT', 0.0, 'intraday', 10000.0, 10000.0)""",
            (ts,),
        )


def _insert_recon_log(store, date_str: str) -> None:
    ts = f"{date_str}T15:45:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO reconciliation_log
               (ts, check_name, tier, symbol, trade_id, description,
                action_taken, success)
               VALUES (?, 'TEST', 'COSMETIC', 'TEST', NULL, 'd', 'a', 1)""",
            (ts,),
        )


def _count(store, table: str) -> int:
    return int(store.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")["n"])


def test_prunes_rows_older_than_window(store):
    # candles default window = 90d. Insert one well past, one inside.
    _insert_candle(store, _days_ago(200), token=1)  # should be pruned
    _insert_candle(store, _days_ago(10), token=2)   # should survive
    assert _count(store, "candles") == 2

    res = run_retention(store=store, date_iso=REF_DATE, log=LOG)

    assert res["candles"] == 1
    assert _count(store, "candles") == 1
    surviving = store.fetch_one("SELECT instrument_token AS t FROM candles")
    assert surviving["t"] == 2


def test_cutoff_is_strict_less_than(store):
    # A row exactly AT the cutoff date is kept (delete uses `< cutoff`).
    window = DEFAULT_RETENTION["candles"][1]
    _insert_candle(store, _days_ago(window), token=1)      # == cutoff -> kept
    _insert_candle(store, _days_ago(window + 1), token=2)  # < cutoff  -> pruned

    res = run_retention(store=store, date_iso=REF_DATE, log=LOG)

    assert res["candles"] == 1
    assert _count(store, "candles") == 1


def test_fm_ledger_uses_long_window(store):
    # fm_ledger default = 365d. A 200-day-old row must survive.
    _insert_fm_ledger(store, _days_ago(200))   # inside 365d -> kept
    _insert_fm_ledger(store, _days_ago(400))   # beyond 365d -> pruned

    res = run_retention(store=store, date_iso=REF_DATE, log=LOG)

    assert res["fm_ledger"] == 1
    assert _count(store, "fm_ledger") == 1


def test_substr_ts_column_table(store):
    # reconciliation_log has no stored `date` column; uses substr(ts,1,10).
    _insert_recon_log(store, _days_ago(200))  # default 180d -> pruned
    _insert_recon_log(store, _days_ago(30))   # kept

    res = run_retention(store=store, date_iso=REF_DATE, log=LOG)

    assert res["reconciliation_log"] == 1
    assert _count(store, "reconciliation_log") == 1


def test_dry_run_deletes_nothing(store):
    _insert_candle(store, _days_ago(200), token=1)
    _insert_candle(store, _days_ago(300), token=2)

    res = run_retention(store=store, date_iso=REF_DATE, log=LOG, dry_run=True)

    assert res["candles"] == 2          # reports what WOULD be deleted
    assert _count(store, "candles") == 2  # but nothing actually removed


def test_override_changes_window(store):
    _insert_candle(store, _days_ago(50), token=1)  # inside default 90d
    # Override candles to 30d -> the 50-day-old row is now prunable.
    res = run_retention(
        store=store, date_iso=REF_DATE, log=LOG, overrides={"candles": 30}
    )
    assert res["candles"] == 1
    assert _count(store, "candles") == 0


def test_failure_isolated_and_counted(store):
    # A bogus policy table fails its DELETE but must not abort the rest.
    _insert_candle(store, _days_ago(200), token=1)
    policy = {
        "candles": ("date", 90),
        "no_such_table": ("date", 90),
    }
    res = run_retention(store=store, date_iso=REF_DATE, log=LOG, policy=policy)

    assert res["candles"] == 1            # real table still pruned
    assert res["no_such_table"] == -1     # failure recorded, not raised
    assert res["_failures"] == 1


def test_vacuum_runs_without_error(store):
    _insert_candle(store, _days_ago(200), token=1)
    res = run_retention(
        store=store, date_iso=REF_DATE, log=LOG, vacuum=True
    )
    assert res["_failures"] == 0
    assert _count(store, "candles") == 0
