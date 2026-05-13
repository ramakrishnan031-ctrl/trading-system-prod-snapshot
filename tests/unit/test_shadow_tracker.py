"""
tests/unit/test_shadow_tracker.py

Validates orders/shadow_tracker.py against SH1-SH15 locked decisions.

Tests cover: event handling, cascade, hit detection, persistence, PnL,
EOD, alerts, token mapping, thread safety, is_real flag.

Run: python -m pytest tests/unit/test_shadow_tracker.py -v
Or:  python tests/unit/test_shadow_tracker.py  (standalone)
"""
from __future__ import annotations

import sys
import tempfile
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from core.events import EodSquareoffComplete, EventBus, PositionClosed
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.shadow_tracker import Inning, ShadowTracker, _check_hit, _calc_pnl

_IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / stubs
# ─────────────────────────────────────────────────────────────────────────────

_TS_OPEN = "2026-04-16T10:00:00"   # Naive IST, within market hours
_TS_ENTRY = "2026-04-16T09:35:00"
_TS_EXIT  = "2026-04-16T11:00:00"


class _FakeTimeAuthority:
    def __init__(self, ts: Optional[datetime] = None) -> None:
        self._ts = ts or datetime(2026, 4, 16, 10, 30, 0)  # naive IST

    def now_ist(self) -> datetime:
        return self._ts

    def today_ist(self) -> str:
        return self._ts.strftime("%Y-%m-%d")


class _FakeMarketWindows:
    def __init__(self, is_open: bool = True) -> None:
        self._open = is_open

    def is_market_open(self, now: datetime) -> bool:
        return self._open


class _FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, severity: str, title: str = "", body: str = "",
             source_module: str = "", context: dict | None = None) -> None:
        self.calls.append({
            "severity": severity,
            "title": title,
            "body": body,
            "source_module": source_module,
            "context": context,
        })


class _FakeLiveFeed:
    pass


@dataclass
class _MockStrategy:
    direction: str
    entry_method: str = "LIMIT"
    entry_offset_pct: float = 0.0
    sl_method: str = "FIXED_PCT"
    sl_pct: float = 0.02        # 2%
    sl_min_pct: float = 0.003
    sl_max_pct: float = 0.05
    tgt_method: str = "FIXED_PCT"
    tgt_pct: float = 0.04       # 4%
    tgt_risk_reward: float = 2.0


class _MockInstrumentCache:
    """Returns symbol from a dict of token->symbol."""
    def __init__(self, token_map: dict) -> None:
        self._map = token_map

    def get_by_token(self, token: int):
        if token not in self._map:
            raise KeyError(f"Unknown token: {token}")
        return MagicMock(symbol=self._map[token])


def _seed_signal(store: StateStore, signal_id: str, symbol: str = "RELIANCE") -> None:
    now_s = _TS_ENTRY
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals
              (signal_id, symbol, scanner, strategy, triggered_at, received_at,
               expires_at, status, fingerprint, fingerprint_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (signal_id, symbol, "scanner1", "strategy1",
             now_s, now_s, now_s, "TRADED",
             f"fp_{signal_id}", "2026-04-16"),
        )


def _seed_trade(
    store: StateStore,
    trade_id: str,
    signal_id: str,
    symbol: str = "RELIANCE",
    direction: str = "LONG",
    entry_actual: float = 2500.0,
    sl: float = 2450.0,
    tgt: float = 2600.0,
    exit_price: float = 2600.0,
    exit_reason: str = "TGT_HIT",
    exit_time: str = _TS_EXIT,
    strategy: str = "strategy1",
) -> None:
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO trades
              (trade_id, signal_id, symbol, direction, strategy, sector,
               qty_planned, qty_filled, entry_target_price, entry_actual_price,
               sl_initial, tgt_initial, margin_reserved, risk_amount,
               created_at, entry_time, exit_time, exit_price, exit_reason,
               gross_pnl, charges, net_pnl, status, order_protocol, updated_at)
            VALUES
              (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id, signal_id, symbol, direction, strategy, "ENERGY",
                10, 10, entry_actual, entry_actual,
                sl, tgt, 5000.0, 500.0,
                _TS_ENTRY, _TS_ENTRY, exit_time,
                exit_price, exit_reason,
                (exit_price - entry_actual) * 10 if direction == "LONG" else (entry_actual - exit_price) * 10,
                50.0, 0.0,
                "CLOSED", "LIMIT_TRIPLE", _TS_EXIT,
            ),
        )


def _make_tracker(
    store: StateStore,
    bus: EventBus,
    market_open: bool = True,
    notifier=None,
    strategies: dict = None,
    max_innings: int = 3,
    alert_per_inning: bool = True,
    enabled: bool = True,
    instrument_cache=None,
) -> ShadowTracker:
    ta = _FakeTimeAuthority()
    mw = _FakeMarketWindows(is_open=market_open)
    lf = _FakeLiveFeed()
    st = ShadowTracker(
        state_store=store,
        bus=bus,
        live_feed=lf,
        market_windows=mw,
        time_authority=ta,
        notifier=notifier,
        strategies=strategies,
        max_innings=max_innings,
        alert_per_inning=alert_per_inning,
        enabled=enabled,
    )
    if instrument_cache:
        st.set_instrument_cache(instrument_cache)
    return st


# ─────────────────────────────────────────────────────────────────────────────
# EVENT HANDLING
# ─────────────────────────────────────────────────────────────────────────────

def test_position_closed_creates_inning_1(tmp_path: Path) -> None:
    """PositionClosed event -> inning 1 persisted with is_real=True (SH3)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    notifier = _FakeNotifier()
    # max_innings=1 so no cascade; isolates inning-1 creation
    tracker = _make_tracker(store, bus, notifier=notifier, max_innings=1)

    _seed_signal(store, "sig1")
    _seed_trade(store, "t1", "sig1", exit_reason="TGT_HIT")

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t1",
        symbol="RELIANCE",
        signal_id="sig1",
        exit_price=2600.0,
        realized_pnl=1000.0,
    ))

    innings = store.get_innings_for_trade("t1")
    assert len(innings) == 1
    assert innings[0]["inning_number"] == 1
    assert innings[0]["is_real"] == 1
    assert innings[0]["symbol"] == "RELIANCE"
    assert innings[0]["direction"] == "LONG"
    assert abs(innings[0]["exit_price"] - 2600.0) < 0.01
    assert innings[0]["exit_reason"] == "TGT"
    print("  OK PositionClosed creates inning 1 with is_real=True")
    store.close()


def test_position_closed_trade_not_found_logs_error(tmp_path: Path) -> None:
    """PositionClosed for unknown trade_id -> no inning created, no crash (SH3)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    tracker = _make_tracker(store, bus)

    # Fire event with non-existent trade_id
    bus.publish(PositionClosed(
        source_module="test",
        trade_id="NO_SUCH_TRADE",
        symbol="RELIANCE",
        signal_id="sig_none",
        exit_price=100.0,
        realized_pnl=0.0,
    ))

    innings = store.get_innings_for_trade("NO_SUCH_TRADE")
    assert len(innings) == 0
    print("  OK unknown trade_id -> no inning, no crash")
    store.close()


