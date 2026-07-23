"""
reports/daily_trade_review.py — Trading System v2

NEW consolidated, DB-PURE report generator. Tab order (Dashboard first):
  Dashboard       — summary of the six sheets: reconciliation banner, coverage panel, yesterday-vs-today
                    deltas, trading/profitability/capital summaries, deterministic (rule-based) highlights.
                    Every headline derives from the same sources the detail sheets use → cannot disagree.
  Reconciliation  — integrity backbone: per-identity PASS/FAIL/PENDING blocks + overall verdict.
  Orders          — forensic trade master: one row per trade ("what happened to this trade?").
  Signals         — one row per signal that reached storage (honest post-webhook-dedup basis).
  Strategies      — 5 tables: per-strategy/time-bucket/direction/RR (daily) + a trailing-window
                    ranking (transparent confidence-weighted composite). Aggregates the trades truth layer.
  Slippage        — 6 blocks: summary + 3-leg decomposition, band ("tier") analysis, strategy-wise,
                    stock-wise, worst-20, 10-day trend. From trade_slippage_log (slippage_recorder).
  Config          — sectioned key/value of the day's resolved AppConfig (W0 config_snapshots).
Runs PARALLEL to reports/daily_report.py + reports/daily_review.py — it does NOT edit,
retire, or re-cron them.

    Output: reports/output/daily_trade_review_report_<YYYY-MM-DD>.xlsx

DB-ONLY (guardrail): this module reads ONLY the DB via StateStore. No yaml/csv/api
reads anywhere. It reuses the shared openpyxl palette from reports/style_constants.py
(the 4 tiny cell helpers are mirrored locally to stay decoupled from daily_report.py).

Parity: read-only analytics — mode-agnostic. PAPER and LIVE trades render identically
(mode is a column/context, never a code path).

── Flag resolutions (investigated against the real schema/code, 01-Jul-2026) ──
FLAG 1 (Exit Trigger Timestamp): DEFINITIVELY ABSENT. No column records when an
    SL/TGT trigger FIRED distinct from the fill (trades.exit_time ≈ orders.filled_at;
    a resting SL/TGT's placed_at is at entry, not the trigger). → "Exit Trigger" and
    "Exit Delay s" render "— pending W5". Entry Delay IS confirmable (order_to_fill_ms
    = ENTRY placed→filled) and is built.
FLAG 2 (Closure type): RESOLVED via a reconciliation_log join, labelled HONESTLY.
    status='CLOSED_MANUAL'/exit_reason='MANUAL' is a 4-way collision (daily-EOD,
    operator-manual, RMS, kill-flatten) that ALL write reconciliation_log
    check_name='MANUAL_CLOSE' — which is the reconciler *detecting* an external
    close, NOT a repair. So MANUAL_CLOSE is NOT mapped to RECON_CLOSE (that would
    over-claim); only STUCK_EXITING (reconciler-INITIATED finalize) is RECON_CLOSE,
    and %ORPHAN%/SYSTEM_OVERSELL is ORPHAN_RECOVERY. The daily-EOD squareoff writes
    no positive per-trade marker, so EOD / operator / RMS are indistinguishable →
    the truthful label for that bucket is SYSTEM_CLOSE (not RECON_CLOSE, not a
    guessed EOD). A POSITIVE EOD marker (exit_reason='EOD_SQUAREOFF' / legacy '*EOD*')
    is honoured as EOD_SQUAREOFF. RECON_EOD_CLOSE only when BOTH are independently
    present. Permanent fix = W8 (a per-trade closure_source written at close time).
    Normal SL/TGT closes carry no recon row → fall through unaffected. Evidence → Remarks.
FLAG 3 (Data Freshness): MFE/MAE per-trade provenance ABSENT (no source tag on
    trade_excursions; excursion_reconstruction_runs holds only aggregate counts).
    S&R IS derivable as a 2-state (sr_detector_results.actual_result NULL=detect-only /
    NOT NULL=backfilled). → "Data Freshness" renders an honest composite; the
    RECONSTRUCTED-vs-BACKFILLED split for MFE/MAE is logged as follow-up W6.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import openpyxl
from openpyxl.utils import get_column_letter

from core.state_store import StateStore
from reports import signal_status as sig_status
from reports.style_constants import (
    FILL_GREEN, FILL_RED, FILL_AMBER, FILL_GREY, FILL_HEADER,
    FONT_BODY, FONT_HEADER, FONT_WHITE_BOLD, FONT_TITLE,
    BORDER_ALL, ALIGN_CENTER, ALIGN_LEFT, ALIGN_RIGHT,
    NUM_FMT_CURRENCY, NUM_FMT_INTEGER,
)
from openpyxl.styles import PatternFill, Font

_log = logging.getLogger("daily_trade_review")

# ── pending-capture follow-up markers (missing-data labels, per design) ──────────
#   capture-gap  → "— pending W#"   (source does not exist yet; a writer must add it)
#   sparse-exists → "N/A — no data" (source exists but this trade has no row)
PENDING_MARGIN = "— pending W2"        # trades.broker_margin_blocked (audit W2)
PENDING_EXIT_TRIGGER = "— pending W5"  # FLAG 1: exit-trigger timestamp capture
PENDING_EXCH_ORDER = "— pending W7"    # orders.exchange_order_id capture
NA_NO_DATA = "N/A — no data"           # sparse-but-exists (no row for this trade)
NA_NO_SNAP = "— pending W0"            # config_snapshots has no row for this date (pre-W0)

# ── closure-type sources (FLAG 2) ────────────────────────────────────────────────
# Reconciler-INITIATED close/repair → RECON_CLOSE. STUCK_EXITING is the reconciler
# finalizing a stuck (kill-switch) exit — the reconciler itself completes the close.
_RECON_INITIATED_CLOSE = {"STUCK_EXITING"}
# Reconciler-INITIATED orphan / oversell recovery → ORPHAN_RECOVERY.
_ORPHAN_CHECKS = {"ORPHAN_ORDER", "ORPHAN_ADOPTION", "INFLIGHT_ORPHAN",
                  "INFLIGHT_ORPHAN_FLATTEN", "SYSTEM_OVERSELL"}
# The reconciler merely DETECTED an external close it did NOT initiate (daily-EOD
# squareoff, operator-manual, RMS auto-squareoff, un-relayed SL). ALL collapse to
# this one check_name (`mark_trade_manually_closed`) with exit_reason='MANUAL' and
# NO per-trade trigger recorded — so it must NOT be labelled RECON_CLOSE (that would
# over-claim a reconciler repair). Honest bucket = SYSTEM_CLOSE; permanent fix = W8.
_EXTERNAL_CLOSE_DETECT = {"MANUAL_CLOSE"}
_SL_REASONS = {"SL_HIT", "EMERGENCY_SL_TICK"}
_OTHER_CLOSE_TYPES = {"EOD_SQUAREOFF", "SYSTEM_CLOSE", "RECON_CLOSE", "RECON_EOD_CLOSE"}
_OPEN_STATUSES = {"PENDING", "PENDING_FILL", "OPEN", "PARTIAL", "EXITING",
                  "UNKNOWN_IN_FLIGHT"}


def _is_eod_positive(er: str) -> bool:
    """A POSITIVE per-trade EOD marker: the emergency-exit path writes
    exit_reason='EOD_SQUAREOFF'; a legacy close reason may contain 'EOD'
    (e.g. 'MANUAL_CLOSE_EOD'). 'EOD_EXIT_FAILED' is NOT a close (the trade stays
    OPEN) and is excluded. A daily-EOD squareoff SUCCESS writes NO such marker —
    it collapses to exit_reason='MANUAL' (see _EXTERNAL_CLOSE_DETECT)."""
    return er == "EOD_SQUAREOFF" or ("EOD" in er and "FAIL" not in er)

# ── 9 colour-banded groups (distinct header fills) ───────────────────────────────
_GROUP_TITLES = {
    "A": "A · SIGNAL", "B": "B · PLANNED", "C": "C · LIFECYCLE", "D": "D · EXECUTION",
    "E": "E · P&L", "F": "F · SLIPPAGE", "G": "G · QUALITY", "H": "H · S&R", "I": "I · META",
}
_GROUP_FILLS = {
    "A": PatternFill("solid", fgColor="1F4E79"), "B": PatternFill("solid", fgColor="2E75B6"),
    "C": PatternFill("solid", fgColor="548235"), "D": PatternFill("solid", fgColor="7030A0"),
    "E": PatternFill("solid", fgColor="C00000"), "F": PatternFill("solid", fgColor="BF8F00"),
    "G": PatternFill("solid", fgColor="385723"), "H": PatternFill("solid", fgColor="843C0C"),
    "I": PatternFill("solid", fgColor="404040"),
}

# ── column spec: (group, header, key, fmt, width) — header row3 + data order ──────
#   fmt: text | int | money | num2 | time
_COLSPECS: List[Tuple[str, str, str, str, float]] = [
    # A · Signal
    ("A", "#", "seq", "int", 5), ("A", "Date", "date", "text", 11),
    ("A", "Signal Time", "signal_time", "time", 10), ("A", "VM Receipt", "vm_receipt", "time", 10),
    ("A", "Symbol", "symbol", "text", 12), ("A", "Strategy", "strategy", "text", 16),
    ("A", "Trade Type", "trade_type", "text", 10), ("A", "Dir", "direction", "text", 6),
    ("A", "Score", "signal_score", "int", 7), ("A", "Min Score", "min_score", "text", 9),
    # B · Planned
    ("B", "Qty", "qty_planned", "int", 7), ("B", "Sys Entry", "sys_entry", "money", 10),
    ("B", "Sys SL", "sys_sl", "money", 10), ("B", "Sys TGT", "sys_tgt", "money", 10),
    ("B", "Risk Amt", "risk_amt", "money", 10), ("B", "Reward Amt", "reward_amt", "money", 10),
    ("B", "Planned RR", "planned_rr", "num2", 9), ("B", "SL %", "sl_pct", "num2", 8),
    ("B", "TGT %", "tgt_pct", "num2", 8), ("B", "Capital Consumed", "capital_consumed", "money", 12),
    ("B", "Broker Margin Blocked", "broker_margin", "text", 14),
    # C · Lifecycle
    ("C", "Entry Placed", "entry_placed", "time", 10), ("C", "Entry Filled", "entry_filled", "time", 10),
    ("C", "SL Created", "sl_created", "time", 10), ("C", "TGT Created", "tgt_created", "time", 10),
    ("C", "Exit Trigger", "exit_trigger", "text", 12), ("C", "Exit Time", "exit_time", "time", 10),
    ("C", "Duration min", "duration_min", "num2", 9), ("C", "Entry Delay s", "entry_delay_s", "num2", 9),
    ("C", "Exit Delay s", "exit_delay_s", "text", 11),
    # D · Execution
    ("D", "Filled Qty", "qty_filled", "int", 8), ("D", "Filled Entry", "filled_entry", "money", 10),
    ("D", "Filled SL", "filled_sl", "money", 10), ("D", "Filled TGT", "filled_tgt", "money", 10),
    ("D", "Filled Exit", "filled_exit", "money", 10), ("D", "Exit Reason", "exit_reason", "text", 15),
    ("D", "Closure Type", "closure_type", "text", 15),
    # E · P&L
    ("E", "Gross", "gross", "money", 10), ("E", "Brokerage", "cost_brokerage", "money", 9),
    ("E", "Exchange", "cost_exchange", "money", 9), ("E", "STT", "cost_stt", "money", 8),
    ("E", "GST", "cost_gst", "money", 8), ("E", "SEBI", "cost_sebi", "money", 8),
    ("E", "Stamp", "cost_stamp", "money", 8), ("E", "Total Charges", "total_charges", "money", 11),
    ("E", "Net", "net", "money", 10), ("E", "ROI %", "roi_pct", "num2", 8),
    # F · Slippage
    ("F", "Price Slab", "price_band", "text", 9), ("F", "Allowed Slip", "allowed_slip", "money", 10),
    ("F", "Actual Slip", "actual_slip", "money", 10), ("F", "Slip Δ", "slip_delta", "money", 9),
    ("F", "Slip %", "slip_pct", "num2", 8), ("F", "Slip Cost", "slip_cost", "money", 9),
    ("F", "Profit Impact %", "rr_damage", "num2", 11),
    # G · Quality
    ("G", "MAE %", "mae", "num2", 8), ("G", "MFE %", "mfe", "num2", 8),
    ("G", "MFE Cap %", "mfe_cap", "num2", 9), ("G", "RR Achieved", "rr_achieved", "num2", 9),
    ("G", "Efficiency %", "efficiency", "num2", 10), ("G", "Grade", "grade", "text", 6),
    # H · S&R
    ("H", "Nearest Support (tf · dist%)", "near_support", "text", 26),
    ("H", "Nearest Resistance (tf · dist%)", "near_resistance", "text", 26),
    # I · Meta
    ("I", "Trade Status", "trade_status", "text", 13), ("I", "Trade Result", "trade_result", "text", 9),
    ("I", "Data Freshness", "data_freshness", "text", 22), ("I", "Remarks", "remarks", "text", 32),
    ("I", "System Trade ID", "trade_id", "text", 24), ("I", "Broker Order ID", "broker_order_id", "text", 20),
    ("I", "Exchange Order ID", "exchange_order_id", "text", 16),
]

# Columns summed into the TOTALS row
_TOTAL_KEYS = {
    "qty_filled", "gross", "cost_brokerage", "cost_exchange", "cost_stt", "cost_gst",
    "cost_sebi", "cost_stamp", "total_charges", "net", "slip_cost", "capital_consumed",
}


# ═════════════════════════════════════════════════════════════════════════════════
# Small value helpers
# ═════════════════════════════════════════════════════════════════════════════════

def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _parse_dt(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    s = str(iso).strip().replace("T", " ")
    # tolerate a trailing tz offset like +05:30
    for cut in (19, 16):
        try:
            return datetime.strptime(s[:cut], "%Y-%m-%d %H:%M:%S" if cut == 19 else "%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return None


def _fmt_time(iso: Optional[str]) -> str:
    dt = _parse_dt(iso)
    return dt.strftime("%H:%M:%S") if dt else ""


def _minutes_between(a: Optional[str], b: Optional[str]) -> Optional[float]:
    da, db = _parse_dt(a), _parse_dt(b)
    if not da or not db:
        return None
    return round((db - da).total_seconds() / 60.0, 1)


def _fmt_zone(zone_json: Optional[str], dist_pct: Any, confidence: Any) -> str:
    """'band_low-band_high [CONF] tf1,tf2 · dist%' from a ScoredZone JSON snapshot."""
    if not zone_json:
        return NA_NO_DATA
    try:
        z = json.loads(zone_json)
    except (TypeError, ValueError):
        return NA_NO_DATA
    lo, hi = _f(z.get("band_low")), _f(z.get("band_high"))
    band = f"{lo:.2f}-{hi:.2f}" if lo is not None and hi is not None else "?"
    conf = confidence or z.get("confidence") or "?"
    tfs = z.get("timeframes") or z.get("timeframe") or []
    tf = ",".join(str(t) for t in tfs) if isinstance(tfs, (list, tuple)) else str(tfs)
    d = _f(dist_pct)
    dist = f"{d:.2f}%" if d is not None else "?"
    return f"{band} [{conf}] {tf} · {dist}".strip()


def _grade(efficiency: Optional[float]) -> str:
    if efficiency is None:
        return ""
    if efficiency >= 80:
        return "A"
    if efficiency >= 60:
        return "B"
    if efficiency >= 40:
        return "C"
    if efficiency >= 20:
        return "D"
    return "F"


# ═════════════════════════════════════════════════════════════════════════════════
# FLAG 2 — closure-type classifier (documented precedence)
# ═════════════════════════════════════════════════════════════════════════════════

def classify_closure(trade: Dict[str, Any], recon_rows: List[Dict[str, Any]]) -> Tuple[str, str]:
    """
    Classify a trade's closure, HONESTLY, into one of {SL, TGT, EOD_SQUAREOFF,
    SYSTEM_CLOSE, RECON_CLOSE, RECON_EOD_CLOSE, ORPHAN_RECOVERY, UNKNOWN} (or '—'
    if never opened / still open). Returns (closure_type, evidence).

    Precedence (order preserved from the original spec, but the RECON_CLOSE
    trigger is NARROWED to be truthful):
        RECON_EOD_CLOSE  (only if BOTH a reconciler-initiated close AND a positive EOD marker)
        > RECON_CLOSE    (reconciler-INITIATED finalize: STUCK_EXITING — NOT generic MANUAL_CLOSE)
        > ORPHAN_RECOVERY(reconciler-initiated orphan/oversell recovery)
        > [no position]  (REJECTED* / CANCELLED)
        > EOD_SQUAREOFF  (POSITIVE per-trade EOD marker; a co-occurring MANUAL_CLOSE recon row is
                          EOD bookkeeping and does NOT override it)
        > SYSTEM_CLOSE   (the honest collapsed bucket: exit_reason='MANUAL' + reconciler MANUAL_CLOSE
                          detect = daily-EOD / operator-manual / RMS / kill, trigger NOT recorded — W8)
        > SL / TGT
        > UNKNOWN

    WHY (verified 01-Jul, code + real data): the daily-EOD squareoff writes NO
    per-trade marker — it lets the reconciler record the flat position as
    check_name='MANUAL_CLOSE' + exit_reason='MANUAL', the SAME signature as an
    operator-manual close and a kill-flatten. So MANUAL_CLOSE is a *detection* of an
    external close, NOT a reconciler repair; mapping it to RECON_CLOSE over-claims.
    Only STUCK_EXITING (reconciler finalises a stuck exit) is a true RECON_CLOSE.
    Until W8 adds a per-trade closure_source, daily-EOD / operator / RMS are
    genuinely indistinguishable → the truthful label is SYSTEM_CLOSE, not RECON_CLOSE.
    A cleanly closed SL/TGT trade carries NO reconciliation_log row, so it falls
    through to the exit_reason branches unaffected.
    """
    checks = {(r.get("check_name") or "").upper() for r in recon_rows}
    er = (trade.get("exit_reason") or "").upper()
    status = (trade.get("status") or "").upper()

    recon_close = checks & _RECON_INITIATED_CLOSE
    orphan = checks & _ORPHAN_CHECKS
    eod_pos = _is_eod_positive(er)

    # RECON_EOD_CLOSE only when BOTH independently identifiable (reconciler-initiated
    # AND a positive EOD marker). Never invented from a generic MANUAL_CLOSE.
    if recon_close and eod_pos:
        return "RECON_EOD_CLOSE", ("reconciliation_log.check_name=" + ",".join(sorted(recon_close))
                                   + f" + exit_reason={er}")
    if recon_close:
        return "RECON_CLOSE", ("reconciliation_log.check_name=" + ",".join(sorted(recon_close))
                               + " (reconciler-INITIATED finalize)")
    if orphan:
        return "ORPHAN_RECOVERY", "reconciliation_log.check_name=" + ",".join(sorted(orphan))
    if status.startswith("REJECTED") or status == "CANCELLED":
        return "—", f"status={status} (no position opened)"
    if eod_pos:  # positive EOD marker wins over a co-occurring MANUAL_CLOSE (bookkeeping)
        note = " (+MANUAL_CLOSE recon = EOD bookkeeping)" if _EXTERNAL_CLOSE_DETECT & checks else ""
        return "EOD_SQUAREOFF", f"exit_reason={er}{note}"
    if (_EXTERNAL_CLOSE_DETECT & checks) or "MANUAL" in er or status == "CLOSED_MANUAL":
        # daily-EOD squareoff / operator-manual / RMS / kill-flatten collapse — trigger
        # not separately recorded (all write exit_reason='MANUAL' + check_name='MANUAL_CLOSE').
        return "SYSTEM_CLOSE", ("external close via manual/system path (daily-EOD / operator / RMS); "
                                "trigger not separately recorded — see W8 "
                                f"[status={status or 'NULL'}/exit_reason={er or 'NULL'}"
                                + ("/MANUAL_CLOSE" if _EXTERNAL_CLOSE_DETECT & checks else "") + "]")
    if er in _SL_REASONS:
        return "SL", f"exit_reason={er}"
    if er == "TGT_HIT":
        return "TGT", "exit_reason=TGT_HIT"
    if er.startswith("GTT"):
        # CNC overnight OCO fired (SL or TGT side not recorded in exit_reason).
        return "UNKNOWN", f"exit_reason={er} (delivery OCO; side not stored)"
    if status in _OPEN_STATUSES:
        return "—", "still open / in-flight"
    return "UNKNOWN", f"status={status or 'NULL'}/exit_reason={er or 'NULL'}"


# ═════════════════════════════════════════════════════════════════════════════════
# Data loading (DB-pure) + per-trade record build
# ═════════════════════════════════════════════════════════════════════════════════

def _load_min_score(store: StateStore, date_iso: str) -> Optional[int]:
    """Global scoring.min_pass_score from the day's config snapshot (W0). None if
    no snapshot exists for the date (pre-W0 history)."""
    row = store.fetch_one(
        "SELECT config_json FROM config_snapshots WHERE snapshot_date = ? "
        "ORDER BY snapshot_ts DESC LIMIT 1", (date_iso,))
    if not row:
        return None
    try:
        cfg = json.loads(row["config_json"])
        return cfg.get("scoring", {}).get("min_pass_score")
    except (TypeError, ValueError, KeyError):
        return None


def _pick_order(orders: List[Dict[str, Any]], legs: Tuple[str, ...],
                *, filled: bool = False) -> Optional[Dict[str, Any]]:
    cand = [o for o in orders if (o.get("leg") or "") in legs]
    if filled:
        cand = [o for o in cand if (o.get("status") or "") == "COMPLETE"]
    if not cand:
        return None
    return sorted(cand, key=lambda o: o.get("placed_at") or "")[0]


def build_records(store: StateStore, date_iso: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return (records, meta). One record per trade created on date_iso."""
    trades = store.get_trades_for_date(date_iso)
    orders = store.get_orders_for_date(date_iso)
    signals = {s["signal_id"]: s for s in store.get_signals_for_date(date_iso)}
    recon = store.get_reconciliation_log_for_date(date_iso)
    excursions = {e["trade_id"]: e for e in store.get_trade_excursions_for_date(date_iso)}
    slip = {r["trade_id"]: dict(r) for r in store.fetch_all(
        "SELECT * FROM trade_slippage_log WHERE trade_date = ?", (date_iso,))}

    score_by_sig: Dict[str, Any] = {}
    for r in store.get_screener_results_for_date(date_iso):
        sid, sc = r["signal_id"], _f(r.get("score"))
        if sid not in score_by_sig or (sc or -1) > (score_by_sig[sid] or -1):
            score_by_sig[sid] = sc

    sig_ids = [t["signal_id"] for t in trades if t.get("signal_id")]
    sr: Dict[str, Dict[str, Any]] = {}
    if sig_ids:
        ph = ",".join("?" * len(sig_ids))
        for r in store.fetch_all(
                f"SELECT * FROM sr_detector_results WHERE signal_id IN ({ph}) "
                f"ORDER BY created_at", tuple(sig_ids)):
            sr.setdefault(r["signal_id"], dict(r))

    orders_by_trade: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for o in orders:
        orders_by_trade[o["trade_id"]].append(o)
    recon_by_trade: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in recon:
        if r.get("trade_id"):
            recon_by_trade[r["trade_id"]].append(r)

    min_score = _load_min_score(store, date_iso)

    records: List[Dict[str, Any]] = []
    for seq, t in enumerate(sorted(trades, key=lambda x: x.get("created_at") or ""), start=1):
        tid, sid = t["trade_id"], t.get("signal_id")
        sig = signals.get(sid, {})
        tos = orders_by_trade.get(tid, [])
        entry_o = _pick_order(tos, ("ENTRY", "CO"))
        sl_o = _pick_order(tos, ("SL",))
        tgt_o = _pick_order(tos, ("TGT",))
        sl_fill_o = _pick_order(tos, ("SL",), filled=True)
        tgt_fill_o = _pick_order(tos, ("TGT",), filled=True)
        sp = slip.get(tid, {})
        exc = excursions.get(tid, {})
        srr = sr.get(sid, {})
        closure, evidence = classify_closure(t, recon_by_trade.get(tid, []))

        entry = _f(t.get("entry_target_price"))
        sl = _f(t.get("sl_initial"))
        tgt = _f(t.get("tgt_initial"))
        qtyp = _f(t.get("qty_planned"))
        net = _f(t.get("net_pnl"))
        margin = _f(t.get("margin_reserved"))
        reward = (qtyp * abs(tgt - entry)) if (qtyp and entry is not None and tgt is not None) else None
        planned_rr = _f(t.get("tgt_risk_reward_applied"))
        if planned_rr is None and entry is not None and sl is not None and tgt is not None and entry != sl:
            planned_rr = round(abs(tgt - entry) / abs(entry - sl), 2)
        sl_pct = round(abs(entry - sl) / entry * 100, 2) if entry and sl is not None else None
        tgt_pct = round(abs(tgt - entry) / entry * 100, 2) if entry and tgt is not None else None
        roi_pct = round(net / margin * 100, 2) if net is not None and margin else None

        # Slippage
        actual_slip = _f(sp.get("entry_slippage_rs"))
        allowed_slip = None
        tol = _f(t.get("tolerance_fraction_used"))
        psd = _f(sp.get("planned_sl_distance"))
        if tol is not None and psd is not None:
            allowed_slip = round(tol * psd, 4)
        slip_delta = (round(actual_slip - allowed_slip, 4)
                      if actual_slip is not None and allowed_slip is not None else None)
        qf = _f(t.get("qty_filled"))
        slip_cost = round(actual_slip * qf, 2) if actual_slip is not None and qf else None

        # Quality (excursions sparse)
        mfe, mae = _f(exc.get("mfe_pct")), _f(exc.get("mae_pct"))
        filled_entry = _f(t.get("entry_actual_price"))
        exit_px = _f(t.get("exit_price"))
        mfe_cap = None
        if (mfe is not None and filled_entry and exit_px is not None
                and exc.get("mfe_price") is not None):
            mfe_px = _f(exc.get("mfe_price"))
            denom = mfe_px - filled_entry
            if denom:
                mfe_cap = round((exit_px - filled_entry) / denom * 100, 2)
        rr_achieved = _f(sp.get("actual_rr"))
        efficiency = mfe_cap
        has_exc = bool(exc)

        # Freshness (FLAG 3) — honest composite
        fresh_parts = []
        if has_exc:
            fresh_parts.append("MFE:RECON")
        if srr:
            fresh_parts.append("S&R:" + ("BACKFILL" if srr.get("actual_result") is not None else "DETECT"))
        data_freshness = " · ".join(fresh_parts) if fresh_parts else NA_NO_DATA

        # Trade type from entry order product
        prod = (entry_o or {}).get("product") or ""
        trade_type = {"MIS": "INTRADAY", "CO": "INTRADAY", "CNC": "DELIVERY"}.get(prod, prod or "")

        # Trade result
        status = (t.get("status") or "")
        if net is None or status in _OPEN_STATUSES:
            trade_result = ""
        elif net > 0:
            trade_result = "WIN"
        elif net < 0:
            trade_result = "LOSS"
        else:
            trade_result = "BREAKEVEN"

        # Remarks: closure evidence + notable flags
        remarks = [evidence]
        if t.get("recovered_flag"):
            remarks.append("recovered=1")
        if t.get("exits_verified") == 0:
            remarks.append(f"exits_mismatch:{t.get('exits_verify_detail')}")
        if t.get("needs_tgt_retry"):
            remarks.append(f"tgt_retry x{t.get('tgt_retry_count')}")

        rec = {
            # A
            "seq": seq, "date": (t.get("created_at") or "")[:10],
            "signal_time": _fmt_time(sig.get("triggered_at")),
            "vm_receipt": _fmt_time(sig.get("received_at")),
            "symbol": t.get("symbol"), "strategy": t.get("strategy"),
            "trade_type": trade_type, "direction": t.get("direction"),
            "signal_score": score_by_sig.get(sid), "min_score": min_score if min_score is not None else NA_NO_SNAP,
            # B
            "qty_planned": t.get("qty_planned"), "sys_entry": entry, "sys_sl": sl, "sys_tgt": tgt,
            "risk_amt": _f(t.get("risk_amount")), "reward_amt": reward, "planned_rr": planned_rr,
            "sl_pct": sl_pct, "tgt_pct": tgt_pct, "capital_consumed": margin,
            "broker_margin": PENDING_MARGIN,
            # C
            "entry_placed": _fmt_time((entry_o or {}).get("placed_at")),
            "entry_filled": _fmt_time(t.get("entry_time")),
            "sl_created": _fmt_time((sl_o or {}).get("placed_at")),
            "tgt_created": _fmt_time((tgt_o or {}).get("placed_at")),
            "exit_trigger": PENDING_EXIT_TRIGGER, "exit_time": _fmt_time(t.get("exit_time")),
            "duration_min": _minutes_between(t.get("entry_time"), t.get("exit_time")),
            "entry_delay_s": (round(_f(t.get("order_to_fill_ms")) / 1000.0, 2)
                              if _f(t.get("order_to_fill_ms")) is not None else None),
            "exit_delay_s": PENDING_EXIT_TRIGGER,
            # D
            "qty_filled": t.get("qty_filled"), "filled_entry": filled_entry,
            "filled_sl": _f((sl_fill_o or {}).get("avg_fill_price")) or _f(sp.get("sl_fill_price")),
            "filled_tgt": _f((tgt_fill_o or {}).get("avg_fill_price")) or _f(sp.get("tgt_fill_price")),
            "filled_exit": exit_px, "exit_reason": t.get("exit_reason") or "", "closure_type": closure,
            # E
            "gross": _f(t.get("gross_pnl")), "cost_brokerage": _f(t.get("cost_brokerage")),
            "cost_exchange": _f(t.get("cost_exchange_txn")), "cost_stt": _f(t.get("cost_stt")),
            "cost_gst": _f(t.get("cost_gst")), "cost_sebi": _f(t.get("cost_sebi")),
            "cost_stamp": _f(t.get("cost_stamp_duty")), "total_charges": _f(t.get("charges")),
            "net": net, "roi_pct": roi_pct,
            # F
            "price_band": sp.get("price_band") or (NA_NO_DATA if not sp else ""),
            "allowed_slip": allowed_slip, "actual_slip": actual_slip, "slip_delta": slip_delta,
            "slip_pct": _f(sp.get("entry_slippage_pct")), "slip_cost": slip_cost,
            "rr_damage": _f(sp.get("rr_damage_pct")),
            # G
            "mae": mae, "mfe": mfe, "mfe_cap": mfe_cap, "rr_achieved": rr_achieved,
            "efficiency": efficiency, "grade": _grade(efficiency),
            # H
            "near_support": _fmt_zone(srr.get("nearest_support_zone"),
                                      srr.get("dist_to_support_pct"), srr.get("support_confidence"))
                            if srr else NA_NO_DATA,
            "near_resistance": _fmt_zone(srr.get("nearest_resistance_zone"),
                                         srr.get("dist_to_resistance_pct"), srr.get("resistance_confidence"))
                               if srr else NA_NO_DATA,
            # I
            "trade_status": status, "trade_result": trade_result, "data_freshness": data_freshness,
            "remarks": "; ".join(x for x in remarks if x), "trade_id": tid,
            "broker_order_id": (entry_o or {}).get("order_id") or "",
            "exchange_order_id": PENDING_EXCH_ORDER,
            # non-rendered helpers for conditional formatting
            "_net_raw": net, "_slip_over": (slip_delta is not None and slip_delta > 0),
            "_mismatch": (t.get("exits_verified") == 0 or closure == "UNKNOWN"
                          or any((r.get("check_name") or "") in _ORPHAN_CHECKS
                                 for r in recon_by_trade.get(tid, []))),
        }
        records.append(rec)

    mode = ""
    if trades:
        modes = {(t.get("mode") or "").upper() for t in trades if t.get("mode")}
        mode = "/".join(sorted(modes)) if modes else ""
    meta = {"date": date_iso, "mode": mode, "n": len(records),
            "min_score": min_score, "has_config_snapshot": min_score is not None}
    return records, meta


