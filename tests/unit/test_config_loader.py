"""
tests/unit/test_config_loader.py

Validates core/config_loader.py against CL1–CL6 locked decisions:
  - load_all() with all 8 valid stubs → AppConfig fully populated
  - all 8 sub-configs present with correct types
  - missing file → ConfigMissingError with filename in context
  - malformed YAML → ConfigSchemaError
  - extra unknown key in YAML → ConfigSchemaError (CL3 extra="forbid")
  - missing required field → ConfigSchemaError
  - wrong type for a field → ConfigSchemaError
  - file_hashes populated for all 8 files (CL4)
  - hash values are 64-char hex strings (SHA-256)
  - hash changes when file content changes (CL4)
  - load_all() with custom config_dir works (CL1)
  - ConfigMissingError and ConfigSchemaError are TradingSystemError subtypes
  - empty scanners dict is valid (scan_webhook_map, chartink_scanners stubs)
  - invalid date format in nse_holidays raises ConfigSchemaError

All tests use tempfile.TemporaryDirectory for isolation; no dependency on
the real config/ directory.

Run: python -m pytest tests/unit/test_config_loader.py -v
Or:  python tests/unit/test_config_loader.py  (standalone mode)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.config_loader import (
    _CONFIG_FILES,
    AlertsConfig,
    AppConfig,
    OrderReconcilerConfig,
    BrokerCostsConfig,
    BrokerLimitsConfig,
    ChartinkScannersConfig,
    EodSquareoffConfig,
    HolidayEntry,
    NseHolidaysConfig,
    ScanWebhookMapConfig,
    ScannerEntry,
    ScoringConfig,
    ShadowTrackerConfig,
    SlippageConfig,
    SmartTgtConfig,
    SmtpConfig,
    SystemConfig,
    TelegramChannelConfig,
    TelegramConfig,
    load_all,
)
from core.exceptions import ConfigMissingError, ConfigSchemaError, TradingSystemError


# ─────────────────────────────────────────────────────────────────────────────
# Stub YAML content — minimal-valid, matches each schema exactly
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_CONFIG = """\
trading_hours:
  entry_start: "09:30"
  entry_end: "13:30"
  eod_entry_cutoff: "15:15"
  eod_squareoff_time: "15:17"
signal_queue:
  capacity: 300
  backpressure_pct: 0.80
  expiry_sec: 60
product_map:
  zerodha:
    INTRADAY: "MIS"
    DELIVERY: "CNC"
    COVER_ORDER: "CO"
    BRACKET_ORDER: ""
clock:
  warn_skew_sec: 2.0
  alert_skew_sec: 5.0
  halt_skew_sec: 30.0
  startup_max_skew_sec: 30.0
order_monitor:
  poll_interval_sec: 2
  fill_timeout_sec: 60
capital:
  intraday_bucket_pct: 0.70
  positional_bucket_pct: 0.30
  leverage_map:
    INTRADAY: 5.0
    COVER_ORDER: 6.0
    DELIVERY: 1.0
    BRACKET_ORDER: 5.0
position_sizing:
  risk_per_trade_pct: 0.01
  max_concentration_pct: 0.10
  min_qty_threshold: 1
  lot_skew_rejection_threshold: 0.25
  min_tick_size: 0.05
  max_single_order_qty: 10000
  max_position_value_pct: 0.40
  tier_multipliers:
    HIGH: 1.0
    MEDIUM: 0.70
    LOW: 0.50
risk:
  max_open_positions: 10
  max_daily_trades: 20
  max_sector_exposure_pct: 0.40
  max_consecutive_losses: 4
  daily_loss_limit_pct: 0.05
signal_processor:
  worker_count: 5
  drain_poll_sec: 0.1
  pipeline_timeout_sec: 30
kill_switch:
  api_failure_threshold: 3
  enable_auto_trip: true
webhook:
  bind_host: "127.0.0.1"
  bind_port: 5000
  require_hmac: true
eod_squareoff:
  inter_order_delay_ms: 500
  poll_interval_sec: 5
  auto_resume_kill_switch: true
alerts:
  failed_alerts_log_path: "logs/failed_alerts.log"
  sentinel_dir: "data_store"
  watcher_max_attempts: 5
  watcher_lock_path: "data_store/alert_watcher.lock"
  watcher_log_path: "logs/alert_watcher.log"
  telegram:
    bot_token_env: "TELEGRAM_BOT_TOKEN"
    channels:
      - chat_id_env: "TELEGRAM_CHANNEL_PRIMARY"
        label: "Primary Alert Channel"
        enabled: true
      - chat_id_env: "TELEGRAM_CHANNEL_SECONDARY"
        label: "Secondary Alert Channel"
        enabled: false
    personal_chat_id_env: "TELEGRAM_PERSONAL_CHAT_ID"
    whitelist_only: true
  smtp:
    host: "smtp.gmail.com"
    port: 587
    use_tls: true
    username: "alerts@example.com"
    password: "app-password-here"
    from_address: "alerts@example.com"
    to_addresses:
      - "operator@example.com"
    timeout_sec: 30

order_reconciler:
  poll_interval_sec: 15
  capital_drift_tolerance: 50.0
shadow_tracker:
  enabled: true
  max_innings: 3
  alert_per_inning: true
smart_tgt:
  enabled: true
  trigger_pct: 0.005
  step_pct: 0.003
  volume_dependent_trails: false
entry_gate:
  slippage_buffer: 2.0
paper:
  auto_fill_delay_sec: 0.5
drift_handler:
  log_only_threshold_rs: 250.0
  soft_kill_threshold_rs: 1000.0
  hard_kill_threshold_rs: 2500.0
  consecutive_cycles_before_escalate: 3
logging:
  min_free_disk_gb: 2.0
"""

_BROKER_COSTS = """\
zerodha:
  brokerage_flat_intraday: 20.0
  brokerage_pct_intraday: 0.03
  stt_sell_pct: 0.025
  stt_cnc_pct: 0.1
  exchange_txn_pct: 0.00297
  gst_pct: 18.0
  sebi_pct: 0.0001
  stamp_duty_mis_buy_pct: 0.003
  stamp_duty_cnc_buy_pct: 0.015
  futures:
    stt_pct: 0.0125
    stamp_duty_buy_pct: 0.002
  options_buy:
    stt_pct: 0.0
    stamp_duty_pct: 0.003
  options_sell:
    stt_pct: 0.0625
    stamp_duty_pct: 0.0
"""

_BROKER_LIMITS = """\
order:
  burst: 8
  rate_per_sec: 8
quote:
  burst: 1
  rate_per_sec: 1
