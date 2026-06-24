"""Slice 1 (22-Jun-2026): fill-time R:R fix + SL/TGT placement after-check.

Part A — the broker TGT must use the originating strategy's tgt_risk_reward
(frozen at placement), NOT order_placer's hardcoded default.
Part B — after the deferred SL/TGT are placed, verify they landed at the
intended price + qty; alert on mismatch (missing/wrong SL = CRITICAL,
TGT-only = WARN); record the verdict on the trade (read-back columns).
Schema  — v34 -> v35 migration adds the three new trades columns.

Reuses the real OrderPlacer/engine/protocol stack + the lightweight mocks
from test_order_placer.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

import pytest

from broker.cost_calculator import CostCalculator
from broker.order_monitor import OrderMonitor
from core.events import EventBus
from core.state_store import EXPECTED_SCHEMA_VERSION, StateStore
from orders.full_entry_engine import FullEntryEngine
from orders.order_manager import OrderManager
from orders.order_placer import OrderPlacer, _FillEntry, _LEG_ENTRY
from orders.order_protocol_co import CoPlusTgtProtocol
from orders.order_protocol_limit import LimitTripleProtocol

# Reuse the proven harness mocks/helpers.
from tests.unit.test_order_placer import (
    _MockAdapter,
    _MockFundManager,
    _make_store,
    _seed_signal,
    _default_resolver,
    _log,
)


class _FakeNotifier:
    """Captures TelegramNotifier.send() calls (severity is what we assert on)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, *, severity, title, body, source_module, context=None) -> None:
        self.sent.append({"severity": severity, "title": title, "body": body})

    def severities(self) -> list[str]:
        return [s["severity"] for s in self.sent]


def _make_placer(tmp_path, *, rr_ratio=2.0, default_protocol="LIMIT_TRIPLE",
                 notifier=None, mode="LIVE"):
    store = _make_store(tmp_path)
    adapter = _MockAdapter()
    engine = FullEntryEngine(
        co_protocol=CoPlusTgtProtocol(adapter=adapter, logger=_log()),
        limit_protocol=LimitTripleProtocol(adapter=adapter, logger=_log()),
        logger=_log(), default_protocol=default_protocol,
    )
    om = OrderManager(store, _log())
    placer = OrderPlacer(
        entry_engine=engine,
        order_manager=om,
        fund_manager=_MockFundManager(),
        bus=EventBus(),
        logger=_log(),
        order_monitor=MagicMock(spec=OrderMonitor),
        cost_calculator=MagicMock(spec=CostCalculator),
        rr_ratio=rr_ratio,
        default_order_protocol=default_protocol,
        product_resolver=_default_resolver(),
        notifier=notifier,
        mode=mode,
    )
    return placer, store, om, adapter


def _place_then_fill(tmp_path, *, protocol="LIMIT_TRIPLE", side="BUY",
                     entry=100.0, sl=99.0, avg_fill=100.0, rr=1.5,
                     rr_default=2.0, qty=10, notifier=None, mode="LIVE"):
    """place() an entry (freezing rr) then drive the deferred-exit placement at
    the given fill price. Returns (placer, store, om, adapter, trade_id)."""
    placer, store, om, adapter = _make_placer(
        tmp_path, rr_ratio=rr_default, default_protocol=protocol,
        notifier=notifier, mode=mode,
    )
    sig_id = _seed_signal(store)
    placer.place(
        symbol="SYM", side=side, qty=qty, entry_price=entry, sl_price=sl,
        intent="INTRADAY", signal_id=sig_id, reservation_id="res1",
        tgt_risk_reward=rr,
    )
    trade_id = store.fetch_one(
        "SELECT trade_id FROM trades WHERE signal_id = ?", (sig_id,)
    )["trade_id"]
    direction = "LONG" if side == "BUY" else "SHORT"
    fe = _FillEntry(
        trade_id=trade_id, reservation_id="res1", symbol="SYM", qty=qty,
        leg=_LEG_ENTRY, order_protocol=protocol, direction=direction,
        side=side, sl_price=sl, tgt_price=entry, intent="INTRADAY",
        tgt_risk_reward=(rr if rr else 0.0),
    )
    adapter.placed.clear()  # drop the ENTRY placement; keep only the exits
    if protocol == "LIMIT_TRIPLE":
        placer._place_limit_triple_exits(
            trade_id=trade_id, fill_entry=fe, qty_filled=qty,
            avg_fill_price=avg_fill, reason="test",
        )
    else:
        placer._place_co_tgt_exit(
            trade_id=trade_id, fill_entry=fe, qty_filled=qty,
            avg_fill_price=avg_fill, reason="test",
        )
    return placer, store, om, adapter, trade_id


