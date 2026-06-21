"""
scripts/preflight/deliver.py -- route a PreflightReport to email + Telegram.

Reuses the existing, approved channels (derive-don't-duplicate):
  * email  -> alerts.critical.write_critical_sentinel(content_type=text/html) ->
              alert_watcher emails multipart/alternative with the verbatim subject.
  * Telegram -> scripts.cron_officer._send_telegram_md (the tested MarkdownV2 sender).

Routing (ALERT-ONLY; never blocks):
  * send when severity==CRITICAL (loud, immediate, any phase) OR phase=="C" (the
    09:20 final consolidated report). Phase A/B clean runs are sentinel-only (their
    status surfaces in the Cron Officer briefing banner) -> no inbox spam.
  * Telegram only OUTSIDE the ban window (telegram_ban_until in cron_registry's
    officer block, the single source of truth); during the ban it is email-only.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from scripts.preflight.report import (
    PreflightReport, render_html, render_plaintext, render_telegram, subject,
)

try:
    from core.logger import get_logger
    _log = get_logger("preflight.deliver")
except Exception:  # pragma: no cover
    import logging
    _log = logging.getLogger("preflight.deliver")


def ban_active_for(config_dir: Path, as_of: date) -> bool:
    """Reuse the Cron Officer's telegram-ban window (single source of truth)."""
    try:
        from core.cron_registry import load_cron_registry
        return bool(load_cron_registry(config_dir / "cron_registry.yaml").officer.ban_active(as_of))
    except Exception as exc:  # pragma: no cover
        _log.warning("preflight.deliver.ban_lookup_failed", extra={"error": str(exc)})
        return False


def _send_telegram(tg_text: str, severity: str, config_dir: Path) -> None:
    from scripts.cron_officer import _send_telegram_md  # tested MarkdownV2 sender
    _send_telegram_md(tg_text, severity, config_dir)


def deliver(
    report: PreflightReport,
    config_dir: Path,
    *,
    dry_run: bool,
    sentinel_dir: str | Path = "data_store",
    ban_active: Optional[bool] = None,
) -> dict:
    """Render + route. Returns the rendered parts (for dry-run / tests). Never raises."""
    if ban_active is None:
        ban_active = ban_active_for(config_dir, report.run_date)

    html = render_html(report)
    plain = render_plaintext(report)
    tg = render_telegram(report, ban_active)
    subj = subject(report, ban_active)
    should_send = (report.severity == "CRITICAL") or (report.phase == "C")
    parts = {"subject": subj, "html": html, "plain": plain, "telegram": tg,
             "ban_active": ban_active, "sent": False}

    if dry_run:
        print(f"[DRY-RUN] deliver: send={should_send} ban={ban_active} subject={subj}")
        return parts
    if not should_send:
        return parts

    # email (rich HTML) -- always when sending
    try:
        from alerts.critical import write_critical_sentinel
        write_critical_sentinel(
            title=f"Pre-flight Phase {report.phase} {report.run_date.isoformat()}",
            body=plain,
            source_module="preflight",
            sentinel_dir=sentinel_dir,
            context={"severity": report.severity, "phase": report.phase},
            subject=subj,
            content_type="text/html",
            plain_fallback=plain,
            html_body=html,
        )
        parts["sent"] = True
    except Exception as exc:
        _log.error("preflight.deliver.email_failed", extra={"error": str(exc)})

    # Telegram only outside the ban window
    if not ban_active:
        try:
            _send_telegram(tg, report.severity, config_dir)
        except Exception as exc:
            _log.error("preflight.deliver.telegram_failed", extra={"error": str(exc)})
    return parts
