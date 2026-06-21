"""
scripts/preflight/orchestrator.py -- the pre-flight entry point.

    python -m scripts.preflight.orchestrator --phase A [--dry-run] [--as-of-date YYYY-MM-DD]

Runs the phase's check-set, applies SAFE auto-fixes to failed fixable checks
(re-checking after each), rolls the results into a PreflightReport, writes the
cross-phase sentinel, and prints the terminal report. ALERT-ONLY: a CRITICAL
finding never changes the process exit code -- exit is non-zero only if the
orchestrator itself crashed, so the Cron Officer's exit-code marker reflects
"did pre-flight run", not "did the system pass".

Phase A is fully wired here. Phase B (engine readiness) + Phase C (signal warmup)
check-sets are populated in the next build step (they need the app running).
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.preflight import autofix, sentinel
from scripts.preflight.base import Check, CheckContext, CheckResult, Status
from scripts.preflight.checks import broker as _broker_checks
from scripts.preflight.checks import phase_a_checks, phase_b_checks
from scripts.preflight.report import CheckRecord, PreflightReport, apply_to_sentinel, render_terminal

try:
    from core.logger import get_logger
    _log = get_logger("preflight.orchestrator")
except Exception:  # pragma: no cover
    import logging
    _log = logging.getLogger("preflight.orchestrator")


def _now():
    try:
        from core.time_authority import now_ist
        return now_ist()
    except Exception:  # pragma: no cover
        return datetime.now(timezone.utc)


def make_run_id(phase: str, now: Optional[datetime] = None) -> str:
    now = now or _now()
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{phase.lower()}"


def checks_for_phase(phase: str) -> List[Check]:
    if phase == "A":
        return phase_a_checks()
    if phase == "B":
        return phase_b_checks()
    # Phase C (signal warmup) check-set is added next.
    return []


def is_trading_day(ctx: CheckContext) -> bool:
    """Fail-open: if the calendar can't be read, run rather than silently skip."""
    try:
        from utils.holiday_guard import is_trading_day as _itd
        return _itd(ctx.as_of_date, ctx.config_dir)
    except Exception as exc:  # pragma: no cover
        _log.warning("preflight.holiday_guard_unavailable", extra={"error": str(exc)})
        return True


def run_phase(ctx: CheckContext, checks: List[Check], run_id: str) -> PreflightReport:
    """Pure-ish: run each check, auto-fix fixable failures, return a report.

    No I/O side effects beyond the checks themselves + audited fixes -- the
    caller writes the sentinel and prints. A check that raises is converted to a
    FAIL (run() must never crash the whole pre-flight).
    """
    started = _now().isoformat()
    records: List[CheckRecord] = []

    for check in checks:
        t0 = time.perf_counter()
        try:
            res = check.run(ctx)
        except Exception as exc:
            res = CheckResult(Status.FAIL, detail=f"check raised: {type(exc).__name__}: {exc}")
        status = res.status
        fix_attempted = False
        fix_result = ""

        if res.status is Status.FAIL and check.auto_fixable:
            outcome = autofix.attempt(check, ctx, run_id=run_id)
            if outcome.attempted:
                fix_attempted = True
                if outcome.final_status is Status.AUTOFIXED:
                    fix_result = "SUCCESS"
                    status = Status.AUTOFIXED
                    if outcome.recheck is not None:
                        res = outcome.recheck
                else:
                    fix_result = "FAILED"

        duration_ms = int((time.perf_counter() - t0) * 1000)
        records.append(CheckRecord(
            name=check.name,
            group=check.group,
            criticality=check.criticality,
            status=status,
            detail=res.detail,
            duration_ms=duration_ms,
            fix_attempted=fix_attempted,
            fix_result=fix_result,
        ))

    completed = _now().isoformat()
    return PreflightReport(
        phase=ctx.phase,
        run_date=ctx.as_of_date,
        run_id=run_id,
        mode=ctx.mode,
        account=ctx.account,
        started_at=started,
        completed_at=completed,
        records=records,
    )


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Trading System pre-flight check")
    p.add_argument("--phase", choices=["A", "B", "C"], default="A")
    p.add_argument("--config-dir", type=Path, default=Path("config"))
    p.add_argument("--db-path", type=Path, default=Path("data_store/trading_system.db"))
    p.add_argument("--mode", choices=["live", "paper"], default="live")
    p.add_argument("--account", default="LFL836")
    p.add_argument("--as-of-date", type=lambda s: date.fromisoformat(s), default=None,
                   help="simulate a given date (YYYY-MM-DD); default = today (IST)")
    p.add_argument("--dry-run", action="store_true",
                   help="never mutate (no auto-fix) and never send alerts")
    p.add_argument("--sentinel-path", type=Path, default=sentinel.DEFAULT_SENTINEL_PATH)
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    as_of = args.as_of_date or _now().date()

    ctx = CheckContext(
        config_dir=args.config_dir,
        db_path=args.db_path,
        mode=args.mode,
        account=args.account,
        as_of_date=as_of,
        phase=args.phase,
        dry_run=args.dry_run,
    )

    # Holiday guard: no deep checks on a non-trading day.
    if not is_trading_day(ctx):
        print(f"🛑 NSE holiday / non-trading day ({as_of.isoformat()}) — pre-flight skipped.")
        _log.info("preflight.skipped_holiday", extra={"date": as_of.isoformat()})
        return 0

    # One standalone broker probe (on-disk token session) injected for the live
    # broker checks; None in paper / when no valid token. Reads only -> dry-run safe.
    ctx.extra["broker_probe"] = _broker_checks.build_broker_probe(ctx)

    checks = checks_for_phase(args.phase)
    if not checks:
        print(f"Phase {args.phase}: no checks registered yet (added in a later build step).")
        return 0

    run_id = make_run_id(args.phase, _now())
    report = run_phase(ctx, checks, run_id)

    _out = render_terminal(report)
    try:
        print(_out)
    except UnicodeEncodeError:
        # a non-UTF-8 console (e.g. Windows cp1252) must never crash the run
        print(_out.encode("ascii", "replace").decode("ascii"))

    # Persist the sentinel (skipped in dry-run so a rehearsal can't overwrite the
    # real day's state).
    if not args.dry_run:
        s = sentinel.load(args.sentinel_path)
        if s.run_date != as_of.isoformat():
            s = sentinel.Sentinel()  # new day -> fresh
        s = apply_to_sentinel(report, s)
        sentinel.write(s, args.sentinel_path)

    _log.info("preflight.phase_complete", extra={
        "phase": args.phase, "overall": report.overall_status,
        "critical": report.failed_critical, "warnings": report.warnings,
        "autofixed": report.autofixed, "run_id": run_id,
    })

    # ALERT-ONLY: success exit even with CRITICAL findings (they go to the
    # report/sentinel/alert, not the exit code).
    return 0


if __name__ == "__main__":
    sys.exit(main())
