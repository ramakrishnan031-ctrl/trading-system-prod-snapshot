"""Strategy Control Tower — groups, rankings (V3 hand-check), attribution (V5),
silent strategy, and the 15-second UAT checklist."""
from __future__ import annotations

from backend.services import strategy_tower as st


def _tower(gui_config, today):
    return st.build_strategy_tower(gui_config, today)


def _row(tower, name):
    for r in tower["rows"]:
        if r["basic"]["name"] == name:
            return r
    raise AssertionError(f"strategy {name} missing from tower")


def test_counts(gui_config, today):
    t = _tower(gui_config, today)
    assert t["count"] == 5            # 5 configured (incl. silent + disabled)
    assert t["enabled_count"] == 4    # gap_fade_short disabled


# ── SIGNALS group + attribution (R1/R2) ──
def test_signals_group_exact_attribution_n_to_1(gui_config, today):
    r = _row(_tower(gui_config, today), "gap_fade_long")
    s = r["signals"]
    # webhook level: gap_fade_long(58) + gap_fade_long_alt(12) — N:1 stays EXACT
    assert s["received"] == 70
    assert s["webhook_accepted"] == 60
    assert r["basic"]["scanners"] == ["gap_fade_long", "gap_fade_long_alt"]
    # stored level
    assert s["stored"] == 22
    assert s["accepted"] == 8
    assert s["rejected"] == 3
    assert s["duplicated"] == 10
    assert s["expired"] == 1
    assert s["attribution"] == "exact"


def test_scanner_shared_label_for_unmapped(gui_config, today):
    """V5: the unmapped scanner MUST surface as 'scanner-level (shared)'."""
    t = _tower(gui_config, today)
    assert len(t["scanner_level"]) == 1
    row = t["scanner_level"][0]
    assert row["scanner"] == "momentum_combo"
    assert row["label"] == "scanner-level (shared)"
    assert row["received"] == 7 and row["accepted"] == 5 and row["rejected"] == 2


# ── PROCESSING group (R5) ──
def test_processing_group(gui_config, today):
    t = _tower(gui_config, today)
    gap = _row(t, "gap_fade_long")["processing"]
    assert gap == {"created": 7, "submitted": 7, "filled": 6, "rejected": 0, "cancelled": 1}
    vwap = _row(t, "vwap_bounce_long")["processing"]
    assert vwap == {"created": 4, "submitted": 4, "filled": 3, "rejected": 1, "cancelled": 0}


# ── TRADING + PERFORMANCE (R4 ROI) ──
def test_trading_and_performance(gui_config, today):
    r = _row(_tower(gui_config, today), "gap_fade_long")
    tr, pf = r["trading"], r["performance"]
    assert tr["open"] == 2 and tr["closed"] == 2
    assert tr["wins"] == 1 and tr["losses"] == 1 and tr["win_rate"] == 50.0
    assert tr["avg_win"] == 200.0 and tr["avg_loss"] == -100.0
    assert pf["net_pnl"] == 100.0
    assert pf["gross_pnl"] == 110.0 and pf["charges"] == 10.0
    assert pf["capital_used_today"] == 20000.0      # 4 trades × 5000 margin
    assert pf["roi_pct"] == 0.5                     # R4: 100 / 20000
    assert pf["best_trade"] == 200.0 and pf["worst_trade"] == -100.0
    assert pf["expectancy"] == 50.0                 # .5×200 − .5×100


# ── RISK group ──
def test_risk_group(gui_config, today):
    r = _row(_tower(gui_config, today), "gap_fade_long")["risk"]
    assert r["capital_used"] == 10000.0             # 2 open × 5000
    assert r["capital_remaining"] == 60000.0        # 0.70×100000 − 10000
    assert r["capital_cap_basis"] == "global_intraday_bucket"
    assert r["active_positions"] == 2 and r["max_concurrent"] == 3
    assert r["position_capacity_remaining"] == 1
    assert r["global_max_open_positions"] == 5


