"""tests/unit/test_mc7_release_committed_margin.py — Wave-6, M-C7 regression.

M-C7 (docs/audit/full_system_audit_04july2026.md): FundManager.release_used
recomputed the margin to free as `required_margin(qty, price, intent,
CURRENT leverage_map)` (M2). But commit_to_used persisted the commit-time margin
(M1) in the COMMIT fm_ledger row, and rehydrate REPLAYS that same M1. So a
leverage-config edit across a restart with a carried position left a permanent
residual M1−M2 in `used` — silent, because the mis-released amount lands in avail
and the global invariant still balances.

Fix: release reverses the PERSISTED committed margin, proportional to exit_qty —
`committed_M1 × exit_qty / committed_qty` (M1 + committed_qty from the trade's
COMMIT fm_ledger row via `StateStore.get_entry_commit_margin`, threaded through the
new `release_used(trade_id=…)`). Leverage-change-invariant.

RED→GREEN is self-contained: `release_used(trade_id=None)` takes the legacy
recompute FALLBACK and reproduces the drift (used == M1−M2); `release_used(
trade_id=tid)` reverses the committed M1 (used → 0). No git-stash needed.

Components — REAL: StateStore (real schema + fm_ledger), two FundManagers on the
SAME store to simulate a restart (the 2nd with an EDITED leverage_map), real
reserve/commit_to_used/rehydrate_from_open_trades/release_used. SIMULATED: nothing
in the capital path (OrderManager writes real trade rows; no broker).
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from capital.fund_manager import FundManager, required_margin
from core.events import EventBus
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.order_manager import OrderManager

_SCHEMA = Path(__file__).parent.parent.parent / "core" / "schema.sql"
# L1 = commit-time leverage; L2 = EDITED leverage after a config change + restart.
_L1 = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}
_L2 = {"INTRADAY": 10.0, "COVER_ORDER": 6.0, "DELIVERY": 2.0, "BRACKET_ORDER": 5.0}
_BAL = 100_000.0
_INTRADAY_PCT = 0.70


def _store(tmp: Path) -> StateStore:
    return StateStore(tmp / "t.db", _SCHEMA)


def _fm(store: StateStore, leverage: dict) -> FundManager:
    fm = FundManager(
        state_store=store, bus=EventBus(), logger=logging.getLogger("t_fm"),
        intraday_bucket_pct=_INTRADAY_PCT, positional_bucket_pct=0.30,
        daily_loss_limit_pct=0.10, leverage_map=leverage, slm_margin_buffer_pct=0.0,
    )
    fm.initialize(_BAL)
    return fm


def _seed_and_commit(store, fm, *, sid, symbol, qty, price, intent, product,
                     direction="LONG") -> str:
    """reserve + commit_to_used on `fm` (margin -> `used`, RESERVE+COMMIT rows in
    fm_ledger) + an OPEN trade (reservation_id) + ENTRY order (product). Returns
    trade_id."""
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
    fm.commit_to_used(res.reservation_id, price, qty)   # reserved -> used (persists M1)
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
            "WHERE trade_id=?",
            (qty, price, tid),
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


def _restart(store, leverage) -> FundManager:
    """Simulate a process restart with an EDITED leverage_map: a fresh FundManager
    on the SAME store, rehydrated (Phase-1 replays the persisted RESERVE/COMMIT
    ledger amounts -> `used` seeded with the commit-time M1, NOT a recompute)."""
    fm2 = _fm(store, leverage)
    fm2.rehydrate_from_open_trades()
    return fm2


def _used_intraday(fm):
    return fm.get_snapshot().intraday_used


def _used_positional(fm):
    return fm.get_snapshot().positional_used


# ─────────────────────────────────────────────────────────────────────────────
# T1 — M-C7 core: leverage change + restart. GREEN = committed reversal → used→0
# ─────────────────────────────────────────────────────────────────────────────
def test_t1_green_release_reverses_committed_margin_across_leverage_change():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm1 = _fm(store, _L1)
        tid = _seed_and_commit(store, fm1, sid="sig_1", symbol="RELIANCE",
                               qty=10, price=2500.0, intent="INTRADAY", product="MIS")
        M1 = required_margin(10, 2500.0, "INTRADAY", _L1)     # 5000
        assert abs(_used_intraday(fm1) - M1) < 1e-6

        fm2 = _restart(store, _L2)                            # leverage EDITED 5 -> 10
        assert abs(_used_intraday(fm2) - M1) < 1e-6           # rehydrate replayed M1

        # GREEN: release reverses the committed M1 (trade_id threaded) -> used → 0.
        fm2.release_used(symbol="RELIANCE", exit_price=2500.0, exit_qty=10,
                         intent="INTRADAY", entry_price=2500.0, direction="LONG",
                         costs=0.0, trade_id=tid)
        assert abs(_used_intraday(fm2)) < 1e-6, _used_intraday(fm2)
        store.close()


def test_t1_red_recompute_fallback_reproduces_drift():
    """The legacy recompute path (trade_id=None fallback) STILL drifts — proves the
    bug is real and that threading the identity is what fixes it. used == M1−M2."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm1 = _fm(store, _L1)
        _seed_and_commit(store, fm1, sid="sig_1", symbol="RELIANCE",
                         qty=10, price=2500.0, intent="INTRADAY", product="MIS")
        M1 = required_margin(10, 2500.0, "INTRADAY", _L1)     # 5000
        M2 = required_margin(10, 2500.0, "INTRADAY", _L2)     # 2500
        assert M1 != M2

        fm2 = _restart(store, _L2)
        # RED: trade_id=None -> recompute with L2 -> deducts M2 -> residual M1−M2.
        fm2.release_used(symbol="RELIANCE", exit_price=2500.0, exit_qty=10,
                         intent="INTRADAY", entry_price=2500.0, direction="LONG",
                         costs=0.0, trade_id=None)
        assert abs(_used_intraday(fm2) - (M1 - M2)) < 1e-6, _used_intraday(fm2)
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# T2 — warm-restart baseline (SAME leverage): no regression to the normal path
# ─────────────────────────────────────────────────────────────────────────────
def test_t2_warm_restart_same_leverage_used_to_zero():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm1 = _fm(store, _L1)
        tid = _seed_and_commit(store, fm1, sid="sig_1", symbol="RELIANCE",
                               qty=10, price=2500.0, intent="INTRADAY", product="MIS")
        fm2 = _restart(store, _L1)                            # SAME leverage
        fm2.release_used(symbol="RELIANCE", exit_price=2500.0, exit_qty=10,
                         intent="INTRADAY", entry_price=2500.0, direction="LONG",
                         costs=0.0, trade_id=tid)
        assert abs(_used_intraday(fm2)) < 1e-6
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# T3 — partial-close (M-O2 compatible) across a leverage change: proportional slices
# ─────────────────────────────────────────────────────────────────────────────
def test_t3_partial_release_proportional_committed_margin():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm1 = _fm(store, _L1)
        tid = _seed_and_commit(store, fm1, sid="sig_1", symbol="RELIANCE",
                               qty=10, price=2500.0, intent="INTRADAY", product="MIS")
        M1 = required_margin(10, 2500.0, "INTRADAY", _L1)     # 5000
        fm2 = _restart(store, _L2)                            # leverage EDITED
        assert abs(_used_intraday(fm2) - M1) < 1e-6

        # Partial 1: close 4 of 10 -> release M1*4/10 = 2000 (NOT the L2 recompute 1000).
        fm2.release_used(symbol="RELIANCE", exit_price=2500.0, exit_qty=4,
                         intent="INTRADAY", entry_price=2500.0, direction="LONG",
                         costs=0.0, trade_id=tid)
        assert abs(_used_intraday(fm2) - (M1 - M1 * 4 / 10)) < 1e-6   # 3000

        # Partial 2: close the remaining 6 -> release M1*6/10 = 3000 -> used → 0.
        fm2.release_used(symbol="RELIANCE", exit_price=2500.0, exit_qty=6,
                         intent="INTRADAY", entry_price=2500.0, direction="LONG",
                         costs=0.0, trade_id=tid)
        assert abs(_used_intraday(fm2)) < 1e-6                        # total released == M1
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# T4 — multi-bucket: intraday + delivery, leverage edited → each reverses its own M1
# ─────────────────────────────────────────────────────────────────────────────
def test_t4_multi_bucket_each_reverses_committed():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm1 = _fm(store, _L1)
        tid_i = _seed_and_commit(store, fm1, sid="sig_i", symbol="RELIANCE",
                                 qty=10, price=2500.0, intent="INTRADAY", product="MIS")
        tid_p = _seed_and_commit(store, fm1, sid="sig_p", symbol="TATASTEEL",
                                 qty=5, price=2000.0, intent="DELIVERY", product="CNC")
        M1_i = required_margin(10, 2500.0, "INTRADAY", _L1)   # 5000
        M1_p = required_margin(5, 2000.0, "DELIVERY", _L1)    # 10000

        fm2 = _restart(store, _L2)                            # both intents edited
        assert abs(_used_intraday(fm2) - M1_i) < 1e-6
        assert abs(_used_positional(fm2) - M1_p) < 1e-6

        fm2.release_used(symbol="RELIANCE", exit_price=2500.0, exit_qty=10,
                         intent="INTRADAY", entry_price=2500.0, direction="LONG",
                         costs=0.0, trade_id=tid_i)
        fm2.release_used(symbol="TATASTEEL", exit_price=2000.0, exit_qty=5,
                         intent="DELIVERY", entry_price=2000.0, direction="LONG",
                         costs=0.0, trade_id=tid_p)
        assert abs(_used_intraday(fm2)) < 1e-6
        assert abs(_used_positional(fm2)) < 1e-6
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# T5 — M-C1 preserved: PnL booking unchanged → live warm-restart total==broker.net
# ─────────────────────────────────────────────────────────────────────────────
def test_t5_mc1_carryover_preserved_after_fix():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm1 = _fm(store, _L1)
        tid = _seed_and_commit(store, fm1, sid="sig_1", symbol="RELIANCE",
                               qty=10, price=2500.0, intent="INTRADAY", product="MIS")
        # Close at a PROFIT via the fixed release (trade_id threaded). PnL booking is
        # unchanged by M-C7 (only the margin source changed).
        rr = fm1.release_used(symbol="RELIANCE", exit_price=2600.0, exit_qty=10,
                              intent="INTRADAY", entry_price=2500.0, direction="LONG",
                              costs=0.0, trade_id=tid)
        pnl = (2600.0 - 2500.0) * 10                          # +1000
        assert abs(rr.pnl_delta - pnl) < 1e-6                 # PnL booking intact
        with store.transaction() as cur:
            cur.execute("UPDATE trades SET status='CLOSED' WHERE trade_id=?", (tid,))

        # M-C1 identity: a live warm restart seeds broker.net − Σ today RELEASE_USED,
        # then rehydrate Phase-2 re-adds it → total == broker.net (no double-count).
        broker_net = _BAL + pnl
        fm2 = _fm(store, _L1)
        carryover = fm2.today_realized_pnl_carryover()
        assert abs(carryover - pnl) < 1e-6                    # carryover is PnL-based, intact
        # Re-seed the live way and rehydrate.
        fm3 = _fm(store, _L1)
        fm3.sync_from_broker(broker_net - fm3.today_realized_pnl_carryover())
        fm3.rehydrate_from_open_trades()
        assert abs(fm3.get_snapshot().total - broker_net) < 1e-6
        store.close()
