"""
tests/unit/test_pb01_registration.py — V3 Step 10b: PB-01 registration + fail-closed.

Proves the registration artifacts for the PB-01 "Breakout + Retest" shadow playbook are
wired end-to-end AND that PB-01 can NEVER place a real order (the non-negotiable
G-NO-ORDER safety requirement) at the strategy-control layer.

PB-01 is registered so a Chartink EOD scanner can be RECEIVED / AUTHENTICATED / ROUTED
and so the V3 decision chain can produce would-be records — but it is enabled:false +
v3_playbook:true, so strategies.control.strategy_will_trade() returns WON'T TRADE and the
pipeline rejects it BEFORE any sizing / reservation / order placement.
"""
from __future__ import annotations

from pathlib import Path

from core.config_loader import load_all
from strategies.control import (
    CAUSE_DISABLED,
    strategy_will_trade,
)
from strategies.loader import StrategyLoader

_ROOT = Path(__file__).parent.parent.parent
_CFG = _ROOT / "config"
_SCAN_MAP = _CFG / "scan_webhook_map.yaml"

PB01 = "pb01_breakout_retest"


def _load_strategies():
    return StrategyLoader().load_all_strategies(
        _CFG / "strategies",
        scan_webhook_map_path=_SCAN_MAP,   # S10 cross-validation must pass
        force_intraday_only=True,
    )


# ── Registration wiring ───────────────────────────────────────────────────────

def test_pb01_in_scan_webhook_map_resolves_to_strategy():
    cfg = load_all(_CFG)
    assert PB01 in cfg.scan_webhook_map.scanners, "PB-01 not registered in scan_webhook_map"
    assert cfg.scan_webhook_map.scanners[PB01].strategy == PB01


def test_pb01_in_chartink_scanners():
    cfg = load_all(_CFG)
    assert PB01 in cfg.chartink_scanners.scanners, "PB-01 missing from chartink_scanners"


def test_pb01_strategy_yaml_loads_and_is_shadow_playbook():
    strategies = _load_strategies()  # raises if S10 cross-validation fails
    assert PB01 in strategies, "PB-01 strategy YAML not loaded"
    pb = strategies[PB01]
    assert pb.v3_playbook is True, "PB-01 must be marked v3_playbook:true"
    assert pb.enabled is False, "PB-01 must be enabled:false (fail-closed registration)"


# ── G-NO-ORDER: fail-closed at the control layer ──────────────────────────────

def test_pb01_wont_trade_under_live_settings():
    """Live config (trade_type=INTRADAY, force_intraday_only=True) -> WON'T TRADE."""
    pb = _load_strategies()[PB01]
    v = strategy_will_trade(pb, trade_type="INTRADAY", force_intraday_only=True)
    assert v.will_trade is False
    assert v.cause == CAUSE_DISABLED  # the enabled:false switch, not a product gate


def test_pb01_wont_trade_even_under_most_permissive_settings():
    """Even trade_type=BOTH + force OFF cannot make PB-01 trade — enabled:false dominates.

    This is the G-NO-ORDER guarantee: no combination of the master trade_type / breaker
    can admit PB-01, so it can never reach sizing / reservation / placement.
    """
    pb = _load_strategies()[PB01]
    for tt in ("INTRADAY", "DELIVERY", "BOTH"):
        for force in (True, False):
            v = strategy_will_trade(pb, trade_type=tt, force_intraday_only=force)
            assert v.will_trade is False, f"PB-01 admitted under trade_type={tt} force={force}"
            assert v.cause == CAUSE_DISABLED


# ── G-OFF: the 15 live strategies are unchanged (no playbook) ──────────────────

def test_live_15_strategies_are_not_playbook():
    strategies = _load_strategies()
    live = {n: s for n, s in strategies.items() if not s.v3_playbook}
    assert len(live) == 15, f"expected 15 live strategies, got {len(live)}"
    # Exactly one playbook is registered (PB-01).
    playbooks = {n for n, s in strategies.items() if s.v3_playbook}
    assert playbooks == {PB01}, f"unexpected playbook set: {playbooks}"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"  OK {_name}")
    print("\nPB-01 registration + fail-closed: all checks passed.")
