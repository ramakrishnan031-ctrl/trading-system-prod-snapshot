"""
Risk & capital screens (M7-M10, all read-only, login_required):
  GET /api/risk      — daily loss / consec losses / kill switch / positions / open risk
  GET /api/capital   — opening / allocated / used / remaining + fm_ledger view
  GET /api/exposure  — gross + per-strategy + per-symbol concentration
  GET /api/pnl       — realized splits + intraday equity curve (fm_ledger)
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from ..auth import login_required
from ..readers import config_reader, db_reader
from ..services import freshness

risk_capital_api = Blueprint("risk_capital_api", __name__)


def _ctx():
    cfg = current_app.config["GUI_CONFIG"]
    today = freshness.ist_today_iso()
    return cfg, today


@risk_capital_api.route("/api/risk", methods=["GET"])
@login_required
def get_risk():
    cfg, today = _ctx()
    sc = config_reader.get_system_config(cfg, today)
    risk = (sc.get("risk") or {}) if isinstance(sc, dict) else {}
    opening = db_reader.opening_capital(cfg, today)
    loss_pct = risk.get("daily_loss_limit_pct")
    loss_limit = round(float(loss_pct) * opening, 2) if (loss_pct and opening) else None
    ks = db_reader.get_kill_switch(cfg)
    return jsonify({
        "today": today,
        # D2 (approved permanent): reads fm_ledger.RELEASE_USED.pnl_delta losses
        # directly, NOT get_daily_realized_net_pnl.
        # 25-Jul-2026 RE-LABEL: the reason changed and the label had not. W10 (that
        # reader's cost double-subtract) was FIXED 2026-07-17, so it is no longer why
        # the reader is avoided. The live reason is RESET_PNL: get_daily_realized_net_pnl
        # sums ALL pnl_delta rows including the EOD RESET_PNL counter-entry, which
        # post-15:17 would zero the day's realized loss. RELEASE_USED.pnl_delta stays
        # the direct realized-loss source.
        "daily_loss": {
            "used": db_reader.realized_loss_today(cfg, today),
            "limit": loss_limit, "limit_pct": loss_pct,
            "source_note": "fm_ledger RELEASE_USED losses (D2; reader excluded — EOD RESET_PNL)",
        },
        "consecutive_losses": {
            "used": db_reader.consecutive_loss_streak(cfg),
            "limit": risk.get("max_consecutive_losses"),
        },
        "kill_switch": {
            "state": ks.get("state"), "reason": ks.get("reason"),
            "since": ks.get("triggered_at"), "by": ks.get("triggered_by"),
            "halted": ks.get("state") in ("SOFT_KILL", "HARD_KILL"),
        },
        "open_positions": {
            "used": db_reader.open_positions_count(cfg),
            "limit": risk.get("max_open_positions"),
        },
        "open_risk_amount": db_reader.open_risk_amount_sum(cfg),
        "opening_capital": round(opening, 2) if opening else None,
    })


@risk_capital_api.route("/api/capital", methods=["GET"])
@login_required
def get_capital():
    cfg, today = _ctx()
    sc = config_reader.get_system_config(cfg, today)
    cap_cfg = (sc.get("capital") or {}) if isinstance(sc, dict) else {}
    opening = db_reader.opening_capital(cfg, today)
    usage = db_reader.capital_usage(cfg, today)
    intraday_pct = cap_cfg.get("intraday_bucket_pct")
    positional_pct = cap_cfg.get("positional_bucket_pct")
    allocated = round(float(intraday_pct) * opening, 2) if (intraday_pct and opening) else None
    used = round(usage["margin_used"], 2)
    return jsonify({
        "today": today,
        "opening_capital": round(opening, 2) if opening else None,   # today's FIRST INIT row
        "buckets": {
            "intraday_pct": intraday_pct, "positional_pct": positional_pct,
            "intraday_allocated": allocated,
        },
        "used": used,
        "pending": round(usage["margin_reserved"], 2),
        "remaining": round(allocated - used, 2) if allocated is not None else None,
        "deployed_pct": round(100.0 * used / opening, 1) if opening else None,
        "realized_pnl_today": round(usage["realized_pnl_today"], 2),
        "ledger": db_reader.ledger_entries(cfg, today),              # newest first
    })


#: Fields the engine does not persist anywhere the GUI can reach. Isolation rule
#: I4 forbids kiteconnect in this service, so a broker read is not an option —
#: these render as an explicit not-observed state rather than an invented number.
_UNOBSERVED = {
    "available": False,
    "value": None,
    "reason": ("not recorded in any GUI-readable source: the engine does not "
               "persist broker pay-in/pay-out, and isolation rule I4 forbids a "
               "broker call from the dashboard service"),
}


#: THE PINNED SIMULATION — Rama's agreed scenario, 14-Aug-2026. These are the
#: scenario's OWN constants and are deliberately NOT read from live config: a
#: pinned case must stay reproducible even if config drifts. They currently match
#: the deployed config (0.70/0.30, 5x/1x), and the screen says so.
#: ⛔ These values NEVER touch the live figures — they are computed by a pure
#: function below and returned under their own key.
_PINNED = {
    "opening_real_cash": 10000.0,
    "additional_payin": 5000.0,
    "payout": 0.0,
    "mis_order_notional": 6000.0,
    "gtt_order_notional": 2000.0,
    "mis_pct": 0.70, "gtt_pct": 0.30,
    "mis_leverage": 5.0, "gtt_leverage": 1.0,
}


def _pinned_side(real_cash: float) -> dict:
    """One side (BEFORE or AFTER) of the pinned simulation.

    Uses the SAME arithmetic as the live path, so the simulation demonstrates the
    engine's own semantics rather than a separate model:
        real allocation  = real cash x bucket pct
        segment capacity = real allocation x leverage
        real reserved    = order notional / leverage      <- NOT the notional
        remaining        = (allocation - reserved) x leverage
    """
    p = _PINNED
    out = {"total_real_cash": round(real_cash, 2)}
    for key, pct, lev, notional in (
            ("mis", p["mis_pct"], p["mis_leverage"], p["mis_order_notional"]),
            ("gtt", p["gtt_pct"], p["gtt_leverage"], p["gtt_order_notional"])):
        alloc = round(real_cash * pct, 2)
        reserved = round(notional / lev, 2)     # 6,000 @5x -> 1,200 · 2,000 @1x -> 2,000
        remaining_real = round(alloc - reserved, 2)
        out[key] = {
            "real_allocation": alloc,
            "segment_capacity": round(alloc * lev, 2),
            "order_notional": notional,
            "real_reserved": reserved,
            "remaining_real": remaining_real,
            "remaining_segment_capacity": round(remaining_real * lev, 2),
        }
    consumed = round(out["mis"]["real_reserved"] + out["gtt"]["real_reserved"], 2)
    out["real_cash_consumed"] = consumed
    out["remaining_real_cash"] = round(real_cash - consumed, 2)
    out["total_segment_capacity"] = round(
        out["mis"]["segment_capacity"] + out["gtt"]["segment_capacity"], 2)
    out["total_remaining_capacity"] = round(
        out["mis"]["remaining_segment_capacity"]
        + out["gtt"]["remaining_segment_capacity"], 2)
    return out


def pinned_simulation() -> dict:
    """Deterministic BEFORE/AFTER pay-in comparison. Pure — no DB, no config, no
    live value reaches it, so it renders identically on any machine and any day.
    """
    p = _PINNED
    before_cash = p["opening_real_cash"]
    after_cash = before_cash + p["additional_payin"] - p["payout"]
    return {"input": dict(p),
            "before": _pinned_side(before_cash),
            "after": _pinned_side(after_cash)}


@risk_capital_api.route("/api/capital/segments", methods=["GET"])
@login_required
def get_capital_segments():
    """Screen-08 capital flow: real cash, and the MIS/GTT real-vs-segment split.

    EVERY number here is engine truth read from the DB. Nothing is derived from
    the mockup's pinned simulation, and no value is invented when a source is
    missing — see _UNOBSERVED.

    THE FOUR QUANTITIES ARE KEPT APART, because conflating them is the whole
    reason this screen exists:
      real cash          broker cash the engine believes it has (fm_ledger)
      real capital       cash allocated to a segment (real cash x bucket pct)
      segment capacity   real capital x leverage — BUYING POWER, not cash
      real committed     trades.margin_reserved — cash actually committed
    Order notional is DERIVED as committed x leverage, never the reverse: the
    engine stores margin, so margin is the authority (capital/fund_manager.py
    required_margin — "NOT notional").
    """
    cfg, today = _ctx()
    sc = config_reader.get_system_config(cfg, today)
    cap_cfg = (sc.get("capital") or {}) if isinstance(sc, dict) else {}
    lev_map = (cap_cfg.get("leverage_map") or {}) if isinstance(cap_cfg, dict) else {}

    opening = db_reader.opening_capital(cfg, today)          # day's FIRST INIT
    total_live = db_reader.current_total_capital(cfg, today)  # latest INIT/SYNC
    seg = db_reader.capital_by_segment(cfg, today)

    pcts = {"intraday": cap_cfg.get("intraday_bucket_pct"),
            "delivery": cap_cfg.get("positional_bucket_pct")}
    levs = {"intraday": lev_map.get("INTRADAY"), "delivery": lev_map.get("DELIVERY")}

    segments = {}
    for key in ("intraday", "delivery"):
        pct, lev = pcts[key], levs[key]
        committed = seg[key]["real_committed"]
        real_alloc = (round(float(pct) * total_live, 2)
                      if (pct is not None and total_live is not None) else None)
        capacity = (round(real_alloc * float(lev), 2)
                    if (real_alloc is not None and lev is not None) else None)
        remaining_real = (round(real_alloc - committed, 2)
                          if real_alloc is not None else None)
        segments[key] = {
            "allocation_pct": pct,
            "leverage": lev,
            "real_allocation": real_alloc,
            "segment_capacity": capacity,
            "real_used": seg[key]["real_used"],
            "real_reserved": seg[key]["real_reserved"],
            "real_committed": committed,
            # DERIVED from margin, never the source of it.
            "order_value_notional": (round(committed * float(lev), 2)
                                     if lev is not None else None),
            "remaining_real": remaining_real,
            "remaining_segment_capacity": (
                round(remaining_real * float(lev), 2)
                if (remaining_real is not None and lev is not None) else None),
        }

    consumed = round(seg["intraday"]["real_committed"]
                     + seg["delivery"]["real_committed"], 2)
    return jsonify({
        "today": today,
        "real_cash": {
            "opening": round(opening, 2) if opening is not None else None,
            "payin_today": _UNOBSERVED,
            "payout_today": _UNOBSERVED,
            "total_live": round(total_live, 2) if total_live is not None else None,
            "consumed": consumed,
            "available": (round(total_live - consumed, 2)
                          if total_live is not None else None),
            # Stated so the screen can show WHY total may equal opening.
            "moved_since_open": (
                round(total_live - opening, 2)
                if (total_live is not None and opening is not None) else None),
            "formula": "real cash = opening + pay-in - pay-out",
            # ⭐ What makes `total_live` honest: it is the ENGINE'S BELIEF as of
            # its last broker sync, ⛔ not the broker's current balance. The engine
            # syncs once per day, so a later movement is not reflected — the screen
            # prints this so a stale basis is visible rather than silent.
            "last_sync": db_reader.last_capital_sync(cfg, today),
            "basis_note": (
                "engine truth, not the broker's live balance: the engine re-reads "
                "broker cash once per day, so any pay-in or pay-out after that "
                "sync is NOT reflected above"
            ),
        },
        "config": {
            "intraday_pct": pcts["intraday"], "delivery_pct": pcts["delivery"],
            "intraday_leverage": levs["intraday"],
            "delivery_leverage": levs["delivery"],
            "slm_margin_buffer_pct": cap_cfg.get("slm_margin_buffer_pct"),
        },
        "segments": segments,
        # ⛔ SEPARATE KEY, SEPARATE ARITHMETIC, NO LIVE INPUT. The screen renders
        # this under its own "(Pinned Simulation)" heading and never merges it
        # with the live figures above.
        "simulation": pinned_simulation(),
        "strategies": db_reader.strategy_capital_by_segment(cfg, today),
        "source_note": (
            "engine truth from fm_ledger + trades.margin_reserved; "
            "margin is qty*price/leverage (NOT notional); the 5% SL-M buffer is "
            "held in fm_ledger bucket availability and is not in these figures"
        ),
    })


@risk_capital_api.route("/api/exposure", methods=["GET"])
@login_required
def get_exposure():
    cfg, today = _ctx()
    opening = db_reader.opening_capital(cfg, today)
    usage = db_reader.capital_usage(cfg, today)
    data = db_reader.exposure_breakdown(cfg, top_n=10)
    data.update({
        "today": today,
        "opening_capital": round(opening, 2) if opening else None,
        "margin_used": round(usage["margin_used"], 2),
        "margin_vs_capital_pct": (round(100.0 * usage["margin_used"] / opening, 1)
                                  if opening else None),
        # G5b additive (Capital & Risk exposure zone): long/short/net split.
        "by_direction": db_reader.exposure_by_direction(cfg),
    })
    return jsonify(data)


@risk_capital_api.route("/api/pnl", methods=["GET"])
@login_required
def get_pnl():
    cfg, today = _ctx()
    session = db_reader.get_session_info(cfg)
    summary = db_reader.pnl_summary_today(cfg, today)
    return jsonify({
        "today": today,
        "mode": session.get("mode"),
        # trades carry no mode column — one DB is one mode; stated, not split.
        "mode_note": "single-mode DB: trades are not mode-tagged; "
                     "this DB's session mode is shown as data",
        "summary": summary,
        "equity_curve": db_reader.equity_curve_points(cfg, today),   # fm_ledger seq
        "curve_note": "cumulative fm_ledger RELEASE_USED.pnl_delta (realized only)",
        # B8/A9 (additive — G2a byte-compat precedent): closed trades detail.
        "closed_trades": db_reader.closed_trades_today(cfg, today),
    })
