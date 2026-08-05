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
