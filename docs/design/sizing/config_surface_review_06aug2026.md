# THE CONFIG SURFACE REVIEW — WHAT CAN BE ISOLATED FOR DELIVERY

**06-Aug-2026 · Step 1c · third of `docs/design/sizing/`**
⛔ **MEASUREMENT + CLASSIFICATION ONLY. No design · no proposed value · no proposed key · nothing changed.**
Citations at deployed SHA `0197923`. Production read ~15:2x IST.

> ## 🔴 HEADLINE — **THE ISOLATION IS BETTER THAN ASSUMED IN ONE PLACE AND WORSE IN ANOTHER**
> ✅ **Rama's own example is REFUTED by the source.** *"Max trades per day = 6 is consumed by BOTH
> pipelines"* — **it is not.** Both count caps are a clean `if/else` on the bucket: a delivery entry
> checks **only** the delivery cap, an intraday entry **only** the global one.
> ⭐ **And the code distinguishes deliberate sharing from accidental sharing, in writing** —
> `max_consecutive_losses` carries *"deliberately SHARED — no delivery variant"* at the check itself.
> 🔴 **What IS shared and undeclared: the two levers that actually bind.** Concentration and the tier
> multiplier have no delivery control at all, and between them they decide every quantity the system
> has ever traded.

---

## §A.1 · SCOPE — the include/exclude rule, and the count

**Corpus:** `config/system_config.yaml` — **301 leaf keys** across **42 sections**, by YAML parse.
*(The item-4 §2 indentation-walk recorded 302. ⛔ The 1-key difference is not chased here.)*

**INCLUDE** a key if changing it can alter a trade's **existence · size · execution · lifecycle ·
alerting**. **EXCLUDE** infrastructure, transport, paths, logging, DB, ports, and **signal-generation
internals**.

| excluded family | keys | why |
|---|---|---|
| `sr_detector` · `regime` · `v3_chain` · `watchlist` | **98** | ⭐ **signal GENERATION, not trade SHAPE.** They decide *whether a signal exists*, not how it is sized or carried. ⚠️ **G2 lives here** — named so the exclusion is not read as "irrelevant" |
| `broker` · `clock` · `webhook` · `logging` · `live_feed` · `paper` · `eod_cleanup` · `shadow_tracker` · `scanner_check_delay_sec` | ~90 | transport, mode, infra |

⇒ **IN SCOPE: 113 keys across 25 families.** ⭐ Ordered below **by what binds**, not by the YAML.

---

## §A.4 · 🔴 THE SHARED-KEY LIST — **read this first; it IS the minimum delivery surface**

### ✅ ALREADY ISOLATED — verified at source, ⛔ not assumed

| key pair | isolation mechanism | cite |
|---|---|---|
| `risk.max_open_positions` / `risk.max_open_delivery_positions` | **`if bucket=="positional": …delivery cap… else: …global cap…`** | `risk_engine.py:468-475` |
| `risk.max_daily_trades` / `risk.max_daily_delivery_trades` | same `if/else` shape | `risk_engine.py:552-571` |
| `position_sizing.risk_per_trade_pct` / `delivery_risk_per_trade_pct` | `eff_risk_pct` selection | `position_sizer.py:300-303` |
| `position_sizing.max_position_value_pct` / `delivery_max_position_value_pct` | `eff_max_position_value_pct` | `position_sizer.py:305-308` |
| `capital.leverage_map` | keyed per intent — `INTRADAY 5.0 · DELIVERY 1.0` | `position_sizer.py:313` |
| `capital.intraday_bucket_pct` / `positional_bucket_pct` | the 70/30 split itself | — |

> ⛔ **Rama's example is refuted.** The count caps are the *best*-isolated things in the system.

### ⭐ DELIBERATELY SHARED — the code says so at the check

| key | the code's own words |
|---|---|
| `risk.max_consecutive_losses` | *"SLICE2.5-PHASE-3 (A): **deliberately SHARED** — no delivery variant. The streak breaker is a portfolio-wide circuit and applies to delivery entries too."* `risk_engine.py:574-575` |

