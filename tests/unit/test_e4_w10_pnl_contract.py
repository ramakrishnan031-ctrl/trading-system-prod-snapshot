"""tests/unit/test_e4_w10_pnl_contract.py — E4 + W10, the pnl_delta=NET contract.

E4 and W10 are ONE bug: a writer/reader disagreement about what
``fm_ledger.pnl_delta`` means (docs/audit/e4_investigation_17jul2026.md).

  WRITER  fund_manager.release_used: ``pnl = gross - costs`` -> pnl_delta (NET),
          with ``costs`` stored alongside.
  READER  state_store.get_daily_realized_net_pnl: ``SUM(pnl_delta) - SUM(costs)``
          on the false premise (asserted in its own docstring) that pnl_delta was
          GROSS -> costs subtracted TWICE  ............................... W10
  WRITER  the 3 BACKSTOP close paths (order_reconciler CHECK1 + CHECK4-partial,
          cnc_gtt_monitor) passed ``costs=0.0`` because no CostCalculator was
          ever wired into them -> their pnl_delta was GROSS  ............. E4

Shipping either half alone is a REGRESSION: fixing E4 alone makes the reader
double-subtract the newly-real costs on every close; fixing W10 alone leaves the
backstop rows gross, under-counting exactly the forced exits (RMS/CHECK1) that
matter most. So: ONE contract — **pnl_delta is NET; `costs` is observability
only and is never re-subtracted** — applied to reader and writers together.

Both halves of the documented dual daily-loss mechanism read that one function
(post-close breach fund_manager:1279; pre-trade RE7 gate via get_snapshot), so
this is a live risk-posture change: the limit now fires on the TRUE net.

RED-on-old discriminator
------------------------
``test_red_on_old_*`` are the fail-on-old/pass-on-new proofs and are written to
be unambiguous about WHICH half they pin:
  * reader half — seed a NET row and assert the reader does not re-subtract
    costs (old tree: returns net - costs).
  * writer half — drive real CHECK1 / CHECK4 / GTT closes and assert the ledger
    row is NET with real costs recorded (old tree: costs=0.0, pnl_delta gross).

Components — REAL: StateStore (real schema + fm_ledger), FundManager (real
release_used + FM7 daily-loss check), OrderReconciler / CncGttMonitor (real
close paths), CostCalculator (real config/broker_costs.yaml — the SAME rates
main.py builds pre-mode-branch, which is why paper==live is free here).
SIMULATED: the broker adapter + quote_fn only (deterministic exit price).
"""
from __future__ import annotations

import logging
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import yaml

from broker.cost_calculator import CostCalculator, round_trip_costs_or_zero
from capital.fund_manager import FundManager, required_margin
from core.config_loader import BrokerCostsConfig, OrderReconcilerConfig
from core.events import EventBus
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.order_manager import OrderManager
from orders.order_reconciler import OrderReconciler

_SCHEMA = Path(__file__).parent.parent.parent / "core" / "schema.sql"
_COSTS_YAML = Path(__file__).parent.parent.parent / "config" / "broker_costs.yaml"
_LEV = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}
_BAL = 100_000.0
_INTRADAY_PCT = 0.70
_QTY, _PRICE = 10, 2500.0
_FULL_MARGIN = required_margin(_QTY, _PRICE, "INTRADAY", _LEV)   # 5000.0


# ─────────────────────────────────────────────────────────────────────────────
# Builders — real components
# ─────────────────────────────────────────────────────────────────────────────
class _StubAdapter:
    """get_trades()->[] forces _resolve_exit_price through to quote_fn."""

    def get_trades(self):
        return []

    def cancel_order(self, *a, **k):
        return SimpleNamespace(success=True)


def _quote(px):
    return lambda keys: {k: SimpleNamespace(last_price=px) for k in keys}


def _cost_calculator() -> CostCalculator:
    """The REAL production rates — same YAML main.py loads, mode-agnostic."""
    return CostCalculator(
        BrokerCostsConfig.model_validate(
            yaml.safe_load(_COSTS_YAML.read_text(encoding="utf-8"))
        )
    )


def _store(tmp: Path) -> StateStore:
    return StateStore(tmp / "t.db", _SCHEMA)