def test_position_closed_disabled_no_op(tmp_path: Path) -> None:
    """PositionClosed when enabled=False -> no inning created (SH13)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    tracker = _make_tracker(store, bus, enabled=False)

    _seed_signal(store, "sig2")
    _seed_trade(store, "t2", "sig2")

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t2",
        symbol="RELIANCE",
        signal_id="sig2",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    innings = store.get_innings_for_trade("t2")
    assert len(innings) == 0
    print("  OK enabled=False -> PositionClosed is no-op")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# CASCADE
# ─────────────────────────────────────────────────────────────────────────────

def test_cascade_tgt_starts_inning_2(tmp_path: Path) -> None:
    """Inning 1 exit_reason=TGT -> inning 2 auto-started (SH3, SH4)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    tracker = _make_tracker(store, bus, strategies=strategies)

    _seed_signal(store, "sig3")
    _seed_trade(store, "t3", "sig3", exit_reason="TGT_HIT", exit_price=2600.0)

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t3",
        symbol="RELIANCE",
        signal_id="sig3",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    innings = store.get_innings_for_trade("t3")
    assert len(innings) == 2, f"Expected 2 innings, got {len(innings)}"
    assert innings[0]["inning_number"] == 1
    assert innings[1]["inning_number"] == 2
    assert innings[1]["is_real"] == 0
    assert innings[1]["entry_price"] == pytest.approx(2600.0)
    print("  OK TGT hit -> inning 2 auto-started")
    store.close()


def test_cascade_sl_starts_inning_2(tmp_path: Path) -> None:
    """Inning 1 exit_reason=SL -> inning 2 auto-started (SH3, SH4)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    tracker = _make_tracker(store, bus, strategies=strategies)

    _seed_signal(store, "sig4")
    _seed_trade(store, "t4", "sig4", exit_reason="SL_HIT", exit_price=2450.0)

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t4",
        symbol="RELIANCE",
        signal_id="sig4",
        exit_price=2450.0,
        realized_pnl=0.0,
    ))

    innings = store.get_innings_for_trade("t4")
    assert len(innings) == 2
    assert innings[1]["entry_price"] == pytest.approx(2450.0)
    print("  OK SL hit -> inning 2 auto-started with new entry=2450")
    store.close()


def test_cascade_inning_3_started_after_inning_2_sl(tmp_path: Path) -> None:
    """Inning 2 SL hit -> inning 3 auto-started (SH7 cascade)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    cache = _MockInstrumentCache({738561: "RELIANCE"})
    tracker = _make_tracker(store, bus, strategies=strategies,
                            instrument_cache=cache)

    _seed_signal(store, "sig5")
    _seed_trade(store, "t5", "sig5", exit_reason="TGT_HIT", exit_price=2600.0)

    # Inning 1 -> 2
    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t5",
        symbol="RELIANCE",
        signal_id="sig5",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    # Inning 2 is now active; simulate SL hit via tick
    # Inning 2 entry=2600, sl = 2600*(1-0.02) = 2548
    innings = store.get_innings_for_trade("t5")
    assert len(innings) == 2
    ing2_sl = innings[1]["sl_price"]

    # Push tick at SL
    tracker.on_tick({"instrument_token": 738561, "last_price": ing2_sl - 1.0})

    innings = store.get_innings_for_trade("t5")
    assert len(innings) == 3
    assert innings[2]["is_real"] == 0
    print(f"  OK inning 2 SL hit -> inning 3 started (sl={ing2_sl:.2f})")
    store.close()


def test_cascade_stops_at_max_innings(tmp_path: Path) -> None:
    """Inning 3 completes -> no inning 4 (max_innings=3) (SH7)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    cache = _MockInstrumentCache({738561: "RELIANCE"})
    tracker = _make_tracker(store, bus, strategies=strategies,
                            instrument_cache=cache, max_innings=3)

    _seed_signal(store, "sig6")
    _seed_trade(store, "t6", "sig6", exit_reason="TGT_HIT", exit_price=2600.0)

    # Inning 1 -> 2
    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t6",
        symbol="RELIANCE",
        signal_id="sig6",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    innings = store.get_innings_for_trade("t6")
    ing2_sl = innings[1]["sl_price"]

    # Inning 2 -> SL hit -> inning 3
    tracker.on_tick({"instrument_token": 738561, "last_price": ing2_sl - 1.0})
    innings = store.get_innings_for_trade("t6")
    assert len(innings) == 3
    ing3_sl = innings[2]["sl_price"]

    # Inning 3 -> SL hit -> no inning 4
    tracker.on_tick({"instrument_token": 738561, "last_price": ing3_sl - 1.0})
    innings = store.get_innings_for_trade("t6")
    assert len(innings) == 3, f"Expected 3 innings max, got {len(innings)}"
    print("  OK max_innings=3 stops cascade after inning 3")
    store.close()


def test_cascade_eod_exit_no_inning_2(tmp_path: Path) -> None:
    """Inning 1 exit_reason=EOD -> NO inning 2 started (SH3, SH7)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    tracker = _make_tracker(store, bus)

    _seed_signal(store, "sig7")
    _seed_trade(store, "t7", "sig7", exit_reason="EOD", exit_price=2510.0)

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t7",
        symbol="RELIANCE",
        signal_id="sig7",
        exit_price=2510.0,
        realized_pnl=0.0,
    ))

    innings = store.get_innings_for_trade("t7")
    assert len(innings) == 1
    assert innings[0]["exit_reason"] == "EOD"
    print("  OK EOD exit -> only 1 inning, no cascade")
    store.close()


