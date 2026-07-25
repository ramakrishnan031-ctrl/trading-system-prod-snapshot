# MEMORY.md compaction — 25-Jul-2026 (3rd pass)

Triggered by the index approaching its read limit. Memory-palace only — **no repo or production change** beyond this report. Read-only w.r.t. the trading system.

## Method

Same as the 24-Jul pass, plus the two levers the trigger explicitly allows (*"keep one line per entry, move detail into topic files, and merge or drop stale entries"*):

1. **Verbatim relocation** active → archive for genuinely CLOSED items (a verbatim move cannot manufacture a gloss).
2. **Detail moved into topic files** — an index line becomes a hook + pointer where the topic file already holds the full text.
3. **Merge** of a research cluster into one line, every topic-file link and every protective clause preserved.
4. **Protected and untouched:** the CAREFUL-LOOP section (`never sweep these`) and every entry that carries a live decision, gate or DO-NOT rule.

## What moved (verbatim, 4 items)

Relocated under a new dated archive block *"25-Jul relocation (3rd pass)"*:

- `🌅✅ FRI 24-JUL §A CLEAN · A3 PROVEN · A1-A3 DONE` — all DONE, deployed `bc75406`.
- `✅🔑🔝 S4 /health 401 FAMILY CLOSED — B2′ DEPLOYED 21-Jul aff5256` — closed, sweep done.
- `🏁✅⭐🔝 Q9 COMPLETE 19-Jul — 22/22 WIRED · 16 REACHABLE` — complete.
- The 8 wiki-link stubs under `DONE/DEPLOYED → archive` (they pointed at already-archived content).

## What was merged

Six research entries (D3 · D4 · #06 · #07 · traded-book selection · mortality census) → **one 438 B line**, retaining all six topic-file links, the `⇒ D1` coupling, and the ⚠️ **NEVER run `forward_shadow_record.py` manually** rule inline (an index reader must not have to open a file to learn a DO-NOT).

## ⭐ Two CORRECTNESS fixes the pass surfaced (the real value here)

1. **`Service self-exits 16:00 IST daily` was about to become FALSE.** The 25-Jul deploy (`570b3e8`) made the shutdown configurable at **17:35** from Mon 27-Jul. Line corrected to name the configured key, not a literal. A stale index line asserting the wrong shutdown hour is exactly the kind of thing that misdirects an incident.
2. **The ledger pointer still named `bc75406`** as the latest deploy; corrected to `570b3e8`.

Also fixed: a dangling colon left by the stub relocation, and the header's compaction pointer.

## Result

| | before | after |
|---|---:|---:|
| `MEMORY.md` (on disk, CRLF) | 20,856 B | **17,762 B** |
| shed | | **3,094 B (14.8%)** |
| `MEMORY_ARCHIVE_2026H1.md` | 55,835 B | 57,392 B |

## Verification (RUN, not asserted)

- **Lossless relocation:** each relocated line counted **1× in archive, 0× in active** (in-process string count, not grep — the lines begin with `-` and break `grep` argument parsing).
- **Byte budget** (`awk length > 450 if 🔝 else 300`): **prints nothing — PASS.** The merge initially produced a 1,236 B line, which violated the index's own rule; it was cut to 438 B rather than trading one rule for another.
- **Orphan links:** every `](*.md)` target in `MEMORY.md` resolves to an existing file — **PASS**.
- **Line endings:** CRLF throughout, matching every other memory file (checked against files this pass did not write). Not altered.

## ⚠️ Stopped 252 B above the 17,510 B target — deliberately, and why

Everything remaining is either a live decision (D-series, prune #09, 2FA runbook, watchman), a DO-NOT rule (pre-receive, `--db <copy>`, rr_floor, security-watcher), an operational invariant (migration-on-open, prior-day kill auto-clear, WAL sidecar), or the protected CAREFUL-LOOP section. **Shedding the last 252 B (1.4%) means dropping live content, not compacting it.**

The 24-Jul pass set this precedent explicitly — it *"did NOT chase 17.1 KB"* for the same reason. The real remedy is the **structural split (P4-6 / P4-7)**, which that report records as Rama's call and which this pass again did not take.

📌 **Corrects the 25-Jul register:** `MASTER_PENDING_REVISED_25-Jul-2026.txt` §D lists *"MEMORY.md STRUCTURAL SPLIT — NOT DUE, ~3-4 KB headroom"*. That was measured against the split trigger, not the read limit, and the read limit bit the same night. **The split is now the live remedy for a recurring trigger, not a someday item.**
