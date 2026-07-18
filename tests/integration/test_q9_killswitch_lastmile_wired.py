"""
tests/integration/test_q9_killswitch_lastmile_wired.py — Q9 batch 2 (#3).

THE KILL SWITCH'S LAST-MILE RE-CHECK, PROVEN THROUGH THE WIRED PATH (TOCTOU).

THE BUG CLASS. The kill switch is checked EARLY in the signal pipeline, but the order is
dispatched to the broker LATER. If Rama arms the kill in that window, an order can still go
out AFTER he has said STOP. The defence is a last-mile re-check immediately before dispatch.
Before this file the re-check was UNIT-ONLY (tests/unit/test_a3_entry_kill_recheck.py): no
integration test had ever armed the kill inside the real window and proven that nothing
reached the broker.

THE WINDOW, MAPPED IN REAL CODE (Q9 batch 2 §B1, not assumed):
  * OP-LM1 — orders/order_placer.py:1004 — `is_active("entry")` BEFORE the pre-submit work.
    Raises OrderRejectedError("kill_switch_active_last_mile").
  * ...the window...  orders/order_placer.py:1016 `self._om.update_trade_status(trade_id,
    "PENDING")` is the real call that executes strictly between the two checks under this
    fixture (empirically probed — `_fetch_ltp` is NOT called on the paper path, so it is not
    a usable seam despite appearing in the window in source order).
  * A-3 — orders/order_placer.py:1289 — the TRUE last mile: `is_active("entry")` immediately
    before `self._engine.execute(...)`, inside the retry loop, raised OUTSIDE the try so the
    loop's OrderRejectedError handler cannot catch-and-retry it.
    Raises OrderRejectedError("kill_switch_active_last_mile_presubmit").

⭐ THE TWO CHECKS RAISE DIFFERENT MESSAGES, and this file leans on that: asserting the
`_presubmit` message proves the LAST-MILE check fired and not the earlier OP-LM1. That is the
Q9 batch 1 lesson applied — a safety-gate test must prove it exercised the INTENDED gate and
not an earlier one. No multi-way accepts anywhere in this file.

KILL SEMANTICS (capital/kill_switch.py:447-459): `is_active("entry")` is True for SOFT_KILL
OR HARD_KILL; `is_active("exit")` is True only for HARD_KILL (a soft kill must still allow
exits). Both last-mile checks use "entry", so they block ENTRIES under either kill and never
block an exit under a soft kill — which is the correct asymmetry.

PARITY (Rule #5). The check is SHARED, not duplicated per mode: order_placer.place() has no
paper/live branch between the checks and dispatch (`self._mode` is used only for alert
labels, :691/:710/:964/:1130); the divergence lives inside broker/zerodha_adapter.py
(paper_mode → _paper_place_order, :348, ZA10). So proving the block in the fixture's paper
mode proves it for live too — there is no second copy to drift, which is what caused the P1
bug elsewhere.

WHY NO EARLIER GATE CAN MASK THIS (§B1f): the kill is armed INSIDE order_placer.place(),
i.e. after the webhook check (webhook_receiver.py:512), after the signal-processor checks
(signal_processor.py:684 etc.) and after the risk engine's KILL_SWITCH check
(risk_engine.py:270) have all already passed. Reaching `place()` at all is the proof they did.
"""
from __future__ import annotations

import pytest

from core.exceptions import OrderRejectedError
from tests.integration.conftest import SystemContext
from tests.integration.test_full_signal_flow import _seed_signal_row

SYMBOL = "RELIANCE"
QTY = 10
ENTRY = 100.0
SL = 98.0


def _capital_picture(ctx: SystemContext) -> dict:
    """Full money picture asserted at every transition (Q9 standing rule A)."""
    snap = ctx.fund_manager.get_snapshot()
    return {
        "total": snap.total,
        "intraday_avail": snap.intraday_avail,
        "intraday_reserved": snap.intraday_reserved,
        "intraday_used": snap.intraday_used,
        "daily_realized_pnl": snap.daily_realized_pnl,
    }


def _spy_broker(ctx: SystemContext) -> list:
    """Delegating spy on the BROKER BOUNDARY (adapter.place_order).

    'No order reached the broker' is asserted against this call record, not against a
    status string — a status can be wrong for many reasons; the broker call either
    happened or it did not.
    """
    calls: list = []
    real = ctx.adapter.place_order

    def _wrapped(*a, **k):
        calls.append(k.get("symbol") or (a[0] if a else "?"))
        return real(*a, **k)

    ctx.adapter.place_order = _wrapped
    return calls


def _arm_kill_inside_window(ctx: SystemContext, reason: str) -> dict:
    """Arm the kill switch INSIDE the (OP-LM1, A-3) window via a DELEGATING wrapper on a
    REAL call that executes there — order_manager.update_trade_status(..., "PENDING").

    The wrapper delegates to the real implementation (arm, then call through); it does not
    replace behaviour, so the path under test is unchanged apart from the arming.
    """
    state = {"armed": False}
    real = ctx.order_manager.update_trade_status

    def _wrapped(trade_id, status, *a, **k):
        if status == "PENDING" and not state["armed"]:
            state["armed"] = True
            ctx.kill_switch.soft_kill(reason=reason, triggered_by="q9_batch2_test")
        return real(trade_id, status, *a, **k)

    ctx.order_manager.update_trade_status = _wrapped
    return state


