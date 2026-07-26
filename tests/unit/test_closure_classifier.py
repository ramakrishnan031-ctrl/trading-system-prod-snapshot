"""tests/unit/test_closure_classifier.py -- CW-C (26-Jul-2026).

The pure classifier behind CHECK1. Every test here is a decision CHECK1 gets wrong
today: it concluded "Position closed externally" from ONE fact (the symbol is absent
from get_positions()), while 35 of 41 CLOSED_MANUAL trades had one of our own legs
COMPLETE and all four CRITICAL emails ever sent were our own orders filling.

⭐ THE FIRST PRINCIPLE IS THE ACCEPTANCE TEST, not "no more false CRITICALs":
   a genuine external close must still fire, with EVERY evidence source degraded.

⚠️ THE CONTRADICTION RULE is the safety property. Silent disagreement between
evidence sources is precisely how a wrong answer looks confident, and downstream it
is indistinguishable from a correct one.

Run: python -m pytest tests/unit/test_closure_classifier.py -v
"""

from __future__ import annotations

import pytest

from core.closure_source import (
    EV_BROKER_ORDER_ID, EV_LOCAL_AND_BROKER, EV_LOCAL_COMPLETE, EV_MID_FILL,
    EXTERNAL_UNATTRIBUTED, OWN_EOD, OWN_SL, OWN_TGT,
)
from orders.closure_classifier import Evidence, OurLeg, classify


def _leg(leg="TGT", status="OPEN", oid="O1", mid=False) -> OurLeg:
    return OurLeg(order_id=oid, leg=leg, status=status, mid_fill=mid)


# ── the ladder, rung by rung ──────────────────────────────────────────────────

def test_rung1_broker_order_id_is_the_strongest_identity() -> None:
    """The broker NAMES the order that filled. Nothing beats direct identity."""
    v = classify(Evidence(our_legs=(_leg("TGT", "OPEN", "O1"),),
                          broker_filled_order_ids=frozenset({"O1"})))
    assert v.closure_source == OWN_TGT and v.severity == "INFO"
    assert v.rung == EV_BROKER_ORDER_ID and v.is_ours


def test_rung2_local_complete_and_broker_corroborates() -> None:
    v = classify(Evidence(our_legs=(_leg("SL", "COMPLETE", "O1"),),
                          broker_filled_order_ids=frozenset({"O1"})))
    assert v.closure_source == OWN_SL and v.rung == EV_LOCAL_AND_BROKER


def test_rung3_local_complete_alone() -> None:
    """⚠️ This rung MUST exist: get_trades() returns [] in paper, so without it
    paper would classify every own-leg close as EXTERNAL_UNATTRIBUTED."""
    v = classify(Evidence(our_legs=(_leg("EOD", "COMPLETE", "O1"),),
                          broker_trades_read_ok=False))
    assert v.closure_source == OWN_EOD and v.rung == EV_LOCAL_COMPLETE
    assert v.severity == "INFO"


def test_rung4_mid_fill_cancel_refusal_is_positive_evidence() -> None:
    """The broker refused the cancel BECAUSE our leg was filling. That is the
    signal CHECK1 computes, logs, and today throws away."""
    v = classify(Evidence(our_legs=(_leg("TGT", "OPEN", "O1", mid=True),)))
    assert v.closure_source == OWN_TGT and v.rung == EV_MID_FILL


def test_precedence_is_ordered_not_scored() -> None:
    """Broker + local agreeing on the same leg resolves once, without ambiguity."""
    v = classify(Evidence(
        our_legs=(_leg("TGT", "COMPLETE", "O1", mid=True),),
        broker_filled_order_ids=frozenset({"O1"})))
    assert v.closure_source == OWN_TGT and not v.contradiction


# ── ⚠️ the safety property: disagreement is CRITICAL, never a tie ─────────────

def test_two_sources_naming_different_legs_is_CRITICAL_not_the_higher_rung() -> None:
    """⭐ get_trades() names our SL; our local TGT row says COMPLETE. Both cannot be
    true. Taking rung 1 because it outranks rung 3 would report OWN_SL with total
    confidence while the book says otherwise."""
    v = classify(Evidence(
        our_legs=(_leg("SL", "OPEN", "O_SL"), _leg("TGT", "COMPLETE", "O_TGT")),
        broker_filled_order_ids=frozenset({"O_SL"})))
    assert v.closure_source == EXTERNAL_UNATTRIBUTED
    assert v.severity == "CRITICAL"
    assert v.contradiction is True


def test_one_source_naming_two_legs_is_also_a_disagreement() -> None:
    v = classify(Evidence(our_legs=(_leg("SL", "COMPLETE", "O1"),
                                    _leg("TGT", "COMPLETE", "O2"))))
    assert v.closure_source == EXTERNAL_UNATTRIBUTED and v.contradiction


def test_midfill_disagreeing_with_broker_is_CRITICAL() -> None:
    v = classify(Evidence(
        our_legs=(_leg("SL", "OPEN", "O_SL", mid=True), _leg("TGT", "OPEN", "O_TGT")),
        broker_filled_order_ids=frozenset({"O_TGT"})))
    assert v.closure_source == EXTERNAL_UNATTRIBUTED and v.contradiction


