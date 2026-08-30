"""Screen 11 — Execution Analytics.

The screen's central problem is that the reference design asks for SEVEN delays
and this system instruments FOUR. Most of what follows guards the honesty of
that gap: an un-instrumented stage must stay VISIBLE and must never acquire a
number, and an execution that never filled must never be counted as fast.

⭐ Assertions are anchored to the fixture's KNOWN values or to a PROPERTY that
must hold. ⛔ No assertion is written by reading back what the code produced.

FIXTURE ARITHMETIC (conftest), so every number below is traceable:
  8 trades created TODAY, all seeded (120, 850, 970) ms   → total 0.97 s  FAST
    …except the 4 OPEN ones, which share the same triple  → total 1.05 s  FAST
  trd_w1  YDAY     (2800, 3400, 6200) ms → 6.20 s  SLOW      · fill 3.40 s
  trd_w2  YDAY     ( 900, 2600, 3500) ms → 3.50 s  MODERATE  · fill 2.60 s
  trd_m1  TENDAYS  (None, None, None)    → UNMEASURED
  screening latencies: sig_trd_c1 = 41.5 ms · sig_trd_c4 = 12.0 ms
"""
from __future__ import annotations

import io
import os
import re

from backend.services import execution_analytics as ea

from conftest import TENDAYS, YDAY  # noqa: E402


def _read(*parts) -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, *parts), encoding="utf-8") as fh:
        return fh.read()


def _tpl() -> str:
    return _read("frontend", "templates", "execution.html")


def _css() -> str:
    return _read("frontend", "static", "style.css")


def _shipped(client) -> str:
    """The VISIBLE HTML: rendered, then with `<!-- … -->` comments removed. Jinja
    notes never leave the server; HTML comments DO ship but are not UI, and a
    note explaining why Scanner is absent must not read as Scanner present."""
    html = client.get("/execution").data.decode("utf-8")
    return re.sub(r"<!--.*?-->", " ", html, flags=re.S)


def _month(client, extra=""):
    return client.get("/api/analytics/execution?period=month" + extra).get_json()


# ── THE INSTRUMENTATION GAP — the point of this screen ───────────────────────
def test_unmeasured_stages_are_listed_not_dropped(client):
    """⛔ Dropping Risk/Capital/Exchange would imply the remaining stages account
    for the whole lifecycle. They are present, flagged, and carry a reason."""
    summary = {s["stage"]: s for s in _month(client)["delay_summary"]}
    for stage in ("Risk Delay", "Capital Delay", "Exchange Delay"):
        s = summary[stage]
        assert s["measured"] is False, stage
        assert s["reason"], stage
        assert s["avg_sec"] is None and s["worst_sec"] is None and s["n"] == 0


def test_an_unmeasured_stage_never_acquires_a_number(client):
    """⛔ THE failure this screen exists to prevent: a 0.00 sec risk delay would
    read as 'measured, and it was instant' — the opposite of the truth."""
    for q in ("period=today", "period=week", "period=month", "period=month&status=FAST"):
        for s in client.get("/api/analytics/execution?" + q).get_json()["delay_summary"]:
            if not s["measured"]:
                assert s["avg_sec"] is None, (q, s["stage"])
                assert s["worst_sec"] is None, (q, s["stage"])


def test_the_seven_reference_stages_are_all_present_in_order(client):
    """The reference's analytical intent is preserved: all seven rows, in
    lifecycle order — four measured, three declared as gaps."""
    stages = [s["stage"] for s in _month(client)["delay_summary"]]
    assert stages == ["Screening Delay", "Signal → Order Delay", "Risk Delay",
                      "Capital Delay", "Exchange Delay", "Fill Delay", "Total Delay"]
    measured = [s["measured"] for s in _month(client)["delay_summary"]]
    assert measured == [True, True, False, False, False, True, True]


def test_the_gap_is_stated_on_the_payload_and_on_the_screen(client):
    d = _month(client)
    assert "NOT INSTRUMENTED" in d["instrumentation_note"]
    assert "exchange_timestamp" in d["instrumentation_note"]
    html = _shipped(client)
    assert "not instrumented" in html.lower()


def test_signal_to_order_is_labelled_a_composite(client):
    """It spans validation + risk + capital + create + submit. ⛔ It must never be
    relabelled as any single one of them, which would invent a measurement."""
    d = _month(client)
    assert "COMPOSITE" in d["composite_note"]
    row = [s for s in d["delay_summary"] if s["stage"] == "Signal → Order Delay"][0]
    assert "COMPOSITE" in (row.get("note") or "")
    for banned in ("Risk Time", "Capital Time", "Validation Delay",
                   "Exchange Accept Time", "Order Create Time"):
        assert banned not in _shipped(client), banned


def test_no_warning_is_emitted_for_a_quantity_that_is_not_recorded(client):
    """The reference asks for exchange-delay and broker-delay warnings. Neither
    is recorded, and ⛔ a warning that can never fire reads as an all-clear."""
    d = _month(client)
    kinds = {w["kind"] for w in d["warnings"]}
    assert kinds <= {"TOTAL_DELAY", "FILL_DELAY"}
    assert "never fire" in d["warnings_note"]


# ── SCANNER REMOVED ──────────────────────────────────────────────────────────
def test_scanner_absent_from_the_payload(client):
    d = _month(client)
    assert "per_scanner" not in d
    assert "scanner" not in (d["filters"]["options"] or {})
    assert "scanner" not in d["filters"]["keys"]
    for row in d["rows"]:
        assert "scanner" not in row


def test_scanner_absent_from_the_screen(client):
    html = _shipped(client)
    assert "All Scanners" not in html
    assert "Scanner Execution Ranking" not in html
    assert 'label:"Scanner"' not in html
    body = html.split("exec-page", 1)[1]
    # 30-Aug: the "why" note is GONE, so the bar rises from one mention to ZERO.
    # It existed to explain an EMPTY footprint; that footprint now carries the
    # Intraday & Delivery panel, which makes the note both obsolete and the last
    # piece of user-facing Scanner wording on the screen.
    assert body.count("Scanner") == 0
    assert "Intraday &amp; Delivery Execution Analysis" in body


