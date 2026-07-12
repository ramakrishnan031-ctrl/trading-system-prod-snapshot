# V3 SHARED-ENGINE — STEP 10: PIPELINE PLUMBING (PLAN ONLY)

**Date (IST):** 12-Jul-2026 · **Baseline:** `main == origin/main == bare HEAD == c1ad82e` (V3 shared engine Steps 1-9 deployed; VM config **`v3_hardgate_mode: shadow` + `allocator_mode: shadow`, everything else OFF**) · **Mode:** DESIGN / PLAN ONLY — **no code, no config, no behaviour change; deploy ledger UNCHANGED (nothing built).** · **Method:** every premise re-verified against `main`@`c1ad82e` source (file:line cited). Self-contained for Web Claude + ChatGPT + Rama review.

> **Objective.** Design how a signal flows end-to-end through the V3 decision chain — **Market Regime → S&R → V3 Hard Gates → V3 Score → Portfolio Allocator → Risk/Sizing → (would-be) Entry** — running in **SHADOW** alongside the live pipeline, producing WOULD-BE trades with **NO real orders**, so it can be compared to live and later promoted. The chain must thread through the **EXISTING** pipeline via **ONE flag-selected seam** (R2 — no parallel V3 processor, no duplicated business logic). This is the plan; a separate instruction authorizes the build after approval.

> **Headline (read this first).** Steps 1-9 delivered every V3 module as an **independent, local shadow**: each observes/logs its OWN decision and gates nothing. What is genuinely **MISSING** is not more modules — it is (1) the **decision content** that makes it a "chain" (a playbook definition, the playbook-scope hard gates, the 3-layer score), (2) the **consumption wiring** (regime + S&R *produce* output that **nothing consumes**), (3) a **unified would-be-trade record** that captures one candidate's whole-chain verdict, and (4) PB-01's **front-end** (a stateful overnight watchlist + next-morning entry, which is entirely new). The **plumbing template already exists and is proven** — the `_process_one` sizing→allocator-hook seam (`signals/signal_processor.py:924-965`). The plan threads the chain through that seam and is honest that the V3 IP (gates + score + playbook) is the real remaining build.

---

## TASK 0 — HONEST INVENTORY: WIRED vs MISSING (the foundation)

Source-verified against `main`@`c1ad82e`. "Built" = code exists; "Wired" = something consumes its output on a decision path; "Consumed" is the operative gap.

