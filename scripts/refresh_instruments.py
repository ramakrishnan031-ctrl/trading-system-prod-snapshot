"""
scripts/refresh_instruments.py -- Trading System v2

Purpose:
    Build instruments.csv from three authoritative sources:
      1. NSE security master CSV  -- symbol universe
      2. NSE sector index CSVs    -- sector classification
      3. Kite Connect API         -- instrument tokens, lot sizes, tick sizes

    Run on first setup and whenever NSE adds/removes symbols.

Locked Design Decisions:
    RI1  -- Security master defines the symbol universe (~2800 EQ-series symbols).
    RI2  -- Sector from config/reference_data/index_members/ (13 sector files).
    RI3  -- is_fno from fno_symbols.csv; security master fno_stock column ignored.
    RI4  -- Kite API provides instrument_token, lot_size, tick_size per symbol.
    RI5  -- Sector priority: PSU_BANK / PRIVATE_BANK override BANK on conflict.
    RI6  -- Atomic write: write .csv.tmp then os.replace() to final path.
    RI7  -- Sanity check: abort (exit 1) if output row count < 1000.
    RI8  -- --account: load api_key_env from accounts.csv + access_token from
             data_store/session/zerodha_token.json.  Fallback: env vars.
    RI9  -- --dry-run: display stats without writing.
    RI10 -- --verbose: per-symbol detail output.
    RI11 -- Only EQ-series rows from security master (series='eq'|'be'|'n'|'bt').
    RI12 -- Symbols not matched in Kite: token=0, lot_size=1, tick_size=0.05.
    RI13 -- --csv: configurable output path (default config/instruments.csv).
    RI14 -- Exit codes: 0=success, 1=file/config error, 2=API/auth error.
    RI15 -- All pure functions (_load_*, _build_*) testable without Kite/network.

Usage:
    python scripts/refresh_instruments.py [--account LFL836] [--dry-run] [--verbose]
    ZERODHA_API_KEY=<key> ZERODHA_ACCESS_TOKEN=<token> python scripts/refresh_instruments.py
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_DEFAULT_CSV = _ROOT / "config" / "instruments.csv"
_DEFAULT_SECURITY_MASTER = _ROOT / "config" / "reference_data" / "security_master_file.csv"
_DEFAULT_INDEX_DIR = _ROOT / "config" / "reference_data" / "index_members"

_CSV_COLUMNS = [
    "symbol", "instrument_token", "exchange",
    "lot_size", "tick_size", "is_fno", "sector",
]

# RI11: accepted equity series (lowercase)
_EQ_SERIES = frozenset(["eq", "be", "n", "bt", "il"])

# RI2 + RI5: sector files in ascending priority; later entry wins on conflict.
# PRIVATE_BANK and PSU_BANK are last so they override BANK.
_SECTOR_INDEX_PRIORITY: list[tuple[str, str]] = [
    ("nifty_bank.csv",         "BANK"),
    ("nifty_fin_services.csv", "FIN_SERVICES"),
    ("nifty_auto.csv",         "AUTO"),
    ("nifty_fmcg.csv",         "FMCG"),
    ("nifty_healthcare.csv",   "HEALTHCARE"),
    ("nifty_it.csv",           "IT"),
    ("nifty_metal.csv",        "METAL"),
    ("nifty_oil_gas.csv",      "OIL_GAS"),
    ("nifty_pharma.csv",       "PHARMA"),
    ("nifty_realty.csv",       "REALTY"),
    ("nifty_private_bank.csv", "PRIVATE_BANK"),
    ("nifty_psu_bank.csv",     "PSU_BANK"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Pure data functions (RI15: testable without Kite)
# ─────────────────────────────────────────────────────────────────────────────

def _load_security_master(path: Path) -> list[str]:
    """Load EQ-series symbols from NSE security master CSV (RI1, RI11)."""
    if not path.exists():
        print(f"ERROR: security master not found: {path}", file=sys.stderr)
        sys.exit(1)
    symbols: list[str] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sym = row.get("symbol", "").strip()
            # Header has ' series' with leading space due to CSV formatting
            series = row.get(" series", row.get("series", "")).strip().lower()
            if not sym:
                continue
            if series and series not in _EQ_SERIES:
                continue
            symbols.append(sym)
    return symbols


def _load_sector_map(index_dir: Path) -> dict[str, str]:
    """
    Build symbol -> sector_name from sector index CSVs (RI2, RI5).

    Files loaded in _SECTOR_INDEX_PRIORITY order; later files overwrite earlier
    so PSU_BANK/PRIVATE_BANK beat BANK on the same symbol.
    """
    sector_map: dict[str, str] = {}
    for filename, sector_name in _SECTOR_INDEX_PRIORITY:
        fpath = index_dir / filename
        if not fpath.exists():
            continue
        with open(fpath, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                sym = row.get("symbol", "").strip()
                if sym:
                    sector_map[sym] = sector_name
    return sector_map


def _load_fno_set(index_dir: Path) -> frozenset[str]:
    """Load FNO symbols from fno_symbols.csv (RI3)."""
    fno_path = index_dir / "fno_symbols.csv"
    if not fno_path.exists():
        return frozenset()
    symbols: set[str] = set()
    with open(fno_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sym = row.get("symbol", "").strip()
            if sym:
                symbols.add(sym)
    return frozenset(symbols)


def _build_kite_lookup(kite_instruments: list[dict]) -> dict[str, dict]:
    """Index Kite instrument list by tradingsymbol for NSE equity segment."""
    lookup: dict[str, dict] = {}
    for inst in kite_instruments:
        sym = inst.get("tradingsymbol", "")
        seg = inst.get("segment", "")
        if seg in ("NSE", "NSE-EQ"):
            lookup[sym] = inst
    return lookup


def _build_rows(
    symbols: list[str],
    sector_map: dict[str, str],
    fno_set: frozenset[str],
    kite_lookup: dict[str, dict],
    verbose: bool = False,
) -> tuple[list[dict], list[str]]:
    """
    Merge sources into instruments.csv rows (RI1-RI5, RI12).

    Returns (rows, missing_from_kite).  Symbols absent from Kite get
    token=0, lot_size=1, tick_size=0.05 (RI12).
    """
    rows: list[dict] = []
    missing: list[str] = []

    for sym in symbols:
        kite = kite_lookup.get(sym)
        if kite is None:
            missing.append(sym)
            token = 0
            lot_size = 1
            tick_size = 0.05
        else:
            token = int(kite.get("instrument_token", 0))
            lot_size = int(kite.get("lot_size", 1))
            tick_size = float(kite.get("tick_size", 0.05))

        is_fno = sym in fno_set
        sector = sector_map.get(sym, "")

        if verbose:
            print(f"  {sym}: token={token} lot={lot_size} tick={tick_size} "
                  f"fno={is_fno} sector={sector!r}")

        rows.append({
            "symbol":           sym,
            "instrument_token": token,
            "exchange":         "NSE",
            "lot_size":         lot_size,
            "tick_size":        tick_size,
            "is_fno":           "true" if is_fno else "false",
            "sector":           sector,
        })

    return rows, missing


def _write_csv(csv_path: Path, rows: list[dict]) -> None:
    """Atomically write rows to csv_path (RI6)."""
    tmp_path = csv_path.with_suffix(".csv.tmp")
    try:
        with open(tmp_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        tmp_path.replace(csv_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Kite + credential helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_kite_instruments(api_key: str, access_token: str, exchange: str = "NSE") -> list[dict]:
    """Download instruments from Kite Connect API (RI4)."""
    try:
        from kiteconnect import KiteConnect  # type: ignore[import]
    except ImportError:
        print(
            "ERROR: kiteconnect package not installed. Run: pip install kiteconnect",
            file=sys.stderr,
        )
        sys.exit(2)
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite.instruments(exchange)


def _resolve_credentials(args: argparse.Namespace) -> tuple[str, str]:
    """
    Return (api_key, access_token) (RI8).

    With --account: reads api_key_env from accounts.csv + access_token from
    data_store/session/zerodha_token.json.
    Without --account: falls back to ZERODHA_API_KEY / ZERODHA_ACCESS_TOKEN env.
    """
    if getattr(args, "account", None):
        from core.account_registry import AccountRegistry
        registry = AccountRegistry.load(_ROOT / "config" / "accounts.csv")
        matches = [a for a in registry.get_enabled_accounts() if a.account_id == args.account]
        if not matches:
            print(f"ERROR: account {args.account!r} not found in accounts.csv", file=sys.stderr)
            sys.exit(1)
        acct = matches[0]
        api_key = os.environ.get(acct.api_key_env, "")
        if not api_key:
            print(f"ERROR: env var {acct.api_key_env!r} not set", file=sys.stderr)
            sys.exit(1)
        token_path = _ROOT / "data_store" / "session" / "zerodha_token.json"
        if not token_path.exists():
            print(f"ERROR: token file not found: {token_path}", file=sys.stderr)
            sys.exit(2)
        token_data = json.loads(token_path.read_text(encoding="utf-8"))
        access_token = token_data.get("access_token", "")
        if not access_token:
            print("ERROR: access_token missing from token file", file=sys.stderr)
            sys.exit(2)
        return api_key, access_token

    api_key = os.environ.get("ZERODHA_API_KEY", "")
    access_token = os.environ.get("ZERODHA_ACCESS_TOKEN", "")
    if not api_key or not access_token:
        print(
            "ERROR: ZERODHA_API_KEY and ZERODHA_ACCESS_TOKEN must be set in env.",
            file=sys.stderr,
        )
        sys.exit(1)
    return api_key, access_token


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build instruments.csv from NSE security master + sector indices + Kite API"
    )
    p.add_argument(
        "--csv", type=Path, default=_DEFAULT_CSV,
        help="Output CSV path (default: config/instruments.csv)",
    )
    p.add_argument(
        "--security-master", type=Path, default=_DEFAULT_SECURITY_MASTER,
        help="NSE security master CSV path",
    )
    p.add_argument(
        "--index-dir", type=Path, default=_DEFAULT_INDEX_DIR,
        help="Sector index CSVs directory",
    )
    p.add_argument(
        "--account", type=str, default=None,
        help="Account ID from accounts.csv (resolves credentials from env + token file)",
    )
    p.add_argument("--dry-run", action="store_true", help="Print output without writing")
    p.add_argument("--verbose", action="store_true", help="Print per-symbol detail")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    print(f"Loading security master: {args.security_master}")
    symbols = _load_security_master(args.security_master)
    print(f"  {len(symbols)} equity symbols")

    print(f"Loading sector maps from: {args.index_dir}")
    sector_map = _load_sector_map(args.index_dir)
    fno_set = _load_fno_set(args.index_dir)
    print(f"  {len(sector_map)} symbols with sector  |  {len(fno_set)} FNO symbols")

    print("Fetching instruments from Kite Connect API ...")
    api_key, access_token = _resolve_credentials(args)
    kite_instruments = _fetch_kite_instruments(api_key, access_token)
    kite_lookup = _build_kite_lookup(kite_instruments)
    print(f"  {len(kite_lookup)} NSE equity instruments from Kite")

    rows, missing = _build_rows(symbols, sector_map, fno_set, kite_lookup, args.verbose)

    if missing:
        sample = missing[:10]
        ellipsis = "..." if len(missing) > 10 else ""
        print(f"WARNING: {len(missing)} symbols not in Kite data (token=0): {sample}{ellipsis}")

    # RI7: sanity check
    if len(rows) < 1000:
        print(
            f"ERROR: sanity check failed — {len(rows)} rows built, expected >1000.",
            file=sys.stderr,
        )
        return 1

    print(f"Built {len(rows)} rows.")

    if args.dry_run:
        print("--dry-run: no changes written.")
        return 0

    _write_csv(args.csv, rows)
    print(f"Wrote {len(rows)} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
