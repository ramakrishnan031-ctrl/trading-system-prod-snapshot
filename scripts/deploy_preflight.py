#!/usr/bin/env python3
# =============================================================================
# scripts/deploy_preflight.py  —  T4: operator preflight before any
# market-window-gated action (deploy / restart). Runs on the PC; the VM is the
# AUTHORITATIVE clock and market-state source.
#
# It anchors ONLY on `date -u` (UTC — needs no zoneinfo, safe in MSYS2) and on
# the VM's own now_ist() / MarketWindows.is_market_open() — NEVER on
# `TZ='Asia/Kolkata' date` (the 29-Jun MSYS2 defect).
#
# Steps:
#   1. Run scripts/check_tz.sh for VISIBILITY only (surface its result; never
#      hard-block — this PC is known-broken-MSYS2 so it always fails here).
#   2. PC UTC (`date -u +%s`) vs VM UTC (`ssh <vm> date -u +%s`):
#      |skew| <= tolerance (default 120s) else FAIL LOUD. This is the ONLY use
#      of PC time — for AGREEMENT, never as the decision clock.
#   3. Market-state AUTHORITATIVE from the VM (now_ist + holiday-aware
#      is_market_open). Market-gated action while market OPEN -> FAIL LOUD.
#   4. Exit 0 (safe) / non-zero with a loud reason.
#
# Exactly what would have prevented the 29-Jun mistake: anchors on date -u +
# the VM, never on TZ='Asia/Kolkata' date.
# =============================================================================
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_VM = os.environ.get("TRADING_VM", "trading-vm")
DEFAULT_VM_PROJ = "/home/ubuntu/systems/trading-system"
DEFAULT_VM_PYTHON = "/home/ubuntu/systems/venv/bin/python"
DEFAULT_TOLERANCE_S = 120

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The VM market-state probe: build a holiday-aware MarketWindows from config and
# ask it about now_ist(). One line so it travels cleanly over ssh `python -c`.
_VM_MARKET_CODE = (
    "from core.config_loader import load_all;"
    "from core.market_windows import MarketWindows;"
    "from core.time_authority import now_ist;"
    "from datetime import time;"
    "c=load_all();th=c.system.trading_hours;"
    "mw=MarketWindows("
    "market_open=time(*map(int,th.market_open.split(':'))),"
    "market_close=time(*map(int,th.market_close.split(':'))),"
    "holidays={h.date for h in c.nse_holidays.holidays});"
    "n=now_ist();print(n.isoformat(), mw.is_market_open(n))"
)


# ── thin I/O helpers (monkeypatched in tests) ────────────────────────────────
def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def tz_self_check() -> tuple[bool | None, str]:
    """Run scripts/check_tz.sh for visibility. Returns (ok|None, last-line)."""
    script = _REPO_ROOT / "scripts" / "check_tz.sh"
    try:
        p = _run(["bash", str(script)])
    except Exception as exc:  # bash missing etc. — visibility only, never fatal
        return None, f"could not run check_tz.sh ({type(exc).__name__}: {exc})"
    detail = (p.stdout + p.stderr).strip().splitlines()
    return (p.returncode == 0), (detail[-1] if detail else "")


def pc_utc_epoch() -> int:
    """PC UTC seconds via `date -u +%s` (UTC needs no zoneinfo — safe in MSYS2)."""
    p = _run(["date", "-u", "+%s"])
    return int(p.stdout.strip())


def vm_utc_epoch(vm_host: str) -> int:
    """VM UTC seconds via `ssh <vm> date -u +%s` (the authoritative clock)."""
    p = _run(["ssh", vm_host, "date -u +%s"])
    if p.returncode != 0 or not p.stdout.strip():
        raise RuntimeError(f"vm utc query failed: rc={p.returncode} {p.stderr.strip()[:200]}")
    return int(p.stdout.strip())