⭐ **This row is the template.** A shared key with a written reason is a decision. Everything below is
shared **without** one.

### 🔴 SHARED AND UNDECLARED — **every one is an isolation defect by definition**

| # | key | pipeline today | 🔴 binds? | why it matters |
|---|---|---|---|---|
| **1** | **`position_sizing.max_concentration_pct`** | **BOTH** | 🔴 **483/483** | ⭐⭐ **the ONLY cap that has ever decided a quantity.** No delivery twin exists — `:424` reads the global raw while `:381` reads the effective |
| **2** | **`position_sizing.tier_multipliers`** | **BOTH** | 🔴 **372/483** | ⭐⭐ halves every position it touches. **No delivery twin. No `delivery_tier` key anywhere** |
| **3** | `risk.daily_loss_limit_pct` | **BOTH** | armed | 3 % of **TOTAL**, not of the bucket ⇒ a delivery loss soft-kills the intraday day *(register H1)* |
| 4 | `risk.max_sector_exposure_pct` | BOTH | `observe` mode | no delivery variant; sector cap is portfolio-wide |
| 5 | `risk.one_trade_per_symbol_direction_per_day` | BOTH | yes | *(register H5)* a held CNC blocks intraday on that symbol, reported as `DUPLICATE_SYMBOL` |
| 6 | `trading_hours.entry_start` / `entry_end` | BOTH | yes | **10:00–15:00 is an intraday shape.** A delivery entry window is a different concept |
| 7 | `trading_hours.eod_entry_cutoff` | BOTH | yes | 15:15 — intraday-shaped |
| 8 | `signal_processor.per_symbol_cooldown_sec` · `min_gap_between_entries_sec` · `entry_burst_*` | BOTH | yes | seconds-scale throttles on a days-scale pipeline |
| 9 | all 9 `entry_gate.*` | BOTH | yes | slippage/spread/depth tuned for intraday fills |
| 10 | `strategy_circuit_breaker.*` (4) | BOTH | yes | `cutoff_time: 12:00` is an intraday clock |
| 11 | `circuit_breaker.force_close_time` / `partial_fill_timeout_minutes` | BOTH | yes | ⭐ **the 5-minute partial-fill timeout is the "10-minute GTT timeout" family** |
| 12 | `order_reconciler.capital_drift_tolerance*` | BOTH | 🔴 firing | already registered — an intraday-leverage calibration |
| 13 | `capital.slm_margin_buffer_pct` | BOTH | yes | 5 % SL-Market buffer applied to CNC, **which has no SL-Market order** |
| 14 | `drift_handler.*` thresholds (4) | BOTH | latent | absolute rupee tiers, no pipeline dimension |
| 15 | `alerts.*` (9) | BOTH | yes | ⭐ Rama's separate-vocabulary decision lands here |

