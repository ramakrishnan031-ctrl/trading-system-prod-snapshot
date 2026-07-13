#!/usr/bin/env python3
"""
generate_screened_stocks_csv.py -- Trading System v2

PURPOSE:
    Generate screened_stocks_YYYY-MM-DD.csv for post-trade analysis.

    Queries StateStore DB (trades + signals tables) to extract:
    - Symbols that got ORDER PLACED → TRADED column
    - Symbols that were REJECTED/SKIPPED → NON_TRADED + REJECTION_REASON columns

    Output is a 3-column CSV (vertical layout) for easy Excel/candle analysis.

USAGE WITH CANDLE_FETCHER:
    1. This script runs daily at 16:01 via cron
    2. Download the CSV from VM
    3. Run: python candle_fetcher.py YYYY-MM-DD
    4. Analyze candles vs our entry/SL/TGT levels
    5. Tune strategy parameters

CRON ENTRY (to add to VM crontab):
    1 16 * * 1-5 cd /home/ubuntu/systems/trading-system && /home/ubuntu/systems/venv/bin/python scripts/generate_screened_stocks_csv.py >> logs/cron.log 2>&1

Paper/Live parity: UNIFIED - works for both modes

FIX-039: Rewritten to use StateStore DB queries instead of log parsing.
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.state_store import StateStore


# ---------------------------------------------------------------------------
# Rejection reason expansion
# ---------------------------------------------------------------------------

def expand_rejection_reason(rejection_reason: str | None, status: str) -> str:
    """
    Expand rejection reason into human-readable format.

    Args:
        rejection_reason: The rejection_reason field from signals table (may be None)
        status: The status field (e.g., 'REJECTED_SCORE', 'DROPPED_DEDUP')

    Returns:
        Expanded human-readable reason string
    """
    # If we have a specific reason, use it
    if rejection_reason:
        return rejection_reason

    # Otherwise derive from status code
    STATUS_MAP = {
        # Signal-level rejections
        'DROPPED_DEDUP': 'Duplicate signal (same symbol+scanner+minute)',
        'DROPPED_EXPIRED': 'Signal expired before processing',
        'DROPPED_KILL_SWITCH': 'Kill switch active',
        'REJECTED_DUPLICATE_SYMBOL': 'Duplicate symbol (active position exists)',
        'REJECTED_SCORE': 'Score too low',
        'REJECTED_OUTSIDE_ENTRY_WINDOW': 'Outside entry window',
        'REJECTED_CAPITAL_EXCEEDED': 'Insufficient capital',
        'REJECTED_MAX_POSITIONS': 'Max positions limit reached',
        'REJECTED_QUOTE_UNAVAILABLE': 'Quote unavailable (API failure)',
        'REJECTED_INVALID_DERIVED_PRICE': 'Invalid derived price',
        'REJECTED_TGT_DISTANCE_TOO_SMALL': 'Target distance too small',
        'REJECTED_UNKNOWN_STRATEGY': 'Unknown strategy',
        'REJECTED_PLACEMENT_FAILED': 'Order placement failed',

        # Screener rejections
        'SKIPPED_EXECUTOR_ERROR': 'Screener executor error',
        'SKIPPED_SCORER_ERROR': 'Screener scorer error',
        'SKIPPED_QUOTE_UNAVAILABLE': 'Quote unavailable (API failure)',
    }

    # Check for exact match
    if status in STATUS_MAP:
        return STATUS_MAP[status]

    # Check for prefixed matches (e.g., REJECTED_SCORE_48)
    if status.startswith('REJECTED_SCORE_'):
        score = status.replace('REJECTED_SCORE_', '')
        return f'Score too low ({score}/100)'

    # Fallback: clean up the status code
    if status.startswith('REJECTED_'):
        clean = status.replace('REJECTED_', '').replace('_', ' ').title()
        return clean
    elif status.startswith('DROPPED_'):
        clean = status.replace('DROPPED_', '').replace('_', ' ').title()
        return f'Dropped: {clean}'
    elif status.startswith('SKIPPED_'):
        clean = status.replace('SKIPPED_', '').replace('_', ' ').title()
        return f'Skipped: {clean}'

    return status.replace('_', ' ').title()


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

def get_traded_symbols(store: StateStore, date_str: str) -> List[str]:
    """
    Query trades table for symbols that were actually traded on the given date.

    Args:
        store: StateStore instance
        date_str: Date in YYYY-MM-DD format

    Returns:
        Sorted list of unique traded symbols
    """
    query = """
        SELECT DISTINCT symbol
        FROM trades
        WHERE substr(created_at, 1, 10) = ?
          AND status NOT IN ('CANCELLED', 'FAILED')
        ORDER BY symbol
    """

    with store.transaction(readonly=True) as cur:
        cur.execute(query, (date_str,))
        rows = cur.fetchall()

    return [row[0] for row in rows]


def get_non_traded_symbols(store: StateStore, date_str: str) -> List[Tuple[str, str]]:
    """
    Query signals table for symbols that were rejected/dropped on the given date.

    Args:
        store: StateStore instance
        date_str: Date in YYYY-MM-DD format

    Returns:
        List of (symbol, reason) tuples for non-traded symbols
    """
    query = """
        SELECT symbol, status, rejection_reason
        FROM signals
        WHERE substr(received_at, 1, 10) = ?
          AND (status LIKE 'REJECTED_%' OR status LIKE 'DROPPED_%' OR status LIKE 'SKIPPED_%')
        ORDER BY received_at
    """

    with store.transaction(readonly=True) as cur:
        cur.execute(query, (date_str,))
        rows = cur.fetchall()

    # Deduplicate symbols (keep first occurrence with its reason)
    seen = set()
    result = []
    for symbol, status, rejection_reason in rows:
        if symbol not in seen:
            seen.add(symbol)
            expanded_reason = expand_rejection_reason(rejection_reason, status)
            result.append((symbol, expanded_reason))

    return result


# ---------------------------------------------------------------------------
# CSV generation
# ---------------------------------------------------------------------------

def generate_csv(
    date_str: str,
    traded: List[str],
    non_traded_with_reasons: List[Tuple[str, str]],
    output_dir: Path,
) -> Path:
    """
    Generate screened_stocks_YYYY-MM-DD.csv with 3-column vertical layout.

    Args:
        date_str: Date in YYYY-MM-DD format
        traded: List of symbols that got ORDER PLACED
        non_traded_with_reasons: List of (symbol, reason) tuples
        output_dir: Directory to write CSV (reports/daily_review/)

    Returns:
        Path to generated CSV file
    """
    # Extract non-traded symbols and reasons
    non_traded = [symbol for symbol, _ in non_traded_with_reasons]
    reasons = [reason for _, reason in non_traded_with_reasons]

    # Pad lists to same length (at least 1 for header)
    max_len = max(len(traded), len(non_traded), 1)

    traded_padded = traded + [''] * (max_len - len(traded))
    non_traded_padded = non_traded + [''] * (max_len - len(non_traded))
    reasons_padded = reasons + [''] * (max_len - len(reasons))

    # Create output file
    output_path = output_dir / f"screened_stocks_{date_str}.csv"
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)

        # Header
        writer.writerow(['TRADED', 'NON_TRADED', 'REJECTION_REASON'])

        # Data rows
        for t, nt, r in zip(traded_padded, non_traded_padded, reasons_padded):
            writer.writerow([t, nt, r])

    return output_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """
    Generate screened stocks CSV for today's trading session.

    Returns:
        0 on success, 1 on error
    """
    # Determine date (default: today)
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            print(f"ERROR: Invalid date format. Use YYYY-MM-DD", file=sys.stderr)
            return 1
    else:
        date_str = datetime.now().strftime('%Y-%m-%d')

    # Paths
    project_root = Path(__file__).parent.parent
    # M-SC2 (audit 04-Jul): was `data/state.db` — a path that has never existed since the
    # FIX-039 rewrite, so this crond job (16:01 Mon-Fri) hit the not-found branch every trading
    # day and wrote an empty headers-only CSV while returning 0 (SUCCESS). The canonical main DB
    # is data_store/trading_system.db (see capture_metrics_baseline / check_cron_drift).
    db_path = project_root / "data_store" / "trading_system.db"
    output_dir = project_root / "reports" / "daily_review"

    print(f"Generating screened stocks CSV for {date_str}...")
    print(f"  Database: {db_path}")

    # Check DB exists
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}", file=sys.stderr)
        print("  Creating empty CSV with headers only...")
        # Create empty CSV with headers (FIX-039: no crash on empty DB) so downstream report
        # consumers still find the file, BUT return non-zero — a genuinely missing production DB
        # is a real failure the Cron Officer must SEE, not a silent SUCCESS (M-SC2).
        csv_path = generate_csv(date_str, [], [], output_dir)
        print(f"  CSV written to: {csv_path}")
        print("  WARNING: No data (database not found)")
        return 1

    # Query database
    try:
        store = StateStore(db_path=db_path)

        traded = get_traded_symbols(store, date_str)
        non_traded_with_reasons = get_non_traded_symbols(store, date_str)

        store.close()

    except Exception as exc:
        print(f"ERROR: Database query failed: {exc}", file=sys.stderr)
        print("  Creating empty CSV with headers only...")
        # FIX-039: no crash on DB error (still write the file), but surface the failure (M-SC2).
        csv_path = generate_csv(date_str, [], [], output_dir)
        print(f"  CSV written to: {csv_path}")
        print("  WARNING: No data (database error)")
        return 1

    # Generate CSV
    csv_path = generate_csv(date_str, traded, non_traded_with_reasons, output_dir)

    # Round-trip validation: re-read and verify CSV integrity
    try:
        with open(csv_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)
            assert header == ['TRADED', 'NON_TRADED', 'REJECTION_REASON'], f"Header mismatch: {header}"
            row_count = sum(1 for _ in reader)
        print(f"  CSV validation: OK ({row_count} data rows)")
    except Exception as exc:
        print(f"  CSV validation FAILED: {exc}", file=sys.stderr)
        return 1

    # Summary
    print(f"  Traded symbols: {len(traded)}")
    print(f"  Non-traded symbols: {len(non_traded_with_reasons)}")
    print(f"  CSV written to: {csv_path}")

    if len(traded) == 0 and len(non_traded_with_reasons) == 0:
        print("  WARNING: No symbols found for this date. Check if trading occurred.")

    return 0


def _cron_main() -> int:
    """Cron entry: holiday-skip + heartbeat + per-job alert (TASK #3)."""
    from utils.cron_heartbeat import HeartbeatTimer, skip_if_non_trading_day

    if skip_if_non_trading_day("generate_screened_csv"):
        return 0
    timer = HeartbeatTimer("generate_screened_csv", alert=True)
    with timer:
        rc = main()
        rc = 0 if rc is None else rc
        if rc != 0:
            timer.status = "FAILED"
            timer.message = f"exit code {rc}"
    return rc


if __name__ == '__main__':
    sys.exit(_cron_main())
