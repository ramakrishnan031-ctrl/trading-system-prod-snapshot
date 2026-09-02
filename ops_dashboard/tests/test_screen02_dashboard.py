"""tests/test_screen02_dashboard.py — SCREEN 02 DASHBOARD ("Mission Control").

The approved artwork is `gui/02. Dashboard.png` + `gui/02. Dashboard.txt`.

Screen 02 answers six questions in fifteen seconds, so every number on it has to
be a MEASUREMENT. What these tests hold, and why each one can go red:

  · RECENT EVENTS reads the STRUCTURED trading feed, ⛔ never `system_events` —
    which holds only STARTUP / SHUTDOWN / KILL_AUTO_CLEARED / CONFIG_DIFF
    (measured on production, 18-Aug-2026), so the artwork's trading rows could
    never have come from it;
  · the SIGNAL / ORDER / RISK / EXIT badges the artwork draws are REACHABLE, and
    the badge comes from the row's own category, ⛔ not from matching its text;
  · a row from another DAY cannot enter a panel captioned Today;
  · no event field can widen the dashboard's grid;
  · SERVICE HEALTH's "LAST UPDATE" is the unit's real state-change stamp, ⛔ never
    the dashboard's own clock, and an unknown stamp says UNAVAILABLE;
  · the pipeline carries the artwork's THIRTEEN labels verbatim, in order, and
    its colour is semantic, ⛔ not a freshness reading;
  · the KPI tones are the artwork's, and a KPI value cannot escape its card;
  · nothing on the screen is set below the spec's 13px floor.
"""
from __future__ import annotations

import inspect
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_TPL = os.path.join(_ROOT, "frontend", "templates", "dashboard.html")
_CSS = os.path.join(_ROOT, "frontend", "static", "style.css")

from backend.api import dashboard as dashboard_api  # noqa: E402
from backend.readers import host_reader  # noqa: E402
from backend.services import live_activity, pipeline_state  # noqa: E402

from conftest import TODAY as TODAY_ISO  # noqa: E402


def _tpl() -> str:
    with open(_TPL, encoding="utf-8") as fh:
        return fh.read()


def _tpl_code() -> str:
    """The template with `//` line comments and `<!-- -->` stripped.

    📌 A source-scanning test must scan what RUNS. This template's own comments
    NAME the helpers that were removed, in order to record why — so a naive
    substring search finds `evCat(` in the very note saying it is gone.
    """
    import re as _re
    text = _re.sub(r"<!--.*?-->", "", _tpl(), flags=_re.S)
    return "\n".join(_re.sub(r"//.*$", "", line) for line in text.splitlines())


def _css() -> str:
    with open(_CSS, encoding="utf-8") as fh:
        return fh.read()


def _screen02_css() -> str:
    """Only Screen 02's own block.

    📌 BOUNDED AT THE NEXT SCREEN HEADER, ⛔ never run to EOF. Reading to the end
    of the file is the defect this campaign has now found four times: the block
    silently adopts whatever the next screen appends to it.
    """
    css = _css()
    start = css.index("== Screen 02 — Dashboard")
    stop = css.index("== Screen 03 — Strategies")
    assert stop > start
    return css[start:stop]


