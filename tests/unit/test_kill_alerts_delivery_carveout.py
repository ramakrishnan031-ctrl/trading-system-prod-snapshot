"""tests/unit/test_kill_alerts_delivery_carveout.py -- ledger #9 (03-Aug-2026).

The two 15:15 alerts arrive TOGETHER, every trading afternoon, and BOTH used to claim
that EOD squareoff closes everything:

    main.py:703         "EOD squareoff will close all positions at 15:17."
    kill_switch.py:615  "New signals: BLOCKED | Open positions: managed to SL/TGT/EOD"

⛔ EOD6 does NOT touch DELIVERY (CNC) -- `eod_squareoff.py:23 / :34 / :1073`. A spared
delivery leg is CARRIED by design (ledger #2 / Q4). Both statements were therefore
correct only while delivery was impossible, and go FALSE at the flip: they would tell
the operator, every afternoon, that positions which deliberately survive are about to
be closed. That is the alert-fatigue/false-claim class this ledger item exists to
remove, and it would have arrived on flip day itself.

⭐ WHY THIS ASSERTS A PROPERTY, NOT THE STRING.
Pinning the exact body would go RED on a harmless rewording and GREEN on a
reworded-but-still-wrong one. What must not regress is the CLAIM: no unqualified
"all positions", the managed set scoped to intraday, and the delivery carve-out named.
(Same reasoning as "no fixed number where a property is meant".)

⭐ AND WHY ONE TEST COVERS BOTH SITES.
Two modules, but ONE operator moment -- they land seconds apart at 15:15 and must not
disagree. Ledger #8 earned this the hard way: a fix at one site was not permanent
because the same wrong instruction lived at two more. Anyone who "simplifies" either
body back to the universal claim must fail HERE, not in production.

⚠️ The kill_switch body is SHARED by the scheduled (WARNING) and emergency (CRITICAL)
paths, so the property is asserted on both. It holds for both because SOFT_KILL never
flattens -- it blocks entries and lets exits run.

>> CORRECTED 03-Sep-2026 (T1). That last sentence is sound only while exits EXIST.
   MISSING_EXITS is the measured exception: order_reconciler's CHECK9 trips that kill
   precisely BECAUSE the protective orders are gone from the broker. On 03-Sep the
   ANANTRAJ body told the operator "Intraday positions: managed to SL/TGT/EOD" while
   the position had no exit order at all and the emergency fallback had been rejected
   8 times; it was flattened by hand 2 minutes later.
   (docs/incident/2026-09-03_naked_position_ANANTRAJ.md)
   So MISSING_EXITS now gets its OWN body -- and it must keep the SAME delivery
   carve-out property, which is asserted below alongside the shared one.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from capital.kill_switch import KillSwitch
from core.events import EventBus
from core.state_store import StateStore
from main import _make_force_close_cb

# A universal claim about what gets closed. Present in the pre-fix main.py body.
_UNIVERSAL_CLAIM = "all positions"

# The reason strings the two paths actually pass (read from source):
#   main.py:695 -> the scheduled 15:15 breaker
_SCHEDULED_REASON = "circuit_breaker_force_close_15:15"
#   an EMERGENCY reason -- must not equal a SCHEDULED_KILL_REASONS literal
_EMERGENCY_REASON = "Auto-trip: 3 consecutive API failures (threshold=3)"
#   a MISSING_EXITS reason -- the literal prefix order_reconciler emits (:2836)
_MISSING_EXITS_REASON = (
    "MISSING_EXITS: naked position ANANTRAJ trade_id=trd_b7d0f9ce3b31 "
    "sl_order=260903171147099"
)


def _assert_delivery_carveout(body: str, where: str) -> None:
    """The claim this pair must keep making, wherever it is worded."""
    low = body.lower()

    assert _UNIVERSAL_CLAIM not in low, (
        f"{where}: body makes the UNQUALIFIED claim {_UNIVERSAL_CLAIM!r}. EOD6 does not "
        f"touch delivery (eod_squareoff.py:23/:34/:1073) -- a CNC leg is carried by "
        f"design, so this is false the moment delivery is enabled.\nbody was:\n{body}"
    )
    assert "intraday" in low, (
        f"{where}: the managed/squared set must be SCOPED to intraday. An unscoped "
        f"statement reads as universal.\nbody was:\n{body}"
    )
    assert "cnc" in low, (
        f"{where}: the delivery carve-out must be NAMED, and named as CNC -- that is "
        f"the product the operator sees at the broker.\nbody was:\n{body}"
    )
    assert "delivery" in low, (
        f"{where}: 'CNC' alone assumes the reader maps product->concept mid-incident; "
        f"say 'delivery' too.\nbody was:\n{body}"
    )


def _force_close_body() -> str:
    """Drive the REAL force-close callback and capture the alert body it sends."""
    notifier = MagicMock()
    cb = _make_force_close_cb(MagicMock(), notifier, mode="LIVE")
    cb()
    assert notifier.send.call_count == 1, (
        f"expected exactly one force-close alert, got {notifier.send.call_count}"
    )
    return notifier.send.call_args.kwargs["body"]


def _soft_kill_body(tmp_path: Path, reason: str) -> str:
    """Drive a REAL KillSwitch.soft_kill and capture the alert body it sends."""
    store = StateStore(tmp_path / "carveout.db")
    try:
        ks = KillSwitch(
            state_store=store,
            bus=EventBus(),
            logger=logging.getLogger("test_kill_alerts_delivery_carveout"),
        )
        notifier = MagicMock()
        ks.set_notifier(notifier, mode="LIVE")
        ks.soft_kill(reason=reason, triggered_by="test")
        assert notifier.send.call_count == 1, (
            f"expected exactly one halt alert, got {notifier.send.call_count}"
        )
        return notifier.send.call_args.kwargs["body"]
    finally:
        store.close()


# ── the property, at both sites ─────────────────────────────────────────────────

def test_force_close_alert_does_not_claim_all_positions_are_closed() -> None:
    """RED before the fix: 'EOD squareoff will close all positions at 15:17.'"""
    _assert_delivery_carveout(_force_close_body(), "main.py force-close alert")


@pytest.mark.parametrize("reason", [_SCHEDULED_REASON, _EMERGENCY_REASON])
def test_soft_kill_alert_scopes_managed_positions_to_intraday(
    tmp_path: Path, reason: str
) -> None:
    """RED before the fix: 'Open positions: managed to SL/TGT/EOD' (both paths --
    the body is shared by the scheduled WARNING and the emergency CRITICAL)."""
    _assert_delivery_carveout(_soft_kill_body(tmp_path, reason), f"soft_kill({reason!r})")


def test_the_two_1515_alerts_do_not_disagree_about_delivery(tmp_path: Path) -> None:
    """They land seconds apart. One correcting without the other is the ledger-#8
    failure mode: the operator reads whichever arrives second."""
    force_close = _force_close_body().lower()
    soft_kill = _soft_kill_body(tmp_path, _SCHEDULED_REASON).lower()

    for term in ("cnc", "delivery"):
        assert (term in force_close) == (term in soft_kill), (
            f"the 15:15 pair disagree on {term!r}: force_close={term in force_close}, "
            f"soft_kill={term in soft_kill}. They arrive together; fixing one body and "
            f"not the other leaves the operator with two different answers."
        )


# ── what must NOT have changed while fixing the wording ─────────────────────────

def test_soft_kill_body_still_carries_the_reason(tmp_path: Path) -> None:
    """test_scheduled_kill_severity asserts `reason in body or title`; keep the body
    half true so that test is not silently reduced to its title branch."""
    body = _soft_kill_body(tmp_path, _EMERGENCY_REASON)
    assert _EMERGENCY_REASON in body, (
        "the halt alert must still name the reason in its BODY"
    )


def test_soft_kill_alert_still_says_entries_are_blocked(tmp_path: Path) -> None:
    """The operationally load-bearing half of the message: a SOFT_KILL blocks new
    signals and lets exits run. Rewording the delivery clause must not drop it."""
    body = _soft_kill_body(tmp_path, _SCHEDULED_REASON).lower()
    assert "blocked" in body, f"the SOFT_KILL body must still say entries are BLOCKED:\n{body}"


def test_force_close_alert_still_says_pending_entries_were_cancelled() -> None:
    """Same: the force-close alert's other fact must survive the wording change."""
    body = _force_close_body().lower()
    assert "cancelled" in body, (
        f"the force-close body must still say pending entry orders were cancelled:\n{body}"
    )


