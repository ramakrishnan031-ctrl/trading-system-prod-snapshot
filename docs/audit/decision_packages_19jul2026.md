# Decision Packages + Memory-Compaction Verification — 19-Jul-2026 (DOCS-ONLY)

**Scope:** (A) verify the weekend's MEMORY.md compaction dropped nothing load-bearing; (B) assemble one decision file per open choice in `docs/decisions/`, with the corrected evidence; (C) add the entry-throttle finding to the board as a decision, carrying its counter-case.

**Deploy state:** PC == origin == VM bare == `3dda9f7`; code tag `deploy-19jul-consecutive-losses` → `d271525` (delta markdown-only); schema v44. System DOWN; market closed; book flat; kill-switch INACTIVE. **Nothing executable changed.** Queries, where needed, used the `mode=ro` preserved snapshot; the live DB is proven untouched (end of report).

**No recommendation appears anywhere in this report or the decision files.** Every decision is Rama's; the discipline here is to present the choice, not to make it.

---

## A. Memory-compaction verification — nothing load-bearing was lost

MEMORY.md was compacted from ~19.9KB to 17.1KB across eight passes under a byte target. The concern that eight rounds of cutting the file that holds the open decisions, the day before a live boot, is exactly where something disappears silently — is correct, so this is an item-by-item audit, not a summary.

**Method:** every compaction edit's before/after is in this session's history, so removals are enumerated directly and each survivor was grep-verified to still exist elsewhere.

### Every removal, classified

| # | Entry | What was cut | Class | Survives at |
|---|---|---|---|---|
| 1 | Census pointer | "cross-verified to the unit", "no per-signal reason", "not a blind spot", full boundary-correction prose, "same killer", "25,960", "cheaply estimable" | (i) | `signal_mortality_census_19jul` topic + census report + census-fallout line |
| 2 | Daily-report fix | deploy/module prose | (i) | `daily_report_classification_fix_18jul` topic |
| 3 | Q9 entry | "SEEDED worktree", verbose LESSONS, **PRE-MONDAY VM CHECK (94/94 imports…)**, "3 keys decorative", "(was 16; 2 NTP)" | (i)/(ii) | `q9_coverage_matrix_final` §C (the 94/94 check); q9-batch3 topic; SYSTEM_MAP Q9 banner |
| 4 | Bucket board | bucket-A label, C's item list, "writable", G's "liveness alarm / registry officer 16:22 / TRIVIAL restore / alert-watcher soak / F1 enforce", I's "live-test cert" | (i) | careful-loop queue (S5-tail, P3-s14), regime line, registry topic, `ct_guard_invariant_18jul` + deploy reports (soak), `batch_classification_16jul` (live-test cert) |
| 5 | Feedback rules 37–41 | the "why/how" prose; one `[[consecutive-losses…]]` cross-ref | (i) | each rule's own topic file; link target present at 2 other lines |
| 6 | Throttle entry | score-gap detail, ~60s, baseline hash, 3-mechanism parenthetical | (i) | `throttle_selection_record_correction_19jul` topic + report |
| 7 | Fallout lines | struck-through DONE items (~~layer 14~~, ~~ntp~~, ~~daily_report:464~~), "disclosed in xlsx" | (ii) | completed items |
| 8 | AB-910 pointer | "prod 2FA seed in PC tree · backups+DB on ONE disk" | (i) | `ab910_ops_security_audit_16jul` topic + SYSTEM_MAP AB-910 banner + RAMA-ACTIONS |
| 9 | Ops-archive / ledger / security-watcher / Regime / Deferred / next-loop / GUI / VM-arch | assorted prose; one `[[q9-live-seed…]]` cross-ref | (i) | each entry's topic file; link target present at line 63 |

**No removal was category (iii) (existed only in MEMORY.md).** The two candidates that looked category-(iii) — **"alert-watcher soak"** and **"live-test cert"** — both survive in topic files/reports (`ct_guard_invariant_18jul.md:85`, `deploy_batch1_done_16jul.md:57`, the deploy reports; and `batch_classification_16jul2026.md:175`). The two trimmed `[[links]]` both still resolve (targets present elsewhere in MEMORY.md). **Nothing was restored because nothing needed restoring.**

