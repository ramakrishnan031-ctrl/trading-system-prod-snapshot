# tests/unit/test_mis_blocklist.py — MIS Learned Blocklist (source-free)
"""
Tests for the MIS Learned Blocklist (Step 0 gated out the daily list):
  * is_mis_block_rejection() detection (message + context, non-MIS negatives)
  * MisLearnedBlocklist: record/persist/reload, TTL re-test, refresh, fail-safe
  * OrderPlacer 400-handler: records on MIS-block only; existing steps UNCHANGED
  * SecondaryScreener hook: REJECTED_NOT_MIS_TRADABLE (LONG+SHORT), pass-through,
    NON-MIS never-dropped, SHADOW vs ACTIVE, DORMANCY (byte-identical)
"""
import logging
from datetime import date
from unittest.mock import MagicMock

import pytest

from core.exceptions import BrokerError, OrderRejectedError
from core.mis_blocklist import MisLearnedBlocklist, is_mis_block_rejection
from orders.order_placer import OrderPlacer
from screening.secondary_screener import SecondaryScreener

_MIS_MSG = ("Zerodha rejected order: MIS orders are currently blocked for ORBTEXP. "
            "Place a CNC order instead.")


# ─────────────────────────── is_mis_block_rejection ───────────────────────────
class TestIsMisBlockRejection:
    def test_detects_in_message(self):
        assert is_mis_block_rejection(OrderRejectedError(_MIS_MSG)) is True

    def test_detects_in_context_reason(self):
        exc = OrderRejectedError("Zerodha rejected order", rejection_reason=_MIS_MSG)
        assert is_mis_block_rejection(exc) is True

    def test_case_insensitive(self):
        assert is_mis_block_rejection(Exception("mis ORDERS are CURRENTLY blocked for X")) is True

    def test_non_mis_reasons_are_false(self):
        assert is_mis_block_rejection(OrderRejectedError("Insufficient funds")) is False
        assert is_mis_block_rejection(OrderRejectedError("Tick size for this script is 0.05")) is False
        assert is_mis_block_rejection(BrokerError("RR_GATE_FAILED")) is False

    def test_handles_weird_input(self):
        assert is_mis_block_rejection(None) is False
        assert is_mis_block_rejection(123) is False


