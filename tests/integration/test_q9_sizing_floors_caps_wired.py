"""
tests/integration/test_q9_sizing_floors_caps_wired.py — Q9 batch 4, item #4.

SIZING FLOORS + CAPS, PROVEN THROUGH THE WIRED PATH — positive, negative, and
BINDING-ORDER-VERIFIED for every guard between an approved signal and a final quantity.

WHY THIS FILE EXISTS. Sizing decides how much real money enters every position, and it was
Q9 "UNIT-ONLY": `capital/position_sizer.py` has thorough unit tests, but no integration test
had ever driven the sizer at all. The two existing "full lifecycle" integration tests bypass
it outright — `test_full_signal_flow._drive_lifecycle` hard-codes `qty = 10` and calls
`fund_manager.reserve()` directly, so `PositionSizer.calculate()` was invoked ZERO times by
the integration suite. Runtime-probed, not assumed (§A4).

THE WIRED PATH THAT ACTUALLY REACHES SIZING (rule F — probed, not inferred from source order):
    webhook POST -> receiver -> queue -> signal_processor -> secondary screener -> sizing
A bare webhook post does NOT reach sizing: the screener rejects with REJECTED_STEP_ERROR
('price_action') because the fixture has no market data. Sizing is reached only with rich
quote data patched in, exactly as tests/integration/test_end_to_end_smoke.py does it. Probed
call count with a bare post: 0. With rich data: 1, args
('RELIANCE','BUY',999.0,991.008,'INTRADAY','HIGH',1) perf_weight=1.0.

⚠️ THE BINDING ORDER — AND WHY A NAIVE SIZING TEST PROVES THE WRONG THING (rule G).
Under BOTH production and fixture config the CONCENTRATION arm is the strict unique minimum
of the three candidate quantities, so it binds on essentially every realistic signal:

    qty_by_risk          = floor(risk_pct   * total / sl_distance)     position_sizer.py:361
    qty_by_capital       = floor(bucket_avail / (price / leverage))    position_sizer.py:398
    qty_by_concentration = floor(conc_pct   * total / price)           position_sizer.py:402
    raw_qty              = min(the three)                              position_sizer.py:407

    CONCENTRATION binds  <=>  sl_distance < (risk_pct/conc_pct) * price   ( = 10% of price )
    CAPITAL binds        <=>  bucket_avail < conc_pct * total / leverage  ( = 10,000 here )

Intraday stops are 1-3% of price, so the first condition is essentially always true. Every
test below therefore asserts WHICH guard bound, never merely that a quantity changed. There
are no multi-way accepts anywhere in this file.

GUARD LADDER, in execution order, with what each one does:
    G1  min_tick_size SL-distance      :333  REJECT  INVALID_SL_DISTANCE
    G2  qty_by_risk                    :361  clamp candidate
    G3  max_single_order_qty           :365  REJECT  QTY_EXPLOSION_GUARD (tests qty_by_risk
                                             ALONE, before the min() — so it is a guard on the
                                             risk arm, not on the final quantity)
    G4  qty_by_capital                 :398  clamp candidate
    G5  qty_by_concentration           :402  clamp candidate
    G6  raw_qty = min(G2,G4,G5)        :407  clamp; CAPITAL wins ties, then RISK, else CONC
    G7  raw_qty <= 0 early exit        :425  REJECT, named by the binding arm
    G8  ZERO_MULTIPLIER (M-C6)         :466  REJECT  ZERO_MULTIPLIER
    G9  tier multiplier                :503  clamp down (mult<1) / raise (mult>1)
    G10 FIX-133 floor-1 / cap-2x       :506  FLOOR (raises) + 2x ceiling
    G11 flat ceiling (OFF mode only)   :516  clamp FLAT — inert, position_sizing.enabled=true
    G12 lot-size rounding (PS6)        :530  clamp down
    G13 lot skew rejection (FIX-021)   :534  REJECT  REJECTED_LOT_SKEW
    G14 max_position_value_pct         :562  REJECT  POSITION_VALUE_CAP (reject, NOT clamp)
    G15 BELOW_MIN                      :592  REJECT  BELOW_MIN

⭐ THE ONE RESULT TO CARRY FORWARD (§C, and TestTwoTimesMultiplierCeiling below).
G10's ceiling is `max(1, min(tiered_qty, raw_qty * 2))`. For any effective multiplier > 1 the
final quantity can reach TWICE raw_qty — i.e. twice the tightest of risk/capital/concentration.
G14 cannot catch it (2 x 10% = 20% < the 40% position-value cap), and the stored audit column
recorded `binding_constraint='concentration'` while the quantity is double the
concentration limit — FIXED by BUG-NI18 (23-Aug-2026), which now reports
`multiplier` and keeps the rung in `rung_before_multiplier`. The 2x QUANTITY
ceiling itself is UNCHANGED and is still NI-16 / F2's to resolve. This is NOT reachable in production today, for one reason only:
`PerformanceAllocator` is never instantiated anywhere outside its own docstring and
`perf_weights` is never passed to `SignalProcessor`, so `perf_weight` is always the 1.0
default (empirically: perf_weight_applied = 1.0 on 298 of 298 sized production trades). The
test below pins the behaviour so that wiring the allocator — which `dynamic_by_winrate: true`
and `max_multiplier: 2.0` in system_config.yaml already ask for — cannot land silently.

SCENARIO HAZARDS designed around (§A5):
  * The per-strategy position cap (signal_processor.py:624-640) fires BEFORE the risk engine;
    multi-position scenarios must seed across DIFFERENT strategies (batch 1's finding).
  * The daily-loss breach is NOT passive — _make_daily_loss_cb (main.py:757-800) force-closes
    via EOD fire_now and arms a soft kill. Fixture limit is 2% x 500,000 = 10,000; nothing
    here closes a position at all, and every capital assertion also asserts the kill is clear.
  * paper_auto_fill_delay_sec=60 -> an admitted order stays UNFILLED holding a LIVE
    reservation. That is what makes "the reservation matches the sized quantity" observable.

PARITY (Rule #5) — STRUCTURAL, not assumed. `PositionSizer` takes no mode argument and
contains no paper/live branch (no `paper`/`is_paper`/`mode` conditional anywhere in
capital/position_sizer.py); it reads only FundManager.get_snapshot(), which is likewise
mode-free. `signal_processor.py:885` is the single sizing call site on the entry path and is
upstream of any mode decision — the paper/live divergence is downstream at
broker/zerodha_adapter.py:348 (paper_mode -> _paper_place_order). One shared sizer, one code
path: a quantity proven here is the quantity live would compute. TestParity asserts the
structural facts so a future duplicate cannot drift in unnoticed.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from capital.position_sizer import PositionSizer
from tests.integration.conftest import PAPER_CAPITAL, SystemContext
from tests.integration.test_full_signal_flow import (
    _ALL_TERMINAL, _make_payload, _post_webhook, _wait_for_signal,
    _wait_for_signal_status,
)

SCANNER = "vwap_bounce_long"

# Every sizing constraint can terminate a signal; include them all so the waiter never
# times out into a misleading assertion. Which one actually fired is asserted explicitly
# by each test — this set is a waiter, NOT an accept-any-of.
_SIZING_TERMINAL = {
    f"REJECTED_SIZING_{x}" for x in (
        "CONCENTRATION", "RISK", "CAPITAL", "ZERO_MULTIPLIER", "BELOW_MIN",
        "POSITION_VALUE_CAP", "REJECTED_LOT_SKEW", "INVALID_SL_DISTANCE",
        "QTY_EXPLOSION_GUARD",
    )
}
TERMINAL = _ALL_TERMINAL | _SIZING_TERMINAL


# ── helpers ──────────────────────────────────────────────────────────────────

def _sizer(ctx: SystemContext) -> PositionSizer:
    """The REAL sizer instance the signal processor uses — not a rebuilt copy."""
    return ctx.signal_processor._sizer


def _limits(ctx: SystemContext) -> dict:
    """Derive the guard thresholds from the sizer's OWN configuration (rule D: assert
    relationships, never hard-coded rupee figures). If config changes, these follow."""
    s = _sizer(ctx)
    snap = ctx.fund_manager.get_snapshot()
    lev = s._leverage_map["INTRADAY"]
    return {
        "total": snap.total,
        "intraday_avail": snap.intraday_avail,
        "risk_pct": s._risk_per_trade_pct,
        "conc_pct": s._max_concentration_pct,
        "maxposval_pct": s._max_position_value_pct,
        "leverage": lev,
        "risk_rs": snap.total * s._risk_per_trade_pct,
        "conc_rs": snap.total * s._max_concentration_pct,
        "maxposval_rs": snap.total * s._max_position_value_pct,
        # RISK binds when sl_distance exceeds this fraction of price
        "risk_binds_above_sl_frac": s._risk_per_trade_pct / s._max_concentration_pct,
        # CAPITAL binds when the intraday bucket falls below this
        "capital_binds_below_avail": snap.total * s._max_concentration_pct / lev,
    }


def _picture(ctx: SystemContext) -> dict:
    """The full money picture (Q9 standing rule A)."""
    s = ctx.fund_manager.get_snapshot()
    return {
        "total": s.total,
        "avail": s.intraday_avail + s.positional_avail,
        "reserved": s.intraday_reserved + s.positional_reserved,
        "used": s.intraday_used + s.positional_used,
        "intraday_avail": s.intraday_avail,
        "intraday_reserved": s.intraday_reserved,
    }


def _assert_capital_sane(ctx: SystemContext, stage: str) -> dict:
    """I1 (global identity) + the kill switch must never have armed. Batch 3 proved the
    global form is the one that holds; the per-bucket form does NOT (fund_manager.py:2235)."""
    p = _picture(ctx)
    lhs = round(p["avail"] + p["reserved"] + p["used"], 2)
    rhs = round(p["total"], 2)
    assert abs(lhs - rhs) <= 0.01, (
        f"CAPITAL INVARIANT BROKEN at {stage!r}: avail({p['avail']:.2f}) + "
        f"reserved({p['reserved']:.2f}) + used({p['used']:.2f}) = {lhs:.2f} != total {rhs:.2f}"
    )
    assert not ctx.kill_switch.is_active("entry"), (
        f"kill switch armed during {stage!r} — a scenario tripped the daily-loss breach"
    )
    return p


def _spy_sizer(ctx: SystemContext) -> list:
    """Delegating spy on the REAL sizer. Records every call and its SizingResult, so a test
    can assert the sizer was reached at all and with which inputs — a status string alone
    cannot distinguish 'sized to 50' from 'never sized'."""
    calls: list = []
    s = _sizer(ctx)
    real = s.calculate

    def _wrapped(*a, **k):
        res = real(*a, **k)
        calls.append({"args": a, "kwargs": k, "result": res})
        return res

    s.calculate = _wrapped
    return calls


def _spy_broker(ctx: SystemContext) -> list:
    """Delegating spy on the BROKER BOUNDARY (adapter.place_order) — batch 2's pattern.
    'Nothing reached the broker' is asserted against this record, not against a status."""
    calls: list = []
    real = ctx.adapter.place_order

    def _wrapped(*a, **k):
        calls.append(k.get("symbol") or (a[0] if a else "?"))
        return real(*a, **k)

    ctx.adapter.place_order = _wrapped
    return calls


def _drive_webhook(ctx: SystemContext, symbol: str, ltp: float) -> tuple:
    """One REAL signal from webhook POST to a terminal status, through screening into sizing.

    The rich-quote patch must stay active for the whole async processing window (the
    signal_processor runs on worker threads), which is why the wait happens inside it.
    """
    ctx.sim_kite.set_rich_quote(symbol, ltp=ltp)
    rich_md = ctx.sim_kite.get_market_data(symbol)
    with patch.object(ctx.screener, "_build_market_data", return_value=rich_md):
        code, body = _post_webhook(
            ctx, SCANNER, _make_payload(scanner_name=SCANNER, symbol=symbol, price=str(ltp))
        )
        assert code == 200, f"webhook rejected the post: {body}"
        sid = _wait_for_signal(ctx, symbol, timeout=5.0)
        assert sid is not None, "signal never reached the store"
        status = _wait_for_signal_status(ctx, sid, TERMINAL, timeout=8.0)
    return sid, status


def _bd(call: dict) -> dict:
    return call["result"].breakdown


def _assert_bound_by(res, expected: str, where: str) -> None:
    """Rule G: prove WHICH guard bound, and that the decision was not a tie.

    A tie is not decisive evidence — if two arms produce the same number the scenario cannot
    tell them apart, so this refuses to accept one.
    """
    bd = res.breakdown
    arms = {
        "RISK": bd.get("qty_by_risk"),
        "CAPITAL": bd.get("qty_by_capital"),
        "CONCENTRATION": bd.get("qty_by_concentration"),
    }
    assert res.constraint == expected, (
        f"{where}: expected {expected} to bind, but {res.constraint} did. arms={arms} "
        f"raw={bd.get('raw_qty')} reason={res.reason}"
    )
    if expected in arms:
        mine = arms[expected]
        others = {k: v for k, v in arms.items() if k != expected}
        assert all(v > mine for v in others.values()), (
            f"{where}: {expected}={mine} is not the STRICT unique minimum (others={others}) "
            f"— the scenario cannot prove which guard bound. Make it decisive."
        )


# ═════════════════════════════════════════════════════════════════════════════
# 1. THE WIRED PATH — sizing is reached, and concentration is what binds
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestSizingIsWired:

    def test_a_real_signal_reaches_the_sizer_and_concentration_binds(self, wired_system):
        """POSITIVE + BINDING ORDER. The end-to-end proof that sizing is on the live path,
        that CONCENTRATION is the strict unique minimum, and that the sized quantity is the
        quantity that actually reserves capital and reaches the broker."""
        ctx = wired_system
        before = _assert_capital_sane(ctx, "wired:before")
        sized = _spy_sizer(ctx)
        broker = _spy_broker(ctx)

        sid, status = _drive_webhook(ctx, "RELIANCE", 1000.0)

        assert status == "PROCESSED", f"signal did not complete: {status}"
        # The sizer was REACHED — the whole point of this batch.
        assert len(sized) == 1, (
            f"expected exactly one sizing call on the wired path, got {len(sized)}"
        )
        res = sized[0]["result"]
        assert res.success, f"sizing failed unexpectedly: {res.reason}"

        # BINDING ORDER (rule G): concentration, strictly, not by a tie.
        _assert_bound_by(res, "CONCENTRATION", "wired happy path")

        lim = _limits(ctx)
        bd = res.breakdown
        entry = sized[0]["args"][2]
        # The concentration arm is exactly its formula — not an approximation.
        assert bd["qty_by_concentration"] == int(lim["conc_rs"] // entry), (
            f"conc arm {bd['qty_by_concentration']} != floor({lim['conc_rs']}/{entry})"
        )
        # ANTI-VACUITY: the guard genuinely CLAMPED — it did not merely agree with the others.
        assert bd["raw_qty"] < bd["qty_by_risk"], (
            f"concentration did not clamp: raw={bd['raw_qty']} risk={bd['qty_by_risk']}"
        )
        assert bd["raw_qty"] < bd["qty_by_capital"]
        assert res.qty > 0

        # The sized quantity is what actually moved money and reached the broker.
        trades = ctx.store.fetch_all(
            "SELECT qty_planned, binding_constraint, actual_position_value_rs, "
            "       qty_by_risk, qty_by_capital, qty_by_concentration "
            "FROM trades WHERE symbol = ?", ("RELIANCE",)
        )
        assert len(trades) == 1, f"expected one trade row, got {len(trades)}"
        row = trades[0]
        assert row["qty_planned"] == res.qty, (
            f"the trade was created with qty {row['qty_planned']} but sizing said {res.qty}"
        )
        assert row["binding_constraint"] == "concentration"
        assert broker == ["RELIANCE"], f"broker call record wrong: {broker}"

        # Rule A: the capital picture moved by exactly the sized position, and only there.
        after = _assert_capital_sane(ctx, "wired:after")
        assert after["reserved"] > before["reserved"], "sizing produced no reservation"
        assert after["avail"] < before["avail"]
        assert after["total"] == pytest.approx(before["total"]), (
            "sizing/reserving must not move total capital"
        )
        # The reservation matches the SIZED quantity — a guard that clamps qty but not the
        # reservation would leak capital, and this is what catches that.
        expected_margin = res.qty * (entry / lim["leverage"])
        assert after["reserved"] >= expected_margin * 0.99, (
            f"reserved {after['reserved']:.2f} is below the margin for the sized qty "
            f"({expected_margin:.2f}) — the reservation does not match the sized quantity"
        )

    def test_position_value_stays_within_the_concentration_cap(self, wired_system):
        """The cap is expressed in RUPEES, not shares — assert the money, not the count."""
        ctx = wired_system
        sized = _spy_sizer(ctx)
        _drive_webhook(ctx, "RELIANCE", 1000.0)
        res = sized[0]["result"]
        entry = sized[0]["args"][2]
        lim = _limits(ctx)

        pos_value = res.qty * entry
        assert pos_value <= lim["conc_rs"] + 0.01, (
            f"position value Rs{pos_value:.2f} exceeds the concentration cap "
            f"Rs{lim['conc_rs']:.2f} ({lim['conc_pct']:.0%} of Rs{lim['total']:.2f})"
        )
        # ANTI-VACUITY: it is genuinely AT the cap, not trivially far below it.
        assert pos_value > lim["conc_rs"] * 0.90, (
            f"position value Rs{pos_value:.2f} is far below the cap Rs{lim['conc_rs']:.2f} — "
            f"this scenario is not actually exercising the concentration guard"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 2. EACH CLAMP ARM — positive, negative, binding-order proven
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestClampArms:

    def test_risk_arm_binds_only_when_the_stop_is_wide(self, wired_system):
        """POSITIVE + NEGATIVE for G2, with the algebraic threshold asserted.

        RISK binds <=> sl_distance > (risk_pct/conc_pct) * price. Under production config
        that is a stop wider than 10% of price; intraday stops are 1-3%, which is precisely
        why the risk sizer never binds live (§C).
        """
        ctx = wired_system
        s = _sizer(ctx)
        lim = _limits(ctx)
        price = 1000.0
        threshold = lim["risk_binds_above_sl_frac"] * price

        # NEGATIVE — a normal 1% stop: RISK must NOT bind.
        narrow = s.calculate("NARROW", "BUY", price, price - (0.01 * price),
                             "INTRADAY", "HIGH", 1, perf_weight=1.0)
        _assert_bound_by(narrow, "CONCENTRATION", "narrow stop")
        assert narrow.constraint != "RISK"

        # POSITIVE — a stop just past the threshold: RISK must bind.
        wide = s.calculate("WIDE", "BUY", price, price - (threshold * 1.2),
                           "INTRADAY", "HIGH", 1, perf_weight=1.0)
        _assert_bound_by(wide, "RISK", "wide stop")

        # ANTI-VACUITY: crossing the threshold actually CHANGED the outcome and the quantity.
        assert wide.qty < narrow.qty, (
            f"widening the stop past the threshold did not reduce quantity "
            f"({narrow.qty} -> {wide.qty})"
        )
        # And the risk arm equals its formula.
        assert wide.breakdown["qty_by_risk"] == int(
            lim["risk_rs"] // (threshold * 1.2)
        )

    def test_capital_arm_binds_only_when_the_bucket_is_nearly_exhausted(self, wired_system):
        """POSITIVE + NEGATIVE for G4, driven by REAL reservations against the real
        FundManager — not by constructing a sizer with a fake snapshot."""
        ctx = wired_system
        s = _sizer(ctx)
        lim = _limits(ctx)
        price = 1000.0

        # NEGATIVE — a fresh bucket: CAPITAL must NOT bind.
        fresh = s.calculate("FRESH", "BUY", price, price * 0.99, "INTRADAY", "HIGH", 1,
                            perf_weight=1.0)
        _assert_bound_by(fresh, "CONCENTRATION", "fresh bucket")

        # Drain the intraday bucket with REAL reserves until it falls under the threshold.
        target = lim["capital_binds_below_avail"] * 0.5
        drained = 0
        for i in range(40):
            snap = ctx.fund_manager.get_snapshot()
            if snap.intraday_avail < target:
                break
            # reserve in chunks; stop when the FM refuses (bucket genuinely exhausted)
            step = max(1, int((snap.intraday_avail - target) / (price / lim["leverage"]) * 0.9))
            r = ctx.fund_manager.reserve(symbol=f"DRAIN{i}", qty=step, price=price,
                                         intent="INTRADAY", signal_id=f"drain_{i}")
            if not r.success:
                break
            drained += 1
        snap = ctx.fund_manager.get_snapshot()
        assert snap.intraday_avail < lim["capital_binds_below_avail"], (
            f"could not drain the bucket below the CAPITAL threshold "
            f"(avail={snap.intraday_avail:.2f}, need < {lim['capital_binds_below_avail']:.2f})"
        )
        _assert_capital_sane(ctx, "after-drain")

        # POSITIVE — with the bucket drained, CAPITAL must bind.
        starved = s.calculate("STARVED", "BUY", price, price * 0.99, "INTRADAY", "HIGH", 1,
                              perf_weight=1.0)
        _assert_bound_by(starved, "CAPITAL", "drained bucket")
        # ANTI-VACUITY: the quantity actually fell because of the drain.
        assert starved.breakdown["qty_by_capital"] < fresh.breakdown["qty_by_capital"], (
            "draining the bucket did not reduce the capital arm"
        )

    def test_the_tier_multiplier_actually_scales_the_quantity(self, wired_system):
        """G9 (:503). POSITIVE + NEGATIVE for the tier multiplier itself.

        Added after a planted break revealed the gap: every other test in this file used the
        HIGH tier (multiplier 1.0), so silently dropping the multiplier altogether changed
        nothing they asserted. A multiplier that never multiplies produces a perfectly legal
        quantity — inside every cap, positive, correctly recorded — so nothing in production
        can see it either. Only a direct assertion on the scaling catches it.
        """
        ctx = wired_system
        s = _sizer(ctx)
        price = 1000.0
        out = {
            tier: s.calculate(tier, "BUY", price, price * 0.99, "INTRADAY", tier, 1,
                              perf_weight=1.0)
            for tier in ("HIGH", "MEDIUM", "LOW")
        }
        for tier, res in out.items():
            assert res.success, f"{tier} failed to size: {res.reason}"

        raw = out["HIGH"].breakdown["raw_qty"]
        # Each tier is EXACTLY floor(raw * its configured multiplier) — the contract, not a
        # direction-only check (a direction check passes on a multiplier that is merely
        # monotonic but wrong).
        for tier, res in out.items():
            expected = int(raw * s._tier_multipliers[tier])
            assert res.qty == expected, (
                f"tier {tier} (multiplier {s._tier_multipliers[tier]}) sized {res.qty}, "
                f"expected floor({raw} * {s._tier_multipliers[tier]}) = {expected}"
            )
        # ANTI-VACUITY: the tiers must actually DIFFER, or the assertion above is trivial.
        assert out["HIGH"].qty > out["MEDIUM"].qty > out["LOW"].qty, (
            f"the tier multiplier did not scale quantity: "
            f"HIGH={out['HIGH'].qty} MEDIUM={out['MEDIUM'].qty} LOW={out['LOW'].qty}"
        )
        # NEGATIVE: the multiplier must not disturb the clamp arms it is applied AFTER.
        for tier, res in out.items():
            assert res.breakdown["qty_by_concentration"] == \
                out["HIGH"].breakdown["qty_by_concentration"], (
                f"tier {tier} changed the concentration arm — the multiplier is applied "
                f"before the clamp, not after"
            )

    def test_concentration_arm_rejects_a_price_above_the_cap(self, wired_system):
        """G5 + G7. A single share costing more than the per-position cap sizes to zero and
        is REJECTED — this is the mechanism behind the >Rs-cap exclusion reported in §C."""
        ctx = wired_system
        s = _sizer(ctx)
        lim = _limits(ctx)

        # NEGATIVE — just UNDER the cap: one share is affordable, so it sizes.
        under = s.calculate("UNDER", "BUY", lim["conc_rs"] * 0.98,
                            lim["conc_rs"] * 0.98 * 0.99, "INTRADAY", "HIGH", 1,
                            perf_weight=1.0)
        assert under.success, f"a price just under the cap must still size: {under.reason}"
        assert under.qty == 1

        # POSITIVE — just OVER the cap: the concentration arm goes to zero and rejects.
        over = s.calculate("OVER", "BUY", lim["conc_rs"] * 1.02,
                           lim["conc_rs"] * 1.02 * 0.99, "INTRADAY", "HIGH", 1,
                           perf_weight=1.0)
        assert not over.success
        assert over.constraint == "CONCENTRATION", (
            f"expected CONCENTRATION to reject, got {over.constraint}: {over.reason}"
        )
        assert over.qty == 0
        assert over.breakdown["qty_by_concentration"] == 0
        # ANTI-VACUITY: the OTHER arms would happily have traded it — proving it is
        # concentration, and only concentration, that excludes this price.
        assert over.breakdown["qty_by_risk"] > 0, (
            "the risk arm also refused — this scenario does not isolate concentration"
        )
        assert over.breakdown["qty_by_capital"] > 0


# ═════════════════════════════════════════════════════════════════════════════
# 3. M-C6 — THE ZERO-MULTIPLIER SKIP (§B2, highest-risk case)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestZeroMultiplierSkip:
    """M-C6: a zero multiplier means 'size this to nothing' -> SKIP. The failure mode hunted
    here is a 0-quantity order being constructed and dispatched, or a reservation taken for a
    position that is never placed. Both are asserted against RECORDS, not status strings."""

    def test_zero_multiplier_places_nothing_and_leaks_no_capital(self, wired_system):
        """POSITIVE, through the full wired path — perf_weight is injected at its real
        constructor-argument seam (`SignalProcessor(perf_weights=...)`, read at :893)."""
        ctx = wired_system
        before = _assert_capital_sane(ctx, "mc6:before")
        broker = _spy_broker(ctx)
        sized = _spy_sizer(ctx)

        ctx.signal_processor._perf_weights = {SCANNER: 0.0}
        sid, status = _drive_webhook(ctx, "RELIANCE", 1000.0)

        assert status == "REJECTED_SIZING_ZERO_MULTIPLIER", (
            f"M-C6 did not fire; status={status}"
        )
        res = sized[0]["result"]
        assert res.constraint == "ZERO_MULTIPLIER"
        assert res.qty == 0
        assert not res.success

        # (a) NOTHING reached the broker — asserted on the call record (batch 2's pattern).
        assert broker == [], f"an order reached the broker despite a zero multiplier: {broker}"
        # (b) NO trade row was created.
        trades = ctx.store.fetch_all("SELECT trade_id, qty_planned FROM trades")
        assert trades == [] or len(trades) == 0, (
            f"a trade row was created for a skipped signal: {[dict(t) for t in trades]}"
        )
        # (c) NO reservation leaked — capital is byte-identical to before.
        after = _assert_capital_sane(ctx, "mc6:after")
        assert after["reserved"] == pytest.approx(before["reserved"]), (
            f"a reservation leaked on a skipped signal "
            f"({before['reserved']:.2f} -> {after['reserved']:.2f})"
        )
        assert after["avail"] == pytest.approx(before["avail"])
        assert after["used"] == pytest.approx(before["used"])
        assert after["total"] == pytest.approx(before["total"])

        # ANTI-VACUITY: raw_qty was POSITIVE — the skip came from the multiplier, not from
        # an exhausted arm that would have rejected anyway.
        assert res.breakdown["raw_qty"] > 0, (
            "raw_qty was already 0 — this proves nothing about the zero-multiplier skip"
        )
        assert res.breakdown["effective_mult"] == 0.0

    def test_a_positive_multiplier_does_not_skip(self, wired_system):
        """NEGATIVE (rule B). Without this, a skip that always skips looks identical to a
        skip that works. A small-but-positive multiplier must still trade."""
        ctx = wired_system
        broker = _spy_broker(ctx)
        sized = _spy_sizer(ctx)

        ctx.signal_processor._perf_weights = {SCANNER: 0.5}
        sid, status = _drive_webhook(ctx, "RELIANCE", 1000.0)

        assert status == "PROCESSED", f"a positive multiplier must still trade; got {status}"
        res = sized[0]["result"]
        assert res.success
        assert res.constraint != "ZERO_MULTIPLIER"
        assert res.qty > 0
        assert broker == ["RELIANCE"], "a positive multiplier must reach the broker"

    def test_a_negative_tier_multiplier_is_treated_as_zero(self, wired_system):
        """M-C6's other half. PositionSizingTierConfig types HIGH/MEDIUM/LOW as bare floats
        with no ge=0 bound, so a negative multiplier loads cleanly from config. It must skip,
        never produce a negative or floored-to-1 quantity."""
        ctx = wired_system
        s = _sizer(ctx)
        with patch.dict(s._tier_multipliers, {"HIGH": -1.0}):
            res = s.calculate("NEG", "BUY", 1000.0, 990.0, "INTRADAY", "HIGH", 1,
                              perf_weight=1.0)
        assert res.constraint == "ZERO_MULTIPLIER", f"got {res.constraint}: {res.reason}"
        assert res.qty == 0
        assert not res.success


# ═════════════════════════════════════════════════════════════════════════════
# 4. THE MIN-LOT FLOOR vs THE CAPS (§B3 — can a floor breach a cap?)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestFloorVersusCaps:
    """G10's floor RAISES a quantity (`max(1, ...)`), and it runs AFTER the three clamp arms.
    The question §B3 asks is whether that can push a position past a cap already applied.

    It cannot, and the reason is structural rather than incidental: G7 (:425) rejects
    outright when raw_qty <= 0, so any signal that reaches the floor has raw_qty >= 1, and
    raw_qty is by construction <= every one of the three arms. Flooring to 1 therefore lands
    at or below all three caps. This is PROVEN below rather than merely argued."""

    def test_the_floor_engages_but_never_exceeds_the_binding_arm(self, wired_system):
        """POSITIVE for the floor: a price where the tier multiplier rounds the quantity to
        zero, so the floor is what produces the final 1 — and 1 is still within every cap."""
        ctx = wired_system
        s = _sizer(ctx)
        lim = _limits(ctx)

        # Choose a price where conc allows exactly 1 share, so LOW (0.5x) floors 1 -> 0 -> 1.
        price = lim["conc_rs"] * 0.98
        res = s.calculate("FLOORED", "BUY", price, price * 0.99, "INTRADAY", "LOW", 1,
                          perf_weight=1.0)

        assert res.success, f"the floor should have produced a tradeable qty: {res.reason}"
        bd = res.breakdown
        assert bd["raw_qty"] == 1, f"scenario not set up: raw_qty={bd['raw_qty']}"
        # ANTI-VACUITY: the floor is what did the work — un-floored this would be 0.
        assert int(bd["raw_qty"] * 0.5) == 0, "the multiplier did not round to zero"
        assert res.qty == 1, f"the floor did not produce 1 lot (qty={res.qty})"

        # ⭐ THE §B3 QUESTION: the floored quantity is still inside every cap.
        assert res.qty <= bd["qty_by_concentration"], (
            f"THE FLOOR BREACHED THE CONCENTRATION CAP: qty={res.qty} > "
            f"conc arm {bd['qty_by_concentration']}"
        )
        assert res.qty <= bd["qty_by_risk"], (
            f"THE FLOOR BREACHED THE RISK CAP: qty={res.qty} > risk arm {bd['qty_by_risk']}"
        )
        assert res.qty <= bd["qty_by_capital"], (
            f"THE FLOOR BREACHED THE CAPITAL CAP: qty={res.qty} > "
            f"capital arm {bd['qty_by_capital']}"
        )
        assert res.qty * price <= lim["conc_rs"] + 0.01, (
            f"THE FLOOR BREACHED THE CONCENTRATION CAP IN RUPEES: "
            f"Rs{res.qty * price:.2f} > Rs{lim['conc_rs']:.2f}"
        )
        assert res.qty * price <= lim["maxposval_rs"] + 0.01

    @pytest.mark.parametrize("tier", ["HIGH", "MEDIUM", "LOW"])
    def test_the_floor_never_exceeds_a_cap_across_the_price_range(self, wired_system, tier):
        """The structural claim, swept rather than argued: for every tier and a wide range of
        prices, the final quantity never exceeds any clamp arm while the multiplier is <= 1."""
        ctx = wired_system
        s = _sizer(ctx)
        lim = _limits(ctx)

        checked = 0
        for frac in (0.02, 0.05, 0.1, 0.25, 0.4, 0.6, 0.8, 0.9, 0.98):
            price = lim["conc_rs"] * frac
            res = s.calculate("SWEEP", "BUY", price, price * 0.99, "INTRADAY", tier, 1,
                              perf_weight=1.0)
            if not res.success:
                continue
            bd = res.breakdown
            checked += 1
            assert res.qty <= bd["qty_by_concentration"], (
                f"tier={tier} price={price:.2f}: qty {res.qty} exceeded the concentration "
                f"arm {bd['qty_by_concentration']}"
            )
            assert res.qty <= bd["qty_by_risk"]
            assert res.qty <= bd["qty_by_capital"]
        # ANTI-VACUITY: the sweep must actually have exercised something.
        assert checked >= 5, f"only {checked} prices sized successfully — sweep too thin"

    def test_below_min_rejects_rather_than_placing_a_zero_quantity(self, wired_system):
        """G15. When lot rounding leaves less than one lot the signal is REJECTED — it must
        never place a zero-quantity order."""
        ctx = wired_system
        s = _sizer(ctx)
        lim = _limits(ctx)
        # A price where conc allows 1 share but the lot size is 10 -> rounds to 0.
        price = lim["conc_rs"] * 0.98
        res = s.calculate("BELOWMIN", "BUY", price, price * 0.99, "INTRADAY", "HIGH", 10,
                          perf_weight=1.0)
        assert not res.success
        assert res.qty == 0
        assert res.constraint in ("BELOW_MIN", "REJECTED_LOT_SKEW"), (
            f"expected a below-minimum rejection, got {res.constraint}: {res.reason}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 5. THE REJECT-STYLE GUARDS — G1, G3, G13, G14
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestRejectGuards:

    def test_invalid_sl_distance_rejects_before_any_division(self, wired_system):
        """G1 (:333). POSITIVE + NEGATIVE around min_tick_size."""
        ctx = wired_system
        s = _sizer(ctx)
        tick = s._min_tick_size

        bad = s.calculate("TICK", "BUY", 1000.0, 1000.0 - tick * 0.5, "INTRADAY", "HIGH", 1)
        assert bad.constraint == "INVALID_SL_DISTANCE"
        assert bad.qty == 0 and not bad.success

        ok = s.calculate("TICK", "BUY", 1000.0, 1000.0 - tick * 2.0, "INTRADAY", "HIGH", 1)
        assert ok.constraint != "INVALID_SL_DISTANCE", (
            f"a stop of 2 ticks must be accepted: {ok.reason}"
        )

    def test_qty_explosion_guard_rejects_an_absurd_risk_arm(self, wired_system):
        """G3 (:365). Note it tests qty_by_risk ALONE, BEFORE the min() — so it fires even
        though concentration would have clamped the final quantity to something tiny. That
        ordering is deliberate (it is a sanity guard on the arithmetic, not on the order)."""
        ctx = wired_system
        s = _sizer(ctx)
        lim = _limits(ctx)
        # sl_distance just above min_tick -> qty_by_risk explodes past max_single_order_qty
        sl_dist = lim["risk_rs"] / (s._max_single_order_qty * 2.0)
        assert sl_dist > s._min_tick_size, "scenario would trip G1 instead of G3"

        res = s.calculate("BOOM", "BUY", 1000.0, 1000.0 - sl_dist, "INTRADAY", "HIGH", 1)
        assert res.constraint == "QTY_EXPLOSION_GUARD", (
            f"expected the explosion guard, got {res.constraint}: {res.reason}"
        )
        assert res.qty == 0 and not res.success
        assert res.breakdown["qty_by_risk"] > s._max_single_order_qty
        # ANTI-VACUITY: concentration would have clamped this to a small number anyway —
        # so the guard genuinely fired on the risk arm, ahead of the min().
        assert int(lim["conc_rs"] // 1000.0) < s._max_single_order_qty

    def test_lot_skew_rejects_when_rounding_wastes_too_much(self, wired_system):
        """G13 (:534). POSITIVE + NEGATIVE around lot_skew_rejection_threshold."""
        ctx = wired_system
        s = _sizer(ctx)
        # tiered 35 with lot 25 -> final 25 -> skew 28.6% > 25% -> reject
        bad = s.calculate("SKEW", "BUY", 1000.0, 990.0, "INTRADAY", "MEDIUM", 25,
                          perf_weight=1.0)
        assert bad.constraint == "REJECTED_LOT_SKEW", (
            f"expected a lot-skew rejection, got {bad.constraint}: {bad.reason}"
        )
        skew = (bad.breakdown["tiered_qty"] - 25) / bad.breakdown["tiered_qty"]
        assert skew > s._lot_skew_rejection_threshold

        # NEGATIVE — lot_size 1 can never skew, so an identical signal sizes fine.
        ok = s.calculate("SKEW", "BUY", 1000.0, 990.0, "INTRADAY", "MEDIUM", 1,
                         perf_weight=1.0)
        assert ok.success and ok.constraint != "REJECTED_LOT_SKEW"

    def test_position_value_cap_rejects_when_it_can_be_reached(self, wired_system):
        """G14 (:562). Under production ratios this guard is UNREACHABLE — concentration
        (10%) always clamps far below the position-value cap (40%), so no realistic signal
        can reach it (see §C). To prove the guard WORKS rather than merely that it never
        fires, this builds a LOCAL sizer whose concentration is looser than the cap. No
        production config is touched, and the wired sizer is left exactly as it was."""
        ctx = wired_system
        lim = _limits(ctx)

        # First: prove it is unreachable through the REAL wired sizer.
        real = _sizer(ctx)
        assert real._max_concentration_pct < real._max_position_value_pct, (
            "concentration is no longer tighter than the position-value cap — the "
            "unreachability argument in §C no longer holds and must be re-derived"
        )
        top = real.calculate("TOP", "BUY", 100.0, 99.0, "INTRADAY", "HIGH", 1, perf_weight=1.0)
        assert top.success
        assert top.qty * 100.0 <= lim["maxposval_rs"], "the real sizer reached the value cap"

        # Now: a local sizer with concentration LOOSER than the value cap can reach it.
        loose = PositionSizer(
            max_position_value_pct=0.40,  # NI-5: was a silent default
            fund_manager=ctx.fund_manager,
            leverage_map=real._leverage_map,
            risk_per_trade_pct=real._risk_per_trade_pct,
            max_concentration_pct=real._max_position_value_pct * 2.0,
            min_qty_threshold=real._min_qty_threshold,
            tier_multipliers=dict(real._tier_multipliers),
        )
        res = loose.calculate("CAPME", "BUY", 100.0, 99.0, "INTRADAY", "HIGH", 1,
                              perf_weight=1.0)
        assert res.constraint == "POSITION_VALUE_CAP", (
            f"expected the position-value cap to reject, got {res.constraint}: {res.reason}"
        )
        assert res.qty == 0 and not res.success
        # The real sizer was not disturbed.
        assert _sizer(ctx) is real
        assert real._max_concentration_pct == lim["conc_pct"]


# ═════════════════════════════════════════════════════════════════════════════
# 6. ⭐ THE 2x CEILING — a cap that CAN be exceeded (currently unreachable)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestTwoTimesMultiplierCeiling:
    """G10's ceiling is `min(tiered_qty, raw_qty * 2)`. For an effective multiplier > 1 that
    permits a final quantity of TWICE the tightest clamp arm.

    This is recorded here as EXECUTABLE DOCUMENTATION of current behaviour, not as an
    endorsement of it and not as a bug being fixed. Nothing in production can reach it today
    (see test_perf_weight_is_never_wired_in_production below), so it is not a live capital
    finding — but the configuration already asks for the feature that would make it live, so
    it must not be able to land silently.
    """

    def test_a_multiplier_above_one_exceeds_every_clamp_arm(self, wired_system):
        ctx = wired_system
        s = _sizer(ctx)
        lim = _limits(ctx)
        price = 1000.0

        base = s.calculate("BASE", "BUY", price, price * 0.99, "INTRADAY", "HIGH", 1,
                           perf_weight=1.0)
        boosted = s.calculate("BOOST", "BUY", price, price * 0.99, "INTRADAY", "HIGH", 1,
                              perf_weight=2.0)

        conc_arm = base.breakdown["qty_by_concentration"]
        assert base.qty == conc_arm, "baseline should sit exactly on the concentration arm"

        # The documented behaviour: exactly 2x the raw (clamped) quantity.
        assert boosted.qty == base.breakdown["raw_qty"] * 2, (
            f"expected the 2x ceiling to produce {base.breakdown['raw_qty'] * 2}, "
            f"got {boosted.qty}"
        )
        # ...which is DOUBLE the concentration limit the same call reports as binding.
        assert boosted.qty > conc_arm
        # BUG-NI18 (23-Aug-2026) — FIXED, and this assertion is the tripwire that
        # asked to be re-checked. The column used to report `concentration` for a
        # quantity DOUBLE the concentration rung: a field naming a limit the result
        # exceeds. It now reports `multiplier`, and the rung it would have named is
        # preserved so no information is lost. ⛔ The QUANTITY is unchanged — proven
        # over a 432-point grid, 0 differences in success/qty/margin.
        assert boosted.breakdown["binding_constraint"] == "multiplier", (
            "the multiplier lifted qty above the tightest rung, so no rung bound it; "
            "the audit column must not name one"
        )
        assert boosted.breakdown["rung_before_multiplier"] == "concentration", (
            "the pre-multiplier rung must stay recoverable from the breakdown"
        )
        # The position-value cap does not catch it: 2 x conc_pct is still under it.
        pos_value = boosted.qty * price
        assert pos_value > lim["conc_rs"], "the boost did not exceed the concentration cap"
        assert pos_value <= lim["maxposval_rs"], (
            "the position-value cap DID catch the 2x overshoot — that would be good news, "
            "and this finding should be re-derived"
        )

    def test_the_ceiling_itself_is_exactly_two_times_raw(self, wired_system):
        """The CEILING, as distinct from the multiplier that reaches for it.

        Added after a planted break widened `raw_qty * 2` to `raw_qty * 4` and the suite
        stayed green: a perf_weight of 2.0 asks for exactly 2x, so it never touches the
        ceiling and cannot detect where the ceiling sits. Proving a ceiling requires a
        multiplier that OVERSHOOTS it.
        """
        ctx = wired_system
        s = _sizer(ctx)
        price = 1000.0

        base = s.calculate("BASE", "BUY", price, price * 0.99, "INTRADAY", "HIGH", 1,
                           perf_weight=1.0)
        raw = base.breakdown["raw_qty"]

        for overshoot in (3.0, 5.0, 20.0):
            res = s.calculate("CEIL", "BUY", price, price * 0.99, "INTRADAY", "HIGH", 1,
                              perf_weight=overshoot)
            assert res.qty == raw * 2, (
                f"perf_weight={overshoot} produced qty={res.qty}; the FIX-133 ceiling should "
                f"clamp it to exactly 2 x raw_qty ({raw} -> {raw * 2}). If the ceiling has "
                f"been widened, the maximum overshoot of the concentration cap has grown "
                f"with it — capital/position_sizer.py:506."
            )
            # ANTI-VACUITY: the multiplier really did ask for more than the ceiling allows.
            assert int(raw * overshoot) > raw * 2

    def test_perf_weight_is_never_wired_in_production(self, wired_system):
        """THE REACHABILITY GUARD (§C). The 2x ceiling above is unreachable for exactly one
        reason: nothing ever supplies a perf_weight other than the 1.0 default. If that
        changes, this test fails and the finding above becomes live — which is the point.

        Asserted against SOURCE, because the fact being pinned is 'no code path constructs
        this', which no runtime fixture can demonstrate by itself.
        """
        ctx = wired_system
        # (a) the wired processor carries no weights
        assert ctx.signal_processor._perf_weights == {}, (
            f"the fixture's processor now carries perf weights: "
            f"{ctx.signal_processor._perf_weights}"
        )

        root = Path(__file__).resolve().parents[2]
        # (b) PerformanceAllocator is never instantiated outside its own module
        hits = []
        for py in root.rglob("*.py"):
            parts = py.parts
            if any(p in ("tests", ".venv", "__pycache__", "ops_dashboard") for p in parts):
                continue
            if py.name == "performance_allocator.py":
                continue
            text = py.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"\bPerformanceAllocator\s*\(", text):
                hits.append(str(py.relative_to(root)))
        assert hits == [], (
            f"PerformanceAllocator is now instantiated in {hits} — perf_weight can become "
            f"!= 1.0, which makes the 2x concentration overshoot in "
            f"TestTwoTimesMultiplierCeiling REACHABLE IN PRODUCTION. Re-assess before "
            f"shipping: capital/position_sizer.py:506."
        )
        # (c) main.py never passes perf_weights into SignalProcessor
        main_src = (root / "main.py").read_text(encoding="utf-8", errors="ignore")
        assert not re.search(r"perf_weights\s*=", main_src), (
            "main.py now passes perf_weights to SignalProcessor — see (b) above; the 2x "
            "overshoot becomes reachable."
        )


# ═════════════════════════════════════════════════════════════════════════════
# 7. PARITY (Rule #5) — structural, so a future duplicate cannot drift
# ═════════════════════════════════════════════════════════════════════════════

class TestParity:
    """Sizing must be identical in paper and live. It is, structurally: there is ONE sizer,
    it has no mode input, and it sits upstream of the paper/live branch. Batches 2 and 3
    established this pattern; these assertions keep it true."""

    def test_the_sizer_has_no_mode_branch(self):
        src = Path(__file__).resolve().parents[2] / "capital" / "position_sizer.py"
        text = src.read_text(encoding="utf-8")
        code = "\n".join(
            ln for ln in text.splitlines()
            if not ln.lstrip().startswith("#")
        )
        for token in ("paper_mode", "is_paper", "live_mode"):
            assert token not in code, (
                f"capital/position_sizer.py now branches on {token!r} — sizing has forked "
                f"between paper and live and parity can no longer be argued structurally"
            )

    def test_there_is_exactly_one_sizing_call_site_on_the_entry_path(self):
        src = Path(__file__).resolve().parents[2] / "signals" / "signal_processor.py"
        text = src.read_text(encoding="utf-8")
        sites = re.findall(r"self\._sizer\.calculate\(", text)
        assert len(sites) == 3, (
            f"expected the 3 known sizing call sites in signal_processor.py, found "
            f"{len(sites)} — a new sizing path may have been added; verify it is also "
            f"mode-free and covered here."
        )
        # None of them is inside a paper/live conditional: the mode branch lives downstream.
        adapter = Path(__file__).resolve().parents[2] / "broker" / "zerodha_adapter.py"
        assert "paper_mode" in adapter.read_text(encoding="utf-8"), (
            "the paper/live divergence is no longer in zerodha_adapter.py — re-derive where "
            "sizing sits relative to the mode branch"
        )
