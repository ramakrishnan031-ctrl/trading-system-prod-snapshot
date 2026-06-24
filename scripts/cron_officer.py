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
    PENDING_REDESIGN, RAN_UNVERIFIED, SKIPPED, CronReport, JobOutcome, briefing_subject,
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
                  hb: dict, marks_dir: Path, grace_minutes: int = 0) -> JobOutcome:
    """One job's outcome via its effective detection method.

    ``grace_minutes`` (24-Jun race-debounce): a heartbeat job due within this many
    minutes of the snapshot may have just fired and not yet committed its
    heartbeat (the 09:20 cron-cluster race) -> treated as PENDING, not MISSED."""
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
        # Just-fired grace (race-debounce): a job due within grace_minutes of the
        # snapshot may not have committed its heartbeat yet (the 09:20:00 cluster
        # race, where now_time == the fixed briefing time == the job's due time).
        # Hold it as PENDING so a sub-minute commit lag never escalates to a false
        # CRITICAL; a genuinely missed job (due > grace ago) still returns MISSED.
        if job.due_time is not None and grace_minutes > 0:
            overdue_min = (now_time.hour * 60 + now_time.minute) - (
                job.due_time.hour * 60 + job.due_time.minute)
            if 0 <= overdue_min <= grace_minutes:
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


def _strategy_status_payload(config_dir: Path) -> Optional[dict]:
    """Slice 2: build the morning-briefing STRATEGY STATUS fragments. Reads
    trade_type + force_intraday_only from system_config.yaml (lightweight — the
    same values the running app loads) and renders via scripts.strategy_status.
    Returns None on ANY failure — the briefing must never break on the table."""
    try:
        import yaml as _yaml
        from scripts.strategy_status import (
            build_status_rows, compact_lists, render_html, render_plaintext,
        )
        sc = _yaml.safe_load(
            (config_dir / "system_config.yaml").read_text(encoding="utf-8")
        ) or {}
        tt = sc.get("trade_type", "INTRADAY")
        fio = bool(sc.get("force_intraday_only", True))
        rows = build_status_rows(config_dir, trade_type=tt, force_intraday_only=fio)
        will, wont, err = compact_lists(rows)
        return {
            "html": render_html(rows, tt, fio),
            "plain": render_plaintext(rows, tt),
            "will": will, "wont": wont, "err": err, "master": tt,
        }
    except Exception as exc:  # noqa: BLE001 — never block the briefing
        _log.warning("cron_officer.strategy_status_failed", extra={"error": str(exc)})
        return None


def _read_preflight_summary(today: date, root: Path = _ROOT) -> dict:
    """Map the pre-flight sentinel -> briefing-banner dict (reuses the sentinel
    loader). Missing or stale (run_date != today) => NOT_RUN."""
    try:
        from scripts.preflight.sentinel import load as _load
        s = _load(root / "data_store" / "preflight" / "today.json")
    except Exception:
        return {"status": "NOT_RUN"}
    if not s.run_date or s.run_date != today.isoformat():
        return {"status": "NOT_RUN"}
    return {"status": s.overall_status, "critical": s.critical_count,
            "warnings": s.warning_count, "autofixed": s.autofix_count,
            "alert_id": s.alert_id}


def _tier_mode_line(config_dir: Path) -> str:
    """Diary #4: one-line position-sizing mode badge for the EOD report."""
    try:
        import yaml
        data = yaml.safe_load((config_dir / "system_config.yaml").read_text(encoding="utf-8")) or {}
        ps = data.get("position_sizing") or {}
        if ps.get("enabled", True):
            return "Tier multiplier today: ON (score-tier x perf sizing)"
        return f"Tier multiplier today: OFF (flat Rs {ps.get('flat_value_rs')}/order)"
    except Exception:
        return "Tier multiplier today: (unknown - config unreadable)"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 (self-maintaining cron): ROSTER INTEGRITY + auto-discovery + self-test.