def test_cascade_market_closed_no_inning_2(tmp_path: Path) -> None:
    """Inning 1 TGT but market closed -> NO inning 2 (SH3)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    tracker = _make_tracker(store, bus, market_open=False)

    _seed_signal(store, "sig8")
    _seed_trade(store, "t8", "sig8", exit_reason="TGT_HIT")

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t8",
        symbol="RELIANCE",
        signal_id="sig8",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    innings = store.get_innings_for_trade("t8")
    assert len(innings) == 1
    print("  OK market closed -> no inning 2 started")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# HIT DETECTION (SH6)
# ─────────────────────────────────────────────────────────────────────────────

def _make_inning(direction: str, sl: float, tgt: float) -> Inning:
    ts = datetime(2026, 4, 16, 10, 0, 0)
    return Inning(
        inning_number=2, trade_id="t_hit", symbol="RELIANCE",
        direction=direction, entry_price=100.0, entry_ts=ts,
        sl_price=sl, tgt_price=tgt,
        exit_price=None, exit_ts=None, exit_reason=None,
        duration_sec=None, pnl_pct=None, pnl_per_share=None,
        is_real=False,
    )


def test_hit_long_sl(tmp_path: Path) -> None:
    """LONG: SL hit when ltp <= sl_price (SH6)."""
    ing = _make_inning("LONG", sl=90.0, tgt=120.0)
    assert _check_hit(ing, 89.99) == "SL"
    assert _check_hit(ing, 90.0) == "SL"   # boundary
    print("  OK LONG SL hit detected (boundary + below)")


def test_hit_long_tgt(tmp_path: Path) -> None:
    """LONG: TGT hit when ltp >= tgt_price (SH6)."""
    ing = _make_inning("LONG", sl=90.0, tgt=120.0)
    assert _check_hit(ing, 120.01) == "TGT"
    assert _check_hit(ing, 120.0) == "TGT"  # boundary
    print("  OK LONG TGT hit detected (boundary + above)")


def test_hit_long_between_no_hit(tmp_path: Path) -> None:
    """LONG: ltp between SL and TGT -> no hit (SH6)."""
    ing = _make_inning("LONG", sl=90.0, tgt=120.0)
    assert _check_hit(ing, 100.0) is None
    assert _check_hit(ing, 90.01) is None
    assert _check_hit(ing, 119.99) is None
    print("  OK LONG no hit when ltp between SL and TGT")


def test_hit_short_sl(tmp_path: Path) -> None:
    """SHORT: SL hit when ltp >= sl_price (SH6)."""
    ing = _make_inning("SHORT", sl=110.0, tgt=80.0)
    assert _check_hit(ing, 110.01) == "SL"
    assert _check_hit(ing, 110.0) == "SL"  # boundary
    print("  OK SHORT SL hit detected")


def test_hit_short_tgt(tmp_path: Path) -> None:
    """SHORT: TGT hit when ltp <= tgt_price (SH6)."""
    ing = _make_inning("SHORT", sl=110.0, tgt=80.0)
    assert _check_hit(ing, 79.99) == "TGT"
    assert _check_hit(ing, 80.0) == "TGT"  # boundary
    print("  OK SHORT TGT hit detected")


def test_hit_short_between_no_hit(tmp_path: Path) -> None:
    """SHORT: ltp between TGT and SL -> no hit (SH6)."""
    ing = _make_inning("SHORT", sl=110.0, tgt=80.0)
    assert _check_hit(ing, 95.0) is None
    assert _check_hit(ing, 80.01) is None
    assert _check_hit(ing, 109.99) is None
    print("  OK SHORT no hit when ltp between TGT and SL")


# ─────────────────────────────────────────────────────────────────────────────
# SIMULATED INNING CALCULATION (SH4)
# ─────────────────────────────────────────────────────────────────────────────

def test_inning2_fixed_pct_long(tmp_path: Path) -> None:
    """LONG FIXED_PCT: inning 2 sl/tgt computed correctly from inning 1 exit (SH4)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    # sl_pct=0.02, tgt_pct=0.04
    strategies = {"strategy1": _MockStrategy(direction="LONG", sl_pct=0.02, tgt_pct=0.04)}
    tracker = _make_tracker(store, bus, strategies=strategies)

    _seed_signal(store, "sig_f1")
    _seed_trade(store, "t_f1", "sig_f1", exit_reason="TGT_HIT",
                exit_price=2600.0, entry_actual=2500.0, sl=2450.0, tgt=2600.0)

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t_f1",
        symbol="RELIANCE",
        signal_id="sig_f1",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    innings = store.get_innings_for_trade("t_f1")
    assert len(innings) == 2
    ing2 = innings[1]
    # entry=2600, sl = 2600 * (1-0.02) = 2548
    assert abs(ing2["entry_price"] - 2600.0) < 0.01
    assert abs(ing2["sl_price"] - 2548.0) < 0.5
    # tgt = 2600 * (1+0.04) = 2704
    assert abs(ing2["tgt_price"] - 2704.0) < 0.5
    print(f"  OK LONG FIXED_PCT inning 2 sl={ing2['sl_price']:.2f} tgt={ing2['tgt_price']:.2f}")
    store.close()


def test_inning2_fixed_pct_short(tmp_path: Path) -> None:
    """SHORT FIXED_PCT: inning 2 sl/tgt computed correctly from inning 1 exit (SH4)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="SHORT", sl_pct=0.02, tgt_pct=0.04)}
    tracker = _make_tracker(store, bus, strategies=strategies)

    _seed_signal(store, "sig_f2")
    _seed_trade(store, "t_f2", "sig_f2", direction="SHORT",
                exit_reason="TGT_HIT", exit_price=2400.0, entry_actual=2500.0,
                sl=2550.0, tgt=2400.0)

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t_f2",
        symbol="RELIANCE",
        signal_id="sig_f2",
        exit_price=2400.0,
        realized_pnl=0.0,
    ))

    innings = store.get_innings_for_trade("t_f2")
    assert len(innings) == 2
    ing2 = innings[1]
    # SHORT entry=2400, sl = 2400*(1+0.02) = 2448, tgt = 2400*(1-0.04) = 2304
    assert abs(ing2["entry_price"] - 2400.0) < 0.01
    assert abs(ing2["sl_price"] - 2448.0) < 0.5
    assert abs(ing2["tgt_price"] - 2304.0) < 0.5
    print(f"  OK SHORT FIXED_PCT inning 2 sl={ing2['sl_price']:.2f} tgt={ing2['tgt_price']:.2f}")
    store.close()


def test_inning2_risk_reward_tgt(tmp_path: Path) -> None:
    """RISK_REWARD target method applied to new entry (SH4)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    # sl_pct=0.02 (sl=entry*0.98), RISK_REWARD ratio=2.0
    strat = _MockStrategy(direction="LONG", sl_pct=0.02, tgt_method="RISK_REWARD",
                          tgt_risk_reward=2.0)
    strategies = {"strategy1": strat}
    tracker = _make_tracker(store, bus, strategies=strategies)

    _seed_signal(store, "sig_rr")
    _seed_trade(store, "t_rr", "sig_rr", exit_reason="TGT_HIT", exit_price=2600.0)

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t_rr",
        symbol="RELIANCE",
        signal_id="sig_rr",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    innings = store.get_innings_for_trade("t_rr")
    assert len(innings) == 2
    ing2 = innings[1]
    entry = ing2["entry_price"]   # 2600
    sl = ing2["sl_price"]         # 2600 * 0.98 = 2548
    sl_dist = abs(entry - sl)
    expected_tgt = entry + sl_dist * 2.0
    assert abs(ing2["tgt_price"] - expected_tgt) < 0.5
    print(f"  OK RISK_REWARD inning 2 tgt={ing2['tgt_price']:.2f} (expected={expected_tgt:.2f})")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# PERSISTENCE (SH9, SH10)
# ─────────────────────────────────────────────────────────────────────────────

