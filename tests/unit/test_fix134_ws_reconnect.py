"""Tests for FIX-134 Item 37: WebSocket reconnect hardening."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


class TestReconnectHardening:
    """Test LiveFeedManager reconnect behavior (mocked KiteTicker)."""

    def _make_manager(self, **kwargs):
        from data.live_feed import LiveFeedManager
        defaults = dict(
            api_key="test",
            access_token="test",
            logger=logging.getLogger("test_lf"),
            paper_mode=True,
            max_reconnect_attempts=10,
            reconnect_delay_sec=5,
        )
        defaults.update(kwargs)
        return LiveFeedManager(**defaults)

    def test_reconnect_count_increments(self):
        mgr = self._make_manager()
        assert mgr.reconnect_count == 0
        mgr._on_reconnect(None, 1)
        assert mgr.reconnect_count == 1
        mgr._on_reconnect(None, 2)
        assert mgr.reconnect_count == 2

    def test_noreconnect_triggers_soft_kill(self):
        ks = MagicMock()
        mgr = self._make_manager(kill_switch=ks)
        mgr._on_noreconnect(None)
        ks.soft_kill.assert_called_once_with("LIVEFEED_RECONNECT_EXHAUSTED")

    def test_noreconnect_sends_telegram(self):
        notifier = MagicMock()
        mgr = self._make_manager()
        mgr.set_notifier(notifier, "LIVE")
        mgr._on_noreconnect(None)
        assert notifier.send.called
        call_kw = notifier.send.call_args.kwargs
        assert "DEAD" in call_kw["title"]
        assert call_kw["severity"] == "ERROR"

    def test_reconnect_sends_warning(self):
        notifier = MagicMock()
        mgr = self._make_manager()
        mgr.set_notifier(notifier, "PAPER")
        mgr._on_reconnect(None, 1)
        assert notifier.send.called
        call_kw = notifier.send.call_args.kwargs
        assert call_kw["severity"] == "WARNING"
        assert "Reconnecting" in call_kw["title"]

    def test_on_connect_resubscribes(self):
        mgr = self._make_manager()
        mgr._subscribed = {12345, 67890}
        ws = MagicMock()
        mgr._on_connect(ws, {})
        ws.subscribe.assert_called_once()
        assert mgr.is_connected()

    def test_on_close_sets_disconnected(self):
        mgr = self._make_manager()
        mgr._connected = True
        mgr._on_close(None, 1000, "normal")
        assert not mgr.is_connected()
        assert mgr._disconnect_time is not None

    def test_paper_mode_skips_connect(self):
        mgr = self._make_manager(paper_mode=True)
        mgr.connect()
        assert not mgr.is_connected()

    def test_config_model_defaults(self):
        from core.config_loader import LiveFeedConfig
        cfg = LiveFeedConfig()
        assert cfg.max_reconnect_attempts == 10
        assert cfg.reconnect_backoff_base_seconds == 1
        assert cfg.reconnect_backoff_max_seconds == 30

    def test_backoff_sequence(self):
        """Verify exponential backoff formula: min(base * 2^attempt, max)."""
        base = 1
        max_delay = 30
        expected = [1, 2, 4, 8, 16, 30, 30, 30, 30, 30]
        for attempt in range(10):
            delay = min(base * (2 ** attempt), max_delay)
            assert delay == expected[attempt]

    def test_noreconnect_without_killswitch(self):
        mgr = self._make_manager(kill_switch=None)
        mgr._on_noreconnect(None)
        assert not mgr.is_connected()

    def test_reconnect_notifier_failure_silenced(self):
        notifier = MagicMock()
        notifier.send.side_effect = RuntimeError("Telegram down")
        mgr = self._make_manager()
        mgr.set_notifier(notifier)
        mgr._on_reconnect(None, 1)
        assert mgr.reconnect_count == 1

    # ── FIX-189 (P1-B): off-hours reconnect-exhaustion is not an incident ──────

    def test_fix189_noreconnect_offhours_no_softkill_no_critical(self):
        """Max reconnects exhausted OUTSIDE market hours (expired overnight token /
        broker maintenance) must NOT SOFT_KILL, NOT fire the critical-failure
        callback, and downgrade the alert to WARNING. This was the false 07:07
        'KiteTicker max reconnect exhausted' CRITICAL while running overnight."""
        mw = MagicMock()
        mw.is_market_open.return_value = False
        ks = MagicMock()
        crit = MagicMock()
        notifier = MagicMock()
        mgr = self._make_manager(
            kill_switch=ks, market_windows=mw, on_critical_failure=crit
        )
        mgr.set_notifier(notifier, "LIVE")
        mgr._on_noreconnect(None)

        ks.soft_kill.assert_not_called()
        crit.assert_not_called()
        if notifier.send.called:
            assert notifier.send.call_args.kwargs["severity"] == "WARNING"

    def test_fix189_noreconnect_in_session_still_escalates(self):
        """The gate is surgical — DURING market hours a dead feed is still a real
        incident: SOFT_KILL + critical-failure callback + ERROR alert."""
        mw = MagicMock()
        mw.is_market_open.return_value = True
        ks = MagicMock()
        crit = MagicMock()
        notifier = MagicMock()
        mgr = self._make_manager(
            kill_switch=ks, market_windows=mw, on_critical_failure=crit
        )
        mgr.set_notifier(notifier, "LIVE")
        mgr._on_noreconnect(None)

        ks.soft_kill.assert_called_once_with("LIVEFEED_RECONNECT_EXHAUSTED")
        crit.assert_called_once()
        assert notifier.send.call_args.kwargs["severity"] == "ERROR"

    def test_fix189_noreconnect_no_market_windows_failsafe_escalates(self):
        """If no market_windows is injected we fail safe and keep legacy
        escalation (SOFT_KILL), so older wiring is never silently weakened."""
        ks = MagicMock()
        mgr = self._make_manager(kill_switch=ks)  # market_windows defaults to None
        mgr._on_noreconnect(None)
        ks.soft_kill.assert_called_once_with("LIVEFEED_RECONNECT_EXHAUSTED")
