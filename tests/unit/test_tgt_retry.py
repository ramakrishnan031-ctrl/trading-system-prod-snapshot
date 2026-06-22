"""
tests/unit/test_tgt_retry.py

Task (2026-06-19): standalone TGT retry mechanism.

Covers:
  - state_store flag helpers (mark/clear/bump/candidates/context)
  - LimitTripleProtocol.place_tgt_only (clamp + place + never-raise)
  - OrderPlacer.retry_tgt_for_trade (guards + OCO-safe placement)
  - TGTRetryManager (backoff schedule, due-ness, placed->clear+INFO,
    give-up after max -> WARNING, skip guards, kill switch / market hours)
  - the FIX-190 Bug C -> needs_tgt_retry flag set, then THELEELA-style replay
"""
from __future__ import annotations

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Optional
from unittest.mock import MagicMock, patch

import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.product_resolver import ProductResolver
from broker.zerodha_adapter import PlacedOrder, CancelResult
from core.events import EventBus
from core.exceptions import BrokerError
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.full_entry_engine import FullEntryEngine
from orders.order_manager import OrderManager
from orders.order_placer import OrderPlacer, _LEG_TGT
from orders.order_protocol_co import CoPlusTgtProtocol
from orders.order_protocol_limit import LimitTripleProtocol
from orders.tgt_retry_manager import TGTRetryManager


# ─────────────────────────────────────────────────────────────────────────────
# Minimal mocks (self-contained)
# ─────────────────────────────────────────────────────────────────────────────

def _log() -> logging.Logger:
    return logging.getLogger("test_tgt_retry")


def _resolver() -> ProductResolver:
    return ProductResolver({"zerodha": {"INTRADAY": "MIS", "DELIVERY": "CNC", "COVER_ORDER": "CO"}})


class _MockAdapter:
    """place_order returns a PlacedOrder, or raises if configured. Optional
    circuit limits per symbol for the clamp path."""

    def __init__(self, raises: Optional[Exception] = None,
                 upper: Optional[float] = None, lower: Optional[float] = None) -> None:
        self._raises = raises
        self._upper = upper
        self._lower = lower
        self.placed: List[dict] = []
        self.cancelled: List[str] = []

    def place_order(self, symbol, side, qty, price, order_type, intent,
                    tag=None, trigger_price=0.0, variety="regular"):
        if self._raises is not None:
            raise self._raises
        import uuid
        from core.ids import new_order_id
        from datetime import datetime
        po = PlacedOrder(
            internal_order_id=new_order_id(),
            broker_order_id="PAPER_" + uuid.uuid4().hex[:12].upper(),
            symbol=symbol, side=side, qty=qty, price=price,
            order_type=order_type, product="MIS", status="SUBMITTED",
            ts=datetime.now(),
        )
        self.placed.append({"symbol": symbol, "side": side, "qty": qty,
                            "price": price, "order_type": order_type})
        return po

    def cancel_order(self, broker_order_id: str):
        self.cancelled.append(broker_order_id)
        return CancelResult(broker_order_id=broker_order_id, success=True, reason="")

    def get_quote(self, symbols):
        # _circuit_limits calls get_quote([symbol]) and expects {symbol: quote}.
        if self._upper is None and self._lower is None:
            return {}
        q = type("Q", (), {"upper_circuit": self._upper, "lower_circuit": self._lower})()
        return {s: q for s in symbols}


class _MockFundManager:
    _LEVERAGE_MAP = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}

    def __init__(self) -> None:
        self._leverage_map = dict(self._LEVERAGE_MAP)
        self.released_used: List[dict] = []

    def release_used(self, **kw):
        self.released_used.append(kw)

    def required_margin(self, qty, price, intent):
        return (qty * price) / self._LEVERAGE_MAP.get(intent, 1.0)


class _MockKillSwitch:
    def __init__(self, active: bool = False) -> None:
        self._active = active

    def is_active(self, intent: str = "entry") -> bool:
        return self._active


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / seeding
# ─────────────────────────────────────────────────────────────────────────────

