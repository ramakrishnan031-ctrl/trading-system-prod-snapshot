# SIZING — THE RECOUNT, THE DEPENDENCY TABLE, AND THE GRAPH

**06-Aug-2026 · the work the freeze does NOT block · companion to `delivery_config_surface_06aug2026.md`**
⛔ **MEASUREMENT · CLASSIFICATION · DEPENDENCIES ONLY.** No value · no YAML key · no code · no
authority ruling · no pipeline-ownership ruling. Citations at the deployed SHA `0197923`.

> ⛔ **NO BUCKET PERCENTAGE IS QUOTED IN THIS DOCUMENT.** §1 is why.

---

# §1 · 🔴 THE RECOUNT — **the four are INSIDE the 113, and the total reconciles by ACCIDENT**

## §1.1 · Inside or outside? — **INSIDE. Measured, not reasoned.**

**(P) all four exist as leaf keys in the corpus file** `config/system_config.yaml`:

| key | line | section | in an excluded family? |
|---|---|---|---|
| `delivery_risk_per_trade_pct: null` | `:190` | `position_sizing` | ❌ no |
| `delivery_max_position_value_pct: null` | `:191` | `position_sizing` | ❌ no |
| `max_open_delivery_positions: 3` | `:205` | `risk` | ❌ no |
| `max_daily_delivery_trades: 5` | `:206` | `risk` | ❌ no |

**(S) the corpus rule** (`config_surface_review_06aug2026.md:31-43`): 301 leaf keys − 98
signal-generation − ~90 infra ⇒ 113. **INCLUDE** = *"changing it can alter a trade's existence ·
size · execution · lifecycle · alerting."*
⇒ All four sit in `position_sizing.*` / `risk.*` — **neither an excluded family** — and all four
plainly alter size or existence. ⭐ **`config_surface_review` §A.4 `:53-56` already lists all four
by name as in-scope.**

> ## ✅ **ANSWER: THE FOUR ARE INSIDE THE 113.**

## §1.2 · 🔴 THEREFORE THE PARTITION IS BROKEN — **in TWO offsetting ways**

**The four-bucket total `31 + 34 + 6 + 42 = 113` is arithmetically correct and semantically wrong**,
because it mixes two different populations:

| | finding | evidence |
|---|---|---|
| **(a)** | **the 4 existing delivery twins are in NO bucket** | the TWIN bucket counts **intraday parents only** — its own count audit says so (`:179-185`), and the arithmetic `1+3+2+3+9+4+2+2+2+4+1 = 33 (+1 H5) = 34` contains no delivery key |
| **(b)** | **the DELIVERY-ONLY 6 are NOT keys at all** | ⛔ the bucket's own heading: **"6 *(no key exists for any of them)*"** (`:187`). A corpus defined as *leaf keys in `system_config.yaml`* **cannot contain a key that does not exist** |

⇒ **Existing in-scope keys actually classified = `31 + 34 + 42` = 107.**
⇒ **6 phantom slots were added (b) and at least 4 real keys omitted (a) — and the two errors very
nearly cancel, which is why the total looked right.**

> ### ⭐⭐ **THIS IS THE WORST POSSIBLE SHAPE FOR A MISCOUNT**
> ⛔ **A total that reconciles is the single strongest signal a reader has that a classification is
> complete.** Here it reconciled **because two independent errors offset**. 🏷️ **Filed as the fourth
> instance of the `M12` class (§5 of the practices file).**

**The residual, stated honestly:** `113 − 107 = 6` unclassified, of which **4 are named above** and
**2 are UNIDENTIFIED**. ⚠️ **That residual inherits the corpus's own ±1 uncertainty** — the review
records `301` by YAML parse against `302` by indentation walk (`:31-32`, *"not chased here"*).
⛔ **Do not quote "2" as a measured count; it is a remainder, and one of its inputs is unsettled.**

## §1.3 · POST-FILTER BUCKET COUNTS — ⛔ **and the destination line does NOT close**

**(P) §6's own tables, re-added row by row.** PASS `1+3+1+1+2+1 = 9` ✅ · DROP
`1+3+9+3+2+1+4+1+1 = 25` ✅ · `9 + 25 = 34` ✅ — **the filter's own arithmetic is sound.**

🔴 **But its destination summary (`:334`) accounts for only 17 of the 25 drops:**

| destination as stated | n | which keys |
|---|---|---|
| → **SHARED FOREVER** | 9 | `entry_gate.*` — ⭐ *the value preference stated verbatim; a fill is a fill* |
| → **INTENTIONALLY UNAVAILABLE** | 4 | `tgt_retry.*` — 🔴 *reclassified: the CNC order set has **zero TGT rows**; a twin would assert a mechanism that does not exist* |
| **readmittable** | 3 | sector (3) — *the difference is real, but the parent is in `observe` mode ⇒ inert twice over* |
| → **§7 (a ruling, not a value)** | 1 | `one_trade_per_symbol_direction_per_day` |
| | **17** | ⛔ **against a DROP total of 25** |

