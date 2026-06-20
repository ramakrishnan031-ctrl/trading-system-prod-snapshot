#!/usr/bin/env python3
"""
scripts/cron_officer.py -- Trading System v2  (TASK #3 / Cron Officer)

Monitors, reports, and alerts on cron-job execution using the registry
(config/cron_registry.yaml) as the single source of truth and the cron_heartbeat
table as the execution record. Does NOT poll — per-job alerts are emitted by the
jobs themselves (HeartbeatTimer + alerts/cron_alerts.py). This officer adds:

    --briefing     Morning schedule for today (04:55 daily).
    --eod-summary  EOD execution report: completed / failed / missed (18:30 Mon-Fri).
    --check-change Diff config/cron_registry.yaml vs the live `crontab -l`.

Common flags: --dry-run (print, don't send), --config-dir, --db-path.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from core.cron_registry import CronJob, CronRegistry
from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist
from utils.cron_heartbeat import record_heartbeat

_log = get_logger("cron_officer")
_BAR = "━" * 24
_ROOT = Path(__file__).resolve().parent.parent


def security_watcher_health(root: Path, now: datetime) -> tuple[str, bool]:
    """Supervise the security-watcher service (VM Security Manager). It rewrites
    data_store/security_state.json every ~60s pass, so a stale (>5 min) or missing
    file means the watcher is likely DOWN. Returns (report_line, stale)."""
    p = root / "data_store" / "security_state.json"
    try:
        age = now.timestamp() - p.stat().st_mtime
    except OSError:
        return ("🔴 Security watcher: state file MISSING — service may be down", True)
    if age <= 300:
        return (f"🔒 Security watcher: alive (last pass {int(age)}s ago)", False)
    return (f"🔴 Security watcher: STALE — no pass in {int(age) // 60}m (service may be DOWN)", True)


# ─────────────────────────────────────────────────────────────────────────────
# Briefing
# ─────────────────────────────────────────────────────────────────────────────

def _time_label(job: CronJob) -> str:
    if job.due_time is not None:
        return job.due_time.strftime("%H:%M")
    if job.cadence == "intraday":
        return "*/5  "
    if job.cadence == "hourly":
        return "hourly"
    return "  -  "


def _sort_key(job: CronJob):
    return (0, job.due_time) if job.due_time is not None else (1, time(23, 59))


def build_briefing(registry: CronRegistry, today: date, config_dir: Path,
                   holiday_name: Optional[str] = None) -> str:
    """Build the morning-briefing message for `today`."""
    from utils.holiday_guard import is_trading_day

    try:
        trading = is_trading_day(today, config_dir)
    except Exception:
        trading = today.weekday() < 5

    day_str = today.strftime("%d-%b (%A)")
    due = sorted(registry.jobs_due_on(today, config_dir), key=_sort_key)

    if not trading:
        # Only the all-days jobs run; market jobs skipped.
        reason = f"NSE Holiday: {holiday_name}" if holiday_name else "Weekend"
        running = [j.name for j in due]  # jobs_due_on already excludes market_day on non-trading days
        lines = [
            f"📋 [LFL836] {reason} — {day_str}",
            "No market-day jobs today.",
            f"Only running: {', '.join(running) if running else 'none'}",
        ]
        return "\n".join(lines)

    crit = sum(1 for j in due if j.critical)
    lines = [f"📋 [LFL836] Today's Schedule — {day_str}", _BAR]
    for j in due:
        mark = " ⚡" if j.critical else ""
        lines.append(f"{_time_label(j)}  {j.name}{mark}")
    lines += [_BAR, f"Total: {len(due)} jobs | Critical: {crit}"]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# EOD summary
# ─────────────────────────────────────────────────────────────────────────────

def _today_heartbeats(store: StateStore, today: date) -> dict[str, dict]:
    """Latest heartbeat per job for `today` (IST)."""
    midnight_iso = datetime.combine(today, time.min).isoformat()
    rows = store.get_cron_heartbeats_since(midnight_iso)
    latest: dict[str, dict] = {}
    for r in rows:  # rows are DESC by executed_at; first seen is latest
        latest.setdefault(r["job_name"], r)
    return latest


def build_eod_summary(registry: CronRegistry, store: StateStore, today: date,
                      config_dir: Path, now_time: time) -> tuple[str, bool]:
    """
    Build the EOD execution report. Returns (message, critical_miss) where
    critical_miss is True if a CRITICAL job that was due (by now) has no heartbeat.
    """
    hb = _today_heartbeats(store, today)
    expected = registry.expected_heartbeat_jobs(today, config_dir, before_time=now_time)

    completed, failed, skipped, missed = [], [], [], []
    total_runtime = 0.0
    for job in expected:
        row = hb.get(job.name)
        if row is None:
            missed.append(job)
            continue
        status = (row.get("status") or "").upper()
        total_runtime += float(row.get("duration_sec") or 0.0)
        if status == "FAILED":
            failed.append(job)
        elif status == "SKIPPED":
            skipped.append(job)
        else:  # SUCCESS / PARTIAL
            completed.append(job)

    critical_miss = any(j.critical for j in missed)
    mins, secs = divmod(int(total_runtime), 60)
    n_exp = len(expected)

    def _names(jobs):
        return ", ".join(j.name for j in jobs) if jobs else "—"

    lines = [
        f"📊 [LFL836] Cron Officer — Daily Report — {today.strftime('%d-%b')}",
        _BAR,
        f"✅ Completed: {len(completed)}/{n_exp}",
        f"❌ Failed: {len(failed)} ({_names(failed)})",
        f"⏭ Skipped: {len(skipped)} ({_names(skipped)})",
        f"⏱ Total runtime: {mins}m {secs}s",
        f"🔴 Missed: {len(missed)} ({_names(missed)})",
        _BAR,
    ]
    return "\n".join(lines), critical_miss


# ─────────────────────────────────────────────────────────────────────────────
# Change detection (registry vs live crontab)
# ─────────────────────────────────────────────────────────────────────────────

def _job_token(script: str) -> Optional[str]:
    """A distinctive token to match a registry job against a crontab line."""
    m = re.search(r"([A-Za-z0-9_]+)\.py", script)
    if m:
        return m.group(1)
    m = re.search(r"-m\s+([A-Za-z0-9_.]+)", script)
    if m:
        return m.group(1)
    return None


def _cron_hhmm(min_f: str, hour_f: str) -> Optional[str]:
    """Return 'HH:MM' if both fields are plain integers, else None."""
    if min_f.isdigit() and hour_f.isdigit():
        return f"{int(hour_f):02d}:{int(min_f):02d}"
    return None


def parse_crontab(text: str) -> list[dict]:
    """Parse `crontab -l` text into [{hhmm, token, raw}] for command lines."""
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(None, 5)
        if len(fields) < 6:
            continue
        minute, hour, _dom, _mon, _dow, command = fields
        out.append({
            "hhmm": _cron_hhmm(minute, hour),
            "token": _job_token(command),
            "raw": command,
        })
    return out


def build_change_report(registry: CronRegistry, crontab_text: str) -> tuple[str, bool]:
    """
    Diff python jobs in the registry against the live crontab (matched by token).
    Returns (message, has_diff). Shell jobs are not token-matched (noted).
    """
    reg_py = {_job_token(j.script): j for j in registry.all_jobs()
              if j.type == "python" and _job_token(j.script)}
    cron_lines = parse_crontab(crontab_text)
    cron_tokens = {c["token"]: c for c in cron_lines if c["token"]}

    added = [tok for tok in reg_py if tok not in cron_tokens]       # in registry, not crontab
    missing = [tok for tok in cron_tokens if tok not in reg_py]     # in crontab, not registry
    changed = []
    for tok, job in reg_py.items():
        c = cron_tokens.get(tok)
        if c and c["hhmm"] and job.due_time is not None:
            if c["hhmm"] != job.due_time.strftime("%H:%M"):
                changed.append(f"{tok} {c['hhmm']}→{job.due_time.strftime('%H:%M')}")

    has_diff = bool(added or missing or changed)
    if not has_diff:
        return "✅ [LFL836] Cron registry matches live crontab (python jobs).", False

    lines = ["⚠️ [LFL836] Cron Divergence Detected", _BAR]
    for tok in added:
        lines.append(f"+ in registry, not in crontab: {tok} ({reg_py[tok].schedule})")
    for tok in missing:
        lines.append(f"- in crontab, not in registry: {tok}")
    for ch in changed:
        lines.append(f"~ time changed: {ch}")
    return "\n".join(lines), True


# ─────────────────────────────────────────────────────────────────────────────
# Send + CLI
# ─────────────────────────────────────────────────────────────────────────────

def _send(severity: str, title: str, body: str, config_dir: Path, dry_run: bool) -> None:
    print(body)
    if dry_run:
        print(f"\n[DRY-RUN] would send {severity}: {title}")
        return
    try:
        from alerts.telegram_notifier import TelegramNotifier

        notifier = TelegramNotifier.from_env(logger=_log, config_dir=config_dir)
        if notifier is None:
            _log.info("cron_officer.telegram_unconfigured")
            return
        notifier.send(severity=severity, title=title, body=body, source_module="cron_officer")
    except Exception as exc:
        _log.error("cron_officer.send_failed", extra={"error": str(exc)})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Cron Officer")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--briefing", action="store_true")
    mode.add_argument("--eod-summary", action="store_true")
    mode.add_argument("--check-change", action="store_true")
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--db-path", type=Path, default=Path("data_store/trading_system.db"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        registry = CronRegistry.load(args.config_dir / "cron_registry.yaml")
    except Exception as e:
        _log.error("cron_officer.registry_load_failed", extra={"error": str(e)})
        print(f"Failed to load cron registry: {e}")
        return 1

    now = now_ist()

    if args.briefing:
        from utils.holiday_guard import get_holiday_name
        holiday = None
        try:
            holiday = get_holiday_name(now.date(), args.config_dir)
        except Exception:
            pass
        msg = build_briefing(registry, now.date(), args.config_dir, holiday)
        _send("INFO", "Today's Schedule", msg, args.config_dir, args.dry_run)
        record_heartbeat("cron_officer_briefing", db_path=args.db_path)
        return 0

    if args.eod_summary:
        if not args.db_path.exists():
            print(f"Database not found: {args.db_path}")
            return 1
        store = StateStore(args.db_path)
        msg, critical_miss = build_eod_summary(registry, store, now.date(), args.config_dir, now.time())
        store.close()
        # Phase 3: supervise the security-watcher service heartbeat.
        wline, watcher_stale = security_watcher_health(_ROOT, now)
        msg = f"{msg}\n{wline}"
        severity = "CRITICAL" if (critical_miss or watcher_stale) else "INFO"
        _send(severity, "Cron Daily Report", msg, args.config_dir, args.dry_run)
        record_heartbeat("cron_officer_eod", db_path=args.db_path)
        return 0

    # --check-change
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        crontab_text = result.stdout
    except Exception as e:
        print(f"Could not read crontab: {e}")
        return 1
    msg, has_diff = build_change_report(registry, crontab_text)
    if has_diff:
        _send("WARNING", "Cron Divergence", msg, args.config_dir, args.dry_run)
        return 1
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