def _reserve_and_place(ctx: SystemContext, signal_id: str):
    _seed_signal_row(ctx, signal_id, SYMBOL)
    res = ctx.fund_manager.reserve(
        symbol=SYMBOL, qty=QTY, price=ENTRY, intent="INTRADAY", signal_id=signal_id,
    )
    assert res.success, f"reserve failed: {res}"
    ctx.order_placer.place(
        symbol=SYMBOL, side="BUY", qty=QTY, entry_price=ENTRY, sl_price=SL,
        intent="INTRADAY", signal_id=signal_id,
        reservation_id=res.reservation_id, strategy="vwap_bounce_long",
    )
    return res


@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestKillSwitchLastMileWired:

    # ── POSITIVE: armed inside the TOCTOU window → must block ────────────────
    def test_kill_armed_inside_toctou_window_blocks_before_broker(self, wired_system):
        ctx = wired_system
        broker_calls = _spy_broker(ctx)
        before = _capital_picture(ctx)
        assert not ctx.kill_switch.is_active("entry"), "precondition: kill must start clear"

        armed = _arm_kill_inside_window(ctx, reason="q9_batch2_toctou")

        # Deliberately NOT pytest.raises(...): if the re-check is broken, place() simply
        # succeeds, and the most informative failure is then "an order reached the broker",
        # not "DID NOT RAISE". Capture the exception and assert the broker record FIRST.
        raised = None
        try:
            _reserve_and_place(ctx, "q9_ks_positive")
        except OrderRejectedError as exc:
            raised = exc

        assert armed["armed"], (
            "the arming seam never fired — update_trade_status(PENDING) did not run, so the "
            "kill was never armed inside the window and this test proved nothing"
        )

        # ⭐ THE ASSERTION THAT MATTERS: nothing reached the broker.
        assert broker_calls == [], (
            f"AN ORDER REACHED THE BROKER AFTER THE KILL WAS ARMED MID-FLIGHT: "
            f"{broker_calls} — the last-mile re-check (order_placer.py:1289) did not hold"
        )
        assert raised is not None, (
            "place() completed without raising even though the kill was armed inside the "
            "(OP-LM1, A-3) window — the entry was not rejected"
        )

        # ⭐ THE LAST-MILE check fired, not the earlier OP-LM1 (different messages).
        msg = str(raised)
        assert "kill_switch_active_last_mile_presubmit" in msg, (
            f"expected the A-3 last-mile re-check (order_placer.py:1289) to fire, got {msg!r}. "
            f"A bare 'kill_switch_active_last_mile' would mean OP-LM1 caught it and the "
            f"TOCTOU window was never actually exercised."
        )
        # ...corroborated at the DB boundary: no ENTRY order row was ever written.
        # (orders has no `symbol` column — it is keyed on trade_id.)
        entry_orders = ctx.store.fetch_all("SELECT * FROM orders WHERE leg = 'ENTRY'")
        assert entry_orders == [], f"ENTRY order row written despite the kill: {entry_orders}"

        # ── B5: the ORPHAN-RESERVATION check ────────────────────────────────
        # The kill fired AFTER reserve() but BEFORE dispatch. If the reservation is not
        # released, capital leaks on every kill until restart or reconciliation.
        after = _capital_picture(ctx)
        assert after["intraday_reserved"] == pytest.approx(before["intraday_reserved"]), (
            f"CAPITAL LEAKED: reserved did not return to its pre-signal value "
            f"({before['intraday_reserved']} → {after['intraday_reserved']}) after a "
            f"last-mile block — every kill would strand margin"
        )
        assert after["intraday_avail"] == pytest.approx(before["intraday_avail"]), (
            f"available capital not restored after a last-mile block "
            f"({before['intraday_avail']} → {after['intraday_avail']})"
        )
        assert after["intraday_used"] == pytest.approx(before["intraday_used"])
        assert after["daily_realized_pnl"] == pytest.approx(before["daily_realized_pnl"]), (
            "a blocked entry must not move realized P&L"
        )

    # ── NEGATIVE: kill clear → the SAME signal must dispatch ─────────────────
    def test_same_signal_dispatches_when_kill_is_clear(self, wired_system):
        """Without this half the test cannot distinguish a working last-mile check from
        one that blocks everything. Same symbol, same qty, same prices — only the kill
        state differs."""
        ctx = wired_system
        broker_calls = _spy_broker(ctx)
        assert not ctx.kill_switch.is_active("entry")

        _reserve_and_place(ctx, "q9_ks_negative")

        assert broker_calls, (
            "the order did NOT reach the broker with the kill switch CLEAR — the last-mile "
            "check (or something upstream) is blocking unconditionally"
        )
        assert not ctx.kill_switch.is_active("entry"), "kill must still be clear"

    # ── DISTINGUISHER: armed BEFORE place() → the EARLIER check catches it ───
    def test_kill_armed_before_place_is_caught_by_the_earlier_check(self, wired_system):
        """Proves the two checks are genuinely distinct sites, which is what makes the
        positive test's `_presubmit` assertion meaningful rather than incidental."""
        ctx = wired_system
        broker_calls = _spy_broker(ctx)
        ctx.kill_switch.soft_kill(reason="q9_batch2_pre_place", triggered_by="q9_batch2_test")
        assert ctx.kill_switch.is_active("entry")

        with pytest.raises(OrderRejectedError) as ei:
            _reserve_and_place(ctx, "q9_ks_early")

        msg = str(ei.value)
        assert "kill_switch_active_last_mile" in msg
        assert "presubmit" not in msg, (
            f"expected OP-LM1 (order_placer.py:1004) to catch a kill armed BEFORE place(), "
            f"but the A-3 presubmit check reported it instead: {msg!r}"
        )
        assert broker_calls == [], f"order reached the broker despite an active kill: {broker_calls}"
