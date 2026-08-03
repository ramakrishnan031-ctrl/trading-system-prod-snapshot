# F-BACKLOG — ops dashboard future roadmap

**LOCKED — ROADMAP ONLY.** These are *not* scheduled work and carry no commitment. Nothing here
is built until it is separately raised, scoped and ruled.

> ## PROVENANCE — read this before editing
> **Migrated verbatim 04-Aug-2026** from `mempalace.yaml:215-224`, key
> `future_backlog_LOCKED_ROADMAP_ONLY`, as **step 1 of D5 / R13 (retire `mempalace.yaml`)**.
>
> ⭐ **The migration is why the retirement is safe.** `mempalace.yaml` was **untracked and
> git-ignored** (`.gitignore:82`), and repo-wide sweep found **no tracked copy of F1–F9** — it
> held the only one, while two tracked files directed future work at it. Retiring the file
> without moving this content first would have been a **deletion**, not a retirement.
>
> ⚠️ **F1–F9 are carried WORD-FOR-WORD, including their original phrasing and shorthand.** A
> migration that paraphrases is a rewrite. ⛔ Do not "tidy" them; if an item is unclear, that is
> a fact about the original and should be resolved by a decision, not by an edit here.
>
> ⛔ **Anti-duplication was checked before this file was created** (§G3 sibling): no existing
> roadmap/backlog home exists under `ops_dashboard/docs/` or `docs/gui_project/`. The one
> candidate — `G5_REDESIGN_PHASE_B.md` **§SECTION K, "PHASE-C IMPLEMENTATION BACKLOG"** — was
> **rejected on purpose**: it is an *ordered list of buildable units for Phase C*, a different
> lifecycle from a locked post-soak roadmap, and its `1./2./3.` numbering cannot host the
> **F10+** scheme the soak documents reference.

## The backlog

| # | item |
|---|---|
| **F1** | Trading-day replay timeline (signal->close, per-trade) |
| **F2** | 'Explain why' one-click reason chain on any rejection/cancel/dupe |
| **F3** | Config snapshot history + compare (read-only) |
| **F4** | Strategy lifecycle over Today/Week/Month periods |
| **F5** | Reports download revisit (post-soak) |
| **F6** | Journal viewer (G4) |
| **F7** | CSV exports (post-Q3 pattern) |
| **F8** | Multi-day trend charts (G4) |
| **F9** | Unrealized/LTP (G4, after B-1) |

## Adding to it

**This file is the F-backlog's home.** Soak findings append here with **F10+** numbering —
`SOAK_EVIDENCE_TEMPLATE.md` §(c) and `G5e_DEPLOYMENT_PLAN.md` §C.3 both point here.

⛔ **Appending is not scheduling.** An entry here records *"the dashboard could not show this"*;
it does not create an obligation, a priority or a register row.
