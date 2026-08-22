"""
tests/unit/test_phase3_delivery_caps_conditional_capital.py — SLICE2.5-PHASE-3.

Two features, ONE of which is now LIVE:
  A) SEPARATE delivery (CNC) count caps — risk_engine OPEN_POSITIONS / DAILY_TRADES
     branch on sizing_result.bucket=="positional", product-keyed counts.
     ⛔ NI-3 (22-Aug-2026): this header used to describe (A) as dormant because
     delivery_enabled was false. IT IS TRUE NOW (force_intraday_only false,
     trade_type BOTH, delivery has traded), so (A) is ENFORCED on live entries.
     The tests below are unchanged — only this description was wrong.
  B) CONDITIONAL capital allocation — resolve_bucket_allocation() drives the
     effective intraday/positional split; FundManager itself is unchanged.
     Still default-OFF (conditional_allocation_enabled: false).

Parity: risk_engine / fund_manager / state_store are shared, so these apply
identically in paper + live. Coverage: dormancy (flag off), inertness (coerced ->
intraday bucket), MIS regression (intraday branch untouched), the flag allocation
cases, the delivery caps, and the no-borrow reject path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from capital.fund_manager import resolve_bucket_allocation as _alloc
from capital.risk_engine import RiskEngine
from core.state_store import StateStore
from core.time_authority import now_ist
from tests.unit.test_fund_manager import _make_fm
from tests.unit.test_order_reconciler import _insert_order
from tests.unit.test_risk_engine import (
    _CapturingHandler,
    _MockFundManager,
    _insert_trade,
    _make_sizing,
    _make_snap,
)

_TODAY = now_ist().date().isoformat()


# ── builders ────────────────────────────────────────────────────────────────────
def _engine(store, fm, *, max_open=100, max_daily=100,
            max_open_delivery=3, max_daily_delivery=5) -> RiskEngine:
    import logging
    log = logging.getLogger("test_phase3")
    log.handlers.clear()
    log.addHandler(_CapturingHandler())
    return RiskEngine(
        fund_manager=fm, state_store=store, max_open_positions=max_open,
        max_daily_trades=max_daily, max_sector_exposure_pct=0.40,
        max_consecutive_losses=100, daily_loss_limit_pct=0.99,
        sector_lookup_fn=lambda _s: "IT", logger=log, kill_switch=None,
        max_open_delivery_positions=max_open_delivery,
        max_daily_delivery_trades=max_daily_delivery,
        # 22-Aug-2026 (fix item 1): these tests drive DELIVERY (positional) entries
        # through the gate, so the delivery limits must be supplied — the engine no
        # longer borrows the global ones. Set equal to the globals above so every
        # assertion in this file keeps its exact arithmetic.
        delivery_max_sector_exposure_pct=0.40,
        delivery_daily_loss_limit_pct=0.99,
    )


def _seed_delivery(store, trade_id, *, symbol, status="OPEN", created_date=None):
    """An OPEN delivery trade = trade row + an ENTRY order with product='CNC'."""
    _insert_trade(store, trade_id, symbol=symbol, sector="IT", status=status,
                  created_date=created_date or _TODAY)
    _insert_order(store, f"o_{trade_id}", trade_id, leg="ENTRY", product="CNC",
                  status="COMPLETE")


def _seed_intraday(store, trade_id, *, symbol, status="OPEN", created_date=None):
    _insert_trade(store, trade_id, symbol=symbol, sector="IT", status=status,
                  created_date=created_date or _TODAY)
    _insert_order(store, f"o_{trade_id}", trade_id, leg="ENTRY", product="MIS",
                  status="COMPLETE")


# ════════════════════════════════════════════════════════════════════════════════
# FEATURE B — conditional allocation (pure resolve_bucket_allocation)
# ════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("ce,da,ia,expected", [
    (False, True, True, (0.70, 0.30)),    # flag OFF -> fixed split (★ dormancy)
    (False, False, True, (0.70, 0.30)),   # flag OFF -> fixed regardless of flags
    (False, True, False, (0.70, 0.30)),
    (True, False, True, (1.0, 0.0)),      # only-intraday -> 100/0
    (True, True, False, (0.0, 1.0)),      # only-delivery -> 0/100
    (True, True, True, (0.70, 0.30)),     # both active  -> config split
    (True, False, False, (1.0, 0.0)),     # neither       -> 100/0 (safe idle)
])
def test_allocation_cases(ce, da, ia, expected):
    out = _alloc(conditional_enabled=ce, delivery_active=da, intraday_active=ia,
                 intraday_pct=0.70, positional_pct=0.30)
    assert out == expected
    assert abs(out[0] + out[1] - 1.0) < 1e-9   # FM12: always sums to 1.0


def test_allocation_dormancy_flag_off_ignores_active_types():
    # ★ DORMANCY PROOF: flag off -> the fixed config split no matter the active types.
    for da in (True, False):
        for ia in (True, False):
            assert _alloc(conditional_enabled=False, delivery_active=da,
                          intraday_active=ia, intraday_pct=0.70,
                          positional_pct=0.30) == (0.70, 0.30)


# ════════════════════════════════════════════════════════════════════════════════
# FEATURE A — delivery count methods (product-keyed, CNC ENTRY order)
# ════════════════════════════════════════════════════════════════════════════════
def test_count_open_delivery_only_cnc_and_open_statuses(tmp_path: Path):
    store = StateStore(tmp_path / "t.db")
    _seed_delivery(store, "d1", symbol="AAA", status="OPEN")
    _seed_delivery(store, "d2", symbol="BBB", status="PARTIAL")
    _seed_delivery(store, "d3", symbol="CCC", status="PENDING_FILL")
    _seed_delivery(store, "d4", symbol="DDD", status="CLOSED")        # not open -> excluded
    _seed_intraday(store, "i1", symbol="EEE", status="OPEN")          # MIS -> excluded
    assert store.count_open_delivery_positions() == 3
    store.close()


def test_count_daily_delivery_only_today_cnc(tmp_path: Path):
    store = StateStore(tmp_path / "t.db")
    _seed_delivery(store, "d1", symbol="AAA", status="OPEN", created_date=_TODAY)
    _seed_delivery(store, "d2", symbol="BBB", status="CLOSED", created_date=_TODAY)
    _seed_delivery(store, "d3", symbol="CCC", status="OPEN", created_date="2020-01-01")  # not today
    _seed_intraday(store, "i1", symbol="DDD", status="OPEN", created_date=_TODAY)        # MIS
    assert store.count_daily_delivery_trades(_TODAY) == 2
    store.close()


# ════════════════════════════════════════════════════════════════════════════════
# FEATURE A — risk_engine enforcement (bucket=="positional")
# ════════════════════════════════════════════════════════════════════════════════
def test_open_delivery_cap_rejects_4th(tmp_path: Path):
    store = StateStore(tmp_path / "t.db")
    fm = _MockFundManager(_make_snap())                     # positional_avail 300k
    eng = _engine(store, fm, max_open_delivery=3, max_daily_delivery=100)
    for i in range(3):
        _seed_delivery(store, f"d{i}", symbol=f"SYM{i}", status="OPEN")
    res = eng.approve("INFY", "BUY", "DELIVERY",
                      _make_sizing(bucket="positional"), "sig")
    assert not res.approved and res.failed_check == "OPEN_POSITIONS"
    assert "Delivery position cap" in res.reason
    store.close()


def test_daily_delivery_cap_rejects_6th(tmp_path: Path):
    store = StateStore(tmp_path / "t.db")
    fm = _MockFundManager(_make_snap())
    eng = _engine(store, fm, max_open_delivery=100, max_daily_delivery=5)
    for i in range(5):
        _seed_delivery(store, f"d{i}", symbol=f"SYM{i}", status="OPEN",
                       created_date=_TODAY)
    res = eng.approve("INFY", "BUY", "DELIVERY",
                      _make_sizing(bucket="positional"), "sig")
    assert not res.approved and res.failed_check == "DAILY_TRADES"
    assert "Delivery daily trade limit" in res.reason
    store.close()


def test_delivery_under_cap_approves(tmp_path: Path):
    store = StateStore(tmp_path / "t.db")
    fm = _MockFundManager(_make_snap())
    eng = _engine(store, fm, max_open_delivery=3, max_daily_delivery=5)
    _seed_delivery(store, "d0", symbol="SYM0", status="OPEN")   # 1 open, under both caps
    res = eng.approve("INFY", "BUY", "DELIVERY",
                      _make_sizing(bucket="positional"), "sig")
    assert res.approved, res.reason
    store.close()


# ════════════════════════════════════════════════════════════════════════════════
# ★ INERTNESS + ★ MIS REGRESSION
# ════════════════════════════════════════════════════════════════════════════════
def test_inertness_intraday_bucket_ignores_delivery_cap(tmp_path: Path):
    """★ Coerced delivery -> intent=INTRADAY -> bucket=intraday -> the delivery cap
    is NEVER consulted (the intraday/global branch runs instead). 5 open delivery
    positions (> max_open_delivery=3) do NOT reject an intraday-bucket entry."""
    store = StateStore(tmp_path / "t.db")
    fm = _MockFundManager(_make_snap())
    eng = _engine(store, fm, max_open=100, max_open_delivery=3, max_daily_delivery=5)
    for i in range(5):
        _seed_delivery(store, f"d{i}", symbol=f"SYM{i}", status="OPEN")
    res = eng.approve("INFY", "BUY", "INTRADAY",
                      _make_sizing(bucket="intraday"), "sig")
    assert res.approved, res.reason   # delivery cap irrelevant; global max_open=100 not hit
    store.close()


def test_inertness_count_open_delivery_zero_when_no_cnc(tmp_path: Path):
    """★ With only MIS (intraday) entry orders — the coerced state — the delivery
    count method returns 0, so the caps are provably inert."""
    store = StateStore(tmp_path / "t.db")
    for i in range(4):
        _seed_intraday(store, f"i{i}", symbol=f"SYM{i}", status="OPEN")
    assert store.count_open_delivery_positions() == 0
    assert store.count_daily_delivery_trades(_TODAY) == 0
    store.close()


def test_mis_regression_global_cap_unchanged(tmp_path: Path):
    """★ MIS REGRESSION: an intraday entry hits the EXISTING global OPEN_POSITIONS
    cap with the EXISTING message ('Position cap reached', not 'Delivery')."""
    store = StateStore(tmp_path / "t.db")
    fm = _MockFundManager(_make_snap())
    eng = _engine(store, fm, max_open=2, max_open_delivery=100,
                  max_daily=100, max_daily_delivery=100)
    _seed_intraday(store, "i0", symbol="SYM0", status="OPEN")
    _seed_intraday(store, "i1", symbol="SYM1", status="OPEN")   # 2 active == max_open
    res = eng.approve("INFY", "BUY", "INTRADAY",
                      _make_sizing(bucket="intraday"), "sig")
    assert not res.approved and res.failed_check == "OPEN_POSITIONS"
    assert "Position cap reached" in res.reason      # the GLOBAL message
    assert "Delivery" not in res.reason              # NOT the delivery branch
    store.close()


# ════════════════════════════════════════════════════════════════════════════════
# REJECT PATH — no cross-bucket borrow when delivery bucket is 0%
# ════════════════════════════════════════════════════════════════════════════════
def test_reject_path_zero_positional_no_borrow(tmp_path: Path):
    """Flag TRUE + delivery inactive -> resolve gives (1.0, 0.0). A FundManager with
    0% positional REJECTS a DELIVERY reserve ('Insufficient positional capital') while
    the intraday bucket (100%) is untouched — no cross-bucket borrow (FM3)."""
    ipct, ppct = _alloc(conditional_enabled=True, delivery_active=False,
                        intraday_active=True, intraday_pct=0.70, positional_pct=0.30)
    assert (ipct, ppct) == (1.0, 0.0)

    store = StateStore(tmp_path / "t.db")
    fm = _make_fm(store, intraday_pct=ipct, positional_pct=ppct)
    fm.initialize(100_000.0)

    deliv = fm.reserve("INFY", 10, 2500.0, "DELIVERY", "sig-d")
    assert not deliv.success
    assert "positional" in deliv.reason_if_failed.lower()        # rejected, no borrow

    intra = fm.reserve("TCS", 1, 2500.0, "INTRADAY", "sig-i")
    assert intra.success                                          # intraday bucket fine
    store.close()