# FAIL-SAFE: every helper is best-effort and is called wrapped in try/except by
# build_report — a failure here NEVER crashes the core report (it degrades to an
# UNKNOWN line). The EOD report is the monitoring OUTPUT; it must not become its
# own single point of failure.
# ─────────────────────────────────────────────────────────────────────────────
_SEV_ORDER = {"INFO": 0, "WARN": 1, "CRITICAL": 2}


def _max_sev(a: str, b: str) -> str:
    return a if _SEV_ORDER.get(a, 0) >= _SEV_ORDER.get(b, 0) else b


def _registry_stamp(config_dir: Path) -> str:
    """Short sha256 of cron_registry.yaml — ties the live roster to its content
    (git is NOT available in the deployed working tree)."""
    import hashlib
    try:
        return hashlib.sha256((config_dir / "cron_registry.yaml").read_bytes()).hexdigest()[:12]
    except Exception:
        return "unknown"


def _read_crontab_safe() -> Optional[str]:
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        return r.stdout
    except Exception:
        return None


def _auto_discover(registry: CronRegistry, crontab_text: Optional[str]) -> list:
    """Live crontab lines whose resolved job is NOT in the registry -> runtime
    RAN_UNVERIFIED JobOutcomes. RUNTIME ONLY — never mutates the registry YAML."""
    if not crontab_text:
        return []
    from scripts.generate_crontab import _command_lines, parse as _parse, resolve_job_name as _rjn
    reg_names = {j.name for j in registry.all_jobs()}
    out: list = []
    for raw in _command_lines(crontab_text):
        try:
            f = _parse(raw)
            nm = _rjn(f)
        except Exception:
            continue                       # unparseable -> the drift-check owns it
        if nm in reg_names:
            continue
        due_label, due_sort = "—", time(23, 59)
        mn, hr = (f["cron_expression"].split() + ["", ""])[:2]
        if mn.isdigit() and hr.isdigit():
            due_label, due_sort = f"{int(hr):02d}:{int(mn):02d}", time(int(hr), int(mn))
        out.append(JobOutcome(nm, "ON_DEMAND", due_label, due_sort, RAN_UNVERIFIED, "none", 0.0,
                              "in crontab, not in registry — needs registry entry + contract"))
    return out


