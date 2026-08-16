"""SCREEN 18 — LIVE ACTIVITY.  The Mission Control Wall.

The approved design (`gui/18. Live_Activity.png` + `.txt`) is binding for
STRUCTURE; the DATA is this system's.

⭐ THE CENTRAL PROPERTY: every tile on this wall is either a stored fact or the
NOT INSTRUMENTED state. The shared fixture seeds no same-day signal→trade chain
and no WARNING alert, so several tests below seed the chain they need — each one
CAN therefore go red.
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3

from backend.readers import db_reader
from backend.services import live_activity

from conftest import TODAY, _ts


def _read(*parts) -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, *parts), encoding="utf-8") as fh:
        return fh.read()


def _tpl() -> str:
    return _read("frontend", "templates", "live_activity.html")


def _css() -> str:
    return _read("frontend", "static", "style.css")


def _shipped(client) -> str:
    html = client.get("/live-activity").data.decode("utf-8")
    return re.sub(r"<!--.*?-->", " ", html, flags=re.S)


def _page_content(client) -> str:
    """This screen's VISIBLE markup — shared chrome and scripts removed."""
    body = _shipped(client)
    i = body.find('<div class="lav-page"')
    assert i > 0, "the live-activity page root is missing"
    body = body[i:]
    j = body.find("<script>")
    return body[:j] if j > 0 else body


def _s(client, qs=""):
    return client.get("/api/live-activity/screen" + qs).get_json()


def _conn(gui_config):
    return sqlite3.connect(gui_config["paths"]["main_db"])


def _seed_same_day_chain(gui_config, *, sid="s18_chain", symbol="RELIANCE",
                         strategy="vwap_bounce_long", trigger_price=2845.30,
                         filled=True):
    """A signal received TODAY that became a trade TODAY.

    ⭐ The shared fixture links its trades to YESTERDAY-dated signals, so the
    pipeline's Order and Fill stages are 0 there and any assertion about them
    would be vacuous. This seeds the chain the real system produces every day.
    """
    c = _conn(gui_config)
    tid = "trd_" + sid
    c.execute(
        "INSERT INTO signals(signal_id,symbol,scanner,strategy,received_at,"
        "status,trade_id,trigger_price) VALUES(?,?,?,?,?,?,?,?)",
        (sid, symbol, strategy, strategy, _ts("10:45:30"), "TRADED", tid,
         trigger_price))
    c.execute(
        "INSERT INTO trades(trade_id,signal_id,symbol,direction,strategy,status,"
        "qty_planned,qty_filled,entry_target_price,entry_actual_price,"
        "sl_initial,tgt_initial,margin_reserved,created_at,entry_time) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, sid, symbol, "LONG", strategy, "OPEN", 100, 100, 2845.30, 2845.45,
         2820.00, 2890.00, 5000.0, _ts("10:45:29"),
         _ts("10:45:28") if filled else None))
    c.execute(
        "INSERT INTO orders(order_id,trade_id,leg,transaction_type,order_type,"
        "product,variety,qty_requested,qty_filled,status,placed_at,filled_at,"
        "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ord_" + sid, tid, "ENTRY", "BUY", "LIMIT", "MIS", "regular", 100, 100,
         "COMPLETE", _ts("10:45:29"),
         _ts("10:45:28") if filled else None, _ts("10:45:29")))
    c.commit()
    c.close()
    return sid, tid


# ── the approved vocabularies ───────────────────────────────────────────────
def test_the_approved_vocabularies_are_exact():
    assert live_activity.CATEGORIES == (
        "Signal", "Order", "Position", "Trade", "Risk", "Capital",
        "System", "Alert")
    assert live_activity.STATUSES == ("Success", "Processing", "Failed", "Info")
    assert live_activity.PIPELINE_STAGES == (
        "Signal", "Validation", "Risk", "Capital", "Order", "Fill")
    assert live_activity.FEED_FILTERS == (
        "All", "Signals", "Orders", "Positions", "Trades", "System",
        "Alerts", "Reset")
    assert live_activity.SYSTEM_EVENT_TYPES == (
        "Service Restarted", "Broker Reconnected", "Database Warning",
        "Recovery Triggered")
    assert live_activity.POSITION_COLUMNS == (
        "Symbol", "Strategy", "Qty", "Entry Price", "LTP", "MTM (₹)",
        "MTM (%)", "SL", "TGT", "Status")


def test_the_traffic_light_is_exactly_the_four_approved_states(client):
    """Green = Success · Yellow = Processing · Red = Failed · Blue = Information.
    ⛔ No fifth state leaks in — a WARNING alert is not relabelled 'Processing',
    which would mean 'in flight' and is a different claim."""
    p = _s(client)
    seen = {r["status"] for r in p["records"]}
    assert seen <= set(live_activity.STATUSES), seen
    assert set(p["status_meaning"]) == set(live_activity.STATUSES)


def test_every_feed_row_carries_one_of_the_eight_approved_categories(client):
    p = _s(client)
    assert p["records"], "fixture produced no activity"
    for r in p["records"]:
        assert r["category"] in live_activity.CATEGORIES, r