### Board-item survival — confirmed explicitly

Every open board item is present in the current MEMORY.md, with its line:

| Board item | Present at |
|---|---|
| E4/W10 | Core (ledger line + bucket B) — `e4-w10-pnl-contract`@`ad34ee4`, sign-off-gated |
| D1 · D2 · D3 · D4 | bucket board ("D1–D4"), OPEN-FOR-RAMA (D1), RAMA-ACTIONS ("D1–D4 on the decision sheet") |
| PerformanceAllocator | OPEN-FOR-RAMA line ("NEVER wired ⇒ perf_weight≡1.0…") |
| Regime strategic choice | Regime+Q10 line ("do NOT flip regime.enabled mid-soak") |
| FREEZE `min_pass_score` | Regime+Q10 line ("FREEZE min_pass_score while measuring") |
| Q10 Part B (Kite token) | Regime+Q10 line ("Part B awaits Rama's Kite token") |
| Security actions | RAMA-ACTIONS line (Telegram, 2FA, backup, rpcbind, SSH, require_hmac, pre-receive) |
| Prune-retention | was never a discrete pre-compaction board *line* — it was recorded as the census *finding*; it is now a decision file (§B) |

**Conclusion: the compaction was lossless at the content level.** The one thing the compaction did structurally change — collapsing the "restated so nothing is lost" bucket board — is safe precisely because the board was, by its own description, a duplicate index of items that live in their own entries.

---

## B. The decision packages

`docs/decisions/` now holds one file per open decision, each stating: **the choice** (as options), **what is known** (current corrected evidence, with citations, noting any value corrected this weekend), **what is unknown** (and whether it is knowable from data, only by running the system, or not at all), **what changes if the direction is wrong** (in rupees where the record supports it), and **what would settle it** (with cost). Index: `docs/decisions/00_INDEX.md`.

| File | Decision | One line on the choice | What gates it |
|---|---|---|---|
| 01 | E4/W10 `pnl_delta` | adopt the NET contract vs keep the double-subtracting reader | posture sign-off; deploy needs a manual flatten first |
| 02 | D1 concentration cap | leave the ~Rs 990/pos ceiling vs raise it | coupled to D3 — a lever on a book of unknown sign |
| 03 | D2 strategy mix | keep / retire negative-edge strategies / rebalance | positive-control baseline + Q10 (token) |
| 04 | D3 `min_pass_score` | leave 60 vs lower it — **backtest and real-book point opposite ways, equal weight** | tension with FREEZE |
| 05 | D4 exits | keep naked static / enable config BE / build BE-after-0.5R | out-of-sample test; routes to M-S4 |
| 06 | PerformanceAllocator | wire it vs leave `perf_weight ≡ 1.0` | ~~likely masked by the concentration cap~~ **REFUTED 19-Jul** — post-cap multiplier, changes qty **233/298**; gate = merit (D2/D3) + leverage (`masking_premise_sweep_19jul2026.md`) |
| 07 | Regime | leave off / enable / build Phase 1 first | Q10 (~2.2 months + token); do-not-flip-mid-soak |
| 08 | Freeze `min_pass_score` | freeze during measurement vs allow changes | downstream of Regime + D3 |
| 09 | Prune retention | leave 90d status-selective / retain rejections / change window | Rama's intent on rejection-analysis need |
| 10 | Entry-throttle admission | arrival-order vs change params vs ranked batching | ranking value coupled to D3 (see §C) |

Two corrected values are carried prominently into the files that use them, with the superseded value noted:
- **D1** now carries the corrected >Rs 990 direction — **LONG 10.00% vs SHORT 10.72% by rate** (supersedes "LONGs ~4.5× harder"); the count skew is the 10.2× long-volume skew.
- **D2** carries the corrected book composition — **91% LONG** (supersedes the "~58% short" premise).