| V3 stage | Built? | Wired / consumed today? | What's missing to run ONE signal end-to-end in shadow |
|---|---|---|---|
| **03.02 Market Regime** | **YES.** `regime/` pkg (engine + `MarketRegimeShadowRunner`), default-OFF (`regime.enabled: false`). Computes index 3-axis regime once/cycle, logs, persists `latest` (`regime/runner.py:63-75`). | **NO — consumed by nothing.** `runner.latest` (`runner.py:40`) is exposed but no pipeline reads it; the docstring says it "gates NOTHING" (`runner.py:6-7`). | (a) VM live **NIFTY index-historical access** verified (T4, deferred — Sunday no live token; token 256265). (b) A **consumer**: inject `regime.latest`/persisted into the per-signal V3 branch as the **Context-layer** input + the `extreme_flag` into the gate. |
| **03.01 S&R Detection** | **YES.** `sr_detector/` shadow/observer; intraday VWAP/ORB anchors behind default-OFF `sr_detector.intraday_anchors_enabled`; `confidence_class` defaults `ANCHOR_ONLY`. Observed at `signal_processor.py:517` (`_sr_detector.observe(Candidate)`) — background, log-only. | **NO — observer only.** Nothing consumes levels for a decision; `confidence_class` never flips past `ANCHOR_ONLY` this step. | A **consumer** that reads the symbol's S&R levels/zones **synchronously** at candidate time (the `ZoneCache` pattern the SNR-V2 diverter already uses, `signal_processor.py:840`) → feeds the **S&R-based R:R gate** (03.03), the **computed SL** (03.06 `sl_price` seam), and the **Context-layer S&R-quality** score. |
| **03.03 Hard Gate** | **PARTIAL.** LIVE rules IMPLEMENTED + wired: at-circuit, circuit-proximity, freshness (60s) in `HardGate.evaluate()` (`screening/hard_gate.py:135-164`), invoked in `secondary_screener.screen()` (shadow `:292`, enforce `:184`). Liquidity = NO-OP (A8). | Circuit/age/proximity **wired** (shadow logs OLD-vs-NEW; live follows OLD). | The **PLAYBOOK-scope gates** — **confirmation, valid pullback, S&R-based R:R, strong-HTF, extreme** — are **NAMED in the docstring** (`hard_gate.py:6-8`) but have **ZERO code**. They are **stubs awaiting playbook context** (S&R levels, regime, HTF alignment, playbook rule — none threaded yet). **This is remaining work, not built.** |
| **03.04 Scoring** | **PARTIAL.** The **8-step re-scale (the "Execution SEED")** is BUILT: `rescaled_total` / `tier_for` (`hard_gate.py:171-196`), v3 thresholds 50/56/75, shadow logs OLD-vs-NEW (`secondary_screener.py:512`). The old `QualityScorer` is **UNCHANGED** (`quality_scorer.py:59-129`). | Execution-seed re-scale **wired in shadow** (parity-proven offline, 114,807 rows). | The **V3 THREE-LAYER score (Playbook 40 / Context 40 / Execution 20)** is **NOT built.** Only the Execution layer's seed exists. Missing: the **Playbook** layer (playbook-fit), the **Context** layer (regime multiplier + S&R quality + HTF alignment), and the 40/40/20 composition + re-scale of the seed into the 20. |
| **03.05 Portfolio Allocator** | **YES.** `allocation/` pkg, default-OFF; **shadow** observes→regret (`portfolio_allocator.py:84,255`); enforce batch worker built but inert (`:278`). | **Shadow — but on the WRONG set.** It observes the **existing FCFS candidate flow** (every screened+sized signal), NOT a V3 candidate set: `enforce_scope: v3_only` + `v3_scope_fn=lambda s: bool(getattr(s,"v3_playbook",False))` (`main.py:2732`) → **always False** (no strategy has the attribute) → "none exist yet" (`config/system_config.yaml:358`). | **V3-playbook strategies must exist + be tagged** so the allocator scopes its ranking/regret to the V3 subset. The mechanism is ready; the input set is empty. |
| **03.06 Risk & Sizing** | **YES / COMPLETE.** `position_sizer` + `risk_engine` (10 gates) + `fund_manager`; inert delivery scaffold; LIVE SL still FIXED_PCT. | Wired live; sizing runs on every signal (`signal_processor.py:904`). | The **`sl_price` param is an OPEN seam** (the sizer derives no SL itself) — but **nothing feeds an S&R-derived SL into it** on a V3 path. Wire the 03.01/03.03 `computed_sl` → the sizer's `sl_price` arg on the V3 candidate only (no sizer change; LIVE SL unchanged). |
| **03.07 Entry** | **YES / COMPLETE + battle-tested.** `order_placer` / `full_entry_engine` / protocols; software OCO. | Wired live (`_admit_and_place` places real orders, `signal_processor.py:1045`). | Nothing missing for LIVE. The **shadow V3 path must STOP before `place()`** and emit a would-be record instead of admitting. |
| **03.08 Trade Mgmt** | **YES / COMPLETE.** Single-SL-owner; monotonic managers. | Wired live (post-entry). | **N/A for shadow** — only matters after a real entry, which shadow never reaches. |
| **03.09 Exit** | **YES / COMPLETE.** Terminal exits / OCO-cancel / 15:17 squareoff (CNC-exempt) / kills. | Wired live (post-entry). | **N/A for shadow.** |
| **Playbook registry / PB-01 definition** | **MISSING entirely.** No playbook module; no `v3_playbook` field on `StrategyConfig` (`strategies/schema.py:35` — verified absent); no PB-01 definition. | — | A **declarative playbook definition/registry** (which gates apply, which score inputs, the entry rule) + **PB-01** authored. This is the **decision content** — the real V3 IP. |
| **Watchlist / next-morning entry** | **MISSING** (confirmed Phase 0 + Step 9). `EntryGate` is a **same-day intraday** pullback watchlist, **cleared at EOD** to prevent stale next-morning rehydration (FIX-046). | — | A **NEW stateful overnight watchlist**: EOD Chartink candidate → **persisted across the night** → next-morning **5-min retest entry** → emits a signal into `_process_one`. Required by PB-01; the largest NEW front-end piece. |

