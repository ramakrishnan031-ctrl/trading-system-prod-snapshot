"""A-3 (audit 02-Jul): entry-path kill-switch TOCTOU.

`order_placer.place()` checked `is_active("entry")` at OP-LM1 (~:944), then did
pre-submit network I/O (`_fetch_ltp` / drift re-quote / `_check_liquidity`), then
submitted the ENTRY via `engine.execute()` (~:1215). A SOFT_KILL activated by another
thread in that window was NOT re-checked, so one entry could leak past a just-activated
kill (`soft_kill` has no order-cancel backstop). The fix adds a second `is_active`
re-check at the TOP of the 429-retry loop, immediately before each `engine.execute()`.

Real collaborators: real `OrderPlacer.place()`, real `KillSwitch` (persist-first to a
real `StateStore`), real `OrderManager`. SIMULATED: the pre-submit network step
(`adapter.get_quote_raw`, used by `_fetch_ltp`/`_check_liquidity`) activates SOFT_KILL —
standing in for another thread tripping the kill during the I/O window; and a recording
stub engine so the submit itself is observable ("engine.execute NOT called").
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

import pytest

from broker.cost_calculator import CostCalculator
from broker.order_monitor import OrderMonitor
from core.events import EventBus
from core.exceptions import BrokerRateLimit429Error, OrderRejectedError
from capital.kill_switch import KillSwitch
from orders.full_entry_engine import FullEntryEngine
from orders.order_manager import OrderManager
from orders.order_placer import OrderPlacer
from orders.order_protocol_co import CoPlusTgtProtocol
from orders.order_protocol_limit import LimitTripleProtocol

from tests.unit.test_order_placer import (
    _MockAdapter,
    _MockFundManager,
    _default_resolver,
    _log,
    _make_store,
    _seed_signal,
)


# ── Controlled collaborators ─────────────────────────────────────────────────


class _RecordingEngine:
    """Observable entry engine. Records execute() calls. Optionally raises a 429 on
    the FIRST call (flipping the kill) to exercise the retry loop for T2."""

    def __init__(self, kill_switch=None, raise_429_first: bool = False) -> None:
        self.calls = 0
        self._ks = kill_switch
        self._raise_429_first = raise_429_first

    def execute(self, **kwargs):
        self.calls += 1
        if self._raise_429_first and self.calls == 1:
            if self._ks is not None:
                self._ks.soft_kill("kill_during_429_backoff", triggered_by="test")
            raise BrokerRateLimit429Error("simulated 429")
        return MagicMock()  # unreached in the kill tests


class _KillFlipAdapter(_MockAdapter):
    """Its pre-submit network step (get_quote_raw, via _fetch_ltp/_check_liquidity)
    activates SOFT_KILL — simulating another thread tripping the kill mid-place().
    Returns {} so the slippage/liquidity guards fail open (no unrelated reject)."""

    def __init__(self, kill_switch) -> None:
        super().__init__(quote_ltp=None)  # {} -> guards fail open
        self._ks = kill_switch
        self.quote_calls = 0

    def get_quote_raw(self, instruments):
        self.quote_calls += 1
        self._ks.soft_kill("concurrent_kill_during_place", triggered_by="test")  # idempotent
        return {}


def _build(tmp_path: Path, *, engine=None, adapter=None, mode: str = "LIVE"):
    """Wire a real OrderPlacer + real KillSwitch + real StateStore/OrderManager."""
    store = _make_store(tmp_path)
    bus = EventBus()
    ks = KillSwitch(state_store=store, bus=bus, logger=_log())
    if adapter is None:
        adapter = _MockAdapter(quote_ltp=100.0)
    if engine is None:
        engine = FullEntryEngine(
            co_protocol=CoPlusTgtProtocol(adapter=adapter, logger=_log()),
            limit_protocol=LimitTripleProtocol(adapter=adapter, logger=_log()),
            logger=_log(), default_protocol="LIMIT_TRIPLE",
        )
    om = OrderManager(store, _log())
    fm = _MockFundManager()
    placer = OrderPlacer(
        entry_engine=engine,
        order_manager=om,
        fund_manager=fm,
        bus=bus,
        logger=_log(),
        kill_switch=ks,                                   # A-3: wire the real kill switch
        broker_adapter=adapter,                           # for _fetch_ltp / _check_liquidity
        order_monitor=MagicMock(spec=OrderMonitor),
        cost_calculator=MagicMock(spec=CostCalculator),
        rr_ratio=2.0,
        default_order_protocol="LIMIT_TRIPLE",
        product_resolver=_default_resolver(),
        liquidity_check_enabled=True,                     # ensure a pre-loop get_quote_raw
        mode=mode,
    )
    return placer, store, fm, ks, adapter, engine


def _trade_status(store, sig_id):
    rows = store.fetch_all("SELECT status FROM trades WHERE signal_id = ?", (sig_id,))
    return rows[0]["status"] if rows else None


# ── T1 — the re-check fires (mid-place SOFT_KILL → entry rejected) ────────────


def test_t1_midplace_softkill_rejects_before_submit():
    """SOFT_KILL activated during the pre-submit network I/O -> the entry is rejected
    at the last-mile re-check; engine.execute is never called (no leaked entry)."""
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        engine = _RecordingEngine()
        placer, store, fm, ks, adapter, _ = _build(Path(tmp), engine=engine)
        adapter = _KillFlipAdapter(ks)
        placer._adapter = adapter  # the network step that flips the kill
        sig_id = _seed_signal(store)
        assert not ks.is_active("entry")  # starts clean

        with pytest.raises(OrderRejectedError) as ei:
            placer.place(
                symbol="RELIANCE", side="BUY", qty=10,
                entry_price=2500.0, sl_price=2450.0,
                intent="INTRADAY", signal_id=sig_id, reservation_id="res_t1",
            )

        assert "kill_switch_active_last_mile_presubmit" in str(ei.value)
        assert adapter.quote_calls >= 1                      # the kill flipped mid-place
        assert ks.is_active("entry")                          # SOFT_KILL is now active
        assert engine.calls == 0                              # THE ENTRY WAS NOT SUBMITTED
        assert adapter.placed == []                           # nothing at the broker
        assert _trade_status(store, sig_id) == "FAILED"       # trade rolled back
        assert "res_t1" in fm.released                        # reservation released
        store.close()


# ── T2 — the re-check is INSIDE the loop (kill during a 429 backoff) ──────────


def test_t2_kill_during_429_backoff_rejects_on_retry():
    """attempt 0 hits a 429 (and the kill flips); the retry (attempt 1) is rejected by
    the in-loop re-check -> proves the check is inside the loop, not just before it."""
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        engine = _RecordingEngine(raise_429_first=True)   # set _ks after build
        placer, store, fm, ks, adapter, _ = _build(Path(tmp), engine=engine)
        engine._ks = ks
        sig_id = _seed_signal(store)

        with pytest.raises(OrderRejectedError) as ei:
            placer.place(
                symbol="RELIANCE", side="BUY", qty=10,
                entry_price=2500.0, sl_price=2450.0,
                intent="INTRADAY", signal_id=sig_id, reservation_id="res_t2",
            )

        assert "kill_switch_active_last_mile_presubmit" in str(ei.value)
        assert engine.calls == 1          # attempt 0 (429); attempt 1 rejected BEFORE execute
        assert _trade_status(store, sig_id) == "FAILED"
        assert "res_t2" in fm.released
        store.close()


# ── T3 — happy path unchanged (kill INACTIVE -> entry places normally) ────────


def test_t3_happy_path_places_normally_when_kill_inactive():
    """With the kill INACTIVE the re-check is a no-op; the real engine submits the
    ENTRY exactly as before (no over-block, no added behaviour on the clean path)."""
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        adapter = _MockAdapter(quote_ltp=2500.0)          # valid quote, no kill flip
        placer, store, fm, ks, adapter2, engine = _build(Path(tmp), adapter=adapter)
        sig_id = _seed_signal(store)
        assert not ks.is_active("entry")

        placer.place(
            symbol="RELIANCE", side="BUY", qty=10,
            entry_price=2500.0, sl_price=2450.0,
            intent="INTRADAY", signal_id=sig_id, reservation_id="res_t3",
        )

        assert len(adapter.placed) == 1                   # ENTRY submitted (2.1: entry only)
        assert adapter.placed[0]["side"] == "BUY"
        assert _trade_status(store, sig_id) == "PENDING"  # not FAILED
        assert "res_t3" not in fm.released                # reservation kept
        store.close()


# ── T4 — parity (both modes; is_active is mode-agnostic) ─────────────────────


@pytest.mark.parametrize("mode", ["LIVE", "PAPER"])
def test_t4_parity_both_modes_reject(mode):
    """The re-check is a single shared, mode-agnostic path — a mid-place SOFT_KILL is
    rejected identically in LIVE and PAPER."""
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        engine = _RecordingEngine()
        placer, store, fm, ks, adapter, _ = _build(Path(tmp), engine=engine, mode=mode)
        placer._adapter = _KillFlipAdapter(ks)
        # PAPER skips the LIVE-only liquidity check, so drive the flip via the slippage
        # LTP fetch too: force a fresh get_quote_raw by leaving release_ltp unset.
        sig_id = _seed_signal(store)

        with pytest.raises(OrderRejectedError) as ei:
            placer.place(
                symbol="RELIANCE", side="BUY", qty=10,
                entry_price=2500.0, sl_price=2450.0,
                intent="INTRADAY", signal_id=sig_id, reservation_id="res_t4",
            )

        assert "kill_switch_active_last_mile_presubmit" in str(ei.value)
        assert engine.calls == 0
        assert _trade_status(store, sig_id) == "FAILED"
        store.close()
