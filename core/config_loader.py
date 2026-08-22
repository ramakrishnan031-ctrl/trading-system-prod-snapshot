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
from datetime import time as _dt_time
from pathlib import Path

import yaml
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from core.exceptions import ConfigMissingError, ConfigSchemaError


# ─────────────────────────────────────────────────────────────────────────────
# system_config.yaml — SystemConfig
# Locked: P1, P2, P4, P11b, P14, P15, Q1, G4
# ─────────────────────────────────────────────────────────────────────────────

# ── The exclusive upper bound for trading_hours.service_window_end ───────────
# WHY 18:15 AND NOT A ROUND NUMBER: it is the forward-shadow recorder's cron time
# (cron_registry.yaml -> forward_shadow_record, "18:15 Mon-Fri"). That job is the only
# out-of-sample evidence producer in the system and what it writes cannot be
# regenerated. A configured shutdown at/after it would leave the service alive while
# it runs.
#
# This is POSTURE, not a concurrency finding. WAL would in fact tolerate the overlap —
# the 16:05 daily report already reads against the live writer every trading day. The
# rule is that nothing in the evening pipeline should ever have to reason about whether
# a live writer is present. Said plainly on purpose: the weaker "they would contend"
# argument is wrong, and a future reader would be right to relax a bound resting on it.
#
# DRIFT-GUARDED, not merely commented — tests/unit/test_service_window_config.py::
# test_max_is_before_the_forward_shadow_job reads the schedule from the registry and
# fails if this bound ever reaches it. Move that cron and the bound must move with it.
#
# SECOND ROLE: main.SERVICE_START_CUTOFF binds to this value, so the LATEST the service
# may START is >= every legal stop time. That makes the crash-restart trap
# unrepresentable rather than untested.
# See docs/audit/service_window_configurable_25jul2026.md §3.
SERVICE_WINDOW_END_MAX = _dt_time(18, 15)


class TradingHoursConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_start: str           # "HH:MM" IST — entry window opens (P1)
    entry_end: str             # "HH:MM" IST — entry window closes (P1)
    eod_entry_cutoff: str      # FIX-073: "HH:MM" IST — absolute last entry moment
    eod_squareoff_time: str    # "HH:MM" IST — square-off trigger (P1)
    market_open: str = "09:15"   # "HH:MM" IST — NSE regular-session open
    market_close: str = "15:30"  # "HH:MM" IST — NSE regular-session close
    # "HH:MM" IST — when the service self-exits for the day
    # (main._start_eod_self_exit_thread). DEFAULTED so that under extra="forbid" each
    # half of a deploy is independently safe (schema-without-key and key-without-schema
    # would both otherwise fail the boot), and so a revert is a one-line config edit.
    service_window_end: str = "16:00"

    @model_validator(mode="after")
    def _validate_window_ordering(self) -> "TradingHoursConfig":
        """
        2026-04-26 audit CFG-1: catch operator typos that would otherwise
        silently shrink (or invert) the entry window. P1 mandates
        entry_start < entry_end and market_open <= entry_start
        and entry_end <= eod_entry_cutoff <= eod_squareoff_time <= market_close.

        2026-07-25: the chain is extended one link to service_window_end, so the
        shutdown time is bounded by the SAME mechanism as the rest of the family:
        market_close <= service_window_end < SERVICE_WINDOW_END_MAX.
        Below market_close would cut the session short; at/after the max would leave
        the service alive for the forward-shadow recorder (see the constant).
        This fires at CONFIG LOAD — before StateStore (main.py:1767) or any broker
        handle exists — so a bad value is a loud, early boot failure, never a
        silently-wrong shutdown time.
        """
        from datetime import time as _time

        def _hhmm(s: str) -> _time:
            h, m = s.split(":")
            return _time(int(h), int(m))

        mo = _hhmm(self.market_open)
        es = _hhmm(self.entry_start)
        ee = _hhmm(self.entry_end)
        eec = _hhmm(self.eod_entry_cutoff)
        eod = _hhmm(self.eod_squareoff_time)
        mc = _hhmm(self.market_close)
        if not (mo <= es < ee <= eec <= eod <= mc):
            raise ValueError(
                "trading_hours ordering violation; require "
                "market_open <= entry_start < entry_end <= eod_entry_cutoff <= "
                "eod_squareoff_time <= market_close, got "
                f"market_open={self.market_open}, entry_start={self.entry_start}, "
                f"entry_end={self.entry_end}, eod_entry_cutoff={self.eod_entry_cutoff}, "
                f"eod_squareoff_time={self.eod_squareoff_time}, "
                f"market_close={self.market_close}"
            )

        swe = _hhmm(self.service_window_end)
        if not (mc <= swe < SERVICE_WINDOW_END_MAX):
            raise ValueError(
                "trading_hours.service_window_end out of range; require "
                f"market_close <= service_window_end < "
                f"{SERVICE_WINDOW_END_MAX:%H:%M} (the forward-shadow recorder's cron "
                "time; see SERVICE_WINDOW_END_MAX), got "
                f"market_close={self.market_close}, "
                f"service_window_end={self.service_window_end}"
            )
        return self


class SignalQueueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capacity: int              # max signals in queue (P15 default: 300)
    backpressure_pct: float    # HTTP 503 at capacity × pct (P15 default: 0.80)
    expiry_sec: int            # WR6: reject signal older than this many seconds (default 60)
    warning_pct: float = 0.60  # FIX-134 Item 35: X-Queue-Warning header above this fill %


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
    # SLICE2.5-PHASE-3 (B): when TRUE, the intraday/positional split is resolved
    # CONDITIONALLY from the active trade types (only-intraday->100/0,
    # only-delivery->0/100, both->the fixed split above) instead of always using the
    # fixed split. Default FALSE = the fixed split, byte-for-byte unchanged. Computed
    # in main.py via fund_manager.resolve_bucket_allocation(); FundManager itself only
    # ever receives the final pcts (it is unchanged).
    conditional_allocation_enabled: bool = False
    # BUILD 1 (#1, 24-Jun-2026): the absolute `daily_loss_limit` (₹) was DELETED.
    # daily_loss_limit_pct (risk:) is now the SOLE daily-loss authority — the
    # post-close realized breach (FundManager) derives its ₹ limit as
    # daily_loss_limit_pct × current capital, the same pct the pre-trade gate uses.
    slm_margin_buffer_pct: float = 0.05  # FIX-090: SL-M margin buffer % (unknown fill price risk)
    sl_limit_offset_pct: float = 0.005   # P0 2026-06-15: limit offset past trigger for SL (stop-limit) legs
    # SLICE2.5-P1: DEEP protective limit offset for a CNC OCO-GTT SL leg (separate
    # from the 0.5% intraday offset). A wider offset (default 3%) so an overnight
    # gap-down still fills within the floor instead of resting unfilled.
    gtt_sl_limit_offset_pct: float = 0.03
    emergency_exit_buffer_pct: float = 0.01  # FIX-181: marketable-LIMIT buffer for emergency/kill exits
    leverage_map: LeverageMapConfig  # FM16: per-intent leverage multiplier

    @field_validator("sl_limit_offset_pct")
    @classmethod
    def _validate_sl_limit_offset(cls, v: float) -> float:
        # 0 is allowed (limit == trigger, tight fill) but negative or >= 10% is a typo.
        if v < 0 or v >= 0.10:
            raise ValueError("sl_limit_offset_pct must be >= 0 and < 0.10 (10%)")
        return v

    @field_validator("gtt_sl_limit_offset_pct")
    @classmethod
    def _validate_gtt_sl_limit_offset(cls, v: float) -> float:
        # SLICE2.5-P1: a DEEP protective offset (default 3%); must be > 0 (a 0% GTT
        # SL limit risks resting unfilled on a gap) and < 20% (a wider offset is a typo).
        if not (0 < v < 0.20):
            raise ValueError("gtt_sl_limit_offset_pct must be > 0 and < 0.20 (20%)")
        return v

    @field_validator("emergency_exit_buffer_pct")
    @classmethod
    def _validate_emergency_exit_buffer(cls, v: float) -> float:
        # 0 is allowed (limit == LTP) but negative or >= 10% is a typo.
        if v < 0 or v >= 0.10:
            raise ValueError("emergency_exit_buffer_pct must be >= 0 and < 0.10 (10%)")
        return v

    @field_validator("intraday_bucket_pct", "positional_bucket_pct")
    @classmethod
    def _validate_bucket_pct(cls, v: float) -> float:
        if not (0 < v < 1):
            raise ValueError("bucket_pct must be between 0 and 1 exclusive")
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