def test_trade_state_filter_narrows_the_whole_screen(client):
    """TRADE STATE is a SECOND, different axis from Execution Status.

    ⛔ `status` is the DELAY BAND (fast/moderate/slow/unmeasured); `trade_state`
    is the LIFECYCLE position (closed/open/pending/rejected). Both are rendered
    as columns, so both must filter — otherwise "show me only the CLOSED ones"
    is impossible on a screen that displays the column.
    """
    base = _month(client)
    closed = _month(client, "&trade_state=CLOSED")
    assert closed["execution_count"] <= base["execution_count"]
    # every surviving row really is CLOSED - the gate, not just the label
    assert {r["trade_state"] for r in closed["rows"]} <= {"CLOSED"}
    # and it is the SINGLE gate: the rankings narrow with the table, so no panel
    # can describe a different population
    assert sum(r["executions"] for r in closed["per_trade_type"]) == closed["execution_count"]
    assert closed["filters"]["active"]["trade_state"] == "CLOSED"


def test_trade_state_is_offered_as_the_full_vocabulary(client):
    """⛔ Not just the values present this period - the option to isolate REJECTED
    must not vanish on a clean day, which is exactly when it is looked for."""
    opts = _month(client)["filters"]["options"]["trade_state"]
    assert set(opts) >= {"CLOSED", "OPEN", "PENDING", "REJECTED"}
    assert "trade_state" in _month(client)["filters"]["keys"]


def test_trade_state_and_execution_status_are_independent_axes(client):
    """Filtering one must not silently apply the other."""
    # `active` carries only the filters actually SET (falsy values are dropped),
    # so independence means the other key is ABSENT, not present-and-None.
    a = _month(client, "&trade_state=CLOSED")["filters"]["active"]
    b = _month(client, "&status=FAST")["filters"]["active"]
    assert a.get("trade_state") == "CLOSED" and "status" not in a
    assert b.get("status") == "FAST" and "trade_state" not in b


def test_scanner_filter_is_not_silently_accepted(client):
    a = _month(client)
    b = _month(client, "&scanner=gap_fade_long")
    assert a["execution_count"] == b["execution_count"]


def test_scanner_attribution_screen_is_untouched(client):
    assert client.get("/api/scanner-attribution?period=week").get_json()["rows"]


def test_existing_today_scoped_execution_endpoint_is_untouched(client):
    """The G2b-2 `/api/execution` keeps its own contract — Screen 11 is ADDITIVE,
    exactly as /api/analytics/slippage was to /api/slippage."""
    d = client.get("/api/execution").get_json()
    assert d["latency"]["order_to_fill_ms"]["n"] == 8
    assert len(d["execution_log"]) == 2


# ── SCORE COLUMNS follow the 13-Aug ruling ───────────────────────────────────
def test_score_columns_follow_the_13_aug_ruling(client):
    html = _shipped(client)
    assert "Signal Score" not in html
    assert "System Score" in html and "Score Threshold" in html
    scored = [r for r in _month(client)["rows"] if r["system_score"] is not None]
    assert scored
    for r in scored:
        assert r["system_score"] == 72 and r["score_threshold"] == 65


# ── ALIGNMENT BY COLUMN ROLE ─────────────────────────────────────────────────
def test_alignment_is_by_column_role_not_blanket():
    css = _css()
    assert ".exec-page .cap-table th { text-align: center; }" in css
    assert ".exec-page .cap-table th.lbl { text-align: left; }" in css
    assert ".exec-page table td { text-align: center" not in css
    assert ".exec-page .cap-table td { text-align: center" not in css


def test_label_columns_declared_left_and_data_columns_centred():
    t = _tpl()
    cols = re.search(r"COLS:\s*\[(.*?)\n\s*\],", t, re.S).group(1)
    entries = re.findall(r'\{\s*key:"(\w+)",\s*label:"([^"]+)"(.*?)\}', cols, re.S)
    labels = [lbl for _k, lbl, _r in entries]
    assert labels[:6] == ["Trading Date", "Time", "Strategy", "Symbol",
                          "Trade Type", "Direction"]
    assert "Scanner" not in labels
    left = [lbl for _k, lbl, rest in entries if "lbl:true" in rest]
    # Symbol is a LEFT label column (design TXT + the 30-Aug production
    # instruction). `lbl` drives BOTH sides — th gains `lbl`, td drops `ctr` —
    # so the heading and every data cell move together.
    assert left == ["Trading Date", "Strategy", "Symbol"]
    assert 'class="ctr"' in t


def _default_cols() -> list:
    """[(key, label, group, is_label)] parsed from DEFAULT_COLS in source order."""
    block = re.search(r"DEFAULT_COLS:\s*\[(.*?)\n\s*\],\s*\n\s*cols:", _tpl(), re.S).group(1)
    out = []
    for m in re.finditer(r'\{\s*key:"(\w+)",\s*label:"([^"]+)"(.*?)\n?\s*\},', block, re.S):
        rest = m.group(3)
        g = re.search(r'group:"([^"]*)"', rest)
        out.append((m.group(1), m.group(2), g.group(1) if g else "", "lbl:true" in rest))
    return out


def test_every_column_declares_a_group_and_groups_are_contiguous():
    """The band header is computed from the LIVE column order by `groups()`, so
    the invariant to guard is the DATA, not a hard-coded colspan list: every
    column names a band, and in the DEFAULT order each band is one contiguous
    run. ⛔ A band split across two runs would print the same label twice."""
    cols = _default_cols()
    assert len(cols) >= 15
    assert all(g for _k, _l, g, _b in cols), "every column must declare a group"
    seen, runs = [], []
    for _k, _l, g, _b in cols:
        if not runs or runs[-1] != g:
            runs.append(g)
    assert len(runs) == len(set(runs)), ("a group appears in two runs", runs)
    for g in runs:
        seen.append(g)
    assert seen == ["Common info", "Delay analysis (sec)", "Execution",
                    "Lifecycle instants (IST)", "Trade lifecycle"]