# ─────────────────────────── MisLearnedBlocklist ──────────────────────────────
class TestMisLearnedBlocklist:
    def _bl(self, tmp_path, ttl=5, today=date(2026, 6, 30)):
        return MisLearnedBlocklist(
            path=tmp_path / "mis_blocklist.json", ttl_days=ttl, today_fn=lambda: today
        )

    def test_record_then_blocked(self, tmp_path):
        bl = self._bl(tmp_path)
        assert bl.is_blocked("ORBTEXP") is False
        bl.record_block("ORBTEXP")
        assert bl.is_blocked("ORBTEXP") is True
        # case-insensitive
        assert bl.is_blocked("orbtexp") is True

    def test_persist_survives_restart(self, tmp_path):
        path = tmp_path / "mis_blocklist.json"
        bl = MisLearnedBlocklist(path=path, ttl_days=5, today_fn=lambda: date(2026, 6, 30))
        bl.record_block("DPWIRES")
        assert path.exists()
        # NEW instance from the same file = a restart
        bl2 = MisLearnedBlocklist(path=path, ttl_days=5, today_fn=lambda: date(2026, 6, 30))
        assert bl2.is_blocked("DPWIRES") is True

    def test_ttl_expiry_allows_retest(self, tmp_path):
        path = tmp_path / "mis_blocklist.json"
        # blocked on the 30th
        MisLearnedBlocklist(path=path, ttl_days=5, today_fn=lambda: date(2026, 6, 30)).record_block("EIFFL")
        # day 4 -> still blocked (0..4 within ttl=5)
        bl4 = MisLearnedBlocklist(path=path, ttl_days=5, today_fn=lambda: date(2026, 7, 4))
        assert bl4.is_blocked("EIFFL") is True
        # day 5 -> TTL expired -> allowed through to re-test
        bl5 = MisLearnedBlocklist(path=path, ttl_days=5, today_fn=lambda: date(2026, 7, 5))
        assert bl5.is_blocked("EIFFL") is False

    def test_fresh_400_refreshes_date(self, tmp_path):
        path = tmp_path / "mis_blocklist.json"
        MisLearnedBlocklist(path=path, ttl_days=5, today_fn=lambda: date(2026, 6, 30)).record_block("GNRL")
        # 6 days later it would be expired...
        bl = MisLearnedBlocklist(path=path, ttl_days=5, today_fn=lambda: date(2026, 7, 6))
        assert bl.is_blocked("GNRL") is False
        # ...but a fresh 400 today re-blocks it
        bl.record_block("GNRL")
        assert bl.is_blocked("GNRL") is True

    def test_corrupt_date_fails_open(self, tmp_path):
        path = tmp_path / "mis_blocklist.json"
        path.write_text('{"BADSYM": "not-a-date"}', encoding="utf-8")
        bl = MisLearnedBlocklist(path=path, ttl_days=5, today_fn=lambda: date(2026, 6, 30))
        assert bl.is_blocked("BADSYM") is False  # fail-open, never crash

    def test_corrupt_file_starts_empty(self, tmp_path):
        path = tmp_path / "mis_blocklist.json"
        path.write_text("{ this is not json", encoding="utf-8")
        bl = MisLearnedBlocklist(path=path, ttl_days=5, today_fn=lambda: date(2026, 6, 30))
        assert bl.is_blocked("ANYTHING") is False
        # still usable after a corrupt load
        bl.record_block("X")
        assert bl.is_blocked("X") is True

    def test_empty_symbol_is_noop(self, tmp_path):
        bl = self._bl(tmp_path)
        bl.record_block("")
        assert bl.is_blocked("") is False


# ─────────────────────────── OrderPlacer 400-handler ──────────────────────────
class TestHandlerRecords:
    def _placer(self, mis_blocklist):
        return OrderPlacer(
            entry_engine=MagicMock(), order_manager=MagicMock(), fund_manager=MagicMock(),
            bus=MagicMock(), logger=logging.getLogger("test_op"), order_monitor=MagicMock(),
            cost_calculator=MagicMock(), mis_blocklist=mis_blocklist,
        )

    def test_records_on_mis_block(self, tmp_path):
        bl = MisLearnedBlocklist(path=tmp_path / "b.json", ttl_days=5, today_fn=lambda: date(2026, 6, 30))
        placer = self._placer(bl)
        placer._handle_placement_failure(
            "trd1", "res1", "sig1", OrderRejectedError(_MIS_MSG), symbol="ORBTEXP",
        )
        assert bl.is_blocked("ORBTEXP") is True

    def test_does_not_record_non_mis(self, tmp_path):
        bl = MisLearnedBlocklist(path=tmp_path / "b.json", ttl_days=5, today_fn=lambda: date(2026, 6, 30))
        placer = self._placer(bl)
        placer._handle_placement_failure(
            "trd1", "res1", "sig1", OrderRejectedError("Insufficient funds"), symbol="RELIANCE",
        )
        assert bl.is_blocked("RELIANCE") is False

    def test_existing_behaviour_unchanged(self, tmp_path):
        """The pre-existing steps (mark FAILED + release capital) still run."""
        bl = MisLearnedBlocklist(path=tmp_path / "b.json", ttl_days=5, today_fn=lambda: date(2026, 6, 30))
        placer = self._placer(bl)
        placer._handle_placement_failure(
            "trd1", "res1", "sig1", OrderRejectedError(_MIS_MSG), symbol="ORBTEXP",
            final_status="FAILED",
        )
        placer._om.update_trade_status.assert_called_once_with("trd1", "FAILED")
        placer._fm.release.assert_called_once()

    def test_none_blocklist_is_safe(self):
        placer = self._placer(None)  # not wired -> inert, no crash
        placer._handle_placement_failure(
            "trd1", "res1", "sig1", OrderRejectedError(_MIS_MSG), symbol="ORBTEXP",
        )
        placer._om.update_trade_status.assert_called_once()