# ── T1 (03-Sep-2026): the one kill for which "managed to SL/TGT" is false ───────

def test_missing_exits_body_does_not_claim_the_position_is_managed(tmp_path: Path) -> None:
    """RED before T1: the MISSING_EXITS kill sent
    "Intraday positions: managed to SL/TGT/EOD" -- while the position had NO exit
    order at the broker. That is false BY CONSTRUCTION for this reason: CHECK9
    trips the kill because the exits are missing."""
    body = _soft_kill_body(tmp_path, _MISSING_EXITS_REASON).lower()

    # A bare "managed to sl/tgt" substring check is the WRONG instrument: the
    # corrected body contains the phrase inside "it is NOT managed to SL/TGT",
    # so that check goes RED on a correct body. Assert the AFFIRMATIVE claim is
    # gone and the negation is present -- both directions can fail.
    assert "positions: managed to sl/tgt" not in body, (
        "the MISSING_EXITS body still makes the AFFIRMATIVE claim that intraday "
        "positions are managed to SL/TGT. This kill fires BECAUSE the exits are "
        "gone -- the reassurance is false when it is sent, and it invites the "
        f"operator to stand down. body was: {body}"
    )
    assert "not managed to sl/tgt" in body, (
        "the MISSING_EXITS body must say plainly that the position is NOT managed "
        f"to SL/TGT -- silence reads as the old reassurance. body was: {body}"
    )