historical:
  burst: 2
  rate_per_sec: 2
margins:
  burst: 8
  rate_per_sec: 8
backoff_sequence_sec:
  - 1
  - 5
  - 30
timeouts:
  connect_sec: 5
  read_sec: 10
"""

_SLIPPAGE = """\
tiers:
  liquid:
    slippage_bps: 5
  mid:
    slippage_bps: 15
  small:
    slippage_bps: 30
default_tier: liquid
"""

_SCORING = """\
steps:
  volume_surge: 15
  vwap_position: 10
  atr_filter: 10
  rsi_range: 10
  price_action: 15
  sector_strength: 10
  time_of_day: 5
  spread_check: 5
  circuit_check: 10
  signal_age: 10
min_pass_score: 60
high_score_threshold: 80
medium_score_threshold: 65
"""

_SCAN_WEBHOOK_MAP = """\
scanners: {}
"""

_CHARTINK_SCANNERS = """\
scanners: {}
"""

_NSE_HOLIDAYS = """\
holidays:
  - date: "2026-01-26"
    name: "Republic Day"
  - date: "2026-08-15"
    name: "Independence Day"
  - date: "2026-12-25"
    name: "Christmas"
"""

# Ordered to match _CONFIG_FILES registry in config_loader
_ALL_STUBS: dict[str, str] = {
    "system_config.yaml":    _SYSTEM_CONFIG,
    "broker_costs.yaml":     _BROKER_COSTS,
    "broker_limits.yaml":    _BROKER_LIMITS,
    "slippage_model.yaml":   _SLIPPAGE,
    "scoring_weights.yaml":  _SCORING,
    "scan_webhook_map.yaml": _SCAN_WEBHOOK_MAP,
    "chartink_scanners.yaml":_CHARTINK_SCANNERS,
    "nse_holidays_2026.yaml":_NSE_HOLIDAYS,
}


def _write_stubs(config_dir: Path, overrides: dict[str, str] | None = None) -> None:
    """Write all stub YAML files to config_dir, applying any overrides."""
    stubs = dict(_ALL_STUBS)
    if overrides:
        stubs.update(overrides)
    for filename, content in stubs.items():
        (config_dir / filename).write_text(content, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — happy path
# ─────────────────────────────────────────────────────────────────────────────

def test_load_all_valid_stubs_returns_app_config() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    assert isinstance(cfg, AppConfig)
    print("  OK load_all() returns AppConfig for valid stubs")


def test_all_8_sub_configs_present() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    assert isinstance(cfg.system,            SystemConfig)
    assert isinstance(cfg.broker_costs,      BrokerCostsConfig)
    assert isinstance(cfg.broker_limits,     BrokerLimitsConfig)
    assert isinstance(cfg.slippage,          SlippageConfig)
    assert isinstance(cfg.scoring,           ScoringConfig)
    assert isinstance(cfg.scan_webhook_map,  ScanWebhookMapConfig)
    assert isinstance(cfg.chartink_scanners, ChartinkScannersConfig)
    assert isinstance(cfg.nse_holidays,      NseHolidaysConfig)
    print("  OK All 8 sub-configs present with correct types")


def test_system_config_values_match_stubs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    assert cfg.system.signal_queue.capacity == 300
    assert cfg.system.signal_queue.backpressure_pct == 0.80
    assert cfg.system.trading_hours.entry_start == "09:30"
    assert cfg.system.trading_hours.eod_squareoff_time == "15:17"
    assert cfg.system.product_map["zerodha"]["INTRADAY"] == "MIS"
    assert cfg.system.product_map["zerodha"]["COVER_ORDER"] == "CO"
    assert cfg.system.product_map["zerodha"]["BRACKET_ORDER"] == ""
    assert cfg.system.clock.halt_skew_sec == 30.0
    assert cfg.system.order_monitor.poll_interval_sec == 2
    assert cfg.system.order_monitor.fill_timeout_sec == 60
    assert cfg.system.capital.intraday_bucket_pct == 0.70
    assert cfg.system.capital.positional_bucket_pct == 0.30
    # BUILD 1 (#1): capital.daily_loss_limit deleted — daily_loss_limit_pct is sole authority
    assert not hasattr(cfg.system.capital, "daily_loss_limit")
    assert cfg.system.capital.leverage_map.INTRADAY == 5.0
    assert cfg.system.capital.leverage_map.DELIVERY == 1.0
    assert cfg.system.position_sizing.risk_per_trade_pct == 0.01
    assert cfg.system.position_sizing.max_concentration_pct == 0.10
    assert cfg.system.position_sizing.min_qty_threshold == 1
    assert cfg.system.position_sizing.tier_multipliers.HIGH == 1.0
    assert cfg.system.position_sizing.tier_multipliers.MEDIUM == 0.70
    assert cfg.system.position_sizing.tier_multipliers.LOW == 0.50
    print("  OK SystemConfig values match stub YAML (incl. order_monitor, capital, position_sizing)")


def test_eod_squareoff_config_values_match_stubs() -> None:
    """EodSquareoffConfig section parses correctly with correct field types (EOD12)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    assert isinstance(cfg.system.eod_squareoff, EodSquareoffConfig)
    assert cfg.system.eod_squareoff.inter_order_delay_ms == 500
    assert cfg.system.eod_squareoff.poll_interval_sec == 5
    assert cfg.system.eod_squareoff.auto_resume_kill_switch is True
    print("  OK EodSquareoffConfig values match stub YAML (EOD12)")