def test_the_group_band_is_recomputed_from_the_live_order():
    """⛔ Not a static colspan row: with draggable columns a fixed band would
    mislabel every column the moment one is moved."""
    t = _tpl()
    assert 'x-for="g in groups()"' in t
    assert ':colspan="g.span"' in t
    body = re.search(r"groups\(\)\s*\{(.*?)\n    \},", t, re.S).group(1)
    assert "this.cols" in body and "DEFAULT_COLS" not in body


# ── STATUS BANDS ─────────────────────────────────────────────────────────────
def test_status_bands_are_the_approved_three_plus_unmeasured():
    """BASE = TOTAL delay in seconds. Boundaries checked on both sides."""
    assert ea.classify(0.0) == "FAST"
    assert ea.classify(2.0) == "FAST"           # inclusive upper edge
    assert ea.classify(2.01) == "MODERATE"
    assert ea.classify(5.0) == "MODERATE"
    assert ea.classify(5.01) == "SLOW"
    assert ea.classify(None) == "UNMEASURED"


def test_an_execution_that_never_filled_is_never_fast(client):
    """⛔ A PENDING or REJECTED trade has no total delay. Calling that 'fast'
    would sort the failures to the top of the fastest ranking."""
    rows = {r["trade_id"]: r for r in _month(client)["rows"]}
    assert rows["trd_m1"]["status"] == "UNMEASURED"
    assert rows["trd_m1"]["total_sec"] is None
    assert "no ENTRY order row" in rows["trd_m1"]["remarks"]


def test_fixture_bands_are_exactly_the_arithmetic(client):
    rows = {r["trade_id"]: r for r in _month(client)["rows"]}
    assert rows["trd_w1"]["total_sec"] == 6.2 and rows["trd_w1"]["status"] == "SLOW"
    assert rows["trd_w2"]["total_sec"] == 3.5 and rows["trd_w2"]["status"] == "MODERATE"
    assert rows["trd_c1"]["total_sec"] == 0.97 and rows["trd_c1"]["status"] == "FAST"


def test_the_four_status_counts_partition_the_population(client):
    """A PROPERTY, ⛔ not a magic number — on every period and filter."""
    for q in ("period=today", "period=week", "period=month",
              "period=month&direction=LONG", "period=month&status=SLOW"):
        t = client.get("/api/analytics/execution?" + q).get_json()["totals"]
        assert t["fast"] + t["moderate"] + t["slow"] + t["unmeasured"] == t["total_orders"], q


# ── SCREENING — the one real sub-stage breakdown ─────────────────────────────
def test_screening_delay_comes_from_the_per_step_latencies(client):
    """The two fixture blobs sum to DIFFERENT totals on purpose: a reader that
    returned a constant, or summed the wrong signal's blob, would fail here."""
    rows = {r["trade_id"]: r for r in _month(client)["rows"]}
    assert rows["trd_c1"]["screening_sec"] == 0.042          # 41.5 ms
    assert rows["trd_c4"]["screening_sec"] == 0.012          # 12.0 ms


def test_malformed_screening_json_is_absent_not_zero():
    """⛔ An unparseable measurement is an ABSENT measurement, never a fast one."""
    import sqlite3
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "s.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE screener_results (signal_id TEXT, latencies TEXT, ts TEXT)")
    conn.executemany("INSERT INTO screener_results VALUES(?,?,?)", [
        ("good", '{"a": 5.0, "b": 5.0}', "2026-08-15T10:00:00"),
        ("bad_json", "{not json", "2026-08-15T10:00:00"),
        ("empty", "{}", "2026-08-15T10:00:00"),
        ("null", None, "2026-08-15T10:00:00"),
    ])
    conn.commit()
    conn.close()
    from backend.readers import db_reader
    out = db_reader.screening_latencies({"paths": {"main_db": path}},
                                        ["good", "bad_json", "empty", "null"])
    assert set(out) == {"good"}
    assert out["good"]["total_ms"] == 10.0


# ── ONE filtered population behind every panel ───────────────────────────────
def test_every_panel_describes_the_same_filtered_set(client):
    d = _month(client)
    n = len(d["rows"])
    assert n == d["execution_count"] == d["totals"]["total_orders"]
    assert sum(r["executions"] for r in d["per_strategy"]) == n
    assert sum(r["executions"] for r in d["per_symbol"]) == n
    assert sum(s["count"] for s in d["status_distribution"]) == n
    assert sum(b["orders"] for b in d["distribution"]) == d["totals"]["measured"]
    assert sum(b["executions"] for b in d["trend"]) <= n      # outside-session rows


def test_a_filter_narrows_every_panel_together(client):
    d = _month(client, "&status=SLOW")
    assert d["execution_count"] == 1 and d["execution_count_unfiltered"] == 11
    assert all(r["status"] == "SLOW" for r in d["rows"])
    assert sum(r["executions"] for r in d["per_strategy"]) == 1
    assert sum(b["orders"] for b in d["distribution"]) == 1


def test_filter_options_come_from_the_unfiltered_period(client):
    d = _month(client, "&symbol=BBB")
    assert "AAA" in d["filters"]["options"]["symbol"]
    assert d["execution_count"] == 1 and d["execution_count_unfiltered"] == 11


# ── DELAY DISTRIBUTION ───────────────────────────────────────────────────────
def test_the_five_approved_buckets_always_render(client):
    assert [b["bucket"] for b in _month(client)["distribution"]] == [
        "0-1 sec", "1-2 sec", "2-5 sec", "5-10 sec", "10+ sec"]


