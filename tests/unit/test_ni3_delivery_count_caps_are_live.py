"""
tests/unit/test_ni3_delivery_count_caps_are_live.py

NI-3 (22-Aug-2026) — COMMENTS ONLY. No logic changed anywhere for this item.

max_open_delivery_positions and max_daily_delivery_trades carried a clause saying
they did nothing while force_intraday_only coerced every strategy to INTRADAY.
force_intraday_only is FALSE. The clause was a true conditional with a false
antecedent, which reads as "ignore me" -- the same hazard the delivery SIZING
comments carried until fix item 1 removed them.

Three things are pinned here, because a comment fix that only deletes words is
worth nothing if the replacement words are also wrong:

  1. NO SURVIVING CLAIM -- the phrase family is gone from every site that describes
     these two caps. The check is proven able to go red by running the same sweep
     over text that still contains it.
  2. THE ANTECEDENT REALLY IS FALSE in the shipped config. If force_intraday_only
     is ever set back to true, this goes red and the new comments must be revisited
     -- which is the correct outcome, not a nuisance.
  3. THE NEW WORD IS TRUE -- "ENFORCED" is asserted against the code, not just
     written into a comment: a delivery entry at the cap is rejected.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]

# Every place that describes the two delivery COUNT caps.
# ⛔ docs/SYSTEM_MAP.md is deliberately NOT here: it is a DATED changelog and its
# 25/26-Jun entries were true when written. Rewriting a dated record to match today
# would falsify the history, which is a worse defect than the one being fixed.
_SITES = [
    "config/system_config.yaml",
    "capital/risk_engine.py",
    "core/config_loader.py",
    "core/state_store.py",
    "tests/unit/test_phase3_delivery_caps_conditional_capital.py",
]

# The claim family, lower-cased. Each asserts the caps do nothing.
_STALE_CLAIMS = (
    "inert while force_intraday_only",
    "inert while coerced",
    "delivery_enabled stays false",
    "locked-but-inert",
    "locked but inert",
)


@pytest.mark.parametrize("relpath", _SITES)
def test_no_surviving_claim_that_the_delivery_count_caps_are_inert(relpath: str) -> None:
    text = (_REPO / relpath).read_text(encoding="utf-8").lower()
    hits = [claim for claim in _STALE_CLAIMS if claim in text]
    assert not hits, f"{relpath} still carries: {hits}"


def test_the_stale_claim_sweep_can_go_red() -> None:
    """CONTROL. The sweep above is only evidence if it detects the claim when present.

    Runs the identical matcher over the pre-NI-3 wording. If this ever passes
    vacuously, the parametrized test above is worthless.
    """
    pre_ni3 = (
        "  max_open_delivery_positions: 3  # SLICE2.5-PHASE-3 (A): separate cap on "
        "concurrent open DELIVERY (CNC) positions; inert while force_intraday_only=true"
    ).lower()
    assert [c for c in _STALE_CLAIMS if c in pre_ni3], "the matcher cannot see the claim"


def test_the_antecedent_is_false_in_the_shipped_config() -> None:
    """The comments now say ENFORCED. That is only true while this holds."""
    cfg = yaml.safe_load((_REPO / "config" / "system_config.yaml").read_text(encoding="utf-8"))
    assert cfg["force_intraday_only"] is False, (
        "force_intraday_only is TRUE again -- the NI-3 comments say these caps are "
        "ENFORCED and would now be misleading. Revisit them; do not silence this test."
    )
    assert cfg["delivery_enabled"] is True
    assert cfg["trade_type"] == "BOTH"
    # and the caps are their own numbers, not copies of the intraday twins
    risk = cfg["risk"]
    assert risk["max_open_delivery_positions"] == 3
    assert risk["max_daily_delivery_trades"] == 5
    assert risk["max_open_positions"] == 5
    assert risk["max_daily_trades"] == 10


def test_the_branch_ni3_redescribes_is_the_branch_that_is_wired() -> None:
    """"ENFORCED" must point at real code, not just replace one comment with another.

    Deliberately narrow -- a containment check. The BEHAVIOURAL proof that these
    caps reject a delivery entry already exists and is NOT duplicated here:
    test_phase3_delivery_caps_conditional_capital.py::test_open_delivery_cap_rejects_4th
    and ::test_daily_delivery_cap_rejects_6th drive a real RiskEngine over a real
    StateStore and assert the rejection reason. What this adds is the link: the branch
    NI-3 re-describes is the one those tests exercise.
    """
    src = (_REPO / "capital" / "risk_engine.py").read_text(encoding="utf-8")
    assert 'if sizing_result.bucket == "positional":' in src
    assert "open_delivery_count >= self._max_open_delivery" in src
    assert "daily_delivery_count >= self._max_daily_delivery" in src

    proof = (_REPO / "tests" / "unit"
             / "test_phase3_delivery_caps_conditional_capital.py").read_text(encoding="utf-8")
    assert "def test_open_delivery_cap_rejects_4th" in proof
    assert "def test_daily_delivery_cap_rejects_6th" in proof
