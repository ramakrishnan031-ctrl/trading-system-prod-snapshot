# E4/W10 — did the cost double-count ever change a daily-loss outcome? (READ-ONLY, 19-Jul-2026)

**Question:** turn *"the E4/W10 posture shifts the daily-loss input by Σcosts"* into *"the shift would have changed the outcome on N of 23 book days."*

**Answer up front: N = 0.** But the number that matters — the threshold — was wrong in the decision file, and establishing it correctly is most of this report. **The corrected threshold is ~Rs 300/day, not the Rs 25,000 the file stated.** N is still 0, by a wide margin, on the corrected threshold.

**Deploy state:** PC == origin == VM bare == `5a83a46`; tag `deploy-19jul-consecutive-losses` → `d271525`; schema v44. System DOWN; book flat. All queries used the `mode=ro` preserved snapshot; **no `scripts/*.py --db` was run** (the §0 rule). Live DB proven untouched (end of report).

---

## A. The production daily-loss configuration — established from config + code, not memory

### A1. One source, two enforcement points, no absolute

| fact | evidence |
|---|---|
| The daily-loss limit is **`daily_loss_limit_pct = 0.03`** (3% of capital) | `config/system_config.yaml:193` — *"SOLE daily-loss authority (3% of capital), PERMANENT"* |
| There is **no absolute mechanism** | `config/system_config.yaml:131-133` — *"BUILD 1 (#1, 24-Jun): absolute `daily_loss_limit` (was ₹300) DELETED. `daily_loss_limit_pct` … is the SOLE daily-loss authority."* |
| Both halves read the **same** key | `main.py:2231` `FundManager(daily_loss_limit_pct=app_config.system.risk.daily_loss_limit_pct)` and `main.py:2369` `RiskEngine(daily_loss_limit_pct=risk_cfg.daily_loss_limit_pct)` — one field, `app_config.system.risk.daily_loss_limit_pct` |
| Both apply it as **pct × current capital**, realized-only | pre-trade gate `risk_engine.py:569` `limit = self._daily_loss_pct * snap.total` (enforces realized-only while `daily_loss_include_unrealized: false`, `config:200`); post-close breach `fund_manager.py:1280` `loss_limit = self._daily_loss_limit_pct * self._total` |

So the "dual mechanism" is **two enforcement points of one limit** (a pre-trade gate that blocks new entries, and a post-close breach that arms the kill-switch), **not two limits with different keys, and neither absolute.** This **confirms batch 2's single-source conclusion** and refutes the "different config keys / absolute Rs 10,000" framing.

### A2. The capital base = broker margin ≈ Rs 10,000

- `fund_manager.py:440` `self._total = broker_balance` (live mode; `main.py:2241` comment: *"live uses broker margins"*).
- Ledger `INIT`/`SYNC` rows show the broker-synced balance across the book: **Rs 9,995.50 (16-Jun), 9,994.90 (17-Jun), 10,039.60 (18-Jun), 10,000.00 (06-Jul)** — i.e. **~Rs 9,995–10,040**, roughly constant.
- **The `Rs 1,000,000` INIT on 12-Jun is the pre-sync fixture seed** (the first two boots, before the first broker sync), exactly the fixture value the brief warned about. It is not the operating capital.

### A3. The resulting threshold ≈ Rs 300/day

3% × ~Rs 10,000 = **Rs 299.85–301.20 ≈ Rs 300/day**. The threshold is *moving* (`self._total` shrinks as realised losses accrue: `fund_manager.py:1259` `self._total += pnl`), but on this book the daily P&L is ~0.5% of capital, so the threshold moves **< Rs 2 within a day** — negligible. The figures below use ~Rs 300; the moving version is within Rs 2 and changes nothing.

### A4. This contradicts the decision file — corrected in this commit

`docs/decisions/01_e4_w10_pnl_contract.md` stated *"pre-trade pct-gate = 5% ≈ Rs 25,000, post-close FundManager absolute = Rs 10,000."* **All of that is wrong**: it is 3% (not 5%/2%), single-source (not two keys), pct-relative (no absolute), and **Rs 300 (not Rs 25,000)** — the Rs 25,000 implies a Rs 500,000 book (the fixture), ~50× the real capital. The values were fixture constructor literals mis-carried via the **stale `dual-daily-loss-mechanism` memory (35 days old)**, which still lists the deleted absolute `capital.daily_loss_limit`. Both the decision file and that memory are corrected as part of this batch.

