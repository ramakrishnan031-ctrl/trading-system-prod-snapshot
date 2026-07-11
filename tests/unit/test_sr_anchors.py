"""
tests/unit/test_sr_anchors.py — V3 03.01 S&R Detection ADAPT (Step 2).

Covers the additive, default-OFF anchor/swing layer:
  • intraday_anchors: VWAP + ORB (config window) computed deterministically.
  • build_swings_payload: structural swings labelled 30m=PRIMARY / 1h=MAJOR.
  • build_anchor_payload / round_number_levels.
  • detector: anchors OFF (default) → evidence byte-identical, confidence_class
    ANCHOR_ONLY; anchors ON → evidence carries anchors/swings/confidence_class.
  • confidence_class NEVER flips to ANCHOR_PLUS_VALIDATED_SWINGS in this step.
Nothing here gates capital — the detector stays a shadow observer.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta

from core.config_loader import SRDetectorConfig
from sr_detector import build_sr_detector
from sr_detector.models import (
    CONF_ANCHOR_ONLY,
    CONF_ANCHOR_PLUS_VALIDATED_SWINGS,
    TF_ROLE,
    Candidate,
    Candle,
    ScoredZone,
)
from sr_detector.zone_builder import (
    build_anchor_payload,
    build_swings_payload,
    intraday_anchors,
    round_number_levels,
)

_LOG = logging.getLogger("test_sr_anchors")
_NOW = datetime(2026, 6, 26, 14, 0)
_SESSION = (time(9, 15), time(15, 30))


# ── pure helpers: intraday_anchors (VWAP + ORB) ───────────────────────────────

def _fine_5m(on_date, count, tz=None):
    """`count` 5-minute candles from 09:15. Distinctive OHLCV so VWAP/ORB check."""
    out = []
    for i in range(count):
        ts = datetime.combine(on_date, time(9, 15), tzinfo=tz) + timedelta(minutes=5 * i)
        out.append(Candle(ts=ts, open=100 + i, high=105 + i, low=98 + i, close=101 + i, volume=100))
    return out


def test_intraday_anchors_orb_window_and_vwap():
    d = date(2026, 6, 26)
    fine = _fine_5m(d, 12)  # 09:15..10:10
    a = intraday_anchors(fine, on_date=d, session_open=time(9, 15),
                         session_close=time(15, 30), orb_window_minutes=15)
    # ORB window 15 min = first 3 candles (09:15/09:20/09:25)
    #   highs 105,106,107 → 107 ; lows 98,99,100 → 98
    assert a["orb_high"] == 107.0
    assert a["orb_low"] == 98.0
    assert a["orb_window_minutes"] == 15
    assert a["vwap"] is not None       # computed over the scoped session bars


def test_intraday_anchors_orb_window_is_config_param():
    d = date(2026, 6, 26)
    fine = _fine_5m(d, 12)
    # 30-min window = first 6 candles → highs up to 110, lows from 98
    a30 = intraday_anchors(fine, on_date=d, session_open=time(9, 15),
                           session_close=time(15, 30), orb_window_minutes=30)
    assert a30["orb_high"] == 110.0    # 105+5
    assert a30["orb_low"] == 98.0
    # a narrower 5-min window = just the first candle
    a5 = intraday_anchors(fine, on_date=d, session_open=time(9, 15),
                          session_close=time(15, 30), orb_window_minutes=5)
    assert a5["orb_high"] == 105.0 and a5["orb_low"] == 98.0


def test_intraday_anchors_excludes_other_dates_and_preopen():
    d = date(2026, 6, 26)
    fine = _fine_5m(d, 3)
    other_day = Candle(ts=datetime(2026, 6, 25, 9, 15), open=1, high=999, low=1, close=1, volume=100)
    preopen = Candle(ts=datetime(2026, 6, 26, 9, 0), open=1, high=999, low=1, close=1, volume=100)
    a = intraday_anchors([other_day, preopen] + fine, on_date=d, session_open=time(9, 15),
                         session_close=time(15, 30), orb_window_minutes=15)
    assert a["orb_high"] == 107.0      # the 999 highs (other date / pre-open) excluded


def test_intraday_anchors_insufficient_data_none():
    d = date(2026, 6, 26)
    a = intraday_anchors([], on_date=d, session_open=time(9, 15),
                         session_close=time(15, 30), orb_window_minutes=15)
    assert a["vwap"] is None and a["orb_high"] is None and a["orb_low"] is None


def test_intraday_anchors_determinism():
    d = date(2026, 6, 26)
    fine = _fine_5m(d, 8)
    kw = dict(on_date=d, session_open=time(9, 15), session_close=time(15, 30), orb_window_minutes=15)
    assert intraday_anchors(fine, **kw) == intraday_anchors(fine, **kw)


# ── round numbers + anchor payload ────────────────────────────────────────────

def test_round_number_levels_straddle_price():
    lv = round_number_levels(523.0)
    assert 500.0 in lv and 600.0 in lv and 550.0 in lv   # magnitude + half
    assert round_number_levels(0) == () and round_number_levels(None) == ()


def test_build_anchor_payload_merges_prior_day_and_intraday():
    daily = [
        Candle(ts=datetime(2026, 6, 24), open=90, high=110, low=88, close=100, volume=1),
        Candle(ts=datetime(2026, 6, 25), open=100, high=120, low=95, close=115, volume=1),  # prior day
        Candle(ts=datetime(2026, 6, 26), open=115, high=130, low=112, close=125, volume=1),
    ]
    intraday = {"vwap": 118.0, "orb_high": 121.0, "orb_low": 116.0, "orb_window_minutes": 15}
    p = build_anchor_payload(daily, reference_price=119.0, intraday=intraday)
    # prior_day = daily[-2] = the 25th → PDH120/PDL95/PDC115
    assert p["prior_day"] == {"PDH": 120, "PDL": 95, "PDC": 115}
    assert p["vwap"] == 118.0 and p["orb_high"] == 121.0
    assert p["reference_price"] == 119.0
    assert 100.0 in p["round_numbers"]   # magnitude-100 level near 119


# ── swing labelling: 30m=PRIMARY / 1h=MAJOR ───────────────────────────────────

def _zone(kind, low, high, tfs, conf="HIGH"):
    return ScoredZone(band_low=low, band_high=high, kind=kind, score=5.0,
                      confidence=conf, touches=3, timeframes=tuple(tfs))


def test_build_swings_payload_labels_primary_and_major():
    zones = [
        _zone("RESISTANCE", 520, 525, ("30minute",)),
        _zone("SUPPORT", 480, 485, ("60minute",)),
        _zone("RESISTANCE", 530, 535, ("30minute", "60minute")),  # both roles
    ]
    sw = build_swings_payload(zones)
    assert "PRIMARY" in sw and "MAJOR" in sw
    assert TF_ROLE["30minute"] == "PRIMARY" and TF_ROLE["60minute"] == "MAJOR"
    # the multi-TF zone appears under both PRIMARY and MAJOR
    primary_bands = {(z["band_low"], z["band_high"]) for z in sw["PRIMARY"]}
    major_bands = {(z["band_low"], z["band_high"]) for z in sw["MAJOR"]}
    assert (530.0, 535.0) in primary_bands and (530.0, 535.0) in major_bands
    assert all("confidence" in z and "timeframe" in z for lst in sw.values() for z in lst)


# ── detector: default-OFF byte-identical vs ON enrichment ─────────────────────

class _FakeStore:
    def __init__(self):
        self.rows = []

    def insert_sr_detector_result(self, row):
        self.rows.append(row)


class _Row:
    instrument_token = 555


class _Cache:
    def get_by_symbol(self, sym):
        return _Row()


def _triangle(n, base_ts, step_min, lo=480.0, hi=520.0, period=20):
    out, half = [], period // 2
    for i in range(n):
        ph = i % period
        price = lo + (hi - lo) * (ph / half) if ph <= half else hi - (hi - lo) * ((ph - half) / half)
        out.append({"date": base_ts + timedelta(minutes=step_min * i), "open": price,
                    "high": price + 0.5, "low": price - 0.5, "close": price, "volume": 1000})
    return out


def _fetch_with_fine(token, frm, to, interval):
    if interval == "day":
        return _triangle(140, _NOW - timedelta(days=140), 1440)
    if interval == "60minute":
        return _triangle(140, _NOW - timedelta(hours=140), 60)
    if interval == "30minute":
        return _triangle(140, _NOW - timedelta(minutes=30 * 140), 30)
    if interval == "5minute":
        # today's fine bars from 09:15 (a rising ramp) so VWAP/ORB are computable
        out = []
        for i in range(50):  # 09:15 → ~13:20
            ts = datetime.combine(_NOW.date(), time(9, 15)) + timedelta(minutes=5 * i)
            out.append({"date": ts, "open": 500 + i, "high": 502 + i, "low": 499 + i,
                        "close": 501 + i, "volume": 1000})
        return out
    return []


def _cand():
    return Candidate(signal_id="SIG1", symbol="TESTSTK", strategy="momentum_long",
                     direction="LONG", intended_entry=519.0, sl_price=510.0,
                     tgt_price=535.0, qty=10, intent="INTRADAY", mode="paper", ts=_NOW, score=62)


def _build(store, *, anchors_on, with_session=True):
    cfg = SRDetectorConfig(enabled=True, intraday_anchors_enabled=anchors_on)
    kw = {}
    if with_session:
        kw = dict(session_open=time(9, 15), session_close=time(15, 30))
    return build_sr_detector(
        config=cfg, fetch_fn=_fetch_with_fine, instrument_cache=_Cache(),
        store=store, logger=_LOG, mode="paper", now_fn=lambda: _NOW, **kw)


def test_detector_default_off_is_byte_identical_evidence():
    store = _FakeStore()
    det = _build(store, anchors_on=False)
    det.process_candidate(_cand())
    ev = json.loads(store.rows[0]["confluence_evidence"])
    # default-OFF: evidence carries ONLY the pre-V3 keys, no anchor/swing enrichment
    assert set(ev.keys()) == {"resistance_zones", "support_zones"}


def test_detector_on_enriches_evidence_with_anchors_swings_confidence():
    store = _FakeStore()
    det = _build(store, anchors_on=True)
    det.process_candidate(_cand())
    ev = json.loads(store.rows[0]["confluence_evidence"])
    assert "anchors" in ev and "swings" in ev
    assert ev["confidence_class"] == CONF_ANCHOR_ONLY
    anchors = ev["anchors"]
    assert anchors["vwap"] is not None                 # computed from the 5m fine fetch
    assert anchors["orb_high"] is not None and anchors["orb_low"] is not None
    assert anchors["orb_window_minutes"] == 15
    assert "prior_day" in anchors and "round_numbers" in anchors


def test_detector_analysis_object_carries_confidence_class_default_anchor_only():
    det = _build(_FakeStore(), anchors_on=True)
    a = det.analyze(_cand())
    # confidence_class is ANCHOR_ONLY and NEVER the validated class in this step
    assert a.confidence_class == CONF_ANCHOR_ONLY
    assert a.confidence_class != CONF_ANCHOR_PLUS_VALIDATED_SWINGS
    assert a.anchors and a.swings   # populated when anchors_on


def test_detector_on_without_session_bounds_skips_vwap_orb():
    # anchors on but no session bounds injected → prior_day/round still present,
    # vwap/orb absent (never fabricated), never raises.
    store = _FakeStore()
    det = _build(store, anchors_on=True, with_session=False)
    det.process_candidate(_cand())
    ev = json.loads(store.rows[0]["confluence_evidence"])
    assert "anchors" in ev
    assert ev["anchors"].get("vwap") is None
    assert "prior_day" in ev["anchors"]