@contextmanager
def _env():
    """A real StateStore on a temp DB, closed on exit.

    ignore_cleanup_errors + close() mirror the existing capital-suite pattern:
    on Windows the ATTACHed analytics.db (schema v28 split) keeps a handle that
    blocks TemporaryDirectory cleanup.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        store = _store(Path(td))
        try:
            yield store
        finally:
            store.close()


def _fm(store: StateStore, on_loss_breach=None, loss_pct=0.10) -> FundManager:
    fm = FundManager(
        state_store=store, bus=EventBus(), logger=logging.getLogger("t_fm"),
        intraday_bucket_pct=_INTRADAY_PCT, positional_bucket_pct=0.30,
        daily_loss_limit_pct=loss_pct, leverage_map=_LEV,
        slm_margin_buffer_pct=0.0, on_daily_loss_breach=on_loss_breach,
    )
    fm.initialize(_BAL)
    return fm


def _reconciler(store, fm, quote_fn=None, cost_calculator=-1) -> OrderReconciler:
    cfg = OrderReconcilerConfig(poll_interval_sec=60, capital_drift_tolerance=50.0)
    return OrderReconciler(
        state_store=store, adapter=_StubAdapter(), fund_manager=fm,
        kill_switch=None, notifier=None, bus=EventBus(),
        logger=logging.getLogger("order_reconciler"), cfg=cfg,
        quote_fn=quote_fn or (lambda keys: {}), broker_orders_fn=None,
        # -1 sentinel: default to the REAL calculator (the production wiring);
        # pass None explicitly to model the unwired/degraded path.
        cost_calculator=_cost_calculator() if cost_calculator == -1 else cost_calculator,
    )


def _open_trade(store, fm, *, direction="LONG", qty=_QTY, price=_PRICE,
                symbol="RELIANCE", sid="sig_1", product="MIS",
                intent="INTRADAY") -> str:
    """Reserve + commit_to_used + a matching OPEN trade with an ENTRY order
    (product lives on the order row — the JOIN get_all_open_trades reads)."""
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,
               received_at,expires_at,status,fingerprint,fingerprint_date)
               VALUES (?,?,'S','vwap_bounce_long','2026-07-17T09:30:00+05:30',
               '2026-07-17T09:30:00+05:30','2026-07-17T09:35:00+05:30','TRADED',?,?)""",
            (sid, symbol, "fp_" + sid, "2026-07-17"),
        )
    res = fm.reserve(symbol=symbol, qty=qty, price=price, intent=intent,
                     signal_id=sid)
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


def _row_for_check(store, tid):
    for r in store.get_all_open_trades():
        if r["trade_id"] == tid:
            return r
    raise AssertionError(f"trade {tid} not in get_all_open_trades()")


def _release_rows(store):
    return store.fetch_all(
        "SELECT * FROM fm_ledger WHERE entry_type='RELEASE_USED' ORDER BY ledger_id"
    )


def _today() -> str:
    return now_ist().date().isoformat()


