"""
v3_chain/models.py — Trading System v2 · V3 Step 10a data types.

Two dataclasses:
  • V3Signal    — the LIGHT snapshot emitted on the hot path (fire-and-forget). It
                  captures EVERYTHING the async worker needs so the worker re-reads
                  NOTHING live except the truncated historical fetch (NO-LOOKAHEAD).
                  In particular `as_of` (the live decision instant), the screener's
                  step_results + market_data snapshot, and the regime snapshot AS OF
                  signal time are all captured here, at `now`, on the hot path.
  • WouldBeRecord — the one JSONL row per enriched signal (the promotion evidence +
                  the G-KALYAN join to real outcomes).

Pure data — no behaviour, no I/O. stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class V3Signal:
    """The hot-path snapshot of a screened+sized signal, handed to the V3 chain
    observer. Copied (not referenced) so a later mutation cannot change the verdict."""
    signal_id: str
    symbol: str
    scanner_name: str
    strategy_name: str
    side: str                       # "BUY" | "SELL"
    intent: str
    # LIVE placement basis (for the V3-vs-LIVE comparison — a finding in itself):
    entry_price: float
    live_sl_price: float            # the FIXED_PCT-derived SL the live path uses
    live_tgt_price: Optional[float] # the live target (derived with the same fn the admit path uses)
    trigger_price: float
    # the LIVE screener outputs (reused — NOT recomputed):
    score: float                    # live screen score
    tier: str
    step_results: Dict[str, float]  # raw 0-1 step scores (copy)
    market_data: Dict[str, Any]     # screen-time market_data snapshot (copy)
    sector: str
    # as-of / no-lookahead:
    as_of: datetime                 # the live decision instant (= now at emit time)
    triggered_at: Any               # the signal's Chartink trigger ts (naive IST)
    # regime AS OF signal time (captured on the hot path at emit; None when regime OFF):
    regime_state: Any = None        # regime.models.RegimeState | None
    v3_playbook: bool = False       # is this a V3-playbook strategy (10b)? (10a: all False)


@dataclass
class WouldBeRecord:
    """One would-be-trade row (JSONL). The V3 verdict is LOG-ONLY — no order is placed.
    `live_outcome_link` (= signal_id) joins to the ACTUAL trade outcome for G-KALYAN."""
    # identity
    signal_id: str
    symbol: str
    strategy: str
    scanner: str
    side: str
    intent: str
    # as-of / no-lookahead audit trail (V1 of the addendum's verification)
    signal_ts: str                  # iso — the live decision instant
    enrichment_ts: str              # iso — when the worker computed the verdict
    last_candle_close_ts: Dict[str, Optional[str]]  # per-tf latest bar-close used (all <= signal_ts)
    # regime (as of signal time)
    regime: Optional[dict]
    regime_asof_unavailable: bool
    # S&R
    nearest_support: Optional[dict]
    nearest_resistance: Optional[dict]
    sr_confidence_class: Optional[str]
    sr_sync_hit: bool               # would a warm cache have hit at signal time?
    # V3 vs LIVE placement (the comparison is itself a finding)
    v3_sl: Optional[float]
    v3_tgt: Optional[float]
    v3_rr: Optional[float]
    live_entry: float
    live_sl: float
    live_tgt: Optional[float]
    live_rr: Optional[float]
    # gates: {gate_name: {"passed": bool, "reason": str|None}}
    gates: Dict[str, dict]
    # score: {"playbook": None, "context": {...}, "execution": {...}, "partial_total": float}
    score: dict
    live_score: float
    live_tier: str
    # verdict
    v3_verdict: str                 # WOULD_PASS_GATES | WOULD_REJECT_<gate>
    live_outcome_link: str          # = signal_id (join key to trades/outcome)
    notes: Optional[str] = None

    def to_json_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "scanner": self.scanner,
            "side": self.side,
            "intent": self.intent,
            "signal_ts": self.signal_ts,
            "enrichment_ts": self.enrichment_ts,
            "last_candle_close_ts": self.last_candle_close_ts,
            "regime": self.regime,
            "regime_asof_unavailable": self.regime_asof_unavailable,
            "nearest_support": self.nearest_support,
            "nearest_resistance": self.nearest_resistance,
            "sr_confidence_class": self.sr_confidence_class,
            "sr_sync_hit": self.sr_sync_hit,
            "v3_sl": _rnd(self.v3_sl),
            "v3_tgt": _rnd(self.v3_tgt),
            "v3_rr": _rnd(self.v3_rr),
            "live_entry": _rnd(self.live_entry),
            "live_sl": _rnd(self.live_sl),
            "live_tgt": _rnd(self.live_tgt),
            "live_rr": _rnd(self.live_rr),
            "gates": self.gates,
            "score": self.score,
            "live_score": self.live_score,
            "live_tier": self.live_tier,
            "v3_verdict": self.v3_verdict,
            "live_outcome_link": self.live_outcome_link,
            "notes": self.notes,
        }


def _rnd(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), 4)