def vm_market_state(vm_host: str, vm_proj: str, vm_python: str) -> tuple[str, bool]:
    """Authoritative (now_ist ISO, is_market_open) straight from the VM."""
    remote = f'cd {vm_proj} && PYTHONPATH=. {vm_python} -c "{_VM_MARKET_CODE}"'
    p = _run(["ssh", vm_host, remote])
    out = p.stdout.strip()
    if p.returncode != 0 or not out:
        raise RuntimeError(f"vm market-state failed: rc={p.returncode} {p.stderr.strip()[:200]}")
    parts = out.split()
    return parts[0], (parts[-1] == "True")


# ── pure decision logic (fully unit-testable, no I/O) ─────────────────────────
def evaluate(
    *,
    pc_epoch: int,
    vm_epoch: int,
    market_open: bool,
    market_gated: bool,
    tolerance_s: int = DEFAULT_TOLERANCE_S,
    tz_ok: bool | None = None,
    tz_detail: str = "",
    vm_iso: str = "",
) -> tuple[int, list[str]]:
    """Return (exit_code, report_lines). 0 = safe to proceed, non-zero = abort."""
    lines: list[str] = []
    fail = False

    # 1. tz self-check — VISIBILITY ONLY, never blocks.
    if tz_ok is False:
        lines.append(f"[INFO] tz self-check FAILED (expected on this MSYS2 PC; NOT blocking): {tz_detail}")
    elif tz_ok is True:
        lines.append("[INFO] tz self-check OK (TZ='Asia/Kolkata' agrees with now_ist).")
    else:
        lines.append(f"[INFO] tz self-check unavailable (NOT blocking): {tz_detail}")

    # 2. PC/VM UTC agreement — the ONLY use of PC time (agreement, not decision).
    skew = abs(pc_epoch - vm_epoch)
    if skew <= tolerance_s:
        lines.append(f"[OK] PC/VM UTC agree (skew {skew}s <= {tolerance_s}s).")
    else:
        fail = True
        lines.append(f"[FAIL] PC/VM UTC OUT OF SYNC (skew {skew}s > {tolerance_s}s) -- clocks disagree; do NOT deploy.")

    # 3/4. Market-state — AUTHORITATIVE from the VM.
    if market_gated:
        if market_open:
            fail = True
            lines.append(f"[FAIL] VM reports MARKET OPEN ({vm_iso}) -- refusing market-gated action (no mid-session deploy/restart).")
        else:
            lines.append(f"[OK] VM reports market CLOSED ({vm_iso}) -- safe for a market-gated action.")
    else:
        lines.append("[INFO] action declared NOT market-gated -- skipping the market-open gate.")

    lines.append("PREFLIGHT: " + ("FAIL" if fail else "PASS"))
    return (1 if fail else 0), lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T4 deploy preflight (VM-authoritative time + market gate).")
    ap.add_argument("--vm-host", default=DEFAULT_VM, help=f"ssh host alias (default {DEFAULT_VM})")
    ap.add_argument("--vm-proj", default=DEFAULT_VM_PROJ, help="VM project dir")
    ap.add_argument("--vm-python", default=DEFAULT_VM_PYTHON, help="VM venv python")
    ap.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE_S, help="max PC/VM UTC skew (s)")
    ap.add_argument("--not-market-gated", action="store_true",
                    help="action is NOT market-window-gated (skip the market-open gate)")
    args = ap.parse_args(argv)

    tz_ok, tz_detail = tz_self_check()
    try:
        pc = pc_utc_epoch()
        vm = vm_utc_epoch(args.vm_host)
        vm_iso, market_open = vm_market_state(args.vm_host, args.vm_proj, args.vm_python)
    except Exception as exc:
        print(f"[FAIL] preflight could not gather VM/clock data: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("PREFLIGHT: FAIL", file=sys.stderr)
        return 2

    code, lines = evaluate(
        pc_epoch=pc, vm_epoch=vm, market_open=market_open,
        market_gated=not args.not_market_gated, tolerance_s=args.tolerance,
        tz_ok=tz_ok, tz_detail=tz_detail, vm_iso=vm_iso,
    )
    for ln in lines:
        print(ln, file=(sys.stderr if ln.startswith("[FAIL]") else sys.stdout))
    return code


if __name__ == "__main__":
    sys.exit(main())
