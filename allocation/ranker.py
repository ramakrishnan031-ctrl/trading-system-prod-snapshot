"""
allocation/ranker.py — V3 03.05 deterministic candidate ranking (A7).

Sort key (fully deterministic / replayable, no clock or random reads):
    score DESC, tier_rank DESC, triggered_epoch ASC, signal_id ASC
"""
from __future__ import annotations

from typing import List

from allocation.models import ScoredCandidate, tier_rank


def rank_candidates(candidates: List[ScoredCandidate]) -> List[ScoredCandidate]:
    """Return a new list ranked best-first per A7. Input is not mutated."""
    return sorted(
        candidates,
        key=lambda c: (-float(c.score), -tier_rank(c.tier), float(c.triggered_epoch), str(c.signal_id)),
    )


def arrival_ordered(candidates: List[ScoredCandidate]) -> List[ScoredCandidate]:
    """FCFS proxy ordering: earliest-triggered first (tiebreak signal_id), the order
    the concurrent FCFS pool would admit in on average. Used only for the shadow
    regret counterfactual — never governs live admission."""
    return sorted(candidates, key=lambda c: (float(c.triggered_epoch), str(c.signal_id)))
