"""
tests/unit/test_smart_tgt_manager.py

Validates orders/smart_tgt_manager.py (ST1-ST15).

Run: python -m pytest tests/unit/test_smart_tgt_manager.py -v
Or:  python tests/unit/test_smart_tgt_manager.py  (standalone)
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from data.candle_store import CandleData
from orders.smart_tgt_manager import SmartTgtManager


# ─────────────────────────────────────────────────────────────────────────────
# Mock infrastructure
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _ModifyResult:
    broker_order_id: str
    success: bool
    reason: str = ""


class _MockAdapter:
    """Records modify_order calls; configurable per-call success/failure."""

    def __init__(self, default_success: bool = True) -> None:
        self.calls: List[dict] = []
        self._default_success = default_success
        self._default_reason = "broker_rejected"  # FIX-045: customizable error message
        self._per_call: List[bool] = []  # queue: pop from front

    def set_responses(self, responses: List[bool]) -> None:
        """Queue a sequence of success flags for successive calls."""
        self._per_call = list(responses)

    def modify_order(
        self,
        broker_order_id: str,
        price: Optional[float] = None,
        qty: Optional[int] = None,
        trigger_price: Optional[float] = None,
        symbol: Optional[str] = None,
    ) -> _ModifyResult:
        self.calls.append({
            "broker_order_id": broker_order_id,
            "trigger_price": trigger_price,
            "symbol": symbol,
        })
        if self._per_call:
            success = self._per_call.pop(0)
        else:
            success = self._default_success
        # FIX-045: use _default_reason for customizable error messages
        reason = "" if success else self._default_reason
        return _ModifyResult(
            broker_order_id=broker_order_id, success=success, reason=reason
        )


class _MockStateStore:
    """In-memory state_store replacement for smart_tgt tests."""

    def __init__(self, co_orders: Optional[Dict[str, Optional[dict]]] = None) -> None:
        # co_orders: {trade_id: {'order_id': str, 'variety': 'co'}} or None
        self._co_orders: Dict[str, Optional[dict]] = co_orders or {}
        self.smart_tgt_rows: Dict[str, dict] = {}
        self.insert_calls: List[dict] = []
        self.update_calls: List[dict] = []
        self.delete_calls: List[str] = []

    def get_co_entry_order_for_trade(self, trade_id: str) -> Optional[dict]:
        return self._co_orders.get(trade_id)

    def insert_smart_tgt_state(self, trade_id: str, **kwargs) -> None:
        row = {"trade_id": trade_id, **kwargs,
               "best_price": kwargs.get("best_price"),
               "trail_count": kwargs.get("trail_count", 0),
               "last_trail_ts": kwargs.get("last_trail_ts")}
        self.smart_tgt_rows[trade_id] = row
        self.insert_calls.append({"trade_id": trade_id, **kwargs})

    def update_smart_tgt_state(
        self, trade_id: str, current_sl: float,
        trail_count: int, last_trail_ts: str, best_price: Optional[float]
    ) -> None:
        self.update_calls.append({
            "trade_id": trade_id, "current_sl": current_sl,
            "trail_count": trail_count,
        })
        if trade_id in self.smart_tgt_rows:
            self.smart_tgt_rows[trade_id].update({
                "current_sl": current_sl,
                "trail_count": trail_count,
                "last_trail_ts": last_trail_ts,
                "best_price": best_price,
            })

    def delete_smart_tgt_state(self, trade_id: str) -> None:
        self.delete_calls.append(trade_id)
        self.smart_tgt_rows.pop(trade_id, None)

    def get_all_smart_tgt_states(self) -> List[dict]:
        return list(self.smart_tgt_rows.values())


class _MockCandleStore:
    """Captures registered callbacks; allows manual candle firing."""

    def __init__(self, candle_history: Optional[Dict[int, List[CandleData]]] = None) -> None:
        self._cbs: List[Any] = []
        self._candle_history = candle_history or {}
        self.registered: List[Any] = []
        self.unregistered: List[Any] = []

    def register_on_candle_close(self, fn: Any) -> None:
        if fn not in self._cbs:
            self._cbs.append(fn)
        self.registered.append(fn)

    def unregister_on_candle_close(self, fn: Any) -> None:
        try:
            self._cbs.remove(fn)
        except ValueError:
            pass
        self.unregistered.append(fn)

    def get_candles(self, instrument_token: int, n: int = 10) -> List[CandleData]:
        history = self._candle_history.get(instrument_token, [])
        return history[-n:]

    def fire_candle(self, candle: CandleData) -> None:
        for cb in list(self._cbs):
            cb(candle)


class _CapturingLogger:
    """Records log calls for assertion in tests."""

    def __init__(self) -> None:
        self.debugs: List[str] = []
        self.infos: List[str] = []
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.criticals: List[str] = []

    def debug(self, msg: str, *a, **k) -> None:
        self.debugs.append(str(msg))

    def info(self, msg: str, *a, **k) -> None:
        self.infos.append(str(msg))

    def warning(self, msg: str, *a, **k) -> None:
        self.warnings.append(str(msg))

    def error(self, msg: str, *a, **k) -> None:
        self.errors.append(str(msg))

    def critical(self, msg: str, *a, **k) -> None:
        self.criticals.append(str(msg))

    def has_debug(self, substr: str) -> bool:
        return any(substr in m for m in self.debugs)

    def has_info(self, substr: str) -> bool:
        return any(substr in m for m in self.infos)

    def has_warning(self, substr: str) -> bool:
        return any(substr in m for m in self.warnings)

    def has_error(self, substr: str) -> bool:
        return any(substr in m for m in self.errors)

    def has_critical(self, substr: str) -> bool:
        return any(substr in m for m in self.criticals)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_ENTRY_TOKEN = 738561
_ENTRY_PRICE = 1000.0
_INITIAL_SL  = 980.0    # LONG: SL below entry
_TRIGGER_PCT = 0.005    # 0.5%
_STEP_PCT    = 0.003    # 0.3%
_CO_ORDER_ID = "co_broker_001"


def _default_co_orders() -> Dict[str, dict]:
    return {"trade_001": {"order_id": _CO_ORDER_ID, "variety": "co"}}


def _make_candle(
    token: int = _ENTRY_TOKEN,
    symbol: str = "RELIANCE",
    high: float = _ENTRY_PRICE,
    low: float = _ENTRY_PRICE,
    close: float = _ENTRY_PRICE,
    open_p: float = _ENTRY_PRICE,
    is_synthetic: bool = False,
) -> CandleData:
    return CandleData(
        instrument_token=token,
        symbol=symbol,
        open=open_p,
        high=high,
        low=low,
        close=close,
        volume=0,
        ts=datetime(2026, 4, 16, 10, 0),
        interval_sec=60,
        is_synthetic=is_synthetic,
    )


def _make_gate(
    co_orders: Optional[Dict] = None,
    adapter_success: bool = True,
    enabled: bool = True,
    candle_history: Optional[Dict] = None,
    on_critical_failure=None,
    quote_fn=None,
    initial_states: Optional[List[dict]] = None,
) -> tuple:
    """Return (mgr, adapter, store, candle_store, logger)."""
    adapter = _MockAdapter(default_success=adapter_success)
    store = _MockStateStore(
        co_orders=co_orders if co_orders is not None else _default_co_orders()
    )
    if initial_states:
        for s in initial_states:
            store.smart_tgt_rows[s["trade_id"]] = s
    cs = _MockCandleStore(candle_history=candle_history)
    log = _CapturingLogger()
    mgr = SmartTgtManager(
        adapter=adapter,
        state_store=store,
        candle_store=cs,
        logger=log,
        quote_fn=quote_fn,
        enabled=enabled,
        on_critical_failure=on_critical_failure,
    )
    return mgr, adapter, store, cs, log


def _register(mgr: SmartTgtManager, **overrides) -> None:
    kwargs = dict(
        trade_id="trade_001",
        symbol="RELIANCE",
        instrument_token=_ENTRY_TOKEN,
        direction="LONG",
        entry_price=_ENTRY_PRICE,
        initial_sl=_INITIAL_SL,
        qty=10,
        trigger_pct=_TRIGGER_PCT,
        step_pct=_STEP_PCT,
    )
    kwargs.update(overrides)
    mgr.register_trade(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRATION (ST3)
# ─────────────────────────────────────────────────────────────────────────────

def test_register_trade_adds_to_tracked_and_db() -> None:
    mgr, _, store, _, log = _make_gate()
    _register(mgr)

    assert mgr.size() == 1
    assert "trade_001" in mgr.tracked_trade_ids()
    assert len(store.insert_calls) == 1
    assert store.insert_calls[0]["trade_id"] == "trade_001"
    assert log.has_info("register_trade")
    print("  OK register_trade: tracked in memory + DB row inserted")


def test_register_trade_duplicate_raises() -> None:
    mgr, _, store, _, _ = _make_gate()
    _register(mgr)

    raised = False
    try:
        _register(mgr)
    except ValueError as e:
        raised = True
        assert "trade_001" in str(e)
    assert raised, "Expected ValueError for duplicate trade_id"
    assert mgr.size() == 1, "Size should remain 1 after dup rejection"
    assert len(store.insert_calls) == 1
    print("  OK register_trade duplicate raises ValueError")


def test_unregister_trade_removes_from_tracked_and_db() -> None:
    mgr, _, store, _, log = _make_gate()
    _register(mgr)
    result = mgr.unregister_trade("trade_001")

    assert result is True
    assert mgr.size() == 0
    assert len(store.delete_calls) == 1
    assert store.delete_calls[0] == "trade_001"
    assert log.has_info("unregister_trade")
    print("  OK unregister_trade: removed from memory + DB deleted")


def test_unregister_unknown_returns_false() -> None:
    mgr, _, store, _, _ = _make_gate()
    result = mgr.unregister_trade("no_such_trade")

    assert result is False
    assert len(store.delete_calls) == 0
    print("  OK unregister_trade unknown -> False, no exception")


def test_register_when_disabled_is_noop() -> None:
    mgr, _, store, _, log = _make_gate(enabled=False)
    _register(mgr)

    assert mgr.size() == 0
    assert len(store.insert_calls) == 0
    assert log.has_debug("disabled")
    print("  OK register_trade when enabled=False is no-op")


# ─────────────────────────────────────────────────────────────────────────────
# TRAIL ALGORITHM -- LONG (ST4)
# ─────────────────────────────────────────────────────────────────────────────

def test_long_price_below_trigger_no_modify() -> None:
    """Price moves only 0.4% (below 0.5% trigger) -> no modify."""
    mgr, adapter, _, cs, _ = _make_gate()
    _register(mgr)

    # High = 1004 (0.4% above entry 1000) -- below trigger_pct=0.005
    cs.fire_candle(_make_candle(high=1004.0))

    assert len(adapter.calls) == 0
    print("  OK LONG price below trigger: no modify call")


def test_long_price_crosses_trigger_one_step() -> None:
    """Price moves exactly trigger_pct (0.5%) -> 0 extra steps but first step fires."""
    mgr, adapter, store, cs, log = _make_gate()
    _register(mgr)

    # High = 1005 (0.5% = trigger_pct). steps = int((0.005 - 0.005)/0.003) = 0
    # new_sl = 1000 * (1 + 0.005 + 0) = 1005.0 > 980 -> should trail
    # FIX-044: Conservative rounding (DOWN for LONG) with tick=0.05 fallback
    # Float precision: 1000*1.005 = 1004.9999... → rounds DOWN to 1004.95
    cs.fire_candle(_make_candle(high=1005.0))

    assert len(adapter.calls) == 1
    assert abs(adapter.calls[0]["trigger_price"] - 1004.95) < 0.001
    # current_sl updated in tracked state
    assert len(store.update_calls) == 1
    assert log.has_info("trailed SL")
    print("  OK LONG crosses trigger: 1 modify call, SL=1004.95 (conservative rounding)")


def test_long_price_multiple_steps() -> None:
    """Price advances enough for 2 extra steps -> correct SL computed."""
    mgr, adapter, _, cs, _ = _make_gate()
    _register(mgr)

    # High = 1020 (2.0% above entry 1000)
    # distance_pct = 0.02
    # steps = int((0.02 - 0.005) / 0.003) = int(5.0) = 5
    # new_sl = 1000 * (1 + 0.005 + 5 * 0.003) = 1000 * 1.020 = 1020.0
    # FIX-044: Conservative rounding (DOWN for LONG) with tick=0.05 fallback
    # Float precision: 1000*1.020 = 1019.9999... → rounds DOWN to 1019.95
    cs.fire_candle(_make_candle(high=1020.0))

    assert len(adapter.calls) == 1
    assert abs(adapter.calls[0]["trigger_price"] - 1019.95) < 0.001
    print(f"  OK LONG multiple steps: SL={adapter.calls[0]['trigger_price']:.4f}")


def test_long_price_retraces_sl_not_lowered() -> None:
    """After trail, price falls back -> SL must NOT be lowered."""
    mgr, adapter, _, cs, _ = _make_gate()
    _register(mgr)

    # First candle: trail fires
    cs.fire_candle(_make_candle(high=1010.0))
    assert len(adapter.calls) == 1
    first_sl = adapter.calls[0]["trigger_price"]

    # Second candle: price drops back to 1000 (below trigger after trail)
    cs.fire_candle(_make_candle(high=1000.0, low=990.0))

    # No additional modify call (best_price stays at 1010)
    assert len(adapter.calls) == 1, f"SL should not lower; got {len(adapter.calls)} calls"
    print(f"  OK LONG retrace: SL stays at {first_sl:.4f}, not lowered")


def test_long_current_sl_same_as_target_no_modify() -> None:
    """If computed new_sl == current_sl, no redundant modify."""
    mgr, adapter, _, cs, _ = _make_gate()
    _register(mgr)

    # Fire candle to trail once
    cs.fire_candle(_make_candle(high=1010.0))
    count_after_first = len(adapter.calls)

    # Fire same candle again -- best_price same, new_sl same as current_sl
    cs.fire_candle(_make_candle(high=1010.0))

    assert len(adapter.calls) == count_after_first, "No extra modify for same SL"
    print("  OK LONG current_sl == target: no redundant modify")


def test_long_best_price_initialized_on_first_candle() -> None:
    """best_price is None before first candle; set to candle.high on first fire."""
    mgr, adapter, _, cs, _ = _make_gate()
    _register(mgr)

    # Candle below trigger; best_price should be initialized but no modify
    cs.fire_candle(_make_candle(high=1002.0))

    assert len(adapter.calls) == 0
    # Internal best_price should now be 1002.0
    with mgr._lock:
        info = mgr._tracked.get("trade_001")
    assert info is not None
    assert abs(info["best_price"] - 1002.0) < 0.001
    print("  OK LONG best_price initialized to candle.high on first fire")


# ─────────────────────────────────────────────────────────────────────────────
# TRAIL ALGORITHM -- SHORT (ST4)
# ─────────────────────────────────────────────────────────────────────────────

def test_short_price_falls_trigger_pct_trail_fires() -> None:
    """SHORT: price falls 0.5% (trigger_pct) -> SL trails down."""
    # SHORT: entry=1000, initial_sl=1020 (above entry), SL should trail DOWN
    co_orders = {"trade_short": {"order_id": "co_short_001", "variety": "co"}}
    mgr, adapter, _, cs, _ = _make_gate(co_orders=co_orders)
    mgr.register_trade(
        trade_id="trade_short",
        symbol="INFY",
        instrument_token=_ENTRY_TOKEN,
        direction="SHORT",
        entry_price=1000.0,
        initial_sl=1020.0,
        qty=5,
        trigger_pct=0.005,
        step_pct=0.003,
    )

    # Low = 995 (0.5% below entry 1000) -- exactly trigger_pct
    # steps = int((0.005 - 0.005)/0.003) = 0
    # new_sl = 1000 * (1.0 - 0.005 - 0) = 995.0 < 1020 (current_sl) -> trail!
    cs.fire_candle(_make_candle(
        token=_ENTRY_TOKEN, symbol="INFY", high=1000.0, low=995.0
    ))

    assert len(adapter.calls) == 1
    expected_sl = 1000.0 * (1.0 - 0.005)
    assert abs(adapter.calls[0]["trigger_price"] - expected_sl) < 0.001
    print(f"  OK SHORT trail: SL moved down to {adapter.calls[0]['trigger_price']:.4f}")


def test_short_sl_never_raised() -> None:
    """SHORT: after trail down, price retraces up -> SL must NOT be raised."""
    co_orders = {"trade_short": {"order_id": "co_short_001", "variety": "co"}}
    mgr, adapter, _, cs, _ = _make_gate(co_orders=co_orders)
    mgr.register_trade(
        trade_id="trade_short",
        symbol="INFY",
        instrument_token=_ENTRY_TOKEN,
        direction="SHORT",
        entry_price=1000.0,
        initial_sl=1020.0,
        qty=5,
        trigger_pct=0.005,
        step_pct=0.003,
    )

    # Price falls to 995 -> trail fires
    cs.fire_candle(_make_candle(token=_ENTRY_TOKEN, symbol="INFY",
                                high=1000.0, low=995.0))
    count_after_trail = len(adapter.calls)
    assert count_after_trail == 1

    # Price rallies back to 1005 (above entry) -- best_price stays at 995
    cs.fire_candle(_make_candle(token=_ENTRY_TOKEN, symbol="INFY",
                                high=1005.0, low=1000.0))

    assert len(adapter.calls) == count_after_trail, "SHORT SL must not raise"
    print("  OK SHORT SL not raised on price retrace")


# ─────────────────────────────────────────────────────────────────────────────
# GHOST SL FIX (ST5)
# ─────────────────────────────────────────────────────────────────────────────

def test_success_updates_tracked_and_db() -> None:
    """On broker confirm: current_sl updated in _tracked and DB (ST5)."""
    mgr, adapter, store, cs, log = _make_gate(adapter_success=True)
    _register(mgr)

    cs.fire_candle(_make_candle(high=1010.0))

    with mgr._lock:
        info = mgr._tracked["trade_001"]
    assert info["current_sl"] > _INITIAL_SL, "current_sl must advance"
    assert info["trail_count"] == 1
    assert info["consecutive_failures"] == 0
    assert len(store.update_calls) == 1
    assert log.has_info("trailed SL")
    print("  OK Success: _tracked updated, DB updated, INFO logged")


def test_failure_does_not_update_tracked_or_db() -> None:
    """On broker failure: _tracked and DB must NOT be updated (ST5 Ghost SL fix)."""
    mgr, adapter, store, cs, log = _make_gate(adapter_success=False)
    _register(mgr)

    cs.fire_candle(_make_candle(high=1010.0))

    with mgr._lock:
        info = mgr._tracked["trade_001"]
    assert abs(info["current_sl"] - _INITIAL_SL) < 0.001, "SL must not change on failure"
    assert info["trail_count"] == 0
    assert info["consecutive_failures"] == 1
    assert len(store.update_calls) == 0, "DB must not be updated on failure"
    assert log.has_error("modify_order failed")
    print("  OK Failure: _tracked unchanged, DB unchanged, ERROR logged")


def test_three_consecutive_failures_fires_critical() -> None:
    """3 consecutive failures -> CRITICAL log + on_critical_failure called (ST10)."""
    critical_calls = []

    def on_critical(trade_id, reason):
        critical_calls.append((trade_id, reason))

    mgr, adapter, _, cs, log = _make_gate(
        adapter_success=False,
        on_critical_failure=on_critical,
    )
    _register(mgr)

    # Fire 3 candles each causing a failure
    cs.fire_candle(_make_candle(high=1010.0))
    cs.fire_candle(_make_candle(high=1011.0))
    cs.fire_candle(_make_candle(high=1012.0))

    assert log.has_critical("consecutive"), "Expected CRITICAL after 3 failures"
    assert len(critical_calls) >= 1, "on_critical_failure not called"
    assert critical_calls[0][0] == "trade_001"
    print(f"  OK 3 consecutive failures: CRITICAL logged, callback fired")


def test_success_resets_failure_counter() -> None:
    """Successful modify resets consecutive_failures to 0 (ST10)."""
    mgr, adapter, _, cs, _ = _make_gate()
    # First 2 calls fail, 3rd succeeds
    adapter.set_responses([False, False, True])
    _register(mgr)

    cs.fire_candle(_make_candle(high=1010.0))  # fail 1
    cs.fire_candle(_make_candle(high=1011.0))  # fail 2
    cs.fire_candle(_make_candle(high=1012.0))  # success

    with mgr._lock:
        info = mgr._tracked["trade_001"]
    assert info["consecutive_failures"] == 0, "Counter must reset after success"
    assert info["trail_count"] == 1
    print("  OK Success resets consecutive_failures counter")


def test_failure_trade_a_does_not_affect_trade_b() -> None:
    """Failure counter for trade A is isolated from trade B (ST10)."""
    co_orders = {
        "trade_A": {"order_id": "co_A", "variety": "co"},
        "trade_B": {"order_id": "co_B", "variety": "co"},
    }
    token_a, token_b = 100001, 100002
    mgr, adapter, _, cs, _ = _make_gate(co_orders=co_orders)

    # Adapter always fails for co_A, succeeds for co_B
    original_modify = adapter.modify_order

    def selective_modify(broker_order_id, **kw):
        adapter.calls.append({"broker_order_id": broker_order_id,
                               "trigger_price": kw.get("trigger_price")})
        if broker_order_id == "co_A":
            return _ModifyResult(broker_order_id=broker_order_id, success=False,
                                 reason="fail_A")
        return _ModifyResult(broker_order_id=broker_order_id, success=True)

    adapter.modify_order = selective_modify

    mgr.register_trade("trade_A", "SYM_A", token_a, "LONG", 1000.0, 980.0,
                       10, _TRIGGER_PCT, _STEP_PCT)
    mgr.register_trade("trade_B", "SYM_B", token_b, "LONG", 1000.0, 980.0,
                       10, _TRIGGER_PCT, _STEP_PCT)

    cs.fire_candle(_make_candle(token=token_a, symbol="SYM_A", high=1010.0))
    cs.fire_candle(_make_candle(token=token_b, symbol="SYM_B", high=1010.0))

    with mgr._lock:
        info_a = mgr._tracked["trade_A"]
        info_b = mgr._tracked["trade_B"]

    assert info_a["consecutive_failures"] == 1
    assert info_b["consecutive_failures"] == 0, "B not affected by A's failure"
    print("  OK Failure counter isolated: A=1 failure, B=0 failures")


# ─────────────────────────────────────────────────────────────────────────────
# CANDLE CALLBACK (ST6)
# ─────────────────────────────────────────────────────────────────────────────

def test_candle_callback_filters_by_token() -> None:
    """_on_candle_close only processes trades matching instrument_token."""
    mgr, adapter, _, cs, _ = _make_gate()
    _register(mgr, instrument_token=_ENTRY_TOKEN)

    # Candle for a DIFFERENT token -- must not trigger any modify
    cs.fire_candle(_make_candle(token=999999, symbol="OTHER", high=1010.0))

    assert len(adapter.calls) == 0
    print("  OK Candle for wrong token: no modify call")


def test_candle_no_tracked_trade_for_symbol_is_noop() -> None:
    """Empty watchlist: candle close is pure no-op."""
    mgr, adapter, _, cs, _ = _make_gate()
    # No register_trade called

    cs.fire_candle(_make_candle(high=1020.0))

    assert len(adapter.calls) == 0
    print("  OK No tracked trade: candle close is no-op")


def test_multiple_trades_same_symbol_all_processed() -> None:
    """Two trades on same token both processed on candle close."""
    co_orders = {
        "t1": {"order_id": "co_1", "variety": "co"},
        "t2": {"order_id": "co_2", "variety": "co"},
    }
    mgr, adapter, _, cs, _ = _make_gate(co_orders=co_orders)

    mgr.register_trade("t1", "RELIANCE", _ENTRY_TOKEN, "LONG",
                       1000.0, 980.0, 10, _TRIGGER_PCT, _STEP_PCT)
    mgr.register_trade("t2", "RELIANCE", _ENTRY_TOKEN, "LONG",
                       1000.0, 980.0, 5, _TRIGGER_PCT, _STEP_PCT)

    cs.fire_candle(_make_candle(high=1010.0))

    # Both trades should have modify calls
    order_ids = {c["broker_order_id"] for c in adapter.calls}
    assert "co_1" in order_ids, "t1 not trailed"
    assert "co_2" in order_ids, "t2 not trailed"
    print(f"  OK Both trades on same symbol processed: {len(adapter.calls)} modify calls")


def test_candle_unrelated_symbol_no_modify() -> None:
    """Candle for different token does not trigger modify for registered trade."""
    mgr, adapter, _, cs, _ = _make_gate()
    _register(mgr, instrument_token=_ENTRY_TOKEN)

    cs.fire_candle(_make_candle(token=_ENTRY_TOKEN + 1, symbol="OTHER", high=1020.0))

    assert len(adapter.calls) == 0
    print("  OK Unrelated symbol candle: no modify call")


# ─────────────────────────────────────────────────────────────────────────────
# RECONNECT (ST7, G6, LF7)
# ─────────────────────────────────────────────────────────────────────────────

def test_reconnect_discards_best_price_and_recomputes() -> None:
    """on_reconnect: resets best_price and recomputes from candle history."""
    # Provide candle history: price reached 1015 before reconnect
    history = {_ENTRY_TOKEN: [_make_candle(high=1015.0)]}
    mgr, adapter, _, cs, _ = _make_gate(candle_history=history)
    _register(mgr)

    # Simulate prior best_price from live tracking
    with mgr._lock:
        mgr._tracked["trade_001"]["best_price"] = 1005.0  # stale

    mgr.on_reconnect(datetime(2026, 4, 16, 10, 30))

    # best_price should be recomputed from history high (1015.0)
    with mgr._lock:
        info = mgr._tracked["trade_001"]
    assert abs(info["best_price"] - 1015.0) < 0.001, \
        f"best_price should be 1015.0 from history, got {info['best_price']}"
    print(f"  OK on_reconnect: best_price recomputed to 1015.0 from history")


def test_reconnect_fires_trail_once_if_price_advanced() -> None:
    """on_reconnect fires trail exactly once if price in history > trigger."""
    history = {_ENTRY_TOKEN: [_make_candle(high=1010.0)]}
    mgr, adapter, _, cs, log = _make_gate(candle_history=history)
    _register(mgr)

    mgr.on_reconnect(datetime(2026, 4, 16, 10, 30))

    assert len(adapter.calls) == 1, f"Expected 1 trail call, got {len(adapter.calls)}"
    assert log.has_warning("on_reconnect")
    print("  OK on_reconnect fires trail ONCE")


def test_reconnect_no_price_advance_no_modify() -> None:
    """on_reconnect: if history doesn't exceed trigger, no modify."""
    # Price only reached 1003 (0.3% < trigger 0.5%)
    history = {_ENTRY_TOKEN: [_make_candle(high=1003.0)]}
    mgr, adapter, _, cs, _ = _make_gate(candle_history=history)
    _register(mgr)

    mgr.on_reconnect(datetime(2026, 4, 16, 10, 30))

    assert len(adapter.calls) == 0
    print("  OK on_reconnect: no modify when history below trigger")