> ⭐ **§1.3's question answered — the two large moves went to different buckets for different
> reasons, and the distinction is the point:** `entry_gate.*` (9) fell because **there is no semantic
> difference** *(→ SHARED FOREVER)*; `tgt_retry.*` (4) fell because **the mechanism does not exist on
> the CNC path** *(→ INTENTIONALLY UNAVAILABLE)*. ⛔ **A drop is not a destination.**

### 🔴 THE 8 DROPS WITH NO STATED DESTINATION

| row | key(s) | n | destination **by the filter's own rule** | ⛔ needs a ruling? |
|---|---|---|---|---|
| 7b | `strategy_circuit_breaker.enabled` · `.loss_multiplier` · `.lookback_days` | 3 | **SHARED FOREVER** — *"same concept"*, and `lookback_days` is already in days on both sides | no |
| 12 | `price_drift_threshold` | 1 | **SHARED FOREVER** — same concept, same moment, before the paths diverge | no |
| 10b | `lot_skew_rejection_threshold` | 1 | **INTENTIONALLY UNAVAILABLE** — `:558` skips it when `lot_size == 1`; cash equities are lot_size 1 ⇒ **structurally inert on BOTH paths** | no |
| 4 | `daily_loss_include_unrealized` | 1 | **readmittable** — the same shape as sector: the gate logs `would_reject` and enforces realized-only ⇒ readmit **when the parent enforces** | no |
| 8 | `risk_per_trade_pct` · `max_position_value_pct` | 2 | ⛔ **CANNOT BE ASSIGNED HERE** — these are the parents of two of §1.1's four unclassified twins. Their destination **is** the twin-retirement question | 🔴 **YES — Rama's** |

⭐ **The filter's rule supplies 6 of the 8 unaided** (`:311-312`: *cannot state a difference ⇒ it is
`SHARED FOREVER`, with that as its declared reason*). ⛔ **The last 2 are a ruling, not a
classification** — recorded, not taken.

---

# §2 · THE DEPENDENCY TABLE — **the column with teeth is `VALIDATION TEST`**

⭐ **`VALIDATION TEST` = the test that would go RED if this key had NO EFFECT.**
⛔ **Not "a test exists." "A test that could FAIL if the key were ignored."**

## §2.1 · The 9 that passed the semantic filter

| # | parent key | shared / delivery | depends on | telemetry field | 🔴 VALIDATION TEST — *goes RED if the key is ignored* |
|---|---|---|---|---|---|
| 1 | `max_concentration_pct` | **shared, no twin** | ⛔ **nothing upstream** — §2.4 | `binding_constraint='concentration'` | set the delivery cap tighter than the intraday one on a signal that **would size above it**; assert `qty` falls **and** `binding_constraint` names concentration. ⚠️ **Anti-vacuity: the fixture must size ABOVE the tighter cap** — it binds 483/483 today, so a naive fixture passes for the wrong reason |
| 2 | `tier_multipliers` (3) | **shared, no twin** | quality tier ← scorer; **the FIX-133 floor** | `sizing_breakdown.tier_multiplier` | ⭐⭐ **MUST ASSERT `raw_qty ≥ 2` AS A PRECONDITION.** At `raw_qty = 1`, `floor(1 × 0.5) = 0 → lifted to 1` ⇒ **the tier changes nothing on 111/483 (23 %)**. ⛔ **A fixture drawn from the 111 passes GREEN while the key does nothing** — vacuous by construction (§6.2) |
| 3 | `daily_loss_limit_pct` | **shared** | `_total` *(and see §3.2 — it is a LOOP)* | `failed_check='DAILY_LOSS'` ⚠️ **outside the agreed ladder set** | drive realized P&L past a **delivery-denominated** limit that the **total**-denominated limit would not trip; assert the gate fires. ⭐ **Second half, easily missed: the TIME BASIS** — `pnl_delta` is keyed to the **exit** day, so a 4-day loss lands on one day's budget (§6.1). **A test that fixes only the denominator looks complete and is half a test** |
| 7 | `strategy_circuit_breaker.cutoff_time` | **shared** | the strategy governor's pause record | ⚠️ **the breaker's pause record — outside the ladder set** | set a delivery cutoff later than `12:00`; assert a delivery entry between the two times is **allowed** while an intraday one is **paused** |
| 9 | `max_open_positions` · `max_daily_trades` | ✅ **already isolated** | bucket selection `if bucket=="positional"` | the existing `DAILY_TRADES` / `OPEN_POSITIONS` `failed_check` | ⭐⭐ **THE POSITIVE CONTROL — this is what passing looks like.** `risk_engine.py:468` / `:552` are an `if/else` on the bucket; the slot cap is **observably at 2/3 today**. ⛔ **Validate the instrument against this row before trusting any other row** |
| 10 | `min_qty_threshold` | **shared** | ⚠️ **the FIX-133 floor decides first** | `sizing_breakdown` | ⛔ **Assert the FIX-133 floor did NOT already decide** — §8 records that the floor, not this threshold, is the rung that binds. A test that does not exclude the floor is measuring the floor |

