"""Tests for core/cron_registry.py (TASK #3 / Cron Officer registry)."""
from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from core.cron_registry import CronJob, CronRegistry, load_cron_registry
from core.exceptions import ConfigMissingError, ConfigSchemaError

_REAL = Path("config/cron_registry.yaml")

# Known 2026 weekdays (2026-06-18 is a Thursday)
MON = date(2026, 6, 15)
SAT = date(2026, 6, 20)
SUN = date(2026, 6, 21)
FIRST = date(2026, 6, 1)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "cron_registry.yaml"
    p.write_text(body, encoding="utf-8")
    return p


# ── Phase 3 regression: personal_tooling must NOT define EOD timing ──────────

def test_eod_report_time_excludes_personal_tooling(tmp_path):
    """A late personal_tooling job (e.g. a 20:33 Claude heartbeat) must NOT push
    back the trading EOD report time — a heartbeat is not a trading deliverable.
    Without the last_due_time() exclusion the 22:00 heartbeat below forces EOD to
    22:05; with it, EOD floors to 18:45 off the real 16:00 job."""
    reg = load_cron_registry(_write(tmp_path, """
jobs:
  real_eod_job:
    script: x.py
    schedule: "16:00 daily"
    type: python
    cadence: daily
  late_heartbeat:
    script: hb
    schedule: "22:00 daily"
    type: shell
    cadence: daily
    personal_tooling: true
officer:
  eod_floor_time: "18:45"
  eod_gap_minutes: 5
"""))
    assert reg.last_due_time(MON, tmp_path) == time(16, 0), "personal job must be ignored"
    assert reg.eod_report_time(MON, tmp_path) == time(18, 45), "EOD must floor, not wait for 22:00 heartbeat"

    # control: the SAME late job WITHOUT personal_tooling DOES define EOD time
    reg2 = load_cron_registry(_write(tmp_path, """
jobs:
  real_eod_job:
    script: x.py
    schedule: "16:00 daily"
    type: python
    cadence: daily
  late_real_job:
    script: y.py
    schedule: "22:00 daily"
    type: python
    cadence: daily
officer:
  eod_floor_time: "18:45"
  eod_gap_minutes: 5
"""))
    assert reg2.eod_report_time(MON, tmp_path) == time(22, 5), \
        "a non-personal late job SHOULD push EOD (proves exclusion is personal_tooling-specific)"


# ── Real registry smoke ─────────────────────────────────────────────────────


class TestRealRegistry:
    def test_loads_and_validates(self):
        reg = load_cron_registry(_REAL)
        assert reg.count() >= 28
        # 08:15 shift landed
        assert reg.get("auto_refresh_token").schedule.startswith("08:15")
        assert reg.get("auto_refresh_token").critical is True
        # officer jobs present
        assert "cron_officer_briefing" in {j.name for j in reg.all_jobs()}
        assert "cron_officer_eod" in {j.name for j in reg.all_jobs()}
        # the previously-missing data-safety jobs are now registered
        assert "analytics_backup" in {j.name for j in reg.all_jobs()}
        assert "db_retention" in {j.name for j in reg.all_jobs()}

    def test_every_job_has_name_injected(self):
        reg = load_cron_registry(_REAL)
        assert all(j.name for j in reg.all_jobs())

    def test_critical_jobs_subset(self):
        reg = load_cron_registry(_REAL)
        crit = {j.name for j in reg.critical_jobs()}
        assert "auto_refresh_token" in crit and "reconcile_positions" in crit
        assert "fetch_fno_ban" not in crit


# ── FIX-189 (P0-A): generated crontab must run under bash + `. ./.env` ───────
# cron's default /bin/sh is dash; dash's `.` builtin will NOT source a relative
# path without a slash, so a bare `. .env` fails ("sh: .: .env: not found") and
# EVERY job dies before Python (no token refresh, no heartbeats, no logs).


