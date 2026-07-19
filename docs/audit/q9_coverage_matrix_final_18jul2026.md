# Q9 — FINAL COVERAGE MATRIX (programme close-out, 18-Jul-2026)

**Supersedes the metadata columns of** `q9_money_path_coverage_map_18jul2026.md` (the original
map stays as the file:line inventory of the money paths).
**Evidence:** the six batch reports. **Nothing is re-derived here** — this is bookkeeping.

---

## 1. THE MATRIX

Fields, as established across the programme:
**W** wired · **P** positive proof · **N** negative proof · **AV** anti-vacuity ·
**PB** planted-break proof · **GO** gate/binding-order verified · **PP** production path
verified · **REACH** reachable under *current production config* (batch 4's addition).

| # | Safety layer | W | P | N | AV | PB | GO | PP | REACH | Closed by |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Kill switch @ webhook admission | ✅ | ✅ | — | — | — | — | ✅ | ✅ REACHABLE | pre-Q9 (`smoke:357`) |
| 2 | Hard-kill flatten chain | ✅ | ✅ | — | — | — | — | ⚠️ | ✅ REACHABLE | pre-Q9 — ⚠️ **mock broker only; the first live HARD_KILL is still M-C8's real test** |
| 3 | Emergency-exit chain | ✅ | ✅ | — | — | — | — | ✅ | ✅ REACHABLE | pre-Q9 (`emergency_exit_chain:136`) |
| 4 | Sector concentration (gate-8) enforce | ✅ | ✅ | — | — | — | — | ✅ | ⚠️ CONDITIONAL — prod runs `sector_cap_mode: observe` | pre-Q9 (fixture forces enforce) |
| 5 | Capital reserve→commit→release | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ REACHABLE | pre-Q9 + **batch 3** |
| 6 | Capital release on broker rejection | ✅ | ✅ | — | — | — | — | ✅ | ✅ REACHABLE | pre-Q9 (`flow:441`) |
| 7 | **Daily loss limit — pre-trade (RE7)** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ REACHABLE | **batch 1** |
| 8 | **Daily loss limit — post-close breach** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ REACHABLE | **batch 1** |
| 9 | **Kill-switch last-mile re-check (TOCTOU)** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ REACHABLE | **batch 2** |
| 10 | `max_position_value` cap | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ **UNREACHABLE** — conc (10%) × tier (0.5) ≈ 5% vs a 40% cap | **batch 4** |
| 11 | Tier multiplier | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ REACHABLE — 0.5 on 298/298 | **batch 4** |
| 12 | FIX-133 min-lot floor | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ REACHABLE — produced the qty on **65/298 (21.8%)** | **batch 4** |
| 13 | M-C6 zero-multiplier SKIP | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ **UNREACHABLE** — `perf_weight` ≡ 1.0 | **batch 4** |
| 14 | **Consecutive-losses gate (RE10)** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ REACHABLE — precondition met on **3 of 21** production days, but **0 rejections in 32,928 signals** (see §3) | **19-Jul batch** |

**Layers added by the programme** (not in the original 14):

| # | Layer | W | P | N | AV | PB | GO | PP | REACH | Closed by |
|---|---|---|---|---|---|---|---|---|---|---|
| 15 | **Capital invariant at every lifecycle stage** | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ REACHABLE — production asserts it itself (`fund_manager.py:2268`) | **batch 3** |
| 16 | **E4/W10 value contract** (reader vs ground truth) | ✅ | ✅ | — | ✅ | ✅ | — | ✅ | ⛔ fix UNPUSHED, sign-off-gated | **batch 3** (strict `xfail`) |
| 17 | Sizing: concentration + >cap rejection | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ REACHABLE — binds **298/298** | **batch 4** |
| 18 | Sizing: risk · capital · lot-skew · BELOW_MIN · FLAT · explosion · 2× ceiling | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ **UNREACHABLE** — dead by algebra/config | **batch 4** |
| 19 | **Post-restart capital replay** (Phases 1–3) | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ⚠️ **CONDITIONAL** — needs a mid-day restart | **batch 5** |
| 20 | **Post-restart kill-state survival** (KS3 + stale-clear) | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ / ⚠️ conditional (prior-day clear) | **batch 5** |
| 21 | **Post-restart daily-P&L survival + enforcement** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ REACHABLE — structural (never in memory) | **batch 5** |
| 22 | **LIVE capital seed — M-C1 cancellation** | ✅ | ✅ | ✅ | ✅ | ✅ | — | ⚠️ see §2 | ✅ REACHABLE — production runs `--mode live` | **this batch** |

---

## 2. WHAT "PP" MEANS FOR LAYER 22 (stated, not glossed)

The two-line live-seed expression (`main.py:2255-2258`) is **inline in `main()` with no function
boundary**, so no test can invoke it without running `main()` — which in live mode is precisely
what must never happen. What the wired tests drive is the machinery it depends on, all real:
`today_realized_pnl_carryover()` · `_today_release_used_pnl_rows()` (**call count 2 — one per
side, asserted**) · `initialize()` · `rehydrate_from_open_trades()`. The expression itself is
held by a structural regex pin
(`test_q9_post_restart_capital_wired.TestParity::test_the_live_seed_still_subtracts_the_carryover`).

⇒ The part that can silently break is wired; the part that is two lines is pinned. That is the
honest boundary.

---

## 3. ✅ LAYER 14 — CLOSED 19-Jul (it was the last one)

**The consecutive-losses gate (RE10) is now WIRED** — 13 tests,
`tests/integration/test_q9_consecutive_losses_wired.py`, report
`consecutive_losses_gate_wired_19jul2026.md`. It got its own batch rather than being smuggled
into M-C1, because the scenario had to thread between the daily-trades cap above it and the
post-close soft-kill below it.

**The finding that shaped it: there is no counter.** The streak is RECOMPUTED on every
`approve()` from `recent_trade_pnls(max_consec+1, today)` — never incremented, never in memory
(`core/schema.sql:459-460` says the stored column "was never written"). So:

- **it resets on any non-loss close** (a breakeven too — RE10 defines a loss as `net_pnl < -1e-6`)
  **and at the day boundary** (FIX-183, added because a cross-day streak was a **deadlock**:
  breaking it needs a win, and the block prevents one). ⇒ **The halt is not for the rest of the
  day; one winning close lifts it.** Proven, not described.
- **it survives a restart by construction** — same shape as batch 5's daily P&L. **A restart is
  NOT a bypass.**

**⭐ REACHABILITY — a category of its own.** Not UNREACHABLE like the batch-4 guards (dead by
algebra), and not routinely binding either. Measured against production: the precondition has
been met on **3 of 21 trading days** (streaks of 5, 5, 6) — yet **`REJECTED_CONSECUTIVE_LOSSES`
= 0 across all 32,928 signals**. Two checks explain it without any defect:

1. **No bypass.** On 2026-07-08 the streak reached 4 at exit **10:22:23**, but all six trades had
   been **entered by 10:14:19** — the gate reads at *entry* while the streak changes at *exit*, so
   in-flight positions cannot be retro-blocked.
2. **Why it never fired.** All **63** signals arriving after 10:22:23 that day were
   `SKIPPED_QUOTE_UNAVAILABLE` — they died upstream of the risk engine.

> **⚠️ CORRECTED 19-Jul-2026 (census `docs/audit/signal_mortality_census_19jul2026.md` claim 1).** Point 2's reason is **survivorship bias**: 2026-07-08 is in the pre-09-Jul pruned era (`REJECTED_*` deleted, `SKIPPED_*` kept), so "63 all skipped" is a prune artefact, not a pricing stop — acceptance actually *accelerated* after 10:22 (peak **1,462/hr**; 63 = **1.21%**, 6th-lowest of 21 days). **The verdict (REACHABLE, never-binding) and point 1 stand** — they rest on entry-vs-exit recomputation, verified separately.

⇒ **REACHABLE: live, correctly configured, precondition demonstrably met, never yet binding.**

---

## 4. FINAL TALLY

> **UPDATED 19-Jul — Q9 IS NOW COMPLETE AT 22/22.** Layer 14 closed; see §3.

| | Count |
|---|---|
| Original 14 layers **WIRED** | **14 / 14** ✅ |
| Total layers now wired (incl. the 8 added) | **22 / 22** ✅ |
| **REACHABLE** under current production config | **16** |
| **UNREACHABLE** (dead by algebra or config) | **3** — the value cap, M-C6, and the sizing group in row 18 |
| **CONDITIONAL** | **3** — gate-8 (observe mode), post-restart replay (mid-day restart), prior-day kill clear |
| Sign-off-gated | **1** — E4/W10 |

**The programme's headline:** of the 15 sizing guards batch 4 enumerated, only **4** can bind in
production. Coverage and reachability are different questions, and the matrix now records both.

> **⚠️ POPULATION-BIAS QUALIFICATION added 19-Jul-2026 (census §B1; `docs/audit/throttle_selection_and_record_correction_19jul2026.md`).** "Only 4 of 15 sizing guards can bind" is correct **for the population that reaches the sizer**, which is **enriched 1.45× in >Rs 990 names** (34.73% at the sizer vs 24.02% at admission); the concentration cap deletes that band before the risk engine sees it (0.14% >Rs 990 there). Several guards that "cannot bind" are unreachable partly **because of that upstream cap**, not solely their own thresholds. This bounds the domain of the claim; the arithmetic (4 of 15) is unchanged.

---

## 5. WHAT REMAINS UNPROVEN **IN PRODUCTION** AFTER MONDAY

Monday 20-Jul 08:15 is a **cold boot on a flat book** (0 open positions, kill-switch INACTIVE,
book flat since Fri 17-Jul). Therefore Phase 1 replays **0** trades, Phase 2 carries **0** P&L
rows, and the M-C1 carryover is **0** — the subtraction is a no-op. **Rehydrate is a no-op.**
Monday proves the boot does not crash on the restore path; nothing more.

Still unproven in production, all needing a **mid-day restart with live state**:

1. Phase 1 replay of a real open position
2. Phase 2 realized-P&L carryover
3. **The M-C1 live-seed cancellation with a non-zero carryover** (this batch proves the
   mechanism in the fixture; production has never exercised it)
4. Same-day kill survival across a restart

And separately: **the first live HARD_KILL is still M-C8's real test** (layer 2 — the drill used
a mock broker).

