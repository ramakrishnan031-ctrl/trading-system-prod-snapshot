"""
tests/unit/test_position_sizer_delivery_scaffold.py

22-Aug-2026 (fix item 1) — THIS FILE'S SUBJECT WAS INVERTED, DELIBERATELY.

It used to prove an "INERT delivery-sizing scaffold": that an unset delivery knob
fell back to the global (intraday) value, "byte-identical". That fallback is the
defect this build removes — delivery is live, has traded, and was being sized on
the intraday risk budget through exactly that branch. The three assertions below
that used to demand inheritance now demand REFUSAL.

Kept under the original filename on purpose: the history of a contract that
reversed is easier to follow when the file that asserted the old one asserts the
new one. (The word "scaffold" in the name is now stale — recorded, not silently
renamed mid-gate.)
"""
from __future__ import annotations

import logging

import pytest

from capital.position_sizer import PositionSizer


class _Snap:
    total = 100000.0
    intraday_avail = 70000.0
    positional_avail = 30000.0
    daily_realized_pnl = 0.0


class _FM:
    def get_snapshot(self):
        return _Snap()


_LEV = {"INTRADAY": 5.0, "DELIVERY": 1.0}

# The delivery values a fully-configured sizer carries in these tests. Equal to the
# globals below, so "configured" and "inherited" would have produced the same number
# — which is precisely why the old fallback was invisible.
_DELIVERY_OK = dict(
    delivery_risk_per_trade_pct=0.01,
    delivery_max_concentration_pct=0.10,
    delivery_max_position_value_pct=0.40,
)


def _sizer(**kw):
    return PositionSizer(
        fund_manager=_FM(), leverage_map=_LEV,
        risk_per_trade_pct=0.01, max_concentration_pct=0.10,
        max_position_value_pct=0.40, logger=logging.getLogger("t"), **kw,
    )


# entry 100 / sl 50 -> sl_distance 50 -> qty_by_risk = (cap*risk_pct)/50; RISK binds.
def _calc(s, intent="INTRADAY"):
    return s.calculate("SYM", "BUY", 100.0, 50.0, intent, score_tier="HIGH")


def test_unconfigured_delivery_refuses_it_does_not_inherit():
    """WAS: `test_default_none_is_byte_identical` — an unset delivery knob silently
    became the global. NOW: sizing a DELIVERY entry without delivery config RAISES,
    and the message names the key so the log alone diagnoses it."""
    s = _sizer()  # no delivery kwargs at all
    with pytest.raises(ValueError) as exc:
        _calc(s, intent="DELIVERY")
    assert "delivery_risk_per_trade_pct" in str(exc.value)
    # and it must not have quietly produced the intraday answer instead
    assert "20" not in str(exc.value)


def test_each_missing_delivery_key_is_named_individually():
    """A partially-configured sizer still refuses, naming the FIRST key it needs.
    Every key is checked, not just the first one that happened to be added."""
    for missing in ("delivery_risk_per_trade_pct",
                    "delivery_max_concentration_pct",
                    "delivery_max_position_value_pct"):
        kw = dict(_DELIVERY_OK)
        kw[missing] = None
        with pytest.raises(ValueError) as exc:
            _calc(_sizer(**kw), intent="DELIVERY")
        assert missing in str(exc.value), f"{missing} was not named in the refusal"


def test_delivery_knob_set_but_intraday_uses_global():
    """UNCHANGED IN INTENT: the delivery keys never touch an intraday entry."""
    kw = dict(_DELIVERY_OK)
    kw["delivery_risk_per_trade_pct"] = 0.005          # half the global risk
    s = _sizer(**kw)
    assert _calc(s, intent="INTRADAY").qty == 20       # still global 0.01
    # and an intraday entry does not even need delivery config present
    assert _calc(_sizer(), intent="INTRADAY").qty == 20


def test_delivery_bucket_applies_the_delivery_risk():
    """A DELIVERY (positional) entry is sized on the DELIVERY risk budget."""
    kw = dict(_DELIVERY_OK)
    kw["delivery_risk_per_trade_pct"] = 0.005
    assert _calc(_sizer(**kw), intent="DELIVERY").qty == 10    # 500/50
    # configured equal to the global -> the same 20 the old fallback produced.
    # This is the behaviour-neutrality of the shipped values, stated as a test.
    assert _calc(_sizer(**_DELIVERY_OK), intent="DELIVERY").qty == 20


def test_delivery_max_position_value_is_inert_on_intraday():
    """A tiny delivery position cap set, but INTRADAY sizing is unaffected."""
    kw = dict(_DELIVERY_OK)
    kw["delivery_max_position_value_pct"] = 0.001   # would reject most delivery sizes
    assert _calc(_sizer(**kw), intent="INTRADAY").success is True
