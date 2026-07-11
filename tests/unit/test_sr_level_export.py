"""
tests/unit/test_sr_level_export.py — V3 03.01 level-export (Task 4) pure core.

The manual-marking export's pure functions are tested with an injected analyze
function (no broker): flatten_analysis, rows_to_csv, export_levels.
"""
from __future__ import annotations

from sr_detector.models import CONF_ANCHOR_ONLY, SRAnalysis
from scripts.sr_level_export import (
    COLUMNS,
    export_levels,
    flatten_analysis,
    rows_to_csv,
)


def _analysis():
    return SRAnalysis(
        structure_status="OK",
        confidence_class=CONF_ANCHOR_ONLY,
        anchors={
            "prior_day": {"PDH": 120.0, "PDL": 95.0, "PDC": 115.0},
            "round_numbers": [100.0, 150.0],
            "reference_price": 119.0,
            "vwap": 118.0,
            "orb_high": 121.0,
            "orb_low": 116.0,
            "orb_window_minutes": 15,
        },
        swings={
            "PRIMARY": [{"band_low": 520.0, "band_high": 525.0, "kind": "RESISTANCE",
                         "confidence": "HIGH", "timeframe": "30minute"}],
            "MAJOR": [{"band_low": 480.0, "band_high": 485.0, "kind": "SUPPORT",
                       "confidence": "MEDIUM", "timeframe": "60minute"}],
        },
    )


def test_flatten_analysis_emits_anchor_and_swing_rows():
    rows = flatten_analysis("RELIANCE", "2026-07-10", _analysis())
    types = [r["level_type"] for r in rows]
    # anchors: PDH/PDL/PDC + 2 round + VWAP + ORB_HIGH + ORB_LOW
    assert types.count("ROUND") == 2
    for t in ("PDH", "PDL", "PDC", "VWAP", "ORB_HIGH", "ORB_LOW"):
        assert t in types
    # swings labelled by role, tagged NOT-YET-VALIDATED (ANCHOR_ONLY)
    swing_rows = [r for r in rows if r["layer"] == "SWING"]
    assert {r["role_timeframe"] for r in swing_rows} == {"PRIMARY", "MAJOR"}
    assert all(r["confidence_class"] == CONF_ANCHOR_ONLY for r in rows)
    # every row carries the symbol + date + full column set
    assert all(r["symbol"] == "RELIANCE" and r["date"] == "2026-07-10" for r in rows)


def test_flatten_analysis_omits_absent_intraday_anchors():
    a = SRAnalysis(structure_status="OK",
                   anchors={"prior_day": {"PDH": 10.0, "PDL": 8.0, "PDC": 9.0},
                            "round_numbers": [], "vwap": None,
                            "orb_high": None, "orb_low": None},
                   swings={})
    rows = flatten_analysis("X", "2026-07-10", a)
    types = {r["level_type"] for r in rows}
    assert "VWAP" not in types and "ORB_HIGH" not in types   # never fabricated
    assert {"PDH", "PDL", "PDC"} <= types


def test_rows_to_csv_header_and_rowcount():
    rows = flatten_analysis("TCS", "2026-07-10", _analysis())
    csv_text = rows_to_csv(rows)
    lines = csv_text.strip().splitlines()
    assert lines[0] == ",".join(COLUMNS)
    assert len(lines) == 1 + len(rows)   # header + one line per level


def test_export_levels_injected_analyze_fn():
    calls = []

    def fake_analyze(symbol, on_date):
        calls.append((symbol, on_date))
        return _analysis() if symbol == "GOOD" else None   # None = fetch failure

    rows = export_levels(["GOOD", "BAD"], ["2026-07-10"], fake_analyze)
    assert ("GOOD", "2026-07-10") in calls and ("BAD", "2026-07-10") in calls
    # only GOOD contributes rows; BAD (None) contributes none
    assert {r["symbol"] for r in rows} == {"GOOD"}
    assert len(rows) == len(flatten_analysis("GOOD", "2026-07-10", _analysis()))