# ── ⛔ the gaps, and they must never become values ──────────────────────────
def test_current_mtm_is_never_computed_or_guessed(client):
    """⭐ THE HEADLINE GAP OF THIS SCREEN. MTM needs a live price and this
    dashboard has none — /api/positions already declares ltp/mtm/unrealized/
    current_rr unavailable. ⛔ Realised P&L is a DIFFERENT quantity and must
    never be shown in the MTM card's place."""
    p = _s(client)
    assert p["kpi"]["current_mtm"] is None
    assert p["kpi"]["current_mtm_pct"] is None
    assert p["gaps"]["current_mtm"]["measured"] is False
    # the realised figure IS real and is kept, under its own name
    assert "realized_pnl_today" in p["kpi"]
    # ⭐ The KPI deck is data-driven (the Screen-15 pattern), so the card lives
    # in the KPIS array; the ACTIVE POSITIONS columns are static markup and the
    # NOT INSTRUMENTED state is asserted there, on the shipped page.
    assert 'label: "CURRENT MTM"' in _tpl()
    assert "NOT INSTRUMENTED" in _page_content(client)
    # ⛔ the card renders the state, never a number
    assert 'if (k === "current_mtm") return "NOT INSTRUMENTED";' in _tpl()


def test_ltp_and_mtm_columns_are_never_filled(client):
    p = _s(client)["active_positions"]
    assert p["unavailable"]["reason"] == "Pending Broker Source (G4)"
    assert set(p["unavailable"]["fields"]) == {"ltp", "mtm_rs", "mtm_pct"}
    assert p["rows"], "fixture produced no open positions"
    for r in p["rows"]:
        assert r["ltp"] is None and r["mtm_rs"] is None and r["mtm_pct"] is None


def test_the_open_positions_delta_is_not_instrumented(client):
    """⛔ The open set is a live trades.status read with no stored history.
    ⭐ Signals and Orders ARE dated rows, so their yesterday comparison is
    measured — the two cases must not be levelled to the same answer."""
    p = _s(client)
    k = p["kpi"]
    assert k["open_positions_delta"] is None
    assert p["gaps"]["open_positions_delta"]["measured"] is False
    assert isinstance(k["open_positions"], int)
    # the measurable ones carry a real comparison base
    assert isinstance(k["signals_yesterday"], int)
    assert isinstance(k["orders_yesterday"], int)
    assert k["compare_date"] < p["today"]


def test_no_all_systems_operational_health_claim_is_printed(client):
    """⛔ Nothing here measures the health of all services, and Screen 12 ruled
    that the engines are THREADS inside one systemd service. TRADING STATUS
    shows the kill switch and the market phase, and prints that basis."""
    p = _s(client)
    body = _page_content(client)
    assert "All Systems Operational" not in body
    assert "All Systems Operational" not in json.dumps(p)
    assert p["gaps"]["system_health_claim"]["measured"] is False
    assert p["kpi"]["trading_status"] in ("ACTIVE", "HALTED", "IDLE")
    assert "Kill switch" in p["kpi"]["trading_status_basis"]


def test_uninstrumented_system_events_show_no_count(client):
    """⛔ An approved example nothing writes is NOT zero — it is unmeasurable."""
    p = _s(client)
    missing = [t for t in p["system_event_types"]
               if t not in p["instrumented_system_event_types"]]
    assert set(missing) == {"Broker Reconnected", "Database Warning"}
    for t in missing:
        assert all(e["event"] != t for e in p["system_events"]["rows"])
    assert p["gaps"]["system_events"]["measured"] is False


def test_the_system_events_cap_is_never_silent(client):
    """⛔ NO SILENT CAP. The binding PNG draws four entries; the panel says how
    many exist, because a truncated list that says nothing reads as a quiet day."""
    s = _s(client)["system_events"]
    assert s["shown"] <= live_activity.SYSTEM_EVENTS_SHOWN == 4
    assert s["shown"] == len(s["rows"])
    assert s["total"] >= s["shown"]
    assert "sysEventsSub()" in _tpl()


def test_the_instrumented_system_event_list_matches_what_the_builder_emits():
    """⚠️ The declared list and the emitter must agree, or a type marked
    uninstrumented would carry rows on screen."""
    emitted = set(live_activity._SYSEVT_EVENT.values())
    assert emitted <= set(live_activity.INSTRUMENTED_SYSTEM_EVENT_TYPES), (
        emitted - set(live_activity.INSTRUMENTED_SYSTEM_EVENT_TYPES))
    assert emitted <= set(live_activity.SYSTEM_EVENT_TYPES)


def test_no_risk_check_passed_event_is_manufactured(client):
    """⛔ A risk check that PASSES writes nothing. Only rejections are stored."""
    p = _s(client)
    assert p["gaps"]["risk_check_passed"]["measured"] is False
    risk = [r for r in p["records"] if r["category"] == "Risk"]
    assert risk, "fixture seeds three REJECTED_DAILY_LOSS signals"
    for r in risk:
        assert "Passed" not in r["event"]
        assert r["status"] in ("Failed",)


def test_every_declared_gap_carries_a_real_reason(client):
    gaps = _s(client)["gaps"]
    for key in ("current_mtm", "open_positions_delta", "system_health_claim",
                "system_events", "risk_check_passed"):
        assert gaps[key]["measured"] is False
        assert gaps[key]["value"] is None
        assert len(gaps[key]["reason"]) > 30, key


