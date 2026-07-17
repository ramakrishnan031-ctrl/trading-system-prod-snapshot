"""tests/unit/test_s5_benign_functional.py — S5: the benign functional-status set.

F2 (15-Jul) split EXECUTION from FUNCTION: a cron job can exit 0 and still have
done nothing useful (the 14-Jul "green heartbeat, empty CSV" silent failure). The
Cron Officer therefore flags any functional_status outside a benign set.

S5 (AB-910 tail): that set was ("OK","SUCCESS","DELIVERED") — too narrow. It
flagged outcomes that are CORRECT BY DESIGN, so the EOD report cried wolf on
quiet days:

  * EMPTY_NO_DATA — generate_screened_csv's own criterion
    (scripts/generate_screened_stocks_csv.py:312), whose docstring states it is
    "legitimate on a no-trade day, but recorded so the operator sees data-present
    vs empty". A trading day that screens no stocks is not a failure — it is the
    honest answer. Flagging it every quiet day trains the operator to ignore the
    functional line, which is precisely how the 14-Jul silent failure survived.
  * SKIPPED — a deliberate, recorded no-op.

The rule settled here: benign == "the job did its job, and an empty/absent result
is the CORRECT answer for today". Anything that MIGHT be a real gap stays loud —
FAILED, MISSING, DEGRADED and UNKNOWN are all still flagged. UNKNOWN especially:
it means the criterion could not be evaluated, i.e. silence about silence, which
is exactly what F2 exists to surface.

Note the two DIFFERENT paths, which the audit finding conflated:
  * heartbeat STATUS "SKIPPED"  -> caught by build_eod_summary's
    `elif status == "SKIPPED"`, counted under "Skipped", NEVER reaches the
    functional check. (This is why S1's holiday guard needs nothing from S5.)
  * functional_status "SKIPPED" -> a job that RAN, exited SUCCESS, and reported
    SKIPPED functionally. That is what this set governs.
"""
from __future__ import annotations

import pytest

from scripts.cron_officer import _BENIGN_FUNCTIONAL


# ─────────────────────────────────────────────────────────────────────────────
# The benign set — a legitimate quiet day must NOT raise a functional issue
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("status", ["OK", "SUCCESS", "DELIVERED"])
def test_success_shapes_are_benign(status):
    assert status in _BENIGN_FUNCTIONAL


def test_empty_no_data_is_benign():
    """RED-on-old: the pre-S5 set was ("OK","SUCCESS","DELIVERED"), so
    generate_screened_csv's documented-legitimate EMPTY_NO_DATA was flagged as a
    functional issue on EVERY quiet trading day."""
    assert "EMPTY_NO_DATA" in _BENIGN_FUNCTIONAL


def test_skipped_is_benign():
    """A deliberate, recorded no-op is not a functional failure."""
    assert "SKIPPED" in _BENIGN_FUNCTIONAL


def test_benign_check_is_case_insensitive_via_upper():
    """The Officer compares func.upper() — the set must hold upper-case keys, or
    the comparison silently never matches and everything reads as an issue."""
    assert all(s == s.upper() for s in _BENIGN_FUNCTIONAL)


# ─────────────────────────────────────────────────────────────────────────────
# A GENUINE functional failure must still be flagged (the set is not a mute button)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("status", ["FAILED", "MISSING", "DEGRADED", "UNKNOWN"])
def test_real_gaps_are_still_flagged(status):
    """S5 widened the benign set; it must not have blunted F2.

    UNKNOWN matters most: it means the criterion could not be evaluated — silence
    about silence — which is exactly what F2 exists to surface.
    """
    assert status not in _BENIGN_FUNCTIONAL


def test_the_benign_set_is_exactly_what_was_settled():
    """Pin the set. Widening it is a deliberate decision (does an empty/absent
    result mean the job did its job today?), never an accident."""
    assert _BENIGN_FUNCTIONAL == frozenset({
        "OK", "SUCCESS", "DELIVERED", "SKIPPED", "EMPTY_NO_DATA",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Every status the codebase actually emits is classified deliberately
# ─────────────────────────────────────────────────────────────────────────────
def test_every_emitted_functional_status_is_classified():
    """No emitted value may be un-triaged: each is either benign by decision or
    flagged by decision. A new functional_status added without a decision here
    would otherwise default to 'flagged' and quietly become EOD noise."""
    emitted_benign = {"OK", "EMPTY_NO_DATA", "SKIPPED"}
    emitted_flagged = {"FAILED", "UNKNOWN", "MISSING", "DEGRADED"}

    assert emitted_benign <= _BENIGN_FUNCTIONAL
    assert not (emitted_flagged & _BENIGN_FUNCTIONAL)
