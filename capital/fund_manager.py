"""
capital/fund_manager.py -- Trading System v2

Purpose:
    Single source of truth for all capital state. Every capital mutation
    (reserve, release, commit, release_used) flows through this module.
    Replaces 5+ scattered capital tracking points identified in the audit.

Locked Design Decisions:
    FM1  -- Single source of truth: total, available, reserved, used,
             daily_realized_pnl.
    FM2  -- Three-balance invariant: available + reserved + used == total.
             Checked inside EVERY mutation before commit. (audit G3)
    FM3  -- Two independent buckets: intraday (70%) and positional (30%).
             Cross-bucket borrowing is FORBIDDEN.
    FM4  -- required_margin = qty * price / leverage. NOT notional.
             Audit catastrophic flaw fix: old code deducted 5x actual margin.
    FM5  -- Atomic reserve/release/commit via single threading.RLock.
    FM6  -- reservation_id (16 hex chars) per reserve() call. Stored in
             _reservations dict for release/commit lookups.
    FM7  -- daily_realized_pnl tracked; on_daily_loss_breach fired post-trade.
    FM8  -- get_snapshot() returns frozen CapitalSnapshot under lock.
    FM9  -- sync_from_broker(balance): sets total, recomputes available.
             NEVER subtracts used from broker balance (audit double-deduction fix).
    FM10 -- Every mutation writes to fm_ledger (state_store) in same txn.
             BL-5: ledger row is written BEFORE the in-memory mutation
             (write-ahead logging). If the app crashes between the INSERT
             and the bucket update, rehydrate replays fm_ledger to rebuild
             in-memory state. If the ledger INSERT itself raises, the
             mutation is skipped and the caller sees the exception.
    FM11 -- Invariant violation raises CapitalInvariantViolation (CRITICAL).
    FM12 -- Constructor validates bucket pcts sum to 1.0 and leverage_map
             covers all 4 intents.
    FM13 -- initialize(broker_balance) called once at startup.
    FM14 -- reset_daily_pnl() called at EOD.
    FM15 -- Layer 3 (capital/). Imports: stdlib + core.*.
    FM16 -- SystemConfig.capital added.
    FM17 -- NOT in scope: position sizing, risk per trade, cost deduction.
    FM18 -- BL-1: rehydrate_from_open_trades reconstructs in-memory state
             from the persistence triangle (fm_ledger + trades + orders) on
             startup. Uses _apply_reserve / _apply_release / _apply_commit
             pure-mutation helpers shared with the public reserve / release
             / commit_to_used paths -- public methods orchestrate (validate
             -> ledger -> apply -> invariant), replay invokes _apply* without
             writing the ledger back. Invariant is checked ONCE at the end
             of replay (not per step), since intermediate states between
             RESERVE and COMMIT are momentarily unusual. Failure raises
             CapitalStateInconsistent (distinct from CapitalInvariantViolation
             so callers can distinguish startup-replay corruption from a live
             mid-mutation invariant break).
    FM19 -- BL-9: optional kill_switch dependency. _check_invariant calls
             kill_switch.hard_kill BEFORE the existing on_critical_failure
             callback and BEFORE re-raising, so a provably corrupted bin-card
             state cancels open orders immediately rather than just blocking
             new ones. hard_kill is wrapped in its own try/except -- if the
             kill path itself fails, the invariant exception still propagates
             (belt-and-braces). kill_switch=None degrades gracefully: the
             existing on_critical_failure path still runs (soft_kill wiring
             remains available for other critical-signal callers).
    FM20 -- BL-4 (Phase C.1): commit_to_used wraps its entire body in a
             hard-kill handler. Any exception (unknown reservation, ledger
             failure, apply failure, invariant violation) fires
             kill_switch.hard_kill before re-raising the original. Unlike
             BL-9, this handler does NOT invoke on_critical_failure -- the
             callback stays narrow to invariant-violation semantics.
             Scope: commit_to_used only; reserve() and release_used() keep
             their existing (recoverable / reconciler-backstopped) error
             policies.

What This Module Does NOT Do:
    - Does not size positions (capital/position_sizer.py)
    - Does not compute risk per trade (capital/risk_engine.py)
    - Does not deduct broker costs (caller passes net values)
    - Does not subscribe to events directly
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Final, Optional

from capital.invariant import assert_capital_invariant
from core.events import CapitalDriftDetected, EventBus
from core.exceptions import CapitalInvariantViolation, CapitalStateInconsistent
from core.logger import log_exception
from core.state_store import StateStore
from core.time_authority import now_ist

if TYPE_CHECKING:
    from capital.kill_switch import KillSwitch

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_INTRADAY_INTENTS: frozenset[str] = frozenset({"INTRADAY", "COVER_ORDER", "BRACKET_ORDER"})
_POSITIONAL_INTENTS: frozenset[str] = frozenset({"DELIVERY"})
_ALL_INTENTS: frozenset[str] = _INTRADAY_INTENTS | _POSITIONAL_INTENTS

# EF-3: release_used now requires `direction` to compute PnL correctly.
# LONG profits when exit > entry; SHORT profits when exit < entry.
_VALID_DIRECTIONS: Final[frozenset[str]] = frozenset({"LONG", "SHORT"})

_INTRADAY_BUCKET = "intraday"
_POSITIONAL_BUCKET = "positional"

_INVARIANT_TOLERANCE = 0.01   # 1 paise tolerance for float rounding

# BL-1 / FM18: orders.product -> semantic intent for rehydrate replay.
# CO is COVER_ORDER (intraday-bucketed); MIS is plain INTRADAY; CNC and NRML
# are deliverable holdings (positional bucket). Anything outside this map
# falls through to a bucket-derived inference; see _replay_open_trade.
_PRODUCT_TO_INTENT: Final[dict[str, str]] = {
    "MIS": "INTRADAY",
    "CO": "COVER_ORDER",
    "CNC": "DELIVERY",
    "NRML": "DELIVERY",
}


# ─────────────────────────────────────────────────────────────────────────────
# Return-type dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReservationResult:
    success: bool
    reservation_id: str    # 16 hex chars; empty string on failure
    margin: float          # required margin; 0.0 on failure
    bucket: str            # "intraday" | "positional"; empty on failure
    reason_if_failed: str  # empty on success


@dataclass(frozen=True)
class CommitResult:
    reservation_id: str
    actual_margin: float    # margin deducted from used at fill price/qty
    excess_returned: float  # margin returned to available (partial fill)
    bucket: str


@dataclass(frozen=True)
class ReleaseResult:
    reservation_id: str
    margin_released: float
    bucket: str
    pnl_delta: float        # realized PnL change (0 for plain release)


@dataclass(frozen=True)
class CapitalSnapshot:
    total: float
    intraday_avail: float
    intraday_reserved: float
    intraday_used: float
    positional_avail: float
    positional_reserved: float
    positional_used: float
    daily_realized_pnl: float
    ts: str   # ISO-8601 IST string


# ─────────────────────────────────────────────────────────────────────────────
# Internal reservation record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Reservation:
    reservation_id: str
    symbol: str
    qty: int
    price: float
    intent: str
    margin: float
    bucket: str
    signal_id: Optional[str]
    ts: str


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helper (FM4)
# ─────────────────────────────────────────────────────────────────────────────

def required_margin(
    qty: int,
    price: float,
    intent: str,
    leverage_map: dict[str, float],
) -> float:
    """
    Compute required margin = qty * price / leverage.
    NOT notional (audit catastrophic flaw fix: FM4).

    Args:
        qty:          number of shares
        price:        order price per share
        intent:       semantic product intent
        leverage_map: {intent -> leverage_multiplier}

    Returns:
        Margin amount in rupees.
    """
    leverage = leverage_map.get(intent, 1.0)
    return (qty * price) / leverage


# ─────────────────────────────────────────────────────────────────────────────
# FundManager
# ─────────────────────────────────────────────────────────────────────────────

class FundManager:
    """
    Single source of truth for all capital state (FM1).

    Thread-safe via single RLock. Every public method is atomic.
    Every mutation writes to fm_ledger for audit trail (FM10).

    Usage::
        fm = FundManager(store, bus, logger,
                         intraday_bucket_pct=0.70,
                         positional_bucket_pct=0.30,
                         daily_loss_limit=10000.0,
                         leverage_map={"INTRADAY": 5.0, ...})
        fm.initialize(broker_balance=500000.0)
        result = fm.reserve("RELIANCE", 10, 2500.0, "INTRADAY", "sig_abc")
        if result.success:
            ...
    """

    def __init__(
        self,
        state_store: StateStore,
        bus: EventBus,
        logger: object,
        intraday_bucket_pct: float = 0.70,
        positional_bucket_pct: float = 0.30,
        daily_loss_limit: float = 10_000.0,
        leverage_map: Optional[dict[str, float]] = None,
        on_daily_loss_breach: Optional[Callable[[], None]] = None,
        on_critical_failure: Optional[Callable[[str], None]] = None,
        kill_switch: Optional["KillSwitch"] = None,
    ) -> None:
        # FM12: validate constructor arguments
        if leverage_map is None:
            leverage_map = {
                "INTRADAY": 5.0,
                "COVER_ORDER": 6.0,
                "DELIVERY": 1.0,
                "BRACKET_ORDER": 5.0,
            }
        missing = _ALL_INTENTS - set(leverage_map.keys())
        if missing:
            raise ValueError(
                f"leverage_map is missing entries for intents: {sorted(missing)}"
            )
        if abs((intraday_bucket_pct + positional_bucket_pct) - 1.0) > 1e-9:
            raise ValueError(
                f"intraday_bucket_pct ({intraday_bucket_pct}) + "
                f"positional_bucket_pct ({positional_bucket_pct}) must equal 1.0"
            )
        if daily_loss_limit <= 0:
            raise ValueError(f"daily_loss_limit must be > 0, got {daily_loss_limit}")

        self._store = state_store
        self._bus = bus
        self._log = logger
        self._intraday_pct = intraday_bucket_pct
        self._positional_pct = positional_bucket_pct
        self._daily_loss_limit = daily_loss_limit
        self._leverage_map = dict(leverage_map)
        self._on_loss_breach = on_daily_loss_breach
        self._on_critical = on_critical_failure
        self._kill_switch = kill_switch  # FM19 / BL-9

        self._lock = threading.RLock()

        # BL-5: per-instance session id, stamped on every fm_ledger row so
        # restart audits can partition mutations by FundManager lifetime.
        self._session_id = "fm_" + uuid.uuid4().hex[:12]
        self._log.info(
            "fund_manager.session_start",
            extra={"session_id": self._session_id},
        )

        # Capital state (FM1, FM3) -- set by initialize()
        self._total: float = 0.0

        # Intraday bucket
        self._intraday_avail: float = 0.0
        self._intraday_reserved: float = 0.0
        self._intraday_used: float = 0.0

        # Positional bucket
        self._positional_avail: float = 0.0
        self._positional_reserved: float = 0.0
        self._positional_used: float = 0.0

        self._daily_pnl: float = 0.0
        self._initialized: bool = False

        # FM6: active reservations
        self._reservations: dict[str, _Reservation] = {}

    # ── public API ────────────────────────────────────────────────────────────

    def initialize(self, broker_balance: float) -> None:
        """
        Set total capital from first broker sync, split into buckets (FM13).
        Writes INIT row to fm_ledger.
        Must be called exactly once before any reserve/release.

        H-4: double-initialize guard. A second call would write a second INIT
        row (with balance_before=0.0 -- corrupt) and silently zero existing
        reservations/used. WARNING + no-op is safer than silently destroying
        live capital state.
        """
        with self._lock:
            if self._initialized:
                self._log.warning(
                    "fund_manager.initialize called again; no-op (H-4 guard)",
                    extra={
                        "existing_total": self._total,
                        "ignored_balance": broker_balance,
                    },
                )
                return
            ts = now_ist().isoformat()
            # BL-5: ledger row first (write-ahead), then in-memory mutation.
            self._write_ledger(
                ts=ts,
                entry_type="INIT",
                amount=broker_balance,
                bucket="both",
                balance_before=0.0,
                balance_after=broker_balance,
                reason=f"initialize with broker_balance={broker_balance}",
            )
            self._total = broker_balance
            self._intraday_avail = broker_balance * self._intraday_pct
            self._intraday_reserved = 0.0
            self._intraday_used = 0.0
            self._positional_avail = broker_balance * self._positional_pct
            self._positional_reserved = 0.0
            self._positional_used = 0.0
            self._daily_pnl = 0.0
            self._initialized = True

            self._log.info(
                "fund_manager.initialize",
                extra={"total": broker_balance,
                       "intraday_avail": self._intraday_avail,
                       "positional_avail": self._positional_avail},
            )

    def required_margin(
        self,
        qty: int,
        price: float,
        intent: str,
    ) -> float:
        """
        Public margin-compute using the FM's leverage map (H-3).

        Thin wrapper over the module-level required_margin() free function so
        callers (e.g. order_placer for trades.margin_reserved metadata) do not
        reach into self._leverage_map and do not need to know leverage internals.

        Args:
            qty:    number of shares
            price:  order price per share
            intent: semantic product intent (INTRADAY, DELIVERY, COVER_ORDER, ...)

        Returns:
            Required margin in rupees. Intents absent from _leverage_map fall
            back to 1.0x via required_margin()'s .get() default (FM4).
        """
        return required_margin(qty, price, intent, self._leverage_map)

    def reserve(
        self,
        symbol: str,
        qty: int,
        price: float,
        intent: str,
        signal_id: Optional[str] = None,
    ) -> ReservationResult:
        """
        Atomically compute margin and reserve it from the appropriate bucket (FM5).

        Returns ReservationResult(success=True, ...) or success=False with reason.
        Does NOT raise on insufficient capital -- returns failure gracefully.

        Raises:
            CapitalInvariantViolation: invariant check fails post-mutation (FM11).
            RuntimeError: if not initialized.
        """
        with self._lock:
            self._assert_initialized()
            bucket = self._bucket_for_intent(intent)
            margin = required_margin(qty, price, intent, self._leverage_map)
            avail_before = self._bucket_avail(bucket)

            if margin > avail_before:
                return ReservationResult(
                    success=False,
                    reservation_id="",
                    margin=margin,
                    bucket=bucket,
                    reason_if_failed=(
                        f"Insufficient {bucket} capital: need {margin:.2f}, "
                        f"have {avail_before:.2f}"
                    ),
                )

            # BL-5: write-ahead. Project the post-mutation balance, write the
            # ledger row first, then execute the in-memory mutation.
            rid = uuid.uuid4().hex[:16]
            ts = now_ist().isoformat()
            projected_after = avail_before - margin
            self._write_ledger(
                ts=ts,
                entry_type="RESERVE",
                amount=margin,
                bucket=bucket,
                balance_before=avail_before,
                balance_after=projected_after,
                signal_id=signal_id,
                reservation_id=rid,
                reason=f"{symbol} qty={qty} @ {price} intent={intent}",
                margin_delta=+margin,
            )

            # FM18: pure mutation via shared helper (used by both this public
            # path and rehydrate replay). Public path: ledger then apply then
            # invariant. Replay path: apply only (no ledger, no invariant).
            self._apply_reserve(
                rid=rid,
                bucket=bucket,
                margin=margin,
                symbol=symbol,
                qty=qty,
                price=price,
                intent=intent,
                signal_id=signal_id,
                ts=ts,
            )

            self._check_invariant("reserve", rid)

            return ReservationResult(
                success=True,
                reservation_id=rid,
                margin=margin,
                bucket=bucket,
                reason_if_failed="",
            )

    def release(self, reservation_id: str, reason: str = "") -> bool:
        """
        Return reserved margin to available. Used on cancellation / rejection (FM5).

        Returns:
            True  -- margin released successfully.
            False -- reservation_id not found (already released or unknown). Idempotent.

        Raises:
            CapitalInvariantViolation: invariant check fails post-mutation.
        """
        with self._lock:
            self._assert_initialized()
            # Peek (not pop) — BL-5 write-ahead commits before in-memory change.
            res = self._reservations.get(reservation_id)
            if res is None:
                return False   # FM5: idempotent

            avail_before = self._bucket_avail(res.bucket)
            projected_after = avail_before + res.margin
            ts = now_ist().isoformat()
            self._write_ledger(
                ts=ts,
                entry_type="RELEASE",
                amount=-res.margin,
                bucket=res.bucket,
                balance_before=avail_before,
                balance_after=projected_after,
                signal_id=res.signal_id,
                reservation_id=reservation_id,
                reason=reason or "released",
                margin_delta=-res.margin,
            )

            # FM18: pure mutation via shared helper (used by both this public
            # path and rehydrate replay).
            self._apply_release(reservation_id)

            self._check_invariant("release", reservation_id)
            return True

    def commit_to_used(
        self,
        reservation_id: str,
        actual_fill_price: float,
        actual_qty: int,
    ) -> CommitResult:
        """
        Move margin from reserved to used on order fill (FM5).
        Handles partial fills: excess margin returns to available.

        BL-4 (Phase C.1): ANY exception raised inside this method (unknown
        reservation, ledger-write failure, apply-mutation failure, invariant
        violation) implies the broker has confirmed the fill but capital
        accounting is inconsistent -- an unrecoverable state. Before re-
        raising, the method fires kill_switch.hard_kill() so callers cannot
        accidentally swallow the corruption by catching Exception broadly
        (OrderPlacer._handle_entry_fill does exactly that today).

        Scoping note: this hard-kill policy is SPECIFIC to commit_to_used.
        reserve() failures are recoverable via signal rejection.
        release_used() failures keep the existing swallow+reconciler backstop
        (the position has already been realized at broker; capital cleanup
        proceeds out-of-band). Widening this pattern to other mutators
        requires its own test matrix per-method.

        Args:
            reservation_id:   from reserve().
            actual_fill_price: the actual fill price (may differ from reserved).
            actual_qty:        filled quantity (may be < reserved qty).

        Raises:
            ValueError: reservation_id unknown (then also fires hard_kill).
            CapitalInvariantViolation: invariant fails post-mutation (BL-9
                fires hard_kill inside _check_invariant; BL-4's outer handler
                may fire it again -- hard_kill is idempotent).
            Any other exception from ledger/apply is re-raised (also after
                hard_kill has fired).
        """
        try:
            with self._lock:
                self._assert_initialized()
                res = self._reservations.get(reservation_id)
                if res is None:
                    raise ValueError(
                        f"reservation_id {reservation_id!r} not found in active reservations"
                    )

                actual_margin = required_margin(
                    actual_qty, actual_fill_price, res.intent, self._leverage_map
                )
                excess = max(0.0, res.margin - actual_margin)

                # BL-5: ledger row first. COMMIT is a bucket-internal reshape
                # (reserved -> used, optional excess -> avail), so margin_delta=0
                # (no net change to the sum of reserved+used from this bucket's POV
                # when there is no excess; the excess path adds to avail not to
                # the reserved+used pair, so the "in-flight" margin decreases by
                # `excess`). We use amount=actual_margin for audit continuity and
                # balance_before/after reflect the AVAILABLE balance in `bucket`.
                avail_before = self._bucket_avail(res.bucket)
                projected_after = avail_before + excess
                ts = now_ist().isoformat()
                self._write_ledger(
                    ts=ts,
                    entry_type="COMMIT",
                    amount=actual_margin,
                    bucket=res.bucket,
                    balance_before=avail_before,
                    balance_after=projected_after,
                    signal_id=res.signal_id,
                    reservation_id=reservation_id,
                    reason=(
                        f"fill: qty={actual_qty} price={actual_fill_price} "
                        f"excess_returned={excess:.2f}"
                    ),
                    margin_delta=0.0,
                )

                # FM18: pure mutation via shared helper (used by both this public
                # path and rehydrate replay).
                self._apply_commit(
                    reservation_id=reservation_id,
                    actual_margin=actual_margin,
                    excess=excess,
                )

                self._check_invariant("commit_to_used", reservation_id)

                return CommitResult(
                    reservation_id=reservation_id,
                    actual_margin=actual_margin,
                    excess_returned=excess,
                    bucket=res.bucket,
                )
        except Exception as exc:
            # BL-4 (Phase C.1): commit_to_used failure implies broker-
            # confirmed fill but capital state inconsistent. Trip hard_kill
            # before re-raising so callers that catch Exception broadly
            # (e.g. OrderPlacer._handle_entry_fill) cannot swallow corruption.
            # Pattern mirrors BL-9 (_check_invariant); hard_kill is
            # idempotent per kill_switch state machine so double-fire from
            # BL-9 + BL-4 on an invariant violation is safe.
            # BL-4 does NOT invoke on_critical_failure (stays narrow to
            # BL-9 semantic; a ledger/apply failure is not necessarily an
            # invariant breach).
            reason = (
                f"commit_to_used failed for reservation_id="
                f"{reservation_id}: {exc}"
            )
            self._log.critical(
                "commit_to_used_failed_hard_kill",
                extra={
                    "reservation_id": reservation_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "actual_fill_price": actual_fill_price,
                    "actual_qty": actual_qty,
                },
            )
            if self._kill_switch is not None:
                try:
                    self._kill_switch.hard_kill(
                        reason=reason,
                        triggered_by="fund_manager.commit_to_used",
                    )
                except Exception as kse:
                    log_exception(self._log, kse)
                    self._log.critical(
                        "commit_to_used: kill_switch.hard_kill ALSO failed",
                        extra={"kill_error": str(kse)},
                    )
            else:
                self._log.critical(
                    "commit_to_used failed with kill_switch=None; "
                    "escalation not possible"
                )
            raise

    def release_used(
        self,
        symbol: str,
        exit_price: float,
        exit_qty: int,
        intent: str,
        entry_price: float,
        direction: str,
        costs: float = 0.0,
    ) -> ReleaseResult:
        """
        Release used margin on position close (FM5). Updates daily_realized_pnl (FM7).

        Args:
            symbol:      trading symbol (for ledger)
            exit_price:  price at which position was closed
            exit_qty:    number of shares closed
            intent:      original intent (determines bucket and leverage)
            entry_price: original entry price (for PnL calculation)
            direction:   "LONG" | "SHORT" — required for direction-correct PnL.
            costs:       total transaction costs (passed by caller)

        PnL sign convention (EF-3):
            LONG  profit = exit > entry  (close above cost)
            SHORT profit = exit < entry  (cover below sell price)

        Both produce positive pnl_delta when profitable and negative when
        losing. The daily_realized_pnl aggregate is therefore direction-
        agnostic by construction. Prior to EF-3 this method was LONG-only,
        which silently inverted SHORT PnL; caught during A.3.d pre-work
        because BL-7 had kept _on_order_filled from ever firing on exits,
        so no caller had exercised non-breakeven SHORT prices before.

        Raises:
            ValueError: direction not in {LONG, SHORT}.
            CapitalInvariantViolation: invariant fails post-mutation.
        """
        if direction not in _VALID_DIRECTIONS:
            raise ValueError(
                f"release_used: direction must be one of "
                f"{sorted(_VALID_DIRECTIONS)}, got {direction!r}"
            )
        with self._lock:
            self._assert_initialized()
            bucket = self._bucket_for_intent(intent)
            # Use entry_price to compute the margin that was locked in used (FM4).
            # Exit price may differ; always release the entry margin from used.
            margin = required_margin(exit_qty, entry_price, intent, self._leverage_map)

            # EF-3: direction-aware gross PnL. LONG: (exit-entry)*qty.
            # SHORT: (entry-exit)*qty. Subtract costs for net PnL.
            if direction == "LONG":
                gross_pnl = (exit_price - entry_price) * exit_qty
            else:  # SHORT
                gross_pnl = (entry_price - exit_price) * exit_qty
            pnl = gross_pnl - costs

            avail_before = self._bucket_avail(bucket)
            projected_after = avail_before + margin + pnl

            # BL-5: write-ahead. Record the intended mutation first; replay
            # via rehydrate uses direction + pnl_delta + costs to rebuild
            # the same end state (EF-3 direction correctness carries into
            # the ledger).
            ts = now_ist().isoformat()
            self._write_ledger(
                ts=ts,
                entry_type="RELEASE_USED",
                amount=-margin,
                bucket=bucket,
                balance_before=avail_before,
                balance_after=projected_after,
                reason=(
                    f"{symbol} exit: qty={exit_qty} price={exit_price} "
                    f"pnl={pnl:.2f} costs={costs:.2f}"
                ),
                direction=direction,
                margin_delta=-margin,
                pnl_delta=pnl,
                costs=costs,
            )

            # In-memory mutation (caught up to the ledger)
            self._bucket_deduct_used(bucket, margin)
            self._bucket_add_avail(bucket, margin + pnl)
            # PnL changes total capital (FM2 invariant: avail+res+used==total)
            self._total += pnl
            self._daily_pnl += pnl

            self._check_invariant("release_used", symbol)

            # FM7: check daily loss limit after updating PnL
            if self._daily_pnl <= -self._daily_loss_limit:
                self._log.critical(
                    "fund_manager.daily_loss_breach",
                    extra={"daily_pnl": self._daily_pnl,
                           "limit": self._daily_loss_limit},
                )
                if self._on_loss_breach is not None:
                    self._on_loss_breach()

            return ReleaseResult(
                reservation_id="",
                margin_released=margin,
                bucket=bucket,
                pnl_delta=pnl,
            )

    def sync_from_broker(self, broker_balance: float) -> None:
        """
        Update total capital from broker truth (FM9).
        Available = total - reserved - used (per bucket).
        NEVER subtracts used from broker_balance (audit double-deduction fix).

        Args:
            broker_balance: net equity from broker (already the total; NOT net of positions).
        """
        with self._lock:
            self._assert_initialized()
            old_total = self._total

            # BL-5: write-ahead. SYNC recomputes bucket availables from the
            # authoritative broker balance; the ledger row records the
            # total-level delta so rehydrate can distinguish a sync event
            # from a reservation / release.
            ts = now_ist().isoformat()
            self._write_ledger(
                ts=ts,
                entry_type="SYNC",
                amount=broker_balance - old_total,
                bucket="both",
                balance_before=old_total,
                balance_after=broker_balance,
                reason=f"broker sync: {old_total:.2f} -> {broker_balance:.2f}",
            )

            # In-memory mutation
            self._total = broker_balance
            # Recompute available = total - reserved - used, split by bucket pct
            intraday_total = broker_balance * self._intraday_pct
            positional_total = broker_balance * self._positional_pct

            # H-1: silent max(0.0, ...) clamps removed. sync_from_broker was the
            # only mutator skipping _check_invariant; bucket overflow (broker
            # total shrinks below reserved+used on a bucket) was silently
            # masked. Now surfaces as CapitalInvariantViolation via the
            # per-bucket INV6 guard added to _check_invariant.
            self._intraday_avail = (
                intraday_total - self._intraday_reserved - self._intraday_used
            )
            self._positional_avail = (
                positional_total - self._positional_reserved - self._positional_used
            )
            self._log.info(
                "fund_manager.sync_from_broker",
                extra={"old_total": old_total, "new_total": broker_balance},
            )

            # Publish CapitalDriftDetected if significant TOTAL change (FM9)
            delta = broker_balance - old_total
            if abs(delta) > 1.0:
                self._bus.publish(CapitalDriftDetected(
                    source_module="fund_manager",
                    expected=old_total,
                    actual=broker_balance,
                    delta=delta,
                ))

            # H-1: detect bucket overflow (either bucket went negative after
            # sync). Publish drift BEFORE _check_invariant fires -- the
            # invariant path calls hard_kill and raises, which would
            # short-circuit the publish if ordered after.
            bucket_overflow = (
                self._intraday_avail < -_INVARIANT_TOLERANCE
                or self._positional_avail < -_INVARIANT_TOLERANCE
            )
            if bucket_overflow:
                # Most-negative bucket gives the rupee magnitude for BL-2
                # tiering; drift_handler routes escalating sources by
                # source_module (fund_manager_bucket_overflow is a new
                # escalating source, added to _ESCALATING_SOURCES).
                gap = min(self._intraday_avail, self._positional_avail)
                # H-1: publish drift event for telemetry; _check_invariant
                # below will fire hard_kill via BL-9. drift_handler will
                # receive this event AND observe the hard_kill state; its
                # escalation ladder is idempotent wrt already-HARD_KILL
                # state, so both paths can fire without conflict.
                try:
                    self._bus.publish(CapitalDriftDetected(
                        source_module="fund_manager_bucket_overflow",
                        expected=0.0,   # buckets should never go negative
                        actual=gap,     # most-negative bucket available
                        delta=abs(gap), # rupee magnitude for BL-2 tiering
                    ))
                except Exception as pub_exc:
                    self._log.error(
                        "fund_manager.publish_bucket_overflow_drift_failed: %s",
                        pub_exc,
                    )

            # H-1: invariant check now runs on every sync. Per-bucket INV6
            # guard fires CapitalInvariantViolation on bucket overflow;
            # BL-9 hard_kill fires before the raise.
            self._check_invariant("sync_from_broker", self._session_id)

    def get_live_reservations(self) -> dict[str, "_Reservation"]:
        """
        Return a locked snapshot copy of live reservations (BL-3).

        Used by OrderReconciler._check7_capital_accounting_drift to verify
        that fund_manager's in-memory _reservations dict still matches the
        signed sum of fm_ledger margin_delta rows for each rid.

        Returns a SHALLOW copy of self._reservations under the lock; the
        _Reservation dataclasses themselves are not deep-copied because they
        are treated as immutable in this codebase. Mutating the returned
        dict has no effect on FundManager state.

        Full _Reservation objects (not just margins) are returned so future
        checks can verify symbol/qty/intent without a signature change.
        """
        with self._lock:
            return dict(self._reservations)

    def get_snapshot(self) -> CapitalSnapshot:
        """Return a frozen, consistent point-in-time view of capital state (FM8)."""
        with self._lock:
            return CapitalSnapshot(
                total=self._total,
                intraday_avail=self._intraday_avail,
                intraday_reserved=self._intraday_reserved,
                intraday_used=self._intraday_used,
                positional_avail=self._positional_avail,
                positional_reserved=self._positional_reserved,
                positional_used=self._positional_used,
                daily_realized_pnl=self._daily_pnl,
                ts=now_ist().isoformat(),
            )

    def reset_daily_pnl(self) -> None:
        """Reset daily realized PnL to 0 at EOD. reserved/used NOT reset (FM14)."""
        with self._lock:
            old_pnl = self._daily_pnl
            ts = now_ist().isoformat()
            # BL-5: ledger first, then zero-out
            self._write_ledger(
                ts=ts,
                entry_type="RESET_PNL",
                amount=0.0,
                bucket="both",
                balance_before=old_pnl,
                balance_after=0.0,
                reason=f"EOD reset: previous pnl={old_pnl:.2f}",
                pnl_delta=-old_pnl,
            )
            self._daily_pnl = 0.0
            self._log.info(
                "fund_manager.reset_daily_pnl",
                extra={"previous_pnl": old_pnl},
            )

    # ── BL-1 / FM18: rehydrate (startup replay) ──────────────────────────────

    def rehydrate_from_open_trades(
        self,
        start_of_today_iso: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Reconstruct in-memory capital state from the persistence triangle:
        fm_ledger (capital transitions) + trades (position identity) +
        orders (entry product). Called once at startup, AFTER initialize().

        Walks every OPEN/PARTIAL trade, looks up its reservation_id via
        signal_id -> first RESERVE row in fm_ledger, then replays the
        ordered RESERVE/COMMIT chain for that reservation through
        _apply_reserve / _apply_commit -- the same mutation helpers the
        public reserve()/commit_to_used() use, but WITHOUT writing the
        ledger back (that's where the data came from).

        Symbol/qty/price/intent are sourced from trades + orders, not the
        ledger -- see EF-5 for why the ledger lacks those columns by design
        (each table owns what it owns).

        After per-trade replay, today's RELEASE_USED rows are walked to
        rebuild daily_realized_pnl + total. That step ONLY adjusts PnL/total;
        it does NOT touch buckets (the closed trades whose RELEASE_USED rows
        these are weren't replayed in Phase 1, so their bucket movements
        already cancel out).

        The bin-card invariant is checked ONCE at the end. Per-step
        invariants would false-positive on legitimately-mid-flight states.
        On failure, raises CapitalStateInconsistent (NOT
        CapitalInvariantViolation -- callers can distinguish startup-replay
        corruption from a live mutation invariant break).

        Args:
            start_of_today_iso: ISO-8601 IST timestamp; floor of today used
                for PnL carryover. Defaults to today 00:00:00 IST.

        Returns:
            dict with keys:
                replayed_trades:   number of open trades replayed
                replayed_pnl_rows: number of RELEASE_USED rows applied for daily_pnl
                anomalies:         list of {trade_id, signal_id?, reason}
                                   for trades skipped due to data gaps

        Raises:
            CapitalStateInconsistent: invariant fails after replay completes.
            RuntimeError: if not initialized.
        """
        with self._lock:
            self._assert_initialized()

            if start_of_today_iso is None:
                today = now_ist()
                start_of_today_iso = today.replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).isoformat()

            anomalies: list[dict[str, Any]] = []
            replayed_trades = 0

            # Phase 1: per-open-trade replay
            open_trades = self._store.get_all_open_trades()
            for trade in open_trades:
                if self._replay_open_trade(trade, anomalies):
                    replayed_trades += 1

            # Phase 2: today's realized-PnL carryover. For each CLOSED trade
            # (whose RESERVE+COMMIT were NOT replayed in Phase 1 because the
            # trade is not open), we apply only the *net* effect of the full
            # lifecycle: bucket avail += pnl, _total += pnl, _daily_pnl += pnl.
            # The -margin/+margin legs of the CLOSED lifecycle cancel to zero,
            # so we don't touch reserved/used here.
            pnl_rows = self._store.fetch_all(
                """
                SELECT pnl_delta, bucket FROM fm_ledger
                WHERE entry_type = 'RELEASE_USED'
                  AND ts >= ?
                  AND pnl_delta != 0
                ORDER BY ledger_id ASC
                """,
                (start_of_today_iso,),
            )
            replayed_pnl_rows = 0
            for row in pnl_rows:
                pnl = float(row["pnl_delta"])
                bucket = row["bucket"]
                self._bucket_add_avail(bucket, pnl)
                self._daily_pnl += pnl
                self._total += pnl
                replayed_pnl_rows += 1

            # Phase 3: invariant check ONCE (FM18). Wrap to distinguish
            # startup-replay corruption from a live mid-mutation break.
            try:
                self._check_invariant("rehydrate", "BL-1")
            except CapitalInvariantViolation as exc:
                raise CapitalStateInconsistent(
                    f"Capital state invariant failed after rehydrate: {exc}",
                    anomalies=anomalies,
                    replayed_trades=replayed_trades,
                    replayed_pnl_rows=replayed_pnl_rows,
                ) from exc

            self._log.info(
                "fund_manager.rehydrate_complete",
                extra={
                    "replayed_trades": replayed_trades,
                    "replayed_pnl_rows": replayed_pnl_rows,
                    "anomaly_count": len(anomalies),
                    "daily_pnl": self._daily_pnl,
                    "total": self._total,
                },
            )
            for a in anomalies:
                self._log.warning("fund_manager.rehydrate_anomaly", extra=a)

            return {
                "replayed_trades": replayed_trades,
                "replayed_pnl_rows": replayed_pnl_rows,
                "anomalies": anomalies,
            }

    def _replay_open_trade(
        self,
        trade: Any,
        anomalies: list[dict[str, Any]],
    ) -> bool:
        """
        Replay one open trade's RESERVE+COMMIT ledger chain. Returns True if
        anything was applied; False if the trade was skipped as an anomaly.

        Per BL-1 spec, ONLY RESERVE and COMMIT are replayed for an OPEN/
        PARTIAL trade -- those are the only entries that produce a coherent
        end state for an open position. RELEASE / RELEASE_USED rows in the
        chain would imply the trade should not be open; they're noted as
        anomalies but not applied (Phase 2 handles RELEASE_USED for
        closed-trade PnL carryover separately).
        """
        trade_id = trade["trade_id"]
        signal_id = trade["signal_id"]

        if signal_id is None:
            anomalies.append({
                "trade_id": trade_id,
                "reason": "trade.signal_id is NULL",
            })
            return False

        # EF-5: two-hop lookup. The trades table lacks a reservation_id
        # column; we resolve via the most recent RESERVE row for this signal.
        rid = self._store.get_reservation_id_for_signal(signal_id)
        if rid is None:
            anomalies.append({
                "trade_id": trade_id,
                "signal_id": signal_id,
                "reason": "no RESERVE row in fm_ledger for this signal_id",
            })
            return False

        # Fetch the ledger chain for this reservation, in INSERT order.
        rows = self._store.fetch_all(
            """
            SELECT ledger_id, ts, entry_type, amount, bucket,
                   balance_before, balance_after, signal_id,
                   reservation_id, margin_delta, pnl_delta
            FROM fm_ledger
            WHERE reservation_id = ?
            ORDER BY ledger_id ASC
            """,
            (rid,),
        )
        if not rows:
            anomalies.append({
                "trade_id": trade_id,
                "signal_id": signal_id,
                "reservation_id": rid,
                "reason": "reservation_id present in lookup but no ledger rows found",
            })
            return False

        # Decision (a): symbol/qty/price/intent come from trades + orders.
        symbol = trade["symbol"]

        qty_filled = int(trade["qty_filled"] or 0)
        if qty_filled > 0:
            qty = qty_filled
        else:
            qty = int(trade["qty_planned"])
            self._log.warning(
                "fund_manager.rehydrate_qty_fallback",
                extra={
                    "trade_id": trade_id,
                    "qty_filled": qty_filled,
                    "qty_planned": qty,
                    "reason": "qty_filled=0; trade placed but unfilled at crash",
                },
            )

        entry_actual = trade["entry_actual_price"]
        if entry_actual is not None and float(entry_actual) != 0.0:
            price = float(entry_actual)
        else:
            price = float(trade["entry_target_price"])
            self._log.warning(
                "fund_manager.rehydrate_price_fallback",
                extra={
                    "trade_id": trade_id,
                    "entry_actual_price": entry_actual,
                    "entry_target_price": price,
                    "reason": "entry_actual_price unset; using target as fallback",
                },
            )

        product = trade["product"]
        intent = _PRODUCT_TO_INTENT.get(product) if product else None
        if intent is None:
            # Pathological: no ENTRY order row, or product not in map.
            # Fall back to bucket of the first RESERVE row.
            first_reserve = next(
                (r for r in rows if r["entry_type"] == "RESERVE"), None
            )
            if first_reserve is None:
                anomalies.append({
                    "trade_id": trade_id,
                    "signal_id": signal_id,
                    "reservation_id": rid,
                    "reason": (
                        f"no entry order product mapping (product={product!r}) "
                        f"and no RESERVE row to infer bucket from"
                    ),
                })
                return False
            bucket = first_reserve["bucket"]
            intent = "INTRADAY" if bucket == _INTRADAY_BUCKET else "DELIVERY"
            self._log.warning(
                "fund_manager.rehydrate_intent_fallback",
                extra={
                    "trade_id": trade_id,
                    "product": product,
                    "fallback_intent": intent,
                    "fallback_bucket": bucket,
                },
            )

        # Replay loop: apply only RESERVE + COMMIT.
        applied_any = False
        saw_reserve = False
        for row in rows:
            et = row["entry_type"]
            bucket = row["bucket"]
            if et == "RESERVE":
                if saw_reserve:
                    continue   # second RESERVE for same rid -- skip
                self._apply_reserve(
                    rid=rid,
                    bucket=bucket,
                    margin=float(row["amount"]),
                    symbol=symbol,
                    qty=qty,
                    price=price,
                    intent=intent,
                    signal_id=signal_id,
                    ts=row["ts"],
                )
                saw_reserve = True
                applied_any = True
            elif et == "COMMIT":
                if not saw_reserve:
                    anomalies.append({
                        "trade_id": trade_id,
                        "reservation_id": rid,
                        "reason": "COMMIT ledger row precedes RESERVE",
                    })
                    continue
                actual_margin = float(row["amount"])
                excess = float(row["balance_after"]) - float(row["balance_before"])
                self._apply_commit(
                    reservation_id=rid,
                    actual_margin=actual_margin,
                    excess=excess,
                )
                applied_any = True
            else:
                # RELEASE / RELEASE_USED / etc. on an OPEN trade -- pathological.
                anomalies.append({
                    "trade_id": trade_id,
                    "reservation_id": rid,
                    "ledger_id": row["ledger_id"],
                    "reason": (
                        f"unexpected entry_type={et!r} in chain for "
                        f"OPEN/PARTIAL trade; not applied"
                    ),
                })

        return applied_any

    # ── _apply_* helpers (FM18 / BL-1) ────────────────────────────────────────
    # Pure mutation helpers shared by public methods (after ledger write) and
    # rehydrate replay (without ledger write). NEITHER writes the ledger NOR
    # checks the invariant; the orchestrating caller is responsible for both.

    def _apply_reserve(
        self,
        *,
        rid: str,
        bucket: str,
        margin: float,
        symbol: str,
        qty: int,
        price: float,
        intent: str,
        signal_id: Optional[str],
        ts: str,
    ) -> None:
        """Move margin from avail to reserved; record the reservation."""
        self._bucket_deduct_avail(bucket, margin)
        self._bucket_add_reserved(bucket, margin)
        self._reservations[rid] = _Reservation(
            reservation_id=rid,
            symbol=symbol,
            qty=qty,
            price=price,
            intent=intent,
            margin=margin,
            bucket=bucket,
            signal_id=signal_id,
            ts=ts,
        )

    def _apply_release(self, reservation_id: str) -> None:
        """Pop reservation; restore margin to avail; deduct from reserved."""
        res = self._reservations.pop(reservation_id)
        self._bucket_add_avail(res.bucket, res.margin)
        self._bucket_deduct_reserved(res.bucket, res.margin)

    def _apply_commit(
        self,
        *,
        reservation_id: str,
        actual_margin: float,
        excess: float,
    ) -> None:
        """Pop reservation; deduct full reserved; add actual to used; excess
        (if any) returns to avail."""
        res = self._reservations.pop(reservation_id)
        self._bucket_deduct_reserved(res.bucket, res.margin)
        self._bucket_add_used(res.bucket, actual_margin)
        if excess > 0:
            self._bucket_add_avail(res.bucket, excess)

    # ── bucket helpers ────────────────────────────────────────────────────────

    def _bucket_for_intent(self, intent: str) -> str:
        if intent in _INTRADAY_INTENTS:
            return _INTRADAY_BUCKET
        if intent in _POSITIONAL_INTENTS:
            return _POSITIONAL_BUCKET
        raise ValueError(f"Unknown intent: {intent!r}")

    def _bucket_avail(self, bucket: str) -> float:
        return self._intraday_avail if bucket == _INTRADAY_BUCKET else self._positional_avail

    def _bucket_reserved(self, bucket: str) -> float:
        return self._intraday_reserved if bucket == _INTRADAY_BUCKET else self._positional_reserved

    def _bucket_used(self, bucket: str) -> float:
        return self._intraday_used if bucket == _INTRADAY_BUCKET else self._positional_used

    def _bucket_deduct_avail(self, bucket: str, amount: float) -> None:
        if bucket == _INTRADAY_BUCKET:
            self._intraday_avail -= amount
        else:
            self._positional_avail -= amount

    def _bucket_add_avail(self, bucket: str, amount: float) -> None:
        if bucket == _INTRADAY_BUCKET:
            self._intraday_avail += amount
        else:
            self._positional_avail += amount

    def _bucket_add_reserved(self, bucket: str, amount: float) -> None:
        if bucket == _INTRADAY_BUCKET:
            self._intraday_reserved += amount
        else:
            self._positional_reserved += amount

    def _bucket_deduct_reserved(self, bucket: str, amount: float) -> None:
        if bucket == _INTRADAY_BUCKET:
            self._intraday_reserved -= amount
        else:
            self._positional_reserved -= amount

    def _bucket_add_used(self, bucket: str, amount: float) -> None:
        if bucket == _INTRADAY_BUCKET:
            self._intraday_used += amount
        else:
            self._positional_used += amount

    def _bucket_deduct_used(self, bucket: str, amount: float) -> None:
        if bucket == _INTRADAY_BUCKET:
            self._intraday_used -= amount
        else:
            self._positional_used -= amount

    # ── invariant (FM2, FM11, INV7) ──────────────────────────────────────────

    def _check_invariant(self, mutation_type: str, context_id: str) -> None:
        """
        Verify available + reserved + used == total for both buckets combined.
        Delegates to assert_capital_invariant (INV7 refactor: behavior-preserving).

        fund_manager tracks _total directly (initial broker balance +/- all PnL).
        Passes cash_floor=self._total and realized_pnl_today=0.0 so that
        compute_rhs returns _total unchanged — equivalent to the previous
        inline check.

        On violation (FM11 + FM19 / BL-9):
          1. hard_kill first (if kill_switch wired) -- cancels in-flight orders
             immediately; wrapped in its own try/except so the original
             invariant exception always propagates even if the kill path fails.
          2. on_critical_failure callback next (legacy soft-kill / notifier
             wiring for other critical-signal callers; unchanged).
          3. raise last -- callers always see CapitalInvariantViolation.
        """
        total_avail = self._intraday_avail + self._positional_avail
        total_reserved = self._intraday_reserved + self._positional_reserved
        total_used = self._intraday_used + self._positional_used
        try:
            # H-1: per-bucket INV6 guard. Global sum check alone can hide
            # bucket overflow (one bucket negative, other positive enough to
            # offset, sum passes). Checking each bucket against its cap
            # surfaces NEGATIVE_MARGIN_AVAILABLE when reserved+used exceeds
            # the bucket's share of _total (e.g. after sync_from_broker
            # shrinks the broker balance).
            if self._intraday_avail < -_INVARIANT_TOLERANCE:
                assert_capital_invariant(
                    margin_available=self._intraday_avail,
                    margin_reserved=self._intraday_reserved,
                    margin_used=self._intraday_used,
                    cash_floor=self._total * self._intraday_pct,
                    realized_pnl_today=0.0,
                    bucket="intraday",
                    mutation_type=mutation_type,
                    reservation_id=context_id,
                    tolerance=_INVARIANT_TOLERANCE,
                )
            if self._positional_avail < -_INVARIANT_TOLERANCE:
                assert_capital_invariant(
                    margin_available=self._positional_avail,
                    margin_reserved=self._positional_reserved,
                    margin_used=self._positional_used,
                    cash_floor=self._total * self._positional_pct,
                    realized_pnl_today=0.0,
                    bucket="positional",
                    mutation_type=mutation_type,
                    reservation_id=context_id,
                    tolerance=_INVARIANT_TOLERANCE,
                )
            assert_capital_invariant(
                margin_available=total_avail,
                margin_reserved=total_reserved,
                margin_used=total_used,
                cash_floor=self._total,        # rhs = _total + min(0,0) = _total
                realized_pnl_today=0.0,        # fund_manager tracks _total directly
                bucket="global",
                mutation_type=mutation_type,
                reservation_id=context_id,
                tolerance=_INVARIANT_TOLERANCE,
            )
        except CapitalInvariantViolation as exc:
            log_exception(self._log, exc)
            # FM19 / BL-9: hard_kill FIRST -- provably corrupted capital state
            # warrants immediate order cancellation, not just a soft block.
            if self._kill_switch is not None:
                try:
                    self._kill_switch.hard_kill(
                        reason=f"capital_invariant_violated: {exc}",
                        triggered_by="fund_manager._check_invariant",
                    )
                except Exception as kse:
                    self._log.critical(
                        "kill_switch.hard_kill failed during invariant violation",
                        extra={"kill_error": str(kse)},
                    )
            else:
                self._log.critical(
                    "invariant violation with kill_switch=None; "
                    "on_critical_failure path (if wired) still runs"
                )
            # Legacy soft-kill / notifier wiring (unchanged).
            if self._on_critical is not None:
                try:
                    self._on_critical(str(exc))
                except Exception as cbe:
                    self._log.error(
                        "on_critical_failure callback raised",
                        extra={"error": str(cbe)},
                    )
            raise

    # ── ledger write (FM10 / BL-5 write-ahead) ────────────────────────────────

    def _write_ledger(
        self,
        *,
        ts: str,
        entry_type: str,
        amount: float,
        bucket: str,
        balance_before: float,
        balance_after: float,
        signal_id: Optional[str] = None,
        reservation_id: Optional[str] = None,
        reason: Optional[str] = None,
        direction: Optional[str] = None,
        trade_id: Optional[str] = None,
        margin_delta: float = 0.0,
        pnl_delta: float = 0.0,
        costs: float = 0.0,
    ) -> None:
        """
        Write one row to fm_ledger inside a transaction (FM10 / BL-5).

        BL-5 contract: this is a WRITE-AHEAD entry. Callers invoke it BEFORE
        mutating in-memory bucket state. The row persists the intent; the
        in-memory mutation catches up next. If this INSERT raises, the caller
        skips the mutation (propagates the exception). If this INSERT succeeds
        and the mutation crashes before completing, rehydrate (B.2) replays
        fm_ledger rows to rebuild in-memory state.

        entry_type is validated by a CHECK constraint in the schema; an
        unknown value raises sqlite3.IntegrityError at INSERT time.
        """
        try:
            with self._store.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO fm_ledger
                        (ts, entry_type, amount, bucket,
                         balance_before, balance_after,
                         signal_id, reservation_id, reason,
                         session_id, direction, trade_id,
                         margin_delta, pnl_delta, costs)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ts, entry_type, amount, bucket,
                     balance_before, balance_after,
                     signal_id, reservation_id, reason,
                     self._session_id, direction, trade_id,
                     margin_delta, pnl_delta, costs),
                )
        except Exception as exc:
            log_exception(self._log, exc)
            raise

    # ── guard ─────────────────────────────────────────────────────────────────

    def _assert_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError(
                "FundManager.initialize(broker_balance) must be called before "
                "any capital operation"
            )