def test_insert_inning_stores_all_fields(tmp_path: Path) -> None:
    """insert_inning stores all Inning fields correctly (SH10)."""
    store = StateStore(tmp_path / "test.db")
    ts = datetime(2026, 4, 16, 10, 0, 0)
    inning = Inning(
        inning_number=1, trade_id="t_persist", symbol="TCS",
        direction="LONG", entry_price=3000.0, entry_ts=ts,
        sl_price=2940.0, tgt_price=3120.0,
        exit_price=3120.0, exit_ts=ts,
        exit_reason="TGT", duration_sec=3600,
        pnl_pct=4.0, pnl_per_share=120.0,
        is_real=True,
    )
    store.insert_inning(inning)

    rows = store.get_innings_for_trade("t_persist")
    assert len(rows) == 1
    r = rows[0]
    assert r["trade_id"] == "t_persist"
    assert r["inning_number"] == 1
    assert r["symbol"] == "TCS"
    assert r["direction"] == "LONG"
    assert abs(r["entry_price"] - 3000.0) < 0.01
    assert abs(r["sl_price"] - 2940.0) < 0.01
    assert abs(r["tgt_price"] - 3120.0) < 0.01
    assert abs(r["exit_price"] - 3120.0) < 0.01
    assert r["exit_reason"] == "TGT"
    assert r["duration_sec"] == 3600
    assert abs(r["pnl_pct"] - 4.0) < 0.01
    assert abs(r["pnl_per_share"] - 120.0) < 0.01
    assert r["is_real"] == 1
    print("  OK insert_inning stores all fields correctly")
    store.close()


def test_update_inning_close_populates_fields(tmp_path: Path) -> None:
    """update_inning_close() sets exit fields on an open inning (SH10)."""
    store = StateStore(tmp_path / "test.db")
    ts = datetime(2026, 4, 16, 10, 0, 0)
    inning = Inning(
        inning_number=2, trade_id="t_upd_close", symbol="INFY",
        direction="SHORT", entry_price=1500.0, entry_ts=ts,
        sl_price=1530.0, tgt_price=1440.0,
        exit_price=None, exit_ts=None, exit_reason=None,
        duration_sec=None, pnl_pct=None, pnl_per_share=None,
        is_real=False,
    )
    store.insert_inning(inning)

    store.update_inning_close(
        trade_id="t_upd_close",
        inning_number=2,
        exit_price=1440.0,
        exit_ts="2026-04-16T12:00:00",
        exit_reason="TGT",
        duration_sec=7200,
        pnl_pct=4.0,
        pnl_per_share=60.0,
    )

    rows = store.get_innings_for_trade("t_upd_close")
    r = rows[0]
    assert abs(r["exit_price"] - 1440.0) < 0.01
    assert r["exit_reason"] == "TGT"
    assert r["duration_sec"] == 7200
    assert abs(r["pnl_pct"] - 4.0) < 0.01
    print("  OK update_inning_close populates closure fields")
    store.close()


def test_get_innings_for_trade_order(tmp_path: Path) -> None:
    """get_innings_for_trade returns innings ordered by inning_number (SH10)."""
    store = StateStore(tmp_path / "test.db")
    ts = datetime(2026, 4, 16, 10, 0, 0)
    for n in [3, 1, 2]:
        store.insert_inning(Inning(
            inning_number=n, trade_id="t_ord", symbol="SBIN",
            direction="LONG", entry_price=500.0 + n, entry_ts=ts,
            sl_price=490.0, tgt_price=520.0,
            exit_price=None, exit_ts=None, exit_reason=None,
            duration_sec=None, pnl_pct=None, pnl_per_share=None,
            is_real=(n == 1),
        ))

    rows = store.get_innings_for_trade("t_ord")
    assert [r["inning_number"] for r in rows] == [1, 2, 3]
    print("  OK get_innings_for_trade returns ordered by inning_number")
    store.close()


def test_get_innings_for_date_filters(tmp_path: Path) -> None:
    """get_innings_for_date returns only innings for that date (SH10)."""
    store = StateStore(tmp_path / "test.db")
    ts_today = datetime(2026, 4, 16, 10, 0, 0)
    ts_other = datetime(2026, 4, 15, 10, 0, 0)

    store.insert_inning(Inning(
        inning_number=1, trade_id="t_date1", symbol="RELIANCE",
        direction="LONG", entry_price=2500.0, entry_ts=ts_today,
        sl_price=2450.0, tgt_price=2600.0,
        exit_price=None, exit_ts=None, exit_reason=None,
        duration_sec=None, pnl_pct=None, pnl_per_share=None, is_real=True,
    ))
    store.insert_inning(Inning(
        inning_number=1, trade_id="t_date2", symbol="TCS",
        direction="LONG", entry_price=3000.0, entry_ts=ts_other,
        sl_price=2940.0, tgt_price=3120.0,
        exit_price=None, exit_ts=None, exit_reason=None,
        duration_sec=None, pnl_pct=None, pnl_per_share=None, is_real=True,
    ))

    rows_today = store.get_innings_for_date("2026-04-16")
    rows_other = store.get_innings_for_date("2026-04-15")
    assert len(rows_today) == 1 and rows_today[0]["trade_id"] == "t_date1"
    assert len(rows_other) == 1 and rows_other[0]["trade_id"] == "t_date2"
    print("  OK get_innings_for_date filters by date correctly")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# PNL (SH7)
# ─────────────────────────────────────────────────────────────────────────────

def test_pnl_long_profit(tmp_path: Path) -> None:
    """LONG profit: pnl_per_share positive, pnl_pct positive (SH7)."""
    pps, pct = _calc_pnl("LONG", 2500.0, 2600.0)
    assert abs(pps - 100.0) < 0.01
    assert abs(pct - 4.0) < 0.01
    print(f"  OK LONG profit pnl_per_share={pps:.2f} pnl_pct={pct:.2f}%")


def test_pnl_long_loss(tmp_path: Path) -> None:
    """LONG loss: pnl_per_share negative, pnl_pct negative (SH7)."""
    pps, pct = _calc_pnl("LONG", 2500.0, 2450.0)
    assert abs(pps - (-50.0)) < 0.01
    assert pct < 0
    print(f"  OK LONG loss pnl_per_share={pps:.2f} pnl_pct={pct:.2f}%")


def test_pnl_short_profit(tmp_path: Path) -> None:
    """SHORT profit: entry > exit -> positive pnl (SH7)."""
    pps, pct = _calc_pnl("SHORT", 2500.0, 2400.0)
    assert abs(pps - 100.0) < 0.01
    assert pct > 0
    print(f"  OK SHORT profit pnl_per_share={pps:.2f} pnl_pct={pct:.2f}%")


def test_pnl_short_loss(tmp_path: Path) -> None:
    """SHORT loss: exit > entry -> negative pnl (SH7)."""
    pps, pct = _calc_pnl("SHORT", 2500.0, 2550.0)
    assert abs(pps - (-50.0)) < 0.01
    assert pct < 0
    print(f"  OK SHORT loss pnl_per_share={pps:.2f} pnl_pct={pct:.2f}%")


# ─────────────────────────────────────────────────────────────────────────────
# EOD HANDLING (SH8)
# ─────────────────────────────────────────────────────────────────────────────

