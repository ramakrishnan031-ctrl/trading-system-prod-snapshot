"""T4 timezone-reliability operator tools — tests.

Covers:
  * deploy_preflight.evaluate() pure decision logic (tolerance, market gate,
    tz-visibility-never-blocks) — deterministic, no I/O.
  * ist_now.sh  -> emits a valid +05:30 ISO timestamp.
  * check_tz.sh -> FAILS LOUD on the broken (UTC) suspect form, PASSES on
    agreement (suspect offset forced via CHECK_TZ_FAKE_OFF).

The bash-script tests locate a bash interpreter and skip gracefully if none is
available (e.g. a minimal CI), so the pure-logic tests are the hard guarantee.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts.deploy_preflight import evaluate

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "scripts"


# ── B) evaluate() pure logic ──────────────────────────────────────────────────

def test_evaluate_pass_when_clocks_agree_and_market_closed() -> None:
    code, lines = evaluate(pc_epoch=1000, vm_epoch=1005, market_open=False,
                           market_gated=True, tolerance_s=120, tz_ok=False,
                           tz_detail="msys2", vm_iso="2026-06-29T20:30:00+05:30")
    assert code == 0
    assert lines[-1] == "PREFLIGHT: PASS"


def test_evaluate_fail_when_market_open_and_gated() -> None:
    code, lines = evaluate(pc_epoch=1000, vm_epoch=1000, market_open=True,
                           market_gated=True, tz_ok=True,
                           vm_iso="2026-06-29T11:00:00+05:30")
    assert code == 1
    assert any("MARKET OPEN" in ln and ln.startswith("[FAIL]") for ln in lines)
    assert lines[-1] == "PREFLIGHT: FAIL"


def test_evaluate_market_open_ok_when_not_gated() -> None:
    # A non-market-gated action may proceed even while the market is open.
    code, lines = evaluate(pc_epoch=1000, vm_epoch=1000, market_open=True,
                           market_gated=False)
    assert code == 0
    assert any("not market-gated" in ln.lower() for ln in lines)


def test_evaluate_fail_when_clocks_out_of_sync() -> None:
    code, lines = evaluate(pc_epoch=1000, vm_epoch=1200, market_open=False,
                           market_gated=True, tolerance_s=120)
    assert code == 1  # 200s skew > 120s tolerance
    assert any("OUT OF SYNC" in ln for ln in lines)


def test_evaluate_clock_skew_boundary_inclusive() -> None:
    # Exactly at tolerance must pass (<=, not <).
    code, _ = evaluate(pc_epoch=0, vm_epoch=120, market_open=False,
                       market_gated=True, tolerance_s=120)
    assert code == 0


def test_evaluate_tz_check_failure_never_blocks() -> None:
    # tz_ok False (broken MSYS2) must NOT cause a FAIL on its own.
    code, lines = evaluate(pc_epoch=1, vm_epoch=1, market_open=False,
                           market_gated=True, tz_ok=False, tz_detail="broken")
    assert code == 0
    assert any(ln.startswith("[INFO]") and "NOT blocking" in ln for ln in lines)


# ── A) + C) bash scripts ──────────────────────────────────────────────────────

def _find_bash() -> str | None:
    b = shutil.which("bash")
    if b:
        return b
    for c in (r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files\Git\usr\bin\bash.exe"):
        if Path(c).exists():
            return c
    return None


_BASH = _find_bash()
_needs_bash = pytest.mark.skipif(_BASH is None, reason="no bash interpreter available")


@_needs_bash
def test_ist_now_emits_valid_ist() -> None:
    p = subprocess.run([_BASH, str(_SCRIPTS / "ist_now.sh")],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    dt = datetime.fromisoformat(out)            # parses or raises
    assert dt.utcoffset() == timedelta(hours=5, minutes=30), out


@_needs_bash
def test_check_tz_fails_on_broken_utc_form() -> None:
    env = dict(os.environ, CHECK_TZ_FAKE_OFF="+0000", CHECK_TZ_FAKE_HM="14:36")
    p = subprocess.run([_BASH, str(_SCRIPTS / "check_tz.sh")],
                       capture_output=True, text=True, timeout=60, env=env)
    assert p.returncode == 1, (p.stdout, p.stderr)
    assert "UNRELIABLE" in p.stderr


@_needs_bash
def test_check_tz_passes_on_agreement() -> None:
    # now_ist() is always +0530; forcing the suspect offset to match -> pass.
    env = dict(os.environ, CHECK_TZ_FAKE_OFF="+0530")
    p = subprocess.run([_BASH, str(_SCRIPTS / "check_tz.sh")],
                       capture_output=True, text=True, timeout=60, env=env)
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert p.stdout.startswith("OK:")
