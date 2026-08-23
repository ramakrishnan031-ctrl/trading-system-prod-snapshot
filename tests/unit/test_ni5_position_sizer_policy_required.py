"""
tests/unit/test_ni5_position_sizer_policy_required.py

NI-5 (22-Aug-2026) — PositionSizer's SAFETY-CRITICAL POLICY limits lose their
silent defaults; its LEGITIMATE PROGRAMMING defaults keep theirs.

The classification, and why each side of it is where it is:

  POLICY (no default) — these multiply capital into a number of shares, so a
  silent value sizes REAL MONEY on a number nobody chose:
      risk_per_trade_pct · max_concentration_pct · max_position_value_pct

  PROGRAMMING (default kept) — none of these can ENLARGE a position. They are a
  1-share floor, tick/skew guards, a sanity cap, optional collaborators, a mode
  switch already guarded by its own raise, and item 1's delivery knobs whose None
  is a hard error on use:
      min_qty_threshold · tier_multipliers · logger · instrument_cache ·
      lot_skew_rejection_threshold · min_tick_size · max_single_order_qty ·
      broker_adapter · enabled · flat_value_rs · delivery_*

RiskEngine's docstring already forbade defaults for itself — *"RiskEngine takes NO
defaults — all caps are required, so a component built without config fails fast
rather than running loose."* The two classes now agree, and that agreement is
asserted below rather than left as a comment.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from capital.fund_manager import CapitalSnapshot          # noqa: E402
from capital.position_sizer import PositionSizer          # noqa: E402
from core.time_authority import now_ist                   # noqa: E402

_POLICY = ("risk_per_trade_pct", "max_concentration_pct", "max_position_value_pct")

# Kept on purpose. The test names them so REMOVING one is also a deliberate act.
_PROGRAMMING = {
    "min_qty_threshold": 1,
    "tier_multipliers": None,
    "logger": None,
    "instrument_cache": None,
    "lot_skew_rejection_threshold": 0.25,
    "min_tick_size": 0.05,
    "max_single_order_qty": 10000,
    "broker_adapter": None,
    "enabled": True,
    "flat_value_rs": None,
    "delivery_risk_per_trade_pct": None,
    "delivery_max_concentration_pct": None,
    "delivery_max_position_value_pct": None,
}

_LEV = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}


class _FM:
    def get_snapshot(self):
        return CapitalSnapshot(
            total=100_000.0, intraday_avail=70_000.0, intraday_reserved=0.0,
            intraday_used=0.0, positional_avail=30_000.0, positional_reserved=0.0,
            positional_used=0.0, daily_realized_pnl=0.0,
            ts=now_ist().replace(tzinfo=None).isoformat())


def _params():
    return inspect.signature(PositionSizer.__init__).parameters


@pytest.mark.parametrize("name", _POLICY)
def test_policy_limits_have_no_default(name: str) -> None:
    p = _params()[name]
    assert p.default is inspect.Parameter.empty, (
        f"{name} carries a silent default of {p.default!r} again — a PositionSizer "
        "built without config would size real money on a number nobody chose."
    )


@pytest.mark.parametrize("name,expected", sorted(_PROGRAMMING.items()))
def test_programming_defaults_are_kept_and_unchanged(name: str, expected) -> None:
    """The other half of the classification. Deleting every default was NOT the fix."""
    p = _params()[name]
    assert p.default is not inspect.Parameter.empty, f"{name} lost a legitimate default"
    assert p.default == expected, f"{name} default moved: {p.default!r} != {expected!r}"


@pytest.mark.parametrize("omit", _POLICY)
def test_omitting_a_policy_limit_raises(omit: str) -> None:
    """Construction FAILS rather than running loose — the RiskEngine contract."""
    kwargs = dict(fund_manager=_FM(), leverage_map=_LEV,
                  risk_per_trade_pct=0.01, max_concentration_pct=0.10,
                  max_position_value_pct=0.40)
    kwargs.pop(omit)
    with pytest.raises(TypeError) as exc:
        PositionSizer(**kwargs)
    assert omit in str(exc.value), str(exc.value)


def test_the_classification_matches_riskengines_stated_contract() -> None:
    """RiskEngine says it takes NO defaults for its caps. PositionSizer now agrees."""
    from capital.risk_engine import RiskEngine
    doc = RiskEngine.__doc__ or ""
    assert "takes NO defaults" in doc, (
        "RiskEngine's docstring no longer states the contract this item aligned to — "
        "re-check whether the classification still has an anchor."
    )
    re_params = inspect.signature(RiskEngine.__init__).parameters
    for name in ("max_open_positions", "max_daily_trades", "max_sector_exposure_pct",
                 "max_consecutive_losses", "daily_loss_limit_pct"):
        assert re_params[name].default is inspect.Parameter.empty, name


def test_behaviour_is_unchanged_when_the_values_are_passed() -> None:
    """The whole point: explicit 0.01/0.10/0.40 reproduces what the defaults produced.

    total=100k, risk 1% -> risk_rs 1000; entry 50 / sl 40 -> sl_dist 10 -> 100 shares;
    capital 70k @ lev 5 -> 7000; concentration 10% -> 10000/50 = 200. RISK binds at 100.
    """
    sizer = PositionSizer(fund_manager=_FM(), leverage_map=_LEV,
                          risk_per_trade_pct=0.01, max_concentration_pct=0.10,
                          max_position_value_pct=0.40)
    r = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="HIGH")
    assert r.success and r.qty == 100 and r.constraint == "RISK", (r.qty, r.constraint)


def test_every_construction_site_in_the_tree_passes_all_three() -> None:
    """The sweep that makes the change safe, run as a test rather than trusted once.

    Every PositionSizer(...) in the tree must supply all three policy limits. Sites
    that build their arguments in a dict and splat it are exempt from the AST check
    and are asserted separately by importing nothing -- they are covered by the
    suite actually running.
    """
    import ast

    need = set(_POLICY)
    missing, seen, splat = [], 0, 0
    for path in sorted(_REPO.rglob("*.py")):
        sp = str(path).replace("\\", "/")
        if "/venv/" in sp or "__pycache__" in sp or "/.git/" in sp:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            nm = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else None)
            if nm != "PositionSizer":
                continue
            seen += 1
            if any(k.arg is None for k in node.keywords):
                splat += 1
                continue
            if node.args:
                missing.append(f"{sp}:{node.lineno} passes {len(node.args)} POSITIONAL args")
                continue
            gap = need - {k.arg for k in node.keywords if k.arg}
            if gap:
                missing.append(f"{sp}:{node.lineno} missing {sorted(gap)}")

    assert seen >= 20, f"the sweep found only {seen} construction sites — it is not looking"
    assert not missing, "PositionSizer built without a policy limit:\n  " + "\n  ".join(missing)
