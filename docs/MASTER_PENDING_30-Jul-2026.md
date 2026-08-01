# MASTER PENDING REGISTER — 30-Jul-2026 (Thursday night, market closed)

> # 🔁 SUPERSEDED BY `docs/MASTER_PENDING_01-Aug-2026.md` AS THE WORKING REGISTER (01-Aug-2026) — ⛔ RETAINED AS THE CITED SOURCE AUTHORITY. DO NOT DELETE.
> Read the **01-Aug register** to know **what is open**. Read **this file** for the full
> text of the 29–31 Jul delta, the G/R stale-sweep (N=38), **F1–F6**, the state tags, §4's
> rescued operator knowledge and §7's delete verdicts — the 01-Aug register carries one
> line and a pointer for each.
> ⭐ **Where the two disagree, THIS FILE WINS** and the 01-Aug register is the thing to
> correct.
> ⚠️ **ONE KNOWN DISCREPANCY, recorded by the 01-Aug register (its §D.3) rather than
> edited here:** §3.7's sweep closes **G5 · G6 · G16** with evidence, but §6's `K` list
> does not deduct them — so the carried figure **M=131 still contains those 3**. ⛔ Nothing
> is lost by it (all three are named with evidence in §3.6/§3.7); the 01-Aug register
> places them in its settled band and accounts for them explicitly in its reconciliation.

> **THIS FILE IS A DELTA PLUS AN INDEX. IT DOES NOT REPLACE
> `docs/MASTER_PENDING_28-Jul-2026.txt` — IT SITS ON TOP OF IT.**

The 28-Jul register already consolidated **seven** Downloads editions into a repo
artifact, with its own reconciliation (N=420 = M118 + K91 + J211) and an explicit
not-carried list. ⛔ **Re-doing that work would create a second copy of a register —
the exact thing two days were spent removing.** So this file carries only:

1. what is **owed next** (§1),
2. what **changed** between 28-Jul evening and 30-Jul night (§3),
3. the handful of items that **live nowhere else** and would be lost if Downloads
   were cleared (§4),
4. the **reconciliation** (§6) and the **delete verdicts** (§7).

⛔ **VERIFY, DO NOT TRANSCRIBE.** Every figure in §0 and every ✅ in §3 was measured
against the live VM or the repo on **30-Jul between 19:1x and 20:5x**.

---

## 0. LIVE STATE — MEASURED 30-Jul ~20:4x from the VM. THE MOST PERISHABLE PART.

| | value | vs 28-Jul |
|---|---|---|
| deployed SHA (bare == origin == PC) | **`96a5e66`** | was `ce08668` |
| `main` ahead of origin | **1** — `67939eb` docs, UNPUSHED by design (freeze) | was 1 (docs) |
| schema_version | **45** (== deployed `EXPECTED_SCHEMA_VERSION`) | 45, no migration |
| trading service | `inactive (dead)` · `Result=success` · `ExecMainStatus=0` · exit **17:35:04** · `NRestarts=0` | same shape |
| trades (all time) | **467** | 443 (+24) |
| `trades.closure_source` non-NULL | **50** | 39 |
| `innings` | **322 — UNCHANGED** ⇒ ShadowTracker disable still HOLDING | 322 |
| `gtt_state` rows | **0** — ⚠️ see §4.1, this is BY DESIGN and is a trap | 0 |
| `pb01_watchlist` | **66** | 37 |
| `signals` | **67,182** | 59,230 |
| open trades (OPEN/PARTIAL/PENDING_FILL) | **0** | — |
| kill switch | `SOFT_KILL circuit_breaker_force_close_15:15` — ⭐ auto-clears at 08:15 | same |
| backups | **8.4 G**, `data_store/backups/`, **4 `predeploy-*` still present** | 9.0 G, 4 |
| **broker: 5 CNC holdings + 5 GTTs** | **all 5 `active`**, qty 3, CNC SELL, 2-leg OCO, zero extras | armed 29-Jul |

⏳ **RE-VERIFY THIS BLOCK, NOT THE DOCUMENT:**
`ssh trading-vm 'git --git-dir=/home/ubuntu/trading-system.git rev-parse HEAD'`

---

## ⭐⭐ 1. OWED NEXT — LEAD WITH THIS

| when | what | notes |
|---|---|---|
| **FRI 31-JUL 09:15–11:00** | ⛔⛔ **THE T2 CLOSE — LIVE MONEY, RAMA'S ACTION.** 5 × 3 CNC. | Card: `Downloads/FRI_31-JUL_T2_CLOSE_COMMANDS.txt`. Shares are settled demat ⇒ **this is the real DDPI test**. Thursday's card is SUPERSEDED. |
| **FRI 31-JUL evening** | Push `fix-symdir-27jul` (`300a247`, +7) **+ the unpushed docs commit `67939eb`** | Still ONE code branch. ⇒ goes LIVE **Mon 3-Aug 08:15**. |
| **MON 3-AUG** | Observation day — the symbol+direction rule + mis_filter shadow go live 08:15 | ⛔ kept clear for anything else. |
| **SAT 1-AUG / SUN 2-AUG** | ⛔⛔ **THE BUY-DAY PRODUCT FILTER'S REAL WINDOW — re-flagged 31-Jul, see §3.8/F6** | Mon 3-Aug is **BUILD NOTHING, MEASURE**, and the filter is a **careful-loop** item (it decides a PRODUCT on a live order) ⇒ it is **not** a Monday-night patch. If it misses the weekend, ⛔ do not rush it — **something slips, and which thing is Rama's call.** |
| **BEFORE TUE 4-AUG** | **the buy-day product filter** (Q7, dated commitment) — ✅ **Q9's BL9 trace is DONE (30-Jul), so the filter is the only pre-4-Aug build left** | ⭐ **The filter must land BEFORE anything holdings-aware — see §3.3.** ⭐ Q9 = reachability **UNCHANGED** ⇒ the filter's urgency rests on the **carry pilot alone**. ⚠️ **OPEN (§7.9(f)): 2-part gate (flip goes, pilot waits) or 3-part (all of 4-Aug waits)? Decide BEFORE Monday.** |
| **TUE 4-AUG** | ⚠️ **THE FLAG FLIP — first irreversible step.** ⭐ **The flip and the CARRY PILOT must be SEPARATED** | flip = non-blocker; carry pilot = blocked until the filter ships. |
| **AFTER 4-AUG** | GTT-verification-on-kill (Q8) · reconciliation step 1 · FORCE_EXIT_ALL · security monitor · K1–K3 · T2–T4 · M1/M2 | ⛔ all gated. |

⇒ **Day-by-day authority stays `Downloads/DEPLOY_CALENDAR_28-JUL_TO_04-AUG.txt`** (live through 4-Aug — ⛔ KEEP).

---

## 2. THE FIVE REGISTERS THAT OWN EVERYTHING ELSE — ⛔ POINTERS, NOT COPIES

| register | owns | location |
|---|---|---|
| `docs/MASTER_PENDING_28-Jul-2026.txt` | **R0–R12, Q1–Q6, G1–G25, the 11 refusals, X1–X15, PARKED, §8 the entries finding** | repo ✅ |
| `docs/decisions/00_INDEX.md` | numbered decisions 01–11 | repo ✅ |
| `docs/decisions/ACTIONS_not_decisions.md` | K1–K3, T1–T4, M1–M2, security backlog | repo ✅ |
| `Downloads/DEPLOY_CALENDAR_28-JUL_TO_04-AUG.txt` | the day-by-day sequence + every gate | ⛔ Downloads, KEEP |
| memory `UNPUSHED_PENDING_DEPLOY_LEDGER.md` | every built-but-unpushed head | memory ✅ |