---

## B. The computation — N = 0

### B0. The deployed reader IS Option B (proven from the data)

`reset_daily_pnl` writes a `RESET_PNL` row of `−get_daily_realized_net_pnl()`. On all **22** reset days that row equals **−(Σpnl_delta − Σcosts)**, i.e. **−net_B** (e.g. 07-07: reset `+54.19` = −(−54.19); 06-17: `−40.48`). So the deployed reader returns **Σpnl_delta − Σcosts (Option B, the double-subtract)**; the E4/W10 fix makes it **Σpnl_delta (Option A)**. The two differ by same-day **Σcosts**.

### B1–B3. Per book day: both readings vs the ~Rs 300 threshold

Reconstructed from `fm_ledger` `RELEASE_USED` rows (the only rows carrying `pnl_delta`/`costs`), respecting the nightly reset. `net_A = Σpnl_delta`; `net_B = Σpnl_delta − Σcosts`; `worst_cumB` = the worst *intraday running-cumulative* under Option B (what the check would actually see). Breach if the reading ≤ −Rs 300. **The loss days, worst first:**

| date | net_A | net_B | worst intraday cum_B | margin to −Rs 300 | breach either reading? |
|---|---:|---:|---:|---:|---|
| 2026-07-07 | −48.63 | −54.19 | **−56.47** | **Rs 243.5** | no |
| 2026-07-08 | −33.08 | −36.05 | −36.05 | Rs 264 | no |
| 2026-06-24 | −29.30 | −30.84 | −31.02 | Rs 269 | no |
| 2026-07-14 | −14.06 | −17.58 | −26.05 | Rs 274 | no |
| 2026-07-09 | −10.32 | −15.20 | −25.37 | Rs 275 | no |
| 2026-06-16 | −21.45 | −21.45 | −21.45 | Rs 279 | no |
| 2026-07-03 | −16.07 | −19.62 | −20.42 | Rs 280 | no |
| 2026-07-13 | −14.54 | −18.26 | — | Rs 282 | no |
| … 15 other days | all shallower (best day +47.9) | | | > Rs 282 | no |

**N = 0 of 23 days on the Rs 300 threshold** (and there is only one threshold — §A). No day, under either reading, at its worst intraday point, comes within **Rs 243** of the limit. For the double-count to flip an outcome a day's true-net loss would have to land in the window `(−300, −300 + Σcosts]` ≈ `(−300, −294.4]`; the worst the book ever reached is **−56.47**.

### B4. Is N = 0 structural, or an artefact of a tiny book?

**Structural, with ~5.3× headroom — but not robust to D1.** The worst day (07-07) reached **19% of the daily-loss limit** (−56.47 / −300). The *difference between the two readings* (max Σcosts Rs 5.56/day) is **1.9% of the threshold**.

Scaling if D1 raises sizing k× (positions ~k×, so daily losses and Σcosts both ~k×; threshold fixed at 3% × capital):

| sizing | worst intraday cum_B | margin to −Rs 300 | N |
|---|---:|---:|---|
| 1× (today) | −56.47 | Rs 243 | 0 |
| ~4× | ≈ −226 | ≈ Rs 74 | 0 |
| ~5.3× | ≈ −300 | ≈ 0 | day breaches under **both** readings |

So raising sizing tightens the margin, but the double-count only becomes *decisive* in a razor-thin regime: a day whose **true-net** loss lands within a few rupees of the threshold while the double-count pushes it over. At the point sizing is large enough for that, the day is essentially breaching on its own merits under both readings. **The double-count is not capable of being the deciding factor on any book the record has seen, and only becomes so in a measure-zero coincidence even at 5× sizing.** (Topping up capital raises the threshold and makes N=0 more robust, not less.)

### B6. The 36 pre-fix `costs = 0` rows do not distort N