# ══════════════════════════════════════════════════════════════════════════
# RECENT EVENTS — the source, and what the source can contain
# ══════════════════════════════════════════════════════════════════════════
class TestRecentEventsSource:

    def test_the_feed_does_not_read_system_events(self):
        """⛔ `system_events` is a SERVICE-LIFECYCLE table.

        Measured on production 18-Aug-2026: STARTUP (74) · SHUTDOWN (72) ·
        KILL_AUTO_CLEARED (40) · CONFIG_DIFF (28) — and ZERO trading events, all
        time. The artwork's rows are Signal Received / Order Filled / SL Hit /
        Trade Closed, so a panel fed from that table could not ever have shown
        what it promises.
        """
        src = inspect.getsource(dashboard_api)
        # ⛔ the READER, not a substring: "recent_events" also occurs inside
        # "build_recent_events", which is the replacement.
        assert "db_reader" not in src, (
            "the dashboard is reading db_reader.recent_events (system_events) again")
        assert "live_activity.build_recent_events" in src

    def test_the_feed_reuses_screen_18s_builders(self):
        """⭐ ONE event architecture, ⛔ not two.

        A second synthesis would let the same instant render differently on two
        screens; the guard is that the feed function calls the SAME per-category
        builders Screen 18's wall calls.
        """
        src = inspect.getsource(live_activity.build_recent_events)
        for builder in ("_from_signals", "_from_orders", "_from_trades",
                        "_from_risk", "_from_capital"):
            assert builder in src, "feed no longer reuses %s" % builder

    def test_every_artwork_badge_is_reachable(self):
        """⭐ NON-VACUITY: the four badges the artwork draws must be produceable.

        The panel this replaced derived its badge by substring-matching the event
        text, and against its only real source EVERY row fell into one bucket —
        four of its five branches could not fire. A badge vocabulary no row can
        reach is decoration.
        """
        reachable = set(live_activity.DASH_CATEGORY_BADGE.values())
        for badge in ("SIGNAL", "ORDER", "RISK", "EXIT"):
            assert badge in reachable, "%s is unreachable" % badge

    def test_every_category_the_feed_can_emit_has_a_badge(self):
        """⛔ No category may fall through to a near-enough label."""
        for category in live_activity.CATEGORIES:
            assert category in live_activity.DASH_CATEGORY_BADGE, (
                "category %r can be emitted but has no badge" % category)

    def test_the_badge_comes_from_the_row_not_from_its_text(self, gui_config):
        feed = live_activity.build_recent_events(gui_config)
        for row in feed["records"]:
            assert row["badge"] == live_activity.DASH_CATEGORY_BADGE[row["category"]]

    def test_a_routine_category_is_not_styled_as_an_alarm(self):
        """⛔ FOUND BY RENDERING: `Capital Reserved` came out RED under a warning
        triangle. Red is the loudest signal on this page and the binding spec
        reserves it for losses and rejections, so routine bookkeeping must not
        wear it. `Risk` and `Alert` keep it — those ARE the exceptional ones.
        """
        code = _tpl_code()
        m = re.search(r"const EV_CLASS = \{(.*?)\};", code, re.S)
        assert m, "EV_CLASS not found"
        mapping = dict(re.findall(r"(\w+):\s*'(\w+)'", m.group(1)))
        assert mapping["Capital"] != "risk", "Capital renders as an alarm"
        assert mapping["Position"] != "risk"
        assert mapping["Risk"] == "risk", "the alarm class must still exist for Risk"

    def test_the_free_text_classifier_is_gone_from_the_template(self):
        """⛔ `evCat()` classified by scanning the event STRING."""
        code = _tpl_code()
        assert "evCat(" not in code, "the free-text classifier is back"
        assert "EV_CLASS" in code, "the structured category map is missing"
        # and the template must not re-derive a category from the text itself
        assert not re.search(r"event_type\s*\|\|", code)


# ══════════════════════════════════════════════════════════════════════════
# RECENT EVENTS — a Today panel may only hold today
# ══════════════════════════════════════════════════════════════════════════
class TestRecentEventsAreToday:

    def test_no_row_is_from_another_day(self, gui_config, today):
        feed = live_activity.build_recent_events(gui_config)
        assert feed["today"] == today
        for row in feed["records"]:
            assert row["date"] == today, (
                "a row dated %s reached a panel captioned Today" % row["date"])

    def test_a_stale_row_is_refused_even_when_its_source_offers_one(
            self, gui_config, monkeypatch):
        """⭐ PLANTED REGRESSION, so the guard is proven to bite.

        A kill switch triggered on an earlier day is the real case: it is read
        from PERSISTED state, so its row survives across days and arrived in
        this feed rendering as a bare clock — measured 18-Aug with a kill dated
        03-AUG.

        ⭐ THE PLANTED ROW IS DATED IN THE FUTURE, and that is the whole point.
        A row dated in the PAST sorts last and falls outside the ten-row limit
        on any busy day, so it is dropped by the LIMIT rather than by the date
        filter — the guard then passes whether or not the filter exists. A
        future row sorts FIRST, so only the filter can keep it out.
        """
        real = live_activity._from_risk

        def _with_a_stale_row(signal_rows, kill):
            rows = list(real(signal_rows, kill))
            rows.append(live_activity._ev(
                "RISK-STALE", "2099-01-01T09:15:00+05:30", "Risk",
                "Kill Switch SOFT_KILL", details="planted", status="Failed"))
            return rows

        monkeypatch.setattr(live_activity, "_from_risk", _with_a_stale_row)
        feed = live_activity.build_recent_events(gui_config)
        assert feed["records"], "the feed is empty, so nothing was actually filtered"
        assert not [r for r in feed["records"] if str(r.get("ts", "")).startswith("2099")], (
            "an out-of-day row reached a Today panel")

    def test_the_row_carries_its_date_so_a_clock_cannot_stand_alone(self, gui_config):
        """The old panel printed HH:MM:SS only, which is how six days of events
        read as one morning. The date must travel with the row."""
        for row in live_activity.build_recent_events(gui_config)["records"]:
            assert row["date"], "a row has no date"
            assert row["time"], "a row has no clock"


