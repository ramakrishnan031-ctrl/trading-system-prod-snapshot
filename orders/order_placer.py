"""
orders/order_placer.py — Trading System v2

Purpose:
    Orchestrates the full order placement lifecycle for a single trade.
    Called by signal_processor with (symbol, side, qty, entry_price,
    sl_price, intent, signal_id, reservation_id). Creates the trade row,
    places all entry orders, starts fill monitoring, and commits (or
    releases) the capital reservation on fill/failure.

Locked Design Decisions:
    OP1  -- place() is the only public method called by signal_processor.
            Signature: place(symbol, side, qty, entry_price, sl_price,
            intent, signal_id, reservation_id) → None.
    OP2  -- place() is synchronous: creates trade, places orders, registers
            with order_monitor, returns. Fill handling is async (event-driven).
    OP3  -- tgt_price computed internally: entry ± (entry-sl)*rr_ratio.
            Default rr_ratio=2.0 (configurable at construction).
            LONG: tgt = entry + (entry - sl) * rr
            SHORT: tgt = entry - (sl - entry) * rr
    OP4  -- trade_id assigned via core.ids.new_trade_id() inside place().
            DB row created before any broker call. On broker failure,
            trade status set to FAILED and capital reservation released.
    OP5  -- In-memory fill map: internal_order_id → {trade_id, reservation_id,
            symbol, qty, leg}. Cleaned up on OrderFilled or FAILED.
    OP6  -- Subscribes to OrderFilled event at construction. Handler:
            commit capital, record fill in DB, update trade status to OPEN.
    OP7  -- On entry placement failure: set trade=FAILED, release reservation,
            cancel any partially placed orders best-effort.
    OP8  -- Thread-safe: _fill_map guarded by threading.Lock.
    OP9  -- order_protocol determined by intent: "INTRADAY" → default_protocol
            (LIMIT_TRIPLE by default; overridable at construction).
            Future: per-strategy config via scanner_configs.
    OP10 -- Sector passed as None (data/symbol_validator not yet built).
    OP11 -- Layer 5 (orders/). Imports orders/, core/, capital/, broker.

Last-Mile Gaps (locked 2026-04-16):
    OP-LM1 -- Kill-switch last-mile check. Immediately before engine.execute(),
              if kill_switch.is_active("entry"): mark trade FAILED, release
              reservation, raise OrderRejectedError("kill_switch_active_last_mile").
              kill_switch injected at construction (optional; None = disabled).
    OP-LM2 -- Reservation release on placement failure. Every BrokerError and
              soft-failure path calls fund_manager.release(reservation_id, reason)
              before returning/raising. Implemented via _handle_placement_failure().
    OP-LM3 -- Empty broker_order_id treated as failure. Validated in protocol
              files (order_protocol_limit.py, order_protocol_co.py) immediately
              after each adapter.place_order() call. Raises OrderRejectedError.

BL-8 (locked 2026-04-19, Phase C.2):
    OP-BL8a -- _persist_entry_orders is ATOMIC. Uses
               OrderManager.insert_orders_atomic so all ENTRY/SL/TGT INSERTs
               commit together or none do. Pre-BL-8 the helper did three
               separate INSERTs and SWALLOWED any exception ("reconciler
               will rebuild from broker state"). That swallow was the
               silent-failure mode BL-8 closes -- reconciler is a backstop,
               not a primary recovery mechanism.
    OP-BL8b -- _persist_entry_orders now PROPAGATES exceptions. The caller
               (place()) catches, cancels every broker order it placed via
               _cancel_broker_orders, then routes through
               _handle_placement_failure to mark the trade FAILED + release
               the reservation, then fires kill_switch.hard_kill (capital
               tracking has broken: orders live at broker, no DB rows).
    OP-BL8c -- _handle_placement_failure accepts an optional
               broker_order_ids: Iterable[str] = (). When non-empty, runs
               _cancel_broker_orders before the existing FAILED+release
               flow. Single cleanup orchestrator for every failure path.
    OP-BL8d -- _cancel_broker_orders is best-effort. It iterates the IDs,
               calls adapter.cancel_order for each, and on result.success=False
               (or unexpected exception) logs CRITICAL with the grep-friendly
               tag CANCEL_FAILED_MANUAL_INTERVENTION_REQUIRED. It does NOT
               abort cleanup for the remaining orders.
    OP-BL8e -- hard_kill ONLY fires when DB persist fails AFTER broker
               accepted orders. Protocol-only failures (broker rejected
               cleanly) and CoPlusTgt soft failures (CO live, TGT dead) do
               NOT fire hard_kill: the protocol or place() already cancelled
               the broker side, so capital tracking is intact.
    OP-BL8f -- CoPlusTgt soft-failure path (success=False with CO live) now
               passes the live broker_order_ids to _handle_placement_failure
               so the CO is cancelled. Pre-BL-8 the CO was left live and the
               reconciler was the primary recovery; post-BL-8 the reconciler
               is a backstop.

BL-19 (locked 2026-04-19, Phase D.1):
    OP-BL19a -- place() wraps self._engine.execute in a retry loop scoped
                ONLY to BrokerRateLimit429Error. Other BrokerError subclasses
                still get a single attempt (ZA11 / OP7). The retry is narrow
                by design: a general retry would invite "retry everything"
                pattern creep.
    OP-BL19b -- The retry loop does NOT sleep. The adapter (BL-6) has
                already called rate_limiter.penalize() before raising the
                429, so the next iteration's acquire() blocks until the
                bucket thaws. That is the backoff pacing.
    OP-BL19c -- Max retries = rate_limit_backoff.max_placer_retries
                (default 3). On exhaustion, propagates via the existing
                _handle_placement_failure (FAILED + release + optional
                hard_kill -- identical to any other BrokerError).
    OP-BL19d -- Safe w.r.t. duplicate orders: each protocol raise cancels
                any in-flight legs it placed (LimitTriple cancels ENTRY on
                SL fail; CoPlusTgt has no inter-leg state on raise), so
                re-executing the protocol does not produce duplicates.

Naked-Short Fix (locked 2026-04-24, Phase A/2.1 + 3.4):
    OP-NS1 -- LIMIT_TRIPLE is two-phase. engine.execute places ENTRY only;
              SL + TGT are DEFERRED to fill time via engine.place_deferred_exits
              at the ACTUAL filled qty (event.filled_qty), not the requested
              qty. Closes the naked-short window where ENTRY partial-fills
              (e.g. 100/1000) and TGT executes at 1000 producing a 900 short.
    OP-NS2 -- _handle_entry_fill (COMPLETE path) places exits AFTER
              commit_to_used + record_entry_fill. Order: commit → record →
              exits → smart_tgt register. commit-first so capital accounting
              matches broker truth even if exits placement raises.
    OP-NS3 -- _on_order_status_changed (partial-cancel path, Audit #7) ALSO
              places exits for any non-zero qty_filled. Pre-NS1 the Audit #7
              path committed capital but never placed SL/TGT; the pre-NS1
              protocol had already placed them at requested qty — which WAS
              the naked-short bug. Post-NS1 both paths are symmetric: fill →
              commit → record → place_exits.
    OP-NS4 -- DELIVERY intent uses SL order_type (price = trigger_price);
              INTRADAY uses SL-M. Zerodha rejects SL-M on CNC. Branch lives
              inside LimitTripleProtocol.place_exits (OPL7).
    OP-NS5 -- On exit placement failure AFTER ENTRY fill: position is live
              with no SL. This is a capital-safety breach. Fire
              kill_switch.hard_kill with grep tag
              LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED. Do NOT re-raise
              inside the event handler; reconciler is the backstop.

Atomic Registration Fix (locked 2026-04-24, Phase A/1.1):
    OP-AR1 -- Per leg, _fill_map insert MUST happen BEFORE order_monitor.track().
              Pre-fix: track() added the order to _watched first; the poll
              thread could fire OrderFilled within microseconds and look up
              _fill_map[internal_id] -> empty -> ghost-entry. Post-fix:
              _fill_map is populated first; track() second; cleanup on
              track-failure pops the just-inserted _fill_map row in
              addition to rolling back successfully_tracked legs.
    OP-AR2 -- successfully_inserted (separate from successfully_tracked)
              tracks _fill_map inserts so the OP-EF2a cleanup path can pop
              both the tracked-AND-inserted set and the inserted-but-not-
              yet-tracked entry that triggered the raise.

EF-2 (locked 2026-04-19, Phase E.6):
    OP-EF2a -- The 3-leg track()+_fill_map loop that runs AFTER
               _persist_entry_orders is wrapped in a try/except. If
               order_monitor.track() raises (duplicate internal_id
               ValueError today, any future failure mode tomorrow),
               broker orders are live + DB rows exist + monitor coverage
               is partial. Cleanup runs:
                 1. Pop any _fill_map entries this trade added pre-raise.
                 2. untrack() every successfully-tracked leg (idempotent).
                 3. Emit CRITICAL log with grep tag EF2_TRACK_FAILURE_CLEANUP.
                 4. Delegate to _handle_placement_failure (BL-8 helper):
                    cancel broker orders + mark trade FAILED + release
                    reservation.
                 5. Propagate the original exception to signal_processor.
    OP-EF2b -- Does NOT fire kill_switch.hard_kill. Capital tracking stays
               consistent (cancel-or-log-CRITICAL + release reservation).
               This is "protocol-only failure" class per OP-BL8e; hard_kill
               is reserved for DB/broker drift scenarios (BL-4/BL-8/BL-9).
               Documented inline so future maintainers do not "helpfully
               add hard_kill for symmetry."
    OP-EF2c -- Trigger today is near-impossible (new_order_id uses UUID4,
               collision probability ~0). The gap is kept closed anyway:
               symmetric to BL-8's persist-failure gap; cheap defense for
               any future failure-mode addition (new _FillEntry ctor
               validation, new track() precondition, etc.).
    OP-EF2d -- The greenlight-framed race ("fill arrives before _fill_map
               populated") is NOT addressed. Live mode is poll-based
               (delayed-discovery, bounded by poll_interval_sec; tolerable).
               Paper production mode has a 10x safety margin at
               auto_fill_delay_sec=0.5 (synth fires at T+500ms; main thread
               populates _fill_map by T+50ms). Not production-reachable;
               no quarantine queue needed.

What This Module Does NOT Do:
    - Does not implement SL modification (smart_tgt_manager's job)
    - Does not implement EOD exit (eod_squareoff's job)
    - Does not implement order timeout (order_timeout's job)
    - Does not reconcile with broker (order_reconciler's job)
    - Does not send Telegram alerts (alerts/ module's job)
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, Final, Iterable, List, Optional

from broker.cost_calculator import CostCalculator
from broker.order_monitor import OrderMonitor
from broker.position_helpers import determine_close_direction  # FIX-190 (Bug A)
from broker.product_resolver import ProductResolver
from capital.fund_manager import FundManager
from capital.kill_switch import KillSwitch
from core.config_loader import RateLimitBackoffConfig, SmartTgtConfig
from core.events import EventBus, OrderFilled, OrderPartiallyTerminated, OrderStatusChanged, PositionClosed
from core.exceptions import BrokerError, BrokerRateLimit429Error, BrokerTimeoutError, OrderRejectedError, SLUnplaceableError
from core.ids import new_trade_id, truncate_tag_for_broker
from core.logger import log_exception
from core.time_authority import now_ist
from orders.entry_engine import EntryResult
from orders.full_entry_engine import FullEntryEngine
from orders.order_manager import OrderInsertSpec, OrderManager
from orders.price_math import (
    DEFAULT_TICK,
    EMERGENCY_EXIT_BUFFER_PCT,
    calc_tgt_price,
    marketable_limit_price,
    tier_slippage_tolerance_rs,
)
from orders.slippage_recorder import get_price_band
from orders.smart_tgt_manager import SmartTgtManager


def resolve_slippage_fraction(
    cfg: Any,
    symbol: str,
    strategy: str,
    price_band: Optional[str],
) -> tuple[float, str]:
    """Phase 3a — resolve the EFFECTIVE sl_fraction via the manual override
    hierarchy. Most-specific wins: Symbol > Strategy > Price Band > Global.
    Returns (fraction, source) where source is e.g. ``"symbol:IDEA"`` /
    ``"strategy:gap_fade"`` / ``"band:0-100"`` / ``"global"``. Pure (no I/O).

    Only meaningful for the sl_fraction mode; an empty/disabled `overrides`
    block falls straight through to the global `max_slippage_fraction`."""
    glob = getattr(cfg, "max_slippage_fraction", 0.22)
    ov = getattr(cfg, "overrides", None)
    if ov is not None and getattr(ov, "enabled", False):
        by_symbol = getattr(ov, "by_symbol", None) or {}
        if symbol and symbol in by_symbol:
            return by_symbol[symbol], f"symbol:{symbol}"
        by_strategy = getattr(ov, "by_strategy", None) or {}
        if strategy and strategy in by_strategy:
            return by_strategy[strategy], f"strategy:{strategy}"
        by_band = getattr(ov, "by_price_band", None) or {}
        if price_band and price_band in by_band:
            return by_band[price_band], f"band:{price_band}"
    return glob, "global"


def validate_slippage_overrides(
    overrides: Any,
    known_symbols: Optional[set] = None,
    known_strategies: Optional[set] = None,
    *,
    extreme_lo: float = 0.05,
    extreme_hi: float = 0.50,
) -> list[str]:
    """Phase 3a — startup sanity check for the override maps. Returns a list of
    human-readable WARNING strings (empty = all good). Pure (no I/O / no logging)
    so it is trivially testable; the caller logs each line. Does NOT reject —
    hard range rejection (0,1] already happens at config load. Flags:
      * fractions that look extreme (<extreme_lo or >extreme_hi),
      * by_symbol keys that match no known instrument (likely typo → silently ignored),
      * by_strategy keys that match no loaded strategy (likely typo → silently ignored)."""
    warnings: list[str] = []
    if overrides is None or not getattr(overrides, "enabled", False):
        return warnings
    for label, mapping in (
        ("by_price_band", getattr(overrides, "by_price_band", None) or {}),
        ("by_strategy", getattr(overrides, "by_strategy", None) or {}),
        ("by_symbol", getattr(overrides, "by_symbol", None) or {}),
    ):
        for key, frac in mapping.items():
            if frac < extreme_lo or frac > extreme_hi:
                warnings.append(
                    f"slippage override {label}[{key!r}]={frac} looks extreme "
                    f"(outside {extreme_lo}-{extreme_hi}); double-check it is intended"
                )
    if known_symbols is not None:
        for sym in (getattr(overrides, "by_symbol", None) or {}):
            if sym not in known_symbols:
                warnings.append(
                    f"slippage override by_symbol[{sym!r}] is not a known instrument "
                    f"— the override will be SILENTLY IGNORED (typo?)"
                )
    if known_strategies is not None:
        for strat in (getattr(overrides, "by_strategy", None) or {}):
            if strat not in known_strategies:
                warnings.append(
                    f"slippage override by_strategy[{strat!r}] is not a loaded strategy "
                    f"— the override will be SILENTLY IGNORED (typo?)"
                )
    return warnings


def _compute_slippage_tolerance(
    cfg: Any,
    signal_price: float,
    sl_price: Optional[float],
    tier_tuples: list,
    max_pct: float,
    *,
    fraction_override: Optional[float] = None,
) -> tuple[float, Optional[float]]:
    """Entry-slippage tolerance (Rs) for the active mode, plus the hard ceiling.
    Returns (tolerance_rs, sl_distance_rs_or_None). Pure (no I/O) for testing.

      sl_fraction: min(SL_distance × fraction, absolute_cap_rs)
                   — SL_distance = |signal − sl|; `fraction` is `fraction_override`
                   when supplied (Phase 3a per symbol/strategy/band resolution),
                   else the global `max_slippage_fraction`. Falls back to
                   absolute_cap_rs if the SL price is unavailable.
      flat_tiers:  per-price-band Rs (tier_tuples).
      pct:         signal_price × max_pct%.
    `hard_max_slippage_rs` is applied as an absolute ceiling in every mode."""
    mode = getattr(cfg, "mode", "sl_fraction")
    sl_dist: Optional[float] = None
    if mode == "sl_fraction":
        frac = (fraction_override if fraction_override is not None
                else cfg.max_slippage_fraction)
        if sl_price is not None and sl_price > 0:
            sl_dist = abs(signal_price - sl_price)
            tol = min(sl_dist * frac, cfg.absolute_cap_rs)
        else:
            tol = cfg.absolute_cap_rs        # SL unavailable -> backstop only
    elif mode == "flat_tiers":
        tol = tier_slippage_tolerance_rs(signal_price, tier_tuples, cfg.default_max_slippage_rs)
    elif mode == "pct":
        tol = signal_price * (max_pct / 100.0)
    else:
        tol = cfg.absolute_cap_rs            # unknown mode -> safe small cap
    return min(tol, cfg.hard_max_slippage_rs), sl_dist


def _slippage_decision(
    cfg: Any,
    signal_price: float,
    sl_price: Optional[float],
    slip_rs: float,
    slip_pct: float,
    tier_tuples: list,
    max_pct: float,
    *,
    fraction_override: Optional[float] = None,
) -> tuple[Optional[str], float, Optional[float]]:
    """Decide whether to abort the entry on slippage. Returns
    (abort_reason_or_None, tolerance_rs, sl_distance_or_None). Pure (no I/O).
    Aborts on the mode tolerance; the flat % is kept as belt-and-suspenders for
    the non-pct modes when `also_apply_pct_check`. `fraction_override` (Phase 3a)
    replaces the global sl_fraction when supplied."""
    tol, sl_dist = _compute_slippage_tolerance(
        cfg, signal_price, sl_price, tier_tuples, max_pct,
        fraction_override=fraction_override)
    if slip_rs > tol:
        extra = f", SL_dist=₹{sl_dist:.2f}, {slip_rs / sl_dist * 100:.0f}% of SL" if sl_dist else ""
        return (f"slippage ₹{slip_rs:.2f} > tolerance ₹{tol:.2f} "
                f"(mode={getattr(cfg, 'mode', '?')}{extra})"), tol, sl_dist
    if (getattr(cfg, "also_apply_pct_check", True) and getattr(cfg, "mode", "") != "pct"
            and slip_pct > max_pct):
        return f"slippage {slip_pct:.2f}% > flat limit {max_pct:.1f}%", tol, sl_dist
    return None, tol, sl_dist


# ─────────────────────────────────────────────────────────────────────────────
# Leg taxonomy (BL-7a)
#
# The `leg` field on _FillEntry routes OrderFilled events to the correct
# handler inside _on_order_filled:
#
#     ENTRY       → _handle_entry_fill  (commit capital, open position)
#     SL/TGT/EOD  → _handle_exit_fill   (release capital, close position)
#
# (The split handler is wired in BL-7d; A.3.c guards non-ENTRY legs.)
# EOD is reserved for eod_squareoff-originated tracks; it is a valid value
# today even though eod_squareoff does not currently populate _fill_map.
# ─────────────────────────────────────────────────────────────────────────────

_LEG_ENTRY: Final[str] = "ENTRY"
_LEG_SL:    Final[str] = "SL"
_LEG_TGT:   Final[str] = "TGT"
_LEG_EOD:   Final[str] = "EOD"
_VALID_LEGS: frozenset[str] = frozenset({_LEG_ENTRY, _LEG_SL, _LEG_TGT, _LEG_EOD})

# BL-7d: exit-leg → OrderManager.close_trade exit_reason taxonomy.
# Must match orders.order_manager._VALID_EXIT_REASONS.
_LEG_TO_EXIT_REASON: Final[Dict[str, str]] = {
    _LEG_SL:  "SL_HIT",
    _LEG_TGT: "TGT_HIT",
    _LEG_EOD: "EOD_SQUAREOFF",
}

# BL-10a: order_protocol → broker product code, used to derive product for
# CostCalculator and intent for FundManager.release_used on exit. The trades
# table does not persist product/intent, so we recover them from the protocol
# cached on _FillEntry. Keep in lockstep with order_reconciler._PRODUCT_TO_INTENT.
_PROTOCOL_TO_PRODUCT: Final[Dict[str, str]] = {
    "CO_PLUS_TGT":  "CO",
    "LIMIT_TRIPLE": "MIS",
}
# FIX-166 F17: canonical copy now in core.constants
from core.constants import PRODUCT_TO_INTENT as _PRODUCT_TO_INTENT

# Terminal order statuses — an order in one of these is no longer live (Task:
# TGT-retry idempotency guard checks this to detect an already-live TGT). Mirrors
# order_reconciler._TERMINAL_ORDER_STATUSES.
_TERMINAL_ORDER_STATUSES: frozenset = frozenset(
    {"COMPLETE", "CANCELLED", "FAILED", "EXPIRED", "REJECTED"}
)


# ─────────────────────────────────────────────────────────────────────────────
# FIX-061: Exit retry params for LTP validation errors
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _ExitRetryParams:
    """
    FIX-061: Tracks exit orders awaiting first valid LTP for retry.

    When SL/SL-M placement fails with trigger/LTP validation error (error code
    16418 or message containing "Trigger price" or "LTP cannot be validated"),
    we add to _pending_exit_retry and subscribe to LiveFeed. On first valid
    tick (LTP > 0), we retry placement up to MAX_RETRIES times.
    """
    trade_id: str
    fill_entry: Any  # _FillEntry
    qty_filled: int
    avg_fill_price: float
    reason: str
    retry_count: int = 0
    MAX_RETRIES: int = 3


# ─────────────────────────────────────────────────────────────────────────────
# Internal fill-map entry
# ─────────────────────────────────────────────────────────────────────────────

class _FillEntry:
    """
    One row in OrderPlacer._fill_map, keyed by internal_order_id.

    `leg` drives routing in _on_order_filled (see Leg taxonomy above).
    `order_protocol` / `direction` are cached here so entry-fill handling
    can branch (e.g. register with SmartTgtManager only for CO_PLUS_TGT)
    without a DB round-trip on every fill.

    Naked-short fix (2.1): for LIMIT_TRIPLE, the ENTRY leg also caches
    `side`, `sl_price`, `tgt_price`, `intent` so the fill handler can
    place SL + TGT via FullEntryEngine.place_deferred_exits without a
    DB round-trip. On exit legs these fields are populated for symmetry
    but not consulted.

    Invariants:
        leg ∈ _VALID_LEGS; constructor raises ValueError otherwise.
    """

    __slots__ = (
        "trade_id", "reservation_id", "symbol", "qty", "leg",
        "order_protocol", "direction",
        "side", "sl_price", "tgt_price", "intent",
        "tgt_risk_reward",
    )

    def __init__(
        self,
        trade_id: str,
        reservation_id: str,
        symbol: str,
        qty: int,
        leg: str,
        order_protocol: str,
        direction: str,
        side: str = "",
        sl_price: float = 0.0,
        tgt_price: float = 0.0,
        intent: str = "",
        tgt_risk_reward: float = 0.0,   # Slice 1: strategy R:R; 0.0/falsy -> fill uses _rr_ratio fallback
    ) -> None:
        if leg not in _VALID_LEGS:
            raise ValueError(
                f"_FillEntry.leg must be one of {sorted(_VALID_LEGS)}, "
                f"got {leg!r}"
            )
        self.trade_id = trade_id
        self.reservation_id = reservation_id
        self.symbol = symbol
        self.qty = qty
        self.leg = leg
        self.order_protocol = order_protocol
        self.direction = direction
        self.side = side
        self.sl_price = sl_price
        self.tgt_price = tgt_price
        self.intent = intent
        self.tgt_risk_reward = tgt_risk_reward


# ─────────────────────────────────────────────────────────────────────────────
# OrderPlacer
# ─────────────────────────────────────────────────────────────────────────────

class OrderPlacer:
    """
    Orchestrates trade creation, order placement, and fill handling (OP1–OP11).

    Usage::
        placer = OrderPlacer(
            entry_engine=full_entry_engine,
            order_manager=om,
            fund_manager=fm,
            bus=event_bus,
            logger=log,
            rr_ratio=2.0,
            default_order_protocol="LIMIT_TRIPLE",
        )
        # signal_processor calls:
        placer.place(symbol=…, side=…, qty=…, entry_price=…, sl_price=…,
                     intent=…, signal_id=…, reservation_id=…)
    """

    def __init__(
        self,
        entry_engine: FullEntryEngine,
        order_manager: OrderManager,
        fund_manager: FundManager,
        bus: EventBus,
        logger: logging.Logger,
        order_monitor: OrderMonitor,
        cost_calculator: CostCalculator,
        rr_ratio: float = 2.0,
        default_order_protocol: str = "LIMIT_TRIPLE",
        kill_switch: Optional[KillSwitch] = None,
        product_resolver: Optional[ProductResolver] = None,
        smart_tgt_manager: Optional[SmartTgtManager] = None,
        smart_tgt_config: Optional[SmartTgtConfig] = None,
        breakeven_manager: Optional[Any] = None,  # FIX-132 Item 8: BreakevenManager
        rate_limit_backoff: Optional[RateLimitBackoffConfig] = None,  # BL-19
        entry_gate_slippage_buffer: float = 2.0,  # FIX-025: gate release slippage protection
        notifier: Optional[object] = None,   # TelegramNotifier; optional
        mode: str = "LIVE",                   # session mode label for alert title
        live_feed: Optional[Any] = None,      # FIX-061: LiveFeedManager for LTP retry
        broker_adapter: Optional[Any] = None,  # FIX-072: optional adapter for margin cache invalidation
        market_windows: Optional[Any] = None,  # FIX-073: market windows for EOD entry cutoff check
        price_drift_threshold: float = 0.005,  # FIX-075: 0.5% default drift threshold for margin top-up
        max_entry_slippage_pct: float = 1.0,  # FIX-128: abort if LTP deviates > this % from trigger
        slippage_control: Optional[Any] = None,  # SlippageControlConfig (sl_fraction/flat_tiers/pct)
        slippage_bands: Optional[list] = None,  # Phase 3a: price-band labels for override resolution
        liquidity_check_enabled: bool = False,  # FIX-134 Item 38
        liquidity_max_spread_pct: float = 0.5,
        liquidity_min_depth_qty: int = 500,
        min_effective_rr: float = 0.0,  # FIX-136 Item 54: abort if R:R below this after slippage
        emergency_exit_buffer_pct: float = EMERGENCY_EXIT_BUFFER_PCT,  # FIX-181
    ) -> None:
        # BL-7b: CO_PLUS_TGT needs trigger/step fractions at fill time.
        if smart_tgt_manager is not None and smart_tgt_config is None:
            raise ValueError(
                "OrderPlacer: smart_tgt_manager was provided but smart_tgt_config "
                "was not. CO_PLUS_TGT protocol needs trigger_pct/step_pct at fill "
                "time; pass smart_tgt_config=<SmartTgtConfig(...)> or omit both."
            )
        self._engine = entry_engine
        self._om = order_manager
        self._fm = fund_manager
        self._bus = bus
        self._log = logger
        self._order_monitor = order_monitor  # BL-7b: required for A.3.c track() wiring
        self._cost_calculator = cost_calculator  # BL-10a: exit-path cost computation
        self._rr_ratio = rr_ratio
        self._default_protocol = default_order_protocol
        self._kill_switch = kill_switch  # OP-LM1: may be None (disabled)
        self._product_resolver = product_resolver  # HIGH #7: use resolver for product codes
        self._smart_tgt_manager = smart_tgt_manager  # BL-7b: None = SmartTgt disabled
        self._smart_tgt_config = smart_tgt_config    # BL-7b: trigger_pct/step_pct source
        self._breakeven_manager = breakeven_manager  # FIX-132 Item 8: None = disabled
        # FIX-181: marketable-LIMIT buffer for emergency exits (LTP ± buffer).
        self._emergency_exit_buffer_pct = emergency_exit_buffer_pct
        # BL-19: 429 retry policy. Defaults apply if caller omits the config.
        self._rl_backoff: RateLimitBackoffConfig = (
            rate_limit_backoff or RateLimitBackoffConfig()
        )
        # FIX-025: gate release slippage protection buffer
        self._entry_gate_slippage_buffer = entry_gate_slippage_buffer
        # IC8: injected by main.py after Module 38; None = no tick rounding
        self._instrument_cache = None  # set via set_instrument_cache()
        # Telegram alerts (optional): wiring for ORDER PLACED / TGT HIT / SL HIT
        self._notifier = notifier
        self._mode = mode

        # OP5: internal_order_id → _FillEntry
        self._fill_map: Dict[str, _FillEntry] = {}
        self._fill_map_lock = threading.Lock()

        # FIX-061: Exit retry tracking for LTP validation errors
        self._pending_exit_retry: Dict[str, _ExitRetryParams] = {}
        self._pending_exit_retry_lock = threading.Lock()
        self._live_feed = live_feed
        self._ltp_callback_registered = False

        # FIX-068: Timeout recovery tracking for UNKNOWN_IN_FLIGHT orders
        # Maps trade_id -> {signal_id, reservation_id, internal_order_ids: list}
        # order_reconciler polls these and resolves them after 3 cycles (45s)
        self._timeout_recovery_queue: Dict[str, Dict[str, Any]] = {}
        self._timeout_recovery_lock = threading.Lock()

        # FIX-072: Optional broker adapter for margin cache invalidation on 16388
        self._adapter = broker_adapter

        # FIX-073: Optional market windows for EOD entry cutoff check
        self._market_windows = market_windows

        # FIX-075: Price drift threshold for margin top-up
        self._price_drift_threshold = price_drift_threshold
        # FIX-128: max allowed % deviation between trigger price and current LTP
        self._max_entry_slippage_pct = max_entry_slippage_pct
        # Entry-slippage control (sl_fraction/flat_tiers/pct). Precompute the
        # (max_price, max_slippage_rs) tuples for the flat_tiers lookup.
        self._slippage_control = slippage_control
        self._slippage_tier_tuples = [
            (t.max_price, t.max_slippage_rs)
            for t in getattr(slippage_control, "tiers", None) or []
        ]
        # Phase 3a: price-band labels for the override hierarchy (Symbol > Strategy
        # > Band > Global). Empty list -> band overrides never match (fine).
        self._slippage_bands = list(slippage_bands or [])
        # FIX-134 Item 38: liquidity check before entry
        self._liquidity_check_enabled = liquidity_check_enabled
        self._liquidity_max_spread_pct = liquidity_max_spread_pct
        self._liquidity_min_depth_qty = liquidity_min_depth_qty
        self._min_effective_rr = min_effective_rr

        # OP6: subscribe to OrderFilled (synchronous; no deadlock risk — the
        # paper-synth lock is released before bus.publish() is called).
        self._bus.subscribe(OrderFilled, self._on_order_filled)
        # FIX-028: subscribe to OrderPartiallyTerminated for partial-fill-then-cancel
        # path. This event carries all fill details (qty, price, slippage) for
        # placing exits at the actual filled quantity.
        self._bus.subscribe(OrderPartiallyTerminated, self._on_order_partially_terminated)
        # Audit #7 + FIX-017: subscribe to OrderStatusChanged for zero-fill-then-cancel
        # path (qty_filled == 0). Partial fills now handled by OrderPartiallyTerminated.
        self._bus.subscribe(OrderStatusChanged, self._on_order_status_changed)

    def set_instrument_cache(self, cache) -> None:
        """Wire InstrumentCache for IC8 tick-size rounding (called from main.py)."""
        self._instrument_cache = cache

    def get_timeout_recovery_trades(self) -> List[str]:
        """
        FIX-068: Return list of trade_ids currently in UNKNOWN_IN_FLIGHT state.
        Called by order_reconciler during CHECK_UNKNOWN_IN_FLIGHT check.
        """
        with self._timeout_recovery_lock:
            return list(self._timeout_recovery_queue.keys())

    def remove_from_timeout_recovery(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """
        FIX-068: Remove trade from timeout recovery queue after reconciler resolves it.
        Returns the queue entry if found, None otherwise.
        """
        with self._timeout_recovery_lock:
            return self._timeout_recovery_queue.pop(trade_id, None)

    def rehydrate_fill_map(self, state_store) -> int:
        """
        B.1 (2026-04-25): Repopulate _fill_map with non-terminal SL/TGT/EOD exit
        legs of open trades after restart.

        Without this, when OrderMonitor.rehydrate_from_store re-tracks an
        exit leg and a fill arrives, _on_order_filled looks up the
        broker-order-id-keyed _fill_map and finds nothing -- so
        _handle_exit_fill is never called and the trade never closes in DB
        (release_used + PositionClosed never fire either).

        Keying mirrors OrderMonitor.rehydrate_from_store: we use
        broker_order_id as the synthetic internal_order_id since the
        original ord_-prefixed id from new_order_id() is not persisted, and
        OrderFilled events for rehydrated legs carry broker_order_id as
        internal_order_id.

        ENTRY legs are intentionally skipped: their reservation_id is gone
        post-restart and capital state is reconstructed by
        fund_manager.rehydrate_from_open_trades. Re-entering ENTRY into
        _fill_map would route post-restart fills to _handle_entry_fill,
        which would call commit_to_used with an unknown reservation_id.

        reservation_id on the rehydrated _FillEntry is left empty:
        _handle_exit_fill does not consult it (release_used keys on
        symbol+intent, not the reservation ledger).

        Returns the number of entries inserted.
        """
        try:
            rows = state_store.get_open_orders_for_rehydration()
        except Exception as exc:  # noqa: BLE001
            log_exception(self._log, exc)
            self._log.error(
                "order_placer.rehydrate_fill_map_fetch_failed",
                extra={"error": str(exc)},
            )
            return 0

        rehydrated = 0
        with self._fill_map_lock:
            for row in rows:
                broker_order_id = row["order_id"]
                if not broker_order_id:
                    continue
                leg = (row["leg"] or "").upper()
                if leg not in (_LEG_SL, _LEG_TGT, _LEG_EOD):
                    continue
                if broker_order_id in self._fill_map:
                    continue

                self._fill_map[broker_order_id] = _FillEntry(
                    trade_id=row["trade_id"] or "",
                    reservation_id="",  # see docstring
                    symbol=row["symbol"] or "",
                    qty=int(row["qty_requested"] or 0),
                    leg=leg,
                    order_protocol=(row["order_protocol"] or "").upper(),
                    direction=row["direction"] or "",
                )
                rehydrated += 1

        self._log.info(
            "order_placer.rehydrate_fill_map",
            extra={"rehydrated": rehydrated},
        )
        return rehydrated

    # ── public interface ──────────────────────────────────────────────────────

    def place(
        self,
        *,
        symbol: str,
        side: str,
        qty: int,
        entry_price: float,
        sl_price: float,
        intent: str,
        signal_id: str,
        reservation_id: str,
        strategy: str = "",
        tgt_price: Optional[float] = None,  # SPW6: provided by signal_processor; overrides OP3
        release_ltp: Optional[float] = None,  # FIX-025: gate release LTP for slippage protection
        signal_trigger_price: Optional[float] = None,  # FIX-128: original Chartink trigger for slippage guard
        sizing_breakdown: Optional[dict] = None,  # Diary #4: PositionSizer.breakdown for the trades sizing audit
        tgt_risk_reward: Optional[float] = None,  # Slice 1: originating strategy's R:R, frozen for the fill-time TGT recalc
    ) -> None:
        """
        Create trade, place entry orders, register fill tracking. (OP1–OP4)

        Raises BrokerError on hard broker failure (after cleanup).
        signal_processor catches this and marks signal PLACEMENT_FAILED.

        FIX-025: If release_ltp is provided (from EntryGate PRICE_HIT), applies
        slippage protection to entry_price before placing orders:
          LONG:  adjusted = min(entry_price + buffer, release_ltp)
          SHORT: adjusted = max(entry_price - buffer, release_ltp)
        """
        # FIX-025: Apply slippage protection if release_ltp provided
        requested_entry = entry_price
        if release_ltp is not None:
            if side == "BUY":
                # LONG: limit can't be higher than release_ltp
                adjusted_limit = min(entry_price + self._entry_gate_slippage_buffer, release_ltp)
            else:  # side == "SELL"
                # SHORT: limit can't be lower than release_ltp
                adjusted_limit = max(entry_price - self._entry_gate_slippage_buffer, release_ltp)
            entry_price = adjusted_limit
            self._log.info(
                "order_placer.slippage_protection",
                extra={
                    "signal_id": signal_id,
                    "symbol": symbol,
                    "side": side,
                    "requested_entry": requested_entry,
                    "release_ltp": release_ltp,
                    "adjusted_limit": adjusted_limit,
                    "buffer": self._entry_gate_slippage_buffer,
                },
            )

        # 23-Jun: entry/SL/TGT tick-snapping moved to the SINGLE adapter chokepoint
        # (zerodha_adapter._snap_order_to_tick, fail-safe). The old IC8
        # order_placer._round_to_tick (float-floor, fail-open) is removed — the
        # adapter is now the sole snap point (no per-call-site rounding).

        # OP3: use caller-supplied tgt_price if provided; else compute internally
        if tgt_price is None:
            tgt_price = self._compute_tgt(side, entry_price, sl_price)

        # FIX-136 Item 54: R:R gate — abort if effective R:R too low after slippage
        if self._min_effective_rr > 0 and entry_price != sl_price:
            sl_dist = abs(entry_price - sl_price)
            if side == "BUY":
                reward_dist = tgt_price - entry_price
            else:
                reward_dist = entry_price - tgt_price
            effective_rr = reward_dist / sl_dist if sl_dist > 0 else 0.0
            if effective_rr < self._min_effective_rr:
                self._log.warning(
                    "order_placer.rr_gate_failed",
                    extra={
                        "signal_id": signal_id, "symbol": symbol,
                        "effective_rr": round(effective_rr, 3),
                        "min_rr": self._min_effective_rr,
                        "entry": entry_price, "sl": sl_price, "tgt": tgt_price,
                    },
                )
                raise OrderRejectedError(
                    f"RR_GATE_FAILED: effective R:R={effective_rr:.2f} < min={self._min_effective_rr}",
                    trade_id="", signal_id=signal_id, symbol=symbol,
                )

        # OP9: choose protocol
        order_protocol = self._default_protocol

        # OP4: compute trade fields
        direction = "LONG" if side == "BUY" else "SHORT"
        risk_amount = abs(entry_price - sl_price) * qty
        # H-3: use FundManager's leverage-aware compute instead of a hardcoded
        # 0.20 (coincidentally correct for INTRADAY 5x only; wrong for
        # DELIVERY 1x / COVER_ORDER 6x / etc.). No cross-module private
        # attribute access -- FundManager.required_margin() encapsulates its
        # leverage map internally.
        margin_reserved = self._fm.required_margin(
            qty=qty, price=entry_price, intent=intent,
        )

        # Phase 3a: resolve the effective entry-slippage fraction via the manual
        # override hierarchy (Symbol > Strategy > Price Band > Global). Resolved
        # up-front so it can be both (a) enforced by the slippage guard below and
        # (b) recorded on the trade row (tolerance_source) for transparency and
        # later Phase-3b effectiveness analysis. Only the sl_fraction mode uses a
        # fraction; other modes record no source (their tolerance is tier/pct-based).
        _tol_fraction: Optional[float] = None
        _tol_source: Optional[str] = None
        _scfg0 = self._slippage_control
        if (_scfg0 is not None and getattr(_scfg0, "enabled", False)
                and getattr(_scfg0, "mode", "") == "sl_fraction"):
            _band_price = (signal_trigger_price
                           if (signal_trigger_price and signal_trigger_price > 0)
                           else entry_price)
            _band0 = get_price_band(_band_price, self._slippage_bands)
            _tol_fraction, _tol_source = resolve_slippage_fraction(
                _scfg0, symbol, strategy, _band0)

        # OP4: create trade row FIRST (status=PENDING_FILL)
        # EF-5: thread reservation_id so the trades row records which fm_ledger
        # reservation funded it; simplifies rehydrate and audit.
        trade_id = self._om.create_trade(
            signal_id=signal_id,
            symbol=symbol,
            direction=direction,
            strategy=strategy,
            sector=None,        # OP10: symbol_validator not yet built
            qty=qty,
            entry_target_price=entry_price,
            sl_initial=sl_price,
            tgt_initial=tgt_price,
            order_protocol=order_protocol,
            margin_reserved=margin_reserved,
            risk_amount=risk_amount,
            reservation_id=reservation_id,
            mode=self._mode,
            tolerance_fraction_used=_tol_fraction,   # Phase 3a
            tolerance_source=_tol_source,            # Phase 3a
            sizing_breakdown=sizing_breakdown,       # Diary #4: sizing audit
            tgt_risk_reward_applied=tgt_risk_reward, # Slice 1: strategy R:R frozen at placement
        )

        # Link signal → trade
        # H-21 / M-5: link_signal_trade runs BEFORE _engine.execute(), so no
        # broker orders exist at link-failure time. Pre-E.3 this path
        # swallow-and-continued ("reconciler can fix the link later") -- but a
        # broker position with no origin-signal linkage breaks audit traceability
        # and makes reconciler CHECK 2 classify it as ORPHAN_ADOPTION. Apply the
        # BL-8 hard-fail pattern with broker_order_ids=() (nothing to cancel):
        # release the reservation, mark trade FAILED, raise. signal_processor's
        # outer except will mark the signal PLACEMENT_FAILED -- no orphan trade
        # row, no orphan broker position.
        try:
            self._om.link_signal_trade(signal_id, trade_id)
        except Exception as exc:
            self._handle_placement_failure(
                trade_id, reservation_id, signal_id, exc,
                broker_order_ids=(),
            )
            raise OrderRejectedError(
                f"link_signal_trade failed: {exc}",
                trade_id=trade_id, signal_id=signal_id, symbol=symbol,
            ) from exc

        self._log.info(
            "order_placer.place_start",
            extra={
                "signal_id": signal_id, "trade_id": trade_id,
                "symbol": symbol, "side": side, "qty": qty,
                "entry_price": entry_price, "sl_price": sl_price,
                "tgt_price": tgt_price, "protocol": order_protocol,
            },
        )

        # ── OP-LM1: last-mile kill_switch check ───────────────────────────
        if self._kill_switch is not None and self._kill_switch.is_active("entry"):
            ks_exc = OrderRejectedError(
                "kill_switch_active_last_mile",
                trade_id=trade_id, signal_id=signal_id, symbol=symbol,
            )
            self._handle_placement_failure(
                trade_id, reservation_id, signal_id, ks_exc, symbol=symbol
            )
            raise ks_exc

        # ── Place entry orders ─────────────────────────────────────────────
        # FIX-071 Part A: Transition trade to PENDING before broker call.
        # This ensures OrderMonitor can recognize the order even if it polls
        # before track() is called (instant-fill race condition).
        try:
            self._om.update_trade_status(trade_id, "PENDING")
        except Exception as status_exc:
            self._log.warning(
                "order_placer.pending_status_update_failed",
                extra={"trade_id": trade_id, "error": str(status_exc)},
            )
            # Non-fatal: continue with placement even if status update fails

        # FIX-073: EOD entry cutoff check before broker placement.
        # Prevents signals delayed in rate limiter from opening positions
        # after EOD squareoff time (broker RMS penalty risk).
        if self._market_windows is not None:
            if self._market_windows.is_past_eod_entry_cutoff(now_ist()):
                eod_exc = OrderRejectedError(
                    f"order rejected: past EOD entry cutoff "
                    f"{self._market_windows.eod_entry_cutoff_t.strftime('%H:%M')}",
                    trade_id=trade_id, signal_id=signal_id, symbol=symbol,
                )
                self._log.warning(
                    "order_placer.rejected_past_eod_cutoff",
                    extra={
                        "trade_id": trade_id,
                        "signal_id": signal_id,
                        "symbol": symbol,
                        "cutoff_time": self._market_windows.eod_entry_cutoff_t.strftime('%H:%M'),
                    },
                )
                self._handle_placement_failure(
                    trade_id, reservation_id, signal_id, eod_exc,
                    final_status="REJECTED",
                    symbol=symbol,
                )
                raise eod_exc

        # FIX-128 (Fix A): Slippage guard — abort if market has moved too far from signal trigger.
        # For gate-path signals, release_ltp is already captured at gate release; reuse it
        # to avoid a redundant quote fetch. For direct-path signals, fetch current LTP.
        # Best-effort: if LTP unavailable, skip check and proceed.
        if signal_trigger_price is not None and signal_trigger_price > 0:
            _slip_ltp = release_ltp  # gate path: already have LTP
            if _slip_ltp is None:
                # Bug 6 (FIX-180): use adapter.get_quote_raw via _fetch_ltp
                # (live_feed has no .quote()). Best-effort; None -> skip guard.
                _slip_ltp = self._fetch_ltp(symbol)
            if _slip_ltp is not None:
                _slip_rs = abs(_slip_ltp - signal_trigger_price)
                _slip_pct = _slip_rs / signal_trigger_price * 100
                _scfg = self._slippage_control
                if _scfg is not None and getattr(_scfg, "enabled", False):
                    # Phase 3a: enforce the SAME fraction resolved (and recorded
                    # on the trade) above, so the override hierarchy drives the
                    # abort decision. None -> _compute falls back to the global.
                    _abort_reason, _slip_tol, _slip_sl_dist = _slippage_decision(
                        _scfg, signal_trigger_price, sl_price, _slip_rs, _slip_pct,
                        self._slippage_tier_tuples, self._max_entry_slippage_pct,
                        fraction_override=_tol_fraction,
                    )
                else:
                    # slippage_control disabled/absent -> legacy flat % guard (FIX-128)
                    _slip_tol = signal_trigger_price * (self._max_entry_slippage_pct / 100.0)
                    _slip_sl_dist = None
                    _abort_reason = (
                        f"slippage {_slip_pct:.2f}% > limit {self._max_entry_slippage_pct:.1f}%"
                        if _slip_pct > self._max_entry_slippage_pct else None
                    )
                # Calibration: always record the observed slippage + how much of the
                # SL risk-budget it consumed (even within tolerance) so
                # max_slippage_fraction can be tuned from real numbers. Phase 3a:
                # also record WHICH override rule supplied the fraction (tolerance_source).
                self._log.info(
                    "order_placer.entry_slippage_observed",
                    extra={
                        "symbol": symbol, "side": side,
                        "trigger_price": signal_trigger_price, "current_ltp": _slip_ltp,
                        "slippage_rs": round(_slip_rs, 2), "slippage_pct": round(_slip_pct, 3),
                        "sl_distance_rs": (round(_slip_sl_dist, 2) if _slip_sl_dist else None),
                        "tolerance_rs": round(_slip_tol, 2),
                        "fraction_of_sl_used": (round(_slip_rs / _slip_sl_dist, 3)
                                                if _slip_sl_dist else None),
                        "mode": getattr(_scfg, "mode", None),
                        "tolerance_fraction": _tol_fraction,    # Phase 3a
                        "tolerance_source": _tol_source,        # Phase 3a
                        "aborted": _abort_reason is not None,
                    },
                )
                if _abort_reason is not None:
                    slip_exc = OrderRejectedError(
                        f"slippage_exceeded: trigger={signal_trigger_price:.2f} "
                        f"ltp={_slip_ltp:.2f} | {_abort_reason}",
                        trade_id=trade_id, signal_id=signal_id, symbol=symbol,
                    )
                    self._log.warning(
                        "order_placer.slippage_guard_exceeded",
                        extra={
                            "trade_id": trade_id, "signal_id": signal_id,
                            "symbol": symbol, "side": side,
                            "trigger_price": signal_trigger_price,
                            "current_ltp": _slip_ltp,
                            "slippage_rs": round(_slip_rs, 2),
                            "slippage_pct": round(_slip_pct, 3),
                            "tolerance_fraction": _tol_fraction,    # Phase 3a
                            "tolerance_source": _tol_source,        # Phase 3a
                            "reason": _abort_reason,
                        },
                    )
                    if self._notifier is not None:
                        try:
                            _src_note = (f" | rule: {_tol_source}"
                                         if _tol_source and _tol_source != "global" else "")
                            self._notifier.send(
                                severity="WARNING",
                                title=f"[{self._mode}] SLIPPAGE GUARD — {symbol}",
                                body=(
                                    f"Order aborted: {side} | {_abort_reason}\n"
                                    f"Trigger: ₹{signal_trigger_price:.2f} | "
                                    f"LTP: ₹{_slip_ltp:.2f} | Slip: ₹{_slip_rs:.2f}{_src_note}"
                                ),
                                source_module="order_placer",
                            )
                        except Exception as _ne:
                            self._log.error("order_placer: slippage notifier.send failed: %s", _ne)
                    self._handle_placement_failure(
                        trade_id, reservation_id, signal_id, slip_exc,
                        final_status="REJECTED",
                        symbol=symbol,
                        suppress_alert=True,  # slippage guard already sent its own alert
                    )
                    raise slip_exc

        # FIX-075: Price drift check before placement
        # If price has drifted significantly since reservation, top up margin.
        # Best-effort: if quote fetch fails, log warning and proceed (don't block order).
        original_entry_price = entry_price
        if self._adapter is not None:
            try:
                # Bug 6 (FIX-180): fetch current LTP via adapter.get_quote_raw
                # (live_feed has no .quote()); None -> skip drift top-up.
                current_ltp = self._fetch_ltp(symbol)
                if current_ltp is not None and original_entry_price:
                    drift_pct = abs(current_ltp - original_entry_price) / original_entry_price

                    # Load threshold from config
                    drift_threshold = self._price_drift_threshold

                    if drift_pct > drift_threshold:
                        self._log.info(
                            "order_placer.price_drift_detected",
                            extra={
                                "trade_id": trade_id,
                                "signal_id": signal_id,
                                "symbol": symbol,
                                "original_price": original_entry_price,
                                "current_ltp": current_ltp,
                                "drift_pct": drift_pct,
                                "threshold": drift_threshold,
                            },
                        )

                        # Recalculate required margin with current LTP (H-3: use instance method)
                        original_margin = self._fm.required_margin(qty, original_entry_price, intent)
                        new_margin = self._fm.required_margin(qty, current_ltp, intent)
                        additional_margin = new_margin - original_margin

                        if additional_margin > 0:
                            # Need more margin - attempt top-up
                            top_up_result = self._fm.top_up_reservation(
                                reservation_id=reservation_id,
                                additional_margin=additional_margin,
                                reason=f"price drift {drift_pct*100:.2f}% → ₹{current_ltp:.2f}",
                            )

                            if not top_up_result.success:
                                # Insufficient capital for top-up - reject order
                                drift_exc = OrderRejectedError(
                                    f"price drift {drift_pct*100:.2f}% requires ₹{additional_margin:.2f} "
                                    f"additional margin, but insufficient capital available",
                                    trade_id=trade_id, signal_id=signal_id, symbol=symbol,
                                )
                                self._log.warning(
                                    "order_placer.rejected_price_drift",
                                    extra={
                                        "trade_id": trade_id,
                                        "signal_id": signal_id,
                                        "symbol": symbol,
                                        "drift_pct": drift_pct,
                                        "additional_margin": additional_margin,
                                        "rejection_reason": top_up_result.reason_if_failed,
                                    },
                                )
                                self._handle_placement_failure(
                                    trade_id, reservation_id, signal_id, drift_exc,
                                    final_status="REJECTED_PRICE_DRIFT",
                                )
                                raise drift_exc

                            # Top-up succeeded - use current_ltp for placement
                            entry_price = current_ltp
                            self._log.info(
                                "order_placer.price_drift_top_up_success",
                                extra={
                                    "trade_id": trade_id,
                                    "signal_id": signal_id,
                                    "symbol": symbol,
                                    "additional_margin": additional_margin,
                                    "adjusted_entry_price": current_ltp,
                                },
                            )
                        # else: drift increased price but less margin needed (e.g., SHORT position), proceed

            except OrderRejectedError:
                # Re-raise rejection errors (insufficient capital for top-up)
                raise
            except Exception as quote_exc:
                # Quote fetch or drift check failed - log warning and continue
                # FIX-075: don't block order on quote failure (best-effort)
                self._log.warning(
                    "order_placer.price_drift_check_failed",
                    extra={
                        "trade_id": trade_id,
                        "signal_id": signal_id,
                        "symbol": symbol,
                        "error": str(quote_exc),
                        "note": "proceeding with original price",
                    },
                )

        # FIX-134 Item 38: Liquidity check before entry (live mode only).
        # Paper mode skips (simulated fills). Best-effort: failure = proceed.
        if self._mode == "LIVE" and self._adapter is not None:
            liq_ok, liq_reason = self._check_liquidity(symbol, side, trade_id, signal_id)
            if not liq_ok:
                liq_exc = OrderRejectedError(
                    f"insufficient_liquidity: {liq_reason}",
                    trade_id=trade_id, signal_id=signal_id, symbol=symbol,
                )
                self._handle_placement_failure(
                    trade_id, reservation_id, signal_id, liq_exc,
                    final_status="CANCELLED",
                    symbol=symbol,
                )
                raise liq_exc

        # BL-19: retry the engine only on BrokerRateLimit429Error. On each
        # raise, the protocol has already cancelled any legs it placed (OP7 /
        # OP-BL8e), so re-executing is safe w.r.t. duplicate orders. The
        # adapter already called rate_limiter.penalize() before raising the
        # 429, so the next attempt's acquire() blocks until the bucket thaws --
        # that IS the backoff pacing; we never sleep directly here.
        # Non-429 BrokerErrors still get one attempt per ZA11 / OP7.
        # FIX-072: Also retry once on 16388 (insufficient margin) after
        # invalidating the margin cache to force a fresh fetch.
        max_429_retries = self._rl_backoff.max_placer_retries
        result: Optional[EntryResult] = None
        retried_16388 = False  # FIX-072: track if we've already retried margin rejection
        for attempt in range(max_429_retries + 1):
            try:
                result = self._engine.execute(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    entry_price=entry_price,
                    sl_price=sl_price,
                    tgt_price=tgt_price,
                    intent=intent,
                    trade_id=trade_id,
                    order_protocol=order_protocol,
                )
                break  # success
            except OrderRejectedError as rej_exc:
                # FIX-072: Handle 16388 (insufficient margin) with cache invalidation + 1 retry
                kite_code = rej_exc.context.get("kite_status_code")
                if kite_code == 16388 and not retried_16388:
                    retried_16388 = True
                    self._log.warning(
                        "order_placer.16388_margin_rejection_retry",
                        extra={
                            "trade_id": trade_id,
                            "signal_id": signal_id,
                            "symbol": symbol,
                            "intent": intent,
                            "rejection_reason": rej_exc.context.get("rejection_reason", ""),
                        },
                    )
                    # Invalidate margin cache to force fresh fetch on retry
                    if self._adapter is not None:
                        self._adapter.invalidate_margin_cache(symbol, intent)
                    # Continue loop to retry with fresh margin
                    continue
                elif kite_code == 16388 and retried_16388:
                    # Second 16388 after fresh margin fetch: genuine insufficient margin
                    self._log.error(
                        "order_placer.16388_margin_still_insufficient",
                        extra={
                            "trade_id": trade_id,
                            "signal_id": signal_id,
                            "symbol": symbol,
                            "intent": intent,
                            "rejection_reason": rej_exc.context.get("rejection_reason", ""),
                        },
                    )
                    # Mark as REJECTED (not FAILED) and release reservation
                    self._handle_placement_failure(
                        trade_id, reservation_id, signal_id, rej_exc,
                        final_status="REJECTED",
                        symbol=symbol,
                    )
                    raise
                else:
                    # Other rejection (not 16388): single attempt per ZA11/OP7
                    self._handle_placement_failure(
                        trade_id, reservation_id, signal_id, rej_exc, symbol=symbol,
                    )
                    raise
            except BrokerRateLimit429Error as rl_exc:
                if attempt == max_429_retries:
                    # BL-19: exhausted -- same cleanup as any BrokerError
                    self._log.error(
                        "order_placer.429_retries_exhausted",
                        extra={
                            "attempts": attempt + 1,
                            "trade_id": trade_id,
                            "signal_id": signal_id,
                            "operation": rl_exc.context.get("operation"),
                            "last_delay_sec": rl_exc.context.get("delay_sec"),
                        },
                    )
                    self._handle_placement_failure(
                        trade_id, reservation_id, signal_id, rl_exc, symbol=symbol,
                    )
                    raise
                self._log.warning(
                    "order_placer.429_retry",
                    extra={
                        "attempt": attempt + 1,
                        "max_retries": max_429_retries,
                        "delay_sec": rl_exc.context.get("delay_sec"),
                        "operation": rl_exc.context.get("operation"),
                        "trade_id": trade_id,
                        "signal_id": signal_id,
                    },
                )
                # continue loop -- next iteration's acquire() blocks on the
                # frozen bucket, delivering the backoff without caller sleep.
            except BrokerTimeoutError as timeout_exc:
                # FIX-068: Timeout during place_order -- we don't know if the
                # order reached the broker. Do NOT mark FAILED, do NOT release
                # capital. Transition to UNKNOWN_IN_FLIGHT and let reconciler
                # poll the broker to determine actual state.
                self._log.critical(
                    "order_placer.place_timeout_UNKNOWN_IN_FLIGHT",
                    extra={
                        "trade_id": trade_id,
                        "signal_id": signal_id,
                        "symbol": symbol,
                        "reservation_id": reservation_id,
                        "timeout_exc": str(timeout_exc),
                    },
                )
                try:
                    self._om.update_trade_status(trade_id, "UNKNOWN_IN_FLIGHT")
                except Exception as db_exc:
                    log_exception(self._log, db_exc)
                    self._log.critical(
                        "order_placer.timeout_status_update_failed",
                        extra={"trade_id": trade_id, "db_exc": str(db_exc)},
                    )
                # Add to timeout recovery queue for reconciler
                with self._timeout_recovery_lock:
                    self._timeout_recovery_queue[trade_id] = {
                        "signal_id": signal_id,
                        "reservation_id": reservation_id,
                        "symbol": symbol,
                        "added_at": now_ist().isoformat(),
                    }
                # Propagate to signal_processor so it knows placement is in unknown state
                raise
            except BrokerError as exc:
                # OP7 + OP-BL8e + ZA11: non-429 BrokerError = single attempt.
                # The protocol's own cleanup already cancelled any in-flight
                # legs (LimitTriple cancels ENTRY on SL fail; CoPlusTgt has
                # no inter-leg state on raise). Capital tracking is intact,
                # so NO hard_kill -- just FAILED + release.
                self._handle_placement_failure(
                    trade_id, reservation_id, signal_id, exc, symbol=symbol,
                )
                raise

        if not result.success:
            # OP-BL8f: soft failure (CoPlusTgt: CO live, TGT dead). Cancel the
            # CO via _handle_placement_failure(broker_order_ids=...) so the
            # reconciler is a backstop, not the primary recovery path.
            soft_err = BrokerError(
                f"Entry engine returned success=False: {result.rejection_reason}"
            )
            soft_ids = [
                bid for bid in (
                    result.entry_broker_order_id,
                    result.sl_broker_order_id,
                    result.tgt_broker_order_id,
                ) if bid
            ]
            self._handle_placement_failure(
                trade_id, reservation_id, signal_id, soft_err,
                broker_order_ids=soft_ids,
            )
            raise soft_err

        # ── Persist order rows (OP-BL8a/b) ────────────────────────────────
        try:
            self._persist_entry_orders(trade_id, result, symbol, qty, side, intent)
        except Exception as persist_exc:
            # OP-BL8b/e: broker accepted orders but DB write failed. Capital
            # tracking is broken (orders live, no DB rows). Cancel everything
            # we just placed, mark FAILED, release reservation, then fire
            # kill_switch.hard_kill -- this is a capital-tracking breakdown
            # the reconciler cannot detect (no DB rows to compare against).
            placed_ids = [
                bid for bid in (
                    result.entry_broker_order_id,
                    result.sl_broker_order_id,
                    result.tgt_broker_order_id,
                ) if bid
            ]
            self._handle_placement_failure(
                trade_id, reservation_id, signal_id, persist_exc,
                broker_order_ids=placed_ids,
            )
            if self._kill_switch is not None:
                try:
                    self._kill_switch.hard_kill(
                        reason=(
                            f"persist_entry_orders failed after broker success: "
                            f"{type(persist_exc).__name__}: {persist_exc}"
                        ),
                        triggered_by="order_placer.place",
                    )
                except Exception as kse:
                    log_exception(self._log, kse)
                    self._log.critical(
                        "order_placer.hard_kill_failed",
                        extra={
                            "trade_id": trade_id,
                            "kill_error": str(kse),
                        },
                    )
            raise

        # ── Register for fill tracking (OP5) + monitor (BL-7c / A.3.c) ─────
        # track() + _fill_map write happens per leg. ENTRY always; SL only
        # for LIMIT_TRIPLE (CO bundles SL at broker side); TGT when present.
        # Empty broker_order_id → skip (handles soft-fail legs gracefully).
        #
        # OP-EF2a (Phase E.6): wrap in try/except. Symmetric to BL-8's
        # persist-failure cleanup -- if track() raises after persist
        # succeeded, broker orders are live + DB rows exist + monitor
        # coverage is partial. Roll back _fill_map + untrack + delegate
        # to _handle_placement_failure. Trigger is near-impossible today
        # (UUID4 collision) but the gap is real; see module docstring.
        exit_side = "SELL" if side == "BUY" else "BUY"
        now = now_ist()  # shared across all 3 legs (one broker placement)
        successfully_tracked: List[str] = []
        # OP-AR2: separate set of internal_ids inserted into _fill_map but not
        # yet handed to order_monitor.track(). On track() failure the cleanup
        # path must pop these too, otherwise a stale _fill_map row leaks.
        successfully_inserted: List[str] = []

        try:
            # OP-AR1 (atomic registration): insert _fill_map BEFORE track().
            # The order_monitor poll thread can fire OrderFilled microseconds
            # after track() returns; if _fill_map is still empty at that point
            # the fill becomes a ghost entry. Insert-then-track closes the
            # window because OrderFilled handlers always look up _fill_map.
            if result.entry_internal_id and result.entry_broker_order_id:
                with self._fill_map_lock:
                    self._fill_map[result.entry_internal_id] = _FillEntry(
                        trade_id=trade_id,
                        reservation_id=reservation_id,
                        symbol=symbol,
                        qty=qty,
                        leg=_LEG_ENTRY,
                        order_protocol=order_protocol,
                        direction=direction,
                        # Naked-short fix (2.1): cache exit params for deferred
                        # place_exits() on LIMIT_TRIPLE ENTRY fill.
                        side=side,
                        sl_price=sl_price,
                        tgt_price=tgt_price,
                        intent=intent,
                        # Slice 1: freeze the strategy R:R so the fill-time TGT
                        # recalc honours THIS trade's config (0.0 -> _rr_ratio fallback).
                        tgt_risk_reward=(tgt_risk_reward or 0.0),
                    )
                successfully_inserted.append(result.entry_internal_id)
                self._order_monitor.track(
                    internal_order_id=result.entry_internal_id,
                    broker_order_id=result.entry_broker_order_id,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    expected_price=entry_price,
                    placed_at=now,
                    leg="ENTRY",
                    tgt_price=tgt_price,
                    sl_price=sl_price,
                )
                successfully_tracked.append(result.entry_internal_id)

            # SL leg — CO_PLUS_TGT has SL bundled into the CO at broker side.
            if (
                result.order_protocol != "CO_PLUS_TGT"
                and result.sl_internal_id
                and result.sl_broker_order_id
            ):
                with self._fill_map_lock:
                    self._fill_map[result.sl_internal_id] = _FillEntry(
                        trade_id=trade_id,
                        reservation_id=reservation_id,
                        symbol=symbol,
                        qty=qty,
                        leg=_LEG_SL,
                        order_protocol=order_protocol,
                        direction=direction,
                    )
                successfully_inserted.append(result.sl_internal_id)
                self._order_monitor.track(
                    internal_order_id=result.sl_internal_id,
                    broker_order_id=result.sl_broker_order_id,
                    symbol=symbol,
                    side=exit_side,
                    qty=qty,
                    expected_price=sl_price,
                    placed_at=now,
                    leg="SL",
                )
                successfully_tracked.append(result.sl_internal_id)

            # TGT leg — both LIMIT_TRIPLE and CO_PLUS_TGT place a separate TGT order.
            if result.tgt_internal_id and result.tgt_broker_order_id:
                with self._fill_map_lock:
                    self._fill_map[result.tgt_internal_id] = _FillEntry(
                        trade_id=trade_id,
                        reservation_id=reservation_id,
                        symbol=symbol,
                        qty=qty,
                        leg=_LEG_TGT,
                        order_protocol=order_protocol,
                        direction=direction,
                    )
                successfully_inserted.append(result.tgt_internal_id)
                self._order_monitor.track(
                    internal_order_id=result.tgt_internal_id,
                    broker_order_id=result.tgt_broker_order_id,
                    symbol=symbol,
                    side=exit_side,
                    qty=qty,
                    expected_price=tgt_price,
                    placed_at=now,
                    leg="TGT",
                )
                successfully_tracked.append(result.tgt_internal_id)
        except Exception as track_exc:
            # OP-EF2a/OP-EF2b: track() raised after _persist_entry_orders
            # succeeded. Broker has orders; DB has rows; monitor coverage
            # is partial. Clean up (pop _fill_map entries, untrack any
            # successful legs) then delegate to _handle_placement_failure
            # for broker cancel + trade FAILED + reservation release.
            #
            # Do NOT fire hard_kill here. Capital tracking remains
            # consistent (cancel-or-log + release-reservation). This is
            # "protocol-only failure" class per OP-BL8e; hard_kill is
            # reserved for DB/broker drift scenarios (BL-4/BL-8/BL-9
            # paths). Documented so future maintainers do not "helpfully
            # add hard_kill here for symmetry."
            # OP-AR2: pop everything we inserted (superset of tracked).
            # successfully_inserted always >= successfully_tracked because the
            # _fill_map insert precedes track() per leg; on track() raise the
            # current leg is in inserted but not tracked.
            with self._fill_map_lock:
                for iid in successfully_inserted:
                    self._fill_map.pop(iid, None)
            for iid in successfully_tracked:
                try:
                    self._order_monitor.untrack(iid)
                except Exception as untrack_exc:  # noqa: BLE001
                    log_exception(self._log, untrack_exc)
                    self._log.error(
                        "order_placer.untrack_during_cleanup_failed",
                        extra={"internal_order_id": iid,
                               "error": str(untrack_exc)},
                    )

            all_broker_ids = [
                bid for bid in (
                    result.entry_broker_order_id,
                    result.sl_broker_order_id,
                    result.tgt_broker_order_id,
                ) if bid
            ]
            self._log.critical(
                "order_placer.ef2_track_failure_cleanup "
                "EF2_TRACK_FAILURE_CLEANUP: track() raised after "
                "_persist_entry_orders success; rolling back",
                extra={
                    "trade_id": trade_id,
                    "error": str(track_exc),
                    "error_type": type(track_exc).__name__,
                    "legs_successfully_tracked": successfully_tracked,
                    "broker_order_ids_to_cancel": all_broker_ids,
                },
            )

            self._handle_placement_failure(
                trade_id, reservation_id, signal_id, track_exc,
                broker_order_ids=all_broker_ids,
            )
            raise  # propagate to signal_processor

        self._log.info(
            "order_placer.place_complete",
            extra={
                "trade_id": trade_id,
                "entry_broker_id": result.entry_broker_order_id,
                "protocol": result.order_protocol,
            },
        )

        # Telegram alert: ORDER PLACED (optional; never crash on notifier failure)
        if self._notifier is not None:
            try:
                now_hm = now_ist().strftime("%H:%M")
                smart_on = (
                    self._smart_tgt_manager is not None
                    and self._smart_tgt_config is not None
                    and getattr(self._smart_tgt_config, "enabled", False)
                )
                smart_line = (
                    "Smart TGT monitoring: ACTIVE (FIXED mode)"
                    if smart_on else "Smart TGT monitoring: disabled"
                )
                body = (
                    f"Fill: ₹{entry_price:,.2f} | Qty: {qty} | {now_hm} IST\n"
                    f"SL: ₹{sl_price:,.2f} ✓ | TGT: ₹{tgt_price:,.2f} ✓\n"
                    f"{smart_line}"
                )
                self._notifier.send(
                    severity="INFO",
                    title=f"[{self._mode}] ✅ ORDER PLACED — {symbol}",
                    body=body,
                    source_module="order_placer",
                )
            except Exception as exc:
                self._log.error("order_placer: place notifier.send failed: %s", exc)

    # ── event handler ─────────────────────────────────────────────────────────

    def _on_order_filled(self, event: OrderFilled) -> None:
        """
        Handle OrderFilled event (OP6 + BL-7d).

        Dispatches on fill_entry.leg to the appropriate handler. Called from
        order_monitor's poll thread; must be thread-safe (OP8).
        """
        internal_id = event.internal_order_id
        with self._fill_map_lock:
            fill_entry = self._fill_map.get(internal_id)

        if fill_entry is None:
            # Not our trade (could be from another component or already handled)
            return

        # BL-7d: dispatch on leg. _VALID_LEGS enforced at _FillEntry construction.
        if fill_entry.leg == _LEG_ENTRY:
            self._handle_entry_fill(event, fill_entry)
        else:
            self._handle_exit_fill(event, fill_entry)

    def _on_order_partially_terminated(self, event: OrderPartiallyTerminated) -> None:
        """
        FIX-028: Handle partial-fill-then-terminate scenario.

        Order reached terminal state (CANCELLED/FAILED/EXPIRED) with qty_filled > 0.
        This is a naked position risk: the entry filled partially, creating a live
        position at the broker, but the order never reached COMPLETE so OrderFilled
        did not fire.

        This handler:
          1. Commits capital with actual_qty = filled_qty (excess auto-returned).
          2. Records partial entry fill in DB (status=OPEN).
          3. Places exit legs (SL/TGT) at the filled qty to protect the position.

        Exit placement uses the retry mechanism from FIX-012: retry up to 4 times
        with backoff; on final failure, trigger soft_kill (not hard_kill) to allow
        manual intervention while preventing new trades.

        Idempotency: pops from _fill_map atomically, so OrderPartiallyTerminated
        and OrderFilled cannot double-process.
        """
        internal_id = event.internal_order_id
        with self._fill_map_lock:
            fill_entry = self._fill_map.pop(internal_id, None)

        if fill_entry is None:
            # Not our trade or already handled
            return

        # Only handle entry legs; exit legs are logged and skipped
        if fill_entry.leg != _LEG_ENTRY:
            self._log.warning(
                "order_placer.partial_terminated_exit_leg_skipped",
                extra={
                    "internal_order_id": internal_id,
                    "trade_id": fill_entry.trade_id,
                    "leg": fill_entry.leg,
                    "reason": event.reason,
                    "qty_filled": event.filled_qty,
                },
            )
            return

        trade_id = fill_entry.trade_id
        qty_filled = event.filled_qty
        avg_price = event.avg_fill_price

        self._log.warning(
            "order_placer.partial_entry_terminated",
            extra={
                "trade_id": trade_id,
                "internal_order_id": internal_id,
                "reason": event.reason,
                "qty_filled": qty_filled,
                "qty_requested": fill_entry.qty,
                "avg_fill_price": avg_price,
            },
        )

        # Commit partial fill: commit_to_used returns excess margin to available
        try:
            self._fm.commit_to_used(
                reservation_id=fill_entry.reservation_id,
                actual_fill_price=avg_price,
                actual_qty=qty_filled,
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "order_placer.partial_terminated_commit_failed",
                extra={
                    "trade_id": trade_id,
                    "reservation_id": fill_entry.reservation_id,
                },
            )
            # commit_to_used has already fired hard_kill (BL-4)

        # Record partial entry fill in DB (sets status=OPEN)
        try:
            self._om.record_entry_fill(
                trade_id=trade_id,
                avg_fill_price=avg_price,
                qty_filled=qty_filled,
                filled_at=event.filled_at or now_ist().isoformat(),
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "order_placer.partial_terminated_record_fill_failed",
                extra={"trade_id": trade_id},
            )

        # Place exits at the actual filled qty to protect the partial position
        if fill_entry.order_protocol == "LIMIT_TRIPLE":
            self._place_limit_triple_exits(
                trade_id=trade_id,
                fill_entry=fill_entry,
                qty_filled=qty_filled,
                avg_fill_price=avg_price,
                reason=f"partial_terminated_{event.reason.lower()}",
            )
        elif fill_entry.order_protocol == "CO_PLUS_TGT":
            self._place_co_tgt_exit(
                trade_id=trade_id,
                fill_entry=fill_entry,
                qty_filled=qty_filled,
                avg_fill_price=avg_price,
                reason=f"partial_terminated_{event.reason.lower()}",
            )

        # FIX-130 (Item 16): Telegram alert for partial fill
        if self._notifier is not None:
            try:
                body = (
                    f"{event.symbol}: {qty_filled}/{fill_entry.qty} qty filled"
                    f" @ {avg_price:.2f}\n"
                    f"SL placed for {qty_filled} shares | unfilled portion cancelled"
                )
                self._notifier.send(
                    title=f"[{self._mode}] PARTIAL FILL -- {event.symbol}",
                    body=body,
                    source_module="order_placer",
                )
            except Exception as exc:
                self._log.error("order_placer: partial_fill notifier.send failed: %s", exc)

    def _on_order_status_changed(self, event: OrderStatusChanged) -> None:
        """
        FIX-017: Handle zero-fill terminal status for entry orders.

        OrderFilled fires only on COMPLETE (OM8). OrderPartiallyTerminated fires
        for partial fills with terminal status (FIX-028). This handler covers the
        remaining gap: zero-fill terminal status (qty_filled == 0).

        Zero-fill path (qty_filled == 0):
          - Release full reservation back to available.
          - Mark trade FAILED (no position at broker).

        Partial-fill path (qty_filled > 0):
          - Now handled by _on_order_partially_terminated (FIX-028).
          - OrderPartiallyTerminated is emitted by order_monitor when a terminal
            state is reached with qty_filled > 0.

        Exit-leg terminal statuses (SL/TGT/EOD) are logged and skipped: those
        legs have different capital accounting (release_used, not commit/release
        reservation) and are outside scope of FIX-017 / FIX-028.

        Idempotency: pops from _fill_map atomically, so OrderStatusChanged,
        OrderPartiallyTerminated, and OrderFilled cannot double-process.
        """
        status = (event.status or "").upper()
        if status not in ("CANCELLED", "REJECTED", "FAILED", "EXPIRED"):
            return

        internal_id = event.internal_order_id
        with self._fill_map_lock:
            fill_entry = self._fill_map.pop(internal_id, None)
        if fill_entry is None:
            return

        if fill_entry.leg != _LEG_ENTRY:
            self._log.warning(
                "order_placer.terminal_status_exit_leg_skipped",
                extra={
                    "internal_order_id": internal_id,
                    "trade_id": fill_entry.trade_id,
                    "leg": fill_entry.leg,
                    "status": status,
                    "qty_filled": event.qty_filled,
                },
            )
            return

        # FIX-017: Zero-fill cancellation path (no position created).
        # Release full reservation and mark trade FAILED.
        if event.qty_filled <= 0:
            self._log.warning(
                "order_placer.entry_cancelled_zero_fill",
                extra={
                    "trade_id": fill_entry.trade_id,
                    "internal_order_id": internal_id,
                    "status": status,
                    "qty_requested": fill_entry.qty,
                    "reservation_id": fill_entry.reservation_id,
                },
            )
            # FIX-129 (Item 43): Telegram alert for broker-side rejection of placed order.
            if self._notifier is not None and status in ("FAILED", "REJECTED"):
                try:
                    rejection_text = getattr(event, "rejection_reason", "") or status
                    self._notifier.send(
                        severity="WARNING",
                        title=f"[{self._mode}] ORDER REJECTED — {fill_entry.symbol}",
                        body=(
                            f"Broker rejected entry order after placement.\n"
                            f"Status: {status} | Trade: {fill_entry.trade_id}\n"
                            f"Reason: {str(rejection_text)[:200]}"
                        ),
                        source_module="order_placer",
                    )
                except Exception as _ne:
                    self._log.error("order_placer: zero_fill rejection alert failed: %s", _ne)
            try:
                self._fm.release(
                    reservation_id=fill_entry.reservation_id,
                    reason=f"entry_{status.lower()}_zero_fill",
                )
            except Exception as exc:
                log_exception(self._log, exc)
                self._log.error(
                    "order_placer.zero_fill_release_failed",
                    extra={
                        "trade_id": fill_entry.trade_id,
                        "reservation_id": fill_entry.reservation_id,
                    },
                )
            try:
                self._om.update_trade_status(
                    trade_id=fill_entry.trade_id,
                    status="FAILED",
                )
            except Exception as exc:
                log_exception(self._log, exc)
                self._log.error(
                    "order_placer.zero_fill_mark_failed_error",
                    extra={"trade_id": fill_entry.trade_id},
                )
            return

        # FIX-028: Partial-fill path (qty_filled > 0) is PRIMARY handled by
        # _on_order_partially_terminated (which pops the entry first). However,
        # we keep fallback handling here for two scenarios:
        #   1. Tests that directly publish OrderStatusChanged without order_monitor
        #   2. Edge cases where OrderPartiallyTerminated wasn't emitted
        # If we reach here with qty_filled > 0, process it (safe fallback).
        if event.qty_filled > 0:
            avg_price = float(event.avg_fill_price or 0.0)
            self._log.warning(
                "order_placer.partial_entry_terminated_via_status_changed",
                extra={
                    "trade_id": fill_entry.trade_id,
                    "internal_order_id": internal_id,
                    "status": status,
                    "qty_filled": event.qty_filled,
                    "qty_requested": fill_entry.qty,
                    "avg_fill_price": avg_price,
                    "note": "FIX-028: fallback path (normally OrderPartiallyTerminated)",
                },
            )

            # Commit partial fill
            try:
                self._fm.commit_to_used(
                    reservation_id=fill_entry.reservation_id,
                    actual_fill_price=avg_price,
                    actual_qty=event.qty_filled,
                )
            except Exception as exc:
                log_exception(self._log, exc)
                self._log.error(
                    "order_placer.partial_commit_failed",
                    extra={
                        "trade_id": fill_entry.trade_id,
                        "reservation_id": fill_entry.reservation_id,
                    },
                )

            # Record partial entry fill in DB
            try:
                self._om.record_entry_fill(
                    trade_id=fill_entry.trade_id,
                    avg_fill_price=avg_price,
                    qty_filled=event.qty_filled,
                    filled_at=now_ist().isoformat(),
                )
            except Exception as exc:
                log_exception(self._log, exc)
                self._log.error(
                    "order_placer.partial_record_fill_failed",
                    extra={"trade_id": fill_entry.trade_id},
                )

            # Place exits at filled qty
            if fill_entry.order_protocol == "LIMIT_TRIPLE":
                self._place_limit_triple_exits(
                    trade_id=fill_entry.trade_id,
                    fill_entry=fill_entry,
                    qty_filled=event.qty_filled,
                    avg_fill_price=avg_price,
                    reason=f"partial_terminated_{status.lower()}_fallback",
                )
            elif fill_entry.order_protocol == "CO_PLUS_TGT":
                self._place_co_tgt_exit(
                    trade_id=fill_entry.trade_id,
                    fill_entry=fill_entry,
                    qty_filled=event.qty_filled,
                    avg_fill_price=avg_price,
                    reason=f"partial_terminated_{status.lower()}_fallback",
                )
            return

    def _handle_entry_fill(self, event: OrderFilled, fill_entry: "_FillEntry") -> None:
        """
        Commit capital reservation and record entry fill in DB (BL-7d).

        Pops the entry row from _fill_map. Safe to call once per internal_id.
        Exceptions in commit_to_used / record_entry_fill are logged but not
        re-raised: the trade is open at the broker; the reconciler is the
        backstop for capital/DB drift.
        """
        internal_id = event.internal_order_id
        with self._fill_map_lock:
            self._fill_map.pop(internal_id, None)

        trade_id = fill_entry.trade_id
        reservation_id = fill_entry.reservation_id

        self._log.info(
            "order_placer.fill_received",
            extra={
                "trade_id": trade_id,
                "internal_order_id": internal_id,
                "broker_order_id": event.broker_order_id,
                "avg_fill_price": event.avg_fill_price,
                "filled_qty": event.filled_qty,
            },
        )

        # Commit capital reservation (reservation → used)
        try:
            self._fm.commit_to_used(
                reservation_id=reservation_id,
                actual_fill_price=event.avg_fill_price,
                actual_qty=event.filled_qty,
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "order_placer.commit_capital_failed",
                extra={"trade_id": trade_id, "reservation_id": reservation_id},
            )
            # Post-BL-4 (Phase C.1): commit_to_used has already fired
            # kill_switch.hard_kill before re-raising, so the system is
            # halting new orders and cancelling in-flight ones. This catch
            # block still runs to attach trade_id/reservation_id context
            # to the logs, but the "continue" below is effectively a
            # wind-down -- nothing new can be placed. DB recording of the
            # fill still proceeds so the trade row matches broker truth.

        # Record fill in DB
        try:
            self._om.record_entry_fill(
                trade_id=trade_id,
                avg_fill_price=event.avg_fill_price,
                qty_filled=event.filled_qty,
                filled_at=event.filled_at or now_ist().isoformat(),
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "order_placer.record_fill_failed",
                extra={"trade_id": trade_id},
            )

        # FIX-016: Naked-short fix. For both protocols, exits are DEFERRED to fill
        # time and placed at the ACTUAL filled qty (not the requested qty).
        #   - LIMIT_TRIPLE: places both SL + TGT
        #   - CO_PLUS_TGT: places TGT only (SL embedded in CO bracket)
        if fill_entry.order_protocol == "LIMIT_TRIPLE":
            self._place_limit_triple_exits(
                trade_id=trade_id,
                fill_entry=fill_entry,
                qty_filled=int(event.filled_qty),
                avg_fill_price=float(event.avg_fill_price),
                reason="entry_fill",
            )
        elif fill_entry.order_protocol == "CO_PLUS_TGT":
            self._place_co_tgt_exit(
                trade_id=trade_id,
                fill_entry=fill_entry,
                qty_filled=int(event.filled_qty),
                avg_fill_price=float(event.avg_fill_price),
                reason="entry_fill",
            )

        # FIX-132 Item 8: register LIMIT_TRIPLE trades with BreakevenManager.
        if (
            fill_entry.order_protocol == "LIMIT_TRIPLE"
            and self._breakeven_manager is not None
            and getattr(fill_entry, "strategy_obj", None) is not None
            and getattr(fill_entry.strategy_obj, "trailing_sl_enabled", False)
        ):
            try:
                trade_row = self._om.get_trade(trade_id)
                if trade_row is not None:
                    s = fill_entry.strategy_obj
                    self._breakeven_manager.register_trade(
                        trade_id=trade_id,
                        symbol=fill_entry.symbol,
                        direction=fill_entry.direction,
                        entry_price=float(event.avg_fill_price),
                        target_price=float(trade_row.get("tgt_initial", 0) or 0),
                        breakeven_trigger_pct=float(s.trailing_sl_breakeven_trigger_pct),
                        partial_lock_trigger_pct=float(s.trailing_sl_partial_lock_trigger_pct),
                        partial_lock_sl_pct=float(s.trailing_sl_partial_lock_sl_pct),
                    )
            except Exception as exc:
                log_exception(self._log, exc)
                self._log.error(
                    "order_placer.breakeven_register_failed",
                    extra={"trade_id": trade_id, "error": str(exc)},
                )

        # BL-7d: register CO_PLUS_TGT trades with SmartTgtManager for SL trailing.
        # LIMIT_TRIPLE legs have static SL orders already at the broker; CO
        # legs have a CO_TRIGGER that needs server-side trail updates.
        if (
            fill_entry.order_protocol == "CO_PLUS_TGT"
            and self._smart_tgt_manager is not None
            and self._smart_tgt_config is not None
        ):
            try:
                trade_row = self._om.get_trade(trade_id)
                if trade_row is None:
                    raise RuntimeError(f"trade {trade_id!r} vanished before register")
                initial_sl = float(trade_row["sl_initial"])
                token = 0
                if self._instrument_cache is not None:
                    try:
                        token = self._instrument_cache.get_by_symbol(
                            fill_entry.symbol
                        ).instrument_token
                    except Exception:
                        token = 0  # cache miss; smart_tgt tolerates 0 (LTP lookup fallback)
                self._smart_tgt_manager.register_trade(
                    trade_id=trade_id,
                    symbol=fill_entry.symbol,
                    instrument_token=token,
                    direction=fill_entry.direction,
                    entry_price=event.avg_fill_price,
                    initial_sl=initial_sl,
                    qty=event.filled_qty,
                    trigger_pct=self._smart_tgt_config.trigger_pct,
                    step_pct=self._smart_tgt_config.step_pct,
                )
            except Exception as exc:
                log_exception(self._log, exc)
                self._log.critical(
                    "order_placer.smart_tgt_register_failed",
                    extra={"trade_id": trade_id, "symbol": fill_entry.symbol,
                           "error": str(exc)},
                )
                # Do NOT re-raise: trade is open; reconciler + manual ops as backstop

    def _handle_exit_fill(self, event: OrderFilled, fill_entry: "_FillEntry") -> None:
        """
        Close the trade and release used capital on SL/TGT/EOD fill (BL-7d + BL-10a).

        Pops the _fill_map entry, computes direction-aware gross PnL and
        round-trip charges, finalizes the trade row (OrderManager.close_trade),
        releases used capital (FundManager.release_used), publishes
        PositionClosed, and unregisters from SmartTgtManager when applicable.

        Failure policy:
            - _VALID_LEGS already excludes ENTRY; caller guarantees exit leg.
            - get_trade returning None is unrecoverable: log CRITICAL and return
              (no close, no release). Reconciler is the backstop.
            - close_trade raising ValueError("already CLOSED") means a double-fire
              (e.g. OCO SL+TGT race): log WARNING, skip release_used + publish.
            - release_used, publish, unregister failures are logged but not
              re-raised. The trade is closed at the broker; the reconciler
              catches capital/state drift.
        """
        internal_id = event.internal_order_id
        with self._fill_map_lock:
            self._fill_map.pop(internal_id, None)

        trade_id = fill_entry.trade_id
        exit_reason = _LEG_TO_EXIT_REASON[fill_entry.leg]  # KeyError → programmer bug

        self._log.info(
            "order_placer.exit_fill_received",
            extra={
                "trade_id": trade_id,
                "internal_order_id": internal_id,
                "broker_order_id": event.broker_order_id,
                "leg": fill_entry.leg,
                "exit_reason": exit_reason,
                "avg_fill_price": event.avg_fill_price,
                "filled_qty": event.filled_qty,
            },
        )

        trade_row = self._om.get_trade(trade_id)
        if trade_row is None:
            self._log.critical(
                "order_placer.exit_fill_trade_missing",
                extra={"trade_id": trade_id, "internal_order_id": internal_id},
            )
            return

        signal_id = trade_row.get("signal_id") or ""
        entry_price = float(trade_row.get("entry_actual_price") or 0.0)
        direction = trade_row.get("direction") or fill_entry.direction

        # Derive product/intent from the cached order_protocol. The trades table
        # does not persist product; protocol is authoritative at fill time.
        product = _PROTOCOL_TO_PRODUCT.get(fill_entry.order_protocol, "")
        if not product:
            self._log.warning(
                "order_placer.exit_fill_unknown_protocol",
                extra={
                    "trade_id": trade_id,
                    "order_protocol": fill_entry.order_protocol,
                },
            )
            product = "MIS"  # safe default: intraday
        intent = _PRODUCT_TO_INTENT.get(product, "INTRADAY")

        exit_price = float(event.avg_fill_price)
        exit_qty = int(event.filled_qty)

        # BL-10a: round-trip charges via CostCalculator.
        # v14: capture full breakdown for per-trade cost columns.
        cost_breakdown = None
        try:
            cost_breakdown = self._cost_calculator.round_trip_breakdown(
                qty=exit_qty,
                entry_price=entry_price,
                exit_price=exit_price,
                product=product,
            )
            charges = cost_breakdown.total
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "order_placer.cost_calc_failed",
                extra={"trade_id": trade_id, "product": product},
            )
            charges = 0.0

        # Direction-correct gross PnL (EF-3).
        if direction == "LONG":
            gross_pnl = (exit_price - entry_price) * exit_qty
        else:  # SHORT
            gross_pnl = (entry_price - exit_price) * exit_qty

        # BL-10a: close_trade first (authoritative DB state + double-close guard).
        try:
            closed_row = self._om.close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_qty=exit_qty,
                exit_reason=exit_reason,
                gross_pnl=gross_pnl,
                charges=charges,
                cost_breakdown=cost_breakdown,
            )
        except ValueError as exc:
            # Double-close (e.g. OCO race): DB already CLOSED, capital already released.
            self._log.warning(
                "order_placer.exit_fill_already_closed",
                extra={"trade_id": trade_id, "error": str(exc)},
            )
            return
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "order_placer.close_trade_failed",
                extra={"trade_id": trade_id, "exit_reason": exit_reason},
            )
            return

        net_pnl = (closed_row or {}).get("net_pnl", gross_pnl - charges)

        # v14: compute and persist MFE/MAE from candle data
        try:
            exc_data = self._om._store.compute_trade_excursions(trade_id)
            if exc_data:
                self._om._store.insert_trade_excursion(trade_id=trade_id, **exc_data)
        except Exception as exc:
            self._log.debug("order_placer.excursion_compute_failed: %s", exc)

        # Telegram alert: TARGET HIT / STOP LOSS HIT (optional).
        # EOD exits are intentionally excluded — covered by EOD DAILY SUMMARY.
        if self._notifier is not None and exit_reason in ("TGT_HIT", "SL_HIT"):
            try:
                if exit_reason == "TGT_HIT":
                    emoji = "🎯"
                    title_word = "TARGET HIT"
                    pnl_sign = "+"
                else:
                    emoji = "🔴"
                    title_word = "STOP LOSS HIT"
                    pnl_sign = "+" if net_pnl >= 0 else "-"
                body = (
                    f"Exit: ₹{float(exit_price):,.2f} | Direction: {direction}\n"
                    f"Net P&L: {pnl_sign}₹{abs(float(net_pnl)):,.2f}"
                )
                self._notifier.send(
                    severity="INFO",
                    title=f"[{self._mode}] {emoji} {title_word} — {fill_entry.symbol}",
                    body=body,
                    source_module="order_placer",
                )
            except Exception as exc:
                self._log.error(
                    "order_placer: exit_fill notifier.send failed: %s", exc
                )

        # Audit #5: OCO — cancel the sibling exit leg so a late fill can't
        # re-open a naked position after we've already claimed the exit.
        # Runs after close_trade (which guards against double-exit) and
        # before release_used (so a sibling fill racing this cancel still
        # finds the trade CLOSED and short-circuits via the double-close
        # warning path).
        try:
            self._cancel_oco_siblings(
                trade_id=trade_id,
                except_broker_order_id=event.broker_order_id,
                order_protocol=fill_entry.order_protocol,
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "order_placer.oco_sibling_cancel_failed",
                extra={"trade_id": trade_id},
            )

        # Release used capital. reservation_id is not meaningful here; release_used
        # uses symbol+intent bucket for accounting (not the reservation ledger).
        try:
            self._fm.release_used(
                symbol=fill_entry.symbol,
                exit_price=exit_price,
                exit_qty=exit_qty,
                intent=intent,
                entry_price=entry_price,
                direction=direction,
                costs=charges,
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "order_placer.release_used_failed",
                extra={
                    "trade_id": trade_id, "symbol": fill_entry.symbol,
                    "intent": intent, "direction": direction,
                },
            )
            # Continue: trade is CLOSED in DB; reconciler's CAPITAL_DRIFT check is the backstop.

        # BL-10a: publish PositionClosed for subscribers (shadow_tracker, alerts).
        # realized_pnl uses NET (after charges), consistent with reports.
        try:
            self._bus.publish(PositionClosed(
                source_module="order_placer",
                symbol=fill_entry.symbol,
                trade_id=trade_id,
                signal_id=signal_id,
                exit_price=exit_price,
                realized_pnl=float(net_pnl),
            ))
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "order_placer.publish_position_closed_failed",
                extra={"trade_id": trade_id},
            )

        # v14: copy sl_trail_count from smart_tgt_state before unregister deletes the row.
        if (
            fill_entry.order_protocol == "CO_PLUS_TGT"
            and self._smart_tgt_manager is not None
        ):
            try:
                row = self._om._store.fetch_one(
                    "SELECT trail_count FROM smart_tgt_state WHERE trade_id = ?",
                    (trade_id,),
                )
                if row is not None:
                    self._om._store.execute(
                        "UPDATE trades SET sl_trail_count = ? WHERE trade_id = ?",
                        (row["trail_count"], trade_id),
                    )
            except Exception as exc:
                self._log.debug(
                    "order_placer.sl_trail_count_copy_failed: %s", exc,
                )

            try:
                self._smart_tgt_manager.unregister_trade(trade_id)
            except Exception as exc:
                log_exception(self._log, exc)
                self._log.error(
                    "order_placer.smart_tgt_unregister_failed",
                    extra={"trade_id": trade_id},
                )

        # FIX-132 Item 8: unregister LIMIT_TRIPLE trades from BreakevenManager on exit
        if self._breakeven_manager is not None:
            try:
                self._breakeven_manager.unregister_trade(trade_id)
            except Exception as exc:
                log_exception(self._log, exc)
                self._log.error(
                    "order_placer.breakeven_unregister_failed",
                    extra={"trade_id": trade_id},
                )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _finalize_cnc_gtt(self, trade_id: str, fill_entry: "_FillEntry",
                          qty_filled: int, legs, reason: str) -> None:
        """SLICE2.5-P1: finalize a DELIVERY (CNC) trade whose overnight protection is a
        single broker-side OCO GTT (placed by the centralized gate). Unlike the
        LIMIT_TRIPLE path there are NO day-leg order rows to persist, NO _fill_map OCO
        siblings (the GTT IS the OCO at the broker), and the day-leg after-check does
        not apply. We log the gtt_id and record a verified exit. DURABLE gtt_id
        persistence (carry-state columns) lands in Phase 2 — P1 is schema-neutral."""
        self._log.info(
            "order_placer.cnc_gtt_placed",
            extra={
                "trade_id": trade_id, "symbol": fill_entry.symbol, "gtt_id": legs.gtt_id,
                "qty_filled": qty_filled, "sl_trigger": legs.sl_trigger_price,
                "sl_limit": legs.sl_price, "tgt_trigger": legs.tgt_price, "reason": reason,
            },
        )
        try:
            self._om.record_exits_verification(
                trade_id, 1,
                f"GTT placed (CNC overnight protection); gtt_id={legs.gtt_id}",
            )
        except Exception:  # noqa: BLE001 — never break the fill path
            pass

    def _persist_sl_only_protected(
        self, *, trade_id: str, fill_entry: "_FillEntry",
        qty_filled: int, legs, reason: str,
    ) -> None:
        """FIX-190 (Bug C): persist + track an SL-only protected position when the
        TGT could not be placed (the SL is live, so the position IS protected).

        Does NOT emergency-exit or HARD_KILL — a missing TGT is an opportunity
        cost, not a protection breach. The position exits via its SL or EOD
        square-off. (Bug D's circuit-band clamp makes the circuit-rejection case
        that triggered this on 19-Jun no longer occur; this is the safety net for
        any other transient TGT failure.)
        """
        self._log.critical(
            "order_placer.limit_triple_tgt_unplaced_sl_protected",
            extra={
                "trade_id": trade_id, "symbol": fill_entry.symbol,
                "qty_filled": qty_filled,
                "sl_broker_id": legs.sl_broker_order_id,
                "reason": reason,
                "detail": "TGT not placed (e.g. circuit band); position protected "
                          "by the live SL; exits via SL or EOD; NOT escalating",
            },
        )
        if self._product_resolver is None:
            self._fire_hard_kill_for_unprotected_position(
                trade_id,
                RuntimeError("product_resolver missing; cannot persist SL"),
            )
            return
        product = self._product_resolver.resolve(fill_entry.intent)
        exit_side = "SELL" if fill_entry.side == "BUY" else "BUY"
        specs = [
            OrderInsertSpec(
                broker_order_id=legs.sl_broker_order_id,
                leg="SL",
                transaction_type=exit_side,
                order_type=legs.sl_order_type,
                product=product,
                variety="regular",
                qty_requested=qty_filled,
                price=legs.sl_price,
                trigger_price=legs.sl_trigger_price,
            ),
        ]
        try:
            self._om.insert_orders_atomic(trade_id, specs)
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "order_placer.sl_only_persist_failed "
                "LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED",
                extra={"trade_id": trade_id, "error": str(exc)},
            )
            self._cancel_broker_orders(
                [legs.sl_broker_order_id], reason="sl_only_persist_failed",
            )
            self._fire_hard_kill_for_unprotected_position(trade_id, exc)
            return

        # Task (TGT retry, 2026-06-19): the SL persisted, so the position is
        # protected but owes a TGT. Flag it for TGTRetryManager to re-attempt the
        # TGT on a backoff schedule WITHOUT touching the live SL. Best-effort —
        # a flag failure must not break the SL-protected path (the SL still
        # protects; worst case the TGT is simply never retried).
        try:
            self._om._store.mark_needs_tgt_retry(trade_id)
            self._log.info(
                "order_placer.tgt_retry_flagged",
                extra={"trade_id": trade_id, "symbol": fill_entry.symbol},
            )
        except Exception as exc:
            self._log.warning(
                "order_placer.tgt_retry_flag_failed",
                extra={"trade_id": trade_id, "error": str(exc)},
            )

        now = now_ist()
        try:
            with self._fill_map_lock:
                self._fill_map[legs.sl_internal_id] = _FillEntry(
                    trade_id=trade_id,
                    reservation_id=fill_entry.reservation_id,
                    symbol=fill_entry.symbol,
                    qty=qty_filled,
                    leg=_LEG_SL,
                    order_protocol="LIMIT_TRIPLE",
                    direction=fill_entry.direction,
                )
            self._order_monitor.track(
                internal_order_id=legs.sl_internal_id,
                broker_order_id=legs.sl_broker_order_id,
                symbol=fill_entry.symbol,
                side=exit_side,
                qty=qty_filled,
                expected_price=legs.sl_trigger_price,
                placed_at=now,
                leg="SL",
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "order_placer.sl_only_track_failed",
                extra={"trade_id": trade_id, "error": str(exc)},
            )

    def retry_tgt_for_trade(self, trade_id: str) -> str:
        """
        TGT retry (Task 2026-06-19): attempt to place the missing TGT for a
        LIMIT_TRIPLE trade left SL-only by FIX-190 Bug C. Re-reads fresh state
        and re-checks every guard immediately before placing (TGTRetryManager's
        snapshot may be stale). On success the TGT is persisted AND registered in
        _fill_map + order_monitor, so a TGT fill triggers the software OCO (cancel
        the SL + close the trade) exactly like the normal place_exits path. The
        live SL is never touched.

        Returns a status string consumed by TGTRetryManager:
          "placed"              - TGT placed + persisted + tracked  -> clear flag, INFO
          "skipped_closed"      - trade gone / no longer OPEN/PARTIAL -> clear flag
          "skipped_no_sl"       - SL not standing; never place a naked TGT -> clear flag
          "skipped_has_tgt"     - an active TGT already exists         -> clear flag
          "skipped_unplaceable" - clamped/recomputed TGT not profitable -> keep retrying
          "failed"              - broker rejected the TGT              -> keep retrying
        """
        store = self._om._store
        trade = store.get_trade_for_tgt_retry(trade_id)
        if trade is None or (trade["status"] not in ("OPEN", "PARTIAL")):
            return "skipped_closed"

        qty = int(trade["qty_filled"] or 0)
        if qty <= 0:
            return "skipped_closed"

        # Guard 1: the SL must still be standing. A retry must NEVER leave a naked
        # TGT — a TGT fill with no SL is an unprotected reverse. If the SL is gone
        # the reconciler (CHECK9 / G5b) owns recovery, not this path.
        if store.get_sl_order_for_trade(trade_id) is None:
            self._log.warning(
                "order_placer.tgt_retry_skipped_no_sl",
                extra={"trade_id": trade_id, "symbol": trade["symbol"]},
            )
            return "skipped_no_sl"

        # Guard 2: idempotency — never place a second TGT if one is already live.
        for o in store.get_orders_for_trade(trade_id):
            if o["leg"] == "TGT" and o["status"] not in _TERMINAL_ORDER_STATUSES:
                return "skipped_has_tgt"

        symbol = trade["symbol"]
        direction = trade["direction"] or "LONG"
        entry_side = "BUY" if direction == "LONG" else "SELL"
        exit_side = "SELL" if entry_side == "BUY" else "BUY"
        entry_price = float(trade["entry_actual_price"] or 0.0)
        sl_price = float(trade["sl_initial"] or 0.0)
        product = trade["product"] or ""
        intent = _PRODUCT_TO_INTENT.get(product, "INTRADAY")

        # Recompute the TGT the way the original placement did (preserve R:R from
        # the actual fill); fall back to the stored tgt_initial if needed.
        tgt_price = 0.0
        if entry_price > 0 and sl_price > 0:
            tgt_price = calc_tgt_price(
                direction=direction, entry_price=entry_price,
                sl_price=sl_price,
                # Slice 1: TGT retry must use the same strategy R:R as placement
                # (read from the stored column; fallback 2.0 + WARNING).
                rr_ratio=self._resolve_fill_rr(
                    trade["tgt_risk_reward_applied"], trade_id, symbol),
            )
        if tgt_price <= 0:
            tgt_price = float(trade["tgt_initial"] or 0.0)

        # Guard: never place a TGT we could not compute a positive price for.
        # De-dup (NOCIL fix): the WRONG-SIDE invariant is now enforced inside the
        # placeability gate (clamp_exit_into_band) at the single chokepoint —
        # place_tgt_only refuses a wrong-side / band-too-tight TGT and reports it
        # via result.unplaceable. The old per-caller Guard-3 wrong-side clause +
        # the post-clamp place-then-cancel re-check are therefore removed (only
        # the tgt_price<=0 computation guard remains).
        if tgt_price <= 0:
            return "skipped_unplaceable"

        result = self._engine.place_deferred_tgt_only(
            symbol=symbol, entry_side=entry_side, qty=qty,
            tgt_price=tgt_price, intent=intent, trade_id=trade_id, tag=trade_id,
            entry_fill=entry_price,  # NOCIL placeability gate reference
        )
        if not result.placed:
            # Gate refused a wrong-side (band-too-tight) TGT -> keep retrying;
            # a genuine broker reject -> "failed".
            return "skipped_unplaceable" if result.unplaceable else "failed"

        # The gate-approved (clamped) price actually placed at the broker — used
        # for the DB row, the OCO fill-map registration, and the log below.
        final_tgt = result.tgt_price

        # Persist the TGT order row, then register for software OCO. Same machinery
        # as place_exits so a TGT fill cancels the SL and closes the trade.
        if self._product_resolver is None:
            self._cancel_broker_orders(
                [result.tgt_broker_order_id], reason="tgt_retry_no_product_resolver",
            )
            return "failed"
        resolved_product = self._product_resolver.resolve(intent)
        spec = OrderInsertSpec(
            broker_order_id=result.tgt_broker_order_id,
            leg="TGT",
            transaction_type=exit_side,
            order_type="LIMIT",
            product=resolved_product,
            variety="regular",
            qty_requested=qty,
            price=final_tgt,
        )
        try:
            self._om.insert_orders_atomic(trade_id, [spec])
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "order_placer.tgt_retry_persist_failed",
                extra={"trade_id": trade_id, "error": str(exc)},
            )
            self._cancel_broker_orders(
                [result.tgt_broker_order_id], reason="tgt_retry_persist_failed",
            )
            return "failed"

        # OCO registration: fill_map FIRST, track() SECOND (matches place_exits).
        # reservation_id="" — exit legs use release_used (not the reservation) for
        # capital accounting, same convention as a rehydrated exit leg.
        try:
            with self._fill_map_lock:
                self._fill_map[result.tgt_internal_id] = _FillEntry(
                    trade_id=trade_id,
                    reservation_id="",
                    symbol=symbol,
                    qty=qty,
                    leg=_LEG_TGT,
                    order_protocol="LIMIT_TRIPLE",
                    direction=direction,
                )
            self._order_monitor.track(
                internal_order_id=result.tgt_internal_id,
                broker_order_id=result.tgt_broker_order_id,
                symbol=symbol,
                side=exit_side,
                qty=qty,
                expected_price=final_tgt,
                placed_at=now_ist(),
                leg="TGT",
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "order_placer.tgt_retry_track_failed",
                extra={"trade_id": trade_id, "error": str(exc)},
            )
            # TGT is placed + persisted; a track failure means a fill may not
            # auto-update the DB, but the reconciler is the backstop. Treat as
            # placed so the flag clears (avoids a duplicate TGT on the next cycle).

        self._log.info(
            "order_placer.tgt_retry_placed",
            extra={
                "trade_id": trade_id, "symbol": symbol,
                "tgt_price": final_tgt, "qty": qty,
                "clamped": result.clamped,
            },
        )
        return "placed"

    def _place_limit_triple_exits(
        self,
        *,
        trade_id: str,
        fill_entry: "_FillEntry",
        qty_filled: int,
        avg_fill_price: float,
        reason: str,
    ) -> None:
        """
        Naked-short fix (2.1): place SL + TGT for a LIMIT_TRIPLE trade AFTER
        the ENTRY has filled (COMPLETE or partial-then-cancelled).

        Called from _handle_entry_fill (COMPLETE) and _on_order_status_changed
        (partial cancel with qty_filled > 0). Both paths reach here with the
        ENTRY already committed to capital and recorded in DB.

        qty_filled is the ACTUAL filled qty, not the requested qty. Sizing
        SL/TGT to the filled qty is the core of the naked-short fix: a
        partial fill of 100 shares produces SL+TGT at 100, never at 1000.

        FIX-013: TGT price is recalculated from avg_fill_price to preserve
        risk:reward ratio under entry slippage. SL price remains anchored to
        the original strategy-requested level.

        Failure policy:
          - qty_filled ≤ 0 → skip (nothing to protect).
          - place_deferred_exits raises BrokerError → position is live with
            NO SL. This is a capital-protection breach. Fire kill_switch.hard_kill
            (if injected) and log CRITICAL with grep tag
            LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED. Do NOT re-raise:
            we are inside an event handler; the reconciler is the backstop.
          - TGT succeeded after SL failure is not possible (place_exits places
            SL first; on SL failure, TGT is not attempted). If the protocol
            raises, either nothing or only SL is placed.
          - Persist/track failures AFTER broker ack: cancel the broker orders
            best-effort, hard_kill, do not re-raise.
        """
        if qty_filled <= 0:
            self._log.warning(
                "order_placer.limit_triple_exits_skipped_zero_qty",
                extra={"trade_id": trade_id, "reason": reason},
            )
            return

        # FIX-013: Recalculate TGT from actual fill price to preserve R:R.
        # SL stays anchored to original strategy level.
        actual_tgt_price = calc_tgt_price(
            direction=fill_entry.direction,
            entry_price=avg_fill_price,
            sl_price=fill_entry.sl_price,
            # Slice 1: honour THIS trade's strategy R:R (fallback 2.0 + WARNING).
            rr_ratio=self._resolve_fill_rr(
                fill_entry.tgt_risk_reward, fill_entry.trade_id, fill_entry.symbol),
        )
        theoretical_tgt = fill_entry.tgt_price
        tgt_delta = actual_tgt_price - theoretical_tgt

        self._log.info(
            "order_placer.tgt_recalc_from_fill_price",
            extra={
                "trade_id": trade_id,
                "symbol": fill_entry.symbol,
                "avg_fill_price": avg_fill_price,
                "theoretical_tgt": theoretical_tgt,
                "actual_tgt": actual_tgt_price,
                "delta": tgt_delta,
                "sl_price": fill_entry.sl_price,
            },
        )

        try:
            legs = self._engine.place_deferred_exits(
                order_protocol="LIMIT_TRIPLE",
                symbol=fill_entry.symbol,
                entry_side=fill_entry.side,
                qty=qty_filled,
                sl_price=fill_entry.sl_price,
                tgt_price=actual_tgt_price,
                intent=fill_entry.intent,
                trade_id=trade_id,
                tag=trade_id,
                entry_fill=avg_fill_price,  # NOCIL placeability gate reference
            )
        except Exception as exc:
            # FIX-061: Detect LTP validation errors and add to retry queue
            if self._is_ltp_validation_error(exc):
                self._add_to_exit_retry(
                    trade_id=trade_id,
                    fill_entry=fill_entry,
                    qty_filled=qty_filled,
                    avg_fill_price=avg_fill_price,
                    reason=reason,
                )
                return

            # All other errors: attempt emergency market exit, then hard_kill
            log_exception(self._log, exc)
            self._log.critical(
                "order_placer.limit_triple_exits_failed "
                "LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED: "
                "ENTRY filled but SL/TGT placement raised; position has no protection",
                extra={
                    "trade_id": trade_id,
                    "symbol": fill_entry.symbol,
                    "qty_filled": qty_filled,
                    "sl_price": fill_entry.sl_price,
                    "tgt_price": fill_entry.tgt_price,
                    "intent": fill_entry.intent,
                    "reason": reason,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            # FIX-148 (GAP 1): Emergency market exit before hard_kill
            self._emergency_market_exit(
                trade_id, fill_entry, qty_filled,
                reason=f"sl_placement_failed: {type(exc).__name__}",
            )
            self._fire_hard_kill_for_unprotected_position(trade_id, exc)
            return

        # SLICE2.5-P1: a DELIVERY (CNC) trade is protected by a single broker-side OCO
        # GTT (placed by the centralized gate in place_deferred_exits). Finalize and
        # return — NO day-leg persist, NO _fill_map OCO registration, NO day-leg
        # after-check (none of which apply to a GTT). INTRADAY path below is unchanged.
        if getattr(legs, "is_gtt", False):
            self._finalize_cnc_gtt(trade_id, fill_entry, qty_filled, legs, reason)
            return

        # FIX-190 (Bug C): SL placed but TGT could not be placed (e.g. target
        # outside the circuit band). The position IS protected by the live stop,
        # so persist the SL only and DO NOT escalate. Previously place_exits
        # raised on a TGT-only failure and the except above HARD_KILLed the whole
        # book + emergency-exited (the 19-Jun cascade). A missing TGT is an
        # opportunity cost, not a protection breach.
        if not legs.tgt_placed:
            self._persist_sl_only_protected(
                trade_id=trade_id, fill_entry=fill_entry,
                qty_filled=qty_filled, legs=legs, reason=reason,
            )
            # Slice 1 Part B: record the verdict (readable). No fresh alert — the
            # SL-only state is an expected, handled condition (FIX-190 Bug C: SL
            # is standing, TGT is owed to TGTRetryManager).
            try:
                self._om.record_exits_verification(
                    trade_id, 0, "TGT unplaced (SL standing; TGT retry queued)"
                )
            except Exception:  # noqa: BLE001 — never break the fill path
                pass
            return

        # Persist SL + TGT rows atomically. Use product derived from intent
        # (same as _persist_entry_orders).
        if self._product_resolver is None:
            # Should never happen — OrderPlacer constructor could allow it
            # for tests, but _persist_entry_orders also asserts this. Log
            # CRITICAL so the exits-placed-but-not-persisted state is surfaced.
            self._log.critical(
                "order_placer.limit_triple_exits_no_product_resolver "
                "LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED",
                extra={"trade_id": trade_id},
            )
            self._fire_hard_kill_for_unprotected_position(
                trade_id,
                RuntimeError("product_resolver missing; cannot persist exit legs"),
            )
            return

        product = self._product_resolver.resolve(fill_entry.intent)
        exit_side = "SELL" if fill_entry.side == "BUY" else "BUY"
        specs: List[OrderInsertSpec] = [
            OrderInsertSpec(
                broker_order_id=legs.sl_broker_order_id,
                leg="SL",
                transaction_type=exit_side,
                order_type=legs.sl_order_type,   # always "SL" (stop-limit) post-P0 2026-06-15
                product=product,
                variety="regular",
                qty_requested=qty_filled,
                price=legs.sl_price,
                trigger_price=legs.sl_trigger_price,
            ),
            OrderInsertSpec(
                broker_order_id=legs.tgt_broker_order_id,
                leg="TGT",
                transaction_type=exit_side,
                order_type="LIMIT",
                product=product,
                variety="regular",
                qty_requested=qty_filled,
                price=legs.tgt_price,
            ),
        ]

        broker_ids_to_cancel: List[str] = [
            legs.sl_broker_order_id, legs.tgt_broker_order_id,
        ]

        try:
            self._om.insert_orders_atomic(trade_id, specs)
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "order_placer.limit_triple_exits_persist_failed "
                "LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED: "
                "SL/TGT placed at broker but DB persist failed; cancelling legs",
                extra={
                    "trade_id": trade_id,
                    "broker_order_ids": broker_ids_to_cancel,
                    "error": str(exc),
                },
            )
            self._cancel_broker_orders(
                broker_ids_to_cancel,
                reason=f"limit_triple_exits_persist_failed: {type(exc).__name__}",
            )
            self._fire_hard_kill_for_unprotected_position(trade_id, exc)
            return

        # OP-AR1 (atomic registration): _fill_map insert FIRST, track() SECOND
        # for each leg. Symmetric to the place() flow above.
        now = now_ist()
        try:
            with self._fill_map_lock:
                self._fill_map[legs.sl_internal_id] = _FillEntry(
                    trade_id=trade_id,
                    reservation_id=fill_entry.reservation_id,
                    symbol=fill_entry.symbol,
                    qty=qty_filled,
                    leg=_LEG_SL,
                    order_protocol="LIMIT_TRIPLE",
                    direction=fill_entry.direction,
                )
            self._order_monitor.track(
                internal_order_id=legs.sl_internal_id,
                broker_order_id=legs.sl_broker_order_id,
                symbol=fill_entry.symbol,
                side=exit_side,
                qty=qty_filled,
                expected_price=legs.sl_trigger_price,
                placed_at=now,
                leg="SL",
            )

            with self._fill_map_lock:
                self._fill_map[legs.tgt_internal_id] = _FillEntry(
                    trade_id=trade_id,
                    reservation_id=fill_entry.reservation_id,
                    symbol=fill_entry.symbol,
                    qty=qty_filled,
                    leg=_LEG_TGT,
                    order_protocol="LIMIT_TRIPLE",
                    direction=fill_entry.direction,
                )
            self._order_monitor.track(
                internal_order_id=legs.tgt_internal_id,
                broker_order_id=legs.tgt_broker_order_id,
                symbol=fill_entry.symbol,
                side=exit_side,
                qty=qty_filled,
                expected_price=legs.tgt_price,
                placed_at=now,
                leg="TGT",
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "order_placer.limit_triple_exits_track_failed "
                "LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED: "
                "SL/TGT persisted but track() failed; exits may not update DB on fill",
                extra={"trade_id": trade_id, "error": str(exc)},
            )
            # Do NOT cancel here: persistence succeeded and reconciler will
            # catch any monitor coverage gap on the next cycle. Escalate
            # since the condition is unexpected.
            self._fire_hard_kill_for_unprotected_position(trade_id, exc)
            return

        self._log.info(
            "order_placer.limit_triple_exits_placed",
            extra={
                "trade_id": trade_id,
                "symbol": fill_entry.symbol,
                "qty_filled": qty_filled,
                "sl_broker_id": legs.sl_broker_order_id,
                "sl_order_type": legs.sl_order_type,
                "tgt_broker_id": legs.tgt_broker_order_id,
                "reason": reason,
            },
        )

        # Slice 1 Part B: verify SL + TGT landed at the intended price/qty.
        # Pass the circuit-band ceiling each leg was clamped to (if any) so a
        # legitimate clamp is recognised, not false-flagged (NOCIL after-check fix).
        self._verify_exits_placed(
            trade_id=trade_id, symbol=fill_entry.symbol, protocol="LIMIT_TRIPLE",
            intended_sl=fill_entry.sl_price,
            intended_tgt=actual_tgt_price, qty_filled=qty_filled,
            sl_clamp_price=(legs.sl_trigger_price if legs.sl_clamped else None),
            tgt_clamp_price=(legs.tgt_price if legs.tgt_clamped else None),
        )

    def _place_co_tgt_exit(
        self,
        *,
        trade_id: str,
        fill_entry: "_FillEntry",
        qty_filled: int,
        avg_fill_price: float,
        reason: str,
    ) -> None:
        """
        FIX-016: Place TGT for CO_PLUS_TGT AFTER CO ENTRY fills.

        CO protocol has SL embedded in the CO bracket at the broker, so this
        only places the TGT leg. Called from _handle_entry_fill on CO fill event.

        FIX-013: TGT price is recalculated from avg_fill_price to preserve
        risk:reward ratio under entry slippage. SL price remains anchored to
        the original strategy-requested level (embedded in CO bracket).

        Failure policy:
          - qty_filled ≤ 0 → skip (nothing to protect).
          - place_deferred_exits raises BrokerError → position is live with
            NO TGT. This is a capital-protection breach. Fire kill_switch.hard_kill
            (if injected) and log CRITICAL with grep tag
            CO_TGT_FAILED_POSITION_UNPROTECTED. Do NOT re-raise:
            we are inside an event handler; the reconciler is the backstop.
          - Persist/track failures AFTER broker ack: cancel the broker order
            best-effort, hard_kill, do not re-raise.
        """
        if qty_filled <= 0:
            self._log.warning(
                "order_placer.co_tgt_exit_skipped_zero_qty",
                extra={"trade_id": trade_id, "reason": reason},
            )
            return

        # FIX-013: Recalculate TGT from actual fill price to preserve R:R.
        # SL stays anchored to original strategy level (in CO bracket).
        actual_tgt_price = calc_tgt_price(
            direction=fill_entry.direction,
            entry_price=avg_fill_price,
            sl_price=fill_entry.sl_price,
            # Slice 1: honour THIS trade's strategy R:R (fallback 2.0 + WARNING).
            rr_ratio=self._resolve_fill_rr(
                fill_entry.tgt_risk_reward, fill_entry.trade_id, fill_entry.symbol),
        )
        theoretical_tgt = fill_entry.tgt_price
        tgt_delta = actual_tgt_price - theoretical_tgt

        self._log.info(
            "order_placer.co_tgt_recalc_from_fill_price",
            extra={
                "trade_id": trade_id,
                "symbol": fill_entry.symbol,
                "avg_fill_price": avg_fill_price,
                "theoretical_tgt": theoretical_tgt,
                "actual_tgt": actual_tgt_price,
                "delta": tgt_delta,
                "sl_price": fill_entry.sl_price,
            },
        )

        try:
            legs = self._engine.place_deferred_exits(
                order_protocol="CO_PLUS_TGT",
                symbol=fill_entry.symbol,
                entry_side=fill_entry.side,
                qty=qty_filled,
                sl_price=fill_entry.sl_price,  # Not used by CO protocol, but required by signature
                tgt_price=actual_tgt_price,
                intent=fill_entry.intent,
                trade_id=trade_id,
                tag=trade_id,
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "order_placer.co_tgt_exit_failed "
                "CO_TGT_FAILED_POSITION_UNPROTECTED: "
                "CO ENTRY filled but TGT placement raised; position has no take-profit protection",
                extra={
                    "trade_id": trade_id,
                    "symbol": fill_entry.symbol,
                    "qty_filled": qty_filled,
                    "tgt_price": actual_tgt_price,
                    "intent": fill_entry.intent,
                    "reason": reason,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            self._fire_hard_kill_for_unprotected_position(trade_id, exc)
            return

        # Persist TGT row. Use product derived from intent (same as _persist_entry_orders).
        if self._product_resolver is None:
            self._log.critical(
                "order_placer.co_tgt_exit_no_product_resolver "
                "CO_TGT_FAILED_POSITION_UNPROTECTED",
                extra={"trade_id": trade_id},
            )
            self._fire_hard_kill_for_unprotected_position(
                trade_id,
                RuntimeError("product_resolver missing; cannot persist TGT leg"),
            )
            return

        product = self._product_resolver.resolve(fill_entry.intent)
        exit_side = "SELL" if fill_entry.side == "BUY" else "BUY"
        specs: List[OrderInsertSpec] = [
            OrderInsertSpec(
                broker_order_id=legs.tgt_broker_order_id,
                leg="TGT",
                transaction_type=exit_side,
                order_type="LIMIT",
                product=product,
                variety="regular",
                qty_requested=qty_filled,
                price=legs.tgt_price,
            ),
        ]

        broker_ids_to_cancel: List[str] = [legs.tgt_broker_order_id]

        try:
            self._om.insert_orders_atomic(trade_id, specs)
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "order_placer.co_tgt_exit_persist_failed "
                "CO_TGT_FAILED_POSITION_UNPROTECTED: "
                "TGT placed at broker but DB persist failed; cancelling leg",
                extra={
                    "trade_id": trade_id,
                    "broker_order_ids": broker_ids_to_cancel,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            self._cancel_broker_orders(broker_ids_to_cancel)
            self._fire_hard_kill_for_unprotected_position(trade_id, exc)
            return

        # Track TGT with order_monitor for fill detection.
        now = now_ist()
        exit_side = "SELL" if fill_entry.side == "BUY" else "BUY"

        try:
            # FIX-016: TGT tracking for CO_PLUS_TGT (internal_id from place_exits result)
            with self._fill_map_lock:
                self._fill_map[legs.tgt_internal_id] = _FillEntry(
                    trade_id=trade_id,
                    reservation_id=fill_entry.reservation_id,
                    symbol=fill_entry.symbol,
                    qty=qty_filled,
                    leg=_LEG_TGT,
                    order_protocol="CO_PLUS_TGT",
                    direction=fill_entry.direction,
                )
            self._order_monitor.track(
                internal_order_id=legs.tgt_internal_id,
                broker_order_id=legs.tgt_broker_order_id,
                symbol=fill_entry.symbol,
                side=exit_side,
                qty=qty_filled,
                expected_price=legs.tgt_price,
                placed_at=now,
                leg="TGT",
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "order_placer.co_tgt_exit_track_failed "
                "CO_TGT_FAILED_POSITION_UNPROTECTED: "
                "TGT persisted but track() failed; exit may not update DB on fill",
                extra={"trade_id": trade_id, "error": str(exc)},
            )
            self._fire_hard_kill_for_unprotected_position(trade_id, exc)
            return

        self._log.info(
            "order_placer.co_tgt_exit_placed",
            extra={
                "trade_id": trade_id,
                "symbol": fill_entry.symbol,
                "qty_filled": qty_filled,
                "tgt_broker_id": legs.tgt_broker_order_id,
                "tgt_price": legs.tgt_price,
                "reason": reason,
            },
        )

        # Slice 1 Part B: verify the TGT landed at the intended price/qty. SL is
        # broker-managed inside the CO bracket (intended_sl=None -> skip SL check).
        self._verify_exits_placed(
            trade_id=trade_id, symbol=fill_entry.symbol, protocol="CO_PLUS_TGT",
            intended_sl=None,
            intended_tgt=actual_tgt_price, qty_filled=qty_filled,
        )

    # ── FIX-061: LTP retry helpers ────────────────────────────────────────────

    def _is_ltp_validation_error(self, exc: Exception) -> bool:
        """
        FIX-061: Detect LTP validation errors from broker.

        Returns True if exception is error code 16418 or message contains
        "Trigger price" or "LTP cannot be validated".
        """
        # SL-unplaceable is NOT an LTP-validation reject: it must route to the
        # emergency-close + hard_kill path (position cannot be stopped), never
        # the LTP exit-retry queue. Guard explicitly even though the keyword
        # checks below would not match it.
        if isinstance(exc, SLUnplaceableError):
            return False

        # Check error code 16418
        if isinstance(exc, (BrokerError, OrderRejectedError)):
            error_code = exc.context.get("kite_status_code")
            if error_code == 16418:
                return True

            # Check message content
            rejection_reason = exc.context.get("rejection_reason", "")
            error_msg = str(exc).lower()
            trigger_keywords = ["trigger price", "ltp cannot be validated"]
            if any(keyword in error_msg for keyword in trigger_keywords):
                return True
            if any(keyword in rejection_reason.lower() for keyword in trigger_keywords):
                return True

        return False

    def _add_to_exit_retry(
        self,
        *,
        trade_id: str,
        fill_entry: "_FillEntry",
        qty_filled: int,
        avg_fill_price: float,
        reason: str,
    ) -> None:
        """
        FIX-061: Add exit order to retry queue and subscribe to LiveFeed.

        Called when SL/SL-M placement fails with LTP validation error.
        """
        symbol = fill_entry.symbol

        self._log.warning(
            "order_placer.exit_ltp_validation_error_retry_queued",
            extra={
                "trade_id": trade_id,
                "symbol": symbol,
                "retry_reason": "AWAITING_FIRST_LTP",
            },
        )

        with self._pending_exit_retry_lock:
            self._pending_exit_retry[trade_id] = _ExitRetryParams(
                trade_id=trade_id,
                fill_entry=fill_entry,
                qty_filled=qty_filled,
                avg_fill_price=avg_fill_price,
                reason=reason,
                retry_count=0,
            )

        # Subscribe to LiveFeed if available and not already subscribed
        if self._live_feed is not None and self._instrument_cache is not None:
            try:
                # Get instrument token from cache
                row = self._instrument_cache.get_by_symbol(symbol)
                if row is not None:
                    instrument_token = row.instrument_token
                    self._live_feed.subscribe([instrument_token])

                    # Register callback if not already done
                    if not self._ltp_callback_registered:
                        self._live_feed.register_callback(self._on_ltp_tick_for_retry)
                        self._ltp_callback_registered = True

                    self._log.info(
                        "order_placer.exit_retry_subscribed_to_ltp",
                        extra={
                            "trade_id": trade_id,
                            "symbol": symbol,
                            "instrument_token": instrument_token,
                        },
                    )
                else:
                    self._log.error(
                        "order_placer.exit_retry_symbol_not_in_cache",
                        extra={"trade_id": trade_id, "symbol": symbol},
                    )
            except Exception as exc:
                log_exception(self._log, exc)
                self._log.error(
                    "order_placer.exit_retry_subscribe_failed",
                    extra={"trade_id": trade_id, "symbol": symbol},
                )

    def _on_ltp_tick_for_retry(self, ticks: List[dict]) -> None:
        """
        FIX-061: Callback for LiveFeed ticks to retry exit placement.

        Called when any tick arrives. Checks if we have pending retries for
        the symbol and retries placement if LTP is valid (> 0).
        """
        if not ticks:
            return

        # FIX-169 F26: collect work under lock, execute retries outside lock
        # (replaces unsafe release/acquire pattern inside with-block)
        collected: list = []
        with self._pending_exit_retry_lock:
            if not self._pending_exit_retry:
                return

            symbol_to_token: Dict[int, str] = {}
            if self._instrument_cache is not None:
                for trade_id, params in list(self._pending_exit_retry.items()):
                    row = self._instrument_cache.get_by_symbol(params.fill_entry.symbol)
                    if row is not None:
                        symbol_to_token[row.instrument_token] = params.fill_entry.symbol

            for tick in ticks:
                instrument_token = tick.get("instrument_token")
                ltp = tick.get("last_price", 0)

                if instrument_token not in symbol_to_token or ltp <= 0:
                    continue

                symbol = symbol_to_token[instrument_token]

                trades_to_retry = [
                    trade_id
                    for trade_id, params in list(self._pending_exit_retry.items())
                    if params.fill_entry.symbol == symbol
                ]

                for trade_id in trades_to_retry:
                    params = self._pending_exit_retry.get(trade_id)
                    if params is None:
                        continue

                    self._log.info(
                        "order_placer.exit_retry_first_ltp_received",
                        extra={
                            "trade_id": trade_id,
                            "symbol": symbol,
                            "ltp": ltp,
                            "retry_count": params.retry_count,
                        },
                    )

                    del self._pending_exit_retry[trade_id]
                    collected.append(params)

        for params in collected:
            self._retry_limit_triple_exits(params)

    def _retry_limit_triple_exits(self, params: _ExitRetryParams) -> None:
        """
        FIX-061: Retry placing SL+TGT exits after receiving first valid LTP.

        On success: logs info and returns.
        On failure: increments retry_count.
        After MAX_RETRIES: triggers soft_kill and logs CRITICAL.
        """
        params.retry_count += 1
        trade_id = params.trade_id
        symbol = params.fill_entry.symbol

        self._log.info(
            "order_placer.exit_retry_attempt",
            extra={
                "trade_id": trade_id,
                "symbol": symbol,
                "attempt": params.retry_count,
                "max_retries": params.MAX_RETRIES,
            },
        )

        # Retry placement directly (bypass _place_limit_triple_exits to avoid recursion)
        fill_entry = params.fill_entry
        qty_filled = params.qty_filled
        avg_fill_price = params.avg_fill_price

        # Recalculate TGT price from actual fill (same logic as _place_limit_triple_exits)
        from orders.price_math import calc_tgt_price
        actual_tgt_price = calc_tgt_price(
            direction=fill_entry.direction,
            entry_price=avg_fill_price,
            sl_price=fill_entry.sl_price,
            # Slice 1: honour THIS trade's strategy R:R (fallback 2.0 + WARNING).
            rr_ratio=self._resolve_fill_rr(
                fill_entry.tgt_risk_reward, fill_entry.trade_id, fill_entry.symbol),
        )

        try:
            legs = self._engine.place_deferred_exits(
                order_protocol="LIMIT_TRIPLE",
                symbol=fill_entry.symbol,
                entry_side=fill_entry.side,
                qty=qty_filled,
                sl_price=fill_entry.sl_price,
                tgt_price=actual_tgt_price,
                intent=fill_entry.intent,
                trade_id=trade_id,
                tag=trade_id,
                entry_fill=avg_fill_price,  # NOCIL placeability gate reference
            )
        except Exception as exc:
            # Placement failed - handle errors in the except block
            # Still failing after retry
            if self._is_ltp_validation_error(exc):
                # Still LTP error - check if we should retry again
                if params.retry_count < params.MAX_RETRIES:
                    # Re-add to pending queue for next tick
                    with self._pending_exit_retry_lock:
                        self._pending_exit_retry[trade_id] = params

                    self._log.warning(
                        "order_placer.exit_retry_still_failing_ltp",
                        extra={
                            "trade_id": trade_id,
                            "symbol": symbol,
                            "retry_count": params.retry_count,
                            "max_retries": params.MAX_RETRIES,
                        },
                    )
                    return
                else:
                    # Exhausted retries — emergency market exit + hard_kill
                    log_exception(self._log, exc)
                    self._log.critical(
                        "order_placer.exit_retry_exhausted "
                        "LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED",
                        extra={
                            "trade_id": trade_id,
                            "symbol": symbol,
                            "retry_count": params.retry_count,
                            "error": str(exc),
                        },
                    )

                    # FIX-148 (GAP 1): Emergency market exit
                    self._emergency_market_exit(
                        trade_id, fill_entry, qty_filled,
                        reason=f"sl_retries_exhausted_after_{params.MAX_RETRIES}",
                    )
                    self._fire_hard_kill_for_unprotected_position(trade_id, exc)
                    return

            # Non-LTP error on retry — emergency market exit + hard_kill
            log_exception(self._log, exc)
            self._log.critical(
                "order_placer.exit_retry_non_ltp_error "
                "LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED",
                extra={
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "retry_count": params.retry_count,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            # FIX-148 (GAP 1): Emergency market exit
            self._emergency_market_exit(
                trade_id, fill_entry, qty_filled,
                reason=f"sl_retry_non_ltp_error: {type(exc).__name__}",
            )
            self._fire_hard_kill_for_unprotected_position(trade_id, exc)
            return

        # SLICE2.5-P1: DELIVERY (CNC) → finalize the OCO GTT, skip day-leg persistence
        # (same centralized treatment as the main fill path).
        if getattr(legs, "is_gtt", False):
            self._finalize_cnc_gtt(trade_id, fill_entry, qty_filled, legs, "exit_retry")
            return

        # FIX-165d: Success path — dedented to be reachable after try/except
        if self._product_resolver is None:
            self._log.critical(
                "order_placer.exit_retry_no_product_resolver",
                extra={"trade_id": trade_id},
            )
            return

        product = self._product_resolver.resolve(fill_entry.intent)
        exit_side = "SELL" if fill_entry.side == "BUY" else "BUY"
        specs = [
            OrderInsertSpec(
                broker_order_id=legs.sl_broker_order_id,
                leg="SL",
                transaction_type=exit_side,
                order_type=legs.sl_order_type,
                product=product,
                variety="regular",
                qty_requested=qty_filled,
                price=legs.sl_price,
                trigger_price=legs.sl_trigger_price,
            ),
            OrderInsertSpec(
                broker_order_id=legs.tgt_broker_order_id,
                leg="TGT",
                transaction_type=exit_side,
                order_type="LIMIT",
                product=product,
                variety="regular",
                qty_requested=qty_filled,
                price=legs.tgt_price,
            ),
        ]

        try:
            self._om.insert_orders_atomic(trade_id, specs)
        except Exception as persist_exc:
            log_exception(self._log, persist_exc)
            self._log.critical(
                "order_placer.exit_retry_persist_failed",
                extra={"trade_id": trade_id},
            )
            self._cancel_broker_orders(
                [legs.sl_broker_order_id, legs.tgt_broker_order_id],
                reason=f"exit_retry_persist_failed: {type(persist_exc).__name__}",
            )
            self._fire_hard_kill_for_unprotected_position(trade_id, persist_exc)
            return

        now = now_ist()
        try:
            with self._fill_map_lock:
                self._fill_map[legs.sl_internal_id] = _FillEntry(
                    trade_id=trade_id,
                    reservation_id=fill_entry.reservation_id,
                    symbol=fill_entry.symbol,
                    qty=qty_filled,
                    leg=_LEG_SL,
                    order_protocol="LIMIT_TRIPLE",
                    direction=fill_entry.direction,
                )
            self._order_monitor.track(
                internal_order_id=legs.sl_internal_id,
                broker_order_id=legs.sl_broker_order_id,
                symbol=fill_entry.symbol,
                side=exit_side,
                qty=qty_filled,
                expected_price=legs.sl_trigger_price,
                placed_at=now,
                leg="SL",
            )

            with self._fill_map_lock:
                self._fill_map[legs.tgt_internal_id] = _FillEntry(
                    trade_id=trade_id,
                    reservation_id=fill_entry.reservation_id,
                    symbol=fill_entry.symbol,
                    qty=qty_filled,
                    leg=_LEG_TGT,
                    order_protocol="LIMIT_TRIPLE",
                    direction=fill_entry.direction,
                )
            self._order_monitor.track(
                internal_order_id=legs.tgt_internal_id,
                broker_order_id=legs.tgt_broker_order_id,
                symbol=fill_entry.symbol,
                side=exit_side,
                qty=qty_filled,
                expected_price=legs.tgt_price,
                placed_at=now,
                leg="TGT",
            )

            self._log.info(
                "order_placer.exit_retry_success",
                extra={
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "retry_count": params.retry_count,
                },
            )

            # Slice 1 Part B: verify the retried SL + TGT landed correctly.
            # (Pass the per-leg circuit-band ceiling so a legit clamp on the
            # re-placed exits is recognised. NOTE: the prior `legs=legs` kwarg was
            # a latent bug — _verify_exits_placed has no `legs` param — masked
            # because this retry path was itself dead until 23-Jun; fixed here.)
            self._verify_exits_placed(
                trade_id=trade_id, symbol=fill_entry.symbol, protocol="LIMIT_TRIPLE",
                intended_sl=fill_entry.sl_price,
                intended_tgt=actual_tgt_price, qty_filled=qty_filled,
                sl_clamp_price=(legs.sl_trigger_price if legs.sl_clamped else None),
                tgt_clamp_price=(legs.tgt_price if legs.tgt_clamped else None),
            )

        except Exception as track_exc:
            log_exception(self._log, track_exc)
            self._log.critical(
                "order_placer.exit_retry_track_failed",
                extra={"trade_id": trade_id},
            )

    # ────────────────────────────────────────────────────────────────────────────

    def _fire_hard_kill_for_unprotected_position(
        self, trade_id: str, exc: Exception,
    ) -> None:
        """
        Escalate: a position is live with broken exit protection.
        This is exactly the capital-safety condition kill_switch.hard_kill exists
        for. Best-effort — any failure fires a CRITICAL log and returns.
        """
        if self._kill_switch is None:
            self._log.critical(
                "order_placer.hard_kill_not_configured "
                "LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED",
                extra={"trade_id": trade_id, "error": str(exc)},
            )
            return
        try:
            self._kill_switch.hard_kill(
                reason=(
                    f"LIMIT_TRIPLE exits failed after ENTRY filled: "
                    f"{type(exc).__name__}: {exc}"
                ),
                triggered_by="order_placer._place_limit_triple_exits",
            )
        except Exception as kse:
            log_exception(self._log, kse)
            self._log.critical(
                "order_placer.hard_kill_failed",
                extra={"trade_id": trade_id, "kill_error": str(kse)},
            )

    def _mark_trade_exiting(self, trade_id: str) -> None:
        """FIX-190 (Bug A): mark a trade EXITING (best-effort) so a concurrent
        flatten path does not re-select and double-sell it."""
        try:
            with self._om._store.transaction() as cur:
                cur.execute(
                    "UPDATE trades SET status = ?, updated_at = ? WHERE trade_id = ?",
                    ("EXITING", now_ist().isoformat(), trade_id),
                )
        except Exception as exc:
            self._log.warning(
                "order_placer: mark EXITING failed for %s: %s", trade_id, exc
            )

    def _cancel_trade_resting_exits(self, trade_id: str) -> None:
        """FIX-190 (Bug E): cancel a trade's resting SL/TGT at the broker before an
        emergency flatten so they don't survive as orphans. Best-effort."""
        try:
            rows = self._om._store.fetch_all(
                "SELECT broker_order_id FROM orders WHERE trade_id = ? "
                "AND leg IN ('SL','TGT') "
                "AND status NOT IN ('CANCELLED','FAILED','EXPIRED','COMPLETE')",
                (trade_id,),
            )
            # Defensive: best-effort in a forced-exit path — a missing column or
            # odd payload (e.g. a test mock store) must never crash. Build the id
            # list inside the try so iterating a non-iterable result is caught.
            ids = [
                r["broker_order_id"] for r in (rows or [])
                if r["broker_order_id"]
            ]
        except Exception as exc:
            self._log.warning(
                "order_placer: query resting exits failed for %s: %s", trade_id, exc
            )
            return
        if ids:
            self._cancel_broker_orders(
                ids, reason=f"emergency_exit_cancel_resting:{trade_id}",
            )

    def _emergency_market_exit(
        self, trade_id: str, fill_entry: "_FillEntry", qty: int, reason: str,
    ) -> bool:
        """
        FIX-148 (GAP 1): Best-effort emergency MARKET exit when SL placement
        fails permanently. Returns True if order was successfully placed.

        Does NOT handle capital release — that happens when the exit fills
        via the normal _handle_exit_fill path.
        """
        symbol = fill_entry.symbol
        fallback_side = "SELL" if fill_entry.side == "BUY" else "BUY"

        # FIX-190 (Bug E): cancel this trade's resting SL/TGT FIRST so they don't
        # survive as orphans that later re-fire into a naked position.
        self._cancel_trade_resting_exits(trade_id)

        # FIX-190 (Bug A): reverse-aware exit. If the broker confirms we are
        # already flat (a concurrent HARD_KILL flatten beat us, or the position
        # never opened) do NOT fire another order — that is the THELEELA oversell
        # (BUY 1 -> SELL 1 -> SELL 1 -> naked short -1). A genuine short closes
        # with BUY. On a broker error we fall back to the intended exit.
        side, ex_qty = determine_close_direction(
            self._adapter, symbol, fallback_side, qty,
        )
        if side is None or ex_qty <= 0:
            self._log.critical(
                "order_placer.emergency_exit_skipped_already_flat",
                extra={"trade_id": trade_id, "symbol": symbol, "reason": reason},
            )
            self._mark_trade_exiting(trade_id)
            return True
        qty = ex_qty

        # FIX-181: marketable LIMIT (LTP ± buffer) rather than MARKET so the
        # forced exit still fills but caps worst-case slippage. Falls back to
        # MARKET only if we cannot get a positive LTP (a LIMIT needs a price).
        ltp = self._fetch_ltp(symbol)
        tick = self._tick_for(symbol)
        if ltp and ltp > 0:
            exit_price = marketable_limit_price(
                side, ltp, self._emergency_exit_buffer_pct, tick,
            )
            exit_order_type = "LIMIT"
        else:
            exit_price = 0.0
            exit_order_type = "MARKET"

        self._log.critical(
            "order_placer.emergency_exit_attempt SL_PLACEMENT_FAILED",
            extra={
                "trade_id": trade_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "order_type": exit_order_type,
                "price": exit_price,
                "ltp": ltp,
                "reason": reason,
            },
        )

        try:
            # Bug B (P0 2026-06-15): FullEntryEngine has no `adapter` attribute;
            # OrderPlacer holds the broker adapter directly as self._adapter.
            placed = self._adapter.place_order(
                symbol=symbol,
                side=side,
                qty=qty,
                price=exit_price,
                order_type=exit_order_type,
                intent=fill_entry.intent,
                tag=truncate_tag_for_broker(f"EXIT_{trade_id}"),
            )
        except Exception as exc:
            self._log.critical(
                "order_placer.emergency_market_exit_failed",
                extra={
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "error": str(exc),
                },
            )
            return False

        self._log.critical(
            "order_placer.emergency_market_exit_placed",
            extra={
                "trade_id": trade_id,
                "symbol": symbol,
                "broker_order_id": placed.broker_order_id,
            },
        )

        # FIX-190 (Bug A): mark EXITING so a concurrent HARD_KILL flatten (which
        # selects OPEN/PARTIAL/PENDING_FILL) does not re-select and double-sell.
        self._mark_trade_exiting(trade_id)

        # Persist + track the emergency exit order
        try:
            product = self._product_resolver.resolve(fill_entry.intent) if self._product_resolver else "MIS"
            self._om.insert_order(
                trade_id=trade_id,
                broker_order_id=placed.broker_order_id,
                leg="EOD",
                transaction_type=side,
                order_type=exit_order_type,
                product=product,
                variety="regular",
                qty_requested=qty,
                price=exit_price,
            )
            with self._fill_map_lock:
                self._fill_map[placed.internal_order_id] = _FillEntry(
                    trade_id=trade_id,
                    reservation_id=fill_entry.reservation_id,
                    symbol=symbol,
                    qty=qty,
                    leg=_LEG_EOD,  # FIX-165h: was _LEG_SL; must match DB + monitor
                    order_protocol=fill_entry.order_protocol,
                    direction=fill_entry.direction,
                )
            self._order_monitor.track(
                internal_order_id=placed.internal_order_id,
                broker_order_id=placed.broker_order_id,
                symbol=symbol,
                side=side,
                qty=qty,
                expected_price=exit_price,
                placed_at=now_ist(),
                leg="EOD",
            )
        except Exception as exc:
            self._log.error(
                "order_placer.emergency_market_exit_track_failed: %s", exc,
            )

        # Send Telegram alert
        if self._notifier is not None:
            try:
                self._notifier.send(
                    severity="CRITICAL",
                    title=f"[{self._mode}] EMERGENCY EXIT -- {symbol}",
                    body=(
                        f"SL placement failed permanently\n"
                        f"Trade: {trade_id}\n"
                        f"Emergency {exit_order_type} {side} {qty} shares placed"
                        f"{f' @ {exit_price}' if exit_price else ''}\n"
                        f"Reason: {reason}"
                    ),
                    source_module="order_placer",
                )
            except Exception:
                pass

        return True

    def _tick_for(self, symbol: str) -> float:
        """FIX-181: instrument tick for `symbol`; falls back to DEFAULT_TICK."""
        if self._instrument_cache is None:
            return DEFAULT_TICK
        try:
            tick = self._instrument_cache.tick_size(symbol)
            return tick if tick and tick > 0 else DEFAULT_TICK
        except Exception:
            return DEFAULT_TICK

    def _fetch_ltp(self, symbol: str) -> Optional[float]:
        """
        Bug 6 (FIX-180): fetch current LTP via the broker adapter.

        The slippage guard and price-drift top-up previously called
        self._live_feed.quote(symbol), but LiveFeedManager has no quote()
        method (AttributeError silently disabled both guards on every order —
        first-live-day incident). Route through adapter.get_quote_raw() instead,
        the same source _check_liquidity uses (FIX-166 F08 pattern).

        Best-effort: returns None on any error or if no adapter/positive LTP.
        """
        if self._adapter is None:
            return None
        try:
            raw_quote = self._adapter.get_quote_raw([f"NSE:{symbol}"])
            if not raw_quote:
                return None
            q = raw_quote.get(f"NSE:{symbol}")
            if not q:
                return None
            ltp = float(q.get("last_price", 0) or 0)
            return ltp if ltp > 0 else None
        except Exception as exc:
            self._log.debug(
                "order_placer.fetch_ltp_failed",
                extra={"symbol": symbol, "error": str(exc)},
            )
            return None

    def _check_liquidity(
        self, symbol: str, side: str, trade_id: str, signal_id: str,
    ) -> tuple[bool, str]:
        """
        FIX-134 Item 38: Check bid-ask spread and depth before entry.
        Returns (ok, reason). Best-effort: returns (True, "") on any error.
        """
        max_spread_pct = self._liquidity_max_spread_pct
        min_depth_qty = self._liquidity_min_depth_qty
        if not self._liquidity_check_enabled:
            return True, ""
        try:
            raw_quote = self._adapter.get_quote_raw([f"NSE:{symbol}"])
            if not raw_quote:
                return True, ""
            key = f"NSE:{symbol}"
            if key not in raw_quote:
                return True, ""
            q = raw_quote[key]
            depth = q.get("depth", {})
            buy_depth = depth.get("buy", [])
            sell_depth = depth.get("sell", [])
            ltp = float(q.get("last_price", 0) or 0)
            if ltp <= 0:
                return True, ""

            best_bid = float(buy_depth[0].get("price", 0)) if buy_depth else 0
            best_ask = float(sell_depth[0].get("price", 0)) if sell_depth else 0
            if best_bid <= 0 or best_ask <= 0:
                return True, ""

            spread_pct = (best_ask - best_bid) / ltp * 100
            if spread_pct > max_spread_pct:
                reason = f"spread {spread_pct:.2f}% > max {max_spread_pct}%"
                self._log.warning(
                    "order_placer.liquidity_check_failed",
                    extra={
                        "symbol": symbol, "spread_pct": spread_pct,
                        "max_spread_pct": max_spread_pct,
                        "trade_id": trade_id,
                    },
                )
                if self._notifier is not None:
                    try:
                        self._notifier.send(
                            severity="WARNING",
                            title=f"[{self._mode}] LOW LIQUIDITY -- {symbol}",
                            body=f"Spread {spread_pct:.2f}% > limit {max_spread_pct}%",
                            source_module="order_placer",
                        )
                    except Exception:
                        pass
                return False, reason

            if side == "BUY":
                top_depth_qty = int(sell_depth[0].get("quantity", 0)) if sell_depth else 0
            else:
                top_depth_qty = int(buy_depth[0].get("quantity", 0)) if buy_depth else 0
            if top_depth_qty < min_depth_qty:
                reason = f"depth {top_depth_qty} < min {min_depth_qty}"
                self._log.warning(
                    "order_placer.liquidity_depth_check_failed",
                    extra={
                        "symbol": symbol, "depth_qty": top_depth_qty,
                        "min_depth_qty": min_depth_qty,
                        "trade_id": trade_id,
                    },
                )
                if self._notifier is not None:
                    try:
                        self._notifier.send(
                            severity="WARNING",
                            title=f"[{self._mode}] LOW LIQUIDITY -- {symbol}",
                            body=f"Depth {top_depth_qty} < min {min_depth_qty}",
                            source_module="order_placer",
                        )
                    except Exception:
                        pass
                return False, reason

            return True, ""
        except Exception as exc:
            self._log.debug(
                "order_placer.liquidity_check_error: %s", exc
            )
            return True, ""

    def _resolve_fill_rr(
        self, candidate_rr: Optional[float], trade_id: str, symbol: str
    ) -> float:
        """Slice 1: the R:R to use for a fill-time / TGT-retry recalc.

        Prefer the originating strategy's ratio, frozen at placement (carried on
        the fill entry as tgt_risk_reward, or persisted on
        trades.tgt_risk_reward_applied). Fall back to the constructor default
        (self._rr_ratio, normally 2.0) for trades that have no stored ratio
        (reconciler-recovered / pre-v35), logging a WARNING so the fallback is
        visible. This is what makes the broker TGT honour each strategy's
        configured R:R instead of a single hardcoded ratio.
        """
        rr = candidate_rr or 0.0
        if rr and rr > 0:
            return float(rr)
        self._log.warning(
            "order_placer.rr_fallback_to_default",
            extra={
                "trade_id": trade_id,
                "symbol": symbol,
                "fallback_rr": self._rr_ratio,
                "reason": "no strategy tgt_risk_reward (recovered / pre-v35 trade)",
            },
        )
        return self._rr_ratio

    # ── Slice 1 Part B: SL/TGT placement after-check ──────────────────────────

    @staticmethod
    def _exit_price_mismatch(placed: float, intended: float) -> bool:
        """True if a placed exit price differs materially from the intended one.

        Tolerance = max(₹0.05, 0.2% of intended): absorbs tick rounding while
        still catching a real clamp (e.g. a circuit-band move, which shifts the
        price by far more than that). A missing/zero placed price is a mismatch.
        """
        if not placed or not intended:
            return True
        eps = max(0.05, abs(intended) * 0.002)
        return abs(placed - intended) > eps

    def _explained_by_clamp(
        self, placed: Optional[float], clamp_price: Optional[float]
    ) -> bool:
        """True iff a price mismatch vs the intended exit is FULLY explained by a
        legitimate circuit-band clamp: the leg WAS clamped (``clamp_price`` is the
        band ceiling it was clamped to, else None) AND the placed price equals that
        ceiling within the same tick tolerance.

        The independent ``clamp_price`` (the clamp's computed band output, not the
        read-back order row) is the key: a placed price that matches NEITHER the
        intended NOR the band is a REAL mismatch — so a genuine bug on a
        near-circuit stock still flags. This is NOT blind 'a clamp happened ->
        suppress'.
        """
        if clamp_price is None or not placed:
            return False
        return not self._exit_price_mismatch(placed, clamp_price)

    def _verify_exits_placed(
        self,
        *,
        trade_id: str,
        symbol: str,
        protocol: str,
        intended_sl: Optional[float],
        intended_tgt: float,
        qty_filled: int,
        sl_clamp_price: Optional[float] = None,
        tgt_clamp_price: Optional[float] = None,
    ) -> None:
        """Slice 1 Part B: verify the just-placed SL/TGT exits landed at the
        intended price + qty. ALERT-ONLY — never cancels or re-places (a bigger
        decision deferred). Records the verdict on the trade
        (exits_verified / exits_verify_detail — READ-BACK, unlike the write-only
        v34 sizing columns).

        Ground truth = the persisted exit order rows (which mirror what was sent
        to the broker). Parity: both paper and live persist the same rows, so
        there is no mode branch. LIMIT_TRIPLE persists SL + TGT; CO_PLUS_TGT
        persists TGT only (its SL lives inside the broker CO bracket).

        Circuit-clamp awareness (24-Jun): when a leg was legitimately clamped into
        the circuit band (``sl_clamp_price`` / ``tgt_clamp_price`` = the band
        ceiling it was clamped to), the placed price differs from the pre-clamp
        intended price by design. That is NOT a mismatch: it is recorded as a note
        and the leg verifies OK. A difference NOT explained by the band ceiling is
        still a real mismatch (see ``_explained_by_clamp``).

        Severity: a missing/wrong SL is a protection breach -> CRITICAL; a
        missing/wrong TGT with the SL intact -> WARN (the position is still
        protected, it just won't auto-target). A clamp-explained difference is
        neither — verified=1, no alert.
        """
        try:
            orders = self._om.get_orders_for_trade(trade_id)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "order_placer.exits_verify_read_failed",
                extra={"trade_id": trade_id, "error": str(exc)},
            )
            return

        def _live(leg: str) -> list:
            return [
                o for o in orders
                if o.get("leg") == leg
                and o.get("status") not in _TERMINAL_ORDER_STATUSES
            ]

        problems: List[str] = []
        notes: List[str] = []          # legitimate-but-noteworthy (e.g. circuit clamp)
        sl_ok = True
        placed_sl: Optional[float] = None
        placed_tgt: Optional[float] = None

        # SL: LIMIT_TRIPLE places a standalone SL order. CO_PLUS_TGT carries the
        # SL inside the broker CO bracket (intended_sl passed as None for CO).
        if protocol == "LIMIT_TRIPLE" and intended_sl is not None:
            sl_rows = _live("SL")
            if not sl_rows:
                problems.append("SL MISSING (no live SL order)")
                sl_ok = False
            else:
                if len(sl_rows) > 1:
                    # LAYER 4 (RAMCOIND fix, 25-Jun): >1 live SL = a same-path double
                    # place. The cross-path G5b-race duplicate (placed ~1.3s later by
                    # the reconciler) is caught by the reconciler's continuous one-SL
                    # invariant (Layer 2); this is the cheap placement-time guard.
                    problems.append(f"DUPLICATE_SL ({len(sl_rows)} live SL orders)")
                    sl_ok = False
                o = sl_rows[0]
                placed_sl = float(o.get("trigger_price") or o.get("price") or 0.0)
                if self._exit_price_mismatch(placed_sl, intended_sl):
                    if self._explained_by_clamp(placed_sl, sl_clamp_price):
                        notes.append(
                            f"SL clamped to circuit band {placed_sl:.2f} "
                            f"(intended {intended_sl:.2f})"
                        )
                    else:
                        problems.append(f"SL trigger {placed_sl:.2f} != intended {intended_sl:.2f}")
                        sl_ok = False
                if int(o.get("qty_requested") or 0) != int(qty_filled):
                    problems.append(f"SL qty {o.get('qty_requested')} != filled {qty_filled}")
                    sl_ok = False

        # TGT: both protocols place a standalone LIMIT TGT.
        tgt_ok = True
        tgt_rows = _live("TGT")
        if not tgt_rows:
            problems.append("TGT MISSING (no live TGT order)")
            tgt_ok = False
        else:
            if len(tgt_rows) > 1:
                # LAYER 4 (RAMCOIND fix, 25-Jun): >1 live TGT = a same-path double place.
                problems.append(f"DUPLICATE_TGT ({len(tgt_rows)} live TGT orders)")
                tgt_ok = False
            o = tgt_rows[0]
            placed_tgt = float(o.get("price") or 0.0)
            if self._exit_price_mismatch(placed_tgt, intended_tgt):
                if self._explained_by_clamp(placed_tgt, tgt_clamp_price):
                    notes.append(
                        f"TGT clamped to circuit band {placed_tgt:.2f} "
                        f"(intended {intended_tgt:.2f})"
                    )
                else:
                    problems.append(f"TGT {placed_tgt:.2f} != intended {intended_tgt:.2f}")
                    tgt_ok = False
            if int(o.get("qty_requested") or 0) != int(qty_filled):
                problems.append(f"TGT qty {o.get('qty_requested')} != filled {qty_filled}")
                tgt_ok = False

        verified = 1 if not problems else 0
        if problems:
            detail = "; ".join(problems)
        elif notes:
            detail = "; ".join(notes)   # verified=1, but record the legit clamp
        else:
            detail = "ok"

        # Always log intended-vs-actual for both legs (audit trail).
        self._log.info(
            "order_placer.exits_verify",
            extra={
                "trade_id": trade_id, "symbol": symbol, "protocol": protocol,
                "verified": verified, "detail": detail, "qty_filled": qty_filled,
                "intended_sl": intended_sl, "placed_sl": placed_sl,
                "intended_tgt": intended_tgt, "placed_tgt": placed_tgt,
                "sl_clamp_price": sl_clamp_price, "tgt_clamp_price": tgt_clamp_price,
            },
        )

        # Record the verdict (best-effort; never break the fill path).
        try:
            self._om.record_exits_verification(trade_id, verified, detail)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "order_placer.exits_verify_record_failed",
                extra={"trade_id": trade_id, "error": str(exc)},
            )

        if verified:
            return

        # Mismatch -> alert (alert-only this slice; no auto-cancel/re-place).
        severity = "CRITICAL" if not sl_ok else "WARNING"
        if not sl_ok:
            self._log.critical(
                "order_placer.exits_verify_mismatch EXITS_AFTERCHECK_SL_MISMATCH",
                extra={"trade_id": trade_id, "symbol": symbol, "detail": detail},
            )
        else:
            self._log.warning(
                "order_placer.exits_verify_mismatch",
                extra={"trade_id": trade_id, "symbol": symbol, "detail": detail},
            )
        if self._notifier is not None:
            try:
                self._notifier.send(
                    severity=severity,
                    title=f"[{self._mode}] EXIT CHECK — {symbol}",
                    body=(
                        f"SL/TGT after-check FAILED ({protocol}) for trade {trade_id}:\n"
                        f"{detail}"
                    ),
                    source_module="order_placer",
                )
            except Exception as exc:  # noqa: BLE001
                self._log.error(
                    "order_placer.exits_verify_alert_failed",
                    extra={"trade_id": trade_id, "error": str(exc)},
                )

    def _compute_tgt(self, side: str, entry_price: float, sl_price: float) -> float:
        """OP3: compute tgt_price using R:R ratio (delegates to price_math, FIX-004)."""
        return calc_tgt_price(side, entry_price, sl_price, self._rr_ratio)

    def _handle_placement_failure(
        self,
        trade_id: str,
        reservation_id: str,
        signal_id: str,
        exc: Exception,
        broker_order_ids: Iterable[str] = (),
        final_status: str = "FAILED",  # FIX-072: allow "REJECTED" for margin failures
        symbol: str = "",              # FIX-129: for Telegram alert body
        suppress_alert: bool = False,  # FIX-129: True when caller already sent its own alert
    ) -> None:
        """
        OP7 + OP-BL8c: cancel any live broker orders, mark trade as final_status
        (default FAILED), release capital reservation.

        Each step is best-effort with its own try/except. Cancellation is
        first because once we've decided to roll back, leaving live orders
        at the broker is the worst outcome.

        broker_order_ids defaults to () so the kill_switch and protocol-only
        failure paths (where no orders made it to the broker) call this
        method exactly the way they used to.

        FIX-072: final_status defaults to "FAILED" for backward compatibility,
        but can be set to "REJECTED" for known rejection scenarios like 16388
        (insufficient margin after retry with fresh broker data).

        FIX-129 (Item 43): sends Telegram WARNING when an order is rejected
        (suppress_alert=True from paths that already send a more specific alert,
        e.g. slippage guard which carries trigger/LTP context).
        """
        log_exception(self._log, exc)

        # FIX-129: Telegram alert for placement failures.
        if self._notifier is not None and not suppress_alert:
            try:
                self._notifier.send(
                    severity="WARNING",
                    title=f"[{self._mode}] ORDER REJECTED — {symbol or 'unknown'}",
                    body=(
                        f"Status: {final_status}\n"
                        f"Reason: {str(exc)[:200]}"
                    ),
                    source_module="order_placer",
                )
            except Exception as _ne:
                self._log.error("order_placer: rejection notifier.send failed: %s", _ne)

        # OP-BL8c: cancel any broker orders that were placed before the failure
        ids = [bid for bid in broker_order_ids if bid]
        if ids:
            self._cancel_broker_orders(
                ids,
                reason=f"placement_failure: {type(exc).__name__}",
            )

        try:
            self._om.update_trade_status(trade_id, final_status)
        except Exception as db_exc:
            log_exception(self._log, db_exc)
        try:
            self._fm.release(reservation_id, f"placement_failed: {exc}")
        except Exception as cap_exc:
            log_exception(self._log, cap_exc)

    def _cancel_oco_siblings(
        self,
        trade_id: str,
        except_broker_order_id: str,
        order_protocol: str,
    ) -> None:
        """
        Audit #5: cancel any open sibling exit legs after one side fills.

        Traverses orders for `trade_id` and cancels every row that:
          * has a broker_order_id,
          * is not the current fill (except_broker_order_id),
          * is not already terminal, and
          * represents an exit leg (SL/TGT) OR a CO bracket ENTRY (variety=co).

        The CO-bracket special case: CO_PLUS_TGT has no separate SL row; the
        SL lives inside the CO bracket. Cancelling the CO entry (variety=co)
        when the separate TGT LIMIT has filled is how we collapse the inner
        SL after the fact.

        Best-effort: a failed cancel logs but does not raise -- the
        reconciler picks up any orphans.
        """
        try:
            rows = self._om.get_orders_for_trade(trade_id)
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "order_placer.oco_get_orders_failed",
                extra={"trade_id": trade_id},
            )
            return

        adapter = self._resolve_adapter()
        if adapter is None:
            self._log.critical(
                "order_placer.oco_no_adapter "
                "CANCEL_FAILED_MANUAL_INTERVENTION_REQUIRED",
                extra={"trade_id": trade_id},
            )
            return

        terminal = {"COMPLETE", "CANCELLED", "REJECTED", "FAILED"}
        for row in rows:
            bid = row.get("order_id") or ""
            if not bid or bid == except_broker_order_id:
                continue
            leg = (row.get("leg") or "").upper()
            status = (row.get("status") or "").upper()
            variety = row.get("variety") or "regular"

            # CO bracket ENTRY: cancel to collapse the inner SL even when
            # the ENTRY's own status is COMPLETE (the bracket stays live).
            if leg == "ENTRY":
                if order_protocol != "CO_PLUS_TGT" or variety != "co":
                    continue
            else:
                if leg not in ("SL", "TGT"):
                    continue
                if status in terminal:
                    continue

            try:
                result = adapter.cancel_order(bid, variety=variety)
            except Exception as exc:  # noqa: BLE001 - best-effort
                log_exception(self._log, exc)
                self._log.warning(
                    "order_placer.oco_sibling_cancel_raised",
                    extra={
                        "trade_id": trade_id,
                        "broker_order_id": bid,
                        "leg": leg,
                        "variety": variety,
                        "error": str(exc),
                    },
                )
                continue

            if not getattr(result, "success", False):
                self._log.warning(
                    "order_placer.oco_sibling_cancel_rejected",
                    extra={
                        "trade_id": trade_id,
                        "broker_order_id": bid,
                        "leg": leg,
                        "variety": variety,
                        "reason": getattr(result, "reason", ""),
                    },
                )
            else:
                self._log.info(
                    "order_placer.oco_sibling_cancel_ok",
                    extra={
                        "trade_id": trade_id,
                        "broker_order_id": bid,
                        "leg": leg,
                        "variety": variety,
                    },
                )

    def _resolve_adapter(self):
        """Reach through the engine to the shared broker adapter."""
        adapter = getattr(self._engine, "_co", None)
        adapter = getattr(adapter, "_adapter", None) if adapter is not None else None
        if adapter is None:
            adapter = getattr(getattr(self._engine, "_limit", None), "_adapter", None)
        return adapter

    def _cancel_broker_orders(
        self,
        broker_order_ids: List[str],
        reason: str,
    ) -> None:
        """
        OP-BL8d: best-effort cancel each broker order.

        On adapter rejection (CancelResult.success=False) or unexpected
        exception, log CRITICAL with the grep-friendly tag
        ``CANCEL_FAILED_MANUAL_INTERVENTION_REQUIRED`` and continue to the
        next ID. Never raises; cleanup must reach the FAILED+release stage
        regardless of cancel outcome.
        """
        # The adapter lives on the protocol objects, not on OrderPlacer; reach
        # through the engine. Both protocols share the same adapter instance.
        adapter = getattr(self._engine, "_co", None)
        adapter = getattr(adapter, "_adapter", None) if adapter is not None else None
        if adapter is None:
            adapter = getattr(getattr(self._engine, "_limit", None), "_adapter", None)
        if adapter is None:
            self._log.critical(
                "order_placer.cancel_no_adapter CANCEL_FAILED_MANUAL_INTERVENTION_REQUIRED",
                extra={
                    "broker_order_ids": broker_order_ids,
                    "reason": reason,
                    "detail": "no adapter reachable from engine; orders likely orphaned",
                },
            )
            return

        for bid in broker_order_ids:
            try:
                result = adapter.cancel_order(bid)
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                log_exception(self._log, exc)
                self._log.critical(
                    "order_placer.cancel_raised CANCEL_FAILED_MANUAL_INTERVENTION_REQUIRED",
                    extra={
                        "broker_order_id": bid,
                        "reason": reason,
                        "error": str(exc),
                    },
                )
                continue

            if not getattr(result, "success", False):
                self._log.critical(
                    "order_placer.cancel_rejected CANCEL_FAILED_MANUAL_INTERVENTION_REQUIRED",
                    extra={
                        "broker_order_id": bid,
                        "reason": reason,
                        "broker_reason": getattr(result, "reason", ""),
                    },
                )
            else:
                self._log.info(
                    "order_placer.cancel_ok",
                    extra={"broker_order_id": bid, "reason": reason},
                )

    def _persist_entry_orders(
        self,
        trade_id: str,
        result: EntryResult,
        symbol: str,
        qty: int,
        side: str,
        intent: str,
    ) -> None:
        """
        Persist order rows to DB after successful placement (OP-BL8a/OP-BL8b).

        Builds an OrderInsertSpec list for whichever legs have a broker_order_id
        and hands the batch to OrderManager.insert_orders_atomic so every row
        commits together or none do.

        On exception this method PROPAGATES (post-BL-8). place() catches and
        runs the cancel-broker-orders + FAILED + release + hard_kill cleanup.
        """
        exit_side = "SELL" if side == "BUY" else "BUY"

        # HIGH #7: product code must come from injected resolver; no hardcoded fallback.
        if self._product_resolver is None:
            raise RuntimeError("OrderPlacer requires product_resolver")
        product = self._product_resolver.resolve(intent)
        co_variety = "co" if result.order_protocol == "CO_PLUS_TGT" else "regular"

        specs: List[OrderInsertSpec] = []

        if result.entry_broker_order_id:
            specs.append(OrderInsertSpec(
                broker_order_id=result.entry_broker_order_id,
                leg="ENTRY",
                transaction_type=side,
                order_type="SL" if result.order_protocol == "CO_PLUS_TGT" else "LIMIT",
                product=product,
                variety=co_variety,
                qty_requested=qty,
            ))

        # SL order (LIMIT_TRIPLE defers SL to place_exits, so this is normally
        # empty here; kept for safety). P0 2026-06-15: label as "SL" (stop-limit),
        # never "SL-M" — Zerodha rejects SL-M via API.
        if result.sl_broker_order_id:
            specs.append(OrderInsertSpec(
                broker_order_id=result.sl_broker_order_id,
                leg="SL",
                transaction_type=exit_side,
                order_type="SL",
                product=product,
                variety="regular",
                qty_requested=qty,
            ))

        # TGT order
        if result.tgt_broker_order_id:
            specs.append(OrderInsertSpec(
                broker_order_id=result.tgt_broker_order_id,
                leg="TGT",
                transaction_type=exit_side,
                order_type="LIMIT",
                product=product,
                variety="regular",
                qty_requested=qty,
            ))

        # OP-BL8a: atomic batch INSERT. Exceptions propagate; place() handles
        # cleanup (cancel broker orders, mark FAILED, release reservation,
        # fire hard_kill).
        self._om.insert_orders_atomic(trade_id, specs)