**One-line summary for Rama:** the *pipes* are built and OFF (seam, candidate record, allocator observer, regime runner, S&R observer, sizer, entry engine); the *water* is not (playbook definition, playbook gates, 3-layer score), and regime/S&R currently *drip to the floor* (produce output nothing drinks). "Run one signal end-to-end through the V3 chain in shadow" is **not achievable today** without building the decision content + the consumption wiring + (for PB-01) the overnight watchlist.

---

## P1 — THE MINIMUM PATH TO PB-01 IN SHADOW

Shortest honest list of what must be built for **one PB-01 candidate to flow end-to-end through the V3 chain in shadow** (a would-be trade, no order):

1. **Playbook substrate** — add `v3_playbook: bool = False` to `StrategyConfig` (default → the 15 live strategies are byte-identical) **+** a **declarative playbook registry** (per playbook: required gates, score inputs, entry rule) **+ PB-01 authored** (its scanner source, gates, score, next-morning entry rule).
2. **Consumption wiring** — inject the **regime** snapshot (`runner.latest`/persisted) and a **synchronous S&R level read** into a new, flag-gated **V3 enrichment branch** on the existing `_process_one` path (read-only; no hot-path fetch).
3. **The 3-layer score** — build **Playbook (40)** + **Context (40)** layers on top of the existing **Execution seed (→20)**; compose 40/40/20; recalibrate thresholds. (The seed + shadow OLD-vs-NEW plumbing already exists — extend it.)
4. **The playbook-scope hard gates** — implement **confirmation / valid-pullback / S&R-R:R / strong-HTF / extreme** in `HardGate` (playbook scope; **log-only on the V3 path, never reject a live order**).
5. **Unified would-be record + shadow emission** — extend the candidate **additively** (gate results + 3-layer score + S&R levels + regime + computed SL/TGT + R:R + rank + would-admit?); emit it as a would-be-trade row at the sizing→admit seam under a `v3_chain_mode: shadow` flag; wire the **existing `shadow_tracker`** to simulate the would-be outcome; extend `scripts/v3_shadow_soak_report.py` with a `--v3` reader.
6. **Stateful overnight watchlist + next-morning 5-min entry** — the NEW front-end: EOD-capture PB-01 candidates → **persist overnight** → next-morning 5-min retest monitor → emit a signal into `_process_one`. (Largest NEW piece; it is what makes PB-01 a *real* end-to-end candidate rather than a synthetic one.)

**Honesty note:** items 1, 3, 4 are the V3 IP (not plumbing) and need their own rules/thresholds from Rama/ChatGPT (see P10 open questions). Items 2, 5 are pure plumbing on the proven seam. Item 6 is a self-contained new subsystem. The chain (2-5) can be **built and soaked against existing live signals first** (a webhook signal exercises the enrichment), and PB-01 (1 + 6) rides it afterward — so the plumbing is proven before the new front-end is added.

---

## P2 — THE SEAM (the core design question)

**Options evaluated:**

- **(a) A V3 branch INSIDE `signal_processor._process_one`, flag-selected.** ✅ **RECOMMENDED.**
- (b) A separate shadow consumer of the same signal stream.
- (c) A replay / offline harness.

**Recommendation: (a), realized as (i) ENRICHMENT of the candidate already flowing through `_process_one`, plus (ii) a flag-gated STOP-and-emit for `v3_playbook` candidates.**

**Evidence it fits (no duplication):**
- `_process_one` **already is** the pipeline: pre-flight → strategy-control gate → `screen()` (hard-gate + score shadow **already live here**, `secondary_screener.py:184,292`) → `_derive_prices` → `_sizer.calculate` (`:904`) → **allocator hook** (`:926`) → `_admit_and_place` (`:1045`). **Every V3 stage already has a home on this one path.**
- The **allocator hook at `signal_processor.py:924-965` is the proven template** for "observe/submit a candidate at the PREPARE→ADMIT boundary." The V3 would-be record emits at the **same seam** — after sizing, before/instead of admit.
- `_build_candidate` → `ScoredCandidate` (+ `AdmitPayload`) (`signal_processor.py:1328`, `allocation/models.py:36`) is the **existing candidate primitive**. **Extend it additively** with the V3 fields — do not build a parallel record.
- **Regime is index-level (once/cycle)** — it is already a runner with `.latest`; **inject it** so the V3 branch *reads* the current regime without recomputing (no duplication).
- **S&R is per-symbol** — `sr_detector` is already injected and observed (`:517`); the V3 branch reads levels from the **same detector/ZoneCache synchronously** — the SNR-V2 retest diverter proves this exact synchronous-cache-read pattern on the hot path (`:840`, "reads the ZoneCache SYNCHRONOUSLY (never fetches on the hot path)").

