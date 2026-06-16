"""
tests/unit/test_fix181.py — FIX-181 (post-Live-Day-1 hardening, 2026-06-16)

Covers the six FIX-181 fixes:

  Step 1 — tick-size rounding on all order prices (GICRE incident: an off-tick
           SL limit/trigger is rejected by Zerodha outright).
  Step 2 — emergency / kill exits use marketable LIMIT (not MARKET), tag <=20.
  Step 3 — open_positions cap off-by-one (>= -> >).
  Step 4 — orphan-fill reconciliation (filled-PENDING adopted, not abandoned).
  Step 5 — daily-trade count excludes FAILED / CANCELLED.
  Step 6 — holiday-aware broker-API guard with Friday-holiday T-1 preponment.
"""

from __future__ import annotations

import logging

import pytest

from pathlib import Path

from orders.price_math import (
    DEFAULT_TICK,
    EMERGENCY_EXIT_BUFFER_PCT,
    calc_sl_limit_price,
    marketable_limit_price,
    round_to_tick,
)


def _log() -> logging.Logger:
    return logging.getLogger("test_fix181")


# ═════════════════════════════════════════════════════════════════════════════
# Step 1 — tick-size rounding
# ═════════════════════════════════════════════════════════════════════════════

class TestStep1_RoundToTick:

    def test_round_up(self) -> None:
        # 577.75 * 1.005 = 580.6389 -> up -> 580.65
        assert round_to_tick(580.6389, 0.05, "up") == pytest.approx(580.65)

    def test_round_down(self) -> None:
        # 566.25 * 0.995 = 563.42 -> down -> 563.40
        assert round_to_tick(563.42, 0.05, "down") == pytest.approx(563.40)

    def test_round_nearest(self) -> None:
        assert round_to_tick(100.07, 0.05, "nearest") == pytest.approx(100.05)
        assert round_to_tick(100.08, 0.05, "nearest") == pytest.approx(100.10)

    def test_non_default_tick(self) -> None:
        # 0.50-tick instrument
        assert round_to_tick(497.3, 0.50, "nearest") == pytest.approx(497.5)
        assert round_to_tick(497.3, 0.50, "down") == pytest.approx(497.0)
        assert round_to_tick(497.3, 0.50, "up") == pytest.approx(497.5)

    def test_result_is_always_tick_multiple(self) -> None:
        for tick in (0.01, 0.05, 0.10, 0.50, 1.0, 5.0):
            for mode in ("up", "down", "nearest"):
                out = round_to_tick(1234.5678, tick, mode)
                ratio = out / tick
                assert abs(ratio - round(ratio)) < 1e-9, (out, tick, mode)

    def test_nonpositive_tick_falls_back_to_2dp(self) -> None:
        assert round_to_tick(123.456, 0.0) == pytest.approx(123.46)
        assert round_to_tick(123.456, -1.0) == pytest.approx(123.46)


class TestStep1_CalcSlLimitPriceTickAligned:

    def test_sell_stop_rounds_down_to_tick(self) -> None:
        # SELL stop (exits LONG): trigger 577.75 * 0.995 = 574.86125 -> DOWN -> 574.85
        out = calc_sl_limit_price("SELL", 577.75, 0.005)
        assert out == pytest.approx(574.85)
        assert out < 577.75
        assert abs((out / 0.05) - round(out / 0.05)) < 1e-9

    def test_buy_stop_rounds_up_to_tick(self) -> None:
        # BUY stop (exits SHORT): trigger 577.75 * 1.005 = 580.638... -> UP -> 580.65
        out = calc_sl_limit_price("BUY", 577.75, 0.005)
        assert out == pytest.approx(580.65)
        assert out > 577.75
        assert abs((out / 0.05) - round(out / 0.05)) < 1e-9

    def test_buy_stop_already_aligned(self) -> None:
        # LONG SL trigger 360.00; BUY-stop path: 360 * 1.005 = 361.8 -> 361.80
        assert calc_sl_limit_price("BUY", 360.0, 0.005) == pytest.approx(361.8)

    def test_custom_tick_size(self) -> None:
        # 0.50-tick instrument, SELL stop rounds DOWN to a 0.50 multiple
        out = calc_sl_limit_price("SELL", 500.0, 0.005, tick_size=0.50)
        # 500 * 0.995 = 497.5 -> already aligned
        assert out == pytest.approx(497.5)
        out2 = calc_sl_limit_price("SELL", 503.0, 0.005, tick_size=0.50)
        # 503 * 0.995 = 500.485 -> DOWN to 0.50 -> 500.0
        assert out2 == pytest.approx(500.0)

    def test_default_tick_constant(self) -> None:
        assert DEFAULT_TICK == 0.05