def test_eod_closes_active_innings(tmp_path: Path) -> None:
    """EodSquareoffComplete -> active innings closed with exit_reason=EOD (SH8)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    cache = _MockInstrumentCache({738561: "RELIANCE"})
    tracker = _make_tracker(store, bus, strategies=strategies,
                            instrument_cache=cache)

    _seed_signal(store, "sig_eod")
    _seed_trade(store, "t_eod", "sig_eod", exit_reason="TGT_HIT", exit_price=2600.0)

    # Create inning 1 + cascade to inning 2
    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t_eod",
        symbol="RELIANCE",
        signal_id="sig_eod",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    # Inning 2 is now active; fire EOD
    bus.publish(EodSquareoffComplete(
        source_module="test",
        fired_date="2026-04-16",
    ))

    innings = store.get_innings_for_trade("t_eod")
    assert len(innings) == 2
    assert innings[1]["exit_reason"] == "EOD"
    assert innings[1]["exit_price"] is not None
    print("  OK EOD closes active inning 2 with exit_reason=EOD")
    store.close()


def test_eod_no_new_innings_after_fire(tmp_path: Path) -> None:
    """No new innings started after EodSquareoffComplete fires (SH8)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    cache = _MockInstrumentCache({738561: "RELIANCE"})
    tracker = _make_tracker(store, bus, strategies=strategies,
                            instrument_cache=cache)

    _seed_signal(store, "sig_noeod")
    _seed_trade(store, "t_noeod", "sig_noeod", exit_reason="TGT_HIT", exit_price=2600.0)

    # Fire EOD first
    bus.publish(EodSquareoffComplete(
        source_module="test",
        fired_date="2026-04-16",
    ))

    # Then PositionClosed should create inning 1 but not cascade to inning 2
    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t_noeod",
        symbol="RELIANCE",
        signal_id="sig_noeod",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    innings = store.get_innings_for_trade("t_noeod")
    assert len(innings) == 1, f"Expected 1 (no cascade after EOD), got {len(innings)}"
    print("  OK no inning 2 started after EOD fired")
    store.close()


def test_eod_disabled_no_op(tmp_path: Path) -> None:
    """EodSquareoffComplete when enabled=False -> no innings closed (SH13)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    tracker = _make_tracker(store, bus, enabled=False)

    bus.publish(EodSquareoffComplete(source_module="test"))
    # No crash; nothing happens
    print("  OK EodSquareoffComplete with enabled=False is no-op")
    store.close()


def test_fix023_eod_closes_three_active_innings(tmp_path: Path) -> None:
    """
    FIX-023: Open 3 simulated innings, fire EOD without SL/TGT hit.
    All 3 innings closed with reason=EOD, _active_innings is empty.
    """
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    cache = _MockInstrumentCache({
        738561: "RELIANCE",
        408065: "INFY",
        5633: "ACC",
    })
    tracker = _make_tracker(store, bus, strategies=strategies, instrument_cache=cache)

    # Create 3 trades, each cascading to inning 2
    for tid, sid, sym in [
        ("t_a", "sig_a", "RELIANCE"),
        ("t_b", "sig_b", "INFY"),
        ("t_c", "sig_c", "ACC"),
    ]:
        _seed_signal(store, sid, symbol=sym)
        _seed_trade(store, tid, sid, symbol=sym, exit_reason="TGT_HIT", exit_price=2600.0)
        bus.publish(PositionClosed(
            source_module="test",
            trade_id=tid,
            symbol=sym,
            signal_id=sid,
            exit_price=2600.0,
            realized_pnl=0.0,
        ))

    # All 3 should now have inning 2 active
    assert len(tracker._active_innings) == 3, \
        f"Expected 3 active innings, got {len(tracker._active_innings)}"

    # Fire EOD
    bus.publish(EodSquareoffComplete(source_module="test", fired_date="2026-04-16"))

    # All innings should be closed
    assert len(tracker._active_innings) == 0, \
        f"Expected 0 active innings after EOD, got {len(tracker._active_innings)}"

    # Verify DB shows all innings closed with reason=EOD
    for tid in ["t_a", "t_b", "t_c"]:
        innings = store.get_innings_for_trade(tid)
        assert len(innings) == 2, f"{tid}: expected 2 innings"
        assert innings[1]["exit_reason"] == "EOD", \
            f"{tid} inning 2: expected exit_reason=EOD, got {innings[1]['exit_reason']}"
        assert innings[1]["exit_price"] is not None

    print("  OK FIX-023: 3 active innings closed at EOD, _active_innings empty")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# ALERTS (SH7, SH3)
# ─────────────────────────────────────────────────────────────────────────────

def test_alerts_on_inning_close(tmp_path: Path) -> None:
    """alert_per_inning=True -> notifier.send called on each inning close (SH7)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    notifier = _FakeNotifier()
    tracker = _make_tracker(store, bus, notifier=notifier, alert_per_inning=True)

    _seed_signal(store, "sig_alert")
    _seed_trade(store, "t_alert", "sig_alert", exit_reason="TGT_HIT", exit_price=2600.0)

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t_alert",
        symbol="RELIANCE",
        signal_id="sig_alert",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    assert len(notifier.calls) >= 1
    call = notifier.calls[0]
    assert call["severity"] == "INFO"
    # New format: symbol is in title, not body
    assert "RELIANCE" in call["title"]
    assert "Inning: 1" in call["body"]
    print(f"  OK alert sent on inning close (severity={call['severity']})")
    store.close()


def test_no_alerts_when_disabled(tmp_path: Path) -> None:
    """alert_per_inning=False -> no notifier calls (SH7)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    notifier = _FakeNotifier()
    tracker = _make_tracker(store, bus, notifier=notifier, alert_per_inning=False)

    _seed_signal(store, "sig_noalert")
    _seed_trade(store, "t_noalert", "sig_noalert", exit_reason="TGT_HIT")

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t_noalert",
        symbol="RELIANCE",
        signal_id="sig_noalert",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    assert len(notifier.calls) == 0
    print("  OK alert_per_inning=False -> no notifier calls")
    store.close()


def test_alert_contains_symbol_and_pnl(tmp_path: Path) -> None:
    """Alert message contains symbol, inning_number, entry, exit, pnl_pct (SH7)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    notifier = _FakeNotifier()
    tracker = _make_tracker(store, bus, notifier=notifier)

    _seed_signal(store, "sig_alertfields")
    _seed_trade(store, "t_af", "sig_alertfields", exit_reason="TGT_HIT", exit_price=2600.0)

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t_af",
        symbol="RELIANCE",
        signal_id="sig_alertfields",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    assert len(notifier.calls) >= 1
    call0 = notifier.calls[0]
    title = call0["title"]
    msg = call0["body"]
    # New format: symbol in title; body has exit price and pnl_pct
    assert "RELIANCE" in title
    # Price shown with comma-formatting (₹2,600.00)
    assert "2,600" in msg or "2600" in msg or "2,500" in msg or "2500" in msg
    assert "%" in msg                       # pnl_pct
    print("  OK alert message contains required fields")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# TOKEN MAPPING (SH5)
# ─────────────────────────────────────────────────────────────────────────────

