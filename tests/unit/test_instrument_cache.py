"""
tests/unit/test_instrument_cache.py

Validates core/instrument_cache.py against IC1-IC15 locked decisions.

Run: python -m pytest tests/unit/test_instrument_cache.py -v
Or:  python tests/unit/test_instrument_cache.py  (standalone)
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.exceptions import ConfigMissingError, ConfigSchemaError, InstrumentNotFoundError
from core.instrument_cache import InstrumentCache, InstrumentRow


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_VALID_CSV = """\
symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector
RELIANCE,738561,NSE,1,0.05,false,ENERGY
TCS,2953217,NSE,1,0.05,false,IT
INFY,408065,NSE,1,0.05,false,IT
HDFCBANK,341249,NSE,1,0.05,false,FINANCIALS
SBIN,779521,NSE,1,0.05,false,FINANCIALS
"""

_FNO_CSV = """\
symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector
NIFTY_FUT,256265,NSE,50,0.05,true,INDEX
"""


def _write_csv(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "instruments.csv"
    p.write_text(content, encoding="utf-8")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# IC12: load() tests
# ─────────────────────────────────────────────────────────────────────────────

def test_load_valid_csv(tmp_path: Path) -> None:
    """load() with valid CSV returns populated cache (IC12)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    assert cache.count() == 5
    print("  OK load() valid CSV -> 5 rows")


def test_load_missing_file_raises(tmp_path: Path) -> None:
    """load() on absent file raises ConfigMissingError (IC12)."""
    import pytest
    with pytest.raises(ConfigMissingError):
        InstrumentCache.load(tmp_path / "no_such_file.csv")
    print("  OK load() missing file -> ConfigMissingError")


def test_load_missing_column_raises(tmp_path: Path) -> None:
    """Missing required column -> ConfigSchemaError (IC12)."""
    import pytest
    bad = "symbol,instrument_token\nRELIANCE,738561\n"
    p = _write_csv(tmp_path, bad)
    with pytest.raises(ConfigSchemaError):
        InstrumentCache.load(p)
    print("  OK load() missing columns -> ConfigSchemaError")


def test_load_bad_int_raises(tmp_path: Path) -> None:
    """Non-integer instrument_token -> ConfigSchemaError (IC12)."""
    import pytest
    bad = "symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector\n"
    bad += "RELIANCE,NOT_AN_INT,NSE,1,0.05,false,ENERGY\n"
    p = _write_csv(tmp_path, bad)
    with pytest.raises(ConfigSchemaError):
        InstrumentCache.load(p)
    print("  OK load() bad int token -> ConfigSchemaError")


def test_load_lot_size_zero_raises(tmp_path: Path) -> None:
    """lot_size=0 is invalid -> ConfigSchemaError (IC12)."""
    import pytest
    bad = "symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector\n"
    bad += "RELIANCE,738561,NSE,0,0.05,false,ENERGY\n"
    p = _write_csv(tmp_path, bad)
    with pytest.raises(ConfigSchemaError):
        InstrumentCache.load(p)
    print("  OK load() lot_size=0 -> ConfigSchemaError")


def test_load_tick_size_zero_raises(tmp_path: Path) -> None:
    """tick_size=0 is invalid -> ConfigSchemaError (IC12)."""
    import pytest
    bad = "symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector\n"
    bad += "RELIANCE,738561,NSE,1,0.0,false,ENERGY\n"
    p = _write_csv(tmp_path, bad)
    with pytest.raises(ConfigSchemaError):
        InstrumentCache.load(p)
    print("  OK load() tick_size=0 -> ConfigSchemaError")


def test_load_empty_symbol_raises(tmp_path: Path) -> None:
    """Empty symbol -> ConfigSchemaError (IC12)."""
    import pytest
    bad = "symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector\n"
    bad += ",738561,NSE,1,0.05,false,ENERGY\n"
    p = _write_csv(tmp_path, bad)
    with pytest.raises(ConfigSchemaError):
        InstrumentCache.load(p)
    print("  OK load() empty symbol -> ConfigSchemaError")


# ─────────────────────────────────────────────────────────────────────────────
# IC3: InstrumentRow type correctness
# ─────────────────────────────────────────────────────────────────────────────