class TestStep1_AdapterSnap:
    """The adapter is the authoritative net: place_order snaps price+trigger."""

    def _adapter_with_cache(self, tick: float):
        from tests.unit.test_zerodha_adapter import _make_adapter

        class _FakeCache:
            def tick_size(self, symbol: str) -> float:
                return tick

        adapter, kite, _, _, _ = _make_adapter()
        adapter.set_instrument_cache(_FakeCache())
        return adapter, kite

    def test_snap_sl_sell_rounds_limit_down_trigger_nearest(self) -> None:
        adapter, _ = self._adapter_with_cache(0.05)
        price, trigger = adapter._snap_order_to_tick(
            "RELIANCE", "SL", "SELL", 574.86125, 577.77
        )
        assert price == pytest.approx(574.85)   # limit DOWN
        assert trigger == pytest.approx(577.75)  # nearest

    def test_snap_sl_buy_rounds_limit_up(self) -> None:
        adapter, _ = self._adapter_with_cache(0.05)
        price, _ = adapter._snap_order_to_tick(
            "RELIANCE", "SL", "BUY", 580.638, 577.75
        )
        assert price == pytest.approx(580.65)   # limit UP

    def test_snap_limit_entry_nearest(self) -> None:
        adapter, _ = self._adapter_with_cache(0.05)
        price, _ = adapter._snap_order_to_tick(
            "RELIANCE", "LIMIT", "BUY", 100.07, 0.0
        )
        assert price == pytest.approx(100.05)

    def test_snap_noop_without_cache(self) -> None:
        from tests.unit.test_zerodha_adapter import _make_adapter
        adapter, _, _, _, _ = _make_adapter()
        # cache not wired -> passthrough
        price, trigger = adapter._snap_order_to_tick(
            "RELIANCE", "SL", "SELL", 574.86125, 577.77
        )
        assert price == 574.86125
        assert trigger == 577.77

    def test_snap_unknown_symbol_passthrough(self) -> None:
        from tests.unit.test_zerodha_adapter import _make_adapter

        class _RaisingCache:
            def tick_size(self, symbol: str) -> float:
                raise KeyError(symbol)

        adapter, _, _, _, _ = _make_adapter()
        adapter.set_instrument_cache(_RaisingCache())
        price, trigger = adapter._snap_order_to_tick(
            "NOPE", "SL", "SELL", 574.86125, 577.77
        )
        assert price == 574.86125
        assert trigger == 577.77

    def test_place_order_live_snaps_price_sent_to_kite(self) -> None:
        """End-to-end: off-tick SL price is snapped before reaching kite."""
        adapter, kite = self._adapter_with_cache(0.05)
        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return "KITE-SNAP-1"

        kite.place_order = _capture  # type: ignore[assignment]
        adapter.place_order(
            symbol="RELIANCE", side="SELL", qty=10, price=574.86125,
            order_type="SL", intent="INTRADAY", trigger_price=577.77,
        )
        assert captured["price"] == pytest.approx(574.85)
        assert captured["trigger_price"] == pytest.approx(577.75)


# ═════════════════════════════════════════════════════════════════════════════
# Step 2 — marketable LIMIT emergency / kill exits
# ═════════════════════════════════════════════════════════════════════════════