# ══════════════════════════════════════════════════════════════════════════
# RECENT EVENTS — the CURATED DIGEST
# ══════════════════════════════════════════════════════════════════════════
# ⭐ WHY THESE EXIST. The first version of this suite asserted only that the four
# artwork badges appear in the category→badge MAP. That is true, and it is not
# the claim that matters: a badge present in a map is not a badge an operator can
# SEE. Measured on production 18-Aug 13:36, a raw newest-ten returned TEN
# IDENTICAL `Signal Received` rows spanning TWO SECONDS, because signals were
# 3,568 of 3,747 feed rows that day (95.2%) at 24-50 per minute. Every guard
# below is about what SURVIVES INTO THE PANEL under that real load.
class TestTheDigestSurvivesABurst:

    #: ⛔ LITERALS, and deliberately NOT the module's constants. Asserting a cap
    #: against the same constant the code uses makes the guard VACUOUS — raising
    #: the cap raises the assertion with it and the test stays green while the
    #: protection is gone. (Measured: that is exactly what happened on the first
    #: pass here, and to the note cap before it.) These are the numbers the
    #: PANEL's contract depends on; the code's constants must live under them,
    #: which is asserted separately.
    ROUTINE_CEILING = 1
    CATEGORY_CEILING = 4

    def test_the_code_caps_stay_under_the_panel_contract(self):
        assert live_activity.DASH_ROUTINE_MAX <= self.ROUTINE_CEILING
        assert live_activity.DASH_CATEGORY_MAX <= self.CATEGORY_CEILING
        assert "Signal" in live_activity.DASH_ROUTINE_CATEGORIES

    def _burst(self, n, category="Signal", event="Signal Received"):
        """n rows of one category, all stamped LATE today so they sort first."""
        return [
            live_activity._ev(
                "BURST-%s-%04d" % (category, i),
                "%sT23:59:%02d+05:30" % (TODAY_ISO, 59 - (i % 60)),
                category, event, symbol="SYM%02d" % i, status="Info")
            for i in range(n)
        ]

    def _ops(self):
        """One row of each operational category, all EARLIER than the burst."""
        rows = []
        for i, (cat, ev) in enumerate((
                ("Order", "Order Filled"), ("Trade", "TGT Hit"),
                ("Risk", "Risk Check Failed"), ("Capital", "Capital Released"),
                ("Position", "Position Opened"))):
            rows.append(live_activity._ev(
                "OPS-%d" % i, "%sT10:0%d:00+05:30" % (TODAY_ISO, i),
                cat, ev, symbol="OPS%d" % i, status="Info"))
        return rows

    def test_a_signal_burst_cannot_consume_the_panel(self):
        """⛔ THE MEASURED PRODUCTION FAILURE, as a test.

        500 signal arrivals, every one NEWER than every operational event — the
        exact shape of a real market-hours second. A blind newest-N returns ten
        signals; the digest must not.
        """
        events = self._burst(500) + self._ops()
        events.sort(key=lambda e: (e["ts"], e["ref_id"]), reverse=True)
        picked = live_activity._curate(events, 10)
        signal_rows = [e for e in picked if e["category"] == "Signal"]
        assert len(signal_rows) <= self.ROUTINE_CEILING, (
            "%d signal rows took the panel" % len(signal_rows))
        # ⭐ and the freed slots go to real events, ⛔ not to nothing: the
        # fixture offers exactly one row per operational category, so a working
        # digest returns all of them plus the single representative signal.
        assert len(picked) == len(self._ops()) + self.ROUTINE_CEILING, (
            "expected %d rows, got %d" % (len(self._ops()) + self.ROUTINE_CEILING, len(picked)))

    def test_operational_categories_stay_visible_during_that_burst(self):
        """⭐ THE OTHER HALF, and the half that actually matters: capping the
        flood is worthless if nothing operational takes the freed slots."""
        events = self._burst(500) + self._ops()
        events.sort(key=lambda e: (e["ts"], e["ref_id"]), reverse=True)
        picked = live_activity._curate(events, 10)
        cats = {e["category"] for e in picked}
        for operational in ("Order", "Trade", "Risk", "Capital", "Position"):
            assert operational in cats, (
                "%s vanished under a signal burst; visible: %s" % (operational, sorted(cats)))

    def test_the_artwork_badges_are_reachable_ON_SCREEN_under_load(self):
        """⛔ NOT the map — the RENDERED SET. This is the guard the first pass
        was missing."""
        events = self._burst(500) + self._ops()
        events.sort(key=lambda e: (e["ts"], e["ref_id"]), reverse=True)
        badges = {live_activity.DASH_CATEGORY_BADGE[e["category"]]
                  for e in live_activity._curate(events, 10)}
        for badge in ("SIGNAL", "ORDER", "RISK", "EXIT"):
            assert badge in badges, (
                "%s cannot appear on screen under load; got %s" % (badge, sorted(badges)))

    def test_an_operational_category_cannot_take_the_panel_either(self):
        """Capital is the real case, ⛔ not a hypothetical: 77 ledger rows on
        production 18-Aug, and FIVE of the ten newest non-signal rows were
        CAPITAL because a release is written beside every exit."""
        events = self._burst(60, category="Capital", event="Capital Released") + self._ops()
        events.sort(key=lambda e: (e["ts"], e["ref_id"]), reverse=True)
        picked = live_activity._curate(events, 10)
        capital_rows = [e for e in picked if e["category"] == "Capital"]
        assert len(capital_rows) <= self.CATEGORY_CEILING, (
            "%d capital rows took the panel" % len(capital_rows))
        assert {"Order", "Trade", "Risk"} <= {e["category"] for e in picked}

    def test_the_digest_stays_in_time_order(self):
        """⭐ A row is only ever SKIPPED, ⛔ never reordered — so no row is
        promoted above an event that happened after it."""
        events = self._burst(200) + self._ops()
        events.sort(key=lambda e: (e["ts"], e["ref_id"]), reverse=True)
        picked = live_activity._curate(events, 10)
        stamps = [e["ts"] for e in picked]
        assert stamps == sorted(stamps, reverse=True), "the digest is out of time order"

    def test_the_digest_never_invents_a_row(self):
        """Every row shown must be one the sources actually produced."""
        events = self._burst(200) + self._ops()
        events.sort(key=lambda e: (e["ts"], e["ref_id"]), reverse=True)
        source_ids = {e["ref_id"] for e in events}
        for e in live_activity._curate(events, 10):
            assert e["ref_id"] in source_ids

    def test_a_quiet_day_is_not_padded(self):
        """⛔ The cap bounds the feed; it must never REACH for filler."""
        picked = live_activity._curate(self._ops()[:2], 10)
        assert len(picked) == 2

    def test_the_cap_is_not_silent(self, gui_config):
        """⛔ A panel that drops rows must not imply it showed everything. The
        payload carries the day's real totals beside what it chose."""
        feed = live_activity.build_recent_events(gui_config)
        assert feed["total_today"] >= feed["count"]
        assert feed["suppressed"] == feed["total_today"] - feed["count"]
        assert isinstance(feed["by_category"], dict)
        assert feed["caps"]["routine"] == live_activity.DASH_ROUTINE_MAX

    def test_screen_18s_wall_is_not_curated(self):
        """⛔ SCOPE: the digest is Screen 02's. Screen 18 keeps every row."""
        wall = inspect.getsource(live_activity.build_live_activity)
        assert "_curate" not in wall, "the curation leaked into Screen 18's wall"