⛔ **For anything those five own, this file gives one line and a pointer.** Nothing
below re-states them.

---

## 3. WHAT CHANGED SINCE 28-JUL — THE ACTUAL CONTENT OF THIS FILE

### 3.1 ✅ CLOSED — the 28-Jul deploy queue is DISCHARGED

`docs/MASTER_PENDING_28-Jul-2026.txt` §3 listed Q1–Q6. **Five of six are now DEPLOYED**
(30-Jul 19:55:30, `ce08668..4cc4d04`, 15 commits):

| id | branch/commit | status |
|---|---|---|
| Q1 | `fix-tests-27jul` (4 commits) | ✅ **DEPLOYED** |
| Q2 | `fix-boot-27jul` (#16a + S4) | ✅ **DEPLOYED** — ⛔ **NOT `<VERIFIED LIVE>`: both first EXECUTE at the Fri 08:15 boot** |
| Q3 | `214a878` → cherry-picked as `264dd5b` | ✅ **DEPLOYED** (log-only) |
| Q4 | `249317d` | ✅ **DEPLOYED** |
| Q5 | `53a2443` + 3 more docs commits | ✅ **DEPLOYED** |
| Q6 | `fix-symdir-27jul` | ⏳ **OPEN → Fri 31-Jul evening, ALONE** |

**Gates, all measured:** clean 17:35:04 self-exit · 0 open trades · §7.1 `#16a` gate
exact + `PROCEED` · reflog protection still `never` · **zero orphaned reservations**
(RESERVE 35 = RELEASE 33 + COMMIT 2). **Regression:** BASE `6be4c1d` 10F/5435P →
AFTER `4cc4d04` 10F/5469P, **new-failure set EMPTY**, +34 tests passing. **Verified 3
ways:** SHA identity · deployed bytes **md5-identical to the pushed blobs** · reflog
`push`+`checkout` both at `19:55:30`. **No schema change** (45==45==45).

✅ **R0 (the T2 arm time) — SPENT.** Armed 29-Jul; 5 CNC + 5 GTTs held.

### 3.2 ⭐ THE T2 SEQUENCE — arm done, close deferred a day, and WHY

The Thursday close was **stood down**, not missed: the shares were still **T1**, so a
sale would have been **BTST** — and the demat debit happens at **settlement, not at the
order**, so an `EXIT=0` would have proved the *order path* and **NOT DDPI**.
⛔ "No TPIN popup" was never the test — an API order shows none either way. A DDPI
failure surfaces as **short delivery**, not as `EXIT=1`.
⇒ Friday's shares are settled demat ⇒ **Friday is the real test.**

### 3.3 ⭐⭐ Q4 REVISED — AND THE ORDERING CONSTRAINT THAT CAME OUT OF IT

Full record: **`docs/audit/q4_hard_kill_delivery_decision_30jul2026.md`** (all four
questions now closed or registered; no decision outstanding on it).

- **HARD_KILL's invariant is NARROWED and STATED:** *"leaves no live INTRADAY
  position"* — not *"no live broker position"*. Delivery survives HARD_KILL.
- ⚠️ **T+1 blindness is right BY ACCIDENT** — the kill does not choose to spare
  delivery, it cannot **see** it. ⛔ Do not close it as "already correct".
- ⛔⛔ **THE ORDERING CONSTRAINT — a hard sequencing rule across three workstreams:**
  **the buy-day product filter MUST land BEFORE anything makes a live component
  holdings-aware.** Applied term by term in
  `docs/audit/reconciliation_redesign_design_30jul2026.txt` §D-8, because it does
  **not** bite everywhere: **step 1 (scope `reconcile_positions` to intraday) is a
  RESTRICTION and is UNAFFECTED**; steps 2 and 4, Q1(b), FORCE_EXIT_ALL and the GTT
  check are **BLOCKED** until the filter lands.
- **Q7 CLOSED** = ship the filter before 4-Aug (dated commitment).
- **Q6 CLOSED** (Rama, 20:30) = **flatten the unknown, AND raise CRITICAL when the
  fallback is taken** — the CRITICAL is **in scope of the filter, not a follow-up**.
- **Q8** = verify GTT **quantity**, both directions (under- *and* over-coverage).
- **Q9** = trace `conditional_allocation_enabled` → BL9 reachability. **REQUIRED
  before 4-Aug**, read-only.
- **Filter spec, complete, in one line:** *restrict flatten + FIX-181 sweep to
  `product in ("MIS","CO")` (from EOD6/FIX-015) · fallback-FLATTEN a NULL product ·
  raise CRITICAL naming that trade when it does.*

### 3.4 🔴 NEW FINDINGS — registered 30-Jul, none a blocker

- **F1 — `reconcile_positions` is BLIND to delivery from T+1.** It reads
  `positions()` only (`:158-168`; ⛔ **never `holdings()`**) ⇒ **a 15:45 SUCCESS is NOT
  evidence the book is flat.** ✅ Refuted: 15:15/15:17 do **not** force-close CNC.
  ⚠️ HARD_KILL **does**, on the buy day only (that is §3.3's filter).
  ⭐⭐ **TRACED 30-Jul night — and the "daily false CRITICAL" wording was too strong.
  F1 IS REAL BUT LATENT: it has NEVER FIRED AND COULD NOT HAVE.** The misfire needs a
  delivery trade `OPEN` in the **live** DB; MEASURED, the only CNC orders ever written
  there are **3** — AVL FAILED, SETL CANCELLED, HARIOMPIPE CANCELLED — **none** reaching
  `OPEN/PARTIAL` with `qty_filled>0`. ⇒ **gate = the first live delivery trade (4-Aug
  flip + carry pilot).** ⛔ **Two OPPOSITE cases must not be blurred:** the T2 basket has
  **no** live trade row ⇒ it produces **ORPHAN_AT_BROKER** (a *test artefact* of the
  isolated DB — MEASURED 29-Jul: 5 rows, `broker_qty=3`; and 30-Jul: **zero** rows,
  SUCCESS); the real-delivery case produces **MISSING_AT_BROKER**, the opposite
  direction. ⇒ **Folds into reconciliation step 1** (`D-4(a)`, scope to intraday) —
  ⛔ **not a separate workstream**. Full evidence: `ACTIONS_not_decisions` → "F1".
- **F2 — `fix-tests-27jul` shipped 3 test failures.** `tests/crash_test/
  test_ct_harness_safety.py` ×3, **one root cause**: `SCRATCH_DIR = data_store/
  ct_scratch` (`ct_utils.py:80`) sits **inside** the dir `1542c6e`'s autouse
  `_block_real_data_store` guard blocks, and those tests never request the
  `allow_real_data_store` opt-in. **Test-only.** Fix = point `CT_SCRATCH_DIR` outside
  `data_store/`, or grant the opt-in. ⇒ **This UPDATES `ACTIONS_not_decisions` T2**:
  `tests/crash_test/` *is* collected by `run_tests.py` (`pytest tests/`), even though
  it is not by `pytest tests/unit tests/integration`. **T2's "never run" is true of
  the narrower command only.**
  ⚠️⚠️ **AND THE STRUCTURAL POINT: because the regression BASE *must* include
  `fix-tests-27jul` (without it the suite posts real Telegram alerts), a failure that
  commit INTRODUCES can never appear in the before/after delta.** Found by **reading**
  the BASE set, not counting it.
- **F3 — a mandated gate could not prove what it claimed.** The close rehearsal
  (confirm flag omitted ⇒ `EXIT=2`) **cannot reach `load_all()`**: `main()` returns at
  `if not args.confirm` *before* `_build_live_adapter()`, where `load_all()` lives
  (`:157`). The refusal text names the confirm guard ⇒ measured, not inferred.
  ⇒ **A direct `load_all()` run on the deployed tree was added** ⇒ `CONFIG_LOAD_OK`.
  ⛔ **Do not reuse the rehearsal alone as evidence about config.** Recorded as case #6
  in memory `feedback_verify_rc_not_output`.
- **F4 — the EOD report gives the operator a WRONG instruction.** *"Kill switch:
  SOFT_KILL — needs `deploy/resume.sh` before market open"* appears **every day**
  (24, 28, 29, 30-Jul). The 15:15 kill is a **prior-day** kill that auto-clears at
  08:15; `resume.sh` begins with `systemctl stop`, so run **after** a boot it would
  stop live exit management and the 15:17 squareoff. **Standing, not new tonight.**
  ⇒ Third wrong site, after the two `ACTIONS_not_decisions` K1(iii) already fixed.
  Caution added to the Friday card.
- **F5 — a new error class in the census:** Zerodha **refuses MIS** on some symbols
  ("MIS orders are currently blocked for X, place CNC instead") — 6 ERROR lines on
  30-Jul across ASAHISONG + GALLANTT (2 events × 3 loggers). Handled correctly, no
  position taken. ⭐ Relevant: a staged-but-never-pushed branch
  `mis-tradability-filter-30jun` with 22 tests (`PATHS.md:235`) is exactly this.
  Also 3 transient `kt-oms` NetworkExceptions — retried, no escalation.

### 3.6 ✅ THREE G-ITEMS CLOSED 30-Jul night (read-only / doc-only)

**G16 — `DEPLOYMENT.md` stale paths ⇒ ✅ CLOSED. ⛔ NO EDIT MADE, AND THAT IS THE
CORRECT OUTCOME — the register item was STALE.**
The register claimed the `.env`/backups lines read `/home/ubuntu/trading-system/`
(missing `systems/`). **They do not.** `DEPLOYMENT.md` asserts exactly TWO distinct
paths and both are already right: `/home/ubuntu/systems/trading-system/.env`
(×4) and `/home/ubuntu/systems/trading-system/data_store/backups` (×1 — already the
correct `data_store/backups`, not `~/backups`). **Both VERIFIED to EXIST on the VM**;
the wrong forms exist neither in the doc nor on the VM. **Git says why: `67850b8`
(2026-07-16) "docs(deployment): correct the .env + backups paths to the real VM
layout" — the fix landed TWELVE DAYS BEFORE the register recorded it as open.**
⭐ Editing a correct file to satisfy a stale register entry would have been the
defect. ⚠️ *(`/home/ubuntu/backups` does exist on the VM but `DEPLOYMENT.md` never
references it — it is a different, unrelated directory.)*

**G4 — the "V3 DECISION CONTENT SPECIFICATION v1.0" ⇒ ⚠️ CONFIRMED ABSENT.
Status: DOCUMENTED-CURRENT-STATE; the DECISION is owed by Rama. ⛔ No spec invented.**
**SEARCH WIDTH, stated so the absence is falsifiable:** (1) repo filename variants
`*v3*decision*` / `*decision*content*` / `*v3*spec*` / `*content*spec*`, case-insens.
— **0**; (2) repo full-text on the title — 13 hits, **every one a CITATION**, checked
line by line (the only document-shaped hit was a dangling git blob that proved to be
an old copy of `core/config_loader.py`); (3) `git log --all --diff-filter=A` —
**never added**; (4) **`docs/v3/`, where the V3 docs actually live — 9 files, all
STEP4-10/PHASE0 plans and gapmaps, no content spec**; (5) VM by filename across the
whole home — **0**; (6) VM by content (`operator_docs`, `preserved`, repo `docs/`) —
5, the same citations.
⭐ **AND AN INDEPENDENT SECOND WITNESS ALREADY EXISTED:**
`docs/audit/pb01_simulation_feasibility_2026-07-24.md:30` recorded the same absence
on 24-Jul and adds the mitigating fact — **"its thresholds live as config, so the
detection content is reconstructable even without the prose spec."**
⚠️ **THE EXPOSURE IS SMALLER THAN "PB-01's PARAMETERS HAVE NO WRITTEN SOURCE"
IMPLIES, and the reason is worth stating:** PB-01 is `enabled: false`, FAIL-CLOSED,
and **can never place an order by construction** (G-NO-ORDER, proven by test);
`v3_chain_mode: shadow` is LOG-ONLY. Its own YAML says every number in it is
**"a schema-valid SEED for the registration stub only"** — the real parameters were
always meant to arrive as config at the Step-10b build.
**OBSERVED-FROM-CODE — the operative values today. ⛔ CURRENT BEHAVIOUR, NOT A
RATIFIED SPEC:** `level_lookback_sessions: 20` · `gap_guard_pct: 0.03` ·
`pullback_proximity_pct: 0.005` · `confirm_min_body_frac: 0.50` ·
`confirm_volume_mult: 1.20` · `hold_buffer_atr_mult: 0.20` · `atr30_period: 14` ·
`baseline_candles_per_session: 75` · `rr_floor: 2.0` · `sl_buffer_atr_mult: 0.20`
(all in `config/system_config.yaml`, each with an explanatory inline comment).
Missing-data rules are stated in `screening/hard_gate.py:218-222` (G-RR must-have →
missing S&R FAILS; G-HTF and G-EXTREME fail-OPEN).
⇒ **WHAT IS ACTUALLY MISSING IS A DECISION, NOT A DOCUMENT.** Nobody ratified what
PB-01's gates/window/thresholds *should* be. ⛔ **Do not close this by writing the
spec from the code — that manufactures a spec to match whatever the code does.**
**The decision is owed before PB-01 could ever be promoted to trading**, which is
gated far beyond 4-Aug ⇒ **NOT urgent, but it must not be quietly dropped.**

**G6 — PB-01 25 → 12 ⇒ ✅ CLOSED, BENIGN. It is an INPUT fact, not a system fact.**
⭐ **WIDTH FIRST, and the width inverts the question.** Per `trading_date`:
**25 (28-Jul) · 12 (29-Jul) · 15 (30-Jul) · 14 (31-Jul, PENDING)**. Three of four
days sit at 12-15 ⇒ **25 is the OUTLIER and 12 is ordinary variance.** The "drop"
was a return to baseline.
**THE DECOMPOSITION, from the `pb01_capture` heartbeat `message` (the authority):**

| capture night | `symbols=` (INPUT) | `queued=` | rows written | skipped `<20 sessions` |
|---|---|---|---|---|
| 27-Jul | 27 | 27 | 25 | 2 |
| **28-Jul** | **12** | **12** | **12** | **0** |
| 29-Jul | 16 | 16 | 15 | 1 |
| 30-Jul | 16 | 16 | 14 | 2 |

⇒ **The INPUT moved: 27 → 12 → 16 → 16.** On the 12-day the pass-through was
**perfect — symbols=12, queued=12, written=12, zero skips.**
**Of the four candidate causes:** ✅ **fewer signals delivered (INPUT) — CONFIRMED** ·
❌ more rejected by a gate — **REFUTED** (0 skips that night) · ❌ capture-side
truncation — **REFUTED** (`queued == symbols == rows` on every night) · fewer
qualifying setups is the same fact one level upstream (inside Chartink) and is
equally benign.
⭐ **CAPTURED-vs-SURVIVED, the distinction that bit here before: 12 was what was
CAPTURED**, and all 12 survived to terminal statuses (2 CONSUMED · 2
EXPIRED_WINDOW · 2 INVALIDATED · 6 SKIPPED_GAP). **No loss at either boundary.**
⚠️ One observation registered, NOT a G6 blocker: `sr_detector` **token lookup
failed** fired **7×** on 30-Jul (vs 1/0/0 before) — ETF-style symbols (LIQUIDBETF,
AUTOBEES) absent from the instrument cache. They are skipped with a stated reason
and PB-01 should not trade them, so it is benign; worth a note if the count keeps
climbing.

### 3.7 ⭐⭐ THE STALE-REGISTER SWEEP (30-Jul night) — every open G/R item, one verdict

**Why:** two of three items earlier tonight **dissolved on contact** — G16 was fixed
16-Jul (twelve days before it was logged open) and G4 had an independent 24-Jul
witness. That is a signal the register carried entries resolved out-of-band and never
marked. **"Open" must mean open.**
⛔ **The bar: CLOSE only with a cited commit / file:line / measurement. Ambiguous ⇒
STAY-OPEN. Aggressive on evidence, conservative on inference** — closing a real
kill/capital item on a hopeful reading is the worse error.

#### ✅ CLOSE-NOW (4 top-level) — each with its evidence

| item | evidence |
|---|---|
| **G5** — no send-side alert audit trail below CRITICAL | `214a878` → cherry-picked `264dd5b`, **DEPLOYED 30-Jul 19:55:30** in `4cc4d04`; `_audit_send` verified present in the **deployed VM tree** (3 refs) and md5-identical to the pushed blob. ⚠️ **FORWARD-ONLY** — historical gaps stay unanswerable, there is no source to reconstruct them from. 🏷️ `<DEPLOYED>`, **not** `<VERIFIED LIVE>`: it first executes on the next alert send. |
| **G6** — PB-01 25 → 12 | §3.6. Width **25·12·15·14**; heartbeat `symbols=` **27→12→16→16**; the 12-day was `symbols=12 queued=12 written=12`, **zero skips**. INPUT fact; gate-rejection and capture truncation both **refuted**. |
| **G16** — `DEPLOYMENT.md` stale paths | §3.6. **`67850b8` (16-Jul)** already fixed it; both asserted paths **verified to exist on the VM**. ⛔ No edit made — the register entry was stale, not the doc. |
| **R0** — the T2 arm time | **SPENT** — armed 29-Jul 11:31; 5 CNC + 5 GTTs, all re-verified `active` broker-side 30-Jul. |

#### 🔗 RETIRE / RECLASSIFY (1 top-level)

| item | disposition |
|---|---|
| **G14** — S5 second-half · X2 `eod_verify` columns | ⇒ **DECIDED PARK, not a TODO.** X2 **re-measured tonight: `eod_verification` 30 rows, `pnl_variance = 0.0` on ALL 30, zero nulls, min=max=0.0** — still dead. It is parked **because fixing it ARMS a dormant P&L check**, which is a decision, not neglect. ⛔ Stop reading it as pending work. |

#### 🔗 SUB-ITEM resolutions — folded into their survivor, survivor stays open

- **G15** — the three `event_type` sites: **CONFIRMED closed** by `5c70def` (21-Jul);
  `_is_critical_event()` verified at `reports/daily_report.py:262`, used `:575`.
  ⏳ **G15 stays open** for the *remaining* cousins — `REJECTED_KILL_SWITCH` verified
  **still a free-text literal** at `ops_dashboard/backend/readers/db_reader.py:41`.
- **G19 — M-O9 CLOSED as INERT.** Re-measured at **467 trades**: exit reasons are
  SL_HIT 92 · TGT_HIT 65 · MANUAL 43 · … — **no trail exit has ever fired.** Reopen
  trigger = the entry protocol leaving `LIMIT_TRIPLE`. ⏳ G19's other 6 sub-items open.
- **G22 — DG-3 MERGED into G23.** The register itself said *"DG-3 == G23, counted
  once"*; the duplicate line is retired. ⏳ DG-1 / DG-2 stay open.
- **G24 — P5-4 CLOSED.** `test_control_tower_phase1a` is **not** in tonight's
  enumerated 10-failure set (AFTER tree `4cc4d04`, in-window) ⇒ it passes.
  ⏳ P5-2 / P5-3 stay open.
- **G12 — T2's wording CORRECTED, item stays open.** *"`tests/crash_test/` is never
  run"* is true **only of the narrower `pytest tests/unit tests/integration`**.
  MEASURED tonight: `run_tests.py` runs `pytest tests/`, which **does** collect
  `tests/crash_test/` — that is how F2's three failures surfaced. ⏳ T3/T4 unchanged.

#### ⏳ STAY-OPEN (20) — ⛔ deliberately NOT closed

**Money path** (need re-parity + Rama's sizing call): **G2** scorer 25/100 constant
0.0 · **G3** sector resolution (blocks R2/D1) · **G8** leverage gap.
**Kill / capital, careful-loop, gated after 4-Aug or the first live kill:** **G11**
(K1/K2/K3) · **G13** first live HARD_KILL · **G18** the 10 MEDs · **G25** Slice-2.5
(M-C2/M-O7/M-O6/H-6 — **correctly bundled to arm WITH delivery**).
**Other genuinely open:** **G1** liveness-probe 95-min gap (own small design; the
manual ~17:10 check is the compensating control) · **G7** evidence infrastructure ·
**G9** built-and-never-run — **re-verified tonight: `conditional_allocation_enabled:
false`, `mis_filter.enabled: false`, both still never-run** · **G10** 20 live keys in
the test env (the network guard shipped tonight is only the FIRST layer; a separate
test token is still owed) · **G12** · **G15** · **G17** AB-910 phases 9+10 never
produced · **G19** · **G20** (~55 line items — ⛔ pointer only, do not touch the two
July audit files) · **G21** BK-1…BK-8 · **G22** · **G23** B3 authoritative flip ·
**G24**.

#### ⚠️ NEEDS-RAMA (13) — open, but only a decision moves them

**G4** (the V3 spec: investigation CLOSED tonight; what remains is a **decision** —
nobody ratified PB-01's gates/window/thresholds) · R2 D1 sizing (blocked by G3) ·
R3 D2 direction · R4 D3 min_pass · R5 prune cap · R6 backup cap · R9 `mis_filter`
enforcing flip · R10 conditional-allocation flip · R11 `predeploy-*` · R12 WAAREERTL.

⚠️ **SUPERSEDED SAME NIGHT (23:3x) — this list was 13 and is now 10.** Rama decided
three of them: **R1 ✅ CLOSED (register item)** and **R7 ✅ CLOSED** → §5.0, kept for
record; **R8 🟪 DECISION DEFERRED** → §5 Bucket 1, retagged with its live caveat.
⛔ **They moved state; they did not vanish.** See the rebalanced count below.

⭐ **A3 check on R9/R10 — is the underlying work already done?** **R10: YES, the code
is BUILT** (`resolve_bucket_allocation`, `fund_manager.py:111`, unit-tested; traced
tonight under Q9) ⇒ R10 is **purely a flip decision**. **R9: the SHADOW half is built
on `fix-symdir-27jul` and goes live Mon 3-Aug**; the **enforcing** flip is the
decision. ⇒ Neither is closeable, but neither is blocked on engineering.

#### ⛔ RECONCILIATION — N = C + M + P + D

```
  N  open G/R items at the start of the sweep (G1-G25 + R0-R12)  =  38
  C  CLOSE-NOW      G5 · G6 · G16 · R0                           =   4
  M  RETIRE/RECLASS G14 (decided park)                           =   1
  P  STAY-OPEN                                                   =  20
  D  NEEDS-RAMA     G4 + R1-R12                                  =  13
                                        C + M + P + D  = 4+1+20+13 = 38  ✅
```

**⚠️ REBALANCED 30-Jul 23:3x — THREE ITEMS CHANGED STATE (⛔ none vanished).**
Rama decided R1, R7 and R8, so they move OUT of `D` — but they are **kept for
record** in §5.0 / §5 Bucket 1, not deleted:

```
  N  unchanged                                                   =  38
  C  CLOSE-NOW (evidence)   G5 · G6 · G16 · R0                   =   4
  M  RETIRE/RECLASS         G14 (decided park)                   =   1
  P  STAY-OPEN              (unchanged)                          =  20
  D  NEEDS-RAMA             G4 + R2·R3·R4·R5·R6·R9·R10·R11·R12    =  10   (was 13)
  ✅ DECIDED 30-Jul         R1 · R7      -> §5.0, kept for record =   2   (new)
  🟪 DEFERRED 30-Jul        R8           -> §5 B1, retagged       =   1   (new)
                          C + M + P + D + ✅ + 🟪 = 4+1+20+10+2+1 = 38  ✅
```
⛔ **`D` fell 13 → 10 because THREE ITEMS MOVED, not because three disappeared.**
Each is named above and each is still readable in the register under its tag —
which is the whole point of the tags.
⚠️ **And the ✅ on R1 closes a REGISTER LINE, not the profitability problem** — see
the 📌 standing note in §5.0. **The edge question is DEFERRED, not resolved.**
⚠️ **Sub-item closes (M-O9 · DG-3 · P5-4 · G15's three sites · G12's T2 wording) are
recorded INSIDE their surviving parent and are NOT counted as top-level closes** —
counting them twice would inflate the close rate, which is the vanity this sweep
exists to avoid. ⛔ **Nothing vanished: every one of the 38 appears above by name.**

### 3.5 ⚠️ CORRECTIONS TO NUMBERS THAT WERE CARRIED AND ARE WRONG

- **"33–34 test failures past 18:15" is STALE.** Measured tonight in-window: **10**.
  It predates `fix-tests-27jul` (which fixed the network/data_store/logs guards and
  the instance-lock flake). ⛔ **Take a fresh base; never carry the number.**
- **"crontab 110 lines" is WRONG — it is 160.** Installed `crontab -l` is
  **byte-identical** to canonical `deploy/cron/trading-system.cron`; canonical
  unchanged tonight ⇒ the post-receive *"crontab AUTO-INSTALLED"* was again a faithful
  **no-op**, and `forward_shadow_record` is intact at `15 18 * * 1-5`.
- **`backups` 9.0 G → 8.4 G**, still **4 `predeploy-*`** ⇒ **R11 UNCHANGED and still
  open.** (⚠️ They are at `data_store/backups/`, **not** `~/backups` — a wrong path
  reads as "they are gone".)

---

### 3.8 🔴 REGISTERED 31-Jul NIGHT — **F6, a PROCESS finding: a scheduling fact was asserted from memory and nearly moved an irreversible step**

- **F6 — a trading-day claim reached a re-gating proposal without ever being checked
  against its source.** On 31-Jul ~22:0x, after the symdir deploy was already recorded,
  it was claimed that **Mon 3-Aug-2026 is an NSE holiday** ("Sunday / Independence-Day
  weekend") and that the observe-Mon → flip-Tue sequence therefore had to move. Rama
  pulled the primary source at 22:09 — circular **NSE/CMTR/71775 (12-Dec-2025)** — which
  refutes it twice over: **3-Aug-2026 is a MONDAY**, and Independence Day is **15-Aug**,
  a **Saturday**, on the circular's weekend list. ⇒ **3-Aug and 4-Aug are normal trading
  days; there was no collision.**
  - ⭐⭐ **ROOT CAUSE = MISREAD, NOT A DATA DEFECT — and the two have opposite fixes, so
    the distinction IS the finding.** `config/nse_holidays_2026.yaml` (resolved at
    `config_loader.py:2166`; sole source, every consumer routes through
    `utils/holiday_guard.py`) has **no 2026-08-03 entry and no August entry at all** —
    the list jumps `2026-06-26` → `2026-09-14`. Its only "Aug" string is the comment
    `#   15-Aug-2026 (Sat) Independence Day`, quoted verbatim. ⛔ **A COMMENT IN A DATA
    FILE WAS READ AS DATA.** The loader confirms: `is_trading_day` = **True** for both
    2026-08-03 and 2026-08-04, set size **15**. ⇒ **NO holiday-file edit is owed**, and
    the "register a gated boot-path config change" branch does **not** apply.
  - ✅ **THE WHOLE FILE RECONCILES TO THE CIRCULAR EXACTLY — 15/15 trading holidays and
    4/4 weekend holidays**, date and description, no extra, none missing. ⇒ **no
    mismatch finding to register.** (Done while the circular was open, per the standing
    "it is cheap now" rule.)
  - ✅ **NOTHING HAD TO BE REVERTED — the false claim never reached an artifact.** Clean
    tree · no commit after `2b2ea77` on any branch · no stash · the card still
    `MON_03-AUG_OBSERVATION_CARD.txt` with `'2026-08-03'` three times in its body ·
    Downloads calendar **md5-identical** to the tracked one. ⭐ The no-holiday-language
    sweep was run **with a control first** (same corpus returns 17 + 2 hits for
    "3-Aug"), so the zero is a real absence and not a dead search.
  - ⛔ **THE CONTROL THIS EARNS:** *any holiday / trading-day / weekday claim that
    affects SCHEDULING must be verified against the file text — quote the line, or do
    not make the claim.* A weekday is a one-line check; nothing about it is cheaper to
    assume than to verify. ⭐ It is the same "verify, don't assume" rule the project
    already runs on — it failed on the one class of fact that felt too ordinary to check.
  - ⚠️ **THE ONE REAL CONSEQUENCE: the buy-day product filter's window is short again**
    (the false slip would have bought a day). See §1 and calendar §7.9(f) — including
    the **open Rama decision** on whether a filter slip postpones only the carry pilot
    or all of 4-Aug.
  - ⚠️ **2027 UNCHANGED AND STILL OWED:** `config/nse_holidays_2027.yaml` absent
    (re-checked 31-Jul). First 08:15 boot of 2027 raises `ConfigMissingError` and does
    not start; 15-Dec email reminder DEPLOYED (`cbcad2c`). ⛔ Never invent the dates.
  - Full evidence and the verbatim file lines: calendar **§7.9**.

---

## 4. ⭐ ITEMS THAT LIVE NOWHERE ELSE — RESCUED FROM THE DOWNLOADS CARDS

⛔ **These are the reason the delete list in §7 is safe.** Each was verified absent
from the repo, the four registers and memory before being copied here.

### 4.1 ⚠️ THE `gtt_state` TRAP — read this before verifying protection

**Live `data_store/trading_system.db` has `gtt_state` = ZERO ROWS, and that is
CORRECT.** The T2 script writes `gtt_state` only into a **per-run throwaway store**
(`_throwaway_store_path()` = `data_store/t2_proof_<ts>/t2_proof.db`, its own subdir so
the ATTACHed `analytics.db` sibling is isolated too); `_assert_isolated()` **refuses**
to run against the live DB.
⛔ **Do NOT query live `gtt_state` to confirm the five GTTs — it shows 0 and reads as
"protection gone".** **The broker API is the only authority.**

### 4.2 The evening-check procedures — ⛔ these existed ONLY in `TUESDAY_EVENING_CHECKS_28-JUL.txt`

**(a) The day's error census.** ⛔ **Classify, do not count.**
```
ssh trading-vm 'cd ~/systems/trading-system && grep -cE "\"level\":\"(ERROR|CRITICAL)\"" logs/system_<DATE>.log'
```
**Known-good baseline** (anything outside it is a finding):
- 1 × CRITICAL `08:15:1x` startup kill-switch notice
- 3 × CRITICAL `15:15:01` routine circuit-breaker triple (`main` ×2 + `kill_switch` ×1)
- 2 × ERROR **per** slippage rejection (`order_placer` + `signal_processor`)
- 1–3 × ERROR at `17:35:0x` feed teardown on the clean self-exit
- *(30-Jul added: 3 × ERROR per broker MIS-block event, and transient `kt-oms`)*
⚠️ **M2 still applies: the census CANNOT see cron-process CRITICALs** — those live in
`logs/cron-*.log`. "No new CRITICAL in the census" ≠ "no new CRITICAL".

**(b) Orphaned capital reservations.** ⛔ Run **after** the book settles (~17:40).
```sql
select r.reservation_id, round(r.amount,2) as reserved, substr(r.reason,1,30) as what
from fm_ledger r
where r.date="<DATE>" and r.entry_type="RESERVE"
  and not exists (select 1 from fm_ledger t where t.date="<DATE>"
                  and t.reservation_id=r.reservation_id
                  and t.entry_type in ("RELEASE","COMMIT"))
order by r.ledger_id;
```
**EXPECT ZERO ROWS.** Any row = capital reserved and never returned = ⛔ ABORT the
deploy. *(30-Jul: 0 rows; RESERVE 35 = RELEASE 33 + COMMIT 2.)*

### 4.3 The T2 operator knowledge worth keeping for the 4-Aug carry pilot

- **ASM/GSM/T2T check** — ⛔ **NOT exposed by the Kite API.** It is Rama's scrip-page
  check per symbol, and **nobody else can do it**. Flagged ⇒ drop that one stock,
  proceed with the rest. ⛔ YESBANK / NHPC stay excluded (circuit band, settled).
- **Manual-GTT fallback formula** (if a GTT ever has to be placed by hand):
  `SL trigger = fill × 0.90` · `SL limit = SL trigger × 0.97` ·
  `TGT trigger = fill × 1.10` · `TGT limit = TGT trigger × 0.995` ·
  type **OCO (2-leg) · SELL · CNC**. *(The 0.97/0.995 are
  `gtt_sl_limit_offset_pct` 3% and `sl_limit_offset_pct` 0.5% — derivable, recorded
  so nobody recomputes under time pressure.)*
- **The GTT lister** — the **only** authority on GTT state (⛔ Kite's web UI never
  shows a GTT id):
```
ssh trading-vm 'cd ~/systems/trading-system && set -a && . ./.env && set +a && PYTHONPATH=. /home/ubuntu/systems/venv/bin/python -c "
import json,os
from pathlib import Path
from kiteconnect import KiteConnect
tok=json.loads(Path(\"data_store/session/zerodha_token.json\").read_text())[\"access_token\"]
k=KiteConnect(api_key=os.environ.get(\"ZERODHA_API_KEY_LFL836\") or os.environ.get(\"ZERODHA_API_KEY\")); k.set_access_token(tok)
for x in k.get_gtts(): o=x[\"orders\"][0]; print(x[\"id\"], x[\"status\"], x[\"condition\"][\"tradingsymbol\"], o[\"quantity\"], o[\"product\"], len(x[\"orders\"]))
"'
```
- ⛔ **Git Bash, never PowerShell** — PowerShell silently strips the inner quotes
  (MEASURED twice, 28-Jul).
- **Step-zero hash** of the T2 script: `0a3c505c25e6518c7b774619` (all copies agree).
- **DP charges ~₹15–16 per scrip per delivery SELL** ⇒ ~₹75–80 on a 5-stock basket
  against ~₹644 invested (~12%). **The P&L will look bad; that is the cost of the
  test, not a fault.**

---

## 5. ⭐⭐ THE TRULY REMAINING ACTIONABLE BACKLOG

### 🏷️ LEGEND — READ THIS FIRST. The tag is what separates *done* from *parked*.

| tag | means | how to read it |
|---|---|---|
| ✅ **CLOSED (decision final)** | Rama decided; **nothing lives on**. | ⛔ Not open work. Kept for record only. |
| 🟪 **DECISION DEFERRED** | Rama **parked** it; a decision is **still owed** later. | ⚠️ Still owed — "keep as a placeholder" defers the choice, it does not make it. |
| 📌 **STANDING NOTE / LIVE CAVEAT** | The technical reason it was raised, **still true after the decision**. | ⛔⛔ **A closed decision and a resolved caveat are DIFFERENT THINGS.** Collapsing them is how a live constraint gets forgotten. |

⛔ **Kept-for-record ≠ open work.** Decided items live in §5.0 below, not in the
live buckets. Post-sweep (§3.7) + the 30-Jul decisions, this is the whole live list.

---

### 5.0 ✅🟪 DECIDED — KEPT FOR RECORD (⛔ not open work)

**R7 — GEMINI WATCHMAN · ✅ CLOSED (decision final)**
> **RAMA, 30-Jul:** *"Continue using it. Do not retire it."*
📌 **ONE CAVEAT SURVIVES, as a DO-NOT:** ⛔ **do not tighten the prompt** — that was
the single option flagged as carrying risk; the keep-and-use option Rama chose is
free. **Nothing else lives on.**

**R1 — STRATEGY REVISION · ✅ CLOSED as a REGISTER ITEM**
⛔⛔ **NOT "the edge is solved".**
> **RAMA, 30-Jul:** the existing **15 strategies (12 intraday + 3 delivery) remain
> as-is**; future scanner improvements — **hammer, evening/morning-star patterns** —
> and any **NEW strategies** are **BACKLOG**, to be done later.
⭐ That framing is right and needs **no system change**: a new strategy is a
`strategies.md` / config addition, not a code change.
📌📌 **STANDING NOTE — THIS DOES *NOT* CLOSE WITH THE REGISTER LINE:**
**R1 was raised because the system shows no measurable profitable edge.** Three
independent lines — **statistical** (the band inversion: a high score marks an
already-EXTENDED move) · **geometric** (the V3 RR gate: median 0.33 R:R against a
2.0 floor) · **arithmetic** (win rate **~38-39%** against a **~43.5% breakeven**) —
converge that **the entries buy EXTENSION**.
⇒ **Parking the register item is a valid decision. The EDGE QUESTION is DEFERRED,
NOT RESOLVED**, and it reopens if/when Rama chooses to work on strategy.
⛔ **Do not let the ✅ imply the profitability problem has been answered.**

---

### BUCKET 1 — NEEDS RAMA'S DECISION (nothing else can move these)

| # | the decision | why it is yours |
|---|---|---|
| **R10** | **Conditional-allocation flip** — the 4th delivery flag (4-Aug). | ⭐ **The code is BUILT and traced** (`resolve_bucket_allocation`, `fund_manager.py:111`; Q9 trace 30-Jul). Purely a flip decision. Without it ~70% of capital strands in the idle intraday bucket. |
| **R9** | `mis_filter` **enforcing** flip. | The SHADOW half ships Friday, live Mon 3-Aug. Enforcing touches the **signal path** ⇒ your call. |
| **R2** | D1 sizing / `max_concentration_pct`. | ⛔ **HOLD — blocked by G3.** ⚠️ Read `sector_exposure()`'s handling of `'UNKNOWN'` **before** this moves; the error direction inverted. |
| **G4** | **Ratify PB-01's gates / window / thresholds.** | The "V3 DECISION CONTENT SPECIFICATION v1.0" **does not exist** (6-search width, 30-Jul). Current values recorded OBSERVED-FROM-CODE. ⛔ **A decision is missing, not a document** — owed before any PB-01 promotion, gated far beyond 4-Aug. |
| **R8** 🟪 | `TELEGRAM_CHANNEL_SECONDARY` — 🟪 **DECISION DEFERRED**, ⛔ **not CLOSED.** **Rama, 30-Jul:** *"Keep as a placeholder / provision for future use. No implementation now."* ⭐ That **parks** the choice; it does not make it. | 📌 **LIVE CAVEAT — the reason it stays owed:** if this channel is **EVER** enabled it **MUST** be decided **together with the M-A2 8-second send budget**. The budget is **SHARED across channels**, so a 2nd channel makes the alert ladder **~52 s** and the 8 s deadline would **cut channel 2 off mid-ladder**. ⛔ **Enabling it without that decision silently breaks the alert path.** Carry this caveat wherever R8 appears. |
| **R11** | `predeploy-*` backups — delete, or write the retention rule. | ⚠️ **Renaming to `pre_*` is a DELETE in disguise.** Re-verified 30-Jul: **4 files present, backups 8.4 G.** |
| **R5 · R6** | prune-retention cap value · backup-retention cap value. | Both are "pick a steady-state number". R6: measure a week of post-clear nights first. |
| **R3 · R4 · R12** | D2 direction · D3 min_pass (downgraded) · WAAREERTL 23-Jul external close. | R3 needs months + a positive control. R12 may be a **capital** question, not execution. |
| **ops** | 2FA seed → VM-only (**Fri 7 / Sat 8-Aug**) · rotate Telegram token · disable rpcbind. | ⏰ dated / standing. |
| **⏰ dated** | **Commit NSE's published `nse_holidays_2027.yaml` before 31-Dec-2026.** | MEASURED: the first 08:15 boot of 2027 **does not start** without it. ✅ It emails you from 15-Dec. ⛔ **Never invent the dates.** |

⛔ **SETTLED — do not re-propose:** SSH→Tailscale-only (phone off the tailnet 23+
days ⇒ closing `:22` voids the emergency runbook) · `require_hmac` → **keep FALSE**
(Chartink cannot sign ⇒ zero signals) · arm `pre-receive` → **not as-is** (guard-2
false-rejects every push) · MIS→CNC fallback → **will not be built** · F2 operator
soft-kill → **dropped**.

### BUCKET 2 — GATED IMPLEMENTATION (each with its gate)

| # | work | gate |
|---|---|---|
| — | **The buy-day product filter** (+ its CRITICAL-on-NULL-fallback) | ⏰ **BEFORE 4-Aug**, and ⛔ **before anything holdings-aware** |
| — | `fix-symdir-27jul` | **Fri 31-Jul evening** → live Mon 3-Aug |
| **G3** | sector **resolution** (81/82 populated rows read `UNKNOWN`) | money path ⇒ own review + prediction + deploy. **Hard prerequisite of any sizing increase** |
| **G2** | B2/M-S4 — 25 of 100 scorer points are a constant `0.0` | needs re-parity + re-soak; coupled to R3/D2 |
| **G8** | the leverage gap (system sizes UNLEVERED) | the **recalibration** is the work, not the multiplier |
| **G1** | liveness probe stops 16:00 vs service 17:35 — **95 min unwatched daily** | its own small design (cron window **and** `_LIVENESS_END` move together). Compensating control = the manual ~17:10 check |
| **G11** K1 | emergency kill + same-day restart ⇒ service does not come back | after 4-Aug. *(Docs already fixed; the BEHAVIOUR half is what is open)* |
| **G13** | first live HARD_KILL · Phase-1 replay · M-C1 non-zero carryover | needs a real mid-day restart with live state |
| **G18** | the 10 capital/kill MEDs (M-C4/M-C5/M-C6/M-C8/W10/P3-c6…c10) | careful-loop, after 4-Aug |
| **G25** | Slice-2.5: M-C2 · M-O7 · M-O6 · H-6 | ⭐ **arm WITH delivery — correctly bundled so whoever flips delivery FINDS them** |
| **G23** | B3 / P1 authoritative flip (`eod_broker_reconcile`) | a clean shadow week + an MTM spot-check |
| **G7** | evidence infrastructure — the two shadows cannot validate a new entry thesis | ⚠️ every service-down day is a lost OOS day |
| **G10** | a **separate test token** (20 live prod keys sit in the test env) | the network guard shipped 30-Jul is only the first layer |
| **G12** | T3 `test_fix181` LIMIT-vs-MARKET · T4 `backfill…w8.py:92` restates the vocabulary · **F2** `CT_SCRATCH_DIR` inside `data_store/` | after 4-Aug; all test-side |
| **G15** | remaining free-text cousins (`REJECTED_KILL_SWITCH` @ `db_reader.py:41`, throttle category, W9) | low |
| **G17** | AB-910 phases 9+10 never produced | formal slice unaudited |
| **G19** · **G20** · **G21** · **G22** · **G24** | 6 TIER-B · ~55 architecture/LOW (⛔ pointer only) · BK-1…BK-8 · DG-1/DG-2 · P5-2/P5-3 | all low / deferred |
| **G9** | 9 built-and-never-run — ⭐ ask *"does anything ACT on what it produces?"* | re-verified 30-Jul: still never-run |

---

## 6. RECONCILIATION — ⛔ THE COUNT

**COUNTING UNIT:** a named, trackable item, as its source names it. ⛔ The 28-Jul
register's own 420 appearances are **NOT recounted** — they were reconciled there and
that file is in the repo. This count covers **what enters THIS file**.

```
  SOURCE                                                        ITEMS
  M from docs/MASTER_PENDING_28-Jul-2026.txt (its live set)      118
  arising 29–30 Jul (new decisions, deploy, findings, fixes)      24
                                                        N  =    142

  M  CARRIED FORWARD live into this file / its pointers      =   131
       112 unchanged from the 28-Jul live set (§2 pointer)
        19 new and still live (§3, §4, §5)
  K  CLOSED with evidence since 28-Jul                       =    10
        Q1·Q2·Q3·Q4·Q5 deployed · R0 arm spent ·
        Q6-NULL-fallback decided · tonight's deploy done ·
        the 33–34F baseline corrected · the 110-line crontab corrected
  J  MERGED as a duplicate appearance                        =     1
        F2 (crash_test) folds into ACTIONS_not_decisions T2 as an UPDATE
                                    M + K + J = 131+10+1  =   142  ✅
```

⚠️ **HOW MUCH TO TRUST THIS:** **M is exact** — every item is enumerated in §2–§5 or
pointed at by name. **J is derived (N−M−K), not independently counted.** The guarantee
is **M plus the not-carried list below**, not the arithmetic.

⛔ **DELIBERATELY NOT CARRIED, each with its reason** — so absence is a decision:
- **All spent operator-card mechanics** (arm steps, boot checks, pre-flight
  checklists for events that HAPPENED) — spent by definition; their *outcomes* are in
  §0 and §3, and the reusable *procedures* were rescued into §4.
- **The 29-Jul arm-window scheduling analysis** (MACHINE vs RAMA minutes, the
  interview split) — **spent**; the arm happened at 11:31.
- **Thursday's close card** — superseded by the Friday card, **proven**: all five
  commands and GTT IDs byte-identical, and all of its §A/§A2 content present.
- **The 28-Jul register's own §9 not-carried list** — still valid, still in the repo,
  not restated here.

---

## 7. ⭐ THE DELETE LIST — RAMA, THIS IS THE SENTENCE YOU ARE WAITING FOR

> **These 12 are safe to delete, because the 30-Jul master (this file) plus the five
> repo registers now hold everything they contained. ⛔ The DEPLOY CALENDAR and the
> FRIDAY CLOSE CARD stay — the calendar is live through 4-Aug and the Friday card is
> the thing you execute at 09:20 tomorrow.**

⛔ **I have deleted nothing.** Verdicts only.

| # | file | verdict | why / where it now lives |
|---|---|---|---|
| 1 | `MASTER_PENDING_REGISTER_FINAL_16-Jul-2026.txt` | ✅ **SAFE** | consolidated into `docs/MASTER_PENDING_28-Jul-2026.txt` (in the repo) |
| 2 | `MASTER_PENDING_REVISED_25-Jul-2026.txt` | ✅ **SAFE** | same |
| 3 | `MASTER_PENDING_REVISED_25-Jul-2026_EVENING.txt` | ✅ **SAFE** | ⭐ was the **sole copy of sections F–L**; **independently re-verified tonight** — all 8 marker items return non-zero in the 28-Jul register (the 27-Jul editions returned 0 for all 8) |
| 4 | `MASTER_PENDING_REVISED_27-Jul-2026.txt` | ✅ **SAFE** | consolidated; it is one of the two that dropped F–L |
| 5 | `MASTER_PENDING_REVISED_27-Jul-2026_EVENING.txt` | ✅ **SAFE** | same |
| 6 | `DO_NOT_DELETE_READ_FIRST_27-Jul-2026.txt` | ✅ **SAFE** | ⭐ **its whole reason for existing is discharged** — the F–L recovery it demanded happened and is verified |
| 7 | `TUESDAY_28-JUL_CARD.txt` | ✅ **SAFE** | consolidated; boot checks SPENT (v45 migrated, service ran 08:15:15→17:35:04) |
| 8 | `MASTER_PENDING_28-Jul-2026.txt` *(Downloads copy)* | ✅ **SAFE** | ⭐ **md5-identical** to the tracked `docs/MASTER_PENDING_28-Jul-2026.txt` — deleting the copy loses nothing |
| 9 | `TUESDAY_EVENING_CHECKS_28-JUL.txt` | ✅ **SAFE — but only because of §4.2** | ⚠️ it held the **error-census baseline** and the **orphaned-reservation query**, found **nowhere else** in repo/registers/memory. **Both are now in §4.2.** ⛔ Had this list been written without that rescue, deleting it would have lost them. |
| 10 | `WED_29-JUL_T2_ARM_COMMANDS.txt` | ✅ **SAFE** | arm executed 29-Jul; GTT ids captured and re-verified broker-side 30-Jul. Reusable parts (ASM/GSM check, GTT lister, Git-Bash rule, step-zero hash) → §4.3 |
| 11 | `RAMA_11-31_STEPS.txt` | ✅ **SAFE** | spent. Its unique **manual-GTT formula** → §4.3 |
| 12 | `VSCODE_WED_29-Jul-2026_FINAL.txt` | ✅ **SAFE** | Rama's arm decisions; executed and recorded in memory `t2_arm_result_29jul` |
| 13 | `VSCODE_ADDENDUM_29-Jul-2026_ARM_WINDOW_REVISED.txt` | ✅ **SAFE** | the window-split scheduling problem; **spent** — the arm ran 11:31 |
| 14 | `THU_30-JUL_T2_CLOSE_COMMANDS.txt` | ✅ **SAFE** | **PROVEN superseded**: 5/5 commands + GTT ids byte-identical to the Friday card, and every §A/§A2 item present there |
| 15 | `DEPLOY_CALENDAR_28-JUL_TO_04-AUG.txt` | ⛔⛔ **KEEP** | **LIVE through Tue 4-Aug.** Day-by-day sequence and every gate. Not superseded by anything. |
| 16 | `FRI_31-JUL_T2_CLOSE_COMMANDS.txt` | ⛔⛔⛔ **KEEP — DO NOT TOUCH** | **RAMA EXECUTES THIS AT 09:20 TOMORROW.** Live money. |

⚠️ **A COUNT NOTE, stated rather than smoothed:** the tasking named *"the 15
highlighted"* but listed **14**. Two same-lineage register files were **not** in that
list — `MASTER_PENDING_REVISED_25-Jul-2026.txt` and
`MASTER_PENDING_REGISTER_FINAL_16-Jul-2026.txt` — so they are covered above as #1 and
#2 rather than left unassessed. That is why this table has 16 rows.

### ⚠️ Out of scope tonight — noted only, NO verdict given (§B4)

Also in Downloads, clearly stale but **outside the trading path** and **not assessed**:
`daily_trade_review_report.txt` · `daily_trade_review_report_master_spec.txt` ·
`Live_test.txt` · `open_dairy_points.txt` · `Output.txt` ·
`audit_05jul2026.md` + `full_system_audit_04july2026.md` — ⚠️ **these last two are the
NAMED SOURCE OF RECORD for `G20`'s ~55 line-level LOW items.** ⛔ Do not delete them on
a tidying impulse; check whether the repo copies are identical first.

---

## 8. HOW TO KEEP THIS FILE ALIVE

1. **This file is a DELTA.** When the 28-Jul register is next re-consolidated, fold §3
   and §4 into it and start a new delta — ⛔ do not let two full registers coexist.
2. **§0 expires at the Fri 08:15 boot.** §1 expires Friday evening.
3. ⭐ **When a new edition drops a section, that is SILENT LOSS with no dangling
   pointer to notice.** Diff SECTION HEADINGS before calling anything a superset —
   and diff the *content*, as §7 rows 3, 9 and 14 did.
4. **Label every item** `<BUILT>` · `<DEPLOYED>` · `<VERIFIED LIVE>` · `<PENDING>` ·
   `<DEFERRED>`. ⛔ "fixed" is retired. **DEPLOYED IS NOT EVIDENCE.**
5. ⛔ **A file is only deletable once you have named where each of its unique items
   now lives.** §4 is that naming; §7 is the verdict that depends on it.

---

**END — 30-Jul-2026 ~20:5x IST (Thursday, market closed).**
Read-only consolidation: schema · service · deploy SHA · reflog · crontab · backups ·
broker GTTs · and every delete verdict verified against the repo or the live VM.
No code, config, DB or service was touched in producing it.
