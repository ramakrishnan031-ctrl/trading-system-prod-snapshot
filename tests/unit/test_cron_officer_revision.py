"""Cron Officer revision (Phase 2-6): category derivation, Bug A/B, detection
methods, rich render, content_type email, ban routing."""
from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path

import pytest

from core.cron_registry import CronRegistry, OfficerConfig, resolve_category
from core.state_store import StateStore
from scripts import cron_officer as co
from scripts import cron_report_render as R

_REAL = Path("config/cron_registry.yaml")
MON = date(2026, 6, 22)
_CATS = ("MARKET_DAY", "DAILY", "WEEKLY", "MONTHLY", "ON_DEMAND")


# ── Phase 2: category derivation (single source of truth) ─────────────────────

def test_resolve_category_from_cadence():
    assert resolve_category("market_day") == "MARKET_DAY"
    assert resolve_category("daily") == "DAILY"
    assert resolve_category("weekly") == "WEEKLY"
    assert resolve_category("monthly") == "MONTHLY"
    assert resolve_category("intraday") == "MARKET_DAY"
    assert resolve_category("hourly") == "DAILY"
    assert resolve_category("market_day", "ON_DEMAND") == "ON_DEMAND"  # override wins


def test_every_real_job_resolves_to_valid_category():
    reg = CronRegistry.load(_REAL)
    for j in reg.all_jobs():
        assert j.effective_category in _CATS, j.name


def test_detection_methods_derived():
    reg = CronRegistry.load(_REAL)
    # premarket_healthcheck retired (subsumed by pre-flight Phase A); reconcile_positions
    # is the heartbeat_db exemplar now.
    assert reg.get("reconcile_positions").effective_detection_method == "heartbeat_db"
    assert reg.get("log_cleanup").effective_detection_method == "exit_code_file"
    assert reg.get("check_cron_drift").effective_detection_method == "none"
    assert reg.get("preflight_phase_a").effective_detection_method == "exit_code_file"  # explicit override


# ── Phase 3 Bug A: registry key matches the recorded heartbeat name ───────────

def test_bug_a_registry_key_renamed():
    names = {j.name for j in CronRegistry.load(_REAL).all_jobs()}
    assert "gemini_data_integrity_check" in names
    assert "gemini_data_integrity" not in names


# ── Phase 6: officer config + dynamic EOD time + ban ──────────────────────────

def test_officer_config_eod_time_and_ban():
    reg = CronRegistry.load(_REAL)
    assert reg.officer.morning_briefing_time == "09:20"
    assert reg.eod_report_time(MON, Path("config")) == time(18, 50)
    # F4 (15-Jul): the stale telegram_ban_until ('2026-06-23') was PURGED (null) — no ban
    # is active for any date now. (This previously asserted ban_active(MON) is True.)
    assert reg.officer.telegram_ban_until is None
    assert reg.officer.ban_active(MON) is False
    assert reg.officer.ban_active(date(2026, 6, 24)) is False


def test_officer_config_defaults_when_absent():
    oc = OfficerConfig()
    assert oc.eod_floor_time == "18:45" and oc.ban_active(MON) is False


# ── tested helpers ────────────────────────────────────────────────────────────

def test_progress_bar_math():
    assert R.progress_bar(0, 10) == ("▱" * 10, 0)
    assert R.progress_bar(5, 10) == ("▰" * 5 + "▱" * 5, 50)
    assert R.progress_bar(10, 10) == ("▰" * 10, 100)
    assert R.progress_bar(28, 30)[1] == 93
    assert R.progress_bar(1, 0) == ("▱" * 10, 0)   # no ZeroDivision


def test_escape_md_v2_every_reserved_char():
    assert R.escape_md_v2("a.b-c!") == "a\\.b\\-c\\!"
    for ch in "_*[]()~`>#+-=|{}.!":
        assert R.escape_md_v2(ch) == "\\" + ch


# ── build_report classification ───────────────────────────────────────────────

def _report(tmp_path, store):
    return co.build_report(CronRegistry.load(_REAL), store, MON, Path("config"),
                           time(18, 50), is_eod=True,
                           marks_dir=tmp_path / "marks", audit_dir=tmp_path / "audit")


def test_bug_b_cron_officer_eod_not_missed_with_started(tmp_path):
    store = StateStore(tmp_path / "t.db")
    store.insert_cron_heartbeat(job_name="cron_officer_eod",
                                executed_at="2026-06-22T18:50:00+05:30",
                                status="STARTED", duration_sec=0.0, message=None)
    rep = _report(tmp_path, store)
    store.close()
    eod = next(j for j in rep.jobs if j.name == "cron_officer_eod")
    assert eod.status == R.COMPLETED   # STARTED counts as seen, not MISSED


