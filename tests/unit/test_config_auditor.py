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

    # ── A4 (Q4(c)): structure_exit single-SL-owner vs strategy trailing_sl ──────
    @staticmethod
    def _trail(trailing: bool, enabled: bool = True):
        class _S:
            def __init__(self):
                self.trailing_sl_enabled, self.enabled = trailing, enabled
        return _S()

    def test_structure_exit_plus_trailing_sl_blocks(self, base_system):
        strategies = {"gap_go_long": self._trail(True),
                      "vwap_bounce_long": self._trail(False)}
        s = _mut(base_system, structure_exit__structure_exit_enabled=True)
        r = audit(s, groups="A", strategies=strategies)
        assert r.verdict == "BLOCK"
        a4 = [f for f in r.blocks if f.code == "A4_structure_exit_trailing_sl"]
        assert a4 and "CONTRADICTORY CONFIG" in a4[0].message
        # names the offender, not the innocent
        assert "gap_go_long" in a4[0].message and "vwap_bounce_long" not in a4[0].message
        assert a4[0].metrics["trailing_strategies"] == ["gap_go_long"]

    def test_structure_exit_off_trailing_sl_ok(self, base_system):
        # trailing strategy is harmless when structure-exit is OFF (only one SL owner)
        strategies = {"gap_go_long": self._trail(True)}
        s = _mut(base_system, structure_exit__structure_exit_enabled=False)
        r = audit(s, groups="A", strategies=strategies)
        assert not any(f.code == "A4_structure_exit_trailing_sl" for f in r.blocks)

    def test_structure_exit_on_no_trailing_ok(self, base_system):
        strategies = {"gap_go_long": self._trail(False),
                      "vwap_bounce_long": self._trail(False)}
        s = _mut(base_system, structure_exit__structure_exit_enabled=True)
        r = audit(s, groups="A", strategies=strategies)
        assert not any(f.code == "A4_structure_exit_trailing_sl" for f in r.blocks)

    def test_structure_exit_disabled_strategy_trailing_not_blocked(self, base_system):
        # a DISABLED strategy that trails won't trade -> no SL to race -> no block
        strategies = {"gap_go_long": self._trail(True, enabled=False)}
        s = _mut(base_system, structure_exit__structure_exit_enabled=True)
        r = audit(s, groups="A", strategies=strategies)
        assert not any(f.code == "A4_structure_exit_trailing_sl" for f in r.blocks)

    def test_structure_exit_no_strategies_context_skips_a4(self, base_system):
        # config-only startup subset (strategies=None) cannot evaluate A4 -> no false block
        s = _mut(base_system, structure_exit__structure_exit_enabled=True)
        r = audit(s, groups="A")   # no strategies
        assert not any(f.code == "A4_structure_exit_trailing_sl" for f in r.blocks)

    # ── A5 (SLICE2.5 #16a, 27-Jul): delivery live + fixed split + delivery-only book ──
    @staticmethod
    def _intent(intent: str, enabled: bool = True):
        class _S:
            def __init__(self):
                self.intent, self.enabled = intent, enabled
                self.trailing_sl_enabled = False    # keep A4 out of these results
        return _S()

    @staticmethod
    def _a5(r):
        return [f for f in r.blocks if f.code == "A5_delivery_without_conditional_allocation"]

    def _delivery_live(self, base_system, **over):
        kw = dict(delivery_enabled=True, force_intraday_only=False,
                  trade_type="DELIVERY", capital__conditional_allocation_enabled=False)
        kw.update(over)
        return _mut(base_system, **kw)

    def test_a5_delivery_only_book_with_fixed_split_blocks(self, base_system):
        strategies = {"pos_swing": self._intent("DELIVERY"),
                      "gap_go_long": self._intent("INTRADAY", enabled=False)}
        r = audit(self._delivery_live(base_system), groups="A", strategies=strategies)
        a5 = self._a5(r)
        assert r.verdict == "BLOCK" and a5, "delivery-only book + fixed split must BLOCK"
        assert "CONTRADICTORY CONFIG" in a5[0].message
        # names the offender, not the innocent
        assert "pos_swing" in a5[0].message and "gap_go_long" not in a5[0].message
        assert a5[0].metrics["delivery_only_strategies"] == ["pos_swing"]

    def test_a5_does_not_fire_when_both_intents_are_live(self, base_system):
        """THE false positive the naive rule would have caused.

        MEASURED at fund_manager.py:139-146: with conditional_enabled TRUE *and both
        intents active* the split is the SAME as with it FALSE. So a rule keyed on
        the flag PAIR would block a behaviourally IDENTICAL config -- and
        trade_type=BOTH is the likeliest production setting. RED if A5 is ever
        rewritten to test the flags instead of the active intent set.
        """
        strategies = {"pos_swing": self._intent("DELIVERY"),
                      "gap_go_long": self._intent("INTRADAY")}
        r = audit(self._delivery_live(base_system, trade_type="BOTH"),
                  groups="A", strategies=strategies)
        assert not self._a5(r), "BOTH with both intents live is not a foot-gun"

    def test_a5_silent_when_conditional_allocation_is_on(self, base_system):
        strategies = {"pos_swing": self._intent("DELIVERY")}
        r = audit(self._delivery_live(base_system, capital__conditional_allocation_enabled=True),
                  groups="A", strategies=strategies)
        assert not self._a5(r)

    def test_a5_cannot_fire_on_an_ordinary_morning(self, base_system):
        """The precondition is what makes BLOCK the right severity: delivery_enabled
        has been false since 15-Jun, so this rule cannot cost an unattended boot."""
        strategies = {"pos_swing": self._intent("DELIVERY")}
        r = audit(self._delivery_live(base_system, delivery_enabled=False),
                  groups="A", strategies=strategies)
        assert not self._a5(r)

    def test_a5_silent_when_no_strategy_can_trade(self, base_system):
        # a silent book is A3's WARN, not this BLOCK -- do not double-report it
        strategies = {"pos_swing": self._intent("DELIVERY", enabled=False)}
        r = audit(self._delivery_live(base_system), groups="A", strategies=strategies)
        assert not self._a5(r)

    def test_a5_skipped_without_strategies_context(self, base_system):
        # the config-only startup subset cannot resolve the active set -> no false block
        r = audit(self._delivery_live(base_system), groups="A")
        assert not self._a5(r)

    def test_a5_is_wired_as_a_boot_gate_not_only_a_preflight_report(self, base_system):
        """A5 needs `strategies`, which config_loader's startup audit does NOT pass --
        so without a re-run in main.py the BLOCK degrades silently into an 08:30
        pre-flight EMAIL. main.py re-runs group A with strategies and exits 3
        (RestartPreventExitStatus="3 4" -> stops cleanly, no restart loop).

        Pinned as a source predicate because the realistic drift is silent: rename the
        finding code in the auditor and the guard's string filter stops matching, with
        nothing failing anywhere. RED if either side drifts, or if the guard stops
        passing strategies.
        """
        # Derive the code from a finding the auditor ACTUALLY emits, identified by its
        # subject rather than by the literal -- so a rename on EITHER side goes red.
        # (Asserting the literal against main.py alone does not: main.py still contains
        # the old string, so the test passed while the coupling was broken. MEASURED.)
        strategies = {"pos_swing": self._intent("DELIVERY")}
        r = audit(self._delivery_live(base_system), groups="A", strategies=strategies)
        emitted = [f for f in r.blocks if "conditional_allocation_enabled" in f.message]
        assert emitted, "precondition: the auditor must emit the foot-gun BLOCK"
        code = emitted[0].code

        src = (Path("main.py")).read_text(encoding="utf-8")
        i = src.find("slice25_delivery_capital_footgun")
        assert i > 0, "boot guard missing from main.py -- A5 would be a report, not a gate"
        # CODE lines only. A window that includes the explanatory comment above the
        # guard would match on prose -- the same trap as grepping source for an
        # option name (see test_lock_socket_does_not_set_so_reuseaddr). MEASURED:
        # with comments included, disabling the guard with `if False:` stayed GREEN.
        window = src[max(0, i - 1400):i + 300].splitlines()
        guard = "\n".join(
            ln for ln in window if ln.strip() and not ln.strip().startswith("#"))
        assert code in guard, (
            f"boot guard does not filter on the auditor's emitted code {code!r} -- a "
            "rename on either side silently turns the BLOCK back into a pre-flight report")
        assert "strategies=strategies" in guard, (
            "boot guard must pass strategies; without them A5 can never be seen")
        assert "return 3" in guard, "config block must exit 3 (no restart loop)"
        assert "delivery_enabled" in guard, (
            "boot guard must be GATED on delivery_enabled -- a guard disabled by"
            " a constant is dead code that this scan would otherwise still find")


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

    # ── NI-2 (22-Aug-2026): C2 must evaluate the EFFECTIVE ceiling ───────────
    # The multiplier is applied AFTER the concentration constraint, so comparing
    # posv against conc directly can pass a config in which the catastrophic-loss
    # backstop binds on routine sizing. Every test below uses multiplier != 1.

    def test_c2_ni2_fires_once_the_multiplier_is_evaluated(self, base_system):
        """The config the OLD check passed and should not have.

        conc 0.25 / posv 0.40 satisfies posv > conc, so the pre-NI-2 comparison was
        SILENT. The effective routine ceiling is 0.25 x 2.0 = 0.50 > 0.40, i.e. the
        backstop would bind on routine sizing.
        """
        s = _mut(base_system,
                 position_sizing__max_concentration_pct=0.25,
                 position_sizing__max_position_value_pct=0.40,
                 position_sizing__max_multiplier=2.0)
        ps = s.position_sizing
        # premise, asserted rather than assumed: the OLD check is silent on this input
        assert ps.max_position_value_pct > ps.max_concentration_pct
        assert ps.max_multiplier != 1.0

        r = audit(s, groups="C")
        hits = [f for f in r.warns if f.code == "C2_position_cap_not_looser"]
        assert hits, "C2 must fire once the multiplier is part of the ceiling"
        assert hits[0].metrics["max_effective_multiplier"] == pytest.approx(2.0)
        assert hits[0].metrics["effective_concentration_ceiling"] == pytest.approx(0.50)
        assert "0.50" in f"{hits[0].metrics['effective_concentration_ceiling']:.2f}"

    def test_c2_ceiling_is_derived_from_config_not_hardcoded(self, base_system):
        """Same conc/posv, max_multiplier 1.0 -> the effective ceiling IS concentration.

        Proves the multiplier term is live rather than a constant 2 baked in: with a
        multiplier that genuinely cannot double, C2 reduces exactly to its pre-NI-2
        behaviour and stays silent on 0.25/0.40.
        """
        s = _mut(base_system,
                 position_sizing__max_concentration_pct=0.25,
                 position_sizing__max_position_value_pct=0.40,
                 position_sizing__max_multiplier=1.0)
        r = audit(s, groups="C")
        assert not [f for f in r.warns if f.code == "C2_position_cap_not_looser"]

    def test_c2_is_silent_on_the_shipped_config(self, base_system):
        """Behaviour-neutrality on the config that actually ships.

        0.10 x 2.0 = 0.20 effective ceiling vs a 0.40 backstop -> a 2x margin, so the
        change adds NO finding to the live config. If concentration is ever raised past
        0.20 this goes red, which is the point.
        """
        ps = base_system.position_sizing
        r = audit(base_system, groups="C")
        assert not [f for f in r.warns if f.code == "C2_position_cap_not_looser"]
        assert ps.max_concentration_pct * ps.max_multiplier < ps.max_position_value_pct

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
        # G1 logic: an entry_end within 15min of squareoff WARNs. T5 (29-Jun)
        # aligned the LIVE config to entry_end=15:00 (17min before squareoff 15:17),
        # so the live config no longer warns -- assert the LOGIC via explicit 15:15.
        s = _mut(base_system, trading_hours__entry_end="15:15")
        assert any(f.code == "G1_entry_end_near_squareoff" for f in audit(s, groups="G").warns)
        # T5 outcome: the live config (entry_end=15:00) must NOT warn.
        assert not any(f.code == "G1_entry_end_near_squareoff"
                       for f in audit(base_system, groups="G").warns)

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

