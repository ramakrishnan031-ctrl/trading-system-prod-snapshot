"""
tests/unit/test_e1_report_config_dir.py -- Trading System v2

E1: `reports/daily_report.py` accepts `--config-dir` but sheet 6 called
`build_taxonomy_map()` with no argument, so the declared pipeline/horizon columns
were always read from `./config` relative to CWD, whatever the flag said.
`strategies/taxonomy.py:23` itself is correct -- it honours its parameter. The
defect is the caller.

PRODUCTION IMPACT: none. The cron runs `-m reports.daily_report` with no
`--config-dir` (config/cron_registry.yaml), and both the argparse default and
build_taxonomy_map's default are "config", so the 16:05 job is byte-identical
before and after. This only ever bit a manual run pointed at another config dir.
That no-op-in-production property is itself pinned below.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import reports.daily_report as dr


def _empty_data():
    """Minimal ReportData shape for sheet 6 (empty collections, one date)."""
    return SimpleNamespace(signals=[], trades=[], screener_results=[],
                           date_iso="2026-07-24")


class TestSheet6HonoursConfigDir:

    def test_config_dir_is_passed_through_to_build_taxonomy_map(self):
        """RED on HEAD: build_sheet_6_strategy takes no config_dir (TypeError),
        and the inner call passes no argument at all."""
        wb = openpyxl.Workbook()
        seen = {}

        def _spy(config_dir="config"):
            seen["config_dir"] = config_dir
            return {}

        with patch("strategies.taxonomy.build_taxonomy_map", new=_spy):
            dr.build_sheet_6_strategy(wb, _empty_data(),
                                      config_dir=Path("/nonexistent/cfg"))

        assert str(seen["config_dir"]) == str(Path("/nonexistent/cfg"))

    def test_omitting_config_dir_keeps_the_historic_default(self):
        """PIN: the parameter is optional and defaults to "config", so every
        existing caller (and the no-flag cron) behaves exactly as before."""
        wb = openpyxl.Workbook()
        seen = {}

        def _spy(config_dir="config"):
            seen["config_dir"] = config_dir
            return {}

        with patch("strategies.taxonomy.build_taxonomy_map", new=_spy):
            dr.build_sheet_6_strategy(wb, _empty_data())

        assert str(seen["config_dir"]) == "config"

    def test_a_real_alternate_config_dir_changes_the_map(self, tmp_path):
        """Anti-vacuity: prove the flag can actually change the RESULT, not just
        the argument. A config dir with no strategies/ yields an empty map --
        which differs from the repo's real config dir."""
        from strategies.taxonomy import build_taxonomy_map
        real = build_taxonomy_map(Path(__file__).parent.parent.parent / "config")
        empty = build_taxonomy_map(tmp_path)
        assert real, "the repo config dir must yield a non-empty taxonomy map"
        assert empty == {}
        assert real != empty


class TestProductionInvocationUnchanged:

    def test_cron_passes_no_config_dir_flag(self):
        """B2: Monday's 16:05 must behave byte-identically. The registry entry
        must not carry --config-dir, so the argparse default ("config") applies
        and matches build_taxonomy_map's own default."""
        import yaml
        reg = yaml.safe_load(
            (Path(__file__).parent.parent.parent
             / "config" / "cron_registry.yaml").read_text(encoding="utf-8"))
        jobs = reg.get("jobs", reg)
        entry = jobs["daily_report"]
        blob = " ".join(str(entry.get(k, "")) for k in ("script", "command"))
        assert "config-dir" not in blob, (
            "the cron now passes --config-dir; re-check that the E1 change is "
            "still a production no-op"
        )

    def test_argparse_default_matches_taxonomy_default(self):
        """The two defaults must agree, or the no-flag path would still diverge."""
        import inspect
        from strategies.taxonomy import build_taxonomy_map
        assert inspect.signature(
            build_taxonomy_map).parameters["config_dir"].default == "config"
        parser_src = inspect.getsource(dr)
        assert '"--config-dir"' in parser_src and 'default="config"' in parser_src


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
