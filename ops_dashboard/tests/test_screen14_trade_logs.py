"""SCREEN 14 — TRADE LOGS.

The approved design (`gui/14. Trade_Logs.png` + `.txt`) is binding for STRUCTURE;
the DATA is this system's. Both lines are held here: every approved element is
present, and nothing this system fails to record is invented to fill it.

⭐ THE CENTRAL PROPERTY: this system stores no trade-event table, so every event
is DERIVED from a real stored timestamp. A stage the system does not time must
render as an explicit gap — and a test here must fail if it ever silently
becomes a plausible value.
"""
from __future__ import annotations

import io
import json
import os
import re

from backend.services import trade_logs


def _read(*parts) -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, *parts), encoding="utf-8") as fh:
        return fh.read()


def _tpl() -> str:
    return _read("frontend", "templates", "trade_logs.html")


def _css() -> str:
    return _read("frontend", "static", "style.css")


def _shipped(client) -> str:
    """The HTML the browser actually receives, with comments stripped — a Jinja
    `{# #}` never ships and an HTML comment is not UI, so neither may satisfy an
    assertion about what the operator can see."""
    html = client.get("/trade-logs").data.decode("utf-8")
    return re.sub(r"<!--.*?-->", " ", html, flags=re.S)


def _page_content(client) -> str:
    """The TRADE LOGS screen's VISIBLE markup — shared chrome and scripts removed.

    ⛔ Two things must NOT be scanned or the check produces a false failure: the
    global nav carries a link to the SEPARATE Scanner Attribution screen, and the
    inline script carries comments that NAME the rule they enforce. Neither is
    this screen's UI. (Same helper Screen 13 uses, for the same reason.)"""
    body = _shipped(client)
    i = body.find('<div class="tlg-page"')
    assert i > 0, "the trade-logs page root is missing"
    body = body[i:]
    j = body.find("<script>")
    return body[:j] if j > 0 else body


WIDE = "?start=2000-01-01&end=2099-12-31"


def _s(client, qs=WIDE):
    return client.get("/api/trade-logs/screen" + qs).get_json()


# ── the approved vocabularies ───────────────────────────────────────────────
def test_the_thirteen_approved_event_types_are_the_vocabulary():
    assert trade_logs.EVENT_TYPES == (
        "Signal Received", "Signal Accepted", "Signal Rejected",
        "Risk Passed", "Risk Rejected", "Capital Passed", "Capital Rejected",
        "Order Created", "Order Submitted", "Order Filled",
        "SL Hit", "TGT Hit", "Position Closed")
    assert trade_logs.SEVERITIES == ("Info", "Warning", "Error", "Critical")
    assert trade_logs.COMPONENTS == (
        "Signal Engine", "Risk Engine", "Capital Engine",
        "Order Engine", "Broker Connector", "Exit Manager")
    assert trade_logs.TIMELINE_STAGES == (
        "Signal Received", "Validation", "Risk", "Capital",
        "Order", "Fill", "Position", "Exit")


def test_every_event_type_maps_to_one_approved_component():
    for ev in trade_logs.EVENT_TYPES:
        assert trade_logs._COMPONENT_OF[ev] in trade_logs.COMPONENTS


# ── ⛔ SCANNER IS REMOVED, STRATEGY IS RETAINED ─────────────────────────────
def test_scanner_appears_nowhere_the_operator_can_see(client):
    assert "scanner" not in _page_content(client).lower()
    # ⭐ and the nav link to the SEPARATE Scanner Attribution screen is UNTOUCHED
    assert "/scanner-attribution" in _shipped(client)
    payload = _s(client)
    assert "scanner" not in json.dumps(payload).lower()


def test_the_readers_do_not_even_select_scanner():
    """⛔ Enforced at the READER, so no template or export edit can bring it
    back. `signals.scanner` exists in the schema — it is deliberately unread."""
    import inspect
    from backend.readers import db_reader
    for fn in (db_reader.tradelog_signals_range, db_reader.tradelog_signals_by_id):
        src = inspect.getsource(fn)
        sql = src[src.index("SELECT"):src.index("FROM signals")]
        assert "scanner" not in sql.lower(), fn.__name__


