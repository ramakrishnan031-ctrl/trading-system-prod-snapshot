# MEMORY.md compaction — 20-Jul-2026 (relocation, verified, with two self-corrections)

**PC-side memory-palace maintenance. No production change of any kind.** The active index had grown
back to **23,545 bytes against a 24.4 KiB (24,986 B) hard read limit** — ~1.4 KB of margin, one day
after the 19-Jul compaction left it at 18,915 B. Method unchanged: **relocate, do not delete.**

Two claims I made earlier in this session **failed their own verification** and are corrected in §E.
They are recorded here rather than quietly dropped, because both are instances of the standing rule
that an audit finding is a hypothesis until its premise is checked.

---

## A. Snapshots — three points, so growth is measurable

| Snapshot | MEMORY.md | lines | files |
|---|---|---|---|
| `memory_snapshots/memory_snapshot_2026-07-19_201338/` | 23,503 B | — | 505 |
| `memory_snapshots/memory_snapshot_2026-07-19_205049/` (19-Jul baseline) | 24,602 B | 75 | 506 |
| **`memory_snapshots/memory_snapshot_2026-07-20_190729/` (this batch's baseline)** | **23,545 B** | **85** | **583** |

The `_190729` snapshot is the "before" that every verification below diffs against. All three are
retained; none was overwritten.

---

## B. The result

| | bytes | KiB | lines | margin to 24,986 B |
|---|---|---|---|---|
| before | 23,545 | 22.99 | 85 | 1,441 B |
| **after** | **16,627** | **16.24** | **85** | **8,359 B (8.16 KiB)** |
| saved | 6,918 | 6.76 | 0 | +6,918 B |

**Target chosen: ≥8 KB of margin**, i.e. enough that ~27 batches at the enforced budget (§D) fit
before the ceiling is a concern again — versus the ~6–7 batches the 19-Jul compaction bought. The
line count is unchanged at 85 because four irreducible enumerations were **split** rather than cut
(§C), which costs ~0 bytes and preserves every item.

### ⚠️ Unit reconciliation (two figures, both correct)
An interim figure of "margin 6,444 B" was computed against a **24,400 B** limit (1 KB = 1000 B). The
19-Jul report's arithmetic (18,915 B + 6,071 B = 24,986 B) uses **24.4 KiB = 24,986 B**. This report
uses the 19-Jul convention throughout, so **margin = 8,359 B**. Not a disagreement — two conventions
over the same file, reconciled exactly. The KiB convention is now the one in force.

---

## C. Removals — itemised, each with its destination

Every removal is either (i) a duplicate of a link that survives on the same line, or (ii) content
that already lives in the linked topic file. Nothing was deleted outright.

| Removed from the index | Destination |
|---|---|
| **14 × `[[slug]]` that duplicated the same line's own `(file.md)` link** — decision-packages, monday-post-session-clean, artifact-baseline-reconciliation, d3-band-inversion, forward-shadow-capacity, candle-retention-perfallocator, daily-report-classification-fix, killswitch-autoclear, capital-vocabulary, feedback-regression-must-not-cross-midnight, feedback-never-classify-by-free-text, feedback-live-vs-latent, feedback-verify-the-finding-premise, feedback-verify-rc-not-output | the **same line's** markdown link, unchanged — target still one click away (verified, §D) |
| `[[monday-preboot-readiness-19jul]]` | relocated one hop: linked from `monday_first_real_boot_20jul.md`, `decision_readiness_triage_19jul.md`, `artifact_baseline_reconciliation_19jul.md` — all index-linked |
| `[[regime-thesis-validation-18jul]]`, `[[regime-phase1-investigation-18jul]]`, `[[regime-minscore-control-18jul]]` | relocated one hop into the new `regime_computable_today_20jul.md`, which is index-linked from line 11 |
| Two separate MONDAY lines (pre-boot + first-real-boot) merged into one | [[monday-post-session-clean-20jul]], [[monday-first-real-boot-proven-20jul]] |
| `Q10 Part B backfill = MOOT` line + the old `REGIME not computable` line | **superseded by tonight's runtime result** — replaced by the line-11 verdict; history in `regime_computable_today_20jul.md` and `docs/audit/q10_part_b_backfill_20jul2026.md` |
| `Regime direction-preference ALREADY EXISTS (shadow)` line | `regime_direction_preference_investigation_20jul2026.md`, `regime_computable_today_20jul.md` |
| Q9 line: `deploy-19jul-consecutive-losses`→`d271525`, "LIVE-vs-LATENT", "Regression base = MAIN-TREE, BASELINE 14" | the 8 q9 batch topic files; BASELINE 14 also survives on the PC test-env line |
| `> 🏁 BATCH-SAFE pile EMPTY (17-Jul)` blockquote (obsolete) | [archive](../../..) — `MEMORY_ARCHIVE_2026H1.md` |
| Assorted prose: "rehydrate 0/0/0", "`sim_R` 6.4% null", "(0-34 junk)", "E4/W10 runbook prepared-not-executed", "never git-committed", "at this size" | all present in the respective topic files / `docs/audit/` reports |

### Splits (0 bytes lost, per-line budget satisfied)
| Line split | Why |
|---|---|
| DONE/DEPLOYED archive line → header + slug list | 9 slugs are irreducible |
| Fallout line → **(a)** and **(b)** | ~12 queued items, none droppable |
| RAMA-ACTIONS → security list + decisions list | A4 requires the operator list in full |
| Deferred (OPEN) → two lines | 6 slugs |
| Throttle line → finding + **its own `--db <copy>` RULE line** | the standing rule now stands alone and is far easier to find |

---

## D. Verification — against the `_190729` snapshot

Verifier: `mem_verify.py` (scratchpad, read-only). Re-run after every edit.

- **Byte budget: 0 lines over.** The header's own check prints nothing:
  `LC_ALL=C awk 'length>(index($0,"🔝")?450:300){print NR": "length"B"}' MEMORY.md`
- **The check can go red** — it flagged 4 real violations (L11 301 B, L16 304 B, L17 334 B, L75 302 B)
  after my first pass, which is why they were trimmed; lowering the cap to 200 lights up 28 lines.
  A check that cannot fail is not evidence.
- **Unresolved `[[slug]]`: 0 before, 0 after.** **Missing `(file.md)` targets: 0.**
- **Dropped slugs: 18 — every one accounted for.** 14 still linked on the same line via `(file.md)`;
  4 relocated one hop and confirmed reachable from index-linked files (§C).
- **Added slugs: 1** — `regime-computable-today-20jul`.
- **Every A4-mandated item survives, by line number (0 missing):**

| Item | line | Item | line |
|---|---|---|---|
| Board, all 10 items enumerated | 6 | RULE: never `scripts/*.py --db <copy>` | 18 |
| `docs/decisions/00_INDEX.md` pointer | 6 | RULE: regression must not cross midnight | 46 |
| Artifact baseline + cross-batch rule | 13 | RULE: read a threshold from code | 38 |
| RULE: within-batch ≠ cross-batch | 13 | RULE: capital vocabulary | 45 |
| RULE: never run the forward-shadow recorder | 15 | RULE: a finding is a HYPOTHESIS | 49 |
| **RULE: expectations need their own validation pass** | **50** | RULE: a green check could have been red | 50 |
| REGIME verdict (new) | 11 | `:703` REFUSED WITH EVIDENCE | 74 |
| `preflight/checks/signals.py:28` queued | 8, 74 | `scanner_unreachable` stays a WARNING | 74 |
| `/health` 4th-instance sweep queued | 8, 76 | Rama operator list | 84, 85 |

---

## E. ⚠️ Two of my own claims, corrected

**E1. "The dropped link `[[feedback-verify-rc-not-output]]` was dangling — removing it is a repair."
This was WRONG.** The verifier reports **0 unresolved slugs in the pre-compaction file**. That slug
resolves perfectly well — to `feedback_verify_rc_not_output.md`, via the filename-derived form
(underscores → hyphens). The file *also* answers to its frontmatter name
`verify-check-the-rc-not-the-output`. Both forms are valid; there was no dangling link. The removal
was harmless (the file is still linked on the same line) but it was **not** a repair.

**E2. "The 19-Jul report's `DROPPED = 0` had a blind spot." This was WRONG, and it maligned a
correct check.** The 19-Jul report §C explicitly examined this exact file, noted that the slug
resolves via its frontmatter `name:`, and classified it as *not* an orphan. That analysis was right.
My claim inherited E1's false premise and propagated it.

Both are the same failure mode as the five wrong checklist expectations found earlier today: **the
prose assertion was never measured.** The SQL/queries were fine; the sentence about them was not.
This is why the rule "expectations need their own validation pass" now sits on line 50 — and it
applies to *my* claims, not only to the checklist's.

---

## F. The regrowth diagnosis — measured, not assumed

**The rate.** 19-Jul post-compaction **18,915 B / 70 lines** → 20-Jul pre-compaction
**23,545 B / 85 lines**, over ~22 hours: **+4,630 B and +15 lines**, across roughly 8 batches.

**⚠️ This corrects my working hypothesis.** I had assumed the line *count* was stable and only the
lines were getting fatter. The numbers say otherwise — **both axes moved**:

| axis | 19-Jul convention | 20-Jul actual | over |
|---|---|---|---|
| lines per batch | 1 | **~1.9** | 1.9× |
| bytes per new line | 300–400 B | **~309 B** (4,630 ÷ 15) | at the ceiling |
| **bytes per batch** | **~350 B** | **~580 B** | **1.7×** |

So the 19-Jul convention ("a batch adds ONE pointer line") was not broken by fat lines alone — it was
**exceeded on the count axis too**, at roughly two lines per batch, each already at the 300 B ceiling.
A rule that caps only size would have missed half the growth; a rule that caps only count (the
original "one line per entry") missed the other half. That is why the previous convention bought 6–7
batches instead of the 15–18 it projected.

### The mechanism now in force (header, line 3)

1. **≤300 B per line; ≤450 B for a 🔝-pinned line.** Caps the size axis.
2. **A batch adds ONE line *or* edits one — never past cap.** Caps the count axis. If a pointer needs
   a second clause, the clause goes in the topic file, not the index.
3. **A check that is byte-accurate, exemption-aware, and currently green:**
   `LC_ALL=C awk 'length>(index($0,"🔝")?450:300){print NR": "length"B"}' MEMORY.md`

**Why the check is written that way — the previous one was wrong twice.** The rule as first drafted
said `awk 'length($0)>300'`, which (a) counts **characters, not bytes** — on this emoji-dense file it
reported 23 where the true byte count was 25 — and (b) had **no 🔝 exemption**, so it was red on 23
lines the rule considered compliant. *A check that is permanently red is exactly as useless as one
that can never go red*, and it would have been ignored within a day. `LC_ALL=C` makes `length` count
bytes; `index($0,"🔝")` selects the tier.

**Headroom, stated honestly:** 8,359 B. At the enforced budget (≤300 B, one line per batch) that is
**~27 batches**. At the *observed* 20-Jul rate of ~580 B/batch it is **~14 batches**. The compaction
bought the margin; only rule 2 decides which of those two numbers applies.

**The remaining structural risk:** the top 32 lines still hold **60%** of the content (9,964 of
16,542 B), down from 67%. The budget caps how bad that can get but does not by itself flatten it. If
the index crosses ~20 KB again, the next lever is **rotation on a schedule** — Core-Reference entries
older than ~30 days move to `MEMORY_ARCHIVE_2026H1.md` — not another prose trim.

---

## Deploy note
The memory palace is PC-side and outside the repo; the compaction is not a git change. This report is
the only repo artifact (docs-only). Nothing here touches anything that boots tomorrow.
