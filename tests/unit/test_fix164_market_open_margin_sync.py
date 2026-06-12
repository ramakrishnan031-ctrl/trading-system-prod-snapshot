"""
tests/unit/test_fix164_market_open_margin_sync.py

FIX-164: Market-open margin re-sync at 09:15 IST.

Call-count contract for _now_ist inside _run():
  call 1: `now = _now_ist()`                           → 08:30 (before open)
  call 2: `end = _now_ist().timestamp() + wait_sec`    → 08:30 (so end ≈ 09:15)
  call 3: while-loop condition `_now_ist().timestamp()` → 09:15:01 (> end → loop exits, no sleep)
  call 4+: inside sync body (not expected)

Tests avoid patching time.sleep so the test's own time.sleep(0.3) works normally.
"""
from __future__ import annotations

import sys
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from main import _start_market_open_margin_sync_thread


_IST = timezone(timedelta(hours=5, minutes=30))


def _log():
    return logging.getLogger("test_fix164")


def _make_broker(net: float) -> MagicMock:
    broker = MagicMock()
    margins = MagicMock()
    margins.net = net
    broker.get_margins.return_value = margins
    return broker


def _make_fm(total: float) -> MagicMock:
    fm = MagicMock()
    snap = MagicMock()
    snap.total = total
    fm.get_snapshot.return_value = snap
    return fm


def _make_market_windows(is_holiday: bool = False) -> MagicMock:
    mw = MagicMock()
    mw.is_trading_holiday.return_value = is_holiday
    return mw


def _today() -> datetime.date:
    return datetime.now(_IST).date()


def _dt(h: int, m: int, s: int = 0) -> datetime:
    d = _today()
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=_IST)


def _make_now_sequence(*dts: datetime):
    """
    Returns a side_effect callable that yields dts in order.
    After the sequence is exhausted it repeats the last element.
    """
    seq = list(dts)
    idx = [0]

    def _now():
        i = min(idx[0], len(seq) - 1)
        idx[0] += 1
        return seq[i]

    return _now


def _before_open_sequence():
    """
    Sequence for "started before 09:15":
      call 1 → 08:30 (now)
      call 2 → 08:30 (end calculation)
      call 3 → 09:15:01 (while condition: > end ≈ 09:15 → exits immediately)
    No actual sleep() needed.
    """
    return _make_now_sequence(_dt(8, 30), _dt(8, 30), _dt(9, 15, 1))


# ─────────────────────────────────────────────────────────────────────────────

def test_sync_fires_when_started_before_market_open():
    """sync_from_broker is called when the system starts before 09:15."""
    broker = _make_broker(net=10_000.0)
    fm = _make_fm(total=0.0)
    shutdown = threading.Event()
    mw = _make_market_windows()

    with patch("core.time_authority.now_ist", side_effect=_before_open_sequence()):
        _start_market_open_margin_sync_thread(
            broker_adapter=broker, fund_manager=fm, notifier=None,
            mode="LIVE", log=_log(), shutdown_event=shutdown, market_windows=mw,
        )
        time.sleep(0.3)

    fm.sync_from_broker.assert_called_once_with(10_000.0)
    print("  OK: sync_from_broker called when started before 09:15")


def test_sync_skipped_when_started_after_market_open():
    """No sync when system starts at or after 09:15 — startup fetch already used live capital."""
    broker = _make_broker(net=10_000.0)
    fm = _make_fm(total=10_000.0)
    shutdown = threading.Event()
    mw = _make_market_windows()

    with patch("core.time_authority.now_ist", return_value=_dt(9, 30)):
        _start_market_open_margin_sync_thread(
            broker_adapter=broker, fund_manager=fm, notifier=None,
            mode="LIVE", log=_log(), shutdown_event=shutdown, market_windows=mw,
        )
        time.sleep(0.2)

    fm.sync_from_broker.assert_not_called()
    broker.get_margins.assert_not_called()
    print("  OK: no sync when started after 09:15")


def test_notification_sent_when_capital_changes():
    """Notification sent when new_capital differs from startup by > Rs 1."""
    broker = _make_broker(net=10_000.0)
    fm = _make_fm(total=0.0)  # Rs 0 at startup — deposit made after
    shutdown = threading.Event()
    mw = _make_market_windows()
    notifier = MagicMock()

    with patch("core.time_authority.now_ist", side_effect=_before_open_sequence()):
        _start_market_open_margin_sync_thread(
            broker_adapter=broker, fund_manager=fm, notifier=notifier,
            mode="LIVE", log=_log(), shutdown_event=shutdown, market_windows=mw,
        )
        time.sleep(0.3)

    notifier.send.assert_called_once()
    kwargs = notifier.send.call_args[1]
    assert "10,000" in kwargs["body"], f"Expected Rs 10,000 in body: {kwargs['body']}"
    assert "Capital Updated" in kwargs["title"]
    print("  OK: notification sent when capital changes > Rs 1")


