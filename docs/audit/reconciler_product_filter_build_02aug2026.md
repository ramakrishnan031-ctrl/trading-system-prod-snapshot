# LEDGER #2b — RECONCILER SELL-UNDER-KILL PRODUCT FILTER: BUILD RECORD
**Executed 02-Aug-2026 ~10:0x–1x:xx IST (Sunday, pre-18:15 window throughout).**
**Status: `<BUILT — NOT DEPLOYED, NOT PUSHED>`.** Authority: the #2b card;
`docs/audit/buyday_filter_build_01aug2026.md` §2 (the disclosure); audit D-8 / Q4
ordering rule. 🔴 **Which deploy it rides is Rama's ruling (R4) — see §6.**

---

## 1. STEP-1 FINDINGS (all measured before editing)

| item | finding |
|---|---|
| The site | **Confirmed exactly as disclosed.** `order_reconciler._check2_inflight_orphan` → `_flatten_broker_position`; the flatten call has drifted :2023 → **:2103** by my own edit only (the site was at :2023 on entry, byte-faithful to the disclosure). No second callee, no moved site ⇒ **no STOP condition** |
| Callers of `_flatten_broker_position` | **Exactly ONE** (the check2 kill branch). That is what makes a filter in the caller equivalent to a filter in the sell itself — and it is now **pinned by a test** so a second caller cannot appear silently |
| Product source (G3) | `bp` is a **RAW BROKER row**: `zerodha_adapter.Position.product` (`:185` "broker code") is Kite's `net[].product` passed straight through (`:1235`). Predicate reads the raw broker string — validated independently of the local-DB vocabulary, exactly like ledger #2's SITE 2. ⛔ Deliberately NOT routed through `_PRODUCT_TO_INTENT` (which maps LOCAL products) |
| Broker domain | Kite equities {MIS, CNC, NRML, CO(+MTF)}; anything beyond-set hits the loud-unknown path by construction |
| **Disposition recording (the card's step 2)** | **VERIFIED — the card's assumption holds.** This path records the orphan **nowhere**: no per-symbol suppression set (unlike `_check2_orphan_adoption`'s once-a-day `_human_order_symbols`), no `handled` mark, no resolve write. ~~A spared CNC row is therefore **re-seen and re-reported every cycle**.~~ Pinned by test. ⛔ **AMENDED 02-Aug (#2c-R) — see the CHECK6 amendment note directly below: within this method the statement is exact, but the CADENCE IT IMPLIES IS BOUNDED from outside it** |

> ### ⛔ AMENDED 02-Aug (#2c-R) — **"RE-ALERTS EVERY CYCLE BY DESIGN" IS BOUNDED, AND**
> ### **THE BOUND COMES FROM OUTSIDE THIS METHOD.**
> Measured 02-Aug, **disclosed, NOT patched.** `_check6_orphan_orders` (**wired in
> production**, `main.py:2739`) walks `PENDING_FILL` trades whose ENTRY order is absent
> from the broker's open orders — the same shape that reaches CHECK2 — and its **FIX-B**
> counter marks the trade **FAILED and releases its reservation on the 3rd consecutive
> cycle**. CHECK6 runs **after** CHECK2 in a cycle, so cycle 3 still alerts; from cycle 4
> the caller's `if inflight:` (`:930`) is False and the **same broker position routes to
> `_check2_orphan_adoption`** — once-a-day per-symbol suppression, and it can file the
> position as `HUMAN_ORDER`.
> ⇒ **the spare yields ~3 CRITICALs, not an unbounded stream**, while the delivery
> position is **still live at the broker** and **its capital has been released**.
> ⭐ **This applies to the CNC spare exactly as it does to #2c-R's CO refusal** — the claim
> is corrected here rather than left standing, in both records.
>
> ➡️ **FORWARD POINTER (do not re-derive):** capital-released-while-live and the
> `HUMAN_ORDER` misfiling are **NOT this item's defect and are NOT repaired here** — they
> are the cancel-race's endpoint reached by a **second, independent** path, and they are
> **registered to debt-ledger #3 (IA-P5-02 family)**, whose scope note was expanded for
> exactly this. ⛔ #3's **ordering is unchanged**; **not a new register row — 231 stands.**
> 🔴 **Post-flip, a CNC delivery holding spared by #2b follows exactly this path**;
> reachability is **latent-on-latent** (HARD_KILL **and** an in-flight entry **and** a
> fill; HARD_KILL has never fired) ⇒ **documented, NOT flip-blocking.**
| Persistence | `ReconciliationAction` tier ≠ COSMETIC ⇒ one `reconciliation_log` row per cycle (`:1055-1069`). No CHECK constraint on `check_name`; **schema unchanged, no migration** |
| Closure-classifier safety | `reports/daily_trade_review._ORPHAN_CHECKS` (`:93-94`) is an explicit **set**, not a substring match. The new `INFLIGHT_ORPHAN_SPARED_DELIVERY` is **not** in it, so a spare is never classified `ORPHAN_RECOVERY` — correct: a spare closes nothing. Pinned by test |

## 2. WHAT WAS BUILT (one code commit)

- **`orders/order_reconciler.py`** — imports the ONE shared name
  `core.constants.EMERGENCY_FLATTEN_PRODUCTS` (extends the existing `core.constants`
  import at `:96`; leaf module, no cycle). Inside the `kill_active` branch of
  `_check2_inflight_orphan`, **before** the "FLATTENING" log and the sell:
  - **CNC → SPARED.** CRITICAL-loud spared-log (symbol, qty, trade, `product=CNC`,
    `site=reconciler_check2`) + a new `ReconciliationAction(check_name=
    "INFLIGHT_ORPHAN_SPARED_DELIVERY", tier="CRITICAL", success=True)`. Nothing is
    sold; nothing is marked handled.
  - **Not in `EMERGENCY_FLATTEN_PRODUCTS` and not CNC (NULL/NRML/unrecognised) →
    FLATTEN + CRITICAL**, through the **existing shared emitter**
    `KillSwitch._alert_unknown_product` with `site="reconciler_check2"`. **No second
    emitter was created** (#4). `self._ks` is non-None by construction here
    (`kill_active` implies it); the delegation is wrapped so that a missing/broken
    emitter still produces a loud local CRITICAL — alerting may never make the
    flatten quiet, and may never break it.
  - **MIS/CO → flatten as today**, byte-identical behaviour.
- **`_flatten_broker_position` docstring** — the precondition is written where a future
  caller will read it: *this function SELLS, so it must never be reached for a CNC
  position; its one caller applies the filter; a new caller must too.*
- **No status literal touched · no `holdings()` introduced** ⇒ the Q4 ordering rule
  ("the product filter lands BEFORE anything makes the kill holdings-aware") is intact,
  and **D-8 stays blocked exactly as before** — this changes no `held`/status semantics.

### 2a. The per-product-row hazard — carried over, and what it actually is here
Ledger #2's hazard (sparing a CNC row must not suppress a same-symbol MIS row) is
**structurally different at this site, and the difference is upstream of it**: the
reconciler's cycle snapshot is **symbol-keyed** — `broker_pos = {p.symbol: p for p in
raw_positions}` (`:863`) — so a same-symbol MIS row is **already collapsed away by the
dict before any check runs**. Consequences, stated plainly:

- **Sparing suppresses nothing the snapshot had not already dropped.** Both directions
  are covered by tests (a spare re-fires next cycle; a spare on one symbol does not
  silence another).
- **My change is strictly an improvement in the collapsed case.** If the dict happens to
  hold the CNC row: *today* the reconciler sells that qty under `intent="INTRADAY"` —
  which does not offset the CNC position and opens a fresh naked MIS short (the H-5
  class); *after* this change it is spared and reported. If the dict holds the MIS row,
  behaviour is unchanged.
- **What still flattens the masked MIS row** is the kill_switch broker sweep, which
  reads genuine per-(symbol, product) rows.
- ⚪ **NOT repaired here (reported):** re-keying that snapshot on (symbol, product) would
  change checks 1-5 as well — outside this filter's scope, and it is the reconciler
  workstream that D-8 blocks.

### 2b. ⚠️ DISCLOSED, NOT PATCHED — `intent` is hardcoded INTRADAY at the sell
`_flatten_broker_position` passes `intent="INTRADAY"` unconditionally. Post-filter the
reachable products are MIS, CO and unknown/NULL — right for MIS, and the deliberate loud
fallback for unknown — but **a CO position would be exited under an MIS intent**. This is
a **fourth thing**, not one of the card's three (spare-CNC / loud-unknown / spared-log),
so patching it here would be the silent scope expansion (G5) the card's OUT-list forbids.
🔴 Owed: a ruling, not a fix-in-passing.

> ### ⛔ AMENDED 02-Aug (#2c Step-1) — **THIS ENTRY UNDERSTATED THE FINDING.**
> The original text above (kept legible) read as *"CO would exit under an MIS intent, the
> H-5 class the kill_switch sites map away via `PRODUCT_TO_INTENT`"* — which implies **a
> mapping fix would cure it. IT WOULD NOT.** #2c was carded to do exactly that mapping;
> its Step-1 gate measured the runtime semantics **and stopped before any edit**. What was
> measured, with the citations:
>
> **(a) `intent` IS the live Kite `product` field — not a label.** `place_order` resolves
> it through `broker/product_resolver.py:12` (`INTRADAY→"MIS"`, `DELIVERY→"CNC"`,
> `COVER_ORDER→"CO"`) and sends it to Kite as `product=broker_code`. Three hops, no
> branching: reconciler sell → `zerodha_adapter.place_order` `broker_code = self._pr.resolve(...)`
> → `self._kite.place_order(..., product=broker_code, ...)`.
>
> **(b) ⛔ A CO POSITION CANNOT BE SQUARED OFF BY A REVERSE ORDER AT ALL** — and this repo
> already says so. `orders/eod_squareoff.py`, the Audit-3.1 comment above `is_co`, verbatim:
> *"CO positions cannot be squared off with a reverse MARKET -- Zerodha rejects and
> auto-squares at 15:20 with a ₹50+GST penalty. The correct path is
> `cancel_order(variety="co")` on the CO entry bracket; the broker collapses the bracket
> and closes the position at market."* EOD implements precisely that — the
> `cancel_order(entry_broker_id, variety="co")` call, gated on
> `is_co = (order_protocol == "CO_PLUS_TGT") and (entry_variety == "co")`, with a missing
> `entry_broker_order_id` treated as a CRITICAL that cannot proceed. ⇒ **mapping
> CO→COVER_ORDER here would merely emit the order the broker refuses.**
>
> **(c) THIS PATH CANNOT REACH A PARENT ORDER ID.** Inputs are
> `(symbol, bp, tag_prefix, trade_id)`; `bp` is a broker **position** row
> (`symbol, qty, avg_price, product, side`) — no order id anywhere on the path.
> ⭐ `trade_id` **is** present: the only affordance any redesign has to look one up.
>
> **(d) TODAY'S REAL BEHAVIOUR on a CO position, stated plainly — worse than the original
> disclosure implied:** `product="MIS"` is **ACCEPTED** by the broker, does **NOT** net
> against the CO position (Kite nets per `(symbol, product)`), and therefore **opens a
> NAKED MIS SHORT while the CO position survives.** *(Nuance for the redesign: the carded
> mapping would have converted this silent-wrong-outcome into a loud rejection — a better
> failure MODE, but still not an exit. That is not a reason to ship it.)*
>
> **(e) Doubly dormant ⇒ LATENT, not live.** CO has never been used
> (`orders/order_protocol_co.py`: *"CO never used: 805/805 regular, X6"*; declared by 12/15
> YAMLs and discarded) **AND** `force_intraday_only: true` (`config/system_config.yaml:89`)
> coerces every non-INTRADAY intent back to INTRADAY inside `place_order` before
> resolution. ⇒ the carded fix would also have been **a no-op in today's configuration**,
> whose only observable effect would be a new `WARNING` per flatten — arming silently the
> day that breaker is flipped.
>
> **(f) ⭐⭐ PAPER CANNOT VALIDATE THIS CLASS — the most reusable thing Step-1 found, and a
> STANDING RULE beyond #2c.** Paper nets by **SYMBOL** (`_paper_positions`, and
> `get_positions` iterates `self._paper_positions.items()`); live Kite nets per
> **(symbol, product)**. ⇒ **a paper drill of ANY product-semantics change is vacuously
> green.** Any future work whose correctness depends on product identity must be validated
> against live semantics or by construction — never by a paper run. Joins the
> `paper_cannot_exercise` class.
>
> **(g) No `orders` row is written on this path** (RC18 — direct adapter call), so there is
> no audit-row side effect either way. **And the variety hazard, record-only:** this caller
> never passes `variety`; `place_order` defaults `variety="regular"` and **nothing validates
> variety-against-product** (`_validate_place_order` checks side/qty/price/trigger only). A
> latent hazard for **any** future non-INTRADAY intent placed at this site.
>
> ⇒ **#2c is CLOSED as STOPPED AT STEP-1 — a validated redesign trigger, NOT an
> implementation failure.** The redesign is **#2c-R** (with ChatGPT for red-team). ⛔ Neither
> candidate architecture (parent-id lookup → `cancel_order(variety="co")`, mirroring EOD's
> proven path rather than copying it; or refuse-and-escalate) has been started.
> ⛔ **Do not "fix" this by mapping the intent.**

### 2c. Downstream effect of the NEW `check_name` — traced, and knowingly accepted
A new `check_name` is read by four consumers. All four were checked; **none is edited**:

| consumer | matcher | effect of `INFLIGHT_ORPHAN_SPARED_DELIVERY` | verdict |
|---|---|---|---|
| `reports/daily_trade_review.py:93` `_ORPHAN_CHECKS` | explicit **set** | **not** a member ⇒ never classified `ORPHAN_RECOVERY`, never flags `_mismatch` | ✅ **correct — a spare closes nothing.** Pinned by test |
| `reports/daily_report.py:576` "Orphan Orders" | `"ORPHAN" in check_name` | **counted** | ✅ correct — it **is** an orphan detection |
| `scripts/system_manager.py:612` "Orphan detections" | `check_name LIKE '%ORPHAN%'` | **counted ⇒ warns** | ✅ correct — a delivery position orphaned under a kill *should* warn |
| `reconciliation_log` (schema) | no CHECK constraint | one row per cycle while spared | ✅ intended (§1) |

⚪ The last two are substring matchers — the already-registered "classify by free text"
class (`daily_report_classification_fix_18jul2026.md` item 9, *reported, not changed*).
**Not worsened here**, and the name lands on the right side of both. The name deliberately
**keeps** the word ORPHAN: hiding a real orphan from the operator's orphan count to keep a
counter tidy would be the dishonest choice.

## 3. DOCS RIDER (separate docs-only commit — no code, no registry)
`docs/audit/effect_verification_contract_01aug2026.md` §AMENDMENTS gains **B-2a**.
⭐ **The rider found more than the card expected, and it is recorded by measurement, not
recall.** The card asked for "the two kill_switch adapter call sites (:1549/:1609)".
Measured repo-wide (**search width stated in the note**: `grep -rn "\.place_order("
--include=*.py .` minus `tests/`, `venv/`, and the adapter's own `def`):

- **kill_switch has THREE**, not two — :1629 local pass · :1708 broker sweep ·
  **:1789 the retry loop** (at `297b587`: :1540 · :1600 · :1681). The #2 record's
  correction was itself short by one.
- **B-2's dispersal list also missed** 3 of the reconciler's 4 sites,
  `sl_breach_monitor.py:221`, and `structure_exit_manager.py:445`. Full 19-site / 8-module
  table is in the note.
- ⭐ **The third kill_switch site owes nothing:** `failed_trades` is populated only at the
  two now-filtered sites (`:1654`, `:1723`), each carrying the intent its own filter
  derived, and a CNC row is `continue`d **before** either try-block — so the retry loop
  can never re-fire a CNC. **Measured, not assumed.**
- ⛔ **NO effect-point change, NO registry change, NO code change** follows: kill-flatten
  sells go through the adapter directly by design, telemetry counts `placer.place()` on
  purpose, and the reconciler is counted at `reconcile_once`. Record only.

## 4. VALIDATION

| check | result |
|---|---|
| **Targeted tests** — `tests/unit/test_reconciler_product_filter.py`, **16, all green** | MIS + CO flatten quietly (parametrised over the shared set) · CNC spared, **nothing sold** · a **short** CNC row spared too (the spare is on PRODUCT, never direction) · case/whitespace variant `" cnc "` still spared · NULL/NRML/MTF/typo flatten **+ the shared emitter**, `site="reconciler_check2"` · a **broken emitter still leaves the flatten loud and unbroken** · no-kill ⇒ no product decision at all, any product · **spare stays visible to the next cycle** (3 identical cycles ⇒ 3 identical CRITICAL dispositions) · a spare on one symbol does not silence another · a spare is **not** in `_ORPHAN_CHECKS` · single-vocabulary scanner · **single-caller tripwire** |
| **RED-on-old** (base worktree at `99ca2eb`, **never a stash**; test file md5-identical in both trees: `317de655…`) | **11 failed / 5 passed on old vs 16 passed on new.** The 5 green-on-both are exactly the invariants I preserved (MIS/CO flatten, no-kill inaction, cross-symbol, single-caller). The tests could have been red |
| **Kill-adjacent + reconciler suites** (14 files, 333 tests) — *measured on both sides in the same session and window, not recalled* | **new: 1F/332P · base: 1F/332P — NEW-FAILURE SET EMPTY.** The one failure is the same test both sides: `test_fix181::TestStep4_ReconcilerInflightOrphan::test_inflight_orphan_flattened_when_kill_active`, `assert 'MARKET' == 'LIMIT'` — **the known standing T3 LIMIT-vs-MARKET item**, named in the #2 record's own validation table, untouched by me |
| **Diff scope** | `orders/order_reconciler.py` + the new test file + the docs commit. **0 status literals touched · 0 `holdings()` introduced** (both grepped over the `+` lines of the diff). No schema change, no migration, no config key, no cron, no new path |
| Full regression | see §4a |
| Revert | see §4b |

### 4a. Regression stamp — ⭐ NEW-FAILURE SET **EMPTY (0)**, and the baseline was
### measured fresh in this session, never recalled

Both runs `pytest tests/unit tests/integration -q`, **02-Aug ~10:16–11:0x IST — same
session, same pre-18:15 window, neither crossing midnight** (the two clock rules the
attribution depends on).

| run | result |
|---|---|
| **new code** (primary tree) | **7 failed / 5,491 passed / 4 skipped** — 868s |
| **base** (worktree at `99ca2eb`, clean, **never a stash**) | **37 failed / 5,445 passed / 4 skipped** — 866s |
| **`comm -23` (failures in MINE, not in base)** | ⭐ **EMPTY — 0** |

**The base's extra 30 failures are worktree ARTIFACTS, and the mechanism was PROVEN, not
labelled** (the standing rule: *"known env failures" is a label, not a diagnosis*):

1. **26 × `test_main.py` — a git-IGNORED data file absent from a fresh worktree.**
   `config/instruments.csv` is ignored at `.gitignore:39` (`git check-ignore -v`), so
   `git worktree add` does not materialise it; `main.main()` → `load_all(config_dir)`
   then fails and returns **5** where each test expects its own code. ✅ **PROVEN by
   restore:** copying that one file into the worktree took `test_main.py` from **26
   failures → 4**, and those 4 are *exactly* the 4 that also fail in-tree.
2. **4 × `test_t4_deploy_preflight` / `test_preflight` — subprocess PATH.** Their errors
   name it verbatim: *"Python was not found; run without arguments to install from the
   Microsoft Store"* / *"check_tz.sh: could not obtain authoritative IST"*. These spawn
   `bash`/`python`, which the scratchpad worktree cannot resolve. (The standing note that
   in-process test guards do not cover a subprocess applies here too.)

**The arithmetic closes exactly on both axes, which independently corroborates the
diagnosis:** base 5,445P **+ 30** artifacts = **5,475** true in-tree base; **+ 16** new
tests = **5,491** = measured. And 37F **− 30** = **7F** = measured. *(5,475/7F also equals
the #2 record's documented standing baseline — a cross-check, not a dependency: every
number above was measured this session.)*

**The 7 standing failures, named** (all present in BOTH sets): `test_fix181` inflight-orphan
LIMIT-vs-MARKET (the known T3 item) · `test_closure_source_contract` vocabulary scanner
(offender `scripts/backfill_closure_source_w8.py:92`, unrelated — its regex matches only
`OWN_SL|OWN_TGT|OWN_EOD|OWN_KILL|EXTERNAL_UNATTRIBUTED`, none of which this change
introduces) · `test_main` ×4 (3 `TestContinueFromGate` — the IA-P2-01 production-unreachable
gate — + 1 BL15) · `test_phase17_batch2` flask max-content-length.

⚪ **The documented q9 consecutive-losses oscillator did not fire in either run** — absent
from both failure sets. A calm pair; no flake needed naming.

### 4b. Revert check
`git revert` of the code commit restores today's behaviour exactly: the change is
additive inside one branch of one method plus one import and one docstring — no
extracted helper, no moved code, no renamed symbol, nothing else references the new
`check_name`.

## 5. PAPER / LIVE PARITY — and one honest label correction

One shared code path; `order_reconciler` runs in both modes; one code commit; the
targeted tests are mode-agnostic.

⭐ **This site does NOT join the "paper cannot exercise it" class, and that is measured:**
paper `get_positions()` returns `Position(product=info.get("product", "MIS"))`
(`zerodha_adapter.py:1204`), and `_paper_positions` records
`"product": _rec.get("product") or "MIS"` from the order record (`:2285`) — so a paper
**CNC entry produces a genuine `product="CNC"` position** and the spare branch is
rehearsable in a composed paper drill. Contrast `check1_mid_fill_defer_sec`, which paper
structurally cannot exercise.

⚠️ **But one sub-property is NOT paper-rehearsable:** `_paper_positions` is symbol-keyed,
so paper cannot produce two rows for one symbol with different products. The
per-product-row shape (§2a) is live-only — which is also why §2a's masking is reasoned
from the live adapter's per-row return, not from a paper drill.

## 6. LABEL HONESTY & GATES

- **Reachability today: CNC-UNREACHABLE.** It needs a **delivery entry in flight during a
  HARD_KILL**. `delivery_enabled: false` (`config/system_config.yaml:102`,
  `config_loader.py:1754`) ⇒ no CNC entry can be in flight. And **HARD_KILL has never
  fired** (measured, ledger #2). Two independent gates.
- ⇒ **This item can only ever reach `<BUILT>` → `<DEPLOYED>` + dormant-armed.
  `<VERIFIED LIVE>` requires a real HARD_KILL with a delivery position in flight** — it
  will not be claimed on anything less.
- ⛔ **Nothing deploys or pushes from this build.** It does **not** gate the flag flip
  (no delivery entries exist until the carry pilot trades).
- ✅ ~~🔴 **R4 — Rama's ruling, unchanged and now unblocked in both directions:** ride the
  Monday-evening deploy stack, **or** name it a documented carry-pilot blocker.~~
  **ANSWERED 02-Aug ~16:32 — IT RIDES THE MONDAY-EVENING PUSH.** Rama's **R2** (deploy slot
  = **Option Y**: Mon eve = code + docs **without** the flip flags) and **R3** (*#2b and
  #2c-R ride the same push; they already sit in the same linear history and cannot be
  excluded* — `campaign_practices.md` §D1, there is no partial deploy).
  ⚠️ **NAMING COLLISION, stated so the two are never conflated:** *this record's* "R4" is
  **#2b's own deploy-slot question** (now closed). **Rama's new R4** in
  `docs/MASTER_PENDING_01-Aug-2026.md` §A-DEC is a **different ruling** — the
  `cnc_gtt_placer`/`cnc_gtt_monitor` **registry edits ride the flip-flag push (Tue 4-Aug
  eve)**. Same label, unrelated subjects.
  ⭐ And the flip date itself moved: **the flip is now WED 5-AUG**, with **Tue 4-Aug** a
  shakedown day carrying the first real EOD census (**R2**). Full ruling + provenance:
  `docs/MASTER_PENDING_01-Aug-2026.md` **§A-DEC**.

---
---

# LEDGER #2c-R — ORPHAN-CO **REFUSE-AND-ESCALATE**: BUILD RECORD

**Executed 02-Aug-2026 (Sunday). Code committed `42db913` ~12:4x before a power-down;
validation completed in a fresh session 12:1x–1x:xx IST — same day, same pre-18:15
window, neither run crossing midnight.**
**Status: `<BUILT — NOT DEPLOYED, NOT PUSHED>`.**
Authority: the #2c-R card (Option 1, ChatGPT red-team Q1–Q6 binding) · #2c Step-1 proof
(F1–F8, recorded above) · Audit 3.1 · #2b build record §2b as amended.
⛔ **OFF Monday's critical path.** `origin/main` still `297b587`; nothing pushed.

## R1. WHY REFUSE — the permanent fix, not a patch (#4 permanent-fixation)

#2c Step-1 proved this path **cannot** square a CO position: Audit 3.1 says a CO position
cannot be closed by a reverse order at all (the broker rejects it and auto-squares at
15:20 with a ₹50+GST penalty), the correct action is
`cancel_order(entry_broker_id, variety="co")` on the parent bracket, and **this path has
no parent broker order id** — its inputs are `(symbol, bp, tag_prefix, trade_id)` and `bp`
is a broker *position* row. The pre-#2c-R code sold `product="MIS"`, which the broker
**accepts** but which does **not** net against a CO position (Kite nets per
`(symbol, product)`) ⇒ it **opened a naked MIS short while the CO position survived**.
⇒ **Placing no order is strictly safer than placing a wrong, position-CREATING one.**
The cure is to stop emitting the wrong order, not to relabel it.

## R2. STEP-1 RE-CHECK (the card's 1a/1b, reported inline as instructed)

| item | finding |
|---|---|
| **1a — structure at the site** | **Confirmed, no STOP condition.** CO fell through the membership test into the silent flatten exactly as F4 described (reproduced in-code, not by running). The site's structure matches the #2b record |
| **1b — does Audit 3.1 also bite `kill_switch`?** | ⭐ **YES — REAL, and REPORTED NOT PATCHED** (see §R7). It is now its own carded item |
| Placement without touching the shared constant | **Possible** ⇒ no STOP. The refusal is a site-local branch **before** the membership test |

## R3. WHAT WAS BUILT (one code commit, `42db913`)

`orders/order_reconciler.py`, inside the `kill_active` branch of
`_check2_inflight_orphan`. **Branch order (ChatGPT Q2 — no gaps, no double-alert):**

1. **CNC → SPARE** — existing #2b behaviour, unchanged.
2. **CO → REFUSE + CRITICAL escalate** — **NEW**, and placed **BEFORE the membership
   test** (`:2077` vs `:2174`).
3. **unknown/NULL → FLATTEN + CRITICAL** via the existing shared emitter — unchanged.
4. **MIS / remaining allowed → flatten** — unchanged.

- **The refusal places NO order of any kind.** It emits a CRITICAL naming the reason
  (CO position · Audit 3.1: cannot be squared by a reverse order · no parent broker order
  id on this path · operator/EOD action required) with symbol, qty, product, trade_id and
  `site="reconciler_check2"`, through the **existing** notifier — **no second emitter**
  (Q3). A broken notifier still leaves the refusal intact (pinned by test).
- **Predicate reads the RAW BROKER product string** (`bp.product`, G3 style, same as
  #2b), deliberately **not** routed through the local `_PRODUCT_TO_INTENT`.
- **Disposition:** a dedicated `check_name` in the ORPHAN family —
  **`INFLIGHT_ORPHAN_REFUSED_CO`**, distinct from `INFLIGHT_ORPHAN_SPARED_DELIVERY`.
- ⭐ **`success=False`, and the asymmetry is deliberate:** the CNC spare is a correct
  **final** state (nothing owed) and records `success=True`; a refusal is
  **correct-but-INCOMPLETE** — the position is still live and still needs a human — so the
  audit row must say so. The only consumer of that column is a dashboard timeline reader
  (`ops_dashboard/backend/readers/db_reader.py:1484`), which **renders** it and never
  branches on it. ⚠️ Checked explicitly: `success=False` **cannot** drive an escalation —
  the reconciler's only escalating counter (RC12 → `soft_kill`) counts consecutive
  **`BrokerAuthError` cycles**, not failed actions.
- **`_flatten_broker_position` docstring** — the precondition now names **both** CNC and
  CO, states that the one caller applies both branches, and that a new caller must too.

### R3a. ⛔ THE SHARED CONSTANT WAS NOT TOUCHED — asserted, not assumed
`CO` **is** a member of `core.constants.EMERGENCY_FLATTEN_PRODUCTS` (`frozenset({"MIS",
"CO"})`), and that one name is read by **five** sites: `kill_switch` `:1585` + `:1683`,
`eod_squareoff` `:1085` + `:1453`, and the reconciler `:2174`. Removing CO from it would
have silently changed **four** other sites — the exact blast-radius error this campaign
exists to prevent. Hence the site-local branch. **Measured across `42db913`:**
`core/constants.py`, `capital/kill_switch.py`, `orders/eod_squareoff.py`,
`broker/zerodha_adapter.py`, `broker/product_resolver.py` and
`scripts/clear_kill_switch.py` are all **byte-identical** (md5 both sides), and
`core/constants.py` does not appear in the commit's file list at all.

⚪ **Documentation nit, recorded and deliberately NOT fixed:** that constant's own comment
(`core/constants.py:15-17`) still names **four** readers — it predates the reconciler
joining at #2b, so there are now **five**. Fixing it would mean editing the one file this
card forbids touching; it is left for whenever `core/constants.py` is next legitimately
opened. Filed with the campaign's lying-comment inventory.

## R4. THE CADENCE — and ⭐ A NEW MEASUREMENT THAT BOUNDS IT

The refusal is recorded as handled/resolved **nowhere**, so it re-fires for as long as the
trade stays in-flight — intended, and louder than the spare, because a CO position
surviving a HARD_KILL is an unresolved hazard needing human action.

⚠️⚠️ **But it is BOUNDED, and not by this branch — measured 02-Aug, disclosed, NOT
patched.** `_check6_orphan_orders` (**wired in production**, `main.py:2739`) walks
`PENDING_FILL` trades whose ENTRY order is absent from the broker's open orders — which is
*precisely* the FIX-181 shape that reaches this branch, since the entry has already
filled. Its **FIX-B** counter marks the trade **FAILED and releases the reservation on the
3rd consecutive cycle**. CHECK6 runs **after** CHECK2 within a cycle, so cycle 3 still
emits the refusal; from cycle 4 the trade is no longer in-flight, the caller's
`if inflight:` (`:930`) is False, and the **same broker position routes to
`_check2_orphan_adoption`** — which carries a once-a-day per-symbol suppression set and
can label it `HUMAN_ORDER` (IA-P5-02's class).

⇒ **the operator gets ~3 CRITICALs, not an unbounded stream, while the CO position is
still live at the broker and its capital has been released.** A `PENDING` (not
`PENDING_FILL`) trade is outside CHECK6's query and does re-fire unbounded.

### ➡️ FORWARD POINTER — where that downstream consequence is OWNED (do not re-derive)
The two things that follow the bound — **capital released while the position is still
live at the broker**, and **the system's own position filed as `HUMAN_ORDER`** — are
**NOT this item's defect and are NOT repaired here.** They are the *same endpoint* as the
cancel-race, reached by a *second, independent* path, and they are **registered to
debt-ledger #3 ("Fill/cancel seam truth"), IA-P5-02 family**, whose scope note was
expanded for exactly this on 02-Aug. ⛔ **#3's ordering is unchanged** and it is **not a
new register item — 231 stands.** 🔴 **Live relevance: post-flip, a CNC delivery holding
spared by #2b follows exactly this path.** Reachability is **latent-on-latent** (needs a
HARD_KILL **and** an in-flight entry **and** a fill; HARD_KILL has never fired) ⇒
**documented, NOT flip-blocking.** *A future reviewer should not have to re-derive this
relationship: it was measured 02-Aug and it lives in §B.1 row 3.*

### ⛔⛔ BINDING ON ANY FUTURE CHECK6 WORK (R-3, registered 03-Aug-2026)
**If you are here to change CHECK6, this constraint is yours.** The 3-cycle FIX-B bound
described above is what holds **BOTH** shipped items to ~3 CRITICALs rather than an
unbounded stream — **#2b**'s CNC spare (`INFLIGHT_ORPHAN_SPARED_DELIVERY`, `6495baa`) and
**#2c-R**'s CO refusal (`INFLIGHT_ORPHAN_REFUSED_CO`, `42db913`).

⛔ **Any CHECK6 redesign MUST explicitly document whether it changes (a) alert count,
(b) escalation behaviour, or (c) refusal semantics — and state the answer for #2b and
#2c-R BY NAME**, so neither silently regresses.

⭐ **Why it is written here and not only in #3's record:** the coupling runs the wrong way
for discovery. **Nothing in either item's code mentions CHECK6** — the bound is an emergent
property of cycle ordering (CHECK6 runs *after* CHECK2), so a CHECK6 author retuning a
counter gets **no local signal** that two other items depend on it. Registered at three
sites for that reason: this section · `MASTER_PENDING_01-Aug-2026.md` §B.1 row 3 ·
`docs/audit/ledger3_design_registration_03aug2026.md` §5 (the fuller reasoning — ⛔ **not
duplicated here**). ⛔ Registration only: **no CHECK6 change is authorised**, and all of #3
remains gated.

⭐ **This is PRE-EXISTING and SHARED: #2b's CNC spare inherits exactly the same ceiling**,
so that record's "re-alerts EVERY cycle BY DESIGN" is **bounded too** — corrected here
rather than left standing. ⛔ **Not fixed in this card:** bounding is CHECK6's behaviour,
and widening a CO-refusal card into CHECK6 is the blast-radius error above. Backlogged
with Option 2. **Latent today** for both branches (CO doubly dormant; no delivery entries
exist yet) — it arms for CNC with the carry pilot.

## R5. DOWNSTREAM TRACE — the same four consumers #2b traced, re-confirmed

| consumer | matcher | effect of `INFLIGHT_ORPHAN_REFUSED_CO` | verdict |
|---|---|---|---|
| `reports/daily_trade_review.py:93` `_ORPHAN_CHECKS` | explicit **set** | **not** a member | ✅ **correct — a refusal closes nothing.** Pinned by test |
| `reports/daily_report.py:576` | `"ORPHAN" in check_name` | **counted** | ✅ correct — it **is** an orphan detection |
| `scripts/system_manager.py:612` | `check_name LIKE '%ORPHAN%'` | **counted ⇒ warns** | ✅ correct — a CO position refused under a kill *should* warn |
| `reconciliation_log` | columns `(ts, check_name, tier, symbol, trade_id, description, action_taken, success)` | one row per cycle; tier ≠ COSMETIC ⇒ persisted (RC10) | ✅ **no schema change, no migration** |

## R6. VALIDATION

| check | result |
|---|---|
| **Targeted tests** — `tests/unit/test_reconciler_co_refusal.py`, **15 collected, all green** | CO refused, **nothing sold** · CRITICAL emitted naming the reason · a **broken notifier** does not break the refusal · a **short** CO row refused too (the refusal is on PRODUCT, never direction) · case/whitespace variants (`" co "`) still refused · CO under **no kill** ⇒ no product decision at all · MIS still flattens **and** CNC still spares · a CO refusal on one symbol does not silence another · the refused row **stays visible to the next cycle** · the refusal is **not** in `_ORPHAN_CHECKS` · the shared vocabulary is **unnarrowed** · ⭐ a **structural pin** that the CO branch precedes the membership test |
| **RED-on-old** — base worktree, **never a stash**; test file md5-identical both sides | **11 failed / 4 passed on old vs 15 passed on new.** The tests could have been red |
| **#2b suite — UNWEAKENED, and by count too** | **16 collected, all green — the same 16 as at #2b.** Its parametrisation ran over the whole shared set and asserted CO flattens *quietly*; that row is now **wrong, not weakened**, so it is marked **SUPERSEDED-BUT-LEGIBLE** in place and the set is parametrised over `EMERGENCY_FLATTEN_PRODUCTS − {"CO"}` (**still derived from the shared constant, never a hardcoded list**), with a new test asserting the exclusion is *exactly* `{"CO"}` and the shared set is un-narrowed. −1 parametrised row, +1 assertion ⇒ 16 → 16. Single-vocabulary and single-caller tripwires still hold |
| **Diff scope** | `orders/order_reconciler.py` + the new test file + the #2b test file. **`core/constants.py` UNTOUCHED** (§R3a) · `kill_switch` / `eod_squareoff` / adapter / resolver **byte-identical** · 0 status literals · 0 `holdings()` ⇒ **Q4 ordering rule intact, D-8 still blocked** · no schema, migration, config key, cron or new path |
| **Full regression** | see §R6a |
| **Revert** | see §R6b |

### R6a. Regression stamp — ⭐ NEW-FAILURE SET **EMPTY (0)**, base measured fresh

Both halves `pytest tests/unit tests/integration -q --tb=no -rf`, **02-Aug 12:1x–12:4x IST
— same session, same pre-18:15 window, neither crossing midnight** (the two clock rules
the attribution depends on). Base = a worktree at **`0a9e13a`** (the pre-#2c-R tree),
**never a stash**, with the git-ignored `config/instruments.csv` copied in.

| run | result |
|---|---|
| **new code** (primary tree) | **7 failed / 5,506 passed / 4 skipped** — 881s |
| **base** (worktree at `0a9e13a`) | **10 failed / 5,488 passed / 4 skipped** — 875s |
| **`comm -23` (failures in MINE, not in base)** | ⭐ **EMPTY — 0** |

**The base's 3 extra failures are worktree ARTIFACTS, and they are the already-PROVEN
subprocess-PATH class** (the standing rule: *"known env failures" is a label, not a
diagnosis*): `test_t4_deploy_preflight` ×3 (`test_check_tz_passes_on_agreement`,
`test_check_tz_fails_on_broken_utc_form`, `test_ist_now_emits_valid_ist`) spawn
`bash`/`python`, which a scratchpad worktree cannot resolve. ⭐ **The 26 `test_main`
phantoms of the #2b run did NOT recur — because `config/instruments.csv` was copied into
the worktree up front**, which is that mechanism's own control.

**The arithmetic closes exactly on both axes, which independently corroborates the
diagnosis:** collected **5,517 − 5,502 = 15** = *exactly* the new CO-refusal tests (the
#2b file is net 0: −1 superseded row, +1 assertion). Failures **10 − 3 = 7** = measured.
Passes **5,488 + 3 (artifacts, now green in-tree) + 15 (new tests) = 5,506** = measured.

**The 7 standing failures, named — all present in BOTH sets:** `test_fix181`
inflight-orphan **LIMIT-vs-MARKET (the known T3 item the card names)** ·
`test_closure_source_contract` vocabulary scanner (offender
`scripts/backfill_closure_source_w8.py:92`, unrelated — its regex matches only
`OWN_SL|OWN_TGT|OWN_EOD|OWN_KILL|EXTERNAL_UNATTRIBUTED`, none of which this change
introduces) · `test_main` ×4 (3 × `TestContinueFromGate` — the IA-P2-01
production-unreachable gate — + 1 × BL15) · `test_phase17_batch2` flask
max-content-length. *(Identical to the #2b run's 7 by name — a cross-check, not a
dependency: every number here was measured this session.)*

⚪ **The documented q9 consecutive-losses oscillator did not fire in either run** — absent
from both failure sets. A calm pair; no flake needed naming.

⚠️ **METHOD NOTE, recorded because it is a standing trap:** the campaign invocation is the
**narrow** `tests/unit tests/integration`, **not** `run_tests.py`, which runs the full tree
including the 44 never-gated `tests/crash_test/` files — the ones that `load_dotenv()` the
real `.env` and carry the uncovered subprocess hole. Harmless in a worktree (which has no
`.env`), **not** in the main tree. A wide run was started here, stopped, and its partial
output deleted rather than left to be mistaken for a baseline.

### R6b. Revert check — **done, not asserted**
`git revert --no-commit 42db913` in a scratch worktree applied cleanly (exit 0) and the
resulting tree diffs to **ZERO BYTES** against the pre-#2c-R tree `0a9e13a`
(`git diff 0a9e13a | wc -c` = **0**), with exactly the three expected paths showing as
reverted. The change is additive inside one branch of one method plus one docstring; no
extracted helper, no moved code, no renamed symbol.

## R7. ⭐ STEP-1b — A NEW DISCLOSURE, REPORTED AND **NOT** PATCHED

The card asked whether the same Audit 3.1 constraint bites `kill_switch`'s own emergency
sites. **It does — measured at source, all three of its adapter sell sites:**

- `capital/kill_switch.py` places at **`:1629`** (local pass), **`:1708`** (broker sweep)
  and **`:1789`** (the retry loop); the intent is mapped just before the first two at
  `:1599` / `:1698` via the shared `PRODUCT_TO_INTENT`, in which **`CO → "COVER_ORDER"`**.
- **`variety` appears NOWHERE in `capital/kill_switch.py`** (grepped; zero hits) ⇒ it
  always defaults to `"regular"`.
- ⇒ with the intraday coercion **off**, it would send a reverse order with
  `product="CO", variety="regular"` — **the order Audit 3.1 says the broker rejects**;
  with the coercion **on** (today) it sends **MIS** — accepted, doesn't net, **naked MIS
  short**: the identical defect the reconciler just fixed.
- ⛔ **NOT patched here** (the twice-earned *report, don't expand* rule). **It needs its
  own card.**

⭐ **Two corollaries worth carrying:**
1. **`kill_switch` is BETTER PLACED than the reconciler for Option 2** — its open-trades
   query already joins `orders … leg IN ('ENTRY','CO')` (`:1531`) and `orders.order_id`
   **is** the broker id, so the CO parent bracket id is **one column away**.
2. ✅ **`eod_squareoff` is CLEAN** — its position map is only a qty **filter**, and its
   trade-driven pass already does `cancel_order(entry_broker_id, variety="co")`
   (`:1189`, rationale at `:1037`/`:1162`). **It is the reference implementation** the
   other two sites should mirror.

## R8. OPTION 2 — PARKED, WITH AN EXPLICIT UNPARK TRIGGER

**Option 2 = parent-order-id lookup → `cancel_order(variety="co")` on the parent bracket.**
It is the *correct* close for a CO position, and it remains **BACKLOG, not built**.

- **UNPARK TRIGGER (either):** CO trading is **intentionally enabled**, **OR** the broker
  layer provides **reliable parent-order lookup**.
- **OWNER: the CO protocol surface — ⛔ NOT the reconciler.** Per §R7 corollary 1, the
  natural home is `kill_switch` (the parent id is one column from a query it already runs);
  the reconciler's CHECK2 path structurally cannot reach a parent id.
- **Also owed to it:** the CHECK6/FIX-B alert ceiling of §R4 (bounding belongs to CHECK6).

## R9. THE RECORD-ONLY ITEM CARRIED FORWARD (ChatGPT Q4)

**`variety` is never validated against `product`** — anywhere. `place_order` defaults
`variety="regular"` and `_validate_place_order` checks side/qty/price/trigger only. This
is an **independent future hardening item**, carried forward deliberately with **no guard
built** in this card (a guard here would be scope creep into the placement path).

## R10. PAPER / LIVE PARITY (#5/#8)

One shared code path, both modes, one commit; the targeted tests are mode-agnostic.
⚠️ **Parity is proven by TARGETED TESTS, never by a paper drill** — paper nets by
**symbol** while live Kite nets per **(symbol, product)**, so a paper drill of this class
is **vacuously green** (#2c Step-1 finding (f), now a standing rule). This is the class's
first build to be validated entirely under that rule.

## R11. LABEL HONESTY & GATES

- **CO is DOUBLY DORMANT**: never used (805/805 regular) **and** `force_intraday_only:
  true` coerces non-INTRADAY back inside `place_order`. A HARD_KILL has also **never
  fired**.
- ⇒ **Label ceiling: this can only reach `<BUILT>` → `<DEPLOYED>` + dormant-armed.
  `<VERIFIED LIVE>` requires a real CO position under a real HARD_KILL** and will not be
  claimed on anything less.
- ⛔ **Nothing deploys or pushes from this build.** It does not gate the flip, the
  observation day, or the deploy sequence.