def test_strategy_is_retained_everywhere(client):
    body = _shipped(client)
    assert "Strategy" in body
    payload = _s(client)
    assert "strategy" in json.dumps(payload["records"][0]).lower()
    assert "Strategy" in trade_logs.EXPORT_HEADER


def test_the_export_header_has_no_scanner_column():
    assert "Scanner" not in trade_logs.EXPORT_HEADER
    assert "Strategy" in trade_logs.EXPORT_HEADER


# ── the grain: EVENT per row, not trade per row ─────────────────────────────
def test_the_feed_is_event_per_row_not_trade_per_row(client):
    """The G5d screen this replaces was one row per trade; the approved design
    is a forensic EVENT feed. One trade must therefore appear many times."""
    rows = _s(client)["records"]
    with_trade = [r for r in rows if r["trade_id"]]
    assert len(with_trade) > len({r["trade_id"] for r in with_trade})
    for r in rows:
        assert r["event_type"] in trade_logs.EVENT_TYPES


# ── ⛔ the gaps, and they must never become numbers ─────────────────────────
def test_risk_passed_and_capital_passed_are_never_counted(client):
    """⭐ THE MOST IMPORTANT TEST ON THIS SCREEN. A passing gate writes no row
    anywhere in this system, so these are UNMEASURABLE, not zero. If a future
    change starts emitting them, this fails and the gap text must be revisited
    — a plausible count here would be a fabricated one."""
    payload = _s(client)
    assert payload["uninstrumented_events"] == ["Risk Passed", "Capital Passed"]
    for r in payload["records"]:
        assert r["event_type"] not in ("Risk Passed", "Capital Passed")
    assert payload["gaps"]["risk_capital_pass"]["measured"] is False


def test_the_screen_declares_every_gap_it_relies_on(client):
    gaps = _s(client)["gaps"]
    for key in ("risk_capital_pass", "risk_capital_time", "error_code",
                "resolution", "user", "signal_path_logs", "retention_period"):
        assert gaps[key]["measured"] is False
        assert gaps[key]["value"] is None
        assert len(gaps[key]["reason"]) > 30, key


def test_error_code_and_resolution_are_never_invented(client):
    """The approved design shows EXCH-1016 / RISK-2001 / CAP-3002. A repo-wide
    search finds no such scheme, so the columns stay explicit gaps."""
    payload = _s(client)
    for e in payload["errors"]:
        assert e["error_code"] is None
        assert e["resolution"] is None
    for r in payload["records"]:
        assert r["error_code"] is None and r["resolution"] is None
    body = _shipped(client)
    assert "NOT INSTRUMENTED" in body


def test_recovery_never_invents_a_recovered_in_duration(client):
    """`reconciliation_log` stores ONE instant per action and no duration."""
    for r in _s(client)["recovery"]:
        assert r["recovered_in"] is None


# ── determinism: a poll may not duplicate, reshuffle or lose a row ──────────
def test_repeated_builds_neither_duplicate_nor_lose_events(client):
    first = _s(client)["records"]
    for _ in range(3):
        again = _s(client)["records"]
        assert len(again) == len(first)
        assert [r["ref_id"] for r in again] == [r["ref_id"] for r in first]


def test_reference_ids_are_unique_and_name_their_real_source(client):
    rows = _s(client)["records"]
    ids = [r["ref_id"] for r in rows]
    assert len(set(ids)) == len(ids)
    prefixes = {i.split("-")[0] for i in ids}
    assert prefixes <= {"SIG", "SIGACC", "SIGREJ", "RSKREJ", "CAPREJ", "TRD", "ORD"}


def test_equal_timestamps_order_deterministically(client):
    """⭐ Two events sharing one stamp must not be able to swap between polls —
    the sort key falls through to the reference id."""
    rows = _s(client)["records"]
    keyed = [(r["ts"], r["ref_id"]) for r in rows]
    assert keyed == sorted(keyed, reverse=True)