**Why NOT (b):** a separate stream consumer would **re-implement** strategy lookup, screening, price derivation, and sizing — exactly the duplicate/parallel path R2 forbids — and would drift from live (two copies of the pipeline to keep in parity forever).

**Why NOT (c):** a replay/offline harness is valuable for **calibration** and we already have the offline tools (`v3_hardgate_parity_recompute.py`, `v3_shadow_soak_report.py`). But it **cannot** produce a *live* would-be trade to compare against the live pipeline's *actual* trades in real time — it is a **complement**, not the seam.

**How (a) avoids the duplicate-structure trap:** the V3 chain is **not a second run of the pipeline**. It is (i) additive **enrichment** of the candidate already computed by the live path, and (ii) a **flag-gated fork at the admit boundary** — for a `v3_playbook` candidate under `v3_chain_mode: shadow`, write the would-be record **instead of** calling `_admit_and_place`; for everything else, fall through unchanged. Screen/derive/size are **reused verbatim**. The only NEW code is the **decision content** (playbook gates + 3-layer score + registry) and the **watchlist front-end** — never a parallel signal path.

**PB-01's front-end is a new signal SOURCE, not a new pipeline.** The overnight watchlist → next-morning retest emits a signal into the **same `_process_one`** (exactly as `webhook_receiver` does today). One pipeline, one seam, multiple sources.

---

## P3 — THE V3 CHAIN ORDER + DATA FLOW

`regime (once/cycle, index) → S&R (per symbol) → hard gates → score → allocator → sizing → would-be entry`

| Stage | Input it needs | Where the input comes from TODAY | Must be ADDED |
|---|---|---|---|
| **Regime** | NIFTY index OHLC | `OhlcFetcher.fetch_by_token(256265)` → `MarketRegimeShadowRunner.run_once()` → `.latest` (`regime/runner.py:63`) | Inject `.latest`/persisted into the per-signal V3 branch (Context input + `extreme_flag` → gate). Verify live index access (T4). |
| **S&R** | Per-symbol levels/zones/anchors (VWAP/ORB + PDH/PDL/PDC + swings, `TF_ROLE`) | `sr_detector` (anchors behind `intraday_anchors_enabled`); today observed only (`:517`) | A **synchronous read** of the symbol's levels at candidate time (ZoneCache pattern, `:840`) → R:R gate + computed SL + Context S&R-quality. |
| **Hard gates** | trigger/entry + `market_data` (circuit/age) **+** playbook context (confirmation, pullback, S&R-R:R, HTF, regime-extreme) | circuit/age/proximity already in `HardGate` (`hard_gate.py:135`) + `market_data` built in `screen()` | **Implement the playbook gates** reading S&R + regime + HTF + the playbook rule (`hard_gate.py:6-8` are stubs). |
| **Score** | 8 execution steps **+** Playbook-fit inputs **+** Context inputs (regime mult, S&R quality, HTF alignment) | 8 steps + `rescaled_total` seed (`hard_gate.py:171`) | **Playbook (40) + Context (40)** layers + 40/40/20 composition + seed→20 rescale. |
| **Allocator** | A `ScoredCandidate` set within a window | `_build_candidate` at the sizing seam (`:1328`); allocator `observe`/`submit` (`:84/:94`) | Tag PB-01 `v3_playbook` so `enforce_scope: v3_only` scopes to it; extend regret to the V3 subset. |
| **Sizing** | entry, `sl_price`, tier | `_sizer.calculate(... sl_price ...)` (`:904`); LIVE `sl_price` is FIXED_PCT | Feed the **S&R-computed SL** into `sl_price` on the V3 candidate only (no sizer change). |
| **Would-be entry** | The fully-enriched candidate | `_admit_and_place` (`:1045`) places real orders | On the shadow V3 path, **STOP before `place()`** and write the would-be record. |

---

## P4 — SHADOW OUTPUT (the promotion evidence)

