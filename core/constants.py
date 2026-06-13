"""Shared constants used across capital, orders, and broker modules."""

from typing import Final

PRODUCT_TO_INTENT: Final[dict[str, str]] = {
    "MIS": "INTRADAY",
    "CO": "COVER_ORDER",
    "CNC": "DELIVERY",
    "NRML": "DELIVERY",
}
