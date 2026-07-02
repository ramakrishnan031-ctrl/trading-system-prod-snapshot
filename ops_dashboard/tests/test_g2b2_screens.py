"""G2b-2 — M7-M21 screens: endpoint behavior, logs hardening (V2/V5), drift
banner (V4), tower scorecard integration, nav completeness (V6)."""
from __future__ import annotations

import copy
import json
import os

from backend.readers import log_reader
from backend.services import freshness, strategy_tower


# ── M7 Risk ──
def test_risk_api(client):
    d = client.get("/api/risk").get_json()
    assert d["daily_loss"]["used"] == 450.0
    assert d["daily_loss"]["limit"] == 3000.0
    assert "W10" in d["daily_loss"]["source_note"]
    assert d["consecutive_losses"] == {"used": 3, "limit": 5}
    assert d["kill_switch"]["state"] == "INACTIVE" and d["kill_switch"]["halted"] is False
    assert d["open_positions"] == {"used": 4, "limit": 5}
    assert d["open_risk_amount"] == 400.0          # 4 open × 100


# ── M8 Capital ──
def test_capital_api(client):
    d = client.get("/api/capital").get_json()
    assert d["opening_capital"] == 100000.0
    assert d["buckets"]["intraday_allocated"] == 70000.0
    assert d["used"] == 42000.0 and d["pending"] == 3000.0
    assert d["remaining"] == 28000.0 and d["deployed_pct"] == 42.0
    ledger = d["ledger"]
    assert len(ledger) == 7                        # 2 INIT + 5 RELEASE_USED
    assert ledger[0]["ledger_id"] > ledger[-1]["ledger_id"]   # newest first
    assert {"entry_type", "pnl_delta", "balance_after"} <= set(ledger[0])


# ── M9 Exposure ──
def test_exposure_api(client):
    d = client.get("/api/exposure").get_json()
    assert d["gross_exposure"] == 40000.0          # 4 open × 10000
    assert d["per_symbol"][0] == {"symbol": "AAA", "value": 40000.0, "positions": 4}
    strat = {r["strategy"]: r for r in d["per_strategy"]}
    assert strat["gap_fade_long"]["positions"] == 2
    assert d["margin_used"] == 42000.0


# ── M10 P&L (equity curve from the ledger sequence) ──
def test_pnl_api(client):
    d = client.get("/api/pnl").get_json()
    s = d["summary"]
    assert s["net"] == -25.0 and s["closed"] == 4  # −100−50−75+200
    assert s["charges"] == 20.0                    # gross(−5) − net(−25)
    assert {r["strategy"] for r in s["per_strategy"]} == {"gap_fade_long", "vwap_bounce_long"}
    curve = d["equity_curve"]
    assert [p["cum_pnl"] for p in curve] == [200.0, 100.0, 50.0, -25.0, -250.0]
    assert "single-mode" in d["mode_note"]


# ── M11 Services ──
def test_services_api(client):
    d = client.get("/api/services").get_json()
    assert len(d["units"]) == 6
    assert d["trader"]["trader_alive"] is False    # PC: :8080 unreachable
    cron = {c["job"]: c for c in d["cron"]}
    assert "retired_job" not in cron               # enabled:false excluded
    assert cron["eod_verify"]["status"] == "SUCCESS"
    assert cron["silent_job"]["status"] == "PENDING"   # monitored, no heartbeat
    assert d["control_tower_findings"][0]["severity"] == "HIGH"


# ── M12 VM ──
def test_vm_api(client):
    d = client.get("/api/vm").get_json()
    assert d["cpu_ram_history"]["collected"] is False
    assert "psutil" in d["cpu_ram_history"]["note"]
    assert "disk_root" in d["live"]                # shutil works on every platform
    assert isinstance(d["disk_history"], list)     # empty analytics fixture → []


# ── M13 Logs (V2 traversal + V5 tail perf) ──
def test_logs_list_and_tail(client, today):
    d = client.get("/api/logs").get_json()
    names = {f["name"] for f in d["files"]}
    assert f"system_{today}.log" in names and f"debug_{today}.log" in names

    d = client.get(f"/api/logs?file=system_{today}.log&n=50").get_json()
    assert d["count"] == 10 and d["rows"][0]["json"] is True
    d = client.get(f"/api/logs?file=system_{today}.log&level=ERROR").get_json()
    assert d["count"] == 1 and d["rows"][0]["level"] == "ERROR"
    d = client.get(f"/api/logs?file=system_{today}.log&id=trd_c1").get_json()
    assert d["count"] == 1 and d["rows"][0]["trade_id"] == "trd_c1"
    d = client.get(f"/api/logs?file=debug_{today}.log").get_json()
    assert d["count"] == 2 and d["rows"][0]["json"] is False


def test_logs_traversal_forbidden(client):
    for attempt in ("../secret.txt", "..%2Fsecret", "sub/inner.log",
                    "..\\..\\windows\\win.ini", "/etc/passwd"):
        r = client.get(f"/api/logs?file={attempt}")
        assert r.status_code == 403, attempt
    assert client.get("/api/logs?file=nope.log").status_code == 404


