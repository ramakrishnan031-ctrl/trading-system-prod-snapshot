"""
alerts/cron_alerts.py -- Trading System v2  (TASK #3 / Cron Officer)

Per-job cron alert POLICY. Maps a job's heartbeat outcome to a TelegramNotifier
severity and sends it; the existing tiered delivery then does the rest:

    CRITICAL -> writes a sentinel FIRST (alert_watcher emails it) + Telegram +
                direct SMTP fallback (FIX-132). This IS the Telegram->Email fallback.
    ERROR    -> Telegram; failed_alerts.log on Telegram failure.
    INFO     -> Telegram only.

Policy (Cron Officer Layer 3B):
    FAILED  + critical job -> CRITICAL   (escalates to email)
    FAILED  + non-critical -> ERROR
    SUCCESS + critical job -> INFO       (confirmation)
    SUCCESS + non-critical -> silent     (noise reduction)
    SKIPPED / PARTIAL      -> silent

Honors the telegram.enabled master switch: TelegramNotifier.from_env() returns a
notifier whose send() is a silent no-op when the switch is OFF — so OFF means
fully silent (no Telegram, no sentinel, no email), by design (TASK-10).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.logger import get_logger

_log = get_logger("cron_alerts")


def _resolve_critical(job_name: str, config_dir: Path) -> Optional[bool]:
    """Look up a job's criticality from the registry.

    Returns True/False when the registry answers, and **None when it cannot** —
    unreadable YAML, missing file, unknown job. The distinction matters: this used to
    swallow every error and return False, i.e. "not critical". A registry problem
    therefore DOWNGRADED a critical job's FAILED alert from CRITICAL to ERROR, and only
    CRITICAL carries the email fallback — so the one alert that most needed to escalate
    lost its escalation, silently, precisely when the config was broken. "I don't know"
    is not "no"; see alert_job_result for how the unknown is resolved per status.
    """
    try:
        from core.cron_registry import CronRegistry

        reg = CronRegistry.load(config_dir / "cron_registry.yaml")
        return reg.get(job_name).critical
    except Exception as exc:  # noqa: BLE001 — alerting must never crash a job
        # Was silent. A registry we cannot read is itself worth saying out loud.
        _log.warning(
            "cron_alerts.criticality_unresolved",
            extra={"job": job_name, "error": str(exc),
                   "effect": "FAILED alerts escalate as CRITICAL (fail-safe)"},
        )
        return None


def alert_job_result(
    job_name: str,
    status: str,
    *,
    duration_sec: Optional[float] = None,
    started: Optional[str] = None,
    ended: Optional[str] = None,
    message: Optional[str] = None,
    critical: Optional[bool] = None,
    config_dir: Path = Path("config"),
) -> None:
    """Send a per-job alert per the Cron Officer policy. Never raises."""
    try:
        status = (status or "").upper()
        if critical is None:
            critical = _resolve_critical(job_name, config_dir)

        # criticality is now TRI-STATE: True / False / None (registry unreadable).
        # The unknown resolves DIFFERENTLY per status, on purpose:
        #   FAILED  + unknown -> CRITICAL. A failed job we cannot classify must keep the
        #                        email fallback; under-alerting on a real failure is the
        #                        expensive mistake, an extra CRITICAL is merely noise.
        #   SUCCESS + unknown -> silent. Escalating the unknown here would ping on EVERY
        #                        successful job while the registry is broken — spam that
        #                        trains the operator to ignore the channel, which is how
        #                        you lose the next real alert.
        if status == "FAILED":
            severity = "ERROR" if critical is False else "CRITICAL"
            emoji = "❌"  # ❌
        elif status == "SUCCESS" and critical is True:
            severity = "INFO"
            emoji = "✅"  # ✅
        else:
            # SUCCESS non-critical/unknown, SKIPPED, PARTIAL, unknown status -> silent
            return

        if duration_sec is not None and started and ended:
            window = f"{started} → {ended} ({duration_sec:.1f}s)"
        elif duration_sec is not None:
            window = f"{duration_sec:.1f}s"
        else:
            window = "n/a"

        title = f"{emoji} [LFL836] {job_name} — {status}"
        body_lines = [f"cron job: {job_name}", f"status: {status}", f"timing: {window}"]
        if message:
            body_lines.append(f"detail: {message}")
        body = "\n".join(body_lines)

        from alerts.telegram_notifier import TelegramNotifier

        notifier = TelegramNotifier.from_env(logger=_log, config_dir=config_dir)
        if notifier is None:
            _log.info("cron_alerts.telegram_unconfigured", extra={"job": job_name, "status": status})
            return
        notifier.send(severity=severity, title=title, body=body, source_module=f"cron:{job_name}")
    except Exception as exc:  # pragma: no cover - alerting must never crash a job
        _log.warning("cron_alerts.failed", extra={"job": job_name, "error": str(exc)})
