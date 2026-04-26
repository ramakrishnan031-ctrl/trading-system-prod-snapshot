"""
tests/unit/test_main.py -- Trading System v2

Unit tests for main.py orchestration.

Tests cover (per MAIN build spec):
  - CLI parsing (MAIN2)
  - Phase sequencing (MAIN4-MAIN12)
  - Subsystem wiring (MAIN8-MAIN9)
  - Startup reconciliation (MAIN10)
  - Start sequence (MAIN11)
  - Shutdown (MAIN15)
  - Callbacks (MAIN18)
  - Status mode (MAIN16)

Run: python -m pytest tests/unit/test_main.py -v
Or:  python tests/unit/test_main.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import main as _main_module
from core.config_loader import HolidayEntry
from main import (
    VERSION,
    _parse_args,
    _load_holidays,
    _print_status,
    _make_critical_failure_cb,
    _make_orphan_cb,
    _make_gate_release_cb,
    _log_kill_switch_event,
)

_IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_app_config():
    """Build a minimal AppConfig-like mock with all required sub-configs."""
    cfg = MagicMock(name="AppConfig")
    cfg.file_hashes = {"system_config.yaml": "abc123"}

    sys_cfg = cfg.system
    sys_cfg.kill_switch.api_failure_threshold = 3
    sys_cfg.kill_switch.enable_auto_trip = True
    sys_cfg.clock.warn_skew_sec = 2.0
    sys_cfg.clock.alert_skew_sec = 5.0
    sys_cfg.clock.halt_skew_sec = 30.0
    sys_cfg.clock.startup_max_skew_sec = 30.0
    sys_cfg.capital.intraday_bucket_pct = 0.7
    sys_cfg.capital.positional_bucket_pct = 0.3
    sys_cfg.capital.daily_loss_limit = 10000.0
    sys_cfg.capital.leverage_map.INTRADAY = 5.0
    sys_cfg.capital.leverage_map.COVER_ORDER = 6.0
    sys_cfg.capital.leverage_map.DELIVERY = 1.0
    sys_cfg.capital.leverage_map.BRACKET_ORDER = 5.0
    sys_cfg.position_sizing.risk_per_trade_pct = 0.01
    sys_cfg.position_sizing.max_concentration_pct = 0.10
    sys_cfg.position_sizing.min_qty_threshold = 1
    sys_cfg.position_sizing.tier_multipliers.HIGH = 1.0
    sys_cfg.position_sizing.tier_multipliers.MEDIUM = 0.70
    sys_cfg.position_sizing.tier_multipliers.LOW = 0.50
    sys_cfg.risk.max_open_positions = 5
    sys_cfg.risk.max_daily_trades = 20
    sys_cfg.risk.max_sector_exposure_pct = 0.3
    sys_cfg.risk.max_consecutive_losses = 3
    sys_cfg.risk.daily_loss_limit_pct = 0.05
    sys_cfg.order_monitor.poll_interval_sec = 2
    sys_cfg.order_monitor.fill_timeout_sec = 60
    sys_cfg.order_reconciler.poll_interval_sec = 15
    sys_cfg.order_reconciler.capital_drift_tolerance = 500.0
    sys_cfg.order_reconciler.enable_event_driven = True
    sys_cfg.signal_processor.worker_count = 3
    sys_cfg.signal_processor.drain_poll_sec = 0.1
    sys_cfg.eod_squareoff.poll_interval_sec = 5
    sys_cfg.eod_squareoff.inter_order_delay_ms = 500
    sys_cfg.shadow_tracker.enabled = True
    sys_cfg.shadow_tracker.max_innings = 3
    sys_cfg.shadow_tracker.alert_per_inning = True
    sys_cfg.signal_queue.capacity = 100
    sys_cfg.webhook.bind_host = "127.0.0.1"
    sys_cfg.webhook.bind_port = 5000
    _mock_chan = MagicMock()
    _mock_chan.chat_id_env = "TELEGRAM_CHANNEL_PRIMARY"
    _mock_chan.label = "Primary Alert Channel"
    _mock_chan.enabled = True
    sys_cfg.alerts.telegram.bot_token_env = "TELEGRAM_BOT_TOKEN"
    sys_cfg.alerts.telegram.channels = [_mock_chan]
    sys_cfg.alerts.failed_alerts_log_path = "/tmp/alerts.log"
    sys_cfg.alerts.sentinel_dir = "/tmp/sentinel"
    sys_cfg.product_map = {"zerodha": {"INTRADAY": "MIS", "DELIVERY": "CNC"}}

    cfg.nse_holidays.holidays = [HolidayEntry(date=date(2026, 1, 26), name="Republic Day")]
    cfg.scoring = MagicMock()
    cfg.scan_webhook_map.scanners = {}
    cfg.chartink_scanners = MagicMock()
    cfg.broker_limits = MagicMock()
    cfg.broker_costs = MagicMock()
    return cfg


def _make_mock_store():
    store = MagicMock(name="StateStore")
    store.get_session_row.return_value = None
    store.find_shutdown_event_for_date.return_value = None
    return store


def _make_mock_kill_switch(state="INACTIVE"):
    ks = MagicMock(name="KillSwitch")
    ks.status.return_value = {"state": state, "reason": "", "triggered_at": None}
    ks.is_active.return_value = (state != "INACTIVE")
    ks.current_state.return_value = MagicMock(value=state)
    return ks


def _make_mock_scenario_result(scenario="COLD"):
    from utils.startup_checks import StartupScenario, StartupScenarioResult
    s = StartupScenario[scenario]
    return StartupScenarioResult(
        scenario=s,
        session_date_previous=None,
        kill_state="INACTIVE",
        kill_reason="",
        shutdown_marker_found=False,
        last_shutdown_ts=None,
        detection_details={"reason": "test"},
    )


def _make_mock_logger():
    return MagicMock(
        info=MagicMock(), warning=MagicMock(),
        critical=MagicMock(), error=MagicMock(),
        debug=MagicMock(),
    )


def _make_mock_time_authority():
    ta = MagicMock(name="time_authority")
    ta.now_ist.return_value = datetime(2026, 4, 16, 10, 0, tzinfo=_IST)
    ta.now_ist_iso.return_value = "2026-04-16T10:00:00+05:30"
    return ta


def _make_mock_account_registry():
    """Build a minimal AccountRegistry-like mock for test_main.py patches."""
    mock_acct = MagicMock()
    mock_acct.account_id = "LFL836"
    mock_acct.broker = "zerodha"
    mock_acct.label = "Kandasamy"
    mock_acct.is_primary = True
    mock_acct.api_key_env = "ZERODHA_API_KEY"
    mock_acct.api_secret_env = "ZERODHA_API_SECRET"
    mock_acct.paper_capital = 50_000.0
    mock_acct.enabled = True

    mock_reg = MagicMock()
    mock_reg.primary.return_value = mock_acct
    mock_reg.count.return_value = 1
    mock_reg.get_enabled_accounts.return_value = [mock_acct]
    return mock_reg


def _make_all_patches(extra=None):
    """
    Build the patch dict for patch.multiple("main", ...).
    Keys are attribute names inside the main module (NO "main." prefix).
    """
    store_mock = _make_mock_store()
    ks_mock = _make_mock_kill_switch()
    reconciler_mock = MagicMock()
    reconciler_mock.reconcile_once.return_value = []
    adapter_mock = MagicMock()
    # F.1 / H-26 + EF-7: adapter net must match paper_capital (50k) so the
    # EF-7 startup consistency check passes in the default fixture.
    adapter_mock.get_margins.return_value = MagicMock(net=50_000.0)
    adapter_mock.get_quote = MagicMock()
    # F.1 / EF-7: fund_manager.total must be numeric and match adapter.net.
    # check_paper_capital_consistency() reads fund_manager.get_snapshot().total.
    fund_manager_mock = MagicMock()
    fund_manager_mock.total = 50_000.0
    fund_manager_mock.get_snapshot.return_value = MagicMock(total=50_000.0)

    ta_mock = _make_mock_time_authority()

    patches = {
        "setup_logging": MagicMock(),
        "get_logger": MagicMock(return_value=_make_mock_logger()),
        "load_all": MagicMock(return_value=_make_mock_app_config()),
        "StateStore": MagicMock(return_value=store_mock),
        "EventBus": MagicMock(return_value=MagicMock()),
        "KillSwitch": MagicMock(return_value=ks_mock),
        "_init_time_authority": MagicMock(),
        "time_authority": ta_mock,
        "detect_startup_scenario": MagicMock(
            return_value=_make_mock_scenario_result("COLD")
        ),
        "check_config_hash": MagicMock(
            return_value=MagicMock(changed=False, changed_files=[])
        ),
        "run_all_startup_checks": MagicMock(
            return_value=MagicMock(ok=True, blocking_failures=[], warnings=[])
        ),
        "check_webhook_endpoint": MagicMock(
            return_value=MagicMock(reachable=True)
        ),
        "OrderStateMachine": MagicMock(return_value=MagicMock()),
        "RateLimiter": MagicMock(return_value=MagicMock()),
        "ProductResolver": MagicMock(return_value=MagicMock()),
        "CostCalculator": MagicMock(return_value=MagicMock()),
        "ZerodhaAdapter": MagicMock(return_value=adapter_mock),
        "TelegramNotifier": MagicMock(return_value=MagicMock()),
        "FundManager": MagicMock(return_value=fund_manager_mock),
        "PositionSizer": MagicMock(return_value=MagicMock()),
        "RiskEngine": MagicMock(return_value=MagicMock()),
        "LiveFeedManager": MagicMock(return_value=MagicMock()),
        "CandleStore": MagicMock(return_value=MagicMock()),
        "SmartTgtManager": MagicMock(return_value=MagicMock()),
        "OrderMonitor": MagicMock(return_value=MagicMock()),
        "CoPlusTgtProtocol": MagicMock(return_value=MagicMock()),
        "LimitTripleProtocol": MagicMock(return_value=MagicMock()),
        "FullEntryEngine": MagicMock(return_value=MagicMock()),
        "OrderManager": MagicMock(return_value=MagicMock()),
        "OrderPlacer": MagicMock(return_value=MagicMock()),
        "OrderReconciler": MagicMock(return_value=reconciler_mock),
        "StrategyLoader": MagicMock(return_value=MagicMock(**{
            "load_all_strategies.return_value": {},
        })),
        "QualityScorer": MagicMock(return_value=MagicMock()),
        "StepExecutor": MagicMock(return_value=MagicMock()),
        "SecondaryScreener": MagicMock(return_value=MagicMock()),
        "EodSquareoff": MagicMock(return_value=MagicMock()),
        "SignalProcessor": MagicMock(return_value=MagicMock()),
        "ShadowTracker": MagicMock(return_value=MagicMock()),
        "EntryGate": MagicMock(return_value=MagicMock()),
        "WebhookReceiver": MagicMock(return_value=MagicMock(**{
            "app": MagicMock(**{"run": MagicMock()}),
        })),
        "_write_session": MagicMock(),
        "_install_signal_handlers": MagicMock(
            side_effect=lambda: _main_module._shutdown_event.set()
        ),
        "time": MagicMock(sleep=MagicMock()),
        "is_trading_day": MagicMock(return_value=True),
        "next_trading_day": MagicMock(),
        "is_token_valid": MagicMock(return_value=True),
        "load_token": MagicMock(return_value={
            "access_token": "fake_live_token",
            "account_id": "LFL836",
        }),
        "AccountRegistry": MagicMock(**{"load.return_value": _make_mock_account_registry()}),
    }

    if extra:
        patches.update(extra)
    return patches


_FAKE_ENV = {
    "ZERODHA_API_KEY": "fake_api_key",
    "ZERODHA_ACCESS_TOKEN": "fake_access_token",
    "TELEGRAM_BOT_TOKEN": "fake_bot_token",
    "TELEGRAM_CHANNEL_PRIMARY": "-100111222333",
    "WEBHOOK_SECRET": "fake_secret",
}


def _run_main(argv=None, extra_patches=None):
    """Helper: reset shutdown event, apply all patches, run main()."""
    _main_module._shutdown_event.clear()
    patches = _make_all_patches(extra=extra_patches)
    with patch.dict("os.environ", _FAKE_ENV), patch.multiple("main", **patches):
        return _main_module.main(argv or ["--mode", "paper"]), patches


# ─────────────────────────────────────────────────────────────────────────────
# 1. CLI parsing (MAIN2)
# ─────────────────────────────────────────────────────────────────────────────

class TestParseArgs:

    def test_defaults(self):
        args = _parse_args([])
        assert args.mode == "paper"
        assert args.resume is False
        assert args.config is None
        assert args.status is False
        assert args.dry_run is False
        assert args.version is False

    def test_mode_paper(self):
        assert _parse_args(["--mode", "paper"]).mode == "paper"

    def test_mode_live(self):
        assert _parse_args(["--mode", "live"]).mode == "live"

    def test_mode_invalid_raises_system_exit(self):
        import pytest
        with pytest.raises(SystemExit):
            _parse_args(["--mode", "INVALID"])

    def test_resume_flag(self):
        assert _parse_args(["--resume"]).resume is True

    def test_config_path(self):
        assert _parse_args(["--config", "/some/path"]).config == "/some/path"

    def test_status_flag(self):
        assert _parse_args(["--status"]).status is True

    def test_dry_run_flag(self):
        assert _parse_args(["--dry-run"]).dry_run is True

    def test_version_flag(self):
        assert _parse_args(["--version"]).version is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. Helper functions
# ─────────────────────────────────────────────────────────────────────────────

class TestHelpers:

    def test_load_holidays_returns_date_set(self):
        cfg = MagicMock()
        cfg.nse_holidays.holidays = [
            HolidayEntry(date=date(2026, 1, 26), name="Republic Day"),
            HolidayEntry(date=date(2026, 8, 15), name="Independence Day"),
        ]
        result = _load_holidays(cfg)
        assert date(2026, 1, 26) in result
        assert date(2026, 8, 15) in result
        assert len(result) == 2

    def test_load_holidays_empty(self):
        cfg = MagicMock()
        cfg.nse_holidays.holidays = []
        assert _load_holidays(cfg) == set()

    def test_version_constant_is_string(self):
        assert isinstance(VERSION, str) and VERSION

    def test_print_status_returns_zero(self):
        store = _make_mock_store()
        ks = _make_mock_kill_switch()
        scenario_result = _make_mock_scenario_result("COLD")
        assert _print_status(store, ks, scenario_result) == 0

    def test_print_status_does_not_start_threads(self):
        # _print_status must not attempt to construct or start subsystems
        store = _make_mock_store()
        ks = _make_mock_kill_switch()
        scenario_result = _make_mock_scenario_result("COLD")
        rc = _print_status(store, ks, scenario_result)
        assert rc == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. Callbacks (MAIN18)
# ─────────────────────────────────────────────────────────────────────────────

class TestCallbacks:

    def test_critical_failure_triggers_soft_kill(self):
        ks = _make_mock_kill_switch()
        cb = _make_critical_failure_cb(ks, MagicMock())
        cb("live_feed", "connection dropped")
        ks.soft_kill.assert_called_once()
        assert "live_feed" in str(ks.soft_kill.call_args)

    def test_critical_failure_sends_alert(self):
        ks = _make_mock_kill_switch()
        notifier = MagicMock()
        cb = _make_critical_failure_cb(ks, notifier)
        cb("live_feed", "connection dropped")
        notifier.send.assert_called_once()
        assert notifier.send.call_args[1]["severity"] == "CRITICAL"

    def test_critical_failure_no_notifier_safe(self):
        ks = _make_mock_kill_switch()
        cb = _make_critical_failure_cb(ks, None)
        cb("live_feed", "connection dropped")  # must not raise
        ks.soft_kill.assert_called_once()

    def test_orphan_cb_triggers_soft_kill_and_alert(self):
        ks = _make_mock_kill_switch()
        notifier = MagicMock()
        cb = _make_orphan_cb(ks, notifier)
        cb("ord_abc", "never filled")
        ks.soft_kill.assert_called_once()
        notifier.send.assert_called_once()
        assert notifier.send.call_args[1]["severity"] == "CRITICAL"

    def test_gate_release_price_hit_calls_continue_from_gate(self):
        sp = MagicMock(name="SignalProcessor")
        cb = _make_gate_release_cb(sp)
        entry = MagicMock(symbol="RELIANCE", signal_id="sig_001")
        cb(entry, "PRICE_HIT")
        sp.continue_from_gate.assert_called_once_with(entry)

    def test_gate_release_timeout_no_action(self):
        sp = MagicMock(name="SignalProcessor")
        cb = _make_gate_release_cb(sp)
        cb(MagicMock(symbol="REL", signal_id="s1"), "TIMEOUT")
        sp.continue_from_gate.assert_not_called()

    def test_gate_release_quote_unavailable_no_action(self):
        sp = MagicMock(name="SignalProcessor")
        cb = _make_gate_release_cb(sp)
        cb(MagicMock(symbol="REL", signal_id="s1"), "QUOTE_UNAVAILABLE")
        sp.continue_from_gate.assert_not_called()

    def test_log_kill_switch_event_no_exception(self):
        ev = MagicMock(previous_state="INACTIVE", new_state="SOFT_KILL", reason="test")
        _log_kill_switch_event(ev)  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# 4. main() phase sequencing
# ─────────────────────────────────────────────────────────────────────────────

def _run(argv=None, extra=None):
    """Run main() with fake env vars + all patches. Returns exit code."""
    _main_module._shutdown_event.clear()
    patches = _make_all_patches(extra=extra)
    with patch.dict("os.environ", _FAKE_ENV), patch.multiple("main", **patches):
        return _main_module.main(argv or ["--mode", "paper"])


class TestMainPhaseSequencing:

    def test_version_flag_exits_0(self):
        _main_module._shutdown_event.clear()
        with patch.dict("os.environ", _FAKE_ENV), \
             patch("main.setup_logging"), \
             patch("main.get_logger", return_value=_make_mock_logger()):
            rc = _main_module.main(["--version"])
        assert rc == 0

    def test_invalid_mode_returns_5(self):
        _main_module._shutdown_event.clear()
        rc = _main_module.main(["--mode", "INVALID"])
        assert rc == 5

    def test_logger_setup_before_config_load(self):
        call_order = []
        _main_module._shutdown_event.clear()
        patches = _make_all_patches()
        patches["setup_logging"] = MagicMock(
            side_effect=lambda *a, **kw: call_order.append("setup_logging")
        )
        patches["load_all"] = MagicMock(
            side_effect=lambda *a, **kw: (
                call_order.append("load_all"), _make_mock_app_config()
            )[1]
        )
        with patch.dict("os.environ", _FAKE_ENV), patch.multiple("main", **patches):
            _main_module.main(["--mode", "paper"])
        assert call_order.index("setup_logging") < call_order.index("load_all")

    def test_halt_without_resume_returns_4(self):
        rc = _run(extra={
            "detect_startup_scenario": MagicMock(
                return_value=_make_mock_scenario_result("HALT")
            ),
            "KillSwitch": MagicMock(return_value=_make_mock_kill_switch("HARD_KILL")),
        })
        assert rc == 4

    def test_halt_with_resume_calls_resume(self):
        ks = _make_mock_kill_switch("HARD_KILL")
        ks.is_active.return_value = False  # cleared after resume
        rc = _run(argv=["--mode", "paper", "--resume"], extra={
            "detect_startup_scenario": MagicMock(
                return_value=_make_mock_scenario_result("HALT")
            ),
            "KillSwitch": MagicMock(return_value=ks),
        })
        ks.resume.assert_called_once()
        assert "--resume" in ks.resume.call_args[1].get("reason", "")

    def test_startup_checks_failure_returns_3(self):
        rc = _run(extra={
            "run_all_startup_checks": MagicMock(
                return_value=MagicMock(
                    ok=False, blocking_failures=["missing_secrets"], warnings=[]
                )
            ),
        })
        assert rc == 3

    def test_dry_run_ok_exits_0(self):
        rc = _run(argv=["--mode", "paper", "--dry-run"])
        assert rc == 0

    def test_dry_run_fail_exits_3(self):
        rc = _run(argv=["--mode", "paper", "--dry-run"], extra={
            "run_all_startup_checks": MagicMock(
                return_value=MagicMock(
                    ok=False, blocking_failures=["clock_skew"], warnings=[]
                )
            ),
        })
        assert rc == 3

    def test_config_hash_change_sends_warn_alert(self):
        notifier = MagicMock()
        rc = _run(extra={
            "check_config_hash": MagicMock(
                return_value=MagicMock(
                    changed=True, changed_files=["system_config.yaml"]
                )
            ),
            "TelegramNotifier": MagicMock(return_value=notifier),
        })
        assert rc == 0
        warn_calls = [
            c for c in notifier.send.call_args_list
            if c[1].get("severity") == "WARN"
        ]
        assert len(warn_calls) >= 1

    def test_crash_scenario_writes_crash_detected_event(self):
        store_mock = _make_mock_store()
        rc = _run(extra={
            "detect_startup_scenario": MagicMock(
                return_value=_make_mock_scenario_result("CRASH")
            ),
            "StateStore": MagicMock(return_value=store_mock),
        })
        assert rc == 0
        event_types = [
            c[1].get("event_type") for c in store_mock.insert_system_event.call_args_list
        ]
        assert "CRASH_DETECTED" in event_types


# ─────────────────────────────────────────────────────────────────────────────
# 4a. I.4 / 2026-04-25 audit: validate selected_account.broker == "zerodha"
# ─────────────────────────────────────────────────────────────────────────────


class TestI4BrokerValidation:
    """I.4: AccountRow.broker is read from accounts.csv but never validated
    downstream. A typo (e.g. 'Zerodha' with capital Z, or 'icici') would
    silently route through the Zerodha adapter and die with a cryptic
    later error. main() now exits 9 with a helpful message."""

    def _registry_with_broker(self, broker: str):
        """Build an account registry mock whose primary().broker is `broker`."""
        mock_acct = MagicMock()
        mock_acct.account_id = "TEST123"
        mock_acct.broker = broker
        mock_acct.label = "Test"
        mock_acct.is_primary = True
        mock_acct.api_key_env = "ZERODHA_API_KEY"
        mock_acct.api_secret_env = "ZERODHA_API_SECRET"
        mock_acct.paper_capital = 50_000.0
        mock_acct.enabled = True
        mock_reg = MagicMock()
        mock_reg.primary.return_value = mock_acct
        mock_reg.count.return_value = 1
        mock_reg.get_enabled_accounts.return_value = [mock_acct]
        return mock_reg

    def test_unsupported_broker_returns_9(self):
        rc = _run(extra={
            "AccountRegistry": MagicMock(**{
                "load.return_value": self._registry_with_broker("upstox")
            }),
        })
        assert rc == 9, f"Expected exit 9 for upstox broker, got {rc}"

    def test_capital_z_zerodha_normalized_and_accepted(self):
        """I.4: case-insensitive comparison; 'Zerodha' (capital Z) accepted."""
        rc = _run(extra={
            "AccountRegistry": MagicMock(**{
                "load.return_value": self._registry_with_broker("Zerodha")
            }),
        })
        assert rc == 0, f"Expected exit 0 for 'Zerodha', got {rc}"

    def test_empty_broker_returns_9(self):
        """I.4: empty/whitespace broker rejected (catches accidentally
        blank cell in accounts.csv)."""
        rc = _run(extra={
            "AccountRegistry": MagicMock(**{
                "load.return_value": self._registry_with_broker("")
            }),
        })
        assert rc == 9


# ─────────────────────────────────────────────────────────────────────────────
# 4b. BL-15: WEBHOOK_SECRET required in live mode, not in paper
# ─────────────────────────────────────────────────────────────────────────────

class TestBl15WebhookSecretRequired:
    """Audit BL-15: live mode must validate HMAC on every webhook, which
    requires WEBHOOK_SECRET to be set. Paper stays permissive."""

    @staticmethod
    def _capture_startup_call():
        captured = {}
        def _sideeffect(*args, **kwargs):
            captured.update(kwargs)
            return MagicMock(ok=True, blocking_failures=[], warnings=[])
        return captured, _sideeffect

    def test_live_mode_adds_webhook_secret_to_required_secrets(self):
        captured, sideeffect = self._capture_startup_call()
        rc = _run(argv=["--mode", "live"], extra={
            "run_all_startup_checks": MagicMock(side_effect=sideeffect),
        })
        assert rc == 0
        assert "WEBHOOK_SECRET" in captured["required_secrets"]

    def test_paper_mode_does_not_require_webhook_secret(self):
        captured, sideeffect = self._capture_startup_call()
        rc = _run(argv=["--mode", "paper"], extra={
            "run_all_startup_checks": MagicMock(side_effect=sideeffect),
        })
        assert rc == 0
        assert "WEBHOOK_SECRET" not in captured["required_secrets"]

    def test_live_mode_keeps_original_secrets(self):
        """BL-15 must append, not replace -- other keys stay required.

        Post account-registry refactor: API key/secret env vars are namespaced
        by account_id (e.g. ZERODHA_API_KEY_LFL836). TELEGRAM_BOT_TOKEN and
        WEBHOOK_SECRET remain shared globals.
        """
        captured, sideeffect = self._capture_startup_call()
        _run(argv=["--mode", "live"], extra={
            "run_all_startup_checks": MagicMock(side_effect=sideeffect),
        })
        required = captured["required_secrets"]
        assert any(k.startswith("ZERODHA_API_KEY_") for k in required)
        assert any(k.startswith("ZERODHA_API_SECRET_") for k in required)
        assert "TELEGRAM_BOT_TOKEN" in required
        assert "WEBHOOK_SECRET" in required


# ─────────────────────────────────────────────────────────────────────────────
# 5. Subsystem wiring (MAIN8-MAIN9)
# ─────────────────────────────────────────────────────────────────────────────

class TestSubsystemWiring:

    def test_event_bus_subscriptions_registered(self):
        bus_mock = MagicMock()
        rc = _run(extra={"EventBus": MagicMock(return_value=bus_mock)})
        assert rc == 0
        assert bus_mock.subscribe.call_count >= 2

    def test_fund_manager_initialized_with_paper_capital(self):
        # SU19: paper mode uses selected_account.paper_capital, not broker net.
        # F.1 / H-26: paper_capital is 50_000 (matches live start) -- fixture mirrors.
        fm_mock = MagicMock()
        fm_mock.total = 50_000.0  # F.1 / EF-7: satisfy consistency check
        _run(extra={"FundManager": MagicMock(return_value=fm_mock)})
        fm_mock.initialize.assert_called_once_with(50_000.0)

    def test_live_feed_reconnect_chain_wired(self):
        lf_mock = MagicMock()
        _run(extra={"LiveFeedManager": MagicMock(return_value=lf_mock)})
        lf_mock.set_on_reconnect_callback.assert_called_once()

    def test_paper_mode_skips_kite_client_build(self):
        build_kite = MagicMock()
        adapter_cls = MagicMock(return_value=MagicMock(**{
            # F.1 / EF-7: align with paper_capital so EF-7 check passes.
            "get_margins.return_value": MagicMock(net=50_000.0),
            "get_quote": MagicMock(),
        }))
        _run(extra={"ZerodhaAdapter": adapter_cls, "_build_kite_client": build_kite})
        build_kite.assert_not_called()
        assert adapter_cls.call_args[1].get("paper_mode") is True

    def test_shadow_tracker_constructed_and_wired(self):
        st_mock = MagicMock()
        _run(extra={"ShadowTracker": MagicMock(return_value=st_mock)})
        st_mock.set_instrument_cache.assert_called_once()

    def test_startup_reconciliation_called_before_threads(self):
        reconciler_mock = MagicMock()
        reconciler_mock.reconcile_once.return_value = []
        _run(extra={"OrderReconciler": MagicMock(return_value=reconciler_mock)})
        reconciler_mock.reconcile_once.assert_called()

    def test_reconciler_kill_switch_active_after_recon_exits_1(self):
        ks = MagicMock()
        ks.is_active.return_value = True
        ks.current_state.return_value = MagicMock(value="SOFT_KILL")
        ks.status.return_value = {"state": "SOFT_KILL", "reason": "", "triggered_at": None}
        reconciler_mock = MagicMock()
        reconciler_mock.reconcile_once.return_value = []
        rc = _run(extra={
            "KillSwitch": MagicMock(return_value=ks),
            "OrderReconciler": MagicMock(return_value=reconciler_mock),
        })
        assert rc == 1


# ─────────────────────────────────────────────────────────────────────────────
# 6. Start sequence (MAIN11)
# ─────────────────────────────────────────────────────────────────────────────

class TestStartSequence:

    def test_live_feed_connect_called(self):
        lf_mock = MagicMock()
        _run(extra={"LiveFeedManager": MagicMock(return_value=lf_mock)})
        lf_mock.connect.assert_called_once()

    def test_order_monitor_start_called(self):
        om_mock = MagicMock()
        _run(extra={"OrderMonitor": MagicMock(return_value=om_mock)})
        om_mock.start.assert_called_once()

    def test_signal_processor_start_called(self):
        sp_mock = MagicMock()
        _run(extra={"SignalProcessor": MagicMock(return_value=sp_mock)})
        sp_mock.start.assert_called_once()

    def test_startup_system_event_written(self):
        store_mock = _make_mock_store()
        _run(extra={"StateStore": MagicMock(return_value=store_mock)})
        event_types = [
            c[1].get("event_type") for c in store_mock.insert_system_event.call_args_list
        ]
        assert "STARTUP" in event_types

    def test_startup_info_notification_sent(self):
        notifier = MagicMock()
        _run(extra={"TelegramNotifier": MagicMock(return_value=notifier)})
        # New format: title is "[MODE] 🚀 System Active | MODE Mode"
        info_starts = [
            c for c in notifier.send.call_args_list
            if c[1].get("severity") == "INFO"
            and "system active" in c[1].get("title", "").lower()
        ]
        assert len(info_starts) >= 1

    def test_webhook_health_check_performed(self):
        wh_check = MagicMock(return_value=MagicMock(reachable=True))
        _run(extra={"check_webhook_endpoint": wh_check})
        wh_check.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 7. Shutdown (MAIN15)
# ─────────────────────────────────────────────────────────────────────────────

class TestShutdown:

    def test_shutdown_event_written(self):
        store_mock = _make_mock_store()
        rc = _run(extra={"StateStore": MagicMock(return_value=store_mock)})
        assert rc == 0
        event_types = [
            c[1].get("event_type") for c in store_mock.insert_system_event.call_args_list
        ]
        assert "SHUTDOWN" in event_types

    def test_signal_processor_stop_called(self):
        sp_mock = MagicMock()
        _run(extra={"SignalProcessor": MagicMock(return_value=sp_mock)})
        sp_mock.stop.assert_called()

    def test_store_closed_on_shutdown(self):
        store_mock = _make_mock_store()
        _run(extra={"StateStore": MagicMock(return_value=store_mock)})
        store_mock.close.assert_called()

    def test_clean_shutdown_returns_0(self):
        assert _run() == 0


# ─────────────────────────────────────────────────────────────────────────────
# 8. Status mode (MAIN16)
# ─────────────────────────────────────────────────────────────────────────────

class TestStatusMode:

    def test_status_mode_exits_0(self):
        rc = _run(argv=["--status"])
        assert rc == 0

    def test_status_mode_does_not_start_subsystems(self):
        sp_cls = MagicMock(return_value=MagicMock())
        lf_cls = MagicMock(return_value=MagicMock())
        rc = _run(argv=["--status"], extra={
            "SignalProcessor": sp_cls,
            "LiveFeedManager": lf_cls,
        })
        assert rc == 0
        sp_cls.assert_not_called()
        lf_cls.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 9. continue_from_gate() in SignalProcessor (MAIN18)
# ─────────────────────────────────────────────────────────────────────────────

class TestContinueFromGate:

    def _make_processor(self, order_placer=None):
        from signals.signal_processor import SignalProcessor

        strategy = MagicMock()
        strategy.intent = "INTRADAY"
        strategy.lot_size = 1
        strategy.direction = "LONG"

        store = MagicMock()
        ks = MagicMock()
        ks.is_active.return_value = False

        mw = MagicMock()
        mw.is_entry_allowed.return_value = True

        reservation = MagicMock(success=True, reservation_id="rsv_001")
        fm = MagicMock()
        fm.reserve.return_value = reservation

        sizing = MagicMock(success=True, qty=10)
        sizer = MagicMock()
        sizer.calculate.return_value = sizing

        approval = MagicMock(approved=True)
        risk = MagicMock()
        risk.approve.return_value = approval

        return SignalProcessor(
            signal_queue=MagicMock(),
            state_store=store,
            bus=MagicMock(),
            fund_manager=fm,
            position_sizer=sizer,
            risk_engine=risk,
            kill_switch=ks,
            market_windows=mw,
            strategies={"test_strategy": strategy},
            scan_webhook_map={},
            secondary_screener=MagicMock(),
            quality_scorer=MagicMock(),
            order_placer=order_placer,
            logger=_make_mock_logger(),
        )

    def _make_watch_entry(self):
        entry = MagicMock()
        entry.signal_id = "sig_gate_001"
        entry.symbol = "RELIANCE"
        entry.direction = "LONG"
        entry.entry_price = 2500.0
        entry.sl_price = 2450.0
        entry.tgt_price = 2600.0
        entry.tier = "A"
        entry.strategy_name = "test_strategy"
        return entry

    def test_price_hit_calls_placer_with_correct_prices(self):
        placer = MagicMock()
        proc = self._make_processor(order_placer=placer)
        entry = self._make_watch_entry()
        proc.continue_from_gate(entry)
        placer.place.assert_called_once()
        kw = placer.place.call_args[1]
        assert kw["symbol"] == "RELIANCE"
        assert kw["entry_price"] == 2500.0
        assert kw["sl_price"] == 2450.0
        assert kw["tgt_price"] == 2600.0

    def test_kill_switch_active_rejects_with_status_update(self):
        proc = self._make_processor()
        proc._ks.is_active.return_value = True
        entry = self._make_watch_entry()
        proc.continue_from_gate(entry)
        call_args = [str(c) for c in proc._store.update_signal_status.call_args_list]
        assert any("REJECTED_KILL_SWITCH" in c for c in call_args)

    def test_unknown_strategy_rejects(self):
        proc = self._make_processor()
        entry = self._make_watch_entry()
        entry.strategy_name = "does_not_exist"
        proc.continue_from_gate(entry)
        call_args = [str(c) for c in proc._store.update_signal_status.call_args_list]
        assert any("REJECTED_UNKNOWN_STRATEGY" in c for c in call_args)

    def test_no_placer_releases_reservation_and_updates_status(self):
        proc = self._make_processor(order_placer=None)
        entry = self._make_watch_entry()
        proc.continue_from_gate(entry)
        proc._fm.release.assert_called()
        call_args = [str(c) for c in proc._store.update_signal_status.call_args_list]
        assert any("PROCESSED_NO_PLACER" in c for c in call_args)

    def test_sizing_failure_rejects(self):
        proc = self._make_processor()
        proc._sizer.calculate.return_value = MagicMock(
            success=False, constraint="MIN_QTY", reason="below threshold"
        )
        entry = self._make_watch_entry()
        proc.continue_from_gate(entry)
        call_args = [str(c) for c in proc._store.update_signal_status.call_args_list]
        assert any("REJECTED" in c for c in call_args)

    def test_risk_rejection_does_not_release_reservation(self):
        proc = self._make_processor()
        proc._risk.approve.return_value = MagicMock(
            approved=False, failed_check="MAX_OPEN_POSITIONS", reason="cap hit"
        )
        entry = self._make_watch_entry()
        proc.continue_from_gate(entry)
        # No reservation was made before risk check
        proc._fm.release.assert_not_called()

    def test_stats_placed_incremented_on_success(self):
        placer = MagicMock()
        proc = self._make_processor(order_placer=placer)
        entry = self._make_watch_entry()
        initial = proc._stats["placed"]
        proc.continue_from_gate(entry)
        assert proc._stats["placed"] == initial + 1
        assert proc._stats["processed"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    suites = [
        TestParseArgs,
        TestHelpers,
        TestCallbacks,
        TestMainPhaseSequencing,
        TestSubsystemWiring,
        TestStartSequence,
        TestShutdown,
        TestStatusMode,
        TestContinueFromGate,
    ]

    total = passed = failed = 0
    for suite_cls in suites:
        suite = suite_cls()
        methods = [m for m in dir(suite) if m.startswith("test_")]
        for method in methods:
            total += 1
            try:
                _main_module._shutdown_event.clear()
                getattr(suite, method)()
                passed += 1
                print(f"  PASS  {suite_cls.__name__}.{method}")
            except Exception as exc:
                failed += 1
                print(f"  FAIL  {suite_cls.__name__}.{method}: {exc}")

    print(f"\nResults: {passed}/{total} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
