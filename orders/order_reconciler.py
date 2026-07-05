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
RC4  — RETIRED. Previously subscribed OrderStateChanged for event-driven
        kicks. Audit #12 disabled at runtime (rate-limit pressure under
        bursts of OSM transitions); 2026-04-26 audit DEAD-1/CFG-4 removed
        the subscriber method and the enable_event_driven config flag.
        Reconciliation is daemon-poll only.
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
        order, fetch LTP via quote_fn and place a fresh SL (stop-limit) or MARKET exit.
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
        capital_drift_tolerance).
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
from datetime import date, datetime, time as dt_time, timedelta
from typing import Callable, Dict, List, Optional

from capital.fund_manager import FundManager
from capital.kill_switch import KillSwitch
from alerts.telegram_notifier import TelegramNotifier
from core.config_loader import OrderReconcilerConfig
from core.events import (
    CapitalDriftDetected,
    EventBus,
    PositionClosed,  # BL-10b: out-of-band closure notification
)
from core.exceptions import BrokerAuthError, BrokerTimeoutError
from core.logger import bind_trade, log_exception
from core.market_windows import is_market_day, is_within_market_hours
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.order_manager import OrderManager
from orders.price_math import (
    DEFAULT_SL_LIMIT_OFFSET_PCT,
    DEFAULT_TICK,
    EMERGENCY_EXIT_BUFFER_PCT,
    calc_sl_limit_price,
    marketable_limit_price,
)

# DUP-1 (2026-04-26 audit): _IST removed; never read locally.

# FIX-166 F17: canonical copy now in core.constants
from core.constants import PRODUCT_TO_INTENT as _PRODUCT_TO_INTENT

# Terminal order statuses — never overwritten by a cancel/sweep (FIX-186).
_TERMINAL_ORDER_STATUSES: frozenset[str] = frozenset(
    {"COMPLETE", "CANCELLED", "FAILED", "EXPIRED"}
)

# FIX-189 (P1-B): window during which the broker funds/margins endpoint is
# reliable. Outside it (overnight / pre-auth), Zerodha's funds endpoint returns
# net=0.0, which the G3 check otherwise reads as a catastrophic capital drift.
# Slightly padded around the 09:15-15:30 session so live-session reads always
# count as reliable.
_MARGIN_RELIABLE_OPEN = dt_time(9, 0)
_MARGIN_RELIABLE_CLOSE = dt_time(15, 45)

# FIX-186 (FIX 1): classify a broker cancel_order failure reason so the local
# DB can be finalized correctly even when order_monitor never polls again
# (e.g. the cancel landed at EOD shutdown — the 17-Jun IRFC orphan leak).
#   already-gone  → the order no longer exists at the broker → mark CANCELLED.
#   being-processed → the order is mid-fill and may COMPLETE → do NOT mark;
#                     let order_monitor observe the real terminal state.
_CANCEL_ALREADY_GONE_MARKERS: tuple[str, ...] = (
    "not found",
    "does not exist",
    "no such order",
    "already cancel",   # "already cancelled"
)
_CANCEL_BEING_PROCESSED_MARKERS: tuple[str, ...] = (
    "being processed",
)


def _cancel_reason_already_gone(reason: str) -> bool:
    """True if a failed broker cancel means the order is already gone."""
    r = (reason or "").lower()
    return any(m in r for m in _CANCEL_ALREADY_GONE_MARKERS)


def _cancel_reason_being_processed(reason: str) -> bool:
    """True if a failed broker cancel means the order is mid-fill (may COMPLETE)."""
    r = (reason or "").lower()
    return any(m in r for m in _CANCEL_BEING_PROCESSED_MARKERS)


# LAYER 1 (RAMCOIND fix, 25-Jun): G5b is CRASH recovery. A trade whose entry filled
# only seconds ago is still having its SL/TGT placed by the normal LIMIT_TRIPLE path,
# so recovery has no business firing yet. Skipping recovery within this window off the
# PERSISTED entry-fill time deterministically closes the ~40ms TOCTOU race against the
# lagging local orders table (the 25-Jun duplicate-SL), in EVERY mode, without
# depending on broker/_fill_map timing. An old unprotected fill (after a real crash /
# restart) is well past the window, so genuine recovery still fires.
_G5B_SETTLING_WINDOW_SEC = 10.0


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


# ── A-1/E-1: tag-correlation recovery (naked-orphan fix) ────────────────────
_ENTRY_SIDE = {"LONG": "BUY", "SHORT": "SELL"}

# Recovery-state trades whose ENTRY may be live at the broker but has no local
# orders row (timeout / crash). These broker statuses partition the correlated
# ENTRY order: filled (protect it), still resting (defer, stay protected), or
# genuinely dead (FAILED + release). Kite uses "TRIGGER PENDING" for a resting
# stop and "OPEN"/"AMO REQ RECEIVED"/… for a resting limit.
_RECOVERY_FILLED_STATUSES = frozenset({"COMPLETE"})
_RECOVERY_DEAD_STATUSES = frozenset({"REJECTED", "CANCELLED"})
# A broker order is TERMINAL (no further fills possible) once COMPLETE / REJECTED /
# CANCELLED. Recovery only adopts (protects) or FAILEDs on a TERMINAL order: a
# terminal order with filled_quantity>0 is a real position (even a partial-then-
# cancel) that MUST be protected, never disowned; a terminal order with zero fill
# genuinely did not fill -> safe FAILED. A non-terminal (still-resting) order —
# even one partially filled — is DEFERRED so its final qty is adopted once it
# settles, avoiding an SL placed for a qty that then grows.
_RECOVERY_TERMINAL_STATUSES = _RECOVERY_FILLED_STATUSES | _RECOVERY_DEAD_STATUSES
# Poll budget before a broker-reachable ABSENCE is trusted as FAILED (guards the
# order-propagation window; ~45s at the 15s reconcile poll, matching FIX-068).
_RECOVERY_ABSENCE_POLL_BUDGET = 3
_RECOVERY_STATES = ("UNKNOWN_IN_FLIGHT", "PENDING", "PENDING_FILL")


def correlate_entry_by_tag(trade_id, direction, symbol, qty, all_orders):
    """Correlate a broker ENTRY order back to a local recovery-state trade by its
    broker tag (A-1/E-1). PURE function (no I/O) → unit-testable.

    Recovery scenario: an ENTRY reached the broker but its local orders row was never
    persisted (timeout / crash), so it must be found at the broker WITHOUT a
    broker_order_id. Every production entry is tagged ``truncate_tag_for_broker(trade_id)``
    (structural guarantee — place() has no tag param); entry+SL+TGT SHARE that tag, so
    candidates are filtered to the ENTRY leg by side.

    ``all_orders`` = ``adapter.get_all_orders()`` dicts (keys: tag, transaction_type,
    symbol, quantity, status, order_id, …). Returns ``(kind, order)``:
      - ("MATCH", order)     exactly one entry-side order carries our tag (or a unique
                             symbol+qty narrowing of a tag collision)
      - ("ABSENT", None)     no entry-side order carries our tag
      - ("AMBIGUOUS", None)  >1 candidate even after symbol+qty narrowing (a real 48-bit
                             tag collision — astronomically rare) → caller must NOT
                             blind-adopt: protect/flatten + flag for manual.
    """
    from core.ids import truncate_tag_for_broker  # local import (file convention)

    expected_tag = truncate_tag_for_broker(trade_id or "")
    entry_side = _ENTRY_SIDE.get((direction or "").upper())
    if not expected_tag or entry_side is None:
        return ("ABSENT", None)

    cands = [
        o for o in (all_orders or [])
        if (o.get("tag") or "") == expected_tag
        and (o.get("transaction_type") or "").upper() == entry_side
    ]
    if len(cands) == 1:
        return ("MATCH", cands[0])
    if not cands:
        return ("ABSENT", None)

    # >1 share tag+side (tag collision). Narrow by symbol + qty before giving up.
    narrowed = [
        o for o in cands
        if (o.get("symbol") or "") == (symbol or "")
        and int(o.get("quantity") or 0) == int(qty or 0)
    ]
    if len(narrowed) == 1:
        return ("MATCH", narrowed[0])
    return ("AMBIGUOUS", None)


