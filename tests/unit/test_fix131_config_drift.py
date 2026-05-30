"""
tests/unit/test_fix131_config_drift.py

FIX-131 Item 20: Config drift detection on startup.
Verifies the already-implemented check_config_hash() behavior:
  - Changed files detected, warning added
  - No alert on first run (COLD scenario)
  - check_config_hash returns ConfigHashResult with changed_files list
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.state_store import StateStore
from core.time_authority import now_ist
from utils.startup_checks import check_config_hash


def _log():
    import logging
    return logging.getLogger("test_config_drift")


def _make_store(tmp: Path) -> StateStore:
    return StateStore(tmp / "test.db")


def _seed_session_with_hashes(store: StateStore, hashes: dict) -> None:
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            """INSERT OR REPLACE INTO session
               (id, session_date, account_id, broker, mode, trade_type,
                last_config_hash, session_start, last_updated)
               VALUES (1, ?, 'default', 'zerodha', 'paper', 'INTRADAY', ?, ?, ?)""",
            (now[:10], json.dumps(hashes), now, now),
        )


class TestConfigDrift:

    def test_no_change_returns_unchanged(self) -> None:
        """Same hashes stored and current: no change detected."""
        import types
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            hashes = {"system_config.yaml": "abc123", "broker_costs.yaml": "def456"}
            _seed_session_with_hashes(store, hashes)

            cfg = types.SimpleNamespace(file_hashes=hashes)
            result = check_config_hash(store, cfg, _log())

            assert not result.changed
            assert result.changed_files == []
            store.close()
        print("  OK: no hash change -> result.changed=False")

    def test_changed_file_detected(self) -> None:
        """Changed file hash detected and reported in changed_files."""
        import types
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            old_hashes = {"system_config.yaml": "old_hash_abc", "broker_costs.yaml": "def456"}
            new_hashes = {"system_config.yaml": "new_hash_xyz", "broker_costs.yaml": "def456"}
            _seed_session_with_hashes(store, old_hashes)

            cfg = types.SimpleNamespace(file_hashes=new_hashes)
            result = check_config_hash(store, cfg, _log())

            assert result.changed
            assert "system_config.yaml" in result.changed_files
            assert "broker_costs.yaml" not in result.changed_files
            store.close()
        print("  OK: changed file detected in changed_files list")

    def test_new_file_detected_as_change(self) -> None:
        """New config file added since last session detected as change."""
        import types
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            old_hashes = {"system_config.yaml": "abc123"}
            new_hashes = {"system_config.yaml": "abc123", "new_file.yaml": "xyz789"}
            _seed_session_with_hashes(store, old_hashes)

            cfg = types.SimpleNamespace(file_hashes=new_hashes)
            result = check_config_hash(store, cfg, _log())

            assert result.changed
            assert "new_file.yaml" in result.changed_files
            store.close()
        print("  OK: new config file detected as change")

    def test_cold_start_no_change_reported(self) -> None:
        """First run (no session row) reports no change (no previous baseline)."""
        import types
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            # No session seeded — COLD start

            cfg = types.SimpleNamespace(
                file_hashes={"system_config.yaml": "abc123"}
            )
            result = check_config_hash(store, cfg, _log())

            assert not result.changed, "First run should NOT report config change"
            store.close()
        print("  OK: cold start (no session row) reports no config change")


if __name__ == "__main__":
    tests = [
        TestConfigDrift().test_no_change_returns_unchanged,
        TestConfigDrift().test_changed_file_detected,
        TestConfigDrift().test_new_file_detected_as_change,
        TestConfigDrift().test_cold_start_no_change_reported,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
