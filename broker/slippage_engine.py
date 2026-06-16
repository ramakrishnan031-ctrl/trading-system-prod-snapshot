"""
broker/slippage_engine.py -- Trading System v2

Purpose:
    P12 implementation — apply per-liquidity-tier slippage to paper-mode
    synthesized fills so paper P&L does not systematically over-state vs
    live P&L. The audit (2026-04-26 CFG-6) flagged that
    config/slippage_model.yaml was loaded but never consumed; paper synth
    in zerodha_adapter._synth_fill set fill_price = price exactly. This
    closes that gap.

Design Decisions:
    SE1 -- Stateless, dependency-injected. No singletons, no module-level
            state. Constructed once at startup and passed to ZerodhaAdapter.
    SE2 -- Tier resolution from InstrumentCache:
            - is_fno=True  → "liquid"
            - is_fno=False → "mid"
            - unknown sym  → SlippageConfig.default_tier
            (instruments.csv has no avg_volume column; F&O eligibility is
            the canonical NSE-side liquidity proxy. Sector-level "small"
            classification can be added later if a column appears.)
    SE3 -- Slippage is always ADVERSE (paper conservatism, P12):
            - BUY  : fill = price + bps_pct * price  (paid more)
            - SELL : fill = price - bps_pct * price  (received less)
    SE4 -- Result is rounded to instrument tick_size when known. If symbol
            is not in cache, no tick rounding is applied (raw float).
    SE5 -- bps math uses 1 bps = 0.01% = price * (bps / 10_000).
    SE6 -- apply() is pure: same (symbol, side, price) → same output.
            No mutation, no I/O, no logging from the hot path.
    SE7 -- side is uppercased; anything other than "BUY"/"SELL" raises
            ValueError. The adapter is the only caller and always passes
            uppercase, so this is defence-in-depth.

What This Module Does NOT Do:
    - Does not handle live mode. Live fills come from the broker; their
      price IS the truth.
    - Does not model market impact, order book depth, or partial fills.
    - Does not log per-fill slippage; the adapter logs the post-slippage
      fill_price as part of the existing OrderFilled extra= payload.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Optional

from core.config_loader import SlippageConfig
from core.exceptions import InstrumentNotFoundError
from core.instrument_cache import InstrumentCache


class SlippageEngine:
    """P12 paper-mode slippage applicator (SE1-SE7)."""

    def __init__(
        self,
        config: SlippageConfig,
        instrument_cache: InstrumentCache,
        logger=None,  # FIX-050: optional logger for DEBUG when sub-tick guard activates
    ) -> None:
        self._cfg = config
        self._cache = instrument_cache
        self._log = logger
        # Pre-validate default_tier so apply() can rely on it.
        if config.default_tier not in config.tiers:
            raise ValueError(
                f"slippage_model.yaml: default_tier {config.default_tier!r} "
                f"not present in tiers {list(config.tiers.keys())!r}"
            )

    def tier_for(self, symbol: str) -> str:
        """
        SE2: resolve liquidity tier for a symbol.

        FIX-109 TODO: Current FNO-only proxy is incomplete.
        Improvement criteria:
          1. Add avg_volume_20d threshold (e.g., >5M shares → liquid)
          2. Add sector-based proxies (e.g., NIFTY50 constituents → liquid)
          3. Handle illiquid FNO contracts (weekly expiries, deep OTM)
          4. Consider spread % from instrument_cache as liquidity signal

        Current heuristic:
          - FNO → liquid (rough proxy)
          - Cash → mid (conservative)
          - Unknown → default_tier
        """
        try:
            row = self._cache.get_by_symbol(symbol)
        except InstrumentNotFoundError:
            return self._cfg.default_tier
        if row.is_fno:
            return "liquid" if "liquid" in self._cfg.tiers else self._cfg.default_tier
        return "mid" if "mid" in self._cfg.tiers else self._cfg.default_tier

    def apply(self, symbol: str, side: str, price: float) -> float:
        """
        SE3-SE5: return adverse-slipped fill price for `price`.

        Args:
            symbol: NSE trading symbol; tier comes from InstrumentCache.
            side:   "BUY" or "SELL" (case-insensitive).
            price:  pre-slippage price (limit, trigger, or LTP-gated value).

        Returns:
            Adjusted fill price, tick-rounded if symbol is known.
        """
        side_u = side.upper()
        if side_u not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {side!r}")
        if price <= 0:
            return price  # MARKET-with-no-LTP path; nothing to slip.

        tier = self.tier_for(symbol)
        bps = self._cfg.tiers[tier].slippage_bps
        delta = price * (bps / 10_000.0)
        adjusted = price + delta if side_u == "BUY" else price - delta

        tick = self._tick_size(symbol)
        if tick is not None and tick > 0:
            # FIX-050: Sub-tick guard - if delta is smaller than half a tick,
            # skip rounding to avoid artificially inflating slippage.
            # Example: price=5.00, delta=0.0075, tick=0.05 → skip rounding,
            # return 5.0075 instead of 5.05 (which would be 10x the intended slip).
            if abs(delta) < (tick / 2.0):
                if self._log:
                    self._log.debug(
                        f"SlippageEngine: sub-tick guard activated for {symbol} "
                        f"(delta={delta:.6f} < tick/2={tick/2.0:.6f}), "
                        f"returning {adjusted:.4f} without rounding"
                    )
                return adjusted

            # SE3 conservatism: round AWAY from price for the trader.
            # BUY rounds up to next tick; SELL rounds down to prev tick.
            if side_u == "BUY":
                adjusted = _round_up_to_tick(adjusted, tick)
            else:
                adjusted = _round_down_to_tick(adjusted, tick)
        return adjusted

    def _tick_size(self, symbol: str) -> Optional[float]:
        try:
            return self._cache.get_by_symbol(symbol).tick_size
        except InstrumentNotFoundError:
            return None


def _round_up_to_tick(value: float, tick: float) -> float:
    """
    Round value UP to nearest tick_size multiple using decimal.Decimal.

    FIX-014: Replaces float arithmetic + delta-check with Decimal ROUND_CEILING
    to eliminate precision drift (e.g., 10.9999999998 → 11.00 for tick=0.05).
    """
    d_value = Decimal(str(value))
    d_tick = Decimal(str(tick))
    # Divide and round up to nearest integer multiple
    d_result = (d_value / d_tick).quantize(Decimal('1'), rounding=ROUND_CEILING) * d_tick
    return float(d_result)


def _round_down_to_tick(value: float, tick: float) -> float:
    """
    Round value DOWN to nearest tick_size multiple using decimal.Decimal.

    FIX-014: Replaces float arithmetic + delta-check with Decimal ROUND_FLOOR
    to eliminate precision drift.
    """
    d_value = Decimal(str(value))
    d_tick = Decimal(str(tick))
    # Divide and round down to nearest integer multiple
    d_result = (d_value / d_tick).quantize(Decimal('1'), rounding=ROUND_FLOOR) * d_tick
    return float(d_result)


def _round_nearest_to_tick(value: float, tick: float) -> float:
    """
    Round value to the NEAREST tick_size multiple using decimal.Decimal.

    FIX-181: companion to _round_up_to_tick / _round_down_to_tick for entry
    and TGT LIMIT prices, where no directional bias is required — only that
    the price lands on a valid exchange tick (Zerodha rejects off-tick prices).
    """
    d_value = Decimal(str(value))
    d_tick = Decimal(str(tick))
    d_result = (d_value / d_tick).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * d_tick
    return float(d_result)