# ── ⭐ the true-positive path — it must survive every degradation ──────────────

def test_no_own_leg_accounts_for_it_is_CRITICAL() -> None:
    """The genuine external close. This is the default and it must stay reachable."""
    v = classify(Evidence(our_legs=()))
    assert v.closure_source == EXTERNAL_UNATTRIBUTED and v.severity == "CRITICAL"
    assert not v.is_ours


def test_legs_present_but_none_filled_is_CRITICAL() -> None:
    v = classify(Evidence(our_legs=(_leg("SL", "CANCELLED", "O1"),
                                    _leg("TGT", "CANCELLED", "O2"))))
    assert v.closure_source == EXTERNAL_UNATTRIBUTED


@pytest.mark.parametrize("ev,why", [
    (Evidence(our_legs=(_leg("TGT", "COMPLETE"),), orders_read_ok=False), "orders unreadable"),
    (Evidence(our_legs=(_leg("TGT", "COMPLETE"),), ambiguous_cancel=True), "ambiguous cancel"),
])
def test_absence_of_evidence_never_suppresses_the_critical(ev: Evidence, why: str) -> None:
    """⭐ THE FIRST PRINCIPLE. Even with a COMPLETE own leg present, a degraded
    source forces CRITICAL -- suppression requires POSITIVE evidence, and a failed
    read is not evidence."""
    v = classify(ev)
    assert v.closure_source == EXTERNAL_UNATTRIBUTED, why
    assert v.severity == "CRITICAL"


def test_broker_unreachable_does_not_by_itself_force_critical_when_local_spoke() -> None:
    """The counterpart: broker_trades_read_ok=False is a SILENT source, not a failed
    one -- paper is permanently in this state. Rung 3 still decides."""
    v = classify(Evidence(our_legs=(_leg("TGT", "COMPLETE", "O1"),),
                          broker_trades_read_ok=False))
    assert v.is_ours and v.rung == EV_LOCAL_COMPLETE


def test_broker_naming_an_order_that_is_not_ours_is_not_evidence() -> None:
    """Someone else's fill on the same symbol must not be read as ours."""
    v = classify(Evidence(our_legs=(_leg("TGT", "OPEN", "O_MINE"),),
                          broker_filled_order_ids=frozenset({"O_SOMEONE_ELSE"})))
    assert v.closure_source == EXTERNAL_UNATTRIBUTED


def test_entry_leg_is_never_a_closure_source() -> None:
    """Only SL/TGT/EOD close a position. An ENTRY fill must not be misread."""
    v = classify(Evidence(our_legs=(OurLeg("O1", "ENTRY", "COMPLETE"),),
                          broker_filled_order_ids=frozenset({"O1"})))
    assert v.closure_source == EXTERNAL_UNATTRIBUTED


# ── ⏳ §D: rung 4 is a claim about NOW, and it expires ────────────────────────

def test_deferral_expiry_makes_a_stale_mid_fill_silent() -> None:
    """⭐ The leg we were told was "being processed" never landed. The claim has gone
    stale, so it stops counting as evidence and the first principle takes over --
    which is what makes an expired deferral LOUD rather than a quiet own-leg
    attribution nobody would ever look at."""
    ev = Evidence(our_legs=(_leg("TGT", "OPEN", "O1", mid=True),))
    assert classify(ev).closure_source == OWN_TGT          # before the window passes

    v = classify(Evidence(our_legs=ev.our_legs, deferral_expired=True))
    assert v.closure_source == EXTERNAL_UNATTRIBUTED and v.severity == "CRITICAL"
    assert "deferral_expired" in v.notes, "the alert must say WHY it does not know"


def test_deferral_expiry_retires_a_stale_claim_it_does_not_destroy_evidence() -> None:
    """⚠️ Expiry retires rung 4 ONLY. If the leg did reach COMPLETE while we waited,
    that is a live source and the close IS ours -- forcing CRITICAL there would be a
    false alarm manufactured by a timer."""
    v = classify(Evidence(our_legs=(_leg("TGT", "COMPLETE", "O1", mid=True),),
                          deferral_expired=True))
    assert v.closure_source == OWN_TGT and v.severity == "INFO"
    assert v.rung == EV_LOCAL_COMPLETE


def test_a_stale_mid_fill_cannot_manufacture_a_contradiction() -> None:
    """⭐ The subtle one. A stale rung-4 claim naming the SL while a live local row
    says the TGT is COMPLETE must not be read as "sources disagree" -- that would
    turn a correct own-leg close into a CRITICAL because of a claim that had already
    expired. A silent source cannot disagree with anyone."""
    v = classify(Evidence(
        our_legs=(_leg("TGT", "COMPLETE", "O1"), _leg("SL", "OPEN", "O2", mid=True)),
        deferral_expired=True))
    assert not v.contradiction
    assert v.closure_source == OWN_TGT and v.severity == "INFO"


def test_classifier_is_pure() -> None:
    """No I/O, no mutation of its input: the same Evidence classifies identically
    however many times it is asked."""
    ev = Evidence(our_legs=(_leg("TGT", "COMPLETE", "O1"),))
    assert classify(ev) == classify(ev)
    assert ev.our_legs[0].status == "COMPLETE"
