"""SCREEN 21 — SCANNER ATTRIBUTION.

The approved design (`gui/21. Scanner_Attribution.png` + `.txt`) is binding for
STRUCTURE; the DATA is this system's.

⭐ THE CENTRAL PROPERTY OF THIS SCREEN: the main table shows **Strategy** and
⛔ NOT a separate **Scanner** column, because the two are 1:1 in this system.
That is MEASURED here against the real `scan_webhook_map.yaml`, ⛔ not asserted:
if a future map ever made one strategy serve two scanners, the measurement test
goes red and the removal has to be re-argued rather than silently inherited.

⭐ THE SECOND PROPERTY: Signals, Accepted and Rejected all share the STORED-
SIGNAL base, and the rejection donut splits exactly the Rejected set — so its
four buckets sum to the Rejected column and to 100%. That is the discipline
Screen 20's donut was corrected to on 16-Aug after it read 36% + 68% = 104%.

⚠️ The shared fixture rejects signals under TWO different structured statuses
(REJECTED_DAILY_LOSS ×3 → Risk Limit, REJECTED_CAPITAL ×2 → Capital Limit) and
ALSO stores an expired and ten duplicate signals, so the bucket assertions have
failing inputs available in both directions: a classifier that swept everything
into one bucket, and one that counted expired/duplicate as rejections, both go
red.
"""
from __future__ import annotations

import io
import os
import re
import sqlite3

import _js_syntax

import yaml

from backend.readers import config_reader
from backend.services import (analytics_period, scanner_attribution,
                              strategy_health, strategy_tower)

from conftest import TODAY, _ts


def _read(*parts) -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, *parts), encoding="utf-8") as fh:
        return fh.read()


def _tpl() -> str:
    return _read("frontend", "templates", "scanner_attribution.html")


def _css() -> str:
    return _read("frontend", "static", "style.css")


def _page(client) -> str:
    html = client.get("/scanner-attribution").data.decode("utf-8")
    return re.sub(r"<!--.*?-->", " ", html, flags=re.S)


def _s(client, qs=""):
    return client.get("/api/scanner-attribution/screen" + qs).get_json()


def _css_block() -> str:
    css = _css()
    block = css[css.index("SCREEN 21 — SCANNER ATTRIBUTION"):]
    return block[:block.index("SCREEN 22")]


def _executable(src: str) -> str:
    """`src` with its docstrings removed.

    ⚠️ NEEDED because the functions under test NAME `signals.rejection_reason`
    in order to forbid reading it. A naive substring check would flag exactly
    the comment that records the rule, and the obvious "fix" would be to delete
    the explanation — which is the wrong thing to lose.
    """
    return re.sub(r'"""(?:.|\n)*?"""', " ", src)


# ═════════════════════════════════════════════════════════════════════════════
# THE BINDING RULE — no Scanner column, and the measurement behind it
# ═════════════════════════════════════════════════════════════════════════════
def test_scanner_and_strategy_are_one_to_one_in_the_production_map():
    """⭐ THE MEASUREMENT THAT JUSTIFIES DROPPING THE COLUMN, taken against the
    REAL production file — ⛔ not the fixture, which is free to be different.

    If this ever goes red, the 1:1 claim no longer holds and the removal has to
    be re-argued rather than silently inherited.
    """
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(here, "config", "scan_webhook_map.yaml")
    if not os.path.isfile(path):                       # deployed tree without config
        return
    with io.open(path, encoding="utf-8") as fh:
        scanners = (yaml.safe_load(fh) or {}).get("scanners") or {}
    strategies = [e.get("strategy") for e in scanners.values() if isinstance(e, dict)]
    assert scanners, "the production scanner map is empty"
    assert len(strategies) == len(set(strategies)), (
        "scanner->strategy is no longer 1:1; the Scanner column removal must be "
        "re-argued: " + str(sorted(strategies)))


def test_there_is_no_scanner_column_anywhere_on_this_screen(client):
    """⛔ THE BINDING RULE (Rama, 16-Aug). The identity shown is Strategy."""
    tpl = _tpl()
    cols = tpl[tpl.index("DEFAULT_COLS:"):tpl.index("SPECIAL:")]
    assert "scanner" not in cols.lower(), cols
    assert '"Scanner"' not in cols

    hdr = scanner_attribution.EXPORT_HEADER
    assert "Scanner" not in hdr, hdr
    assert "Strategy" in hdr


def test_the_scanner_identity_is_preserved_where_it_belongs(gui_config):
    """⭐ REMOVED FROM THE TABLE IS NOT LOST. It is in the payload, in the
    SCANNER MAPPING panel and on the mapping export sheet."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    assert any(r["scanners"] for r in p["rows"]), "no row carries its scanner set"
    assert p["mapping"]["rows"], "the mapping panel is empty"
    assert all("scanner" in m for m in p["mapping"]["rows"])
    sheets = scanner_attribution.export_sheets(p)
    titles = [t for t, _h, _r in sheets]
    assert "Scanner Mapping" in titles, titles


def test_the_table_columns_are_the_approved_ones_in_the_approved_order():
    """The artwork's order with `Scanner` removed and `Strategy (Primary)`
    renamed — ⛔ nothing else moved, and `Health` keeps its place."""
    tpl = _tpl()
    block = tpl[tpl.index("DEFAULT_COLS:"):tpl.index("SPECIAL:")]
    labels = re.findall(r'label:\s*"([^"]+)"', block)
    assert labels == ["Rank", "Strategy", "Health", "Signals", "Accepted",
                      "Rejected", "Orders", "Trades", "Win %", "Profit Factor",
                      "Net P&L", "Quality Score", "Trend"], labels


def test_the_export_header_matches_the_table_columns():
    tpl = _tpl()
    block = tpl[tpl.index("DEFAULT_COLS:"):tpl.index("SPECIAL:")]
    labels = re.findall(r'label:\s*"([^"]+)"', block)
    assert list(scanner_attribution.EXPORT_HEADER) == labels


def test_the_column_widths_are_bound_to_the_column_key():
    """⛔ Widths bound to a POSITION get reassigned by a drag."""
    tpl = _tpl()
    block = tpl[tpl.index("DEFAULT_COLS:"):tpl.index("SPECIAL:")]
    for line in block.splitlines():
        if "key:" in line and "label:" in line:
            assert "w:" in line, line.strip()


# ═════════════════════════════════════════════════════════════════════════════
# ONE POPULATION, ONE BASE
# ═════════════════════════════════════════════════════════════════════════════
def test_the_table_counts_come_from_the_same_tower_screens_03_and_20_use(gui_config):
    """⭐ ONE data path. ⛔ Screens 20 and 21 must not report different signal,
    order or trade counts for the same strategy."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    tower = strategy_tower.build_strategy_tower(gui_config, TODAY)
    by_tower = {r["basic"]["name"]: r for r in tower["rows"]}
    assert by_tower, "the tower produced no rows — the comparison would be vacuous"
    for r in p["rows"]:
        t = by_tower[r["strategy"]]
        assert r["signals"] == int(t["signals"]["stored"])
        assert r["accepted"] == int(t["signals"]["accepted"])
        assert r["rejected"] == int(t["signals"]["rejected"])
        assert r["orders"] == int(t["processing"]["created"])
        assert r["trades"] == int(t["trading"]["open"]) + int(t["trading"]["closed"])


