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
| **Disposition recording (the card's step 2)** | **VERIFIED — the card's assumption holds.** This path records the orphan **nowhere**: no per-symbol suppression set (unlike `_check2_orphan_adoption`'s once-a-day `_human_order_symbols`), no `handled` mark, no resolve write. A spared CNC row is therefore **re-seen and re-reported every cycle**. Pinned by test |
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
fallback for unknown — but **a CO position would be exited under an MIS intent**, the
same H-5 wrong-product class the kill_switch sites map away via `PRODUCT_TO_INTENT`.
This is a **fourth thing**, not one of the card's three (spare-CNC / loud-unknown /
spared-log), so patching it here would be the silent scope expansion (G5) the card's
OUT-list forbids. Recorded in the code docstring and here; **no CO position has ever
reached this path**. 🔴 Owed: a ruling, not a fix-in-passing.

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
- 🔴 **R4 — Rama's ruling, unchanged and now unblocked in both directions:** ride the
  Monday-evening deploy stack, **or** name it a documented carry-pilot blocker. Building
  it now keeps both options open; it is no longer an *unbuilt* blocker either way.
