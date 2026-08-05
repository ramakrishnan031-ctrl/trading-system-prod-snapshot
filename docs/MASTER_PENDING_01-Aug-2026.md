# MASTER PENDING REGISTER — 01-Aug-2026 (Saturday, market closed)

> ## ⭐ THIS IS THE WORKING REGISTER. IT **SUPERSEDES** `MASTER_PENDING_28-Jul-2026.txt` AND `MASTER_PENDING_30-Jul-2026.md` AS THE THING YOU READ.
> ⛔ **IT DOES NOT REPLACE THEM AS AUTHORITIES.** Both are RETAINED, in the repo, and
> **cited by name on every item that came from them.** Delete neither. Where this file
> and a source disagree, **the source wins** and this file is the thing that is wrong.

**Why this file exists, in one sentence:** two thread-families are about to run in
parallel and will compete for attention — **(A)** the live-trading thread (Mon 3-Aug
observation → Tue 4-Aug flip → the carry pilot), which has **hard dates**, and **(B)**
the deferred-work threads (the audit's 12-item debt ledger, the ~105 open pre-audit
items, R1) — and without ONE register, starting the fix campaign buries the live gate,
or the live gate buries the fix campaign.

**Clock-read:** 01-Aug-2026, **Saturday**, session start **14:44 IST**. ~~Deployed SHA
`297b587`; `main` **21 ahead**, all docs-only, unpushed by design.~~

