"""
broker/product_resolver.py -- Trading System v2

Purpose:
    Single source of truth for translating semantic product intents into
    broker-specific product codes. Eliminates hardcoded "MIS"/"CNC"/"CO"
    strings from the rest of the codebase (audit Issue #29).

Locked Design Decisions:
    PR1  -- Single source of truth for intent -> broker product code.
    PR2  -- Four valid intents: INTRADAY, DELIVERY, COVER_ORDER, BRACKET_ORDER.
    PR3  -- Zerodha: INTRADAY->"MIS", DELIVERY->"CNC", COVER_ORDER->"CO",
            BRACKET_ORDER->""  (empty = not supported, raises ProductNotSupportedError).
    PR4  -- resolve(intent, broker='zerodha') -> str; raises on missing/empty mapping.
    PR5  -- intent_for(broker_code, broker='zerodha') -> str (reverse lookup for reconciler).
    PR6  -- Validates at construction: non-empty map; each broker entry must be a dict.
    PR7  -- Raises ProductNotSupportedError (BrokerError, SEVERITY='ERROR') on bad mapping.
    PR8  -- product_map: dict[str, dict[str, str]] injected from cfg.system.product_map.
    PR9  -- Layer 2 (broker/). Imports: stdlib + core.exceptions only.
    PR10 -- No silent fallbacks. Missing or empty mapping raises immediately (Rule 14).

What This Module Does NOT Do:
    - Does not read config files (caller injects product_map dict)
    - Does not hardcode any product code
    - Does not implement retry or backoff logic
    - Does not support BSE segment codes (NSE equity scope only for now)
"""
from __future__ import annotations

from core.exceptions import ProductNotSupportedError

# ─────────────────────────────────────────────────────────────────────────────
# Constants (PR2)
# ─────────────────────────────────────────────────────────────────────────────

_VALID_INTENTS: frozenset[str] = frozenset({
    "INTRADAY",
    "DELIVERY",
    "COVER_ORDER",
    "BRACKET_ORDER",
})


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

class ProductResolver:
    """
    Translates semantic product intents into broker-specific product codes.

    Constructed once at startup with the product_map extracted from
    cfg.system.product_map (a dict[str, dict[str, str]] where the outer
    key is the broker name and the inner dict maps intent -> code).

    Usage::
        resolver = ProductResolver(cfg.system.product_map)
        code = resolver.resolve("INTRADAY")          # -> "MIS"
        code = resolver.resolve("DELIVERY", "zerodha")  # -> "CNC"
        intent = resolver.intent_for("MIS")          # -> "INTRADAY"
    """

    def __init__(self, product_map: dict[str, dict[str, str]]) -> None:
        """
        Args:
            product_map: broker -> {intent -> broker_code} mapping.
                         Typically cfg.system.product_map from system_config.yaml.

        Raises:
            ValueError: if product_map is empty, or any broker entry is not a dict.
        """
        if not product_map:
            raise ValueError(
                "product_map must not be empty; "
                "check system_config.yaml product_map section"
            )
        for broker, mapping in product_map.items():
            if not isinstance(mapping, dict):
                raise ValueError(
                    f"product_map[{broker!r}] must be a dict mapping "
                    f"intent -> code, got {type(mapping).__name__}"
                )

        # Defensive copy so mutations to the injected dict don't affect us (PR9).
        self._map: dict[str, dict[str, str]] = {
            broker: dict(mapping)
            for broker, mapping in product_map.items()
        }

    def resolve(self, intent: str, broker: str = "zerodha") -> str:
        """
        Return the broker-specific product code for a semantic intent.

        Args:
            intent: one of INTRADAY | DELIVERY | COVER_ORDER | BRACKET_ORDER (PR2).
            broker: broker identifier key in product_map (default "zerodha").

        Returns:
            Non-empty broker product code string (e.g. "MIS", "CNC", "CO").

        Raises:
            ValueError: intent is not one of the four valid intents (PR2).
            ProductNotSupportedError: broker not in map, OR mapped code is
                                      empty/None (PR4, PR10).
        """
        if intent not in _VALID_INTENTS:
            raise ValueError(
                f"Unknown product intent: {intent!r}. "
                f"Valid intents: {sorted(_VALID_INTENTS)}"
            )

        if broker not in self._map:
            raise ProductNotSupportedError(
                f"Broker {broker!r} not found in product_map",
                intent=intent,
                broker=broker,
                available_intents=sorted(_VALID_INTENTS),
            )

        code = self._map[broker].get(intent)
        if not code:  # None or empty string -> not supported (PR3, PR10)
            available = sorted(
                k for k, v in self._map[broker].items() if v
            )
            raise ProductNotSupportedError(
                f"Intent {intent!r} is not supported by broker {broker!r} "
                f"(mapped to empty/null code)",
                intent=intent,
                broker=broker,
                available_intents=available,
            )

        return code

    def intent_for(self, broker_code: str, broker: str = "zerodha") -> str:
        """
        Reverse lookup: return the semantic intent for a broker-specific code (PR5).

        Used by order_reconciler to map broker positions back to system taxonomy.

        Args:
            broker_code: broker-specific product code (e.g. "MIS", "CNC", "CO").
            broker:      broker identifier key in product_map (default "zerodha").

        Returns:
            Semantic intent string (e.g. "INTRADAY").

        Raises:
            ValueError: broker not in map, OR broker_code not found for that broker.
        """
        if broker not in self._map:
            raise ValueError(
                f"Unknown broker: {broker!r}. "
                f"Known brokers: {sorted(self._map)}"
            )

        for intent, code in self._map[broker].items():
            if code == broker_code:
                return intent

        known_codes = sorted(v for v in self._map[broker].values() if v)
        raise ValueError(
            f"Broker code {broker_code!r} not found for broker {broker!r}. "
            f"Known codes: {known_codes}"
        )

    @property
    def known_intents(self) -> frozenset[str]:
        """Return the set of valid semantic intents (PR2). Useful for validation."""
        return _VALID_INTENTS
