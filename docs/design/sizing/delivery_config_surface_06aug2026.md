# THE DELIVERY CONFIG SURFACE — PLACEHOLDERS

**06-Aug-2026 · Step 2 · fourth of `docs/design/sizing/`**
⛔⛔ **PLACEHOLDERS ONLY. NOT ONE VALUE. No key created in `system_config.yaml`. No code.**
Built on `current_sizing_chain_06aug2026.md`, `tier_multiplier_measurement_06aug2026.md`,
`config_surface_review_06aug2026.md`. Citations at deployed SHA `0197923`.

> # 🔴 GATE — **THIS DESIGN IS BLOCKED, AND SAYS SO**
> **Item 5 (the delivery configuration surface) is HARD-GATED on item 4**, and item 4 is blocked on
> an authority ruling Rama has not given. ⛔ **Nothing here is authorised, and no key here may be
> written to `system_config.yaml` until that ruling exists.** ⭐ It is drafted now because the
> *measurements* that shape it are complete and perishable — **not** because the gate has moved.

---

# §2 · THE MEASUREMENT — **the slot cap DOES count carried positions. ✅**

**§2.1 — `count_open_delivery_positions()` (`core/state_store.py:772-782`):**
```sql
SELECT COUNT(DISTINCT t.trade_id) FROM trades t
  JOIN orders o ON o.trade_id=t.trade_id AND o.leg='ENTRY' AND o.product='CNC'
 WHERE t.status IN ('OPEN','PARTIAL','PENDING_FILL')      -- ⭐ NO DATE FILTER
```
⇒ ✅ **A carried position occupies its slot for as long as it is open.** **§2.2's feared unbounded
growth does NOT occur** — the cap is a true concurrency cap and it already spans the carry.

**§2.4 — `count_daily_delivery_trades(date_iso)` (`:784-798`):**
```sql
 WHERE SUBSTR(t.created_at,1,10) = ?     -- ⭐ DATE-FILTERED on creation
```
⇒ ✅ **A carried position consumes a trade slot ONLY on the day it was opened.**

> ### ⭐⭐ THE TWO CAPS ARE SEMANTICALLY CORRECT **AND COMPLEMENTARY**
> **slot cap (3) = concurrency, spans carries · daily cap (5) = new-entry rate, resets daily.**
> §2.3's warning is **already satisfied by construction**: the slot cap *is* the count axis and it
> *already* carries duration. ⛔ A max-carry-days limit is the **duration** axis of the same
> constraint — they do not contradict, **but they interact**: a position held N days occupies a slot
> for N days, so `max_carry_days` and `max_open_delivery_positions` jointly set delivery throughput.
> **⛔ They must be designed together or the second will silently bound the first.**

## §2.5 · 🔴 A NEW COST OF THE F6 PHANTOM, MEASURED HERE AND NOT PREVIOUSLY NAMED

**(P) at 16:13 IST — the exact query `risk_engine.py:469` gates on:**
```
open delivery slots used:  2  of  3
  trd_e66ee17b…  ATULAUTO  OPEN  opened 2026-08-05   ← 🔴 THE PHANTOM: no broker position
  trd_010f8e21…  DIFFNKG   OPEN  opened 2026-08-06   ← real
```
⭐⭐ **Because the slot query has no date bound and the phantom trade never closes, the phantom
permanently occupies 1 of 3 delivery slots — 33 % of delivery concurrency.**

⇒ **A second, independent cost alongside the ₹587 stranded capital (~21 % of the bucket).**
🔴 **Three phantoms would block delivery entirely, and the symptom would be
`REJECTED_OPEN_POSITIONS` — which reads as a cap doing its job.**
⛔ Recorded; **not fixed, and it does not change the F6 design** *(see
`../f6_delivery_exit_predicate_design_06aug2026.md` §15)*.

---

# §4 · THE SURFACE — **placeholders, ordered by measured impact**

⛔ **Every value below is `<PLACEHOLDER>`. Not one number is proposed.**

### 1 · `delivery.max_concentration_pct` — 🔴 **the highest-impact gap in the system**
**Why first:** `binding_constraint = 'concentration'` on **483/483** trades and **3,468** rejections.
**It is the only cap that has ever decided a quantity, and it has no delivery control at all**
(`position_sizer.py:424` reads the global raw, while `:381` reads the effective for risk).
⭐ **Different semantics, not a copy:** intraday concentration is a same-session exposure; a delivery
position is concentrated **for days**, across gaps, with no intraday exit.

### 2 · `delivery.tier_multipliers` — the second binding lever
**Why:** halves **372/483** positions. No delivery key exists anywhere (measured: zero hits).
> ⚠️⚠️ **STATE THIS BEFORE ITS FIRST TEST OR IT WILL LOOK BROKEN:** at `raw_qty = 1` the tier is
> **cancelled by the FIX-133 floor** — `floor(1 × 0.5) = 0 → lifted to 1`. **On 111 of 483 trades
> the switch changes nothing.** ⭐ Its effect appears only from `raw_qty ≥ 2`, which at present
> capital is where 372 of 483 sit.

### 3 · `delivery.daily_loss_limit_pct` — ⭐ **denominated on the BUCKET, not on TOTAL**
**Rama found a real gap.** Today `daily_loss_limit_pct: 0.03` is **3 % of TOTAL**
(`fund_manager.py:1316`), so a delivery drawdown is measured against intraday's capital too — and a
delivery loss soft-kills the intraday day *(register H1)*.
⚠️ **And the timing makes it sharper:** `pnl_delta` is realised and keyed to the **exit** day, so a
multi-day delivery loss lands entirely on one day's budget.

### 4 · Carry lifecycle — 🆕 **delivery-only, no intraday analogue exists**
`delivery.max_carry_days` · `delivery.carry_countdown_alert_days` · **and §2's slot semantics as a
declared contract**, not an emergent one. ⛔ Design with #1's slot cap, per §2.