def test_row_types_correct(tmp_path: Path) -> None:
    """InstrumentRow fields have correct Python types (IC3)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    row = cache.get_by_symbol("RELIANCE")
    assert isinstance(row.symbol, str)
    assert isinstance(row.instrument_token, int)
    assert isinstance(row.exchange, str)
    assert isinstance(row.lot_size, int)
    assert isinstance(row.tick_size, float)
    assert isinstance(row.is_fno, bool)
    assert isinstance(row.sector, str)
    print("  OK InstrumentRow field types correct (IC3)")


def test_is_fno_parsed_bool(tmp_path: Path) -> None:
    """is_fno='true' parses to True; 'false' to False (IC3)."""
    p = _write_csv(tmp_path, _VALID_CSV + _FNO_CSV.split("\n", 1)[1])
    cache = InstrumentCache.load(p)
    assert cache.get_by_symbol("RELIANCE").is_fno is False
    assert cache.get_by_symbol("NIFTY_FUT").is_fno is True
    print("  OK is_fno bool parsing correct (IC3)")


# ─────────────────────────────────────────────────────────────────────────────
# IC4: has()
# ─────────────────────────────────────────────────────────────────────────────

def test_has_known_symbol(tmp_path: Path) -> None:
    """has() returns True for loaded symbols (IC4)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    assert cache.has("RELIANCE") is True
    assert cache.has("TCS") is True
    print("  OK has() True for loaded symbols (IC4)")


def test_has_unknown_symbol(tmp_path: Path) -> None:
    """has() returns False for unknown symbols; never raises (IC4)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    assert cache.has("WIPRO") is False
    print("  OK has() False for unknown symbol (IC4)")


# ─────────────────────────────────────────────────────────────────────────────
# IC5: get_by_symbol()
# ─────────────────────────────────────────────────────────────────────────────

def test_get_by_symbol_known(tmp_path: Path) -> None:
    """get_by_symbol returns correct row (IC5)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    row = cache.get_by_symbol("INFY")
    assert row.instrument_token == 408065
    assert row.sector == "IT"
    print("  OK get_by_symbol known symbol (IC5)")


def test_get_by_symbol_unknown_raises(tmp_path: Path) -> None:
    """get_by_symbol unknown raises InstrumentNotFoundError (IC5)."""
    import pytest
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    with pytest.raises(InstrumentNotFoundError):
        cache.get_by_symbol("UNKNOWN_XYZ")
    print("  OK get_by_symbol unknown -> InstrumentNotFoundError (IC5)")


# ─────────────────────────────────────────────────────────────────────────────
# IC6: get_by_token()
# ─────────────────────────────────────────────────────────────────────────────

def test_get_by_token_known(tmp_path: Path) -> None:
    """get_by_token returns correct row (IC6)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    row = cache.get_by_token(738561)
    assert row.symbol == "RELIANCE"
    print("  OK get_by_token known token (IC6)")


def test_get_by_token_unknown_raises(tmp_path: Path) -> None:
    """get_by_token unknown raises InstrumentNotFoundError (IC6)."""
    import pytest
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    with pytest.raises(InstrumentNotFoundError):
        cache.get_by_token(99999999)
    print("  OK get_by_token unknown -> InstrumentNotFoundError (IC6)")


# ─────────────────────────────────────────────────────────────────────────────
# IC7: all_rows()
# ─────────────────────────────────────────────────────────────────────────────

def test_all_rows_load_order(tmp_path: Path) -> None:
    """all_rows() returns rows in CSV load order (IC7)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    rows = cache.all_rows()
    assert [r.symbol for r in rows] == ["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"]
    print("  OK all_rows() preserves load order (IC7)")


def test_all_rows_returns_copy(tmp_path: Path) -> None:
    """all_rows() returns a copy; mutating it doesn't affect cache (IC7)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    rows = cache.all_rows()
    rows.clear()
    assert cache.count() == 5  # original unaffected
    print("  OK all_rows() returns copy (IC7)")


# ─────────────────────────────────────────────────────────────────────────────
# IC8: sector()
# ─────────────────────────────────────────────────────────────────────────────

def test_sector_known_symbol(tmp_path: Path) -> None:
    """sector() returns correct sector for known symbol (IC8)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    assert cache.sector("RELIANCE") == "ENERGY"
    assert cache.sector("TCS") == "IT"
    print("  OK sector() returns correct sector (IC8)")


def test_sector_unknown_returns_unknown(tmp_path: Path) -> None:
    """sector() returns 'UNKNOWN' for unknown symbol, never raises (IC8)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    result = cache.sector("WIPRO_NOT_IN_CSV")
    assert result == "UNKNOWN"
    print("  OK sector() unknown symbol -> 'UNKNOWN' without raising (IC8)")


def test_sector_blank_returns_unknown(tmp_path: Path) -> None:
    """sector() returns 'UNKNOWN' when CSV sector column is blank (IC8)."""
    csv_blank = (
        "symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector\n"
        "WIPRO,969473,NSE,1,0.05,false,\n"
    )
    p = _write_csv(tmp_path, csv_blank)
    cache = InstrumentCache.load(p)
    assert cache.sector("WIPRO") == "UNKNOWN"
    print("  OK sector() blank column -> 'UNKNOWN' (IC8)")


# ─────────────────────────────────────────────────────────────────────────────
# IC9: lot_size()
# ─────────────────────────────────────────────────────────────────────────────

def test_lot_size_known(tmp_path: Path) -> None:
    """lot_size() returns correct value (IC9)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    assert cache.lot_size("RELIANCE") == 1
    print("  OK lot_size() known symbol (IC9)")


