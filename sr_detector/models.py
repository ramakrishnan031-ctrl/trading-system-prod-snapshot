"""
sr_detector/models.py — Trading System v2 · S&R Detector V1 (SNR-DETECTOR-V1)

Purpose:
    Immutable value objects shared across the S&R detector package. Pure data —
    no behaviour, no I/O, stdlib only.

What This Module Does NOT Do:
    - No fetching, no DB, no config reading.
    - No imports outside stdlib (keeps sr_detector/ a pure package).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Market data
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Candle:
    """One OHLCV bar. ts is timezone-aware (or naive IST) datetime."""
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    @staticmethod
    def from_kite(row: dict) -> "Candle":
        """Build from a kite.historical_data() dict row."""
        return Candle(
            ts=row["date"],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row.get("volume") or 0),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Structure
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Pivot:
    """A swing pivot — a local extreme over N bars each side."""
    price: float
    ts: datetime
    kind: str            # "HIGH" | "LOW"
    bar_index: int


@dataclass(frozen=True)
class Zone:
    """
    A price BAND (never a single line) clustered from same-kind pivots on ONE
    timeframe. RESISTANCE is built from swing highs, SUPPORT from swing lows.
    """
    band_low: float
    band_high: float
    kind: str            # "RESISTANCE" | "SUPPORT"
    touches: int
    timeframe: str
    last_touch_ts: Optional[datetime] = None

    @property
    def center(self) -> float:
        return (self.band_low + self.band_high) / 2.0

    @property
    def width(self) -> float:
        return self.band_high - self.band_low

    def contains(self, price: float, buffer_pct: float = 0.0) -> bool:
        """True if price falls inside the band, optionally widened by buffer_pct."""
        pad = self.center * (buffer_pct / 100.0)
        return (self.band_low - pad) <= price <= (self.band_high + pad)


@dataclass(frozen=True)
class ScoredZone:
    """
    A confluence-scored zone, possibly MERGED across timeframes. Carries the
    evidence breakdown that lets us audit + calibrate (spec E).
    """
    band_low: float
    band_high: float
    kind: str                              # "RESISTANCE" | "SUPPORT"
    score: float
    confidence: str                        # "HIGH" | "MEDIUM" | "LOW"
    touches: int
    timeframes: Tuple[str, ...]            # which TFs contributed
    # evidence: ordered (method_name, contribution) pairs — JSON-serialisable.
    evidence: Tuple[Tuple[str, float], ...] = ()
    last_touch_ts: Optional[datetime] = None

    @property
    def center(self) -> float:
        return (self.band_low + self.band_high) / 2.0

    def contains(self, price: float, buffer_pct: float = 0.0) -> bool:
        pad = self.center * (buffer_pct / 100.0)
        return (self.band_low - pad) <= price <= (self.band_high + pad)

    def to_dict(self) -> dict:
        return {
            "band_low": round(self.band_low, 4),
            "band_high": round(self.band_high, 4),
            "kind": self.kind,
            "score": round(self.score, 4),
            "confidence": self.confidence,
            "touches": self.touches,
            "timeframes": list(self.timeframes),
            "evidence": [[m, round(c, 4)] for m, c in self.evidence],
        }


# ─────────────────────────────────────────────────────────────────────────────
# The placed candidate the observer sees (post-placement, non-gating)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Candidate:
    """
    A snapshot of a placed entry handed to the detector. Built post-placement in
    signal_processor; everything here is already in hand at _emit_signal_alert.
    """
    signal_id: str
    symbol: str
    strategy: str
    direction: str                 # "LONG" | "SHORT" | "BUY" | "SELL"
    intended_entry: float
    sl_price: float
    tgt_price: float
    qty: int
    intent: str                    # "INTRADAY" | "DELIVERY"
    mode: str                      # "paper" | "live"
    ts: datetime
    score: Optional[int] = None    # None on the gate-release path

    @property
    def is_long(self) -> bool:
        return self.direction.strip().upper() in {"LONG", "BUY"}


# ─────────────────────────────────────────────────────────────────────────────
# Retest proposal + the analysis result (the row to persist)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RetestProposal:
    """V1 logs the PROPOSAL only — the hypothetical outcome is a later EOD step."""
    would_wait: bool = False
    entry: Optional[float] = None
    sl: Optional[float] = None


# Structure-status sentinels (persisted in the flags layer / for fetch failures).
STRUCT_OK = "OK"
STRUCT_NONE = "NO_CLEAR_STRUCTURE"
STRUCT_FETCH_FAILED = "FETCH_FAILED"

# V3 03.01 — confidence_class: which detection layer the analysis may be trusted
# on. Defaults to ANCHOR_ONLY: anchors (PDH/PDL/PDC, round, VWAP, ORB) are
# reference levels; structural SWINGS are computed + emitted but flagged
# NOT-YET-VALIDATED until Rama's manual-marking validation passes (the later,
# human-gated flip to ANCHOR_PLUS_VALIDATED_SWINGS). The 03.03 R:R gate will use
# ANCHOR-only levels for live capital until then.
CONF_ANCHOR_ONLY = "ANCHOR_ONLY"
CONF_ANCHOR_PLUS_VALIDATED_SWINGS = "ANCHOR_PLUS_VALIDATED_SWINGS"

# V3 03.01 — S&R Timeframe Policy (intraday): map a fetched interval to its
# structural ROLE so swing levels are labelled primary/major for validation.
TF_ROLE = {
    "day": "DAILY",
    "60minute": "MAJOR",     # 1-hour = MAJOR structural
    "30minute": "PRIMARY",   # 30-minute = PRIMARY structural
    "15minute": "MINOR",
    "5minute": "MICRO",
}


@dataclass(frozen=True)
class SRAnalysis:
    """The detector's verdict for one candidate — everything that gets logged."""
    structure_status: str                          # STRUCT_OK | STRUCT_NONE | STRUCT_FETCH_FAILED
    nearest_resistance: Optional[ScoredZone] = None
    nearest_support: Optional[ScoredZone] = None
    dist_to_resistance_pct: Optional[float] = None
    dist_to_support_pct: Optional[float] = None
    breakout_volume: Optional[float] = None
    flags: Tuple[str, ...] = ()
    retest: RetestProposal = field(default_factory=RetestProposal)
    evidence: dict = field(default_factory=dict)   # full per-zone breakdown (JSON)
    note: Optional[str] = None                     # e.g. "fetch_failed: <reason>"
    # V3 03.01 (additive; default-safe so every existing construction is unchanged):
    anchors: dict = field(default_factory=dict)    # Layer-A reference levels: prior_day
    #   (PDH/PDL/PDC), round_numbers, vwap, orb_high/orb_low/orb_window_minutes.
    swings: dict = field(default_factory=dict)     # Layer-B structural swing levels by
    #   role: {"PRIMARY":[...], "MAJOR":[...], ...} — computed but NOT-YET-VALIDATED.
    confidence_class: str = CONF_ANCHOR_ONLY       # ANCHOR_ONLY (default) | ANCHOR_PLUS_VALIDATED_SWINGS