def test_alerts_config_values_match_stubs() -> None:
    """AlertsConfig, TelegramConfig, SmtpConfig parse correctly (TG12, AW11)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    assert isinstance(cfg.system.alerts, AlertsConfig)
    assert cfg.system.alerts.failed_alerts_log_path == "logs/failed_alerts.log"
    assert cfg.system.alerts.sentinel_dir == "data_store"
    assert cfg.system.alerts.watcher_max_attempts == 5
    assert cfg.system.alerts.watcher_lock_path == "data_store/alert_watcher.lock"
    assert cfg.system.alerts.watcher_log_path == "logs/alert_watcher.log"
    # respawn-guard thresholds (16-Jul): stub YAML doesn't set them → proves the byte-identical
    # defaults (== the monitoring-canary historical literals: delta 3, rate 6.0/hr).
    assert cfg.system.alerts.respawn_restart_delta_threshold == 3
    assert cfg.system.alerts.respawn_rate_per_hour_threshold == 6.0

    tg = cfg.system.alerts.telegram
    assert isinstance(tg, TelegramConfig)
    assert tg.bot_token_env == "TELEGRAM_BOT_TOKEN"
    assert len(tg.channels) == 2
    assert tg.channels[0].chat_id_env == "TELEGRAM_CHANNEL_PRIMARY"
    assert tg.channels[0].label == "Primary Alert Channel"
    assert tg.channels[0].enabled is True
    assert tg.channels[1].chat_id_env == "TELEGRAM_CHANNEL_SECONDARY"
    assert tg.channels[1].enabled is False
    assert tg.personal_chat_id_env == "TELEGRAM_PERSONAL_CHAT_ID"
    assert tg.whitelist_only is True

    smtp = cfg.system.alerts.smtp
    assert isinstance(smtp, SmtpConfig)
    assert smtp.host == "smtp.gmail.com"
    assert smtp.port == 587
    assert smtp.use_tls is True
    assert smtp.username == "alerts@example.com"
    assert smtp.from_address == "alerts@example.com"
    assert smtp.to_addresses == ["operator@example.com"]
    assert smtp.timeout_sec == 30
    print("  OK AlertsConfig values match stub YAML (TG12, AW11)")


def test_risk_config_sector_cap_defaults_observe() -> None:
    """F1 (16-Jul): sector_cap_mode defaults to 'observe' (behaviour-neutral) and
    sector_unknown_alert_pct to 0.20 when the YAML omits them (declared safe defaults)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)
    assert cfg.system.risk.sector_cap_mode == "observe"
    assert cfg.system.risk.sector_unknown_alert_pct == 0.20


# ─────────────────────────────────────────────────────────────────────────────
# G.3 / 2026-04-25 audit — SMTP password from env var
# ─────────────────────────────────────────────────────────────────────────────


def test_g3_smtp_resolved_password_prefers_env_var() -> None:
    """G.3: when password_env is set, resolved_password() reads os.environ
    and ignores the plaintext password field."""
    import os
    smtp = SmtpConfig(
        host="smtp.example.com", port=587, use_tls=True,
        username="u", password="ignored-plaintext",
        password_env="TEST_SMTP_PASSWORD_ENV",
        from_address="from@example.com",
        to_addresses=["to@example.com"], timeout_sec=10,
    )
    os.environ["TEST_SMTP_PASSWORD_ENV"] = "from-env-var"
    try:
        assert smtp.resolved_password() == "from-env-var"
    finally:
        del os.environ["TEST_SMTP_PASSWORD_ENV"]
    print("  OK G.3: resolved_password() reads password_env first")


def test_g3_smtp_resolved_password_falls_back_to_plaintext() -> None:
    """G.3: when password_env is unset (dev/test config), resolved_password()
    returns the plaintext password field."""
    smtp = SmtpConfig(
        host="smtp.example.com", port=587, use_tls=True,
        username="u", password="dev-plaintext",
        from_address="from@example.com",
        to_addresses=["to@example.com"], timeout_sec=10,
    )
    assert smtp.resolved_password() == "dev-plaintext"
    print("  OK G.3: resolved_password() falls back to plaintext when password_env empty")


def test_g3_smtp_password_env_set_but_var_missing_raises() -> None:
    """G.3: password_env names a missing/empty env var -> fail-fast at
    resolution time. Catches the case where the operator forgot to export
    the var on a fresh VM before alert_watcher starts."""
    import os
    smtp = SmtpConfig(
        host="smtp.example.com", port=587, use_tls=True,
        username="u", password="",
        password_env="TEST_SMTP_PASSWORD_NOT_EXPORTED",
        from_address="from@example.com",
        to_addresses=["to@example.com"], timeout_sec=10,
    )
    # Make sure it really isn't set.
    os.environ.pop("TEST_SMTP_PASSWORD_NOT_EXPORTED", None)

    raised = False
    try:
        smtp.resolved_password()
    except ValueError as exc:
        raised = True
        assert "TEST_SMTP_PASSWORD_NOT_EXPORTED" in str(exc)
    assert raised, "Expected ValueError for missing env var"
    print("  OK G.3: missing env var raises at resolved_password()")


def test_g3_smtp_neither_password_nor_env_raises_at_construct() -> None:
    """G.3: model validator rejects a config that has neither plaintext nor
    env var — catches accidental empty stanzas at YAML load time."""
    raised = False
    try:
        SmtpConfig(
            host="smtp.example.com", port=587, use_tls=True,
            username="u", password="",
            password_env="",
            from_address="from@example.com",
            to_addresses=["to@example.com"], timeout_sec=10,
        )
    except Exception as exc:  # pydantic ValidationError wraps ValueError
        raised = True
        assert "password" in str(exc).lower()
    assert raised, "Expected validation error"
    print("  OK G.3: empty password + empty password_env rejected at construct")


def test_order_reconciler_config_values_match_stubs() -> None:
    """OrderReconcilerConfig parses correctly from stub YAML (RC17)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    assert isinstance(cfg.system.order_reconciler, OrderReconcilerConfig)
    assert cfg.system.order_reconciler.poll_interval_sec == 15
    assert cfg.system.order_reconciler.capital_drift_tolerance == 50.0
    print("  OK OrderReconcilerConfig values match stub YAML (RC17)")


def test_broker_costs_values_match_stubs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    assert cfg.broker_costs.zerodha.brokerage_flat_intraday == 20.0
    assert cfg.broker_costs.zerodha.brokerage_pct_intraday == 0.03
    assert cfg.broker_costs.zerodha.stt_sell_pct == 0.025
    assert cfg.broker_costs.zerodha.stt_cnc_pct == 0.1
    assert cfg.broker_costs.zerodha.exchange_txn_pct == 0.00297
    assert cfg.broker_costs.zerodha.gst_pct == 18.0
    assert cfg.broker_costs.zerodha.sebi_pct == 0.0001
    assert cfg.broker_costs.zerodha.stamp_duty_mis_buy_pct == 0.003
    assert cfg.broker_costs.zerodha.stamp_duty_cnc_buy_pct == 0.015
    print("  OK BrokerCostsConfig values match stub YAML (all 9 fields)")


def test_broker_limits_values_match_stubs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    assert cfg.broker_limits.order.burst == 8
    assert cfg.broker_limits.order.rate_per_sec == 8
    assert cfg.broker_limits.quote.burst == 1
    assert cfg.broker_limits.backoff_sequence_sec == [1, 5, 30]
    assert cfg.broker_limits.timeouts.connect_sec == 5
    assert cfg.broker_limits.timeouts.read_sec == 10
    print("  OK BrokerLimitsConfig values match stub YAML (incl. timeouts)")


def test_slippage_tiers_present() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    assert cfg.slippage.tiers["liquid"].slippage_bps == 5
    assert cfg.slippage.tiers["mid"].slippage_bps == 15
    assert cfg.slippage.tiers["small"].slippage_bps == 30
    assert cfg.slippage.default_tier == "liquid"
    print("  OK SlippageConfig tiers and default_tier match stub YAML")


def test_scoring_weights_and_thresholds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    assert cfg.scoring.steps.volume_surge == 15
    assert cfg.scoring.steps.signal_age == 10
    assert cfg.scoring.min_pass_score == 60
    # BUILD 1 (#4): scoring-side tier_multipliers deleted (sizer uses system_config)
    assert not hasattr(cfg.scoring, "tier_multipliers")
    assert cfg.scoring.high_score_threshold == 80
    print("  OK ScoringConfig weights and thresholds match stub YAML")


def test_empty_scanners_dict_is_valid() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    assert cfg.scan_webhook_map.scanners == {}
    assert cfg.chartink_scanners.scanners == {}
    print("  OK Empty scanners: {} is valid for both scanner config files")


def test_nse_holidays_loaded_as_list() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    dates = {h.date.isoformat() for h in cfg.nse_holidays.holidays}
    assert "2026-01-26" in dates
    assert "2026-08-15" in dates
    assert len(cfg.nse_holidays.holidays) == 3
    print(f"  OK NseHolidaysConfig loaded {len(cfg.nse_holidays.holidays)} holidays")


def test_load_all_with_custom_config_dir() -> None:
    """CL1: config_dir parameter is honoured; real config/ not touched."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)   # explicit path, not Path("config")

    assert isinstance(cfg, AppConfig)
    print("  OK load_all() with explicit config_dir works correctly")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — ConfigMissingError
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_file_raises_config_missing_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        (d / "broker_costs.yaml").unlink()   # delete one file

        raised = False
        try:
            load_all(d)
        except ConfigMissingError:
            raised = True
    assert raised, "Expected ConfigMissingError for absent file"
    print("  OK Missing file raises ConfigMissingError")


