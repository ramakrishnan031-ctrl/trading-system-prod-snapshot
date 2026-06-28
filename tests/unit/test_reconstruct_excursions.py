"""
tests/unit/test_reconstruct_excursions.py — MFE/MAE Option B (post-EOD reconstruction).

Covers:
  * The datetime-safe compare in StateStore.compute_trade_excursions — the
    ROOT-CAUSE regression: space-format naive candle ts vs ISO-T +05:30 trade
    times must now MATCH (a lexical BETWEEN matched zero). LONG + SHORT signs.
  * The reconstruct_one outcome taxonomy: WRITTEN / SKIPPED_UNRECONSTRUCTABLE /
    FAILED, and the hard-guard identity written+skipped+failed == examined.
  * Idempotency (INSERT OR REPLACE), fetch-if-missing, select_trades scoping,
    and the excursion_reconstruction_runs audit DAO.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from core.time_authority import ist_timezone
from scripts.reconstruct_excursions import (
    FAILED,
    SKIPPED,
    WOULD_FETCH,
    WRITTEN,
    reconstruct_one,
    run,
    select_trades,
)
from tests.unit.test_order_reconciler import _make_store

LOG = logging.getLogger("test_reconstruct_excursions")
_DAY = "2026-06-25"
_TS = f"{_DAY}T10:00:00+05:30"


# ─────────────────────────────────────────────────────────────────────────────
# seed helpers
# ─────────────────────────────────────────────────────────────────────────────

def _seed_signal(store, signal_id, symbol):
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,"
            "received_at,expires_at,status,fingerprint,fingerprint_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (signal_id, symbol, "SC", "st", _TS, _TS, _TS, "PROCESSED",
             f"fp_{signal_id}", _DAY),
        )


def _seed_trade(store, trade_id, signal_id, *, symbol, direction, entry_price,
                entry_time, exit_time, status="CLOSED"):
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO trades (trade_id,signal_id,symbol,direction,strategy,qty_planned,"
            "qty_filled,entry_target_price,entry_actual_price,sl_initial,tgt_initial,"
            "margin_reserved,risk_amount,created_at,status,order_protocol,updated_at,"
            "entry_time,exit_time,exit_reason,net_pnl) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, signal_id, symbol, direction, "st", 10, 10, entry_price,
             entry_price, entry_price * 0.98, entry_price * 1.03, 200.0, 20.0, _TS,
             status, "LIMIT_TRIPLE", _TS, entry_time, exit_time, "TGT_HIT", 50.0),
        )


def _candle(store, symbol, hhmmss, o, h, l, c, *, token=111, day=_DAY):
    # NB: SPACE-format naive ts — exactly what the 15:40 backfill writes.
    store.insert_candle(
        symbol=symbol, instrument_token=token, ts=f"{day} {hhmmss}",
        interval_sec=60, open_=o, high=h, low=l, close=c, volume=1000, is_synthetic=0,
    )


def _excursion_row(store, trade_id):
    return store.fetch_one("SELECT * FROM trade_excursions WHERE trade_id = ?", (trade_id,))


def _trade_row(store, trade_id):
    return store.fetch_one(
        "SELECT trade_id, symbol, direction, entry_actual_price, entry_time, exit_time "
        "FROM trades WHERE trade_id = ?", (trade_id,))


# ─────────────────────────────────────────────────────────────────────────────
# 1. DATETIME-SAFE COMPARE — the root-cause regression
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_datetime_safe_long(tmp_path: Path):
    """Space-format candles vs ISO-T +05:30 trade times must MATCH (LONG)."""
    store = _make_store(tmp_path)
    _seed_signal(store, "S1", "DYCL")
    _seed_trade(store, "T1", "S1", symbol="DYCL", direction="LONG", entry_price=377.6,
                entry_time=f"{_DAY}T10:08:31.368342+05:30",
                exit_time=f"{_DAY}T10:13:19.057820+05:30")
    for hh, hi, lo in [("10:09:00", 379.0, 377.0), ("10:11:00", 381.0, 376.0),
                       ("10:13:00", 380.0, 378.5)]:
        _candle(store, "DYCL", hh, o=378, h=hi, l=lo, c=379)

    out = store.compute_trade_excursions("T1")
    assert out is not None, "datetime-safe compare must find the space-format candles"
    assert out["mfe_price"] == 381.0   # LONG MFE = max high
    assert out["mae_price"] == 376.0   # LONG MAE = min low
    assert out["mfe_pct"] > 0          # favourable reads positive
    assert out["mae_pct"] < 0          # adverse reads negative


def test_compute_datetime_safe_short_signs(tmp_path: Path):
    """SHORT excursions are inverted: MFE=min low(+%), MAE=max high(-%)."""
    store = _make_store(tmp_path)
    _seed_signal(store, "S2", "BANDHANBNK")
    _seed_trade(store, "T2", "S2", symbol="BANDHANBNK", direction="SHORT", entry_price=203.48,
                entry_time=f"{_DAY}T10:14:16.012261+05:30",
                exit_time=f"{_DAY}T11:32:16.441441+05:30")
    _candle(store, "BANDHANBNK", "10:20:00", o=203, h=205.0, l=201.0, c=202)
    _candle(store, "BANDHANBNK", "11:00:00", o=202, h=203.0, l=200.0, c=201)

    out = store.compute_trade_excursions("T2")
    assert out is not None
    assert out["mfe_price"] == 200.0   # SHORT MFE = lowest price (best)
    assert out["mae_price"] == 205.0   # SHORT MAE = highest price (worst)
    assert out["mfe_pct"] > 0
    assert out["mae_pct"] < 0


def test_compute_old_lexical_would_have_failed(tmp_path: Path):
    """Guard: the raw lexical BETWEEN the bug used matches zero (proves the fix
    is what makes the datetime-safe path succeed, not coincidental data)."""
    store = _make_store(tmp_path)
    _seed_signal(store, "S1", "DYCL")
    et, xt = f"{_DAY}T10:08:31.368342+05:30", f"{_DAY}T10:13:19.057820+05:30"
    _seed_trade(store, "T1", "S1", symbol="DYCL", direction="LONG", entry_price=377.6,
                entry_time=et, exit_time=xt)
    _candle(store, "DYCL", "10:11:00", o=378, h=381, l=376, c=379)

    lexical = store.fetch_all(
        "SELECT ts FROM candles WHERE symbol=? AND ts>=? AND ts<=?", ("DYCL", et, xt))
    assert lexical == [] or len(lexical) == 0       # old behaviour: zero match
    assert store.compute_trade_excursions("T1") is not None  # new behaviour: found


# ─────────────────────────────────────────────────────────────────────────────
# 2. OUTCOME TAXONOMY
# ─────────────────────────────────────────────────────────────────────────────

def _long_trade_with_candles(store, tid="T1", sid="S1", symbol="DYCL"):
    _seed_signal(store, sid, symbol)
    _seed_trade(store, tid, sid, symbol=symbol, direction="LONG", entry_price=377.6,
                entry_time=f"{_DAY}T10:08:31.368342+05:30",
                exit_time=f"{_DAY}T10:13:19.057820+05:30")
    _candle(store, symbol, "10:11:00", o=378, h=381, l=376, c=379)
    return _trade_row(store, tid)


def test_outcome_written_and_persists(tmp_path: Path):
    store = _make_store(tmp_path)
    trade = _long_trade_with_candles(store)
    outcome, _ = reconstruct_one(store, trade, fetcher=None, dry_run=False, log=LOG)
    assert outcome == WRITTEN
    assert _excursion_row(store, "T1") is not None


def test_outcome_dry_run_writes_nothing(tmp_path: Path):
    store = _make_store(tmp_path)
    trade = _long_trade_with_candles(store)
    outcome, _ = reconstruct_one(store, trade, fetcher=None, dry_run=True, log=LOG)
    assert outcome == WRITTEN          # would-write
    assert _excursion_row(store, "T1") is None  # but no row written


def test_outcome_skipped_null_exit(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "S9", "ACME")
    _seed_trade(store, "T9", "S9", symbol="ACME", direction="LONG", entry_price=100.0,
                entry_time=f"{_DAY}T10:00:00+05:30", exit_time=None)
    outcome, note = reconstruct_one(store, _trade_row(store, "T9"), fetcher=None,
                                    dry_run=False, log=LOG)
    assert outcome == SKIPPED
    assert "missing entry/exit time" in note


def test_outcome_failed_when_no_candles_and_no_fetcher(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "S3", "NOCANDLE")
    _seed_trade(store, "T3", "S3", symbol="NOCANDLE", direction="LONG", entry_price=50.0,
                entry_time=f"{_DAY}T10:00:00+05:30", exit_time=f"{_DAY}T10:30:00+05:30")
    outcome, note = reconstruct_one(store, _trade_row(store, "T3"), fetcher=None,
                                    dry_run=False, log=LOG)
    assert outcome == FAILED
    assert "unavailable" in note


def test_outcome_skipped_sub_minute_when_candles_present(tmp_path: Path):
    """Candles exist for the date but none inside a sub-minute window → permanent
    SKIPPED (distinct note), NOT an alarming FAILED."""
    store = _make_store(tmp_path)
    _seed_signal(store, "S4", "TINY")
    _seed_trade(store, "T4", "S4", symbol="TINY", direction="LONG", entry_price=10.0,
                entry_time=f"{_DAY}T10:09:10+05:30", exit_time=f"{_DAY}T10:09:50+05:30")
    _candle(store, "TINY", "10:09:00", o=10, h=11, l=9, c=10)   # before window
    _candle(store, "TINY", "10:10:00", o=10, h=11, l=9, c=10)   # after window
    outcome, note = reconstruct_one(store, _trade_row(store, "T4"), fetcher=None,
                                    dry_run=False, log=LOG)
    assert outcome == SKIPPED
    assert note.startswith("sub-minute window")  # distinct from the NULL-exit note (J2)


def test_outcome_would_fetch_in_dry_run(tmp_path: Path):
    """R1: candles absent + dry-run → WOULD_FETCH preview (NOT FAILED)."""
    store = _make_store(tmp_path)
    _seed_signal(store, "S6", "ABSENT")
    _seed_trade(store, "T6", "S6", symbol="ABSENT", direction="LONG", entry_price=50.0,
                entry_time=f"{_DAY}T10:00:00+05:30", exit_time=f"{_DAY}T10:30:00+05:30")
    outcome, note = reconstruct_one(store, _trade_row(store, "T6"), fetcher=None,
                                    dry_run=True, log=LOG)
    assert outcome == WOULD_FETCH
    assert _excursion_row(store, "T6") is None  # dry-run writes nothing


def test_fetch_if_missing_then_written(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "S5", "FETCHME")
    _seed_trade(store, "T5", "S5", symbol="FETCHME", direction="LONG", entry_price=100.0,
                entry_time=f"{_DAY}T10:00:00+05:30", exit_time=f"{_DAY}T10:30:00+05:30")
    ist = ist_timezone()

    def fake_fetcher(symbol, date_iso):
        rows = [{
            "date": datetime(2026, 6, 25, 10, 15, 0, tzinfo=ist),
            "open": 100, "high": 105, "low": 98, "close": 103, "volume": 1234,
        }]
        return (222, rows)

    outcome, _ = reconstruct_one(store, _trade_row(store, "T5"), fetcher=fake_fetcher,
                                 dry_run=False, log=LOG)
    assert outcome == WRITTEN
    row = _excursion_row(store, "T5")
    assert row is not None and row["mfe_price"] == 105


# ─────────────────────────────────────────────────────────────────────────────
# 3. RUN LOOP — hard guard + idempotency
# ─────────────────────────────────────────────────────────────────────────────

def test_run_hard_guard_identity(tmp_path: Path):
    store = _make_store(tmp_path)
    # 1 written
    _long_trade_with_candles(store, tid="TW", sid="SW", symbol="WROTE")
    # 1 skipped (null exit)
    _seed_signal(store, "SS", "SKIP")
    _seed_trade(store, "TS", "SS", symbol="SKIP", direction="LONG", entry_price=100.0,
                entry_time=f"{_DAY}T10:00:00+05:30", exit_time=None)
    # 1 failed (no candles, no fetcher)
    _seed_signal(store, "SF", "FAIL")
    _seed_trade(store, "TF", "SF", symbol="FAIL", direction="LONG", entry_price=100.0,
                entry_time=f"{_DAY}T10:00:00+05:30", exit_time=f"{_DAY}T10:30:00+05:30")

    trades = select_trades(store, all_closed=True, date_iso=None)
    stats = run(store, trades, fetcher=None, dry_run=False, log=LOG)
    assert stats["examined"] == 3
    assert stats["written"] == 1 and stats["skipped"] == 1 and stats["failed"] == 1
    assert stats["guard_ok"] is True
    assert stats["written"] + stats["skipped"] + stats["failed"] == stats["examined"]


def test_run_dry_run_accounting(tmp_path: Path):
    """Dry-run preview identity (R1): would_write + would_fetch + skipped + failed == examined."""
    store = _make_store(tmp_path)
    _long_trade_with_candles(store, tid="TW", sid="SW", symbol="WROTE")  # would_write
    _seed_signal(store, "SA", "ABSENT")                                  # would_fetch (no candles)
    _seed_trade(store, "TA", "SA", symbol="ABSENT", direction="LONG", entry_price=100.0,
                entry_time=f"{_DAY}T10:00:00+05:30", exit_time=f"{_DAY}T10:30:00+05:30")
    _seed_signal(store, "SN", "NULLX")                                   # skipped (null exit)
    _seed_trade(store, "TN", "SN", symbol="NULLX", direction="LONG", entry_price=100.0,
                entry_time=f"{_DAY}T10:00:00+05:30", exit_time=None)

    trades = select_trades(store, all_closed=True, date_iso=None)
    stats = run(store, trades, fetcher=None, dry_run=True, log=LOG)
    assert stats["written"] == 1 and stats["would_fetch"] == 1 and stats["skipped"] == 1
    assert stats["failed"] == 0 and stats["guard_ok"] is True
    assert (stats["written"] + stats["would_fetch"] + stats["skipped"] + stats["failed"]
            == stats["examined"])
    assert store.fetch_one("SELECT COUNT(*) AS c FROM trade_excursions")["c"] == 0  # no writes


def test_run_is_idempotent(tmp_path: Path):
    store = _make_store(tmp_path)
    _long_trade_with_candles(store)
    trades = select_trades(store, all_closed=True, date_iso=None)
    run(store, trades, fetcher=None, dry_run=False, log=LOG)
    run(store, trades, fetcher=None, dry_run=False, log=LOG)  # again
    n = store.fetch_one("SELECT COUNT(*) AS c FROM trade_excursions WHERE trade_id='T1'")["c"]
    assert n == 1  # INSERT OR REPLACE — exactly one row


def test_select_trades_daily_keys_on_exit_date(tmp_path: Path):
    store = _make_store(tmp_path)
    _long_trade_with_candles(store)  # exit on _DAY
    _seed_signal(store, "SX", "OTHER")
    _seed_trade(store, "TX", "SX", symbol="OTHER", direction="LONG", entry_price=100.0,
                entry_time="2026-06-24T10:00:00+05:30", exit_time="2026-06-24T10:30:00+05:30")
    daily = select_trades(store, all_closed=False, date_iso=_DAY)
    assert [t["trade_id"] for t in daily] == ["T1"]
    allc = select_trades(store, all_closed=True, date_iso=None)
    assert {t["trade_id"] for t in allc} == {"T1", "TX"}


# ─────────────────────────────────────────────────────────────────────────────
# 4. AUDIT DAO
# ─────────────────────────────────────────────────────────────────────────────

def test_audit_run_dao_roundtrip(tmp_path: Path):
    store = _make_store(tmp_path)
    store.insert_excursion_reconstruction_run(
        run_id="abc123", mode="daily", started_at=_TS, completed_at=_TS,
        trades_examined=5, trades_written=2, trades_skipped_unreconstructable=2,
        trades_failed=1, status="FAILED", notes="x",
    )
    row = store.fetch_one("SELECT * FROM excursion_reconstruction_runs WHERE run_id='abc123'")
    assert row["trades_examined"] == 5
    assert row["trades_skipped_unreconstructable"] == 2
    assert row["status"] == "FAILED"