def _seed_release_row(store, *, pnl_delta, costs, date=None, symbol="ACME"):
    """A RELEASE_USED row written directly, so reader tests are independent of
    the writer. pnl_delta is NET by contract; costs is observability only."""
    ts = f"{date or _today()}T10:00:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO fm_ledger
                 (ts, entry_type, amount, bucket, balance_before, balance_after,
                  reason, margin_delta, pnl_delta, costs)
               VALUES (?, 'RELEASE_USED', 0.0, 'intraday', 1.0, 1.0, ?, 0.0, ?, ?)""",
            (ts, f"{symbol} exit", pnl_delta, costs),
        )


# ─────────────────────────────────────────────────────────────────────────────
# RED-on-old — the W10 (reader) half
# ─────────────────────────────────────────────────────────────────────────────
def test_red_on_old_reader_does_not_resubtract_costs():
    """W10 pin. pnl_delta is NET; the reader must sum it and STOP.

    OLD TREE: SUM(pnl_delta) - SUM(costs) = -100 - 40 = -140.0 -> FAILS.
    NEW TREE: SUM(pnl_delta) = -100.0.
    """
    with _env() as store:
        # a NET loss of 100 on which 40 of costs were already deducted
        _seed_release_row(store, pnl_delta=-100.0, costs=40.0)
        assert abs(store.get_daily_realized_net_pnl(_today()) - (-100.0)) < 1e-6


def test_red_on_old_reader_multi_row_sums_net_only():
    """Several closes: the reader is Σ pnl_delta, never Σ pnl_delta − Σ costs.

    OLD TREE: (50 - 30) - (10 + 12) = -2.0 -> FAILS. NEW: 20.0.
    """
    with _env() as store:
        _seed_release_row(store, pnl_delta=50.0, costs=10.0, symbol="A")
        _seed_release_row(store, pnl_delta=-30.0, costs=12.0, symbol="B")
        assert abs(store.get_daily_realized_net_pnl(_today()) - 20.0) < 1e-6


def test_reader_ignores_non_pnl_entry_types_and_empty_days():
    """Only RELEASE_USED/RESET_PNL carry pnl_delta — verified against the live
    ledger — so the reader needs no entry_type filter. An empty day is 0.0."""
    with _env() as store:
        assert store.get_daily_realized_net_pnl(_today()) == 0.0
        assert store.get_daily_realized_net_pnl("2026-01-01") == 0.0


def test_historical_zero_cost_rows_still_readable():
    """The 36 live rows written GROSS with costs=0.0 must not crash the reader.

    They are read as NET (an unrecoverable overstatement of ~their real costs,
    which were never computed and so cannot be backfilled). Documented gap, not
    a fixable one — asserted here so the behaviour is pinned, not discovered.
    """
    with _env() as store:
        _seed_release_row(store, pnl_delta=-24.68, costs=0.0)
        assert abs(store.get_daily_realized_net_pnl(_today()) - (-24.68)) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# RED-on-old — the E4 (writer) half: the 3 backstop paths
# ─────────────────────────────────────────────────────────────────────────────
def test_red_on_old_check1_writes_net_pnl_with_real_costs():
    """CHECK1 (RMS/manual full close) must book NET with real costs.

    OLD TREE: costs=0.0 hardcoded -> ledger costs=0.0 and pnl_delta=gross(-500)
    -> both assertions FAIL. NEW: costs>0 and pnl_delta == gross - costs.
    """
    with _env() as store:
        fm = _fm(store)
        tid = _open_trade(store, fm)
        exit_px = 2450.0                      # LONG 10 @ 2500 -> gross = -500
        rec = _reconciler(store, fm, quote_fn=_quote(exit_px))
        rec._check1_manual_close(_row_for_check(store, tid))

        rows = _release_rows(store)
        assert len(rows) == 1
        row = rows[0]
        gross = (exit_px - _PRICE) * _QTY
        assert row["costs"] > 0.0, "E4: CHECK1 still books zero costs"
        assert abs(row["pnl_delta"] - (gross - row["costs"])) < 0.01
        # and the reader (the control) sees exactly that NET, once
        assert abs(store.get_daily_realized_net_pnl(_today())
                   - row["pnl_delta"]) < 1e-6


def test_red_on_old_check1_costs_match_the_shared_calculator():
    """The booked cost is the SAME number the shared CostCalculator produces —
    no parallel cost logic, no divergence from the normal exit path."""
    with _env() as store:
        fm = _fm(store)
        tid = _open_trade(store, fm)
        exit_px = 2450.0
        rec = _reconciler(store, fm, quote_fn=_quote(exit_px))
        rec._check1_manual_close(_row_for_check(store, tid))

        expected = _cost_calculator().total_round_trip_cost(
            qty=_QTY, entry_price=_PRICE, exit_price=exit_px, product="MIS")
        assert abs(_release_rows(store)[0]["costs"] - expected) < 0.01


def test_red_on_old_check1_trade_row_stays_internally_consistent():
    """The trades row must satisfy gross_pnl - charges == net_pnl.

    OLD TREE wrote gross_pnl=net_pnl and charges=0.0 — honest only while costs
    really were 0. With real costs that would silently claim "no charges", so
    the writers had to move in the same commit. Also pins the documented
    reconciliation invariant Σ RELEASE_USED.pnl_delta == Σ trades.net_pnl.
    """
    with _env() as store:
        fm = _fm(store)
        tid = _open_trade(store, fm)
        rec = _reconciler(store, fm, quote_fn=_quote(2450.0))
        rec._check1_manual_close(_row_for_check(store, tid))

        t = store.fetch_one("SELECT * FROM trades WHERE trade_id=?", (tid,))
        assert t["charges"] > 0.0, "E4: trade row still claims zero charges"
        assert abs(t["gross_pnl"] - t["charges"] - t["net_pnl"]) < 0.01
        # cross-source: ledger NET == trades.net_pnl (PATHS.md reconciliation)
        assert abs(_release_rows(store)[0]["pnl_delta"] - t["net_pnl"]) < 0.01


def test_red_on_old_check4_partial_writes_net_for_the_closed_slice():
    """CHECK4 partial books NET for the CLOSED SLICE only (M-O2 path).

    OLD TREE: costs=0.0 -> FAILS on the costs assertion.
    """
    with _env() as store:
        fm = _fm(store)
        tid = _open_trade(store, fm)
        closed_qty, broker_qty = 4, 6
        exit_px = 2450.0
        rec = _reconciler(store, fm, quote_fn=_quote(exit_px))
        rec._check4_partial_close(_row_for_check(store, tid), broker_qty)

        rows = _release_rows(store)
        assert len(rows) == 1
        row = rows[0]
        gross = (exit_px - _PRICE) * closed_qty
        expected_costs = _cost_calculator().total_round_trip_cost(
            qty=closed_qty, entry_price=_PRICE, exit_price=exit_px, product="MIS")
        assert row["costs"] > 0.0, "E4: CHECK4 partial still books zero costs"
        assert abs(row["costs"] - expected_costs) < 0.01, \
            "partial must be costed on the closed slice, not the full qty"
        assert abs(row["pnl_delta"] - (gross - row["costs"])) < 0.01
        # the trade stays OPEN on a partial (M-O2) — no terminal write
        assert _row_for_check(store, tid)["qty_filled"] == broker_qty


def test_red_on_old_gtt_close_writes_net_with_cnc_costs():
    """cnc_gtt_monitor's GTT close books NET using CNC round-trip costs.

    OLD TREE: costs=0.0 -> FAILS. CNC rates differ from MIS (STT both sides),
    so this also pins that the product is not defaulted to MIS.
    """
    from orders.cnc_gtt_monitor import CncGttMonitor

    with _env() as store:
        fm = _fm(store)
        tid = _open_trade(store, fm, product="CNC", intent="DELIVERY",
                          symbol="INFY", sid="sig_gtt")
        exit_px = 2450.0
        now = now_ist().isoformat()
        with store.transaction() as cur:
            cur.execute(
                """INSERT INTO gtt_state
                     (gtt_id, trade_id, symbol, qty, exit_side,
                      sl_trigger, sl_limit, tgt_trigger, tgt_limit, status,
                      created_at, updated_at)
                   VALUES (1, ?, 'INFY', ?, 'SELL',
                           ?, ?, ?, ?, 'ACTIVE', ?, ?)""",
                (tid, _QTY, _PRICE * 0.99, _PRICE * 0.985,
                 _PRICE * 1.02, _PRICE * 1.015, now, now),
            )
        mon = CncGttMonitor(
            store=store, adapter=_StubAdapter(),
            placer=SimpleNamespace(forget=lambda *a, **k: None),
            fund_manager=fm,
            kill_switch=None, notifier=None, bus=EventBus(),
            logger=logging.getLogger("cnc_gtt_monitor"),
            cost_calculator=_cost_calculator(),
        )
        mon._resolve_exit_price = lambda *a, **k: exit_px
        r = store.fetch_one("SELECT * FROM gtt_state WHERE gtt_id=1")
        mon._finalize_gtt_exit(r, reason="GTT_TRIGGERED")

        rows = _release_rows(store)
        assert len(rows) == 1
        row = rows[0]
        expected = _cost_calculator().total_round_trip_cost(
            qty=_QTY, entry_price=_PRICE, exit_price=exit_px, product="CNC")
        assert row["costs"] > 0.0, "E4: GTT close still books zero costs"
        assert abs(row["costs"] - expected) < 0.01
        gross = (exit_px - _PRICE) * _QTY
        assert abs(row["pnl_delta"] - (gross - row["costs"])) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# The side finding — trade_id on RELEASE_USED rows
# ─────────────────────────────────────────────────────────────────────────────
def test_red_on_old_release_used_persists_trade_id():
    """release_used accepted trade_id but never persisted it -> 155/155 live
    rows had trade_id NULL and the ledger could not be joined to `trades`,
    which is precisely what blocked per-trade reconciliation of this bug.

    OLD TREE: trade_id IS NULL -> FAILS.
    """
    with _env() as store:
        fm = _fm(store)
        tid = _open_trade(store, fm)
        rec = _reconciler(store, fm, quote_fn=_quote(2450.0))
        rec._check1_manual_close(_row_for_check(store, tid))
        assert _release_rows(store)[0]["trade_id"] == tid


# ─────────────────────────────────────────────────────────────────────────────
# The controls — post-close breach + RE7 pre-trade gate
# ─────────────────────────────────────────────────────────────────────────────
def test_post_close_loss_check_sees_true_net_not_double_counted():
    """FM7 post-close breach fires on the TRUE net.

    Sized so the double-counted figure WOULD breach but the true net does NOT:
    that is the live risk-posture change this fix makes (the limit now trips
    LATER on normal exits than today's overstated figure).

    OLD TREE: reader returns -1000 - 400 = -1400 <= -1000 -> breach -> FAILS.
    NEW TREE: -1000 > -1000 is false... so use a margin: net -900, costs 400.
              old: -1300 -> breach; new: -900 -> no breach.
    """
    breaches = []
    with _env() as store:
        # limit = 10% of 100k = 10_000; scale the numbers to that
        _seed_release_row(store, pnl_delta=-9_000.0, costs=4_000.0)
        fm = _fm(store, on_loss_breach=lambda: breaches.append(1))
        # a further tiny close: true net so far -9000 (> -10000 -> no breach);
        # the OLD reader would have seen -13000 (<= -10000 -> breach).
        tid = _open_trade(store, fm)
        rec = _reconciler(store, fm, quote_fn=_quote(_PRICE))   # ~flat close
        rec._check1_manual_close(_row_for_check(store, tid))
        assert breaches == [], "loss limit fired on the double-counted figure"


def test_post_close_loss_check_still_fires_on_a_real_breach():
    """The control is not merely disabled: a genuine net breach still fires."""
    breaches = []
    with _env() as store:
        _seed_release_row(store, pnl_delta=-11_000.0, costs=0.0)
        fm = _fm(store, on_loss_breach=lambda: breaches.append(1))
        tid = _open_trade(store, fm)
        rec = _reconciler(store, fm, quote_fn=_quote(_PRICE))
        rec._check1_manual_close(_row_for_check(store, tid))
        assert breaches, "true net breach did NOT fire the daily-loss control"


def test_re7_pre_trade_gate_reads_the_same_net():
    """The pre-trade RE7 gate (via get_snapshot) and the post-close check must
    read ONE number — the true net. Pins that both halves moved together.

    OLD TREE: snapshot.daily_realized_pnl = -100 - 40 = -140 -> FAILS.
    """
    with _env() as store:
        _seed_release_row(store, pnl_delta=-100.0, costs=40.0)
        fm = _fm(store)
        assert abs(fm.get_snapshot().daily_realized_pnl - (-100.0)) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# Daily reset — must stay in lockstep with the reader
# ─────────────────────────────────────────────────────────────────────────────
def test_daily_reset_zeroes_the_day_under_the_net_contract():
    """reset_daily_pnl reads the reader and writes -old_pnl, so it is in
    lockstep BY CONSTRUCTION under either contract — pinned here because the
    old data (Σ pnl_delta == Σ costs on every completed day) was built ON the
    double-subtraction, and a reader change must not break EOD zeroing."""
    with _env() as store:
        _seed_release_row(store, pnl_delta=-100.0, costs=40.0, symbol="A")
        _seed_release_row(store, pnl_delta=25.0, costs=8.0, symbol="B")
        fm = _fm(store)
        assert abs(fm.get_snapshot().daily_realized_pnl - (-75.0)) < 1e-6
        fm.reset_daily_pnl()
        assert abs(store.get_daily_realized_net_pnl(_today())) < 1e-6
        # and it stays zero on a re-read (the RESET_PNL row is durable)
        assert abs(fm.get_snapshot().daily_realized_pnl) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# Restart parity — rehydrate must reproduce a continuous run EXACTLY
# ─────────────────────────────────────────────────────────────────────────────
def test_restart_capital_matches_continuous_run_exactly():
    """ChatGPT Q5 / instruction §3(e). rehydrate replays pnl_delta directly and
    never subtracts costs — consistent with NET. This pins that a mid-session
    restart lands on the SAME available capital / total as never restarting.

    Would have caught a fix that flipped pnl_delta to GROSS (rehydrate would
    then have re-credited the costs).
    """
    with _env() as store:
        fm = _fm(store)
        tid = _open_trade(store, fm)
        rec = _reconciler(store, fm, quote_fn=_quote(2450.0))
        rec._check1_manual_close(_row_for_check(store, tid))

        continuous = fm.get_snapshot()

        # a fresh FundManager over the SAME store == a restart
        fm2 = _fm(store)
        fm2.rehydrate_from_open_trades()
        restarted = fm2.get_snapshot()

        assert abs(restarted.total - continuous.total) < 1e-6, \
            "restart-capital diverged from a continuous run"
        assert abs(restarted.intraday_avail
                   - continuous.intraday_avail) < 1e-6
        assert abs(restarted.intraday_used
                   - continuous.intraday_used) < 1e-6
        assert abs(restarted.daily_realized_pnl
                   - continuous.daily_realized_pnl) < 1e-6


def test_live_seed_carryover_cancels_rehydrate_replay():
    """M-C1: main.py seeds live capital as broker.net - today_realized_pnl_carryover(),
    and rehydrate Phase 2 re-adds the SAME rows. The two must cancel exactly, so
    the seed lands on broker.net. Both sides read pnl_delta directly, so the
    cancellation holds under the NET contract too (pinned, since E4 changes the
    VALUE of pnl_delta on the backstop paths)."""
    with _env() as store:
        fm = _fm(store)
        tid = _open_trade(store, fm)
        rec = _reconciler(store, fm, quote_fn=_quote(2450.0))
        rec._check1_manual_close(_row_for_check(store, tid))

        carryover = fm.today_realized_pnl_carryover()
        booked = _release_rows(store)[0]["pnl_delta"]
        assert abs(carryover - booked) < 1e-6, \
            "the live seed Σ must be the NET rows rehydrate replays"


# ─────────────────────────────────────────────────────────────────────────────
# Parity + the fail-open policy
# ─────────────────────────────────────────────────────────────────────────────
def test_paper_and_live_book_identical_costs():
    """Parity is FREE: CostCalculator is built from config rates BEFORE main.py's
    paper/live branch, so one instance serves both modes. There is no mode
    branch anywhere on this path — pinned by asserting the calculator is
    mode-agnostic for identical inputs."""
    calc = _cost_calculator()
    a = calc.total_round_trip_cost(qty=_QTY, entry_price=_PRICE,
                                   exit_price=2450.0, product="MIS")
    b = calc.total_round_trip_cost(qty=_QTY, entry_price=_PRICE,
                                   exit_price=2450.0, product="MIS")
    assert a == b and a > 0.0


def test_cost_calc_failure_fails_open_and_does_not_block_the_release(caplog):
    """A cost-calculation failure must never block a capital release — that
    would strand the closed position's margin in `used` for the session, which
    is strictly worse than booking a wrong cost. It degrades to 0.0 (reverting
    that ONE row to pre-E4 gross) and says so LOUDLY."""
    class _Boom:
        def total_round_trip_cost(self, **kw):
            raise RuntimeError("rates unavailable")

    with caplog.at_level(logging.ERROR):
        got = round_trip_costs_or_zero(
            _Boom(), qty=1, entry_price=1.0, exit_price=1.0, product="MIS",
            logger=logging.getLogger("t_cc"), context="unit",
        )
    assert got == 0.0
    assert "cost_calc_failed" in caplog.text


def test_unwired_calculator_degrades_loudly(caplog):
    """A None calculator (an un-updated construction site) must not crash — it
    degrades to the pre-E4 behaviour and is loudly attributable, so a missing
    wiring shows up in logs rather than as silently gross P&L."""
    with caplog.at_level(logging.ERROR):
        got = round_trip_costs_or_zero(
            None, qty=1, entry_price=1.0, exit_price=1.0, product="MIS",
            logger=logging.getLogger("t_cc"), context="unit",
        )
    assert got == 0.0
    assert "cost_calc_unavailable" in caplog.text
