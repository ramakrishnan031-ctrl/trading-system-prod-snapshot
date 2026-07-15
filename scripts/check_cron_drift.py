#!/usr/bin/env python3
"""
scripts/check_cron_drift.py — FIX-145 / TASK #3 / Phase 3 (self-maintaining cron)

Two independent passes, run daily at 18:00 IST:

  PASS 1 — heartbeat-miss (FIX-145): every monitored, due-today job must have a
    heartbeat in the last 24h. Missing -> WARNING. (cron_officer_eod is monitored,
    so this pass already watches the Officer: drift-check -> Officer tier-1.)

  PASS 2 — content drift (Phase 3): the live `crontab -l` vs generate(registry),
    NORMALIZED via the proven generator (parse<->compose). BIDIRECTIONAL:
      - enabled job whose canonical line is ABSENT from live -> CRITICAL
        (WARN if personal_tooling — a heartbeat is not a trading outage);
      - live line whose resolved job is NOT in the registry -> RAN_UNVERIFIED
        (auto-discovery; "needs registry entry + contract", informational);
      - a live line that no longer parses -> CRITICAL (cannot verify).
    A hand-edited / reordered wrapper (FIX-189) parses to different fields, so it
    composes differently and reads as drift — normalization cannot hide it.

This module RECORDS ITS OWN heartbeat at the end so the Cron Officer / systemd
watchdog can assert the drift-check itself ran (watch-the-watcher).

Usage:
    python scripts/check_cron_drift.py [--db-path P] [--config-dir P]
                                       [--crontab-file P] [--dry-run]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from core.cron_registry import CronRegistry
from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist
from scripts.generate_crontab import (
    _command_lines, compose, load_jobs, parse, resolve_job_name,
)
# F3: reuse the Cron Officer's marker reader (single source of truth for exit_code_file
# detection) so the drift-check mirrors the Officer's detection map instead of duplicating it.
from scripts.cron_officer import NO_SIGNAL, _read_marker

_log = get_logger("check_cron_drift")


# ── PASS 1: signal-miss (F3: detection-method-aware) ─────────────────────────
def check_cron_drift(store: StateStore, registry: CronRegistry, config_dir: Path,
                     marks_dir: Path = Path("data_store/cron_marks")) -> list[str]:
    """Names of monitored, due-today jobs missing their EXPECTED signal in 24h.

    F3 (15-Jul-2026): dispatch by the job's detection method, MIRRORING the Cron Officer
    (_classify_job) — a heartbeat_db job needs a heartbeat row; an exit_code_file job needs
    a fresh cron_marks/<name>.done marker. Pre-fix this pass checked heartbeats for EVERY
    monitored job, so the four MARKER-detected jobs (preflight_phase_a/b/c, sr_detector_
    backfill — which write markers, never heartbeats) always looked "missing" → a standing
    daily FALSE 'no heartbeat in 24h' warning even though they ran fine."""
    now = now_ist()
    cutoff_iso = (now - timedelta(hours=24)).isoformat()
    seen_hb = {h["job_name"] for h in store.get_cron_heartbeats_since(cutoff_iso)}
    expected = registry.expected_heartbeat_jobs(now.date(), config_dir=config_dir,
                                                before_time=now.time())
    missing: list[str] = []
    for j in expected:
        if j.effective_detection_method == "exit_code_file":
            st, _rt, _note = _read_marker(j.marker_name or j.name, now.date(), marks_dir)
            if st == NO_SIGNAL:              # no fresh marker today → genuinely missing
                missing.append(j.name)
        elif j.name not in seen_hb:          # heartbeat_db → needs a heartbeat row
            missing.append(j.name)
    return missing


# ── PASS 2: content drift (Phase 3) ──────────────────────────────────────────
class ContentDrift:
    """Bidirectional content-drift result (live `crontab -l` vs generate(registry))."""

    def __init__(self) -> None:
        self.absent_critical: list[str] = []     # enabled, not personal_tooling — CRITICAL
        self.absent_warn: list[str] = []         # enabled personal_tooling — WARN
        self.unregistered: list[str] = []        # live-not-registry — RAN_UNVERIFIED
        self.unparseable: list[str] = []         # live line no longer parses — CRITICAL

    @property
    def has_critical(self) -> bool:
        return bool(self.absent_critical or self.unparseable)

    @property
    def has_any(self) -> bool:
        return bool(self.absent_critical or self.absent_warn
                    or self.unregistered or self.unparseable)


def content_drift(registry_path: Path, crontab_text: str) -> ContentDrift:
    """Compare the live crontab against generate(registry), normalized via the
    proven parse<->compose. Set-based, so line ORDER is ignored."""
    jobs = load_jobs(registry_path)
    enabled = [j for j in jobs if j.get("enabled", True)]
    reg_names = {j["name"] for j in jobs}
    gen_lines = {compose(j): j for j in enabled}

    # Normalize live lines through the generator; an unparseable line is itself drift.
    live_norm: set[str] = set()
    out = ContentDrift()
    for raw in _command_lines(crontab_text):
        try:
            fields = parse(raw)
            live_norm.add(compose(fields))
            if resolve_job_name(fields) not in reg_names:
                out.unregistered.append(raw)
        except Exception:
            out.unparseable.append(raw)

    for line, job in gen_lines.items():
        if line not in live_norm:
            if job.get("personal_tooling"):
                out.absent_warn.append(job["name"])
            else:
                out.absent_critical.append(job["name"])
    return out


def _read_crontab(crontab_file: Path | None) -> str | None:
    if crontab_file is not None:
        return crontab_file.read_text(encoding="utf-8")
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        return r.stdout
    except Exception as exc:  # noqa: BLE001
        _log.warning("check_cron_drift.crontab_unreadable", extra={"error": str(exc)})
        return None


def _build_alert(missing: list[str], cd: ContentDrift | None, registry: CronRegistry) -> tuple[str, str]:
    """Return (severity, message). severity = CRITICAL|WARNING|OK."""
    lines: list[str] = []
    severity = "OK"
    if cd and cd.has_critical:
        severity = "CRITICAL"
    elif missing or (cd and cd.has_any):
        severity = "WARNING"

    if cd and cd.absent_critical:
        lines.append("🔴 ENABLED in registry, ABSENT from live crontab (CRITICAL):")
        lines += [f"   - {n} ({registry.get(n).schedule})" for n in cd.absent_critical]
    if cd and cd.unparseable:
        lines.append("🔴 Live crontab line no longer parses (CRITICAL):")
        lines += [f"   - {ln}" for ln in cd.unparseable]
    if cd and cd.absent_warn:
        lines.append("⚠️ Personal-tooling job absent from live (WARN):")
        lines += [f"   - {n}" for n in cd.absent_warn]
    if cd and cd.unregistered:
        lines.append("🆕 Live job NOT in registry — RAN_UNVERIFIED (needs registry entry + contract):")
        lines += [f"   - {ln}" for ln in cd.unregistered]
    if missing:
        lines.append("⏰ Monitored, due-today jobs with NO heartbeat in 24h:")
        lines += [f"   - {n} (expected: {registry.get(n).schedule})" for n in missing]

    if not lines:
        return "OK", "✅ [LFL836] Cron integrity OK: live == registry; all due heartbeats present."
    return severity, f"[LFL836] CRON INTEGRITY {severity}\n" + "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Check for cron heartbeat + content drift")
    p.add_argument("--db-path", type=Path, default=Path("data_store/trading_system.db"))
    p.add_argument("--config-dir", type=Path, default=Path("config"))
    p.add_argument("--crontab-file", type=Path, default=None,
                   help="read crontab from FILE instead of `crontab -l` (testing)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not args.db_path.exists():
        print(f"Database not found: {args.db_path}")
        return 1
    try:
        registry = CronRegistry.load(args.config_dir / "cron_registry.yaml")
    except Exception as e:  # noqa: BLE001
        print(f"Failed to load cron registry: {e}")
        return 1

    store = StateStore(args.db_path)
    try:
        missing = check_cron_drift(store, registry, args.config_dir)
        crontab_text = _read_crontab(args.crontab_file)
        cd = content_drift(args.config_dir / "cron_registry.yaml", crontab_text) if crontab_text else None
        severity, msg = _build_alert(missing, cd, registry)
        print(msg)
        _log.info("check_cron_drift.result", extra={"severity": severity,
                  "missing": missing,
                  "absent_critical": cd.absent_critical if cd else [],
                  "unregistered": len(cd.unregistered) if cd else 0})

        if severity != "OK" and not args.dry_run:
            try:
                from alerts.telegram_notifier import TelegramNotifier
                notifier = TelegramNotifier.from_config(args.config_dir)
                if notifier is not None:
                    notifier.send_alert(msg, level=("CRITICAL" if severity == "CRITICAL" else "WARNING"))
            except Exception as e:  # noqa: BLE001
                _log.error("check_cron_drift.alert_failed", extra={"error": str(e)})
    finally:
        store.close()

    # Watch-the-watcher: record OUR run so the Officer / systemd watchdog can
    # assert the drift-check itself ran today.
    if not args.dry_run:
        try:
            from utils.cron_heartbeat import record_heartbeat
            record_heartbeat("check_cron_drift", db_path=args.db_path)
        except Exception as e:  # noqa: BLE001
            _log.error("check_cron_drift.self_heartbeat_failed", extra={"error": str(e)})

    return 0 if severity == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