def test_buckets_are_a_partition_over_their_boundaries():
    for s, want in ((0.0, "0-1 sec"), (0.999, "0-1 sec"), (1.0, "1-2 sec"),
                    (1.999, "1-2 sec"), (2.0, "2-5 sec"), (4.999, "2-5 sec"),
                    (5.0, "5-10 sec"), (9.999, "5-10 sec"), (10.0, "10+ sec"),
                    (600.0, "10+ sec")):
        assert ea.delay_bucket(s) == want, s
    assert ea.delay_bucket(None) is None


def test_distribution_counts_only_measured_rows(client):
    """⛔ An unmeasured execution has no delay and cannot sit in a delay bucket."""
    d = _month(client)
    assert sum(b["orders"] for b in d["distribution"]) == d["totals"]["measured"]
    assert d["totals"]["measured"] == d["totals"]["total_orders"] - d["totals"]["unmeasured"]


# ── THE TOTAL-VS-PARTS PROPERTY ──────────────────────────────────────────────
def test_total_is_checked_against_its_parts_not_assumed(client):
    """`total_latency_ms` is written independently of the two parts and each is
    clamped to ≥0, so agreement is a PROPERTY TO CHECK. The fixture's rows do
    sum, and any that did not would be reported rather than reconciled."""
    d = _month(client)
    assert d["reconcile"]["unreconciled_rows"] == 0
    for r in d["rows"]:
        if r["parts_reconcile"] is not None:
            assert r["parts_reconcile"] is True
            assert abs(r["total_sec"] - (r["signal_to_order_sec"]
                                         + r["order_to_fill_sec"])) <= 0.002


def test_a_disagreeing_row_is_flagged_rather_than_hidden():
    """The check must be able to go RED — proven directly on the enricher."""
    rows = ea._enrich([{"trade_id": "x", "signal_to_order_ms": 100,
                        "order_to_fill_ms": 100, "total_latency_ms": 5000}], {}, {})
    assert rows[0]["parts_reconcile"] is False
    assert "do not sum" in rows[0]["remarks"]


# ── THROUGHPUT ───────────────────────────────────────────────────────────────
def test_throughput_uses_three_independent_clocks(client):
    """⛔ Not one series divided three ways: signals arrive whether or not an
    order follows. The fixture has MORE signals than orders, which a divided
    series could not produce."""
    d = _month(client)
    tot = d["throughput"]["totals"]
    assert tot["signals"] > tot["created"]
    assert tot["created"] >= tot["filled"]
    assert "independent clocks" in d["throughput_note"].lower()
    for p in d["throughput"]["series"]:
        assert {"minute", "at", "day", "day_index", "signals", "created", "filled",
                "signals_n", "created_n", "filled_n"} == set(p)


def test_total_signals_declares_its_different_denominator(client):
    """Total Signals is whole-population; Total Orders is the filtered trade
    count. ⛔ The two must not be presented as a funnel without saying so."""
    d = _month(client)
    assert d["totals"]["total_signals"] != d["totals"]["total_orders"]
    assert "NOT the filtered trade count" in d["totals"]["signals_basis"]


# ── EXIT TIME + TRADE DURATION (second pass) ─────────────────────────────────
def test_duration_is_measured_from_the_entry_FILL(client):
    """Trade Duration = exit − ENTRY FILL (`trades.entry_time`, which schema.sql
    :141 defines as "when entry was filled"). ⛔ NOT from signal or submit time —
    using either would inflate every duration by the execution delay this screen
    separately reports.

    trd_c1: entry fill 10:06:00 → exit 15:10:00 = 5h04m = 18,240 s."""
    rows = {r["trade_id"]: r for r in _month(client)["rows"]}
    c1 = rows["trd_c1"]
    assert c1["entry_fill_time"] == "10:06:00" and c1["exit_time"] == "15:10:00"
    assert c1["duration_sec"] == 18240.0
    assert c1["duration"] == "05:04:00"
    # and it is NOT the execution delay, nor derived from it
    assert c1["duration_sec"] != c1["total_sec"]


def test_an_open_trade_has_no_exit_and_no_duration(client):
    """⛔ The failure this guards: 00:00:00 on a live position, which would read
    as a trade that exited the instant it filled."""
    rows = [r for r in _month(client)["rows"] if r["trade_state"] == "OPEN"]
    assert rows, "fixture has open trades"
    for r in rows:
        assert r["exit_time"] is None
        assert r["duration_sec"] is None and r["duration"] is None
        assert r["entry_fill_time"], "an OPEN trade still has an entry fill"


def test_trade_state_is_read_not_inferred_from_delay():
    """State and speed are DIFFERENT axes. A row with no delay is not CLOSED, and
    a CLOSED row is not automatically fast."""
    assert ea.trade_state({"exit_time": "2026-08-15T15:00:00"}) == "CLOSED"
    assert ea.trade_state({"entry_status": "REJECTED"}) == "REJECTED"
    assert ea.trade_state({"entry_time": "2026-08-15T10:00:00"}) == "OPEN"
    assert ea.trade_state({"entry_placed_at": "2026-08-15T10:00:00"}) == "PENDING"
    assert ea.trade_state({}) == "UNKNOWN"
    # ⛔ a rejected order must never be reported as OPEN just because it has a fill
    assert ea.trade_state({"entry_status": "REJECTED",
                           "entry_time": "2026-08-15T10:00:00"}) == "REJECTED"


def test_duration_refuses_impossible_and_partial_inputs():
    """⛔ Never a plausible small number from a bad pair."""
    assert ea._duration_sec(None, "2026-08-15T15:00:00") is None
    assert ea._duration_sec("2026-08-15T10:00:00", None) is None
    assert ea._duration_sec("bad", "2026-08-15T15:00:00") is None
    # a negative span is clock skew, ⛔ not a 0-second trade
    assert ea._duration_sec("2026-08-15T15:00:00", "2026-08-15T10:00:00") is None
    assert ea._duration_sec("2026-08-15T10:00:00", "2026-08-15T10:00:05") == 5.0


