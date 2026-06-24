"""Tests for scripts/cron_officer.py (TASK #3 briefing / EOD / change-detect)."""
from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from core.cron_registry import CronRegistry
from core.state_store import StateStore
from scripts.cron_officer import (
    _classify_job,
    _compute_severity,
    _cron_hhmm,
    _job_token,
    build_briefing,
    build_change_report,
    build_eod_summary,
    parse_crontab,
    COMPLETED,
    MISSED,
    PENDING,
)

MON = date(2026, 6, 15)
SAT = date(2026, 6, 20)
_REAL = Path("config/cron_registry.yaml")

_MINI = """
jobs:
  job_a:
    script: scripts/job_a.py
    schedule: "09:00 Mon-Fri"
    type: python
    critical: true
    market_day_only: true
    cadence: market_day
    monitored: true
  job_b:
    script: scripts/job_b.py
    schedule: "09:05 Mon-Fri"
    type: python
    critical: false
    market_day_only: true
    cadence: market_day
    monitored: true
  job_c:
    script: scripts/job_c.py
    schedule: "09:10 Mon-Fri"
    type: python
    critical: true
    market_day_only: true
    cadence: market_day
    monitored: true
"""


def _mini(tmp_path: Path) -> CronRegistry:
    p = tmp_path / "cron_registry.yaml"
    p.write_text(_MINI, encoding="utf-8")
    return CronRegistry.load(p)


# ── briefing ─────────────────────────────────────────────────────────────────


class TestBriefing:
    def test_trading_day_lists_jobs(self, tmp_path):
        reg = CronRegistry.load(_REAL)
        msg = build_briefing(reg, MON, tmp_path)  # empty cfg dir -> weekday fallback
        assert "Today's Schedule" in msg
        assert "auto_refresh_token" in msg
        assert "⚡" in msg  # critical marker
        assert "Total:" in msg

    def test_weekend_shows_no_market_jobs(self, tmp_path):
        reg = CronRegistry.load(_REAL)
        msg = build_briefing(reg, SAT, tmp_path)
        assert "No market-day jobs today" in msg
        # a market_day job must NOT appear
        assert "auto_refresh_token" not in msg
        # an all-days job should
        assert "db_backup" in msg


# ── EOD summary ──────────────────────────────────────────────────────────────


class TestEodSummary:
    def test_counts_and_critical_miss(self, tmp_path):
        reg = _mini(tmp_path)
        store = StateStore(tmp_path / "t.db")
        store.insert_cron_heartbeat(
            job_name="job_a", executed_at="2026-06-15T09:00:00+05:30",
            status="SUCCESS", duration_sec=5.0, message=None,
        )
        store.insert_cron_heartbeat(
            job_name="job_b", executed_at="2026-06-15T09:05:00+05:30",
            status="FAILED", duration_sec=2.0, message="boom",
        )
        # job_c (critical) has NO heartbeat -> missed
        msg, critical_miss = build_eod_summary(reg, store, MON, tmp_path, time(18, 0))
        store.close()

        assert "Completed: 1/3" in msg
        assert "Failed: 1 (job_b)" in msg
        assert "Missed: 1 (job_c)" in msg
        assert "0m 7s" in msg  # 5.0 + 2.0
        assert critical_miss is True

    def test_no_miss_no_critical_flag(self, tmp_path):
        reg = _mini(tmp_path)
        store = StateStore(tmp_path / "t.db")
        for j, t in [("job_a", "09:00:00"), ("job_b", "09:05:00"), ("job_c", "09:10:00")]:
            store.insert_cron_heartbeat(
                job_name=j, executed_at=f"2026-06-15T{t}+05:30",
                status="SUCCESS", duration_sec=1.0, message=None,
            )
        msg, critical_miss = build_eod_summary(reg, store, MON, tmp_path, time(18, 0))
        store.close()
        assert "Completed: 3/3" in msg
        assert critical_miss is False


# ── miss-grace debounce (24-Jun: 09:20 heartbeat-commit race) ────────────────


