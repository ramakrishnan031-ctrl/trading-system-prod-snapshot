"""
core/config_loader.py — Trading System v2

Purpose:
    Single authoritative loader for all non-strategy YAML configuration files.
    Validates every file against a strict Pydantic v2 schema on startup.
    Computes SHA-256 hash of each file for change detection between restarts.

Locked Design Decisions:
    CL1 — load_all(config_dir: Path = Path("config")) → AppConfig.
           Module-level function. No singleton. AppConfig is a Pydantic BaseModel
           holding all 8 sub-configs as typed fields. main.py calls it at Phase 0b,
           holds the result, and passes sub-configs to each subsystem via DI.
    CL2 — Atomic load. All 8 files must parse + validate. Any failure raises
           immediately. No partial AppConfig ever returned.
    CL3 — Strict schemas. Every Pydantic model uses extra="forbid". Unknown
           keys in YAML raise ConfigSchemaError — typos in key names are caught.
    CL4 — SHA-256 hash per file. load_all() stores filename → hex digest in
           AppConfig.file_hashes for Phase 0b config-change detection.
    CL5 — No env-var interpolation, no includes. yaml.safe_load() only.
           Values needing env vars go in .env, read by consumer modules.
    CL6 — All 8 file schemas + AppConfig in one file. Split into
           core/config_schemas/ if file exceeds 600 lines.

Config Files Loaded (strategies/*.yaml NOT handled here — see strategies/loader.py):
    system_config.yaml      → SystemConfig      (P1, P2, P4, P11b, P14, P15, Q1, G4)
    broker_costs.yaml       → BrokerCostsConfig (P12)
    broker_limits.yaml      → BrokerLimitsConfig (G7)
    slippage_model.yaml     → SlippageConfig    (P12)
    scoring_weights.yaml    → ScoringConfig     (P9b)
    scan_webhook_map.yaml   → ScanWebhookMapConfig (P17)
    chartink_scanners.yaml  → ChartinkScannersConfig (P17)
    nse_holidays_2026.yaml  → NseHolidaysConfig

What This Module Does NOT Do:
    - Does not load strategies/*.yaml (handled by strategies/loader.py)
    - Does not read .env files or expand environment variables (CL5)
    - Does not support hot reload — load-once at startup (Phase 0b)
    - Does not fill in defaults for missing keys (CL3: extra="forbid", all required)
    - Does not return a partial config on any error (CL2)
"""
from __future__ import annotations

import hashlib
from datetime import date as _date
from pathlib import Path

import yaml
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from core.exceptions import ConfigMissingError, ConfigSchemaError


# ─────────────────────────────────────────────────────────────────────────────
# system_config.yaml — SystemConfig
# Locked: P1, P2, P4, P11b, P14, P15, Q1, G4
# ─────────────────────────────────────────────────────────────────────────────

class TradingHoursConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_start: str           # "HH:MM" IST — entry window opens (P1)
    entry_end: str             # "HH:MM" IST — entry window closes (P1)
    eod_squareoff_time: str    # "HH:MM" IST — square-off trigger (P1)
    market_open: str = "09:15"   # "HH:MM" IST — NSE regular-session open
    market_close: str = "15:30"  # "HH:MM" IST — NSE regular-session close

    @model_validator(mode="after")
    def _validate_window_ordering(self) -> "TradingHoursConfig":
        """
        2026-04-26 audit CFG-1: catch operator typos that would otherwise
        silently shrink (or invert) the entry window. P1 mandates
        entry_start < entry_end and market_open <= entry_start
        and entry_end <= eod_squareoff_time <= market_close.
        """
        from datetime import time as _time

        def _hhmm(s: str) -> _time:
            h, m = s.split(":")
            return _time(int(h), int(m))

        mo = _hhmm(self.market_open)
        es = _hhmm(self.entry_start)
        ee = _hhmm(self.entry_end)
        eod = _hhmm(self.eod_squareoff_time)
        mc = _hhmm(self.market_close)
        if not (mo <= es < ee <= eod <= mc):
            raise ValueError(
                "trading_hours ordering violation; require "
                "market_open <= entry_start < entry_end <= "
                "eod_squareoff_time <= market_close, got "
                f"market_open={self.market_open}, entry_start={self.entry_start}, "
                f"entry_end={self.entry_end}, "
                f"eod_squareoff_time={self.eod_squareoff_time}, "
                f"market_close={self.market_close}"
            )
        return self


class SignalQueueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capacity: int              # max signals in queue (P15 default: 300)
    backpressure_pct: float    # HTTP 503 at capacity × pct (P15 default: 0.80)
    expiry_sec: int            # WR6: reject signal older than this many seconds (default 60)


class ClockSkewProbeConfig(BaseModel):
    """BL-21: periodic broker clock skew probe driver."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    probe_interval_sec: int = 60

    @field_validator("probe_interval_sec")
    @classmethod
    def _probe_interval_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("probe_interval_sec must be > 0")
        return v


class ClockConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    warn_skew_sec: float         # G4 tier: log warning only
    alert_skew_sec: float        # G4 tier: Telegram alert, continue trading
    halt_skew_sec: float         # G4 tier: fire on_critical_skew callback -> soft_kill
    startup_max_skew_sec: float  # G4: refuse to start if startup skew exceeds this
    probe: ClockSkewProbeConfig = Field(default_factory=ClockSkewProbeConfig)  # BL-21


class LeverageMapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    INTRADAY: float
    COVER_ORDER: float
    DELIVERY: float
    BRACKET_ORDER: float


class CapitalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intraday_bucket_pct: float     # FM16: fraction of total for intraday (0 < x < 1)
    positional_bucket_pct: float   # FM16: fraction of total for positional (0 < x < 1)
    daily_loss_limit: float        # FM16: absolute rupee cap on daily loss (> 0)
    leverage_map: LeverageMapConfig  # FM16: per-intent leverage multiplier

    @field_validator("intraday_bucket_pct", "positional_bucket_pct")
    @classmethod
    def _validate_bucket_pct(cls, v: float) -> float:
        if not (0 < v < 1):
            raise ValueError("bucket_pct must be between 0 and 1 exclusive")
        return v

    @field_validator("daily_loss_limit")
    @classmethod
    def _validate_daily_loss(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("daily_loss_limit must be > 0")
        return v


class OrderMonitorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    poll_interval_sec: int    # OM14: >= 1; how often to poll broker for fill status
    fill_timeout_sec: int     # OM14: >= 5; cancel unfilled order after this many seconds

    @field_validator("poll_interval_sec")
    @classmethod
    def _validate_poll_interval(cls, v: int) -> int:
        if v < 1:
            raise ValueError("poll_interval_sec must be >= 1")
        return v

    @field_validator("fill_timeout_sec")
    @classmethod
    def _validate_fill_timeout(cls, v: int) -> int:
        if v < 5:
            raise ValueError("fill_timeout_sec must be >= 5")
        return v


class PositionSizingTierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    HIGH: float     # PS5: tier multiplier for HIGH quality signals (1.0 = full size)
    MEDIUM: float   # PS5: tier multiplier for MEDIUM quality signals (0.7 default)
    LOW: float      # PS5: tier multiplier for LOW quality signals (0.5 default)


class PositionSizingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk_per_trade_pct: float          # PS2: fraction of total capital at risk per trade
    max_concentration_pct: float       # PS2: max fraction of total capital in one symbol
    min_qty_threshold: int             # PS6: reject if final qty below this
    tier_multipliers: PositionSizingTierConfig  # PS5

    @field_validator("risk_per_trade_pct")
    @classmethod
    def _validate_risk_pct(cls, v: float) -> float:
        if not (0 < v < 1):
            raise ValueError("risk_per_trade_pct must be between 0 and 1 exclusive")
        return v

    @field_validator("max_concentration_pct")
    @classmethod
    def _validate_conc_pct(cls, v: float) -> float:
        if not (0 < v <= 1):
            raise ValueError("max_concentration_pct must be between 0 (exclusive) and 1 (inclusive)")
        return v

    @field_validator("min_qty_threshold")
    @classmethod
    def _validate_min_qty(cls, v: int) -> int:
        if v < 1:
            raise ValueError("min_qty_threshold must be >= 1")
        return v


class SignalProcessorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_count: int           # SP15: thread pool size (1–50)
    drain_poll_sec: float       # SP15: queue polling interval when empty (> 0)
    pipeline_timeout_sec: int   # SP15: hard per-signal timeout (>= 1)
    # MED #12: ATR fallback behaviour when ATR data is unavailable.
    # "WARN" (default) = log WARNING and fall back to FIXED_PCT.
    # "HALT" = reject the signal with REJECTED_NO_ATR_DATA.
    atr_fallback_mode: Literal["WARN", "HALT"] = "WARN"
    # BL-16: minimum target distance as fraction of entry. Guards against
    # degenerate configs that would produce target == entry (guaranteed loss).
    tgt_min_pct: float = 0.003

    @field_validator("worker_count")
    @classmethod
    def _validate_worker_count(cls, v: int) -> int:
        if not (1 <= v <= 50):
            raise ValueError("worker_count must be >= 1 and <= 50")
        return v

    @field_validator("drain_poll_sec")
    @classmethod
    def _validate_poll(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("drain_poll_sec must be > 0")
        return v

    @field_validator("pipeline_timeout_sec")
    @classmethod
    def _validate_timeout(cls, v: int) -> int:
        if v < 1:
            raise ValueError("pipeline_timeout_sec must be >= 1")
        return v

    @field_validator("tgt_min_pct")
    @classmethod
    def _validate_tgt_min_pct(cls, v: float) -> float:
        if not (0 < v < 1):
            raise ValueError("tgt_min_pct must be > 0 and < 1")
        return v


class WebhookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bind_host: str    # WR14: interface to bind (default "127.0.0.1")
    bind_port: int    # WR14: port to bind (default 5000)
    require_hmac: bool  # WR14: if True, reject requests missing valid HMAC


class EodSquareoffConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inter_order_delay_ms: int   # EOD12: ms delay between exit orders (>= 0, <= 5000)
    poll_interval_sec: int      # EOD12: scheduler poll cadence in seconds (>= 1)
    auto_resume_kill_switch: bool  # EOD12: if True, resume soft_kill after EOD fire
    # Audit 3.3 + 5.2 (locked 2026-04-25): EOD exit protocol controls
    exit_protocol: str = "MARKET"            # "MARKET" (legacy) | "LIMIT_THEN_MARKET"
    limit_aggressive_pct: float = 0.01       # LTP +/- this for SELL/BUY exit limits
    limit_grace_sec: float = 120.0           # wait before promoting unfilled LIMITs to MARKET

    @field_validator("inter_order_delay_ms")
    @classmethod
    def _validate_delay(cls, v: int) -> int:
        if not (0 <= v <= 5000):
            raise ValueError("inter_order_delay_ms must be between 0 and 5000 inclusive")
        return v

    @field_validator("poll_interval_sec")
    @classmethod
    def _validate_poll(cls, v: int) -> int:
        if v < 1:
            raise ValueError("poll_interval_sec must be >= 1")
        return v

    @field_validator("exit_protocol")
    @classmethod
    def _validate_exit_protocol(cls, v: str) -> str:
        allowed = {"MARKET", "LIMIT_THEN_MARKET"}
        if v not in allowed:
            raise ValueError(f"exit_protocol must be one of {allowed}, got {v!r}")
        return v

    @field_validator("limit_aggressive_pct")
    @classmethod
    def _validate_limit_pct(cls, v: float) -> float:
        if not (0.0 < v <= 0.10):
            raise ValueError(
                f"limit_aggressive_pct must be in (0, 0.10] (max 10% slippage budget); got {v!r}"
            )
        return v

    @field_validator("limit_grace_sec")
    @classmethod
    def _validate_grace(cls, v: float) -> float:
        if not (0.0 <= v <= 300.0):
            raise ValueError(
                f"limit_grace_sec must be in [0, 300] (5-min sanity cap); got {v!r}"
            )
        return v


class KillSwitchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_failure_threshold: int   # KS7: consecutive API failures before auto-trip (>= 1)
    enable_auto_trip: bool       # KS7: if False, record_api_failure never auto-trips

    @field_validator("api_failure_threshold")
    @classmethod
    def _validate_threshold(cls, v: int) -> int:
        if v < 1:
            raise ValueError("api_failure_threshold must be >= 1")
        return v


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_open_positions: int          # RE13: hard cap on concurrent open positions (>= 1)
    max_daily_trades: int            # RE13: hard cap on trades per day (>= 1)
    max_sector_exposure_pct: float   # RE13: max fraction of capital in one sector (> 0, <= 1)
    max_consecutive_losses: int      # RE13: halt after N consecutive losses (>= 1)
    daily_loss_limit_pct: float      # RE13: daily loss limit as fraction of total capital (> 0, <= 1)

    @field_validator("max_open_positions", "max_daily_trades", "max_consecutive_losses")
    @classmethod
    def _validate_positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be >= 1")
        return v

    @field_validator("max_sector_exposure_pct", "daily_loss_limit_pct")
    @classmethod
    def _validate_pct(cls, v: float) -> float:
        if not (0 < v <= 1):
            raise ValueError("must be > 0 and <= 1")
        return v


class SmtpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str                     # AW7: SMTP server hostname
    port: int                     # AW7: SMTP port (default 587)
    use_tls: bool                 # AW7: if True use STARTTLS
    username: str                 # AW7: SMTP auth username
    password: str = ""            # G.3: plaintext fallback ONLY for tests/dev;
                                  # production must use password_env. Empty
                                  # default lets YAML omit the field entirely
                                  # when password_env is set.
    password_env: str = ""        # G.3 (2026-04-25): name of env var holding
                                  # the SMTP password. Resolved at boot via
                                  # SmtpConfig.resolved_password(). Mirrors the
                                  # *_env convention used for telegram tokens.
    from_address: str             # AW7: envelope From address
    to_addresses: list[str]       # AW7: list of recipient addresses
    timeout_sec: int              # AW7: SMTP connection timeout in seconds

    @field_validator("port")
    @classmethod
    def _validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("port must be between 1 and 65535")
        return v

    @field_validator("timeout_sec")
    @classmethod
    def _validate_timeout(cls, v: int) -> int:
        if v < 1:
            raise ValueError("timeout_sec must be >= 1")
        return v

    @field_validator("to_addresses")
    @classmethod
    def _validate_to(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("to_addresses must not be empty")
        return v

    @model_validator(mode="after")
    def _validate_password_source(self) -> "SmtpConfig":
        # G.3: at least one password source must be set. We keep this loose
        # (does not require password_env in non-prod) but the resolver below
        # will fail at runtime if neither yields a value.
        if not self.password and not self.password_env:
            raise ValueError(
                "SmtpConfig: either password (dev/test) or password_env "
                "(production) must be set."
            )
        return self

    def resolved_password(self) -> str:
        """G.3: return the SMTP password, preferring env var. Raises
        ValueError when password_env is set but the env var is unset/empty
        (fail-fast at alert_watcher boot rather than at first send)."""
        import os
        if self.password_env:
            val = os.environ.get(self.password_env, "")
            if not val:
                raise ValueError(
                    f"SmtpConfig.password_env={self.password_env!r} is set "
                    f"but the env var is empty. Export it on the alert_watcher "
                    f"host (e.g. via systemd EnvironmentFile)."
                )
            return val
        return self.password


class TelegramChannelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id_env: str    # env var name whose value is the Telegram chat/group ID
    label: str          # human-readable label for logging
    enabled: bool = False  # only channels with enabled=True receive messages


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"  # env var holding the Bot API token
    telegram_alerts_in_paper_mode: bool = True  # if True, send real Telegram alerts in paper mode
    channels: list[TelegramChannelConfig]        # whitelist of known channels
    personal_chat_id_env: str = ""               # reserved for v2.1 bot commands; zero sends
    whitelist_only: bool = True                  # if True, only listed+enabled channels receive msgs

    @field_validator("channels")
    @classmethod
    def _validate_channels(cls, v: list) -> list:
        if not v:
            raise ValueError("channels must not be empty")
        return v


class OrderReconcilerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    poll_interval_sec: int        # RC17: periodic reconciliation cadence (P14 = 15s)
    capital_drift_tolerance: float  # RC17: max acceptable broker/local capital delta

    @field_validator("poll_interval_sec")
    @classmethod
    def _validate_poll_interval(cls, v: int) -> int:
        if v < 1:
            raise ValueError("poll_interval_sec must be >= 1")
        return v

    @field_validator("capital_drift_tolerance")
    @classmethod
    def _validate_drift_tolerance(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError("capital_drift_tolerance must be >= 0.0")
        return v


class AlertsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    failed_alerts_log_path: str   # TG12: path for ERROR-tier fallback log
    sentinel_dir: str             # TG12/CR2: directory for .flag sentinel files
    watcher_max_attempts: int     # AW11: max SMTP retry attempts before .failed
    watcher_lock_path: str        # AW11: lock file path for alert_watcher
    watcher_log_path: str         # AW11: alert_watcher own log file path
    telegram: TelegramConfig      # TG12: Telegram Bot API config
    smtp: SmtpConfig              # AW7: SMTP config for alert_watcher

    @field_validator("watcher_max_attempts")
    @classmethod
    def _validate_max_attempts(cls, v: int) -> int:
        if v < 1:
            raise ValueError("watcher_max_attempts must be >= 1")
        return v


class ShadowTrackerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool              # SH13: if False, all handlers become no-ops
    max_innings: int           # SH4: total innings allowed (1–5); inning 1 is real
    alert_per_inning: bool     # SH7: if True, send Telegram alert on each close

    @field_validator("max_innings")
    @classmethod
    def _validate_max_innings(cls, v: int) -> int:
        if not (1 <= v <= 5):
            raise ValueError("max_innings must be between 1 and 5 inclusive")
        return v


class PaperConfig(BaseModel):
    """
    H-20 / ZA16a: paper-mode fill synthesis settings.

    auto_fill_delay_sec: seconds to wait after place_order before the
        paper adapter's daemon thread transitions OSM SUBMITTED->COMPLETE
        and publishes OrderFilled. Default 0.5s mimics typical broker
        fill latency; set to 0 for synchronous-feel tests.

    ltp_gating_enabled: Audit 2.2 / 6.2 (locked 2026-04-24). When True,
        paper LIMIT/SL/SL-M orders only synthesise a fill when LTP has
        crossed the order condition. Pre-fix every LIMIT filled
        unconditionally, inflating paper P&L. Default False here so
        existing test fixtures keep working; production paper YAML sets
        True so the paper trial reflects realistic fills.
    ltp_gating_max_wait_sec: bounded poll horizon for LIMIT/SL synth.
        After this window without an LTP crossing, the order stays
        SUBMITTED (no synth-fill); the broker-equivalent behaviour is
        "still pending until cancelled" which order_timeout/EOD cleans up.
    ltp_gating_poll_sec: cadence at which the synth thread re-queries LTP.
    """
    model_config = ConfigDict(extra="forbid")
    auto_fill_delay_sec: float = 0.5  # >= 0; 0 = fire on next scheduler tick
    ltp_gating_enabled: bool = False
    ltp_gating_max_wait_sec: float = 60.0
    ltp_gating_poll_sec: float = 0.5

    @field_validator("auto_fill_delay_sec")
    @classmethod
    def _validate_delay(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"auto_fill_delay_sec must be >= 0, got {v!r}")
        if v > 10.0:
            raise ValueError(
                f"auto_fill_delay_sec must be <= 10.0 (sanity cap), got {v!r}"
            )
        return v

    @field_validator("ltp_gating_max_wait_sec")
    @classmethod
    def _validate_max_wait(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"ltp_gating_max_wait_sec must be >= 0, got {v!r}")
        if v > 3600.0:
            raise ValueError(
                f"ltp_gating_max_wait_sec must be <= 3600 (1h sanity cap), got {v!r}"
            )
        return v

    @field_validator("ltp_gating_poll_sec")
    @classmethod
    def _validate_poll(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"ltp_gating_poll_sec must be > 0, got {v!r}")
        if v > 30.0:
            raise ValueError(
                f"ltp_gating_poll_sec must be <= 30 (sanity cap), got {v!r}"
            )
        return v


class SmartTgtConfig(BaseModel):
    """
    BL-7b: deployment-wide defaults for SmartTgtManager trailing behavior.

    Per-strategy overrides are intentionally NOT supported here — today all
    intraday strategy YAMLs use identical values (trigger_pct=0.005,
    step_pct=0.003). When a future requirement demands per-strategy trails,
    add the override mechanism here rather than scattering YAML-reading logic
    across the codebase.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool              # master switch; if False, OrderPlacer skips register_trade
    trigger_pct: float         # fraction of entry price before first SL trail fires
    step_pct: float            # fraction of entry price per subsequent trail step

    @field_validator("trigger_pct", "step_pct")
    @classmethod
    def _fraction(cls, v: float) -> float:
        if not (0 < v < 1):
            raise ValueError(
                "must be a positive fraction < 1 "
                "(e.g. 0.005 for 0.5%, NOT 5 for 5%)"
            )
        return v


