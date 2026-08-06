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

### 7 · Everything else — a twin, a declared reason, or intentionally unavailable *(§3)*

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

### DELIVERY TWIN — 34
| keys | reason |
|---|---|
| 🔴 `max_concentration_pct` | **binds 483/483, no twin** — surface item 1 |
| 🔴 `tier_multipliers` (3) | **binds 372/483, no twin** — surface item 2 |
| `daily_loss_limit_pct` · `daily_loss_include_unrealized` | bucket denominator — surface item 3 |
| `max_sector_exposure_pct` · `sector_cap_mode` · `sector_unknown_alert_pct` | a days-long sector exposure is a different risk from a session's |
| `entry_gate.*` (9) | same concept, **values tuned for intraday fills** |
| `strategy_circuit_breaker.*` (4) | `cutoff_time: 12:00` is an intraday clock; the concept survives, the unit does not |
| `risk_per_trade_pct` · `max_position_value_pct` + their 2 existing twins | ⚠️ **twins already exist and are inert** — kept in-bucket for completeness, ⛔ **excluded from the surface (§4.1)** |
| `max_open_positions` · `max_daily_trades` + their 2 existing twins | ✅ **already isolated** |
| `min_qty_threshold` · `lot_skew_rejection_threshold` | rounding policy differs when one share is the whole position |
| `tgt_retry.*` (4) | retry cadence on a days horizon |
| `price_drift_threshold` · `one_trade_per_symbol_direction_per_day` | *(register H5 — the second is currently a cross-pipeline leak)* |

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
