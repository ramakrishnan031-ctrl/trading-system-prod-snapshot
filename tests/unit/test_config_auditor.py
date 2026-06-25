"""
tests/unit/test_config_auditor.py — BUILD 2 (25-Jun-2026): Config Sanity Auditor.

Covers every check group (A-G), the startup BLOCK gate, the pre-flight integration,
and parity. Tests mutate a deep copy of the REAL loaded SystemConfig (so the structure
is always exactly production) and call the auditor directly — bypassing the model
validator — so contradiction states that would refuse to construct can still be audited.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.config_auditor import (
    Severity,
    audit,
    audit_app_config,
    audit_system_config,
    ConfigContradictionError,
)

_CFG = Path("config")


@pytest.fixture(scope="module")
def app_config():
    from core.config_loader import load_all
    return load_all(_CFG)


@pytest.fixture(scope="module")
def base_system(app_config):
    return app_config.system


def _mut(base, **dotted):
    """Deep-copy `base` and set dotted paths (a__b__c=value). Assignment does NOT
    re-run the model validator (Pydantic validate_assignment is off), so we can build
    otherwise-illegal states for the auditor to inspect."""
    s = base.model_copy(deep=True)
    for key, value in dotted.items():
        obj = s
        parts = key.split("__")
        for p in parts[:-1]:
            obj = getattr(obj, p)
        setattr(obj, parts[-1], value)
    return s


# ── Group A — contradictions ──────────────────────────────────────────────────

class TestGroupAContradictions:
    def test_force_intraday_plus_delivery_blocks(self, base_system):
        s = _mut(base_system, force_intraday_only=True, trade_type="DELIVERY")
        r = audit(s, groups="A")
        assert r.verdict == "BLOCK"
        assert r.blocks and "CONTRADICTORY CONFIG" in r.blocks[0].message

    def test_valid_combos_pass(self, base_system):
        for fio, tt in [(True, "INTRADAY"), (True, "BOTH"), (False, "DELIVERY")]:
            s = _mut(base_system, force_intraday_only=fio, trade_type=tt)
            r = audit(s, groups="A")
            assert r.verdict in ("PASS", "WARN"), (fio, tt)
            assert not r.blocks, (fio, tt)

    def test_trade_type_out_of_domain_blocks(self, base_system):
        s = _mut(base_system, force_intraday_only=False, trade_type="BOGUS")
        r = audit(s, groups="A")
        assert r.blocks and "CONTRADICTORY CONFIG" in r.blocks[0].message

    def test_zero_strategies_trade_warns(self, base_system):
        # trade_type=DELIVERY + force_intraday_only false, but every strategy is
        # INTRADAY -> master DELIVERY gates them all out -> 0 trade -> WARN.
        class _S:
            def __init__(self, intent, enabled=True):
                self.intent, self.enabled = intent, enabled
        strategies = {"a": _S("INTRADAY"), "b": _S("INTRADAY")}
        s = _mut(base_system, force_intraday_only=False, trade_type="DELIVERY")
        r = audit(s, groups="A", strategies=strategies)
        assert r.verdict == "WARN"
        assert any(f.code == "A3_zero_strategies_trade" for f in r.warns)

    def test_real_config_no_contradiction(self, base_system):
        assert audit(base_system, groups="A").verdict == "PASS"


# ── Group B — single-source regression guards ─────────────────────────────────

class TestGroupBSingleSource:
    def test_resurrected_daily_loss_limit_warns(self, base_system):
        r = audit(base_system, groups="B",
                  raw_system_yaml={"capital": {"daily_loss_limit": 300}})
        assert r.verdict == "WARN"
        assert any(f.code == "B1_daily_loss_limit_resurrected" for f in r.warns)

    def test_resurrected_max_position_value_rs_warns(self, base_system):
        r = audit(base_system, groups="B",
                  raw_system_yaml={"position_sizing": {"max_position_value_rs": 2500}})
        assert any(f.code == "B2_max_position_value_rs_resurrected" for f in r.warns)

    def test_resurrected_live_test_mode_warns(self, base_system):
        r = audit(base_system, groups="B",
                  raw_system_yaml={"risk": {"live_test_mode": True,
                                            "live_test_max_open_positions": 1}})
        assert any(f.code == "B3_live_test_mode_resurrected" for f in r.warns)

    def test_tier_multipliers_in_scoring_warns(self, base_system):
        r = audit(base_system, groups="B", raw_system_yaml={},
                  raw_scoring_yaml={"tier_multipliers": {"HIGH": 1.0}})
        assert any(f.code == "B4_tier_multipliers_duplicated" for f in r.warns)

    def test_clean_raw_yaml_passes(self, base_system):
        r = audit(base_system, groups="B",
                  raw_system_yaml={"capital": {}, "position_sizing": {}, "risk": {}},
                  raw_scoring_yaml={})
        assert r.verdict == "PASS"

    def test_no_raw_yaml_skips(self, base_system):
        # startup path passes no raw yaml -> group B contributes nothing (PASS verdict)
        assert audit(base_system, groups="B").verdict == "PASS"


# ── Group C — capital-relative sanity ─────────────────────────────────────────

class TestGroupCCapitalRelative:
    def test_real_config_passes(self, base_system):
        assert audit(base_system, groups="C").verdict == "PASS"

    def test_position_cap_not_looser_than_concentration_warns(self, base_system):
        # pos cap <= concentration -> the backstop binds before routine sizing.
        s = _mut(base_system, position_sizing__max_position_value_pct=0.10,
                 position_sizing__max_concentration_pct=0.10)
        r = audit(s, groups="C")
        assert any(f.code == "C2_position_cap_not_looser" for f in r.warns)

    def test_daily_loss_pct_out_of_range_warns(self, base_system):
        s = _mut(base_system, risk__daily_loss_limit_pct=0.50)
        r = audit(s, groups="C")
        assert any(f.code == "C1_daily_loss_pct_range" for f in r.warns)

    def test_risk_per_trade_out_of_range_warns(self, base_system):
        s = _mut(base_system, position_sizing__risk_per_trade_pct=0.20)
        r = audit(s, groups="C")
        assert any(f.code == "C3_risk_per_trade_range" for f in r.warns)

    def test_risk_exceeds_concentration_warns(self, base_system):
        s = _mut(base_system, position_sizing__risk_per_trade_pct=0.04,
                 position_sizing__max_concentration_pct=0.02,
                 position_sizing__max_position_value_pct=0.40)
        r = audit(s, groups="C")
        assert any(f.code == "C4_risk_exceeds_concentration" for f in r.warns)


# ── Group D — active-override listing ─────────────────────────────────────────

class TestGroupDActiveOverrides:
    def test_empty_overrides_says_none(self, base_system):
        r = audit(base_system, groups="D")
        assert r.verdict == "PASS"
        assert any(f.code == "D_none" for f in r.infos)

    def test_set_override_is_listed(self, base_system):
        s = _mut(base_system,
                 entry_gate__slippage_control__overrides__by_symbol={"RELIANCE": 0.30})
        r = audit(s, groups="D", known_symbols={"RELIANCE"})
        active = [f for f in r.infos if f.code == "D_active"]
        assert active and "RELIANCE" in active[0].message

    def test_symbol_typo_warns(self, base_system):
        s = _mut(base_system,
                 entry_gate__slippage_control__overrides__by_symbol={"NOTASYM": 0.30})
        r = audit(s, groups="D", known_symbols={"RELIANCE"})
        assert any(f.code == "D_override_warn" for f in r.warns)


# ── Group E — launch-phase reminders ──────────────────────────────────────────

class TestGroupELaunchPhase:
    def test_entry_start_surfaced(self, base_system):
        r = audit(base_system, groups="E")
        infos = [f for f in r.infos if "entry_start" in f.message]
        assert infos and "LAUNCH-PHASE" in infos[0].message
        assert str(base_system.trading_hours.entry_start) in infos[0].message


# ── Group F — stale-default guard ─────────────────────────────────────────────

class TestGroupFStaleDefault:
    def test_aligned_defaults_pass(self, base_system):
        # the real config matches the component defaults (BUILD 1 aligned them)
        assert audit(base_system, groups="F").verdict == "PASS"

    def test_diverging_position_cap_default_warns(self, base_system):
        # component default is 0.40; set config to 0.35 -> divergence WARN.
        s = _mut(base_system, position_sizing__max_position_value_pct=0.35)
        r = audit(s, groups="F")
        assert any(f.metrics.get("param") == "max_position_value_pct" for f in r.warns)

    def test_diverging_daily_loss_default_warns(self, base_system):
        s = _mut(base_system, risk__daily_loss_limit_pct=0.05)
        r = audit(s, groups="F")
        assert any(f.metrics.get("param") == "daily_loss_limit_pct" for f in r.warns)


# ── Group G — cross-field sanity ──────────────────────────────────────────────

class TestGroupGCrossField:
    def test_entry_end_near_squareoff_warns(self, base_system):
        # real config: entry_end 15:15, squareoff 15:17 -> within 15min -> WARN.
        r = audit(base_system, groups="G")
        assert any(f.code == "G1_entry_end_near_squareoff" for f in r.warns)

    def test_comfortable_window_passes(self, base_system):
        s = _mut(base_system, trading_hours__entry_end="14:00")
        r = audit(s, groups="G")
        assert not any(f.code == "G1_entry_end_near_squareoff" for f in r.warns)

    def test_high_leverage_warns(self, base_system):
        s = _mut(base_system, capital__leverage_map__INTRADAY=15.0)
        r = audit(s, groups="G")
        assert any(f.code.startswith("G2_leverage") for f in r.warns)

    def test_micro_tick_warns(self, base_system):
        s = _mut(base_system, position_sizing__min_tick_size=0.001)
        r = audit(s, groups="G")
        assert any(f.code == "G3_min_tick_size" for f in r.warns)


# ── Report model + startup gate ───────────────────────────────────────────────

class TestReportAndStartup:
    def test_one_line_pass(self, base_system):
        # Build an all-clean report by avoiding the pre-existing G entry-window WARN.
        s = _mut(base_system, trading_hours__entry_end="14:00")
        r = audit(s, groups="ACG")
        assert r.one_line().startswith("Config sanity: PASS")

    def test_raise_if_blocked(self, base_system):
        s = _mut(base_system, force_intraday_only=True, trade_type="DELIVERY")
        r = audit(s, groups="A")
        with pytest.raises(ConfigContradictionError, match="CONTRADICTORY CONFIG"):
            r.raise_if_blocked()

    def test_startup_subset_runs_acg_only(self, base_system):
        # audit_system_config must not emit B/D/E/F findings (no context at startup).
        r = audit_system_config(base_system)
        assert {f.group for f in r.findings} <= {"A", "C", "G"}

    def test_startup_blocks_contradiction_via_model_validate(self):
        from pydantic import ValidationError
        from core.config_loader import SystemConfig, load_all
        data = load_all(_CFG).system.model_dump()
        data["force_intraday_only"] = True
        data["trade_type"] = "DELIVERY"
        with pytest.raises(ValidationError, match="CONTRADICTORY CONFIG"):
            SystemConfig.model_validate(data)
        # all non-contradictory combos still boot
        for fio, tt in [(True, "INTRADAY"), (True, "BOTH"), (False, "DELIVERY")]:
            data["force_intraday_only"], data["trade_type"] = fio, tt
            assert SystemConfig.model_validate(data).trade_type == tt

    def test_real_config_full_audit_no_blocks(self, app_config):
        r = audit_app_config(app_config, config_dir=_CFG)
        assert not r.blocks
        # A/B/C/F clean on the shipped config; only G (entry-window) warns.
        assert r.worst_in_group("A") is Severity.PASS
        assert r.worst_in_group("C") is Severity.PASS
        assert r.worst_in_group("F") is Severity.PASS


# ── Pre-flight integration + parity ───────────────────────────────────────────

class TestPreflightIntegration:
    def _ctx(self, mode="live"):
        from scripts.preflight.base import CheckContext
        return CheckContext(config_dir=_CFG, db_path=Path("data_store/trading_system.db"),
                            mode=mode, phase="A")

    def test_config_sanity_group_in_phase_a(self):
        from scripts.preflight.checks import phase_a_checks
        groups = {c.group for c in phase_a_checks()}
        assert "Config Sanity" in groups

    def test_seven_group_rows(self):
        from scripts.preflight.checks.config_sanity import CHECKS
        names = {c.name for c in CHECKS}
        assert names == {"A_contradictions", "B_single_source", "C_capital_relative",
                         "D_active_overrides", "E_launch_phase", "F_stale_defaults",
                         "G_cross_field"}

    def test_rows_render_on_real_config(self):
        from scripts.preflight.base import Status
        from scripts.preflight.checks.config_sanity import CHECKS
        ctx = self._ctx()
        results = {c.name: c.run(ctx) for c in CHECKS}
        # A clean, G warns (entry-window) on the shipped config.
        assert results["A_contradictions"].status is Status.PASS
        assert results["G_cross_field"].status is Status.WARN
        # none of the group rows hard-fail on the shipped config
        assert all(r.status is not Status.FAIL for r in results.values())

    def test_parity_paper_equals_live(self):
        from scripts.preflight.checks.config_sanity import CHECKS
        live = {c.name: c.run(self._ctx("live")) for c in CHECKS}
        paper = {c.name: c.run(self._ctx("paper")) for c in CHECKS}
        for name in live:
            assert (live[name].status, live[name].detail) == (paper[name].status, paper[name].detail), name

    def test_audit_computed_once_per_context(self):
        # the memo means the 7 rows share one auditor run.
        from scripts.preflight.checks import config_sanity
        ctx = self._ctx()
        config_sanity.CHECKS[0].run(ctx)
        assert config_sanity._CACHE_KEY in ctx.extra