def test_duration_spans_a_date_boundary_correctly():
    """A delivery trade entered one day and exited the next must produce the FULL
    span. ⛔ The failure this guards: comparing clock times only, which would give
    a negative or a wrapped duration and then be discarded as unmeasurable."""
    d = ea._duration_sec("2026-08-14T15:20:00+05:30", "2026-08-15T09:30:00+05:30")
    assert d == 18 * 3600 + 10 * 60          # 18h10m across midnight
    assert ea.fmt_duration(d) == "18:10:00"


def test_duration_ignores_the_server_locale():
    """Both stamps are IST from the system's own clock, so the difference is a
    plain subtraction — ⛔ never a tz conversion, which would shift one side."""
    same = ea._duration_sec("2026-08-15T10:00:00+05:30", "2026-08-15T11:00:00+05:30")
    naive = ea._duration_sec("2026-08-15T10:00:00", "2026-08-15T11:00:00")
    assert same == naive == 3600.0
    # ⛔ and a MIXED pair is refused rather than silently off by the offset
    assert ea._duration_sec("2026-08-15T10:00:00+05:30", "2026-08-15T11:00:00") is None


def test_rows_are_newest_first(client):
    """§20F: the operator reads the latest execution at the top."""
    rows = _month(client)["rows"]
    keys = [(r["date"] or "", r["time"] or "") for r in rows]
    assert keys == sorted(keys, reverse=True)


def test_duration_format_never_renders_a_zero_for_absent():
    assert ea.fmt_duration(None) is None
    assert ea.fmt_duration(0) == "00:00:00"        # a REAL zero is still shown
    assert ea.fmt_duration(3661) == "01:01:01"
    assert ea.fmt_duration(1.24) == "00:00:01.24"


def test_duration_summary_counts_only_closed_trades(client):
    d = _month(client)
    dur = d["duration"]
    closed = [r for r in d["rows"] if r["duration_sec"] is not None]
    assert dur["closed"] == len(closed) and dur["total"] == len(d["rows"])
    assert dur["closed"] < dur["total"], "fixture has open trades too"
    assert "ENTRY FILL" in dur["basis"]


def test_export_carries_exit_and_duration(client):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(
        client.get("/api/export/execution?period=month").data))
    ws = wb["Executions"]
    header = [c.value for c in ws[1]]
    for col in ("Exit Time", "Exit Reason", "Trade State",
                "Trade Duration", "Trade Duration (sec)"):
        assert col in header, col
    body = list(ws.iter_rows(min_row=2, values_only=True))
    si, di = header.index("Trade State"), header.index("Trade Duration")
    for r in body:
        if r[si] == "OPEN":
            assert r[di] in (None, ""), "an open trade must export no duration"


# ── COLUMN DRAG / REORDER — the established convention ───────────────────────
def test_columns_use_the_projects_existing_drag_convention():
    """⛔ Not a new interaction: Screens 04/05/07 already do this and the same
    handler names, classes and localStorage merge are reused verbatim."""
    t = _tpl()
    for token in ('draggable="true"', "@dragstart=", "@dragover.prevent=",
                  "@dragleave=", "@drop.prevent=", "@dragend=",
                  "onDragStart(", "onDrop(", "onDragEnd()", "initCols()",
                  "saveCols()", "resetCols()", "is-drag", "is-over"):
        assert token in t, token
    assert "screen11.execution.colOrder" in t     # its own versioned key


def test_a_drag_is_not_read_as_a_sort_click():
    """The drag ends with a click on the same th; without the guard every
    reorder would also flip the sort."""
    t = _tpl()
    body = re.search(r"headClick\(key\)\s*\{(.*?)\n    \},", t, re.S).group(1)
    assert "_dragEndAt" in body and "250" in body


def test_reordering_cannot_lose_or_duplicate_a_column():
    """initCols merges a SAVED order with the current set: unknown keys are
    dropped, new columns are appended, and the result is used only when it still
    has every column. ⛔ A stale saved order must not silently hide a column."""
    t = _tpl()
    body = re.search(r"initCols\(\)\s*\{(.*?)\n    \},", t, re.S).group(1)
    assert "indexOf(byKey[k]) === -1" in body          # no duplicates
    assert "next.indexOf(c) === -1" in body            # appends new columns
    assert "next.length === this.DEFAULT_COLS.length" in body   # never partial


def test_panel_level_drag_is_not_invented():
    """⛔ Nothing in this project drags PANELS — no gridstack, no sortable, no
    draggable section anywhere. Implementing it here would be a new page-level
    pattern, not a reused one, so the screen does not pretend to offer it."""
    import glob
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # ⚠️ Scan for USAGE, not for the word: this file's own comment NAMES the
    # libraries while explaining that none is present, and a bare word-scan
    # therefore fails on the very note that documents the decision. Same shape
    # as the retired-score-label guard.
    static = glob.glob(os.path.join(here, "frontend", "static", "*.js"))
    assert static, "the static dir must actually have been found"
    assert not [p for p in static
                if os.path.basename(p).lower().startswith(
                    ("gridstack", "sortable", "muuri", "packery"))]
    for path in glob.glob(os.path.join(here, "frontend", "**", "*.html"), recursive=True):
        txt = open(path, encoding="utf-8", errors="ignore").read()
        for call in ("new GridStack", "GridStack.init", "new Sortable",
                     "Sortable.create"):
            assert call not in txt, (call, path)
    # ⛔ and this screen makes no PANEL draggable either: `draggable` appears
    # only on the table's column headers.
    t = _tpl()
    tags = [m.group(1) for m in re.finditer(r"<(\w+)[^>]*?draggable", t, re.S)]
    assert tags and set(tags) == {"th"}, tags


