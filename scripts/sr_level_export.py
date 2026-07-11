"""
scripts/sr_level_export.py — Trading System v2 · V3 03.01 S&R Detection

Purpose (Task 4 — manual-marking validation):
    READ-ONLY export of the detector's ANCHOR + SWING levels for a set of
    symbols + dates, in a flat CSV Rama can visually compare against his MANUAL
    chart marks. This is the #1 03.01 safeguard: validate auto-detected S&R
    against the human eye BEFORE it ever gates capital. It changes NO trading
    behaviour — it runs the same pure detection path (with the intraday-anchor
    layer turned on) and dumps the levels; it never places, sizes, or persists.

Design:
    - The pure core (`flatten_analysis`, `rows_to_csv`, `export_levels`) is fully
      unit-tested with an injected analyze function — no broker needed.
    - `main()` wires a READ-ONLY market-data kite + the sr_detector OhlcFetcher,
      builds a detector per as-of date (anchors enabled), and writes the CSV.
      Run on the VM with a live token (like sr_detector_backfill).

Usage:
    python scripts/sr_level_export.py --symbols RELIANCE,TCS --dates 2026-07-10 \
        [--interval 5minute --orb-window 15 --out reports/output/sr_levels.csv]
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sr_detector.models import CONF_ANCHOR_ONLY, Candidate, SRAnalysis  # noqa: E402

# Flat CSV column order (comparison-friendly for the human eye).
COLUMNS = [
    "symbol", "date", "layer", "level_type", "role_timeframe",
    "kind", "price_low", "price_high", "confidence", "confidence_class",
]


def _row(symbol, on_date, layer, level_type, role_tf, kind,
         price_low, price_high, confidence, confidence_class) -> dict:
    return {
        "symbol": symbol,
        "date": on_date,
        "layer": layer,
        "level_type": level_type,
        "role_timeframe": role_tf,
        "kind": kind or "",
        "price_low": price_low,
        "price_high": price_high,
        "confidence": confidence or "",
        "confidence_class": confidence_class,
    }


def flatten_analysis(symbol: str, on_date: str, analysis: SRAnalysis) -> List[dict]:
    """Flatten one SRAnalysis into level rows (anchors + swings). Pure."""
    cc = getattr(analysis, "confidence_class", CONF_ANCHOR_ONLY)
    anchors = getattr(analysis, "anchors", {}) or {}
    swings = getattr(analysis, "swings", {}) or {}
    rows: List[dict] = []

    # ── Layer A: anchors ──
    for name, price in (anchors.get("prior_day") or {}).items():
        rows.append(_row(symbol, on_date, "ANCHOR", name, "", "", price, price, "", cc))
    for price in anchors.get("round_numbers") or []:
        rows.append(_row(symbol, on_date, "ANCHOR", "ROUND", "", "", price, price, "", cc))
    if anchors.get("vwap") is not None:
        rows.append(_row(symbol, on_date, "ANCHOR", "VWAP", "", "",
                         anchors["vwap"], anchors["vwap"], "", cc))
    if anchors.get("orb_high") is not None:
        rows.append(_row(symbol, on_date, "ANCHOR", "ORB_HIGH", "", "",
                         anchors["orb_high"], anchors["orb_high"], "", cc))
    if anchors.get("orb_low") is not None:
        rows.append(_row(symbol, on_date, "ANCHOR", "ORB_LOW", "", "",
                         anchors["orb_low"], anchors["orb_low"], "", cc))

    # ── Layer B: structural swings (labelled PRIMARY/MAJOR/…; NOT-YET-VALIDATED) ──
    for role, zones in swings.items():
        for z in zones:
            rows.append(_row(
                symbol, on_date, "SWING", "SWING", role, z.get("kind"),
                z.get("band_low"), z.get("band_high"), z.get("confidence"), cc,
            ))
    return rows


def rows_to_csv(rows: Sequence[dict]) -> str:
    """Render rows to a CSV string with the fixed header. Pure."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def export_levels(
    symbols: Sequence[str],
    dates: Sequence[str],
    analyze_fn: Callable[[str, str], Optional[SRAnalysis]],
) -> List[dict]:
    """For every (symbol, date), call `analyze_fn(symbol, date) -> SRAnalysis`
    and flatten to level rows. `analyze_fn` is injected so the core is testable
    without a broker. A None analysis (fetch failure) contributes no rows. Pure
    w.r.t. `analyze_fn`."""
    out: List[dict] = []
    for sym in symbols:
        for d in dates:
            analysis = analyze_fn(sym, d)
            if analysis is None:
                continue
            out.extend(flatten_analysis(sym, d, analysis))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Real wiring (VM / live token). Not unit-tested (needs a broker session).