# ── one population feeds every panel ───────────────────────────────────────
def test_kpi_event_counts_reconcile_to_the_table(client):
    p = _s(client)
    rows = p["records"]
    assert p["kpi"]["total_events"] == len(rows) == p["count"]
    assert p["kpi"]["warnings"] == sum(1 for r in rows if r["severity"] == "Warning")
    assert p["kpi"]["errors"] == sum(
        1 for r in rows if r["severity"] in ("Error", "Critical"))


def test_the_trade_cards_count_trades_and_the_event_cards_count_events(client):
    """⛔ A card labelled TRADES must not report an event count: one filled trade
    emits several events, so that would inflate it several-fold. Each card's
    percentage is of its OWN base and the payload carries both bases."""
    p = _s(client)
    rows = p["records"]
    k = p["kpi"]
    assert k["trades_in_view"] == len({r["trade_id"] for r in rows if r["trade_id"]})
    assert k["successful"] <= k["trades_in_view"]
    assert k["failed"] <= k["trades_in_view"]
    # the event cards are genuinely event-scaled, and on this fixture that is a
    # DIFFERENT number — so the test could actually go red if they were swapped
    assert k["total_events"] != k["trades_in_view"]


def test_event_type_and_severity_panels_sum_to_the_population(client):
    p = _s(client)
    assert sum(p["by_event_type"].values()) == p["count"]
    assert sum(p["by_severity"].values()) == p["count"]


def test_the_chart_conserves_counts_and_cannot_double_count(client):
    p = _s(client)
    assert p["over_time"]["total"] == p["count"]
    assert len(p["over_time"]["days"]) == len(p["over_time"]["counts"])


def test_a_filter_narrows_every_panel_together(client):
    wide = _s(client)
    narrow = _s(client, WIDE + "&event_type=Order%20Filled")
    assert narrow["count"] < wide["count"]
    assert all(r["event_type"] == "Order Filled" for r in narrow["records"])
    assert narrow["kpi"]["total_events"] == narrow["count"]
    assert sum(narrow["by_event_type"].values()) == narrow["count"]
    assert narrow["over_time"]["total"] == narrow["count"]


def test_retention_describes_the_store_not_the_filter(client):
    """⛔ A retention figure that shrank when the operator narrowed a date range
    would not be a retention figure."""
    wide = _s(client)["retention"]
    narrow = _s(client, "?start=2099-01-01&end=2099-01-02")["retention"]
    assert wide["records"] == narrow["records"]


# ── the timeline / replay tells the truth about what it cannot know ────────
def _a_trade_id(client):
    for r in _s(client)["records"]:
        if r["trade_id"]:
            return r["trade_id"]
    raise AssertionError("fixture has no trade-bearing event")


def test_the_timeline_has_the_eight_approved_stages_in_order(client):
    d = client.get("/api/trade-logs/detail?trade_id=" + _a_trade_id(client)).get_json()
    assert [s["stage"] for s in d["timeline"]] == list(trade_logs.TIMELINE_STAGES)


def test_risk_and_capital_stages_are_gaps_with_a_stated_reason(client):
    d = client.get("/api/trade-logs/detail?trade_id=" + _a_trade_id(client)).get_json()
    by = {s["stage"]: s for s in d["timeline"]}
    for stage in ("Risk", "Capital"):
        assert by[stage]["measured"] is False
        assert by[stage]["ts"] is None
        assert "no row" in by[stage]["gap"]


def test_the_measured_stages_carry_real_stored_timestamps(client):
    """⭐ Non-vacuous: the replay must actually reconstruct something. If every
    stage came back unmeasured this test goes red rather than passing quietly."""
    d = client.get("/api/trade-logs/detail?trade_id=" + _a_trade_id(client)).get_json()
    assert d["stages_measured"] >= 4
    for s in d["timeline"]:
        if s["measured"]:
            assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", s["ts"])


