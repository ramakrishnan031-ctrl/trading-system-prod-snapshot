"""
tests/unit/test_fix140_price_movement_cancel.py

FIX-140: Cancel ENTRY order if LTP moves too far toward TGT before fill.
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


def _make_monitor(price_movement_cancel_pct: float = 0.70) -> OrderMonitor:
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
        price_movement_cancel_pct=price_movement_cancel_pct,
    )


def _make_entry(
    side: str = "BUY",
    expected_price: float = 100.0,
    tgt_price: float = 106.0,
    sl_price: float = 97.0,
) -> _WatchEntry:
    return _WatchEntry(
        internal_order_id="int_001",
        broker_order_id="brok_001",
        symbol="RELIANCE",
        side=side,
        qty=10,
        expected_price=expected_price,
        placed_at=_now_ist(),
        leg="ENTRY",
        tgt_price=tgt_price,
        sl_price=sl_price,
    )


# ── Feature disabled ─────────────────────────────────────────────────────


def test_disabled_when_pct_zero():
    """price_movement_cancel_pct=0 means check is skipped entirely."""
    mon = _make_monitor(price_movement_cancel_pct=0.0)
    entry = _make_entry()
    mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


def test_skipped_for_non_entry_leg():
    """Exit legs (SL/TGT/EOD) never trigger price-movement cancel."""
    mon = _make_monitor()
    for leg in ("SL", "TGT", "EOD"):
        entry = _make_entry()
        entry.leg = leg
        mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


def test_skipped_when_tgt_price_missing():
    """No tgt_price means check is skipped."""
    mon = _make_monitor()
    entry = _make_entry(tgt_price=0.0)
    mon._check_price_movement_cancel(entry)
    mon._adapter.cancel_order.assert_not_called()


# ── BUY side ─────────────────────────────────────────────────────────────


def test_buy_cancel_when_ltp_moves_past_threshold():
    """BUY: entry=100, tgt=106, ltp=105 -> 83% movement -> cancel (>70%)."""
    mon = _make_monitor(price_movement_cancel_pct=0.70)
    entry = _make_entry(side="BUY", expected_price=100.0, tgt_price=106.0)

    with patch.object(mon, "_ltp_expected_fallback", return_value=105.0):
        mon._check_price_movement_cancel(entry)

    mon._adapter.cancel_order.assert_called_once_with("brok_001")


def test_buy_no_cancel_when_below_threshold():
    """BUY: entry=100, tgt=106, ltp=103 -> 50% movement -> keep (< 70%)."""
    mon = _make_monitor(price_movement_cancel_pct=0.70)
    entry = _make_entry(side="BUY", expected_price=100.0, tgt_price=106.0)

    with patch.object(mon, "_ltp_expected_fallback", return_value=103.0):
        mon._check_price_movement_cancel(entry)

    mon._adapter.cancel_order.assert_not_called()


def test_buy_no_cancel_when_price_moves_against():
    """BUY: price moved down (toward SL, not TGT) -> no cancel."""
    mon = _make_monitor(price_movement_cancel_pct=0.70)
    entry = _make_entry(side="BUY", expected_price=100.0, tgt_price=106.0)

    with patch.object(mon, "_ltp_expected_fallback", return_value=98.0):
        mon._check_price_movement_cancel(entry)

    mon._adapter.cancel_order.assert_not_called()


# ── SELL side ────────────────────────────────────────────────────────────


def test_sell_cancel_when_ltp_moves_past_threshold():
    """SELL: entry=100, tgt=94, ltp=95 -> 83% movement -> cancel (>70%)."""
    mon = _make_monitor(price_movement_cancel_pct=0.70)
    entry = _make_entry(side="SELL", expected_price=100.0, tgt_price=94.0)

    with patch.object(mon, "_ltp_expected_fallback", return_value=95.0):
        mon._check_price_movement_cancel(entry)

    mon._adapter.cancel_order.assert_called_once_with("brok_001")


def test_sell_no_cancel_when_below_threshold():
    """SELL: entry=100, tgt=94, ltp=98 -> 33% movement -> keep (<70%)."""
    mon = _make_monitor(price_movement_cancel_pct=0.70)
    entry = _make_entry(side="SELL", expected_price=100.0, tgt_price=94.0)

    with patch.object(mon, "_ltp_expected_fallback", return_value=98.0):
        mon._check_price_movement_cancel(entry)

    mon._adapter.cancel_order.assert_not_called()


def test_sell_no_cancel_when_price_moves_against():
    """SELL: price moved up (toward SL, not TGT) -> no cancel."""
    mon = _make_monitor(price_movement_cancel_pct=0.70)
    entry = _make_entry(side="SELL", expected_price=100.0, tgt_price=94.0)

    with patch.object(mon, "_ltp_expected_fallback", return_value=102.0):
        mon._check_price_movement_cancel(entry)

    mon._adapter.cancel_order.assert_not_called()


# ── Edge cases ───────────────────────────────────────────────────────────


def test_ltp_fetch_failure_skips_check():
    """If adapter returns ltp=0 (fetch failed), check is skipped."""
    mon = _make_monitor(price_movement_cancel_pct=0.70)
    entry = _make_entry()

    with patch.object(mon, "_ltp_expected_fallback", return_value=0.0):
        mon._check_price_movement_cancel(entry)

    mon._adapter.cancel_order.assert_not_called()


def test_cancel_failure_logged_but_no_crash():
    """If cancel_order fails, log error but don't crash."""
    mon = _make_monitor(price_movement_cancel_pct=0.70)
    mon._adapter.cancel_order.return_value = SimpleNamespace(success=False, reason="timeout")
    entry = _make_entry(side="BUY", expected_price=100.0, tgt_price=106.0)

    with patch.object(mon, "_ltp_expected_fallback", return_value=105.0):
        mon._check_price_movement_cancel(entry)

    mon._adapter.cancel_order.assert_called_once()
    mon._log.error.assert_called()


