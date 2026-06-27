"""
tests/unit/test_sr_detector_backfill.py — SNR-DETECTOR-V1 EOD outcome backfill.

Covers (spec I): join sr_detector_results -> trades by signal_id and fill
actual_fill / actual_result / win_loss / pnl. Terminal trades are finalised;
open trades are left NULL for a later run; a placed-but-no-trade row is NO_TRADE.
"""
from __future__ import annotations

from pathlib import Path

from scripts.sr_detector_backfill import backfill
from tests.unit.test_order_reconciler import _make_store

_DATE = "2026-06-26"
_TS = f"{_DATE}T10:00:00+05:30"


def _seed_signal(store, signal_id, symbol="ACME"):
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,"
            "received_at,expires_at,status,fingerprint,fingerprint_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (signal_id, symbol, "SC", "st", _TS, _TS, _TS, "PROCESSED",
             f"fp_{signal_id}", _DATE),
        )


def _seed_trade(store, trade_id, signal_id, *, status, net_pnl=None,
                exit_reason=None, entry_actual_price=100.0, symbol="ACME"):
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO trades (trade_id,signal_id,symbol,direction,strategy,qty_planned,"
            "qty_filled,entry_target_price,entry_actual_price,sl_initial,tgt_initial,"
            "margin_reserved,risk_amount,created_at,status,order_protocol,updated_at,"
            "exit_reason,net_pnl) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, signal_id, symbol, "LONG", "st", 10, 10, 100.0,
             entry_actual_price, 98.0, 103.0, 200.0, 20.0, _TS, status,
             "LIMIT_TRIPLE", _TS, exit_reason, net_pnl),
        )


def _insert_sr(store, signal_id, symbol="ACME"):
    store.insert_sr_detector_result({
        "signal_id": signal_id, "symbol": symbol, "ts": _TS, "mode": "live",
        "strategy": "st", "direction": "LONG", "score": 60, "intended_entry": 100.0,
        "actual_fill": None, "nearest_resistance_zone": None, "nearest_support_zone": None,
        "dist_to_resistance_pct": None, "dist_to_support_pct": None,
        "resistance_confidence": "HIGH", "support_confidence": "NONE",
        "confluence_evidence": "{}", "breakout_volume": None, "flags": "[]",
        "would_wait_for_retest": 0, "proposed_retest_entry": None,
        "proposed_retest_sl": None, "structure_status": "OK",
        "detector_version": "snr-v1", "created_at": _TS,
    })


def _outcome(store, signal_id):
    return store.fetch_one(
        "SELECT actual_fill, actual_result, win_loss, pnl "
        "FROM sr_detector_results WHERE signal_id = ?", (signal_id,))


def test_backfill_fills_closed_winner(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "S1")
    _seed_trade(store, "T1", "S1", status="CLOSED", net_pnl=50.0,
                exit_reason="TGT_HIT", entry_actual_price=100.5)
    _insert_sr(store, "S1")

    stats = backfill(store, _DATE)
    assert stats["filled"] == 1
    row = _outcome(store, "S1")
    assert row["actual_result"] == "TGT_HIT"
    assert row["win_loss"] == "WIN"
    assert row["pnl"] == 50.0
    assert row["actual_fill"] == 100.5


def test_backfill_marks_loser(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "S2")
    _seed_trade(store, "T2", "S2", status="CLOSED", net_pnl=-30.0, exit_reason="SL_HIT")
    _insert_sr(store, "S2")
    backfill(store, _DATE)
    assert _outcome(store, "S2")["win_loss"] == "LOSS"


def test_backfill_skips_open_trade_for_later(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "S3")
    _seed_trade(store, "T3", "S3", status="OPEN")
    _insert_sr(store, "S3")
    stats = backfill(store, _DATE)
    assert stats["skipped_open"] == 1
    assert _outcome(store, "S3")["actual_result"] is None   # left for a later run


def test_backfill_no_trade_is_terminal(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "S4")
    _insert_sr(store, "S4")                 # no trade row at all
    backfill(store, _DATE)
    row = _outcome(store, "S4")
    assert row["actual_result"] == "NO_TRADE" and row["win_loss"] == "NO_FILL"


def test_backfill_failed_trade_is_no_fill(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "S5")
    _seed_trade(store, "T5", "S5", status="FAILED")
    _insert_sr(store, "S5")
    backfill(store, _DATE)
    assert _outcome(store, "S5")["win_loss"] == "NO_FILL"


def test_backfill_is_idempotent(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "S6")
    _seed_trade(store, "T6", "S6", status="CLOSED", net_pnl=10.0, exit_reason="TGT_HIT")
    _insert_sr(store, "S6")
    first = backfill(store, _DATE)
    second = backfill(store, _DATE)
    assert first["filled"] == 1
    assert second["considered"] == 0 and second["filled"] == 0   # nothing left
