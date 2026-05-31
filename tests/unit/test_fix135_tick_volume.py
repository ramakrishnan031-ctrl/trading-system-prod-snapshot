"""Tests for FIX-135 Item 46: tick volume accumulation in CandleStore."""
from __future__ import annotations

import logging
from datetime import datetime

import pytest

from data.candle_store import CandleStore, _Accumulator


# ── _Accumulator volume ──────────────────────────────────────────────────


class TestAccumulatorVolume:
    def test_initial_volume(self):
        acc = _Accumulator(
            open=100.0, high=100.0, low=100.0, close=100.0,
            tick_count=1, window_start=datetime(2026, 5, 31, 10, 0),
            volume=500,
        )
        assert acc.volume == 500

    def test_default_volume_zero(self):
        acc = _Accumulator(
            open=100.0, high=100.0, low=100.0, close=100.0,
            tick_count=1, window_start=datetime(2026, 5, 31, 10, 0),
        )
        assert acc.volume == 0

    def test_update_accumulates_volume(self):
        acc = _Accumulator(
            open=100.0, high=100.0, low=100.0, close=100.0,
            tick_count=1, window_start=datetime(2026, 5, 31, 10, 0),
            volume=500,
        )
        acc.update(101.0, volume=300)
        assert acc.volume == 800

    def test_multiple_updates_accumulate(self):
        acc = _Accumulator(
            open=100.0, high=100.0, low=100.0, close=100.0,
            tick_count=1, window_start=datetime(2026, 5, 31, 10, 0),
            volume=0,
        )
        acc.update(101.0, volume=100)
        acc.update(102.0, volume=200)
        acc.update(99.0, volume=150)
        assert acc.volume == 450

    def test_zero_volume_ticks_dont_change(self):
        acc = _Accumulator(
            open=100.0, high=100.0, low=100.0, close=100.0,
            tick_count=1, window_start=datetime(2026, 5, 31, 10, 0),
            volume=500,
        )
        acc.update(101.0, volume=0)
        assert acc.volume == 500


# ── CandleStore on_tick ──────────────────────────────────────────────────


class TestCandleStoreVolume:
    def _make_store(self):
        return CandleStore(
            logger=logging.getLogger("test_cs"),
            candle_interval_sec=60,
        )

    def test_on_tick_with_volume(self):
        cs = self._make_store()
        ts = datetime(2026, 5, 31, 10, 0, 15)
        cs.on_tick(12345, 100.0, ts, volume=500)
        assert 12345 in cs._accum
        assert cs._accum[12345].volume == 500

    def test_multiple_ticks_accumulate_volume(self):
        cs = self._make_store()
        ts = datetime(2026, 5, 31, 10, 0, 15)
        cs.on_tick(12345, 100.0, ts, volume=500)
        ts2 = datetime(2026, 5, 31, 10, 0, 30)
        cs.on_tick(12345, 101.0, ts2, volume=300)
        assert cs._accum[12345].volume == 800

    def test_on_tick_without_volume_defaults_zero(self):
        cs = self._make_store()
        ts = datetime(2026, 5, 31, 10, 0, 15)
        cs.on_tick(12345, 100.0, ts)
        assert cs._accum[12345].volume == 0

    def test_candle_close_carries_volume(self):
        cs = self._make_store()
        cs.set_token_map({12345: "RELIANCE"})
        captured = []
        cs.register_on_candle_close(lambda c: captured.append(c))

        ts1 = datetime(2026, 5, 31, 10, 0, 15)
        cs.on_tick(12345, 100.0, ts1, volume=500)
        ts2 = datetime(2026, 5, 31, 10, 0, 45)
        cs.on_tick(12345, 101.0, ts2, volume=300)

        cs._close_candles()

        assert len(captured) == 1
        assert captured[0].volume == 800
        assert captured[0].symbol == "RELIANCE"

    def test_synthetic_candle_has_zero_volume(self):
        cs = self._make_store()
        cs.set_token_map({12345: "RELIANCE"})
        captured = []
        cs.register_on_candle_close(lambda c: captured.append(c))

        ts1 = datetime(2026, 5, 31, 10, 0, 15)
        cs.on_tick(12345, 100.0, ts1, volume=500)

        cs._close_candles()
        cs._close_candles()

        assert len(captured) == 2
        assert captured[0].volume == 500
        assert captured[0].is_synthetic is False
        assert captured[1].volume == 0
        assert captured[1].is_synthetic is True