def test_lot_size_fno(tmp_path: Path) -> None:
    """lot_size() for F&O returns lot (IC9)."""
    csv_fno = (
        "symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector\n"
        "NIFTY_FUT,256265,NSE,50,0.05,true,INDEX\n"
    )
    p = _write_csv(tmp_path, csv_fno)
    cache = InstrumentCache.load(p)
    assert cache.lot_size("NIFTY_FUT") == 50
    print("  OK lot_size() F&O returns 50 (IC9)")


def test_lot_size_unknown_raises(tmp_path: Path) -> None:
    """lot_size() unknown symbol raises InstrumentNotFoundError (IC9)."""
    import pytest
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    with pytest.raises(InstrumentNotFoundError):
        cache.lot_size("NO_SUCH")
    print("  OK lot_size() unknown -> InstrumentNotFoundError (IC9)")


# ─────────────────────────────────────────────────────────────────────────────
# IC10: tick_size()
# ─────────────────────────────────────────────────────────────────────────────

def test_tick_size_known(tmp_path: Path) -> None:
    """tick_size() returns correct value (IC10)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    assert abs(cache.tick_size("RELIANCE") - 0.05) < 1e-9
    print("  OK tick_size() known symbol (IC10)")


def test_tick_size_unknown_raises(tmp_path: Path) -> None:
    """tick_size() unknown raises InstrumentNotFoundError (IC10)."""
    import pytest
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    with pytest.raises(InstrumentNotFoundError):
        cache.tick_size("NO_SUCH")
    print("  OK tick_size() unknown -> InstrumentNotFoundError (IC10)")


# ─────────────────────────────────────────────────────────────────────────────
# IC11: token_map()
# ─────────────────────────────────────────────────────────────────────────────

def test_token_map_correct(tmp_path: Path) -> None:
    """token_map() returns {token: symbol} for all rows (IC11)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    tmap = cache.token_map()
    assert tmap[738561] == "RELIANCE"
    assert tmap[2953217] == "TCS"
    assert len(tmap) == 5
    print("  OK token_map() correct mapping (IC11)")


# ─────────────────────────────────────────────────────────────────────────────
# IC15: count()
# ─────────────────────────────────────────────────────────────────────────────

def test_count_matches_csv(tmp_path: Path) -> None:
    """count() matches number of data rows in CSV (IC15)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    assert cache.count() == 5
    print("  OK count() == 5 (IC15)")


# ─────────────────────────────────────────────────────────────────────────────
# IC14: Thread safety (read-only after construction)
# ─────────────────────────────────────────────────────────────────────────────

def test_concurrent_reads_safe(tmp_path: Path) -> None:
    """Concurrent reads from multiple threads don't corrupt cache (IC14)."""
    import threading
    p = _write_csv(tmp_path, _VALID_CSV)
    cache = InstrumentCache.load(p)
    errors = []

    def reader():
        try:
            for _ in range(100):
                assert cache.sector("RELIANCE") == "ENERGY"
                assert cache.lot_size("TCS") == 1
                assert cache.has("SBIN")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"
    print("  OK concurrent reads safe (IC14)")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    tests = [
        test_load_valid_csv,
        test_load_missing_file_raises,
        test_load_missing_column_raises,
        test_load_bad_int_raises,
        test_load_lot_size_zero_raises,
        test_load_tick_size_zero_raises,
        test_load_empty_symbol_raises,
        test_row_types_correct,
        test_is_fno_parsed_bool,
        test_has_known_symbol,
        test_has_unknown_symbol,
        test_get_by_symbol_known,
        test_get_by_symbol_unknown_raises,
        test_get_by_token_known,
        test_get_by_token_unknown_raises,
        test_all_rows_load_order,
        test_all_rows_returns_copy,
        test_sector_known_symbol,
        test_sector_unknown_returns_unknown,
        test_sector_blank_returns_unknown,
        test_lot_size_known,
        test_lot_size_fno,
        test_lot_size_unknown_raises,
        test_tick_size_known,
        test_tick_size_unknown_raises,
        test_token_map_correct,
        test_count_matches_csv,
        test_concurrent_reads_safe,
    ]

    passed = failed = 0
    for fn in tests:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                fn(Path(tmp))
                passed += 1
            except Exception:
                print(f"  FAIL  {fn.__name__}")
                traceback.print_exc()
                failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    if failed:
        sys.exit(1)
