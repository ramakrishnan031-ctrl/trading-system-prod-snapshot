# A-DEC-3 ORDER ITEM 4 — STEP 1 SURVEY: **WHAT CONTROL INVENTORY ALREADY EXISTS?**

> ## ⛔ SURVEY ONLY. **NOTHING IS ANNOTATED, DESIGNED OR BUILT HERE.**
> ⛔ No delivery value is proposed for any key. ⛔ No row-shape is invented. ⛔ No implementation.
> **231 stands** — this creates no register row.

**Session:** 05-Aug-2026, ~13:46–15:5x IST. Docs-only; no VM, broker or live-DB contact.
**Why this item:** item 5 (the delivery configuration surface) is **hard-gated on item 4**, and item
4's own instruction is a prohibition before it is a task — ***"EXTEND THE EXISTING INVENTORY — DO NOT
CREATE A SECOND CONTROL INVENTORY."*** ⇒ **Step 1 is finding out what exists.**

---

## §1 — 🔴 THE HEADLINE: **"PART A" DOES NOT EXIST — AND TWO CONTROL INVENTORIES DO**

### 1.1 There is no artifact called "Part A"
**Width:** `git ls-files | xargs grep -ln "Part A"` over **every tracked file** → **12 files**.
Of those, **11 are unrelated** (code comments about "Part A" of a specific fix, in
`broker/order_monitor.py`, `core/state_store.py`, `orders/order_placer.py`,
`screening/entry_gate.py`, 3 test files, and 3 older web_claude docs).
The **12th** is `docs/audit/regime_thesis_minscore_control_18jul2026.md`, titled
***"Q10 Part A — the per-day active min-score control"*** — ⛔ **a different Part A entirely: a
min-score analysis, not a control inventory.**