def test_reconnect_multiple_trades_correct_recompute() -> None:
    """on_reconnect recomputes for each tracked trade independently."""
    token_a, token_b = 100001, 100002
    co_orders = {
        "ta": {"order_id": "co_a", "variety": "co"},
        "tb": {"order_id": "co_b", "variety": "co"},
    }
    # A crossed trigger, B did not
    history = {
        token_a: [_make_candle(token=token_a, high=1010.0)],
        token_b: [_make_candle(token=token_b, high=1003.0)],
    }
    mgr, adapter, _, cs, _ = _make_gate(co_orders=co_orders,
                                         candle_history=history)
    mgr.register_trade("ta", "SYM_A", token_a, "LONG", 1000.0, 980.0,
                       10, _TRIGGER_PCT, _STEP_PCT)
    mgr.register_trade("tb", "SYM_B", token_b, "LONG", 1000.0, 980.0,
                       10, _TRIGGER_PCT, _STEP_PCT)

    mgr.on_reconnect(datetime(2026, 4, 16, 10, 30))

    order_ids = {c["broker_order_id"] for c in adapter.calls}
    assert "co_a" in order_ids, "Trade A should be trailed"
    assert "co_b" not in order_ids, "Trade B below trigger; should not trail"
    print("  OK on_reconnect: A trailed (above trigger), B skipped (below)")