# ══════════════════════════════════════════════════════════════════════════
# RECENT EVENTS — nothing here may resize the page
# ══════════════════════════════════════════════════════════════════════════
class TestEventContentCannotWidenTheGrid:

    def test_the_note_is_bounded_at_the_source(self, gui_config):
        for row in live_activity.build_recent_events(gui_config)["records"]:
            note = row.get("note") or ""
            assert len(note) <= live_activity.DASH_NOTE_MAX, (
                "note of %d chars exceeds the %d cap"
                % (len(note), live_activity.DASH_NOTE_MAX))

    #: ⛔ A LITERAL, and deliberately not `DASH_NOTE_MAX`. Asserting against the
    #: code's own constant made this guard VACUOUS — raising the cap raised the
    #: assertion with it, so the bound could be removed and the test stayed
    #: green. This is the contract the LAYOUT depends on; the code's constant
    #: must live under it, and that is asserted separately below.
    NOTE_CEILING = 64

    def test_the_note_cap_stays_under_the_layout_ceiling(self):
        assert live_activity.DASH_NOTE_MAX <= self.NOTE_CEILING

    def test_a_huge_detail_string_still_produces_a_bounded_note(self):
        """⭐ PLANTED: the exact shape that broke the layout was a raw
        `{"changed_files": [...]}` blob rendered straight into the row."""
        blob = '{"changed_files": ' + str(["f%d.yaml" % i for i in range(60)]) + "}"
        assert len(blob) > 500, "the planted blob must actually be huge"
        note = live_activity._dash_note({"symbol": None, "details": blob})
        assert note is not None
        assert len(note) <= self.NOTE_CEILING, (
            "a %d-char detail produced a %d-char note" % (len(blob), len(note)))
        assert len(note) < len(blob)

    def test_the_feed_does_not_ship_the_raw_detail_field(self, gui_config):
        """⛔ The unbounded field is not sent at all — a field that is absent
        cannot size a grid track."""
        for row in live_activity.build_recent_events(gui_config)["records"]:
            assert "details" not in row

    def test_the_three_column_grid_tracks_cannot_be_sized_by_content(self):
        """⭐ `minmax(0, …)`: an `fr` track's default floor is min-content, and
        that floor is what let one long string widen the whole page at 1440."""
        block = _screen02_css()
        m = re.search(r"\.dash-page \.mc-3col \{[^}]*grid-template-columns:([^;]+);",
                      block, re.S)
        assert m, "mc-3col grid rule not found"
        cols = m.group(1)
        assert cols.count("minmax(0") == 3, "a track can still be sized by its content"

    def test_the_kpi_deck_tracks_cannot_be_sized_by_content(self):
        block = _screen02_css()
        for m in re.finditer(r"\.dash-page \.kpi-deck \{[^}]*grid-template-columns:([^;]+);",
                             block, re.S):
            assert "minmax(0" in m.group(1)

    def test_the_deck_reflows_above_1440(self):
        """⛔ The old breakpoint sat at 1400 — just BELOW a 1440 viewport — so
        1440 kept twelve tracks it could not fit and the page overflowed."""
        block = _screen02_css()
        widths = [int(w) for w in re.findall(
            r"@media \(max-width: (\d+)px\) \{ \.dash-page \.kpi-deck", block)]
        assert widths, "no kpi-deck breakpoint found"
        assert max(widths) > 1440, (
            "the widest deck breakpoint is %d, so a 1440 viewport keeps the "
            "twelve-column deck" % max(widths))


