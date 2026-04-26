"""
tests/integration/test_end_to_end_smoke.py -- Trading System v2

Module 37: End-to-end smoke tests (IT1-IT12).

7 paper-mode scenarios exercising the full signal pipeline from
webhook POST → signal_processor → screener → order_placer → state_store.

All tests use the `wired_system` fixture from conftest.py:
  - paper_mode=True (no real Zerodha calls)
  - now_ist() patched to MOCK_NOW (2026-04-15 10:30 IST, Wednesday)
  - SQLite in tmp_path (isolated per test)

Signal flow for scenarios 1-5:
  POST /webhook/vwap_bounce_long
    → WebhookReceiver validates + inserts signal + enqueues
    → SignalProcessor picks up + runs pipeline
    → state_store.signals shows final status

Locked decisions: IT1-IT12.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional
from unittest.mock import patch

import pytest

from core.events import OrderFilled, PositionClosed

from tests.integration.conftest import (
    MOCK_TRIGGERED_AT,
    SCANNER_NAME,
    SystemContext,
)

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _make_payload(
    scanner_name: str = SCANNER_NAME,
    symbol: str = "RELIANCE",
    price: str = "100.0",
    triggered_at: str = MOCK_TRIGGERED_AT,
) -> bytes:
    """Build Chartink-compatible webhook JSON payload."""
    body = {
        "stocks": symbol,
        "trigger_prices": price,
        "triggered_at": triggered_at,
        "scan_name": scanner_name,
    }
    return json.dumps(body).encode("utf-8")


def _post_webhook(ctx: SystemContext, scanner_name: str, payload: bytes):
    """POST a webhook using Flask test client. Returns (status_code, json_data)."""
    with ctx.receiver.app.test_client() as client:
        resp = client.post(
            f"/webhook/{scanner_name}",
            data=payload,
            content_type="application/json",
        )
    return resp.status_code, resp.get_json(silent=True)


def _wait_for_signal_status(
    ctx: SystemContext,
    signal_id: str,
    target_statuses: set,
    timeout: float = 5.0,
) -> Optional[str]:
    """Poll state_store until signal reaches one of target_statuses or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = ctx.store.fetch_all(
            "SELECT status FROM signals WHERE signal_id = ?", (signal_id,)
        )
        if rows:
            status = rows[0]["status"]
            if status in target_statuses:
                return status
        time.sleep(0.05)
    # Return whatever status we got
    rows = ctx.store.fetch_all(
        "SELECT status FROM signals WHERE signal_id = ?", (signal_id,)
    )
    return rows[0]["status"] if rows else None


