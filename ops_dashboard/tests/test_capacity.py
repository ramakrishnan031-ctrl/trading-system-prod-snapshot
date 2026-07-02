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
