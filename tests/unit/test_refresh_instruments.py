"""
tests/unit/test_refresh_instruments.py -- Trading System v2

Unit tests for scripts/refresh_instruments.py (RI1-RI15).

All tests operate on fixture data under tests/fixtures/reference_data/;
no Kite API calls are made.

Run: python -m pytest tests/unit/test_refresh_instruments.py -v
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root on path
_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

import scripts.refresh_instruments as ri

_FIXTURES = _ROOT / "tests" / "fixtures" / "reference_data"
_INDEX_DIR = _FIXTURES / "index_members"


# ─────────────────────────────────────────────────────────────────────────────
# RI1 / RI11: _load_security_master
# ─────────────────────────────────────────────────────────────────────────────

def test_load_security_master_loads_eq_symbols():
    """EQ-series symbols are loaded; SME series symbol is excluded (RI11)."""
    symbols = ri._load_security_master(_FIXTURES / "security_master_small.csv")
    assert "INFY" in symbols
    assert "HCLTECH" in symbols
    assert "AXISBANK" in symbols
    assert "HDFCBANK" in symbols
    assert "SMESYM" not in symbols


def test_load_security_master_count():
    """Fixture has 4 EQ rows and 1 SME row; only 4 returned."""
    symbols = ri._load_security_master(_FIXTURES / "security_master_small.csv")
    assert len(symbols) == 4


def test_load_security_master_file_not_found(tmp_path):
    """Missing security master causes sys.exit(1) (RI14)."""
    with pytest.raises(SystemExit) as exc_info:
        ri._load_security_master(tmp_path / "no_such_file.csv")
    assert exc_info.value.code == 1


# ─────────────────────────────────────────────────────────────────────────────
# RI2 / RI5: _load_sector_map
# ─────────────────────────────────────────────────────────────────────────────

def test_load_sector_map_assigns_it():
    """Symbols in nifty_it.csv get sector IT."""
    sector_map = ri._load_sector_map(_INDEX_DIR)
    assert sector_map.get("INFY") == "IT"
    assert sector_map.get("HCLTECH") == "IT"


def test_load_sector_map_priority_private_bank_over_bank():
    """AXISBANK is in both nifty_bank and nifty_private_bank; PRIVATE_BANK wins (RI5)."""
    sector_map = ri._load_sector_map(_INDEX_DIR)
    assert sector_map.get("AXISBANK") == "PRIVATE_BANK"


def test_load_sector_map_bank_without_override():
    """HDFCBANK is only in nifty_bank; gets BANK sector."""
    sector_map = ri._load_sector_map(_INDEX_DIR)
    assert sector_map.get("HDFCBANK") == "BANK"


def test_load_sector_map_missing_files_skipped(tmp_path):
    """Empty index dir returns empty map without error."""
    sector_map = ri._load_sector_map(tmp_path)
    assert sector_map == {}


# ─────────────────────────────────────────────────────────────────────────────
# RI3: _load_fno_set
# ─────────────────────────────────────────────────────────────────────────────

def test_load_fno_set_basic():
    """FNO symbols loaded from fixture fno_symbols.csv."""
    fno_set = ri._load_fno_set(_INDEX_DIR)
    assert "INFY" in fno_set
    assert "AXISBANK" in fno_set
    assert "HDFCBANK" not in fno_set


def test_load_fno_set_missing_file_returns_empty(tmp_path):
    """Missing fno_symbols.csv returns empty frozenset, no error (RI3)."""
    fno_set = ri._load_fno_set(tmp_path)
    assert fno_set == frozenset()


# ─────────────────────────────────────────────────────────────────────────────
# RI4: _build_kite_lookup
# ─────────────────────────────────────────────────────────────────────────────

def test_build_kite_lookup_filters_to_nse():
    """Only NSE/NSE-EQ segment instruments are kept."""
    kite_data = [
        {"tradingsymbol": "INFY", "segment": "NSE", "instrument_token": 408065, "lot_size": 1, "tick_size": 0.05},
        {"tradingsymbol": "INFY", "segment": "BSE", "instrument_token": 500209, "lot_size": 1, "tick_size": 0.05},
        {"tradingsymbol": "NIFTY", "segment": "NFO-IDX", "instrument_token": 256265, "lot_size": 50, "tick_size": 0.05},
    ]
    lookup = ri._build_kite_lookup(kite_data)
    assert "INFY" in lookup
    assert lookup["INFY"]["instrument_token"] == 408065  # NSE row, not BSE
    assert "NIFTY" not in lookup


# ─────────────────────────────────────────────────────────────────────────────
# RI1-RI5: _build_rows
# ─────────────────────────────────────────────────────────────────────────────

_KITE_LOOKUP = {
    "INFY":    {"tradingsymbol": "INFY",    "instrument_token": 408065,  "segment": "NSE", "lot_size": 1, "tick_size": 0.05},
    "HCLTECH": {"tradingsymbol": "HCLTECH", "instrument_token": 1850625, "segment": "NSE", "lot_size": 1, "tick_size": 0.05},
    "AXISBANK":{"tradingsymbol": "AXISBANK","instrument_token": 1510401, "segment": "NSE", "lot_size": 1, "tick_size": 0.05},
    "HDFCBANK":{"tradingsymbol": "HDFCBANK","instrument_token": 341249,  "segment": "NSE", "lot_size": 1, "tick_size": 0.05},
}


def test_build_rows_token_from_kite():
    """instrument_token comes from Kite lookup (RI4)."""
    rows, missing = ri._build_rows(["INFY"], {}, frozenset(), _KITE_LOOKUP)
    assert len(rows) == 1
    assert rows[0]["instrument_token"] == 408065
    assert missing == []


def test_build_rows_missing_from_kite_gets_defaults():
    """Symbol absent from Kite gets token=0, lot=1, tick=0.05 (RI12)."""
    rows, missing = ri._build_rows(["UNKNOWN"], {}, frozenset(), {})
    assert rows[0]["instrument_token"] == 0
    assert rows[0]["lot_size"] == 1
    assert rows[0]["tick_size"] == 0.05
    assert "UNKNOWN" in missing


def test_build_rows_is_fno_flag():
    """is_fno='true' for FNO symbols; 'false' otherwise (RI3)."""
    fno_set = frozenset(["INFY"])
    rows, _ = ri._build_rows(["INFY", "HCLTECH"], {}, fno_set, _KITE_LOOKUP)
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["INFY"]["is_fno"] == "true"
    assert by_sym["HCLTECH"]["is_fno"] == "false"


def test_build_rows_sector_assigned():
    """Sector from sector_map is written into row (RI2)."""
    sector_map = {"INFY": "IT"}
    rows, _ = ri._build_rows(["INFY", "HCLTECH"], sector_map, frozenset(), _KITE_LOOKUP)
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["INFY"]["sector"] == "IT"
    assert by_sym["HCLTECH"]["sector"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# RI6: _write_csv
# ─────────────────────────────────────────────────────────────────────────────

def test_write_csv_creates_file_with_correct_columns(tmp_path):
    """CSV written atomically; header matches _CSV_COLUMNS (RI6, RI13)."""
    rows = [{"symbol": "INFY", "instrument_token": 408065, "exchange": "NSE",
             "lot_size": 1, "tick_size": 0.05, "is_fno": "true", "sector": "IT"}]
    out = tmp_path / "instruments.csv"
    ri._write_csv(out, rows)
    assert out.exists()
    with open(out, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == ri._CSV_COLUMNS
        data = list(reader)
    assert len(data) == 1
    assert data[0]["symbol"] == "INFY"
    assert data[0]["sector"] == "IT"


# ─────────────────────────────────────────────────────────────────────────────
# RI7: sanity check
# ─────────────────────────────────────────────────────────────────────────────

def test_main_sanity_check_fails_on_too_few_rows(tmp_path):
    """main() returns 1 when built rows < 1000 (RI7)."""
    small_master = tmp_path / "sm.csv"
    small_master.write_text(
        "symbol,name_of_company, series\nINFY,Infosys,eq\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / "idx"
    index_dir.mkdir()

    kite_data = [{"tradingsymbol": "INFY", "segment": "NSE", "instrument_token": 1, "lot_size": 1, "tick_size": 0.05}]

    with patch.object(ri, "_fetch_kite_instruments", return_value=kite_data), \
         patch.object(ri, "_resolve_credentials", return_value=("key", "token")):
        result = ri.main([
            "--security-master", str(small_master),
            "--index-dir", str(index_dir),
            "--csv", str(tmp_path / "out.csv"),
        ])
    assert result == 1


# ─────────────────────────────────────────────────────────────────────────────
# RI9: dry-run
# ─────────────────────────────────────────────────────────────────────────────

def test_main_dry_run_does_not_write(tmp_path):
    """--dry-run returns 0 and does not create output file (RI9)."""
    # Build a master with >1000 symbols
    lines = ["symbol,name_of_company, series"]
    for i in range(1100):
        lines.append(f"SYM{i:04d},Company {i},eq")
    master = tmp_path / "big.csv"
    master.write_text("\n".join(lines) + "\n", encoding="utf-8")
    index_dir = tmp_path / "idx"
    index_dir.mkdir()
    out_csv = tmp_path / "instruments.csv"

    with patch.object(ri, "_fetch_kite_instruments", return_value=[]), \
         patch.object(ri, "_resolve_credentials", return_value=("k", "t")):
        result = ri.main([
            "--security-master", str(master),
            "--index-dir", str(index_dir),
            "--csv", str(out_csv),
            "--dry-run",
        ])
    assert result == 0
    assert not out_csv.exists()