⇒ ⭐⭐ **THE REGISTER'S THREE REFERENCES TO *"the Part A inventory"* / *"the Part A control
inventory"* (`MASTER_PENDING` `:368`, `:1119`, and §B#7 `:763`) RESOLVE TO NOTHING BY THAT NAME.**
**CLASSIFICATION: (c) ASSUMPTION DISPROVED.** ⛔ **Item 4 as written names a subject that does not
exist.** *(The instruction's INTENT is sound and untouched — see §1.3 for what it should point at.)*

### 1.2 What DOES exist — **INVENTORY #1**, and it is the closer match
**`docs/audit/audit_05jul2026.md:527` — `### 6.1 What exists (control inventory)`**, inside
`## PHASE 6 — RISK & CAPITAL` (`:525`). *(This file is register source **S6**, already designated
**"READ-ONLY POINTER… cited only, never re-enumerated, never deleted."**)*

- **Shape:** a markdown table, **27 controls**, numbered 1–27.
- **Columns:** `# | Control | Key (value) | Enforcement point | Stage`
- **Identified by:** a row number + a human-readable name + (usually) a dotted config key.
- **Coverage:** kill-switch, sizing, bucket capital, max open/daily, delivery caps, consecutive
  losses, both daily-loss mechanisms, unrealized MTM, sector exposure, contrary/duplicate,
  risk-per-trade, concentration, position-value cap, leverage, qty guards, 70/30, strategy circuit
  breaker, entry throttle, the three-balance invariant, **both drift handlers**, API-failure trip,
  EOD squareoff.

#### 🔴 WHICH OF THE FIVE AXES IT CARRIES — **NONE, AS AXES**
| axis | present as a column? | reality |
|---|---|---|
| **SCOPE** (global · pipeline-local · strategy-local) | ⛔ **NO** | inferable only from prose — row 6 *"Delivery caps (dormant)"*, row 20 *"Per-strategy"* |
| **BASIS** (notional · margin · realised cash · count) | ⛔ **NO** | leaks inline in **some** rows — row 15 *"NOTIONAL, total-relative"*, row 11's exposure is a `margin_reserved` SUM. **Absent from most.** |
| **DENOMINATOR** (total · bucket · position) | ⛔ **NO** | same — *"× total"*, *"total-relative"* appear in prose where someone happened to write them |
| **MODE** (observe · enforce) | ⛔ **NO** | leaks into the **`Stage`** column — row 10 *"pre-trade (advisory)"*, row 24 *"alert-only"* |
| **WHY** (the incident) | ⛔ **NO** | FIX-ids appear inline in a minority of rows |
| **STATUS** (active · placeholder · reserved) | ⛔ **NO** | leaks into the **`Control` NAME** — row 6 *"Delivery caps (dormant)"*, row 10 *"(SHADOW)"* |

⭐ **It has a sixth thing the five axes do not name: `Stage`** — lifecycle position (`pre-trade` ·
`sizing` · `reserve` · `post-close` · `post-hoc` · `backstop` · `every mutation`). **That is genuinely
useful and item 4 should not discard it.**

#### 🔴🔴 **CAN IT EXPRESS `ENFORCE + PLACEHOLDER`? — NO, AND FOR THE EXACT REASON A-DEC-3 §1 PREDICTED**
**MODE and STATUS are both absent as axes — and worse, the ad-hoc markers that stand in for them sit
in THE SAME FIELDS.** Row 6 puts *"(dormant)"* — a STATUS — inside the **Control name**; row 10 puts
*"(SHADOW)"* in the name **and** *"advisory"* — a MODE — in **Stage**. ⇒ **the two axes are conflated
into free text, in the same cells.**
⇒ ⛔ **The dangerous cell is literally unnameable in this artifact**, which is A-DEC-3 §1's own
sentence — *"Collapse them and that cell is unnameable"* — **confirmed against the real document.**
**CLASSIFICATION: (a) CONFIRMED DEFECT** *(a gap in the inventory, not in the system).*

#### ⚠️ AND ITS LINE CITES HAVE ROTTED — **it needs RE-MEASUREMENT, not just annotation**
Spot-checked 4 cites at HEAD (**M3**): **3 of 4 have moved**, and they do not land on near-misses —
they land on unrelated code.
| row | inventory cite (05-Jul) | at HEAD |
|---|---|---|
| 16 · position-value cap | `position_sizer.py:476-506` | ⛔ **lands in a FIX-133 floor comment**; the cap is at **`:585`** |
| 14 · risk-per-trade | `position_sizer.py:333-334` | ⛔ **lands in live-leverage logging**; `qty_by_risk` is at **`:382`** |
| 24 · G3 broker-vs-local drift | `order_reconciler.py:2883-3015` | ⛔ **lands in a BANSALWIRE docstring**; G3 is at **`:3590-3691`** |
| 23 · drift tiers | `drift_handler.py:65-69` | ✅ **HELD** — `_ESCALATING_SOURCES` at `:66-70` |
⇒ **CLASSIFICATION: (a) CONFIRMED DEFECT.** ⭐ **This is §M3 on a document rather than on a card: the
cites are true at their measured SHA and nowhere else. Item 4 inherits a re-measurement cost it did
not know it had.**

### 1.3 ⭐⭐ **INVENTORY #2 — AND IT IS BETTER ON THE ONE AXIS THAT MATTERS MOST**
**`ops_dashboard/docs/G2a_capacity_inventory.md`** — *"G2a — Capacity Inventory: Configured Limits ↔
Live Counters"*, dated **03-Jul-2026**.

- **Shape:** sectioned markdown tables, **~40 rows**.
- **Columns:** `# | config key (dotted) | scope | meaning | LIVE COUNTER source | Remaining formula | v1?`
- ⭐ **KEYED ON THE DOTTED CONFIG KEY** — a stabler identifier than inventory #1's human name.
- ⭐⭐ **IT HAS A `scope` COLUMN**, already populated: `global / day` · `global / concurrent` ·
  `global / instant` · `per-trade`. **That is the SCOPE axis, partially present and already in use.**
- ⭐⭐ **IT HAS A LIVE CONSUMER: `ops_dashboard/backend/services/capacity.py`** ⇒ **it is not merely a
  document — it describes something that EXECUTES**, and drifting from it has a visible effect.
- It carries a **"Capital basis"** preamble (day-opening capital from `fm_ledger INIT.balance_after`)
  — ⭐ **that is a DENOMINATOR statement, made once at the top rather than per row.**
- **Overlaps inventory #1 substantially** — e.g. its row 28 covers `order_reconciler.capital_drift_
  tolerance` / `_pct` / `human_order_margin_tolerance`, which is inventory #1's row 24.

> ## 🔴🔴 **THEREFORE THE ANTI-DUPLICATION QUESTION IS ALREADY ANSWERED, AND THE ANSWER IS "TWO EXIST".**
> Item 4 says *"EXTEND THE EXISTING INVENTORY — DO NOT CREATE A SECOND"*. **There are already two, they
> overlap, they use different identifiers, different columns and different scopes, and NEITHER is
> called "Part A".**
> ⇒ ⛔ **ITEM 4 MUST RECONCILE BEFORE IT EXTENDS.** Annotating either one in isolation would produce
> a **third** authority — **the multi-authority defect (§B#7) committed by the very work meant to
> cure it**, which is precisely what item 4's prohibition exists to prevent.
> ⛔ **WHICH ONE SURVIVES, OR WHETHER THEY MERGE, IS A DESIGN DECISION AND IS NOT TAKEN HERE.**

### 1.4 Further listers found — ⚠️ **named, not assessed**
**Width:** `git ls-files | xargs grep -ln "capital_drift_tolerance"` (a control that must appear in
any real inventory), plus a sweep for `"What exists (inventory)"` across `docs/`.
- **`docs/audit/audit_05jul2026.md:160` — `§2.1 What exists (inventory)`**: a **CONFIG-FILE**
  inventory (which files exist, how many lines, who loads them). ⚠️ **A sibling in NAME but not in
  SUBJECT** — it inventories files, not controls. **Not a duplicate.**
- **`ops_dashboard/backend/services/capacity.py`** — **executing code** that surfaces limits to the
  GUI. ⇒ **a THIRD representation of the same facts, and the only one that runs.**
- **`docs/locked_decisions.yaml`** — 3,067 lines, **181 LOCKED decisions**. ⚠️ **Not an inventory, but
  it may bind values that any annotation must respect.** ⛔ **Not opened in this survey.**
- **`config/system_config.yaml`** itself — carries a `[LAUNCH-PHASE]`/`[PERMANENT]` tagging convention
  at `:10-19`. ⭐ **That is a STATUS-like axis living in the config file itself, and it is a fourth
  place the same information is expressed.** ⛔ Not assessed here.

---

## §2 — THE GAP LIST — ⛔ **NAMED, NOT FILLED**

**(a) Axes missing from inventory #1:** **all five**, plus STATUS. See the table in §1.2 — they exist
only as prose in a minority of rows, and MODE/STATUS are conflated.
**(b) Axes missing from inventory #2:** BASIS, MODE, WHY, and STATUS. ⭐ **SCOPE is present**;
DENOMINATOR is present **once, globally**, not per control.

**(c) 🔴 CONTROLS THAT EXIST IN THE SYSTEM BUT ARE ABSENT FROM THE INVENTORY — the MODIFIERS.**
The register already names two, and this survey confirms neither appears in either inventory:
- **the tier multiplier** — applied **after** the caps (`raw_qty × tier_multiplier`), measured at a
  permanent **0.5 on 438/438 trades**;
- **`dynamic_by_winrate`** (min 0.5 / max 2.0).
⭐ **The taxonomy has no home for MODIFIERS at all: the five axes describe CAPS.** A modifier is not a
cap — it does not reject, it *scales* — so it cannot be filed as one.
⛔⛔ **NO ROW-SHAPE IS INVENTED HERE. The gap is real and its shape is an OPEN DESIGN QUESTION.**

**(d) ⚠️ One already-measured live instance of why the annotation matters — CITED, NOT RE-MEASURED:**
`position_sizer.py:585` **enforces** `eff_max_position_value_pct` while `:596` and `:609` **report**
`self._max_position_value_pct` — the global. **Identical today only because the delivery override is
`null`.** *(Record: `docs/audit/check1_product_skip_step1_05aug2026.md`.)*

**(e) ⭐ AND A SECOND ONE FOUND TODAY, WHICH THE INVENTORY *ALMOST* CAUGHT:** inventory #1's row 24
already records the G3 drift control **and** already notes *"source `order_reconciler` = non-
escalating"* — ⇒ **half of this morning's finding was in the inventory the whole time.** ⛔ **What it
does NOT carry is SCOPE and DENOMINATOR** — and those are exactly what would have made visible that
its 10% band is an **intraday-leverage calibration** applied to an unlevered delivery book.
⭐⭐ **That is the single strongest argument for item 4 that this survey produced: the annotation is
not bookkeeping — the missing axes are the ones that would have predicted a live CRITICAL.**

---

## §3 — WHAT THIS SURVEY DID **NOT** DO

⛔ No annotation written · ⛔ no inventory extended, merged or created · ⛔ no delivery value proposed
for any key · ⛔ no row-shape invented for modifiers · ⛔ no decision taken on which inventory
survives · ⛔ `locked_decisions.yaml` and the `system_config.yaml` tagging convention not assessed ·
⛔ §2 of the instruction card (the raw intraday config enumeration) **not started** — it is gated on
this survey being committed first.

## ❓ OPEN, FOR ITEM 4 PROPER — ⛔ NOT ANSWERED HERE
1. **Which inventory is the authority** — #1 (27 controls, richer enforcement detail, rotted cites) or
   #2 (~40 rows, dotted keys, a `scope` column, and a live consumer)? Or a merge?
2. **What is the row-shape for a MODIFIER**, given the five axes describe caps?
3. **Does `locked_decisions.yaml` bind any value an annotation would touch?**
4. **Is `system_config.yaml`'s `[LAUNCH-PHASE]`/`[PERMANENT]` convention the STATUS axis already**,
   in the wrong place — or a fifth authority?

---

# §2 — THE RAW CONFIG ENUMERATION *(appendix to the item-4 step-1 survey)*

> ## ⛔⛔ **RAW LIST ONLY. NO CLASSIFICATION. NOT ONE PROPOSED VALUE.**
> ⛔ **No delivery twin is suggested for any key. No key is marked "transfers" / "does not apply".**
> **That classification — transfers as-is · transfers with different semantics · does not apply ·
> new-and-delivery-only — is ITEM 5's work, it is gated on item 4, and Rama has said it is a
> DISCUSSION he wants to have, not an output handed to him.**

### ⚠️ ONE DELIBERATE DEPARTURE FROM THE INSTRUCTION, STATED RATHER THAN MADE SILENTLY
The card asked for *"every **INTRADAY-applicable** setting"*. ⛔ **Deciding which settings are
intraday-applicable IS the classification item 5 owns** — filtering here would pre-empt exactly the
discussion the card protects, and would do it invisibly, by omission. ⇒ **every leaf key is listed,
and NONE is marked.** *(If a narrower list is wanted, it should be produced by the classification
step, not before it.)*

### METHOD, so the list is reproducible
- **Keys:** every leaf key carrying a value in `config/system_config.yaml` (651 lines), extracted by
  indentation-walk → **302 leaf entries · 271 distinct key names**.
- **Consumers:** one pass over **all tracked `*.py` excluding `tests/`, `ops_dashboard/`, and
  `core/config_loader.py`** — the loader *declares* the schema, so counting it would answer
  "declared" when the card asked for **"consumed"**. First hit shown; total site count in the last
  column.
- ⛔⛔ **A SUBSTRING NAME-MATCH IS A WEAK CONSUMER TEST, AND THE COLUMN IS LABELLED ACCORDINGLY —
  "first production **name-match**", NOT "consumer".** It over-counts (any comment mentioning the
  word scores a hit) and can under-count (a key reached via `getattr`/dict access).
- 🔴 **57 of 302 rows are marked `⛔ AMBIGUOUS`** — their leaf name is generic (`enabled`, `fallback`,
  `timeout_sec`, `poll_interval_sec`…) and matches **>25 sites**, so a "first hit" would be
  **meaningless and would read as evidence.** ⭐ **They are marked rather than shown, deliberately:
  a misleading cell is worse than an empty one** *(§V5 — a value that cannot fail is not a
  measurement; here, a first-hit that is almost certainly wrong is not a consumer)*.
  ⇒ **Item 4 must resolve those 57 by their DOTTED PATH through the config object, not by name.**

### ⚠️ 11 KEYS HAVE NO PRODUCTION NAME-MATCH — ⛔ **NOT a claim they are dead**
`auto_resume_kill_switch` · `entry_tf` · `max_multiplier` · `min_multiplier` · `multi_account_mode` ·
`personal_chat_id_env` · `pipeline_timeout_sec` · `playbook_scanner` ·
`reconnect_backoff_base_seconds` · `reconnect_backoff_max_seconds` · `whitelist_only`
⛔ **Each needs its own check before anyone calls it dead** — the width above cannot see `getattr` or
dict access. ⭐ **This is the IA-P7-04 "dead knob" shape and it is recorded, not resolved.**
⚠️ **Note `min_multiplier` / `max_multiplier` are `dynamic_by_winrate`'s bounds — one of the two
MODIFIERS the survey found absent from both inventories.** ⛔ Coincidence noted, not interpreted.

### 🔴 AND THE FLAG THE CARD ASKED FOR, RAISED AND NOT RESOLVED
The register already holds that **`entry_start` / `entry_end` / `eod_entry_cutoff` and the timeout
family are INTRADAY-SHAPED CONCEPTS** — a delivery position is held for **days**, so a delivery
equivalent would be **a new setting, not a renamed one.** ⛔ **Flagged here; not resolved, not
designed, and no delivery-side name is proposed.**

| # | key path | value (yaml line) | first production name-match | sites |
|---|---|---|---|---|
| | **`broker:`** | | | |
| 1 | `broker.primary` | `"zerodha"` <sub>(:22)</sub> | ⛔ **AMBIGUOUS — `primary` is a generic name** | 56 |
| 2 | `broker.fallback` | `"angelone"` <sub>(:23)</sub> | ⛔ **AMBIGUOUS — `fallback` is a generic name** | 217 |
| 3 | `broker.fallback_enabled` | `false` <sub>(:24)</sub> | `broker/angelone_adapter.py:12` | 2 |
| 4 | `broker.multi_account_mode` | `false` <sub>(:25)</sub> | ⚠️ **none by name** | — |
| | **`trading_hours:`** | | | |
| 5 | `trading_hours.entry_start` | `"10:00"` <sub>(:28)</sub> | ⛔ **AMBIGUOUS — `entry_start` is a generic name** | 29 |
| 6 | `trading_hours.entry_end` | `"15:00"` <sub>(:47)</sub> | ⛔ **AMBIGUOUS — `entry_end` is a generic name** | 32 |
| 7 | `trading_hours.eod_entry_cutoff` | `"15:15"` <sub>(:48)</sub> | `core/market_windows.py:57` | 10 |
| 8 | `trading_hours.eod_squareoff_time` | `"15:17"` <sub>(:49)</sub> | `core/config_auditor.py:566` | 10 |
| 9 | `trading_hours.market_open` | `"09:15"` <sub>(:50)</sub> | ⛔ **AMBIGUOUS — `market_open` is a generic name** | 77 |
| 10 | `trading_hours.market_close` | `"15:30"` <sub>(:51)</sub> | ⛔ **AMBIGUOUS — `market_close` is a generic name** | 44 |
| 11 | `trading_hours.service_window_end` | `"17:35"` <sub>(:59)</sub> | `main.py:1044` | 10 |
| | **`signal_queue:`** | | | |
| 12 | `signal_queue.capacity` | `300` <sub>(:72)</sub> | ⛔ **AMBIGUOUS — `capacity` is a generic name** | 35 |
| 13 | `signal_queue.backpressure_pct` | `0.80` <sub>(:73)</sub> | `signals/webhook_receiver.py:542` | 1 |
| 14 | `signal_queue.expiry_sec` | `600` <sub>(:74)</sub> | `core/config_validator.py:12` | 13 |
| 15 | `signal_queue.warning_pct` | `0.60` <sub>(:75)</sub> | `scripts/disk_monitor.py:53` | 4 |
| | **`force_intraday_only:`** | | | |
| 16 | `force_intraday_only` | `false` <sub>(:89)</sub> | ⛔ **AMBIGUOUS — `force_intraday_only` is a generic name** | 70 |
| | **`trade_type:`** | | | |
| 17 | `trade_type` | `BOTH` <sub>(:96)</sub> | ⛔ **AMBIGUOUS — `trade_type` is a generic name** | 86 |
| | **`delivery_enabled:`** | | | |
| 18 | `delivery_enabled` | `true` <sub>(:102)</sub> | ⛔ **AMBIGUOUS — `delivery_enabled` is a generic name** | 38 |
| | **`product_map:`** | | | |
| 19 | `product_map.zerodha.INTRADAY` | `"MIS"` <sub>(:106)</sub> | ⛔ **AMBIGUOUS — `INTRADAY` is a generic name** | 143 |
| 20 | `product_map.zerodha.DELIVERY` | `"CNC"` <sub>(:107)</sub> | ⛔ **AMBIGUOUS — `DELIVERY` is a generic name** | 108 |
| 21 | `product_map.zerodha.COVER_ORDER` | `"CO"` <sub>(:108)</sub> | `broker/product_resolver.py:11` | 18 |
| 22 | `product_map.zerodha.BRACKET_ORDER` | `""` <sub>(:109)</sub> | `broker/product_resolver.py:11` | 12 |
| | **`mis_filter:`** | | | |
| 23 | `mis_filter.enabled` | `true` <sub>(:126)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 24 | `mis_filter.shadow` | `true` <sub>(:127)</sub> | ⛔ **AMBIGUOUS — `shadow` is a generic name** | 224 |
| 25 | `mis_filter.ttl_days` | `1` <sub>(:128)</sub> | `core/mis_blocklist.py:68` | 6 |
| | **`clock:`** | | | |
| 26 | `clock.warn_skew_sec` | `2.0` <sub>(:131)</sub> | `main.py:368` | 1 |
| 27 | `clock.alert_skew_sec` | `5.0` <sub>(:132)</sub> | `main.py:369` | 1 |
| 28 | `clock.halt_skew_sec` | `30.0` <sub>(:133)</sub> | `main.py:370` | 1 |
| 29 | `clock.startup_max_skew_sec` | `30.0` <sub>(:134)</sub> | `main.py:371` | 3 |
| 30 | `clock.probe.enabled` | `true` <sub>(:136)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 31 | `clock.probe.probe_interval_sec` | `60` <sub>(:137)</sub> | `broker/clock_skew_probe.py:6` | 3 |
| | **`order_monitor:`** | | | |
| 32 | `order_monitor.poll_interval_sec` | `2` <sub>(:140)</sub> | ⛔ **AMBIGUOUS — `poll_interval_sec` is a generic name** | 46 |
| 33 | `order_monitor.fill_timeout_sec` | `60` <sub>(:141)</sub> | `broker/order_monitor.py:12` | 11 |
| | **`capital:`** | | | |
| 34 | `capital.intraday_bucket_pct` | `0.70` <sub>(:144)</sub> | `capital/fund_manager.py:279` | 9 |
| 35 | `capital.positional_bucket_pct` | `0.30` <sub>(:145)</sub> | `capital/fund_manager.py:280` | 7 |
| 36 | `capital.conditional_allocation_enabled` | `false` <sub>(:146)</sub> | `core/config_auditor.py:244` | 7 |
| 37 | `capital.slm_margin_buffer_pct` | `0.05` <sub>(:150)</sub> | `capital/fund_manager.py:304` | 2 |
| 38 | `capital.sl_limit_offset_pct` | `0.005` <sub>(:151)</sub> | ⛔ **AMBIGUOUS — `sl_limit_offset_pct` is a generic name** | 26 |
| 39 | `capital.gtt_sl_limit_offset_pct` | `0.03` <sub>(:152)</sub> | `main.py:2673` | 7 |
| 40 | `capital.emergency_exit_buffer_pct` | `0.01` <sub>(:153)</sub> | `capital/kill_switch.py:47` | 13 |
| 41 | `capital.leverage_map.INTRADAY` | `5.0` <sub>(:155)</sub> | ⛔ **AMBIGUOUS — `INTRADAY` is a generic name** | 143 |
| 42 | `capital.leverage_map.COVER_ORDER` | `6.0` <sub>(:156)</sub> | `broker/product_resolver.py:11` | 18 |
| 43 | `capital.leverage_map.DELIVERY` | `1.0` <sub>(:157)</sub> | ⛔ **AMBIGUOUS — `DELIVERY` is a generic name** | 108 |
| 44 | `capital.leverage_map.BRACKET_ORDER` | `5.0` <sub>(:158)</sub> | `broker/product_resolver.py:11` | 12 |
| | **`position_sizing:`** | | | |
| 45 | `position_sizing.enabled` | `true` <sub>(:161)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 46 | `position_sizing.risk_per_trade_pct` | `0.01` <sub>(:166)</sub> | `capital/position_sizer.py:81` | 24 |
| 47 | `position_sizing.max_concentration_pct` | `0.10` <sub>(:167)</sub> | `capital/position_sizer.py:83` | 14 |
| 48 | `position_sizing.min_qty_threshold` | `1` <sub>(:168)</sub> | `capital/position_sizer.py:130` | 5 |
| 49 | `position_sizing.lot_skew_rejection_threshold` | `0.25` <sub>(:169)</sub> | `capital/position_sizer.py:134` | 5 |
| 50 | `position_sizing.min_tick_size` | `0.05` <sub>(:170)</sub> | `capital/position_sizer.py:135` | 10 |
| 51 | `position_sizing.max_single_order_qty` | `10000` <sub>(:171)</sub> | `capital/position_sizer.py:136` | 5 |
| 52 | `position_sizing.max_position_value_pct` | `0.40` <sub>(:177)</sub> | ⛔ **AMBIGUOUS — `max_position_value_pct` is a generic name** | 27 |
| 53 | `position_sizing.tier_multipliers.HIGH` | `1.0` <sub>(:179)</sub> | ⛔ **AMBIGUOUS — `HIGH` is a generic name** | 112 |
| 54 | `position_sizing.tier_multipliers.MEDIUM` | `0.70` <sub>(:180)</sub> | ⛔ **AMBIGUOUS — `MEDIUM` is a generic name** | 51 |
| 55 | `position_sizing.tier_multipliers.LOW` | `0.50` <sub>(:181)</sub> | ⛔ **AMBIGUOUS — `LOW` is a generic name** | 121 |
| 56 | `position_sizing.dynamic_by_winrate` | `true` <sub>(:182)</sub> | `main.py:2522` | 1 |
| 57 | `position_sizing.min_multiplier` | `0.5` <sub>(:183)</sub> | ⚠️ **none by name** | — |
| 58 | `position_sizing.max_multiplier` | `2.0` <sub>(:184)</sub> | ⚠️ **none by name** | — |
| 59 | `position_sizing.delivery_risk_per_trade_pct` | `null` <sub>(:190)</sub> | `capital/position_sizer.py:147` | 5 |
| 60 | `position_sizing.delivery_max_position_value_pct` | `null` <sub>(:191)</sub> | `capital/position_sizer.py:148` | 5 |
| | **`risk:`** | | | |
| 61 | `risk.max_open_positions` | `5` <sub>(:203)</sub> | `allocation/portfolio_allocator.py:41` | 16 |
| 62 | `risk.max_daily_trades` | `10` <sub>(:204)</sub> | `capital/risk_engine.py:15` | 11 |
| 63 | `risk.max_open_delivery_positions` | `3` <sub>(:205)</sub> | `capital/risk_engine.py:148` | 3 |
| 64 | `risk.max_daily_delivery_trades` | `5` <sub>(:206)</sub> | `capital/risk_engine.py:149` | 3 |
| 65 | `risk.one_trade_per_symbol_direction_per_day` | `true` <sub>(:224)</sub> | `capital/risk_engine.py:162` | 4 |
| 66 | `risk.max_sector_exposure_pct` | `0.40` <sub>(:225)</sub> | `capital/risk_engine.py:15` | 7 |
| 67 | `risk.max_consecutive_losses` | `4` <sub>(:226)</sub> | `capital/risk_engine.py:16` | 8 |
| 68 | `risk.daily_loss_limit_pct` | `0.03` <sub>(:227)</sub> | ⛔ **AMBIGUOUS — `daily_loss_limit_pct` is a generic name** | 31 |
| 69 | `risk.sector_cap_mode` | `observe` <sub>(:232)</sub> | `capital/risk_engine.py:156` | 4 |
| 70 | `risk.sector_unknown_alert_pct` | `0.20` <sub>(:233)</sub> | `main.py:2730` | 6 |
| 71 | `risk.daily_loss_include_unrealized` | `false` <sub>(:234)</sub> | `capital/risk_engine.py:152` | 7 |
| 72 | `risk.price_drift_threshold` | `0.005` <sub>(:235)</sub> | `orders/order_placer.py:584` | 3 |
| | **`webhook:`** | | | |
| 73 | `webhook.bind_host` | `"0.0.0.0"` <sub>(:238)</sub> | `main.py:3542` | 2 |
| 74 | `webhook.bind_port` | `5000` <sub>(:245)</sub> | `main.py:3543` | 4 |
| 75 | `webhook.require_hmac` | `false` <sub>(:246)</sub> | `signals/webhook_receiver.py:132` | 17 |
| 76 | `webhook.dedup_window_seconds` | `300` <sub>(:247)</sub> | `signals/webhook_receiver.py:199` | 6 |
| 77 | `webhook.per_ip_rate_limit_enabled` | `true` <sub>(:250)</sub> | `signals/webhook_receiver.py:210` | 1 |
| 78 | `webhook.per_ip_burst` | `60` <sub>(:251)</sub> | `signals/webhook_receiver.py:212` | 1 |
| 79 | `webhook.per_ip_refill_per_sec` | `5.0` <sub>(:252)</sub> | `signals/webhook_receiver.py:213` | 1 |
| | **`signal_processor:`** | | | |
| 80 | `signal_processor.worker_count` | `5` <sub>(:255)</sub> | `main.py:3141` | 11 |
| 81 | `signal_processor.drain_poll_sec` | `0.1` <sub>(:256)</sub> | `main.py:3142` | 5 |
| 82 | `signal_processor.pipeline_timeout_sec` | `30` <sub>(:257)</sub> | ⚠️ **none by name** | — |
| 83 | `signal_processor.tgt_min_pct` | `0.003` <sub>(:258)</sub> | `main.py:3145` | 5 |
| 84 | `signal_processor.min_gap_between_entries_sec` | `20` <sub>(:260)</sub> | `main.py:3147` | 3 |
| 85 | `signal_processor.entry_burst_window_sec` | `60` <sub>(:261)</sub> | `main.py:3148` | 3 |
| 86 | `signal_processor.entry_burst_max` | `3` <sub>(:262)</sub> | `main.py:3149` | 3 |
| 87 | `signal_processor.per_symbol_cooldown_sec` | `300` <sub>(:263)</sub> | `main.py:3150` | 5 |
| | **`kill_switch:`** | | | |
| 88 | `kill_switch.api_failure_threshold` | `3` <sub>(:266)</sub> | `capital/kill_switch.py:22` | 7 |
| 89 | `kill_switch.enable_auto_trip` | `true` <sub>(:267)</sub> | `capital/kill_switch.py:23` | 7 |
| | **`eod_squareoff:`** | | | |
| 90 | `eod_squareoff.inter_order_delay_ms` | `500` <sub>(:270)</sub> | `main.py:2921` | 4 |
| 91 | `eod_squareoff.poll_interval_sec` | `5` <sub>(:271)</sub> | ⛔ **AMBIGUOUS — `poll_interval_sec` is a generic name** | 46 |
| 92 | `eod_squareoff.auto_resume_kill_switch` | `true` <sub>(:272)</sub> | ⚠️ **none by name** | — |
| 93 | `eod_squareoff.exit_protocol` | `"LIMIT_THEN_MARKET"` <sub>(:273)</sub> | `main.py:2926` | 7 |
| 94 | `eod_squareoff.limit_aggressive_pct` | `0.01` <sub>(:274)</sub> | `main.py:2927` | 6 |
| 95 | `eod_squareoff.limit_grace_sec` | `120` <sub>(:275)</sub> | `main.py:2928` | 8 |
| | **`alerts:`** | | | |
| 96 | `alerts.failed_alerts_log_path` | `"logs/failed_alerts.log"` <sub>(:278)</sub> | `alerts/telegram_notifier.py:12` | 4 |
| 97 | `alerts.sentinel_dir` | `"data_store"` <sub>(:279)</sub> | ⛔ **AMBIGUOUS — `sentinel_dir` is a generic name** | 82 |
| 98 | `alerts.watcher_max_attempts` | `5` <sub>(:280)</sub> | `scripts/alert_watcher.py:28` | 2 |
| 99 | `alerts.alert_digest_threshold` | `3` <sub>(:281)</sub> | `scripts/alert_watcher.py:485` | 1 |
| 100 | `alerts.watcher_lock_path` | `"data_store/alert_watcher…` <sub>(:282)</sub> | `scripts/alert_watcher.py:28` | 2 |
| 101 | `alerts.watcher_log_path` | `"logs/alert_watcher.log"` <sub>(:283)</sub> | `scripts/alert_watcher.py:29` | 2 |
| 102 | `alerts.telegram.enabled` | `true` <sub>(:285)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 103 | `alerts.telegram.bot_token_env` | `"TELEGRAM_BOT_TOKEN"` <sub>(:286)</sub> | `core/config_snapshotter.py:25` | 3 |
| 104 | `alerts.telegram.telegram_alerts_in_paper_mode` | `true` <sub>(:287)</sub> | `main.py:2289` | 1 |
| 105 | `alerts.telegram.max_retries` | `3` <sub>(:288)</sub> | `alerts/telegram_notifier.py:13` | 21 |
| 106 | `alerts.telegram.retry_backoff_seconds` | `2.0` <sub>(:289)</sub> | `alerts/telegram_notifier.py:205` | 4 |
| 107 | `alerts.telegram.rate_limit_per_minute` | `20` <sub>(:290)</sub> | `alerts/telegram_notifier.py:206` | 3 |
| 108 | `alerts.telegram.send_deadline_seconds` | `8` <sub>(:291)</sub> | `alerts/telegram_notifier.py:212` | 3 |
| 109 | `alerts.telegram.channels.label` | `"Primary Alert Channel"` <sub>(:315)</sub> | ⛔ **AMBIGUOUS — `label` is a generic name** | 227 |
| 110 | `alerts.telegram.channels.enabled` | `true` <sub>(:316)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 111 | `alerts.telegram.channels.label` | `"Secondary Alert Channel"` <sub>(:318)</sub> | ⛔ **AMBIGUOUS — `label` is a generic name** | 227 |
| 112 | `alerts.telegram.channels.enabled` | `false` <sub>(:319)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 113 | `alerts.telegram.personal_chat_id_env` | `"TELEGRAM_PERSONAL_CHAT_ID"` <sub>(:320)</sub> | ⚠️ **none by name** | — |
| 114 | `alerts.telegram.whitelist_only` | `true` <sub>(:321)</sub> | ⚠️ **none by name** | — |
| 115 | `alerts.email_fallback.enabled` | `true` <sub>(:339)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 116 | `alerts.email_fallback.smtp_host` | `"smtp.gmail.com"` <sub>(:340)</sub> | `alerts/telegram_notifier.py:830` | 2 |
| 117 | `alerts.email_fallback.smtp_port` | `587` <sub>(:341)</sub> | `alerts/telegram_notifier.py:831` | 2 |
| 118 | `alerts.email_fallback.from_addr_env` | `"ALERT_EMAIL_USER"` <sub>(:342)</sub> | `alerts/telegram_notifier.py:805` | 1 |
| 119 | `alerts.email_fallback.password_env` | `"ALERT_EMAIL_PASSWORD"` <sub>(:343)</sub> | `alerts/telegram_notifier.py:806` | 4 |
| 120 | `alerts.email_fallback.to_addr_env` | `"ALERT_EMAIL_TO"` <sub>(:344)</sub> | `alerts/telegram_notifier.py:807` | 1 |
| 121 | `alerts.email_fallback.use_tls` | `true` <sub>(:345)</sub> | `alerts/telegram_notifier.py:832` | 5 |
| 122 | `alerts.smtp.host` | `"smtp.gmail.com"` <sub>(:347)</sub> | ⛔ **AMBIGUOUS — `host` is a generic name** | 75 |
| 123 | `alerts.smtp.port` | `587` <sub>(:348)</sub> | ⛔ **AMBIGUOUS — `port` is a generic name** | 3321 |
| 124 | `alerts.smtp.use_tls` | `true` <sub>(:349)</sub> | `alerts/telegram_notifier.py:832` | 5 |
| 125 | `alerts.smtp.username` | `"ramakrishnan031@gmail.com"` <sub>(:350)</sub> | `scripts/alert_watcher.py:294` | 3 |
| 126 | `alerts.smtp.password_env` | `"ALERT_SMTP_PASSWORD"` <sub>(:357)</sub> | `alerts/telegram_notifier.py:806` | 4 |
| 127 | `alerts.smtp.from_address` | `"ramakrishnan031@gmail.com"` <sub>(:358)</sub> | `scripts/alert_watcher.py:179` | 9 |
| 128 | `alerts.smtp.timeout_sec` | `30` <sub>(:361)</sub> | ⛔ **AMBIGUOUS — `timeout_sec` is a generic name** | 85 |
| | **`logging:`** | | | |
| 129 | `logging.min_free_disk_gb` | `2.0` <sub>(:364)</sub> | `main.py:2124` | 5 |
| | **`order_reconciler:`** | | | |
| 130 | `order_reconciler.poll_interval_sec` | `15` <sub>(:367)</sub> | ⛔ **AMBIGUOUS — `poll_interval_sec` is a generic name** | 46 |
| 131 | `order_reconciler.capital_drift_tolerance` | `50.0` <sub>(:368)</sub> | `capital/drift_handler.py:27` | 15 |
| 132 | `order_reconciler.capital_drift_tolerance_pct` | `0.10` <sub>(:369)</sub> | `orders/order_reconciler.py:3629` | 2 |
| 133 | `order_reconciler.human_order_margin_tolerance` | `5000.0` <sub>(:370)</sub> | `orders/order_reconciler.py:386` | 5 |
| 134 | `order_reconciler.capital_drift_alert_interval_sec` | `1800` <sub>(:371)</sub> | `orders/order_reconciler.py:397` | 6 |
| 135 | `order_reconciler.stuck_exiting_timeout_minutes` | `30` <sub>(:372)</sub> | `orders/order_reconciler.py:1507` | 2 |
| 136 | `order_reconciler.check1_mid_fill_defer_sec` | `0.0` <sub>(:373)</sub> | `core/closure_source.py:96` | 2 |
| | **`eod_reconcile:`** | | | |
| 137 | `eod_reconcile.authoritative` | `false` <sub>(:376)</sub> | ⛔ **AMBIGUOUS — `authoritative` is a generic name** | 72 |
| 138 | `eod_reconcile.pnl_tolerance` | `100.0` <sub>(:377)</sub> | `scripts/eod_broker_reconcile.py:114` | 5 |
| | **`eod_cleanup:`** | | | |
| 139 | `eod_cleanup.signal_retention_days` | `90` <sub>(:380)</sub> | `scripts/eod_cleanup.py:44` | 15 |
| | **`tgt_retry:`** | | | |
| 140 | `tgt_retry.enabled` | `true` <sub>(:385)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 141 | `tgt_retry.poll_interval_sec` | `30` <sub>(:386)</sub> | ⛔ **AMBIGUOUS — `poll_interval_sec` is a generic name** | 46 |
| 142 | `tgt_retry.max_attempts` | `5` <sub>(:387)</sub> | `main.py:2795` | 16 |
| 143 | `tgt_retry.backoff_base_sec` | `30` <sub>(:388)</sub> | `main.py:2796` | 4 |
| | **`shadow_tracker:`** | | | |
| 144 | `shadow_tracker.enabled` | `false` <sub>(:402)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 145 | `shadow_tracker.max_innings` | `3` <sub>(:403)</sub> | `main.py:2598` | 7 |
| 146 | `shadow_tracker.alert_per_inning` | `true` <sub>(:404)</sub> | `main.py:2599` | 6 |
| | **`sr_detector:`** | | | |
| 147 | `sr_detector.enabled` | `true` <sub>(:407)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 148 | `sr_detector.timeframes` | `["day", "60minute", "30mi…` <sub>(:408)</sub> | `reports/daily_trade_review.py:234` | 21 |
| 149 | `sr_detector.lookback_days` | `180` <sub>(:409)</sub> | ⛔ **AMBIGUOUS — `lookback_days` is a generic name** | 40 |
| 150 | `sr_detector.cache_ttl_sec` | `1800` <sub>(:410)</sub> | `broker/zerodha_adapter.py:443` | 13 |
| 151 | `sr_detector.default_pivot_n` | `5` <sub>(:411)</sub> | `sr_detector/zone_builder.py:32` | 3 |
| 152 | `sr_detector.cluster_pct` | `0.5` <sub>(:412)</sub> | `sr_detector/zone_builder.py:34` | 7 |
| 153 | `sr_detector.merge_pct` | `0.4` <sub>(:413)</sub> | `sr_detector/confluence.py:12` | 6 |
| 154 | `sr_detector.band_buffer_pct` | `0.1` <sub>(:414)</sub> | `sr_detector/confluence.py:58` | 4 |
| 155 | `sr_detector.entry_proximity_pct` | `1.0` <sub>(:415)</sub> | `sr_detector/flags.py:39` | 4 |
| 156 | `sr_detector.volume_surge_mult` | `1.5` <sub>(:416)</sub> | `sr_detector/flags.py:40` | 3 |
| 157 | `sr_detector.w_swing` | `1.0` <sub>(:418)</sub> | `sr_detector/confluence.py:14` | 4 |
| 158 | `sr_detector.w_volume` | `1.0` <sub>(:419)</sub> | `sr_detector/confluence.py:16` | 4 |
| 159 | `sr_detector.w_multi_tf` | `1.0` <sub>(:420)</sub> | `sr_detector/confluence.py:50` | 3 |
| 160 | `sr_detector.w_prior_day` | `1.0` <sub>(:421)</sub> | `sr_detector/confluence.py:51` | 3 |
| 161 | `sr_detector.w_round` | `0.5` <sub>(:422)</sub> | `sr_detector/confluence.py:18` | 4 |
| 162 | `sr_detector.w_recency` | `0.5` <sub>(:423)</sub> | `sr_detector/confluence.py:19` | 5 |
| 163 | `sr_detector.t_high` | `5.0` <sub>(:424)</sub> | `screening/hard_gate.py:325` | 8 |
| 164 | `sr_detector.t_med` | `3.0` <sub>(:425)</sub> | `sr_detector/confluence.py:22` | 4 |
| 165 | `sr_detector.wait_for_retest_enabled` | `false` <sub>(:427)</sub> | `main.py:2974` | 3 |
| 166 | `sr_detector.near_zone_buffer_pct` | `0.3` <sub>(:428)</sub> | `screening/retest_monitor.py:295` | 1 |
| 167 | `sr_detector.require_confidence` | `HIGH` <sub>(:429)</sub> | `screening/retest_monitor.py:296` | 1 |
| 168 | `sr_detector.retest_timeout_sec` | `1800` <sub>(:430)</sub> | `main.py:3330` | 1 |
| 169 | `sr_detector.retest_max_away_pct` | `1.0` <sub>(:431)</sub> | `main.py:3331` | 1 |
| 170 | `sr_detector.confirm_strong_close_frac` | `0.6` <sub>(:432)</sub> | `main.py:3332` | 4 |
| 171 | `sr_detector.breakout_margin_pct` | `0.0` <sub>(:433)</sub> | `main.py:3333` | 3 |
| 172 | `sr_detector.onem_lookback_days` | `2` <sub>(:434)</sub> | `main.py:3325` | 1 |
| 173 | `sr_detector.zone_cache_ttl_sec` | `1800` <sub>(:435)</sub> | `main.py:3109` | 1 |
| 174 | `sr_detector.zone_rewarm_margin_sec` | `300` <sub>(:436)</sub> | `main.py:3119` | 1 |
| 175 | `sr_detector.retest_poll_interval_sec` | `20` <sub>(:437)</sub> | `main.py:3338` | 1 |
| 176 | `sr_detector.sl_buffer_pct` | `0.2` <sub>(:438)</sub> | `main.py:3161` | 18 |
| 177 | `sr_detector.intraday_anchors_enabled` | `false` <sub>(:440)</sub> | `main.py:3051` | 4 |
| 178 | `sr_detector.anchor_intraday_interval` | `5minute` <sub>(:441)</sub> | `scripts/sr_level_export.py:154` | 2 |
| 179 | `sr_detector.anchor_lookback_days` | `1` <sub>(:442)</sub> | `sr_detector/detector.py:86` | 2 |
| 180 | `sr_detector.orb_window_minutes` | `15` <sub>(:443)</sub> | `scripts/sr_level_export.py:154` | 9 |
| | **`regime:`** | | | |
| 181 | `regime.enabled` | `false` <sub>(:446)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 182 | `regime.index_symbol` | `"NIFTY 50"` <sub>(:447)</sub> | `regime/engine.py:79` | 1 |
| 183 | `regime.index_token` | `256265` <sub>(:448)</sub> | `regime/engine.py:78` | 3 |
| 184 | `regime.daily_lookback_days` | `400` <sub>(:449)</sub> | `main.py:3072` | 3 |
| 185 | `regime.intraday_interval` | `5minute` <sub>(:450)</sub> | `regime/engine.py:81` | 4 |
| 186 | `regime.intraday_lookback_days` | `1` <sub>(:451)</sub> | `regime/engine.py:82` | 2 |
| 187 | `regime.ema_fast` | `50` <sub>(:452)</sub> | `regime/engine.py:83` | 7 |
| 188 | `regime.ema_slow` | `200` <sub>(:453)</sub> | `regime/engine.py:84` | 8 |
| 189 | `regime.adx_period` | `14` <sub>(:454)</sub> | `regime/engine.py:86` | 4 |
| 190 | `regime.adx_trend_threshold` | `25.0` <sub>(:455)</sub> | `regime/engine.py:87` | 3 |
| 191 | `regime.atr_period` | `14` <sub>(:456)</sub> | `core/daily_stats.py:38` | 5 |
| 192 | `regime.vol_high_ratio` | `1.3` <sub>(:457)</sub> | `regime/engine.py:89` | 4 |
| 193 | `regime.vol_low_ratio` | `0.7` <sub>(:458)</sub> | `regime/engine.py:90` | 4 |
| 194 | `regime.trend_day_range_atr_mult` | `1.5` <sub>(:459)</sub> | `regime/engine.py:91` | 2 |
| 195 | `regime.compute_interval_sec` | `60.0` <sub>(:460)</sub> | `main.py:3085` | 1 |
| | **`portfolio_allocator:`** | | | |
| 196 | `portfolio_allocator.allocator_mode` | `"shadow"` <sub>(:463)</sub> | `allocation/__init__.py:4` | 5 |
| 197 | `portfolio_allocator.enforce_scope` | `"v3_only"` <sub>(:464)</sub> | `allocation/portfolio_allocator.py:77` | 3 |
| 198 | `portfolio_allocator.candle_interval_seconds` | `60.0` <sub>(:465)</sub> | `allocation/portfolio_allocator.py:117` | 3 |
| 199 | `portfolio_allocator.drain_tail_seconds` | `2.0` <sub>(:466)</sub> | `allocation/portfolio_allocator.py:117` | 2 |
| 200 | `portfolio_allocator.max_portfolio_deployment_pct` | `null` <sub>(:467)</sub> | `allocation/portfolio_allocator.py:184` | 1 |
| 201 | `portfolio_allocator.long_short_skew_max` | `null` <sub>(:468)</sub> | `allocation/portfolio_allocator.py:188` | 1 |
| 202 | `portfolio_allocator.regret_log_path` | `"data_store/allocator/reg…` <sub>(:469)</sub> | `allocation/portfolio_allocator.py:277` | 2 |
| | **`v3_chain:`** | | | |
| 203 | `v3_chain.v3_chain_mode` | `"shadow"` <sub>(:472)</sub> | `main.py:3024` | 4 |
| 204 | `v3_chain.atr30_period` | `14` <sub>(:474)</sub> | `v3_chain/pb01_entry.py:282` | 3 |
| 205 | `v3_chain.rr_floor` | `2.0` <sub>(:475)</sub> | `screening/hard_gate.py:247` | 6 |
| 206 | `v3_chain.sl_buffer_atr_mult` | `0.20` <sub>(:476)</sub> | `screening/hard_gate.py:248` | 7 |
| 207 | `v3_chain.htf_ema_period` | `20` <sub>(:477)</sub> | `v3_chain/pb01_runner.py:148` | 2 |
| 208 | `v3_chain.htf_swing_pivot_n` | `3` <sub>(:478)</sub> | `v3_chain/pb01_runner.py:149` | 2 |
| 209 | `v3_chain.confirm_min_body_frac` | `0.50` <sub>(:480)</sub> | `v3_chain/pb01_entry.py:227` | 3 |
| 210 | `v3_chain.confirm_volume_mult` | `1.20` <sub>(:481)</sub> | `v3_chain/pb01_entry.py:228` | 2 |
| 211 | `v3_chain.baseline_candles_per_session` | `75` <sub>(:482)</sub> | `v3_chain/pb01_entry.py:295` | 1 |
| 212 | `v3_chain.pullback_proximity_pct` | `0.005` <sub>(:483)</sub> | `v3_chain/pb01_entry.py:218` | 2 |
| 213 | `v3_chain.pullback_proximity_atr_mult` | `0.50` <sub>(:484)</sub> | `v3_chain/pb01_entry.py:219` | 2 |
| 214 | `v3_chain.hold_buffer_atr_mult` | `0.20` <sub>(:485)</sub> | `screening/hard_gate.py:430` | 6 |
| 215 | `v3_chain.retest_quality_atr_span` | `0.75` <sub>(:486)</sub> | `v3_chain/pb01_runner.py:193` | 1 |
| 216 | `v3_chain.level_touches_cap` | `5` <sub>(:487)</sub> | `v3_chain/pb01_runner.py:198` | 1 |
| 217 | `v3_chain.w_retest_quality` | `15.0` <sub>(:489)</sub> | `v3_chain/score.py:171` | 1 |
| 218 | `v3_chain.w_confirmation_strength` | `15.0` <sub>(:490)</sub> | `v3_chain/score.py:172` | 1 |
| 219 | `v3_chain.w_level_significance` | `10.0` <sub>(:491)</sub> | `v3_chain/score.py:173` | 1 |
| 220 | `v3_chain.w_regime_preference` | `8.0` <sub>(:492)</sub> | `v3_chain/score.py:139` | 1 |
| 221 | `v3_chain.w_sr_target_quality` | `8.0` <sub>(:493)</sub> | `v3_chain/score.py:140` | 1 |
| 222 | `v3_chain.w_htf_alignment` | `8.0` <sub>(:494)</sub> | `v3_chain/score.py:141` | 1 |
| 223 | `v3_chain.w_sector_strength` | `8.0` <sub>(:495)</sub> | `v3_chain/score.py:142` | 1 |
| 224 | `v3_chain.w_momentum_position` | `8.0` <sub>(:496)</sub> | `v3_chain/score.py:143` | 1 |
| 225 | `v3_chain.exec_budget` | `20.0` <sub>(:497)</sub> | `v3_chain/score.py:120` | 2 |
| 226 | `v3_chain.w_exec_volume_surge` | `15.0` <sub>(:498)</sub> | `v3_chain/score.py:147` | 2 |
| 227 | `v3_chain.w_exec_atr` | `10.0` <sub>(:499)</sub> | `v3_chain/score.py:147` | 2 |
| 228 | `v3_chain.w_exec_time_of_day` | `5.0` <sub>(:500)</sub> | `v3_chain/score.py:148` | 2 |
| 229 | `v3_chain.w_exec_spread` | `5.0` <sub>(:501)</sub> | `v3_chain/score.py:148` | 2 |
| 230 | `v3_chain.confluence_combine` | `"mean"` <sub>(:502)</sub> | `v3_chain/score.py:129` | 1 |
| 231 | `v3_chain.sr_target_quality_high` | `1.0` <sub>(:503)</sub> | `v3_chain/runner.py:340` | 1 |
| 232 | `v3_chain.sr_target_quality_medium` | `0.6` <sub>(:504)</sub> | `v3_chain/runner.py:342` | 1 |
| 233 | `v3_chain.sr_target_quality_low` | `0.3` <sub>(:505)</sub> | `v3_chain/runner.py:343` | 1 |
| 234 | `v3_chain.min_pass_score` | `60` <sub>(:507)</sub> | `core/config_auditor.py:372` | 17 |
| 235 | `v3_chain.medium_score_threshold` | `65` <sub>(:508)</sub> | `core/config_auditor.py:372` | 8 |
| 236 | `v3_chain.high_score_threshold` | `80` <sub>(:509)</sub> | `core/config_auditor.py:372` | 7 |
| 237 | `v3_chain.structure_intervals` | `["day", "60minute", "30mi…` <sub>(:511)</sub> | `v3_chain/pb01_runner.py:109` | 2 |
| 238 | `v3_chain.fetch_lookback_days` | `180` <sub>(:512)</sub> | `main.py:3230` | 3 |
| 239 | `v3_chain.fetch_cache_ttl_sec` | `1800.0` <sub>(:513)</sub> | `main.py:3232` | 2 |
| 240 | `v3_chain.max_queue` | `512` <sub>(:514)</sub> | `sr_detector/detector.py:100` | 6 |
| 241 | `v3_chain.would_be_log_path` | `"data_store/v3/would_be.j…` <sub>(:515)</sub> | `scripts/v3_shadow_soak_report.py:190` | 5 |
| | **`watchlist:`** | | | |
| 242 | `watchlist.enabled` | `true` <sub>(:518)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 243 | `watchlist.playbook_scanner` | `"pb01_breakout_retest"` <sub>(:519)</sub> | ⚠️ **none by name** | — |
| 244 | `watchlist.level_lookback_sessions` | `20` <sub>(:521)</sub> | `v3_chain/watchlist_capture.py:155` | 2 |
| 245 | `watchlist.capture_fetch_lookback_days` | `60` <sub>(:522)</sub> | `v3_chain/pb01_entry.py:291` | 1 |
| 246 | `watchlist.entry_start` | `"09:20"` <sub>(:524)</sub> | ⛔ **AMBIGUOUS — `entry_start` is a generic name** | 29 |
| 247 | `watchlist.entry_end` | `"11:00"` <sub>(:525)</sub> | ⛔ **AMBIGUOUS — `entry_end` is a generic name** | 32 |
| 248 | `watchlist.entry_tf` | `"5minute"` <sub>(:526)</sub> | ⚠️ **none by name** | — |
| 249 | `watchlist.gap_guard_pct` | `0.03` <sub>(:527)</sub> | `v3_chain/pb01_entry.py:188` | 2 |
| 250 | `watchlist.poll_interval_sec` | `20.0` <sub>(:528)</sub> | ⛔ **AMBIGUOUS — `poll_interval_sec` is a generic name** | 46 |
| 251 | `watchlist.would_be_log_path` | `"data_store/v3/pb01_would…` <sub>(:529)</sub> | `scripts/v3_shadow_soak_report.py:190` | 5 |
| | **`structure_exit:`** | | | |
| 252 | `structure_exit.structure_exit_enabled` | `false` <sub>(:532)</sub> | `core/config_auditor.py:215` | 8 |
| 253 | `structure_exit.sl_buffer_pct` | `0.2` <sub>(:533)</sub> | `main.py:3161` | 18 |
| 254 | `structure_exit.break_buffer_pct` | `0.0` <sub>(:534)</sub> | `orders/structure_exit_manager.py:132` | 2 |
| 255 | `structure_exit.require_strong_close` | `true` <sub>(:535)</sub> | `orders/structure_exit_manager.py:133` | 2 |
| 256 | `structure_exit.break_strong_close_frac` | `0.6` <sub>(:536)</sub> | `orders/structure_exit_manager.py:134` | 1 |
| 257 | `structure_exit.min_zone_confidence` | `HIGH` <sub>(:537)</sub> | `orders/structure_exit_manager.py:30` | 3 |
| | **`smart_tgt:`** | | | |
| 258 | `smart_tgt.enabled` | `true` <sub>(:540)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 259 | `smart_tgt.trigger_pct` | `0.005` <sub>(:541)</sub> | ⛔ **AMBIGUOUS — `trigger_pct` is a generic name** | 36 |
| 260 | `smart_tgt.step_pct` | `0.003` <sub>(:542)</sub> | `core/state_store.py:2117` | 20 |
| 261 | `smart_tgt.volume_dependent_trails` | `false` <sub>(:543)</sub> | `main.py:2630` | 7 |
| 262 | `smart_tgt.max_modify_failures` | `3` <sub>(:544)</sub> | `main.py:2631` | 6 |
| | **`entry_gate:`** | | | |
| 263 | `entry_gate.slippage_buffer` | `2.0` <sub>(:547)</sub> | `main.py:2718` | 8 |
| 264 | `entry_gate.max_entry_slippage_pct` | `1.0` <sub>(:553)</sub> | `main.py:2719` | 9 |
| 265 | `entry_gate.max_spread_pct` | `0.5` <sub>(:554)</sub> | `orders/order_placer.py:589` | 14 |
| 266 | `entry_gate.min_depth_qty` | `500` <sub>(:555)</sub> | `orders/order_placer.py:590` | 7 |
| 267 | `entry_gate.liquidity_check_enabled` | `true` <sub>(:556)</sub> | `orders/order_placer.py:588` | 3 |
| 268 | `entry_gate.min_effective_rr` | `1.0` <sub>(:557)</sub> | `main.py:2727` | 7 |
| 269 | `entry_gate.min_pending_rr` | `1.0` <sub>(:558)</sub> | `broker/order_monitor.py:182` | 9 |
| 270 | `entry_gate.circuit_proximity_reject_enabled` | `true` <sub>(:559)</sub> | `main.py:2890` | 7 |
| 271 | `entry_gate.slippage_control.enabled` | `true` <sub>(:567)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 272 | `entry_gate.slippage_control.mode` | `sl_fraction` <sub>(:568)</sub> | ⛔ **AMBIGUOUS — `mode` is a generic name** | 867 |
| 273 | `entry_gate.slippage_control.max_slippage_fraction` | `0.22` <sub>(:569)</sub> | `orders/order_placer.py:262` | 6 |
| 274 | `entry_gate.slippage_control.absolute_cap_rs` | `5.00` <sub>(:570)</sub> | `orders/order_placer.py:336` | 5 |
| 275 | `entry_gate.slippage_control.hard_max_slippage_rs` | `10.0` <sub>(:571)</sub> | `orders/order_placer.py:343` | 2 |
| 276 | `entry_gate.slippage_control.also_apply_pct_check` | `true` <sub>(:572)</sub> | `orders/order_placer.py:377` | 2 |
| 277 | `entry_gate.slippage_control.default_max_slippage_rs` | `2.00` <sub>(:574)</sub> | `orders/order_placer.py:355` | 1 |
| 278 | `entry_gate.slippage_control.overrides.enabled` | `true` <sub>(:589)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 279 | `entry_gate.slippage_control.overrides.by_price_band` | `{}` <sub>(:595)</sub> | `core/config_auditor.py:475` | 4 |
| 280 | `entry_gate.slippage_control.overrides.by_strategy` | `{}` <sub>(:599)</sub> | `core/config_auditor.py:474` | 10 |
| 281 | `entry_gate.slippage_control.overrides.by_symbol` | `{}` <sub>(:604)</sub> | ⛔ **AMBIGUOUS — `by_symbol` is a generic name** | 33 |
| | **`paper:`** | | | |
| 282 | `paper.auto_fill_delay_sec` | `0.5` <sub>(:619)</sub> | `broker/zerodha_adapter.py:51` | 8 |
| 283 | `paper.ltp_gating_enabled` | `true` <sub>(:620)</sub> | `broker/zerodha_adapter.py:357` | 7 |
| 284 | `paper.ltp_gating_max_wait_sec` | `21600` <sub>(:621)</sub> | `broker/zerodha_adapter.py:358` | 6 |
| 285 | `paper.ltp_gating_poll_sec` | `5.0` <sub>(:622)</sub> | `broker/zerodha_adapter.py:359` | 5 |
| | **`drift_handler:`** | | | |
| 286 | `drift_handler.log_only_threshold_rs` | `250.0` <sub>(:625)</sub> | `capital/drift_handler.py:137` | 4 |
| 287 | `drift_handler.soft_kill_threshold_rs` | `1000.0` <sub>(:626)</sub> | `capital/drift_handler.py:135` | 3 |
| 288 | `drift_handler.hard_kill_threshold_rs` | `2500.0` <sub>(:627)</sub> | `capital/drift_handler.py:133` | 3 |
| 289 | `drift_handler.consecutive_cycles_before_escalate` | `3` <sub>(:628)</sub> | `capital/drift_handler.py:181` | 1 |
| | **`strategy_circuit_breaker:`** | | | |
| 290 | `strategy_circuit_breaker.enabled` | `true` <sub>(:631)</sub> | ⛔ **AMBIGUOUS — `enabled` is a generic name** | 338 |
| 291 | `strategy_circuit_breaker.loss_multiplier` | `2.0` <sub>(:632)</sub> | `capital/strategy_governor.py:6` | 6 |
| 292 | `strategy_circuit_breaker.cutoff_time` | `"12:00"` <sub>(:633)</sub> | `capital/strategy_governor.py:37` | 5 |
| 293 | `strategy_circuit_breaker.lookback_days` | `10` <sub>(:634)</sub> | ⛔ **AMBIGUOUS — `lookback_days` is a generic name** | 40 |
| | **`circuit_breaker:`** | | | |
| 294 | `circuit_breaker.partial_fill_timeout_minutes` | `5` <sub>(:637)</sub> | `broker/order_monitor.py:178` | 4 |
| 295 | `circuit_breaker.force_close_time` | `"15:15"` <sub>(:638)</sub> | `broker/order_monitor.py:180` | 14 |
| 296 | `circuit_breaker.max_api_failures` | `3` <sub>(:639)</sub> | `broker/order_monitor.py:179` | 6 |
| | **`live_feed:`** | | | |
| 297 | `live_feed.max_reconnect_attempts` | `10` <sub>(:642)</sub> | `data/live_feed.py:44` | 7 |
| 298 | `live_feed.reconnect_backoff_base_seconds` | `1` <sub>(:643)</sub> | ⚠️ **none by name** | — |
| 299 | `live_feed.reconnect_backoff_max_seconds` | `30` <sub>(:644)</sub> | ⚠️ **none by name** | — |
| | **`fno_ban:`** | | | |
| 300 | `fno_ban.url` | `"https://nsearchives.nsei…` <sub>(:647)</sub> | ⛔ **AMBIGUOUS — `url` is a generic name** | 119 |
| 301 | `fno_ban.fail_closed` | `false` <sub>(:648)</sub> | `scripts/fetch_fno_ban.py:40` | 11 |
| | **`scanner_check_delay_sec:`** | | | |
| 302 | `scanner_check_delay_sec` | `5.0` <sub>(:651)</sub> | `utils/startup_checks.py:1591` | 1 |
