"""tests/unit/test_mo2_check4_partial_capital.py — Wave-6, M-O2 regression.

M-O2 (docs/audit/full_system_audit_04july2026.md): the reconciler's CHECK4
(PARTIAL_CLOSE — broker qty < local qty_filled, i.e. part of the position was
closed OUTSIDE the system) historically updated qty_filled + cancelled stale
SL/TGT + alerted, but NEVER released the capital for the closed portion nor booked
its realized PnL. CHECK1 (full close) does both via fm.release_used; CHECK4 skipped
it — so the phantom-qty margin stayed locked in `used` all session and the closed
portion's PnL never entered daily_realized (the FM7 daily-loss gate could not see a
partial stop-out).

Fix (order_reconciler._check4_partial_close): mirror CHECK1 proportionally —
release_used(exit_qty=local_qty-broker_qty) — gated by a qty_filled CAS
(``WHERE qty_filled=local_qty``) so the release is exactly-once. The trade stays
OPEN (no terminal write). PositionClosed is deliberately NOT published on a partial
(its subscribers — paper adapter, slippage_recorder, shadow_tracker — treat it as a
FULL close), so the observability is via logs, not the event.

Components — REAL: StateStore (real schema + fm_ledger), FundManager (real
reserve/commit_to_used/release_used + FM7 daily-loss check), OrderReconciler (real
_check4_partial_close / _check1_manual_close / _resolve_exit_price). SIMULATED: the
broker adapter (get_trades()->[] so exit-price falls to the injected quote_fn; no
live SL/TGT orders so the cancel path no-ops) and the quote_fn (a controlled LTP so
the marketable exit price — hence PnL — is deterministic).

RED (pre-fix) vs GREEN (fixed) discriminator: `used` / fm_ledger RELEASE_USED rows /
daily_realized. Pre-fix CHECK4 releases nothing, so T1/T4/T6 (used unchanged, 0
RELEASE_USED rows) and T5 (broker_qty==0 not routed to CHECK1 → trade left OPEN)
FAIL; post-fix they pass.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace

from capital.fund_manager import FundManager, required_margin
from core.config_loader import OrderReconcilerConfig
from core.events import EventBus
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.order_manager import OrderManager
from orders.order_reconciler import OrderReconciler

_SCHEMA = Path(__file__).parent.parent.parent / "core" / "schema.sql"
_LEV = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}
_BAL = 100_000.0
_INTRADAY_PCT = 0.70
_QTY, _PRICE = 10, 2500.0
# margin for qty=10 @ 2500 INTRADAY lev 5 = 10*2500/5 = 5000
_FULL_MARGIN = required_margin(_QTY, _PRICE, "INTRADAY", _LEV)   # 5000.0


# ─────────────────────────────────────────────────────────────────────────────
# Simulated broker boundary
# ─────────────────────────────────────────────────────────────────────────────
class _StubAdapter:
    """Minimal adapter: get_trades()->[] forces _resolve_exit_price to fall
    through to quote_fn (deterministic exit price); cancel_order is a no-op (the
    test trades carry no live SL/TGT rows, so the cancel path never fires)."""

    def get_trades(self):
        return []

    def cancel_order(self, *a, **k):
        return SimpleNamespace(success=True)


def _quote(px):
    """A quote_fn returning last_price=px for every bare symbol key."""
    return lambda keys: {k: SimpleNamespace(last_price=px) for k in keys}


# ─────────────────────────────────────────────────────────────────────────────
# Real builders
# ─────────────────────────────────────────────────────────────────────────────
def _store(tmp: Path) -> StateStore:
    return StateStore(tmp / "t.db", _SCHEMA)


def _fm(store: StateStore, on_loss_breach=None) -> FundManager:
    fm = FundManager(
        state_store=store, bus=EventBus(), logger=logging.getLogger("t_fm"),
        intraday_bucket_pct=_INTRADAY_PCT, positional_bucket_pct=0.30,
        daily_loss_limit_pct=0.10, leverage_map=_LEV,
        slm_margin_buffer_pct=0.0, on_daily_loss_breach=on_loss_breach,
    )
    fm.initialize(_BAL)
    return fm


def _reconciler(store: StateStore, fm: FundManager, quote_fn=None) -> OrderReconciler:
    cfg = OrderReconcilerConfig(poll_interval_sec=60, capital_drift_tolerance=50.0)
    return OrderReconciler(
        state_store=store, adapter=_StubAdapter(), fund_manager=fm,
        kill_switch=None, notifier=None, bus=EventBus(),
        logger=logging.getLogger("order_reconciler"), cfg=cfg,
        quote_fn=quote_fn or (lambda keys: {}), broker_orders_fn=None,
    )


def _open_trade(store, fm, *, direction="LONG", qty=_QTY, price=_PRICE,
                symbol="RELIANCE", sid="sig_1") -> str:
    """Reserve + commit_to_used (margin lands in `used`) + a matching OPEN trade
    with an ENTRY order (product=MIS, so get_all_open_trades' JOIN resolves the
    intent to INTRADAY) and entry_actual_price set. Returns trade_id.

    `product` lives on the ENTRY order row (the trades table has no product
    column) — exactly the JOIN the reconciler reads via get_all_open_trades."""
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,
               received_at,expires_at,status,fingerprint,fingerprint_date)
               VALUES (?,?,'S','vwap_bounce_long','2026-07-09T09:30:00+05:30',
               '2026-07-09T09:30:00+05:30','2026-07-09T09:35:00+05:30','TRADED',?,?)""",
            (sid, symbol, "fp_" + sid, "2026-07-09"),
        )
    res = fm.reserve(symbol=symbol, qty=qty, price=price, intent="INTRADAY",
                     signal_id=sid)
    assert res.success, res.reason_if_failed
    fm.commit_to_used(res.reservation_id, price, qty)   # reserved -> used
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
               VALUES (?, ?, 'ENTRY', ?, 'LIMIT', 'MIS', 'regular', ?, 'COMPLETE',
                       0.0, ?, ?)""",
            (f"ord_{tid}", tid, "BUY" if direction == "LONG" else "SELL",
             qty, now, now),
        )
    return tid


def _row_for_check(store, tid):
    """The production input to CHECK4: the get_all_open_trades() row (carries
    `product` from the ENTRY-order JOIN)."""
    for r in store.get_all_open_trades():
        if r["trade_id"] == tid:
            return r
    raise AssertionError(f"trade {tid} not in get_all_open_trades()")


def _trade_row(store, tid):
    return store.fetch_one("SELECT * FROM trades WHERE trade_id=?", (tid,))


def _release_used_rows(store, symbol="RELIANCE"):
    return store.fetch_all(
        "SELECT * FROM fm_ledger WHERE entry_type='RELEASE_USED' "
        "AND reason LIKE ? ORDER BY ledger_id",
        (f"{symbol}%",),
    )


def _used(fm) -> float:
    return fm.get_snapshot().intraday_used


def _today() -> str:
    return now_ist().date().isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# T1 — partial external close (core): capital released + PnL booked, trade OPEN
# ─────────────────────────────────────────────────────────────────────────────
def test_t1_partial_close_releases_capital_and_books_pnl():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store)
        tid = _open_trade(store, fm)
        rec = _reconciler(store, fm, quote_fn=_quote(2600.0))  # LONG exit 2600 → profit
        assert abs(_used(fm) - _FULL_MARGIN) < 1e-6

        action = rec._check4_partial_close(_row_for_check(store, tid), broker_qty=6)

        closed_qty = _QTY - 6  # 4
        # (GREEN; pre-fix RED: no release → used stays 5000, 0 RELEASE_USED rows)
        assert abs(_used(fm) - (_FULL_MARGIN - required_margin(closed_qty, _PRICE,
                   "INTRADAY", _LEV))) < 1e-6                       # 5000 - 2000 = 3000
        rows = _release_used_rows(store)
        assert len(rows) == 1
        assert abs(rows[0]["pnl_delta"] - (2600.0 - 2500.0) * closed_qty) < 1e-6   # +400
        assert abs(rows[0]["margin_delta"] + 2000.0) < 1e-6                        # -2000
        assert abs(store.get_daily_realized_net_pnl(_today()) - 400.0) < 1e-6
        # qty reduced, trade STILL OPEN (no terminal write → terminal-guard silent)
        row2 = _trade_row(store, tid)
        assert row2["qty_filled"] == 6
        assert row2["status"] == "OPEN"
        assert action.check_name == "PARTIAL_CLOSE"
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# T2 — direction correctness: LONG loss and SHORT profit book the right sign
# ─────────────────────────────────────────────────────────────────────────────
def test_t2_long_partial_loss_sign():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store)
        tid = _open_trade(store, fm, direction="LONG")
        rec = _reconciler(store, fm, quote_fn=_quote(2300.0))   # LONG exit below entry → loss
        rec._check4_partial_close(_row_for_check(store, tid), broker_qty=6)
        rows = _release_used_rows(store)
        assert len(rows) == 1
        # LONG pnl = (exit - entry) * closed_qty = (2300-2500)*4 = -800
        assert abs(rows[0]["pnl_delta"] - (2300.0 - 2500.0) * 4) < 1e-6
        store.close()


def test_t2_short_partial_profit_sign():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store)
        tid = _open_trade(store, fm, direction="SHORT")
        rec = _reconciler(store, fm, quote_fn=_quote(2400.0))   # SHORT cover below entry → profit
        rec._check4_partial_close(_row_for_check(store, tid), broker_qty=6)
        rows = _release_used_rows(store)
        assert len(rows) == 1
        # SHORT pnl = (entry - exit) * closed_qty = (2500-2400)*4 = +400
        assert abs(rows[0]["pnl_delta"] - (2500.0 - 2400.0) * 4) < 1e-6
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# T3 — daily-loss safety: a partial LOSS is booked where the FM7 gate reads it
# ─────────────────────────────────────────────────────────────────────────────
def test_t3_partial_loss_counts_toward_daily_loss_gate():
    breaches = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store, on_loss_breach=lambda: breaches.append(True))
        tid = _open_trade(store, fm)
        # closed_qty=8 (broker_qty=2), exit 1200: pnl=(1200-2500)*8 = -10400,
        # which breaches 10% of (100000-10400)=8960.
        rec = _reconciler(store, fm, quote_fn=_quote(1200.0))
        rec._check4_partial_close(_row_for_check(store, tid), broker_qty=2)
        assert store.get_daily_realized_net_pnl(_today()) <= -10000.0
        assert breaches == [True], "FM7 daily-loss check did not see the partial loss"
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# T4 — idempotency (no double-release): a re-detected same-delta cycle no-ops
# ─────────────────────────────────────────────────────────────────────────────
def test_t4_idempotent_no_double_release():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store)
        tid = _open_trade(store, fm)
        rec = _reconciler(store, fm, quote_fn=_quote(2500.0))   # exit=entry → pnl 0 (isolate margin)
        stale = _row_for_check(store, tid)                      # snapshot: qty_filled=10

        rec._check4_partial_close(stale, broker_qty=6)          # 1st: releases margin(4)=2000
        used1 = _used(fm)
        assert abs(used1 - (_FULL_MARGIN - 2000.0)) < 1e-6      # 3000

        # 2nd cycle with the SAME stale row (still qty_filled=10) → CAS matches 0 → NO release.
        # (If the CAS were removed but the release kept, used would drop to 1000 here.)
        rec._check4_partial_close(stale, broker_qty=6)
        assert abs(_used(fm) - used1) < 1e-6, "double-release! the qty CAS did not guard"
        assert len(_release_used_rows(store)) == 1
        assert abs(store.get_daily_realized_net_pnl(_today())) < 1e-6
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# T5 — full close via CHECK4 (broker_qty==0) finalizes, not left OPEN at qty 0
# ─────────────────────────────────────────────────────────────────────────────
def test_t5_broker_qty_zero_routes_to_full_close():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store)
        tid = _open_trade(store, fm)
        rec = _reconciler(store, fm, quote_fn=_quote(2500.0))
        action = rec._check4_partial_close(_row_for_check(store, tid), broker_qty=0)

        # routed to CHECK1's full-close accounting (GREEN; pre-fix RED: stays PARTIAL_CLOSE/OPEN)
        assert action.check_name == "MANUAL_CLOSE"
        assert abs(_used(fm)) < 1e-6                    # full margin released
        row = _trade_row(store, tid)
        assert row["status"] == "CLOSED_MANUAL"         # finalized, terminal
        assert row["status"] != "OPEN"
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# T6 — sequential DIFFERENT partials: each releases its own delta exactly once
# ─────────────────────────────────────────────────────────────────────────────
def test_t6_sequential_partials_each_release_once():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store)
        tid = _open_trade(store, fm)
        rec = _reconciler(store, fm, quote_fn=_quote(2500.0))   # pnl 0 → isolate margin

        rec._check4_partial_close(_row_for_check(store, tid), broker_qty=7)   # closed 3 → release 1500
        assert abs(_used(fm) - (_FULL_MARGIN - required_margin(3, _PRICE, "INTRADAY", _LEV))) < 1e-6

        rec._check4_partial_close(_row_for_check(store, tid), broker_qty=4)   # closed 3 → release 1500
        assert abs(_used(fm) - (_FULL_MARGIN - required_margin(6, _PRICE, "INTRADAY", _LEV))) < 1e-6

        row = _trade_row(store, tid)
        assert row["qty_filled"] == 4
        assert row["status"] == "OPEN"
        assert len(_release_used_rows(store)) == 2       # two partials, each released once
        store.close()