def test_exact_threshold_triggers_cancel():
    """BUY: entry=100, tgt=110, ltp=107 -> exactly 70% -> cancel."""
    mon = _make_monitor(price_movement_cancel_pct=0.70)
    entry = _make_entry(side="BUY", expected_price=100.0, tgt_price=110.0)

    with patch.object(mon, "_ltp_expected_fallback", return_value=107.0):
        mon._check_price_movement_cancel(entry)

    mon._adapter.cancel_order.assert_called_once()


def test_config_loader_price_movement_cancel_pct():
    """OrderMonitorConfig accepts and validates price_movement_cancel_pct."""
    from core.config_loader import OrderMonitorConfig
    cfg = OrderMonitorConfig(
        poll_interval_sec=2,
        fill_timeout_sec=60,
        price_movement_cancel_pct=0.70,
    )
    assert cfg.price_movement_cancel_pct == 0.70


def test_config_loader_rejects_invalid_pct():
    """price_movement_cancel_pct must be 0-1."""
    from core.config_loader import OrderMonitorConfig
    with pytest.raises(Exception):
        OrderMonitorConfig(
            poll_interval_sec=2,
            fill_timeout_sec=60,
            price_movement_cancel_pct=1.5,
        )


def test_watch_entry_stores_tgt_sl():
    """_WatchEntry stores tgt_price and sl_price."""
    entry = _make_entry(tgt_price=110.0, sl_price=95.0)
    assert entry.tgt_price == 110.0
    assert entry.sl_price == 95.0


def test_track_passes_tgt_sl_to_watch_entry():
    """track() with tgt_price/sl_price stores them on the watch entry."""
    mon = _make_monitor()
    now = _now_ist()
    with patch("broker.order_monitor.today_ist", return_value="2026-06-01"):
        mon.track(
            internal_order_id="int_002",
            broker_order_id="brok_002",
            symbol="INFY",
            side="BUY",
            qty=5,
            expected_price=1500.0,
            placed_at=now,
            leg="ENTRY",
            tgt_price=1530.0,
            sl_price=1485.0,
        )
    composite = mon._internal_to_composite["int_002"]
    entry = mon._watched[composite]
    assert entry.tgt_price == 1530.0
    assert entry.sl_price == 1485.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