# ═════════════════════════════════════════════════════════════════════════════════
# Rendering (openpyxl) — 3-row header, freeze, outline groups, conditional fmt, totals
# ═════════════════════════════════════════════════════════════════════════════════

def _num_format(fmt: str) -> Optional[str]:
    return {"int": NUM_FMT_INTEGER, "money": NUM_FMT_CURRENCY, "num2": "0.00"}.get(fmt)


def _align(fmt: str):
    return ALIGN_LEFT if fmt in ("text", "time") else ALIGN_RIGHT


def render_orders_sheet(wb: openpyxl.Workbook, records: List[Dict[str, Any]], meta: Dict[str, Any]) -> None:
    ws = wb.create_sheet(title="Orders")
    ws.sheet_view.showGridLines = False
    n_cols = len(_COLSPECS)
    last_col = get_column_letter(n_cols)

    # Row 1: title
    ws.merge_cells(f"A1:{last_col}1")
    tc = ws.cell(1, 1, value=(
        f"ORDERS — Forensic Trade Master   |   {meta['date']}   |   "
        f"mode={meta['mode'] or 'n/a'}   |   {meta['n']} trades"
        + ("" if meta["has_config_snapshot"] else "   |   (no config_snapshot for date — Min Score pending W0)")))
    tc.font = FONT_TITLE
    tc.alignment = ALIGN_LEFT

    # Row 2: group bands (merge contiguous same-group columns) + Row 3: headers
    gi = 0
    for g, cols in _group_spans():
        c0, c1 = cols[0] + 1, cols[-1] + 1
        ws.merge_cells(start_row=2, start_column=c0, end_row=2, end_column=c1)
        gc = ws.cell(2, c0, value=_GROUP_TITLES[g])
        gc.fill = _GROUP_FILLS[g]
        gc.font = FONT_WHITE_BOLD
        gc.alignment = ALIGN_CENTER
        gc.border = BORDER_ALL
        gi += 1
    for idx, (g, header, key, fmt, width) in enumerate(_COLSPECS, start=1):
        c = ws.cell(3, idx, value=header)
        c.font = FONT_HEADER
        c.fill = _GROUP_FILLS[g]
        c.font = Font(name="Arial", size=9, bold=True, color="FFFFFF")
        c.alignment = ALIGN_CENTER
        c.border = BORDER_ALL
        ws.column_dimensions[get_column_letter(idx)].width = width

    # Data rows
    r = 4
    for rec in records:
        row_tint = None
        if rec["closure_type"] == "SL":
            row_tint = FILL_RED
        elif rec["closure_type"] == "TGT":
            row_tint = FILL_GREEN
        for idx, (g, header, key, fmt, width) in enumerate(_COLSPECS, start=1):
            val = rec.get(key)
            cell = ws.cell(r, idx, value=val)
            cell.font = FONT_BODY
            cell.alignment = _align(fmt)
            cell.border = BORDER_ALL
            nf = _num_format(fmt)
            if nf and isinstance(val, (int, float)):
                cell.number_format = nf
            if row_tint is not None:
                cell.fill = row_tint
        # conditional overrides (after row tint)
        _paint(ws, r, "net", FILL_GREEN if (rec["_net_raw"] or 0) > 0 else FILL_RED,
               font=Font(name="Arial", size=9, color=("006100" if (rec["_net_raw"] or 0) > 0 else "9C0006"), bold=True),
               when=rec["_net_raw"] is not None)
        _paint(ws, r, "actual_slip", FILL_AMBER, when=rec["_slip_over"])
        _paint(ws, r, "slip_delta", FILL_AMBER, when=rec["_slip_over"])
        # non-SL/TGT closes (EOD/SYSTEM/RECON) → neutral amber; anomalies (UNKNOWN/orphan) → red (wins)
        _paint(ws, r, "closure_type", FILL_AMBER, when=(rec["closure_type"] in _OTHER_CLOSE_TYPES))
        _paint(ws, r, "closure_type", FILL_RED,
               font=Font(name="Arial", size=9, color="9C0006", bold=True), when=rec["_mismatch"])
        r += 1

    # TOTALS row
    tot = ws.cell(r, 1, value="TOTALS")
    tot.font = Font(name="Arial", size=9, bold=True)
    tot.fill = FILL_GREY
    seq_col = _key_col("seq")
    ws.cell(r, _key_col("symbol"), value=f"{meta['n']} trades").font = Font(name="Arial", size=9, bold=True)
    for idx, (g, header, key, fmt, width) in enumerate(_COLSPECS, start=1):
        cell = ws.cell(r, idx)
        cell.fill = FILL_GREY
        cell.border = BORDER_ALL
        if key in _TOTAL_KEYS:
            s = sum((rec.get(key) or 0) for rec in records if isinstance(rec.get(key), (int, float)))
            cell.value = round(s, 2)
            cell.font = Font(name="Arial", size=9, bold=True)
            cell.alignment = ALIGN_RIGHT
            nf = _num_format(fmt)
            if nf:
                cell.number_format = nf

    # Freeze: header rows 1-3 + identity columns through 'Symbol'
    freeze_col = get_column_letter(_key_col("symbol") + 1)
    ws.freeze_panes = f"{freeze_col}4"

    # Excel OUTLINE groups per B..I (collapsible); group A stays visible (identity/frozen)
    ws.sheet_properties.outlinePr.summaryRight = False
    for g, cols in _group_spans():
        if g == "A":
            continue
        try:
            ws.column_dimensions.group(get_column_letter(cols[0] + 1),
                                       get_column_letter(cols[-1] + 1),
                                       outline_level=1, hidden=False)
        except Exception as exc:  # noqa: BLE001
            _log.warning("outline group %s failed: %s", g, exc)