**What a V3 shadow decision PRODUCES** — one **would-be-trade record** per PB-01 candidate:
- **identity:** signal_id, symbol, scanner, playbook (PB-01), side, intent, ts
- **regime snapshot:** direction / volatility / day-type + confidences + `preference_multiplier` + `extreme_flag`
- **S&R context:** nearest support/resistance, zone `confidence_class`, computed **R:R**, computed **SL/TGT**
- **gates:** each gate → pass/fail + reason (at-circuit, proximity, freshness, confirmation, pullback, S&R-R:R, strong-HTF, extreme)
- **score:** total + the **three layers** (playbook / context / execution) + tier
- **allocator:** rank within the window, **would-admit?**, crowd-out/starvation context
- **sizing:** qty, margin_required, SL, TGT, R:R
- **decision:** `WOULD_ENTER` / `WOULD_REJECT_<gate|score|rank>`

**Where persisted:** a NEW **JSONL** shadow store `data_store/v3/would_be_trades.jsonl` — **NO schema change**, mirroring the allocator's `regret.jsonl` and the shadow-tracker pattern. (A real table is a *later* decision, only if promoted.)

**How it is COMPARED to the live pipeline (honest — this differs from the existing soaks):**
- The **scorer soak** (`v3_shadow_soak_report --scorer`) already compares **OLD-vs-NEW score on the SAME live signals**.
- The **allocator soak** (`--allocator`) already compares **ranked-vs-FCFS regret**.
- **PB-01 has NO live counterpart** (it is a new next-morning strategy), so its promotion evidence is **NOT** OLD-vs-NEW on existing trades. It is: **(i)** the would-be book's **track record** — simulate each would-be trade's outcome via the **existing `shadow_tracker`** on live quotes → win-rate / expectancy; **(ii)** gate/score **calibration** (are gates rejecting sensibly? is the 3-layer score separating winners?); **(iii) zero live-behaviour delta** (the whole chain is shadow; the live 15 strategies stay byte-identical). Add a `--v3` mode to `v3_shadow_soak_report.py` that reads the would-be JSONL and reports these. **Do not over-claim parity — surface that PB-01's evidence is its own track record.**

---

## P5 — TWO-PIPELINE SCAFFOLDING (delivery stays OFF)

**PB-01 rides the INTRADAY pipeline, not delivery.** "EOD candidate → next-morning **5-min retest entry**" = a **next-morning intraday** entry (5-min retest, same-day exit under `force_intraday_only`). The **overnight** part is the **WATCHLIST (analysis carry)**, **not a position carry** — no CNC, no GTT, no capital held overnight.

- PB-01's strategy YAML declares `intent: INTRADAY` + `v3_playbook: true`. `force_intraday_only: true` / `delivery_enabled: false` / `trade_type: INTRADAY` all **unchanged** → the two doubly-locked delivery gates + the GTT carve-out are untouched; the 12-WILL/3-WON'T Option-A split is **not** disturbed.
- The watchlist is a **pure analysis store**: holds no capital, places no order. So the "two-pipeline" scaffolding here is simply: **intraday = live (PB-01 shadow rides it); delivery = still OFF (PB-01 does not touch it).**
- **A later playbook wanting true overnight *position* carry is Slice 2.5** (separate, DDPI-gated, T2 branch `854112b`) — **explicitly out of scope** for this plumbing plan.

---

## P6 — FLAG DESIGN + OFF BYTE-IDENTITY

**Existing V3 flags (unchanged):** `scoring.v3_hardgate_mode` (off/shadow/enforce), `system.portfolio_allocator.allocator_mode` (off/shadow/enforce), `regime.enabled` (bool), `sr_detector.intraday_anchors_enabled` (bool).

**NEW flags this build introduces:**
- `StrategyConfig.v3_playbook: bool = False` — default keeps all 15 live strategies byte-identical; only PB-01 sets `true`.
- A master **`v3_chain_mode: off | shadow`** (default `off`) gating the enrichment branch + the would-be emission. **Binary for now** — `enforce` is a far-later step; keeping the flag binary avoids a dead `enforce` branch on the hot path.
- **`watchlist.enabled: false`** gating the overnight capture + next-morning entry stage.