class DriftHandlerConfig(BaseModel):
    """
    BL-2: CapitalDriftHandler escalation thresholds (absolute rupee values).

    Absolute thresholds chosen over percentage-based for three reasons:
    (a) CapitalDriftDetected.delta is emitted in rupees by the escalating
        publisher (fund_manager.sync_from_broker / FM9), so thresholds can
        compare directly without division;
    (b) existing reconciler config (capital_drift_tolerance) is also
        absolute -- symmetry avoids mental mode-switching;
    (c) paper-trial scale (~Rs50k) makes percentage and absolute
        equivalent; revisit when live-account scale demands it.

    consecutive_cycles_before_escalate: a log-only drift that persists
    across this many consecutive fund_manager events auto-escalates to
    soft_kill. Reconciler-sourced events do NOT reset or increment this
    counter (the counter is fund_manager-scoped).
    """
    model_config = ConfigDict(extra="forbid")
    log_only_threshold_rs: float = 250.0       # below this = NOISE; at/above = LOG_ONLY
    soft_kill_threshold_rs: float = 1_000.0    # at/above this = SOFT kill tier
    hard_kill_threshold_rs: float = 2_500.0    # at/above this = HARD kill tier
    consecutive_cycles_before_escalate: int = 3

    @field_validator("log_only_threshold_rs", "soft_kill_threshold_rs",
                     "hard_kill_threshold_rs")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"drift threshold must be > 0, got {v!r}")
        return v

    @field_validator("consecutive_cycles_before_escalate")
    @classmethod
    def _positive_cycles(cls, v: int) -> int:
        if v < 1:
            raise ValueError(
                f"consecutive_cycles_before_escalate must be >= 1, got {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _validate_ordering(self) -> "DriftHandlerConfig":
        if not (self.log_only_threshold_rs
                < self.soft_kill_threshold_rs
                < self.hard_kill_threshold_rs):
            raise ValueError(
                "drift thresholds must satisfy "
                "log_only < soft_kill < hard_kill "
                f"(got log_only={self.log_only_threshold_rs}, "
                f"soft_kill={self.soft_kill_threshold_rs}, "
                f"hard_kill={self.hard_kill_threshold_rs})"
            )
        return self


class SystemConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trading_hours: TradingHoursConfig
    signal_queue: SignalQueueConfig
    product_map: dict[str, dict[str, str]]  # broker -> {INTENT -> code} (PR8)
    clock: ClockConfig
    order_monitor: OrderMonitorConfig         # OM14
    capital: CapitalConfig                    # FM16
    position_sizing: PositionSizingConfig     # PS9
    risk: RiskConfig                          # RE13
    kill_switch: KillSwitchConfig             # KS7
    webhook: WebhookConfig                    # WR14
    signal_processor: SignalProcessorConfig   # SP15
    eod_squareoff: EodSquareoffConfig         # EOD12
    alerts: AlertsConfig                      # TG12/AW11: alert subsystem config
    order_reconciler: OrderReconcilerConfig   # RC17: reconciler tuning
    shadow_tracker: ShadowTrackerConfig       # SH11: multi-inning tracking config
    smart_tgt: SmartTgtConfig                 # BL-7b: SmartTgtManager defaults
    paper: PaperConfig                        # H-20/ZA16a: paper fill synthesis
    drift_handler: DriftHandlerConfig         # BL-2: drift escalation policy


# ─────────────────────────────────────────────────────────────────────────────
# broker_costs.yaml — BrokerCostsConfig
# Locked: P12 (identical rates in live and paper mode)
# ─────────────────────────────────────────────────────────────────────────────

class ZerodhaRatesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brokerage_flat_intraday: float   # flat ₹ per executed order cap (MIS/CO/CNC SELL)
    brokerage_pct_intraday: float    # % of turnover (MIS/CO/CNC SELL); min with flat (CC3)
    stt_sell_pct: float              # % on sell turnover for MIS/CO intraday sell only (CC4)
    stt_cnc_pct: float               # % on turnover for CNC both sides (CC4)
    exchange_txn_pct: float          # % on turnover (NSE equity segment) (CC5)
    gst_pct: float                   # % on (brokerage + exchange_txn + sebi) (CC6)
    sebi_pct: float                  # % on turnover (CC7)
    stamp_duty_mis_buy_pct: float    # % on buy turnover for MIS/CO (CC8; was stamp_duty_buy_pct)
    stamp_duty_cnc_buy_pct: float    # % on buy turnover for CNC (CC8)


class BrokerCostsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    zerodha: ZerodhaRatesConfig


# ─────────────────────────────────────────────────────────────────────────────
# broker_limits.yaml — BrokerLimitsConfig
# Locked: G7 (client-side token bucket per endpoint category)
# ─────────────────────────────────────────────────────────────────────────────

class TokenBucketConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    burst: int          # max tokens available (burst capacity)
    rate_per_sec: int   # refill rate (tokens per second)


class TimeoutsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connect_sec: int   # TCP connection timeout (ZA12)
    read_sec: int      # response read timeout (ZA12)


class RateLimitBackoffConfig(BaseModel):
    """
    BL-6: exponential backoff applied to rate_limiter.penalize() when the
    broker returns HTTP 429. Distinct from backoff_sequence_sec (G7), which
    is the soft-kill escalation ladder for sustained rate-limit failure.

    The schedule used on attempt N (0-indexed, per-category):
        delay_sec = min(max_delay_sec, initial_delay_sec * multiplier**N)
                    + uniform(-jitter_sec, +jitter_sec)

    Jitter decorrelates concurrent callers that would otherwise all penalize
    and retry on identical schedules. max_placer_retries is the hard cap
    on placer-level retries (BL-19) before BrokerRateLimit429Error propagates.
    """
    model_config = ConfigDict(extra="forbid")
    initial_delay_sec: float = 0.2
    max_delay_sec: float = 5.0
    max_placer_retries: int = 3
    multiplier: float = 2.0
    jitter_sec: float = 0.05

    @field_validator("initial_delay_sec", "max_delay_sec", "jitter_sec")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"rate_limit_backoff delay must be >= 0, got {v!r}")
        return v

    @field_validator("multiplier")
    @classmethod
    def _multiplier_ge_one(cls, v: float) -> float:
        if v < 1.0:
            raise ValueError(
                f"rate_limit_backoff.multiplier must be >= 1.0 "
                f"(exponential growth, not decay), got {v!r}"
            )
        return v

    @field_validator("max_placer_retries")
    @classmethod
    def _non_negative_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError(
                f"rate_limit_backoff.max_placer_retries must be >= 0, got {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _validate_delays(self) -> "RateLimitBackoffConfig":
        if self.max_delay_sec < self.initial_delay_sec:
            raise ValueError(
                f"rate_limit_backoff.max_delay_sec ({self.max_delay_sec}) "
                f"must be >= initial_delay_sec ({self.initial_delay_sec})"
            )
        return self


class BrokerLimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: TokenBucketConfig
    quote: TokenBucketConfig
    historical: TokenBucketConfig
    margins: TokenBucketConfig
    backoff_sequence_sec: list[int]   # G7: 1s / 5s / 30s before soft_kill
    timeouts: TimeoutsConfig          # ZA12: kiteconnect HTTP timeouts
    # BL-6: 429 exponential backoff (default applied if yaml omits the block)
    rate_limit_backoff: RateLimitBackoffConfig = Field(
        default_factory=RateLimitBackoffConfig
    )


# ─────────────────────────────────────────────────────────────────────────────
# slippage_model.yaml — SlippageConfig
# Locked: P12 (per-tier slippage applied in paper and live cost calculations)
# ─────────────────────────────────────────────────────────────────────────────

class SlippageTierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slippage_bps: int   # basis points (1 bps = 0.01%)


class SlippageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tiers: dict[str, SlippageTierConfig]   # tier_name → config
    default_tier: str                       # used when symbol tier is unknown


# ─────────────────────────────────────────────────────────────────────────────
# scoring_weights.yaml — ScoringConfig
# Locked: P9b (all weights and thresholds config-driven; nothing hardcoded)
# ─────────────────────────────────────────────────────────────────────────────

class ScoringStepsConfig(BaseModel):
    """One integer weight per screener step. All 10 required (P9b)."""
    model_config = ConfigDict(extra="forbid")
    volume_surge: int
    vwap_position: int
    atr_filter: int
    rsi_range: int
    price_action: int
    sector_strength: int
    time_of_day: int
    spread_check: int
    circuit_check: int
    signal_age: int


class TierMultipliersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    high: float
    medium: float
    low: float


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: ScoringStepsConfig
    min_pass_score: int
    tier_multipliers: TierMultipliersConfig
    high_score_threshold: int
    medium_score_threshold: int


# ─────────────────────────────────────────────────────────────────────────────
# scan_webhook_map.yaml — ScanWebhookMapConfig
# Locked: P17 (no duplicate scanner names; every name maps to a valid strategy YAML)
# ─────────────────────────────────────────────────────────────────────────────

class ScannerEntry(BaseModel):
    """One scanner → strategy mapping entry (S14 format)."""
    model_config = ConfigDict(extra="forbid")
    strategy: str
    chartink_url: str

    @field_validator("chartink_url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"chartink_url must be http(s): {v}")
        return v


class ScanWebhookMapConfig(BaseModel):
    """Maps Chartink scanner names to ScannerEntry (strategy + chartink_url)."""
    model_config = ConfigDict(extra="forbid")
    scanners: dict[str, ScannerEntry]


# ─────────────────────────────────────────────────────────────────────────────
# chartink_scanners.yaml — ChartinkScannersConfig
# Locked: P17 (URLs checked via HTTP HEAD at startup preflight)
# ─────────────────────────────────────────────────────────────────────────────

class ChartinkScannersConfig(BaseModel):
    """Canonical Chartink scanner URLs for pre-flight health check."""
    model_config = ConfigDict(extra="forbid")
    scanners: dict[str, str]   # scanner_name → full Chartink URL


# ─────────────────────────────────────────────────────────────────────────────
# nse_holidays_2026.yaml — NseHolidaysConfig
# ─────────────────────────────────────────────────────────────────────────────

class HolidayEntry(BaseModel):
    """One NSE holiday entry with date and descriptive name."""
    model_config = ConfigDict(extra="forbid")
    date: _date
    name: str

    @field_validator("date", mode="before")
    @classmethod
    def _parse_iso_date(cls, v: object) -> _date:
        if isinstance(v, str):
            return _date.fromisoformat(v)
        if isinstance(v, _date):
            return v
        raise ValueError(f"Expected YYYY-MM-DD string for date, got {v!r}")


class NseHolidaysConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    holidays: list[HolidayEntry]


# ─────────────────────────────────────────────────────────────────────────────
# AppConfig — container for all loaded configs (CL1)
# ─────────────────────────────────────────────────────────────────────────────

class AppConfig(BaseModel):
    """
    Holds all validated sub-configs and per-file SHA-256 hashes.
    Constructed only by load_all(); never instantiated directly by other modules.
    """
    model_config = ConfigDict(extra="forbid")
    system: SystemConfig
    broker_costs: BrokerCostsConfig
    broker_limits: BrokerLimitsConfig
    slippage: SlippageConfig
    scoring: ScoringConfig
    scan_webhook_map: ScanWebhookMapConfig
    chartink_scanners: ChartinkScannersConfig
    nse_holidays: NseHolidaysConfig
    file_hashes: dict[str, str]   # filename → sha256 hexdigest (CL4)


# ─────────────────────────────────────────────────────────────────────────────
# File registry — single source of truth for file ↔ AppConfig key ↔ schema
# ─────────────────────────────────────────────────────────────────────────────

_CONFIG_FILES: tuple[tuple[str, str, type[BaseModel]], ...] = (
    ("system",            "system_config.yaml",    SystemConfig),
    ("broker_costs",      "broker_costs.yaml",     BrokerCostsConfig),
    ("broker_limits",     "broker_limits.yaml",    BrokerLimitsConfig),
    ("slippage",          "slippage_model.yaml",   SlippageConfig),
    ("scoring",           "scoring_weights.yaml",  ScoringConfig),
    ("scan_webhook_map",  "scan_webhook_map.yaml", ScanWebhookMapConfig),
    ("chartink_scanners", "chartink_scanners.yaml",ChartinkScannersConfig),
    ("nse_holidays",      "nse_holidays_2026.yaml",NseHolidaysConfig),
)


# ─────────────────────────────────────────────────────────────────────────────
# Public API (CL1)
# ─────────────────────────────────────────────────────────────────────────────

def load_all(config_dir: Path = Path("config")) -> AppConfig:
    """
    Load and validate all 8 config files from config_dir (CL1, CL2).

    Reads each file, hashes raw bytes (CL4), parses YAML with safe_load (CL5),
    and validates against the corresponding Pydantic schema (CL3). All 8 files
    must succeed — any failure raises immediately; no partial AppConfig returned.

    Args:
        config_dir: directory containing the YAML files.
                    Default is Path("config") relative to the process cwd.
                    Override in tests by passing a tmp directory.

    Returns:
        AppConfig with all validated sub-configs and file_hashes populated.

    Raises:
        ConfigMissingError: a required file does not exist.
        ConfigSchemaError:  a file exists but YAML parse or schema validation fails.
    """
    validated: dict[str, object] = {}
    hashes: dict[str, str] = {}

    for key, filename, schema_cls in _CONFIG_FILES:
        path = config_dir / filename

        if not path.is_file():
            raise ConfigMissingError(
                f"Required config file not found: {filename}",
                file=filename,
                path=str(path),
            )

        raw_bytes = path.read_bytes()
        hashes[filename] = hashlib.sha256(raw_bytes).hexdigest()

        try:
            data = yaml.safe_load(raw_bytes)
        except yaml.YAMLError as exc:
            raise ConfigSchemaError(
                f"YAML parse error in {filename}: {exc}",
                file=filename,
                path=str(path),
            ) from exc

        # safe_load returns None for an empty file — normalise to empty dict
        if data is None:
            data = {}

        try:
            validated[key] = schema_cls.model_validate(data)
        except ValidationError as exc:
            raise ConfigSchemaError(
                f"Schema validation failed for {filename}",
                file=filename,
                error_count=exc.error_count(),
                errors=exc.errors(),
            ) from exc

    return AppConfig(**validated, file_hashes=hashes)