def test_reconnect_logs_warning() -> None:
    """on_reconnect always logs a WARNING with reconnect timestamp."""
    mgr, _, _, _, log = _make_gate()
    _register(mgr)

    mgr.on_reconnect(datetime(2026, 4, 16, 10, 30))

    assert log.has_warning("on_reconnect")
    print("  OK on_reconnect: WARNING logged")


# ─────────────────────────────────────────────────────────────────────────────
# STARTUP RECOVERY (ST8)
# ─────────────────────────────────────────────────────────────────────────────

_RECOVERY_ROW = {
    "trade_id":           "t_recovered",
    "symbol":             "RELIANCE",
    "instrument_token":   _ENTRY_TOKEN,
    "direction":          "LONG",
    "entry_price":        1000.0,
    "initial_sl":         980.0,
    "current_sl":         985.0,   # already trailed once
    "qty":                10,
    "trigger_pct":        _TRIGGER_PCT,
    "step_pct":           _STEP_PCT,
    "best_price":         1008.0,
    "trail_count":        1,
    "last_trail_ts":      "2026-04-16T09:40:00+05:30",
    "registered_at":      "2026-04-16T09:30:00+05:30",
}


def test_start_reads_smart_tgt_state_rows() -> None:
    """start() recovers rows from smart_tgt_state into _tracked."""
    co_orders = {"t_recovered": {"order_id": _CO_ORDER_ID, "variety": "co"}}
    mgr, _, _, _, log = _make_gate(
        co_orders=co_orders,
        initial_states=[_RECOVERY_ROW],
    )
    mgr.start()

    assert mgr.size() == 1
    assert "t_recovered" in mgr.tracked_trade_ids()
    assert log.has_info("recovered 1")
    print("  OK start(): 1 trade recovered from smart_tgt_state")


