"""
allocation/ — V3 03.05 Portfolio Allocator (ranked batch admission).

Default-OFF. When allocator_mode == "off" this package is not even imported by the hot
path (main.py builds the allocator only for shadow/enforce; signal_processor gets
allocator=None → the current FCFS admission is byte-identical).
"""
from __future__ import annotations

from allocation.models import AdmitPayload, RegretRecord, ScoredCandidate, tier_rank
from allocation.portfolio_allocator import PortfolioAllocator
from allocation.ranker import arrival_ordered, rank_candidates
from allocation.window_buffer import WindowBuffer

__all__ = [
    "AdmitPayload",
    "RegretRecord",
    "ScoredCandidate",
    "tier_rank",
    "PortfolioAllocator",
    "arrival_ordered",
    "rank_candidates",
    "WindowBuffer",
]
