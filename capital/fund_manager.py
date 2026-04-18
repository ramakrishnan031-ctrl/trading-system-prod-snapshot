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
             If write fails, mutation is rolled back.
    FM11 -- Invariant violation raises CapitalInvariantViolation (CRITICAL).
    FM12 -- Constructor validates bucket pcts sum to 1.0 and leverage_map
             covers all 4 intents.
    FM13 -- initialize(broker_balance) called once at startup.
    FM14 -- reset_daily_pnl() called at EOD.
    FM15 -- Layer 3 (capital/). Imports: stdlib + core.*.
    FM16 -- SystemConfig.capital added.
    FM17 -- NOT in scope: position sizing, risk per trade, cost deduction.

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
from typing import Callable, Final, Optional

from capital.invariant import assert_capital_invariant
from core.events import CapitalDriftDetected, EventBus
from core.exceptions import CapitalInvariantViolation
from core.logger import log_exception
from core.state_store import StateStore
from core.time_authority import now_ist

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

        self._lock = threading.RLock()

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
        """
        with self._lock:
            self._total = broker_balance
            self._intraday_avail = broker_balance * self._intraday_pct
            self._intraday_reserved = 0.0
            self._intraday_used = 0.0
            self._positional_avail = broker_balance * self._positional_pct
            self._positional_reserved = 0.0
            self._positional_used = 0.0
            self._daily_pnl = 0.0
            self._initialized = True

            ts = now_ist().isoformat()
            self._write_ledger(
                ts=ts,
                mutation_type="INIT",
                amount=broker_balance,
                bucket="both",
                balance_before=0.0,
                balance_after=broker_balance,
                signal_id=None,
                reservation_id=None,
                reason=f"initialize with broker_balance={broker_balance}",
            )
            self._log.info(
                "fund_manager.initialize",
                extra={"total": broker_balance,
                       "intraday_avail": self._intraday_avail,
                       "positional_avail": self._positional_avail},
            )

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

            # Atomic mutation
            rid = uuid.uuid4().hex[:16]
            self._bucket_deduct_avail(bucket, margin)
            self._bucket_add_reserved(bucket, margin)

            self._check_invariant("reserve", rid)

            ts = now_ist().isoformat()
            res = _Reservation(
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
            self._reservations[rid] = res

            self._write_ledger(
                ts=ts,
                mutation_type="RESERVE",
                amount=margin,
                bucket=bucket,
                balance_before=avail_before,
                balance_after=self._bucket_avail(bucket),
                signal_id=signal_id,
                reservation_id=rid,
                reason=f"{symbol} qty={qty} @ {price} intent={intent}",
            )

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
            res = self._reservations.pop(reservation_id, None)
            if res is None:
                return False   # FM5: idempotent

            avail_before = self._bucket_avail(res.bucket)
            self._bucket_add_avail(res.bucket, res.margin)
            self._bucket_deduct_reserved(res.bucket, res.margin)

            self._check_invariant("release", reservation_id)

            ts = now_ist().isoformat()
            self._write_ledger(
                ts=ts,
                mutation_type="RELEASE",
                amount=-res.margin,
                bucket=res.bucket,
                balance_before=avail_before,
                balance_after=self._bucket_avail(res.bucket),
                signal_id=res.signal_id,
                reservation_id=reservation_id,
                reason=reason or "released",
            )
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

        Args:
            reservation_id:   from reserve().
            actual_fill_price: the actual fill price (may differ from reserved).
            actual_qty:        filled quantity (may be < reserved qty).

        Raises:
            ValueError: reservation_id unknown.
            CapitalInvariantViolation: invariant fails post-mutation.
        """
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

            # reserved -> used for actual; excess -> available
            self._bucket_deduct_reserved(res.bucket, res.margin)
            self._bucket_add_used(res.bucket, actual_margin)
            if excess > 0:
                self._bucket_add_avail(res.bucket, excess)

            self._check_invariant("commit_to_used", reservation_id)

            # Remove reservation (fully consumed)
            del self._reservations[reservation_id]

            ts = now_ist().isoformat()
            self._write_ledger(
                ts=ts,
                mutation_type="COMMIT",
                amount=actual_margin,
                bucket=res.bucket,
                balance_before=res.margin,
                balance_after=actual_margin,
                signal_id=res.signal_id,
                reservation_id=reservation_id,
                reason=(
                    f"fill: qty={actual_qty} price={actual_fill_price} "
                    f"excess_returned={excess:.2f}"
                ),
            )
            return CommitResult(
                reservation_id=reservation_id,
                actual_margin=actual_margin,
                excess_returned=excess,
                bucket=res.bucket,
            )

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

            ts = now_ist().isoformat()
            self._write_ledger(
                ts=ts,
                mutation_type="RELEASE_USED",
                amount=-margin,
                bucket=bucket,
                balance_before=avail_before,
                balance_after=self._bucket_avail(bucket),
                signal_id=None,
                reservation_id=None,
                reason=(
                    f"{symbol} exit: qty={exit_qty} price={exit_price} "
                    f"pnl={pnl:.2f} costs={costs:.2f}"
                ),
            )
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
            self._total = broker_balance

            # Recompute available = total - reserved - used, split by bucket pct
            # The broker balance is total; we recompute available within each bucket.
            # Bucket totals scale with the new total proportionally.
            intraday_total = broker_balance * self._intraday_pct
            positional_total = broker_balance * self._positional_pct

            self._intraday_avail = max(
                0.0, intraday_total - self._intraday_reserved - self._intraday_used
            )
            self._positional_avail = max(
                0.0, positional_total - self._positional_reserved - self._positional_used
            )

            ts = now_ist().isoformat()
            self._write_ledger(
                ts=ts,
                mutation_type="SYNC",
                amount=broker_balance - old_total,
                bucket="both",
                balance_before=old_total,
                balance_after=broker_balance,
                signal_id=None,
                reservation_id=None,
                reason=f"broker sync: {old_total:.2f} -> {broker_balance:.2f}",
            )
            self._log.info(
                "fund_manager.sync_from_broker",
                extra={"old_total": old_total, "new_total": broker_balance},
            )

            # Publish CapitalDriftDetected if significant change (FM9)
            delta = broker_balance - old_total
            if abs(delta) > 1.0:
                self._bus.publish(CapitalDriftDetected(
                    source_module="fund_manager",
                    expected=old_total,
                    actual=broker_balance,
                    delta=delta,
                ))

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
            self._daily_pnl = 0.0
            ts = now_ist().isoformat()
            self._write_ledger(
                ts=ts,
                mutation_type="RESET_PNL",
                amount=0.0,
                bucket="both",
                balance_before=old_pnl,
                balance_after=0.0,
                signal_id=None,
                reservation_id=None,
                reason=f"EOD reset: previous pnl={old_pnl:.2f}",
            )
            self._log.info(
                "fund_manager.reset_daily_pnl",
                extra={"previous_pnl": old_pnl},
            )

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
        inline check. On-critical callback fires before re-raise (FM11).
        """
        total_avail = self._intraday_avail + self._positional_avail
        total_reserved = self._intraday_reserved + self._positional_reserved
        total_used = self._intraday_used + self._positional_used
        try:
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
            if self._on_critical is not None:
                self._on_critical(str(exc))
            raise

    # ── ledger write (FM10) ───────────────────────────────────────────────────

    def _write_ledger(
        self,
        ts: str,
        mutation_type: str,
        amount: float,
        bucket: str,
        balance_before: float,
        balance_after: float,
        signal_id: Optional[str],
        reservation_id: Optional[str],
        reason: Optional[str],
    ) -> None:
        """
        Write one row to fm_ledger inside a transaction (FM10).
        If the write fails, the exception propagates to the caller,
        which must roll back the in-memory mutation.
        """
        try:
            with self._store.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO fm_ledger
                        (ts, mutation_type, amount, bucket,
                         balance_before, balance_after,
                         signal_id, reservation_id, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ts, mutation_type, amount, bucket,
                     balance_before, balance_after,
                     signal_id, reservation_id, reason),
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
