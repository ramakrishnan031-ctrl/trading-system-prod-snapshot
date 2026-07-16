"""F1 (16-Jul-2026): order_placer populates trades.sector AT INSERT from the SAME canonical
source gate-8 uses (InstrumentCache.sector), buckets misses as "UNKNOWN" (never NULL), and fires
a one-shot data-quality alert once the UNKNOWN fraction exceeds the configured threshold.

Fail-on-old: the pre-fix create_trade call passed sector=None -> trades.sector was NULL on every
row -> StateStore.sector_exposure() summed to 0 -> the 40% sector cap never saw the resting book.
These exercise the resolver + DQ helpers directly (heavy collaborators are mocked)."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

from orders.order_placer import OrderPlacer


class _StubCache:
    """Minimal InstrumentCache double: sector(symbol) -> mapped value or 'UNKNOWN' (never raises)."""
    def __init__(self, mapping: dict) -> None:
        self._m = mapping

    def sector(self, symbol: str) -> str:
        return self._m.get(symbol, "UNKNOWN")


def _make_placer(*, notifier=None, sector_unknown_alert_pct: float = 0.20) -> OrderPlacer:
    return OrderPlacer(
        entry_engine=MagicMock(), order_manager=MagicMock(), fund_manager=MagicMock(),
        bus=MagicMock(), logger=logging.getLogger("test_op_sector"),
        order_monitor=MagicMock(), cost_calculator=MagicMock(),
        notifier=notifier, mode="PAPER",
        sector_unknown_alert_pct=sector_unknown_alert_pct,
    )


def test_resolve_trade_sector_from_instrument_cache() -> None:
    op = _make_placer()
    op.set_instrument_cache(_StubCache({"RELIANCE": "ENERGY", "TCS": "IT"}))
    assert op._resolve_trade_sector("RELIANCE") == "ENERGY"
    assert op._resolve_trade_sector("TCS") == "IT"
    assert op._resolve_trade_sector("NOTINANYINDEX") == "UNKNOWN"   # miss -> UNKNOWN bucket, never None


def test_resolve_trade_sector_no_cache_is_unknown_never_raises() -> None:
    op = _make_placer()   # instrument_cache stays None (not wired)
    assert op._resolve_trade_sector("ANYTHING") == "UNKNOWN"


def test_resolve_trade_sector_lookup_error_degrades_to_unknown() -> None:
    op = _make_placer()
    boom = MagicMock()
    boom.sector.side_effect = RuntimeError("cache broken")
    op.set_instrument_cache(boom)
    assert op._resolve_trade_sector("X") == "UNKNOWN"   # a lookup fault must never break placement


def test_sector_data_quality_alert_fires_once_past_threshold() -> None:
    notifier = MagicMock()
    op = _make_placer(notifier=notifier, sector_unknown_alert_pct=0.20)
    op.set_instrument_cache(_StubCache({}))   # every symbol -> UNKNOWN (100% > 20%)
    for i in range(op._SECTOR_DQ_MIN_SAMPLE + 3):
        op._resolve_trade_sector(f"SYM{i}")
    assert notifier.send.call_count == 1, "DQ alert must fire exactly once (one-shot per session)"
    assert notifier.send.call_args.kwargs.get("severity") == "WARNING"


def test_sector_data_quality_no_alert_below_threshold() -> None:
    notifier = MagicMock()
    op = _make_placer(notifier=notifier, sector_unknown_alert_pct=0.20)
    op.set_instrument_cache(_StubCache({f"K{i}": "IT" for i in range(20)}))
    for i in range(15):
        op._resolve_trade_sector(f"K{i}")     # all known
    op._resolve_trade_sector("UNK")           # 1/16 ~6% unknown < 20% threshold
    assert notifier.send.call_count == 0, "no DQ alert below the threshold"


def test_sector_data_quality_min_sample_guard() -> None:
    notifier = MagicMock()
    op = _make_placer(notifier=notifier, sector_unknown_alert_pct=0.20)
    op.set_instrument_cache(_StubCache({}))   # all UNKNOWN
    for i in range(op._SECTOR_DQ_MIN_SAMPLE - 1):   # below the minimum sample
        op._resolve_trade_sector(f"S{i}")
    assert notifier.send.call_count == 0, "must not alert before the minimum sample size"