def test_on_tick_resolves_symbol_and_checks_innings(tmp_path: Path) -> None:
    """on_tick uses instrument_cache to resolve symbol; checks active innings (SH5)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    cache = _MockInstrumentCache({738561: "RELIANCE"})
    tracker = _make_tracker(store, bus, strategies=strategies,
                            instrument_cache=cache)

    _seed_signal(store, "sig_tick")
    _seed_trade(store, "t_tick", "sig_tick", exit_reason="TGT_HIT", exit_price=2600.0)

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t_tick",
        symbol="RELIANCE",
        signal_id="sig_tick",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    # Inning 2 is active. Tick at TGT closes it.
    innings = store.get_innings_for_trade("t_tick")
    assert len(innings) == 2
    ing2_tgt = innings[1]["tgt_price"]

    tracker.on_tick({"instrument_token": 738561, "last_price": ing2_tgt + 1.0})

    innings = store.get_innings_for_trade("t_tick")
    assert innings[1]["exit_reason"] == "TGT"
    print(f"  OK on_tick resolves symbol and closes inning on TGT hit")
    store.close()


def test_on_tick_unknown_token_no_crash(tmp_path: Path) -> None:
    """Unknown token in tick -> log WARNING, skip (SH5)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    cache = _MockInstrumentCache({738561: "RELIANCE"})
    tracker = _make_tracker(store, bus, instrument_cache=cache)

    # Unknown token should not crash
    tracker.on_tick({"instrument_token": 99999999, "last_price": 100.0})
    print("  OK unknown token in tick -> no crash")
    store.close()


def test_on_tick_no_cache_no_crash(tmp_path: Path) -> None:
    """on_tick with no instrument_cache wired -> silent skip (SH5)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    tracker = _make_tracker(store, bus)  # no cache wired

    tracker.on_tick({"instrument_token": 738561, "last_price": 100.0})
    print("  OK on_tick with no cache -> no crash")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# IS_REAL FLAG (SH1)
# ─────────────────────────────────────────────────────────────────────────────

def test_is_real_flags(tmp_path: Path) -> None:
    """Inning 1: is_real=True; Innings 2+: is_real=False (SH1, SH3)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    cache = _MockInstrumentCache({738561: "RELIANCE"})
    tracker = _make_tracker(store, bus, strategies=strategies,
                            instrument_cache=cache)

    _seed_signal(store, "sig_real")
    _seed_trade(store, "t_real", "sig_real", exit_reason="TGT_HIT", exit_price=2600.0)

    bus.publish(PositionClosed(
        source_module="test",
        trade_id="t_real",
        symbol="RELIANCE",
        signal_id="sig_real",
        exit_price=2600.0,
        realized_pnl=0.0,
    ))

    innings = store.get_innings_for_trade("t_real")
    assert len(innings) == 2
    assert innings[0]["is_real"] == 1   # inning 1 is real
    assert innings[1]["is_real"] == 0   # inning 2 is simulated
    print("  OK is_real=1 for inning 1, is_real=0 for inning 2")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# THREAD SAFETY (SH14)
# ─────────────────────────────────────────────────────────────────────────────

def test_concurrent_position_closed_and_tick(tmp_path: Path) -> None:
    """Concurrent PositionClosed + on_tick for same symbol: no races (SH14)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    cache = _MockInstrumentCache({738561: "RELIANCE", 779521: "SBIN"})
    tracker = _make_tracker(store, bus, strategies=strategies,
                            instrument_cache=cache)

    _seed_signal(store, "sig_th1")
    _seed_trade(store, "t_th1", "sig_th1", symbol="RELIANCE",
                exit_reason="TGT_HIT", exit_price=2600.0)

    errors = []

    def fire_event():
        try:
            bus.publish(PositionClosed(
                source_module="thread",
                trade_id="t_th1",
                symbol="RELIANCE",
                signal_id="sig_th1",
                exit_price=2600.0,
                realized_pnl=0.0,
            ))
        except Exception as exc:
            errors.append(exc)

    def fire_ticks():
        try:
            for _ in range(20):
                tracker.on_tick({"instrument_token": 738561, "last_price": 2500.0})
                tracker.on_tick({"instrument_token": 779521, "last_price": 500.0})
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=fire_event)
    t2 = threading.Thread(target=fire_ticks)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not errors, f"Thread errors: {errors}"
    print("  OK concurrent PositionClosed + on_tick no races")
    store.close()


def test_concurrent_ticks_two_threads(tmp_path: Path) -> None:
    """Concurrent on_tick from two threads for different symbols: both processed (SH14)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    cache = _MockInstrumentCache({738561: "RELIANCE", 779521: "SBIN"})
    tracker = _make_tracker(store, bus, instrument_cache=cache)

    errors = []

    def tick_thread(token: int, price: float):
        try:
            for _ in range(50):
                tracker.on_tick({"instrument_token": token, "last_price": price})
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=tick_thread, args=(738561, 2500.0))
    t2 = threading.Thread(target=tick_thread, args=(779521, 500.0))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not errors, f"Thread errors: {errors}"
    print("  OK concurrent ticks from two threads no races")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# BL-13: EOD guard (restart persistence + per-IST-date idempotency)
# ─────────────────────────────────────────────────────────────────────────────

def _insert_eod_log(store: StateStore, date_iso: str) -> None:
    """Simulate upstream eod_squareoff writing its log row for date_iso."""
    store.insert_eod_squareoff_log(
        fired_date=date_iso,
        fired_at=f"{date_iso}T15:17:00",
        positions_attempted=0, positions_succeeded=0, positions_failed=0,
        cancels_attempted=0, cancels_succeeded=0, cancels_failed=0,
        duration_sec=0.0,
    )


def test_bl13_eod_fires_once_on_first_event(tmp_path: Path) -> None:
    """First EodSquareoffComplete sets _eod_fired=True and _eod_fired_date=today."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    tracker = _make_tracker(store, bus)
    assert tracker._eod_fired is False
    assert tracker._eod_fired_date is None

    bus.publish(EodSquareoffComplete(source_module="test", fired_date="2026-04-16"))

    assert tracker._eod_fired is True
    assert tracker._eod_fired_date == "2026-04-16"
    print("  OK first EOD event marks bool + date")
    store.close()


def test_bl13_second_event_same_day_is_noop(tmp_path: Path) -> None:
    """Second EodSquareoffComplete same day: guard trips; innings not re-closed."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    cache = _MockInstrumentCache({738561: "RELIANCE"})
    tracker = _make_tracker(store, bus, strategies=strategies, instrument_cache=cache)

    _seed_signal(store, "sig_dup")
    _seed_trade(store, "t_dup", "sig_dup", exit_reason="TGT_HIT", exit_price=2600.0)
    bus.publish(PositionClosed(
        source_module="test", trade_id="t_dup", symbol="RELIANCE",
        signal_id="sig_dup", exit_price=2600.0, realized_pnl=0.0,
    ))
    # Inning 2 now active; fire first EOD -> closes inning 2 as EOD
    bus.publish(EodSquareoffComplete(source_module="test", fired_date="2026-04-16"))
    first_exit_ts = store.get_innings_for_trade("t_dup")[1]["exit_ts"]
    assert store.get_innings_for_trade("t_dup")[1]["exit_reason"] == "EOD"

    # Second same-day event: guard must trip; no re-close
    bus.publish(EodSquareoffComplete(source_module="test", fired_date="2026-04-16"))
    second_exit_ts = store.get_innings_for_trade("t_dup")[1]["exit_ts"]
    assert second_exit_ts == first_exit_ts, \
        "exit_ts should not change on duplicate EOD"
    print("  OK second same-day EOD is noop")
    store.close()


