"""
orders/mis_autosquareoff.py — MIS AUTO-SQUAREOFF ORCHESTRATOR (28-Aug-2026).

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
---------------------------------------------
This is an ADD-ALONGSIDE unit. It does NOT replace, move or modify:
  * `circuit_breaker_force_close_15:15` — a SOFT_KILL that closes nothing
    (main.py:693-698). A DIFFERENT control that merely coincides in time.
  * `eod_squareoff_time: 15:17` — a COMPOUND end-of-day routine (cancel pending,
    exit positions, summary, WAL checkpoint, fund_manager.reset_daily_pnl).
    Moving it would book any 15:07-15:17 realised P&L AFTER the day's RESET_PNL
    ledger row, and a non-CAS position is broker-squared at 15:25 — eighteen
    minutes after the proposed reset. That is a silent money defect, so 15:17
    stays exactly where it is and keeps its bookkeeping role.

WHY IT EXISTS
-------------
Zerodha's earliest EQUITY-STOCK auto-square-off is 15:12 for CAS (Closing
Auction Session) stocks — the F&O-listed equities, whose continuous trading ends
at 15:15. The system's only position-closing pass ran at 15:17, i.e. FIVE MINUTES
LATE for any CAS stock, structurally, every day. On 27-Aug-2026 TATAPOWER (a CAS
stock, is_fno=true) was closed externally at 15:12:47 and the ledger carries a
Rs 59.00 "Call and Trade charges (Auto Square Off)" debit = Rs 50 + 18% GST = ONE
order.

ROLE LABELS — so nobody misreads the clocks later:
  15:03 / 15:06  PRIMARY MIS SAFETY            (this unit)
  15:09          configured protective cutoff  (config, broker-adjustable)
  15:15          SOFT_KILL — closes nothing
  15:17          EOD ACCOUNTING + EMERGENCY BACKSTOP — NOT the primary square-off

THE TWO PASSES HAVE DIFFERENT PURPOSES
--------------------------------------
  PASS 1 @ cutoff - first_offset  (15:03) — PRICE. LIMIT_THEN_MARKET, grace capped.
  PASS 2 @ cutoff - second_offset (15:06) — CERTAINTY. MARKET, NO GRACE, EVER.

SCHEDULE MOVED 03-Sep-2026 (was 15:07 / 15:10, cutoff 15:12)
------------------------------------------------------------
Zerodha publishes TWO different CAS figures — the support page says 15:12, their
03-Aug-2026 post says 15:10. Rule adopted: never design to the LATER of two
conflicting broker deadlines. The cutoff moved to 15:09 so both passes AND the
post-pass verification finish before the EARLIEST possible broker action. The old
15:10 PASS_2 sat exactly ON that boundary.
KNOWN COST, on the ledger: 15:09 is universal, so it gives up ~16 minutes of
holding time on non-CAS names (broker cutoff 15:25). A per-symbol CAS/non-CAS
deadline is the correct end state; it needs the F&O list and a lookup.

WHAT 03-Sep-2026 CHANGED IN THE FAILURE PATH (F1/F2)
----------------------------------------------------
This unit cancels the protective orders FIRST and only then decides whether it can
exit. Before 03-Sep, every failure after that point returned with the stop
cancelled and no sell placed — there was no undo. Measured twice: 02-Sep
COALINDIA and 03-Sep ANANTRAJ (which stayed naked until a human closed it).
  F1  _restore_protection() re-places the SL leg on EVERY failure path.
  F2  _verify_cancelled() now polls to a bounded deadline instead of once at
      +27-53 ms; the same orders read terminal at +1.115 s.
See docs/incident/2026-09-03_naked_position_ANANTRAJ.md.

PASS 2 owns its protocol explicitly (PASS_2_EXIT_PROTOCOL). It is NOT reached by
overriding a field on the generic EOD path, so a future maintainer changing the
general `eod_squareoff.limit_grace_sec` CANNOT reintroduce a 120s blocking sleep
into the final safety pass.

SERIALISATION — a property to PRESERVE, not to add
--------------------------------------------------
`eod_squareoff` runs ONE daemon poll thread calling check_and_fire() synchronously
(eod_squareoff.py:300-312), so a blocking _fire blocks the loop and overlap is
structurally impossible. This unit deliberately mirrors that: ONE poll thread,
both passes, evaluated sequentially, with a per-date PER-PASS fired flag under one
lock. If the two passes were ever scheduled on independent threads/timers, PASS 1
mid-promotion could collide with PASS 2's MARKET exit on the same symbol — two
exits in flight, reversing the position.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# The product boundary. NARROWER than the EOD path's EMERGENCY_FLATTEN_PRODUCTS,
# which is frozenset({"MIS", "CO"}) and therefore admits CO. This unit is
# equity-MIS only and must NOT inherit CO eligibility by reusing that constant.
# CO has never been placed in production (SELECT DISTINCT product FROM orders =
# {MIS, CNC}), so the CO arm is latent — the narrowing guards it anyway.
# ─────────────────────────────────────────────────────────────────────────────
MIS_PRODUCT: str = "MIS"

# PASS 2's protocol is a CONSTANT of this unit, not a config knob and not a field
# read from the generic EOD settings. Named so a mutation test can assert it.
PASS_2_EXIT_PROTOCOL: str = "MARKET"

# ── F2 (03-Sep-2026): the cancel-verification settle window ──────────────────
# The broker accepts a cancel asynchronously; the order history lags it. Measured
# on 03-Sep: the verification read at +27-53 ms saw a non-terminal status, and the
# SAME orders read terminal at +1.115 s. The single immediate poll produced 3 of 3
# CANCEL_FAILED across 02-Sep and 03-Sep -- each time AFTER the protective orders
# had already been cancelled, i.e. it manufactured the naked position.
#
# 5 s is a budget, not a measurement (n=1 for the 1.115 s figure -- do NOT derive a
# tolerance from it). It is chosen from the SCHEDULE: PASS_1 15:03, PASS_2 15:06,
# verify/restore 15:08, internal cutoff 15:09 -- a 5 s worst case per pass is
# comfortably inside every gap. Module-level so tests can shrink it.
_CANCEL_SETTLE_DEADLINE_SEC: float = 5.0
_CANCEL_SETTLE_POLL_SEC: float = 0.25



def _row_get(row, key: str, default=None):
    """Read one field from a dict / sqlite3.Row / attribute object.

    F1 runs inside an ALREADY-FAILING path. A KeyError/IndexError here would
    mask the original failure and skip the alert -- which is exactly the class
    of defect F1 exists to remove. Missing field -> default, never an exception.
    """
    try:
        if hasattr(row, key):
            return getattr(row, key)
    except Exception:  # noqa: BLE001
        pass
    try:
        return row[key]
    except Exception:  # noqa: BLE001
        return default


class MisState:
    """Explicit outcome vocabulary. Every terminal path names one of these."""
    NO_MIS = "NO_MIS"
    MIS_FOUND = "MIS_FOUND"
    CANCEL_FAILED = "CANCEL_FAILED"
    EXIT_SUBMITTED = "EXIT_SUBMITTED"
    EXIT_PARTIALLY_FILLED = "EXIT_PARTIALLY_FILLED"
    EXIT_FILLED = "EXIT_FILLED"
    EXIT_REJECTED = "EXIT_REJECTED"
    MIS_REMAINS = "MIS_REMAINS"
    BROKER_STATE_UNAVAILABLE = "BROKER_STATE_UNAVAILABLE"
    RECONCILIATION_UNKNOWN = "RECONCILIATION_UNKNOWN"
    DEADLINE_BREACH = "DEADLINE_BREACH"
    DEADLINE_BREACH_ANTICIPATED = "DEADLINE_BREACH_ANTICIPATED"
    # Scheduling observability (FILE 27 A-2: these two are DISTINCT, and neither
    # implies concurrency — the serial poller makes concurrency impossible).
    PASS_1_RUNNING_AT_PASS_2_DUE = "PASS_1_RUNNING_AT_PASS_2_DUE"
    PASS_2_STARTED_LATE = "PASS_2_STARTED_LATE"
    PASS_1_ABANDONED_FOR_PASS_2 = "PASS_1_ABANDONED_FOR_PASS_2"
    PASS_1_DEGRADED_TO_MARKET = "PASS_1_DEGRADED_TO_MARKET"


PASS_1 = "PASS_1"
PASS_2 = "PASS_2"

# A pass that could not READ broker truth has not completed -- it must stay
# retryable (bounded by max_pass_1_attempts) rather than be latched as done.
# Latching it would silently convert "we never found out" into "we finished",
# which is the same class of error as treating a query failure as flat.
_INCONCLUSIVE_STATES = frozenset({
    MisState.BROKER_STATE_UNAVAILABLE,
    MisState.RECONCILIATION_UNKNOWN,
})


# ── the timing contract lives in core/ (extracted 29-Aug-2026) ───────────────
# Re-exported here so every existing importer keeps working unchanged. F must
# import it from core.mis_squareoff_timing directly and NEVER from this module:
# a test asserts this module is absent from sys.modules when F fires, and this
# re-export is precisely the line that would let a future "simplification" put it
# back on F's import path. The re-export and that assertion are a matched pair.
from core.ids import truncate_tag_for_broker
from core.mis_squareoff_timing import (  # noqa: F401  (re-exported)
    MisSquareoffConfigError,
    MisSquareoffTiming,
    _parse_hhmm,
    _parse_offset_minutes,
)




@dataclass
class SymbolOutcome:
    symbol: str
    state: str
    requested_qty: int = 0
    broker_qty_at_start: int = 0
    exit_side: str = ""
    broker_order_id: str = ""
    detail: str = ""


@dataclass
class PassResult:
    which: str
    state: str
    scheduled_at: Optional[datetime] = None
    actual_start: Optional[datetime] = None
    lateness_ms: int = 0
    query_started_at: Optional[datetime] = None
    query_completed_at: Optional[datetime] = None
    completion_time: Optional[datetime] = None
    remaining_budget_at_start_ms: int = 0
    deadline_preserved: bool = True
    ran_past_check_2: bool = False
    symbols: List[SymbolOutcome] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


class MisAutoSquareoff:
    """
    The orchestrator. Dependency-injected so every branch is testable without a
    broker, a clock or a database.

    `now_fn` is the single clock source — there is no implicit datetime.now()
    anywhere in this file, so tests drive time explicitly and IST is whatever the
    injected authority says it is.
    """

    def __init__(
        self,
        *,
        adapter,
        store,
        logger,
        timing: MisSquareoffTiming,
        now_fn: Callable[[], datetime],
        is_trading_holiday_fn: Callable[[datetime], bool],
        limit_grace_sec: int = 120,
        inter_order_delay_sec: float = 0.5,
        max_pass_1_attempts: int = 3,
        # MEASURED-BOUND estimate (28-Aug observed maxima), NOT a guarantee:
        # 3 symbols, per-symbol delay placement -> ~2.0s; pessimistic per-call
        # placement -> ~8.2s. Used only for the pre-flight budget CHECK, which
        # never skips the exit.
        pass_2_bound_ms_per_symbol: int = 800,
        pass_2_bound_fixed_ms: int = 400,
        notifier=None,
        critical_sink: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._adapter = adapter
        self._store = store
        self._log = logger
        self._t = timing
        self._now = now_fn
        self._is_holiday = is_trading_holiday_fn
        self._limit_grace_sec = int(limit_grace_sec)
        self._inter_order_delay_sec = float(inter_order_delay_sec)
        self._max_pass_1_attempts = int(max_pass_1_attempts)
        self._p2_per_symbol_ms = int(pass_2_bound_ms_per_symbol)
        self._p2_fixed_ms = int(pass_2_bound_fixed_ms)
        self._notifier = notifier
        # F's entry point. Optional: the orchestrator is fully functional
        # without it, which is the point -- F observes, it does not control.
        self._critical_sink = critical_sink

        self._lock = threading.Lock()
        # A-3: per-date PER-PASS fired state. A single shared boolean is NOT
        # equivalent: PASS 1 succeeding would leave it True and PASS 2 would
        # never fire at all — the safety pass would silently not exist.
        self._fired: Dict[Tuple[date, str], bool] = {}
        self._pass_1_attempts: Dict[date, int] = {}
        self._pass_1_ran_past_check_2: Dict[date, bool] = {}
        self._results: Dict[Tuple[date, str], PassResult] = {}

    # ── scheduling ───────────────────────────────────────────────────────────

    def _at(self, now: datetime, t: dtime) -> datetime:
        return now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)

    def check_and_fire(self, now: Optional[datetime] = None) -> bool:
        """
        Evaluate both passes. Called synchronously from ONE poll thread, so the
        two passes can never execute concurrently — they are serialised by the
        caller's loop, exactly like eod_squareoff.

        B-2: once `now >= CHECK_2`, PASS 2 TAKES PRIORITY. A repeatedly-failing
        PASS 1 must not consume the window and starve the safety pass.
        """
        now = now or self._now()
        if self._is_holiday(now):
            return False
        today = now.date()
        check_1_dt = self._at(now, self._t.check_1)
        check_2_dt = self._at(now, self._t.check_2)

        # ── PASS 2 first: it owns the deadline (B-2) ──
        if now >= check_2_dt:
            with self._lock:
                if self._fired.get((today, PASS_2), False):
                    return False
                self._fired[(today, PASS_2)] = True
                pass_1_done = self._fired.get((today, PASS_1), False)
                attempts = self._pass_1_attempts.get(today, 0)
            if not pass_1_done and attempts > 0:
                # PASS 1 tried and never completed; we give it up rather than let
                # it delay the safety pass. Correct trade — and it must be VISIBLE.
                self._emit(MisState.PASS_1_ABANDONED_FOR_PASS_2,
                           f"pass_1 attempts={attempts} incomplete at CHECK_2")
            try:
                self._run_pass(PASS_2, now, check_2_dt)
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._fired[(today, PASS_2)] = False
                self._log.critical(
                    "mis_autosquareoff: PASS 2 raised; flag reset for retry: %s", exc)
                raise
            return True

        # ── PASS 1 ──
        if now >= check_1_dt:
            with self._lock:
                if self._fired.get((today, PASS_1), False):
                    return False
                attempts = self._pass_1_attempts.get(today, 0)
                if attempts >= self._max_pass_1_attempts:
                    # B-1: bounded retries. Do not spin until the poll loop stops.
                    return False
                self._pass_1_attempts[today] = attempts + 1
            try:
                r1 = self._run_pass(PASS_1, now, check_1_dt)
            except Exception as exc:  # noqa: BLE001
                self._log.critical(
                    "mis_autosquareoff: PASS 1 attempt %d raised: %s",
                    attempts + 1, exc)
                return False
            if r1.state in _INCONCLUSIVE_STATES:
                # Not done. Leave the flag clear so a later tick may retry, up to
                # max_pass_1_attempts -- and PASS 2 still takes priority at CHECK_2.
                self._log.warning(
                    "mis_autosquareoff: PASS 1 attempt %d inconclusive (%s); "
                    "retryable", attempts + 1, r1.state)
                return False
            with self._lock:
                self._fired[(today, PASS_1)] = True
            return True

        return False

    def start_polling(self, poll_interval_sec: Optional[int] = None) -> None:
        """ONE daemon thread. BOTH passes. Evaluated sequentially.

        This is the property #25 tests: check_and_fire() runs synchronously in a
        single loop, so a blocking pass blocks the loop and two passes can never
        be in flight at once. Scheduling PASS 1 and PASS 2 on independent
        threads/timers would reintroduce the collision this design exists to make
        impossible -- PASS 1 mid-promotion against PASS 2's MARKET exit on the
        same symbol, two exits in flight, position reversed.
        """
        interval = int(poll_interval_sec or self._t.poll_interval_sec)

        def _poll_loop() -> None:
            while True:
                try:
                    self.check_and_fire(self._now())
                except Exception as exc:  # noqa: BLE001
                    self._log.critical(
                        "mis_autosquareoff: poll loop error: %s", exc)
                time.sleep(interval)

        t = threading.Thread(
            target=_poll_loop, daemon=True, name="mis_autosquareoff_poll")
        with self._lock:
            self._polling_thread = t
        t.start()
        self._log.info(
            "mis_autosquareoff polling thread started (interval=%ds); "
            "CHECK_1=%s CHECK_2=%s cutoff=%s margin=%ds",
            interval, self._t.check_1, self._t.check_2, self._t.cutoff,
            self._t.margin_sec,
        )

    def is_alive(self) -> bool:
        """Read-only liveness: a dead MIS scheduler means positions ride to the
        broker cutoff, so it must be visible to /health like the EOD one."""
        with self._lock:
            t = getattr(self, "_polling_thread", None)
        return t is not None and t.is_alive()

    # ── the passes ───────────────────────────────────────────────────────────

    def _run_pass(self, which: str, now: datetime, scheduled: datetime) -> PassResult:
        today = now.date()
        res = PassResult(which=which, state=MisState.NO_MIS,
                         scheduled_at=scheduled, actual_start=now)
        res.lateness_ms = max(0, int((now - scheduled).total_seconds() * 1000))
        if which == PASS_2 and res.lateness_ms > 0:
            res.notes.append(MisState.PASS_2_STARTED_LATE)
        if which == PASS_2 and self._pass_1_ran_past_check_2.get(today, False):
            res.notes.append(MisState.PASS_1_RUNNING_AT_PASS_2_DUE)

        hard_deadline = self._at(now, self._t.cutoff)

        # ── fresh broker state. A FAILURE IS NEVER FLAT. ──
        res.query_started_at = self._now()
        try:
            positions = self._adapter.get_positions()
            if positions is None:
                raise RuntimeError("get_positions returned None")
        except Exception as exc:  # noqa: BLE001
            res.query_completed_at = self._now()
            res.state = MisState.BROKER_STATE_UNAVAILABLE
            res.deadline_preserved = False
            self._emit(MisState.BROKER_STATE_UNAVAILABLE,
                       f"{which}: broker position query failed: {exc}", critical=True)
            self._store_result(today, which, res)
            return res
        res.query_completed_at = self._now()

        candidates = self._find_open_mis_positions_for_auto_squareoff(positions)
        self._log.info(
            "MIS_AUTO_SQUAREOFF_SCAN",
            extra={
                "pass": which,
                "mis_candidates": len(candidates),
                "cnc_excluded": sum(
                    1 for p in positions
                    if self._product_of(p) == "CNC" and self._qty_of(p) != 0),
                "co_excluded": sum(
                    1 for p in positions
                    if self._product_of(p) == "CO" and self._qty_of(p) != 0),
                "unknown_product_excluded": sum(
                    1 for p in positions
                    if self._product_of(p) not in (MIS_PRODUCT, "CNC", "CO")
                    and self._qty_of(p) != 0),
            },
        )

        if not candidates:
            res.state = MisState.NO_MIS
            self._log.info("mis_autosquareoff %s: FLAT (0 MIS positions)", which)
            self._store_result(today, which, res)
            return res

        res.state = MisState.MIS_FOUND

        # ── C-1/C-2: pre-flight budget check. It NEVER skips the exit. ──
        remaining_ms = int((hard_deadline - self._now()).total_seconds() * 1000)
        res.remaining_budget_at_start_ms = remaining_ms
        estimate_ms = self._p2_fixed_ms + self._p2_per_symbol_ms * len(candidates)
        if remaining_ms < estimate_ms:
            res.deadline_preserved = False
            self._emit(
                MisState.DEADLINE_BREACH_ANTICIPATED,
                f"{which}: remaining={remaining_ms}ms < estimate={estimate_ms}ms "
                f"for {len(candidates)} symbol(s); PROCEEDING ANYWAY",
                critical=True,
            )
            # A late exit is far better than no exit. There is deliberately NO
            # skip-if-insufficient branch here.

        self._execute_mis_auto_squareoff(which, candidates, res, hard_deadline)
        self._verify_mis_positions_closed(which, res, hard_deadline)

        if which == PASS_1 and self._now() >= self._at(now, self._t.check_2):
            self._pass_1_ran_past_check_2[today] = True
            res.ran_past_check_2 = True

        res.completion_time = self._now()
        self._store_result(today, which, res)
        return res

    # ── eligibility ──────────────────────────────────────────────────────────

    @staticmethod
    def _product_of(p) -> str:
        """
        The AUTHORITATIVE product is the broker position's own field. Never the
        strategy intent, never GTT presence, never trades.product (which does not
        exist — HAZ-4).

        An ABSENT product resolves to "" and is therefore EXCLUDED. This is
        deliberate: the live adapter defaults an absent product to "" while the
        PAPER adapter defaults it to "MIS" (zerodha_adapter.py:1204 vs :1234), so
        paper is the PERMISSIVE side on a safety boundary. Reading the attribute
        without re-defaulting means a missing product is excluded in BOTH modes.
        """
        v = getattr(p, "product", None)
        if v is None and isinstance(p, dict):
            v = p.get("product")
        return str(v).strip().upper() if v else ""

    @staticmethod
    def _qty_of(p) -> int:
        v = getattr(p, "qty", None)
        if v is None and isinstance(p, dict):
            v = p.get("qty", 0)
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _symbol_of(p) -> str:
        v = getattr(p, "symbol", None)
        if v is None and isinstance(p, dict):
            v = p.get("symbol")
        return str(v).strip() if v else ""

    def _find_open_mis_positions_for_auto_squareoff(self, positions) -> List[dict]:
        """
        ELIGIBLE: broker position exists AND explicit product == "MIS" AND qty != 0
                  AND a truthy symbol.
        NOT ELIGIBLE: CNC, CO, NRML, missing/unknown product, zero qty.

        qty != 0 — NOT qty > 0. A SHORT (negative qty) is eligible and closes with
        a BUY. 27-Aug had TATAPOWER MIS qty -1.
        """
        out: List[dict] = []
        for p in positions:
            sym = self._symbol_of(p)
            if not sym:
                continue
            if self._product_of(p) != MIS_PRODUCT:
                continue
            qty = self._qty_of(p)
            if qty == 0:
                continue
            out.append({"symbol": sym, "qty": qty})
        return sorted(out, key=lambda r: r["symbol"])

    # ── execution ────────────────────────────────────────────────────────────

    def _execute_mis_auto_squareoff(
        self, which: str, candidates: List[dict], res: PassResult,
        hard_deadline: datetime,
    ) -> None:
        """
        PER SYMBOL: cancel that symbol's resting MIS protective orders -> VERIFY
        the cancellation -> only then submit the exit.

        MIS SL/TGT are plain orders, NOT a broker-side OCO. If an exit is
        submitted while a protective order for the same symbol is still live,
        BOTH can fill and a long 1 becomes a short 1.

        Only that symbol's MIS orders are cancelled. Never "all pending orders",
        never anything CNC, never a GTT.
        """
        for i, cand in enumerate(candidates):
            sym = cand["symbol"]
            qty = cand["qty"]
            outcome = SymbolOutcome(
                symbol=sym, state=MisState.MIS_FOUND, broker_qty_at_start=qty,
                # LONG -> SELL, SHORT -> BUY.
                exit_side="SELL" if qty > 0 else "BUY",
                # FRESH BROKER POSITION QUANTITY IS THE REMAINING QUANTITY.
                # Never trades.qty_filled: that column is maintained toward
                # broker truth by the reconciler's CHECK 4 CAS
                # (order_reconciler.py:2408) but only EVENTUALLY — it is as fresh
                # as the last reconcile cycle. Two minutes from a hard cutoff this
                # pass must not depend on reconciler timing. And never
                # local_filled - broker_remaining: no double-subtraction.
                requested_qty=abs(qty),
            )

            try:
                resting = self._store.get_open_mis_exit_orders_for_symbol(sym)
            except Exception as exc:  # noqa: BLE001
                outcome.state = MisState.CANCEL_FAILED
                outcome.detail = f"resting-order lookup failed: {exc}"
                self._emit(MisState.CANCEL_FAILED,
                           f"{which} {sym}: {outcome.detail}", critical=True)
                res.symbols.append(outcome)
                continue

            cancel_ok = True
            for row in resting:
                oid = row["order_id"] if not hasattr(row, "order_id") else row.order_id
                variety = (row["variety"] if not hasattr(row, "variety")
                           else row.variety) or "regular"
                try:
                    r = self._adapter.cancel_order(str(oid), variety=str(variety))
                    if not getattr(r, "success", False):
                        cancel_ok = False
                        outcome.detail = (
                            f"cancel {oid} rejected: {getattr(r, 'reason', '')}")
                        break
                except Exception as exc:  # noqa: BLE001
                    cancel_ok = False
                    outcome.detail = f"cancel {oid} raised: {exc}"
                    break

            if cancel_ok and resting:
                cancel_ok = self._verify_cancelled(resting)
                if not cancel_ok:
                    outcome.detail = "cancellation not confirmed at broker"

            if not cancel_ok:
                # A double exit is worse than a late one. Do NOT submit blindly.
                # F1: but the cancels may ALREADY have taken effect, so returning
                # here is what left ANANTRAJ naked. Put the stop back first.
                restored = self._restore_protection(sym, resting, which)
                outcome.state = MisState.CANCEL_FAILED
                self._emit(MisState.CANCEL_FAILED,
                           f"{which} {sym}: {outcome.detail}; exit NOT submitted; "
                           f"{restored}",
                           critical=True)
                res.symbols.append(outcome)
                continue

            try:
                placed = self._adapter.place_order(
                    symbol=sym,
                    side=outcome.exit_side,
                    qty=outcome.requested_qty,
                    price=0.0,
                    order_type=PASS_2_EXIT_PROTOCOL if which == PASS_2 else "MARKET",
                    intent="INTRADAY",
                    tag=f"mis_autosq_{which.lower()}",
                )
                outcome.broker_order_id = str(getattr(placed, "broker_order_id", ""))
                outcome.state = MisState.EXIT_SUBMITTED
            except Exception as exc:  # noqa: BLE001
                # F1: the protective orders were cancelled moments ago and the
                # exit was refused by the broker. Without a restore this is the
                # naked position, arrived at from the other direction.
                restored = self._restore_protection(sym, resting, which)
                outcome.state = MisState.EXIT_REJECTED
                outcome.detail = f"place_order raised: {exc}; {restored}"
                self._emit(MisState.EXIT_REJECTED,
                           f"{which} {sym}: {outcome.detail}", critical=True)

            res.symbols.append(outcome)

            # Per-SYMBOL stagger, not after the last one — mirrors the EOD path's
            # measured placement (eod_squareoff.py:1378).
            if i < len(candidates) - 1 and self._inter_order_delay_sec > 0:
                time.sleep(self._inter_order_delay_sec)

    def _restore_protection(self, sym: str, resting, which: str) -> str:
        """F1 (03-Sep-2026): put the stop back when the exit was NOT submitted.

        THE DEFECT THIS EXISTS FOR. The routine cancels the protective orders
        FIRST and only then decides whether it can exit. Every failure after that
        point -- cancel not confirmed, no LTP, broker rejection, exception,
        deadline -- previously returned with the stop cancelled and no sell
        placed. There was no undo. Measured twice: 02-Sep COALINDIA and 03-Sep
        ANANTRAJ, and on 03-Sep the position stayed naked until a human closed it.

        This is the undo. It re-places the SL leg with the SAME parameters it had,
        read from the local orders row, so nothing is recomputed and no new price
        model is introduced. The TGT is deliberately NOT restored: it is upside,
        not protection, and a second resting sell is a risk in its own right.

        Returns a short detail string for the alert. Never raises -- see
        _row_get: this runs inside an already-failing path, and an exception here
        would mask the original failure and skip its alert.
        """
        try:
            return self._restore_protection_inner(sym, resting, which)
        except Exception as exc:  # noqa: BLE001 -- the contract is "never raises"
            self._log.critical(
                "mis_autosquareoff RESTORE raised for %s: %s -- POSITION MAY BE "
                "UNPROTECTED, MANUAL ACTION REQUIRED", sym, exc,
            )
            return f"RESTORE ERRORED ({exc}) -- POSITION MAY BE UNPROTECTED"

    def _restore_protection_inner(self, sym: str, resting, which: str) -> str:
        """The body of _restore_protection. Called only through it."""
        # (1) IDEMPOTENCY -- never stack a second protective order.
        already: Optional[bool] = None
        try:
            for o in (self._adapter.get_open_orders() or []):
                if str(o.get("symbol", "")) == sym and float(
                        o.get("trigger_price") or 0) > 0:
                    already = True
                    break
            else:
                already = False
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "mis_autosquareoff: open-order read failed during restore for %s "
                "(%s) -- proceeding", sym, exc,
            )
            already = None   # unknown

        if already is True:
            return "protection already resting at broker; no restore needed"

        # `already is None` (unknown) -> PROCEED. Leaving a position genuinely
        # unprotected is worse than a duplicate leg, and a duplicate is already
        # caught downstream by the reconciler's one-live-SL invariant. This
        # mirrors G5b's documented fail-safe for an unavailable snapshot.

        # (2) Recover the ORIGINAL SL parameters. Not recomputed -- restored.
        sl_row = None
        for row in resting:
            if str(_row_get(row, "leg", "")).upper() != "SL":
                continue
            trade_id = _row_get(row, "trade_id", "")
            oid = str(_row_get(row, "order_id", ""))
            try:
                for o in (self._store.get_orders_for_trade(str(trade_id)) or []):
                    if str(_row_get(o, "order_id", "")) == oid:
                        sl_row = o
                        break
            except Exception as exc:  # noqa: BLE001
                self._log.error(
                    "mis_autosquareoff: could not read SL order %s for restore: %s",
                    oid, exc,
                )
            if sl_row is not None:
                break

        if sl_row is None:
            return "RESTORE FAILED: original SL parameters not found"

        # (3) Re-place it, exactly as it was.
        try:
            qty = int(_row_get(sl_row, "qty_requested", 0) or 0)
            placed = self._adapter.place_order(
                symbol=sym,
                side=str(_row_get(sl_row, "transaction_type", "")),
                qty=qty,
                price=float(_row_get(sl_row, "price", 0.0) or 0.0),
                order_type=str(_row_get(sl_row, "order_type", "SL")),
                intent="INTRADAY",
                tag=truncate_tag_for_broker(str(_row_get(sl_row, "trade_id", ""))),
                trigger_price=float(_row_get(sl_row, "trigger_price", 0.0) or 0.0),
                variety=str(_row_get(sl_row, "variety", "regular") or "regular"),
            )
            bid = str(getattr(placed, "broker_order_id", ""))
            self._log.critical(
                "mis_autosquareoff RESTORE: %s %s re-placed protective SL "
                "broker_order_id=%s after %s failed to submit an exit",
                which, sym, bid, which,
            )
            return f"protection RESTORED (order {bid})"
        except Exception as exc:  # noqa: BLE001
            self._log.critical(
                "mis_autosquareoff RESTORE FAILED for %s: %s -- POSITION IS "
                "UNPROTECTED, MANUAL ACTION REQUIRED", sym, exc,
            )
            return f"RESTORE FAILED ({exc}) -- POSITION UNPROTECTED"

    def _verify_cancelled(self, resting) -> bool:
        """Confirm at the broker. 'Cancel accepted' is not 'cancel effective'.

        F2 (03-Sep-2026): poll until terminal or a bounded deadline.

        The mechanism here was never wrong -- it reads the BROKER's order history,
        which is the right source of truth. The defect was TIMING: it polled ONCE,
        27-53 ms after the cancel, and treated "not yet terminal" as failure. On
        03-Sep the same orders read terminal 1.115 s later. Measured result of the
        single poll: 3 of 3 CANCEL_FAILED, on 02-Sep (COALINDIA) and 03-Sep
        (ANANTRAJ) -- and each time the protective orders had ALREADY been
        cancelled, so the position was left naked.

        A False return is now a real, settled negative rather than a race, and the
        caller must RESTORE protection (F1) -- never proceed blind.
        """
        deadline = time.monotonic() + _CANCEL_SETTLE_DEADLINE_SEC
        for row in resting:
            oid = row["order_id"] if not hasattr(row, "order_id") else row.order_id
            while True:
                hist = None
                try:
                    hist = self._adapter.get_order_history(str(oid))
                except Exception:  # noqa: BLE001
                    hist = None   # a failed READ is not evidence; keep polling
                if hist:
                    status = str(getattr(hist[-1], "status", "")).upper()
                    if status in ("CANCELLED", "REJECTED", "COMPLETE"):
                        break     # this order is settled; on to the next
                if time.monotonic() >= deadline:
                    self._log.warning(
                        "mis_autosquareoff: cancel of %s not confirmed terminal "
                        "within %.1fs -- treating as NOT cancelled",
                        oid, _CANCEL_SETTLE_DEADLINE_SEC,
                    )
                    return False
                time.sleep(_CANCEL_SETTLE_POLL_SEC)
        return True

    def _verify_mis_positions_closed(
        self, which: str, res: PassResult, hard_deadline: datetime,
    ) -> None:
        """
        Re-read FRESH broker state. 'Order accepted' is not 'position closed'.
        A query failure here is RECONCILIATION_UNKNOWN, never 'flat'.
        """
        try:
            positions = self._adapter.get_positions()
            if positions is None:
                raise RuntimeError("get_positions returned None")
        except Exception as exc:  # noqa: BLE001
            res.state = MisState.RECONCILIATION_UNKNOWN
            res.deadline_preserved = False
            self._emit(MisState.RECONCILIATION_UNKNOWN,
                       f"{which}: post-exit verification query failed: {exc}",
                       critical=True)
            return

        still_open = self._find_open_mis_positions_for_auto_squareoff(positions)
        now = self._now()
        if still_open:
            res.state = MisState.MIS_REMAINS
            if now >= hard_deadline:
                res.state = MisState.DEADLINE_BREACH
                res.deadline_preserved = False
            self._emit(
                res.state,
                f"{which}: {len(still_open)} MIS position(s) still open: "
                f"{[r['symbol'] for r in still_open]}",
                critical=True,
            )
            return

        res.state = MisState.EXIT_FILLED
        if now >= hard_deadline:
            # Flat, but we crossed the cutoff getting there. Not ordinary success.
            res.state = MisState.DEADLINE_BREACH
            res.deadline_preserved = False
            self._emit(MisState.DEADLINE_BREACH,
                       f"{which}: flat, but the cutoff was crossed", critical=True)

    def _retry_remaining_mis_positions(self, which: str) -> PassResult:
        """PASS 2 is the retry of PASS 1, from FRESH broker state."""
        now = self._now()
        return self._run_pass(which, now, self._at(now, self._t.check_2))

    # ── PASS 1's capped grace (R-2) ──────────────────────────────────────────

    def effective_grace_sec(self, now: Optional[datetime] = None) -> int:
        """
        R-2 with A-1's floor:
            effective_grace = max(0, min(limit_grace_sec, CHECK_2 - now - margin))

        The floor makes a negative sleep impossible. And the cap has a property
        worth naming: at extreme lateness it returns 0, so PASS 1 promotes to
        MARKET immediately — PASS 1 SELF-DEGRADES from price-seeking into PASS 2's
        certainty behaviour as the deadline approaches. That falls out of the
        formula for free; PASS_1_DEGRADED_TO_MARKET records it.
        """
        now = now or self._now()
        check_2_dt = self._at(now, self._t.check_2)
        room = (check_2_dt - now).total_seconds() - self._t.margin_sec
        grace = max(0, min(self._limit_grace_sec, int(room)))
        if grace == 0:
            self._emit(MisState.PASS_1_DEGRADED_TO_MARKET,
                       "PASS 1 grace capped to 0; immediate MARKET promotion")
        return grace

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _store_result(self, d: date, which: str, res: PassResult) -> None:
        with self._lock:
            self._results[(d, which)] = res

    def result(self, d: date, which: str) -> Optional[PassResult]:
        with self._lock:
            return self._results.get((d, which))

    def _emit(self, state: str, detail: str, *, critical: bool = False) -> None:
        # The LOG happens first and unconditionally: it is the source of truth.
        # Nothing below may prevent, replace or downgrade it. F is an
        # observability mechanism, not control authority -- "DEADLINE_BREACH
        # occurred" and "F delivery failed" are two separate records and must
        # never collapse into "no alert" / "no incident" / "warning only" / PASS.
        if critical:
            self._log.critical("mis_autosquareoff %s: %s", state, detail)
        else:
            self._log.info("mis_autosquareoff %s: %s", state, detail)

        # F (D-1b). Invoked AFTER the log, in its own guard, so a broken or absent
        # notifier changes nothing about the CRITICAL that just happened.
        if critical and self._critical_sink is not None:
            try:
                self._critical_sink(state, detail)
            except Exception as exc:  # noqa: BLE001
                self._log.error(
                    "mis_autosquareoff: F sink failed for %s (the CRITICAL above "
                    "stands regardless): %s", state, exc)

        if critical and self._notifier is not None:
            try:
                self._notifier.send(
                    severity="CRITICAL",
                    title=f"MIS AUTO-SQUAREOFF — {state}",
                    message=detail,
                )
            except Exception:  # noqa: BLE001
                self._log.error("mis_autosquareoff: notifier failed for %s", state)
