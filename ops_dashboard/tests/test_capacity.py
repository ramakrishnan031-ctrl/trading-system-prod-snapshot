"""capacity — limit/used/remaining rows (STEP-0 #1-#8) against the fixture."""
from __future__ import annotations

from backend.services import capacity as cap


def _rows(gui_config, today):
    out = cap.build_capacity(gui_config, today, trader_health={"trader_alive": False, "health": None})
    return {r["key"]: r for r in out["rows"]}, out


def test_row_count_and_source(gui_config, today):
    rows, out = _rows(gui_config, today)
    assert len(out["rows"]) == 8
    assert out["config_source"] == "config_snapshot"
    assert out["opening_capital"] == 100000.0


def test_daily_trades(gui_config, today):
    r = _rows(gui_config, today)[0]["max_daily_trades"]
    assert r["used"] == 8 and r["limit"] == 10 and r["remaining"] == 2
    assert r["status"] == "WARNING"   # 8 >= 0.8*10


def test_open_positions(gui_config, today):
    r = _rows(gui_config, today)[0]["max_open_positions"]
    assert r["used"] == 4 and r["limit"] == 5 and r["status"] == "WARNING"


def test_daily_loss(gui_config, today):
    r = _rows(gui_config, today)[0]["daily_loss_limit"]
    assert r["used"] == 450.0 and r["limit"] == 3000.0   # 0.03 * 100000
    assert r["unit"] == "rs" and r["status"] == "OK"


def test_intraday_capital(gui_config, today):
    r = _rows(gui_config, today)[0]["intraday_capital"]
    assert r["used"] == 42000.0 and r["limit"] == 70000.0   # 0.70 * 100000
    assert r["pct"] == 60.0 and r["status"] == "OK"
    assert r["pending"] == 3000.0


def test_consecutive_losses(gui_config, today):
    r = _rows(gui_config, today)[0]["max_consecutive_losses"]
    assert r["used"] == 3 and r["limit"] == 5 and r["status"] == "OK"


def test_signal_queue_unavailable_when_trader_down(gui_config, today):
    r = _rows(gui_config, today)[0]["signal_queue"]
    assert r["status"] == "UNAVAILABLE" and r["limit"] == 300 and r["used"] is None


def test_delivery_rows_inert(gui_config, today):
    rows = _rows(gui_config, today)[0]
    for key in ("max_open_delivery_positions", "max_daily_delivery_trades"):
        assert rows[key]["inert"] is True
        assert rows[key]["status"] == "INERT"
        assert rows[key]["used"] == 0


# ── G2b-1: grouped capacity screen ──
def test_groups_present(gui_config, today):
    _, out = _rows(gui_config, today)
    names = [g["name"] for g in out["groups"]]
    assert names == ["Orders", "Positions", "Capital", "Risk", "Strategy", "System-guards"]


def _group(gui_config, today, name):
    _, out = _rows(gui_config, today)
    for g in out["groups"]:
        if g["name"] == name:
            return {r["key"]: r for r in g["rows"]}
    raise AssertionError(name)


def test_orders_group_rows(gui_config, today):
    g = _group(gui_config, today, "Orders")
    assert g["max_daily_trades"]["used"] == 8
    burst = g["entry_burst"]
    assert burst["limit"] == 3 and isinstance(burst["used"], int)
    qty = g["max_single_order_qty"]
    assert qty["used"] == 10 and qty["limit"] == 10000
    assert g["min_gap_between_entries"]["type"] == "config_only"
    assert g["entry_window"]["type"] == "window"
    assert g["entry_window"]["status"] in ("INSIDE", "OUTSIDE")


def test_capital_group_exposure_rows(gui_config, today):
    g = _group(gui_config, today, "Capital")
    conc = g["max_concentration"]
    # all 4 open positions are symbol AAA @ value 10000 → worst symbol = 40000
    assert conc["used"] == 40000.0 and conc["limit"] == 10000.0   # 0.10 × 100000
    assert conc["status"] == "BREACH"                              # honest: seed breaches it
    assert "AAA" in conc["note"]
    sect = g["max_sector_exposure"]
    assert sect["used"] == 40000.0 and sect["limit"] == 40000.0    # 0.40 × 100000
    posval = g["max_position_value"]
    assert posval["used"] == 10000.0 and posval["limit"] == 40000.0


def test_risk_group_config_rows(gui_config, today):
    g = _group(gui_config, today, "Risk")
    assert g["max_consecutive_losses"]["used"] == 3
    kill = g["kill_api_failure"]
    assert kill["type"] == "config_only"
    assert kill["configured"]["threshold"] == 3
    assert kill["kill_state"] == "INACTIVE"
    assert g["drift_thresholds"]["configured"]["soft_kill"] == 1000.0


def test_system_guards_group(gui_config, today):
    g = _group(gui_config, today, "System-guards")
    ip = g["per_ip_rate_limit"]
    assert ip["type"] == "config_only"
    assert ip["configured"]["burst"] == 60
    assert ip["rate_limited_today"] == 0          # D7 proxy: 429s today, never fabricated
    assert g["signal_queue"]["status"] == "UNAVAILABLE"
    assert g["slippage_budget"]["configured"]["max_fraction_of_sl"] == 0.22
    assert g["smart_tgt"]["configured"]["trigger_pct"] == 0.005


def test_strategy_group_pointer(gui_config, today):
    g = _group(gui_config, today, "Strategy")
    assert g["tier_multipliers"]["configured"]["tiers"]["MEDIUM"] == 0.70
    assert "Strategy Tower" in g["per_strategy_caps"]["configured"]["where"]
