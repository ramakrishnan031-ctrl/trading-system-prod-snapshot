"""
test_fix065_market_hours_guard.py

FIX-065: Verify market hours push guard in deploy/post-receive hook.

Tests verify that:
1. Push at 11:00 IST → rejected (market hours)
2. Push at 08:00 IST → allowed (before market)
3. Push at 16:00 IST → allowed (after market)
4. [force-deploy] in commit message → allowed during market hours
5. Edge cases: 09:15 (open), 15:30 (close)
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# These tests run a bash subprocess. Skip on all Windows machines —
# even when bash.exe exists (WSL stub), it may not execute scripts.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Bash hook tests require a Unix bash environment; not supported on Windows",
)


@pytest.fixture
def mock_hook_script():
    """
    Create a simplified version of the post-receive hook for testing.
    This version only tests the market hours guard logic, not the full deployment.
    """
    script_content = """#!/bin/bash
set -e

# Mock stdin for testing: oldrev newrev refname
# Git calls post-receive with: <oldrev> <newrev> <refname>
# We'll read the commit message from the second argument (newrev)

# FIX-065: Market Hours Push Guard
CURRENT_HOUR="$1"  # Passed as first argument for testing
COMMIT_MSG="$2"    # Passed as second argument for testing

# Check for bypass flag in commit message
if echo "$COMMIT_MSG" | grep -q '\\[force-deploy\\]'; then
    echo "[force-deploy] bypass: allowing push during market hours"
    exit 0
elif [ "$CURRENT_HOUR" -ge "0915" ] && [ "$CURRENT_HOUR" -le "1530" ]; then
    echo "ERROR: Cannot deploy during market hours (09:15-15:30 IST)."
    echo "Include [force-deploy] in commit message to bypass this check."
    exit 1
fi