class TestMissGraceDebounce:
    """A heartbeat job due within `grace_minutes` of the snapshot may have JUST
    fired and not yet committed its heartbeat (the 09:20 cron-cluster race) ->
    PENDING, not a false MISSED -> no false CRITICAL. A genuine miss (due > grace
    ago) STILL escalates, so real detection is preserved."""

    def _job_c(self, tmp_path):
        reg = _mini(tmp_path)  # job_c due 09:10, monitored heartbeat_db, critical
        return next(j for j in reg.jobs_due_on(MON, tmp_path) if j.name == "job_c")

    def test_just_due_no_heartbeat_within_grace_is_pending(self, tmp_path):
        # snapshot AT the due time, heartbeat not yet committed -> PENDING (debounce)
        out = _classify_job(self._job_c(tmp_path), MON, time(9, 10), {}, tmp_path,
                            grace_minutes=2)
        assert out.status == PENDING

    def test_just_due_without_grace_is_missed(self, tmp_path):
        # grace=0 reproduces the pre-fix behaviour (the false MISSED at the boundary)
        out = _classify_job(self._job_c(tmp_path), MON, time(9, 10), {}, tmp_path,
                            grace_minutes=0)
        assert out.status == MISSED

    def test_genuine_miss_past_grace_still_missed(self, tmp_path):
        # 09:20 snapshot, due 09:10 -> 10 min overdue > 2 min grace -> MISSED
        out = _classify_job(self._job_c(tmp_path), MON, time(9, 20), {}, tmp_path,
                            grace_minutes=2)
        assert out.status == MISSED

    def test_committed_heartbeat_is_completed_regardless_of_grace(self, tmp_path):
        hb = {"job_c": {"status": "SUCCESS", "duration_sec": 1.0}}
        out = _classify_job(self._job_c(tmp_path), MON, time(9, 10), hb, tmp_path,
                            grace_minutes=2)
        assert out.status == COMPLETED

    def test_within_grace_does_not_escalate_but_real_miss_does(self, tmp_path):
        job_c = self._job_c(tmp_path)
        race = _classify_job(job_c, MON, time(9, 10), {}, tmp_path, grace_minutes=2)
        assert _compute_severity([race], watcher_stale=False) == "INFO"   # no false CRITICAL
        real = _classify_job(job_c, MON, time(9, 20), {}, tmp_path, grace_minutes=2)
        assert _compute_severity([real], watcher_stale=False) == "CRITICAL"  # real miss flags

    def test_registry_default_grace_is_two_minutes(self, tmp_path):
        # the officer-config default (consumed by build_report) is 2 min.
        assert _mini(tmp_path).officer.miss_grace_minutes == 2


# ── crontab parsing + change detection ───────────────────────────────────────


class TestParsing:
    def test_cron_hhmm(self):
        assert _cron_hhmm("0", "8") == "08:00"
        assert _cron_hhmm("30", "2") == "02:30"
        assert _cron_hhmm("*/5", "9") is None
        assert _cron_hhmm("0", "*") is None

    def test_job_token(self):
        assert _job_token("scripts/auto_refresh_token.py") == "auto_refresh_token"
        assert _job_token("python -m reports.daily_report") == "reports.daily_report"
        assert _job_token("find logs -name '*.log' -delete") is None

    def test_parse_crontab(self):
        text = (
            "# comment\n"
            "0 9 * * 1-5 cd /x && python scripts/job_a.py >> log\n"
            "*/5 9-15 * * 1-5 cd /x && python scripts/capture.py\n"
        )
        rows = parse_crontab(text)
        assert len(rows) == 2
        assert rows[0]["hhmm"] == "09:00" and rows[0]["token"] == "job_a"
        assert rows[1]["hhmm"] is None and rows[1]["token"] == "capture"


class TestChangeReport:
    def test_detects_added_missing(self, tmp_path):
        reg = _mini(tmp_path)
        crontab = (
            "0 9 * * 1-5 cd /x && python scripts/job_a.py\n"
            "5 9 * * 1-5 cd /x && python scripts/job_b.py\n"
            "0 10 * * 1-5 cd /x && python scripts/unknown_job.py\n"
        )
        msg, has_diff = build_change_report(reg, crontab)
        assert has_diff is True
        assert "job_c" in msg        # in registry, not crontab
        assert "unknown_job" in msg  # in crontab, not registry

    def test_detects_time_change(self, tmp_path):
        reg = _mini(tmp_path)
        crontab = (
            "0 8 * * 1-5 cd /x && python scripts/job_a.py\n"   # 08:00 vs registry 09:00
            "5 9 * * 1-5 cd /x && python scripts/job_b.py\n"
            "10 9 * * 1-5 cd /x && python scripts/job_c.py\n"
        )
        msg, has_diff = build_change_report(reg, crontab)
        assert has_diff is True
        assert "08:00→09:00" in msg

    def test_no_diff(self, tmp_path):
        reg = _mini(tmp_path)
        crontab = (
            "0 9 * * 1-5 python scripts/job_a.py\n"
            "5 9 * * 1-5 python scripts/job_b.py\n"
            "10 9 * * 1-5 python scripts/job_c.py\n"
        )
        msg, has_diff = build_change_report(reg, crontab)
        assert has_diff is False
        assert "matches live crontab" in msg
