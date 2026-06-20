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

from alerts.critical import write_critical_sentinel
from core.cron_registry import CronJob, CronRegistry
from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist
from scripts.cron_report_render import (
    COMPLETED, FAILED, MISSED, NO_SIGNAL, NOT_TRACKED, PENDING,
    PENDING_REDESIGN, SKIPPED, CronReport, JobOutcome, briefing_subject,
    eod_subject, render_briefing_html, render_briefing_plaintext,
    render_briefing_telegram, render_eod_html, render_eod_plaintext,
    render_eod_telegram,
)
from utils.cron_heartbeat import record_heartbeat

_log = get_logger("cron_officer")
_BAR = "━" * 24
_ROOT = Path(__file__).resolve().parent.parent
_MARKS_DIR = _ROOT / "data_store" / "cron_marks"
_AUDIT_DIR = _ROOT / "data_store" / "cron_audit"

# daily_report heartbeat is DEFERRED (lands with the xlsx redesign) — Bug C.
# Until then it is shown as ⏸ Pending (never ❌/⚠️, never CRITICAL).
_PENDING_REDESIGN_JOBS = {"daily_report"}


def get_preflight_complete_signal(now: datetime, briefing_time: time = time(9, 20)) -> bool:
    """Phase 5.1 hook STUB: the morning briefing fires once pre-flight is done.
    For now this is a pure time gate (True at/after the configured briefing time);
    the pre-flight work (Diary #1) will later wire the real completion signal."""
    return now.time() >= briefing_time


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
# Full report model (Phase 2.4/4) — EVERY job due today, by detection method
# ─────────────────────────────────────────────────────────────────────────────

def _detect_mode(store: StateStore) -> str:
    """Paper | Live from the most recent trade's mode; default Live."""
    try:
        row = store.fetch_one(
            "SELECT mode FROM trades WHERE mode IS NOT NULL ORDER BY created_at DESC LIMIT 1")
        if row and row["mode"]:
            return str(row["mode"]).capitalize()
    except Exception:
        pass
    return "Live"


def _read_marker(name: str, today: date, marks_dir: Path) -> tuple[str, float, str]:
    """exit_code_file detection: read data_store/cron_marks/<name>.done
    ('<rc> <iso-ts>'). Fresh-today + rc 0 -> COMPLETED; rc!=0 -> FAILED;
    absent/stale -> NO_SIGNAL (informational, NEVER a false CRITICAL)."""
    try:
        parts = (marks_dir / f"{name}.done").read_text(encoding="utf-8").strip().split(None, 1)
        rc = int(parts[0])
        ts = parts[1] if len(parts) > 1 else ""
        if ts[:10] != today.isoformat():
            return NO_SIGNAL, 0.0, "no run recorded today"
        return (COMPLETED, 0.0, "") if rc == 0 else (FAILED, 0.0, f"exit {rc}")
    except (OSError, ValueError, IndexError):
        return NO_SIGNAL, 0.0, "marker pending"


def _classify_job(job: CronJob, today: date, now_time: time,
                  hb: dict, marks_dir: Path) -> JobOutcome:
    """One job's outcome via its effective detection method."""
    cat = job.effective_category
    due_label = _time_label(job).strip()
    due_sort = job.due_time or time(23, 59)

    if job.name in _PENDING_REDESIGN_JOBS:   # Bug C deferral
        return JobOutcome(job.name, cat, due_label, due_sort, PENDING_REDESIGN,
                          job.effective_detection_method, 0.0,
                          "heartbeat lands with the daily_report.xlsx redesign")

    method = job.effective_detection_method
    if method == "heartbeat_db":
        row = hb.get(job.name)
        if row is not None:
            status = (row.get("status") or "").upper()
            rt = float(row.get("duration_sec") or 0.0)
            if status == "FAILED":
                return JobOutcome(job.name, cat, due_label, due_sort, FAILED, method, rt,
                                  (row.get("message") or "failed")[:120])
            if status == "SKIPPED":
                return JobOutcome(job.name, cat, due_label, due_sort, SKIPPED, method, rt,
                                  "non-trading day")
            # SUCCESS / PARTIAL / STARTED (Bug B self-row) -> completed
            return JobOutcome(job.name, cat, due_label, due_sort, COMPLETED, method, rt, "")
        if job.due_time is not None and now_time < job.due_time:
            return JobOutcome(job.name, cat, due_label, due_sort, PENDING, method, 0.0, "")
        return JobOutcome(job.name, cat, due_label, due_sort, MISSED, method, 0.0, "")

    if method == "exit_code_file":
        st, rt, note = _read_marker(job.name, today, marks_dir)
        if st == NO_SIGNAL and job.due_time is not None and now_time < job.due_time:
            st, note = PENDING, ""
        return JobOutcome(job.name, cat, due_label, due_sort, st, method, rt, note)

    # detection_method none / log_marker (unimplemented) -> visibility only
    return JobOutcome(job.name, cat, due_label, due_sort, NOT_TRACKED, method, 0.0,
                      job.excluded_reason or "")