class TestFix189CronShell:
    CRON = Path("deploy/cron/trading-system.cron")

    def _job_lines(self):
        for ln in self.CRON.read_text(encoding="utf-8").splitlines():
            if ln.lstrip().startswith("#") or not ln.strip():
                continue
            yield ln

    def test_shell_is_bash(self):
        assert any(ln.strip() == "SHELL=/bin/bash" for ln in self._job_lines()), \
            "canonical crontab must declare SHELL=/bin/bash"

    def test_no_bare_dot_env_sourcing(self):
        offenders = [ln for ln in self._job_lines() if "&& . .env &&" in ln]
        assert offenders == [], (
            "dash-unsafe bare `. .env` in crontab job(s): " + "; ".join(offenders)
        )

    def test_env_sourced_with_slash(self):
        text = self.CRON.read_text(encoding="utf-8")
        assert ". ./.env" in text, "jobs must source via `. ./.env` (slash form)"


# ── ENV-EXPORT FIX: cron must export .env vars to the child Python ───────────
# `.env` uses bare `VAR=value` (no `export`), so a plain `. ./.env` sets shell
# vars the child `python` does NOT inherit — every cron job ran without its
# .env secrets (reconcile_positions broker-creds + Telegram both failed). The
# source must be wrapped in `set -a` (allexport) ... `set +a`.


class TestCronEnvExport:
    CRON = Path("deploy/cron/trading-system.cron")

    def _env_sourcing_lines(self):
        for ln in self.CRON.read_text(encoding="utf-8").splitlines():
            if ln.lstrip().startswith("#") or not ln.strip():
                continue
            if ". ./.env" in ln:
                yield ln

    def test_every_sourcing_line_uses_allexport(self):
        offenders = [
            ln for ln in self._env_sourcing_lines()
            if "set -a && . ./.env && set +a" not in ln
        ]
        assert offenders == [], (
            "env-sourcing job(s) missing `set -a && . ./.env && set +a` allexport "
            "wrapper (vars won't reach Python): " + "; ".join(offenders)
        )

    def test_no_unwrapped_plain_source(self):
        # A plain `&& . ./.env &&` not preceded by `set -a` would silently drop
        # secrets. Every occurrence must be the wrapped form.
        for ln in self._env_sourcing_lines():
            assert "set -a && . ./.env && set +a &&" in ln, (
                f"unwrapped .env source (no allexport): {ln}"
            )


# ── due_time parsing ─────────────────────────────────────────────────────────


class TestDueTime:
    def test_parses_leading_hhmm(self):
        assert CronJob(name="x", script="s", schedule="08:15 Mon-Fri", type="python",
                       cadence="market_day").due_time == time(8, 15)

    def test_none_for_intraday_and_hourly(self):
        assert CronJob(name="x", script="s", schedule="*/5 09-15 Mon-Fri", type="python",
                       cadence="intraday").due_time is None
        assert CronJob(name="x", script="s", schedule="hourly", type="python",
                       cadence="hourly").due_time is None


# ── is_due_on (cadence + holiday) ───────────────────────────────────────────


class TestIsDueOn:
    def _reg(self):
        return load_cron_registry(_REAL)

    def test_daily_always_due(self, tmp_path):
        reg = self._reg()
        j = reg.get("log_cleanup")
        assert reg.is_due_on(j, MON, tmp_path) and reg.is_due_on(j, SAT, tmp_path)

    def test_market_day_skips_weekend(self, tmp_path):
        reg = self._reg()
        j = reg.get("eod_verify")
        assert reg.is_due_on(j, MON, tmp_path) is True   # empty cfg -> weekday fallback
        assert reg.is_due_on(j, SAT, tmp_path) is False
        assert reg.is_due_on(j, SUN, tmp_path) is False

    def test_market_day_skips_nse_holiday(self, tmp_path):
        # tmp holiday calendar marking MON as a holiday (quoted -> parsed as str,
        # matching the supported nse_holidays_<year>.yaml entry format)
        (tmp_path / "nse_holidays_2026.yaml").write_text(
            'holidays:\n  - "2026-06-15"\n', encoding="utf-8"
        )
        reg = self._reg()
        assert reg.is_due_on(reg.get("eod_verify"), MON, tmp_path) is False

    def test_weekly_only_on_weekday(self, tmp_path):
        reg = self._reg()
        j = reg.get("gemini_weekly_patterns")
        assert reg.is_due_on(j, SUN, tmp_path) is True
        assert reg.is_due_on(j, MON, tmp_path) is False

    def test_monthly_only_on_first(self, tmp_path):
        reg = self._reg()
        j = reg.get("backup_restore_drill")
        assert reg.is_due_on(j, FIRST, tmp_path) is True
        assert reg.is_due_on(j, MON, tmp_path) is False