def test_request_decision_output_uses_real_screener_values(client):
    d = client.get("/api/trade-logs/detail?trade_id=" + _a_trade_id(client)).get_json()
    rr = d["request_response"]
    assert rr["input"]["system_score"] is not None
    assert rr["input"]["eligible_score"] is not None
    # ⭐ two genuinely different real columns, not one printed twice
    assert rr["input"]["system_score"] != rr["input"]["eligible_score"]
    assert rr["decision"]["verdict"] in ("Accepted", "Rejected")


def test_an_unknown_trade_id_is_a_404_not_an_empty_shell(client):
    assert client.get("/api/trade-logs/detail?trade_id=nope").status_code == 404


# ── XLSX ────────────────────────────────────────────────────────────────────
def test_export_is_the_filtered_set_and_has_no_scanner(client):
    from openpyxl import load_workbook
    r = client.get("/api/export/trade-logs" + WIDE + "&event_type=Order%20Filled")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.data))
    ws = wb["Trade Log Events"]
    header = [c.value for c in ws[1]]
    assert "Scanner" not in header and "Strategy" in header
    body = list(ws.iter_rows(min_row=2, values_only=True))
    expected = _s(client, WIDE + "&event_type=Order%20Filled")["count"]
    assert len(body) == expected
    assert {row[6] for row in body} == {"Order Filled"}


# ── the approved layout is present ─────────────────────────────────────────
def test_every_approved_panel_is_on_the_page(client):
    body = _shipped(client)
    for heading in ("TRADE LOGS", "FILTERS", "TRADE LOG EVENTS", "EVENT TYPES",
                    "SEVERITY LEVELS", "SEARCH", "RETENTION OVERVIEW",
                    "TRADE TIMELINE", "EVENT DETAILS", "CRITICAL CHANGES",
                    "AUTO-RECOVERY HISTORY", "RECENT ERRORS", "EXPORT"):
        assert heading in body, heading
    for control in ("Reset Filters", "Apply Filters", "Replay Trade Lifecycle",
                    "Export to XLSX", "Rows per page", "Message Text"):
        assert control in body, control


def test_the_approved_ten_columns_survive_scanner_removal():
    tpl = _tpl()
    block = re.search(r"DEFAULT_COLS:\s*\[(.*?)\n    \],", tpl, re.S).group(1)
    keys = re.findall(r'key:\s*"([a-z_]+)"', block)
    assert keys == ["date", "time", "trade_id", "order_id", "symbol", "strategy",
                    "event_type", "status", "message", "component"]
    assert "scanner" not in block.lower()


def test_symbol_comes_before_strategy_everywhere_it_is_ordered():
    """⭐ A FINAL-COMPLIANCE INVARIANT, pinned in all three places at once so the
    three cannot drift: the visible/draggable default order, and the export.
    Both columns are kept — Scanner is the one that was removed, ⛔ not Symbol."""
    tpl = _tpl()
    block = re.search(r"DEFAULT_COLS:\s*\[(.*?)\n    \],", tpl, re.S).group(1)
    keys = re.findall(r'key:\s*"([a-z_]+)"', block)
    assert keys.index("symbol") < keys.index("strategy")
    hdr = list(trade_logs.EXPORT_HEADER)
    assert hdr.index("Symbol") < hdr.index("Strategy")


def test_the_export_opens_in_the_approved_table_order():
    """The first TEN export columns are the approved table columns in the
    approved order; Severity and Reference ID follow AFTER, never interleaved."""
    tpl = _tpl()
    block = re.search(r"DEFAULT_COLS:\s*\[(.*?)\n    \],", tpl, re.S).group(1)
    labels = re.findall(r'label:\s*"([^"]+)"', block)
    assert list(trade_logs.EXPORT_HEADER[:10]) == labels
    assert list(trade_logs.EXPORT_HEADER[10:]) == ["Severity", "Reference ID"]