def test_bug_a_heartbeat_now_detected(tmp_path):
    store = StateStore(tmp_path / "t.db")
    store.insert_cron_heartbeat(job_name="gemini_data_integrity_check",
                                executed_at="2026-06-22T17:00:00+05:30",
                                status="SUCCESS", duration_sec=3.0, message=None)
    rep = _report(tmp_path, store)
    store.close()
    j = next(j for j in rep.jobs if j.name == "gemini_data_integrity_check")
    assert j.status == R.COMPLETED


def _report_with(tmp_path, *, heartbeat_for_daily_report: bool):
    """Build a real EOD report where every heartbeat job has fired, optionally
    excluding daily_report -- so the only variable is daily_report's heartbeat."""
    store = StateStore(tmp_path / "t.db")
    reg = CronRegistry.load(_REAL)
    for j in reg.all_jobs():
        if j.effective_detection_method != "heartbeat_db":
            continue
        if j.name == "daily_report" and not heartbeat_for_daily_report:
            continue
        store.insert_cron_heartbeat(job_name=j.name,
                                    executed_at="2026-06-22T10:00:00+05:30",
                                    status="SUCCESS", duration_sec=1.0, message=None)
    # markers for the exit_code_file jobs so nothing is MISSED/NO_SIGNAL-bad
    marks = tmp_path / "marks"; marks.mkdir()
    for j in reg.all_jobs():
        if j.effective_detection_method == "exit_code_file":
            (marks / f"{j.name}.done").write_text("0 2026-06-22T00:00:01+05:30")
    rep = co.build_report(reg, store, MON, Path("config"), time(18, 50), is_eod=True,
                          marks_dir=marks, audit_dir=tmp_path / "audit",
                          root=tmp_path)  # root w/o security_state -> watcher line only
    store.close()
    return rep


def test_daily_report_completes_like_any_other_heartbeat_job(tmp_path):
    """The Bug-C deferral is gone: daily_report's heartbeat is READ.

    While `daily_report` sat in _PENDING_REDESIGN_JOBS the classifier returned
    before ever calling hb.get(), so the job rendered ⏸ Pending no matter what
    the heartbeat said -- for two months after the heartbeat started landing.
    """
    rep = _report_with(tmp_path, heartbeat_for_daily_report=True)
    dr = next(j for j in rep.jobs if j.name == "daily_report")
    assert dr.status == R.COMPLETED
    assert dr.status != R.PENDING_REDESIGN
    assert rep.failed == 0 and rep.missed == 0
    # watcher_stale=False isolates job severity: this fixture has no
    # security_state, so rep.severity is CRITICAL from the stale watcher alone
    # and would be green here for the wrong reason.
    assert co._compute_severity(rep.jobs, False) == "INFO"


def test_daily_report_missing_heartbeat_now_escalates(tmp_path):
    """The blind spot itself: with the deferral in place this case reported
    ⏸ Pending and INFO. A silently dead daily_report was unreportable."""
    rep = _report_with(tmp_path, heartbeat_for_daily_report=False)
    dr = next(j for j in rep.jobs if j.name == "daily_report")
    assert dr.status == R.MISSED
    assert rep.missed >= 1
    # watcher_stale=False, so the escalation can only come from daily_report --
    # otherwise this passes on the stale watcher and proves nothing.
    assert co._compute_severity(rep.jobs, False) == "CRITICAL"
    assert co._compute_severity([dr], False) == "CRITICAL"


def test_pending_redesign_set_is_empty_and_suppresses_nothing(tmp_path):
    """Guards the set itself. The mechanism is retained deliberately, but any
    name in it is un-alertable -- so an addition must be a conscious act, not a
    leftover. If this goes red, something was silenced."""
    assert co._PENDING_REDESIGN_JOBS == set()


def test_marker_detection_states(tmp_path):
    marks = tmp_path / "marks"; marks.mkdir()
    (marks / "log_cleanup.done").write_text("0 2026-06-22T00:00:01+05:30")
    (marks / "db_backup.done").write_text("1 2026-06-22T01:00:01+05:30")
    (marks / "stale.done").write_text("0 2026-06-19T00:00:01+05:30")
    assert co._read_marker("log_cleanup", MON, marks)[0] == R.COMPLETED
    assert co._read_marker("db_backup", MON, marks)[0] == R.FAILED
    assert co._read_marker("missing", MON, marks)[0] == R.NO_SIGNAL
    assert co._read_marker("stale", MON, marks)[0] == R.NO_SIGNAL


