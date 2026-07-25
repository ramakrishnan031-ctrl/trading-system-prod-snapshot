# Forward-shadow scoping pass

**READ-ONLY.** 2026-07-24 (IST). Deployed HEAD: `bc75406`. DB `mode=ro&immutable=1`; VM logs/JSONL read-only. **`scripts/forward_shadow_record.py` was NEVER run — output and code read only** (§2.1, the master constraint). No code/config/flag change; signal path untouched; service not restarted. This pass establishes whether the evidence exists and is sound — **not** what it says (§6.4: no performance numbers).

Companion to `exit_policy_netting`/`trailing_stop_never_fired` (exits thread now CLOSED — separate memory).

---

## VERDICT (the three questions, in order)

> **1. Is it recording correctly? YES, and — the check that matters — UNCONTAMINATED.** 9 dates (13→24-Jul), each PROVEN to have run by a `SUCCESS` row in `cron_heartbeat` (not inferred from a file); append-only holds (**dup=0** on every date, **0 malformed lines** across 17,454); each date was computed **same-day** under **exactly one** `git_commit`, chronologically increasing — no past day was ever backfilled or recomputed. Caveats, none fatal: **13-Jul is a manual off-cron seed** (23:41, pre-deploy commit — in-sample, not clean OOS); **17-Jul is absent because the live system produced 0 signals that day** (an upstream service outage — the VM's crons were up), not a recorder fault; **16-Jul is anomalously thin** (181 vs ~1,600–2,500).
> **2. What is one record? One SCORED SIGNAL of a day** (`screener_results ⋈ signals`, score not null). It pairs the **live** score + admit/reject decision with an **M-S4 re-computed score that is COMPUTED-ONLY, never used for any decision**, plus a simulated true-path `sim_R`; `realized_pnl` is filled only if the signal actually traded. (Read from the writer, not the field names: `side` is inferred from the strategy *name*; the record recomputes the *score* only — not M-S4's full admission pipeline.)
> **3. Is there enough to say anything? No — not remotely.** 8 clean OOS days and ~14k scored signals, but **only 45 realized OOS trades** (~5.6/day). Days and signals are not sample size; realized would-be-trades are, and 45 is far too few to distinguish anything. A powered sample is weeks-to-months out.

---

## §1 — Exits thread CLOSED (recorded first, per instruction)

Agreed by Rama + ChatGPT, 24-Jul. **No further exit test / mechanism / Cover-Order / order-modification work; the trailing stop, breakeven manager, and structure-exit manager all stay exactly as-is.** Reason (MEASURED): 8 policies on 115 real trades with real costs — current −0.100R, best alt −0.076R, worst −0.144R; none profitable; the family spans 0.068R against a 0.100R deficit. **Reopen TRIGGER (not a note):** exits are revisited **only after the entry logic (M-S4) materially changes**, then by re-deriving the study on the new trade population ("ENTRIES FIXED — re-derive post-M-S4"). Also recorded, not actioned: the `broker_order_id` column bug; the maintained-but-never-wired module pattern (worth a SYSTEM_MAP line); the daily-report cron heartbeat gap; the four unmatched `predeploy-*` backup files. (Full detail in memory + the exit reports.)

## §3 — Is it recording? (measured)

| date | records | uniq sig | dup | computed | commit | note |
|---|---:|---:|---:|---|---|---|
| 2026-07-13 | 3467 | 3467 | 0 | 23:39–23:41 | `d23239c` | **manual seed (off-cron, pre-deploy) — in-sample** |
| 2026-07-14 | 1644 | 1644 | 0 | 18:15 | `bb9b1e7a` | OOS cron |
| 2026-07-15 | 2535 | 2535 | 0 | 18:15 | `2dc69d5` | OOS cron |
| 2026-07-16 | 181 | 181 | 0 | 18:15 | `c9fb298b` | OOS cron — **thin (see below)** |
| 2026-07-17 | — | — | — | — | — | **absent: 0 signals live (service outage); crons up** |
| 2026-07-20 | 1028 | 1028 | 0 | 18:15 | `056963e` | OOS cron |
| 2026-07-21 | 2283 | 2283 | 0 | 18:15 | `8fdd286c` | OOS cron |
| 2026-07-22 | 2302 | 2302 | 0 | 18:15 | `4585bd2` | OOS cron |
| 2026-07-23 | 2159 | 2159 | 0 | 18:15 | `56e83169` | OOS cron |
| 2026-07-24 | 1855 | 1855 | 0 | 18:15 | `bc754063` | OOS cron (deployed HEAD) |

- **§3.3 (ran, not inferred):** `cron_heartbeat` holds a `SUCCESS` row per date whose `message` `wrote=N` matches each record count exactly (24-Jul wrote=1855, 16-Jul wrote=181, 13-Jul wrote=3467). Measured, not from file presence.
- **§3.4 (heartbeat):** the writer records a `SUCCESS` heartbeat after each write (`:237-241`) and a CRITICAL sentinel on crash (`:243-254`) — fail-loud. **One blind spot:** a day with 0 new signals returns early (`:162-163`) writing **no** heartbeat, so "nothing to record" is indistinguishable from "cron didn't fire" in the heartbeat table. That is exactly the 17-Jul case.
- **§3.5 (varies by day):** yes — distinct counts, unique signals (uniqSig==n, no within-day dups), and commits per day. Not a re-written constant.
- **§3.6 (integrity):** 0 malformed JSON across 17,454 lines; dup=0.
- **§3.7 (contamination — the decisive check): CLEAN.** Every date computed same-day under one chronological commit; no duplicate `(date,signal_id)`; no past day rewritten. Append-only holds. **The seed exception (13-Jul):** computed 23:41 off-cron under the pre-deploy `d23239c` — treat it as in-sample and exclude from OOS analysis; the genuine OOS cron days begin 14-Jul.
- **16-Jul thin (181):** the service had a mid-session restart that day (a `SmartTgtManager stopped` at 10:54 in the logs), so only a partial day of signals was scored — a coverage note, not a corruption.

**§3 gate: PASSES.** The dataset is real, append-only, and uncontaminated. Proceeding.

## §4 — What is in a record (from the writer, not the names)

Source: `scripts/forward_shadow_record.py:143-224`. Population SQL (`:143-150`): `screener_results s JOIN signals sig … WHERE date(s.ts)=? AND s.score IS NOT NULL AND s.step_results IS NOT NULL` → **one record per scored signal of the day.** Fields, read from the code that writes them:

- `old_score` / `old_band` — the **live** score the system actually computed (`s.score`).
- `decision` / `reject_reason` — the **live** admit/reject (`sig.status` / `sig.rejection_reason`).
- `ms4_score` / `ms4_band` — the M-S4-recomputed score (`ms4_score(...)`), docstring `:5-6,:11`: **"COMPUTED, never used for any decision."**
- `ms4_stats_ok` — whether ATR/RSI/vol daily-stats were available for the recompute (else fail-safe values).
- `sim_R` — `simulate_true_path(entry, direction, path)` on the day's 1-min candles from the trigger minute onward (same SL/TGT/cost as the §3/Phase-2 audit).
- `realized_pnl` — `trades.net_pnl` **only if the signal traded** (`trade_id` present); else null.
- provenance: `method_version` (`fs-v1`), `git_commit`, `scoring_weights_sha`, `system_config_sha`.
- ⚠️ **`side` is inferred from the strategy NAME** (`_direction` `:82-84`, keyword match on short/breakdown/fade/rejection) — a name-inference, not the authoritative signal direction (§4.2).

- **§4.3 (format changed?):** `method_version = fs-v1` throughout, and `scoring_weights_sha` is **constant** (`6a0bd48…`) across 13→24-Jul, so the scorer that drives `ms4_score` is stable and the days are comparable. `git_commit` and `system_config_sha` vary with deploys, but no scoring-weight change — no `fs-v` roll was triggered.
- **§4.4 (deliberately not captured):** (a) M-S4's **full admission** — only the *score* is recomputed, not the throttle/circuit/sizing/quote gates under M-S4, so "would M-S4 *trade* it" is not in the data; (b) pre-score signals — anything rejected before scoring (dedup, pre-gates) never enters the population; (c) true intra-candle ordering — `sim_R` rests on 1-min OHLC and inherits the adverse/favourable-first ambiguity.

## §5 — What is it comparing? (and a premise correction)

- **§5.1/5.2 (fs-v1):** it compares, on the **same signal**, the live `old_score`→decision against the counterfactual `ms4_score`. The two diverge **at the scoring step only** (`ms4_score` recompute); admission and outcome are the live path's. So fs-v1 is an M-S4 **scoring** shadow — its thesis is `min_pass_score` (D3/D4), not the V3 entry chain.
- **§5.3 (same population?): YES.** The records ARE the day's `screener_results` — exactly the signals the live path scored. `decision` is the live decision. It is a genuine same-population counterfactual, not two mismatched populations.
- **⚠️ Premise correction (source wins, standing rule 7):** the instruction frames "the shadow" as existing to test PB-01. It does not — there are **three** distinct shadow artifacts:
  - **`forward_shadow_fs-v1.jsonl`** — the M-S4 *score* shadow (this pass's subject; the protected OOS evidence).
  - **`would_be.jsonl`** (`v3_chain/runner.py:150,268`) — the **V3 chain vs live** shadow: 281 records (15→24-Jul), `v3_verdict` = **223 WOULD_REJECT_RR / 54 WOULD_REJECT_HTF / 4 WOULD_PASS_GATES**, every record links to a live outcome. **`regime` is null on 281/281 → the regime module is inert** (regime.enabled=false), as designed.
  - **`pb01_would_be.jsonl`** (`v3_chain/pb01_runner.py:242`) — **does not exist → PB-01 has produced 0 would-be records** over the period. PB-01 (the pullback module) is **present in code but never reached** — a fourth instance of this week's "present-but-inert" pattern (the pullback *strategies* `first_pullback_*` do appear in the other shadows' populations; the PB-01 *module* has emitted nothing).
- **§5.5 (modules exercised vs inert):** in `would_be.jsonl` the SR module is exercised (support/resistance + `sr_confidence_class` populated), the RR and HTF gates are exercised (they drive 277 of 281 rejections), **regime is inert** (null throughout), and **PB-01 is inert** (no file).

## §6 — Is there enough to say anything? (volume + power only — no performance)

- **Days:** 8 clean OOS cron days (14,15,16,20,21,22,23,24-Jul; 13-Jul seed excluded; 17-Jul lost to the service outage).
- **Scored signals (OOS):** 13,987.
- **Simulatable (`sim_R` not null, OOS):** 13,369 — but these are overwhelmingly *rejected* signals (a counterfactual outcome is computed for every scored signal regardless of decision). **This is not a would-be-trade count.**
- **§6.1 — the number that matters — realized OOS trades: 45** (14-Jul 9 · 15-Jul 7 · 16-Jul 3 · 20-Jul 4 · 21-Jul 5 · 22-Jul 6 · 23-Jul 6 · 24-Jul 5). The would-be-trade population *under M-S4* (signals a lower threshold would newly admit and that would clear the other gates) is **not directly captured** — the shadow recomputes the score only — so realized trades (45) are the binding, ground-truth sample.
- **§6.2 (anti-vacuity):** it is not zero — there is a nascent sample — but 45 realized trades over 8 days is tiny. The volume of *scored signals* (14k) is not the sample size for any outcome question.
- **§6.3 (power, plainly):** **not remotely enough to distinguish anything yet.** Bound by realized would-be-trades, not days or signals. At ~5.6 trades/day, reaching even ~100 realized would-be-trades is ~3–4 more trading weeks; a sample that could separate a `min_pass`/scoring effect from noise (given the book's ~38–39% win rate and the earlier D3/D4 power work) is **weeks-to-months** out, and every service-down day (like 17-Jul) is a lost OOS day. This matches the standing D4 finding: the constraint is power, and power accrues only while the system runs.

## Evidence appendix (reproduce; all read-only)

- Files: `data_store/v3/forward_shadow_fs-v1.jsonl` (17,454 lines), `would_be.jsonl` (281), `pb01_would_be.jsonl` (absent); `data_store/cron_marks/forward_shadow_record.done` = `0 2026-07-24T18:16:06`.
- Aggregations: per-date count/uniq/dup/computed_at/commit via `python3` over the JSONL (0 bad JSON); decision distribution and realized/sim_R non-null counts (volume only).
- Cron: `cron_heartbeat` rows `id 1956/2065/2177/2292/2532/2646/2760/2875/2990`, all `SUCCESS`, `wrote=` matching record counts. 17-Jul: `trades=0, screener_results=0, 115 other-cron heartbeats`.
- Writer: `scripts/forward_shadow_record.py:143-150` (population), `:82-84` (name-inferred side), `:5-6,:11` (ms4 computed-only), `:162-163` (0-signal early return, no heartbeat), `:237-254` (heartbeat + crash sentinel). V3: `v3_chain/runner.py:150,268`; `v3_chain/pb01_runner.py:242`.
- Deployed `bc75406`; `scripts/forward_shadow_record.py` NOT run.
