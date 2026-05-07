#!/usr/bin/env python3
"""
Find potential Zerodha symbol matches for failed Chartink symbols.

Usage:
    python scripts/find_symbol_aliases.py < failed_symbols.txt
"""
import csv
import sys
from difflib import SequenceMatcher
from pathlib import Path


def fuzzy_match(s1: str, s2: str) -> float:
    """Return similarity ratio between two strings (0.0-1.0)."""
    return SequenceMatcher(None, s1.upper(), s2.upper()).ratio()


def find_candidates(chartink_symbol: str, zerodha_symbols: list[tuple[str, str]]) -> list[tuple[str, float]]:
    """
    Find potential matches for chartink_symbol in zerodha_symbols.

    Returns list of (zerodha_symbol, similarity_score) sorted by score descending.
    """
    candidates = []

    # Strategy 1: Exact match (should not happen if symbol failed)
    for z_sym, token in zerodha_symbols:
        if z_sym == chartink_symbol:
            candidates.append((z_sym, 1.0))

    # Strategy 2: Prefix match (Chartink abbreviates)
    for z_sym, token in zerodha_symbols:
        if z_sym.startswith(chartink_symbol):
            # TVSSCS -> TVSSRICHAK (starts with TVSSCS, then adds suffix)
            candidates.append((z_sym, 0.95))

    # Strategy 3: Chartink symbol is contained in Zerodha symbol
    for z_sym, token in zerodha_symbols:
        if chartink_symbol in z_sym:
            candidates.append((z_sym, 0.9))

    # Strategy 4: Fuzzy string match (> 0.8 similarity)
    for z_sym, token in zerodha_symbols:
        ratio = fuzzy_match(chartink_symbol, z_sym)
        if ratio > 0.8:
            candidates.append((z_sym, ratio))

    # Deduplicate and sort by score
    seen = set()
    unique = []
    for sym, score in sorted(candidates, key=lambda x: -x[1]):
        if sym not in seen:
            seen.add(sym)
            unique.append((sym, score))

    return unique[:5]  # Top 5 candidates


def main():
    instruments_csv = Path("config/instruments.csv")
    if not instruments_csv.exists():
        print(f"ERROR: {instruments_csv} not found", file=sys.stderr)
        sys.exit(1)

    # Load Zerodha symbols
    zerodha_symbols = []
    with open(instruments_csv) as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                symbol, token = row[0], row[1]
                if token != "0":  # Skip invalid tokens
                    zerodha_symbols.append((symbol, token))

    print(f"# Loaded {len(zerodha_symbols)} valid Zerodha symbols", file=sys.stderr)
    print(f"# Reading failed symbols from stdin...", file=sys.stderr)
    print()

    # Process failed symbols from stdin
    failed_symbols = [line.strip() for line in sys.stdin if line.strip()]

    print("# Symbol alias candidates (YAML format)")
    print("# Review and add to config/symbol_aliases.yaml")
    print()

    no_match_count = 0
    for chartink_sym in sorted(failed_symbols):
        candidates = find_candidates(chartink_sym, zerodha_symbols)
        if candidates:
            top = candidates[0]
            if top[1] >= 0.9:
                # High confidence match
                print(f"{chartink_sym}: {top[0]}  # confidence={top[1]:.2f}")
            else:
                # Lower confidence - show alternatives
                print(f"# {chartink_sym}: uncertain match")
                for cand, score in candidates:
                    print(f"#   {cand} (score={score:.2f})")
                print()
        else:
            print(f"# {chartink_sym}: NO MATCH FOUND")
            no_match_count += 1

    print(f"\n# Summary: {len(failed_symbols)} failed, {no_match_count} with no matches", file=sys.stderr)


if __name__ == "__main__":
    main()
