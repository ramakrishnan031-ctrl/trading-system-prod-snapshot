"""
capital/position_sizer.py -- Trading System v2

Purpose:
    Pure calculation of position size (qty) for a signal, given entry price,
    SL price, risk parameters, and available capital from FundManager.
    No state mutations, no side effects, no broker calls (PS1, PS13).

Locked Design Decisions:
    PS1  -- Pure calculation: no state, no side effects, no broker calls.
    PS2  -- Risk-based sizing formula (see calculate() docstring).
    PS3  -- Constructor: PositionSizer(fund_manager, leverage_map, ...).
    PS4  -- API: calculate() -> SizingResult (frozen dataclass).
    PS5  -- Tier multipliers: HIGH=1.0, MEDIUM=0.7, LOW=0.5.
             Applied AFTER min(risk, capital, concentration), BEFORE lot_size.
    PS6  -- Lot size rounding: qty = (qty // lot_size) * lot_size.
             If result < lot_size: SizingResult(success=False, constraint=BELOW_MIN).
    PS7  -- Bucket determination from intent; snapshot read from fund_manager.
    PS8  -- Validation raises ValueError (programmer errors, not signal rejections).
    PS9  -- SystemConfig.position_sizing added.
    PS10 -- SL direction sanity: log WARNING only, calculation proceeds regardless.
    PS11 -- Layer 4 (capital/). Deps: stdlib, core.logger, capital.fund_manager.
    PS12 -- NOT in scope: risk engine, sector concentration, daily loss limit,
             capital reservation (caller does reserve() after success).
    PS13 -- Deterministic: same inputs + same snapshot -> same SizingResult.

What This Module Does NOT Do:
    - Does not reserve capital (caller calls fund_manager.reserve() after success)
    - Does not check portfolio-level risk limits (risk_engine does that)
    - Does not compute sector/industry concentration (separate module)
    - Does not check daily loss limit (fund_manager tracks that)
    - Does not introduce randomness or I/O
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from core.effect_telemetry import handle as _effect_handle

if TYPE_CHECKING:
    from capital.fund_manager import FundManager

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_VALID_INTENTS: frozenset[str] = frozenset(
    {"INTRADAY", "COVER_ORDER", "BRACKET_ORDER", "DELIVERY"}
)
_INTRADAY_INTENTS: frozenset[str] = frozenset(
    {"INTRADAY", "COVER_ORDER", "BRACKET_ORDER"}
)
_VALID_TIERS: frozenset[str] = frozenset({"HIGH", "MEDIUM", "LOW"})
_VALID_SIDES: frozenset[str] = frozenset({"BUY", "SELL"})

_DEFAULT_TIER_MULTIPLIERS: dict[str, float] = {
    "HIGH": 1.0,
    "MEDIUM": 0.7,
    "LOW": 0.5,
}


# ─────────────────────────────────────────────────────────────────────────────
# Return type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SizingResult:
    """
    Frozen result of a position sizing calculation (PS4).

    Fields:
        success:         True if a valid qty was computed.
        qty:             Final quantity to trade (0 on failure).
        margin_required: Capital to reserve = qty * (entry_price / leverage).
        risk_amount:     Actual rupees at risk = qty * sl_distance.
        bucket:          "intraday" | "positional" (PS7).
        constraint:      What bound the qty:
                           "RISK"          -- the effective risk-per-trade pct was tightest
                           "CAPITAL"       -- available bucket capital was tightest
                           "CONCENTRATION" -- the effective concentration pct was tightest
                                              ("effective" = the DELIVERY key for a
                                              positional entry, the global key for an
                                              intraday one; they are never mixed)
                           "BELOW_MIN"     -- tier/lot_size rounding made qty < minimum
                           "ZERO_MULTIPLIER" -- M-C6: tier_mult × perf_weight <= 0, i.e.
                                              sizing said "trade nothing" -> skip (NOT
                                              floored to 1 lot)
        reason:          Human-readable explanation (non-empty always).
        breakdown:       Dict with all candidate qtys + tier multiplier for audit.
    """
    success: bool
    qty: int
    margin_required: float
    risk_amount: float
    bucket: str
    constraint: str
    reason: str
    breakdown: dict


# ─────────────────────────────────────────────────────────────────────────────
# PositionSizer
# ─────────────────────────────────────────────────────────────────────────────

class PositionSizer:
    """
    Computes how many shares to trade for a signal (PS1-PS13).

    Read-only access to FundManager (get_snapshot only). No writes.
    Thread-safe: stateless beyond constructor arguments (PS13).

    Usage::
        sizer = PositionSizer(
            fund_manager=fm,
            leverage_map={"INTRADAY": 5.0, "DELIVERY": 1.0, ...},
            risk_per_trade_pct=0.01,
            max_concentration_pct=0.10,
            # Required to size a DELIVERY (positional) entry. Omit them and a
            # positional entry raises — it will NOT borrow the intraday values.
            delivery_risk_per_trade_pct=0.01,
            delivery_max_concentration_pct=0.10,
            delivery_max_position_value_pct=0.40,
        )
        result = sizer.calculate("RELIANCE", "BUY", 2500.0, 2450.0, "INTRADAY")
        if result.success:
            reserve_result = fm.reserve(..., qty=result.qty, ...)
    """

    def __init__(
        self,
        fund_manager: "FundManager",
        leverage_map: dict[str, float],
        risk_per_trade_pct: float = 0.01,
        max_concentration_pct: float = 0.10,
        min_qty_threshold: int = 1,
        tier_multipliers: Optional[dict[str, float]] = None,
        logger=None,
        instrument_cache=None,  # IC7: optional InstrumentCache for lot_size lookup
        lot_skew_rejection_threshold: float = 0.25,  # FIX-021: reject if skew exceeds this
        min_tick_size: float = 0.05,  # FIX-041: min SL distance (penny stock guard)
        max_single_order_qty: int = 10000,  # FIX-041: sanity cap on computed qty
        # FIX-144 / BUILD 1 (#2, #A.4): capital-relative hard cap on qty*price.
        # Default is the conservative 40% (was a stale, far-looser absolute 50000).
        max_position_value_pct: float = 0.40,
        broker_adapter=None,  # FIX-072: optional adapter for live margin fetch
        enabled: bool = True,                   # Diary #4: ON = score-tier × perf sizing (default)
        flat_value_rs: Optional[float] = None,  # Diary #4: flat Rs/order; required when enabled=False
        # DELIVERY-scoped sizing limits (22-Aug-2026, fix item 1). Applied ONLY to a
        # positional (delivery) bucket; the intraday bucket never reads them.
        # ⛔ These do NOT fall back to the global values. They default to None only so
        # that an intraday-only caller (every direct construction in the test suite)
        # need not supply delivery config it will never use; the moment a POSITIONAL
        # entry is sized, a None here is a hard ValueError naming the key — never a
        # silent substitution of the intraday number. In production this class is
        # constructed at exactly one site (main.py) from config that now REQUIRES all
        # three keys, so None is unreachable there.
        delivery_risk_per_trade_pct: Optional[float] = None,
        delivery_max_concentration_pct: Optional[float] = None,
        delivery_max_position_value_pct: Optional[float] = None,
    ) -> None:
        self._fm = fund_manager
        self._leverage_map = dict(leverage_map)
        self._risk_per_trade_pct = risk_per_trade_pct
        self._max_concentration_pct = max_concentration_pct
        self._min_qty_threshold = min_qty_threshold
        self._tier_multipliers = dict(tier_multipliers or _DEFAULT_TIER_MULTIPLIERS)
        self._log = logger
        self._instrument_cache = instrument_cache  # IC7
        self._lot_skew_rejection_threshold = lot_skew_rejection_threshold  # FIX-021
        self._min_tick_size = min_tick_size  # FIX-041
        self._max_single_order_qty = max_single_order_qty  # FIX-041
        self._max_position_value_pct = max_position_value_pct  # FIX-144 / BUILD 1 (#2)
        self._broker_adapter = broker_adapter  # FIX-072
        # Diary #4: sizing mode. enabled=True -> score-tier × perf-weight (unchanged).
        # enabled=False -> flat Rs/order (Option δ); flat_value_rs is one more ceiling
        # on top of risk/capital/concentration (those still bind). Config-load also
        # enforces this, but guard here too (PositionSizer is built directly in tests).
        self._enabled = enabled
        self._flat_value_rs = flat_value_rs
        # effect-telemetry (ledger #1, frozen contract A2.1 + A2.3): handles
        # resolved once; hot-path ops are single integer increments.
        self._fx_verdict = _effect_handle("position_sizer")
        self._fx_risk_bind = _effect_handle("sizer.risk_bind")
        self._fx_live_margin = _effect_handle("sizer.live_margin")
        # DELIVERY-scoped limits (see ctor note). Never merged with the global ones.
        self._delivery_risk_per_trade_pct = delivery_risk_per_trade_pct
        self._delivery_max_concentration_pct = delivery_max_concentration_pct
        self._delivery_max_position_value_pct = delivery_max_position_value_pct
        if not enabled and (flat_value_rs is None or flat_value_rs <= 0):
            raise ValueError(
                f"PositionSizer: enabled=False (flat sizing) requires flat_value_rs > 0, "
                f"got {flat_value_rs!r}"
            )

    def _require_delivery(self, value: Optional[float], key: str) -> float:
        """Return a delivery-scoped limit, or REFUSE — never inherit the intraday one.

        22-Aug-2026 (fix item 1). The whole point of this helper is that it has no
        `else` returning a global value. Rama's standard, verbatim: *"No Silent
        Fallbacks: missing params cause immediate rejection with logged reason."*
        The key is named in the message so the failure is diagnosable from the log
        alone. In production `config/system_config.yaml` makes all three keys REQUIRED,
        so reaching this raise means a component was wired outside the config path.
        """
        if value is None:
            if self._log is not None:
                # ⛔ NOT `extra={"msg": ...}` — "msg" is a reserved LogRecord attribute
                # and logging raises KeyError on the collision, i.e. the diagnostic
                # would destroy the very failure it is describing.
                self._log.critical(
                    "position_sizer.delivery_limit_missing",
                    extra={"key": f"position_sizing.{key}",
                           "detail": ("delivery sizing limit is not configured; refusing "
                                      "to size a positional (CNC) entry on the intraday "
                                      "value")},
                )
            raise ValueError(
                f"PositionSizer: position_sizing.{key} is not configured; a delivery "
                f"(positional) entry cannot be sized. This value does NOT fall back to "
                f"the intraday (global) setting."
            )
        return value

    def calculate(self, *args, **kwargs) -> "SizingResult":
        """effect-telemetry (frozen A2.1): the ONE lexical point for "a sizing
        verdict produced" — `_calculate` has eight return sites, all funnel
        here. A raise is not a verdict and is deliberately not counted.
        Signature/behaviour identical to `_calculate` (pure pass-through)."""
        result = self._calculate(*args, **kwargs)
        self._fx_verdict.inc()
        return result

    def _calculate(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        sl_price: float,
        intent: str,
        score_tier: str = "MEDIUM",
        lot_size: int = 1,
        entry_offset_pct: float = 0.0,  # FIX-066
        perf_weight: float = 1.0,       # FIX-132 Item 9: performance-weighted multiplier
    ) -> SizingResult:
        """
        Compute position size using risk-based formula (PS2).

        Formula (all quantities floor()-truncated to integers). `eff_*` means the
        DELIVERY key when bucket == "positional" and the GLOBAL key when it is
        "intraday" — the two books never share a number and an absent delivery key
        raises rather than borrowing the intraday one:
            risk_per_trade_rs      = total_capital * eff_risk_pct
            sl_distance            = abs(entry_price - sl_price)
            qty_by_risk            = floor(risk_per_trade_rs / sl_distance)

            effective_entry_price  = entry_price * (1 + entry_offset_pct)  # FIX-066
            margin_per_share       = effective_entry_price / leverage
            qty_by_capital         = floor(avail_bucket / margin_per_share)

            qty_by_concentration   = floor((total_capital * eff_conc_pct) / entry_price)

            raw_qty                = min(qty_by_risk, qty_by_capital, qty_by_concentration)
            tiered_qty             = floor(raw_qty * tier_multiplier)
            final_qty              = (tiered_qty // lot_size) * lot_size

        Args:
            symbol:           Trading symbol (used for logging only).
            side:             "BUY" or "SELL" (PS8).
            entry_price:      Expected entry price per share (> 0).
            sl_price:         Stop-loss price per share (> 0, != entry_price).
            intent:           One of INTRADAY, COVER_ORDER, BRACKET_ORDER, DELIVERY.
            score_tier:       Signal quality tier: "HIGH", "MEDIUM", or "LOW" (PS5).
            lot_size:         Shares per lot. 1 for equity; F&O uses contract lot (PS6).
            entry_offset_pct: FIX-066 - Buffer applied to entry_price for margin calc.
                              order_placer places at entry_price * (1 + entry_offset_pct),
                              so margin must account for this to avoid RMS rejection.
                              Defaults to 0.0 (no buffer).

        Returns:
            SizingResult (always returned, never raises for sizing failures).

        Raises:
            ValueError: for programmer errors (PS8) — invalid entry_price,
                        sl_price==entry_price, bad intent/tier/lot_size/side.
        """
        # IC7: resolve lot_size from instrument_cache if available and caller
        # passed the default (1). Explicit non-1 values from caller take precedence.
        if lot_size == 1 and self._instrument_cache is not None:
            try:
                lot_size = self._instrument_cache.lot_size(symbol)
            except Exception:
                pass  # InstrumentNotFoundError or missing cache -> keep default

        # ── PS8: Input validation (programmer errors → ValueError) ────────────
        if side not in _VALID_SIDES:
            raise ValueError(
                f"side must be 'BUY' or 'SELL', got {side!r}"
            )
        if entry_price <= 0:
            raise ValueError(
                f"entry_price must be > 0, got {entry_price}"
            )
        if sl_price <= 0:
            raise ValueError(
                f"sl_price must be > 0, got {sl_price}"
            )
        if intent not in _VALID_INTENTS:
            raise ValueError(
                f"intent {intent!r} not in valid set {sorted(_VALID_INTENTS)}"
            )
        if score_tier not in _VALID_TIERS:
            raise ValueError(
                f"score_tier {score_tier!r} not in {sorted(_VALID_TIERS)}"
            )
        if lot_size <= 0:
            raise ValueError(
                f"lot_size must be > 0, got {lot_size}"
            )

        # ── PS10: SL direction sanity (WARNING only, calc proceeds) ──────────
        if side == "BUY" and sl_price > entry_price:
            self._warn(
                "position_sizer.sl_direction_warning",
                {"side": side, "entry": entry_price, "sl": sl_price,
                 "msg": "BUY sl_price > entry_price (SL should be below entry for BUY)"},
            )
        elif side == "SELL" and sl_price < entry_price:
            self._warn(
                "position_sizer.sl_direction_warning",
                {"side": side, "entry": entry_price, "sl": sl_price,
                 "msg": "SELL sl_price < entry_price (SL should be above entry for SELL)"},
            )

        # ── PS7: Bucket + snapshot ─────────────────────────────────────────────
        bucket = "intraday" if intent in _INTRADAY_INTENTS else "positional"
        snap = self._fm.get_snapshot()
        total_capital = snap.total
        avail = snap.intraday_avail if bucket == "intraday" else snap.positional_avail

        # ── Per-book risk limits (22-Aug-2026, fix item 1) ────────────────────────
        # A positional (delivery / CNC) entry is sized on the DELIVERY limits; an
        # intraday (MIS/CO/BO) entry is sized on the GLOBAL ones. The two books do not
        # share a number and neither can move the other.
        #
        # ⛔ WHAT WAS HERE BEFORE, AND WHY IT IS GONE: the previous expression read
        #       delivery_X if (bucket == "positional" and delivery_X is not None)
        #       else global_X
        # so an unset delivery key SILENTLY INHERITED the intraday value. The comment
        # justifying that called these keys unread because it called the V3 delivery
        # path dormant — FALSE on both counts. Delivery is live and has traded, and
        # this branch sized three real CNC entries on the INTRADAY risk budget.
        # A missing delivery limit is now a hard error, never a substitution.
        if bucket == "positional":
            eff_risk_pct = self._require_delivery(
                self._delivery_risk_per_trade_pct, "delivery_risk_per_trade_pct")
            eff_conc_pct = self._require_delivery(
                self._delivery_max_concentration_pct, "delivery_max_concentration_pct")
            eff_max_position_value_pct = self._require_delivery(
                self._delivery_max_position_value_pct, "delivery_max_position_value_pct")
        else:
            eff_risk_pct = self._risk_per_trade_pct
            eff_conc_pct = self._max_concentration_pct
            eff_max_position_value_pct = self._max_position_value_pct

        # ── PS2: Three candidate quantities ───────────────────────────────────
        # FIX-072: Try live margin from broker API first, fallback to static on error
        leverage = self._leverage_map.get(intent, 1.0)  # static fallback
        if self._broker_adapter is not None:
            try:
                margin_pct = self._broker_adapter.get_live_margin_pct(symbol, intent)
                # effect-telemetry (frozen A2.3): live broker margin consulted
                # (FIX-072 / IA-P3-02) — rides the success branch that already
                # emits `position_sizer.live_margin_used` below; the fallback
                # except-path is deliberately NOT counted.
                self._fx_live_margin.inc()
                # Convert margin_pct to leverage: leverage = 1 / margin_pct
                # e.g., margin_pct=0.20 (20%) -> leverage=5.0
                live_leverage = 1.0 / margin_pct if margin_pct > 0 else 1.0
                leverage = live_leverage
                if self._log is not None:
                    self._log.info(
                        "position_sizer.live_margin_used",
                        extra={
                            "symbol": symbol,
                            "intent": intent,
                            "margin_pct": margin_pct,
                            "live_leverage": live_leverage,
                            "static_leverage": self._leverage_map.get(intent, 1.0),
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                # API failed or paper mode: fallback to static leverage
                if self._log is not None:
                    self._log.warning(
                        "position_sizer.live_margin_fallback",
                        extra={
                            "symbol": symbol,
                            "intent": intent,
                            "error": str(exc),
                            "fallback_leverage": leverage,
                        },
                    )
        sl_distance = abs(entry_price - sl_price)

        # FIX-041: Guard 1 — SL distance below minimum tick size (penny stock / config error)
        # Prevents ZeroDivisionError and micro-fraction qty explosion.
        # MED #8 enhanced: was sl_distance == 0.0, now sl_distance < min_tick_size.
        if sl_distance < self._min_tick_size:
            if self._log is not None:
                self._log.critical(
                    "position_sizer.invalid_sl_distance",
                    extra={
                        "symbol": symbol,
                        "sl_distance": sl_distance,
                        "min_tick_size": self._min_tick_size,
                        "entry_price": entry_price,
                        "sl_price": sl_price,
                    },
                )
            return SizingResult(
                success=False,
                qty=0,
                margin_required=0.0,
                risk_amount=0.0,
                bucket=bucket,
                constraint="INVALID_SL_DISTANCE",
                reason=(
                    f"sl_distance={sl_distance:.4f} < min_tick_size={self._min_tick_size} "
                    f"for {symbol} (entry={entry_price}, sl={sl_price}); "
                    f"cannot size position (ZeroDivisionError guard)"
                ),
                breakdown={},
            )

        risk_rs = total_capital * eff_risk_pct
        qty_by_risk = int(math.floor(risk_rs / sl_distance))

        # FIX-041: Guard 2 — Qty explosion sanity cap
        # Prevents micro-fraction sl_distance from producing million-share orders.
        if qty_by_risk > self._max_single_order_qty:
            if self._log is not None:
                self._log.critical(
                    "position_sizer.qty_explosion_guard",
                    extra={
                        "symbol": symbol,
                        "qty_by_risk": qty_by_risk,
                        "max_single_order_qty": self._max_single_order_qty,
                        "sl_distance": sl_distance,
                        "entry_price": entry_price,
                        "sl_price": sl_price,
                    },
                )
            return SizingResult(
                success=False,
                qty=0,
                margin_required=0.0,
                risk_amount=0.0,
                bucket=bucket,
                constraint="QTY_EXPLOSION_GUARD",
                reason=(
                    f"qty_by_risk={qty_by_risk} > max_single_order_qty={self._max_single_order_qty} "
                    f"for {symbol} (sl_distance={sl_distance:.4f}); "
                    f"rejecting to prevent broker account suspension"
                ),
                breakdown={"qty_by_risk": qty_by_risk},
            )

        # FIX-066: Apply entry_offset_pct to margin calculation.
        # order_placer places at entry_price * (1 + entry_offset_pct), so broker
        # charges margin on that higher price. Reserve must cover the actual margin.
        effective_entry_price = entry_price * (1.0 + entry_offset_pct)
        margin_per_share = effective_entry_price / leverage
        qty_by_capital = (
            int(math.floor(avail / margin_per_share)) if margin_per_share > 0 else 0
        )

        qty_by_concentration = int(math.floor(
            (total_capital * eff_conc_pct) / entry_price
        ))

        # Binding constraint: CAPITAL wins on tie (most conservative), then RISK
        raw_qty = min(qty_by_risk, qty_by_capital, qty_by_concentration)
        if qty_by_capital <= qty_by_risk and qty_by_capital <= qty_by_concentration:
            constraint = "CAPITAL"
        elif qty_by_risk <= qty_by_concentration:
            constraint = "RISK"
            # effect-telemetry (frozen A2.3, gamma): the risk-per-trade term
            # actually bound a size (IA-P3-04 — algebraically never today).
            self._fx_risk_bind.inc()
        else:
            constraint = "CONCENTRATION"

        # Build breakdown (PS4) — populated before any early exits
        breakdown: dict = {
            "qty_by_risk": qty_by_risk,
            "qty_by_capital": qty_by_capital,
            "qty_by_concentration": qty_by_concentration,
            "raw_qty": raw_qty,
            "tier_multiplier": self._tier_multipliers.get(score_tier, 1.0),
        }

        # Early exit: no quantity possible at all (capital/risk/conc exhausted)
        if raw_qty <= 0:
            reason = (
                f"qty=0: {constraint} exhausted for {symbol} "
                f"(risk_qty={qty_by_risk} capital_qty={qty_by_capital} "
                f"conc_qty={qty_by_concentration})"
            )
            return SizingResult(
                success=False,
                qty=0,
                margin_required=0.0,
                risk_amount=0.0,
                bucket=bucket,
                constraint=constraint,
                reason=reason,
                breakdown=breakdown,
            )

        # ── Sizing mode (Diary #4) ─────────────────────────────────────────────
        tier_mult = self._tier_multipliers.get(score_tier, 1.0)  # configured weight (audit)
        if self._enabled:
            # ON: PS5 tier multiplier × PA4 performance weight (FIX-132 Item 9) — unchanged.
            effective_mult = tier_mult * max(0.0, perf_weight)  # perf_weight >= 0 guard
            # M-C6 (16-Jul-2026): a ZERO (or negative) multiplier means "size this to
            # nothing" — SKIP the trade. FIX-133's floor below turned it into 1 lot,
            # i.e. real capital and real risk on a signal the sizing model had just
            # said to stay out of. The floor exists so a small-but-POSITIVE multiplier
            # still trades (0.3 × 2 lots rounding to 0 should not silently kill a
            # wanted trade); it was never meant to manufacture a position out of an
            # explicit zero. Split the two cases and the floor keeps its real job.
            #
            # `<= 0` needs no tolerance: perf_weight is already clamped >= 0 one line
            # up, so the product cannot be a tiny FP negative — a negative here means
            # a genuinely negative tier_mult (PositionSizingTierConfig types HIGH/
            # MEDIUM/LOW as bare floats with no ge=0 bound, so a config typo reaches
            # this), and -1.0 * 0.0 == -0.0 which `<= 0` also catches. Negative is
            # treated exactly as zero: there is no meaning to a negative size.
            #
            # NOT reachable today: performance_allocator clamps min_weight=0.5 (PA3/
            # PA8) and signal_processor defaults an unknown strategy to 1.0, so
            # effective_mult >= 0.25 in production and this branch never fires. It is
            # a hard PREREQUISITE for ever lowering min_weight.
            if effective_mult <= 0:
                breakdown["tier_multiplier_mode"] = "ON"
                breakdown["tier_weight_applied"] = tier_mult
                breakdown["perf_weight_applied"] = round(perf_weight, 4)
                breakdown["flat_value_rs_used"] = None
                breakdown["qty_by_flat"] = None
                breakdown["tiered_qty"] = 0
                breakdown["tier_mult"] = tier_mult
                breakdown["perf_weight"] = round(perf_weight, 4)
                breakdown["effective_mult"] = effective_mult
                reason = (
                    f"qty=0: ZERO_MULTIPLIER for {symbol} — effective_mult="
                    f"{effective_mult} (tier_mult={tier_mult} × perf_weight="
                    f"{perf_weight}) sizes this trade to nothing; skipping rather "
                    f"than flooring to 1 lot"
                )
                if self._log is not None:
                    self._log.warning(
                        "position_sizer.zero_multiplier_skip",
                        extra={
                            "symbol": symbol,
                            "tier_mult": tier_mult,
                            "perf_weight": perf_weight,
                            "effective_mult": effective_mult,
                            "raw_qty": raw_qty,
                        },
                    )
                return SizingResult(
                    success=False,
                    qty=0,
                    margin_required=0.0,
                    risk_amount=0.0,
                    bucket=bucket,
                    constraint="ZERO_MULTIPLIER",
                    reason=reason,
                    breakdown=breakdown,
                )
            tiered_qty = int(math.floor(raw_qty * effective_mult))
            # FIX-133 Item 21: cap at 2x base_qty, floor at 1 — for a POSITIVE
            # multiplier only (see the M-C6 note above).
            tiered_qty = max(1, min(tiered_qty, raw_qty * 2))
            breakdown["tier_multiplier_mode"] = "ON"
            breakdown["tier_weight_applied"] = tier_mult
            breakdown["perf_weight_applied"] = round(perf_weight, 4)
            breakdown["flat_value_rs_used"] = None
            breakdown["qty_by_flat"] = None
        else:
            # OFF (Option δ): flat Rs/order. flat_value_rs is one more ceiling on top of
            # risk/capital/concentration. Score-tier and perf-weight are NOT applied, so
            # the size is score-neutral. NO floor-at-1: a flat below 1 lot -> BELOW_MIN skip.
            qty_by_flat = int(math.floor(self._flat_value_rs / entry_price))
            tiered_qty = min(raw_qty, qty_by_flat)
            if qty_by_flat < raw_qty:
                constraint = "FLAT"  # flat is the binding ceiling
            breakdown["tier_multiplier_mode"] = "OFF_FLAT"
            breakdown["tier_weight_applied"] = None
            breakdown["perf_weight_applied"] = None
            breakdown["flat_value_rs_used"] = self._flat_value_rs
            breakdown["qty_by_flat"] = qty_by_flat
        breakdown["tiered_qty"] = tiered_qty
        breakdown["tier_mult"] = tier_mult
        breakdown["perf_weight"] = round(perf_weight, 4)

        # ── PS6: Lot size rounding ─────────────────────────────────────────────
        final_qty = (tiered_qty // lot_size) * lot_size

        # ── FIX-021: Lot skew rejection (skip if lot_size == 1 or final_qty == 0) ──
        # If final_qty==0, let BELOW_MIN handle it (more accurate constraint name).
        if lot_size != 1 and tiered_qty > 0 and final_qty > 0:
            skew = (tiered_qty - final_qty) / tiered_qty
            if skew > self._lot_skew_rejection_threshold:
                reason = (
                    f"REJECTED_LOT_SKEW for {symbol}: skew={skew:.1%} "
                    f"exceeds threshold {self._lot_skew_rejection_threshold:.1%} "
                    f"(tiered_qty={tiered_qty}, final_qty={final_qty}, lot_size={lot_size})"
                )
                return SizingResult(
                    success=False,
                    qty=0,
                    margin_required=0.0,
                    risk_amount=0.0,
                    bucket=bucket,
                    constraint="REJECTED_LOT_SKEW",
                    reason=reason,
                    breakdown=breakdown,
                )

        # ── FIX-144 / BUILD 1 (#2): Position value cap (catastrophic-loss / bug-guard) ──
        # Capital-relative hard cap on qty*price regardless of how it was computed.
        # cap = eff_max_position_value_pct × current capital (total_capital, fetched
        # above). REJECT (not clamp) — this fires on an ANOMALY, not routine
        # sizing (which is governed by concentration + risk). Catches:
        # - Bugs in earlier constraints
        # - High-priced stocks where even small qty is large exposure
        #
        # 22-Aug-2026 (fix item 1): the log field and the reason string below used to
        # report `self._max_position_value_pct` — the GLOBAL — while the line above
        # ENFORCED `eff_max_position_value_pct`. They were identical only because the
        # delivery override was null, and populating it is precisely what separates
        # them. Both now report the value that was actually enforced; a rejection that
        # states a percentage nobody applied is a trap, not a diagnostic.
        position_value = final_qty * entry_price
        max_position_value = eff_max_position_value_pct * total_capital
        if position_value > max_position_value:
            if self._log is not None:
                self._log.critical(
                    "position_sizer.position_value_cap_exceeded",
                    extra={
                        "symbol": symbol,
                        "final_qty": final_qty,
                        "entry_price": entry_price,
                        "position_value": position_value,
                        "max_position_value": max_position_value,
                        "max_position_value_pct": eff_max_position_value_pct,
                        "bucket": bucket,
                        "capital": total_capital,
                    },
                )
            return SizingResult(
                success=False,
                qty=0,
                margin_required=0.0,
                risk_amount=0.0,
                bucket=bucket,
                constraint="POSITION_VALUE_CAP",
                reason=(
                    f"position_value={position_value:.2f} > max={max_position_value:.2f} "
                    f"({eff_max_position_value_pct:.0%} of capital {total_capital:.2f}) "
                    f"for {symbol} (qty={final_qty}, price={entry_price}); "
                    f"rejecting to prevent catastrophic loss"
                ),
                breakdown=breakdown,
            )

        if final_qty < lot_size or final_qty < self._min_qty_threshold:
            if self._enabled:
                reason = (
                    f"qty={final_qty} below minimum for {symbol}: "
                    f"tier={score_tier}({tier_mult}) lot_size={lot_size} "
                    f"min_threshold={self._min_qty_threshold} "
                    f"(risk_qty={qty_by_risk} capital_qty={qty_by_capital} "
                    f"conc_qty={qty_by_concentration} tiered={tiered_qty})"
                )
            else:
                reason = (
                    f"flat_value_rs Rs{self._flat_value_rs:.0f} below 1 lot at entry "
                    f"Rs{entry_price:.2f} for {symbol} (qty={final_qty} lot_size={lot_size}); "
                    f"signal skipped (risk_qty={qty_by_risk} capital_qty={qty_by_capital} "
                    f"conc_qty={qty_by_concentration} flat_qty={breakdown.get('qty_by_flat')})"
                )
            return SizingResult(
                success=False,
                qty=0,
                margin_required=0.0,
                risk_amount=0.0,
                bucket=bucket,
                constraint="BELOW_MIN",
                reason=reason,
                breakdown=breakdown,
            )

        margin_required = final_qty * margin_per_share
        risk_amount = final_qty * sl_distance

        breakdown["binding_constraint"] = constraint.lower()
        breakdown["actual_position_value_rs"] = round(final_qty * entry_price, 2)

        if self._enabled:
            reason = (
                f"{symbol} qty={final_qty} [{constraint}-bound tier={score_tier}({tier_mult})]: "
                f"risk_qty={qty_by_risk} capital_qty={qty_by_capital} "
                f"conc_qty={qty_by_concentration} lot_size={lot_size}"
            )
        else:
            reason = (
                f"{symbol} qty={final_qty} [{constraint}-bound FLAT Rs{self._flat_value_rs:.0f}]: "
                f"risk_qty={qty_by_risk} capital_qty={qty_by_capital} "
                f"conc_qty={qty_by_concentration} flat_qty={breakdown['qty_by_flat']} lot_size={lot_size}"
            )

        return SizingResult(
            success=True,
            qty=final_qty,
            margin_required=margin_required,
            risk_amount=risk_amount,
            bucket=bucket,
            constraint=constraint,
            reason=reason,
            breakdown=breakdown,
        )

    # ── private ───────────────────────────────────────────────────────────────

    def _warn(self, msg: str, extra: dict) -> None:
        if self._log is not None:
            self._log.warning(msg, extra=extra)