def _group_spans() -> List[Tuple[str, List[int]]]:
    """Ordered [(group, [0-based col indices])] for contiguous same-group runs."""
    spans: List[Tuple[str, List[int]]] = []
    for i, (g, *_rest) in enumerate(_COLSPECS):
        if spans and spans[-1][0] == g:
            spans[-1][1].append(i)
        else:
            spans.append((g, [i]))
    return spans


def _key_col(key: str) -> int:
    for i, (_g, _h, k, *_r) in enumerate(_COLSPECS, start=1):
        if k == key:
            return i
    raise KeyError(key)


def _paint(ws, row: int, key: str, fill, *, font=None, when: bool = True) -> None:
    if not when:
        return
    cell = ws.cell(row, _key_col(key))
    cell.fill = fill
    if font is not None:
        cell.font = font


# ═════════════════════════════════════════════════════════════════════════════════
# SHEET 2 — SIGNALS  (one row per signal that REACHED STORAGE, post webhook-dedup)
#
# HONEST BASIS (verified 01-Jul, code + real data): the per-signal denominator is
# "signals persisted in the `signals` table". Duplicates are dropped at the webhook
# TTLCache/fingerprint dedup BEFORE any row is written (webhook_receiver returns
# status='DUPLICATE' with no INSERT), so they have NO per-signal row — only the
# aggregate webhook_audit.signals_rejected count. Score/secondary/capital REJECTS
# are stored (a QUEUED row updated to REJECTED_*). So:
#   • Received (all POST-level)  = SUM(webhook_audit.signals_accepted+signals_rejected)  [aggregate]
#   • Reached storage (accepted) = COUNT(signals)  == SUM(webhook_audit.signals_accepted)
#   • Dropped pre-storage        = SUM(signals_rejected)  [dupes + invalid/out-of-window/backpressure;
#                                   per-signal detail NOT captured → capture follow-up W9]
# The sheet's rows + Δ=0 identity are at the STORAGE level (Received_stored =
# Qualified + Rejected + Skipped/Other). The webhook drop is shown as CONTEXT — no
# fabricated per-signal duplicate rows, no fake Received=Q+R+Dup identity.
# ═════════════════════════════════════════════════════════════════════════════════

# Vocabulary + bucketing moved to reports/signal_status.py so daily_report.py and this file
# share ONE implementation (they previously classified signal statuses independently, and
# daily_report's copy did it by substring-matching the free-text rejection_reason). Aliased
# here to keep this module's call sites unchanged.
_QUALIFIED_STATUSES = sig_status.QUALIFIED_STATUSES
_KNOWN_OTHER_STATUSES = sig_status.KNOWN_OTHER_STATUSES
_signal_bucket = sig_status.bucket


def _signal_stage(status: str, has_screener: bool, has_trade: bool) -> str:
    """Derive the pipeline stage a signal reached:
    RECEIVED → SCORED → SECONDARY_FILTER → CAPITAL_CHECK → ORDERABLE → ORDER_CREATED."""
    s = (status or "").upper()
    if has_trade or s in {"PROCESSED", "TRADED"}:
        return "ORDER_CREATED"
    if s == "PLACEMENT_FAILED":
        return "ORDERABLE"
    if any(k in s for k in ("SIZING", "OPEN_POSITIONS", "DAILY_TRADES",
                            "CONSECUTIVE_LOSSES", "ENTRY_THROTTLED", "STRATEGY_POSITION",
                            "STRATEGY_CIRCUIT")):
        return "CAPITAL_CHECK"
    if any(k in s for k in ("CIRCUIT_PROXIMITY", "QUOTE_UNAVAILABLE", "DUPLICATE_SYMBOL",
                            "LIQUIDITY", "SPREAD")) or s.startswith("SKIPPED_"):
        return "SECONDARY_FILTER"
    if "SCORE" in s:
        return "SCORED"
    if has_screener:
        return "SCORED"
    return "RECEIVED"   # OUTSIDE_ENTRY_WINDOW / SHADOW_INNING / EXPIRED / QUEUED / ...


_SIGNAL_COLSPECS: List[Tuple[str, str, str, float]] = [
    ("Date", "date", "text", 11), ("Signal Time", "signal_time", "time", 10),
    ("VM Receipt", "vm_receipt", "time", 10), ("Processing ms", "processing_ms", "num2", 11),
    ("Symbol", "symbol", "text", 12), ("Strategy", "strategy", "text", 16),
    ("Trade Type", "trade_type", "text", 10), ("Dir", "direction", "text", 6),
    ("Score", "score", "int", 7), ("Min Score", "min_score", "text", 9),
    ("Score Δ", "score_delta", "num2", 8), ("Qualified", "qualified", "text", 9),
    ("Rejected", "rejected", "text", 9), ("Duplicate", "duplicate", "text", 9),
    ("Rejection Reason", "rejection_reason", "text", 22),
    ("Secondary Filter", "secondary_filter", "text", 18),
    ("Capital Check", "capital_check", "text", 18), ("Final Decision", "final_decision", "text", 14),
    ("Stage Reached", "stage", "text", 16), ("Signal ID", "signal_id", "text", 24),
    ("Trade ID", "trade_id", "text", 24),
]