def test_health_is_the_same_state_screen_20_shows(gui_config):
    """⛔ ONE state machine. Two screens calling the same strategy Healthy and
    Warning at the same instant is worse than either being wrong alone."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    h = strategy_health.build_strategy_health_screen(gui_config)
    by_health = {r["strategy"]: r["state"] for r in h["rows"]}
    assert by_health
    for r in p["rows"]:
        assert r["health"] == by_health[r["strategy"]], r["strategy"]


def test_the_five_health_states_are_the_approved_ones(gui_config):
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    assert list(p["health_states"]) == ["Healthy", "Quiet", "Warning",
                                        "Silent", "Disabled"]
    for r in p["rows"]:
        assert r["health"] in p["health_states"], r["health"]


def test_the_fixture_exercises_more_than_one_health_state(gui_config):
    """⭐ A fixture where every row is Healthy could not tell a working state
    machine from one that returns a constant."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    assert len({r["health"] for r in p["rows"]}) > 1, \
        [r["health"] for r in p["rows"]]


def test_signals_accepted_and_rejected_share_the_stored_base(gui_config):
    """⭐ ONE BASE. Accepted + Rejected + Duplicated + Expired == Signals,
    exactly — ⛔ the four are the only outcomes of a stored signal."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    for r in p["rows"]:
        assert (r["accepted"] + r["rejected"] + r["duplicated"] + r["expired"]
                == r["signals"]), r


def test_the_webhook_intake_count_is_carried_separately(gui_config):
    """⭐ `received` is the INTAKE count and is a different quantity from
    `signals`; it travels under its own name so nothing conflates them."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    assert all("received" in r for r in p["rows"])
    assert any(r["received"] != r["signals"] for r in p["rows"]), \
        "the fixture makes intake and stored identical — the distinction is vacuous"


# ═════════════════════════════════════════════════════════════════════════════
# REJECTION ANALYSIS — structured classification, one base, 100%
# ═════════════════════════════════════════════════════════════════════════════
def test_the_four_approved_buckets_are_always_present_and_in_order(gui_config):
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    labels = [b["label"] for b in p["rejection_analysis"]["buckets"]]
    assert labels == ["Low Score", "Risk Limit", "Capital Limit", "Other Reasons"]


def test_the_buckets_sum_to_the_rejected_total_and_to_100_percent(gui_config):
    """⛔ THE DEFECT THIS PINS: a donut whose slices do not share one base reads
    36% + 68% = 104% (Screen 20, corrected 16-Aug)."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    rj = p["rejection_analysis"]
    assert rj["total"] > 0, "no rejections in the fixture — the check is vacuous"
    assert sum(b["n"] for b in rj["buckets"]) == rj["total"]
    assert abs(sum(b["pct"] for b in rj["buckets"]) - 100.0) < 0.05


def test_the_donut_total_equals_the_sum_of_the_rejected_column(gui_config):
    """⭐ The panel and the table describe ONE set."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    assert p["rejection_analysis"]["total"] == sum(r["rejected"] for r in p["rows"])


def test_duplicate_and_expired_are_not_counted_as_rejections(gui_config):
    """⛔ They are separate outcomes of the same base. The fixture seeds 10
    DUPLICATE and 1 REJECTED_EXPIRED, so a classifier that swept them in would
    over-count by 11."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    dup = sum(r["duplicated"] for r in p["rows"])
    exp = sum(r["expired"] for r in p["rows"])
    assert dup == 10 and exp == 1, (dup, exp)
    assert p["rejection_analysis"]["total"] == 5, p["rejection_analysis"]


def test_classification_is_by_structured_status_not_free_text():
    """⛔ `signals.rejection_reason` is FREE TEXT and branching on it is the
    defect `reports/signal_status.py` exists to make impossible — it once
    reported 1,189 capital rejections when the true count was 0."""
    src = _read("backend", "services", "scanner_attribution.py")
    body = src[src.index("def _reject_bucket"):src.index("def _humanise")]
    assert "rejection_reason" not in _executable(body), body
    reader = _read("backend", "readers", "db_reader.py")
    q = reader[reader.index("def signals_rejected_by_status"):]
    q = q[:q.index("def position_reconciliation_latest")]
    assert "rejection_reason" not in _executable(q), q
    # ⭐ AND THE GUARD CAN GO RED: the field name IS present in the prose of
    # both, so a stripper that returned everything would fail this line.
    assert "rejection_reason" in body and "rejection_reason" in q


def test_the_per_score_rejection_family_collapses():
    """⭐ The score gate emits ONE status PER SCORE. Without collapsing, a real
    day's breakdown fragments into ~40 unnoticeable lines."""
    assert scanner_attribution._family("REJECTED_SCORE_29") == "REJECTED_SCORE"
    assert scanner_attribution._family("REJECTED_SCORE_59") == "REJECTED_SCORE"
    assert scanner_attribution._reject_bucket("REJECTED_SCORE_41") == "Low Score"
    # ⚠️ AND THE COLLAPSED NAME BUCKETS THE SAME WAY. `_reject_bucket` is called
    # with a raw status in one place and with the already-collapsed family in
    # another; before this held, the export's breakdown sheet shipped the row
    # `Low Score | Other Reasons | 9` — the label and the bucket contradicting
    # each other on one line.
    assert scanner_attribution._family("REJECTED_SCORE") == "REJECTED_SCORE"
    assert scanner_attribution._reject_bucket("REJECTED_SCORE") == "Low Score"
    assert scanner_attribution._humanise("REJECTED_SCORE") == "Low Score"
    # ⛔ and it must not swallow a different status that merely starts the same
    assert scanner_attribution._family("REJECTED_SCORER_X") == "REJECTED_SCORER_X"
    assert scanner_attribution._reject_bucket("REJECTED_SCORER_X") == "Other Reasons"