class TestStep2_MarketableLimitPrice:

    def test_sell_below_ltp(self) -> None:
        # SELL exit: 100 * (1 - 0.01) = 99.0
        assert marketable_limit_price("SELL", 100.0, 0.01) == pytest.approx(99.0)

    def test_buy_above_ltp(self) -> None:
        # BUY exit: 100 * (1 + 0.01) = 101.0
        assert marketable_limit_price("BUY", 100.0, 0.01) == pytest.approx(101.0)

    def test_result_tick_aligned(self) -> None:
        out = marketable_limit_price("SELL", 333.33, 0.01)  # 329.9967 -> down -> 329.95
        assert abs((out / 0.05) - round(out / 0.05)) < 1e-9

    def test_custom_tick(self) -> None:
        # 0.50-tick: SELL 503 * 0.99 = 497.97 -> down -> 497.50
        assert marketable_limit_price("SELL", 503.0, 0.01, 0.5) == pytest.approx(497.5)

    def test_default_buffer_constant(self) -> None:
        assert EMERGENCY_EXIT_BUFFER_PCT == 0.01

    def test_invalid_ltp_raises(self) -> None:
        with pytest.raises(ValueError):
            marketable_limit_price("SELL", 0.0)

    def test_invalid_side_raises(self) -> None:
        with pytest.raises(ValueError):
            marketable_limit_price("HOLD", 100.0)


class TestStep2_KillSwitchExit:

    def _make_ks(self, tmp_path: Path, adapter):
        import logging as _logging
        from capital.kill_switch import KillSwitch
        from core.events import EventBus
        from core.state_store import StateStore

        store = StateStore(tmp_path / "ks_fix181.db")
        return KillSwitch(
            state_store=store,
            bus=EventBus(),
            logger=_logging.getLogger("test_ks_fix181"),
            adapter=adapter,
            emergency_exit_buffer_pct=0.01,
        )

    def test_exit_uses_marketable_limit_when_ltp_available(self, tmp_path) -> None:
        class _Adapter:
            def get_quote_raw(self, instruments):
                return {"NSE:RELIANCE": {"last_price": 100.0}}

        ks = self._make_ks(tmp_path, _Adapter())
        order_type, price = ks._marketable_exit_params("RELIANCE", "SELL")
        assert order_type == "LIMIT"
        assert price == pytest.approx(99.0)  # 100 * (1 - 0.01)

    def test_exit_buy_side_above_ltp(self, tmp_path) -> None:
        class _Adapter:
            def get_quote_raw(self, instruments):
                return {"NSE:INFY": {"last_price": 200.0}}

        ks = self._make_ks(tmp_path, _Adapter())
        order_type, price = ks._marketable_exit_params("INFY", "BUY")
        assert order_type == "LIMIT"
        assert price == pytest.approx(202.0)  # 200 * (1 + 0.01)

    def test_exit_falls_back_to_market_without_ltp(self, tmp_path) -> None:
        class _Adapter:
            def get_quote_raw(self, instruments):
                raise RuntimeError("no quote")

        ks = self._make_ks(tmp_path, _Adapter())
        order_type, price = ks._marketable_exit_params("RELIANCE", "SELL")
        assert order_type == "MARKET"
        assert price == 0.0


class TestStep2_OrderPlacerEmergencyExit:

    def test_tag_never_exceeds_20_chars(self) -> None:
        from core.ids import truncate_tag_for_broker
        long_trade_id = "abcdef01-2345-6789-abcd-ef0123456789"
        tag = truncate_tag_for_broker(f"EXIT_{long_trade_id}")
        assert len(tag) <= 20


# ═════════════════════════════════════════════════════════════════════════════
# Step 3 — open_positions off-by-one (>= -> >)
# ═════════════════════════════════════════════════════════════════════════════