def test_missing_file_context_has_filename() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        (d / "scoring_weights.yaml").unlink()

        try:
            load_all(d)
        except ConfigMissingError as exc:
            assert exc.context["file"] == "scoring_weights.yaml", (
                f"Expected file='scoring_weights.yaml' in context, got {exc.context}"
            )
            assert "path" in exc.context
            print(f"  OK ConfigMissingError.context has file={exc.context['file']!r}")
        else:
            assert False, "ConfigMissingError not raised"


def test_config_missing_error_is_trading_system_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        (d / "system_config.yaml").unlink()

        try:
            load_all(d)
        except TradingSystemError:
            print("  OK ConfigMissingError caught as TradingSystemError")
        else:
            assert False, "Not caught as TradingSystemError"


# ─────────────────────────────────────────────────────────────────────────────
# Tests — ConfigSchemaError (malformed, extra key, missing field, wrong type)
# ─────────────────────────────────────────────────────────────────────────────

def test_malformed_yaml_raises_config_schema_error() -> None:
    bad_yaml = "key: : invalid: [\n"

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d, overrides={"broker_costs.yaml": bad_yaml})

        raised = False
        try:
            load_all(d)
        except ConfigSchemaError:
            raised = True
    assert raised, "Expected ConfigSchemaError for malformed YAML"
    print("  OK Malformed YAML raises ConfigSchemaError")


def test_extra_unknown_key_raises_config_schema_error() -> None:
    """CL3: extra='forbid' — unknown keys must be rejected."""
    extra_key_yaml = _BROKER_COSTS + "unknown_key: should_fail\n"

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d, overrides={"broker_costs.yaml": extra_key_yaml})

        raised = False
        try:
            load_all(d)
        except ConfigSchemaError:
            raised = True
    assert raised, "Expected ConfigSchemaError for extra unknown key"
    print("  OK Extra unknown key raises ConfigSchemaError (extra='forbid')")