def test_the_bucket_map_uses_the_reader_s_own_status_tuples():
    """⭐ Risk and Capital are the tuples `db_reader` already owns, so Screen
    03's failure strip and this donut cannot disagree about what a risk
    rejection is."""
    from backend.readers import db_reader
    for st in db_reader._RISK_REJECT_STATUSES:
        assert scanner_attribution._reject_bucket(st) == "Risk Limit", st
    for st in db_reader._CAPITAL_REJECT_STATUSES:
        assert scanner_attribution._reject_bucket(st) == "Capital Limit", st


def test_the_fixture_lands_in_more_than_one_bucket(gui_config):
    """⭐ With every rejection in one bucket, a classifier returning a constant
    would pass."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    hit = [b["label"] for b in p["rejection_analysis"]["buckets"] if b["n"]]
    assert len(hit) >= 2, hit


def test_every_named_family_agrees_with_the_bucket_it_is_shown_beside(gui_config):
    """⛔⛔ THE DEFECT THIS PINS, measured in the shipped workbook: the Rejection
    Breakdown sheet carried `Low Score | Other Reasons | 9`, because the family
    column was collapsed to `REJECTED_SCORE` while the bucket beside it was
    classified from a pattern that only matched `REJECTED_SCORE_<n>`. A label
    and its own category contradicting each other on one line is worse than
    either being missing."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    by_bucket = {b["label"]: b["n"] for b in p["rejection_analysis"]["buckets"]}
    rolled = {}
    for f in p["rejection_analysis"]["families"]:
        rolled[f["bucket"]] = rolled.get(f["bucket"], 0) + f["n"]
    assert rolled, "no rejection families — the check is vacuous"
    for bucket, n in rolled.items():
        assert by_bucket[bucket] == n, (bucket, n, by_bucket)
    # and the export sheet carries the same pairing
    sheets = {t: rows for t, _h, rows in scanner_attribution.export_sheets(p)}
    for label, bucket, _n in sheets["Rejection Breakdown"]:
        if label == "Low Score":
            assert bucket == "Low Score", (label, bucket)


def test_other_reasons_never_hides_a_named_status(gui_config):
    """⭐ Every status inside the buckets is named with its own count."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    rj = p["rejection_analysis"]
    assert sum(f["n"] for f in rj["families"]) == rj["total"]
    assert all(f["label"] and f["bucket"] for f in rj["families"])


def test_a_source_with_no_rejections_has_no_top_reason(gui_config):
    """⛔ None, not a fabricated leader."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    quiet = [r for r in p["rows"] if r["rejected"] == 0]
    assert quiet, "the fixture has no clean source — the check is vacuous"
    assert all(r["top_reject_reason"] is None for r in quiet)
    loud = [r for r in p["rows"] if r["rejected"] > 0]
    assert loud and all(r["top_reject_reason"] for r in loud)


# ═════════════════════════════════════════════════════════════════════════════
# THE FUNNEL
# ═════════════════════════════════════════════════════════════════════════════
def test_the_funnel_is_the_four_approved_stages_over_one_base(gui_config):
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    f = p["funnel"]
    assert [s["label"] for s in f["stages"]] == ["Signals", "Accepted",
                                                 "Orders", "Trades"]
    assert f["stages"][0]["pct"] in (100.0, None)
    total = sum(r["signals"] for r in p["rows"])
    for s in f["stages"]:
        if total:
            assert abs(s["pct"] - 100.0 * s["n"] / total) < 0.02, s


def test_the_funnel_totals_equal_the_table_column_totals(gui_config):
    """⭐ ONE set of numbers on the screen — the funnel is the table, summed."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    got = {s["label"]: s["n"] for s in p["funnel"]["stages"]}
    assert got["Signals"] == sum(r["signals"] for r in p["rows"])
    assert got["Accepted"] == sum(r["accepted"] for r in p["rows"])
    assert got["Orders"] == sum(r["orders"] for r in p["rows"])
    assert got["Trades"] == sum(r["trades"] for r in p["rows"])


def test_a_non_monotonic_funnel_is_flagged_and_explained(gui_config):
    """⛔ A later stage CAN exceed an earlier one — a trade opened today from
    yesterday's signal has no signal above it in today's window. The screen must
    SAY so rather than draw a tidy funnel over it.

    ⭐ The shared fixture reproduces exactly that (its trades link to
    YESTERDAY-dated signals), so this check has a real failing input.
    """
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    f = p["funnel"]
    counts = [s["n"] for s in f["stages"]]
    mono = all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1))
    assert f["monotonic"] == mono
    if not mono:
        assert f["carryover_note"], "a non-monotonic funnel with no explanation"
    else:
        assert f["carryover_note"] is None


# ═════════════════════════════════════════════════════════════════════════════
# QUALITY SCORE
# ═════════════════════════════════════════════════════════════════════════════
def test_the_score_weights_are_the_artwork_s_four_at_25_and_total_100():
    w = scanner_attribution.SCORE_WEIGHTS
    assert [k for k, _l, _p in w] == ["acceptance_rate", "trade_conversion",
                                      "win_rate", "profitability"]
    assert all(p == 25 for _k, _l, p in w)
    assert sum(p for _k, _l, p in w) == 100


def test_a_source_with_no_signals_scores_none_not_zero(gui_config):
    """⭐ Zero is a measurement ("it produced signals and none were any good");
    None is the absence of one."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    silent = [r for r in p["rows"] if r["signals"] == 0]
    assert silent, "the fixture has no silent source — the check is vacuous"
    assert all(r["quality_score"] is None for r in silent)
    assert all(r["quality_reason"] for r in silent)


