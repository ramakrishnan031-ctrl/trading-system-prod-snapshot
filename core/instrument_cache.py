"""
core/instrument_cache.py -- Trading System v2

Purpose:
    In-memory read-only cache of instrument master data loaded from
    config/instruments.csv.  Provides O(1) lookups by symbol and by
    instrument_token.  Never mutates after load().

Locked Design Decisions:
    IC1  -- Load from config/instruments.csv (CSV, not YAML/DB).
             Columns: symbol, instrument_token, exchange, lot_size,
             tick_size, is_fno, sector.
    IC2  -- Raises InstrumentNotFoundError (core/exceptions.py) on unknown
             symbol/token lookups. Not KeyError.
    IC3  -- InstrumentRow: frozen dataclass with all 7 columns typed.
    IC4  -- has(symbol) -> bool for cheap presence check.
    IC5  -- get_by_symbol(symbol) -> InstrumentRow (IC2 on miss).
    IC6  -- get_by_token(token: int) -> InstrumentRow (IC2 on miss).
    IC7  -- all_rows() -> list[InstrumentRow] in load order.
    IC8  -- sector(symbol) -> str; returns "UNKNOWN" if symbol missing
             (never raises -- used in risk_engine lambdas).
    IC9  -- lot_size(symbol) -> int; raises InstrumentNotFoundError on miss.
    IC10 -- tick_size(symbol) -> float; raises InstrumentNotFoundError on miss.
    IC11 -- token_map() -> dict[int, str] (token -> symbol); used by CandleStore.
    IC12 -- load(path) classmethod; raises ConfigMissingError if file absent,
             ConfigSchemaError if any row is malformed.
    IC13 -- Layer 0-1 (core/). Imports: stdlib + core.exceptions only.
    IC14 -- Thread-safe after construction: internal dicts are read-only.
    IC15 -- count() -> int: number of instruments loaded.

What This Module Does NOT Do:
    - Does not download from Kite API (scripts/refresh_instruments.py does that)
    - Does not persist to DB or state_store
    - Does not listen to market data ticks
    - Does not validate whether symbols are tradeable (startup_checks does that)
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from core.exceptions import ConfigMissingError, ConfigSchemaError, InstrumentNotFoundError


# ─────────────────────────────────────────────────────────────────────────────
# InstrumentRow
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class InstrumentRow:
    """
    Immutable snapshot of one row from instruments.csv (IC3).

    Fields mirror the CSV columns exactly.  All numeric types are
    pre-converted at load time so callers never need to cast.
    """
    symbol:           str    # NSE trading symbol e.g. "RELIANCE"
    instrument_token: int    # Kite instrument token (integer)
    exchange:         str    # "NSE" | "BSE"
    lot_size:         int    # 1 for equity; F&O uses contract lot
    tick_size:        float  # Minimum price movement e.g. 0.05
    is_fno:           bool   # True if this instrument has F&O
    sector:           str    # Sector name for risk bucketing; "" if unknown


# ─────────────────────────────────────────────────────────────────────────────
# InstrumentCache
# ─────────────────────────────────────────────────────────────────────────────

class InstrumentCache:
    """
    In-memory O(1) lookup table for instrument master data (IC1-IC15).

    Typical usage::
        cache = InstrumentCache.load(Path("config/instruments.csv"))
        row = cache.get_by_symbol("RELIANCE")
        lot = cache.lot_size("TCS")
        token_map = cache.token_map()   # wire to candle_store
    """

    def __init__(self, rows: List[InstrumentRow]) -> None:
        """
        Build indexes from a list of InstrumentRow objects.

        Called by load(); not typically called directly.
        """
        self._rows: List[InstrumentRow] = list(rows)
        self._by_symbol: Dict[str, InstrumentRow] = {r.symbol: r for r in rows}
        self._by_token: Dict[int, InstrumentRow] = {r.instrument_token: r for r in rows}

    # ── classmethod constructor ───────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path) -> "InstrumentCache":
        """
        Parse instruments.csv and return a populated cache (IC12).

        Args:
            path: Absolute or relative path to instruments.csv.

        Raises:
            ConfigMissingError: file does not exist.
            ConfigSchemaError: a required column is absent or a row has
                               a type-conversion error.
        """
        if not path.exists():
            raise ConfigMissingError(
                f"instruments.csv not found at {path}",
                path=str(path),
            )

        required_columns = {
            "symbol", "instrument_token", "exchange",
            "lot_size", "tick_size", "is_fno", "sector",
        }

        rows: List[InstrumentRow] = []

        try:
            with open(path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                if reader.fieldnames is None:
                    raise ConfigSchemaError(
                        "instruments.csv is empty or has no header row",
                        path=str(path),
                    )
                missing = required_columns - set(reader.fieldnames)
                if missing:
                    raise ConfigSchemaError(
                        f"instruments.csv missing required columns: {sorted(missing)}",
                        path=str(path),
                        missing_columns=sorted(missing),
                    )

                for lineno, raw in enumerate(reader, start=2):
                    try:
                        row = InstrumentRow(
                            symbol=raw["symbol"].strip(),
                            instrument_token=int(raw["instrument_token"]),
                            exchange=raw["exchange"].strip(),
                            lot_size=int(raw["lot_size"]),
                            tick_size=float(raw["tick_size"]),
                            is_fno=raw["is_fno"].strip().lower() in ("true", "1", "yes"),
                            sector=raw["sector"].strip(),
                        )
                    except (KeyError, ValueError) as exc:
                        raise ConfigSchemaError(
                            f"instruments.csv line {lineno}: {exc}",
                            path=str(path),
                            line=lineno,
                        ) from exc

                    if not row.symbol:
                        raise ConfigSchemaError(
                            f"instruments.csv line {lineno}: symbol is empty",
                            path=str(path),
                            line=lineno,
                        )
                    if row.lot_size < 1:
                        raise ConfigSchemaError(
                            f"instruments.csv line {lineno}: lot_size must be >= 1, "
                            f"got {row.lot_size} for {row.symbol}",
                            path=str(path),
                            line=lineno,
                        )
                    if row.tick_size <= 0:
                        raise ConfigSchemaError(
                            f"instruments.csv line {lineno}: tick_size must be > 0, "
                            f"got {row.tick_size} for {row.symbol}",
                            path=str(path),
                            line=lineno,
                        )
                    rows.append(row)

        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigMissingError(
                f"Could not read instruments.csv at {path}: {exc}",
                path=str(path),
            ) from exc

        return cls(rows)

    # ── public API ─────────────────────────────────────────────────────────────

    def has(self, symbol: str) -> bool:
        """Return True if symbol is in the cache (IC4). Never raises."""
        return symbol in self._by_symbol

    def get_by_symbol(self, symbol: str) -> InstrumentRow:
        """
        Return the InstrumentRow for the given symbol (IC5).

        Raises:
            InstrumentNotFoundError: symbol not in cache.
        """
        row = self._by_symbol.get(symbol)
        if row is None:
            raise InstrumentNotFoundError(
                f"Symbol {symbol!r} not found in instrument cache",
                symbol=symbol,
            )
        return row

    def get_by_token(self, token: int) -> InstrumentRow:
        """
        Return the InstrumentRow for the given instrument_token (IC6).

        Raises:
            InstrumentNotFoundError: token not in cache.
        """
        row = self._by_token.get(token)
        if row is None:
            raise InstrumentNotFoundError(
                f"instrument_token {token} not found in instrument cache",
                token=token,
            )
        return row

    def all_rows(self) -> List[InstrumentRow]:
        """Return all rows in load order (IC7). Returns a copy."""
        return list(self._rows)

    def sector(self, symbol: str) -> str:
        """
        Return the sector for a symbol (IC8).

        Returns "UNKNOWN" if symbol is not in cache (never raises).
        Returns the sector value from instruments.csv, or "UNKNOWN" if blank.
        """
        row = self._by_symbol.get(symbol)
        if row is None:
            return "UNKNOWN"
        return row.sector if row.sector else "UNKNOWN"

    def lot_size(self, symbol: str) -> int:
        """
        Return the lot_size for a symbol (IC9).

        Raises:
            InstrumentNotFoundError: symbol not in cache.
        """
        return self.get_by_symbol(symbol).lot_size

    def tick_size(self, symbol: str) -> float:
        """
        Return the tick_size for a symbol (IC10).

        Raises:
            InstrumentNotFoundError: symbol not in cache.
        """
        return self.get_by_symbol(symbol).tick_size

    def token_map(self) -> Dict[int, str]:
        """
        Return a dict mapping instrument_token -> symbol (IC11).

        Used to wire CandleStore.set_token_map() in main.py.
        """
        return {r.instrument_token: r.symbol for r in self._rows}

    def count(self) -> int:
        """Return the number of instruments loaded (IC15)."""
        return len(self._rows)