# ── expected_heartbeat_jobs ─────────────────────────────────────────────────


class TestExpectedHeartbeats:
    def test_excludes_shell_and_unmonitored(self, tmp_path):
        reg = load_cron_registry(_REAL)
        names = {j.name for j in reg.expected_heartbeat_jobs(MON, tmp_path)}
        assert "log_cleanup" not in names      # shell
        assert "db_backup" not in names        # shell
        assert "check_cron_drift" not in names  # monitored=false
        assert "capture_metrics" not in names  # monitored=false (intraday)
        assert "eod_verify" in names           # monitored market_day

    def test_before_time_excludes_later_jobs(self, tmp_path):
        reg = load_cron_registry(_REAL)
        names = {j.name for j in reg.expected_heartbeat_jobs(MON, tmp_path, before_time=time(9, 0))}
        assert "auto_refresh_token" in names   # 08:15 <= 09:00
        assert "eod_verify" not in names       # 15:55 > 09:00

    def test_eod_officer_excluded_at_1800_drift_check(self, tmp_path):
        reg = load_cron_registry(_REAL)
        names = {j.name for j in reg.expected_heartbeat_jobs(MON, tmp_path, before_time=time(18, 0))}
        assert "cron_officer_eod" not in names  # 18:50 > 18:00
        assert "gemini_data_integrity_check" in names  # 17:00 <= 18:00 (Bug A: key renamed)

    def test_phase_c_cutover_expectations(self, tmp_path):
        """Phase C: the redesigned daily_trade_review (monitored) IS expected; the retired
        daily_review (DELETED from the registry entirely) is NOT — so the Officer raises no
        false MISSED for it; daily_report stays expected during the bake-in."""
        reg = load_cron_registry(_REAL)
        names = {j.name for j in reg.expected_heartbeat_jobs(MON, tmp_path)}
        assert "daily_trade_review" in names       # new monitored report (16:07)
        assert "daily_review" not in names         # retired — deleted from the registry
        assert "daily_report" in names             # kept in parallel during bake-in


# ── validation ───────────────────────────────────────────────────────────────


class TestValidation:
    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigMissingError):
            CronRegistry.load(tmp_path / "nope.yaml")

    def test_extra_field_forbidden(self, tmp_path):
        p = _write(tmp_path, "jobs:\n  x:\n    script: s\n    schedule: '08:00 daily'\n"
                             "    type: python\n    cadence: daily\n    bogus: 1\n")
        with pytest.raises(ConfigSchemaError):
            CronRegistry.load(p)

    def test_bad_type_rejected(self, tmp_path):
        p = _write(tmp_path, "jobs:\n  x:\n    script: s\n    schedule: '08:00 daily'\n"
                             "    type: ruby\n    cadence: daily\n")
        with pytest.raises(ConfigSchemaError):
            CronRegistry.load(p)

    def test_weekly_requires_weekday(self, tmp_path):
        p = _write(tmp_path, "jobs:\n  x:\n    script: s\n    schedule: '18:00 Sunday'\n"
                             "    type: python\n    cadence: weekly\n")
        with pytest.raises(ConfigSchemaError):
            CronRegistry.load(p)

    def test_monthly_requires_day_of_month(self, tmp_path):
        p = _write(tmp_path, "jobs:\n  x:\n    script: s\n    schedule: '03:00 1st'\n"
                             "    type: python\n    cadence: monthly\n")
        with pytest.raises(ConfigSchemaError):
            CronRegistry.load(p)