def test_tail_perf_10mb_not_full_read(gui_config):
    """V5: tail-2000 of a >10MB file must NOT read the whole file."""
    big = os.path.join(gui_config["paths"]["logs_dir"], "big.log")
    line = json.dumps({"ts": "2026-07-03T10:00:00", "level": "INFO",
                       "logger": "x", "msg": "y" * 60}) + "\n"
    n_lines = (11 * 1024 * 1024) // len(line) + 1
    with open(big, "w", encoding="utf-8") as fh:
        fh.write(line * n_lines)
    size = os.path.getsize(big)
    assert size > 10 * 1024 * 1024
    out = log_reader.tail_lines(big, 2000)
    assert len(out["lines"]) == 2000
    assert out["bytes_read"] < size            # never a full-file read
    assert out["bytes_read"] < 2 * 1024 * 1024  # block-tail stays local to the end


# ── M14 Audit ──
def test_audit_api(client):
    d = client.get("/api/audit").get_json()
    assert d["total"] > 0
    assert set(d["sources"]) == {"system_events", "reconciliation_log", "webhook_audit",
                                 "eod_verification", "preflight", "control_tower"}
    tss = [r["ts"] for r in d["rows"]]
    assert tss == sorted(tss, reverse=True)     # newest first
    d = client.get("/api/audit?table=reconciliation_log").get_json()
    assert all(r["source"] == "reconciliation_log" for r in d["rows"]) and d["total"] == 1
    d = client.get("/api/audit?severity=CRITICAL").get_json()
    assert all(r["severity"] == "CRITICAL" for r in d["rows"])
    d = client.get("/api/audit?page=2&size=5").get_json()
    assert d["page"] == 2 and len(d["rows"]) <= 5


# ── M15 Alerts ──
def test_alerts_api(client):
    d = client.get("/api/alerts").get_json()
    assert len(d["alerts"]) == 2
    assert d["alerts"][0]["severity"] == "INFO"        # newest first (15:10 > 12:00)
    assert len(d["failed_alerts_tail"]) == 1
    assert len(d["sentinels"]) == 1
    assert d["sentinels"][0]["name"].startswith("critical_alert_")
    assert "OUTBOUND-ONLY" in d["note"]


# ── M16 Slippage (breach model) ──
def test_slippage_api(client):
    d = client.get("/api/slippage").get_json()
    assert d["count"] == 2
    by_result = {r["trade_result"]: r for r in d["rows"]}
    assert by_result["LOSS"]["breach"] is True          # 3.0 > min(0.22×10, 5) = 2.2
    assert by_result["LOSS"]["tolerance_rs"] == 2.2
    assert by_result["WIN"]["breach"] is False          # 0.5 < 2.2
    agg = d["per_strategy"][0]
    assert agg["strategy"] == "gap_fade_long" and agg["breaches"] == 1
    assert d["worst"][0]["entry_slippage_rs"] == 3.0


# ── M17 Execution ──
def test_execution_api(client):
    d = client.get("/api/execution").get_json()
    lat = d["latency"]["order_to_fill_ms"]
    assert lat["n"] == 8
    buckets = {b["bucket"]: b["count"] for b in lat["buckets"]}
    assert buckets["500-1000 ms"] == 8 and buckets["250-500 ms"] == 0   # 850/900 ms seeds
    s2o = {b["bucket"]: b["count"] for b in d["latency"]["signal_to_order_ms"]["buckets"]}
    assert s2o["100-250 ms"] == 8                                        # 120/150 ms seeds
    assert len(d["execution_log"]) == 2
    assert len(d["reconciliation_actions"]) == 1


# ── M18 Statistics ──
def test_statistics_api(client):
    d = client.get("/api/statistics").get_json()
    per_dir = {r["direction"]: r for r in d["per_direction"]}
    assert set(per_dir) == {"LONG", "SHORT"}            # permanent split
    lng = per_dir["LONG"]
    assert lng["closed"] == 4 and lng["wins"] == 1 and lng["losses"] == 3
    assert lng["win_rate"] == 25.0 and lng["net"] == -25.0
    assert per_dir["SHORT"]["closed"] == 0              # honest zeros, not fabricated
    exc = {t["trade_id"]: t for t in d["trades_with_excursions"]}
    assert exc["trd_c1"]["mfe_pct"] == 1.2 and exc["trd_c1"]["mae_pct"] == -0.8
    assert exc["trd_c2"]["mfe_pct"] is None             # not reconstructed → blank
    assert d["innings"] == {"total": 2, "real": 1}


# ── M19 Reports (download gated OFF) ──
def test_reports_api(client, today):
    d = client.get("/api/reports").get_json()
    assert d["count"] == 2
    assert d["download_enabled"] is False
    assert "Q3" in d["download_note"]
    assert "NO retention" in d["retention_note"]
    r = client.get(f"/api/reports/download?file=daily_report_{today}.xlsx")
    assert r.status_code == 403                          # Q3 pending → hard-gated


