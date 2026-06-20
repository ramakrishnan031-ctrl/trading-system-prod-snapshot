"""Tests for Cron Officer security-watcher supervision (Phase 3)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from scripts.cron_officer import security_watcher_health

_IST = timezone(timedelta(hours=5, minutes=30))


def _state_file(root, age_sec):
    ds = root / "data_store"
    ds.mkdir(parents=True, exist_ok=True)
    f = ds / "security_state.json"
    f.write_text("{}", encoding="utf-8")
    epoch = datetime.now().timestamp() - age_sec
    os.utime(f, (epoch, epoch))


def test_watcher_alive_when_state_fresh(tmp_path):
    _state_file(tmp_path, age_sec=30)
    line, stale = security_watcher_health(tmp_path, datetime.now(_IST))
    assert stale is False and "alive" in line


def test_watcher_stale_when_state_old(tmp_path):
    _state_file(tmp_path, age_sec=3600)
    line, stale = security_watcher_health(tmp_path, datetime.now(_IST))
    assert stale is True and "STALE" in line


def test_watcher_missing_state_file(tmp_path):
    line, stale = security_watcher_health(tmp_path, datetime.now(_IST))
    assert stale is True and "MISSING" in line
