"""tests/test_b3_b4_spec_decisions.py — B3 and B4, RULED 19-Aug-2026.

Two Queue-B items, opposite outcomes. Both are pinned here so neither can drift:
one because it is now VALIDATED, the other because it is BLOCKED and a later pass
must not quietly implement it.

──────────────────────────────────────────────────────────────────────────────
B4 — REJECTION TAXONOMY.  ✅ VALIDATED, ⛔ NO CODE CHANGE.
──────────────────────────────────────────────────────────────────────────────
`02. Dashboard.txt`, LIVE PIPELINE, "Preferred Flow", states the stages verbatim:

    Received -> Validated -> Duplicate -> Rejected -> Risk Rejected ->
    Capital Rejected -> Orders Created -> Orders Placed -> Orders Filled ->
    SL Hit -> TGT Hit -> Trade Closed

`pipeline_state.STAGE_DEFS` already carries all twelve, IN THAT ORDER, and the
dashboard renders each with its count and its last event time — which is the
other thing the spec asks for ("For every stage display: Count and Last Event
Time"). The three-way rejection split the spec defines therefore already exists.

⛔ WHAT THE SPEC DOES **NOT** DEFINE is the MEMBERSHIP of the two families —
which check codes count as risk and which as capital. `_RISK_REJECT_STATUSES`
(10) and `_CAPITAL_REJECT_STATUSES` (3) come from the engine's own check codes,
⛔ not from the design. Nothing in the specification requires a correction, so
none is invented: the ambiguity is recorded instead.

⚠️ ONE ADDITION, stated rather than hidden: STAGE_DEFS carries a thirteenth
stage, MANUAL EXIT, between TGT Hit and Trade Closed. The spec calls its list
"Preferred Flow" and a manual exit is a real terminal state, so this is an
addition, ⛔ not a contradiction — but it IS a deviation from the written list and
is named here so the visual review can accept or reject it deliberately.

──────────────────────────────────────────────────────────────────────────────
B3 — S07 "RR DAMAGE %".  🔴 BLOCKED BY SPEC/DATA DECISION. ⛔ NOT IMPLEMENTED.
──────────────────────────────────────────────────────────────────────────────
`07. Trade_Explorer.txt`, RR ANALYSIS, asks for three rows and gives a worked
example:

    Expected RR: 1 : 2      Actual RR: 1 : 1.6      RR Damage: 20%

⇒ the spec's own arithmetic is (planned − actual) ÷ planned.

THE PROJECT ALREADY OWNS **TWO** QUANTITIES, and they are different numbers:

  rr_damage_pct        (entry_adverse + sl_adverse − tgt_favourable)
                       ÷ planned_sl_distance × 100
                       BASE = the planned RISK BUDGET (SL distance).
                       Persisted by orders/slippage_recorder.calc_rr_damage_pct,
                       and displayed on Screen 10 under the label "RR Damage %".

  rr_degradation_pct   100 × (planned_rr − actual_rr) ÷ planned_rr
                       BASE = the planned R:R. slippage_analytics names this
                       verbatim as "the reference design's 1:2 -> 1:1.6 = 20%
                       arithmetic" — i.e. it IS what Screen 07's example asks
                       for. Displayed on Screen 10 as "RR Degradation % (avg)".

So the spec's LABEL matches one quantity and the spec's ARITHMETIC matches the
other, and the project carries a standing rule that they are never presented as
each other ("one label, one meaning"), pinned by
test_screen10_slippage.test_rr_damage_and_rr_degradation_are_reported_separately.

🔑 THE EXACT MISSING DEFINITION, and it is the only thing blocking this:
    WHICH quantity Screen 07's "RR Damage %" row must show, given that the label
    is already bound on Screen 10 to the OTHER one.

⛔ It cannot be resolved by choosing the smaller change, because every option
trades one project rule against another:
  (1) spec label + spec arithmetic  -> "RR Damage %" means two different things
      on S07 and S10;
  (2) spec arithmetic + honest label ("RR Degradation %") -> keeps one label /
      one meaning, deviates from the spec's wording;
  (3) existing rr_damage_pct under the spec's label -> agrees with S10, but the
      three rows on screen then do not reconcile: 20% would not follow from
      1:2 and 1:1.6.

⚠️ THERE IS ALSO A DATA GAP, and it is independent of the label. Screen 07 reads
`db_reader.trade_explorer_rows`, which does NOT join `trade_slippage_log`;
planned_rr / actual_rr / rr_damage_pct are Screen 10's spine (`_TSL_COLS` via
`slippage_rows_range`). Even once the label is ruled, S07 needs that join — new
data plumbing on a trading screen, ⛔ not a UI tweak.

⛔ Nothing is fabricated, and no trading/risk semantics are altered to populate a
UI field. The tests below assert the ABSENCE deliberately.
"""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

