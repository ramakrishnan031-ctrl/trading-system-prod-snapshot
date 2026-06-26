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

import math
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


def resolve_bucket_allocation(
    *,
    conditional_enabled: bool,
    delivery_active: bool,
    intraday_active: bool,
    intraday_pct: float,
    positional_pct: float,
) -> tuple[float, float]:
    """SLICE2.5-PHASE-3 (B): the EFFECTIVE (intraday_pct, positional_pct) split.

    Pure + deterministic (unit-tested directly; main.py calls it and passes the
    result to FundManager, which is otherwise UNCHANGED). Always sums to 1.0 so
    the FM12 ctor invariant holds.

      conditional_enabled FALSE (default) -> the fixed config split, byte-for-byte
                                             unchanged (zero behaviour change).
      conditional_enabled TRUE:
        only-intraday (not delivery_active)            -> (1.0, 0.0)
        only-delivery (delivery_active, not intraday)  -> (0.0, 1.0)
        BOTH active                                    -> the config split
        neither active                                 -> (1.0, 0.0)  [safe idle]

    delivery 0% => every delivery reserve() rejects "Insufficient positional
    capital" and never borrows intraday (the no-borrow guarantee is already in
    reserve(): it consults ONLY the intent's bucket).
    """
    if not conditional_enabled:
        return intraday_pct, positional_pct
    if delivery_active and not intraday_active:
        delivery = 1.0
    elif delivery_active and intraday_active:
        delivery = positional_pct
    else:  # not delivery_active (incl. neither active) -> all intraday
        delivery = 0.0
    return 1.0 - delivery, delivery


# FIX-113: Invariant tolerance (rupees) for floating-point comparisons.
# Why 1.0 is appropriate:
#   - Paper mode: LTP-based fills vs limit-price orders introduce ±0.05-0.50 rounding
#   - Live mode: broker-reported margin vs local calc can differ by ±0.10-0.50 due to:
#       * Broker using different rounding for stamp duty/GST
#       * Intraday leverage timing (margin released async)
#   - 1.0 rupee catches genuine errors (10+ rupee drift) while tolerating noise
#   - Too tight (e.g., 0.1) would false-alarm on legitimate rounding differences
_INVARIANT_TOLERANCE = 1.0