def _make_store(tmp_path: Path) -> StateStore:
    return StateStore(db_path=tmp_path / "test.db", schema_path=Path("core/schema.sql"))


def _make_placer(store, adapter):
    limit_proto = LimitTripleProtocol(adapter=adapter, logger=_log())
    co_proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
    engine = FullEntryEngine(co_protocol=co_proto, limit_protocol=limit_proto,
                             logger=_log(), default_protocol="LIMIT_TRIPLE")
    om = OrderManager(store, _log())
    placer = OrderPlacer(
        entry_engine=engine, order_manager=om, fund_manager=_MockFundManager(),
        bus=EventBus(), logger=_log(), order_monitor=MagicMock(),
        cost_calculator=MagicMock(), rr_ratio=2.0,
        default_order_protocol="LIMIT_TRIPLE", product_resolver=_resolver(),
    )
    return placer


def _seed_open_trade(store, trade_id="t1", symbol="THELEELA", direction="LONG",
                     qty=10, entry=100.0, sl=98.0, tgt=104.0, status="OPEN",
                     with_sl_order=True, with_tgt_order=False):
    """Seed signal + OPEN LIMIT_TRIPLE trade + ENTRY order (+ optional SL/TGT)."""
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,"
            "received_at,expires_at,status,fingerprint,fingerprint_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"sig_{trade_id}", symbol, "SC", "st", now, now, now, "TRADED",
             f"fp_{trade_id}", now[:10]),
        )
        cur.execute(
            "INSERT INTO trades (trade_id,signal_id,symbol,direction,strategy,sector,"
            "qty_planned,qty_filled,entry_target_price,entry_actual_price,sl_initial,"
            "tgt_initial,margin_reserved,risk_amount,created_at,status,order_protocol,"
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, f"sig_{trade_id}", symbol, direction, "st", "EN", qty, qty,
             entry, entry, sl, tgt, 1000.0, 20.0, now, status, "LIMIT_TRIPLE", now),
        )
        cur.execute(
            "INSERT INTO orders (order_id,trade_id,leg,transaction_type,order_type,"
            "product,variety,qty_requested,status,placed_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"e_{trade_id}", trade_id, "ENTRY", "BUY", "LIMIT", "MIS", "regular",
             qty, "COMPLETE", now, now),
        )
        if with_sl_order:
            cur.execute(
                "INSERT INTO orders (order_id,trade_id,leg,transaction_type,order_type,"
                "product,variety,qty_requested,status,trigger_price,placed_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"sl_{trade_id}", trade_id, "SL", "SELL", "SL", "MIS", "regular",
                 qty, "TRIGGER_PENDING", sl, now, now),
            )
        if with_tgt_order:
            cur.execute(
                "INSERT INTO orders (order_id,trade_id,leg,transaction_type,order_type,"
                "product,variety,qty_requested,status,placed_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"tg_{trade_id}", trade_id, "TGT", "SELL", "LIMIT", "MIS", "regular",
                 qty, "OPEN", now, now),
            )


# ─────────────────────────────────────────────────────────────────────────────
# state_store helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_state_store_flag_helpers():
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        _seed_open_trade(store, "t1")
        assert store.mark_needs_tgt_retry("t1") is True
        cands = store.get_tgt_retry_candidates()
        assert [c["trade_id"] for c in cands] == ["t1"]
        ctx = store.get_trade_for_tgt_retry("t1")
        assert ctx["product"] == "MIS" and ctx["status"] == "OPEN"
        assert store.bump_tgt_retry("t1") == 1
        assert store.bump_tgt_retry("t1") == 2
        assert store.clear_needs_tgt_retry("t1") is True
        assert store.get_tgt_retry_candidates() == []
        # count preserved for forensics
        assert store.get_trade_for_tgt_retry("t1")["tgt_retry_count"] == 2
        store.close()
        print("  OK state_store TGT-retry helpers")