**OFF byte-identity (the proof obligation):** with `v3_chain_mode: off` + every strategy `v3_playbook: false` + `watchlist.enabled: false`:
- the enrichment hook is **skipped by a single flag check** (mirroring `if self._allocator is not None:` `:926` and `if self._sr_detector is None: return` `:499`);
- **no** would-be JSONL row is written; **no** new signal source emits; `_admit_and_place` runs **verbatim**.
- **Proof method (reuse the established gates):** (i) `test_signal_processor` **stash-diff = zero-new** (as every prior step proved); (ii) the `test_main` **clean-vs-dirty 26==26** OFF byte-identity check; (iii) a targeted test: `v3_chain_mode: off` ⇒ 0 would-be rows + live path identical to baseline on a fixed signal set.

---

## P7 — REGRESSION MAPPING

- **Callers / side-effects of the seam.** `_process_one` is the **sole** caller of `_admit_and_place` (fused) and `admit_prepared` (enforce). The V3 enrichment inserts **between sizing (`:922`) and the allocator hook (`:926`)** — the *same* boundary the allocator already occupies. **Must preserve:** the reservation lifecycle (`_AdmitCtx`), in-flight bookkeeping (FIX-165c / FIX-018 TOCTOU), the `queue.Full` reservation-leak guard, and the TOCTOU-hardened capital/count caps inside `portfolio_lock`. The enrichment is **read-only and pre-reservation** — it touches none of these.
- **Hot-path impact (the chain must NOT slow or perturb live — it is shadow):** (i) regime read is **O(1)** from `.latest`/persisted (no compute on the signal path); (ii) S&R read is a **synchronous cache read** (never a hot-path fetch — SNR-V2 diverter pattern `:840`); (iii) the would-be emission + shadow-tracker simulate are **fire-and-forget, fully guarded, never raise into the pipeline** (mirroring `_sr_detector.observe` `:518-520` and `allocator.observe` `:89-92`); (iv) a non-`v3_playbook` signal costs a **single flag check** then skips the whole branch.
- **Parity (paper==live).** The chain is mode-agnostic (same quotes, same math); the would-be record is written identically in both modes; `shadow_tracker` already handles paper/live. No new paper/live branch is introduced.

---

## P8 — SEQUENCING (ordered build steps → "PB-01 runs in shadow")

1. **Playbook substrate** — `v3_playbook` schema field (byte-identical) + declarative playbook registry + author PB-01.
2. **Consumption wiring** — inject regime snapshot + synchronous S&R level read into a flag-gated V3 enrichment branch on `_process_one` (read-only, behind `v3_chain_mode`).
3. **3-layer score** — Playbook (40) + Context (40) on the Execution seed (→20); compose; recalibrate; shadow-log OLD-vs-NEW (extend the existing `_log_v3_shadow_compare` plumbing).
4. **Playbook hard gates** — implement confirmation / valid-pullback / S&R-R:R / strong-HTF / extreme in `HardGate` (log-only on the V3 path; never reject live).
5. **Unified would-be record + shadow emission** — extend the candidate additively; emit the JSONL at the sizing→admit seam under `v3_chain_mode: shadow`; wire `shadow_tracker` to simulate outcomes; add `v3_shadow_soak_report --v3`.
6. **Stateful watchlist + next-morning 5-min entry** — the NEW front-end (EOD capture → **overnight-durable** persist → next-morning 5-min retest → emit into `_process_one`). **Design its persistence + expiry to NOT rehydrate stale** (the exact class of bug FIX-046 fixed for `EntryGate`).
7. **Allocator scope** — tag PB-01 `v3_playbook` so the (already-shadow) allocator scopes its regret to the V3 subset.
8. **Soak** — run PB-01 in shadow ≥N sessions; the would-be track record + gate/score calibration is the promotion evidence.

**Where the watchlist fits (explicit):** it is **step 6** — the **signal source** for PB-01, so it must exist before a *real* PB-01 candidate can flow. But **steps 2-5 (wiring/score/gates/record) can be built and validated against existing live intraday signals first** (a webhook signal exercises the enrichment end-to-end without the watchlist). So: **prove the plumbing on existing signals, then add the new front-end and let PB-01 ride it.** This de-risks the largest NEW piece by validating the chain before it depends on the watchlist.

---

## P9 — CUTOVER + CLEANUP

