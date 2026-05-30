"""
tests/unit/test_fix131_wal_checkpoint.py

FIX-131 Item 23: WAL checkpoint on graceful shutdown.
  - checkpoint_wal() method exists and runs without error
  - Returns dict with busy/log/checkpointed keys
  - Works on fresh empty DB
"""
from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.state_store import StateStore


def _make_store(tmp: Path) -> StateStore:
    return StateStore(tmp / "test.db")


class TestWalCheckpoint:

    def test_checkpoint_wal_method_exists(self) -> None:
        """StateStore.checkpoint_wal() method exists."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            assert hasattr(store, "checkpoint_wal"), "checkpoint_wal() method must exist"
            store.close()
        print("  OK: checkpoint_wal() method exists on StateStore")

    def test_checkpoint_wal_returns_stats_dict(self) -> None:
        """checkpoint_wal() returns dict with busy/log/checkpointed keys."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            stats = store.checkpoint_wal()
            assert isinstance(stats, dict), "checkpoint_wal() must return a dict"
            assert "busy" in stats
            assert "log" in stats
            assert "checkpointed" in stats
            store.close()
        print(f"  OK: checkpoint_wal() returns stats: {stats}")

    def test_checkpoint_wal_on_empty_db(self) -> None:
        """checkpoint_wal() runs without error on empty/fresh DB."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            try:
                stats = store.checkpoint_wal()
                assert stats["busy"] >= 0
                assert stats["log"] >= 0
                assert stats["checkpointed"] >= 0
            except Exception as exc:
                raise AssertionError(f"checkpoint_wal() must not raise on empty DB: {exc}")
            store.close()
        print("  OK: checkpoint_wal() succeeds on empty DB")

    def test_checkpoint_wal_uses_passive_mode(self) -> None:
        """checkpoint_wal() source code uses PASSIVE (not TRUNCATE)."""
        import inspect
        from core.state_store import StateStore
        source = inspect.getsource(StateStore.checkpoint_wal)
        assert "PASSIVE" in source.upper(), (
            "checkpoint_wal() must use PASSIVE mode in its SQL"
        )
        assert "TRUNCATE" not in source.upper(), (
            "checkpoint_wal() must NOT use TRUNCATE (that's checkpoint())"
        )
        print("  OK: checkpoint_wal() uses PASSIVE mode in source")

    def test_wal_checkpoint_script_exists(self) -> None:
        """scripts/wal_checkpoint.py cron script exists."""
        script = Path(__file__).parent.parent.parent / "scripts" / "wal_checkpoint.py"
        assert script.exists(), f"scripts/wal_checkpoint.py must exist at {script}"
        print("  OK: scripts/wal_checkpoint.py exists")


if __name__ == "__main__":
    tests = [
        TestWalCheckpoint().test_checkpoint_wal_method_exists,
        TestWalCheckpoint().test_checkpoint_wal_returns_stats_dict,
        TestWalCheckpoint().test_checkpoint_wal_on_empty_db,
        TestWalCheckpoint().test_checkpoint_wal_uses_passive_mode,
        TestWalCheckpoint().test_wal_checkpoint_script_exists,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
