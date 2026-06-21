"""
tests/unit/test_preflight_render.py -- pre-flight report renderers (HTML / Telegram
/ plaintext / subject). Reuses the Cron Officer cosmetic language.
"""
from __future__ import annotations

from datetime import date

from scripts.preflight import sentinel
from scripts.preflight.base import Criticality, Status
from scripts.preflight.report import (
    CheckRecord, PreflightReport, render_html, render_plaintext, render_telegram, subject,
)


def _rec(name, status, crit=Criticality.CRITICAL, group="VM Health", detail="d",
         fix_attempted=False, fix_result=""):
    return CheckRecord(name=name, group=group, criticality=crit, status=status,
                       detail=detail, duration_ms=5,
                       fix_attempted=fix_attempted, fix_result=fix_result)


def _rep(records):
    return PreflightReport(phase="A", run_date=date(2026, 6, 22),
                           run_id="20260622_083000_a", mode="live", account="LFL836",
                           started_at="s", completed_at="2026-06-22T08:35:00+05:30",
                           records=records)


# ── subjects ─────────────────────────────────────────────────────────────────────
def test_subject_ready():
    s = subject(_rep([_rec("a", Status.PASS)]))
    assert s == "[LFL836] ✅ Pre-flight READY — 22-Jun"


def test_subject_ban_prefix_on_critical_during_ban():
    # Fix 4: BAN prefix must be on CRITICAL too (so an [LFL836-BAN] filter catches it).
    s = subject(_rep([_rec("a", Status.FAIL)]), ban_active=True)
    assert s == "[LFL836-BAN] 🔴 CRITICAL — Pre-flight FAILED — 22-Jun"


def test_subject_no_ban_prefix_after_ban_date():
    s = subject(_rep([_rec("a", Status.FAIL)]), ban_active=False)
    assert not s.startswith("[LFL836-BAN]") and s.startswith("[LFL836]")


def test_subject_ban_prefix_for_ready():
    s = subject(_rep([_rec("a", Status.PASS)]), ban_active=True)
    assert s.startswith("[LFL836-BAN]")


def test_subject_warnings():
    s = subject(_rep([_rec("a", Status.WARN, crit=Criticality.WARN)]))
    assert "READY (1 warnings)" in s and s.startswith("[LFL836]")


# ── HTML ─────────────────────────────────────────────────────────────────────────
def test_render_html_smoke():
    rep = _rep([
        _rec("vm_ram", Status.PASS),
        _rec("svc_cron", Status.FAIL, detail="cron not active"),
        _rec("alert_backlog", Status.WARN, crit=Criticality.WARN, group="Recovery"),
        _rec("db_journal_mode", Status.AUTOFIXED, group="Database",
             fix_attempted=True, fix_result="SUCCESS"),
        _rec("log_rotation", Status.SKIPPED, crit=Criticality.INFO, group="Recovery"),
    ])
    html = render_html(rep)
    assert html.startswith("<html>") and html.rstrip().endswith("</html>")
    assert "🚀 Pre-flight" in html
    assert "cron not active" in html          # critical detail surfaced
    assert "PER-CHECK" in html
    assert "20260622_083000_a" in html        # alert id in footer


def test_render_html_severity_color_critical():
    html = render_html(_rep([_rec("x", Status.FAIL)]))
    assert "#C62828" in html  # critical red accent


def test_render_html_ready_color():
    html = render_html(_rep([_rec("x", Status.PASS)]))
    assert "#2E7D32" in html  # green accent


# ── Telegram (MarkdownV2) ─────────────────────────────────────────────────────────
def test_render_telegram_escapes_specials():
    tg = render_telegram(_rep([_rec("db_journal_mode", Status.FAIL)]))
    assert "db\\_journal\\_mode" in tg        # underscore escaped
    assert "Full details" in tg


def test_render_telegram_counts():
    tg = render_telegram(_rep([_rec("a", Status.PASS), _rec("b", Status.FAIL)]))
    assert "Passed" in tg and "Critical" in tg


def test_telegram_includes_skipped_line():
    tg = render_telegram(_rep([_rec("a", Status.PASS),
                               _rec("b", Status.SKIPPED, crit=Criticality.INFO)]))
    assert "Skipped" in tg and "⏭" in tg


# ── Fix 2: stat cards + sum invariant ─────────────────────────────────────────────
def test_stat_cards_include_skipped():
    html = render_html(_rep([_rec("a", Status.PASS),
                             _rec("b", Status.SKIPPED, crit=Criticality.INFO)]))
    assert "⏭ Skipped" in html  # the stat card label


def test_total_equals_sum_of_all_status_buckets():
    rep = _rep([
        _rec("a", Status.PASS),
        _rec("b", Status.FAIL),                                  # critical
        _rec("c", Status.FAIL, crit=Criticality.WARN),          # counts as warning
        _rec("d", Status.WARN, crit=Criticality.WARN),
        _rec("e", Status.AUTOFIXED),
        _rec("f", Status.SKIPPED, crit=Criticality.INFO),
    ])
    assert rep.total == (rep.passed + rep.failed_critical + rep.warnings
                         + rep.autofixed + rep.skipped)


# ── plaintext ─────────────────────────────────────────────────────────────────────
def test_render_plaintext_sections():
    txt = render_plaintext(_rep([_rec("a", Status.FAIL), _rec("b", Status.PASS)]))
    assert "CRITICAL FAILURES" in txt and "PER-CHECK" in txt
