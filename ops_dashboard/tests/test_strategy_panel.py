"""strategy_panel — per-strategy merge of config + today's trades."""
from __future__ import annotations

from backend.services import strategy_panel as sp


def _by_name(gui_config, today):
    panel = sp.build_strategy_panel(gui_config, today)
    return {r["name"]: r for r in panel["rows"]}, panel


def test_counts_and_enabled(gui_config, today):
    rows, panel = _by_name(gui_config, today)
    assert panel["count"] == 4          # 4 configured strategies
    assert panel["enabled_count"] == 3  # gap_fade_short disabled
    assert rows["gap_fade_short"]["enabled"] is False
    assert rows["gap_fade_short"]["trades"] == 0


def test_gap_fade_long(gui_config, today):
    r = _by_name(gui_config, today)[0]["gap_fade_long"]
    assert r["trades"] == 4 and r["wins"] == 1 and r["losses"] == 1
    assert r["net_pnl"] == 100.0 and r["win_rate"] == 50.0
    assert r["open_count"] == 2 and r["max_concurrent"] == 3
    assert r["concurrent_remaining"] == 1


def test_vwap_bounce_long(gui_config, today):
    r = _by_name(gui_config, today)[0]["vwap_bounce_long"]
    assert r["trades"] == 3 and r["losses"] == 2 and r["net_pnl"] == -125.0
    assert r["win_rate"] == 0.0


def test_ranking(gui_config, today):
    _, panel = _by_name(gui_config, today)
    ranks = {r["name"]: r["rank"] for r in panel["rows"]}
    assert ranks["gap_fade_long"] == 1                 # highest net P&L
    assert ranks["vwap_bounce_long"] == len(panel["rows"])  # lowest net P&L → last
