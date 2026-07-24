"""LIVENESS ALARM (17-Jul-2026): trading-system.service must never be able to die
silently during the hours it has to be up.

The whole module is new, so every import here fails on the pre-fix tree (fail-on-old);
the case that matters most is test_fires_on_the_17jul_outage, which reconstructs the exact
systemd state of today's silent death and asserts the alarm fires — and asserts the OLD
canary would NOT have caught it.

THE S4 LESSON APPLIED: the probe must really READ service state. The matrix tests inject a
runner for determinism, so test_default_runner_shells_out_to_systemctl pins that the
DEFAULT (production) path genuinely shells out to `systemctl show trading-system.service`.
Without that, a fixture that quietly assumes "active" would make every test below green
while the probe is blind — which is exactly how S4 shipped.
"""
from __future__ import annotations

import json
from datetime import datetime, time as _time_cls, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.liveness_probe import (
    _LIVENESS_END,
    _LIVENESS_START,
    classify_liveness,
    format_alarm,
    is_trading_day_safe,
    probe_liveness,
    within_liveness_window,
)

_IST = timezone(timedelta(hours=5, minutes=30))

# The real systemd state of the 17-Jul outage, verbatim from `systemctl show` on the VM.
# Note Result=success / ExecMainStatus=0: it shut ITSELF down cleanly. That is why
# NRestarts stayed 0 and every existing monitor read green.
_DEAD_17JUL = (
    "ActiveState=inactive\n"
    "SubState=dead\n"
    "Result=success\n"
    "ExecMainStatus=0\n"
    "InactiveEnterTimestamp=Fri 2026-07-17 08:16:09 IST\n"
)
_ALIVE = (
    "ActiveState=active\n"
    "SubState=running\n"
    "Result=success\n"
    "ExecMainStatus=0\n"
    "InactiveEnterTimestamp=\n"
)

_IN_WINDOW = datetime(2026, 7, 17, 9, 0, tzinfo=_IST)      # Friday, a trading day


def _probe(tmp_path, *, out=_DEAD_17JUL, now=_IN_WINDOW, trading=True, kill="INACTIVE",
           monkeypatch=None):
    """Drive the real probe with injected I/O. Calendar + kill-switch are patched at their
    module boundary so the probe's own decision logic is exercised, not bypassed."""
    monkeypatch.setattr("scripts.liveness_probe.is_trading_day_safe", lambda *a, **k: trading)
    monkeypatch.setattr("scripts.liveness_probe.read_kill_state", lambda *a, **k: kill)
    return probe_liveness(runner=lambda: out, state_path=tmp_path / "state.json", now=now)


# ── FIRES: the case that actually happened and was missed ────────────────────

def test_fires_on_the_17jul_outage(tmp_path, monkeypatch):
    """THE regression: inactive + trading day + in-window + no kill marker -> ALARM.

    This is 17-Jul reconstructed exactly. It went undetected all session: the canary's
    respawn probe watches alert-watcher.service and its dashboard probe watches
    gui-dashboard — NOTHING watched this unit."""
    should_alarm, code, detail, props = _probe(tmp_path, monkeypatch=monkeypatch)
    assert should_alarm is True, f"the 17-Jul silent death must alarm, got {code}: {detail}"
    assert code == "DOWN"
    assert "08:16:09" in detail

    # The message must carry the smoking gun: a CLEAN exit 0 = shut itself down, not crashed.
    msg = format_alarm(props, props["InactiveEnterTimestamp"], _IN_WINDOW)
    assert "DOWN during the service window" in msg
    assert "08:16:09" in msg
    assert "Result=success" in msg and "ExecMainStatus=0" in msg


def test_old_canary_would_not_have_caught_it():
    """Red-on-old in spirit: the pre-existing canary CANNOT see this unit. Its respawn
    probe defaults to alert-watcher.service and its dashboard probe to gui-dashboard, so a
    dead trading-system scores healthy on every canary path. Pins the gap so a refactor
    cannot quietly re-open it."""
    import inspect

    from scripts.monitoring_canary import check_dashboard, check_service_respawn, run_canary

    assert inspect.signature(check_service_respawn).parameters["unit"].default == "alert-watcher.service"
    assert inspect.signature(check_dashboard).parameters["unit"].default == "gui-dashboard"
    assert "trading-system" not in inspect.getsource(run_canary), \
        "if the canary now watches trading-system, this probe's rationale needs revisiting"


# ── SILENT: every legitimate down-state ─────────────────────────────────────

def test_silent_on_nse_holiday(tmp_path, monkeypatch):
    """Holiday + inactive + in-window -> NO alarm (authoritative calendar, S1)."""
    should_alarm, code, _, _ = _probe(tmp_path, trading=False, monkeypatch=monkeypatch)
    assert should_alarm is False
    assert code == "NON_TRADING_DAY"


@pytest.mark.parametrize("kill", ["SOFT_KILL", "HARD_KILL"])
def test_silent_when_operator_parked_it(tmp_path, monkeypatch, kill):
    """The 16-Jul planned pause ("no trading issue", by=operator) must not alarm."""
    should_alarm, code, _, _ = _probe(tmp_path, kill=kill, monkeypatch=monkeypatch)
    assert should_alarm is False
    assert code == "OPERATOR_HALT"