def _latency_ms(latencies_json: Any) -> Optional[float]:
    """Best-effort total screening latency (ms) from screener_results.latencies JSON."""
    if not latencies_json:
        return None
    try:
        d = json.loads(latencies_json) if isinstance(latencies_json, str) else latencies_json
    except (TypeError, ValueError):
        return None
    if isinstance(d, dict):
        if "total_ms" in d and isinstance(d["total_ms"], (int, float)):
            return round(float(d["total_ms"]), 1)
        nums = [v for v in d.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        return round(sum(nums), 1) if nums else None
    if isinstance(d, (int, float)):
        return round(float(d), 1)
    return None


def build_signal_records(store: StateStore, date_iso: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    signals = store.get_signals_for_date(date_iso)
    trades = store.get_trades_for_date(date_iso)
    orders = store.get_orders_for_date(date_iso)
    min_score = _load_min_score(store, date_iso)

    # one screener row per signal (prefer PASSED, else highest score)
    scr: Dict[str, Dict[str, Any]] = {}
    for r in store.get_screener_results_for_date(date_iso):
        sid = r["signal_id"]
        prev = scr.get(sid)
        if (prev is None or r.get("status") == "PASSED"
                or (_f(r.get("score")) or -1) > (_f(prev.get("score")) or -1)):
            scr[sid] = r
    trade_by_sig = {t["signal_id"]: t for t in trades}
    entry_prod: Dict[str, str] = {}
    for o in orders:
        if o.get("leg") in ("ENTRY", "CO") and o["trade_id"] not in entry_prod:
            entry_prod[o["trade_id"]] = o.get("product")

    wa = store.fetch_one(
        "SELECT COUNT(*) posts, COALESCE(SUM(signals_accepted),0) acc, "
        "COALESCE(SUM(signals_rejected),0) rej FROM webhook_audit WHERE date = ?", (date_iso,))
    wh_acc = int(wa["acc"]) if wa else 0
    wh_rej = int(wa["rej"]) if wa else 0

    buckets: Dict[str, int] = defaultdict(int)
    dup_stored = 0
    records: List[Dict[str, Any]] = []
    for s in sorted(signals, key=lambda x: x.get("received_at") or ""):
        sid = s["signal_id"]
        status = (s.get("status") or "")
        su = status.upper()
        sr = scr.get(sid, {})
        tr = trade_by_sig.get(sid)
        bucket = _signal_bucket(status)
        buckets[bucket] += 1
        is_dup = (su == "DUPLICATE" or "DUPLICATE" in su)
        if is_dup:
            dup_stored += 1

        score = _f(sr.get("score"))
        score_delta = (round(score - min_score, 2)
                       if score is not None and min_score is not None else None)
        stage = _signal_stage(status, bool(sr), tr is not None)
        prod = entry_prod.get(tr["trade_id"]) if tr else None
        trade_type = {"MIS": "INTRADAY", "CO": "INTRADAY", "CNC": "DELIVERY"}.get(prod, "—") if tr else "—"

        # Secondary filter verdict (the screener's own status), else not-reached
        secondary = sr.get("status") if sr else ("N/A — no data" if bucket == "rejected"
                                                 and stage == "RECEIVED" else "not reached")
        # Capital check result (derived from where it stopped)
        if stage in ("ORDERABLE", "ORDER_CREATED"):
            capital = "PASSED"
        elif stage == "CAPITAL_CHECK":
            capital = f"REJECTED: {status}"
        else:
            capital = "not reached"
        final = {"qualified": ("TRADED" if tr else "ORDER_FAILED" if su == "PLACEMENT_FAILED" else "QUALIFIED"),
                 "rejected": "REJECTED", "skipped": "SKIPPED", "dropped": "DROPPED"}.get(bucket, status or "—")

        records.append({
            "date": (s.get("received_at") or "")[:10],
            "signal_time": _fmt_time(s.get("triggered_at")),
            "vm_receipt": _fmt_time(s.get("received_at")),
            "processing_ms": _latency_ms(sr.get("latencies")),
            "symbol": s.get("symbol"), "strategy": s.get("strategy"),
            "trade_type": trade_type, "direction": (tr.get("direction") if tr else "—"),
            "score": score if score is not None else ("N/A — no data" if not sr else None),
            "min_score": min_score if min_score is not None else NA_NO_SNAP,
            "score_delta": score_delta,
            "qualified": "Y" if bucket == "qualified" else "N",
            "rejected": "Y" if bucket == "rejected" else "N",
            "duplicate": "Y" if is_dup else "N",
            "rejection_reason": (s.get("rejection_reason") or (status if bucket in ("rejected", "skipped") else "")),
            "secondary_filter": secondary, "capital_check": capital, "final_decision": final,
            "stage": stage, "signal_id": sid, "trade_id": (s.get("trade_id") or ""),
            "_bucket": bucket,
        })

    stored = len(records)
    traded = sum(1 for r in records if r.get("trade_id"))
    meta = {
        "date": date_iso, "n": stored, "min_score": min_score,
        "has_config_snapshot": min_score is not None,
        "qualified": buckets.get("qualified", 0), "rejected": buckets.get("rejected", 0),
        "skipped": buckets.get("skipped", 0), "dropped": buckets.get("dropped", 0),
        "other": buckets.get("other", 0), "unmapped": buckets.get("unmapped", 0),
        "traded": traded, "dup_stored": dup_stored,
        "wh_received": wh_acc + wh_rej, "wh_accepted": wh_acc, "wh_dropped": wh_rej,
    }
    return records, meta


def render_signals_sheet(wb: openpyxl.Workbook, records: List[Dict[str, Any]], meta: Dict[str, Any]) -> None:
    ws = wb.create_sheet(title="Signals")
    ws.sheet_view.showGridLines = False
    ncol = len(_SIGNAL_COLSPECS)
    last = get_column_letter(ncol)

    ws.merge_cells(f"A1:{last}1")
    t = ws.cell(1, 1, value=("SIGNALS — basis: one row per signal that REACHED STORAGE "
                             f"(persisted, post webhook-dedup)   |   {meta['date']}   |   {meta['n']} stored signals"))
    t.font = FONT_TITLE
    t.alignment = ALIGN_LEFT

    # Reconciliation totals block
    def kv(r, label, value, fill=None, bold=True):
        c1 = ws.cell(r, 1, value=label)
        c1.font = Font(name="Arial", size=9, bold=bold)
        c2 = ws.cell(r, 3, value=value)
        c2.font = Font(name="Arial", size=9, bold=bold)
        c2.alignment = ALIGN_LEFT
        if fill is not None:
            c1.fill = fill
            c2.fill = fill

    pct = round(meta["wh_accepted"] / meta["wh_received"] * 100, 1) if meta["wh_received"] else 0.0
    ws.cell(3, 1, value="FUNNEL  (received → storage → qualified → traded):").font = FONT_HEADER
    kv(4, "  Received  (webhook POST-level)", meta["wh_received"])
    kv(5, f"  → Reached storage  (accepted, {pct}% of received)", meta["wh_accepted"], fill=FILL_GREY)
    kv(6, "  → Qualified  (passed all gates)", meta["qualified"], fill=FILL_GREEN)
    kv(7, "  → Traded  (order created)", meta["traded"], fill=FILL_GREEN)
    ws.cell(9, 1, value="STORAGE-LEVEL breakdown (this sheet's rows — the honest Δ=0 partition):").font = FONT_HEADER
    kv(10, "  Stored", meta["n"])
    kv(11, "  Rejected", meta["rejected"], fill=FILL_AMBER)
    kv(12, "  Skipped / Other", meta["skipped"] + meta["other"])
    delta = meta["n"] - (meta["qualified"] + meta["rejected"] + meta["skipped"]
                         + meta["other"] + meta["dropped"])   # excludes 'unmapped' → Δ = #unmapped
    kv(13, "  Δ  (Stored − Σ known buckets; must be 0)", delta,
       fill=(FILL_GREEN if delta == 0 else FILL_RED))
    kv(14, "  Dropped pre-storage  (webhook; per-signal NOT captured)",
       f"{meta['wh_dropped']}   [W9]", fill=FILL_GREY)
    ws.cell(15, 1, value=("  (Webhook dupes are dropped pre-storage — no per-signal rows; the full "
                          "received = Qualified+Rejected+Duplicate identity awaits W9. "
                          + ("No config_snapshot for this date → Min Score/Score Δ pending W0."
                             if not meta["has_config_snapshot"] else ""))
            ).font = Font(name="Arial", size=8, italic=True)

    hdr_row = 17
    for idx, (header, key, fmt, width) in enumerate(_SIGNAL_COLSPECS, start=1):
        c = ws.cell(hdr_row, idx, value=header)
        c.font = Font(name="Arial", size=9, bold=True, color="FFFFFF")
        c.fill = FILL_HEADER
        c.alignment = ALIGN_CENTER
        c.border = BORDER_ALL
        ws.column_dimensions[get_column_letter(idx)].width = width

    r = hdr_row + 1
    _bucket_fill = {"qualified": FILL_GREEN, "rejected": FILL_AMBER, "skipped": FILL_GREY, "dropped": FILL_GREY}
    for rec in records:
        tint = FILL_GREY if rec["duplicate"] == "Y" else _bucket_fill.get(rec["_bucket"])
        for idx, (header, key, fmt, width) in enumerate(_SIGNAL_COLSPECS, start=1):
            val = rec.get(key)
            cell = ws.cell(r, idx, value=val)
            cell.font = FONT_BODY
            cell.alignment = _align(fmt)
            cell.border = BORDER_ALL
            nf = _num_format(fmt)
            if nf and isinstance(val, (int, float)):
                cell.number_format = nf
            if tint is not None:
                cell.fill = tint
        r += 1

    ws.freeze_panes = f"A{hdr_row + 1}"


# ═════════════════════════════════════════════════════════════════════════════════
# SHEET 3 — RECONCILIATION  (the integrity backbone: per-identity PASS/FAIL/PENDING)
#
# Never fake PASS, never fake FAIL, never compute outside the DB. Each block states
# an identity, its two sides from the DB, and a status. Partition blocks (1,2) FAIL
# on an UNMAPPED status (schema drift). The CAPITAL block is a real cross-source
# check (fm_ledger RELEASE_USED.pnl_delta vs Σ trades.net_pnl). The BROKER block is
# PENDING_CAPTURE (broker P&L/positions/margin are not persisted → W2/W3) — never FAIL.
#
# M-R4 (14-Jul-2026): the old "2 · ORDER" block (qualified = order_placed_ok +
# placement_failed) was DELETED and the remaining blocks renumbered. Its RHS was derived
# from its LHS (placement_failed ≡ qualified − placed_ok), so RHS == LHS identically and it
# could NEVER FAIL — it compared the trade list to itself and printed "PASS/verified" while
# verifying nothing. A block that manufactures false confidence is worse than no block.
# Genuine signal→order reconciliation needs an INDEPENDENT external source (the broker's
# full-day order count); that folds into 4 · BROKER once the P1 feeder is authoritative.
# Until then this is an honest gap, not a fake check.
# ═════════════════════════════════════════════════════════════════════════════════

_CAPITAL_DRIFT_TOLERANCE = 1.0   # ₹ — ledger-vs-trades realized P&L must match within this


def _trade_bucket(status: str) -> str:
    # Reconciliation Block-2 is a STATUS partition of every trade record. Its "entered"
    # class = the order reached a live-or-closed position status (open/partial/exiting/
    # closed/closed_manual). FIX 3 (Phase-B.1): renamed from "filled" so the word
    # "filled" has ONE meaning report-wide — the Dashboard's qty-based execution metric
    # (qty_filled>0). A status=CLOSED_MANUAL record with qty_filled=0 (e.g. GICRE: entry
    # order CANCELLED, no shares) is "entered" here (status class) but NOT "filled" on the
    # Dashboard — two different, now-unambiguous questions, no shared label.
    s = (status or "").upper()
    if s in {"OPEN", "PARTIAL", "EXITING", "CLOSED", "CLOSED_MANUAL"}:
        return "entered"
    if s == "CANCELLED":
        return "cancelled"
    if s.startswith("REJECTED") or s in {"FAILED", "UNKNOWN_IN_FLIGHT"}:
        return "rejected_failed"
    if s in {"PENDING", "PENDING_FILL"}:
        return "pending"
    return "unmapped"


def build_reconciliation(store: StateStore, date_iso: str,
                         trade_records: List[Dict[str, Any]],
                         smeta: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    def _latest(sql: str, p: Tuple) -> str:
        r = store.fetch_one(sql, p)
        return _fmt_time(r[0]) if r and r[0] else "—"
    sig_at = _latest("SELECT MAX(received_at) FROM signals WHERE substr(received_at,1,10)=?", (date_iso,))
    trd_at = _latest("SELECT MAX(updated_at) FROM trades WHERE substr(created_at,1,10)=?", (date_iso,))
    led_at = _latest("SELECT MAX(ts) FROM fm_ledger WHERE date=?", (date_iso,))
    blocks: List[Dict[str, Any]] = []

    # 1 · SIGNAL-STORAGE  (partition; FAIL on unmapped status)
    stored = smeta["n"]
    rhs1 = smeta["qualified"] + smeta["rejected"] + smeta["skipped"] + smeta["other"] + smeta["dropped"]
    d1 = stored - rhs1   # == #unmapped
    pct = round(smeta["wh_accepted"] / smeta["wh_received"] * 100, 1) if smeta["wh_received"] else 0.0
    blocks.append({
        "name": "1 · SIGNAL-STORAGE", "identity": "stored = qualified + rejected + skipped + other",
        "lhs": stored, "rhs": rhs1, "status": "PASS" if d1 == 0 else "FAIL", "verified_at": sig_at,
        "detail": (f"Δ={d1} (unmapped statuses → 0). FUNNEL context: received(webhook)={smeta['wh_received']} "
                   f"→ stored={stored} ({pct}%); dropped pre-storage={smeta['wh_dropped']} = PENDING_CAPTURE "
                   f"(full received=Qual+Rej+Dup identity awaits W9)."),
    })

    # 2 · TRADE  (partition; FAIL on unmapped status)
    # NB: the former "2 · ORDER" block was DELETED (M-R4 — see the sheet header): its identity
    # was a tautology (RHS derived from LHS) so it could never FAIL. Order reconciliation needs
    # an external source; it folds into 4 · BROKER once the P1 feeder is authoritative.
    tb: Dict[str, int] = defaultdict(int)
    for t in trade_records:
        tb[_trade_bucket(t.get("trade_status"))] += 1
    placed = len(trade_records)
    rhs3 = tb["entered"] + tb["cancelled"] + tb["rejected_failed"] + tb["pending"]
    d3 = placed - rhs3   # == #unmapped
    blocks.append({
        "name": "2 · TRADE", "identity": "placed = entered + cancelled + rejected/failed + pending",
        "lhs": placed, "rhs": rhs3, "status": "PASS" if d3 == 0 else "FAIL", "verified_at": trd_at,
        "detail": (f"entered(open/partial/exiting/closed)={tb['entered']} cancelled={tb['cancelled']} "
                   f"rejected/failed={tb['rejected_failed']} pending={tb['pending']}; Δ={d3} (unmapped → 0). "
                   "NB: 'entered' = STATUS reached a position; distinct from the Dashboard's qty-based 'filled'."),
    })

    # 3 · CAPITAL  (opening + realized = closing; ledger realized == trades realized)
    o = store.fetch_one("SELECT balance_after FROM fm_ledger WHERE date=? AND entry_type='INIT' "
                        "ORDER BY ts LIMIT 1", (date_iso,))
    opening = _f(o["balance_after"]) if o else None
    lr = store.fetch_one("SELECT COALESCE(SUM(pnl_delta),0.0) r FROM fm_ledger "
                         "WHERE date=? AND entry_type='RELEASE_USED'", (date_iso,))
    ledger_realized = round(_f(lr["r"]) or 0.0, 2)
    # M-R3: key trades_realized by the CLOSE date, not created_at, so it lines up with the
    # ledger's close-date RELEASE_USED sum above. An overnight/CNC trade created on day D but
    # closed on D+1 realises its P&L on D+1 in the ledger; keying trades by created_at put it
    # on D -> a false "capital corruption" FAIL on the date seam. Reuse get_today_closed_pnl
    # (the M-K1-corrected close-date sum via substr(COALESCE(exit_time,updated_at))) rather
    # than duplicate the query. Byte-identical for the intraday-only book (created == closed
    # same day); correct once delivery/overnight trading is enabled.
    trades_realized = round(store.get_today_closed_pnl(date_iso), 2)
    drift = round(abs(ledger_realized - trades_realized), 2)
    if opening is None:
        cap_status, cap_detail = "PENDING_CAPTURE", "no fm_ledger INIT row (non-trading day / fresh DB)"
    else:
        closing = round(opening + ledger_realized, 2)
        cap_status = "PASS" if drift <= _CAPITAL_DRIFT_TOLERANCE else "FAIL"
        cap_detail = (f"opening={opening} + realized(ledger RELEASE_USED.pnl_delta)={ledger_realized} = closing={closing}. "
                      f"Cross-source drift |ledger − Σtrades.net_pnl({trades_realized})| = {drift} (tolerance ₹{_CAPITAL_DRIFT_TOLERANCE:.2f}). "
                      "NB: this block sums RELEASE_USED.pnl_delta directly (the clean trade-close realized), NOT "
                      "get_daily_realized_net_pnl — the latter sums ALL pnl_delta rows including the EOD RESET_PNL "
                      "counter-entry, which post-15:17 would zero the day's realized. (W10 — that reader's earlier "
                      "cost double-subtract — was fixed 2026-07-17; it now returns a clean SUM(pnl_delta).) "
                      "(The RESET_PNL ledger row is a by-design daily EOD reset, NOT pollution; RMS closes pass "
                      "costs=0.0 can drift — flagged, not hidden.)")
    blocks.append({
        "name": "3 · CAPITAL", "identity": "opening + realized_pnl = closing  (ledger == trades)",
        "lhs": ledger_realized, "rhs": trades_realized, "status": cap_status, "verified_at": led_at,
        "detail": cap_detail,
    })

    # 4 · BROKER  (pending capture — never FAIL; future home of external order reconciliation, M-R4)
    blocks.append({
        "name": "4 · BROKER", "identity": "system P&L = broker P&L · positions · margin",
        "lhs": "—", "rhs": "pending W2/W3", "status": "PENDING_CAPTURE", "verified_at": "—",
        "detail": "broker P&L / positions / margin are NOT persisted (in-memory only) — W2 (broker margin blocked) / W3 (pnl+position reconciliation wiring).",
    })

    statuses = [b["status"] for b in blocks]
    if "FAIL" in statuses:
        overall = "FAIL"
    elif "PENDING_CAPTURE" in statuses:
        overall = f"PASS — {statuses.count('PENDING_CAPTURE')} pending capture"
    else:
        overall = "PASS"
    return blocks, {"date": date_iso, "overall": overall,
                    "n_fail": statuses.count("FAIL"), "n_pending": statuses.count("PENDING_CAPTURE")}


def render_reconciliation_sheet(wb: openpyxl.Workbook, blocks: List[Dict[str, Any]], meta: Dict[str, Any]) -> None:
    ws = wb.create_sheet(title="Reconciliation")
    ws.sheet_view.showGridLines = False
    headers = ["Block", "Identity", "LHS", "RHS", "STATUS", "Verified-At", "Detail"]
    widths = [20, 48, 12, 16, 18, 12, 96]
    verdict = meta["overall"]
    vfill = FILL_RED if verdict == "FAIL" else (FILL_AMBER if "pending" in verdict else FILL_GREEN)
    _status_fill = {"PASS": FILL_GREEN, "FAIL": FILL_RED, "PENDING_CAPTURE": FILL_AMBER}

    ws.merge_cells("A1:G1")
    t = ws.cell(1, 1, value=f"RECONCILIATION — {meta['date']}      OVERALL: {verdict}")
    t.font = FONT_TITLE
    t.fill = vfill
    t.alignment = ALIGN_LEFT
    for i, h in enumerate(headers, start=1):
        c = ws.cell(3, i, value=h)
        c.font = FONT_WHITE_BOLD
        c.fill = FILL_HEADER
        c.alignment = ALIGN_CENTER
        c.border = BORDER_ALL
        ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
    r = 4
    for b in blocks:
        vals = [b["name"], b["identity"], b["lhs"], b["rhs"], b["status"], b["verified_at"], b["detail"]]
        for i, v in enumerate(vals, start=1):
            c = ws.cell(r, i, value=v)
            c.font = FONT_BODY
            c.border = BORDER_ALL
            c.alignment = ALIGN_LEFT if i in (1, 2, 7) else ALIGN_CENTER
            if isinstance(v, float):
                c.number_format = NUM_FMT_CURRENCY
            elif isinstance(v, int):
                c.number_format = NUM_FMT_INTEGER
        sc = ws.cell(r, 5)
        sc.fill = _status_fill.get(b["status"], FILL_GREY)
        sc.font = Font(name="Arial", size=9, bold=True)
        r += 1
    ov = ws.cell(r + 1, 1, value="OVERALL VERDICT")
    ov.font = Font(name="Arial", size=10, bold=True)
    ovc = ws.cell(r + 1, 5, value=verdict)
    ovc.fill = vfill
    ovc.font = Font(name="Arial", size=10, bold=True)
    ovc.alignment = ALIGN_CENTER
    ws.freeze_panes = "A4"


# ═════════════════════════════════════════════════════════════════════════════════
# SHEET 4 — CONFIG  (first consumer of W0 config_snapshots; DB-pure, reads ONLY the
# day's config_snapshots row — never YAML). Sectioned two-column key/value view of the
# resolved AppConfig AS IT WAS captured at that day's startup. Nested values
# (leverage_map, scoring weights, broker-cost rates, slippage tiers) render as indented
# sub-rows. No config snapshot for the date (pre-W0 history) → the whole sheet is an
# honest "— pending W0" placeholder, never blank.
#
# section → config_json path map (verified 01-Jul against the real load_all() dump):
#   SYSTEM       system.{trade_type,force_intraday_only,delivery_enabled}, system.trading_hours.*,
#                system.capital.leverage_map.*   (max_capital is NOT in config → runtime account balance)
#   RISK         system.risk.*  +  system.position_sizing.{max_position_value_pct,max_concentration_pct,risk_per_trade_pct}
#   SCORING      scoring.{min_pass_score,high/medium_score_threshold}  +  scoring.steps.*  (weights)
#   SLIPPAGE     slippage.{default_tier,tiers.*.slippage_bps}  +  system.entry_gate.{max_entry_slippage_pct,slippage_buffer}
#   BROKER COSTS broker_costs.zerodha.*  (equity rates; futures/options sub-dicts noted, not expanded)
#   STRATEGY     — pending W0.1 (per-strategy config is NOT in config_snapshots; strategies load via StrategyLoader)
# ═════════════════════════════════════════════════════════════════════════════════

NA_CFG_RUNTIME = "— not in config (runtime account balance)"

_CONFIG_SECTION_FILLS = {
    "SYSTEM": PatternFill("solid", fgColor="1F4E79"),
    "RISK": PatternFill("solid", fgColor="C00000"),
    "SCORING": PatternFill("solid", fgColor="548235"),
    "SLIPPAGE": PatternFill("solid", fgColor="BF8F00"),
    "BROKER COSTS": PatternFill("solid", fgColor="7030A0"),
    "STRATEGY": PatternFill("solid", fgColor="404040"),
}


def _cfg_get(d: Any, *keys: str, default: Any = None) -> Any:
    """Safely navigate a dotted path through nested dicts; default if any hop is absent."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _cfg_val(v: Any) -> Any:
    """Render a config value faithfully: bool→'True'/'False', None→'', numbers/str as-is."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "True" if v else "False"
    return v


def build_config_data(store: StateStore, date_iso: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return (sections, meta) for the day's config snapshot. sections is a list of
    {title, key, rows:[(label, value, indent)]}. meta.has_snapshot=False → render the
    pending-W0 placeholder (no config_snapshots row exists for this date)."""
    row = store.fetch_one(
        "SELECT snapshot_id, snapshot_date, snapshot_ts, account_id, mode, trade_type, "
        "config_hash, config_json FROM config_snapshots WHERE snapshot_date = ? "
        "ORDER BY snapshot_ts DESC LIMIT 1", (date_iso,))
    if not row:
        return [], {"date": date_iso, "has_snapshot": False}
    try:
        cfg = json.loads(row["config_json"])
    except (TypeError, ValueError):
        return [], {"date": date_iso, "has_snapshot": False, "parse_error": True}

    sysd = cfg.get("system", {}) or {}
    th = sysd.get("trading_hours", {}) or {}
    lev = _cfg_get(sysd, "capital", "leverage_map", default={}) or {}
    risk = sysd.get("risk", {}) or {}
    psz = sysd.get("position_sizing", {}) or {}
    egate = sysd.get("entry_gate", {}) or {}
    scoring = cfg.get("scoring", {}) or {}
    steps = scoring.get("steps", {}) or {}
    slip = cfg.get("slippage", {}) or {}
    tiers = slip.get("tiers", {}) or {}
    zc = _cfg_get(cfg, "broker_costs", "zerodha", default={}) or {}

    def kv(label: str, value: Any, indent: int = 0) -> Tuple[str, Any, int]:
        return (label, value, indent)

    sections: List[Dict[str, Any]] = []

    # 1 · SYSTEM
    sysrows = [
        kv("trade_type", sysd.get("trade_type")),
        kv("force_intraday_only", sysd.get("force_intraday_only")),
        kv("delivery_enabled", sysd.get("delivery_enabled")),
        kv("max_capital", NA_CFG_RUNTIME),
        kv("trading_hours", ""),
        kv("entry_start", th.get("entry_start"), 1),
        kv("entry_end", th.get("entry_end"), 1),
        kv("eod_entry_cutoff", th.get("eod_entry_cutoff"), 1),
        kv("eod_squareoff_time", th.get("eod_squareoff_time"), 1),
        kv("market_open", th.get("market_open"), 1),
        kv("market_close", th.get("market_close"), 1),
        kv("leverage_map (× multiplier)", ""),
        kv("INTRADAY", lev.get("INTRADAY"), 1),
        kv("COVER_ORDER (CO)", lev.get("COVER_ORDER"), 1),
        kv("BRACKET_ORDER (BO)", lev.get("BRACKET_ORDER"), 1),
        kv("DELIVERY", lev.get("DELIVERY"), 1),
    ]
    sections.append({"title": "1 · SYSTEM", "key": "SYSTEM", "rows": sysrows})

    # 2 · RISK
    riskrows = [
        kv("max_open_positions", risk.get("max_open_positions")),
        kv("max_daily_trades", risk.get("max_daily_trades")),
        kv("max_consecutive_losses", risk.get("max_consecutive_losses")),
        kv("daily_loss_limit_pct", risk.get("daily_loss_limit_pct")),
        kv("max_sector_exposure_pct", risk.get("max_sector_exposure_pct")),
        kv("max_position_value_pct", psz.get("max_position_value_pct")),
        kv("max_concentration_pct", psz.get("max_concentration_pct")),
        kv("risk_per_trade_pct", psz.get("risk_per_trade_pct")),
    ]
    sections.append({"title": "2 · RISK", "key": "RISK", "rows": riskrows})

    # 3 · SCORING
    scorerows = [
        kv("min_pass_score", scoring.get("min_pass_score")),
        kv("high_score_threshold", scoring.get("high_score_threshold")),
        kv("medium_score_threshold", scoring.get("medium_score_threshold")),
        kv("weights (steps · Σ = 100)", ""),
    ]
    for k, v in sorted(steps.items(), key=lambda it: -(_f(it[1]) or 0)):
        scorerows.append(kv(k, v, 1))
    sections.append({"title": "3 · SCORING", "key": "SCORING", "rows": scorerows})

    # 4 · SLIPPAGE  (real shape = tier-based bps, NOT a price-slab→allowed table)
    sliprows = [
        kv("model", "tier-based (slippage_bps by liquidity tier)"),
        kv("default_tier", slip.get("default_tier")),
        kv("tiers (slippage_bps)", ""),
    ]
    for tname in ("liquid", "mid", "small"):
        sliprows.append(kv(tname, _cfg_get(tiers, tname, "slippage_bps"), 1))
    sliprows += [
        kv("entry_gate.max_entry_slippage_pct", egate.get("max_entry_slippage_pct")),
        kv("entry_gate.slippage_buffer", egate.get("slippage_buffer")),
        kv("system.slippage_bands", ", ".join(str(x) for x in (sysd.get("slippage_bands") or []))),
    ]
    sections.append({"title": "4 · SLIPPAGE", "key": "SLIPPAGE", "rows": sliprows})

    # 5 · BROKER COSTS (zerodha, equity; futures/options sub-dicts present but not expanded)
    costrows = [
        kv("brokerage_flat_intraday", zc.get("brokerage_flat_intraday")),
        kv("brokerage_pct_intraday", zc.get("brokerage_pct_intraday")),
        kv("stt_sell_pct", zc.get("stt_sell_pct")),
        kv("stt_cnc_pct", zc.get("stt_cnc_pct")),
        kv("exchange_txn_pct", zc.get("exchange_txn_pct")),
        kv("gst_pct", zc.get("gst_pct")),
        kv("sebi_pct", zc.get("sebi_pct")),
        kv("stamp_duty_mis_buy_pct", zc.get("stamp_duty_mis_buy_pct")),
        kv("stamp_duty_cnc_buy_pct", zc.get("stamp_duty_cnc_buy_pct")),
        kv("(futures / options rates)", "present in config_json — not expanded here"),
    ]
    sections.append({"title": "5 · BROKER COSTS (zerodha · equity)", "key": "BROKER COSTS", "rows": costrows})

    # 6 · STRATEGY (per-strategy config not snapshotted — W0.1)
    sections.append({"title": "6 · STRATEGY (per-strategy)", "key": "STRATEGY", "rows": [
        kv("per-strategy config",
           "— pending W0.1 (not snapshotted; strategies load via StrategyLoader)"),
    ]})

    meta = {
        "date": date_iso, "has_snapshot": True,
        "snapshot_id": row["snapshot_id"], "snapshot_ts": row["snapshot_ts"],
        "mode": row["mode"], "account": row["account_id"],
        "trade_type": row["trade_type"], "config_hash": row["config_hash"],
    }
    return sections, meta


def render_config_sheet(wb: openpyxl.Workbook, sections: List[Dict[str, Any]],
                        meta: Dict[str, Any]) -> None:
    ws = wb.create_sheet(title="Config")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 52

    ws.merge_cells("A1:B1")
    t = ws.cell(1, 1, value=f"Config snapshot for {meta['date']}")
    t.font = FONT_TITLE
    t.alignment = ALIGN_LEFT

    if not meta.get("has_snapshot"):
        ws.merge_cells("A3:B3")
        c = ws.cell(3, 1, value="— pending W0 (no config snapshot for this date)")
        c.font = Font(name="Arial", size=10, italic=True)
        ws.freeze_panes = "A2"
        return

    ws.merge_cells("A2:B2")
    sub = ws.cell(2, 1, value=(
        f"Snapshot ID {meta['snapshot_id']}   ·   Captured-at {meta['snapshot_ts']}   ·   "
        f"Mode {meta['mode']}   ·   Account {meta['account']}   ·   trade_type {meta['trade_type']}"
        f"   ·   hash {str(meta['config_hash'])[:12]}"))
    sub.font = Font(name="Arial", size=8, italic=True)
    sub.alignment = ALIGN_LEFT

    r = 4
    for sec in sections:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        sc = ws.cell(r, 1, value=sec["title"])
        fill = _CONFIG_SECTION_FILLS.get(sec["key"], FILL_HEADER)
        sc.fill = fill
        sc.font = FONT_WHITE_BOLD
        sc.alignment = ALIGN_LEFT
        sc.border = BORDER_ALL
        bc = ws.cell(r, 2)
        bc.fill = fill
        bc.border = BORDER_ALL
        r += 1
        for label, value, indent in sec["rows"]:
            kc = ws.cell(r, 1, value=("    " * int(indent)) + str(label))
            kc.font = Font(name="Arial", size=9, bold=(int(indent) == 0))
            kc.alignment = ALIGN_LEFT
            kc.border = BORDER_ALL
            vv = _cfg_val(value)
            vc = ws.cell(r, 2, value=vv)
            vc.font = FONT_BODY
            vc.alignment = ALIGN_LEFT if isinstance(vv, str) else ALIGN_RIGHT
            vc.border = BORDER_ALL
            if isinstance(vv, float):
                vc.number_format = "0.######"
            elif isinstance(vv, int) and not isinstance(vv, bool):
                vc.number_format = NUM_FMT_INTEGER
            r += 1

    ws.freeze_panes = "A4"


# ═════════════════════════════════════════════════════════════════════════════════
# SHEET 5 — STRATEGIES  (first analytics sheet; DB-pure, aggregates the proven truth
# layer — the Orders `records` + Signals `srecords` for the day, plus a trailing-window
# query for the ranking). 5 spaced tables, each labelled with its BASIS. No fabricated
# aggregates: every number sums real per-trade rows. win = net_pnl>0, loss = net_pnl<0
# (the system's own definition, `compute_strategy_metrics`). `strategy_metrics` is NOT
# used (thin/derived/CLOSED-only) — `trades` is the authoritative layer.
# ═════════════════════════════════════════════════════════════════════════════════

_STRAT_FILLS = {
    "T1": PatternFill("solid", fgColor="1F4E79"),
    "T2": PatternFill("solid", fgColor="548235"),
    "T3": PatternFill("solid", fgColor="7030A0"),
    "T4": PatternFill("solid", fgColor="BF8F00"),
    "T5": PatternFill("solid", fgColor="C00000"),
}

# 30-min entry-time buckets (edges 09:15-09:30 and 15:00-15:15 are 15-min).
_TIME_BUCKETS = [("09:15", "09:30"), ("09:30", "10:00"), ("10:00", "10:30"),
                 ("10:30", "11:00"), ("11:00", "11:30"), ("11:30", "12:00"),
                 ("12:00", "12:30"), ("12:30", "13:00"), ("13:00", "13:30"),
                 ("13:30", "14:00"), ("14:00", "14:30"), ("14:30", "15:00"),
                 ("15:00", "15:15")]

_TRAILING_SESSIONS = 20   # T5 default window


def _bucket_for(hhmmss: Optional[str]) -> Optional[str]:
    """Return the 'HH:MM-HH:MM' bucket for an HH:MM:SS time, or None if out of range."""
    if not hhmmss or len(hhmmss) < 5:
        return None
    hm = hhmmss[:5]
    for lo, hi in _TIME_BUCKETS:
        if lo <= hm < hi:
            return f"{lo}-{hi}"
    return None


def _minmax_norm(vals: List[Optional[float]]) -> List[float]:
    """Min-max normalize to 0-1 across the list; 0.5 for every entry when all equal
    (or all None) — a neutral score that never fabricates spread."""
    xs = [v for v in vals if v is not None]
    if not xs:
        return [0.5 for _ in vals]
    lo, hi = min(xs), max(xs)
    if hi <= lo:
        return [0.5 for _ in vals]
    return [((v - lo) / (hi - lo)) if v is not None else 0.0 for v in vals]


def _avg(xs: List[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 2) if xs else None


def _build_t5_ranking(store: StateStore, date_iso: str,
                      n_sessions: int = _TRAILING_SESSIONS) -> Dict[str, Any]:
    """Per-strategy ranking over a TRAILING window of the last `n_sessions` distinct
    trade sessions (by entry date) up to date_iso. Transparent composite (all
    components shown); confidence down-weights low-sample strategies."""
    dates = [r["d"] for r in store.fetch_all(
        "SELECT DISTINCT substr(created_at,1,10) AS d FROM trades "
        "WHERE substr(created_at,1,10) <= ? ORDER BY d DESC LIMIT ?",
        (date_iso, n_sessions))]
    formula = ("composite = (0.40·win%_norm + 0.40·netROI%_norm + 0.20·avgRR_norm) "
               "× confidence;  confidence = min(trades_n / 20, 1);  norm = min-max across strategies")
    if not dates:
        return {"n_sessions": 0, "window_from": None, "window_to": date_iso,
                "rows": [], "formula": formula}
    lo_date = min(dates)
    rr_by_trade: Dict[str, float] = {}
    for r in store.fetch_all(
            "SELECT trade_id, actual_rr FROM trade_slippage_log "
            "WHERE trade_date >= ? AND trade_date <= ?", (lo_date, date_iso)):
        if r["actual_rr"] is not None:
            rr_by_trade[r["trade_id"]] = _f(r["actual_rr"])

    agg: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "wins": 0, "net": 0.0, "cap": 0.0, "rr": []})
    for t in store.fetch_all(
            "SELECT trade_id, strategy, net_pnl, margin_reserved FROM trades "
            "WHERE substr(created_at,1,10) >= ? AND substr(created_at,1,10) <= ? "
            "AND net_pnl IS NOT NULL AND status IN ('CLOSED','CLOSED_MANUAL')",
            (lo_date, date_iso)):
        a = agg[t["strategy"] or "?"]
        a["n"] += 1
        net = _f(t["net_pnl"]) or 0.0
        a["net"] += net
        if net > 0:
            a["wins"] += 1
        a["cap"] += _f(t["margin_reserved"]) or 0.0
        rr = rr_by_trade.get(t["trade_id"])
        if rr is not None:
            a["rr"].append(rr)

    rows: List[Dict[str, Any]] = []
    for s, a in agg.items():
        n = a["n"]
        rows.append({
            "strategy": s, "trades_n": n,
            "win_pct": round(a["wins"] / n * 100, 2) if n else 0.0,
            "roi_pct": round(a["net"] / a["cap"] * 100, 2) if a["cap"] else 0.0,
            "avg_rr": _avg(a["rr"]),
            "confidence": round(min(n / 20.0, 1.0), 2),
        })
    win_n = _minmax_norm([r["win_pct"] for r in rows])
    roi_n = _minmax_norm([r["roi_pct"] for r in rows])
    rr_n = _minmax_norm([r["avg_rr"] for r in rows])
    for i, r in enumerate(rows):
        r["win_norm"], r["roi_norm"], r["rr_norm"] = (round(win_n[i], 3),
                                                      round(roi_n[i], 3), round(rr_n[i], 3))
        r["composite"] = round((0.40 * win_n[i] + 0.40 * roi_n[i] + 0.20 * rr_n[i])
                               * r["confidence"], 4)
    rows.sort(key=lambda r: r["composite"], reverse=True)
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return {"n_sessions": len(dates), "window_from": lo_date, "window_to": date_iso,
            "rows": rows, "formula": formula}


def _win_loss_pct(wins: int, losses: int) -> Tuple[Optional[float], Optional[float]]:
    """Win% / Loss% over DECIDED trades (wins + losses) — the same definite-outcome
    denominator as the §5 day-summary headline (win = net>0, loss = net<0; never-filled,
    cancelled, or still-open records are NOT in the denominator). Guarantees win% + loss%
    == 100 whenever any trade is decided, and returns (None, None) — rendered
    "N/A — no data" — when none is, so a strategy/direction with no decided trade never
    shows a misleading 0.0%. FIX 1 (Phase-B.1): removes the all-attempts-denominator
    dilution that made per-strategy/per-direction win% contradict the headline."""
    decided = wins + losses
    if decided <= 0:
        return None, None
    return round(wins / decided * 100, 2), round(losses / decided * 100, 2)


def build_strategy_data(store: StateStore, date_iso: str, records: List[Dict[str, Any]],
                        srecords: List[Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate the day's Orders/Signals truth layer into the 5 Strategies tables
    (T1-T4 daily) + the trailing ranking (T5)."""
    sig_by_strat: Dict[str, int] = defaultdict(int)
    for s in srecords:
        sig_by_strat[s.get("strategy") or "?"] += 1

    # ── T1 PERFORMANCE (per strategy) ──
    perf: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "losses": 0, "gross": 0.0, "net": 0.0,
                 "cap": 0.0, "max_win": None, "max_loss": None})
    for r in records:
        a = perf[r.get("strategy") or "?"]
        a["trades"] += 1
        g, net, cap = _f(r.get("gross")), r.get("_net_raw"), _f(r.get("capital_consumed"))
        if g is not None:
            a["gross"] += g
        if cap is not None:
            a["cap"] += cap
        if net is not None:
            a["net"] += net
            if net > 0:
                a["wins"] += 1
                a["max_win"] = net if a["max_win"] is None else max(a["max_win"], net)
            elif net < 0:
                a["losses"] += 1
                a["max_loss"] = net if a["max_loss"] is None else min(a["max_loss"], net)
    t1 = []
    for s in (set(perf) | set(sig_by_strat)):
        a = perf[s]
        tr = a["trades"]
        wp, lp = _win_loss_pct(a["wins"], a["losses"])   # FIX 1: decided (wins+losses) denominator
        t1.append({
            "strategy": s, "signals": sig_by_strat.get(s, 0), "trades": tr,
            "wins": a["wins"], "losses": a["losses"],
            "win_pct": wp, "loss_pct": lp,
            "gross": round(a["gross"], 2), "net": round(a["net"], 2),
            "roi_pct": round(a["net"] / a["cap"] * 100, 2) if a["cap"] else None,
            "capital_used": round(a["cap"], 2),
            "max_win": round(a["max_win"], 2) if a["max_win"] is not None else None,
            "max_loss": round(a["max_loss"], 2) if a["max_loss"] is not None else None,
        })
    t1.sort(key=lambda x: x["net"], reverse=True)

    # ── T2 TIME-BUCKET (signals by receipt-time, trades by entry-time) ──
    tb = {f"{lo}-{hi}": {"signals": 0, "trades": 0, "wins": 0, "losses": 0,
                         "net": 0.0, "cap": 0.0} for lo, hi in _TIME_BUCKETS}
    for s in srecords:
        b = _bucket_for(s.get("vm_receipt"))
        if b:
            tb[b]["signals"] += 1
    for r in records:
        b = _bucket_for(r.get("entry_filled"))
        if not b:
            continue
        d = tb[b]
        d["trades"] += 1
        net, cap = r.get("_net_raw"), _f(r.get("capital_consumed"))
        if net is not None:
            d["net"] += net
            if net > 0:
                d["wins"] += 1
            elif net < 0:
                d["losses"] += 1
        if cap is not None:
            d["cap"] += cap
    t2 = []
    for lo, hi in _TIME_BUCKETS:
        d = tb[f"{lo}-{hi}"]
        if d["signals"] == 0 and d["trades"] == 0:
            continue   # omit empty buckets (compact daily view)
        t2.append({
            "bucket": f"{lo}-{hi}", "signals": d["signals"], "trades": d["trades"],
            "wins": d["wins"], "losses": d["losses"],
            "win_pct": _win_loss_pct(d["wins"], d["losses"])[0],   # FIX 1: decided denominator
            "profit": round(d["net"], 2),
            "roi_pct": round(d["net"] / d["cap"] * 100, 2) if d["cap"] else None,
        })

    # ── T3 LONG vs SHORT ──
    td = {"LONG": {"trades": 0, "wins": 0, "losses": 0, "net": 0.0, "cap": 0.0},
          "SHORT": {"trades": 0, "wins": 0, "losses": 0, "net": 0.0, "cap": 0.0}}
    for r in records:
        dr = (r.get("direction") or "").upper()
        if dr not in td:
            continue
        d = td[dr]
        d["trades"] += 1
        net, cap = r.get("_net_raw"), _f(r.get("capital_consumed"))
        if net is not None:
            d["net"] += net
            if net > 0:
                d["wins"] += 1
            elif net < 0:
                d["losses"] += 1
        if cap is not None:
            d["cap"] += cap
    t3 = []
    for dr in ("LONG", "SHORT"):
        d = td[dr]
        t3.append({
            "direction": dr, "signals": d["trades"], "trades": d["trades"],
            "wins": d["wins"], "losses": d["losses"],
            "win_pct": _win_loss_pct(d["wins"], d["losses"])[0],   # FIX 1: decided denominator
            "profit": round(d["net"], 2),
            "roi_pct": round(d["net"] / d["cap"] * 100, 2) if d["cap"] else None,
        })

    # ── T4 RR (per strategy) ──
    rr: Dict[str, Dict[str, List[float]]] = defaultdict(
        lambda: {"psl": [], "ptgt": [], "asl": [], "atgt": [], "rr": []})
    for r in records:
        a = rr[r.get("strategy") or "?"]
        if _f(r.get("sl_pct")) is not None:
            a["psl"].append(_f(r.get("sl_pct")))
        if _f(r.get("tgt_pct")) is not None:
            a["ptgt"].append(_f(r.get("tgt_pct")))
        fe, fsl, ftgt = _f(r.get("filled_entry")), _f(r.get("filled_sl")), _f(r.get("filled_tgt"))
        if fe and fsl is not None:
            a["asl"].append(round(abs(fe - fsl) / fe * 100, 4))
        if fe and ftgt is not None:
            a["atgt"].append(round(abs(ftgt - fe) / fe * 100, 4))
        if _f(r.get("rr_achieved")) is not None:
            a["rr"].append(_f(r.get("rr_achieved")))
    t4 = [{"strategy": s, "plan_sl_pct": _avg(a["psl"]), "plan_tgt_pct": _avg(a["ptgt"]),
           "act_sl_pct": _avg(a["asl"]), "act_tgt_pct": _avg(a["atgt"]), "avg_rr": _avg(a["rr"])}
          for s, a in sorted(rr.items())]

    return {"date": date_iso, "mode": meta.get("mode"), "t1": t1, "t2": t2, "t3": t3,
            "t4": t4, "t5": _build_t5_ranking(store, date_iso)}


def _render_strat_table(ws, start_row: int, title: str, headers: List[str],
                        keys: List[str], fmts: List[str], rows: List[Dict[str, Any]],
                        fill, *, note: Optional[str] = None,
                        empty_label: str = "— no data for this date") -> int:
    last_col = len(headers)
    r = start_row
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=last_col)
    tc = ws.cell(r, 1, value=title)
    tc.fill = fill
    tc.font = FONT_WHITE_BOLD
    tc.alignment = ALIGN_LEFT
    for i in range(1, last_col + 1):
        ws.cell(r, i).fill = fill
        ws.cell(r, i).border = BORDER_ALL
    r += 1
    if note:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=last_col)
        ws.cell(r, 1, value=note).font = Font(name="Arial", size=8, italic=True)
        r += 1
    for i, h in enumerate(headers, start=1):
        c = ws.cell(r, i, value=h)
        c.font = Font(name="Arial", size=9, bold=True, color="FFFFFF")
        c.fill = fill
        c.alignment = ALIGN_CENTER
        c.border = BORDER_ALL
    r += 1
    if not rows:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=last_col)
        ws.cell(r, 1, value=empty_label).font = Font(name="Arial", size=9, italic=True)
        return r + 2
    for row in rows:
        for i, (k, f) in enumerate(zip(keys, fmts), start=1):
            v = row.get(k)
            if v is None:
                v = "" if f == "text" else NA_NO_DATA
            c = ws.cell(r, i, value=v)
            c.font = FONT_BODY
            c.alignment = _align("text" if isinstance(v, str) else f)
            c.border = BORDER_ALL
            nf = _num_format(f)
            if nf and isinstance(v, (int, float)) and not isinstance(v, bool):
                c.number_format = nf
        r += 1
    return r + 2   # blank-row gap between tables


