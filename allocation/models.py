"""
allocation/models.py — V3 03.05 Portfolio Allocator data types.

Light, dependency-free dataclasses shared by the ranker, window buffer, the shadow
regret observer, and the enforce admission worker. No trading logic here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AdmitPayload:
    """
    The HEAVY admit inputs — carried ONLY on an enforce-scope candidate so the
    admission worker can call signal_processor's shared admit+place callable
    (`_admit_and_place`) with exactly the values the fused FCFS path would have used.

    Opaque to the ranker and the shadow regret path (they read only the light
    `ScoredCandidate` fields). None on a shadow-observed candidate (shadow never
    admits, so it needs no payload).
    """
    scanner_name: str
    strategy_obj: Any
    sizing: Any                 # SizingResult from position_sizer
    screen_result: Any          # ScreenResult (carries score/tier)
    entry_price: float
    sl_price: float
    trigger_price: float
    triggered_at: Any           # naive IST datetime
    retry_count: int
    now: Any                    # now_ist() captured at PREPARE time


@dataclass
class ScoredCandidate:
    """
    One screened+sized candidate competing for admission in a window.

    The light fields drive ranking (A7) and the regret metrics (A5). `triggered_epoch`
    is a float captured at build time so the ranker's tiebreak is clock-free and
    fully replayable (no now() read inside the sort).
    """
    signal_id: str
    symbol: str
    strategy_name: str
    side: str                   # "BUY" | "SELL"
    intent: str
    score: float
    tier: str                   # "HIGH" | "MEDIUM" | "LOW" | ...
    margin_required: float
    sector: str
    triggered_epoch: float
    payload: Optional[AdmitPayload] = None   # set for enforce; None for shadow-light


# Tier → ordinal rank for the deterministic tiebreak (higher = better).
_TIER_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def tier_rank(tier: str) -> int:
    """Ordinal rank for a tier label; unknown tiers sort last (0)."""
    return _TIER_RANK.get(str(tier).upper(), 0)


@dataclass
class RegretRecord:
    """
    One window's shadow regret summary (A5) — persisted as a JSONL row.

    All three metrics are DIRECTIONAL (the counterfactual simulates both ranked and
    arrival-order admission against the same start-of-window snapshot; it does not
    replay the live FCFS interleaving — see the plan's P6 caveat).
    """
    window_key: int                 # the wall-clock boundary this window aligns to (epoch // interval)
    ts_iso: str
    n_candidates: int
    n_ranked_admit: int
    n_fcfs_admit: int
    crowd_out: int                  # FCFS admitted, ranked would REJECT (low-score crowding out higher)
    starvation: int                 # ranked would admit, FCFS REJECTED (high-score starved by arrival order)
    score_weighted_regret: float    # Σscore(ranked-admit) − Σscore(fcfs-admit); >0 ⇒ ranked captures more quality
    free_slots: int
    avail_capital: float
    ranked_admit_symbols: list = field(default_factory=list)
    fcfs_admit_symbols: list = field(default_factory=list)

    def to_json_dict(self) -> dict:
        return {
            "window_key": self.window_key,
            "ts": self.ts_iso,
            "n_candidates": self.n_candidates,
            "n_ranked_admit": self.n_ranked_admit,
            "n_fcfs_admit": self.n_fcfs_admit,
            "crowd_out": self.crowd_out,
            "starvation": self.starvation,
            "score_weighted_regret": round(self.score_weighted_regret, 4),
            "free_slots": self.free_slots,
            "avail_capital": round(self.avail_capital, 2),
            "ranked_admit": self.ranked_admit_symbols,
            "fcfs_admit": self.fcfs_admit_symbols,
        }
