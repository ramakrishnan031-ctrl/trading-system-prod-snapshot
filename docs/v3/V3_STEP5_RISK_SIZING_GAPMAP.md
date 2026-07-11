# V3 STEP 5 — 03.06 RISK & POSITION-SIZING: GAP-MAP + REVIEW ITEMS

**Date (IST):** 12-Jul-2026 · **Type:** ADAPT — verify + minimal inert scaffold. · **LIVE sizing byte-identical** (proven). · **For:** Web Claude + ChatGPT + Rama architectural sign-off.

**Headline:** 03.06 is essentially COMPLETE in the existing `position_sizer` / `risk_engine` / `fund_manager`. The only genuine gap was delivery-specific *sizing* knobs — scaffolded INERT this step. Nothing else needed closing now. Two fail-OPEN cases + the ATR-into-live-SL decision are flagged for architectural review (not changed unilaterally).

---

## T1 — GAP-MAP (03.06 requirement | existing implementation + path | present/GAP)

| 03.06 requirement | Existing implementation (source) | Status |
|---|---|---|
| **Risk-based sizing from an SL DISTANCE** | `capital/position_sizer.py::calculate` — `sl_distance = abs(entry_price - sl_price)` (L301); `qty_by_risk = floor(risk_rs / sl_distance)` (L334). SL comes in as the `sl_price` **parameter** (L169) | **PRESENT** |
| **min(risk, capital, concentration)** | `position_sizer.py:380` `raw_qty = min(qty_by_risk, qty_by_capital, qty_by_concentration)` | **PRESENT** |
| **Tier ON/OFF + multipliers** | `enabled` ctor flag (L136); `tier_multipliers` HIGH 1.0 / MEDIUM 0.70 / LOW 0.50 (L56); OFF → flat Rs/order, score-neutral (L428) | **PRESENT** |
| **Per-pipeline (bucket) caps** | `fund_manager.py` intraday 70% / positional 30% ABSOLUTE split, `reserve()` consults only the intent's bucket, no cross-bucket borrow; `resolve_bucket_allocation` (L111). Delivery COUNT caps in `risk_engine` (`max_open_delivery_positions 3` / `max_daily_delivery_trades 5`, enforced only `bucket=="positional"`). Per-strategy `max_concurrent_positions` in `signal_processor` | **PRESENT** |
| **Daily risk limit** | `risk_engine.py` DAILY_LOSS (3% of capital, L509-545) + CONSECUTIVE_LOSSES (max 4, L493) + DAILY_TRADES (max 10) | **PRESENT** |
| **Fail-CLOSED** | sizing-invalid / capital / count gates all REJECT. **Two deliberate fail-OPEN** (T5a): unknown/failed sector → UNKNOWN+admit (`_resolve_sector` L614); `kill_switch=None` → KILL_SWITCH skipped+WARN (L167/L343) | **PRESENT (2 flagged)** |
| **Catastrophic position-value cap** | `position_sizer` `max_position_value_pct 0.40` REJECT (L476) | **PRESENT** |
| **Concentration (per-trade)** | `qty_by_concentration = floor(total_capital * 0.10 / entry_price)` (L375) | **PRESENT** |
| **Delivery leverage** | `capital.leverage_map` `DELIVERY: 1.0` | **PRESENT** |
| **Delivery risk_pct (per-bucket)** | — (sizer used ONE global `risk_per_trade_pct`) | **GAP → scaffolded INERT (T3)** |
| **Delivery max_position_value (per-bucket)** | — (sizer used ONE global `max_position_value_pct`) | **GAP → scaffolded INERT (T3)** |
| **ATR-based SL for sizing** | `sl_price` param accepts ANY source; LIVE derives SL via FIXED_PCT today; `core/candle_math.atr` now exists but is **NOT wired** to live SL | **SEAM PRESENT; ATR-wiring = T5b review** |

## T2 — SL-DISTANCE SEAM (no live change)

Confirmed: the sizer's risk arm sizes from `sl_distance = abs(entry_price - sl_price)`, and `sl_price` is a plain **input parameter**. The V3 playbook path can feed 03.03's S&R-derived `computed_sl` as `sl_price` LATER **with no sizer change** — the seam already exists. This step did NOT change how the LIVE path derives its SL (still the strategy FIXED_PCT).

## T3 — DELIVERY SCAFFOLD (INERT) — the one change made

Added two OPTIONAL, default-`None` delivery-scoped sizing knobs, reusing the EXISTING sizer (no parallel/delivery sizer):
- `position_sizing.delivery_risk_per_trade_pct` + `delivery_max_position_value_pct` (`config_loader.PositionSizingConfig`, validated `(0,1]`; `system_config.yaml` = `null`).
- `position_sizer.calculate` computes `eff_risk_pct` / `eff_max_position_value_pct` = the delivery value **only when** `bucket=="positional"` AND the knob is set, else the global value.
- Wired through `main.py`.

