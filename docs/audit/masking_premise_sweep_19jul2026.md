# Masking-premise sweep + reachability re-check — 19-Jul-2026

**READ-ONLY + DOCS. No recommendation.** The PerformanceAllocator measurement
(`candle_retention_and_perfallocator_feasibility_19jul2026.md`) refuted a premise the record carried
as settled — *"a size multiplier is masked by the concentration cap that binds 100% of trades."* This
batch (1) sweeps that premise out of every place it still reads as current, leaving the superseded claim
legible; (2) checks whether the refutation moves any **reachability** verdict — the tempting error being
to let a real refutation over-propagate into conclusions it does not touch; and (3) snapshots the memory
palace so the owed MEMORY.md compaction is unblocked. Nothing executable changed. System DOWN; book flat.

**The refutation, restated:** `perf_weight` is applied **after** `raw_qty = min(risk, capital,
concentration)` — a **post-cap multiplier** up to a `2×raw_qty` ceiling (`position_sizer.py:446/503/506`).
So concentration binding raw_qty (`binding_constraint='concentration'` on 298/298) does **not** prevent
it from changing final qty: it changes on **233/298 trades (78%)** over the configured [0.5, 2.0], and
**151/298 (51%)** over a modest [0.8, 1.25]. Decorative only on the 65 `raw_qty=1` trades (integer
floor). Conditional on today's **unlevered** sizing.

---

## A. The sweep — every occurrence, corrected with a dated, legible note

The record was **half-corrected** (the worst state): the triage `#06` row and the 00_INDEX `#06` row had
been updated, but the **coupling sections** — and the decision file's own body — still asserted the
masking. A reader could not tell which was current. Corrected each **without silent rewrite** (the
superseded text is struck through but readable):

| # | Location | What it said | Correction |
|---|---|---|---|
| 1 | `docs/decisions/00_INDEX.md:27` (coupling section) | "#06 ↔ D1: a size multiplier is **masked by the concentration cap** that binds 100% of trades." | struck + dated note: post-cap multiplier, 233/298; gate = merit (D2/D3) + leverage, not the cap |
| 2 | `docs/decisions/06_performance_allocator.md` **body** (What-is-known) | "A multiplier applied *before* a cap … is **masked by that cap** … the Q9 batch-4 finding … applies here." | struck + **misattribution flagged** (batch-4 reasoned it correctly; see §B) |
| 3 | `06_performance_allocator.md` body (What-is-unknown) | "whether a `perf_weight>1` … is **dominated by the concentration ceiling** in every case." | marked **✅ ANSWERED**: 233/298; not dominated |
| 4 | `06_performance_allocator.md` body (What-changes-if-wrong) | "**If B (wire) and it is masked by the concentration cap:** no behavioural change." | struck + dated note: wiring WOULD change qty on 233/298 |
| 5 | `docs/audit/decision_readiness_triage_19jul2026.md:26` (coupling paragraph) | "· **#06↔D1**." | `#06↔D1` → **`#06↔merit(D2/D3)+leverage`**, masking refuted |
| 6 | `docs/audit/decision_packages_19jul2026.md:63` (table cell) | "likely **masked by the concentration cap**" | struck + dated note: post-cap multiplier, 233/298 |
| 7 | `docs/audit/e4_w10_outcome_impact_19jul2026.md:95` (COMPUTABLE-NOW row) | the reachability question, labelled "COMPUTABLE NOW" | **COMPUTED: YES, 233/298** — the label held |

**Left unchanged, correctly** (these state only the *true* part — that concentration binds 100% — never
the masking inference; correcting them would itself be over-propagation):
- `docs/audit/sizing_interaction_impact_report_13jul2026.md:43` — "CONCENTRATION binds 100%" (a fact).
- memory `capital_sizing_audit_18jun.md:39,51` — "the Rs-cap never binds; concentration binds lower
  first" (the row-10-style algebra, true).
- `docs/SYSTEM_MAP.md:1165` — false positive ("swallowed by the surrounding try/except", unrelated).
- The Q9 batch-4 report/memory — **contain no masking assertion** (see §B). ~~Memory palace and `PATHS.md` carried no assertion.~~ **[⚠️ CORRECTED 21-Jul: FALSE for the memory palace — `decision_packages_19jul.md:26` and `decision_readiness_triage_19jul.md:16` carried the masking assertion UNCORRECTED until the 21-Jul sweep (`PATHS.md` was indeed clean). This summary was itself an attribution gloss — a correction doc claiming a completeness it did not have.]**

### #06's gate, restated (carried into every corrected location)
#06 is **not** gated on the concentration cap. It is gated on the multiplier's **merit — D2/D3** (is there
a per-strategy signal worth scaling by? the win-rate signal has no demonstrated ranking power: M-S4
ρ +0.003; D3's inversion did not replicate out-of-sample) — **plus the sizing/leverage picture** (which
is where #06 genuinely touches D1). The **shelf-life caveat travels with the finding**: 233/298 is against
today's **unlevered** sizing; 5× MIS would change the regime (raw_qty grows, fewer trades pin at 1, so it
would bind on *more*), and the count would need re-running. No leverage design is done here; #06's merit
question is untouched by this batch and remains Rama's.

