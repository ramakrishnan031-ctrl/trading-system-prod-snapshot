"""SCREEN 22 — HOLDINGS.  The broker-first reconciliation centre.

`gui/22. Holdings.png` + `.txt` are BINDING for structure.

    BROKER = FINAL SOURCE OF TRUTH   ·   SYSTEM = EXPECTED STATE
    DELTA  = THE DIFFERENCE BETWEEN THEM

═══════════════════════════════════════════════════════════════════════════════
⭐ THE BROKER SIDE IS REAL, AND THIS IS WHERE IT COMES FROM

  `position_reconciliation` (core/schema.sql TABLE 23) is a PER-SYMBOL, per-day
  broker-vs-system comparison written by `scripts/reconcile_positions.py` at
  15:45 IST: `broker_qty`, `system_qty` and a STRUCTURED `status` of
  OK | QTY_MISMATCH | ORPHAN_AT_BROKER | MISSING_AT_BROKER | ERROR.

  ⚠️ THIS SUPERSEDES THE OLD SCREEN'S CLAIM. The previous holdings.html said the
  broker side was "Pending Broker Source (G4/P1)" and would "activate when P1
  ships". P1 (`eod_broker_reconciliation`) is a DATE-LEVEL VERDICT table — one
  row per day, no symbols — so it was never the source this screen needed. The
  per-symbol source has existed since v20 and was simply not read.

⛔⛔ AND HERE IS WHAT THAT SOURCE CANNOT SEE — IT IS LOAD-BEARING

  `reconcile_positions._fetch_broker_positions` calls `kite.positions()` ONLY.
  It NEVER calls `kite.holdings()`. A delivery position that has settled into
  the holdings book (T+1) is therefore ABSENT from the broker side of this
  comparison, and a CNC trade still OPEN in the system will be reported
  MISSING_AT_BROKER even though the shares are sitting in the account.

  ⇒ EVERY delivery row on this screen carries that caveat, the payload states
  it, and the footer prints it. ⛔ A 15:45 "all matched" is NOT a statement that
  the delivery book is flat. Reading it as one is exactly the error this note
  exists to prevent.

⛔⛔ THERE IS NO LIVE PRICE ANYWHERE IN THIS DASHBOARD

  `ops_dashboard` has ZERO live-price call sites — `db_reader.position_
  unrealised()` documents it, is fully tested, and every caller passes
  `ltp=None`. So CURRENT PRICE, MARKET VALUE, UNREALIZED P&L, TOTAL VALUE and
  TOTAL P&L have no source and are reported as gaps with their reason. ⛔ The
  entry price is NOT substituted to make the columns look populated: it would
  make every unrealised P&L exactly zero, which reads as a measurement.

  ⭐ WHAT IS MEASURED, AND IS SHOWN UNDER ITS OWN NAME, is the value AT COST —
  filled qty × filled entry price, the definition `_position_value_of` already
  fixes. The artwork itself distinguishes the two: its SIDE-BY-SIDE panel labels
  the broker column "Market Value" and the system column "Expected Value".

═══════════════════════════════════════════════════════════════════════════════
⭐ THE THREE-WAY STATUS COMES FROM THE STRUCTURED SOURCE, ⛔ NOT FROM A BAND

  The artwork shows Matched · Minor Difference · Mismatch. Splitting them on a
  quantity threshold would be an invented classifier (and the artwork's own
  numbers contradict any single one: it calls Δ5-of-40 minor and Δ10-of-100 a
  mismatch). The SOURCE already draws the distinction that matters:

      OK                                  → Matched
      QTY_MISMATCH                        → Minor Difference   both sides hold
                                            the symbol; only the size differs
      ORPHAN_AT_BROKER · MISSING_AT_BROKER→ Mismatch           one side has
                                            NOTHING — the position is unowned
      ERROR                               → unavailable, with the reason

  ⛔ No numeric threshold is introduced anywhere on this screen.

⚠️ RECONCILIATION IS SYMBOL-GRAIN, because that is the grain the source measures
at (`_get_system_positions` sums qty_filled per SYMBOL across open trades). A
table row is (symbol, product); when one symbol spans more than one product row
the verdict is shared and every such row is flagged `recon_shared`, so a
symbol-level verdict is never silently presented as a product-level one.
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from ..readers import config_reader, db_reader
from . import freshness, scanner_attribution, strategy_meta

# ── the approved vocabularies ────────────────────────────────────────────────
#: The artwork's RECONCILIATION STATUS legend, in the approved order.
STATUSES = ("Matched", "Minor Difference", "Mismatch")

#: The structured source statuses → the approved labels. ⛔ Not a threshold.
_STATUS_MAP = {
    "OK": "Matched",
    "QTY_MISMATCH": "Minor Difference",
    "ORPHAN_AT_BROKER": "Mismatch",
    "MISSING_AT_BROKER": "Mismatch",
}

#: The artwork's HOLDINGS HEALTH SUMMARY tiles, in the order it draws them.
HEALTH_TILES = ("Orphan Position", "Unknown Position", "Quantity Mismatch",
                "Price Mismatch", "Broker Only", "System Only")

#: The product buckets the artwork's POSITIONS BY PRODUCT donut and HOLDINGS
#: CATEGORIES strip draw. ⭐ `Other` is emitted ONLY when a row actually carries
#: a product outside the three, or none at all — ⛔ never as a permanent empty
#: slot, which is what the brief means by "only when actually present".
PRODUCTS = (("MIS", "Intraday (MIS)"), ("CNC", "Delivery (CNC)"),
            ("NRML", "NRML (Carry Forward)"), ("OTHER", "Other"))

#: Products that mean an overnight/delivery holding rather than an intraday one.
#: Duplicated BY VALUE from `db_reader._trade_type_of_product` (isolation I1
#: applies to production packages; this is the GUI's own single definition,
#: reused rather than restated).
_DELIVERY_PRODUCTS = ("CNC", "NRML")


def _gap(reason: str, short: str = "") -> dict:
    return {"measured": False, "value": None, "reason": reason,
            "short": short or reason}


def _pct(n, d) -> Optional[float]:
    """⛔ None — never 0.0 — when the denominator is absent."""
    try:
        d = float(d)
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return None
    return round(100.0 * float(n) / d, 2)


#: ⛔ THE UNMEASURABLE FIELDS, named once. Every one of them needs a live price,
#: and this dashboard has no price source at all. Listing them here means the
#: table, the KPI strip, the delivery panel and the export all report the SAME
#: gap with the SAME reason — a field cannot be honest in one panel and blank in
#: another.
PRICE_GAP = ("current_price", "market_value", "unrealized_pnl")
PRICE_GAP_REASON = (
    "no live price source exists in this dashboard. ops_dashboard has zero "
    "quote call sites, so the last traded price is not available and anything "
    "derived from it — current price, market value, unrealised P&L — cannot be "
    "computed. The entry price is deliberately NOT substituted: it would make "
    "every unrealised P&L exactly zero, which reads as a measurement.")
PRICE_GAP_SHORT = "No live price source — market value needs a quote."

DELIVERY_BLIND_REASON = (
    "the 15:45 reconciliation reads kite.positions() only and never "
    "kite.holdings(), so a delivery position that has settled into the holdings "
    "book (T+1) is not visible on the broker side of this comparison. A matched "
    "verdict here is therefore NOT a statement that the delivery book is flat.")
DELIVERY_BLIND_SHORT = ("The 15:45 reconcile reads positions() only — it is "
                        "blind to settled T+1 delivery.")


def _product_of(raw) -> str:
    """The product bucket for one row. ⛔ NULL is `OTHER`, ⛔ never silently a
    product: `orders.product` is reached by a LEFT JOIN and a trade whose ENTRY
    order row is missing genuinely has no recorded product."""
    p = (raw or "").strip().upper()
    return p if p in ("MIS", "CNC", "NRML") else "OTHER"


def _holding_days(stamp: Optional[str], today: str) -> Optional[int]:
    """Calendar days since entry. ⛔ None when the stamp is missing — 0 would
    read as "opened today", a different and misleading claim."""
    if not stamp:
        return None
    try:
        a = _dt.date(*(int(x) for x in str(stamp)[:10].split("-")))
        b = _dt.date(*(int(x) for x in today.split("-")))
    except (ValueError, TypeError):
        return None
    return max(0, (b - a).days)


def _value_at_cost(row: dict) -> Optional[float]:
    """Filled qty × filled entry price — the SAME narrow definition
    `db_reader._position_value_of` fixes, reused so this screen and Screen 06
    cannot mean different things by "value". ⛔ None, never 0.0, when either
    operand is missing."""
    return db_reader._position_value_of(row)


def build_holdings_screen(cfg: dict, symbol: Optional[str] = None,
                          product: Optional[str] = None,
                          status: Optional[str] = None,
                          source: Optional[str] = None,
                          trade_type: Optional[str] = None,
                          now=None) -> dict:
    """The whole screen from ONE system read and ONE reconciliation run, so the
    KPI strip, the table, the delta panel, the orphan detector and the export
    all describe the same instant.

    ⚠️ NOT period-scoped. Holdings are a LIVE POSITION STATE and the broker side
    is whatever the most recent reconciliation run measured; a `period` would
    invite a reader to believe the book of an arbitrary past day is retrievable.
    """
    now = now or freshness.ist_now()
    today = freshness.ist_today_iso(now)

    system_rows = db_reader.holdings_system_rows(cfg)
    recon = db_reader.position_reconciliation_latest(cfg)
    timeline = db_reader.position_reconciliation_timeline(cfg)
    session = db_reader.get_session_info(cfg)
    meta = strategy_meta.strategy_meta(cfg)

    by_symbol = {r["symbol"]: r for r in recon}
    # how many table rows each symbol will produce — a symbol-level verdict
    # shown on more than one row must SAY it is shared
    rows_per_symbol: dict = {}
    for r in system_rows:
        rows_per_symbol[r["symbol"]] = rows_per_symbol.get(r["symbol"], 0) + 1

    rows = []
    for s in system_rows:
        rows.append(_system_row(s, by_symbol.get(s["symbol"]), meta, today,
                                rows_per_symbol.get(s["symbol"], 1), session))

    # ⭐ THE OTHER SIDE. A symbol the reconciliation saw but the system has no
    # CURRENTLY-OPEN trade for produces no row above, so without this loop the
    # orphan detector would be one-sided — and a one-sided orphan detector finds
    # only the orphans it was pointed at.
    # ⚠️ `ALL` is skipped: `reconcile_positions` writes a single row with
    # symbol='ALL' when the broker fetch itself failed, which is a RUN-level
    # error, ⛔ not an instrument. It is surfaced through `broker_reachable`
    # instead of being drawn as a holding.
    seen = {r["symbol"] for r in system_rows}
    fetch_failed = False
    for rec in recon:
        if rec["symbol"] == "ALL":
            fetch_failed = True
            continue
        if rec["symbol"] in seen:
            continue
        rows.append(_recon_only_row(rec, session))

    shown = _filter(rows, symbol, product, status, source, trade_type)

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "today": today,
        "rows": shown, "count": len(shown), "total": len(rows),
        "active": {"symbol": symbol or None, "product": product or None,
                   "status": status or None, "source": source or None,
                   "trade_type": trade_type or None},
        "kpi": _kpi(rows, recon),
        "delta": _delta(recon),
        "by_product": _by_product(rows),
        "categories": _by_product(rows),      # the artwork draws both, one source
        "health": _health(rows, recon),
        "broker_snapshot": _broker_snapshot(session, timeline, recon),
        "timeline": _timeline(timeline),
        "delivery": _delivery(rows, today),
        "orphans": _orphans(recon),
        "side_by_side": _side_by_side(rows, recon, timeline, now),
        "rejection_insights": _rejection_insights(cfg, today),
        "statuses": list(STATUSES),
        "filters": _filter_options(rows, meta),
        "poll_interval_ms": freshness.poll_interval_ms(cfg, now),
        "reconciliation": {
            "source": "position_reconciliation (scripts/reconcile_positions.py @ 15:45 IST)",
            "grain": "symbol",
            "rows_in_last_run": timeline["rows_in_last_run"],
            "last_run": timeline["last_run"],
            "available": bool(recon),
            # ⛔ A run whose broker fetch failed writes ONE row, symbol 'ALL',
            # status ERROR. That is a run-level failure, not a holding — the
            # screen must say the broker was unreachable rather than draw an
            # instrument called ALL.
            "broker_reachable": not fetch_failed,
        },
        "gaps": {
            "price": _gap(PRICE_GAP_REASON, PRICE_GAP_SHORT),
            "delivery_blind": _gap(DELIVERY_BLIND_REASON, DELIVERY_BLIND_SHORT),
            "price_mismatch": _gap(
                "no broker price is recorded anywhere for an open position, so "
                "a system price and a broker price cannot be compared. This "
                "tile reports unavailable rather than 0, because 0 would mean "
                "'compared, and none differ'.",
                "No broker price is stored, so prices cannot be compared."),
            "broker_sync_control": _gap(
                "this dashboard is read-only over the production database and "
                "has no write or broker path (isolation I2/L4). The broker side "
                "is refreshed by the 15:45 reconcile_positions cron, so there "
                "is nothing here that a Sync Now button could trigger.",
                "Read-only dashboard — the broker side is refreshed by the "
                "15:45 cron."),
        },
        "note": ("Broker is the final source of truth; the system side is the "
                 "expected state and Delta is the difference. The broker side "
                 "is the most recent 15:45 reconciliation run, which reads "
                 "positions() only and is blind to settled T+1 delivery. "
                 "All values are in INR; times are IST (Asia/Kolkata)."),
    }


# ── row construction ─────────────────────────────────────────────────────────
def _recon_fields(rec: Optional[dict], shared: bool) -> dict:
    """Broker Qty · System Qty · Delta Qty · Reconciliation Status for one row.

    ⛔ A symbol with NO reconciliation record gets None everywhere and the
    status "Not Reconciled" — ⛔ never "Matched". Absence of a comparison is not
    agreement, and a screen that renders it as agreement is worse than one that
    renders nothing.
    """
    if rec is None:
        return {"broker_qty": None, "system_qty": None, "delta_qty": None,
                "recon_status": None, "recon_source_status": None,
                "recon_at": None, "recon_shared": False, "recon_error": False,
                "recon_note": "no reconciliation record for this symbol"}
    src = (rec.get("status") or "").strip().upper()
    b, s = rec.get("broker_qty"), rec.get("system_qty")
    delta = (int(b) - int(s)) if (b is not None and s is not None) else None
    return {
        "broker_qty": b, "system_qty": s, "delta_qty": delta,
        "recon_status": _STATUS_MAP.get(src),
        "recon_source_status": src,
        "recon_at": rec.get("created_at"),
        "recon_resolved_at": rec.get("resolved_at"),
        "recon_shared": bool(shared),
        "recon_error": src == "ERROR",
        "recon_note": ("the reconciliation run could not read the broker for "
                       "this symbol" if src == "ERROR" else None),
    }


def _system_row(s: dict, rec: Optional[dict], meta: dict, today: str,
                rows_for_symbol: int, session: dict) -> dict:
    prod = _product_of(s.get("product"))
    info = meta.get(s.get("strategy")) or {}
    stamp = s.get("entry_time") or s.get("created_at") or ""
    out = {
        "trade_id": s.get("trade_id"),
        "trade_date": stamp[:10] or None,
        "trade_time": stamp[11:19] or None,
        "strategy": s.get("strategy"),
        "display_name": info.get("display_name") or s.get("strategy"),
        "trade_type": info.get("trade_type"),
        "symbol": s.get("symbol"),
        "product": prod,
        "product_raw": s.get("product"),
        "direction": s.get("direction"),
        "quantity": s.get("qty_filled"),
        "qty_planned": s.get("qty_planned"),
        "avg_price": s.get("entry_actual_price"),
        "value_at_cost": _value_at_cost(s),
        "holding_days": _holding_days(stamp, today),
        "is_delivery": prod in _DELIVERY_PRODUCTS,
        "origin": "both" if rec is not None else "system",
        # ⛔ the three price fields have no source — the SAME gap everywhere
        "current_price": None, "market_value": None, "unrealized_pnl": None,
    }
    out.update(_recon_fields(rec, rows_for_symbol > 1))
    out["source"] = _source_label(out, session)
    return out


def _recon_only_row(rec: dict, session: dict) -> dict:
    """A reconciled symbol with NO currently-open system trade.

    Two real cases land here and they are NOT the same thing:
      · the broker holds it and the system never did  → ORPHAN_AT_BROKER
      · the system held it at 15:45 and the broker did not, and the trade has
        since left the open set                        → MISSING_AT_BROKER
    `origin` distinguishes them so a row with no broker quantity can never
    inflate the Broker Holdings count.

    ⛔ Strategy, trade date, quantity, average price and value stay None: there
    is no open trade to read them from, and inventing an owner for an unowned
    position is precisely the failure this screen exists to expose. The artwork
    draws exactly this — its ADANIENT and WIPRO rows carry em-dashes on the left
    half and real quantities on the reconciliation half.
    """
    out = {
        "trade_id": None, "trade_date": None, "trade_time": None,
        "strategy": None, "display_name": None, "trade_type": None,
        "symbol": rec["symbol"], "product": None, "product_raw": None,
        "direction": None, "quantity": None, "qty_planned": None,
        "avg_price": None, "value_at_cost": None, "holding_days": None,
        "is_delivery": False,
        "origin": "broker" if (rec.get("broker_qty") or 0) else "recon",
        "current_price": None, "market_value": None, "unrealized_pnl": None,
    }
    out.update(_recon_fields(rec, False))
    out["source"] = _source_label(out, session)
    return out


def _source_label(row: dict, session: dict) -> str:
    """Where this row's evidence comes from. ⭐ The broker NAME is read from the
    `session` row, ⛔ never hard-coded — the artwork prints "Zerodha" because
    that is what this account is."""
    src = (row.get("recon_source_status") or "").upper()
    if src == "ORPHAN_AT_BROKER":
        return "Broker Only"
    if src == "MISSING_AT_BROKER":
        return "System Only"
    if row.get("recon_status") is not None:
        return (session.get("broker") or "broker").title()
    return "System"


# ── filters ──────────────────────────────────────────────────────────────────
def _filter(rows: list, symbol, product, status, source, trade_type) -> list:
    out = rows
    if symbol:
        q = symbol.strip().upper()
        out = [r for r in out
               if q in (r["symbol"] or "").upper()
               or q in (r["display_name"] or "").upper()
               or q in (r["strategy"] or "").upper()]
    if product:
        want = product.strip().upper()
        out = [r for r in out if r["product"] == want]
    if status:
        want = status.strip().title()
        out = [r for r in out if r["recon_status"] == want]
    if source:
        want = source.strip().lower()
        out = [r for r in out if (r["source"] or "").lower() == want]
    if trade_type:
        want = trade_type.strip().title()
        out = [r for r in out if r["trade_type"] == want]
    return out


def _filter_options(rows: list, meta: dict) -> dict:
    """⭐ EVERY OPTION IS A VALUE THAT EXISTS IN THE CURRENT DATA — a dropdown
    offering a choice that matches nothing reads as a broken control."""
    present_products = {r["product"] for r in rows if r["product"]}
    return {
        "product": [{"key": k, "label": lbl} for k, lbl in PRODUCTS
                    if k in present_products],
        "status": [s for s in STATUSES
                   if any(r["recon_status"] == s for r in rows)],
        "source": sorted({r["source"] for r in rows if r["source"]}),
        "trade_type": strategy_meta.trade_type_options(meta),
        "symbol": sorted({r["symbol"] for r in rows if r["symbol"]}),
    }


# ── panels ───────────────────────────────────────────────────────────────────
def _instruments(recon: list) -> list:
    """The reconciliation run's rows, one per SYMBOL, run-level errors removed.

    ⛔⛔ THE GRAIN MATTERS AND GETTING IT WRONG MULTIPLIES MONEY. The comparison
    is written per SYMBOL (`_get_system_positions` sums qty_filled per symbol
    across open trades), while the TABLE is per (symbol, product) and a symbol
    with four open trades produces four rows. Counting broker quantities off the
    table would add that symbol's broker qty in four times — measured on the
    shared fixture: 195 instead of 75. Every broker-side figure on this screen
    is therefore taken from HERE, ⛔ never from the table rows.
    """
    return [r for r in (recon or []) if (r.get("symbol") or "") != "ALL"]


def _kpi(rows: list, recon: list) -> dict:
    """The six approved cards.

    ⭐ THE BROKER SIDE IS SYMBOL-GRAIN (`_instruments`), the SYSTEM side is
    position-grain (one open trade = one holding). They are different bases and
    the payload names both, because "28 broker holdings vs 27 system holdings"
    is only meaningful if a reader can tell what each side counted.
    ⛔ TOTAL VALUE and TOTAL P&L are the two the artwork fills with market
    figures and this system cannot measure. They come back unavailable WITH
    their reason; the measured value AT COST travels beside them under its own
    name so the card still says something true.
    """
    inst = _instruments(recon)
    broker = [r for r in inst if r.get("broker_qty")]
    system = [r for r in rows if r["origin"] in ("system", "both")]
    statuses = [_STATUS_MAP.get((r.get("status") or "").upper()) for r in inst]
    matched = sum(1 for s in statuses if s == "Matched")
    mismatched = sum(1 for s in statuses if s in ("Minor Difference", "Mismatch"))
    reconciled = matched + mismatched
    at_cost = [r["value_at_cost"] for r in system if r["value_at_cost"] is not None]
    return {
        "broker_holdings": len(broker),
        "broker_base": "symbols with a non-zero broker quantity in the last run",
        "system_holdings": len(system),
        "system_base": "currently-open system positions",
        "matched": matched,
        "matched_pct": _pct(matched, reconciled),
        "mismatches": mismatched,
        "mismatch_pct": _pct(mismatched, reconciled),
        "reconciled": reconciled,
        "not_reconciled": sum(1 for r in rows if r["recon_status"] is None),
        # measured
        "value_at_cost": round(sum(at_cost), 2) if at_cost else None,
        "valued_positions": len(at_cost),
        "unpriced_positions": len(system) - len(at_cost),
        # ⛔ unmeasurable
        "total_value": None,
        "total_pnl": None,
    }


def _delta(recon: list) -> dict:
    """DELTA ANALYSIS — Broker − System, summed over SYMBOLS.

    ⛔ Over `_instruments`, ⛔ never over table rows — see the note there.
    ⛔ Only symbols carrying BOTH quantities contribute; the count of those
    excluded travels so the base is visible.
    """
    inst = _instruments(recon)
    pairs = [r for r in inst
             if r.get("broker_qty") is not None and r.get("system_qty") is not None]
    b = sum(int(r["broker_qty"]) for r in pairs)
    s = sum(int(r["system_qty"]) for r in pairs)
    return {"broker_qty": b, "system_qty": s, "delta_qty": b - s,
            "symbols": len(pairs),
            "excluded": len(inst) - len(pairs),
            "base": "symbols carrying both a broker and a system quantity"}


def _by_product(rows: list) -> dict:
    """POSITIONS BY PRODUCT / HOLDINGS CATEGORIES — ONE computation feeding both
    panels, so the donut and the strip beneath it can never disagree.

    ⭐ Only the SYSTEM side is bucketed: a broker-only row has no ENTRY order and
    therefore no recorded product, and counting it under "Other" would inflate a
    product breakdown with rows that have no product at all. Those rows are
    reported separately as `unknown_product`.
    """
    system = [r for r in rows if r["origin"] in ("system", "both")]
    total = len(system)
    counts = {k: 0 for k, _ in PRODUCTS}
    for r in system:
        counts[r["product"]] += 1
    slices = [{"key": k, "label": lbl, "n": counts[k], "pct": _pct(counts[k], total)}
              for k, lbl in PRODUCTS if counts[k] or k != "OTHER"]
    return {"total": total, "slices": slices,
            "unknown_product": sum(1 for r in rows if not r["product"]),
            "base": "open system positions"}


def _health(rows: list, recon: list) -> dict:
    """HOLDINGS HEALTH SUMMARY — six tiles.

      Orphan Position   a symbol present on ONE side only (either direction)
      Unknown Position  a table row the system cannot name an owner for
      Quantity Mismatch both sides hold it, the sizes differ
      Price Mismatch    ⛔ UNAVAILABLE — no broker price is recorded anywhere
      Broker Only       ORPHAN_AT_BROKER
      System Only       MISSING_AT_BROKER

    ⭐ THE FIVE RECONCILIATION TILES ARE SYMBOL-GRAIN and their percentages use
    the reconciled-symbol base; UNKNOWN POSITION is row-grain over the table and
    uses the table's base. Each tile publishes which base it used — mixing the
    two silently is how a 7.14% turns into a number of nothing.
    ⛔ Price Mismatch reports unavailable rather than 0. A 0 there would claim
    the comparison was made and found nothing, which is false — the comparison
    cannot be made at all.
    """
    inst = _instruments(recon)
    def _n(status):
        return sum(1 for r in inst if (r.get("status") or "").upper() == status)
    broker_only, system_only = _n("ORPHAN_AT_BROKER"), _n("MISSING_AT_BROKER")
    qty = _n("QTY_MISMATCH")
    unknown = sum(1 for r in rows if not r["strategy"])
    total = len(inst)
    tiles = [
        {"label": "Orphan Position", "n": broker_only + system_only,
         "pct": _pct(broker_only + system_only, total), "tone": "neg",
         "measured": True, "base": "reconciled symbols"},
        {"label": "Unknown Position", "n": unknown, "pct": _pct(unknown, len(rows)),
         "tone": "warn", "measured": True, "base": "holdings rows"},
        {"label": "Quantity Mismatch", "n": qty, "pct": _pct(qty, total),
         "tone": "yellow", "measured": True, "base": "reconciled symbols"},
        {"label": "Price Mismatch", "n": None, "pct": None, "tone": "gray",
         "measured": False, "base": None},
        {"label": "Broker Only", "n": broker_only, "pct": _pct(broker_only, total),
         "tone": "orange", "measured": True, "base": "reconciled symbols"},
        {"label": "System Only", "n": system_only, "pct": _pct(system_only, total),
         "tone": "info", "measured": True, "base": "reconciled symbols"},
    ]
    return {"tiles": tiles, "total": total, "rows": len(rows),
            "base": "reconciled symbols (Unknown Position uses holdings rows)"}


def _broker_snapshot(session: dict, timeline: dict, recon: list) -> dict:
    """BROKER SNAPSHOT.

    ⭐ LAST BROKER SYNC IS THE RECONCILIATION RUN'S OWN STAMP — the instant the
    broker was actually read for positions. ⛔ It is NOT `capital_snapshot.
    last_broker_sync`, which is the CASH sync and answers a different question.
    ⛔ There is no "auto-sync every 60 sec": the real cadence is the 15:45
    weekday cron, and the panel prints that.
    """
    account = session.get("account_id")
    broker = (session.get("broker") or "").title() or None
    return {
        "last_sync": timeline["last_run"],
        "broker": broker,
        "account_id": account,
        "account_label": ("%s (%s)" % (broker, account)) if broker and account else (broker or account),
        "mode": session.get("mode"),
        "holdings_count": sum(1 for r in _instruments(recon) if r.get("broker_qty")),
        "rows_in_last_run": timeline["rows_in_last_run"],
        "cadence": "reconcile_positions cron — 15:45 IST, Mon-Fri",
        "sync_control": False,
    }


def _timeline(timeline: dict) -> dict:
    """RECONCILIATION TIMELINE — three stamps, each from the column that records
    it. ⛔ `last_correction` is None until a row is manually resolved; the schema
    says so, so None here is a real "never", not a missing feature."""
    return {
        "last_reconciliation": timeline["last_run"],
        "last_mismatch": timeline["last_mismatch"],
        "last_correction": timeline["last_correction"],
        "runs": timeline["runs"],
        "correction_note": ("position_reconciliation.resolved_at is written only "
                            "when a mismatch is manually resolved"),
    }


def _delivery(rows: list, today: str) -> dict:
    """DELIVERY PORTFOLIO.

    ⛔ CURRENT VALUE at market and UNREALISED P&L need a live price and are
    unavailable. ⭐ The value AT COST and the average holding days ARE measured
    and are shown under their own names.
    """
    d = [r for r in rows if r["is_delivery"]]
    at_cost = [r["value_at_cost"] for r in d if r["value_at_cost"] is not None]
    days = [r["holding_days"] for r in d if r["holding_days"] is not None]
    return {
        "positions": len(d),
        "value_at_cost": round(sum(at_cost), 2) if at_cost else None,
        "valued": len(at_cost), "unpriced": len(d) - len(at_cost),
        "avg_holding_days": (round(sum(days) / len(days), 1) if days else None),
        "dated": len(days),
        "current_value": None,
        "unrealized_pnl": None,
        "products": sorted({r["product"] for r in d}),
        "blind_to_t1": True,
    }


def _orphans(recon: list) -> dict:
    """ORPHAN DETECTOR — TWO-SIDED, which is the whole point.

    A one-sided detector finds only the orphans it was pointed at; the position
    that is missing from the side you trusted is the one that costs money.
    """
    inst = _instruments(recon)
    broker_only = [r for r in inst if (r.get("status") or "").upper() == "ORPHAN_AT_BROKER"]
    system_only = [r for r in inst if (r.get("status") or "").upper() == "MISSING_AT_BROKER"]
    fmt = lambda r: {"symbol": r["symbol"], "broker_qty": r.get("broker_qty"),  # noqa: E731
                     "system_qty": r.get("system_qty"),
                     "resolved_at": r.get("resolved_at")}
    return {
        "broker_without_system": {"n": len(broker_only),
                                  "rows": [fmt(r) for r in broker_only]},
        "system_without_broker": {"n": len(system_only),
                                  "rows": [fmt(r) for r in system_only]},
        "note": DELIVERY_BLIND_SHORT,
    }


def _side_by_side(rows: list, recon: list, timeline: dict, now) -> dict:
    """BROKER SNAPSHOT vs SYSTEM SNAPSHOT.

    ⭐ The artwork's OWN LABELS already distinguish the two quantities —
    "Market Value" on the broker side, "Expected Value" on the system side — so
    showing the measured cost basis as the system's Expected Value and the
    broker's Market Value as unavailable follows the design rather than bending
    it.
    """
    system = [r for r in rows if r["origin"] in ("system", "both")]
    at_cost = [r["value_at_cost"] for r in system if r["value_at_cost"] is not None]
    return {
        "broker": {"label": "BROKER SNAPSHOT",
                   "positions": sum(1 for r in _instruments(recon)
                                    if r.get("broker_qty")),
                   "market_value": None,
                   "at": timeline["last_run"],
                   "at_label": "Last Sync"},
        "system": {"label": "SYSTEM SNAPSHOT",
                   "positions": len(system),
                   "expected_value": round(sum(at_cost), 2) if at_cost else None,
                   "at": now.strftime("%Y-%m-%d %H:%M:%S"),
                   "at_label": "Last Updated"},
    }


def _rejection_insights(cfg: dict, today: str) -> dict:
    """REJECTION / QUALITY INSIGHTS (RELATED) — the artwork puts a small
    signal-funnel summary at the foot of this screen and links it to Screen 21.

    ⭐ IT CALLS SCREEN 21'S OWN CLASSIFIER, ⛔ not a second one: the two screens
    must never disagree about how many signals were rejected today or which
    reason led.
    """
    rejected = db_reader.signals_rejected_by_status(cfg, today)
    counts = db_reader.signal_kpi_counts(cfg, today)
    by_status: dict = {}
    for r in rejected:
        by_status[r["status"]] = by_status.get(r["status"], 0) + int(r["n"])
    total = int(counts.get("total") or 0)
    accepted = int(counts.get("accepted") or 0)
    n_rej = sum(by_status.values())
    return {
        "total_signals": total,
        "accepted": accepted, "accepted_pct": _pct(accepted, total),
        "rejected": n_rej, "rejected_pct": _pct(n_rej, total),
        "top": scanner_attribution._top_reason(by_status),
        "href": "/scanner-attribution",
        "base": "stored signals today",
    }


# ── XLSX ─────────────────────────────────────────────────────────────────────
#: ⭐ THE APPROVED TABLE COLUMNS, in the artwork's order.
EXPORT_HEADER = ("Trade Date", "Trade Time", "Strategy", "Symbol", "Product",
                 "Quantity", "Avg Price", "Current Price", "Market Value",
                 "Unrealized P&L", "Broker Qty", "System Qty", "Delta Qty",
                 "Reconciliation Status", "Source")

NA = "NOT INSTRUMENTED"


def _cell(v):
    return NA if v is None or v == "" else v


def export_sheets(payload: dict) -> list:
    """[(title, header, rows)] — the FILTERED view only.

    ⛔ The three price columns export as NOT INSTRUMENTED on every row, never
    blank: a blank cell in a spreadsheet reads as zero, and a zero market value
    is a materially different claim from an unmeasured one.
    ⭐ Sheet 2 carries the orphans on BOTH sides, and Sheet 3 the gaps with their
    reasons — a workbook that omitted them would let a reader total the measured
    columns and believe they account for the whole book.
    """
    table = [list(EXPORT_HEADER)]
    for r in payload.get("rows") or []:
        table.append([
            _cell(r.get("trade_date")), _cell(r.get("trade_time")),
            _cell(r.get("display_name") or r.get("strategy")),
            _cell(r.get("symbol")), _cell(r.get("product")),
            _cell(r.get("quantity")), _cell(r.get("avg_price")),
            NA, NA, NA,                       # current price / market value / P&L
            _cell(r.get("broker_qty")), _cell(r.get("system_qty")),
            _cell(r.get("delta_qty")), _cell(r.get("recon_status")),
            _cell(r.get("source")),
        ])

    orph = [["Side", "Symbol", "Broker Qty", "System Qty", "Resolved At"]]
    o = payload.get("orphans") or {}
    for side, key in (("Broker without system record", "broker_without_system"),
                      ("System without broker record", "system_without_broker")):
        for r in (o.get(key) or {}).get("rows") or []:
            orph.append([side, r["symbol"], _cell(r["broker_qty"]),
                         _cell(r["system_qty"]), _cell(r.get("resolved_at"))])

    gaps = [["Field", "Status", "Reason"]]
    for label, key in (("Current Price / Market Value / Unrealized P&L", "price"),
                       ("Delivery T+1 visibility", "delivery_blind"),
                       ("Price Mismatch", "price_mismatch"),
                       ("Broker sync control", "broker_sync_control")):
        g = (payload.get("gaps") or {}).get(key) or {}
        gaps.append([label, NA, g.get("reason", "")])

    return [("Holdings", table[0], table[1:]),
            ("Orphans", orph[0], orph[1:]),
            ("Not Instrumented", gaps[0], gaps[1:])]


def export_rows(payload: dict) -> list:
    sheets = export_sheets(payload)
    return [list(sheets[0][1])] + [list(r) for r in sheets[0][2]]
