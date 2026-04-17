"""
tests/integration/sim_kite.py -- Trading System v2

SimKiteClient: scriptable, stateful quote provider for integration tests.
Injected into ZerodhaAdapter as the quote_provider callable (paper_mode=True).

Design (IT1, IT2):
  - set_quote(symbol, ...) to configure per-symbol quotes
  - set_rich_quote(symbol, ltp) for a screener-passing quote in one call
  - get_quote_fn() returns the callable ZerodhaAdapter expects
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from broker.zerodha_adapter import Quote

_IST = timezone(timedelta(hours=5, minutes=30), name="IST")


@dataclass
class _QuoteSpec:
    """Internal storage for a configured test quote."""
    symbol: str
    ltp: float
    bid: float
    ask: float
    volume: int
    vwap: Optional[float]
    atr: Optional[float]
    rsi: Optional[float]
    avg_volume_20d: Optional[float]
    day_high: Optional[float]
    day_low: Optional[float]
    open_price: Optional[float]
    circuit_state: str


class SimKiteClient:
    """
    Scriptable quote provider for paper-mode integration tests.

    ZerodhaAdapter.get_quote() delegates to the callable returned by
    get_quote_fn() when paper_mode=True.  SimKiteClient lets each test
    configure any combination of per-symbol quotes.

    Usage::
        sim = SimKiteClient()
        sim.set_rich_quote("RELIANCE", ltp=100.0)   # screener will pass
        adapter = ZerodhaAdapter(
            kite_client=None, ...,
            paper_mode=True,
            quote_provider=sim.get_quote_fn(),
        )
    """

    _DEFAULT_LTP: float = 100.0
    _DEFAULT_BID: float = 99.9
    _DEFAULT_ASK: float = 100.1
    _DEFAULT_VOLUME: int = 100_000

    def __init__(self) -> None:
        self._quotes: Dict[str, _QuoteSpec] = {}

    # ------------------------------------------------------------------
    # Configuration API
    # ------------------------------------------------------------------

    def set_quote(
        self,
        symbol: str,
        ltp: float,
        bid: float,
        ask: float,
        volume: int,
        *,
        vwap: Optional[float] = None,
        atr: Optional[float] = None,
        rsi: Optional[float] = None,
        avg_volume_20d: Optional[float] = None,
        day_high: Optional[float] = None,
        day_low: Optional[float] = None,
        open_price: Optional[float] = None,
        circuit_state: str = "",
    ) -> None:
        """Configure a quote for a symbol. Replaces any previous setting."""
        self._quotes[symbol] = _QuoteSpec(
            symbol=symbol,
            ltp=ltp,
            bid=bid,
            ask=ask,
            volume=volume,
            vwap=vwap,
            atr=atr,
            rsi=rsi,
            avg_volume_20d=avg_volume_20d,
            day_high=day_high if day_high is not None else ltp * 1.02,
            day_low=day_low if day_low is not None else ltp * 0.98,
            open_price=open_price if open_price is not None else ltp * 0.985,
            circuit_state=circuit_state,
        )

    def set_rich_quote(self, symbol: str, ltp: float = 100.0) -> None:
        """
        Set a quote with rich market data that lets the secondary screener pass.

        Steps analysis for vwap_bounce_long (LONG, INTRADAY):
          vol_surge=4x(>1.5)=15, vwap_pos=10, atr=2%>0.5%=10, rsi=55=10,
          price_action~0.75=11, sector_miss=5, time_10:30=4,
          spread=0.4%<0.5%=5, circuit=10, age(10s)=10 -> total ~90 HIGH
        """
        self.set_quote(
            symbol,
            ltp=ltp,
            bid=ltp * 0.998,
            ask=ltp * 1.002,
            volume=2_000_000,
            vwap=ltp * 0.95,
            atr=ltp * 0.02,
            rsi=55.0,
            avg_volume_20d=500_000.0,
            day_high=ltp * 1.02,
            day_low=ltp * 0.98,
            open_price=ltp * 0.985,
            circuit_state="",
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_market_data(self, symbol: str) -> Optional[dict]:
        """
        Return the rich market_data dict for a symbol, or None if not set.
        Used to patch SecondaryScreener._build_market_data in tests.
        """
        spec = self._quotes.get(symbol)
        if spec is None:
            return None
        return {
            "ltp": spec.ltp,
            "bid": spec.bid,
            "ask": spec.ask,
            "volume": spec.volume,
            "vwap": spec.vwap,
            "atr": spec.atr,
            "rsi": spec.rsi,
            "avg_volume_20d": spec.avg_volume_20d,
            "day_high": spec.day_high,
            "day_low": spec.day_low,
            "open": spec.open_price,
            "circuit_state": spec.circuit_state,
            "sector": None,
            "prev_close": spec.ltp,
        }

    def get_quote_fn(self) -> Callable[[List[str]], Dict[str, Quote]]:
        """
        Return a callable matching ZerodhaAdapter's quote_provider signature:
            fn(symbols: list[str]) -> dict[str, Quote]
        """
        def _fn(symbols: List[str]) -> Dict[str, Quote]:
            result: Dict[str, Quote] = {}
            for sym in symbols:
                spec = self._quotes.get(sym)
                if spec is not None:
                    result[sym] = Quote(
                        symbol=sym,
                        last_price=spec.ltp,
                        bid=spec.bid,
                        ask=spec.ask,
                        volume=spec.volume,
                        ts=datetime.now(tz=_IST),
                    )
                else:
                    result[sym] = Quote(
                        symbol=sym,
                        last_price=self._DEFAULT_LTP,
                        bid=self._DEFAULT_BID,
                        ask=self._DEFAULT_ASK,
                        volume=self._DEFAULT_VOLUME,
                        ts=datetime.now(tz=_IST),
                    )
            return result

        return _fn
