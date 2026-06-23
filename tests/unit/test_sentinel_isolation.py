"""
tests/unit/test_sentinel_isolation.py — regression guard for the conftest autouse
sandbox (tests/conftest.py::_isolate_real_sentinels).

Running the full suite on the LIVE VM emailed test-written CRITICAL sentinels
because tests wrote ``critical_alert_*.flag`` into the real <project>/data_store
that the live alert-watcher consumes. These assert the sandbox keeps test writes
OUT of the real dir, while leaving explicit-tmp writes alone.
"""
from __future__ import annotations

from pathlib import Path

import alerts.critical as ac

_REAL = (Path(__file__).resolve().parents[2] / "data_store").resolve()


def _flags(d: Path):
    return set(d.glob("critical_alert_*.flag")) if d.exists() else set()


def test_default_dir_write_does_not_touch_real_data_store():
    before = _flags(_REAL)
    # No sentinel_dir -> the function default "data_store" -> would hit the real
    # data_store without the autouse fixture.
    ac.write_critical_sentinel(
        title="ISO-TEST default dir", body="x",
        source_module="test_sentinel_isolation",
    )
    assert _flags(_REAL) == before


def test_explicit_real_dir_write_is_redirected():
    before = _flags(_REAL)
    ac.write_critical_sentinel(
        title="ISO-TEST explicit real dir", body="x",
        source_module="test_sentinel_isolation", sentinel_dir=_REAL,
    )
    assert _flags(_REAL) == before


def test_explicit_tmp_dir_passes_through(tmp_path):
    # A test's own tmp dir is NOT the real data_store -> pass through unchanged.
    ac.write_critical_sentinel(
        title="ISO-TEST tmp", body="x",
        source_module="test_sentinel_isolation", sentinel_dir=tmp_path,
    )
    assert len(list(tmp_path.glob("critical_alert_*.flag"))) == 1
