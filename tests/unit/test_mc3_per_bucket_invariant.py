"""tests/unit/test_mc3_per_bucket_invariant.py — Wave-6, M-C3 regression.

M-C3 (docs/audit/full_system_audit_04july2026.md): the capital invariant's
per-bucket guard (the pre-audit "H-1" block in fund_manager._check_invariant) fired
ONLY when a bucket's AVAILABLE went negative. So a wrong-bucket release (or any
future bug) that drove a bucket's USED or RESERVED negative while its avail stayed
>= 0 balanced the global books and passed SILENTLY — the per-bucket no-borrow
guarantee drifted undetected.

Fix (detection only): extend the per-bucket trigger to a negative per-bucket USED or
RESERVED too (both buckets), reusing the SAME assert_capital_invariant ->
CapitalInvariantViolation -> hard_kill path. NON-NEGATIVITY only — NOT a per-bucket
equality check (PnL legitimately shifts the per-bucket split; an equality check
would false-fire). The guard invokes assert_capital_invariant only when a partition
is already negative, so its INV6 field-guard raises BEFORE the equality check is
reached — a legitimate PnL-shifted split never trips it.

RED→GREEN: T1/T2/T3 raise post-fix; against the pre-fix (git-stash the fund_manager
change) they are SILENT. T4 is the crux — legitimate states must NEVER raise (a
false CapitalInvariantViolation -> an unnecessary hard_kill).

Components — REAL: StateStore (real schema + fm_ledger), FundManager (real invariant
/ reserve / commit_to_used / release_used / rehydrate). SIMULATED: nothing in the
capital path (the corrupt-state tests set the in-memory partitions directly to model
a per-bucket borrow with the GLOBAL books still balanced).
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from capital.fund_manager import FundManager, required_margin
from capital.invariant import assert_capital_invariant
from core.events import EventBus
from core.exceptions import CapitalInvariantViolation
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.order_manager import OrderManager

_SCHEMA = Path(__file__).parent.parent.parent / "core" / "schema.sql"
_LEV = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}
_BAL = 100_000.0
_INTRADAY_PCT = 0.70


def _store(tmp: Path) -> StateStore:
    return StateStore(tmp / "t.db", _SCHEMA)


def _fm(store: StateStore) -> FundManager:
    fm = FundManager(
        state_store=store, bus=EventBus(), logger=logging.getLogger("t_fm"),
        intraday_bucket_pct=_INTRADAY_PCT, positional_bucket_pct=0.30,
        daily_loss_limit_pct=0.10, leverage_map=_LEV, slm_margin_buffer_pct=0.0,
    )
    fm.initialize(_BAL)   # intraday_avail=70000, positional_avail=30000, total=100000
    return fm


def _seed_and_commit(store, fm, *, sid, symbol, qty, price, intent, product,
                     direction="LONG") -> str:
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,
               received_at,expires_at,status,fingerprint,fingerprint_date)
               VALUES (?,?,'S','vwap_bounce_long','2026-07-09T09:30:00+05:30',
               '2026-07-09T09:30:00+05:30','2026-07-09T09:35:00+05:30','TRADED',?,?)""",
            (sid, symbol, "fp_" + sid, "2026-07-09"),
        )
    res = fm.reserve(symbol=symbol, qty=qty, price=price, intent=intent, signal_id=sid)
    assert res.success, res.reason_if_failed
    fm.commit_to_used(res.reservation_id, price, qty)
    om = OrderManager(store, logging.getLogger("t_om"))
    tid = om.create_trade(
        signal_id=sid, symbol=symbol, direction=direction,
        strategy="vwap_bounce_long", sector=None, qty=qty,
        entry_target_price=price, sl_initial=price * 0.99, tgt_initial=price * 1.02,
        order_protocol="LIMIT_TRIPLE", margin_reserved=res.margin,
        risk_amount=250.0, reservation_id=res.reservation_id,
    )
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            "UPDATE trades SET status='OPEN', qty_filled=?, entry_actual_price=? "
            "WHERE trade_id=?", (qty, price, tid),
        )
        cur.execute(
            """INSERT INTO orders
                 (order_id, trade_id, leg, transaction_type, order_type, product,
                  variety, qty_requested, status, trigger_price, placed_at, updated_at)
               VALUES (?, ?, 'ENTRY', ?, 'LIMIT', ?, 'regular', ?, 'COMPLETE',
                       0.0, ?, ?)""",
            (f"ord_{tid}", tid, "BUY" if direction == "LONG" else "SELL",
             product, qty, now, now),
        )
    return tid


def _assert_buckets_non_negative(fm, tol=0.01):
    s = fm.get_snapshot()
    assert s.intraday_used >= -tol and s.intraday_reserved >= -tol
    assert s.positional_used >= -tol and s.positional_reserved >= -tol
    assert s.intraday_avail >= -tol and s.positional_avail >= -tol


