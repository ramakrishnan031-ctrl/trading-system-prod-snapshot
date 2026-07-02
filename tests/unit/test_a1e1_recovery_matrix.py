"""A-1/E-1 naked-orphan fix — Phase E: capital-correctness proof + full recovery
matrix.

Two layers:

  1. CAPITAL-CORRECTNESS PROOF (TestCapitalCorrectness) — the six cases from the
     approved corrected mechanism, exercised against a REAL FundManager + a REAL
     StateStore. The CRASH cases use a second FundManager rehydrated on the same
     store (rehydrate_from_open_trades skips PENDING, so its reservation is truly
     absent from memory — exactly the crash reality). Every case asserts the
     three-balance invariant holds and the buckets match a normal reserve/commit/
     release lifecycle.

  2. RECOVERY MATRIX (TestRecoveryMatrix) — the design's 12-row fail-on-old /
     pass-on-fix table driven through the reconciler's unified recovery prepass
     (_recover_in_flight_entries -> _adopt_or_fail), for BOTH the timeout and
     crash feeds, plus paper-parity via the real ZerodhaAdapter (paper) oracle.

Old behaviour these lock against: a timed-out/crashed entry was marked FAILED +
capital released WITHOUT confirming broker absence, then disowned as human when it
filled -> a naked, unmonitored position.
"""
from __future__ import annotations

import logging
import tempfile
from collections import namedtuple
from pathlib import Path
from typing import Optional

from capital.fund_manager import FundManager, required_margin
from core.events import EventBus
from core.ids import truncate_tag_for_broker
from core.state_store import StateStore
from orders.order_manager import OrderManager
from orders.order_reconciler import OrderReconciler, correlate_entry_by_tag

_SCHEMA = Path(__file__).parent.parent.parent / "core" / "schema.sql"
_LEV = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}
_BAL = 100_000.0
_INTRADAY_PCT = 0.70
# margin for qty=10 @ 2500 INTRADAY lev 5 = 10*2500/5 = 5000
_QTY, _PRICE = 10, 2500.0
_MARGIN = required_margin(_QTY, _PRICE, "INTRADAY", _LEV)   # 5000.0
_INTRADAY_TOTAL = _BAL * _INTRADAY_PCT                      # 70000.0


# ─────────────────────────────────────────────────────────────────────────────
# Shared builders
# ─────────────────────────────────────────────────────────────────────────────
def _store(tmp: Path) -> StateStore:
    return StateStore(tmp / "t.db", _SCHEMA)


def _make_fm(store: StateStore) -> FundManager:
    return FundManager(
        state_store=store, bus=EventBus(), logger=logging.getLogger("t_fm"),
        intraday_bucket_pct=_INTRADAY_PCT, positional_bucket_pct=0.30,
        daily_loss_limit_pct=0.10, leverage_map=_LEV,
        slm_margin_buffer_pct=0.0,
    )


def _initialized_fm(store: StateStore) -> FundManager:
    fm = _make_fm(store)
    fm.initialize(_BAL)
    return fm


