"""
tests/unit/test_refresh_instruments_assert.py -- post-write assertion guard added
after the 21-Jun instruments-staleness incident. Proves a silent no-write / stale /
truncated / malformed file is caught (so refresh exits non-zero -> heartbeat flags).
"""
from __future__ import annotations

import os
import time

from scripts.refresh_instruments import _check_fresh_write


def _good(p):
    # > 1000 bytes so it clears the size floor (real instruments.csv is ~75 KB)
    p.write_text("symbol,instrument_token,lot_size\n" + "SYMBOL,123456,1\n" * 300, encoding="utf-8")
    return p


def test_fresh_write_ok(tmp_path):
    ok, err = _check_fresh_write(_good(tmp_path / "instruments.csv"))
    assert ok and err == ""


def test_fresh_write_missing(tmp_path):
    ok, err = _check_fresh_write(tmp_path / "nope.csv")
    assert not ok and "missing" in err


def test_fresh_write_stale_mtime(tmp_path):
    p = _good(tmp_path / "instruments.csv")
    old = time.time() - 7200
    os.utime(p, (old, old))
    ok, err = _check_fresh_write(p)
    assert not ok and "mtime" in err


def test_fresh_write_too_small(tmp_path):
    p = tmp_path / "instruments.csv"
    p.write_text("symbol,token\n", encoding="utf-8")  # < 1000 bytes
    ok, err = _check_fresh_write(p)
    assert not ok and "below" in err


def test_fresh_write_bad_header(tmp_path):
    p = tmp_path / "instruments.csv"
    p.write_text("garbage,foo,bar\n" + "a,b,c\n" * 250, encoding="utf-8")
    ok, err = _check_fresh_write(p)
    assert not ok and "header" in err