def _compute_severity(jobs: list, watcher_stale: bool) -> str:
    """CRITICAL on any FAILED/MISSED or a stale security watcher. ⏸ Pending,
    NO_SIGNAL and NOT_TRACKED never escalate (Bug C / first-deploy safe)."""
    if watcher_stale or any(j.status in (FAILED, MISSED) for j in jobs):
        return "CRITICAL"
    return "INFO"


def _change_log(registry: CronRegistry, today: date, audit_dir: Path) -> tuple[list, list]:
    """Diff today's job-name set vs the most recent prior snapshot; persist today's
    snapshot (data_store/cron_audit/job_list_<date>.json) for tomorrow's diff."""
    import json as _json
    names = sorted(j.name for j in registry.all_jobs())
    added: list = []
    removed: list = []
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        prior_names = None
        for p in sorted(audit_dir.glob("job_list_*.json"), reverse=True):
            if p.stem.replace("job_list_", "") < today.isoformat():
                prior_names = set(_json.loads(p.read_text(encoding="utf-8")))
                break
        if prior_names is not None:
            added = [n for n in names if n not in prior_names]
            removed = sorted(prior_names - set(names))
        (audit_dir / f"job_list_{today.isoformat()}.json").write_text(
            _json.dumps(names, indent=2), encoding="utf-8")
    except Exception as exc:  # never block the report on the change-log
        _log.warning("cron_officer.change_log_failed", extra={"error": str(exc)})
    return added, removed


def build_report(registry: CronRegistry, store: StateStore, today: date,
                 config_dir: Path, now_time: time, *, is_eod: bool,
                 root: Path = _ROOT, marks_dir: Path = _MARKS_DIR,
                 audit_dir: Path = _AUDIT_DIR, mode: Optional[str] = None) -> CronReport:
    """Phase 2.4/4: the full per-job report covering EVERY job due today (not
    just the monitored subset), each classified by its detection method."""
    hb = _today_heartbeats(store, today) if store is not None else {}
    due = sorted(registry.jobs_due_on(today, config_dir), key=_sort_key)
    jobs = [_classify_job(j, today, now_time, hb, marks_dir) for j in due]
    # Security-watcher supervision uses REAL wall-clock (not a simulated date).
    wline, watcher_stale = security_watcher_health(root, now_ist())
    added, removed = _change_log(registry, today, audit_dir) if is_eod else ([], [])
    excluded = [(j.name, j.excluded_reason or "")
                for j in registry.all_jobs() if j.excluded_reason]
    extra = [wline,
             "Known: daily_report heartbeat is pending the xlsx redesign (shown ⏸ Pending)."]
    return CronReport(
        day=today, weekday=today.strftime("%A"),
        mode=mode or _detect_mode(store), is_eod=is_eod, jobs=jobs,
        severity=_compute_severity(jobs, watcher_stale),
        added=added, removed=removed, excluded=excluded, extra_lines=extra,
        ts_iso=now_ist().isoformat(),
        alert_id=now_ist().strftime("%Y%m%d_%H%M%S") + ("_eod" if is_eod else "_brief"),
        ban_active=registry.officer.ban_active(today),
    )


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


def _hhmm(s: str, default: time) -> time:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", s or "")
    if not m:
        return default
    hh, mm = int(m.group(1)), int(m.group(2))
    return time(hh, mm) if (0 <= hh <= 23 and 0 <= mm <= 59) else default


def _resolve_sentinel_dir() -> Path:
    """Where alert_watcher looks for sentinels (best-effort from config)."""
    try:
        from core.config_loader import load_all
        return Path(load_all().system.alerts.sentinel_dir)
    except Exception:
        return _ROOT / "data_store"


def _send_telegram_md(text: str, severity: str, config_dir: Path) -> None:
    """Best-effort Telegram send with MarkdownV2 (used OUTSIDE the ban window)."""
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=_log, config_dir=config_dir)
        if notifier is None:
            _log.info("cron_officer.telegram_unconfigured")
            return
        try:  # parse_mode best-effort; plain still delivers if unsupported
            notifier.send(severity=severity, title="Cron Officer", body=text,
                          source_module="cron_officer", parse_mode="MarkdownV2")
        except TypeError:
            notifier.send(severity=severity, title="Cron Officer", body=text,
                          source_module="cron_officer")
    except Exception as exc:
        _log.error("cron_officer.telegram_failed", extra={"error": str(exc)})


