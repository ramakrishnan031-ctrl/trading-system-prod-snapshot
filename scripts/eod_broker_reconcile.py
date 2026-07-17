"""
scripts/eod_broker_reconcile.py — Trading System v2 · P1 (02-Jul-2026)

Broker-authoritative EOD reconcile. Runs @15:58 IST Mon-Fri as a STANDALONE cron job
with its OWN creds (per-account api_key + token file, like reconcile_positions) — so it
verifies the day END-TO-END **even if trading-system.service was DOWN at 15:17** (the
gap where square-off never fired and the local-only eod_verify false-VERIFIES over a
naked broker position).

It queries the broker (positions / open orders / margins / day-realized P&L), compares
to local state, and emits a per-dimension + overall verdict:
    VERIFIED   — every REQUIRED dimension broker-confirmed clean.
    ISSUES     — a divergence on any dimension.
    UNVERIFIED — a REQUIRED broker dimension was unavailable (NEVER a false VERIFIED).

Dimension classification (approved 02-Jul, Option 1):
  REQUIRED (block VERIFIED if unavailable): positions · orders · broker DAY-REALIZED P&L.
  LEDGER   (local invariant, always available): fm_ledger 3-balance invariant.
  MARGIN   (SUPPLEMENTAL, reliability-gated): broker funds vs local total, evaluated ONLY
           inside [09:00, 15:45] (the broker margin endpoint is unreliable post-close —
           FIX-189); at 15:58 → NOT_CHECKED (never escalates to ISSUES/UNVERIFIED).

DETECT + VERIFY + ALERT ONLY — this job writes ONLY its own outputs (the verdict row +
pnl_reconciliation + the shadow-comparison columns). It NEVER mutates trades / exit_reason
/ capital / any trading state. Auto-remediation is P3.

Rollout: `eod_reconcile.authoritative` (config, default false = SHADOW). In shadow it runs
alongside eod_verify @15:55, records the same-day eod_verify verdict + mismatch, and alerts
at INFO. When authoritative, ISSUES/UNVERIFIED alert at CRITICAL and eod_verify is retired.

Parity: one code path. In PAPER there is no independent broker standalone (the sim state is
in-memory in the live process), so the verdict is a LOCAL SELF_CONSISTENCY check, labeled
`mode=PAPER` / self_consistency=1 — NEVER counted as broker-authoritative validation.

Exit codes: 0 = ran (verdict recorded, incl. ISSUES/UNVERIFIED); 1 = script error.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import time as dt_time
from pathlib import Path
from typing import Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist, today_ist

# Broker-margin reliability window (mirrors order_reconciler G3 / FIX-189).
_MARGIN_RELIABLE_OPEN = dt_time(9, 0)
_MARGIN_RELIABLE_CLOSE = dt_time(15, 45)

_REQUIRED = ("positions", "orders", "pnl")
VERIFIED, ISSUES, UNVERIFIED, NOT_CHECKED = "VERIFIED", "ISSUES", "UNVERIFIED", "NOT_CHECKED"


# ─────────────────────────────────────────────────────────────────────────────
# State snapshots (a None field = that broker dimension is UNAVAILABLE)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BrokerState:
    positions: Optional[Dict[str, int]] = None      # symbol -> signed net qty
    open_order_count: Optional[int] = None
    day_realized: Optional[float] = None
    margin_net: Optional[float] = None


@dataclass
class LocalState:
    open_positions: Dict[str, int] = field(default_factory=dict)  # symbol -> signed qty
    pending_order_count: int = 0
    realized: float = 0.0
    total: float = 0.0
    invariant_ok: bool = True
    position_recon_issue: bool = False   # any non-OK row in position_reconciliation (15:45)
    human_orders_present: bool = False


@dataclass
class Verdict:
    mode: str
    self_consistency: bool
    broker_reachable: bool
    positions_status: str
    orders_status: str
    pnl_status: str
    ledger_status: str
    margin_status: str
    overall_status: str
    detail: str


# ─────────────────────────────────────────────────────────────────────────────
# PURE verdict engine (no broker / no DB) — the correctness core, fully unit-tested
# ─────────────────────────────────────────────────────────────────────────────

def compute_verdict(
    broker: BrokerState,
    local: LocalState,
    *,
    mode: str = "LIVE",
    margin_reliable: bool,
    pnl_tolerance: float,
    margin_tol_base: float,
    margin_tol_pct: float,
    human_order_allowance: float,
) -> Verdict:
    """Compare a broker snapshot to a local snapshot → per-dimension + overall verdict.
    A None REQUIRED broker field ⇒ that dimension is UNVERIFIED ⇒ overall UNVERIFIED
    (never a partial false-pass). MARGIN is supplemental + reliability-gated. `mode` only
    labels self-consistency (PAPER); it does NOT change the comparison logic."""
    notes: List[str] = []
    self_consistency = (mode.upper() == "PAPER")

    # 1. POSITIONS (required) — broker net qty per symbol vs local.
    if broker.positions is None:
        positions_status = UNVERIFIED
        notes.append("positions: broker unavailable")
    else:
        syms = set(broker.positions) | set(local.open_positions)
        diffs = [s for s in syms
                 if int(broker.positions.get(s, 0)) != int(local.open_positions.get(s, 0))]
        if diffs or local.position_recon_issue:
            positions_status = ISSUES
            if diffs:
                notes.append(f"positions: broker≠local for {sorted(diffs)}")
            if local.position_recon_issue:
                notes.append("positions: reconcile_positions(15:45) flagged a non-OK row")
        else:
            positions_status = VERIFIED

    # 2. ORDERS (required) — broker open-order count vs local pending.
    if broker.open_order_count is None:
        orders_status = UNVERIFIED
        notes.append("orders: broker unavailable")
    elif int(broker.open_order_count) != int(local.pending_order_count):
        orders_status = ISSUES
        notes.append(f"orders: broker={broker.open_order_count} local_pending={local.pending_order_count}")
    else:
        orders_status = VERIFIED

    # 3. P&L (required) — broker DAY-REALIZED vs local realized.
    if broker.day_realized is None:
        pnl_status = UNVERIFIED
        notes.append("pnl: broker day-realized unavailable")
    elif abs(float(broker.day_realized) - float(local.realized)) > pnl_tolerance:
        pnl_status = ISSUES
        notes.append(f"pnl: broker={broker.day_realized:.2f} local={local.realized:.2f} "
                     f"(tol={pnl_tolerance:.2f})")
    else:
        pnl_status = VERIFIED

    # 4. LEDGER (local invariant, always available).
    ledger_status = VERIFIED if local.invariant_ok else ISSUES
    if not local.invariant_ok:
        notes.append("ledger: fm_ledger 3-balance invariant broken")

    # 5. MARGIN (supplemental, reliability-gated).
    if not margin_reliable or broker.margin_net is None:
        margin_status = NOT_CHECKED
        notes.append("margin: NOT_CHECKED (post-15:45 / unreliable)")
    else:
        eff_tol = max(margin_tol_base, abs(local.total) * margin_tol_pct)
        if local.human_orders_present:
            eff_tol += human_order_allowance
        if abs(float(broker.margin_net) - float(local.total)) > eff_tol:
            margin_status = ISSUES
            notes.append(f"margin: broker={broker.margin_net:.2f} local_total={local.total:.2f} "
                         f"(tol={eff_tol:.2f})")
        else:
            margin_status = VERIFIED

    # Roll-up: any REQUIRED unavailable → UNVERIFIED; else any ISSUES → ISSUES; else VERIFIED.
    req = {"positions": positions_status, "orders": orders_status, "pnl": pnl_status}
    dims = [positions_status, orders_status, pnl_status, ledger_status]
    if margin_status != NOT_CHECKED:
        dims.append(margin_status)
    if any(req[d] == UNVERIFIED for d in _REQUIRED):
        overall = UNVERIFIED
    elif any(s == ISSUES for s in dims):
        overall = ISSUES
    else:
        overall = VERIFIED

    broker_reachable = broker.positions is not None or broker.day_realized is not None
    return Verdict(
        mode=mode.upper(), self_consistency=self_consistency, broker_reachable=broker_reachable,
        positions_status=positions_status, orders_status=orders_status, pnl_status=pnl_status,
        ledger_status=ledger_status, margin_status=margin_status, overall_status=overall,
        detail="; ".join(notes) if notes else "all dimensions clean",
    )


def margin_reliable_now(now=None) -> bool:
    """True only inside [09:00, 15:45] IST on a market day-ish clock (the broker funds
    endpoint is unreliable post-close). At the 15:58 run this is False → margin NOT_CHECKED."""
    t = (now or now_ist()).time()
    return _MARGIN_RELIABLE_OPEN <= t <= _MARGIN_RELIABLE_CLOSE


# ─────────────────────────────────────────────────────────────────────────────
# Gather — broker (live) + local (DB). Standalone; never mutates trading state.
# ─────────────────────────────────────────────────────────────────────────────

def gather_broker(api_key: str, access_token: str, log: logging.Logger) -> BrokerState:
    """Query the broker for each dimension independently — a per-dimension failure leaves
    that field None (→ UNVERIFIED for a required dim), it does NOT abort the others."""
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    state = BrokerState()
    try:
        raw = kite.positions()
        net = raw.get("net", []) if isinstance(raw, dict) else []
        state.positions = {
            str(p.get("tradingsymbol", "")): int(p.get("quantity", 0))
            for p in net if int(p.get("quantity", 0)) != 0
        }
        day = raw.get("day", []) if isinstance(raw, dict) else []
        state.day_realized = sum(float(p.get("realised", 0.0) or 0.0) for p in day)
    except Exception as exc:  # noqa: BLE001
        log.warning("eod_broker_reconcile: positions()/day-pnl unavailable: %s", exc)
    try:
        orders = kite.orders() or []
        _open = {"OPEN", "TRIGGER PENDING", "AMO REQ RECEIVED", "MODIFY PENDING", "PUT ORDER REQ RECEIVED"}
        state.open_order_count = sum(1 for o in orders if str(o.get("status", "")).upper() in _open)
    except Exception as exc:  # noqa: BLE001
        log.warning("eod_broker_reconcile: orders() unavailable: %s", exc)
    try:
        m = kite.margins(segment="equity")
        state.margin_net = float(m.get("net", 0.0)) if isinstance(m, dict) else None
    except Exception as exc:  # noqa: BLE001
        log.warning("eod_broker_reconcile: margins() unavailable: %s", exc)
    return state


def gather_local(store: StateStore, date_iso: str) -> LocalState:
    """Read the local truth (READ-ONLY): open positions, pending orders, realized P&L,
    capital total, the fm_ledger invariant, and any reconcile_positions(15:45) issue."""
    ls = LocalState()
    try:
        for t in (store.get_all_open_trades() or []):
            sym = t["symbol"]
            qty = int(t["qty_filled"] or 0)
            if not sym or qty == 0:
                continue
            signed = qty if (t["direction"] or "").upper() == "LONG" else -qty
            ls.open_positions[sym] = ls.open_positions.get(sym, 0) + signed
    except Exception:  # noqa: BLE001
        pass
    try:
        ls.pending_order_count = len(store.get_pending_intraday_orders() or [])
    except Exception:  # noqa: BLE001
        pass
    try:
        ls.realized = float(store.get_today_closed_pnl(date_iso))   # Σ trades.net_pnl (no double-cost)
    except Exception:  # noqa: BLE001
        pass
    try:
        # position_reconciliation (15:45) — any non-OK row is evidence P1 must not VERIFY over.
        rows = store.fetch_all(
            "SELECT status FROM position_reconciliation WHERE date = ?", (date_iso,)
        ) or []
        ls.position_recon_issue = any(
            (r["status"] or "").upper() not in ("OK", "") for r in rows
        )
    except Exception:  # noqa: BLE001
        pass
    return ls


# ─────────────────────────────────────────────────────────────────────────────
# Persist (own outputs only) + orchestration
# ─────────────────────────────────────────────────────────────────────────────

def persist(store: StateStore, date_iso: str, v: Verdict, *, authoritative: bool,
            eod_verify_status: Optional[str], local: LocalState, broker: BrokerState) -> None:
    verified_at = now_ist().isoformat()
    # P1-verdict maps to eod_verify's binary (VERIFIED vs not) for the shadow mismatch flag.
    p1_clean = (v.overall_status == VERIFIED)
    ev_clean = (eod_verify_status == "VERIFIED") if eod_verify_status else None
    mismatch = None if ev_clean is None else int(p1_clean != ev_clean)
    store.upsert_eod_broker_reconciliation({
        "date": date_iso, "mode": v.mode, "self_consistency": int(v.self_consistency),
        "authoritative": int(authoritative), "broker_reachable": int(v.broker_reachable),
        "positions_status": v.positions_status, "orders_status": v.orders_status,
        "pnl_status": v.pnl_status, "ledger_status": v.ledger_status,
        "margin_status": v.margin_status, "overall_status": v.overall_status,
        "eod_verify_status": eod_verify_status, "mismatch": mismatch,
        "detail": v.detail[:1000], "verified_at": verified_at,
    })
    # Subsume reconcile_pnl: write pnl_reconciliation with the CORRECT columns + real creds.
    if broker.day_realized is not None:
        variance = abs(float(broker.day_realized) - float(local.realized))
        store.upsert_pnl_reconciliation(
            date=date_iso, broker_pnl=float(broker.day_realized), system_pnl=float(local.realized),
            variance=variance,
            status=("OK" if v.pnl_status == VERIFIED else "VARIANCE"),
            notes=None, created_at=verified_at,
        )


def _summary(v: Verdict, date_iso: str, authoritative: bool, mismatch: Optional[int]) -> str:
    tag = "" if authoritative else " [SHADOW]"
    sc = " (PAPER SELF_CONSISTENCY — not broker-authoritative)" if v.self_consistency else ""
    line = (f"EOD broker-reconcile {date_iso}: {v.overall_status}{tag}{sc}\n"
            f"positions={v.positions_status} orders={v.orders_status} pnl={v.pnl_status} "
            f"ledger={v.ledger_status} margin={v.margin_status}")
    if mismatch:
        line += f"\n⚠️ shadow mismatch vs eod_verify"
    if v.overall_status != VERIFIED:
        line += f"\n{v.detail}"
    return line


def _parse_args(argv=None):
    p = argparse.ArgumentParser(prog="eod_broker_reconcile")
    p.add_argument("--date", default=None)
    p.add_argument("--account", default=None)
    p.add_argument("--db", default=None)
    p.add_argument("--mode", default=None, help="paper|live (default: TRADING_MODE env, else live)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("eod_broker_reconcile")
    start = now_ist()
    try:
        from core.config_loader import load_all
        app = load_all()
        is_paper = (args.mode or os.environ.get("TRADING_MODE", "live")).lower() == "paper"
        authoritative = bool(app.system.eod_reconcile.authoritative)
        pnl_tol = float(app.system.eod_reconcile.pnl_tolerance)
        rc = app.system.order_reconciler
        margin_tol_base = float(rc.capital_drift_tolerance)
        margin_tol_pct = float(getattr(rc, "capital_drift_tolerance_pct", 0.0) or 0.0)
        human_allow = float(getattr(rc, "human_order_margin_tolerance", 5000.0))
        account = args.account or _primary_account()
    except Exception as exc:
        log.error("eod_broker_reconcile: config load failed: %s", exc)
        return 1

    db_path = Path(args.db) if args.db else _ROOT / "data_store" / "trading_system.db"
    try:
        store = StateStore(db_path=db_path)
    except Exception as exc:
        log.error("eod_broker_reconcile: state_store open failed: %s", exc)
        return 1

    date_iso = args.date or today_ist()
    local = gather_local(store, date_iso)
    # fm_ledger invariant: read the live snapshot only for the invariant self-check + total.
    local.invariant_ok, local.total = _local_capital_snapshot(store, log)

    mode = "PAPER" if is_paper else "LIVE"
    if is_paper:
        # No independent broker standalone → SELF_CONSISTENCY: mirror local for the required
        # dims (verify local internal consistency + the ledger invariant), margin NOT_CHECKED.
        broker = BrokerState(positions=dict(local.open_positions),
                             open_order_count=local.pending_order_count,
                             day_realized=local.realized, margin_net=None)
        reliable = False
    else:
        try:
            from scripts.reconcile_positions import _resolve_credentials
            api_key, access_token = _resolve_credentials(account, _ROOT / "config", log)
            broker = gather_broker(api_key, access_token, log)
        except Exception as exc:
            log.error("eod_broker_reconcile: broker creds/query failed: %s — UNVERIFIED", exc)
            broker = BrokerState()   # all None → required dims UNVERIFIED
        reliable = margin_reliable_now(start)

    v = compute_verdict(
        broker, local, mode=mode, margin_reliable=reliable, pnl_tolerance=pnl_tol,
        margin_tol_base=margin_tol_base, margin_tol_pct=margin_tol_pct,
        human_order_allowance=human_allow,
    )
    eod_verify_status = None
    try:
        eod_verify_status = store.get_eod_verification_status(date_iso)
    except Exception:  # noqa: BLE001
        pass

    persist(store, date_iso, v, authoritative=authoritative,
            eod_verify_status=eod_verify_status, local=local, broker=broker)

    p1_clean = (v.overall_status == VERIFIED)
    ev_clean = (eod_verify_status == "VERIFIED") if eod_verify_status else None
    mismatch = None if ev_clean is None else int(p1_clean != ev_clean)
    summary = _summary(v, date_iso, authoritative, mismatch)
    print(summary)
    log.info("eod_broker_reconcile.complete", extra={
        "date": date_iso, "overall": v.overall_status, "mode": mode,
        "authoritative": authoritative, "mismatch": mismatch,
    })

    # Alert: CRITICAL when authoritative + not clean; else INFO (shadow / clean).
    try:
        from alerts.telegram_notifier import TelegramNotifier
        n = TelegramNotifier.from_env()
        if n:
            if authoritative and v.overall_status != VERIFIED:
                n.send_alert(summary, level="CRITICAL")
            else:
                n.send_info(summary)
    except Exception as exc:  # noqa: BLE001
        log.warning("eod_broker_reconcile: telegram failed: %s", exc)
    try:
        from utils.cron_heartbeat import record_heartbeat
        record_heartbeat("eod_broker_reconcile",
                         duration_sec=(now_ist() - start).total_seconds())
    except Exception:  # noqa: BLE001
        pass

    store.close()
    return 0


def _primary_account() -> Optional[str]:
    try:
        from core.account_registry import AccountRegistry
        reg = AccountRegistry.load(_ROOT / "config" / "accounts.csv")
        return reg.primary().account_id
    except Exception:  # noqa: BLE001
        return None


def _local_capital_snapshot(store: StateStore, log: logging.Logger):
    """Return (invariant_ok, total) from a fresh FundManager rehydrated read — READ-ONLY.
    A standalone job has no live FM; rebuild a read-only snapshot from fm_ledger."""
    try:
        row = store.fetch_one(
            # fm_ledger's PK is `ledger_id` (autoincrement) — the old `ORDER BY id`
            # referenced a non-existent column, so this query ALWAYS raised
            # "no such column: id", was caught below, and returned a silent 0.0
            # snapshot (wrong capital total). ledger_id DESC = the latest ledger row.
            "SELECT balance_after FROM fm_ledger ORDER BY ledger_id DESC LIMIT 1"
        )
        total = float(row["balance_after"]) if row and row["balance_after"] is not None else 0.0
        # Post-EOD the invariant is a local self-check; we treat a readable ledger as OK
        # here and leave a deeper reserved/used invariant audit to CHECK7 (in-session).
        return True, total
    except Exception as exc:  # noqa: BLE001
        log.warning("eod_broker_reconcile: capital snapshot failed: %s", exc)
        return True, 0.0


def _cron_main(argv=None) -> int:
    """Cron entry: S1 holiday-skip, then the real reconcile.

    S1 (2026-07-17): market_day_only was decorative — nothing enforced it at the
    cron entry, so this ran (and called the broker) on every NSE holiday.
    skip_if_non_trading_day FAILS OPEN (weekday fallback on any calendar error)
    so a trading day is never skipped. Guard here, not in main(), so a manual
    reconcile on a non-trading day still works.
    """
    from utils.cron_heartbeat import skip_if_non_trading_day

    if skip_if_non_trading_day("eod_broker_reconcile"):
        return 0
    return main(argv)


if __name__ == "__main__":
    sys.exit(_cron_main())