# ── ⛔ no Scanner anywhere ──────────────────────────────────────────────────
def test_there_is_no_scanner_field_anywhere(client):
    """⛔ Screen 18's binding design names STRATEGY. No column, no chip, no
    tile, no filter, no export column carries a scanner."""
    tpl = _tpl()
    cols = re.search(r"DEFAULT_COLS:\s*\[(.*?)\n    \],", tpl, re.S).group(1)
    chips = re.search(r"CHIPS:\s*\[(.*?)\n    \],", tpl, re.S).group(1)
    tiles = re.search(r"TILES:\s*\[(.*?)\n    \],", tpl, re.S).group(1)
    for block in (cols, chips, tiles):
        assert "scanner" not in block.lower()
    assert not any("Scanner" in h for h in live_activity.EXPORT_HEADER)
    assert not any("Scanner" in c for c in live_activity.POSITION_COLUMNS)
    for r in _s(client)["records"]:
        assert "scanner" not in {k.lower() for k in r}
    assert "STRATEGY ACTIVITY" in _page_content(client)


def test_symbol_comes_before_strategy_in_the_positions_table(client):
    """⭐ The Screens 13-15 convention, and the binding PNG draws it the same
    way. ⚠️ The FEED's fourth column is ONE approved heading — Strategy /
    Symbol — so the rule has nothing to reorder there and the heading is kept
    verbatim."""
    cols = live_activity.POSITION_COLUMNS
    assert cols.index("Symbol") < cols.index("Strategy")
    body = _page_content(client)
    i = body.index("ACTIVE POSITIONS")
    table = body[i:i + 2200]
    assert table.index(">Symbol<") < table.index(">Strategy<")
    # the feed's combined heading is preserved verbatim
    assert 'label: "Strategy / Symbol"' in _tpl()


def test_the_approved_six_feed_columns_in_the_approved_order():
    tpl = _tpl()
    cols = re.search(r"DEFAULT_COLS:\s*\[(.*?)\n    \],", tpl, re.S).group(1)
    assert re.findall(r'key:\s*"([a-z_]+)"', cols) == [
        "time", "category", "event", "strategy_symbol", "details", "status"]
    assert re.findall(r'label:\s*"([^"]+)"', cols) == [
        "Time", "Category", "Event", "Strategy / Symbol", "Details", "Status"]


# ── the pipeline: ONE base, and it narrows ─────────────────────────────────
def test_the_pipeline_has_the_six_approved_stages_in_order(client):
    p = _s(client)["pipeline"]
    assert [s["name"] for s in p["stages"]] == list(live_activity.PIPELINE_STAGES)
    assert p["names"] == list(live_activity.PIPELINE_STAGES)


def test_the_pipeline_narrows_and_states_its_one_base(gui_config, client):
    """⭐ A funnel whose stages come from different denominators cannot narrow
    honestly. Every stage here is counted off today's STORED signals, and the
    seeded same-day chain makes Order and Fill non-zero so the last two stages
    are exercised rather than vacuously 0."""
    _seed_same_day_chain(gui_config)
    p = _s(client)["pipeline"]
    counts = [s["count"] for s in p["stages"]]
    assert counts == sorted(counts, reverse=True), counts
    assert counts[0] > 0 and counts[-1] > 0, counts
    assert p["base"] == "stored signals received today"
    # the drops are named, so the narrowing can be checked arithmetically
    d = p["dropped"]
    assert counts[1] == counts[0] - d["duplicate"] - d["expired"]
    assert counts[2] == counts[1] - d["risk_rejected"]
    assert counts[3] == counts[2] - d["capital_rejected"]


def test_the_pipeline_never_mixes_in_the_webhook_denominator(gui_config, client):
    """⛔ `webhook_audit.received` counts pre-insert duplicates and is a
    DIFFERENT denominator from stored signals. The fixture seeds 100 webhook
    receives against 24 stored signals, so a reader that used the webhook count
    would be caught here."""
    funnel = db_reader.webhook_funnel(gui_config, TODAY)
    p = _s(client)["pipeline"]
    signal_stage = p["stages"][0]["count"]
    assert funnel["received"] != signal_stage
    assert signal_stage == db_reader.activity_day_counts(
        gui_config, TODAY)["signals"]


def test_exactly_one_stage_is_marked_active(gui_config, client):
    _seed_same_day_chain(gui_config)
    stages = _s(client)["pipeline"]["stages"]
    stamped = [s for s in stages if s["last"]]
    assert stamped
    assert sum(1 for s in stages if s["active"]) >= 1
    newest = max(s["last"] for s in stamped)
    for s in stages:
        assert s["active"] == bool(s["last"] and s["last"] == newest)


# ── the feed: real events, deterministic, filterable ───────────────────────
def test_the_feed_covers_every_approved_category(client):
    """⭐ Eight approved categories, and the fixture exercises them all — a
    category with no source would be a silent hole in the wall."""
    by = _s(client)["by_category"]
    for c in live_activity.CATEGORIES:
        assert c in by
    present = {c for c, n in by.items() if n}
    assert present == set(live_activity.CATEGORIES), (
        set(live_activity.CATEGORIES) - present)


