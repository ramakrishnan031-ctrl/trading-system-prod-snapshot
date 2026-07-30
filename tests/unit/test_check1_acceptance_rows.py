"""tests/unit/test_check1_acceptance_rows.py -- the acceptance evidence for CHECK1's
classifier (27-Jul-2026). The fix itself shipped in v45 (`2f8fd87` §C, `81f9372` §D);
this file is the proof obligation that shipped WITHOUT it.

Two claims are pinned here, and nothing else:

⭐ A2 -- SUPPRESSION REQUIRES POSITIVE EVIDENCE, AND A FAILED READ IS NOT EVIDENCE.
   `test_closure_classifier.py` proves this of the pure function, by passing
   `orders_read_ok=False` directly. That is the *classifier's* contract, not the
   *system's*: it says nothing about whether the wiring can ever set the flag, nor
   which read sets it. Here the failure is PLANTED AT THE DB BOUNDARY -- `fetch_all`
   raises -- so order_reconciler's own `except` handlers run and the flag is earned.

⭐ A3/A4 -- THE TRUE-POSITIVE PATH, ON THE FIVE PRODUCTION ROWS THAT ARE ITS ONLY
   REAL CANDIDATES. Of the 41 `CLOSED_MANUAL` trades, 35 have one of our own legs
   COMPLETE. Six do not; GICRE is a cancelled entry that never filled (`qty_filled=0`
   -- not a position, so not a closure), leaving five. They must all still fire
   CRITICAL, and the design says they do "by construction". ⚠️ By construction is an
   argument. These are the measurement.

⚠️ THE TWO GROUPS ARE DIFFERENT CLAIMS, and A4 is the harder one:
     EVEREADY / AEROENTER / RCF -- the record is COMPLETE and says nothing of ours
       closed the position. Evidence of absence.
     SULA / AGARIND            -- the record is SILENT. Both predate exit-leg
       recording entirely (the first SL/TGT row in `orders` is 2026-06-17, they are
       15-Jun and 16-Jun), so "no completed exit leg" is a gap in the record, not a
       fact about the world. ⭐ SILENCE MUST NEVER SUPPRESS -- and that is the rule
       working correctly on an ambiguous case, not a true positive being detected.

Run: python -m pytest tests/unit/test_check1_acceptance_rows.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.closure_source import EXTERNAL_UNATTRIBUTED
from tests.unit.test_check1_classified_close import (
    _EXIT_LEG_READ, _ORPHAN_LOOKUP, _bt, _run_check1, _severities,
)


def _why(notifier) -> str:
    """The 'Why:' line the operator actually reads."""
    return notifier.send.call_args.kwargs["body"]


# ═══════════════════════════════════════════════════════════════════════════════
# A2 -- an unreadable `orders` table, planted, at the wiring level
# ═══════════════════════════════════════════════════════════════════════════════

def test_orphan_lookup_read_failure_forces_CRITICAL_over_a_complete_own_leg(
        tmp_path: Path) -> None:
    """⭐⭐ THE FIRST PRINCIPLE, END TO END, AND THE ONE THAT COULD GO EITHER WAY.

    Our TGT leg is COMPLETE in the local book -- rung 3, which on its own returns
    OWN_TGT at INFO and is the single most common real shape (35 of 41). But the
    orphan-leg lookup, a SECOND read of the same table, failed. One source is
    positive and one is broken, and a broken source is an unknown.

    ⇒ CRITICAL. Flip `orders_read_ok` to a hardcoded True and this test goes from
    CRITICAL to INFO -- the severity itself moves, which is what makes this the
    non-vacuous half of A2.
    """
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("tgt1", "TGT", "COMPLETE")],
        plant_read_failure=_ORPHAN_LOOKUP)
    try:
        assert _severities(notifier) == ["CRITICAL"], (
            "a COMPLETE own leg must NOT suppress the alert when another read of "
            "the same table failed")
        assert row["closure_source"] == EXTERNAL_UNATTRIBUTED
        assert "orders_read_failed" in _why(notifier)
    finally:
        store.close()


def test_exit_leg_read_failure_says_UNREADABLE_not_no_own_leg(tmp_path: Path) -> None:
    """The other read, and a narrower claim -- stated narrowly on purpose.

    ⚠️ When the exit-leg read fails, `_our_legs` is empty for the same reason the
    flag is False, so the verdict is EXTERNAL_UNATTRIBUTED down BOTH routes and the
    severity alone proves nothing. The claim this test carries is therefore the
    REASON, not the severity: the alert must say `orders_read_failed`, because
    "we could not read our own book" sends the operator to the DB while "nothing of
    ours accounts for it" sends them to the broker. Same severity, opposite actions.
    """
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("tgt1", "TGT", "COMPLETE")],
        plant_read_failure=_EXIT_LEG_READ)
    try:
        assert _severities(notifier) == ["CRITICAL"]
        assert row["closure_source"] == EXTERNAL_UNATTRIBUTED
        body = _why(notifier)
        assert "orders_read_failed" in body
        assert "no_own_leg_accounts_for_close" not in body, (
            "an unreadable book must not be reported as a proven external close")
    finally:
        store.close()


def test_a_read_failure_outranks_even_broker_corroboration(tmp_path: Path) -> None:
    """The strongest evidence we can hold -- the broker NAMING our own order that
    filled (rung 1) -- still does not survive a failed read of our own orders. The
    rule is not "the best source wins"; it is that an unknown resolves towards the
    alert whatever else is true."""
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("tgt1", "TGT", "COMPLETE")],
        broker_trades=[_bt("tgt1")], plant_read_failure=_ORPHAN_LOOKUP)
    try:
        assert _severities(notifier) == ["CRITICAL"]
        assert row["closure_source"] == EXTERNAL_UNATTRIBUTED
    finally:
        store.close()


# ═══════════════════════════════════════════════════════════════════════════════
# A3 -- the three genuine candidates: the record is complete, and it exonerates us
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("symbol,entry,exit_px,shape", [
    # ⭐ a REAL broker exit price, and no leg of ours anywhere near it.
    ("EVEREADY", 361.00, 367.00, "broker exit price, no own leg"),
    ("AEROENTER", 132.13, 131.99, "broker exit price, no own leg"),
])
def test_a_broker_exit_price_that_is_not_ours_stays_CRITICAL(
        tmp_path: Path, symbol: str, entry: float, exit_px: float,
        shape: str) -> None:
    """⭐ EVEREADY and AEROENTER. The broker DID fill something on this symbol and
    we can read the price -- but the order that filled is not one of ours. A
    resolved exit price is not attribution: knowing WHAT the position went out at
    says nothing about WHO closed it, and conflating the two is how a confident
    wrong answer gets built.

    ⚠️ These two are consistent with a genuine external close and cannot now be
    proven either way -- Kite's trades() is same-day only and the 20/23/24-Jul
    evidence is gone. The test pins the CLASSIFICATION of their shape, which is all
    that was ever provable.
    """
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[], symbol=symbol, entry_price=entry,
        broker_trades=[_bt("SOMEONE_ELSES_ORDER", price=exit_px, symbol=symbol)])
    try:
        assert _severities(notifier) == ["CRITICAL"], shape
        assert row["closure_source"] == EXTERNAL_UNATTRIBUTED
        body = _why(notifier)
        assert f"Exit: {exit_px:.2f}" in body, "the real exit price is still reported"
        assert "broker_trades" in body, "and it is labelled as broker-sourced"
    finally:
        store.close()


def test_rcf_entry_proxy_fallback_stays_CRITICAL(tmp_path: Path) -> None:
    """RCF -- exit price unknown, so the price falls back to the entry proxy and the
    row reads pnl 0.00. ⚠️ A zero P&L is the shape most likely to be dismissed as
    'nothing happened'; it must alert exactly as loudly as the other two."""
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[], symbol="RCF", entry_price=137.73, broker_trades=[])
    try:
        assert _severities(notifier) == ["CRITICAL"]
        assert row["closure_source"] == EXTERNAL_UNATTRIBUTED
        body = _why(notifier)
        assert "entry_proxy" in body, "an unresolved exit price must be labelled"
        assert "Exit: 137.73" in body
    finally:
        store.close()


def test_a_cancelled_leg_does_not_exonerate_the_close(tmp_path: Path) -> None:
    """The three candidates each had SL/TGT rows that never filled. A leg that
    EXISTS is not a leg that CLOSED anything -- only COMPLETE (or a broker-refused
    mid-fill cancel) is evidence."""
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("sl1", "SL", "CANCELLED"), ("tgt1", "TGT", "CANCELLED")],
        symbol="EVEREADY", entry_price=361.00)
    try:
        assert _severities(notifier) == ["CRITICAL"]
        assert row["closure_source"] == EXTERNAL_UNATTRIBUTED
    finally:
        store.close()


# ═══════════════════════════════════════════════════════════════════════════════
# A4 -- the two silent rows: SILENCE MUST NEVER SUPPRESS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("symbol,entry", [
    ("SULA", 159.59),       # 15-Jun-2026
    ("AGARIND", 572.55),    # 16-Jun-2026
])
def test_a_trade_predating_exit_leg_recording_still_fires_CRITICAL(
        tmp_path: Path, symbol: str, entry: float) -> None:
    """⭐⭐ SULA and AGARIND. Their `orders` table holds the ENTRY and nothing else,
    because on 15/16-Jun the system did not yet write SL/TGT rows at all (the first
    is 17-Jun). So the classifier is asked a question the data cannot answer.

    It must answer CRITICAL -- and this is the case where that is NOT a detection.
    It is the first principle refusing to convert a gap in the record into a claim
    about the world, in either direction. Reading these two as proven external
    closes would be the same error as suppressing them.
    """
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("entry1", "ENTRY", "COMPLETE")],
        symbol=symbol, entry_price=entry)
    try:
        assert _severities(notifier) == ["CRITICAL"]
        assert row["closure_source"] == EXTERNAL_UNATTRIBUTED
        assert "no_own_leg_accounts_for_close" in _why(notifier)
    finally:
        store.close()


def test_a_filled_ENTRY_leg_is_never_read_as_the_thing_that_closed_it(
        tmp_path: Path) -> None:
    """⭐ The trap inside A4. SULA's ENTRY leg IS COMPLETE, and the broker WILL name
    it in trades() -- it is a real fill on the right symbol. Only SL/TGT/EOD can
    close a position; if the leg filter ever widened to 'any COMPLETE leg', both
    silent rows would flip to a confident OWN_* at INFO and the alert would vanish
    on exactly the rows that most need it.
    """
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("entry1", "ENTRY", "COMPLETE")], symbol="SULA",
        entry_price=159.59,
        broker_trades=[_bt("entry1", price=159.59, symbol="SULA")])
    try:
        assert _severities(notifier) == ["CRITICAL"]
        assert row["closure_source"] == EXTERNAL_UNATTRIBUTED
    finally:
        store.close()


# ═══════════════════════════════════════════════════════════════════════════════
# PARITY -- the same five rows, in paper
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("symbol,entry", [
    ("EVEREADY", 361.00), ("AEROENTER", 132.13), ("RCF", 137.73),
    ("SULA", 159.59), ("AGARIND", 572.55),
])
def test_all_five_rows_still_fire_CRITICAL_in_paper(
        tmp_path: Path, symbol: str, entry: float) -> None:
    """⚠️ PAPER IS STRUCTURALLY WEAKER THAN LIVE: `get_trades()` returns [] there,
    permanently, so rungs 1-2 are unreachable and the exit price always falls back
    to the entry proxy. That collapses all five rows onto ONE shape in paper.

    ⭐ Which is the parity question worth asking: does the weaker mode weaken the
    ANSWER? It must not. A degraded evidence source is exactly the condition the
    first principle covers -- less evidence can only push the verdict towards the
    alert, never away from it.
    """
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[], symbol=symbol, entry_price=entry, broker_trades=[])
    try:
        assert _severities(notifier) == ["CRITICAL"]
        assert row["closure_source"] == EXTERNAL_UNATTRIBUTED
    finally:
        store.close()


# ═══════════════════════════════════════════════════════════════════════════════
# GICRE -- the row that is not a closure at all
# ═══════════════════════════════════════════════════════════════════════════════

def test_gicre_shape_a_never_filled_entry_releases_nothing_and_alerts_nothing(
        tmp_path: Path) -> None:
    """GICRE was removed from the candidate set by asking what the data COULD have
    contained: `qty_filled=0` is a cancelled entry, so there was never a position to
    close externally. CHECK1 must not manufacture a closure event from it -- no
    capital release (there is none reserved against a zero fill) and no alert.
    """
    store, notifier, action, row = _run_check1(
        tmp_path, legs=[], symbol="GICRE", entry_price=0.0, qty=0)
    try:
        assert _severities(notifier) == [], "a non-trade must not page anyone"
        assert row["status"] == "CLOSED_MANUAL", "the stale OPEN row is still cleared"
        assert action.success
    finally:
        store.close()
