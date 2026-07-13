"""
scripts/forward_shadow_record.py — FORWARD SHADOW recorder (research; RECORDS ONLY).

Runs EOD, forward, alongside the live system. For EVERY scored signal of the day it
appends one JSONL record: identity, OLD score + band, the M-S4-fixed score (COMPUTED,
never used for any decision), the live ADMIT/REJECT + reason, the true-path SIMULATED
outcome (same SL/TGT/cost as the §3/Phase-2 audit), and the REALISED P&L for signals that
traded. This is the OUT-OF-SAMPLE evidence a live min_pass_score change requires (the
backfill can only estimate).

*** PLACES NO ORDERS, CHANGES NO PRODUCTION CONFIG, NEVER TOUCHES THE HOT PATH. M-S4 stays
    OFF. An EOD batch that reads + simulates + appends JSONL only. ***

Reuses core.daily_stats (no-lookahead), v3_chain.forward_shadow (tested core),
scripts.reconstruct_excursions (candle capture), the V3 JSONL-shadow pattern.

Usage:  PYTHONPATH=. python scripts/forward_shadow_record.py [--date YYYY-MM-DD] [--dry-run]
Idempotent: a signal already present in the JSONL for the date is skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

OUT_PATH = ROOT / "data_store" / "v3" / "forward_shadow.jsonl"
JOB_NAME = "forward_shadow_record"


def _weights() -> dict:
    # config/scoring_weights.yaml is the single source (its own header says so).
    import yaml
    data = yaml.safe_load((ROOT / "config" / "scoring_weights.yaml").read_text())
    return {k: float(v) for k, v in (data.get("steps") or {}).items()}


def _direction(strategy: str) -> str:
    s = (strategy or "").lower()
    return "SHORT" if any(w in s for w in ("short", "breakdown", "fade", "rejection")) else "LONG"


def _build_kite(log):
    """(kite, inst_map, sector_map) or (None, {}, {}) — read-only broker handle for the
    daily (M-S4) fetch + the 1-min ensure. Same token path as reconstruct_excursions;
    returns None on any failure (the recorder then records score/decision only)."""
    tok_path = ROOT / "data_store" / "session" / "zerodha_token.json"
    if not tok_path.exists():
        log.warning("forward_shadow.no_token — recording score/decision only")
        return None, {}, {}
    try:
        from kiteconnect import KiteConnect
        api_key = os.environ.get("ZERODHA_API_KEY_LFL836", "")
        access = json.loads(tok_path.read_text()).get("access_token")
        kite = KiteConnect(api_key=api_key); kite.set_access_token(access)
        kite.profile()
        inst = kite.instruments("NSE")
        inst_map = {i["tradingsymbol"]: i["instrument_token"] for i in inst}
        sector_map = {i["tradingsymbol"]: (i.get("segment") or "NSE") for i in inst}  # segment as coarse present-flag
        return kite, inst_map, sector_map
    except Exception as exc:  # noqa: BLE001
        log.warning("forward_shadow.broker_connect_failed err=%s — recording score/decision only", exc)
        return None, {}, {}


def main(argv=None) -> int:
    from core.state_store import StateStore
    from core.time_authority import now_ist
    from core.logger import get_logger
    from core.daily_stats import compute_daily_stats
    from sr_detector.models import Candle
    from v3_chain.forward_shadow import ms4_step_values, ms4_score, score_band, simulate_true_path
    from scripts.reconstruct_excursions import ensure_candles

    ap = argparse.ArgumentParser(prog="forward_shadow_record")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today IST)")
    ap.add_argument("--dry-run", action="store_true", help="compute + print, append nothing, no fetch")
    args = ap.parse_args(argv)

    log = get_logger(JOB_NAME)
    date_iso = args.date or now_ist().date().isoformat()
    weights = _weights()
    store = StateStore(ROOT / "data_store" / "trading_system.db")
    try:
        rows = store.fetch_all(
            """SELECT s.signal_id, s.score AS old_score, s.step_results, s.market_data_snapshot,
                      sig.symbol, sig.strategy, sig.status AS decision, sig.rejection_reason,
                      sig.trigger_price, sig.triggered_at, sig.trade_id
               FROM screener_results s JOIN signals sig ON s.signal_id = sig.signal_id
               WHERE date(s.ts) = ? AND s.score IS NOT NULL AND s.step_results IS NOT NULL""",
            (date_iso,),
        )
        seen = set()
        if OUT_PATH.exists():
            for line in open(OUT_PATH, encoding="utf-8"):
                try:
                    r = json.loads(line)
                    if r.get("date") == date_iso:
                        seen.add(r.get("signal_id"))
                except Exception:
                    continue
        rows = [r for r in rows if r["signal_id"] not in seen]
        log.info("forward_shadow.start date=%s new=%d already=%d dry_run=%s", date_iso, len(rows), len(seen), args.dry_run)
        if not rows:
            print(f"forward_shadow: date={date_iso} nothing new ({len(seen)} present)"); return 0

        kite, inst_map, sector_map = (None, {}, {}) if args.dry_run else _build_kite(log)
        now = now_ist().replace(tzinfo=None)
        # a 1-min fetcher for ensure_candles (same shape reconstruct_excursions expects)
        def onemin_fetcher(symbol, d):
            t = inst_map.get(symbol)
            if not t or kite is None:
                return None
            frm = now.replace(hour=9, minute=0); to = now.replace(hour=15, minute=31)
            frm = frm.replace(year=int(d[:4]), month=int(d[5:7]), day=int(d[8:10]))
            to = to.replace(year=int(d[:4]), month=int(d[5:7]), day=int(d[8:10]))
            rr = kite.historical_data(t, frm, to, "minute")
            return (t, rr) if rr else None

        daily_cache: dict = {}
        def dstats_for(symbol):
            if symbol in daily_cache:
                return daily_cache[symbol]
            st = {"avg_volume_20d": None, "atr14": None, "rsi14": None}
            t = inst_map.get(symbol)
            if t and kite is not None:
                try:
                    dc = [Candle.from_kite(x) for x in kite.historical_data(t, now - timedelta(days=90), now, "day")]
                    st = compute_daily_stats(dc, date_iso)
                except Exception:
                    pass
            daily_cache[symbol] = st
            return st

        written = simulated = 0; recs = []
        for r in rows:
            sr = json.loads(r["step_results"]); m = json.loads(r["market_data_snapshot"] or "{}")
            d = _direction(r["strategy"]); entry = m.get("ltp") or r["trigger_price"]
            if not args.dry_run:
                try:
                    ensure_candles(store, r["symbol"], [date_iso], fetcher=onemin_fetcher, log=log)
                except Exception:
                    pass
            cand = store.fetch_all("SELECT high,low,close,ts FROM candles WHERE symbol=? AND date=? ORDER BY ts",
                                   (r["symbol"], date_iso))
            en = str(r["triggered_at"])[11:16]
            path = [(c["high"], c["low"], c["close"]) for c in cand if str(c["ts"])[11:16] >= en]
            sim_R = simulate_true_path(float(entry), d, path) if (entry and path) else None
            if sim_R is not None:
                simulated += 1
            st = dstats_for(r["symbol"]) if not args.dry_run else {"avg_volume_20d": None, "atr14": None, "rsi14": None}
            ms4v = ms4_step_values(volume=m.get("volume", 0) or 0, ltp=entry or 0, direction=d,
                                   avg_volume_20d=st["avg_volume_20d"], atr14=st["atr14"], rsi14=st["rsi14"],
                                   sector=sector_map.get(r["symbol"]))
            new_score = ms4_score(sr, ms4v, weights)
            realized = None
            if r["trade_id"]:
                t2 = store.fetch_one("SELECT net_pnl FROM trades WHERE trade_id=?", (r["trade_id"],))
                realized = t2["net_pnl"] if t2 else None
            recs.append({"date": date_iso, "signal_id": r["signal_id"], "symbol": r["symbol"],
                         "strategy": r["strategy"], "side": d, "ts": r["triggered_at"],
                         "old_score": r["old_score"], "old_band": score_band(r["old_score"]),
                         "ms4_score": new_score, "ms4_band": score_band(new_score),
                         "ms4_stats_ok": st["atr14"] is not None,
                         "decision": r["decision"], "reject_reason": r["rejection_reason"],
                         "sim_R": sim_R, "realized_pnl": realized, "computed_at": now_ist().isoformat()})
            written += 1

        if args.dry_run:
            for rec in recs[:5]:
                print(json.dumps(rec))
            print(f"forward_shadow DRY-RUN: {written} would-write, {simulated} simulated (no fetch → ms4 fail-safe)")
        else:
            OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(OUT_PATH, "a", encoding="utf-8") as fh:
                for rec in recs:
                    fh.write(json.dumps(rec) + "\n")
            print(f"forward_shadow: date={date_iso} wrote={written} simulated={simulated} -> {OUT_PATH}")
            try:
                from utils.cron_heartbeat import record_heartbeat
                record_heartbeat(JOB_NAME, status="SUCCESS", message=f"date={date_iso} wrote={written} sim={simulated}")
            except Exception:
                pass
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
