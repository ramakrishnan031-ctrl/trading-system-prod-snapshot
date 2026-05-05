"""Tests for utils/instance_lock.py -- single-instance enforcement."""
import os
import socket
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.instance_lock import (
    _LOCK_FILE,
    acquire_instance_lock,
    check_port_available,
    release_instance_lock,
)


class TestAcquireInstanceLock:
    """GUARD 2: PID lock file prevents duplicate instances."""

    def setup_method(self):
        """Clean up any stale lock from prior test."""
        if _LOCK_FILE.exists():
            _LOCK_FILE.unlink()

    def teardown_method(self):
        if _LOCK_FILE.exists():
            _LOCK_FILE.unlink()

    def test_acquires_when_no_lock_exists(self):
        ok, reason = acquire_instance_lock()
        assert ok is True
        assert reason == ""
        assert _LOCK_FILE.exists()
        assert int(_LOCK_FILE.read_text().strip()) == os.getpid()

    def test_fails_when_same_pid_alive(self):
        _LOCK_FILE.write_text(str(os.getpid()))
        ok, reason = acquire_instance_lock()
        assert ok is False
        assert "Another instance running" in reason
        assert str(os.getpid()) in reason

    def test_succeeds_when_stale_lock_dead_pid(self):
        _LOCK_FILE.write_text("999999999")
        ok, reason = acquire_instance_lock()
        assert ok is True
        assert reason == ""
        assert int(_LOCK_FILE.read_text().strip()) == os.getpid()

    def test_succeeds_when_lock_has_garbage(self):
        _LOCK_FILE.write_text("not_a_pid")
        ok, reason = acquire_instance_lock()
        assert ok is True
        assert reason == ""

    def test_release_removes_lock_for_own_pid(self):
        acquire_instance_lock()
        assert _LOCK_FILE.exists()
        release_instance_lock()
        assert not _LOCK_FILE.exists()

    def test_release_does_not_remove_other_pids_lock(self):
        _LOCK_FILE.write_text("12345")
        release_instance_lock()
        assert _LOCK_FILE.exists()
        assert _LOCK_FILE.read_text().strip() == "12345"

    def test_release_silent_when_no_lock(self):
        release_instance_lock()  # should not raise


class TestCheckPortAvailable:
    """GUARD 1: Port conflict detection before binding."""

    def test_available_port_returns_true(self):
        ok, reason = check_port_available(59999)
        assert ok is True
        assert reason == ""

    def test_occupied_port_returns_false(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 59998))
        server.listen(1)
        try:
            ok, reason = check_port_available(59998)
            assert ok is False
            assert "59998" in reason
            assert "already in use" in reason
        finally:
            server.close()

    def test_checks_correct_host(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 59997))
        server.listen(1)
        try:
            ok, _ = check_port_available(59997, "127.0.0.1")
            assert ok is False
        finally:
            server.close()


class TestIntegrationMainGuards:
    """Verify both guards integrate correctly with main.py startup flow."""

    def test_lock_then_port_check_sequence(self):
        """Both guards can run in sequence without conflict."""
        ok1, _ = acquire_instance_lock()
        assert ok1 is True
        ok2, _ = check_port_available(59996)
        assert ok2 is True
        release_instance_lock()

    def test_lock_prevents_second_acquire(self):
        """Second acquire fails while first holds the lock."""
        ok1, _ = acquire_instance_lock()
        assert ok1 is True
        ok2, reason = acquire_instance_lock()
        assert ok2 is False
        assert "Another instance" in reason
        release_instance_lock()
