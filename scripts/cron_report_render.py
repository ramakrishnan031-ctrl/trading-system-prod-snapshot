"""
scripts/cron_report_render.py -- Trading System v2  (Cron Officer Phase 4/5)

PURE rendering for the Cron Officer's morning briefing + EOD report. No I/O, no
DB, no network -- takes a computed CronReport and returns strings:

  * render_eod_html(report)        -> rich multipart HTML body (Phase 4)
  * render_eod_plaintext(report)   -> plain-text mirror (HTML fallback)
  * render_eod_telegram(report)    -> Telegram MarkdownV2 (Phase 5.3)
  * render_briefing_*              -> the morning equivalents (Phase 5.2)
  * eod_subject / briefing_subject -> subject line incl. severity + ban prefix
  * escape_md_v2 / progress_bar    -> tested helpers

Email HTML follows Gmail-safe rules: inline CSS only (no <style> head), table
layout (no flex/grid), no background-image. The data model (JobOutcome /
CronReport) lives here so cron_officer.py and the tests share one definition.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import List, Optional, Tuple

# ── status constants ──────────────────────────────────────────────────────────
COMPLETED = "COMPLETED"
FAILED = "FAILED"
MISSED = "MISSED"
SKIPPED = "SKIPPED"
PENDING = "PENDING"                    # due later today (briefing only)
PENDING_REDESIGN = "PENDING_REDESIGN"  # daily_report — Bug C deferral
NO_SIGNAL = "NO_SIGNAL"                # exit_code_file marker absent (info only)
NOT_TRACKED = "NOT_TRACKED"            # detection_method none (visibility only)
RAN_UNVERIFIED = "RAN_UNVERIFIED"      # auto-discovered (live-not-registry) / no contract yet

# Statuses that drive the headline CRITICAL severity. RAN_UNVERIFIED is
# deliberately NOT here — "needs attention", not "broken" (no false-escalation).
_BAD = (FAILED, MISSED)


@dataclass
class JobOutcome:
    name: str
    category: str
    due_label: str          # "HH:MM" / "*/5" / "hourly" / "—"
    due_sort: time          # for ordering
    status: str
    detection: str          # heartbeat_db / exit_code_file / log_marker / none
    runtime_sec: float = 0.0
    note: str = ""          # failure reason / "pending redesign" / etc.


@dataclass
class CronReport:
    day: date
    weekday: str
    mode: str                       # Paper | Live | Mixed
    is_eod: bool                    # True=EOD report, False=morning briefing
    jobs: List[JobOutcome] = field(default_factory=list)
    severity: str = "INFO"          # INFO | WARN | CRITICAL
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    excluded: List[Tuple[str, str]] = field(default_factory=list)  # (name, reason)
    extra_lines: List[str] = field(default_factory=list)           # e.g. security watcher
    alert_id: str = ""
    ts_iso: str = ""
    ban_active: bool = False
    preflight: Optional[dict] = None   # pre-flight sentinel summary -> briefing banner
    # Slice 2: pre-rendered strategy-status fragments {"html","telegram","plain"}
    # (morning briefing only). Built in cron_officer.build_report; spliced by the
    # briefing renderers below so this module stays pure (no config I/O here).
    strategy_status: Optional[dict] = None

    # ── derived counts ────────────────────────────────────────────────────────
    def _count(self, *statuses: str) -> int:
        return sum(1 for j in self.jobs if j.status in statuses)

    @property
    def total(self) -> int:
        return len(self.jobs)

    @property
    def completed(self) -> int:
        return self._count(COMPLETED)

    @property
    def failed(self) -> int:
        return self._count(FAILED)

    @property
    def missed(self) -> int:
        return self._count(MISSED)

    @property
    def skipped(self) -> int:
        return self._count(SKIPPED)

    @property
    def pending(self) -> int:
        return self._count(PENDING, PENDING_REDESIGN)

    @property
    def ran_unverified(self) -> int:
        return self._count(RAN_UNVERIFIED)

    @property
    def runtime_sec(self) -> float:
        return sum(j.runtime_sec or 0.0 for j in self.jobs)

    def status_counts(self) -> dict:
        """Count per status. By construction sums to `total` — no job falls into
        an uncounted bucket (status-count integrity for the new RAN_UNVERIFIED)."""
        from collections import Counter
        return dict(Counter(j.status for j in self.jobs))


# ── tested helpers ────────────────────────────────────────────────────────────

_MDV2_SPECIAL = set(r"_*[]()~`>#+-=|{}.!")


def escape_md_v2(text) -> str:
    """Escape every Telegram MarkdownV2 reserved char with a backslash."""
    return "".join("\\" + c if c in _MDV2_SPECIAL else c for c in str(text))


def progress_bar(completed: int, total: int, cells: int = 10) -> Tuple[str, int]:
    """(bar, pct) — filled ▰ cells proportional to completed/total."""
    if not total:
        return "▱" * cells, 0
    frac = completed / total
    pct = int(round(frac * 100))
    filled = max(0, min(cells, int(round(frac * cells))))
    return "▰" * filled + "▱" * (cells - filled), pct


# ── palettes ──────────────────────────────────────────────────────────────────

_SEV = {
    "INFO":     {"emoji": "✅", "accent": "#2E7D32", "bg": "#E8F5E9"},
    "WARN":     {"emoji": "⚠️", "accent": "#F57C00", "bg": "#FFF8E1"},
    "CRITICAL": {"emoji": "🔴", "accent": "#C62828", "bg": "#FFEBEE"},
}

_STATUS = {
    COMPLETED:        {"emoji": "✅", "text": "Completed", "bg": "#2E7D32", "fg": "#FFFFFF"},
    FAILED:           {"emoji": "❌", "text": "Failed",    "bg": "#C62828", "fg": "#FFFFFF"},
    MISSED:           {"emoji": "⚠️", "text": "Missed",    "bg": "#F57C00", "fg": "#FFFFFF"},
    SKIPPED:          {"emoji": "⏭", "text": "Skipped",   "bg": "#616161", "fg": "#FFFFFF"},
    PENDING:          {"emoji": "⏳", "text": "Pending",   "bg": "#1565C0", "fg": "#FFFFFF"},
    PENDING_REDESIGN: {"emoji": "⏸", "text": "Pending",   "bg": "#FBC02D", "fg": "#000000"},
    NO_SIGNAL:        {"emoji": "▫️", "text": "No signal", "bg": "#9E9E9E", "fg": "#FFFFFF"},
    NOT_TRACKED:      {"emoji": "▫️", "text": "Untracked", "bg": "#BDBDBD", "fg": "#000000"},
    RAN_UNVERIFIED:   {"emoji": "🆕", "text": "Unverified", "bg": "#1565C0", "fg": "#FFFFFF"},
}


def _sev(report: CronReport) -> dict:
    return _SEV.get(report.severity, _SEV["INFO"])


def _runtime_label(sec: float) -> str:
    if not sec:
        return "—"
    if sec < 60:
        return f"{int(round(sec))}s"
    m, s = divmod(int(round(sec)), 60)
    return f"{m}m {s}s"


# ── subjects ──────────────────────────────────────────────────────────────────

def _tag(report: CronReport) -> str:
    # Ban-window prefix overrides the normal account tag (Phase 4.6/5.4).
    return "[LFL836-BAN]" if report.ban_active else "[LFL836]"


def eod_subject(report: CronReport) -> str:
    d = report.day.strftime("%d-%b")
    if report.severity == "CRITICAL":
        return f"{_tag(report)} 🔴 CRITICAL — Cron Daily — {d}"
    emoji = _SEV.get(report.severity, _SEV["INFO"])["emoji"]
    return f"{_tag(report)} {emoji} Cron Daily Report — {d}"


def briefing_subject(report: CronReport) -> str:
    d = report.day.strftime("%d-%b")
    return f"{_tag(report)} 📋 Cron Morning Briefing — {d}"


# ── Telegram (MarkdownV2) ─────────────────────────────────────────────────────

_DIV = "━" * 26


def _e(x) -> str:
    return escape_md_v2(x)


def render_eod_telegram(report: CronReport) -> str:
    bar, pct = progress_bar(report.completed, max(1, report.total - report.pending))
    sev_emoji = _sev(report)["emoji"]
    lines = [
        "*📊 Cron Officer — EOD Summary*",
        f"`{_e(report.day.strftime('%d-%b-%Y'))}` *\\({_e(report.weekday)}\\)*  \\|  Mode: *{_e(report.mode)}*",
        _DIV,
        "🎯 *Result*",
        f"`Completed :` *{report.completed}/{report.total}*  ✅",
        f"`Failed    :` *{report.failed}*  ❌",
        f"`Missed    :` *{report.missed}*  ⚠️",
        f"`Skipped   :` *{report.skipped}*  ⏭",
        "",
        f"Completion: {bar}  `{pct}%`",
        _DIV,
    ]
    failed = [j for j in report.jobs if j.status == FAILED]
    if failed:
        lines.append("❌ *Failed*")
        for j in failed:
            lines.append(f"└ `{_e(j.due_label)}` {_e(j.name)}")
            if j.note:
                lines.append(f"   _{_e(j.note)}_")
    missed = [j for j in report.jobs if j.status == MISSED]
    if missed:
        lines.append("⚠️ *Missed*")
        for j in missed:
            lines.append(f"└ `{_e(j.due_label)}` {_e(j.name)}")
    redesign = [j for j in report.jobs if j.status == PENDING_REDESIGN]
    if redesign:
        lines.append("⏸ *Pending Redesign*")
        for j in redesign:
            lines.append(f"└ `{_e(j.due_label)}` {_e(j.name)}")
            if j.note:
                lines.append(f"   _{_e(j.note)}_")
    lines += [
        _DIV,
        f"🔔 *Severity:* `{_e(report.severity)}`  {sev_emoji}",
        "📧 Full details → email",
    ]
    return "\n".join(lines)


def render_briefing_telegram(report: CronReport) -> str:
    done = [j for j in report.jobs if j.status == COMPLETED]
    pending = [j for j in report.jobs if j.status in (PENDING, PENDING_REDESIGN)]
    skipped = report.skipped
    bar, pct = progress_bar(len(done), max(1, report.total - skipped))
    lines = [
        "*📋 Cron Officer — Morning Briefing*",
        f"`{_e(report.day.strftime('%d-%b-%Y'))}` *\\({_e(report.weekday)}\\)*  \\|  Mode: *{_e(report.mode)}*",
        _DIV,
        "📊 *Today's Plan*",
        f"`Total tasks   :` *{report.total}*",
        f"`Done so far   :` *{len(done)}*  ✅",
        f"`Pending       :` *{len(pending)}*  ⏳",
        f"`Skipped       :` *{skipped}*  ⏭",
        "",
        f"Progress: {bar}  `{pct}%`",
        _DIV,
    ]
    if done:
        lines.append("✅ *Already completed*")
        for j in done[:8]:
            lines.append(f"├ `{_e(j.due_label)}` {_e(j.name)}")
    if pending:
        lines.append("⏳ *Pending today \\(next 5\\)*")
        for j in pending[:5]:
            lines.append(f"├ `{_e(j.due_label)}` {_e(j.name)}")
        if len(pending) > 5:
            lines.append(f"└ `\\.\\.\\. \\+{len(pending) - 5} more`")
    # Slice 2: compact STRATEGY STATUS. Names go inside ` ` code spans so their
    # underscores are MarkdownV2-literal (no escaping needed); counts/parens are
    # escaped. Full table is in the email.
    ss = report.strategy_status
    if ss and (ss.get("will") or ss.get("wont") or ss.get("err")):
        will, wont, err = ss.get("will", []), ss.get("wont", []), ss.get("err", [])
        def _names(ns):
            return ", ".join(f"`{n}`" for n in ns) if ns else "—"
        lines.append(_DIV)
        lines.append(f"🎯 *Strategy Status* \\(master: {_e(ss.get('master', ''))}\\)")
        lines.append(f"✅ WILL TRADE \\({len(will)}\\): {_names(will)}")
        lines.append(f"⛔ WON'T TRADE \\({len(wont)}\\): {_names(wont)}")
        if err:
            lines.append(f"⚠️ CONFIG ERROR \\({len(err)}\\): {_names(err)}")
        lines.append("📧 Full table → email")
    lines += [_DIV, f"📨 EOD report ≈ *{_e('18:50 IST')}*"]
    # A post-ban CRITICAL briefing also sends a full clean HTML email (the compact
    # Telegram list above stays truncated for phones); point Rama to the full view.
    if report.severity == "CRITICAL":
        lines.append("📧 Full list → email")
    if report.preflight:
        emoji, _a, _b, label = _pf_style(report.preflight)
        lines.insert(0, f"*🚀 Pre\\-flight:* `{_e(label)}` {emoji}")
    return "\n".join(lines)


# ── plain-text (HTML fallback) ────────────────────────────────────────────────

def render_eod_plaintext(report: CronReport) -> str:
    bar, pct = progress_bar(report.completed, max(1, report.total - report.pending))
    L = [
        f"Cron Officer — Daily Report — {report.day.strftime('%d-%b-%Y')} ({report.weekday})",
        f"Mode: {report.mode} | Severity: {report.severity}",
        "=" * 52,
        "SUMMARY",
        f"  Total scheduled today : {report.total}",
        f"  Completed             : {report.completed}",
        f"  Failed                : {report.failed}",
        f"  Missed                : {report.missed}",
        f"  Skipped by design     : {report.skipped}",
        f"  Completion            : [{bar}] {pct}%",
        "",
    ]
    failed = [j for j in report.jobs if j.status == FAILED]
    L.append(f"FAILED ({len(failed)})")
    for j in failed:
        L.append(f"  - {j.due_label}  {j.name}  — {j.note or 'see logs'}")
    if not failed:
        L.append("  (none)")
    missed = [j for j in report.jobs if j.status == MISSED]
    L.append(f"MISSED ({len(missed)})")
    for j in missed:
        L.append(f"  - {j.due_label}  {j.name}")
    if not missed:
        L.append("  (none — clean run)")
    L.append("")
    L.append("CHANGE LOG (vs yesterday)")
    L.append(f"  Added   : {', '.join(report.added) or '(none)'}")
    L.append(f"  Removed : {', '.join(report.removed) or '(none)'}")
    L.append("")
    L.append("PER-TASK BREAKDOWN")
    L.append(f"  {'Time':<7}{'Task':<30}{'Status':<12}{'Run':>6}")
    for j in sorted(report.jobs, key=lambda x: (x.due_sort, x.name)):
        st = _STATUS.get(j.status, _STATUS[NOT_TRACKED])
        L.append(f"  {j.due_label:<7}{j.name:<30}{st['text']:<12}{_runtime_label(j.runtime_sec):>6}")
    L.append("")
    if report.excluded:
        L.append("EXCLUDED FROM MONITORING")
        for name, reason in report.excluded:
            L.append(f"  - {name} — {reason}")
        L.append("")
    for line in report.extra_lines:
        L.append(line)
    L.append("-" * 52)
    L.append(f"Severity : {report.severity} | Module: cron_officer | Mode: {report.mode}")
    L.append(f"Time     : {report.ts_iso}")
    L.append(f"Alert ID : {report.alert_id}")
    return "\n".join(L)


def render_briefing_plaintext(report: CronReport) -> str:
    done = [j for j in report.jobs if j.status == COMPLETED]
    pending = [j for j in report.jobs if j.status in (PENDING, PENDING_REDESIGN)]
    L = [
        f"Cron Officer — Morning Briefing — {report.day.strftime('%d-%b-%Y')} ({report.weekday})",
        f"Mode: {report.mode}",
        "=" * 52,
        f"  Total tasks today : {report.total}",
        f"  Done so far       : {len(done)}",
        f"  Pending           : {len(pending)}",
        f"  Skipped by design : {report.skipped}",
        "",
        "ALREADY COMPLETED",
    ]
    for j in done:
        L.append(f"  - {j.due_label}  {j.name}")
    if not done:
        L.append("  (none yet)")
    L.append("PENDING TODAY")
    for j in pending:
        L.append(f"  - {j.due_label}  {j.name}")
    # Slice 2: STRATEGY STATUS (plain mirror of the email table).
    if report.strategy_status and report.strategy_status.get("plain"):
        L.append("")
        L.append("STRATEGY STATUS")
        L.append(report.strategy_status["plain"])
    L.append("")
    L.append(f"EOD report ≈ 18:50 IST | Alert ID: {report.alert_id}")
    if report.preflight:
        _e0, _a, _b, label = _pf_style(report.preflight)
        L.insert(0, f"Pre-flight: {label} "
                 f"({report.preflight.get('critical', 0)} crit, "
                 f"{report.preflight.get('warnings', 0)} warn)")
    return "\n".join(L)


# ── HTML (Gmail-safe: inline CSS, table layout) ───────────────────────────────

def _esc_html(x) -> str:
    return (str(x).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _pill(status: str) -> str:
    s = _STATUS.get(status, _STATUS[NOT_TRACKED])
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
        f'background:{s["bg"]};color:{s["fg"]};font-size:12px;white-space:nowrap;">'
        f'{s["emoji"]} {_esc_html(s["text"])}</span>'
    )


def _stat_card(value, label: str, accent: str) -> str:
    return (
        f'<td style="padding:6px;"><table cellpadding="0" cellspacing="0" width="100%" '
        f'style="border:1px solid #E0E0E0;border-radius:6px;background:#FFFFFF;"><tr>'
        f'<td align="center" style="padding:10px;">'
        f'<div style="font-size:22px;font-weight:bold;color:{accent};">{_esc_html(value)}</div>'
        f'<div style="font-size:11px;color:#616161;text-transform:uppercase;">{_esc_html(label)}</div>'
        f'</td></tr></table></td>'
    )


def _html_progress(completed: int, total: int) -> str:
    _, pct = progress_bar(completed, total)
    filled_w = max(0, min(100, pct))
    return (
        f'<table cellpadding="0" cellspacing="0" width="100%" style="margin:8px 0;"><tr>'
        f'<td style="background:#E0E0E0;border-radius:8px;padding:0;">'
        f'<table cellpadding="0" cellspacing="0" width="{filled_w}%"><tr>'
        f'<td style="background:#2E7D32;height:14px;border-radius:8px;">&nbsp;</td>'
        f'</tr></table></td>'
        f'<td width="48" align="right" style="font-size:12px;color:#424242;padding-left:8px;">'
        f'{pct}%</td></tr></table>'
    )


def _section(title: str, inner_html: str, accent: str = "#E0E0E0") -> str:
    return (
        f'<table cellpadding="0" cellspacing="0" width="100%" '
        f'style="margin:12px 0;border:1px solid #E0E0E0;border-left:4px solid {accent};'
        f'border-radius:4px;"><tr><td style="padding:10px 12px;">'
        f'<div style="font-size:13px;font-weight:bold;color:#424242;margin-bottom:6px;">{title}</div>'
        f'{inner_html}</td></tr></table>'
    )


def render_eod_html(report: CronReport) -> str:
    sev = _sev(report)
    # Banner
    banner = (
        f'<table cellpadding="0" cellspacing="0" width="100%" '
        f'style="background:{sev["bg"]};border-radius:6px;"><tr>'
        f'<td style="padding:14px 16px;border-left:6px solid {sev["accent"]};">'
        f'<div style="font-size:18px;font-weight:bold;color:{sev["accent"]};">'
        f'{sev["emoji"]} Cron Officer — Daily Report</div>'
        f'<div style="font-size:13px;color:#424242;margin-top:2px;">'
        f'{_esc_html(report.day.strftime("%d-%b-%Y"))} ({_esc_html(report.weekday)}) '
        f'&nbsp;|&nbsp; Mode: {_esc_html(report.mode)}</div>'
        f'</td></tr></table>'
    )
    # Stat cards + progress
    cards = (
        '<table cellpadding="0" cellspacing="0" width="100%"><tr>'
        + _stat_card(report.total, "Total", "#1565C0")
        + _stat_card(report.completed, "✅ Done", "#2E7D32")
        + _stat_card(report.failed, "❌ Failed", "#C62828")
        + _stat_card(report.missed, "⚠️ Missed", "#F57C00")
        + _stat_card(report.skipped, "⏭ Skipped", "#616161")
        + '</tr></table>'
        + _html_progress(report.completed, max(1, report.total - report.pending))
    )
    summary = _section("SUMMARY", cards, sev["accent"])

    def _job_lines(statuses, empty):
        rows = [j for j in report.jobs if j.status in statuses]
        if not rows:
            return f'<div style="color:#9E9E9E;font-size:13px;">{empty}</div>'
        out = []
        for j in rows:
            note = f' <span style="color:#757575;">— {_esc_html(j.note)}</span>' if j.note else ""
            out.append(
                f'<div style="font-size:13px;margin:2px 0;">• <code>{_esc_html(j.due_label)}</code> '
                f'<b>{_esc_html(j.name)}</b>{note}</div>'
            )
        return "".join(out)

    failed_sec = _section(f'❌ FAILED ({report.failed})',
                          _job_lines((FAILED,), "(none)"), "#C62828")
    missed_sec = _section(f'⚠️ MISSED ({report.missed})',
                          _job_lines((MISSED,), "(none — clean run)"), "#F57C00")
    skipped_sec = _section(f'⏭ SKIPPED BY DESIGN ({report.skipped})',
                           _job_lines((SKIPPED,), "(none)"), "#616161")

    # Change log
    chg = (
        f'<div style="font-size:13px;">Added today: <b>{_esc_html(", ".join(report.added) or "(none)")}</b></div>'
        f'<div style="font-size:13px;">Removed today: <b>{_esc_html(", ".join(report.removed) or "(none)")}</b></div>'
    )
    changelog_sec = _section("🔄 CHANGE LOG (vs yesterday)", chg)

    # Per-task table
    rows_html = []
    for i, j in enumerate(sorted(report.jobs, key=lambda x: (x.due_sort, x.name))):
        stripe = "#FAFAFA" if i % 2 else "#FFFFFF"
        row_bg = "#FFFDE7" if j.status == PENDING_REDESIGN else stripe
        note = f'<div style="font-size:11px;color:#9E9E9E;">{_esc_html(j.note)}</div>' if j.note else ""
        rows_html.append(
            f'<tr style="background:{row_bg};">'
            f'<td style="padding:4px 8px;font-family:monospace;font-size:12px;border-bottom:1px solid #EEEEEE;">{_esc_html(j.due_label)}</td>'
            f'<td style="padding:4px 8px;font-size:12px;border-bottom:1px solid #EEEEEE;">{_esc_html(j.name)}{note}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid #EEEEEE;">{_pill(j.status)}</td>'
            f'<td style="padding:4px 8px;font-family:monospace;font-size:12px;text-align:right;border-bottom:1px solid #EEEEEE;color:#616161;">{_esc_html(j.detection)}</td>'
            f'<td style="padding:4px 8px;font-family:monospace;font-size:12px;text-align:right;border-bottom:1px solid #EEEEEE;">{_esc_html(_runtime_label(j.runtime_sec))}</td>'
            f'</tr>'
        )
    table = (
        '<table cellpadding="0" cellspacing="0" width="100%" style="border:1px solid #E0E0E0;border-collapse:collapse;">'
        '<tr style="background:#ECEFF1;">'
        '<th style="padding:6px 8px;text-align:left;font-size:11px;color:#455A64;">Time</th>'
        '<th style="padding:6px 8px;text-align:left;font-size:11px;color:#455A64;">Task</th>'
        '<th style="padding:6px 8px;text-align:left;font-size:11px;color:#455A64;">Status</th>'
        '<th style="padding:6px 8px;text-align:right;font-size:11px;color:#455A64;">Detect</th>'
        '<th style="padding:6px 8px;text-align:right;font-size:11px;color:#455A64;">Run</th>'
        '</tr>' + "".join(rows_html) + '</table>'
    )
    pertask_sec = _section(f"📋 PER-TASK BREAKDOWN ({report.total} jobs)", table)

    # Excluded
    if report.excluded:
        exc = "".join(
            f'<div style="font-size:12px;">• <b>{_esc_html(n)}</b> — {_esc_html(r)}</div>'
            for n, r in report.excluded)
    else:
        exc = '<div style="color:#9E9E9E;font-size:12px;">(none currently)</div>'
    excluded_sec = _section("🚫 EXCLUDED FROM MONITORING", exc)

    extra = ""
    if report.extra_lines:
        extra = _section("ℹ️ NOTES", "".join(
            f'<div style="font-size:12px;">{_esc_html(ln)}</div>' for ln in report.extra_lines))

    footer = (
        f'<table cellpadding="0" cellspacing="0" width="100%" style="margin-top:12px;'
        f'background:#FAFAFA;border-top:1px solid #E0E0E0;"><tr><td style="padding:10px 12px;'
        f'font-family:monospace;font-size:11px;color:#616161;">'
        f'Severity : {_esc_html(report.severity)}<br>Module&nbsp;&nbsp;: cron_officer<br>'
        f'Mode&nbsp;&nbsp;&nbsp;&nbsp;: {_esc_html(report.mode)}<br>'
        f'Time&nbsp;&nbsp;&nbsp;&nbsp;: {_esc_html(report.ts_iso)}<br>'
        f'Alert ID : {_esc_html(report.alert_id)}</td></tr></table>'
    )

    body = (banner + summary + failed_sec + missed_sec + skipped_sec
            + changelog_sec + pertask_sec + excluded_sec + extra + footer)
    return (
        '<html><body style="margin:0;padding:12px;background:#F5F5F5;'
        'font-family:Arial,Helvetica,sans-serif;color:#212121;">'
        '<table cellpadding="0" cellspacing="0" width="100%" style="max-width:680px;margin:0 auto;">'
        f'<tr><td>{body}</td></tr></table></body></html>'
    )


# ── pre-flight banner (embedded at the top of the morning briefing) ─────────────
_PF_BANNER = {
    "READY":               ("✅", "#2E7D32", "#E8F5E9", "READY"),
    "READY_WITH_WARNINGS": ("⚠️", "#F57C00", "#FFF8E1", "READY · warnings"),
    "CRITICAL_FAILURE":    ("🔴", "#C62828", "#FFEBEE", "CRITICAL"),
    "NOT_RUN":             ("❓", "#C62828", "#FFEBEE", "NOT RUN — investigate"),
}


def _pf_style(pf: Optional[dict]) -> Tuple[str, str, str, str]:
    status = (pf or {}).get("status") or "NOT_RUN"
    return _PF_BANNER.get(status, _PF_BANNER["NOT_RUN"])


def _preflight_banner_html(pf: Optional[dict]) -> str:
    if not pf:
        return ""
    emoji, accent, bg, label = _pf_style(pf)
    if ((pf.get("status") or "NOT_RUN") == "NOT_RUN"):
        sub = "pre-flight sentinel missing or stale — did pre-flight run?"
    else:
        sub = (f'{pf.get("critical", 0)} critical · {pf.get("warnings", 0)} warnings · '
               f'{pf.get("autofixed", 0)} auto-fixed · id {_esc_html(pf.get("alert_id", ""))}')
    return (
        f'<table cellpadding="0" cellspacing="0" width="100%" '
        f'style="background:{bg};border-radius:6px;margin-bottom:8px;"><tr>'
        f'<td style="padding:12px 16px;border-left:6px solid {accent};">'
        f'<div style="font-size:16px;font-weight:bold;color:{accent};">🚀 Pre-flight: {emoji} {_esc_html(label)}</div>'
        f'<div style="font-size:12px;color:#424242;margin-top:2px;">{sub}</div>'
        f'</td></tr></table>'
    )


def render_briefing_html(report: CronReport) -> str:
    done = report.completed
    pending = report.pending
    banner = (
        '<table cellpadding="0" cellspacing="0" width="100%" style="background:#E3F2FD;border-radius:6px;">'
        '<tr><td style="padding:14px 16px;border-left:6px solid #1565C0;">'
        '<div style="font-size:18px;font-weight:bold;color:#1565C0;">📋 Cron Officer — Morning Briefing</div>'
        f'<div style="font-size:13px;color:#424242;margin-top:2px;">'
        f'{_esc_html(report.day.strftime("%d-%b-%Y"))} ({_esc_html(report.weekday)}) &nbsp;|&nbsp; Mode: {_esc_html(report.mode)}</div>'
        '</td></tr></table>'
    )
    cards = (
        '<table cellpadding="0" cellspacing="0" width="100%"><tr>'
        + _stat_card(report.total, "Total", "#1565C0")
        + _stat_card(done, "✅ Done", "#2E7D32")
        + _stat_card(pending, "⏳ Pending", "#1565C0")
        + _stat_card(report.skipped, "⏭ Skipped", "#616161")
        + '</tr></table>'
        + _html_progress(done, max(1, report.total - report.skipped))
    )
    summary = _section("TODAY'S PLAN", cards, "#1565C0")

    def _list(statuses, empty):
        rows = [j for j in sorted(report.jobs, key=lambda x: (x.due_sort, x.name))
                if j.status in statuses]
        if not rows:
            return f'<div style="color:#9E9E9E;font-size:13px;">{empty}</div>'
        return "".join(
            f'<div style="font-size:13px;margin:2px 0;">• <code>{_esc_html(j.due_label)}</code> {_esc_html(j.name)}</div>'
            for j in rows)

    done_sec = _section("✅ ALREADY COMPLETED", _list((COMPLETED,), "(none yet)"), "#2E7D32")
    pend_sec = _section("⏳ PENDING TODAY", _list((PENDING, PENDING_REDESIGN), "(none)"), "#1565C0")
    # Slice 2: STRATEGY STATUS table (full, no truncation). Pre-rendered fragment.
    strat_sec = ""
    if report.strategy_status and report.strategy_status.get("html"):
        strat_sec = _section("STRATEGY STATUS", report.strategy_status["html"], "#6A1B9A")
    footer = (
        '<div style="margin-top:12px;font-family:monospace;font-size:11px;color:#616161;">'
        f'EOD report ≈ 18:50 IST &nbsp;|&nbsp; Alert ID: {_esc_html(report.alert_id)}</div>'
    )
    body = _preflight_banner_html(report.preflight) + banner + summary + done_sec + pend_sec + strat_sec + footer
    return (
        '<html><body style="margin:0;padding:12px;background:#F5F5F5;'
        'font-family:Arial,Helvetica,sans-serif;color:#212121;">'
        '<table cellpadding="0" cellspacing="0" width="100%" style="max-width:680px;margin:0 auto;">'
        f'<tr><td>{body}</td></tr></table></body></html>'
    )
