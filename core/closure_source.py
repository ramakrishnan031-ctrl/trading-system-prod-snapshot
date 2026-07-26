"""THE CANONICAL CLOSURE VOCABULARY — one location, for CHECK1, W8 and anything later.

⭐ THIS MODULE IS THE SINGLE SOURCE OF TRUTH. Do not restate these values, the
precedence order, or the contradiction rule anywhere else. Restating them in two
modules IS the divergence W8 (`trades.closure_source`, P3-r10) exists to retire —
`reports/daily_trade_review.py:34-46` already documents the 4-way collision
(daily-EOD / operator-manual / RMS / kill-flatten) that the absence of this
vocabulary caused.

The human-facing contract, with the reasoning, is `docs/closure_source_contract.md`.
`tests/unit/test_closure_source_contract.py` pins the two together so they cannot
drift apart.

═══════════════════════════════════════════════════════════════════════════════
FIRST PRINCIPLE — everything below is subordinate to it
═══════════════════════════════════════════════════════════════════════════════
⭐ SUPPRESS THE CRITICAL ONLY ON POSITIVE EVIDENCE THAT ONE OF OUR OWN LEGS
   ACCOUNTS FOR THE CLOSE. NEVER ON ABSENCE OF EVIDENCE.

Broker unreachable · `orders` unreadable · cancel reason unrecognised · match
ambiguous  ⇒  EXTERNAL_UNATTRIBUTED at CRITICAL. Every unknown resolves TOWARDS the
alert, never away from it. A design that reaches zero false positives by failing
quiet is the silent-failure pattern this project has hit seven times, and it would
be harder to detect than the noise it replaced.

═══════════════════════════════════════════════════════════════════════════════
TWO SEPARATE AXES — do not merge them
═══════════════════════════════════════════════════════════════════════════════
`closure_source`  = WHO/WHAT closed the position (the reason).
`exit_mechanism`  = HOW the order reached the broker (the venue).

They are orthogonal, and mixing them is how the current collision started. A GTT
leg IS an SL or a TGT for classification purposes; that it was broker-managed is a
MECHANISM, not a different reason. This matters for Slice 2.5, where every CNC exit
runs through a GTT: without the split, delivery exits would be unattributable by
reason, or SL/TGT would silently mean different things in MIS and CNC.
"""

from typing import Final

# ── closure_source — WHO closed it ────────────────────────────────────────────

OWN_SL: Final = "OWN_SL"                                # our stop-loss leg filled
OWN_TGT: Final = "OWN_TGT"                              # our target leg filled
OWN_EOD: Final = "OWN_EOD"                              # our EOD square-off leg filled
OWN_KILL: Final = "OWN_KILL"                            # kill-switch flatten / orphan sweep
EXTERNAL_UNATTRIBUTED: Final = "EXTERNAL_UNATTRIBUTED"  # nothing of ours accounts for it

CLOSURE_SOURCES: Final[frozenset[str]] = frozenset({
    OWN_SL, OWN_TGT, OWN_EOD, OWN_KILL, EXTERNAL_UNATTRIBUTED,
})

#: The subset that means "one of our own legs did it" — i.e. NOT an external close.
OWN_CLOSURE_SOURCES: Final[frozenset[str]] = frozenset({
    OWN_SL, OWN_TGT, OWN_EOD, OWN_KILL,
})

# ⛔ DELIBERATELY ABSENT: BROKER_RMS and OPERATOR_MANUAL as separate values.
# The 05-Jul-2026 audit established they are indistinguishable in-data ("an
# unauthorized manual close is indistinguishable in-data from a CO SL fire").
# Inventing two values we cannot populate would re-create the over-claim in a new
# place. ONE HONEST BUCKET BEATS TWO CONFIDENT GUESSES. If Kite ever exposes an RMS
# marker, it becomes a REFINEMENT of EXTERNAL_UNATTRIBUTED — never a retro-fit of
# the four OWN_* values.

# ── exit_mechanism — HOW it reached the broker ────────────────────────────────

MECH_LIMIT: Final = "LIMIT"        # a resting/marketable LIMIT we placed
MECH_MARKET: Final = "MARKET"      # a MARKET order we placed
MECH_GTT: Final = "GTT"            # broker-managed OCO GTT (delivery / Slice 2.5)
MECH_CO: Final = "CO"              # broker-managed cover-order SL
MECH_UNKNOWN: Final = "UNKNOWN"    # not determinable — never guessed

EXIT_MECHANISMS: Final[frozenset[str]] = frozenset({
    MECH_LIMIT, MECH_MARKET, MECH_GTT, MECH_CO, MECH_UNKNOWN,
})

# ── the precedence ladder — STRONGEST IDENTITY FIRST ──────────────────────────
# First match wins. Ties are IMPOSSIBLE because the sources are ORDERED, not scored.

EV_BROKER_ORDER_ID: Final = "broker_order_id"   # 1. get_trades() names the order that filled
EV_LOCAL_AND_BROKER: Final = "local_and_broker" # 2. local leg COMPLETE *and* broker corroborates
EV_LOCAL_COMPLETE: Final = "local_complete"     # 3. local leg COMPLETE alone (a cached broker fact)
EV_MID_FILL: Final = "mid_fill"                 # 4. cancel refused "being processed" — a leg was filling

EVIDENCE_PRECEDENCE: Final[tuple[str, ...]] = (
    EV_BROKER_ORDER_ID,
    EV_LOCAL_AND_BROKER,
    EV_LOCAL_COMPLETE,
    EV_MID_FILL,
)

# ⏳ RUNG 4 IS A CLAIM ABOUT *NOW*, AND IT EXPIRES.
# "Being processed" means a leg is filling at this instant — so the honest response
# is not to attribute but to WAIT: CHECK1 may DEFER for a bounded number of seconds
# (`order_reconciler.check1_mid_fill_defer_sec`) and let our own fill callback own
# the close, and with it the capital release. That is a TIMING mechanism, not a new
# rung.
# If the bound elapses with nothing terminal, the claim has gone STALE: the leg we
# were told was filling never landed. A stale claim stops counting as evidence, so
# rung 4 falls SILENT and the ladder drops through to whatever source can still
# speak — and to EXTERNAL_UNATTRIBUTED at CRITICAL if none can, which is the first
# principle again. Rungs 1-3 are unaffected: expiry retires a stale claim, it never
# destroys evidence that is still good.
# ⚠️ An expired deferral is the case where the design was WRONG, so it is never
# silent — the expiry is logged whatever the resulting verdict.

# ⚠️⚠️ THE CONTRADICTION RULE — this is the safety property, not a detail.
# If two sources DISAGREE about which leg closed the position, that is NOT a tie and
# it MUST NOT resolve to the higher-precedence source. It resolves to
# EXTERNAL_UNATTRIBUTED at CRITICAL. Silent disagreement between evidence sources is
# precisely how a wrong answer looks confident. Precedence orders sources that are
# SILENT; it never overrules a source that SPOKE.