def test_every_score_is_within_its_own_bounds(gui_config):
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    for r in p["rows"]:
        if r["quality_score"] is None:
            continue
        assert 0 <= r["quality_score"] <= 100, r
        for k, v in r["quality_parts"].items():
            if v is not None:
                assert 0 <= v <= 100, (r["strategy"], k, v)


def test_the_score_is_reproducible_from_its_published_parts(gui_config):
    """⭐ Every row publishes its components; the composite must be exactly the
    weighted sum of them, with an absent component contributing 0."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    weights = {k: w for k, _l, w in scanner_attribution.SCORE_WEIGHTS}
    checked = 0
    for r in p["rows"]:
        if r["quality_score"] is None:
            continue
        total = sum((r["quality_parts"].get(k) or 0.0) * w / 100.0
                    for k, w in weights.items())
        assert r["quality_score"] == int(round(total)), r
        checked += 1
    assert checked, "no scored row — the check is vacuous"


def test_trade_conversion_cannot_exceed_its_own_ceiling(gui_config):
    """⛔ A re-entry on one accepted signal must not push a 0-100 component past
    100 and silently inflate the composite."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    for r in p["rows"]:
        v = (r["quality_parts"] or {}).get("trade_conversion")
        if v is not None:
            assert v <= 100.0, r


def test_the_quality_gauge_excludes_unscored_sources_from_its_mean(gui_config):
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    q = p["quality"]
    scored = [r["quality_score"] for r in p["rows"] if r["quality_score"] is not None]
    assert q["scored"] == len(scored)
    assert q["unscored"] == len(p["rows"]) - len(scored)
    if scored:
        assert q["score"] == int(round(sum(scored) / len(scored)))
    else:
        assert q["score"] is None


# ═════════════════════════════════════════════════════════════════════════════
# PROFITABILITY, RANKING, TREND, KPI
# ═════════════════════════════════════════════════════════════════════════════
def test_fleet_roi_and_profit_factor_are_recomputed_from_sums(gui_config):
    """⛔ NOT the mean of per-source ratios: the mean of ratios is not the ratio
    of the sums, and printing one under the other's name is how a screen starts
    disagreeing with its own table."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    prof = p["profitability"]
    net = sum(float(r["net"] or 0.0) for r in p["rows"])
    deployed = sum(float(r["capital_used_today"] or 0.0) for r in p["rows"])
    win = sum(float(r["win_sum"] or 0.0) for r in p["rows"])
    loss = abs(sum(float(r["loss_sum"] or 0.0) for r in p["rows"]))
    assert abs(prof["net"] - round(net, 2)) < 0.01
    if deployed > 0:
        assert abs(prof["roi_pct"] - round(100.0 * net / deployed, 2)) < 0.01
    if loss > 0:
        assert abs(prof["profit_factor"] - round(win / loss, 2)) < 0.01
    assert prof["roi_base"] and prof["profit_factor_base"]


def test_the_five_approved_ranking_modes_exist_and_each_one_sorts(gui_config):
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    assert [m["key"] for m in p["modes"]] == ["net_pnl", "roi", "win_rate",
                                              "profit_factor", "trade_count"]
    for key, _label in scanner_attribution.MODES:
        rows = p["ranking"][key]
        metric = scanner_attribution._MODE_METRIC[key]
        vals = [r[metric] for r in rows]
        assert vals == sorted(vals, reverse=True), (key, vals)
        assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
        assert len(rows) <= 3


def test_the_ranking_tabs_do_not_reorder_the_main_table(gui_config):
    """⭐ The main table is ranked by Net P&L, always — that is the order the
    artwork draws, and switching a ranking tab must not reshuffle it."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    assert p["table_order"] == "net_pnl"
    nets = [r["net"] for r in p["rows"] if r["net"] is not None]
    assert nets == sorted(nets, reverse=True), nets
    assert "mode" not in p, "a server-side ranking mode would move the table"


def test_rank_1_actually_leads_net_pnl(gui_config):
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    rows = p["rows"]
    assert rows and rows[0]["rank"] == 1
    assert rows[0]["net"] == max(r["net"] for r in rows if r["net"] is not None)


def test_trend_states_are_only_the_approved_three_or_absent(gui_config):
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    for r in p["rows"]:
        assert r["trend"] in (None, "Improving", "Stable", "Declining"), r


def test_the_trend_summary_counts_the_rows_it_is_shown_beside(gui_config):
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    ts = p["trend_summary"]
    for state in ("Improving", "Stable", "Declining"):
        assert ts[state] == sum(1 for r in p["rows"] if r["trend"] == state)
    assert ts["unavailable"] == sum(1 for r in p["rows"] if r["trend"] is None)


def test_the_trend_uses_screen_20s_own_function(gui_config):
    """⛔ ONE definition. Two screens calling the same strategy Improving and
    Declining at the same instant is a contradiction, not a nuance."""
    src = _read("backend", "services", "scanner_attribution.py")
    assert "strategy_health._trend(" in src
    assert scanner_attribution.ACTIVITY_DAYS is strategy_health.ACTIVITY_DAYS


def test_the_six_approved_kpi_cards_are_present_in_order():
    tpl = _tpl()
    block = tpl[tpl.index("KPIS: ["):tpl.index("TRENDS: [")]
    labels = re.findall(r'label:\s*"([^"]+)"', block)
    assert labels == ["TOTAL SCANNERS", "ACTIVE SCANNERS", "BEST SCANNER",
                      "WORST SCANNER", "HIGHEST WIN RATE",
                      "HIGHEST PROFIT FACTOR"], labels