class OrderReconciler:
    """
    Reconciles local trade state against the live broker state (RC1-RC20).

    Pure 15-second daemon poll thread (P14). Audit #12 disabled the
    optional event-driven trigger; the subscriber and the
    enable_event_driven flag were removed by the 2026-04-26 audit.

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
        mode: str = "LIVE",      # session mode label for alert title
        order_placer: Optional[Any] = None,  # FIX-068: OrderPlacer for timeout recovery
        cnc_gtt_monitor: Optional[Any] = None,  # SLICE2.5-P2: overnight-GTT reconcile
        market_hours_fn: Optional[Callable[[], bool]] = None,  # SLICE2.5-P2: cadence gate
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
        self._mode = mode
        self._order_mgr = OrderManager(state_store, logger)
        self._order_placer = order_placer  # FIX-068
        # SLICE2.5-P2: overnight CNC-GTT reconcile (startup [4a] + 15-min in-hours
        # cadence [4b]). When wired, delivery (CNC) trades with an ACTIVE gtt_state
        # row are EXCLUDED from the position/SL/exit checks below and managed here.
        self._cnc_gtt_monitor = cnc_gtt_monitor
        self._market_hours_fn = market_hours_fn or (lambda: True)
        self._cnc_poll_count = 0
        self._cnc_first_in_hours_done = False
        self._cnc_monitor_every = 60  # cycles between full monitor runs (15 min / 15s)

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._auth_error_count = 0   # RC12: consecutive BrokerAuthError counter

        # FIX-038: exponential backoff for repeated alerts
        self._poll_count: int = 0
        self._alerted_discrepancies: Dict[tuple, dict] = {}  # (trade_id, check_name) -> {alert_count, next_alert_at_poll}

        # B-1: unrealized-MTM refresh observability counters
        self._mtm_refresh_success: int = 0
        self._mtm_refresh_failure: int = 0

        # FIX-068: Track poll counts for UNKNOWN_IN_FLIGHT trades
        # Maps trade_id -> poll_count (incremented each cycle; FAILED after 3)
        self._timeout_poll_counts: Dict[str, int] = {}

        # FIX-B: Track orphan cycle counts
        # Maps trade_id -> cycle_count (incremented each cycle orphan is seen; auto-close after 3)
        self._orphan_cycle_count: Dict[str, int] = {}

        # FIX-182: human / untracked broker positions (CHECK2 orphans with no
        # local trade record at all). The system manages only system trades;
        # these are operator-placed Kite orders we neither adopt nor protect.
        # Logged once per symbol per day, then silenced; their margin widens
        # the G3 capital-drift tolerance so they don't spam CRITICAL alerts.
        self._human_order_symbols: set[str] = set()
        self._human_order_date: Optional[date] = None
        # LAYER 3 (RAMCOIND fix, 25-Jun): symbols whose SYSTEM_OVERSELL residual has
        # already been flattened today — so we never double-cover if the cover MARKET
        # order has not yet cleared the broker position by the next cycle.
        self._oversell_flattened_symbols: set[str] = set()
        # Default Rs 5000 if cfg omits it (back-compat with older configs) or
        # if cfg is a test mock whose attribute isn't a real number.
        _hot = getattr(cfg, "human_order_margin_tolerance", 5000.0)
        try:
            self._human_order_margin_tolerance: float = float(_hot)
        except (TypeError, ValueError):
            self._human_order_margin_tolerance = 5000.0

        # TASK-11: dedicated throttle for repeat CAPITAL_DRIFT alerts. First
        # detection alerts immediately; thereafter at most once per the
        # configured interval (default 1800s / 30 min). Replaces the generic
        # exponential backoff for this check. Default if cfg omits it (older
        # configs) or cfg is a test mock whose attr isn't a real number.
        _cdai = getattr(cfg, "capital_drift_alert_interval_sec", 1800.0)
        try:
            self._capital_drift_alert_interval_sec: float = float(_cdai)
        except (TypeError, ValueError):
            self._capital_drift_alert_interval_sec = 1800.0
        self._last_capital_drift_alert_poll: Optional[int] = None
        # Per-EPISODE drift logging: a reconciliation_log row is written only
        # when a NEW drift episode begins or an alert actually fires — NOT on
        # every suppressed 15s cycle. Stops the "144 log rows for 3 alerts"
        # report inflation (System Manager counts rows). The alert throttle
        # (_should_alert_capital_drift) is unchanged.
        self._drift_episode_active: bool = False

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        RC14: run startup reconciliation, subscribe events, start poll thread.
        """
        # FIX-008: check for unexpected CNC overnight positions before trading
        self._check_cnc_overnight_positions()

        # SLICE2.5-P2 (4a): re-verify every overnight GTT against the broker (now
        # seeing HOLDINGS, not just same-day positions) — recreate a vanished one,
        # finalise a GTT-fired exit. The monitor's Y4 guard handles a stale/delayed
        # token at 08:15 (defer + alert, never crash, never treat no-data as flat);
        # the next in-hours poll retries.
        if self._cnc_gtt_monitor is not None:
            try:
                self._cnc_gtt_monitor.reconcile()
            except Exception as exc:  # noqa: BLE001 — startup must never crash
                self._log.error("cnc_gtt_monitor startup reconcile failed: %s", exc)

        # Startup reconcile before trading begins
        self.reconcile_once()

        # Start daemon poll thread (RC3)
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="order_reconciler_poll",
            daemon=True,
        )
        self._thread.start()
        self._log.info(
            "order_reconciler started (poll_interval=%ds)",
            self._cfg.poll_interval_sec,
        )

    def stop(self) -> None:
        """Signal poll thread to exit and wait for it to finish."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._log.info("order_reconciler stopped")

    def sweep_stale_orders(self) -> int:
        """
        FIX-186 (FIX 2): defense-in-depth sweep. Mark any non-terminal local
        order whose parent trade is already terminal (CLOSED / CLOSED_MANUAL /
        CANCELLED / FAILED) as CANCELLED. This backstops any orphan order row
        that a broker-side cancel finalized without updating the local DB —
        e.g. a cancel that landed at EOD before order_monitor's next poll
        (the 17-Jun IRFC leak). Mode-agnostic; runs identically in paper/live.

        Called at startup (after reconcile_once) and at EOD (after the two-pass
        squareoff). Returns the number of rows swept. Sends a WARNING Telegram
        alert if any were swept.
        """
        try:
            rows = self._store.fetch_all(
                """SELECT order_id FROM orders
                   WHERE status NOT IN ('COMPLETE','CANCELLED','FAILED','EXPIRED')
                     AND trade_id IN (
                         SELECT trade_id FROM trades
                         WHERE status IN ('CLOSED','CLOSED_MANUAL','CANCELLED','FAILED')
                     )""",
            )
        except Exception as exc:
            self._log.error("sweep_stale_orders: query failed: %s", exc)
            return 0

        if not rows:
            return 0

        ts = self._now_ist()
        swept = 0
        try:
            with self._store.transaction() as cur:
                for row in rows:
                    cur.execute(
                        "UPDATE orders SET status = 'CANCELLED', updated_at = ? "
                        "WHERE order_id = ? AND status NOT IN "
                        "('COMPLETE','CANCELLED','FAILED','EXPIRED')",
                        (ts, row["order_id"]),
                    )
                    if cur.rowcount and cur.rowcount > 0:
                        swept += cur.rowcount
        except Exception as exc:
            self._log.error("sweep_stale_orders: update failed: %s", exc)
            return 0

        if swept > 0:
            self._log.warning(
                "Sweep: marked %d stale orders as CANCELLED (parent trade terminal)",
                swept,
            )
            if self._notifier is not None:
                try:
                    self._notifier.send(
                        severity="WARNING",
                        title=f"[{self._mode}] Stale orders swept",
                        body=(
                            f"Marked {swept} stale order(s) as CANCELLED — their "
                            f"parent trade is already terminal (orphan-order backstop)."
                        ),
                        source_module="order_reconciler",
                    )
                except Exception as exc:
                    self._log.error("sweep_stale_orders: notifier.send failed: %s", exc)
        return swept

    def _should_alert_for_discrepancy(
        self, trade_id: Optional[str], check_name: str, bypass_backoff: bool = False
    ) -> bool:
        """
        FIX-038: Exponential backoff for repeated alerts.

        Returns True if we should alert now, False if still in backoff window.
        Updates _alerted_discrepancies tracking dict.

        Backoff schedule:
        - 1st detection: alert immediately
        - 2nd alert: after 8 polls (~2 mins at 15s/poll)
        - 3rd alert: after 32 polls (~8 mins)
        - 4th+ alert: every 120 polls (~30 mins) - capped

        Args:
            trade_id: Trade ID (or None for account-level checks)
            check_name: Check name (e.g., "POSITION_GREW")
            bypass_backoff: If True, always alert (for HARD_KILL, MISSING_EXITS)
        """
        if bypass_backoff:
            return True

        key = (trade_id or "", check_name)

        if key not in self._alerted_discrepancies:
            # First detection - alert immediately
            self._alerted_discrepancies[key] = {
                "alert_count": 1,
                "next_alert_at_poll": self._poll_count + 8,  # Next alert in 8 polls
            }
            return True

        entry = self._alerted_discrepancies[key]
        if self._poll_count >= entry["next_alert_at_poll"]:
            # Time to alert again
            entry["alert_count"] += 1
            alert_count = entry["alert_count"]

            # Calculate next alert poll based on exponential backoff (capped at 120)
            if alert_count == 2:
                next_gap = 32  # 3rd alert after 32 polls
            elif alert_count == 3:
                next_gap = 120  # 4th+ alerts every 120 polls
            else:
                next_gap = 120  # Cap at 120 polls (30 mins)

            entry["next_alert_at_poll"] = self._poll_count + next_gap
            return True

        # Still in backoff window
        return False

    def _should_alert_capital_drift(self) -> bool:
        """
        TASK-11: throttle repeat G3 CAPITAL_DRIFT alerts to at most once per
        ``capital_drift_alert_interval_sec`` (default 1800s / 30 min).

        First detection alerts immediately; subsequent detections are suppressed
        until the configured interval (converted to poll cycles) has elapsed.
        This replaces the generic exponential backoff for CAPITAL_DRIFT so the
        repeat cadence is a single operator-tunable value rather than a
        2min/8min/30min ramp. Capital drift is informational here — kill
        escalation is governed separately by drift_handler thresholds — so a
        quieter, fixed cadence is safe.

        Returns True if we should alert now, False if still within the window.
        """
        poll_interval = max(1, int(getattr(self._cfg, "poll_interval_sec", 15)))
        interval_polls = max(
            1, round(self._capital_drift_alert_interval_sec / poll_interval)
        )
        last = self._last_capital_drift_alert_poll
        if last is None or (self._poll_count - last) >= interval_polls:
            self._last_capital_drift_alert_poll = self._poll_count
            return True
        return False

    def _mark_discrepancy_resolved(
        self, trade_id: Optional[str], check_name: str
    ) -> None:
        """
        FIX-038: Mark a discrepancy as resolved, removing it from tracking.

        Logs INFO when a previously-alerted discrepancy is resolved.
        """
        key = (trade_id or "", check_name)
        if key in self._alerted_discrepancies:
            del self._alerted_discrepancies[key]
            self._log.info(
                "discrepancy resolved: trade_id=%s check=%s",
                trade_id or "(account-level)",
                check_name,
            )

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
            # FIX-038: increment poll count for backoff calculations
            self._poll_count += 1
            return self._reconcile()
        except Exception as exc:
            self._log.error("reconcile_once unhandled error: %s", exc, exc_info=True)
            return []
        finally:
            self._lock.release()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Daemon thread body: sleep poll_interval_sec then reconcile."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._cfg.poll_interval_sec)
            if self._stop_event.is_set():
                break
            self.reconcile_once()
            self._maybe_run_cnc_monitor()

    def _maybe_run_cnc_monitor(self) -> None:
        """SLICE2.5-P2 (4b): run the overnight-GTT reconcile on the 15-min cadence,
        ONLY within market hours; drain the Y1 pre-open queue immediately on the FIRST
        in-hours cycle (not waiting for the 15-min mark). Reuses this daemon — no new
        scheduler (mirrors the _should_alert_capital_drift throttle pattern)."""
        if self._cnc_gtt_monitor is None:
            return
        try:
            in_hours = bool(self._market_hours_fn())
        except Exception:  # noqa: BLE001 — predicate failure -> treat as out-of-hours
            in_hours = False
        if not in_hours:
            return
        self._cnc_poll_count += 1
        first = not self._cnc_first_in_hours_done
        if first or (self._cnc_poll_count % self._cnc_monitor_every == 0):
            self._cnc_first_in_hours_done = True
            try:
                self._cnc_gtt_monitor.reconcile(in_hours=True)
            except Exception as exc:  # noqa: BLE001 — never kill the poll thread
                self._log.error("cnc_gtt_monitor.reconcile failed: %s", exc)

    def _run_gtt_adoption_prepass(self) -> None:
        """FIX-183: run the CncGttMonitor orphan-GTT adoption pass as a NARROW
        prepass at the top of every reconcile cycle, so an adopted gtt_state row
        excludes its carried CNC trade from CHECK1 before CHECK1 can mis-close it
        (the C2.1 gap). No-op when no monitor is wired. Never raises — a prepass
        failure must not stop the reconcile's own safety checks from running.

        This is the ONLY safe placement for both paths: startup runs reconcile_once()
        (order_reconciler.start) and the 15-min poll runs reconcile_once() before the
        monitor's own reconcile() — so adopting inside reconcile_once (here) precedes
        its CHECK1 in BOTH, without reordering the existing MIS-path checks."""
        if self._cnc_gtt_monitor is None:
            return
        try:
            self._cnc_gtt_monitor.adopt_orphan_gtts()
        except Exception as exc:  # noqa: BLE001 — prepass must never break the cycle
            self._log.error("cnc_gtt adoption prepass failed: %s", exc)

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
        return now_ist().isoformat()

    # ── FIX-008: CNC overnight position bootstrap check ───────────────────────

    def _check_cnc_overnight_positions(self) -> None:
        """
        FIX-008: At startup, warn if the broker holds any CNC (delivery)
        positions that have no corresponding OPEN local trade.

        These are unexpected overnight carry-over positions that the system
        did not open this session. The check is advisory (WARNING log + alert)
        — it does NOT place exits or call soft_kill, because CNC positions may
        be intentional positions managed outside this system.

        Skips silently if adapter.get_positions() raises.
        """
        try:
            positions = self._adapter.get_positions()
        except Exception as exc:
            self._log.warning(
                "FIX-008 cnc_check: get_positions failed at startup: %s — skipping", exc
            )
            return

        if not positions:
            return

        # Collect symbols with OPEN local trades for quick lookup
        try:
            open_trades = self._store.get_all_open_trades()
        except Exception as exc:
            self._log.warning(
                "FIX-008 cnc_check: get_all_open_trades failed: %s — skipping", exc
            )
            return

        local_symbols = {t["symbol"] for t in (open_trades or [])}

        for pos in positions:
            product = getattr(pos, "product", None) or (
                pos.get("product", "") if isinstance(pos, dict) else ""
            )
            if str(product).upper() != "CNC":
                continue

            symbol = getattr(pos, "symbol", None) or (
                pos.get("symbol", "") if isinstance(pos, dict) else ""
            )
            qty = getattr(pos, "qty", 0) or (
                pos.get("qty", 0) if isinstance(pos, dict) else 0
            )
            if not qty:
                continue

            if symbol not in local_symbols:
                self._log.warning(
                    "FIX-008 CNC_OVERNIGHT: broker holds CNC position %s qty=%s "
                    "with no matching local OPEN trade — possible overnight carry-over",
                    symbol, qty,
                )
                if self._notifier is not None:
                    try:
                        self._notifier.send(
                            severity="WARNING",
                            title=f"[{self._mode}] CNC overnight position detected",
                            body=(
                                f"Broker holds CNC {symbol} qty={qty} with no local "
                                f"OPEN trade. This may be an overnight carry-over. "
                                f"Manual review required."
                            ),
                            source_module="order_reconciler",
                        )
                    except Exception as exc:
                        self._log.error("FIX-008 notifier.send failed: %s", exc)

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

        # FIX-183: ORPHAN-GTT ADOPTION PREPASS — reconstruct any live broker GTT
        # that has no gtt_state row and correlate it to its open delivery trade,
        # BEFORE the delivery-exclusion build + CHECK1 below. Closes C2.1: a carried
        # row-less CNC GTT would otherwise be mis-marked CLOSED_MANUAL by CHECK1 (its
        # holding lives in holdings(), not positions(), so bp is None). Narrow +
        # fail-safe: never crashes the cycle, only ever inserts a row or WARNs.
        self._run_gtt_adoption_prepass()

        # A-1/E-1: UNIFIED IN-FLIGHT-ENTRY RECOVERY PREPASS. Correlate every
        # timed-out (UNKNOWN_IN_FLIGHT) or crashed (orphaned-PENDING) entry back
        # to its broker order by tag and adopt-and-protect (or FAILED-and-release
        # only on confirmed absence). Runs HERE — before CHECK1 and the G5b loop —
        # so an adopted-OPEN trade gets its recovery SL (G5b) + TGT retry in this
        # SAME cycle rather than sitting naked for a full poll interval. Replaces
        # the old post-G5b _check_unknown_in_flight (timeout-only, blind FAILED).
        # Never raises (see the method); a recovery failure must not stop the
        # safety checks below.
        if self._order_placer is not None:
            actions.extend(self._recover_in_flight_entries())

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

        # SLICE2.5-P2: delivery (CNC) trades with an ACTIVE overnight GTT are managed
        # by CncGttMonitor (holdings + GTT aware), NOT by the position/SL/exit checks
        # below — a carried CNC holding lives in holdings() not positions(), so CHECK1
        # would wrongly mark it CLOSED_MANUAL and G5b/CHECK9 would place a spurious SL.
        # local_symbols still includes them so CHECK2 won't orphan-adopt a held CNC.
        try:
            _delivery_rows = self._store.get_active_gtt_states()
        except Exception:  # noqa: BLE001 — gtt_state read must never break reconcile
            _delivery_rows = []
        delivery_trade_ids = {r["trade_id"] for r in _delivery_rows}
        delivery_symbols = {r["symbol"] for r in _delivery_rows}

        # FIX-186 (FIX 3): keep the broker-position snapshot in scope for the G5b
        # recovery-SL loop below so it can skip symbols the broker no longer holds.
        # None means "snapshot unavailable" (timeout/auth) → G5b proceeds (fail-safe).
        broker_pos: Optional[dict] = None
        if raw_positions is not None:
            broker_pos = {p.symbol: p for p in raw_positions}
            local_symbols = {t["symbol"] for t in local_trades}

            # Checks 1, 3, 4, 5: iterate local open trades
            for trade in local_trades:
                if trade["trade_id"] in delivery_trade_ids:
                    continue  # SLICE2.5-P2: delivery trade -> CncGttMonitor owns it
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
                        # FIX-038: Mark any previous discrepancies as resolved
                        self._mark_discrepancy_resolved(trade["trade_id"], "POSITION_GREW")
                        self._mark_discrepancy_resolved(trade["trade_id"], "PARTIAL_CLOSE")
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

            # CHECK 2: ORPHAN_ADOPTION — broker position not in local OPEN/PARTIAL
            # trades (RC5b). FIX-181: a broker position whose only local record
            # is an in-flight (PENDING_FILL/PENDING) trade is NOT a true orphan —
            # its entry filled at the broker. On HARD_KILL it must be flattened,
            # not abandoned (GICRE incident); otherwise it is a transient fill
            # the normal fill path will complete, so we don't act destructively.
            for symbol, bp in broker_pos.items():
                if symbol not in local_symbols and symbol not in delivery_symbols:
                    # Task 4 (2026-06-19): a broker position whose only local
                    # record is an EXITING trade is OURS mid-exit, not an orphan.
                    # local_symbols only covers OPEN/PARTIAL (get_all_open_trades),
                    # so without this guard CHECK2 would wrongly adopt/flag a
                    # stuck-EXITING position before _check_stuck_exiting (below)
                    # resolves it. Leave it for that handler.
                    if self._store.get_trades_by_status_and_symbol(
                        ("EXITING",), symbol
                    ):
                        continue
                    inflight = self._store.get_trades_by_status_and_symbol(
                        ("PENDING_FILL", "PENDING"), symbol
                    )
                    if inflight:
                        actions.append(
                            self._check2_inflight_orphan(symbol, bp, inflight[0])
                        )
                    else:
                        actions.append(self._check2_orphan_adoption(symbol, bp))

            # Task 4 (2026-06-19): resolve trades stuck in EXITING. A HARD_KILL /
            # emergency flatten marks a trade EXITING before flattening (Bug A,
            # FIX-190); if the process dies mid-exit (the 19-Jun incident) the
            # trade lingers in EXITING with locked capital + orphan SL/TGT, and the
            # checks above never touch it (they only handle OPEN/PARTIAL/PENDING_
            # FILL). Runs inside the raw_positions guard so it always has broker
            # truth to decide flat-vs-still-held (never acts blind).
            actions.extend(self._check_stuck_exiting(broker_pos))

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

        # Bug 3 (FIX-180): checks 1-5 above can transition trades to
        # CLOSED_MANUAL / PARTIAL in the DB *this same cycle* (e.g. CHECK 1
        # manual-close when the broker position is gone). The SL-placement
        # logic below (check9 missing-exits, G5b crash-recovery) must NOT act
        # on the stale snapshot taken at the top of the cycle — otherwise it
        # places recovery SL orders on positions that were just reconciled
        # closed. Re-query fresh so closed trades drop out of the working set.
        # SLICE2.5-P2: also exclude delivery trades (CncGttMonitor owns them) so
        # CHECK9 / G5b / duplicate-exit never place a spurious SL on a GTT-protected
        # CNC position (its protection is the broker GTT, not a local SL order row).
        local_trades = [
            t for t in self._store.get_all_open_trades()
            if t["trade_id"] not in delivery_trade_ids
        ]

        # FIX-002: MISSING_EXITS — OPEN trade has SL in local DB but not on broker
        # Runs before G5b so naked positions are caught before recovery attempts.
        if self._broker_orders_fn is not None and raw_positions is not None:
            try:
                actions.extend(self._check9_missing_exits(local_trades))
            except BrokerTimeoutError:
                self._log.warning(
                    "order_reconciler: check9 get_open_orders timed out; skipping (RC11)"
                )
            except BrokerAuthError:
                self._note_auth_error(cycle_auth_errors)
            except Exception as exc:
                self._log.error(
                    "_check9_missing_exits unhandled error: %s", exc, exc_info=True
                )

        # G5b: CRASH_RECOVERY_SL — missing SL on open trades (RC7)
        for trade in local_trades:
            sl_row = self._store.get_sl_order_for_trade(trade["trade_id"])
            if sl_row is None:
                act = self._g5b_crash_recovery_sl(trade, broker_positions=broker_pos)
                if act is not None:
                    actions.append(act)

        # LAYER 2 (RAMCOIND fix, 25-Jun): one-live-SL / one-live-TGT invariant.
        # Always-on net BEHIND the Layer-1 G5b guard — cancels any duplicate exit leg
        # (e.g. a G5b/LIMIT_TRIPLE placement race) within one cycle, long before a
        # stop-hit, so a duplicate can never become a naked over-sell.
        try:
            actions.extend(self._check_duplicate_exits(local_trades))
        except Exception as exc:
            self._log.error(
                "_check_duplicate_exits unhandled error: %s", exc, exc_info=True
            )

        # G3: CAPITAL_DRIFT (RC8)
        cap_act = self._g3_capital_drift(cycle_auth_errors)
        if cap_act is not None:
            actions.append(cap_act)

        # CHECK 7: CAPITAL_ACCOUNTING_DRIFT (BL-3) -- fm vs fm_ledger
        try:
            actions.extend(self._check7_capital_accounting_drift())
        except Exception as exc:
            self._log.error(
                "_check7_capital_accounting_drift unhandled error: %s",
                exc, exc_info=True,
            )

        # CHECK 8: CO_SL_DRIFT (M-2) -- alert-only; compares broker CO
        # trigger_price against SmartTgtManager-tracked current_sl. MUST NOT
        # call adapter.modify_order in this check -- auto-repair is deferred
        # pending paper-trial data on genuine drift frequency.
        try:
            actions.extend(self._check8_co_sl_drift())
        except BrokerTimeoutError:
            self._log.warning(
                "order_reconciler: check8 get_open_orders timed out; skipping (RC11)"
            )
        except BrokerAuthError:
            self._note_auth_error(cycle_auth_errors)
        except Exception as exc:
            self._log.error(
                "_check8_co_sl_drift unhandled error: %s",
                exc, exc_info=True,
            )

        # A-1/E-1: the in-flight-entry recovery now runs as a PREPASS at the top
        # of this cycle (see _recover_in_flight_entries above) — not here — so an
        # adopted-OPEN entry is protected by the G5b loop above in the same cycle.

        # B-1: refresh per-position unrealized MTM for the pre-trade daily-loss gate
        # (advisory; never touches capital accounting). Never raises — a refresh
        # failure must not stop the cycle's safety checks.
        try:
            self._refresh_unrealized_mtm()
        except Exception as exc:  # noqa: BLE001
            self._log.error("_refresh_unrealized_mtm unhandled error: %s", exc, exc_info=True)
            try:
                self._fm.mark_unrealized_mtm_refreshed(available=False)
            except Exception:  # noqa: BLE001
                pass

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

    # ── CHECK 1: MANUAL_CLOSE / RMS_SQUAREOFF ───────────────────────────────────

    def _check1_manual_close(self, trade) -> ReconciliationAction:
        """
        Local trade is OPEN/PARTIAL but broker has no matching position (RC5a).

        FIX-148 (GAP 5): Enhanced to handle RMS auto-squareoff correctly:
        1. Fetch actual exit price from broker trades() (not breakeven proxy)
        2. Cancel orphaned SL/TGT orders at broker
        3. Send CRITICAL Telegram alert with real PnL
        4. Release capital with actual exit price
        """
        trade_id = trade["trade_id"]
        symbol = trade["symbol"]
        log = bind_trade(self._log, trade_id=trade_id)
        success = True
        steps: List[str] = []

        try:
            actually_closed = self._store.mark_trade_manually_closed(trade_id)
            steps.append("mark_trade_manually_closed")
        except Exception as exc:
            log.error(
                "check1: mark_trade_manually_closed failed for %s: %s", trade_id, exc
            )
            success = False
            actually_closed = False
            steps.append(f"mark_trade_manually_closed FAILED: {exc}")

        if not actually_closed:
            log.info(
                "check1: trade %s already in terminal status; skipping capital release",
                trade_id,
            )
            steps.append("skip_release(already_closed)")
            return ReconciliationAction(
                check_name="MANUAL_CLOSE",
                tier="COSMETIC",
                symbol=symbol,
                trade_id=trade_id,
                description=(
                    f"Trade {trade_id} already closed by another path; "
                    f"no capital release needed"
                ),
                action_taken="; ".join(steps),
                success=True,
            )

        # FIX-148: Cancel orphaned SL/TGT orders at broker before capital release.
        cancelled_count = self._cancel_orphaned_orders_for_trade(trade_id, symbol, log)
        if cancelled_count > 0:
            steps.append(f"cancelled_{cancelled_count}_orphaned_orders")

        entry_price = trade["entry_actual_price"]
        qty = trade["qty_filled"] or 0
        product = trade["product"]
        intent = _PRODUCT_TO_INTENT.get(product or "", "")

        try:
            direction = trade["direction"] or "LONG"
        except (KeyError, IndexError):
            direction = "LONG"
        if direction not in ("LONG", "SHORT"):
            log.warning(
                "check1: trade %s has unexpected direction %r; defaulting to LONG",
                trade_id, direction,
            )
            direction = "LONG"

        # FIX-148: Fetch actual exit price from broker trades API.
        exit_price = self._resolve_exit_price(symbol, direction, entry_price, log)
        exit_source = "broker_trades" if exit_price != entry_price else "entry_proxy"
        steps.append(f"exit_price={exit_price:.2f}({exit_source})")

        if entry_price and float(entry_price) > 0 and qty > 0 and intent:
            try:
                release_result = self._fm.release_used(
                    symbol=symbol,
                    exit_price=float(exit_price),
                    exit_qty=qty,
                    intent=intent,
                    entry_price=float(entry_price),
                    direction=direction,
                    costs=0.0,
                )
                steps.append(f"capital_released(pnl={release_result.pnl_delta:.2f})")
            except Exception as exc:
                log.warning(
                    "check1: release_used failed for %s: %s", trade_id, exc
                )
                steps.append(f"capital_release FAILED: {exc}")
            else:
                # Bug 4 (FIX-180): persist exit financials onto the trade row so
                # trades.net_pnl matches fm_ledger.pnl_delta. RMS/manual closes
                # pass costs=0.0 above, so gross==net and charges=0.0.
                try:
                    self._store.record_manual_close_financials(
                        trade_id=trade_id,
                        exit_price=float(exit_price),
                        net_pnl=float(release_result.pnl_delta),
                    )
                    steps.append("trade_financials_recorded")
                except Exception as exc:
                    log.warning(
                        "check1: record_manual_close_financials failed for %s: %s",
                        trade_id, exc,
                    )
                    steps.append(f"trade_financials FAILED: {exc}")
                try:
                    self._bus.publish(PositionClosed(
                        source_module="order_reconciler",
                        symbol=symbol,
                        trade_id=trade_id,
                        signal_id=trade["signal_id"] or "",
                        exit_price=float(exit_price),
                        realized_pnl=float(release_result.pnl_delta),
                    ))
                    steps.append("position_closed_published")
                except Exception as exc:
                    log_exception(log, exc)
                    log.error(
                        "reconciler.manual_close_publish_position_closed_failed",
                        extra={"trade_id": trade_id, "symbol": symbol},
                    )

                # FIX-148: CRITICAL Telegram alert with real PnL
                if self._notifier is not None:
                    try:
                        pnl = release_result.pnl_delta
                        self._notifier.send(
                            severity="CRITICAL",
                            title=f"[{self._mode}] RMS/MANUAL CLOSE -- {symbol}",
                            body=(
                                f"Position closed externally\n"
                                f"Trade: {trade_id}\n"
                                f"Entry: {float(entry_price):.2f} | Exit: {exit_price:.2f}\n"
                                f"Qty: {qty} | PnL: {pnl:+.2f}\n"
                                f"Source: {exit_source}"
                            ),
                            source_module="order_reconciler",
                        )
                    except Exception as exc:
                        log.error("check1: notifier.send failed: %s", exc)
        elif not intent:
            log.warning(
                "check1: unknown product %r for %s; skipping capital release",
                product, trade_id,
            )

        log.critical(
            "CHECK1 MANUAL_CLOSE: trade_id=%s symbol=%s "
            "local=OPEN/PARTIAL broker=no_position exit_price=%.2f exit_source=%s",
            trade_id, symbol, exit_price if exit_price else 0.0, exit_source,
        )
        return ReconciliationAction(
            check_name="MANUAL_CLOSE",
            tier="RECOVERABLE",
            symbol=symbol,
            trade_id=trade_id,
            description=(
                f"Local trade {trade_id} is OPEN/PARTIAL but broker has "
                f"no position for {symbol}; exit_price={exit_price:.2f}"
            ),
            action_taken="; ".join(steps) if steps else "none",
            success=success,
        )

    def _check_stuck_exiting(self, broker_pos: dict) -> List[ReconciliationAction]:
        """
        Task 4 (2026-06-19): resolve trades stuck in EXITING.

        A HARD_KILL / emergency flatten marks a trade EXITING before flattening
        (Bug A, FIX-190). EXITING is meant to be transient (EXITING ->
        CLOSED/CLOSED_MANUAL once the exit reconciles). If the process dies
        mid-exit (the 19-Jun incident) the trade lingers in EXITING with locked
        capital + orphan SL/TGT, and CHECK1/G5b never touch it (they only act on
        OPEN/PARTIAL/PENDING_FILL). Resolved here against broker truth:
          - flat at broker -> CHECK1 finalize (CLOSED_MANUAL + release capital +
            cancel orphan SL/TGT) — the incident's case; now that
            mark_trade_manually_closed accepts EXITING this works end-to-end with
            no manual EXITING->OPEN flip.
          - still holding  -> hand back to normal management (EXITING -> OPEN) +
            WARNING; SL/TGT, EOD squareoff, or an active kill switch's own flatten
            loop then closes it the proper way.

        Only trades EXITING longer than cfg.stuck_exiting_timeout_minutes are
        touched, so an exit legitimately in progress is left alone. The caller
        runs this inside the raw_positions guard, so broker_pos is always real
        broker truth (never act blind).
        """
        actions: List[ReconciliationAction] = []
        timeout_min = int(getattr(self._cfg, "stuck_exiting_timeout_minutes", 30) or 30)
        cutoff = (now_ist() - timedelta(minutes=timeout_min)).isoformat()
        try:
            stuck = self._store.get_stuck_exiting_trades(cutoff)
        except Exception as exc:
            self._log.error(
                "_check_stuck_exiting: get_stuck_exiting_trades failed: %s",
                exc, exc_info=True,
            )
            return actions

        for trade in stuck:
            symbol = trade["symbol"]
            bp = broker_pos.get(symbol)
            broker_qty = abs(bp.qty) if bp is not None else 0
            try:
                if broker_qty == 0:
                    # Flat at broker: flatten succeeded, DB never finalized.
                    actions.append(self._check1_manual_close(trade))
                else:
                    # Still holding: resume normal management.
                    actions.append(self._resume_exiting_to_open(trade, broker_qty))
            except Exception as exc:
                self._log.error(
                    "_check_stuck_exiting: resolve failed for %s: %s",
                    trade["trade_id"], exc, exc_info=True,
                )
        return actions

    def _resume_exiting_to_open(self, trade, broker_qty: int) -> ReconciliationAction:
        """
        Task 4 (2026-06-19): a stuck EXITING trade STILL has a live broker
        position. Flip it back to OPEN so the normal protective machinery (SL/TGT,
        EOD squareoff, or an active kill switch's flatten loop) manages it — the
        generalized form of the 19-Jun manual recovery (flip EXITING -> OPEN).
        Capital is left as-is: a live position legitimately holds its margin.
        """
        trade_id = trade["trade_id"]
        symbol = trade["symbol"]
        log = bind_trade(self._log, trade_id=trade_id)
        reverted = False
        try:
            reverted = self._store.revert_exiting_to_open(trade_id)
        except Exception as exc:
            log.error(
                "stuck_exiting: revert_exiting_to_open failed for %s: %s",
                trade_id, exc,
            )

        if reverted:
            log.warning(
                "STUCK_EXITING: trade_id=%s symbol=%s still held at broker "
                "(qty=%d) -> reverted EXITING to OPEN for normal management",
                trade_id, symbol, broker_qty,
            )
            if self._notifier is not None:
                try:
                    self._notifier.send(
                        severity="WARNING",
                        title=f"[{self._mode}] Stuck EXITING resumed -> OPEN",
                        body=(
                            f"{symbol} ({trade_id}) was stuck in EXITING with a "
                            f"live broker position (qty={broker_qty}); reverted to "
                            f"OPEN so SL/TGT/EOD (or the kill switch) manages it."
                        ),
                        source_module="order_reconciler",
                    )
                except Exception as exc:
                    log.error("stuck_exiting: notifier.send failed: %s", exc)

        return ReconciliationAction(
            check_name="STUCK_EXITING",
            tier="RECOVERABLE",
            symbol=symbol,
            trade_id=trade_id,
            description=(
                f"Trade {trade_id} stuck in EXITING with live position "
                f"qty={broker_qty}; reverted to OPEN"
                if reverted else
                f"Trade {trade_id} stuck in EXITING; revert to OPEN FAILED "
                f"(already left EXITING or DB error)"
            ),
            action_taken="revert_exiting_to_open" if reverted else "revert_failed",
            success=reverted,
        )

    def _resolve_exit_price(
        self, symbol: str, direction: str, entry_price, log
    ) -> float:
        """
        FIX-148: Best-effort exit price resolution for externally closed positions.

        Priority: broker trades() → LTP quote → entry_price fallback.
        """
        entry_f = float(entry_price) if entry_price else 0.0

        # Try broker trades API
        try:
            broker_trades = self._adapter.get_trades()
            exit_side = "SELL" if direction == "LONG" else "BUY"
            matching = [
                t for t in broker_trades
                if t["tradingsymbol"] == symbol
                and t["transaction_type"] == exit_side
                and t["quantity"] > 0
            ]
            if matching:
                latest = matching[-1]
                price = latest["average_price"]
                if price > 0:
                    log.info(
                        "check1: exit_price resolved from broker trades: %.2f", price
                    )
                    return price
        except Exception as exc:
            log.warning("check1: get_trades failed: %s — trying LTP fallback", exc)

        # Fallback: fetch current LTP
        try:
            quotes = self._quote_fn([symbol])
            if symbol in quotes:
                ltp = quotes[symbol].last_price
                if ltp and ltp > 0:
                    log.info(
                        "check1: exit_price resolved from LTP: %.2f", ltp
                    )
                    return ltp
        except Exception as exc:
            log.warning("check1: quote_fn failed: %s — using entry_price proxy", exc)

        return entry_f

    def _cancel_orphaned_orders_for_trade(
        self, trade_id: str, symbol: str, log
    ) -> int:
        """
        FIX-148: Cancel any open SL/TGT orders at broker for a trade whose
        position has been externally closed. Returns count of successfully
        cancelled orders.

        FIX-186 (FIX 1): finalize the LOCAL orders row immediately after the
        broker cancel, rather than relying on order_monitor to observe the
        cancellation on a later poll. If the cancel lands at EOD shutdown
        (the 17-Jun IRFC incident), order_monitor never polls again and the
        stale non-terminal row leaks into the next trading day where no check
        can match it (the broker resets order history daily). Classify the
        broker response:
          - success                 → mark CANCELLED in local DB
          - "order not found"/gone  → mark CANCELLED (already terminal at broker)
          - "being processed"       → leave alone (may fill); order_monitor owns it
          - any other error         → leave alone for retry; log ERROR
        """
        cancelled = 0
        try:
            rows = self._store.fetch_all(
                """SELECT order_id, leg FROM orders
                   WHERE trade_id = ? AND leg IN ('SL', 'TGT')
                     AND status NOT IN ('CANCELLED', 'COMPLETE', 'REJECTED', 'FAILED')""",
                (trade_id,),
            )
        except Exception as exc:
            log.error("check1: orphan order lookup failed: %s", exc)
            return 0

        for row in (rows or []):
            broker_id = row["order_id"]
            leg = row["leg"]
            if not broker_id:
                continue
            try:
                result = self._adapter.cancel_order(broker_id)
            except Exception as exc:
                log.warning(
                    "check1: cancel orphaned %s order %s raised: %s",
                    leg, broker_id, exc,
                )
                continue

            if result.success:
                self._mark_order_cancelled_local(broker_id, log)
                log.info(
                    "Orphan order %s (%s) cancelled at broker AND marked "
                    "CANCELLED in local DB for trade %s",
                    broker_id, leg, trade_id,
                )
                cancelled += 1
            elif _cancel_reason_already_gone(result.reason):
                # Order no longer exists at the broker — finalize locally so it
                # cannot leak as an orphan row.
                self._mark_order_cancelled_local(broker_id, log)
                log.info(
                    "Orphan order %s (%s) already gone at broker (%s); marked "
                    "CANCELLED in local DB for trade %s",
                    broker_id, leg, result.reason, trade_id,
                )
                cancelled += 1
            elif _cancel_reason_being_processed(result.reason):
                # Mid-fill: may COMPLETE. Do NOT mark — let order_monitor
                # observe the real terminal state on its next poll.
                log.warning(
                    "check1: orphan %s order %s is being processed at broker "
                    "(may fill); leaving local status for order_monitor: %s",
                    leg, broker_id, result.reason,
                )
            else:
                log.error(
                    "check1: cancel orphaned %s order %s failed (left for retry, "
                    "local status unchanged): %s",
                    leg, broker_id, result.reason,
                )
        return cancelled

    def _mark_order_cancelled_local(self, broker_order_id: str, log) -> None:
        """
        FIX-186 (FIX 1): set a local orders row to CANCELLED. The terminal-status
        guard in the WHERE clause makes this a no-op if order_monitor already
        finalized the row (e.g. to COMPLETE), so a genuinely-filled order is
        never clobbered to CANCELLED.
        """
        try:
            with self._store.transaction() as cur:
                cur.execute(
                    "UPDATE orders SET status = 'CANCELLED', updated_at = ? "
                    "WHERE order_id = ? AND status NOT IN "
                    "('COMPLETE', 'CANCELLED', 'FAILED', 'EXPIRED')",
                    (self._now_ist(), broker_order_id),
                )
        except Exception as exc:
            log.error(
                "check1: failed to mark order %s CANCELLED in local DB: %s",
                broker_order_id, exc,
            )

    # ── CHECK 2: ORPHAN_ADOPTION ──────────────────────────────────────────────

    def _reset_human_orders_if_new_day(self) -> None:
        """FIX-182: clear the per-day human-order symbol set at IST date change."""
        today = now_ist().date()
        if self._human_order_date != today:
            self._human_order_symbols = set()
            self._oversell_flattened_symbols = set()   # Layer 3
            self._human_order_date = today

    def _check2_orphan_adoption(self, symbol: str, bp) -> ReconciliationAction:
        """
        Broker position exists but no local trade of ANY status tracks it (RC5b).

        FIX-182 (human-order policy): post-FIX-181, an in-flight fill (a broker
        position whose local trade is still PENDING/PENDING_FILL) is routed to
        _check2_inflight_orphan. So reaching here means the system has no record
        of this symbol at all — it is an operator-placed (human) order in Kite.
        Per Rama's decision the system manages only system trades: we do NOT
        adopt, protect, or flatten it. We log it once per symbol per day at INFO
        for the audit trail and add it to the human-order set (which widens the
        G3 capital-drift tolerance). Subsequent cycles are silent — no repeated
        ERROR spam and no repeated CapitalDriftDetected (the latter was already
        non-escalating, but it still produced per-cycle INFO from drift_handler).
        """
        self._reset_human_orders_if_new_day()

        if symbol in self._human_order_symbols:
            # Already detected today — stay quiet (COSMETIC, not persisted).
            return ReconciliationAction(
                check_name="ORPHAN_ADOPTION",
                tier="COSMETIC",
                symbol=symbol,
                trade_id=None,
                description=(
                    f"Human/untracked broker position {symbol} qty={bp.qty} "
                    f"already noted today; not managed by system"
                ),
                action_taken="none (human order; silenced after first detection)",
                success=True,
            )

        # LAYER 3 (RAMCOIND fix, 25-Jun): is this the system's OWN over-sell — a
        # duplicate exit leg that filled — rather than a human order? Layers 1+2 should
        # stop a duplicate ever filling, but if one ever does, a SYSTEM-created naked
        # position must NOT be silently disowned: flatten it (CRITICAL).
        if symbol not in self._oversell_flattened_symbols:
            oversell = self._detect_system_oversell(symbol, bp)
            if oversell is not None:
                self._oversell_flattened_symbols.add(symbol)
                return self._flatten_system_oversell(symbol, bp, oversell)

        # First detection today (genuine human / untracked order).
        self._human_order_symbols.add(symbol)
        self._log.info(
            "CHECK2 HUMAN_ORDER: symbol=%s broker_qty=%d avg_price=%.2f — no "
            "local trade; treating as operator/untracked order. System manages "
            "system trades only; not adopting or protecting. (logged once/day)",
            symbol, bp.qty, bp.avg_price,
        )
        # LAYER 3 (3b safety-net): a NAKED untracked position (no protective stop at
        # the broker) must never be silent — surface it once/day as a WARNING (FIX-182
        # still suppresses per-cycle spam: this fires once on first detection, like the
        # log line above). A human position WITH its own protection stays silent
        # (FIX-182 unchanged); and when the broker order source is unavailable we
        # cannot confirm naked, so we stay quiet (the conservative FIX-182 default).
        is_naked = self._position_is_naked(symbol, bp)
        if is_naked and self._notifier is not None:
            try:
                self._notifier.send(
                    severity="WARNING",
                    title=f"[{self._mode}] Naked untracked position — {symbol}",
                    body=(
                        f"Untracked/operator position {symbol} qty={bp.qty} "
                        f"avg={bp.avg_price:.2f} — no local trade and NO protective stop "
                        f"at the broker. Not auto-managed (operator policy); surfaced "
                        f"once today. If unexpected, check for a manual order or a "
                        f"system anomaly."
                    ),
                    source_module="order_reconciler",
                )
            except Exception as exc:  # noqa: BLE001
                self._log.error("CHECK2 orphan: notifier.send failed: %s", exc)

        return ReconciliationAction(
            check_name="ORPHAN_ADOPTION",
            tier="RECOVERABLE",
            symbol=symbol,
            trade_id=None,
            description=(
                f"Human/untracked broker position {symbol} qty={bp.qty} "
                f"avg_price={bp.avg_price:.2f}; no local trade — not managed by system"
                + (" [NAKED — alerted]" if is_naked else "")
            ),
            action_taken=(
                "logged once; added to human-order set; not adopted"
                + ("; WARNING (naked)" if is_naked else "")
            ),
            success=True,
        )

    # LAYER 3 (RAMCOIND fix, 25-Jun): how recently a closed system trade still
    # explains an over-sell, and how close the residual's avg must be to its exit.
    _OVERSELL_LOOKBACK_SEC = 300.0
    _OVERSELL_PRICE_TOL_PCT = 0.02

    def _detect_system_oversell(self, symbol: str, bp) -> Optional[dict]:
        """Return the recently-closed system trade if ``bp`` matches the SYSTEM
        over-sell signature (a duplicate exit leg that filled), else None.

        Signature (ALL must hold): a system trade on this symbol CLOSED within the
        last few minutes; the residual is OPPOSITE that trade's position (a LONG
        over-sell leaves a short, a SHORT over-buy leaves a long); |qty| ≤ the traded
        qty; and the residual's avg price ≈ the trade's exit price (the duplicate
        filled at the SL/exit). Unambiguous-only — anything short of every criterion
        is treated as a human order, not auto-flattened."""
        try:
            row = self._store.fetch_one(
                "SELECT trade_id, direction, qty_filled, exit_price, exit_time "
                "FROM trades WHERE symbol = ? AND status IN ('CLOSED','CLOSED_MANUAL') "
                "AND exit_time IS NOT NULL ORDER BY exit_time DESC LIMIT 1",
                (symbol,),
            )
        except Exception:
            return None
        if not row or not row["exit_time"]:
            return None
        try:
            closed_at = datetime.fromisoformat(str(row["exit_time"]))
            if (now_ist() - closed_at).total_seconds() > self._OVERSELL_LOOKBACK_SEC:
                return None
        except Exception:
            return None

        orphan_qty = int(getattr(bp, "qty", 0) or 0)
        if orphan_qty == 0:
            return None
        direction = str(row["direction"] or "").upper()
        # over-sell residual is OPPOSITE the trade's position.
        if direction == "LONG" and orphan_qty >= 0:
            return None
        if direction == "SHORT" and orphan_qty <= 0:
            return None
        traded = int(row["qty_filled"] or 0)
        if traded <= 0 or abs(orphan_qty) > traded:
            return None
        exit_price = float(row["exit_price"] or 0.0)
        avg = float(getattr(bp, "avg_price", 0.0) or 0.0)
        if exit_price > 0 and avg > 0:
            if abs(avg - exit_price) / exit_price > self._OVERSELL_PRICE_TOL_PCT:
                return None
        return dict(row)

    def _flatten_system_oversell(self, symbol: str, bp, oversell: dict) -> ReconciliationAction:
        """Flatten a SYSTEM over-sell residual with a covering MARKET order + CRITICAL
        alert. The system created this exposure (a duplicate exit leg filled), so —
        unlike a human order — it must not stay open."""
        log = bind_trade(self._log, trade_id=oversell.get("trade_id"))
        orphan_qty = int(getattr(bp, "qty", 0) or 0)
        cover_side = "BUY" if orphan_qty < 0 else "SELL"
        cover_qty = abs(orphan_qty)
        placed_ok, detail = False, ""
        try:
            placed = self._adapter.place_order(
                symbol=symbol, side=cover_side, qty=cover_qty, price=0.0,
                order_type="MARKET", intent="INTRADAY", tag="rc_oversell_flat",
            )
            placed_ok = True
            detail = (f"covering {cover_side} {cover_qty} MARKET "
                      f"(broker_order_id={getattr(placed, 'broker_order_id', '?')})")
        except Exception as exc:  # noqa: BLE001
            detail = f"cover order FAILED: {exc} — NAKED residual, MANUAL action required"
            log.error("CHECK2 SYSTEM_OVERSELL: flatten failed for %s: %s", symbol, exc)

        msg = (
            f"SYSTEM_OVERSELL: untracked {symbol} qty={orphan_qty} @ "
            f"{getattr(bp, 'avg_price', 0.0):.2f} matches a duplicate exit leg of trade "
            f"{oversell.get('trade_id')} (closed {oversell.get('exit_time')}); {detail}"
        )
        log.critical("CHECK2 %s", msg)
        if self._notifier is not None:
            try:
                self._notifier.send(
                    severity="CRITICAL",
                    title=f"[{self._mode}] SYSTEM OVER-SELL flattened — {symbol}",
                    body=msg, source_module="order_reconciler",
                )
            except Exception as exc:  # noqa: BLE001
                self._log.error("CHECK2 oversell: notifier.send failed: %s", exc)

        return ReconciliationAction(
            check_name="SYSTEM_OVERSELL",
            tier="CRITICAL",
            symbol=symbol,
            trade_id=oversell.get("trade_id"),
            description=msg,
            action_taken=detail,
            success=placed_ok,
        )

    def _position_is_naked(self, symbol: str, bp) -> bool:
        """True if the broker shows NO protective stop for this untracked position (a
        long needs a SELL stop, a short a BUY stop). Drives the Layer-3b 'never silent
        for a naked position' alert. Returns False when the broker order source is
        unavailable — we cannot confirm naked, so we stay quiet (the FIX-182 default,
        which keeps an operator's own protected manual orders silent)."""
        if self._broker_orders_fn is None:
            return False
        qty = int(getattr(bp, "qty", 0) or 0)
        if qty == 0:
            return False
        protect_side = "SELL" if qty > 0 else "BUY"
        try:
            for o in (self._broker_orders_fn() or []):
                if (str(o.get("symbol", "")) == symbol
                        and str(o.get("transaction_type", "")).upper() == protect_side
                        and float(o.get("trigger_price") or 0.0) > 0.0):
                    return False   # a protective stop exists → not naked
            return True
        except Exception:  # noqa: BLE001 — cannot confirm → stay quiet
            return False

    def _check2_inflight_orphan(self, symbol: str, bp, trade) -> ReconciliationAction:
        """
        FIX-181 (GICRE incident): a broker position whose only local record is an
        in-flight (PENDING_FILL/PENDING) trade — its entry LIMIT filled at the
        broker but the fill was not yet recorded locally.

        HARD_KILL active -> FLATTEN immediately. This is the abandonment race:
        the kill swept open trades, then a resting entry filled afterwards (or the
        entry was force-marked CANCELLED while it actually filled). The position
        must not survive the kill, so we flatten it here as a backstop to the
        kill_switch broker-position sweep.

        Otherwise -> the normal fill path (order_monitor) will transition this
        trade to OPEN within a cycle or two; we do NOT act destructively on a
        transient state. No scary CapitalDriftDetected for a known in-flight trade.
        """
        trade_id = trade["trade_id"]
        kill_active = False
        try:
            kill_active = self._ks is not None and self._ks.is_active("exit")
        except Exception:
            kill_active = False

        if kill_active:
            self._log.critical(
                "CHECK2 INFLIGHT_ORPHAN + HARD_KILL: %s qty=%d trade=%s — entry "
                "filled at broker during/after kill; FLATTENING",
                symbol, bp.qty, trade_id,
            )
            ok = self._flatten_broker_position(symbol, bp, "KILL", trade_id)
            return ReconciliationAction(
                check_name="INFLIGHT_ORPHAN_FLATTEN",
                tier="CRITICAL",
                symbol=symbol,
                trade_id=trade_id,
                description=(
                    f"In-flight {symbol} qty={bp.qty} filled at broker during "
                    f"HARD_KILL; flattened to avoid abandonment"
                ),
                action_taken=f"flatten_placed={ok}",
                success=ok,
            )

        self._log.warning(
            "CHECK2 INFLIGHT_ORPHAN: %s qty=%d trade=%s — entry filled at broker, "
            "awaiting local fill confirmation (no action; fill path will adopt)",
            symbol, bp.qty, trade_id,
        )
        return ReconciliationAction(
            check_name="INFLIGHT_ORPHAN",
            tier="COSMETIC",
            symbol=symbol,
            trade_id=trade_id,
            description=(
                f"Broker position {symbol} qty={bp.qty} matches in-flight trade "
                f"{trade_id} (status={trade['status']}); fill path will complete it"
            ),
            action_taken="none (transient in-flight fill)",
            success=True,
        )

    def _flatten_broker_position(
        self, symbol: str, bp, tag_prefix: str, trade_id: str = "orphan"
    ) -> bool:
        """
        FIX-181: flatten a broker position with a marketable LIMIT (LTP ± buffer,
        adapter snaps to tick) so it fills but caps slippage; MARKET fallback if
        no LTP. Returns True if an order was placed. Best-effort — never raises.
        """
        try:
            qty = abs(int(bp.qty))
            if qty == 0:
                return False
            # bp.qty > 0 = long position -> SELL to flatten; < 0 = short -> BUY.
            exit_side = "SELL" if bp.qty > 0 else "BUY"
            # Best-effort LTP via quote_fn (same source the reconciler already uses).
            # M-O1: quote_fn is adapter.get_quote — it takes BARE symbols and keys
            # its result by bare symbol (it prepends NSE: internally). Passing
            # "NSE:{symbol}" made it query NSE:NSE:SYM → miss → ltp None → raw
            # MARKET, defeating the FIX-181 cap. Mirror the bare-symbol idiom the
            # other quote sites use (:1329 / :2859).
            ltp = None
            try:
                raw = self._quote_fn([symbol])
                q = raw.get(symbol) if raw else None
                if q is not None:
                    ltp = float(getattr(q, "last_price", 0) or 0) or None
            except Exception:
                ltp = None

            if ltp and ltp > 0:
                price = marketable_limit_price(
                    exit_side, ltp, EMERGENCY_EXIT_BUFFER_PCT, DEFAULT_TICK
                )
                order_type = "LIMIT"
            else:
                price = 0.0
                order_type = "MARKET"

            from core.ids import truncate_tag_for_broker
            placed = self._adapter.place_order(
                symbol=symbol,
                side=exit_side,
                qty=qty,
                price=price,
                order_type=order_type,
                intent="INTRADAY",
                tag=truncate_tag_for_broker(f"{tag_prefix}_{trade_id}"),
            )
            return bool(getattr(placed, "broker_order_id", None))
        except Exception as exc:
            self._log.critical(
                "reconciler._flatten_broker_position failed for %s: %s",
                symbol, exc,
            )
            return False

    # ── CHECK 4: PARTIAL_CLOSE ────────────────────────────────────────────────

    def _check4_partial_close(self, trade, broker_qty: int) -> ReconciliationAction:
        """
        Local qty_filled > broker qty — partial position closure at broker (RC5d).

        FIX-148 (A3): Enhanced to handle partial RMS exits:
        1. Update qty_filled in DB
        2. Cancel old SL/TGT orders at broker (wrong qty)
        3. Telegram alert for partial close
        G5b on the next cycle will detect missing SL and place a fresh one
        at the correct (reduced) qty.
        """
        trade_id = trade["trade_id"]
        symbol = trade["symbol"]
        log = bind_trade(self._log, trade_id=trade_id)
        local_qty = trade["qty_filled"] or 0
        success = True
        steps: List[str] = []

        try:
            with self._store.transaction() as cur:
                cur.execute(
                    "UPDATE trades SET qty_filled = ?, updated_at = ? WHERE trade_id = ?",
                    (broker_qty, self._now_ist(), trade_id),
                )
            steps.append(f"qty_filled={local_qty}->{broker_qty}")
        except Exception as exc:
            log.error(
                "check4: update qty_filled failed for %s: %s", trade_id, exc
            )
            success = False
            steps.append(f"qty_update_FAILED: {exc}")

        # FIX-148: Cancel stale SL/TGT orders (they're sized for old qty).
        # G5b will detect no active SL on next cycle and place a fresh one
        # at broker_qty.
        cancelled = self._cancel_orphaned_orders_for_trade(trade_id, symbol, log)
        if cancelled > 0:
            steps.append(f"cancelled_{cancelled}_stale_orders")

        # FIX-148: Telegram alert for partial external close
        if self._notifier is not None:
            try:
                self._notifier.send(
                    severity="WARNING",
                    title=f"[{self._mode}] PARTIAL CLOSE -- {symbol}",
                    body=(
                        f"Partial external close detected\n"
                        f"Trade: {trade_id}\n"
                        f"Qty: {local_qty} -> {broker_qty}\n"
                        f"Old SL/TGT cancelled; G5b will re-place at new qty"
                    ),
                    source_module="order_reconciler",
                )
            except Exception as exc:
                log.error("check4: notifier.send failed: %s", exc)

        log.warning(
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
            action_taken="; ".join(steps) if steps else f"qty_filled updated to {broker_qty}",
            success=success,
        )

    # ── CHECK 5: POSITION_GREW ────────────────────────────────────────────────

    def _check5_position_grew(
        self, trade, broker_qty: int, local_qty: int
    ) -> ReconciliationAction:
        """
        Broker qty > local qty_filled — unexpected position growth (RC5e).

        Hard discrepancy. Publishes CapitalDriftDetected and logs at ERROR.

        FIX-038: ERROR logging uses exponential backoff to prevent alert spam.
        """
        trade_id = trade["trade_id"]
        symbol = trade["symbol"]
        log = bind_trade(self._log, trade_id=trade_id)

        # FIX-038: Check if we should alert (exponential backoff)
        should_alert = self._should_alert_for_discrepancy(trade_id, "POSITION_GREW")

        if should_alert:
            log.error(
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
                log.error("check5: publish CapitalDriftDetected failed: %s", exc)
        else:
            # Still log at DEBUG level even during backoff
            log.debug(
                "CHECK5 POSITION_GREW (backoff): trade_id=%s %s local_qty=%d broker_qty=%d",
                trade_id, symbol, local_qty, broker_qty,
            )

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

        FIX-B: Tracks orphan cycle count per trade_id. After 3 consecutive
        cycles where the order is still orphaned, marks trade FAILED, releases
        capital, and removes from counter. Resets counter if orphan resolves
        naturally (trade no longer PENDING_FILL or order found at broker).
        """
        actions: List[ReconciliationAction] = []
        broker_open = self._broker_orders_fn()
        broker_ids = {
            str(o.get("order_id", "")) for o in (broker_open or [])
        }

        pending = self._store.get_pending_all_products()
        pending_trade_ids = {trade["trade_id"] for trade in pending}

        # FIX-B: Reset counters for trades that are no longer PENDING_FILL
        resolved_ids = [tid for tid in self._orphan_cycle_count if tid not in pending_trade_ids]
        for tid in resolved_ids:
            del self._orphan_cycle_count[tid]
            self._log.info(
                "FIX-B: orphan_cycle_count cleared for %s (trade no longer PENDING_FILL)", tid
            )

        for trade in pending:
            trade_id = trade["trade_id"]
            bid = str(trade["broker_order_id"] or "")
            if bid and bid not in broker_ids:
                # FIX-B: Increment orphan cycle counter
                self._orphan_cycle_count[trade_id] = self._orphan_cycle_count.get(trade_id, 0) + 1
                cycle_count = self._orphan_cycle_count[trade_id]

                if cycle_count >= 3:
                    # FIX-B: Auto-close after 3 cycles
                    log = bind_trade(self._log, trade_id=trade_id)
                    log.warning(
                        "FIX-B ORPHAN_AUTO_CLOSE: trade_id=%s broker_order_id=%s "
                        "orphaned for %d cycles, marking FAILED and releasing capital",
                        trade_id, bid, cycle_count,
                    )

                    # Mark trade FAILED
                    try:
                        with self._store.transaction() as cur:
                            cur.execute(
                                "UPDATE trades SET status = ?, updated_at = ? WHERE trade_id = ?",
                                ("FAILED", self._now_ist(), trade_id)
                            )
                    except Exception as exc:
                        log.error("FIX-B: update trade status FAILED: %s", exc)

                    # Release capital
                    # Get reservation_id from trade row (EF-5: stored in trades table, not orders)
                    # FIX-B: get_pending_all_products now includes reservation_id
                    reservation_id = trade["reservation_id"]
                    if reservation_id:
                        try:
                            self._fm.release(reservation_id, f"orphan_auto_close_after_{cycle_count}_cycles")
                            log.info("FIX-B: capital released for reservation_id=%s", reservation_id)
                        except Exception as exc:
                            log.error("FIX-B: capital release failed: %s", exc)
                    else:
                        log.warning("FIX-B: no reservation_id found in trade %s", trade_id)

                    # Remove from counter
                    del self._orphan_cycle_count[trade_id]

                    actions.append(ReconciliationAction(
                        check_name="ORPHAN_ORDER",
                        tier="RECOVERABLE",
                        symbol=trade["symbol"],
                        trade_id=trade_id,
                        description=(
                            f"PENDING_FILL order {bid} orphaned for {cycle_count} cycles, "
                            f"auto-closed as FAILED for {trade['symbol']}"
                        ),
                        action_taken=f"marked_FAILED; capital_released; counter_cleared",
                        success=True,
                    ))
                else:
                    # FIX-B: Still counting cycles
                    self._log.warning(
                        "CHECK6 ORPHAN_ORDER: trade_id=%s broker_order_id=%s "
                        "not found in broker open orders (cycle %d/3)",
                        trade_id, bid, cycle_count,
                    )
                    actions.append(ReconciliationAction(
                        check_name="ORPHAN_ORDER",
                        tier="UNRECOVERABLE",
                        symbol=trade["symbol"],
                        trade_id=trade_id,
                        description=(
                            f"PENDING_FILL order {bid} not found in "
                            f"broker open orders for {trade['symbol']} (cycle {cycle_count}/3)"
                        ),
                        action_taken=f"logged; will auto-close after 3 cycles",
                        success=True,
                    ))
            else:
                # FIX-B: Order found at broker or no broker_order_id yet - reset counter if present
                if trade_id in self._orphan_cycle_count:
                    del self._orphan_cycle_count[trade_id]
                    self._log.info(
                        "FIX-B: orphan_cycle_count cleared for %s (order now found at broker)", trade_id
                    )
        return actions

    # ── CHECK 9: MISSING_EXITS (FIX-002) ─────────────────────────────────────

    def _check9_missing_exits(
        self, local_trades: list
    ) -> List[ReconciliationAction]:
        """
        FIX-002: For each OPEN local trade, verify the local SL order's
        broker_order_id appears in broker's open-order list.

        Distinct from G5b (which checks for a missing LOCAL SL record).
        This check catches: local DB has an SL row but the broker order was
        silently cancelled/expired — a naked position with no protective leg.

        On detection: CRITICAL log + soft_kill(). Does not auto-place a new
        SL order (that is G5b's job on the next cycle once soft_kill clears).

        Only runs when broker_orders_fn is available (same guard as CHECK 6).
        """
        actions: List[ReconciliationAction] = []

        broker_open = self._broker_orders_fn()
        broker_order_ids = {
            str(o.get("order_id", "")) for o in (broker_open or [])
            if o.get("order_id")
        }

        for trade in local_trades:
            trade_id = trade["trade_id"]
            symbol = trade["symbol"]

            sl_row = self._store.get_sl_order_for_trade(trade_id)
            if sl_row is None:
                # G5b handles this (no local SL record at all)
                continue

            broker_sl_id = str(sl_row["order_id"] if sl_row["order_id"] else "")
            if not broker_sl_id:
                # SL row exists but has no broker ID yet (just placed this cycle) — skip
                continue

            if broker_sl_id in broker_order_ids:
                # SL order is live on the broker — healthy; stamp OK.
                try:
                    self._store.update_order_reconciliation_status(broker_sl_id, "OK")
                except Exception as exc:  # noqa: BLE001
                    self._log.debug("check9: update_order_reconciliation_status OK failed: %s", exc)
                continue

            # FIX-155b: If an exit order (SL/TGT/EOD) already completed for
            # this trade, the position is closing — not a naked position.
            # Prevents false positive when SL fills but order_monitor hasn't
            # yet closed the trade in DB.
            completed_exit = self._store.fetch_one(
                "SELECT COUNT(*) AS n FROM orders "
                "WHERE trade_id = ? AND leg IN ('SL', 'TGT', 'EOD') "
                "AND status = 'COMPLETE'",
                (trade_id,),
            )
            if completed_exit and int(completed_exit["n"]) > 0:
                self._log.info(
                    "check9: trade %s has COMPLETE exit order — "
                    "closing in progress, skipping naked-position check",
                    trade_id,
                )
                continue

            # FIX-157: Re-check current trade status. Earlier checks in this
            # cycle (e.g., CHECK 1) may have closed the trade as CLOSED_MANUAL.
            # CLOSED_MANUAL closes directly without completing SL/TGT orders,
            # so FIX-155b's COMPLETE-exit check above misses them.
            current_trade = self._store.fetch_one(
                "SELECT status FROM trades WHERE trade_id = ?",
                (trade_id,),
            )
            if current_trade and current_trade["status"] not in ("OPEN", "PARTIAL"):
                self._log.info(
                    "check9: trade %s status=%s (no longer open) — "
                    "skipping naked-position check",
                    trade_id, current_trade["status"],
                )
                continue

            # RACE-AWARE CONFIRM (01-Jul-2026, FACET 1): the SL's absence from broker
            # open-orders is AMBIGUOUS — it may have just FILLED (the SL-fill race:
            # position closing, NOT naked) rather than vanished. The local-COMPLETE guard
            # (FIX-155b) above lags the broker (order_monitor's 2s fill poll), which is why
            # the 1-Jul BANSALWIRE stop-loss was mis-read as naked → false SOFT_KILL. Confirm
            # against BROKER TRUTH (SL fill status + live position) before declaring naked.
            if not self._confirm_genuinely_naked(broker_sl_id, symbol, self._log):
                continue

            # Naked position: local SL record exists but broker has no matching order.
            # FIX-129 (Item 26): stamp SL_MISSING before alerting.
            try:
                self._store.update_order_reconciliation_status(broker_sl_id, "SL_MISSING")
            except Exception as exc:  # noqa: BLE001
                self._log.debug("check9: update_order_reconciliation_status SL_MISSING failed: %s", exc)

            log = bind_trade(self._log, trade_id=trade_id)

            # FIX-038: MISSING_EXITS always alerts (bypass backoff per spec)
            should_alert = self._should_alert_for_discrepancy(
                trade_id, "MISSING_EXITS", bypass_backoff=True
            )

            if should_alert:
                log.critical(
                    "CHECK9 MISSING_EXITS: trade_id=%s symbol=%s sl_order_id=%s "
                    "found in local DB but NOT in broker open orders — naked position",
                    trade_id, symbol, broker_sl_id,
                )

                # FIX-155: Guard against cascade — skip if emergency exit
                # already pending for this trade (paper-mode timing race).
                pending_exit = self._store.fetch_one(
                    "SELECT COUNT(*) AS n FROM orders "
                    "WHERE trade_id = ? AND leg = 'EOD' AND order_type = 'MARKET' "
                    "AND status IN ('PENDING', 'SUBMITTED', 'OPEN')",
                    (trade_id,),
                )
                if pending_exit and int(pending_exit["n"]) > 0:
                    log.info(
                        "check9: emergency exit already pending for trade_id=%s, "
                        "skipping duplicate placement",
                        trade_id,
                    )
                    emergency_result = "skipped(already_pending)"
                else:
                    emergency_result = self._emergency_market_close(trade, log)

                try:
                    self._ks.soft_kill(
                        reason=(
                            f"MISSING_EXITS: naked position {symbol} "
                            f"trade_id={trade_id} sl_order={broker_sl_id}"
                        ),
                        triggered_by="order_reconciler",
                    )
                except Exception as exc:
                    log.error("check9: soft_kill failed: %s", exc)

                # FIX-148: Telegram CRITICAL alert
                if self._notifier is not None:
                    try:
                        self._notifier.send(
                            severity="CRITICAL",
                            title=f"[{self._mode}] NAKED POSITION -- {symbol}",
                            body=(
                                f"SL order {broker_sl_id} missing from broker\n"
                                f"Trade: {trade_id}\n"
                                f"Emergency exit: {emergency_result}"
                            ),
                            source_module="order_reconciler",
                        )
                    except Exception as exc:
                        log.error("check9: notifier.send failed: %s", exc)

            action_desc = "CRITICAL logged; soft_kill triggered; reconciliation_status=SL_MISSING"
            if should_alert:
                action_desc += f"; emergency_exit={emergency_result}"

            actions.append(ReconciliationAction(
                check_name="MISSING_EXITS",
                tier="UNRECOVERABLE",
                symbol=symbol,
                trade_id=trade_id,
                description=(
                    f"OPEN trade {trade_id} has SL order {broker_sl_id} "
                    f"in local DB but NOT found in broker open orders"
                ),
                action_taken=action_desc,
                success=True,
            ))

        return actions

    def _confirm_genuinely_naked(self, broker_sl_id: str, symbol: str, log) -> bool:
        """Race-aware broker-truth re-check before declaring a naked position (01-Jul-2026).

        The SL's absence from the broker's OPEN-order list is AMBIGUOUS: it may have just
        FILLED (the common SL-fill race — the position is closing normally, NOT naked) or
        genuinely VANISHED (cancelled/rejected — potentially a naked position). Only a LIVE
        broker query disambiguates; the LOCAL order status LAGS the broker fill
        (order_monitor's 2s poll), which is exactly why CHECK9's local FIX-155b
        COMPLETE-check missed the 1-Jul BANSALWIRE stop-loss and false-flagged it naked →
        spurious SOFT_KILL + a would-be emergency sell (which, with the tag fix, would now
        oversell into a short).

        Returns True (genuinely naked → act) ONLY if the SL did NOT fill AND the broker
        still shows an open position. Returns False (NOT naked → skip: no emergency exit,
        no soft_kill) if the SL FILLED (status COMPLETE) OR the position is FLAT.

        Fail-safe: if BOTH broker queries fail (broker-blind), conservatively returns True
        — a false CRITICAL alert is safer than a missed naked position, and FACET 2's live
        re-check in the emergency exit still prevents any oversell. Parity: same adapter
        methods in paper + live.
        """
        saw_truth = False
        # (1) Did the SL actually FILL? absent-from-open because COMPLETE = the race.
        try:
            hist = self._adapter.get_order_history(broker_sl_id)
            if hist:
                saw_truth = True
                last = str(getattr(hist[-1], "status", "")).upper()
                if last == "COMPLETE":
                    log.info("check9: SL %s is COMPLETE (filled) at broker — position closing "
                             "normally, NOT naked (SL-fill race)", broker_sl_id)
                    return False
        except Exception as exc:  # noqa: BLE001
            log.warning("check9: get_order_history(%s) failed during naked-confirm: %s",
                        broker_sl_id, exc)
        # (2) Is the position actually still open at the broker? naked REQUIRES a position.
        try:
            held = 0
            for _p in (self._adapter.get_positions() or []):
                if _p.symbol == symbol:
                    held = int(_p.qty)
                    break
            saw_truth = True
            if held == 0:
                log.info("check9: broker position for %s is FLAT — NOT naked (position already "
                         "closed)", symbol)
                return False
        except Exception as exc:  # noqa: BLE001
            log.warning("check9: get_positions() failed during naked-confirm for %s: %s",
                        symbol, exc)
        if not saw_truth:
            log.warning("check9: could not confirm broker truth for %s (both queries failed) "
                        "— conservatively treating as naked", symbol)
        # SL not confirmed-filled AND position not confirmed-flat (or broker-blind) -> naked.
        return True

    def _emergency_market_close(self, trade, log) -> str:
        """
        FIX-148 (GAP 4): Place emergency MARKET exit for a naked position.

        Best-effort: if placement fails, returns failure reason (reconciler
        soft_kill is the backstop). Does NOT release capital — that happens
        when the market order fills via normal _handle_exit_fill path, or
        on the next CHECK 1 cycle if the position disappears.
        """
        trade_id = trade["trade_id"]
        symbol = trade["symbol"]
        try:
            direction = trade["direction"] or "LONG"
        except (KeyError, IndexError):
            direction = "LONG"
        qty = trade["qty_filled"] or 0
        try:
            product = trade["product"] or "MIS"
        except (KeyError, IndexError):
            product = "MIS"
        intent = _PRODUCT_TO_INTENT.get(product or "", "INTRADAY")

        if qty <= 0:
            return "skipped(zero_qty)"

        side = "SELL" if direction == "LONG" else "BUY"

        # FACET 2 (01-Jul-2026, OVERSELL GUARD): re-check the LIVE broker position right
        # before selling. The naked flag can race with a just-filled SL (the position may
        # already be flat); now that the tag fix lets this exit actually place, selling the
        # tracked qty into a flat/reduced book would OVERSELL into an unintended short. Sell
        # only what is genuinely held; skip if flat or unconfirmable (never sell what isn't
        # there). Parity: uses the same adapter.get_positions() in paper + live.
        try:
            live_held = 0
            for _p in (self._adapter.get_positions() or []):
                if _p.symbol == symbol:
                    live_held = abs(int(_p.qty))
                    break
        except Exception as exc:  # noqa: BLE001
            log.error("check9: get_positions() failed in emergency exit for %s (%s) — cannot "
                      "confirm held qty; SKIPPING sell to avoid oversell", symbol, exc)
            return "skipped(position_unconfirmed)"
        if live_held <= 0:
            log.info("check9: broker position for %s already FLAT — skipping emergency sell "
                     "(no oversell)", symbol)
            return "skipped(already_flat)"
        if live_held < qty:
            log.warning("check9: broker holds %d %s but tracked qty=%d — selling only the held "
                        "qty (no oversell)", live_held, symbol, qty)
        qty = min(qty, live_held)   # never sell more than is actually held at the broker

        # FIX (01-Jul-2026): truncate the tag — a full trade_id ('trd_'+32hex = 36 chars)
        # exceeds Zerodha's 20-char tag limit and REJECTED this emergency exit (the naked-
        # position last line of defense; 1-Jul BANSALWIRE). Mirrors the entry-leg pattern
        # (_flatten_broker_position :1680). The adapter boundary now also guards this, so
        # this is belt-and-suspenders — but keep it so the persisted order tag matches too.
        from core.ids import truncate_tag_for_broker
        try:
            placed = self._adapter.place_order(
                symbol=symbol,
                side=side,
                qty=qty,
                price=0.0,
                order_type="MARKET",
                intent=intent,
                tag=truncate_tag_for_broker(trade_id),
            )
            log.critical(
                "check9: EMERGENCY MARKET EXIT placed for %s %s qty=%d "
                "broker_order_id=%s",
                symbol, side, qty, placed.broker_order_id,
            )

            # Persist the emergency order in DB
            try:
                self._order_mgr.insert_order(
                    trade_id=trade_id,
                    broker_order_id=placed.broker_order_id,
                    leg="EOD",
                    transaction_type=side,
                    order_type="MARKET",
                    product=product,
                    variety="regular",
                    qty_requested=qty,
                    price=0.0,
                )
            except Exception as exc:
                log.error("check9: emergency order DB persist failed: %s", exc)

            return f"placed({placed.broker_order_id})"
        except Exception as exc:
            log.critical(
                "check9: EMERGENCY MARKET EXIT FAILED for %s: %s", symbol, exc,
            )
            return f"failed({exc})"

    # ── G5b: CRASH_RECOVERY_SL ───────────────────────────────────────────────

    def _g5b_crash_recovery_sl(
        self, trade, broker_positions: Optional[dict] = None
    ) -> Optional[ReconciliationAction]:
        """
        OPEN/PARTIAL trade has no active SL order — place a fresh recovery order
        per G5b logic (RC7).

        LONG:  LTP > sl_initial -> SL SELL (stop-limit) at sl_initial
               LTP <= sl_initial -> MARKET SELL (SL already breached)
        SHORT: LTP < sl_initial -> SL BUY  (stop-limit) at sl_initial
               LTP >= sl_initial -> MARKET BUY  (SL already breached)
        P0 (2026-06-15): stop-limit (SL), never SL-M (Zerodha API rejects SL-M).

        Order is placed via adapter.place_order() (RC18 — no order_placer import)
        and persisted via OrderManager.insert_order().

        FIX-186 (FIX 3): if a fresh broker-position snapshot is provided and it
        shows NO live position for this symbol (absent or qty==0), skip placement.
        Otherwise a recovery SL placed for a position that is simultaneously being
        manually closed is immediately orphaned by CHECK1 the next cycle (the
        17-Jun IRFC race). Fail-safe: when the snapshot is unavailable (None) we
        proceed, since leaving a genuinely open position unprotected is worse.
        """
        trade_id = trade["trade_id"]
        symbol = trade["symbol"]
        log = bind_trade(self._log, trade_id=trade_id)
        direction = trade["direction"]   # "LONG" | "SHORT"
        sl_price = trade["sl_initial"]
        qty = trade["qty_filled"] or 0
        product = trade["product"]
        intent = _PRODUCT_TO_INTENT.get(product or "", "INTRADAY")

        if qty <= 0 or sl_price is None or float(sl_price) <= 0:
            return None

        # FIX-190 (Bug F): do NOT place a recovery SL if a non-terminal SL order
        # already exists for this trade. The 19-Jun incident placed a DUPLICATE
        # SL (G5b alongside the live LIMIT_TRIPLE SL) because the trade looked
        # "unprotected" after its TGT failed while its SL was actually PENDING.
        # One SL authority per trade — only G5b-place when there is genuinely none.
        try:
            existing_sl = self._store.fetch_one(
                "SELECT COUNT(*) AS n FROM orders WHERE trade_id = ? AND leg = 'SL' "
                "AND status NOT IN ('CANCELLED','FAILED','EXPIRED','COMPLETE')",
                (trade_id,),
            )
            if existing_sl and int(existing_sl["n"]) > 0:
                log.info(
                    "G5b skip recovery-SL for %s — a non-terminal SL order already "
                    "exists for this trade (FIX-190 Bug F)", symbol,
                )
                return None
        except Exception as exc:
            log.warning(
                "G5b: existing-SL check failed for %s: %s; proceeding", symbol, exc
            )

        # LAYER 1 (RAMCOIND fix, 25-Jun): the FIX-190 Bug F guard above reads the
        # LOCAL orders table, which order_monitor.track populates ~40ms AFTER the
        # broker placement. G5b fell into that TOCTOU window on 25-Jun (its guard
        # checked 12ms before the SL row landed) and placed a DUPLICATE SL. Two
        # additional guards that do NOT depend on the lagging table close the race:
        #
        # (a) SETTLING WINDOW — recovery has no business firing while the normal exit
        #     placement is still in flight. Deterministic + mode-agnostic.
        fill_age = self._entry_fill_age_seconds(trade_id)
        if fill_age is not None and fill_age < _G5B_SETTLING_WINDOW_SEC:
            log.info(
                "G5b skip recovery-SL for %s — entry filled %.1fs ago (< %.0fs settling "
                "window); LIMIT_TRIPLE exits are still being placed (Layer 1)",
                symbol, fill_age, _G5B_SETTLING_WINDOW_SEC,
            )
            return None

        # (b) AUTHORITATIVE check — the broker reflects an SL the instant it is placed
        #     (unlike the ~40ms-lagging local table). If a live SL already exists there
        #     (or in order_placer's in-memory _fill_map when the broker order source is
        #     unwired), skip. Race-proof belt-and-suspenders behind the settling window.
        exit_side = "SELL" if direction == "LONG" else "BUY"
        if self._already_has_live_sl(trade_id, symbol, exit_side):
            log.info(
                "G5b skip recovery-SL for %s — a live SL already exists (authoritative "
                "broker/_fill_map check, Layer 1)", symbol,
            )
            return None

        # FIX-186 (FIX 3): skip recovery SL when the broker positively reports no
        # live position for this symbol (manual close likely in progress).
        if broker_positions is not None:
            bp = broker_positions.get(symbol)
            if bp is None or abs(int(getattr(bp, "qty", 0) or 0)) == 0:
                log.info(
                    "G5b skipped recovery-SL for %s — no broker position found "
                    "(likely manual close in progress)",
                    symbol,
                )
                return None

        sl_price = float(sl_price)

        log.warning(
            "G5b CRASH_RECOVERY_SL: trade_id=%s %s %s "
            "has no active SL order — placing recovery order",
            trade_id, symbol, direction,
        )

        # Fetch LTP via injected quote_fn
        try:
            quotes = self._quote_fn([symbol])
            ltp = quotes[symbol].last_price if symbol in quotes else None
        except Exception as exc:
            log.error("G5b: quote_fn failed for %s: %s", symbol, exc)
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
            log.warning(
                "G5b: no LTP available for %s; cannot place recovery SL", symbol
            )
            return None

        # Determine order side, type, trigger and limit based on G5b rules.
        # P0 (2026-06-15): SL recovery uses order_type="SL" (stop-limit), never
        # SL-M (Zerodha rejects SL-M via API). limit_price is offset past the
        # trigger; MARKET breach-exits keep price/trigger = 0.0.
        limit_price = 0.0
        if direction == "LONG":
            side = "SELL"
            if ltp > sl_price:
                order_type = "SL"
                trigger = sl_price
                limit_price = calc_sl_limit_price(side, sl_price, DEFAULT_SL_LIMIT_OFFSET_PCT)
                desc = (
                    f"LONG {symbol}: LTP={ltp} > sl={sl_price} "
                    f"-> placing SL SELL trig={sl_price} limit={limit_price}"
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
                order_type = "SL"
                trigger = sl_price
                limit_price = calc_sl_limit_price(side, sl_price, DEFAULT_SL_LIMIT_OFFSET_PCT)
                desc = (
                    f"SHORT {symbol}: LTP={ltp} < sl={sl_price} "
                    f"-> placing SL BUY trig={sl_price} limit={limit_price}"
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
                price=limit_price,
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
                price=limit_price,
                trigger_price=trigger,
            )
            log.warning(
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
            log.error(
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
            log.error("G5b: place_order auth error for trade_id=%s: %s", trade_id, exc)
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
            log.error(
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

    # ── LAYER 1 (RAMCOIND 25-Jun): G5b duplicate-SL race guards ─────────────────

    def _entry_fill_age_seconds(self, trade_id: str) -> Optional[float]:
        """Seconds since this trade's ENTRY filled (from the persisted entry_time),
        or None if unavailable. Drives the G5b settling window."""
        try:
            row = self._store.fetch_one(
                "SELECT entry_time FROM trades WHERE trade_id = ?", (trade_id,)
            )
        except Exception:
            return None
        if not row or not row["entry_time"]:
            return None
        try:
            filled = datetime.fromisoformat(str(row["entry_time"]))
            return (now_ist() - filled).total_seconds()
        except Exception:
            return None

    def _already_has_live_sl(self, trade_id: str, symbol: str, exit_side: str) -> bool:
        """Authoritative 'does a live SL already exist for this trade?'.

        Primary source = the BROKER open-order book (get_open_orders returns only
        OPEN / TRIGGER PENDING orders, so anything returned is live; an SL is an
        exit-side order with trigger_price > 0 — get_open_orders carries no
        order_type, and a TGT LIMIT has trigger 0). This reflects an SL the instant
        it is placed, unlike the ~40ms-lagging local table. Falls back to
        order_placer's in-memory _fill_map ONLY when the broker source is unwired
        (e.g. tests / broker_orders_fn=None)."""
        if self._broker_orders_fn is not None:
            try:
                for o in (self._broker_orders_fn() or []):
                    if (str(o.get("symbol", "")) == symbol
                            and str(o.get("transaction_type", "")).upper() == exit_side
                            and float(o.get("trigger_price") or 0.0) > 0.0):
                        return True
                return False
            except Exception as exc:  # noqa: BLE001 — degrade to the fallback
                self._log.debug(
                    "G5b: broker-orders SL check failed for %s: %s", symbol, exc
                )
        # Fallback: order_placer._fill_map (populated at placement, ahead of the
        # local-table write). Used only when the broker source is unavailable.
        op = self._order_placer
        if op is not None and hasattr(op, "_fill_map"):
            try:
                lock = getattr(op, "_fill_map_lock", None)
                if lock is not None:
                    with lock:
                        entries = list(op._fill_map.values())
                else:
                    entries = list(op._fill_map.values())
                for e in entries:
                    if (getattr(e, "trade_id", None) == trade_id
                            and str(getattr(e, "leg", "")).upper() == "SL"):
                        return True
            except Exception as exc:  # noqa: BLE001
                self._log.debug(
                    "G5b: _fill_map SL check failed for %s: %s", trade_id, exc
                )
        return False

    # ── LAYER 2 (RAMCOIND 25-Jun): DUPLICATE-EXIT invariant (the keystone) ──────
    # Statuses that mean an order leg is no longer live at the broker.
    _LIVE_EXIT_EXCLUDE = ("CANCELLED", "COMPLETE", "REJECTED", "FAILED", "EXPIRED")

    def _check_duplicate_exits(self, local_trades: list) -> List[ReconciliationAction]:
        """
        Enforce EXACTLY ONE live exit leg of each kind per OPEN/PARTIAL trade.

        Root incident (RAMCOIND 25-Jun): G5b crash-recovery placed a 2nd SL OUTSIDE
        the LIMIT_TRIPLE software OCO (a ~40ms TOCTOU race against the lagging local
        orders table). Both SLs shared the trigger, so on the stop-hit BOTH filled —
        +1 closed the long and the duplicate over-sold into a -1 naked short. This
        invariant runs every cycle and cancels the duplicate (the RAMCOIND dup would
        have been cancelled ~12 min before its 10:12:49 stop-hit), so the over-sell
        can never occur regardless of HOW the duplicate arose — the always-on net
        behind the Layer-1 G5b guard.

        Scope: the SL invariant applies to LIMIT_TRIPLE trades only — a CO_PLUS_TGT
        trade carries its SL inside the broker CO bracket and has ZERO local SL legs
        by design (detected here via its CO entry order, variety='co'). The TGT
        invariant applies to both protocols (each places a standalone LIMIT TGT). We
        only ever act on a DUPLICATE (>1 live legs); a MISSING leg is CHECK9 / G5b's
        job, so a CO trade (0 local SL legs) is never touched.
        """
        actions: List[ReconciliationAction] = []
        for trade in local_trades:
            try:
                orders = self._order_mgr.get_orders_for_trade(trade["trade_id"])
            except Exception as exc:
                self._log.error(
                    "dup_exits: order lookup failed for %s: %s", trade["trade_id"], exc
                )
                continue
            # CO trades carry the SL in the broker CO bracket (no local SL leg) — never
            # dedupe their SL (N1). The CO entry order is the only one with variety='co'.
            is_co = any(str(o.get("variety") or "").lower() == "co" for o in orders)
            if not is_co:
                actions.extend(self._dedupe_exit_leg(trade, orders, "SL"))
            actions.extend(self._dedupe_exit_leg(trade, orders, "TGT"))
        return actions

    def _dedupe_exit_leg(
        self, trade, orders: list, leg: str
    ) -> List[ReconciliationAction]:
        """Cancel duplicate live ``leg`` orders for one trade, keeping the canonical
        (earliest-placed) leg. Returns [] when there is nothing to do (0 or 1 live
        legs). Cancel-safety: only the LATER duplicate(s) are cancelled, exactly one
        leg is kept, and the kept count is re-verified so the trade is NEVER left
        naked (a concurrent fill of the canonical during the pass → re-place the SL +
        CRITICAL)."""
        live_legs = [
            o for o in orders
            if o.get("leg") == leg
            and str(o.get("status") or "").upper() not in self._LIVE_EXIT_EXCLUDE
        ]
        if len(live_legs) <= 1:
            return []   # 0 or 1 live — nothing to dedupe (a MISSING leg is CHECK9/G5b).

        trade_id = trade["trade_id"]
        symbol = trade["symbol"]
        log = bind_trade(self._log, trade_id=trade_id)

        # get_orders_for_trade is ORDER BY placed_at → live_legs[0] is the canonical
        # leg: placed on the entry fill, before any later G5b/retry duplicate; it owns
        # the software OCO and is the leg smart_tgt/breakeven trail in place. Keep it.
        canonical = live_legs[0]
        canonical_id = canonical.get("order_id")
        cancelled: List[str] = []
        for extra in live_legs[1:]:
            broker_id = extra.get("order_id")
            if not broker_id or broker_id == canonical_id:
                continue
            try:
                result = self._adapter.cancel_order(broker_id)
            except Exception as exc:
                log.warning(
                    "dup_exits: cancel duplicate %s %s raised: %s", leg, broker_id, exc
                )
                continue
            if getattr(result, "success", False) or _cancel_reason_already_gone(
                getattr(result, "reason", "")
            ):
                # _mark_order_cancelled_local is terminal-guarded → a no-op if the
                # duplicate already FILLED (its over-sell, if any, is then handled by
                # CHECK2 SYSTEM_OVERSELL / Layer 3), so a filled order is never
                # clobbered to CANCELLED.
                self._mark_order_cancelled_local(broker_id, log)
                cancelled.append(str(broker_id))

        if not cancelled:
            return []   # all extras already terminal — nothing actually changed.

        # Cancel-safety (N5): re-verify ≥1 live leg of this kind remains. The canonical
        # was never targeted, so normally it survives; only a concurrent fill/cancel of
        # the canonical during this pass could zero it out.
        tier, severity, replaced = "RECOVERABLE", "WARNING", ""
        try:
            remaining = self._store.fetch_all(
                "SELECT order_id FROM orders WHERE trade_id = ? AND leg = ? "
                "AND status NOT IN ('CANCELLED','COMPLETE','REJECTED','FAILED','EXPIRED')",
                (trade_id, leg),
            )
        except Exception:
            remaining = None
        if remaining is not None and len(remaining) == 0:
            cur = self._store.fetch_one(
                "SELECT status FROM trades WHERE trade_id = ?", (trade_id,)
            )
            still_open = bool(cur and cur["status"] in ("OPEN", "PARTIAL"))
            if leg == "SL" and still_open:
                # The canonical SL raced to terminal during the cancel pass → the
                # position is momentarily naked. Re-place protective SL NOW via the
                # (Layer-1-guarded) G5b path rather than wait a cycle. CRITICAL.
                tier = severity = "CRITICAL"
                try:
                    self._g5b_crash_recovery_sl(trade)
                    replaced = " | re-placed protective SL (canonical raced to fill)"
                except Exception as exc:  # noqa: BLE001
                    replaced = f" | re-place FAILED ({exc}) — NAKED, manual action"
            # A zero-remain TGT is not naked (the SL still protects) → stays WARNING.

        msg = (
            f"DUPLICATE_{leg}: {symbol} had {len(live_legs)} live {leg} legs — "
            f"cancelled {len(cancelled)} duplicate(s) {cancelled}, kept canonical "
            f"{canonical_id}{replaced}"
        )
        log.warning("dup_exits: %s", msg)
        if self._notifier is not None:
            try:
                self._notifier.send(
                    severity=severity,
                    title=f"[{self._mode}] Duplicate {leg} cancelled — {symbol}",
                    body=msg,
                    source_module="order_reconciler",
                )
            except Exception as exc:  # noqa: BLE001
                self._log.error("dup_exits: notifier.send failed: %s", exc)

        return [ReconciliationAction(
            check_name=f"DUPLICATE_{leg}",
            tier=tier,
            symbol=symbol,
            trade_id=trade_id,
            description=(
                f"{len(live_legs)} live {leg} legs (duplicate exit) — kept canonical "
                f"{canonical_id}, cancelled {cancelled}"
            ),
            action_taken=f"cancelled duplicate {leg} {cancelled}{replaced}",
            success=True,
        )]

    # ── G3: CAPITAL_DRIFT ─────────────────────────────────────────────────────

    def _refresh_unrealized_mtm(self) -> None:
        """B-1: recompute per-position unrealized MTM from (open trades × current LTP)
        and publish it to the FundManager for the pre-trade daily-loss gate.

        SET-BASED: updates every OPEN/PARTIAL trade and PRUNES any MTM entry no longer
        open, so a trade closed by ANY path (SL/TGT/manual/EOD) drops out with no
        per-close-path hook and no stale entry can wrongly inflate the daily loss.

        Parity: `quote_fn` = get_quote returns a REAL LTP in both paper (via the paper
        quote provider) and live — ONE code path, no `if paper` branch. The compute
        `(ltp − avg_fill) × qty × sign` is mode-agnostic.

        ADVISORY ONLY: never touches reservations / _total / the 3-balance invariant.
        On a quote outage → mark the MTM UNAVAILABLE so the gate degrades to
        realized-only + WARN (never block-all, never fabricate, never silent)."""
        try:
            open_trades = self._store.get_all_open_trades() or []
        except Exception as exc:  # noqa: BLE001
            self._log.error("mtm_refresh: get_all_open_trades failed: %s", exc)
            self._fm.mark_unrealized_mtm_refreshed(available=False)
            self._mtm_refresh_failure += 1
            return

        # Prune FIRST so a just-closed trade never lingers even if the quote call fails.
        open_ids = {t["trade_id"] for t in open_trades}
        try:
            self._fm.prune_unrealized_mtm(open_ids)
        except Exception as exc:  # noqa: BLE001
            self._log.error("mtm_refresh: prune failed: %s", exc)

        if not open_trades:
            # Nothing open → MTM is trivially 0 and fresh.
            self._fm.mark_unrealized_mtm_refreshed(available=True)
            self._mtm_refresh_success += 1
            return

        symbols = sorted({t["symbol"] for t in open_trades if t["symbol"]})
        try:
            quotes = self._quote_fn(symbols)   # dict: symbol -> Quote(last_price=...)
        except Exception as exc:  # noqa: BLE001
            # Outage → keep last-known values but mark UNAVAILABLE → gate degrades.
            self._log.warning("mtm_refresh: quote_fn failed (%s); MTM marked unavailable", exc)
            self._fm.mark_unrealized_mtm_refreshed(available=False)
            self._mtm_refresh_failure += 1
            return

        updated = 0
        for t in open_trades:
            q = quotes.get(t["symbol"]) if quotes else None
            ltp = float(getattr(q, "last_price", 0.0) or 0.0) if q is not None else 0.0
            avg = float(t["entry_actual_price"] or 0.0)
            qty = int(t["qty_filled"] or 0)
            if ltp <= 0 or avg <= 0 or qty <= 0:
                continue   # missing quote / not-yet-filled → skip (leave last-known)
            sign = 1.0 if (t["direction"] or "").upper() == "LONG" else -1.0
            self._fm.update_unrealized_mtm(t["trade_id"], (ltp - avg) * qty * sign)
            updated += 1

        if updated == 0:
            # Positions open but no usable quote for any → treat as unavailable so the
            # gate does not rely on a map that looks "fresh" but is empty/stale.
            self._fm.mark_unrealized_mtm_refreshed(available=False)
            self._mtm_refresh_failure += 1
            self._log.warning("mtm_refresh: no usable quotes for %d open position(s)", len(open_trades))
        else:
            self._fm.mark_unrealized_mtm_refreshed(available=True)
            self._mtm_refresh_success += 1
            self._log.debug(
                "mtm_refresh: %d/%d positions updated (success=%d failure=%d)",
                updated, len(open_trades), self._mtm_refresh_success, self._mtm_refresh_failure,
            )

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

        # FIX-189 (P1-B): suppress the overnight false positive. Outside the
        # trading session the Zerodha funds endpoint returns net=0.0 (pre-auth /
        # post-settlement); against a real local total that reads as a
        # catastrophic drift and fired a false CRITICAL "Capital Drift" alert at
        # 04:24 while the service was (wrongly) running overnight. Gate ONLY this
        # exact pattern — broker net is exactly 0.0, real capital is expected, and
        # we are outside market hours — so genuine in-session drift still alerts
        # and paper mode (which never reports net=0.0 overnight) is unaffected.
        now = now_ist()
        broker_margin_reliable = is_market_day(now) and is_within_market_hours(
            now.time(), _MARGIN_RELIABLE_OPEN, _MARGIN_RELIABLE_CLOSE
        )
        if (
            actual == 0.0
            and expected > self._cfg.capital_drift_tolerance
            and not broker_margin_reliable
        ):
            self._log.info(
                "G3 CAPITAL_DRIFT skipped: broker net=0.0 outside market hours "
                "(expected=%.2f) — unreliable overnight/pre-auth margin read",
                expected,
            )
            return None

        # FIX-182: human / untracked orders block broker margin the FM does not
        # know about, so broker net legitimately differs from local total. When
        # any human order was detected today, widen the tolerance by the
        # configured human-order margin allowance so this expected drift does
        # not raise CRITICAL capital-drift alerts. Genuine catastrophic drift
        # (beyond the allowance) still alerts.
        effective_tolerance = self._cfg.capital_drift_tolerance
        # FIX-190 (Bug I): in-session, broker margin legitimately drops by the
        # deployed capital, so the tight Rs tolerance fires constantly (the 10:00
        # Δ503 noise). During market hours widen to max(Rs, expected*pct). pct=0
        # disables (back-compat). Outside hours the Rs tolerance still applies.
        drift_pct = getattr(self._cfg, "capital_drift_tolerance_pct", 0.0) or 0.0
        if drift_pct > 0 and broker_margin_reliable:
            effective_tolerance = max(effective_tolerance, abs(expected) * drift_pct)
        if self._human_order_symbols:
            effective_tolerance += self._human_order_margin_tolerance

        if delta <= effective_tolerance:
            # TASK-11: drift back within tolerance — reset the throttle so the
            # next genuine drift alerts immediately instead of waiting out the
            # 30-min window. (Was: FIX-038 _mark_discrepancy_resolved.)
            self._last_capital_drift_alert_poll = None
            # Per-episode logging: the episode is over, so the next drift starts
            # a fresh (logged) episode.
            self._drift_episode_active = False
            return None

        # Per-episode logging: first cycle of a new drift episode?
        is_new_episode = not self._drift_episode_active
        self._drift_episode_active = True

        # TASK-11: throttle repeat alerts to one per capital_drift_alert_interval_sec
        # (default 30 min) instead of the FIX-038 exponential backoff.
        should_alert = self._should_alert_capital_drift()

        if should_alert:
            self._log.error(
                "G3 CAPITAL_DRIFT: expected=%.2f actual=%.2f delta=%.2f "
                "tolerance=%.2f (base=%.2f human_orders=%s)",
                expected, actual, delta, effective_tolerance,
                self._cfg.capital_drift_tolerance,
                sorted(self._human_order_symbols) or "none",
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
                    title=f"[{self._mode}] ⚠️ Capital Drift Detected",
                    body=(
                        f"Broker: ₹{float(actual):,.2f} | "
                        f"Local: ₹{float(expected):,.2f}\n"
                        f"Delta: ₹{float(delta):,.2f} "
                        f"(tolerance: ₹{float(effective_tolerance):,.2f})"
                    ),
                    source_module="order_reconciler",
                    context={
                        "expected": expected,
                        "actual": actual,
                        "delta": delta,
                        "tolerance": effective_tolerance,
                        "base_tolerance": self._cfg.capital_drift_tolerance,
                        "human_orders": sorted(self._human_order_symbols),
                    },
                )
            except Exception as exc:
                self._log.error("G3: TelegramNotifier.send failed: %s", exc)

            action_taken = "CapitalDriftDetected published; CRITICAL alert sent"
        else:
            # Still log at DEBUG every cycle (forensics) even when suppressed.
            self._log.debug(
                "G3 CAPITAL_DRIFT (suppressed/throttled): expected=%.2f actual=%.2f delta=%.2f",
                expected, actual, delta,
            )
            action_taken = "drift detected but alert suppressed (throttled)"

        # Per-episode logging: persist a reconciliation_log row only when a NEW
        # episode begins OR an alert actually fired — NOT on every suppressed
        # cycle. (Pre-fix: 144 rows for 3 alerts on 19-Jun.) The per-cycle DEBUG
        # line above still records every cycle for forensics; the alert throttle
        # is unchanged.
        if not (is_new_episode or should_alert):
            return None

        return ReconciliationAction(
            check_name="CAPITAL_DRIFT",
            tier="UNRECOVERABLE",
            symbol="",
            trade_id=None,
            description=(
                f"Broker capital={actual:.2f} vs local={expected:.2f} "
                f"delta={delta:.2f} exceeds tolerance={effective_tolerance:.2f}"
            ),
            action_taken=action_taken,
            success=True,
        )

    # ── CHECK 7: CAPITAL_ACCOUNTING_DRIFT (BL-3) ──────────────────────────────

    def _check7_capital_accounting_drift(self) -> List[ReconciliationAction]:
        """
        BL-3: verify FundManager._reservations matches the signed sum of
        fm_ledger margin_delta rows for each live reservation.

        Iterates fund_manager.get_live_reservations() (snapshot copy) and for
        each rid compares _Reservation.margin against
        StateStore.sum_fm_ledger_margin_delta(rid). If |delta| exceeds
        cfg.capital_drift_tolerance, publishes one CapitalDriftDetected per
        drifting rid with source_module="fund_manager_self_check" and
        emits one ReconciliationAction.

        Direction: unidirectional (fm -> ledger) only. Orphan detection
        (rids in ledger but not in fm._reservations) is deferred to Phase E.

        delta = fm_margin - ledger_sum (signed; CapitalDriftHandler abs()es it).

        Per-reservation reporting (not aggregated): each drifting rid gets
        its own event + action so ops can grep the trail by reservation_id.
        """
        actions: List[ReconciliationAction] = []
        try:
            live = self._fm.get_live_reservations()
        except Exception as exc:
            self._log.error(
                "_check7_capital_accounting_drift: get_live_reservations failed: %s",
                exc, exc_info=True,
            )
            return actions

        tolerance = self._cfg.capital_drift_tolerance

        for rid, res in live.items():
            try:
                ledger_sum = self._store.sum_fm_ledger_margin_delta(rid)
            except Exception as exc:
                self._log.error(
                    "_check7: sum_fm_ledger_margin_delta(%s) failed: %s",
                    rid, exc, exc_info=True,
                )
                continue

            fm_margin = res.margin
            delta = fm_margin - ledger_sum
            if abs(delta) <= tolerance:
                continue

            self._log.error(
                "BL-3 CAPITAL_ACCOUNTING_DRIFT rid=%s symbol=%s "
                "fm_margin=%.2f ledger_sum=%.2f delta=%.2f tolerance=%.2f",
                rid, res.symbol, fm_margin, ledger_sum, delta, tolerance,
            )

            try:
                self._bus.publish(CapitalDriftDetected(
                    source_module="fund_manager_self_check",
                    expected=fm_margin,
                    actual=ledger_sum,
                    delta=delta,
                ))
            except Exception as exc:
                self._log.error(
                    "_check7: publish CapitalDriftDetected failed for rid=%s: %s",
                    rid, exc,
                )

            actions.append(ReconciliationAction(
                check_name="CAPITAL_ACCOUNTING_DRIFT",
                tier="UNRECOVERABLE",
                symbol=res.symbol,
                trade_id=None,
                description=(
                    f"rid={rid} symbol={res.symbol} fm_margin={fm_margin:.2f} "
                    f"ledger_sum={ledger_sum:.2f} delta={delta:.2f} "
                    f"exceeds tolerance={tolerance:.2f}"
                ),
                action_taken=(
                    "CapitalDriftDetected published "
                    "(source=fund_manager_self_check)"
                ),
                success=True,
            ))

        return actions

    # ── CHECK 8: CO_SL_DRIFT (M-2) ────────────────────────────────────────────

    def _check8_co_sl_drift(self) -> List[ReconciliationAction]:
        """
        M-2: Compare locally-tracked CO SL trigger (smart_tgt_state.current_sl)
        against the broker's live CO trigger_price for each SmartTgtManager
        tracked trade.

        Alert-only by design. On drift:
          - Log CRITICAL with grep tag ``CO_SL_DRIFT_DETECTED`` (alert_watcher
            relays to Telegram).
          - Return ``ReconciliationAction(check_name="CO_SL_DRIFT",
            tier="RECOVERABLE", action_taken="alert_only")``.

        MUST NOT call ``adapter.modify_order`` in this check. Auto-repair is
        deferred to Phase F or later, pending paper-trial data on how often
        genuine drift occurs and how it manifests (broker-side re-hoist vs
        local state lag). First cut observes only.

        Skips entirely when there are no smart_tgt_state rows (the common case
        outside a live session). Broker errors propagate to the caller so the
        standard RC11/RC12 policy handles them.
        """
        actions: List[ReconciliationAction] = []

        tracked_states = self._store.get_all_smart_tgt_states()
        if not tracked_states:
            return actions

        broker_open = self._adapter.get_open_orders()
        broker_triggers = {
            str(o.get("order_id", "")): float(o.get("trigger_price", 0.0))
            for o in (broker_open or [])
            if o.get("order_id")
        }

        tolerance_rs = 0.01   # 1 paise; CO trigger is a price, not a rupee sum

        for row in tracked_states:
            trade_id = row["trade_id"]
            symbol = row["symbol"]
            local_sl = float(row["current_sl"])
            log = bind_trade(self._log, trade_id=trade_id)

            co_row = self._store.get_co_entry_order_for_trade(trade_id)
            if co_row is None:
                continue   # not a CO trade, or already closed; not _check8's concern
            co_order_id = str(co_row["order_id"] or "")
            if not co_order_id:
                continue

            broker_trigger = broker_triggers.get(co_order_id)
            if broker_trigger is None:
                # CO not in broker's open-orders list; CHECK 6 handles orphans.
                continue

            delta = broker_trigger - local_sl
            if abs(delta) <= tolerance_rs:
                continue

            log.critical(
                "CO_SL_DRIFT_DETECTED trade_id=%s symbol=%s "
                "local_sl=%.4f broker_trigger=%.4f delta=%.4f",
                trade_id, symbol, local_sl, broker_trigger, delta,
            )
            actions.append(ReconciliationAction(
                check_name="CO_SL_DRIFT",
                tier="RECOVERABLE",
                symbol=symbol,
                trade_id=trade_id,
                description=(
                    f"CO SL trigger drift: local_sl={local_sl:.4f} "
                    f"broker_trigger={broker_trigger:.4f} "
                    f"delta={delta:.4f} tolerance={tolerance_rs:.4f}"
                ),
                action_taken="alert_only",
                success=True,
            ))

        return actions

    # ── A-1/E-1: UNIFIED in-flight-entry RECOVERY (naked-orphan root-cause fix) ──
    # Replaces the old FIX-068 _check_unknown_in_flight (timeout-only, blind-FAILED
    # after 3 polls, keyed on a broker_order_id a timed-out entry never learned).
    # Runs as a PREPASS at the top of the cycle (see _reconcile) so an adopted-OPEN
    # trade is protected by G5b + the TGT retry within the SAME cycle. BOTH feeds —
    # the timeout UNKNOWN_IN_FLIGHT queue AND the crash orphaned-PENDING set — flow
    # through the ONE _adopt_or_fail path: adopt-and-protect a broker-confirmed
    # entry, or FAILED-and-release ONLY on broker-confirmed absence (never blind).

    def _recover_in_flight_entries(self) -> List[ReconciliationAction]:
        """A-1/E-1 unified recovery prepass. Correlate every in-flight-entry
        recovery trade back to its broker ENTRY by tag, then adopt (protect) or
        FAILED (release) on evidence. Never raises — a recovery failure must not
        stop the cycle's own safety checks. Gated: no broker call when there is
        nothing to recover."""
        actions: List[ReconciliationAction] = []

        try:
            timeout_ids = (
                self._order_placer.get_timeout_recovery_trades()
                if self._order_placer is not None else []
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error("recovery: get_timeout_recovery_trades failed: %s", exc)
            timeout_ids = []
        try:
            crash_rows = self._store.get_orphaned_pending_trades()
        except Exception as exc:  # noqa: BLE001
            self._log.error("recovery: get_orphaned_pending_trades failed: %s", exc)
            crash_rows = []

        # Unified, de-duplicated work list: trade_id -> feed source. (A trade
        # cannot be in both feeds — timeout sets UNKNOWN_IN_FLIGHT, crash stays
        # PENDING — but dedupe defensively so it can never be adopted twice.)
        work: dict = {}
        for tid in (timeout_ids or []):
            work[tid] = "timeout"
        for r in (crash_rows or []):
            work.setdefault(r["trade_id"], "crash")
        if not work:
            return actions   # nothing to recover -> no broker call

        # ONE broker orderbook read for the whole recovery set. Unreachable ->
        # DEFER the ENTIRE set (never FAILED / release on a blind poll).
        try:
            all_orders = self._adapter.get_all_orders()
        except (BrokerTimeoutError, BrokerAuthError) as exc:
            self._log.warning(
                "recovery: get_all_orders unreachable (%s); deferring %d recovery "
                "trade(s) — no FAILED, no release", type(exc).__name__, len(work),
            )
            return actions
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "recovery: get_all_orders failed: %s; deferring recovery set", exc
            )
            return actions

        hard_kill = False
        try:
            hard_kill = self._ks is not None and self._ks.is_active("exit")
        except Exception:  # noqa: BLE001
            hard_kill = False

        for trade_id, source in work.items():
            try:
                trade_row = self._store.fetch_one(
                    "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
                )
            except Exception as exc:  # noqa: BLE001
                self._log.error("recovery: fetch trade %s failed: %s", trade_id, exc)
                continue
            if trade_row is None:
                self._forget_recovery_trade(trade_id, source)
                continue
            # Defensive: only act on a genuine recovery state (another path may
            # have resolved it between the feed read and now).
            if (trade_row["status"] or "") not in _RECOVERY_STATES:
                self._forget_recovery_trade(trade_id, source)
                continue
            try:
                act = self._adopt_or_fail(
                    trade_row, all_orders, source=source, hard_kill=hard_kill
                )
            except Exception as exc:  # noqa: BLE001
                self._log.error(
                    "recovery: _adopt_or_fail failed for %s: %s",
                    trade_id, exc, exc_info=True,
                )
                act = None
            if act is not None:
                actions.append(act)
        return actions

    def _forget_recovery_trade(self, trade_id: str, source: str) -> None:
        """Drop a resolved trade from the in-memory recovery bookkeeping. The
        crash feed (get_orphaned_pending_trades) self-clears once the ENTRY row is
        backfilled or the trade leaves PENDING; only the timeout queue + poll
        counter need explicit cleanup."""
        if source == "timeout" and self._order_placer is not None:
            try:
                self._order_placer.remove_from_timeout_recovery(trade_id)
            except Exception as exc:  # noqa: BLE001
                self._log.debug("recovery: remove_from_timeout_recovery failed: %s", exc)
        self._timeout_poll_counts.pop(trade_id, None)

    def _adopt_or_fail(
        self, trade_row, all_orders, *, source: str, hard_kill: bool
    ) -> Optional[ReconciliationAction]:
        """THE single recovery decision: correlate the trade's ENTRY at the broker
        by tag, then route to adopt-and-protect / defer / FAILED-and-release.
        Objectives: never call a system order human; never release before
        broker-absence is confirmed; never leave a filled position unmanaged;
        never adopt twice."""
        trade_id = trade_row["trade_id"]
        symbol = trade_row["symbol"] or ""
        direction = trade_row["direction"] or ""
        qty = int(trade_row["qty_planned"] or 0)
        log = bind_trade(self._log, trade_id=trade_id)

        kind, order = correlate_entry_by_tag(
            trade_id, direction, symbol, qty, all_orders
        )

        if kind == "AMBIGUOUS":
            return self._recovery_ambiguous(trade_row, log)
        if kind == "ABSENT":
            return self._recovery_absent(trade_row, source, log)

        # MATCH: decide on the broker ENTRY order's TERMINAL-ity + fill.
        status = (order.get("status") or "").upper()
        filled = int(order.get("filled_quantity") or 0)
        if status in _RECOVERY_TERMINAL_STATUSES:
            if filled > 0:
                # A real position — even a partial-then-CANCEL — must be adopted
                # and protected, NEVER disowned as human (the naked-orphan bug).
                return self._recovery_adopt_filled(
                    trade_row, order, source, log, hard_kill
                )
            # Terminal with zero fill: it genuinely did not fill -> safe FAILED.
            return self._recovery_fail_dead(trade_row, source, status, log)
        # Non-terminal (still RESTING at the broker, possibly partially filled):
        # keep capital correct + defer; re-correlated next cycle so the FINAL qty
        # is adopted once it settles (COMPLETE / CANCELLED-with-partial), avoiding
        # an SL placed for a qty that then grows.
        return self._recovery_defer_resting(trade_row, order, log)

    def _recovery_absent(
        self, trade_row, source: str, log
    ) -> Optional[ReconciliationAction]:
        """No entry-side order carries our tag. Broker is reachable (get_all_orders
        succeeded) but confirm over a small poll budget (order-propagation window)
        before FAILED — then release capital (the ONLY evidence-based release)."""
        trade_id = trade_row["trade_id"]
        symbol = trade_row["symbol"] or ""
        n = self._timeout_poll_counts.get(trade_id, 0) + 1
        self._timeout_poll_counts[trade_id] = n
        if n < _RECOVERY_ABSENCE_POLL_BUDGET:
            log.info(
                "recovery: %s ABSENT at broker (poll %d/%d) — deferring FAILED",
                trade_id, n, _RECOVERY_ABSENCE_POLL_BUDGET,
            )
            return ReconciliationAction(
                check_name="RECOVERY_ABSENT_POLLING", tier="COSMETIC",
                symbol=symbol, trade_id=trade_id,
                description=f"Entry absent at broker (poll {n}/{_RECOVERY_ABSENCE_POLL_BUDGET})",
                action_taken="defer", success=True,
            )
        # Confirmed absent -> FAILED (atomic guard) + release (crash-aware).
        if not self._store.fail_recovery_trade(trade_id):
            log.info("recovery: %s already resolved before FAILED — skip release", trade_id)
            self._forget_recovery_trade(trade_id, source)
            return None
        try:
            self._fm.release_adopted_reservation(
                trade_row, reason=f"recovery_absent_after_{n}_polls"
            )
        except Exception as exc:  # noqa: BLE001
            log.error("recovery: release_adopted_reservation failed for %s: %s", trade_id, exc)
        self._forget_recovery_trade(trade_id, source)
        log.critical(
            "recovery: %s confirmed ABSENT at broker after %d polls -> FAILED + "
            "capital released", trade_id, n,
        )
        return ReconciliationAction(
            check_name="RECOVERY_ABSENT_FAILED", tier="UNRECOVERABLE",
            symbol=symbol, trade_id=trade_id,
            description=f"Entry not at broker after {n} polls -> FAILED",
            action_taken="marked_failed released_capital", success=True,
        )

    def _recovery_fail_dead(
        self, trade_row, source: str, status: str, log
    ) -> Optional[ReconciliationAction]:
        """The correlated ENTRY is REJECTED / CANCELLED at the broker — it
        genuinely did not fill. FAILED + release (safe; positive evidence)."""
        trade_id = trade_row["trade_id"]
        symbol = trade_row["symbol"] or ""
        if not self._store.fail_recovery_trade(trade_id):
            self._forget_recovery_trade(trade_id, source)
            return None
        try:
            self._fm.release_adopted_reservation(
                trade_row, reason=f"recovery_entry_{status.lower()}"
            )
        except Exception as exc:  # noqa: BLE001
            log.error("recovery: release_adopted_reservation failed for %s: %s", trade_id, exc)
        self._forget_recovery_trade(trade_id, source)
        log.warning(
            "recovery: %s ENTRY %s at broker -> FAILED + capital released",
            trade_id, status,
        )
        return ReconciliationAction(
            check_name="RECOVERY_ENTRY_DEAD", tier="RECOVERABLE",
            symbol=symbol, trade_id=trade_id,
            description=f"Entry {status} at broker -> FAILED",
            action_taken="marked_failed released_capital", success=True,
        )

    def _recovery_defer_resting(
        self, trade_row, order, log
    ) -> Optional[ReconciliationAction]:
        """The correlated ENTRY is still RESTING at the broker (not filled). Keep
        its RESERVE present in memory (crash lost it) so available capital is not
        over-counted, then defer — a later cycle re-correlates it (fill -> adopt,
        cancel/reject -> FAILED). No entry-row backfill, so the crash feed re-scans
        it; no timeout-queue removal, so the timeout feed re-polls it."""
        trade_id = trade_row["trade_id"]
        symbol = trade_row["symbol"] or ""
        try:
            self._fm.restore_adopted_reservation(trade_row)
        except Exception as exc:  # noqa: BLE001
            log.error("recovery: restore_adopted_reservation failed for %s: %s", trade_id, exc)
        # It IS at the broker -> reset the absence poll budget.
        self._timeout_poll_counts.pop(trade_id, None)
        log.info(
            "recovery: %s ENTRY resting at broker (status=%s) — reserve ensured, "
            "deferring; will adopt on fill", trade_id, order.get("status"),
        )
        return ReconciliationAction(
            check_name="RECOVERY_ENTRY_RESTING", tier="COSMETIC",
            symbol=symbol, trade_id=trade_id,
            description=f"Entry resting at broker (status={order.get('status')}); deferring",
            action_taken="reserve_ensured defer", success=True,
        )

    def _recovery_adopt_filled(
        self, trade_row, order, source: str, log, hard_kill: bool
    ) -> Optional[ReconciliationAction]:
        """THE naked-orphan fix: the correlated ENTRY is FILLED (COMPLETE) at the
        broker — a real position. Adopt it and PROTECT it (never disown as human,
        never leave naked), or FLATTEN it under an active HARD_KILL."""
        trade_id = trade_row["trade_id"]
        symbol = trade_row["symbol"] or ""
        filled_qty = int(order.get("filled_quantity") or order.get("quantity") or 0)
        avg_price = float(order.get("average_price") or 0.0)
        broker_order_id = str(order.get("order_id") or "")
        product = ((order.get("product") or "").upper()) or "MIS"
        # entry_time proxy = trade.created_at (tz-aware IST, guaranteed older than
        # the G5b settling window since it precedes placement) so the same-cycle
        # G5b recovery-SL is NOT wrongly deferred by a fresh timestamp.
        filled_at = (trade_row["created_at"] or now_ist().isoformat())

        if filled_qty <= 0:
            # COMPLETE but zero filled qty is contradictory — defer, don't act.
            log.warning("recovery: %s COMPLETE but filled_qty<=0; deferring", trade_id)
            return None

        if hard_kill:
            return self._recovery_flatten_under_kill(
                trade_row, source, filled_qty, avg_price, filled_at,
                product, broker_order_id, log,
            )

        # 1) Atomic adopt: recovery-state -> OPEN + backfill the fill fields in ONE
        #    transaction (exactly-once; a second cycle sees a non-recovery state).
        if not self._store.adopt_recovery_trade_to_open(
            trade_id, avg_fill_price=avg_price, qty_filled=filled_qty,
            filled_at=filled_at,
        ):
            log.info("recovery: %s already adopted by another cycle — no-op", trade_id)
            self._forget_recovery_trade(trade_id, source)
            return None

        # 2) Capital: crash-aware commit (restore the reserve if a crash lost it,
        #    then reserved -> used). Exactly ONE commit per trade.
        try:
            self._fm.commit_adopted_entry(trade_row, avg_price, filled_qty)
        except Exception as exc:  # noqa: BLE001
            # commit_to_used already fired hard_kill (BL-4). The position is
            # adopted + OPEN, so G5b still protects it this cycle; surface loudly.
            log.critical("recovery: commit_adopted_entry failed for %s: %s", trade_id, exc)

        # 3) Backfill the ENTRY orders row (traceability + product for G5b/reports).
        self._backfill_entry_order_row(
            trade_id, broker_order_id, trade_row, filled_qty, avg_price, product, log
        )

        # 4) Owe a TGT -> flag TGTRetryManager. The SL comes from G5b THIS cycle
        #    (recovery runs as a prepass, before the G5b loop).
        try:
            self._store.mark_needs_tgt_retry(trade_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("recovery: mark_needs_tgt_retry failed for %s: %s", trade_id, exc)

        self._forget_recovery_trade(trade_id, source)
        log.critical(
            "recovery: ADOPTED filled entry %s qty=%d avg=%.2f broker_order_id=%s "
            "-> OPEN; G5b places the SL this cycle, TGT retry flagged (was heading "
            "for FAILED -> naked)", trade_id, filled_qty, avg_price, broker_order_id,
        )
        self._alert_recovery_adopted(trade_row, symbol, filled_qty, avg_price)
        return ReconciliationAction(
            check_name="RECOVERY_ADOPTED_FILLED", tier="RECOVERABLE",
            symbol=symbol, trade_id=trade_id,
            description=(
                f"Adopted broker-filled entry {symbol} qty={filled_qty} "
                f"avg={avg_price:.2f} (source={source}) -> OPEN + protected"
            ),
            action_taken="adopted_open committed_capital tgt_retry_flagged", success=True,
        )

    def _recovery_flatten_under_kill(
        self, trade_row, source: str, filled_qty: int, avg_price: float,
        filled_at: str, product: str, broker_order_id: str, log,
    ) -> Optional[ReconciliationAction]:
        """HARD_KILL + matched FILLED entry: FLATTEN, never resume. Commit capital
        (reserved -> used, so the ledger reflects the position we truly hold), flip
        straight to EXITING (excludes G5b/CHECK1 -> no protective SL races the
        flatten), backfill the entry row, then place the oversell-guarded emergency
        market close. Finalized (release_used) by _check_stuck_exiting -> CHECK1."""
        trade_id = trade_row["trade_id"]
        symbol = trade_row["symbol"] or ""
        if not self._store.mark_recovery_trade_exiting(
            trade_id, avg_fill_price=avg_price, qty_filled=filled_qty,
            filled_at=filled_at,
        ):
            log.info("recovery(kill): %s already resolved — no-op", trade_id)
            self._forget_recovery_trade(trade_id, source)
            return None
        try:
            self._fm.commit_adopted_entry(trade_row, avg_price, filled_qty)
        except Exception as exc:  # noqa: BLE001
            log.critical("recovery(kill): commit_adopted_entry failed for %s: %s", trade_id, exc)
        self._backfill_entry_order_row(
            trade_id, broker_order_id, trade_row, filled_qty, avg_price, product, log
        )
        # Re-fetch so the emergency close reads the backfilled EXITING row
        # (qty_filled / direction).
        fresh = self._store.fetch_one(
            "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
        )
        result = self._emergency_market_close(fresh if fresh is not None else trade_row, log)
        self._forget_recovery_trade(trade_id, source)
        log.critical(
            "recovery: HARD_KILL matched filled entry %s -> EXITING + emergency "
            "flatten (%s)", trade_id, result,
        )
        return ReconciliationAction(
            check_name="RECOVERY_KILL_FLATTEN", tier="CRITICAL",
            symbol=symbol, trade_id=trade_id,
            description=(
                f"HARD_KILL: matched filled entry {symbol} qty={filled_qty} "
                f"flattened (never adopted into a kill)"
            ),
            action_taken=f"exiting flatten={result}", success=True,
        )

    def _recovery_ambiguous(
        self, trade_row, log
    ) -> Optional[ReconciliationAction]:
        """>1 entry-side order shares our tag even after symbol+qty narrowing — a
        real 48-bit tag collision (astronomically rare). NEVER blind-adopt (could
        mis-own another trade's order) and NEVER release (the entry may be live).
        Defer + CRITICAL alert for manual resolution — capital stays reserved
        (conservative) and the trade is not disowned as human."""
        trade_id = trade_row["trade_id"]
        symbol = trade_row["symbol"] or ""
        log.critical(
            "recovery: AMBIGUOUS tag collision for %s (%s) — >1 broker entry shares "
            "the tag; NOT auto-adopting or releasing. MANUAL review required.",
            trade_id, symbol,
        )
        if self._notifier is not None:
            try:
                self._notifier.send(
                    severity="CRITICAL",
                    title=f"[{self._mode}] Recovery AMBIGUOUS — {symbol}",
                    body=(
                        f"In-flight-entry recovery for {trade_id} ({symbol}) found >1 "
                        f"broker order sharing its tag (a rare tag collision). The "
                        f"system will NOT auto-adopt or release capital — manual "
                        f"resolution required. Capital stays reserved; the trade is "
                        f"held in recovery."
                    ),
                    source_module="order_reconciler",
                )
            except Exception as exc:  # noqa: BLE001
                log.error("recovery: AMBIGUOUS notifier.send failed: %s", exc)
        return ReconciliationAction(
            check_name="RECOVERY_AMBIGUOUS", tier="CRITICAL",
            symbol=symbol, trade_id=trade_id,
            description=f"Tag collision for {trade_id}; manual review (no adopt, no release)",
            action_taken="deferred alerted", success=True,
        )

    def _backfill_entry_order_row(
        self, trade_id: str, broker_order_id: str, trade_row, filled_qty: int,
        avg_price: float, product: str, log,
    ) -> None:
        """Restore the missing ENTRY orders row (the local record the timeout/crash
        never persisted) keyed by the matched broker_order_id, marked COMPLETE.
        Idempotent — skips if an ENTRY row already exists for this trade."""
        if not broker_order_id:
            return
        try:
            for o in (self._store.get_orders_for_trade(trade_id) or []):
                if (o["leg"] or "").upper() == "ENTRY":
                    return   # already present -> idempotent no-op
        except Exception:  # noqa: BLE001
            pass
        direction = trade_row["direction"] or "LONG"
        side = "BUY" if direction == "LONG" else "SELL"
        try:
            self._order_mgr.insert_order(
                trade_id=trade_id, broker_order_id=broker_order_id, leg="ENTRY",
                transaction_type=side, order_type="LIMIT", product=product,
                variety="regular",
                qty_requested=int(trade_row["qty_planned"] or filled_qty),
                price=float(trade_row["entry_target_price"] or avg_price),
            )
            # Reflect the true broker state (COMPLETE) so CHECK6/reports don't see
            # a phantom PENDING entry.
            self._order_mgr.update_order_status(
                broker_order_id, "COMPLETE", qty_filled=filled_qty,
                avg_fill_price=avg_price,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("recovery: backfill ENTRY orders row failed for %s: %s", trade_id, exc)

    def _alert_recovery_adopted(
        self, trade_row, symbol: str, filled_qty: int, avg_price: float
    ) -> None:
        """WARNING alert: an adopted, now-protected filled entry. Surfaced once (the
        adoption is a one-shot transition) so the operator knows a timed-out/crashed
        entry was recovered rather than lost."""
        if self._notifier is None:
            return
        try:
            self._notifier.send(
                severity="WARNING",
                title=f"[{self._mode}] Recovered in-flight entry — {symbol}",
                body=(
                    f"A timed-out/crashed ENTRY for {symbol} ({trade_row['trade_id']}) "
                    f"was found FILLED at the broker (qty={filled_qty} avg={avg_price:.2f}) "
                    f"and ADOPTED -> OPEN + protected (SL via recovery, TGT retry). "
                    f"Previously this became a naked orphan."
                ),
                source_module="order_reconciler",
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error("recovery: adopted notifier.send failed: %s", exc)
