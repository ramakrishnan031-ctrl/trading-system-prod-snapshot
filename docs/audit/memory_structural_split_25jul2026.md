# MEMORY.md — STRUCTURAL SPLIT (25-Jul-2026)

**Authorised** (VS Code handoff §A, 25-Jul ~02:55 IST: *"Rama approved. The read limit bit tonight; verbatim relocation alone can no longer hold it."*). **Memory-palace only** — no repo, config, service or trading-path change beyond this report. Fully reversible (§7).

This is the split the 23-Jul pass reserved and the 24-Jul and 25-Jul passes both declined to take on their own authority (*"that stays Rama's"* / *"the split is now the live remedy for a recurring trigger, not a someday item"*).

> ⚠️ **Scope note.** P4-6 (`docs/SYSTEM_MAP.md`) and P4-7 (`PATHS.md`) are the *repo-document* splits and both files carry an explicit **"do not split silently"** banner. **Neither was touched.** This pass splits the **memory index only**, which is what §A authorised.

---

## 1. Why a split and not another compaction

The 25-Jul compaction stopped 252 B above its target and said why: everything left is a live decision, a DO-NOT rule, an operational invariant, or the protected CAREFUL-LOOP section. **Prose compaction is exhausted; archive relocation is exhausted** (nothing left is closed). The remaining lever is structural — give live-but-cold content a destination other than the hot index.

## 2. The shape (four files, one read-trigger each)

| file | tier | read trigger | auto-loaded |
|---|---|---|:--:|
| `MEMORY.md` | **HOT** | every session | **yes** |
| `MEMORY_BOARD.md` | **OPEN WORK** | before starting any batch | no |
| `MEMORY_REFERENCE.md` | **REFERENCE** | when you touch that area | no |
| `MEMORY_ARCHIVE_2026H1.md` | **CLOSED** | tracing history | no (**unchanged this pass**) |

### The placement rule (now written into the index header, so it governs future batches)

> A line carrying a **DO-NOT / NEVER / imperative**, or an invariant that prevents a **wrong capital/signal-path action** or a **wrong production conclusion** → **HOT**.
> Open work, status, a decision awaiting Rama → **BOARD**. A stable fact, an environment how-to, tooling → **REFERENCE**. Closed/superseded → **ARCHIVE**.

**The governing constraint was: protection must not degrade.** Only `MEMORY.md` is auto-loaded, so moving a rule out of it stops that rule being enforced by default. Every line carrying a DO-NOT therefore stayed HOT **in full, verbatim** — that is why the hot file is still 13.7 KB and not 6 KB. The split buys headroom **and a destination for future growth**; it deliberately does not buy the last kilobyte.

## 3. What moved (25 of 73 content lines, all VERBATIM — nothing summarised)

**→ `MEMORY_BOARD.md` (12 lines, open work with no DO-NOT clause):** DECISIONS BOARD · C6 SCOPED 25-Jul · WAVE-7 TRIAGED · FIXATION BATCH 24-Jul · PRUNE #09 DESIGN · 2FA seed→VM-only RUNBOOK · DONE/DEPLOYED→archive (incl. Rama's `cleanup.py --live` question) · AB-910 audit · BUCKET BOARD · the ≤17-Jul Operations archive pointer · the two `★ Deferred (OPEN)` lines.

**→ `MEMORY_REFERENCE.md` (13 lines, stable facts / env how-tos):** SATS tooling · Token workflow · VM Architecture LOCKED · Master project state · Naive IST timestamps · Reply style (Web Claude only) · SSH key passwordless · Use VM terminal for curl · Transfer VM scripts via base64 · in_flight is in-memory only · Log rotation · Trade export filters · the whole `Hygiene Queue` section (PC test-env).

**Stayed in `MEMORY.md`: 48 content lines** (73 − 25).

**Stayed HOT, deliberately, though they look moveable:**

| line | why it did not move |
|---|---|
| Capital operational note · Order lifecycle note · CO bracket note | capital/order-path traps — absence produces a **wrong conclusion**, not just lost time |
| GUI redesign workflow | carries an imperative (*FOLLOW VERBATIM*); consistency beat saving 291 B |
| Sequential agents ONLY · Paper/Live parity · READ SYSTEM_MAP first · Foundation Rules · Memory hygiene | behavioural rules; unloaded = unenforced |
| Webhook flow diagnosis | prevents a false-outage reading of the designed 403 gate |
| RESEARCH cluster | carries **NEVER run `forward_shadow_record.py` manually** |
| EXITS THREAD CLOSED · UNPUSHED LEDGER · Service self-exits · SHUTDOWN=CONFIG | each is a *don't-reopen* / *read-before* / *don't-misread* rule |
| **the entire CAREFUL-LOOP queue** and **RAMA-ACTIONS** | protected sections — untouched, in place, verbatim (§A2) |

## 4. Result

| | before | after |
|---|---:|---:|
| `MEMORY.md` (auto-loaded) | 17,796 B / 88 lines | **13,721 B / 70 lines** |
| shed from the hot path | | **−4,075 B (−22.9%)** |
| `MEMORY_BOARD.md` | — | 3,631 B / 20 lines |
| `MEMORY_REFERENCE.md` | — | 2,380 B / 23 lines |
| `MEMORY_ARCHIVE_2026H1.md` | 57,392 B | 57,392 B (**untouched**) |
| union of the three index files | 17,796 B | 19,732 B (+1,936 B of new navigation headers) |

