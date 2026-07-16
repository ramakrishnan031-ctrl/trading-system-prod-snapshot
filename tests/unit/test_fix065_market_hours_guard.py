"""
test_fix065_market_hours_guard.py

FIX-065: the market-hours DEPLOY guard — deploy/hooks/market_hours_guard.sh.

*** REWRITTEN 17-Jul-2026 (sweep S6). ***
These tests used to build a mock hook as an inline bash string and assert against THAT,
so they passed no matter what the real hook did — and the real hook had no guard at all.
The guard existed only in the dead `deploy/post-receive` duplicate (deleted in batch-2),
meaning FIX-065 had NEVER been live while 12 green tests implied it was. A test that
cannot fail when the feature is absent is not coverage; it is decoration.

They now run THE SHIPPED SCRIPT (deploy/hooks/market_hours_guard.sh) as a subprocess.
The script takes (HHMM, DOW, COMMIT_MSG) and returns 0=allow / 1=reject, so the clock is
injected rather than mocked — deterministic, and it fails if the file is deleted or its
logic regresses. Deleting the script turns every test here RED, which is the point.

Guard contract:
  - Reject   09:15-15:30 IST on a weekday (dow 1-5), unless [force-deploy] in the message
  - Allow    outside that window, on weekends, and with the [force-deploy] bypass
  - Fail OPEN on an unparseable clock (never wedge deploys on the guard's own error)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_GUARD = Path(__file__).resolve().parents[2] / "deploy" / "hooks" / "market_hours_guard.sh"

# The guard is bash. Skip only when no bash is reachable — NOT blanket-on-Windows: Git Bash
# runs it fine, and a test that silently skips on the dev machine is how the original 12
# went unnoticed for months.
_BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(
    _BASH is None, reason="no bash on PATH; the deploy guard is a bash script"
)


def run_guard(hhmm: str, dow: str, msg: str = "regular commit") -> tuple[int, str]:
    """Run the REAL guard script. Returns (exit_code, stdout+stderr)."""
    assert _GUARD.is_file(), f"the shipped guard is missing: {_GUARD}"
    r = subprocess.run(
        [_BASH, str(_GUARD), hhmm, dow, msg],
        capture_output=True, text=True,
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


WEEKDAY = "3"   # Wednesday
SATURDAY = "6"


def test_the_guard_actually_exists():
    """Guards the failure mode that made the originals vacuous: no real file behind them."""
    assert _GUARD.is_file(), "deploy/hooks/market_hours_guard.sh must ship"


# ── inside the window → reject ────────────────────────────────────────────────

@pytest.mark.parametrize("hhmm", ["0915", "1100", "1200", "1530"])
def test_push_during_market_hours_rejected(hhmm):
    rc, out = run_guard(hhmm, WEEKDAY)
    assert rc == 1, f"push at {hhmm} IST must be rejected"
    assert "REJECT" in out and "market hours" in out.lower()


# ── outside the window → allow ────────────────────────────────────────────────

@pytest.mark.parametrize("hhmm", ["0800", "0914", "1531", "1600", "2300", "0600"])
def test_push_outside_market_hours_allowed(hhmm):
    rc, _ = run_guard(hhmm, WEEKDAY)
    assert rc == 0, f"push at {hhmm} IST must be allowed"


def test_weekend_always_allowed():
    """Saturday 11:00 is inside the clock window but is not a session."""
    rc, _ = run_guard("1100", SATURDAY)
    assert rc == 0


# ── the [force-deploy] bypass ─────────────────────────────────────────────────

def test_force_deploy_bypasses_during_market_hours():
    rc, out = run_guard("1100", WEEKDAY, "Critical fix [force-deploy]")
    assert rc == 0
    assert "force-deploy" in out.lower()


def test_force_deploy_is_case_sensitive():
    """Bash `case` is case-sensitive; [FORCE-DEPLOY] must NOT bypass."""
    rc, _ = run_guard("1100", WEEKDAY, "Critical fix [FORCE-DEPLOY]")
    assert rc == 1


def test_multiple_force_deploy_tags_still_bypass():
    rc, _ = run_guard("1100", WEEKDAY, "Fix [force-deploy] more [force-deploy]")
    assert rc == 0


# ── robustness: never wedge deploys on the guard's own error ──────────────────

@pytest.mark.parametrize("hhmm,dow", [("", "3"), ("abcd", "3"), ("1100", ""), ("1100", "x")])
def test_unparseable_clock_fails_open(hhmm, dow):
    """A deploy guard is an operational convenience, not a security control: if it cannot
    tell the time it must ALLOW, never block every deploy."""
    rc, _ = run_guard(hhmm, dow)
    assert rc == 0, "guard must fail OPEN on unparseable input"


def test_leading_zero_hours_are_not_parsed_as_octal():
    """`0915` in bash arithmetic without 10# is an octal error, which would crash the
    guard at exactly the minute it matters most (market open)."""
    rc, out = run_guard("0915", WEEKDAY)
    assert rc == 1, "09:15 must reject, not error out"
    assert "value too great" not in out and "syntax error" not in out


# ── the guard is actually wired into a hook that CAN reject a push ────────────

def test_guard_is_wired_into_pre_receive_not_post_receive():
    """post-receive cannot reject a push (git ignores its exit status; refs are already
    updated), so a guard there would skip the checkout while the bare ref moved — a
    half-deploy. This asserts the wiring is in the hook that can actually say no."""
    hooks = _GUARD.parent
    pre = (hooks / "pre-receive").read_text(encoding="utf-8")
    assert "market_hours_guard.sh" in pre, "the guard must be called from pre-receive"

    post = (hooks / "post-receive").read_text(encoding="utf-8")
    assert "market_hours_guard.sh" not in post, (
        "the guard must NOT live in post-receive — it cannot reject a push there"
    )