def render_strategies_sheet(wb: openpyxl.Workbook, data: Dict[str, Any]) -> None:
    ws = wb.create_sheet(title="Strategies")
    ws.sheet_view.showGridLines = False
    for i, w in enumerate([22, 9, 8, 7, 8, 8, 9, 12, 12, 9, 13, 11, 11], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.merge_cells("A1:M1")
    t = ws.cell(1, 1, value=f"STRATEGIES — {data['date']}   |   mode={data.get('mode') or 'n/a'}")
    t.font = FONT_TITLE
    t.alignment = ALIGN_LEFT
    ws.freeze_panes = "A2"

    r = 3
    r = _render_strat_table(
        ws, r, "T1 · PERFORMANCE  (per strategy — THIS DAY)",
        ["Strategy", "Signals", "Trades", "Wins", "Losses", "Win%", "Loss%",
         "Gross", "Net", "ROI%", "Capital Used", "Max Win", "Max Loss"],
        ["strategy", "signals", "trades", "wins", "losses", "win_pct", "loss_pct",
         "gross", "net", "roi_pct", "capital_used", "max_win", "max_loss"],
        ["text", "int", "int", "int", "int", "num2", "num2", "money", "money",
         "num2", "money", "money", "money"],
        data["t1"], _STRAT_FILLS["T1"],
        note=("BASIS: this day's trades (Orders truth layer); Signals = reached-storage per "
              "strategy; win=net>0, loss=net<0. Win%/Loss% are over DECIDED trades "
              "(wins+losses) — they sum to 100% and match the Dashboard headline, and are "
              "NOT diluted by never-filled attempts (the Trades column still counts every "
              "record). ROI% = Σnet / Σcapital."))
    r = _render_strat_table(
        ws, r, "T2 · TIME-BUCKET  (30-min, THIS DAY)",
        ["Time Bucket", "Signals", "Trades", "Wins", "Losses", "Win%", "Profit", "ROI%"],
        ["bucket", "signals", "trades", "wins", "losses", "win_pct", "profit", "roi_pct"],
        ["text", "int", "int", "int", "int", "num2", "money", "num2"],
        data["t2"], _STRAT_FILLS["T2"],
        note=("BASIS: trades bucketed by ENTRY time; signals by VM-receipt time; empty "
              "buckets omitted."))
    r = _render_strat_table(
        ws, r, "T3 · LONG vs SHORT  (per direction — THIS DAY)",
        ["Direction", "Signals", "Trades", "Wins", "Losses", "Win%", "Profit", "ROI%"],
        ["direction", "signals", "trades", "wins", "losses", "win_pct", "profit", "roi_pct"],
        ["text", "int", "int", "int", "int", "num2", "money", "num2"],
        data["t3"], _STRAT_FILLS["T3"],
        note=("BASIS: this day's trades by direction; Signals = the signals that produced "
              "these trades (1 per trade)."))
    r = _render_strat_table(
        ws, r, "T4 · RISK:REWARD  (per strategy — THIS DAY, tuning aid)",
        ["Strategy", "Planned SL%", "Planned TGT%", "Actual SL%", "Actual TGT%", "Avg RR Achieved"],
        ["strategy", "plan_sl_pct", "plan_tgt_pct", "act_sl_pct", "act_tgt_pct", "avg_rr"],
        ["text", "num2", "num2", "num2", "num2", "num2"],
        data["t4"], _STRAT_FILLS["T4"],
        note=("BASIS: planned = |entry−sys_sl|/entry & |sys_tgt−entry|/entry; actual = from "
              "filled SL/TGT legs; Avg RR = trade_slippage_log.actual_rr."))
    t5 = data["t5"]
    r = _render_strat_table(
        ws, r, f"T5 · RANKING  (TRAILING {t5['n_sessions']} SESSIONS — not this day only)",
        ["Rank", "Strategy", "Trades(n)", "Win%", "Net ROI%", "Avg RR", "Confidence",
         "Win_norm", "ROI_norm", "RR_norm", "Composite"],
        ["rank", "strategy", "trades_n", "win_pct", "roi_pct", "avg_rr", "confidence",
         "win_norm", "roi_norm", "rr_norm", "composite"],
        ["int", "text", "int", "num2", "num2", "num2", "num2", "num2", "num2", "num2", "num2"],
        t5["rows"], _STRAT_FILLS["T5"],
        note=(f"BASIS: TRAILING {t5['n_sessions']} sessions "
              f"({t5.get('window_from') or '—'} → {t5['window_to']}), NOT this day only. "
              f"{t5['formula']}. Confidence deliberately stops a low-sample strategy topping a "
              f"high-sample one."),
        empty_label="— no trades in the trailing window")


# ═════════════════════════════════════════════════════════════════════════════════
# SHEET 6 — SLIPPAGE  (last analytics sheet; absorbs the parked "Slippage Phase-2
# reports"). Source = trade_slippage_log (the per-trade roll-up `orders/slippage_recorder`
# writes) + the Orders `records` (for gross P&L + the entry tolerance). DB-pure.
#
# Decomposition (verified against the system's own calc_rr_damage_pct): total slip cost =
# (entry_slippage_rs + sl_slippage_rs − tgt_slippage_rs) × qty — 3 legs (ENTRY adverse+,
# SL adverse+, TGT favourable−); the 3 components sum to the total by construction. There
# is NO 4th leg — SL/TGT ARE the exits; EOD/manual exit slippage is NOT recorded (flagged).
#
# Tier note: slippage.tiers (liquid/mid/small bps) is resolved at RUNTIME by
# broker/slippage_engine.tier_for(symbol) via InstrumentCache (paper-fill model) and is NOT
# persisted per trade — no DB-pure tier exists. So the "tier" block buckets by the RECORDED
# price_band; the configured tier bps (from W0 config_snapshots) are shown as a reference.
# Follow-up W12 = persist the resolved slippage tier per trade for true per-tier vs-expected.
# ═════════════════════════════════════════════════════════════════════════════════

_SLIP_FILLS = {
    "summary": PatternFill("solid", fgColor="C00000"),
    "band": PatternFill("solid", fgColor="BF8F00"),
    "strategy": PatternFill("solid", fgColor="1F4E79"),
    "symbol": PatternFill("solid", fgColor="548235"),
    "worst": PatternFill("solid", fgColor="843C0C"),
    "trend": PatternFill("solid", fgColor="404040"),
}


def _slip_tiers_for_date(store: StateStore, date_iso: str) -> Tuple[Optional[dict], Optional[str]]:
    """Configured slippage.tiers (tier→bps) + default_tier from the day's config snapshot
    (W0). (None, None) if no snapshot for the date (pre-W0)."""
    row = store.fetch_one(
        "SELECT config_json FROM config_snapshots WHERE snapshot_date = ? "
        "ORDER BY snapshot_ts DESC LIMIT 1", (date_iso,))
    if not row:
        return None, None
    try:
        slip = (json.loads(row["config_json"]).get("slippage") or {})
        tiers = {k: _cfg_get(v, "slippage_bps") for k, v in (slip.get("tiers") or {}).items()}
        return (tiers or None), slip.get("default_tier")
    except (TypeError, ValueError):
        return None, None


def build_slippage_data(store: StateStore, date_iso: str, records: List[Dict[str, Any]],
                        meta: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate trade_slippage_log for the date into the 6 Slippage blocks."""
    tsl = [dict(r) for r in store.fetch_all(
        "SELECT * FROM trade_slippage_log WHERE trade_date = ?", (date_iso,))]
    by_tid = {r.get("trade_id"): r for r in records}
    gross_total = sum((_f(r.get("gross")) or 0.0) for r in records)

    per: List[Dict[str, Any]] = []
    for r in sorted(tsl, key=lambda x: x.get("trade_id") or ""):
        qty = _f(r["qty"]) or 0
        e, s, tg = _f(r["entry_slippage_rs"]), _f(r["sl_slippage_rs"]), _f(r["tgt_slippage_rs"])
        # full precision internally; round only for display/aggregation output
        entry_cost = (e or 0.0) * qty
        sl_cost = (s or 0.0) * qty
        tgt_cost = -(tg or 0.0) * qty                  # favourable TGT subtracts (matches rr_damage)
        orec = by_tid.get(r.get("trade_id"), {})
        epct = _f(r["entry_slippage_pct"])
        per.append({
            "trade_id": r.get("trade_id"), "symbol": r.get("symbol"),
            "strategy": r.get("strategy_name") or "?",
            "price_band": r.get("price_band") or NA_NO_DATA, "qty": qty,
            "entry_slip_rs": e, "entry_slip_bps": (round(epct * 100, 2) if epct is not None else None),
            "entry_cost": entry_cost, "sl_cost": sl_cost, "tgt_cost": tgt_cost,
            "total_cost": entry_cost + sl_cost + tgt_cost,
            "rr_damage_pct": _f(r["rr_damage_pct"]),
            "excess_over_tol": _f(orec.get("slip_delta")),   # entry actual − allowed (₹/share)
        })

    # ── Block 1: SUMMARY + 3-way decomposition (aggregate at full precision, round once) ──
    entry_raw = sum(p["entry_cost"] for p in per)
    sl_raw = sum(p["sl_cost"] for p in per)
    tgt_raw = sum(p["tgt_cost"] for p in per)
    total_raw = sum(p["total_cost"] for p in per)     # == entry_raw + sl_raw + tgt_raw exactly
    summary = {
        "n": len(per), "total_cost": round(total_raw, 2),
        "entry_total": round(entry_raw, 2), "sl_total": round(sl_raw, 2),
        "tgt_total": round(tgt_raw, 2), "sum_check": round(entry_raw + sl_raw + tgt_raw, 2),
        "gross_total": round(gross_total, 2),
        "pct_of_gross": (round(total_raw / gross_total * 100, 2) if gross_total else None),
    }

    # ── Blocks 2-4: by price_band / strategy / symbol ──
    def _agg(keyfn) -> List[Dict[str, Any]]:
        d: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"n": 0, "bps": [], "cost": 0.0})
        for p in per:
            a = d[keyfn(p)]
            a["n"] += 1
            a["cost"] += p["total_cost"]
            if p["entry_slip_bps"] is not None:
                a["bps"].append(p["entry_slip_bps"])
        out = [{"key": k, "trades": a["n"], "avg_bps": _avg(a["bps"]),
                "max_bps": round(max(a["bps"]), 2) if a["bps"] else None,
                "min_bps": round(min(a["bps"]), 2) if a["bps"] else None,
                "total_cost": round(a["cost"], 2)} for k, a in d.items()]
        out.sort(key=lambda x: x["total_cost"], reverse=True)
        return out

    tiers, default_tier = _slip_tiers_for_date(store, date_iso)

    # ── Block 5: WORST-20 by slip cost ──
    worst = []
    for i, p in enumerate(sorted(per, key=lambda x: x["total_cost"], reverse=True)[:20], start=1):
        worst.append({"seq": i, "symbol": p["symbol"], "strategy": p["strategy"],
                      "entry_slip_rs": p["entry_slip_rs"], "total_cost": round(p["total_cost"], 2),
                      "excess_over_tol": p["excess_over_tol"]})

    # ── Block 6: 10-day trend ──
    trend = []
    tdates = [row["d"] for row in store.fetch_all(
        "SELECT DISTINCT trade_date AS d FROM trade_slippage_log WHERE trade_date <= ? "
        "ORDER BY d DESC LIMIT 10", (date_iso,))]
    for d in sorted(tdates):
        rows = store.fetch_all(
            "SELECT qty, entry_slippage_rs, sl_slippage_rs, tgt_slippage_rs, entry_slippage_pct "
            "FROM trade_slippage_log WHERE trade_date = ?", (d,))
        bps, cost = [], 0.0
        for r in rows:
            q = _f(r["qty"]) or 0
            cost += ((_f(r["entry_slippage_rs"]) or 0.0) + (_f(r["sl_slippage_rs"]) or 0.0)
                     - (_f(r["tgt_slippage_rs"]) or 0.0)) * q
            ep = _f(r["entry_slippage_pct"])
            if ep is not None:
                bps.append(ep * 100)
        trend.append({"date": d, "trades": len(rows), "avg_bps": _avg(bps),
                      "total_cost": round(cost, 2)})

    return {"date": date_iso, "mode": meta.get("mode"),
            "summary": summary, "bands": _agg(lambda p: p["price_band"]),
            "strategies": _agg(lambda p: p["strategy"]), "symbols": _agg(lambda p: p["symbol"]),
            "worst": worst, "trend": trend, "tiers": tiers, "default_tier": default_tier}


def render_slippage_sheet(wb: openpyxl.Workbook, data: Dict[str, Any]) -> None:
    ws = wb.create_sheet(title="Slippage")
    ws.sheet_view.showGridLines = False
    for i, w in enumerate([24, 11, 11, 11, 12, 13, 14, 14], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.merge_cells("A1:H1")
    t = ws.cell(1, 1, value=f"SLIPPAGE — {data['date']}   |   mode={data.get('mode') or 'n/a'}")
    t.font = FONT_TITLE
    t.alignment = ALIGN_LEFT
    ws.freeze_panes = "A2"

    s = data["summary"]
    # ── Block 1: SUMMARY + decomposition (key/value) ──
    ws.merge_cells("A3:H3")
    b1 = ws.cell(3, 1, value="1 · SUMMARY + DECOMPOSITION  (total slip cost = ENTRY + SL − TGT; the 3 legs sum to total)")
    b1.fill = _SLIP_FILLS["summary"]
    b1.font = FONT_WHITE_BOLD
    b1.alignment = ALIGN_LEFT
    for i in range(1, 9):
        ws.cell(3, i).fill = _SLIP_FILLS["summary"]
        ws.cell(3, i).border = BORDER_ALL
    kv = [
        ("Trades with slippage rows", s["n"]),
        ("Total slip cost (₹)", s["total_cost"]),
        ("  Entry (adverse, +cost)", s["entry_total"]),
        ("  SL exit (adverse, +cost)", s["sl_total"]),
        ("  TGT exit (favourable, −cost)", s["tgt_total"]),
        ("  Σ components (== total)", s["sum_check"]),
        ("Gross P&L (Σ, ₹)", s["gross_total"]),
        ("Slippage as % of gross P&L", s["pct_of_gross"] if s["pct_of_gross"] is not None else NA_NO_DATA),
    ]
    r = 4
    for label, val in kv:
        kc = ws.cell(r, 1, value=label)
        kc.font = Font(name="Arial", size=9, bold=not label.startswith("  "))
        kc.alignment = ALIGN_LEFT
        kc.border = BORDER_ALL
        vc = ws.cell(r, 2, value=val)
        vc.font = FONT_BODY
        vc.alignment = ALIGN_RIGHT if isinstance(val, (int, float)) else ALIGN_LEFT
        vc.border = BORDER_ALL
        if isinstance(val, float):
            vc.number_format = NUM_FMT_CURRENCY if "cost" in label.lower() or "P&L" in label else "0.00"
        r += 1
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
    ws.cell(r, 1, value=("NOTE: only ENTRY + SL-exit + TGT-exit slippage is captured; EOD/manual-exit "
                         "slippage is NOT recorded by the roll-up (follow-up).")).font = Font(
        name="Arial", size=8, italic=True)
    r += 2

    # ── Block 2: price-band ("tier") analysis + configured-tier reference ──
    r = _render_strat_table(
        ws, r, "2 · BAND ANALYSIS  (bucketed by recorded price_band — slippage tiers are NOT persisted per trade)",
        ["Price Band", "Trades", "Avg bps", "Max bps", "Min bps", "Total Cost"],
        ["key", "trades", "avg_bps", "max_bps", "min_bps", "total_cost"],
        ["text", "int", "num2", "num2", "num2", "money"],
        data["bands"], _SLIP_FILLS["band"],
        note=("Actual ENTRY slippage in bps per price_band. slippage.tiers (liquid/mid/small) is a "
              "runtime paper-fill model resolved via InstrumentCache — NOT persisted per trade (W12)."))
    # configured tiers reference row
    if data["tiers"]:
        tier_txt = "  ·  ".join(f"{k}={v} bps" for k, v in data["tiers"].items())
        ws.merge_cells(start_row=r - 2, start_column=1, end_row=r - 2, end_column=6)
        ws.cell(r - 2, 1, value=f"CONFIGURED slippage.tiers (reference, from W0 config): {tier_txt}   "
                                f"(default_tier={data['default_tier']})").font = Font(
            name="Arial", size=8, italic=True, color="7030A0")

    # ── Block 3: strategy-wise ──
    r = _render_strat_table(
        ws, r, "3 · STRATEGY-WISE",
        ["Strategy", "Trades", "Avg bps", "Max bps", "Min bps", "Total Slip Cost"],
        ["key", "trades", "avg_bps", "max_bps", "min_bps", "total_cost"],
        ["text", "int", "num2", "num2", "num2", "money"],
        data["strategies"], _SLIP_FILLS["strategy"],
        note="Actual ENTRY slippage (bps) + total 3-leg slip cost per strategy.")

    # ── Block 4: stock-wise ──
    r = _render_strat_table(
        ws, r, "4 · STOCK-WISE",
        ["Symbol", "Trades", "Avg bps", "Max bps", "Min bps", "Total Slip Cost"],
        ["key", "trades", "avg_bps", "max_bps", "min_bps", "total_cost"],
        ["text", "int", "num2", "num2", "num2", "money"],
        data["symbols"], _SLIP_FILLS["symbol"],
        note="Actual ENTRY slippage (bps) + total 3-leg slip cost per symbol.")

    # ── Block 5: worst-20 by slip cost ──
    r = _render_strat_table(
        ws, r, "5 · WORST-20  (by total slip cost)",
        ["#", "Symbol", "Strategy", "Entry Slip ₹/sh", "Total Slip Cost", "Excess-over-Tol ₹/sh"],
        ["seq", "symbol", "strategy", "entry_slip_rs", "total_cost", "excess_over_tol"],
        ["int", "text", "text", "money", "money", "money"],
        data["worst"], _SLIP_FILLS["worst"],
        note="Excess-over-tolerance = entry actual − allowed (₹/share); >0 breached the entry tolerance.")

    # ── Block 6: 10-day trend ──
    r = _render_strat_table(
        ws, r, "6 · 10-DAY TREND  (last 10 sessions)",
        ["Date", "Trades", "Avg bps", "Total Slip Cost"],
        ["date", "trades", "avg_bps", "total_cost"],
        ["text", "int", "num2", "money"],
        data["trend"], _SLIP_FILLS["trend"],
        note="Trailing 10 distinct trade-sessions up to the render date.")


# ═════════════════════════════════════════════════════════════════════════════════
# SHEET 0 — DASHBOARD  (FINAL sheet, placed FIRST). Summarizes the six sealed sheets.
# DB-pure: every headline derives from the SAME sources the detail sheets use (the
# already-built records/srecords/sdata/slipdata/rmeta), so the summary CANNOT disagree
# with the detail. Deterministic highlights only — no AI narrative (that stays Tier-3).
# ═════════════════════════════════════════════════════════════════════════════════

_DASH_FILLS = {
    "coverage": PatternFill("solid", fgColor="404040"),
    "delta": PatternFill("solid", fgColor="1F4E79"),
    "trading": PatternFill("solid", fgColor="2E75B6"),
    "profit": PatternFill("solid", fgColor="548235"),
    "capital": PatternFill("solid", fgColor="7030A0"),
    "highlight": PatternFill("solid", fgColor="BF8F00"),
}
_SLIP_GROSS_FLAG_PCT = 25.0        # slip cost > 25% of gross → high-slippage highlight


def _prior_trading_day(store: StateStore, date_iso: str) -> Optional[str]:
    """Most recent distinct trade-session date before date_iso (data-driven → skips
    weekends/holidays with no trades). None if none earlier."""
    row = store.fetch_one(
        "SELECT MAX(substr(created_at,1,10)) AS d FROM trades "
        "WHERE substr(created_at,1,10) < ?", (date_iso,))
    return row["d"] if row and row["d"] else None


def _day_summary(records: List[Dict[str, Any]], srecords: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Headline metrics for one day, derived from the Orders/Signals records (same
    truth layer the detail sheets use).

    win_pct uses _win_loss_pct — the ONE definition — so the headline and the
    per-strategy/per-direction figures cannot disagree. It previously divided by
    len(realized), which counts BREAKEVENS (net exactly 0) in the denominator, while
    _win_loss_pct divides by decided (wins + losses). Its docstring claimed the two
    matched; with one breakeven in the book they did not. Deriving beats restating.
    """
    realized = [r for r in records if r.get("_net_raw") is not None]
    wins = sum(1 for r in realized if r["_net_raw"] > 0)
    losses = sum(1 for r in realized if r["_net_raw"] < 0)
    capital = sum(_f(r.get("capital_consumed")) or 0.0 for r in records)
    slips = [_f(r.get("slip_pct")) * 100 for r in records if _f(r.get("slip_pct")) is not None]
    by: Dict[str, float] = defaultdict(float)
    for r in realized:
        by[r.get("strategy") or "?"] += r["_net_raw"]
    leader = max(by.items(), key=lambda kv: kv[1])[0] if by else None
    return {"net": round(sum(r["_net_raw"] for r in realized), 2),
            "win_pct": _win_loss_pct(wins, losses)[0],
            "trades": len(records), "signals": len(srecords),
            "capital": round(capital, 2),
            "avg_slip_bps": round(sum(slips) / len(slips), 2) if slips else None,
            "leader": leader}


def _max_concurrent(records: List[Dict[str, Any]]) -> Tuple[int, Optional[float]]:
    """Sweep-line max concurrent open positions + max concurrent capital, from the
    trades' entry/exit clock times (HH:MM:SS). Ends processed before starts at a tie."""
    ev: List[Tuple[str, int, float]] = []
    for r in records:
        ent = r.get("entry_filled")
        if not ent:
            continue
        ext = r.get("exit_time") or "15:30:00"
        cap = _f(r.get("capital_consumed")) or 0.0
        ev.append((ent, 1, cap))
        ev.append((ext, -1, -cap))
    if not ev:
        return 0, None
    ev.sort(key=lambda e: (e[0], e[1]))   # at a tie, -1 (end) sorts before +1 (start)
    cur_n = max_n = 0
    cur_cap = max_cap = 0.0
    for _, dn, dcap in ev:
        cur_n += dn
        cur_cap += dcap
        max_n = max(max_n, cur_n)
        max_cap = max(max_cap, cur_cap)
    return max_n, round(max_cap, 2)


def build_dashboard_data(store: StateStore, date_iso: str, records: List[Dict[str, Any]],
                         srecords: List[Dict[str, Any]], meta: Dict[str, Any],
                         smeta: Dict[str, Any], sdata: Dict[str, Any],
                         slipdata: Dict[str, Any], rmeta: Dict[str, Any]) -> Dict[str, Any]:
    today = _day_summary(records, srecords)
    prior_date = _prior_trading_day(store, date_iso)
    prior = None
    if prior_date:
        precs, _pm = build_records(store, prior_date)
        psigs, _psm = build_signal_records(store, prior_date)
        prior = _day_summary(precs, psigs)

    # ── coverage ──
    tids = [r.get("trade_id") for r in records if r.get("trade_id")]
    mfe_cov = None
    if tids:
        ph = ",".join("?" * len(tids))
        ec = store.fetch_one(
            f"SELECT COUNT(DISTINCT trade_id) AS n FROM trade_excursions WHERE trade_id IN ({ph})",
            tuple(tids))
        mfe_cov = round((ec["n"] or 0) / len(tids) * 100, 1)
    has_cfg = store.fetch_one(
        "SELECT 1 FROM config_snapshots WHERE snapshot_date = ? LIMIT 1", (date_iso,)) is not None
    # FIX 2 (Phase-B.1): compute the webhook drop % PER-DATE from the same webhook_audit
    # source the Signals funnel uses — was a hardcoded "82%" (a stale 30-Jun example that
    # was wrong on every other date, e.g. 16-Jun's true 41.8%).
    _wh_recv = smeta.get("wh_received") or 0
    _drop_pct = round(smeta.get("wh_dropped", 0) / _wh_recv * 100, 1) if _wh_recv else None
    coverage = [
        {"section": "Signals", "coverage": "100% (stored basis)",
         "note": (f"{_drop_pct}% webhook-dropped → W9" if _drop_pct is not None
                  else "webhook drop → see Signals sheet (W9)")},
        {"section": "Orders", "coverage": "100%", "note": "exit-trigger ts → W5"},
        {"section": "Reconciliation", "coverage": "100% internal", "note": "broker PENDING → W2/W3"},
        {"section": "Config", "coverage": ("100% (post-W0)" if has_cfg else "— pending W0"),
         "note": "strategy section → W0.1"},
        {"section": "Strategies", "coverage": "100%", "note": "—"},
        {"section": "Slippage", "coverage": "100% (price_band basis)",
         "note": "tier → W12; EOD-exit slip not captured"},
        {"section": "MFE/MAE", "coverage": (f"{mfe_cov}%" if mfe_cov is not None else NA_NO_DATA),
         "note": "sparse (reconstructed post-EOD) → W6"},
        {"section": "Telegram", "coverage": "0%", "note": "not in DB → W1"},
    ]

    # ── deltas ──
    def _d(a, b):
        return round(a - b, 2) if (isinstance(a, (int, float)) and isinstance(b, (int, float))) else None
    deltas = []
    dm = [("Net Profit ₹", "net"), ("Win Rate %", "win_pct"), ("Capital Used ₹", "capital"),
          ("Avg Slippage bps", "avg_slip_bps"), ("Signals (stored)", "signals"),
          ("Trades", "trades"), ("Strategy Leader", "leader")]
    for label, key in dm:
        tv, pv = today.get(key), (prior.get(key) if prior else None)
        if key == "leader":
            delta = "—" if pv is None else ("same" if tv == pv else f"{pv} → {tv}")
        else:
            delta = _d(tv, pv) if pv is not None else None
        deltas.append({"metric": label, "today": tv if tv is not None else NA_NO_DATA,
                       "prior": (pv if pv is not None else NA_NO_DATA),
                       "delta": (delta if delta is not None else NA_NO_DATA)})

    # ── trading summary ──
    placed = sum(1 for r in records if r.get("broker_order_id"))
    filled = sum(1 for r in records if (_f(r.get("qty_filled")) or 0) > 0)
    closure: Dict[str, int] = defaultdict(int)
    for r in records:
        closure[r.get("closure_type") or "—"] += 1
    t3 = {r["direction"]: r for r in sdata["t3"]}
    trading = {
        "wh_received": smeta.get("wh_received"), "stored": smeta.get("n"),
        "qualified": smeta.get("qualified"), "traded": smeta.get("traded"),
        "placed": placed, "filled": filled,
        "closure": dict(sorted(closure.items(), key=lambda kv: -kv[1])),
        "long": t3.get("LONG", {}), "short": t3.get("SHORT", {}),
    }

    # ── profitability ──
    realized = [r for r in records if r.get("_net_raw") is not None]
    gross = round(sum(_f(r.get("gross")) or 0.0 for r in records), 2)
    net = round(sum(r["_net_raw"] for r in realized), 2)
    charges = round(sum(_f(r.get("total_charges")) or 0.0 for r in records), 2)
    capital = sum(_f(r.get("capital_consumed")) or 0.0 for r in records)
    wins = [r["_net_raw"] for r in realized if r["_net_raw"] > 0]
    losses = [r["_net_raw"] for r in realized if r["_net_raw"] < 0]
    gprofit, gloss = sum(wins), abs(sum(losses))
    profit = {
        "gross": gross, "net": net, "charges": charges,
        "roi_pct": round(net / capital * 100, 2) if capital else None,
        "profit_factor": round(gprofit / gloss, 2) if gloss else None,
        "win_rate": round(len(wins) / len(realized) * 100, 2) if realized else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "max_win": round(max(wins), 2) if wins else None,
        "max_loss": round(min(losses), 2) if losses else None,
        "slip_cost": slipdata["summary"]["total_cost"],
        "slip_pct_of_gross": slipdata["summary"]["pct_of_gross"],
    }

    # ── capital ──
    o = store.fetch_one("SELECT balance_after FROM fm_ledger WHERE date=? AND entry_type='INIT' "
                        "ORDER BY ts LIMIT 1", (date_iso,))
    opening = _f(o["balance_after"]) if o else None
    lr = store.fetch_one("SELECT COALESCE(SUM(pnl_delta),0.0) r FROM fm_ledger "
                         "WHERE date=? AND entry_type='RELEASE_USED'", (date_iso,))
    ledger_realized = round(_f(lr["r"]) or 0.0, 2)
    tr = store.fetch_one("SELECT COALESCE(SUM(net_pnl),0.0) r FROM trades "
                         "WHERE substr(created_at,1,10)=? AND status IN ('CLOSED','CLOSED_MANUAL')",
                         (date_iso,))
    trades_realized = round(_f(tr["r"]) or 0.0, 2)
    pk = store.fetch_one("SELECT MAX(balance_after) p FROM fm_ledger WHERE date=?", (date_iso,))
    peak = _f(pk["p"]) if pk else None
    max_pos, max_cap = _max_concurrent(records)
    capital_blk = {
        "opening": opening, "peak": peak,
        "closing": round(opening + ledger_realized, 2) if opening is not None else None,
        "drift": round(abs(ledger_realized - trades_realized), 2),
        "max_concurrent": max_pos,
        "utilization_pct": round(max_cap / opening * 100, 2) if (opening and max_cap) else None,
    }

    # ── deterministic highlights ──
    hi: List[str] = []
    if rmeta["overall"] != "PASS":
        hi.append(f"⚑ RECONCILIATION: {rmeta['overall']}")
    sp = profit["slip_pct_of_gross"]
    if sp is not None and sp > _SLIP_GROSS_FLAG_PCT:
        drv = slipdata["worst"][0] if slipdata["worst"] else None
        hi.append(f"⚑ HIGH SLIPPAGE: cost = {sp}% of gross (> {_SLIP_GROSS_FLAG_PCT}%)"
                  + (f"; driver {drv['symbol']} ₹{drv['total_cost']}" if drv else ""))
    # DIRECTION SKEW — compare win% only over DECIDED trades (wins+losses) per side; a
    # direction with no decided trade has no meaningful win% and is skipped, so a lone
    # never-filled short can no longer trigger a spurious "SHORT underperforms". FIX 1.
    lo_t3, sh_t3 = t3.get("LONG", {}), t3.get("SHORT", {})
    ld = lo_t3.get("wins", 0) + lo_t3.get("losses", 0)
    sd = sh_t3.get("wins", 0) + sh_t3.get("losses", 0)
    lw, sw = lo_t3.get("win_pct"), sh_t3.get("win_pct")
    if ld and sd and lw is not None and sw is not None:
        if abs(lw - sw) >= 20:
            hi.append(f"⚑ DIRECTION SKEW: LONG {lw}% vs SHORT {sw}% win — "
                      f"{'SHORT' if sw < lw else 'LONG'} underperforms ({ld}L/{sd}S decided)")
        else:
            hi.append(f"Long {lw}% vs Short {sw}% win ({ld}L/{sd}S decided)")
    if realized:
        bw = max(realized, key=lambda r: r["_net_raw"])
        bl = min(realized, key=lambda r: r["_net_raw"])
        hi.append(f"Biggest winner {bw.get('symbol')} ₹{round(bw['_net_raw'], 2)}; "
                  f"biggest loser {bl.get('symbol')} ₹{round(bl['_net_raw'], 2)}")
    low_n = sum(1 for r in sdata["t5"]["rows"] if r["trades_n"] < 3)
    if low_n:
        hi.append(f"⚑ {low_n} strategy(ies) with <3 trades in the trailing window → ranking caution")
    hi.append(f"{rmeta.get('n_pending', 0)} reconciliation section(s) PENDING_CAPTURE (broker W2/W3); "
              f"coverage gaps: MFE/MAE {mfe_cov if mfe_cov is not None else '?'}% (W6), Telegram 0% (W1), tier (W12)")

    return {"date": date_iso, "mode": meta.get("mode"),
            "banner": rmeta["overall"], "n_fail": rmeta.get("n_fail", 0),
            "coverage": coverage, "prior_date": prior_date, "deltas": deltas,
            "trading": trading, "profit": profit, "capital": capital_blk, "highlights": hi}


def render_dashboard_sheet(wb: openpyxl.Workbook, data: Dict[str, Any]) -> None:
    ws = wb.create_sheet(title="Dashboard")
    ws.sheet_view.showGridLines = False
    for i, w in enumerate([30, 20, 20, 20], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.merge_cells("A1:D1")
    t = ws.cell(1, 1, value=f"DAILY DASHBOARD — {data['date']}   |   mode={data.get('mode') or 'n/a'}")
    t.font = FONT_TITLE
    t.alignment = ALIGN_LEFT

    # ── 1 · RECONCILIATION BANNER (headline; FAIL sits above profitability) ──
    verdict = data["banner"]
    bfill = FILL_RED if data["n_fail"] else (FILL_AMBER if "pending" in verdict else FILL_GREEN)
    ws.merge_cells("A2:D2")
    b = ws.cell(2, 1, value=f"RECONCILIATION: {verdict}"
                + ("   ← headline: integrity FAIL takes precedence over P&L" if data["n_fail"] else ""))
    b.fill = bfill
    b.font = Font(name="Arial", size=12, bold=True, color=("FFFFFF" if data["n_fail"] else "000000"))
    b.alignment = ALIGN_CENTER
    for i in range(1, 5):
        ws.cell(2, i).fill = bfill
        ws.cell(2, i).border = BORDER_ALL
    ws.freeze_panes = "A3"

    def _section(r, title, fill, span=4):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=span)
        c = ws.cell(r, 1, value=title)
        c.fill = fill
        c.font = FONT_WHITE_BOLD
        c.alignment = ALIGN_LEFT
        for i in range(1, span + 1):
            ws.cell(r, i).fill = fill
            ws.cell(r, i).border = BORDER_ALL
        return r + 1

    def _kv(r, label, val, *, bold=False, money=False):
        kc = ws.cell(r, 1, value=label)
        kc.font = Font(name="Arial", size=9, bold=bold)
        kc.border = BORDER_ALL
        vc = ws.cell(r, 2, value=val)
        vc.font = Font(name="Arial", size=9, bold=bold)
        vc.alignment = ALIGN_RIGHT if isinstance(val, (int, float)) else ALIGN_LEFT
        vc.border = BORDER_ALL
        if isinstance(val, float):
            vc.number_format = NUM_FMT_CURRENCY if money else "0.00"
        return r + 1

    r = 4
    # ── 2 · DATA COVERAGE PANEL ──
    r = _section(r, "2 · DATA COVERAGE", _DASH_FILLS["coverage"])
    for h, w in zip(["Section", "Coverage", "Follow-up / Gap"], (1, 2, 3)):
        c = ws.cell(r, w, value=h)
        c.font = FONT_HEADER
        c.fill = _DASH_FILLS["coverage"]
        c.font = Font(name="Arial", size=9, bold=True, color="FFFFFF")
        c.border = BORDER_ALL
    ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=4)
    r += 1
    for cov in data["coverage"]:
        ws.cell(r, 1, value=cov["section"]).border = BORDER_ALL
        ws.cell(r, 1).font = FONT_BODY
        ws.cell(r, 2, value=cov["coverage"]).border = BORDER_ALL
        ws.cell(r, 2).font = FONT_BODY
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=4)
        ws.cell(r, 3, value=cov["note"]).border = BORDER_ALL
        ws.cell(r, 3).font = FONT_BODY
        r += 1
    r += 1

    # ── 3 · YESTERDAY vs TODAY ──
    r = _section(r, f"3 · YESTERDAY ({data['prior_date'] or 'n/a'}) vs TODAY", _DASH_FILLS["delta"])
    for h, w in zip(["Metric", "Today", "Prior", "Δ"], (1, 2, 3, 4)):
        c = ws.cell(r, w, value=h)
        c.font = Font(name="Arial", size=9, bold=True, color="FFFFFF")
        c.fill = _DASH_FILLS["delta"]
        c.border = BORDER_ALL
    r += 1
    for d in data["deltas"]:
        for w, k in ((1, "metric"), (2, "today"), (3, "prior"), (4, "delta")):
            c = ws.cell(r, w, value=d[k])
            c.font = FONT_BODY
            c.alignment = ALIGN_RIGHT if (w > 1 and isinstance(d[k], (int, float))) else ALIGN_LEFT
            c.border = BORDER_ALL
            if isinstance(d[k], float):
                c.number_format = "0.00"
        r += 1
    r += 1

    # ── 4 · TRADING SUMMARY ──
    tr = data["trading"]
    r = _section(r, "4 · TRADING SUMMARY", _DASH_FILLS["trading"])
    r = _kv(r, "Signals — webhook received", tr["wh_received"])
    r = _kv(r, "Signals — reached storage", tr["stored"])
    r = _kv(r, "Signals — qualified", tr["qualified"])
    r = _kv(r, "Trades — traded / placed / filled (entry qty>0)",
            f"{tr['traded']} / {tr['placed']} / {tr['filled']}")

    def _dir_winpct(side: str) -> str:
        d = tr[side]
        wp = d.get("win_pct")   # decided-basis (FIX 1); None when no decided trade
        return f"{d.get('trades', 0)} · {wp}%" if wp is not None else f"{d.get('trades', 0)} · N/A"
    r = _kv(r, "Long (trades · win%)", _dir_winpct("long"))
    r = _kv(r, "Short (trades · win%)", _dir_winpct("short"))
    r = _kv(r, "Closure-type breakdown", "  ".join(f"{k}:{v}" for k, v in tr["closure"].items()) or "—")
    r += 1

    # ── 5 · PROFITABILITY ──
    p = data["profit"]
    r = _section(r, "5 · PROFITABILITY", _DASH_FILLS["profit"])
    r = _kv(r, "Gross P&L", p["gross"], money=True)
    r = _kv(r, "Total charges", p["charges"], money=True)
    r = _kv(r, "Net P&L", p["net"], bold=True, money=True)
    r = _kv(r, "ROI %", p["roi_pct"] if p["roi_pct"] is not None else NA_NO_DATA)
    r = _kv(r, "Profit factor", p["profit_factor"] if p["profit_factor"] is not None else NA_NO_DATA)
    r = _kv(r, "Win rate %", p["win_rate"] if p["win_rate"] is not None else NA_NO_DATA)
    r = _kv(r, "Avg win / Avg loss", f"{p['avg_win']} / {p['avg_loss']}")
    r = _kv(r, "Max win / Max loss", f"{p['max_win']} / {p['max_loss']}")
    r = _kv(r, "Slippage cost", p["slip_cost"], money=True)
    r = _kv(r, "Slippage as % of gross",
            p["slip_pct_of_gross"] if p["slip_pct_of_gross"] is not None else NA_NO_DATA)
    r += 1

    # ── 6 · CAPITAL ──
    c = data["capital"]
    r = _section(r, "6 · CAPITAL", _DASH_FILLS["capital"])
    r = _kv(r, "Opening", c["opening"] if c["opening"] is not None else NA_NO_DATA, money=isinstance(c["opening"], float))
    r = _kv(r, "Peak", c["peak"] if c["peak"] is not None else NA_NO_DATA, money=isinstance(c["peak"], float))
    r = _kv(r, "Closing", c["closing"] if c["closing"] is not None else NA_NO_DATA, money=isinstance(c["closing"], float))
    r = _kv(r, "Capital drift (ledger vs trades)", c["drift"], money=True)
    r = _kv(r, "Max concurrent positions", c["max_concurrent"])
    r = _kv(r, "Utilization %", c["utilization_pct"] if c["utilization_pct"] is not None else NA_NO_DATA)
    r += 1

    # ── 7 · DETERMINISTIC HIGHLIGHTS (rule-based, NOT AI) ──
    r = _section(r, "7 · DETERMINISTIC HIGHLIGHTS  (rule-based, not AI)", _DASH_FILLS["highlight"])
    for h in data["highlights"]:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        hc = ws.cell(r, 1, value=h)
        hc.font = Font(name="Arial", size=9, bold=h.startswith("⚑"),
                       color=("9C0006" if h.startswith("⚑") else "000000"))
        hc.alignment = ALIGN_LEFT
        hc.border = BORDER_ALL
        r += 1