class TestStep3_OpenPositionsCap:
    """The candidate is pre-incremented into processor_in_flight_count, so the
    cap must use > (not >=): max=3 must truly allow a 3rd concurrent position."""

    def _engine(self, tmp_path, max_open):
        from tests.unit.test_risk_engine import (
            _CapturingHandler,
            _MockFundManager,
            _MockKillSwitch,
            _make_engine,
            _make_snap,
        )
        from core.state_store import StateStore

        store = StateStore(tmp_path / "re_fix181.db")
        engine = _make_engine(
            store,
            _MockFundManager(_make_snap()),
            _CapturingHandler(),
            kill_switch=_MockKillSwitch(active=False),
            max_open=max_open,
        )
        return engine, store

    def test_max_3_allows_third(self, tmp_path) -> None:
        from tests.unit.test_risk_engine import _insert_trade, _make_sizing
        engine, store = self._engine(tmp_path, max_open=3)
        # 2 existing open + candidate (in-flight=1) -> active_total=3 == max -> ALLOW
        _insert_trade(store, "t1", status="OPEN")
        _insert_trade(store, "t2", status="OPEN")
        result = engine.approve(
            "RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-3rd",
            processor_in_flight_count=1,
        )
        assert result.approved, result.reason
        store.close()

    def test_max_3_rejects_fourth(self, tmp_path) -> None:
        from tests.unit.test_risk_engine import _insert_trade, _make_sizing
        engine, store = self._engine(tmp_path, max_open=3)
        # 3 existing open + candidate (in-flight=1) -> active_total=4 > max -> REJECT
        _insert_trade(store, "t1", status="OPEN")
        _insert_trade(store, "t2", status="OPEN")
        _insert_trade(store, "t3", status="OPEN")
        result = engine.approve(
            "INFY", "BUY", "INTRADAY", _make_sizing(), "sig-4th",
            processor_in_flight_count=1,
        )
        assert not result.approved
        assert result.failed_check == "OPEN_POSITIONS"
        store.close()


# ═════════════════════════════════════════════════════════════════════════════
# Step 4 — orphan-fill reconciliation (GICRE incident)
# ═════════════════════════════════════════════════════════════════════════════

class TestStep4_GetTradesByStatusAndSymbol:

    def test_returns_inflight_trade_for_symbol(self, tmp_path) -> None:
        from tests.unit.test_order_reconciler import _insert_trade
        from core.state_store import StateStore

        store = StateStore(tmp_path / "s4_a.db")
        _insert_trade(store, "t1", symbol="GICRE", status="PENDING_FILL")
        _insert_trade(store, "t2", symbol="OTHER", status="OPEN")
        rows = store.get_trades_by_status_and_symbol(
            ("PENDING_FILL", "PENDING"), "GICRE"
        )
        assert len(rows) == 1
        assert rows[0]["trade_id"] == "t1"
        # OPEN trade not matched by the in-flight filter
        assert store.get_trades_by_status_and_symbol(("PENDING_FILL",), "OTHER") == []
        store.close()


class TestStep4_KillSwitchBrokerSweep:
    """LAYER A: HARD_KILL must flatten a broker position with NO matching local
    OPEN/PARTIAL/PENDING_FILL trade (entry filled after being marked CANCELLED,
    or filled post-kill)."""

    def _ks(self, tmp_path, adapter):
        import logging as _logging
        from capital.kill_switch import KillSwitch
        from core.events import EventBus
        from core.state_store import StateStore

        store = StateStore(tmp_path / "s4_ks.db")
        ks = KillSwitch(
            state_store=store,
            bus=EventBus(),
            logger=_logging.getLogger("test_s4_ks"),
            adapter=adapter,
            emergency_exit_buffer_pct=0.01,
        )
        return ks, store

    def test_sweep_flattens_orphan_broker_position(self, tmp_path) -> None:
        from dataclasses import dataclass

        @dataclass
        class _Pos:
            symbol: str
            qty: int

        calls = []

        class _Adapter:
            def get_positions(self):
                return [_Pos("GICRE", 4)]

            def get_quote_raw(self, instruments):
                return {"NSE:GICRE": {"last_price": 200.0}}

            def place_order(self, **kw):
                calls.append(kw)
                return type("P", (), {"broker_order_id": "B1"})()

        ks, store = self._ks(tmp_path, _Adapter())
        # No local open trades at all -> only the broker sweep can catch GICRE.
        report = ks._exit_all_trades_indestructible()
        assert len(calls) == 1
        assert calls[0]["symbol"] == "GICRE"
        assert calls[0]["side"] == "SELL"          # long position -> SELL to flatten
        assert calls[0]["order_type"] == "LIMIT"   # marketable LIMIT
        assert calls[0]["price"] == pytest.approx(198.0)  # 200 * (1 - 0.01)
        assert report.attempted == 1
        assert report.failed == []
        store.close()

    def test_sweep_skips_symbol_already_handled(self, tmp_path) -> None:
        from dataclasses import dataclass
        from tests.unit.test_order_reconciler import _insert_trade

        @dataclass
        class _Pos:
            symbol: str
            qty: int

        calls = []

        class _Adapter:
            def get_positions(self):
                return [_Pos("RELIANCE", 10)]

            def get_quote_raw(self, instruments):
                return {"NSE:RELIANCE": {"last_price": 2500.0}}

            def place_order(self, **kw):
                calls.append(kw)
                return type("P", (), {"broker_order_id": "B2"})()

        ks, store = self._ks(tmp_path, _Adapter())
        # A local OPEN trade for RELIANCE -> first pass handles it; sweep must NOT
        # double-fire on the same symbol.
        _insert_trade(store, "t1", symbol="RELIANCE", status="OPEN", qty_filled=10)
        ks._exit_all_trades_indestructible()
        reliance_calls = [c for c in calls if c["symbol"] == "RELIANCE"]
        assert len(reliance_calls) == 1  # only the local-trade exit, not a sweep dup
        store.close()


