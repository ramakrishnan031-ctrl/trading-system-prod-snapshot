"""
tests/unit/test_capture_metrics_baseline.py -- FIX-150

Tests for scripts/capture_metrics_baseline.py.

Coverage:
  - Snapshot capture stores all 7 metrics
  - Dry-run does not write to DB
  - Daily summary computation (avg/max/p95)
  - Summary with no snapshots returns None
  - Summary writes to system_metrics_daily
  - Drift detection triggers Telegram alert
  - Drift detection no-op when no prior history
  - main() happy paths (snapshot + summarize)
  - main() error path returns 1
  - CLI arg parsing
"""
from __future__ import annotations

import importlib.util
import sqlite3
from core import db_connect  # O6: ATTACH analytics.db
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module import
# ---------------------------------------------------------------------------

def _load_module():
    spec = importlib.util.spec_from_file_location(
        "capture_metrics_baseline",
        Path("scripts/capture_metrics_baseline.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_mod = _load_module()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Create a temp DB with system_metrics tables from schema."""
    from core.state_store import StateStore
    db = tmp_path / "test.db"
    StateStore(db)
    return str(db)


@pytest.fixture
def log():
    return MagicMock()


# ---------------------------------------------------------------------------
# Tests: capture_snapshot
# ---------------------------------------------------------------------------

class TestCaptureSnapshot:
    def test_stores_all_metrics(self, db_path, log):
        metrics = _mod.capture_snapshot(db_path, log, dry_run=False)
        assert "timestamp" in metrics
        assert "cpu_pct" in metrics
        assert "memory_mb" in metrics
        assert "db_size_mb" in metrics
        assert "log_size_mb" in metrics
        assert "open_fds" in metrics
        assert "thread_count" in metrics
        assert "disk_used_pct" in metrics

        conn = db_connect.connect(db_path)
        rows = conn.execute("SELECT COUNT(*) FROM system_metrics").fetchone()
        conn.close()
        assert rows[0] == 1

    def test_dry_run_no_db_write(self, db_path, log):
        metrics = _mod.capture_snapshot(db_path, log, dry_run=True)
        assert "timestamp" in metrics

        conn = db_connect.connect(db_path)
        rows = conn.execute("SELECT COUNT(*) FROM system_metrics").fetchone()
        conn.close()
        assert rows[0] == 0

    def test_db_size_includes_wal(self, tmp_path, db_path, log):
        wal_path = Path(db_path + "-wal")
        wal_path.write_bytes(b"\x00" * 1024)

        size = _mod._get_db_size_mb(db_path)
        assert size > 0

    def test_log_size_empty_dir(self, tmp_path):
        empty = tmp_path / "no_logs"
        empty.mkdir()
        assert _mod._get_log_size_mb(empty) == 0.0

    def test_log_size_nonexistent_dir(self, tmp_path):
        assert _mod._get_log_size_mb(tmp_path / "nonexistent") == 0.0

    def test_multiple_snapshots(self, db_path, log):
        _mod.capture_snapshot(db_path, log)
        _mod.capture_snapshot(db_path, log)
        _mod.capture_snapshot(db_path, log)

        conn = db_connect.connect(db_path)
        rows = conn.execute("SELECT COUNT(*) FROM system_metrics").fetchone()
        conn.close()
        assert rows[0] == 3


# ---------------------------------------------------------------------------
# Tests: compute_daily_summary
# ---------------------------------------------------------------------------

class TestDailySummary:
    def _insert_snapshots(self, db_path, date_iso, values):
        conn = db_connect.connect(db_path)
        for i, (cpu, mem, db, lg, fds, thr, disk) in enumerate(values):
            ts = f"{date_iso}T09:{i:02d}:00+05:30"
            conn.execute(
                """INSERT INTO system_metrics
                   (timestamp, cpu_pct, memory_mb, db_size_mb, log_size_mb,
                    open_fds, thread_count, disk_used_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts, cpu, mem, db, lg, fds, thr, disk),
            )
        conn.commit()
        conn.close()

    @patch.object(_mod, "today_ist", return_value="2026-06-03")
    def test_summary_returns_none_no_snapshots(self, mock_today, db_path, log):
        result = _mod.compute_daily_summary(db_path, log)
        assert result is None

    @patch.object(_mod, "today_ist", return_value="2026-06-03")
    def test_summary_computes_stats(self, mock_today, db_path, log):
        self._insert_snapshots(db_path, "2026-06-03", [
            (10.0, 100.0, 5.0, 2.0, 10, 8, 40.0),
            (20.0, 200.0, 5.5, 2.5, 12, 10, 42.0),
            (30.0, 150.0, 5.2, 2.1, 11, 9, 41.0),
        ])
        result = _mod.compute_daily_summary(db_path, log)
        assert result is not None
        assert result["cpu_pct"]["avg"] == pytest.approx(20.0, abs=0.1)
        assert result["cpu_pct"]["max_val"] == 30.0
        assert result["memory_mb"]["max_val"] == 200.0

    @patch.object(_mod, "today_ist", return_value="2026-06-03")
    def test_summary_writes_to_daily_table(self, mock_today, db_path, log):
        self._insert_snapshots(db_path, "2026-06-03", [
            (15.0, 120.0, 5.0, 2.0, 10, 8, 40.0),
        ])
        _mod.compute_daily_summary(db_path, log)

        conn = db_connect.connect(db_path)
        row = conn.execute(
            "SELECT date, snapshot_count, cpu_avg FROM system_metrics_daily WHERE date = '2026-06-03'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "2026-06-03"
        assert row[1] == 1
        assert row[2] == pytest.approx(15.0, abs=0.1)

    @patch.object(_mod, "today_ist", return_value="2026-06-03")
    def test_summary_dry_run_no_write(self, mock_today, db_path, log):
        self._insert_snapshots(db_path, "2026-06-03", [
            (15.0, 120.0, 5.0, 2.0, 10, 8, 40.0),
        ])
        result = _mod.compute_daily_summary(db_path, log, dry_run=True)
        assert result is not None

        conn = db_connect.connect(db_path)
        row = conn.execute("SELECT COUNT(*) FROM system_metrics_daily").fetchone()
        conn.close()
        assert row[0] == 0

    @patch.object(_mod, "today_ist", return_value="2026-06-03")
    def test_summary_replaces_existing_row(self, mock_today, db_path, log):
        self._insert_snapshots(db_path, "2026-06-03", [
            (10.0, 100.0, 5.0, 2.0, 10, 8, 40.0),
        ])
        _mod.compute_daily_summary(db_path, log)
        self._insert_snapshots(db_path, "2026-06-03", [
            (50.0, 300.0, 6.0, 3.0, 15, 12, 50.0),
        ])
        _mod.compute_daily_summary(db_path, log)

        conn = db_connect.connect(db_path)
        rows = conn.execute(
            "SELECT COUNT(*) FROM system_metrics_daily WHERE date = '2026-06-03'"
        ).fetchone()
        conn.close()
        assert rows[0] == 1


# ---------------------------------------------------------------------------
# Tests: drift detection
# ---------------------------------------------------------------------------

class TestDriftDetection:
    def _seed_history(self, db_path, date_iso, cpu_max, mem_max, disk_max, db_max):
        conn = db_connect.connect(db_path)
        conn.execute(
            """INSERT INTO system_metrics_daily
               (date, snapshot_count, cpu_avg, cpu_max, cpu_p95,
                memory_avg_mb, memory_max_mb, memory_p95_mb,
                db_size_mb, log_size_mb, thread_avg, thread_max,
                disk_used_avg_pct, disk_used_max_pct)
               VALUES (?, 10, ?, ?, ?, ?, ?, ?, ?, 1.0, 8.0, 10, ?, ?)""",
            (date_iso, cpu_max * 0.8, cpu_max, cpu_max * 0.9,
             mem_max * 0.8, mem_max, mem_max * 0.9,
             db_max, disk_max * 0.8, disk_max),
        )
        conn.commit()
        conn.close()

    def test_no_alert_when_no_history(self, db_path, log):
        summary = {
            "cpu_pct": {"max_val": 90.0},
            "memory_mb": {"max_val": 500.0},
            "disk_used_pct": {"max_val": 80.0},
            "db_size_mb": {"max_val": 10.0},
        }
        _mod._check_drift(db_path, "2026-06-03", summary, log)
        log.warning.assert_not_called()

    @patch.object(_mod, "_send_telegram")
    def test_alert_on_cpu_drift(self, mock_tg, db_path, log):
        self._seed_history(db_path, "2026-06-02", cpu_max=50.0, mem_max=200.0,
                           disk_max=60.0, db_max=5.0)
        summary = {
            "cpu_pct": {"max_val": 45.0},
            "memory_mb": {"max_val": 100.0},
            "disk_used_pct": {"max_val": 30.0},
            "db_size_mb": {"max_val": 3.0},
        }
        _mod._check_drift(db_path, "2026-06-03", summary, log)
        assert mock_tg.called
        log.warning.assert_called_once()
        assert "CPU" in log.warning.call_args[0][1]

    @patch.object(_mod, "_send_telegram")
    def test_no_alert_below_threshold(self, mock_tg, db_path, log):
        self._seed_history(db_path, "2026-06-02", cpu_max=50.0, mem_max=200.0,
                           disk_max=60.0, db_max=5.0)
        summary = {
            "cpu_pct": {"max_val": 10.0},
            "memory_mb": {"max_val": 50.0},
            "disk_used_pct": {"max_val": 20.0},
            "db_size_mb": {"max_val": 1.0},
        }
        _mod._check_drift(db_path, "2026-06-03", summary, log)
        mock_tg.assert_not_called()
        log.warning.assert_not_called()

    @patch.object(_mod, "_send_telegram")
    def test_disk_drift_branch_inert_even_with_valid_spike(self, mock_tg, db_path, log):
        # T1: disk_used_pct is repaired (valid hist now), but disk alerting is
        # owned by disk_monitor.py. A clear disk spike (95% vs hist 40%) must NOT
        # fire here -- the drift branch is intentionally gated off. cpu/mem/db are
        # kept below threshold so disk is the only thing that *could* alert.
        self._seed_history(db_path, "2026-06-02", cpu_max=50.0, mem_max=200.0,
                           disk_max=40.0, db_max=5.0)
        summary = {
            "cpu_pct": {"max_val": 10.0},
            "memory_mb": {"max_val": 50.0},
            "disk_used_pct": {"max_val": 95.0},   # 95 > 40*0.8=32 -> would alert if not gated
            "db_size_mb": {"max_val": 1.0},
        }
        _mod._check_drift(db_path, "2026-06-03", summary, log)
        mock_tg.assert_not_called()
        log.warning.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: main()
# ---------------------------------------------------------------------------

class TestMain:
    @patch.object(_mod, "capture_snapshot", return_value={"cpu_pct": 10.0})
    @patch.object(_mod, "get_logger", return_value=MagicMock())
    def test_main_snapshot(self, mock_logger, mock_snap):
        result = _mod.main(["--db", "test.db", "--dry-run"])
        assert result == 0

    @patch.object(_mod, "compute_daily_summary", return_value=None)
    @patch.object(_mod, "get_logger", return_value=MagicMock())
    def test_main_summarize(self, mock_logger, mock_summary):
        result = _mod.main(["--db", "test.db", "--summarize", "--dry-run"])
        assert result == 0

    @patch.object(_mod, "capture_snapshot", side_effect=RuntimeError("boom"))
    @patch.object(_mod, "get_logger", return_value=MagicMock())
    def test_main_error_returns_1(self, mock_logger, mock_snap):
        result = _mod.main(["--db", "test.db"])
        assert result == 1


# ---------------------------------------------------------------------------
# Tests: CLI arg parsing
# ---------------------------------------------------------------------------

class TestArgParsing:
    def test_default_args(self):
        args = _mod._parse_args([])
        assert not args.summarize
        assert not args.dry_run

    def test_summarize_flag(self):
        args = _mod._parse_args(["--summarize"])
        assert args.summarize

    def test_dry_run_flag(self):
        args = _mod._parse_args(["--dry-run"])
        assert args.dry_run

    def test_custom_db(self):
        args = _mod._parse_args(["--db", "/tmp/custom.db"])
        assert args.db == "/tmp/custom.db"


# ---------------------------------------------------------------------------
# Tests: helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_get_thread_count_positive(self):
        assert _mod._get_thread_count() > 0

    def test_get_cpu_pct_returns_number(self):
        result = _mod._get_cpu_pct()
        assert isinstance(result, float)

    def test_get_memory_mb_returns_number(self):
        result = _mod._get_memory_mb()
        assert isinstance(result, float)

    def test_get_disk_used_pct_is_valid_percent(self):
        # T1: shutil-based now -> a real percentage, never the -1.0 psutil sentinel.
        import shutil as _shutil
        result = _mod._get_disk_used_pct()
        assert isinstance(result, float)
        assert result != -1.0
        assert 0.0 < result <= 100.0, result
        u = _shutil.disk_usage(_mod._ROOT)
        expected = round(u.used / u.total * 100.0, 2)
        assert abs(result - expected) <= 1.0, (result, expected)

    def test_db_and_log_size_unchanged_regression(self, tmp_path):
        # T1 must not perturb the pathlib-based size metrics.
        f = tmp_path / "x.db"
        f.write_bytes(b"a" * (2 * 1024 * 1024))  # 2 MB
        assert abs(_mod._get_db_size_mb(str(f)) - 2.0) < 0.01
        logd = tmp_path / "logs"
        logd.mkdir()
        (logd / "a.log").write_bytes(b"b" * (1024 * 1024))  # 1 MB
        assert abs(_mod._get_log_size_mb(logd) - 1.0) < 0.01