def deliver_report(report: CronReport, config_dir: Path, *, dry_run: bool,
                   sentinel_dir: Optional[Path] = None) -> dict:
    """Render + route a CronReport. EOD ALWAYS emails (rich HTML + plain mirror);
    the morning briefing emails ONLY during the Telegram ban, else Telegrams.
    Returns the rendered parts (dry-run inspection / tests). Never raises."""
    if report.is_eod:
        html, plain, tg = (render_eod_html(report), render_eod_plaintext(report),
                           render_eod_telegram(report))
        subject = eod_subject(report)
    else:
        html, plain, tg = (render_briefing_html(report), render_briefing_plaintext(report),
                           render_briefing_telegram(report))
        subject = briefing_subject(report)
    parts = {"subject": subject, "html": html, "plain": plain, "telegram": tg}

    if dry_run:
        print(f"[DRY-RUN] subject: {subject}\n")
        print(plain)
        return parts

    sdir = sentinel_dir or _resolve_sentinel_dir()
    ban = report.ban_active
    if report.is_eod or ban:        # EOD always emails; briefing emails during ban
        try:
            write_critical_sentinel(
                title=f"Cron {'EOD' if report.is_eod else 'Briefing'} {report.day.isoformat()}",
                body=plain, source_module="cron_officer", sentinel_dir=sdir,
                context={"severity": report.severity},
                subject=subject, content_type="text/html",
                plain_fallback=plain, html_body=html,
            )
        except Exception as exc:
            _log.error("cron_officer.email_sentinel_failed", extra={"error": str(exc)})
    if not ban:                     # Telegram only outside the ban window
        _send_telegram_md(tg, report.severity, config_dir)
    return parts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Cron Officer")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--briefing", action="store_true")
    mode.add_argument("--eod-summary", action="store_true")
    mode.add_argument("--check-change", action="store_true")
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--db-path", type=Path, default=Path("data_store/trading_system.db"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-dry-run", action="store_true",
                        help="Render + print only (never send); bypass the holiday-guard skip (test).")
    parser.add_argument("--as-of-date", type=lambda s: date.fromisoformat(s), default=None,
                        help="Simulate the report for YYYY-MM-DD (dry-run a market day on a non-market day).")
    args = parser.parse_args(argv)

    try:
        registry = CronRegistry.load(args.config_dir / "cron_registry.yaml")
    except Exception as e:
        _log.error("cron_officer.registry_load_failed", extra={"error": str(e)})
        print(f"Failed to load cron registry: {e}")
        return 1

    now = now_ist()
    today = args.as_of_date or now.date()
    simulated = args.as_of_date is not None
    force = args.force_dry_run
    dry = args.dry_run or force          # force-dry-run never sends

    # --check-change needs no DB.
    if args.check_change:
        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
            crontab_text = result.stdout
        except Exception as e:
            print(f"Could not read crontab: {e}")
            return 1
        msg, has_diff = build_change_report(registry, crontab_text)
        if has_diff:
            _send("WARNING", "Cron Divergence", msg, args.config_dir, dry)
            return 1
        print(msg)
        return 0

    # briefing + eod-summary need the store (heartbeats + mode).
    store: Optional[StateStore] = None
    if args.db_path.exists():
        store = StateStore(args.db_path)
    elif not simulated:
        print(f"Database not found: {args.db_path}")
        return 1

    try:
        if args.briefing:
            from utils.holiday_guard import is_trading_day
            try:
                trading = is_trading_day(today, args.config_dir)
            except Exception:
                trading = today.weekday() < 5
            if not trading and not force:
                # Phase 5.1: no morning briefing on weekends/NSE holidays. Still
                # record the heartbeat so the EOD report doesn't flag it missed.
                if not dry:
                    record_heartbeat("cron_officer_briefing", db_path=args.db_path)
                print(f"non-trading day ({today.isoformat()}) — morning briefing skipped")
                return 0
            # Phase 5.1 pre-flight gate (stub): real cron fires at the briefing time.
            get_preflight_complete_signal(now, _hhmm(registry.officer.morning_briefing_time, time(9, 20)))
            now_time = (_hhmm(registry.officer.morning_briefing_time, time(9, 20))
                        if simulated else now.time())
            report = build_report(registry, store, today, args.config_dir, now_time, is_eod=False)
            deliver_report(report, args.config_dir, dry_run=dry)
            if not dry:
                record_heartbeat("cron_officer_briefing", db_path=args.db_path)
            return 0

        # --eod-summary
        # Bug B: record a STARTED heartbeat BEFORE building the report so the
        # officer stops counting ITSELF as missed; a SUCCESS row lands at the end.
        if not dry:
            record_heartbeat("cron_officer_eod", status="STARTED", db_path=args.db_path)
        now_time = (registry.eod_report_time(today, args.config_dir)
                    if simulated else now.time())
        report = build_report(registry, store, today, args.config_dir, now_time, is_eod=True)
        deliver_report(report, args.config_dir, dry_run=dry)
        if not dry:
            record_heartbeat("cron_officer_eod", status="SUCCESS", db_path=args.db_path)
        return 0
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
