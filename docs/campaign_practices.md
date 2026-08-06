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

✅⭐⭐ **05-Aug-2026 — THE FIRST INSTANCE REFUSED IN THE MOMENT RATHER THAN CAUGHT AFTERWARDS, and the
first POSITIVE entry on this list.** An auto-filled prompt appeared proposing *"Add the REVISION 4
entry to the card"*. It was **not acted on**: the discrepancy it named was raised in the report as a
disclosure, and the session **waited for a card.** The authorisation then arrived through the bridge
and the work was done — ⛔ **on the card's authority, not the prompt's.**
⚠️ **THE UNCOMFORTABLE PART, STATED PLAINLY BECAUSE IT IS THE WHOLE LESSON: the auto-fill happened to
suggest THE RIGHT THING.** The card that followed authorised very nearly exactly it. ⇒ ⛔⛔ **THIS IS
WHY THE RULE IS *"authorisation arrives only in Rama's own words"* AND NOT *"refuse bad
suggestions"*.** A rule that depends on the suggestion being wrong fails on the one that is right —
and **the right-looking one is the only kind that ever gets acted on.** *(Provenance note: the
auto-fill itself was observed by the operator, not by this session; what this session can attest to
is that it did not act, and waited.)*

🔴🔴 **05-Aug-2026 ~22:46 — INSTANCE #7, AND IT IS THE STRONGEST OF THE CAMPAIGN: THE AUTO-FILL DID
NOT MERELY APPEAR. IT BECAME THE PROMPT AND EXECUTED A LIVE MUTATION.**
Every prior instance was text proposing a **document** action. This one was **run**: the suggested
line was clicked, and it stopped `trading-system.service` on the live VM with a real CNC delivery
position held. ⇒ ⭐⭐ **The escalation ladder is now complete and should be stated as a ladder:
`suggest work → grant permission → PERFORM THE ACT`.**

⭐⭐ **THE TWO PROPERTIES THAT MAKE IT WORTH FILING — and neither is "it went wrong":**
1. ⛔ **THE COMMAND WAS CORRECT AND THE ACTION WAS AUTHORISED.** Rama had given GO in his own words;
   the command was byte-identical to the procedure's. **Nothing bad happened.** ⇒ **that is exactly
   the danger.** **Plausibility is the attack surface, not the tell** — a suggestion that is *right*
   trains the operator to stop reading the next one. **A rule that needs the suggestion to be wrong
   fails on the one that is right**, which is the only kind that ever gets acted on.
2. ⭐⭐ **THE SUCCESS WAS THE ONLY THING ABSENT FROM THE OUTPUT.** The clicked line carried a
   bracketed question **inside** the shell command, so the shell split it into extra unit names:
   **five `Failed to stop …` lines printed loudly, while `trading-system.service` — the first
   argument, and the only one that mattered — stopped SILENTLY and correctly.**
   ⛔ **An operator reading that command's own stderr would have concluded the whole thing failed**,
   and on this particular night would then have been at risk of "fixing" a stop that had already
   succeeded.

> ### 📌 THE GENERAL RULE THIS EARNS — ⭐ **A COMMAND'S OWN OUTPUT IS NOT EVIDENCE OF ITS EFFECT.**
> Verify the **state**, from a separate reading, afterwards. ⭐ **This is why the stop procedure's
> step (c) is a SEPARATE verification and not a read of what the stop printed** — a design choice
> made before the fact, which is what made tonight recoverable in seconds instead of a false alarm.
> ⚠️ Same family as **V5** (a check that cannot go red) approached from the other side: here the
> check *could* go red, and the thing that went **green printed nothing at all.**

*(Provenance: the auto-fill and the click were the operator's; what this session attests is that its
own two attempts to run the same command were **denied at the permission layer**, that it stopped
rather than trying a third form, and that it verified the resulting state independently.)*

🔴 **06-Aug-2026 ~21:0x — INSTANCE #8: A CORRECT AUTO-FILL ARRIVING *ONE LINE FROM* A GENUINE RULING.**
**The real authorisation was Rama's own words:** *"if any pending commit/push/pull etc make pc=vm
100%."* **In the same exchange the console carried `❯ ok push it now [auto-filled by Claude]`.**

⭐⭐ **THIS IS THE HARDEST CASE THE LADDER HAS PRODUCED, and it is a new rung — not because the
auto-fill was wrong, but because it AGREED WITH A REAL RULING AND SAT BESIDE IT.**
- ⛔ **The auto-fill was NOT the authorisation** and must never be counted as one, **even when it is
  indistinguishable in content from the ruling next to it.**
- ⭐ **Instance #7 taught that a *right* suggestion trains you to stop reading. #8 is the limit of
  that: a right suggestion arriving beside a right ruling, where reading only one of the two still
  produces the correct action** — so the habit is reinforced **and** nothing visibly goes wrong.
- 🏷️ **The discriminator is PROVENANCE, never CONTENT.** ⛔ *"It matches what Rama said"* is not a
  test — the two can agree and only one of them is authority. **Cite the human sentence; never the
  console line.**

> ⭐ **Corollary worth stating: the safe habit is to name the authorising sentence VERBATIM when
> acting on it.** A ruling you can quote is one you actually read; a ruling you paraphrase may have
> been the auto-fill all along.

