"""
regime/ — Trading System v2 · V3 03.02 Market Regime Engine

A NEW, index-level market-regime package (Phase-0 grep negative → genuinely new).
Emits a three-axis regime (direction / volatility / day-type) with ordinal
confidence + a positive-confirmation-only extreme flag. SHADOW — gates nothing
in this step. Reuses the Common-Utilities substrate (core.candle_math) and the
existing OhlcFetcher for index candles (by config token); no duplicated ATR /
candle / session / calendar / fetch logic.

Public API:
    build_market_regime(...)      → a wired MarketRegimeEngine
    MarketRegimeShadowRunner      → periodic shadow compute+log+persist (default-off)
    RegimeState / AxisResult      → the emitted value objects
"""
from __future__ import annotations

from regime.engine import MarketRegimeEngine
from regime.models import AxisResult, RegimeState
from regime.runner import MarketRegimeShadowRunner, read_persisted_regime

__all__ = [
    "build_market_regime",
    "MarketRegimeEngine",
    "MarketRegimeShadowRunner",
    "read_persisted_regime",
    "RegimeState",
    "AxisResult",
]


def build_market_regime(
    *,
    config,
    fetcher,
    logger,
    now_fn,
    market_windows,
    exchange_status_fn=None,
):
    """Construct a wired MarketRegimeEngine. `fetcher` is the existing
    sr_detector OhlcFetcher (reused for index candles by config token).
    `exchange_status_fn` is an OPTIONAL positive-confirmation halt feed — when
    None, extreme_flag stays FALSE and a CRITICAL is logged (absence is not a
    halt)."""
    return MarketRegimeEngine(
        config=config,
        fetcher=fetcher,
        logger=logger,
        now_fn=now_fn,
        market_windows=market_windows,
        exchange_status_fn=exchange_status_fn,
    )
