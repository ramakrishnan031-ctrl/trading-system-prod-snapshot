"""
tests/unit/test_ledger2d_co_bracket.py — LEDGER #2d.

A CO position cannot be closed by a reverse order: Zerodha rejects it and
auto-squares at 15:20 with a Rs50+GST penalty (Audit 3.1). The only correct
gesture is cancelling the CO bracket. `eod_squareoff` already did this; the
HARD_KILL flatten did not, at any of its three sites.

⛔ WHY THESE TESTS ARE BY CONSTRUCTION AND NOT A PAPER DRILL (practices §V1):
paper nets by SYMBOL while live Kite nets per (SYMBOL, PRODUCT), so a paper
drill of ANY product-semantics change is vacuously green. CO is also doubly
dormant (`force_intraday_only` coerces it away), so there is no live path to
observe either. Every assertion below is therefore driven through the REAL
`_exit_all_trades_indestructible` with a broker-boundary simulator.

Coverage maps to the settled rulings:
  R-a  gate on product=='CO', then CONFIRM variety=='co'; divergence => CRITICAL
       refusal, NEVER a fallthrough to a reverse order.
  R-b  a CO trade is NOT added to handled_symbols — a duplicate CRITICAL (noise)
       is preferred over a same-symbol MIS row left unflattened (money path).
  R-c  the branch sits ABOVE determine_close_direction, so #2e is avoided by
       construction; the resting-TGT cancel is REORDERED, not skipped.
  D1   Site B (broker sweep) REFUSES and escalates — it has no local row, so no
       bracket id, so neither available action is correct.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from broker.position_helpers import cancel_co_bracket
from capital.kill_switch import KillSwitch
from core.events import EventBus
from core.state_store import StateStore

_IST = timezone(timedelta(hours=5, minutes=30))
_NOW = "2026-08-04T10:00:00+05:30"


# ─────────────────────────────────────────────────────────────────────────────
# The seam itself
# ─────────────────────────────────────────────────────────────────────────────

class _Recorder:
    def __init__(self, result=None, raises=None):
        self.calls = []
        self._result = result
        self._raises = raises

    def cancel_order(self, oid, variety=None, **kw):
        self.calls.append((oid, variety))
        if self._raises is not None:
            raise self._raises
        return self._result


class TestCancelCoBracketSeam:
    def test_uses_co_variety(self):
        rec = _Recorder(result=SimpleNamespace(success=True, reason=""))
        cancel_co_bracket(rec, "brk_1")
        assert rec.calls == [("brk_1", "co")], (
            "Audit 3.1: the bracket must be cancelled with variety='co'; the "
            "default 'regular' is the wrong variety for a CO bracket."
        )

    def test_success_returns_ok(self):
        rec = _Recorder(result=SimpleNamespace(success=True, reason=""))
        assert cancel_co_bracket(rec, "brk_1") == (True, "")

    def test_rejection_carries_the_broker_reason(self):
        rec = _Recorder(result=SimpleNamespace(success=False, reason="order not found"))
        assert cancel_co_bracket(rec, "brk_1") == (False, "order not found")

    def test_rejection_without_reason_falls_back(self):
        rec = _Recorder(result=SimpleNamespace(success=False, reason=""))
        assert cancel_co_bracket(rec, "brk_1") == (False, "rejected")

    def test_does_not_swallow_adapter_exceptions(self):
        """⭐ Pins a DELIBERATE decision that reads like a bug.

        The #2d design record's contract says the helper "neither logs nor
        raises". Taken literally that would mean catching — but today a raising
        cancel_order propagates out of eod_squareoff's CO branch into its
        generic handler (`EOD exit unexpected error` + _mark_exit_failed). If
        this helper caught it and returned (False, reason), EOD would instead
        emit CO_SQUAREOFF_CANCEL_REJECTED — a different CRITICAL and a different
        grep sentinel, i.e. exactly the altered EOD behaviour the card's STOP
        condition forbids. The boundary rule wins over the literal wording.

        If someone "fixes" the helper to catch, this test goes red and explains
        why it must not.
        """
        rec = _Recorder(raises=RuntimeError("broker down"))
        with pytest.raises(RuntimeError, match="broker down"):
            cancel_co_bracket(rec, "brk_1")


# ─────────────────────────────────────────────────────────────────────────────
# HARD_KILL flatten — shared fixture
# ─────────────────────────────────────────────────────────────────────────────

class _CoAdapter:
    """Broker-boundary simulator. Records cancels and places IN ORDER so the
    R-c reordering can be asserted rather than assumed."""

    def __init__(self, positions=None, cancel_ok=True, cancel_reason=""):
        self.positions = positions or {}      # symbol -> (signed_qty, product)
        self.events = []                      # ordered
        self.placed = []
        self.cancelled = []                   # plain (regular-variety) cancels
        self.co_cancels = []                  # (oid, variety) bracket cancels
        self._cancel_ok = cancel_ok
        self._cancel_reason = cancel_reason

    def get_positions(self):
        return [SimpleNamespace(symbol=s, qty=q, product=p)
                for s, (q, p) in self.positions.items() if q != 0]

    def get_quote_raw(self, instruments):
        return {k: {"last_price": 100.0} for k in instruments}

    def cancel_order(self, oid, variety=None, **kw):
        if variety == "co":
            self.co_cancels.append((oid, variety))
            self.events.append(("co_cancel", oid))
            return SimpleNamespace(success=self._cancel_ok, reason=self._cancel_reason)
        self.cancelled.append(oid)
        self.events.append(("cancel", oid))
        return SimpleNamespace(success=True, reason="")

    def place_order(self, symbol, side, qty, order_type, price, intent, tag=None, **kw):
        rec = {"symbol": symbol, "side": side, "qty": qty, "intent": intent}
        self.placed.append(rec)
        self.events.append(("place", rec))
        return SimpleNamespace(
            internal_order_id=f"int_{len(self.placed)}",
            broker_order_id=f"brk_{len(self.placed)}",
            symbol=symbol, side=side, qty=qty, price=price,
            order_type=order_type, product="MIS", status="SUBMITTED",
            ts=datetime.now(),
        )


def _seed(store, *, product="CO", variety="co", entry_oid="ENTRY_CO",
          protocol="CO_PLUS_TGT", with_tgt=True):
    """One OPEN long whose ENTRY leg carries `product`/`variety`."""
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, symbol, scanner, strategy, triggered_at, "
            "received_at, expires_at, status, fingerprint, fingerprint_date) VALUES "
            "('sig_co', 'ONGC', 'gap_go_long', 'gap_go_long', ?, ?, ?, 'PROCESSING', "
            "'fp_co', ?)",
            (_NOW, _NOW, _NOW, _NOW[:10]),
        )
        cur.execute(
            "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, "
            "qty_planned, qty_filled, entry_target_price, entry_actual_price, sl_initial, "
            "tgt_initial, margin_reserved, risk_amount, created_at, entry_time, "
            "order_protocol, status, updated_at) VALUES ('T_CO', 'sig_co', 'ONGC', 'LONG', "
            "'gap_go_long', 10, 10, 100.0, 100.0, 98.0, 104.0, 1000.0, 200.0, ?, ?, ?, "
            "'OPEN', ?)",
            (_NOW, _NOW, protocol, _NOW),
        )
        if entry_oid is not None:
            cur.execute(
                "INSERT INTO orders (order_id, trade_id, leg, transaction_type, order_type, "
                "product, variety, qty_requested, status, placed_at, updated_at) VALUES "
                "(?, 'T_CO', 'ENTRY', 'BUY', 'SL', ?, ?, 10, 'COMPLETE', ?, ?)",
                (entry_oid, product, variety, _NOW, _NOW),
            )
        if with_tgt:
            cur.execute(
                "INSERT INTO orders (order_id, trade_id, leg, transaction_type, order_type, "
                "product, variety, qty_requested, status, placed_at, updated_at) VALUES "
                "('TGT_CO', 'T_CO', 'TGT', 'SELL', 'LIMIT', ?, 'regular', 10, 'OPEN', ?, ?)",
                (product, _NOW, _NOW),
            )


def _make_ks(tmp_path, adapter, monkeypatch, name="co", notifier=None):
    clock = [datetime(2026, 8, 4, 10, 0, 0, tzinfo=_IST)]
    monkeypatch.setattr("capital.kill_switch.now_ist", lambda: clock[0])
    monkeypatch.setattr("capital.kill_switch._HARD_KILL_MAX_RETRY_HOURS", 0.01)
    monkeypatch.setattr(
        time, "sleep",
        lambda d: clock.__setitem__(0, clock[0] + timedelta(seconds=d)),
    )
    store = StateStore(str(tmp_path / f"{name}.db"))
    return store, KillSwitch(
        state_store=store, bus=EventBus(), logger=logging.getLogger(f"t_{name}"),
        adapter=adapter, enable_auto_trip=False, notifier=notifier,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Site A — the local pass
# ─────────────────────────────────────────────────────────────────────────────

class TestSiteALocalPass:
    def test_co_is_cancelled_as_a_bracket_and_never_reversed(self, tmp_path, monkeypatch):
        adapter = _CoAdapter()
        store, ks = _make_ks(tmp_path, adapter, monkeypatch)
        _seed(store)
        report = ks._exit_all_trades_indestructible()
        store.close()

        assert adapter.co_cancels == [("ENTRY_CO", "co")], (
            "the CO bracket must be cancelled with variety='co'"
        )
        assert adapter.placed == [], (
            "Audit 3.1: a CO position must NEVER receive a reverse order — the "
            "broker rejects it and auto-squares with a penalty."
        )
        assert report.failed == []

    def test_resting_tgt_is_cancelled_before_the_bracket(self, tmp_path, monkeypatch):
        """R-c: 'reordered, not skipped'. A CO_PLUS_TGT trade carries a separate
        regular-variety TGT. It must still be cancelled, and BEFORE the bracket
        cancel closes the position — otherwise a live TGT is left resting and can
        fill into a naked reverse (FIX-190 Bug E's invariant)."""
        adapter = _CoAdapter()
        store, ks = _make_ks(tmp_path, adapter, monkeypatch)
        _seed(store)
        ks._exit_all_trades_indestructible()
        store.close()

        assert "TGT_CO" in adapter.cancelled, (
            "the resting TGT cancel was SKIPPED for the CO path; the ruling is "
            "REORDERED, not skipped."
        )
        kinds = [k for k, _ in adapter.events]
        assert kinds.index("cancel") < kinds.index("co_cancel"), (
            "the resting TGT must be cancelled BEFORE the bracket cancel closes "
            "the position, or it survives as an orphan that can re-open one."
        )

    def test_variety_divergence_refuses_and_does_not_reverse(self, tmp_path, monkeypatch):
        """R-a: product='CO' but variety!='co' means there is NO bracket to
        cancel. It must CRITICAL and refuse — never fall through to a reverse."""
        notifier = MagicMock()
        adapter = _CoAdapter()
        store, ks = _make_ks(tmp_path, adapter, monkeypatch, notifier=notifier)
        _seed(store, product="CO", variety="regular")
        report = ks._exit_all_trades_indestructible()
        store.close()

        assert adapter.co_cancels == [], "must not cancel a bracket that does not exist"
        assert adapter.placed == [], "⛔ must NOT fall through to a reverse order"
        assert report.failed == ["T_CO"], (
            "a refused CO position may still be LIVE — it must be reported "
            "failed, never counted as succeeded."
        )
        # AR8 calls this a CRITICAL/alert, and per expected_alarms only a
        # CRITICAL leaves a durable trace — a bare log line would not reach the
        # operator, and this leaves a live intraday position.
        assert notifier.send.call_count == 1
        kwargs = notifier.send.call_args.kwargs
        assert kwargs["severity"] == "CRITICAL"
        assert "KS_CO_VARIETY_DIVERGENCE" in kwargs["body"]

    def test_missing_broker_id_refuses(self, tmp_path, monkeypatch):
        """A CO entry row persisted with an EMPTY broker id — the shape
        order_protocol_co raises OrderRejectedError for. There is nothing to
        cancel and a reverse is rejected, so it must refuse rather than guess.

        ⚠️ NOT the same as having no ENTRY row at all: then the open-trades
        subquery yields NULL, `raw_product` is empty, and the row correctly takes
        the UNKNOWN-PRODUCT flatten path instead — it never reaches this branch.
        """
        adapter = _CoAdapter()
        store, ks = _make_ks(tmp_path, adapter, monkeypatch)
        _seed(store, entry_oid="")
        report = ks._exit_all_trades_indestructible()
        store.close()

        assert adapter.co_cancels == []
        assert adapter.placed == []
        assert report.failed == ["T_CO"]

    def test_co_does_not_join_handled_symbols(self, tmp_path, monkeypatch):
        """R-b: a same-symbol MIS broker row must STILL be swept. Kite positions()
        is per-product, so suppressing the sweep for this symbol would leave a
        live intraday position unflattened — a money-path failure, versus the
        duplicate CRITICAL (noise) that not-adding costs."""
        adapter = _CoAdapter(positions={"ONGC": (10, "MIS")})
        store, ks = _make_ks(tmp_path, adapter, monkeypatch)
        _seed(store)
        ks._exit_all_trades_indestructible()
        store.close()

        assert adapter.co_cancels == [("ENTRY_CO", "co")]
        assert [p["symbol"] for p in adapter.placed] == ["ONGC"], (
            "R-b: the same-symbol MIS broker row must still be flattened by the "
            "sweep; adding the CO trade to handled_symbols would hide it."
        )
        assert adapter.placed[0]["intent"] == "INTRADAY"

    def test_branch_sits_above_determine_close_direction(self, tmp_path, monkeypatch):
        """R-c, structurally: determine_close_direction reads broker_net_qty,
        which is #2e's territory. Branching above it avoids #2e BY CONSTRUCTION
        rather than by discipline."""
        called = []
        monkeypatch.setattr(
            "capital.kill_switch.determine_close_direction",
            lambda *a, **k: called.append(a) or ("SELL", 10),
        )
        adapter = _CoAdapter()
        store, ks = _make_ks(tmp_path, adapter, monkeypatch)
        _seed(store)
        ks._exit_all_trades_indestructible()
        store.close()

        assert called == [], (
            "the CO branch must return before determine_close_direction is "
            "reached, or the code has crossed into #2e's territory."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Site B — the broker sweep
# ─────────────────────────────────────────────────────────────────────────────

class TestSiteBBrokerSweep:
    def test_orphan_co_is_refused_not_reversed(self, tmp_path, monkeypatch):
        """D1: Site B iterates BROKER positions and exists precisely for rows
        with no local trade — so there is no bracket id to cancel, and a reverse
        order would be rejected. Both actions are wrong; it must refuse loudly.
        'Mirror eod_squareoff at all three sites' was never achievable here."""
        notifier = MagicMock()
        adapter = _CoAdapter(positions={"SAIL": (25, "CO")})
        store, ks = _make_ks(tmp_path, adapter, monkeypatch, notifier=notifier)
        report = ks._exit_all_trades_indestructible()
        store.close()

        assert adapter.placed == [], (
            "⛔ an orphan CO position must NOT be reversed (Audit 3.1)"
        )
        assert adapter.co_cancels == [], "no local row means no bracket id to cancel"
        assert report.failed == ["SAIL"], (
            "the refusal must escalate — a live CO position the kill could not "
            "close is not a success."
        )
        assert notifier.send.call_count == 1
        body = notifier.send.call_args.kwargs["body"]
        assert "KS_CO_SWEEP_REFUSED" in body
        assert "IN FLIGHT" in body, (
            "R-b obligation 1: the refusal must say a bracket cancel may already "
            "be in flight from the local pass, so the expected duplicate INFORMS "
            "rather than reading as a second, unrelated failure."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Site C — the retry loop
# ─────────────────────────────────────────────────────────────────────────────

class TestSiteCRetryLoop:
    def test_retry_is_a_cancel_never_a_reverse(self, tmp_path, monkeypatch):
        """A failed bracket cancel must be retried AS A CANCEL. Re-firing it is
        the only correct retry; a reverse order would be rejected by the broker."""
        adapter = _CoAdapter(cancel_ok=False, cancel_reason="broker busy")
        store, ks = _make_ks(tmp_path, adapter, monkeypatch)
        _seed(store)
        report = ks._exit_all_trades_indestructible()
        store.close()

        assert len(adapter.co_cancels) > 1, (
            "the failed bracket cancel must be RETRIED as a cancel"
        )
        assert all(v == "co" for _, v in adapter.co_cancels)
        assert adapter.placed == [], (
            "⛔ the retry must never substitute a reverse order for a CO position"
        )
        assert report.failed == ["T_CO"]


# ─────────────────────────────────────────────────────────────────────────────
# Accounting
# ─────────────────────────────────────────────────────────────────────────────

class TestRefusalAccounting:
    def test_refused_co_is_not_counted_as_succeeded(self, tmp_path, monkeypatch):
        """`succeeded = attempted - len(failed)`. A refused CO that never entered
        the retry queue would otherwise drain the loop and be silently counted as
        closed — reporting a HARD_KILL as fully successful while an intraday
        position is still live at the broker."""
        adapter = _CoAdapter()
        store, ks = _make_ks(tmp_path, adapter, monkeypatch)
        _seed(store, product="CO", variety="regular")
        report = ks._exit_all_trades_indestructible()
        store.close()

        assert report.attempted == 1
        assert report.succeeded == 0, (
            "a refused CO position must not be counted as a successful exit"
        )
        assert report.failed == ["T_CO"]