class TestStep4_ReconcilerInflightOrphan:
    """LAYER B: a broker position matching a local in-flight trade is flattened
    under HARD_KILL, and left alone (no destructive action) otherwise."""

    def _recon(self, tmp_path, kill_active, quote_ltp=None):
        from unittest.mock import MagicMock
        from tests.unit.test_order_reconciler import _make_reconciler, _make_store

        store = _make_store(tmp_path)
        ks = MagicMock()
        ks.is_active.return_value = kill_active
        adapter = MagicMock()
        adapter.place_order.return_value = type("P", (), {"broker_order_id": "BX"})()
        quote_fn = (lambda s: {}) if quote_ltp is None else (
            lambda s: {f"NSE:{list(s)[0].split(':')[1]}":
                       type("Q", (), {"last_price": quote_ltp})()}
        )
        recon = _make_reconciler(
            store, adapter=adapter, kill_switch=ks, quote_fn=quote_fn
        )
        return recon, store, adapter

    def test_inflight_orphan_flattened_when_kill_active(self, tmp_path) -> None:
        from dataclasses import dataclass

        @dataclass
        class _BP:
            symbol: str
            qty: int
            avg_price: float

        recon, store, adapter = self._recon(tmp_path, kill_active=True, quote_ltp=200.0)
        trade = {"trade_id": "t1", "status": "PENDING_FILL"}
        action = recon._check2_inflight_orphan("GICRE", _BP("GICRE", 4, 199.0), trade)
        assert action.check_name == "INFLIGHT_ORPHAN_FLATTEN"
        adapter.place_order.assert_called_once()
        kw = adapter.place_order.call_args.kwargs
        assert kw["side"] == "SELL"
        assert kw["order_type"] == "LIMIT"
        assert kw["price"] == pytest.approx(198.0)
        store.close()

    def test_inflight_orphan_no_action_when_kill_inactive(self, tmp_path) -> None:
        from dataclasses import dataclass

        @dataclass
        class _BP:
            symbol: str
            qty: int
            avg_price: float

        recon, store, adapter = self._recon(tmp_path, kill_active=False)
        trade = {"trade_id": "t1", "status": "PENDING_FILL"}
        action = recon._check2_inflight_orphan("GICRE", _BP("GICRE", 4, 199.0), trade)
        assert action.check_name == "INFLIGHT_ORPHAN"
        assert action.tier == "COSMETIC"
        adapter.place_order.assert_not_called()  # no destructive action on transient
        store.close()


# ═════════════════════════════════════════════════════════════════════════════
# Step 5 — daily-trade count excludes FAILED / CANCELLED
# ═════════════════════════════════════════════════════════════════════════════