class MisFilterConfig(BaseModel):
    """MIS Learned Blocklist — source-free pre-drop of MIS-blocked symbols.

    Default OFF -> fully dormant (existing flow byte-identical). When enabled,
    `shadow` controls log-only vs actual reject. `ttl_days` is the re-test window:
    a symbol blocked by the broker's MIS-400 is pre-dropped for this many calendar
    days, then allowed through to re-test (self-correcting). See core/mis_blocklist.py.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False     # master switch; False = no filtering (dormant)
    shadow: bool = True       # when enabled: True = log would-drop only; False = actually reject
    ttl_days: int = 1         # re-test TTL in calendar days (>= 1; default 1 = daily re-test)

    @field_validator("ttl_days")
    @classmethod
    def _validate_ttl_days(cls, v: int) -> int:
        if v < 1:
            raise ValueError("mis_filter.ttl_days must be >= 1")
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
    lot_skew_rejection_threshold: float  # FIX-021: reject if (tiered-final)/tiered > threshold
    min_tick_size: float               # FIX-041: min SL distance (penny stock guard)
    max_single_order_qty: int          # FIX-041: sanity cap on computed qty
    # FIX-144 / BUILD 1 (#2, 24-Jun-2026): capital-relative catastrophic-loss /
    # bug-guard. The cap = max_position_value_pct × current capital, computed at
    # sizing time. REJECT (not clamp) if qty*price exceeds it. Was a fixed ₹2500
    # (a ₹10k-era cap with a hard scaling cliff); now scales with the account.
    # Routine sizing is still governed by concentration (10%) + risk (1%); this
    # only fires on an anomaly.
    max_position_value_pct: float      # FIX-144: hard cap on qty*price as fraction of capital
    tier_multipliers: PositionSizingTierConfig  # PS5
    dynamic_by_winrate: bool = True            # FIX-133 Item 21: enable perf-weighted sizing
    min_multiplier: float = 0.5                # FIX-133 Item 21: floor for perf weight
    max_multiplier: float = 2.0                # FIX-133 Item 21: cap for perf weight
    enabled: bool = True                       # Diary #4: ON = score-tier × perf sizing (default)
    flat_value_rs: Optional[float] = None      # Diary #4: flat Rs/order; required (>0) when enabled=False
    # ---- DELIVERY-scoped sizing limits: REQUIRED, and there is NO inheritance ----
    # 22-Aug-2026 (fix item 1). These replace a scaffold whose comment asserted that the
    # V3 delivery path was dormant and that these keys therefore went unread.
    # BOTH CLAIMS WERE FALSE. Delivery is live and has traded (three recorded CNC round
    # trips), and the sizer read these on EVERY positional entry — silently inheriting
    # the INTRADAY numbers whenever they were null. That inheritance is what this build
    # removes; the stale comment is what hid it for weeks.
    #
    # Declared Optional[float] with NO DEFAULT on purpose, so both failure modes are
    # loud and both name the key:
    #   * no default  → a MISSING key fails as "Field required" at loc=<the key>;
    #   * Optional[]  → an explicit `null` reaches the validator below, which rejects it
    #                   with a message saying WHY, still at loc=<the key>.
    # Either way load_all raises ConfigSchemaError, main._config_error_detail renders
    # "<key>: <reason>", and the boot logs it at CRITICAL and returns 5. No code path
    # turns an absent delivery value into the global one.
    delivery_risk_per_trade_pct: Optional[float]
    delivery_max_concentration_pct: Optional[float]
    delivery_max_position_value_pct: Optional[float]

    @field_validator("delivery_risk_per_trade_pct",
                     "delivery_max_concentration_pct",
                     "delivery_max_position_value_pct")
    @classmethod
    def _validate_delivery_pct(cls, v: Optional[float]) -> float:
        if v is None:
            raise ValueError(
                "delivery sizing pct must be set explicitly to a number in (0, 1]; "
                "it does NOT fall back to the global (intraday) value"
            )
        if not (0 < v <= 1):
            raise ValueError("delivery sizing pct must be in (0, 1]")
        return v

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

    @field_validator("lot_skew_rejection_threshold")
    @classmethod
    def _validate_lot_skew_threshold(cls, v: float) -> float:
        if not (0 < v <= 1):
            raise ValueError("lot_skew_rejection_threshold must be between 0 (exclusive) and 1 (inclusive)")
        return v

    @field_validator("max_position_value_pct")
    @classmethod
    def _validate_max_position_value_pct(cls, v: float) -> float:
        # BUILD 1 (#2): capital-relative cap; must be a sane fraction of capital.
        if not (0 < v <= 1):
            raise ValueError("max_position_value_pct must be between 0 (exclusive) and 1 (inclusive)")
        return v

    @model_validator(mode="after")
    def _validate_flat_value(self) -> "PositionSizingConfig":
        # Diary #4: OFF (flat) mode requires a positive flat_value_rs; reject startup
        # otherwise so we never silently run flat sizing with no rupee target.
        if not self.enabled:
            if self.flat_value_rs is None or self.flat_value_rs <= 0:
                raise ValueError(
                    "tier multiplier disabled (position_sizing.enabled=false) requires "
                    "position_sizing.flat_value_rs > 0"
                )
        return self


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
    # FIX-190 (Bug G): entry throttle — spaces out placed entries to prevent a
    # burst (the 5-entries-in-5s Chartink spike on 19-Jun). All default OFF so
    # existing configs/tests are unaffected; system_config enables them.
    #   min_gap_between_entries_sec: reject a new entry placed < this since the last.
    #   entry_burst_max placed entries allowed within entry_burst_window_sec (0=off).
    min_gap_between_entries_sec: float = 0.0
    entry_burst_window_sec: float = 60.0
    entry_burst_max: int = 0
    # Bug G (full): per-symbol cooldown — no re-entry of the SAME symbol within
    # this many seconds (stops rapid same-symbol churn). 0 = off.
    per_symbol_cooldown_sec: float = 0.0

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
    dedup_window_seconds: int = 300  # FIX-131 Item 17: fingerprint bucket width
    # C-2 (02-Jul-2026): per-source-IP rate limit (token bucket). Bounds flooding
    # from a single source while tolerating Chartink's legitimate open-bell burst
    # (~40 signals from one IP). Keyed on request.remote_addr; 429 on exceed.
    per_ip_rate_limit_enabled: bool = True
    per_ip_burst: int = 60              # tokens for an instantaneous burst
    per_ip_refill_per_sec: float = 5.0  # sustained requests/sec/IP after the burst

    @field_validator("per_ip_burst")
    @classmethod
    def _validate_per_ip_burst(cls, v: int) -> int:
        if v < 1:
            raise ValueError("webhook.per_ip_burst must be >= 1")
        return v

    @field_validator("per_ip_refill_per_sec")
    @classmethod
    def _validate_per_ip_refill(cls, v: float) -> float:
        if v < 0:
            raise ValueError("webhook.per_ip_refill_per_sec must be >= 0")
        return v


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
    # SLICE2.5-PHASE-3 (A): SEPARATE delivery-scoped count caps, parallel to the
    # intraday/global caps. Enforced ONLY for a delivery (CNC) entry
    # (sizing_result.bucket=="positional"); an intraday entry is unaffected.
    # NI-3 (22-Aug-2026): a clause here used to say these caps did nothing while
    # force_intraday_only coerced every strategy to INTRADAY. ⛔ THAT IS NOT THE STATE --
    # force_intraday_only is FALSE, delivery_enabled TRUE, trade_type BOTH, and delivery
    # has traded. Not repeated verbatim: a stale claim quoted in place still reads as
    # current; the wording is in the commit diff. This was the FOURTH copy of that same
    # false-antecedent claim on these two keys -- the card named three (the two YAML
    # lines and the two risk_engine sites) and a sweep found this one too.
    # Defaults make them optional in YAML.
    max_open_delivery_positions: int = 3   # hard cap on concurrent open DELIVERY positions (>= 1)
    max_daily_delivery_trades: int = 5     # hard cap on DELIVERY entries per day (>= 1)
    # 27-Jul-2026: one COMPLETED trade per symbol+DIRECTION per trading day.
    # false (default) = OFF, byte-identical to the pre-27-Jul path. true = a second
    # entry on the same symbol in the same direction is rejected for the rest of the
    # day. Opposite direction is ALWAYS allowed (a long exit followed by a short
    # entry is a reversal, not a re-entry). Ships OFF: the whole book contains only
    # 2 re-entries, so there is no evidence base for turning it on.
    one_trade_per_symbol_direction_per_day: bool = False
    max_sector_exposure_pct: float   # RE13: max fraction of capital in one sector (> 0, <= 1)
    max_consecutive_losses: int      # RE13: halt after N consecutive losses (>= 1)
    daily_loss_limit_pct: float      # RE13: daily loss limit as fraction of total capital (> 0, <= 1)
    # ---- DELIVERY-scoped pre-trade gate limits: REQUIRED, no inheritance --------
    # 22-Aug-2026 (fix item 1). Same contract and same declaration trick as the
    # position_sizing delivery keys: Optional[float] with NO default, so a missing key
    # AND an explicit null both fail loudly at load, each naming the key. The gates
    # read ONLY the delivery value for a delivery (CNC) entry and ONLY the global value
    # for an intraday entry — neither can move the other.
    #
    # SCOPE, recorded so this is never over-read: delivery_daily_loss_limit_pct scopes
    # the PRE-TRADE gate (risk_engine check 7) ONLY. The post-close portfolio circuit
    # breaker in fund_manager stays GLOBAL — there is one account-wide realized P&L and
    # no per-book attribution to split it with. CONSECUTIVE_LOSSES stays shared too.
    delivery_max_sector_exposure_pct: Optional[float]
    delivery_daily_loss_limit_pct: Optional[float]
    # B-1 (02-Jul): ENFORCE flag for the daily-loss gate's unrealized-MTM term.
    # false = SHADOW (the reconciler populates MTM + the gate LOGS would_reject_with_
    # unrealized but ENFORCES realized-only → zero behaviour change). true = enforce
    # realized+unrealized. Default false so the fix ships dark and is validated before
    # it changes a capital gate. Reversible by flipping this flag.
    daily_loss_include_unrealized: bool = False
    price_drift_threshold: float = 0.005  # FIX-075: 0.5% default drift threshold for margin top-up
    # F1 (16-Jul): sector concentration cap (gate-8) mode + data-quality threshold.
    # sector_cap_mode gates the SECTOR_EXPOSURE check: "observe" (DEFAULT) LOGS a would-reject
    # record but does NOT reject (behaviour-neutral — trades.sector now fills, the 40% cap only
    # logs); "enforce" rejects as designed. Flip observe->enforce ONLY after an observe soak
    # (>=1 session) + Rama's explicit approval (activating a live capital gate). Reversible.
    # sector_unknown_alert_pct fires a one-shot data-quality alert if the UNKNOWN-sector fraction
    # of inserted trades exceeds it (the cap is only as good as trades.sector).
    sector_cap_mode: str = "observe"
    sector_unknown_alert_pct: float = 0.20

    # BUILD 1 (#3, 24-Jun-2026): the FIX-190 live_test_* override fields were
    # DELETED. The live_test caps had been set EQUAL to the base caps
    # (max_open_positions=5, max_daily_trades=10) for parity, so the main.py swap
    # was a no-op that only LOOKED active — misleading dead config. The base caps
    # above are now the sole authority in both paper and live.

    @field_validator("max_open_positions", "max_daily_trades", "max_consecutive_losses",
                     "max_open_delivery_positions", "max_daily_delivery_trades")
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

    @field_validator("delivery_max_sector_exposure_pct", "delivery_daily_loss_limit_pct")
    @classmethod
    def _validate_delivery_gate_pct(cls, v: Optional[float]) -> float:
        # A null is rejected here, not defaulted: a delivery gate limit that is absent
        # must stop the boot, never inherit the intraday one.
        if v is None:
            raise ValueError(
                "delivery gate pct must be set explicitly to a number in (0, 1]; "
                "it does NOT fall back to the global (intraday) value"
            )
        if not (0 < v <= 1):
            raise ValueError("delivery gate pct must be > 0 and <= 1")
        return v

    @field_validator("sector_cap_mode")
    @classmethod
    def _validate_sector_cap_mode(cls, v: str) -> str:
        if v not in ("observe", "enforce"):
            raise ValueError("sector_cap_mode must be 'observe' or 'enforce'")
        return v

    @field_validator("sector_unknown_alert_pct")
    @classmethod
    def _validate_unknown_pct(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("sector_unknown_alert_pct must be between 0 and 1")
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


class EmailFallbackConfig(BaseModel):
    """FIX-132 Item 10: email fallback when Telegram fails for CRITICAL alerts."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    from_addr_env: str = "ALERT_EMAIL_USER"
    password_env: str = "ALERT_EMAIL_PASSWORD"
    to_addr_env: str = "ALERT_EMAIL_TO"
    use_tls: bool = True

    @field_validator("smtp_port")
    @classmethod
    def _validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("smtp_port must be between 1 and 65535")
        return v


class TelegramChannelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id_env: str    # env var name whose value is the Telegram chat/group ID
    label: str          # human-readable label for logging
    enabled: bool = False  # only channels with enabled=True receive messages


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True  # TASK-10: master ON/OFF switch. False = suppress ALL Telegram alerts (silent)
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"  # env var holding the Bot API token
    telegram_alerts_in_paper_mode: bool = True  # if True, send real Telegram alerts in paper mode
    channels: list[TelegramChannelConfig]        # whitelist of known channels
    personal_chat_id_env: str = ""               # reserved for v2.1 bot commands; zero sends
    whitelist_only: bool = True                  # if True, only listed+enabled channels receive msgs
    max_retries: int = 3                         # FIX-131 Item 18: retry attempts for failed sends
    retry_backoff_seconds: float = 2.0           # FIX-131 Item 18: backoff delay between retries
    rate_limit_per_minute: int = 20              # FIX-131 Item 18: max messages per minute
    # M-A2: whole-send wall-clock budget (seconds), shared across enabled channels
    # and covering the rate-limit wait + HTTP timeouts + backoff sleeps. Sends are
    # synchronous on live threads, so this is what stops a hung Telegram endpoint
    # from holding an order/kill/signal path. null = unbounded (pre-M-A2).
    # 25-Jul-2026: 30.0 -> 8.0. 30 sat above the measured ~26 s ladder, so it
    # barely bound on the order path it exists to protect. 8 = one full HTTP
    # attempt (5) + one backoff (2) + margin. Tunable from yaml; null = unbounded.
    send_deadline_seconds: Optional[float] = 8.0

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
    # FIX-190 (Bug I): in-session percentage tolerance. During market hours broker
    # margin legitimately drops by the deployed capital, so the absolute Rs
    # tolerance fires constantly (the 10:00 Δ503 noise). In-session the effective
    # tolerance becomes max(Rs, expected*pct). 0.0 disables (back-compat default).
    capital_drift_tolerance_pct: float = 0.0
    # FIX-182: extra capital-drift allowance (Rs) applied when human / untracked
    # broker orders are detected on the day. Their blocked margin legitimately
    # widens the broker-vs-local gap; this prevents CRITICAL drift alert spam
    # while still catching genuine catastrophic drift beyond the allowance.
    human_order_margin_tolerance: float = 5000.0
    # TASK-11: minimum seconds between repeat CAPITAL_DRIFT alerts. First
    # detection alerts immediately; thereafter the alert is throttled to at most
    # once per this interval (default 1800s / 30 min). Replaces the old
    # exponential backoff (2min/8min/30min ramp) for this check with a single
    # operator-tunable cadence. Informational only — kill escalation is governed
    # separately by drift_handler thresholds.
    capital_drift_alert_interval_sec: float = 1800.0
    # Task 4 (2026-06-19): a trade marked EXITING by a HARD_KILL / emergency
    # flatten that never finalized (process died mid-exit — the 19-Jun incident)
    # lingers with locked capital + orphan SL/TGT, untouched by CHECK1/G5b (they
    # only act on OPEN/PARTIAL/PENDING_FILL). The reconciler resolves EXITING
    # trades older than this many minutes against broker truth (flat ->
    # CLOSED_MANUAL; still held -> back to OPEN for normal management). A fresh
    # EXITING (an exit legitimately in progress) is left alone until it ages past
    # this threshold.
    stuck_exiting_timeout_minutes: int = 30
    # §D (2026-07-26): CHECK1's mid-fill DEFERRAL bound, in SECONDS of wall clock.
    # When the broker refuses our orphan-leg cancel with "being processed", one of
    # OUR OWN legs is filling at this instant: the position vanished because our
    # exit filled, and our fill callback simply has not arrived yet. CHECK1 polls
    # positions and wins that race by ~0.9s, so it can release capital against a
    # verdict formed before the callback that owns the close exists. For this many
    # seconds it therefore finalizes NOTHING and lets the exit path own the close.
    # On expiry it finalizes anyway and says so — a bound that can expire must
    # never expire silently.
    #
    # SECONDS, NOT CYCLES, deliberately: a cycle count is a proxy for elapsed time
    # whose meaning changes silently the day poll_interval_sec is retuned.
    #
    # 0.0 = OFF = the pre-§D path, exactly — not "approximately". At 0.0 the
    # deferral code is not entered at all; tests/unit/test_check1_deferral.py
    # asserts that by booby-trapping the bookkeeping and the clock.
    check1_mid_fill_defer_sec: float = 0.0

    @field_validator("poll_interval_sec")
    @classmethod
    def _validate_poll_interval(cls, v: int) -> int:
        if v < 1:
            raise ValueError("poll_interval_sec must be >= 1")
        return v

    @field_validator("stuck_exiting_timeout_minutes")
    @classmethod
    def _validate_stuck_exiting_timeout(cls, v: int) -> int:
        if v < 1:
            raise ValueError("stuck_exiting_timeout_minutes must be >= 1")
        return v

    @field_validator("capital_drift_tolerance")
    @classmethod
    def _validate_drift_tolerance(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError("capital_drift_tolerance must be >= 0.0")
        return v

    @field_validator("human_order_margin_tolerance")
    @classmethod
    def _validate_human_order_tolerance(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError("human_order_margin_tolerance must be >= 0.0")
        return v

    @field_validator("capital_drift_alert_interval_sec")
    @classmethod
    def _validate_drift_alert_interval(cls, v: float) -> float:
        if v < 1.0:
            raise ValueError("capital_drift_alert_interval_sec must be >= 1.0")
        return v

    @field_validator("check1_mid_fill_defer_sec")
    @classmethod
    def _validate_check1_defer(cls, v: float) -> float:
        # 0.0 is the OFF value, so the floor is 0.0 and not 1.0. A negative bound
        # would make every deferral expire on the cycle it started — the deferral
        # silently disabled while the config still claimed it was on.
        if v < 0.0:
            raise ValueError("check1_mid_fill_defer_sec must be >= 0.0")
        return v


class AlertsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    failed_alerts_log_path: str   # TG12: path for ERROR-tier fallback log
    sentinel_dir: str             # TG12/CR2: directory for .flag sentinel files
    watcher_max_attempts: int     # AW11: max SMTP retry attempts before .failed
    alert_digest_threshold: int = 3  # FIX-095: send digest email when pending flags > this count
    watcher_lock_path: str        # AW11: lock file path for alert_watcher
    watcher_log_path: str         # AW11: alert_watcher own log file path
    watcher_interval_sec: int = 60         # P5: --loop sleep between passes (default-off; --once stays default)
    watcher_heartbeat_path: Optional[str] = None  # P5: liveness heartbeat file written each --loop pass
    # 16-Jul: monitoring-canary respawn-guard thresholds. Defaults == the canary's historical
    # literals, so behaviour is unchanged unless overridden. See scripts/monitoring_canary.py.
    respawn_restart_delta_threshold: int = 3        # min NRestarts jump between canary samples to flag
    respawn_rate_per_hour_threshold: float = 6.0    # restarts/hr above which that jump is a respawn loop
    email_fallback: EmailFallbackConfig = EmailFallbackConfig()  # FIX-132 Item 10
    telegram: TelegramConfig      # TG12: Telegram Bot API config
    smtp: SmtpConfig              # AW7: SMTP config for alert_watcher

    @field_validator("watcher_max_attempts")
    @classmethod
    def _validate_max_attempts(cls, v: int) -> int:
        if v < 1:
            raise ValueError("watcher_max_attempts must be >= 1")
        return v


class LoggingConfig(BaseModel):
    """FIX-099: Logging subsystem configuration."""
    model_config = ConfigDict(extra="forbid")
    min_free_disk_gb: float  # Minimum free disk space (GB) required before startup

    @field_validator("min_free_disk_gb")
    @classmethod
    def _validate_min_free_disk_gb(cls, v: float) -> float:
        if v < 0.1:
            raise ValueError("min_free_disk_gb must be >= 0.1")
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


class SRDetectorConfig(BaseModel):
    """
    SNR-DETECTOR-V1 — shadow support/resistance detector config.

    ONE default-off flag (`enabled`) gates the whole module in BOTH paper + live.
    Every other field is a confluence/zone tuning knob (config-tuned during the
    visual-review phase). When disabled (the default) the detector is not even
    constructed (main.py passes sr_detector=None) → zero pipeline change.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    timeframes: list[str] = Field(default_factory=lambda: ["day", "60minute", "30minute"])
    lookback_days: int = 180
    cache_ttl_sec: float = 1800.0
    # pivots (N bars each side; per-TF override via pivot_n_by_tf)
    default_pivot_n: int = 5
    pivot_n_by_tf: dict[str, int] = Field(default_factory=dict)
    # zones / merge
    cluster_pct: float = 0.5
    merge_pct: float = 0.4
    band_buffer_pct: float = 0.1
    # volume profile
    volume_bins: int = 24
    volume_node_frac: float = 0.7
    # breakout / flags
    breakout_avg_window: int = 20
    entry_proximity_pct: float = 1.0
    volume_surge_mult: float = 1.5
    weak_breakout_frac: float = 0.25
    retest_sl_buffer_pct: float = 0.3
    # confluence weights + thresholds
    w_swing: float = 1.0
    w_volume: float = 1.0
    w_multi_tf: float = 1.0
    w_prior_day: float = 1.0
    w_round: float = 0.5
    w_recency: float = 0.5
    t_high: float = 5.0
    t_med: float = 3.0
    touch_cap: int = 4
    recency_window_days: float = 90.0
    recency_min_factor: float = 0.0
    # worker / logging
    max_queue: int = 256
    max_zones_logged: int = 12

    # ── SNR-V2 Phase A: WAIT_FOR_RETEST entry side — SYMMETRIC LONG+SHORT (default-OFF) ──
    wait_for_retest_enabled: bool = False    # MASTER flag for Phase A divert+retest (both sides)
    near_zone_buffer_pct: float = 0.3        # entry "inside" the zone tolerance (resistance/support)
    require_confidence: str = "HIGH"         # only this zone confidence diverts (both sides)
    retest_timeout_sec: float = 1800.0       # give up the wait
    retest_max_away_pct: float = 1.0         # wrong-way reject past the band (LONG<band_low / SHORT>band_high)
    confirm_strong_close_frac: float = 0.6   # close must be in the strong N of the 1m range (upper=LONG/lower=SHORT)
    breakout_margin_pct: float = 0.0         # close must clear the band by this % (small)
    onem_lookback_days: int = 2              # SHORT 1m window (respect the minute cap)
    zone_cache_ttl_sec: float = 1800.0       # ZoneCache TTL
    zone_rewarm_margin_sec: float = 300.0    # re-warm a cached symbol this long before expiry
    retest_poll_interval_sec: float = 20.0   # RetestMonitor poll cadence
    sl_buffer_pct: float = 0.2               # structure SL = band_low − this%

    # ── V3 03.01: Layer-A intraday anchors (VWAP + ORB) — default-OFF ──
    # Master gate for the extra TODAY-only fine fetch used to compute VWAP + ORB.
    # OFF (default) → detector behaviour byte-identical (no extra broker call,
    # no anchor/swing evidence enrichment); the level-export tool turns it on.
    # Prior-day (PDH/PDL/PDC) + round anchors are always available (existing
    # fetches). Swings + confidence_class emission are gated by this same flag so
    # the live default row stays unchanged until validation.
    intraday_anchors_enabled: bool = False
    anchor_intraday_interval: str = "5minute"   # fine TF for VWAP/ORB (intraday only)
    anchor_lookback_days: int = 1               # today-only window for the fine fetch
    orb_window_minutes: int = 15                # opening-range window (scanner convention)

    @field_validator("anchor_intraday_interval")
    @classmethod
    def _validate_anchor_interval(cls, v: str) -> str:
        allowed = {"5minute", "15minute", "30minute"}
        if v not in allowed:
            raise ValueError(
                "sr_detector.anchor_intraday_interval must be intraday %s; got %r"
                % (sorted(allowed), v)
            )
        return v

    @field_validator("require_confidence")
    @classmethod
    def _validate_require_confidence(cls, v: str) -> str:
        if v not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("sr_detector.require_confidence must be HIGH, MEDIUM, or LOW")
        return v

    @field_validator("timeframes")
    @classmethod
    def _validate_timeframes(cls, v: list[str]) -> list[str]:
        allowed = {"day", "60minute", "30minute", "15minute", "5minute"}
        if not v:
            raise ValueError("sr_detector.timeframes must not be empty")
        bad = [tf for tf in v if tf not in allowed]
        if bad:
            raise ValueError(
                "sr_detector.timeframes has unsupported intervals %s; allowed %s"
                % (bad, sorted(allowed))
            )
        return v


class RegimeConfig(BaseModel):
    """
    V3 03.02 — index-level Market Regime engine config.

    ONE default-off flag (`enabled`) gates the whole shadow module in BOTH
    paper + live. Every other field is a tuning knob. The market index (NIFTY 50)
    is NOT in the instrument cache, so its token is supplied here and fetched
    through the existing rate-limited OHLC closure (reuse, not a new data path).
    Regime is a PREFERENCE — nothing here gates trading in this step.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    index_symbol: str = "NIFTY 50"
    index_token: int = 256265              # NIFTY 50 spot index (Kite instrument_token)
    daily_lookback_days: int = 400         # enough for EMA200 + swing structure
    intraday_interval: str = "5minute"
    intraday_lookback_days: int = 1
    ema_fast: int = 50
    ema_slow: int = 200
    slope_lookback: int = 5
    adx_period: int = 14
    adx_trend_threshold: float = 25.0
    atr_period: int = 14
    vol_high_ratio: float = 1.3            # cur ATR / baseline TR ≥ this → HIGH vol
    vol_low_ratio: float = 0.7            # ≤ this → LOW vol
    trend_day_range_atr_mult: float = 1.5  # intraday range ≥ this × ATR → trend-day candidate
    swing_pivot_n: int = 3
    compute_interval_sec: float = 60.0     # shadow-runner cadence

    @field_validator("intraday_interval")
    @classmethod
    def _validate_regime_interval(cls, v: str) -> str:
        allowed = {"5minute", "15minute", "30minute"}
        if v not in allowed:
            raise ValueError(
                "regime.intraday_interval must be intraday %s; got %r" % (sorted(allowed), v)
            )
        return v

    @model_validator(mode="after")
    def _validate_ema_order(self):
        if self.ema_fast >= self.ema_slow:
            raise ValueError("regime.ema_fast must be < regime.ema_slow")
        return self


class PortfolioAllocatorConfig(BaseModel):
    """
    V3 03.05 — Portfolio Allocator (ranked batch admission) config.

    ONE default-off flag (`allocator_mode`) gates the whole thing in BOTH paper +
    live. **off (default) = the current FCFS admission, BYTE-IDENTICAL** (the
    allocator object is not even constructed in main.py, so signal_processor gets
    allocator=None and the routing hook is never entered). shadow = a non-blocking
    observer computes the would-be ranked-admission set alongside live FCFS and logs
    the regret metrics (it NEVER reserves or places). enforce = a single admission
    worker ranks a candle-window batch and governs admission via the EXISTING
    reservation/gate primitives (the fused FCFS admit is bypassed only for in-scope
    candidates). Sits ON fund_manager.reserve / portfolio_lock /
    count_live_reservations / risk_engine.approve — reimplements none of them.

    Every knob below is INERT by default (deployment cap None → no-op; skew None →
    no-op; scope v3_only + no V3-playbook strategies yet → enforce governs nothing),
    so a flip to shadow/enforce changes as little as possible until each knob is
    deliberately set. See docs/v3/V3_STEP6_PORTFOLIO_ALLOCATOR_PLAN.md.
    """
    model_config = ConfigDict(extra="forbid")
    allocator_mode: str = "off"                 # off | shadow | enforce (★ master gate)
    enforce_scope: str = "v3_only"              # v3_only | all — v3_only governs ONLY V3-playbook strategies (none exist yet → inert)
    # A1: wall-clock-aligned window (NOT candle_store) — align to this interval, then
    # collect a short drain tail before ranking. Both far below the 60s signal expiry.
    candle_interval_seconds: float = 60.0       # window boundary alignment (wall-clock)
    drain_tail_seconds: float = 2.0             # collect this long after each boundary before ranking
    # A3: shared portfolio-wide concentration cap (deployed-margin %). None = INERT
    # (no-op). When set: admit iff deployed + candidate.margin <= pct * total.
    # COMPOSES with the per-sector 0.40 cap (does NOT touch risk_engine gate 8).
    max_portfolio_deployment_pct: Optional[float] = None
    # A6: long/short directional skew cap (max fraction of the batch admits in one
    # direction). None = INERT.
    long_short_skew_max: Optional[float] = None
    # T6: where the shadow regret rows are appended (JSONL; created on first write).
    regret_log_path: str = "data_store/allocator/regret.jsonl"

    @field_validator("allocator_mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        if v not in {"off", "shadow", "enforce"}:
            raise ValueError("portfolio_allocator.allocator_mode must be off|shadow|enforce")
        return v

    @field_validator("enforce_scope")
    @classmethod
    def _validate_scope(cls, v: str) -> str:
        if v not in {"v3_only", "all"}:
            raise ValueError("portfolio_allocator.enforce_scope must be v3_only|all")
        return v

    @field_validator("max_portfolio_deployment_pct")
    @classmethod
    def _validate_deployment_pct(cls, v: Optional[float]) -> Optional[float]:
        # Constrained (0, 1]; a value < the single-sector cap (0.40) would bind before
        # the sector cap — still SAFE (more conservative), just document-worthy.
        if v is not None and not (0 < v <= 1):
            raise ValueError("portfolio_allocator.max_portfolio_deployment_pct, when set, must be in (0, 1]")
        return v

    @field_validator("long_short_skew_max")
    @classmethod
    def _validate_skew(cls, v: Optional[float]) -> Optional[float]:
        # A skew cap below 0.5 is nonsensical (every batch is >=50% one side when odd);
        # require (0.5, 1].
        if v is not None and not (0.5 < v <= 1):
            raise ValueError("portfolio_allocator.long_short_skew_max, when set, must be in (0.5, 1]")
        return v

    @model_validator(mode="after")
    def _validate_window(self):
        if self.candle_interval_seconds <= 0:
            raise ValueError("portfolio_allocator.candle_interval_seconds must be > 0")
        if not (0 < self.drain_tail_seconds < self.candle_interval_seconds):
            raise ValueError(
                "portfolio_allocator.drain_tail_seconds must be in (0, candle_interval_seconds)"
            )
        return self


class V3ChainConfig(BaseModel):
    """
    V3 Step 10 — the V3 DECISION CHAIN (enrichment + generic gates + 3-layer score
    + would-be record) config. Governs the SHADOW enrichment that runs the V3 chain
    over live signals to measure it against real outcomes (the "Kalyan" question).

    ONE default-off flag (`v3_chain_mode`) gates the whole thing in BOTH paper +
    live. **off (default) = BYTE-IDENTICAL** — the V3ChainRunner is not even
    constructed in main.py, so signal_processor gets v3_chain=None and the hot-path
    hook is a single skipped flag check. shadow = a guarded fire-and-forget observer
    emits a copy of each screened+sized signal to a BACKGROUND worker which computes
    the V3 verdict (gates + score, LOG-ONLY, never rejects/delays/alters a live
    order) and appends a would-be record. BINARY for now (no `enforce` — that is a
    far-later step; keeping the flag binary avoids a dead enforce branch on the hot
    path). All gate thresholds + score weights are SEED numbers to be calibrated
    from the shadow data (spec §1 governing principle) — starting points, not truth.

    See docs/v3/V3_STEP10_PIPELINE_PLUMBING_PLAN.md + the V3 DECISION CONTENT
    SPECIFICATION v1.0.
    """
    model_config = ConfigDict(extra="forbid")
    v3_chain_mode: str = "off"                  # off | shadow (★ master gate; BINARY, no enforce yet)

    # ── gate knobs (spec §3) ──────────────────────────────────────────────────
    atr30_period: int = 14                      # ATR(30m) period for the G-RR SL buffer (distinct from regime.atr_period)
    rr_floor: float = 2.0                       # G-RR: R:R must be >= this (seed)
    sl_buffer_atr_mult: float = 0.20            # G-RR SL buffer = this × ATR30 (seed)
    htf_ema_period: int = 20                    # G-HTF + htf_alignment: 1h EMA period
    htf_swing_pivot_n: int = 3                  # G-HTF + htf_alignment: 1h swing-pivot N (each side)

    # ── 10b PLAYBOOK gate knobs (spec §3; PB-01 must-haves G-CONFIRM / G-PULLBACK). ──
    # All SEEDS — calibrate from the PB-01 would-be soak, never treat as tuned truth.
    confirm_min_body_frac: float = 0.50         # G-CONFIRM: body_frac=|close-open|/(high-low) >= this (rejects dojis)
    confirm_volume_mult: float = 1.20           # G-CONFIRM: 5m volume >= this × baseline_5m_volume
    baseline_candles_per_session: int = 75      # baseline_5m_volume = SMA20(daily volume) / this (75 five-min bars/session)
    pullback_proximity_pct: float = 0.005       # G-PULLBACK TOUCHED: within max(this×LEVEL, atr_mult×ATR30) of LEVEL
    pullback_proximity_atr_mult: float = 0.50
    hold_buffer_atr_mult: float = 0.20          # G-PULLBACK HELD: a 5m CLOSE below (LEVEL - this×ATR30) → INVALIDATED
    # ── 10b PLAYBOOK score-factor knobs (spec §4). ──
    retest_quality_atr_span: float = 0.75       # retest_quality = max(0, 1 - |dist|/this), dist=(pullback_low-LEVEL)/ATR30
    level_touches_cap: int = 5                  # level_significance = min(touches, this) / this

    # ── 3-layer score weights (spec §4; every step used exactly once, no double-count) ──
    # PLAYBOOK (40) — forward-compat, UNUSED in 10a (no playbook exists yet; recorded null).
    w_retest_quality: float = 15.0
    w_confirmation_strength: float = 15.0
    w_level_significance: float = 10.0
    # CONTEXT (40).
    w_regime_preference: float = 8.0
    w_sr_target_quality: float = 8.0
    w_htf_alignment: float = 8.0                # CONFLUENCE GROUP {ema-position, swing-structure}
    w_sector_strength: float = 8.0
    w_momentum_position: float = 8.0            # CONFLUENCE GROUP {rsi_range, vwap_position}
    # EXECUTION (20) — the raw step weights + the layer budget; the composer rescales
    # each by (exec_budget / Σ raw) so the four execution-ish steps sum to 20 (spec §4).
    exec_budget: float = 20.0
    w_exec_volume_surge: float = 15.0
    w_exec_atr: float = 10.0
    w_exec_time_of_day: float = 5.0
    w_exec_spread: float = 5.0

    # ── confluence combination (the anti-inflation rule, spec §4) ─────────────
    # Members of a group are combined into ONE bounded value BEFORE weighting so
    # correlated factors cannot each take a full weight. "mean" = mean of the
    # members' normalized fractions (default); "max" = the strongest member.
    confluence_combine: str = "mean"

    # ── sr_target_quality mapping (zone confidence → fraction, spec §4) ────────
    sr_target_quality_high: float = 1.0
    sr_target_quality_medium: float = 0.6
    sr_target_quality_low: float = 0.3

    # ── regime base-preference maps (spec §4 regime_preference) ───────────────
    # Each axis' base preference for PB-01 (intraday LONG breakout-retest); each is
    # multiplied by that axis' OWN confidence multiplier (from 03.02). regime OFF /
    # UNKNOWN → contributes 0 (never dominant; 8 of 40).
    regime_pref_direction: dict[str, float] = Field(
        default_factory=lambda: {"BULL": 1.0, "SIDEWAYS": 0.5, "BEAR": 0.0})
    regime_pref_day_type: dict[str, float] = Field(
        default_factory=lambda: {"TREND_DAY": 1.0, "UNDETERMINED": 0.5, "RANGE_DAY": 0.4})
    regime_pref_volatility: dict[str, float] = Field(
        default_factory=lambda: {"HIGH": 0.85, "NORMAL": 1.0, "LOW": 1.0})

    # ── 3-layer thresholds (forward-compat; 10a records the score, does not gate on it) ──
    # The distribution differs from the 8-step re-scale, so Step-4b's 50/56/75 do NOT
    # transfer — start at the classic shape and CALIBRATE from the would-be data.
    min_pass_score: int = 60
    medium_score_threshold: int = 65
    high_score_threshold: int = 80

    # ── worker / fetch knobs (the async enrichment worker) ────────────────────
    # The structural TFs the worker fetches (via the SAME rate-limited closure the
    # sr_detector uses) and truncates to signal time (NO-LOOKAHEAD addendum).
    structure_intervals: list[str] = Field(
        default_factory=lambda: ["day", "60minute", "30minute"])
    fetch_lookback_days: int = 180
    fetch_cache_ttl_sec: float = 1800.0         # own OhlcFetcher cache TTL (distinct from sr_detector.cache_ttl_sec)
    max_queue: int = 512
    would_be_log_path: str = "data_store/v3/would_be.jsonl"

    @field_validator("v3_chain_mode")
    @classmethod
    def _validate_v3_chain_mode(cls, v: str) -> str:
        # BINARY on purpose (no enforce in 10a) — a dead enforce branch on the hot
        # path is a foot-gun; enforce arrives in a later, deliberate step.
        if v not in {"off", "shadow"}:
            raise ValueError("v3_chain.v3_chain_mode must be off|shadow")
        return v

    @field_validator("confluence_combine")
    @classmethod
    def _validate_confluence(cls, v: str) -> str:
        if v not in {"mean", "max"}:
            raise ValueError("v3_chain.confluence_combine must be mean|max")
        return v

    @model_validator(mode="after")
    def _validate_v3_chain(self):
        if self.atr30_period <= 0:
            raise ValueError("v3_chain.atr30_period must be > 0")
        if self.rr_floor <= 0:
            raise ValueError("v3_chain.rr_floor must be > 0")
        if self.exec_budget <= 0:
            raise ValueError("v3_chain.exec_budget must be > 0")
        if not (self.high_score_threshold > self.medium_score_threshold > self.min_pass_score):
            raise ValueError(
                "v3_chain thresholds must satisfy high > medium > min_pass "
                f"(got {self.high_score_threshold}/{self.medium_score_threshold}/{self.min_pass_score})"
            )
        return self


class WatchlistConfig(BaseModel):
    """
    V3 Step 10b — the PB-01 OVERNIGHT WATCHLIST + next-morning entry stage config.

    ONE default-off master flag (`enabled`) gates BOTH the EOD capture and the
    next-morning entry stage in BOTH paper + live. **enabled=false (default) =
    BYTE-IDENTICAL** — the capture worker + entry-stage monitor are not constructed
    in main.py, and an EOD webhook (if one arrived) is captured to nothing. The
    watchlist is ANALYSIS ONLY — no position, no capital, no CNC/GTT overnight; PB-01
    is SHADOW (would-be records) and enabled:false (fail-closed) regardless.

    All windows/thresholds are SEEDS to calibrate from the PB-01 would-be soak (spec
    §7 open items O1/O3) — starting points, not truth.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False                        # ★ master gate — OFF = byte-identical

    # which EOD scanner is PB-01 (binds the eod route → the playbook). The strategy is
    # resolved via scan_webhook_map; this is the scanner-name the capture worker owns.
    playbook_scanner: str = "pb01_breakout_retest"

    # ── capture (EOD, day D) ──────────────────────────────────────────────────
    level_lookback_sessions: int = 20            # LEVEL = highest daily HIGH of the N sessions BEFORE the breakout day
    capture_fetch_lookback_days: int = 60        # how far back to fetch daily candles to compute LEVEL (calendar days)

    # ── next-morning entry stage (day D+1) ────────────────────────────────────
    entry_start: str = "09:20"                   # first 5-min candle must CLOSE before any confirmation (spec §7)
    entry_end: str = "11:00"                     # the next-morning retest thesis has expired by mid-morning (seed)
    entry_tf: str = "5minute"                    # the retest timeframe (built via candle_math.resample)
    gap_guard_pct: float = 0.03                  # SKIPPED_GAP if open > LEVEL × (1 + this) (seed 3%)
    poll_interval_sec: float = 20.0              # entry-stage candle poll cadence (off the hot path)

    # ── would-be record (T6 shadow; SEPARATE from the 10a would_be.jsonl) ─────
    would_be_log_path: str = "data_store/v3/pb01_would_be.jsonl"  # PB-01 shadow records (LOG-ONLY, NO order)

    @field_validator("entry_start", "entry_end")
    @classmethod
    def _validate_hhmm(cls, v: str) -> str:
        try:
            hh, mm = v.split(":")
            _t = _dt_time(int(hh), int(mm))
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"watchlist entry time must be HH:MM, got {v!r}") from exc
        return v

    @model_validator(mode="after")
    def _validate_watchlist(self):
        sh, sm = map(int, self.entry_start.split(":"))
        eh, em = map(int, self.entry_end.split(":"))
        if _dt_time(sh, sm) >= _dt_time(eh, em):
            raise ValueError(
                f"watchlist.entry_start ({self.entry_start}) must be < entry_end ({self.entry_end})")
        if self.level_lookback_sessions <= 0:
            raise ValueError("watchlist.level_lookback_sessions must be > 0")
        if not (0.0 < self.gap_guard_pct < 1.0):
            raise ValueError("watchlist.gap_guard_pct must be in (0, 1)")
        if self.poll_interval_sec <= 0:
            raise ValueError("watchlist.poll_interval_sec must be > 0")
        return self


class StructureExitConfig(BaseModel):
    """
    SNR-V2 Phase B — structure-aware exit (trail SL to structure + confirmed-break
    exit) for MIS / LIMIT_TRIPLE static-leg trades.

    ONE default-off master flag (`structure_exit_enabled`) gates the whole module
    in BOTH paper + live. When disabled (the default) StructureExitManager is not
    constructed and nothing subscribes to the candle feed → zero pipeline change.
    Single SL owner: do NOT co-enable any strategy.trailing_sl_enabled.
    """
    model_config = ConfigDict(extra="forbid")
    structure_exit_enabled: bool = False     # MASTER flag — OFF
    sl_buffer_pct: float = 0.2               # SL-to-structure buffer (match Phase A)
    break_buffer_pct: float = 0.0            # extra margin beyond the band for a break
    require_strong_close: bool = True        # decision (c) — strong close required
    break_strong_close_frac: float = 0.6     # adverse-frac threshold for the break close
    min_zone_confidence: str = "HIGH"        # decision (b) — HIGH only

    @field_validator("min_zone_confidence")
    @classmethod
    def _validate_min_zone_confidence(cls, v: str) -> str:
        if v not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("structure_exit.min_zone_confidence must be HIGH, MEDIUM, or LOW")
        return v

    @field_validator("break_strong_close_frac")
    @classmethod
    def _validate_break_frac(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("structure_exit.break_strong_close_frac must be in [0, 1]")
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
        if v > 25200.0:
            raise ValueError(
                f"ltp_gating_max_wait_sec must be <= 25200 (7h trading day cap), got {v!r}"
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

    FIX-026: volume_dependent_trails is a master kill-switch for any future
    volume/VWAP-based trail logic. Currently CandleData.volume is always 0
    (LF11), so this flag is preventive. When volume becomes reliable, set
    this to True to enable volume-based trail enhancements.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool              # master switch; if False, OrderPlacer skips register_trade
    trigger_pct: float         # fraction of entry price before first SL trail fires
    step_pct: float            # fraction of entry price per subsequent trail step
    volume_dependent_trails: bool  # FIX-026: enable volume/VWAP-based trail logic
    max_modify_failures: int = 3   # FIX-142: CRITICAL alert after this many consecutive modify failures

    @field_validator("trigger_pct", "step_pct")
    @classmethod
    def _fraction(cls, v: float) -> float:
        if not (0 < v < 1):
            raise ValueError(
                "must be a positive fraction < 1 "
                "(e.g. 0.005 for 0.5%, NOT 5 for 5%)"
            )
        return v


class SlippageTier(BaseModel):
    """One price band of the tiered entry-slippage guard. `price < max_price`
    selects this band (lower bound exclusive); `max_slippage_rs` is the max
    acceptable |LTP - trigger| (in Rs) before the entry is aborted."""
    model_config = ConfigDict(extra="forbid")
    max_price: float
    max_slippage_rs: float


class SlippageOverridesConfig(BaseModel):
    """Phase 3a — MANUAL override hierarchy for the sl_fraction entry-slippage
    tolerance. The effective fraction is resolved MOST-SPECIFIC-WINS:

        Symbol  >  Strategy  >  Price Band  >  Global (max_slippage_fraction)

    Every map is optional and starts EMPTY (→ the global fraction applies, so the
    system behaves exactly as before until Rama adds an override). Rama sets these
    from trading knowledge NOW (e.g. IDEA: 0.15, RELIANCE: 0.30); Phase 3b will
    later RECOMMEND values from accumulated slippage data. Only the `sl_fraction`
    mode consults these (flat_tiers/pct carry their own per-band Rs tolerances).

    `enabled: false` ignores all maps (fast global kill-switch for the hierarchy).
    Fractions are hard-rejected here unless in (0.0, 1.0]; values that merely look
    extreme (<0.05 or >0.50) are WARNED about at startup (validate_slippage_overrides),
    which also flags by_symbol/by_strategy keys that match no known instrument/strategy
    (typo catch)."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    by_price_band: dict[str, float] = Field(default_factory=dict)
    by_strategy: dict[str, float] = Field(default_factory=dict)
    by_symbol: dict[str, float] = Field(default_factory=dict)

    @field_validator("by_price_band", "by_strategy", "by_symbol")
    @classmethod
    def _fractions_in_range(cls, v: dict[str, float], info) -> dict[str, float]:
        for key, frac in v.items():
            if not isinstance(frac, (int, float)) or isinstance(frac, bool) \
                    or not (0.0 < float(frac) <= 1.0):
                raise ValueError(
                    f"slippage_control.overrides.{info.field_name}[{key!r}] = {frac!r}: "
                    f"slippage fraction must be a number in (0.0, 1.0]"
                )
        return v


class SlippageControlConfig(BaseModel):
    """Entry-slippage abort tolerance — calibratable without code changes. The
    pre-order guard aborts an entry when |LTP − signal_trigger| exceeds a
    `tolerance` computed per `mode`:
      sl_fraction (DEFAULT): tolerance = min(SL_distance × max_slippage_fraction,
                             absolute_cap_rs) — auto-scales with price AND the
                             strategy's SL%; directly caps how much of the risk
                             budget slippage may eat. The fraction may be
                             overridden per symbol/strategy/price-band (Phase 3a,
                             see `overrides`).
      flat_tiers: per-price-band Rs from `tiers`.
      pct:        signal_price × entry_gate.max_entry_slippage_pct.
    `hard_max_slippage_rs` is an absolute ceiling applied in EVERY mode. (SL is
    fixed/signal-based while TGT recalcs from the fill — FIX-013 — so entry
    slippage directly inflates risk; sl_fraction caps that fraction.)"""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    mode: str = "sl_fraction"               # sl_fraction | flat_tiers | pct
    max_slippage_fraction: float = 0.22     # sl_fraction: GLOBAL default; slippage ≤ this × SL distance
    absolute_cap_rs: float = 5.0            # sl_fraction: backstop (the smaller of the two wins)
    tiers: list[SlippageTier] = Field(default_factory=list)  # flat_tiers mode
    default_max_slippage_rs: float = 2.0    # flat_tiers: fallback when no band matches
    also_apply_pct_check: bool = True       # also apply the flat % (belt+suspenders), non-pct modes
    hard_max_slippage_rs: float = 10.0      # absolute ceiling, ALWAYS applied regardless of mode
    overrides: SlippageOverridesConfig = Field(  # Phase 3a: per symbol/strategy/band fraction
        default_factory=SlippageOverridesConfig
    )

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, v: str) -> str:
        if v not in ("sl_fraction", "flat_tiers", "pct"):
            raise ValueError(
                f"slippage_control.mode must be sl_fraction|flat_tiers|pct, got {v!r}"
            )
        return v


class EntryGateConfig(BaseModel):
    """
    FIX-025: EntryGate slippage protection config.
    FIX-134 Item 38: Liquidity check config (spread, depth).

    When EntryGate releases a signal with PRICE_HIT, the release_ltp is
    captured and passed to OrderPlacer. OrderPlacer applies slippage
    protection:
      LONG:  adjusted_limit = min(requested_entry + buffer, release_ltp)
      SHORT: adjusted_limit = max(requested_entry - buffer, release_ltp)

    FIX-128 (Fix A): max_entry_slippage_pct caps the allowed deviation
    between current market price and the original signal trigger price.
    If abs(ltp - trigger) / trigger * 100 > this value, order is aborted.
    """
    model_config = ConfigDict(extra="forbid")
    slippage_buffer: float  # Rs buffer for limit price adjustment
    max_entry_slippage_pct: float = 1.0  # FIX-128: % cap on trigger->LTP deviation
    max_spread_pct: float = 0.5         # FIX-134 Item 38: max bid-ask spread %
    min_depth_qty: int = 500            # FIX-134 Item 38: min depth qty at best price
    liquidity_check_enabled: bool = True  # FIX-134 Item 38: enable/disable
    min_effective_rr: float = 1.0       # FIX-136 Item 54: abort if R:R < this after slippage
    min_pending_rr: float = 0.0         # FIX-141: cancel pending entry if remaining R:R < this (0=disabled)
    circuit_proximity_reject_enabled: bool = True  # NOCIL fix: pre-fill reject of entries at/beyond the circuit-band exit ceiling (fast-disable lever)
    slippage_control: SlippageControlConfig = Field(  # entry-slippage abort (sl_fraction/flat_tiers/pct)
        default_factory=SlippageControlConfig
    )


class LiveFeedConfig(BaseModel):
    """FIX-134 Item 37: WebSocket reconnect hardening config."""
    model_config = ConfigDict(extra="forbid")
    max_reconnect_attempts: int = 10
    reconnect_backoff_base_seconds: int = 1
    reconnect_backoff_max_seconds: int = 30


class FnoBanConfig(BaseModel):
    """FIX-136 Item 44 + 22-Jun-2026 endpoint fix: F&O ban list fetch config.

    Source is the NSE Clearing daily securities-in-ban CSV (the old /api/ JSON
    endpoint was retired). fail_closed defaults OFF (FAIL-OPEN): a fetch failure
    must not block NSE-EQ trading, since the ban list currently has no runtime
    consumer. Flip to true only when F&O trading is enabled and a conservative
    block on fetch failure is wanted.
    """
    model_config = ConfigDict(extra="forbid")
    url: str = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
    fail_closed: bool = False


class StrategyCircuitBreakerConfig(BaseModel):
    """FIX-130 (Item 6): Intraday strategy circuit breaker settings."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    loss_multiplier: float = 2.0   # pause if loss > multiplier × avg_daily_loss
    cutoff_time: str = "12:00"     # don't pause after this IST time
    lookback_days: int = 10        # days of history to compute avg_daily_loss


class CircuitBreakerConfig(BaseModel):
    """
    FIX-128 (Fix C): Position-level circuit breaker settings.

    partial_fill_timeout_minutes: cancel a stuck PARTIAL-fill order after
        this many minutes from first partial. Supplement to the existing
        fill_timeout_sec (which only applies to OPEN → no-fill timeout).
    force_close_time: IST HH:MM at which all pending ENTRY orders are
        cancelled and all open positions are force-closed (circuit breaker
        distinct from the regular EOD squareoff at 15:17).
    max_api_failures: consecutive broker API errors before HARD_KILL.
    """
    model_config = ConfigDict(extra="forbid")
    partial_fill_timeout_minutes: int = 5
    force_close_time: str = "15:15"
    max_api_failures: int = 3


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


class BrokerConfig(BaseModel):
    """FIX-133 Item 28/29: multi-broker + multi-account support."""
    model_config = ConfigDict(extra="forbid")
    primary: str = "zerodha"
    fallback: str = "angelone"
    fallback_enabled: bool = False
    multi_account_mode: bool = False  # FIX-133 Item 29


class TgtRetryConfig(BaseModel):
    """Task (2026-06-19): standalone TGT retry. A LIMIT_TRIPLE trade whose SL is
    live but whose TGT could not be placed (FIX-190 Bug C) is re-attempted on an
    exponential backoff WITHOUT disturbing the standing SL. Optional section —
    the defaults reproduce the designed 30/60/120/240/480s schedule and give up
    after 5 attempts (the position stays SL-protected)."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    poll_interval_sec: int = 30        # how often the retry loop wakes
    max_attempts: int = 5              # give up after this many (position stays SL-protected)
    backoff_base_sec: int = 30         # nth retry waits base*2**(n-1): 30/60/120/240/480

    @field_validator("poll_interval_sec")
    @classmethod
    def _v_tgt_poll(cls, v: int) -> int:
        if v < 1:
            raise ValueError("tgt_retry.poll_interval_sec must be >= 1")
        return v

    @field_validator("max_attempts")
    @classmethod
    def _v_tgt_max(cls, v: int) -> int:
        if v < 1:
            raise ValueError("tgt_retry.max_attempts must be >= 1")
        return v

    @field_validator("backoff_base_sec")
    @classmethod
    def _v_tgt_base(cls, v: int) -> int:
        if v < 1:
            raise ValueError("tgt_retry.backoff_base_sec must be >= 1")
        return v


class EodReconcileConfig(BaseModel):
    """P1 (02-Jul): broker-authoritative EOD reconcile (scripts/eod_broker_reconcile.py).
    authoritative=false = SHADOW (P1 runs alongside eod_verify, writes its verdict + the
    shadow comparison, alerts at INFO, gates nothing). true = P1 is THE authoritative EOD
    verdict (ISSUES/UNVERIFIED at CRITICAL). Default false → ships dark; flip after shadow
    observation, then retire eod_verify. pnl_tolerance = ₹ band on broker-day-realized vs
    local realized before the P&L dimension is ISSUES."""
    model_config = ConfigDict(extra="forbid")
    authoritative: bool = False
    pnl_tolerance: float = 100.0


class EodCleanupConfig(BaseModel):
    """EOD hygiene prune (scripts/eod_cleanup.py step 4). `signal_retention_days` = keep-window
    (days) for terminal NOISE signal fingerprints (EXPIRED / DUPLICATE / REJECTED_*); older ones
    are DROPPED — children-first (FK-safe), batched — so the signals table + its dedup index do
    not grow unbounded (a live DB holds operational data, not a research archive). Trade-linked
    signals are NEVER pruned (capital-safety guard lives in the script). Tunable; lower it for a
    one-time backlog clear, then raise back. Default 90 (Rama, 15-Jul-2026)."""
    model_config = ConfigDict(extra="forbid")
    signal_retention_days: int = Field(default=90, ge=1)


class SystemConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    broker: BrokerConfig = BrokerConfig()  # FIX-133 Item 28
    trading_hours: TradingHoursConfig
    signal_queue: SignalQueueConfig
    special_sessions: dict[str, dict[str, str]] | None = None  # FIX-094: date -> {market_open, market_close, eod_squareoff_time}
    excluded_symbols: list[str] = []          # FIX-C: symbols rejected at webhook edge
    mis_filter: MisFilterConfig = Field(default_factory=MisFilterConfig)  # MIS learned blocklist (default OFF)
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
    logging: LoggingConfig                    # FIX-099: logging subsystem config
    order_reconciler: OrderReconcilerConfig   # RC17: reconciler tuning
    eod_reconcile: EodReconcileConfig = Field(default_factory=EodReconcileConfig)  # P1: broker-authoritative EOD reconcile (shadow default)
    eod_cleanup: EodCleanupConfig = Field(default_factory=EodCleanupConfig)  # 15-Jul: signal-fingerprint retention prune (children-first, FK-safe)
    tgt_retry: TgtRetryConfig = Field(default_factory=TgtRetryConfig)  # Task: standalone TGT retry
    shadow_tracker: ShadowTrackerConfig       # SH11: multi-inning tracking config
    sr_detector: SRDetectorConfig = Field(default_factory=SRDetectorConfig)  # SNR-DETECTOR-V1: shadow S&R detector (default-off)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)  # V3 03.02: index-level market regime (shadow, default-off)
    portfolio_allocator: PortfolioAllocatorConfig = Field(default_factory=PortfolioAllocatorConfig)  # V3 03.05: ranked batch admission (default-off)
    v3_chain: V3ChainConfig = Field(default_factory=V3ChainConfig)  # V3 Step 10: decision-chain shadow enrichment (default-off)
    watchlist: WatchlistConfig = Field(default_factory=WatchlistConfig)  # V3 Step 10b: PB-01 overnight watchlist + entry stage (default-off)
    structure_exit: StructureExitConfig = Field(default_factory=StructureExitConfig)  # SNR-V2 Phase B: structure-aware exit (default-off)
    smart_tgt: SmartTgtConfig                 # BL-7b: SmartTgtManager defaults
    entry_gate: EntryGateConfig               # FIX-025: gate release slippage protection
    paper: PaperConfig                        # H-20/ZA16a: paper fill synthesis
    drift_handler: DriftHandlerConfig         # BL-2: drift escalation policy
    circuit_breaker: CircuitBreakerConfig = CircuitBreakerConfig()  # FIX-128: defaults if absent
    strategy_circuit_breaker: StrategyCircuitBreakerConfig = StrategyCircuitBreakerConfig()  # FIX-130
    live_feed: LiveFeedConfig = LiveFeedConfig()  # FIX-134 Item 37
    fno_ban: FnoBanConfig = FnoBanConfig()    # FIX-136 Item 44
    scanner_check_delay_sec: float = 5.0      # FIX-D: delay before scanner checks (network stabilization)
    slippage_bands: list[str] = Field(        # price bands for trade_slippage_log.price_band (v31)
        default_factory=lambda: ["0-100", "100-200", "200-300", "300-500", "500-1000", "1000+"]
    )
    force_intraday_only: bool = True          # P0 2026-06-15: force every strategy to INTRADAY (MIS); blocks accidental CNC/DELIVERY orders
    # SLICE2.5-P1: master CNC/delivery capability lock. A REAL CNC order or its
    # OCO-GTT is placed ONLY when delivery_enabled=true AND force_intraday_only=false
    # AND trade_type in {DELIVERY,BOTH}. Default false — the GTT path is exercised in
    # Phase 1 only via the paper sim + the explicitly-flagged market-hours test (T2).
    # The lock is enforced at the broker boundary (zerodha_adapter refuses CNC/GTT
    # when false). Turning delivery on requires the full Slice 2.5 lifecycle.
    delivery_enabled: bool = False
    # Slice 2 (LAYER 1 — master product gate): which product type may trade today.
    # Read by the strategy-control resolver (strategies/control.strategy_will_trade)
    # at the entry gate + the status table. INTRADAY (default) = only intent==INTRADAY
    # strategies trade; DELIVERY = only delivery; BOTH = either. It only GATES (never
    # rewrites intent — force_intraday_only owns that) so the MIS-only guarantee is
    # untouched while the breaker is on.
    trade_type: str = "INTRADAY"

    @field_validator("trade_type")
    @classmethod
    def _validate_trade_type(cls, v: str) -> str:
        allowed = {"INTRADAY", "DELIVERY", "BOTH"}
        if v not in allowed:
            raise ValueError(
                "trade_type must be one of %s, got %r" % (sorted(allowed), v)
            )
        return v

    @model_validator(mode="after")
    def _cross_field_sanity_checks(self) -> "SystemConfig":
        """
        Cross-field sanity validation (FIX-147 → BUILD 2, 25-Jun-2026).

        The rules now live in the Config Sanity Auditor (core/config_auditor) — the
        SINGLE source shared with the 08:30 pre-flight Config Sanity check-group.
        This model_validator is the STARTUP caller: it runs the config-only group
        subset (A contradictions, C capital-relative, G cross-field), logs the
        WARN findings (alert-only), and FAILS FAST on any BLOCK — the BUILD 1 #10
        force_intraday_only+DELIVERY contradiction — by re-raising it as a
        ValueError that Pydantic wraps into a ValidationError. Fail fast rather than
        boot into a silent dead-state. (B single-source, D overrides, E launch-phase
        and F stale-default need the richer pre-flight context and run there.)
        """
        import logging
        from core.config_auditor import audit_system_config

        log = logging.getLogger("config_sanity")
        report = audit_system_config(self)

        # Fail-fast on contradictions (BLOCK). The joined message carries
        # "CONTRADICTORY CONFIG" (ConfigContradictionError subclasses ValueError, so
        # Pydantic wraps it into a ValidationError — preserving the #10 contract).
        report.raise_if_blocked()

        # Everything else is alert-only — log each WARN at boot (unchanged behaviour).
        for finding in report.warns:
            log.warning("config_sanity: %s", finding.message)

        return self


# ─────────────────────────────────────────────────────────────────────────────
# broker_costs.yaml — BrokerCostsConfig
# Locked: P12 (identical rates in live and paper mode)
# ─────────────────────────────────────────────────────────────────────────────

class FuturesRatesConfig(BaseModel):
    """FIX-027: Futures-specific rates."""
    model_config = ConfigDict(extra="forbid")
    stt_pct: float                   # STT % on both BUY and SELL for futures
    stamp_duty_buy_pct: float        # Stamp duty % on buy-side for futures


class OptionsBuyRatesConfig(BaseModel):
    """FIX-027: Options buy-side rates."""
    model_config = ConfigDict(extra="forbid")
    stt_pct: float                   # STT % on options BUY (should be 0)
    stamp_duty_pct: float            # Stamp duty % on options buy premium


class OptionsSellRatesConfig(BaseModel):
    """FIX-027: Options sell-side rates."""
    model_config = ConfigDict(extra="forbid")
    stt_pct: float                   # STT % on options SELL premium
    stamp_duty_pct: float            # Stamp duty % on sell side (should be 0)


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
    futures: FuturesRatesConfig      # FIX-027: Futures rates
    options_buy: OptionsBuyRatesConfig   # FIX-027: Options buy rates
    options_sell: OptionsSellRatesConfig  # FIX-027: Options sell rates


class BrokerCostsConfig(BaseModel):
    """
    FIX-131 Item 25: All rate fields must be explicitly defined in YAML.
    Pydantic raises ValidationError on first-use if any required field is missing.
    No hardcoded fallback values exist in CostCalculator (CC9).
    """
    model_config = ConfigDict(extra="forbid")
    zerodha: ZerodhaRatesConfig

    @model_validator(mode="after")
    def _validate_required_rates_non_negative(self) -> "BrokerCostsConfig":
        """FIX-131 Item 25: rates must be non-negative; key rates must be > 0."""
        z = self.zerodha
        non_negative = [
            ("brokerage_flat_intraday", z.brokerage_flat_intraday),
            ("brokerage_pct_intraday", z.brokerage_pct_intraday),
            ("stt_sell_pct", z.stt_sell_pct),
            ("stt_cnc_pct", z.stt_cnc_pct),
            ("exchange_txn_pct", z.exchange_txn_pct),
            ("gst_pct", z.gst_pct),
            ("sebi_pct", z.sebi_pct),
            ("stamp_duty_mis_buy_pct", z.stamp_duty_mis_buy_pct),
            ("stamp_duty_cnc_buy_pct", z.stamp_duty_cnc_buy_pct),
        ]
        for field_name, value in non_negative:
            if value < 0:
                raise ValueError(
                    f"broker_costs.yaml: zerodha.{field_name} must be >= 0, got {value}"
                )
        # Key rates that must be positive to be meaningful
        if z.brokerage_flat_intraday == 0 and z.brokerage_pct_intraday == 0:
            raise ValueError(
                "broker_costs.yaml: at least one of brokerage_flat_intraday or "
                "brokerage_pct_intraday must be > 0"
            )
        if z.gst_pct == 0:
            raise ValueError("broker_costs.yaml: gst_pct must be > 0 (GST is mandatory)")
        return self


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


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: ScoringStepsConfig
    min_pass_score: int
    # BUILD 1 (#4, 24-Jun): scoring-side `tier_multipliers` was DELETED. The
    # position sizer's tier weights come from system_config.position_sizing
    # .tier_multipliers (0.70/0.50); this scoring-side block (0.75/0.5) was never
    # read by the sizer — misleading dead config. Tier ASSIGNMENT still uses the
    # score thresholds below.
    high_score_threshold: int
    medium_score_threshold: int

    # ── V3 03.03/03.04 — Hard-Gate extraction + scorer re-scale (default-OFF) ──
    # OFF (default): the 10-step scorer + the thresholds above run unchanged
    # (BYTE-IDENTICAL live path). shadow: compute both, live follows OLD, log NEW.
    # enforce: the gate + 8-step + v3 thresholds decide. The v3_* keys are
    # SEPARATE from the OLD thresholds so OFF/reports keep reading 60/80/65.
    v3_hardgate_mode: str = "off"                       # off | shadow | enforce
    v3_gate_steps: list[str] = Field(default_factory=lambda: ["circuit_check", "signal_age"])
    v3_freshness_max_sec: float = 60.0                  # freshness gate cutoff (A4)
    v3_min_pass_score: int = 50                         # re-scaled (analytic; data-fit before enforce)
    v3_high_score_threshold: int = 75
    v3_medium_score_threshold: int = 56

    @field_validator("v3_hardgate_mode")
    @classmethod
    def _validate_v3_mode(cls, v: str) -> str:
        if v not in {"off", "shadow", "enforce"}:
            raise ValueError("scoring.v3_hardgate_mode must be off|shadow|enforce")
        return v

    @model_validator(mode="after")
    def _validate_threshold_ordering(self):
        # A9: high > medium > min_pass, for BOTH the live and the v3 thresholds.
        if not (self.high_score_threshold > self.medium_score_threshold > self.min_pass_score):
            raise ValueError(
                "scoring thresholds must satisfy high > medium > min_pass "
                f"(got {self.high_score_threshold}/{self.medium_score_threshold}/{self.min_pass_score})"
            )
        if not (self.v3_high_score_threshold > self.v3_medium_score_threshold > self.v3_min_pass_score):
            raise ValueError(
                "scoring v3 thresholds must satisfy high > medium > min_pass "
                f"(got {self.v3_high_score_threshold}/{self.v3_medium_score_threshold}/{self.v3_min_pass_score})"
            )
        return self


# ─────────────────────────────────────────────────────────────────────────────
# scan_webhook_map.yaml — ScanWebhookMapConfig
# Locked: P17 (no duplicate scanner names; every name maps to a valid strategy YAML)
# ─────────────────────────────────────────────────────────────────────────────

class ScannerEntry(BaseModel):
    """One scanner → strategy mapping entry (S14 format)."""
    model_config = ConfigDict(extra="forbid")
    strategy: str
    chartink_url: str
    # V3 Step 10b: the scanner's TYPE — a STRUCTURAL routing property (not a name-string
    # compare). DEFAULT "intraday" → the 15 live scanners are byte-identical. An "eod"
    # scanner (PB-01) is a DAILY/EOD alert: the receiver skips the intraday entry-window
    # gate and routes it ONLY to the EOD-capture queue (the watchlist) — it can NEVER
    # enter the intraday signal_queue / order path (constraint #1, fail-closed).
    scanner_type: str = "intraday"

    @field_validator("chartink_url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"chartink_url must be http(s): {v}")
        return v

    @field_validator("scanner_type")
    @classmethod
    def _validate_scanner_type(cls, v: str) -> str:
        if v not in {"intraday", "eod"}:
            raise ValueError(f"scanner_type must be intraday|eod, got {v!r}")
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
    ("nse_holidays",      f"nse_holidays_{_date.today().year}.yaml", NseHolidaysConfig),
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