def test_there_is_no_visible_columns_button(client):
    """⛔ The binding PNG's table toolbar is Search + Export ONLY. Header drag
    stays; the reset control other screens carry is not shown here, and ⛔ no
    other column-management affordance replaces it."""
    body = _page_content(client)
    assert "↺ Columns" not in body
    assert "Columns</button>" not in body
    assert "resetCols()" not in body          # not wired to any visible control
    # ⭐ but drag/reorder itself is UNTOUCHED
    assert "onDragStart" in body and 'draggable="true"' in body


def test_the_third_detail_tab_is_labelled_raw_payload(client):
    body = _page_content(client)
    assert "RAW PAYLOAD" in body
    tpl = _tpl()
    tabs = re.search(r"TABS:\s*\[(.*?)\],", tpl, re.S).group(1)
    labels = re.findall(r'l:\s*"([^"]+)"', tabs)
    assert labels == ["DETAILS", "INPUT / DECISION / OUTPUT", "RAW PAYLOAD"]


def test_the_raw_payload_tab_says_what_it_cannot_show(client):
    """⛔ A derived event has no broker/webhook payload of its own, and the tab
    says so rather than letting the operator assume the record IS one."""
    body = _page_content(client)
    assert "NO SEPARATE RAW PAYLOAD" in body


def test_the_filters_are_the_approved_set_without_scanner():
    tpl = _tpl()
    block = re.search(r"SELECTS:\s*\[(.*?)\n    \],", tpl, re.S).group(1)
    keys = re.findall(r'key:\s*"([a-z_]+)"', block)
    assert keys == ["symbol", "strategy", "trade_type", "direction",
                    "event_type", "status"]
    assert "scanner" not in block.lower()
    for extra in ('x-model="f.trade_id"', 'x-model="f.order_id"',
                  'x-model="f.start"', 'x-model="f.end"'):
        assert extra in tpl


# ── table: drag / alignment / pager, the established conventions ───────────
def test_header_and_body_share_one_column_list_so_they_cannot_desync():
    """⛔ The classic drag defect: headers reorder, cells do not."""
    tpl = _tpl()
    assert tpl.count('x-for="c in cols"') == 2


def test_column_drag_reuses_the_established_convention():
    tpl = _tpl()
    for hook in ("onDragStart", "onDrop", "onDragEnd", "initCols", "saveCols",
                 "resetCols", "isDefaultOrder", "localStorage"):
        assert hook in tpl, hook
    assert "screen14.tradelogs.colOrder.v2" in tpl
    assert "screen14.tradelogs.colOrder.v1" not in tpl   # ⛔ retired, never reused
    assert "next.length === this.DEFAULT_COLS.length" in tpl


def test_a_restored_column_order_can_never_change_the_column_SET():
    """⭐ A saved order may permute the columns; it must never add, drop or
    rename one. `initCols` rebuilds ONLY from DEFAULT_COLS by key and refuses
    the result unless the length still matches — which is also why a stale
    saved order survives a pure REORDER and had to be retired by a key bump
    rather than by hoping it would self-correct."""
    tpl = _tpl()
    body = re.search(r"initCols\(\)\s*\{(.*?)\n    \},", tpl, re.S).group(1)
    assert "byKey[k]" in body                       # only known keys admitted
    assert "next.indexOf(byKey[k]) === -1" in body  # no duplicates
    assert "next.length === this.DEFAULT_COLS.length" in body


def test_a_drag_cannot_reintroduce_scanner():
    """Column order is restored by KEY against DEFAULT_COLS, and no Scanner key
    exists there — so no saved order can resurrect it."""
    tpl = _tpl()
    body = re.search(r"initCols\(\)\s*\{(.*?)\n    \},", tpl, re.S).group(1)
    assert "byKey[k]" in body and "DEFAULT_COLS" in body
    assert "scanner" not in body.lower()


def test_alignment_is_by_role_and_not_blanket_centred():
    css = _css()
    block = css[css.index("SCREEN 14 — TRADE LOGS"):]
    assert ".tlg-tbl th, .tlg-page .tlg-tbl td { text-align: left; }" in block
    assert "th.ctr" in block and "td.ctr" in block
    tpl = _tpl()
    cols = re.search(r"DEFAULT_COLS:\s*\[(.*?)\n    \],", tpl, re.S).group(1)
    assert cols.count("ctr: true") == 1          # Status only


