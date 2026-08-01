"""
tests/unit/test_p0_live_day1_fixes.py

P0 emergency fixes from the first live trading day (2026-06-15). One test
group per bug:

  Bug A — Zerodha rejects SL-M via API. SL legs are now SL (stop-limit) with a
          limit price offset past the trigger. (orders/price_math.calc_sl_limit_price,
          order_protocol_limit, order_reconciler G5b, breakeven_manager.)
  Bug B — order_placer emergency exit used self._engine.adapter (FullEntryEngine
          has no `adapter` attr); fixed to self._adapter. (Also asserted in
          tests/unit/test_fix148_broker_gaps.py.)
  Bug C — kill_switch indestructible exit called place_order() without the
          required `intent`, and checked a non-existent `.success` attribute.
  Bug D — force_intraday_only config forces every strategy to INTRADAY (MIS),
          so the system can never place CNC/DELIVERY orders.
  Bug E — risk_engine position cap could be exceeded due to a TOCTOU between two
          separate count queries; now a single atomic count_active_positions().

Run: python -m pytest tests/unit/test_p0_live_day1_fixes.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from orders.price_math import DEFAULT_SL_LIMIT_OFFSET_PCT, calc_sl_limit_price
from orders.order_protocol_limit import LimitTripleProtocol
from capital.kill_switch import KillSwitch
from capital.risk_engine import RiskEngine
from capital.fund_manager import CapitalSnapshot
from capital.position_sizer import SizingResult
from core.events import EventBus
from core.state_store import StateStore
from strategies.loader import StrategyLoader


# ═════════════════════════════════════════════════════════════════════════════
# Shared test doubles / helpers
# ═════════════════════════════════════════════════════════════════════════════

import logging


def _log() -> logging.Logger:
    lg = logging.getLogger("test_p0_fixes")
    lg.setLevel(logging.CRITICAL)  # quiet
    return lg


class _RecordingAdapter:
    """Captures place_order kwargs; returns a PlacedOrder-like object."""
    def __init__(self) -> None:
        self.placed: list[dict] = []

    def place_order(self, **kwargs):
        self.placed.append(kwargs)
        idx = len(self.placed)
        return type("PO", (), {
            "broker_order_id": f"B{idx}",
            "internal_order_id": f"I{idx}",
            "product": "MIS",
            "variety": "regular",
        })()


# ═════════════════════════════════════════════════════════════════════════════
# Bug A — SL-M removed; SL (stop-limit) with offset limit price
# ═════════════════════════════════════════════════════════════════════════════

class TestBugA_SlLimitPrice:

    def test_sell_stop_limit_below_trigger(self) -> None:
        """SELL stop (exits a LONG): limit = trigger * (1 - offset), below trigger."""
        limit = calc_sl_limit_price("SELL", 2450.0, 0.005)
        assert limit == pytest.approx(2437.75)
        assert limit < 2450.0

    def test_buy_stop_limit_above_trigger(self) -> None:
        """BUY stop (exits a SHORT): limit = trigger * (1 + offset), above trigger."""
        limit = calc_sl_limit_price("BUY", 1600.0, 0.005)
        assert limit == pytest.approx(1608.0)
        assert limit > 1600.0

    def test_default_offset_is_half_pct(self) -> None:
        assert DEFAULT_SL_LIMIT_OFFSET_PCT == 0.005
        assert calc_sl_limit_price("SELL", 100.0) == pytest.approx(99.5)

    def test_zero_offset_equals_trigger(self) -> None:
        assert calc_sl_limit_price("SELL", 100.0, 0.0) == pytest.approx(100.0)

    def test_result_is_rounded_to_tick(self) -> None:
        # FIX-181 (GICRE incident): result is snapped to a valid tick multiple,
        # not just 2 decimals. 333.33 * 0.995 = 331.66335 -> SELL rounds DOWN
        # to the nearest 0.05 tick -> 331.65 (NOT 331.66, which Zerodha rejects).
        assert calc_sl_limit_price("SELL", 333.33, 0.005) == pytest.approx(331.65)
        # default tick 0.05: every result is a 0.05 multiple
        out = calc_sl_limit_price("SELL", 333.33, 0.005)
        assert abs((out / 0.05) - round(out / 0.05)) < 1e-9

    def test_invalid_side_raises(self) -> None:
        with pytest.raises(ValueError):
            calc_sl_limit_price("HOLD", 100.0)

    def test_nonpositive_trigger_raises(self) -> None:
        with pytest.raises(ValueError):
            calc_sl_limit_price("SELL", 0.0)
        with pytest.raises(ValueError):
            calc_sl_limit_price("SELL", -5.0)


class TestBugA_LimitTripleProtocol:

    def test_intraday_places_sl_not_slm(self) -> None:
        """INTRADAY exits place order_type='SL' with a limit below trigger (LONG)."""
        adapter = _RecordingAdapter()
        proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        legs = proto.place_exits(
            symbol="RELIANCE", entry_side="BUY", qty=10,
            sl_price=2450.0, tgt_price=2600.0,
            intent="INTRADAY", trade_id="t1",
        )
        sl = adapter.placed[0]
        assert sl["order_type"] == "SL"
        assert sl["order_type"] != "SL-M"
        assert sl["trigger_price"] == pytest.approx(2450.0)
        assert sl["price"] == pytest.approx(2437.75)
        assert legs.sl_order_type == "SL"

    def test_custom_offset_is_used(self) -> None:
        """The configured sl_limit_offset_pct flows into the SL limit price."""
        adapter = _RecordingAdapter()
        proto = LimitTripleProtocol(adapter=adapter, logger=_log(),
                                    sl_limit_offset_pct=0.01)
        proto.place_exits(
            symbol="RELIANCE", entry_side="BUY", qty=10,
            sl_price=2000.0, tgt_price=2200.0,
            intent="INTRADAY", trade_id="t2",
        )
        sl = adapter.placed[0]
        assert sl["price"] == pytest.approx(1980.0)  # 2000 * (1 - 0.01)

    def test_short_sl_limit_above_trigger(self) -> None:
        """SHORT exit is a BUY stop: limit above trigger."""
        adapter = _RecordingAdapter()
        proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        proto.place_exits(
            symbol="INFY", entry_side="SELL", qty=5,
            sl_price=1600.0, tgt_price=1500.0,
            intent="INTRADAY", trade_id="t3",
        )
        sl = adapter.placed[0]
        assert sl["side"] == "BUY"
        assert sl["order_type"] == "SL"
        assert sl["price"] == pytest.approx(1608.0)
        assert sl["price"] > sl["trigger_price"]


# ═════════════════════════════════════════════════════════════════════════════
# Bug C — kill_switch indestructible exit: intent + broker_order_id contract
# ═════════════════════════════════════════════════════════════════════════════

def _insert_trade_with_order(
    store: StateStore, trade_id: str, *, symbol: str, direction: str,
    status: str, qty: int, product: str,
) -> None:
    now = "2026-06-15T10:00:00+05:30"
    sig_id = f"sig_{trade_id}"
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, symbol, scanner, strategy, "
            "triggered_at, received_at, expires_at, status, fingerprint, "
            "fingerprint_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sig_id, symbol, "SC", "strat", now, now,
             "2026-06-15T10:05:00+05:30", "TRADED", f"fp_{trade_id}", "2026-06-15"),
        )
        cur.execute(
            "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, "
            "qty_planned, qty_filled, entry_target_price, sl_initial, tgt_initial, "
            "margin_reserved, risk_amount, created_at, status, order_protocol, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, sig_id, symbol, direction, "strat", qty, qty, 100.0, 95.0,
             110.0, 1000.0, 50.0, now, status, "LIMIT_TRIPLE", now),
        )
        cur.execute(
            "INSERT INTO orders (order_id, trade_id, leg, transaction_type, "
            "order_type, product, variety, qty_requested, status, trigger_price, "
            "placed_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"ord_{trade_id}", trade_id, "ENTRY",
             "BUY" if direction == "LONG" else "SELL", "LIMIT", product,
             "regular", qty, "COMPLETE", 0.0, now, now),
        )


class _KsAdapter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def place_order(self, **kwargs):
        self.calls.append(kwargs)
        return type("PO", (), {"broker_order_id": "EXIT1"})()


class _KsAdapterNoId:
    """Returns an empty broker_order_id once, then a real one (forces 1 retry path)."""
    def __init__(self) -> None:
        self.calls = 0

    def place_order(self, **kwargs):
        self.calls += 1
        bid = "" if self.calls == 1 else "EXIT_OK"
        return type("PO", (), {"broker_order_id": bid})()


class TestBugC_KillSwitchExit:
    """M-C8 (16-Jul-2026): these drive the flatten via _exit_all_trades_indestructible()
    directly, as their siblings already do (test_h4:109, test_h5:67,
    test_hard_kill_flatten_chain:132). They used to enter through hard_kill(), which
    since M-C8 DISPATCHES the flatten to a worker and returns immediately on the
    adapter path — so an assertion made straight after it raced the worker, and the
    returned report is a dispatch marker, not a result. What these tests are ABOUT is
    unchanged and every assertion below is verbatim: Bug C is about the flatten's
    intent derivation and success detection, not about hard_kill's synchrony.
    hard_kill's dispatch-to-flatten path is covered by
    test_mc8_async_hardkill.py::test_f4.
    """

    def test_exit_passes_intent_derived_from_product(self, tmp_path: Path) -> None:
        """[SUPERSEDED CONTRACT, 02-Aug-2026 — ledger #2 / Q4] Originally the
        Bug-C assertion: a CNC OPEN trade is flattened with intent=DELIVERY.
        Q4 (Rama, 30-Jul) narrows the kill to "no live INTRADAY position": a
        local CNC trade is now SPARED (no placement, honest zero attempted).
        Bug C's real concern (intent derived from product, never hardcoded)
        survives in the NRML anomaly path — covered by
        test_kill_switch_product_filter.py::test_site1_nrml_flattens_loud_
        under_its_own_intent — and in the MIS sibling below, unchanged."""
        store = StateStore(tmp_path / "ks.db")
        _insert_trade_with_order(store, "t1", symbol="HARIOMPIPE",
                                 direction="LONG", status="OPEN", qty=10, product="CNC")
        adapter = _KsAdapter()
        ks = KillSwitch(state_store=store, bus=EventBus(), logger=_log(),
                        adapter=adapter, enable_auto_trip=False)

        report = ks._exit_all_trades_indestructible()  # M-C8: hard_kill dispatches this

        assert adapter.calls == [], (
            "Q4/ledger #2: a CNC trade must be SPARED by the HARD_KILL flatten "
            "(delivery survives the kill), not exited."
        )
        assert report.attempted == 0 and report.failed == []
        store.close()

    def test_mis_position_exits_with_intraday(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / "ks2.db")
        _insert_trade_with_order(store, "t1", symbol="SETL",
                                 direction="SHORT", status="OPEN", qty=5, product="MIS")
        adapter = _KsAdapter()
        ks = KillSwitch(state_store=store, bus=EventBus(), logger=_log(),
                        adapter=adapter, enable_auto_trip=False)

        ks._exit_all_trades_indestructible()  # M-C8: hard_kill dispatches this

        call = adapter.calls[0]
        assert call["intent"] == "INTRADAY"
        assert call["side"] == "BUY"                 # exit a SHORT
        store.close()

    def test_success_uses_broker_order_id_not_dot_success(self, tmp_path: Path) -> None:
        """PlacedOrder has no .success; a non-empty broker_order_id == success.

        Pre-fix this AttributeError'd on every placement and looped forever.
        """
        store = StateStore(tmp_path / "ks3.db")
        _insert_trade_with_order(store, "t1", symbol="ABC",
                                 direction="LONG", status="OPEN", qty=1, product="MIS")
        adapter = _KsAdapter()
        ks = KillSwitch(state_store=store, bus=EventBus(), logger=_log(),
                        adapter=adapter, enable_auto_trip=False)

        report = ks._exit_all_trades_indestructible()  # M-C8: hard_kill dispatches this
        # The contract that matters for Bug C: the broker exit is treated as a
        # success (no AttributeError, no infinite retry loop) and reported.
        assert report.succeeded == 1
        assert report.failed == []
        # FIX-179: 'EXITING' is now an allowed trades.status, so the best-effort
        # transitional write succeeds (was silently rejected by the CHECK).
        row = store.fetch_one("SELECT status FROM trades WHERE trade_id='t1'")
        assert row["status"] == "EXITING"
        store.close()


class TestExitingStatus:
    """FIX-179: 'EXITING' is a valid trades.status (schema CHECK + state machine)."""

    def test_schema_version_bumped_to_29(self) -> None:
        # v29 added EXITING; v30 (TGT retry) added needs_tgt_retry/tgt_retry_count/
        # tgt_last_retry_at. EXPECTED_SCHEMA_VERSION must be at least 29.
        from core.state_store import EXPECTED_SCHEMA_VERSION
        assert EXPECTED_SCHEMA_VERSION >= 29
        assert EXPECTED_SCHEMA_VERSION >= 30  # v30 added the TGT-retry cols; later versions keep them

    def test_fresh_db_accepts_exiting_status(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / "exiting.db")
        _insert_trade_with_order(store, "t1", symbol="ABC", direction="LONG",
                                 status="OPEN", qty=1, product="MIS")
        # Would raise sqlite3.IntegrityError (CHECK) if EXITING were not allowed.
        with store.transaction() as cur:
            cur.execute(
                "UPDATE trades SET status='EXITING' WHERE trade_id='t1'"
            )
        row = store.fetch_one("SELECT status FROM trades WHERE trade_id='t1'")
        assert row["status"] == "EXITING"
        store.close()

    def test_migration_registered_for_trades_at_v29(self) -> None:
        from core.migrations import MIGRATION_TABLES
        assert "trades" in MIGRATION_TABLES.get(29, [])

    def test_state_machine_allows_exiting_transitions(self) -> None:
        from tests.crash_test.state_machine_validator import TRADE_TRANSITIONS
        assert "EXITING" in TRADE_TRANSITIONS
        assert "EXITING" in TRADE_TRANSITIONS["OPEN"]
        assert "EXITING" in TRADE_TRANSITIONS["PARTIAL"]
        # Task 4 (2026-06-19): EXITING -> OPEN added as a reconciler recovery
        # transition (a trade stuck in EXITING that still holds a live broker
        # position is reverted to OPEN for normal management).
        assert TRADE_TRANSITIONS["EXITING"] == {"CLOSED", "CLOSED_MANUAL", "OPEN"}


# ═════════════════════════════════════════════════════════════════════════════
# Bug D — force_intraday_only (Option A, 10-Jul-2026: NO load-time intent rewrite)
# The old destructive rewrite (DELIVERY->INTRADAY at load) was REMOVED. The declared
# intent is preserved; force_intraday_only dormants DELIVERY at the entry-gate resolver
# and MIS-only is enforced at the broker product chokepoint (see test_zerodha_adapter
# Option A double-lock + test_slice2_strategy_control resolver counts).
# ═════════════════════════════════════════════════════════════════════════════

class TestBugD_ForceIntradayOnly:

    _STRAT_DIR = Path(__file__).parent.parent.parent / "config" / "strategies"

    def test_force_preserves_declared_intent_and_dormants_delivery(self) -> None:
        # Option A: the loader NO LONGER rewrites intent. DELIVERY strategies keep
        # intent=DELIVERY (T5 preserved-intent) and are DORMANTED by the resolver under
        # the breaker (they never place); MIS-only is enforced at the product chokepoint.
        from strategies.control import strategy_will_trade
        loader = StrategyLoader()
        strategies = loader.load_all_strategies(self._STRAT_DIR, force_intraday_only=True)
        assert strategies
        deliv = [n for n, c in strategies.items() if c.intent == "DELIVERY"]
        assert deliv, "declared DELIVERY intent must be PRESERVED (not rewritten) under force"
        for name, cfg in strategies.items():
            # V3 Step 10b: the PB-01 shadow playbook is v3_playbook + enabled:false, so it
            # never trades by construction — Option A's "INTRADAY still trades" invariant is
            # about the 15 live strategies, not the shadow playbook.
            if cfg.v3_playbook:
                continue
            v = strategy_will_trade(cfg, trade_type="INTRADAY", force_intraday_only=True)
            if cfg.intent == "DELIVERY":
                assert not v.will_trade, f"{name}: DELIVERY must be dormant under the breaker"
            else:
                assert v.will_trade, f"{name}: INTRADAY must still trade"

    def test_without_force_delivery_preserved(self) -> None:
        loader = StrategyLoader()
        strategies = loader.load_all_strategies(self._STRAT_DIR, force_intraday_only=False)
        # At least one positional/DELIVERY strategy exists in the repo configs.
        intents = {c.intent for c in strategies.values()}
        assert "DELIVERY" in intents, (
            "expected a DELIVERY strategy in config/strategies when force is off"
        )

    def test_default_is_no_override(self) -> None:
        """Default arg keeps original intents (override is opt-in at the call site)."""
        loader = StrategyLoader()
        strategies = loader.load_all_strategies(self._STRAT_DIR)
        intents = {c.intent for c in strategies.values()}
        assert "DELIVERY" in intents


# ═════════════════════════════════════════════════════════════════════════════
# Bug E — atomic active-position count closes the position-cap TOCTOU
# ═════════════════════════════════════════════════════════════════════════════

class _MockFundManager:
    def __init__(self, snap: CapitalSnapshot) -> None:
        self._snap = snap

    def get_snapshot(self) -> CapitalSnapshot:
        return self._snap

    def get_total_unrealized_mtm(self) -> float:
        return 0.0

    def get_unrealized_mtm_status(self):
        """B-1: (total, is_fresh) — read by the daily-loss gate."""
        return 0.0, True

    def count_live_reservations(self) -> int:
        """FIX-185: authoritative in-flight count (none in this mock)."""
        return 0


def _snap() -> CapitalSnapshot:
    return CapitalSnapshot(
        total=1_000_000.0, intraday_avail=700_000.0, intraday_reserved=0.0,
        intraday_used=0.0, positional_avail=300_000.0, positional_reserved=0.0,
        positional_used=0.0, daily_realized_pnl=0.0,
        ts="2026-06-15T10:00:00+05:30",
    )


def _sizing() -> SizingResult:
    return SizingResult(
        success=True, qty=10, margin_required=10_000.0, risk_amount=500.0,
        bucket="intraday", constraint="RISK", reason="ok", breakdown={},
    )


def _insert_trade(store: StateStore, trade_id: str, status: str,
                  symbol: str = "AAA") -> None:
    now = "2026-06-15T10:00:00+05:30"
    sig_id = f"sig_{trade_id}"
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, symbol, scanner, strategy, "
            "triggered_at, received_at, expires_at, status, fingerprint, "
            "fingerprint_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sig_id, symbol, "SC", "strat", now, now,
             "2026-06-15T10:05:00+05:30", "TRADED", f"fp_{trade_id}", "2026-06-15"),
        )
        cur.execute(
            "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, "
            "qty_planned, qty_filled, entry_target_price, sl_initial, tgt_initial, "
            "margin_reserved, risk_amount, created_at, status, order_protocol, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, sig_id, symbol, "LONG", "strat", 10, 10, 100.0, 95.0,
             110.0, 1000.0, 50.0, now, status, "LIMIT_TRIPLE", now),
        )


def _engine(store: StateStore, fm: _MockFundManager, max_open: int) -> RiskEngine:
    return RiskEngine(
        fund_manager=fm, state_store=store, max_open_positions=max_open,
        max_daily_trades=100, max_sector_exposure_pct=1.0,
        max_consecutive_losses=100, daily_loss_limit_pct=1.0,
        sector_lookup_fn=lambda s: "X", logger=_log(), kill_switch=None,
    )


class TestBugE_PositionCap:

    def test_count_active_positions_single_atomic_query(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / "e.db")
        _insert_trade(store, "t1", "OPEN", "AAA")
        _insert_trade(store, "t2", "PARTIAL", "BBB")
        _insert_trade(store, "t3", "PENDING_FILL", "CCC")
        _insert_trade(store, "t4", "CLOSED", "DDD")   # not active
        assert store.count_active_positions() == 3
        store.close()

    def test_pending_fill_counts_toward_cap(self, tmp_path: Path) -> None:
        """3 PENDING_FILL trades at max_open=3 -> a 4th signal is rejected.

        FIX-181 (off-by-one): the candidate is pre-incremented into
        processor_in_flight_count by signal_processor, so 3 DB + candidate(1)
        = 4 > max(3) -> reject.
        """
        store = StateStore(tmp_path / "e2.db")
        for i in range(3):
            _insert_trade(store, f"t{i}", "PENDING_FILL", f"S{i}")
        engine = _engine(store, _MockFundManager(_snap()), max_open=3)

        result = engine.approve("ZZZ", "BUY", "INTRADAY", _sizing(), "sig-x",
                                processor_in_flight_count=1)

        assert not result.approved
        assert result.failed_check == "OPEN_POSITIONS"
        store.close()

    def test_mixed_open_and_pending_fill_rejected_at_cap(self, tmp_path: Path) -> None:
        # FIX-181: 3 active DB + candidate(1) = 4 > max(3) -> reject.
        store = StateStore(tmp_path / "e3.db")
        _insert_trade(store, "a", "OPEN", "A")
        _insert_trade(store, "b", "PENDING_FILL", "B")
        _insert_trade(store, "c", "PARTIAL", "C")
        engine = _engine(store, _MockFundManager(_snap()), max_open=3)
        result = engine.approve("ZZZ", "BUY", "INTRADAY", _sizing(), "sig-x",
                                processor_in_flight_count=1)
        assert not result.approved
        assert result.failed_check == "OPEN_POSITIONS"
        store.close()

    def test_processor_in_flight_still_counted(self, tmp_path: Path) -> None:
        """TOCTOU guard kept: 2 active DB + 2 processor-in-flight (candidate + a
        concurrent signal) at cap=3 -> 4 > 3 -> reject.

        FIX-181: with the corrected `>` boundary, 2 DB + candidate(1) = 3 == max
        is now ALLOWED (the legitimate 3rd slot); a SECOND concurrent in-flight
        signal is what pushes over the cap and triggers the TOCTOU rejection.
        """
        store = StateStore(tmp_path / "e4.db")
        _insert_trade(store, "a", "OPEN", "A")
        _insert_trade(store, "b", "PENDING_FILL", "B")
        engine = _engine(store, _MockFundManager(_snap()), max_open=3)
        result = engine.approve("ZZZ", "BUY", "INTRADAY", _sizing(), "sig-x",
                                processor_in_flight_count=2)
        assert not result.approved
        assert result.failed_check == "OPEN_POSITIONS"
        store.close()

    def test_below_cap_approved(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / "e5.db")
        _insert_trade(store, "a", "OPEN", "A")
        _insert_trade(store, "b", "PENDING_FILL", "B")
        engine = _engine(store, _MockFundManager(_snap()), max_open=3)
        result = engine.approve("ZZZ", "BUY", "INTRADAY", _sizing(), "sig-x")
        assert result.approved, result.reason
        store.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