class TestGroupG5StrategyWindow:
    """M-K2 (24-Jul): G5 read `entry_start`/`entry_end`, but a StrategyConfig exposes
    `entry_start_time`/`entry_end_time` (strategies/schema.py), so getattr always
    returned None and the per-strategy window check NEVER fired. The fix renames the
    fields AND calibrates: warn only when a strategy window has NO usable overlap with
    the global envelope (can never enter) — a merely-wider window is harmlessly gated
    and must stay quiet (the 09:25-vs-10:00 launch-phase posture; 04-Jul 'cosmetic')."""

    def test_g5_fires_on_no_overlap_window(self, base_system):
        # RED before the fix: getattr(s, "entry_start", None) is None, so G5 is silent
        # even for a window that can never enter (opens 15:30; global closes 15:00).
        from types import SimpleNamespace
        strategies = {"late_only": SimpleNamespace(
            entry_start_time="15:30", entry_end_time="15:45")}
        r = audit(base_system, groups="G", strategies=strategies)
        assert any(f.code == "G5_window_late_only" for f in r.warns)

    def test_g5_quiet_on_gated_wider_window(self, base_system):
        # A 09:25 declaration (before global 10:00) overlaps 10:00-15:00 -> gated,
        # must NOT warn (guards against re-creating a daily false WARN in the G row).
        from types import SimpleNamespace
        strategies = {"early_decl": SimpleNamespace(
            entry_start_time="09:25", entry_end_time="15:00")}
        r = audit(base_system, groups="G", strategies=strategies)
        assert not any(f.code == "G5_window_early_decl" for f in r.warns)

    def test_g5_quiet_on_real_shipped_strategies(self, app_config):
        # End-to-end anti-noise guard: the shipped 09:25 strategies must produce ZERO
        # G5 window WARNs against the shipped global 10:00-15:00 envelope.
        from strategies.loader import StrategyLoader
        strategies = StrategyLoader().load_all_strategies(
            _CFG / "strategies",
            force_intraday_only=app_config.system.force_intraday_only,
        )
        r = audit(app_config.system, groups="G", strategies=strategies)
        assert not any(f.code.startswith("G5_window_") for f in r.warns)


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
        # A clean; after T5 (entry_end 15:15->15:00) the G entry-window WARN is gone,
        # so G_cross_field is now clean too on the shipped config.
        assert results["A_contradictions"].status is Status.PASS
        assert results["G_cross_field"].status is Status.PASS
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