def test_bl13_new_day_event_processes_normally(tmp_path: Path) -> None:
    """After date change, new EOD event updates _eod_fired_date (guard not frozen to D0)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    ta = _FakeTimeAuthority(datetime(2026, 4, 16, 10, 30, 0))
    mw = _FakeMarketWindows()
    lf = _FakeLiveFeed()
    tracker = ShadowTracker(
        state_store=store, bus=bus, live_feed=lf,
        market_windows=mw, time_authority=ta,
    )

    # Day 1 EOD
    bus.publish(EodSquareoffComplete(source_module="test", fired_date="2026-04-16"))
    assert tracker._eod_fired_date == "2026-04-16"

    # Advance date in time_authority (simulates clock rollover without restart)
    ta._ts = datetime(2026, 4, 17, 10, 30, 0)

    # Day 2 EOD: guard sees different today; handler runs and updates date
    bus.publish(EodSquareoffComplete(source_module="test", fired_date="2026-04-17"))
    assert tracker._eod_fired_date == "2026-04-17"
    print("  OK new-day EOD updates _eod_fired_date (per-IST-date guard)")
    store.close()


def test_bl13_mark_persists_across_restart(tmp_path: Path) -> None:
    """Restart after upstream EOD: tracker2 restores _eod_fired from eod_squareoff_log."""
    store = StateStore(tmp_path / "test.db")
    bus1 = EventBus()
    tracker1 = _make_tracker(store, bus1)
    assert tracker1._eod_fired is False

    # Upstream wrote the log row (mark-before-fire ordering)
    today_iso = tracker1._today_ist()
    _insert_eod_log(store, today_iso)

    # Simulated restart: new bus, new tracker, same DB
    bus2 = EventBus()
    tracker2 = _make_tracker(store, bus2)
    assert tracker2._eod_fired is True
    assert tracker2._eod_fired_date == today_iso
    print("  OK mark persists across restart via eod_squareoff_log")
    store.close()


def test_bl13_post_eod_restart_blocks_cascade(tmp_path: Path) -> None:
    """Post-EOD restart: inning 1 created but cascade to inning 2 blocked."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    # Pre-seed eod_squareoff_log before tracker constructed
    _insert_eod_log(store, "2026-04-16")

    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    cache = _MockInstrumentCache({738561: "RELIANCE"})
    tracker = _make_tracker(store, bus, strategies=strategies, instrument_cache=cache)
    assert tracker._eod_fired is True
    assert tracker._eod_fired_date == "2026-04-16"

    _seed_signal(store, "sig_post")
    _seed_trade(store, "t_post", "sig_post", exit_reason="TGT_HIT", exit_price=2600.0)
    bus.publish(PositionClosed(
        source_module="test", trade_id="t_post", symbol="RELIANCE",
        signal_id="sig_post", exit_price=2600.0, realized_pnl=0.0,
    ))
    innings = store.get_innings_for_trade("t_post")
    assert len(innings) == 1, f"Expected only inning 1, got {len(innings)}"
    print("  OK post-EOD restart blocks cascade to inning 2")
    store.close()


def test_bl13_no_log_row_startup_leaves_eod_fired_false(tmp_path: Path) -> None:
    """No eod_squareoff_log row on startup: defaults False/None."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    tracker = _make_tracker(store, bus)
    assert tracker._eod_fired is False
    assert tracker._eod_fired_date is None
    print("  OK no-row startup defaults to False/None")
    store.close()


def test_bl13_guard_uses_ist_date(tmp_path: Path) -> None:
    """Guard uses time_authority.today_ist() (IST), not event.fired_date or UTC."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    tracker = _make_tracker(store, bus)
    # _FakeTimeAuthority.today_ist() returns "2026-04-16"; event carries a different date
    bus.publish(EodSquareoffComplete(source_module="test", fired_date="1999-12-31"))
    assert tracker._eod_fired_date == "2026-04-16", \
        "guard must use time_authority.today_ist(), not event.fired_date"
    print("  OK guard uses time_authority.today_ist() (IST-aware)")
    store.close()


def test_bl13_log_row_query_failure_degrades_gracefully(tmp_path: Path) -> None:
    """Startup query raises: ShadowTracker constructs, logs error, runs with _eod_fired=False."""
    real_store = StateStore(tmp_path / "test.db")

    class _FailingStore:
        """Proxies StateStore but raises on get_eod_squareoff_log_for_date."""
        def __init__(self, real):
            self._real = real
        def __getattr__(self, name):
            return getattr(self._real, name)
        def get_eod_squareoff_log_for_date(self, date_iso: str):
            raise RuntimeError("simulated DB failure")

    failing_store = _FailingStore(real_store)
    bus = EventBus()
    mock_logger = MagicMock()
    ta = _FakeTimeAuthority()
    mw = _FakeMarketWindows()
    lf = _FakeLiveFeed()

    # Must not raise
    tracker = ShadowTracker(
        state_store=failing_store, bus=bus, live_feed=lf,
        market_windows=mw, time_authority=ta, logger=mock_logger,
    )

    assert tracker._eod_fired is False
    assert tracker._eod_fired_date is None
    assert mock_logger.error.called, "expected logger.error on degraded startup"
    print("  OK query failure degrades gracefully (no crash, logged, False state)")
    real_store.close()


# ─────────────────────────────────────────────────────────────────────────────
# B.5 / Audit 5.1 — is_tracking() public API
# ─────────────────────────────────────────────────────────────────────────────

def test_b5_is_tracking_true_when_simulated_inning_active(tmp_path: Path) -> None:
    """B.5: is_tracking(symbol) returns True after a TGT cascade kicks off
    inning 2 on that symbol."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    tracker = _make_tracker(store, bus, strategies=strategies)

    assert tracker.is_tracking("RELIANCE") is False, "no innings yet"

    _seed_signal(store, "sig_b5_tr")
    _seed_trade(store, "t_b5_tr", "sig_b5_tr", exit_reason="TGT_HIT", exit_price=2600.0)
    bus.publish(PositionClosed(
        source_module="test", trade_id="t_b5_tr", symbol="RELIANCE",
        signal_id="sig_b5_tr", exit_price=2600.0, realized_pnl=0.0,
    ))

    assert tracker.is_tracking("RELIANCE") is True, (
        "after cascade, is_tracking should report active simulated inning"
    )
    assert tracker.is_tracking("INFY") is False, (
        "is_tracking must be symbol-scoped"
    )
    print("  OK B.5 is_tracking True for active simulated symbol")
    store.close()


def test_b5_is_tracking_false_when_disabled(tmp_path: Path) -> None:
    """B.5: is_tracking() short-circuits to False when tracker is disabled."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    tracker = _make_tracker(store, bus, strategies=strategies, enabled=False)
    # Even if we manually shove an inning in (simulating leftover state),
    # is_tracking returns False because the module is disabled.
    tracker._active_innings["fake"] = type("_I", (), {"symbol": "RELIANCE"})()
    assert tracker.is_tracking("RELIANCE") is False
    print("  OK B.5 is_tracking False when disabled")
    store.close()