def test_total_scanners_counts_the_configured_registry(gui_config):
    """⭐ The artwork's own caption says "All configured scanners"."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    reg = config_reader.get_scanner_registry(gui_config)
    assert p["kpi"]["total_scanners"] == len(reg) > 0


def test_active_scanners_is_scanner_grain_on_both_sides(gui_config):
    """⛔⛔ NOT `total - disabled_strategies`. The map permits N:1, so a
    subtraction would take a STRATEGY count off a SCANNER count.

    ⭐ The shared fixture is exactly that hazard: it maps SIX scanners onto FIVE
    strategies (`gap_fade_long_alt` → `gap_fade_long`), so the naive
    subtraction and the correct count differ whenever the shared strategy is
    disabled — and here they still differ in base, which the assertion pins.
    """
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    reg = config_reader.get_scanner_registry(gui_config)
    health = {r["strategy"]: r["health"] for r in p["rows"]}
    want = sum(1 for e in reg if health.get(e["strategy"]) != "Disabled")
    assert p["kpi"]["active_scanners"] == want
    assert p["kpi"]["active_scanners"] + p["kpi"]["disabled_scanners"] == len(reg)


def test_the_fixture_map_is_n_to_1_so_the_grain_check_is_not_vacuous(gui_config):
    reg = config_reader.get_scanner_registry(gui_config)
    strategies = [e["strategy"] for e in reg]
    assert len(strategies) > len(set(strategies)), \
        "the fixture map is 1:1, so the scanner/strategy grain distinction is untested"


def test_kpi_cards_name_a_real_row_or_nothing(gui_config):
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    names = {r["strategy"] for r in p["rows"]}
    for key in ("best", "worst", "highest_win_rate", "highest_profit_factor"):
        card = p["kpi"][key]
        if card is not None:
            assert card["strategy"] in names, (key, card)


# ═════════════════════════════════════════════════════════════════════════════
# SCANNER MAPPING — real URLs, never constructed
# ═════════════════════════════════════════════════════════════════════════════
def test_mapping_urls_come_from_the_yaml_and_are_never_constructed(gui_config):
    """⭐ Read here from the file directly, so a change in the service cannot
    make this pass."""
    path = os.path.join(gui_config["paths"]["config_dir"], "scan_webhook_map.yaml")
    with io.open(path, encoding="utf-8") as fh:
        on_disk = (yaml.safe_load(fh) or {}).get("scanners") or {}
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    rows = {m["scanner"]: m for m in p["mapping"]["rows"]}
    assert rows, "no mapping rows"
    for name, entry in on_disk.items():
        assert rows[name]["url"] == entry.get("chartink_url"), name
        assert rows[name]["strategy"] == entry.get("strategy"), name


def test_a_scanner_without_a_url_reports_unavailable_not_a_guess(gui_config, tmp_path):
    """⛔ A plausible-looking address the operator might click is worse than an
    em-dash."""
    cfg_dir = gui_config["paths"]["config_dir"]
    path = os.path.join(cfg_dir, "scan_webhook_map.yaml")
    with io.open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    doc["scanners"]["no_url_scanner"] = {"strategy": "gap_fade_long"}
    with io.open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False)
    reg = config_reader.get_scanner_registry(gui_config)
    row = next(e for e in reg if e["scanner"] == "no_url_scanner")
    assert row["chartink_url"] is None
    assert row["strategy"] == "gap_fade_long"


def test_a_mapping_to_a_missing_strategy_is_reported(gui_config):
    """⭐ A mapping pointing at a strategy with no YAML is a real finding."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    for m in p["mapping"]["rows"]:
        assert isinstance(m["strategy_configured"], bool)
    assert p["mapping"]["source"].endswith("scan_webhook_map.yaml")


def test_the_legacy_scan_webhook_map_reader_is_untouched(gui_config):
    """⭐ ADDITIVE: the existing {scanner: strategy} reader keeps its shape."""
    m = config_reader.get_scan_webhook_map(gui_config)
    assert isinstance(m, dict) and m
    assert all(isinstance(v, str) for v in m.values())


# ═════════════════════════════════════════════════════════════════════════════
# THE PANEL WITH NO SOURCE
# ═════════════════════════════════════════════════════════════════════════════
def test_the_health_timeline_reports_its_gap_and_invents_nothing(gui_config):
    """⛔ Nothing stores a per-source STATE HISTORY. The artwork itself marks
    the panel "(Example: …)" and those values are NOT rendered as data."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    assert p["health_timeline"] == {"available": False, "rows": []}
    gap = p["gaps"]["health_timeline"]
    assert gap["measured"] is False and gap["reason"] and gap["short"]


def test_the_artworks_example_values_are_not_in_the_template():
    """⛔ The artwork's HEALTH TIMELINE draws 09:15 / 11:30 / 01:00 / 02:30 /
    03:45 as an example. None of them may ship as data."""
    tpl = _tpl()
    # ⛔ Anchored on the PANEL, ⛔ not on the first occurrence of the words: the
    # header comment names the panel too, and slicing from there would measure
    # the comment instead of the markup.
    panel = tpl[tpl.index('<section class="panel sca-timeline">'):]
    panel = panel[:panel.index("</section>")]
    assert "HEALTH TIMELINE" in panel
    for stamp in ("09:15", "11:30", "01:00", "02:30", "03:45"):
        assert stamp not in panel, stamp
    assert "NOT INSTRUMENTED" in panel


# ═════════════════════════════════════════════════════════════════════════════
# EXPORT
# ═════════════════════════════════════════════════════════════════════════════
def test_the_export_writes_the_same_rows_the_table_shows(gui_config):
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    rows = scanner_attribution.export_rows(p)
    assert len(rows) == len(p["rows"]) + 1
    for out, r in zip(rows[1:], p["rows"]):
        assert out[0] == r["rank"]
        assert out[1] == (r["display_name"] or r["strategy"])


def test_an_unmeasured_export_cell_is_never_blank(gui_config):
    """⛔ A BLANK CELL READS AS ZERO in a spreadsheet."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    for title, header, rows in scanner_attribution.export_sheets(p):
        assert all(h for h in header), title
        for row in rows:
            for cell in row:
                assert cell is not None and cell != "", (title, row)