def test_severity_logic():
    ok = R.JobOutcome("a", "DAILY", "09:00", time(9), R.COMPLETED, "heartbeat_db")
    pend = R.JobOutcome("daily_report", "MARKET_DAY", "16:05", time(16, 5),
                        R.PENDING_REDESIGN, "none")
    nosig = R.JobOutcome("log_cleanup", "DAILY", "00:00", time(0), R.NO_SIGNAL, "exit_code_file")
    assert co._compute_severity([ok, pend, nosig], False) == "INFO"
    bad = R.JobOutcome("b", "DAILY", "09:00", time(9), R.FAILED, "heartbeat_db")
    assert co._compute_severity([ok, bad], False) == "CRITICAL"
    miss = R.JobOutcome("c", "MARKET_DAY", "15:45", time(15, 45), R.MISSED, "heartbeat_db")
    assert co._compute_severity([ok, miss], False) == "CRITICAL"
    assert co._compute_severity([ok], True) == "CRITICAL"   # watcher stale


# ── content_type email (alert_watcher) ────────────────────────────────────────

def test_email_plain_default():
    from scripts.alert_watcher import _build_email
    msg = _build_email({"title": "t", "body": "hi", "context": {"severity": "INFO"}},
                       "f@x.com", ["t@x.com"])
    assert msg.get_content_type() == "text/plain"


def test_email_html_multipart_with_custom_subject():
    from scripts.alert_watcher import _build_email
    msg = _build_email({"title": "t", "subject": "[LFL836-BAN] sub",
                        "content_type": "text/html", "plain_fallback": "plain mirror",
                        "html_body": "<b>hi</b>", "context": {"severity": "INFO"}},
                       "f@x.com", ["t@x.com"])
    assert msg.get_content_type() == "multipart/alternative"
    assert msg["Subject"] == "[LFL836-BAN] sub"
    payloads = [p.get_content_type() for p in msg.get_payload()]
    assert payloads == ["text/plain", "text/html"]   # html last = preferred


def test_email_html_without_fallback_fails_fast():
    from scripts.alert_watcher import _build_email
    with pytest.raises(ValueError):
        _build_email({"title": "t", "content_type": "text/html", "html_body": "<b>x</b>",
                      "context": {"severity": "INFO"}}, "f@x.com", ["t@x.com"])


# ── delivery + ban routing ────────────────────────────────────────────────────

def _mk_report(is_eod=True, ban=True, sev="INFO"):
    return R.CronReport(
        day=MON, weekday="Monday", mode="Live", is_eod=is_eod,
        jobs=[R.JobOutcome("a", "DAILY", "09:00", time(9), R.COMPLETED, "heartbeat_db")],
        severity=sev, ban_active=ban, alert_id="x", ts_iso="2026-06-22T18:50:00+05:30")


def test_deliver_eod_writes_html_sentinel(tmp_path):
    co.deliver_report(_mk_report(), Path("config"), dry_run=False, sentinel_dir=tmp_path)
    flags = list(tmp_path.glob("critical_alert_*.flag"))
    assert len(flags) == 1
    data = json.loads(flags[0].read_text(encoding="utf-8"))
    assert data["content_type"] == "text/html"
    assert data["plain_fallback"] and data["html_body"]
    assert data["subject"].startswith("[LFL836-BAN]")


def test_deliver_briefing_during_ban_emails(tmp_path):
    # During the ban a briefing must email (not Telegram-only).
    co.deliver_report(_mk_report(is_eod=False, ban=True), Path("config"),
                      dry_run=False, sentinel_dir=tmp_path)
    assert len(list(tmp_path.glob("critical_alert_*.flag"))) == 1


def test_deliver_dry_run_writes_nothing(tmp_path):
    parts = co.deliver_report(_mk_report(), Path("config"), dry_run=True, sentinel_dir=tmp_path)
    assert list(tmp_path.glob("*.flag")) == []
    assert parts["subject"].startswith("[LFL836-BAN]") and "<html>" in parts["html"]


# ── 24-Jun email-leak fix: post-ban CRITICAL briefing routing ─────────────────