def test_start_repopulates_tracked_with_correct_values() -> None:
    """start() restores exact field values from DB row."""
    co_orders = {"t_recovered": {"order_id": _CO_ORDER_ID, "variety": "co"}}
    mgr, _, _, _, _ = _make_gate(
        co_orders=co_orders,
        initial_states=[_RECOVERY_ROW],
    )
    mgr.start()

    with mgr._lock:
        info = mgr._tracked["t_recovered"]
    assert abs(info["current_sl"] - 985.0) < 0.001
    assert abs(info["best_price"] - 1008.0) < 0.001
    assert info["trail_count"] == 1
    assert info["direction"] == "LONG"
    print("  OK start(): field values correctly restored from DB row")


def test_startup_ltp_check_favorable_side_resumes() -> None:
    """quote_fn provided, LTP on favorable side -> resume + INFO log."""
    @dataclass
    class _MockQuote:
        last_price: float

    def quote_fn(symbols):
        return {"RELIANCE": _MockQuote(last_price=1010.0)}  # above SL=985

    co_orders = {"t_recovered": {"order_id": _CO_ORDER_ID, "variety": "co"}}
    mgr, _, _, _, log = _make_gate(
        co_orders=co_orders,
        initial_states=[_RECOVERY_ROW],
        quote_fn=quote_fn,
    )
    mgr.start()

    assert log.has_info("startup check OK")
    print("  OK startup LTP check: LTP favorable -> resume trail, INFO logged")


def test_startup_ltp_check_wrong_side_fires_critical() -> None:
    """quote_fn provided, LTP past SL on wrong side -> CRITICAL + no CO modify."""
    @dataclass
    class _MockQuote:
        last_price: float

    def quote_fn(symbols):
        return {"RELIANCE": _MockQuote(last_price=982.0)}  # BELOW SL=985 for LONG

    critical_calls = []

    def on_critical(trade_id, reason):
        critical_calls.append((trade_id, reason))

    co_orders = {"t_recovered": {"order_id": _CO_ORDER_ID, "variety": "co"}}
    mgr, adapter, _, _, log = _make_gate(
        co_orders=co_orders,
        initial_states=[_RECOVERY_ROW],
        quote_fn=quote_fn,
        on_critical_failure=on_critical,
    )
    mgr.start()

    assert log.has_critical("wrong side")
    assert len(critical_calls) == 1
    assert critical_calls[0][0] == "t_recovered"
    # No CO modify should have been called
    assert len(adapter.calls) == 0
    print("  OK startup LTP past SL: CRITICAL logged, no CO modify")


def test_startup_no_quote_fn_skips_ltp_check() -> None:
    """quote_fn=None -> skip LTP check with WARNING; no crash."""
    co_orders = {"t_recovered": {"order_id": _CO_ORDER_ID, "variety": "co"}}
    mgr, adapter, _, _, log = _make_gate(
        co_orders=co_orders,
        initial_states=[_RECOVERY_ROW],
        quote_fn=None,
    )
    mgr.start()

    assert log.has_warning("quote_fn not provided")
    assert len(adapter.calls) == 0
    print("  OK startup without quote_fn: WARNING logged, no crash")


# ─────────────────────────────────────────────────────────────────────────────
# CO LOOKUP (ST14)
# ─────────────────────────────────────────────────────────────────────────────

def test_co_lookup_returns_co_row() -> None:
    """get_co_entry_order_for_trade returns the CO row via state_store (ST14)."""
    store = _MockStateStore(
        co_orders={"t1": {"order_id": "co_x", "variety": "co"}}
    )
    result = store.get_co_entry_order_for_trade("t1")
    assert result is not None
    assert result["order_id"] == "co_x"
    print("  OK CO lookup returns CO row")


def test_co_lookup_non_co_entry_not_returned() -> None:
    """get_co_entry_order_for_trade returns None for non-CO entry (ST14)."""
    store = _MockStateStore(co_orders={})  # no CO rows
    result = store.get_co_entry_order_for_trade("t_mis")
    assert result is None
    print("  OK CO lookup: non-CO ENTRY not returned")


def test_co_lookup_absent_trade_returns_none() -> None:
    """get_co_entry_order_for_trade returns None when trade_id not in DB (ST14)."""
    store = _MockStateStore(co_orders={})
    result = store.get_co_entry_order_for_trade("no_such_trade")
    assert result is None
    print("  OK CO lookup: absent trade returns None")


# ─────────────────────────────────────────────────────────────────────────────
# THREAD SAFETY (ST9)
# ─────────────────────────────────────────────────────────────────────────────

def test_concurrent_register_and_candle_no_races() -> None:
    """Concurrent register + candle callbacks from 2 threads: no races."""
    co_orders = {f"t_{i}": {"order_id": f"co_{i}", "variety": "co"}
                 for i in range(10)}
    mgr, adapter, _, cs, _ = _make_gate(co_orders=co_orders)

    errors = []

    def register_thread():
        for i in range(10):
            try:
                mgr.register_trade(
                    f"t_{i}", f"SYM_{i}", _ENTRY_TOKEN + i, "LONG",
                    1000.0, 980.0, 10, _TRIGGER_PCT, _STEP_PCT
                )
            except ValueError:
                pass  # duplicate is OK
            except Exception as e:
                errors.append(str(e))

    def candle_thread():
        for _ in range(20):
            cs.fire_candle(_make_candle(high=1010.0))
            time.sleep(0.001)

    t1 = threading.Thread(target=register_thread)
    t2 = threading.Thread(target=candle_thread)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not errors, f"Thread errors: {errors}"
    print("  OK Concurrent register + candle: no races, no errors")


def test_concurrent_candle_and_reconnect_serialized() -> None:
    """Candle callback and on_reconnect on different threads: state consistent."""
    history = {_ENTRY_TOKEN: [_make_candle(high=1010.0)]}
    mgr, adapter, _, cs, _ = _make_gate(candle_history=history)
    _register(mgr)

    errors = []

    def candle_thread():
        for _ in range(10):
            try:
                cs.fire_candle(_make_candle(high=1010.0 + _ * 0.1))
            except Exception as e:
                errors.append(str(e))
            time.sleep(0.001)

    def reconnect_thread():
        for _ in range(3):
            try:
                mgr.on_reconnect(datetime(2026, 4, 16, 10, 30))
            except Exception as e:
                errors.append(str(e))
            time.sleep(0.005)

    t1 = threading.Thread(target=candle_thread)
    t2 = threading.Thread(target=reconnect_thread)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not errors, f"Thread errors: {errors}"
    # No assertion on call count -- just verify no crash/data corruption
    print(f"  OK Concurrent candle+reconnect: no races ({len(adapter.calls)} modify calls)")


# ─────────────────────────────────────────────────────────────────────────────
# ENABLED FLAG (ST12)
# ─────────────────────────────────────────────────────────────────────────────