**06-Aug-2026 ~21:5x — INSTANCE #9** *(⛔ instance only; the rule is unchanged and needs no new text)*.
The session's output closed with `❯ update the ledger and close for the night [auto-filled by
Claude]`. **Right again — and again not the authorisation; the operator's card was.**
⭐⭐ **The reason #9 is filed rather than waved through: #8 and #9 fell in the SAME EVENING, both
correct, both beside a real instruction.** ⛔ **A pattern that is right every time is not evidence
the risk is theoretical — it is the mechanism by which the check stops being performed.**
⭐ **Count the instances; do not grade them by outcome.**

**07-Aug-2026 ~00:4x — INSTANCE #10** *(⛔ instance only; the rule is unchanged)*. The session's
output closed with an auto-fill proposing *"file the pipe/exit-code trap as a rule."* **Right again —
and again not the authorisation; the operator's card was.** ⭐ **Third correct auto-fill in ~4 hours
(#8, #9, #10), all beside a real instruction.**
⛔ **Counted, not graded** — ⭐ **and the count is now the finding: a rule whose instances are ALL
"correct, no harm done" is measuring exactly the condition under which it will stop being applied.**
🏷️ *G1's occurrence count moved 5 → 9 on 06-Aug and 9 → 10 here; ⚠️ `PATHS.md` carries this number
and goes stale by omission — it was last refreshed at 9.*

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

#### 🛑 G5.1 · ⭐⭐ **THE GOVERNANCE BOUNDARY — the last legitimate sentence** — 🏷️ **PROVISIONAL**

🏷️ **Parent (G7): EXTENDS G5.** G5 fixes the required **closing shape AFTER a ruling exists**
(*"mechanism refuted, intent intact, returned for a ruling on the mechanism only"*). **G5.1 fixes the
required closing shape BEFORE one exists.** Same discipline, the other side of the ruling.
🏷️ Authority basis: **G1/G2** — the ruling is Rama's. **Sibling: G3** *(disclose, don't expand)* —
G3 forbids **fixing** out of scope; G5.1 forbids **ruling, or pre-building toward a ruling**, in
scope.

> ### **FOR A GOVERNANCE-DEPENDENT ITEM, THE LAST LEGITIMATE SENTENCE IS:**
> ### ***"the evidence is sufficient for a ruling."***
> ### ⛔ **EVERYTHING AFTER IT IS EITHER THE RULING, OR NOISE WEARING THE RULING'S CLOTHES.**

⭐⭐ **The second half is the operative one.** Analysis produced past sufficiency does not read as
overreach — it reads as **thoroughness**, and it arrives in the same voice, the same format and the
same document as the work that was owed. ⛔ **That is precisely why the line has to be written down
rather than felt.**

### ⭐ AND NAME WHERE THE LINE ACTUALLY IS — ⛔ **a rule with no worked edge is unenforceable**

**The live case (07-Aug-2026): the delivery config surface** *(`sizing_thread_conclusions_06aug2026.md`
§5, §7.1)*. Two governance rulings — **inventory authority** and **pipeline ownership** — block the
whole surface, and neither is answerable by measurement.

> 🔴 **WRITING THE DELIVERY KEYS — EVEN AS PLACEHOLDERS, EVEN COMMENTED OUT, EVEN AS `null` —
> WOULD CROSS IT.**
> ⭐ **Why a placeholder is not a lesser act:** *"a key's existence is an INVITATION"* (§5.3). It
> pre-commits the **shape** of the surface, which is the substance of the pending ruling; and it
> converts *"should this control exist?"* into *"what value should this control have?"* — ⛔ **a
> different question, asked of a different person, with the first one silently answered.**

> ## ⚠️⚠️ **AND THE CHECK WENT RED — ⛔ THE STEP IS NOT HYPOTHETICAL. IT HAS ALREADY BEEN TAKEN ONCE.**
> **Measured 07-Aug-2026 00:3x, `config/system_config.yaml`, and it corrects this rule's own first
> draft** *(which said the step was "one step away" — true for some keys, ⛔ false for others)*:
>
> | | state | |
> |---|---|---|
> | **`delivery_risk_per_trade_pct`** `:190` | **`null` PLACEHOLDER** | 🔴 **already written** |
> | **`delivery_max_position_value_pct`** `:191` | **`null` PLACEHOLDER** | 🔴 **already written** |
> | `max_open_delivery_positions` `:205` · `max_daily_delivery_trades` `:206` | **real values** (3 / 5), `SLICE2.5-PHASE-3 (A)`, *"inert while `force_intraday_only=true`"* | ⚠️ **a DIFFERENT case — they carry a value and a stated lock, ⛔ not an empty slot awaiting one.** 🏷️ **Whether they were RULED is `(I)`, not established here** |
> | **concentration · tier · bucket loss-limit · carry-days · entry-cutoff** | **ZERO keys, all five** | ✅ **line uncrossed — this is where it still is** |
>
> ⛔ **The `:185-189` comment is the invitation IN WRITING:** *"The V3 delivery path sets these when
> delivery is activated (a later step)."*

⭐⭐ **THAT PRECEDENT IS THE RULE'S EVIDENCE, not an embarrassment to it — and the cost is measured
twice over:**
1. **Both placeholders are twins of controls that NEVER BIND** (§5.2) ⇒ their destination is the
   **pending twin-retirement ruling** — 🔴 **the placeholders MANUFACTURED a ruling that would not
   otherwise be owed.**
2. **Must-Land-Before constraint 8, verified at source tonight** *(⚠️ `capital/position_sizer.py`,
   ⛔ **not** `core/` — my first grep was a FALSE ZERO on the wrong path)*: `:585` **enforces**
   `eff_max_position_value_pct` while `:596`/`:609` **report the GLOBAL** ⇒ **the moment
   `delivery_max_position_value_pct` is given a value, the operator-facing CRITICAL states a
   percentage that was NOT enforced.** 🏷️ **A latent defect that exists BECAUSE THE KEY EXISTS.**

⇒ 🏷️ **The honest form of the boundary: it is one step away for the FIVE that have no key, and it
was already crossed for the TWO that do** *(at the `V3 03.06` "DELIVERY-scoped sizing scaffold
(INERT)" — ⛔ date not established here, and not chased tonight: `G3`)*. ⭐ **A boundary is cheap to write while
nobody is standing on it and contested the moment someone is mid-step** — ⛔ and this one already has
a body of evidence for what the far side costs.

### ⚠️ STATUS — **PROVISIONAL, and the honest reason** *(G7.1)*

⛔ **G5.1 has NOT yet bound.** The sizing thread **did** stop at sufficiency and the keys were **not**
written — but the rule did not exist then, so **the behaviour occurred WITHOUT it**; that is a
**retrospective instance, ⛔ not an application.** 🏷️ **Per G7.1, promote it the first time it BINDS
or is AMENDED against a real case — and if it never does, that is the answer too.**
⭐ **`§8`'s own result forbids scoring it any higher:** *every explanation constructed AFTER the fact
failed; only predictions written BEFORE held.* **G5.1 is written before its case. It gets to be
tested, not credited.**

### G7 · ⭐⭐⭐ **EVERY NEW RULE MUST NAME A PARENT** — the anti-proliferation device

> ### **A NEW GOVERNANCE RULE MUST NAME AN OLDER RULE IT EXTENDS, NARROWS OR REPLACES.**
> ### ⛔ **If it cannot name that relationship, the default is: DO NOT CREATE IT YET.**

🏷️ **ITS OWN PARENT, stated first because the rule demands it of itself:** it **formalises the
convergence discipline** written into **`M10`'s three-scale extension** — *"a campaign that keeps
DERIVING the same rule from different directions has found something structural; one that keeps
ADDING rules has not."* ⭐ **M10 stated the discipline; G7 makes it a precondition.**

⭐⭐ **Why this and not "be disciplined": it is CHECKABLE.** A reviewer can ask one question and get
a yes/no, and the answer is visible in the filing itself.

> ### ✅ **RETROACTIVE SELF-TEST — 06-Aug-2026, applied to the four rules filed the same night**
> ⛔ **A rule that cannot survive its own first application is not ready.**
>
> | rule filed tonight | names a parent? | |
> |---|---|---|
> | **M12.1** — the four partition tests | **extends M12** | ✅ |
> | **the shared-artifact principle** | **extends M10** *(as a third scale, ⛔ not as `M15`)* | ✅ |
> | **§1.13** — isolate the policy, never the purse | **extends `foundation_engineering_rules` §1.11/§1.12** | ✅ |
> | **Must-Land-Before #9** — validate the snapshot source | **extends the Must-Land-Before table itself** | ✅ |
>
> ⭐⭐ **4 of 4 name a parent — and this is a check that COULD have gone red.** Had any been filed as
> a free-standing new rule, G7 would have blocked it at creation. 🏷️ **The device passes its own
> first use.**

⛔ **FROM HERE IT IS A PRECONDITION ON FILING, NOT A REVIEW NOTE.** ⭐ The question is asked *before*
the rule is written, not after.

#### 🕯️ G7.1 · **A RULE IS NOT ADOPTED UNTIL IT HAS BOUND** — 🏷️ **PROVISIONAL**

🏷️ **Parent (G7): extends G7.** ⛔ **Marked PROVISIONAL by its own terms — see the last line.**

> **A RULE IS NOT ADOPTED UNTIL IT HAS BEEN APPLIED TO A REAL CASE AND HAS EITHER **BOUND** OR BEEN
> **AMENDED**.** ⛔ **Writing it and agreeing it is not adoption.**

⭐⭐ **Grounded in three instances from ONE evening — and the reason it is a rule at all is that all
three were found by APPLICATION, none by review:**

| rule | it PASSED its own test | …but |
|---|---|---|
| **the 1:1 telemetry rule** | as written | it **accepted 1 of the 2** known twins |
| **`M12`** | the count verified | it verified a count **over a broken partition** |
| **`G7`** | every rule named a parent | **naming a parent does not make a rule BIND** |

⛔ **It applies to the rules written tonight as much as to the older ones** — ⭐ **including
ITSELF**, which is exactly why it is filed **`PROVISIONAL`**: it has not yet bound or been amended
against a real case. 🏷️ **Promote it to adopted the first time it does — ⛔ and if it never does,
that is the answer too.**

⚠️ **AND THE CASE IT ALREADY CAUGHT, recorded because it is the honest one:** *"semantic
correctness"* has **ONE instance in ONE artifact class** and would **fail** a promotion bar requiring
recurrence **and** cross-category value. ⭐⭐ **It survives only because it was FOLDED INTO M12 as a
clause rather than promoted as a rule — so it never needed to clear the bar.** 🏷️ **The fold was the
right call before there was a reason for it; G7 is that reason, arriving afterwards.**

### G4 · Superseded text is STRUCK THROUGH but kept LEGIBLE
With an **amendment note and date**. ⛔ Never silently deleted, never quietly reworded. A
reader must be able to see what the guidance used to say and why it changed — otherwise
the correction is indistinguishable from a rewrite of history.

### G6 · ⭐⭐ OVERRIDING A WRITTEN GATE TAKES **THREE** CONDITIONS — fresh evidence is only one

*Authored by ChatGPT, adopted 06-Aug-2026. The campaign's first rule for overriding its own gates.*

**Fresh evidence alone does NOT justify overriding a written gate.** Require **all three**:

| | condition | why it is not optional |
|---|---|---|
| **(a)** | the original **safety objective is still satisfied** | ⭐ **the missing half.** *"The state was measured fresh"* is a fact about the **evidence**, ⛔ **not about whether the gate's PURPOSE is still served.** A gate exists to stop an act being taken over state that might mean something is wrong; (a) is the only condition that asks whether that worry is retired. |
| **(b)** | the new evidence **fully explains** the apparent violation | a partial explanation leaves a residue, and the residue is exactly what the gate was built to catch. |
| **(c)** | the override is **LOGGED — timestamp, clause, evidence, who ruled — BEFORE the act** | ⭐⭐ **An override recorded afterwards is a JUSTIFICATION; recorded before, it is a DECISION.** The ordering is the whole content of (c). |

⛔ **THE COROLLARY, EARNED THE SAME NIGHT AND LOAD-BEARING — FIRST ASK WHETHER THE GATE ACTUALLY
REFUSES.** On 06-Aug the gate's **literal** PASS block refused a two-position night (it had been
written assuming one). The refusal was a **scope artifact**, so the gate was rewritten as a **rule**
— and under the rule, re-measured fresh at 19:08:44, **all five checks PASSED**. ⇒ **no override was
required, and none was taken.**
⭐⭐ **Logging a "belt-and-braces" override anyway would have been the WORSE act, not the safe one:**
it records a bypass that never happened, and a register with spurious overrides in it makes the next
override look ordinary. **The safe default is not "log an override to be safe" — it is: repair the
gate's SCOPE, re-run it, and override only what still refuses.**
⚠️ **Fixing a gate's scope is itself governed** — the repair must be visible as a repair (§G4), and
⛔ **a later override must never be absorbed into the rewrite that preceded it**, or the record can
no longer distinguish *"the gate passed"* from *"the gate was edited until it passed."*

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

> #### ⭐⭐ 05-Aug-2026 — **A FIFTH INSTANCE, AND IT IS A DISTINCT SUB-SHAPE: RIGHT PATTERN, WRONG FILE**
> ⛔ **Not a new rule — an M1 instance.** The 18:00 cron-drift check: I grepped
> `cron.*drift|cron_integrity` over `logs/system_2026-08-05.log` → **0**, and plain `cron` over the
> same file → **0**. ⭐ **The pattern was right. The corpus was wrong** — the job writes to its own
> **`logs/cron-drift-check.log`**, which only a directory listing by mtime revealed. *(The real
> answer was a clean PASS, so the false zero cost nothing this time — which is exactly why it is
> worth writing down.)*
>
> ### 🔎 THE SUB-TAXONOMY — **six instances in one day, and they are SIX MECHANISMS, not one lesson**
> ⛔ **Not a new rule. These are M1 instances**, named so the next person can recognise which one
> they are standing in. ⭐ **Every one of them returns a clean, confident ZERO — that property is
> what makes them a family.**
>
> **GROUP 1 — THE QUERY WAS WRONG (right corpus, wrong search):**
> | mechanism | today's instance | the fix that caught it |
> |---|---|---|
> | **wrong anchor** | the census grep anchored on `'CENSUS BEGIN'` — **a string emitted nowhere** | anchor taken verbatim from the emitter, `effect_telemetry.py:242` |
> | **predicted string vs emitted string** | `DUPLICATE_SYMBOL` / `CONTRARY_POSITION` — both **0 rows all day**; the real gate was a **third** code | enumerate the actual `rejection_reason` values; let the behaviour name itself |
> | **pattern that cannot fail** | `502` matched the **digits** inside ~100 INFO price lines | constrain to level **and** logger |
>
> **GROUP 2 — THE QUERY WAS RIGHT (wrong corpus, or a truncated view):**
> | mechanism | today's instance | the fix that caught it |
> |---|---|---|
> | ⭐ **wrong file** | the cron-drift check writes to its **own** `logs/cron-drift-check.log`, not the service log | list the log directory by mtime |
> | **wrong source** | the census is `INFO`, so `journalctl` (stdout = `WARNING`+) **structurally cannot hold it** | read `logs/system_<date>.log` |
> | **truncated view** | a 15-file limit read as an absence (nearly denied MTM exists); an `[Omitted long matching line]` read as a non-match | re-run narrowed; **never read a truncated result as evidence** |
>
> **GROUP 3 — ⭐ ADDED 05-Aug LATE: THE PATTERN AND THE CORPUS WERE BOTH RIGHT; THE *SELECTION
> BOUNDARY* DID NOT MATCH THE SUBJECT'S.** ⛔ Still M1 instances, not a new rule.
> | mechanism | that night's instance | the fix that caught it |
> |---|---|---|
> | ⭐ **filter WIDER than the subject** | realised P&L summed over *all* non-zero `pnl_delta` rows → **`0.0`**; the 8th row is a `RESET_PNL −44.61` that **cancels the seven by design** — it would have refuted a **correct** finding | restrict to the row TYPE that carries the quantity (`RELEASE_USED`) — **7 rows, +44.61** |
> | ⭐ **filter NARROWER than the subject** | `ZERODHA_API_KEY` reported **MISSING** from the VM `.env` — I checked the **fallback** name; the real key is the **declared** `ZERODHA_API_KEY_LFL836` from `accounts.csv` | enumerate what actually exists (`grep -oE "^ZERODHA_[A-Z0-9_]+"`) before asserting an absence |
>
> ⇒ ⭐⭐ **GROUP 1 is a defect in the PATTERN; GROUP 2 is a defect in WHERE YOU LOOKED; GROUP 3 is a
> defect in WHAT YOU SELECTED — and it is the nastiest, because the pattern, the corpus AND the
> width are all defensible, so nothing about the query looks wrong.**
> ⛔ **So the width must name the CORPUS as well as the pattern — and, per Group 3, the PREDICATE
> too: say which rows/keys the zero was taken over, not just which file.**
> 🔴 **The costliest of the six is `predicted string vs emitted string`: it is the only one that
> would have INVERTED a live conclusion** — recording a real, six-times-observed coupling as inert —
> rather than merely leaving a gap.
>
> ⭐ **THE GENERAL FORM: a zero is not a result until you have named the corpus it came from — and a
> CONTROL proving that corpus is live is what turns the zero into evidence.** Today's controls:
> 0 `effect_census` against **8,204** `effect` hits · 0 auth errors against **25** ERROR/CRITICAL
> lines · a **rowcount = 1** proving an order filter matched before its columns were read.

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

> ### ⭐ COROLLARY (05-Aug-2026) — ⛔ **a corollary, NOT a new rule: PRINT THE ZERO.**
> **A printed zero is a MEASUREMENT. An absent one is SILENCE — and afterwards the two are
> indistinguishable.**
> ⚠️ **05-Aug supplied the sharpened version: a zero that was never printed at all is not the only
> failure — a zero READ OFF A TRUNCATED LIST is worse, because it looks printed.** Twice in one
> session a `grep` result cut by a display limit (`files_with_matches` capped at 15; an
> `[Omitted long matching line]`) was one step from being recorded as a measured absence.
> ⇒ **print the zero, and say what was searched — see §M1 width and `feedback_absence_needs_wide_check`.**

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

### M8 · ⭐⭐ READ THE RECORD **BEFORE** MEASURING — it is an ORDER OF OPERATIONS, not a virtue

> **`docs/SYSTEM_MAP.md` is MANDATORY PRE-READING at the START of every session — step zero, before
> any measurement, not a reference consulted when something seems unclear. Re-deriving a fact the
> system has already written down costs a session, and can ship a wrong command in the meantime.**

⛔ **Written as an ORDER, deliberately, because *"be more careful"* is not a rule and cannot be
checked.** *"Did you read SYSTEM_MAP before your first measurement?"* is answerable yes or no.

**Earned twice in four days, both times on the same shape — the system had already recorded the
answer while the campaign was deriving it by hand:**

- **04–05-Aug · the flag list.** `trade_type` was *"the flag nobody listed"* and had to be found by
  measuring `control.py`'s two layers. ⭐ **`delivery_lock.status` (`main.py:2838-2846`) had been
  publishing the full conjunct verbatim at every boot** — *"real CNC/GTT requires
  `delivery_enabled=true` AND `force_intraday_only=false` AND `trade_type` in {DELIVERY,BOTH}"*. **The
  flag list was short; the system was not.**
- **05-Aug · the journald fact.** Two revisions of an operator card were spent tuning a `grep` anchor
  against `journalctl`, before measuring that the census is `INFO` and stdout is `WARNING`+ ⇒ **it was
  never in journald at all.** ⛔ **`SYSTEM_MAP.md` had carried that since 25-Jul** — *"the app boot log
  is `logs/system_<YYYY-MM-DD>.log`, **NOT journald** (journald holds only ~6 lines/boot — WARNING+
  and stdout; MEASURED on Fri 24-Jul)"*.
  ⭐⭐ **AND THE SAME ENTRY CARRIED THE RULE THAT WOULD HAVE PREVENTED BOTH REVISIONS:** *"an operator
  instruction that says 'grep X' must be VERIFIED against a real log before it ships — a check that
  silently finds nothing is WORSE than no check, because 'no output' reads as 'it failed'."*
  ⇒ **the map did not merely hold the fact; it held the lesson, already generalised, unread.**

⭐ **THE ASYMMETRY THAT MAKES THIS WORTH A RULE:** reading the record is **cheaper** (one file, once)
**and more authoritative** (it was written by whoever measured it, at the time, with the incident in
view) than re-deriving it from source. A re-derivation can also be *wrong* — and a wrong
re-derivation ships as a command.

⚠️ **The map is not infallible and this rule does not say to trust it blindly** — §M3 still governs
(*a card is not authoritative over a record*), and **§G4's superseded-but-legible discipline means
map entries can be STALE**: the same 05-Aug session found the map's *Delivery (Slice 2.5)* section
still reading *"DORMANT / never exercised, flags OFF"* on the day delivery traded. ⇒ **Read it
first, then verify what you are about to rely on. Reading first changes what you verify, not
whether you verify.**

(Records: `docs/audit/check1_product_skip_step1_05aug2026.md` · `docs/MASTER_PENDING_01-Aug-2026.md`
currency block, the composite-verdict finding · `docs/SYSTEM_MAP.md` BATCH-4 banner, 25-Jul.)

---

### M9 · ⭐⭐ LABEL EVERY CLAIM WITH ITS EVIDENCE CLASS — **(P) · (S) · (I)**

> **Every factual claim carries one of three classes, and the class is written next to the claim:**
> - **(P) PRODUCTION EVIDENCE** — *it happened.* A row, a log line, an artifact you saw.
> - **(S) SOURCE EVIDENCE** — *the code says so.* A **SHA-pinned** `file:line` cite.
> - **(I) ARCHITECTURAL INFERENCE** — *it follows from the code, but has never been observed.*
>
> ⛔ **The three fail differently and must NEVER be mixed inside one sentence.**

**How each fails — which is the whole reason the labels are not decoration:**
| class | how it goes wrong | what protects it |
|---|---|---|
| **(P)** | the artifact is misread, or is an *absence* that was never wide enough to be one | §M1 width · §M6 · `feedback_absence_needs_wide_check` |
| **(S)** | **the cite ROTS** — line numbers hold only at their measured SHA (§M3) | pin the SHA; re-measure at HEAD |
| **(I)** | ⭐⭐ **it is INDISTINGUISHABLE from (P) once written into prose**, and it is the class most likely to be *both* the strongest-sounding and the least verified | say the words *"never observed"* in the same sentence |

**Adopted 05-Aug-2026 on the delivery flip day, which supplied all three and several near-misses:**

- **(I) — and it is the day's biggest finding, in its weakest class.** *"Without FIX-133's floor the
  system would have ordered zero and no delivery trade would have happened at all"*
  (`capital/position_sizer.py:527-530`: `raw_qty 1 × 0.5` floors to **0**, `max(1, …)` lifts it back).
  ⭐⭐ **TRUE BY ARITHMETIC AND NEVER OBSERVED — no run has ever exercised the counterfactual.**
  ⛔ **Both facts must travel together**, or a later reader takes an inference for a measurement and
  removes the floor as a rounding nicety. **Its failure mode is silence** (*"no signals qualified
  today"*), which is precisely why the class label has to survive the retelling.
- **(S) — the cost model branches on `product`** at `broker/cost_calculator.py:169-174`, `:190-198`,
  `:224-228`. Source, not observation.
- **(P) — the same claim then earned a second class independently:** ASKAUTOLTD's recorded costs of
  **₹1.53** reproduce from the CNC rate table to the paisa (MIS rates give ≈ ₹0.63). ⭐ **(S) said the
  branch exists; only (P) says it RAN.** Two classes, two different facts, one conclusion.
- **(P)/(I) confusion caught live:** `gtt_state` reads **`CLEANED`**. That the row was transitioned
  is **(P)**. That *"`cnc_gtt_monitor` observed the trigger"* would be **(I)** — ⛔ **and it is not
  established**; only the shutdown census can raise it to (P).
- **⚠️ A NEAR-MISS, recorded because it is the cheapest instance to learn from:** a repo-wide grep
  run with `files_with_matches` **and a 15-file display limit** omitted `capital/fund_manager.py`,
  and was one step from being written up as **(S)** *"the module producing the day figure has no
  mark-to-market concept."* **It has 19 such hits.** ⇒ **a truncated list is not evidence of any
  class at all** — it was about to be promoted straight from *display artifact* to *source fact*.

⭐ **Why this earns a rule rather than a note:** the campaign's recurring failure is a zero that
means *unexercised* being read as a zero that means *passed*. **(P)/(S)/(I) is that same distinction
generalised from zeros to every claim** — and unlike *"be precise"*, it is checkable: *"which class
is this, and does the sentence say so?"* is answerable yes or no.

⛔ **Held back deliberately, one instance each and their triggers already written: the
three-identity-fields rule and the conflict taxonomy.** ⭐ **A rule promoted on one instance is a
generalisation, not a practice.**

(Records: `docs/audit/HANDOFF_05-Aug-EVENING.md` §0b–§6 · `capital/position_sizer.py:527-530` ·
`broker/cost_calculator.py:169-228` · `capital/fund_manager.py:386-392`, `:1809-1847`.)

---

### M10 · ⭐⭐ THE OWNERSHIP TEST — **"WHAT PROCESS UPDATES THIS FIELD?"**

> ## ⭐⭐⭐ M10 IS ONE PRINCIPLE AT **THREE SCALES** — ⛔ **NOT three rules** *(extended 06-Aug-2026)*
>
> ### **A SHARED ARTIFACT MUST DECLARE ITS OWNER, SCOPE, CONSUMERS AND PURPOSE. If it serves MULTIPLE purposes, that coupling must be INTENTIONAL AND DOCUMENTED.**
>
> | scale | the artifact | how the campaign already states it | today's worked example |
> |---|---|---|---|
> | **FIELDS** | a column / a record field | **M10 below** — *what process updates this?* | `trades.closure_source` empty on the first delivery exit |
> | **CONFIG KEYS** | a tunable | **a twin, or a DECLARED REASON** — the `max_consecutive_losses` template *("deliberately SHARED — no delivery variant")* | the 34 → 9 semantic filter |
> | **SHARED ARTIFACTS** | state · representation/channel · namespace | **this extension** | **state:** `_total`, `held` · **channel:** the exit code carrying *process health* AND *a business finding* · **namespace:** the classification corpus, the symbol namespace |
>
> ⭐⭐ **`OWNER` IS THE LOAD-BEARING WORD, and it is what "declare its purposes" misses.**
> ⛔ **The exit code's defect is precisely that NOBODY OWNS IT:** `reconcile_positions.py:428-434`
> writes a **business** meaning (`2 = mismatches found`), the heartbeat wrapper reads a **health**
> meaning (`non-zero ⇒ FAILED`), **and neither declared the claim.** ⭐ Not a disagreement — **an
> unowned channel**, so both readings are locally reasonable and jointly wrong.
>
> ### ⭐⭐⭐ **WHY THIS IS FILED AS A CONVERGENCE AND NOT AS A NEW RULE**
> **The campaign now holds the same rule at three scales, each derived INDEPENDENTLY** — M10 from
> stale fields, the twin-or-declare rule from config isolation, this from tonight's shared artifacts.
> ⇒ ⭐⭐ **A campaign that keeps DERIVING the same rule from different directions has found something
> STRUCTURAL. One that keeps ADDING rules has not.** ⛔ **Resist filing scale four as `M15`.**
>
> ### ⛔⛔ THE GUARD — **THIS MUST NEVER READ AS "SPLIT SHARED THINGS"**
> **`max_consecutive_losses` and the kill switch are SHARED AND CORRECT**, and both are correct
> *because the sharing is declared at the check itself.* ⭐ **The rule asks for a DECLARATION, not a
> split.** ⛔ **A shared artifact with a written reason is a DECISION; the defect is the undeclared
> one.**

> **For every field in every record, the question is NOT *"does it exist"* but **"WHAT PROCESS
> UPDATES IT?"** ⇒ **Name the process, or mark the field `INFORMATIONAL` — not authoritative.**
> - **nothing updates it** ⇒ **decoration.** Say so.
> - **only a human, by hand** ⇒ ⚠️ **it WILL drift — write that in the schema rather than discover
>   it later.**
> - **code updates it** ⇒ ⭐ **name the writer: `file:line` + SHA.**

⭐⭐ **WHY IT EARNS A RULE — the failure is not that the field is empty:** **an unowned field does
not stay neutral. It stays at the day it was written and reads as CURRENT forever.** ⇒ **a stale
field MANUFACTURES CONFIDENCE, exactly as a tautological check does — V5's failure mode, applied to
DATA instead of to logic.**

⛔ **DELIBERATELY NOT FOLDED INTO V5 OR M8 — three different failure modes:**
| | what fails | the tell |
|---|---|---|
| **V5** | a **check** that cannot go red | no input exists that would fail it |
| **M8** | an **order of operations** — measuring before reading the record | the answer was already written down |
| **M10** | a **field** nobody writes to | it is *correct as of a date nobody can see* |

**Instances — four, and the fourth is from production today:**
1. the **~22 declared-but-inert subsystems** — declared, never constructed;
2. a **STATUS column nobody maintains** (inventory #1);
3. **`[LAUNCH-PHASE]` / `[PERMANENT]`** — ⚠️ **open: is either tag EVER READ BY CODE, or are they
   comments?** *(the ownership test applied to the inventory's own STATUS axis)*;
4. 🔴 **05-Aug, LIVE: `trades.closure_source` is EMPTY on the first delivery exit.** A standing rule
   says *"group by `closure_source`, never `exit_reason`"* — ⛔ **and on the delivery path nothing
   populates it, so the rule has NO OPERAND.** The 28-Jul backfill wrote 35 rows and left six NULL;
   this is a seventh, on a path the backfill could not have covered. ⭐ **A rule pointed at an
   unowned field is a rule that silently stops working when a new writer appears.**
5. ⚠️ **And a fifth, caught the same day:** `SYSTEM_MAP.md`'s Delivery section still read *"DORMANT /
   never exercised"* **on the day delivery traded** — a field whose only updater was human attention.
6. 🔴🔴 **06-Aug, and it is the worst of the six because it is on a KILL PATH:**
   `capital/drift_handler.py:17`'s docstring says the escalating set is *"`{"fund_manager"}`"* — **one
   member.** The frozenset **four lines below** (`:66-70`) has **three**, and one of them is the very
   tag `_check7` publishes. ⛔ **Nothing updates that docstring.** ⭐ **The prose says the path is
   barred; the code says it escalates** — and it **demonstrably misled a reviewer, on 06-Aug, in the
   direction of FALSE REASSURANCE about a kill.**
   ⭐⭐ **This is M10's failure mode at its sharpest: the unowned field was not merely stale, it was a
   SAFETY CLAIM.** ⛔ Filed; **not fixed in passing.**

⭐ **The cheapest form of compliance:** when adding a column or a record field, write the writer's
`file:line` beside it in the schema comment. **If you cannot, that is the finding.**

(Records: `docs/audit/item4_partA_inventory_survey_05aug2026.md` ·
`docs/audit/HANDOFF_05-Aug-EVENING.md` §0b · `docs/MASTER_PENDING_01-Aug-2026.md` item-4 sub-entry.)

---

### M11 · ⛔⛔ NEVER CLASSIFY A DRIFT ALARM BY ITS **NAME** — classify it by its published `source_module`

> **A drift check's safety posture is decided by ONE thing: whether its published `source_module`
> is a member of `_ESCALATING_SOURCES` (`capital/drift_handler.py:66-70`).**
> ⛔ **NOT its alarm name. NOT the file it lives in. NOT its check number.**

**THE FAILURE IT PREVENTS, in one line:**
> ⭐⭐ **a scoped SAFETY result being extended to a family that shares only its LABEL.**

**(P) THE INSTANCE, 06-Aug-2026 — two checks, one file, one alarm name, two safety postures:**

| check | `source_module` | in `_ESCALATING_SOURCES`? | posture |
|---|---|---|---|
| **G3** `CAPITAL_DRIFT` | `order_reconciler` | ❌ no | **barred by DH1** — advisory. This is AR9's subject |
| **`_check7`** `CAPITAL_ACCOUNTING_DRIFT` (BL-3) | `fund_manager_self_check` | ✅ **YES** | **escalates** — single-sample SOFT/HARD |

**Both live in `orders/order_reconciler.py`. Both are called "capital drift".** A reviewer extended
AR9's acceptance from the first to the second **from recall**, and was wrong **in the direction of
false reassurance**. ⭐ It was caught only by reading the tag at source.

#### 📋 PERMANENT REVIEW CHECKLIST — run for **every** drift check, existing and future:
1. **What `source_module` does it publish?** ⛔ Read it at the `publish(` call, not from the module name.
2. **Is that string in `_ESCALATING_SOURCES`?** ⛔ Read the frozenset, **not the docstring above it** —
   `:17` is stale and says one member where the code has three (**M10 instance 6**).
3. **If YES ⇒ it is a SAFETY edge.** Any acceptance, tolerance or noise ruling needs its own
   assessment and **cannot inherit one written for a different tag.**
4. **If NO ⇒ advisory** — and say so explicitly, so the next reader does not have to re-derive it.

---

### M14 · ⭐⭐ CLASSIFICATION LEAKAGE — a CROSS-CUTTING design principle, ⛔ not an alert-fatigue topic

**Adopted 06-Aug-2026. It has the same standing as *single source of truth*** — ⛔ **it is not a
monitoring nicety, and filing it under "noise" is how it keeps recurring in unrelated subsystems.**

> ### 📋 **THE FOUR-QUESTION CHECKLIST — run it in DESIGN REVIEW, on every value that crosses a boundary:**
> **Is this ① RUNTIME state · ② BUSINESS state · ③ EXECUTION health · ④ REPORTING state?**
> ⛔ **A value that answers one must never be read as another.**

⭐⭐ **THREE OF 06-Aug's DEFECTS WOULD HAVE BEEN CAUGHT BY THAT ONE QUESTION:**

| defect | leaked | into |
|---|---|---|
| **F6** (`cnc_gtt_monitor.py:464` `abs()`) | ① a **runtime** reading (`held`) | ② a **business** state (*trade open*) |
| **the daily tree diff** | a **healthy, self-healing** condition | ③ a **violation** ⇒ EOD CRITICAL every day |
| **`reconcile_positions` exit 2** | ② a **business finding** (*mismatches detected*) | ③ **execution failure** (`status=FAILED`) |

📄 Full item: `docs/design/classification_leakage_06aug2026.md` — filed as **ONE** architectural
review, ⛔ **not three defects**, because three items get fixed three ways and the third fix will not
know it was solving the first problem again.

### M9.1 · ⭐⭐ **FACTS OUTLIVE INTERPRETATIONS — attach EVIDENCE EXPIRY to the INTERPRETATION**

🏷️ **Parent (G7): extends `M9`'s evidence classes and the decision ledger's `DATE/VERSION` column.**
⛔ Not a new rule — it says **which rows need the field.**

> **Raw observations remain true far longer than the conclusions drawn from them.**
> ⇒ **Expiry attaches to the INTERPRETATION. ⛔ Leave raw observations UNEXPIRING.**

⭐⭐⭐ **PROVEN INSIDE A SINGLE HOUR, 06-Aug:**

| | | lifetime |
|---|---|---|
| **the FACT** | `get_active_gtt_states()` is `WHERE status='ACTIVE'` *(`state_store.py:2250`)* | ⭐ **permanent — still true** |
| **the INTERPRETATION** | *"therefore the phantom self-clears at tomorrow's boot"* | ⛔ **expired within the hour — refuted BY THE FACT ITSELF** |

⇒ ⭐ **The observation cost nothing to keep. The interpretation cost a decision path.**

> ### 📌 THE PRACTICAL FORM — **the evidence class already tells you which rows need the field**
> **An `(P)` observation rarely expires. An `(I)` inference almost always does.** `(S)` sits between:
> a source reading is durable **at its SHA** and expires when the code moves *(which is `M3`)*.
> ⇒ ⛔ **Do not put an expiry on every row — put it on the `(I)` rows, and on `(S)` rows whose SHA
> can move.**

### M13 · ⛔ A LOG WITHOUT TIMESTAMPS CANNOT ANSWER "WAS IT TODAY?" — and it *looks* like it can

**Earned 06-Aug-2026, as a near-miss caught before it was reported.**

`logs/cron-reconcile-positions.log` carries **no per-line timestamps**. Investigating a 15:45
`FAILED — exit code 2`, the file contained a line that explained a failure perfectly:
`reconcile_positions.broker_fetch_failed: ZERODHA_API_KEY and ZERODHA_ACCESS_TOKEN must be set`.

⛔ **It was not today's line.** It occurs **exactly once in the whole file** (width stated), and
today's run reached the **mismatch** branch — which is unreachable without a *successful* fetch.

> ⭐⭐ **THE MECHANISM, AND IT IS THE REUSABLE PART: an append-only log invites you to read the
> NEAREST MATCHING LINE as the most recent one.** The file's `mtime` is today, the line is plausible,
> and the reasoning completes itself. ⛔ **Nothing about the query looks wrong** — the same shape as
> `M1`'s Group 3.
> ⇒ **RULE: when a log has no timestamps, it cannot establish WHEN. Go to a source that carries
> time** — here `cron_heartbeat` (`executed_at`, `status`, `message`), which settled it in one query.
> ⭐ **And prefer a source that would DISAGREE if you were wrong**: the heartbeat gave the exit code
> *and* the duration, both of which contradicted the crash hypothesis.

⚠️ **Sibling, same incident:** the *finding itself* was built on a **date coincidence** — *"it failed
on the first day a T+1 holding existed."* **Both halves were false**: it was not the first time
(4 exit-2s, incl. 29-Jul and 31-Jul with no T+1 holding), and it had not failed at all. ⭐ **A
correlation with today's date is the weakest possible evidence and the most tempting**
(`feedback_verify_the_finding_premise`).

### M12 · ⭐⭐ A REASONING ARTIFACT IS A CONFIGURATION ITEM — **and any numbered list carries its own count**

*Generalised from ChatGPT's point 4, adopted 06-Aug-2026: "treat reasoning artifacts — cost
numbering, dependency maps, evidence classes — as configuration items deserving the same consistency
checks as code."*

⛔ **"Be careful" is not a rule.** ⭐ **The checkable form:**

> **Any numbered list in a design document STATES ITS OWN COUNT, and the count is RE-VERIFIED when
> the list changes.** A total that is not re-added when a row moves is a stale assertion, not a
> total.

**FOUR instances, all in a single session (06-Aug-2026) — in artifacts whose entire purpose is to
make counting reliable:**

| # | artifact | said | was | how it was caught |
|---|---|---|---|---|
| 1 | F6's cost list | *"the fifth cost"* | **the sixth** — the list omitted cost 4, the three live sell GTTs | author re-reading |
| 2 | F6's regression heading | *"(6) — all six required"* | **8 listed** | author re-reading |
| 3 | sizing bucket totals | `31+34+6+42 = 113` ✅ | ⛔ **reconciles by ACCIDENT** — 6 phantom slots *(keys that do not exist)* almost exactly offset ≥4 real omissions *(existing keys in no bucket)* | a recount demanded by the reviewer |
| 4 | the semantic filter's destinations | *"9 · 4 · 3 · 1"* | **17 of 25 drops** — 8 had no stated destination | re-adding the rows |

> ### ⭐⭐ **INSTANCE 3 IS THE ONE THAT TEACHES THE RULE**
> **A total that RECONCILES is the strongest signal a reader has that a classification is complete.**
> There it reconciled **because two independent errors offset**. ⛔ **A correct-looking total is not
> evidence; the re-addition is.**

⚠️ **AND THE HONEST NOTE ABOUT HOW THESE WERE FOUND: three of the four were caught by the AUTHOR
RE-READING, not by any check.** ⛔ **That is LUCK, NOT PROCESS** — which is precisely the argument
for the stated-count rule: it converts a re-read into a check that can fail.

#### M12.1 · 🔴 VALIDATING A **PARTITION** — FOUR TESTS, AND **THE TOTAL GOES LAST**

*Folded in 06-Aug-2026 rather than filed as a fifth rule, per M10's convergence discipline.*

| # | test | asks |
|---|---|---|
| **①** | **membership validity** | is every listed member actually a member of the corpus? |
| **②** | **completeness** | is every corpus member in exactly one bucket? |
| **③** | **exclusivity** | is any member in more than one? |
| **④** | **arithmetic reconciliation** | do the parts sum to the whole? |

> ### ⛔⛔ **RUN ④ LAST. NEVER FIRST.**
> **Scored against 06-Aug's sizing partition:** **① FAILED** — six "members" of `DELIVERY-ONLY`
> were **not keys at all** *(the bucket's own heading says so)* · **② FAILED** — four existing keys
> sat in **no bucket** · **③ untested** · **④ PASSED** *(`31+34+6+42 = 113`)*.
> ⇒ ⭐⭐⭐ **④ PASSING IS WHAT HID ① AND ②.** The two failures were **near-equal and opposite**, so
> the total reconciled and closed the question before it was asked.
> 🏷️ **The general form: a correct total is the strongest available evidence of completeness, and it
> is evidence of nothing when the errors offset.** ⛔ **Check membership before you check arithmetic.**

### 🕯️ CANDIDATES — ⛔ **HELD, NOT PROMOTED. ONE INSTANCE EACH.**

⭐ **"One occasion is not a property" is this campaign's own standard, and it applies to its own
rules.** Both below were argued for on 05-Aug and both were **refused promotion on the same
ground**:

| candidate | its single instance | ⏰ **TRIGGER FOR PROMOTION** |
|---|---|---|
| **the three-identity-fields rule** — Row ID (immutable) · dotted key · display name; nothing joins on the display name | inventory #1 | **a second, INDEPENDENT instance** |
| **the conflict taxonomy** — `documentation error` · `implementation drift` · `intentional divergence` · `insufficient evidence` | the item-4 reconciliation | **a second, INDEPENDENT instance** |

⛔ **On the conflict taxonomy specifically: it is the FOUR FINDING-BUCKETS specialised to a
migration — ⛔ NOT a new scheme.** Recorded here so nobody later adopts it as one.

> #### ⏳ **RE-TESTED 06-Aug — ⛔ BOTH STAY HELD. Neither trigger fired.**
> **The three-identity-fields rule** was proposed for promotion on the grounds that
> `gtt_state.status` "fails it on its face — three writers, recording response not observation."
> ⛔ **REFUSED. That is ADJACENT, NOT THE SAME RULE.** The candidate is about **identity vs
> display** — *Row ID immutable · dotted key · display name; nothing joins on the display name.*
> Today's finding is about a **state field recording the wrong subject** (response, not
> observation) **with multiple writers** — which belongs to **M10** and to the marker/identity
> discussion in the F6 design, **not** to the three-identity-fields shape.
> ⭐ **The campaign's own standard applied to itself: a second instance must be the SAME rule, not
> a neighbouring one.** ⛔ **The conflict taxonomy is unchanged at one instance.**
📌 **Both are written in full at `docs/MASTER_PENDING_01-Aug-2026.md`, item-4 sub-entry** — ⭐ **held
as candidates does NOT mean unrecorded; it means not yet general.**

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

### V5 · ⭐⭐ A CHECK WITH NO FAILING INPUT IS AN IDENTITY WEARING A CHECK'S CLOTHES

> **BEFORE PROPOSING A VERIFICATION, EXPAND IT AND NAME A VALUE THAT WOULD MAKE IT FAIL.
> If no such value exists, it is not a check — and it will read as confirmation forever.**

**Earned 05-Aug-2026, and the incident is OURS — this bridge authored it.** The operator was to
be handed, as his confirmation that a live `CRITICAL — Capital Drift Detected` was benign:

```
delta == (opening − actual) + (expected − opening)
```

⛔ **Expand it: `opening` cancels.** It reduces to `delta == expected − actual`, which is the
**definition** of `delta`. ⇒ **it holds for ANY value of `opening` whatsoever** — the right one, a
wrong one, zero, a million. It was offered with the words *"the arithmetic closes to the paisa,
which is why I am putting it in front of you"*, and **the closing-to-the-paisa was a property of
algebra, not of the account.**
⭐ **It was caught by the implementer, not by the author** — and the conclusion it was defending
survived, but on a completely different basis: the **structural** measurement that the two operands
are different quantities (`snapshot.total` vs `margins.net`, one net of blocked margin and one not).
**The answer was right; the offered proof was empty.**

> ### ⭐ COROLLARY (05-Aug-2026) — ⛔ **a corollary, NOT a new rule: THE SAME TEST APPLIES TO RULES.**
> **A RULE WITH NO FAILING INPUT IS GUIDANCE IN STRICTER PROSE.** ⇒ **Either name the check that
> ENFORCES an invariant, or record it as `UNENFORCED — human discipline only`.**
> ⛔ **Both are acceptable. Pretending is not.**
> ⭐ **Why it belongs under V5 rather than beside it:** V5 is about a *check* that cannot go red;
> this is about a *rule* that cannot be violated detectably. **Same failure — confidence with no
> possible counter-evidence — one level up.**

### ⭐⭐ WHY THIS FAMILY IS DANGEROUS RATHER THAN MERELY USELESS
**A tautological check is not neutral — it MANUFACTURES CONFIDENCE.** It presents as *"the arithmetic
closes exactly"*, which is precisely the sentence a tired operator stops reading after.
⇒ **A MISSING check leaves you uncertain. A TAUTOLOGICAL one leaves you WRONGLY CERTAIN.**
That asymmetry is the whole reason this is a rule and not a style note: the failure mode is not a gap
in coverage, it is **false assurance delivered in the voice of evidence.**

**Three instances in one day makes it a CLASS, not a slip** — all three on artifacts about to be
handed to an operator:
1. **the `stuck_exiting` grep** — asked *"has this path ever fired?"* but the path returns
   `check_name="MANUAL_CLOSE"`, byte-identical to CHECK1's own disposition, and logs only on failure
   ⇒ **no observable difference between its target and something else. DELETED.**
2. **this identity** — no failing input exists. **REPLACED** with comparisons against *independent*
   quantities (the broker's own `used margin`, the day's booked P&L).
3. **the `2>/dev/null` grep** — a missing log file produced clean output that read as *"no locks"*
   ⇒ **the failure was suppressed rather than absent. FIXED.**

**The test, in practice:** say out loud what value would turn this red. If you cannot name one, you
have not written a check.

⛔ **NOT a variant of §V4 and NOT a variant of §M8, deliberately.** **V4** is about a test that asserts
what a stub was *told* to return — there the seam is mocked. **V5** is about a check with **no failing
input at all** — nothing is mocked; the arithmetic itself cannot fail. **M8** is about not *reading*
the record. These are three different ways to hold a worthless piece of evidence.

(Records: `docs/audit/ADDENDUM_capital_drift_05-Aug-2026.md` §3 · the operator card's REVISION 3
row 12 and REVISION 2 row 13.)

---

## 🧾 GOVERNANCE DEBT — **the category. ⛔ NAMED 06-Aug-2026, DELIBERATELY NOT POPULATED.**

🏷️ **Parent (G7): extends the existing DEBT-LEDGER concept** *(`MASTER_PENDING` §B)* — ⛔ **a second
axis on it, not a second ledger.**

> **GOVERNANCE DEBT = knowledge repeatedly rediscovered because OWNERSHIP, ASSUMPTIONS or DECISION
> BOUNDARIES are insufficiently encoded.**
> ⭐⭐ **Distinct from implementation debt in the way that matters: it raises REVIEW COST, not
> RUNTIME RISK.** A system carrying only governance debt runs correctly and is expensive to reason
> about — ⛔ which is exactly why it never gets prioritised against a runtime bug.

**ITS FIRST ENTRIES ARE ALREADY EARNED AND MEASURED — ⛔ NAMED HERE, NOT CHASED:**
1. **the `M8` re-derivations** — journald *(the census is not there)* · `clear_stale_state`
   *(boot-only, already recorded 05-Aug and re-derived 06-Aug)* · the **AR9 scope** *(two
   corrections in one day)*;
2. **four numbers that had to be RE-MEASURED on 06-Aug** because the record held them without their
   evidence set — the `438/438` corpus *(actually 483)*, the `44` cron command-lines *(46)*, the
   `M8` family ceiling in `PATHS.md` *(M14)*, and `G1`'s occurrence count *(5 → 9)*.

⭐ **The pattern in both groups is one thing: a conclusion recorded without the evidence set that
produced it, so the next reader cannot tell whether it is still true and re-derives it.**
⇒ 🏷️ **That is precisely what the decision ledger's `DATE/VERSION` column and `M9.1` exist to stop —
which is why this category is named now and populated later.**

⛔ **DO NOT POPULATE TONIGHT.** ⭐ Populating it is itself review work, and the bottleneck is
already governance.
> 🔄 **SCOPE NOTE (07-Aug 00:5x):** that line was written **06-Aug about 06-Aug** and is **not**
> struck — ⭐ **it still governs unsolicited population.** The block below is added **on the
> operator's explicit instruction**, and it is a **disposition record**, ⛔ not review work: it
> decides nothing and re-derives nothing.

### 🧾 ENTRY GD-1 · **THE EIGHT REFINEMENTS — HELD UNDER (E), 07-Aug-2026 00:5x**

**Eight further governance refinements were proposed after the sizing thread closed.** ⭐ **Every one
is reasonable. ⛔ Not one is admissible**, because the sizing thread's exit criterion **(E)**
*(`sizing_thread_conclusions_06aug2026.md` §9)* reopens the thread **only** for a reversed ruling,
production evidence contradicting a registered conclusion, or an open measurement resolving against
its recorded prediction — ⛔ **and (E) names "a governance refinement" explicitly as NOT one.**

**DISPOSITION OF ALL EIGHT — `M12`: the list states its count, and it splits 5 + 3 = 8.**

| # | proposal | disposition |
|---|---|---|
| **3** | dependency map | ⛔ **ALREADY EXISTS** — `dependency_map_06aug2026.md` |
| **4** | evidence vs policy | ⛔ **ALREADY `M9`** — the `(P)`/`(S)`/`(I)` classes |
| **5** | the behavioural contract | ✅ **ALREADY ADOPTED** — round 13 §3.2 |
| **6** | decision provenance | ⭐ **ALREADY IN THE LEDGER** — `Authority` + `DATE/VERSION` columns |
| **7** | a retirement criterion | ⛔ **COVERED** by the governance-debt exit criteria |
| **1** · **2** · **8** | analytical-vs-engineering closure · conclusion granularity · the review sequence | ⭐ **GENUINE refinements — ⛔ with NO MEASURED INSTANCE** |

> ## ⭐⭐⭐ **FIVE OF EIGHT WERE ALREADY IN PLACE — and THAT is the finding.**
> ⛔ **The pull to ADD is stronger than the memory of WHAT EXISTS.** ⭐ A campaign that cannot
> recall its own instruments will keep re-commissioning them, and each re-commissioning **looks like
> progress**.

🔓 **HELD, ⛔ NOT REJECTED — and the reopen trigger is written now rather than left empty** *(the
ledger's own lesson: an entry with no reopen condition is ABANDONED, not accepted)*:

> ### **TRIGGER: a SECOND ledger cold-read failure whose SHAPE the existing vocabulary cannot express.**
> ⭐ **Second, not first** — the first is a data point; the second is a pattern. ⛔ And *"cannot
> express"* is the operative test: a cold read that fails for a reason `M9`/`G5`/the ledger schema
> **already name** is a gap in APPLICATION, ⛔ not in vocabulary, and does not fire this.

### 🏁 **AND (E) BOUND ON ITS FIRST APPLICATION — `G7.1` SATISFIED, IN MINUTES**

**`G7.1`: a rule is not adopted until it has been APPLIED to a real case and has either BOUND or been
AMENDED.** ⭐⭐ **(E) was written at ~00:2x and its first real case arrived at ~00:5x.**
⇒ 🔴 **DECLINING THESE EIGHT IS WHAT ADOPTS (E).** ⛔ **Accepting them would have meant (E) never
bound — and it would have joined the three rules that passed their own test while failing their
purpose** *(the 1:1 telemetry rule · `M12` · `G7`)*.
🏷️ **Recorded as `(P)` — the rule was applied to a case that could have embarrassed it, which is the
only thing that validates a governance rule.**

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

### D2 · The **BEHAVIOURAL SURFACE INVENTORY** runs before EVERY deploy window
> ⚠️ **RENAMED 06-Aug-2026 from "the SHA inventory gate" — ⛔ and the old name is what BROKE it.**
> **(P)** run literally on 06-Aug it asked *"does each short SHA appear in the ledger?"* → **49
> named / 48 unnamed**, which reads as a catastrophic gate failure. ⛔⛔ **The check is
> STRUCTURALLY BROKEN: A COMMIT CANNOT CONTAIN ITS OWN SHA**, and the 48 "unnamed" were the docs
> commits that **are** the record. ⭐ **The name made a reviewer test the wrong predicate, and the
> wrong predicate produced a confident false FAIL.**

**THE INTENT, stated at the gate so a future reviewer does not revert to literal SHA counting:**
**WHICH COMMITS CHANGE BEHAVIOUR, and is each of those named?** ⭐ Re-run that way on 06-Aug:
**4 of 97 touched non-docs (2 were `PATHS.md`); only 2 were Python; both named.**
⭐ **It has already caught its own stamp commits twice** — trailing docs/stamp commits are exactly
what escapes an inventory, because they are the ones nobody thinks of as "a change".

### D5 · ⭐⭐ THE REGRESSION GATE IS A **BEHAVIOURAL EQUIVALENCE** GATE, not an "all tests pass" gate

**It proves NO NEW behavioural failures — ⛔ not that the suite is green.** ⭐ **This is the only
formulation that works on a suite with standing failures, and this suite has them** (AR2, AR3, the
`test_instance_lock` orphan flake).

**Run BASE and MERGE with the IDENTICAL invocation (§V2), then compare SETS in BOTH directions.**

> ### 🔴 **06-Aug IS THE CASE THAT PROVES IT, AND THE COUNT WOULD HAVE COST THE MOST**
> **BASE 55 failures → MERGE 29.** A bare count reads *"29 failures — block the push."*
> **The SETS read: MERGE-ONLY = 1** *(a pre-existing ordering flake — it fails identically on BASE
> when run in isolation)* **· BASE-ONLY = 27** ⇒ ⭐⭐ **THE PUSH FIXES 27 TESTS AND BREAKS NONE.**
> ⛔ **A count would have blocked a push that repairs 27.**
> ⭐ **And the arithmetic closes — `55 − 27 + 1 = 29` — which is the check that COULD have gone red.**
> 🏷️ **Record the direction: the count and the set disagreed in the direction that costs the most.**

⚠️ **A merge-only entry is not automatically a regression** — re-run it **in isolation on BOTH
trees** before calling it one. On 06-Aug that reclassified the single merge-only failure as a
full-suite **ordering artifact**.

#### 🔴🔴 D5.1 · **A PIPELINE'S EXIT CODE IS THE LAST COMMAND'S** — ⛔ a MEASURED DEFECT IN THIS GATE

🏷️ **Parent (G7): `G1` INSTANCE #7, property 2 — *"THE SUCCESS WAS THE ONLY THING ABSENT FROM THE
OUTPUT"* — applied to the SHELL instead of to `systemctl`.** ⭐⭐ **Same failure one layer down: the
thing that REPORTS the result is not the thing that DID the work.** In #7 the shell's *argument
splitting* produced a misleading report; here the shell's *exit-code propagation* does.

> ### **A PIPELINE'S EXIT CODE IS THE LAST COMMAND'S, NOT THE ONE YOU CARE ABOUT.**
> ### ⛔ **NEVER READ `$?` AFTER A PIPE ON A GATE.**
> ✅ **Use `PIPESTATUS`, or read the summary line, or do not pipe at all.**

**🔬 THE MEASURED INSTANCE — 07-Aug-2026 00:2x–00:40, the regression gate itself:**
the gate was invoked as `pytest tests/unit tests/integration -q --tb=no | tail -45`. **The task
reported `exit code 0`. NINE TESTS HAD FAILED.** The zero was **`tail`'s**.
⇒ 🔴 **A FALSE GREEN ON THE GATE THAT AUTHORISES PUSHES** — ⛔ and D5 is the gate whose entire job is
to decide whether a push is safe.

> ### ⚠️⚠️ **RECORDED AS A NEAR-MISS, ⛔ NOT AS A CAUGHT BUG — and the distinction is the point.**
> **What saved it was the structural check** *(zero non-docs diff vs `origin/main`; zero tests read
> any doc)* — ⭐ **and that check was run for a DIFFERENT REASON: to establish attribution for a
> docs-only push, not to audit the exit code.** ⛔ **The exit code was never the thing that was
> trusted, by luck of the change's shape.**
> ⇒ 🏷️ **On a push with a real behavioural surface there is no such structural escape, and the false
> green would have stood.** ⭐ **The rule exists so the next one is not luck.**

⭐ **Sibling, and it generalises past pipes:** this is the same family as *"a green check is evidence
only if it could have been red"* — ⛔ **an exit code that cannot see the failure it is being asked
about is a vacuous check**, not a lenient one.

⛔ **NEVER let `ahead 0` stand for PC == VM.** ⭐ **The VM is not a git checkout** *(it is a
`checkout -f` target of the bare repo and has no `.git`)*, so `ahead 0` **cannot answer it even in
principle.** **Keep both, always, and state which one you ran.**

**And the EVIDENCE HIERARCHY for a low-risk deploy — walk it in order:**
> **source diff → AST equivalence → regression equivalence → runtime deployment.**
⭐ **06-Aug walked all four on `c5c1926`:** the diff said *comment-only*; **AST comparison said
IDENTICAL**; the regression set said no new failures; md5 said PC == VM. ⭐ **Each step could have
contradicted the one before it — that is what made the chain worth walking.**

#### D6.1 · 📌 THE CRONTAB BASELINE — ⛔ **a hash taken AFTER the fact answers nothing**

`post-receive` prints *"crontab AUTO-INSTALLED from canonical"* on **every** push. To prove it was a
no-op you need a **PRE-push** hash. **06-Aug had none**, so it was closed by construction *(the diff
touched no `deploy/`, no `config/cron_registry.yaml`)* — ⭐ **sound, and weaker than a hash.**

**BASELINE RECORDED 06-Aug-2026 22:04:20 IST** *(post-push, service inactive, state known-good)*:

| what | md5 | |
|---|---|---|
| live `crontab -l` | **`b8276da7043975cda2d0ce6578960c6a`** | 148 lines · **46 command-lines** |
| `deploy/cron/trading-system.cron` | **`b8276da7043975cda2d0ce6578960c6a`** | ⭐⭐ **BYTE-IDENTICAL to live** |
| `config/cron_registry.yaml` | `cc6329f05c3b3fd2bdfac288e329485d` | the source of truth |

⭐ **Live == canonical settles 06-Aug better than the construction argument did — it proves the
OUTCOME, not just the input.** ⚠️ **One gap stays open and is stated rather than glossed: this cannot
prove live had not drifted BEFORE the push and been silently repaired by the auto-install.** ⛔ **Only
a pre-push hash answers that — which is why this baseline now exists.**
⚠️ *Noted, not chased: `SYSTEM_MAP` records **44** command-lines as of 30-Jun; it is **46** today.*

**⇒ STANDING PRE-STEP, before every push:** `ssh trading-vm 'crontab -l | md5sum'` — **record it,
then compare after.**

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

---

### AR9 · The capital-drift CRITICAL — noise accepted **for one session only**, with four reopen conditions

- **Accepted:** `⚠️ Capital Drift Detected` (**CRITICAL**, `Module: order_reconciler`) fires on a
  delivery day **by construction**, and is treated as **informational**. It fired **3×** on
  05-Aug-2026 (~10:01 → 11:51:20), the first day this system traded delivery.
- **Reasoning — both halves MEASURED at the DEPLOYED SHA `0197923`** *(⭐ `order_reconciler.py` and
  `drift_handler.py` are byte-identical at `0197923` and HEAD, so the cites hold at both)*:
  **(1) THE OPERANDS ARE DIFFERENT QUANTITIES.** `expected = snapshot.total` (**total** capital —
  reservations reduce the *available* buckets, not the total) vs `actual = margins.net` (Kite's
  `equity.net`, **net of blocked margin**; `available.cash` is parsed **separately**)
  ⇒ **the delta IS the deployed capital.** `order_reconciler.py:3590-3591`, `zerodha_adapter.py:1452-1453`.
  **(2) IT CANNOT ESCALATE.** Published as `source_module="order_reconciler"` (`:3667`);
  `drift_handler._ESCALATING_SOURCES` (`:66-70`) contains only `fund_manager`,
  `fund_manager_self_check`, `fund_manager_bucket_overflow`; a non-escalating source logs **one INFO
  line and returns** (`:147-161`) — before any tier, counter, `soft_kill` or `hard_kill`.
  ⭐ **The tolerance is `max(₹50, 10% of expected)`, and FIX-190 (Bug I) added that band to silence
  exactly this noise FOR A LEVERED INTRADAY BOOK.** Delivery is 1× ⇒ **a delivery book deploying more
  than ~10% of capital breaches it by construction**, and the bucket is 30% of total.
> ### 🔴🔴 **SCOPE CORRECTION — 05-Aug-2026 EVENING. AR9 SCORED THE *IN-SESSION* REGIME ONLY.**
> ⛔ **The `max(₹50, 10%)` band above is IN-SESSION. Out of session the tolerance is a flat ₹50** —
> `config/system_config.yaml:368` labels it *"Production threshold (**out-of-session / overnight**)"*,
> `:369` labels the pct *"**in-session** tolerance"*.
> ⭐⭐ **AND OVERNIGHT IS THE ONLY TIME A DELIVERY POSITION CAN BE HELD.** ⇒ **the regime AR9
> actually needs to cover is the one it was never scored against.**
> **(P) MEASURED the same evening:** 3 in-session breaches at `tolerance≈990`, then **5 out-of-session
> at `tolerance=50.00`, exactly 30 min apart, identical operands, ALL DELIVERED as CRITICAL** — and
> continuing, because the service stays up while delivery is held.
> ⛔ **This is a SCOPE CORRECTION ON AR9, not a new finding and not a reopen.** ⭐ It is recorded so
> nobody later reads *"accepted, 3×, one session"* as covering an overnight carry. **It does not
> propose a tolerance, and the one-session evidence bar is UNCHANGED.**
> ✅ **What DID close: the delta decomposes exactly** — `632.01 = 587.40 deployed + 44.61 unsettled
> realised`, both sides to the paisa across two independent subsystems. **No money is missing.**
>
> ### 🔴🔴 **SECOND SCOPE CORRECTION — 06-Aug-2026. AR9 COVERS *ONE CHECK*, NOT THE FAMILY.**
> ⛔ **AR9's subject is the G3 drift alarm, which publishes `source_module="order_reconciler"` —
> a tag DH1 BARS from escalation** (`drift_handler.py:66-70`). ⭐ **That is why it was acceptable
> as noise.**
> ⛔ **It does NOT cover `_check7` / BL-3 `CAPITAL_ACCOUNTING_DRIFT`**, which publishes
> `source_module="fund_manager_self_check"` (`order_reconciler.py:3782`) — **a tag DH1 does NOT
> bar** ⇒ **single-sample SOFT/HARD escalation.**
> ⭐⭐ **TWO CHECKS · ONE FILE · ONE ALARM NAME · TWO DIFFERENT SAFETY POSTURES.** That is the
> trap, and naming it is the fix. **(P)** A reviewer extended AR9's scoped result to `_check7`
> from recall on 06-Aug and was wrong **in the direction of false reassurance**; it was caught
> only by measuring the tag at source.
>
> #### 📌 **REOPEN CONDITION THIS EARNS (the fifth):**
> ⛔ **Any drift publisher whose `source_module` is IN `_ESCALATING_SOURCES` is OUTSIDE AR9 and
> requires its own assessment.** ⭐ Membership is the discriminator — **not the alarm's name, not
> the file it lives in, not the check number.**
> ⚠️ **The trap is documented in the code too:** `drift_handler.py:17` still says *"Today that's
> `{"fund_manager"}`"* — **one** member — while the frozenset four lines below has **three**.
> ⛔ **Filed separately; do not fix in passing.**

> #### ✅⭐ **VERIFICATION UPGRADED 05-Aug 23:2x — ⛔ NOT a new instance; the SAME breach, held to a higher standard.**
> The decomposition above was previously **arithmetic that closed**. It is now **operands measured
> independently**: opening `9,883.70` **(P)** `fm_ledger` INIT 08:15:15 · realised `44.61` **(P)** by
> **two** paths (7 `RELEASE_USED` rows **and** the same 7 trades' `net_pnl`) · CNC purchase `587.40`
> **(P)** `qty_filled × entry_actual_price`. **(S)** `order_reconciler.py:3590-3592` —
> `expected = snapshot.total`, `actual = margins.net` ⇒ **the mechanism is confirmed at source, not
> inferred from the numbers.** **(P)** the band: `tolerance=50.00 (base=50.00)`, **delta = 12.64× it**;
> **(S)** `:3626-3631` applies the pct widening **during market hours only**.
> ⛔⛔ **AND THE VACUITY TRAP IS EXPLICITLY EXCLUDED — which matters, because `V5` deleted a claim of
> this exact shape earlier the same day.** Written as `(opening−actual)+(expected−opening)` the
> identity is vacuous; **neither term here was obtained by subtraction from the alert.** ⭐ **It could
> have gone red: a trade value of 590, or a realised P&L of 40, and the sum is not 632.01.**
> ⛔ **Nothing about the acceptance changes: still ONE session, still no tolerance proposed.**

- **Accepted by:** the bridge, 05-Aug-2026, on the measurement above. **Rama has not been asked to
  ratify a tolerance and must not be, on this evidence base.**
- ⛔⛔ **THE EVIDENCE BASE IS ONE SESSION. That is explicitly NOT enough to justify changing a
  money-path governor.** ⭐ **What would move it is RECURRENCE ACROSS MULTIPLE DELIVERY SESSIONS**,
  not a louder single day. ⇒ **no tolerance value is proposed here or anywhere.**

> ### 🔴 REOPEN CONDITIONS — **any ONE of these ends the acceptance**
> 1. **Any of the six `THIS IS REAL IF` discriminators trips** (`docs/expected_alarms.md` §3a) —
>    the broker's own books not balancing, the gap not matching `used margin` read at the same
>    instant, the local-vs-opening difference not matching booked P&L, a non-empty `human_orders`,
>    an out-of-hours fire with a non-zero `actual`, or a `kill_switch_state` that is not `INACTIVE`
>    today.
> 2. **The alert appears from an ESCALATING source** (`fund_manager`, `fund_manager_self_check`,
>    `fund_manager_bucket_overflow`) — ⛔ **that is a different event entirely and CAN kill.**
> 3. **`_ESCALATING_SOURCES` is modified** — the acceptance rests on the reconciler being outside it.
> 4. ⭐ **The delivery configuration surface sets a tolerance** (§A-DEC-3 order item 5) — **at which
>    point this stops being a closed thread and becomes a LIVE DESIGN INPUT.**

⚠️ **Recorded because an accepted risk with no reopen condition is not accepted — it is abandoned.**
📌 Registered: `MASTER_PENDING` **§B#7** (fourth two-pipeline coupling member, and the first that did
not wait to be predicted) · **§B#5** (DH1's third instance, first with real delivery on the book).
Worksheet: `docs/audit/ADDENDUM_capital_drift_05-Aug-2026.md`.
