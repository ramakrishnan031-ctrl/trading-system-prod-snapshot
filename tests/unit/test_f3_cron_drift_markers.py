"""F3 (15-Jul-2026): check_cron_drift must consult MARKERS for exit_code_file jobs.

Pre-fix, PASS 1 checked the cron_heartbeat table for EVERY monitored job — so the four
marker-detected jobs (preflight_phase_a/b/c, sr_detector_backfill, which write
cron_marks/<name>.done and never a heartbeat) always looked "missing", producing a
standing daily FALSE 'no heartbeat in 24h' warning even though they ran fine.

These tests FAIL on the pre-fix code (a marker job with a fresh marker was still flagged).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from core.cron_registry import CronRegistry
from core.state_store import StateStore
import scripts.check_cron_drift as ccd

# Wed 2026-07-15 16:00 IST (naive, per the test tz convention) — after sr_detector_backfill
# (15:58) and the preflight jobs (08:30/09:14/09:15), so all four are "due today".
_FIXED = datetime(2026, 7, 15, 16, 0)


def _reg() -> CronRegistry:
    return CronRegistry.load(Path("config") / "cron_registry.yaml")


def test_marker_job_with_fresh_marker_not_flagged(tmp_path):
    db = tmp_path / "t.db"
    StateStore(db_path=db).close()          # build schema; NO heartbeats seeded
    marks = tmp_path / "marks"
    marks.mkdir()
    # sr_detector_backfill (exit_code_file) ran today → a fresh rc-0 marker
    (marks / "sr_detector_backfill.done").write_text("0 2026-07-15T15:58:00+05:30")

    store = StateStore(db_path=db)
    try:
        with patch.object(ccd, "now_ist", return_value=_FIXED):
            missing = ccd.check_cron_drift(store, _reg(), Path("config"), marks_dir=marks)
    finally:
        store.close()

    # F3: NOT flagged — the marker proves it ran (pre-fix: flagged, no heartbeat).
    assert "sr_detector_backfill" not in missing
    # control: another marker job with NO marker written IS genuinely missing.
    assert "preflight_phase_a" in missing


def test_marker_job_without_marker_is_still_flagged(tmp_path):
    db = tmp_path / "t.db"
    StateStore(db_path=db).close()
    marks = tmp_path / "marks"
    marks.mkdir()                            # empty → no markers at all

    store = StateStore(db_path=db)
    try:
        with patch.object(ccd, "now_ist", return_value=_FIXED):
            missing = ccd.check_cron_drift(store, _reg(), Path("config"), marks_dir=marks)
    finally:
        store.close()

    # a marker job that genuinely did NOT run today is still flagged (no false NEGATIVE)
    assert "sr_detector_backfill" in missing