class TestStep5_CountTradesToday:

    def _store(self, tmp_path):
        from core.state_store import StateStore
        return StateStore(tmp_path / "s5.db")

    def test_failed_and_cancelled_not_counted(self, tmp_path) -> None:
        from tests.unit.test_risk_engine import _insert_trade
        store = self._store(tmp_path)
        d = "2026-06-16"
        _insert_trade(store, "f1", status="FAILED", created_date=d)
        _insert_trade(store, "c1", status="CANCELLED", created_date=d)
        assert store.count_trades_today(d) == 0
        store.close()

    def test_executed_statuses_counted(self, tmp_path) -> None:
        from tests.unit.test_risk_engine import _insert_trade
        store = self._store(tmp_path)
        d = "2026-06-16"
        _insert_trade(store, "o1", status="OPEN", created_date=d)
        _insert_trade(store, "cl1", status="CLOSED", created_date=d)
        _insert_trade(store, "pf1", status="PENDING_FILL", created_date=d)
        assert store.count_trades_today(d) == 3
        store.close()

    def test_mixed_only_executed_counted(self, tmp_path) -> None:
        from tests.unit.test_risk_engine import _insert_trade
        store = self._store(tmp_path)
        d = "2026-06-16"
        _insert_trade(store, "o1", status="OPEN", created_date=d)
        _insert_trade(store, "f1", status="FAILED", created_date=d)
        _insert_trade(store, "f2", status="FAILED", created_date=d)
        _insert_trade(store, "c1", status="CANCELLED", created_date=d)
        # only the OPEN trade counts
        assert store.count_trades_today(d) == 1
        store.close()


# ═════════════════════════════════════════════════════════════════════════════
# Step 6 — holiday-aware broker API guard (T-1 preponment)
# ═════════════════════════════════════════════════════════════════════════════

class TestStep6_BrokerApiHolidayGuard:

    def _dt(self, y, mo, d, h, mi):
        from datetime import datetime
        from core.time_authority import ist_timezone
        return datetime(y, mo, d, h, mi, tzinfo=ist_timezone())

    def test_friday_holiday_preponment_thursday_cutoff(self) -> None:
        from datetime import date
        from core.market_windows import is_broker_api_available
        # Friday 2026-06-19 is a holiday -> Thursday 18:00 returns False (T-1)
        hols = {date(2026, 6, 19)}
        assert is_broker_api_available(self._dt(2026, 6, 18, 18, 0), hols) is False
        # Thursday before 17:30 is still available
        assert is_broker_api_available(self._dt(2026, 6, 18, 17, 0), hols) is True

    def test_thursday_normal_when_friday_not_holiday(self) -> None:
        from core.market_windows import is_broker_api_available
        # No holidays -> Thursday 18:00 is available (normal week)
        assert is_broker_api_available(self._dt(2026, 6, 18, 18, 0), set()) is True
        # legacy (no holiday arg) also available
        assert is_broker_api_available(self._dt(2026, 6, 18, 18, 0)) is True

    def test_normal_friday_cutoff(self) -> None:
        from core.market_windows import is_broker_api_available
        assert is_broker_api_available(self._dt(2026, 6, 19, 18, 0), set()) is False
        assert is_broker_api_available(self._dt(2026, 6, 19, 17, 29), set()) is True

    def test_friday_holiday_itself_unavailable(self) -> None:
        from datetime import date
        from core.market_windows import is_broker_api_available
        hols = {date(2026, 6, 19)}
        # Friday is a holiday -> unavailable all day (even morning)
        assert is_broker_api_available(self._dt(2026, 6, 19, 9, 0), hols) is False

    def test_weekend_always_false(self) -> None:
        from core.market_windows import is_broker_api_available
        assert is_broker_api_available(self._dt(2026, 6, 20, 10, 0)) is False  # Sat
        assert is_broker_api_available(self._dt(2026, 6, 21, 10, 0)) is False  # Sun

    def test_current_holiday_set_missing_file_returns_empty(self, tmp_path) -> None:
        from utils.holiday_guard import current_holiday_set
        # No nse_holidays_*.yaml in tmp_path -> empty set, no raise
        assert current_holiday_set(tmp_path) == frozenset()
