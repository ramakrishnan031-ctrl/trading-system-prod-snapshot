"""
core/config_auditor.py — BUILD 2 (25-Jun-2026): the Config Sanity Auditor.

The enforcement capstone of the Config Authority work. BUILD 1 CLEANED the config
(single daily-loss source, capital-relative position cap, dead-config cleanup);
BUILD 2 ENFORCES it stays clean and surfaces violations.

ONE rule engine, two callers (single source of truth — no rule lives twice):

  * STARTUP  — core/config_loader.SystemConfig._cross_field_sanity_checks() calls
               audit_system_config() with the config-only subset of groups. It logs
               WARN/INFO findings and raises (fail-fast) on any BLOCK. The #10
               force_intraday_only+DELIVERY contradiction now lives HERE.
  * PRE-FLIGHT — scripts/preflight/checks/config_sanity.py calls audit_app_config()
               with the FULL context (capital + strategies + symbols + raw YAML) and
               renders the per-group PASS/WARN/BLOCK report into the 08:30 Phase-A
               check-set → the 09:20 email + Telegram.

DESIGN PRINCIPLE: VALIDATE + ALERT only. The auditor never changes trading
behaviour. The only hard action is the BLOCK-level contradiction gate, which already
existed (BUILD 1 #10) and is merely re-homed here. Pure + dependency-light at module
top (heavy imports are deferred inside the groups that need them) so the startup path
can call it on every load_all() without cost or import-cycle risk.

Check groups (each finding is PASS / INFO / WARN / BLOCK with a clear message):
  A. Contradictions ......... logically-dead configs (BLOCK) + pointless ones (WARN)
  B. Single-source integrity . regression guards: a deleted key reappearing (WARN)
  C. Capital-relative sanity . pct ranges + the concentration<position-cap ladder (WARN)
  D. Active-override listing .. T3 visibility of every non-empty override (INFO/WARN)
  E. Launch-phase reminders ... params anchored to early-live caution (INFO)
  F. Stale-default guard ...... component constructor defaults vs config intent (WARN)
  G. Cross-field sanity ....... timing/leverage/tick cross-checks (WARN)
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Finding / report model
# ─────────────────────────────────────────────────────────────────────────────

class Severity(enum.Enum):
    """How loud a single finding is. Ordered worst-last for rollups."""
    PASS = "PASS"
    INFO = "INFO"
    WARN = "WARN"
    BLOCK = "BLOCK"


_SEV_RANK = {Severity.PASS: 0, Severity.INFO: 1, Severity.WARN: 2, Severity.BLOCK: 3}

# Group code -> human title (the report sections, and the pre-flight check names).
GROUP_TITLES: Dict[str, str] = {
    "A": "Contradictions",
    "B": "Single-source integrity",
    "C": "Capital-relative sanity",
    "D": "Active overrides",
    "E": "Launch-phase reminders",
    "F": "Stale-default guard",
    "G": "Cross-field sanity",
}
GROUP_ORDER = "ABCDEFG"


class ConfigContradictionError(ValueError):
    """Raised by ConfigAuditReport.raise_if_blocked(). Subclasses ValueError so a
    Pydantic model_validator that lets it propagate is wrapped into a
    ValidationError exactly like a plain ValueError (preserves the BUILD 1 #10
    `pytest.raises(ValidationError, match="CONTRADICTORY CONFIG")` contract)."""


@dataclass(frozen=True)
class AuditFinding:
    group: str                       # one of GROUP_ORDER
    code: str                        # stable id, e.g. "A1_force_intraday_delivery"
    severity: Severity
    message: str
    metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def group_title(self) -> str:
        return GROUP_TITLES.get(self.group, self.group)


@dataclass
class ConfigAuditReport:
    findings: List[AuditFinding] = field(default_factory=list)

    # ── filters ──────────────────────────────────────────────────────────────
    def by_severity(self, sev: Severity) -> List[AuditFinding]:
        return [f for f in self.findings if f.severity is sev]

    @property
    def blocks(self) -> List[AuditFinding]:
        return self.by_severity(Severity.BLOCK)

    @property
    def warns(self) -> List[AuditFinding]:
        return self.by_severity(Severity.WARN)

    @property
    def infos(self) -> List[AuditFinding]:
        return self.by_severity(Severity.INFO)

    def for_group(self, group: str) -> List[AuditFinding]:
        return [f for f in self.findings if f.group == group]

    def worst_in_group(self, group: str) -> Severity:
        fs = self.for_group(group)
        if not fs:
            return Severity.PASS
        return max((f.severity for f in fs), key=lambda s: _SEV_RANK[s])

    # ── rollups ──────────────────────────────────────────────────────────────
    @property
    def verdict(self) -> str:
        if self.blocks:
            return "BLOCK"
        if self.warns:
            return "WARN"
        return "PASS"

    @property
    def actionable(self) -> List[AuditFinding]:
        """BLOCK + WARN findings (what the operator must read)."""
        return [f for f in self.findings if f.severity in (Severity.BLOCK, Severity.WARN)]

    def one_line(self) -> str:
        """Compact verdict for a pre-flight check `detail` / log line."""
        if self.verdict == "PASS":
            n_info = len(self.infos)
            tail = f" ({n_info} info)" if n_info else ""
            return f"Config sanity: PASS{tail}"
        items = "; ".join(f"{f.group}:{f.message}" for f in self.actionable[:6])
        if self.blocks:
            return f"Config sanity: BLOCK ({len(self.blocks)}) — {items}"
        return f"Config sanity: {len(self.warns)} warning(s) — {items}"

    def raise_if_blocked(self) -> None:
        """Fail-fast gate for the startup path. Raises ConfigContradictionError
        (a ValueError) carrying every BLOCK message joined."""
        if self.blocks:
            raise ConfigContradictionError(" | ".join(f.message for f in self.blocks))


# ─────────────────────────────────────────────────────────────────────────────
# Launch-phase registry (group E). The [LAUNCH-PHASE] tag lives in system_config.yaml
# comments; we encode the INTENT here (the single machine-readable home) rather than
# parse comments. Add a row when a param is tagged [LAUNCH-PHASE] in the YAML.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _LaunchParam:
    path: str
    getter: Callable[[Any], Any]
    note: str


LAUNCH_PHASE_PARAMS: tuple[_LaunchParam, ...] = (
    _LaunchParam(
        path="trading_hours.entry_start",
        getter=lambda sc: sc.trading_hours.entry_start,
        note="conservative early-live start (avoids opening-hour volatility); "
             "relax toward 09:20 as capital/confidence grows",
    ),
)

# Stale-default guard registry (group F). Each row asserts a component constructor's
# DEFAULT still matches config intent — BUILD 1 aligned these; a future edit that
# changes the YAML but not the code default (or vice-versa) would make a component
# built without explicit config silently diverge.
_STALE_DEFAULT_GUARDS: tuple[tuple[str, str, str, Callable[[Any], Any]], ...] = (
    ("capital.position_sizer", "PositionSizer", "max_position_value_pct",
     lambda sc: sc.position_sizing.max_position_value_pct),
    ("capital.fund_manager", "FundManager", "daily_loss_limit_pct",
     lambda sc: sc.risk.daily_loss_limit_pct),
)


# ─────────────────────────────────────────────────────────────────────────────
# Group implementations — each returns a list[AuditFinding]
# ─────────────────────────────────────────────────────────────────────────────

def _group_a_contradictions(sc: Any, strategies: Optional[dict]) -> List[AuditFinding]:
    """Logically-contradictory configs. The hard ones BLOCK (fail-fast); the
    'valid but pointless' ones WARN."""
    out: List[AuditFinding] = []

    # A1 — BUILD 1 #10: force_intraday_only rewrites every strategy to INTRADAY (the
    # MIS-only guarantee); trade_type=DELIVERY then gates out every INTRADAY strategy
    # → 0 strategies could ever trade. Silent dead-system footgun → BLOCK.
    # NB: the message MUST contain "CONTRADICTORY CONFIG" (startup test contract).
    if getattr(sc, "force_intraday_only", False) and getattr(sc, "trade_type", None) == "DELIVERY":
        out.append(AuditFinding(
            "A", "A1_force_intraday_delivery", Severity.BLOCK,
            "CONTRADICTORY CONFIG: force_intraday_only=true rewrites all strategies "
            "to INTRADAY, but trade_type=DELIVERY blocks INTRADAY -> 0 strategies "
            "would trade. Set force_intraday_only=false to trade DELIVERY, or "
            "trade_type to INTRADAY/BOTH.",
        ))

    # A2 — belt-and-suspenders: trade_type out of domain. The Pydantic field
    # validator blocks this first; this only fires if the schema is bypassed.
    tt = getattr(sc, "trade_type", None)
    if tt not in ("INTRADAY", "DELIVERY", "BOTH"):
        out.append(AuditFinding(
            "A", "A2_trade_type_domain", Severity.BLOCK,
            f"CONTRADICTORY CONFIG: trade_type={tt!r} is not one of "
            "INTRADAY/DELIVERY/BOTH.",
        ))

    # A4 — SNR-V2 SINGLE SL OWNER (Q4(c)): structure_exit_enabled (system flag) makes the
    # StructureExitManager the sole owner of the SL leg; a strategy with
    # trailing_sl_enabled=true would ALSO advance that leg for its own trades → two SL
    # owners racing on one order. Only a LIVE contradiction when structure-exit is on AND
    # ≥1 ENABLED strategy trails → BLOCK. Needs strategies (skipped when absent, e.g. the
    # config-only startup subset — main.py re-runs group A WITH strategies as a construction
    # guard). NB: message contains "CONTRADICTORY CONFIG" (consistent with A1/A2).
    if strategies:
        _se = getattr(sc, "structure_exit", None)
        _se_on = getattr(_se, "structure_exit_enabled", False) is True if _se is not None else False
        if _se_on:
            _trailers = sorted(
                n for n, s in strategies.items()
                if getattr(s, "enabled", True) and getattr(s, "trailing_sl_enabled", False) is True
            )
            if _trailers:
                out.append(AuditFinding(
                    "A", "A4_structure_exit_trailing_sl", Severity.BLOCK,
                    "CONTRADICTORY CONFIG: structure_exit_enabled=true makes the "
                    "StructureExitManager the single SL owner, but these ENABLED strategies "
                    f"also trail their SL (trailing_sl_enabled=true): {', '.join(_trailers)}. "
                    "Two SL owners would race on one leg. Disable structure_exit, or set "
                    "trailing_sl_enabled=false on those strategies.",
                    metrics={"trailing_strategies": _trailers},
                ))

    # A5 — SLICE2.5 #16a (27-Jul-2026): the delivery capital foot-gun.
    #
    # MEASURED at capital/fund_manager.py:139-146. With
    # conditional_allocation_enabled FALSE the bucket split is pinned to the fixed
    # config values. If delivery is live and the ACTIVE book resolves to
    # DELIVERY-ONLY, the intraday bucket is never reservable -> that share of capital
    # is stranded, silently. Nothing errors; the system simply trades at the
    # positional bucket's size and looks like a sizing bug.
    #
    # WHY BLOCK AND NOT WARN — the precondition, not the severity of the outcome.
    # NI-12 (23-Aug-2026): this said delivery_enabled "has been false since the 15-Jun
    # incident", so the rule "CANNOT fire on an ordinary morning". Both halves are now
    # false — delivery_enabled is TRUE (:102) and delivery has traded, so this rule CAN
    # fire on an ordinary morning and the BLOCK severity is load-bearing, not theoretical,
    # minutes after they did it. A fail-fast whose precondition is a DELIBERATE ACT
    # costs a minute. One whose precondition is ENVIRONMENTAL costs a trading day --
    # that is S4, 17-Jul, and it is why the S4 self-check gets the opposite answer.
    #
    # ⚠️ CONDITIONED ON THE ACTIVE INTENT SET, NEVER ON THE FLAG PAIR. With
    # conditional_enabled TRUE *and both intents active* fund_manager returns the
    # SAME split as FALSE. So the naive rule ("delivery_enabled => require
    # conditional_allocation_enabled") would BLOCK a trade_type=BOTH book that is
    # behaviourally IDENTICAL -- and BOTH is the likeliest production configuration.
    # Uses strategy_will_trade, the same authority A3 already trusts, rather than
    # re-deriving which strategies are live.
    if strategies:
        try:
            from strategies.control import strategy_will_trade
            _cap = getattr(sc, "capital", None)
            _cond_on = bool(getattr(_cap, "conditional_allocation_enabled", False))
            if bool(getattr(sc, "delivery_enabled", False)) and not _cond_on:
                _fio = bool(getattr(sc, "force_intraday_only", False))
                _live = {
                    n: v for n, v in (
                        (n, strategy_will_trade(s, trade_type=tt, force_intraday_only=_fio))
                        for n, s in strategies.items()
                    ) if v.will_trade
                }
                # Verdict.product is the EFFECTIVE post-coercion product -- the one
                # that actually selects the capital bucket. (Verdict has no .intent.)
                if _live and all(v.product == "DELIVERY" for v in _live.values()):
                    _intra = getattr(_cap, "intraday_bucket_pct", None)
                    _share = f"{_intra:.0%}" if isinstance(_intra, (int, float)) else "the intraday"
                    out.append(AuditFinding(
                        "A", "A5_delivery_without_conditional_allocation", Severity.BLOCK,
                        "CONTRADICTORY CONFIG: delivery_enabled=true and every strategy that "
                        f"can trade is DELIVERY ({', '.join(sorted(_live))}), but "
                        "capital.conditional_allocation_enabled=false pins the fixed bucket "
                        f"split -- the intraday bucket is unreachable, so {_share} of capital "
                        "is stranded and every delivery position is sized off the remainder. "
                        "Set capital.conditional_allocation_enabled=true, or enable an "
                        "INTRADAY strategy, or set delivery_enabled=false.",
                        metrics={"delivery_only_strategies": sorted(_live),
                                 "intraday_bucket_pct": _intra},
                    ))
        except Exception:  # noqa: BLE001 — the probe must never break the audit
            pass

    # A3 — strategy-dependent: would ANY strategy trade today? trade_type +
    # force_intraday_only + per-strategy enabled/intent can combine to silence the
    # whole book (e.g. trade_type=DELIVERY with every delivery strategy disabled).
    # Valid but pointless → WARN (only when not already blocked above and strategies
    # are available).
    if strategies and not out:
        try:
            from strategies.control import strategy_will_trade
            fio = bool(getattr(sc, "force_intraday_only", False))
            will = [n for n, s in strategies.items()
                    if strategy_will_trade(s, trade_type=tt, force_intraday_only=fio).will_trade]
            if not will:
                out.append(AuditFinding(
                    "A", "A3_zero_strategies_trade", Severity.WARN,
                    f"trade_type={tt} + force_intraday_only={fio} + the current "
                    f"per-strategy enabled/intent leave 0 of {len(strategies)} "
                    "strategies able to trade — valid but the book is silent.",
                    metrics={"will_trade_count": 0, "strategy_count": len(strategies)},
                ))
        except Exception:  # noqa: BLE001 — strategy probe must never break the audit
            pass

    if not out:
        out.append(AuditFinding("A", "A_ok", Severity.PASS, "no contradictions"))
    return out


def _group_b_single_source(raw_system: Optional[dict],
                           raw_scoring: Optional[dict]) -> List[AuditFinding]:
    """Regression guards: a key BUILD 1 deleted reappearing in the YAML would
    resurrect a killed conflict. extra='forbid' already HARD-FAILS a reappearance
    that is not also re-added to the schema; this WARN catches the subtler case where
    a future dev re-adds it to BOTH yaml and schema. Needs the raw YAML (the model
    cannot see stripped keys), so it runs in the pre-flight context only."""
    out: List[AuditFinding] = []
    if raw_system is None:
        return out  # no raw yaml (startup path) → nothing to regression-guard

    cap = (raw_system.get("capital") or {})
    pos = (raw_system.get("position_sizing") or {})
    risk = (raw_system.get("risk") or {})

    if "daily_loss_limit" in cap:
        out.append(AuditFinding(
            "B", "B1_daily_loss_limit_resurrected", Severity.WARN,
            "capital.daily_loss_limit reappeared — BUILD 1 deleted it; "
            "daily_loss_limit_pct (risk:) is the SOLE daily-loss authority. "
            "Remove the absolute Rs key.",
        ))
    if "max_position_value_rs" in pos:
        out.append(AuditFinding(
            "B", "B2_max_position_value_rs_resurrected", Severity.WARN,
            "position_sizing.max_position_value_rs reappeared — BUILD 1 replaced it "
            "with the capital-relative max_position_value_pct. Remove the Rs key.",
        ))
    live_test = sorted(k for k in risk if str(k).startswith("live_test"))
    if live_test:
        out.append(AuditFinding(
            "B", "B3_live_test_mode_resurrected", Severity.WARN,
            f"risk.{{{', '.join(live_test)}}} reappeared — BUILD 1 deleted the "
            "live_test_* overrides; the base caps are the sole authority in both modes.",
        ))
    if raw_scoring is not None and "tier_multipliers" in (raw_scoring or {}):
        out.append(AuditFinding(
            "B", "B4_tier_multipliers_duplicated", Severity.WARN,
            "scoring_weights.yaml has tier_multipliers — they belong ONLY under "
            "system_config.position_sizing. Remove the scoring duplicate.",
        ))

    # A9 (V3 03.04): tier thresholds must satisfy high > medium > min_pass, for BOTH
    # the live thresholds and the v3 re-scale set. (config_loader also fail-fasts on
    # this; the auditor surfaces it on the raw YAML in pre-flight.)
    if raw_scoring is not None:
        for lo_key, mid_key, hi_key, label in (
            ("min_pass_score", "medium_score_threshold", "high_score_threshold", "live"),
            ("v3_min_pass_score", "v3_medium_score_threshold", "v3_high_score_threshold", "v3"),
        ):
            lo, mid, hi = raw_scoring.get(lo_key), raw_scoring.get(mid_key), raw_scoring.get(hi_key)
            if None not in (lo, mid, hi) and not (hi > mid > lo):
                out.append(AuditFinding(
                    "B", f"B5_scoring_threshold_ordering_{label}", Severity.BLOCK,
                    f"scoring {label} thresholds must satisfy high > medium > min_pass "
                    f"(got {hi}/{mid}/{lo}).",
                ))

    if not out:
        out.append(AuditFinding(
            "B", "B_ok", Severity.PASS,
            "no resurrected deleted keys (daily_loss_limit / max_position_value_rs / "
            "live_test_* / scoring tier_multipliers)"))
    return out


# NI-2 (22-Aug-2026). The sizer clamps the tiered quantity to twice the pre-multiplier
# quantity — `tiered_qty = max(1, min(tiered_qty, raw_qty * 2))` in
# capital/position_sizer.py — so no configured multiplier can push notional past 2×
# whatever the min(risk, capital, concentration) constraint allowed. Held here as a
# named constant rather than a bare 2 so the source of the number is stated.
# ⚠️ M3: it mirrors a literal in another file; if that clamp ever changes, this changes.
_SIZER_TIERED_QTY_HARD_CAP = 2.0


def _max_effective_multiplier(ps: Any) -> float:
    """The largest multiplier routine sizing can apply on top of the qty constraints.

    tier_mult × perf_weight, where perf_weight is bounded above by
    position_sizing.max_multiplier — the whole product then clamped by the sizer's own
    hard 2× ceiling. Derived from the config rather than hard-coded, so raising a tier
    weight or max_multiplier moves this check with it instead of leaving it stale.
    """
    tiers = getattr(ps, "tier_multipliers", None)
    weights = [float(getattr(tiers, name, 0.0) or 0.0) for name in ("HIGH", "MEDIUM", "LOW")]
    tier_ceiling = max([w for w in weights if w > 0.0], default=1.0)
    perf_ceiling = float(getattr(ps, "max_multiplier", 1.0) or 1.0)
    return min(tier_ceiling * perf_ceiling, _SIZER_TIERED_QTY_HARD_CAP)


def _group_c_capital_relative(sc: Any) -> List[AuditFinding]:
    """Capital-relative sanity. The bug-guard cap MUST be looser than the routine
    concentration cap, else it would bind on normal trades. pct values must sit in
    sane ranges (a 50% daily-loss limit is a config error, not a limit)."""
    out: List[AuditFinding] = []
    ps = sc.position_sizing
    rk = sc.risk

    # C1 — daily loss limit a sane fraction (schema allows up to 1.0; >10% is almost
    # certainly a typo for a daily limit).
    dll = rk.daily_loss_limit_pct
    if not (0 < dll <= 0.10):
        out.append(AuditFinding(
            "C", "C1_daily_loss_pct_range", Severity.WARN,
            f"daily_loss_limit_pct={dll:.1%} is outside the sane 0–10% band "
            "(a daily loss limit above ~10% of capital is likely a config error).",
            metrics={"daily_loss_limit_pct": dll}))

    # C2 — the ladder: the catastrophic-loss cap must stay LOOSER than the ROUTINE
    # ceiling on notional. The cap is a BACKSTOP; if it is <= what routine sizing can
    # produce it fires on ordinary trades instead of on an anomaly.
    #
    # NI-2 (22-Aug-2026): comparing max_position_value_pct against max_concentration_pct
    # DIRECTLY was wrong, because concentration is not the routine ceiling. The
    # tier/perf multiplier is applied AFTER the concentration constraint —
    # capital/position_sizer.py computes tiered_qty = floor(raw_qty * effective_mult)
    # and then caps it at raw_qty * 2 — so the routine ceiling is
    #     max_concentration_pct  ×  the largest multiplier sizing can produce.
    # Worked example the old check passed and should not have: conc 0.25 / posv 0.40
    # satisfies 0.40 > 0.25, while 0.25 × 2.0 = 0.50 > 0.40, i.e. the backstop binds on
    # ROUTINE sizing — the exact condition C2 exists to prevent.
    #
    # ⛔ This evaluates the multiplier; it does NOT change the multiplier's policy.
    # perf_weight is still unwired (main.py constructs SignalProcessor with no
    # perf_weights, so .get(name, 1.0) always returns 1.0), which is why the effective
    # ceiling is 1.0 in practice today. That is a separate fact and stays untouched:
    # this check must be right for the config as WRITTEN, not for today's accident.
    conc = ps.max_concentration_pct
    posv = ps.max_position_value_pct
    mult_ceiling = _max_effective_multiplier(ps)
    effective_conc = conc * mult_ceiling
    if posv <= effective_conc:
        out.append(AuditFinding(
            "C", "C2_position_cap_not_looser", Severity.WARN,
            f"max_position_value_pct ({posv:.0%}) must be LOOSER than the EFFECTIVE "
            f"routine ceiling max_concentration_pct × max effective multiplier "
            f"({conc:.0%} × {mult_ceiling:g} = {effective_conc:.0%}); as set the "
            "catastrophic-loss backstop would bind before routine concentration sizing.",
            metrics={"max_position_value_pct": posv, "max_concentration_pct": conc,
                     "max_effective_multiplier": mult_ceiling,
                     "effective_concentration_ceiling": effective_conc}))

    # C3 — risk per trade a sane fraction.
    rpt = ps.risk_per_trade_pct
    if not (0 < rpt <= 0.05):
        out.append(AuditFinding(
            "C", "C3_risk_per_trade_range", Severity.WARN,
            f"risk_per_trade_pct={rpt:.1%} is outside the sane 0–5% band.",
            metrics={"risk_per_trade_pct": rpt}))

    # C4 — risk per trade should not exceed the per-symbol concentration cap.
    if rpt > conc:
        out.append(AuditFinding(
            "C", "C4_risk_exceeds_concentration", Severity.WARN,
            f"risk_per_trade_pct ({rpt:.0%}) exceeds max_concentration_pct "
            f"({conc:.0%}) — a single trade's risk budget is larger than the "
            "per-symbol exposure cap.",
            metrics={"risk_per_trade_pct": rpt, "max_concentration_pct": conc}))

    # C5 — cumulative open risk vs the daily loss limit (was the BUILD 1 cross-field
    # warn). Only meaningful when the daily limit is not effectively disabled (100%).
    if dll < 1.0:
        max_cum = rpt * rk.max_open_positions
        if max_cum > dll * 2:
            out.append(AuditFinding(
                "C", "C5_cumulative_risk_vs_daily_loss", Severity.WARN,
                f"max_open_positions ({rk.max_open_positions}) x risk_per_trade_pct "
                f"({rpt:.1%}) = {max_cum:.1%} cumulative risk — exceeds 2x "
                f"daily_loss_limit_pct ({dll:.1%}).",
                metrics={"cumulative_risk": max_cum}))

    if not out:
        out.append(AuditFinding(
            "C", "C_ok", Severity.PASS,
            f"capital-relative caps sane (loss {dll:.0%} <= 10%, conc {conc:.0%} < "
            f"pos-cap {posv:.0%}, risk/trade {rpt:.0%})"))
    return out


def _group_d_active_overrides(sc: Any, known_symbols: Optional[set],
                              known_strategies: Optional[set]) -> List[AuditFinding]:
    """T3 visibility: list every NON-EMPTY slippage override so Rama SEES active
    overrides each morning (and never forgets a symbol override that has drifted
    from the global). Reuses orders.order_placer.validate_slippage_overrides for the
    typo/extreme WARNs (single source — not reimplemented)."""
    out: List[AuditFinding] = []
    try:
        ov = sc.entry_gate.slippage_control.overrides
    except Exception:  # noqa: BLE001
        return [AuditFinding("D", "D_unavailable", Severity.INFO,
                             "slippage overrides not present in this config")]

    maps = {
        "by_symbol": dict(getattr(ov, "by_symbol", {}) or {}),
        "by_strategy": dict(getattr(ov, "by_strategy", {}) or {}),
        "by_price_band": dict(getattr(ov, "by_price_band", {}) or {}),
    }
    active = {k: v for k, v in maps.items() if v}
    enabled = bool(getattr(ov, "enabled", False))

    if not active:
        out.append(AuditFinding(
            "D", "D_none", Severity.INFO,
            "no active slippage overrides — pure global fraction everywhere"))
    else:
        parts = [f"{k}={v}" for k, v in active.items()]
        state = "enabled" if enabled else "DISABLED (maps ignored)"
        out.append(AuditFinding(
            "D", "D_active", Severity.INFO,
            f"active slippage overrides [{state}]: " + "; ".join(parts),
            metrics={"active": active, "enabled": enabled}))

    # typo / extreme warnings (reused validator)
    try:
        from orders.order_placer import validate_slippage_overrides
        for w in validate_slippage_overrides(ov, known_symbols, known_strategies):
            out.append(AuditFinding("D", "D_override_warn", Severity.WARN, w))
    except Exception:  # noqa: BLE001 — validator import/use must never break the audit
        pass
    return out


def _group_e_launch_phase(sc: Any) -> List[AuditFinding]:
    """Surface [LAUNCH-PHASE]-tagged params with a review reminder (INFO)."""
    out: List[AuditFinding] = []
    for p in LAUNCH_PHASE_PARAMS:
        try:
            val = p.getter(sc)
        except Exception:  # noqa: BLE001
            continue
        out.append(AuditFinding(
            "E", f"E_{p.path.replace('.', '_')}", Severity.INFO,
            f"{p.path}={val} [LAUNCH-PHASE] — {p.note}",
            metrics={"path": p.path, "value": val}))
    if not out:
        out.append(AuditFinding("E", "E_none", Severity.INFO, "no launch-phase params"))
    return out


def _group_f_stale_default(sc: Any) -> List[AuditFinding]:
    """Guard that BUILD-1-aligned component constructor DEFAULTS still match config
    intent. A component built without explicit config (a default-arg call) that
    diverges from the YAML is a silent footgun → WARN. Deferred imports + fully
    guarded (introspection must never break the audit)."""
    import inspect

    out: List[AuditFinding] = []
    import importlib
    for module_path, cls_name, param, getter in _STALE_DEFAULT_GUARDS:
        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, cls_name)
            default = inspect.signature(cls.__init__).parameters[param].default
            cfg_val = getter(sc)
        except Exception:  # noqa: BLE001 — never crash on a refactor / missing attr
            continue
        if default is inspect.Parameter.empty:
            continue
        if default != cfg_val:
            out.append(AuditFinding(
                "F", f"F_{cls_name}_{param}", Severity.WARN,
                f"{cls_name}.__init__ default {param}={default} diverges from config "
                f"{param}={cfg_val} — a {cls_name} built without explicit config "
                "would use the stale default.",
                metrics={"component": cls_name, "param": param,
                         "default": default, "config": cfg_val}))
    if not out:
        out.append(AuditFinding(
            "F", "F_ok", Severity.PASS,
            "component defaults match config intent (position-value cap + daily-loss pct)"))
    return out


def _group_g_cross_field(sc: Any, strategies: Optional[dict]) -> List[AuditFinding]:
    """Cross-field sanity that no single-field validator can catch (operator typos)."""
    from datetime import time as _time

    def _hhmm(s: str) -> _time:
        h, m = str(s).split(":")
        return _time(int(h), int(m))

    out: List[AuditFinding] = []
    th = sc.trading_hours

    # G1 — entry window has time to work before square-off.
    entry_end = _hhmm(th.entry_end)
    eod = _hhmm(th.eod_squareoff_time)
    gap = (eod.hour * 60 + eod.minute) - (entry_end.hour * 60 + entry_end.minute)
    if gap < 15:
        out.append(AuditFinding(
            "G", "G1_entry_end_near_squareoff", Severity.WARN,
            f"entry_end ({th.entry_end}) is within 15min of eod_squareoff_time "
            f"({th.eod_squareoff_time}) - trades may not have time to hit targets",
            metrics={"gap_minutes": gap}))

    # G2 — leverage sanity.
    lev = sc.capital.leverage_map
    for intent, value in (("INTRADAY", lev.INTRADAY), ("COVER_ORDER", lev.COVER_ORDER)):
        if value > 10:
            out.append(AuditFinding(
                "G", f"G2_leverage_{intent}", Severity.WARN,
                f"leverage_map.{intent} = {value}x seems high - verify this matches "
                "your broker's actual margin",
                metrics={"intent": intent, "leverage": value}))

    # G3 — micro tick size.
    mts = sc.position_sizing.min_tick_size
    if mts < 0.01:
        out.append(AuditFinding(
            "G", "G3_min_tick_size", Severity.WARN,
            f"min_tick_size ({mts}) < 0.01 - may allow micro-fraction SL distances",
            metrics={"min_tick_size": mts}))

    # G5 — per-strategy entry window must have a USABLE OVERLAP with the global entry
    # envelope. FIX M-K2 (24-Jul): the fields are entry_start_time / entry_end_time
    # (strategies/schema.py) — the old entry_start / entry_end never existed on a
    # StrategyConfig, so getattr returned None and this check NEVER fired once.
    #
    # Calibration (24-Jul, per the "stays quiet when it should" requirement): warn ONLY
    # when the strategy window has no overlap with the global window — i.e. the strategy
    # can NEVER enter a trade (starts at/after the global close, or ends at/before the
    # global open). A window that merely extends BEYOND the global (starts earlier and/or
    # ends later) is harmlessly GATED by the global envelope — the intended launch-phase
    # posture (16 strategies declare 09:25 while the global gates to 10:00; the 04-Jul
    # audit classed it "cosmetic"). Warning on it would re-create a daily false WARN in
    # the 09:20 email's G row — the exact class this auditor exists to avoid.
    #
    # ORIGINAL condition (verbatim — it NEVER executed, so this comment is the only
    # record of its intended behaviour): it read getattr(s, "entry_start")/"entry_end"
    # (always None) and would have warned on CONTAINMENT violations:
    #     if entry_start < global entry_start:  WARN "strategy start before global start"
    #     if entry_end   > global entry_end:    WARN "strategy end after global end"
    # i.e. it flagged ANY strategy window extending BEYOND the global envelope.
    #
    # NARROWING — M-K2 is STRICTLY NARROWER than that original. It warns only on a
    # window with NO overlap (can-never-trade). NEWLY SILENT (cases the old intent would
    # have flagged and this deliberately does not): (a) a strategy STARTING earlier than
    # the global start (e.g. 09:25 vs 10:00); (b) a strategy ENDING later than the global
    # end (e.g. 15:30 vs 15:00). Both are harmlessly clamped by the global envelope, so
    # they are NOT config errors — suppressing them is the point.
    #
    # KNOWN LIMITATION (do NOT "fix" with a threshold): "usable overlap" is BINARY. A
    # window overlapping the global by only a few minutes (e.g. 14:55-15:30 vs 10:00-15:00
    # → 5 min) is effectively crippled but technically overlaps, so G5 stays quiet. A
    # minimum-overlap-minutes rule is a tunable nobody has a principled value for = a
    # future false signal; this edge is NAMED here rather than guarded.
    if strategies:
        g_start = _hhmm(th.entry_start)
        g_end = _hhmm(th.entry_end)
        for name, s in strategies.items():
            ss = getattr(s, "entry_start_time", None)
            se = getattr(s, "entry_end_time", None)
            try:
                if ss is None or se is None:
                    continue
                if _hhmm(ss) >= g_end or _hhmm(se) <= g_start:
                    out.append(AuditFinding(
                        "G", f"G5_window_{name}", Severity.WARN,
                        f"strategy {name} entry window ({ss}-{se}) has NO overlap with "
                        f"the global entry window ({th.entry_start}-{th.entry_end}) - it "
                        f"can never enter a trade"))
            except Exception:  # noqa: BLE001
                continue

    if not out:
        out.append(AuditFinding("G", "G_ok", Severity.PASS, "cross-field timing/leverage/tick sane"))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def audit(
    system: Any,
    *,
    raw_system_yaml: Optional[dict] = None,
    raw_scoring_yaml: Optional[dict] = None,
    strategies: Optional[dict] = None,
    known_symbols: Optional[set] = None,
    known_strategies: Optional[set] = None,
    capital: Optional[float] = None,   # reserved: ₹ rendering by callers
    groups: str = GROUP_ORDER,
) -> ConfigAuditReport:
    """Run the requested check groups against a SystemConfig (duck-typed).

    `groups` is a string of group letters to run (default all A-G). Callers with
    limited context pass a subset (the startup path runs ACEG; pre-flight runs all).
    """
    findings: List[AuditFinding] = []
    if "A" in groups:
        findings += _group_a_contradictions(system, strategies)
    if "B" in groups:
        findings += _group_b_single_source(raw_system_yaml, raw_scoring_yaml)
    if "C" in groups:
        findings += _group_c_capital_relative(system)
    if "D" in groups:
        findings += _group_d_active_overrides(system, known_symbols, known_strategies)
    if "E" in groups:
        findings += _group_e_launch_phase(system)
    if "F" in groups:
        findings += _group_f_stale_default(system)
    if "G" in groups:
        findings += _group_g_cross_field(system, strategies)
    return ConfigAuditReport(findings=findings)


# Startup subset: the groups computable from the SystemConfig alone, with no external
# context and no heavy imports. A=contradictions (BLOCK gate), C=capital-relative
# (WARN), G=cross-field (WARN). B/D/F need pre-flight context; E (launch-phase
# reminders) is a periodic nudge that belongs in the 09:20 pre-flight email, not every
# process boot — so the startup boot-log stays exactly as it is today.
STARTUP_GROUPS = "ACG"


def audit_system_config(system: Any, *, groups: str = STARTUP_GROUPS) -> ConfigAuditReport:
    """The startup-path entry point. Called by SystemConfig._cross_field_sanity_checks
    on every load_all(); pure + cheap. The caller logs the WARN/INFO findings and
    calls raise_if_blocked()."""
    return audit(system, groups=groups)


def audit_app_config(
    app_config: Any,
    *,
    config_dir: Optional[Any] = None,
    strategies: Optional[dict] = None,
    known_symbols: Optional[set] = None,
    capital: Optional[float] = None,
    groups: str = GROUP_ORDER,
) -> ConfigAuditReport:
    """The pre-flight entry point. Pulls the SystemConfig from a loaded AppConfig and
    (when config_dir is given) the raw YAML needed by the single-source regression
    guards (group B). `strategies` keys feed the override typo-check + group A3."""
    raw_system = raw_scoring = None
    if config_dir is not None:
        raw_system = _safe_load_yaml(config_dir, "system_config.yaml")
        raw_scoring = _safe_load_yaml(config_dir, "scoring_weights.yaml")
    known_strategies = set(strategies.keys()) if strategies else None
    return audit(
        app_config.system,
        raw_system_yaml=raw_system,
        raw_scoring_yaml=raw_scoring,
        strategies=strategies,
        known_symbols=known_symbols,
        known_strategies=known_strategies,
        capital=capital,
        groups=groups,
    )


def _safe_load_yaml(config_dir: Any, filename: str) -> Optional[dict]:
    try:
        import yaml
        from pathlib import Path
        path = Path(config_dir) / filename
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 — raw read is best-effort; group B simply skips
        return None