# ══════════════════════════════════════════════════════════════════════════
# SERVICE HEALTH — the stamp is measured or it says so
# ══════════════════════════════════════════════════════════════════════════
class TestServiceHealthStamp:

    def test_the_reader_asks_for_the_state_change_stamp(self):
        """⭐ `StateChangeTimestamp`, ⛔ not `ActiveEnterTimestamp`.

        Measured on production 18-Aug: `cron-watchdog.timer` last fired
        17-Aug 19:30:01 but was ARMED 03-Jul 22:21:03, six weeks earlier, and an
        INACTIVE unit reports a state-change stamp where it may report no enter
        stamp at all.
        """
        assert "StateChangeTimestamp" in host_reader._CHANGE_PROPS

    def test_every_unit_carries_a_stamp_field(self, gui_config):
        units = host_reader.all_units_with_change(gui_config)
        assert len(units) == len(gui_config["units"])
        for u in units:
            assert "unit" in u and "state" in u and "last_change_at" in u

    def test_an_unknown_stamp_is_none_and_never_a_substitute(self):
        """On a box with no systemd there IS no stamp; the reader must say so
        rather than offering any other time."""
        got = host_reader.unit_change("definitely-not-a-real-unit.service")
        assert got["last_change_at"] is None

    def test_the_template_never_prints_the_dashboards_own_clock_as_a_stamp(self):
        """⛔ THE ORIGINAL DEFECT: `svcTime()` returned `summary.ist_now` for any
        ACTIVE unit, so every healthy row showed the current time — a value that
        reads as a measurement and is not one. Measured on production the same
        day, `token-watcher` had not changed state for 21 DAYS.
        """
        tpl = _tpl()
        assert "svcTime(" not in tpl, "the fabricated stamp helper is back"
        stamp = re.search(r"svcStamp\(u\) \{(.*?)\n    \},", tpl, re.S)
        assert stamp, "svcStamp not found"
        body = stamp.group(1)
        assert "ist_now" not in body, "the stamp is being read off the page clock"
        assert "last_change_at" in body
        assert "UNAVAILABLE" in body

    def test_activating_is_not_rendered_as_a_failure(self):
        """`security-watcher` runs a designed RestartSec=60 heartbeat and sits in
        `activating`/`auto-restart` most of the time. ⛔ It must not read as down."""
        tpl = _tpl()
        down = re.search(r"svcDown\(s\)\s*\{(.*?)\},", tpl, re.S)
        assert down, "svcDown not found"
        # ⛔ the STATE, not a substring: 'deactivating' legitimately contains it,
        # and deactivating IS a down state.
        assert "'activating'" not in down.group(1), (
            "the designed RestartSec heartbeat would render as a failure")


