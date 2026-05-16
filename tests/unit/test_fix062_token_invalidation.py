"""
FIX-062: Token invalidation on BrokerAuthError.

Tests token file renaming to prevent infinite restart loop.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def test_invalidate_token_renames_file() -> None:
    """FIX-062: _invalidate_token() renames zerodha_token.json atomically."""
    with tempfile.TemporaryDirectory() as tmp:
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp)

            # Create token file
            token_path = Path("zerodha_token.json")
            token_path.write_text('{"access_token": "test123"}')

            # Import and call
            import main as main_module
            main_module._log = logging.getLogger("test")
            main_module._invalidate_token()

            # Assert renamed
            assert not token_path.exists(), "Token file should be removed"
            invalid_path = Path("zerodha_token.invalid")
            assert invalid_path.exists(), "Invalid token file should exist"
            assert invalid_path.read_text() == '{"access_token": "test123"}'

            print("  OK token file renamed atomically")
        finally:
            os.chdir(original_cwd)


def test_invalidate_token_missing_file_logs_warning() -> None:
    """FIX-062: _invalidate_token() logs warning if token file missing."""
    with tempfile.TemporaryDirectory() as tmp:
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp)

            # No token file exists
            import main as main_module
            mock_log = MagicMock()
            main_module._log = mock_log

            main_module._invalidate_token()

            # Assert warning logged
            assert mock_log.warning.called
            call_args = str(mock_log.warning.call_args)
            assert "not found" in call_args or "skipping" in call_args

            print("  OK missing file logs warning")
        finally:
            os.chdir(original_cwd)


def test_mark_auth_failed_sets_flag() -> None:
    """FIX-062: _mark_auth_failed() sets global flag."""
    import main as main_module

    # Reset flag
    main_module._broker_auth_failed = False

    mock_log = MagicMock()
    main_module._log = mock_log

    main_module._mark_auth_failed()

    assert main_module._broker_auth_failed is True
    assert mock_log.critical.called

    print("  OK flag set and critical logged")


def test_critical_callback_marks_auth_failure() -> None:
    """FIX-062: Critical callback marks auth failure when reason contains BrokerAuthError."""
    import main as main_module

    # Reset flag
    main_module._broker_auth_failed = False

    mock_kill_switch = MagicMock()
    mock_notifier = MagicMock()
    mock_log = MagicMock()
    main_module._log = mock_log

    callback = main_module._make_critical_failure_cb(
        kill_switch=mock_kill_switch,
        notifier=mock_notifier,
        mode="LIVE",
    )

    # Call with BrokerAuthError reason
    callback("order_monitor", "BrokerAuthError x3 -- monitor stopping")

    assert main_module._broker_auth_failed is True
    assert mock_kill_switch.soft_kill.called

    print("  OK critical callback marks auth failure")


def test_critical_callback_non_auth_error_does_not_mark() -> None:
    """FIX-062: Critical callback does NOT mark flag for non-auth errors."""
    import main as main_module

    # Reset flag
    main_module._broker_auth_failed = False

    mock_kill_switch = MagicMock()
    mock_notifier = MagicMock()
    mock_log = MagicMock()
    main_module._log = mock_log

    callback = main_module._make_critical_failure_cb(
        kill_switch=mock_kill_switch,
        notifier=mock_notifier,
        mode="LIVE",
    )

    # Call with non-auth error
    callback("order_monitor", "BrokerTimeoutError -- monitor stopping")

    assert main_module._broker_auth_failed is False
    assert mock_kill_switch.soft_kill.called

    print("  OK non-auth error does not mark flag")


def test_shutdown_invalidates_token_if_flag_set() -> None:
    """FIX-062: _shutdown() calls _invalidate_token() if flag is set."""
    with tempfile.TemporaryDirectory() as tmp:
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp)

            # Create token file
            token_path = Path("zerodha_token.json")
            token_path.write_text('{"access_token": "test123"}')

            import main as main_module
            main_module._log = logging.getLogger("test")

            # Set flag
            main_module._broker_auth_failed = True

            # Create mocks for _shutdown params
            mock_args = {
                "signal_proc": MagicMock(stop=MagicMock()),
                "entry_gate": MagicMock(stop=MagicMock()),
                "smart_tgt": MagicMock(stop=MagicMock()),
                "order_reconciler": MagicMock(stop=MagicMock()),
                "order_monitor": MagicMock(stop=MagicMock()),
                "live_feed": MagicMock(disconnect=MagicMock()),
                "candle_store": MagicMock(stop=MagicMock()),
                "notifier": MagicMock(send=MagicMock()),
                "store": MagicMock(close=MagicMock()),
                "webhook_receiver": MagicMock(stop=MagicMock()),
                "clock_skew_probe": MagicMock(stop=MagicMock()),
            }

            main_module._shutdown(**mock_args)

            # Assert token was invalidated
            assert not token_path.exists(), "Token file should be removed"
            invalid_path = Path("zerodha_token.invalid")
            assert invalid_path.exists(), "Invalid token file should exist"

            print("  OK shutdown invalidates token when flag set")
        finally:
            os.chdir(original_cwd)
            # Reset flag
            import main as main_module
            main_module._broker_auth_failed = False


def test_shutdown_does_not_invalidate_if_flag_not_set() -> None:
    """FIX-062: _shutdown() does NOT invalidate token if flag is False."""
    with tempfile.TemporaryDirectory() as tmp:
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp)

            # Create token file
            token_path = Path("zerodha_token.json")
            token_path.write_text('{"access_token": "test123"}')

            import main as main_module
            main_module._log = logging.getLogger("test")

            # Flag is False
            main_module._broker_auth_failed = False

            # Create mocks for _shutdown params
            mock_args = {
                "signal_proc": MagicMock(stop=MagicMock()),
                "entry_gate": MagicMock(stop=MagicMock()),
                "smart_tgt": MagicMock(stop=MagicMock()),
                "order_reconciler": MagicMock(stop=MagicMock()),
                "order_monitor": MagicMock(stop=MagicMock()),
                "live_feed": MagicMock(disconnect=MagicMock()),
                "candle_store": MagicMock(stop=MagicMock()),
                "notifier": MagicMock(send=MagicMock()),
                "store": MagicMock(close=MagicMock()),
                "webhook_receiver": MagicMock(stop=MagicMock()),
                "clock_skew_probe": MagicMock(stop=MagicMock()),
            }

            main_module._shutdown(**mock_args)

            # Assert token was NOT invalidated
            assert token_path.exists(), "Token file should still exist"
            assert token_path.read_text() == '{"access_token": "test123"}'

            print("  OK shutdown preserves token when flag not set")
        finally:
            os.chdir(original_cwd)


if __name__ == "__main__":
    test_invalidate_token_renames_file()
    test_invalidate_token_missing_file_logs_warning()
    test_mark_auth_failed_sets_flag()
    test_critical_callback_marks_auth_failure()
    test_critical_callback_non_auth_error_does_not_mark()
    test_shutdown_invalidates_token_if_flag_set()
    test_shutdown_does_not_invalidate_if_flag_not_set()
    print("\nAll FIX-062 tests passed!")
