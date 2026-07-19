# Decision-readiness triage — which of the ten are answerable now, and the E4/W10 deploy prep (19-Jul-2026)

**Docs-only. Nothing was deployed, merged, started or booted; no recommendation is made.** This prepares two of the areas Rama named — §B (E4/W10 sign-off + deploy) and §D (the ten decisions) — so he can act. It ships nothing. The E4/W10 branch was **not** checked out, merged, rebased, tagged or pushed; the inspection behind §C was read-only (`git log`/`merge-base`/`diff <commit>..<commit>`).

Deploy state: PC == origin == VM bare == `1462984`; code tag `deploy-19jul-consecutive-losses` → `d271525` (delta markdown-only). Schema v44. System DOWN, book flat, kill INACTIVE.

---

## A. The ten, triaged — three are answerable now; seven are waiting on something specific

"Ten open decisions" is true and unhelpful — three turn only on Rama's judgement today, and the other seven are each gated on a named thing. Classification verified against the decision files (not inherited).

| # | Decision | Bucket | The gate, named | Does waiting cost anything? |
|---|---|---|---|---|
| **01** | **E4/W10** — `pnl_delta` contract | **DECIDABLE NOW** | none — evidence complete (N=0 computed); residual is a posture call + a manual-flatten deploy precondition | **No.** N=0 of 23 days; the double-count changes nothing on the current book. |
| **08** | **Freeze `min_pass_score`** | **DECIDABLE NOW** | none — a priority/hygiene judgement, not evidence | **No** — and it is **already frozen by inaction** (nobody is changing `min_pass`; D3 is gated). Not a deadline. |
| **09** | **Prune retention** | **DECIDABLE NOW** | none — turns on Rama's intent (is rejection-composition a recurring need?) | **No.** The daily prune deletes **0 rows until ~10-Sep**; a 6-day snapshot already preserves current rejection data. |
| **02** | **D1** — concentration cap | **GATED ON ANOTHER DECISION** | **D3** (raise the cap only once the book's sign is known) **+ the leverage design area** | No cost to *leave open*. The cost is one-directional: raising on a negative book scales loss (2× at 0.25, 4× at 0.50). |
| **06** | **PerformanceAllocator** | **GATED ON ANOTHER DECISION** | **D1 / the sizing-&-leverage picture** — a multiplier is masked by the cap that binds 100% of trades | **No.** `perf_weight` is pinned 1.0; leaving it unwired is a no-op. (A reachability algebra on existing data would quantify the masking — cheap, but unrun and its "computable now" label is distrusted.) |
| **10** | **Entry-throttle admission** | **GATED ON ANOTHER DECISION** | **D3** — ranked admission (Option C) only has value if the score ranks, which D3's own evidence says it does not | **No** for the ranking part. The **visibility** sub-point (persist the throttle's gate category to a structured column) is a cheap, independent reporting change. |
| **03** | **D2** — strategy mix | **GATED ON EVIDENCE** | **Q10 Part B** (Kite token → backfill → *months*) + a positive-control baseline (D3). Even the backfill can't determine at n=23 — it starts the clock. | **Possibly** — the one with a delay cost. A *genuinely-broken* strategy keeps draining (e.g. `vwap_bounce_long` −12.1R over the window). But broken-vs-regime is **unmeasurable today**, so the cost is a real drain of unknown regime-dependence. |
| **04** | **D3** — `min_pass_score` (band inversion) | **GATED ON EVIDENCE** | **forward-shadow OOS days** — accrue only as the system runs; a robust verdict needs >1 regime. The first 3 OOS days point *away* from the finding. | **No** to leaving at 60. If the backtest is right, trading the worst band is an ongoing opportunity cost — but that is exactly what is unproven (and the first OOS window contradicts it). |
| **05** | **D4** — exit policy | **GATED ON EVIDENCE** | **power** — ~**17–34 trading days** of winners (≈50–100), which accrue only as the system runs (candles survived; the sample did not). | **No.** The best backtested policy only reaches **~breakeven** anyway; this is a drawdown/tail question, not an edge question. |
| **07** | **Regime** — enable / build / leave | **GATED ON EVIDENCE** | **Q10 to power** — ~**2.2 months** forward within-cell data (+ the Kite token to start the bands) + a "do-not-flip-mid-soak" constraint. | **No.** The shadow data needed to know either way **accrues regardless** of the decision. |

**Coupling (from the index, not re-derived):** D1↔D3 · D3↔#08(Freeze, mutually exclusive in one window) · D3↔#10 · D2↔#07↔Q10 · #06↔D1. So the seven gated decisions collapse to a few roots: **D3/the scorer** (gates 02, 04, 08, 10), **Q10/the token** (gates 03, 07), and **power/running-days** (04, 05). Answer D3 and refresh the token, and most of the board unlocks in sequence.

**A4 — no manufactured urgency.** #08 (Freeze) is de-facto in effect because nobody is proposing to move `min_pass_score` and D3 is gate-class; it is stated as a standing state, not dressed as a deadline. Only **#03 (D2)** carries any cost-to-delay, and that cost is itself unquantifiable from today's record.

---

## B. One-screen briefs for the three decidable-now decisions

Written so Rama can answer from the brief without opening the long file. Each states the choice, both options with equal weight, the single number that matters, the scope boundary, and the do-nothing default. New files in `docs/decisions/` (no existing equivalents — checked), each marked as a summary that does **not** supersede its full file:

- [`BRIEF_01_e4_w10_pnl_contract.md`](../decisions/BRIEF_01_e4_w10_pnl_contract.md)
- [`BRIEF_08_freeze_min_pass.md`](../decisions/BRIEF_08_freeze_min_pass.md)
- [`BRIEF_09_prune_retention.md`](../decisions/BRIEF_09_prune_retention.md)