def test_bl13_idempotency_guard_works_without_startup_restore(tmp_path: Path) -> None:
    """In-process double-publish (no restart): guard still trips on second call."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    strategies = {"strategy1": _MockStrategy(direction="LONG")}
    cache = _MockInstrumentCache({738561: "RELIANCE"})
    tracker = _make_tracker(store, bus, strategies=strategies, instrument_cache=cache)
    assert tracker._eod_fired_date is None   # no startup restore

    _seed_signal(store, "sig_inproc")
    _seed_trade(store, "t_inproc", "sig_inproc", exit_reason="TGT_HIT", exit_price=2600.0)
    bus.publish(PositionClosed(
        source_module="test", trade_id="t_inproc", symbol="RELIANCE",
        signal_id="sig_inproc", exit_price=2600.0, realized_pnl=0.0,
    ))
    # First EOD closes inning 2 normally
    bus.publish(EodSquareoffComplete(source_module="test", fired_date="2026-04-16"))
    ex1 = store.get_innings_for_trade("t_inproc")[1]["exit_ts"]

    # Second EOD in-process: guard trips even though no restart occurred
    bus.publish(EodSquareoffComplete(source_module="test", fired_date="2026-04-16"))
    ex2 = store.get_innings_for_trade("t_inproc")[1]["exit_ts"]
    assert ex1 == ex2, "exit_ts should not change on in-process duplicate EOD"
    print("  OK in-process double-publish is idempotent (no restart required)")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────
# DATETIME NAIVE/AWARE CONSISTENCY — 11-May-2026 fix
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_ts_empty_string_returns_naive(tmp_path=None) -> None:
    """_parse_ts('') must return a NAIVE datetime (contract consistency)."""
    from orders.shadow_tracker import _parse_ts
    result = _parse_ts("")
    assert result.tzinfo is None, f"Expected naive, got tzinfo={result.tzinfo}"
    print("  OK _parse_ts('') returns naive datetime")


def test_parse_ts_aware_input_returns_naive(tmp_path=None) -> None:
    """_parse_ts with tz-aware ISO string must strip tzinfo."""
    from orders.shadow_tracker import _parse_ts
    result = _parse_ts("2026-04-16T10:00:00+05:30")
    assert result.tzinfo is None, f"Expected naive, got tzinfo={result.tzinfo}"
    print("  OK _parse_ts(aware) returns naive datetime")


def test_position_closed_null_exit_time_no_crash(tmp_path: Path) -> None:
    """PositionClosed on a trade with exit_time=NULL must not crash
    due to naive/aware datetime subtraction (production bug 11-May-2026)."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    aware_ta = _FakeTimeAuthority(
        datetime(2026, 4, 16, 10, 30, 0, tzinfo=_IST)
    )
    mw = _FakeMarketWindows(is_open=True)
    lf = _FakeLiveFeed()
    tracker = ShadowTracker(
        state_store=store, bus=bus, live_feed=lf,
        market_windows=mw, time_authority=aware_ta,
        max_innings=1, alert_per_inning=False, enabled=True,
    )

    _seed_signal(store, "sig_dt")
    _seed_trade(store, "t_dt", "sig_dt", exit_reason="SL_HIT",
                exit_time=None)

    bus.publish(PositionClosed(
        source_module="test", trade_id="t_dt", symbol="RELIANCE",
        signal_id="sig_dt", exit_price=2475.0, realized_pnl=-250.0,
    ))
    rows = store.fetch_all(
        "SELECT * FROM innings WHERE trade_id = ?", ("t_dt",)
    )
    assert len(rows) == 1, f"Expected 1 inning, got {len(rows)}"
    assert rows[0]["duration_sec"] >= 0
    print("  OK PositionClosed with NULL exit_time does not crash (aware TA)")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests_no_arg = [
        test_hit_long_sl,
        test_hit_long_tgt,
        test_hit_long_between_no_hit,
        test_hit_short_sl,
        test_hit_short_tgt,
        test_hit_short_between_no_hit,
        test_pnl_long_profit,
        test_pnl_long_loss,
        test_pnl_short_profit,
        test_pnl_short_loss,
        test_parse_ts_empty_string_returns_naive,
        test_parse_ts_aware_input_returns_naive,
    ]
    tests_with_path = [
        test_position_closed_creates_inning_1,
        test_position_closed_trade_not_found_logs_error,
        test_position_closed_disabled_no_op,
        test_cascade_tgt_starts_inning_2,
        test_cascade_sl_starts_inning_2,
        test_cascade_inning_3_started_after_inning_2_sl,
        test_cascade_stops_at_max_innings,
        test_cascade_eod_exit_no_inning_2,
        test_cascade_market_closed_no_inning_2,
        test_inning2_fixed_pct_long,
        test_inning2_fixed_pct_short,
        test_inning2_risk_reward_tgt,
        test_insert_inning_stores_all_fields,
        test_update_inning_close_populates_fields,
        test_get_innings_for_trade_order,
        test_get_innings_for_date_filters,
        test_eod_closes_active_innings,
        test_eod_no_new_innings_after_fire,
        test_eod_disabled_no_op,
        test_fix023_eod_closes_three_active_innings,
        test_alerts_on_inning_close,
        test_no_alerts_when_disabled,
        test_alert_contains_symbol_and_pnl,
        test_on_tick_resolves_symbol_and_checks_innings,
        test_on_tick_unknown_token_no_crash,
        test_on_tick_no_cache_no_crash,
        test_is_real_flags,
        test_concurrent_position_closed_and_tick,
        test_concurrent_ticks_two_threads,
        test_bl13_eod_fires_once_on_first_event,
        test_bl13_second_event_same_day_is_noop,
        test_bl13_new_day_event_processes_normally,
        test_bl13_mark_persists_across_restart,
        test_bl13_post_eod_restart_blocks_cascade,
        test_bl13_no_log_row_startup_leaves_eod_fired_false,
        test_bl13_guard_uses_ist_date,
        test_bl13_log_row_query_failure_degrades_gracefully,
        test_bl13_idempotency_guard_works_without_startup_restore,
        # B.5 / Audit 5.1 — is_tracking() public API
        test_b5_is_tracking_true_when_simulated_inning_active,
        test_b5_is_tracking_false_when_disabled,
        # 11-May-2026 datetime fix
        test_position_closed_null_exit_time_no_crash,
    ]

    passed = failed = 0

    for fn in tests_no_arg:
        try:
            fn(None)
            passed += 1
        except Exception:
            print(f"  FAIL {fn.__name__}")
            traceback.print_exc()
            failed += 1

    for fn in tests_with_path:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            try:
                fn(Path(tmp))
                passed += 1
            except Exception:
                print(f"  FAIL {fn.__name__}")
                traceback.print_exc()
                failed += 1

    print(f"\n{passed}/{passed+failed} passed")
    if failed:
        sys.exit(1)