def test_missing_required_field_raises_config_schema_error() -> None:
    """Remove 'gst_pct' from zerodha section — required field missing."""
    missing_field_yaml = """\
zerodha:
  brokerage_flat_intraday: 20.0
  brokerage_pct_intraday: 0.03
  stt_sell_pct: 0.025
  stt_cnc_pct: 0.1
  exchange_txn_pct: 0.00297
  sebi_pct: 0.0001
  stamp_duty_mis_buy_pct: 0.003
  stamp_duty_cnc_buy_pct: 0.015
  futures:
    stt_pct: 0.0125
    stamp_duty_buy_pct: 0.002
  options_buy:
    stt_pct: 0.0
    stamp_duty_pct: 0.003
  options_sell:
    stt_pct: 0.0625
    stamp_duty_pct: 0.0
"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d, overrides={"broker_costs.yaml": missing_field_yaml})

        raised = False
        try:
            load_all(d)
        except ConfigSchemaError:
            raised = True
    assert raised, "Expected ConfigSchemaError for missing required field"
    print("  OK Missing required field raises ConfigSchemaError")


def test_wrong_type_raises_config_schema_error() -> None:
    """max_open_positions must be int; 'ten' is not valid."""
    bad_type_yaml = _SYSTEM_CONFIG.replace(
        "max_open_positions: 10",
        'max_open_positions: "ten"',
    )
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d, overrides={"system_config.yaml": bad_type_yaml})

        raised = False
        try:
            load_all(d)
        except ConfigSchemaError:
            raised = True
    assert raised, "Expected ConfigSchemaError for wrong type"
    print("  OK Wrong type (str where int expected) raises ConfigSchemaError")


def test_config_schema_error_context_has_filename() -> None:
    bad_yaml = "zerodha:\n  brokerage_flat_intraday: 20.0\n"  # missing 8 fields

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d, overrides={"broker_costs.yaml": bad_yaml})

        try:
            load_all(d)
        except ConfigSchemaError as exc:
            assert exc.context["file"] == "broker_costs.yaml"
            assert "error_count" in exc.context
            assert exc.context["error_count"] >= 1
            print(
                f"  OK ConfigSchemaError.context: file={exc.context['file']!r}, "
                f"error_count={exc.context['error_count']}"
            )
        else:
            assert False, "ConfigSchemaError not raised"


def test_config_schema_error_is_trading_system_error() -> None:
    bad_yaml = "not_valid_yaml: : [\n"

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d, overrides={"slippage_model.yaml": bad_yaml})

        try:
            load_all(d)
        except TradingSystemError:
            print("  OK ConfigSchemaError caught as TradingSystemError")
        else:
            assert False, "Not caught as TradingSystemError"


def test_extra_nested_key_raises_config_schema_error() -> None:
    """extra='forbid' applies to nested models too (e.g. limits section)."""
    extra_nested = _SYSTEM_CONFIG.replace(
        "  max_open_positions: 10",
        "  max_open_positions: 10\n  unknown_nested_key: 999",
    )
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d, overrides={"system_config.yaml": extra_nested})

        raised = False
        try:
            load_all(d)
        except ConfigSchemaError:
            raised = True
    assert raised, "Expected ConfigSchemaError for extra nested key"
    print("  OK Extra nested key raises ConfigSchemaError (extra='forbid' on sub-models)")


def test_trading_hours_inverted_window_rejected() -> None:
    """
    CFG-1 (2026-04-26 audit): TradingHoursConfig must reject YAML where
    entry_start >= entry_end (or any other ordering violation against P1).
    """
    bad = _SYSTEM_CONFIG.replace(
        'entry_start: "09:30"\n  entry_end: "13:30"',
        'entry_start: "13:30"\n  entry_end: "09:30"',
    )
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d, overrides={"system_config.yaml": bad})

        raised = False
        try:
            load_all(d)
        except ConfigSchemaError:
            raised = True
    assert raised, "Expected ConfigSchemaError for inverted entry window"
    print("  OK Inverted entry window rejected by TradingHoursConfig (CFG-1)")


def test_trading_hours_yaml_pins_operator_chosen_window() -> None:
    """
    CFG-1 pin (evolved 2026-06-16 Live Week 1): the production
    system_config.yaml must declare entry_start/entry_end values that
    were chosen deliberately by the operator. The original CFG-1 pin
    enforced the P1 spec (09:30/13:30); paper Week 2 widened it to
    09:20/15:15; Live Week 1 narrows the start to 10:00 (conservative,
    avoids opening-hour volatility). This test pins the current live
    window (10:00/15:00) so the values can't silently drift back to the
    prior 09:25 namesake or to a typo without a corresponding test edit.
    (T5 29-Jun: entry_end aligned 15:15->15:00 to match the per-strategy
    entry_end_time=15:00; committed so a checkout -f can't revert it.)

    Regression guards still in place:
      - 09:25 (the original namesake) is asserted absent.
      - eod_squareoff_time is still pinned to 15:17 (P1 hard rule).
    """
    project_root = Path(__file__).parent.parent.parent
    raw = yaml.safe_load(
        (project_root / "config" / "system_config.yaml").read_text(encoding="utf-8")
    )
    th = raw.get("trading_hours", {})
    assert th.get("entry_start") == "10:00", \
        f"Expected 10:00 (Live Week 1 conservative start), got {th.get('entry_start')}"
    assert th.get("entry_end") == "15:00", \
        f"Expected 15:00 (T5 29-Jun: aligned to per-strategy 15:00), got {th.get('entry_end')}"
    assert th.get("entry_start") != "09:25", \
        "09:25 namesake must never return (CFG-1 regression guard)"
    assert th.get("eod_squareoff_time") == "15:17"
    print("  OK Production YAML trading_hours match Live Week 1 (10:00/15:00/15:17)")


def test_invalid_date_in_nse_holidays_raises_config_schema_error() -> None:
    bad_dates = "holidays:\n  - '2026-01-26'\n  - 'not-a-date'\n"

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d, overrides={"nse_holidays_2026.yaml": bad_dates})

        raised = False
        try:
            load_all(d)
        except ConfigSchemaError:
            raised = True
    assert raised, "Expected ConfigSchemaError for invalid date format in nse_holidays"
    print("  OK Invalid date format in nse_holidays raises ConfigSchemaError")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — SHA-256 file hashes (CL4)
# ─────────────────────────────────────────────────────────────────────────────

def test_file_hashes_populated_for_all_8_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    expected_files = {
        "system_config.yaml",
        "broker_costs.yaml",
        "broker_limits.yaml",
        "slippage_model.yaml",
        "scoring_weights.yaml",
        "scan_webhook_map.yaml",
        "chartink_scanners.yaml",
        "nse_holidays_2026.yaml",
    }
    assert set(cfg.file_hashes.keys()) == expected_files, (
        f"Missing hash keys: {expected_files - set(cfg.file_hashes.keys())}"
    )
    print(f"  OK file_hashes populated for all {len(cfg.file_hashes)} files")


def test_file_hashes_are_64_char_hex_strings() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    for filename, digest in cfg.file_hashes.items():
        assert len(digest) == 64, (
            f"{filename}: expected 64-char SHA-256 hex, got len={len(digest)}"
        )
        assert all(c in "0123456789abcdef" for c in digest), (
            f"{filename}: hash contains non-hex char"
        )
    print("  OK All 8 hashes are 64-char lowercase hex strings (SHA-256)")


def test_hash_changes_when_file_content_changes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg1 = load_all(d)

        # Modify one file
        (d / "broker_costs.yaml").write_text(
            _BROKER_COSTS.replace("20.0", "25.0"), encoding="utf-8"
        )
        cfg2 = load_all(d)

    h1 = cfg1.file_hashes["broker_costs.yaml"]
    h2 = cfg2.file_hashes["broker_costs.yaml"]
    assert h1 != h2, "Hash must change when file content changes"

    # Unchanged files must keep the same hash
    assert (
        cfg1.file_hashes["system_config.yaml"]
        == cfg2.file_hashes["system_config.yaml"]
    ), "Unchanged file hash must be stable"
    print("  OK Hash changes when content changes; unchanged files keep same hash")


def test_hash_is_stable_across_two_loads() -> None:
    """Same file → same hash across repeated load_all() calls."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg1 = load_all(d)
        cfg2 = load_all(d)

    assert cfg1.file_hashes == cfg2.file_hashes
    print("  OK Hashes are stable across repeated load_all() calls on unchanged files")


# ─────────────────────────────────────────────────────────────────────────────
# SH11 — ShadowTrackerConfig tests
# ─────────────────────────────────────────────────────────────────────────────

def test_shadow_tracker_config_valid() -> None:
    """ShadowTrackerConfig parses correctly from stub YAML (SH11)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        cfg = load_all(d)

    assert isinstance(cfg.system.shadow_tracker, ShadowTrackerConfig)
    assert cfg.system.shadow_tracker.enabled is True
    assert cfg.system.shadow_tracker.max_innings == 3
    assert cfg.system.shadow_tracker.alert_per_inning is True
    print("  OK ShadowTrackerConfig parses correctly from stub YAML")


def test_shadow_tracker_disabled() -> None:
    """ShadowTrackerConfig enabled=false parses correctly."""
    stub = _SYSTEM_CONFIG.replace(
        "shadow_tracker:\n  enabled: true",
        "shadow_tracker:\n  enabled: false",
    )
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        (d / "system_config.yaml").write_text(stub)
        cfg = load_all(d)

    assert cfg.system.shadow_tracker.enabled is False
    print("  OK ShadowTrackerConfig enabled=false parses correctly")


def test_shadow_tracker_max_innings_out_of_range() -> None:
    """ShadowTrackerConfig max_innings < 1 or > 5 raises ConfigSchemaError (SH11)."""
    bad = _SYSTEM_CONFIG.replace("max_innings: 3", "max_innings: 6")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        (d / "system_config.yaml").write_text(bad)
        try:
            load_all(d)
            assert False, "Expected ConfigSchemaError for max_innings=6"
        except ConfigSchemaError:
            pass
    print("  OK max_innings=6 raises ConfigSchemaError")


def test_shadow_tracker_max_innings_min_boundary() -> None:
    """ShadowTrackerConfig max_innings=1 is valid lower bound (SH11)."""
    stub = _SYSTEM_CONFIG.replace("max_innings: 3", "max_innings: 1")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_stubs(d)
        (d / "system_config.yaml").write_text(stub)
        cfg = load_all(d)

    assert cfg.system.shadow_tracker.max_innings == 1
    print("  OK ShadowTrackerConfig max_innings=1 is valid")


def test_real_scan_webhook_map_yaml_loads() -> None:
    """Load the real config/scan_webhook_map.yaml directly to catch schema drift."""
    project_root = Path(__file__).parent.parent.parent
    raw = yaml.safe_load((project_root / "config" / "scan_webhook_map.yaml").read_text(encoding="utf-8"))
    cfg = ScanWebhookMapConfig.model_validate(raw)
    # ⭐ THE PROPERTY, NOT THE COUNT (26-Jul-2026). This asserted `== 16`, which
    # changes the day anyone ADDS A SCANNER -- a routine edit -- so it would fail for
    # a reason that is not a bug and the number would simply be hand-edited. What the
    # 16 actually stood in for is the file's own stated contract (its header: "every
    # strategy name must have a corresponding YAML file in config/strategies/"), and
    # THAT fails on a real defect (a typo'd or deleted strategy) while staying silent
    # on a routine addition, because adding a scanner means adding its strategy file.
    # ⚠️ Deliberately NOT `len(cfg.scanners) == len(raw["scanners"])`: pydantic RAISES
    # on a bad entry rather than dropping it, so that comparison can never fail --
    # it would have replaced one weak assertion with a vacuous one.
    strategy_dir = project_root / "config" / "strategies"
    for name, entry in cfg.scanners.items():
        assert isinstance(entry, ScannerEntry), f"Scanner {name!r} entry not ScannerEntry"
        assert entry.strategy, f"Scanner {name!r} has empty strategy"
        assert (strategy_dir / f"{entry.strategy}.yaml").is_file(), (
            f"Scanner {name!r} names strategy {entry.strategy!r} but "
            f"config/strategies/{entry.strategy}.yaml does not exist"
        )
        assert entry.chartink_url.startswith("https://"), (
            f"Scanner {name!r} chartink_url not https: {entry.chartink_url!r}"
        )
    # A floor, not an equality: catches a TRUNCATED or gutted map (the other thing
    # the count guarded) without firing when a scanner is legitimately added.
    assert len(cfg.scanners) >= 10, (
        f"scan_webhook_map.yaml looks truncated: only {len(cfg.scanners)} scanners"
    )
    assert "pb01_breakout_retest" in cfg.scanners, "PB-01 scanner missing from map"
    print(f"  OK Real scan_webhook_map.yaml: {len(cfg.scanners)} scanners, all valid")


def test_real_nse_holidays_yaml_loads() -> None:
    """Load the real config/nse_holidays_2026.yaml directly to catch schema drift."""
    project_root = Path(__file__).parent.parent.parent
    raw = yaml.safe_load((project_root / "config" / "nse_holidays_2026.yaml").read_text(encoding="utf-8"))
    cfg = NseHolidaysConfig.model_validate(raw)
    # ⭐ THE PROPERTY, NOT THE COUNT (26-Jul-2026). `== 15` changes whenever NSE adds
    # or moves a holiday -- routine, annual, and not a bug -- so the number would be
    # hand-edited rather than the change being examined. The properties that a wrong
    # file actually violates are: every entry parses, every date is in the configured
    # year, no date repeats, and the file is not truncated.
    assert all(isinstance(h, HolidayEntry) for h in cfg.holidays)
    assert all(h.date.year == 2026 for h in cfg.holidays)
    assert len({h.date for h in cfg.holidays}) == len(cfg.holidays), (
        "a duplicated holiday date would silently double-count one closed day"
    )
    # A floor: NSE publishes ~15 CM-segment holidays a year, so a file with fewer
    # than 10 is truncated, not updated. Fires on loss, never on a routine edit.
    assert len(cfg.holidays) >= 10, (
        f"nse_holidays_2026.yaml looks truncated: only {len(cfg.holidays)} holidays"
    )
    assert any(h.name == "Republic Day" for h in cfg.holidays)
    print(f"  OK Real nse_holidays_2026.yaml: {len(cfg.holidays)} holidays, all valid")


def test_every_registered_config_file_exists_for_the_CURRENT_year() -> None:
    """⏰ A DATED TRIPWIRE, ON PURPOSE — the third axis of the clock-dependency class
    (26-Jul-2026). The first two were hour-of-day (25-Jul) and day-of-week (26-Jul);
    this one is YEAR, and unlike those two it is not a test bug — it stops the boot.

    `_CONFIG_FILES` resolves the holiday filename as
    `f"nse_holidays_{date.today().year}.yaml"`, evaluated ONCE at module import. Only
    `nse_holidays_2026.yaml` exists. MEASURED by patching `date.today` before import:
    on the first boot of 2027 `load_all()` raises

        ConfigMissingError: Required config file not found: nse_holidays_2027.yaml

    and `load_all()` is on the boot path, so the service does not start. Nothing in
    the suite catches that today, because every existing test writes stubs named
    `nse_holidays_2026.yaml` — which is exactly why they would ALL break on the same
    morning, for a reason that is not a bug in the code under test.

    ⛔ WHEN THIS GOES RED, THE FIX IS TO ADD THE FILE, NEVER TO EDIT THIS ASSERTION.
    Red here means: NSE's holiday list for the new year has not been committed yet,
    and the next 08:15 boot will fail. That is an action, not a stale expectation.

    Stated as the property rather than as one filename, so it also catches any future
    registry entry added without its file."""
    config_dir = Path(__file__).parent.parent.parent / "config"
    missing = [fname for _key, fname, _schema in _CONFIG_FILES
               if not (config_dir / fname).exists()]
    assert not missing, (
        f"config/ is missing {missing} — every file in the _CONFIG_FILES registry must "
        f"exist or load_all() raises ConfigMissingError and the service cannot boot. "
        f"If this names nse_holidays_<year>.yaml, the new year's NSE holiday list has "
        f"not been added yet: create the file, do not edit this test."
    )


# ── the EARLY half of the same tripwire ──────────────────────────────────────

_HOLIDAY_LEAD_DAYS = 30      # fires from 1-Dec; see the test docstring for why not 60


def _missing_next_year_holiday_file(today):
    """Return the absent `nse_holidays_<next year>.yaml`, or None if not yet due.

    PURE in `today` on purpose: the tripwire can then be driven to any date without
    patching a clock, so proving it can go RED costs nothing and touches nothing.
    """
    from datetime import date as _date
    year_end = _date(today.year, 12, 31)
    if (year_end - today).days > _HOLIDAY_LEAD_DAYS:
        return None
    fname = f"nse_holidays_{today.year + 1}.yaml"
    config_dir = Path(__file__).parent.parent.parent / "config"
    return None if (config_dir / fname).exists() else fname


def test_next_years_holiday_file_lands_before_the_current_one_runs_out() -> None:
    """⏰ THE EARLY HALF OF THE TRIPWIRE — it must fire in DECEMBER, not in January.

    Its sibling above goes red on 1-Jan when `nse_holidays_<new year>.yaml` is absent.
    By then the 08:15 boot has already failed: `load_all()` raises ConfigMissingError
    at `main.py:1816` and main returns 5.

    ⭐ AND THE CHECK BUILT TO CATCH EXACTLY THIS CANNOT REACH IT.
    `check_config_files_present()` explicitly requires `nse_holidays_{current_year}
    .yaml` and would record `missing_config_files` as a BLOCKING failure with a clear
    message — but it runs inside `run_all_startup_checks()` at `main.py:2069`, **253
    lines after** the `load_all()` that already killed the boot. A downstream check
    cannot catch an upstream death.

    ⭐ Note also that the SAME DATA, read two ways in one boot, has two OPPOSITE
    failure policies: the SU6 holiday guard reads the YAML directly and explicitly
    swallows the missing file (`main.py:1769` — "proceed with startup"), while
    `load_all`'s blanket "all 8 files must exist" turns the very same absence into a
    dead boot. The file is a HARD boot requirement serving a SOFT purpose.

    So the warning has to arrive EARLY, and the suite is the cheapest place that
    costs no production code: it runs constantly here, and a red test is read by
    whoever takes the gate instead of emailed to whoever is asleep.

    ⛔⛔ WHEN THIS GOES RED, THE ACTION IS: obtain NSE's PUBLISHED holiday list for
    the coming year and commit `config/nse_holidays_<year>.yaml`. **DO NOT invent,
    infer, or extrapolate the dates.** A guessed calendar is far worse than a missing
    one — the system would trade on a market holiday, or skip a real trading day, and
    believe it was right. NSE publishes the following year's list around Nov–Dec.

    LEAD TIME IS 30 DAYS, DELIBERATELY NOT 60. Long enough to act in, short enough
    that NSE has actually published: a red test nobody *can* fix is precisely the
    noise this week has spent days undoing. From 1-Dec there is a full month before
    the boot dies."""
    from datetime import date
    missing = _missing_next_year_holiday_file(date.today())
    assert missing is None, (
        f"config/{missing} is absent and the current year's calendar runs out within "
        f"{_HOLIDAY_LEAD_DAYS} days. On 1-Jan load_all() will raise ConfigMissingError "
        f"and the 08:15 boot will not start. Commit NSE's PUBLISHED list for that year "
        f"— never a guessed one — and do not edit this test."
    )


def test_the_lead_time_tripwire_is_not_vacuous() -> None:
    """A green check is evidence only if it could have been red — and today it IS
    green, so the red has to be demonstrated rather than assumed.

    Driven purely by date, against the REAL config/ directory (which holds 2026 and
    not 2027), so these assertions exercise the same code path the live test does."""
    from datetime import date
    # today (26-Jul-2026): 158 days of runway -> silent, which is why the live test passes
    assert _missing_next_year_holiday_file(date(2026, 7, 26)) is None
    # 31 days out -> still silent, the boundary is not off by one
    assert _missing_next_year_holiday_file(date(2026, 11, 30)) is None
    # 1-Dec: 30 days -> FIRES, naming the file that does not exist
    assert _missing_next_year_holiday_file(date(2026, 12, 1)) == "nse_holidays_2027.yaml"
    assert _missing_next_year_holiday_file(date(2026, 12, 31)) == "nse_holidays_2027.yaml"
    # and it is satisfied by the file EXISTING, not by the calendar moving on:
    # 2026's own file is present, so a 2025-year-end check would have been silent.
    assert _missing_next_year_holiday_file(date(2025, 12, 15)) is None


def test_system_config_has_no_limits_block() -> None:
    """
    BL-17 regression guard: the dead `limits:` block must not return.
    Its fields were duplicates of `risk:` (max_open_positions,
    daily_loss_limit_pct), nothing read from it, and one duplicated key
    held a different value (limits.daily_loss_limit_pct=2.0 vs
    risk.daily_loss_limit_pct=0.05) — a silent trap for anyone who edited
    the wrong block.
    """
    project_root = Path(__file__).parent.parent.parent
    raw = yaml.safe_load((project_root / "config" / "system_config.yaml").read_text(encoding="utf-8"))
    assert isinstance(raw, dict), "system_config.yaml root must be a mapping"
    assert "limits" not in raw, (
        "system_config.yaml must not contain a top-level `limits:` block "
        "(BL-17). Use `risk:` for portfolio-level limits."
    )
    print("  OK BL-17: system_config.yaml has no `limits:` block")


def test_no_duplicate_config_keys() -> None:
    """
    BL-17 regression guard: for any key name that appears under more than
    one top-level block in system_config.yaml, the values must differ.
    Equal values across two blocks are a sign of a copy/paste drift like
    the BL-17 pair limits.max_open_positions=10 / risk.max_open_positions=10
    which silently disagreed on ownership.

    Different values are allowed (e.g. `poll_interval_sec` legitimately
    differs across order_monitor/eod_squareoff/order_reconciler).
    """
    project_root = Path(__file__).parent.parent.parent
    raw = yaml.safe_load((project_root / "config" / "system_config.yaml").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)

    # BL-7b: master-switch names are deliberately shared across module blocks
    # (shadow_tracker.enabled, smart_tgt.enabled, ...). BL-17's concern is
    # ownership drift between blocks that claim to own the same concept
    # (limits.max_open_positions vs risk.max_open_positions), not flag-name
    # reuse for per-module on/off toggles.
    #
    # SNR-V2 Phase B: `sl_buffer_pct` is the same structure-SL-buffer methodology
    # applied in two independently-owned phases — sr_detector (entry-side WAIT_FOR_
    # RETEST divert) and structure_exit (exit-side SL trail). They share the 0.2
    # default by design (parallel knobs) but each owns its own value and may be
    # tuned apart; this is deliberate parallelism, not the limits/risk ownership
    # drift BL-17 targets.
    _SHARED_FLAG_ALLOWLIST = {"enabled", "sl_buffer_pct"}

    # Walk: key_name -> list[(block_name, value)]
    appearances: dict[str, list[tuple[str, object]]] = {}
    for block_name, block_body in raw.items():
        if not isinstance(block_body, dict):
            continue
        for child_key, child_val in block_body.items():
            # Ignore mappings and lists; we care about scalar duplication only
            if isinstance(child_val, (dict, list)):
                continue
            if child_key in _SHARED_FLAG_ALLOWLIST:
                continue
            appearances.setdefault(child_key, []).append((block_name, child_val))

    collisions: list[str] = []
    for key_name, sites in appearances.items():
        if len(sites) < 2:
            continue
        # Group by value; any value that appears in >1 block is a collision
        by_value: dict[object, list[str]] = {}
        for block_name, val in sites:
            by_value.setdefault(val, []).append(block_name)
        for val, blocks in by_value.items():
            if len(blocks) > 1:
                collisions.append(
                    f"key `{key_name}` with value `{val!r}` appears under "
                    f"blocks {blocks}"
                )

    assert not collisions, (
        "system_config.yaml has duplicate (key, value) pairs across "
        "top-level blocks (BL-17 class): " + "; ".join(collisions)
    )
    print(
        f"  OK BL-17: no duplicate (key, value) pairs across "
        f"{len([k for k, v in raw.items() if isinstance(v, dict)])} top-level blocks"
    )


def test_no_test_reads_repo_config_with_the_platform_encoding() -> None:
    """D (25-Jul-2026): repo config files are UTF-8; tests must say so.

    `config_loader` reads config with `path.read_bytes()` and hands the bytes to
    `yaml.safe_load`, which is UTF-8 by spec -- so the APPLICATION is safe and
    non-ASCII in a config file is never a boot hazard. Tests that use bare
    `read_text()` instead get the PLATFORM default (cp1252 on this Windows host),
    which HARD-ERRORS on any byte cp1252 does not map (0x81/0x8D/0x8F/0x90/0x9D).

    That bit for real: a star (U+2B50 = E2 AD 90) added to a `system_config.yaml`
    comment turned THREE unrelated config tests red, for a reason that had nothing
    to do with what they assert. A suite that fails on the decoration in a comment
    makes every BASE-vs-MERGE gate ambiguous -- the same failure mode as the
    wall-clock time-bomb fixed in test_interactive_startup.py.

    Scoped to CODE, not comments: documenting the retired pattern stays legal.
    """
    import re

    tests_root = Path(__file__).parent.parent
    offenders: list[str] = []
    # bare .read_text() -- no args at all -- on a line that names a config path
    bare = re.compile(r"\.read_text\(\s*\)")

    for py in sorted(tests_root.rglob("*.py")):
        for lineno, line in enumerate(
            py.read_text(encoding="utf-8").splitlines(), start=1
        ):
            code = line.split("#", 1)[0]          # CODE only; comments are legal
            if "config" not in code:
                continue
            if bare.search(code):
                offenders.append(f"{py.relative_to(tests_root)}:{lineno}")

    assert not offenders, (
        "these tests read a repo config file with the platform default encoding; "
        'use read_text(encoding="utf-8") so a non-ASCII byte in a config comment '
        f"cannot fail them for an unrelated reason: {offenders}"
    )
    print("  OK no test reads repo config with the platform default encoding")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_load_all_valid_stubs_returns_app_config,
        test_all_8_sub_configs_present,
        test_system_config_values_match_stubs,
        test_eod_squareoff_config_values_match_stubs,
        test_alerts_config_values_match_stubs,
        # G.3 / 2026-04-25 audit -- SMTP password from env var
        test_g3_smtp_resolved_password_prefers_env_var,
        test_g3_smtp_resolved_password_falls_back_to_plaintext,
        test_g3_smtp_password_env_set_but_var_missing_raises,
        test_g3_smtp_neither_password_nor_env_raises_at_construct,
        test_order_reconciler_config_values_match_stubs,
        test_broker_costs_values_match_stubs,
        test_broker_limits_values_match_stubs,
        test_slippage_tiers_present,
        test_scoring_weights_and_thresholds,
        test_empty_scanners_dict_is_valid,
        test_nse_holidays_loaded_as_list,
        test_load_all_with_custom_config_dir,
        test_missing_file_raises_config_missing_error,
        test_missing_file_context_has_filename,
        test_config_missing_error_is_trading_system_error,
        test_malformed_yaml_raises_config_schema_error,
        test_extra_unknown_key_raises_config_schema_error,
        test_missing_required_field_raises_config_schema_error,
        test_wrong_type_raises_config_schema_error,
        test_config_schema_error_context_has_filename,
        test_config_schema_error_is_trading_system_error,
        test_extra_nested_key_raises_config_schema_error,
        test_trading_hours_inverted_window_rejected,
        test_trading_hours_yaml_pins_operator_chosen_window,
        test_invalid_date_in_nse_holidays_raises_config_schema_error,
        test_file_hashes_populated_for_all_8_files,
        test_file_hashes_are_64_char_hex_strings,
        test_hash_changes_when_file_content_changes,
        test_hash_is_stable_across_two_loads,
        test_shadow_tracker_config_valid,
        test_shadow_tracker_disabled,
        test_shadow_tracker_max_innings_out_of_range,
        test_shadow_tracker_max_innings_min_boundary,
        test_real_scan_webhook_map_yaml_loads,
        test_real_nse_holidays_yaml_loads,
        test_system_config_has_no_limits_block,
        test_no_duplicate_config_keys,
    ]

    print("=" * 70)
    print("config_loader.py — Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            test()
        except AssertionError as e:
            failed.append((test.__name__, f"AssertionError: {e}"))
            print(f"  FAIL: {e}")
        except Exception as e:
            failed.append((test.__name__, f"{type(e).__name__}: {e}"))
            print(f"  ERROR: {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1

    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