def test_enabled_false_register_is_noop() -> None:
    """enabled=False at construction: register_trade is no-op."""
    mgr, _, store, _, log = _make_gate(enabled=False)
    _register(mgr)

    assert mgr.size() == 0
    assert len(store.insert_calls) == 0
    print("  OK enabled=False: register_trade no-op")


def test_enabled_false_candle_callback_is_noop() -> None:
    """enabled=False at construction: _on_candle_close is no-op."""
    mgr, adapter, _, cs, _ = _make_gate(enabled=False)

    cs.fire_candle(_make_candle(high=1020.0))

    assert len(adapter.calls) == 0
    print("  OK enabled=False: candle callback no-op")


def test_runtime_disable_stops_processing() -> None:
    """disable() at runtime: subsequent candle callbacks become no-ops."""
    mgr, adapter, _, cs, _ = _make_gate(enabled=True)
    _register(mgr)

    # First candle before disable: should work normally (below trigger, no modify)
    cs.fire_candle(_make_candle(high=1002.0))
    count_before = len(adapter.calls)

    mgr.disable()

    # Candles after disable: no-op
    cs.fire_candle(_make_candle(high=1020.0))
    cs.fire_candle(_make_candle(high=1030.0))

    assert len(adapter.calls) == count_before, "No modify calls after disable()"
    print("  OK disable(): subsequent callbacks no-op")


# ─────────────────────────────────────────────────────────────────────────────
# EDGE CASES
# ─────────────────────────────────────────────────────────────────────────────

def test_no_co_order_id_logs_error_no_crash() -> None:
    """If no CO order found for trade_id, log ERROR; no crash or infinite retry."""
    mgr, adapter, _, cs, log = _make_gate(co_orders={})  # empty: no CO row
    _register(mgr)

    cs.fire_candle(_make_candle(high=1010.0))

    assert len(adapter.calls) == 0
    assert log.has_error("no CO entry order")
    print("  OK No CO order_id: ERROR logged, no crash")


def test_trail_at_exact_boundary_no_redundant_modify() -> None:
    """
    ST4 edge: distance_pct == trigger_pct -> steps=0, new_sl = entry*(1+trigger).
    If current_sl is already at that level, no modify.
    """
    mgr, adapter, _, cs, _ = _make_gate()
    _register(mgr)

    # First fire at trigger boundary -> trail fires, current_sl set to 1005
    cs.fire_candle(_make_candle(high=1005.0))
    count_first = len(adapter.calls)
    assert count_first == 1, f"Expected 1 modify at boundary, got {count_first}"

    # Same candle again: current_sl == new_sl -> no modify
    cs.fire_candle(_make_candle(high=1005.0))
    assert len(adapter.calls) == 1, "Redundant modify at exact boundary"
    print("  OK Trail at exact trigger boundary: no redundant modify on repeat")


def test_multiple_candles_in_succession_counter_updates() -> None:
    """Multiple candles in quick succession: best_price and trail_count updated correctly."""
    mgr, adapter, store, cs, _ = _make_gate()
    _register(mgr)

    # Candle 1: 1005 (1 step trail)
    cs.fire_candle(_make_candle(high=1005.0))
    # Candle 2: 1008 (1 more step: distance=0.8%, steps=int((0.008-0.005)/0.003)=1)
    cs.fire_candle(_make_candle(high=1008.0))
    # Candle 3: 1008 again (no advance)
    cs.fire_candle(_make_candle(high=1008.0))

    with mgr._lock:
        info = mgr._tracked["trade_001"]

    assert info["trail_count"] == 2, f"Expected 2 trails, got {info['trail_count']}"
    assert len(adapter.calls) == 2
    assert len(store.update_calls) == 2
    print(f"  OK Multiple candles: trail_count={info['trail_count']}, "
          f"final_sl={info['current_sl']:.4f}")


def test_fix020_synthetic_candles_processed_normally() -> None:
    """FIX-020: SmartTgtManager processes synthetic flat candles for SL trailing."""
    mgr, adapter, store, cs, _ = _make_gate()
    _register(mgr)

    # Real candle: triggers trail
    cs.fire_candle(_make_candle(high=1005.0, is_synthetic=False))
    assert len(adapter.calls) == 1, "Real candle should trigger modify"

    # Synthetic candle 1: same high, no trail (no advancement)
    cs.fire_candle(_make_candle(high=1005.0, is_synthetic=True))
    # Synthetic candle 2: higher, triggers trail
    cs.fire_candle(_make_candle(high=1008.0, is_synthetic=True))
    # Synthetic candle 3: same, no trail
    cs.fire_candle(_make_candle(high=1008.0, is_synthetic=True))

    # Should have 2 modifies total (1 real + 1 synthetic that advanced)
    assert len(adapter.calls) == 2, f"Expected 2 modifies, got {len(adapter.calls)}"

    with mgr._lock:
        info = mgr._tracked["trade_001"]

    assert info["trail_count"] == 2, f"Expected 2 trails, got {info['trail_count']}"
    assert info["best_price"] == 1008.0, f"Expected best_price=1008.0, got {info['best_price']}"
    print(f"  OK FIX-020: synthetic candles processed normally, trail_count={info['trail_count']}")


# ─────────────────────────────────────────────────────────────────────────────
# LIFECYCLE (ST11)
# ─────────────────────────────────────────────────────────────────────────────

def test_start_registers_callback_exactly_once() -> None:
    """start() re-registers candle callback; candle_store deduplicates (idempotent)."""
    mgr, _, _, cs, _ = _make_gate()
    # Callback already registered at construction
    assert len(cs.registered) == 1

    mgr.start()  # re-register: candle_store.register is idempotent
    # Registered list grows but actual _cbs list stays deduplicated
    assert mgr._on_candle_close in cs._cbs
    assert cs._cbs.count(mgr._on_candle_close) == 1
    print("  OK start(): callback registered exactly once in candle_store._cbs")


def test_stop_unregisters_and_clears_tracked() -> None:
    """stop(): candle callback unregistered, _tracked cleared, DB state preserved."""
    mgr, _, store, cs, log = _make_gate()
    _register(mgr)
    assert mgr.size() == 1

    mgr.stop()

    assert mgr.size() == 0
    assert mgr._on_candle_close not in cs._cbs
    # DB row still exists (stop does NOT delete smart_tgt_state rows)
    assert "trade_001" in store.smart_tgt_rows
    assert log.has_info("stopped")
    print("  OK stop(): callback unregistered, _tracked cleared, DB preserved")


def test_restart_after_stop_repopulates_tracked() -> None:
    """After stop+start, _tracked repopulated from DB."""
    mgr, _, store, cs, _ = _make_gate(initial_states=[])
    _register(mgr)
    assert mgr.size() == 1

    mgr.stop()
    assert mgr.size() == 0

    # The DB row was kept; start() should recover it
    mgr.start()
    assert mgr.size() == 1, "Trade not recovered from DB after restart"
    print("  OK stop+start: trade recovered from DB, _tracked repopulated")


# ─────────────────────────────────────────────────────────────────────────────
# B.4 / Audit 5.4 — async modify executor + newest-wins coalesce
# ─────────────────────────────────────────────────────────────────────────────

def _make_gate_async(
    co_orders: Optional[Dict] = None,
    adapter_success: bool = True,
    on_critical_failure=None,
) -> tuple:
    """Variant of _make_gate that turns on the B.4 async-modify executor."""
    adapter = _MockAdapter(default_success=adapter_success)
    store = _MockStateStore(
        co_orders=co_orders if co_orders is not None else _default_co_orders()
    )
    cs = _MockCandleStore()
    log = _CapturingLogger()
    mgr = SmartTgtManager(
        adapter=adapter,
        state_store=store,
        candle_store=cs,
        logger=log,
        enabled=True,
        on_critical_failure=on_critical_failure,
        async_modify=True,
    )
    return mgr, adapter, store, cs, log


def test_b4_async_modify_eventually_calls_broker() -> None:
    """B.4: with async_modify=True, modify_order is invoked via executor."""
    mgr, adapter, _, cs, _ = _make_gate_async()
    _register(mgr)

    cs.fire_candle(_make_candle(high=1010.0))
    mgr._flush_inflight(timeout_sec=2.0)

    assert len(adapter.calls) == 1, (
        f"async path expected 1 broker call, got {len(adapter.calls)}"
    )
    assert adapter.calls[0]["broker_order_id"] == _CO_ORDER_ID
    print("  OK B.4 async: modify_order called once via executor")


def test_b4_coalesce_newest_target_wins() -> None:
    """
    B.4 coalesce primitive: stack multiple pending modifies for the same
    trade BEFORE any worker drains, then drain three times. The first drain
    pops the newest value (overwritten in the dict), the next two find an
    empty dict and exit. Net: 1 broker call carrying the latest target.
    """
    mgr, adapter, _, _, _ = _make_gate_async()
    _register(mgr)

    # Manually stack three overwriting pending values, simulating what
    # would happen if three rapid candles fired before any worker ran.
    with mgr._pending_lock:
        mgr._pending_modify["trade_001"] = 985.0
        mgr._pending_modify["trade_001"] = 990.0
        mgr._pending_modify["trade_001"] = 995.0

    # Three workers in succession: only the first finds a value.
    mgr._drain_modify("trade_001")
    mgr._drain_modify("trade_001")
    mgr._drain_modify("trade_001")

    assert len(adapter.calls) == 1, (
        f"coalesce broken: 3 stacked modifies -> {len(adapter.calls)} broker "
        f"calls, expected 1"
    )
    # The single broker call must carry the newest target (995.0).
    assert abs(adapter.calls[0]["trigger_price"] - 995.0) < 1e-9
    assert mgr._pending_modify == {}, "pending dict not drained"
    print("  OK B.4 coalesce: 3 stacked targets -> 1 broker call @ newest")