# ─────────────────────────────────────────────────────────────────────────────
# place_tgt_only (protocol)
# ─────────────────────────────────────────────────────────────────────────────

def test_place_tgt_only_success_and_clamp():
    # Upper circuit 103 -> a TGT of 104 clamps down into the band.
    adapter = _MockAdapter(upper=103.0, lower=90.0)
    proto = LimitTripleProtocol(adapter=adapter, logger=_log())
    res = proto.place_tgt_only(symbol="X", entry_side="BUY", qty=10,
                               tgt_price=104.0, intent="INTRADAY", trade_id="t1")
    assert res.placed is True
    assert res.clamped is True
    assert res.tgt_price < 104.0  # clamped below the upper band
    assert len(adapter.placed) == 1 and adapter.placed[0]["side"] == "SELL"
    print("  OK place_tgt_only clamps + places")


def test_place_tgt_only_never_raises_on_reject():
    adapter = _MockAdapter(raises=BrokerError("circuit"))
    proto = LimitTripleProtocol(adapter=adapter, logger=_log())
    res = proto.place_tgt_only(symbol="X", entry_side="BUY", qty=10,
                               tgt_price=104.0, intent="INTRADAY", trade_id="t1")
    assert res.placed is False           # rejection -> not placed, no raise
    print("  OK place_tgt_only returns placed=False on broker reject (no raise)")


# ─────────────────────────────────────────────────────────────────────────────
# OrderPlacer.retry_tgt_for_trade (guards + OCO)
# ─────────────────────────────────────────────────────────────────────────────

def test_retry_places_and_registers_for_oco():
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        adapter = _MockAdapter()
        placer = _make_placer(store, adapter)
        _seed_open_trade(store, "t1", entry=100.0, sl=98.0, tgt=104.0)

        assert placer.retry_tgt_for_trade("t1") == "placed"
        # TGT row persisted
        tgt_rows = [o for o in store.get_orders_for_trade("t1") if o["leg"] == "TGT"]
        assert len(tgt_rows) == 1
        # registered for software OCO (fill_map has the TGT leg)
        legs = [fe.leg for fe in placer._fill_map.values()]
        assert _LEG_TGT in legs
        print("  OK retry places TGT + persists + registers for OCO")


def test_retry_skips_when_sl_gone():
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        adapter = _MockAdapter()
        placer = _make_placer(store, adapter)
        _seed_open_trade(store, "t1", with_sl_order=False)  # no standing SL
        assert placer.retry_tgt_for_trade("t1") == "skipped_no_sl"
        assert adapter.placed == []   # never placed a naked TGT
        print("  OK retry refuses to place a naked TGT when SL is gone")


def test_retry_skips_when_closed():
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        placer = _make_placer(store, _MockAdapter())
        _seed_open_trade(store, "t1", status="CLOSED")
        assert placer.retry_tgt_for_trade("t1") == "skipped_closed"
        print("  OK retry skips a closed trade")


def test_retry_skips_when_tgt_already_present():
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        adapter = _MockAdapter()
        placer = _make_placer(store, adapter)
        _seed_open_trade(store, "t1", with_tgt_order=True)  # live TGT already
        assert placer.retry_tgt_for_trade("t1") == "skipped_has_tgt"
        assert adapter.placed == []
        print("  OK retry is idempotent: no second TGT")


def test_retry_failed_on_broker_reject():
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        adapter = _MockAdapter(raises=BrokerError("circuit band"))
        placer = _make_placer(store, adapter)
        _seed_open_trade(store, "t1")
        assert placer.retry_tgt_for_trade("t1") == "failed"
        assert [o for o in store.get_orders_for_trade("t1") if o["leg"] == "TGT"] == []
        print("  OK retry returns 'failed' on broker reject; no TGT row")


