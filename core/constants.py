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
# position" (Rama, 30-Jul). THE SINGLE SOURCE (red-team G1): there is no second
# copy to drift.
#
# ⛔ FIVE call sites read this ONE name — CHANGING THE SET CHANGES ALL FIVE:
#     1. capital/kill_switch.py    :1585  — emergency, local pass
#     2. capital/kill_switch.py    :1683  — emergency, broker sweep
#     3. orders/eod_squareoff.py   :1085  — scheduled 15:17 EOD6 pass
#     4. orders/eod_squareoff.py   :1453  — scheduled FIX-182 residual pass
#     5. orders/order_reconciler.py:2174  — CHECK2 inflight-orphan (joined #2b)
# (File+site names are the durable part; the line refs are a 02-Aug-2026
# snapshot and will drift.)
# ⚠️ COUNT CORRECTED 02-Aug-2026 (#2c-R): this comment used to say "the two
# emergency sites ... and the two scheduled passes". That predated the
# reconciler joining at #2b, so it UNDERSTATED THE BLAST RADIUS BY ONE. A
# comment that understates blast radius is the exact truth-telling defect the
# integrity campaign exists to remove — hence this correction.
#
# ⚠️ MEMBERSHIP IS NOT "WILL BE SOLD" AT SITE 5. CO is a member of this set,
# but order_reconciler REFUSES a CO position in a site-LOCAL branch placed
# BEFORE its membership test (#2c-R): Audit 3.1 — a CO position cannot be
# squared by a reverse order at all, and that path has no parent bracket id.
# ⛔ Do NOT "simplify" that by removing CO from this set: it would silently
# change the other four sites.
#
# A product OUTSIDE this set and not
# "CNC" (NULL/NRML/anything unrecognised) is flattened LOUDLY (CRITICAL) by
# the emergency sites — G2: never soften, never silently spare the unknown.
EMERGENCY_FLATTEN_PRODUCTS: Final[frozenset] = frozenset({"MIS", "CO"})
