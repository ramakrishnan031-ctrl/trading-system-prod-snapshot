# MASTER PENDING REGISTER — 30-Jul-2026 (Thursday night, market closed)

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
| **BEFORE TUE 4-AUG** | **the buy-day product filter** (Q7, dated commitment) — ✅ **Q9's BL9 trace is DONE (30-Jul), so the filter is the only pre-4-Aug build left** | ⭐ **The filter must land BEFORE anything holdings-aware — see §3.3.** ⭐ Q9 = reachability **UNCHANGED** ⇒ the filter's urgency rests on the **carry pilot alone**. |
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

## 5. NEEDS RAMA — nothing else can move these

| # | item | state |
|---|---|---|
| — | **The Friday 09:20 close** | ⛔ his action, tomorrow |
| R1 | **STRATEGY REVISION** — ⭐ the only item on the board that bears on **profitability**. Nothing technical blocks it. | OPEN, the real bottleneck |
| R2 | D1 sizing / `max_concentration_pct` | ⛔ HOLD — blocked by G3 (sector resolution) |
| R5/R6 | prune-retention cap · backup-retention cap | OPEN |
| R7/R8 | the watchman · `TELEGRAM_CHANNEL_SECONDARY` (⚠️ coupled to the 8 s send budget) | OPEN |
| R9/R10 | `mis_filter` enforcing flip · **conditional-allocation flip** (the 4th delivery flag) | OPEN — R10 is part of 4-Aug |
| R11 | `predeploy-*` — 4 files, ~764 MB. ⚠️ **renaming to `pre_*` is a DELETE in disguise** | OPEN, re-verified 30-Jul |
| R12 | WAAREERTL 23-Jul external close — possibly a CAPITAL question | OPEN |
| — | **2FA seed → VM-only** (Fri 7 / Sat 8-Aug) · rotate Telegram token · disable rpcbind | OPEN |
| — | ⛔ **REFUSED, do not re-propose:** SSH→Tailscale-only (phone off the tailnet 23+ days) · `require_hmac` → keep FALSE · arm `pre-receive` → not as-is | settled |
| — | ⏰ **Commit NSE's published `nse_holidays_2027.yaml` before 31-Dec-2026** — the first 08:15 boot of 2027 does **not** start without it (MEASURED). ✅ emails from 15-Dec. | dated |

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