The union grew by 1,936 B — that is the cost of the routing headers and it is paid in the *cold* files and the small hot header, not in content. **No memory content was deleted, shortened or reworded.**

## 5. Verification — RUN, not asserted (all 8 PASS)

Script: `scratchpad/verify_split.py`, comparing the pre-split snapshot against the **union** of the three post-split files.

| # | check | result |
|---|---|---|
| 1 | 73 content lines, each present **exactly 1×** in the union | ✅ |
| 1b | union contains **exactly** the same 73 content lines (none added/altered) | ✅ |
| 2 | wiki-links `[[…]]` — 34 unique before, all reachable | ✅ 0 lost |
| 3 | `.md` refs — 66 unique before, all reachable | ✅ 0 lost |
| 4 | the 14 tracked dangerous markers, count in union ≥ count before | ✅ |
| 5 | byte budget (≤300 B, ≤450 B for 🔝) on **all three** files, **chars and bytes** | ✅ 0 violations |
| 6 | orphan links — every `](*.md)` target exists on disk | ✅ 0 orphans |
| 7 | pure CRLF + trailing CRLF + no double-blank runs, all three files | ✅ |

**Marker locations after the split** (13 of 14 stayed hot; the 14th moved with its own line):
`DO NOT ARM` · `NEVER run` · ``never `scripts`` · `KEEP FALSE` · `Do NOT 'fix'` · `NO absolute` · `VIRTUAL GENERATED` · `NOT as-is` · ``forfeits `RESET_PNL` `` → **`MEMORY.md`**. `ONE-WAY` (2FA re-enrol) → **`MEMORY_BOARD.md`**, travelling verbatim with the 2FA runbook line it qualifies.
Four of the 23-Jul list (`halt STAYS`, `0 recur = LATENT`, `SILENT DROPS = 0`, ``excl. `RESET_PNL` ``) were **already absent before this pass** — relocated to the archive by the 24-Jul pass. before=0, union=0. **Not a loss of this split**; recorded so the 14-marker list is not read as 14-still-in-the-index.

### Two defects this pass caught in its own work (both fixed before landing)

1. **The new header lines broke the index's own byte budget** (515 B / 422 B / 373 B / 366 B against a 300 B limit). The 25-Jul precedent is explicit — *"cut rather than trade one rule for another"* — so the headers were split into ≤300 B physical lines instead of exempting them. Markdown still renders each block as one paragraph.
2. **The new headers were being counted as memory entries** (they began with `> `). They now carry no `- `/`> ` prefix, so no future reader — human or machine — mistakes navigation for content, and the budget/lossless checks classify them correctly.

Both were caught by running the checks, not by reading the output — check 5 and check 1b **could have been red, and were**.

## 6. Known pointer consequence (recorded, not a defect)

`docs/audit/decision_packages_19jul2026.md` and `docs/decisions/ACTIONS_not_decisions.md` refer to *"the DECISIONS BOARD"*, *"the bucket board"* and *"RAMA-ACTIONS"* as living in `MEMORY.md`.

- **RAMA-ACTIONS** and the **CAREFUL-LOOP** queue are still in `MEMORY.md` — those references stay exact.
- **DECISIONS BOARD** and **BUCKET BOARD** now live in `MEMORY_BOARD.md`, which is named and linked in the first screen of `MEMORY.md`. The references remain **reachable in one hop** and were left unedited (editing prose in unrelated reports to chase a move is how glosses get manufactured).

`grep` over `docs/` and `PATHS.md` found **no** reference to the section names `Core References`, `Feedback / Rules` or `Hygiene Queue`, so renaming `## Core References` → `## Core — hazards, invariants & DO-NOTs` breaks nothing.

## 7. Reversibility

- **Restore point:** `memory_snapshots/memory_snapshot_2026-07-25_presplit/` — `MEMORY.md` (17,796 B) + `MEMORY_ARCHIVE_2026H1.md` (57,392 B), both **md5-verified identical** to the originals before any edit.
- **Undo** = copy the snapshot's `MEMORY.md` back and delete the two new files. Nothing else changed.
- The split script (`scratchpad/split_memory.py`) reads **from the snapshot**, not from the live file, so it is idempotent and re-runnable.
- One pre-existing line was rewritten rather than moved: the `**Structure:**` preamble. Its **BYTE BUDGET clause is preserved verbatim** except the `awk` file list, extended from `MEMORY.md` to all three index files (with `FILENAME` added to the print) so the rule is true of what it names.

## 8. What this buys

Not primarily the 4 KB. It buys the **placement rule**: reference-shaped and board-shaped content now has a home that is not the auto-loaded file. The index was growing ~0.9 KB/batch with genuinely-active content and had hit a lossy-compaction trigger three passes running (19, 20, 23, 24, 25-Jul). After this pass, only **hazards and rules** land in the hot file — and those grow far more slowly than status does.

Prior passes: `memory_compaction_23jul2026.md` · `memory_compaction_24jul2026.md` · `memory_compaction_25jul2026.md`.