def _alert_path_health(config_dir: Path, sentinel_dir: Optional[Path] = None) -> tuple[bool, str]:
    """LOW-NOISE liveness: Telegram reachability via getMe (a read-only probe, NOT
    a posted message) + an EPHEMERAL sentinel-dir write (cleaned). Healthy = silent
    (this just feeds the roster block); a DEAD path escalates severity and rides the
    EOD email (the FIX-191 fallback). Never raises."""
    import os
    ok, bits = True, []
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        ok, _ = False, bits.append("telegram: no token")
    else:
        try:
            import requests
            r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=6)
            if r.status_code == 200 and r.json().get("ok"):
                bits.append("telegram: reachable")
            else:
                ok = False; bits.append(f"telegram: getMe HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            ok = False; bits.append(f"telegram: unreachable ({type(exc).__name__})")
    sdir = sentinel_dir or _resolve_sentinel_dir()
    try:
        sdir.mkdir(parents=True, exist_ok=True)
        probe = sdir / ".cron_officer_selftest.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        bits.append("sentinel-dir: writable")
    except Exception as exc:  # noqa: BLE001
        ok = False; bits.append(f"sentinel-dir: NOT writable ({type(exc).__name__})")
    return ok, "; ".join(bits)


def _roster_integrity(registry: CronRegistry, config_dir: Path, store: Optional[StateStore],
                      today: date, crontab_text: Optional[str]) -> tuple[list, str]:
    """The daily ROSTER INTEGRITY block (a-d) + sha256 stamp. Returns (lines,
    severity). Best-effort per check; a failing sub-check shows UNKNOWN, not a crash."""
    lines: list[str] = ["ROSTER INTEGRITY"]
    sev = "INFO"; issues = 0

    # (a) live == generate(registry)
    try:
        if crontab_text:
            from scripts.check_cron_drift import content_drift
            cd = content_drift(config_dir / "cron_registry.yaml", crontab_text)
            if cd.absent_critical or cd.unparseable:
                sev = _max_sev(sev, "CRITICAL"); issues += 1
                lines.append(f"  (a) live==generate: DRIFT — absent {cd.absent_critical or '-'}, "
                             f"unparseable {len(cd.unparseable)}")
            elif cd.absent_warn or cd.unregistered:
                sev = _max_sev(sev, "WARN"); issues += 1
                lines.append(f"  (a) live==generate: drift (WARN) — personal-absent "
                             f"{cd.absent_warn or '-'}, unregistered {len(cd.unregistered)}")
            else:
                lines.append("  (a) live == generate(registry): OK")
        else:
            lines.append("  (a) live==generate: UNKNOWN (crontab unavailable)")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  (a) live==generate: UNKNOWN ({type(exc).__name__})")

    # (b) contract coverage (P4 deliverable contracts pending; none silently unmonitored)
    try:
        needs = [j.name for j in registry.all_jobs()
                 if j.enabled and not j.personal_tooling and not j.excluded_reason]
        lines.append(f"  (b) contract coverage: 0/{len(needs)} deliverable contracts "
                     "(P4 pending; all jobs accounted for via monitored/excluded/personal)")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  (b) contract coverage: UNKNOWN ({type(exc).__name__})")

    # (c) alert path alive (low-noise self-test)
    try:
        c_ok, c_detail = _alert_path_health(config_dir)
        if not c_ok:
            sev = _max_sev(sev, "CRITICAL"); issues += 1
        lines.append(f"  (c) alert path: {'ALIVE' if c_ok else 'DEAD'} — {c_detail}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  (c) alert path: UNKNOWN ({type(exc).__name__})")

    # (d) officer + drift-check both heartbeated today (watch-the-watcher tier-1)
    try:
        hb = _today_heartbeats(store, today) if store is not None else {}
        down = [n for n in ("cron_officer_eod", "check_cron_drift") if n not in hb]
        if down:
            sev = _max_sev(sev, "WARN"); issues += 1
            lines.append(f"  (d) watcher heartbeats: MISSING {down}")
        else:
            lines.append("  (d) officer + drift-check both ran: OK")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  (d) watcher heartbeats: UNKNOWN ({type(exc).__name__})")

    verdict = "OK" if issues == 0 else f"DEGRADED ({issues})"
    lines[0] = f"ROSTER INTEGRITY: {verdict}   Registry: {_registry_stamp(config_dir)}"
    return lines, sev


def build_report(registry: CronRegistry, store: StateStore, today: date,
                 config_dir: Path, now_time: time, *, is_eod: bool,
                 root: Path = _ROOT, marks_dir: Path = _MARKS_DIR,
                 audit_dir: Path = _AUDIT_DIR, mode: Optional[str] = None,
                 crontab_text: Optional[str] = None) -> CronReport:
    """Phase 2.4/4: the full per-job report covering EVERY job due today (not
    just the monitored subset), each classified by its detection method."""
    hb = _today_heartbeats(store, today) if store is not None else {}
    due = sorted(registry.jobs_due_on(today, config_dir), key=_sort_key)
    grace = max(0, int(getattr(registry.officer, "miss_grace_minutes", 0)))
    jobs = [_classify_job(j, today, now_time, hb, marks_dir, grace) for j in due]

    # Phase 3 (EOD): auto-discovery of live-not-registry jobs -> RAN_UNVERIFIED.
    # FAIL-SAFE: a failure here must NOT crash the core report.
    ct = crontab_text
    if is_eod and ct is None:
        ct = _read_crontab_safe()
    if is_eod:
        try:
            jobs.extend(_auto_discover(registry, ct))
        except Exception as exc:  # noqa: BLE001
            _log.warning("cron_officer.auto_discover_failed", extra={"error": str(exc)})

    # Security-watcher supervision uses REAL wall-clock (not a simulated date).
    wline, watcher_stale = security_watcher_health(root, now_ist())
    added, removed = _change_log(registry, today, audit_dir) if is_eod else ([], [])
    excluded = [(j.name, j.excluded_reason or "")
                for j in registry.all_jobs() if j.excluded_reason]
    extra = [wline,
             "Known: daily_report heartbeat is pending the xlsx redesign (shown ⏸ Pending)."]
    if is_eod:
        extra.append(_tier_mode_line(config_dir))  # Diary #4: sizing-mode badge
    severity = _compute_severity(jobs, watcher_stale)

    # Phase 3 (EOD): ROSTER INTEGRITY block (a-d) + sha256 stamp. FAIL-SAFE — a
    # failure degrades to an UNKNOWN line; the core report still renders + sends.
    if is_eod:
        try:
            roster_lines, roster_sev = _roster_integrity(registry, config_dir, store, today, ct)
            extra += roster_lines
            severity = _max_sev(severity, roster_sev)
        except Exception as exc:  # noqa: BLE001
            _log.warning("cron_officer.roster_integrity_failed", extra={"error": str(exc)})
            extra.append("ROSTER INTEGRITY: UNKNOWN — computation failed (core report intact)")
    # Morning briefing embeds the pre-flight banner; a missing/stale sentinel
    # (pre-flight never ran / crashed) escalates the briefing to CRITICAL (spec).
    preflight = None
    strategy_status = None
    if not is_eod:
        preflight = _read_preflight_summary(today, root)
        if preflight.get("status") == "NOT_RUN":
            severity = "CRITICAL"
        strategy_status = _strategy_status_payload(config_dir)  # Slice 2
    return CronReport(
        day=today, weekday=today.strftime("%A"),
        mode=mode or _detect_mode(store), is_eod=is_eod, jobs=jobs,
        severity=severity,
        added=added, removed=removed, excluded=excluded, extra_lines=extra,
        ts_iso=now_ist().isoformat(),
        alert_id=now_ist().strftime("%Y%m%d_%H%M%S") + ("_eod" if is_eod else "_brief"),
        ban_active=registry.officer.ban_active(today),
        preflight=preflight,
        strategy_status=strategy_status,
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
    """Best-effort Telegram send with MarkdownV2 (used OUTSIDE the ban window).

    write_sentinel=False: even a CRITICAL report must NOT make the notifier write
    a bare sentinel here (that bare sentinel was the raw-MarkdownV2 email leak of
    24-Jun). The clean HTML email backup is written separately by deliver_report.
    """
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=_log, config_dir=config_dir)
        if notifier is None:
            _log.info("cron_officer.telegram_unconfigured")
            return
        try:  # parse_mode best-effort; plain still delivers if unsupported
            notifier.send(severity=severity, title="Cron Officer", body=text,
                          source_module="cron_officer", parse_mode="MarkdownV2",
                          write_sentinel=False)
        except TypeError:
            notifier.send(severity=severity, title="Cron Officer", body=text,
                          source_module="cron_officer", write_sentinel=False)
    except Exception as exc:
        _log.error("cron_officer.telegram_failed", extra={"error": str(exc)})


def deliver_report(report: CronReport, config_dir: Path, *, dry_run: bool,
                   sentinel_dir: Optional[Path] = None) -> dict:
    """Render + route a CronReport. Routing:
      * EOD report                   -> clean HTML email (always) + Telegram (post-ban)
      * briefing, during the ban     -> clean HTML email (no Telegram)
      * briefing, post-ban CRITICAL  -> Telegram + clean HTML email backup (Rama 24-Jun)
      * briefing, post-ban INFO/WARN -> Telegram only (no email)
    The Telegram path never writes a sentinel itself (_send_telegram_md ->
    write_sentinel=False), so the only email is the well-formed HTML one written
    here — no raw-MarkdownV2 blob can leak into the inbox.
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
    # One clean HTML email when: EOD (always), during the ban (briefing falls
    # back to email), OR a post-ban CRITICAL report — a belt-and-suspenders
    # backup so a critical briefing is never missed even if Telegram is down
    # (Rama 24-Jun). Post-ban INFO/WARN briefings stay Telegram-only.
    email_backup = report.is_eod or ban or report.severity == "CRITICAL"
    if email_backup:
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
    if not ban:                     # Telegram outside the ban window (no sentinel)
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