# ── THROUGHPUT: the second-pass rebuild ──────────────────────────────────────
def test_throughput_axis_is_gap_filled_not_event_dots(client):
    """⛔ THE DEFECT THIS REPLACES: the first version plotted only minutes that
    HAPPENED to have activity and joined them, so a Monday minute sat next to a
    Thursday minute and every point was 1 — a 0/1 sawtooth, not a rate.

    The axis now covers EVERY bucket of the session window on EVERY day, so a
    quiet minute is a real zero and a busy one visibly spikes."""
    d = _month(client)
    s = d["throughput"]["series"]
    zero = [p for p in s if not (p["signals_n"] or p["created_n"] or p["filled_n"])]
    assert zero, "a gap-filled axis must contain quiet buckets"
    assert len(zero) > len(s) - len(zero), "most buckets in this fixture are quiet"
    # contiguous within a day: consecutive buckets differ by exactly bucket_min
    b = d["throughput"]["totals"]["bucket_min"]
    same_day = [p for p in s if p["day"] == s[0]["day"]]
    mins = [int(p["at"][:2]) * 60 + int(p["at"][3:]) for p in same_day]
    assert all(mins[i + 1] - mins[i] == b for i in range(len(mins) - 1))


def test_throughput_never_spans_a_date_boundary(client):
    """⛔ 'Do not connect unrelated event records in a way that implies false
    continuity' — the day is carried on every point so the chart can break the
    line, and days appear in order without interleaving."""
    s = _month(client)["throughput"]["series"]
    days = [p["day"] for p in s]
    assert len(set(days)) > 1, "the month window spans several days"
    firsts = [d for i, d in enumerate(days) if i == 0 or days[i - 1] != d]
    assert firsts == sorted(firsts) and len(firsts) == len(set(firsts))


def test_throughput_is_a_per_minute_rate_whatever_the_bucket(client):
    """The unit stays 'per minute' as the window grows: value = count ÷ bucket."""
    d = _month(client)
    b = d["throughput"]["totals"]["bucket_min"]
    for p in d["throughput"]["series"]:
        # the payload rounds the rate to 4 dp, so compare at that precision
        assert p["signals"] == round(p["signals_n"] / b, 4)
        assert p["created"] == round(p["created_n"] / b, 4)
        assert p["filled"] == round(p["filled_n"] / b, 4)


def test_throughput_bucket_keeps_the_chart_readable(client):
    """The bucket widens with the range so the point count stays bounded — the
    axis must never become thousands of unreadable ticks."""
    for per in ("today", "week", "month"):
        d = client.get("/api/analytics/execution?period=" + per).get_json()
        n = len(d["throughput"]["series"])
        assert 0 < n <= 130, (per, n)
        assert d["throughput"]["totals"]["bucket_min"] >= 1


def test_throughput_counts_are_conserved_by_the_bucketing(client):
    """⛔ Aggregation must not lose or double-count an event. Everything inside
    the session window is in exactly one bucket; anything outside is REPORTED."""
    d = _month(client)
    s, t = d["throughput"]["series"], d["throughput"]["totals"]
    binned = sum(p["signals_n"] + p["created_n"] + p["filled_n"] for p in s)
    assert binned + t["outside_session"] == t["signals"] + t["created"] + t["filled"]


def test_the_chart_breaks_the_throughput_line_between_days():
    t = _tpl()
    body = re.search(r"tpMarkup\(\)\s*\{(.*?)\n    \},", t, re.S).group(1)
    assert "p.day !== day" in body and "flush()" in body


# ── ONE BOTTOM ANALYTICS ZONE ────────────────────────────────────────────────
def test_analytics_live_in_one_zone_not_two_tiers():
    """⛔ The defect this replaces: an upper analytics partition, a tall empty
    region, and a separate lower chart partition."""
    t, css = _tpl(), _css()
    assert 'class="exec-zone"' in t
    assert t.count('class="exec-zone-row') == 2
    # both rows are children of the ONE zone, sharing its gap
    zone = t.split('class="exec-zone"', 1)[1]
    assert zone.index("exec-zone-a") < zone.index("exec-zone-b")
    assert ".exec-page .exec-zone { display: flex; flex-direction: column; gap: 16px;" in css
    # ⛔ the retired two-tier grids must be gone
    for dead in ("exec-row2u", "exec-row3"):
        assert dead not in css and dead not in t, dead


def test_the_rail_holds_the_three_rankings_and_nothing_else():
    """The rail outgrowing the table is what opened the dead column, so what it
    may hold stays pinned. 30-Aug: it is THREE rankings — the Intraday & Delivery
    panel takes the footprint the reference gives Scanner, BETWEEN Strategy and
    Symbol. Warnings and the status guide stay in the bottom zone: those are the
    two that made the rail outgrow the table, and this is the assertion that
    keeps them out."""
    t = _tpl()
    rail = t.split('class="exec-rail"', 1)[1].split("</aside>", 1)[0]
    assert rail.count("<section") == 3
    assert "Strategy Execution Ranking" in rail and "Symbol Execution Ranking" in rail
    assert "Intraday &amp; Delivery Execution Analysis" in rail
    assert "Recent Execution Warnings" not in rail and "Status Guide" not in rail
    # ⛔ Position matters: the replacement must sit where Scanner sat, between the
    # two rankings — not appended after Symbol, which would be a different layout.
    assert (rail.index("Strategy Execution Ranking")
            < rail.index("Intraday &amp; Delivery Execution Analysis")
            < rail.index("Symbol Execution Ranking"))


def test_intraday_delivery_panel_is_real_data_and_keeps_unknown_visible(client):
    """The panel replacing Scanner must be the SAME aggregation as the rankings
    beside it, ⛔ not a second one — and UNKNOWN must stay its own row.

    ⛔ Folding UNKNOWN into Intraday would put a fabricated product into the one
    analysis whose job is to separate the two books; a trade with no ENTRY order
    row has no product at all (see `_trade_type`)."""
    rows = _month(client)["per_trade_type"]
    assert rows, "the panel must render from the execution spine, not a stub"
    labels = {r["pipeline"] for r in rows}
    assert labels <= {"Intraday", "Delivery", "UNKNOWN"}
    # every group carries the same shape the other two rankings do
    for r in rows:
        assert set(r) >= {"pipeline", "executions", "measured",
                          "avg_delay_sec", "worst_delay_sec", "fastest_sec"}
        assert r["executions"] >= 1
    # the split is a PARTITION of the filtered set — nothing invented, nothing lost
    assert sum(r["executions"] for r in rows) == _month(client)["execution_count"]


