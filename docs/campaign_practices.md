# CAMPAIGN PRACTICES — the standing rules this campaign EARNED

**Adopted 02-Aug-2026.** Cross-linked from `docs/MASTER_PENDING_01-Aug-2026.md` §2.

**What this file is:** the rules for **how we work** — governance, measurement,
validation, deploy. Every one was paid for by an incident, and each is recorded **with
that incident**, because a rule without its scar gets argued away.

**What this file is NOT:** engineering rules for the *system*. Those live in
`docs/foundation_engineering_rules.md` (deterministic, idempotent, boring code, …) and
`docs/trading_system_project_specific_rules.md` (single source of truth, broker is truth,
…). ⛔ Different subject — do not merge them.

> ⛔ **CALIBRATION GUARD.** This document does **not** license new register rows. The
> reconciled count (**231**) stands; record within existing rows, sub-entries and
> cross-references. Byte budgets still apply. **If this rule and the count ever conflict,
> REPORT it — never resolve it silently.**

> ## 🔒 THE DOCUMENTATION THREAD IS **CLOSED** as of 02-Aug-2026
> Four consecutive refinement rounds produced this file, each smaller than the last. **The
> architecture is now sound and is not to be re-opened for polish:**
> `MASTER_PENDING` = **index** · `docs/audit/` = **evidence** · this file = **the process
> handbook** · **§R** = the accepted-risk register · **SHA-pinned citations** = the
> authority chain.
> ⛔ **Further additions land ONLY when a NEW INCIDENT earns a rule — not as refinement
> passes.** A fifth polishing round would be process work displacing ledger work, which is
> the failure mode this closure exists to prevent.
> ⇒ **The next work is LEDGER work.**

---

## 0. THE DOCUMENTATION RULE (adopted 02-Aug-2026)

Record **immediately**, in the appropriate permanent place, every:

1. **design completion**
2. **architectural decision**
3. **REJECTED option — and why**
4. **sequencing decision**
5. **governance ruling**
6. **REVIEW CONCLUSION — including *"reviewed, nothing to change"***. ⭐ A review that left
   no trace **cannot later be relied on as having happened** — and "we looked at that" is
   exactly the claim this campaign has repeatedly found to be untrue.
7. **DEPLOYMENT DECISION — including a decision NOT to deploy, and its reason.** A hold is
   a decision; an unrecorded hold decays into a forgotten item.
8. **IMPLEMENTATION COMPLETION**
9. ⭐ **RISK ACCEPTANCE — see §R.**

| what it is | where it goes |
|---|---|
| Changes execution order, deploy gating, or priority | **`MASTER_PENDING`** — the operational **index** — preserving authoritative source references |
| Implementation-specific: build notes, verification evidence, regression results, commit SHAs | the **`docs/audit/` build record**, **cross-linked from `MASTER_PENDING`** |

**`MASTER_PENDING` stays the INDEX; `docs/audit/` records stay the EVIDENCE.**

**Why:** several rules below existed only in chat transcripts and one-time cards. A rule
that isn't written down gets rediscovered the expensive way — **the same truth-telling
failure the audit indicts, applied to our own process.**

---

## G. GOVERNANCE — ⭐ the most important, because NOTHING technical enforces them

### G1 · An auto-filled console prompt is NEVER an instruction, and NEVER an approval
**Six occurrences.** The first four *suggested* a next action. **#5 asserted a ruling** —
it read *"Option A approved — remove `--reset`, hold out of Monday"*, impersonating a
decision only Rama can make. Acting on it would have started #8b Step 2 on an
authorisation **that did not exist**.

⛔⛔ **#6 REPEATED IT** (*"Approved — Option A, remove `--reset`, and hold out of
Monday."*). **Two consecutive approval-impersonating prompts ⇒ #5 was not an anomaly; the
escalation is now an established PATTERN**, and it should be expected to recur on every
decision that is visibly pending. ⭐ The escalation direction is worth naming: the
auto-fills moved from *suggesting work* to *granting permission* — the second is
categorically worse, because the first can only waste effort while the second can
manufacture authority.

⭐ **The closer it matches what everyone expects Rama to say, the more dangerous it is** —
because that is exactly what makes it feel safe to act on. Plausibility is the attack
surface, not the tell.
⇒ **Authorisation arrives ONLY as a card through the bridge carrying Rama's own words.**

### G2 · ChatGPT's replies CARRY RAMA'S AUTHORITY — **AMENDED 03-Aug-2026 by Rama**

⭐⭐ **STANDING RULING (Rama, 03-Aug-2026, ~23:5x IST): "ChatGPT's replies count as my
replies, no deviations."** A considered reply from ChatGPT is therefore a **decision**, not
advice — deploy slots, gates, ride-or-hold, and whether an item ships included. Treat it as
you would a card in Rama's own words.

⛔ **THE BOUNDARY THIS DOES *NOT* COVER — and the chain is why it matters.** There is now a
path by which text can travel: **console auto-fill → pasted into ChatGPT → returned as a
ruling → carrying Rama's authority — without Rama having read it.**
**§G1 STANDS UNCHANGED AND UNAMENDED: an auto-filled prompt is NEVER an instruction and
NEVER an approval.** The delegation covers ChatGPT's **considered replies**; it does **not
launder text that originated in a suggestion box. Authority attaches to the reasoning, not
to the round trip.**
⇒ If a "ruling" is materially just the auto-fill echoed back, it is **not** a G2 decision —
say so and ask, exactly as G1 requires.

⚠️ **Recording this amendment is itself load-bearing.** The old text below said the
opposite, and it is cited in this register. **A governance rule that changes silently is
worse than one that never existed**: without this note, G2's old wording would be quoted
against a valid decision a week from now.

