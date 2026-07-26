"""orders/closure_classifier.py — CHECK1's evidence classifier (CW-C, 26-Jul-2026).

PURE. Takes evidence, returns a verdict, touches nothing — no DB, no broker, no
logging, no clock. That is the entire point: the decision CHECK1 gets wrong today is
made here, in isolation, where it can be exhaustively tested.

⭐ FIRST PRINCIPLE (core/closure_source.py, docs/closure_source_contract.md):
   SUPPRESS THE CRITICAL ONLY ON POSITIVE EVIDENCE THAT ONE OF OUR OWN LEGS ACCOUNTS
   FOR THE CLOSE. NEVER ON ABSENCE OF EVIDENCE.

Everything unknown — broker unreachable, orders unreadable, cancel reason
unrecognised, sources disagreeing — resolves to EXTERNAL_UNATTRIBUTED at CRITICAL.

WHY THIS EXISTS. `order_reconciler._check1_manual_close` concluded "Position closed
externally" from ONE fact: the symbol is absent from get_positions(). Measured
26-Jul: 35 of 41 CLOSED_MANUAL trades had one of our OWN legs COMPLETE, and all four
CRITICAL emails ever sent were our own orders filling. The information needed to be
right was already in hand and was discarded in three separate places.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.closure_source import (
    EV_BROKER_ORDER_ID, EV_LOCAL_AND_BROKER, EV_LOCAL_COMPLETE, EV_MID_FILL,
    EXTERNAL_UNATTRIBUTED, OWN_EOD, OWN_SL, OWN_TGT,
)

#: leg -> closure_source. A GTT leg is an SL or TGT by REASON (its broker-managed
#: nature is the MECHANISM axis), so no GTT entry belongs here.
_LEG_TO_SOURCE = {"SL": OWN_SL, "TGT": OWN_TGT, "EOD": OWN_EOD}


@dataclass(frozen=True)
class OurLeg:
    """One of our own exit orders for the trade whose position vanished."""
    order_id: str
    leg: str                 # "SL" | "TGT" | "EOD"
    status: str              # local orders.status
    mid_fill: bool = False   # broker refused the cancel: "being processed"


@dataclass(frozen=True)
class Evidence:
    """Everything CHECK1 gathers BEFORE it decides. Gathered, then classified."""
    our_legs: tuple[OurLeg, ...] = ()
    #: broker order_ids that appear in get_trades() for this symbol on the exit side.
    #: EMPTY is ambiguous by itself — see broker_trades_read_ok.
    broker_filled_order_ids: frozenset[str] = frozenset()
    #: False when get_trades() failed OR is structurally unavailable (paper returns []).
    broker_trades_read_ok: bool = True
    #: False when the local orders read failed.
    orders_read_ok: bool = True
    #: A cancel failed for a reason we do not recognise — explicitly NOT evidence.
    ambiguous_cancel: bool = False


@dataclass(frozen=True)
class Verdict:
    closure_source: str
    severity: str                      # "INFO" | "CRITICAL"
    rung: Optional[str] = None         # which precedence rung decided it
    leg: Optional[str] = None          # the leg we attribute it to
    contradiction: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_ours(self) -> bool:
        return self.closure_source != EXTERNAL_UNATTRIBUTED


_EXTERNAL = "external"


def _external(*notes: str, contradiction: bool = False) -> Verdict:
    return Verdict(
        closure_source=EXTERNAL_UNATTRIBUTED,
        severity="CRITICAL",
        rung=None,
        leg=None,
        contradiction=contradiction,
        notes=tuple(notes),
    )


def classify(ev: Evidence) -> Verdict:
    """Apply the ordered precedence ladder. First match wins; ties are impossible
    because the sources are ORDERED, not scored.

    ⚠️ THE CONTRADICTION RULE runs BEFORE precedence: if two sources that SPOKE
    disagree about WHICH leg closed the position, that is not a tie and must not
    resolve to the higher-precedence source. Precedence orders sources that are
    SILENT; it never overrules one that SPOKE.
    """
    # A read failure is never evidence. Fail loud, per the first principle.
    if not ev.orders_read_ok:
        return _external("orders_read_failed")
    if ev.ambiguous_cancel:
        return _external("ambiguous_cancel_reason")

    legs = [l for l in ev.our_legs if l.leg in _LEG_TO_SOURCE]

    # Which legs does each source NAME? A source that names nothing is SILENT.
    named_by_broker = {
        l.leg for l in legs
        if ev.broker_trades_read_ok and l.order_id in ev.broker_filled_order_ids
    }
    named_by_local = {l.leg for l in legs if l.status == "COMPLETE"}
    named_by_midfill = {l.leg for l in legs if l.mid_fill}

    spoke = [s for s in (named_by_broker, named_by_local, named_by_midfill) if s]

    # ⚠️ CONTRADICTION: two sources spoke and named DIFFERENT legs.
    for i in range(len(spoke)):
        for j in range(i + 1, len(spoke)):
            if spoke[i] != spoke[j]:
                return _external(
                    "sources_disagree",
                    f"named={sorted(set().union(*spoke))}",
                    contradiction=True,
                )
    # A single source naming two different legs is equally a disagreement.
    for s in spoke:
        if len(s) > 1:
            return _external("multiple_legs_named", f"legs={sorted(s)}",
                             contradiction=True)

    # ── the ladder ────────────────────────────────────────────────────────────
    if named_by_broker:
        leg = next(iter(named_by_broker))
        rung = EV_LOCAL_AND_BROKER if leg in named_by_local else EV_BROKER_ORDER_ID
        return Verdict(_LEG_TO_SOURCE[leg], "INFO", rung, leg)

    if named_by_local:
        leg = next(iter(named_by_local))
        return Verdict(_LEG_TO_SOURCE[leg], "INFO", EV_LOCAL_COMPLETE, leg)

    if named_by_midfill:
        leg = next(iter(named_by_midfill))
        return Verdict(_LEG_TO_SOURCE[leg], "INFO", EV_MID_FILL, leg)

    # Nothing of ours accounts for it. THE DEFAULT, and the true-positive path.
    return _external("no_own_leg_accounts_for_close")