# ══════════════════════════════════════════════════════════════════════════
# PIPELINE — the artwork's labels, and a colour that is not a clock
# ══════════════════════════════════════════════════════════════════════════
ARTWORK_STAGES = [
    "Received", "Validated", "Duplicate", "Rejected", "Risk Rejected",
    "Capital Rejected", "Orders Created", "Orders Placed", "Orders Filled",
    "SL Hit", "TGT Hit", "Manual Exit", "Trade Closed",
]


class TestPipelineMatchesTheArtwork:

    def test_the_thirteen_labels_are_the_artworks_in_order(self):
        assert [name for _k, name, _kind, _sem in pipeline_state.STAGE_DEFS] == ARTWORK_STAGES

    def test_no_label_is_hyphenated(self):
        """The build shipped `Risk-Rejected`, `SL-Hit`, `Manual/Other-Exit` …
        Two of them wrapped, which is why every card reserved two label lines."""
        for _k, name, _kind, _sem in pipeline_state.STAGE_DEFS:
            assert "-" not in name and "/" not in name, "%r is not the artwork's label" % name

    def test_the_artworks_own_stage_colours(self):
        """Read off the approved PNG at 5x. Received is AMBER because it is the
        intake, Orders Created is BLUE because it is informational, and SL Hit is
        AMBER because a stop firing is a warning."""
        semantic = {k: s for k, _n, _kind, s in pipeline_state.STAGE_DEFS}
        assert semantic["received"] == "ORANGE"
        assert semantic["orders_created"] == "BLUE"
        assert semantic["sl_hit"] == "ORANGE"
        assert semantic["tgt_hit"] == "GREEN"
        assert semantic["rejected"] == "RED"
        assert semantic["duplicate"] == "PURPLE"

    def test_the_colour_is_not_a_freshness_reading(self):
        """⭐ THE ARTWORK ITSELF DISPROVES A FRESHNESS RULE: its Orders Filled
        (last event 7 s before the mock clock) is GREEN while its SL Hit (141 s)
        is AMBER. Under a freshness rule the FRESHER card would be the
        highlighted one. So the clock must not be in this signature at all.
        """
        params = list(inspect.signature(pipeline_state.derive_color).parameters)
        assert params == ["semantic", "count", "failures"]

    def test_a_zero_stage_is_neutral_whatever_its_semantic_colour(self):
        for semantic in ("ORANGE", "GREEN", "BLUE", "PURPLE", "RED"):
            assert pipeline_state.derive_color(semantic, 0) == "GRAY"

    def test_capital_rejected_at_zero_is_neutral(self):
        """⛔ DELIBERATE, RECORDED DEVIATION from the artwork. The artwork draws
        `Capital Rejected 0` amber while drawing `Duplicate 0` and
        `Manual Exit 0` neutral — three zero-count cards, two treatments. An
        amber card reading 0 tells the operator there is a capital rejection when
        there is none, so the count-driven rule wins and the deviation is
        recorded rather than copied.
        """
        semantic = {k: s for k, _n, _kind, s in pipeline_state.STAGE_DEFS}
        assert pipeline_state.derive_color(semantic["capital_rejected"], 0) == "GRAY"
        assert pipeline_state.derive_color(semantic["capital_rejected"], 4) == "ORANGE"

    def test_failures_still_outrank_the_semantic_colour(self):
        assert pipeline_state.derive_color("GREEN", 5, 2) == "RED"

    def test_the_build_still_emits_thirteen_stages(self, gui_config, today):
        pipe = pipeline_state.build_pipeline(gui_config, today)
        assert len(pipe["stages"]) == 13
        assert [s["name"] for s in pipe["stages"]] == ARTWORK_STAGES

    def test_the_active_stage_is_a_ring_not_a_colour(self):
        """The freshest stage keeps its live marker — but as a box-shadow, so it
        cannot fight a palette the artwork fixes per stage."""
        block = _screen02_css()
        m = re.search(r"\.dash-page \.pl-active \{([^}]*)\}", block)
        assert m, "pl-active rule missing"
        assert "box-shadow" in m.group(1)
        assert "background" not in m.group(1)

    def test_a_stage_label_is_one_line(self):
        block = _screen02_css()
        m = re.search(r"\.dash-page \.pl-name \{([^}]*)\}", block, re.S)
        assert m, "pl-name rule missing"
        assert "nowrap" in m.group(1)
        assert "line-clamp" not in m.group(1)