# ─────────────────────────────────────────────────────────────────────────────

def _make_real_analyze_fn(interval: str, orb_window: int):  # pragma: no cover
    """Build analyze_fn wiring a READ-ONLY market-data kite + OhlcFetcher +
    detector (anchors enabled), one as-of the requested date's session close."""
    from core.config_loader import load_all
    from core.instrument_cache import InstrumentCache
    from core.logger import get_logger
    from core.market_windows import MarketWindows
    from broker.rate_limiter import RateLimiter
    from sr_detector import build_sr_detector
    # Reuse main.py's read-only market-data kite + rate-limited fetch closure.
    from main import _build_market_data_kite, _make_sr_fetch_fn, _parse_hhmm

    app_config = load_all(str(ROOT / "config"))
    log = get_logger("sr_level_export")
    instrument_cache = InstrumentCache(str(ROOT / "config" / "instruments.csv"))
    instrument_cache.load()
    md_kite = _build_market_data_kite(is_paper=False, kite_client=None)
    rate_limiter = RateLimiter(app_config.broker_limits)
    fetch_fn = _make_sr_fetch_fn(md_kite, rate_limiter)

    th = app_config.system.trading_hours
    s_open, s_close = _parse_hhmm(th.market_open), _parse_hhmm(th.market_close)

    # Anchors ON + the requested fine interval / ORB window (a COPY — never
    # mutate the live config object used by the running system).
    sr_cfg = app_config.system.sr_detector.model_copy(update={
        "enabled": True, "intraday_anchors_enabled": True,
        "anchor_intraday_interval": interval, "orb_window_minutes": orb_window,
    })

    def analyze_fn(symbol: str, on_date: str) -> Optional[SRAnalysis]:
        as_of = datetime.combine(date.fromisoformat(on_date), time(15, 30))
        det = build_sr_detector(
            config=sr_cfg, fetch_fn=fetch_fn, instrument_cache=instrument_cache,
            store=None, logger=log, mode="live", now_fn=lambda: as_of,
            session_open=s_open, session_close=s_close,
        )
        # Reference price = the day's last close (from the daily fetch).
        daily = det._fetcher.fetch_interval(symbol, "day", int(sr_cfg.lookback_days)) or []
        ref = float(daily[-1].close) if daily else 0.0
        cand = Candidate(
            signal_id=f"export::{symbol}::{on_date}", symbol=symbol,
            strategy="__export__", direction="LONG", intended_entry=ref,
            sl_price=0.0, tgt_price=0.0, qty=0, intent="INTRADAY",
            mode="live", ts=as_of, score=None,
        )
        return det.analyze(cand)

    return analyze_fn


def main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description="Export S&R anchor+swing levels for manual validation.")
    ap.add_argument("--symbols", required=True, help="comma-separated symbols")
    ap.add_argument("--dates", required=True, help="comma-separated YYYY-MM-DD")
    ap.add_argument("--interval", default="5minute", help="fine intraday interval for VWAP/ORB")
    ap.add_argument("--orb-window", type=int, default=15, help="opening-range window (minutes)")
    ap.add_argument("--out", default=None, help="output CSV path")
    args = ap.parse_args(argv)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]

    analyze_fn = _make_real_analyze_fn(args.interval, args.orb_window)
    rows = export_levels(symbols, dates, analyze_fn)
    csv_text = rows_to_csv(rows)

    out_path = Path(args.out) if args.out else (ROOT / "reports" / "output" / "sr_levels.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(csv_text, encoding="utf-8")
    print(f"sr_level_export: wrote {len(rows)} level rows for "
          f"{len(symbols)} symbol(s) × {len(dates)} date(s) → {out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
