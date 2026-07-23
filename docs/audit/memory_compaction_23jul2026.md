# MEMORY.md Compaction — 23-Jul-2026

**Scope:** the Claude memory-palace index (`~/.claude/projects/D--Projects-trading-system/memory/MEMORY.md`) and its archive. **Memory + docs only** — no code, config, service, or trading-path change. Off-market, `trading-system.service` inactive (16:00 self-exit), nothing time-gated. Predecessor: `docs/audit/memory_compaction_20jul2026.md` (and `..._19jul2026.md`); this pass supersedes the 20-Jul one as the index's `Compacted:` pointer.

**Fixation (per the instruction):** LOSSLESS. Relocate, never delete. The paramount criterion is that no memory line survives with its load-bearing qualifier dropped — a confident, wrong line is worse than an absent one. The completeness claim below rests on a **mechanical grep check**, not on impression (the 19-Jul masking sweep's error — asserting completeness it had not grepped — is the precedent this must not repeat).

---

## A. Pre-work cleanup

- `label-layer-batch-23jul` deleted via `git branch -d` (refuses if unmerged — used as the merge check; it deleted cleanly ⇒ confirmed merged). `git branch -D` deliberately NOT used.
- Tree clean; `PC main == origin (VM bare) == f234a85`.

## B. What the pass did

### B1 Snapshot (restore point)
`memory_snapshots/MEMORY_2026-07-23_pre_compaction.md` — byte-identical copy of the pre-edit index (20,733 B), outside the compaction's reach. Per the hard constraint, if the pass could not finish cleanly the snapshot would be restored and the pass abandoned.

### B2 Inventory (the before-state, so "nothing lost" can be *grepped*)
| metric | before |
|---|---|
| bytes / lines | 20,733 B / 98 lines |
| unique wiki-links `[[…]]` | 48 |
| unique `.md` refs (link + backtick + bare) | 68 |
| tracked dangerous markers | 14 (each count 1) |

The 14 tracked markers (the load-bearing negations that must survive verbatim): `DO NOT ARM`, `NEVER run` (forward_shadow_record), ``never `scripts``, `KEEP FALSE` (require_hmac), `Do NOT 'fix'` (security-watcher), `NO absolute` (daily-loss), `VIRTUAL GENERATED`, `ONE-WAY` (2FA re-enrol), `NOT as-is` (pre-receive), `halt STAYS` (HARD_KILL D7), `0 recur = LATENT` (orphan), `SILENT DROPS = 0`, ``excl. `RESET_PNL` ``, ``forfeits `RESET_PNL` ``.

### B3–B5 Classification and actions
Three tiers were applied. The overriding finding: **this index is already post-compacted (20-Jul) and is largely load-bearing** — dangerous markers, open decisions (the DECISIONS BOARD, D3/D4/#06, PRUNE #09), Rama-actions, the never-sweep CAREFUL-LOOP queue, and the Feedback/Rules. There is little pure-redundant prose to cut.

**NEVER COMPACT (left verbatim):** all 14 dangerous markers; the DECISIONS BOARD and per-decision lines; RAMA-ACTIONS; the entire CAREFUL-LOOP queue; the Feedback/Rules section; stable reference notes (VM arch, DB schema, capital/order/CO notes, token workflow).

**COMPRESS IN PLACE — prose only, every link/ref/qualifier kept (5 lines):** WAVE-7 (kept `arm IFF delivery` / `inert` markers; dropped M-O8/CO 0/663 detail), MONDAY 20-Jul (kept `excl. RESET_PNL` + worst-intraday; dropped "6.18% of −296", "1 new date"), S4 401 (kept `:703 REFUSED stands` + `No 4th instance`; dropped the mirror/live-PASS detail), E4+W10 deployed (kept impact/N=0; dropped "+19.61 vs +18.29 net"), ORPHAN §3 (kept `NONE since`; dropped "2637 rows/8 sym" and the "'20'=failed-TG" parenthetical). Two accuracy-only edits: the `Compacted:` pointer (→ this report) and the UNPUSHED-ledger line (→ "label-layer batch deployed 23-Jul `f234a85`").

**RELOCATE (verbatim move to `MEMORY_ARCHIVE_2026H1.md`, 3 entries):** PHASE-2 P&L carryover (PROVEN, off-unproven-list), DAILY-REPORT classification fix (DEPLOYED 18-Jul), LOCKED-CONTRACT staleness sweep (DONE 22-Jul). Each carries **none** of the 14 markers and is fully closed. A *verbatim move* is the lowest-risk high-yield lever — it cannot manufacture a gloss, because nothing is summarised; the full original detail (incl. `daily_pnl=-12.57`, `OP-NS4/5/BL19d/LM3`, `3,098→0`) now lives in the archive under a dated "18–22-Jul closed items (relocated 23-Jul)" section, reachable via the archive link at the top of the active index.

### B-defect A newline-merge bug was hit **and repaired** (recorded for honesty)
The three archive removals used a leading-`\n` `old_string` (`"\n- <line>"` → `""`). This form merged each removed line's neighbours (dropped one extra newline apiece): S4-401+PERSISTED-KILL, Q9+DONE/DEPLOYED, and the Recent-Ops/CAREFUL-LOOP section break. **No content was lost** — 98 − 3 removed − 3 merges = 92 lines, and two merged lines tripped the per-line byte-budget check (659 B, 604 B), which is how it was caught. Fixed by re-inserting the 3 newlines; line count returned to the expected 95. **Lesson:** delete a line by matching `"<line>\n"` (content + trailing newline), and always re-run the line-count + budget check after a deletion.

## C. Lossless verification (mechanical — grepped, not asserted)

Extraction regexes were corrected first (the initial pass missed underscore wiki-links and backtick file-refs): wiki-links `\[\[[A-Za-z0-9_-]+\]\]`, refs `[A-Za-z0-9_./-]+\.md`.

| check | result |
|---|---|
| wiki-links: every before-link reachable in **active ∪ archive** | ✅ 47 in active + `[[phase2-carryover-proven-21jul]]` in archive = all 48 |
| `.md` refs: every before-ref reachable in **active ∪ archive** | ✅ all, **except** `docs/audit/memory_compaction_20jul2026.md` (see note) |
| 14 dangerous markers still in active | ✅ each count 1 |
| per-line byte budget (`≤300`, `≤450` for 🔝) | ✅ 0 violations |
| max consecutive blank lines | 1 (no formatting artifact) |
| line count | 95 = 98 − 3 relocated |

**Note on the one before-only ref:** `docs/audit/memory_compaction_20jul2026.md` was the index's prior `Compacted:` pointer; it is superseded by *this* report and is **referenced here** (top of document) and physically present in `docs/audit/` — so it remains reachable. This is the single intentional reference change of the pass.

## D. Result and the real finding

| | before | after | Δ |
|---|---|---|---|
| bytes | 20,733 | 19,648 | **−1,085 (−5.2%)** |
| lines | 98 | 95 | −3 |
| headroom to the 24.4 KB read limit | 3.67 KB | **4.75 KB** | +1.08 KB |

**The finding is as important as the bytes:** a *safe* (qualifier-preserving) prose compaction of this index yields little — the bulk is genuinely-active, load-bearing content, and it was already compacted 20-Jul. The gain here came mostly from the 3 verbatim archive relocations, not prose. **Meaningful further reduction is a structural decision, not a prose squeeze**, and is Rama's call:

1. **Larger archive-relocation pass** — several more closed entries *could* move (e.g. the orphan-forensics cluster, S4-401, AB-910), but each carries a latent-active caveat or a watched-family status, so moving them trades index-at-a-glance visibility for headroom. A deliberate, reviewed batch — not an automated squeeze.
2. **The structural split (P4-6 / P4-7)** already flagged in `PATHS.md` — the sustainable fix, since the index grows ~0.9 KB/batch with genuinely-active content. Every report and memory points into `MEMORY.md`/`SYSTEM_MAP.md` by section, so the split needs an agreed shape first.

Until then the index has ~4.75 KB of headroom (~5 batches). **No pressure** — this pass prioritised losslessness over hitting any byte target, which is the correct trade per the instruction.

## E. Artifacts
- Active index: `MEMORY.md` (19,648 B / 95 lines).
- Archive: `MEMORY_ARCHIVE_2026H1.md` (+ the dated 18–22-Jul relocations).
- Restore point: `memory_snapshots/MEMORY_2026-07-23_pre_compaction.md` (20,733 B).
- This report. Worth a second reader (ChatGPT) — a completeness claim about the memory palace benefits from one.
