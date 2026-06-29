"""ops/control_tower/model.py -- Control Tower Phase 1b shared types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Finding:
    """One detected issue, in TOWER vocabulary. UPSERTed into
    control_tower_findings on the dedup identity (category, resource_name,
    reason)."""
    category: str            # security | cron | config | freshness | disk | backup
    severity: str            # CRITICAL | HIGH | MEDIUM | LOW | INFO  (tower scale)
    resource_type: str       # file | service | job | table | mount | dir
    resource_name: str       # the dedup key part — keep it stable + non-empty
    reason: str              # the dedup key part — why this is a finding
    location: str = ""
    recommended_action: str = ""


@dataclass
class SourceResult:
    """A monitor adapter's read: a per-source status + its findings."""
    source: str              # security | cron | config | excursion
    status: str              # ok | warn | critical | unavailable | not_run | failed
    findings: List[Finding] = field(default_factory=list)
    detail: str = ""
