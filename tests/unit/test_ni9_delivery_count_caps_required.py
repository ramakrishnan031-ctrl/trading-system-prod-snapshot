"""
tests/unit/test_ni9_delivery_count_caps_required.py

BUG-NI9 — RiskEngine kept silent 3 / 5 defaults for the two delivery COUNT caps.

`RiskEngine`'s own docstring says it *"takes NO defaults -- all caps are required, so
a component built without config fails fast rather than running loose."* It
contradicted itself on exactly `max_open_delivery_positions` and
`max_daily_delivery_trades`, which defaulted to 3 and 5. NI-4 closed the CONFIG half
(a missing YAML key now refuses at boot); an engine built OUTSIDE the config path
still got silent values.

The fix reuses `_require_delivery`, the refusal helper fix item 1 already added to
this same file for the delivery pct limits, rather than making the parameters
positionally required. That choice is deliberate: a caller that never gates a
delivery entry is unaffected, so the silent-value hole closes without touching the
eight test files that construct a RiskEngine but never reach the positional branch.
"""
from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest

from capital.risk_engine import RiskEngine
from core.state_store import StateStore
from tests.unit.test_risk_engine import (
    _MockFundManager,
    _make_sizing,
    _make_snap,
)


def _engine(store, *, with_caps: bool):
    kw = dict(
        fund_manager=_MockFundManager(_make_snap()), state_store=store,
        max_open_positions=100, max_daily_trades=100,
        max_sector_exposure_pct=0.40, max_consecutive_losses=100,
        daily_loss_limit_pct=0.99, sector_lookup_fn=lambda _s: "IT",
        logger=logging.getLogger("t_ni9"), kill_switch=None,
        delivery_max_sector_exposure_pct=0.40,
        delivery_daily_loss_limit_pct=0.99,
    )
    if with_caps:
        kw["max_open_delivery_positions"] = 3
        kw["max_daily_delivery_trades"] = 5
    return RiskEngine(**kw)


def test_the_signature_no_longer_carries_the_silent_defaults() -> None:
    params = inspect.signature(RiskEngine.__init__).parameters
    for name in ("max_open_delivery_positions", "max_daily_delivery_trades"):
        assert params[name].default is None, (
            f"{name} carries a silent default of {params[name].default!r} again — a "
            "RiskEngine built outside the config path would gate real delivery entries "
            "on a number nobody chose")


def test_gating_a_DELIVERY_entry_without_the_caps_REFUSES(tmp_path: Path) -> None:
    """THE DEFECT: this used to silently gate on 3 / 5."""
    store = StateStore(tmp_path / "t.db")
    eng = _engine(store, with_caps=False)
    with pytest.raises(ValueError) as exc:
        eng.approve("INFY", "BUY", "DELIVERY",
                    _make_sizing(bucket="positional"), "sig")
    msg = str(exc.value)
    assert "max_open_delivery_positions" in msg, (
        "the refusal must name the missing key so the log alone diagnoses it")
    assert "does NOT fall back" in msg


def test_an_INTRADAY_entry_is_unaffected_by_the_missing_caps(tmp_path: Path) -> None:
    """The whole point of optional-and-refuse: callers that cannot reach the
    delivery branch keep working, so the blast radius stays at the sites that
    actually gate a positional entry."""
    store = StateStore(tmp_path / "t.db")
    eng = _engine(store, with_caps=False)
    res = eng.approve("INFY", "BUY", "INTRADAY",
                      _make_sizing(bucket="intraday"), "sig")
    assert res.approved, res.reason


def test_supplying_the_caps_restores_normal_gating(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "t.db")
    eng = _engine(store, with_caps=True)
    res = eng.approve("INFY", "BUY", "DELIVERY",
                      _make_sizing(bucket="positional"), "sig")
    assert res.approved, res.reason
