"""
tests/unit/test_position_sizer_delivery_scaffold.py — V3 03.06 (Step 5).

Proves the INERT delivery-sizing scaffold:
  • default None → global risk/max-position-value (byte-identical);
  • delivery knobs SET but intent=INTRADAY → still global (never touches live sizing,
    which is always intraday under force_intraday_only);
  • the scaffold branch DOES apply for a positional (delivery) entry — for the future
    V3 delivery path — without activating delivery anywhere.
"""
from __future__ import annotations

import logging

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


def _sizer(**kw):
    return PositionSizer(
        fund_manager=_FM(), leverage_map=_LEV,
        risk_per_trade_pct=0.01, max_concentration_pct=0.10,
        max_position_value_pct=0.40, logger=logging.getLogger("t"), **kw,
    )


# entry 100 / sl 50 → sl_distance 50 → qty_by_risk = (cap*risk_pct)/50; RISK binds.
def _calc(s, intent="INTRADAY"):
    return s.calculate("SYM", "BUY", 100.0, 50.0, intent, score_tier="HIGH")


def test_default_none_is_byte_identical():
    base = _calc(_sizer())                                   # no delivery kwargs
    scaf = _calc(_sizer(delivery_risk_per_trade_pct=None,
                        delivery_max_position_value_pct=None))
    assert (base.qty, base.margin_required, base.risk_amount, base.constraint) == \
           (scaf.qty, scaf.margin_required, scaf.risk_amount, scaf.constraint)
    assert base.qty == 20                                    # risk-bound: 1000/50


def test_delivery_knob_set_but_intraday_uses_global():
    # delivery knob SET, but an INTRADAY entry (the only kind that occurs live) is
    # UNCHANGED — the scaffold never touches the live path.
    s = _sizer(delivery_risk_per_trade_pct=0.005)            # half the global risk
    assert _calc(s, intent="INTRADAY").qty == 20             # still global 0.01


def test_delivery_bucket_applies_delivery_risk_scaffold():
    # a DELIVERY (positional) entry uses the delivery risk — proves the branch works
    # for the future V3 delivery path (nothing here activates delivery).
    s = _sizer(delivery_risk_per_trade_pct=0.005)
    assert _calc(s, intent="DELIVERY").qty == 10             # 500/50 (delivery 0.005)
    # and with NO delivery knob, a delivery entry falls back to the global 0.01
    assert _calc(_sizer(), intent="DELIVERY").qty == 20


def test_delivery_max_position_value_scaffold_inert_on_intraday():
    # a tiny delivery position cap set, but INTRADAY sizing is unaffected.
    s = _sizer(delivery_max_position_value_pct=0.001)        # would reject most delivery sizes
    assert _calc(s, intent="INTRADAY").success is True       # intraday untouched
