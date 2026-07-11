"""
regime/models.py — Trading System v2 · V3 03.02 Market Regime Engine

Immutable value objects for the index-level market regime. Pure data — no I/O,
no behaviour beyond trivial helpers. The engine emits a THREE-AXIS state
(direction / volatility / day-type), each with an ordinal confidence mapped to a
preference multiplier, plus a positive-confirmation-only extreme flag.

SHADOW: nothing here gates trading. The Context score (03.04) and the Hard Gate
(03.03) consume these outputs in LATER steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── status ──
STATUS_OK = "OK"
STATUS_UNKNOWN = "UNKNOWN"          # fail-safe: missing/insufficient data → neutral

# ── axis 1: direction ──
DIR_BULL = "BULL"
DIR_BEAR = "BEAR"
DIR_SIDEWAYS = "SIDEWAYS"           # most-uncertain direction

# ── axis 2: volatility ──
VOL_HIGH = "HIGH"
VOL_NORMAL = "NORMAL"               # most-uncertain volatility
VOL_LOW = "LOW"

# ── axis 3: day-type ──
DAY_TREND = "TREND_DAY"
DAY_RANGE = "RANGE_DAY"
DAY_UNDETERMINED = "UNDETERMINED"   # most-uncertain day-type (early session)

# ── ordinal confidence → preference multiplier (Task 4) ──
# Ordinal FIRST — a calibrated % is a later, data-driven step (do NOT fake precision).
CONF_HIGH = "HIGH"
CONF_MEDIUM = "MEDIUM"
CONF_LOW = "LOW"
CONF_TRANSITION = "TRANSITION"
CONFIDENCE_MULTIPLIER = {
    CONF_HIGH: 1.0,
    CONF_MEDIUM: 0.5,
    CONF_LOW: 0.2,
    CONF_TRANSITION: 0.0,
}


@dataclass(frozen=True)
class AxisResult:
    """One regime axis: its value, ordinal confidence, the derived preference
    multiplier, and the evidence behind it (for audit/calibration)."""
    value: str
    confidence: str
    multiplier: float
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "confidence": self.confidence,
            "multiplier": round(self.multiplier, 4),
            "evidence": self.evidence,
        }


def axis(value: str, confidence: str, evidence: Optional[dict] = None) -> AxisResult:
    """Build an AxisResult, mapping ordinal confidence → preference multiplier."""
    return AxisResult(
        value=value,
        confidence=confidence,
        multiplier=CONFIDENCE_MULTIPLIER.get(confidence, 0.0),
        evidence=evidence or {},
    )


def neutral_axis(value: str) -> AxisResult:
    """The most-uncertain axis value at TRANSITION confidence (multiplier 0) —
    used when an axis cannot be computed. Never crashes the book: a neutral
    preference means the system trades normally (regime is a preference)."""
    return axis(value, CONF_TRANSITION)


@dataclass(frozen=True)
class RegimeState:
    """The full regime verdict for one cycle. `preference_multiplier` is the
    HEADLINE (the direction axis) the Context score reads; each axis also carries
    its own multiplier for finer use later. `extreme_flag` is emitted here but
    NOT wired to stop trading in this step (03.03 consumes it later)."""
    status: str                        # STATUS_OK | STATUS_UNKNOWN
    direction: AxisResult
    volatility: AxisResult
    day_type: AxisResult
    extreme_flag: bool
    preference_multiplier: float       # headline = direction.multiplier
    ts: Optional[str] = None           # ISO timestamp of the computation
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "ts": self.ts,
            "direction": self.direction.to_dict(),
            "volatility": self.volatility.to_dict(),
            "day_type": self.day_type.to_dict(),
            "extreme_flag": self.extreme_flag,
            "preference_multiplier": round(self.preference_multiplier, 4),
            "note": self.note,
        }


def unknown_state(ts: Optional[str] = None, note: Optional[str] = None) -> RegimeState:
    """Fail-safe state: status UNKNOWN, every axis neutral (multiplier 0),
    extreme_flag FALSE. The system would trade normally — this is NOT a halt."""
    return RegimeState(
        status=STATUS_UNKNOWN,
        direction=neutral_axis(DIR_SIDEWAYS),
        volatility=neutral_axis(VOL_NORMAL),
        day_type=neutral_axis(DAY_UNDETERMINED),
        extreme_flag=False,
        preference_multiplier=0.0,
        ts=ts,
        note=note,
    )