def test_missing_exits_body_tells_the_operator_manual_action_may_be_needed(
    tmp_path: Path,
) -> None:
    """The operationally load-bearing half. On 03-Sep the automated exit was
    rejected 8 times and a human was the only thing that closed the position; the
    alert must say so rather than imply automation has it."""
    body = _soft_kill_body(tmp_path, _MISSING_EXITS_REASON).lower()
    assert "manual" in body, (
        f"the MISSING_EXITS body must tell the operator manual action may be "
        f"required: {body}"
    )
    assert "no exit order" in body, (
        f"the MISSING_EXITS body must state the measured fact -- there is no exit "
        f"order at the broker: {body}"
    )


def test_missing_exits_body_keeps_the_delivery_carveout(tmp_path: Path) -> None:
    """A second body is a second place for the ledger-#8 failure mode to hide.
    The carve-out property must hold here exactly as it does on the shared body."""
    _assert_delivery_carveout(
        _soft_kill_body(tmp_path, _MISSING_EXITS_REASON),
        f"soft_kill({_MISSING_EXITS_REASON!r})",
    )


def test_missing_exits_body_still_carries_reason_and_blocked(tmp_path: Path) -> None:
    """The new branch must not drop what the shared body is pinned on."""
    body = _soft_kill_body(tmp_path, _MISSING_EXITS_REASON)
    assert _MISSING_EXITS_REASON in body, "the MISSING_EXITS body must name the reason"
    assert "BLOCKED" in body, "the MISSING_EXITS body must still say entries are BLOCKED"


def test_the_shared_body_is_unchanged_for_every_other_reason(tmp_path: Path) -> None:
    """T1 is a NEW branch, not a rewrite. Non-MISSING_EXITS kills must still get the
    original reassurance -- it is true for them, and silently removing it would lose
    information on every ordinary halt."""
    for i, reason in enumerate((_SCHEDULED_REASON, _EMERGENCY_REASON)):
        # A fresh store PER reason: a KillSwitch whose store already holds
        # SOFT_KILL is already-active, so the second soft_kill() is a no-op and
        # sends NO alert. Re-using one tmp_path silently measures nothing.
        d = tmp_path / f"shared{i}"
        d.mkdir()
        body = _soft_kill_body(d, reason).lower()
        assert "managed to sl/tgt/eod" in body, (
            f"soft_kill({reason!r}) lost the shared body's managed-set statement; "
            f"T1 must add a branch, not replace the wording. {body}"
        )