def test_no_notification_when_capital_unchanged():
    """No notification when delta ≤ Rs 1 (capital was already correct at startup)."""
    broker = _make_broker(net=10_000.0)
    fm = _make_fm(total=10_000.0)  # same → delta = 0
    shutdown = threading.Event()
    mw = _make_market_windows()
    notifier = MagicMock()

    with patch("core.time_authority.now_ist", side_effect=_before_open_sequence()):
        _start_market_open_margin_sync_thread(
            broker_adapter=broker, fund_manager=fm, notifier=notifier,
            mode="LIVE", log=_log(), shutdown_event=shutdown, market_windows=mw,
        )
        time.sleep(0.3)

    fm.sync_from_broker.assert_called_once_with(10_000.0)
    notifier.send.assert_not_called()
    print("  OK: no notification when capital unchanged")


def test_shutdown_event_aborts_before_sync():
    """Thread exits without syncing if shutdown fires during the sleep wait."""
    broker = _make_broker(net=10_000.0)
    fm = _make_fm(total=0.0)
    shutdown = threading.Event()
    mw = _make_market_windows()

    call_idx = [0]

    def fake_now():
        i = call_idx[0]
        call_idx[0] += 1
        if i == 0:
            return _dt(8, 30)  # now
        if i == 1:
            return _dt(8, 30)  # end calculation
        # Set shutdown before returning — sleep loop will see it
        shutdown.set()
        return _dt(8, 31)  # still before end → loop would continue, but shutdown set

    with patch("core.time_authority.now_ist", side_effect=fake_now):
        _start_market_open_margin_sync_thread(
            broker_adapter=broker, fund_manager=fm, notifier=None,
            mode="LIVE", log=_log(), shutdown_event=shutdown, market_windows=mw,
        )
        time.sleep(0.3)

    fm.sync_from_broker.assert_not_called()
    print("  OK: sync aborted when shutdown fires during sleep")


def test_holiday_skips_sync():
    """Thread exits immediately on trading holidays without calling sync."""
    broker = _make_broker(net=10_000.0)
    fm = _make_fm(total=0.0)
    shutdown = threading.Event()
    mw = _make_market_windows(is_holiday=True)

    with patch("core.time_authority.now_ist", return_value=_dt(8, 30)):
        _start_market_open_margin_sync_thread(
            broker_adapter=broker, fund_manager=fm, notifier=None,
            mode="LIVE", log=_log(), shutdown_event=shutdown, market_windows=mw,
        )
        time.sleep(0.2)

    fm.sync_from_broker.assert_not_called()
    mw.is_trading_holiday.assert_called_once()
    print("  OK: holiday skips sync")


def test_paper_mode_parity():
    """Paper mode follows identical code path; adapter returns static paper_capital."""
    paper_capital = 50_000.0
    broker = _make_broker(net=paper_capital)
    fm = _make_fm(total=paper_capital)  # same → no notification
    shutdown = threading.Event()
    mw = _make_market_windows()

    with patch("core.time_authority.now_ist", side_effect=_before_open_sequence()):
        _start_market_open_margin_sync_thread(
            broker_adapter=broker, fund_manager=fm, notifier=None,
            mode="PAPER", log=_log(), shutdown_event=shutdown, market_windows=mw,
        )
        time.sleep(0.3)

    fm.sync_from_broker.assert_called_once_with(paper_capital)
    print("  OK: paper mode follows same code path (sync called, delta=0 so no notification)")


def test_broker_exception_does_not_crash_thread():
    """Broker failure is caught; thread exits gracefully without propagating."""
    broker = MagicMock()
    broker.get_margins.side_effect = RuntimeError("broker down")
    fm = _make_fm(total=0.0)
    shutdown = threading.Event()
    mw = _make_market_windows()

    with patch("core.time_authority.now_ist", side_effect=_before_open_sequence()):
        _start_market_open_margin_sync_thread(
            broker_adapter=broker, fund_manager=fm, notifier=None,
            mode="LIVE", log=_log(), shutdown_event=shutdown, market_windows=mw,
        )
        time.sleep(0.3)

    fm.sync_from_broker.assert_not_called()  # exception raised before this call
    print("  OK: broker exception caught; thread exits gracefully")


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_sync_fires_when_started_before_market_open,
        test_sync_skipped_when_started_after_market_open,
        test_notification_sent_when_capital_changes,
        test_no_notification_when_capital_unchanged,
        test_shutdown_event_aborts_before_sync,
        test_holiday_skips_sync,
        test_paper_mode_parity,
        test_broker_exception_does_not_crash_thread,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            import traceback
            print(f"  FAIL {t.__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
