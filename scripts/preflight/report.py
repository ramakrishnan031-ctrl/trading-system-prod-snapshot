"""
scripts/preflight/report.py -- pre-flight result model + renderers.

This first cut provides:
  * the PreflightReport data model (one CheckRecord per check + derived rollups),
  * severity / overall-status rollup logic,
  * a sentinel mapping,
  * a TERMINAL renderer (grouped, status pills, summary box).

The HTML email + Telegram MarkdownV2 renderers reuse the Cron Officer cosmetic
language and land with the dry-run step (they are snapshot-tested there).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List

from scripts.preflight.base import Criticality, Status
from scripts.preflight import sentinel as _sentinel

# terminal status pills
_PILL = {
    Status.PASS: "✅ PASS",
    Status.FAIL: "🔴 FAIL",
    Status.WARN: "⚠️  WARN",
    Status.AUTOFIXED: "🔧 FIXED",
    Status.SKIPPED: "⏭  SKIP",
}


@dataclass
class CheckRecord:
    name: str
    group: str
    criticality: Criticality
    status: Status
    detail: str = ""
    duration_ms: int = 0
    fix_attempted: bool = False
    fix_result: str = ""        # "SUCCESS" | "FAILED" | "" (not attempted)

    @property
    def is_blocking(self) -> bool:
        return self.status is Status.FAIL and self.criticality is Criticality.CRITICAL

    @property
    def is_warning(self) -> bool:
        return self.status is Status.WARN or (
            self.status is Status.FAIL and self.criticality is not Criticality.CRITICAL
        )


@dataclass
class PreflightReport:
    phase: str
    run_date: date
    run_id: str = ""
    mode: str = "live"
    account: str = "LFL836"
    started_at: str = ""
    completed_at: str = ""
    records: List[CheckRecord] = field(default_factory=list)

    # ── derived rollups ─────────────────────────────────────────────────────
    @property
    def total(self) -> int:
        return len(self.records)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.records if r.status is Status.PASS)

    @property
    def failed_critical(self) -> int:
        return sum(1 for r in self.records if r.is_blocking)

    @property
    def warnings(self) -> int:
        return sum(1 for r in self.records if r.is_warning)

    @property
    def autofixed(self) -> int:
        return sum(1 for r in self.records if r.status is Status.AUTOFIXED)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.records if r.status is Status.SKIPPED)

    @property
    def overall_status(self) -> str:
        if self.failed_critical:
            return _sentinel.CRITICAL_FAILURE
        if self.warnings:
            return _sentinel.READY_WITH_WARNINGS
        return _sentinel.READY

    @property
    def severity(self) -> str:
        if self.failed_critical:
            return "CRITICAL"
        if self.warnings:
            return "WARN"
        return "INFO"

    @property
    def blocking_failures(self) -> List[str]:
        return [f"{r.group}/{r.name}: {r.detail}" for r in self.records if r.is_blocking]

    @property
    def warnings_summary(self) -> List[str]:
        return [f"{r.group}/{r.name}: {r.detail}" for r in self.records if r.is_warning]

    @property
    def phase_status(self) -> str:
        """Sentinel phase status for THIS phase."""
        if self.failed_critical:
            return _sentinel.CRITICAL
        if self.warnings:
            return _sentinel.WARN
        return _sentinel.PASSED


def render_terminal(report: PreflightReport) -> str:
    """Grouped, human-readable terminal output (also the per-day .log content)."""
    lines: List[str] = []
    sev_emoji = {"INFO": "✅", "WARN": "⚠️", "CRITICAL": "🔴"}[report.severity]
    lines.append("=" * 60)
    lines.append(f"🚀 PRE-FLIGHT — Phase {report.phase} — "
                 f"{report.run_date.strftime('%d-%b-%Y')} — {report.mode.upper()}/{report.account}")
    lines.append("=" * 60)

    # group-by-group
    seen_groups: List[str] = []
    for r in report.records:
        if r.group not in seen_groups:
            seen_groups.append(r.group)
    for group in seen_groups:
        grp = [r for r in report.records if r.group == group]
        done = sum(1 for r in grp if r.status is not Status.FAIL)
        lines.append("")
        lines.append(f"Group — {group}  ({done}/{len(grp)} ok)")
        for r in grp:
            pill = _PILL.get(r.status, str(r.status.value))
            fix = ""
            if r.fix_attempted:
                fix = f"  [fix:{r.fix_result}]"
            dur = f"{r.duration_ms}ms"
            lines.append(f"  ├─ {r.name:<22} {pill:<9} {dur:>7}  {r.detail}{fix}")

    lines.append("")
    lines.append("-" * 60)
    lines.append(f"{sev_emoji} {report.overall_status}")
    lines.append(f"  ✅ {report.passed}   🔴 {report.failed_critical}   "
                 f"⚠️ {report.warnings}   🔧 {report.autofixed}   ⏭ {report.skipped}   "
                 f"(total {report.total})")
    lines.append("-" * 60)
    return "\n".join(lines)


# ── rich renderers (reuse the approved Cron Officer cosmetic language) ──────────
# escape_md_v2 + progress_bar are the genuinely-reusable PURE helpers; the SEV
# palette + Gmail-safe primitives use the SAME hex values as cron_report_render so
# the visual language is identical (the data models differ, so the scaffolding is
# pre-flight-specific rather than refactoring the approved cron renderer mid-build).
from scripts.cron_report_render import escape_md_v2, progress_bar  # noqa: E402

_SEV = {
    "INFO":     {"emoji": "✅", "accent": "#2E7D32", "bg": "#E8F5E9"},
    "WARN":     {"emoji": "⚠️", "accent": "#F57C00", "bg": "#FFF8E1"},
    "CRITICAL": {"emoji": "🔴", "accent": "#C62828", "bg": "#FFEBEE"},
}
_STATUS_PILL = {
    Status.PASS:      {"emoji": "✅", "text": "Pass",  "bg": "#2E7D32", "fg": "#FFFFFF"},
    Status.FAIL:      {"emoji": "🔴", "text": "Fail",  "bg": "#C62828", "fg": "#FFFFFF"},
    Status.WARN:      {"emoji": "⚠️", "text": "Warn",  "bg": "#F57C00", "fg": "#FFFFFF"},
    Status.AUTOFIXED: {"emoji": "🔧", "text": "Fixed", "bg": "#1565C0", "fg": "#FFFFFF"},
    Status.SKIPPED:   {"emoji": "⏭", "text": "Skip",  "bg": "#616161", "fg": "#FFFFFF"},
}
_DIV = "━" * 26


def _esc(x) -> str:
    return (str(x).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _e(x) -> str:
    return escape_md_v2(x)


def subject(report: PreflightReport, ban_active: bool = False) -> str:
    """Severity-aware subject. The ban-window tag is applied to EVERY pre-flight
    subject INCLUDING CRITICAL (Fix 4, locked 21-Jun) so a [LFL836-BAN] email filter
    can never miss a CRITICAL; after the ban auto-clears, no subject carries it."""
    d = report.run_date.strftime("%d-%b")
    tag = "[LFL836-BAN]" if ban_active else "[LFL836]"
    if report.severity == "CRITICAL":
        return f"{tag} 🔴 CRITICAL — Pre-flight FAILED — {d}"
    if report.overall_status == _sentinel.READY_WITH_WARNINGS:
        return f"{tag} ⚠️ Pre-flight READY ({report.warnings} warnings) — {d}"
    return f"{tag} ✅ Pre-flight READY — {d}"


def _ok_count(report: PreflightReport) -> int:
    return report.total - report.failed_critical - report.warnings


def render_plaintext(report: PreflightReport) -> str:
    bar, pct = progress_bar(_ok_count(report), max(1, report.total))
    L = [
        f"Pre-flight — Phase {report.phase} — {report.run_date.strftime('%d-%b-%Y')} "
        f"[{report.overall_status}]",
        f"Mode: {report.mode} | Account: {report.account} | Severity: {report.severity}",
        "=" * 56,
        f"  Total checks : {report.total}",
        f"  Passed       : {report.passed}",
        f"  Critical     : {report.failed_critical}",
        f"  Warnings     : {report.warnings}",
        f"  Auto-fixed   : {report.autofixed}",
        f"  Skipped      : {report.skipped}",
        f"  Readiness    : [{bar}] {pct}%",
        "",
        f"CRITICAL FAILURES ({report.failed_critical})",
    ]
    crit = [r for r in report.records if r.is_blocking]
    L += [f"  - {r.group}/{r.name}: {r.detail}"
          + (f"  [fix:{r.fix_result}]" if r.fix_attempted else "") for r in crit] or ["  (none)"]
    L.append("")
    L.append(f"WARNINGS ({report.warnings})")
    warns = [r for r in report.records if r.is_warning]
    L += [f"  - {r.group}/{r.name}: {r.detail}" for r in warns] or ["  (none)"]
    L.append("")
    fixed = [r for r in report.records if r.status is Status.AUTOFIXED]
    if fixed:
        L.append(f"AUTO-FIXED ({len(fixed)})")
        L += [f"  - {r.group}/{r.name}: {r.detail}" for r in fixed]
        L.append("")
    L.append("PER-CHECK")
    for r in report.records:
        st = _STATUS_PILL.get(r.status)["text"]
        L.append(f"  {r.group:<16}{r.name:<24}{st:<7}{r.duration_ms:>5}ms  {r.detail}")
    L += ["", "-" * 56,
          f"Severity: {report.severity} | Module: preflight | Mode: {report.mode}",
          f"Time: {report.completed_at}", f"Alert ID: {report.run_id}"]
    return "\n".join(L)


def render_telegram(report: PreflightReport, ban_active: bool = False) -> str:
    bar, pct = progress_bar(_ok_count(report), max(1, report.total))
    sev = _SEV.get(report.severity, _SEV["INFO"])
    lines = [
        f"*🚀 Pre\\-flight \\(Phase {_e(report.phase)}\\)*",
        f"`{_e(report.run_date.strftime('%d-%b-%Y'))}`  \\|  Mode: *{_e(report.mode)}*",
        _DIV,
        f"Status: *{_e(report.overall_status)}*  {sev['emoji']}",
        f"`Passed   :` *{report.passed}/{report.total}*  ✅",
        f"`Critical :` *{report.failed_critical}*  🔴",
        f"`Warnings :` *{report.warnings}*  ⚠️",
        f"`Fixed    :` *{report.autofixed}*  🔧",
        f"`Skipped  :` *{report.skipped}*  ⏭",
        "",
        f"Readiness: {bar}  `{pct}%`",
        _DIV,
    ]
    crit = [r for r in report.records if r.is_blocking]
    if crit:
        lines.append("🔴 *Critical*")
        for r in crit:
            lines.append(f"└ {_e(r.name)}: _{_e(r.detail)}_")
    warns = [r for r in report.records if r.is_warning]
    if warns:
        lines.append("⚠️ *Warnings*")
        for r in warns[:6]:
            lines.append(f"└ {_e(r.name)}")
    if crit or warns:                     # avoid a doubled divider on a clean run
        lines.append(_DIV)
    lines.append("📧 Full details → email")
    return "\n".join(lines)


def _pill(status: Status) -> str:
    s = _STATUS_PILL.get(status, _STATUS_PILL[Status.SKIPPED])
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
            f'background:{s["bg"]};color:{s["fg"]};font-size:12px;white-space:nowrap;">'
            f'{s["emoji"]} {_esc(s["text"])}</span>')


def _stat_card(value, label: str, accent: str) -> str:
    return (f'<td style="padding:6px;"><table cellpadding="0" cellspacing="0" width="100%" '
            f'style="border:1px solid #E0E0E0;border-radius:6px;background:#FFFFFF;"><tr>'
            f'<td align="center" style="padding:10px;">'
            f'<div style="font-size:22px;font-weight:bold;color:{accent};">{_esc(value)}</div>'
            f'<div style="font-size:11px;color:#616161;text-transform:uppercase;">{_esc(label)}</div>'
            f'</td></tr></table></td>')


def _section(title: str, inner: str, accent: str = "#E0E0E0") -> str:
    return (f'<table cellpadding="0" cellspacing="0" width="100%" '
            f'style="margin:12px 0;border:1px solid #E0E0E0;border-left:4px solid {accent};'
            f'border-radius:4px;"><tr><td style="padding:10px 12px;">'
            f'<div style="font-size:13px;font-weight:bold;color:#424242;margin-bottom:6px;">{title}</div>'
            f'{inner}</td></tr></table>')


def _html_progress(ok: int, total: int) -> str:
    _, pct = progress_bar(ok, max(1, total))
    return (f'<table cellpadding="0" cellspacing="0" width="100%" style="margin:8px 0;"><tr>'
            f'<td style="background:#E0E0E0;border-radius:8px;padding:0;">'
            f'<table cellpadding="0" cellspacing="0" width="{max(0, min(100, pct))}%"><tr>'
            f'<td style="background:#2E7D32;height:14px;border-radius:8px;">&nbsp;</td></tr></table></td>'
            f'<td width="48" align="right" style="font-size:12px;color:#424242;padding-left:8px;">'
            f'{pct}%</td></tr></table>')


def render_html(report: PreflightReport) -> str:
    sev = _SEV.get(report.severity, _SEV["INFO"])
    banner = (
        f'<table cellpadding="0" cellspacing="0" width="100%" style="background:{sev["bg"]};border-radius:6px;">'
        f'<tr><td style="padding:14px 16px;border-left:6px solid {sev["accent"]};">'
        f'<div style="font-size:18px;font-weight:bold;color:{sev["accent"]};">'
        f'🚀 Pre-flight — {_esc(report.overall_status)}</div>'
        f'<div style="font-size:13px;color:#424242;margin-top:2px;">'
        f'Phase {_esc(report.phase)} &nbsp;|&nbsp; {_esc(report.run_date.strftime("%d-%b-%Y"))} '
        f'&nbsp;|&nbsp; {_esc(report.mode)}/{_esc(report.account)}</div></td></tr></table>'
    )
    cards = (
        '<table cellpadding="0" cellspacing="0" width="100%"><tr>'
        + _stat_card(report.total, "Total", "#1565C0")
        + _stat_card(report.passed, "✅ Pass", "#2E7D32")
        + _stat_card(report.failed_critical, "🔴 Critical", "#C62828")
        + _stat_card(report.warnings, "⚠️ Warn", "#F57C00")
        + _stat_card(report.autofixed, "🔧 Fixed", "#1565C0")
        + _stat_card(report.skipped, "⏭ Skipped", "#616161")
        + '</tr></table>' + _html_progress(_ok_count(report), report.total)
    )
    summary = _section("READINESS", cards, sev["accent"])

    def _rows(predicate, empty):
        rows = [r for r in report.records if predicate(r)]
        if not rows:
            return f'<div style="color:#9E9E9E;font-size:13px;">{empty}</div>'
        out = []
        for r in rows:
            fix = (f' <span style="color:#1565C0;">[fix: {_esc(r.fix_result)}]</span>'
                   if r.fix_attempted else "")
            out.append(f'<div style="font-size:13px;margin:2px 0;">• <code>{_esc(r.group)}/{_esc(r.name)}</code>'
                       f' <span style="color:#757575;">— {_esc(r.detail)}</span>{fix}</div>')
        return "".join(out)

    crit_sec = _section(f"🔴 CRITICAL FAILURES ({report.failed_critical})",
                        _rows(lambda r: r.is_blocking, "(none — clean)"), "#C62828")
    warn_sec = _section(f"⚠️ WARNINGS ({report.warnings})",
                        _rows(lambda r: r.is_warning, "(none)"), "#F57C00")
    fixed_sec = _section(f"🔧 AUTO-FIXED ({report.autofixed})",
                         _rows(lambda r: r.status is Status.AUTOFIXED, "(none)"), "#2E7D32")

    rows_html = []
    for i, r in enumerate(report.records):
        stripe = "#FAFAFA" if i % 2 else "#FFFFFF"
        rows_html.append(
            f'<tr style="background:{stripe};">'
            f'<td style="padding:4px 8px;font-size:12px;border-bottom:1px solid #EEE;">{_esc(r.group)}</td>'
            f'<td style="padding:4px 8px;font-size:12px;border-bottom:1px solid #EEE;">{_esc(r.name)}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid #EEE;">{_pill(r.status)}</td>'
            f'<td style="padding:4px 8px;font-size:12px;border-bottom:1px solid #EEE;color:#616161;">{_esc(r.detail)}</td>'
            f'<td style="padding:4px 8px;font-family:monospace;font-size:12px;text-align:right;border-bottom:1px solid #EEE;">{r.duration_ms}ms</td>'
            f'</tr>')
    table = (
        '<table cellpadding="0" cellspacing="0" width="100%" style="border:1px solid #E0E0E0;border-collapse:collapse;">'
        '<tr style="background:#ECEFF1;">'
        '<th style="padding:6px 8px;text-align:left;font-size:11px;color:#455A64;">Group</th>'
        '<th style="padding:6px 8px;text-align:left;font-size:11px;color:#455A64;">Check</th>'
        '<th style="padding:6px 8px;text-align:left;font-size:11px;color:#455A64;">Status</th>'
        '<th style="padding:6px 8px;text-align:left;font-size:11px;color:#455A64;">Detail</th>'
        '<th style="padding:6px 8px;text-align:right;font-size:11px;color:#455A64;">Run</th>'
        '</tr>' + "".join(rows_html) + '</table>'
    )
    table_sec = _section(f"📋 PER-CHECK ({report.total})", table)

    footer = (
        f'<table cellpadding="0" cellspacing="0" width="100%" style="margin-top:12px;background:#FAFAFA;'
        f'border-top:1px solid #E0E0E0;"><tr><td style="padding:10px 12px;font-family:monospace;'
        f'font-size:11px;color:#616161;">Severity : {_esc(report.severity)}<br>Module&nbsp;&nbsp;: preflight'
        f'<br>Mode&nbsp;&nbsp;&nbsp;&nbsp;: {_esc(report.mode)}<br>Time&nbsp;&nbsp;&nbsp;&nbsp;: '
        f'{_esc(report.completed_at)}<br>Alert ID : {_esc(report.run_id)}</td></tr></table>'
    )
    body = banner + summary + crit_sec + warn_sec + fixed_sec + table_sec + footer
    return ('<html><body style="margin:0;padding:12px;background:#F5F5F5;'
            'font-family:Arial,Helvetica,sans-serif;color:#212121;">'
            '<table cellpadding="0" cellspacing="0" width="100%" style="max-width:680px;margin:0 auto;">'
            f'<tr><td>{body}</td></tr></table></body></html>')


def apply_to_sentinel(report: PreflightReport, s: _sentinel.Sentinel) -> _sentinel.Sentinel:
    """Update the cross-phase sentinel from THIS phase's report."""
    s.run_date = report.run_date.isoformat()
    s.mode = report.mode
    s.account = report.account
    s.alert_id = report.run_id
    s.last_updated = report.completed_at

    phase_status = report.phase_status
    completed = report.completed_at
    if report.phase == "A":
        s.phase_a_status, s.phase_a_completed_at = phase_status, completed
    elif report.phase == "B":
        s.phase_b_status, s.phase_b_completed_at = phase_status, completed
    elif report.phase == "C":
        s.phase_c_status, s.phase_c_completed_at = phase_status, completed

    # overall = worst across the phases run so far (this phase's view)
    s.overall_status = report.overall_status
    s.critical_count = report.failed_critical
    s.warning_count = report.warnings
    s.autofix_count = report.autofixed
    s.blocking_failures = report.blocking_failures
    s.warnings_summary = report.warnings_summary
    return s