### 5 · Entry timing — ⛔ **a CUTOFF, not a window. Not a copy of `10:00–15:00`.**
On a days horizon the question is **not** *"too late to exit today"* but ⭐ *"too late to OBSERVE
this position before it carries overnight unattended."* ⇒ **`delivery.entry_cutoff` — one boundary,
different concept.** ⛔ Not a twin of `entry_start`/`entry_end`.

### 6 · Alerting — 🆕 delivery-only
`delivery.alerts.*` (separate Telegram vocabulary — Rama's ruling) and
**`delivery.alerts.capital_starvation_enabled`.**
⭐ **Nearly free:** the sizer already computes `constraint` + `reason` + full `breakdown` at every
rejection (`position_sizer.py:449-464`, `:600-614`, `:616+`) **and discards them.**
⭐⭐ **And it is the first instrument that would make *"no signals qualified"* distinguishable from
*"starved"*** — the exact silence the F6 phantom produces, now doubly so given §2.5.

### 8 · `delivery.min_score` — **delivery-only; ⛔ DESIGN EXISTS ELSEWHERE (Q10 Part A)**
Present on the surface so its absence is not read as an oversight — **Rama asked for it explicitly.**
⛔ **Not designed here.** ⇒ `docs/audit/regime_thesis_minscore_control_18jul2026.md`.
⚠️ **Constraint at the key: scores cap at 65** ⇒ a threshold is meaningful only inside **0–65**.
*(Full statement: §9.1.)*

### 7 · Everything else — a twin, a declared reason, or intentionally unavailable *(§3)*
⛔ **And §6 now cuts the twin bucket 34 → 9. Read §6 before treating §3's 34 as the candidate set.**

## §4.1 · ⛔ EXCLUDED — with the measurement as the reason

| excluded | measured reason |
|---|---|
| `delivery_max_position_value_pct` *(exists, `null`)* | 🔴 **the cap has NEVER rejected a trade — zero, ever, in the whole `signals` table.** A twin of a veto that has never fired |
| `delivery_risk_per_trade_pct` *(exists, `null`)* | `position_sizer.py:434` — *"algebraically never today"*; zero `REJECTED_SIZING_RISK` |
| max trades/day · max open positions | ⭐ **already cleanly isolated** — `risk_engine.py:468` and `:552` are an `if/else` on the bucket. ⛔ **This refutes the 4+2 / 2+4 example that prompted the review** |
| `max_single_order_qty` · `min_tick_size` | measured inert — max observed `qty_by_risk` = 123 against a cap of 10,000 |
| **the drift tolerance** | ⛔ **§4.4 — NOT on this surface.** ⭐ **The fix is the COMPARISON, not a wider band.** A per-pipeline tolerance would make a **wrong comparison quieter** — AR9's refused move — and it fails **silently**. ⚠️ **Rama's concern is legitimate and is answered elsewhere** *(the equity-vs-free-cash pairing)*, **not declined** |

## §4.2 · ⛔⛔ NO VALUES
**Not one.** A value set before the Part A authority question is settled is exactly the *"blind
settings"* Rama refused — ⭐ and at ₹10,000 the **arithmetic** decides the quantity anyway
(concentration binds 483/483).

## §4.3 · Rama's formula, recorded in its corrected form
> **`Qty = capital allocated per trade ÷ stop-loss distance per share`**
⛔ **Never derived from R:R** — ⭐ **R:R governs expectancy, not size.**
⚠️ **The sting, recorded with it:** the code **already implements exactly this** — `risk_rs =
total_capital × eff_risk_pct; qty_by_risk = floor(risk_rs / sl_distance)` (`:381-382`) — and
`:434` records that **it has never been the binding constraint.** The formula is right and inert.

---

# §3 · THE FOUR-WAY CLASSIFICATION — all 113 in-scope keys

⛔ **Every bucket carries a reason, including SHARED FOREVER** — ⭐ the whole point of the
`max_consecutive_losses` template is that **the reason lives beside the control, not in a document.**

| bucket | keys | share |
|---|---|---|
| **SHARED FOREVER** | **31** | 27 % |
| **DELIVERY TWIN** | **34** | 30 % |
| **DELIVERY-ONLY** *(new)* | **6** | 5 % |
| ⭐ **INTENTIONALLY UNAVAILABLE** | **42** | **37 %** |

> ⭐⭐ **§3.2 answered: the fourth bucket is the LARGEST.** A surface that only grows is not a design.

### SHARED FOREVER — 31
| keys | reason |
|---|---|
| `risk.max_consecutive_losses` | ⭐ **already declares itself** at `risk_engine.py:574-575` — the template |
| `kill_switch.*` (2) · `circuit_breaker.max_api_failures` | the kill switch's **existence** is intended-shared; a broker outage is not per-pipeline |
| `fno_ban.*` (2) · `excluded_symbols` | regulatory / blacklist facts, not preferences |
| `trading_hours.market_open` · `market_close` · `service_window_end` · `special_sessions` | exchange facts |
| `signal_queue.*` (4) · `signal_processor.worker_count` · `drain_poll_sec` · `pipeline_timeout_sec` | queue plumbing — no trade semantics |
| `order_monitor.*` (2) · `order_reconciler.poll_interval_sec` · `human_order_margin_tolerance` · `stuck_exiting_timeout_minutes` | reconciliation plumbing, one broker |
| `capital.intraday_bucket_pct` · `positional_bucket_pct` · `conditional_allocation_enabled` | **the split itself** — it cannot be per-pipeline without circularity |
| `drift_handler.*` (4) | absolute-rupee kill tiers on one account ⚠️ *(and `_check7` escalates — see M11)* |
| `alerts` transport keys (6) | one Telegram transport; **only the vocabulary is delivery-only** |
| `position_sizing.enabled` · `capital.leverage_map` | master switch; leverage already keyed per intent |

### DELIVERY TWIN — 34 *(**candidates**; ⛔ §6 cuts this to 9 — read §6 before citing this table)*
| keys | reason |
|---|---|
| 🔴 `max_concentration_pct` | **binds 483/483, no twin** — surface item 1 |
| 🔴 `tier_multipliers` (3) | **binds 372/483, no twin** — surface item 2 |
| `daily_loss_limit_pct` · `daily_loss_include_unrealized` | bucket denominator — surface item 3 |
| `max_sector_exposure_pct` · `sector_cap_mode` · `sector_unknown_alert_pct` | a days-long sector exposure is a different risk from a session's |
| `entry_gate.*` (9) | same concept, **values tuned for intraday fills** |
| `strategy_circuit_breaker.*` (4) | `cutoff_time: 12:00` is an intraday clock; the concept survives, the unit does not |
| `risk_per_trade_pct` · `max_position_value_pct` | ⚠️ **twins already exist and are inert** — kept in-bucket for completeness, ⛔ **excluded from the surface (§4.1)** |
| `max_open_positions` · `max_daily_trades` | ✅ **already isolated** |
| `min_qty_threshold` · `lot_skew_rejection_threshold` | rounding policy differs when one share is the whole position |
| `tgt_retry.*` (4) | retry cadence on a days horizon ⛔ **REFUTED in §6 — delivery has no TGT order** |
| `price_drift_threshold` | pre-placement margin top-up |
| ~~`one_trade_per_symbol_direction_per_day`~~ | 🔴 **MOVED OUT — §7. Not a config value; a pipeline-isolation RULING owed to Rama** |

> **⚠️ COUNT AUDIT — the arithmetic, so the table can be cited.** The row-list above enumerates **34
> parent keys** (`1+3+2+3+9+4+2+2+2+4+1` and H5 moved out ⇒ 33 remain here, +1 for H5 = 34 as
> classified). The **four already-existing delivery twins** (`delivery_risk_per_trade_pct`,
> `delivery_max_position_value_pct`, `max_open_delivery_positions`, `max_daily_delivery_trades`) are
> **delivery keys, not intraday parents**, and are **not** counted in the 34. ⛔ **Whether those four
> sit inside the 113 in-scope corpus is UNVERIFIED here** — the four-bucket total `31+34+6+42 = 113`
> leaves no room for them. **One recount is owed before §3's percentages are quoted anywhere.**

### DELIVERY-ONLY — 6 *(no key exists for any of them)*
`max_carry_days` · `carry_countdown_alert_days` · `entry_cutoff` · `alerts.vocabulary` ·
`alerts.capital_starvation_enabled` · `eod_reconcile.allow_open_carry`
**Reason:** each names a concept intraday does not have — duration, overnight observability, and a
reconciliation that must not fail on a carried position.

### ⭐ INTENTIONALLY UNAVAILABLE — 42 · **delivery must NOT have this control**
| keys | reason a twin would be HARMFUL |
|---|---|
| `eod_squareoff.*` (6) · `circuit_breaker.force_close_time` | ⛔ **CNC is exempt by design (EOD6).** A delivery EOD-squareoff key **invites someone to turn it on** and flatten a carry |
| `circuit_breaker.partial_fill_timeout_minutes` | minutes-scale abandonment on a days-scale hold — ⭐ the 10-min-GTT-timeout family |
| `capital.slm_margin_buffer_pct` | ⛔ **there is no SL-Market order on the CNC path.** A delivery value would assert a mechanism that does not exist |
| `capital.sl_limit_offset_pct` · `emergency_exit_buffer_pct` | intraday exit-order geometry; delivery's equivalent is `gtt_sl_limit_offset_pct`, which already exists |
| `signal_processor.per_symbol_cooldown_sec` · `min_gap_between_entries_sec` · `entry_burst_*` (4) | ⛔ seconds-scale throttles encode **the wrong axis**; delivery's throttle is `max_carry_days` |
| `smart_tgt.*` (5) · `structure_exit.*` (6) | tick-driven and dormant — ⭐ a delivery twin would be a knob on a path that has never run |
| `portfolio_allocator.*` (7) | `allocator_mode: shadow`; two `null` keys already |
| `order_reconciler.capital_drift_tolerance*` (3) | ⛔ **§4.4** — a per-pipeline band makes a **wrong comparison quieter**, silently |
| `order_reconciler.check1_mid_fill_defer_sec` | **paper cannot exercise it**; ON is an unrehearsed path into LIVE |
| `mis_filter.*` (3) | MIS-specific by definition |
| `max_single_order_qty` · `min_tick_size` · `dynamic_by_winrate` · `min_multiplier` · `max_multiplier` | broker/exchange facts, or measured inert (`perf_weight = 1.0` on 483/483) |

---

# §5 · THE 1:1 ACCEPTANCE CRITERION — **TESTED AGAINST THE TWO TWINS WE ALREADY HAVE**

> ## THE RULE, AS ADOPTED
> **CONFIG → DECISION → TELEMETRY must be ONE-TO-ONE. Every delivery key must identify exactly one
> decision stage and one telemetry field. ⛔ REJECT ANY KEY THAT CANNOT BE OBSERVED INDEPENDENTLY
> AFTER DEPLOYMENT.**

## §5.1 · 🔴 FIRST — **the complete telemetry surface that exists TODAY, measured at full width**

⛔ **A rule about telemetry fields is untestable without the list of fields that exist.**
**(S) Width: every `breakdown[…]` write plus the dict literal, whole of `capital/position_sizer.py`
(677 lines) — 16 fields, and this is the entire set:**

```
:441-445  qty_by_risk · qty_by_capital · qty_by_concentration · raw_qty · tier_multiplier
:491-499  tier_multiplier_mode · tier_weight_applied · perf_weight_applied ·
          flat_value_rs_used · qty_by_flat · tiered_qty · tier_mult · perf_weight · effective_mult
:544-551  (OFF_FLAT branch — same names)
:646-647  binding_constraint · actual_position_value_rs
```
⭐ **`binding_constraint` (`:646`) is the DECISION field.** It is the only one that records *which rung
won*. Everything else records what a rung *proposed*. ⛔ **That distinction is the whole of §5.3.**

## §5.2 · TWIN B — `delivery_max_position_value_pct` · 🔴 **THE RULE REJECTS IT OUTRIGHT**

**Decision stage:** ✅ exactly one — `:585-586`, the veto.
**Telemetry field:** 🔴 **NONE. Measured, not assumed.**

| where the cap appears | emits on |
|---|---|
| `:589-597` `log.critical("position_value_cap_exceeded")` | ⛔ **the REJECT branch only** |
| `:606-610` `SizingResult(constraint="POSITION_VALUE_CAP", reason=…)` | ⛔ **the REJECT branch only** |
| `:647` `breakdown["actual_position_value_rs"]` | every trade — ⛔ **but it is `final_qty × entry_price`: the NUMERATOR, not the cap** |

⇒ **The cap it was tested against (`max_position_value = eff_max_position_value_pct × total_capital`)
is a local at `:585` and is persisted NOWHERE.** Its only two emitters are on a branch that has
**never executed — zero rejections in the whole `signals` table, ever.**

> ### ⭐⭐ `actual_position_value_rs` IS A **TAUTOLOGICAL OBSERVABLE** — V5, appearing as telemetry
> It is present on every trade, it is named as if it were this cap's field, and **it moves with
> quantity and price but NEVER with the key.** ⛔ **Setting `delivery_max_position_value_pct` to any
> value at or above the level concentration already enforces changes NOT ONE PERSISTED FIELD.**
> ⭐ A *missing* observable leaves you uncertain; **this one leaves you wrongly certain**
> *(`tautological_check_class_05aug2026`)*.

🔴 **AND THE SECOND HALF, WHICH NEEDS NO DATA AT ALL:** `:585` **enforces** the *effective* pct while
`:596` and `:609` **report** `self._max_position_value_pct` — **the GLOBAL.** ⇒ even on the branch
that has never fired, **the one field that could observe the delivery twin is wired to the intraday
key.** ⛔ **That is readable at source at design time.**

> ### ✅ **VERDICT — REJECTED, on the TELEMETRY limb, from SOURCE ALONE.**
> No production corpus required. **The 1:1 rule would have caught this twin the day it was proposed.**

## §5.3 · TWIN A — `delivery_risk_per_trade_pct` · ⚠️ **REJECTED — but ONLY under the strong reading**

**Decision stage:** ✅ exactly one — `:300-303` selects `eff_risk_pct`; `:381-382` computes it.
**Telemetry field:** ⚠️ **`qty_by_risk` (`:441`) — and it is genuinely persisted on every trade.**

⛔ **HERE IS THE HONEST PROBLEM, AND IT IS NOT CONVENIENT:**
**Move this key and `qty_by_risk` moves.** A field changes. Under a *literal* reading — *"names one
decision stage and one telemetry field"* — ⭐ **the rule ACCEPTS this twin.** And accepting it would
be wrong: `binding_constraint = 'concentration'` on **483/483**, and `:434` says in the source
*"algebraically never today"*. **`min()` discards `qty_by_risk` on every trade the system has taken.**

> ### 🔴 **SO THE RULE, AS WRITTEN, CATCHES ONE OF THE TWO. IT IS NOT YET READY.**
> ⛔ **Reported as the result that was not preferred.** A rule endorsed by 1 of 2 is not
> "retroactively validated" — ⭐ and the half it misses is the half where a field *moves* without a
> *decision* changing, which is the more seductive failure.

### §5.3a · ⭐⭐ THE ONE-LINE STRENGTHENING THAT MAKES IT BITE — **and its anchor already exists**

> **The telemetry field must record a DECISION THE KEY WON — not a candidate the key computed.**
> **OPERATIONALLY: the field must be one that `binding_constraint` can name.**

⭐ **This is not invented for the occasion** — `binding_constraint` is a live persisted field
(`:646`), and the value `'risk'` has been written **zero** times in 483 trades. ⇒ the test is a query,
not a judgement.

| | literal rule | **strengthened rule** |
|---|---|---|
| `delivery_max_position_value_pct` | ❌ rejected | ❌ rejected |
| `delivery_risk_per_trade_pct` | ⚠️ **accepted — wrongly** | ✅ **rejected** |

> ## ✅ **ANSWER TO THE CARD'S QUESTION 1: YES — with one clause added, and NOT without it.**
> **As stated, the rule rejects 1 of the 2 known twins. With §5.3a's clause it rejects 2 of 2.**
> ⛔ **Adopt the clause with the rule; the rule alone does not bite where it most needed to.**

## §5.4 · 🔴 A **THIRD**, INDEPENDENT VALIDATION — found in this refinement, not inherited

§7's measurement supplies a case neither twin covers: **a key whose effect is erased by a second,
un-keyed gate that produces the identical outcome.** `one_trade_per_symbol_direction_per_day` would
be *observable in config* and *unobservable in outcome* — ⛔ **turning a delivery twin of it OFF
changes only WHICH reject code is written, never whether the symbol is blocked.**
⭐ **The strengthened rule rejects it. The literal rule does not** *(a field — the reject code —
does change)*. **Three cases, and the clause is load-bearing in two of them.**

---

# §6 · THE SEMANTIC-DIFFERENCE FILTER — **run over all 34. 🔴 34 → 9.**

> ## THE FILTER, INCLUDING THE CONSEQUENCE ChatGPT DID NOT DRAW
> **A twin must state its semantic difference from its intraday parent. IF IT CANNOT, IT IS NOT A
> TWIN — IT IS `SHARED FOREVER`, WITH THAT AS ITS DECLARED REASON.**
> ⛔ **"Same concept, different number" is a VALUE PREFERENCE. A value preference is not an isolation
> argument.**

| # | key(s) | n | verdict | the difference — or why there is none |
|---|---|---|---|---|
| 1 | `max_concentration_pct` | 1 | ✅ **PASS** | a session's exposure vs **days, across gaps, with no intraday exit** |
| 2 | `tier_multipliers` | 3 | ✅ **PASS** | a grade earned on **intraday** scoring applied to a multi-day hold; conviction decays on a different clock |
| 3 | `daily_loss_limit_pct` | 1 | ✅ **PASS** | ⭐ **two differences: the DENOMINATOR *and* the TIME BASIS** — see §6.1 |
| 4 | `daily_loss_include_unrealized` | 1 | ❌ drop | `false` = SHADOW: the gate **logs** `would_reject` and **enforces realized-only** ⇒ a twin controls a path that does not enforce |
| 5 | `max_sector_exposure_pct` · `sector_cap_mode` · `sector_unknown_alert_pct` | 3 | ❌ drop | the difference is **real** *(days-long sector exposure)* ⛔ **but `sector_cap_mode: observe` — the parent does not enforce.** A twin would be **inert twice over**, the exact §A.3 shape. ⭐ **Readmit the day the parent enforces** |
| 6 | `entry_gate.*` | 9 | ❌ **drop — the largest single cut** | ⛔ the bucket's own reason was *"same concept, values tuned for intraday fills"* — **that is the value preference, verbatim.** A fill is a fill: slippage, spread and depth at the moment of entry are **identical concepts** on both paths |
| 7 | `strategy_circuit_breaker.cutoff_time` | 1 | ✅ **PASS** | an intraday clock; **the concept survives, the unit does not** |
| 7b | `.enabled` · `.loss_multiplier` · `.lookback_days` | 3 | ❌ drop | ⛔ `lookback_days: 10` is **already in days** on both sides; `loss_multiplier` and `enabled` are the same concept |
| 8 | `risk_per_trade_pct` · `max_position_value_pct` | 2 | ❌ drop | **§5 — the twins exist, and the rule rejects them.** Already off the surface (§4.1) |
| 9 | `max_open_positions` · `max_daily_trades` | 2 | ✅ **PASS — already built** | ✅ isolated at `risk_engine.py:468` / `:552`; §2.5 shows the slot cap **observably at 2/3 today**. ⭐ **The positive control: this is what passing looks like** |
| 10 | `min_qty_threshold` | 1 | ✅ **PASS** ⚠️ | **one share held 20 minutes vs one share carried overnight** is a different object. ⚠️ **But the rung that actually decides is the FIX-133 floor, not this threshold** — see §8 |
| 10b | `lot_skew_rejection_threshold` | 1 | ❌ drop | ⛔ **`:558` skips it entirely when `lot_size == 1`** — cash equities are lot_size 1 ⇒ **structurally inert on both paths** |
| 11 | `tgt_retry.*` | 4 | ❌ **drop — and RECLASSIFY** | 🔴 **REFUTES the bucket's own reason.** **(P)** the delivery order set is **ONE row, `leg=ENTRY`, `product=CNC` — zero SL rows, zero TGT rows**; protection is a broker-side GTT. ⛔ **A `delivery.tgt_retry.*` asserts a mechanism that does not exist on the CNC path** — the `slm_margin_buffer_pct` shape exactly. ⇒ **→ INTENTIONALLY UNAVAILABLE** |
| 12 | `price_drift_threshold` | 1 | ❌ drop | pre-**placement** drift, before either path diverges — same concept, same moment |
| 13 | `one_trade_per_symbol_direction_per_day` | 1 | 🔴 **OUT — §7** | not a value question at all |
| | **PASS** | **9** | | **7 new + 2 already built** |
| | **DROP** | **25** | | 9 → SHARED FOREVER · 4 → INTENTIONALLY UNAVAILABLE · 3 readmittable · 1 → §7 |

> ## ✅ **ANSWER TO QUESTION 2: 34 → 9 (7 new). THE COUNT FELL BY 74 %.**
> ⭐ **`entry_gate.*` alone is 9 of the 25 drops, and it fell to its own stated reason.** ⛔ A filter
> that had left the 9 standing would have been applied leniently, exactly as §2.1 warned.

## §6.1 · `daily_loss_limit_pct` — ⭐ **the hidden half, stated at the key**
The denominator was the obvious half. **The TIME BASIS is the half that was hidden:** `pnl_delta` is
**realised and keyed to the EXIT day** ⇒ **a loss built over four days lands entirely on one day's
budget.** ⛔ **A bucket-denominated daily limit STILL MIS-MEASURES a multi-day position.** ⭐ Fixing
the denominator without the time basis fixes the smaller half and looks complete.

## §6.2 · ⚠️ THE TIER — **state this AT THE KEY or its first test will look broken**
At `raw_qty = 1` the FIX-133 floor cancels the tier: `floor(1 × 0.5) = 0 → lifted to 1`.
**On 111 of 483 trades (23 %) the switch changes nothing.** ⭐ Its effect appears from `raw_qty ≥ 2`,
which is **372 of 483 (77 %)** at present capital. ⛔ **A validation test drawn from the 111 is
vacuous by construction** — see §8's `raw_qty ≥ 2` precondition.

---

# §7 · 🔴 `one_trade_per_symbol_direction_per_day` — **OUT OF TWIN, AND THE MEASUREMENT GOES FURTHER THAN THE CARD'S**

⭐ **The card's ruling stands and is reinforced. But the source says the named key is NOT the binding
gate, and that changes what the ruling must cover.**

> ## ✅ **RULED 07-Aug-2026 BY RAMA — §7.5's question is ANSWERED. See `MASTER_PENDING_01-Aug-2026.md` §R.2.**
> **ONE simultaneous open position per symbol, account-wide, PIPELINE-INDEPENDENT.** Reject while any
> open position exists; **eligible again the instant it is flat** — *"evaluated from the current
> account state, not from historical ownership."*
> ⭐⭐ **THE THREE GATES BELOW ARE NOW ONE BUSINESS RULE, NOT THREE COINCIDENTALLY-SIMILAR ONES**, and
> the ruling splits them cleanly: **gates 2 and 3 ALREADY implement it** (product-blind, never
> date-scoped, released the instant the trade leaves `PENDING_FILL/OPEN/PARTIAL`); **gate 1 is the
> only one the ruling contradicts** — it is scoped to *historical ownership within the day*, which the
> ruling explicitly rejects.
> 🔴 **§7.3's correction is REINFORCED by production data, not just by source:** `REJECTED_DUPLICATE_SYMBOL`
> **stops on 31-Jul** (107 all-time) and `REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT` **starts on 03-Aug**
> (65) — **(P)** consecutive trading days. ⛔ Anyone measuring the leak under the wrong code after
> 03-Aug reads **zero**. ⭐ Gate 1 masking gate 2 is now MEASURED, not only reasoned.
> ⛔ **NOTHING IS AUTHORISED TO CHANGE.** Full measurement: `docs/audit/rulings_1_2_verification_07aug2026.md`.

## §7.1 · **(S) THREE product-blind symbol gates, not one. Width: all call sites, whole repo.**

| # | gate | site | query | product-aware? | expires? |
|---|---|---|---|---|---|
| **1** | **`SYMBOL_DIRECTION_DAILY_LIMIT`** *(the named key)* | `signal_processor.py:717` | `count_executed_trades_today_for_symbol_direction` → `WHERE symbol=? AND direction=? AND SUBSTR(created_at,1,10)=?` `state_store.py:723-729` | ⛔ **no** | ✅ **midnight** |
| **2** | 🔴 **`DUPLICATE_SYMBOL`** | `risk_engine.py:688-693` | `has_active_position` → `WHERE symbol=? AND status IN ('PENDING_FILL','OPEN','PARTIAL')` `state_store.py:844-851` | ⛔ **no** | 🔴 **NEVER** |
| **3** | `CONTRARY_POSITION` | `risk_engine.py:~680` | `get_active_position_direction` — same shape | ⛔ **no** | 🔴 **NEVER** |

⛔ **None of the three can be product-aware without a join none of them does:** there is **no
`trades.product` column** — product lives on `orders`, reachable only via
`LEFT JOIN … leg='ENTRY'` *(`schema_product_is_on_orders_05aug`)*. ⭐ **Structural, not a value.**

## §7.2 · 🔴 **THE ORDERING — and it is the finding**

**(S) All three call sites, identical shape:** `signal_processor.py:1152→1154` · `1935→1937` ·
`2245→2247` — **`_enforce_one_trade_per_symbol_direction(...)` runs BEFORE `self._risk.approve(...)`.**

> ### ⭐⭐ **GATE 1 FIRES FIRST AND MASKS GATE 2 — BUT ONLY ON DAY 1.**
> Gate 1 is **date-filtered**; gate 2 is **not**. ⇒ on the entry day, the block is written as
> `SYMBOL_DIRECTION_DAILY_LIMIT`. **From day 2 of a carry, gate 1's count resets to zero and the block
> passes silently to `DUPLICATE_SYMBOL`, which has NO CONFIG KEY AT ALL and never expires.**
>
> 🔴 **CONSEQUENCE — decisive for the ruling:** **setting a delivery twin of
> `one_trade_per_symbol_direction_per_day` to `false` would NOT unblock the symbol.** It would change
> **which reject code is written**, nothing else. ⛔ **The named key is not the control. It is the
> label on day 1.**

## §7.3 · ⛔ **A CORRECTION TO OUR OWN PRIOR DOC — the source wins**
`config_surface_review_06aug2026.md` §A.4 row 5 records this leak as *"reported as
`DUPLICATE_SYMBOL`"*. 🔴 **That conflates two different gates.** The H5 gate raises
`_PipelineReject("SYMBOL_DIRECTION_DAILY_LIMIT", …)` at `:717`; `DUPLICATE_SYMBOL` is a **separate**
check (RE5 #10). ⛔ **Anyone measuring the leak under `REJECTED_DUPLICATE_SYMBOL` on the entry day
would be counting the WRONG GATE and would read zero.** ⭐ Row 5 is corrected here, not smoothed.

## §7.4 · 🔴 **A THIRD COST OF THE F6 PHANTOM — not previously named**
The ATULAUTO phantom is `status='OPEN'` and never closes ⇒ `has_active_position('ATULAUTO')` is
**permanently true** ⇒ **every intraday signal on ATULAUTO is blocked by `DUPLICATE_SYMBOL`, forever.**
⇒ alongside ₹587 stranded and 1-of-3 delivery slots held (§2.5), **the phantom also removes a symbol
from the intraday universe indefinitely.** ⛔ Recorded; **does not change the F6 design.**

## §7.5 · ⛔ **THE RULING OWED TO RAMA — stated, NOT answered**
> **Should the two pipelines be permitted to hold the same symbol on the same day at all?**
⛔ **This is a pipeline-isolation ruling, not a config value.** ⭐ **And it must cover all three gates
in §7.1, not the one that has a key** — a ruling written against
`one_trade_per_symbol_direction_per_day` alone would leave the two unkeyed, never-expiring gates
untouched and would present as having been implemented. **Owed to Rama. Not answered here.**

---

# §8 · THE DEPENDENCY TABLE — **the last column is the one with teeth**

> **`VALIDATION TEST` = the test that would go RED if this key had no effect.** ⛔ **Not "a test
> exists" — "a test that could FAIL if the key were ignored."** ⭐ §5's rule made executable.
> ⛔ **Every value remains `<PLACEHOLDER>`. This table names OBSERVABLES, not numbers.**

| key | parent | shared/delivery | 🔴 depends on | telemetry field | 🔴 VALIDATION TEST — goes RED if the key is ignored |
|---|---|---|---|---|---|
| `delivery.max_concentration_pct` | `position_sizing.max_concentration_pct` | **delivery** | ⭐ **nothing upstream — it is the top of the binding chain today** *(§8.2)* | `qty_by_concentration` → `candidate_qty` · **`binding_constraint='concentration'`** → `limiting_rule` ✅ **in the agreed set** | **Size a DELIVERY signal with the twin at a value strictly tighter than the global, and an INTRADAY signal on the same symbol/price in the same run. RED unless `qty_by_concentration` DIFFERS between them.** ⭐ Cannot pass vacuously: `:424` reads the global raw today, so the test fails on current code |
| `delivery.tier_multipliers` | `position_sizing.tier_multipliers` | **delivery** | 🔴 **G2** *(Shape-2 co-requirement)* · 🔴 **`delivery.max_concentration_pct`** *(§8.1)* · the FIX-133 floor | `tier_weight_applied` · `tiered_qty` · **`delta_qty`** ✅ **in the agreed set** | ⚠️ **PRECONDITION `raw_qty ≥ 2`, asserted in the test itself.** Set the delivery tier ≠ global; **RED unless `tier_weight_applied` differs by pipeline AND `tiered_qty` moves.** ⛔ **A fixture at `raw_qty = 1` passes green while the key does nothing — 111/483 of production is that case** |
| `delivery.daily_loss_limit_pct` | `risk.daily_loss_limit_pct` | **delivery** | the 70/30 bucket split · ⭐ **the EXIT-day time basis** *(§6.1)* | 🔴 **NOT in the agreed ladder set** — it is `ApprovalResult.failed_check = 'DAILY_LOSS'` + the `fm_ledger` day figure. **Named, not assumed** | **Drive delivery realised P&L past the delivery limit while total P&L stays inside the global limit. RED unless a DELIVERY entry is refused with `failed_check='DAILY_LOSS'` AND an INTRADAY entry in the same run is APPROVED.** ⭐ The intraday leg is what makes it non-vacuous — it proves *isolation*, not merely *a limit* |
| `delivery.strategy_circuit_breaker.cutoff_time` | `strategy_circuit_breaker.cutoff_time` | **delivery** | `trading_hours.*` · the carry horizon | 🔴 **NOT in the ladder set** — the breaker's own pause record | **Set the delivery cutoff after the global 12:00 and submit a delivery signal at 12:30. RED unless it is admitted while an intraday signal at 12:30 is paused** |
| `delivery.min_qty_threshold` | `position_sizing.min_qty_threshold` | **delivery** | 🔴 **the FIX-133 floor** *(§8.3)* · `lot_size` | `constraint='BELOW_MIN'` → `limiting_rule` ✅ in set | **Set delivery threshold = 2 with `raw_qty` yielding `final_qty = 1`. RED unless the DELIVERY signal is refused `BELOW_MIN` and the intraday one is not.** ⚠️ **§8.3: the floor lifts to 1 first — if the test does not exercise the floor's output it tests nothing** |
| `delivery.max_carry_days` | 🆕 none | **delivery-only** | 🔴 **`max_open_delivery_positions`** *(§8.1)* · the T+1 `holdings()` transition · ⛔ **F6** *(§8.4)* | 🆕 **none exists** — ⛔ **must be created; NOT in the ladder set** *(the ladder is a sizing instrument; this is a lifecycle one)* | **Age a delivery trade past the limit in the store; RED unless a close/alert is raised on that cycle and NOT one cycle earlier.** ⛔ **BLOCKED BY F6 (§8.4) — the exit path cannot close a delivery trade today, so a close-side test cannot go green for the right reason** |
| `delivery.carry_countdown_alert_days` | 🆕 none | **delivery-only** | `max_carry_days` — **it is the countdown TO it** | 🆕 none exists | **RED unless exactly one alert fires at N days remaining and none at N+1** |
| `delivery.alerts.capital_starvation_enabled` | 🆕 none | **delivery-only** | ⭐ **the sizer's ALREADY-COMPUTED discarded breakdown** *(`:449-464`, `:600-614`, `:616+`)* | ⭐ **`constraint` + `reason` + `breakdown` — all three already exist and are DISCARDED.** The dependency is on **exposure**, not construction | **Starve the delivery bucket and submit a qualifying signal. RED unless ONE alert carries strategy·symbol·qty·entry/SL/TGT·score.** 🔴 **AND a negative half: a signal rejected for a NON-capital reason must produce NO alert** — ⛔ without it the test passes by alerting on everything |
| `delivery.entry_cutoff` | ⛔ **not `entry_start`/`entry_end`** — one boundary, different concept | **delivery-only** | `trading_hours.*` · **the observability window before an unattended carry** | 🆕 none exists | **Submit a delivery signal one minute either side of the cutoff. RED unless one is admitted and one refused, while intraday admits both** |
| `delivery.min_score` | `scoring_weights.min_pass_score` | **delivery** | 🔴 **G2 — and the 65 ceiling** *(§9.1)* | ⛔ **design exists elsewhere — Q10 Part A. NOT designed here** | ⛔ **Owed to Q10 Part A. §9.1 records the constraint only** |

## §8.1 · ⭐ `max_carry_days` ↔ `max_open_delivery_positions` — **THE THROUGHPUT RELATION**
> **`throughput ≈ slots ÷ carry_days`.** **At 3 slots and 4 days that is ~0.75 new delivery positions
> per day.**
🔴 **That may sit BELOW what the strategies generate — and it would present as *"few delivery
signals"*, not as a cap.** ⛔ **State this relation wherever EITHER value is set**, in both places.
⭐ **And §2.5 makes it live today: with the phantom holding a slot, the effective numerator is 2, not
3 — ~0.5/day.**

## §8.2 · ⭐ concentration ↔ the tier's observable effect
**While `qty_by_concentration` returns 1, the tier is INVISIBLE** — `floor(1 × 0.5) = 0 → 1`.
⇒ **the tier twin's validation test DEPENDS on the concentration twin admitting `raw_qty ≥ 2`.**
⛔ **Test the tier before concentration and it will look broken. Order is not optional here.**

## §8.3 · ⭐ `min_qty_threshold` ↔ the FIX-133 floor
The floor `max(1, min(tiered_qty, raw_qty × 2))` (`:530`) runs **before** the `BELOW_MIN` check
(`:614`). ⇒ **the floor guarantees `tiered_qty ≥ 1`, so a threshold of 1 can never fire.** ⛔ **A
delivery `min_qty_threshold` is only observable at ≥ 2** — its test must assert that precondition.

## §8.4 · 🔴 **`max_carry_days` IS BLOCKED BY F6 — a dependency on a DEFECT, not on a key**
`f6_delivery_exit_abs_defect_06aug`: `:464`'s `abs()` ⇒ `held=1`, and `held==0` is the only door to
release ⇒ **a delivery trade never closes.** ⇒ **a `max_carry_days` close-side test cannot go green
for the right reason while F6 stands.** ⛔ **Recorded as a build-order constraint. F6 lands first.**

## §8.5 · ⛔ §3.3 APPLIED — **"a key with no dependencies is suspicious"**
Every row above carries at least one dependency **except `delivery.max_concentration_pct`.**
⭐ **Re-checked, and the emptiness is REAL and is itself the finding:** concentration binds
**483/483**, so nothing upstream constrains it — **it IS the top of the chain.** ⛔ **But it is not
free of dependents:** the tier (§8.2), `min_qty_threshold` (§8.3) and the starvation alert all
depend on **it**. ⇒ **the column is empty upward and full downward.** ⭐ **That is why it is surface
item 1: the only key whose test cannot be blocked by another key's absence.**

---

# §9 · THE THREE ROUND-6 ITEMS, LANDED

## §9.1 · `delivery.min_score` — **ON THE SURFACE, ⛔ NOT DESIGNED HERE**
**Surface item 8 · `delivery.min_score` · *delivery-only control; DESIGN EXISTS ELSEWHERE*.**
⇒ **`docs/audit/regime_thesis_minscore_control_18jul2026.md` — Q10 Part A.** ⛔ **Do not design it
twice.** ⭐ It appears here because **absence reads as an oversight, and Rama asked for it explicitly.**
> ⚠️ **THE CONSTRAINT, RECORDED AT THE KEY:** **(P)** the highest score ever produced in **72,755**
> scored signals is **65** *(`config/system_config.yaml:507 min_pass_score: 60`;
> `scoring_weights.yaml:22`)*. ⛔ **A threshold is only meaningful inside 0–65** — above 65 it admits
> nothing, which is indistinguishable from the pipeline being off.

## §9.2 · **THE 98-KEY EXCLUSION IS A SCOPE BOUNDARY, NOT A CLOSED QUESTION**
Excluding signal-generation *(`sr_detector` · `regime` · `v3_chain` · `watchlist`, **including G2**)*
is **defensible**: those keys decide **whether a signal EXISTS**, not how it is sized.
🔴 **But both pipelines share the ENTIRE signal-generation surface**, and *"one system, two isolated
pipelines"* **is not satisfied by isolating sizing alone.**
> ### ⛔ **FOLLOW-UP NAMED, NOT ANSWERED**
> **"Do the 98 signal-generation keys need pipeline isolation, and if so does 'two isolated
> pipelines' mean two scorers?"** ⛔ **Out of scope for item 5. Not answered here.**
> ⭐ **Its urgency is already established elsewhere: the tier twin depends on G2, which lives inside
> the 98.**

## §9.3 · **H5 MOVED** — see §7. Out of DELIVERY TWIN; **owed to Rama as a ruling**, and §7.5 widens
what the ruling must cover from one keyed gate to three gates, two of which have no key.

---

# OPEN QUESTIONS — ⛔ UNANSWERED

1. **Do `max_carry_days` and `max_open_delivery_positions` need a joint contract?** §2 shows they are
   two axes of one constraint; designed apart, the tighter silently bounds the other.
2. **Should the slot cap ignore trades with no broker position?** ⭐ §2.5 shows the phantom holds a
   slot — ⛔ but that is arguably F6's bug to fix, **not the cap's semantics to change.**
3. **Bucket or total for the delivery loss limit** — and does an intraday loss still count toward it?
4. **Is `entry_cutoff` one boundary or two** (earliest as well as latest)?
5. **Should the delivery surface start EMPTY or inherit intraday defaults?** ⭐ The two existing twins
   are `null` and inert; a third costs the same as the first two.
6. **Does `one_trade_per_symbol_direction_per_day` become per-pipeline, or is the H5 leak the bug?**
7. **What happens to `INTENTIONALLY UNAVAILABLE` keys in the code** — absent, or present-and-rejected
   with a reason? ⭐ The second is discoverable; the first is not.
8. **Where does the reason live for a SHARED FOREVER key** — the yaml comment, the check site, or
   both? `max_consecutive_losses` puts it at the check.