# ── M20 Config view (V4 drift banner) ──
def test_config_api_groups_and_drift(client):
    d = client.get("/api/config").get_json()
    assert [g["name"] for g in d["groups"]] == ["System", "Risk", "Capital", "Strategies",
                                                "Scanners", "Execution", "Smart Target",
                                                "Slippage"]
    groups = {g["name"]: g["data"] for g in d["groups"]}
    assert groups["Risk"]["risk"]["max_daily_trades"] == 10
    assert groups["Smart Target"]["smart_tgt"]["trigger_pct"] == 0.005
    assert groups["Slippage"]["slippage_control"]["max_slippage_fraction"] == 0.22
    assert "slippage_control" not in groups["Execution"].get("entry_gate", {})
    assert "strategies" in groups["Strategies"] and "W0.1" in groups["Strategies"]["_note"]
    # header
    assert d["header"]["config_hash"] == "deadbeef" * 2
    assert d["header"]["last_change_date"] == d["today"]   # hash changed vs yesterday
    # V4: drift banner active, changed key = risk → group Risk
    assert d["drift"]["active"] is True
    assert {"key": "risk", "group": "Risk"} in d["drift"]["changed"]


# ── Tower integration: scorecard + silence + funnel on the seeded strategies ──
def _tower_at(gui_config, today, hhmm, all_weekdays=True):
    cfg = copy.deepcopy(gui_config)
    if all_weekdays:
        cfg["market_clock"]["active_weekdays"] = [0, 1, 2, 3, 4, 5, 6]
    now = freshness.parse_ist(f"{today}T{hhmm}:00+05:30")
    return strategy_tower.build_strategy_tower(cfg, today, now)


def test_tower_scorecard_integration(gui_config, today):
    t = _tower_at(gui_config, today, "10:40")       # in entry window, activity expected
    by = {r["basic"]["name"]: r for r in t["rows"]}
    # silent ENABLED strategy while activity expected → RED with the silent reason
    fp = by["first_pullback_long"]
    assert fp["scorecard"]["badge"] == "RED"
    assert any("silent" in r for r in fp["scorecard"]["reasons"])
    assert fp["silence"]["color"] == "RED" and fp["silence"]["age_min"] is None
    # all-losing strategy with a FAILED order → YELLOW (failed order rule)
    vw = by["vwap_bounce_long"]
    assert vw["scorecard"]["badge"] == "YELLOW"
    assert any("failed order" in r for r in vw["scorecard"]["reasons"])
    # healthy strategy (fresh signal 5m ago at 10:40) → GREEN
    assert by["gap_fade_long"]["scorecard"]["badge"] == "GREEN"
    assert by["gap_fade_long"]["silence"]["color"] == "GREEN"
    # disabled → GRAY, never RED
    assert by["gap_fade_short"]["scorecard"]["badge"] == "GRAY"


def test_tower_silence_outside_window_plain(gui_config, today):
    t = _tower_at(gui_config, today, "20:00")       # after close → silence not alarmed
    by = {r["basic"]["name"]: r for r in t["rows"]}
    assert by["first_pullback_long"]["silence"]["color"] is None
    assert by["first_pullback_long"]["scorecard"]["badge"] != "RED"


def test_tower_funnel_and_capital_view(gui_config, today):
    t = _tower_at(gui_config, today, "10:40")
    gap = {r["basic"]["name"]: r for r in t["rows"]}["gap_fade_long"]
    assert gap["funnel"] == {"received": 70, "accepted": 8, "orders_created": 7,
                             "filled": 6, "trades_closed": 2}
    assert gap["failures"] == {"risk_rej": 3, "capital_rej": 0, "order_rej": 0,
                               "duplicate": 10, "expired": 1}
    cv = gap["capital_view"]
    assert cv["allocation_configured"] is None and cv["allocation_basis"] == "global bucket"
    assert cv["positions_used"] == 2 and cv["positions_configured"] == 3


# ── V6: every nav page routes to a real screen ──
_ALL_PAGES = ("/", "/strategies", "/signals", "/orders", "/positions", "/holdings",
              "/capacity", "/risk", "/capital", "/exposure", "/pnl", "/services",
              "/vm", "/logs", "/audit", "/alerts", "/slippage", "/execution",
              "/statistics", "/reports", "/config", "/controls")


def test_all_nav_pages_render(client):
    for page in _ALL_PAGES:
        r = client.get(page)
        assert r.status_code == 200, page


def test_all_new_apis_require_auth(app):
    anon = app.test_client()
    for ep in ("/api/risk", "/api/capital", "/api/exposure", "/api/pnl",
               "/api/services", "/api/vm", "/api/logs", "/api/audit", "/api/alerts",
               "/api/slippage", "/api/execution", "/api/statistics", "/api/reports",
               "/api/config"):
        assert anon.get(ep).status_code == 401, ep