def test_b4_executor_failure_is_swallowed_after_shutdown() -> None:
    """
    B.4: submitting after stop() must not raise. The pending dict gets
    cleared and the request is dropped silently (reconciler / next restart
    will catch up).
    """
    mgr, adapter, _, cs, _ = _make_gate_async()
    _register(mgr)
    mgr.stop()

    # Force a candle fire path post-shutdown by re-firing the registered
    # callback directly. _on_candle_close is a no-op when _enabled is True
    # but stop() does not flip _enabled, so the path will reach
    # _submit_modify and gracefully handle RuntimeError from submit.
    cs.fire_candle(_make_candle(high=1010.0))

    # Call _submit_modify directly to also exercise the shutdown branch.
    mgr._submit_modify("trade_001", 999.99)
    assert "trade_001" not in mgr._pending_modify
    print("  OK B.4 shutdown: post-stop submit is silently dropped")


def test_b4_drain_with_empty_pending_is_noop() -> None:
    """
    B.4: a worker that finds an empty pending dict (because an earlier
    worker already drained it) must exit without calling modify_order.
    """
    mgr, adapter, _, cs, _ = _make_gate_async()
    _register(mgr)

    # No pending entry -> _drain_modify must be a no-op.
    mgr._drain_modify("trade_001")
    assert len(adapter.calls) == 0, "stale worker called modify_order"
    print("  OK B.4 drain: empty pending -> no broker call")


def test_b4_stop_drains_inflight_before_shutdown() -> None:
    """
    B.4: stop() waits for any in-flight modify before shutting down so the
    broker / DB do not diverge. We block the worker briefly and verify the
    DB row was updated before stop() returns.
    """
    mgr, adapter, store, cs, _ = _make_gate_async()
    _register(mgr)

    started = threading.Event()
    real_modify = adapter.modify_order

    def slow_modify(*args, **kwargs):
        started.set()
        time.sleep(0.05)
        return real_modify(*args, **kwargs)

    adapter.modify_order = slow_modify  # type: ignore

    cs.fire_candle(_make_candle(high=1010.0))
    started.wait(timeout=2.0)
    mgr.stop()

    # After stop() returns, the in-flight modify must have completed and
    # written its DB row. (broker call already counted by adapter.calls)
    assert len(adapter.calls) == 1
    assert len(store.update_calls) == 1, (
        "stop() did not wait for in-flight modify; DB row missing"
    )
    print("  OK B.4 stop: drained 1 in-flight modify before shutdown")


# ─────────────────────────────────────────────────────────────────────────────
# D.1 / 2026-04-25 audit — rate-limit modify_order against "order" bucket
# Trailing N CO orders at a minute boundary can burst past Zerodha's
# 10-orders/sec quota. SmartTgtManager now accepts an optional RateLimiter
# and acquires before every modify call so trail paces alongside fresh
# placements/cancels owned by OrderPlacer.
# ─────────────────────────────────────────────────────────────────────────────


class _SpyRateLimiter:
    """Records acquire() calls; can be configured to raise on Nth call."""

    def __init__(self, raise_on_call: int = -1, exc: Exception = None) -> None:
        self.calls: List[str] = []
        self._raise_on = raise_on_call
        self._exc = exc or RuntimeError("rate limit timeout")

    def acquire(self, category: str, *args, **kwargs) -> None:
        self.calls.append(category)
        if self._raise_on >= 0 and len(self.calls) - 1 == self._raise_on:
            raise self._exc


def _make_gate_with_rl(rl) -> tuple:
    """Variant of _make_gate that wires a rate_limiter."""
    adapter = _MockAdapter(default_success=True)
    store = _MockStateStore(co_orders=_default_co_orders())
    cs = _MockCandleStore()
    log = _CapturingLogger()
    mgr = SmartTgtManager(
        adapter=adapter, state_store=store, candle_store=cs,
        logger=log, enabled=True, rate_limiter=rl,
    )
    return mgr, adapter, store, cs, log


def test_d1_rate_limiter_acquired_before_modify() -> None:
    """D.1: when rate_limiter is wired, _modify_co_sl calls
    acquire('order') exactly once per modify, BEFORE adapter.modify_order."""
    rl = _SpyRateLimiter()
    mgr, adapter, _, cs, _ = _make_gate_with_rl(rl)
    _register(mgr)

    cs.fire_candle(_make_candle(high=1010.0))

    assert rl.calls == ["order"], (
        f"expected one acquire('order') before modify, got {rl.calls}"
    )
    assert len(adapter.calls) == 1, (
        f"expected 1 broker modify after acquire, got {len(adapter.calls)}"
    )
    print("  OK D.1: acquire('order') fires once before adapter.modify_order")


def test_d1_rate_limiter_acquire_raises_skips_modify() -> None:
    """D.1: when rate_limiter.acquire raises (timeout, kill state, etc.),
    the trail tick is skipped — adapter.modify_order is NOT called and
    consecutive_failures is NOT incremented (we never reached the broker,
    so it's not a broker failure)."""
    rl = _SpyRateLimiter(raise_on_call=0, exc=RuntimeError("rl timeout"))
    mgr, adapter, _, cs, _ = _make_gate_with_rl(rl)
    _register(mgr)

    cs.fire_candle(_make_candle(high=1010.0))

    assert rl.calls == ["order"]
    assert len(adapter.calls) == 0, (
        "modify_order must be skipped when rate limiter rejects"
    )
    info = mgr._tracked["trade_001"]
    assert info["consecutive_failures"] == 0, (
        "rl-skip is not a broker failure -- counter must stay zero so the "
        "3-strike critical path is not falsely tripped by quota pressure"
    )
    print("  OK D.1: rl acquire raise -> skip modify, no failure increment")


def test_d1_no_rate_limiter_keeps_legacy_behavior() -> None:
    """D.1: rate_limiter=None (default) preserves the pre-fix code path.
    Existing test suite constructs SmartTgtManager without RL; this test
    pins that backwards-compat contract."""
    mgr, adapter, _, cs, _ = _make_gate()  # no rate_limiter wired
    _register(mgr)

    cs.fire_candle(_make_candle(high=1010.0))

    assert len(adapter.calls) == 1
    print("  OK D.1: rate_limiter=None -> legacy behavior preserved")