**Provably INERT / byte-identical:** default `None` → global used everywhere; and the live bucket is **never** `positional` (delivery double-locked OFF + `force_intraday_only` coerces every entry to INTRADAY), so even a set knob never touches live sizing. Delivery count caps (3/5), leverage (1.0), and the 30% positional bucket already existed — this only adds the missing per-bucket *risk / position-value* knobs the future V3 delivery path needs. **Delivery NOT activated.**

## What changed + why · G1 byte-identity

- Files: `capital/position_sizer.py` (+2 optional ctor params, +`eff_*`, 2 substitutions), `core/config_loader.py` (+2 fields +validator), `config/system_config.yaml` (+2 `null` keys), `main.py` (+2 kwargs), `tests/unit/test_position_sizer_delivery_scaffold.py` (NEW, 4 tests). **No `risk_engine` / `fund_manager` code change** (they were already complete).
- **G1:** `test_default_none_is_byte_identical` (None knobs → identical qty/margin/risk/constraint) + `test_delivery_knob_set_but_intraday_uses_global` (knob set but INTRADAY → global) prove the live sizing path is unchanged. Existing sizer/risk/fund_manager tests pass unchanged.

## T5 — REVIEW ITEMS (architectural decisions — NOT changed unilaterally)

### T5a-i · `kill_switch=None` → KILL_SWITCH check skipped (fail-OPEN)
- **Today:** `RiskEngine(kill_switch=None)` skips the KILL_SWITCH gate + logs a WARN once (RE8, L167/L343). Production `main.py` ALWAYS injects a real kill_switch, and `order_placer` re-checks it (last-mile), so live is protected.
- **If flipped to runtime fail-CLOSED** (reject-all when `kill_switch is None`): **blast radius = LARGE on tests** (many build RiskEngine without a kill_switch) and it could halt trading on a transient construction issue.
- **Recommendation:** do NOT flip the runtime gate. Instead add a **boot-time fail-fast** in `main.py` (live mode) asserting `kill_switch` is injected — fail-CLOSED at the right layer (startup) without breaking the runtime gate's ergonomics. Decision requested.

### T5a-ii · sector lookup failure → "UNKNOWN" + admit (fail-OPEN)
- **Today:** `_resolve_sector` returns "UNKNOWN" on exception/empty and admits (RE9, L614). All UNKNOWN symbols share ONE "UNKNOWN" sector bucket.
- **Nuance:** sharing a bucket is CONSERVATIVE on the 40% sector cap (unknowns compete for one bucket → *less* exposure allowed), so this fail-OPEN is not a concentration-blowout — it is a mis-attribution, not a loosening.
- **If flipped to fail-CLOSED** (reject on unknown sector): would reject any symbol missing from the sector map → could halt legitimate trades on an instrument-cache data-gap (over-restrictive).
- **Recommendation:** KEEP fail-OPEN (don't block trading on a data-gap; the bucket is conservative); optionally improve sector-map coverage. Low priority. Decision requested.

### T5b · Wire the new ATR helper into LIVE SL sizing?
- **Today:** LIVE SL = strategy FIXED_PCT; `core/candle_math.atr` exists but is unwired. The sizer sizes from whatever `sl_price` it's given.
- **If wired into LIVE SL derivation:** every intraday trade's SL distance changes → `qty_by_risk` changes → **LIVE position sizes change** (also risk_amount, reservation, R:R). **Blast radius = LARGE (money path).**
- **Recommendation:** do NOT wire ATR into LIVE SL now. For the V3 (shadow) path, ATR/S&R-based SL IS the intent — feed it via the `sl_price` seam on the V3 playbook path and prove parity (Step-4b style) before any live flip. Do NOT swap silently. Decision requested.

## T4 — genuine gaps closed

Only the delivery sizing scaffold (T3) — everything else in 03.06 is already implemented. No other additive change was needed now.

## Acceptance

- **G1:** live sizing byte-identical (scaffold inert; proven by test).
- **G2:** targeted regression GREEN — sizer/risk/fund_manager 174 pass (incl. the 4 new scaffold tests), config_loader/config_auditor 79 pass. **Full-suite CONFIRMATORY run (12-Jul, dirty tree): 4494 pass / 12 fail / 15 skip.** Clean-vs-dirty stash-diff (all Steps 1-5 tracked mods stashed, same 12 node-ids re-run): **12 fail on clean == 12 fail on dirty → my changes add ZERO new failures.** The 12 are the known PC-env baseline (test_main/order_placer_fix061/phase17/state_store/fix181/interactive_startup — deterministic on Windows, green on VM), untouched by Step 5.
- **G3:** this gap-map + the T5 review items delivered for sign-off.

**No live behaviour changed; delivery NOT activated; the two fail-OPEN cases + ATR-into-live-SL are flagged, not changed.** BUILT-but-UNPUSHED (with Steps 1-4b). NEXT after review: Step 6 (03.05 Portfolio Allocator — the NEW ranked-admission core).