def _wait_for_any_signal(ctx: SystemContext, symbol: str, timeout: float = 3.0) -> Optional[str]:
    """Wait for any signal for symbol to appear in state_store; return signal_id."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = ctx.store.fetch_all(
            "SELECT signal_id FROM signals WHERE symbol = ? ORDER BY received_at DESC LIMIT 1",
            (symbol,),
        )
        if rows:
            return rows[0]["signal_id"]
        time.sleep(0.05)
    return None


# ---------------------------------------------------------------------------
# Scenario 1: Happy path — signal received, screener passes, order placed
# ---------------------------------------------------------------------------

class TestScenario1HappyPath:
    """
    IT4, IT5: Full pipeline from webhook POST to order placement.

    RELIANCE signal → vwap_bounce_long strategy → screener passes (rich market
    data patched via set_rich_quote) → position sized → risk approved →
    capital reserved → paper order SUBMITTED.

    The _build_market_data patch must remain active while the async
    signal_processor processes the signal, so both the POST and the wait
    happen inside the same patch.object context.
    """

    def test_happy_path_signal_reaches_placed_status(self, wired_system):
        ctx = wired_system
        symbol = "RELIANCE"

        # Rich market data → secondary screener score ~90 (HIGH tier)
        ctx.sim_kite.set_rich_quote(symbol, ltp=100.0)
        rich_md = ctx.sim_kite.get_market_data(symbol)

        # Keep patch active for the duration of async processing
        with patch.object(ctx.screener, "_build_market_data", return_value=rich_md):
            code, data = _post_webhook(ctx, SCANNER_NAME, _make_payload(symbol=symbol))
            assert code == 200, f"Webhook rejected: {data}"
            assert data["accepted"] == 1

            signal_id = _wait_for_any_signal(ctx, symbol, timeout=3.0)
            assert signal_id is not None, "Signal never inserted to state_store"

            # All terminal statuses (any pipeline exit)
            _terminal = {
                "PROCESSED", "PLACEMENT_FAILED",
                "RESERVED",  # placer may timeout in test
                "PROCESSED_NO_PLACER",
                *{f"REJECTED_{x}" for x in [
                    "SCREEN", "RISK", "SCORE", "KILL_SWITCH",
                    "OUTSIDE_ENTRY_WINDOW", "EXPIRED", "UNKNOWN_STRATEGY",
                    "MAX_OPEN_POSITIONS", "STEP_ERROR", "SIGNAL_AGE",
                ]},
                *{f"REJECTED_SCORE_{i}" for i in range(0, 100)},
            }

            terminal = _wait_for_signal_status(ctx, signal_id, _terminal, timeout=6.0)

        assert terminal == "PROCESSED", (
            f"Expected PROCESSED but got {terminal!r} for {signal_id}"
        )

    def test_happy_path_creates_trade_row(self, wired_system):
        ctx = wired_system
        symbol = "RELIANCE"

        ctx.sim_kite.set_rich_quote(symbol, ltp=100.0)
        rich_md = ctx.sim_kite.get_market_data(symbol)

        with patch.object(ctx.screener, "_build_market_data", return_value=rich_md):
            _post_webhook(ctx, SCANNER_NAME, _make_payload(symbol=symbol))
            signal_id = _wait_for_any_signal(ctx, symbol, timeout=3.0)
            _wait_for_signal_status(ctx, signal_id, {"PROCESSED"}, timeout=6.0)

        trades = ctx.store.fetch_all(
            "SELECT * FROM trades WHERE symbol = ?", (symbol,)
        )
        assert len(trades) >= 1, "Expected at least one trade row after happy path"


# ---------------------------------------------------------------------------
# Scenario 2: Screener rejection — signal rejected by secondary screener
# ---------------------------------------------------------------------------

class TestScenario2ScreenerRejection:
    """
    IT6: Secondary screener rejects the signal.

    No rich market data → _build_market_data returns avg_volume_20d=None, vwap=None.
    step_2 (vwap_position) raises TypeError (None comparison) → REJECTED_STEP_ERROR.
    """

    def test_screener_rejection_status(self, wired_system):
        ctx = wired_system
        symbol = "WIPRO"

        # No sim_kite quote configured → default quote → _build_market_data returns
        # avg_volume_20d=None, vwap=None → step errors → REJECTED_STEP_ERROR
        code, data = _post_webhook(ctx, SCANNER_NAME, _make_payload(symbol=symbol))
        assert code == 200
        assert data["accepted"] == 1

        signal_id = _wait_for_any_signal(ctx, symbol, timeout=3.0)
        assert signal_id is not None

        # Any rejection is acceptable; step_error fires before score check
        _reject_statuses = {
            "REJECTED_STEP_ERROR",
            "REJECTED_SIGNAL_AGE",
            "SKIPPED_QUOTE_UNAVAILABLE",
            *{f"REJECTED_SCORE_{i}" for i in range(0, 60)},
        }
        terminal = _wait_for_signal_status(ctx, signal_id, _reject_statuses, timeout=5.0)

        assert terminal is not None, "Signal never reached rejected status"
        assert "REJECTED" in (terminal or "") or "SKIPPED" in (terminal or ""), (
            f"Expected rejection but got {terminal!r}"
        )


# ---------------------------------------------------------------------------
# Scenario 3: Risk rejection — max_open_positions already at cap
# ---------------------------------------------------------------------------

class TestScenario3RiskRejection:
    """
    IT7: RiskEngine rejects signal when open-position cap is already hit.

    Seed 2 OPEN trades directly in DB (matching risk_engine's max_open_positions=2
    set in conftest), then submit a signal with rich market data.
    """

    def _seed_open_trades(self, ctx: SystemContext, count: int) -> None:
        """Insert OPEN trade rows to saturate the position cap (committed)."""
        for i in range(count):
            signal_id = f"sig_seed_{i:04d}"
            trade_id = f"trd_seed_{i:04d}"
            with ctx.store.transaction() as cur:
                # Insert parent signal row first (FK: trades.signal_id → signals.signal_id)
                cur.execute(
                    """INSERT OR IGNORE INTO signals
                       (signal_id, symbol, scanner, strategy,
                        triggered_at, received_at, expires_at,
                        status, fingerprint, fingerprint_date)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (signal_id, f"SEED{i}", "vwap_bounce_long", "vwap_bounce_long",
                     "2026-04-15 09:45:00", "2026-04-15 09:45:00", "2026-04-15 09:46:00",
                     "PROCESSED", f"fp_seed_{i:04d}", "2026-04-15"),
                )
                cur.execute(
                    """INSERT OR IGNORE INTO trades
                       (trade_id, signal_id, symbol, direction, strategy,
                        qty_planned, qty_filled,
                        entry_target_price, entry_actual_price,
                        sl_initial, tgt_initial,
                        margin_reserved, risk_amount,
                        status, order_protocol,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (trade_id, signal_id, f"SEED{i}", "LONG", "vwap_bounce_long",
                     10, 10, 100.0, 100.0, 98.0, 104.0,
                     1000.0, 200.0, "OPEN", "CO_PLUS_TGT",
                     "2026-04-15 09:45:00", "2026-04-15 09:45:00"),
                )

    def test_risk_rejection_max_open_positions(self, wired_system):
        ctx = wired_system
        symbol = "INFY"

        # Saturate open-position cap (max_open_positions=2 in conftest fixture)
        self._seed_open_trades(ctx, 2)

        # Even with rich quote, risk_engine should block the signal
        ctx.sim_kite.set_rich_quote(symbol, ltp=1500.0)
        rich_md = ctx.sim_kite.get_market_data(symbol)

        with patch.object(ctx.screener, "_build_market_data", return_value=rich_md):
            code, data = _post_webhook(
                ctx, SCANNER_NAME, _make_payload(symbol=symbol, price="1500.0")
            )
            assert code == 200
            assert data["accepted"] == 1

            signal_id = _wait_for_any_signal(ctx, symbol, timeout=3.0)
            assert signal_id is not None

            _risk_reject = {
                "REJECTED_OPEN_POSITIONS",       # RiskEngine failed_check="OPEN_POSITIONS"
                "REJECTED_DAILY_TRADES",
                "REJECTED_DAILY_LOSS",
                "REJECTED_KILL_SWITCH",
                "REJECTED_DUPLICATE_SYMBOL",
                "REJECTED_CONSECUTIVE_LOSSES",
                "REJECTED_SIZING_VALID",
                "REJECTED_CAPITAL",
            }
            terminal = _wait_for_signal_status(ctx, signal_id, _risk_reject, timeout=5.0)

        assert terminal is not None, (
            f"Signal never reached risk-rejected status; last={terminal!r}"
        )
        assert terminal in _risk_reject, f"Unexpected status {terminal!r}"


# ---------------------------------------------------------------------------
# Scenario 4: Deduplication — same signal body sent twice
# ---------------------------------------------------------------------------

class TestScenario4Dedup:
    """
    IT8: WebhookReceiver deduplicates or rejects concurrent in-flight signals.

    Two POSTs with identical body to the same scanner:
      - If the first signal is still in-flight: second returns IN_PROCESS (WR17)
      - If first finished: second returns DUPLICATE (WR7 fingerprint)

    Both outcomes mean the second signal was not newly accepted.
    """

    def test_dedup_second_signal_rejected(self, wired_system):
        ctx = wired_system
        symbol = "TCS"
        payload = _make_payload(symbol=symbol)

        code1, data1 = _post_webhook(ctx, SCANNER_NAME, payload)
        assert code1 == 200
        assert data1["accepted"] == 1, f"First POST should be accepted: {data1}"

        # Second POST: same payload
        code2, data2 = _post_webhook(ctx, SCANNER_NAME, payload)
        assert code2 == 200

        # Either DUPLICATE (fingerprint match) or IN_PROCESS (symbol in-flight)
        # Both mean the second signal was NOT accepted as a new signal
        assert data2["accepted"] == 0, f"Second POST should be rejected: {data2}"
        assert data2["rejected"] == 1

        results2 = data2.get("results", [])
        assert results2, "Expected results list in second response"
        second_status = results2[0]["status"]
        assert second_status in ("DUPLICATE", "IN_PROCESS"), (
            f"Expected DUPLICATE or IN_PROCESS but got {second_status!r}"
        )


# ---------------------------------------------------------------------------
# Scenario 5: Kill switch — signals rejected while kill switch is active
# ---------------------------------------------------------------------------

class TestScenario5KillSwitch:
    """
    IT9: Kill switch blocks signals at the WebhookReceiver level (WR5).

    soft_kill() → POST /webhook → 403 with kill_switch_active message.
    """

    def test_kill_switch_rejects_webhook(self, wired_system):
        ctx = wired_system
        symbol = "HDFCBANK"

        # Activate soft kill
        ctx.kill_switch.soft_kill(reason="test_kill", triggered_by="it_test")
        assert ctx.kill_switch.is_active("entry")

        code, data = _post_webhook(ctx, SCANNER_NAME, _make_payload(symbol=symbol))
        assert code == 403, f"Expected 403 from killed system, got {code}: {data}"

        # Cleanup: resume so teardown is clean
        ctx.kill_switch.resume(reason="test_cleanup", resumed_by="it_test")


# ---------------------------------------------------------------------------
# Scenario 6: EOD squareoff — fire_now() cancels pending entries
# ---------------------------------------------------------------------------

class TestScenario6EodSquareoff:
    """
    IT10: EodSquareoff.fire_now() runs without error in paper mode.

    In paper mode, get_positions() always returns [] so there are no intraday
    positions to exit. EodFireResult is returned with no failures.
    """

    def test_eod_fire_now_succeeds_in_paper_mode(self, wired_system):
        from orders.eod_squareoff import EodSquareoff
        import core.time_authority as ta

        ctx = wired_system
        log = logging.getLogger("it.eod")

        eod = EodSquareoff(
            adapter=ctx.adapter,
            state_store=ctx.store,
            fund_manager=ctx.fund_manager,
            state_machine=ctx.adapter._osm,
            bus=ctx.bus,
            market_windows=ctx.signal_processor._mw,
            time_authority=ta,
            kill_switch=ctx.kill_switch,
            logger=log,
            order_monitor=None,
            inter_order_delay_ms=0,
        )

        result = eod.fire_now(reason="integration_test", triggered_by="it_test")
        assert result is not None, "fire_now() returned None"
        assert result.positions_failed == 0, (
            f"Unexpected position failures: {result.positions_failed}"
        )
        assert result.cancels_failed == 0, (
            f"Unexpected cancel failures: {result.cancels_failed}"
        )


# ---------------------------------------------------------------------------
# Scenario 7: Reconciler manual close — OPEN trade with no broker position
# ---------------------------------------------------------------------------

class TestScenario7ReconcilerManualClose:
    """
    IT11, IT12: OrderReconciler detects manually-closed trade (RC5a).

    In paper mode get_positions() always returns [] (no positions). Pre-seed
    one OPEN trade in the DB. reconcile_once() should detect the mismatch
    (local=OPEN, broker=no position) and mark the trade CLOSED_MANUAL.
    """

    _TRADE_ID = "trd_recon_smoke_001"
    _SIGNAL_ID = "sig_recon_001"
    _SYMBOL = "MARUTI"

    def _seed_open_trade(self, ctx: SystemContext) -> None:
        """Insert a minimal OPEN trade row (committed transaction)."""
        with ctx.store.transaction() as cur:
            # Insert parent signal row first (FK: trades.signal_id → signals.signal_id)
            cur.execute(
                """INSERT OR IGNORE INTO signals
                   (signal_id, symbol, scanner, strategy,
                    triggered_at, received_at, expires_at,
                    status, fingerprint, fingerprint_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self._SIGNAL_ID, self._SYMBOL, "vwap_bounce_long", "vwap_bounce_long",
                 "2026-04-15 09:45:00", "2026-04-15 09:45:00", "2026-04-15 09:46:00",
                 "PROCESSED", "fp_recon_smoke_001", "2026-04-15"),
            )
            cur.execute(
                """INSERT OR IGNORE INTO trades
                   (trade_id, signal_id, symbol, direction, strategy,
                    qty_planned, qty_filled,
                    entry_target_price, entry_actual_price,
                    sl_initial, tgt_initial,
                    margin_reserved, risk_amount,
                    status, order_protocol,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self._TRADE_ID, self._SIGNAL_ID, self._SYMBOL, "LONG", "vwap_bounce_long",
                 5, 5, 10000.0, 10000.0, 9800.0, 10400.0,
                 10000.0, 1000.0, "OPEN", "CO_PLUS_TGT",
                 "2026-04-15 09:45:00", "2026-04-15 09:45:00"),
            )

    def _make_reconciler(self, ctx: SystemContext):
        from orders.order_reconciler import OrderReconciler
        from core.config_loader import OrderReconcilerConfig

        rc_cfg = OrderReconcilerConfig(
            poll_interval_sec=15,
            capital_drift_tolerance=50.0,
        )
        return OrderReconciler(
            state_store=ctx.store,
            adapter=ctx.adapter,
            fund_manager=ctx.fund_manager,
            kill_switch=ctx.kill_switch,
            notifier=None,
            bus=ctx.bus,
            logger=logging.getLogger("it.reconciler"),
            cfg=rc_cfg,
            quote_fn=ctx.adapter.get_quote,
            broker_orders_fn=None,
        )

    def test_reconciler_marks_trade_closed_manual(self, wired_system):
        ctx = wired_system
        self._seed_open_trade(ctx)

        reconciler = self._make_reconciler(ctx)

        # paper mode: get_positions() → [] → RELIANCE is "missing" from broker
        actions = reconciler.reconcile_once()
        assert actions, "Expected at least one reconciliation action"

        # Trade should be marked as manually closed
        rows = ctx.store.fetch_all(
            "SELECT status FROM trades WHERE trade_id = ?", (self._TRADE_ID,)
        )
        assert rows, "Trade row not found after reconcile"
        final_status = rows[0]["status"]
        assert final_status == "CLOSED_MANUAL", (
            f"Expected CLOSED_MANUAL but got {final_status!r}"
        )

    def test_reconciler_writes_reconciliation_log(self, wired_system):
        ctx = wired_system
        self._seed_open_trade(ctx)

        reconciler = self._make_reconciler(ctx)
        actions = reconciler.reconcile_once()
        assert actions

        log_rows = ctx.store.fetch_all(
            "SELECT check_name FROM reconciliation_log WHERE trade_id = ?",
            (self._TRADE_ID,),
        )
        assert log_rows, "Expected reconciliation_log row after MANUAL_CLOSE"
        assert log_rows[0]["check_name"] == "MANUAL_CLOSE"


# ---------------------------------------------------------------------------
# A.3.g — Phase A exit gate: full-lifecycle capital accounting (LONG + SHORT)
# ---------------------------------------------------------------------------

def _seed_signal_row(ctx: SystemContext, signal_id: str, symbol: str) -> None:
    """Insert a minimal signal row; satisfies trades.signal_id FK on create_trade."""
    with ctx.store.transaction() as cur:
        cur.execute(
            """INSERT OR IGNORE INTO signals
               (signal_id, symbol, scanner, strategy,
                triggered_at, received_at, expires_at,
                status, fingerprint, fingerprint_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (signal_id, symbol, "vwap_bounce_long", "vwap_bounce_long",
             "2026-04-15 09:45:00", "2026-04-15 09:45:00", "2026-04-15 09:46:00",
             "PROCESSED", f"fp_{signal_id}", "2026-04-15"),
        )


def _drive_full_lifecycle(
    ctx: SystemContext,
    *,
    symbol: str,
    side: str,
    qty: int,
    entry_price: float,
    sl_price: float,
) -> dict:
    """
    Drive a complete paper-mode trade lifecycle synchronously.

    Reserves capital, calls order_placer.place() (which places all three legs
    through the real LimitTripleProtocol + adapter path), then manually
    publishes OrderFilled for the ENTRY leg and then for the TGT leg.

    With paper_auto_fill_delay_sec=60.0 the adapter's synth threads sleep
    well past test teardown, so the publishes here are the only ones that
    reach the _fill_map before it is drained. No thread-race.

    Returns a dict with trade_id, initial/final snapshots, tgt price, and
    the captured PositionClosed event for downstream assertions.
    """
    signal_id = f"sig_{symbol.lower()}_phaseA"
    _seed_signal_row(ctx, signal_id, symbol)

    initial = ctx.fund_manager.get_snapshot()

    reserve_result = ctx.fund_manager.reserve(
        symbol=symbol, qty=qty, price=entry_price,
        intent="INTRADAY", signal_id=signal_id,
    )
    assert reserve_result.success, f"reserve failed: {reserve_result.reason_if_failed}"
    reservation_id = reserve_result.reservation_id

    # Subscribe BEFORE place so we can't miss the PositionClosed later.
    captured: list[PositionClosed] = []
    ctx.bus.subscribe(PositionClosed, lambda ev: captured.append(ev))

    # place() creates the trade row, places entry+SL+TGT, and registers all
    # three legs in _fill_map. With delay=60s the synth threads are asleep.
    ctx.order_placer.place(
        symbol=symbol,
        side=side,
        qty=qty,
        entry_price=entry_price,
        sl_price=sl_price,
        intent="INTRADAY",
        signal_id=signal_id,
        reservation_id=reservation_id,
    )

    # Post naked-short fix (2.1): LIMIT_TRIPLE.execute() places ENTRY only;
    # SL + TGT are placed by _handle_entry_fill on OrderFilled(ENTRY). So at
    # this point _fill_map has ONE row (ENTRY); TGT appears after we publish.
    with ctx.order_placer._fill_map_lock:
        entry_iid = next(
            iid for iid, fe in ctx.order_placer._fill_map.items()
            if fe.leg == "ENTRY"
        )
        trade_id = ctx.order_placer._fill_map[entry_iid].trade_id

    trade_row = ctx.order_manager.get_trade(trade_id)
    assert trade_row is not None, "trade row missing after place()"
    tgt_price = float(trade_row["tgt_initial"])
    direction = trade_row["direction"]

    # ── ENTRY fill ──────────────────────────────────────────────────────────
    # This fires _handle_entry_fill which calls place_deferred_exits →
    # SL + TGT are now placed + tracked + in _fill_map.
    ctx.bus.publish(OrderFilled(
        source_module="integration_test",
        internal_order_id=entry_iid,
        broker_order_id=f"PAPER_ENTRY_{symbol}",
        symbol=symbol,
        side=side,
        filled_qty=qty,
        avg_fill_price=entry_price,
        expected_price=entry_price,
        slippage_pct=0.0,
        filled_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    ))

    # After entry commit: status=OPEN, capital moved reserved→used.
    opened = ctx.order_manager.get_trade(trade_id)
    assert opened["status"] == "OPEN", f"expected OPEN, got {opened['status']!r}"

    after_entry = ctx.fund_manager.get_snapshot()

    # NOW the TGT leg exists in _fill_map (placed by _handle_entry_fill).
    with ctx.order_placer._fill_map_lock:
        tgt_iid = next(
            iid for iid, fe in ctx.order_placer._fill_map.items()
            if fe.leg == "TGT"
        )

    # ── TGT fill (exit) ─────────────────────────────────────────────────────
    exit_side = "SELL" if side == "BUY" else "BUY"
    ctx.bus.publish(OrderFilled(
        source_module="integration_test",
        internal_order_id=tgt_iid,
        broker_order_id=f"PAPER_TGT_{symbol}",
        symbol=symbol,
        side=exit_side,
        filled_qty=qty,
        avg_fill_price=tgt_price,
        expected_price=tgt_price,
        slippage_pct=0.0,
        filled_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    ))

    closed = ctx.order_manager.get_trade(trade_id)
    assert closed["status"] == "CLOSED", f"expected CLOSED, got {closed['status']!r}"

    final = ctx.fund_manager.get_snapshot()

    return {
        "trade_id": trade_id,
        "direction": direction,
        "tgt_price": tgt_price,
        "initial": initial,
        "after_entry": after_entry,
        "final": final,
        "closed_row": closed,
        "captured_close": captured,
    }


@pytest.mark.parametrize(
    "wired_system",
    [{"paper_auto_fill_delay_sec": 60.0}],
    indirect=True,
)
class TestPhaseAExitGate:
    """
    A.3.g — Phase A exit gate.

    Full-lifecycle paper-mode integration test exercising every module wired
    in Phase A:
        - BL-7 spine (entry→commit, exit→release via _handle_entry_fill /
          _handle_exit_fill, dispatch on _LEG_* taxonomy)
        - BL-10a/BL-10b dual publishers of PositionClosed
        - BL-12 / BL-14 OrderManager.close_trade + direction-aware PnL
        - H-20 / ZA16a paper adapter synth path (here parameterised to 60s
          so the test publishes OrderFilled itself — deterministic, no
          thread scheduling races)
        - EF-3 direction-aware release_used (locked by the SHORT variant)

    Option 1 determinism: delay=60s keeps the adapter's synth threads asleep
    until well past test teardown, so the only OrderFilled events that reach
    OrderPlacer._fill_map are the ones published by this test. Without the
    override, three daemon threads (ENTRY+SL+TGT) would fire in parallel and
    race the manual publishes -- producing an intermittent test that teaches
    engineers to ignore flakes.
    """

    def test_long_happy_path_full_lifecycle_capital_accounting(self, wired_system):
        ctx = wired_system
        symbol = "RELIANCE"
        side = "BUY"
        qty = 10
        entry_price = 100.0
        sl_price = 98.0  # TGT = 100 + (100-98)*2 = 104.0

        out = _drive_full_lifecycle(
            ctx, symbol=symbol, side=side, qty=qty,
            entry_price=entry_price, sl_price=sl_price,
        )

        assert out["direction"] == "LONG"
        assert out["tgt_price"] == pytest.approx(104.0)

        # Capital invariants: full cycle returns reserved & used to zero.
        initial = out["initial"]
        after_entry = out["after_entry"]
        final = out["final"]

        assert initial.intraday_reserved == 0.0
        assert initial.intraday_used == 0.0

        # Post-entry: margin moved from reserved to used (delta should match).
        assert after_entry.intraday_reserved == pytest.approx(0.0)
        assert after_entry.intraday_used == pytest.approx(200.0)
        # margin = qty*entry_price/leverage = 10*100/5 = 200

        # Post-exit: everything released.
        assert final.intraday_reserved == pytest.approx(0.0)
        assert final.intraday_used == pytest.approx(0.0)

        # LONG TGT > entry → positive gross PnL; net after charges.
        gross = (out["tgt_price"] - entry_price) * qty
        assert out["closed_row"]["gross_pnl"] == pytest.approx(gross)
        assert final.daily_realized_pnl > 0.0, (
            "LONG profitable exit must increase daily_realized_pnl"
        )

        # PositionClosed published by order_placer (BL-10a).
        assert len(out["captured_close"]) == 1, (
            f"expected exactly one PositionClosed, got {len(out['captured_close'])}"
        )
        ev = out["captured_close"][0]
        assert ev.trade_id == out["trade_id"]
        assert ev.symbol == symbol
        assert ev.exit_price == pytest.approx(out["tgt_price"])

        # Kill switch never tripped during a clean happy path.
        assert not ctx.kill_switch.is_active("entry")

    def test_short_happy_path_locks_ef3_direction_aware_release(self, wired_system):
        """
        SHORT variant — locks EF-3 at integration level.

        Pre-EF-3, release_used computed gross_pnl = (exit - entry) * qty
        unconditionally. A profitable SHORT (cover below entry) would then
        register as a LOSS and push daily_realized_pnl negative, silently
        wrong for every SHORT trade. This test asserts the post-EF-3
        invariant: TGT hit on a SHORT produces positive daily_realized_pnl.
        """
        ctx = wired_system
        symbol = "INFY"
        side = "SELL"
        qty = 10
        entry_price = 100.0
        sl_price = 102.0   # SL above entry for SHORT
        # OP3 SHORT TGT: tgt = entry - (sl - entry) * rr = 100 - 2*2 = 96.0

        out = _drive_full_lifecycle(
            ctx, symbol=symbol, side=side, qty=qty,
            entry_price=entry_price, sl_price=sl_price,
        )

        assert out["direction"] == "SHORT"
        assert out["tgt_price"] == pytest.approx(96.0)

        final = out["final"]
        assert final.intraday_reserved == pytest.approx(0.0)
        assert final.intraday_used == pytest.approx(0.0)

        # EF-3 assertion: SHORT profit = (entry - exit) * qty > 0.
        expected_gross = (entry_price - out["tgt_price"]) * qty
        assert expected_gross > 0, "test setup bug: SHORT TGT should be profitable"
        assert out["closed_row"]["gross_pnl"] == pytest.approx(expected_gross), (
            "SHORT gross_pnl must be direction-aware (EF-3)"
        )
        assert final.daily_realized_pnl > 0.0, (
            "SHORT profitable exit must increase daily_realized_pnl "
            "(pre-EF-3 this was silently negative)"
        )

        # PositionClosed published (BL-10a) with the correct direction's sign.
        assert len(out["captured_close"]) == 1
        ev = out["captured_close"][0]
        assert ev.trade_id == out["trade_id"]
        assert ev.realized_pnl > 0.0, (
            "PositionClosed.realized_pnl must reflect EF-3 direction sign"
        )
