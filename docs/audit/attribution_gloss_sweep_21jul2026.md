# ATTRIBUTION-GLOSS SWEEP — 21-Jul-2026 (C5)

**Read-only forensics + in-place source corrections. Docs/memory only — no code, no capital
path, no service touched.** Consolidates a recurring failure class that has produced ~8 instances
in three weeks. The point of the sweep is not the eight corrections; it is §5 — *what would
prevent the ninth.*

---

## 1 — THE FAILURE MODE, AND ITS TELL

**Attribution gloss:** a claim gets **restated more confidently than its source supports**, and
the restatement is then **cited downstream as authority** — so the hedge that the primary evidence
carried is gone by the time a third document rests on it.

**The tell — a summary sharper than the thing it summarises.** When a claim reads *more certain*
than the evidence you can actually see for it, that is the trigger to stop and re-derive from
source. Every instance below has this shape: a hedged finding ("batch-4 reasoned the mechanism
correctly", "one regime, roughly powered", "the reader is a *different quantity*") became an
unhedged assertion ("masked by the cap", "cannot compute", "the reader is **not involved at
all**"), and something cited the assertion.

It is a **class, not a list**: the same mechanism produced all eight, so the fix is a change to
*how claims get made*, not eight more corrections.

---

## 2 — THE EIGHT INSTANCES

| # | The gloss (sharper than source) | Source actually said | Author | State before today |
|---|---|---|---|---|
| 1 | perf_weight is **masked by the concentration cap** (cited "Q9 batch-4" as authority) | batch-4 reasoned the POST-cap mechanism *correctly*; refuted 19-Jul (moves qty 233/298) | Claude (decision 06 synthesis) | docs corrected 19-Jul; **memory ×2 still live** |
| 2 | the seed & Phase-2 **never consult** the daily-loss reader | reader IS called on the rehydrate/seed path (`fund_manager.py:1760`); absent only from the cancellation *algebra* | Claude (`live_seed_mc1_wired`) | §2 corrected 20-Jul; **TL;DR, §5 table, SYSTEM_MAP, memory still live** |
| 3 | **THE BASELINE IS NOW 14** (a fixed test-failure count, used as a deploy gate) | a property of one environment at one moment; the same banner says "moved 12→16" | Claude (SYSTEM_MAP/report) | runbook+rule corrected 20-Jul; **SYSTEM_MAP + report still live** |
| 4 | regime **cannot compute** / **insufficient daily candles** (a data shortage) | an April **missing-handle** artifact; live Kite served 271 bars 20-Jul, status OK | Claude (decision 07 / regime docs) | decision 07 corrected 20-Jul; **one investigation doc still live** |
| 5 | Checklist A3 green = pass; A5 "INACTIVE at close"; A7 "403s after 10:00 = BAD" | A3 was `0.0==0.0` (vacuous); close-state is SOFT_KILL by design; window is [10:00,15:00) | Claude (MONDAY checklist) | A3 fixed in-place; **A5, A7 still wrong at source** |
| 6 | `check_scanner:703` is an **unfixed S4 `/health`-401 sibling** | it calls **external Chartink**, where a 401 IS a real anomaly (REFUSED WITH EVIDENCE) | Claude doc + carried in planner instruction files | corrected 20-Jul; effectively closed |
| 7 | the old M-C1 test proves "reader is a different quantity" (a **numerical** proxy) | a number pinned as a proxy for a *structural* property; replaced by structural pins (`e8313f0`) | Claude (the test) | corrected 20-Jul; effectively closed |
| 8 | (a) **"soft-kill, system stays RUNNING"**; (b) "liveness will **spam DOWN** while halted" | (a) a persisted kill HALTS (exit 4); (b) `classify_liveness` treats it as OPERATOR_HALT → silent | (a) **planner** (instruction premise); (b) **Claude** (in-session) | both corrected at first writing (today) |

**Corrected in place today (dated, superseded claim left legible — history not rewritten):**
`SYSTEM_MAP.md:96` (#3) · `SYSTEM_MAP.md:102` (#2) · `MEM/q9_live_seed_mc1_wired_19jul.md:39` (#2) ·
`MEM/decision_packages_19jul.md:26` (#1) · `MEM/decision_readiness_triage_19jul.md:16` (#1) ·
`masking_premise_sweep_19jul2026.md:43` (the meta-gloss, see §4) · `MONDAY_POST_SESSION_CHECKLIST.md`
A5 `:145` + A7 `:170` (#5) · `consecutive_losses_gate_wired_19jul2026.md:366` (#3) ·
`regime_direction_preference_investigation_20jul2026.md:26` (#4). Instances 6, 7, and 8 were
already corrected at source and needed no new edit.

---

## 3 — WHY THEY ACCUMULATE (the structural cause)

Every session bootstraps from the **prior session's summary** — the memory palace, the SYSTEM_MAP
banners, a decision doc's TL;DR — not from the primary evidence. Summaries compress, and
**compression drops the load-bearing qualifier**; that dropped qualifier *is* the gloss. Then the
next session recalls the summary and treats it as ground truth. So the glosses compound along the
exact path the memory palace is designed to serve (recall) — which is why an uncorrected gloss in
`SYSTEM_MAP.md` or `MEM/` is the most expensive kind.

Three amplifiers observed in this batch:

- **Correction-by-location.** A claim gets corrected in one section (a `§2 SUPERSEDED` block, an
  A3 note) while the *same* claim survives un-struck in a TL;DR (`live_seed_mc1_wired:22`), a
  summary table (`:172`), a sibling check (checklist A5/A7), or a downstream doc (`SYSTEM_MAP`).
  This is the "half-corrected — the worst state" the masking sweep itself named.
- **The correction never reaches the reservoir.** The 19-Jul masking correction was applied
  across `docs/` but **not** to `MEM/decision_packages_19jul.md` or `MEM/decision_readiness_triage_19jul.md`.
  The reservoir kept feeding the gloss into every subsequent session.
- **A correction doc can itself gloss** (§4).

---

## 4 — ⚠️ THE CORRECTION THAT WAS ITSELF A GLOSS

`masking_premise_sweep_19jul2026.md:43-44` asserted: *"Memory palace and `PATHS.md` carried no
assertion."* This was **false** — two memory files carried the masking assertion, uncorrected, and
kept doing so for two more days. A *sweep whose job was to close the class* produced a summary
sharper than the check it had done: it claimed completeness (`no assertion`) where it had asserted,
not grepped. This is the failure mode turned on itself, and it is the strongest single argument for
§5.4 below — a claim of completeness is a claim, and gets the same tell.

---

## 5 — ⭐ WHAT WOULD PREVENT THE NEXT ONE

The corrections above are only worth the time if they change how claims get made. Five practices,
each tied to instances that would not have happened under it:

**5.1 — Cite primary evidence, not a summary; re-verify the citation at use.** A gloss becomes
*authority* only when a downstream doc cites a **prior summary** instead of the code/data/test.
Rule: a load-bearing claim carries a primary citation (a `file:symbol`, a measurement, a named
test) and that citation is re-checked where it is used, not inherited. Corollary: **citations rot
— pin by symbol/behaviour, never a mutable proxy.** #2's own correction cited `:1736`, which had
already drifted to a blank line; #7's test pinned a *number* for a structural property. Both would
survive a `file:line`; neither survives contact with a `git blame` six months on. (Prevents the
*propagation* half of #1, #2, #7.)

**5.2 — A summary must carry its source's hedge.** When you compress, keep the load-bearing
qualifier: "batch-4 reasoned it *correctly*", "one regime, *roughly powered*", "absent from the
*cancellation algebra*", "*could* start at 10:00". Dropping the qualifier is the gloss. If a hedge
won't fit the summary, the summary is too short for that claim. (Prevents the *birth* of #1, #2,
#4.)

**5.3 — Label measured-vs-assumed at the point of claim.** #3 ("baseline is 14") and #4 ("cannot
compute") and #5's A3 were **assumptions written in the grammar of measurements**. Every GOOD/BAD,
every threshold, every "the baseline is": was it *measured* this session, or *assumed* from a
prior one? An assumed number in an assertive sentence is a gloss waiting to be cited. (This is the
sibling of the already-standing rule *a green check is evidence only if it could have been red*.)

**5.4 — A correction must reach every carrier, and a claim of completeness gets the tell.** When
you correct a claim, `grep` **all** carriers — `docs/` **and** `MEM/` **and** `SYSTEM_MAP.md` **and
the origin doc's own TL;DR/tables/sibling rows** — and correct each, or the reservoir re-feeds it.
And a sweep that says "nothing else carries this" must have **run the grep**, not asserted it (§4).
A completeness claim is a claim. (Prevents #1's two-day survival, #2's SYSTEM_MAP survival, and the
meta-gloss.)

**5.5 — Correct the highest-propagation surface first.** `SYSTEM_MAP.md` ("read before any work")
and `MEM/` (recalled every session) travel furthest; a gloss there is inherited by every future
session before any primary evidence is consulted. They are the first place to correct and the
first place to audit — not the last.

**5.6 — Stale-read-after-correction: a correction must reach every READER who already copied from
the source, not only every carrier.** §5.4's "reach every carrier" has a second clause. On 21-Jul
the register's CT line was corrected at 14:40; a follow-on instruction was then written at 15:14
**from a 14:18 read of the *pre*-correction register** — the fix reached the document, but not the
reader who had already extracted from it. The result was a stale-premise instruction (the 16-Jul
CT-harness finding restated as current) *plus* an invented causal claim ("the hazard is why the six
tests are parked" — two unrelated facts joined by an unchecked "that is why"), one afternoon after
this sweep named the class. Prevention: a correction to a high-traffic source (register /
`SYSTEM_MAP.md`) should be **announced to anyone with work in flight against it**, not merely
written; and a reader should **re-read the source at the moment of extraction**, not trust a read
minutes old — §5.1's "re-verify the citation at use", applied to whole documents rather than single
claims. (Contributed by the author of the CT-harness instruction, owning the error — the honest
half of a correction.)

**5.7 — The stale-read sub-pattern generalises from citations to INSTRUCTION CONSTRAINTS.** §5.1/§5.6
treat stale *citations*; the same failure shows up in *constraints an instruction carries forward*.
Three shipped this week, all the same shape — a constraint true in an earlier context, carried without
re-checking whether the context still held: **(a)** the CT-harness hazard (§5.6, stale by three days +
an invented causal join); **(b)** the **boot-path deferral** whose stated reason ("tomorrow's boot would
test two things at once") had already expired — E4/W10's boot behaviour had been observed three times
(Monday, this morning, the 11:57 restart); only the EOD *signature* was pending, which is not a boot
observable; **(c)** **"keep the regex pin green with zero test edits"** (B1 instruction), true for the
earlier *no-floor* B1 design but not once B5's floor-passing was folded in — the pin matched empty parens
`\(\)` and passing the day-floor makes the call `today_realized_pnl_carryover(_start_of_today_iso)`, so
"zero edits" and "pass the floor to both" were mutually exclusive against the actual pin. **Prevention: an
instruction constraint earns the same "re-verify at use" as a citation** — a carried-forward constraint is
a claim about the current code/state and must be checked against it, not against the context it was
written in. (Contributed by the author of all three, owning them.)

**The one-line version:** *the gloss is the dropped qualifier; the harm is the summary cited as
authority. Carry the hedge, cite the source, and sweep every carrier — break any one link and the
class stops.*

---

## 6 — ATTRIBUTION (A5: no author graded gently)

The planner's self-accounting was "three mine, two yours." From the **repository's** vantage —
which is what this sweep corrected — the split is far more lopsided: **seven of the eight glosses,
plus the drifted `:1736` inside a correction, live in Claude-authored artifacts** — audit reports,
memory files, the MONDAY checklist, and the M-C1 test. The planner's are #8a (the "stays running"
premise) and the `:703`-sibling framing that rode through the instruction files. #8b (the liveness
overstatement) was Claude's, this session.

Stated without softening: **this is predominantly a Claude failure mode.** The summaries that drop
the hedge, the memory lines that outrun their evidence, the tests that pin a proxy — those are
mine to make and mine to prevent. A sweep that distributed the blame evenly to match the planner's
"3/2" would itself be an attribution gloss. §5 is written accordingly — it targets the way *I*
summarize.

---

*Read-only except the ten dated in-place source corrections listed in §2 and this file. No code,
no config, no capital path, no service. Corrections strike-through the original and add a dated
note; nothing was deleted.*