# ─────────────────────────────────────────────────────────────────────────────
# T1 — per-bucket negative USED (avail >= 0, global balanced): the borrow case
# ─────────────────────────────────────────────────────────────────────────────
def test_t1_per_bucket_negative_used_raises():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store)
        # Model a wrong-bucket release: intraday committed 5000, released from
        # positional -> positional_used = -5000 while positional_avail stays >= 0.
        fm._intraday_avail = 65_000.0
        fm._intraday_used = 5_000.0
        fm._positional_avail = 35_000.0
        fm._positional_used = -5_000.0

        # RED evidence: the GLOBAL check is blind — totals balance, no negative
        # aggregate (this is exactly what the pre-M-C3 _check_invariant relied on).
        assert_capital_invariant(              # does NOT raise
            margin_available=100_000.0, margin_reserved=0.0, margin_used=0.0,
            cash_floor=100_000.0, realized_pnl_today=0.0)

        # GREEN: the M-C3 per-bucket guard surfaces the negative positional_used.
        with pytest.raises(CapitalInvariantViolation):
            fm._check_invariant("test", "t1")
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# T2 — per-bucket negative RESERVED (avail >= 0, global balanced)
# ─────────────────────────────────────────────────────────────────────────────
def test_t2_per_bucket_negative_reserved_raises():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store)
        # intraday_reserved = -5000, OFFSET by +5000 positional_reserved so the
        # GLOBAL reserved aggregate stays 0 (balanced) -> only the per-bucket guard
        # can catch it; intraday_avail stays >= 0.
        fm._intraday_avail = 75_000.0
        fm._intraday_reserved = -5_000.0
        fm._positional_avail = 25_000.0
        fm._positional_reserved = 5_000.0
        with pytest.raises(CapitalInvariantViolation):
            fm._check_invariant("test", "t2")
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# T3 — BOTH buckets are guarded (intraday USED and positional RESERVED)
# ─────────────────────────────────────────────────────────────────────────────
def test_t3_both_buckets_guarded():
    # intraday bucket: negative USED (global balanced by an offsetting positional used)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store)
        fm._intraday_avail = 75_000.0
        fm._intraday_used = -5_000.0
        fm._positional_avail = 25_000.0
        fm._positional_used = 5_000.0
        with pytest.raises(CapitalInvariantViolation):
            fm._check_invariant("test", "t3a")
        store.close()

    # positional bucket: negative RESERVED (global balanced by an offsetting intraday reserved)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store)
        fm._intraday_avail = 65_000.0
        fm._intraday_reserved = 5_000.0
        fm._positional_avail = 35_000.0
        fm._positional_reserved = -5_000.0
        with pytest.raises(CapitalInvariantViolation):
            fm._check_invariant("test", "t3b")
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# T4 — NO FALSE POSITIVE (the crux): legitimate states must NEVER raise
# ─────────────────────────────────────────────────────────────────────────────
def test_t4_no_false_positive_on_legitimate_states():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store)
        # Each of these calls _check_invariant INTERNALLY; a false positive would
        # raise CapitalInvariantViolation here and fail the test.

        # normal reserve -> commit (intraday)
        tid_a = _seed_and_commit(store, fm, sid="sig_a", symbol="RELIANCE",
                                 qty=10, price=2500.0, intent="INTRADAY", product="MIS")
        # multi-bucket: a concurrent positional (delivery) position
        tid_b = _seed_and_commit(store, fm, sid="sig_b", symbol="TATASTEEL",
                                 qty=5, price=2000.0, intent="DELIVERY", product="CNC")
        _assert_buckets_non_negative(fm)

        # profitable close of A (PnL shifts intraday avail up, still >= 0)
        fm.release_used(symbol="RELIANCE", exit_price=2600.0, exit_qty=10,
                        intent="INTRADAY", entry_price=2500.0, direction="LONG",
                        costs=0.0, trade_id=tid_a)
        _assert_buckets_non_negative(fm)

        # losing close of B
        fm.release_used(symbol="TATASTEEL", exit_price=1900.0, exit_qty=5,
                        intent="DELIVERY", entry_price=2000.0, direction="LONG",
                        costs=0.0, trade_id=tid_b)
        _assert_buckets_non_negative(fm)

        # M-O2/M-C7 partial close: reserve+commit C, release part of it
        tid_c = _seed_and_commit(store, fm, sid="sig_c", symbol="INFY",
                                 qty=10, price=1500.0, intent="INTRADAY", product="MIS")
        fm.release_used(symbol="INFY", exit_price=1500.0, exit_qty=4,
                        intent="INTRADAY", entry_price=1500.0, direction="LONG",
                        costs=0.0, trade_id=tid_c)   # partial (M-O2 shape)
        _assert_buckets_non_negative(fm)

        # warm-restart (M-C1 rehydrate) with C still open — must not false-fire
        fm2 = _fm(store)
        fm2.rehydrate_from_open_trades()             # calls _check_invariant("rehydrate")
        _assert_buckets_non_negative(fm2)
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# T5 — tolerance: within-tol float residual does NOT fire; beyond-tol does
# (_INVARIANT_TOLERANCE = 1.0 rupee)
# ─────────────────────────────────────────────────────────────────────────────
def test_t5_tolerance_boundary():
    # within tolerance (|residual| < 1.0) -> NO raise (global stays balanced)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store)
        fm._intraday_used = 0.5
        fm._positional_used = -0.5            # global used sums to 0 (balanced)
        fm._check_invariant("test", "t5_within")   # must NOT raise
        store.close()

    # beyond tolerance (|residual| > 1.0) -> raises (per-bucket, global balanced)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store)
        fm._intraday_used = 2.0
        fm._positional_used = -2.0
        with pytest.raises(CapitalInvariantViolation):
            fm._check_invariant("test", "t5_beyond")
        store.close()
