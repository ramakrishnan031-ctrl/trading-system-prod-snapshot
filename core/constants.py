"""Shared constants used across capital, orders, and broker modules."""

from typing import Final

PRODUCT_TO_INTENT: Final[dict[str, str]] = {
    "MIS": "INTRADAY",
    "CO": "COVER_ORDER",
    "CNC": "DELIVERY",
    "NRML": "DELIVERY",
}

# Q4 / ledger #2 (the buy-day product filter, 02-Aug-2026): the ONLY products
# an emergency (HARD_KILL) flatten may sell. Delivery (CNC) SURVIVES the kill —
# the Q4 invariant is "no live INTRADAY position", NOT "no live broker
# position" (Rama, 30-Jul). THE SINGLE SOURCE (red-team G1): the two emergency
# sites in capital/kill_switch.py and the two scheduled intraday passes in
# orders/eod_squareoff.py (15:17 EOD6 + FIX-182 residual) all reference THIS
# name — there is no second copy to drift. A product OUTSIDE this set and not
# "CNC" (NULL/NRML/anything unrecognised) is flattened LOUDLY (CRITICAL) by
# the emergency sites — G2: never soften, never silently spare the unknown.
EMERGENCY_FLATTEN_PRODUCTS: Final[frozenset] = frozenset({"MIS", "CO"})