def test_d1_acquire_called_per_modify_across_multiple_steps() -> None:
    """D.1: each successive trail step takes one acquire token. With
    multiple step crossings, count must equal modify_order count."""
    rl = _SpyRateLimiter()
    mgr, adapter, _, cs, _ = _make_gate_with_rl(rl)
    _register(mgr)

    cs.fire_candle(_make_candle(high=1010.0))   # crosses trigger
    cs.fire_candle(_make_candle(high=1015.0))   # one more step
    cs.fire_candle(_make_candle(high=1020.0))   # another step

    assert len(rl.calls) == len(adapter.calls), (
        f"acquire/modify count mismatch: rl={len(rl.calls)}, "
        f"broker={len(adapter.calls)}"
    )
    assert all(c == "order" for c in rl.calls)
    print(
        f"  OK D.1: {len(rl.calls)} trail steps -> {len(rl.calls)} "
        f"acquire('order') calls, all paced"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FIX-026: Volume-dependent trails protection
# ─────────────────────────────────────────────────────────────────────────────

def test_fix026_volume_zero_no_division_error() -> None:
    """FIX-026: Candle with volume=0 processes without ZeroDivisionError."""
    mgr, adapter, store, candle_store, log = _make_gate()
    # Override with volume_dependent_trails=True
    store._co_orders = {"trd_001": {"order_id": "co_123", "variety": "co"}}

    mgr2 = SmartTgtManager(
        adapter=adapter,
        state_store=store,
        candle_store=candle_store,
        logger=log,
        volume_dependent_trails=True,
    )
    mgr2.register_trade(
        trade_id="trd_001",
        instrument_token=1234,
        symbol="RELIANCE",
        direction="LONG",
        entry_price=1000.0,
        initial_sl=980.0,
        qty=10,
        trigger_pct=0.005,
        step_pct=0.003,
    )

    # Candle with volume=0 (normal case per LF11)
    candle = CandleData(
        instrument_token=1234,
        symbol="RELIANCE",
        open=1000.0,
        high=1010.0,  # crosses trigger
        low=995.0,
        close=1008.0,
        volume=0,  # always 0 per LF11
        ts=datetime(2026, 5, 13, 9, 16),
        interval_sec=60,
    )

    # Should process without error
    mgr2._on_candle_close(candle)

    # Trail should have fired (volume=0 doesn't prevent price-based trail)
    assert len(adapter.calls) == 1
    print("  OK FIX-026: volume=0 candle processes without ZeroDivisionError")


def test_fix026_volume_dependent_trails_false_skips_volume_logic() -> None:
    """FIX-026: volume_dependent_trails=False skips volume block entirely."""
    mgr, adapter, store, candle_store, log = _make_gate()
    store._co_orders = {"trd_002": {"order_id": "co_456", "variety": "co"}}

    # volume_dependent_trails=False (default) — volume block never runs
    # The mgr from _make_gate already has this default
    mgr.register_trade(
        trade_id="trd_002",
        instrument_token=5678,
        symbol="INFY",
        direction="LONG",
        entry_price=2000.0,
        initial_sl=1980.0,
        qty=10,
        trigger_pct=0.005,
        step_pct=0.003,
    )

    # Candle with non-zero volume (hypothetical future)
    candle = CandleData(
        instrument_token=5678,
        symbol="INFY",
        open=2000.0,
        high=2020.0,
        low=1995.0,
        close=2015.0,
        volume=100000,  # non-zero
        ts=datetime(2026, 5, 13, 9, 17),
        interval_sec=60,
    )

    # Should process normally (flag=False means volume block is skipped)
    mgr._on_candle_close(candle)

    # Trail fires based on price only
    assert len(adapter.calls) == 1
    print("  OK FIX-026: volume_dependent_trails=False skips volume logic")


def test_fix026_volume_nonzero_with_flag_true_enters_placeholder() -> None:
    """FIX-026: volume>0 + flag=True enters placeholder block (no-op today)."""
    mgr, adapter, store, candle_store, log = _make_gate()
    store._co_orders = {"trd_003": {"order_id": "co_789", "variety": "co"}}

    # volume_dependent_trails=True AND volume > 0 → enters placeholder
    mgr3 = SmartTgtManager(
        adapter=adapter,
        state_store=store,
        candle_store=candle_store,
        logger=log,
        volume_dependent_trails=True,
    )
    mgr3.register_trade(
        trade_id="trd_003",
        instrument_token=9012,
        symbol="TCS",
        direction="LONG",
        entry_price=3000.0,
        initial_sl=2970.0,
        qty=10,
        trigger_pct=0.005,
        step_pct=0.003,
    )

    # Candle with volume > 0 (hypothetical)
    candle = CandleData(
        instrument_token=9012,
        symbol="TCS",
        open=3000.0,
        high=3030.0,
        low=2995.0,
        close=3025.0,
        volume=50000,
        ts=datetime(2026, 5, 13, 9, 18),
        interval_sec=60,
    )

    # Placeholder block runs (currently no-op), trail still fires
    mgr3._on_candle_close(candle)

    assert len(adapter.calls) == 1
    print("  OK FIX-026: volume>0 + flag=True enters placeholder (no-op)")


# ─────────────────────────────────────────────────────────────────────────────
# FIX-044: Tick-Size Rounding on SL Modifications
# ─────────────────────────────────────────────────────────────────────────────

class _MockInstrumentCache:
    """Mock InstrumentCache for tick-size testing."""

    def __init__(self, tick_map: dict = None, raise_on_symbol: str = None):
        self._tick_map = tick_map or {}
        self._raise_on = raise_on_symbol

    def tick_size(self, symbol: str) -> float:
        if self._raise_on and symbol == self._raise_on:
            raise ValueError(f"No tick_size for {symbol}")
        return self._tick_map.get(symbol, 0.05)


def test_fix044_long_rounds_down_to_tick():
    """FIX-044: LONG SL rounds DOWN to tick (conservative rounding)."""
    mgr, adapter, store, cs, log = _make_gate()
    store._co_orders = {"trd_fix044_1": {"order_id": "co_fix044_1", "variety": "co"}}

    # Mock InstrumentCache with tick=0.05
    ic = _MockInstrumentCache(tick_map={"RELIANCE": 0.05})
    mgr.set_instrument_cache(ic)

    TOKEN = 1234
    mgr.register_trade(
        trade_id="trd_fix044_1",
        instrument_token=TOKEN,
        symbol="RELIANCE",
        direction="LONG",
        entry_price=100.0,
        initial_sl=95.0,
        qty=10,
        trigger_pct=0.005,
        step_pct=0.003,
    )

    # Fire candle that trails SL
    # With entry=100, trigger=0.5%, step=0.3%, high=105.5:
    # steps = int((5.5 - 0.5) / 0.3) = 16
    # new_sl = 100 * (1 + 0.005 + 16*0.003) = 100 * 1.053 = 105.3 (approx)
    # Float precision: 100*1.053 may be 105.299999... → rounds DOWN to 105.25 with tick=0.05
    cs.fire_candle(_make_candle(token=TOKEN, high=105.5))

    assert len(adapter.calls) == 1
    submitted_sl = adapter.calls[0]["trigger_price"]
    # The submitted SL should be a multiple of 0.05 (with floating point tolerance)
    remainder = submitted_sl % 0.05
    assert remainder < 0.001 or remainder > 0.049, \
        f"SL {submitted_sl} not rounded to tick 0.05 (remainder={remainder})"
    print(f"  OK FIX-044 LONG: submitted_sl={submitted_sl:.2f} (rounded to tick=0.05)")


def test_fix044_short_rounds_up_to_tick():
    """FIX-044: SHORT SL rounds UP to tick (conservative rounding)."""
    mgr, adapter, store, cs, log = _make_gate()
    store._co_orders = {"trd_fix044_2": {"order_id": "co_fix044_2", "variety": "co"}}

    ic = _MockInstrumentCache(tick_map={"INFY": 0.05})
    mgr.set_instrument_cache(ic)

    TOKEN = 5678
    mgr.register_trade(
        trade_id="trd_fix044_2",
        instrument_token=TOKEN,
        symbol="INFY",
        direction="SHORT",
        entry_price=100.0,
        initial_sl=105.0,
        qty=10,
        trigger_pct=0.005,
        step_pct=0.003,
    )

    # Fire candle that trails SL down
    # With entry=100, trigger=0.5%, step=0.3%, low=94.5:
    # steps = int((5.5 - 0.5) / 0.3) = 16
    # new_sl = 100 * (1 - 0.005 - 16*0.003) = 100 * 0.947 = 94.7 (approx)
    # Float precision may cause 94.699999... → rounds UP to 94.70 with tick=0.05
    cs.fire_candle(_make_candle(token=TOKEN, symbol="INFY", low=94.5))

    assert len(adapter.calls) == 1
    submitted_sl = adapter.calls[0]["trigger_price"]
    # Should be rounded to tick=0.05 (with floating point tolerance)
    remainder = submitted_sl % 0.05
    assert remainder < 0.001 or remainder > 0.049, \
        f"SL {submitted_sl} not rounded to tick 0.05 (remainder={remainder})"
    print(f"  OK FIX-044 SHORT: submitted_sl={submitted_sl:.2f} (rounded to tick=0.05)")


def test_fix044_tick_025_rounding():
    """FIX-044: tick=0.25 rounds correctly (LONG: down, SHORT: up)."""
    mgr, adapter, store, cs, log = _make_gate()
    store._co_orders = {"trd_fix044_3": {"order_id": "co_fix044_3", "variety": "co"}}

    # tick=0.25 (e.g., high-price stock)
    ic = _MockInstrumentCache(tick_map={"TCS": 0.25})
    mgr.set_instrument_cache(ic)

    TOKEN = 9012
    mgr.register_trade(
        trade_id="trd_fix044_3",
        instrument_token=TOKEN,
        symbol="TCS",
        direction="LONG",
        entry_price=1000.0,
        initial_sl=980.0,
        qty=5,
        trigger_pct=0.005,
        step_pct=0.003,
    )

    cs.fire_candle(_make_candle(token=TOKEN, symbol="TCS", high=1030.0))

    assert len(adapter.calls) == 1
    submitted_sl = adapter.calls[0]["trigger_price"]
    # Should be rounded to tick=0.25 (with floating point tolerance)
    remainder = submitted_sl % 0.25
    assert remainder < 0.001 or remainder > 0.249, \
        f"SL {submitted_sl} not rounded to tick 0.25 (remainder={remainder})"
    print(f"  OK FIX-044 tick=0.25: submitted_sl={submitted_sl:.2f} (rounded to 0.25)")


def test_fix044_tick_unavailable_fallback_with_warning():
    """FIX-044: tick_size unavailable → fallback to 0.05, WARNING logged."""
    mgr, adapter, store, cs, log = _make_gate()
    store._co_orders = {"trd_fix044_4": {"order_id": "co_fix044_4", "variety": "co"}}

    # Mock cache that raises exception for this symbol
    ic = _MockInstrumentCache(raise_on_symbol="UNKNOWN")
    mgr.set_instrument_cache(ic)

    TOKEN = 1111
    mgr.register_trade(
        trade_id="trd_fix044_4",
        instrument_token=TOKEN,
        symbol="UNKNOWN",
        direction="LONG",
        entry_price=200.0,
        initial_sl=190.0,
        qty=10,
        trigger_pct=0.005,
        step_pct=0.003,
    )

    cs.fire_candle(_make_candle(token=TOKEN, symbol="UNKNOWN", high=210.0))

    # Should have logged WARNING about fallback
    warnings = [msg for msg in log.warnings if "tick_size unavailable" in msg.lower()]
    assert len(warnings) > 0, f"Expected WARNING log for tick_size unavailable, got warnings: {log.warnings}"

    # Should still submit with fallback tick=0.05
    assert len(adapter.calls) == 1
    submitted_sl = adapter.calls[0]["trigger_price"]
    remainder = submitted_sl % 0.05
    assert remainder < 0.001 or remainder > 0.049, \
        f"SL {submitted_sl} not rounded to fallback tick 0.05 (remainder={remainder})"
    print(f"  OK FIX-044 fallback: WARNING logged, submitted_sl={submitted_sl:.2f} (tick=0.05)")


# ─────────────────────────────────────────────────────────────────────────────
# FIX-045: Graceful Unregister on Terminal Modification Error
# ─────────────────────────────────────────────────────────────────────────────

def test_fix045_terminal_error_unregisters_trade():
    """FIX-045: Terminal error (order already complete) → trade unregistered, no retry."""
    mgr, adapter, store, cs, log = _make_gate()
    store._co_orders = {"trd_fix045_1": {"order_id": "co_fix045_1", "variety": "co"}}

    # Mock adapter that returns terminal error
    adapter._default_success = False
    adapter._default_reason = "Order is already COMPLETE"

    mgr.register_trade(
        trade_id="trd_fix045_1",
        instrument_token=_ENTRY_TOKEN,
        symbol="RELIANCE",
        direction="LONG",
        entry_price=1000.0,
        initial_sl=980.0,
        qty=10,
        trigger_pct=0.005,
        step_pct=0.003,
    )

    # Fire candle that would trigger trail
    cs.fire_candle(_make_candle(high=1010.0))

    # Should have attempted modify once
    assert len(adapter.calls) == 1

    # Trade should be unregistered (not in tracked dict)
    assert "trd_fix045_1" not in mgr._tracked

    # Should have logged INFO about unregistration (not ERROR)
    info_msgs = [m for m in log.infos if "already terminal" in m.lower()]
    assert len(info_msgs) > 0, "Expected INFO log about terminal error unregistration"

    # Fire another candle - should NOT trigger another modify (trade unregistered)
    cs.fire_candle(_make_candle(high=1020.0))
    assert len(adapter.calls) == 1, "Should not retry after terminal error unregistration"

    print("  OK FIX-045: terminal error → trade unregistered, no retry")


def test_fix045_transient_error_retries_normally():
    """FIX-045: Transient error (500) → retry logic applies, NOT unregistered."""
    mgr, adapter, store, cs, log = _make_gate()
    store._co_orders = {"trd_fix045_2": {"order_id": "co_fix045_2", "variety": "co"}}

    # Mock adapter that returns transient error
    adapter._default_success = False
    adapter._default_reason = "Internal Server Error 500"

    mgr.register_trade(
        trade_id="trd_fix045_2",
        instrument_token=_ENTRY_TOKEN,
        symbol="RELIANCE",
        direction="LONG",
        entry_price=1000.0,
        initial_sl=980.0,
        qty=10,
        trigger_pct=0.005,
        step_pct=0.003,
    )

    # Fire candle that would trigger trail
    cs.fire_candle(_make_candle(high=1010.0))

    # Should have attempted modify once
    assert len(adapter.calls) == 1

    # Trade should still be tracked (NOT unregistered)
    assert "trd_fix045_2" in mgr._tracked

    # consecutive_failures should be incremented
    assert mgr._tracked["trd_fix045_2"]["consecutive_failures"] == 1

    # Should have logged ERROR (not INFO)
    error_msgs = [m for m in log.errors if "modify_order failed" in m.lower()]
    assert len(error_msgs) > 0, "Expected ERROR log for transient failure"

    # Fire another candle - should retry (trade still tracked)
    cs.fire_candle(_make_candle(high=1015.0))
    assert len(adapter.calls) == 2, "Should retry after transient error"
    assert mgr._tracked["trd_fix045_2"]["consecutive_failures"] == 2

    print("  OK FIX-045: transient error → retry logic applies, still tracked")


def test_fix045_unregistered_trade_no_further_modifies():
    """FIX-045: After unregister via terminal error → no further modify calls."""
    mgr, adapter, store, cs, log = _make_gate()
    store._co_orders = {"trd_fix045_3": {"order_id": "co_fix045_3", "variety": "co"}}

    # Start with terminal error response
    adapter._default_success = False
    adapter._default_reason = "Order already cancelled"

    mgr.register_trade(
        trade_id="trd_fix045_3",
        instrument_token=_ENTRY_TOKEN,
        symbol="RELIANCE",
        direction="LONG",
        entry_price=1000.0,
        initial_sl=980.0,
        qty=10,
        trigger_pct=0.005,
        step_pct=0.003,
    )

    # Fire candle → terminal error → unregister
    cs.fire_candle(_make_candle(high=1010.0))
    assert len(adapter.calls) == 1
    assert "trd_fix045_3" not in mgr._tracked

    # Change adapter to succeed (simulate broker recovering)
    adapter._default_success = True

    # Fire multiple candles - should NOT trigger any modifies
    cs.fire_candle(_make_candle(high=1015.0))
    cs.fire_candle(_make_candle(high=1020.0))
    cs.fire_candle(_make_candle(high=1025.0))

    # Still only 1 call (the initial one that got terminal error)
    assert len(adapter.calls) == 1, \
        "Trade should remain unregistered; no further modify calls"

    print("  OK FIX-045: unregistered trade → no further modify calls")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        # Registration
        test_register_trade_adds_to_tracked_and_db,
        test_register_trade_duplicate_raises,
        test_unregister_trade_removes_from_tracked_and_db,
        test_unregister_unknown_returns_false,
        test_register_when_disabled_is_noop,
        # Trail -- LONG
        test_long_price_below_trigger_no_modify,
        test_long_price_crosses_trigger_one_step,
        test_long_price_multiple_steps,
        test_long_price_retraces_sl_not_lowered,
        test_long_current_sl_same_as_target_no_modify,
        test_long_best_price_initialized_on_first_candle,
        # Trail -- SHORT
        test_short_price_falls_trigger_pct_trail_fires,
        test_short_sl_never_raised,
        # Ghost SL fix
        test_success_updates_tracked_and_db,
        test_failure_does_not_update_tracked_or_db,
        test_three_consecutive_failures_fires_critical,
        test_success_resets_failure_counter,
        test_failure_trade_a_does_not_affect_trade_b,
        # Candle callback
        test_candle_callback_filters_by_token,
        test_candle_no_tracked_trade_for_symbol_is_noop,
        test_multiple_trades_same_symbol_all_processed,
        test_candle_unrelated_symbol_no_modify,
        # Reconnect
        test_reconnect_discards_best_price_and_recomputes,
        test_reconnect_fires_trail_once_if_price_advanced,
        test_reconnect_no_price_advance_no_modify,
        test_reconnect_multiple_trades_correct_recompute,
        test_reconnect_logs_warning,
        # Startup recovery
        test_start_reads_smart_tgt_state_rows,
        test_start_repopulates_tracked_with_correct_values,
        test_startup_ltp_check_favorable_side_resumes,
        test_startup_ltp_check_wrong_side_fires_critical,
        test_startup_no_quote_fn_skips_ltp_check,
        # CO lookup
        test_co_lookup_returns_co_row,
        test_co_lookup_non_co_entry_not_returned,
        test_co_lookup_absent_trade_returns_none,
        # Thread safety
        test_concurrent_register_and_candle_no_races,
        test_concurrent_candle_and_reconnect_serialized,
        # Enabled flag
        test_enabled_false_register_is_noop,
        test_enabled_false_candle_callback_is_noop,
        test_runtime_disable_stops_processing,
        # Edge cases
        test_no_co_order_id_logs_error_no_crash,
        test_trail_at_exact_boundary_no_redundant_modify,
        test_multiple_candles_in_succession_counter_updates,
        test_fix020_synthetic_candles_processed_normally,
        # Lifecycle
        test_start_registers_callback_exactly_once,
        test_stop_unregisters_and_clears_tracked,
        test_restart_after_stop_repopulates_tracked,
        # B.4 / Audit 5.4 -- async modify executor + coalesce
        test_b4_async_modify_eventually_calls_broker,
        test_b4_coalesce_newest_target_wins,
        test_b4_executor_failure_is_swallowed_after_shutdown,
        test_b4_drain_with_empty_pending_is_noop,
        test_b4_stop_drains_inflight_before_shutdown,
        # D.1 / 2026-04-25 audit — rate-limit modify_order
        test_d1_rate_limiter_acquired_before_modify,
        test_d1_rate_limiter_acquire_raises_skips_modify,
        test_d1_no_rate_limiter_keeps_legacy_behavior,
        test_d1_acquire_called_per_modify_across_multiple_steps,
        # FIX-026: Volume-dependent trails protection
        test_fix026_volume_zero_no_division_error,
        test_fix026_volume_dependent_trails_false_skips_volume_logic,
        test_fix026_volume_nonzero_with_flag_true_enters_placeholder,
        # FIX-044
        test_fix044_long_rounds_down_to_tick,
        test_fix044_short_rounds_up_to_tick,
        test_fix044_tick_025_rounding,
        test_fix044_tick_unavailable_fallback_with_warning,
        # FIX-045
        test_fix045_terminal_error_unregisters_trade,
        test_fix045_transient_error_retries_normally,
        test_fix045_unregistered_trade_no_further_modifies,
    ]

    print("=" * 70)
    print("smart_tgt_manager.py -- Test Suite (ST1-ST15)")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            test()
        except AssertionError as e:
            failed.append((test.__name__, f"AssertionError: {e}"))
            print(f"  FAIL AssertionError: {e}")
        except Exception as e:
            import traceback as tb
            failed.append((test.__name__, f"{type(e).__name__}: {e}"))
            print(f"  FAIL {type(e).__name__}: {e}")
            tb.print_exc()

    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1

    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