def test_the_pager_renders_numbered_pages_not_just_arrows():
    tpl = _tpl()
    assert "pageList()" in tpl
    body = re.search(r"pageList\(\)\s*\{(.*?)\n    \},", tpl, re.S).group(1)
    assert "tPageCount" in body and "…" in body


def test_every_table_mixin_member_the_page_calls_actually_exists():
    """⚠️ CARRIED FORWARD FROM SCREEN 13, where this class of bug bit TWICE and
    both times silently: `tSize` (really `tPageSize`) rendered "Showing NaN to
    NaN" and `tPages` (really `tPageCount`) made the numbered pages vanish.
    Alpine swallows an undefined-method binding, so nothing throws and every
    contract test still passes."""
    tpl = _tpl()
    mixin = _read("frontend", "static", "components.js")
    defined = set(re.findall(r"^\s{4}(t[A-Za-z]+)\s*:", mixin, re.M))
    assert {"tPageCount", "tPaged", "tPageSize", "tPage", "tSort"} <= defined
    used = set(re.findall(r"\b(t[A-Z][A-Za-z]*)\b", tpl))
    unknown = sorted(u for u in used if u not in defined)
    assert not unknown, "template calls table-mixin members that do not exist: %s" % unknown


def test_every_page_method_the_markup_calls_is_defined_on_the_component():
    """⭐ The same swallow applies to THIS page's OWN methods, which the Screen-13
    guard could not see. Every `name(` called from an Alpine binding must exist
    in the component object, the table mixin, or the shared page base."""
    tpl = _tpl()
    markup = tpl[:tpl.index("<script>")]
    defined = set(re.findall(r"^\s{4}(?:async\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\(", tpl, re.M))
    defined |= set(re.findall(r"^\s{4}([a-zA-Z_][a-zA-Z0-9_]*):", tpl, re.M))
    mixin = _read("frontend", "static", "components.js")
    defined |= set(re.findall(r"^\s{4}([a-zA-Z_][a-zA-Z0-9_]*)\s*[:(]", mixin, re.M))
    defined |= {"load", "Math", "Date", "JSON", "Number", "String", "Object",
                "encodeURIComponent", "fetch", "includes", "slice", "toLowerCase"}
    # ⛔ `(?<![.\w])` so `Math.max(` and `arr.includes(` are not mistaken for page
    # methods — only a BARE call can be an undefined component member.
    called = set(re.findall(
        r'(?:x-text|x-show|x-html|@click|:class|:href|:disabled|x-model)='
        r'"[^"]*?(?<![.\w])([a-zA-Z_][a-zA-Z0-9_]*)\(', markup))
    unknown = sorted(c for c in called if c not in defined)
    assert not unknown, "markup calls undefined page methods: %s" % unknown


# ── real-time / refresh ────────────────────────────────────────────────────
def test_real_time_uses_the_existing_refresh_path(client):
    """⛔ No second polling architecture: the page listens to the dashboard's
    own `ops-refresh` broadcast."""
    body = _shipped(client)
    assert "ops-refresh.window" in body
    assert 'pageBase("/api/trade-logs/screen")' in body


def test_the_legacy_g5d_endpoint_is_untouched(client):
    """⭐ ADDITIVE, exactly as Screen 07 added `/api/trades/screen` beside
    `/api/trades`. Removing the old route would break its own contract test."""
    assert client.get("/api/trade-logs?period=week").status_code == 200


# ── IST / readability ──────────────────────────────────────────────────────
def test_timestamps_are_ist_and_never_converted(client):
    """Both the store and the display are IST, so the module must not convert.
    Any tz maths here would be a bug, not a feature."""
    src = _read("backend", "services", "trade_logs.py")
    assert "astimezone" not in src and "utcnow" not in src
    for r in _s(client)["records"]:
        assert re.match(r"\d{4}-\d{2}-\d{2}$", r["date"])
        assert re.match(r"\d{2}:\d{2}:\d{2}$", r["time"])