def test_the_export_is_a_real_parseable_workbook(client):
    from openpyxl import load_workbook
    r = client.get("/api/export/scanner-attribution")
    assert r.status_code == 200
    assert r.data[:4] == b"PK\x03\x04"
    wb = load_workbook(io.BytesIO(r.data))
    assert wb.sheetnames == ["Scanner Attribution", "Scanner Mapping",
                             "Rejection Breakdown"]
    hdr = [c.value for c in wb["Scanner Attribution"][1]]
    assert hdr == list(scanner_attribution.EXPORT_HEADER)
    assert "Scanner" not in hdr


def test_the_export_honours_the_filters_it_is_given(client):
    from openpyxl import load_workbook
    full = client.get("/api/scanner-attribution/screen").get_json()
    disabled = [r for r in full["rows"] if r["health"] == "Disabled"]
    assert disabled, "no Disabled row in the fixture — the check is vacuous"
    r = client.get("/api/export/scanner-attribution?health=Disabled")
    wb = load_workbook(io.BytesIO(r.data))
    ws = wb["Scanner Attribution"]
    assert ws.max_row == len(disabled) + 1, ws.max_row


# ═════════════════════════════════════════════════════════════════════════════
# THE PAGE
# ═════════════════════════════════════════════════════════════════════════════
def test_the_page_and_both_endpoints_are_reachable(client):
    assert client.get("/scanner-attribution").status_code == 200
    assert client.get("/api/scanner-attribution/screen").status_code == 200
    assert client.get("/api/export/scanner-attribution").status_code == 200


def test_the_legacy_scanner_endpoint_still_answers(client):
    """⭐ ADDITIVE — the G5c endpoint keeps its own contract and callers."""
    d = client.get("/api/scanner-attribution").get_json()
    assert "rows" in d and "count" in d
    assert client.get("/api/scanner-attribution").status_code == 200


def test_the_approved_panels_are_all_on_the_page(client):
    page = _page(client)
    for title in ("SCANNER ATTRIBUTION", "SCANNER PERFORMANCE TABLE",
                  "SIGNAL FUNNEL", "REJECTION ANALYSIS",
                  "PROFITABILITY OVERVIEW", "SCANNER MAPPING", "EXPORT",
                  "ACTIVITY METRICS", "SCANNER RANKING", "TREND SUMMARY",
                  "OPPORTUNITY QUALITY SCORE",
                  "SCANNER DRILLDOWN QUICK ACCESS", "HEALTH TIMELINE"):
        assert title in page, title


def test_the_six_drilldown_tiles_are_the_artworks(gui_config):
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    assert [d["label"] for d in p["drilldown"]] == [
        "Overview", "Signals", "Orders", "Trades", "Performance", "Health"]
    assert all(d["href"].startswith("/") for d in p["drilldown"])


def test_the_footer_states_the_one_to_one_and_the_shared_base(client):
    page = _page(client)
    assert "1:1" in page
    assert "stored-signal base" in page


def test_the_screen_has_no_write_path(client):
    """L4 — this dashboard never writes to the trading system."""
    page = _page(client)
    root = page[page.index('<div class="sca-page"'):]
    assert "<form" not in root.lower()
    assert 'method="post"' not in root.lower()
    assert 'method: "post"' not in root.lower()
    assert '"POST"' not in root and "'POST'" not in root


def test_the_screen_is_not_period_scoped(client):
    """⚠️ The artwork labels three panels "(TODAY)" and draws no date filter."""
    d = _s(client)
    assert "period" not in d and "from" not in d
    assert d["today"]
    # a period argument must not silently change the answer
    assert _s(client, "?period=month")["today"] == d["today"]


def test_no_alpine_x_if_branch_ships_two_root_elements():
    """⛔ An x-if template with two siblings renders only the FIRST — which is
    how every "no value" cell came to render blank on Screen 19."""
    tpl = _tpl()
    for block in re.findall(r'<template x-if="[^"]+">(.*?)</template>', tpl, re.S):
        depth, top = 0, 0
        for close, tag, attrs in re.findall(r"<(/?)(\w+)([^>]*)>", block):
            if close:
                depth -= 1
                continue
            if depth == 0:
                top += 1
            if not attrs.rstrip().endswith("/"):
                depth += 1
        assert top == 1, "an x-if branch has %d root elements: %s" % (top, block[:90])


def test_no_alpine_x_for_template_sits_inside_an_svg():
    """⛔ The HTML parser turns a <template> inside <svg> into an SVG node named
    "template" and it renders NOTHING. Repeated SVG children use x-html."""
    tpl = _tpl()
    for svg in re.findall(r"<svg\b.*?</svg>", tpl, re.S):
        assert "<template" not in svg, svg[:140]


def test_the_screen_css_is_scoped_to_this_page():
    for line in _css_block().splitlines():
        line = line.strip()
        if not line or line.startswith(("/*", "*", "@", "}")) or "{" not in line:
            continue
        selector = line.split("{")[0].strip()
        assert ".sca-page" in selector or "main.content" in selector, selector


def test_the_readability_floor_holds_for_text():
    """⚠️ SVG labels are USER UNITS, not px — the floor is checked against the
    EFFECTIVE size."""
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", _css_block()):
        selector, body = rule.group(1), rule.group(2)
        m = re.search(r"font-size:\s*([\d.]+)px", body)
        if not m:
            continue
        assert float(m.group(1)) >= 12.9, (selector.strip(), m.group(0))


def test_no_one_off_control_sizing_is_introduced():
    """⛔ Height, padding, radius and background come from the SHARED rules."""
    for line in _css_block().splitlines():
        if ".sel" in line and "height" in line:
            raise AssertionError("one-off control sizing: " + line.strip())


def test_the_page_joins_the_shared_token_block():
    """🔴 MEASURED DEFECT on Screens 19/20: a page outside the token block
    renders every `rgba(var(--t-*), …)` tint fully transparent."""
    css = _css()
    block = css[css.index("--t-blue: 56,139,253"):]
    head = css[:css.index("--t-blue: 56,139,253")]
    selector = head[head.rindex("\n.dash-page"):]
    assert ".sca-page" in selector, selector
    assert "--t-green" in block[:400]