# ═════════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════════

def generate(store: StateStore, date_iso: str, output_dir: Path) -> Path:
    records, meta = build_records(store, date_iso)
    srecords, smeta = build_signal_records(store, date_iso)
    rblocks, rmeta = build_reconciliation(store, date_iso, records, smeta)
    csections, cmeta = build_config_data(store, date_iso)
    sdata = build_strategy_data(store, date_iso, records, srecords, meta)
    slipdata = build_slippage_data(store, date_iso, records, meta)
    ddata = build_dashboard_data(store, date_iso, records, srecords, meta, smeta, sdata, slipdata, rmeta)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    # Render in the FINAL tab order (Dashboard first, then the six detail sheets).
    render_dashboard_sheet(wb, ddata)
    render_reconciliation_sheet(wb, rblocks, rmeta)
    render_orders_sheet(wb, records, meta)
    render_signals_sheet(wb, srecords, smeta)
    render_strategies_sheet(wb, sdata)
    render_slippage_sheet(wb, slipdata)
    render_config_sheet(wb, csections, cmeta)
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"daily_trade_review_report_{date_iso}.xlsx"
    wb.save(out)
    _log.info("daily_trade_review: wrote %s (net=%.2f, %d trades, %d signals, reconciliation=%s, "
              "config=%s, strategies=%d/T5=%d, slippage=%d/₹%.2f)", out,
              ddata["profit"]["net"], meta["n"], smeta["n"], rmeta["overall"],
              "yes" if cmeta.get("has_snapshot") else "pending-W0",
              len(sdata["t1"]), len(sdata["t5"]["rows"]),
              slipdata["summary"]["n"], slipdata["summary"]["total_cost"])
    return out