def test_text_meets_the_thirteen_pixel_floor():
    css = _css()
    start = css.index("SCREEN 14 — TRADE LOGS")
    nxt = re.search(r"SCREEN \d+ [—-]", css[start + 20:])
    block = css[start: start + 20 + nxt.start()] if nxt else css[start:]
    small = [float(m) for m in re.findall(r"font-size:\s*(\d+(?:\.\d+)?)px", block)
             if float(m) < 13]
    assert not small, small
    # SHARED component classes come in below the floor and are lifted HERE only.
    for rule in (".tlg-page th { font-size: 13px; }",
                 ".tlg-page .mono { font-size: 13px; }",
                 ".tlg-page .rt-btn { font-size: 13px; }",
                 ".tlg-page .panel-sub { font-size: 13px; }"):
        assert rule in block, rule


def test_the_screen_is_scoped_and_cannot_repaint_another():
    css = _css()
    start = css.index("SCREEN 14 — TRADE LOGS")
    # ⚠️ BOUND IT AT THE NEXT SCREEN HEADER. Reading to EOF passed only while
    # this was the last block in the file; Screen 15 appended after it and the
    # test then judged .slg-page rules as Screen-14's.
    nxt = re.search(r"SCREEN \d+ [—-]", css[start + 20:])
    block = css[start: start + 20 + nxt.start()] if nxt else css[start:]
    for line in block.splitlines():
        line = line.strip()
        if line.startswith(".") and "{" in line:
            assert line.startswith(".tlg-page"), line


# ── secrets ────────────────────────────────────────────────────────────────
def test_no_raw_webhook_payload_or_secret_reaches_the_screen(client):
    """⛔ `signals.webhook_payload` is an unbounded third-party blob and nothing
    on the approved screen needs it, so it is not even SELECTed."""
    import inspect
    from backend.readers import db_reader
    for fn in (db_reader.tradelog_signals_range, db_reader.tradelog_signals_by_id):
        src = inspect.getsource(fn)
        sql = src[src.index("SELECT"):src.index("FROM signals")]
        assert "webhook_payload" not in sql, fn.__name__
    blob = json.dumps(_s(client)).lower()
    for secret in ("password", "totp", "api_key", "access_token", "secret"):
        assert secret not in blob


