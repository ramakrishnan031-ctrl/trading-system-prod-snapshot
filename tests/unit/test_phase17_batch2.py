"""
tests/unit/test_phase17_batch2.py

Phase 17 Audit Batch 2 tests (quick wins):
    - FIX-076: PRAGMA synchronous = FULL
    - FIX-077: Flask MAX_CONTENT_LENGTH = 1MB
"""
import sqlite3
import tempfile
from pathlib import Path

def test_fix076_state_store_synchronous_full() -> None:
    """
    FIX-076: PRAGMA synchronous must be FULL (not NORMAL) for Oracle Cloud
    networked block storage crash survivability.
    """
    from core.state_store import StateStore, _CONNECTION_PRAGMAS

    # Check that _CONNECTION_PRAGMAS includes FULL
    assert any("synchronous = FULL" in pragma for pragma in _CONNECTION_PRAGMAS), \
        "FIX-076: _CONNECTION_PRAGMAS must include 'PRAGMA synchronous = FULL'"

    # Verify runtime connection has FULL set
    tmpdir_obj = tempfile.TemporaryDirectory()
    tmpdir = tmpdir_obj.name
    try:
        db_path = Path(tmpdir) / "test.db"
        store = StateStore(db_path)

        with store.transaction() as cur:
            cur.execute("PRAGMA synchronous")
            result = cur.fetchone()
            # synchronous returns 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
            assert result is not None and int(result[0]) == 2, \
                f"FIX-076: PRAGMA synchronous must be 2 (FULL), got {result}"

        # Close all connections before cleanup (Windows)
        store.close()
    finally:
        tmpdir_obj.cleanup()

    print("  OK fix076_synchronous_full")


def test_fix077_flask_max_content_length() -> None:
    """
    FIX-077: Flask MAX_CONTENT_LENGTH must be 1MB to prevent OOM on oversized payloads.
    """
    import queue
    from unittest.mock import Mock
    from signals.webhook_receiver import WebhookReceiver

    signal_queue = queue.Queue(maxsize=100)
    state_store = Mock()
    state_store.transaction = Mock()
    market_windows = Mock()
    kill_switch = Mock()
    logger = Mock()

    # No HMAC config - simple test
    config = Mock()
    config.webhook = Mock()
    config.webhook.require_hmac = False

    receiver = WebhookReceiver(
        signal_queue, state_store, config, market_windows, kill_switch, logger
    )

    # Verify Flask app has MAX_CONTENT_LENGTH set to 1MB
    assert receiver.app.config.get("MAX_CONTENT_LENGTH") == 1 * 1024 * 1024, \
        "FIX-077: Flask MAX_CONTENT_LENGTH must be 1MB"

    print("  OK fix077_flask_max_content_length")


# Run tests
if __name__ == "__main__":
    test_fix076_state_store_synchronous_full()
    test_fix077_flask_max_content_length()
    print("\n[Phase 17 Batch 2] All 2 tests passed.")
