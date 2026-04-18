"""
orders/order_reconciler.py — Trading System v2

Hybrid periodic + event-driven reconciliation of local trade state vs the
live broker state (G1, G3 Level 3, G5b, P14).

Locked decisions: RC1–RC20.

RC1  — Class OrderReconciler. Public API: start(), stop(), reconcile_once().
RC2  — Constructor injections: state_store, adapter, fund_manager, kill_switch,
        notifier (TelegramNotifier), bus (EventBus), logger, cfg
        (OrderReconcilerConfig), quote_fn, broker_orders_fn=None.
RC3  — Daemon poll thread fires every cfg.poll_interval_sec (P14 = 15 s).
RC4  — If cfg.enable_event_driven, subscribe OrderStateChanged; call
        reconcile_once() on each event. Non-blocking (skips if locked).
RC5  — Six reconciliation checks per cycle:
          (a) MANUAL_CLOSE  — local OPEN/PARTIAL, broker has no position
          (b) ORPHAN_ADOPTION — broker position, no local trade
          (c) HEALTHY        — quantities match
          (d) PARTIAL_CLOSE  — local qty > broker qty
          (e) POSITION_GREW  — broker qty > local qty
          (f) ORPHAN_ORDER   — PENDING_FILL local order not in broker open orders
                               (only when broker_orders_fn is provided)
RC6  — 3-tier action policy: COSMETIC / RECOVERABLE / UNRECOVERABLE (G1).
RC7  — G5b crash-recovery SL: for each OPEN/PARTIAL trade with no active SL
        order, fetch LTP via quote_fn and place a fresh SL-M or MARKET exit.
RC8  — G3 Level 3 capital drift: compare adapter.get_margins().net to
        fund_manager.get_snapshot().total; if delta >
        cfg.capital_drift_tolerance publish CapitalDriftDetected and send a
        CRITICAL alert.
RC9  — ReconciliationAction dataclass fields: check_name, tier, symbol,
        trade_id (nullable), description, action_taken, success.
RC10 — Each non-COSMETIC action is persisted to reconciliation_log via
        state_store.insert_reconciliation_log().
RC11 — BrokerTimeoutError during any check: log WARNING, skip that check,
        continue with the remainder.
RC12 — BrokerAuthError: increment consecutive counter; on 3rd consecutive
        call kill_switch.soft_kill().  Counter resets on any successful
        broker call.
RC13 — reconcile_once() is non-reentrant: uses Lock.acquire(blocking=False);
        returns [] immediately if a cycle is already running.
RC14 — start() triggers one startup reconciliation before the poll thread
        begins.
RC15 — No paper-mode special-casing in reconciler; paper behaviour is owned
        by the adapter and TelegramNotifier.
RC16 — Logger name must be "order_reconciler" so L7 routing applies.
RC17 — Config section: OrderReconcilerConfig (poll_interval_sec,
        capital_drift_tolerance, enable_event_driven).
RC18 — G5b order placement calls adapter.place_order() directly; no import
        from orders.order_placer.
RC19 — reconciliation_log table added in schema v6 (TABLE 13).
RC20 — reconcile_once() returns List[ReconciliationAction] for white-box
        testing.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Callable, Dict, List, Optional

from capital.fund_manager import FundManager
from capital.kill_switch import KillSwitch
from alerts.telegram_notifier import TelegramNotifier
from core.config_loader import OrderReconcilerConfig
from core.events import (
    CapitalDriftDetected,
    EventBus,
    OrderStateChanged,
)
from core.exceptions import BrokerAuthError, BrokerTimeoutError
from core.state_store import StateStore
from orders.order_manager import OrderManager

_IST = timezone(timedelta(hours=5, minutes=30))

# Broker product code -> fund_manager intent (for capital release on MANUAL_CLOSE)
_PRODUCT_TO_INTENT: Dict[str, str] = {
    "MIS": "INTRADAY",
    "CO": "COVER_ORDER",
    "CNC": "DELIVERY",
    "NRML": "DELIVERY",
}


@dataclass
class ReconciliationAction:
    """
    One reconciliation action taken during a _reconcile() cycle (RC9).

    Persisted to reconciliation_log for each non-COSMETIC action (RC10).
    """
    check_name: str          # "MANUAL_CLOSE" | "ORPHAN_ADOPTION" | "HEALTHY" | ...
    tier: str                # "COSMETIC" | "RECOVERABLE" | "UNRECOVERABLE"
    symbol: str
    trade_id: Optional[str]  # None for account-level checks (e.g. CAPITAL_DRIFT)
    description: str
    action_taken: str
    success: bool


class OrderReconciler:
    """
    Reconciles local trade state against the live broker state (RC1-RC20).

    Combines a 15-second daemon poll thread (P14) with optional event-driven
    triggering on OrderStateChanged (G1 hybrid cadence).

    Usage::

        reconciler = OrderReconciler(
            state_store=store, adapter=adapter, fund_manager=fm,
            kill_switch=ks, notifier=notifier, bus=bus,
            logger=get_logger("order_reconciler"),
            cfg=cfg.order_reconciler,
            quote_fn=adapter.get_quote,
            broker_orders_fn=None,   # or adapter.get_open_orders if available
        )
        reconciler.start()           # runs startup reconcile + launches thread
        ...
        reconciler.stop()
    """

    def __init__(
        self,
        state_store: StateStore,
        adapter,                  # ZerodhaAdapter (avoid circular import)
        fund_manager: FundManager,
        kill_switch: KillSwitch,
        notifier: TelegramNotifier,
        bus: EventBus,
        logger: logging.Logger,
        cfg: OrderReconcilerConfig,
        quote_fn: Callable[[List[str]], dict],
        broker_orders_fn: Optional[Callable[[], list]] = None,
    ) -> None:
        self._store = state_store
        self._adapter = adapter
        self._fm = fund_manager
        self._ks = kill_switch
        self._notifier = notifier
        self._bus = bus
        self._log = logger
        self._cfg = cfg
        self._quote_fn = quote_fn
        self._broker_orders_fn = broker_orders_fn
        self._order_mgr = OrderManager(state_store, logger)

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._auth_error_count = 0   # RC12: consecutive BrokerAuthError counter

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        RC14: run startup reconciliation, subscribe events, start poll thread.
        """
        # Startup reconcile before trading begins
        self.reconcile_once()

        # RC4: subscribe OrderStateChanged for event-driven re-checks
        if self._cfg.enable_event_driven:
            self._bus.subscribe(OrderStateChanged, self._on_order_state_changed)

        # Start daemon poll thread (RC3)
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="order_reconciler_poll",
            daemon=True,
        )
        self._thread.start()
        self._log.info(
            "order_reconciler started (poll_interval=%ds, event_driven=%s)",
            self._cfg.poll_interval_sec,
            self._cfg.enable_event_driven,
        )

    def stop(self) -> None:
        """Signal poll thread to exit and wait for it to finish."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._log.info("order_reconciler stopped")

    def reconcile_once(self) -> List[ReconciliationAction]:
        """
        Public entry point: acquire lock (non-blocking) and run a full cycle.

        Returns the list of ReconciliationActions taken (RC20).
        Returns [] immediately if a cycle is already running (RC13).
        """
        if not self._lock.acquire(blocking=False):
            self._log.debug("order_reconciler: cycle already running, skipping")
            return []
        try:
            return self._reconcile()
        except Exception as exc:
            self._log.error("reconcile_once unhandled error: %s", exc, exc_info=True)
            return []
        finally:
            self._lock.release()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _on_order_state_changed(self, evt: OrderStateChanged) -> None:
        """RC4: event-driven trigger; non-blocking."""
        self.reconcile_once()

    def _poll_loop(self) -> None:
        """Daemon thread body: sleep poll_interval_sec then reconcile."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._cfg.poll_interval_sec)
            if self._stop_event.is_set():
                break
            self.reconcile_once()

    def _note_auth_error(self, cycle_errors: list) -> None:
        """Record a BrokerAuthError occurrence within the current cycle."""
        cycle_errors.append(1)
        self._log.error(
            "BrokerAuthError in order_reconciler (cycle_errors_so_far=%d)",
            len(cycle_errors),
        )

    def _finalise_auth_counter(self, had_auth_error: bool) -> None:
        """
        RC12: update consecutive-cycle auth-error counter after a cycle.

        Increments by 1 per cycle (not per call) and soft_kills on 3 consecutive
        cycles with auth errors.  Resets to 0 if the cycle had no auth errors.
        """
        if had_auth_error:
            self._auth_error_count += 1
            self._log.error(
                "order_reconciler: consecutive auth-error cycles=%d",
                self._auth_error_count,
            )
            if self._auth_error_count >= 3:
                self._ks.soft_kill(
                    reason="order_reconciler: 3 consecutive BrokerAuthError cycles",
                    triggered_by="order_reconciler",
                )
        else:
            self._auth_error_count = 0

    def _now_ist(self) -> str:
        return datetime.now(_IST).isoformat()

    # ── Core reconcile cycle ──────────────────────────────────────────────────

    def _reconcile(self) -> List[ReconciliationAction]:
        """
        Run all 6 checks + G5b crash-recovery SL + G3 capital drift.

        BrokerTimeoutError on a broker call -> log WARNING, skip that check,
        continue (RC11). Lock is already held by the caller.
        RC12: at most one counter increment per cycle.
        """
        actions: List[ReconciliationAction] = []
        cycle_auth_errors: list = []   # RC12: track per-cycle, not per-call

        # ── Fetch broker positions (needed by checks 1-5) ──────────────────
        raw_positions = None
        try:
            raw_positions = self._adapter.get_positions()
        except BrokerTimeoutError:
            self._log.warning(
                "order_reconciler: get_positions timed out; skipping checks 1-5 (RC11)"
            )
        except BrokerAuthError:
            self._note_auth_error(cycle_auth_errors)

        local_trades = self._store.get_all_open_trades()

        if raw_positions is not None:
            broker_pos = {p.symbol: p for p in raw_positions}
            local_symbols = {t["symbol"] for t in local_trades}

            # Checks 1, 3, 4, 5: iterate local open trades
            for trade in local_trades:
                symbol = trade["symbol"]
                bp = broker_pos.get(symbol)

                if bp is None:
                    # CHECK 1: MANUAL_CLOSE (RC5a)
                    actions.append(self._check1_manual_close(trade))

                else:
                    local_qty = trade["qty_filled"] or 0
                    broker_qty = abs(bp.qty)

                    if broker_qty == local_qty:
                        # CHECK 3: HEALTHY (RC5c)
                        actions.append(ReconciliationAction(
                            check_name="HEALTHY",
                            tier="COSMETIC",
                            symbol=symbol,
                            trade_id=trade["trade_id"],
                            description=f"Position healthy: qty={local_qty}",
                            action_taken="none",
                            success=True,
                        ))

                    elif broker_qty < local_qty:
                        # CHECK 4: PARTIAL_CLOSE (RC5d)
                        actions.append(self._check4_partial_close(trade, broker_qty))

                    else:
                        # CHECK 5: POSITION_GREW (RC5e)
                        actions.append(self._check5_position_grew(
                            trade, broker_qty, local_qty
                        ))

            # CHECK 2: ORPHAN_ADOPTION — broker position not in local trades (RC5b)
            for symbol, bp in broker_pos.items():
                if symbol not in local_symbols:
                    actions.append(self._check2_orphan_adoption(symbol, bp))

        # CHECK 6: ORPHAN_ORDER (only when broker_orders_fn provided) (RC5f)
        if self._broker_orders_fn is not None:
            try:
                actions.extend(self._check6_orphan_orders())
            except BrokerTimeoutError:
                self._log.warning(
                    "order_reconciler: check6 get_open_orders timed out; skipping (RC11)"
                )
            except BrokerAuthError:
                self._note_auth_error(cycle_auth_errors)

        # G5b: CRASH_RECOVERY_SL — missing SL on open trades (RC7)
        for trade in local_trades:
            sl_row = self._store.get_sl_order_for_trade(trade["trade_id"])
            if sl_row is None:
                act = self._g5b_crash_recovery_sl(trade)
                if act is not None:
                    actions.append(act)

        # G3: CAPITAL_DRIFT (RC8)
        cap_act = self._g3_capital_drift(cycle_auth_errors)
        if cap_act is not None:
            actions.append(cap_act)

        # RC12: update consecutive auth-error counter once per cycle
        self._finalise_auth_counter(had_auth_error=bool(cycle_auth_errors))

        # Persist non-COSMETIC actions to reconciliation_log (RC10)
        ts = self._now_ist()
        for act in actions:
            if act.tier != "COSMETIC":
                try:
                    self._store.insert_reconciliation_log(
                        ts=ts,
                        check_name=act.check_name,
                        tier=act.tier,
                        symbol=act.symbol,
                        trade_id=act.trade_id,
                        description=act.description,
                        action_taken=act.action_taken,
                        success=act.success,
                    )
                except Exception as exc:
                    self._log.error(
                        "insert_reconciliation_log failed for %s: %s",
                        act.check_name, exc,
                    )

        non_cosmetic = [a for a in actions if a.tier != "COSMETIC"]
        if non_cosmetic:
            self._log.info(
                "reconcile cycle: %d actions (%d non-cosmetic)",
                len(actions), len(non_cosmetic),
            )

        return actions

    # ── CHECK 1: MANUAL_CLOSE ─────────────────────────────────────────────────

    def _check1_manual_close(self, trade) -> ReconciliationAction:
        """
        Local trade is OPEN/PARTIAL but broker has no matching position (RC5a).

        Marks trade CLOSED_MANUAL and releases used capital via fund_manager,
        using entry_actual_price as exit proxy (breakeven PnL, avoids false
        profit/loss).
        """
        trade_id = trade["trade_id"]
        symbol = trade["symbol"]
        success = True
        steps: List[str] = []

        try:
            self._store.mark_trade_manually_closed(trade_id)
            steps.append("mark_trade_manually_closed")
        except Exception as exc:
            self._log.error(
                "check1: mark_trade_manually_closed failed for %s: %s", trade_id, exc
            )
            success = False
            steps.append(f"mark_trade_manually_closed FAILED: {exc}")

        # Release capital — breakeven proxy (entry == exit -> PnL = 0)
        entry_price = trade["entry_actual_price"]
        qty = trade["qty_filled"] or 0
        product = trade["product"]
        intent = _PRODUCT_TO_INTENT.get(product or "", "")
        # EF-3: release_used now requires direction. Breakeven means the sign
        # doesn't matter numerically, but the param is required. Fall back to
        # LONG + WARN if the trade row is missing direction (row shouldn't exist).
        try:
            direction = trade["direction"] or "LONG"
        except (KeyError, IndexError):
            direction = "LONG"
        if direction not in ("LONG", "SHORT"):
            self._log.warning(
                "check1: trade %s has unexpected direction %r; defaulting to LONG",
                trade_id, direction,
            )
            direction = "LONG"

        if entry_price and float(entry_price) > 0 and qty > 0 and intent:
            try:
                self._fm.release_used(
                    symbol=symbol,
                    exit_price=float(entry_price),
                    exit_qty=qty,
                    intent=intent,
                    entry_price=float(entry_price),
                    direction=direction,
                    costs=0.0,
                )
                steps.append("capital_released(breakeven)")
            except Exception as exc:
                self._log.warning(
                    "check1: release_used failed for %s: %s", trade_id, exc
                )
                steps.append(f"capital_release FAILED: {exc}")
        elif not intent:
            self._log.warning(
                "check1: unknown product %r for %s; skipping capital release",
                product, trade_id,
            )

        self._log.warning(
            "CHECK1 MANUAL_CLOSE: trade_id=%s symbol=%s "
            "local=OPEN/PARTIAL broker=no_position",
            trade_id, symbol,
        )
        return ReconciliationAction(
            check_name="MANUAL_CLOSE",
            tier="RECOVERABLE",
            symbol=symbol,
            trade_id=trade_id,
            description=(
                f"Local trade {trade_id} is OPEN/PARTIAL but broker has "
                f"no position for {symbol}"
            ),
            action_taken="; ".join(steps) if steps else "none",
            success=success,
        )

    # ── CHECK 2: ORPHAN_ADOPTION ──────────────────────────────────────────────

    def _check2_orphan_adoption(self, symbol: str, bp) -> ReconciliationAction:
        """
        Broker position exists but no local trade tracks it (RC5b).

        This is UNRECOVERABLE — system has no trade record to reconcile against.
        Publishes CapitalDriftDetected so downstream handlers can react.
        """
        self._log.error(
            "CHECK2 ORPHAN_ADOPTION: symbol=%s broker_qty=%d avg_price=%.2f "
            "— no local trade found",
            symbol, bp.qty, bp.avg_price,
        )
        try:
            notional = float(abs(bp.qty)) * float(bp.avg_price)
            self._bus.publish(CapitalDriftDetected(
                source_module="order_reconciler",
                expected=0.0,
                actual=notional,
                delta=notional,
            ))
        except Exception as exc:
            self._log.error("check2: publish CapitalDriftDetected failed: %s", exc)

        return ReconciliationAction(
            check_name="ORPHAN_ADOPTION",
            tier="UNRECOVERABLE",
            symbol=symbol,
            trade_id=None,
            description=(
                f"Broker has position in {symbol} qty={bp.qty} "
                f"avg_price={bp.avg_price:.2f} but no local trade"
            ),
            action_taken="CapitalDriftDetected published; manual intervention required",
            success=True,
        )

    # ── CHECK 4: PARTIAL_CLOSE ────────────────────────────────────────────────

    def _check4_partial_close(self, trade, broker_qty: int) -> ReconciliationAction:
        """
        Local qty_filled > broker qty — partial position closure at broker (RC5d).

        Updates qty_filled in DB.  Capital adjustment is not attempted here
        since actual exit prices are unavailable; operator reconciles manually.
        """
        trade_id = trade["trade_id"]
        symbol = trade["symbol"]
        local_qty = trade["qty_filled"] or 0
        success = True

        try:
            with self._store.transaction() as cur:
                cur.execute(
                    "UPDATE trades SET qty_filled = ?, updated_at = ? WHERE trade_id = ?",
                    (broker_qty, self._now_ist(), trade_id),
                )
        except Exception as exc:
            self._log.error(
                "check4: update qty_filled failed for %s: %s", trade_id, exc
            )
            success = False

        self._log.warning(
            "CHECK4 PARTIAL_CLOSE: trade_id=%s %s local_qty=%d broker_qty=%d",
            trade_id, symbol, local_qty, broker_qty,
        )
        return ReconciliationAction(
            check_name="PARTIAL_CLOSE",
            tier="RECOVERABLE",
            symbol=symbol,
            trade_id=trade_id,
            description=(
                f"Local qty_filled={local_qty} > broker_qty={broker_qty} for {symbol}"
            ),
            action_taken=f"qty_filled updated to {broker_qty}",
            success=success,
        )

    # ── CHECK 5: POSITION_GREW ────────────────────────────────────────────────

    def _check5_position_grew(
        self, trade, broker_qty: int, local_qty: int
    ) -> ReconciliationAction:
        """
        Broker qty > local qty_filled — unexpected position growth (RC5e).

        Hard discrepancy. Publishes CapitalDriftDetected and logs at ERROR.
        """
        trade_id = trade["trade_id"]
        symbol = trade["symbol"]

        self._log.error(
            "CHECK5 POSITION_GREW: trade_id=%s %s local_qty=%d broker_qty=%d",
            trade_id, symbol, local_qty, broker_qty,
        )
        try:
            self._bus.publish(CapitalDriftDetected(
                source_module="order_reconciler",
                expected=float(local_qty),
                actual=float(broker_qty),
                delta=float(broker_qty - local_qty),
            ))
        except Exception as exc:
            self._log.error("check5: publish CapitalDriftDetected failed: %s", exc)

        return ReconciliationAction(
            check_name="POSITION_GREW",
            tier="UNRECOVERABLE",
            symbol=symbol,
            trade_id=trade_id,
            description=(
                f"Broker qty={broker_qty} > local qty_filled={local_qty} for {symbol}"
            ),
            action_taken="CapitalDriftDetected published; manual intervention required",
            success=True,
        )

    # ── CHECK 6: ORPHAN_ORDER ─────────────────────────────────────────────────

    def _check6_orphan_orders(self) -> List[ReconciliationAction]:
        """
        PENDING_FILL local orders whose broker_order_id is absent from the
        broker's open-order list (RC5f).

        Only runs when broker_orders_fn is not None. Calls broker_orders_fn()
        which may raise BrokerTimeoutError / BrokerAuthError (handled by caller).
        """
        actions: List[ReconciliationAction] = []
        broker_open = self._broker_orders_fn()
        broker_ids = {
            str(o.get("order_id", "")) for o in (broker_open or [])
        }

        pending = self._store.get_pending_all_products()
        for trade in pending:
            bid = str(trade["broker_order_id"] or "")
            if bid and bid not in broker_ids:
                self._log.warning(
                    "CHECK6 ORPHAN_ORDER: trade_id=%s broker_order_id=%s "
                    "not found in broker open orders",
                    trade["trade_id"], bid,
                )
                actions.append(ReconciliationAction(
                    check_name="ORPHAN_ORDER",
                    tier="UNRECOVERABLE",
                    symbol=trade["symbol"],
                    trade_id=trade["trade_id"],
                    description=(
                        f"PENDING_FILL order {bid} not found in "
                        f"broker open orders for {trade['symbol']}"
                    ),
                    action_taken="logged; manual intervention required",
                    success=True,
                ))
        return actions

    # ── G5b: CRASH_RECOVERY_SL ───────────────────────────────────────────────

    def _g5b_crash_recovery_sl(self, trade) -> Optional[ReconciliationAction]:
        """
        OPEN/PARTIAL trade has no active SL order — place a fresh recovery order
        per G5b logic (RC7).

        LONG:  LTP > sl_initial -> SL-M SELL at sl_initial
               LTP <= sl_initial -> MARKET SELL (SL already breached)
        SHORT: LTP < sl_initial -> SL-M BUY  at sl_initial
               LTP >= sl_initial -> MARKET BUY  (SL already breached)

        Order is placed via adapter.place_order() (RC18 — no order_placer import)
        and persisted via OrderManager.insert_order().
        """
        trade_id = trade["trade_id"]
        symbol = trade["symbol"]
        direction = trade["direction"]   # "LONG" | "SHORT"
        sl_price = trade["sl_initial"]
        qty = trade["qty_filled"] or 0
        product = trade["product"]
        intent = _PRODUCT_TO_INTENT.get(product or "", "INTRADAY")

        if qty <= 0 or sl_price is None or float(sl_price) <= 0:
            return None

        sl_price = float(sl_price)

        self._log.warning(
            "G5b CRASH_RECOVERY_SL: trade_id=%s %s %s "
            "has no active SL order — placing recovery order",
            trade_id, symbol, direction,
        )

        # Fetch LTP via injected quote_fn
        try:
            quotes = self._quote_fn([symbol])
            ltp = quotes[symbol].last_price if symbol in quotes else None
        except Exception as exc:
            self._log.error("G5b: quote_fn failed for %s: %s", symbol, exc)
            return ReconciliationAction(
                check_name="CRASH_RECOVERY_SL",
                tier="RECOVERABLE",
                symbol=symbol,
                trade_id=trade_id,
                description=f"No active SL for {direction} {symbol}; LTP fetch failed",
                action_taken=f"quote_fn error: {exc}",
                success=False,
            )

        if ltp is None:
            self._log.warning(
                "G5b: no LTP available for %s; cannot place recovery SL", symbol
            )
            return None

        # Determine order side, type, and trigger based on G5b rules
        if direction == "LONG":
            side = "SELL"
            if ltp > sl_price:
                order_type = "SL-M"
                trigger = sl_price
                desc = (
                    f"LONG {symbol}: LTP={ltp} > sl={sl_price} "
                    f"-> placing SL-M SELL at {sl_price}"
                )
            else:
                order_type = "MARKET"
                trigger = 0.0
                desc = (
                    f"LONG {symbol}: LTP={ltp} <= sl={sl_price} "
                    f"-> SL breached; placing MARKET SELL"
                )
        else:  # SHORT
            side = "BUY"
            if ltp < sl_price:
                order_type = "SL-M"
                trigger = sl_price
                desc = (
                    f"SHORT {symbol}: LTP={ltp} < sl={sl_price} "
                    f"-> placing SL-M BUY at {sl_price}"
                )
            else:
                order_type = "MARKET"
                trigger = 0.0
                desc = (
                    f"SHORT {symbol}: LTP={ltp} >= sl={sl_price} "
                    f"-> SL breached; placing MARKET BUY"
                )

        try:
            placed = self._adapter.place_order(
                symbol=symbol,
                side=side,
                qty=qty,
                price=0.0,
                order_type=order_type,
                intent=intent,
                tag="rc_recovery_sl",
                trigger_price=trigger,
            )
            # Persist order to DB so get_sl_order_for_trade() won't re-fire
            self._order_mgr.insert_order(
                trade_id=trade_id,
                broker_order_id=placed.broker_order_id,
                leg="SL",
                transaction_type=side,
                order_type=order_type,
                product=placed.product,
                variety=placed.variety,
                qty_requested=qty,
                price=0.0,
                trigger_price=trigger,
            )
            self._log.warning(
                "G5b: placed %s %s for trade_id=%s broker_order_id=%s",
                order_type, side, trade_id, placed.broker_order_id,
            )
            return ReconciliationAction(
                check_name="CRASH_RECOVERY_SL",
                tier="RECOVERABLE",
                symbol=symbol,
                trade_id=trade_id,
                description=desc,
                action_taken=(
                    f"Placed {order_type} {side} qty={qty} "
                    f"broker_order_id={placed.broker_order_id}"
                ),
                success=True,
            )
        except BrokerTimeoutError as exc:
            self._log.error(
                "G5b: place_order timed out for trade_id=%s: %s", trade_id, exc
            )
            return ReconciliationAction(
                check_name="CRASH_RECOVERY_SL",
                tier="RECOVERABLE",
                symbol=symbol,
                trade_id=trade_id,
                description=desc,
                action_taken=f"place_order timed out: {exc}",
                success=False,
            )
        except BrokerAuthError as exc:
            self._log.error("G5b: place_order auth error for trade_id=%s: %s", trade_id, exc)
            return ReconciliationAction(
                check_name="CRASH_RECOVERY_SL",
                tier="RECOVERABLE",
                symbol=symbol,
                trade_id=trade_id,
                description=desc,
                action_taken=f"place_order auth error: {exc}",
                success=False,
            )
        except Exception as exc:
            self._log.error(
                "G5b: place_order failed for trade_id=%s: %s",
                trade_id, exc, exc_info=True,
            )
            return ReconciliationAction(
                check_name="CRASH_RECOVERY_SL",
                tier="RECOVERABLE",
                symbol=symbol,
                trade_id=trade_id,
                description=desc,
                action_taken=f"place_order failed: {exc}",
                success=False,
            )

    # ── G3: CAPITAL_DRIFT ─────────────────────────────────────────────────────

    def _g3_capital_drift(
        self, cycle_auth_errors: list
    ) -> Optional[ReconciliationAction]:
        """
        G3 Level 3: compare adapter.get_margins().net to
        fund_manager.get_snapshot().total (RC8).

        If |actual - expected| > cfg.capital_drift_tolerance:
          - Publish CapitalDriftDetected(expected, actual, delta)
          - Send CRITICAL alert via TelegramNotifier (which also writes sentinel)

        cycle_auth_errors is the shared mutable list used by _reconcile() to
        track per-cycle auth errors (RC12).
        """
        try:
            margins = self._adapter.get_margins()
        except BrokerTimeoutError:
            self._log.warning(
                "order_reconciler: get_margins timed out; skipping G3 drift check (RC11)"
            )
            return None
        except BrokerAuthError:
            self._note_auth_error(cycle_auth_errors)
            return None

        snapshot = self._fm.get_snapshot()
        expected = snapshot.total
        actual = margins.net
        delta = abs(actual - expected)

        if delta <= self._cfg.capital_drift_tolerance:
            return None

        self._log.error(
            "G3 CAPITAL_DRIFT: expected=%.2f actual=%.2f delta=%.2f tolerance=%.2f",
            expected, actual, delta, self._cfg.capital_drift_tolerance,
        )

        try:
            self._bus.publish(CapitalDriftDetected(
                source_module="order_reconciler",
                expected=expected,
                actual=actual,
                delta=delta,
            ))
        except Exception as exc:
            self._log.error("G3: publish CapitalDriftDetected failed: %s", exc)

        try:
            self._notifier.send(
                severity="CRITICAL",
                title="Capital Drift Detected",
                body=(
                    f"Broker capital ({actual:.2f}) differs from local "
                    f"({expected:.2f}) by {delta:.2f}, "
                    f"exceeding tolerance {self._cfg.capital_drift_tolerance:.2f}."
                ),
                source_module="order_reconciler",
                context={
                    "expected": expected,
                    "actual": actual,
                    "delta": delta,
                    "tolerance": self._cfg.capital_drift_tolerance,
                },
            )
        except Exception as exc:
            self._log.error("G3: TelegramNotifier.send failed: %s", exc)

        return ReconciliationAction(
            check_name="CAPITAL_DRIFT",
            tier="UNRECOVERABLE",
            symbol="",
            trade_id=None,
            description=(
                f"Broker capital={actual:.2f} vs local={expected:.2f} "
                f"delta={delta:.2f} exceeds tolerance={self._cfg.capital_drift_tolerance:.2f}"
            ),
            action_taken="CapitalDriftDetected published; CRITICAL alert sent",
            success=True,
        )