def test_no_javascript_string_literal_spans_a_newline():
    bad = _js_syntax.string_literals_spanning_a_newline(
        _js_syntax.script_of(_tpl()))
    assert not bad, bad


def test_the_page_script_has_balanced_braces():
    assert _js_syntax.unbalanced_braces(_js_syntax.script_of(_tpl())) == 0


def test_the_js_guard_can_go_red():
    """⭐ A check with no failing input manufactures confidence (V5)."""
    assert _js_syntax.string_literals_spanning_a_newline('var a = "x\ny";')
    assert _js_syntax.unbalanced_braces("function f() { return 1;") == 1


# ═════════════════════════════════════════════════════════════════════════════
# THE EMPTY DAY
# ═════════════════════════════════════════════════════════════════════════════
def test_an_empty_day_produces_no_invented_numbers(gui_config):
    """⛔ With nothing recorded, every derived figure must be None or 0 — ⛔ never
    a flattering default."""
    conn = sqlite3.connect(gui_config["paths"]["main_db"])
    for tbl in ("signals", "trades", "orders", "webhook_audit"):
        conn.execute("DELETE FROM " + tbl)
    conn.commit()
    conn.close()

    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    assert p["rejection_analysis"]["total"] == 0
    assert all(b["n"] == 0 for b in p["rejection_analysis"]["buckets"])
    assert all(b["pct"] is None for b in p["rejection_analysis"]["buckets"])
    assert p["quality"]["score"] is None
    assert p["profitability"]["roi_pct"] is None
    assert p["profitability"]["profit_factor"] is None
    assert all(r["quality_score"] is None for r in p["rows"])
    for key in ("best", "worst", "highest_win_rate", "highest_profit_factor"):
        assert p["kpi"][key] is None, key
    # the configured registry is still real
    assert p["kpi"]["total_scanners"] > 0
    assert p["mapping"]["count"] > 0


def test_percentages_carry_their_base_in_the_payload(gui_config):
    """⛔ EVERY PERCENTAGE CARRIES ITS BASE OR IT IS NOT A NUMBER."""
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    assert p["funnel"]["base"]
    assert p["rejection_analysis"]["base"]
    assert p["quality"]["base"]
    assert p["profitability"]["roi_base"]
    assert analytics_period is not None      # the shared period layer is imported


# ═════════════════════════════════════════════════════════════════════════════
# THE 16-AUG OLD-DESIGN COMPLIANCE GATE — D1 · D2 · D3 · D4
# Each of these pins a deviation the artwork comparison actually found, so the
# artwork's own geometry cannot drift back out.
# ═════════════════════════════════════════════════════════════════════════════
def test_d1_the_drilldown_is_one_row_of_six():
    """⛔ THE DEFECT THIS PINS: the panel rendered 3×2 at every width, including
    1920. The artwork draws SIX tiles in ONE row, and approved Screen 20 already
    renders the identical panel that way."""
    block = _css_block()
    rule = block[block.index(".sca-page .sca-drill-grid"):]
    rule = rule[:rule.index("}")]
    # ⭐ SIX COLUMNS, however the track is expressed — the property is "one row
    # of six", ⛔ not a literal `1fr`. The tracks are content-sized at the design
    # width so `Performance` renders whole; a narrow-width media query below
    # switches them back to equal.
    assert re.search(r"grid-template-columns:\s*repeat\(6,", rule), rule
    assert "repeat(3" not in rule, rule
    # and the narrow fallback is still six across, never a second row
    narrow = block[block.index("@media (max-width: 1400px)"):]
    narrow = narrow[:narrow.index("\n}")]
    assert "repeat(6," in narrow, narrow
    # ⛔ 3×2 must never come back at ANY width. ⚠️ Checked on the DRILLDOWN
    # rules only — the KPI strip legitimately uses `repeat(3, …)` below 1000px,
    # and a whole-block search would flag that instead.
    for rule in re.findall(r"\.sca-drill-grid\s*\{[^}]*\}", block):
        assert "repeat(3," not in rule, rule
        assert "repeat(6," in rule, rule


def test_d1_the_drilldown_geometry_matches_approved_screen_20():
    """⭐ Asserted against Screen 20's OWN lines, not against numbers copied
    here — if either screen's tile changes size the two stop matching.

    ⚠️ HORIZONTAL PADDING IS DELIBERATELY OUT OF THE EQUALITY SET: Screen 21's
    drilldown panel is the narrower of the two (it is one of three in its row,
    not one of two), and the six approved labels need 292px of text at the
    binding 13px floor. 3px rather than 4px per side is what buys `Performance`
    its last few pixels. Every other dimension matches Screen 20 exactly.
    """
    css = _css()
    def _decl(sel):
        i = css.index(sel)
        return " ".join(css[i:css.index("}", i)].split())
    for prop in ("gap: 9px", "min-height: 74px", "border-radius: 9px",
                 "border: 1px solid var(--border-soft)"):
        assert prop in _decl(".sca-page .sca-tile ") or prop in _decl(".sca-page .sca-drill-grid"), prop
        assert prop in _decl(".sh-page .sh-tile ") or prop in _decl(".sh-page .sh-drill-grid"), prop
    # the vertical padding — the part that sets the card's height — is identical
    for sel in (".sca-page .sca-tile ", ".sh-page .sh-tile "):
        assert "padding: 13px " in _decl(sel), sel


def test_d1_every_tile_carries_the_artworks_tone_and_the_first_is_active(gui_config):
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    d = p["drilldown"]
    assert [x["label"] for x in d] == ["Overview", "Signals", "Orders",
                                       "Trades", "Performance", "Health"]
    assert [x["tone"] for x in d] == ["blue", "purple", "amber", "green",
                                      "blue", "red"]
    assert [x["active"] for x in d] == [True, False, False, False, False, False]