# ─────────────────────────── SecondaryScreener hook ───────────────────────────
class TestScreenerHook:
    def _screener(self, tmp_path, *, enabled, shadow, blocked_symbol=None,
                  product="MIS", quote_raises=True):
        bl = MisLearnedBlocklist(path=tmp_path / "b.json", ttl_days=5,
                                 today_fn=lambda: date(2026, 6, 30))
        if blocked_symbol:
            bl.record_block(blocked_symbol)
        # quote_fn raises by default -> if the MIS gate falls through, screen() returns
        # SKIPPED_QUOTE_UNAVAILABLE (so we never need step_executor/scorer to run).
        quote_fn = MagicMock(side_effect=RuntimeError("quote") if quote_raises else None)
        logger = MagicMock()
        screener = SecondaryScreener(
            step_executor=MagicMock(), quality_scorer=MagicMock(), state_store=MagicMock(),
            quote_fn=quote_fn, logger=logger,
            mis_blocklist=bl, mis_filter_enabled=enabled, mis_filter_shadow=shadow,
            resolve_product=lambda intent: product,
        )
        return screener, quote_fn, logger, bl

    def _screen(self, screener, symbol, direction="LONG"):
        from core.time_authority import now_ist
        return screener.screen(
            signal_id="sig1", symbol=symbol, scanner_name="scn", trigger_price=100.0,
            triggered_at=now_ist(), direction=direction, intent="INTRADAY",
            strategy=MagicMock(),
        )

    @pytest.mark.parametrize("direction", ["LONG", "SHORT"])
    def test_active_rejects_blocked(self, tmp_path, direction):
        screener, quote_fn, logger, _ = self._screener(
            tmp_path, enabled=True, shadow=False, blocked_symbol="ORBTEXP")
        result = self._screen(screener, "ORBTEXP", direction)
        assert result.status == "REJECTED_NOT_MIS_TRADABLE"
        assert result.passed is False
        quote_fn.assert_not_called()  # short-circuits BEFORE the funnel
        assert logger.warning.called  # req-4: every decision is logged

    def test_active_passes_not_blocked(self, tmp_path):
        screener, quote_fn, _, _ = self._screener(
            tmp_path, enabled=True, shadow=False, blocked_symbol="ORBTEXP")
        result = self._screen(screener, "SOMEOTHER")  # not in the blocklist
        assert result.status != "REJECTED_NOT_MIS_TRADABLE"
        quote_fn.assert_called_once()  # proceeded past the MIS gate

    def test_non_mis_product_never_dropped(self, tmp_path):
        screener, quote_fn, _, _ = self._screener(
            tmp_path, enabled=True, shadow=False, blocked_symbol="ORBTEXP", product="CNC")
        result = self._screen(screener, "ORBTEXP")  # blocked, but product != MIS
        assert result.status != "REJECTED_NOT_MIS_TRADABLE"
        quote_fn.assert_called_once()

    def test_shadow_logs_but_does_not_reject(self, tmp_path):
        screener, quote_fn, logger, _ = self._screener(
            tmp_path, enabled=True, shadow=True, blocked_symbol="ORBTEXP")
        result = self._screen(screener, "ORBTEXP")
        assert result.status != "REJECTED_NOT_MIS_TRADABLE"  # NOT rejected in shadow
        quote_fn.assert_called_once()                        # fell through
        assert logger.warning.called                         # would-drop still logged

    def test_dormant_does_not_consult_blocklist(self, tmp_path):
        # enabled=False -> byte-identical: the blocklist is never even consulted.
        screener, quote_fn, _, bl = self._screener(
            tmp_path, enabled=False, shadow=False, blocked_symbol="ORBTEXP")
        bl.is_blocked = MagicMock(side_effect=AssertionError("must not be called when dormant"))
        result = self._screen(screener, "ORBTEXP")
        assert result.status != "REJECTED_NOT_MIS_TRADABLE"
        quote_fn.assert_called_once()
