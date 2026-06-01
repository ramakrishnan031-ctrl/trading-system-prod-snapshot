"""
tests/unit/test_fix140_price_movement_cancel.py

FIX-141: Cancel ENTRY order if remaining R:R drops below min_pending_rr.
Replaces FIX-140 fixed-percentage logic with strategy-adaptive R:R check.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from broker.order_monitor import OrderMonitor, _WatchEntry


_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist():
    return datetime.now(_IST)


def _make_monitor(min_pending_rr: float = 1.0) -> OrderMonitor:
    adapter = MagicMock()
    adapter.cancel_order.return_value = SimpleNamespace(success=True, reason="")
    adapter.get_quote.return_value = None
    osm = MagicMock()
    osm.transition.return_value = True
    bus = MagicMock()
    log = MagicMock()
    return OrderMonitor(
        adapter=adapter,
        state_machine=osm,
        bus=bus,
        logger=log,
        poll_interval_sec=2,
        fill_timeout_sec=9999,
        min_pending_rr=min_pending_rr,
    )


def _make_long_entry(
    entry_price: float = 100.0,
    sl_price: float = 97.0,
    tgt_price: float = 106.0,
) -> _WatchEntry:
    """LONG: entry=100, sl=97, tgt=106 → risk=3, initial_reward=6 (2:1 R:R)."""
    return _WatchEntry(
        internal_order_id="int_long",
        broker_order_id="brok_long",
        symbol="RELIANCE",
        side="BUY",
        qty=10,
        expected_price=entry_price,
        placed_at=_now_ist(),
        leg="ENTRY",
        tgt_price=tgt_price,
        sl_price=sl_price,
    )


def _make_short_entry(
    entry_price: float = 100.0,
    sl_price: float = 103.0,
    tgt_price: float = 94.0,
) -> _WatchEntry:
    """SHORT: entry=100, sl=103, tgt=94 → risk=3, initial_reward=6 (2:1 R:R)."""
    return _WatchEntry(
        internal_order_id="int_short",
        broker_order_id="brok_short",
        symbol="INFY",
        side="SELL",
        qty=10,
        expected_price=entry_price,
        placed_at=_now_ist(),
        leg="ENTRY",
        tgt_price=tgt_price,
        sl_price=sl_price,
    )


# ── Feature disabled ─────────────────────────────────────────────────────


def test_disabled_when_min_rr_zero():
    """min_pending_rr=0 disables the check entirely."""
    mon = _make_monitor(min_pending_rr=0.0)
    entry = _make_long_entry()
    mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


def test_skipped_for_non_entry_leg():
    """Exit legs (SL/TGT/EOD) are never checked."""
    mon = _make_monitor()
    for leg in ("SL", "TGT", "EOD"):
        entry = _make_long_entry()
        entry.leg = leg
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


def test_skipped_when_tgt_missing():
    """tgt_price=0 → check skipped."""
    mon = _make_monitor()
    entry = _make_long_entry(tgt_price=0.0)
    mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


def test_skipped_when_sl_missing():
    """sl_price=0 → check skipped (can't compute risk_distance)."""
    mon = _make_monitor()
    entry = _make_long_entry(sl_price=0.0)
    mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


# ── LONG / BUY side ──────────────────────────────────────────────────────


def test_long_no_cancel_when_rr_above_threshold():
    """
    LONG: entry=100, sl=97, tgt=106, ltp=102.5
    remaining_reward = 106 - 102.5 = 3.5
    risk_distance = 100 - 97 = 3
    pending_rr = 3.5/3 ≈ 1.17 >= 1.0 → no cancel
    """
    mon = _make_monitor(min_pending_rr=1.0)
    entry = _make_long_entry(entry_price=100.0, sl_price=97.0, tgt_price=106.0)
    with patch.object(mon, "_ltp_expected_fallback", return_value=102.5):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


def test_long_cancel_when_rr_below_threshold():
    """
    LONG: entry=100, sl=97, tgt=106, ltp=103.6
    remaining_reward = 106 - 103.6 = 2.4
    risk_distance = 100 - 97 = 3
    pending_rr = 2.4/3 = 0.8 < 1.0 → cancel
    """
    mon = _make_monitor(min_pending_rr=1.0)
    entry = _make_long_entry(entry_price=100.0, sl_price=97.0, tgt_price=106.0)
    with patch.object(mon, "_ltp_expected_fallback", return_value=103.6):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_called_once_with("brok_long")


def test_long_cancel_when_ltp_at_tgt():
    """
    LONG: ltp already at TGT → remaining_reward=0 → pending_rr=0 → cancel.
    """
    mon = _make_monitor(min_pending_rr=1.0)
    entry = _make_long_entry(entry_price=100.0, sl_price=97.0, tgt_price=106.0)
    with patch.object(mon, "_ltp_expected_fallback", return_value=106.0):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_called_once()


def test_long_cancel_when_ltp_past_tgt():
    """
    LONG: ltp above TGT → remaining_reward<0 → pending_rr<0 < threshold → cancel.
    """
    mon = _make_monitor(min_pending_rr=1.0)
    entry = _make_long_entry(entry_price=100.0, sl_price=97.0, tgt_price=106.0)
    with patch.object(mon, "_ltp_expected_fallback", return_value=107.0):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_called_once()


def test_long_no_cancel_when_price_moves_against():
    """LONG: ltp below entry (price dropped) → remaining_reward > tgt-entry → no cancel."""
    mon = _make_monitor(min_pending_rr=1.0)
    entry = _make_long_entry(entry_price=100.0, sl_price=97.0, tgt_price=106.0)
    with patch.object(mon, "_ltp_expected_fallback", return_value=98.0):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


# ── SHORT / SELL side ────────────────────────────────────────────────────


def test_short_no_cancel_when_rr_above_threshold():
    """
    SHORT: entry=100, sl=103, tgt=94, ltp=97.5
    remaining_reward = 97.5 - 94 = 3.5
    risk_distance = 103 - 100 = 3
    pending_rr = 3.5/3 ≈ 1.17 >= 1.0 → no cancel
    """
    mon = _make_monitor(min_pending_rr=1.0)
    entry = _make_short_entry(entry_price=100.0, sl_price=103.0, tgt_price=94.0)
    with patch.object(mon, "_ltp_expected_fallback", return_value=97.5):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


def test_short_cancel_when_rr_below_threshold():
    """
    SHORT: entry=100, sl=103, tgt=94, ltp=96.4
    remaining_reward = 96.4 - 94 = 2.4
    risk_distance = 103 - 100 = 3
    pending_rr = 2.4/3 = 0.8 < 1.0 → cancel
    """
    mon = _make_monitor(min_pending_rr=1.0)
    entry = _make_short_entry(entry_price=100.0, sl_price=103.0, tgt_price=94.0)
    with patch.object(mon, "_ltp_expected_fallback", return_value=96.4):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_called_once_with("brok_short")


def test_short_no_cancel_when_price_moves_against():
    """SHORT: ltp above entry (price rose) → remaining_reward > 6 → no cancel."""
    mon = _make_monitor(min_pending_rr=1.0)
    entry = _make_short_entry(entry_price=100.0, sl_price=103.0, tgt_price=94.0)
    with patch.object(mon, "_ltp_expected_fallback", return_value=101.0):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


# ── Guard: risk_distance = 0 ─────────────────────────────────────────────


def test_zero_risk_distance_skipped():
    """If sl_price == entry_price, risk_distance=0 → skip (no div-by-zero)."""
    mon = _make_monitor(min_pending_rr=1.0)
    entry = _make_long_entry(entry_price=100.0, sl_price=100.0, tgt_price=106.0)
    with patch.object(mon, "_ltp_expected_fallback", return_value=105.0):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


def test_negative_risk_distance_skipped():
    """SL on wrong side (sl > entry for LONG) → negative risk_distance → skip."""
    mon = _make_monitor(min_pending_rr=1.0)
    entry = _make_long_entry(entry_price=100.0, sl_price=103.0, tgt_price=106.0)
    with patch.object(mon, "_ltp_expected_fallback", return_value=105.0):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


# ── LTP fetch failure ────────────────────────────────────────────────────


def test_ltp_fetch_failure_skips_check():
    """LTP=0 (adapter fetch failed) → check skipped, no cancel."""
    mon = _make_monitor(min_pending_rr=1.0)
    entry = _make_long_entry()
    with patch.object(mon, "_ltp_expected_fallback", return_value=0.0):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


# ── Strategy-adaptive threshold ─────────────────────────────────────────


def test_low_threshold_only_cancels_in_extreme_cases():
    """
    min_pending_rr=0.5 is a low threshold — only cancel when severely degraded.
    LONG: entry=100, sl=97, tgt=106, ltp=104.6
    remaining_reward = 106 - 104.6 = 1.4
    risk_distance = 100 - 97 = 3
    pending_rr = 1.4/3 ≈ 0.47 < 0.5 → cancel
    """
    mon = _make_monitor(min_pending_rr=0.5)
    entry = _make_long_entry(entry_price=100.0, sl_price=97.0, tgt_price=106.0)
    with patch.object(mon, "_ltp_expected_fallback", return_value=104.6):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_called_once()


def test_low_threshold_no_cancel_when_still_ok():
    """
    min_pending_rr=0.5 — don't cancel when pending R:R is 0.8 (above threshold).
    LONG: entry=100, sl=97, tgt=106, ltp=103.6
    remaining_reward = 106 - 103.6 = 2.4
    risk_distance = 3
    pending_rr = 0.8 >= 0.5 → no cancel
    """
    mon = _make_monitor(min_pending_rr=0.5)
    entry = _make_long_entry(entry_price=100.0, sl_price=97.0, tgt_price=106.0)
    with patch.object(mon, "_ltp_expected_fallback", return_value=103.6):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


def test_strategy_adaptive_wider_rr_absorbs_more_drift():
    """
    Strategy with 2.5 R:R absorbs more absolute drift vs 1.5 R:R at same threshold.
    1.5 R:R trade: entry=100, sl=98, tgt=103, ltp=101.6
      remaining_reward = 103 - 101.6 = 1.4; risk = 2; pending_rr = 0.7 < 1.0 → cancel
    2.5 R:R trade: entry=100, sl=98, tgt=105, ltp=101.6
      remaining_reward = 105 - 101.6 = 3.4; risk = 2; pending_rr = 1.7 >= 1.0 → no cancel
    """
    mon = _make_monitor(min_pending_rr=1.0)

    tight_rr = _WatchEntry(
        internal_order_id="tight", broker_order_id="b_tight", symbol="X",
        side="BUY", qty=1, expected_price=100.0, placed_at=_now_ist(),
        leg="ENTRY", tgt_price=103.0, sl_price=98.0,
    )
    wide_rr = _WatchEntry(
        internal_order_id="wide", broker_order_id="b_wide", symbol="Y",
        side="BUY", qty=1, expected_price=100.0, placed_at=_now_ist(),
        leg="ENTRY", tgt_price=105.0, sl_price=98.0,
    )

    with patch.object(mon, "_ltp_expected_fallback", return_value=101.6):
        mon._check_price_movement_cancel(tight_rr)
        mon._check_price_movement_cancel(wide_rr)

    mon._adapter.cancel_order.assert_called_once_with("b_tight")


# ── Cancel failure handling ──────────────────────────────────────────────


def test_cancel_failure_logged_no_crash():
    """cancel_order fails → error logged, no crash, untrack not called."""
    mon = _make_monitor(min_pending_rr=1.0)
    mon._adapter.cancel_order.return_value = SimpleNamespace(success=False, reason="timeout")
    entry = _make_long_entry()
    with patch.object(mon, "_ltp_expected_fallback", return_value=103.6):
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_called_once()
    mon._log.error.assert_called()


# ── Config validation ────────────────────────────────────────────────────


def test_config_loader_min_pending_rr_in_entry_gate():
    """EntryGateConfig accepts min_pending_rr field."""
    from core.config_loader import EntryGateConfig
    cfg = EntryGateConfig(
        slippage_buffer=2.0,
        min_pending_rr=1.0,
    )
    assert cfg.min_pending_rr == 1.0


def test_config_loader_min_pending_rr_default_zero():
    """EntryGateConfig defaults min_pending_rr to 0 (disabled)."""
    from core.config_loader import EntryGateConfig
    cfg = EntryGateConfig(slippage_buffer=2.0)
    assert cfg.min_pending_rr == 0.0


def test_order_monitor_config_no_price_movement_cancel_pct():
    """OrderMonitorConfig no longer has price_movement_cancel_pct."""
    from core.config_loader import OrderMonitorConfig
    cfg = OrderMonitorConfig(poll_interval_sec=2, fill_timeout_sec=60)
    assert not hasattr(cfg, "price_movement_cancel_pct")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