def test_an_order_fill_event_is_only_emitted_when_a_fill_stamp_exists(client):
    """⛔ Never inferred from a COMPLETE status with no stamp — that would put
    an event on the wall at a time nothing recorded."""
    p = _s(client)
    fills = [r for r in p["records"] if r["event"] == "Order Filled"]
    assert fills, "fixture seeds 60 filled ENTRY orders"
    for r in fills:
        assert r["ts"] and r["status"] == "Success"
    # the 10 CANCELLED orders have no filled_at and therefore no fill event
    created = [r for r in p["records"] if r["event"] == "Order Created"]
    assert len(created) > len(fills)


def test_a_trade_status_follows_the_stored_pnl_not_the_exit_reason(client):
    """⭐ An SL that fires is the system working. The Red/Green split is the
    OUTCOME's sign, read from `trades.net_pnl`. ⛔ Not the exit reason."""
    p = _s(client)
    trades = [r for r in p["records"] if r["category"] == "Trade"]
    assert trades, "fixture closes four trades today"
    for r in trades:
        if r["net_pnl"] is None:
            assert r["status"] == "Info"
        elif float(r["net_pnl"]) < 0:
            assert r["status"] == "Failed", r
        else:
            assert r["status"] == "Success", r
    # both signs are present, so the branch is not vacuously one-sided
    assert {r["status"] for r in trades} >= {"Failed", "Success"}
    # and the approved event names appear
    assert {"SL Hit", "TGT Hit"} <= {r["event"] for r in trades}


def test_a_signal_price_is_the_real_trigger_price_or_an_em_dash(gui_config, client):
    """⭐ Details says "Price" and means `signals.trigger_price`. The shared
    fixture leaves it NULL, so BOTH paths are exercised: the seeded chain has a
    price and the pre-seeded signals do not. ⛔ A missing price is never 0.00."""
    sid, _ = _seed_same_day_chain(gui_config, trigger_price=2845.30)
    p = _s(client)
    seeded = next(r for r in p["records"] if r["ref_id"] == "SIG-" + sid)
    assert "Price: 2,845.30" in seeded["details"]
    others = [r for r in p["records"]
              if r["category"] == "Signal" and r["ref_id"] != "SIG-" + sid]
    assert others
    assert all("Price:" not in r["details"] for r in others)
    assert all("0.00" not in (r["details"] or "") for r in others)


def test_repeated_builds_neither_duplicate_nor_lose_events(client):
    """⚠️ This page polls every 5 seconds during market hours — a feed that
    reshuffled on refresh would be unreadable."""
    first = _s(client)["records"]
    for _ in range(3):
        again = _s(client)["records"]
        assert len(again) == len(first)
        assert [r["ref_id"] for r in again] == [r["ref_id"] for r in first]


def test_reference_ids_are_unique_and_name_their_source(client):
    rows = _s(client)["records"]
    ids = [r["ref_id"] for r in rows]
    assert len(set(ids)) == len(ids)
    assert {i.split("-")[0] for i in ids} <= {
        "SIG", "ORD", "POS", "TRD", "RISK", "CAP", "SYS", "CRON", "ALT"}


def test_equal_timestamps_order_deterministically(client):
    rows = _s(client)["records"]
    keyed = [(r["ts"], r["ref_id"]) for r in rows]
    assert keyed == sorted(keyed, reverse=True)


def test_the_feed_filter_narrows_the_feed_and_only_the_feed(client):
    """⭐ The approved design calls it FEED FILTERS. The KPI strip, the
    pipeline, the strategy table and the capital ring describe THE DAY, and each
    carries its own base — so narrowing the feed must not silently restate them."""
    wide = _s(client)
    narrow = _s(client, "?category=Trades")
    assert 0 < narrow["count"] < wide["count"]
    assert {r["category"] for r in narrow["records"]} == {"Trade"}
    assert narrow["total_events"] == wide["total_events"]
    assert narrow["kpi"] == wide["kpi"]
    assert narrow["pipeline"] == wide["pipeline"]
    assert narrow["capital"] == wide["capital"]


def test_the_filter_accepts_the_tile_label_and_the_category_name(client):
    a = _s(client, "?category=Signals")["count"]
    b = _s(client, "?category=Signal")["count"]
    assert a == b > 0
    assert _s(client, "?category=All")["count"] == _s(client)["count"]
    assert _s(client, "?category=Reset")["count"] == _s(client)["count"]


def test_category_is_the_only_filter_the_design_draws(client):
    """⛔ The approved design draws ONE filter — the nine toolbar chips and the
    eight FEED FILTERS tiles both select a CATEGORY. A status filter or a search
    box would be an invention, so neither the screen nor the export accepts one,
    and an unknown parameter is ignored rather than silently narrowing."""
    import inspect

    sig = inspect.signature(live_activity.build_live_activity)
    assert set(sig.parameters) == {"cfg", "category", "today"}
    assert set(_s(client)["active"]) == {"category"}
    # an unknown parameter changes nothing
    assert _s(client, "?status=Failed&q=zzz")["count"] == _s(client)["count"]


def test_the_toolbar_chips_and_the_tiles_share_one_state():
    """⛔ The design draws the filter twice; two independent states would let
    the wall show one thing and claim another."""
    tpl = _tpl()
    assert tpl.count("setCat(") >= 3
    assert 'cat === c.key ? \'rt-active\' : \'\'' in tpl
    assert "tileActive(t.key)" in tpl
    body = re.search(r"setCat\(key\)\s*\{(.*?)\n    \},", tpl, re.S).group(1)
    assert 'key === "Reset"' in body and "this.cat" in body