# ── HEALTH group (R6) ──
def test_health_group(gui_config, today):
    t = _tower(gui_config, today)
    gap = _row(t, "gap_fade_long")["health"]
    assert gap["last_signal"] is not None
    assert gap["last_successful_trade"].endswith("14:50:00+05:30")
    assert gap["last_failure"].endswith("15:10:00+05:30")      # losing close (no FAILED order)
    vwap = _row(t, "vwap_bounce_long")["health"]
    assert vwap["last_failed_order"].endswith("12:00:00+05:30")
    assert vwap["last_losing_close"].endswith("15:05:00+05:30")
    assert vwap["last_failure"].endswith("15:05:00+05:30")     # max(order, losing close)


# ── Silent strategy ──
def test_silent_strategy(gui_config, today):
    r = _row(_tower(gui_config, today), "first_pullback_long")
    assert r["basic"]["enabled"] is True
    assert r["signals"]["received"] == 0 and r["signals"]["stored"] == 0
    assert r["processing"]["created"] == 0
    assert r["trading"]["open"] == 0 and r["trading"]["closed"] == 0
    assert r["health"]["last_signal"] is None
    assert r["health"]["last_signal_staleness"] == "NONE"


# ── Rankings (V3 hand-check with the seeded numbers) ──
def test_rankings_exact(gui_config, today):
    rk = _tower(gui_config, today)["rankings"]
    assert rk["net_pnl"] == ["gap_fade_long", "first_pullback_long", "gap_fade_short",
                             "range_breakout_long", "vwap_bounce_long"]
    assert rk["win_rate"] == ["gap_fade_long", "vwap_bounce_long", "first_pullback_long",
                              "gap_fade_short", "range_breakout_long"]
    assert rk["expectancy"] == ["gap_fade_long", "vwap_bounce_long", "first_pullback_long",
                                "gap_fade_short", "range_breakout_long"]
    assert rk["success_rate"] == ["range_breakout_long", "gap_fade_long", "vwap_bounce_long",
                                  "first_pullback_long", "gap_fade_short"]


def test_default_rank_badge(gui_config, today):
    t = _tower(gui_config, today)
    assert _row(t, "gap_fade_long")["basic"]["rank"] == 1     # net P&L view
    assert _row(t, "vwap_bounce_long")["basic"]["rank"] == 5
    assert [r["basic"]["rank"] for r in t["rows"]] == [1, 2, 3, 4, 5]


def test_strategy_detail(gui_config, today):
    d = st.strategy_detail(gui_config, "gap_fade_long", today)
    assert d is not None
    assert d["strategy"]["basic"]["name"] == "gap_fade_long"
    assert d["rankings"]["net_pnl"] == 1 and d["rankings"]["success_rate"] == 2
    assert st.strategy_detail(gui_config, "no_such_strategy", today) is None


# ── 15-SECOND GOAL — UAT checklist: every question answerable from tower data ──
def test_uat_15_second_goal(gui_config, today):
    t = _tower(gui_config, today)
    by = {r["basic"]["name"]: r for r in t["rows"]}
    # active / inactive?
    assert by["gap_fade_long"]["basic"]["enabled"] is True
    assert by["gap_fade_short"]["basic"]["enabled"] is False
    # receiving signals / silent?
    assert by["gap_fade_long"]["signals"]["received"] > 0
    assert by["first_pullback_long"]["signals"]["received"] == 0
    # producing trades?
    assert by["gap_fade_long"]["trading"]["closed"] + by["gap_fade_long"]["trading"]["open"] > 0
    # failing? (order rejections / losing closes visible)
    assert by["vwap_bounce_long"]["processing"]["rejected"] == 1
    assert by["vwap_bounce_long"]["health"]["last_failure"] is not None
    # best?
    assert t["rankings"]["net_pnl"][0] == "gap_fade_long"
    # capital-consuming?
    assert by["gap_fade_long"]["risk"]["capital_used"] > 0
    # loss-causing?
    assert by["vwap_bounce_long"]["performance"]["net_pnl"] < 0
