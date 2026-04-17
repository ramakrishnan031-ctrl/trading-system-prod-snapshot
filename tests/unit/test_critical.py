"""
tests/unit/test_critical.py -- Trading System v2

Tests for alerts/critical.py (CR1-CR10).
All tests use temporary directories -- no real data_store writes.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import unittest
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from alerts.critical import (
    list_pending_sentinels,
    mark_delivered,
    mark_failed,
    read_sentinel,
    write_critical_sentinel,
)


# ==============================================================================
# Helpers
# ==============================================================================

def _write(tmpdir, **kwargs) -> Path:
    """Write a sentinel with sensible defaults into tmpdir."""
    return write_critical_sentinel(
        title=kwargs.get("title", "Test alert"),
        body=kwargs.get("body", "Something bad happened"),
        source_module=kwargs.get("source_module", "test.module"),
        context=kwargs.get("context", None),
        sentinel_dir=tmpdir,
    )


# ==============================================================================
# TestWriteCriticalSentinel
# ==============================================================================

class TestWriteCriticalSentinel(unittest.TestCase):
    """CR2, CR3, CR4 -- file creation and content."""

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_creates_flag_file(self):
        path = _write(self.tmpdir)
        self.assertTrue(path.exists(), "sentinel .flag file must exist")
        self.assertEqual(path.suffix, ".flag")

    def test_all_8_fields_present(self):
        path = _write(self.tmpdir, context={"key": "val"})
        data = json.loads(path.read_text(encoding="utf-8"))
        expected_keys = {"id", "ts", "title", "body", "source_module",
                         "context", "hostname", "pid"}
        self.assertEqual(set(data.keys()), expected_keys)

    def test_id_format_timestamp_uuid(self):
        path = _write(self.tmpdir)
        data = json.loads(path.read_text(encoding="utf-8"))
        # expected: YYYYMMDD_HHMMSS_<8hex>
        self.assertRegex(data["id"], r"^\d{8}_\d{6}_[0-9a-f]{8}$")

    def test_no_tmp_file_after_success(self):
        path = _write(self.tmpdir)
        tmp = path.with_suffix(".tmp")
        self.assertFalse(tmp.exists(), ".tmp must not remain after successful write")

    def test_sentinel_dir_created_if_absent(self):
        new_dir = self.tmpdir / "deep" / "nested"
        self.assertFalse(new_dir.exists())
        path = _write(new_dir)
        self.assertTrue(new_dir.exists(), "sentinel_dir must be created")
        self.assertTrue(path.exists())

    def test_title_over_120_chars_truncated(self):
        long_title = "X" * 200
        path = _write(self.tmpdir, title=long_title)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["title"]), 120)

    def test_title_under_120_chars_preserved(self):
        title = "Short title"
        path = _write(self.tmpdir, title=title)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["title"], title)

    def test_hostname_populated(self):
        import socket
        path = _write(self.tmpdir)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["hostname"], socket.gethostname())

    def test_pid_populated(self):
        path = _write(self.tmpdir)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["pid"], os.getpid())

    def test_context_none_becomes_empty_dict(self):
        path = _write(self.tmpdir, context=None)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["context"], {})

    def test_context_dict_preserved(self):
        ctx = {"severity": "CRITICAL", "delta": -500.0}
        path = _write(self.tmpdir, context=ctx)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["context"], ctx)

    def test_filename_contains_id(self):
        path = _write(self.tmpdir)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn(data["id"], path.name)

    def test_returns_path_object(self):
        result = _write(self.tmpdir)
        self.assertIsInstance(result, Path)


# ==============================================================================
# TestListPendingSentinels
# ==============================================================================

class TestListPendingSentinels(unittest.TestCase):
    """CR6 -- list_pending_sentinels."""

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_returns_only_flag_files(self):
        p1 = _write(self.tmpdir)
        p2 = _write(self.tmpdir)
        results = list_pending_sentinels(self.tmpdir)
        self.assertEqual(set(results), {p1, p2})

    def test_excludes_delivered(self):
        p = _write(self.tmpdir)
        mark_delivered(p)
        results = list_pending_sentinels(self.tmpdir)
        self.assertEqual(results, [])

    def test_excludes_failed(self):
        p = _write(self.tmpdir)
        mark_failed(p, "test reason")
        results = list_pending_sentinels(self.tmpdir)
        self.assertEqual(results, [])

    def test_excludes_tmp(self):
        # Plant a fake .tmp file
        (self.tmpdir / "critical_alert_fake.tmp").write_text("{}", encoding="utf-8")
        results = list_pending_sentinels(self.tmpdir)
        self.assertEqual(results, [])

    def test_sorted_by_mtime_ascending(self):
        # Write three sentinels with distinct mtimes
        p1 = _write(self.tmpdir)
        time.sleep(0.02)
        p2 = _write(self.tmpdir)
        time.sleep(0.02)
        p3 = _write(self.tmpdir)
        results = list_pending_sentinels(self.tmpdir)
        self.assertEqual(results, [p1, p2, p3])

    def test_empty_dir_returns_empty_list(self):
        results = list_pending_sentinels(self.tmpdir)
        self.assertEqual(results, [])

    def test_nonexistent_dir_returns_empty_list(self):
        results = list_pending_sentinels(self.tmpdir / "no_such_dir")
        self.assertEqual(results, [])


# ==============================================================================
# TestReadSentinel
# ==============================================================================

class TestReadSentinel(unittest.TestCase):
    """CR7 -- read_sentinel."""

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_parses_valid_json(self):
        p = _write(self.tmpdir)
        data = read_sentinel(p)
        self.assertIsInstance(data, dict)
        self.assertIn("id", data)

    def test_raises_value_error_on_malformed_json(self):
        bad = self.tmpdir / "critical_alert_bad.flag"
        bad.write_text("not json {{{", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_sentinel(bad)

    def test_raises_os_error_on_missing_file(self):
        with self.assertRaises(OSError):
            read_sentinel(self.tmpdir / "does_not_exist.flag")


# ==============================================================================
# TestMarkDelivered
# ==============================================================================

class TestMarkDelivered(unittest.TestCase):
    """CR8 -- mark_delivered."""

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_renames_flag_to_delivered(self):
        p = _write(self.tmpdir)
        delivered = mark_delivered(p)
        self.assertFalse(p.exists(), ".flag must no longer exist")
        self.assertTrue(delivered.exists(), ".delivered must exist")
        self.assertEqual(delivered.suffix, ".delivered")

    def test_returns_delivered_path(self):
        p = _write(self.tmpdir)
        result = mark_delivered(p)
        self.assertEqual(result.suffix, ".delivered")

    def test_second_call_raises(self):
        """Idempotency: calling twice on same path raises (file is gone)."""
        p = _write(self.tmpdir)
        mark_delivered(p)
        with self.assertRaises(OSError):
            mark_delivered(p)


# ==============================================================================
# TestMarkFailed
# ==============================================================================

class TestMarkFailed(unittest.TestCase):
    """CR9 -- mark_failed."""

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_renames_flag_to_failed(self):
        p = _write(self.tmpdir)
        failed = mark_failed(p, "SMTP error")
        self.assertFalse(p.exists(), ".flag must no longer exist")
        self.assertTrue(failed.exists(), ".failed must exist")
        self.assertEqual(failed.suffix, ".failed")

    def test_writes_reason_sibling_file(self):
        p = _write(self.tmpdir)
        failed = mark_failed(p, "SMTP auth failure")
        reason_path = failed.with_suffix(".reason")
        self.assertTrue(reason_path.exists(), ".reason sibling must exist")

    def test_reason_file_contains_reason_string(self):
        p = _write(self.tmpdir)
        mark_failed(p, "connection refused")
        failed = p.with_suffix(".failed")
        reason_text = failed.with_suffix(".reason").read_text(encoding="utf-8")
        self.assertEqual(reason_text, "connection refused")

    def test_returns_failed_path(self):
        p = _write(self.tmpdir)
        result = mark_failed(p, "reason")
        self.assertEqual(result.suffix, ".failed")


# ==============================================================================
# TestConcurrentWrites
# ==============================================================================

class TestConcurrentWrites(unittest.TestCase):
    """CR10 -- thread safety (pure writer, no shared state)."""

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_100_concurrent_writes_no_corruption(self):
        paths: list[Path] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker():
            for _ in range(20):
                try:
                    p = _write(self.tmpdir, title="Concurrent", body="body")
                    with lock:
                        paths.append(p)
                except Exception as exc:
                    with lock:
                        errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Unexpected errors: {errors}")
        self.assertEqual(len(paths), 100, "Expected 100 sentinel files")

        # No .tmp files left
        tmp_files = list(self.tmpdir.glob("*.tmp"))
        self.assertEqual(tmp_files, [], f"Leftover .tmp files: {tmp_files}")

        # All JSON valid
        for p in paths:
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertIn("id", data)


# ==============================================================================
# Standalone runner
# ==============================================================================

def run_all_tests() -> int:
    tests = [
        # TestWriteCriticalSentinel
        TestWriteCriticalSentinel("test_creates_flag_file"),
        TestWriteCriticalSentinel("test_all_8_fields_present"),
        TestWriteCriticalSentinel("test_id_format_timestamp_uuid"),
        TestWriteCriticalSentinel("test_no_tmp_file_after_success"),
        TestWriteCriticalSentinel("test_sentinel_dir_created_if_absent"),
        TestWriteCriticalSentinel("test_title_over_120_chars_truncated"),
        TestWriteCriticalSentinel("test_title_under_120_chars_preserved"),
        TestWriteCriticalSentinel("test_hostname_populated"),
        TestWriteCriticalSentinel("test_pid_populated"),
        TestWriteCriticalSentinel("test_context_none_becomes_empty_dict"),
        TestWriteCriticalSentinel("test_context_dict_preserved"),
        TestWriteCriticalSentinel("test_filename_contains_id"),
        TestWriteCriticalSentinel("test_returns_path_object"),
        # TestListPendingSentinels
        TestListPendingSentinels("test_returns_only_flag_files"),
        TestListPendingSentinels("test_excludes_delivered"),
        TestListPendingSentinels("test_excludes_failed"),
        TestListPendingSentinels("test_excludes_tmp"),
        TestListPendingSentinels("test_sorted_by_mtime_ascending"),
        TestListPendingSentinels("test_empty_dir_returns_empty_list"),
        TestListPendingSentinels("test_nonexistent_dir_returns_empty_list"),
        # TestReadSentinel
        TestReadSentinel("test_parses_valid_json"),
        TestReadSentinel("test_raises_value_error_on_malformed_json"),
        TestReadSentinel("test_raises_os_error_on_missing_file"),
        # TestMarkDelivered
        TestMarkDelivered("test_renames_flag_to_delivered"),
        TestMarkDelivered("test_returns_delivered_path"),
        TestMarkDelivered("test_second_call_raises"),
        # TestMarkFailed
        TestMarkFailed("test_renames_flag_to_failed"),
        TestMarkFailed("test_writes_reason_sibling_file"),
        TestMarkFailed("test_reason_file_contains_reason_string"),
        TestMarkFailed("test_returns_failed_path"),
        # TestConcurrentWrites
        TestConcurrentWrites("test_100_concurrent_writes_no_corruption"),
    ]

    suite = unittest.TestSuite(tests)
    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"))
    result = runner.run(suite)

    passed = len(tests) - len(result.failures) - len(result.errors)
    total = len(tests)

    print("=" * 70)
    print("alerts/critical.py -- Test Suite")
    print("=" * 70)
    for t in tests:
        name = t._testMethodName
        failed_names = [str(f[0]) for f in result.failures + result.errors]
        status = "FAIL" if any(name in f for f in failed_names) else "OK"
        print(f"  {status}  {name}")
    print("=" * 70)

    if result.failures or result.errors:
        for label, items in [("FAILURES", result.failures), ("ERRORS", result.errors)]:
            for tc, tb in items:
                print(f"\n{label}: {tc}")
                print(tb)
        print(f"\nFAILED: {total - passed}/{total}")
        return 1

    print(f"PASSED: all {total} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
