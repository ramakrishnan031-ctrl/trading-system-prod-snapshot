"""
tests/unit/test_diary4_tier_multiplier.py -- Diary #4 tier-multiplier ON/OFF switch.

ON  (enabled=true, default)  = score-tier x perf-weight sizing (existing, unchanged).
OFF (enabled=false)          = flat Rs/order (Option delta): flat_value_rs is ONE MORE
                               ceiling on top of risk/capital/concentration; score and
                               perf-weight are NOT applied.

Worked example used throughout (total=200k, risk=1%, conc=20%, INTRADAY lev 5x,
entry=1000, sl=985 -> sl_distance=15):
    qty_by_risk          = floor(2000 / 15)           = 133
    qty_by_capital       = floor(200000 / (1000/5))   = 1000
    qty_by_concentration = floor((200000*0.20)/1000)  = 40
    raw_qty              = min(133, 1000, 40)          = 40   (CONCENTRATION-bound)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from capital.fund_manager import CapitalSnapshot
from capital.position_sizer import PositionSizer
from core.config_loader import PositionSizingConfig, PositionSizingTierConfig, load_all


def _make_sizer(total=200000.0, risk_pct=0.01, max_conc=0.20, *,
                enabled=True, flat_value_rs=None, lot_skew=0.25):
    fm = MagicMock()
    fm.get_snapshot.return_value = CapitalSnapshot(
        total=total, intraday_avail=total, intraday_reserved=0.0, intraday_used=0.0,
        positional_avail=total, positional_reserved=0.0, positional_used=0.0,
        daily_realized_pnl=0.0, ts="2026-06-22T09:20:00",
    )
    return PositionSizer(
        fund_manager=fm,
        leverage_map={"INTRADAY": 5.0, "DELIVERY": 1.0},
        risk_per_trade_pct=risk_pct,
        max_concentration_pct=max_conc,
        lot_skew_rejection_threshold=lot_skew,
        max_position_value_pct=1.0,   # effectively off; isolate the switch
        enabled=enabled,
        flat_value_rs=flat_value_rs,
    )


# ── ON-path: must be unchanged (Monday's safety net) ─────────────────────────

def test_on_mode_score_based_sizing_unchanged():
    """HIGH/MED/LOW scale raw_qty(40) by 1.0/0.7/0.5 -> 40/28/20 (existing behaviour)."""
    s = _make_sizer()
    high = s.calculate("X", "BUY", 1000.0, 985.0, "INTRADAY", "HIGH")
    med = s.calculate("X", "BUY", 1000.0, 985.0, "INTRADAY", "MEDIUM")
    low = s.calculate("X", "BUY", 1000.0, 985.0, "INTRADAY", "LOW")
    assert (high.qty, med.qty, low.qty) == (40, 28, 20)


def test_schema_default_enabled_is_true():
    """Regression guard: schema default must be ON (never drift to OFF)."""
    cfg = PositionSizingConfig(**_ps_kwargs())
    assert cfg.enabled is True
    assert cfg.flat_value_rs is None


def test_shipped_config_default_enabled_is_true():
    """The deployed config/system_config.yaml must ship enabled: true (Monday unaffected)."""
    cfg = load_all(Path(__file__).parents[2] / "config")
    assert cfg.system.position_sizing.enabled is True


def test_on_mode_breakdown_fields():
    s = _make_sizer()
    r = s.calculate("X", "BUY", 1000.0, 985.0, "INTRADAY", "HIGH")
    assert r.breakdown["tier_multiplier_mode"] == "ON"
    assert r.breakdown["tier_weight_applied"] == 1.0
    assert r.breakdown["perf_weight_applied"] == 1.0
    assert r.breakdown["flat_value_rs_used"] is None
    assert r.breakdown["qty_by_flat"] is None
    assert r.breakdown["binding_constraint"] == "concentration"
    assert r.breakdown["actual_position_value_rs"] == 40 * 1000.0


# ── OFF-path: flat rupee value (Option delta) ────────────────────────────────

def test_off_mode_three_scores_yield_same_qty():
    """OFF: HIGH/MED/LOW on the same signal -> identical qty (score ignored)."""
    s = _make_sizer(enabled=False, flat_value_rs=5000.0)   # qty_by_flat = 5
    qtys = {t: s.calculate("X", "BUY", 1000.0, 985.0, "INTRADAY", t).qty
            for t in ("HIGH", "MEDIUM", "LOW")}
    assert qtys == {"HIGH": 5, "MEDIUM": 5, "LOW": 5}


def test_off_mode_flat_value_caps_qty():
    """flat (5 sh) tighter than raw_qty (40) -> qty=5, constraint=FLAT."""
    s = _make_sizer(enabled=False, flat_value_rs=5000.0)
    r = s.calculate("X", "BUY", 1000.0, 985.0, "INTRADAY", "HIGH")
    assert r.success and r.qty == 5
    assert r.constraint == "FLAT"
    assert r.breakdown["binding_constraint"] == "flat"
    assert r.breakdown["qty_by_flat"] == 5
    assert r.breakdown["flat_value_rs_used"] == 5000.0
    assert r.breakdown["tier_weight_applied"] is None


def test_off_mode_safety_ceiling_caps_below_flat():
    """flat (100 sh) looser than concentration (40) -> qty=40, constraint stays CONCENTRATION."""
    s = _make_sizer(enabled=False, flat_value_rs=100000.0)  # qty_by_flat = 100
    r = s.calculate("X", "BUY", 1000.0, 985.0, "INTRADAY", "HIGH")
    assert r.success and r.qty == 40
    assert r.constraint == "CONCENTRATION"
    assert r.breakdown["binding_constraint"] == "concentration"


def test_off_mode_skip_when_below_one_lot():
    """flat below 1 lot worth -> qty 0 -> BELOW_MIN skip with a flat-specific reason."""
    s = _make_sizer(enabled=False, flat_value_rs=500.0)     # floor(500/1000)=0
    r = s.calculate("X", "BUY", 1000.0, 985.0, "INTRADAY", "HIGH")
    assert not r.success
    assert r.constraint == "BELOW_MIN"
    assert "below 1 lot" in r.reason


def test_off_mode_lot_size_rounding_floor():
    """OFF respects lot rounding: tiered 47 with lot 25 -> 25 (skew guard relaxed here)."""
    s = _make_sizer(total=500000.0, enabled=False, flat_value_rs=47000.0, lot_skew=0.99)
    r = s.calculate("X", "BUY", 1000.0, 985.0, "INTRADAY", "HIGH", lot_size=25)
    assert r.success and r.qty == 25


def test_off_mode_breakdown_fields():
    s = _make_sizer(enabled=False, flat_value_rs=5000.0)
    r = s.calculate("X", "BUY", 1000.0, 985.0, "INTRADAY", "MEDIUM")
    assert r.breakdown["tier_multiplier_mode"] == "OFF_FLAT"
    assert r.breakdown["tier_weight_applied"] is None
    assert r.breakdown["perf_weight_applied"] is None
    assert r.breakdown["flat_value_rs_used"] == 5000.0
    assert r.breakdown["qty_by_flat"] == 5
    assert r.breakdown["actual_position_value_rs"] == 5 * 1000.0


# ── Parity: one code path, no paper/live branch ──────────────────────────────

def test_off_path_is_mode_agnostic_parity():
    """calculate() has no paper/live argument; same inputs -> identical result both calls."""
    s = _make_sizer(enabled=False, flat_value_rs=5000.0)
    a = s.calculate("X", "BUY", 1000.0, 985.0, "INTRADAY", "HIGH")   # "paper" caller
    b = s.calculate("X", "BUY", 1000.0, 985.0, "INTRADAY", "HIGH")   # "live" caller
    assert a.qty == b.qty == 5


# ── Pydantic validator: OFF requires a positive flat_value_rs ─────────────────

def _ps_kwargs(**over):
    base = dict(
        risk_per_trade_pct=0.01, max_concentration_pct=0.10, min_qty_threshold=1,
        lot_skew_rejection_threshold=0.25, min_tick_size=0.05, max_single_order_qty=10000,
        max_position_value_pct=0.40,
        tier_multipliers=PositionSizingTierConfig(HIGH=1.0, MEDIUM=0.7, LOW=0.5),
        # 22-Aug-2026 (fix item 1): the three delivery keys are REQUIRED by the schema.
        # This file is about the Diary #4 tier switch, so they are supplied at their
        # shipped values purely to make the model constructible.
        delivery_risk_per_trade_pct=0.01,
        delivery_max_concentration_pct=0.10,
        delivery_max_position_value_pct=0.40,
    )
    base.update(over)
    return base


@pytest.mark.parametrize("flat", [None, 0.0, -5000.0])
def test_pydantic_rejects_off_without_positive_flat(flat):
    with pytest.raises(ValidationError):
        PositionSizingConfig(**_ps_kwargs(enabled=False, flat_value_rs=flat))


def test_pydantic_accepts_on_with_null_flat_value():
    cfg = PositionSizingConfig(**_ps_kwargs(enabled=True))
    assert cfg.enabled is True and cfg.flat_value_rs is None


def test_pydantic_accepts_off_with_positive_flat():
    cfg = PositionSizingConfig(**_ps_kwargs(enabled=False, flat_value_rs=5000.0))
    assert cfg.enabled is False and cfg.flat_value_rs == 5000.0


def test_sizer_init_rejects_off_without_flat():
    with pytest.raises(ValueError):
        _make_sizer(enabled=False, flat_value_rs=None)


# ── End-to-end: breakdown is persisted onto the trades row (v34) ─────────────

def test_create_trade_persists_sizing_breakdown(tmp_path):
    import logging
    from core.state_store import StateStore
    from orders.order_manager import OrderManager

    schema = Path(__file__).parents[2] / "core" / "schema.sql"
    store = StateStore(tmp_path / "t.db", schema)
    om = OrderManager(state_store=store, logger=logging.getLogger("t"))
    now = "2026-06-22T09:30:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals(signal_id,symbol,scanner,strategy,triggered_at,"
            "received_at,expires_at,status,fingerprint,fingerprint_date) "
            "VALUES('s1','X','sc','st',?,?,?,'TRADED','fp','2026-06-22')", (now, now, now))

    res = _make_sizer(enabled=False, flat_value_rs=5000.0).calculate(
        "X", "BUY", 1000.0, 985.0, "INTRADAY", "MEDIUM")
    tid = om.create_trade(
        signal_id="s1", symbol="X", direction="LONG", strategy="st", sector=None,
        qty=res.qty, entry_target_price=1000.0, sl_initial=985.0, tgt_initial=1030.0,
        order_protocol="LIMIT_TRIPLE", margin_reserved=1000.0, risk_amount=75.0,
        sizing_breakdown=res.breakdown)

    row = store.fetch_one(f"SELECT * FROM trades WHERE trade_id='{tid}'")
    assert row["tier_multiplier_mode"] == "OFF_FLAT"
    assert row["flat_value_rs_used"] == 5000.0
    assert row["qty_by_flat"] == 5
    assert row["binding_constraint"] == "flat"
    assert row["actual_position_value_rs"] == 5000.0
    assert row["tier_weight_applied"] is None
    store.close()


# ── Phase 5: mode surfaced in pre-flight config display + Cron Officer EOD ────

def _write_ps(tmp_path, body):
    (tmp_path / "system_config.yaml").write_text("position_sizing:\n" + body, encoding="utf-8")


def test_preflight_tier_mode_check_on(tmp_path):
    from scripts.preflight.base import CheckContext
    from scripts.preflight.checks.config_integrity import TierMultiplierModeCheck
    _write_ps(tmp_path, "  enabled: true\n  tier_multipliers:\n    HIGH: 1.0\n    MEDIUM: 0.7\n    LOW: 0.5\n")
    r = TierMultiplierModeCheck().run(CheckContext(config_dir=tmp_path, db_path=tmp_path / "x.db"))
    assert "ON" in r.detail and "1.0/0.7/0.5" in r.detail


def test_preflight_tier_mode_check_off(tmp_path):
    from scripts.preflight.base import CheckContext
    from scripts.preflight.checks.config_integrity import TierMultiplierModeCheck
    _write_ps(tmp_path, "  enabled: false\n  flat_value_rs: 5000\n")
    r = TierMultiplierModeCheck().run(CheckContext(config_dir=tmp_path, db_path=tmp_path / "x.db"))
    assert "OFF" in r.detail and "5000" in r.detail


def test_cron_officer_eod_tier_mode_line(tmp_path):
    from scripts.cron_officer import _tier_mode_line
    _write_ps(tmp_path, "  enabled: true\n")
    assert "ON" in _tier_mode_line(tmp_path)
    _write_ps(tmp_path, "  enabled: false\n  flat_value_rs: 5000\n")
    line = _tier_mode_line(tmp_path)
    assert "OFF" in line and "5000" in line