def test_retry_unplaceable_when_clamp_makes_tgt_below_entry():
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        # Upper circuit barely above entry -> the clamp would pull the TGT to/under
        # entry. NOCIL fix: the placeability gate (inside place_tgt_only) refuses it
        # BEFORE any order is sent, so the retry reports unplaceable (keep retrying)
        # with NO broker round-trip (no place-then-cancel — the old Bug-D re-check
        # mechanism the de-dup removed).
        adapter = _MockAdapter(upper=100.5, lower=80.0)
        placer = _make_placer(store, adapter)
        _seed_open_trade(store, "t1", direction="LONG", entry=100.0, sl=98.0, tgt=104.0)
        assert placer.retry_tgt_for_trade("t1") == "skipped_unplaceable"
        # The wrong-side TGT is never placed -> nothing to cancel.
        assert len(adapter.placed) == 0
        assert len(adapter.cancelled) == 0
        assert [o for o in store.get_orders_for_trade("t1") if o["leg"] == "TGT"] == []
        print("  OK retry refuses a clamp-unplaceable TGT pre-broker (keeps retrying)")


# ─────────────────────────────────────────────────────────────────────────────
# TGTRetryManager
# ─────────────────────────────────────────────────────────────────────────────

def _manager(store, placer, **kw):
    kw.setdefault("market_hours_guard", False)  # tests don't depend on wall clock
    return TGTRetryManager(state_store=store, order_placer=placer,
                           notifier=kw.pop("notifier", None),
                           kill_switch=kw.pop("kill_switch", None),
                           logger=_log(), **kw)


def test_manager_backoff_schedule():
    m = TGTRetryManager(state_store=None, order_placer=None, backoff_base_sec=30)
    assert [m._backoff_for(n) for n in range(5)] == [30, 60, 120, 240, 480]
    print("  OK backoff schedule 30/60/120/240/480")


def test_manager_is_due():
    from datetime import timedelta
    m = TGTRetryManager(state_store=None, order_placer=None, backoff_base_sec=30)
    now = now_ist()
    # count=0 -> 30s window
    assert m._is_due(0, (now - timedelta(seconds=31)).isoformat(), now) is True
    assert m._is_due(0, (now - timedelta(seconds=29)).isoformat(), now) is False
    # count=2 -> 120s window
    assert m._is_due(2, (now - timedelta(seconds=119)).isoformat(), now) is False
    assert m._is_due(2, (now - timedelta(seconds=121)).isoformat(), now) is True
    # missing timestamp -> due
    assert m._is_due(0, None, now) is True
    print("  OK is_due respects per-count backoff window")


def test_manager_placed_clears_flag_and_notifies():
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        _seed_open_trade(store, "t1")
        store.mark_needs_tgt_retry("t1")
        # backdate so it is due immediately
        with store.transaction() as cur:
            cur.execute("UPDATE trades SET tgt_last_retry_at=? WHERE trade_id='t1'",
                        ("2026-06-19T09:00:00+05:30",))
        placer = MagicMock()
        placer.retry_tgt_for_trade.return_value = "placed"
        notifier = MagicMock()
        m = _manager(store, placer, notifier=notifier)
        out = m.run_once()
        assert out == ["placed"]
        placer.retry_tgt_for_trade.assert_called_once_with("t1")
        assert store.get_tgt_retry_candidates() == []   # flag cleared
        assert any(c.kwargs.get("severity") == "INFO" for c in notifier.send.call_args_list)
        store.close()
        print("  OK manager: placed -> clear flag + INFO")


def _backdate(store, trade_id="t1"):
    """Force the candidate to be 'due' by backdating its last attempt."""
    with store.transaction() as cur:
        cur.execute("UPDATE trades SET tgt_last_retry_at=? WHERE trade_id=?",
                    ("2026-06-19T09:00:00+05:30", trade_id))


