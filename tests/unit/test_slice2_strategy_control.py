"""Slice 2 (24-Jun): 3-layer strategy control + status table.

LAYER 0 force_intraday_only (load-time intent rewrite, unchanged) · LAYER 1
system_config.trade_type · LAYER 2 strategy.intent · LAYER 3 strategy.enabled.
ONE resolver (strategies.control.strategy_will_trade) drives BOTH the entry gate
(signals.signal_processor, two sites) AND the status table (scripts.strategy_status),
so the table can never disagree with live behaviour.

Default (shipped) state = trade_type INTRADAY, force_intraday_only true, ALL 15
enabled → 15 WILL TRADE / 0 WON'T TRADE (TRUE zero behaviour change; the 3
delivery strategies keep trading as intraday as they do today).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from strategies.control import strategy_will_trade

_CFG = Path("config")


class _S:
    """Minimal strategy stand-in (resolver reads only .intent + .enabled)."""
    def __init__(self, intent="INTRADAY", enabled=True):
        self.intent = intent
        self.enabled = enabled


# ── Phase 8.1 — resolver truth table ─────────────────────────────────────────

class TestResolverTruthTable:
    def test_intraday_master_intraday_intent_enabled_will_trade(self):
        v = strategy_will_trade(_S("INTRADAY"), trade_type="INTRADAY", force_intraday_only=False)
        assert v.will_trade and v.product == "INTRADAY"

    def test_intraday_master_delivery_intent_wont_trade(self):
        v = strategy_will_trade(_S("DELIVERY"), trade_type="INTRADAY", force_intraday_only=False)
        assert not v.will_trade and "master INTRADAY" in v.reason

    def test_delivery_master_delivery_intent_will_trade(self):
        v = strategy_will_trade(_S("DELIVERY"), trade_type="DELIVERY", force_intraday_only=False)
        assert v.will_trade and v.product == "DELIVERY"

    def test_both_master_either_intent_will_trade(self):
        assert strategy_will_trade(_S("INTRADAY"), trade_type="BOTH", force_intraday_only=False).will_trade
        assert strategy_will_trade(_S("DELIVERY"), trade_type="BOTH", force_intraday_only=False).will_trade

    def test_disabled_wont_trade_any_master(self):
        for tt in ("INTRADAY", "DELIVERY", "BOTH"):
            v = strategy_will_trade(_S("INTRADAY", enabled=False), trade_type=tt, force_intraday_only=False)
            assert not v.will_trade and "switch disabled" in v.reason

    def test_force_intraday_only_plus_raw_delivery_wont_trade(self):
        # Defensive breaker branch (a raw DELIVERY intent reaching the resolver
        # under the breaker). In production the loader pre-rewrites to INTRADAY.
        v = strategy_will_trade(_S("DELIVERY"), trade_type="BOTH", force_intraday_only=True)
        assert not v.will_trade and "breaker" in v.reason.lower()

    def test_force_intraday_only_plus_intraday_enabled_will_trade(self):
        v = strategy_will_trade(_S("INTRADAY"), trade_type="INTRADAY", force_intraday_only=True)
        assert v.will_trade and v.product == "INTRADAY"

    def test_enabled_switch_actually_blocks(self):
        # The switch functions even though the default ships all enabled:true.
        on = strategy_will_trade(_S("INTRADAY", enabled=True), trade_type="INTRADAY", force_intraday_only=True)
        off = strategy_will_trade(_S("INTRADAY", enabled=False), trade_type="INTRADAY", force_intraday_only=True)
        assert on.will_trade and not off.will_trade


# ── Phase 8.5 — schema / config ──────────────────────────────────────────────

class TestSchemaConfig:
    def test_enabled_defaults_true_when_absent(self):
        # A YAML without `enabled` -> StrategyConfig.enabled defaults True (opt-in;
        # zero-behaviour-change for any pre-Slice-2 YAML).
        import yaml
        from strategies.schema import StrategyConfig
        raw = yaml.safe_load((_CFG / "strategies" / "gap_go_long.yaml").read_text(encoding="utf-8"))
        raw.pop("enabled", None)
        assert StrategyConfig(**raw).enabled is True

    def test_all_15_yamls_load_with_enabled(self):
        from strategies.schema import validate_strategy
        files = sorted((_CFG / "strategies").glob("*.yaml"))
        assert len(files) == 15
        for p in files:
            cfg = validate_strategy(p)
            assert isinstance(cfg.enabled, bool)

    def test_trade_type_default_intraday(self):
        from core.config_loader import load_all
        assert load_all(_CFG).system.trade_type == "INTRADAY"

    def test_trade_type_validator_rejects_invalid(self):
        from core.config_loader import SystemConfig, load_all
        data = load_all(_CFG).system.model_dump()
        data["trade_type"] = "BOGUS"
        with pytest.raises(Exception):
            SystemConfig.model_validate(data)
        for ok in ("INTRADAY", "DELIVERY", "BOTH"):
            data["trade_type"] = ok
            assert SystemConfig.model_validate(data).trade_type == ok


# ── Phase 8.3 — product wiring + INVARIANT ───────────────────────────────────

class TestProductWiring:
    def test_delivery_intent_resolves_to_cnc(self):
        from broker.product_resolver import ProductResolver
        pr = ProductResolver({"zerodha": {"INTRADAY": "MIS", "DELIVERY": "CNC", "COVER_ORDER": "CO"}})
        assert pr.resolve("DELIVERY", "zerodha") == "CNC"
        assert pr.resolve("INTRADAY", "zerodha") == "MIS"

    def test_invariant_default_state_never_places_cnc(self):
        # Default: trade_type INTRADAY + force_intraday_only true. Every WILL-TRADE
        # strategy's effective product is INTRADAY (MIS) — never DELIVERY/CNC.
        from strategies.loader import StrategyLoader
        loaded = StrategyLoader().load_all_strategies(_CFG / "strategies", force_intraday_only=True)
        for name, s in loaded.items():
            v = strategy_will_trade(s, trade_type="INTRADAY", force_intraday_only=True)
            if v.will_trade:
                assert v.product == "INTRADAY", f"{name} would place non-MIS under default!"


# ── Phase 8.6 — regression: default state = unchanged (15 trade) ──────────────

class TestDefaultStateRegression:
    def test_default_real_config_15_will_trade_0_wont(self):
        from strategies.loader import StrategyLoader
        loaded = StrategyLoader().load_all_strategies(_CFG / "strategies", force_intraday_only=True)
        verdicts = {n: strategy_will_trade(s, trade_type="INTRADAY", force_intraday_only=True)
                    for n, s in loaded.items()}
        will = [n for n, v in verdicts.items() if v.will_trade]
        wont = [n for n, v in verdicts.items() if not v.will_trade]
        assert len(will) == 15 and len(wont) == 0


# ── Phase 8.4 — status table ─────────────────────────────────────────────────

class TestStatusTable:
    def _rows(self, **kw):
        from scripts.strategy_status import build_status_rows
        kw.setdefault("trade_type", "INTRADAY")
        kw.setdefault("force_intraday_only", True)
        return build_status_rows(_CFG, **kw)

    def test_table_verdicts_equal_gate(self):
        # The table verdict for each strategy == the gate resolver on the loaded
        # (post-rewrite) config — same function, same inputs, can't disagree.
        from strategies.loader import StrategyLoader
        rows = self._rows()
        loaded = StrategyLoader().load_all_strategies(_CFG / "strategies", force_intraday_only=True)
        for r in rows:
            gate = strategy_will_trade(loaded[r.strategy], trade_type="INTRADAY", force_intraday_only=True)
            assert r.will_trade == gate.will_trade

    def test_type_column_shows_true_intent(self):
        # Delivery strategies show Type=DELIVERY (true intent) even though they
        # trade as intraday under the breaker (verdict WILL TRADE).
        rows = {r.strategy: r for r in self._rows()}
        assert rows["positional_sector_rotation"].type == "DELIVERY"
        assert rows["positional_sector_rotation"].will_trade is True
        assert rows["gap_go_long"].type == "INTRADAY"

    def test_will_trade_sorted_first(self):
        from scripts.strategy_status import build_status_rows
        # one delivery enabled (BOTH so it WILL), the rest as default
        rows = build_status_rows(_CFG, trade_type="INTRADAY", force_intraday_only=True)
        verdicts = [r.will_trade for r in rows]
        assert verdicts == sorted(verdicts, reverse=True)  # all True here, but order holds

    def test_footnote_flags_delivery_as_intraday(self):
        from scripts.strategy_status import footnote
        note = footnote(self._rows(), force_intraday_only=True)
        assert note and "DELIVERY" in note and "force_intraday_only" in note

    def test_malformed_yaml_becomes_config_error_row(self, tmp_path):
        from scripts.strategy_status import build_status_rows
        sdir = tmp_path / "strategies"
        sdir.mkdir()
        (sdir / "broken.yaml").write_text("name: broken\nintent: [not-a-string\n", encoding="utf-8")
        rows = build_status_rows(tmp_path, trade_type="INTRADAY", force_intraday_only=True)
        assert len(rows) == 1 and rows[0].verdict == "CONFIG ERROR"

    def test_compact_lists_split_correctly(self):
        from scripts.strategy_status import compact_lists
        will, wont, err = compact_lists(self._rows())
        assert len(will) == 15 and wont == [] and err == []

    def test_disabled_strategy_shows_wont_trade(self, tmp_path):
        # Copy a real YAML, set enabled:false -> table shows WON'T TRADE (switch).
        import shutil
        sdir = tmp_path / "strategies"
        sdir.mkdir()
        src = (_CFG / "strategies" / "gap_go_long.yaml").read_text(encoding="utf-8")
        (sdir / "gap_go_long.yaml").write_text(src.replace("enabled: true", "enabled: false"), encoding="utf-8")
        from scripts.strategy_status import build_status_rows
        rows = build_status_rows(tmp_path, trade_type="INTRADAY", force_intraday_only=True)
        assert rows[0].switch == "DISABLED" and not rows[0].will_trade


# ── Phase 8.2 / 5.2 — gate at both sites + CNC paper placement ────────────────
# Reuse the proven signal_processor harness.
from tests.unit.test_signal_processor import (  # noqa: E402
    _make_proc, _run_one, _assert_rejected, _insert_queued_signal, _now_tup,
    _MockStrategy,
)


def _disabled_proc(store, intent="INTRADAY", enabled=False, trade_type="INTRADAY",
                   force_intraday_only=False):
    s = _MockStrategy(name="gap_go_long_v1", direction="LONG", intent=intent)
    s.enabled = enabled
    proc, _, _ = _make_proc(
        store=store,
        strategies={"gap_go_long_v1": s},
        scan_webhook_map={"gap_go_long": {"strategy": "gap_go_long_v1"}},
    )
    proc._trade_type = trade_type
    proc._force_intraday_only = force_intraday_only
    return proc


class TestGate:
    def test_disabled_strategy_rejected_at_process_one(self):
        from tests.unit.test_signal_processor import _make_store
        store, _ = _make_store()
        sig_id = "sig_sc_disabled"
        _insert_queued_signal(store, sig_id, scanner="gap_go_long")
        proc = _disabled_proc(store, enabled=False)
        _run_one(proc, _now_tup(sig_id, scanner="gap_go_long"), store=store)
        _assert_rejected(store, sig_id, "REJECTED_STRATEGY_CONTROL")

    def test_master_intraday_blocks_delivery_at_process_one(self):
        from tests.unit.test_signal_processor import _make_store
        store, _ = _make_store()
        sig_id = "sig_sc_master"
        _insert_queued_signal(store, sig_id, scanner="gap_go_long")
        # enabled DELIVERY strategy, master INTRADAY, breaker off -> blocked
        proc = _disabled_proc(store, intent="DELIVERY", enabled=True,
                              trade_type="INTRADAY", force_intraday_only=False)
        _run_one(proc, _now_tup(sig_id, scanner="gap_go_long"), store=store)
        _assert_rejected(store, sig_id, "REJECTED_STRATEGY_CONTROL")

    def test_enabled_intraday_not_rejected_by_control_gate(self):
        from tests.unit.test_signal_processor import _make_store
        store, _ = _make_store()
        sig_id = "sig_sc_ok"
        _insert_queued_signal(store, sig_id, scanner="gap_go_long")
        proc = _disabled_proc(store, intent="INTRADAY", enabled=True)
        _run_one(proc, _now_tup(sig_id, scanner="gap_go_long"), store=store)
        # NOT rejected by the control gate (may proceed/!= STRATEGY_CONTROL).
        row = store.fetch_one("SELECT status FROM signals WHERE signal_id=?", (sig_id,))
        assert row["status"] != "REJECTED_STRATEGY_CONTROL"

    def test_continue_from_gate_rejects_disabled(self):
        from tests.unit.test_signal_processor import _make_store
        store, _ = _make_store()
        sig_id = "sig_sc_cfg"
        _insert_queued_signal(store, sig_id, scanner="gap_go_long")
        proc = _disabled_proc(store, enabled=False)
        entry = SimpleNamespace(
            signal_id=sig_id, symbol="RELIANCE", strategy_name="gap_go_long_v1",
            entry_price=2500.0, sl_price=2450.0, tgt_price=2600.0,
            direction="LONG", tier="HIGH",
        )
        proc.continue_from_gate(entry)
        _assert_rejected(store, sig_id, "REJECTED_STRATEGY_CONTROL")

    def test_parity_gate_identical_paper_and_live(self):
        # The resolver/gate is pure (no mode branch): same verdict regardless of
        # the SignalProcessor mode label.
        from tests.unit.test_signal_processor import _make_store
        for mode in ("PAPER", "LIVE"):
            store, _ = _make_store()
            sig_id = f"sig_sc_parity_{mode}"
            _insert_queued_signal(store, sig_id, scanner="gap_go_long")
            proc = _disabled_proc(store, enabled=False)
            proc._mode = mode
            _run_one(proc, _now_tup(sig_id, scanner="gap_go_long"), store=store)
            _assert_rejected(store, sig_id, "REJECTED_STRATEGY_CONTROL")


class TestCncPaperPlacement:
    def test_delivery_resolver_to_cnc_paper_place(self):
        """Phase 5.2: resolver(DELIVERY, master BOTH, breaker off) -> product
        DELIVERY -> product_resolver -> CNC -> a paper order places. PLACEMENT
        only (the delivery LIFECYCLE is parked Slice 2.5). NO live delivery."""
        from broker.product_resolver import ProductResolver
        v = strategy_will_trade(_S("DELIVERY"), trade_type="BOTH", force_intraday_only=False)
        assert v.will_trade and v.product == "DELIVERY"
        pr = ProductResolver({"zerodha": {"INTRADAY": "MIS", "DELIVERY": "CNC", "COVER_ORDER": "CO"}})
        assert pr.resolve(v.product, "zerodha") == "CNC"
