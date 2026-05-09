#!/usr/bin/env python3
"""
generate_screened_stocks_csv.py -- Trading System v2

PURPOSE:
    Generate screened_stocks_YYYY-MM-DD.csv for post-trade analysis.

    Parses logs/system_YYYY-MM-DD.log to extract:
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
"""
from __future__ import annotations

import csv
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Set, Tuple


# ---------------------------------------------------------------------------
# Rejection reason mapping (expanded format)
# ---------------------------------------------------------------------------

def expand_rejection_reason(check: str, reason: str = "") -> str:
    """
    Map rejection check codes to expanded readable reasons.

    Args:
        check: Rejection check code (e.g., "SCORE_48", "DUPLICATE_SYMBOL")
        reason: Optional detailed reason from log message

    Returns:
        Expanded human-readable reason string
    """
    # Score-based rejections (regex pattern: SCORE_XX or REJECTED_SCORE_XX)
    score_match = re.match(r"(?:REJECTED_)?SCORE_(\d+)", check)
    if score_match:
        return f"Score too low ({score_match.group(1)}/100)"

    # Exact matches
    EXACT_MAP = {
        # Duplicate symbol
        "DUPLICATE_SYMBOL": "Duplicate symbol (active position/order exists)",
        "REJECTED_DUPLICATE_SYMBOL": "Duplicate symbol (active position/order exists)",

        # Quote unavailable
        "SKIPPED_QUOTE_UNAVAILABLE": "Quote unavailable (API failure)",
        "QUOTE_UNAVAILABLE": "Quote unavailable (API failure)",

        # Entry window
        "OUTSIDE_ENTRY_WINDOW": "Outside entry window (after HH:MM)",
        "REJECTED_OUTSIDE_ENTRY_WINDOW": "Outside entry window (after HH:MM)",

        # Capital constraints
        "CAPITAL_EXCEEDED": "Insufficient capital",
        "INSUFFICIENT_CAPITAL": "Insufficient capital",
        "REJECTED_CAPITAL_EXCEEDED": "Insufficient capital",

        # Position limits
        "MAX_POSITIONS_EXCEEDED": "Max positions limit reached",
        "MAX_POSITIONS": "Max positions limit reached",
        "REJECTED_MAX_POSITIONS": "Max positions limit reached",

        # Kill switch
        "KILL_SWITCH": "Kill switch active",
        "REJECTED_KILL_SWITCH": "Kill switch active",

        # Screener executor/scorer errors
        "SKIPPED_EXECUTOR_ERROR": "Screener executor error",
        "SKIPPED_SCORER_ERROR": "Screener scorer error",

        # Invalid derived price
        "INVALID_DERIVED_PRICE": "Invalid derived price (strategy config issue)",
        "REJECTED_INVALID_DERIVED_PRICE": "Invalid derived price (strategy config issue)",

        # Target distance too small
        "TGT_DISTANCE_TOO_SMALL": "Target distance too small (degenerate config)",
        "REJECTED_TGT_DISTANCE_TOO_SMALL": "Target distance too small (degenerate config)",

        # Unknown strategy
        "UNKNOWN_STRATEGY": "Unknown strategy",
        "REJECTED_UNKNOWN_STRATEGY": "Unknown strategy",

        # Other
        "PLACEMENT_FAILED": "Order placement failed",
    }

    if check in EXACT_MAP:
        return EXACT_MAP[check]

    # Fallback: use the check itself + reason if available
    if reason:
        # Extract concise reason (first sentence only)
        concise_reason = reason.split('.')[0].split(';')[0]
        clean_check = check.replace('REJECTED_', '').replace('_', ' ').title()
        return f"{clean_check} ({concise_reason})"

    return check.replace('REJECTED_', '').replace('_', ' ').title()


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

def parse_log_file(log_path: Path) -> Tuple[List[str], List[Tuple[str, str]]]:
    """
    Parse system log file to extract TRADED and NON_TRADED symbols.

    Args:
        log_path: Path to logs/system_YYYY-MM-DD.log

    Returns:
        (traded_symbols, non_traded_with_reasons)
        traded_symbols: List of symbols that got ORDER PLACED
        non_traded_with_reasons: List of (symbol, reason) tuples for rejected/skipped
    """
    traded: Set[str] = set()
    non_traded: List[Tuple[str, str]] = []

    # Regex patterns for log parsing
    order_placed_pattern = re.compile(r'ORDER PLACED — ([A-Z0-9]+)')

    # Unified rejection pattern: captures all rejection types with (SYMBOL) format
    # Matches:
    #   - Pipeline: "Signal ... (SYMBOL) rejected at CHECK: reason"
    #   - Gate: "Gate signal ... (SYMBOL) rejected at CHECK: reason"
    #   - Screener rejected: "Signal ... (SYMBOL) screener rejected: STATUS"
    #   - Screener SKIPPED: "Signal ... (SYMBOL) screener SKIPPED: STATUS"
    rejection_pattern = re.compile(
        r'(?:Gate signal|Signal) [^\(]+ \(([A-Z0-9]+)\) (?:'
        r'rejected at ([A-Z_0-9]+): (.+)|'           # Pipeline/gate rejection
        r'screener rejected: ([A-Z_0-9]+)|'          # Screener rejection
        r'screener SKIPPED: ([A-Z_0-9]+)'            # Screener skipped
        r')'
    )

    if not log_path.exists():
        print(f"ERROR: Log file not found: {log_path}", file=sys.stderr)
        return [], []

    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            # Check for ORDER PLACED
            match = order_placed_pattern.search(line)
            if match:
                symbol = match.group(1)
                traded.add(symbol)
                continue

            # Check for rejections (pipeline, gate, screener)
            match = rejection_pattern.search(line)
            if match:
                symbol = match.group(1)

                # Determine rejection type and extract reason
                if match.group(2):  # Pipeline/gate rejection (rejected at CHECK: reason)
                    check = match.group(2)
                    reason_detail = match.group(3)
                    expanded_reason = expand_rejection_reason(check, reason_detail)
                elif match.group(4):  # Screener rejection
                    check = match.group(4)
                    expanded_reason = expand_rejection_reason(check, "")
                elif match.group(5):  # Screener SKIPPED
                    check = match.group(5)
                    expanded_reason = expand_rejection_reason(check, "")
                else:
                    continue  # No valid match group

                # Skip if already traded (shouldn't happen, but safety)
                if symbol in traded:
                    continue

                non_traded.append((symbol, expanded_reason))

    return sorted(traded), non_traded


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

    # Pad lists to same length
    max_len = max(len(traded), len(non_traded), 1)  # at least 1 for header

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
    log_path = project_root / "logs" / f"system_{date_str}.log"
    output_dir = project_root / "reports" / "daily_review"

    print(f"Generating screened stocks CSV for {date_str}...")
    print(f"  Log file: {log_path}")

    # Parse log
    traded, non_traded_with_reasons = parse_log_file(log_path)

    # Generate CSV
    csv_path = generate_csv(date_str, traded, non_traded_with_reasons, output_dir)

    # Summary
    print(f"  Traded symbols: {len(traded)}")
    print(f"  Non-traded symbols: {len(non_traded_with_reasons)}")
    print(f"  CSV written to: {csv_path}")

    if len(traded) == 0 and len(non_traded_with_reasons) == 0:
        print("  WARNING: No symbols found in log. Check log file exists and has data.")

    return 0


if __name__ == '__main__':
    sys.exit(main())