# ══════════════════════════════════════════════════════════════════════════
# PAGE-LEVEL OVERFLOW — THE BARE-`fr` DEFECT (measured 19-Aug-2026)
#
# MEASURED BEFORE: at 1440x900 (CSS clientWidth 1416) the page scrolled to 1760
# — a 344px horizontal overflow — and it grew as the viewport narrowed:
#     clientWidth 1728 +32 · 1660 +100 · 1600 +160 · 1536 +224 · 1416 +344
#
# TWO WRONG FIXES WERE TRIED AND REVERTED FIRST, AND BOTH ARE PINNED BELOW SO
# THEY ARE NOT RETRIED:
#   ① raising `.tlg-work`'s stacking breakpoint. It DID stack the rail
#      (computed `grid-template-columns: 1548.17px`, a single column) and the
#      page STILL overflowed 344px — the floor was not in that row.
#   ② constraining `main.content` with `:has(> .tlg-page)`. It DID fix that
#      element (clientWidth 1580 -> 1236) but the page still overflowed 328px,
#      because a second owner sat below it.
#
# THE ACTUAL OWNER: `.tlg-row4` declared `grid-template-columns: 1.3fr 1.6fr .8fr`
# — BARE `fr`. A grid item defaults to `min-width: auto`, so a track cannot
# shrink below its content's intrinsic width and the row stayed 1548px wide
# inside a 1204px container. This file's OWN comment at `.tlg-work` already
# warns against exactly that.
#
# ⭐ AND THE BUG WAS ALSO DISTORTING THE APPROVED PROPORTIONS, not merely
# overflowing: measured `.tlg-errs` 753.7px beside `.tlg-export` 106.7px, which
# is nothing like the declared 1.6 : 0.8. With `minmax(0, …)` they measure
# 664.2 / 332.1 at 1920 — exactly 2:1. The fix RESTORES the artwork's ratios.
#
# AFTER: page overflow ZERO at all eight tested widths (1896, 1776, 1728, 1660,
# 1600, 1536, 1416, 1400), the rail stays BESIDE the main table as the artwork
# draws it, and `.tlg-tbl-wrap` keeps its own `overflow-x: auto` scrolling.
# ══════════════════════════════════════════════════════════════════════════
class TestNoPageLevelOverflow:

    def _row(self, name):
        css = _css()
        m = re.search(r"\.tlg-page \." + name + r" \{([^}]*)\}", css)
        assert m, ".tlg-page .%s rule missing" % name
        return m.group(1)

    def test_row4_tracks_are_minmax_not_bare_fr(self):
        """⛔ The measured owner. A bare `fr` track cannot shrink below its
        content, which is what pushed the page to 1760px."""
        body = self._row("tlg-row4")
        m = re.search(r"grid-template-columns:\s*([^;]+);", body)
        assert m, "no grid-template-columns on .tlg-row4: %s" % body
        tracks = m.group(1)
        assert "minmax(0," in tracks, "bare fr tracks are back: %s" % tracks
        # ⛔ Strip the minmax() wrappers BEFORE looking for a bare fr — the fr
        # units INSIDE minmax(0, 1.3fr) are correct and must not be flagged.
        # (This test caught that in its own first run.)
        outside = re.sub(r"minmax\([^)]*\)", "", tracks)
        assert not re.search(r"[0-9.]+fr", outside), (
            "an unwrapped fr track remains: %s" % tracks)

    def test_the_approved_column_ratios_are_unchanged(self):
        """⛔ The fix must change the SHRINK behaviour, ⛔ not the proportions.
        1.3 : 1.6 : .8 is what the approved layout declares."""
        tracks = re.search(r"grid-template-columns:\s*([^;]+);",
                           self._row("tlg-row4")).group(1)
        assert re.findall(r"([0-9.]+)fr", tracks) == ["1.3", "1.6", "0.8"] or \
               re.findall(r"([0-9.]+)fr", tracks) == ["1.3", "1.6", ".8"], tracks

    def test_the_two_reverted_fixes_have_not_come_back(self):
        """⛔ Both were MEASURED not to work. Re-adding either would be a
        regression to a disproved hypothesis, ⛔ not a fix."""
        css = _css()
        assert "has(> .tlg-page)" not in css, (
            "the main.content constraint is back — measured to leave 328px")
        work = self._row("tlg-work")
        assert "330px" in work, "the rail width changed"
        assert "@media (max-width: 1400px) { .tlg-page .tlg-work" in css, (
            "the .tlg-work breakpoint moved — raising it was measured NOT to fix "
            "the overflow, and it stacked the rail the artwork shows beside")

    def test_the_wide_event_table_still_scrolls_inside_its_own_wrapper(self):
        """⛔ The page must not scroll instead. Measured at 1440: `.tlg-tbl-wrap`
        is 1104/824 — content wider than the box, contained by its own auto."""
        css = _css()
        m = re.search(r"\.tlg-page \.tlg-tbl-wrap \{([^}]*)\}", css)
        assert m, ".tlg-tbl-wrap rule missing"
        assert "auto" in m.group(1), (
            "the event table lost its own horizontal scroll: %s" % m.group(1))

    def test_the_fix_is_scoped_to_screen_14(self):
        """⛔ `.tlg-row4` must not become a shared selector."""
        css = _css()
        for m in re.finditer(r"([^{}\n][^{}]*)\{[^}]*\}", css):
            sel = m.group(1).strip()
            if "tlg-row4" in sel:
                assert ".tlg-page" in sel, "unscoped .tlg-row4 rule: %s" % sel[:70]