# ══════════════════════════════════════════════════════════════════════════
# KPI DECK — the artwork's tones, and a value that stays in its card
# ══════════════════════════════════════════════════════════════════════════
class TestKpiDeck:

    def test_the_twelve_artwork_tones(self):
        """Four differ from the first build, each read off the approved PNG:
        Open Trades / Closed Trades / Profit Factor are NEUTRAL, and Win Rate is
        BLUE — ⛔ not green."""
        tpl = _tpl()
        block = re.search(r"return \[\s*(\{ key: 'received'.*?)\];", tpl, re.S)
        assert block, "kpis() table not found"
        table = block.group(1)
        expected = {
            "received": "info", "accepted": "pos", "rejected": "neg",
            "orders_created": "info", "orders_filled": "pos",
            "open": "neutral", "closed": "neutral",
            "wins": "pos", "losses": "neg",
            "winrate": "info", "pf": "neutral",
        }
        for key, tone in expected.items():
            m = re.search(r"key: '%s'.*?tone: '(\w+)'" % key, table, re.S)
            assert m, "no tone for %s" % key
            assert m.group(1) == tone, "%s is %s, artwork draws %s" % (key, m.group(1), tone)

    def test_the_pnl_tone_follows_its_sign(self):
        tpl = _tpl()
        assert re.search(r"key: 'pnl'.*?pnlNet\(\)\) < 0 \? 'neg' : 'pos'", tpl, re.S)

    def test_every_tone_used_has_a_css_rule(self):
        block = _screen02_css()
        for tone in ("info", "pos", "neg", "neutral"):
            assert ".dash-page .kc-%s {" % tone in block, "no CSS for tone %r" % tone

    def test_the_value_is_white_on_every_card(self):
        """⛔ The artwork prints all twelve numbers WHITE; the tone lives in the
        border and the icon chip. The build tinted the value per tone."""
        block = _screen02_css()
        for tone in ("info", "pos", "neg", "warn"):
            assert not re.search(r"\.kc-%s \.kc-val \{" % tone, block), (
                "tone %s still colours the value" % tone)
        m = re.search(r"\.dash-page \.kc-val \{([^}]*)\}", block, re.S)
        assert m and "color: var(--text)" in m.group(1)

    def test_the_card_is_bordered_and_tinted_not_railed(self):
        """The artwork draws a full border on all four sides over a tinted fill;
        the build drew a 3px rail along the top of an untinted card."""
        block = _screen02_css()
        for sel in (r"\.dash-page \.kc \{", r"\.dash-page \.pl-card \{"):
            m = re.search(sel + r"([^}]*)\}", block, re.S)
            assert m, "rule %s missing" % sel
            assert "border-top: 3px" not in m.group(1), "the top rail is back"
        for tone in ("info", "pos", "neg"):
            m = re.search(r"\.dash-page \.kc-%s \{([^}]*)\}" % tone, block)
            assert m and "border-color" in m.group(1) and "background" in m.group(1)

    def test_a_long_value_steps_down_instead_of_escaping_its_card(self):
        """⭐ FOUND BY RENDERING, invisible to every other test: at 1920 the
        twelve-across deck leaves ~100px of inner card and `₹2,395.00` at 29px
        printed through its own border and past the panel edge.
        """
        tpl = _tpl()
        m = re.search(r"valSize\(v\) \{(.*?)\n    \},", tpl, re.S)
        assert m, "valSize not found"
        body = m.group(1)
        assert "kc-val-sm" in body and "kc-val-xs" in body
        block = _screen02_css()
        sizes = []
        for cls in ("kc-val-md", "kc-val-sm", "kc-val-xs"):
            hit = re.search(r"\.dash-page \.%s \{ font-size: (\d+)px" % cls, block)
            assert hit, "no CSS for %s" % cls
            sizes.append(int(hit.group(1)))
        assert sizes == sorted(sizes, reverse=True), "the steps do not descend"
        assert min(sizes) >= 16, (
            "a critical metric fell to %dpx; the spec floor is 16-18px" % min(sizes))

    def test_the_value_cannot_be_split_mid_number(self):
        """⛔ `overflow-wrap: anywhere` splits SHORT values too — Screen 16
        measured it breaking `22.00%` into `22.0` / `0%` on 18-Aug."""
        block = _screen02_css()
        m = re.search(r"\.dash-page \.kc-val \{([^}]*)\}", block, re.S)
        assert m and "anywhere" not in m.group(1)

    def test_both_artwork_sparklines_are_drawn_from_real_series(self):
        """The artwork draws a sparkline on Signals Received AND on Today's P&L.
        ⛔ Neither may be synthesised."""
        tpl = _tpl()
        assert re.search(r"key: 'received'.*?spark: 'signals'", tpl, re.S)
        assert re.search(r"key: 'pnl'.*?spark: 'pnl'", tpl, re.S)
        src = inspect.getsource(live_activity.build_signals_spark)
        assert "activity_pulse" in src, "the signals spark is not from the real series"

    def test_an_empty_series_draws_no_line_at_all(self, gui_config, monkeypatch):
        """⛔ A flat line along the axis is still a DRAWN CHART, and a reader
        takes a drawn chart as a measurement of shape."""
        monkeypatch.setattr(live_activity.db_reader, "activity_pulse",
                            lambda cfg, today: {"signals": {}})
        spark = live_activity.build_signals_spark(gui_config)
        assert spark["available"] is False
        assert set(spark["points"]) == {0}