# ── the panels ─────────────────────────────────────────────────────────────
def test_strategy_activity_uses_the_same_bases_as_the_kpi_strip(gui_config, client):
    """⛔ `strategy_order_stats` counts ALL legs and `strategy_trade_stats`
    dates by created_at; using either here would make the table disagree with
    the card above it on the same page."""
    _seed_same_day_chain(gui_config)
    p = _s(client)
    sa = p["strategy_activity"]
    rows = sa["rows"]
    assert rows
    day = db_reader.activity_day_counts(gui_config, TODAY)
    assert sum(r["signals"] for r in rows) == day["signals"] == p["kpi"]["signals_today"]
    for r in rows:
        assert r["dot"] == ("on" if r["last_signal"] else "off")
    # ⭐ an order is attributed THROUGH its trade row; what cannot be attributed
    # is REPORTED, so the table plus the reported remainder equals the card.
    assert (sum(r["orders"] for r in rows) + sa["unattributed_orders"]
            == p["kpi"]["orders_today"])
    assert (sum(r["trades"] for r in rows) + sa["unattributed_trades"]
            == day["trades"])


def test_unattributable_orders_are_reported_not_dropped(gui_config, client):
    """🔴 CAUGHT IN TEST: the fixture seeds 70 ENTRY orders but only 8 have a
    trade row, so the strategy table could only ever total 8 while ORDERS TODAY
    said 70. ⛔ A column that quietly totals less than the card above it is the
    drift this screen exists to avoid — the remainder is counted and named."""
    sa = _s(client)["strategy_activity"]
    assert sa["unattributed_orders"] > 0
    assert "base" in sa and "ENTRY orders by placed_at" in sa["base"]
    assert "unattributedNote()" in _tpl()


def test_winners_and_losers_come_from_real_closes_and_never_borrow(client):
    p = _s(client)["winners_losers"]
    assert p["base"] == "trades closed today"
    assert p["last_profit"]["amount"] > 0
    assert p["last_loss"]["amount"] < 0
    assert p["best_trade"]["amount"] >= p["last_profit"]["amount"]
    assert p["worst_trade"]["amount"] <= p["last_loss"]["amount"]
    # ⛔ a card never takes a trade of the opposite sign
    assert p["last_loss"]["trade_id"] != p["last_profit"]["trade_id"]


def test_a_card_with_no_qualifying_close_is_none_not_zero(gui_config):
    """⛔ ₹0 would read as "a flat trade happened"; None reads as "none did"."""
    empty = live_activity._winners_losers([])
    for key in ("last_profit", "last_loss", "best_trade", "worst_trade"):
        assert empty[key] is None
    only_wins = live_activity._winners_losers(
        [{"net_pnl": 10.0, "symbol": "AAA", "exit_time": _ts("10:00:00"),
          "trade_id": "t1", "strategy": "s"}])
    assert only_wins["last_loss"] is None
    assert only_wins["last_profit"]["amount"] == 10.0


def test_the_alerts_banner_shows_only_stored_critical_and_warning(gui_config, client):
    """⛔ Nothing is promoted to critical by keyword, and an INFO alert is never
    shown as a warning to make the banner look busy. The fixture seeds one INFO
    and one CRITICAL; a WARNING is added here so both card kinds are exercised."""
    c = _conn(gui_config)
    c.execute("INSERT INTO telegram_alerts(sent_at,severity,title,status,attempts,"
              "source_module) VALUES(?,?,?,?,?,?)",
              (_ts("10:22:41"), "WARNING", "High Market Volatility", "SENT", 1,
               "risk_engine"))
    c.commit()
    c.close()
    b = _s(client)["alerts_banner"]
    kinds = {c_["kind"] for c_ in b["cards"]}
    assert kinds == {"CRITICAL ALERT", "WARNING"}
    assert b["critical"] == 1 and b["warnings"] == 1
    assert all(c_["severity"] in ("CRITICAL", "ERROR", "WARNING", "WARN")
               for c_ in b["cards"])
    # the INFO alert the fixture seeds is NOT in the banner
    assert all("SL_HIT AAA" not in c_["title"] for c_ in b["cards"])


def test_market_pulse_states_its_window_and_divides_by_it(client):
    p = _s(client)["market_pulse"]
    assert p["window_min"] == live_activity.PULSE_WINDOW_MIN
    assert [s["key"] for s in p["series"]] == ["signals", "orders", "trades"]
    assert [s["label"] for s in p["series"]] == [
        "Signals Per Minute", "Orders Per Minute", "Trades Per Minute"]
    for s in p["series"]:
        assert s["rate"] == round(s["count"] / float(p["window_min"]), 2)
        assert len(s["spark"]) == live_activity.PULSE_SPARK_MIN
        assert s["delta"] == round(s["rate"] - s["prev_rate"], 2)


