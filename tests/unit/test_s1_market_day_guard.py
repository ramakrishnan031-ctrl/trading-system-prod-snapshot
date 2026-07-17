"""tests/unit/test_s1_market_day_guard.py — S1: central market_day_only enforcement.

S1 (AB-910 §2.2): `market_day_only` was DECORATIVE — declared on 30 registry jobs
and on the CronJob model (core/cron_registry.py:75), but read by NO code. The
registry's `cadence: market_day` only tells the Cron Officer not to EXPECT a
heartbeat on a holiday (core/cron_registry.py:257 is_due_on); cron still FIRED the
job. So ~14 jobs did real work on every NSE holiday — Zerodha/Gemini API calls,
junk artifacts — pointlessly, though harmlessly.

The guard itself already existed (`utils.cron_heartbeat.skip_if_non_trading_day`,
"TASK #3 Layer 6") and was already used by 16 jobs. S1 is therefore NOT new
calendar logic — it is calling the existing, already-tested central guard from the
entry points that never called it. Reusing it is the point: one calendar source,
one skip semantic, one failure direction.

*** THE SAFETY PROPERTY THIS FILE EXISTS TO PROVE (addendum §1) ***
A NORMAL TRADING DAY MUST NEVER SUPPRESS auto_refresh_token.

The asymmetry that drives every decision here:
  - skipping a real NSE holiday   -> saves a pointless refresh (trivial upside)
  - wrongly skipping a TRADING day -> no token -> the token-watcher never starts
                                      the app -> THE SYSTEM CANNOT TRADE AT ALL.
So the guard FAILS OPEN: on any calendar error it degrades to a weekday check and
the job RUNS. A missing/corrupt nse_holidays_<year>.yaml can never starve the token.

Heartbeat semantics (why this cannot create Cron-Officer noise): the guard records
status="SKIPPED", which cron_officer's `elif status == "SKIPPED"` catches BEFORE
the functional-status check — so a holiday skip is counted under "⏭ Skipped" and
can never be mistaken for a functional issue. (A job exiting SUCCESS with
functional_status="SKIPPED" WOULD be flagged, since the benign set is
("OK","SUCCESS","DELIVERED") — that is the S5 question, deliberately not this one.)
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.cron_heartbeat import skip_if_non_trading_day
from utils.holiday_guard import is_trading_day

_NO_DB = Path("data_store/__s1_nonexistent__.db")   # no heartbeat write in tests
_CONFIG = Path(__file__).parent.parent.parent / "config"

# Real dates from config/nse_holidays_2026.yaml (NSE Circular NSE/CMTR/71775).
_REAL_HOLIDAY = date(2026, 10, 2)        # Mahatma Gandhi Jayanti (Fri) — listed
_REAL_TRADING_DAY = date(2026, 7, 17)    # Friday — deliberately NOT a listed holiday
_SATURDAY = date(2026, 7, 18)


# ─────────────────────────────────────────────────────────────────────────────
# THE safety property (addendum §1)
# ─────────────────────────────────────────────────────────────────────────────
def test_auto_refresh_token_RUNS_on_a_normal_trading_day():
    """*** THE critical S1 property. *** On a trading day the guard must let
    auto_refresh_token RUN. If this ever fails, the token is not refreshed, the
    token-watcher never starts the app, and the system cannot trade."""
    with patch("utils.holiday_guard.is_trading_day", return_value=True):
        assert skip_if_non_trading_day("auto_refresh_token", db_path=_NO_DB) is False


def test_auto_refresh_token_runs_on_a_real_trading_day_via_the_REAL_calendar():
    """The same property against the REAL config/nse_holidays_2026.yaml — no mock.

    Proves the shipped calendar itself does not misread an ordinary trading day
    as a holiday (a mocked-only test could not catch a bad calendar file)."""
    assert is_trading_day(_REAL_TRADING_DAY, _CONFIG) is True


def test_auto_refresh_token_RUNS_when_the_calendar_is_UNREADABLE():
    """FAIL-OPEN. A missing/corrupt holiday YAML must never starve the token.

    is_trading_day() RAISES FileNotFoundError when nse_holidays_<year>.yaml is
    absent; the guard catches that and degrades to a weekday check, so a weekday
    still RUNS. This is the failure mode that would otherwise silently kill a
    trading session (e.g. 01-Jan-2027 with no nse_holidays_2027.yaml yet)."""
    with patch("utils.holiday_guard.is_trading_day",
               side_effect=FileNotFoundError("nse_holidays_2027.yaml missing")):
        with patch("core.time_authority.now_ist") as now:
            now.return_value.weekday.return_value = 0        # Monday
            now.return_value.date.return_value = _REAL_TRADING_DAY
            assert skip_if_non_trading_day("auto_refresh_token", db_path=_NO_DB) is False


def test_auto_refresh_token_is_skipped_ONLY_on_a_real_holiday():
    """The other half: a genuine NSE holiday DOES skip (that is the whole point)."""
    with patch("utils.holiday_guard.is_trading_day", return_value=False):
        assert skip_if_non_trading_day("auto_refresh_token", db_path=_NO_DB) is True


def test_the_real_calendar_marks_a_listed_holiday_as_non_trading():
    """The shipped calendar correctly identifies a listed NSE holiday."""
    assert is_trading_day(_REAL_HOLIDAY, _CONFIG) is False
    assert is_trading_day(_SATURDAY, _CONFIG) is False


# ─────────────────────────────────────────────────────────────────────────────
# The wiring — _cron_main guards, main() does not (manual runs stay possible)
# ─────────────────────────────────────────────────────────────────────────────
def test_cron_entry_skips_on_holiday_without_calling_main():
    """RED-on-old: before S1, __main__ called main() directly with no guard, so
    the refresh ran on every holiday. _cron_main must skip WITHOUT calling main()."""
    import scripts.auto_refresh_token as art

    with patch.object(art, "main") as real_main:
        with patch("utils.cron_heartbeat.skip_if_non_trading_day", return_value=True):
            assert art._cron_main([]) == 0
    real_main.assert_not_called()


def test_cron_entry_calls_main_on_a_trading_day():
    """The safety property at the WIRING level: on a trading day _cron_main must
    actually invoke main(). A guard that skipped here would starve the token."""
    import scripts.auto_refresh_token as art

    with patch.object(art, "main", return_value=0) as real_main:
        with patch("utils.cron_heartbeat.skip_if_non_trading_day", return_value=False):
            assert art._cron_main([]) == 0
    real_main.assert_called_once()


def test_main_itself_is_NOT_holiday_guarded():
    """The guard belongs at the CRON entry, not in main(): a manual recovery run
    (e.g. re-issuing a token on a weekend) must never be blocked. Mirrors the
    established eod_cleanup/_cron_main split."""
    import inspect
    import scripts.auto_refresh_token as art

    src = inspect.getsource(art.main)
    assert "skip_if_non_trading_day" not in src, \
        "main() must stay manually invocable on a non-trading day"


# ─────────────────────────────────────────────────────────────────────────────
# Registry consistency — the declared intent this guard now honours
# ─────────────────────────────────────────────────────────────────────────────
def test_every_market_day_only_job_is_actually_guarded():
    """S1's completeness pin — and the anti-decay guard.

    `market_day_only` decayed into decoration precisely because nothing ever
    checked that the declaration was honoured. This asserts the invariant
    directly: EVERY job declaring market_day_only resolves to a script that
    actually calls a holiday guard. A new market_day_only job added without one
    fails HERE rather than silently running on 15 NSE holidays a year.

    Was 16/30 guarded before S1 (14 unguarded, incl. the critical
    auto_refresh_token / eod_verify / eod_broker_reconcile).
    """
    import re
    import yaml

    reg = yaml.safe_load((_CONFIG / "cron_registry.yaml").read_text(encoding="utf-8"))
    jobs = reg.get("jobs") or reg
    items = ([dict(name=k, **(v or {})) for k, v in jobs.items()]
             if isinstance(jobs, dict) else jobs)
    root = _CONFIG.parent
    guard_re = re.compile(r"skip_if_non_trading_day|is_holiday_or_weekend|is_trading_day")

    unguarded = []
    for j in items:
        if j.get("market_day_only") is not True:
            continue
        cmd = str(j.get("command") or "")
        m = re.search(r"([\w/\.]+\.py)", cmd) or re.search(r"-m\s+([\w\.]+)", cmd)
        if not m:
            continue
        tok = m.group(1)
        path = root / (tok if tok.endswith(".py") else tok.replace(".", "/") + ".py")
        if not path.exists():
            continue
        if not guard_re.search(path.read_text(encoding="utf-8", errors="ignore")):
            unguarded.append(j["name"])

    assert unguarded == [], (
        f"market_day_only declared but NOT enforced at the cron entry: {unguarded}. "
        "Add `if skip_if_non_trading_day('<job>'): return 0` to the script's "
        "_cron_main (never to main(), which must stay manually invocable)."
    )


def test_auto_refresh_token_declares_market_day_intent():
    """The guard must match the registry's declared intent, not diverge from it."""
    import yaml

    reg = yaml.safe_load((_CONFIG / "cron_registry.yaml").read_text(encoding="utf-8"))
    jobs = reg.get("jobs") or reg
    job = jobs["auto_refresh_token"] if isinstance(jobs, dict) else \
        next(j for j in jobs if j["name"] == "auto_refresh_token")
    assert job.get("market_day_only") is True
    assert job.get("cadence") == "market_day"
