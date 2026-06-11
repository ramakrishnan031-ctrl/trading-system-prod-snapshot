"""
tests/crash_test/forensic_reconstructor.py — Tool 11: Reconstruct complete trade lifecycle.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import (
    get_db_connection, ist_now_iso, today_str, BASE_DIR, RESULTS_DIR,
)

LOG_DIR = BASE_DIR / "logs"


def reconstruct_from_db(trade_id: str, conn) -> Dict:
    """Reconstruct trade lifecycle from DB tables."""
    result = {
        "trade_id": trade_id,
        "source": "db",
        "chain_complete": True,
        "missing_links": [],
        "reconstruction": {},
        "discrepancies": [],
    }

    # Trade
    trade = conn.execute(
        "SELECT * FROM trades WHERE trade_id=?", (trade_id,)
    ).fetchone()
    if not trade:
        return {**result, "chain_complete": False,
                "missing_links": ["trade"], "error": "Trade not found"}

    trade_dict = dict(trade)
    result["reconstruction"]["trade"] = trade_dict

    signal_id = trade["signal_id"]

    # Signal
    signal = conn.execute(
        "SELECT * FROM signals WHERE signal_id=?", (signal_id,)
    ).fetchone()
    if signal:
        result["reconstruction"]["signal"] = dict(signal)
    else:
        result["chain_complete"] = False
        result["missing_links"].append("signal")

    # Screener result
    screen = conn.execute(
        "SELECT * FROM screener_results WHERE signal_id=?", (signal_id,)
    ).fetchone()
    if screen:
        result["reconstruction"]["screen"] = dict(screen)
    else:
        result["chain_complete"] = False
        result["missing_links"].append("screener_result")

    # Capital (fm_ledger)
    ledger_rows = conn.execute(
        "SELECT * FROM fm_ledger WHERE signal_id=? OR trade_id=? ORDER BY ledger_id",
        (signal_id, trade_id)
    ).fetchall()
    capital_chain = [dict(r) for r in ledger_rows]
    result["reconstruction"]["capital"] = capital_chain

    has_reserve = any(r["entry_type"] == "RESERVE" for r in capital_chain)
    has_commit = any(r["entry_type"] == "COMMIT" for r in capital_chain)
    if not has_reserve:
        result["chain_complete"] = False
        result["missing_links"].append("fm_ledger_RESERVE")
    if not has_commit and trade["status"] in ("OPEN", "CLOSED"):
        result["chain_complete"] = False
        result["missing_links"].append("fm_ledger_COMMIT")

    # Orders
    orders = conn.execute(
        "SELECT * FROM orders WHERE trade_id=? ORDER BY placed_at", (trade_id,)
    ).fetchall()
    order_list = [dict(o) for o in orders]
    result["reconstruction"]["orders"] = order_list

    entry_orders = [o for o in order_list if o["leg"] == "ENTRY"]
    sl_orders = [o for o in order_list if o["leg"] == "SL"]

    if not entry_orders:
        result["chain_complete"] = False
        result["missing_links"].append("order_ENTRY")
    if not sl_orders and trade["status"] in ("OPEN", "CLOSED"):
        result["chain_complete"] = False
        result["missing_links"].append("order_SL")

    # Exit info
    if trade["status"] == "CLOSED":
        result["reconstruction"]["exit"] = {
            "exit_price": trade["exit_price"],
            "exit_reason": trade["exit_reason"],
            "exit_time": trade["exit_time"],
        }
        result["reconstruction"]["pnl"] = {
            "gross_pnl": trade["gross_pnl"],
            "charges": trade["charges"],
            "net_pnl": trade["net_pnl"],
        }
        # Check for RELEASE_USED in ledger
        has_release_used = any(r["entry_type"] == "RELEASE_USED" for r in capital_chain)
        if not has_release_used:
            result["chain_complete"] = False
            result["missing_links"].append("fm_ledger_RELEASE_USED")

    # Trade excursions
    excursion = conn.execute(
        "SELECT * FROM trade_excursions WHERE trade_id=?", (trade_id,)
    ).fetchone()
    if excursion:
        result["reconstruction"]["excursion"] = dict(excursion)

    # Smart TGT state
    smart_tgt = conn.execute(
        "SELECT * FROM smart_tgt_state WHERE trade_id=?", (trade_id,)
    ).fetchone()
    if smart_tgt:
        result["reconstruction"]["smart_tgt"] = dict(smart_tgt)

    # Innings (shadow tracker)
    innings = conn.execute(
        "SELECT * FROM innings WHERE trade_id=? ORDER BY inning_number", (trade_id,)
    ).fetchall()
    if innings:
        result["reconstruction"]["innings"] = [dict(i) for i in innings]

    return result


def reconstruct_from_logs(trade_id: str) -> Dict:
    """Reconstruct trade lifecycle from log files."""
    result = {
        "trade_id": trade_id,
        "source": "logs",
        "chain_complete": False,
        "missing_links": [],
        "reconstruction": {"log_entries": []},
        "discrepancies": [],
    }

    log_files = sorted(LOG_DIR.glob("trading_system*.log*")) if LOG_DIR.exists() else []
    if not log_files:
        result["missing_links"].append("no_log_files")
        return result

    entries = []
    pattern = re.compile(re.escape(trade_id))

    for log_file in log_files:
        try:
            with open(log_file, "r", errors="replace") as f:
                for line_num, line in enumerate(f, 1):
                    if pattern.search(line):
                        entries.append({
                            "file": str(log_file.name),
                            "line": line_num,
                            "content": line.strip()[:500],
                        })
        except Exception:
            continue

    result["reconstruction"]["log_entries"] = entries
    result["reconstruction"]["entry_count"] = len(entries)

    if entries:
        result["chain_complete"] = True  # Has some log evidence

    return result


def compare_reconstructions(db_result: Dict, log_result: Dict) -> Dict:
    """Compare DB and log reconstructions."""
    discrepancies = []

    db_chain = db_result.get("chain_complete", False)
    log_chain = log_result.get("chain_complete", False)

    if db_chain and not log_chain:
        discrepancies.append({
            "type": "log_gaps",
            "detail": "DB chain complete but logs have gaps",
        })
    if not db_chain and log_chain:
        discrepancies.append({
            "type": "db_gaps",
            "detail": "Log evidence exists but DB chain incomplete",
            "missing": db_result.get("missing_links", []),
        })

    return {
        "trade_id": db_result.get("trade_id"),
        "db_complete": db_chain,
        "log_complete": log_chain,
        "discrepancies": discrepancies,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Forensic Reconstructor — Crash Test Tool 11"
    )
    parser.add_argument("--trade-id", type=str, help="Trade ID to reconstruct")
    parser.add_argument("--all-trades", action="store_true",
                        help="Reconstruct all trades")
    parser.add_argument("--source", type=str, default="db",
                        choices=["db", "logs", "broker"],
                        help="Reconstruction source")
    parser.add_argument("--compare", action="store_true",
                        help="Compare DB vs logs reconstruction")
    parser.add_argument("--date", type=str, default="today",
                        help="Date filter for --all-trades")
    parser.add_argument("--output", type=str, help="Output file path")
    args = parser.parse_args()

    if args.source == "broker":
        print("BROKER_SOURCE_UNAVAILABLE -- skipping broker reconstruction")
        print("This will be enabled on Monday when API is active")
        sys.exit(0)

    if args.all_trades:
        conn = get_db_connection(readonly=True)
        date_filter = today_str() if args.date == "today" else args.date
        trades = conn.execute(
            "SELECT trade_id FROM trades WHERE date(created_at)=? ORDER BY created_at",
            (date_filter,)
        ).fetchall()

        results = []
        for t in trades:
            tid = t["trade_id"]
            if args.source == "db":
                r = reconstruct_from_db(tid, conn)
            else:
                r = reconstruct_from_logs(tid)
            results.append(r)
            status = "COMPLETE" if r["chain_complete"] else f"INCOMPLETE({r['missing_links']})"
            print(f"  {tid}: {status}")

        conn.close()

        output = {
            "timestamp": ist_now_iso(),
            "date": date_filter,
            "source": args.source,
            "trade_count": len(results),
            "complete": sum(1 for r in results if r["chain_complete"]),
            "incomplete": sum(1 for r in results if not r["chain_complete"]),
            "trades": results,
        }

    elif args.trade_id:
        if args.compare:
            conn = get_db_connection(readonly=True)
            db_result = reconstruct_from_db(args.trade_id, conn)
            conn.close()
            log_result = reconstruct_from_logs(args.trade_id)
            comparison = compare_reconstructions(db_result, log_result)
            output = {
                "timestamp": ist_now_iso(),
                "db": db_result,
                "logs": log_result,
                "comparison": comparison,
            }
        elif args.source == "db":
            conn = get_db_connection(readonly=True)
            output = reconstruct_from_db(args.trade_id, conn)
            conn.close()
        else:
            output = reconstruct_from_logs(args.trade_id)
    else:
        parser.print_help()
        sys.exit(0)

    output_str = json.dumps(output, indent=2, default=str)
    print(output_str)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            f.write(output_str)


if __name__ == "__main__":
    main()