---

## B. Does the refutation move any reachability verdict? — **NO. It moves none.**

Batch-4's headline was *"of the 15 sizing guards, only 4 can bind in production."* I reviewed each
UNREACHABLE verdict for whether it rested on the masking premise or on its own arithmetic. **This is a
review of what the verdicts rested on — batch-4's analysis was not re-run.** The Q9 coverage matrix's four
sizing rows:

| Matrix row | Verdict | What it actually rests on | Moves? |
|---|---|---|---|
| **10** `max_position_value` (40% cap) | UNREACHABLE | Position value = concentration arm (10%) × tier (0.5) ≈ 5% today; **even at perf=2.0 the 2× ceiling caps it at ~2×10% = 20% < 40%.** Its own threshold vs the position value — **robust across the whole `perf_weight` range.** Batch-4's report already reasoned the perf=2.0 → 20% case. | **No** |
| **13** M-C6 `ZERO_MULTIPLIER` SKIP | UNREACHABLE | The **pinning** (`perf_weight ≡ 1.0`) + the tier floor (≥0.5) ⇒ effective_mult ≥ 0.5 > 0. Even if the allocator were wired, the clamp `[0.5, 2.0]` keeps effective_mult ≥ 0.25 > 0; only a **negative** tier (config typo) could fire it. The matrix cites *the pinning*, **not the cap** — no leak. | **No** |
| **17** concentration + >cap rejection | REACHABLE (298/298) | The refutation **confirms** this — concentration binds 298/298. | **No** |
| **18** risk · capital · lot-skew · BELOW_MIN · FLAT · explosion · 2× ceiling | UNREACHABLE (group) | risk/capital arms **never win the `min()`** (`qty_by_risk ≤ qty_by_concentration` in 0 rows — own comparison); `lot_size=1` on all strategies ⇒ lot-skew/lot-round dead; FLAT needs OFF-mode (`enabled=true`); explosion needs a micro SL; the **2× ceiling is latent by the same pinning** (perf≡1.0 ⇒ tiered ≤ raw). None invoke masking. | **No** |

**Why none move — the key finding:** **batch-4 reasoned the post-cap mechanism correctly.** Its own
evidence package states `perf_weight=2.0 → qty=100 vs a concentration arm of 50 = 20% of capital against a
10% cap; G14 (40%) cannot catch it … NOT live today because perf_weight ≡ 1.0`. That is the post-cap
multiplier, seen correctly, latent by *pinning* — the exact opposite of "masked by the cap." **The masking
claim was a later editorial gloss introduced in decision file `06`, which mis-cited batch-4 as its
authority.** So the refutation corrects a **stated coupling** in the decision documents; it does not touch
any **verdict**, because no verdict was ever built on the masking premise.

**Not disturbed (correctly):** the separate, valid **population-bias qualification** on batch-4's headline
(`q9_coverage_matrix_final:115`; census §B1) — that several guards are unreachable *partly because the
upstream concentration cap enriches the sizer population 1.45× in >Rs 990 names* — **stands.** That is
about the population reaching the sizer, not about a multiplier being masked; conflating the two would be
the same over-propagation error this section guards against.

> **Bottom line: the refutation corrects a stated coupling but moves no reachability verdict.** Batch-4's
> "4 of 15 can bind" is unchanged; row 13's pinning reason is unchanged and correctly stated; the 2×
> ceiling remains the latent guard that arms the moment the allocator is wired.

---

## C. Memory-palace snapshot — precondition for the owed compaction, MET

The MEMORY.md compaction is owed and **deferred to after Monday**. The reason the last interrupted trim
could not be verified by byte-diff is that the palace is **not git-tracked and no backup existed**. A
timestamped snapshot is now taken (PC, outside the repo and outside production):

- **Path:** `C:\Users\rama\.claude\projects\D--Projects-trading-system\memory_snapshots\memory_snapshot_2026-07-19_201338\`
- **Contents:** 505 files, **2.07 MB**; `MEMORY.md` copied faithfully (22.95 KB source == snapshot).

**No compaction was performed** — that remains a deliberate after-Monday task, done unhurried and diffed
against a *fresh* snapshot taken at that time. The precondition (a durable, diffable baseline exists) is
now **met**, and the path is recorded in the MEMORY.md header note so the next session does not repeat an
undiffable trim. The size hook fired three times during this batch and was **declined** each time, per the
standing deferral.

---

## Deploy note
Docs-only. PC == origin == VM bare after the push; delta vs `d271525` is markdown-only; the `AUTO-INSTALLED`
crontab reinstall is the invariant no-op. All three artifacts proven byte-identical before/after. Nothing
here touches anything that boots Monday.