- **Two enforce dimensions already have soaks:** the scorer/gate (`v3_hardgate_mode` shadow→enforce, offline parity artifact done) and the allocator (`allocator_mode` shadow→enforce). For **PB-01 specifically, "enforce" = PB-01 actually PLACES** (stops being would-be) — gated by `v3_chain_mode: shadow→enforce` (or a per-playbook go-live flag) **only after** the would-be soak proves the track record.
- **Live flip vs off-market halt-cutover (R6).** The scorer/allocator enforce flips are **config-only** → off-market restart (as the ledger already plans). **PB-01 go-live (first real order from a NEW strategy) should be an off-market enable + a supervised first session** — the T2-canary discipline (Rama authorizes; a supervised watch). If a cleaner cutover needs a halt, "temporary downtime is preferable to permanent complexity" applies.
- **Cleanup (R5) — the flags are migration scaffolding, not permanent:** (i) remove the **fused FCFS path** once allocator enforce is proven (the Step-6 plan already commits to this); (ii) at PB-01 promotion, decide whether the **would-be emission** is removed or kept as a **permanent shadow-vs-live comparator**; (iii) collapse `v3_chain_mode` to always-on once V3 is *the* pipeline. State the removal at promotion time, per R5.

---

## P10 — RISKS + OPEN QUESTIONS

**Risks:**
1. **The decision content is the real, unbuilt V3 IP.** Playbook gates + 3-layer score are **not plumbing**; they consume regime + S&R + HTF that must be **precisely defined** and **calibrated**. This plan makes the plumbing honest; the gates/score need their own design + parity discipline.
2. **S&R consumption must stay a synchronous cache read** — a hot-path fetch would perturb latency. And `intraday_anchors_enabled` is still **OFF**; enabling it (needed for S&R-R:R) is its own soak.
3. **Regime needs live NIFTY index-historical access** (T4, deferred) before it can feed the Context layer live — verify in a Monday token-fresh window.
4. **The watchlist is a NEW stateful overnight store** — it must **not** rehydrate stale next-morning (the FIX-046 class of bug); persistence + expiry need careful design + a test.
5. **Idempotency** — a next-morning re-emitted PB-01 signal must not double-enter; reuse the webhook dedup + one-reservation-one-place + a **watchlist claim key** (idempotency is behavioral, not schema-enforced — Phase-0 risk #4).
6. **Comparison honesty** — PB-01 has no live twin; promotion evidence is its **own** would-be track record, not OLD-vs-NEW. Don't over-claim.

**Open questions (for Web Claude / ChatGPT / Rama):**
- **Q1 (playbook rules).** What EXACTLY are PB-01's gates (confirmation, valid pullback, S&R-based R:R, strong-HTF) and their thresholds? *This is the decision content — needs Rama/ChatGPT to specify.*
- **Q2 (score weights).** Playbook 40 / Context 40 / Execution 20 is the target — what are the **sub-weights within each layer**, and how is the Execution seed (8 steps → 100) re-scaled into the 20?
- **Q3 (HTF source).** Where does "strong-HTF" come from — the S&R `TF_ROLE` swings (30m PRIMARY / 1h MAJOR), the regime, or a new HTF fetch?
- **Q4 (regime consumption).** Does the Context layer use regime as a **multiplier** (`preference_multiplier`, already computed) or as **gate inputs** (`extreme_flag`) — or both?
- **Q5 (watchlist entry rule).** "Next-morning 5-min retest" — retest of WHICH level (prior-day close? the S&R anchor? the Chartink trigger price?), and what is the entry window (first N minutes)?
- **Q6 (flag granularity).** One master `v3_chain_mode`, or a per-playbook go-live flag? *(Recommend per-playbook once >1 playbook exists.)*

---

## ACCEPTANCE

Plan complete; TASK-0 inventory + P1-P10 source-verified against `main`@`c1ad82e` (file:line cited). **No code, no config, no VM change; deploy ledger UNCHANGED (nothing built).** The headline is the inventory: **the plumbing is built and OFF; the decision content (playbook + gates + 3-layer score) and PB-01's overnight front-end are the genuine remaining build**, and the chain threads through the **existing `_process_one` seam** (R2 — no parallel processor). **Ready for Web Claude + ChatGPT + Rama review**; after approval a separate instruction authorizes the build.

*Method note: source read directly (signal_processor, secondary_screener, hard_gate, quality_scorer, portfolio_allocator, allocation/models, regime/runner, strategies/schema, system_config, main.py, v3_shadow_soak_report) + the Phase-0 and Step-4/6 plan docs; no parallel agent fan-out, per the standing rule.*