def _tgt_price(adapter) -> float:
    tgts = [o for o in adapter.placed if o["order_type"] == "LIMIT"]
    assert tgts, f"no TGT (LIMIT) order placed; placed={adapter.placed}"
    return tgts[0]["price"]


# ── Part A: R:R fill-time fix ─────────────────────────────────────────────


class TestFillTimeRR:
    def test_fill_time_uses_strategy_rr_not_hardcoded_2(self):
        """gap_go-style R:R 2.5 -> broker TGT at 2.5x risk, not the default 2.0."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, store, _, adapter, _ = _place_then_fill(
                Path(tmp), rr=2.5, rr_default=2.0, entry=100.0, sl=99.0, avg_fill=100.0,
            )
            # 100 + |100-99| * 2.5 = 102.50  (NOT 102.00)
            assert _tgt_price(adapter) == pytest.approx(102.5)
            store.close()

    def test_fill_time_rr_15_gap_fade(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, store, _, adapter, _ = _place_then_fill(
                Path(tmp), rr=1.5, rr_default=2.0, entry=100.0, sl=99.0, avg_fill=100.0,
            )
            assert _tgt_price(adapter) == pytest.approx(101.5)  # not 102.0
            store.close()

    def test_fill_time_rr_with_slippage_uses_real_risk(self):
        """Fill above signal -> risk includes slippage; R:R applies to real risk."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, store, _, adapter, _ = _place_then_fill(
                Path(tmp), rr=1.5, entry=100.0, sl=99.0, avg_fill=100.20,
            )
            # risk = |100.20 - 99| = 1.20 ; tgt = 100.20 + 1.20*1.5 = 102.00
            assert _tgt_price(adapter) == pytest.approx(102.0)
            store.close()

    def test_missing_stored_rr_falls_back_to_default(self):
        """A trade with no frozen R:R -> fill recalc uses the 2.0 default."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, store, _, adapter, _ = _place_then_fill(
                Path(tmp), rr=0.0, rr_default=2.0, entry=100.0, sl=99.0, avg_fill=100.0,
            )
            assert _tgt_price(adapter) == pytest.approx(102.0)  # 2.0 fallback
            store.close()

    def test_resolve_fill_rr_warns_on_fallback(self):
        """_resolve_fill_rr returns the strategy R:R when present (no warning),
        and falls back to self._rr_ratio + a WARNING when absent. Uses a mock
        logger so the assertion is isolation-proof (no caplog propagation)."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            placer, store, _, _ = _make_placer(Path(tmp), rr_ratio=2.0)
            placer._log = MagicMock()
            assert placer._resolve_fill_rr(1.5, "t1", "SYM") == pytest.approx(1.5)
            placer._log.warning.assert_not_called()
            assert placer._resolve_fill_rr(0.0, "t1", "SYM") == pytest.approx(2.0)
            assert placer._log.warning.called
            assert "rr_fallback_to_default" in placer._log.warning.call_args[0][0]
            store.close()

    def test_stored_rr_matches_placement_rr(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            placer, store, om, adapter = _make_placer(Path(tmp))
            sig_id = _seed_signal(store)
            placer.place(
                symbol="SYM", side="BUY", qty=10, entry_price=100.0, sl_price=99.0,
                intent="INTRADAY", signal_id=sig_id, reservation_id="res1",
                tgt_risk_reward=1.5,
            )
            row = store.fetch_one(
                "SELECT tgt_risk_reward_applied FROM trades WHERE signal_id = ?",
                (sig_id,),
            )
            assert row["tgt_risk_reward_applied"] == pytest.approx(1.5)
            store.close()

    def test_co_tgt_honors_strategy_rr(self):
        """Third recalc site path: CO_PLUS_TGT also honours the strategy R:R."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, store, _, adapter, _ = _place_then_fill(
                Path(tmp), protocol="CO_PLUS_TGT", rr=1.5, rr_default=2.0,
                entry=100.0, sl=99.0, avg_fill=100.0,
            )
            assert _tgt_price(adapter) == pytest.approx(101.5)  # not 102.0
            store.close()

    def test_paper_and_live_same_rr_path(self):
        """Parity: the fill-time recalc has no mode branch -> identical TGT."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp1:
            _, s1, _, a1, _ = _place_then_fill(Path(tmp1), rr=1.5, mode="LIVE")
            live_tgt = _tgt_price(a1); s1.close()
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp2:
            _, s2, _, a2, _ = _place_then_fill(Path(tmp2), rr=1.5, mode="PAPER")
            paper_tgt = _tgt_price(a2); s2.close()
        assert live_tgt == pytest.approx(paper_tgt) == pytest.approx(101.5)


# ── Part B: SL/TGT placement after-check ──────────────────────────────────


def _seed_exit(om, trade_id, leg, *, price, qty, trigger=0.0):
    om.insert_order(
        trade_id=trade_id, broker_order_id=f"B_{leg}_{trade_id[:6]}", leg=leg,
        transaction_type="SELL", order_type=("SL" if leg == "SL" else "LIMIT"),
        product="MIS", variety="regular", qty_requested=qty, price=price,
        trigger_price=trigger,
    )


def _make_trade(placer, store, *, qty=10):
    sig_id = _seed_signal(store)
    placer.place(
        symbol="SYM", side="BUY", qty=qty, entry_price=100.0, sl_price=99.0,
        intent="INTRADAY", signal_id=sig_id, reservation_id="res1",
        tgt_risk_reward=1.5,
    )
    return store.fetch_one(
        "SELECT trade_id FROM trades WHERE signal_id = ?", (sig_id,)
    )["trade_id"]


class TestExitsAfterCheck:
    def test_exits_verified_ok_when_prices_match(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, store, _, _, trade_id = _place_then_fill(
                Path(tmp), rr=1.5, entry=100.0, sl=99.0, avg_fill=100.0,
            )
            row = store.fetch_one(
                "SELECT exits_verified, exits_verify_detail FROM trades WHERE trade_id = ?",
                (trade_id,),
            )
            assert row["exits_verified"] == 1
            assert row["exits_verify_detail"] == "ok"
            store.close()

    def test_missing_sl_raises_critical(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = _FakeNotifier()
            placer, store, om, _ = _make_placer(Path(tmp), notifier=notifier)
            trade_id = _make_trade(placer, store)
            _seed_exit(om, trade_id, "TGT", price=101.5, qty=10)  # TGT only, no SL
            placer._verify_exits_placed(
                trade_id=trade_id, symbol="SYM", protocol="LIMIT_TRIPLE",
                intended_sl=99.0, intended_tgt=101.5, qty_filled=10,
            )
            row = store.fetch_one(
                "SELECT exits_verified, exits_verify_detail FROM trades WHERE trade_id = ?",
                (trade_id,),
            )
            assert row["exits_verified"] == 0
            assert "SL MISSING" in row["exits_verify_detail"]
            assert "CRITICAL" in notifier.severities()
            store.close()

    def test_wrong_sl_price_raises_critical(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = _FakeNotifier()
            placer, store, om, _ = _make_placer(Path(tmp), notifier=notifier)
            trade_id = _make_trade(placer, store)
            _seed_exit(om, trade_id, "SL", price=90.0, qty=10, trigger=90.0)  # intended 99
            _seed_exit(om, trade_id, "TGT", price=101.5, qty=10)
            placer._verify_exits_placed(
                trade_id=trade_id, symbol="SYM", protocol="LIMIT_TRIPLE",
                intended_sl=99.0, intended_tgt=101.5, qty_filled=10,
            )
            row = store.fetch_one(
                "SELECT exits_verified, exits_verify_detail FROM trades WHERE trade_id = ?",
                (trade_id,),
            )
            assert row["exits_verified"] == 0
            assert "SL trigger" in row["exits_verify_detail"]
            assert "CRITICAL" in notifier.severities()
            store.close()

    def test_missing_tgt_warn_not_critical(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = _FakeNotifier()
            placer, store, om, _ = _make_placer(Path(tmp), notifier=notifier)
            trade_id = _make_trade(placer, store)
            _seed_exit(om, trade_id, "SL", price=99.0, qty=10, trigger=99.0)  # SL ok, no TGT
            placer._verify_exits_placed(
                trade_id=trade_id, symbol="SYM", protocol="LIMIT_TRIPLE",
                intended_sl=99.0, intended_tgt=101.5, qty_filled=10,
            )
            row = store.fetch_one(
                "SELECT exits_verified, exits_verify_detail FROM trades WHERE trade_id = ?",
                (trade_id,),
            )
            assert row["exits_verified"] == 0
            assert "TGT MISSING" in row["exits_verify_detail"]
            # TGT-only mismatch (SL intact) -> WARN, never CRITICAL.
            sevs = notifier.severities()
            assert "WARNING" in sevs and "CRITICAL" not in sevs
            store.close()

    def test_exit_qty_mismatch_flagged(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = _FakeNotifier()
            placer, store, om, _ = _make_placer(Path(tmp), notifier=notifier)
            trade_id = _make_trade(placer, store)
            # Both legs at the right price but sized to 5, while the fill was 10.
            _seed_exit(om, trade_id, "SL", price=99.0, qty=5, trigger=99.0)
            _seed_exit(om, trade_id, "TGT", price=101.5, qty=5)
            placer._verify_exits_placed(
                trade_id=trade_id, symbol="SYM", protocol="LIMIT_TRIPLE",
                intended_sl=99.0, intended_tgt=101.5, qty_filled=10,
            )
            row = store.fetch_one(
                "SELECT exits_verified, exits_verify_detail FROM trades WHERE trade_id = ?",
                (trade_id,),
            )
            assert row["exits_verified"] == 0
            assert "qty" in row["exits_verify_detail"]
            store.close()

    def test_after_check_runs_in_paper_mode(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, store, _, _, trade_id = _place_then_fill(
                Path(tmp), rr=1.5, mode="PAPER", entry=100.0, sl=99.0, avg_fill=100.0,
            )
            row = store.fetch_one(
                "SELECT exits_verified FROM trades WHERE trade_id = ?", (trade_id,)
            )
            assert row["exits_verified"] == 1  # after-check ran + passed in paper
            store.close()

    def test_co_after_check_skips_sl(self):
        """CO SL is broker-managed (bracket) -> after-check verifies TGT only."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, store, _, _, trade_id = _place_then_fill(
                Path(tmp), protocol="CO_PLUS_TGT", rr=1.5,
                entry=100.0, sl=99.0, avg_fill=100.0,
            )
            row = store.fetch_one(
                "SELECT exits_verified, exits_verify_detail FROM trades WHERE trade_id = ?",
                (trade_id,),
            )
            assert row["exits_verified"] == 1
            assert row["exits_verify_detail"] == "ok"
            store.close()

    # ── Circuit-clamp awareness (24-Jun): a legit band clamp is not a mismatch ──

    def _verdict(self, store, trade_id):
        return store.fetch_one(
            "SELECT exits_verified, exits_verify_detail FROM trades WHERE trade_id = ?",
            (trade_id,),
        )

    def test_clamp_explained_tgt_verified_ok(self):
        """TGT placed at the circuit band it was clamped to (differs from the
        pre-clamp intended) -> VERIFIED, recorded as a clamp note, NO alert."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = _FakeNotifier()
            placer, store, om, _ = _make_placer(Path(tmp), notifier=notifier)
            trade_id = _make_trade(placer, store)
            _seed_exit(om, trade_id, "SL", price=99.0, qty=10, trigger=99.0)   # SL ok
            _seed_exit(om, trade_id, "TGT", price=216.15, qty=10)             # clamped
            placer._verify_exits_placed(
                trade_id=trade_id, symbol="SYM", protocol="LIMIT_TRIPLE",
                intended_sl=99.0, intended_tgt=220.41, qty_filled=10,
                tgt_clamp_price=216.15,   # the band ceiling place_exits clamped to
            )
            row = self._verdict(store, trade_id)
            assert row["exits_verified"] == 1
            assert "clamped to circuit band" in row["exits_verify_detail"]
            # The after-check raises NO mismatch alert for a legit clamp (the only
            # notification is the pre-existing INFO 'ORDER PLACED' from place()).
            sevs = notifier.severities()
            assert "WARNING" not in sevs and "CRITICAL" not in sevs
            store.close()

    def test_clamp_explained_sl_verified_ok(self):
        """An SL legitimately clamped UP off the lower circuit (placed == band)
        verifies OK — no false CRITICAL."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = _FakeNotifier()
            placer, store, om, _ = _make_placer(Path(tmp), notifier=notifier)
            trade_id = _make_trade(placer, store)
            _seed_exit(om, trade_id, "SL", price=95.0, qty=10, trigger=95.0)  # clamped up
            _seed_exit(om, trade_id, "TGT", price=101.5, qty=10)             # TGT ok
            placer._verify_exits_placed(
                trade_id=trade_id, symbol="SYM", protocol="LIMIT_TRIPLE",
                intended_sl=92.0, intended_tgt=101.5, qty_filled=10,
                sl_clamp_price=95.0,
            )
            row = self._verdict(store, trade_id)
            assert row["exits_verified"] == 1
            assert "SL clamped to circuit band" in row["exits_verify_detail"]
            assert "CRITICAL" not in notifier.severities()
            store.close()

    def test_clamp_flag_set_but_placed_not_band_still_flags(self):
        """DISCRIMINATOR: placed != intended AND a clamp occurred BUT placed !=
        the band ceiling -> NOT explained by the clamp -> still a real mismatch.
        Proves we did not blindly suppress on 'a clamp happened'."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = _FakeNotifier()
            placer, store, om, _ = _make_placer(Path(tmp), notifier=notifier)
            trade_id = _make_trade(placer, store)
            _seed_exit(om, trade_id, "SL", price=99.0, qty=10, trigger=99.0)  # SL ok
            _seed_exit(om, trade_id, "TGT", price=210.0, qty=10)             # != band
            placer._verify_exits_placed(
                trade_id=trade_id, symbol="SYM", protocol="LIMIT_TRIPLE",
                intended_sl=99.0, intended_tgt=220.41, qty_filled=10,
                tgt_clamp_price=216.15,   # band was 216.15 but placed is 210.0
            )
            row = self._verdict(store, trade_id)
            assert row["exits_verified"] == 0
            assert "TGT 210.00 != intended" in row["exits_verify_detail"]
            # SL intact -> TGT-only mismatch -> WARN, not CRITICAL.
            sevs = notifier.severities()
            assert "WARNING" in sevs and "CRITICAL" not in sevs
            store.close()

    def test_real_tgt_mismatch_without_clamp_still_warns(self):
        """No clamp at all (tgt_clamp_price=None) + placed != intended -> the
        original mismatch behaviour is unchanged."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = _FakeNotifier()
            placer, store, om, _ = _make_placer(Path(tmp), notifier=notifier)
            trade_id = _make_trade(placer, store)
            _seed_exit(om, trade_id, "SL", price=99.0, qty=10, trigger=99.0)
            _seed_exit(om, trade_id, "TGT", price=210.0, qty=10)
            placer._verify_exits_placed(
                trade_id=trade_id, symbol="SYM", protocol="LIMIT_TRIPLE",
                intended_sl=99.0, intended_tgt=220.41, qty_filled=10,
            )
            row = self._verdict(store, trade_id)
            assert row["exits_verified"] == 0
            assert "WARNING" in notifier.severities()
            store.close()

    def test_missing_sl_still_critical_even_when_tgt_clamped(self):
        """Clamp-awareness must NOT blind the SL protection check: a missing SL is
        still CRITICAL even when the TGT was legitimately clamped."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = _FakeNotifier()
            placer, store, om, _ = _make_placer(Path(tmp), notifier=notifier)
            trade_id = _make_trade(placer, store)
            _seed_exit(om, trade_id, "TGT", price=216.15, qty=10)   # clamped TGT, NO SL
            placer._verify_exits_placed(
                trade_id=trade_id, symbol="SYM", protocol="LIMIT_TRIPLE",
                intended_sl=99.0, intended_tgt=220.41, qty_filled=10,
                tgt_clamp_price=216.15,
            )
            row = self._verdict(store, trade_id)
            assert row["exits_verified"] == 0
            assert "SL MISSING" in row["exits_verify_detail"]
            assert "CRITICAL" in notifier.severities()
            store.close()

    def test_pacedigitk_shaped_flips_0_to_1(self):
        """Re-classify the PACEDIGITK (Tue 23-Jun) false positive: SL ok, TGT
        clamped to the circuit band (216.15) below the pre-clamp intended (220.41).
        Pre-fix this recorded exits_verified=0; clamp-aware it is VERIFIED."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = _FakeNotifier()
            placer, store, om, _ = _make_placer(Path(tmp), notifier=notifier)
            trade_id = _make_trade(placer, store)
            _seed_exit(om, trade_id, "SL", price=207.96, qty=10, trigger=207.96)
            _seed_exit(om, trade_id, "TGT", price=216.15, qty=10)
            placer._verify_exits_placed(
                trade_id=trade_id, symbol="PACEDIGITK", protocol="LIMIT_TRIPLE",
                intended_sl=207.96, intended_tgt=220.41, qty_filled=10,
                tgt_clamp_price=216.15,
            )
            row = self._verdict(store, trade_id)
            assert row["exits_verified"] == 1          # was 0 pre-fix
            assert "clamped to circuit band 216.15" in row["exits_verify_detail"]
            sevs = notifier.severities()
            assert "WARNING" not in sevs and "CRITICAL" not in sevs
            store.close()

    def test_clamp_aware_logic_parity_paper_and_live(self):
        """Parity: the clamp-aware after-check has no mode branch -> a clamped TGT
        verifies identically in PAPER and LIVE."""
        details = {}
        for mode in ("PAPER", "LIVE"):
            with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                placer, store, om, _ = _make_placer(
                    Path(tmp), notifier=_FakeNotifier(), mode=mode)
                trade_id = _make_trade(placer, store)
                _seed_exit(om, trade_id, "SL", price=99.0, qty=10, trigger=99.0)
                _seed_exit(om, trade_id, "TGT", price=216.15, qty=10)
                placer._verify_exits_placed(
                    trade_id=trade_id, symbol="SYM", protocol="LIMIT_TRIPLE",
                    intended_sl=99.0, intended_tgt=220.41, qty_filled=10,
                    tgt_clamp_price=216.15,
                )
                details[mode] = self._verdict(store, trade_id)["exits_verify_detail"]
                store.close()
        assert details["PAPER"] == details["LIVE"]
        assert "clamped to circuit band" in details["LIVE"]


# ── End-to-end: place_exits clamp -> ExitLegsResult flag -> after-check OK ──


class _CircuitQuote:
    def __init__(self, upper, lower):
        self.upper_circuit = upper
        self.lower_circuit = lower


class _ClampAdapter(_MockAdapter):
    """_MockAdapter + circuit bands, so place_exits actually clamps a leg."""

    def __init__(self, *, upper, lower, **kw):
        super().__init__(**kw)
        self._upper, self._lower = upper, lower

    def get_quote(self, symbols):
        return {s: _CircuitQuote(self._upper, self._lower) for s in symbols}


def _make_clamp_placer(tmp_path, *, upper, lower, notifier=None, mode="LIVE"):
    store = _make_store(tmp_path)
    adapter = _ClampAdapter(upper=upper, lower=lower)
    engine = FullEntryEngine(
        co_protocol=CoPlusTgtProtocol(adapter=adapter, logger=_log()),
        limit_protocol=LimitTripleProtocol(adapter=adapter, logger=_log()),
        logger=_log(), default_protocol="LIMIT_TRIPLE",
    )
    om = OrderManager(store, _log())
    placer = OrderPlacer(
        entry_engine=engine, order_manager=om, fund_manager=_MockFundManager(),
        bus=EventBus(), logger=_log(), order_monitor=MagicMock(spec=OrderMonitor),
        cost_calculator=MagicMock(spec=CostCalculator), rr_ratio=2.0,
        default_order_protocol="LIMIT_TRIPLE", product_resolver=_default_resolver(),
        notifier=notifier, mode=mode,
    )
    return placer, store, om, adapter


def test_clamped_tgt_end_to_end_verifies_ok():
    """Full chain: place_exits clamps the TGT into the band -> ExitLegsResult
    .tgt_clamped=True -> the after-check recognises it -> verified=1, no alert.
    entry 100 / SL 99 / RR 2.5 -> intended TGT 102.5; upper 103 -> band ceiling
    100.90 (clamps; still > the 100 fill so it's placeable)."""
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        notifier = _FakeNotifier()
        placer, store, om, adapter = _make_clamp_placer(
            Path(tmp), upper=103.0, lower=90.0, notifier=notifier)
        sig_id = _seed_signal(store)
        placer.place(symbol="SYM", side="BUY", qty=10, entry_price=100.0,
                     sl_price=99.0, intent="INTRADAY", signal_id=sig_id,
                     reservation_id="res1", tgt_risk_reward=2.5)
        trade_id = store.fetch_one(
            "SELECT trade_id FROM trades WHERE signal_id = ?", (sig_id,))["trade_id"]
        fe = _FillEntry(
            trade_id=trade_id, reservation_id="res1", symbol="SYM", qty=10,
            leg=_LEG_ENTRY, order_protocol="LIMIT_TRIPLE", direction="LONG",
            side="BUY", sl_price=99.0, tgt_price=100.0, intent="INTRADAY",
            tgt_risk_reward=2.5,
        )
        adapter.placed.clear()
        placer._place_limit_triple_exits(
            trade_id=trade_id, fill_entry=fe, qty_filled=10,
            avg_fill_price=100.0, reason="test",
        )
        row = store.fetch_one(
            "SELECT exits_verified, exits_verify_detail FROM trades WHERE trade_id = ?",
            (trade_id,))
        assert row["exits_verified"] == 1
        assert "clamped to circuit band" in row["exits_verify_detail"]
        assert "CRITICAL" not in notifier.severities()
        store.close()


# ── Schema v34 -> v35 migration ───────────────────────────────────────────

_V35_COLS = ["tgt_risk_reward_applied", "exits_verified", "exits_verify_detail"]


def test_v34_to_v35_columns_added(tmp_path):
    """A v34 trades (no Slice-1 cols) gains the three columns on migration to
    v35; the existing row is preserved (new cols read NULL)."""
    db = tmp_path / "mig3435.db"

    store = StateStore(db)
    assert store.get_schema_version() == EXPECTED_SCHEMA_VERSION  # 35
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals(signal_id,symbol,scanner,strategy,triggered_at,"
            "received_at,expires_at,status,fingerprint,fingerprint_date) "
            "VALUES('sig1','X','sc','st','t','t','t','TRADED','fp','2026-06-22')"
        )
        cur.execute(
            "INSERT INTO trades(trade_id,signal_id,symbol,direction,strategy,"
            "qty_planned,entry_target_price,sl_initial,tgt_initial,margin_reserved,"
            "risk_amount,created_at,updated_at,status,order_protocol,"
            "tgt_risk_reward_applied) "
            "VALUES('t1','sig1','X','LONG','st',5,100,95,110,20,5,'t','t',"
            "'PENDING_FILL','CO_PLUS_TGT',1.5)"
        )
    store.close()

    # Simulate a real v34 DB: drop the 3 new columns and rewind the version.
    conn = sqlite3.connect(str(db))
    for col in _V35_COLS:
        conn.execute(f"ALTER TABLE trades DROP COLUMN {col}")
    conn.execute("UPDATE schema_meta SET value='34' WHERE key='schema_version'")
    conn.commit()
    assert "exits_verified" not in {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    conn.close()

    # Reopen -> run_migrations rebuilds trades to v35.
    store2 = StateStore(db)
    assert store2.get_schema_version() == EXPECTED_SCHEMA_VERSION  # 35
    cols = {r["name"] for r in store2.fetch_all("PRAGMA table_info(trades)")}
    assert set(_V35_COLS) <= cols
    assert store2.fetch_one("SELECT COUNT(*) AS n FROM trades")["n"] == 1
    # Rebuild copies the intersecting old columns; the new ones take NULL.
    assert store2.fetch_one(
        "SELECT exits_verified FROM trades WHERE trade_id='t1'")["exits_verified"] is None
    store2.close()