def is_holiday_or_weekend(date_iso: str, config_dir: Path) -> bool:
    """True if date_iso is a weekend or a listed NSE holiday.

    Mirrors reports/daily_report.is_holiday_or_weekend deliberately — one behaviour,
    one shape. The holiday half delegates to utils.holiday_guard (the single source that
    parses BOTH the string and {date:, name:} dict entry formats); the weekend half stays
    local so a missing/unreadable YAML still blocks weekends rather than failing open.
    """
    dt = date.fromisoformat(date_iso)
    if dt.weekday() >= 5:
        return True
    try:
        from utils.holiday_guard import is_trading_day
        return not is_trading_day(dt, config_dir)
    except FileNotFoundError:
        # No holiday file for the year -> cannot be a listed holiday; same as before.
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="DB-pure daily trade review (Orders sheet).")
    ap.add_argument("--db", default="data_store/trading_system.db")
    ap.add_argument("--date", default=None,
                    help="YYYY-MM-DD (IST trading date); default = today IST (like the "
                         "retired daily_review/daily_report — lets cron invoke with no args)")
    ap.add_argument("--output-dir", default="reports/output")
    ap.add_argument("--config-dir", default="config",
                    help="config dir holding nse_holidays_<year>.yaml (holiday guard)")
    ap.add_argument("--force", action="store_true",
                    help="generate even on a weekend/NSE holiday")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Local imports (match daily_review): keep the module import-time surface light.
    from core.time_authority import now_ist
    from utils.cron_heartbeat import record_heartbeat
    date_iso = args.date or now_ist().strftime("%Y-%m-%d")
    db_path = Path(args.db)

    # cron_registry declares this job market_day_only + cadence: market_day, but that
    # field is METADATA ONLY — nothing enforces it, and the crontab (7 16 * * 1-5) only
    # excludes weekends. A mid-week NSE holiday fired this job, which then built and
    # emitted a report for a day with no trading ("no real alerts on non-trading days").
    # The sibling daily_report.py already self-guards; this one never did.
    if not args.force and is_holiday_or_weekend(date_iso, Path(args.config_dir)):
        _log.info("Skipping review — %s is a non-trading day", date_iso)
        print(f"Skipping: {date_iso} is a holiday or weekend (use --force to override)")
        # STILL heartbeat: monitored:true means the Cron Officer expects one, and going
        # silent would raise a false "no heartbeat" alarm on every holiday — trading a
        # spurious report for a spurious alert. F2's split says it exactly: the job
        # EXECUTED fine (SUCCESS) and produced nothing ON PURPOSE (functional SKIPPED).
        if db_path.exists():
            record_heartbeat("daily_trade_review", status="SUCCESS",
                             duration_sec=0.0, message=f"skipped: {date_iso} non-trading day",
                             functional_status="SKIPPED", db_path=db_path)
        return 0

    if not db_path.exists():
        print(f"[FATAL] DB not found: {db_path}")
        return 2
    store = StateStore(db_path)
    started = time.perf_counter()
    try:
        out = generate(store, date_iso, Path(args.output_dir))
        print(f"Wrote {out}")
        # monitored:true in cron_registry → the Cron Officer expects this heartbeat.
        record_heartbeat("daily_trade_review", status="SUCCESS",
                         duration_sec=round(time.perf_counter() - started, 2), db_path=db_path)
        return 0
    except Exception as exc:                       # record FAILED so the Officer sees it, then re-raise
        record_heartbeat("daily_trade_review", status="FAILED",
                         duration_sec=round(time.perf_counter() - started, 2),
                         message=str(exc)[:200], db_path=db_path)
        raise
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