# FIX-166 F17: canonical copy now in core.constants
from core.constants import PRODUCT_TO_INTENT as _PRODUCT_TO_INTENT


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
    excess_returned: float  # margin adjustment to available (positive=returned, negative=deficit)
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
    slm_buffer: float = 0.0  # FIX-090: buffer held for SL-M margin


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
                         daily_loss_limit_pct=0.03,
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
        # BUILD 1 (#1, 24-Jun): single daily-loss source. The post-close realized
        # breach ₹ limit = daily_loss_limit_pct × current capital (was a fixed
        # absolute ₹). Default is the conservative 3%, not the stale 10000 (#A.4).
        daily_loss_limit_pct: float = 0.03,
        leverage_map: Optional[dict[str, float]] = None,
        on_daily_loss_breach: Optional[Callable[[], None]] = None,
        on_critical_failure: Optional[Callable[[str], None]] = None,
        kill_switch: Optional["KillSwitch"] = None,
        slm_margin_buffer_pct: float = 0.05,  # FIX-090
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
        if not (0 < daily_loss_limit_pct <= 1):
            raise ValueError(
                f"daily_loss_limit_pct must be > 0 and <= 1, got {daily_loss_limit_pct}"
            )

        self._store = state_store
        self._bus = bus
        self._log = logger
        self._intraday_pct = intraday_bucket_pct
        self._positional_pct = positional_bucket_pct
        self._daily_loss_limit_pct = daily_loss_limit_pct
        self._leverage_map = dict(leverage_map)
        self._slm_buffer_pct = slm_margin_buffer_pct  # FIX-090
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

        # FIX-051: _daily_pnl removed; read from fm_ledger SQL instead
        self._initialized: bool = False

        # FM6: active reservations
        self._reservations: dict[str, _Reservation] = {}

        # FIX-035: unrealized MTM tracking per trade_id
        self._unrealized_mtm: dict[str, float] = {}

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def portfolio_lock(self):
        """
        Audit 1.2 / Portfolio Lock: expose the internal RLock for callers
        that need approve+reserve to be a single critical section.

        signal_processor wraps RiskEngine.approve + FundManager.reserve in
        `with fm.portfolio_lock:` so two concurrent signals targeting the
        same sector/bucket cannot both pass approve and then both reserve.
        Without the lock, sector-exposure / max-positions checks race
        against concurrent reserves -- a 20%-cap sector can overshoot
        because both signals saw "19% before me" and both reserved.

        It is an RLock, so reserve()/release() (which take the same lock
        internally) can be called by code holding portfolio_lock without
        deadlock.
        """
        return self._lock

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
            # FIX-051: _daily_pnl removed; read from SQL
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

            if not isinstance(qty, (int, float)) or not isinstance(price, (int, float)):
                return ReservationResult(
                    success=False, reservation_id="", margin=0.0, bucket="",
                    reason_if_failed=f"Invalid numeric input: qty={qty}, price={price}",
                )
            try:
                if math.isnan(qty) or math.isnan(price) or math.isinf(qty) or math.isinf(price):
                    return ReservationResult(
                        success=False, reservation_id="", margin=0.0, bucket="",
                        reason_if_failed=f"Invalid numeric input: qty={qty}, price={price}",
                    )
            except TypeError:
                pass

            bucket = self._bucket_for_intent(intent)
            base_margin = required_margin(qty, price, intent, self._leverage_map)

            # FIX-090: Add SL-M margin buffer (5% for unknown fill price risk)
            # Buffer is held until SL-M is accepted, then released via release_slm_buffer()
            slm_buffer = base_margin * self._slm_buffer_pct
            total_margin = base_margin + slm_buffer

            avail_before = self._bucket_avail(bucket)

            if total_margin > avail_before:
                return ReservationResult(
                    success=False,
                    reservation_id="",
                    margin=total_margin,
                    bucket=bucket,
                    reason_if_failed=(
                        f"Insufficient {bucket} capital: need {total_margin:.2f}, "
                        f"have {avail_before:.2f}"
                    ),
                )

            # BL-5: write-ahead. Project the post-mutation balance, write the
            # ledger row first, then execute the in-memory mutation.
            rid = uuid.uuid4().hex[:16]
            ts = now_ist().isoformat()
            projected_after = avail_before - total_margin
            self._write_ledger(
                ts=ts,
                entry_type="RESERVE",
                amount=total_margin,
                bucket=bucket,
                balance_before=avail_before,
                balance_after=projected_after,
                signal_id=signal_id,
                reservation_id=rid,
                reason=f"{symbol} qty={qty} @ {price} intent={intent} (base={base_margin:.2f} buffer={slm_buffer:.2f})",
                margin_delta=+total_margin,
            )

            # FM18: pure mutation via shared helper (used by both this public
            # path and rehydrate replay). Public path: ledger then apply then
            # invariant. Replay path: apply only (no ledger, no invariant).
            self._apply_reserve(
                reservation_id=rid,    # NM-4: local `rid` is a tight-scope alias
                bucket=bucket,
                margin=total_margin,
                symbol=symbol,
                qty=qty,
                price=price,
                intent=intent,
                signal_id=signal_id,
                ts=ts,
                slm_buffer=slm_buffer,  # FIX-090
            )

            # C.1: capture violation, defer hard_kill to after lock release.
            try:
                self._check_invariant("reserve", rid)
            except CapitalInvariantViolation as exc:
                _violation = exc
            else:
                _violation = None

            if _violation is None:
                _result = ReservationResult(
                    success=True,
                    reservation_id=rid,
                    margin=total_margin,  # FIX-090: includes buffer
                    bucket=bucket,
                    reason_if_failed="",
                )

        # Outside lock
        if _violation is not None:
            self._handle_invariant_violation(_violation)
            raise _violation
        return _result

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

            # C.1: capture violation, defer hard_kill to after lock release.
            try:
                self._check_invariant("release", reservation_id)
            except CapitalInvariantViolation as exc:
                _violation = exc
            else:
                _violation = None

        # Outside lock
        if _violation is not None:
            self._handle_invariant_violation(_violation)
            raise _violation
        return True

    def release_slm_buffer(self, reservation_id: str, reason: str = "") -> bool:
        """
        FIX-090: Release the SL-M margin buffer for a reservation.

        Called after SL-M order is successfully accepted by broker. Releases
        the buffer (typically 5% of base margin) back to available capital.

        Returns:
            True  -- buffer released successfully.
            False -- reservation_id not found or buffer already released (idempotent).

        Raises:
            CapitalInvariantViolation: invariant check fails post-mutation.
        """
        with self._lock:
            self._assert_initialized()
            res = self._reservations.get(reservation_id)
            if res is None:
                self._log.debug(
                    "fund_manager.release_slm_buffer_unknown",
                    extra={"reservation_id": reservation_id},
                )
                return False

            if res.slm_buffer <= 0.0:
                self._log.debug(
                    "fund_manager.release_slm_buffer_already_released",
                    extra={"reservation_id": reservation_id},
                )
                return False

            # Release buffer: deduct from reserved, add to available
            buffer_amount = res.slm_buffer
            bucket = res.bucket
            avail_before = self._bucket_avail(bucket)
            ts = now_ist().isoformat()

            # Write ledger (FIX-090: use RELEASE entry_type, reason distinguishes buffer release)
            self._write_ledger(
                ts=ts,
                entry_type="RELEASE",
                amount=buffer_amount,
                bucket=bucket,
                balance_before=avail_before,
                balance_after=avail_before + buffer_amount,
                reservation_id=reservation_id,
                reason=reason or "SL-M buffer released",
                margin_delta=-buffer_amount,
            )

            # Update reservation: reduce margin and clear buffer
            self._bucket_add_avail(bucket, buffer_amount)
            self._bucket_deduct_reserved(bucket, buffer_amount)

            # Update the reservation in-place
            updated_res = _Reservation(
                reservation_id=res.reservation_id,
                symbol=res.symbol,
                qty=res.qty,
                price=res.price,
                intent=res.intent,
                margin=res.margin - buffer_amount,
                bucket=res.bucket,
                signal_id=res.signal_id,
                ts=res.ts,
                slm_buffer=0.0,  # Buffer now released
            )
            self._reservations[reservation_id] = updated_res

            # Invariant check
            try:
                self._check_invariant("release_slm_buffer", reservation_id)
            except CapitalInvariantViolation as exc:
                _violation = exc
            else:
                _violation = None

        # Outside lock
        if _violation is not None:
            self._handle_invariant_violation(_violation)
            raise _violation

        self._log.info(
            "fund_manager.slm_buffer_released",
            extra={
                "reservation_id": reservation_id,
                "buffer_amount": buffer_amount,
                "bucket": bucket,
            },
        )
        return True

    def top_up_reservation(
        self,
        reservation_id: str,
        additional_margin: float,
        reason: str = "",
    ) -> ReservationResult:
        """
        FIX-075: Increase an existing reservation's margin.

        Used when price drift detection requires more margin than originally
        reserved. Atomically checks available capital and increases the
        reservation if sufficient funds exist.

        Args:
            reservation_id: existing reservation to top up
            additional_margin: additional margin to add (must be > 0)
            reason: audit trail note (e.g., "price drift 2% → 102")

        Returns:
            ReservationResult(success=True, ...) if top-up succeeded
            ReservationResult(success=False, ...) if insufficient capital

        Raises:
            ValueError: if reservation_id not found or additional_margin <= 0
            CapitalInvariantViolation: invariant check fails post-mutation
        """
        if additional_margin <= 0:
            raise ValueError(f"additional_margin must be > 0, got {additional_margin}")

        with self._lock:
            self._assert_initialized()
            res = self._reservations.get(reservation_id)
            if res is None:
                raise ValueError(
                    f"reservation_id {reservation_id!r} not found; "
                    f"cannot top up unknown reservation"
                )

            bucket = res.bucket
            avail_before = self._bucket_avail(bucket)

            if additional_margin > avail_before:
                # Insufficient capital for top-up
                return ReservationResult(
                    success=False,
                    reservation_id=reservation_id,
                    margin=additional_margin,
                    bucket=bucket,
                    reason_if_failed=(
                        f"insufficient {bucket} capital for top-up: "
                        f"need ₹{additional_margin:.2f}, avail ₹{avail_before:.2f}"
                    ),
                )

            # Top-up succeeds: deduct from available, add to reserved
            projected_after = avail_before - additional_margin
            ts = now_ist().isoformat()
            self._write_ledger(
                ts=ts,
                entry_type="TOP_UP",
                amount=additional_margin,
                bucket=bucket,
                balance_before=avail_before,
                balance_after=projected_after,
                signal_id=res.signal_id,
                reservation_id=reservation_id,
                reason=reason or "price drift top-up",
                margin_delta=additional_margin,
            )

            # Update in-memory state
            self._bucket_deduct_avail(bucket, additional_margin)
            self._bucket_add_reserved(bucket, additional_margin)

            # Update reservation record with new margin
            # dataclass is frozen, so create a new instance
            new_margin = res.margin + additional_margin
            self._reservations[reservation_id] = _Reservation(
                reservation_id=res.reservation_id,
                symbol=res.symbol,
                qty=res.qty,
                price=res.price,
                intent=res.intent,
                bucket=res.bucket,
                margin=new_margin,
                signal_id=res.signal_id,
                ts=res.ts,
                slm_buffer=res.slm_buffer,
            )

            # FIX-165a: invariant check inside lock (was outside — race condition)
            try:
                self._check_invariant("TOP_UP", reservation_id)
            except CapitalInvariantViolation as exc:
                _violation = exc
            else:
                _violation = None

            if _violation is None:
                _result = ReservationResult(
                    success=True,
                    reservation_id=reservation_id,
                    margin=new_margin,
                    bucket=bucket,
                    reason_if_failed="",
                )

        # Outside lock: handle violation (FIX-165a)
        if _violation is not None:
            self._handle_invariant_violation(_violation)
            raise _violation
        return _result

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
                # Allow negative excess: when fill price > reserved price,
                # the deficit must be deducted from available to keep the
                # invariant balanced.
                excess = res.margin - actual_margin

                # BL-5: ledger row first. COMMIT is a bucket-internal reshape
                # (reserved -> used, excess -> avail), so margin_delta=0.
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
            # C.1: lock is released by `with` __exit__ before this handler
            # runs, so kill_switch.hard_kill below is safe (no deadlock).
            # Invariant violations also flow through _handle_invariant_violation
            # to preserve on_critical_failure dispatch (previously done from
            # inside _check_invariant). hard_kill is idempotent at the
            # kill_switch state machine so the BL-4 commit-specific kill
            # below remains safe to fire alongside.
            if isinstance(exc, CapitalInvariantViolation):
                self._handle_invariant_violation(exc)
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
            # FIX-051: _daily_pnl removed; will read from SQL for loss check

            # C.1: capture violation, defer hard_kill to after lock release.
            try:
                self._check_invariant("release_used", symbol)
            except CapitalInvariantViolation as exc:
                _violation = exc
                _result = None
            else:
                _violation = None
                # FM7: check daily loss limit after updating PnL (existing
                # behavior: only runs when invariant was OK; on violation
                # the state is corrupt and the loss check is moot).
                # FIX-051: Read daily PnL from SQL instead of in-memory accumulator
                # BUILD 1 (#1, 24-Jun): the ₹ limit is derived from
                # daily_loss_limit_pct × current capital (self._total) — the SAME
                # pct the pre-trade gate uses, just on a realized (post-close)
                # basis. Replaces the deleted absolute capital.daily_loss_limit.
                today = now_ist().date().isoformat()
                daily_pnl = self._store.get_daily_realized_net_pnl(today)
                loss_limit = self._daily_loss_limit_pct * self._total
                if self._total > 0 and daily_pnl <= -loss_limit:
                    self._log.critical(
                        "fund_manager.daily_loss_breach",
                        extra={"daily_pnl": daily_pnl,
                               "limit": loss_limit,
                               "daily_loss_limit_pct": self._daily_loss_limit_pct,
                               "capital": self._total},
                    )
                    if self._on_loss_breach is not None:
                        self._on_loss_breach()

                _result = ReleaseResult(
                    reservation_id="",
                    margin_released=margin,
                    bucket=bucket,
                    pnl_delta=pnl,
                )

        # Outside lock
        if _violation is not None:
            self._handle_invariant_violation(_violation)
            raise _violation
        return _result

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

            # C.1 (2026-04-25): collect drift events to publish AFTER lock
            # release. Publishing inside the capital lock can deadlock if a
            # subscriber blocks on a downstream resource (kill_switch,
            # rate_limiter, broker call) while another thread holds that
            # resource and waits on the capital lock.
            _pending_publishes: list[CapitalDriftDetected] = []

            # CapitalDriftDetected if significant TOTAL change (FM9)
            delta = broker_balance - old_total
            if abs(delta) > 1.0:
                _pending_publishes.append(CapitalDriftDetected(
                    source_module="fund_manager",
                    expected=old_total,
                    actual=broker_balance,
                    delta=delta,
                ))

            # H-1: detect bucket overflow (either bucket went negative after
            # sync). Capture BEFORE _check_invariant fires -- the invariant
            # path raises and would short-circuit the append if ordered after.
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
                _pending_publishes.append(CapitalDriftDetected(
                    source_module="fund_manager_bucket_overflow",
                    expected=0.0,   # buckets should never go negative
                    actual=gap,     # most-negative bucket available
                    delta=abs(gap), # rupee magnitude for BL-2 tiering
                ))

            # H-1: invariant check now runs on every sync. Per-bucket INV6
            # guard fires CapitalInvariantViolation on bucket overflow.
            # C.1: capture violation, defer hard_kill to after lock release.
            try:
                self._check_invariant("sync_from_broker", self._session_id)
            except CapitalInvariantViolation as exc:
                _violation = exc
            else:
                _violation = None

        # Outside lock: publish events first, then dispatch violation.
        for evt in _pending_publishes:
            try:
                self._bus.publish(evt)
            except Exception as pub_exc:
                self._log.error(
                    "fund_manager.publish_drift_failed: %s",
                    pub_exc,
                )

        if _violation is not None:
            self._handle_invariant_violation(_violation)
            raise _violation

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

    def count_live_reservations(self) -> int:
        """Return the number of live (uncommitted) entry reservations (FIX-185).

        Every accepted entry holds exactly one reservation from reserve() until
        the entry FILLS (commit pops it as the trade flips to OPEN) or fails
        (release pops it). So this count is the authoritative number of in-flight
        positions that have reserved capital but are NOT yet OPEN/PARTIAL — i.e.
        reserved-but-not-placed plus PENDING_FILL. The risk_engine OPEN_POSITIONS
        check uses it as a TOCTOU-proof hard-cap input that does not depend on the
        signal_processor's in-memory in-flight snapshot (which can under-count in
        a restart burst). Read under self._lock; callers already holding
        portfolio_lock (an RLock) re-acquire it safely.
        """
        with self._lock:
            return len(self._reservations)

    def get_snapshot(self) -> CapitalSnapshot:
        """Return a frozen, consistent point-in-time view of capital state (FM8).

        FIX-051: daily_realized_pnl is read from SQL (fm_ledger) instead of in-memory float.
        """
        with self._lock:
            # FIX-051: Read daily PnL from SQL to avoid float drift
            today = now_ist().date().isoformat()
            daily_pnl = self._store.get_daily_realized_net_pnl(today)

            return CapitalSnapshot(
                total=self._total,
                intraday_avail=self._intraday_avail,
                intraday_reserved=self._intraday_reserved,
                intraday_used=self._intraday_used,
                positional_avail=self._positional_avail,
                positional_reserved=self._positional_reserved,
                positional_used=self._positional_used,
                daily_realized_pnl=daily_pnl,
                ts=now_ist().isoformat(),
            )

    def update_unrealized_mtm(self, trade_id: str, unrealized_pnl: float) -> None:
        """
        FIX-035: Update unrealized MTM for a trade (thread-safe).

        Args:
            trade_id:       Trade identifier.
            unrealized_pnl: Current unrealized P&L for this trade.
                           Positive = profit, negative = loss.
        """
        with self._lock:
            self._unrealized_mtm[trade_id] = unrealized_pnl

    def remove_unrealized_mtm(self, trade_id: str) -> None:
        """
        FIX-035: Remove unrealized MTM entry for a closed trade (thread-safe).

        Args:
            trade_id: Trade identifier to remove.
        """
        with self._lock:
            self._unrealized_mtm.pop(trade_id, None)

    def get_total_unrealized_mtm(self) -> float:
        """
        FIX-035: Return sum of all unrealized MTM (thread-safe).

        Returns the aggregate unrealized P&L across all tracked trades.
        Positive = net unrealized profit, negative = net unrealized loss.
        Returns 0.0 if no trades are tracked.
        """
        with self._lock:
            return sum(self._unrealized_mtm.values())

    def reset_daily_pnl(self) -> None:
        """Reset daily realized PnL to 0 at EOD. reserved/used NOT reset (FM14).

        FIX-051: Reads old_pnl from SQL (fm_ledger) instead of in-memory accumulator.
        The RESET_PNL ledger entry with negative pnl_delta brings the SQL sum back to 0.
        """
        with self._lock:
            # FIX-051: Read current PnL from SQL instead of in-memory float
            ts_now = now_ist()
            today = ts_now.date().isoformat()
            old_pnl = self._store.get_daily_realized_net_pnl(today)

            ts = ts_now.isoformat()
            # BL-5: ledger first, then zero-out
            # The pnl_delta=-old_pnl entry ensures SUM(pnl_delta) = 0 for the day
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
            # FIX-051: No in-memory _daily_pnl to zero out; SQL is the source of truth
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
            # lifecycle: bucket avail += pnl, _total += pnl.
            # FIX-051: _daily_pnl removed; SQL (fm_ledger.pnl_delta) is the source of truth.
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
                # FIX-051: No in-memory _daily_pnl to update; SQL has pnl_delta rows
                self._total += pnl
                replayed_pnl_rows += 1

            # Phase 3: invariant check ONCE (FM18). Wrap to distinguish
            # startup-replay corruption from a live mid-mutation break.
            # C.1 (2026-04-25): capture violation, defer hard_kill+on_critical
            # to after lock release.
            try:
                self._check_invariant("rehydrate", "BL-1")
            except CapitalInvariantViolation as exc:
                _violation = exc
            else:
                _violation = None

        # Outside lock
        if _violation is not None:
            self._handle_invariant_violation(_violation)
            raise CapitalStateInconsistent(
                f"Capital state invariant failed after rehydrate: {_violation}",
                anomalies=anomalies,
                replayed_trades=replayed_trades,
                replayed_pnl_rows=replayed_pnl_rows,
            ) from _violation

        # FIX-051: Read daily_pnl from SQL for logging
        today = now_ist().date().isoformat()
        daily_pnl = self._store.get_daily_realized_net_pnl(today)

        self._log.info(
            "fund_manager.rehydrate_complete",
            extra={
                "replayed_trades": replayed_trades,
                "replayed_pnl_rows": replayed_pnl_rows,
                "anomaly_count": len(anomalies),
                "daily_pnl": daily_pnl,
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

        # EF-5: prefer the reservation_id column on trades (populated from
        # order_placer.place via order_manager.create_trade). Fall back to
        # the two-hop lookup (signal_id -> first RESERVE row in fm_ledger)
        # for pre-EF-5 trade rows where the column is NULL.
        try:
            rid = trade["reservation_id"]
        except (KeyError, IndexError):
            rid = None
        if not rid:
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
                    reservation_id=rid,    # NM-4: local `rid` is a tight-scope alias
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
        reservation_id: str,
        bucket: str,
        margin: float,
        symbol: str,
        qty: int,
        price: float,
        intent: str,
        signal_id: Optional[str],
        ts: str,
        slm_buffer: float = 0.0,  # FIX-090
    ) -> None:
        """Move margin from avail to reserved; record the reservation.

        NM-4 (2026-04-26 audit): param renamed rid -> reservation_id so all
        three _apply_* helpers use the same canonical name.

        FIX-090: slm_buffer tracks the buffer portion held for SL-M margin.
        """
        self._bucket_deduct_avail(bucket, margin)
        self._bucket_add_reserved(bucket, margin)
        self._reservations[reservation_id] = _Reservation(
            reservation_id=reservation_id,
            symbol=symbol,
            qty=qty,
            price=price,
            intent=intent,
            margin=margin,
            bucket=bucket,
            signal_id=signal_id,
            ts=ts,
            slm_buffer=slm_buffer,  # FIX-090
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
        if excess != 0.0:
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

        C.1 (2026-04-25): on violation, this method ONLY raises
        CapitalInvariantViolation. It no longer fires hard_kill or
        on_critical inline; those are deferred to the public mutator that
        catches the exception OUTSIDE its `with self._lock` block (via
        _handle_invariant_violation). Holding the capital lock during
        kill_switch.hard_kill could deadlock if kill_switch's downstream
        (logging, broker cancel, rate_limiter) blocks while another thread
        waits on the capital lock.
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
            # C.1: hard_kill / on_critical moved to _handle_invariant_violation,
            # invoked by the public mutator AFTER lock release. Just re-raise
            # so the caller's lock-scoped try/except can capture and defer.
            raise

    def _handle_invariant_violation(
        self, exc: CapitalInvariantViolation
    ) -> None:
        """
        C.1 (2026-04-25): side-effect dispatch for an invariant violation.
        MUST be called with the capital lock RELEASED -- holding the lock
        during kill_switch.hard_kill risks deadlock if the kill path blocks
        on rate_limiter while another capital-mutating thread waits on the
        lock.

        Mirrors the behavior previously inlined in _check_invariant's catch:
          1. kill_switch.hard_kill (if wired)
          2. on_critical_failure callback (if wired)

        Both wrapped in best-effort try/except so the caller can always
        re-raise the original CapitalInvariantViolation cleanly.
        """
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
        if self._on_critical is not None:
            try:
                self._on_critical(str(exc))
            except Exception as cbe:
                self._log.error(
                    "on_critical_failure callback raised",
                    extra={"error": str(cbe)},
                )

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