# ── TREND ────────────────────────────────────────────────────────────────────
def test_trend_uses_the_approved_timeline(client):
    tr = _month(client)["trend"]
    assert [b["at"] for b in tr] == ["09:15", "10:00", "11:00", "12:00", "13:00", "14:00"]
    assert "SIGNAL time" in _month(client)["trend_note"]


def test_an_empty_trend_bucket_is_unobserved(client):
    by = {b["at"]: b for b in _month(client)["trend"]}
    assert by["09:15"]["observed"] is False
    assert by["09:15"]["avg_delay_sec"] is None


def test_the_chart_breaks_the_line_across_a_gap():
    t = _tpl()
    assert "segs.push" in t and "trendMarkup" in t


def test_no_alpine_template_loop_inside_an_svg():
    """⛔ PINNED on Screen 10 and re-asserted here: `<template>` inside `<svg>` is
    parsed into the SVG namespace, so Alpine repeats nothing and the chart is an
    empty box while every test is green."""
    import glob
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for path in glob.glob(os.path.join(here, "frontend", "templates", "*.html")):
        html = open(path, encoding="utf-8").read()
        for svg in re.findall(r"<svg\b.*?</svg>", html, re.S):
            if re.search(r"<template\b", svg):
                offenders.append(os.path.basename(path))
    assert not offenders, sorted(set(offenders))


# ── RANKINGS ─────────────────────────────────────────────────────────────────
def test_rankings_sort_slowest_first_and_sink_the_unmeasured(client):
    """⛔ A group with nothing measured must not sort as if its average were
    zero, which would put it at the fast end of the list."""
    for key in ("per_strategy", "per_symbol"):
        rows = _month(client)[key]
        avgs = [r["avg_delay_sec"] for r in rows]
        measured = [a for a in avgs if a is not None]
        assert measured == sorted(measured, reverse=True), key
        if None in avgs:
            assert avgs.index(None) == len(measured), key


def test_the_timing_table_shows_each_instant_once(client):
    """⛔ `Time` and the reference's `Signal Time` are the SAME value here
    (signals.received_at). Rendering both was a duplicate column on a table that
    was already overflowing — proven identical, not assumed."""
    labels = [lbl for _k, lbl, _g, _b in _default_cols()]
    assert labels.count("Time") == 1
    assert "Signal" not in labels          # the instant column is gone
    # Time IS the signal instant WHEN the signal lands on the trade's own day.
    # ⛔ When it does not, Time must fall back to the trade's creation rather
    # than pair a today date with yesterday's clock — and the divergence is
    # reported in the remarks instead of being silently rendered.
    for r in _month(client)["rows"]:
        if r["signal_time"] and str(r["signal_time"]) != str(r["time"]):
            assert "different day" in (r["remarks"] or ""), r["trade_id"]


def test_trading_date_and_time_are_always_the_same_instant(client):
    """⛔ THE DEFECT THIS GUARDS: a row rendering "2026-08-15  10:00:00" where the
    date came from the trade and the time from a signal received the day before —
    a date and a time that never coexisted."""
    for r in _month(client)["rows"]:
        if r["time"] and r["signal_time"] == r["time"]:
            # the displayed time came from the signal ⇒ the signal is same-day
            assert str(r["signal_received_at"])[:10] == r["date"], r["trade_id"]


def test_rows_sort_by_the_instant_they_display(client):
    """The SQL order mirrors `_row_instant`, so the visible list is monotonic —
    ⛔ a sort key that is a different clock from the column makes the table look
    unsorted to the person reading it."""
    for per in ("today", "week", "month"):
        rows = client.get("/api/analytics/execution?period=" + per).get_json()["rows"]
        keys = [(r["date"] or "", r["time"] or "") for r in rows]
        assert keys == sorted(keys, reverse=True), per


def test_remarks_travel_in_the_export_even_though_the_column_was_folded(client):
    """Folding Remarks into the Status cell must not lose it — the sheet is where
    the full text has room, and an UNMEASURED row is useless without its reason."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(
        client.get("/api/export/execution?period=month").data))
    ws = wb["Executions"]
    header = [c.value for c in ws[1]]
    assert "Remarks" in header and "Signal Time" in header
    col = header.index("Remarks")
    st = header.index("Status")
    body = list(ws.iter_rows(min_row=2, values_only=True))
    unmeasured = [r for r in body if r[st] == "UNMEASURED"]
    assert unmeasured
    for r in unmeasured:
        assert r[col], "an unmeasured row must carry its reason"


def test_symbol_ranking_carries_the_three_approved_metrics(client):
    r = _month(client)["per_symbol"][0]
    for k in ("avg_delay_sec", "worst_delay_sec", "fastest_sec"):
        assert k in r


# ── WARNINGS ─────────────────────────────────────────────────────────────────
def test_warnings_fire_only_above_their_thresholds(client):
    """⭐ trd_w2 is the CONTROL: 3.50 s total (under 5) and 2.60 s fill (under 3)
    ⇒ it is MODERATE and must produce NO warning. Without it, 'every non-fast
    row warns' would pass."""
    d = _month(client)
    assert {(w["kind"], w["symbol"]) for w in d["warnings"]} == {
        ("TOTAL_DELAY", "BBB"), ("FILL_DELAY", "BBB")}
    assert all(w["symbol"] != "CCC" for w in d["warnings"])