## §2.2 · The DELIVERY-ONLY 6 — ⛔ **no telemetry field exists; it must be BUILT**

⭐ **§2.3's instruction applied: say so rather than assuming the ladder set grows.**
`max_carry_days` · `carry_countdown_alert_days` · `entry_cutoff` · `alerts.vocabulary` ·
`alerts.capital_starvation_enabled` · `eod_reconcile.allow_open_carry`

> 🔴 **`alerts.capital_starvation_enabled` — THE NEGATIVE HALF IS THE TEST.**
> ⛔ **An alert test that only asserts "it fires when starved" passes by alerting on EVERYTHING.**
> The validation test **must** carry a negative case: **a healthy-capital fixture that asserts
> SILENCE.** ⭐ Same family as `V5` (a check with no failing input) and as the drift alarm's
> scored-prediction-of-silence.

## §2.3 · Telemetry that falls OUTSIDE the agreed ladder set — **named, not absorbed**

| key | field | status |
|---|---|---|
| `daily_loss_limit_pct` | `failed_check='DAILY_LOSS'` | exists, **outside the ladder set** |
| `cutoff_time` | the breaker's pause record | exists, **outside the ladder set** |
| **the 6 carry keys** | — | ⛔ **NO FIELD EXISTS. It must be built.** |

## §2.4 · ⛔ A key with no dependencies is SUSPICIOUS, not clean

**The one legitimate empty row so far: `max_concentration_pct`** — empty **upward** because it is the
**top of the chain**, and **full downward** (it decides 483/483 quantities). ⭐ **Any other empty row
gets re-checked, not accepted.**

---

# §3 · THE GRAPH — `Key → Decision → Telemetry → Validation → Downstream`

**Every edge classified `FUNCTIONAL` · `AUDIT` · `SAFETY`.** ⭐ The classification is not decoration:
the `_check7 → DH1 → kill` edge proved **a reviewer cannot tell which kind an edge is by looking** —
an audit-shaped publish turned out to sit in `_ESCALATING_SOURCES` and reach a single-sample kill.

## §3.1 · ✅ **THE INSTRUMENT VALIDATED AGAINST A KNOWN ANSWER — IT SURFACED THE BUCKET SPLIT**

**The known answer** (register, and `delivery_config_surface:158`): the bucket split
*"cannot be per-pipeline without circularity."*

**The graph reproduces it, and names the mechanism:**
```
capital.intraday_bucket_pct ──FUNCTIONAL──> bucket capital  =  _total × pct     (fund_manager.py:2290)
        ▲                                          │
        └──────────── would depend on ─────────────┘   ⛔ IF the pct were per-pipeline
```
⇒ **the split is computed FROM total capital, so a per-pipeline split would have to know which
pipeline's capital it is defining — and that capital is what the split defines.** ⭐ **The loop
closes in one hop.**
> ✅ **INSTRUMENT VALIDATED.** ⛔ Had the graph missed this, the graph would be incomplete — **and
> that failure would have been more useful than any new edge it found.**

## §3.2 · 🔴 A SECOND CIRCULARITY — **currently healthy, and the reason is written down**

**(S) `fund_manager.py:1316`:** `loss_limit = self._daily_loss_limit_pct * self._total`, and **(S)
`:2252`:** `_total` is *"initial broker balance +/- all PnL."*

```
realized loss ──> _total falls ──> loss_limit ( = pct × _total ) falls ──> the threshold moves
      ▲                                                                     TOWARD the loss
      └─────────────────────────── same day ────────────────────────────────────┘
```
⇒ **The daily-loss limit is a fraction of a figure the day's own losses reduce.** As the day
worsens, the limit **tightens**.

> ⭐ **WHY IT IS HEALTHY, recorded per §3.2's instruction:** the feedback is **negative** — it
> tightens under stress, so it errs **restrictive / fail-safe**, and the effect is second-order at
> a 3 % limit *(a 3 % loss moves the limit by ~0.09 % of capital)*.
> ⛔ **But it IS a loop, and two things would change its sign or size:** a **larger** `pct`, or any
> future change that makes `_total` rise intraday on **unrealized** marks. **Re-derive if either
> happens.** 🏷️ Recorded, **not** proposed for change.

## §3.3 · Edge classification — the three that matter

| edge | class | note |
|---|---|---|
| `max_concentration_pct → qty` | **FUNCTIONAL** | 483/483 — the only cap that has ever decided a quantity |
| `tier_multipliers → qty` | **FUNCTIONAL** | 372/483; ⚠️ **cancelled by the FIX-133 floor on the other 111** |
| `daily_loss_limit_pct → FM7 breach → on_loss_breach()` | 🔴 **SAFETY** | ⛔ **not AUDIT.** `:1330` calls the breach callback — this edge reaches a kill path, and it looks like a log line at `:1318` |

---

*Read-only. No value set, no key created, no ruling taken. §1's residual and §1.3's two
ruling-dependent rows are recorded as open, not resolved.*
