"""
strategies/schema.py  -  Pydantic schema for strategy YAML files (S3-S8)

Layer 2 (strategies/). Imports: pydantic, yaml, stdlib, core.exceptions.
No state_store, no broker, no data layer imports.

Locked decisions: S1-S8
"""

from __future__ import annotations

from datetime import time as dt_time
from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic import ValidationError

from core.exceptions import ConfigSchemaError

# ── Valid enum values ─────────────────────────────────────────────────────────

_VALID_DIRECTIONS = {"LONG", "SHORT"}
_VALID_INTENTS = {"INTRADAY", "DELIVERY"}
_VALID_PROTOCOLS = {"CO_PLUS_TGT", "LIMIT_TRIPLE"}
_VALID_ENTRY_METHODS = {"MARKET", "LIMIT"}
_VALID_SL_METHODS = {"FIXED_PCT", "ATR"}
_VALID_TGT_METHODS = {"FIXED_PCT", "RISK_REWARD", "ATR"}
_VALID_DAYS = {"MON", "TUE", "WED", "THU", "FRI"}


# ── StrategyConfig ─────────────────────────────────────────────────────────────

class StrategyConfig(BaseModel):
    """
    S3: Complete strategy configuration schema.
    S4: Validation rules enforced via field and model validators.
    S5: All fields declared here; YAMLs must include all of them.
    extra='forbid' per S4 and Project Rule 14.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Identity (required, no defaults) ---
    name: str
    display_name: str
    description: str

    # --- Direction & Product (required) ---
    direction: str
    intent: str
    order_protocol: str

    # --- Strategy ON/OFF switch (Slice 2, LAYER 3) ---
    # Per-strategy master switch read by the strategy-control resolver
    # (strategies/control.strategy_will_trade) at the entry gate + status table.
    # Default True (opt-in absent → enabled) so adding this field is a zero-behaviour
    # change for every existing YAML. INDEPENDENT of intent/trade_type: a disabled
    # strategy never trades regardless of product gating.
    enabled: bool = True

    # --- Entry ---
    entry_method: str
    entry_offset_pct: float = 0.0

    # --- Stop Loss ---
    sl_method: str
    sl_pct: float = 0.0          # > 0 required when sl_method=FIXED_PCT (model_validator)
    sl_atr_multiplier: float = 1.5
    sl_min_pct: float = 0.003
    sl_max_pct: float = 0.05
    sl_gap_buffer_pct: float = 0.0  # FIX-130: extra SL buffer during 09:15-09:30 gap window (0=disabled)

    # --- Target ---
    tgt_method: str
    tgt_pct: float = 0.0         # > 0 required when tgt_method=FIXED_PCT (model_validator)
    tgt_risk_reward: float = 2.0
    tgt_atr_multiplier: float = 2.5

    # --- Smart TGT (trail SL) ---
    smart_tgt_enabled: bool
    smart_tgt_trail_trigger_pct: float = 0.005
    smart_tgt_trail_step_pct: float = 0.003

    # --- Trailing SL (FIX-132 Item 8): milestone-based SL advancement ---
    # Applies to LIMIT_TRIPLE protocol; all thresholds in % of target distance.
    trailing_sl_enabled: bool = False
    trailing_sl_breakeven_trigger_pct: float = 60.0  # advance SL to breakeven
    trailing_sl_partial_lock_trigger_pct: float = 80.0  # advance SL to partial lock
    trailing_sl_partial_lock_sl_pct: float = 40.0  # SL moves to this % of target distance

    # --- Pullback Wait (P11a) ---
    pullback_wait_enabled: bool
    pullback_wait_tolerance_pct: float = 0.005
    pullback_wait_timeout_sec: int = 180

    # --- Screening Thresholds ---
    min_score: int = 0            # 0 = use global from scoring_weights.yaml
    min_volume_surge: float = 1.3
    min_adr_pct: float = 0.005
    max_spread_pct: float = 0.005

    # --- Risk ---
    # BUILD 1 (#11, 24-Jun): per-strategy `max_risk_pct` was DELETED. It was dead
    # in the live path — the sizer always uses the global position_sizing
    # .risk_per_trade_pct; only scripts/replay_signals.py ever read it (and it
    # now defaults to 0.01 there). Removed from schema + all strategy YAMLs.
    lot_size: int = 1
    max_concurrent_positions: int = 2  # FIX-135 Item 42: per-strategy position cap

    # --- Time ---
    entry_start_time: str = "09:20"  # Must match system_config.yaml entry_start
    entry_end_time: str = "15:00"    # Matches trading_hours.entry_end = 15:00 (T5 29-Jun)
    active_days: List[str] = ["MON", "TUE", "WED", "THU", "FRI"]

    # ── Field validators ─────────────────────────────────────────────────────

    @field_validator("direction")
    @classmethod
    def _val_direction(cls, v: str) -> str:
        if v not in _VALID_DIRECTIONS:
            raise ValueError(
                "direction must be LONG or SHORT, got %r" % v
            )
        return v

    @field_validator("intent")
    @classmethod
    def _val_intent(cls, v: str) -> str:
        if v not in _VALID_INTENTS:
            raise ValueError(
                "intent must be INTRADAY or DELIVERY, got %r" % v
            )
        return v

    @field_validator("order_protocol")
    @classmethod
    def _val_order_protocol(cls, v: str) -> str:
        if v not in _VALID_PROTOCOLS:
            raise ValueError(
                "order_protocol must be CO_PLUS_TGT or LIMIT_TRIPLE, got %r" % v
            )
        return v

    @field_validator("entry_method")
    @classmethod
    def _val_entry_method(cls, v: str) -> str:
        if v not in _VALID_ENTRY_METHODS:
            raise ValueError(
                "entry_method must be MARKET or LIMIT, got %r" % v
            )
        return v

    @field_validator("sl_method")
    @classmethod
    def _val_sl_method(cls, v: str) -> str:
        if v not in _VALID_SL_METHODS:
            raise ValueError(
                "sl_method must be FIXED_PCT or ATR, got %r" % v
            )
        return v

    @field_validator("tgt_method")
    @classmethod
    def _val_tgt_method(cls, v: str) -> str:
        if v not in _VALID_TGT_METHODS:
            raise ValueError(
                "tgt_method must be FIXED_PCT, RISK_REWARD, or ATR, got %r" % v
            )
        return v

    @field_validator("sl_min_pct", "sl_max_pct")
    @classmethod
    def _val_sl_bounds(cls, v: float) -> float:
        if v <= 0 or v >= 1:
            raise ValueError("sl_min_pct and sl_max_pct must be > 0 and < 1")
        return v

    @field_validator("sl_atr_multiplier", "tgt_atr_multiplier")
    @classmethod
    def _val_atr_multipliers(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("ATR multipliers must be > 0")
        return v

    @field_validator("tgt_risk_reward")
    @classmethod
    def _val_tgt_risk_reward(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("tgt_risk_reward must be > 0")
        return v

    @field_validator("smart_tgt_trail_trigger_pct", "smart_tgt_trail_step_pct",
                     "pullback_wait_tolerance_pct")
    @classmethod
    def _val_trail_pcts(cls, v: float) -> float:
        if v <= 0 or v >= 1:
            raise ValueError("Trail/pullback percentage fields must be > 0 and < 1")
        return v

    @field_validator("entry_offset_pct")
    @classmethod
    def _val_entry_offset(cls, v: float) -> float:
        if v < 0 or v >= 1:
            raise ValueError("entry_offset_pct must be >= 0 and < 1")
        return v

    @field_validator("min_adr_pct", "max_spread_pct")
    @classmethod
    def _val_threshold_pcts(cls, v: float) -> float:
        if v < 0:
            raise ValueError("min_adr_pct and max_spread_pct must be >= 0")
        return v

    @field_validator("min_score")
    @classmethod
    def _val_min_score(cls, v: int) -> int:
        if v < 0:
            raise ValueError("min_score must be >= 0 (0 = use global)")
        return v

    @field_validator("min_volume_surge")
    @classmethod
    def _val_min_volume_surge(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("min_volume_surge must be > 0")
        return v

    @field_validator("pullback_wait_timeout_sec")
    @classmethod
    def _val_pullback_timeout(cls, v: int) -> int:
        if v < 1:
            raise ValueError("pullback_wait_timeout_sec must be >= 1")
        return v

    @field_validator("lot_size")
    @classmethod
    def _val_lot_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError("lot_size must be >= 1")
        return v

    @field_validator("active_days")
    @classmethod
    def _val_active_days(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("active_days must not be empty")
        invalid = set(v) - _VALID_DAYS
        if invalid:
            raise ValueError(
                "active_days contains invalid values: %s" % sorted(invalid)
            )
        return v

    # ── Model-level cross-field validator ─────────────────────────────────────

    @model_validator(mode="after")
    def _val_cross_fields(self) -> "StrategyConfig":
        # SL method constraints (S4)
        if self.sl_method == "FIXED_PCT":
            if self.sl_pct <= 0:
                raise ValueError(
                    "sl_pct must be > 0 when sl_method=FIXED_PCT"
                )
            if not (self.sl_min_pct <= self.sl_pct <= self.sl_max_pct):
                raise ValueError(
                    "sl_pct (%.4f) must be between sl_min_pct (%.4f) and sl_max_pct (%.4f)"
                    % (self.sl_pct, self.sl_min_pct, self.sl_max_pct)
                )

        # Sanity: sl_min <= sl_max always
        if self.sl_min_pct > self.sl_max_pct:
            raise ValueError(
                "sl_min_pct (%.4f) must be <= sl_max_pct (%.4f)"
                % (self.sl_min_pct, self.sl_max_pct)
            )

        # TGT method constraints (S4)
        if self.tgt_method == "FIXED_PCT":
            if self.tgt_pct <= 0:
                raise ValueError(
                    "tgt_pct must be > 0 when tgt_method=FIXED_PCT"
                )

        # Entry time window (S4)
        try:
            sh, sm = map(int, self.entry_start_time.split(":"))
            eh, em = map(int, self.entry_end_time.split(":"))
            start = dt_time(sh, sm)
            end = dt_time(eh, em)
        except (ValueError, AttributeError) as exc:
            raise ValueError(
                "Invalid entry_start_time or entry_end_time format; expected HH:MM"
            ) from exc

        if start >= end:
            raise ValueError(
                "entry_start_time (%s) must be < entry_end_time (%s)"
                % (self.entry_start_time, self.entry_end_time)
            )

        return self


# ── validate_strategy ──────────────────────────────────────────────────────────

def validate_strategy(yaml_path: Path) -> StrategyConfig:
    """S8: Load and validate a single strategy YAML. Raises ConfigSchemaError."""
    try:
        with open(yaml_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except FileNotFoundError:
        raise ConfigSchemaError(
            "Strategy YAML not found: %s" % yaml_path
        )
    except yaml.YAMLError as exc:
        raise ConfigSchemaError(
            "Invalid YAML syntax in %s: %s" % (yaml_path, exc)
        )

    if not isinstance(raw, dict):
        raise ConfigSchemaError(
            "Strategy YAML must be a mapping, got %s in %s"
            % (type(raw).__name__, yaml_path)
        )

    try:
        return StrategyConfig(**raw)
    except ValidationError as exc:
        raise ConfigSchemaError(
            "Strategy YAML validation failed for %s: %s" % (yaml_path, exc)
        ) from exc
