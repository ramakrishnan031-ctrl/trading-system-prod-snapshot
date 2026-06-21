"""
scripts/preflight/checks/state.py -- Group 5 (State integrity), Phase A subset.

These are RAW DB reads (no StateStore => no migration side-effect), ALERT-ONLY:
  * kill_switch_state            -- INACTIVE? prior-day (app auto-clears at startup)
                                    vs same-day (halted = CRITICAL)
  * open_positions_at_start      -- MIS should be flat overnight
  * open_orders_at_start         -- resting/orphan broker orders pre-market
  * stuck_exiting_positions      -- trades wedged in EXITING
  * needs_tgt_retry_residue      -- TGT placements still owed

The IN-MEMORY state checks (entry throttle / reservation counter / stuck-signal
nudge) live in Phase B and are alert-only (Fork 2: a separate process can't reset
the running app's memory; the app self-heals them at startup/intraday). The
orphan-order CANCEL fix belongs with the broker group (Group 4, needs the adapter).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional, Tuple

from scripts.preflight.base import Check, CheckContext, CheckResult, Criticality

ACTIVE_TRADE_STATUSES = ("OPEN", "PARTIAL")
ACTIVE_ORDER_STATUSES = ("PENDING", "SUBMITTED", "OPEN", "PARTIAL",
                         "TRIGGER_PENDING", "TRIGGER PENDING")


def _ph(seq) -> str:
    return ",".join("?" * len(seq))


def _safe_count(db_path: Path, sql: str, params=()) -> Tuple[Optional[int], str]:
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            return int(conn.execute(sql, params).fetchone()[0]), ""
        finally:
            conn.close()
    except Exception as exc:
        return None, str(exc)


def _kill_state(db_path: Path) -> Tuple[str, Optional[str], str]:
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT state, triggered_at FROM kill_switch_state WHERE id = 1"
            ).fetchone()
            if not row:
                return "INACTIVE", None, ""
            return str(row[0]), row[1], ""
        finally:
            conn.close()
    except Exception as exc:
        return "UNKNOWN", None, str(exc)


class KillSwitchStateCheck(Check):
    name = "kill_switch_state"
    group = "State"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 10

    def run(self, ctx: CheckContext) -> CheckResult:
        state, triggered_at, err = _kill_state(ctx.db_path)
        if err:
            return self._warn(f"kill_switch_state unreadable: {err}")
        if state == "INACTIVE":
            return self._passed("kill switch INACTIVE")
        trig_date = (triggered_at or "")[:10]
        today = ctx.as_of_date.isoformat()
        if trig_date and trig_date < today:
            return self._warn(
                f"{state} from {trig_date} — prior-day; app auto-clears at startup",
                kill_state=state, triggered_at=triggered_at)
        return self._failed(
            f"{state} active (triggered {triggered_at or '?'}) — trading halted",
            kill_state=state, triggered_at=triggered_at)


class OpenPositionsCheck(Check):
    name = "open_positions_at_start"
    group = "State"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 15

    def run(self, ctx: CheckContext) -> CheckResult:
        n, err = _safe_count(
            ctx.db_path,
            f"SELECT COUNT(*) FROM trades WHERE status IN ({_ph(ACTIVE_TRADE_STATUSES)})",
            ACTIVE_TRADE_STATUSES)
        if err:
            return self._warn(f"trades unreadable: {err}")
        if n == 0:
            return self._passed("no open positions at start")
        return self._failed(f"{n} open position(s) at start — investigate (MIS flat overnight)",
                            open_positions=n)


class OpenOrdersCheck(Check):
    name = "open_orders_at_start"
    group = "State"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 15

    def run(self, ctx: CheckContext) -> CheckResult:
        n, err = _safe_count(
            ctx.db_path,
            f"SELECT COUNT(*) FROM orders WHERE status IN ({_ph(ACTIVE_ORDER_STATUSES)})",
            ACTIVE_ORDER_STATUSES)
        if err:
            return self._warn(f"orders unreadable: {err}")
        if n == 0:
            return self._passed("no resting/active orders at start")
        return self._failed(f"{n} active order(s) at start — orphan? "
                            "(cancel-fix lands with the broker group)", open_orders=n)


class StuckExitingCheck(Check):
    name = "stuck_exiting_positions"
    group = "State"
    criticality = Criticality.WARN
    expected_duration_ms = 15

    def run(self, ctx: CheckContext) -> CheckResult:
        n, err = _safe_count(ctx.db_path,
                             "SELECT COUNT(*) FROM trades WHERE status = 'EXITING'")
        if err:
            return self._warn(f"trades unreadable: {err}")
        if n == 0:
            return self._passed("no trades stuck in EXITING")
        return self._warn(f"{n} trade(s) in EXITING — app _check_stuck_exiting handles intraday",
                          exiting=n)


class NeedsTgtRetryCheck(Check):
    name = "needs_tgt_retry_residue"
    group = "State"
    criticality = Criticality.WARN
    expected_duration_ms = 15

    def run(self, ctx: CheckContext) -> CheckResult:
        n, err = _safe_count(ctx.db_path,
                             "SELECT COUNT(*) FROM trades WHERE needs_tgt_retry = 1")
        if err:
            return self._warn(f"trades unreadable: {err}")
        if n == 0:
            return self._passed("no TGT-retry residue")
        return self._warn(f"{n} trade(s) with needs_tgt_retry=1 — TGTRetryManager picks up", residue=n)


CHECKS = [
    KillSwitchStateCheck(),
    OpenPositionsCheck(),
    OpenOrdersCheck(),
    StuckExitingCheck(),
    NeedsTgtRetryCheck(),
]