def test_manager_gives_up_after_max_attempts():
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        _seed_open_trade(store, "t1")
        store.mark_needs_tgt_retry("t1")
        placer = MagicMock()
        placer.retry_tgt_for_trade.return_value = "failed"
        notifier = MagicMock()
        m = _manager(store, placer, notifier=notifier, max_attempts=5)
        # Each cycle backdates so it is due; bump() then re-stamps last_retry_at.
        for _ in range(5):
            _backdate(store)
            m.run_once()
        assert store.get_tgt_retry_candidates() == []    # gave up -> flag cleared
        assert store.get_trade_for_tgt_retry("t1")["tgt_retry_count"] == 5
        assert any(c.kwargs.get("severity") == "WARNING" for c in notifier.send.call_args_list)
        print("  OK manager: gives up after 5 -> WARNING, SL-protected")


def test_manager_skip_no_sl_clears_flag():
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        _seed_open_trade(store, "t1")
        store.mark_needs_tgt_retry("t1")
        _backdate(store)
        placer = MagicMock()
        placer.retry_tgt_for_trade.return_value = "skipped_no_sl"
        m = _manager(store, placer)
        m.run_once()
        assert store.get_tgt_retry_candidates() == []   # cleared (no longer applicable)
        print("  OK manager: skipped_no_sl -> clears flag")


def test_manager_skips_when_kill_switch_active():
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        _seed_open_trade(store, "t1")
        store.mark_needs_tgt_retry("t1")
        placer = MagicMock()
        m = _manager(store, placer, kill_switch=_MockKillSwitch(active=True),
                     backoff_base_sec=0)
        assert m.run_once() == []
        placer.retry_tgt_for_trade.assert_not_called()  # no TGT while flatten in progress
        print("  OK manager: skips while kill switch active")


def test_manager_skips_outside_market_hours():
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        _seed_open_trade(store, "t1")
        store.mark_needs_tgt_retry("t1")
        placer = MagicMock()
        m = TGTRetryManager(state_store=store, order_placer=placer, logger=_log(),
                            backoff_base_sec=0, market_hours_guard=True)
        with patch("orders.tgt_retry_manager.is_within_market_hours", return_value=False):
            assert m.run_once() == []
        placer.retry_tgt_for_trade.assert_not_called()
        print("  OK manager: skips outside market hours")


def test_manager_not_due_does_not_retry():
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        _seed_open_trade(store, "t1")
        store.mark_needs_tgt_retry("t1")  # last_retry_at = now -> not due (30s window)
        placer = MagicMock()
        m = _manager(store, placer)   # backoff_base default 30
        assert m.run_once() == []
        placer.retry_tgt_for_trade.assert_not_called()
        store.close()
        print("  OK manager: respects backoff (not due -> no retry)")


# ─────────────────────────────────────────────────────────────────────────────
# THELEELA-style replay: Bug C flags -> manager places on retry
# ─────────────────────────────────────────────────────────────────────────────

def test_theleela_replay_bugc_flag_then_retry_succeeds():
    """End-to-end: a trade flagged by Bug C (SL live, TGT unplaced) is picked up
    by the manager and the TGT is placed on retry (circuit relaxed)."""
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        adapter = _MockAdapter()  # circuit now relaxed -> placement succeeds
        placer = _make_placer(store, adapter)
        _seed_open_trade(store, "t1", symbol="THELEELA", entry=100.0, sl=98.0, tgt=104.0)
        # Bug C left it SL-only -> flag it (as _persist_sl_only_protected does)
        store.mark_needs_tgt_retry("t1")
        with store.transaction() as cur:
            cur.execute("UPDATE trades SET tgt_last_retry_at=? WHERE trade_id='t1'",
                        ("2026-06-19T09:00:00+05:30",))  # due
        m = _manager(store, placer, notifier=MagicMock())
        out = m.run_once()
        assert out == ["placed"]
        assert [o for o in store.get_orders_for_trade("t1") if o["leg"] == "TGT"]
        assert store.get_tgt_retry_candidates() == []
        store.close()
        print("  OK THELEELA replay: Bug C flag -> manager retries -> TGT placed")