def _seed_signal(store: StateStore, sid: str = "sig_1", symbol: str = "RELIANCE") -> None:
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,
               received_at,expires_at,status,fingerprint,fingerprint_date)
               VALUES (?,?,'S','vwap_bounce_long','2026-07-02T09:30:00+05:30',
               '2026-07-02T09:30:00+05:30','2026-07-02T09:35:00+05:30','TRADED',?,?)""",
            (sid, symbol, "fp_" + sid, "2026-07-02"),
        )


def _reserve_and_create(store, fm, *, status: str, symbol="RELIANCE",
                        sid="sig_1") -> tuple[str, str]:
    """Reserve intraday capital and create a recovery-state trade wired to it.
    Returns (trade_id, reservation_id)."""
    _seed_signal(store, sid, symbol)
    res = fm.reserve(symbol=symbol, qty=_QTY, price=_PRICE, intent="INTRADAY",
                     signal_id=sid)
    assert res.success, res.reason_if_failed
    om = OrderManager(store, logging.getLogger("t_om"))
    tid = om.create_trade(
        signal_id=sid, symbol=symbol, direction="LONG",
        strategy="vwap_bounce_long", sector=None, qty=_QTY,
        entry_target_price=_PRICE, sl_initial=2475.0, tgt_initial=2550.0,
        order_protocol="LIMIT_TRIPLE", margin_reserved=res.margin,
        risk_amount=250.0, reservation_id=res.reservation_id,
    )
    with store.transaction() as cur:
        # older created_at so G5b's 10s settling window never defers the recovery SL
        cur.execute("UPDATE trades SET status=?, created_at=? WHERE trade_id=?",
                    (status, "2026-07-02T09:00:00+05:30", tid))
    return tid, res.reservation_id


def _crash_fm(store) -> FundManager:
    """A fresh FundManager on the SAME store, rehydrated — its in-memory
    reservation for any PENDING trade is ABSENT (rehydrate replays OPEN/PARTIAL
    only). This IS the crash reality."""
    fm2 = _initialized_fm_noseed(store)
    fm2.rehydrate_from_open_trades()
    return fm2


def _initialized_fm_noseed(store) -> FundManager:
    fm = _make_fm(store)
    fm.initialize(_BAL)
    return fm


def _ledger_types(store, rid: str) -> list[str]:
    return [r["entry_type"] for r in store.fetch_all(
        "SELECT entry_type FROM fm_ledger WHERE reservation_id=? ORDER BY ledger_id",
        (rid,))]


def _trade_row(store, tid):
    return store.fetch_one("SELECT * FROM trades WHERE trade_id=?", (tid,))


# ─────────────────────────────────────────────────────────────────────────────
# 1. CAPITAL-CORRECTNESS PROOF (six cases)
# ─────────────────────────────────────────────────────────────────────────────
class TestCapitalCorrectness:

    def test_timeout_complete_reuses_existing_reservation(self):
        """timeout-COMPLETE: reservation present -> commit directly, NO
        reconstruction (exactly ONE RESERVE row), reserved->used."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            assert rid in fm._reservations                      # in memory (same process)
            cr = fm.commit_adopted_entry(_trade_row(store, tid), _PRICE, _QTY)
            assert cr is not None
            snap = fm.get_snapshot()
            assert abs(snap.intraday_used - _MARGIN) < 1e-6
            assert abs(snap.intraday_reserved) < 1e-6
            assert abs(snap.intraday_avail - (_INTRADAY_TOTAL - _MARGIN)) < 1e-6
            # exactly one RESERVE (no reconstruction) + one COMMIT
            assert _ledger_types(store, rid) == ["RESERVE", "COMMIT"]

    def test_timeout_complete_commit_is_exactly_once(self):
        """A second commit_adopted_entry is a no-op (exactly-one-commit guard)."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            fm.commit_adopted_entry(_trade_row(store, tid), _PRICE, _QTY)
            again = fm.commit_adopted_entry(_trade_row(store, tid), _PRICE, _QTY)
            assert again is None                                # no second commit
            assert _ledger_types(store, rid).count("COMMIT") == 1
            assert abs(fm.get_snapshot().intraday_used - _MARGIN) < 1e-6

    def test_timeout_open_retains_reservation(self):
        """timeout-OPEN: restore is a no-op (present); a later normal commit works."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            assert fm.restore_adopted_reservation(_trade_row(store, tid)) is True
            # unchanged: still reserved, no extra ledger row
            assert abs(fm.get_snapshot().intraday_reserved - _MARGIN) < 1e-6
            assert _ledger_types(store, rid) == ["RESERVE"]
            # the later real fill commits normally
            fm.commit_to_used(rid, _PRICE, _QTY)
            assert abs(fm.get_snapshot().intraday_used - _MARGIN) < 1e-6

    def test_timeout_absent_releases_live_reservation(self):
        """timeout-ABSENT: reservation present -> release normally."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            assert fm.release_adopted_reservation(_trade_row(store, tid), "absent") is True
            snap = fm.get_snapshot()
            assert abs(snap.intraday_reserved) < 1e-6
            assert abs(snap.intraday_avail - _INTRADAY_TOTAL) < 1e-6
            assert _ledger_types(store, rid) == ["RESERVE", "RELEASE"]

    def test_crash_complete_restores_reserve_then_commits(self):
        """crash-COMPLETE: reservation absent -> reconstruct from ledger, commit."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm1 = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm1, status="PENDING")
            fm2 = _crash_fm(store)                              # reservation lost
            assert rid not in fm2._reservations
            # crash rehydrate left the margin in available (PENDING skipped)
            assert abs(fm2.get_snapshot().intraday_avail - _INTRADAY_TOTAL) < 1e-6
            cr = fm2.commit_adopted_entry(_trade_row(store, tid), _PRICE, _QTY)
            assert cr is not None
            snap = fm2.get_snapshot()
            assert abs(snap.intraday_used - _MARGIN) < 1e-6
            assert abs(snap.intraday_reserved) < 1e-6
            assert abs(snap.intraday_avail - (_INTRADAY_TOTAL - _MARGIN)) < 1e-6
            # RESERVE (pre-crash) + COMMIT (recovery). No duplicate RESERVE row.
            assert _ledger_types(store, rid) == ["RESERVE", "COMMIT"]

    def test_crash_open_restores_reserve_only(self):
        """crash-OPEN: reconstruct the reserve (no commit); later fill commits."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm1 = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm1, status="PENDING")
            fm2 = _crash_fm(store)
            assert fm2.restore_adopted_reservation(_trade_row(store, tid)) is True
            snap = fm2.get_snapshot()
            assert abs(snap.intraday_reserved - _MARGIN) < 1e-6
            assert abs(snap.intraday_avail - (_INTRADAY_TOTAL - _MARGIN)) < 1e-6
            assert abs(snap.intraday_used) < 1e-6
            # no ledger duplication — reserve restored in-memory only
            assert _ledger_types(store, rid) == ["RESERVE"]
            # later real fill commits normally (reservation now present)
            fm2.commit_adopted_entry(_trade_row(store, tid), _PRICE, _QTY)
            assert abs(fm2.get_snapshot().intraday_used - _MARGIN) < 1e-6

    def test_crash_absent_closes_ledger_without_double_release(self):
        """crash-ABSENT: memory already free, ledger RESERVE closed with a
        balancing RELEASE; net memory change ZERO, no double-release."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm1 = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm1, status="PENDING")
            fm2 = _crash_fm(store)
            before = fm2.get_snapshot().intraday_avail          # already == full
            assert abs(before - _INTRADAY_TOTAL) < 1e-6
            assert fm2.release_adopted_reservation(_trade_row(store, tid), "absent") is True
            snap = fm2.get_snapshot()
            assert abs(snap.intraday_avail - _INTRADAY_TOTAL) < 1e-6   # unchanged (no double)
            assert abs(snap.intraday_reserved) < 1e-6
            # ledger balanced: RESERVE + RELEASE
            assert _ledger_types(store, rid) == ["RESERVE", "RELEASE"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. RECOVERY MATRIX (reconciler prepass)
# ─────────────────────────────────────────────────────────────────────────────
_Pos = namedtuple("_Pos", "symbol qty avg_price")
_Placed = namedtuple("_Placed", "broker_order_id product variety")


class _FakeAdapter:
    def __init__(self, all_orders, positions=None):
        self._all = all_orders
        self._positions = positions or []
        self.placed = []

    def get_all_orders(self):
        return list(self._all)

    def get_positions(self):
        return list(self._positions)

    def get_open_orders(self):
        return []

    def get_order_history(self, oid):
        return []

    def get_quote(self, symbols):
        return {}

    def place_order(self, **kw):
        self.placed.append(kw)
        return _Placed(f"bo_{len(self.placed)}", kw.get("intent", "MIS"), "regular")


class _FakeOP:
    """Minimal OrderPlacer stand-in for the timeout feed."""
    def __init__(self, queue: Optional[dict] = None):
        self._q = queue or {}

    def get_timeout_recovery_trades(self):
        return list(self._q.keys())

    def remove_from_timeout_recovery(self, tid):
        return self._q.pop(tid, None)


class _FakeKS:
    def __init__(self, hard=False):
        self._hard = hard

    def is_active(self, intent="entry"):
        return self._hard if intent == "exit" else self._hard

    def soft_kill(self, **kw):
        pass

    def hard_kill(self, **kw):
        pass


def _cfg():
    from unittest.mock import Mock
    return Mock(poll_interval_sec=15, capital_drift_tolerance=100.0,
                stuck_exiting_timeout_minutes=30, human_order_margin_tolerance=5000.0,
                capital_drift_alert_interval_sec=1800.0)


def _bo(tid, *, side="BUY", status="COMPLETE", symbol="RELIANCE", qty=_QTY,
        filled=None, avg=_PRICE, oid="bo1", product="MIS", tag=None):
    """A get_all_orders()-shaped broker order for a trade_id (tag auto-derived)."""
    return {
        "order_id": oid,
        "tag": tag if tag is not None else truncate_tag_for_broker(tid),
        "status": status, "transaction_type": side, "symbol": symbol,
        "quantity": qty, "filled_quantity": qty if filled is None else filled,
        "average_price": avg, "trigger_price": 0.0, "product": product,
    }


def _make_reconciler(store, fm, adapter, op, *, hard=False, notifier=None):
    from unittest.mock import Mock
    return OrderReconciler(
        state_store=store, adapter=adapter, fund_manager=fm,
        kill_switch=_FakeKS(hard=hard), notifier=notifier or Mock(),
        bus=EventBus(), logger=logging.getLogger("t_recon"), cfg=_cfg(),
        quote_fn=adapter.get_quote, broker_orders_fn=adapter.get_open_orders,
        mode="PAPER", order_placer=op,
    )


class TestRecoveryMatrix:

    # Row 1: timeout, entry OPEN at broker -> defer (not FAILED), reserve retained.
    def test_row01_timeout_open_defers(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            op = _FakeOP({tid: {"reservation_id": rid, "symbol": "RELIANCE"}})
            adapter = _FakeAdapter([_bo(tid, status="OPEN")])
            rc = _make_reconciler(store, fm, adapter, op)
            acts = rc._recover_in_flight_entries()
            assert _trade_row(store, tid)["status"] == "UNKNOWN_IN_FLIGHT"   # NOT failed
            assert tid in op.get_timeout_recovery_trades()                   # still tracked
            assert any(a.check_name == "RECOVERY_ENTRY_RESTING" for a in acts)
            assert abs(fm.get_snapshot().intraday_reserved - _MARGIN) < 1e-6

    # Row 2: timeout, entry FILLED -> adopted + OPEN + committed (was naked).
    def test_row02_timeout_filled_adopted(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            op = _FakeOP({tid: {"reservation_id": rid, "symbol": "RELIANCE"}})
            adapter = _FakeAdapter([_bo(tid, status="COMPLETE")])
            rc = _make_reconciler(store, fm, adapter, op)
            acts = rc._recover_in_flight_entries()
            row = _trade_row(store, tid)
            assert row["status"] == "OPEN"
            assert row["qty_filled"] == _QTY
            assert row["recovered_flag"] == 1
            assert abs(fm.get_snapshot().intraday_used - _MARGIN) < 1e-6
            assert tid not in op.get_timeout_recovery_trades()
            # ENTRY row backfilled + marked COMPLETE, and TGT retry flagged
            orders = {o["leg"]: o for o in store.get_orders_for_trade(tid)}
            assert "ENTRY" in orders and orders["ENTRY"]["status"] == "COMPLETE"
            assert _trade_row(store, tid)["needs_tgt_retry"] == 1
            assert any(a.check_name == "RECOVERY_ADOPTED_FILLED" for a in acts)

    # Row 3: crash, entry FILLED, restart -> adopted + protected + capital restored.
    def test_row03_crash_filled_adopted(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm1 = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm1, status="PENDING")
            fm2 = _crash_fm(store)                              # reservation lost
            op = _FakeOP({})                                    # crash: not in timeout queue
            adapter = _FakeAdapter([_bo(tid, status="COMPLETE")])
            rc = _make_reconciler(store, fm2, adapter, op)
            rc._recover_in_flight_entries()
            assert _trade_row(store, tid)["status"] == "OPEN"
            snap = fm2.get_snapshot()
            assert abs(snap.intraday_used - _MARGIN) < 1e-6    # reserve reconstructed + committed
            assert _ledger_types(store, rid) == ["RESERVE", "COMMIT"]

    # Row 4: entry never reached broker -> FAILED + release AFTER poll budget.
    def test_row04_absent_failed_only_after_budget(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            op = _FakeOP({tid: {"reservation_id": rid, "symbol": "RELIANCE"}})
            adapter = _FakeAdapter([])                          # broker reachable, no order
            rc = _make_reconciler(store, fm, adapter, op)
            # polls 1,2 -> defer (NOT failed, NOT released)
            rc._recover_in_flight_entries()
            rc._recover_in_flight_entries()
            assert _trade_row(store, tid)["status"] == "UNKNOWN_IN_FLIGHT"
            assert abs(fm.get_snapshot().intraday_reserved - _MARGIN) < 1e-6
            # poll 3 -> FAILED + release
            rc._recover_in_flight_entries()
            assert _trade_row(store, tid)["status"] == "FAILED"
            assert abs(fm.get_snapshot().intraday_reserved) < 1e-6
            assert abs(fm.get_snapshot().intraday_avail - _INTRADAY_TOTAL) < 1e-6

    # Row 5: genuine human order (tag matches no local trade) -> not adopted.
    def test_row05_human_order_not_adopted(self):
        # No recovery trade at all; a tagged order that resolves to nothing must
        # never enter the recovery path (correlate is only ever called for our
        # own recovery trades). Prove correlate itself won't match a foreign tag.
        kind, _ = correlate_entry_by_tag(
            "trd_" + "a" * 32, "LONG", "RELIANCE", _QTY,
            [_bo("trd_" + "b" * 32, status="COMPLETE")],
        )
        assert kind == "ABSENT"

    # Row 6: HARD_KILL + matched filled entry -> flatten (never adopt into a kill).
    def test_row06_hard_kill_flattens(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            op = _FakeOP({tid: {"reservation_id": rid, "symbol": "RELIANCE"}})
            adapter = _FakeAdapter(
                [_bo(tid, status="COMPLETE")],
                positions=[_Pos("RELIANCE", _QTY, _PRICE)],     # position held -> flatten
            )
            rc = _make_reconciler(store, fm, adapter, op, hard=True)
            acts = rc._recover_in_flight_entries()
            assert _trade_row(store, tid)["status"] == "EXITING"     # not OPEN
            assert any(a.check_name == "RECOVERY_KILL_FLATTEN" for a in acts)
            # a flatten (SELL) order was placed, none is a protective SL/G5b
            assert any(p["side"] == "SELL" for p in adapter.placed)
            assert abs(fm.get_snapshot().intraday_used - _MARGIN) < 1e-6

    # Row 7: two cycles -> adopted exactly once (idempotent).
    def test_row07_idempotent_adoption(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            op = _FakeOP({tid: {"reservation_id": rid, "symbol": "RELIANCE"}})
            adapter = _FakeAdapter([_bo(tid, status="COMPLETE")])
            rc = _make_reconciler(store, fm, adapter, op)
            rc._recover_in_flight_entries()
            # second cycle: trade already OPEN + out of queue -> no double commit
            rc._recover_in_flight_entries()
            assert _ledger_types(store, rid).count("COMMIT") == 1
            assert abs(fm.get_snapshot().intraday_used - _MARGIN) < 1e-6

    # Row 8: broker poll fails / unreachable -> stay in recovery; no FAILED/release.
    def test_row08_broker_unreachable_defers(self):
        from core.exceptions import BrokerTimeoutError
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            op = _FakeOP({tid: {"reservation_id": rid, "symbol": "RELIANCE"}})

            class _Unreachable(_FakeAdapter):
                def get_all_orders(self):
                    raise BrokerTimeoutError("down", operation="get_all_orders")

            rc = _make_reconciler(store, fm, _Unreachable([]), op)
            acts = rc._recover_in_flight_entries()
            assert acts == []
            assert _trade_row(store, tid)["status"] == "UNKNOWN_IN_FLIGHT"
            assert abs(fm.get_snapshot().intraday_reserved - _MARGIN) < 1e-6

    # Row 9: partial-fill-then-CANCEL of entry -> adopt+protect at the filled qty
    # (fail-on-old: old marked FAILED -> the partial position was left naked).
    def test_row09_partial_then_cancel_adopted_at_filled_qty(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            op = _FakeOP({tid: {"reservation_id": rid, "symbol": "RELIANCE"}})
            # ordered 10, 6 filled then the remainder CANCELLED (terminal + partial).
            adapter = _FakeAdapter([_bo(tid, status="CANCELLED", filled=6)])
            rc = _make_reconciler(store, fm, adapter, op)
            rc._recover_in_flight_entries()
            row = _trade_row(store, tid)
            assert row["status"] == "OPEN" and row["qty_filled"] == 6
            # capital committed at the filled qty (6*2500/5 = 3000), excess returned
            assert abs(fm.get_snapshot().intraday_used - required_margin(6, _PRICE, "INTRADAY", _LEV)) < 1e-6

    # Row 9b: RESTING partial (OPEN, filled 6) -> DEFER (do not adopt a qty that
    # may still grow); reserve retained, re-correlated next cycle.
    def test_row09b_resting_partial_defers(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            op = _FakeOP({tid: {"reservation_id": rid, "symbol": "RELIANCE"}})
            adapter = _FakeAdapter([_bo(tid, status="OPEN", filled=6)])
            rc = _make_reconciler(store, fm, adapter, op)
            acts = rc._recover_in_flight_entries()
            assert _trade_row(store, tid)["status"] == "UNKNOWN_IN_FLIGHT"   # not adopted
            assert abs(fm.get_snapshot().intraday_reserved - _MARGIN) < 1e-6  # full reserve kept
            assert any(a.check_name == "RECOVERY_ENTRY_RESTING" for a in acts)

    # Row 10: tag collision (>1 match) -> no blind adopt; CRITICAL alert, defer.
    def test_row10_ambiguous_no_blind_adopt(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            op = _FakeOP({tid: {"reservation_id": rid, "symbol": "RELIANCE"}})
            # two BUY orders share tag+symbol+qty -> unnarrowable
            adapter = _FakeAdapter([
                _bo(tid, status="COMPLETE", oid="A"),
                _bo(tid, status="COMPLETE", oid="B"),
            ])
            from unittest.mock import Mock
            notifier = Mock()
            rc = _make_reconciler(store, fm, adapter, op, notifier=notifier)
            acts = rc._recover_in_flight_entries()
            assert _trade_row(store, tid)["status"] == "UNKNOWN_IN_FLIGHT"   # NOT adopted
            assert abs(fm.get_snapshot().intraday_reserved - _MARGIN) < 1e-6  # NOT released
            assert any(a.check_name == "RECOVERY_AMBIGUOUS" for a in acts)
            assert notifier.send.called

    # Row 11: PAPER PARITY — same recovery path, real adapter oracle.
    def test_row11_paper_parity_via_real_adapter(self):
        from unittest.mock import Mock
        from broker.zerodha_adapter import ZerodhaAdapter
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            # Seed the paper oracle: a COMPLETE, tagged BUY order for this trade.
            # Real adapter in paper mode — get_all_orders reads _paper_fills (the
            # oracle), proving ONE recovery path in both modes.
            adapter = ZerodhaAdapter(
                kite_client=Mock(), rate_limiter=Mock(), product_resolver=Mock(),
                cost_calculator=Mock(), state_machine=Mock(),
                logger=logging.getLogger("t_za"), paper_mode=True,
            )
            with adapter._paper_fills_lock:
                adapter._paper_fills["pbo1"] = {
                    "status": "COMPLETE", "filled_qty": _QTY, "avg_price": _PRICE,
                    "symbol": "RELIANCE", "side": "BUY", "qty": _QTY,
                    "price": _PRICE, "trigger_price": 0.0,
                    "tag": truncate_tag_for_broker(tid), "product": "MIS",
                }
            op = _FakeOP({tid: {"reservation_id": rid, "symbol": "RELIANCE"}})
            rc = _make_reconciler(store, fm, adapter, op)
            rc._recover_in_flight_entries()
            assert _trade_row(store, tid)["status"] == "OPEN"       # same path adopts
            assert abs(fm.get_snapshot().intraday_used - _MARGIN) < 1e-6

    # Row 12b (integration): adopt-filled -> G5b places the SL in the SAME cycle.
    # Proves the prepass ordering (recovery runs before the G5b loop) so an
    # adopted-OPEN trade is never left naked for a full poll interval.
    def test_row12b_adopt_then_g5b_places_sl_same_cycle(self):
        _Quote = namedtuple("_Quote", "last_price")

        class _G5bAdapter(_FakeAdapter):
            def get_quote(self, symbols):
                return {s: _Quote(2480.0) for s in symbols}   # LTP > sl 2475 -> SL SELL
            def get_margins(self):
                return {}
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            op = _FakeOP({tid: {"reservation_id": rid, "symbol": "RELIANCE"}})
            adapter = _G5bAdapter(
                [_bo(tid, status="COMPLETE")],
                positions=[_Pos("RELIANCE", _QTY, _PRICE)],   # position held (healthy + G5b)
            )
            rc = _make_reconciler(store, fm, adapter, op)
            rc.reconcile_once()                                # FULL cycle
            assert _trade_row(store, tid)["status"] == "OPEN"
            # G5b placed + persisted a recovery SL in the SAME cycle (not naked).
            sl = store.get_sl_order_for_trade(tid)
            assert sl is not None, "G5b must place the recovery SL in the adopt cycle"
            assert any(p.get("order_type") in ("SL", "MARKET") and p.get("side") == "SELL"
                       for p in adapter.placed)

    # Row 12: matched entry REJECTED at broker -> FAILED + release (safe).
    def test_row12_rejected_entry_failed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _store(Path(tmp))
            fm = _initialized_fm(store)
            tid, rid = _reserve_and_create(store, fm, status="UNKNOWN_IN_FLIGHT")
            op = _FakeOP({tid: {"reservation_id": rid, "symbol": "RELIANCE"}})
            adapter = _FakeAdapter([_bo(tid, status="REJECTED", filled=0)])
            rc = _make_reconciler(store, fm, adapter, op)
            acts = rc._recover_in_flight_entries()
            assert _trade_row(store, tid)["status"] == "FAILED"
            assert abs(fm.get_snapshot().intraday_reserved) < 1e-6
            assert abs(fm.get_snapshot().intraday_avail - _INTRADAY_TOTAL) < 1e-6
            assert any(a.check_name == "RECOVERY_ENTRY_DEAD" for a in acts)