Q10 Part B and the security items are **operator actions, not decisions** — listed briefly in `docs/decisions/ACTIONS_not_decisions.md`, not padded into decision files.

---

## C. The entry-throttle decision — and its counter-case

The finding (`throttle_selection_and_record_correction_19jul2026.md` §B) is now decision file `10_entry_throttle_admission.md`: a global 20s `min_gap` admits first-come-first-served after risk approval; 77 of 217 approved arrive in the 10:00–10:02 burst and 14.3% clear; 132/139 throttles are that one gate; throttling is 100% market-open; selection is not best-first (score 60.090 vs 59.878, no gradient).

**The counter-case is carried at equal weight in the file, because it is strong.** Ranked admission is only worth building if the ranking has predictive power, and this project's own evidence says it does not: M-S4 full-range rho = **+0.003** (OUTCOME C, no ranking power at any level); the band inversion shows the **60–65 band — the only band the throttle ever sees — is the worst-performing** (31% win, −0.27R) at 4–5σ. If the scorer cannot rank, first-come-first-served discards no edge, and ranking by the current score would rank noise (and, given the inversion, could prefer the worst signals). A package that presented the finding without this would read as an argument for building the batching window; it is not.

**What stands regardless of ranking power (the visibility point):** ~23 approved signals per day are discarded by the throttle, 87% of orders land in the first hour, and the throttle's gate category lives only in the free-text `rejection_reason` — so the discard is invisible in every report. That is independent of whether ranking is worth adding.

### C4 — the free-text-classification pattern: four instances

The throttle's free-text-only gate category is the **fourth** instance of a decision recorded in prose where a structured column should carry it — the shape that produced the 3,098-rejection misclassification. Named in full:

1. **Sizing rejection — concentration vs capital (the origin, FIXED 18-Jul).** `reports/daily_report.py:464` branched on the free-text `rejection_reason` (`"CAPITAL" in …`, which embeds `capital_qty=`) where the structured `status = REJECTED_SIZING_CONCENTRATION` existed → **3,098** CONCENTRATION rejections mislabelled CAPITAL; `:549` grouped by the same string → 7,655 rejections fragmented into 190 near-unique lines. Fixed via the single classifier `reports/signal_status.py` (`daily-report-classification-fix-18jul`).
2. **`REJECTED_KILL_SWITCH` stage (census; latent, 0 rows).** Emitted identically by the pre-gate (`signal_processor.py:685`) and risk-engine check 1 (`risk_engine.py:405`); no structured column carries which stage rejected, so the stage is recoverable only from prose/logs.
3. **`is_sizing_rejection()` / `REJECTED_SIZING_VALID` (census; latent, 0 rows).** The status-prefix classifier in `reports/signal_status.py` would treat `REJECTED_SIZING_VALID` — a risk-engine check-2 outcome — as a *sizing* rejection: the prefix picks the wrong stage, the origin defect one layer over.
4. **Entry-throttle gate category (this weekend).** `min_gap` / `burst` / `per_symbol` lives only in the free-text `rejection_reason`; the structured `status` is the undifferentiated `REJECTED_ENTRY_THROTTLED`, so the discard reason is unreportable structurally.

Closely related but distinct (recorded, not counted among the four): **W9** — the 134,342 webhook-layer drops have their reason computed transiently and **never persisted per-signal** at all (no free text, no column). That is "not recorded," a sibling of "recorded in the wrong place."

**All four are reported, not repaired.** The pattern is now a class, not an incident.

---

## PROOF OF READ-ONLY

- Any fresh query used the preserved snapshot (`file:/home/ubuntu/preserved/signal_census_19jul2026/trading_system_snapshot_20260719.db?mode=ro`); no `scripts/*.py --db <copy>` was run (per the rule learned last batch).
- **Live DB before this session:** sha256 `6df0c09a…`, mtime `2026-07-19 11:14:29`, size `89,968,640`.
- **After (see the commit log): identical.** *(Recorded at completion.)*

*Bookkeeping only — no code, config, schema or flag changed. Every decision in `docs/decisions/` is Rama's; nothing here recommends an option.*
