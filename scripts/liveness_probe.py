#!/usr/bin/env python3
"""
scripts/liveness_probe.py -- Trading System v2  (17-Jul-2026, liveness alarm)

Alarms when `trading-system.service` is UNEXPECTEDLY DOWN during the hours it must be up.

WHY THIS EXISTS
    On 17-Jul-2026 the S4 boot fix halted the service at 08:16:09 with a CLEAN exit 0
    (a failed /health self-check fired _shutdown_event). It took 0 trades on a live
    trading day -- and NOTHING noticed for the whole session:
      * monitoring_canary's respawn probe watches `alert-watcher.service`, not this one;
      * its dashboard probe watches `gui-dashboard`, not this one;
      * `Restart=on-failure` + a clean exit 0 means systemd never restarts and never
        complains, so NRestarts stays 0 -- the *healthy* value.
    Nothing has ever watched trading-system's LIVENESS. A headless system that can die
    silently is the gap this closes. See docs/audit/s4_boot_outage_17jul2026.md.

WHAT IT DOES
    Every 5 minutes across the window, on trading days only: if the unit is not active
    and the operator did not park it, raise ONE CRITICAL (Telegram; CRITICAL sentinel
    fallback, exactly as the canary does).

INDEPENDENCE (deliberate)
    This probe must not depend on the health of the thing it monitors. It does NOT import
    main.py: if main.py could not be imported, the probe would die exactly when the system
    is broken -- which is the silent failure it exists to prevent. The window constants are
    therefore duplicated here, and test_liveness_probe.py::
    test_window_end_not_after_a_legitimate_exit asserts this probe's upper bound never
    passes the configured service stop time. Drift is caught by the suite, not paid for at
    runtime.

THE FALSE-ALARM MATRIX (this is the whole design -- see the report for the derivation)
    SILENT  non-trading day (weekend / NSE holiday)   -- authoritative calendar (S1)
    SILENT  outside [09:00, 16:00) IST                -- it is not expected up yet / any more
    SILENT  unit is active                            -- healthy
    SILENT  operator SOFT_KILL / HARD_KILL parked it  -- a deliberate, recorded halt
    SILENT  already alarmed for this same incident    -- dedup on InactiveEnterTimestamp
    ALARM   anything else                             -- an unexpected death

    *** A deliberate `systemctl stop` is NOT distinguishable from a silent death. ***
    Both leave Result=success / ExecMainStatus=0 -- today's outage was itself a clean
    exit 0. So an unmarked stop ALARMS, by design: a missed real death costs a trading
    session, a false alarm costs a Telegram message. The operator already has the
    documented way to say "this is intentional" -- park it with a SOFT_KILL (as the
    16-Jul planned pause did: reason="planned pause ... no trading issue", by=operator),
    which this probe honours.

Exit codes: 0 = probed (alarmed or not); 1 = the probe itself could not run.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date, datetime, time as _time
from pathlib import Path
from typing import Callable, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Load .env so a cron invocation sees TELEGRAM_BOT_TOKEN, mirroring monitoring_canary.
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:  # noqa: BLE001 — the probe must run even if python-dotenv is absent
    pass

from core.logger import get_logger
from core.time_authority import now_ist
# Reused from the canary rather than re-implemented (derive, don't duplicate): the same
# systemctl KEY=VALUE parsing and the same atomic tmp+os.replace state persistence.
from scripts.monitoring_canary import (
    _load_service_state,
    _parse_systemctl_show,
    _save_service_state,
)

_log = get_logger("liveness_probe")

_UNIT = "trading-system.service"
_STATE_FILE = "liveness_alarm_state.json"

# ── The liveness window ──────────────────────────────────────────────────────
# UPPER BOUND 16:00. main._eod_self_exit_due() returns (False, -1) *without querying*
# before its window_end, so the service NEVER self-exits before the configured stop time
# -- at 15:59 it must still be up.
#
# ⚠️ 25-Jul-2026: the service stop time became CONFIGURABLE and is now 17:35, so this
# bound is CONSERVATIVE rather than exact -- it no longer equals the stop time, it is
# merely never after it (which is the property that matters: never alarm during a period
# when a clean exit is already legitimate).
#
# ⛔ Do NOT "resync" this to 17:35. This probe's cron is `*/5 09-15 Mon-Fri` (last run
# 15:55) and structurally cannot run past 16:00; raising the constant alone would
# advertise a window the probe never visits AND would alarm every day after the 17:35
# exit. The 16:00-17:35 tail is genuinely UNWATCHED -- a recorded, triggered follow-up
# (docs/audit/service_window_configurable_25jul2026.md §6), and extending it needs the
# cron AND this bound moved together, which is its own small design.
#
# LOWER BOUND is deliberately NOT main.SERVICE_WINDOW_START (08:00). 08:00 is when the
# service MAY start, not when it MUST be up: the 08:15 token cron -> token-watcher ->
# ~08:30 premarket chain means 08:00-08:30 is a legitimate not-yet-up gap, and alarming
# there would be a guaranteed daily false alarm. 09:00 is after every legitimate start
# path and still 15 minutes before the 09:15 market open -- today's 08:16:09 death would
# have been caught at 09:00, a full hour before the 10:00 entry window.
_LIVENESS_START = _time(9, 0)
_LIVENESS_END = _time(16, 0)   # <= the configured service stop (drift-guarded by the tests)


def within_liveness_window(now: datetime) -> bool:
    """True iff `now` (IST) is in [09:00, 16:00) -- when the service MUST be up."""
    return _LIVENESS_START <= now.time() < _LIVENESS_END


def is_trading_day_safe(today: date, config_dir: Path | str = Path("config")) -> bool:
    """Authoritative NSE calendar, FAIL-OPEN exactly as the S1 guard
    (utils.cron_heartbeat.skip_if_non_trading_day) does: on any calendar error degrade to
    a plain weekday check.

    Direction check -- fail-open is right HERE too, for the same asymmetry S1 used: a
    calendar error on a weekday holiday costs one false alarm; treating a real trading day
    as a holiday would silence the alarm on the exact day it matters.

    Not skip_if_non_trading_day() itself: that records a SKIPPED heartbeat per call, and at
    a 5-minute cadence (`monitored: false`) that would write ~84 rows/day and pollute the
    Cron Officer. This reuses the same authority (utils.holiday_guard.is_trading_day) with
    the same fallback, minus the heartbeat side effect.
    """
    try:
        from utils.holiday_guard import is_trading_day
        return bool(is_trading_day(today, Path(config_dir)))
    except Exception:  # noqa: BLE001 — missing/corrupt nse_holidays_<year>.yaml
        return today.weekday() < 5


def read_kill_state(db_path: Path | str = Path("data_store/trading_system.db")) -> str:
    """The operator's parked marker: kill_switch_state.state (single-row table, KS3).

    Returns 'INACTIVE' / 'SOFT_KILL' / 'HARD_KILL', or 'UNKNOWN' if it cannot be read.
    UNKNOWN does NOT suppress the alarm (see classify_liveness) -- an unreadable kill
    switch must never be able to silence a real death.
    """
    try:
        from core.db_connect import connect
        conn = connect(str(db_path))
        try:
            row = conn.execute("SELECT state FROM kill_switch_state WHERE id = 1").fetchone()
        finally:
            conn.close()
        if row is None:
            return "INACTIVE"
        return str(row[0] if not hasattr(row, "keys") else row["state"])
    except Exception as exc:  # noqa: BLE001
        _log.warning("liveness_probe: kill_switch_state unreadable (%s) — not suppressing", exc)
        return "UNKNOWN"


# ── The verdict (pure — no I/O, fully unit-testable) ─────────────────────────

def classify_liveness(
    *,
    active_state: str,
    now: datetime,
    trading_day: bool,
    kill_state: str,
    since: str,
    already_alarmed_for: Optional[str],
) -> tuple[bool, str, str]:
    """Pure verdict: (should_alarm, code, detail).

    Order is cheapest-and-most-exculpatory first. `since` doubles as the incident id.
    """
    if not trading_day:
        return False, "NON_TRADING_DAY", "non-trading day (weekend/NSE holiday)"
    if not within_liveness_window(now):
        return False, "OUTSIDE_WINDOW", (
            f"{now.strftime('%H:%M')} is outside "
            f"[{_LIVENESS_START.strftime('%H:%M')}-{_LIVENESS_END.strftime('%H:%M')}) IST"
        )
    if active_state == "active":
        return False, "ALIVE", f"{_UNIT}=active"
    # A parked system is DOWN on purpose: an active kill makes main() halt by design.
    # UNKNOWN is deliberately excluded — an unreadable marker cannot buy silence.
    if kill_state in ("SOFT_KILL", "HARD_KILL"):
        return False, "OPERATOR_HALT", f"parked by operator ({kill_state})"
    if already_alarmed_for and already_alarmed_for == since:
        return False, "ALREADY_ALARMED", f"already alarmed for this incident (since {since})"
    return True, "DOWN", f"{_UNIT}={active_state or 'unknown'} since {since or 'unknown'}"


# ── The probe (thin I/O wrapper; every dependency injectable) ────────────────

def probe_liveness(
    *,
    runner: Optional[Callable] = None,
    state_path: Optional[Path] = None,
    now: Optional[datetime] = None,
    config_dir: Path | str = Path("config"),
    db_path: Path | str = Path("data_store/trading_system.db"),
) -> tuple[bool, str, str, dict]:
    """Read REAL service state and return (should_alarm, code, detail, props).

    `runner` defaults to the real `systemctl show trading-system.service` — the production
    path. Tests inject it for determinism, but test_default_runner_shells_out_to_systemctl
    pins that the default really shells out for THIS unit, so no fixture can quietly turn
    this into an assume-healthy stub (the S4 lesson).
    """
    if runner is None:
        runner = lambda: subprocess.run(  # noqa: E731
            ["systemctl", "show", _UNIT,
             "--property=ActiveState", "--property=SubState",
             "--property=Result", "--property=ExecMainStatus",
             "--property=InactiveEnterTimestamp"],
            capture_output=True, text=True, timeout=10).stdout

    try:
        props = _parse_systemctl_show(runner())
    except Exception as exc:  # noqa: BLE001
        # We cannot read the unit. Do NOT stay silent: an unreadable systemctl during the
        # window is itself abnormal, and silence is the failure mode being fixed.
        props = {}
        _log.warning("liveness_probe: systemctl unreadable (%s)", exc)

    now = now or now_ist()
    active_state = props.get("ActiveState", "") or ""
    since = props.get("InactiveEnterTimestamp", "") or ""
    # Fall back to a per-day dedup key when systemd gives no timestamp, so a missing
    # timestamp degrades to "at most one alarm per day", never to an alarm every 5 min.
    incident = since or f"date:{now.date().isoformat()}"

    prev = _load_service_state(state_path)
    should_alarm, code, detail = classify_liveness(
        active_state=active_state,
        now=now,
        trading_day=is_trading_day_safe(now.date(), config_dir),
        kill_state=read_kill_state(db_path) if active_state != "active" else "INACTIVE",
        since=incident,
        already_alarmed_for=prev.get("alarmed_for"),
    )
    if should_alarm:
        _save_service_state(state_path, {"alarmed_for": incident, "iso": now.isoformat()})
    return should_alarm, code, detail, props


def format_alarm(props: dict, since: str, now: datetime) -> str:
    """The operator-facing message. It names the smoking gun: today's death was a CLEAN
    exit 0 (Result=success/ExecMainStatus=0), which says 'shut itself down' rather than
    'crashed' — the detail that pointed straight at S4."""
    return "\n".join([
        f"🔴 [LFL836] LIVENESS: {_UNIT} is DOWN during the service window",
        f"since: {since or 'unknown'}",
        f"now:   {now.strftime('%d-%b %H:%M')} IST "
        f"(window {_LIVENESS_START.strftime('%H:%M')}-{_LIVENESS_END.strftime('%H:%M')})",
        f"state: ActiveState={props.get('ActiveState', '?')} "
        f"SubState={props.get('SubState', '?')} "
        f"Result={props.get('Result', '?')} "
        f"ExecMainStatus={props.get('ExecMainStatus', '?')}",
        "",
        "No signals are being processed and no positions are being managed.",
        "No operator kill marker is set, so this was NOT a planned pause.",
        "(Result=success + ExecMainStatus=0 means it shut itself down cleanly, "
        "not that it crashed — check the boot self-checks in the journal.)",
        "Investigate: journalctl -u trading-system.service --since today",
    ])


def deliver_alarm(message: str) -> None:
    """ONE CRITICAL via Telegram; if Telegram is down too, a CRITICAL sentinel rides the
    F1 fallback. Identical delivery contract to monitoring_canary._deliver_report."""
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=_log)
        if notifier is not None:
            res = notifier.send(severity="CRITICAL",
                                title=f"LIVENESS: {_UNIT} is DOWN",
                                body=message, source_module="liveness_probe")
            if getattr(res, "success", False):
                return
    except Exception as exc:  # noqa: BLE001
        _log.error("liveness alarm telegram failed: %s", exc)
    try:
        from alerts.critical import write_critical_sentinel
        write_critical_sentinel(title=f"LIVENESS: {_UNIT} is DOWN",
                                body=message, source_module="liveness_probe")
    except OSError as exc:
        _log.error("liveness alarm sentinel fallback failed: %s", exc)


def main(argv=None) -> int:
    try:
        from core.config_loader import load_all
        sentinel_dir = Path(load_all().system.alerts.sentinel_dir)
    except Exception:  # noqa: BLE001 — never let a config wobble silence the probe
        sentinel_dir = Path("data_store")

    now = now_ist()
    should_alarm, code, detail, props = probe_liveness(state_path=sentinel_dir / _STATE_FILE,
                                                       now=now)
    _log.info("liveness_probe.result", extra={"code": code, "detail": detail})
    if not should_alarm:
        return 0

    message = format_alarm(props, props.get("InactiveEnterTimestamp", ""), now)
    print(message)
    _log.critical("liveness_probe: %s", detail)
    deliver_alarm(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