def test_market_pulse_counts_real_minutes_in_the_window(gui_config):
    """⭐ A NON-VACUOUS pulse: the rate is time-relative, so it is exercised
    against a synthetic `now` rather than whatever o'clock the suite runs at.
    ⛔ Without this the panel would only ever be asserted at 0.00."""
    import datetime as _dt

    from backend.services import freshness
    c = _conn(gui_config)
    for i in range(5):
        c.execute("INSERT INTO signals(signal_id,symbol,scanner,strategy,"
                  "received_at,status) VALUES(?,?,?,?,?,?)",
                  ("s18_pulse_%d" % i, "AAA", "gap_fade_long", "gap_fade_long",
                   _ts("11:1%d:00" % i), "QUEUED"))
    c.commit()
    c.close()
    now = freshness.parse_ist(TODAY + "T11:30:00+05:30")
    out = live_activity._market_pulse(gui_config, TODAY, now)
    sig = next(s for s in out["series"] if s["key"] == "signals")
    assert sig["count"] >= 5
    assert sig["rate"] > 0
    assert sum(sig["spark"]) >= 5          # the 11:10-11:14 minutes are inside 30
    # ⛔ and a window with nothing in it stays a real zero
    later = live_activity._market_pulse(
        gui_config, TODAY, now + _dt.timedelta(hours=6))
    assert next(s for s in later["series"] if s["key"] == "signals")["count"] == 0


def test_active_positions_is_the_open_set_and_mirrors_the_screen06_status(client,
                                                                          gui_config):
    """⭐ The CURRENT open set, all dates — Screen 06's ruling, reused rather
    than redefined, so a carried delivery position does not fall off the wall."""
    p = _s(client)["active_positions"]
    open_rows = db_reader.open_positions_list(gui_config)
    assert p["count"] == len(open_rows) > 0
    assert {r["trade_id"] for r in p["rows"]} == {r["trade_id"] for r in open_rows}
    by_id = {r["trade_id"]: r for r in open_rows}
    for r in p["rows"]:
        src = by_id[r["trade_id"]]
        assert r["status"] == db_reader._position_status_of(src)
        # the BROKER-FILLED quantity IS the position
        assert r["qty"] == src["qty_filled"]


def test_capital_utilization_reuses_the_screen06_summary(client, gui_config):
    p = _s(client)["capital"]
    src = db_reader.position_summary(gui_config, TODAY)["capital"]
    assert p["utilized"] == src["used"]
    assert p["total"] == src["total"]
    assert p["available"] == src["available"]
    assert abs(p["utilized_pct"] + p["available_pct"] - 100.0) < 0.05


def test_the_donut_is_not_drawn_without_a_capital_base():
    """⛔ A ring built from a missing total would be a picture of nothing."""
    out = live_activity._capital.__doc__
    assert "ring is not drawn" in out
    tpl = _tpl()
    assert 'x-show="capital().available_basis"' in tpl
    assert 'x-show="!capital().available_basis"' in tpl
    body = re.search(r"donutMarkup\(\)\s*\{(.*?)\n    \},", tpl, re.S).group(1)
    assert "if (!c.available_basis) return \"\";" in body


# ── real-time behaviour ────────────────────────────────────────────────────
def test_real_time_uses_the_existing_refresh_path(client):
    """⭐ The dashboard's own freshness-driven cadence (5s market / 60s off),
    ⛔ not a second timer invented for this screen."""
    body = _shipped(client)
    assert "ops-refresh.window" in body
    assert 'pageBase("/api/live-activity/screen")' in body
    assert "setInterval" not in _tpl()
    assert _s(client)["live"]["poll_ms"] in (5000, 60000)


def test_live_mode_and_the_pause_are_display_controls_only(client):
    """⛔ There is no write path in this dashboard (L4). Both controls start and
    stop THIS PAGE's polling and nothing else."""
    tpl = _tpl()
    body = _page_content(client)
    assert "LIVE MODE" in body
    assert ">ON<" in body and ">OFF<" in body
    # the pause and the two buttons all drive the same one flag
    assert tpl.count("setLive(") >= 4
    assert '@ops-refresh.window="live && load()"' in tpl
    # ⛔ nothing posts anywhere
    assert 'method="post"' not in body.lower()
    assert "fetch(" not in tpl
    assert "not instrumented" not in _s(client)["live"]["note"].lower()
    assert "no write path" in _s(client)["live"]["note"]


def test_no_page_route_or_endpoint_was_taken_from_another_screen(client):
    """⭐ ADDITIVE: the G5d `/api/activity` merged feed keeps its own contract."""
    old = client.get("/api/activity").get_json()
    assert {"today", "count", "types", "rows"} <= set(old)
    assert client.get("/live-activity").status_code == 200
    assert client.get("/api/live-activity/screen").status_code == 200


def test_the_new_endpoints_require_auth(app):
    anon = app.test_client()
    for ep in ("/api/live-activity/screen", "/api/export/live-activity"):
        assert anon.get(ep).status_code == 401, ep


# ── XLSX ───────────────────────────────────────────────────────────────────
def test_export_is_the_filtered_feed_in_the_approved_order(client):
    from openpyxl import load_workbook
    r = client.get("/api/export/live-activity?category=Trades")
    assert r.status_code == 200
    ws = load_workbook(io.BytesIO(r.data))["Live Activity"]
    header = [c.value for c in ws[1]]
    assert header[:6] == ["Time", "Category", "Event", "Strategy / Symbol",
                          "Details", "Status"]
    assert "Scanner" not in header
    body = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(body) == _s(client, "?category=Trades")["count"]
    assert {row[1] for row in body} == {"Trade"}