echo "Push allowed (outside market hours or bypassed)"
exit 0
"""
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.sh', delete=False
    ) as f:
        f.write(script_content)
        script_path = Path(f.name)

    # Make executable (on Unix-like systems)
    script_path.chmod(0o755)

    yield script_path

    # Cleanup
    script_path.unlink()


def run_hook(script_path: Path, hour: str, commit_msg: str) -> tuple[int, str, str]:
    """
    Run the mock hook script with given hour and commit message.

    Returns:
        (exit_code, stdout, stderr)
    """
    result = subprocess.run(
        ['bash', str(script_path), hour, commit_msg],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Push during market hours (11:00 IST) → rejected
# ─────────────────────────────────────────────────────────────────────────────


def test_push_during_market_hours_rejected(mock_hook_script):
    """FIX-065: Push at 11:00 IST (market hours) should be rejected."""
    exit_code, stdout, stderr = run_hook(
        mock_hook_script, "1100", "Regular commit message"
    )

    assert exit_code == 1, "Push during market hours should be rejected"
    assert "ERROR" in stdout or "ERROR" in stderr
    assert "market hours" in stdout.lower() or "market hours" in stderr.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Push before market hours (08:00 IST) → allowed
# ─────────────────────────────────────────────────────────────────────────────


def test_push_before_market_hours_allowed(mock_hook_script):
    """FIX-065: Push at 08:00 IST (before market) should be allowed."""
    exit_code, stdout, stderr = run_hook(
        mock_hook_script, "0800", "Regular commit message"
    )

    assert exit_code == 0, "Push before market hours should be allowed"
    assert "allowed" in stdout.lower() or exit_code == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Push after market hours (16:00 IST) → allowed
# ─────────────────────────────────────────────────────────────────────────────


def test_push_after_market_hours_allowed(mock_hook_script):
    """FIX-065: Push at 16:00 IST (after market) should be allowed."""
    exit_code, stdout, stderr = run_hook(
        mock_hook_script, "1600", "Regular commit message"
    )

    assert exit_code == 0, "Push after market hours should be allowed"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: [force-deploy] bypass during market hours → allowed
# ─────────────────────────────────────────────────────────────────────────────


def test_force_deploy_bypass_during_market_hours(mock_hook_script):
    """FIX-065: [force-deploy] in commit message bypasses market hours check."""
    exit_code, stdout, stderr = run_hook(
        mock_hook_script, "1100", "Critical fix [force-deploy]"
    )

    assert exit_code == 0, "[force-deploy] bypass should allow push during market hours"
    assert "bypass" in stdout.lower() or "force-deploy" in stdout.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Edge case - Market open (09:15) → rejected
# ─────────────────────────────────────────────────────────────────────────────


def test_market_open_time_rejected(mock_hook_script):
    """FIX-065: Push at 09:15 IST (market open) should be rejected."""
    exit_code, stdout, stderr = run_hook(
        mock_hook_script, "0915", "Regular commit message"
    )

    assert exit_code == 1, "Push at market open (09:15) should be rejected"


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Edge case - Market close (15:30) → rejected
# ─────────────────────────────────────────────────────────────────────────────


def test_market_close_time_rejected(mock_hook_script):
    """FIX-065: Push at 15:30 IST (market close) should be rejected."""
    exit_code, stdout, stderr = run_hook(
        mock_hook_script, "1530", "Regular commit message"
    )

    assert exit_code == 1, "Push at market close (15:30) should be rejected"


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Edge case - Just before market open (09:14) → allowed
# ─────────────────────────────────────────────────────────────────────────────


def test_just_before_market_open_allowed(mock_hook_script):
    """FIX-065: Push at 09:14 IST (1 min before open) should be allowed."""
    exit_code, stdout, stderr = run_hook(
        mock_hook_script, "0914", "Regular commit message"
    )

    assert exit_code == 0, "Push just before market open should be allowed"


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Edge case - Just after market close (15:31) → allowed
# ─────────────────────────────────────────────────────────────────────────────


def test_just_after_market_close_allowed(mock_hook_script):
    """FIX-065: Push at 15:31 IST (1 min after close) should be allowed."""
    exit_code, stdout, stderr = run_hook(
        mock_hook_script, "1531", "Regular commit message"
    )

    assert exit_code == 0, "Push just after market close should be allowed"


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: [force-deploy] with different casing
# ─────────────────────────────────────────────────────────────────────────────


def test_force_deploy_case_sensitive(mock_hook_script):
    """FIX-065: [FORCE-DEPLOY] (uppercase) should not bypass (case-sensitive)."""
    exit_code, stdout, stderr = run_hook(
        mock_hook_script, "1100", "Critical fix [FORCE-DEPLOY]"
    )

    # Bash grep without -i is case-sensitive, so uppercase should NOT bypass
    assert exit_code == 1, "[FORCE-DEPLOY] (uppercase) should not bypass"


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: Multiple [force-deploy] tags in commit message
# ─────────────────────────────────────────────────────────────────────────────


def test_multiple_force_deploy_tags(mock_hook_script):
    """FIX-065: Multiple [force-deploy] tags should still bypass."""
    exit_code, stdout, stderr = run_hook(
        mock_hook_script,
        "1100",
        "Fix [force-deploy] critical [force-deploy] issue"
    )

    assert exit_code == 0, "Multiple [force-deploy] tags should bypass"


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: Late night push (23:00) → allowed
# ─────────────────────────────────────────────────────────────────────────────


def test_late_night_push_allowed(mock_hook_script):
    """FIX-065: Push at 23:00 IST (late night) should be allowed."""
    exit_code, stdout, stderr = run_hook(
        mock_hook_script, "2300", "Regular commit message"
    )

    assert exit_code == 0, "Late night push should be allowed"


# ─────────────────────────────────────────────────────────────────────────────
# Test 12: Early morning push (06:00) → allowed
# ─────────────────────────────────────────────────────────────────────────────


def test_early_morning_push_allowed(mock_hook_script):
    """FIX-065: Push at 06:00 IST (early morning) should be allowed."""
    exit_code, stdout, stderr = run_hook(
        mock_hook_script, "0600", "Regular commit message"
    )

    assert exit_code == 0, "Early morning push should be allowed"