@pytest.mark.parametrize("when,label", [
    (datetime(2026, 7, 17, 8, 16, tzinfo=_IST), "pre-09:00 (the 08:15 boot chain is still running)"),
    (datetime(2026, 7, 17, 8, 59, tzinfo=_IST), "one minute before the window opens"),
    (datetime(2026, 7, 17, 16, 0, tzinfo=_IST), "16:00 — the EOD self-exit is legitimate"),
    (datetime(2026, 7, 17, 18, 30, tzinfo=_IST), "evening, long since exited"),
])
def test_silent_outside_the_window(tmp_path, monkeypatch, when, label):
    """Inactive outside [09:00,16:00) is EXPECTED, never an alarm."""
    should_alarm, code, _, _ = _probe(tmp_path, now=when, monkeypatch=monkeypatch)
    assert should_alarm is False, f"must stay silent {label}"
    assert code == "OUTSIDE_WINDOW"


def test_silent_when_alive(tmp_path, monkeypatch):
    should_alarm, code, _, _ = _probe(tmp_path, out=_ALIVE, monkeypatch=monkeypatch)
    assert should_alarm is False
    assert code == "ALIVE"


# ── DEDUP: one alarm per incident, not one per probe ────────────────────────

def test_dedup_one_alarm_across_many_probes(tmp_path, monkeypatch):
    """The service stays down all session (~84 probes). Exactly ONE alarm."""
    fired = 0
    for i in range(12):
        now = _IN_WINDOW + timedelta(minutes=5 * i)
        should_alarm, code, _, _ = _probe(tmp_path, now=now, monkeypatch=monkeypatch)
        fired += int(should_alarm)
    assert fired == 1, f"expected exactly ONE alarm across 12 probes, got {fired}"

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["alarmed_for"] == "Fri 2026-07-17 08:16:09 IST"


def test_a_second_distinct_death_alarms_again(tmp_path, monkeypatch):
    """Dedup keys on InactiveEnterTimestamp, so a NEW death is a NEW incident. Down ->
    alarm; recovers; dies again -> alarm again. Dedup must not mute a real second outage."""
    assert _probe(tmp_path, monkeypatch=monkeypatch)[0] is True
    assert _probe(tmp_path, out=_ALIVE, now=_IN_WINDOW + timedelta(minutes=5),
                  monkeypatch=monkeypatch)[0] is False

    second = _DEAD_17JUL.replace("08:16:09", "11:04:22")
    should_alarm, _, detail, _ = _probe(tmp_path, out=second,
                                        now=_IN_WINDOW + timedelta(minutes=125),
                                        monkeypatch=monkeypatch)
    assert should_alarm is True, "a distinct later death must alarm again"
    assert "11:04:22" in detail


def test_missing_timestamp_degrades_to_one_alarm_per_day(tmp_path, monkeypatch):
    """If systemd gives no InactiveEnterTimestamp the incident key falls back to the date —
    at most one alarm/day, never one every 5 minutes."""
    out = _DEAD_17JUL.replace("InactiveEnterTimestamp=Fri 2026-07-17 08:16:09 IST", "InactiveEnterTimestamp=")
    fired = sum(int(_probe(tmp_path, out=out, now=_IN_WINDOW + timedelta(minutes=5 * i),
                           monkeypatch=monkeypatch)[0]) for i in range(6))
    assert fired == 1
    assert json.loads((tmp_path / "state.json").read_text())["alarmed_for"] == "date:2026-07-17"


# ── The probe must really read service state (the S4 lesson) ────────────────

def test_default_runner_shells_out_to_systemctl(tmp_path, monkeypatch):
    """No injected runner -> the REAL default must shell out to systemctl FOR THIS UNIT.

    This is the test S4 didn't have. Every other test here injects a runner; if the default
    ever became an assume-healthy stub, they would all still pass while the probe is blind.
    Pinned at the real boundary (subprocess.run), asserting the argv and that the output is
    actually parsed."""
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return MagicMock(stdout=_DEAD_17JUL)

    monkeypatch.setattr("scripts.liveness_probe.subprocess.run", fake_run)
    monkeypatch.setattr("scripts.liveness_probe.is_trading_day_safe", lambda *a, **k: True)
    monkeypatch.setattr("scripts.liveness_probe.read_kill_state", lambda *a, **k: "INACTIVE")

    should_alarm, _, _, props = probe_liveness(state_path=tmp_path / "s.json", now=_IN_WINDOW)

    assert calls, "the default runner must actually invoke systemctl — not assume health"
    argv = calls[0]
    assert argv[:2] == ["systemctl", "show"]
    assert "trading-system.service" in argv, f"must probe the TRADING unit, got {argv}"
    assert any("ActiveState" in a for a in argv)
    # and the real output was genuinely parsed, not defaulted
    assert props["ActiveState"] == "inactive"
    assert should_alarm is True