def test_deliver_briefing_postban_critical_clean_html_email(tmp_path, monkeypatch):
    # Post-ban (ban=False) CRITICAL briefing -> Telegram AND a clean HTML email
    # backup (NOT a raw-MarkdownV2 blob). Isolate the Telegram side.
    sent = []
    monkeypatch.setattr(co, "_send_telegram_md", lambda *a, **k: sent.append(a))
    co.deliver_report(_mk_report(is_eod=False, ban=False, sev="CRITICAL"),
                      Path("config"), dry_run=False, sentinel_dir=tmp_path)
    flags = list(tmp_path.glob("critical_alert_*.flag"))
    assert len(flags) == 1                              # exactly one clean email
    data = json.loads(flags[0].read_text(encoding="utf-8"))
    assert data["content_type"] == "text/html"
    assert data["html_body"] and data["plain_fallback"]
    assert "<html>" in data["html_body"]                # real HTML …
    assert "\\(" not in data["html_body"]               # … not MarkdownV2 escaping
    assert data["subject"].startswith("[LFL836]")       # ban prefix gone
    assert sent, "Telegram briefing must still be sent post-ban"


def test_deliver_briefing_postban_infowarn_telegram_only(tmp_path, monkeypatch):
    # Post-ban INFO/WARN briefing stays Telegram-only — no email at all.
    sent = []
    monkeypatch.setattr(co, "_send_telegram_md", lambda *a, **k: sent.append(a))
    co.deliver_report(_mk_report(is_eod=False, ban=False, sev="INFO"),
                      Path("config"), dry_run=False, sentinel_dir=tmp_path)
    assert list(tmp_path.glob("*.flag")) == []          # no email
    assert sent                                         # Telegram only


def test_deliver_briefing_email_lists_all_jobs_no_truncation(tmp_path, monkeypatch):
    # Part 2: the EMAIL lists every job (no "+N more"); the compact Telegram render
    # still truncates. 20 completed + 19 pending = 39 jobs.
    monkeypatch.setattr(co, "_send_telegram_md", lambda *a, **k: None)
    jobs = [R.JobOutcome(f"done_job_{i:02d}", "DAILY", f"{i % 24:02d}:00",
                         time(i % 24), R.COMPLETED, "heartbeat_db") for i in range(20)]
    jobs += [R.JobOutcome(f"pending_job_{i:02d}", "MARKET_DAY", "15:45",
                          time(15, 45), R.PENDING, "heartbeat_db") for i in range(19)]
    rep = R.CronReport(day=MON, weekday="Monday", mode="Live", is_eod=False, jobs=jobs,
                       severity="CRITICAL", ban_active=False, alert_id="x",
                       ts_iso="2026-06-22T18:50:00+05:30")
    co.deliver_report(rep, Path("config"), dry_run=False, sentinel_dir=tmp_path)
    html = json.loads(next(tmp_path.glob("critical_alert_*.flag"))
                      .read_text(encoding="utf-8"))["html_body"]
    for i in range(20):
        assert f"done_job_{i:02d}" in html              # every completed job
    for i in range(19):
        assert f"pending_job_{i:02d}" in html           # every pending job (no cap)
    # Compact Telegram STILL truncates (next 5 + "+N more").
    tg = R.render_briefing_telegram(rep)
    assert "more" in tg and "pending_job_18" not in tg


# ── HTML render smoke ─────────────────────────────────────────────────────────

def test_eod_html_has_key_sections():
    rep = R.CronReport(
        day=MON, weekday="Monday", mode="Live", is_eod=True,
        jobs=[R.JobOutcome("reconcile_positions", "MARKET_DAY", "15:45", time(15, 45),
                           R.FAILED, "heartbeat_db", 0.0, "KiteTimeout"),
              R.JobOutcome("daily_report", "MARKET_DAY", "16:05", time(16, 5),
                           R.PENDING_REDESIGN, "none", 0.0, "pending redesign")],
        severity="CRITICAL", alert_id="abc", ts_iso="2026-06-22T18:50:00+05:30")
    html = R.render_eod_html(rep)
    assert "<html>" in html and "PER-TASK BREAKDOWN" in html
    assert "reconcile_positions" in html and "KiteTimeout" in html
    assert "CRITICAL" in html
    plain = R.render_eod_plaintext(rep)
    assert "reconcile_positions" in plain and "daily_report" in plain
    tg = R.render_eod_telegram(rep)
    assert "Cron Officer" in tg and "\\(" not in R.escape_md_v2("(")[1:]  # escaping sane
