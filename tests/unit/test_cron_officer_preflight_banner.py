"""
tests/unit/test_cron_officer_preflight_banner.py -- Cron Officer morning briefing
embeds the pre-flight banner (reads data_store/preflight/today.json). Additive:
preflight=None must leave the briefing unchanged (backward compatible).
"""
from __future__ import annotations

import json
from datetime import date

from scripts import cron_officer
from scripts.cron_report_render import (
    CronReport, render_briefing_html, render_briefing_plaintext, render_briefing_telegram,
)


def _brief(preflight=None):
    return CronReport(day=date(2026, 6, 22), weekday="Monday", mode="Live",
                      is_eod=False, jobs=[], alert_id="b1", preflight=preflight)


# ── render: banner present / colored / backward-compatible ──────────────────────
def test_banner_ready_green():
    html = render_briefing_html(_brief({"status": "READY", "critical": 0, "warnings": 0,
                                        "autofixed": 0, "alert_id": "id1"}))
    assert "🚀 Pre-flight" in html and "READY" in html and "#2E7D32" in html


def test_banner_not_run_red():
    html = render_briefing_html(_brief({"status": "NOT_RUN"}))
    assert "NOT RUN" in html and "#C62828" in html and "missing or stale" in html


def test_no_banner_when_none():
    assert "Pre-flight" not in render_briefing_html(_brief(None))  # unchanged


def test_telegram_banner():
    tg = render_briefing_telegram(_brief({"status": "CRITICAL_FAILURE", "critical": 2,
                                          "warnings": 1, "autofixed": 0, "alert_id": "x"}))
    assert "Pre\\-flight" in tg and tg.splitlines()[0].startswith("*🚀")


def test_plaintext_banner():
    txt = render_briefing_plaintext(_brief({"status": "READY", "critical": 0, "warnings": 0,
                                            "autofixed": 0, "alert_id": "x"}))
    assert txt.splitlines()[0].startswith("Pre-flight: READY")


# ── cron_officer._read_preflight_summary ────────────────────────────────────────
def test_summary_missing(tmp_path):
    assert cron_officer._read_preflight_summary(date(2026, 6, 22), root=tmp_path)["status"] == "NOT_RUN"


def test_summary_today(tmp_path):
    p = tmp_path / "data_store" / "preflight"
    p.mkdir(parents=True)
    (p / "today.json").write_text(json.dumps({
        "run_date": "2026-06-22", "overall_status": "READY_WITH_WARNINGS",
        "critical_count": 0, "warning_count": 2, "autofix_count": 1, "alert_id": "rid"}),
        encoding="utf-8")
    s = cron_officer._read_preflight_summary(date(2026, 6, 22), root=tmp_path)
    assert s["status"] == "READY_WITH_WARNINGS" and s["warnings"] == 2 and s["autofixed"] == 1


def test_summary_stale_is_not_run(tmp_path):
    p = tmp_path / "data_store" / "preflight"
    p.mkdir(parents=True)
    (p / "today.json").write_text(json.dumps({"run_date": "2026-06-19", "overall_status": "READY"}),
                                  encoding="utf-8")
    assert cron_officer._read_preflight_summary(date(2026, 6, 22), root=tmp_path)["status"] == "NOT_RUN"