def test_unreadable_systemctl_still_alarms(tmp_path, monkeypatch):
    """If systemctl itself fails during the window, do NOT go quiet — silence is the bug
    being fixed. Degrade to alarming with 'unknown' state."""
    def boom():
        raise OSError("systemctl missing")
    monkeypatch.setattr("scripts.liveness_probe.is_trading_day_safe", lambda *a, **k: True)
    monkeypatch.setattr("scripts.liveness_probe.read_kill_state", lambda *a, **k: "INACTIVE")
    should_alarm, code, _, _ = probe_liveness(runner=boom, state_path=tmp_path / "s.json",
                                              now=_IN_WINDOW)
    assert should_alarm is True
    assert code == "DOWN"


def test_unreadable_kill_switch_does_not_buy_silence(tmp_path, monkeypatch):
    """An unreadable kill_switch_state must NOT suppress the alarm — otherwise a corrupt
    DB could silence a real death."""
    should_alarm, code, _, _ = _probe(tmp_path, kill="UNKNOWN", monkeypatch=monkeypatch)
    assert should_alarm is True
    assert code == "DOWN"


# ── Window definition + drift guard ────────────────────────────────────────

def test_window_end_not_after_a_legitimate_exit():
    """DRIFT GUARD: the probe deliberately does NOT import main.py (it must not depend on
    the health of what it monitors), so the suite owns the duplication.

    ⚠️ 25-Jul-2026 — THE INVARIANT CHANGED FROM `==` TO `<=`, DELIBERATELY. Recorded here
    so the next reader does not "fix" it back.

    It used to assert `_LIVENESS_END == main.SERVICE_WINDOW_END`. That constant served two
    roles and has been split: the latest the service may START (main.SERVICE_START_CUTOFF)
    and when it STOPS (configured trading_hours.service_window_end). The probe's bound must
    track the STOP time — the moment at which a clean exit becomes legitimate.

    `<=` is the sound form of what this guard always meant: NEVER ALARM DURING A PERIOD
    WHEN A CLEAN EXIT IS ALREADY LEGITIMATE. Equality was only ever incidental to the two
    values being the same number.

    The probe may stop EARLIER than the exit (it does: 16:00 vs 17:35). That is not drift —
    it means the tail of the service window is unwatched. That gap is a recorded, triggered
    follow-up (see docs/audit/service_window_configurable_25jul2026.md §6), not a failure
    of this assertion.

    ⛔ DO NOT restore `==` by pushing _LIVENESS_END up to the configured stop time. This
    probe's cron is `*/5 09-15` (last run 15:55) and structurally cannot reach it; a probe
    advertising a window it never runs in would be worse than the gap it papers over.
    """
    from core.config_loader import load_all
    stop_s = load_all(Path("config")).system.trading_hours.service_window_end
    stop = _time_cls(*(int(p) for p in stop_s.split(":")))
    assert _LIVENESS_END <= stop, (
        f"liveness window end ({_LIVENESS_END}) is AFTER the configured service stop "
        f"({stop}) — the service may self-exit inside the alarm window (guaranteed daily "
        "false alarm)"
    )


def test_window_start_is_after_the_boot_chain():
    """The lower bound must NOT be main.SERVICE_WINDOW_START (08:00): the service only MAY
    start then (08:15 token cron -> ~08:30 premarket). Alarming at 08:00 would be a
    guaranteed daily false alarm."""
    import main
    assert _LIVENESS_START > main.SERVICE_WINDOW_START
    assert _LIVENESS_START >= main._time(8, 45), "must clear the 08:15/08:30 boot chain"
    assert _LIVENESS_START <= main._time(9, 15), "must catch a death before the market opens"


def test_within_liveness_window_boundaries():
    assert within_liveness_window(datetime(2026, 7, 17, 9, 0, tzinfo=_IST)) is True
    assert within_liveness_window(datetime(2026, 7, 17, 15, 59, tzinfo=_IST)) is True
    assert within_liveness_window(datetime(2026, 7, 17, 8, 59, tzinfo=_IST)) is False
    assert within_liveness_window(datetime(2026, 7, 17, 16, 0, tzinfo=_IST)) is False


def test_calendar_fails_open_to_weekday(monkeypatch):
    """S1 parity: a broken calendar degrades to a weekday check (so a weekday holiday with
    an unreadable calendar produces a false alarm, never a missed death)."""
    import utils.holiday_guard as hg
    monkeypatch.setattr(hg, "is_trading_day",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no yaml")))
    assert is_trading_day_safe(datetime(2026, 7, 17).date()) is True    # Friday
    assert is_trading_day_safe(datetime(2026, 7, 18).date()) is False   # Saturday


# ── The pure classifier ────────────────────────────────────────────────────

def test_classifier_precedence_holiday_beats_everything():
    should, code, _ = classify_liveness(
        active_state="inactive", now=_IN_WINDOW, trading_day=False,
        kill_state="INACTIVE", since="x", already_alarmed_for=None)
    assert (should, code) == (False, "NON_TRADING_DAY")