def test_the_export_rows_are_the_screen_rows(client):
    p = _s(client)
    rows = live_activity.export_rows(p)
    assert len(rows) == len(p["records"]) + 1
    assert rows[1][3] == p["records"][0]["strategy_symbol"]


# ── layout / reuse / conventions ───────────────────────────────────────────
def test_every_approved_panel_is_on_the_page(client):
    body = _page_content(client)
    for heading in ("LIVE ACTIVITY", "REAL-TIME ACTIVITY FEED",
                    "LIVE PROCESSING PIPELINE", "STRATEGY ACTIVITY",
                    "ALERTS BANNER", "MARKET PULSE", "LIVE MODE", "EXPORT",
                    "RECENT WINNERS / LOSERS", "SYSTEM EVENTS", "FEED FILTERS",
                    "ACTIVE POSITIONS", "CAPITAL UTILIZATION"):
        assert heading in body, heading
    for control in ("View Full Feed", "View All Strategies", "View All Alerts",
                    "View All Events", "View All Positions", "View Full Pulse",
                    "Export to XLSX"):
        assert control in body, control


def test_the_six_approved_kpi_cards_in_the_approved_order():
    """⭐ The KPI deck is data-driven (the Screen-15 pattern), so the approved
    labels live in the KPIS array and are asserted there, exactly and in order."""
    block = re.search(r"KPIS:\s*\[(.*?)\n    \],", _tpl(), re.S).group(1)
    assert re.findall(r'label:\s*"([^"]+)"', block) == [
        "CURRENT TIME", "SIGNALS TODAY", "ORDERS TODAY", "OPEN POSITIONS",
        "CURRENT MTM", "TRADING STATUS"]
    assert re.findall(r'key:\s*"([a-z_]+)"', block) == [
        "current_time", "signals_today", "orders_today", "open_positions",
        "current_mtm", "trading_status"]


def test_the_four_winner_loser_cards_are_the_approved_four():
    tpl = _tpl()
    block = re.search(r"WL_CARDS:\s*\[(.*?)\n    \],", tpl, re.S).group(1)
    assert re.findall(r'label:\s*"([^"]+)"', block) == [
        "LAST PROFIT", "LAST LOSS", "BEST TRADE TODAY", "WORST TRADE TODAY"]


def test_the_nine_toolbar_chips_and_eight_tiles_are_the_approved_sets():
    tpl = _tpl()
    chips = re.search(r"CHIPS:\s*\[(.*?)\n    \],", tpl, re.S).group(1)
    tiles = re.search(r"TILES:\s*\[(.*?)\n    \],", tpl, re.S).group(1)
    assert re.findall(r'label:\s*"([^"]+)"', chips) == [
        "All", "Signals", "Orders", "Positions", "Trades", "Risk", "Capital",
        "System", "Alerts"]
    assert re.findall(r'label:\s*"([^"]+)"', tiles) == list(
        live_activity.FEED_FILTERS)


def test_header_and_body_share_one_column_list_so_they_cannot_desync():
    assert _tpl().count('x-for="c in cols"') == 2


def test_column_drag_reuses_the_established_convention():
    tpl = _tpl()
    for hook in ("onDragStart", "onDrop", "onDragEnd", "initCols", "saveCols",
                 "resetCols", "isDefaultOrder", "localStorage"):
        assert hook in tpl, hook
    assert "screen18.liveactivity.colOrder.v1" in tpl
    assert "next.length === this.DEFAULT_COLS.length" in tpl


def test_a_stored_order_can_permute_but_never_change_the_column_set():
    body = re.search(r"initCols\(\)\s*\{(.*?)\n    \},", _tpl(), re.S).group(1)
    assert "byKey[k]" in body
    assert "next.indexOf(byKey[k]) === -1" in body
    assert "next.length === this.DEFAULT_COLS.length" in body


def test_no_artificial_height_device_closes_a_gap():
    """⛔ A min-height / fixed height / stretch would HIDE a layout imbalance
    rather than fix it — the lesson Screen 15 paid for."""
    css = _css()
    start = css.index("SCREEN 18 — LIVE ACTIVITY")
    nxt = re.search(r"SCREEN \d+ [—-]", css[start + 20:])
    block = css[start: start + 20 + nxt.start()] if nxt else css[start:]
    for rule in re.findall(r"^\.lav-page[^{]*\{[^}]*\}", block, re.M | re.S):
        head = rule.split("{")[0]
        if ".lav-row" in head or ".panel" in head or "lav-work" in head:
            assert "min-height" not in rule, rule
            assert "height:" not in rule, rule
    assert "align-items: start" in block      # ⛔ never `stretch` on these rows


def test_the_feeds_bounded_height_is_a_scroll_container(client):
    """🔴 CAUGHT IN THE BROWSER: with a real day's events the feed rendered all
    190 rows and the panel grew to 7204px, burying every panel below it. The
    binding PNG draws the feed as a fixed-height window, so the body scrolls.
    ⛔ The height is only ever allowed to travel WITH `overflow-y: auto` — a
    bare height would hide rows instead of letting the operator reach them."""
    css = _css()
    start = css.index("SCREEN 18 — LIVE ACTIVITY")
    block = css[start:]
    rule = re.search(r"\.lav-page \.lav-feed \.lav-tbl-wrap \{[^}]*\}", block).group(0)
    assert "max-height" in rule
    assert "overflow-y: auto" in rule
    # and the footer states the real total, so a scrolled feed never under-reports
    assert "showing()" in _tpl()
    p = _s(client)
    assert p["count"] == len(p["records"])


