"""
tests/unit/test_v3_strategy_taxonomy.py — V3 side-task A · strategy taxonomy.

Metadata + display only. Proves: the required pipeline/horizon enums (validated at load),
the correct tagging of all 16 strategies (12 INTRADAY·SAME_DAY + 1 INTRADAY·NEXT_DAY[PB-01]
+ 3 DELIVERY·SWING), the load-time rejection of a missing/invalid value, and the display
surfaces (taxonomy_label + the operator status table's Category column). The positional_*
strategies are SWING (multi-day) NOT BTST — pinned so a future edit can't silently mis-tag.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.exceptions import ConfigSchemaError
from scripts.strategy_status import build_status_rows
from strategies.schema import StrategyConfig, validate_strategy
from strategies.taxonomy import build_taxonomy_map, taxonomy_label

_CFG = Path("config")


# ── the helper ────────────────────────────────────────────────────────────────

def test_taxonomy_label():
    assert taxonomy_label("INTRADAY", "SAME_DAY") == "INTRADAY·SAME_DAY"
    assert taxonomy_label("DELIVERY", "SWING") == "DELIVERY·SWING"
    assert taxonomy_label(None, None) == "—·—"          # tolerant of missing → never raises


# ── required + validated enums ────────────────────────────────────────────────

def _valid_kwargs(**over):
    base = dict(
        name="t", display_name="T", description="d", direction="LONG", intent="INTRADAY",
        order_protocol="CO_PLUS_TGT", pipeline="INTRADAY", horizon="SAME_DAY",
        entry_method="LIMIT", sl_method="FIXED_PCT", sl_pct=0.01, tgt_method="RISK_REWARD",
        tgt_risk_reward=2.0, smart_tgt_enabled=False, pullback_wait_enabled=False)
    base.update(over)
    return base


def test_pipeline_horizon_required():
    # Both are REQUIRED (no silent default that could mislabel a strategy).
    with pytest.raises(Exception):
        StrategyConfig(**{k: v for k, v in _valid_kwargs().items() if k != "pipeline"})
    with pytest.raises(Exception):
        StrategyConfig(**{k: v for k, v in _valid_kwargs().items() if k != "horizon"})


def test_invalid_enum_values_rejected():
    with pytest.raises(Exception):
        StrategyConfig(**_valid_kwargs(pipeline="SWING"))     # not a pipeline
    with pytest.raises(Exception):
        StrategyConfig(**_valid_kwargs(horizon="INTRADAY"))   # not a horizon
    # valid ones pass
    assert StrategyConfig(**_valid_kwargs(pipeline="DELIVERY", horizon="SWING")).horizon == "SWING"


# ── all 16 tagged correctly ───────────────────────────────────────────────────

def test_all_16_strategies_tagged_correctly():
    tax = build_taxonomy_map(_CFG)
    assert len(tax) == 16
    from collections import Counter
    tally = Counter(tax.values())
    assert tally[("INTRADAY", "SAME_DAY")] == 12
    assert tally[("INTRADAY", "NEXT_DAY")] == 1          # PB-01 — analysis overnight, position intraday
    assert tally[("DELIVERY", "SWING")] == 3
    assert tax["pb01_breakout_retest"] == ("INTRADAY", "NEXT_DAY")


def test_positional_are_swing_not_btst():
    # VERIFIED FINDING (spec-A2): the 3 positional_* YAMLs declare MULTI-DAY holds
    # ("Multi-day long position on…"), so the correct horizon is SWING, NOT BTST. Pinned.
    for name in ("positional_momentum_long", "positional_sector_rotation", "positional_swing_long"):
        cfg = validate_strategy(_CFG / "strategies" / f"{name}.yaml")
        assert cfg.pipeline == "DELIVERY" and cfg.horizon == "SWING", name
        assert cfg.horizon != "BTST", f"{name} is multi-day, NOT buy-today-sell-tomorrow"


# ── the display surface (operator status table) ───────────────────────────────

def test_status_table_carries_category():
    rows = build_status_rows(_CFG, trade_type="INTRADAY", force_intraday_only=True)
    assert len(rows) == 15                                   # PB-01 (v3_playbook) still filtered out
    for r in rows:
        assert "·" in r.category                             # every live row shows pipeline·horizon
    cats = {r.category for r in rows}
    assert "INTRADAY·SAME_DAY" in cats and "DELIVERY·SWING" in cats


if __name__ == "__main__":
    import sys
    mod = sys.modules[__name__]
    for name in sorted(dir(mod)):
        if name.startswith("test_"):
            getattr(mod, name)()
            print(f"  OK {name}")
    print("\nstrategy taxonomy: all checks passed.")