# ── EXPORT ───────────────────────────────────────────────────────────────────
def test_export_is_the_filtered_set_and_carries_every_panel(client):
    from openpyxl import load_workbook
    r = client.get("/api/export/execution?period=month&status=SLOW")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.data))
    assert wb.sheetnames == ["Executions", "Delay Summary", "Strategy Ranking",
                             "Symbol Ranking", "Intraday vs Delivery",
                             "Delay Distribution", "Overview"]
    ws = wb["Executions"]
    header = [c.value for c in ws[1]]
    assert header[:6] == ["Trading Date", "Time", "Strategy", "Symbol",
                          "Trade Type", "Direction"]
    assert "Scanner" not in header and "Signal Score" not in header
    body = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(body) == 1
    assert body[0][header.index("Status")] == "SLOW"


def test_export_carries_the_not_instrumented_rows_verbatim(client):
    """⛔ A sheet that quietly omitted them would let a reader total the measured
    stages and believe they account for the whole lifecycle."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(
        client.get("/api/export/execution?period=month").data))
    rows = list(wb["Delay Summary"].iter_rows(min_row=2, values_only=True))
    gaps = [r for r in rows if r[1] == "NOT INSTRUMENTED"]
    assert len(gaps) == 3
    for g in gaps:
        assert g[2] is None and g[3] is None      # avg + worst stay empty
        assert g[5]                               # the reason travels with it


def test_export_agrees_with_the_screen(client):
    from openpyxl import load_workbook
    d = _month(client)
    wb = load_workbook(io.BytesIO(
        client.get("/api/export/execution?period=month").data))
    ov = {r[0]: r[1] for r in wb["Overview"].iter_rows(min_row=2, values_only=True)}
    assert ov["Total Orders"] == d["totals"]["total_orders"]
    assert ov["Total Signals"] == d["totals"]["total_signals"]
    assert ov["Average Execution Delay (sec)"] == d["totals"]["avg_delay_sec"]
    assert wb["Executions"].max_row == len(d["rows"]) + 1
    assert wb["Delay Distribution"].max_row == 6


# ── PERIOD LAYER ─────────────────────────────────────────────────────────────
def test_period_windows_select_the_right_days(client):
    def n(p):
        return client.get("/api/analytics/execution?period=" + p).get_json()["execution_count"]
    assert n("today") == 8                      # 4 closed + 4 open, created today
    assert n("week") == 10                      # + trd_w1, trd_w2
    assert n("month") == 11                     # + trd_m1


def test_custom_range_is_honoured(client):
    d = client.get(f"/api/analytics/execution?period=custom&from={YDAY}&to={YDAY}").get_json()
    assert {r["trade_id"] for r in d["rows"]} == {"trd_w1", "trd_w2"}
    d2 = client.get(
        f"/api/analytics/execution?period=custom&from={TENDAYS}&to={TENDAYS}").get_json()
    assert {r["trade_id"] for r in d2["rows"]} == {"trd_m1"}


def test_no_default_row_cap(client):
    d = _month(client)
    assert d["row_cap"] is None and d["row_cap_applied"] is False


# ── TRADE TYPE: honest UNKNOWN ───────────────────────────────────────────────
def test_trade_type_is_never_invented(client):
    d = _month(client)
    by = {r["trade_id"]: r["trade_type"] for r in d["rows"]}
    assert by["trd_c1"] == "MIS"
    assert by["trd_w1"] == "UNKNOWN"
    assert "UNKNOWN" in d["filters"]["options"]["trade_type"]


# ── THE SCREEN ITSELF ────────────────────────────────────────────────────────
def test_route_renders(client):
    r = client.get("/execution")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "exec-page" in body and "Execution Analytics" in body


def test_real_time_refresh_is_preserved():
    """Re-fetches on the root component's ops-refresh broadcast, and the poll
    path must NOT reset the pager, the sort or the filters."""
    t = _tpl()
    assert '@ops-refresh.window="refresh()"' in t
    body = re.search(r"refresh\(\)\s*\{(.*?)\}", t, re.S).group(1)
    assert "tPage" not in body and "applied" not in body


def test_no_hard_coded_values_in_the_markup():
    t = _tpl()
    for ghost in ("2,146", "1,483", "2.48 sec", "18.65", "0.38 sec", "1,312"):
        assert ghost not in t, ghost


def test_status_colours_are_the_approved_three_plus_grey():
    css = _css()
    assert ".exec-page .exec-FAST       { background: var(--pos-tint);   color: var(--pos); }" in css
    assert ".exec-page .exec-MODERATE   { background: var(--warn-tint);  color: var(--yellow); }" in css
    assert ".exec-page .exec-SLOW       { background: var(--neg-tint);   color: var(--neg); }" in css
    assert ".exec-page .exec-UNMEASURED { background: var(--muted-tint); color: var(--dim); }" in css


def test_donut_is_stroked_not_a_filled_disc():
    assert ".exec-page .pos-donut circle { fill: none; stroke-width: 5; }" in _css()


def test_css_is_scoped_to_this_screen():
    """⛔ No Screen-11 rule may escape `.exec-page` and restyle another screen."""
    block = _css().split("SCREEN 11 — EXECUTION ANALYTICS", 1)[1].split("*/", 1)[1]
    # Bounded at the NEXT screen's banner, ⛔ not run to end-of-file — the same
    # trap that made Screen 10's version of this test fail the moment this block
    # was appended beneath it.
    block = re.split(r"SCREEN \d+ [—-]", block, maxsplit=1)[0]
    block = re.sub(r"/\*.*?\*/", " ", block, flags=re.S)
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", block)
    assert len(rules) > 40
    for selector, _body in rules:
        selector = selector.strip()
        if selector.startswith("@media"):
            continue
        for part in selector.split(","):
            part = part.strip().lstrip("{").strip()
            if part:
                assert part.startswith(".exec-page"), part


def test_other_screens_are_untouched():
    css = _css()
    for sel in (".cap-page .cap-table th { text-align: center; }",
                ".pnl-page .cap-table th { text-align: center; }",
                ".slp-page .cap-table th { text-align: center; }",
                "table td.ctr { text-align: center !important; }"):
        assert sel in css, sel