*This is the honest edge of the programme. Everything above it is proven; nothing below it is.*

---

## 6. THE HARNESS LESSON, AT PROGRAMME LEVEL

**Planting found holes in the TESTS rather than in production in five separate instances across
four batches.** A green test is evidence only if it could have been red; planting is the primary
defence against a vacuous harness, not a formality.

| Batch | What planting exposed |
|---|---|
| 4 | The tier multiplier was invisible — every test used HIGH (×1.0), so deleting the multiplier changed nothing asserted |
| 4 | The 2× ceiling was undetectable with a 2.0 multiplier — proving a ceiling needs a multiplier that *overshoots* it |
| 5 | `_restart()` reused the fixture's `KillSwitch`, so every kill assertion checked a flag the test never restored — it would have passed with the KS3 load deleted |
| report fix | `has_explicit_disposition` wrongly excluded `QUEUE_FULL`/`TIMEOUT`; and the source guard fired on its **own explanatory comment** |
| this batch | The credential guard was over-broad — it flagged `from kiteconnect import exceptions`, a legitimate exception import, and had to be narrowed to client construction and credential reads |

**And a premise lesson:** an instruction's stated premise is inherited from an earlier report and
must be verified like any other. The daily-report brief expected `3,098 → 0`; the measured truth
was **"mislabelled, not miscounted"**.

---

*Bookkeeping only — no code, config, schema or flag changed by this document.*