from backend.readers import db_reader  # noqa: E402
from backend.services import pipeline_state  # noqa: E402


def _tpl(name):
    with open(os.path.join(_ROOT, "frontend", "templates", name),
              encoding="utf-8") as fh:
        return fh.read()


#: `02. Dashboard.txt`, LIVE PIPELINE -> "Preferred Flow", verbatim and in order.
SPEC_FLOW = [
    "Received", "Validated", "Duplicate", "Rejected", "Risk Rejected",
    "Capital Rejected", "Orders Created", "Orders Placed", "Orders Filled",
    "SL Hit", "TGT Hit", "Trade Closed",
]


class TestB4TaxonomyMatchesTheSpec:

    def test_every_spec_stage_exists_in_the_spec_order(self):
        names = [d[1] for d in pipeline_state.STAGE_DEFS]
        got = [n for n in names if n in SPEC_FLOW]
        assert got == SPEC_FLOW, "pipeline flow drifted from the spec: %s" % got

    def test_the_rejection_split_is_three_distinct_stages(self):
        names = [d[1] for d in pipeline_state.STAGE_DEFS]
        for n in ("Rejected", "Risk Rejected", "Capital Rejected"):
            assert names.count(n) == 1, "%s is not a single distinct stage" % n

    def test_the_only_extra_stage_is_the_named_manual_exit_deviation(self):
        """⛔ If a stage appears that is neither in the spec nor the ONE named
        deviation, this fails — so an addition cannot arrive unannounced."""
        names = [d[1] for d in pipeline_state.STAGE_DEFS]
        extra = [n for n in names if n not in SPEC_FLOW]
        assert extra == ["Manual Exit"], "unannounced pipeline stage(s): %s" % extra

    def test_the_dashboard_shows_count_and_last_event_time_for_every_stage(self):
        """The spec's other pipeline requirement, asserted on the markup that
        renders the stages rather than on the payload that feeds them."""
        code = _tpl("dashboard.html")
        assert 'x-for="(st, i) in (pipeline.stages || [])"' in code
        assert 'x-text="fmtInt(st.count)"' in code, "stage count not rendered"
        assert 'clockOf(st.last_event)' in code, "stage last-event time not rendered"

    def test_the_status_families_are_unchanged(self):
        """⛔ The spec defines the SPLIT, ⛔ never the MEMBERSHIP, so these tuples
        must not move on a UI pass. Their sizes are pinned as a property of
        'unchanged', ⛔ not as a target to edit."""
        assert len(db_reader._RISK_REJECT_STATUSES) == 10
        assert len(db_reader._CAPITAL_REJECT_STATUSES) == 3
        assert not (set(db_reader._RISK_REJECT_STATUSES)
                    & set(db_reader._CAPITAL_REJECT_STATUSES)), \
            "a status joined both families — the counts would double-count it"


class TestB3RemainsBlockedAndUnimplemented:
    """⛔ These assert an ABSENCE **deliberately**. A later pass that implements
    RR Damage on Screen 07 must first resolve the label collision recorded in
    this module's docstring — and will have to change these tests to do it."""

    def test_screen07_does_not_display_an_rr_damage_row(self):
        assert "rr_damage" not in _tpl("trade_explorer.html"), \
            "S07 grew an RR Damage field while B3 is unruled"
        assert "RR Damage" not in _tpl("trade_explorer.html")

    def test_screen07s_reader_still_does_not_join_the_slippage_table(self):
        """The data gap is real and separate from the label question."""
        import inspect
        src = inspect.getsource(db_reader.trade_explorer_rows)
        assert "trade_slippage_log" not in src, \
            "S07 now joins trade_slippage_log — B3's data gap closed without a ruling"

    def test_both_quantities_still_exist_under_their_own_names(self):
        """⛔ The collision is only describable while BOTH still exist and are
        named apart. If one disappears, the recorded decision is stale."""
        svc = os.path.join(_ROOT, "backend", "services", "slippage_analytics.py")
        with open(svc, encoding="utf-8") as fh:
            src = fh.read()
        assert "rr_damage_pct" in src and "rr_degradation_pct" in src
        assert re.search(r'"RR Damage %"', src), "S10 lost the RR Damage label"
        assert re.search(r'"RR Degradation % \(avg\)"', src), \
            "S10 lost the RR Degradation label"
