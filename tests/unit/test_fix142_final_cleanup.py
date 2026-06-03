"""
tests/unit/test_fix142_final_cleanup.py

FIX-142: Final cleanup — smart_tgt configurable failures, Gemini log review,
candle_store docstring, B.2/H5/L6/A.5 verified-already items.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── L8: SmartTgtManager configurable max_modify_failures ─────────────────


class TestSmartTgtMaxFailures:

    def _make_mgr(self, max_failures: int = 3):
        from orders.smart_tgt_manager import SmartTgtManager
        adapter = MagicMock()
        store = MagicMock()
        store.get_all_smart_tgt_states.return_value = []
        candle = MagicMock()
        log = MagicMock()
        return SmartTgtManager(
            adapter=adapter,
            state_store=store,
            candle_store=candle,
            logger=log,
            enabled=False,
            max_modify_failures=max_failures,
        )

    def test_default_is_3(self):
        mgr = self._make_mgr()
        assert mgr._max_modify_failures == 3

    def test_custom_value(self):
        mgr = self._make_mgr(max_failures=5)
        assert mgr._max_modify_failures == 5

    def test_floor_at_1(self):
        mgr = self._make_mgr(max_failures=0)
        assert mgr._max_modify_failures == 1

    def test_fires_critical_at_threshold(self):
        mgr = self._make_mgr(max_failures=2)
        cb = MagicMock()
        mgr._on_critical_failure = cb
        mgr._maybe_fire_critical("t1", "RELIANCE", 2, "timeout")
        cb.assert_called_once()

    def test_no_fire_below_threshold(self):
        mgr = self._make_mgr(max_failures=5)
        cb = MagicMock()
        mgr._on_critical_failure = cb
        mgr._maybe_fire_critical("t1", "RELIANCE", 4, "timeout")
        cb.assert_not_called()

    def test_config_field_exists(self):
        from core.config_loader import SmartTgtConfig
        cfg = SmartTgtConfig(
            enabled=True,
            trigger_pct=0.005,
            step_pct=0.003,
            volume_dependent_trails=False,
            max_modify_failures=5,
        )
        assert cfg.max_modify_failures == 5

    def test_config_field_default(self):
        from core.config_loader import SmartTgtConfig
        cfg = SmartTgtConfig(
            enabled=True,
            trigger_pct=0.005,
            step_pct=0.003,
            volume_dependent_trails=False,
        )
        assert cfg.max_modify_failures == 3


# ── I.2: CandleStore get_candles docstring ───────────────────────────────


def test_candle_store_docstring_mentions_order():
    from data.candle_store import CandleStore
    doc = CandleStore.get_candles.__doc__
    assert "newest-last" in doc.lower() or "chronological" in doc.lower()


# ── B.2: ShadowTracker _check_hit bid/ask=0 already falls back to LTP ───


def _make_inning(direction: str, entry: float, sl: float, tgt: float) -> "Inning":
    from orders.shadow_tracker import Inning
    from datetime import datetime, timezone, timedelta
    _IST = timezone(timedelta(hours=5, minutes=30))
    return Inning(
        inning_number=2, trade_id="t1", symbol="TEST",
        direction=direction, entry_price=entry,
        entry_ts=datetime(2026, 6, 1, 10, 0, 0, tzinfo=_IST),
        sl_price=sl, tgt_price=tgt,
        exit_price=None, exit_ts=None, exit_reason=None,
        duration_sec=None, pnl_pct=None, pnl_per_share=None,
        is_real=False,
    )


def test_check_hit_bid_ask_zero_uses_ltp():
    """When bid=0 and ask=0, _check_hit uses LTP for SL check."""
    from orders.shadow_tracker import _check_hit
    ing = _make_inning("LONG", 100.0, 97.0, 106.0)
    assert _check_hit(ing, ltp=96.0, bid=0.0, ask=0.0) == "SL"
    assert _check_hit(ing, ltp=98.0, bid=0.0, ask=0.0) is None
    assert _check_hit(ing, ltp=106.0, bid=0.0, ask=0.0) == "TGT"


def test_check_hit_short_bid_ask_zero_uses_ltp():
    from orders.shadow_tracker import _check_hit
    ing = _make_inning("SHORT", 100.0, 103.0, 94.0)
    assert _check_hit(ing, ltp=104.0, bid=0.0, ask=0.0) == "SL"
    assert _check_hit(ing, ltp=101.0, bid=0.0, ask=0.0) is None
    assert _check_hit(ing, ltp=94.0, bid=0.0, ask=0.0) == "TGT"


# ── H5: SL/TGT legs already tracked by order_monitor ────────────────────


def test_exit_legs_exempt_from_fill_timeout():
    """SL/TGT/EOD legs skip the fill timeout (designed to remain open)."""
    from broker.order_monitor import _WatchEntry
    from datetime import datetime, timezone, timedelta

    _IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(_IST)
    old = now - timedelta(seconds=300)

    for leg in ("SL", "TGT", "EOD"):
        entry = _WatchEntry(
            internal_order_id=f"int_{leg}",
            broker_order_id=f"brok_{leg}",
            symbol="RELIANCE",
            side="SELL",
            qty=10,
            expected_price=100.0,
            placed_at=old,
            leg=leg,
        )
        mon = MagicMock()
        mon._fill_timeout = 60
        from broker.order_monitor import OrderMonitor
        OrderMonitor._check_fill_timeout(mon, entry, now)
        mon._adapter.cancel_order.assert_not_called()


# ── L6: KillSwitch.resume already has args ───────────────────────────────


def test_kill_switch_resume_requires_args():
    """resume() requires reason and resumed_by."""
    from capital.kill_switch import KillSwitch
    import inspect
    sig = inspect.signature(KillSwitch.resume)
    params = list(sig.parameters.keys())
    assert "reason" in params
    assert "resumed_by" in params


# ── A.5: alert_watcher config error returns 1 ───────────────────────────


def test_alert_watcher_config_error_path_exists():
    """alert_watcher.main wraps config load in try/except returning 1 on error."""
    import inspect
    from scripts.alert_watcher import main
    source = inspect.getsource(main)
    assert "return 1" in source
    assert "Config error" in source or "except" in source


# ── Gemini log review ────────────────────────────────────────────────────


class TestGeminiLogReview:

    def test_extract_warning_plus(self):
        from scripts.gemini_log_review import _extract_warning_plus
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as f:
            f.write("2026-06-01 INFO normal line\n")
            f.write("2026-06-01 WARNING something bad\n")
            f.write("2026-06-01 DEBUG more detail\n")
            f.write("2026-06-01 ERROR crash happened\n")
            f.write("2026-06-01 CRITICAL system down\n")
            f.flush()
            path = Path(f.name)

        try:
            result = _extract_warning_plus(path)
            assert "WARNING something bad" in result
            assert "ERROR crash happened" in result
            assert "CRITICAL system down" in result
            assert "INFO normal line" not in result
            assert "DEBUG more detail" not in result
        finally:
            path.unlink(missing_ok=True)

    def test_extract_empty_file(self):
        from scripts.gemini_log_review import _extract_warning_plus
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as f:
            f.write("2026-06-01 INFO all good\n")
            f.flush()
            path = Path(f.name)
        try:
            result = _extract_warning_plus(path)
            assert result == ""
        finally:
            path.unlink(missing_ok=True)

    def test_extract_missing_file(self):
        from scripts.gemini_log_review import _extract_warning_plus
        result = _extract_warning_plus(Path("/nonexistent/file.log"))
        assert result == ""

    def test_extract_max_lines(self):
        from scripts.gemini_log_review import _extract_warning_plus
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as f:
            for i in range(100):
                f.write(f"2026-06-01 WARNING line {i}\n")
            f.flush()
            path = Path(f.name)
        try:
            result = _extract_warning_plus(path, max_lines=10)
            lines = result.strip().split("\n")
            assert len(lines) == 10
        finally:
            path.unlink(missing_ok=True)

    def test_run_review_no_logs(self):
        from scripts.gemini_log_review import run_review
        log = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "logs"
            log_dir.mkdir()
            out_dir = Path(td) / "output"
            watchman_dir = Path(td) / "watchman"
            watchman_dir.mkdir()
            result = run_review(
                date_iso="2026-06-01",
                log_dir=log_dir,
                output_dir=out_dir,
                watchman_dir=watchman_dir,
                log=log,
                dry_run=False,
            )
        assert result == 2

    def test_run_review_dry_run(self):
        from scripts.gemini_log_review import run_review
        log = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            (log_dir / "trading_2026-06-01.log").write_text(
                "WARNING something\nERROR something else\n", encoding="utf-8"
            )
            out_dir = Path(td) / "output"
            watchman_dir = Path(td) / "watchman"
            watchman_dir.mkdir()
            result = run_review(
                date_iso="2026-06-01",
                log_dir=log_dir,
                output_dir=out_dir,
                watchman_dir=watchman_dir,
                log=log,
                dry_run=True,
            )
        assert result == 0

    def test_run_review_no_api_key(self):
        """CLI not found returns exit code 1."""
        from scripts.gemini_log_review import run_review
        log = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            (log_dir / "trading_2026-06-01.log").write_text(
                "WARNING something\n", encoding="utf-8"
            )
            out_dir = Path(td) / "output"
            watchman_dir = Path(td) / "watchman"
            watchman_dir.mkdir()
            with patch("scripts.gemini_log_review._call_gemini_cli", return_value=None):
                result = run_review(
                    date_iso="2026-06-01",
                    log_dir=log_dir,
                    output_dir=out_dir,
                    watchman_dir=watchman_dir,
                    log=log,
                )
        assert result == 1

    def test_run_review_with_mock_gemini(self):
        from scripts.gemini_log_review import run_review
        log = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            (log_dir / "trading_2026-06-01.log").write_text(
                "WARNING something important\n", encoding="utf-8"
            )
            out_dir = Path(td) / "output"
            watchman_dir = Path(td) / "watchman"
            watchman_dir.mkdir()
            with patch("scripts.gemini_log_review._call_gemini_cli", return_value="All clear."):
                result = run_review(
                    date_iso="2026-06-01",
                    log_dir=log_dir,
                    output_dir=out_dir,
                    watchman_dir=watchman_dir,
                    log=log,
                )
            assert result == 0
            review_file = out_dir / "eod_review_2026-06-01.md"
            assert review_file.exists()
            content = review_file.read_text(encoding="utf-8")
            assert "All clear." in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