> # ▶️▶️ **RESUME — CURRENT AS OF 05-Aug-2026 EVENING. READ THIS BLOCK, THEN THE 05-AUG CONSOLIDATION BELOW.**
> **N = 232** *(was 231; A6 added, A1 discharged to §D — reconciliation in §1)*. ⛔ **DEPLOYED SHA `0197923`, UNMOVED. NOTHING FROM TODAY IS DEPLOYED.**
> ⛔ **THE AHEAD-COUNT IS NOT QUOTED HERE — it has moved ~60 times today and a number in prose ROTS.** Measure it: `git rev-list --count origin/main..main`. Its authority is the memory ledger's top block.
> 🔴 **THE STATE, IN FOUR LINES:**
> **1.** **The flip worked end to end.** Delivery entry AND exit are `VERIFIED LIVE 05-Aug`; **one CNC position is held overnight, a first.** ⛔ `<T+1 CARRY UNVERIFIED>` — nothing wider.
> **2.** **The CHECK1 gate PASSED** and ran in production for the first time. ⇒ **§2b is not reached; no decision is owed on the gate; Thursday's 08:15 boot may run.**
> **3.** 🔴 **A6 (NEW): the service never shut down** — the EOD self-exit is unreachable while delivery is held ⇒ **no census, and (I) no 08:15 boot, so the day's `SOFT_KILL` never auto-clears.** ⛔ **A restart is NOT the safe default** — it hits the HALT trap. **A decision is OWED and it is Rama's.**
> **4.** ⛔ **A stop procedure and a Thursday contingency are WRITTEN AND UNRUN** — `docs/audit/STOP_PROCEDURE_05-Aug-2026.md` (awaits Rama's GO, in his own words) · `docs/audit/THURSDAY_CONTINGENCY_06-Aug-2026.md` (route on `ExecMainStartTimestamp`, ⛔ **never on `is-active` alone**).
> ⛔ **STANDING:** no push (D3) · no code changed today (every commit is `.md`) · **no implementation authorised** — it needs evidence CONFIRMED **and** Rama's approval in his own words.
>
> ## 🔄 CURRENCY BLOCK — **BROUGHT CURRENT 04-Aug-2026 ~23:xx IST · ⭐ RE-MEASURED 05-Aug-2026 00:15 IST (the clock was READ, not recalled — `date` on the PC, offset `+0530`, cross-checked against `date -u`) · ⭐⭐ BROUGHT CURRENT AGAIN 05-Aug-2026 POST-BOOT — see the BOOT PASS below.** ⛔ THE FILENAME STILL SAYS 01-AUG AND MUST NOT BE RENAMED — it is cited by SHA-pinned references across the audit records. **The date in the title is the file's IDENTITY, not its currency; this block is its currency.**
>
> **STATE:** deployed SHA **`0197923`** — ⛔ **UNCHANGED; NOTHING FROM TONIGHT IS DEPLOYED.** ~~**PC == origin == VM bare** · **ahead 0** · `main` **clear**~~ ~~⭐ **SUPERSEDED (§G4), MEASURED 05-Aug 00:15: `main` is AHEAD 5 of `origin/main` (`0197923`); tree clean.**~~ ⭐⭐ **SUPERSEDED AGAIN (§G4), RE-MEASURED 05-Aug POST-BOOT: `main` is AHEAD 6 — `git rev-list --count origin/main..main` = 6, `origin/main` = `0197923ecb4954…`, `HEAD` = `e64146f…`, tree CLEAN.** ⛔ **"AHEAD 5" WAS NEVER WRONG AND IS NOW STALE — it was written INSIDE the commit that made it 6 (`e64146f`, 00:32:43), which is the fixed-point §A5 already names for `4603b75`: a count written in prose cannot include the commit that writes it.** The old counts are struck rather than edited in place because **an ahead-count written in prose ROTS**; the memory ledger's top block is its authority, not this line. *(`origin/main` measured directly; **the VM bare repo was NOT re-measured post-boot from here** — the 07:49–07:54 read-only VM control recorded in the memory ledger measured it at `0197923ecb4954…` == origin, and that is the citation, not an inference.)*
> ⛔ **AND A SECOND PREMISE CORRECTED, because it was about to send a commit through twice: *"last night's register update was left uncommitted"* is FALSE.** `e64146f` **is** that update (`docs/MASTER_PENDING_01-Aug-2026.md`, +55/−15, 05-Aug 00:32:43 +0530) and the working tree is clean. **The register was committed; only the PUSH did not happen** — which is the whole content of the timing correction below, and conflating the two would have manufactured a duplicate commit for work already in history.
> ⛔⛔ **AND THE TIMING PREMISE THAT EVERY ONE OF TONIGHT'S FOUR COMMIT MESSAGES CARRIES IS NOW REFUTED BY THE CLOCK — recorded here because it is the kind of claim that silently ages into a lie.** All four say *"everything committed tonight first executes WED 08:15 — flip morning."* **That was written at 23:35–23:37 on TUE 4-Aug, when a same-night push was still possible. It is now 00:15 on WED 5-Aug and the push did not happen** ⇒ **the VM boots `0197923` at 08:15 and NONE of the four is on it.** ⇒ ⭐ **the flip morning the operator watches runs the SAME code the 04-Aug census ran** — which is, on its own terms, the *conservative* outcome (flip day gains no new variables), but it is **not what the commit bodies claim**, and §A5 now carries the corrected first-execution date per commit.
> ✅✅ **THE FLIP IS `<DEPLOYED>` — ⛔ NOT `<VERIFIED LIVE>`.** Pushed **04-Aug 19:08:09** (`4149263..0197923`, 27 commits). **The flags first LOAD at WED 05-AUG 08:15**, because the service self-exited 17:35 and does not restart. **Delivery is `<VERIFIED LIVE>` only when a real CNC order and its GTT appear at the broker.**
>
> ### ⭐⭐ THE 05-AUG BOOT PASS — **THE FLIP IS NO LONGER "DEPLOYED, NOT VERIFIED". IT IS `<DEPLOYED — CAPABILITY VERIFIED LIVE, OUTCOME UNVERIFIED>`, AND THAT COMPOUND LABEL IS THE HONEST ONE.**
> **(1) ⭐ CAUSE (b) IS CLOSED.** `data_store/session/zerodha_token.json` present **08:15:01.910**, `date: 2026-08-05`; service **`active` 08:15:05**, `NRestarts=0`. ⇒ **"the service never started" is eliminated as an explanation for anything observed today** — the pre-boot observation order (memory ledger, step 1 before step 4) discharged as written. ⛔ **Without this step "the flip failed" and "the service never started" are INDISTINGUISHABLE, and no amount of later evidence separates them.**
> ⭐ **AND THE CHAIN IS PROVEN BY THE ORDERING, NOT BY THE CLOCK:** token at `:01.910` → service at `:05` is a **3.1 s gap inside a 30 s poll** (§3 below). Two events at plausible times prove nothing; **token-strictly-before-service, by less than one poll interval, is the evidence.**
> **(2) BOOT CHECKS.** composition **OK 62/62, UNMOVED** · **zero** migration lines (the evening-schema-push hazard did not apply, as §A2 predicted) · **one** CRITICAL = the known auto-cleared kill pair (`expected_alarms` §1b) · startup checks clean · **book flat**.
> ⚠️ **THE 18:00 DRIFT WARNING IS "NOT DUE", NOT "PASSED" — and the distinction is not pedantry.** At 08:15 the 18:00 job has not run; **absence at boot is not evidence about it.** `71f331b`'s registry half is tested **tonight at 18:00**, not this morning. ⛔ Recording it as "passed" would bank a green that could not have been red (§"a green check is evidence ONLY if it could have been red").
> **(3) CAPITAL AT BOOT.** `broker.net` **₹9,883.70** · `intraday_avail` **₹6,918.59** · `positional_avail` **₹2,965.11** *(70/30, and the positional bucket is now a bucket that can actually be spent)*.
>
> ### ⭐⭐⭐ THE HEADLINE — **THE NO-OP-FLIP RISK IS REFUTED BY MEASUREMENT, NOT BY REASONING**
> `strategy_control.summary` → **`will_trade_count: 15`, INCLUDING ALL THREE POSITIONAL STRATEGIES**; `wont_trade_count: 1` (`pb01`, unrelated). **Yesterday 723/723 STRATEGY_CONTROL rejections were LAYER 0 with LAYER 1 MASKED** (§25 above) ⇒ **the masked layer is now demonstrably satisfied, which is exactly the thing that could not be observed while LAYER 0 returned first.**
> ⭐ **WHY THIS IS A REFUTATION AND NOT MERELY A SUGGESTIVE LOG — re-measured at HEAD:** `main.py:2808-2816` calls **`strategies.control.strategy_will_trade`**, and its own comment says *"same resolver the entry gate + status table use"*. ⛔ **It is not a parallel reimplementation that could agree by accident** — the boot summary and the live entry gate are the same function, so a `will_trade` at boot is the gate's own answer. *(LAYER 0 `strategies/control.py:90`, LAYER 1 `:100` — ⭐ **both line numbers RE-MEASURED AT HEAD `e64146f` and both HOLD**, per §M3.)*
> ⛔⛔ **BUT `will_trade` IS A PERMISSION, NOT AN ORDER — AND THIS IS THE LOAD-BEARING QUALIFICATION.** Between permission and a filled CNC order sit **screening · sizing · the R:R gate · affordability**, none of which has ever run for a positional entry in production. ⇒ ⭐ **THE CAPABILITY IS PROVEN; THE OUTCOME IS NOT.** ⛔ **NOT `<VERIFIED LIVE>`** — that label still requires a real CNC order and its GTT at the broker (§A2).
>
> ### ✅⭐⭐⭐ **AND THEN IT TRADED — (a) IS ANSWERED THE SAME DAY. TWO CNC POSITIONS ARE HELD.**
> **The morning's `will_trade` permission became a FILLED DELIVERY POSITION: screening · sizing · affordability · placement · fill ALL cleared.** ⇒ **§A2 is `<DEPLOYED — ENTRY PATH VERIFIED LIVE 05-Aug; EXIT PATH UNVERIFIED>`** — ⛔ **not a blanket `<VERIFIED LIVE>` for delivery: the GTT trigger, T+1 and the carry are untouched.** ⚠️ **OPERATOR-REPORTED, ⛔ NOT MEASURED FROM HERE** *(the PC's DB copy is mtime **03-Aug 16:08** — it predates the flip push and the boot and cannot hold today's rows)*; **the measurements owed tonight, and the reason the confirmation quantity is TODAY'S TOTAL CNC VALUE rather than symbol names, are in §A2 (a).**
> 🔴🔴 **AND THE CONSEQUENCE THAT CHANGES TONIGHT'S WORK: §C.5's CHECK1 GATE IS NOW MANDATORY, NOT CONTINGENT** — there IS a held CNC position, so the branch that made the gate hypothetical is gone. ⛔ **"The GTT passed at Zerodha" is NOT the claim CHECK1 reads** (re-measured at HEAD — it skips on an **`ACTIVE` `gtt_state` row keyed to the trade's `trade_id`**, `order_reconciler.py:871-872`). ⇒ **§C.5, first row.** ⭐ **And H1 + H5 stop being "live from the first fill" and become LIVE — §B#7.**
>
> ### ⭐⭐ THE FINDING INSIDE THE HEADLINE — **IT GENERALISES, AND IT IS THE MORE VALUABLE HALF**
> `delivery_lock.status` (`main.py:2838-2846`) publishes the system's **OWN COMPOSITE VERDICT** — `cnc_orders_possible: true` — with a `note` that names the full conjunct **verbatim**: *"real CNC/GTT requires `delivery_enabled=true` AND `force_intraday_only=false` AND `trade_type` in {DELIVERY,BOTH}"*. ⇒ ⭐⭐ **THE 04-AUG "FLAG NOBODY LISTED" (`trade_type`, §25) WAS IN THE BOOT LOG THE WHOLE TIME.** The flag list was short; **the system was not.**
> ⭐ **THE LESSON, REGISTERED AS A LESSON AND NOT ONLY AS A FACT: THIS SYSTEM PUBLISHES COMPOSITE ASSERTIONS THAT THE CAMPAIGN HAS BEEN DERIVING BY HAND.** Reading a published verdict is **cheaper AND more authoritative** than re-deriving one — cheaper because it is one grep, more authoritative because it is emitted by the code that enforces it, so it cannot drift from the enforcement the way a hand-derived list can. ⇒ 🔴 **LOOK FOR THE OTHERS.** Known members already: `delivery_lock.status` · `strategy_control.summary` · the composition census · `config_auditor`'s `will_trade_count`. ⛔ **This does NOT retire hand-derivation** — a published verdict is only as good as its predicate, and §M6's rule still applies: **a verdict emitted BELOW an earlier-returning guard measures the guard above it.** *(⚠️ M5 — what this does not cover: no sweep for further composite emitters was run; the four named are the ones encountered, not an enumeration.)*
> ⭐ **WED 5-AUG IS THE FLIP DAY, NOT TUE 4-AUG — and the reason, so a later reader sees a DECISION and not drift: R2 (Option Y), ratified 02-Aug.** 4-Aug was **reassigned, not deleted**: shakedown day + the first real EOD census, then the flip-flag push that evening. **Every dated "4-Aug flip" reference in this file means 5-Aug, by R2.**
> **WHAT THE FLIP ACTUALLY CHANGED (3 value lines + 2 registry states + their prose):** `force_intraday_only: true→false` · **`trade_type: INTRADAY→BOTH`** · `delivery_enabled: false→true` · `cnc_gtt_placer`/`cnc_gtt_monitor` `expected-dormant→expected-event-driven`. ⛔ **NOT** `conditional_allocation_enabled` (R10 — measured a no-op under BOTH) and ⛔ **NOT** `allocator_mode` (its own gate).
> ⭐⭐ **`trade_type` WAS MISSING FROM THE FLAG LIST AND IS LOAD-BEARING — §G5's FOURTH instance (#6 · #5 · D4 · here): the flip's INTENT was ruled and STANDS; its MECHANISM was incomplete.** `control.py:90` (LAYER 0) returns **before** `:100` (LAYER 1) is reached ⇒ measured live 04-Aug, **723/723** STRATEGY_CONTROL rejections were LAYER 0 and **ZERO** were LAYER 1. ⛔ **That zero was not evidence LAYER 1 was satisfied — it was evidence LAYER 0 MASKED it.** Flipping the other two alone would have produced **a flip that looked complete and changed nothing.** ⛔ **R2 is NOT overturned, questioned or weakened.**
> ⭐ **THE REUSABLE LESSON: a zero from a guard sitting BELOW an earlier-returning guard measures the guard ABOVE it, not itself.** Third instance of this shape in the campaign (with §M6 and the drift ladder's 09:15-only feed).
> **THE FLIP TURNS ON EXACTLY 3 STRATEGIES:** `positional_momentum_long` · `positional_sector_rotation` · `positional_swing_long` — the only DELIVERY-intent entries, all enabled, all LONG, **all declaring `order_protocol: LIMIT_TRIPLE`.** ⭐ That last fact is the **structural** half of why #2d rides safely (the flip cannot un-dormant CO through the strategies it enables); the **measured** half is the census reading `co_protocol: acted 0`.
> ✅ **THE FIRST REAL EOD CENSUS RAN AND IS CLEAN** (`BEGIN day=2026-08-04 mode=live entries=70` … `END mismatches=0`): **22 dormant anchors at 0** · known-acting >0 (`kill_switch 1` traced to the real 15:15 SOFT_KILL, `fund_manager 79`) ⇒ counters are **not dead-by-construction** · MISMATCH block **empty** · 8 NEVER-CONSTRUCTED `[expected]`. **Reconciles on both axes:** 52 block entries + 18 inline `infra` = 70; 70 − 8 expected-absent = **62** = the boot's `composition OK 62/62`. **GATE-Q3 checked, not waved: today TRADED, so a money-path zero WOULD have been a finding; none were zero.**
> ⚠️ **M5 — WHAT THIS BLOCK DOES NOT COVER:** it records state and the 04-Aug measurements. It does **not** re-derive §B/§C item counts (**231 stands**), does not re-measure any pre-04-Aug finding, and adds **no new register rows** — every 04-Aug finding below is a sub-entry or scope expansion on an existing row.

**Method:** ⛔ **CONSOLIDATION ONLY. Nothing was re-derived; every item cites the
register/phase it came from.** No code, config, DB, service or live sequence was touched
in producing this. Findings are **cited, not re-measured** — the audit measured them on
01-Aug against `297b587` and that measurement is not repeated here.

---

## 0. THE SOURCES THIS FILE CONSOLIDATES — ⛔ CITE, DO NOT RE-DERIVE

| # | source | what it owns | status |
|---|---|---|---|
| **S1** | `docs/MASTER_PENDING_28-Jul-2026.txt` | R0–R12 · Q1–Q6 · G1–G25 · the 11 refusals · X1–X15 · PARKED · §8 the entries finding. **Its own reconciliation: N=420 = M118 + K91 + J211** | 🔁 **SUPERSEDED as the working register by this file — RETAINED as the cited authority.** ⛔ do not delete |
| **S2** | `docs/MASTER_PENDING_30-Jul-2026.md` | the 29–31 Jul delta · the G/R stale-sweep (N=38) · F1–F6 · the state tags · the delete verdicts. **Its reconciliation: N=142 = M131 + K10 + J1** | 🔁 **SUPERSEDED as the working register by this file — RETAINED as the cited authority.** ⛔ do not delete |
| **S3** | `docs/audit/integrity_audit_2026.md` | ⭐ **THE 16-PHASE INTEGRITY AUDIT** — 4,593 lines, 18 register commits. **95 finding IDs** + §XE.3's **12-item DEBT LEDGER** + the campaign verdict | ✅ **COMPLETE 01-Aug, findings-only.** ⛔ **no fix authorised by it** |
| **S4** | `docs/DEPLOY_CALENDAR_28-JUL_TO_04-AUG.txt` | the day-by-day live sequence and every gate; §7.1 the #16a gate; §7.9 the holiday finding + **§7.9(f) the open decision** | ⛔ **LIVE through Tue 4-Aug — KEEP, and it stays the day-by-day authority** |
| **S5** | `Downloads/MON_03-AUG_OBSERVATION_CARD.txt` | Monday's gate: the query, the read-rules, the ZERO caveat, the hard gate on 4-Aug | ⛔ **LIVE — Rama executes it Monday after the close** |
| **S6** | `docs/audit/full_system_audit_04july2026.md` + `docs/audit/audit_05jul2026.md` | **G20's ~55 line-level LOW items — SOURCE OF RECORD** | ⛔ **READ-ONLY POINTER. Cited only, never re-enumerated, never deleted** |

**Supporting registers unchanged and still owning their content** (⛔ pointers, not
copies): `docs/decisions/00_INDEX.md` (decisions 01–11) · `docs/decisions/
ACTIONS_not_decisions.md` (K1–K3, T1–T4, M1–M2, security backlog) · memory
`UNPUSHED_PENDING_DEPLOY_LEDGER.md`.

### 🏷️ LEGEND — carried VERBATIM from S2 §5. The tag is what separates *done* from *parked*.

| tag | means | how to read it |
|---|---|---|
| ✅ **CLOSED (decision final)** | Rama decided; **nothing lives on**. | ⛔ Not open work. Kept for record only. |
| 🟪 **DECISION DEFERRED** | Rama **parked** it; a decision is **still owed** later. | ⚠️ Still owed — "keep as a placeholder" defers the choice, it does not make it. |
| 📌 **STANDING NOTE / LIVE CAVEAT** | The technical reason it was raised, **still true after the decision**. | ⛔⛔ **A closed decision and a resolved caveat are DIFFERENT THINGS.** |

**Status labels** (S1 §11(7), Rama's rule): `<BUILT>` · `<DEPLOYED>` · `<VERIFIED LIVE>`
· `<PENDING>` · `<DEFERRED>`. ⛔ **"fixed" is retired — it was carrying all five.
DEPLOYED IS NOT EVIDENCE.**

---

# 🔴 05-AUG-2026 EVENING CONSOLIDATION — **N 231 → 232.** ⛔ ONE NEW ROW; FIFTEEN SUB-ENTRIES.

> ⛔ **EVERY ITEM BELOW IS A POINTER TO A COMMITTED RECORD. Nothing here is re-derived, and no
> figure is retyped from conversation.** Primary record: **`docs/audit/HANDOFF_05-Aug-EVENING.md`**
> (§0–§12). Procedures: `STOP_PROCEDURE_05-Aug-2026.md` · `THURSDAY_CONTINGENCY_06-Aug-2026.md`.
> ⛔ **No symbol names appear in this section — totals only.**
> 🏷️ **M9 classes on every claim: (P) production evidence · (S) source, SHA-pinned · (I) inference.**

## 🆕 A6 — **THE DELIVERY CARRY BREAKS THE DAILY PROCESS LIFECYCLE** *(THE ONE NEW ROW)*

**Root cause:** the system assumes **one process per trading day, flat at EOD.** A carried delivery
position falsifies that premise and three guards that silently depended on it fail together.

**(a) THE EOD SELF-EXIT COUPLING — the day's structural finding.**
**(P)** `17:35:00.002 WARNING main: "eod_self_exit: past 17:35 IST but 1 active position(s) remain
— staying up to manage them; will exit once flat"`; service still `active` at 17:49, start
timestamp unchanged from 08:15:05. **(S)** `_eod_self_exit_due` (`main.py:1073-1113`) is due only at
**zero** active positions; **`emit_census()` runs at `_shutdown()` entry (`main.py:1319`)**.
⇒ 🔴 **THE CENSUS IS ABSENT, NOT LATE.**
⭐ **The interpretability control travels with the zero: 0 `effect_census` against 8,204 `effect`
hits in 93,833 lines** — that control is what makes it evidence rather than a worry.
⭐ **(S) UPGRADE — one production call site, inside `_shutdown()`** *(width: `emit_census` repo-wide,
tests included)* ⇒ **no census can EVER be emitted without a shutdown**, not merely "none today".
⭐⭐ **This is the "declared thing with no effect" family arriving on a GUARD rather than a manager**
— and the coupling was invisible until the day it mattered. **Record: HANDOFF §10.1–10.2.**

**(b) THE RESTART TRAP — a LIVE operational hazard, not a historical note.**
**(S)** at the deployed SHA: `clear_stale_state` refuses a **same-day** kill
(`triggered_date >= today`); `auto_clear_scheduled_kill` refuses on **`open_count > 0`**. Both run
at `main.py:1914`/`:1919`, **before** the scenario check at `:1937`, which returns **4 = HALT** with
`--resume` the only escape — **the command the standing instruction forbids that evening.**
⇒ ⛔ **A restart on a delivery evening leaves the service DOWN AND HALTED with its remedy
prohibited.** 🔴 **True again on ANY evening a delivery position is held.** **Record: HANDOFF §12,
`STOP_PROCEDURE` basis table.**

**Also owned by A6:** no boot ⇒ `clear_stale_state` never runs ⇒ the day's kill survives into the
next session **(I)**; and every alarm emitter stays alive overnight (see the drift loop below).
⛔ **A6 is MEASURED AND REGISTERED ONLY. No fix is designed, proposed or authorised.**

## 📌 THE FIFTEEN SUB-ENTRIES — each named to the row that owns it

| # | finding | class | filed as a sub-entry on |
|---|---|---|---|
| **c** | **the delivery ROUND TRIP closed in the ledger** — `GTT_EXIT`, `exits_verified=1`, `RELEASE_USED` in the same second as `exit_time`, bucket restoration exact; total CNC value reconciles four independent ways | **(P)** | **§A2** — 🏷️ `<ROUND TRIP VERIFIED LIVE 05-Aug; T+1 CARRY UNVERIFIED>`. ⛔ nothing wider |
| **d** | **the CHECK1 gate PASSED, and ran in production for the first time** (`gtt_state` was empty before today) | **(P)** | **§C.5** CHECK1 row. ⭐ **Discharges** Thursday's 08:15 boot meeting a T+1 holding. ⛔ **Does NOT discharge the 4th entry path** `_check_stuck_exiting`, still unguarded, reachability still **(d)** |
| **e** | **FIX-133's floor is LOAD-BEARING for delivery** — `floor(1 × 0.5) = 0`; without it: no order, no GTT, no evidence | 🔴 **(I)** | **§C.1 G8** sizing. ⛔ **NEVER OBSERVED — arithmetic, not measurement.** ⚠️ its removal presents as *"no signals qualified today"*, indistinguishable from a quiet day |
| **f** | **sizing is PINNED BY ARITHMETIC, not configuration** — concentration binds strictly at 1 against 8 and 5; tier multiplier inert at `raw_qty=1` for every `m<1`; `raw_qty` must reach **4** for one extra share | **(S)+(I)** | **§C.1 G8** + **A-DEC-3 item 5**. ⭐ **the intraday twin is already registered** — risk sizer dead by algebra, 298/298 and 415/415. ⇒ **two pipelines with two config surfaces produce TWO INERT SURFACES; the binding constraint is arithmetic and SHARED.** ⛔ item 5's **ORDER** changes, not its existence — a knob inert at ₹10k is not inert at ₹100k, and ₹10k is a testing value. ⛔ **no value proposed** |
| **g** | **the H5 coupling, with the RIGHT code** — `SYMBOL_DIRECTION_DAILY_LIMIT` (`signal_processor.py:693` at `0197923`), and it is **product-BLIND** | **(P)+(S)** | **§B.1 row 7** — ✅ **already filed** (`e3edeb4`). ⛔ *"delivery blocks intraday"* is the wrong description and would send the next reader hunting a product branch that does not exist. **The two codes the run sheet named fired ZERO times** |
| **h** | **the cost model BRANCHES on product** — two rate tables; the day's recorded CNC cost reproduces by hand to the paisa; MIS rates would give ~40% of it | **(S)+(P)** | **§C.1**. 🏷️ **the CNC cost path moves to `VERIFIED LIVE 05-Aug`** ⚠️ hand computation, not an execution |
| **h2** | 🔴 **A CORRECTION I OWE: the "falls back to hardcoded rates" claim is IN NO REGISTER EDITION** | **(P)** | see the correction block below |
| **i** | **the day-P&L gap — (d) CANNOT DETERMINE.** Both hypotheses on the record: **H-A** cost-model asymmetry **REFUTED** (§3.1); **H-B** realised-only ledger vs a marked open position — **PC-side half CONFIRMED** (`fund_manager.py:1809-1847`), broker half open | **(S)** | **§C.1**. ⛔ **neither adopted; nothing reconciled** |
| **j** | **AR9 scored the IN-SESSION regime only** — overnight the band is a flat ₹50, ⭐ **and overnight is the only time delivery can be held** ⇒ the acceptance does not cover the regime that matters | **(P)+(S)** | **§C.2 G18** → pointer to `campaign_practices.md` **AR9** scope correction (`b7d3a18`) |
| **k** | **two `reconcile_once` cycles ABORTED mid-flight** on broker 502s (17:22:37, 17:50:58) | **(P)** | **§C.3**. ⛔ *"transient"* understates it — those cycles' safety checks did **not** complete. ⭐ **whether OTHER cycles completed CANNOT BE DETERMINED** — a healthy cycle logs only when it has actions |
| **l** | 🔴 **`acted` COUNTS ROWS EXAMINED, NOT ACTIONS TAKEN** — `cnc_gtt_monitor.py:145-153` appends a label on **every** path incl. `healthy:`/`noop:`, then counts them all | **(S)** | **§B.1 row 1** (effect-verification / acted-telemetry). 🔴 **changes the reading of ALL historical census evidence.** ⭐ **Four cards asserted otherwise; this correction is the record.** Also carried in `STOP_PROCEDURE` §(g) so a future census reader hits it |
| **m** | **`get_gtts` is UNINSTRUMENTED — 0 call sites logged** ⇒ observed-trigger vs swept-row is **(d)**, and ⛔ **the census was never going to settle it** | **(S)** | **§B.1 row 1**. ⭐ what WOULD settle it: instrument `get_gtts`, or log `bg_status` in `_handle_row`. ⛔ **neither proposed** |
| **n** | **item 4 changed shape** — *"Part A"* does not exist; two real inventories do, and they overlap ⇒ **item 4 begins with a RECONCILIATION, not an annotation** | **(P)** | **§B item 4** — ✅ **already filed** (`0d444f4`), marked **`RECOMMENDED — NOT RULED`** |
| **o** | **122 blank-product rows** — all `FAILED`/`REJECTED`, never filled ⇒ **LATENT, not live** | **(P)** | **§C.3**. ⭐ **the discriminator that would make it LIVE: a blank-product row that ever reaches a FILLED state.** Until then it is a data-completeness gap, not a capital risk |
| **p** | **the 18:45 `system_manager` independently predicted Thursday's auto-clear**, in its own words, and scoped the `resume.sh` prohibition correctly | **(P)** | **§C.5**. ⭐ a second, independent source for the Branch-A prediction ⚠️ **its sentence assumes a boot happens** — which is exactly what A6(a) removes |

### 🔴 h2 — THE CORRECTION, STATED PLAINLY BECAUSE IT IS MINE

**`HANDOFF §3.2` records: *"the older pending register's 'falls back to hardcoded rates' claim is
REFUTED."*** ⛔ **THAT ATTRIBUTION IS WRONG. No register edition ever made that claim.**
**Width, stated:** `grep -iE "fallback|hardcod|hard-cod"` across **all three editions**
(`01-Aug` · `30-Jul` · `28-Jul`) → **13 hits, not one about cost rates** — NULL-product FLATTEN,
the manual-GTT formula, MIS→CNC, the `?token=` fallback, BK-8 config drift, paper positions.
⭐ **The underlying FACT stands and is unaffected** — `config_loader.py:1852` says outright that no
hardcoded fallback exists, and a test enforces it (`test_fix131_broker_costs.py`). **What was wrong
was the attribution, not the finding.**
⛔ **So there is nothing in the register to correct — and saying "corrected 1 record" would have
been the same defect in the other direction.** ⭐ **Third instance today of attributing a claim to a
record without reading the record** *(the M8 shape — see `campaign_practices.md` M8 and the M1
sub-taxonomy)*.

### 📎 PRACTICES WRITTEN TODAY — ⛔ POINTERS ONLY, TEXT LIVES IN `docs/campaign_practices.md`
**M7** (a digest without its method) · **M8** (read the record before measuring) · **M9** (evidence
classes P/S/I) · **M10** (the ownership test) · **V5** (a check with no failing input) · **AR9**
(+ its 05-Aug scope correction) · **the M1 sub-taxonomy** (six mechanisms, two groups) · **the first
*positive* G1 instance**. ⛔ **Do not copy their text here.**

---

# §A — LIVE-TRADING THREAD (hard dates, highest priority)

## ⭐⭐ A-DEC — **RAMA'S RULINGS R1–R5 — RATIFIED 02-Aug-2026 ~16:32 IST. AUTHORITATIVE OVER EVERY DATED REFERENCE BELOW.**

> ### 📜 PROVENANCE — recorded because the authority chain matters as much as the content
> **These are RAMA'S OWN decisions, confirmed by him directly on 02-Aug-2026 ~16:32 IST**
> (*"I agreed/approved"*), relayed through the bridge.
> ⛔ **That is precisely what distinguishes them from the THREE auto-filled console prompts
> that impersonated an approval** (`campaign_practices.md` **§G1** — six occurrences, of
> which #5 and #6 asserted rulings verbatim of the kind below) **and from ChatGPT's
> repeated unprompted answers to Rama-only questions** (**§G2**). A ruling is authoritative
> because of **who made it and how it arrived**, not because of what it says.

| # | THE RULING |
|---|---|
| **R1** | ⭐ **THE FILTER-SLIP GATE IS 3-PART.** If the buy-day filter is not live, **postpone the ENTIRE flip** until the filter is available — **not** just the carry pilot. ⇒ **A0 is CLOSED** (see below) |
| **R2** | ⭐⭐ **DEPLOY SLOT = OPTION Y.** **Mon 3-Aug eve:** push **code + docs, WITHOUT the flip flags.** · **Tue 4-Aug:** one additional **clean observation / shakedown day** — first real EOD census that evening, filter **dormant-armed**. · **Tue 4-Aug eve:** push **the flip flags alone.** ⇒ ⭐ **WED 5-AUG IS THE FLIP DAY — no longer Tue 4-Aug** |
| **R3** | **CONFIRMED — #2b and #2c-R ride the same push.** They already sit in the same linear history and **cannot be excluded** (`campaign_practices.md` **§D1** — there is no partial deploy) |
| **R4** | **CONFIRMED YES — the `cnc_gtt_placer` + `cnc_gtt_monitor` registry edits** (expected-dormant → expected-event-driven) ride the **SAME push as the flip flags**, i.e. **Tue 4-Aug evening** under Option Y. ⛔ Otherwise the census **mismatches by design** on the day delivery goes live |
| **R5(a)** | ✅ **DISCHARGED 03-Aug `38f8ab9` `<BUILT — NOT DEPLOYED>`** (implemented as a loud BLOCK — exit 2, both sanctioned paths named — because a silent deletion would have preserved the print-success-change-nothing trap; `--cleanup-*` untouched, allowlist NOT narrowed, R12 still owed). **APPROVED — OPTION A: remove `--reset` from `scripts/check_vm_state.py` entirely.** Basis: no automation caller · no runtime coupling · **no second VM copy** (Rama's own VM check) · `clear_kill_switch.py` already serves the use case correctly in **both** modes |
| **R5(b)** | ⛔ **HOLD — #8b does NOT ride Monday's push.** Implement **only AFTER Monday's critical path has finished** |

## ⭐⭐ A-DEC-2 — **RULINGS D1–D7 — RATIFIED 03-Aug-2026 23:57 IST.** *(record written 04-Aug 00:0x; stamped to the working night per the card — the clock read is stated so the stamp is a convention, not a claim)*

> ### 📜 PROVENANCE — ⚠️ **THIS ROUND ARRIVES UNDER AN AMENDED §G2, AND THAT IS ITSELF A RULING**
> **Rama's standing ruling, 03-Aug-2026: *"ChatGPT's replies count as my replies, no
> deviations."*** ⇒ these seven are **decisions, not advice**, and `campaign_practices.md`
> **§G2 has been amended** to say so (old text struck-through-but-legible per §G4).
> ⛔ **THE BOUNDARY, RECORDED WITH THE DELEGATION BECAUSE THE CHAIN NOW MATTERS:** text can
> travel **console auto-fill → pasted to ChatGPT → returned as a ruling → carrying Rama's
> authority — without Rama having read it.** **§G1 STANDS UNCHANGED: an auto-filled prompt is
> NEVER an instruction and NEVER an approval.** The delegation covers **considered replies**;
> it does **not launder text that originated in a suggestion box.** Authority attaches to the
> reasoning, not to the round trip.

| # | THE RULING |
|---|---|
| **D1** | **#2d = OPTION A.** Sites **A** and **C** adopt the proven `cancel_order(variety="co")` path; ⛔ **Site B REFUSES AND ESCALATES** — matching #2c-R's shape. ⭐ **This is the answer Step 1 pointed at:** Site B iterates BROKER positions that by design have **no local trade row** (`kill_switch.py:1543-1546`), so it cannot reach a parent order id — *"mirror `eod_squareoff` at all three sites"* was **not achievable as stated.** ⭐ **Refusing where you *can* act is only right when you can't.** ⛔ Still gated on its own careful loop; LATENT (CO doubly dormant) ⇒ a hard gate on **enabling CO**, not on the flip |
| **D2** | **#3a = OPTION A — persist BROKER TRUTH ONLY.** If the broker reports nothing, the column **stays empty and says so**. ⛔ **NEVER write the inferred final** (`final_qty`/`final_price`, which substitute the PLANNED qty and the EXPECTED price): **a plausible fabrication in an audit column is indistinguishable from a real fill afterwards** — ⭐ **the identical property that made the backfill wrong (R-4)**, now applied at the writer instead of at the backfill. ⇒ the §1.5(i) decision is CLOSED; §1.5(ii) the untrack/idempotency review and §1.5(iii) the missing COMPLETE-path test remain part of the build |
| **D3** | **`STRATEGY PAUSED` = OPTION A — the BEHAVIOUR is wrong.** Persist the pause across restart, and **split *"create a pause"* from *"honour an existing pause"*** (the one-guard-doing-two-jobs root, record §2.7.3). ⭐ **A conditional reword may ship as a TEMPORARY clarification — ⛔ NEVER as the solution.** ⛔ And ⛔ **not the flat reword**: *"clears on restart"* is FALSE on the pre-cutoff branch (§2.7.2), so even the interim must carry the conditional |
| **D4** | **R12 — scope the allowlist to READ-ONLY DIAGNOSTICS.** ⛔ **No pre-authorised state-changing operations against the live DB.** ⚠️ **MEASUREMENT REQUIRED FIRST — see below; do NOT narrow on assumption.** ⭐⭐ **04-Aug: THE MEASUREMENT RAN AND THE MECHANISM DID NOT SURVIVE IT — ⛔ but THE INTENT IS UNTOUCHED AND CORRECT (§G5).** *"Scope the allowlist"* narrows **1 of 3** independent surfaces; `Bash(ssh *)` alone re-permits `--cleanup-pending` ⇒ narrowing as ruled yields a **"read-only scope" that still permits everything.** 🔴 **RETURNED FOR A RULING ON THE MECHANISM ONLY.** **Recommendation — and it INVERTS D4's implied order: (1) `permissions.deny` FIRST**, expressed to hold **regardless of channel** (`--cleanup-*` · `--reset` · `sed -i` on the production `.env` · webhook POST · live-service restart) — ⭐ **an ALLOWLIST STRUCTURALLY CANNOT express *"never `--cleanup-*`"* while any broad channel exists, and every future wildcard silently re-opens it; a DENY rule can.** Precedent in-system: `~/tools/claude/.claude/settings.json`, **14 deny rules.** **(2) THEN** narrow the allow entries (the 21 PowerShell + the Bash channel). **(3) stale cleanup LAST, or not at all** — ⛔ cosmetic while (1) is undone. 📄 `docs/audit/r12_allowlist_enumeration_04aug2026.md` |
| **D5** | **R13 — RETIRE `mempalace.yaml`.** ⇒ stop naming it in card memory-update directives once the retirement lands. ⚠️ **Scope measured — see below; it is not one file.** ✅ **EXECUTED 04-Aug ~02:0x `<BUILT>`** — but ⛔ **only after a re-measurement halted it once: the file held `F1..F9` with NO tracked copy, so retiring it as scoped would have been a DELETION.** ⭐ Ruled that **moving unique content out is not a G3 "fix" but the PRECONDITION of the authorised retirement** ⇒ migrated verbatim (md5-proven) to `ops_dashboard/docs/F_BACKLOG.md`, **then** retired: 2 pointers repointed (incl. a 5th site the name-keyed sweep could not see), `.gitignore` **ignore KEPT + annotated**, ⛔ `SYSTEM_MAP.md` untouched (dated history). 🔴 **Tail open, non-blocking: the `.exe` stays installed; external card templates unswept.** ⭐ Earned **§M5** |
| **D6** | **R14 — PIN AND CAP pytest/tooling versions** so gate results are reproducible. ⭐ Earned twice: `requirements-dev.txt` says `pytest>=9.0.3` with **no ceiling**, so a force-reinstall silently moved the gate to 9.1.1 mid-campaign. ✅ **LANDED 04-Aug ~01:2x `<BUILT>`** — `pytest==9.0.3` + `pytest-cov==7.1.0`, exact pins matching the versions verified installed, so the pin cannot itself move the gate; the interpreter is now named in **§V2**. ✅ Gate scope collects **5,546** = the recorded **5,539 + exactly the 7 tests `4149263` added** ⇒ the repaired venv and the pin are both sound. ⚠️ `cryptography` also drifted 46.0.7→50.0.0 in the same repair, but it lives in **`requirements.txt` (PRODUCTION)** — ⛔ **deliberately NOT touched here** (§G3); it is a separate call with a VM blast radius. ⚠️ **§V2 still lacks the `venv/` half of the base-worktree recipe** — known, recorded, **not folded in silently** |
| **D7** | **IA-XDOCS-03 (map inversion) — PARK, with an EXPLICIT TRIGGER.** ⛔ Parked ≠ closed; it stays counted. The trigger must be written down, or "parked" decays into "forgotten" |

### ⚠️ D4 · R12 — **MEASUREMENT OWED BEFORE ANY NARROWING** *(not done here)*
Enumerate the **actual reachable command paths** in `.claude/settings.local.json`: which
entries are live, which name paths that **no longer exist**, and what each one actually
permits. ⭐ **Narrowing on assumption is exactly how a "read-only scope" quietly keeps a
destructive hole** — and this surface already has 21 `check_vm_state` entries of which
~~**eleven point at `~/check_vm_state.py`, a path Rama's own VM check proved absent.**~~
⛔ **CORRECTED 04-Aug-2026 per §G4 — struck, not replaced, because the original figure is
what the re-measurement is evidence against.** The eleven are real, **but the count was wrong
and wrong LOW: 66 entries name a VM identity that is not live** (`80.225.198.195` 51 ·
`161.118.188.171` 12 — ⛔ **also stale despite looking current**, the tracked record runs
`161.118.187.249` **19-to-1** and the ssh alias agrees · `opc@` 3), and the stale **path**
generation `~/trading-system/` is **69 — six times the flagged eleven.**
⭐ **This mattered more than the arithmetic:** a measurement scoped to *"how many name the
absent script"* answers a narrower question than *"how many are stale"* — **§M5 again, on the
register's own figure.**
⛔ The read-only diagnostics MUST survive; the two `--cleanup-*` flags MUST NOT be
pre-authorised. **Removing `--reset` from the script did not narrow the allowlist** — they
are independent surfaces, which is why R12 is still owed after `38f8ab9`.

##### ✅ R12 — **MEASURED 04-Aug ~02:3x. ⛔ NOTHING NARROWED.** 📄 `docs/audit/r12_allowlist_enumeration_04aug2026.md`
⭐⭐ **D4's REMEDY AS WRITTEN NARROWS ONE OF TWO INDEPENDENT CHANNELS, AND WOULD HAVE LEFT
EVERY DESTRUCTIVE OPERATION REACHABLE.** All **21** `check_vm_state` entries are on the
**PowerShell** tool, and there is **no `PowerShell(ssh *)`** — so removal genuinely bites there
(**21/21 unsubsumed**). ⛔ **But `Bash(ssh *)` and `Bash(ssh trading-vm *)` exist**, and
`Bash(ssh *)` alone pre-authorises
`ssh trading-vm "python3 scripts/check_vm_state.py --cleanup-pending"` — **the exact command D4
exists to stop.** ⇒ there are **THREE** independent surfaces, not two: the **script**
(narrowed by `38f8ab9`) · the **PowerShell** channel · the **Bash** channel, **which D4 does
not name.**
⛔⛔ **AND THE DESTRUCTIVE SURFACE IS FAR WIDER THAN `check_vm_state`'s three flags.** Measured
across 533 entries: **26** restart the live service · ⭐ **15 POST a hand-written payload to
the LIVE webhook** — *a fabricated signal injected into the running system, on the SIGNAL PATH,
never registered anywhere* · **6** push/deploy (incl. `deploy_audit_fixes.ps1 -Force`) · **5**
mutate the production venv · **3** `git pull` the deployed code · **2** `sed -i` the production
`.env` — ⚠️ **one of which comments out `WEBHOOK_SECRET`, disabling an auth control.**
⚠️ **THE STALE COUNT WAS WRONG, AND LOW: 66 entries name a VM address that is not live, not
11.** Four identities coexist — `trading-vm` alias **149** (✅ live, `~/.ssh/config` →
`161.118.187.249`) · `ubuntu@80.225.198.195` **51** · `ubuntu@161.118.188.171` **12**
(⛔ **also stale** — the tracked record carries `161.118.187.249` ×19 vs this ×1) ·
`opc@…` **3**. Path generations: `~/trading-system/` **69** (⛔ stale, **6× the flagged
eleven**) · `~/check_vm_state.py` **11** · `~/systems/trading-system/` **7** (current).
⛔ **Staleness is not safety** — inert only until a host or path returns, and the wildcard
channel does not depend on any path being correct.
⚠️ **SEVERITY BOUNDED, per the scope correction:** VM assistant work is **agy (Google/Gemini)**;
**Claude has no production role there** ⇒ this governs an **interactive dev-time agent in a
session Rama is driving**, ⛔ not autonomous production access. ⭐ It does not dissolve the
finding — pre-authorisation's whole effect is **removing the prompt**.
🔴 **OPEN, none decided:** the **Bash channel** · whether the 15 webhook-POST entries are
acceptable · whether deleting the 66 stale entries is anything but cosmetic while the channel
stands · ⭐ **whether `permissions.deny` should carry the guarantee instead** — an allowlist
cannot express *"never `--cleanup-*`"* while a broad channel exists, and the precedent is
already in this system (`~/tools/claude/.claude/settings.json`, **14 deny rules**).

### ✅ D5 · R13 — **RETIREMENT SCOPE MEASURED 04-Aug 00:0x (M1 sweep, repo-wide)**
**28 files · 58 mentions** — ⭐ **and the actionable set is far smaller than the sweep**, which
is the finding:
- ⛔ **~24 are HISTORICAL/ARCHIVE** (`docs/web_claude/**` ×15, `docs/archive/**`,
  `docs/audit/**` dated records, `reports/crash_test/**`). **These must NOT be edited** —
  they are the record of what was true then; rewriting them is the history-rewrite §G4
  exists to prevent. **Retirement means they become correct-as-history, not wrong.**
- ✅ **LIVE sites needing the edit: `docs/SYSTEM_MAP.md` · `docs/MASTER_PENDING_01-Aug-2026.md`
  · `ops_dashboard/docs/SOAK_EVIDENCE_TEMPLATE.md` · `.gitignore`.**
- ⚠️ **NEW, and it changes the shape: `venv/Scripts/mempalace.exe` exists — `mempalace` is an
  INSTALLED CLI PACKAGE, not merely a stale yaml.** Retiring the *file* leaves the *tool*
  installed. Whether the package is also uninstalled is a separate call.
- ⛔ The **card templates** that name it are **EXTERNAL** (cards stay outside the repo, by
  standing rule) ⇒ that half cannot be swept from here and must be done at the card source.

#### ⛔⛔ D5 · R13 — **RE-MEASURED 04-Aug ~01:2x AT EXECUTION TIME. THE EDIT IS HALTED; THE SCOPE ABOVE DOES NOT SURVIVE IT.**
The scope block above is **kept verbatim per §G4** — it is superseded on three points, not
wrong-and-erased. Executing it as written would have done real damage.

1. ⛔⛔ **RETIREMENT WOULD DISCARD UNIQUE LIVE CONTENT — THE BLOCKER.**
   `mempalace.yaml:215-224` holds `future_backlog_LOCKED_ROADMAP_ONLY:` — **F1…F9, nine
   concrete GUI roadmap items** *(replay timeline · "explain why" reason chain · config
   snapshot history · strategy lifecycle · reports download · journal viewer · CSV exports ·
   multi-day trend charts · unrealized/LTP)*. **No tracked file holds them** — repo-wide, the
   only tracked hits are two *pointers* and a numbering directive.
   ⚠️ **This does NOT contradict §C.4 R13's *"retiring it discards nothing unique"* — it
   BOUNDS it.** That refutation was measured about **the GUI workstream** (137 tracked files
   under `ops_dashboard/` + `G0_BACKEND_INVESTIGATION_REPORT.md` + `SYSTEM_MAP.md:282`) and
   **holds for that.** It was **never measured about F1–F9**, and for F1–F9 it is **false.**
   ⭐ **The claim was right about what it checked and was then read wider than it was measured**
   — §M1 applied to our own register.
   🔴 **DECISION OWED: where F1–F9 lives after retirement.** ⛔ Until then the retirement is a
   **deletion**, and ⛔ rewriting the two pointers first would aim live files at nothing. **Not
   carried into the repo here: that is a new file, i.e. expansion, and §G3 forbids it in passing.**

2. ⛔ **`docs/SYSTEM_MAP.md` IS MISCLASSIFIED ABOVE AS A LIVE SITE.** Both its mentions are
   **dated changelog entries** — `:282` (*"G2c — VM DEPLOY (03-Jul ~17:25 IST …)"*) and `:1200`
   (*"2026-07-06 — Claude Code (Opus 4.8) — Wave-3 H-9 FIXED …"*). They are **identical in kind
   to the `docs/audit/**` dated records the bullet above excludes as history.** The *file* is
   live; **those lines are not.** ⇒ **editing them is exactly the history-rewrite §G4 exists to
   prevent.** ⭐ The scope was measured at **file** granularity while the criterion is a
   **line** property — the file-level sweep is the narrow one here.

3. ⚠️ **A FIFTH LIVE SITE THE SWEEP COULD NOT SEE:** `ops_dashboard/docs/G5e_DEPLOYMENT_PLAN.md:247`
   directs work to the **F-backlog (F10+ numbering)** — the same retiring destination — but
   **never says "mempalace"**, so a `mempalace`-keyed grep cannot find it. ⭐ **§M1, sharpened:
   the sweep was keyed on the NAME; the blast radius belongs to the DESTINATION.** State the
   search width — *"58 mentions of the word"* is not *"every file that depends on the thing."*

4. ⛔ **`.gitignore:82` MUST NOT SIMPLY BE DELETED.** `mempalace.yaml` is **still on disk**
   (22,585 B, 17-Jul). Dropping the ignore turns a PC-local artifact into an untracked file in
   every `git status` — noise, **and a live risk of committing it by accident.** **Retiring an
   artifact is not the same act as un-ignoring it**; the correct edit is an annotation.

✅ **NET: live sites needing an edit are `MASTER_PENDING` · `SOAK_EVIDENCE_TEMPLATE` ·
`G5e_DEPLOYMENT_PLAN` (+ `.gitignore` as an annotation only) — ⛔ NOT `SYSTEM_MAP.md`.**
⛔ **NOT a new register item — this is D5's own execution record; 231 stands.**

#### ✅✅ D5 · R13 — **UNBLOCKED AND EXECUTED, 04-Aug ~02:0x. `<BUILT>`**
🔴 **The blocker was ruled (§G2, 04-Aug):** ⭐ *"**G3 forbids FIXING things found out of scope.
Moving unique content out of a file you were authorised to retire is not a fix — it is the
PRECONDITION that makes the authorised retirement safe.**"* Read the other way D5 was
**unexecutable by construction**: retire and lose F1–F9, or don't retire and ignore the ruling.
⇒ **the migration is D5's step 1, not a separate item.**

1. ✅ **F1–F9 MIGRATED — `ops_dashboard/docs/F_BACKLOG.md` (new).** ⭐ **Proven verbatim, not
   asserted: the 9 extracted strings are `diff`-clean and md5-identical against
   `mempalace.yaml:216-224` (`53d195f7…`, 9/9).** *A migration that paraphrases is a rewrite.*
   Provenance, the source key and the date are recorded **in the file itself**, so the next
   reader does not need this register to know where it came from.
   ⛔ **Anti-duplication checked first and the rejected candidate is NAMED** (§G3 sibling): no
   roadmap/backlog home exists under `ops_dashboard/docs/` or `docs/gui_project/`; the sole
   candidate — `G5_REDESIGN_PHASE_B.md` **§SECTION K "PHASE-C IMPLEMENTATION BACKLOG"** — was
   **rejected deliberately**: an ordered list of buildable units for Phase C is a **different
   lifecycle** from a locked post-soak roadmap, and its `1./2./3.` numbering cannot host the
   **F10+** scheme both soak documents reference. ⛔ Not `MASTER_PENDING` either — roadmap
   content is not register content, and 231 is not a home for nine backlog lines.
2. ✅ **BOTH POINTERS REPOINTED** at the new home: `SOAK_EVIDENCE_TEMPLATE.md:72` (which also
   **records what it used to say**, so the change is legible per §G4) and
   `G5e_DEPLOYMENT_PLAN.md:247` — **the fifth site the name-keyed sweep could not see.**
3. ✅ **`.gitignore` — THE IGNORE STAYS, and now says why.** `mempalace.yaml` is still on disk
   (22,585 B) and PC-local; dropping the line would surface it as untracked in every
   `git status` and **risk committing it by accident.** ⛔ **Retiring an artifact is not the same
   act as making it committable.** Verified after the edit with `git check-ignore -v` — still
   ignored (`.gitignore:88`), still untracked.
4. ⛔ **`SYSTEM_MAP.md` DELIBERATELY UNTOUCHED** — both mentions are dated changelog entries.
   Editing them is the §G4 rewrite. **The file is live; those lines are not.**
5. 🔴 **D5's TAIL, OPEN AND NOT BLOCKING:** `venv/Scripts/mempalace.exe` stays **installed**.
   Uninstalling a CLI is a different risk class and nothing depends on its timing.
6. ✅ **THE EXTERNAL CARD-TEMPLATE HALF IS CLOSED — AT SOURCE, WHICH IS THE ONLY PLACE IT COULD
   BE.** Cards live outside the repo by standing rule, so no repo sweep could ever have reached
   them. **Closed by the card author (§G2, 04-Aug-2026): *"From this card onward it is gone from
   my headers; I will not write `mempalace` into a memory-update directive again."***
   ⛔ **HISTORICAL cards keep the reference and MUST NOT be chased** — they are
   **correct-as-history**, exactly like the ~24 archive mentions in the scope block above.
   ⚠️ Recorded as **closed-at-source rather than left open against the repo**: an item no repo
   action can discharge, logged as outstanding here, decays into a permanent false TODO.

⭐ **THE RULE THIS EARNED: `campaign_practices.md` §M5 — *a measurement's conclusion may not be
wider than its subject*,** with all three of D5's joins as its worked instances.

### ✅ THE CLAUDE-AGENT CONFIGURATION — **MEASURED 04-Aug 00:0x. ⭐ THE FLAGGED CLAIM SURVIVED.**
The card flagged its own *"4 heartbeat cron lines governed by `~/tools/claude/AGENTS.md`"* as
**seeded from conversation, not measured (M3)**. **Measured on the VM, it is CORRECT** — and it
is worth recording that a self-flagged suspect claim *held*, because the discipline is only
credible if it also confirms:
- ✅ **Exactly 4 cron lines** — 05:30 · 10:31 · 15:32 · 20:33 daily — all running the *same*
  trivial prompt (*"Generate a random 8-character string"*) into `~/tools/claude/cron.log`.
- ✅ **`~/tools/claude/AGENTS.md` EXISTS** (1,270 B, 23-Jun) — a real charter: *"You are NOT an
  operator of the trading system"*, with hard prohibitions mirroring AGY governance.
- ✅ **Its enforcement claim is TRUE.** ⚠️ **I nearly filed the opposite as a finding:**
  `~/.claude/settings.json` is 42 bytes (`theme`+`model` only, no deny list) — but the
  charter means the **project-level** file, and `~/tools/claude/.claude/settings.json` carries
  a real **14-rule `permissions.deny`** (no `.py` writes/edits · no `.db` read/write ·
  no `sqlite3`/`systemctl`/`sudo`/`service` · no `git push` · no writes to `~/systems/**` ·
  no `.env` reads). ⭐ **Second too-narrow check of the night** (the first was the post-push
  grep). Same lesson, same session: **state the search width, then widen before reporting.**
- ✅ **Governed, not stray** — the 4 jobs are registered in `config/cron_registry.yaml`.
- ✅ **Claude has NO production role on the VM.** The trading-system assistant work is **agy
  (Google/Gemini)**: 5 cron jobs — `gemini_premarket_brief` · `gemini_trade_coach` ·
  `gemini_log_review` · `gemini_weekly_patterns` · `gemini_data_integrity_check`.
- ⚠️⚠️ **DISCLOSED, NOT FIXED (§G3), and outside the trading system entirely: THE HEARTBEAT IS
  DEAD.** `cron.log` = 185 lines: **112 successful 8-char outputs, then 46
  `Failed to authenticate. API Error: 401 OAuth access token has expired.`** At 4 runs/day that
  is **≈11–12 days of continuous failure**; ⛔ the log carries **no timestamps**, so the exact
  onset is **not readable from it** — stated rather than estimated as fact. **Severity LOW**
  (sandbox charter, no production role, its own deny list) — but the point stands: **a
  heartbeat that fails is a heartbeat that proves nothing**, and nothing alerted on it for
  ~11 days because it sits **outside the trading system's alert path altogether.**

⭐ **THE CALENDAR, RESTATED ONCE SO NOTHING BELOW HAS TO BE INFERRED (authority: R2):**
| day | what happens |
|---|---|
| **MON 3-AUG** | observation day on `297b587` · PC gates (#1 composition boot · #2 kill drill · calm regression confirm) · observation query at close · **18:15+ push CODE + DOCS ONLY**, book flat, **Rama flattens MANUALLY** |
| **TUE 4-AUG** | ⭐ **SHAKEDOWN DAY — a real role, not an empty gap:** one additional clean observation day, **the first real EOD census** that evening, the buy-day filter **dormant-armed**. Then **push the flip flags + the R4 registry edits** |
| **WED 5-AUG** | ⭐ **THE FLIP** |

⛔ **Every "Tue 4-Aug flip" / "the 4-Aug flip" phrasing anywhere below or in any other
document is SUPERSEDED BY R2 and now means WED 5-AUG.** The **4-Aug date itself is NOT
deleted** — it is reassigned to the **shakedown day + the flip-flag push**, which is why
this is a *resequencing*, not drift. ⭐ **Where a date moved, R2 is the reason.**

⛔ **These are DECISIONS, not accepted risks — they are deliberately NOT filed in
`campaign_practices.md` §R.** If any of them later has a risk accepted as part of it, that
becomes its own AR entry with a reopen condition and a SHA-pinned source.

---

## ⭐⭐ A-DEC-3 — **THE SETTLED SHAPE FOR PIPELINE-SCOPED RISK CONTROL — recorded 04-Aug-2026.** ⛔ SHAPE ONLY. **NOTHING HERE IS AUTHORISED, DESIGNED OR BUILT**, and no register row is created — **231 stands.**

> ⚠️ **M5 — the subject, beside the conclusion:** this block records **how a control must be DESCRIBED and ENFORCED**, derived from the 04-Aug two-pipeline and capital measurements (§B#7, §C.1 G8/G2/G3). It does **not** set a single delivery value, and it does **not** re-measure anything above it.
> ✅ **R1–R5 (A-DEC) and D1–D7 (A-DEC-2) are current as recorded and are NOT amended here.** R2's Wed-5-Aug resequencing is now reflected in the currency block and A2; R4's registry edits **shipped with the flip**; R10 remains open and is now measured as **behaviourally a no-op under BOTH** (§A2).
>
> ### 1. ⭐ THE ANNOTATION — every control carries FIVE axes, and STATUS is a SIXTH kept SEPARATE
> **SCOPE** (GLOBAL safety rule · pipeline-local · strategy-local) · **BASIS** (what it measures: notional · margin · realised cash · count) · **DENOMINATOR** (total capital · bucket · position) · **MODE** (observe · enforce) · **WHY** (the incident or reasoning it exists for).
> ⛔⛔ **STATUS (active · placeholder · reserved) IS A SEPARATE AXIS AND MUST STAY SEPARATE FROM MODE. `ENFORCE + PLACEHOLDER` IS THE DANGEROUS CELL — a control that is enforcing a value nobody chose — and it only becomes VISIBLE if the two axes are kept apart.** Collapse them and that cell is unnameable.
> ⭐ **Earned, not invented:** `max_sector_exposure_pct` reads *"40% of capital"* exactly like `max_position_value_pct` and measures a **different quantity** (§C.1 G3) — a BASIS/DENOMINATOR annotation would have made that visible on the line.
>
> ### 2. ⭐ THE CODE RULE — one sentence, and it is checkable
> **Any query or governor that touches `trades` must either CONSULT the pipeline dimension or DECLARE ITSELF GLOBAL.** A control with no declared scope is how the next feature inherits the wrong behaviour.
> ⛔ **The routing ALREADY EXISTS — `position_sizer.py:291` (re-measured at HEAD 04-Aug): `bucket = "intraday" if intent in _INTRADAY_INTENTS else "positional"`. DO NOT ADD A SECOND SOURCE OF TRUTH.** Every coupling in §B#7 is a query that simply takes no such argument.
>
> ### 3. ⭐ THE TAXONOMY HAS NO HOME FOR **MODIFIERS**, AND THERE ARE AT LEAST TWO
> The five axes describe **caps**. The **tier multiplier** is applied **after** the caps (`raw_qty × tier_multiplier`) and has **no entry anywhere in any inventory** — yet it is a permanent ×0.5 on 438/438 trades. ⚠️ **`dynamic_by_winrate` (min 0.5 / max 2.0, `system_config.yaml:182-184`) is a SECOND modifier, equally uninventoried** — and unlike the tier it can move size in **both** directions.
> ⇒ **A modifier is not a cap and must not be filed as one; it needs its own row-shape.**
>
> ### 4. ⭐ ENFORCEMENT = A PIPELINE-ISOLATION SUITE, AND IT MUST BE BORN FAILING
> **Seed it with H1 and H5 as its FIRST TWO TESTS, written NOW, RED.** ⛔ **Written after the fixes they would pass on day one and prove nothing** — the vacuous-test failure this campaign has hit repeatedly (IA-XTEST-01, practices §V4).
> ⭐ **Plus a test that FAILS if any modifier applies to 100% of trades without an explicit acknowledgement flag** — that single test would have caught the tier ladder, and would catch the next permanent-haircut-wearing-a-ladder-label.
>
> ### 5. ⚠️ A CAPITAL-AFFECTING CHANGE NEEDS A WIND-DOWN PLAN
> **A config revert restores the VALUE, not the POSITIONS.** Any change that can open positions must state, before it ships, how existing positions are wound down if it is reverted. *(Delivery makes this sharper than intraday ever did: EOD6 does not close CNC.)*
>
> ### 6. ⭐ ADD A **GOVERNANCE** BAND — and the prioritisation rule that goes with it
> Governance improvements (the annotation, the code rule, the isolation suite, the wind-down requirement) have been mixing into **§C alongside implementation defects**. ⛔ **Separate queues, so priority is never accidentally swapped** — a governance item and a live money-path defect are not comparable and should never sit in one ranking.
> ⛔⛔ **DEFECTS ARE PRIORITISED BY EXPOSURE AND REACHABILITY, NOT BY EFFORT.** *(The campaign has twice had a "cheap to fix" label do the ranking — §B#6 and §B#3 both carry a struck "~2 lines".)*
>
> ### 7. ⏰ THE ORDER — and it is an ORDER, not a list
> **1.** ⭐ **WEDNESDAY'S FLIP OBSERVATION — it outranks everything in this block.** It cannot be re-run; all of this can.
> **2.** **H1 + H5** — the only two couplings that are LIVE from the first delivery fill.
> **3.** **Persist the broker margin** (both timestamps: at reserve, and a mid-session sample) — the cheapest change that makes any future divergence detectable at all (§B#5).
> **4.** **Annotate the Part A inventory** with the five axes + STATUS. ⛔⛔ **EXTEND THE EXISTING INVENTORY — DO NOT CREATE A SECOND CONTROL INVENTORY.** Two inventories is the multi-authority defect (§B#7) committed deliberately, by the very work meant to cure it.
> **5.** **Draft the delivery configuration surface as PLACEHOLDERS** — ⭐ **Rama's instruction: create the SURFACE, do not tune the VALUES; tune from production evidence later.** Delivery settings are built **from the intraday template**, not invented — which is why the inventory (4) is a hard prerequisite, and why any delivery value set before it would be exactly the "blind settings" being refused.
> **6.** **The concentration / tier replay** — what would the book have done at a different `max_concentration_pct` and an unpinned tier? (§C.1 G8's ~7% utilisation is the question this answers.)
> **7.** **THEN the deployment decision.** ⛔ Not before 6.

---

### 🏷️ SUB-ENTRY ON ITEM **4** — THE 05-Aug DESIGN THREAD, FILED SO IT STOPS BEING RE-DERIVED
### ⛔⛔ **`RECOMMENDED — NOT RULED`. RAMA HAS NOT CHOSEN THE AUTHORITY.** ⛔ No new register row; **231 stands.**

> ⭐ **Why this block exists:** *"we've revisited several of these ideas multiple times."* **An idea
> that has to be re-derived is an idea the project does not actually own.** A four-round design
> thread produced these conclusions and they existed **only in chat**, which is not a record.
> ⚠️ **AND ITEM 4's OWN PREMISE IS ALREADY KNOWN FALSE:** step 1 measured that **"Part A" does not
> exist** and that **TWO control inventories do** (record:
> `docs/audit/item4_partA_inventory_survey_05aug2026.md`). ⇒ **item 4 BEGINS WITH A RECONCILIATION,
> not an annotation** — item 4's wording at `:368` above is superseded by that measurement (§G4:
> struck-through in effect, kept legible because the instruction is the historical record).

**RECOMMENDED AUTHORITY — inventory #2 (`G2a_capacity_inventory.md`), as SHAPE AND KEY,
⛔ NOT NECESSARILY LOCATION.** Four criteria, each a reason and not a preference:
1. it has a **live consumer** (`capacity.py`) ⇒ **it cannot rot unnoticed**;
2. it is keyed on the **dotted key**, not a prose name;
3. its **SCOPE axis is already populated**;
4. **#1 is a section inside a dated 05-Jul audit report** ⇒ extending it would **edit dated history**.

⚠️ **CAVEAT, NOT A BLOCKER:** `ops_dashboard/` is a **subsystem home for a system-wide authority** —
the same shape as the `CT_SCRATCH_DIR`-inside-`data_store/` finding.
⛔⛔ **AUTHORITY, LOCATION AND MIGRATION PLAN ARE THREE SEPARATE RULINGS AND ARE NEVER BUNDLED** — ⭐
**if a bundle fails you cannot tell which part failed.**

**SEQUENCE (each step gates the next):**
`authority` → **STATUS validation (+ ownership)** → **row identity** *(step 2.5 —
`locked_decisions.yaml`'s 181 `LOCKED` may already supply one)* → `reconciliation` → **freeze #1
with a pointer** → **then item 5.**

**THE ROW CONTRACT — the parts that are structural rather than stylistic:**
- **THREE IDENTITY FIELDS, NEVER ONE:** **Row ID** (immutable) · **dotted key** (current
  implementation reference) · **display name** (presentation only).
  ⛔ **Nothing joins on the display name; nothing infers identity from the dotted key.**
- **ALIASES ARE FOR RENAMES ONLY** — append-only, each with **date + SHA**.
  🔴 **A change of SCOPE, DENOMINATOR, BASIS or BEHAVIOUR is a NEW ROW**, the old one `RETIRED` and
  pointing at it. ⭐ *Otherwise a 40%-of-**total** silently becomes 40%-of-**bucket** and nobody can
  find the moment it changed.*
- **RETIRED ROWS ARE NEVER DELETED** — `status=RETIRED` + date + SHA. ⭐ *"This control existed,
  enforced X, was removed on D"* **is evidence**, and historical citations still resolve — to a row
  that says RETIRED.
- **PROVENANCE PER IMPORTED ROW:** source inventory · import SHA · migration date ·
  **`verified_at_sha` + `verified_on` + verifier (`human` / `tool:<name>`)**.
  ⛔ **NOT A BOOLEAN** — ⭐ **a date says when someone looked; only a SHA says what they looked at.**
  ⚠️ *(The card's supporting figure — "we are 33 commits ahead of deployed" — **has already rotted**.
  ⛔ Never quote an ahead-count from prose; re-measure: `git rev-list --count origin/main..main`.)*
- **CONFLICT TAXONOMY:** `documentation error` · `implementation drift` · `intentional divergence` ·
  `insufficient evidence`.
  ⛔ **NEVER DRIVE CONFLICTS TO ZERO** — where the two inventories disagree, **one has been wrong in
  production for months, and WHICH is a MEASUREMENT.**
  🔴 **`intentional divergence` must name WHO decided, WHEN, and WHERE it is recorded — otherwise it
  is `insufficient evidence` wearing a better coat.**
  ⭐ *(It is the four finding-buckets specialised to a migration — ⛔ **not a new scheme.**)*
- **THE RECONCILIATION SUMMARY MUST BALANCE ARITHMETICALLY**, with the **expected equation printed
  in the document beside its result.** ⛔ **Print both sides AND the difference even when it is
  zero** *(M6's corollary: a printed zero is a measurement, an absent one is silence).*

⭐ **THE ARGUMENT FOR DOING ITEM 4 AT ALL, IN ONE LINE — and it is now evidenced, not asserted:**
inventory #1's **row 24 already carried the G3 drift control AND its non-escalating source.** What it
lacked was **SCOPE and DENOMINATOR** — ⭐⭐ **the two axes that would have shown the 10% band is an
*intraday-leverage* calibration.** ⇒ **THE MISSING AXES WOULD HAVE PREDICTED THIS MORNING'S CAPITAL-
DRIFT `CRITICAL` BEFORE IT FIRED.**

⚠️ **OPEN AND UNCHECKED — carried forward, ⛔ not answered here:** do `[LAUNCH-PHASE]` /
`[PERMANENT]` and `locked_decisions.yaml` already constitute the **STATUS** axis — and ⭐ **is either
tag ever READ BY CODE, or are they comments?** *(This is the **ownership test** applied to the
inventory's own STATUS column — see `campaign_practices.md` §M10.)*

---

## ~~⚠️⚠️ A0 — THE ONE OPEN DECISION OWED TO RAMA~~ → ✅ **CLOSED 02-Aug-2026 BY R1: THE GATE IS 3-PART.**

> ✅ **ANSWERED — the THREE-part reading wins** (Rama's earlier lean, ratified): a
> buy-day-filter slip **postpones ALL of the flip**, not merely the carry pilot. The gate
> is *observation ran · came back clean · **filter shipped***.
> ⭐ **Decided BEFORE the query, exactly as both sources demanded** — *"otherwise tonight's
> result gets read through whichever answer is more convenient"* — so the reason this
> mattered was honoured, not just the deadline. **The question below is kept legible (§G4)
> as the record of what was decided and against what alternative; it is NOT open work.**

### ~~The original open question, preserved:~~ ***Did a buy-day-product-filter SLIP postpone only the carry pilot, or all of the flip?***

> ### Does a buy-day-product-filter SLIP postpone **only the carry pilot**, or **all of 4-Aug**?

**SOURCE:** S4 §7.9(f) + S4 line 287 · S5 lines 103–116 · S2 §1 (`⚠️ OPEN (§7.9(f))`).
**WHY-PENDING:** the two readings share the same deadline, so nothing moves *today* —
**they differ only if the filter slips**, which is exactly the case now in play.

| reading | if the filter slips, then… | where it is recorded |
|---|---|---|
| **TWO-part gate** *(as recorded in S4/S2)* | the **four flags still flip Tue 4-Aug**; only the **carry pilot** waits. | S4 line 285–286: *"THE GATE HAS TWO PARTS TODAY, NOT THREE"* |
| **THREE-part gate** *(Rama's earlier lean)* | a filter slip **postpones ALL of 4-Aug** — observation ran · came back clean · **filter shipped**. | S4 §7.9(f): *"tonight's instruction described the flip's hard gate as THREE-part"* |

✅ ~~⛔⛔ **DECIDE IT BEFORE MONDAY'S CLOSE — not after the query.**~~ **DONE — decided
02-Aug, before Monday opened.** Both sources gave the same reason and it was the whole
point: *"otherwise tonight's result gets read through whichever answer is more
convenient."* (S5:114-116; S4:1062 *"Do not resolve this by picking one on the day."*)
⇒ **R1 chose the THREE-part reading.**

⚠️ **THE MATERIAL FACT THE DECISION NOW HAS THAT IT DID NOT HAVE ON 31-JUL:** the
filter's *"honest window"* was **this weekend, Sat 1-Aug / Sun 2-Aug** (S4:1054, S5:109)
— **and this file is a register build, so the filter is not being built in it.** ⇒ the
slip case is no longer hypothetical; it is the live branch unless the filter lands
Sunday. **This file does not resolve that either — it states it.**

---

## A1 — MON 3-AUG OBSERVATION DAY `<DEPLOYED, awaiting VERIFIED LIVE>`

**SOURCE:** S5 (the card, authority) · S4 lines 255–282 · S2 §1.
**WHY-PENDING:** the symbol+direction rule went live at the 31-Jul push (`297b587`,
21:44:48, verified 3 ways, `load_all` OK) and **has never executed in production.**
Monday is its first market day. ⛔ **BUILD NOTHING MONDAY — it is a measurement.**

- **What is live:** `risk.one_trade_per_symbol_direction_per_day: true` →
  `signals/signal_processor.py::_enforce_one_trade_per_symbol_direction` → writes
  `signals.status = REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT`. All three entry paths
  (gate / main / retest), each inside `portfolio_lock` immediately before `approve()`.
  **No bypass.**
- **The query is in S5 lines 43–56. ⛔ Do not rewrite it.** The status list
  (`PENDING_FILL, OPEN, PARTIAL, EXITING, CLOSED, CLOSED_MANUAL`) **is not negotiable** —
  a query written on `status='CLOSED'` would report **correctly-working** rejections as
  UNEXPLAINED and could postpone 4-Aug for no reason at all.
- **READ EACH ROW:** named direction ≥1 ⇒ ✅ rule working · **both counts 0 ⇒ 🔴
  UNEXPLAINED, POSTPONES 4-AUG** · **only the OPPOSITE direction ≥1 ⇒ 🔴 WORSE — the rule
  blocked a REVERSAL, postpones 4-Aug AND the rule comes back off.**
- ⭐⭐ **ZERO ROWS IS NOT A PASS** (S5:79-94). It means the rule was never *exercised*.
  Read it as **NO EVIDENCE**, say so out loud on the night, and **prove the day had
  signal traffic first** (S5's second query) — a day with no signals proves nothing.
- ⚠️ **EXPECTED, NOT FINDINGS:** the `REJECTED_NOT_MIS_TRADABLE (shadow)` WARNING (the
  mis_filter shadow log — **nothing was dropped**; ⛔ anyone reading it as the filter
  *enforcing* will "fix" a working system) · **no migration at the 08:15 boot**
  (`EXPECTED_SCHEMA_VERSION` unchanged at 45 — **a migration line WOULD be a finding**).

### A1(z) — 📌 THE ZERO-CASE CARRY-FORWARD
**SOURCE:** S5:85-87. **WHY-PENDING:** if Monday returns zero rows, the rule enters 4-Aug
**still unexercised in production** — the *configured-but-never-covered* shape, which is
this audit's dominant defect class (S3 verdict). ⛔ **Carry it forward as an OPEN
observation, never as a closed one.** Cross-ref: **IA-XARCH-01 / IA-XCFG-02 / IA-XTEST-01**
(§B#1) — the same class, and the mechanism that would close it.

---

## A2 — ~~TUE 4-AUG~~ ⭐ **WED 5-AUG: THE FLAG FLIP** ~~`<PENDING>`~~ ~~✅ **`<DEPLOYED>` 04-Aug 19:08:09**~~ ~~⭐⭐ **`<DEPLOYED — CAPABILITY VERIFIED LIVE 05-Aug 08:15; OUTCOME UNVERIFIED>`**~~ ⭐⭐⭐ **`<DEPLOYED — ENTRY PATH VERIFIED LIVE 05-Aug; EXIT PATH UNVERIFIED>`** — the first irreversible step, taken, **and it worked** *(⛔ the compound label is deliberate and is now on its SECOND tightening in one day: 08:15 `will_trade` was a PERMISSION; a filled CNC position proves screening→sizing→placement→FILL; ⛔ **the GTT trigger, T+1 and the carry remain unproven**, so delivery as a whole is still not `<VERIFIED LIVE>`)*

> ### ✅ STATUS SUPERSEDED 04-Aug (§G4 — the `<PENDING>` above is struck, not deleted, because the gate sequence that cleared it is the record)
> **All 10 gates ran in order; none skipped.** 1 census clean · 2 the 18:00 drift WARNING matched `expected_alarms` §5a exactly (names exactly the 4 claude heartbeats, arrives as WARNING) · 3 **after 18:15 and the book FLAT — proven WIDE:** *every* trade row in the DB is terminal (FAILED 221 / CLOSED 167 / REJECTED 59 / CLOSED_MANUAL 45 / CANCELLED 9), no OPEN/EXITING/PENDING_FILL on **any** date; `gtt_state` **0 rows** · 4 service `inactive`, `Result=success`, `NRestarts=0`, one continuous run 08:15:09→17:35:04 · 5 forward-shadow banked 18:17, **exactly ONE new date, no contamination** · 6 **D2 SHA inventory 27/27** · 7 tree clean · 8 `ls-remote` · 9 **full gate PASS, sets not counts** · 10 push.
> ⭐ **THE THREE-PART GATE (R1) WAS SATISFIED, NOT WAIVED:** A1 ran, came back clean, and **A4 (the buy-day filter) was live** — it rode the 03-Aug push.
> ✅ **VERIFIED AT THE DEPLOYED TREE, not the bare HEAD:** all 7 changed files **md5-identical PC vs VM**; live values grepped on the VM (`force_intraday_only: false` `:89` · `trade_type: BOTH` `:96` · `delivery_enabled: true` `:102`); both `cnc_gtt_*` read `expected-event-driven`; deploy reflog shows **BOTH halves** at 19:08:09 (`push` **and** `checkout`). ⭐ **Zero schema change CONFIRMED (`core/schema.sql` diff = 0 lines)** ⇒ the evening-schema-push overnight-CRITICAL hazard did not apply.
> ⚠️ **ONE DEVIATION FROM THE PLAN, RECORDED BECAUSE IT WAS A JUDGMENT CALL RAMA COULD HAVE REVERSED (he ruled "push now"):** gate 9 surfaced **one** new failure — `test_slice2_strategy_control.py::TestSchemaConfig::test_trade_type_default_intraday`, whose body asserted `load_all(_CFG).system.trade_type == "INTRADAY"` (**the SHIPPED VALUE**) while its name **and its class `TestSchemaConfig`, every sibling of which asserts a schema property**, promise the **DEFAULT**. ⭐ **§"no fixed number where a property is meant" — exposed BY the flip, not caused by it.** Measured before touching: default still `INTRADAY` (`config_loader.py:1761`), omit-key construct still `INTRADAY` ⇒ **the property is unchanged; only the deployed value moved.** ⛔ The old body **could not have gone red if the DEFAULT itself changed** — the one failure its name describes. Corrected to assert the property; final gate **9F/5,549P/4skip vs BASE 9F/5,549P/4skip, `comm` EMPTY both ways.** ⛔ A test file ships to the VM and **never executes there**, which is why it did not violate the card's "no implementation" rule (whose stated rationale is *"all first execute on flip morning"*).
> ⚠️ **`conditional_allocation_enabled` (R10) DELIBERATELY NOT FLIPPED** — `config_auditor.py:258-262` states that with both intents active it returns the **SAME split as false** ⇒ a **no-op under BOTH**, and the delivery-only case that would make it matter is guarded by **A5 at Severity.BLOCK**. ⛔ The register's *"~70% of capital strands"* premise assumes a **delivery-only** book, which is not what was deployed. **Rama's call remains open; it is simply not urgent.**
> 🔴 **STILL OWED AND NOT CLOSED BY THE PUSH:** the four flags listed below said "four" — **the fourth was `conditional_allocation_enabled`, and `trade_type` was the one nobody listed.** Both facts are now recorded above.

> ### 🔌🕳️⭐⭐ THE BOOT CHAIN — **TRACED 05-Aug. NOTHING IN CRON STARTS THE SERVICE, AND THE REGISTER HAS NEVER SAID OTHERWISE *BECAUSE IT HAS NEVER SAID ANYTHING***
> **THE MECHANISM, measured at HEAD:** `trading-system.service` is **`WantedBy=multi-user.target`** (VM-boot only) with **`Restart=on-failure`** + **`RestartPreventExitStatus=3 4`** ⇒ ⛔ **a clean exit 0 NEVER self-restarts**, which is why the 17:35 self-exit is terminal for the day. The actual starter is **`token-watcher.service` → `deploy/token_watcher.sh` (FIX-188), running since 28-Jul**, `Environment=SLEEP_SEC=30` (unit `:11`; script default `:21` agrees), guarded by **`within_service_window()` — `hour ≥ 8 and < 16`** (`:47-50`, FIX-189) and by the prior-day-exit disposition. **The 08:15 cron writes the TOKEN FILE and nothing else.**
> ⭐ **WHY THIS IS RECORDED AS A FINDING RATHER THAN A CORRECTION — and the width is stated so the claim is falsifiable (§"an absence needs a check wide enough to have found the thing"):** a grep of this register for `token_watcher|token-watcher|token file|zerodha_token` returns **0 hits**, and for `cron.*boot|boot.*cron` **0 hits**, against **14** occurrences of `08:15`. ⇒ ⛔ **The register never asserted that cron starts the service — it treated "the 08:15 boot" as an ATOMIC EVENT with no named mechanism.** That is not a false claim; **it is an unexamined one**, and it is precisely how the single point of failure below stayed unregistered through a whole campaign. **Another declared-vs-actual gap found by measuring rather than by reading.**
> 🔴🔴 **THE CONSEQUENCE, AND IT IS THE REASON THIS BLOCK EXISTS: A FAILED TOKEN REFRESH PRODUCES SILENCE — NO BOOT, NO ERROR, NO ALERT, NO CRITICAL.** ⇒ ⛔ **"no delivery order today" and "the service never started" are INDISTINGUISHABLE unless the token file is checked FIRST.** ⚠️ **AND THE OBVIOUS SECOND CHECK DOES NOT SUBSTITUTE FOR IT:** `logs/cron-auto-token.log` **logs only on abnormality** (no write in 8 days) ⇒ **silence there is SUCCESS, so an unchanged log is NOT proof of a refresh.** ⭐ **The token FILE — `data_store/session/zerodha_token.json`, present AND `date` == today — is the gate; the log is not.**
> ⇒ 📌 **REGISTERED AS: A SINGLE POINT OF FAILURE WITH NO ALARM ON IT.** ⛔ **DO NOT FIX IT — REGISTER IT.** It is unauthorised work, it sits on the boot path, and today is flip day. *(Sibling of G1's 95-minute unwatched window: both are **absence-of-signal** holes, where the healthy state and the dead state emit the same thing — nothing.)* ⚠️ **SYSTEM_MAP entry OWED** (memory `boot_chain_token_watcher_05aug.md`); ⛔ not written here, because this register is not the map.
> ⚠️ **A PREDICTION OF MINE CORRECTED IN THE RECORD, because left standing it would have made a NORMAL boot look wrong EVERY DAY:** I wrote *"expect `active` ~08:15:30–08:16"*. ⛔ **That was the PESSIMISTIC END of the poll window, quoted as if it were the window.** With a **30 s** poll and the token landing at `:01.910`, the service start is distributed **~08:15:02–08:15:32, roughly uniformly** — so **08:15:05 is a FAST DRAW, not an anomaly**, and a start at `:30` is equally normal. ⭐ **A one-sided bound restated as an expectation converts the whole low half of a normal distribution into false findings.**
>
> ### ✅⭐⭐⭐ (a) IS **ANSWERED** — **DELIVERY PLACED AND FILLED. THE FLIP WORKED END TO END ON THE ENTRY PATH.**
> **REPORTED 05-Aug intraday: TWO CNC positions are open at the broker**, product `CNC`, one of them tagged `HOLDING`, with a GTT reported accepted shortly after. ⇒ ⭐⭐ **THE QUESTION THE WHOLE FLIP WAS BUILT TO ANSWER IS CLOSED AFFIRMATIVELY**, and it goes far past this morning's `will_trade` permission: **screening · sizing · affordability · placement · FILL all cleared.** The 04-Aug worry that the flip might be a no-op that looked complete is now refuted twice over — **once by permission at 08:15, once by a filled position.**
> ⚠️⚠️ **PROVENANCE — STATED FIRST, BECAUSE IT DECIDES WHAT THIS BLOCK IS ALLOWED TO CLAIM: THIS IS OPERATOR-REPORTED FROM A KITE SCREENSHOT, ⛔ NOT MEASURED.** ⛔ **NO FIGURE BELOW HAS BEEN VERIFIED AGAINST THE DB OR THE BROKER FROM HERE, AND NONE IS RECORDED AS IF IT HAD BEEN.** The reason is measurable rather than assumed: the PC's `data_store/trading_system.db` carries **mtime 2026-08-03 16:08** — **two days stale, predating both the flip push (04-Aug 19:08) and this morning's boot** ⇒ **it cannot contain a single row from today**, and the standing rule ⛔ **never `scripts/*.py --db <copy>`** forecloses the copy route regardless. ⇒ ⭐ **This entry is a PROMPT TO MEASURE that records what is known and names what is not — it is exactly the seeded-from-conversation shape that has repeatedly produced false findings in this campaign, and it is labelled rather than laundered.**
> 📏 **THE CONFIRMATION QUANTITY IS *TODAY'S TOTAL CNC VALUE*, ⛔ NOT SYMBOL NAMES — and that is a deliberate choice, not squeamishness.** A total is **checkable against one number** (`SUM(qty × avg)` across CNC positions, broker vs DB), it **survives a second fill or a partial exit** in a way a name list does not, and it **keeps position-level identifiers out of a document that is read, quoted and copied.** ⇒ **Record and reconcile the TOTAL; the per-symbol detail lives in the DB and the broker, which are the authorities anyway.**
>
> #### 🔬 OWED TONIGHT — **THE MEASUREMENTS THAT TURN THIS BLOCK FROM REPORTED INTO RECORDED** *(each must cite what it read)*
> **(i) THE POSITION SET.** Count of CNC positions · ⭐ **today's TOTAL CNC value** — computed **both** at the broker and from `trades`, and **reconciled against each other** (a divergence is itself the finding) · per-row `product`, `qty`, entry, timestamp, strategy, `trade_id` **read into the DB record, summarised as a total here.**
> **(ii) ⭐⭐ SCORE THE PREDICTION EXPLICITLY — RIGHT OR WRONG.** The prediction on record was **affordability binds before the 40% position-value cap**, on the scale-free ground that the cap is **40% of TOTAL** while the positional bucket is **30% of TOTAL** (above). ⛔⛔ **AND THE SCORING METHOD IS NOT "READ `binding_constraint`" — MEASURED AT HEAD, THAT LABEL CANNOT SETTLE IT:** `position_sizer.py:427-437` comments **"CAPITAL wins on tie (most conservative), then RISK"**, so `constraint = "CAPITAL"` is **also what a THREE-WAY TIE reports.** ⇒ ⭐ **Compare the three persisted candidates in `breakdown` — `qty_by_risk` · `qty_by_capital` · `qty_by_concentration` (`:441-443`) — and score on which was STRICTLY the minimum**, which is precisely the method §C.1 G8(4) already used to get "strictly the min = 0 on 438/438". **The "never classify by free text" rule, applied to our own evidence.**
> ⚠️ **AND CHECK THE FLOOR BEFORE CREDITING ANY CAP, because at qty 1 it is the likeliest decider:** `raw_qty = min(...)` then **`tiered_qty = floor(raw_qty × tier_multiplier)`** at the measured permanent **`tier 0.5`** ⇒ **`raw_qty = 1` floors to 0 and FIX-133's floor lifts it back to 1** (`:466-476`). ⇒ **If `raw_qty` was 1 or 2, the final qty was decided by the TIER AND ITS FLOOR, not by any cap, and the `binding_constraint` label describes `raw_qty`'s `min()` — NOT the quantity that was actually ordered.** ⛔ **Say that instead, if that is what the breakdown shows.** ⭐ **Note the asymmetry that makes the prediction well-founded in the first place: `qty_by_capital` divides the BUCKET (`:419-420`, `avail`), while risk and concentration both scale `total_capital` (`:382`, `:423-424`).**
> **(iii) ⭐⭐ THE FIRST `gtt_state` ROW THIS SYSTEM HAS EVER WRITTEN IN PRODUCTION** (the table held **0 rows**): its **`status`**, its **`trade_id`**, and its **write timestamp** — ⛔ **and `trade_id` is not optional detail; §C.5's gate turns on it** (below).
>
> ### ⚖️ THE CEILING, HELD HONESTLY — **⛔ THIS IS *NOT* A BLANKET `<VERIFIED LIVE>` FOR DELIVERY**
> **A position that FILLED proves the ENTRY path.** ⛔ **The EXIT path is entirely unproven: the GTT actually triggering · T+1 handling · the carry.** ⇒ **§A2 is relabelled to `<ENTRY PATH VERIFIED LIVE; EXIT PATH UNVERIFIED>`** — ⭐ **the same discipline that produced this morning's compound label, applied again rather than relaxed because the news is good.** *(⚠️ M5 — what this does not cover: nothing here re-measures the boot, and no exit, trigger or T+1 event has been observed at all.)*
> ⭐ **THE TESTABLE PREDICTION, RECORDED BEFORE THE FACT SO IT CAN BE SCORED — and re-measured at HEAD, not recalled:** with `delivery_risk_per_trade_pct` / `delivery_max_position_value_pct` both **`null`** (`system_config.yaml:190-191`), `position_sizer.py:305-308` routes the positional bucket to the **GLOBAL** values ⇒ the position-value cap is **40%** (`:139`). ⛔ **AND ITS BASE IS `total_capital`, NOT THE BUCKET — `:585` `max_position_value = eff_max_position_value_pct * total_capital`**, the same base already measured for the RISK leg at §C.1 G8(4). ⇒ **cap = 0.40 × ₹9,883.70 = ₹3,953.48 against a positional bucket of ₹2,965.11 ⇒ AFFORDABILITY SHOULD BIND FIRST, NOT THE 40% CAP.** ⭐⭐ **AND STATE IT SCALE-FREE, PER THE DENOMINATOR DISCIPLINE (§C.2 G18(a-i)), BECAUSE THE RUPEE FORM EXPIRES TONIGHT AND THE RATIO DOES NOT: the cap is 40% of TOTAL while the whole positional bucket is 30% of TOTAL ⇒ 0.40 > 0.30 ⇒ THE POSITION-VALUE CAP CANNOT BIND ON A POSITIONAL ENTRY AT ANY CAPITAL while the 70/30 split holds.** ⇒ 🔴 **IF SOMETHING ELSE BINDS, THAT IS A FINDING** — and if `POSITION_VALUE_CAP` binds, the finding is **structural**, not a tuning surprise.
> ⚠️⚠️ **A LATENT MISLABEL FOUND WHILE MEASURING THE ABOVE — ⛔ DISCLOSED, NOT FIXED, NOT A NEW ROW (§G3; 231 stands), AND IT IS THE `ENFORCE + PLACEHOLDER` CELL OF §A-DEC-3 §1 ARRIVING IN REAL CODE:** `position_sizer.py:585` **enforces** `eff_max_position_value_pct`, but the CRITICAL log at **`:596`** and the rejection reason at **`:609`** both report **`self._max_position_value_pct` — the GLOBAL**. **Today they are identical** (the delivery override is `null`), which is exactly why nothing is broken and exactly why this is invisible. ⛔ **The moment `delivery_max_position_value_pct` is set — which is A-DEC-3 order item 5, the delivery configuration surface — the operator-facing CRITICAL will state a percentage that was NOT the one enforced.** ⭐ **Same family as `capital_at_risk` and `sl_distance_pct` (§C.2 G18(b)): a name reporting a quantity it does not hold.** ⇒ **it is not a bug today; it is a TRAP LAID FOR ITEM 5, and it must be closed BY item 5, not after it.** *(LATENT ⇒ document and continue, per the LIVE-vs-LATENT rule.)*
> **(b) ~17:35 — THE SHUTDOWN CENSUS. ⛔ THE ONE ARTIFACT THAT CANNOT BE RE-RUN** (it emits at `_shutdown()` and is gone if the service is disturbed). 🔴 **If delivery TRADED and `cnc_gtt_placer`/`cnc_gtt_monitor` still read `acted 0`, THAT IS A FINDING** — R4 moved both to `expected-event-driven` precisely so this reads as a mismatch rather than as design. ⭐⭐ **AND AS OF (a) THIS IS NO LONGER A CONDITIONAL: DELIVERY *DID* TRADE, so the census now carries a REAL TEST rather than a hypothetical one** — the `acted 0` reading that was correct every previous day would today be a defect. ⭐ **The units were switched to event-driven for exactly this day, and this is the evening that scores that decision.**
> **(c) 🔴🔴 WED EVENING, BEFORE THU 08:15 — THE CHECK1 GATE. ⛔⛔ NOW MANDATORY, NOT CONTINGENT — (a) SUPPLIED ITS PRECONDITION.** ⛔ Not restated here; it is **§C.5's first row, in full, with both routes, all three branches, and the trade_id-keyed predicate re-measured at HEAD.** ⭐ **Cross-referenced rather than copied (§G3)** — a gate written twice is a gate that gets updated once.
> ⚠️ **AND THE TWO NON-INTERVENTIONS, because on flip day the temptation runs the other way:** a **CNC position still open after 15:17 is CORRECT** (EOD6 never touches CNC) ⇒ ⛔ **no intervention** — ⭐ **and TODAY is the first day that carve-out is LOAD-BEARING rather than theoretical: for the first time there is a real CNC position for it to spare** · and **THU 6-AUG** is when the holdings-blindness bites (**11 positions-only readers, 6 correct BY ACCIDENT, 3 of those on the kill path** — §C.6 F1), which is a **Thursday** fact and must not be pulled forward into Wednesday's decisions. ⭐ **It also stops being a class-level worry tonight: there are now specific CNC positions that WILL move from `positions()` to `holdings()` at T+1.**

⭐ **DATE MOVED BY R2 (Option Y), 02-Aug — a RESEQUENCING, not drift.** 4-Aug is **not
deleted**: it is now the **shakedown day**, and the **flip flags are pushed on Tue 4-Aug
evening** so the flip itself lands **Wed 5-Aug**. The extra day buys **one additional
clean observation day and the first real EOD census** before anything irreversible.

**SOURCE:** S4 line 283–288 · S2 §1 · S3 IA-XCFG-04 · **the date and sequence: A-DEC R2.**
**WHY-PENDING:** gated on A1. **The four flags** (verified consumers, IA-XCFG-04):
`delivery_enabled` · `force_intraday_only` · `capital.conditional_allocation_enabled`
(= **R10**, §C) · `trade_type` (strategy control).
⭐ **RIDING THE SAME FLIP-FLAG PUSH (R4):** the `cnc_gtt_placer` + `cnc_gtt_monitor`
registry edits (expected-dormant → **expected-event-driven**). ⛔ Without them the census
**mismatches by design** on the very day delivery goes live.

**THE HARD GATE — now THREE parts, all settled (R1):**
1. **Monday's observation ran** (A1);
2. **it came back clean** — ⛔ ANY unexplained row postpones it; it is the query's binary
   answer, **not a judgement made at 16:00 on the day**;
3. ✅ **THE THIRD PART IS NOW DECIDED, NOT OPEN — R1: the gate IS 3-part.** **The buy-day
   filter (A4) must be live.** ⛔ **If the filter slips, the ENTIRE flip postpones** — not
   merely the carry pilot. *(A0 closed 02-Aug; see A-DEC.)*
   ⚪ Status: A4 is `<BUILT — NOT DEPLOYED>` across all three sell-under-kill sites and
   rides the **Mon 3-Aug** code push (R2/R3), so on the current plan part 3 is **satisfied
   by Tuesday** — this gate binds only if that push slips.

✅ **CLEARED BY THE AUDIT, recorded so the flip does not re-derive it** (IA-XCFG-04,
§B-below-line): the delivery-flag graph is **coherent, no unguarded combination**; **VM
config == repo config, md5-identical ×3**; the scheduled kills **correctly exempt
delivery** (T2-proven, P7); the GTT construction path is **broker-proven end-to-end**
(T2 5/5).

⚠️ **PREDICTED BY THE AUDIT — will happen, noisy but harmless** (IA-XEVOLVE-04): F1's
three false-alarm faces on every delivery lifecycle event (**IA-P8-04**) · the GTT-blind
"naked" warnings (**IA-P5-06**) · the never-run `delivery_symbols` exclusion branch (G6)
going live.
🔴 **AND WILL NOT BE CAUGHT BY THE FLIP'S OWN INSTRUMENTS:** the `eod_verify`/shadow gate
is **broken** (**IA-P8-01**, §B#6) ⇒ ⛔ **"a clean shadow week" CANNOT certify the
expansion.**

---

## A3 — THE CARRY PILOT (D1) `<PENDING, BLOCKED>`

**SOURCE:** S2 §1 (*"the flip and the CARRY PILOT must be SEPARATED"*) · S4 §7.9(f).
**WHY-PENDING:** **blocked until A4 ships.** The flip is a non-blocker; the pilot is not.
⚠️ Also blocked by **Q4's HARD_KILL decision** (memory `q4_hard_kill_delivery_30jul`:
*"🔴 Blocks the CARRY PILOT, not the flip"*).

> 🔴🔒 **NEW HARD GATE 03-Aug — `#2e` MUST LAND BEFORE THIS PILOT (Rama's ruling ~10:30).** Record: `docs/audit/ledger2e_design_03aug2026.md` · evidence `docs/audit/kill_drill_03aug2026.md` §4 @ `0c17056`. **THE DEFECT:** under a HARD_KILL the **local-pass** flatten sells the symbol's **net across ALL products** — measured `place_order('SYM','SELL',**15**,…)` on a `(SYM,MIS 10)+(SYM,CNC 5)` book ⇒ **5 shares come out of the delivery position the buy-day filter spared one line earlier.** Root cause `broker/position_helpers.py:34-39` (`broker_net_qty` sums by SYMBOL, no product filter), consumed at `kill_switch.py:1613`; **site 2 (`:1665`) uses the per-row qty and is CORRECT ⇒ the two sites disagree.**
> ⭐ **WHY IT GATES *THIS* ITEM SPECIFICALLY: the pilot exists to HOLD DELIVERY WHILE TRADING INTRADAY — which IS the two-row `(symbol,MIS)+(symbol,CNC)` book. It does not merely make the collision possible, it MANUFACTURES it deliberately and repeatedly.** The CNC half already exists (T2's 5 overnight CNC since 29-Jul); the pilot supplies the missing intraday half by design ⇒ **what is latent today becomes routine the moment the pilot runs.** Same shape as Q4's rule that the filter lands before anything holdings-aware.
> ⛔ **IT DOES NOT GATE WEDNESDAY'S FLIP** — flip and pilot were separated deliberately, and the flip alone does not create the two-row book. ⛔ **AND IT DID NOT BLOCK MONDAY'S PUSH:** the defective helper is byte-identical to what is already live, and the push takes CNC sparing from **0 sites to 2** ⇒ a strict improvement.
> ⛔ **DESIGN ONLY — NOT IMPLEMENTED, NOT AUTHORISED.** `broker_net_qty` is **shared** with `order_placer`'s emergency exit and **FIX-190 created it to PREVENT an oversell** (the 19-Jun THELEELA `BUY 1 → SELL 1 → SELL 1 → short −1`) ⇒ **a careless product filter re-opens that class.** Careful-loop, not a one-liner. Three directions recorded (D-A/D-B/D-C), **none chosen**; Step 1's acceptance criterion is *"can the THELEELA sequence still be detected per-product"*. ⚠️ **Any pin must drive the REAL `determine_close_direction`** — the existing suite stubs it and would be vacuous (practices §V4, 2nd entry). ⛔ No new register row — **231 stands.**

---

## ⛔⛔ A4 — THE BUY-DAY PRODUCT FILTER (+ CRITICAL-on-NULL) ✅ **`<BUILT — NOT DEPLOYED>` — ALL THREE SELL-UNDER-KILL SITES (01-Aug night + #2b 02-Aug)** — ***the single most sequencing-critical item in this register***

> ✅ **BUILT `43043a2`+`15adf75`+`2e606ec` (01-Aug ~20:0x–20:5x):** both HARD_KILL
> emergency sites product-filtered via the ONE shared source
> (`core.constants.EMERGENCY_FLATTEN_PRODUCTS`, also read by the two scheduled EOD
> passes — no second copy to drift); **CNC SPARED loud** (honest attempted-count);
> **NULL/NRML/unknown FLATTEN + CRITICAL** (the silent fallback is gone); the Kite
> per-product-row hazard (spared CNC must not shadow a same-symbol MIS row) found in
> design and test-pinned both directions; regression **NEW-set EMPTY**. Record:
> `docs/audit/buyday_filter_build_01aug2026.md`. ⛔ **DEPLOY = Mon-eve stack, gated on
> D1–D3 ratification + Monday observation clean + Monday PC gates (kill drill, #1
> composition boot, calm regression confirm).** ~~🔴 NEW ruling owed (record §2): the
> reconciler CHECK2 third site — #2b Mon-eve (~6 lines) or a named carry-pilot blocker.~~

> 🔴🧪 **03-Aug MONDAY KILL DRILL — 9/10 PASS, ONE RED. ⛔ FINDINGS-ONLY, NOT FIXED; needs its own card.** Record: `docs/audit/kill_drill_03aug2026.md`.
> ✅ **Case A (the card's book, distinct symbols) 9/9:** CNC spared at BOTH sites with no order placed, MIS flattened, NULL flattened + `UNKNOWN PRODUCT` CRITICAL + notifier escalation, spares logged. **The filter does what this entry claims.** Live DB mtime **identical before and after** (`2026-07-27 15:37:27.138808200`); MagicMock store ⇒ no sqlite path in the run; VM untouched on `297b587`.
> 🔴 **Case B — SAME-SYMBOL MIXED BOOK: site 1 sells the SYMBOL NET, not the intraday qty.** Local MIS trade `SYM` qty **10**, broker holds `SYM` MIS 10 **and** `SYM` CNC 5 ⇒ measured `place_order(SYM, SELL, **15**, INTRADAY)`. **The 5 extra shares come out of the delivery holding this filter just spared.** Root cause is ONE function: `broker/position_helpers.py:34-39` `broker_net_qty` sums every row matching the **symbol** with **no product filter**, and `kill_switch.py:1613` (site 1) consumes it via `determine_close_direction`. ⭐ **Site 2 is CORRECT by contrast — it uses the per-row `pqty` (`:1665`) and never calls the helper.** ⇒ **the two sites disagree, and only one had been measured.**
> ⛔⛔ **THE CLAIM ABOVE IS CORRECTED, NOT DELETED: "test-pinned both directions" names the two SUPPRESSION directions (a spared CNC must not shadow a same-symbol MIS row, at either site). There is a THIRD direction — the exit QUANTITY — which is neither pinned nor correct.** ⭐ **It could not have been caught by that suite: `tests/unit/test_kill_switch_product_filter.py:40-41` monkeypatches `determine_close_direction` to `lambda _a,_s,side,qty: (side,qty)`, so the local qty is returned BY CONSTRUCTION.** A mock whose return value you order cannot test the thing you ordered — any future pin must exercise the REAL helper.
> ✅✅ **NOT A REASON TO HOLD TONIGHT'S PUSH — MEASURED BOTH WAYS: (i) `git diff 297b587..HEAD -- broker/position_helpers.py` is EMPTY ⇒ the defective helper is byte-identical to what is ALREADY RUNNING on the VM (last touched by FIX-190 `b49764f`/`870bbc9`, long before this campaign); (ii) `297b587` contains ZERO `SPARED delivery position` sites and HEAD contains TWO ⇒ production TODAY flattens the ENTIRE CNC holding under a HARD_KILL, and the push spares all of it EXCEPT the same-symbol overlap. The push is a STRICT IMPROVEMENT with a residual gap.**
> ⏳ **REACHABILITY — LATENT, conditions half-present:** needs a HARD_KILL (**never fired**) **and** the same symbol held both intraday and CNC. ⚠️ **The CNC half already exists — T2 has held 5 CNC overnight since 29-Jul** — so only an intraday signal on one of those symbols is missing. 🔴 **The CARRY PILOT raises this materially** (it exists to hold delivery while trading intraday = exactly this two-row book).
> ⛔ **OWED, NOT DONE: (1) a card for the fix — `broker_net_qty` is SHARED (`order_placer`'s emergency exit is the other caller) and FIX-190 exists to PREVENT an oversell, so narrowing it carelessly could re-open the THELEELA class ⇒ careful-loop, NOT a one-liner; (2) this entry's "delivery survives a HARD_KILL" needs the qualifier "except on a same-symbol mixed book"; (3) a pin that uses the real helper.** ⛔ No new register row — **231 stands.**

> ✅ **#2b — THE THIRD SITE IS NOW ALSO `<BUILT — NOT DEPLOYED>` (02-Aug `6495baa`+`c5c2668`).**
> `order_reconciler._check2_inflight_orphan` reads the SAME
> `EMERGENCY_FLATTEN_PRODUCTS`: **CNC SPARED loud** (new `check_name`
> `INFLIGHT_ORPHAN_SPARED_DELIVERY`, and deliberately recorded as resolved NOWHERE so it
> stays visible each cycle) · **NULL/unknown FLATTEN + CRITICAL through the EXISTING
> shared emitter** (no second definition) · MIS/CO unchanged. **0 status literals, 0
> `holdings()` ⇒ the Q4 ordering rule holds and D-8 stays blocked exactly as before.**
> 16 targeted tests (incl. a single-caller tripwire); RED-on-old 11F/5P; regression
> **7F/5,491P, NEW-set EMPTY** vs a base measured fresh the same session; revert verified.
> Record: `docs/audit/reconciler_product_filter_build_02aug2026.md`.
> ⛔ **NOT a new register item — this is A4's own third site, and A4 is debt-ledger #2
> counted ONCE here** (§B#2 stays a cross-reference; adding a §B#2b row would inflate the
> register by 1, the exact vanity S1 §9 / S2 §3.7 warn against). **231 stands.**
> 🔴 **R4 — what remains owed is a DEPLOY-SLOT choice, no longer a build choice:** ride
> the Mon-eve stack, or name it a documented carry-pilot blocker. It never gated the flip.
> ⚪ **Newly disclosed, not patched** (a 4th thing, outside the filter's three): the
> reconciler's flatten passes `intent="INTRADAY"` unconditionally ⇒ a **CO** position
> would exit under an MIS intent (H-5 class). ~~No CO position has ever reached it. Owed: a
> ruling, not a fix-in-passing.~~ **→ SUPERSEDED, see #2c below.**

> ⛔ **#2c — CLOSED as `<STOPPED AT STEP-1>` (02-Aug, docs-only `[this commit]`) — a
> VALIDATED REDESIGN TRIGGER, not an implementation failure. NO CODE WAS WRITTEN.**
> #2c was carded to map the intent (CO→COVER_ORDER) at that sell. Its Step-1 runtime-
> semantics gate **measured all three limbs and stopped before any edit** — the card's
> premise (*intent = a label*) was **FALSE**:
> **(a)** `intent` **IS** the live Kite `product` field (`product_resolver`
> INTRADAY→MIS · DELIVERY→CNC · COVER_ORDER→CO), sent as `product=`.
> **(b)** ⛔ **Audit 3.1 — a CO position CANNOT be squared by a reverse order at all**
> (broker rejects; auto-squares 15:20 with a **₹50+GST penalty**). The correct path is
> `cancel_order(entry_broker_id, variety="co")` on the parent bracket — **`eod_squareoff`
> already does exactly this.** ⇒ the carded mapping would only emit the order the broker
> refuses.
> **(c)** the path **cannot reach a parent order id** — inputs are `(symbol, bp,
> tag_prefix, trade_id)` and `bp` is a broker POSITION row; ⭐ `trade_id` IS present = the
> only affordance a redesign has.
> **(d) 🔴 TODAY'S REAL BEHAVIOUR, worse than first disclosed:** `product="MIS"` is
> **ACCEPTED**, does **NOT** net against the CO position, and **opens a NAKED MIS SHORT
> while the CO position survives.**
> **(e)** LATENT not live — **double dormancy**: CO never used (805/805 regular, X6;
> declared by 12/15 YAMLs, discarded) **AND** `force_intraday_only: true` coerces
> non-INTRADAY back inside `place_order` ⇒ the carded fix was also a **no-op today**, its
> only effect a new WARNING, arming silently on the breaker flip.
> **(f) ⭐⭐ STANDING RULE (the most reusable finding, beyond #2c): PAPER CANNOT VALIDATE
> PRODUCT SEMANTICS** — paper nets by **SYMBOL**, live Kite nets per **(symbol, product)**
> ⇒ **a paper drill of any product-semantics change is vacuously GREEN.** Validate against
> live semantics or by construction, never by a paper run.
> **(g)** no `orders` row is written here (RC18); and **record-only latent hazard:** this
> caller never passes `variety`, `place_order` defaults `"regular"`, and **nothing
> validates variety-against-product**.
> ⛔ **NOT a new register item** — like #2b this is A4's own site, and A4 is debt-ledger #2
> counted ONCE. **231 stands.** ~~🔴 Redesign **#2c-R** is with ChatGPT for red-team; ⛔
> neither candidate architecture has been started.~~ **→ #2c-R IS NOW BUILT, see below.**
> ⛔ **Never "fix" this by mapping the intent.** ⛔ #2c never touched Monday's critical path.

> ✅ **#2c-R — ORPHAN-CO REFUSE-AND-ESCALATE `<BUILT — NOT DEPLOYED, NOT PUSHED>`
> (02-Aug `42db913` code+15 tests, `bb25fa6` record+stamps).** Option 1 of the
> red-team, binding. Under an active HARD_KILL the reconciler's CHECK2 orphan path now
> **REFUSES** to flatten a **CO** position and escalates instead of selling: **no order of
> any kind is placed**, a CRITICAL names the reason (Audit 3.1 — a CO position cannot be
> squared by a reverse order; this path has no parent bracket id; operator/EOD action
> required), disposition `INFLIGHT_ORPHAN_REFUSED_CO` with **`success=False`** (a refusal
> is correct-but-INCOMPLETE, unlike the CNC spare's correct FINAL state).
> ⭐ **Because CO IS a member of the shared `EMERGENCY_FLATTEN_PRODUCTS` (read by FIVE
> sites), the refusal is a site-LOCAL branch placed BEFORE the membership test — ⛔ CO was
> NOT removed from the constant, which would have silently changed four other sites.**
> `core/constants.py` **byte-untouched**, as are `kill_switch`/`eod_squareoff`/adapter/
> resolver. **0 status literals · 0 `holdings()` ⇒ Q4 ordering rule intact, D-8 still
> blocked.** RED-on-old 11F/4P; **regression NEW-failure set EMPTY** (base measured fresh,
> same session + window); **revert proven to ZERO BYTES** against `0a9e13a`.
> ⚠️ **NEW MEASUREMENT — the alert is BOUNDED, and it bounds #2b too:** CHECK6's FIX-B
> (wired, `main.py:2739`) marks a `PENDING_FILL` trade FAILED + releases its reservation on
> the **3rd consecutive cycle**, after which the position routes to
> `_check2_orphan_adoption` (once-a-day suppression, `HUMAN_ORDER` label) ⇒ **~3 CRITICALs,
> not an unbounded stream**, while the position is still live at the broker. **#2b's CNC
> spare inherits the same ceiling** — its "re-alerts EVERY cycle" note is corrected, not
> left standing. ⛔ Disclosed, NOT patched (bounding is CHECK6's).
> ⭐⭐ **STEP-1b — A NEW DISCLOSURE, REPORTED NOT PATCHED, NEEDS ITS OWN CARD: `kill_switch`
> HAS THE SAME CO DEFECT AT ALL THREE OF ITS SELL SITES** (`:1629` local · `:1708` sweep ·
> `:1789` retry). It maps CO→`COVER_ORDER` and **`variety` appears NOWHERE in the file** ⇒
> coercion OFF sends the reverse order Audit 3.1 says the broker **rejects**; coercion ON
> (today) sends **MIS** — accepted, doesn't net, **naked MIS short**. ✅ **`eod_squareoff`
> is CLEAN and is the REFERENCE implementation** (`cancel_order(entry_broker_id,
> variety="co")` `:1189`).
> ⛔ **NOT a new register item** — A4's own site, counted ONCE. **231 stands.**
> ⛔ Label ceiling: **CO is doubly dormant ⇒ `<VERIFIED LIVE>` is unreachable** without a
> real CO position under a real HARD_KILL. ⛔ #2c-R never touched Monday's critical path.
> Record: `docs/audit/reconciler_product_filter_build_02aug2026.md` §R1–R11.

> **⭐ THIS IS THE ONE OVERLAP BETWEEN §A AND §B. IT IS DEBT-LEDGER RANK #2 AND IT IS
> COUNTED EXACTLY ONCE — HERE.** §B#2 is a cross-reference to this entry, not a second item.

**SOURCE:** S2 §3.3 (Q4 revised, Q6/Q7 closed) · `docs/audit/q4_hard_kill_delivery_decision_30jul2026.md`
· `docs/audit/reconciliation_redesign_design_30jul2026.txt` §D-8 · S3 §XE.3 rank **#2**
(*"Q4/Q7, P7.2(b)"*) · S3 XE.2(f) · S5:103-106.

**THE SPEC, COMPLETE, IN ONE LINE (S2 §3.3):** *restrict flatten + the FIX-181 sweep to
`product in ("MIS","CO")` (from EOD6/FIX-015) · fallback-FLATTEN a NULL product · raise
CRITICAL naming that trade when it does.* ⭐ **The CRITICAL is IN SCOPE of the filter,
not a follow-up** (Q6, Rama 30-Jul 20:30). ✅ **Q9's BL9 trace is DONE (30-Jul)** ⇒ the
filter is the only pre-4-Aug build left.

**WHY IT RANKS #2 IN THE DEBT LEDGER (S3 XE.3):** *"The ONLY item with a hard date and an
ordering constraint that blocks three other workstreams."*

⛔⛔ **THE ORDERING CONSTRAINT — the single most important sequencing rule in the audit
(S3 XE.2(f)):** **the filter MUST land BEFORE anything makes a live component
holdings-aware.** HARD_KILL currently sells a delivery position **on its buy day**, and
the T+1 protection is an **ACCIDENT of holdings-blindness** that any holdings-aware change
destroys. It does **not** bite everywhere — applied term by term in the D-8 design:

| D-8 work | filter-blocked? |
|---|---|
| **step 1** — scope `reconcile_positions` to intraday | ✅ **UNAFFECTED** (it is a RESTRICTION) |
| **steps 2 & 4 · Q1(b) · FORCE_EXIT_ALL · the GTT check** | ⛔ **BLOCKED until the filter lands** |

⚠️ **WINDOW:** *"THE HONEST WINDOW IS THE WEEKEND: Sat 1-Aug / Sun 2-Aug"* (S4:1054).
⛔ **If it does not land, DO NOT RUSH IT MONDAY NIGHT** — it decides a PRODUCT on a live
order ⇒ **careful-loop** (design → review → implement), not an evening patch. **Something
slips instead, and which thing slips is A0.**

---

## A5 — ~~THE 21 UNPUSHED DOCS COMMITS~~ / THE NEXT DEPLOY SLOT ~~`<BUILT, unpushed BY DESIGN>`~~ ✅ **DISCHARGED 04-Aug 19:08 — `main` ~~IS~~ WAS CLEAR, AHEAD 0 *AT 19:08*** · ⛔ **RE-OPENED THE SAME NIGHT: ~~AHEAD 5 at 05-Aug 00:15~~ ⭐ AHEAD 6, RE-MEASURED 05-Aug POST-BOOT — see the block below**

> ### ✅ SUPERSEDED 04-Aug (§G4 — the count below is struck, not deleted; it is what the discharge is measured against)
> **Everything described below shipped**, plus everything built 02–04 Aug, in **one push of 27 commits** (`4149263..0197923`). **PC == origin == VM bare == `0197923`; ahead 0 — ⏱️ *measured AT 19:08 and true only there; the slot re-opened at 23:35.***
> ⛔⛔ **THE BASELINE SHA IN THIS SECTION AND IN THE FLIP PLAN'S §4 GATE 6 IS STALE AND MUST NOT BE RE-QUOTED: `297b587..HEAD` was **99** commits (a 31-Jul SHA). The correct push range was `4149263..HEAD` = **27**.** The operator card's *"expected ahead 26"* was right about the NUMBER and wrong about the BASELINE it was derived from — **the two agreed by coincidence, which is exactly how a stale baseline survives.**
> ⚠️ **D2 caught a real gap before the push, which is the point of D2:** 8 of the 27 commits were **not named anywhere in the unpushed ledger** (`690bdb7` · `04924ca` · `4ae55a6` · `4e1a991` · `919ea9c` · `9a6f345` · `ac5259d` · `8715557`). All 8 verified **docs-only `.md`, 0 `.py`, 0 `.yaml`** ⇒ each passed D1's "does it EXECUTE on flip morning?" test. Written into the ledger **before** the push, per the rule; re-inventory then read **27/27 named**.
> ⚠️ **THE RULE RESETS: `main` is clear, so anything committed FROM HERE rides the NEXT push and ~~first executes WED 08:15 — flip morning itself~~.** Apply the same test: **does it EXECUTE, and is that wanted on flip morning?** *(This register is docs and executes nothing.)*
>
> ### ⛔⛔ THE SLOT RE-OPENED THE SAME NIGHT — ~~**AHEAD 5, MEASURED 05-Aug 00:15**~~ ⭐ **AHEAD 6, RE-MEASURED 05-Aug POST-BOOT. AND THE STRUCK CLAUSE ABOVE IS WHY THIS BLOCK EXISTS: "first executes WED 08:15" WAS TRUE ONLY IF A PUSH FOLLOWED, AND NONE DID — ⭐⭐ WHICH IS NO LONGER A PREDICTION. THE 08:15 BOOT HAS NOW HAPPENED, ON `0197923`, AND NONE OF THE SIX WAS ON IT.**
> ⛔ **WHY 5 BECAME 6 WITHOUT ANYTHING BEING ADDED — the arithmetic, because an unexplained +1 in a deploy ledger is exactly the shape of a commit nobody vetted:** `e64146f` **is the commit that wrote "AHEAD 5"**, at 00:32:43, and a count stated in prose cannot include itself. **Nothing new was authored to make it 6; the counter simply caught up with the writer.** ⭐ **The same fixed point this section already documents for `4603b75` — twice now, which makes it a PROPERTY of prose counts, not an incident.** ⇒ 📌 **the durable form is the COMMAND, not the number: `git rev-list --count origin/main..main`.**
> **The six, newest first, each with the D1 test applied and its first-execution date CORRECTED against the measured clock:**
>
> | SHA | what | ⛔ does it EXECUTE? | status | first EXECUTES |
> |---|---|---|---|---|
> | `e64146f` | this file's 05-Aug 00:32 pass — tonight's four + the clock correction | **NO** — this register; docs only | **`<BUILT — NOT DEPLOYED>`** | n/a (read, never run) |
> | `6112791` | §E — the GOVERNANCE band + the prioritisation rule + the index | **NO** — this register; docs only | **`<BUILT — NOT DEPLOYED>`** | n/a (read, never run) |
> | `c5c1926` | `main.py` — WHY `rehydrate` keys on trade status, **comment only** | **NO — PROVEN, not asserted:** `ast.dump(ast.parse(source))` sha256 **identical** before/after (`e19bccce…a171d21`), and the anti-vacuity half stated too — `main.py` md5 **moved** `9bbc1747…` → `6cff0ce8…`, diff **+27/−0** | **`<BUILT — NOT DEPLOYED>`** | it ships, but there is **nothing for the interpreter to do differently** |
> | `7d4ae6b` | `docs/04_db_schema_reference.md` — the denominator discipline | **NO** — docs only | **`<BUILT — NOT DEPLOYED>`** | n/a |
> | `0087d3a` | `signals/signal_processor.py` — the signal alert's risk line + 8 new tests | ⭐ **YES — the only executing change of the five** | **`<BUILT — NOT DEPLOYED>`** | ~~WED 5-Aug 08:15~~ ⛔ **NOT WEDNESDAY — see below** |
> | `2b6b48e` | this file's 04-Aug currency pass | **NO** — docs only | **`<BUILT — NOT DEPLOYED>`** | n/a |
>
> ⚠️ **WHERE THE EVIDENCE LIVES — a §2(8) DEVIATION, REPORTED NOT RESOLVED.** The documentation rule splits *index here · evidence in a `docs/audit/` build record, cross-linked*. ⛔ **MEASURED: none of the four produced a `docs/audit/` record** — the newest are all from earlier on 04-Aug (`reservation_nonatomicity_latent_or_live_04aug2026.md`, which the schema doc already cites, is the closest kin). ⇒ **for `0087d3a` the Step-1 width, the RED-on-old 7F/1P and the BASE-vs-changed gate SETS exist ONLY in the commit message.** ⭐ **Defensible for three docs/comment commits; thinner for the one that changes executing code** — a commit body is durable and searchable, but it is not where §2(8) tells the next reader to look. **Recorded as a deviation so it is a decision, not an oversight; ⛔ no record is manufactured after the fact to paper it over.**
>
> 🔴 **THE ONE CONSEQUENCE THAT MATTERS, STATED PLAINLY BECAUSE IT INVERTS THE ARGUMENT THAT SHIPPED `0087d3a` AT 23:35:** that commit's stated reason for shipping *now* rather than deferring was **"a misleading risk figure is worse on Wednesday than on any other day"** — flip morning, when the alerts are read hardest. **Unpushed, it does not land there.** ⭐ **VERIFIED AT THE DEPLOYED SHA, not inferred:** `git show 0197923:signals/signal_processor.py` still carries `risk_amt`/`capital_at_risk`/`risk_pct` at **`:453-455`** and renders **`Qty: {qty} | Risk: ₹{risk_amt} ({risk_pct}%)`** at **`:468`** ⇒ ⛔ **WEDNESDAY'S SIGNAL ALERTS WILL SHOW THE OLD, MISLEADING LINE.** The rupee figure is right; **the percentage beside the word "Risk" is the SL distance**, and on flip morning it will sit beside the first delivery entries.
> ⚖️ **NOT PRESENTED AS A REASON TO PUSH.** D3 (**no push before 18:15**) and the flip-day rule that Wednesday gains **no new variables** both point the other way, and the operator-facing damage is a **display** defect that **cannot alter a trading decision the system makes** — which is exactly why it was safe to ship and is equally safe to hold. ⛔ **The decision is Rama's; what is recorded here is that the commit body's timing claim is FALSE as of 00:15, so nobody reads it later and concludes the alert was corrected on flip day.**
> ⇒ **On the current state the ~~five~~ SIX first execute at the NEXT push + the following 08:15 boot** — **THU 6-Aug 08:15** if that push lands Wednesday evening. ⚠️ **Thursday is also the day §C.5's holdings-blindness date bites**, so the slot is not empty of other risk.
> ✅✅ **CONFIRMED BY THE CLOCK, 05-Aug — ⛔ THIS IS NOW A MEASURED FACT, NOT A FORECAST, AND `0087d3a`'S CONSEQUENCE HAS LANDED:** the 08:15 boot ran on **`0197923`** (§A2 BOOT PASS), so **Wednesday's signal alerts DID show the old, misleading `Risk: ₹x (y%)` line** — the SL distance beside the word "Risk", on the morning the alerts are read hardest, which is the exact outcome the commit was written at 23:35 to prevent. ⛔ **STILL NOT PRESENTED AS A REASON TO PUSH:** it is a **display** defect that cannot alter a decision the system makes, **D3 (no push before 18:15) settled it at 08:05**, and flip day gains no new variables. ⭐ **Recorded so that nobody later reads the commit body's "first executes WED 08:15" and concludes the alert was corrected on flip day. It was not.**
> ⚠️ **§2(8) AGAIN, AND CONSISTENTLY: this 05-Aug register pass produces NO `docs/audit/` build record either** — same deviation, same reason (docs-only, nothing measured that a build record would hold beyond what is written here), stated rather than quietly exempted. ⛔ **The deviation at `0087d3a` — the ONE executing change — remains the thin one, and is NOT retro-papered.**

**SOURCE:** memory `UNPUSHED_PENDING_DEPLOY_LEDGER.md` (01-Aug 12:28) · S3 campaign-close.
**WHY-PENDING:** `main` is **21 ahead of `297b587`** — **19 integrity-audit register
commits** (`05595a7` … `4603b75`) **+ 2 calendar commits** (`2b2ea77`, `cbe9fab`) — **ALL
docs-only, deliberately unpushed so Mon 3-Aug boots the regression-tested SHA.** ⛔ **This
register rides the same future deploy slot; commit it locally, do not push.** Push all of
them with the next *real* deploy.

⚠️ **A ONE-COMMIT SELF-REFERENCE, stated so nobody reads it as drift:** S3's own closing
line says *"18 incremental commits (20 total ahead)"*. **Measured 01-Aug: 19 and 21.** The
gap is `4603b75` — *the commit whose whole purpose was correcting that self-referential
count, and which therefore cannot count itself.* **Not an error in S3; a fixed point that
one more commit always moves.**

---

# §B — THE AUDIT FIX CAMPAIGN (the debt ledger — ⛔ NOT STARTED, NOT AUTHORISED)

> ## ⛔⛔ **STATUS: #1 COMMISSIONED BY RAMA 01-Aug (implementation card) — PHASE A ONLY; EVERYTHING ELSE FINDINGS-ONLY, NOT STARTED.**
> S3's own closing words: *"**⛔ No fix work is authorised by this document.** It is a
> register of what is true, measured on 01-Aug-2026 against `297b587`."*
> **This band is a RUNNING ORDER — items move only when Rama commissions them.**
> ⭐ **#1 state 01-Aug night:** `<BUILT — ⛔ NOT DEPLOYED, NOT PUSHED>` — gate cleared,
> Phase B landed as ONE commit `132e571` (42/43 units instrumented; B2 assertion; EOD
> census at `_shutdown()`; regression **NEW-failure set EMPTY**, 8F/5,466P vs base
> 10F/5,452P). Build record: `docs/audit/effect_telemetry_phaseB_build_01aug2026.md`.
> ✅ **B-2 (01-Aug night, `4959111`): the `order_placer` STOP is RESOLVED** — approved
> amendment (place() effect-point + NEW `placer.emergency_exit` dormant tripwire;
> registry 70 entries, MISSING: NONE; census demo clean). Status now
> **`<BUILT — STOP RESOLVED, awaiting deploy slot>`**.
> 🔴 **OWED RAMA:** the **deploy slot** — Mon-eve-with-the-flip vs Tue-eve→Wed boot (§7).
> ⛔ Mon still boots `297b587`. ⏳ **MONDAY OWED (pre-deploy):** PC-paper composition
> boot — B2 assertion PASSES clean — plus a calm-machine regression confirm (the B-2
> gate carries 3 isolation-passing flips in the known-flaky consecutive-losses family;
> build record §6a states it exactly). Scope β/γ only; BK-8 (α) separate.

**THE SEQUENCING S3 MANDATES when it IS commissioned:** **(1)** the buy-day product filter
**FIRST** (A4 — the Q4 ordering constraint, binding across three workstreams); **(2)**
then this ledger's order, which ranks by **change-risk × blast-radius, NOT defect count**;
**(3)** with the standing rules: careful-loop for anything touching capital/kill/orders/
schema/sizing · no schema push except immediately before an off-market boot · no push
before 18:15 · label BUILT/DEPLOYED/VERIFIED LIVE, never "fixed".

## §B.1 — THE 12-ITEM DEBT LEDGER, IN ORDER (S3 §XE.3, verbatim ranking)

| # | debt | why it ranks here | register IDs |
|---|---|---|---|
| **1** | **No effect-verification** (α/β/γ; BK-8 + acted-telemetry) | **Highest leverage in the register:** it created ~22 defects and will create the next one; **one mechanism closes the class** | IA-XARCH-01 · IA-XCFG-01 · IA-XCFG-02 · IA-XTEST-01 |
| **2** | **The buy-day product filter** (delivery liquidation on buy day) | The ONLY item with a **hard date** and an **ordering constraint that blocks three other workstreams** | **Q4/Q7 · P7.2(b)** ⇒ ⭐ **= §A4. SAME WORK, COUNTED ONCE, IN §A.** ✅ **`<BUILT>` 01-Aug night (`43043a2`+`2e606ec`)** — both HARD_KILL sites filtered via the ONE shared source; CNC spared-loud; NULL/NRML flatten+CRITICAL; per-product-row hazard test-pinned; regression NEW-set EMPTY. Record: `docs/audit/buyday_filter_build_01aug2026.md`. 🔴 **NEW RULING OWED (record §2):** the reconciler CHECK2 inflight-orphan flatten = a product-blind THIRD sell-under-kill site (CNC-unreachable pre-flip) — **ride Mon-eve as #2b (~6 lines) or name it a CARRY-PILOT blocker.** ⏳ Mon PC kill drill owed pre-deploy |
| **3** | **Fill/cancel seam truth** (zeroed `qty_filled`; the cancel-race → HUMAN_ORDER) — ⭐ **SCOPE EXPANDED 02-Aug (#2c-R), and this RAISES #3's IMPORTANCE: it now ALSO owns CAPITAL-RELEASE-WHILE-THE-POSITION-IS-LIVE via the CHECK6 route.** Measured: `_check6_orphan_orders` (wired, `main.py:2739`) marks a `PENDING_FILL` trade **FAILED and releases its reservation on the 3rd consecutive cycle** while the position is **still live at the broker**; the caller's `if inflight:` (`order_reconciler.py:930`) then goes False and the same position routes to `_check2_orphan_adoption`, which can file **the system's own position as `HUMAN_ORDER`** — the *same endpoint* as the cancel-race, reached by a *second, independent* path. ⛔ **NOT a new ledger item and NOT an ordering change — #3 stays exactly where it is in §XE.3.** 🔴 **Live relevance, plainly: post-flip a CNC delivery holding SPARED by #2b follows exactly this path.** Reachability is **latent-on-latent** (needs a HARD_KILL **and** an in-flight entry **and** a fill; **HARD_KILL has never fired**) ⇒ **documented, NOT flip-blocking.** ⛔ Bounding belongs to CHECK6 — #2c-R deliberately did not touch it. ⭐⭐ **DESIGN REGISTERED 03-Aug — `docs/audit/ledger3_design_registration_03aug2026.md` (DOCS-ONLY; ⛔ NO implementation, NOT authorised, #3 stays fully GATED).** Six red-team design acceptances, ⛔ **filed as ARCHITECTURE REVIEW — which IS ChatGPT's remit — and deliberately NOT under §G2, which governs RAMA's decisions (deploy slots, gates, ride-or-hold). ⛔ A future reader must not collapse the two.** **(1) ENDPOINT FRAMING ACCEPTED:** IA-P5-02's cancel-race and the CHECK6 route are **two INDEPENDENT paths to the SAME endpoint** ⇒ ⛔ **fixing either alone leaves the endpoint reachable by the other; any design treating them as two separate bugs is wrong BY CONSTRUCTION.** **(2) DIRECTION TO TEST AT STEP 1 — ⛔ NOT a settled mechanism: separate RELEASE-THE-RESERVATION from DISOWN-THE-POSITION.** Today they are ONE action, and that collapse may be the root confusion rather than the release timing; ⛔ **whether they are separable in this system is a measurement NOBODY HAS MADE.** **(3) THE SPLIT: #3a `orders.qty_filled` (standalone, SHIPS ALONE) · #3b the `HUMAN_ORDER` endpoint (BOTH paths together, per 1)** — ⛔ do NOT bundle 3a into 3b just because they share this row; ⛔ do NOT split the two paths inside 3b. ⚠️ **the audit's "~2 lines" is an ESTIMATE, NOT a measurement — every item this campaign touched grew on contact.** ⭐ Width stated (M1): **405 of 805** rows are the wrongly-zeroed population (`qty_filled>0` on **0/405**, `filled_at` on **405/405**); the other **400 are CANCELLED and correctly zero.** **(4) R-4 RULED — FIX-FORWARD BY DEFAULT:** backfilling the 405 historical rows only on operational evidence they are consumed by CURRENT production logic, and then **as a SEPARATE, explicitly approved activity** — it writes the LIVE DB, and ⛔ **a wrong backfill is indistinguishable from correct data afterwards.** **(5) ⛔⛔ R-3 CONSTRAINT — BINDING ON ANY FUTURE CHECK6 WORK: CHECK6's 3-cycle FIX-B is what BOUNDS BOTH #2b's CNC spare AND #2c-R's CO refusal to ~3 CRITICALs — and NOTHING IN EITHER ITEM'S CODE MENTIONS CHECK6** (the bound is emergent from cycle ordering) ⇒ **any CHECK6 redesign MUST explicitly state, for #2b and #2c-R BY NAME, whether alert count, escalation behaviour or refusal semantics change**, so neither shipped item silently regresses. **(6) QUEUE ORDER UNCHANGED — #8b Step 2 → #2d → #3**, asked and answered (a review conclusion is a §0 trigger); ⚠️ **measured while registering: that order had never been written down ANYWHERE** (repo-wide `*.md`+`*.txt`). ⛔ **Registration only — ordering unchanged, no new register row, 231 stands.** ⭐⭐ **#3a STEP 1 `<MEASURED — NOT BUILT>` 03-Aug — record `docs/audit/ledger_3a_9_5_step1_03aug2026.md` §1.** ⛔⛔ **IA-P5-01's PREMISE IS REFUTED: *"no runtime consumer reads the column"* is true of CONTROL FLOW and FALSE of RENDERING.** Two live surfaces read it: **`reports/daily_trade_review.py:508-509` (cron `7 16 * * 1-5`)**, where `orders.avg_fill_price` is the **CONTRACTED PRIMARY SOURCE** for `filled_sl`/`filled_tgt` per **`docs/report_data_contract.md:103`** and is dead **179/179** — every row falls silently through to `trade_slippage_log`, itself gated on the **free-text `exit_reason`** the project forbids branching on; and **`ops_dashboard/frontend/templates/orders.html:38-39`** (`gui-dashboard.service` **active+enabled**), which renders **`0/N`** and **`—`** on every filled order ever placed. ⇒ correct statement: **no logic consumer, two display consumers.** ✅ No capital path — that half of IA-P5-01 holds. ⭐ **MEASURED DAMAGE: 14 report cells all-time** (9 SL + 5 TGT) where a COMPLETE leg existed but BOTH sources are empty — **every one `exit_reason='MANUAL'`**, i.e. the `RMS/MANUAL CLOSE` set. The other ~92% are right **BY FALLBACK** — a contracted primary dead for the system's whole life, unnoticed because a secondary covers the common case. ⭐ **WIDTH RESTATED 405/805 → 413/824** (COMPLETE 413 · CANCELLED 411; `qty_filled>0` = **0 across the ENTIRE table, any status, ever**; `filled_at` 413/413; legs ENTRY 209/SL 108/TGT 71/EOD 25). ⭐ **TWO defects stacked, not one:** the payload is stale (`order_monitor.py:989-992` never writes `final_qty/final_price` back to `entry`) **AND** the writer overwrites unconditionally (`order_manager.py:738-752`) where its sibling columns are `COALESCE`-protected — **which is exactly why `filled_at` is correct: the subscriber synthesises it itself at `:155` and never touches the stale payload.** ⛔⛔ **M4 CONFIRMED — "~2 lines" HIDES THREE THINGS: (i) an HONESTY DECISION** — persist broker truth (`filled_qty/avg_price`, may stay 0/NULL) or the inferred final (`final_qty/final_price`, which substitutes PLANNED qty + EXPECTED price and **commits at the writer the exact error R-4 forbids at the backfill**); **(ii) an ORDERING CONSTRAINT ON A CAPITAL-AFFECTING FIELD** — the assignment must precede `_safe_transition`, but a `False` return exits at `:993-994` **without untracking**, leaving `entry.filled_qty>0` in `_watched`, and `_handle_terminal:1033` branches on that field to emit `OrderPartiallyTerminated` (SL placed, partial booked) ⇒ **the fix CREATES a capital-path edge that does not exist today**; **(iii) THE TEST LAYER IS BLIND BY CONSTRUCTION** — `test_order_placer.py:1613-1621` proves the PARTIAL path persists 4/2505.0 (passes, because `_handle_partial` updates `entry`), while the COMPLETE-path tests `:1623-1670` **publish `OrderStatusChanged` DIRECTLY with correct values, bypassing `order_monitor`** = vacuous for this defect, textbook IA-XTEST-01. ⭐⭐ **R-4 IS NOW EVIDENCED, NOT MERELY CAUTIOUS — the backfill source nobody had looked at EXISTS: `order_execution_log`, 370 rows, `actual_price` AND `filled_qty` NOT NULL on 370/370** (written from `OrderFilled`, which carries the CORRECT values — another IA-XARCH-03 "fill truth ×3 stores" instance found from the far side). ⚠️ Its key is the INTERNAL order id vs `orders`' BROKER id ⇒ **direct join = 0**; the `(trade_id, leg)` bridge gives **108/413 uniquely recoverable · 305/413 no match (log starts 20-Jul) · 0 ambiguous** ⇒ **a backfill would restore 26% and leave a column where `0` means "no fill" on some rows and "unknown" on others, indistinguishable forever.** ✅ **R-4's fix-forward default HOLDS, now on a measurement.** ✅ **PAPER *CAN* EXERCISE #3a — the usual blocker is ABSENT:** `_synth_fill` writes `{"status":"COMPLETE","filled_qty":qty,"avg_price":fill_price}` (`zerodha_adapter.py:2231-2235`) and paper `get_order_history` returns them (`:1127-1142`) ⇒ paper's `_handle_complete` gets real values and discards them identically. ⚠️ **BUT ZERO ARTIFACT: 0/824 VM rows are paper mode; the PC paper DB has 0 `orders` rows total** ⇒ the rehearsal must be **produced**, not assumed. ⛔ **#3a STILL SHIPS ALONE — nothing here touches #3b, the CHECK6 route, or R-3.** ⛔ NOT BUILT, NOT AUTHORISED | ~~Money-path correctness with a **naked-unbooked-position endpoint**; 2-line fix for one half.~~ ⭐ **CORRECTED 03-Aug: the endpoint framing stands, but "2-line fix for one half" does NOT — see the M4 finding above (a decision + a capital-path edge the fix creates + a missing test shape).** Money-path correctness with a **naked-unbooked-position endpoint**. ⚠️ **CROSS-REF ADDED 02-Aug (#8b disclosure, ⛔ NOT fixed, NOT a new row): `scripts/check_vm_state.py --cleanup-pending` (`:102-119`) mass-marks EVERY `PENDING_FILL` trade `CANCELLED` in the LIVE DB with NO capital release and NO audit trail.** `PENDING_FILL` is *precisely* the state CHECK2/CHECK6 arbitrate, so this operator tool can **strand capital reservations and collide with the routing registered to this ledger item** — a trade yanked to `CANCELLED` behind the system's back is exactly the divergence class #3 owns. ⛔ **Unlike `--reset` it has NO sanctioned equivalent to redirect to**, so its correct behaviour must be *decided*, not merely re-pointed. Sibling: `--cleanup-orders` (`:122-148`). **Each needs its own card + authorisation** | IA-P5-01 · IA-P5-02 *(+ the #2c-R CHECK6 route · + the #8b `--cleanup-*` flags — ⛔ **NOT new register rows; 231 stands**)* |
| **4** | **Kill-flatness verification + the fault-injecting fake** | The last-line safety layer is **unverified at kill time** and has **one failed live rehearsal** | IA-P7-01 · IA-P7-02 *(BANSALWIRE — ⚠️ the ledger cites this as "IA-P9/BANSALWIRE"; the BANSALWIRE finding is **IA-P7-02**, `integrity_audit_2026.md:2435-2437`; IA-P9-02:2978 also references it. **Citation flagged, not smoothed.**)* · IA-XTEST-05 |
| **5** | **Broker-truth capital escalation** (G3 non-escalating; the seed absorbs) — 🔬 **`<MEASURED — NOT BUILT>` 03-Aug. ⛔⛔ THE MECHANISM IN THIS ROW IS A MISDIAGNOSIS — SAME SHAPE AS #6. RE-SCOPE BEFORE ANY IMPLEMENTATION.** Record: `docs/audit/ledger_3a_9_5_step1_03aug2026.md` §3. ⭐⭐ **THE LADDER IS NOT DEAF — IT IS NEVER SPOKEN TO.** `capital/drift_handler.py` is **fully wired and correct**: real `kill_switch` injected (`main.py:3364-3369`), tiers clean (₹250/₹1,000/₹2,500, escalate after 3 cycles), DH4 source-scoped, DH6 swallow deliberate. **Nothing inside it is broken.** ⛔ **`fund_manager.sync_from_broker` has EXACTLY ONE PRODUCTION CALL SITE — `main.py:1000`** (width: 41 repo-wide hits = the `def`, docstrings, comments in `fund_manager`/`drift_handler`/`config_loader`, 28 in tests). **Its docstring states what it is actually for (`main.py:966-971`): *"FIX-164 … Fixes the case where the system starts pre-market with stale Rs 0 margins and the user deposits funds after startup but before 09:15."*** ⇒ ⭐ **IT IS A DEPOSIT-CATCHER, scheduled at 09:15 precisely because that is AFTER a manual deposit and BEFORE the market opens — i.e. at the ONE moment of the day when a trading-induced divergence CANNOT YET EXIST** (the 08:15 boot already seeded `_total` from `broker.net`; the first entry cannot occur before 10:00). The drift publish is a **side-effect bolted onto a top-up sync**; the mechanism this row assigns was **never built for that job.** ⚠️ **Second narrowing (`main.py:989-991`): `if wait_sec <= 0: "started after 09:15, skipping re-sync"` ⇒ on any day the service starts after 09:15, FM9 does not run AT ALL that day.** ⭐ **MEASURED, `fm_ledger` all-time: 33 SYNC rows · 0 exceed the `abs(delta) > 1.0` publish gate · MAX abs delta 0.00** — every row identical to the paisa (`9910.40→9910.40`, `9360.00→9360.00`, `9359.80→9359.80`…) ⇒ **the only escalating broker-truth publisher has emitted ZERO events in the system's life — BY CONSTRUCTION, NOT LUCK.** ⭐⭐ **WHERE THE −₹637.6 ACTUALLY WENT (this row's own evidence, relocated): `fm_ledger` INIT 29-Jul `9,997.40` → 30-Jul `9,359.80`. It arrived on the 08:15 BOOT SEED — an `INIT`, which is NOT a drift event and has NO comparison of any kind.** The 09:15 SYNC that same day compared 9,359.80 to 9,359.80 and correctly published nothing. ✅ **Sweep confirms the gap** (width stated: `capital/`, `core/`, `main.py`, `orders/`, `scripts/`, `reports/`): **NO day-over-day seed check exists anywhere** — every INIT consumer reads ONE day (`WHERE date = ?`: `state_store.py:2610`, `preflight/checks/engine.py:120`, `daily_trade_review.py:1031` and `:2158`). ⇒ **#5's REAL TARGET IS THE ABSENT DAY-OVER-DAY SEED CHECK (IA-P6-02)** — a different mechanism in a different place, and where the −₹637.6 was actually lost. ⛔ **Building against this row's current framing would produce work on a component that is not broken.** ⭐ **HAS IT EVER FIRED — NO RUNG, EVER.** The tier function has run on exactly TWO production events, both on 06-Jul: `tier:"HARD", delta:10000.0, expected:0.0, source:"order_reconciler"` — **both logged at INFO and dropped, because DH1 bars that source.** ⛔⛔ **BINDING ON ANY FUTURE #5 WORK: DH1 IS NOT A DEFECT — IT IS THE GUARD THAT STOPPED TWO FALSE HARD_KILLs ON A ₹10,000 PHANTOM** (`expected=0.0` because the FundManager was not yet seeded — the IA-P6-01 startup artifact). **Any redesign that loosens DH1 to let reconciler-sourced events escalate MUST carry this measurement: the only two events that source has EVER produced would BOTH have hard-killed the system incorrectly.** Supporting census: `reconciliation_log` `CAPITAL_DRIFT` = **4,183 rows, tier `UNRECOVERABLE`, 15-Jun → 06-Jul, and silent since.** ⇒ complete firing history: **0 events from the source the ladder trusts, 2 from the source it distrusts, both wrong.** ⛔ The re-scoped item needs its own Step 1; nothing designed here. ⛔ NOT BUILT. 🔴⭐ **SCOPE EXPANDED 04-Aug — THE MEASUREMENT THAT WOULD FEED THIS ROW IS TAKEN ~2,225 TIMES A DAY AND PERSISTED ZERO TIMES.** `get_margins` was called **2,225×** on 04-Aug (`call_start`/`call_end` pairs in `system_2026-08-04.log`) and **its RESPONSE VALUE is never logged** — only method name and `duration_ms`. `zerodha_adapter.py:1454` parses the broker's `utilised.debits` into `used`, consumed live by `fund_manager.initialize`, the reconciler G3 check and the banner; **nothing persists it**, and the `fm_ledger` SYNC row carries the balance only. ⇒ ⛔ **What the broker actually blocked while 04-Aug's two positions were open is GONE and cannot be reconstructed — stated, NOT inferred.** ⭐⭐ **This compounds the row's own finding rather than duplicating it: the ladder is starved (fed once at 09:15), AND the one number that would independently confirm the system's whole capital accounting is fetched constantly and discarded every time.** ⇒ **persisting it (both timestamps: at reserve and at a mid-session sample) is the cheapest thing that would make ANY future divergence detectable at all.** ✅ **Recorded honestly: this row's mechanism was NOT needed to settle the 04-Aug leverage question — direct code + ledger measurement refuted it (see §C.1 G8), which is far stronger evidence than the ladder could ever have supplied. The structural point stands for every FUTURE divergence.** ⛔ NOT BUILT | ~~The kill ladder is **structurally deaf to real cash divergence**; measured **−₹637.6 crossing 3 sessions silently**~~ ⭐ **CORRECTED 03-Aug: the ladder is STARVED, not deaf** (family-β, IA-XARCH-01 — built, wired, correct, **unfed**). The −₹637.6 is real, but it was absorbed by the **boot seed**, which no check compares day-over-day. 🔴⭐⭐ **DH1's GUARD HAS NOW BEEN EXERCISED WITH REAL DELIVERY POSITIONS ON THE BOOK — registered 05-Aug as a sub-entry, ⛔ NOT a new row (231 stands).** Three `CRITICAL — Capital Drift Detected` events fired today (~10:01 → 11:51:20) from **`order_reconciler`**, and **every one was correctly refused escalation.** ✅ **VERIFIED IN SOURCE AT THE DEPLOYED SHA `0197923`** (⭐ `order_reconciler.py` and `drift_handler.py` are **byte-identical** at `0197923` and HEAD, so the cites hold at both): the publish is tagged `source_module="order_reconciler"` (`:3667`); **`_ESCALATING_SOURCES` (`drift_handler.py:66-70`) = `{fund_manager, fund_manager_self_check, fund_manager_bucket_overflow}` and does NOT contain it**; at `:147-161` a non-escalating source writes **one INFO line and RETURNS** — before any tier computation, before the DH4 counter, before `soft_kill`/`hard_kill`. ⇒ ⛔ **NO PATH TO A KILL. Structural, not luck.** ⭐ **THIS IS DH1's THIRD INSTANCE AND ITS FIRST ON A BOOK CARRYING REAL DELIVERY POSITIONS** — the two prior events would both have hard-killed the system *incorrectly*, and this one would have too: **the delta it reported is the deployed capital, not a loss** (see §B#7's fourth member and `docs/audit/ADDENDUM_capital_drift_05-Aug-2026.md`). ⛔⛔ **THIS STRENGTHENS THE BINDING CONSTRAINT THIS ROW ALREADY CARRIES: any redesign that lets RECONCILER-SOURCED events escalate MUST account for this instance too — on a delivery book the reconciler's drift signal is DOMINATED by legitimately-blocked margin, so widening DH1 without first fixing the operand mismatch (§B#7) would convert a correct silence into a correct-looking kill.** ⭐ **Recorded on BOTH rows deliberately, as with G2↔tier — a note on one row is how the other inherits the wrong behaviour.** | IA-P6-01 · **IA-P6-02 (⭐ now the ranking finding, not the sibling)** · **05-Aug DH1 exercise** |
| **6** | **`eod_verify` stuck-PENDING + the inverted shadow flag** — 🔬 **`<MEASURED — NOT BUILT>` 03-Aug. ⛔ THE FINDING IS HALF WRONG, and the wrong half is the one this row called "cheap to fix".** Record: `docs/audit/ledger6_eod_verify_measurement_03aug2026.md`. ⭐⭐ **(i) IS A MISDIAGNOSIS — `PENDING` IS THE FIX, NOT THE BREAKAGE.** `eod_verify.py:10-35` (*HONEST-VERIFY, Audit-B Phase-8 + M-SC1, **07-Jul***): the P&L leg previously queried **NON-EXISTENT columns**, swallowed the `OperationalError` into `pnl_variance=0.0`, and emitted **a FALSE `VERIFIED` that never checked P&L (11/11 historical rows)**. ⇒ **the audit's "last VERIFIED 07-Jul / PENDING from 08-Jul" boundary is the FIX LANDING, not a regression**, and `SUCCESS 32/32` is correct too — **exit 0 is documented for PENDING** (*"job ran; PENDING = known gap until a broker feeder exists"*) ⇒ **a verdict, not a failure.** ⛔ **There is NO stopped finalize path to repair; the condition is a MISSING FEEDER — MEASURED: `reconcile_pnl` appears NOWHERE in `cron_registry.yaml`** ⇒ `pnl_reconciliation` empty ⇒ **`eod_verify` is STRUCTURALLY PINNED at PENDING in LIVE and no change to it can help.** ⚠️ **The "obvious" fix (restore VERIFIED) would REINSTATE THE FALSE VERIFIED 07-Jul deliberately removed.** ✅ **(ii) IS REAL, proven BY CONSTRUCTION:** `ev_clean = (status == "VERIFIED")` collapses a **3-state lifecycle field to a boolean** ⇒ clean day `p1_clean=True`/`ev_clean=False` → **`mismatch=1`**; ISSUES day both False → **`mismatch=0`** — exactly inverted, and it matches the VM rows independently. ⚠️ **DUPLICATED AT TWO SITES (`:291-294` in `persist()` + `:400-402` in the caller) — fixing one leaves the persisted and the logged/alerted flag disagreeing.** ⛔ **Local corroboration NOT possible — the PC DB's `eod_verification`/`eod_broker_reconciliation` tables are EMPTY (stale v44 partial); stated, not implied.** ⏰ **RUNS 15:55 / 15:58 Mon-Fri ⇒ it CANNOT execute tonight — but a change pushed tonight FIRST RUNS TUESDAY, the SHAKEDOWN + FIRST-REAL-CENSUS day, the one day reserved for having no new variables.** ⭐ **UNBLOCKS ONLY PART OF G23: fixing (ii) makes the column HONEST, not GREEN** — with `eod_verify` pinned at PENDING the comparison becomes *"P1 clean, eod_verify NOT CHECKED"* = **cannot compare, not agree** ⇒ **the load-bearing blocker is the BROKER P&L FEEDER, a different item.** (And `eod_broker_reconcile.py:29` says that once P1 is authoritative **`eod_verify` is RETIRED** — the shadow is transitional.) 🔴 **REC: DO NOT RIDE TONIGHT** — first run lands on Tuesday's census day · **18+ trading days of inverted noise in a NON-authoritative column nobody acts on** ⇒ zero urgency · it does not deliver the gate anyway · two-site change. **Better slot: after Wednesday's flip, with the feeder decision.** ⛔ NOT BUILT | ~~**Blocks the authoritative-flip gate outright**; cheap to fix, high unblocking value~~ ⭐ **CORRECTED 03-Aug: cheap — yes; but it unblocks the MEASUREMENT, not the GATE** | IA-P8-01 |
| **7** | **Multi-authority concepts** ("held" ×4, status ×34 sites) — ⭐⭐ **SCOPE EXPANDED 04-Aug: THIS ROW NOW ALSO OWNS THE TWO-PIPELINE COUPLING AUDIT.** ⛔ **NOT a new register row — 231 stands.** It belongs here because every coupling below is the SAME defect: **a query that takes no pipeline dimension.** Record: the 04-Aug two-pipeline measurement. **Rama's target architecture: one command centre, TWO independent execution pipelines; INTENDED-shared = order placement · SL/TGT execution · broker connectivity · DB · logging/alerting · the kill switch's EXISTENCE · scheduling. UNINTENDED = anything where one pipeline's activity changes the other's behaviour or resources beyond the configured capital split.** 🔴 **H1 — THE DAILY-LOSS LIMIT IS GLOBAL AND IT IS THE CLEAREST VIOLATION.** `state_store.py:2543-2546` — `SELECT SUM(pnl_delta) FROM fm_ledger WHERE date = ?` — **no bucket, no product, no direction filter**; `fund_manager.py:1316` — `loss_limit = daily_loss_limit_pct * self._total` (**TOTAL**, not the bucket); the pre-trade twin at `risk_engine.py:593-594` is global too. ⛔ **And the consequence is not a blocked entry:** `fund_manager.py:1330` → `main.py:770-811` runs `eod_instance.fire_now()` **then** `kill_switch.soft_kill()` ⇒ **a DELIVERY loss cancels pending INTRADAY entries, market-closes INTRADAY positions, and soft-kills the day.** ⭐ **Scale-free severity, so it does not depend on the ₹10k test capital: `daily_loss_limit_pct` 3% of TOTAL ÷ `positional_bucket_pct` 30% of TOTAL ⇒ the whole day's loss budget is 10% OF THE DELIVERY BUCKET, at any capital.** With ≤3 concurrent delivery positions (~33% of bucket each), **one position down ~30% on its exit day consumes the entire day's budget.** ⭐⭐ **AND THE TIMING MAKES IT WORSE: `pnl_delta` is REALISED and keyed to the EXIT day** ⇒ a delivery position held N days **dumps its whole multi-day loss into ONE day's budget** — intraday's budget, on a day intraday did nothing wrong. 🔴 **H5 — SHARED PER-SYMBOL STATE, the most under-appreciated one.** `has_active_position` (`state_store.py:846-850`) and `get_active_position_direction` (`:867-872`) filter on **symbol + status only — no product, no date** ⇒ consumed at `risk_engine.py:690` (DUPLICATE_SYMBOL) and `:669-686` (CONTRARY_POSITION). **An intraday and a delivery position on the same symbol are ONE thing.** ⛔ **And because there is no date bound and EOD6 never closes CNC, a delivery position held for N days BLOCKS EVERY INTRADAY ENTRY ON THAT SYMBOL FOR ALL N DAYS — invisibly, because it rejects as `DUPLICATE_SYMBOL`, which looks entirely normal in the record.** 🟡 **H2 — the kill switch gives TWO different answers and they must not be collapsed.** **HARD_KILL flatten is correctly scoped** — `core/constants.py:42` `EMERGENCY_FLATTEN_PRODUCTS = frozenset({"MIS","CO"})`, enforced `kill_switch.py:1775`/`:1906`, CNC survives per Q4, unknown product flattens **loudly** with a CRITICAL ⇒ **a correctly shared safety floor, NOT a coupling.** ⛔ **But SOFT_KILL's entry block is PRODUCT-BLIND** (`kill_switch.py:537` *"block new entry orders; allow exits and monitoring"*) ⇒ **any intraday-caused soft-kill also blocks delivery entries, and vice versa — a leak BOTH ways.** 🟡 **H3 — the pools ARE separate in accounting, over ONE broker account, computed ONCE.** Reassuring half: `fund_manager.py:450/453` seed two pools, `reserve()` `:526/534` draws from the trade's own bucket, `risk_engine.py:441-451` rejects on that bucket ⇒ ⛔ **it is NOT "one pool with a label."** Caveat: **`main.py:2415` is the ONLY `initialize()` call and `fund_manager.py:429` early-returns if already initialised** ⇒ **the split is derived once at 09:15 from the broker balance and never re-derived** — an accounting fiction over one real cash balance (the 29-Jul T2 lesson: *an isolated database is not an isolated account*). ✅ **H4 NOT COUPLED — the strategy governor is per-strategy** (`strategy_governor.py:49` keys on `strategy_name`; `:91-100` and `:104-123` both `WHERE strategy = ?`; `:42` `_paused_today` is a set of names). ⭐ **Day-one edge case checked and CLEARED rather than assumed: a delivery strategy has no history ⇒ `_get_avg_daily_loss` returns NULL ⇒ `:125` coerces to `0.0` ⇒ `:74` `if avg_loss >= 0.0: return False` ⇒ never paused.** ⚠️ Naming mismatch recorded: the module and its config are labelled *"intraday strategy circuit breaker"* (`:2`, `system_config.yaml:630`) but the code is **strategy-agnostic and WILL govern the 3 delivery strategies.** ⚠️ **H6 — the reconciler/EOD blindness is the known #7/F1 class, NOT a new coupling:** `kill_switch.py:618-630` shows EOD explicitly does **not** touch CNC (*"carried by design (EOD6)"*); the T+1 `positions()`-vs-`holdings()` gap is a **visibility** defect, not one pipeline altering the other. 🔴 **H7 — COUPLED BY OMISSION:** `position_sizer.py:302`/`:307` use the delivery-specific value **only `if ... is not None`**, and **both are `null`** ⇒ **delivery is sized by intraday risk appetite at 1× leverage.** ⚠️ **A4 GAP — THERE IS NO DELIVERY ENTRY WINDOW, HOLDING PERIOD OR MAX-HOLD ANYWHERE** (searched the whole config: no `delivery_*` key beyond the four already named). `entry_start`/`entry_end`/`eod_entry_cutoff` are **intraday-shaped concepts the delivery pipeline inherits verbatim** — and a delivery position is held for days. **These do not exist to be flipped; they would be NEW settings.** ⚠️ **`auto_resume_kill_switch: true` (`system_config.yaml:272`) is a DEAD KNOB** — repo-wide (excluding `.git`/`venv`) its only non-doc, non-test consumers are the yaml declaration and the schema field `config_loader.py:467`; **never passed to `EodSquareoff`.** ⛔ **A CONFIRMATION, NOT A DISCOVERY — already registered as IA-P7-04 / P3-c8 (wire-or-delete, OPEN).** | Every future reconciliation/delivery change pays this tax; **the fix template already exists in-repo** — ⭐ **04-Aug: and the tax is now PRICED. ~~H1 and H5 are LIVE from Wednesday's first delivery fill; H2/H3/H7 are structural.~~ ⭐⭐⭐ **SUPERSEDED 05-Aug (§G4) — THE FIRST DELIVERY FILL HAS OCCURRED, SO H1 AND H5 ARE NO LONGER CONDITIONAL OR HYPOTHETICAL: THEY ARE LIVE.** *(2 CNC positions held — §A2 (a); ⚠️ operator-reported, not measured from here, but the transition turns ONLY on a held CNC position existing, not on any figure.)* **H5 IS LIVE RIGHT NOW AND IS OBSERVABLE TONIGHT:** each held CNC symbol is **blocked for INTRADAY entry for as long as it is held** (`has_active_position` `state_store.py:846-850` filters **symbol + status only — no product, no date**, consumed at `risk_engine.py:690`) ⇒ **the rejection reads `DUPLICATE_SYMBOL`, which looks entirely normal in the record.** ⭐⭐ **CHECKABLE TONIGHT: was any intraday signal on a held CNC symbol rejected AFTER the CNC fill? If yes, H5 has its FIRST PRODUCTION INSTANCE — record it.** ⛔ **But a NO is not a refutation** — it means no intraday signal happened to arrive on those symbols; ⭐ **this check can CONFIRM and can never REFUTE, because the block is invisible by construction, which is the finding itself.** **H1 IS ARMED, AND ITS CLOCK IS THE EXIT DAY:** whenever a held position exits, its **ENTIRE** P&L lands on that day's **shared, global** budget — `pnl_delta` is REALISED and keyed to the **EXIT** day ⇒ **a multi-day delivery loss dumps into ONE day's intraday budget, on a day intraday did nothing wrong.** ⭐ **Not today's problem, but now a DATED one rather than a design concern — and the date is unknown because the GTT sets it, not the calendar.** **H2/H3/H7 remain structural.** 🔴🔴 **AND A FOURTH MEMBER REGISTERED 05-Aug AS A SUB-ENTRY (⛔ NOT A NEW ROW — 231 stands): THE CAPITAL-DRIFT TOLERANCE. ⭐⭐ IT DID NOT WAIT TO BE PREDICTED — IT FIRED, THREE TIMES, ON FLIP DAY.** H1 and H5 were registered as *"live from the first delivery fill"*; **this one arrived on its own.** **WHICH OF THE TWO SHAPES IT IS — it is BOTH, and that is why it belongs here:** *(a)* **a governor comparing ACROSS pipelines** — `order_reconciler.py:3590-3591` compares `expected = snapshot.total` (**total capital, GLOBAL, reservations reduce the buckets not the total**) against `actual = margins.net` (Kite's `equity.net`, **net of blocked margin**, `zerodha_adapter.py:1452`; `available.cash` is parsed SEPARATELY at `:1453`) ⇒ **two different quantities, and the difference IS the deployed capital**; and *(b)* ⭐ **a THRESHOLD CALIBRATED ON ONE PIPELINE'S LEVERAGE — the sharper half.** `:3629-3631` sets `effective_tolerance = max(₹50, abs(expected) × 0.10)` in session (`system_config.yaml:368-369`), and **FIX-190 (Bug I)'s own comment says why it exists: *"in-session, broker margin legitimately drops by the deployed capital, so the tight Rs tolerance fires constantly"*.** ⇒ ⛔⛔ **THE 10% BAND IS AN INTRADAY CALIBRATION: at ~5× leverage blocked margin is a FRACTION of position value and stays inside 10%; DELIVERY IS 1× AND BLOCKS THE FULL PURCHASE VALUE ⇒ a delivery book deploying more than ~10% of capital BREACHES A 10% BAND BY CONSTRUCTION — and the positional bucket is 30% OF TOTAL.** ⛔ **No delivery-specific tolerance exists** — width: `capital_drift_tolerance` across every tracked `.py`/`.yaml` → **only a GLOBAL rupee floor and a GLOBAL percentage; ZERO variant hits** ⇒ **H7's *coupled-by-omission* shape, confirmed.** ✅ **NOT A LOSS — the operands explain the delta structurally** (record: `docs/audit/ADDENDUM_capital_drift_05-Aug-2026.md`), and ⛔ **it CANNOT escalate — see §B#5.** ⛔⛔ **NO FIX, NO TOLERANCE CHANGE, NO DELIVERY-SPECIFIC VALUE PROPOSED — it is a MONEY-PATH GOVERNOR and goes through the full careful loop.** ⭐ **Its natural home is the DELIVERY CONFIGURATION SURFACE (§A-DEC-3 order item 5, *"create the SURFACE, do not tune the VALUES"*), which is itself gated on item 4, the Part A inventory annotation.** ⚠️ **AND IT IS THE `ENFORCE + PLACEHOLDER` CELL AGAIN: enforcing a value nobody chose FOR THIS PIPELINE.** **CLASSIFICATION: (a) CONFIRMED DEFECT — and ⛔ NOT LATENT: it is firing.** | IA-XARCH-03 · IA-XDUP-02 *(+ the two-pipeline coupling audit H1–H7 · + IA-P7-04's dead knob, cited not re-registered — ⛔ **231 stands**)* · **05-Aug drift measurement** |
| **8** | **The 03_daily runbook's raw-DB kill-clear** — ✅ **`<BUILT>` 02-Aug (ledger #8, docs-only)** | **Wrong instruction in the most-likely-open doc during an incident**; ~~2 lines~~ ⭐ **THREE DOCS, not one** — the audit scoped only `03_daily`, but a repo-wide sweep found the same raw-DB clear presented as the operator procedure in **`docs/RUNBOOK.md:47`** and **`docs/disaster_recovery.md:164`** too; **all three corrected in ONE commit** (a fix at one site would not have been permanent). All now point at **`deploy/resume.sh`** (LIVE/VM) and **`scripts/clear_kill_switch.py`** (PAPER/PC — `resume.sh` is VM-only), state **why** the raw route is wrong, and keep the old text **struck-through-but-legible**. ⭐ **The `03_daily` "Database Locked" section was the more dangerous half:** its **unconditional** `systemctl restart` HALTS the box whenever a same-day kill coincides — **the COMMON case after 15:15**, since the daily breaker SOFT_KILL is active and persists overnight by design. ⚠️ **NEW DISCLOSURE, reported NOT fixed (it is CODE, out of this card's scope): `scripts/check_vm_state.py --reset` (`:34-40`) performs the SAME raw clear** — no audit trail, stale `triggered_at`, and it bypasses the HARD_KILL `--force` gate. ⛔ Label honesty: a runbook reaches `<DEPLOYED>` when it ships Monday; **there is no `<VERIFIED LIVE>` until an operator actually uses it in an incident.** ⛔ No new register row — **231 stands**. ⭐⭐ **#8b — THE EXECUTABLE HALF — `<MEASURED, FIX GATED>` 02-Aug (Step-1 only; ⛔ NO CODE WRITTEN).** `scripts/check_vm_state.py --reset` (`:34-40`) is the same wrong procedure in executable form. **Worst property: it PRINTS "Kill switch RESET to INACTIVE" while changing NOTHING on a running service** (`is_active()` reads in-memory state) — **and `_persist_state` is `INSERT OR REPLACE`, so the DB edit can be silently overwritten back to killed.** Also: no `system_events` audit · `triggered_at` left stale (audit-integrity only — `clear_stale_state` early-returns on INACTIVE) · **HARD_KILL `--force` gate bypassed entirely** · no `--dry-run` · no already-INACTIVE handling · **ZERO test coverage** · **NO automation caller** (not in `cron_registry.yaml`, no hook/unit/deploy script) · **NO runtime coupling** (zero importers; not reachable from `main.py` — the fact the ride decision turns on). ✅ **THE SECOND-COPY QUESTION IS CLOSED — RULED OUT (Rama ran the VM check 02-Aug): `~/check_vm_state.py` DOES NOT EXIST and no stray copy exists outside the repo, so a repo-only fix IS permanent.** ⭐ Recorded honestly: that **dissolves the stronger half** of the hold reasoning ("a repo-only fix is likely incomplete"); the hold now rests only on "no urgency + Monday's stack is already large" — **still the recommendation, but weaker, and Rama's call.** ~~**RECOMMENDATION: Option A (remove `--reset`) + HOLD out of Monday.**~~ ✅✅ **BOTH RATIFIED BY RAMA 02-Aug ~16:32 — see §A-DEC: R5(a) APPROVES Option A (remove `--reset` entirely; basis = no automation caller · no runtime coupling · no second VM copy · `clear_kill_switch.py` already serves the case in both modes), and R5(b) HOLDS it OUT of Monday's push — implement ONLY AFTER Monday's critical path finishes.** ⛔ ~~**The ride-or-hold question is CLOSED; Step 2 is authorised but NOT YET started, and its trigger is Monday's critical path completing.**~~ ✅✅ **STEP 2 DONE 03-Aug `38f8ab9` `<BUILT — NOT DEPLOYED>` — the trigger fired (Monday's critical path = the `297b587..d6c298d` push, 18:22:51). Rides the NEXT push; the VM is pinned at `d6c298d`, so it does NOT execute on Tuesday's shakedown day.** ⭐⭐ **BUILT AS A LOUD BLOCK, NOT A SILENT DELETION — and the reason is this row's own finding: the flag PRINTED SUCCESS WHILE CHANGING NOTHING, so deleting the branch alone would have preserved that trap in a new form (type `--reset` → kill state prints → exit 0 → read as success).** Now **exit 2 on stderr** naming both sanctioned paths, fired **before the live DB is opened**, matching `--reset` in **any argv position** (the removed code checked only `argv[1]`). ✅ **5 tests (NEW `tests/unit/test_check_vm_state_reset_removed.py`), RED-ON-OLD 4F/1P in a worktree at `d6c298d`, md5-identical both sides — T4 green on old code BY DESIGN (it guards that the read-only diagnostics survived).** ⛔ **`--cleanup-pending`/`--cleanup-orders` deliberately UNTOUCHED and still gated. ⛔ The allowlist is NOT narrowed by this — R12 still owed.** ⛔ The read-only diagnostics (`--signals`/`--symbol=`/`--trades`/`--positions`/`--health`/`--orders`/table+kill display) are legitimate and MUST survive any fix. ⛔ Still **one** debt-ledger item with #8 — **231 stands.** Record: `docs/audit/ledger8b_check_vm_state_step1_02aug2026.md` | IA-XDOCS-01 |
| **9** | **Alert fatigue / false-safety claims** ("Smart TGT ACTIVE", F4, the naked warnings) — 🔬 **STEP 1 `<MEASURED>` 03-Aug; ⛔ the CODE half NOT BUILT, the DOC half `<BUILT — NOT DEPLOYED>`.** Record: `docs/audit/ledger_3a_9_5_step1_03aug2026.md` §2. ⚠️ **FIRST FINDING — the `telegram_alerts` DB table has 0 ROWS and is NOT the trail**; the send-side audit is the `alert_send` log line (`alerts/telegram_notifier.py:398-437`, 27-Jul) ⇒ **exactly TWO full days of structured evidence exist** (it first appears 31-Jul). ⭐ **CENSUS: 31-Jul = 49 alerts (INFO 31 · WARNING 18) · 03-Aug = 84 (INFO 75 · WARNING 8 · `WARN` 1).** Per-signal/per-order INFO dominates — **71 of 84 (85%)** on 03-Aug. ⛔⛔ **ZERO of the non-INFO alerts on EITHER day wrote a sentinel** ⇒ the entire WARNING tier is Telegram-only, no email, dropped silently on failure (TG4) ⇒ **the ABSENCE of a WARNING proves nothing and cannot be recovered after the fact.** ⭐ **STRUCTURALLY UNCONDITIONAL: SIX fire every day regardless of state** (`System Active` · `SOFT KILL — scheduled` · `CIRCUIT BREAKER — Force Close` · `DAILY SUMMARY` · `EOD Clean Shutdown` · `System Stopping`; **2 of the 6 are WARNING-tier**), **plus one that cannot go quiet** — `Sector data-quality: high UNKNOWN rate`, nominally conditional but permanent against the measured 96.6% UNKNOWN input (`instruments.csv` 142/2,228), prescribing a fix the operator **cannot execute at 10:12**. ⭐⭐ **THE HALF THAT DOES DAMAGE — FOUR ALERTS PRESCRIBE AN ACTION AND THREE ARE WRONG: (1) `Naked untracked position` (`order_reconciler.py:1844-1855`) — ⛔ FALSE 5/5.** Its body asserts *"NO protective stop at the broker"* and instructs *"check for a manual order or a system anomaly"*, but the detector is **GTT-blind (IA-P5-06)** and all five 31-Jul firings were **T2's own CNC basket, each with a verified GTT** ⇒ **the claim is false and the prescription sends the operator hunting an order that does not exist — the #8c shape, ALREADY FIRING. (2) `CIRCUIT BREAKER — Force Close` (`main.py:703`): *"EOD squareoff will close ALL positions at 15:17"* and (3) `SOFT KILL — scheduled` (`kill_switch.py:609-615`): *"Open positions: managed to SL/TGT/EOD"* — ⛔ BOTH GO FALSE AT THE FLIP.** Verified at source `eod_squareoff.py:23 / :34 / :1073` — **EOD6 does NOT touch CNC**; a spared delivery leg is deliberately carried. They arrive **together, daily, at 15:15**. ✅✅ **RULED BY RAMA 03-Aug — APPROVED: fix the two strings, ride the flip push. `<BUILT — NOT DEPLOYED>`, first executes WED 08:15 — which is the point: the corrected text is live the first afternoon it matters.** Two string literals + comments; **no logic, no imports, no new code path — `git diff` shows only the two bodies.** Both now read *"…INTRADAY (MIS/CO)…"* + *"Delivery (CNC) is carried by design (EOD6) — not squared off."* ⚠️ **The `kill_switch` body is SHARED by the scheduled (WARNING) and emergency (CRITICAL) paths (`:606-618`); the wording holds for both because SOFT_KILL NEVER FLATTENS** — it blocks entries and lets exits run. ⭐ **Both bodies now name the carve-out in the SAME vocabulary** — previously they made the same wrong claim two DIFFERENT ways, so reading both gave the operator no signal either was wrong. ✅ **Pinned by `tests/unit/test_kill_alerts_delivery_carveout.py` (7 tests), which asserts the CLAIM NOT THE STRING** (no unqualified `"all positions"` · managed set scoped to intraday · carve-out named) — pinning the exact body would go RED on a harmless rewording and GREEN on a reworded-but-still-wrong one. ⭐ **One test asserts the two AGREE: green when both are right AND when both are wrong, RED only when they diverge — the ledger-#8 lesson (a fix at one site was not permanent) encoded as a guard.** ✅ **RED-first BY PLANTING: with the two source files stashed, 3F/4P — the 3 failures are exactly the property assertions; the 4 passes are the "what must NOT change" guards + the agreement test.** ✅ **Gate 8F/5,534P/4skip vs the #8c baseline 8F/5,527P ⇒ +7 = exactly the new tests, NEW-FAILURE SET EMPTY — established by re-running all 8 failures against the stashed base in the SAME session+window (all 8 fail identically), ⛔ NOT inferred from the "known PC-env failures" label.** ✅ Also corrected `test_scheduled_kill_severity.py:32`, which quoted the old body and would have become a lying comment the same night two were removed. ⛔ **`expected_alarms.md` §7 rewritten from `⏳ DECISION PENDING` to the resolved shape, old text struck-through-but-legible.** **(4) `STRATEGY PAUSED` (`strategy_governor.py:154-157`): *"paused for the rest of today"* — CONTRADICTED BY ITS OWN CODE**, `:42` `self._paused_today: set[str] = set()  # in-memory; clears on restart/new day` ⇒ **a restart resumes it**; fired live ×2 on 03-Aug. *(Carried unchanged: 11 `ORDER PLACED` alerts on 03-Aug each claimed `Smart TGT monitoring: ACTIVE (FIXED mode)` against a starved engine — a false CLAIM, same family.)* ⭐ **NEW — THE SEVERITY DISPATCHER FAILS QUIET:** `telegram_notifier.py:386-393` routes `if CRITICAL / elif ERROR / else`, so **anything not spelled EXACTLY `CRITICAL` or `ERROR` lands in the drop-on-failure tier** — no sentinel, no email, no validation, no unknown-severity branch. **Two production sites already ship `severity="WARN"` (`main.py:2311`, `scripts/gemini_log_review.py:284`).** Harmless today (WARN/WARNING route identically) but the classifier deciding LOUDNESS is an unvalidated free-text compare **whose failure direction is SILENCE** — the "never classify by free text" rule on the severity axis. ⚠️ **And the two ends of one path default OPPOSITE: `alert_watcher._build_email` INFERS `CRITICAL` when severity is absent.** ⛔ RECORDED, NOT FIXED. *(Premises checked and CLEARED as non-defects: `ops/control_tower/disk.py:53` + `freshness.py:75` use the Control Tower **Finding** vocabulary, never reaching `notifier.send`; `check_cron_drift.py:145`'s `"OK"` is remapped at `:207`.)* ✅✅ **THE `expected_alarms.md` GAP — MEASURED AND CLOSED SAME NIGHT (docs-only, `<BUILT — NOT DEPLOYED>`).** The doc covered the structural classes but **6 of the 9 non-INFO alerts that fired on 03-Aug — and 5 of 6 WARNING titles across both days — were unlisted** (`Naked untracked position` · `SLIPPAGE GUARD` · `Sector data-quality` · `STRATEGY PAUSED` · `Config Changed Since Last Session` · `EOD SQUAREOFF IN ~30 MIN`) ⇒ ⛔ **set against the doc's own closing rule (*"an unlisted alarm is an incident until proven otherwise"*), it was instructing the operator to treat MOST of a normal day's WARNING stream as an incident — inverting its purpose.** New **§6** (all six, each with the mandatory *"THIS IS REAL IF…"* discriminator + the no-sentinel warning) and **§7** (the flip-day pending decision). ⭐ **PRESCRIPTION STATUS AFTER THE RULING: the 15:15 PAIR is FIXED + `<DEPLOYED>` `4149263`. ⛔ TWO REMAIN OPEN, both CODE, both NOT authorised — ⛔ and they are NOT the same animal.** **(a) `Naked untracked position` — false 5/5, the harder one: a GTT-blind DETECTOR (IA-P5-06), LOGIC not text ⇒ ⛔ it CANNOT ride on the reasoning that carried the 15:15 strings; its own careful loop.** **(b) `STRATEGY PAUSED` — 🔬 STEP 1 `<MEASURED — NOT BUILT>` 03-Aug (record §2.7): ⛔⛔ IT IS NOT A STRING FIX, and the auto-suggested "ship the reword tonight as a literal" was REFUTED BY THE MEASUREMENT ITSELF.** The contradiction originates **in the source, 5 lines apart** (`:4-7` *"rest of the trading day"* vs `:9` *"resets on process restart"*) — the alert invents nothing. **The lifetime is CONDITIONALLY wrong and the condition is the cutoff:** after a restart `_paused_today` is empty, so `check()` re-derives — but the cutoff guard (`:64-65`) returns **BEFORE any P&L computation** ⇒ restart in **[10:00,12:00) RE-PAUSES (self-heals)**, restart in **[12:00,15:00) RESUMES the strategy however large the loss** — **3 of the 5 entry hours.** ⇒ a flat *"clears on restart"* would be **FALSE on the pre-cutoff branch — a NEW wrong statement in the opposite direction**; any honest interim must carry the conditional, which is a two-branch behavioural claim on the SIGNAL PATH, not a literal. ⭐⭐ **THE ROOT IS A PATTERN, NOW SEEN TWICE: ONE GUARD DOING TWO JOBS.** `cutoff_time` conflates *"may I CREATE a pause now?"* (all its config comment says) with *"is this strategy CURRENTLY paused?"* — **exactly #3b's registered *release-the-reservation* vs *disown-the-position*.** ⚠️ **The tell in both: the comment/name describes only ONE of the two jobs**, which is why neither was noticed. ⛔ **Name it as a CLASS on the third instance.** ⚠️⚠️ **REACHABILITY IS UNKNOWABLE, NOT ZERO — and the WIDTHS are the finding.** 7 of 21 off-schedule restarts land in the exposed **[12:00,15:00)** window (21-Jun ×2 · 23-Jun ×2 · 26-Jun · 28-Jun · 01-Jul); pause days are 09-Jul · 14-Jul · 20-Jul · 03-Aug ×2. **The naive intersection is EMPTY and that reading is WORTHLESS: `system_*.log` retention starts 03-Jul, so ALL SEVEN candidate events predate the pause evidence.** ⭐ **The journal keeps 44 days, the logs 32 ⇒ the restart evidence OUTLIVES the pause evidence and they only partially overlap ⇒ the empty intersection is an ARTIFACT OF THE GAP.** Stated with widths: ✅ **03-Jul→03-Aug (33d, full overlap): ZERO exposed restarts, 4 pause days — genuinely established**; ⛔ **21-Jun→01-Jul: 7 candidates, PERMANENTLY UNKNOWABLE.** ⭐ All 7 sit in the **June instability era** (ORPHAN_ADOPTION/G5b/CAPITAL_DRIFT storms were 100% June); **not recurred in 33 days** ⇒ **LATENT**, document + pin, ⛔ not stop-and-fix. ⚠️ **A CORRECTION TO MY OWN MEASUREMENT, RECORDED AS ONE: my first pass reported *"no start after 12:00, ever"* — WRONG, keyed on a line that only exists from 31-Jul (2-day width); the full journal shows 52 starts, 16 at/after 12:00.** ⭐ Caught **only because the width was stated beside the zero** — the number alone would have shipped. ⛔ Also: **`_pause_strategy` writes NO DB row** (log + Telegram only) ⇒ a pause that lifts leaves **nothing to reconcile**, and the 5 counted pauses inherit the same 32-day ceiling; **`is_paused()` is public with ZERO production callers** (dead API). 🔴 **DECISION OWED (no deadline — latent): (a) BEHAVIOUR wrong ⇒ persist the pause + split the guard (signal path, real work) · (b) TEXT wrong ⇒ reword WITH the conditional (cheap, honest, ⛔ enshrines the hole). Rec: (a), with a correctly-conditional (b) as INTERIM ONLY** — a system that documents its own gap and stops there is how ~22 dormant subsystems got their accurate-sounding descriptions. **Warned meanwhile in `expected_alarms.md` §6a/§6d.** ⭐ **CROSS-REFERENCE ADDED 05-Aug (⛔ NOT a new row, ⛔ NOT a fourth prescription, and counted at §C.2 `G18(b)` ONLY): a FOURTH member of this row's false-claim family was BUILT 04-Aug 23:35 (`0087d3a`) — the SIGNAL alert's `Risk ₹X (Y%)`, where the percentage was the SL distance.** ⛔ **It is NOT one of this row's two open items and does not close either of them:** **(a) `Naked untracked position`** (GTT-blind DETECTOR, logic not text) and **(b) `STRATEGY PAUSED`** (a two-branch behavioural claim, not a literal) **both remain OPEN, both CODE, both unauthorised — unchanged by tonight.** ⭐ **The distinction is the useful part: `0087d3a` was shippable precisely because it is the ONLY one of the four that is pure DISPLAY** — no detector, no lifetime semantics, no capital reader — **which is exactly the discriminator this row has been missing when deciding what may ride a push.** | **Degrades the channel every other mitigation depends on** — ⭐ **03-Aug: no longer only volume. THREE live prescriptions are wrong, one already false 5/5, and TWO go false on flip day.** | IA-P9-01 · IA-P9-02 |
| **10** | **Deployed-tree-vs-HEAD unverified** — ✅ **`<BUILT — NOT DEPLOYED>` 03-Aug: the VERIFICATION half is closed.** `scripts/system_manager.py` gains **check 12 `deployed_tree_check`** — the audit's own recommendation (*"the P10.2(b) two-liner as a 12th system_manager check, read-only, nightly artifact, no schema"*), turning **ten manual SHA rituals** into a nightly artifact. It reports **tracked drift** (`diff --stat HEAD`) **and untracked `.py`** — the other half, because ⛔ **`checkout -f` leaves untracked files in place** and an untracked `.py` is importable while being in NO commit (check 11 covers only the `.pyc` slice). ⭐⭐ **STEP 1 FOUND THE DESCRIBED TWO-LINER IS WRONG AS DESCRIBED: `git diff HEAD` needs an index, and the deployed tree has no `.git` of its own. MEASURED — an empty index makes git report every tracked file as deleted: `1250 files changed, 344936 deletions(-)` ON A PROVABLY CLEAN TREE** (a zero-byte index is a *third* behaviour: `fatal: index file smaller than expected`), **and using the REAL index would have a monitoring job WRITE the deploy repo's index.** ⇒ the correct form **copies the index and diffs against the copy** — proven both ways (clean⇒empty · planted change⇒detected · real index mtime UNCHANGED), and both wrong forms are **pinned by test** so the copy step cannot be silently "simplified" away. ⛔⛔ **SAFETY: it NEVER sets `soft_kill_reason`** — `system_manager` *can* trip tomorrow's SOFT_KILL (`:1144-1145`), so this was kill-adjacent, not additive; the property holds **by construction** (opt-in per check) and is **test-asserted**. Report-don't-block is the right side of the discriminator because the failure class (partial checkout, stray file, hook drift) is **environment-caused**. Every failure path (no git dir · no index · rc≠0 · 30 s timeout · OSError) **degrades to a WARNING, never a violation.** ✅ **11 new tests, RED-on-old 11 FAILED at `f1972ec`** (base worktree, test md5-identical both sides); ⭐ they build **REAL git repos**, not a mocked `subprocess` — a mocked seam would assert only what we told it (practices §V4, re-earned this same morning). ✅ **Composition smoke test on the live tree correctly caught its own uncommitted drift + the untracked test file, `soft_kill_reason=None`, index mtime unchanged.** Regression **NEW-set EMPTY** (+11 collected = exactly the new tests). ⛔ **CLOSES VERIFICATION, NOT ENFORCEMENT** — nothing here *prevents* a divergent tree, it makes divergence VISIBLE nightly; enforcement stays open. ⛔ **Not `<VERIFIED LIVE>`: its first real run is on the VM against the real bare-repo layout, which no PC test can stand in for.** ⚠️ It also answers *"does the tree match HEAD"*, **not** *"is the running PROCESS that code"* — a service started before a push keeps old code in memory. Record: `docs/audit/ledger10_deployed_tree_check_03aug2026.md` | The invariant **every phase's premise rested on**, held by ritual | IA-P10-01 |
| **11** | **Secrets concentration** (5 accounts in one `.env`; VM test path) — ⭐ **SCOPE EXPANDED 02-Aug (#8b): this row now also carries AUTHORISATION-SURFACE concentration.** `.claude/settings.local.json` → `/permissions/allow` holds **21 `check_vm_state` entries — 2 × `--reset` plus both `--cleanup-*` flags** ⇒ the allowlist **pre-authorises an AI agent to run destructive mass-mutation commands against the LIVE trading DB WITHOUT prompting.** ⛔ **Removing `--reset` from the script does NOT narrow the allowlist** — the two are independent surfaces, which is why this sits here and not inside #8b. ⚠️ **It is also STALE: eleven of those entries point at `~/check_vm_state.py`, a path Rama's 02-Aug VM check proved DOES NOT EXIST.** 🔴 Needs Rama's review — see §C.4 **R12**. ⛔ **NOT a new register item — an authorisation-surface facet of THIS row, counted ONCE here; 231 stands** | Multiplies consequence **5×** for zero benefit; constrains how tests may evolve | IA-XSEC-01 · IA-XSEC-02 *(+ the #8b allowlist facet)* |
| **12** | **Doc/knowledge concentration** (map inversion; register-as-reference) — ✅ **IA-XDOCS-05 CLOSED `<BUILT — NOT DEPLOYED>` 03-Aug: `docs/expected_alarms.md` (NEW), cross-linked from `05_incident_response.md`.** The audit called it *"the highest-value single doc the system currently lacks"*. It covers **all five** enumerated coverage gaps (XDOC.1e), not the four the recommendation scoped: the **15:15 breaker's 4-line CRITICAL chorus** (⭐ *three severity vocabularies on ONE routine event* — log CRITICAL ×4 vs Telegram WARNING vs no sentinel) · the **post-schema-push refuse-window** · **delivery/T2 artefact alerts** (`Orphan GTT`, `MISSING_AT_BROKER`/`ORPHAN_AT_BROKER`) · the **exit-3/4 alert-vs-silent matrix** *including both silent windows* (the post-16:00 HALT gap and the 16:00→17:35 unwatched 95 min) · **plus a 5th section the audit could not have: the alarms THIS PUSH ADDS** (`(shadow)` mis_filter · `SPARED delivery position` · `UNKNOWN PRODUCT` · `INFLIGHT_ORPHAN_*` · check 12's EOD line). ⛔⛔ **DESIGNED AGAINST ITS OWN FAILURE MODE: it lowers the ALARM, never the CHECK — EVERY entry carries a mandatory *"THIS IS REAL IF…"* discriminator**, because a doc that says "these are fine" is dangerous exactly when a real one arrives. ⭐ Anti-duplication measured first (G3 sibling): **no operator doc listed expected alarms — the only hit repo-wide was THIS REGISTER, which IS the finding.** Evidence used: the audit's own quantification — **31-Jul, a healthy profitable day with ZERO real incidents, emitted 49 alerts (18 WARNING)**. ⛔ **IA-XDOCS-03 (map inversion) is NOT closed and was NOT attempted** — its recommendation is *"generate from docstrings; the restructure is owned"*, i.e. **P4-6, an open DECISION (Rama's), not a chore**; `SYSTEM_MAP.md`'s own header says do not split it silently. ⭐ **SCOPE EXPANDED 03-Aug: this row also carries THE STALE ARTIFACT THAT IS STILL BEING CITED.** `mempalace.yaml` sits at `as_of: 2026-04-27` / `head_commit: 16e1595` / `schema_version: 13` (live: **v45**) — ~3 months cold — yet it is named in the **memory-update directive of every card in this campaign**, so cards have been instructing updates to a file nobody maintains. ⚠️ **UNTRACKED, git-ignored (`.gitignore:82`)** ⇒ no history, no review, PC-local only. ⭐ **It contradicts the tracked record in the dangerous direction:** `g2c_vm_deploy` = *"push HELD pending Rama ordering call"* vs `SYSTEM_MAP.md:282` = **DEPLOYED, merged `7b1c92d`** (verified real, 03-Jul 17:23:34) — **the M3 class inside our own process.** 🔴 **RAMA'S CALL — §C.4 `R13`: retire it, or maintain it.** ⛔ **Registered, NOT decided and NOT fixed here.** ⛔ **NOT a new register item — counted ONCE here; 231 stands** | Slows every future change; **no incident hazard** — ⚠️ **but the stale-artifact facet carries a TRUTH-TELLING hazard the original row did not: a cited document that is WRONG is worse than one that is absent, because it is read as current.** 🔴🔴 **AND A THIRD FACET, REGISTERED 05-Aug AS A CROSS-REFERENCE ON THIS ROW (⛔ NOT A NEW ROW — 231 stands): THE DEPLOY LEDGER THAT GATES EVERY PUSH IS NOT IN ANY GIT REPOSITORY.** **MEASURED:** `git rev-parse --show-toplevel` inside the memory directory → **`fatal: not a git repository`**. ⇒ `UNPUSHED_PENDING_DEPLOY_LEDGER.md` — **the artifact the D2 SHA-inventory gate READS before every push, and which caught 8 unnamed commits on 04-Aug and 2 more on 05-Aug** — is **untracked · unhistoried · unreviewable · PC-local only.** **FOUR CONSEQUENCES, each independent:** (1) **it cannot be diffed**, so a regression in it is invisible; (2) **it cannot be recovered if the PC is lost**, and it is the only record of what is built-but-unpushed; (3) **no review can ever run over it** — every other gate artifact in this campaign is reviewable and this one is not; (4) ⛔ **there is no way to NOTICE it silently regressing** — the same absence-of-signal shape as the token file and the 95-minute unwatched window. ⭐⭐ **AND THE PRECEDENT IS EXACT: this is the SAME SHAPE AS `mempalace.yaml` (R13/D5), which was RETIRED 04-Aug precisely for being untracked and stale — on an artifact that is FAR more load-bearing than the one that got retired.** ⛔ **NOT MOVED, NOT TRACKED, NOT RESTRUCTURED — where it lives is a decision with a blast radius (it may be deliberately outside the repo, and the memory directory has its own loading semantics).** 🔴 **DECISION OWED TO RAMA.** **CLASSIFICATION: (a) CONFIRMED DEFECT.** *(Found 05-Aug when TASK 1.6's "commit it locally" turned out to be unexecutable — the blocked task was the symptom, this is the cause.)* | IA-XDOCS-03 · IA-XDOCS-05 · **05-Aug ledger measurement** |

**⇒ 22 distinct IA findings are ranked into the ledger** (rank #2 cites no IA finding —
it is the pre-audit Q4/Q7 item, §A4).

### 🔴 SUB-ENTRY ON ROW 7 (H5) — 05-Aug-2026. ⛔ **NOT A NEW ROW. 231 STANDS.**

> ⛔ **ROW 7 IS CURRENTLY THE SOURCE OF A WRONG SEARCH STRING, AND IT COST NOTHING TO FIND
> ONLY BECAUSE THE CHECK WAS RUN THE OTHER WAY ROUND.**
>
> *Placed here rather than inside row 7 because that row is one enormous single-line table
> cell; appending inside it risks breaking the table. Cross-referenced, not relocated.*
> ⚠️ **M3 instance recorded in passing:** this claim was measured at `:763` and sits at
> **`:832`** at HEAD — the item-4 sub-entry shifted it by exactly **+69 lines**.

**Row 7 says** a held delivery position blocks every intraday entry on that symbol *"invisibly,
because it rejects as `DUPLICATE_SYMBOL`"*, and asks: *was any intraday signal on a held CNC
symbol rejected AFTER the CNC fill? If yes, H5 has its FIRST PRODUCTION INSTANCE.*

| the claim | verdict | evidence |
|---|---|---|
| mechanism: `has_active_position` filters **symbol + status only — no product, no date** | ✅ **CORRECT** | **(S)** `state_store.py:839-853`, read verbatim 05-Aug |
| consumed at `risk_engine.py:690` as `DUPLICATE_SYMBOL` | ✅ **CORRECT** | **(S)** |
| a delivery hold blocks intraday on that symbol **for all N days** | ✅ **CORRECT** | **(S)** |
| ⛔ **the code you will see is `DUPLICATE_SYMBOL`** | 🔴 **WRONG FOR THE FILL DAY** — right from day 2 | **(P)** 05-Aug: **6 × `SYMBOL_DIRECTION_DAILY_LIMIT`, 0 × `DUPLICATE_SYMBOL`** |

**Why:** a **second, earlier, also product-blind** gate shadows it. `signal_processor.py:693`
*(deployed `0197923`; `:717` at HEAD — M3)* raises `SYMBOL_DIRECTION_DAILY_LIMIT` **before**
`risk_engine` is ever reached.

> ### 🔴 THEREFORE H5's OWN MECHANISM HAS **NOT** HAD ITS FIRST PRODUCTION INSTANCE. **STILL LATENT.**
> **(P) CONFIRMED:** a **product-blind** gate blocked intraday signals on a symbol a CNC fill had
> claimed — real, observed, six times. **The coupling CLASS is confirmed.**
> 🔴 **(S) NOT H5's MECHANISM:** `has_active_position` was never reached; the pipeline rejected
> upstream. ⛔ **Those two facts must travel together.**

**⚠️ THE RISK ROW 7 CREATED, STATED PLAINLY:** an operator following it greps `DUPLICATE_SYMBOL`,
gets **zero**, and records *"no H5 instance"* — **while a real product-blind block DID happen.**
⭐ **A live instance of `V5`** (a check with no failing input available to it manufactures
confidence) **and of the wide-check rule.** ✅ What saved it: enumerating the actual
`rejection_reason` values instead of searching for the predicted ones.

> ### ⏰ **THE DATED, FALSIFIABLE PREDICTION — THU 06-Aug**
> **(S)** the two gates have **different reset semantics**, both verified 05-Aug:
> `SYMBOL_DIRECTION_DAILY_LIMIT` counts via `SUBSTR(created_at,1,10) = today`
> (`state_store.py:707-725`) ⇒ **resets at midnight**; `has_active_position` keys on
> `status IN ('PENDING_FILL','OPEN','PARTIAL')` with **no date** (`:839-853`) ⇒ **does not reset
> while the position is OPEN.**
> **(I)** ⇒ **IF ATULAUTO IS STILL HELD ON 06-Aug AND ANY INTRADAY SIGNAL ARRIVES ON IT, THE
> REJECTION CODE SWITCHES TO `DUPLICATE_SYMBOL`** — H5's genuine first production instance.
> ⛔ **If no intraday signal arrives, the result is NOT DETERMINABLE — not a refutation.**
> ⛔ **Never observed. This is arithmetic over two verified predicates, not a measurement.**

⛔ **Nothing is fixed here and no fix is implied.** Row 7's ranking, mechanism and multi-day claim
all stand unchanged; **only the expected code name is corrected, and only for day 1.**
🔗 Measurement record: `docs/audit/HANDOFF_05-Aug-EVENING.md` §8.

## §B.2 — THE 73 FINDINGS **BELOW** THE LEDGER LINE — ⛔ ACCOUNTED FOR, NOT LOST

**S3 carries 95 finding IDs.** 22 are ranked above. **The remaining 73 are not
unimportant — they are un-*ranked*:** the ledger ranks by change-risk × blast-radius, so a
correct-but-narrow LOW sits below a broad MED. ⛔ **They are cited here by phase, exactly
as G20's ~55 line items are pointed at rather than re-enumerated. `docs/audit/integrity_audit_2026.md`
is the authority for every one of them.**

| phase | finding IDs | in-ledger | below-line |
|---|---|---|---|
| **P1** signal ingress | IA-P1-01 … -09 | — | **9** |
| **P2** screening | IA-P2-01 … -08 | — | **8** |
| **P3** sizing/risk | IA-P3-01 … -06 | — | **6** |
| **P4** order construction | IA-P4-01 … -05 | — | **5** |
| **P5** broker/execution | IA-P5-01 … -10 | -01, -02 | **8** |
| **P6** capital/state | IA-P6-01 … -07 | -01, -02 | **5** |
| **P7** kill/safety | IA-P7-01 … -05 | -01, -02 | **3** |
| **P8** reconcile/EOD | IA-P8-01 … -05 | -01 | **4** |
| **P9** reports/alerts | IA-P9-01 … -03 | -01, -02 | **1** |
| **P10** recovery/deploy | IA-P10-01 … -05 | -01 | **4** |
| **X-ARCH** | IA-XARCH-01 … -04 | -01, -03 | **2** |
| **X-DUP** | IA-XDUP-01 … -05 | -02 | **4** |
| **X-CONFIG** | IA-XCFG-01 … -04 | -01, -02 | **2** |
| **X-DOCS** | IA-XDOCS-01 … -05 | -01, -03, -05 | **2** |
| **X-TEST** | IA-XTEST-01 … -05 | -01, -05 | **3** |
| **X-SEC** | IA-XSEC-01 … -05 | -01, -02 | **3** |
| **X-EVOLVE** | IA-XEVOLVE-01 … -04 | — | **4** |
| | **95 total** | **22** | **73** |

⭐ **BELOW-THE-LINE ITEMS THAT NEVERTHELESS CARRY A DATE OR A GATE — flagged so the
ranking does not hide them:**
- **IA-XCFG-04** — the flip's clean bill (§A2). *Recorded so 4-Aug does not re-derive it.*
- **IA-P8-04** · **IA-P5-06** — the noise the flip **will** produce (§A2). *Expect them; they are not findings on the day.*
- **IA-XEVOLVE-04** — the delivery expansion's own assessment: **MED with the filter, HIGH without it.**
- **IA-XEVOLVE-03** — ⛔ the operational rule it yields: *no new code path that can place an order should be exercised on the VM until the fault-injecting fake exists and the crash-test exclusion is explicit.*
- **IA-P10-02** — **v46 (or any schema bump) repeats the 27-Jul refusal night BY CONSTRUCTION.** Binding on every future schema push.
- **IA-XEVOLVE-02** — 📌 **carries R1's deferred-edge note into the verdict verbatim** (§C-R1n).

## §B.3 — 📌 THE VERDICT'S ONE-SENTENCE FRAME (⛔ quote it, do not re-derive it)

> **"The system has no mechanism that verifies a declared thing actually has an effect."**
> ~22 subsystems and knobs are configured, built, often constructed and started — and
> **inert**. They pass every test because **tests construct their own objects and pass the
> arguments production forgot.** This one gap explains **roughly half** of everything the
> campaign found.
>
> **"This system's integrity problem is a truth-telling problem, not a correctness
> problem"** — a far better problem to have, and a far easier one to fix.
>
> **"Worth and powerful, not a toy" — YES, as engineering, with one caveat that must not
> be softened: R1 stands. The system shows NO MEASURABLE PROFITABLE EDGE.** ⇒ *a
> well-built machine that is not yet profitable, whose defects are overwhelmingly of the
> "declared-but-inert" class rather than the "wrong when it runs" class.*

---

# §C — PRE-AUDIT REGISTER ITEMS STILL OPEN (from S1 / S2)

> ⛔ **These are POINTERS with a why-pending and a cross-link. S1 and S2 own their full
> text.** Where an audit finding now SUBSUMES or SHARPENS one, it is linked here **so the
> item is not tracked twice.**

## §C.1 — MONEY PATH (⛔ careful-loop; blocks the sizing thread)

| id | item | why-pending | source | ⭐ audit cross-link |
|---|---|---|---|---|
| **G2** | B2/M-S4 — 25 of 100 scorer points are a constant `0.0` — 🔴⭐ **DEPENDENCY REGISTERED 04-Aug: G2 ↔ THE TIER LADDER. THIS IS THE ONE THAT MUST NOT BE MISSED.** | needs re-parity + re-soak; coupled to R3/D2 — ⛔ **AND NOW COUPLED TO SIZING: G2 MUST SHIP WITH A TIER DECISION OR IT SHIPS A DOUBLING** | S1 §4, S2 §5-B2 | **SHARPENED** — S3 P2.4 re-measured fresh: **25/100 dead-at-0.0 on 16,705/16,705 window rows + 20 more points pinned at half ⇒ 45/100 degenerate**; and **IA-P2-03** (tier ladder degenerate: ~~415/415~~ ⭐ **RE-MEASURED 04-Aug: `tier_weight_applied = 0.5` on 438/438** trades ran at the LOW 0.5 multiplier). ⭐⭐ **THE COUPLING, STATED PLAINLY: only LOW is reachable because the score ceiling sits ABOVE what the scorer can reach — the dead 45/100 is WHY the tier is always 0.5.** ⇒ **fixing G2 would silently DOUBLE position size on every trade that newly reaches a higher tier** (`tier_multipliers` HIGH 1.0 / MEDIUM 0.70 / LOW 0.50, `system_config.yaml:178-181`; applied `raw_qty × tier_multiplier`). ⛔ **G2 is therefore NOT a scoring-only change — it is a CAPITAL-PATH change wearing a scoring label.** ⭐ **And record what the tier ladder IS today: not a dormant control but a PERMANENT 50% HAIRCUT wearing the label of a three-tier conviction ladder.** ⇒ **it is also half of the ~7% buying-power utilisation measured at G8.** ⛔ **Recorded on BOTH rows (here and §C.2 G18), deliberately — a note on one row is how the other inherits the wrong behaviour.** |
| **G3** | sector **resolution** (81/82 populated rows read `UNKNOWN`) | money path ⇒ own review + prediction + deploy. **Hard prerequisite of any sizing increase.** Blocks R2/D1 — ⚠️ **AND NOW: fixing G3 ALONE changes nothing, see the stacked defect** | S1 §4, S2 §5-B2 | **WIDENED** — **IA-P3-06(a)**: a **second dead consumer** found (`_sector_for`, `signal_processor.py:1389-1401`, probes methods that **do not exist**); engine-side resolution is **fully silent** (0 warning lines ever). Status moved: **4 real-sector trades now, not 1**. 🔴⭐ **NEW 04-Aug — A SECOND, INDEPENDENT DEFECT IS STACKED ON THIS ONE, AND EITHER FIX ALONE IS INERT: `max_sector_exposure_pct` IS STRUCTURALLY UNREACHABLE — A UNIT MISMATCH.** It measures **MARGIN** (`state_store.py:820` sums `margin_reserved`; gated `risk_engine.py:638` against `max_sector_pct × snap.total`) while the two caps above it measure **NOTIONAL** (`max_concentration_pct` `position_sizer.py:423-424`; `max_position_value_pct` `:137`). **Scale-free proof: 5 positions × 10% notional = 50% of capital notional ⇒ 10% of capital in MARGIN at 5×, against a 40% margin ceiling ⇒ it would need ~20 concurrent positions to bind, FOUR TIMES `max_open_positions: 5`.** ⇒ ⭐ **this independently explains why the 04-Aug census reads `risk.sector_cap_bound: acted 0` and why IA-P3-05 called the soak "structurally eventless" — the cause is a UNIT MISMATCH, not sector diversification** — and ⛔ **flipping `sector_cap_mode: observe → enforce` today would change NOTHING.** ⚠️ **TWO DEFECTS STACKED: G3 (81/82 UNKNOWN inputs) + the unit mismatch (an unreachable threshold). Fixing either alone leaves the cap inert.** ⚠️ **And note for the delivery template: at CNC 1× leverage notional and margin are IDENTICAL, so these three caps will behave DIFFERENTLY for the two pipelines even with identical values.** |
| **G8** | ~~the leverage gap (the system sizes UNLEVERED)~~ ⛔⛔ **PREMISE REFUTED BY MEASUREMENT 04-Aug — THE SYSTEM IS MARGIN-AWARE. The struck text is kept legible (§G4) because it is what the measurement is evidence AGAINST.** | ~~the **recalibration** is the work, not the multiplier~~ ⭐ **RE-SCOPED: there is no leverage gap to close. The open work is the CONCENTRATION/TIER throttle below — a different lever in a different place.** | S1 §4, S2 §5-B2 | ⭐⭐ **MEASURED 04-Aug, and it produces the disputed number EXACTLY.** **(1) THE LEVERAGE HYPOTHESIS IS REFUTED.** `fund_manager.py:262-263` — `leverage = leverage_map.get(intent, 1.0); return (qty * price) / leverage`, docstring `:250-251`: *"Compute required margin = qty * price / leverage. **NOT notional (audit catastrophic flaw fix: FM4)**"* — **this exact bug was found and fixed, and the fix is labelled.** `reserve()` `:527-532` adds only the 5% `slm_margin_buffer_pct`. ⭐ **The 5× facility is ALREADY IN USE — no config value needs raising to unlock it, and delivery at 1× is correct by the same path.** **(2) THE ₹149.92 RECONCILIATION — exact, and it overturned its own premise:** the question assumed DHAMPURSUG qty=1; **it was qty=3.** `SUM(trades.margin_reserved) = 149.9199` (`db_reader.py:613`) = SCI `60.84909` (=304.24545÷5) + DHAMPURSUG `89.07084` (=148.4514×3÷5). ⇒ **it IS order value ÷ leverage; the arithmetic was right and the quantity was wrong.** *(The ledger COMMIT rows sum to 149.806 instead — `margin_reserved` uses the TARGET price, COMMIT recomputes at the ACTUAL fill. Two correct figures measuring different instants.)* **(3) ⛔ THE RISK-BASE QUESTION IS INERT — this is the finding that moots the proposal.** Per-row over every trade with a persisted breakdown: **binding candidate = CONCENTRATION on 438/438**; rows where **risk** was strictly the min = **0**; rows where **capital/affordability** was strictly the min = **0**. ⭐ **Verified STRUCTURALLY, not by the free-text label — the recomputed `min()` from `qty_by_risk`/`qty_by_capital`/`qty_by_concentration` agrees with `binding_constraint` on all 438** (the "never classify by free text" rule applied to our own evidence). ⇒ **changing what `risk_per_trade_pct` multiplies would have changed ZERO of 438 trades**, and the buying-power half is moot too — `qty_by_capital` has never bound either. **(4) ⚠️ THE BASE IS `total_capital`, NOT THE BUCKET — `position_sizer.py:381` `risk_rs = total_capital * eff_risk_pct`, `:293` `total_capital = snap.total`. ⛔ Strike the "₹7k / the bucket" claim wherever it appears; it was asserted twice and never measured.** ⛔ **AND THE PROPOSED MOVE COLLIDES WITH AN EXISTING LIMIT, as a ratio so it holds at any capital: risk 1% of a levered-bucket base = 1% × (70% × 5) = **3.5% of TOTAL per trade**, against `daily_loss_limit_pct` = **3% of TOTAL** ⇒ ONE stopped trade would exceed the ENTIRE day's loss budget and fire `eod.fire_now()` + `soft_kill()` on the first loss of every day.** ⇒ **if the intent is "risk more per trade", raise `risk_per_trade_pct` explicitly — the base change RELABELS risk, the percentage change DECLARES it.** 🔴 **(5) THE REAL THROTTLE, and Rama's instinct that the system under-deploys is CORRECT by roughly an order of magnitude:** concentration **10% notional** × tier **0.5** × **5 positions** ⇒ **~25% of capital deployed against ~350% of buying power (70% × 5) ⇒ ~7% utilisation.** *(Approximate — lot/floor effects blur it.)* **The levers are `max_concentration_pct` and the degenerate tier ladder, NOT the risk base.** ⚠️ **IA-P3-04 is CONFIRMED by this measurement** (the risk path cannot bind). ⚠️ **IA-P3-01's "hard PRICE CEILING ~₹936–1,000" framing is WITHDRAWN by Rama 04-Aug as a small-capital artifact — at production capital a ₹590 share is a rounding error; the scale-free restatement is a max % of the bucket per position.** ⛔ **Qty-1 rounding is a small-capital SYMPTOM, not an architectural defect.** |

## §C.2 — KILL / CAPITAL / SAFETY (⛔ careful-loop; gated after 4-Aug or the first live kill)

| id | item | why-pending | source | ⭐ audit cross-link |
|---|---|---|---|---|
| **G11** | K1 · K2 · K3 — emergency kill + same-day restart ⇒ service does not come back | after 4-Aug. *(Docs already fixed; the **BEHAVIOUR** half is what is open)* | S1 §4, `ACTIONS_not_decisions` | ⚠️ **K1's DOC half is NOT fully closed** — **IA-XDOCS-01** (§B#8): the fix **MISSED a third site**, `03_daily_operations_runbook.md:133-139`, which still prescribes a **raw live-DB kill-clear + restart**. **SEVERITY HIGH** — it lives in the DAILY-operations doc |
| **G13** | first live HARD_KILL · Phase-1 replay · M-C1 non-zero carryover | needs a real mid-day restart with live state | S1 §4 | **STANDS, composed into IA-P7-02's posture line** — *"the G13 drill was mocked"*. See **IA-P7-01** (§B#4): the flatten counts success at **order ACCEPTANCE**, hard-codes `succeeded = attempted` |
| **G18** | the 10 capital/kill MEDs (M-C4·M-C5·M-C6·M-C8·W10·P3-c6…c10) — 🔴⭐ **THE SECOND HALF OF THE G2 ↔ TIER DEPENDENCY IS REGISTERED HERE, DELIBERATELY** | careful-loop, ~~after 4-Aug~~ ⭐ **after WED 5-Aug (R2)** | S1 §4 (10 named sub-items) | M-C6 + FIX-133 **re-confirmed latent** (S3 P3.4): effective_mult = 0.5 always ⇒ both branches unreachable — ⭐⭐ **RE-MEASURED 04-Aug: `tier_weight_applied = 0.5` on 438/438, so M-C6's "latent" is CONFIRMED and its CAUSE is now named: only LOW is reachable because G2's dead 45/100 keeps every score below the higher tiers' thresholds.** 🔴 **THE DEPENDENCY, RECORDED HERE AS WELL AS AT §C.1 G2 SO NEITHER ROW CAN INHERIT THE WRONG BEHAVIOUR: fixing G2 unpins the tier and SILENTLY DOUBLES position size on every trade that newly reaches a higher tier. G2 must ship WITH a tier decision, or it ships a doubling.** ⛔ **M-C6's two unreachable branches become reachable by the SAME change** — i.e. this row's "latent" verdict expires the moment G2 lands. M-C4/M-C8 noted at the placement pair, ⛔ **not re-opened**. ⚠️ **ALSO REGISTERED HERE 04-Aug (capital-path, ~~⛔ neither fixed nor a new row~~ ⭐ **AMENDED 05-Aug 00:15: still NOT A NEW ROW — 231 stands — but (b) is now BUILT and (a) is annotated; the original wording is struck, not deleted, because it is the state the three commits below are measured against**):** **(a) 10 STALE `fm_ledger` RESERVATIONS — ruled ANNOTATE, ⛔ NEVER RECONCILE.** The live risk is **TOOLING, not capital**: any tool that rebuilds capital state from the ledger alone inherits them. ✅✅ **(a) ANNOTATED IN TWO PLACES, 04-Aug 23:35/23:36 — `<BUILT — NOT DEPLOYED>`, both docs/comment-only.** **(a-i) `7d4ae6b` — `docs/04_db_schema_reference.md`: THE DENOMINATOR DISCIPLINE.** ⛔ **The "16.5% of capital" this campaign had been quoting is the LEAST DURABLE way to state the defect** — its denominator is **today's ₹9,882.30, a TESTING value**, so the figure **shrinks as the account grows and quietly stops sounding alarming while the defect is entirely unchanged.** ⭐ **MEASURED, each ratio with its denominator NAMED: by COUNT 10 / 1,327 = 0.75%** (every reservation ever opened) · **by VALUE ₹1,628.13 / ₹133,274.95 = 1.22%** (every rupee ever reserved) · **by CAPITAL 16.5%** (today's ₹9,882.30 — struck-but-kept per §G4, because it is what makes the TOOLING risk vivid). ⇒ **the three do not disagree; they answer different questions: quote count/value when describing the DATA, the capital ratio ONLY when describing the CONSEQUENCE, and say WHICH capital.** ⛔ **Never write a bare "16.5%"** — a later reader assumes it means 16.5% *of the ledger*, which is off by **~13×**. ⭐ **The finding underneath the commit is that ITEM 2 WAS ALREADY DONE:** the tooling rule, the 10 reservations, the ₹1,628.13, the annotate-never-reconcile ruling with its 141-VOID-`innings` precedent and the rehydrate safety note **were all already in that file** (landed 04-Aug afternoon, `9a6f345`/`ac5259d`) — **checked BEFORE writing, not after**; re-adding any of it would have been the duplication §G3 forbids. **(a-ii) `c5c1926` — `main.py:2439-2465`, at the `rehydrate_from_open_trades` CALL SITE (`:2469`): WHY the reconstruction keys on TRADE STATUS and not on `fm_ledger`.** ⭐⭐ **This is the THIRD correct-by-accident venue this campaign has found, and it is the DANGEROUS one, because the wrong change LOOKS LIKE AN IMPROVEMENT:** *"rebuild capital from the capital ledger rather than from trade rows"* reads as the more principled design and would **silently inherit all ten phantom reservations** (₹1,628.13, 15–18 Jun 2026) — all ten on trades that are **CANCELLED or CLOSED_MANUAL**, which is exactly why `get_all_open_trades()` never returns them and why they **have never touched in-memory capital.** ⛔ **A COMMENT is the right instrument precisely because the hazard is a plausible-looking future refactor, not a runtime condition** — and it sits at the CALL SITE because that is where someone deciding to "improve" the reconstruction is standing; the full rule is **cited to `04_db_schema_reference.md`, not copied** (§G3). ⭐ **COMMENT-ONLY PROVEN, NOT ASSERTED (the #2c precedent): `ast.dump(ast.parse(source))` sha256 IDENTICAL both sides (`e19bccce…a171d21`), comments excluded from the AST by construction — plus the anti-vacuity half, because an AST match means nothing if the file did not change: `main.py` md5 `9bbc1747…` → `6cff0ce8…`, diff +27/−0.** ⛔ **No gate run, and the reason is stated rather than skipped: for THIS claim the AST proof is strictly STRONGER than a gate** — a gate shows the suite still passes; the AST shows there is **nothing for it to have executed differently.** **(b) `capital_at_risk` HOLDS POSITION NOTIONAL, NOT ACCOUNT CAPITAL** — `signal_processor.py:453-455`: `risk_amt = abs(entry − SL) × qty`, `capital_at_risk = entry × qty`, so **`qty` cancels algebraically and `risk_pct ≡ (entry − SL) / entry` at EVERY quantity** (not merely at qty=1, which is what makes the coincidence look like confirmation). It renders to the operator as **`Risk ₹8.86 (1.5%)`** — **1.5% of the POSITION, not of capital**, while the true account-risk figure is ~1% and unrelated. ⭐ **Same operator-facing false-claim family as the 15:15 alerts (§B#9); a variable name doing the misleading.** ~~⛔ RECORDED, NOT FIXED.~~ ✅✅ **(b) `<BUILT — NOT DEPLOYED>` 04-Aug 23:35 — `0087d3a`** (§G4: the verdict above is struck, the DEFECT text is kept, because it is what the fix is evidence against). ⭐ **THE FOUR QUANTITIES ARE NOW NAMED FOR WHAT THEY HOLD** (`signals/signal_processor.py:453-472` the vocabulary comment · `:473-477` the assignments · `:491-493` the render, **all re-measured at HEAD `6112791`**): `position_notional_rs` = qty × entry (**EXPOSURE**, not risk, not capital) · `account_risk_rs` = qty × (entry − SL) (**the real money at risk**) · `sl_distance_pct` = (entry − SL) / entry (**a property of the LEVEL**) · **margin blocked = notional ÷ leverage — NOT AVAILABLE HERE.** The line now reads **`Qty: 3 \| Exposure: ₹1,200.00` / `Risk: ₹60.00 (SL is 5.0% from entry) \| Est. net TGT: +₹120.00`.** ⛔⛔ **A GENUINE "% OF CAPITAL" IS DELIBERATELY NOT RENDERED, and that restraint is the load-bearing decision:** this helper **receives no capital**, and reaching into `FundManager` for one would turn a display fix into a **capital-state reader** — the exact expansion Step 1 was gating against. **Name what we have; do not fabricate what we do not.** ⭐ *"from entry", not "below entry"* — **on a SHORT the SL sits ABOVE entry**, and a test pins that wording. ⭐ **STEP 1 GATED IT AND IT CLEARED, WITH THE WIDTH STATED: `capital_at_risk` existed in exactly THREE places repo-wide** (excluding `.git`/`venv`) — **this register row, its assignment, and its single consumer two lines later**; the derived `risk_pct` fed **one** f-string; **nothing sizes, limits, reports or tests on it.** ✅ **8 new tests (`tests/unit/test_signal_alert_capital_vocabulary.py`) ASSERT THE CLAIM, NOT THE STRING** — pinning the body would go **RED on a harmless rewording and GREEN on a reworded-but-still-wrong one.** ⭐⭐ **The load-bearing one is `test_percentage_is_invariant_to_quantity`: a fraction of CAPITAL must SCALE with position size, and this number does not move across a 100× qty change** ⇒ **that invariance is the ALGEBRAIC proof it can never be a capital ratio**, and it goes red if anyone reintroduces one under this label. ✅ **RED-ON-OLD by planting the old source: 7F/1P — and the single PASS is correct, not a hole:** `test_risk_rupees_is_the_account_money_at_risk` is a **must-NOT-change guard** on the rupee figure, which was never wrong. **Restored byte-identical (md5 matched both sides), by `git stash` — ⛔ never `write_text` (the CRLF trap).** ✅ **GATE: BASE (fresh worktree at `2b6b48e`, V2-staged with `instruments.csv` + a venv junction) 9F/5,549P/4skip · changed 9F/5,557P/4skip · `comm -13` EMPTY *and* `comm -23` EMPTY (identical failure SETS both ways, not counts) · passed delta +8 = exactly the 8 new tests, so the arithmetic closes.** The **9 existing alert-direction tests still pass** ⇒ the alert shape they pin is intact. ⚠️ **M3 NOTE — THE `:453-455` CITATION ABOVE IS NOT STALE, IT IS SHA-DEPENDENT, AND BOTH READINGS ARE LIVE:** it is **still exactly right at the DEPLOYED `0197923`** (verified by `git show`) and **wrong at HEAD**. ⇒ **until the next push, this row must carry both, and the deployed one is the one the operator's Wednesday alert comes from.** ⚠️⚠️ **DISCLOSED, ⛔ NOT FIXED AND ⛔ NOT A NEW ROW (§G3 disclose-don't-expand) — THE NEW NAME COLLIDES INSIDE ITS OWN FILE, IN THE UNIT AXIS THIS VERY COMMIT EXISTS TO POLICE:** `sl_distance_pct` now appears **twice in `signals/signal_processor.py` with TWO DIFFERENT UNITS** — **`:475-477` is a PERCENTAGE** (`… * 100.0`, renders `5.0`) while **`:1643` is a FRACTION** (`sl_distance / entry_price`, compared at `:1645`/`:1655` against `strategy.sl_min_pct` = **`0.003`**, which `strategies/schema.py:215-219` validates as **`> 0 and < 1`** ⇒ a fraction beyond doubt). ⭐ **LATENT, and the reason is structural, not luck: both are FUNCTION-LOCALS in separate methods, so there is no runtime interaction and no reachable defect** — width stated: **10 hits repo-wide** (`--include=*.py --include=*.yaml`, excluding `.git`/`venv`), **all 10 inside this one file plus 2 docstring mentions and 2 in `tests/unit/test_signal_processor.py`**, none crossing scopes. ⛔ **Recorded because the campaign's own rule is that a vocabulary fix which leaves ONE NAME MEANING TWO UNITS in ONE FILE has moved the ambiguity rather than removed it** — the next reader to grep this name gets two answers. |
| **G25** | Slice-2.5: M-C2 · M-O7 · M-O6 · M-O4 · M-O8 · H-6 | ⭐ **arm WITH delivery — correctly bundled so whoever flips delivery FINDS them** | S1 §4 (6 named sub-items), S2 §5-B2 | ⚠️ memory `slice25_execution_plan_27jul`: T2's overnight leg is **NOT isolated from the live SERVICE** ⇒ an **EXPECTED** "Orphan GTT" WARNING, not a finding |

## §C.3 — OBSERVABILITY / OPS / TEST (open, lower gate)

| id | item | why-pending | source | ⭐ audit cross-link |
|---|---|---|---|---|
| **G1** | liveness probe stops **16:00** vs service **17:35** — **95 min unwatched every trading day** | its own small design (cron window **and** `_LIVENESS_END` move together). **Compensating control = the manual ~17:10 check** | S1 §4 (sole survivor of S1 §F-L), S2 §5-B2 | **COMPOSED → IA-P10-03(i)**: an emergency HALT after 16:00 has **no CRITICAL-grade notification until 09:00 next day** |
| **G7** | evidence infrastructure — the two shadows cannot validate a new entry thesis | ⚠️ **every service-down day is a lost OOS day** | S1 §4 | **IA-P2-05**: the V3 shadow evidence base **inherits the same dead inputs it was meant to replace** — 19 days of soak accruing evidence with a **reachability-broken** threshold |
| **G9** | 9 built-and-never-run — ⭐ ask *"does anything ACT on what it produces?"* | re-verified 30-Jul: **still never-run** | S1 §4, S2 §5-B2 | ⭐⭐ **SUBSUMED AND MASSIVELY WIDENED → IA-XARCH-01** (§B#1): **~22 inert subsystems/knobs in three families** (α×9 pass-through · β×7 starvation · γ×6 unreachability). **G9's 9 are a subset.** ⛔ Track the class at §B#1, not here |
| **G10** | a **separate test token** — 20 live prod keys sit in the test env | the network guard shipped 30-Jul is only the **first** layer | S1 §4, S2 §5-B2 | **RE-GRADED + SPLIT → IA-XSEC-02** (§B#11): **PC test runs CANNOT place a real order** (newly established, materially better) — **but VM test runs CAN** (3 crash-test files `load_dotenv()` the real `.env`). **HIGH if the VM path is ever exercised.** Also **IA-XTEST-05** (the subprocess escape) |
| **G12** | **T2** *(wording corrected)* · **T3** `test_fix181` LIMIT-vs-MARKET · **T4** `backfill…w8.py:92` restates the vocabulary · **F2** `CT_SCRATCH_DIR` inside `data_store/` | after 4-Aug; all test-side | S1 §4 (3 sub-items), S2 §3.4/§3.7 | **T4 LINE-VERIFIED + WIDENED → IA-XDUP-04**: `scripts/backfill_closure_source_w8.py:92` restates the closure vocabulary as string literals with **NO import from `core.closure_source`** — *a restatement surviving inside the one concept that HAS a canonical contract* |
| **G15** | remaining free-text cousins (`REJECTED_KILL_SWITCH` @ `db_reader.py:41`, throttle category, W9) | low | S1 §4, S2 §3.7 | **W9/G15 unchanged** (S3 P1.4): per-symbol reject reasons remain response-only. Class kin: **IA-P2-08(a)** (`REJECTED_SCORE_{n}` embeds a variable in the status column) |
| ~~**G17**~~ | ~~AB-910 phases 9+10 never produced~~ ➡️ **MOVED 04-Aug-2026 TO §E (GOVERNANCE). The row is struck here, NOT deleted (§G4), so a reader arriving from an old citation finds where it went instead of concluding it was closed.** | ~~formal slice unaudited~~ ⭐ **unchanged in substance — it is a PROCESS/records gap, and nothing in production behaves differently because of it. ⛔ Moving it did NOT deprioritise it: §E is a separate QUEUE, not a lower tier** | S1 §4 | ⛔ **NOT a new row and NOT a closure — 231 stands** |
| **G19** | 6 TIER-B sub-items (M-A2·M-K3·M-S3·M-D1·W10-relabels·broker_order_id) — **M-O9 CLOSED as INERT** | low | S1 §4 (7 sub-items), S2 §3.7 | **M-S3 re-measured** (S3 P2.4): rotation in place, **0 timeouts / 0 rotations all-time** ⇒ DEPLOYED-not-VERIFIED-LIVE. **M-A2** carries R8's live caveat (§D) |
| **G20** | **~55 architecture/LOW line items** | ⛔⛔ **POINTER ONLY — the two July audits (S6) are the source of record. DO NOT re-enumerate, DO NOT delete them** | S1 §4 (11 named sub-items), S6 | S3 P5.4 **re-verified 5 of them line-by-line** (July-audit LOWs :173–:177 all **STAND**, several *made precise* by **IA-P5-02**/**IA-P5-07**) |
| **G21** | BK-1 · BK-3 · BK-4 · BK-5 · BK-6 · BK-7 · BK-8 | low / deferred | S1 §4 (7 sub-items) | ⭐ **BK-8 IS NOW THE HEADLINE FIX** — the schema↔ctor completeness check is **half of §B#1's one mechanism** (**IA-XCFG-02**: *"BK-8 as named would close family α only"*; the other leg is acted-telemetry) |
| **G22** | DG-1 · DG-2 *(DG-3 merged into G23, counted once)* | low | S1 §4 (2 sub-items), S2 §3.7 | — |
| **G23** | **B3 / P1 authoritative flip** (`eod_broker_reconcile`) | a clean shadow week + an MTM spot-check | S1 §4, S2 §5-B2 | ⛔⛔ **SHARPENED → IA-P8-01** (§B#6): **the gate's evidence column is structurally broken** — `eod_verify` stuck **PENDING for 18 trading days** while its heartbeat reported **SUCCESS 32/32**. ⇒ **G23's gate CANNOT be satisfied until §B#6 is fixed** |
| **G24** | P5-2 · P5-3 *(P5-4 closed 30-Jul)* | low | S1 §4 (3 sub-items), S2 §3.7 | — |

## §C.4 — NEEDS RAMA'S DECISION (⛔ nothing else can move these)

| id | the decision | why it is yours | source |
|---|---|---|---|
| **R10** | **Conditional-allocation flip** — the **4th delivery flag** (4-Aug) | ⭐ **The code is BUILT and traced** (`resolve_bucket_allocation`, `fund_manager.py:111`; Q9 trace 30-Jul). **Purely a flip decision.** Without it ~70% of capital strands in the idle intraday bucket | S2 §5-B1 |
| **R9** | `mis_filter` **ENFORCING** flip | The SHADOW half is **live Mon 3-Aug** (§A1). Enforcing touches the **signal path** ⇒ your call. ⭐⭐ **FIRST *MEASURED* ARGUMENT — 04-Aug-2026, and it is an INDEPENDENT one:** Zerodha itself rejected **DBOL** (*"MIS orders are currently blocked for DBOL. Place a CNC order instead"*) — **the exact symbol the shadow filter had flagged**. The broker and the filter agreed without either consulting the other. Because shadow drops nothing, DBOL was re-sent and re-rejected **3× — 10:00:24 · 10:05:25 · 10:11:14** — while the filter had it blocklisted from **10:01:13**. ⇒ **Enforcing would have saved 2 of the 3 broker round-trips**; ⛔ the first is **not** preventable — that rejection is what *teaches* the blocklist, so the ceiling is `n−1`, never `n`. ⚠️ Cost today was 3 wasted signals + 9 ERROR lines, **no money moved**. ⛔⛔ **DO NOT FLIP THIS WEEK — evidence now, decision after the flip.** Wed 5-Aug is the delivery flip; a second behavioural change on that morning is precisely the variable-stacking the **WED-flip single-variable rule** exists to prevent (⛔ that ruling, **not** this register's `R2` row two lines below — same token, different thing) | S2 §5-B1 · `logs/system_2026-08-04.log` |
| **R2** | D1 sizing / `max_concentration_pct` | ⛔ **HOLD — blocked by G3.** ⚠️ Read `sector_exposure()`'s `'UNKNOWN'` handling **before** this moves; the error direction inverted | S2 §5-B1 |
| **G4** | **Ratify PB-01's gates / window / thresholds** | The *"V3 DECISION CONTENT SPECIFICATION v1.0"* **does not exist** (6-search width, 30-Jul). Values recorded **OBSERVED-FROM-CODE**. ⛔ **A DECISION is missing, not a document — do not close it by writing the spec from the code.** Owed before any PB-01 promotion, gated far beyond 4-Aug | S2 §3.6/§5-B1 |
| **R11** | `predeploy-*` backups — delete, or write the retention rule | ⚠️ **Renaming to `pre_*` is a DELETE in disguise.** Re-verified 30-Jul: **4 files, backups 8.4 G** at `data_store/backups/` (⛔ **not** `~/backups`) | S2 §5-B1 |
| **R5 · R6** | prune-retention cap value · backup-retention cap value | Both are "pick a steady-state number". R6: measure a week of post-clear nights first | S2 §5-B1 |
| ⭐ **R12** | **The assistant permission allowlist pre-authorises DESTRUCTIVE mass-mutation of the LIVE trading DB** — `.claude/settings.local.json` → `/permissions/allow`: 21 `check_vm_state` entries, **2 × `--reset`** + both `--cleanup-*` flags, all runnable by an AI agent **without prompting**. **Decide: prune it, scope it, or ratify it.** | ⛔ **It is an AUTHORISATION decision, not an engineering one** — and it does **not** go away with #8b: removing `--reset` from the script leaves the allowlist untouched. ⚠️ Also **stale** — 11 entries name `~/check_vm_state.py`, proven non-existent by your own 02-Aug VM check. ⛔ **NOT a new register item** — the authorisation-surface facet of **debt-ledger #11** (IA-XSEC family), counted ONCE there; **231 stands** | #8b Step-1 (`docs/audit/ledger8b_check_vm_state_step1_02aug2026.md` §9) · IA-XSEC-01/-02 |
| ⭐ **R13** | **`mempalace.yaml` is STALE BY ~3 MONTHS, and every card in this campaign still names it in its memory-update directive.** `as_of: 2026-04-27` · `head_commit: 16e1595` · `schema_version: 13` (live schema is **v45**). **Decide: RETIRE it, or actually MAINTAIN it.** ⛔ Until you rule, cards must **stop naming it as if it were current** | ⛔ **A live instance of this audit's own verdict class — A DECLARED THING WITH NO EFFECT — inside our own process:** cards have been instructing updates to a file nobody maintains. ⚠️ **MEASURED 03-Aug and worse than "stale": it is UNTRACKED, git-ignored at `.gitignore:82`** ⇒ no history, no review, PC-local only — a doc with no way to be wrong *visibly*. ⭐ **And it CONTRADICTS the tracked record in the dangerous direction:** its `g2c_vm_deploy` still says *"push HELD pending Rama ordering call"* while `docs/SYSTEM_MAP.md:282` records G2c **DEPLOYED 03-Jul ~17:25, merged `7b1c92d`** (verified — real commit, 2026-07-03 17:23:34) ⇒ **M3 turned on our own artifacts.** ✅ **The seeding card's "it is the only written trace of the GUI workstream anywhere" caution is REFUTED — recorded, not applied (M3): 137 tracked files under `ops_dashboard/` + `docs/gui_project/G0_BACKEND_INVESTIGATION_REPORT.md` + `SYSTEM_MAP.md:282`.** ~~Retiring it discards nothing unique.~~ ⛔⛔ **AMENDED 04-Aug-2026 per §G4 — struck, NOT rewritten, because this false line IS the evidence for §M5 and a register line that quietly becomes correct teaches nobody.** ⚠️ **WHAT WAS MEASURED:** the **GUI workstream** — is it traced elsewhere? **Yes** (the 137 files above), and that part **STANDS**. ⛔ **WHAT IT DID NOT COVER:** whether **every line** of the file was duplicated elsewhere. It was not — `mempalace.yaml:215-224` held **`future_backlog_LOCKED_ROADMAP_ONLY: F1–F9`**, nine roadmap items with **no tracked copy**, so executing the retirement on the widened reading would have **DELETED them**. ✅ **RESOLUTION:** migrated **verbatim** (md5 `53d195f7…`, 9/9, `diff`-clean) to **`ops_dashboard/docs/F_BACKLOG.md`**, *then* retired — see §D5's execution block. ⭐ **R13 IS THE INCIDENT THAT EARNED `campaign_practices.md` §M5** — *state the subject in the same sentence as the conclusion*: this line's failure was that it carried a conclusion with **no subject attached**, so no later reader could tell which question it had answered. ⛔ **NOT a new register item** — the doc-concentration facet of **debt-ledger #12** (IA-XDOCS family), counted ONCE there; **231 stands** | debt-ledger #12 · IA-XDOCS-03/-05 · `.gitignore:82` · `SYSTEM_MAP.md:282` |
| ⭐ **R14** | **V2 pins the regression's ARGUMENTS but not its INTERPRETER — should the interpreter become part of the baseline?** Measured 03-Aug: the identical campaign invocation yields **7 failures** under one interpreter and **9** under another. `venv/Scripts/python.exe` is a **launcher stub that re-spawns** (`Popen.pid=4060` vs the child's own `os.getpid()=30908`); under `C:\Python314\python.exe` the two are the same number, and the two extra failures vanish (**6 passed in 0.18s**). ⛔ **Do not amend V2 without this ruling** — `campaign_practices.md` is closed to refinement passes; this is a new incident, so it qualifies, but the rule change is yours | ⭐⭐ **The consequence is not local: EVERY prior stamp in this campaign is AMBIGUOUS about which interpreter produced it**, so any two stamps compared across sessions may differ by two failures for a reason nobody recorded. ⚠️ **Second-order: under the venv, `test_instance_lock::TestSingleInstanceAcrossProcesses` ×2 fail for a FIXED reason ⇒ the single-instance guard is effectively UNEXERCISED by the gate.** It cannot *mask* a delta (it fails on both sides) but it can no longer *catch* a real regression. ⚠️ **The full suite CANNOT simply be moved to the other interpreter** — `C:\Python314` lacks the project deps (`ModuleNotFoundError: cachetools` at collection) ⇒ the choice is *pin-and-subtract* vs *install deps there*, and that is a decision, not a chore. ⛔ **NOT a new register item** — a gate-integrity facet, counted ONCE here; **231 stands** | `docs/audit/kill_drill_03aug2026.md` §5c/§5e @ `28a6d5a` · practices §V2/§V3 |
| **R3 · R4 · R12** | D2 direction · D3 min_pass (downgraded) · WAAREERTL 23-Jul external close | R3 needs months + a positive control. R12 may be a **capital** question, not execution | S2 §5-B1 |

⚠️ **ID COLLISION FLAGGED, NOT SMOOTHED (03-Aug):** `R12` labels **two different items** in this
table — the allowlist row and the `R3 · R4 · R12` row (WAAREERTL). Pre-existing; both keep their
text, exactly as the IA-P7-02 citation mismatch was flagged rather than tidied. **New ids continue
from `R13`.**

## §C.5 — DATED / STANDING OPERATOR ITEMS (⏰ these have clocks)

> ⛔ **ACCOUNTING NOTE, 04-Aug — stated rather than left to be discovered:** the two dated entries added at the head of this table (**the Wed-evening CHECK1 gate** and **the Thu 6-Aug holdings-blindness date**) are **OPERATOR GATES, not new register items.** Each is the *clock* on work already counted elsewhere — the CHECK1 gate belongs to **§A2/§A3's delivery lifecycle**, and the Thursday date is **§C.6 `F1`**, which already exists. ⛔ **Neither introduces a new item; 231 stands.** ⭐ They are surfaced here because §C.5 is the only section a reader consults for "what has a clock on it", and a gate that exists only inside a long §B row is a gate nobody reads in time.
> ⛔ **EXTENDED 05-Aug — the note now covers THREE, and the third is stated with its arithmetic because §E's new rule demands it:** the **daily 08:15 token-file check** below is likewise an **OPERATOR GATE, not a new item** — it is the *clock and the mechanism* on the boot that **§A1/§A2 already count**, traced in full at **§A2's BOOT CHAIN block**. ⛔ **231 STANDS; §1's distribution is UNCHANGED because nothing moved between bands** — ⭐ and that is checked, not assumed: §E's rule is that **a MOVE is invisible to a total and fully visible to a distribution**, so the test is "did any item change section?", and the answer is **no**. *(§C stays 104, §E stays 1, `from A` stays 132.)*

| item | why-pending | source |
|---|---|---|
| 🔌🕳️🔴 **EVERY TRADING MORNING, BEFORE ANY OTHER READ — ⭐ IS `data_store/session/zerodha_token.json` PRESENT WITH `date` == TODAY?** | ⛔ **THIS IS A SINGLE POINT OF FAILURE WITH NO ALARM ON IT — register it, ⛔ do NOT fix it.** Nothing in cron starts the service: the 08:15 cron writes **only** the token file, and `token-watcher.service` (30 s poll, window `hour ≥8 <16`) starts `trading-system.service`, which is `WantedBy=multi-user.target` + `RestartPreventExitStatus=3 4` ⇒ **a clean exit 0 never self-restarts.** 🔴 **A failed refresh is SILENT — no boot, no error, no alert** ⇒ ⛔ **"no order today" and "the service never started" are INDISTINGUISHABLE until this file is checked, and NO LATER EVIDENCE SEPARATES THEM.** ⚠️ **`logs/cron-auto-token.log` is NOT the substitute — it logs only on abnormality (no write in 8 days), so silence there is SUCCESS and an unchanged log is not proof.** ⭐ **Expected boot spread is 08:15:02–08:15:32 roughly uniformly** (token stamp + one 30 s poll) — ⛔ **not "~08:15:30", which is the pessimistic END of the window and would flag every fast draw as an anomaly.** ✅ **DISCHARGED 05-Aug: token `08:15:01.910` `date: 2026-08-05`, service `active 08:15:05`, `NRestarts=0`** | **05-Aug boot-chain trace** (§A2) · memory `boot_chain_token_watcher_05aug.md` · ⚠️ **SYSTEM_MAP entry OWED** |
| 🔴🔴⭐⭐ **WED 5-AUG EVENING, BEFORE THU 08:15 — THE CHECK1 GATE.** ⛔⛔ **UPGRADED 05-Aug: MANDATORY, NOT CONTINGENT — TWO CNC POSITIONS ARE HELD** (§A2 (a)), so the precondition the whole gate waited on has arrived. **ONE QUESTION: *is there a held CNC position with NO `ACTIVE` `gtt_state` row?*** | ⛔⛔ **AND "THE GTT PASSED AT ZERODHA" IS NOT THE CLAIM CHECK1 READS — RE-MEASURED AT HEAD, AND THE PREDICATE IS NARROWER THAN "AN ACTIVE ROW EXISTS":** `order_reconciler.py:855` calls `get_active_gtt_states()` = **`SELECT * FROM gtt_state WHERE status = 'ACTIVE'`** (`state_store.py:2250`), builds `delivery_trade_ids` from those rows' **`trade_id`** (`:858`), and skips the trade at **`:871-872`** only when **`trade["trade_id"] in delivery_trade_ids`**. ⇒ ⭐⭐ **THE SKIP IS KEYED ON `trade_id`, NOT ON SYMBOL AND NOT ON THE BROKER.** A GTT accepted by **Zerodha** with no ACTIVE row is a **different fact** and does not skip; ⭐ **and an ACTIVE row carrying a DIFFERENT or NULL `trade_id` does not skip either — so "is there an ACTIVE row?" is NOT a sufficient check.** ⇒ **VERIFY THREE THINGS TONIGHT AND REPORT THEM SEPARATELY: (1) the BROKER GTT exists (Kite/API) · (2) an `ACTIVE` row exists in `gtt_state` · (3) that row's `trade_id` EQUALS the open trade's `trade_id`.** 🔴 **(1) true with (2) or (3) false IS THE EXACT HAZARD THE GATE EXISTS FOR** ⇒ Thursday's 08:15 must not run undecided: on T+1 the holding leaves `positions()`, `bp is None`, and **CHECK1 fires MANUAL_CLOSE — cancelling broker orders and RELEASING CAPITAL on shares still held.** 📏 **Reconcile on TODAY'S TOTAL CNC VALUE, ⛔ not on symbol names** — one number, broker vs DB, and it stays valid if a position is added or partly exited. ⛔ **Ask it exactly that way — it covers BOTH routes, and checking only for a failed placement misses the second: (1) `cnc_gtt.place_for_fill` RAISES (missing LTP / C8) so `insert_gtt_state` never runs; (2) NO failure at all — the GTT is placed, TRIGGERS Wednesday, leaves `ACTIVE`, and a partially-filled sell leaves a RESIDUAL holding Thursday with no ACTIVE row.** 🔴 **If YES → Thursday's 08:15 must not run undecided: CHECK1 would cancel that position's broker orders and RELEASE ITS CAPITAL while the shares are still held.** Branches: *no held CNC* ⇒ fuse unlit, record it · *held + ACTIVE row* ⇒ protected, record it **and note the protection has now actually RUN once in production** · *held, NO ACTIVE row* ⇒ harden the skip **or** Rama closes the position Wednesday — either is a decision with a fact in hand. ⚠️⚠️ **CARRY THIS SENTENCE: ~~`gtt_state` holds 0 ROWS TODAY, so Wednesday's first CNC trade writes~~ ⭐ SUPERSEDED 05-Aug (§G4) — WEDNESDAY'S CNC TRADE HAS NOW HAPPENED, so the row it wrote (if it wrote one) IS the FIRST ROW THAT TABLE HAS EVER CARRIED IN PRODUCTION ⇒ the protection is exactly as good as a write that has NEVER EXECUTED BEFORE TONIGHT — ⛔ and "the code has a skip for this" is NOT evidence the write happened; ⭐ THAT IS WHY (iii) MEASURES THE ROW ITSELF rather than inferring it from the GTT.** ~~⛔ **Deciding this TONIGHT would be deciding BLIND — Wednesday's exposure is ZERO (at T+0 a CNC position IS in `positions()`), the hazard is THURSDAY, and Wednesday evening sits between them.**~~ ⭐⭐ **SUPERSEDED 05-Aug (§G4) — AND THE STRUCK TEXT IS KEPT BECAUSE ITS REASONING WAS RIGHT AND ITS PREMISE EXPIRED, WHICH IS THE DISTINCTION WORTH PRESERVING.** Deciding it last night WOULD have been blind — **there was no position and no row.** ⛔ **Tonight it is the OPPOSITE: the facts now EXIST and are readable, so the only blind choice left is NOT LOOKING.** ⭐ **Everything else in the struck sentence still holds and is now load-bearing: Wednesday's exposure IS zero (T+0 keeps CNC in `positions()`), the hazard IS Thursday, and Wednesday evening still sits between them — that is precisely why the window is TONIGHT and does not reopen.** | flip plan §4a, 04-Aug · ⭐ **predicate re-measured at HEAD 05-Aug** |
| ⏰🔴 **THU 6-AUG — the holdings-blindness bites** | **11 positions-only readers**, verdicts now issued (§C.6 F1): 1 wrong+capital-path · 1 wrong+advisory · 3 correct by construction · **6 correct by ACCIDENT, 3 of them on the KILL path**. ⛔ F1 is registered as ONE reader and **is a class** | 04-Aug measurement |
| ⏰ **2FA seed → VM-only** | dated **Fri 7-Aug / Sat 8-Aug** | S2 §5-B1, memory |
| ⏰🔴 **Commit NSE's published `nse_holidays_2027.yaml` before 31-Dec-2026** | **MEASURED: the first 08:15 boot of 2027 raises `ConfigMissingError` and DOES NOT START.** ✅ The 15-Dec email reminder is DEPLOYED (`cbcad2c`). ⛔⛔ **NEVER invent the dates** | S2 §5-B1, S4 §7.9(e) |
| **rotate the Telegram token** · **disable rpcbind** | standing security backlog | S2 §5-B1 |

## §C.6 — THE 30-JUL FINDINGS STILL OPEN (F1 · F2 · F4 · F5)

| id | item | why-pending | ⭐ audit cross-link |
|---|---|---|---|
| **F1** | `reconcile_positions` is **BLIND to delivery from T+1** — reads `positions()` only, **never `holdings()`** ⇒ ⛔ **a 15:45 SUCCESS is NOT evidence the book is flat**. ⛔⛔ **CORRECTED 04-Aug-2026 (§G4 — added BESIDE, the original NOT rewritten, because the understatement is the point): F1 IS SCOPED AS ONE READER AND IT IS A CLASS OF TWELVE.** AST census at `2d9c114` (⛔ not grep — grep returns 15 because it cannot tell a call from a **docstring mention**): **12 `get_positions()` call sites vs 1 `get_holdings()` call site**, and **exactly one file reads both** (`cnc_gtt_monitor._gather:432`). The audit's *"3× positions()-only readers"* is a **4× undercount**. ⭐ `zerodha_adapter.py:929` defines holdings as *"Carried (**T+1+**) CNC delivery holdings"* ⇒ **a delivery position sits in `positions()` on its buy day and moves to `holdings()` at T+1, so all twelve go blind to it exactly ONE DAY after purchase** — incl. **the HARD_KILL broker sweep** (`kill_switch:1854`), **all three `eod_squareoff` sites**, and **`broker_net_qty`** (`position_helpers:37`, which backs `determine_close_direction` for the kill path *and* order_placer's emergency exit). ⛔ **NOT a claim all twelve are defects** — the kill's blindness is *correct* per Q4 (delivery survives a HARD_KILL) but **correct BY ACCIDENT**, which is why the buy-day product filter must land before anything makes the kill holdings-aware. ~~**Each of the twelve needs its own verdict; none is issued.**~~ ✅✅ **SUPERSEDED 04-Aug (§G4) — THE VERDICTS ARE NOW ISSUED, one per reader, and they do NOT all point the same way.** Of the **11 positions-ONLY** readers (12 `get_positions()` sites minus the one file that also reads `get_holdings()`): **1 WRONG and on the CAPITAL PATH · 1 WRONG and advisory · 3 CORRECT BY CONSTRUCTION · 6 CORRECT BY ACCIDENT — and 3 of those 6 sit on the KILL PATH.** ⛔⛔ **"Correct by accident" is the whole finding and must not be read as "correct": each of the six is right only because delivery has never been held past T+1, and the flip removes exactly that precondition.** ⇒ **the buy-day product filter must land before ANYTHING makes the kill holdings-aware (Q4's ordering rule), because three of the accidental-correct readers ARE the kill path.** ⏰ **THU 6-AUG is when this bites** — Wednesday's first CNC purchase sits in `positions()` on its buy day (all 11 readers see it) and moves to `holdings()` at T+1. Record: `docs/audit/holdings_blind_readers_verdicts_04aug2026.md`. 🔴 **The flip is what makes the class REACHABLE** ⇒ this is now flip-critical, not latent-forever. Record: `docs/audit/ledger7_multiauthority_measurement_04aug2026.md` §1 | **REAL BUT LATENT** — traced 30-Jul: has never fired and **could not have** (only 3 CNC orders ever in the live DB, all FAILED/CANCELLED). ✅ **Re-confirmed 04-Aug 13:05 by read-only broker probe: holdings 0 · GTTs 0 · net positions 0** — T2's 29-Jul basket is fully closed, so **no already-past-T+1 position exists to test this against**. **Gate = the first live delivery trade (§A2/§A3).** ⇒ **Folds into reconciliation step 1** — ⛔ not a separate workstream. ⛔ **Do NOT buy a CNC position to create the test case before the flip:** it is untracked to the reconciler, fires the known-false `Naked untracked position` alert, and adds a variable to the one morning reserved for having none | ⭐ **A THIRD FALSE-ALARM FACE FOUND → IA-P8-04**: a CNC **sell** from holdings sits in `positions()` as a **NEGATIVE** quantity ⇒ **ORPHAN_AT_BROKER (−qty)**, beyond the doc's buy-day ORPHAN and T+1 MISSING. Measured 31-Jul, 5 rows |
| **F2** | `fix-tests-27jul` shipped **3 test failures** — one root cause: `SCRATCH_DIR = data_store/ct_scratch` sits **inside** the guarded dir | **test-only.** Fix = point `CT_SCRATCH_DIR` outside `data_store/`, or grant the opt-in. ⇒ folded into **G12/T2** as an UPDATE | ⚠️⚠️ **STRUCTURAL POINT PRESERVED:** because the regression BASE *must* include `fix-tests-27jul`, **a failure that commit INTRODUCES can never appear in the before/after delta.** → **IA-XTEST-02** (the base-includes-the-fix structural hole) |
| **F4** | **the EOD report gives the operator a WRONG instruction** — *"SOFT_KILL — needs `deploy/resume.sh` before market open"*, **every day** | The 15:15 kill is a **prior-day** kill that **auto-clears at 08:15**; `resume.sh` begins with `systemctl stop`, so run **after** a boot it would **stop live exit management and the 15:17 squareoff**. Third wrong site | **RANKED → §B#9** (IA-P9-01/-02, alert fatigue / false-safety claims). Sibling of **IA-XDOCS-01** (§B#8) |
| **F5** | Zerodha **refuses MIS** on some symbols — a new error class | Handled correctly, no position taken. ⭐ A staged-never-pushed branch `mis-tradability-filter-30jun` (22 tests, `PATHS.md:235`) is exactly this | S3 P1.4 measured its signal-path signature: `PLACEMENT_FAILED` rows carry the broker message **verbatim**; the 400-handler records into the shared `mis_blocklist` (`main.py:2633-2637`) |

## §C.7 — PARKED, EACH WITH ITS TRIGGER (⛔ a queue, not a graveyard)

**SOURCE: S1 §7 — 7 items.** ⭐ **RE-READ THIS LIST ONLY WHEN A TRIGGER FIRES.**

| parked item | its trigger |
|---|---|
| **Slice 2.5 delivery** (carries M-C2 + M-O7) | **DELIVERY IS ENABLED** ⇒ ⭐ **§A2 fires this** |
| **the live-test cert** (LT001–LT103 + GOLD) | a SEPARATE final project across BOTH pipelines — ⛔ never mixed into a deploy |
| **the 6 destructive crash-tests** (unblocked, unrun) | **a real mid-day restart with live state occurs** (G13) |
| **GUI Screen-04 Signals** | the GUI is being touched for another reason |
| **the ₹0.29 SL** | ⛔ **NO TRIGGER — it is a curiosity, not a defect.** S1 §7: *"If nothing ever fires it should be DELETED rather than parked forever — **say so out loud at the next refresh** rather than carrying it a seventh time."* ⇒ ⭐ **SAYING IT OUT LOUD, as instructed: this refresh carries it again with no trigger and no firing. It is the one parked item whose disposition is DELETE-OR-KEEP and it is owed to Rama.** *(⛔ the exact carry-number is NOT asserted — S1 gives the instruction, not a running count, and this file does not invent one.)* |
| **scanner v1/v2 work** | **the entry logic materially changes (post-M-S4)** |
| **the tick→candle feed** | **a live SAFETY dependency on ticks is identified — ⛔ NONE EXISTS TODAY.** ⭐ **IA-P1-06 sharpens this:** dormancy is **UN-ENFORCED** — `order_placer.py:3393` calls `live_feed.subscribe(...)` on the exit-retry path and the candle-persist consumer is armed at every boot |
| ⭐ **#2c-R OPTION 2 — parent-order-id lookup → `cancel_order(variety="co")`** (the *correct* close for a CO position; #2c-R shipped Option 1, refuse-and-escalate, as the permanent safe fix) — ⛔ **a CROSS-REFERENCE, not an 8th S1 item: this is A4's own parked half, counted ONCE in §A4. 231 stands, and S1 §7's original count of 7 is unchanged.** **OWNER: the CO protocol surface — ⛔ NOT the reconciler** (CHECK2 structurally cannot reach a parent id; `kill_switch` can — its open-trades query already joins `orders … leg IN ('ENTRY','CO')` `:1531` and `orders.order_id` IS the broker id). Carries two riders: the §R7 `kill_switch` three-site CO defect (**needs its own card**) and the §R4 CHECK6/FIX-B alert ceiling | **EITHER: CO trading is INTENTIONALLY ENABLED · OR the broker layer provides RELIABLE PARENT-ORDER LOOKUP.** ⛔ Neither is true today (CO doubly dormant) |

## §C.8 — ⭐⭐ THE ENTRIES FINDING — 📌 R1's STANDING NOTE (S1 §8)

> ### ⛔ THE ✅ ON R1 CLOSED A REGISTER LINE. IT DID NOT ANSWER THE PROFITABILITY PROBLEM.

**SOURCE:** S1 §8 · S2 §5.0 · S3 the campaign verdict + IA-XEVOLVE-02.
**WHY-PENDING:** it is the **only item in this register that no amount of engineering
closes.** Three independent lines converge that **the entries buy EXTENSION**:
**statistical** (band inversion — a high score marks an already-extended move) ·
**geometric** (V3 RR gate: median **0.33** R:R against a **2.0** floor) · **arithmetic**
(win rate **~38–39%** against a **~43.5%** breakeven).

**S3's verdict restates it verbatim and refuses to soften it:** *"a well-built machine
that is not yet profitable… The edge question is deferred, not answered, and **no amount
of fixing the findings in this register will answer it** — that is strategy work, not
engineering work."*

⛔ **DO NOT loosen the V3 gate / `rr_floor` to make this look better** (S1 §5-C, standing
refusal): *"the gate is the only INDEPENDENT read on the entries — tuning it to agree
DESTROYS THE MEASUREMENT."*

⭐ **IA-XEVOLVE-02 adds the one thing R1's decided path cannot see:** the config side of
"add a strategy" is **genuinely safe** (a misconfigured strategy fails **LOUD** at boot) —
but the **SOURCE** side is silent: `range_breakout_long`/`_short` are enabled, mapped,
loaded at every boot and have received **ZERO webhook POSTs for 7 weeks**, and nothing
in-system can notice (**IA-P1-01**). ⇒ **a new strategy #17 would load, and might never
trade, with two live precedents.**

## §C.9 — SUB-ITEMS PRESERVED INSIDE THEIR BUNDLES (55)

⛔ **Counted, named in S1 §9, and NOT re-listed here — re-listing them would fork the
register.** They live inside G11(3) · G12(3) · G13(3) · G18(10) · G19(7) · G20(11) ·
G21(7) · G22(2) · G24(3) · G25(6). **S1 §9 is the enumeration; each bundle above points at
it.**

## §C.10 — OPERATOR KNOWLEDGE RESCUED INTO S2 §4 (8 items) — ⛔ lives nowhere else

⛔ **These were rescued out of the Downloads cards; S2 §4 is now their ONLY home.**
`gtt_state` trap (§4.1 — ⛔ live `gtt_state` = 0 rows and that is **CORRECT**; the broker
API is the only authority) · the daily error census + its known-good baseline (§4.2a) ·
the orphaned-reservation query (§4.2b — **EXPECT ZERO ROWS**) · the ASM/GSM/T2T check
(§4.3 — ⛔ **NOT exposed by the Kite API; only Rama can do it**) · the manual-GTT fallback
formula (§4.3) · the GTT lister (§4.3 — ⛔ **Git Bash, never PowerShell**) · the DP-charges
note (§4.3) · the reconciliation-redesign **D-8** step map (S2 §3.3).

---

# §D — SETTLED / DECIDED / KEPT FOR RECORD (⛔ NOT OPEN WORK)

## §D.1 — ✅ CLOSED THIS PERIOD (31-Jul → 01-Aug)

| item | evidence | source |
|---|---|---|
| **Q6 — `fix-symdir-27jul`** | ✅ **`<DEPLOYED>` 31-Jul 21:44:48 as `297b587`** — verified 3 ways, `load_all` OK. ⛔ **NOT `<VERIFIED LIVE>`: it first EXECUTES at the Mon 3-Aug 08:15 boot** ⇒ that is §A1 | memory `UNPUSHED_PENDING_DEPLOY_LEDGER`, S2 §1 |
| **THE T2 CLOSE** | ✅ **EXECUTED Fri 31-Jul — DDPI PROVEN, 5/5 `EXIT=0`.** The real test (settled demat, not BTST) | memory `t2_arm_result_29jul` / MEMORY.md |
| **F3 — the close-rehearsal gate could not prove what it claimed** | ✅ **RESOLVED IN-LINE 30-Jul**: `main()` returns at `if not args.confirm` **before** `_build_live_adapter()` where `load_all()` lives ⇒ a **direct `load_all()` run** was added ⇒ `CONFIG_LOAD_OK`. ⛔ **Do not reuse the rehearsal alone as evidence about config.** Recorded as case #6 in memory `feedback_verify_rc_not_output` | S2 §3.4 |
| **F6 — the process finding** (a scheduling fact asserted from memory nearly moved an irreversible step) | ✅ **CONTROL ADOPTED, and nothing had to be reverted — the false claim never reached an artifact.** ⛔ **THE CONTROL: any holiday / trading-day / weekday claim affecting SCHEDULING must be verified against the file TEXT — quote the line, or do not make the claim.** ⭐ Root cause = **a COMMENT in a data file read as DATA**, not a data defect. ✅ `nse_holidays_2026.yaml` reconciles to circular NSE/CMTR/71775 **exactly: 15/15 + 4/4** | S2 §3.8, S4 §7.9 |

### 🔄 ARRIVED FROM §A, 05-Aug-2026 EVENING — **A MOVE, NOT A CLOSURE**

| item | why it is discharged | accounting |
|---|---|---|
| **A1 — the Mon 3-Aug observation window** | ✅ **It ran, and the flip proceeded on it.** The window's purpose was to observe a full session before flipping; the flip happened Wed 5-Aug (R2, Option Y) and **entry + exit are now `VERIFIED LIVE`**. There is no residual work. **(P)** — see the 05-Aug consolidation §(c)/(d) | **§A → §D.** `from A`: §A **5 → 4**, §D **22 → 23**. ⛔ **N is UNCHANGED by this move — a move creates nothing.** *(The count moved to 232 for a different reason: A6.)* |

> ⛔ **A2 (the flip) DID NOT MOVE, and the reason is recorded so nobody moves it later on a glance:**
> its entry and exit are verified live, but **`<T+1 CARRY UNVERIFIED>`** — the carry has never
> survived a settlement cycle. ⭐ **Discharging the easy half of an item is how a register loses the
> hard half.** ⛔ **A5 did not move either** — the deploy slot is still open and still unpushed.

## §D.2 — ✅🟪 DECIDED 30-JUL — KEPT FOR RECORD

**R7 — GEMINI WATCHMAN · ✅ CLOSED (decision final).** *Rama, 30-Jul: "Continue using it.
Do not retire it."* 📌 **ONE CAVEAT SURVIVES, as a DO-NOT: ⛔ do not tighten the prompt** —
that was the single option flagged as carrying risk. Nothing else lives on.

**R1 — STRATEGY REVISION · ✅ CLOSED as a REGISTER ITEM.** *Rama, 30-Jul: the existing 15
strategies (12 intraday + 3 delivery) remain as-is; hammer / evening-star / morning-star
scanner improvements and any NEW strategies are BACKLOG.* ⛔⛔ **NOT "the edge is solved" —
📌 its standing note is LIVE and lives at §C.8.**

**R8 — `TELEGRAM_CHANNEL_SECONDARY` · 🟪 DECISION DEFERRED, ⛔ NOT CLOSED.** *Rama, 30-Jul:
"Keep as a placeholder / provision for future use. No implementation now."* ⭐ That
**parks** the choice; it does not make it. 📌 **LIVE CAVEAT — carry it wherever R8
appears:** if this channel is **EVER** enabled it **MUST** be decided **together with the
M-A2 8-second send budget.** The budget is **SHARED across channels**, so a 2nd channel
makes the alert ladder **~52 s** and the 8 s deadline would **cut channel 2 off
mid-ladder.** ⛔ **Enabling it without that decision silently breaks the alert path.**

**G14 — S5 second-half · X2 `eod_verify` columns · 🔗 DECIDED PARK, not a TODO.**
Re-measured 30-Jul: `eod_verification` 30 rows, `pnl_variance = 0.0` on **all 30**, zero
nulls, min=max=0.0 — still dead. **Parked because fixing it ARMS a dormant P&L check**,
which is a decision, not neglect. ⛔ Stop reading it as pending work.

## §D.3 — ✅ CLOSED 30-JUL BY THE STALE-REGISTER SWEEP (3) — ⚠️ AND A COUNT CORRECTION

| item | evidence |
|---|---|
| **G5** — no send-side alert audit trail below CRITICAL | `214a878` → cherry-picked `264dd5b`, **`<DEPLOYED>` 30-Jul 19:55:30**; `_audit_send` verified in the **deployed VM tree** and md5-identical to the pushed blob. ⚠️ **FORWARD-ONLY** — historical gaps stay unanswerable. 🏷️ **not `<VERIFIED LIVE>`** |
| **G6** — PB-01 25 → 12 | ✅ **CLOSED, BENIGN — an INPUT fact.** Width **25·12·15·14**; heartbeat `symbols=` **27→12→16→16**; the 12-day was `symbols=12 queued=12 written=12`, **zero skips**. Gate-rejection and capture-truncation both **REFUTED** |
| **G16** — `DEPLOYMENT.md` stale paths | ✅ **CLOSED — ⛔ NO EDIT MADE, AND THAT IS THE CORRECT OUTCOME.** `67850b8` (16-Jul) had already fixed it — **twelve days before the register recorded it open.** ⭐ *Editing a correct file to satisfy a stale register entry would have been the defect* |

> ### ⚠️⚠️ A RECONCILING CORRECTION AGAINST S2 — STATED, NOT SMOOTHED
> **S2 closes G5 · G6 · G16 with evidence in its §3.7 sweep, but its §6 reconciliation's
> `K` list does not deduct them** (K names Q1–Q5, R0, the Q6-NULL decision, the deploy,
> and two corrected numbers — **10 items, none of them G5/G6/G16**). ⇒ **S2's carried
> figure M=131 still contains these 3.** They are placed here in §D, and the reconciliation
> in §1 below accounts for them explicitly. **This is exactly the class of silent drift
> this register exists to catch, and it is a 3-item bookkeeping discrepancy in S2's own
> count — not a lost item: all three are named, with evidence, in S2 §3.6/§3.7.**

## §D.4 — ⛔ STANDING REFUSALS — DO NOT RE-LITIGATE (11, S1 §5, all re-verified 28-Jul)

| | refusal | the one-line reason |
|---|---|---|
| **A** | `require_hmac` → **KEEP FALSE** | Chartink cannot sign HMAC ⇒ every POST 401s ⇒ **ZERO SIGNALS**. *(S3 P1.4: posture verified in code; **0×401 all-time**)* |
| **B** | the **pre-receive hook** → **DO NOT ARM AS-IS** | guard-2 **false-rejects EVERY push** (trailing-newline bug, `deploy/hooks/pre-receive:53-60`) ⇒ **arming = deploy lockout**. Needs a 1-line fix + a `CRON_GUARD_DRYRUN=1` soak. Guard-1 is sound |
| **C** | `rr_floor` / the **V3 gate** → **DO NOT LOWER OR LOOSEN** | it is the only **INDEPENDENT** read on the entries — **tuning it to agree DESTROYS THE MEASUREMENT** *(see §C.8)* |
| **D** | `regime.enabled` → **DO NOT FLIP** | verified `false` at `system_config.yaml:420`; NIFTY token 256265 **UNVERIFIED** in the production instrument cache |
| **E** | the tick feed / **MODE_FULL** → **NOT AS A ONE-LINER, AND NOT MODE_FULL FIRST** | MODE_FULL would **ACTIVATE M-D1's volume corruption** (`volume_traded` is CUMULATIVE and `candle_store.py:63` SUMS it per tick). **M-D1 FIRST, ALWAYS** |
| **F** | `forward_shadow_record.py` → **NEVER RUN MANUALLY** | the only out-of-sample evidence producer; its output **cannot be regenerated**. ⛔ **A gap is a loss; a manufactured day is a CORRUPTION** |
| **G** | `halt.sh` **DOES NOT EXIST** | the only halt is `systemctl stop`, and it stops entries **AND** exit management **AND** the 15:17 squareoff. ⭐ Flat book ⇒ free; open MIS ⇒ expensive; **a CNC position is NEVER a reason to stop** |
| **H** | SSH → **TAILSCALE-ONLY** → ⛔ **DO NOT CLOSE PORT 22** | **the PC-down runbook depends on :22 being open** — it says SSH from the phone, and the phone has been **OFF the tailnet 23+ days**. ⭐ **THE PHONE IS THE BLOCKER.** ORDER: phone proven on the tailnet → repoint the PC → confirm a console way back in → **only then** :22 |
| **I** | **MIS→CNC fallback** → **CLOSED, WILL NOT BE BUILT** | a decision, not a deferral. **ARCHITECTURE SETTLED: INTRADAY = MIS · DELIVERY = CNC/GTT** |
| **J** | **F2 operator soft-kill** → **DROPPED (Rama)** | ⛔ the "cheap CLI" alternative is **not cheap**: the :8080 app has **no reference to the live KillSwitch** ⇒ **a separate process writing the DB row HALTS NOTHING** |
| **K** | security-watcher `activating/auto-restart` → ⛔ **NOT BROKEN, do not "fix"** | it is the designed `RestartSec=60` heartbeat; **nothing reads its ActiveState** |

⛔ **S2 adds no new refusals; it restates these.** ⚠️ **AND THE AUDIT TOUCHED ONE:**
**IA-XSEC-03** found the *"no root keys exist"* hardening **comment is FALSE** (one
cloud-image root key with a forced command exists) **while the exposure it describes is
genuinely CLOSED** (`PermitRootLogin no` **confirmed effective at the running daemon**).
⇒ **a doc correction that REMOVES A TRAP — it does not reopen refusal H.**

## §D.5 — 🏁 THE CAMPAIGN VERDICT AND ITS GATE (new this period)

**S3 §"CAMPAIGN COMPLETE — 16 PHASES"** is the record: P1–P10 + X-ARCH · X-DUP · X-CONFIG ·
X-DOCS · X-TEST · X-SEC · X-EVOLVE. Method held throughout: **findings only**, measured on
the deployed code, every "found nothing" carrying its search width, **4 stale KNOWNs
closed**, **1 root cause corrected**, **2 of its own measurement errors caught and amended
in-session**. *"Nothing was fixed, nothing pushed, no secret printed, no config or
permission touched, and the 3-Aug/4-Aug sequence was never approached."*

⛔⛔ **THE GATE, KEPT FOR RECORD BECAUSE IT BINDS §B:** **the fix campaign is a SEPARATE
effort and is NOT authorised by the audit.** §B is a running order awaiting Rama's
commissioning.

## §D.6 — THIS FILE'S OWN SUPERSESSION MARK (new this period)

`docs/MASTER_PENDING_28-Jul-2026.txt` and `docs/MASTER_PENDING_30-Jul-2026.md` are
**SUPERSEDED as the working register by this file, and RETAINED as the cited authorities.**
⛔ **Neither is deleted, and every §C item names which one it came from.** The 12 delete
verdicts in S2 §7 are **unchanged and still valid** — ⛔ and **`DEPLOY_CALENDAR_28-JUL_TO_04-AUG.txt`
and `MON_03-AUG_OBSERVATION_CARD.txt` still say KEEP.**

---

# §E — GOVERNANCE (⭐ NEW BAND, 04-Aug-2026) — ⛔ A SEPARATE QUEUE, NOT A PRIORITY TIER
### 🏷️ **`<BUILT — NOT DEPLOYED>` — `6112791`, 04-Aug 23:37 IST.** ⛔ **The band is DOCS: it executes nothing and is `<DEPLOYED>` only when the register itself ships** (§A5's slot, ~~ahead 5~~ **ahead 6**). ⚠️ **And a band cannot be `<VERIFIED LIVE>` at all — there is no production artifact a governance INDEX could produce**, which is itself the honest reason it belongs in a separate queue.

> ## ⛔ WHY THIS BAND EXISTS, AND THE ONE RULE THAT COMES WITH IT
> Governance improvements have been ranked **against implementation defects** — and they are
> not comparable. A records rule and a live money-path defect answer different questions, so
> putting them in one ranking means priority gets swapped **by accident**, not by decision.
> ⛔ **§E IS A SEPARATE QUEUE, NOT A LOWER ONE.** Nothing here is deprioritised by being moved;
> it is removed from a comparison it never belonged in.
>
> ### ⭐⭐ THE PRIORITISATION RULE — write it down, because it has been doing the work unwritten
> > **DEFECTS ARE RANKED BY EXPOSURE AND REACHABILITY, NOT BY EFFORT.**
>
> ✅ **It is already the operative criterion; it has simply never been stated:** H1/H5 lead the
> coupling work **because they are live from the first delivery fill** (reachability), not
> because they are small — H1 is not. **§B#6 was deferred because its first run lands on a
> census day** (timing/exposure), explicitly *not* because it was expensive — the row calls it
> cheap. ⛔ **And twice a "cheap to fix" label did the ranking and was wrong: §B#3 and §B#6 both
> carry a struck *"~2 lines"*.** ⇒ **effort belongs in scheduling, never in ranking.**
>
> ### 🧾 ACCOUNTING — stated explicitly, exactly as for the two §C.5 rows
> ⛔ **MOVING an existing item between bands does NOT create one. 231 STANDS.** ⭐ **And the
> honest count of what moved is ONE: `G17`.** I am **not** manufacturing moves to populate a
> new band — that would be tidiness masquerading as work, and it would corrupt a reconciliation
> that four documents depend on.
>
> | item | from | why it is governance |
> |---|---|---|
> | **G17** — *AB-910 phases 9+10 never produced; the formal slice is unaudited* | §C.3 | It is a **process/records** gap, not a system defect: nothing in production behaves differently because of it. Ranking it beside `G1`'s 95-minute unwatched window was the exact category error this band fixes |
>
> ⚠️ **AND THE MEASUREMENT THAT MATTERS MORE THAN THE MOVE: §C was NOT where the governance
> content actually lived.** A sweep of §C.1–§C.10 finds **one** genuinely-governance row. The
> register's governance content has been living in **§A-DEC / §A-DEC-2 / §A-DEC-3**, in
> **§D.4's standing refusals**, and in **`docs/campaign_practices.md`** — none of which was
> ever ranked against defects at all. ⇒ ⭐ **the mixing the band was created to stop was real
> but SMALL; the larger truth is that governance was UNINDEXED rather than mis-ranked.**
> **This band is therefore primarily an INDEX**, and that is the useful thing it does.
>
> ### 📇 THE INDEX — governance content that lives elsewhere and is NOT moved (⛔ pointers, not copies)
> | where | what it owns | ⛔ do not |
> |---|---|---|
> | **§A-DEC** (R1–R5) · **§A-DEC-2** (D1–D7) | Rama's ratified rulings | re-litigate; **§D.4** lists the standing refusals |
> | **§A-DEC-3** | the settled SHAPE: the five annotation axes + separate STATUS · the pipeline-dimension code rule · the missing home for MODIFIERS · the isolation suite born failing · the wind-down requirement · the order | ⛔ treat as authorised work — **it is shape, nothing is designed or built** |
> | **`docs/campaign_practices.md`** | G1–G5 · M1–M5 · V1–V4 · D1–D4 · AR1–AR8, each with its incident | duplicate a rule into this register; **cite it** |
> | **§D.4** | the 11 standing refusals | re-open without new evidence |
> | **§2** (below) | how to keep this file alive; the AR accepted-risk register | ⛔ let an accepted risk exist without a **reopen condition** |
>
> 🔴 **OPEN GOVERNANCE WORK — carried, not scheduled** (⛔ none authorised; all gated behind
> Wednesday's observation): **(a)** annotate the Part A control inventory with the five axes +
> STATUS — ⛔ **EXTEND the existing inventory, do NOT create a second one**, which would be the
> multi-authority defect (§B#7) committed by the very work meant to cure it · **(b)** the
> pipeline-isolation suite, **seeded with H1 and H5 as its first two FAILING tests** (⛔ written
> after the fixes, they pass on day one and prove nothing) · **(c)** a test that fails if any
> **modifier** applies to 100% of trades without an acknowledgement flag — that single test
> would have caught the tier ladder · **(d)** a home in the taxonomy for **modifiers** at all
> (the tier multiplier and `dynamic_by_winrate` are both uninventoried) · **(e)** ⭐ **COMPACT
> `MEMORY.md` — ADDED 05-Aug WITH AN EXPLICIT TRIGGER, BECAUSE A DEFERRAL WITHOUT ONE DECAYS INTO
> A FORGETTING (§D7's rule, applied to my own deferral).** **MEASURED 05-Aug: 21,147 B against a
> 24,414 B read limit = 86%**, and **ZERO lines violate the per-line byte budget** — ⇒ ⭐ **the file
> is long by ACCUMULATION of ~57 genuinely-live invariants, not by any line being bloated, which is
> the budget check working correctly rather than failing.** **THE NAMED SAFE LEVER:** merge the
> **four-line T2 cluster** into one, now that the T2 basket is closed (holdings 0 / GTTs 0 /
> positions 0, re-confirmed 04-Aug) **and superseded by real delivery trading on 05-Aug** —
> ⛔ **preserving its live invariants as a stub, above all *"Kite's web UI NEVER shows a GTT ID"*,
> which was used in tonight's operator card and must not be lost.** 🔴 **THE TRIGGER — both
> conditions, not either: (i) a session NOT on a live-trading hard date, AND (ii) explicitly
> commissioned to do it.** ⛔ **Not tonight. ⛔ Not as a side-effect of anything else.**
> ⭐ **WHY IT WAS REFUSED ON 05-Aug, recorded so the refusal is a DECISION and not an omission:**
> restructuring the artifact every future session depends on — unsupervised, on flip day, under a
> scope lock, with the operator away — is the wrong trade for 3 KB of headroom, and **every
> relocation candidate checked still carried a live DO-NOT**, which is *why* it is in the HOT file.
>
> ### ✅ THE 05-AUG CARRY-FORWARD SWEEP — **RUN, AND THE HONEST RESULT IS THAT FIVE OF SIX WERE ALREADY LANDED. ⛔ NOTHING RE-ADDED.**
> A check of last night's carry-forward list against the file, **before writing anything**, per §G3 (*"checked BEFORE writing, not after"* — the discipline that already saved `7d4ae6b` from duplicating item 2):
> | carry-forward item | found | where |
> |---|---|---|
> | the settled **annotation** (5 axes + separate STATUS; **`ENFORCE + PLACEHOLDER`** the dangerous cell) | ✅ present | **§A-DEC-3 §1** — ⭐ and it **acquired its first real instance today**: `position_sizer.py:596/:609` report the GLOBAL cap while `:585` enforces the effective one (§A2, item (a)) |
> | the **code rule** (consult the pipeline dimension or declare GLOBAL; routing exists at `position_sizer.py:291`) | ✅ present | **§A-DEC-3 §2** — ⭐ **`:291` RE-MEASURED AT HEAD `e64146f` and it HOLDS** (`bucket = "intraday" if intent in _INTRADAY_INTENTS else "positional"`), per §M3 |
> | the **isolation suite** seeded with H1/H5 **failing**, + the **100%-modifier** test | ✅ present | **§A-DEC-3 §4** |
> | 🔴 the **G2 ↔ tier coupling** registered as a dependency on **BOTH** rows, not a note on one | ✅ **CONFIRMED ON BOTH** — this was the one worth re-checking | **§C.1 `G2`** *and* **§C.2 `G18`**, each carrying the full "ships a doubling" statement |
> | the **modifier gap** (taxonomy covers caps, not modifiers; `dynamic_by_winrate` a second, uninventoried) | ✅ present | **§A-DEC-3 §3** + **§E (d)** below |
> | the **`sl_distance_pct` two-units** disclosure (a vocabulary fix that MOVED the ambiguity, not removed it) | ✅ present | **§C.2 `G18`(b)**, with the 10-hit width stated |
> | ⚠️ the **§2(8) deviation** (no `docs/audit/` build record for the four) | ✅ present **and now extended** to today's pass | **§A5** |
>
> ⭐ **THE POINT OF RECORDING A SWEEP THAT FOUND NOTHING MISSING: a "confirm it is registered" instruction is discharged by CHECKING, and the check is only evidence if its result could have been "absent".** One of the seven (**G2 ↔ tier**) was a genuine two-place claim that could have been half-true, and it was not. ⛔ **Re-stating any of the six here would have been the duplication §G3 forbids.**
>
> ### ⛔ THE ONE THING `6112791` LEFT UNDONE — **CLOSED 05-Aug 00:15, and it is arithmetic, not opinion**
> The band's own accounting says *"MOVING an existing item between bands does NOT create one. 231 STANDS"* — **that is correct and it still holds.** ⛔ **But §1's two tables were not updated to SHOW it**, so as committed the register said `G17` was in §E **and** counted it inside §C's 21 G-items. ⭐ **That is not a count error — the TOTAL was right both ways — it is a PLACEMENT error, and this file's whole §1 promise is *"prove nothing vanished", which is a claim about WHERE things are, not only how many.**
> ✅ **CORRECTED IN §1 BELOW:** the distribution gains an **§E** row (**1, from source A**), **§C moves 105 → 104**, the §C group table strikes `G17` from the G-items list (**21 → 20**), and **N = 231 is UNCHANGED on both axes** — column `from A` still totals **132**. ⛔ **No item was added, removed, closed or re-graded to make this balance.**
> ⚠️ **AND THE GENERAL RULE THIS EARNS, because the band will take more items later: CREATING A BAND IS A RECONCILIATION EVENT.** A move is invisible to a total and **fully visible to a distribution** ⇒ **any future move into §E must edit §1's distribution in the SAME commit**, or the register will keep passing its own count check while pointing at the wrong section.

---

# §1. ⛔ THE RECONCILIATION — PROVE NOTHING VANISHED

**COUNTING UNIT** (same as S1 §9 and S2 §6, deliberately unchanged): *a named, trackable
item as its source names it.* Where a source bundles sub-items under one ID, the bundle
counts as ONE and the sub-items are counted separately **only where the source itself
enumerates them** (S1 §9's 55).

### THE INPUTS

```
  A  PRE-AUDIT live set carried at 31-Jul night                    = 132
       131  S2 §6's M — "CARRIED FORWARD live into this file /
            its pointers", which S2 states is EXACT
            ( = 112 unchanged from S1's live set of 118
              +  19 new-and-still-live from 29-30 Jul )
        +1  F6, registered 31-Jul night — POST-DATES that count
            (S2 §6 counts "arising 29-30 Jul"; F6 arose 31-Jul)

  B  NEW audit finding IDs  (docs/audit/integrity_audit_2026.md)   =  95
        91  numbered finding blocks (P1-P10 + X-ARCH/X-DUP/
            X-CONFIG/X-DOCS/X-TEST/X-SEC)
        +4  X-EVOLVE findings (XE.4, bulleted format)
       ⭐ EXACT, and stated so it is falsifiable: 105 unique
          "IA-*" strings appear in the file MINUS 10 sub-IDs
          (P3-06a · P4-01a/b/n · P4-05a/b · P5-10b · P6-07a/b ·
           P9-03e) = 95 top-level IDs.

  C  ARISING 31-Jul evening -> 01-Aug, + 05-Aug evening           =   5   <- was 4
        the 21-commit unpushed docs deploy-slot ledger  (-> §A5)
        the XE.3 debt-ledger RUNNING ORDER              (-> §B)
        the campaign verdict + its "no fix authorised" gate (-> §D.5)
        this file's supersession of S1/S2               (-> §D.6)
        A6 the delivery-carry lifecycle break (05-Aug)  (-> §A6)   <- NEW

                                                   N = A + B + C  = 232
```

### THE DISTRIBUTION ACROSS §A–~~§D~~ **§E** *(⭐ AMENDED 05-Aug 00:15 — §E added; **N unchanged**)*

```
  BAND                                          from A   from B   from C   TOTAL
  §A  LIVE-TRADING THREAD                            4        0        2       6
  §B  THE AUDIT FIX CAMPAIGN                         0       95        1      96
  §C  PRE-AUDIT ITEMS STILL OPEN                   104        0        0     104
  §D  SETTLED / DECIDED / KEPT FOR RECORD           23        0        2      25   <- A1 ARRIVES
  §E  GOVERNANCE (new band, 6112791)                 1        0        0       1
                                                 -----    -----    -----   -----
                                                   132       95        5     232  ✅
```

### 🔴 **AMENDED 05-Aug-2026 EVENING — N MOVES 231 → 232. ONE NEW ROW, AND IT IS SAID OUT LOUD.**

⛔ **"231 stands" was repeated correctly all day — every finding was a sub-entry. Tonight produced
one thing that is not.** ⭐ **A register that cannot grow is not a register**, so the count was
decided by test rather than by habit.

**THE TEST APPLIED TO EACH CANDIDATE:** *does it have a root cause no existing row owns, and would
fixing it be separate work?* **Sixteen findings were scored. Fifteen failed the test and are
sub-entries. One passed:**

> ### 🆕 **A6 — THE DELIVERY CARRY BREAKS THE DAILY PROCESS LIFECYCLE.** *(source C, band §A)*
> Root cause owned by no existing row: **the system assumes ONE PROCESS PER TRADING DAY, FLAT AT
> EOD.** A carried delivery position falsifies that premise, and three guards that silently depended
> on it fail together. ⛔ **Not a facet of A2 (the flip worked), not of A3 (the carry pilot is not
> started), not of debt-ledger #1 (that is telemetry coverage, not lifecycle).**

**THE TWO CHANGES, AND BOTH AXES BALANCE:**
1. **+1 NEW ROW** — A6, source **C** ⇒ `from C` **4 → 5**, `§A from C` **1 → 2**, **N 231 → 232**.
2. **A1 MOVES §A → §D** (the Mon 3-Aug observation window is discharged — it ran, and the flip
   proceeded on it) ⇒ `§A from A` **5 → 4**, `§D from A` **22 → 23**. ⭐ **A move changes no total**,
   which is why §A still reads 6 while its composition changed.

**COLUMN CHECK, both axes:** `from A` = 4+0+104+23+1 = **132** ✅ · `from B` = **95** ✅ ·
`from C` = 2+1+0+2+0 = **5** ✅ · rows = 6+96+104+25+1 = **232** ✅ · N = 132+95+5 = **232** ✅
⛔ **Nothing was closed, re-graded or dropped to make this reconcile.**

⚠️ **A2 IS DELIBERATELY NOT MOVED.** The flip's entry AND exit are now verified live, but
**`<T+1 CARRY UNVERIFIED>`** — it is not discharged and moving it would bank a green that was never
measured. ⭐ **Discharging the easy half of an item is how a register loses the hard half.**

⭐ **THE ONLY CHANGE IS A MOVE, AND IT BALANCES ON BOTH AXES:** `G17` left **§C** and arrived
in **§E**; **`from A` still totals 132** and **N is still 231.** ⛔ **Nothing was added, closed,
re-graded or dropped to make this reconcile** — had the total needed to move, that would have
been the signal that the band was creating items rather than re-filing one. *(§E was created
by `6112791`; this table is the placement half of that commit, made 05-Aug.)*

✅ **RE-CHECKED 05-Aug POST-BOOT — ⛔ THIS TABLE IS UNCHANGED, AND THE CHECK IS RECORDED
BECAUSE "I DIDN'T TOUCH IT" IS NOT THE SAME CLAIM AS "NOTHING MOVED".** The 05-Aug boot pass
added a substantial amount of *content* — the BOOT PASS, the BOOT CHAIN trace, the three
open items, the token-file operator gate — and **not one register item.** Applying §E's own
test (*"did any item change section?"*): **no**. Every addition is a **sub-entry or scope
expansion on a row that already existed** — the boot facts on **§A1/§A2**, the token gate on
**§A2's boot** (surfaced in §C.5 as a clock, exactly as the CHECK1 gate and the Thursday date
were), the sizing prediction and the `:596/:609` mislabel on **§C.1 G8 / §C.2 G18 / §A-DEC-3
§1**, and the composite-verdict lesson on **§A2**. ⇒ **§C stays 104 · §E stays 1 · `from A`
stays 132 · N = 231.** ⛔ **231 STANDS.**

> ⏰ **SUPERSEDED THE SAME DAY, 05-Aug EVENING — and the paragraph above is left standing because
> it was TRUE WHEN WRITTEN.** It records the POST-BOOT morning check, and at that point 231 was
> correct. **The evening produced A6, a root cause no row owned, and N moved to 232.**
> ⭐ **The two are not in conflict: the morning check found no new item because there was none yet.**
> ⛔ **Do not read the morning's "231 STANDS" as the current count — see the amendment above.**

### THE PER-BAND TALLY, ITEM BY ITEM

**§A = 6** *(⭐ AMENDED 05-Aug evening — composition changed, total unchanged)* — A0 the open
decision · ~~A1 Mon observation~~ **→ §D, discharged 05-Aug** · A2 the 4-Aug flip *(⛔ NOT
discharged — `<T+1 CARRY UNVERIFIED>`)* · A3 the carry pilot · A4 the buy-day filter · A5 the
unpushed deploy slot · 🆕 **A6 the delivery-carry lifecycle break (new 05-Aug)**.
⭐ **A4 is debt-ledger #2 and is counted HERE ONLY.**

**§B = 96** — the XE.3 running order (1, src C) + **all 95 findings**: **22 ranked into
the ledger's 12 rows**, **73 below the line and cited by phase in §B.2**. ⛔ **The 12
ledger rows are a RANKING OVER findings, not 12 additional items** — counting them
separately would inflate this register by 11 and is exactly the vanity S1 §9 and S2 §3.7
both warn against. **Ledger row #2 is §A4 and is not counted again.**

~~**§C = 105**~~ ⭐ **§C = 104** *(05-Aug: `G17` moved to §E — see the distribution note above)* —
| group | count |
|---|---|
| G-items still open (G1·G2·G3·G4·G7·G8·G9·G10·G11·G12·G13·G15·~~G17~~·G18·G19·G20·G21·G22·G23·G24·G25) — ⭐ **`G17` struck HERE and live in §E; the row at §C.3 is struck-but-legible (§G4) so an old citation still resolves** | ~~21~~ **20** |
| R-items still open (R2·R3·R4·R5·R6·R9·R10·R11·R12) | **9** |
| PARKED, each with its trigger (§C.7) | **7** |
| the entries finding / R1's standing note (§C.8) | **1** |
| sub-items preserved inside bundles (S1 §9's enumeration, §C.9) | **55** |
| 30-Jul findings still open — F1 · F2 · F4 · F5 (§C.6) | **4** |
| operator knowledge rescued into S2 §4, living nowhere else (§C.10) | **8** |
| **§C TOTAL** | ~~105~~ **104** |
| ⭐ **§E** — `G17`, moved 04-Aug by `6112791`, placed 05-Aug | **1** |
| **§C + §E** | **105** ✅ *(⛔ unchanged — the move is visible, the sum is not)* |

**§D = 24** — Q6 · the T2 close · F3 · F6 (4, closed this period) · R1 · R7 · R8 (3,
decided 30-Jul) · G14 (1, decided park) · **G5 · G6 · G16 (3, the S2 count correction)** ·
the 11 standing refusals (11) · the campaign verdict + its gate (1, src C) · this file's
supersession mark (1, src C) = **4+3+1+3+11+1+1 = 24**.

### ⚠️ HOW MUCH TO TRUST THIS — stated, because a count that looks exact and is not is worse than an honest range

- ⭐ **B = 95 is EXACT and independently re-measurable** — the derivation (105 unique
  strings − 10 named sub-IDs) is given above so anyone can falsify it in one grep.
- ⭐ **C = 5 is EXACT** *(was 4; A6 added 05-Aug evening)* — all five are enumerated by name.
- ⭐ **§A, §B's ledger, §C's group totals, §D and §E are EXACT** — every one of the 232 is
  named or points at a source that names it. **§C's 55 sub-items are the one group carried
  by pointer, and S1 §9 enumerates all 55 by ID.**
- ⚠️ **A = 132 is TAKEN FROM S2, NOT RE-DERIVED.** S2 states its M=131 is exact and
  enumerable from its own §2–§5; this file adds F6 and does not recount the 131. ⛔ **The
  guarantee here is that everything S2 carried is still carried and is placed — not that
  S2's own arithmetic was re-audited.**
- ⚠️ **The 19 "new-and-still-live" inside S2's M were never itemised by S2.** This file's
  §A/§C/§D placement **reconstructs** them as: **§A** buy-day filter · Mon observation ·
  4-Aug flip · carry pilot · the §7.9(f) open decision **(5)** · **§D** the T2 close · F3
  **(2)** · **§C** F1 · F2 · F4 · F5 **(4)** + the 8 §4-rescued operator items **(8)** =
  **19 ✅**. Every element is named in S2 §3/§4/§5. **It is a reconstruction, and it is
  labelled as one.**
- ⚠️ **The 3-item S2 discrepancy (G5/G6/G16) is a REAL correction, and it does not change
  N.** They were inside S2's M=131 and are now placed in §D. **Nothing vanished: all three
  are named with evidence.**

### ⛔ DELIBERATELY NOT CARRIED — so absence is a decision, never an oversight

- **S1's X1–X15 corrections and S2's §3.5 number corrections** — they are *corrections to
  claims*, not trackable items; **neither S1 nor S2 counted them as items**, and neither
  does this file. ⛔ **They remain live reading in S1 §6 and S2 §3.5** and several are still
  load-bearing (X4 the `innings` VOID set · X10 the CRLF trap · X11 paper-cannot-exercise ·
  X15 the `PRAGMA user_version` rollback trap).
- **S1's own §9 not-carried list and S2's §6 not-carried list** — still valid, still in the
  repo, ⛔ **not restated here** (restating them would be the fork this file exists to
  avoid).
- **S2 §7's 12 delete verdicts + the 2 KEEPs** — unchanged, still valid, ⛔ not re-derived.
- **The audit's 10 sub-IDs** (IA-P3-06a, IA-P4-01a/b/n, IA-P4-05a/b, IA-P5-10b, IA-P6-07a/b,
  IA-P9-03e) — **preserved inside their parent finding**, exactly as S1 preserves the 55
  bundle sub-items. Counting them again would inflate B by 10.
- **The audit's ~19 open questions** (`OQ-*`) — ⛔ **they are questions the audit refused to
  guess into findings, not pending work.** They live in each phase's `.5` section.
- **G20's ~55 line-level LOW items** — ⛔ **POINTER ONLY** (S6 is the source of record),
  counted as **1** (G20), exactly as S1 §9 counted them.

---

# §2. HOW TO KEEP THIS FILE ALIVE

1. **This file is the WORKING register; S1 and S2 are the AUTHORITIES.** ⛔ When they
   disagree with this file, **they win** and this file is corrected — not them.
2. ⭐ **When a new edition drops a section, that is SILENT LOSS with no dangling pointer to
   notice.** Diff SECTION HEADINGS *and* content before calling anything a superset.
   *(S1 §11(6), S2 §8(3) — the rule that produced S1 in the first place.)*
3. **Label every item** `<BUILT>` · `<DEPLOYED>` · `<VERIFIED LIVE>` · `<PENDING>` ·
   `<DEFERRED>`. ⛔ **"fixed" is retired. DEPLOYED IS NOT EVIDENCE** — nine things in this
   system were built, looked alive, and had never run.
4. **§A expires fastest.** A0 expires **Monday's close**. A1 expires Monday night. A2/A3
   expire Tuesday. **§A5 expires at the next real deploy.**
5. ⛔ **§B is a running order, not a task list.** It changes state only when Rama
   commissions the fix campaign — **and then A4 goes first, before anything holdings-aware.**
6. **The same-carrier rule (S1 §11(1)):** any correction reaching `docs/audit/` must also
   reach **this register + the memory palace + SYSTEM_MAP/PATHS**.
7. ⛔ **A file is only deletable once you have named where each of its unique items now
   lives.** This file names S1 and S2 on every item it took from them — **which is exactly
   why neither may be deleted.**
8. ⭐⭐ **THE DOCUMENTATION RULE (adopted 02-Aug-2026).** Record **immediately**, in the
   appropriate permanent place, every **design completion · architectural decision ·
   REJECTED option and why · sequencing decision · governance ruling**:
   - changes **execution order, deploy gating, or priority** → **THIS FILE** (the
     operational **index**), preserving authoritative source references;
   - **implementation-specific** (build notes, verification evidence, regression results,
     commit SHAs) → the **`docs/audit/` build record**, **cross-linked from here**.
   **This file stays the INDEX; `docs/audit/` records stay the EVIDENCE.**
   ⛔ **CALIBRATION GUARD — the rule must not defeat the discipline it serves: it does NOT
   license new register rows.** The reconciled count (**232** since 05-Aug evening; **231** when
   this guard was written) stands; record within existing rows, sub-entries and cross-references,
   exactly as #2b/#2c/#2c-R/#8/#8b did. ⭐ **The guard forbids rows created for EMPHASIS — it has
   never forbidden a row with a root cause no existing row owns, which is the A6 test.**
   Byte budgets still apply. ⛔ **If the rule and the count ever conflict, REPORT it — do
   not resolve it silently.**
   **Why:** rules this campaign earned were living only in chat transcripts and one-time
   cards. A rule that isn't written down gets rediscovered the expensive way — **the same
   truth-telling failure the audit indicts, applied to our own process.**
9. ⭐ **THE STANDING PRACTICES NOW HAVE ONE DURABLE HOME: `docs/campaign_practices.md`** —
   governance (**G1 an auto-filled prompt is never an instruction and never an approval** ·
   G2 ChatGPT is advisory on Rama's decisions · G3 disclose-don't-expand · G4
   superseded-but-legible) · measurement (M1 repo-wide-never-file-wide · M2 prove-don't-
   assert) · validation (V1 paper cannot validate product semantics · V2 the regression
   invocation · V3 the worktree-base mitigation · V4 composition-truth) · deploy (**D1
   there is no partial deploy** · D2 the SHA inventory gate · D3 no push before 18:15 ·
   D4 label honesty). Each rule is recorded **with the incident that earned it**.
   ⛔ It is **process** practice — distinct from `docs/foundation_engineering_rules.md` and
   `docs/trading_system_project_specific_rules.md`, which are **product** engineering
   rules. Do not merge them.
   ⭐ **It also carries §R — THE ACCEPTED-RISKS REGISTER (AR1-AR7)**, each entry naming
   *what was accepted · the reasoning · who accepted it · the date · **the condition that
   would REOPEN it***. **An accepted risk nobody can find later quietly becomes an
   UNACCEPTED one** — rediscovered as a fresh defect, then either re-litigated or "fixed"
   in passing (which G3 forbids). ⛔ **An entry with no reopen condition is not an accepted
   risk; it is an abandoned one.** Seeded from the acceptances already made: **AR1** the
   GATE-Q3 tradeless-day MISMATCH (Rama accepted all 7 gate recommendations, 01-Aug) ·
   **AR2** the q9 oscillator / isolation-only flake · **AR3** the T3 `test_fix181` standing
   failure · **AR4** CHECK6's capital-release-while-live (reopens at the first real
   HARD_KILL) · **AR5** CO dormancy · **AR6** the label ceilings (⛔ reopen only by the real
   event, **never by re-labelling**) · **AR7** #2c-R's Option 2 — **cross-referenced to
   §C.7, not duplicated.**
   ⛔ The trigger list at item 8 also covers **review conclusions (incl. "nothing to
   change") · deployment decisions (incl. a decision NOT to deploy) · implementation
   completions · risk acceptances.**

---

**END — 01-Aug-2026 (Saturday, market closed), IST.**
Consolidation only. **Nothing deployed, nothing started, nothing decided, no finding
re-derived, and the 3-Aug / 4-Aug live sequence untouched.**
~~⛔ **Committed locally; NOT pushed** — it rides the same future deploy slot as the 21
unpushed audit commits (§A5).~~
⭐ **SUPERSEDED 05-Aug-2026 00:15 IST (§G4).** Those 21 **shipped 04-Aug 19:08** inside the
27-commit push `4149263..0197923`. ⛔ **Still committed locally and NOT pushed** — but the slot
it rides is the **new** one: `main` is **ahead 5** of `origin/main` (`0197923`), and the four
04-Aug-night commits + this pass sit in it. **§A5 holds the per-commit D1 test and the
corrected first-execution dates.**
⛔ **The 05-Aug MORNING currency pass changed STATUS, SHAs, PLACEMENT and one refuted timing
premise. It created NO register row, closed nothing, and re-derived no finding — N = 231 stands.**
⏰ **The 05-Aug EVENING pass DID create one (A6) and discharged one (A1 → §D): N = 232.** ⭐ Both
sentences are true of their own pass; the count in this line is the morning's.