> ### ⭐⭐ §A.5 — **THE ORDERING IS THE FINDING**
> **The two keys that bind (#1 concentration, #2 tier) have NO delivery control.**
> **The two that have delivery twins (`risk_per_trade_pct`, `max_position_value_pct`) have NEVER
> bound — see §A.3.**
> ⇒ ⛔ **A review that mirrored the existing surface would reproduce exactly the wrong two knobs.**

---

## §A.3 · 🔴 WOULD CHANGING THIS KEY CHANGE ANY TRADE TODAY? — **measured**

**(P)** `signals` table, every `REJECTED_SIZING_*` status ever written:

| status | count |
|---|---|
| `REJECTED_SIZING_CONCENTRATION` | **3,468** |
| `REJECTED_SIZING_CAPITAL` | **20** |
| *(no other `REJECTED_SIZING_*` status exists)* | — |

| key | binds today? | evidence |
|---|---|---|
| `max_concentration_pct` | ✅ **YES — always** | `binding_constraint='concentration'` on **483/483** trades + 3,468 rejections |
| `tier_multipliers` | ✅ **YES — 372/483** | halves; cancelled by the floor on the other 111 |
| `leverage_map` | ✅ yes, indirectly | the only rung on purchasing power; 20 `CAPITAL` rejections |
| `min_qty_threshold` · `lot_skew_*` | ✅ yes | lot floor reached routinely at these sizes |
| `risk_per_trade_pct` | 🔴 **NO** | zero `REJECTED_SIZING_RISK` ever; `position_sizer.py:434` — *"algebraically never today"* |
| **`max_position_value_pct`** | 🔴 **NO — has NEVER rejected a trade** | **zero** rejections in the whole `signals` table |
| **`delivery_risk_per_trade_pct`** | 🔴 **NO — `null`, and its parent never binds** | doubly inert |
| **`delivery_max_position_value_pct`** | 🔴 **NO — `null`, and its parent never binds** | doubly inert |
| `min_multiplier` / `max_multiplier` | 🔴 **NO** | `perf_weight = 1.0` on 483/483 |
| `dynamic_by_winrate` | 🏷️ **(d)** | true, but the weight has never moved off 1.0 |
| `max_single_order_qty` (10,000) | 🔴 NO | max observed `qty_by_risk` = 123 |
| `min_tick_size` | 🔴 NO | no `INVALID_SL_DISTANCE` observed |
| `portfolio_allocator.*` (7) | 🔴 **NO** | `allocator_mode: shadow` |
| `max_portfolio_deployment_pct` · `long_short_skew_max` | 🔴 NO | both `null` |
| `structure_exit.*` (6) | 🔴 NO | `structure_exit_enabled: false` |
| `smart_tgt.*` (5) | 🔴 NO | never registered — `smart_tgt_state` 0 rows |
| `mis_filter.shadow: true` | 🔴 NO | shadow mode |
| `sector_cap_mode: observe` | 🔴 NO | observe mode |
| `check1_mid_fill_defer_sec: 0.0` | 🔴 NO | 0.0 = OFF |
| `auto_resume_kill_switch` | 🔴 **NO — dead knob** | already registered IA-P7-04 |

> ### 🔴 **AT LEAST 17 IN-SCOPE KEYS CANNOT CHANGE ANY OUTCOME TODAY.**
> ⭐⭐ **And four of them are the delivery-specific ones plus their parents** — the existing delivery
> surface is **inert twice over**: the twins are `null`, *and* the caps they would override have never
> bound. ⛔ **Adding more twins of non-binding caps produces more inert knobs.**

---

## §A.2 / §A.6 · CLASSIFICATION — ordered by what binds

| key | controls | pipeline | twin? | binds | **classification** | why |
|---|---|---|---|---|---|---|
| `max_concentration_pct` | max % of capital in one stock | BOTH | ❌ | 🔴 483/483 | **transfers with DIFFERENT SEMANTICS** | intraday concentration is a same-day risk; a delivery position is concentrated **for days** |
| `tier_multipliers` | size × grade | BOTH | ❌ | 🔴 372/483 | **transfers with DIFFERENT SEMANTICS** | a grade earned on intraday scoring is applied to a multi-day hold |
| `leverage_map` | margin divisor | per-intent | ✅ | ✅ | **transfers as-is** | already keyed per intent |
| `daily_loss_limit_pct` | day's loss budget | BOTH | ❌ | armed | **DIFFERENT SEMANTICS** | ⭐ a delivery P&L lands on its **exit** day, dumping a multi-day result into one day's budget |
| `entry_start` / `entry_end` | when entries may fire | BOTH | ❌ | ✅ | **DIFFERENT SEMANTICS** | ⭐⭐ **the inverse case §A.6 warns about** — not "does not apply"; delivery's analogue is a **max-carry in DAYS**, same concept, different unit |
| `eod_entry_cutoff` 15:15 | last entry | BOTH | ❌ | ✅ | **DIFFERENT SEMANTICS** | delivery's analogue is a carry deadline, not a clock |
| `circuit_breaker.partial_fill_timeout_minutes` | abandon a partial | BOTH | ❌ | ✅ | **DOES NOT APPLY** | ⭐ same family as the 10-min GTT timeout — minutes-scale abandonment on a days-scale hold |
| `eod_squareoff.*` (6) | 15:17 flatten | intraday | n/a | ✅ | **DOES NOT APPLY** | CNC is exempt by design (EOD6) |
| `per_symbol_cooldown_sec` 300 | re-entry throttle | BOTH | ❌ | ✅ | **DIFFERENT SEMANTICS** | 5 minutes vs a position held for days |
| `entry_gate.*` (9) | fill quality | BOTH | ❌ | ✅ | **transfers as-is** | ⚠️ values tuned for intraday, but the concept is identical |
| `slm_margin_buffer_pct` | 5 % reserve buffer | BOTH | ❌ | ✅ | **DOES NOT APPLY** | there is no SL-Market order on the CNC path |
| `max_consecutive_losses` | streak breaker | BOTH | ❌ | ✅ | **transfers as-is** | ⭐ deliberately shared, reason recorded |
| `capital_drift_tolerance*` | drift band | BOTH | ❌ | 🔴 firing | **DIFFERENT SEMANTICS** | already registered — the 10 % band is a leverage calibration |
| `risk_per_trade_pct` (+twin) | risk per trade | BOTH | ✅ | 🔴 no | **transfers as-is** | twin exists, both inert |
| `max_position_value_pct` (+twin) | catastrophic veto | BOTH | ✅ | 🔴 **never** | **transfers as-is** | twin exists, never fired |
| `alerts.*` (9) | notification | BOTH | ❌ | ✅ | **NEW, DELIVERY-ONLY** | Rama's separate-vocabulary ruling |

## §A.7 · RAMA'S DECIDED ITEMS — as rows. **All classify `new, delivery-only`.**

| # | decision | key exists? | classification | depends on |
|---|---|---|---|---|
| 1 | separate Telegram vocabulary for delivery | ❌ | **new, delivery-only** | the `alerts.*` family is shared today |
| 2 | remove the 10-minute GTT timeout | ❌ *(no such key found in scope)* | **does not apply** — delete rather than twin | intraday-shaped |
| 3 | no EOD reconcile failure on carry | ❌ | **new, delivery-only** | `reconcile_positions` reads `positions()` only (F1) |
| 4 | previous-day positions in today's book | ❌ | **new, delivery-only** | the T+1 `holdings()` transition |
| 5 | **max carry-days + countdown alert** | ❌ | **new, delivery-only** | ⭐ **no holding-period or max-hold key exists anywhere in config** |
| 6 | **capital-starvation alert** | ❌ | **new, delivery-only** | ⭐ the sizer already computes `constraint` + `reason` + `breakdown` at every rejection **and discards them** |

---

## §B · THE EXPECTANCY SPLIT — **`cost_R` is essentially FLAT**

**(P)** 220 closed trades carrying both `net_pnl` and `risk_amount > 0`:

| bucket | n | avg net P&L | avg charges | avg risk ₹ | **`cost_R`** | **expectancy R** |
|---|---|---|---|---|---|---|
| **qty = 1** | 134 | −0.389 | 0.485 | 6.492 | **0.0836** | **−0.0587** |
| **qty = 2** | 61 | −0.884 | 0.374 | 4.840 | **0.0849** | **−0.2553** |
| **qty ≥ 3** | 25 | +0.488 | 0.338 | 5.550 | **0.0689** | **−0.092** |

### §B.2 / §B.3 — scored against the pre-stated outcomes

✅ **OUTCOME 1 HOLDS: `cost_R` is flat across buckets** — 0.0836 / 0.0849 / **0.0689**, a spread of
**0.016 R**. **The register's size-independence result survives at these sizes.**
⇒ **The FIX-133 rescue is economically ~free, and the taxonomy proposal loses its last cost.**

🔴 **OUTCOME 2 IS REFUTED, and in the opposite direction to the hypothesis.** `qty = 1` is **not** the
worst-expectancy cohort — it is **the best of the three** (−0.0587 R vs −0.2553 and −0.092).
⛔ **FIX-133 is not manufacturing the worst cohort in the book.** ⭐ Recorded as the result that was
*not* preferred, exactly as §B.3 required.

⚠️ **The predicted proportionality break is visible but small and in the expected direction:**
`qty ≥ 3` is **cheapest** (0.0689) — per-order components amortising. **It does not show up at
`qty = 1` vs `qty = 2`.**

### §B.4 · ⛔ WHAT THIS SPLIT CANNOT SHOW
- **220 trades, one regime, ~2 months.** `qty ≥ 3` is **n = 25** — too small to carry weight.
- **The buckets are not a random sample: they are the smallest positions by construction**, and
  `qty = 1` is where concentration bound hardest — so bucket membership correlates with the signal's
  own price and volatility, not only with size.
- **`qty_filled = 1` (134) is not the same set as the 111 FIX-133 lifts** (`qty_planned`). Overlapping,
  not identical.
- **Expectancy is negative in all three buckets.** ⛔ That is a fact about the corpus, **not** a
  finding about sizing, and it is not pursued here.
- ⚠️ `avg net P&L` and `expectancy R` disagree in sign for `qty ≥ 3` — **ratio-of-averages vs
  average-of-ratios.** Both are reported; neither is preferred.

---

## §C · TELEMETRY FIELDS — ⛔ RECORDED FOR THE BUILD STEP, NOT BUILT

Per rung: `raw_qty → candidate_qty → accepted_qty → final_qty`, plus **`kind`** (`CAP` / `MODIFIER` /
`FLOOR`) · `limiting_rule` · **`denominator`** (`REAL_CAPITAL` / `PURCHASING_POWER`) · `pipeline` ·
`reason` · `decision_stage` · `decision_id` *(keyed on the **rung**, not on execution order)* · and
**`delta_qty = accepted_qty − candidate_qty`** — ⭐ **which exposes a cancelling pair without
post-analysis** *(the FIX-133 lift would read `delta_qty = +1` on 111 trades)*.

- **C.1** — each rung's invariant documented **beside the rung**, so a future modifier cannot violate
  it silently.
- **C.2 — minimum replay matrix: `raw_qty` = 1 · 2 · 3 · 4.** ⭐ Covers the lift (1), the two
  collapse-to-1 cases (2, 3) and the modal halving (4) — **which is 397 of 483 trades, 82 %.**
- **C.3 — transition frequency** under a change to **G2 · tier thresholds · concentration.**
  ⭐ **The 77 % tier figure is its first data point.**

---

# OPEN QUESTIONS FOR THE DESIGN STEP — ⛔ UNANSWERED

1. **Should a shared key require a written reason, like `max_consecutive_losses` has?** ⭐ That one row
   is the only place the system distinguishes deliberate sharing from accidental sharing.
2. **Concentration and tier both need delivery control — but is the right unit the same?** A delivery
   concentration cap governs exposure *over days*, not over a session.
3. **Is the entry window the right analogue for max-carry-days**, or are they independent settings that
   merely rhyme?
4. **Should the delivery surface inherit intraday DEFAULTS or start empty?** ⭐ The two existing twins
   are `null` and inert; a third inert twin costs the same as the first two.
5. **Where does the capital-starvation alert fire** without alerting on every rejected signal?
6. **Does `daily_loss_limit_pct` need a bucket denominator** rather than a total one?
7. **How many of the 98 excluded signal-generation keys shape delivery differently?** ⛔ Out of scope
   here; **G2 lives there** and the tier depends on it.
8. **Should `slm_margin_buffer_pct` be zero for CNC**, given there is no SL-Market order?