> ~~**G2 · ChatGPT's answers to RAMA's decisions are ADVISORY.**~~
> ~~Deploy slots, gates, ride-or-hold, whether an item ships — these are **Rama's**. ChatGPT
> has answered them unprompted several times. Its red-teaming has been genuinely valuable
> (the #2c-R Option-1 direction, the Q1-Q8 constraints), **and that is precisely why the
> line matters**: a good advisor is easy to mistake for an authority.~~
> — **SUPERSEDED 03-Aug-2026 by Rama's standing ruling above. Kept legible per G4.**
> ⭐ Its closing observation is *not* retracted and is worth keeping: a good advisor is easy
> to mistake for an authority. The ruling resolves that by **making the advisor an
> authority** — it does not claim the two were always the same thing.

⚠️ **One consequence to apply, not to debate:** the 03-Aug design acceptances (R-1…R-5 of the
#3 registration) were filed as *architecture review, explicitly NOT §G2 material* because G2
then meant "advisory". Under the amended G2 that distinction no longer separates them by
**authority** — but ⛔ **do not retroactively re-file them**: they were correct as recorded,
the register says why, and rewriting a past classification to match a later rule is the
history-rewrite G4 exists to prevent.

### G3 · DISCLOSE, DON'T EXPAND
A defect found **outside the card's scope** is **reported and carded — never fixed in
passing.** Earned repeatedly, and every time the disclosure became its own item:
`#2 → #2b` (the reconciler's third sell site) · `#2b → #2c` (the CO intent) ·
`#2c-R → #2d` (kill_switch's three CO sell sites) · `#8 → #8b` (the executable
kill-clear) · `#8b → the `--cleanup-*` flags`.
⭐ Sibling: **anti-duplication — check for an existing home before creating a file.**

### G5 · ⭐⭐ A RULING IS AN **INTENT** PLUS A **PROPOSED MECHANISM** — Step 1 tests the mechanism

**The intent survives even when the mechanism does not.** A Step-1 measurement that refutes
*how* a ruling said to do something has **not** overturned the decision to do it.

**Earned on the third instance (04-Aug-2026), which is what makes it a rule and not an anecdote:**

| ruling | its stated mechanism | Step 1 found | intent |
|---|---|---|---|
| **#6** | as described | **misdiagnosis** | ✅ stood |
| **#5** | *"the ladder is deaf"* | **misdiagnosis** — it is never spoken to; the real target is IA-P6-02 | ✅ stood |
| **D4 / R12** | *"scope the allowlist to read-only diagnostics"* | ⛔ **insufficient** — narrows 1 of **3** independent surfaces; `Bash(ssh *)` re-permits the exact command it exists to stop | ✅ stands |

⇒ ⭐ **Three "refuted" rulings in the register are evidence the gate HOLDS, not evidence that
rulings are unreliable.** A reader who has not been here will otherwise draw the second
conclusion, and it is the wrong one: each of these was caught **before** implementation, by the
measurement step that exists for exactly this.

⛔ **THE COROLLARY, AND IT IS THE LOAD-BEARING HALF: a Step 1 that refutes a mechanism MUST SAY
IN THE SAME BREATH THAT THE INTENT STANDS, and return the mechanism for re-scoping.** Otherwise
the refutation reads as **overturning the decision** — which is **not the implementer's to do**
(§G1/§G2). ⇒ *"mechanism refuted, intent intact, returned for a ruling on the mechanism only"*
is the required shape. **Never** *"the ruling was wrong."*

⚠️ **Sibling worth stating:** this is why a card's mechanism should be written as a **proposal
with its reasoning**, not as a bare instruction. A mechanism whose *why* is recorded can be
re-scoped by the next person; one that arrives as a bare imperative can only be obeyed or defied.

### G4 · Superseded text is STRUCK THROUGH but kept LEGIBLE
With an **amendment note and date**. ⛔ Never silently deleted, never quietly reworded. A
reader must be able to see what the guidance used to say and why it changed — otherwise
the correction is indistinguishable from a rewrite of history.

---

## M. MEASUREMENT

### M1 · REPO-WIDE, never file-wide, for any "single site / chokepoint / vocabulary" claim
**Earned four times:**
1. the `order_placer` ":3914 single chokepoint" premise — **wrong** (it was
   `_emergency_market_exit`, 0 executions ever) ⇒ a STOP;
2. the B-2 dispersal list — missed **three** `kill_switch` sites, incl. the retry loop;
3. the raw-DB kill-clear — the audit scoped **one** doc; the sweep found **three**;
4. `EMERGENCY_FLATTEN_PRODUCTS` — its own comment claimed **four** readers; there are
   **five**.
⇒ **State the search width in the same sentence as the claim.** Two greps that disagree
over one corpus ⇒ **the narrow one is lying.**

### M2 · MEASURE, don't infer — and PROVE, don't assert
- comment-only edit ⇒ **AST identity with docstrings stripped**, plus the constant's value
  asserted (not eyeballed);
- revert-clean ⇒ **do the revert** and diff to zero bytes;
- a new test ⇒ **RED-on-old** on a base worktree (never a stash), test file md5-identical
  both sides.
⭐ A green check is evidence **only if it could have been red.**

### M3 · ⭐ A CARD IS NOT AUTHORITATIVE OVER A RECORD
**A card that seeds facts must CITE them. The implementer VERIFIES every seeded fact
against the record BEFORE writing, and RECORDS any divergence rather than silently
applying it.**

**Earned by AR7 (02-Aug-2026):** a card restated a fact from *conversation memory* rather
than from the record — *"Ledger #2's Option-2 parking"* — and the record said **#2c-R's**.
It was caught only because the seeds were verified before being written down. Had it been
copied through, a misattribution would have entered the permanent register wearing the
authority of a card.

⭐ **This is M1/M2 turned on our own process.** *"Two greps over one corpus disagree ⇒ the
narrow one is lying"* applies equally to **a remembered claim versus a written one — and
the remembered one is always the narrow one.** A card is an instruction to act; it is not
evidence about the tree.

⚠️ **The same logic applies to a card that is right.** AR1 was seeded as an acceptance and
*was* one — but the contract alone read as a mere recommendation, and only the build
record settled it. **Verification is owed to correct seeds too**, because the check is
what converts a claim into a citation.

**⭐ SECOND ENTRY — A LINE NUMBER IS A CLAIM ABOUT A SHA, 04-Aug-2026.** The first entry
covers a card seeding a *fact* from memory. This one covers a card seeding a **correct fact
with a stale coordinate** — and it is the sharper case, because nothing about the card was
wrong except *where it pointed.*

⇒ **A card's line numbers are valid ONLY at the SHA they were measured at. Cite that SHA
beside them, and the implementer RE-MEASURES at HEAD before editing.**

**Earned by the #2d card (04-Aug-2026).** It was written from a Step-1 record measured at
`d6c298d`; `4149263` then landed `+13/-1` at `@@ -612,0 +613,11 @@` — **entirely above all
three target sites** — so every `kill_switch.py` citation in the card was stale by a uniform
**+12**, including the `⛔ DO NOT TOUCH` line. Caught before a single edit, by re-measuring
rather than by noticing.

⛔ **The rule is RE-MEASURE, never "add the offset."** This drift was *uniform and
one-file*, which is the **benign** case and the reason it was easy to see. A drift that is
partial, or spread across files, **does not announce itself** — and an implementer who
learned "apply +12" would carry the wrong correction into the first case that mattered.

⭐ **Why this belongs beside M3 rather than under V:** it is the same failure as AR7 —
**seeding from a record without asking what has landed since** — and the fix is the same
shape: the SHA is what converts a pointer into a citation. §R already requires
`<file> §<section> @ <SHA>` for accepted risks *"because a timestamp cannot tell you whether
the target CHANGED; a SHA can."* **A card's line numbers are the same kind of claim and had
been exempt.**

### M4 · ⭐⭐ AN AUDIT-DESCRIBED FIX IS A **HYPOTHESIS**, NOT A SPEC

**M2 says *measure, don't infer* about our OWN claims. M4 extends it to the SOURCE
DOCUMENT** — which this campaign had been treating as authority. **Every remaining
audit-sourced item must be MEASURED before implementation, never transcribed.**

**Earned by ledger #10 (03-Aug-2026).** `integrity_audit_2026.md:3145-3149` described the
closing mechanism as a *"read-only two-liner"*:

```
git --git-dir=~/trading-system.git --work-tree=$TARGET diff --stat HEAD
```

Measured before writing it, it is **wrong as described**. `git diff HEAD` needs an index,
and the deployed work tree has **no `.git` of its own** — the index lives in the bare
repo. Three distinct behaviours, all measured:

| form | result |
|---|---|
| `GIT_INDEX_FILE` → a non-existent path (valid but **EMPTY** index) | **`1250 files changed, 344936 deletions(-)` — on a provably CLEAN tree** |
| `GIT_INDEX_FILE` → a **zero-byte** file | `fatal: index file smaller than expected` (corrupt, not empty — a *different* failure) |
| the repo's **real** index | correct output, **but a monitoring job then WRITES the deploy repo's index** |

⇒ the correct form **copies the index and diffs against the copy**.

⛔⛔ **WHY THIS MATTERS MORE THAN A BUG: shipped as described, the check would have fired
on its FIRST HEALTHY NIGHT.** An alarm that goes off when nothing is wrong is
**IA-P9-02's own disease — alert fatigue / false-safety claims — delivered by the audit's
own recommendation.** ⭐ It is this campaign's central verdict class turned on the audit
itself: **a declared thing that does not have the effect it declares.**

⚠️ **The description was not careless** — it was a sound sketch by someone who had not run
it. That is exactly the point: **a described fix carries the authority of the document and
none of the evidence of a measurement.** Treat it as the hypothesis it is.

**Sibling, same class, same item: "additive" is a BLAST-RADIUS CLAIM and must be measured
too.** Check 12 looked purely additive — a new read-only check appended to a list. It is
not: `system_manager` **can trip tomorrow's SOFT_KILL** (`generate_full_report` →
`trigger_soft_kill`, `:1144-1145`), so any new `CheckResult` is **kill-adjacent by
default**. Measuring that changed the design (never set `soft_kill_reason`; assert it by
test). ⛔ **Never accept "it's additive" without checking what consumes the thing you are
adding to.**

(Record: `docs/audit/ledger10_deployed_tree_check_03aug2026.md` §2 and §4.)

### M5 · ⭐⭐ STATE THE SUBJECT IN THE SAME SENTENCE AS THE CONCLUSION

**Because a measurement's conclusion may not be wider than its subject** — and the only place
that can be enforced is at **write time**.

⭐⭐ **THE RULE IS DELIBERATELY PHRASED AS A WRITING INSTRUCTION, NOT A READING ONE.** *"Don't
read a conclusion too widely"* asks **every future reader** to be careful and is checkable by
none of them. *"Write the subject beside the claim"* is checkable **at the moment of writing, by
the one person who has the measurement in front of them.** Same content; only one of them is
enforceable.

⚠️ **This correction was itself earned.** The rule was first drafted as a reading error waiting
to happen. It is worse than that: in the incident below **the widened claim was already
WRITTEN** — the register carried a flat *"retiring it discards nothing unique"* with **no
subject attached**. ⇒ **the join failed at write time, not at read time**, and nobody reading it
later could have known which question it had answered.

**M1 governs how wide you SEARCH. M5 governs how wide you may then SPEAK.** They fail
differently, and that is why M5 is its own rule: ⭐ **M1 gives you a WRONG ANSWER; M5 gives you a
RIGHT ANSWER TO A QUESTION YOU DID NOT ASK** — which is far harder to catch, because the
measurement really was performed and really was sound.

**Earned by D5 / R13 (04-Aug-2026), and the honest record is the whole chain — three links, no
one of them careless:**

1. The seeding caution — *"`mempalace.yaml` is the only written trace of the GUI workstream
   anywhere"* — was **unmeasured, and wrong as stated.**
2. Its refutation **was measured and was right**: 137 tracked files under `ops_dashboard/` plus
   `G0_BACKEND_INVESTIGATION_REPORT.md` and `SYSTEM_MAP.md:282` ⇒ *"retiring it discards nothing
   unique."*
3. ⛔ **That refutation was then applied wider than its subject.** It had answered *"is the GUI
   **workstream** traced elsewhere?"* It had **never** asked *"is **every line** of
   `mempalace.yaml` duplicated elsewhere?"* — and the answer to the second is **no**:
   `:215-224` held `future_backlog_LOCKED_ROADMAP_ONLY: F1..F9`, **nine roadmap items with no
   tracked copy.** Executing the retirement on the widened reading would have **deleted them.**

⭐ **The failure is at the JOINS, not in any link.** That is worth more than either correction,
because a process that only catches careless work will not catch this at all.

**Two sibling instances, same rule, different joins — both from the same D5 sweep:**
- **NAME vs DESTINATION.** The sweep was keyed on the word *"mempalace"* (58 mentions, 28 files)
  and therefore could not see `ops_dashboard/docs/G5e_DEPLOYMENT_PLAN.md:247`, which directs work
  to the **same retiring destination** without ever naming it. ⇒ *"58 mentions of the word"* is
  **not** *"every file that depends on the thing."*
- **FILE vs LINE granularity.** The same sweep classified `docs/SYSTEM_MAP.md` as a **live site
  needing the edit**, because the *file* is live. Its two mentions (`:282`, `:1200`) are **dated
  changelog entries** — history, and editing them is the rewrite **§G4** exists to prevent. ⇒ the
  scope was measured at **file** granularity while the criterion is a **line** property.
  ⛔ **Expect this in every future sweep:** *"which files mention X"* rarely answers *"which
  lines must change."*

(Record: `docs/MASTER_PENDING_01-Aug-2026.md` §D5 — the re-measurement block and its resolution.)

### M6 · ⭐⭐ A ZERO MEASURES THE GUARD **ABOVE** IT, NOT ITSELF

**Named 04-Aug-2026 on its second and third instances in one day.** A zero is only evidence
about the thing you were asking about **if that thing was reachable when you measured**.
⛔ **Otherwise the zero measures whatever short-circuited above it** — and it reads exactly
like success.

Two instances, different guises, same shape:

- **The masked gate.** `strategies/control.py` — **723/723** STRATEGY_CONTROL rejections were
  LAYER 0 (`force_intraday_only`, `:90`); LAYER 1 (`trade_type`, `:100`) had **zero**. That
  zero looked like *"LAYER 1 is satisfied"*. It meant *"LAYER 1 is **unreachable**"*, because
  LAYER 0 returns first. ⛔ Acting on the first reading would have shipped a **silent no-op
  flip** — the delivery flip changing nothing while appearing complete.
- **The unreached error branch.** `order_reconciler` FIX-B — all three `FIX-B:` error strings
  were **0**, which reads as *"the failure never happened"*. But the **SUCCESS `log.info` was
  also 0** ⇒ the enclosing block **has never executed**, so the error branches are
  **UNREACHED, not never-failed.** ⭐ **The success line is what disambiguates the two, and it
  is the line nobody thinks to grep.**

**The rule:** before reporting a zero, establish that the code path *could have produced a
non-zero* — name the guard above it, or find a positive control (a success line, a
neighbouring counter) proving the block ran at all.

⚠️ **Sibling of §M5, not a duplicate.** M5 governs how wide you may SPEAK about a sound
measurement. **M6 governs whether the measurement was of the thing you named at all.**
⭐ And it composes with §V's *"a green check is evidence only if it could have been red"* —
M6 is that rule turned on a **zero** instead of on a **pass**.

(Records: `docs/audit/flip_push_plan_04aug2026.md` §1 ·
`docs/audit/reservation_nonatomicity_latent_or_live_04aug2026.md` §a.)

---

### M7 · ⭐ A DIGEST WITHOUT ITS METHOD IS NOT EVIDENCE

> **Any hash recorded as evidence must carry, in the same place, the EXACT command that produced
> it and the interpreter version that ran it. A digest without its method is not reproducible,
> and a non-reproducible digest is decoration.**

**Earned 05-Aug-2026.** The comment-only claim for `c5c1926` was recorded as *"`ast.dump(ast.parse())`
sha256 `e19bccce…a171d21` IDENTICAL before and after"* — the right proof, correctly reasoned, and
**the strongest available instrument for that claim** (an AST match shows there was nothing for the
interpreter to execute differently; a passing test suite only shows it still passes).
⛔ **But on independent re-computation the value did not reproduce.** Python 3.11.9 gives
`ef355295a93f6e83…cba8b33b`, and it does not match under **any** of four dump variants tried
(default · `include_attributes=True` · `indent=2` · `annotate_fields=False`).

⭐ **THE CLAIM HELD; THE VALUE DID NOT — AND THOSE ARE DIFFERENT FAILURES.** Re-measured, the AST
**is** identical across `c5c1926`, and `main.py`'s md5 **did** move `9bbc1747…` → `6cff0ce8…`
(181,526 → 183,144 B), so the anti-vacuity half stands too. ⛔ **The original conclusion is NOT
retracted.** What failed is the *audit trail*: a later reader cannot re-derive the number, so the
number does no work — the claim has to be re-measured from scratch to be trusted, which is exactly
what a recorded digest is supposed to prevent.

**Why this is an M-family rule and not a nitpick:** a digest is recorded *precisely* because it is
supposed to be checkable later by someone who does not trust the writer. `ast.dump` output is
version-sensitive and option-sensitive; the same source can yield several legitimate digests. ⇒ **the
method IS the measurement.** Recording the hash without it is the same error as quoting a percentage
without its denominator (§the denominator discipline) — a number that reads as precise and cannot be
checked.

**In practice:** beside any recorded hash, write the command. Not a description of it — the command.
`python -c "import ast,hashlib;print(hashlib.sha256(ast.dump(ast.parse(open('main.py').read())).encode()).hexdigest())"` with `python 3.11.9` beside it is reproducible; *"the AST sha256"* is not.

⚠️ **Sibling of §M2** (*measure, don't infer — and PROVE, don't assert*). **M2 says produce the
proof. M7 says record it so someone else can re-run it.** A proof only the author can reproduce is
an assertion with extra steps.

(Record: `docs/audit/check1_product_skip_step1_05aug2026.md` §T6 finding 13.)

---

## V. VALIDATION

### V1 · PAPER CANNOT VALIDATE PRODUCT SEMANTICS
Paper nets by **symbol**; live Kite nets per **(symbol, product)**. ⇒ **a paper drill of
any product-semantics change is vacuously GREEN.** Validate against live semantics or by
construction — never by a paper run. (#2c Step-1 finding (f).)

### V2 · The regression invocation is part of the baseline
- **`pytest tests/unit tests/integration -q`** — ⛔ **NOT `run_tests.py`**, which collects
  the **44 never-gated `tests/crash_test/`** tests that `load_dotenv()` the **real
  `.env`**. Harmless in a worktree (no `.env`); ⛔ **never in the main tree.**
- **Copy the git-ignored `config/instruments.csv`** into any base worktree — else **26
  phantom `test_main` failures**.
- ⭐ **AND `venv/` — the other half of the same recipe, and it was missing here until
  04-Aug-2026.** Without it **3 `test_t4` tests fail as PHANTOMS** (a Store-python stub — ⛔ not
  TZ, not code) ⇒ the base reads **12F where it should read 9F**, and a phantom in the BASE is
  the asymmetry **§V3** warns about: it can **MASK a real new failure**.
  ⛔⛔ **NEVER `robocopy … /MIR` a tree containing the `venv` junction without `/XJ`** — it
  follows the junction and **mirrors into the REAL venv**, which is how the working venv was
  destroyed on 03-Aug. Delete the junction with `cmd /c rmdir` first, or pass `/XJ`.
  (`Remove-Item -Recurse` carries the identical hazard.)
  ⚠️ **The damage is INVISIBLE TO PIP** — package directories go, `*.dist-info` stays, so `pip`
  reports them installed and a plain `pip install -r …` is a **silent no-op**. Repair is
  `--force-reinstall`, and then **re-check the pins** (§V2's interpreter bullet below).
- **A stopped run's partial log is DELETED**, never left to be mistaken for a baseline.
- Both halves must use the **same** invocation or the sets are not comparable.
- ⭐ **THE INTERPRETER IS PART OF THE BASELINE — name it beside the result (R14 / D6,
  04-Aug-2026).** The gate runs **`pytest==9.0.3`** with **`pytest-cov==7.1.0`**, now pinned
  exactly in `requirements-dev.txt`. **Earned 03-Aug-2026:** those lines carried **no ceiling**
  (`pytest>=9.0.3`), so a `--force-reinstall` venv repair pulled **9.1.1** and silently replaced
  the interpreter every campaign baseline had been measured under. ⛔ **Nothing failed — which
  is what made it dangerous.** ⇒ **a gate result compared across a version change is not a
  comparison**, and a bump is a deliberate **re-baselining** (re-run the full suite, record the
  new standing-failure **SET**).

### V3 · A worktree base is sound ONLY with the mitigation, and ONLY because the arithmetic closes
Copy the ignored runtime files in, then **prove and subtract** residual artifacts, and
**reconcile the totals on both axes**. If the arithmetic does not close, **the base is not
usable.**
⛔ **The asymmetry that makes this matter: a base-only artifact can MASK a real new
failure** (same test failing both sides ⇒ absent from `comm -23`). ⇒ **minimise artifacts;
never explain them away.**

### V4 · COMPOSITION-TRUTH, not unit-construct
Tests construct their own objects and **pass the args production forgot** — which is how
5,500 greens coexisted with ~22 dead subsystems. Assert that production **feeds** the
thing, not merely that the thing works when fed. (IA-XTEST-01.)

**⭐ SECOND ENTRY — EARNED ON THE MONEY PATH, 03-Aug-2026.** The first entry was
structural; this one is an incident, and it is the stronger evidence.

Eight unit tests (`tests/unit/test_kill_switch_product_filter.py`) said the buy-day
product filter was sound. They monkeypatch the broker seam at `:40-41`:

```python
monkeypatch.setattr(ks_mod, "determine_close_direction",
                    lambda _a, _s, side, qty: (side, qty))
```

⇒ the stub hands back the **local** qty **by construction**, so no test in that file can
observe what the real helper computes from broker truth. Those tests are sound about
*which* positions get flattened and **structurally blind to how many shares are sold.**

The Monday PC drill ran the **real** `determine_close_direction` / `broker_net_qty` in a
composed call and found, in **one run**, what the suite could not see at all: on a
same-symbol `(MIS 10) + (CNC 5)` book, site 1 sells **15** — eating 5 shares of the
delivery position the filter had just spared. `broker_net_qty` sums by **symbol** with no
product filter (`broker/position_helpers.py:34-39`), and site 2 — which uses the per-row
qty instead — is correct, so **the two sites disagreed and only one had ever been
measured.**

⭐ **The lesson, sharpened:** *a mock whose return value you order cannot test the thing
you ordered.* A seam stubbed "because it isn't what's under test" **defines** what the
test can conclude — and the eight green tests were, on the quantity question, vacuous.
⛔ **When a stubbed seam sits between the code under test and the decision that reaches
the broker, the suite is not evidence about that decision.** Drive it composed, or state
plainly that the question is untested. (Record: `docs/audit/kill_drill_03aug2026.md` §2.)

---

## R. ACCEPTED RISKS — the register

**Why this section exists:** this campaign has accepted risks **repeatedly and
correctly**, but the acceptances were scattered across build records and transcripts.
⭐ **An accepted risk nobody can find later quietly becomes an *unaccepted* one** — someone
rediscovers it, reads it as a fresh defect, and either re-litigates a settled call or
"fixes" it in passing (which G3 forbids).

**Every entry carries SIX fields — and the last two are the point:**
*what was accepted · the reasoning · who accepted it · the date ·* ⭐ ***the authoritative
SOURCE RECORD** (audit / build / register / card, cited to file and section)* · ⭐ ***the
condition that would REOPEN it**.*

- ⛔ **An entry with no REOPEN CONDITION is not an accepted risk; it is an abandoned one.**
- ⛔ **An entry with no SOURCE RECORD is a *remembered* risk, not a *recorded* one** — it
  can be reinterpreted later from chat history alone, **which is exactly how AR7 arrived
  misattributed** (see §M3). A citation is what makes an acceptance auditable by someone
  who was not in the room.
- ⛔ **Never invent a citation.** If a record is missing, **say so in the entry** — an
  honest "no written record; rests on X" is usable; a fabricated pointer is not.

**CITATION FORMAT:** `<file> §<section> @ <SHA>` — **the commit SHA at which the acceptance
was made**, determined from git (`git log -S`), **never assigned from memory** (§M3
applies to our own entries first).
⭐ **Why the SHA:** it pins the exact text relied upon **at acceptance time**, permanently
and verifiably. Build records **do** get amended later; when that happens git shows the
divergence on demand. That is the *"did this citation merely exist, or was it
revalidated?"* distinction — **solved by construction rather than by attestation.**

> ### ⛔ REJECTED OPTION — a verification TIMESTAMP beside each citation (02-Aug-2026)
> Proposed (ChatGPT, 16:10) as an alternative to the SHA: record *when* each citation was
> last verified. **Declined, and recorded here so it is not re-litigated:**
> - **it decays** — *"verified 02-Aug"* tells a reader in November nothing about whether
>   the target still says that;
> - **it implies a revalidation CADENCE nobody has committed to and no one owns** — an
>   unmet implied obligation is worse than an absent one;
> - **a stale "revalidated" stamp asserts currency it does not have** — the same
>   false-claim class this campaign exists to remove (cf. **D4**: `<DEPLOYED>` is not
>   evidence);
> - ⭐ **the SHA subsumes the intent and does it better: a timestamp cannot tell you
>   whether the target CHANGED; a SHA can.**
>
> If periodic revalidation is ever genuinely wanted, it is **a scheduled task with a named
> owner** — not a field on a risk entry.

---

### AR1 · GATE-Q3 — the tradeless-day MISMATCH is KEPT, not suppressed
- **Accepted:** on a genuinely tradeless day the five money-path ACTIVE units
  (`signal_processor` · `order_placer` · `limit_protocol` · `order_monitor` ·
  `order_reconciler`) legitimately read **acted-0** and **will appear in MISMATCH(i)**.
- **Reasoning:** the line is **truthful** — *"nothing acted today"* is exactly G9's
  question being answered daily. Suppressing it would reclassify real information away.
- **Who / when:** **Rama, 01-Aug-2026** — he accepted all 7 gate recommendations.
- 📄 **SOURCE:** `docs/audit/effect_verification_contract_01aug2026.md` **§A2.4 @ `a5c3704`** (the
  zero-trade-day rule) + its **Q-table row Q3**; the acceptance itself is
  `docs/audit/effect_telemetry_phaseB_build_01aug2026.md`, **opening status line @ `132e571`** —
  *"approved, all 7 recommendations accepted (Rama relayed; ChatGPT conditions binding)"*.
  ⭐ Both citations are needed: the contract alone reads as a **recommendation**, and only
  the build record shows it was **accepted**.
- ⭐ **REOPENS IF:** it produces **alert fatigue in practice** — it sits closest to the
  IA-P9-02 disease the campaign is trying to cure. The contract already records the
  standing alternative (**event-driven**), so reopening is a switch, not a redesign.

### AR2 · The q9 consecutive-losses oscillator + the isolation-only failures
- **Accepted:** treated as **pre-existing shared-state flake**, not a campaign delta.
- **Reasoning:** ⭐ **PROVEN, not assumed** — the q9-streak member oscillated
  **red→red→green across three runs of the same tree**, and the isolation-only failures
  **pass when run alone**. Two consecutive isolated runs of one tree gave different
  failure sets.
- **Who / when:** the implementer, at each regression stamp, 01–02-Aug-2026 (no separate
  ruling was sought — it is a measurement judgement, not a decision).
- 📄 **SOURCE:** `docs/audit/effect_telemetry_phaseB_build_01aug2026.md` **§6a @ `e9abe36`** (the
  three-run oscillation + "all 3 PASS in isolation"), and **§4** for the two GONE;
  re-confirmed absent both halves in
  `docs/audit/reconciler_product_filter_build_02aug2026.md` **§R6a**.
- ⭐ **REOPENS IF:** a **NEW-failure set is ever non-empty because of that family**.
  *(Status 02-Aug: absent from **both** halves of the #2c-R run — a calm pair.)*

### AR3 · The T3 `test_fix181` LIMIT-vs-MARKET standing failure
- **Accepted:** **carried and named in every gate**, not fixed.
- **Reasoning:** pre-existing and unrelated to the changes under test — and, decisively,
  it appears on **BOTH sides of every base/after pair**, so it **cannot mask a delta**.
- **Who / when:** the implementer, carried from the ledger #2 build onward (01-Aug) and
  re-measured at every gate since.
- 📄 **SOURCE:** `docs/audit/buyday_filter_build_01aug2026.md` **§4 @ `2e606ec`** (validation table —
  the adjudicated `test_fix181` LIMIT-vs-MARKET row) · re-measured both sides in
  `docs/audit/reconciler_product_filter_build_02aug2026.md` **§R6a**.
- ⭐ **REOPENS IF:** it ever appears on **only one side** of a pair — that makes it a delta,
  not a standing item — or when the exits thread reopens post-M-S4.

### AR4 · CHECK6's capital-release-while-the-position-is-LIVE
- **Accepted:** disclosed, **not fixed**. FIX-B marks a `PENDING_FILL` trade FAILED and
  **releases its reservation at 3 cycles while the position is still live at the broker**,
  after which it routes to `_check2_orphan_adoption` (`HUMAN_ORDER`).
- **Reasoning:** **latent-on-latent** — it needs a HARD_KILL **and** an in-flight entry
  **and** a fill, and **HARD_KILL has never fired**. Bounding is **CHECK6's** to change;
  widening a CO card into CHECK6 is the blast-radius error the campaign exists to prevent.
- **Who / when:** the implementer measured and disclosed it, 02-Aug-2026; ⚠️ **no Rama
  ruling was sought** — it was judged latent and registered, not decided.
- 📄 **SOURCE:** `docs/audit/reconciler_product_filter_build_02aug2026.md` **§R4 @ `bb25fa6`** (the
  measurement + the bound) and its **forward pointer** immediately after; registered in
  `docs/MASTER_PENDING_01-Aug-2026.md` **§B.1 row 3 @ `07c7fc0`** (scope-expansion note; the forward pointer landed in the same commit).
- ⭐ **REOPENS AT:** the **first real HARD_KILL** — or sooner if delivery makes the path
  reachable, since **post-flip a CNC holding spared by #2b follows exactly this path**.

### AR5 · CO dormancy accepted rather than un-dormanted
- **Accepted:** CO stays **doubly dormant** (never used, 805/805 regular;
  `force_intraday_only` coerces non-INTRADAY back inside `place_order`). We did **not**
  enable or exercise CO in order to test it.
- **Reasoning:** the **correct behaviour is built anyway** — #2c-R refuses a CO position
  at the reconciler, and #2d is carded for `kill_switch`'s three sell sites — so dormancy
  is **not load-bearing for correctness**. ⚠️ **#2d is GATED and NOT started.**
- **Who / when:** #2c Step-1 measurement, 02-Aug-2026; the *"build it correctly anyway"*
  posture is Rama's standing direction, executed as #2c-R and carded as #2d.
- 📄 **SOURCE:** `docs/audit/reconciler_product_filter_build_02aug2026.md` — the
  **"AMENDED 02-Aug (#2c Step-1)"** block **@ `0a9e13a`**, finding **(e)** double dormancy — and **§R11**
  (label ceiling); register `docs/MASTER_PENDING_01-Aug-2026.md` **§A4**, the #2c entry.
- ⭐ **REOPENS IF:** **CO trading is ever intentionally enabled** — which is also Option
  2's unpark trigger (AR7), so the two reopen together.

### AR6 · The label ceilings — accepted as permanent honesty limits
- **Accepted:** several items **cannot reach `<VERIFIED LIVE>` today**: HARD_KILL has
  never fired · CO is doubly dormant · a runbook is unverified until an operator uses it
  in a real incident.
- **Reasoning:** these are **honesty limits, not obstacles**. ⛔ They are **not things to
  be argued upward** — the whole point of D4 is that a label must be earned by a
  production artifact, not by confidence.
- **Who / when:** **Rama, 27-Jul-2026** (the label rule itself); formalised as **D4** in
  this document, 02-Aug-2026.
- 📄 **SOURCE:** `docs/MASTER_PENDING_01-Aug-2026.md` **§2 item 3 @ `f9582e9`** (*"Label every item …
  'fixed' is retired. DEPLOYED IS NOT EVIDENCE — nine things in this system were built,
  looked alive, and had never run"*) + **§D4** of this document. Per-item ceilings:
  `docs/audit/reconciler_product_filter_build_02aug2026.md` **§R11** (CO) and **§6** (the
  CNC/HARD_KILL pair).
- ⭐ **REOPENS ONLY BY THE REAL EVENT** — a real HARD_KILL, a real CO position, an
  operator actually reaching for the runbook. ⛔ **Never by re-labelling.**

### AR7 · #2c-R's **Option 2** parked, with an explicit unpark trigger
- **Accepted:** Option 1 (refuse-and-escalate) shipped as the permanent safe fix; **Option
  2** (parent-order-id lookup → `cancel_order(variety="co")`) is **parked, not abandoned**.
- **Who / when:** the #2c-R card's HALT+RECORD step (ChatGPT Q1-Q5 binding, Rama's
  campaign), 02-Aug-2026.
- 📄 **SOURCE:** `docs/MASTER_PENDING_01-Aug-2026.md` **§C.7 @ `bb25fa6`** (the parked row with its
  trigger and owner) + `docs/audit/reconciler_product_filter_build_02aug2026.md` **§R8 @ `bb25fa6`**.
  ⛔ **Cross-reference, not a duplicate** — the authoritative entry stays in §C.7.
- ⚠️ **Correction to how this was handed to me — and this entry is §M3's worked example:**
  the card described it as *"Ledger #2's Option-2 parking"*. **The record says it is
  #2c-R's** Option 2. Recorded as measured, with the divergence noted rather than
  silently applied.
- ⭐ **REOPENS IF:** **CO trading is intentionally enabled**, **OR** the broker layer
  provides **reliable parent-order lookup**. **Owner: the CO protocol surface — ⛔ NOT the
  reconciler.**

### AR8 · #2d's DUPLICATE CO CRITICAL — noise accepted to protect the money path
- **Accepted:** a CO position handled at `kill_switch` **Site A** (local pass) is
  **deliberately NOT added to `handled_symbols`**, so the **Site B** broker sweep can still see
  it — a bracket cancel is not instantaneous at the broker — and, under **D1**, refuse it with a
  **CRITICAL**. ⇒ **one HARD_KILL can emit a CRITICAL about a position that was already
  correctly handled.**
- **Reasoning:** ⭐ **the two costs are not comparable.** Not adding costs a **duplicate alert —
  noise.** Adding costs a **same-symbol MIS row skipped by the sweep** — i.e. **a live intraday
  position left unflattened during a HARD_KILL**, a **money-path failure** and a direct breach of
  the Q4 invariant. ⇒ take the noise. ⭐ **This is not a fresh judgement: it is the ruling #2b
  already made for the CNC spare, for the same reason and at the same line range**
  (`capital/kill_switch.py:1592-1596` documents it) — so the register records **consistency**,
  not a new trade-off.
  ⚠️ **Bounded by an obligation, not left bare:** Site B's CO refusal message **must name the
  in-flight case** (*"a bracket cancel may already be in flight from the local pass"*). ⛔ A
  duplicate that reads as a second, unrelated failure is **worse than no duplicate** — that is
  the line between accepted noise and manufactured confusion, and it is what keeps this
  acceptance clear of **IA-P9-02** (the alert-fatigue disease the campaign exists to cure).
- **Who / when:** **ChatGPT under the amended §G2, 04-Aug-2026 ~00:55 IST** — ruling R-b of the
  three raised by the #2d Step-1b prep pass. ⚠️ Recorded as a **§G2 decision**, not as an
  implementer judgement: it trades an operator-visible alert against a money-path guarantee, and
  §G2 is what makes that call authoritative rather than advisory.
- 📄 **SOURCE:** `docs/audit/ledger2d_step1_measurement_03aug2026.md` **§A5 R-b @ `38208fc`**
  (the measurement of both costs **and** the ruling recorded beneath it). SHA determined by
  `git log -S`, per the citation format above.
- ⭐ **REOPENS IF:** **CO ever becomes live AND the duplicate proves to cause real operator
  confusion in practice.** ⛔ **Both halves are required** — CO is doubly dormant (**AR5**), so
  until it is enabled this risk **cannot be observed at all**, and a reopen argued from
  anticipated confusion rather than observed confusion is exactly the re-litigation §R exists to
  prevent. **Reopens together with AR5 and AR7.**

---

## D. DEPLOY

### D1 · ⛔ THERE IS NO PARTIAL DEPLOY
`main` is linear and deploy is **push → `checkout -f`**. **Anything committed before a
push rides that push.**
⇒ **Only build what you would be happy to deploy at the next slot.** This is why
"build it now, decide later" is not available, and why docs-only work is the safe thing to
land next to a gated item.

### D2 · The SHA inventory gate runs before EVERY deploy window
Every commit in `297b587..HEAD` must be **named in the unpushed ledger**.
⭐ **It has already caught its own stamp commits twice** — trailing docs/stamp commits are
exactly what escapes a SHA-keyed inventory, because they are the ones nobody thinks of as
"a change".

### D3 · No push before 18:15 IST
Precondition: **the book is flat, and Rama flattens MANUALLY.**
⛔ **Never build an auto-flatten.**

### D4 · LABEL HONESTY — `<BUILT>` → `<DEPLOYED>` → `<VERIFIED LIVE>`
⛔ **`<VERIFIED LIVE>` is NEVER claimed for a latent path**, and "fixed" is retired.
Live examples of the ceiling, all current:
- **HARD_KILL has never fired** ⇒ nothing gated on it can be verified live;
- **CO is doubly dormant** (never used; `force_intraday_only` coerces) ⇒ #2c-R can reach
  `<DEPLOYED>`+dormant-armed and no further;
- **a runbook is not verified until an operator uses it in a real incident.**
⭐ **`<DEPLOYED>` is not evidence** — nine things in this system were built, looked alive,
and had never run.