def test_d1_the_tones_use_existing_theme_tokens_only():
    """⛔ NO NEW COLOUR VALUE (global rule 2). Each tone resolves to a token the
    theme already defines."""
    block = _css_block()
    tones = re.findall(r"\.sca-t-(\w+)\s+\.sca-tile-ico \{ color: var\((--[\w-]+)\); \}", block)
    assert dict(tones) == {"blue": "--blue", "purple": "--purple",
                           "amber": "--yellow", "green": "--pos", "red": "--neg"}, tones
    # ⚠️ the palette packs several tokens onto one line, so a definition is not
    # anchored to a line start — search for the declaration itself.
    css = _css()
    for _t, token in tones:
        assert re.search(re.escape(token) + r":\s*#[0-9a-fA-F]{3,8}", css), token


def test_d2_the_mapping_panel_holds_its_five_row_footprint(gui_config):
    """⛔ THE DEFECT THIS PINS: every row rendered, uncapped. Production has 16
    scanners and the panel grew ~216px, pushing EXPORT down the rail."""
    assert scanner_attribution.MAPPING_PREVIEW == 5
    p = scanner_attribution.build_scanner_attribution_screen(gui_config)
    m = p["mapping"]
    assert m["preview"] == 5
    assert m["hidden"] == max(0, m["count"] - 5)
    # ⭐ NOTHING IS WITHHELD — every row is still in the payload and the export
    assert len(m["rows"]) == m["count"]
    sheets = {t: rows for t, _h, rows in scanner_attribution.export_sheets(p)}
    assert len(sheets["Scanner Mapping"]) == m["count"]


def test_d2_revealing_the_rest_cannot_grow_the_panel():
    """⭐ `View All Mappings →` reveals inside a BOUNDED height, so the rail
    geometry is the same in both states."""
    tpl = _tpl()
    assert "View All Mappings" in tpl
    assert "mappingShown()" in tpl
    # ⛔ BOUNDED IN BOTH STATES. Bounding only the expanded one made
    # revealing the rest SHRINK the panel and pull EXPORT up 32px — the
    # same geometry change the cap exists to prevent, in reverse.
    block = _css_block()
    rule = block[block.index(".sca-page .sca-map-wrap {"):]
    rule = rule[:rule.index("}")]
    assert "max-height" in rule and "overflow-y: auto" in rule, rule
    assert ".sca-map-wrap.is-all" not in block, (
        "a state-dependent cap reintroduces the geometry change")


def test_d3_the_footer_uses_the_approved_showing_format():
    """Artwork: `Showing 1 to N of M …`. ⛔ Was `Showing 8 of 8 strategies`."""
    js = _js_syntax.script_of(_tpl())
    body = js[js.index("showing()"):js.index("rejBase()")]
    assert '"Showing " + (n ? 1 : 0) + " to " + n + " of "' in body, body
    assert "strateg" in body
    assert "scanners" not in body


def test_d4_the_trend_count_noun_agrees_with_the_table_identity():
    """⛔ THE DEFECT THIS PINS: `1 Scanners` — the wrong noun for its own base
    (it counts this table's rows, and this table's identity is the strategy),
    and no singular agreement."""
    tpl = _tpl()
    assert "' Scanners'" not in tpl, "the trend count still says Scanners"
    js = _js_syntax.script_of(tpl)
    body = js[js.index("trendLabel(label)"):]
    body = body[:body.index("},")]
    assert '"Strategy", "Strategies"' in body, body


def test_d4_the_panel_titles_keep_the_artworks_scanner_terminology(client):
    """⭐ ONLY the unit noun changed. The artwork's panel headings stay — this
    screen IS Scanner Attribution, and the ruling was about the duplicated
    identity COLUMN, not about renaming the design's own panels."""
    page = _page(client)
    for title in ("SCANNER PERFORMANCE TABLE", "SCANNER RANKING",
                  "SCANNER MAPPING", "SCANNER DRILLDOWN QUICK ACCESS",
                  "SCANNER ATTRIBUTION"):
        assert title in page, title


def test_the_plural_helper_is_used_wherever_a_count_meets_a_noun():
    """⛔ `1 Strategies` reads as a formatting bug and puts the number itself in
    doubt — the rule `strategy_ranking._n()` already records."""
    js = _js_syntax.script_of(_tpl())
    assert "plural(n, one, many)" in js
    assert js.count("this.plural(") >= 3, js.count("this.plural(")


def test_the_drilldown_row_gives_its_panel_room_below_the_design_width():
    """⛔ THE DEFECT THIS PINS, found in the 16-Aug browser review: at 1440 the
    three-up row 3 left DRILLDOWN 264px, so six equal tiles were 31px each and
    every label collapsed to an initial — `O… Si… O… Tr… P… H…`, with Overview
    and Orders indistinguishable.

    ⭐ THE FIX REUSES SCREEN 22's OWN ROW-3 PATTERN — two-up with the last panel
    spanning — rather than inventing one, and is asserted against Screen 22's
    own line so the two cannot drift apart.
    """
    block = _css_block()
    two_up = block[block.index("@media (max-width: 1900px)"):]
    two_up = two_up[:two_up.index("\n}")]
    assert ".sca-page .sca-row3" in two_up, two_up
    assert "minmax(0, 1fr) minmax(0, 1fr)" in two_up, two_up
    assert "> :last-child { grid-column: 1 / -1; }" in two_up, two_up
    # the pattern it reuses, in Screen 22's own CSS
    css = _css()
    assert ".hld-page .hld-row3 > :last-child { grid-column: 1 / -1; }" in css


def test_the_breakpoints_are_ordered_so_the_tiles_never_overflow():
    """⚠️ MEASURED ACROSS A WIDTH SWEEP, ⛔ not assumed. Three-up row 3 only
    reaches the ~407px the six tiles need above ~1868px, so the two-up
    breakpoint must sit ABOVE the width at which the tiles stop compressing —
    otherwise there is a band (measured at 1600: 314px panel, two labels
    overflowing) where content-sized tiles have nowhere to go.
    """
    block = _css_block()
    two_up = block.index("@media (max-width: 1900px)")
    compress = block.index("@media (max-width: 1400px)")
    assert two_up < compress, "the two-up rule must precede the compress rule"
    assert 1900 > 1400, "the two-up breakpoint must be the wider of the two"
