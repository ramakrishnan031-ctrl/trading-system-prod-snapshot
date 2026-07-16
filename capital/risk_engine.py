"""
capital/risk_engine.py -- Trading System v2

Purpose:
    Portfolio-level pre-trade approval gate. Validates a proposed trade
    against ALL portfolio limits in one atomic check. Returns approve/reject
    with reason. No state mutation. No side effects. Pure decision function.

Locked Design Decisions:
    RE1  -- Single pre-trade approval gate. Returns ApprovalResult.
            No state mutation. No side effects. Pure decision function.
    RE2  -- No hardcoded 1% estimate. Uses SizingResult.margin_required
            and SizingResult.risk_amount directly (schism eliminated).
    RE3  -- Constructor: RiskEngine(fund_manager, state_store,
            max_open_positions, max_daily_trades, max_sector_exposure_pct,
            max_consecutive_losses, daily_loss_limit_pct, sector_lookup_fn,
            logger, kill_switch=None).
    RE4  -- API: approve(symbol, side, intent, sizing_result, signal_id)
            -> ApprovalResult (frozen dataclass).
    RE5  -- Check sequence (10 checks, short-circuit on first fail):
            KILL_SWITCH, SIZING_VALID, CAPITAL, OPEN_POSITIONS,
            DAILY_TRADES, CONSECUTIVE_LOSSES, DAILY_LOSS,
            SECTOR_EXPOSURE, CONTRARY_POSITION, DUPLICATE_SYMBOL.
    RE6  -- SECTOR_EXPOSURE counts BOTH open and in-flight (RE6 audit fix).
    RE7  -- DAILY_LOSS uses fund_manager.get_snapshot().daily_realized_pnl.
            risk_engine is a gate, not a monitor.
    RE8  -- kill_switch=None: KILL_SWITCH check skipped, WARNING logged once
            at construction. Last-mile check; order_placer re-checks too.
    RE9  -- sector_lookup_fn exception: treat as "UNKNOWN", log WARNING.
    RE10 -- Consecutive losses: net_pnl < -1e-6 (audit fix: breakeven != loss).
    RE11 -- Snapshot consistency: all reads happen in approve() body.
            ApprovalResult.snapshot has all 9 fields for every call.
    RE12 -- Approval logged INFO. No state_store writes. Pure read + decide.
    RE14 -- Layer 4 (capital/). Imports: stdlib, core.logger, core.exceptions,
            capital.fund_manager (type), capital.position_sizer (type),
            core.state_store (for queries). No kill_switch import (injected).
    RE16 -- Deterministic. Date read once per approve() call. Same inputs
            + same state -> same ApprovalResult.
    RE17 -- NOT in scope: capital reservation, order placement, position
            monitoring, unrealized P&L tracking.

What This Module Does NOT Do:
    - Does not reserve or release capital (fund_manager does that)
    - Does not place orders (order_manager does that)
    - Does not monitor open positions (order_monitor does that)
    - Does not track unrealized P&L
    - Does not write to state_store or emit events
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, TYPE_CHECKING

from core.time_authority import now_ist

if TYPE_CHECKING:
    from capital.fund_manager import FundManager, CapitalSnapshot
    from capital.position_sizer import SizingResult
    from core.state_store import StateStore

# DUP-1 (2026-04-26 audit): _IST removed; never read locally.

# Threshold for "net loss" per RE10 audit fix.
# net_pnl >= -1e-6 is treated as breakeven, not a loss.
_LOSS_THRESHOLD = -1e-6


# ─────────────────────────────────────────────────────────────────────────────
# Return type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ApprovalResult:
    """
    Frozen result of a pre-trade approval check (RE4).

    Fields:
        approved:      True if all checks passed and the trade may proceed.
        reason:        Human-readable explanation (always non-empty).
        failed_check:  Name of the first check that failed; "" on approval.
        checks_run:    Ordered list of check names executed in this call.
                       Shorter than 9 on rejection (short-circuit, RE5).
        snapshot:      Capital + counts at decision time. Always has all 9
                       keys regardless of which check failed (RE11):
                         available_intraday, available_positional,
                         open_count, in_flight_count, daily_trades_count,
                         daily_realized_pnl, sector_exposure_pct,
                         consecutive_losses, kill_switch_active
    """
    approved: bool
    reason: str
    failed_check: str
    checks_run: List[str]
    snapshot: dict


# ─────────────────────────────────────────────────────────────────────────────
# RiskEngine
# ─────────────────────────────────────────────────────────────────────────────

class RiskEngine:
    """
    Pre-trade approval gate for portfolio-level risk limits (RE1-RE17).

    Thread-safe: stateless after construction; approve() reads shared state
    but never mutates it. Multiple threads may call approve() concurrently.

    Usage::
        engine = RiskEngine(
            fund_manager=fm,
            state_store=store,
            # BUILD 1 (#A.4): example values aligned to the live config
            # (5/10/.../0.03) so this snippet can't seed looser stale caps.
            # NOTE: RiskEngine takes NO defaults — all caps are required, so a
            # component built without config fails fast rather than running loose.
            max_open_positions=5,
            max_daily_trades=10,
            max_sector_exposure_pct=0.40,
            max_consecutive_losses=4,
            daily_loss_limit_pct=0.03,
            sector_lookup_fn=lambda s: instrument_cache.sector_for(s),
            logger=get_logger(__name__),
            kill_switch=ks,          # optional; None disables KILL_SWITCH check
        )
        result = engine.approve("RELIANCE", "BUY", "INTRADAY", sizing_result, signal_id)
        if result.approved:
            reservation = fm.reserve(...)
    """

    def __init__(
        self,
        fund_manager: "FundManager",
        state_store: "StateStore",
        max_open_positions: int,
        max_daily_trades: int,
        max_sector_exposure_pct: float,
        max_consecutive_losses: int,
        daily_loss_limit_pct: float,
        sector_lookup_fn: Callable[[str], str],
        logger: logging.Logger,
        kill_switch: Optional[object] = None,
        # SLICE2.5-PHASE-3 (A): SEPARATE delivery (CNC) count caps. Optional with
        # defaults so existing callers/tests are unaffected; main.py wires the
        # configured values. Enforced ONLY for a delivery entry (bucket=="positional").
        max_open_delivery_positions: int = 3,
        max_daily_delivery_trades: int = 5,
        # B-1 (02-Jul): enforce the unrealized-MTM term in the DAILY_LOSS gate.
        # Default False = SHADOW (log would_reject, enforce realized-only).
        daily_loss_include_unrealized: bool = False,
        # F1 (16-Jul): SECTOR_EXPOSURE gate mode. "observe" (DEFAULT) LOGS a would-reject but
        # does NOT reject; "enforce" rejects as designed. Default observe so populating
        # trades.sector activates the cap in log-only mode until a soak + Rama's approval.
        sector_cap_mode: str = "observe",
    ) -> None:
        self._fm = fund_manager
        self._store = state_store
        self._max_open = max_open_positions
        self._max_daily = max_daily_trades
        self._max_sector_pct = max_sector_exposure_pct
        self._max_consec = max_consecutive_losses
        self._daily_loss_pct = daily_loss_limit_pct
        self._daily_loss_include_unrealized = daily_loss_include_unrealized
        self._sector_cap_mode = sector_cap_mode if sector_cap_mode in ("observe", "enforce") else "observe"
        self._max_open_delivery = max_open_delivery_positions   # PHASE-3 (A)
        self._max_daily_delivery = max_daily_delivery_trades     # PHASE-3 (A)
        self._sector_fn = sector_lookup_fn
        self._log = logger
        self._ks = kill_switch

        if kill_switch is None:
            self._log.warning(
                "RiskEngine: kill_switch=None; KILL_SWITCH check will be skipped. "
                "Ensure kill_switch is injected before live trading."
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def approve(
        self,
        symbol: str,
        side: str,
        intent: str,
        sizing_result: "SizingResult",
        signal_id: str,
        processor_in_flight_count: int = 0,
    ) -> ApprovalResult:
        """
        Run all portfolio-level checks for the proposed trade (RE4, RE5, FIX-018).

        Reads fund_manager snapshot and state_store queries once at the start
        for consistency (RE11, RE16). Runs checks in order, short-circuits on
        the first failure. Logs every call at INFO level (RE12).

        Args:
            symbol:        Instrument symbol (e.g., "RELIANCE").
            side:          "BUY" or "SELL".
            intent:        Product intent ("INTRADAY", "DELIVERY", etc.).
            sizing_result: SizingResult from position_sizer. Contains the
                           margin_required and risk_amount that will be used.
                           Schism eliminated: risk_engine sees identical numbers
                           as position_sizer (RE2).
            signal_id:     signal_id from the originating webhook (for logging).
            processor_in_flight_count: FIX-018 TOCTOU fix. Number of signals
                           currently in the signal_processor pipeline (between
                           approve() and DB insert). Added to DB in_flight_count
                           when checking max_open_positions to prevent race.

        Returns:
            ApprovalResult with approved=True/False, reason, failed_check,
            checks_run list, and snapshot dict.
        """
        # Read date once (RE16: no repeated clock reads within a single call)
        today = now_ist().date().isoformat()

        # ── Read all state upfront for snapshot consistency (RE11) ────────────
        snap = self._fm.get_snapshot()

        # Bug E (P0 2026-06-15): count OPEN + PARTIAL + PENDING_FILL in ONE
        # atomic query for the position-cap decision, eliminating the TOCTOU
        # window where a PENDING_FILL -> OPEN transition between two separate
        # counts left a position uncounted (cap could be exceeded). The two
        # separate reads are kept only for the observability snapshot below.
        active_count = self._store.count_active_positions()
        open_count = self._store.count_open_positions()
        in_flight_count = self._store.count_in_flight_orders()
        daily_count = self._store.count_trades_today(today)
        # Bug B: settled-today (executed minus PENDING_FILL) is the DB-truth half
        # of the reservation-aware daily cap; count_live_reservations() supplies
        # the in-flight half. Read here so all date-scoped reads happen once (RE16).
        settled_today = self._store.count_settled_trades_today(today)

        # SLICE2.5-PHASE-3 (A): delivery-scoped counts, read ONLY for a delivery
        # (CNC) entry (sizing_result.bucket=="positional"). For an intraday entry —
        # and ALWAYS while force_intraday_only coerces every strategy to INTRADAY —
        # this guard is False, so ZERO extra DB queries hit the hot intraday path.
        is_delivery_entry = sizing_result.bucket == "positional"
        open_delivery_count = (
            self._store.count_open_delivery_positions() if is_delivery_entry else 0)
        daily_delivery_count = (
            self._store.count_daily_delivery_trades(today) if is_delivery_entry else 0)

        # Consecutive loss streak (RE10)
        # FIX-183: scope the streak to TODAY. A cross-day streak was a deadlock —
        # it blocks entries, but breaking it needs a winning trade, which the
        # block makes impossible (yesterday's 2-loss EOD halted all signals today).
        pnls = self._store.recent_trade_pnls(self._max_consec + 1, today=today)
        consec = self._count_trailing_losses(pnls)

        # Sector lookup — RE9: exception → "UNKNOWN", log WARNING, do not reject
        sector = self._resolve_sector(symbol)
        existing_sector_margin = self._store.sector_exposure(sector)   # DB truth (trade rows only)
        # FIX-185-class TOCTOU close: harden the SECTOR_EXPOSURE gate with reserved-not-placed
        # reservations. The snapshot's sector_pct below intentionally keeps DB truth (reporting
        # unchanged); ONLY the gate consumes the effective (hardened) value.
        effective_sector_margin = self._effective_sector_margin(sector, existing_sector_margin)

        # Duplicate symbol check data
        has_dup = self._store.has_active_position(symbol)

        # FIX-019: Wash trade prevention - get existing position direction if any
        active_direction = self._store.get_active_position_direction(symbol)

        # Kill switch active state
        kill_active: bool = False
        if self._ks is not None:
            kill_active = bool(self._ks.is_active())

        # Sector exposure as fraction of total (current, before this trade)
        sector_pct = (
            existing_sector_margin / snap.total
            if snap.total > 0 else 0.0
        )

        # ── Build snapshot (RE11: all 9 fields, always) ───────────────────────
        snapshot = {
            "available_intraday":   snap.intraday_avail,
            "available_positional": snap.positional_avail,
            "open_count":           open_count,
            "in_flight_count":      in_flight_count,
            "daily_trades_count":   daily_count,
            "daily_realized_pnl":   snap.daily_realized_pnl,
            "sector_exposure_pct":  sector_pct,
            "consecutive_losses":   consec,
            "kill_switch_active":   kill_active,
        }

        # ── Run checks in order, short-circuit on first failure (RE5, FIX-018, FIX-019) ──
        checks_run: List[str] = []
        result = self._run_checks(
            checks_run, snapshot, snap, sizing_result,
            active_count, open_count, in_flight_count, daily_count,
            settled_today,
            consec, effective_sector_margin, has_dup, kill_active,
            processor_in_flight_count,
            symbol, side, active_direction,
            open_delivery_count, daily_delivery_count,   # PHASE-3 (A)
            sector=sector,                               # F1 (16-Jul): observe-mode log
        )

        # ── Log every call at INFO (RE12) ─────────────────────────────────────
        self._log.info(
            "risk_engine.approve signal_id=%s symbol=%s side=%s intent=%s "
            "approved=%s failed_check=%s checks_run=%d margin=%.2f",
            signal_id, symbol, side, intent,
            result.approved, result.failed_check or "none",
            len(checks_run), sizing_result.margin_required,
        )

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _effective_sector_margin(
        self, sector: str, existing_sector_margin: float,
    ) -> float:
        """
        FIX-185-class TOCTOU close for the SECTOR_EXPOSURE gate.

        `existing_sector_margin` (StateStore.sector_exposure) sums TRADE ROWS only, so a
        RESERVED-NOT-PLACED reservation — fund_manager.reserve() succeeded but its PENDING_FILL
        trade row is written later, OUTSIDE portfolio_lock — is invisible here, and two concurrent
        same-sector signals can each read stale exposure and together breach _max_sector_pct.
        approve() runs inside portfolio_lock (same seam the FIX-185 count gate already reads
        reservations from), so the live reservation set is a consistent read.

        Partition so every unit is counted EXACTLY ONCE (mirrors the FIX-185 count partition):
          • open_partial = sector_exposure(sector, statuses=("OPEN","PARTIAL")) — trade rows whose
            reservation was already popped at fill, hence NOT in get_live_reservations().
          • reserved     = Σ margin of live reservations in this sector = reserved-not-placed
            PLUS any PENDING_FILL still holding its reservation.
        open_partial + reserved therefore never double-counts a PENDING_FILL.

        Returns max(existing, open_partial + reserved): floored by DB truth (covers a restart that
        lost in-memory reservations but kept PENDING_FILL rows) and can ONLY harden, never loosen.
        Degrades to DB truth — but LOUDLY (never silently) — if the fund_manager predates
        get_live_reservations() or the read raises.
        """
        get_res = getattr(self._fm, "get_live_reservations", None)
        if not callable(get_res):
            self._log.warning(
                "risk_engine.sector_toctou_degraded sector=%s reason=no_get_live_reservations "
                "-- SECTOR_EXPOSURE using DB truth only; reserved-not-placed window NOT closed",
                sector,
            )
            return existing_sector_margin
        try:
            reserved = sum(
                r.margin for r in get_res().values()
                if self._resolve_sector(r.symbol) == sector
            )
            open_partial = self._store.sector_exposure(sector, statuses=("OPEN", "PARTIAL"))
            return max(existing_sector_margin, open_partial + reserved)
        except Exception as exc:   # hardening must never break approve(); degrade loudly
            self._log.warning(
                "risk_engine.sector_toctou_degraded sector=%s reason=%r "
                "-- SECTOR_EXPOSURE using DB truth only; reserved-not-placed window NOT closed",
                sector, exc,
            )
            return existing_sector_margin

    def _run_checks(
        self,
        checks_run: List[str],
        snapshot: dict,
        snap: "CapitalSnapshot",
        sizing_result: "SizingResult",
        active_count: int,
        open_count: int,
        in_flight_count: int,
        daily_count: int,
        settled_today: int,
        consec: int,
        effective_sector_margin: float,
        has_dup: bool,
        kill_active: bool,
        processor_in_flight_count: int,
        symbol: str,
        side: str,
        active_direction: Optional[str],
        open_delivery_count: int = 0,      # PHASE-3 (A): delivery-scoped open count
        daily_delivery_count: int = 0,     # PHASE-3 (A): delivery-scoped today count
        sector: str = "UNKNOWN",           # F1 (16-Jul): resolved sector, for the observe-mode log
    ) -> ApprovalResult:
        """Execute checks in RE5 + FIX-018 + FIX-019 order; return the first failure or approval."""

        def reject(check: str, reason: str) -> ApprovalResult:
            return ApprovalResult(
                approved=False,
                reason=reason,
                failed_check=check,
                checks_run=list(checks_run),
                snapshot=snapshot,
            )

        # 1. KILL_SWITCH (only when kill_switch is injected, RE8)
        if self._ks is not None:
            checks_run.append("KILL_SWITCH")
            if kill_active:
                return reject("KILL_SWITCH", "Kill switch is active; no new trades permitted")

        # 2. SIZING_VALID
        checks_run.append("SIZING_VALID")
        if not sizing_result.success:
            return reject(
                "SIZING_VALID",
                f"Sizing failed: {sizing_result.reason}",
            )

        # 3. CAPITAL — bucket must have enough available margin
        checks_run.append("CAPITAL")
        bucket_avail = (
            snap.intraday_avail
            if sizing_result.bucket == "intraday"
            else snap.positional_avail
        )
        if bucket_avail < sizing_result.margin_required:
            return reject(
                "CAPITAL",
                f"Insufficient {sizing_result.bucket} capital: "
                f"available={bucket_avail:.2f}, required={sizing_result.margin_required:.2f}",
            )

        # 4. OPEN_POSITIONS — active (OPEN+PARTIAL+PENDING_FILL) + processor_in_flight < cap
        checks_run.append("OPEN_POSITIONS")
        # SLICE2.5-PHASE-3 (A): a DELIVERY (CNC) entry is capped by its OWN
        # delivery-scoped count, NOT the intraday/global cap. Keyed on
        # bucket=="positional" (the proven 1:1 proxy for "this entry will be CNC" —
        # the resolved product string is not available pre-trade); the count is
        # product-keyed (ENTRY product=='CNC'). A5 — INTENTIONAL asymmetric coupling:
        # the intraday/global branch (else) is LEFT UNCHANGED, so it still counts ALL
        # positions (incl. delivery) -> delivery DOES count toward an intraday entry's
        # cap, while intraday does NOT count toward the delivery cap. This keeps the
        # LIVE intraday cap byte-for-byte unchanged (zero regression on the money
        # path); at current capital, capital binds long before these counts, so the
        # coupling is academic. Inert while coerced (bucket is never positional then,
        # and open_delivery_count==0). Revisit full count-independence only if
        # delivery scales and it matters.
        if sizing_result.bucket == "positional":
            if open_delivery_count >= self._max_open_delivery:
                return reject(
                    "OPEN_POSITIONS",
                    f"Delivery position cap reached: {open_delivery_count} open "
                    f"delivery (CNC), max={self._max_open_delivery}",
                )
        else:
            # FIX-018: processor_in_flight_count prevents the TOCTOU race where
            # concurrent signals both pass this check before either inserts into DB.
            # Bug E (P0 2026-06-15): the DB portion is now a SINGLE atomic count
            # (active_count) instead of count_open + count_in_flight, closing the
            # window where a PENDING_FILL->OPEN transition between the two queries
            # left a position uncounted and let the cap be exceeded.
            # FIX-181 (off-by-one): processor_in_flight_count is PRE-incremented to
            # include THIS candidate (signal_processor bumps _in_flight_count before
            # calling approve), while active_count counts only positions already in
            # the DB (the candidate is not inserted yet). So active_total ==
            # max_open means "candidate + (max_open - 1) existing" = max_open total
            # -> ALLOW. Only active_total > max_open exceeds the cap. The previous
            # `>=` rejected the legitimate final slot (max=3 only ever held 2).
            legacy_total = active_count + processor_in_flight_count

            # FIX-185 (hard cap / restart-burst TOCTOU): the processor_in_flight
            # snapshot is taken in signal_processor at increment time, BEFORE the
            # candidate acquires portfolio_lock, so a burst of signals admitted at
            # restart could under-count it and let the cap be exceeded (observed: 6
            # open vs max 5 on the 18-Jun restart). Add an AUTHORITATIVE in-flight
            # count that does not rely on that snapshot: every accepted entry holds a
            # fund_manager reservation from reserve() until the entry FILLS (commit
            # pops it exactly as status flips to OPEN). So OPEN/PARTIAL (open_count)
            # and live reservations (reserve->fill, includes reserved-not-placed and
            # PENDING_FILL) partition all in-flight/open positions with no overlap and
            # no gap. active_count (DB truth, includes PENDING_FILL) is kept as a
            # floor so a PENDING_FILL row whose in-memory reservation was lost across
            # a restart is still counted. approve() runs inside portfolio_lock, so
            # this read is consistent with reserve(). +1 for THIS candidate (it has
            # not reserved or inserted yet). max() with legacy_total => can only ever
            # HARDEN the cap, never loosen it (no regression risk).
            # getattr guard: a fund_manager implementation predating FIX-185 degrades
            # gracefully to the legacy snapshot-only cap rather than crashing.
            _count_res = getattr(self._fm, "count_live_reservations", None)
            reserved_inflight = _count_res() if callable(_count_res) else 0
            authoritative_total = max(open_count + reserved_inflight, active_count) + 1

            effective_total = max(legacy_total, authoritative_total)
            if effective_total > self._max_open:
                return reject(
                    "OPEN_POSITIONS",
                    f"Position cap reached: {effective_total} active "
                    f"(db_active[open+partial+pending_fill]={active_count}, "
                    f"open_partial={open_count}, live_reservations={reserved_inflight}, "
                    f"processor_in_flight={processor_in_flight_count}), "
                    f"max={self._max_open}",
                )

        # 5. DAILY_TRADES
        # Bug B (2026-06-19): mirror the FIX-185 OPEN_POSITIONS hardening on the
        # daily cap. A bare `count_trades_today` read is TOCTOU-racy: approve()
        # runs inside portfolio_lock but the candidate's PENDING_FILL trade row is
        # inserted LATER by order_placer.place(), OUTSIDE the lock. So a burst of
        # signals each read the same pre-burst daily_count before any row exists
        # and ALL pass the cap -> daily overshoot (the 18-Jun 8-vs-5 burst, the
        # daily-cap twin of the position-cap race FIX-185 already closed).
        # reserve() runs INSIDE the lock, so counting live reservations closes the
        # same window. Partition (no double-count, exactly like FIX-185):
        #   settled_today      = today's executed trades MINUS PENDING_FILL; their
        #                        reservation was popped at fill, so NOT in _reservations.
        #   reserved_inflight  = count_live_reservations() = reserved-not-placed +
        #                        PENDING_FILL (all today's: intraday, reset daily).
        #                        The reserved-not-placed part is exactly the in-flight
        #                        burst that daily_count cannot see yet.
        # daily_count (DB truth, INCLUDES PENDING_FILL) is kept as a FLOOR for the
        # restart case where in-memory reservations were lost but PENDING_FILL rows
        # remain. max() can only HARDEN the cap, never loosen it (no regression).
        # REJECTED/FAILED/CANCELLED enter NEITHER term (FIX-181 exclusion + the
        # signal_processor reservation release), so a rejection frees the slot and
        # the next signal retries to reach max -- Rama's requirement. getattr guard
        # degrades to the legacy daily_count if fund_manager predates FIX-185.
        checks_run.append("DAILY_TRADES")
        # SLICE2.5-PHASE-3 (A): a DELIVERY (CNC) entry is capped by its OWN daily
        # delivery count (today's ENTRY product=='CNC'); an INTRADAY entry keeps the
        # existing reservation-aware global daily cap (else, UNCHANGED — same A5
        # asymmetry as OPEN_POSITIONS). Inert while coerced (daily_delivery_count==0).
        if sizing_result.bucket == "positional":
            if daily_delivery_count >= self._max_daily_delivery:
                return reject(
                    "DAILY_TRADES",
                    f"Delivery daily trade limit reached: {daily_delivery_count} "
                    f"delivery (CNC) today, max={self._max_daily_delivery}",
                )
        else:
            _count_res_daily = getattr(self._fm, "count_live_reservations", None)
            reserved_inflight_daily = _count_res_daily() if callable(_count_res_daily) else 0
            authoritative_daily = settled_today + reserved_inflight_daily
            effective_daily = max(daily_count, authoritative_daily)
            if effective_daily >= self._max_daily:
                return reject(
                    "DAILY_TRADES",
                    f"Daily trade limit reached: {effective_daily} "
                    f"(db_today={daily_count}, settled_today={settled_today}, "
                    f"live_reservations={reserved_inflight_daily}), "
                    f"max={self._max_daily}",
                )

        # 6. CONSECUTIVE_LOSSES (RE10: net_pnl < -1e-6 is a loss)
        # SLICE2.5-PHASE-3 (A): deliberately SHARED — no delivery variant. The streak
        # breaker is a portfolio-wide circuit and applies to delivery entries too.
        checks_run.append("CONSECUTIVE_LOSSES")
        if consec >= self._max_consec:
            return reject(
                "CONSECUTIVE_LOSSES",
                f"Consecutive loss limit reached: {consec} straight losses, "
                f"max={self._max_consec}",
            )

        # 7. DAILY_LOSS — abs(realized + unrealized_mtm) >= limit_pct * total (RE7, FIX-035, B-1)
        # B-1 (02-Jul): the unrealized-MTM term used to be dead (its writers were never
        # called → always 0 → realized-only). It is now populated by the reconciler's
        # 15s refresh. Enforcement is gated by daily_loss_include_unrealized:
        #   OFF (shadow) → LOG what realized+unrealized WOULD do, but ENFORCE realized-only.
        #   ON            → ENFORCE realized+unrealized (only when the MTM is FRESH).
        # If the MTM is stale/unavailable (quote outage / just-restarted), fall back to
        # realized-only + WARN — never block-all, never fabricate, never silent.
        checks_run.append("DAILY_LOSS")
        limit = self._daily_loss_pct * snap.total
        daily_pnl = snap.daily_realized_pnl
        unrealized_mtm, mtm_fresh = self._fm.get_unrealized_mtm_status()

        # The value the gate ACTUALLY enforces on: realized + unrealized only when the
        # enforce flag is ON and the MTM is fresh; realized-only otherwise.
        use_unrealized = self._daily_loss_include_unrealized and mtm_fresh
        enforced_pnl = daily_pnl + (unrealized_mtm if use_unrealized else 0.0)
        # The value that INCLUDES unrealized when fresh — for shadow observability.
        shadow_pnl = daily_pnl + (unrealized_mtm if mtm_fresh else 0.0)

        if self._daily_loss_include_unrealized and not mtm_fresh:
            self._log.warning(
                "risk_engine.daily_loss.mtm_unavailable: enforcing realized-only "
                "(unrealized MTM stale/unavailable) realized=%.2f limit=%.2f",
                daily_pnl, limit,
            )
        # Shadow observability: would realized+unrealized reject when realized-only does NOT?
        if snap.total > 0 and mtm_fresh and not use_unrealized:
            would_reject = shadow_pnl < 0 and abs(shadow_pnl) >= limit
            enforced_reject = enforced_pnl < 0 and abs(enforced_pnl) >= limit
            if would_reject and not enforced_reject:
                self._log.info(
                    "risk_engine.daily_loss.would_reject_with_unrealized: shadow=%.2f "
                    "(realized=%.2f + unrealized=%.2f) >= limit=%.2f — NOT enforced "
                    "(daily_loss_include_unrealized=false)",
                    shadow_pnl, daily_pnl, unrealized_mtm, limit,
                )

        if enforced_pnl < 0 and snap.total > 0 and abs(enforced_pnl) >= limit:
            u_note = f" + unrealized={unrealized_mtm:.2f}" if use_unrealized else " (realized-only)"
            return reject(
                "DAILY_LOSS",
                f"Daily loss limit hit: total_pnl={enforced_pnl:.2f} "
                f"(realized={daily_pnl:.2f}{u_note}), limit={limit:.2f}",
            )

        # 8. SECTOR_EXPOSURE — effective + this trade <= max_pct * total (RE6 +
        #    FIX-185-class TOCTOU: effective_sector_margin already folds in reserved-not-placed)
        checks_run.append("SECTOR_EXPOSURE")
        if snap.total > 0:
            projected = effective_sector_margin + sizing_result.margin_required
            if projected > self._max_sector_pct * snap.total:
                # F1 (16-Jul): in "observe" (default) LOG a would-reject record and CONTINUE
                # (do not reject) — behaviour-neutral while trades.sector fills and the cap is
                # soaked. In "enforce" reject as designed (the live sector cap).
                if self._sector_cap_mode == "enforce":
                    return reject(
                        "SECTOR_EXPOSURE",
                        f"Sector exposure would exceed limit: "
                        f"projected={projected:.2f} "
                        f"({projected / snap.total * 100:.1f}%), "
                        f"max={self._max_sector_pct * 100:.1f}%",
                    )
                self._log.warning(
                    "risk_engine.sector_cap_would_reject mode=observe verdict=WOULD_REJECT "
                    "symbol=%s sector=%s current_pct=%.1f%% projected_pct=%.1f%% "
                    "threshold_pct=%.1f%% projected=%.2f total=%.2f",
                    symbol, sector,
                    snapshot.get("sector_exposure_pct", 0.0) * 100.0,
                    projected / snap.total * 100.0,
                    self._max_sector_pct * 100.0,
                    projected, snap.total,
                )

        # 9. CONTRARY_POSITION — FIX-019 wash trade prevention
        # Reject if an active position (open or in-flight) exists in the OPPOSITE direction.
        # Same-direction signals are NOT blocked by this check (handled by DUPLICATE_SYMBOL).
        checks_run.append("CONTRARY_POSITION")
        if active_direction is not None:
            # Map side (BUY/SELL) to direction (LONG/SHORT) for comparison
            incoming_direction = "LONG" if side == "BUY" else "SHORT"
            is_contrary = (
                (incoming_direction == "LONG" and active_direction == "SHORT") or
                (incoming_direction == "SHORT" and active_direction == "LONG")
            )
            if is_contrary:
                self._log.warning(
                    "CONTRARY_POSITION wash trade blocked: symbol=%s incoming=%s "
                    "conflicting_position=%s",
                    symbol, incoming_direction, active_direction,
                )
                return reject(
                    "CONTRARY_POSITION",
                    f"Cannot open {incoming_direction} position: active {active_direction} "
                    f"position exists for {symbol} (wash trade prevention)",
                )

        # 10. DUPLICATE_SYMBOL — no existing open or in-flight for same symbol
        checks_run.append("DUPLICATE_SYMBOL")
        if has_dup:
            return reject(
                "DUPLICATE_SYMBOL",
                f"Active position or in-flight order already exists for symbol",
            )

        # All checks passed
        return ApprovalResult(
            approved=True,
            reason="All checks passed",
            failed_check="",
            checks_run=list(checks_run),
            snapshot=snapshot,
        )

    def _count_trailing_losses(self, pnls: List[float]) -> int:
        """
        Count the trailing consecutive losses in pnls (most-recent first).
        A loss is net_pnl < -1e-6 per RE10 audit fix.
        Breakeven trades (pnl in [-1e-6, 0]) break the streak.
        """
        count = 0
        for pnl in pnls:
            if pnl < _LOSS_THRESHOLD:
                count += 1
            else:
                break
        return count

    def _resolve_sector(self, symbol: str) -> str:
        """
        Call sector_lookup_fn(symbol). On exception or non-string result,
        return "UNKNOWN" and log WARNING (RE9).
        """
        try:
            result = self._sector_fn(symbol)
            if isinstance(result, str) and result:
                return result
            self._log.warning(
                "RiskEngine: sector_lookup_fn(%r) returned %r (not a non-empty str); "
                "using UNKNOWN",
                symbol, result,
            )
            return "UNKNOWN"
        except Exception as exc:
            self._log.warning(
                "RiskEngine: sector_lookup_fn(%r) raised %s: %s; using UNKNOWN",
                symbol, type(exc).__name__, exc,
            )
            return "UNKNOWN"
