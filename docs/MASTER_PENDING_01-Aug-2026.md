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

**Clock-read:** 01-Aug-2026, **Saturday**, session start **14:44 IST**. Deployed SHA
`297b587`; `main` **21 ahead**, all docs-only, unpushed by design.

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

# §A — LIVE-TRADING THREAD (hard dates, highest priority)

## ⚠️⚠️ A0 — ***THE ONE OPEN DECISION OWED TO RAMA. IT IS NOT MADE IN THIS FILE.***

> ### Does a buy-day-product-filter SLIP postpone **only the carry pilot**, or **all of 4-Aug**?

**SOURCE:** S4 §7.9(f) + S4 line 287 · S5 lines 103–116 · S2 §1 (`⚠️ OPEN (§7.9(f))`).
**WHY-PENDING:** the two readings share the same deadline, so nothing moves *today* —
**they differ only if the filter slips**, which is exactly the case now in play.

| reading | if the filter slips, then… | where it is recorded |
|---|---|---|
| **TWO-part gate** *(as recorded in S4/S2)* | the **four flags still flip Tue 4-Aug**; only the **carry pilot** waits. | S4 line 285–286: *"THE GATE HAS TWO PARTS TODAY, NOT THREE"* |
| **THREE-part gate** *(Rama's earlier lean)* | a filter slip **postpones ALL of 4-Aug** — observation ran · came back clean · **filter shipped**. | S4 §7.9(f): *"tonight's instruction described the flip's hard gate as THREE-part"* |

⛔⛔ **DECIDE IT BEFORE MONDAY'S CLOSE — not after the query.** Both sources give the same
reason, and it is the whole point: *"otherwise tonight's result gets read through
whichever answer is more convenient."* (S5:114-116; S4:1062 *"Do not resolve this by
picking one on the day."*)

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

## A2 — TUE 4-AUG: THE FLAG FLIP `<PENDING>` — ⭐ the first irreversible step

**SOURCE:** S4 line 283–288 · S2 §1 · S3 IA-XCFG-04.
**WHY-PENDING:** gated on A1. **The four flags** (verified consumers, IA-XCFG-04):
`delivery_enabled` · `force_intraday_only` · `capital.conditional_allocation_enabled`
(= **R10**, §C) · `trade_type` (strategy control).

**THE HARD GATE, stated as the sources state it:**
1. **Monday's observation ran** (A1);
2. **it came back clean** — ⛔ ANY unexplained row postpones it; it is the query's binary
   answer, **not a judgement made at 16:00 on the day**;
3. **⚠️ and the third part is A0's open decision** — whether the filter (A4) is part of
   this gate at all.

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

---

## ⛔⛔ A4 — THE BUY-DAY PRODUCT FILTER (+ CRITICAL-on-NULL) ✅ **`<BUILT — NOT DEPLOYED>` 01-Aug night** — ***the single most sequencing-critical item in this register***

> ✅ **BUILT `43043a2`+`15adf75`+`2e606ec` (01-Aug ~20:0x–20:5x):** both HARD_KILL
> emergency sites product-filtered via the ONE shared source
> (`core.constants.EMERGENCY_FLATTEN_PRODUCTS`, also read by the two scheduled EOD
> passes — no second copy to drift); **CNC SPARED loud** (honest attempted-count);
> **NULL/NRML/unknown FLATTEN + CRITICAL** (the silent fallback is gone); the Kite
> per-product-row hazard (spared CNC must not shadow a same-symbol MIS row) found in
> design and test-pinned both directions; regression **NEW-set EMPTY**. Record:
> `docs/audit/buyday_filter_build_01aug2026.md`. ⛔ **DEPLOY = Mon-eve stack, gated on
> D1–D3 ratification + Monday observation clean + Monday PC gates (kill drill, #1
> composition boot, calm regression confirm).** 🔴 **NEW ruling owed (record §2): the
> reconciler CHECK2 third site — #2b Mon-eve (~6 lines) or a named carry-pilot blocker.**

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

## A5 — THE 21 UNPUSHED DOCS COMMITS / THE NEXT DEPLOY SLOT `<BUILT, unpushed BY DESIGN>`

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
| **3** | **Fill/cancel seam truth** (zeroed `qty_filled`; the cancel-race → HUMAN_ORDER) | Money-path correctness with a **naked-unbooked-position endpoint**; 2-line fix for one half | IA-P5-01 · IA-P5-02 |
| **4** | **Kill-flatness verification + the fault-injecting fake** | The last-line safety layer is **unverified at kill time** and has **one failed live rehearsal** | IA-P7-01 · IA-P7-02 *(BANSALWIRE — ⚠️ the ledger cites this as "IA-P9/BANSALWIRE"; the BANSALWIRE finding is **IA-P7-02**, `integrity_audit_2026.md:2435-2437`; IA-P9-02:2978 also references it. **Citation flagged, not smoothed.**)* · IA-XTEST-05 |
| **5** | **Broker-truth capital escalation** (G3 non-escalating; the seed absorbs) | The kill ladder is **structurally deaf to real cash divergence**; measured **−₹637.6 crossing 3 sessions silently** | IA-P6-01 · IA-P6-02 |
| **6** | **`eod_verify` stuck-PENDING + the inverted shadow flag** | **Blocks the authoritative-flip gate outright**; cheap to fix, high unblocking value | IA-P8-01 |
| **7** | **Multi-authority concepts** ("held" ×4, status ×34 sites) | Every future reconciliation/delivery change pays this tax; **the fix template already exists in-repo** | IA-XARCH-03 · IA-XDUP-02 |
| **8** | **The 03_daily runbook's raw-DB kill-clear** | **Wrong instruction in the most-likely-open doc during an incident**; 2 lines | IA-XDOCS-01 |
| **9** | **Alert fatigue / false-safety claims** ("Smart TGT ACTIVE", F4, the naked warnings) | **Degrades the channel every other mitigation depends on** | IA-P9-01 · IA-P9-02 |
| **10** | **Deployed-tree-vs-HEAD unverified** | The invariant **every phase's premise rested on**, held by ritual | IA-P10-01 |
| **11** | **Secrets concentration** (5 accounts in one `.env`; VM test path) | Multiplies consequence **5×** for zero benefit; constrains how tests may evolve | IA-XSEC-01 · IA-XSEC-02 |
| **12** | **Doc/knowledge concentration** (map inversion; register-as-reference) | Slows every future change; **no incident hazard** | IA-XDOCS-03 · IA-XDOCS-05 |

**⇒ 22 distinct IA findings are ranked into the ledger** (rank #2 cites no IA finding —
it is the pre-audit Q4/Q7 item, §A4).

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
| **G2** | B2/M-S4 — 25 of 100 scorer points are a constant `0.0` | needs re-parity + re-soak; coupled to R3/D2 | S1 §4, S2 §5-B2 | **SHARPENED** — S3 P2.4 re-measured fresh: **25/100 dead-at-0.0 on 16,705/16,705 window rows + 20 more points pinned at half ⇒ 45/100 degenerate**; and **IA-P2-03** (tier ladder degenerate: **415/415 trades ran at the LOW 0.5 multiplier**) |
| **G3** | sector **resolution** (81/82 populated rows read `UNKNOWN`) | money path ⇒ own review + prediction + deploy. **Hard prerequisite of any sizing increase.** Blocks R2/D1 | S1 §4, S2 §5-B2 | **WIDENED** — **IA-P3-06(a)**: a **second dead consumer** found (`_sector_for`, `signal_processor.py:1389-1401`, probes methods that **do not exist**); engine-side resolution is **fully silent** (0 warning lines ever). Status moved: **4 real-sector trades now, not 1** |
| **G8** | the leverage gap (the system sizes UNLEVERED) | the **recalibration** is the work, not the multiplier | S1 §4, S2 §5-B2 | **CONFIRMED** — S3 P3.4: binding constraint is notional, no leverage term; ⛔ **no multiplier computed**. See also **IA-P3-01** (the concentration floor is a hard **PRICE CEILING** ~₹936–1,000) and **IA-P3-04** (the risk-per-trade path **cannot bind for any legal config**) |

## §C.2 — KILL / CAPITAL / SAFETY (⛔ careful-loop; gated after 4-Aug or the first live kill)

| id | item | why-pending | source | ⭐ audit cross-link |
|---|---|---|---|---|
| **G11** | K1 · K2 · K3 — emergency kill + same-day restart ⇒ service does not come back | after 4-Aug. *(Docs already fixed; the **BEHAVIOUR** half is what is open)* | S1 §4, `ACTIONS_not_decisions` | ⚠️ **K1's DOC half is NOT fully closed** — **IA-XDOCS-01** (§B#8): the fix **MISSED a third site**, `03_daily_operations_runbook.md:133-139`, which still prescribes a **raw live-DB kill-clear + restart**. **SEVERITY HIGH** — it lives in the DAILY-operations doc |
| **G13** | first live HARD_KILL · Phase-1 replay · M-C1 non-zero carryover | needs a real mid-day restart with live state | S1 §4 | **STANDS, composed into IA-P7-02's posture line** — *"the G13 drill was mocked"*. See **IA-P7-01** (§B#4): the flatten counts success at **order ACCEPTANCE**, hard-codes `succeeded = attempted` |
| **G18** | the 10 capital/kill MEDs (M-C4·M-C5·M-C6·M-C8·W10·P3-c6…c10) | careful-loop, after 4-Aug | S1 §4 (10 named sub-items) | M-C6 + FIX-133 **re-confirmed latent** (S3 P3.4): effective_mult = 0.5 always ⇒ both branches unreachable. M-C4/M-C8 noted at the placement pair, ⛔ **not re-opened** |
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
| **G17** | AB-910 phases 9+10 never produced | formal slice unaudited | S1 §4 | — |
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
| **R9** | `mis_filter` **ENFORCING** flip | The SHADOW half is **live Mon 3-Aug** (§A1). Enforcing touches the **signal path** ⇒ your call | S2 §5-B1 |
| **R2** | D1 sizing / `max_concentration_pct` | ⛔ **HOLD — blocked by G3.** ⚠️ Read `sector_exposure()`'s `'UNKNOWN'` handling **before** this moves; the error direction inverted | S2 §5-B1 |
| **G4** | **Ratify PB-01's gates / window / thresholds** | The *"V3 DECISION CONTENT SPECIFICATION v1.0"* **does not exist** (6-search width, 30-Jul). Values recorded **OBSERVED-FROM-CODE**. ⛔ **A DECISION is missing, not a document — do not close it by writing the spec from the code.** Owed before any PB-01 promotion, gated far beyond 4-Aug | S2 §3.6/§5-B1 |
| **R11** | `predeploy-*` backups — delete, or write the retention rule | ⚠️ **Renaming to `pre_*` is a DELETE in disguise.** Re-verified 30-Jul: **4 files, backups 8.4 G** at `data_store/backups/` (⛔ **not** `~/backups`) | S2 §5-B1 |
| **R5 · R6** | prune-retention cap value · backup-retention cap value | Both are "pick a steady-state number". R6: measure a week of post-clear nights first | S2 §5-B1 |
| **R3 · R4 · R12** | D2 direction · D3 min_pass (downgraded) · WAAREERTL 23-Jul external close | R3 needs months + a positive control. R12 may be a **capital** question, not execution | S2 §5-B1 |

## §C.5 — DATED / STANDING OPERATOR ITEMS (⏰ these have clocks)

| item | why-pending | source |
|---|---|---|
| ⏰ **2FA seed → VM-only** | dated **Fri 7-Aug / Sat 8-Aug** | S2 §5-B1, memory |
| ⏰🔴 **Commit NSE's published `nse_holidays_2027.yaml` before 31-Dec-2026** | **MEASURED: the first 08:15 boot of 2027 raises `ConfigMissingError` and DOES NOT START.** ✅ The 15-Dec email reminder is DEPLOYED (`cbcad2c`). ⛔⛔ **NEVER invent the dates** | S2 §5-B1, S4 §7.9(e) |
| **rotate the Telegram token** · **disable rpcbind** | standing security backlog | S2 §5-B1 |

## §C.6 — THE 30-JUL FINDINGS STILL OPEN (F1 · F2 · F4 · F5)

| id | item | why-pending | ⭐ audit cross-link |
|---|---|---|---|
| **F1** | `reconcile_positions` is **BLIND to delivery from T+1** — reads `positions()` only, **never `holdings()`** ⇒ ⛔ **a 15:45 SUCCESS is NOT evidence the book is flat** | **REAL BUT LATENT** — traced 30-Jul: has never fired and **could not have** (only 3 CNC orders ever in the live DB, all FAILED/CANCELLED). **Gate = the first live delivery trade (§A2/§A3).** ⇒ **Folds into reconciliation step 1** — ⛔ not a separate workstream | ⭐ **A THIRD FALSE-ALARM FACE FOUND → IA-P8-04**: a CNC **sell** from holdings sits in `positions()` as a **NEGATIVE** quantity ⇒ **ORPHAN_AT_BROKER (−qty)**, beyond the doc's buy-day ORPHAN and T+1 MISSING. Measured 31-Jul, 5 rows |
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

  C  ARISING 31-Jul evening -> 01-Aug (this period)                =   4
        the 21-commit unpushed docs deploy-slot ledger  (-> §A5)
        the XE.3 debt-ledger RUNNING ORDER              (-> §B)
        the campaign verdict + its "no fix authorised" gate (-> §D.5)
        this file's supersession of S1/S2               (-> §D.6)

                                                   N = A + B + C  = 231
```

### THE DISTRIBUTION ACROSS §A–§D

```
  BAND                                          from A   from B   from C   TOTAL
  §A  LIVE-TRADING THREAD                            5        0        1       6
  §B  THE AUDIT FIX CAMPAIGN                         0       95        1      96
  §C  PRE-AUDIT ITEMS STILL OPEN                   105        0        0     105
  §D  SETTLED / DECIDED / KEPT FOR RECORD           22        0        2      24
                                                 -----    -----    -----   -----
                                                   132       95        4     231  ✅
```

### THE PER-BAND TALLY, ITEM BY ITEM

**§A = 6** — A0 the open decision · A1 Mon observation *(A1(z) is a read-rule of A1, not a
separate item)* · A2 the 4-Aug flip · A3 the carry pilot · A4 the buy-day filter · A5 the
unpushed deploy slot. ⭐ **A4 is debt-ledger #2 and is counted HERE ONLY.**

**§B = 96** — the XE.3 running order (1, src C) + **all 95 findings**: **22 ranked into
the ledger's 12 rows**, **73 below the line and cited by phase in §B.2**. ⛔ **The 12
ledger rows are a RANKING OVER findings, not 12 additional items** — counting them
separately would inflate this register by 11 and is exactly the vanity S1 §9 and S2 §3.7
both warn against. **Ledger row #2 is §A4 and is not counted again.**

**§C = 105** —
| group | count |
|---|---|
| G-items still open (G1·G2·G3·G4·G7·G8·G9·G10·G11·G12·G13·G15·G17·G18·G19·G20·G21·G22·G23·G24·G25) | **21** |
| R-items still open (R2·R3·R4·R5·R6·R9·R10·R11·R12) | **9** |
| PARKED, each with its trigger (§C.7) | **7** |
| the entries finding / R1's standing note (§C.8) | **1** |
| sub-items preserved inside bundles (S1 §9's enumeration, §C.9) | **55** |
| 30-Jul findings still open — F1 · F2 · F4 · F5 (§C.6) | **4** |
| operator knowledge rescued into S2 §4, living nowhere else (§C.10) | **8** |
| **§C TOTAL** | **105** |

**§D = 24** — Q6 · the T2 close · F3 · F6 (4, closed this period) · R1 · R7 · R8 (3,
decided 30-Jul) · G14 (1, decided park) · **G5 · G6 · G16 (3, the S2 count correction)** ·
the 11 standing refusals (11) · the campaign verdict + its gate (1, src C) · this file's
supersession mark (1, src C) = **4+3+1+3+11+1+1 = 24**.

### ⚠️ HOW MUCH TO TRUST THIS — stated, because a count that looks exact and is not is worse than an honest range

- ⭐ **B = 95 is EXACT and independently re-measurable** — the derivation (105 unique
  strings − 10 named sub-IDs) is given above so anyone can falsify it in one grep.
- ⭐ **C = 4 is EXACT** — all four are enumerated by name.
- ⭐ **§A, §B's ledger, §C's group totals and §D are EXACT** — every one of the 231 is
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

---

**END — 01-Aug-2026 (Saturday, market closed), IST.**
Consolidation only. **Nothing deployed, nothing started, nothing decided, no finding
re-derived, and the 3-Aug / 4-Aug live sequence untouched.**
⛔ **Committed locally; NOT pushed** — it rides the same future deploy slot as the 21
unpushed audit commits (§A5).