**Compression test applied (a reader must be able to argue *either* side from the brief):**
- **01** — A: correct accounting, N=0 so adoption is safe, closes a known double-subtract. B: why loosen a risk control that has never mis-fired, especially before D1/leverage could make the margin matter — the loosening, though tiny now, is not robust to ~5× sizing. **Both arguable. ✓**
- **08** — A: measurement hygiene; a moving threshold makes the regime comparison unconcludable. B: why freeze D3 for months to protect a measurement Q10 already calls NOT DETERMINABLE at n=23? **Both arguable. ✓**
- **09** — A: rejection volume is large with no *demonstrated* recurring need; the snapshot covers today. B/C: rejection composition is exactly what the census/throttle analyses consumed — losing it forecloses future audits, cheaply preventable. **Both arguable. ✓**

**B4 — for 01 specifically:** the brief makes plain that the **evidence question is closed** (N=0 of 23 days; Rs 243 of margin at the worst point; threshold ~Rs 300/day) and what remains is a **posture judgement plus a deploy precondition — not more analysis.**

---

## C. The E4/W10 deploy runbook — written, not executed

[`docs/decisions/RUNBOOK_e4_w10_deploy.md`](../decisions/RUNBOOK_e4_w10_deploy.md) covers, end to end: the manual-flatten precondition · the exact branch/commit (`e4-w10-pnl-contract`@`ad34ee4`) and what it changes · the integrate/push/tag sequence in this repo's conventions (origin **is** the VM bare repo, so a push deploys and the post-receive reinstalls the crontab) · pre-deploy checks (full regression on the merge result vs the 14-failure baseline; identity; fingerprints) · the **post-deploy verification signature** (`RESET_PNL` must stop equalling `−(Σpnl_delta − Σcosts)` and start equalling `−Σpnl_delta`) · rollback (revert, schema-free, minutes) · the 36 pre-fix `costs=0` rows and why deploying after an EOD reset is clean.

**Two things the read-only inspection established for the runbook:**
- **The branch is 81 commits behind its base** (`4c148fb`); this is not a fast-forward. But the substantive merge is **conflict-free**: the five capital-path code files (`fund_manager.py`, `state_store.py`, `order_reconciler.py`, `cnc_gtt_monitor.py`, + new `cost_calculator.py`) had **0 commits on main since the base**. The only 3-way merge points are `main.py` (1 main commit; different region) and two docs.
- **Timing is stated at the top, not buried:** this is a capital-path change, so it goes **after Monday's session is observed**, unhurried, through the careful loop — never as a "while we're here," and never on a trading morning. **No step of it was executed.**

---

## D. The post-Monday queue, ordered — with type and rough size

| order | item | type | size | independent of all 10 decisions? |
|---|---|---|---|---|
| 1 | **Live-seed extraction from `main()`** (`main.py:2255-2258` inline → a function; held only by a structural regex pin) | boot-path | small | **Yes** |
| 2 | **`check_scanner:703`** — add the `== 401` tolerance (like `:808`); **do NOT escalate `scanner_unreachable` to blocking** | boot-path | small | **Yes** |
| 3 | **Candle retention** — will the OOS 1-min candles in `analytics.db` survive the ~17–34 trading days D4 needs? **Verify (`db_retention` cron), do not assume.** | observability / verification | small | **Yes** — and it gates D4's evidence, so do it regardless |
| 4 | **PerformanceAllocator reachability algebra** — does a `perf_weight ≠ 1` change final qty under the current cap on any recorded trade? Treat "COMPUTABLE NOW" as **unverified**. | analysis (decision-support for #06) | small–med | analysis is independent to run; it informs #06 |
| 5 | **Live-path quote observability** — no partial-response detection on the live quote path | observability | medium | **Yes** |
| 6 | **403 signal-count instrumentation** — the ~338k pre-10:00 403 POSTs are never counted | observability | small | **Yes** |
| 7 | **Deferred `event_type` sites · `Rejected (Sizing/Capital)` label · `build_taxonomy_map()` `--config-dir`** | reporting / cleanup | small each | **Yes** |
| 8 | **Wave-7 backlog** | mixed | unknown | depends on contents |
| 9 | **The degenerate Rs 0.29 SL distance** | analysis / bug | small–med | **Yes** |

**Items 1, 2 group as boot-path careful-loop work** — same class, worth doing together in daylight with Monday's behaviour freshly observed. **Most of the queue (1–3, 5–7, 9) is independent of every open decision** and can proceed whatever Rama decides.

**D3 — the leverage question is named separately, as an open design area, not a task.** The system sizes against **unlevered** capital; every limit (daily-loss %, concentration cap, tiers) was calibrated when margin and notional were the same number. With 5× MIS buying power, **the recalibration is the work — not the multiplier**. This is not on the queue above and **no design is done here**; it is flagged so it is not mistaken for a one-line change. It also sits underneath D1 and #06.

**D4 — the MEMORY.md compaction is owed, and here is how to do it.** It is deliberately deferred to after Monday (the file is ~21 KB over its ~17 KB target, which is acceptable — §B5 of the readiness batch). **Take a snapshot first.** The reason last batch's interrupted trim could not be verified by byte-diff is that the memory palace is **not git-tracked and no backup existed**; a snapshot before the next compaction makes it diffable. Do it deliberately, not under a size-hook's pressure.

---

*Docs-only. No decision was made or recommended; the E4/W10 branch was not touched; nothing was deployed, started, or booted. Monday does not depend on this, and this does not depend on Monday.*
