"""
tests/unit/test_preflight_startup_hook.py -- on-demand pre-flight when a restart
missed the cron slot. Uses an injected launcher (no real subprocess).
"""
from __future__ import annotations

from datetime import datetime

from scripts.preflight import sentinel
from scripts.preflight import startup_hook as sh


def _now(h, m):
    return datetime(2026, 6, 22, h, m)  # a Monday


def _write(tmp_path, run_date="2026-06-22", a=sentinel.NOT_STARTED, b=sentinel.NOT_STARTED):
    p = tmp_path / "today.json"
    sentinel.write(sentinel.Sentinel(run_date=run_date, phase_a_status=a, phase_b_status=b), p)
    return p


def test_outside_window_no_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "_is_trading_day", lambda d, c: True)
    sp = _write(tmp_path)
    assert sh.run_on_demand_if_missed(now=_now(5, 0), launcher=lambda p: None, sentinel_path=sp) == []
    assert sh.run_on_demand_if_missed(now=_now(10, 0), launcher=lambda p: None, sentinel_path=sp) == []


def test_phase_a_missed_launches_a_only(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "_is_trading_day", lambda d, c: True)
    sp = _write(tmp_path)               # today, A not started
    rec = []
    out = sh.run_on_demand_if_missed(now=_now(8, 45), launcher=rec.append, sentinel_path=sp)
    assert out == ["A"] and rec == ["A"]   # 08:45 -> A past-due, B (09:14) not yet


def test_both_missed_launches_a_and_b(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "_is_trading_day", lambda d, c: True)
    sp = _write(tmp_path)
    out = sh.run_on_demand_if_missed(now=_now(9, 15), launcher=lambda p: None, sentinel_path=sp)
    assert out == ["A", "B"]


def test_already_run_no_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "_is_trading_day", lambda d, c: True)
    sp = _write(tmp_path, a=sentinel.PASSED)   # A already ran today
    out = sh.run_on_demand_if_missed(now=_now(8, 45), launcher=lambda p: None, sentinel_path=sp)
    assert out == []


def test_stale_sentinel_launches(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "_is_trading_day", lambda d, c: True)
    sp = _write(tmp_path, run_date="2026-06-19", a=sentinel.PASSED, b=sentinel.PASSED)  # yesterday
    out = sh.run_on_demand_if_missed(now=_now(9, 15), launcher=lambda p: None, sentinel_path=sp)
    assert out == ["A", "B"]               # stale -> today's run didn't happen


def test_holiday_no_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "_is_trading_day", lambda d, c: False)
    sp = _write(tmp_path)
    out = sh.run_on_demand_if_missed(now=_now(8, 45), launcher=lambda p: None, sentinel_path=sp)
    assert out == []