# ══════════════════════════════════════════════════════════════════════════
# TYPOGRAPHY — the spec's floor
# ══════════════════════════════════════════════════════════════════════════
class TestTypeFloor:

    #: ⛔ `.sb-ver` styles base.html's SIDEBAR — the shared shell behind all 22
    #: screens, which is out of scope for Screen 02 by explicit ruling. It is
    #: named here so the exemption is a DECISION and not an oversight.
    SHARED_SHELL = (".sb-ver",)

    def test_nothing_on_screen_02_is_set_below_13px(self):
        """`gui/02. Dashboard.txt`, FONT & READABILITY: "Minimum: 13px"."""
        block = _screen02_css()
        offenders = []
        for m in re.finditer(r"([^\n{}]*)\{([^}]*?)font-size:\s*([0-9.]+)px", block, re.S):
            selector = m.group(1).strip().splitlines()[-1].strip() if m.group(1).strip() else ""
            if float(m.group(3)) < 13 and not any(s in selector for s in self.SHARED_SHELL):
                offenders.append((selector, m.group(3)))
        assert not offenders, "below the 13px floor: %s" % offenders

    def test_the_section_headers_stay_in_their_band(self):
        """Spec: Headers 14-16px."""
        block = _screen02_css()
        m = re.search(r"\.dash-page \.mc-title \{[^}]*font-size: (\d+)px", block)
        assert m and 14 <= int(m.group(1)) <= 16


# ══════════════════════════════════════════════════════════════════════════
# STRUCTURE — the panels the artwork draws, and nothing invented
# ══════════════════════════════════════════════════════════════════════════
class TestArtworkStructure:

    def test_the_five_approved_panels_are_present(self):
        tpl = _tpl()
        for title in ("Today's Trading Summary", "Trade Pipeline",
                      "Daily Capacity Monitor", "Service Health",
                      "Recent Events", "Strategy Summary"):
            assert title in tpl, "panel %r missing" % title

    def test_the_capacity_columns_are_the_artworks(self):
        tpl = _tpl()
        head = re.search(r"<thead><tr>\s*<th>Limit</th>(.*?)</tr></thead>", tpl, re.S)
        assert head
        for col in ("Used", "Cap", "Left", "Usage %", "Status"):
            assert col in head.group(1), "capacity column %r missing" % col

    def test_the_view_all_target_is_live_activity(self):
        """The artwork's Recent Events "View all" points at the wall that owns
        the whole feed."""
        assert 'href="/live-activity"' in _tpl()

    def test_no_chart_is_invented(self):
        """The artwork carries TWO sparklines and no other chart. ⛔ Nothing else
        may appear."""
        tpl = _tpl()
        assert tpl.count("<svg") - tpl.count('viewBox="0 0 24 24"') <= 2, (
            "a chart the artwork does not draw has appeared")

    def test_the_dashboard_payload_carries_each_panels_source(self, client):
        res = client.get("/api/dashboard")
        assert res.status_code == 200
        body = res.get_json()
        for key in ("summary", "service_health", "events", "events_meta",
                    "signals_spark", "freshness", "today"):
            assert key in body, "payload is missing %r" % key

    def test_events_stays_a_list_for_the_shared_shell(self, client):
        """⛔ base.html assigns `d.events` straight to the root component
        (`this.events = d.events || []`) and that shell is out of scope for
        Screen 02, so the key must stay a LIST."""
        body = client.get("/api/dashboard").get_json()
        assert isinstance(body["events"], list)