Three whole days (15-, 16-, 19-Jun) have Σcosts = 0, so `net_A = net_B` there (verified: 16-Jun `worst_cumA = worst_cumB = −21.45`). Per `e4-w10-done-17jul`, ~36 individual `RELEASE_USED` rows were written gross (`costs = 0` passed by RMS/CHECK1 closes). Their effect is to make Option B **less** divergent from Option A (fewer costs to subtract), so they cannot manufacture a straddle. And the margin (Rs 243) is ~40× the largest daily Σcosts, so no plausible cost correction moves N off 0.

**N is reported; the posture call (adopt Option A vs keep Option B) stays Rama's.** This batch does not conclude it.

---

## C. Triage — which of the ten decisions can be settled from existing data

Classification of each decision's *cheapest* settlement path. Couplings noted; no computation done here beyond E4/W10.

| # | Decision | Cheapest settlement | Class |
|---|---|---|---|
| 01 | E4/W10 | the per-day computation above | **COMPUTED this batch (N=0)** → residual is **NEEDS RAMA** (posture + manual-flatten precondition) |
| 04 | D3 min_pass | out-of-sample 1-min backtest (is the band inversion stable beyond 06-19…07-13?) | **COMPUTABLE NOW** — the harness (`ms4_fullrange_study`) and 1-min data exist; live transfer then NEEDS RUNNING (and collides with FREEZE) |
| 05 | D4 exits | out-of-sample exit backtest (is the BE-after-0.5R spike stable?) | **COMPUTABLE NOW** — same harness + data |
| 06 | PerformanceAllocator | reachability algebra: can `perf_weight ≠ 1` ever change qty while concentration binds 100%? | **COMPUTED 19-Jul: YES, 233/298 (78%)** — the label held (`candle_retention_and_perfallocator_feasibility_19jul2026.md`) |
| 02 | D1 concentration | a positive-expectancy baseline at larger size | **NEEDS SYSTEM RUNNING** (re-soak after D3); partial COMPUTABLE (the >Rs990 names' 1-min backtest) |
| 03 | D2 strategy mix | per-strategy edge on a corrected scorer + regime attribution | **BLOCKED** (Q10 token) + NEEDS RUNNING; today's per-strategy P&L exists but is confounded |
| 07 | Regime | Q10 to statistical power | **BLOCKED** (Kite token for the backfill) + **NEEDS RUNNING** (~2.2 months) |
| 08 | Freeze min_pass | a priority call (protect the regime measurement vs act on D3) | **NEEDS RAMA** — downstream of Regime + D3, not evidence |
| 09 | Prune-retention | Rama's intent on whether rejection-composition is a recurring need | **NEEDS RAMA** |
| 10 | Throttle admission | does the composed score rank? (the same question as D3) | **BLOCKED on D3** |

### C2. Where yesterday's "what would settle it" now looks under-specified

- **E4/W10 (the direct lesson):** its stated settlement — *"a per-day ledger computation against Rs 10,000 / Rs 25,000"* — silently assumed the thresholds. They were wrong. **A settlement that computes against a threshold must establish that threshold from code first.** This batch did, and the threshold moved ~50×.
- **D1 and D3** carry the same shape: their settlements are stated against config-dependent quantities (the concentration cap; `min_pass_score`). If acted on, the current production value must be read from code first — not from a decision file or memory — exactly as the daily-loss threshold had to be here.
- **D3 / D4 "COMPUTABLE NOW"** is real but bounded: the only 1-min data is ~2 months, single-regime (06-19…07-13). "Out-of-sample" means fresh data the system has not yet produced, so the *stability* check is partly forward-looking, not purely historical.

---

## PROOF OF READ-ONLY

- Every query ran on `file:/home/ubuntu/preserved/signal_census_19jul2026/trading_system_snapshot_20260719.db?mode=ro`. No application code, no `scripts/*.py --db`.
- **Live DB before:** sha256 `6df0c09a…`, mtime `2026-07-19 11:14:29`, size `89,968,640`. **After: identical** (recorded at commit).

*Docs-only. No code, config, schema or flag changed. The E4/W10 posture call, and every other decision, remains Rama's.*
