# MEMORY.md compaction — 19-Jul-2026 (relocation, verified)

**PC-side memory-palace maintenance. No production change of any kind.** The active index had grown to
**24,602 bytes (24.03 KB) against a 24.4 KB hard read limit — ~383 bytes of margin, failing silently.**
The deferral (to after Monday) was lifted: the snapshot precondition was met, and a quiet Sunday with
nothing else in flight is the *right* window for a careful restructure, not a live trading Monday.
Method: **relocate, do not delete** — every removed line's detail already lived in its linked topic file;
the index prose was a duplicate, now compressed to a pointer.

---

## A. Snapshots (two points, so growth is measurable)

| Snapshot | Path | MEMORY.md captured |
|---|---|---|
| pre-last-2-batches | `memory_snapshots/memory_snapshot_2026-07-19_201338/` (505 files, 2.07 MB) | 23,503 B (22.95 KB) |
| **pre-compaction (this batch's baseline)** | `memory_snapshots/memory_snapshot_2026-07-19_205049/` (506 files, 2.08 MB) | **24,602 B (24.03 KB), 75 lines** |

The `_205049` snapshot is the "before" the verification diffs against.

---

## B. The result

| | bytes | KB | lines | margin to 24.4 KB |
|---|---|---|---|---|
| before | 24,602 | 24.03 | 75 | 0.37 KB |
| **after** | **18,915** | **18.47** | **70** | **5.93 KB** |
| saved | 5,687 | 5.55 | 5 | +5.56 KB |

**Target chosen: real headroom over the soft 17.1 KB, prioritising safety over a byte count.** I landed
at 18.5 KB rather than squeezing to ~16 KB because the entire **Feedback/Rules** block (23 standing rules)
was kept **verbatim** — "when in doubt, keep it" applies most to the damage-preventing rules. The savings
came from **structural cuts**, not sentence-trimming: the obsolete compaction-deferral narrative in the
header, and the analysis lines' prose (which duplicates their topic files) compressed to pointers.

### Removals — itemised, each with its destination (all already-linked topic files)

| Removed from the index | Destination (already contains it) |
|---|---|
| Header: the "COMPACTION DEFERRED to after Monday" + snapshot-precondition narrative | obsolete (compaction now done); the reasoning is in `monday_preboot_readiness` / `decision_readiness_triage` reports |
| Board line: ⭐ E4/W10 "N=0 ~Rs300", "§A verified 17.1 KB compaction LOSSLESS", throttle #10 counter-case, "branch 81 behind base / 5 files conflict-free" | [[e4-w10-outcome-impact-19jul]], [[decision-packages-19jul]], [[throttle-selection-record-correction-19jul]], `docs/decisions/RUNBOOK_e4_w10_deploy.md` |
| Monday line: "pushed 1462984", crontab-clean-post-push, "3 artifacts re-fingerprinted byte-identical", "INTERRUPTED mid-trim → resumed LOSSLESS" | [[monday-preboot-readiness-19jul]] |
| forward-shadow line: "2 of 3 computable-now proved otherwise ⇒ PerfAllocator UNVERIFIED" (now superseded — #06 HELD) | [[forward-shadow-capacity-d4-feasibility-19jul]], [[candle-retention-perfallocator-feasibility-19jul]] |
| candle line: full masking-sweep clause + candle-retention detail (merged into the #06 pointer) | [[candle-retention-perfallocator-feasibility-19jul]], [[masking-premise-sweep-19jul]] |
| baseline line: sole-writer / table-diff / PC-stub detail | [[artifact-baseline-reconciliation-19jul]] |
| throttle line: 132/139 min_gap, arrival-density confound, Rs990/249/07-08 sweep | [[throttle-selection-record-correction-19jul]] |
| census line: 134,342 [W9] drops, 3 inherited claims wrong, B1 price-bias | [[signal-mortality-census-19jul]] |
| Q9 block: per-batch narrative + "OPEN FOR RAMA" detail (kept the 8 batch links + lessons) | the 8 q9 batch topic files + [[consecutive-losses-gate-wired-19jul]] |
| BUCKET / CAREFUL-LOOP fallout: merged the 3 fallout lines into one, compressed | the respective topic files (all links retained) |

**Nothing was deleted.** Every item above is a *duplicate* of content that remains in the linked topic
file — which is exactly why the compaction is lossless by construction (§C), not by hope.

---

## C. Verification — item by item, against the `_205049` snapshot

- **`[[link]]` slugs: 53 before → 53 real after. DROPPED = 0.** (An automated diff of unique slugs
  returns the empty set; the only textual delta was an illustrative literal in the header, since removed.)
- **`(file.md)` targets: 49 before → 49 after. DROPPED = 0. MISSING = 0** (all 49 resolve to a file in
  the palace).
- **Orphans: 0 real.** The one flagged slug, `verify-check-the-rc-not-the-output`, resolves via its
  frontmatter `name:` to `feedback_verify_rc_not_output.md` (unchanged from the original — not introduced
  by this compaction).
- **Every §B3 mandatory item survives, by line number:**

| Item | line | Item | line |
|---|---|---|---|
| DECISIONS BOARD (10 items) + `00_INDEX` pointer | 6 | RULE: never `scripts/*.py --db copy` | 12 |
| MONDAY watch + OOS day-4 `sim_R` check | 7 | RULE: read a threshold from code | 32 |
| Artifact baseline (VM) | 8 | RULE: CAPITAL VOCABULARY | 39 |
| RULE: within-batch ≠ cross-batch | 8 | RULE: regression must not cross midnight | 40 |
| RULE: never run the forward-shadow recorder | 10 | Q10 Part B / Kite token | 18 |
| | | Security actions (rotate Telegram token …) | 77 |

  (All 10 board items — E4/W10 · D1 · D2 · D3 · D4 · PerfAllocator · Regime · Freeze · Prune · Throttle —
  are enumerated in full on line 6; the individual analyses keep their own pointer lines 9–13.)
- **Coherence:** first line `# Memory Index (ACTIVE)`, last line the RAMA-ACTIONS block; 70 well-formed
  lines; no truncation, no half-removed block.
- **Final size 18,915 B (18.47 KB); margin to the 24.4 KB read limit = 5.93 KB (6,071 bytes).**

---

## D. Why it grows, and the convention that stops it

**Growth rate:** 17.1 KB (the prior 18/19-Jul compaction) → 24.03 KB now, over ~9 of the 19-Jul batches
= **~0.77 KB/batch**; the single 20:13→20:50 step (the baseline-recon batch alone) was **+1.08 KB**. So a
batch has been adding **~0.8–1.1 KB** to the index.

**What drives it:** not more open items — the board has been stable at 10. It is **each batch appending a
dense multi-clause paragraph** (findings + evidence + caveats) to its pointer line, when that detail
already exists in the batch's topic file and report. The index was accumulating *duplicated* narrative.

**The convention (D3), now recorded in the MEMORY.md header):**
> A batch adds **ONE pointer line** to the index — title + `[[link]]` + a single hook — and puts all
> detail in its topic file / `docs/audit/` report. If the pointer needs a second clause, it belongs in
> the topic file, not the index.

At the old ~0.9 KB/batch, the 5.93 KB of headroom buys only **~6–7 batches** — *a few*. **That means the
convention matters more than the compaction did:** a disciplined one-line pointer is ~0.3–0.4 KB, which
stretches the same headroom to **~15–18 batches**. The compaction bought breathing room; the convention
is what keeps the index off the read limit.

---

## Deploy note
Memory palace is PC-side, outside the repo — the compaction itself is not a git change. This report is
the only repo artifact (docs-only). All three VM artifacts re-fingerprinted and reported for what they
are (a heartbeat cron may have advanced the live DB since 20:35 — benign per the within-batch-≠-cross-
batch rule). Nothing here touches anything that boots Monday.
