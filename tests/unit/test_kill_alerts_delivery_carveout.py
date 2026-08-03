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