def test_the_feed_columns_are_sized_by_key_so_a_drag_cannot_reassign_them():
    """⭐ A fixed table layout is what keeps the approved STATUS traffic light
    inside the panel. The widths hang off the column KEY, so reordering moves a
    column with its own width instead of handing Time's 80px to Details."""
    tpl = _tpl()
    assert "table-layout: fixed" in _css()
    assert ':style="c.w ? (\'width:\' + c.w) : \'\'"' in tpl
    cols = re.search(r"DEFAULT_COLS:\s*\[(.*?)\n    \],", tpl, re.S).group(1)
    widths = re.findall(r'key:\s*"([a-z_]+)".*?w:\s*"(\d+px)"', cols)
    assert [k for k, _ in widths] == ["time", "category", "status"]


def test_alignment_is_by_role_and_not_blanket_centred():
    css = _css()
    start = css.index("SCREEN 18 — LIVE ACTIVITY")
    block = css[start:]
    assert (".lav-page .lav-tbl th, .lav-page .lav-tbl td { text-align: left; }"
            in block)
    cols = re.search(r"DEFAULT_COLS:\s*\[(.*?)\n    \],", _tpl(), re.S).group(1)
    assert cols.count("ctr: true") == 2      # Category + Status only


def test_every_table_mixin_member_the_page_calls_actually_exists():
    """⚠️ Carried forward: Alpine swallows an undefined-method binding, so this
    class of bug is invisible to every contract test. It bit Screen 13 twice."""
    tpl = _tpl()
    mixin = _read("frontend", "static", "components.js")
    defined = set(re.findall(r"^\s{4}(t[A-Za-z]+)\s*:", mixin, re.M))
    used = set(re.findall(r"\b(t[A-Z][A-Za-z]*)\b", tpl))
    unknown = sorted(u for u in used if u not in defined)
    assert not unknown, "template calls table-mixin members that do not exist: %s" % unknown


def test_every_page_method_the_markup_calls_is_defined_on_the_component():
    tpl = _tpl()
    markup = tpl[:tpl.index("<script>")]
    defined = set(re.findall(r"^\s{4}(?:async\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\(", tpl, re.M))
    defined |= set(re.findall(r"^\s{4}([a-zA-Z_][a-zA-Z0-9_]*):", tpl, re.M))
    mixin = _read("frontend", "static", "components.js")
    defined |= set(re.findall(r"^\s{4}([a-zA-Z_][a-zA-Z0-9_]*)\s*[:(]", mixin, re.M))
    defined |= {"load", "Math", "Date", "JSON", "Number", "String", "Object",
                "encodeURIComponent", "fetch", "includes"}
    called = set(re.findall(
        r'(?:x-text|x-show|x-html|@click|:class|:href|:disabled|x-model|:title)='
        r'"[^"]*?(?<![.\w])([a-zA-Z_][a-zA-Z0-9_]*)\(', markup))
    unknown = sorted(c for c in called if c not in defined)
    assert not unknown, "markup calls undefined page methods: %s" % unknown


def test_no_alpine_template_loop_lives_inside_an_svg():
    """⛔ An x-for TEMPLATE inside an SVG renders NOTHING — the HTML parser turns
    it into an SVG node named "template". The donut uses x-html on a group."""
    tpl = _tpl()
    for m in re.finditer(r"<svg\b.*?</svg>", tpl, re.S):
        assert "<template" not in m.group(0), m.group(0)[:200]
    assert '<g x-html="donutMarkup()"></g>' in tpl


# ── IST / readability / scoping ────────────────────────────────────────────
def test_timestamps_are_ist_and_never_converted(client):
    src = _read("backend", "services", "live_activity.py")
    assert "astimezone" not in src and "utcnow" not in src
    for r in _s(client)["records"]:
        assert re.match(r"\d{4}-\d{2}-\d{2}$", r["date"])
        assert re.match(r"\d{2}:\d{2}:\d{2}$", r["time"])


def test_text_meets_the_thirteen_pixel_floor():
    css = _css()
    start = css.index("SCREEN 18 — LIVE ACTIVITY")
    block = css[start:]
    small = [float(m) for m in re.findall(r"font-size:\s*(\d+(?:\.\d+)?)px", block)
             if float(m) < 13]
    assert not small, small
    for rule in (".lav-page th { font-size: 13px; }",
                 ".lav-page .mono { font-size: 13px; }",
                 ".lav-page .rt-btn { font-size: 13px; }",
                 ".lav-page .panel-sub { font-size: 13px; }"):
        assert rule in block, rule


def test_the_screen_is_scoped_and_cannot_repaint_another():
    css = _css()
    start = css.index("SCREEN 18 — LIVE ACTIVITY")
    block = css[start:]
    for line in block.splitlines():
        line = line.strip()
        if line.startswith(".") and "{" in line:
            assert line.startswith(".lav-page"), line


def test_no_secret_reaches_the_screen(client):
    blob = json.dumps(_s(client)).lower()
    for secret in ("password", "totp", "api_key", "access_token", "secret"):
        assert secret not in blob
