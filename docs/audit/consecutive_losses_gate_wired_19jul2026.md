# THE CONSECUTIVE-LOSSES GATE (RE10), WIRED — and the measuring instrument, fixed

**Date:** 19-Jul-2026 (Sunday, IST, off-market; system DOWN since Fri 17-Jul, book flat)
**Basis:** `1fc95c1` (tag `deploy-19jul-q9-live-seed` → `abf276a`), schema v44
**Scope:** TEST-ONLY. No production file changed.
**Closes:** Q9 layer 14 — the last unwired layer of the programme.

---

## 0. TL;DR

**§A — the ruler was bent, by 18%.** The NTP drift assertion was not a rare flake: measured over
100,000 trials, `drift_sec >= 1.0` failed **17.94%** of the time and `>= 3.0` **18.32%**. With two
such tests, roughly **a third of all suite runs** carried at least one spurious failure. Fixed at
the root, and the fix is proven to still catch a real breach.

**§B — layer 14 is wired, 13 tests, and the headline is that there is no counter.** The streak is
recomputed from the trades table on every `approve()`. That single fact answers all three
questions the brief asked: what resets it, whether it survives a restart, and why production shows
streaks longer than the threshold with no bypass involved.

**§B9 — REACHABLE, correctly configured, and it has never once fired.** The precondition has been
met on **3 of 21 trading days**, yet there are **0 `REJECTED_CONSECUTIVE_LOSSES` in 32,928
signals**. That is not a contradiction and not a defect — see §7.

**§C — nothing blocks Monday's 08:15 boot.** One stale lock was found on the VM; it was **my own
artefact** from running the instance-lock tests there, it could not have blocked a boot, and it is
gone.

---

## 1. §A — THE MEASURING INSTRUMENT

### 1.1 Root cause (not a looser assertion)

`utils/startup_checks.py:534-548`:

```python
ntp_utc   = fetcher(ntp_host)   # our stub: time.time() at T0, + offset
local_utc = _time_mod.time()    # at T1, strictly after T0
drift     = abs(local_utc - ntp_utc)
```

The measured drift is **`offset - elapsed`**, where `elapsed = T1 - T0 > 0`. It is therefore
**always ≤ the injected offset**, and `assert drift_sec >= offset` can only hold when the error
terms happen to vanish. The test was asserting something the code cannot guarantee.

**Two error terms, both measured on this tree rather than assumed:**

| Term | Magnitude | Note |
|---|---|---|
| float64 representation | **2.384e-07** | one ULP; at epoch ~1.784e9 the value is in [2³⁰, 2³¹) so ULP = 2⁻²². The observed CI failure was a deficit of *exactly one ULP* |
| elapsed between the two clock reads | p99.9 **7.2e-07**, max **9.1e-06** | over 400k samples |

> **⚠️ This corrects the brief's premise.** The brief asked for a tolerance justified "in terms of
> the ULP at this magnitude". The ULP explains why an **exact comparison** is unsafe, but it is
> **not the dominant term** — the physical elapsed time is ~38× larger, so the tolerance has to be
> sized by the physical term. Sizing it by the ULP (say 1e-6) would have left the test flaky under
> any scheduler jitter.

### 1.2 The tolerance, and why 50 ms

`_CLOCK_TOL = 0.05`, with the derivation recorded in the test source:

- ~**2e5×** the ULP and ~**5.5e3×** the measured worst case — so both error terms are absorbed with
  enormous margin;
- ≈ **3 Windows scheduler quanta** (~15.6 ms). A single preemption landing between those two
  adjacent clock reads dwarfs everything float-related and is the only realistic way this still
  flakes. *This is what actually sets the number;*
- **20× BELOW** the 1.0 s margin from each injected offset to the nearest decision boundary
  (1.0 vs warn 2.0; 3.0 vs warn 2.0 / block 5.0), so it cannot blur a threshold.

### 1.3 Sweep (§A2) — the class is contained

The defect shape is *an exact `>=` on a quantity built as (large epoch + small delta)*.

| Site | Offset vs bound | Verdict |
|---|---|---|
| `test_fix129_ntp_check.py:62` `>= 1.0` | offset 1.0 — **exactly at the bound** | 🔴 fixed |
| `test_fix129_ntp_check.py:74` `>= 3.0` | offset 3.0 — **exactly at the bound** | 🔴 fixed |
| `:86` `>= 5.0` | offset **6.0** — a full second of slack | ✅ safe, untouched |
| `:50` `< 1.0` | offset 0.0, and both errors push *down* | ✅ safe, untouched |
| `:101` `== 0.0` | the skipped path; drift is hard-set, not computed | ✅ not applicable |

`test_rate_limiter.py` (×6) and `test_startup_checks.py:698` also compare durations with `>=`, but
they measure **real sleeps** with deliberate slack (`waited ~50ms, assert >= 0.04`) — a different
shape, not epoch-derived. **Left alone; recorded rather than swept.**

### 1.4 Proven to bite BOTH ways (§A3)

| Plant | Result |
|---|---|
| a genuine 0.5 s drift error (offset 1.0 → 1.5, i.e. **10× the tolerance**) | **FAILS**: `assert 1.499999761581421 == 1.0 ± 0.05`, naming both values |
| `_CLOCK_TOL` widened to 2.0 | **FAILS** the guard: *"_CLOCK_TOL=2.0 is within 10x of the 1.0s margin to the nearest threshold — it could hide a misclassification"* |

Two permanent guards now hold the tolerance honest: one bounds the tolerance against the margin,
the other proves `block_sec + 2×tol` still blocks.

### 1.5 The flake is gone (§A4)

A single green run is no evidence against a coin flip, so:

| Assertion | Failures / 100,000 trials | Worst deficit |
|---|---|---|
| OLD `drift_sec >= 1.0` | **17,940 (17.94%)** | 1.43e-06 |
| OLD `drift_sec >= 3.0` | **18,324 (18.32%)** | 1.43e-06 |
| NEW `approx(abs=0.05)` | **0** | — |

Plus **40 consecutive pytest invocations, 0 failures**. Under the old regime the probability of 40
clean runs is ~0.82⁸⁰ ≈ **1e-7**.

**This retro-explains the discarded regression pair from the M-C1 batch**: at ~18% per test, base
catching one of the pair and mine catching the other is the expected outcome, not a coincidence.

---

## 2. §B1 — THE INVESTIGATION: there is no counter

```
capital/risk_engine.py:250   pnls   = store.recent_trade_pnls(max_consec + 1, today=today)
capital/risk_engine.py:251   consec = _count_trailing_losses(pnls)
capital/risk_engine.py:552   checks_run.append("CONSECUTIVE_LOSSES")
capital/risk_engine.py:553   if consec >= max_consec:  reject("CONSECUTIVE_LOSSES", ...)
core/state_store.py:851      recent_trade_pnls(n, today)  — the ONLY data source
capital/risk_engine.py:674   _count_trailing_losses — loss is net_pnl < -1e-6 (RE10)
```

**The streak is RECOMPUTED on every `approve()`**, never incremented and never held in memory.
`core/schema.sql:459-460` confirms it from the other side: the stored `consecutive_losses` column
"was never written, and risk_engine recomputes it". Everything below follows from that.

**(a) What resets it — two things, both proven, not described:**

1. **Any non-loss close.** `_count_trailing_losses` counts only the *trailing* run. A win breaks it;
   so does a breakeven, since RE10 defines a loss as `net_pnl < -1e-6`.
2. **A new day.** FIX-183 scopes the query to `SUBSTR(exit_time,1,10) = today`. Its own comment
   records the outage that forced it: *a cross-day streak was a **deadlock** — it blocks entries,
   and breaking it needs a winning trade, which the block makes impossible.*

> **Which system does Rama own?** The answer is the *good* one: the halt is **not** for the rest of
> the day. **One winning close lifts it immediately**, and it lifts on its own at the next day
> boundary. Proven by `test_a_winning_close_resets_the_streak_to_zero` and
> `test_after_a_win_breaks_the_streak_trading_resumes`.

**(b) The rejection status** is `REJECTED_CONSECUTIVE_LOSSES`, asserted specifically.

**(c) Does it survive a restart? YES — by construction.** Recomputed from the `trades` table, so
there is nothing in memory to lose. This is the same shape as batch 5's daily P&L (FIX-051).
**A restart is NOT a bypass.** Proven by `TestSurvivesRestart` (both the value and the gate).

**(d) Gates that could mask it** — RE5 order:

```
1 KILL_SWITCH   2 SIZING_VALID   3 CAPITAL   4 OPEN_POSITIONS   5 DAILY_TRADES
6 CONSECUTIVE_LOSSES   7 DAILY_LOSS   8 SECTOR_EXPOSURE   9 CONTRARY_POSITION   10 DUPLICATE_SYMBOL
```

---

## 3. §B2 — THE SCENARIO COLLISION (with the fixture's real values)

> **⚠️ The brief's premise here was inverted, and it matters.** It warned that the daily-loss limit
> sits **BELOW** the gate and must not fire first. In the RE5 order, **`DAILY_LOSS` is check 7 —
> AFTER `CONSECUTIVE_LOSSES` at 6** — so the daily-loss *gate* **cannot mask this gate at all**.
>
> The real hazard is different: the **post-close breach** (`fund_manager`, a separate mechanism
> with its own config key) arming a **SOFT KILL**, which then fires **`KILL_SWITCH` at check 1** and
> produces a rejection that superficially looks like the scenario worked.

**Measured fixture values** (`tests/integration/conftest.py`), not assumed:

| Setting | Value | Effect on the scenario |
|---|---|---|
| `PAPER_CAPITAL` | 500,000 | |
| `RiskEngine(max_consecutive_losses)` | **4** | the threshold; read off the engine, never hard-coded |
| `RiskEngine(max_daily_trades)` | **20** | vs 5 trades used ⇒ **DAILY_TRADES cannot mask** |
| `RiskEngine(max_open_positions)` | 2 | each trade is closed ⇒ open count returns to 0 |
| `RiskEngine(daily_loss_limit_pct)` | **0.05** → ₹25,000 | the pre-trade gate (check 7) |
| `FundManager(daily_loss_limit_pct)` | **0.02** → ₹10,000 | **the post-close breach — the real hazard** |

**The sizing had to change.** The shared `_drive_close` defaults to exit 410.0 = **−9,000 gross per
close**; four of those is −36,000 and blows through both limits. This batch drives exit **490.0 =
−1,000 gross**, so four closes ≈ **−4,2xx**: comfortably inside the ₹10,000 post-close limit and
nowhere near the ₹25,000 pre-trade one. *The brief's "~₹1,000 per close" figure was right in
magnitude but belonged to a different helper — verified against the fixture, as instructed.*

`test_the_loss_sizing_actually_stays_inside_both_daily_limits` **asserts** all of this rather than
trusting it, including `not kill_switch.is_active()` — so if the breach callback ever arms
mid-scenario, that test fails loudly instead of quietly invalidating the others.
`_assert_no_earlier_gate_can_mask()` re-checks all five earlier gates at **every** probe.

---

## 4. §B3–B6 — WHAT THE TESTS PROVE

`tests/integration/test_q9_consecutive_losses_wired.py` — **13 tests**.

| Test | Proves |
|---|---|
| `test_each_losing_close_advances_the_streak_by_exactly_one` | the streak reading moves 1→2→3→4 — the foundation every anti-vacuity assertion rests on |
| `test_the_loss_sizing_actually_stays_inside_both_daily_limits` | **the collision guard**, incl. the kill switch never arming |
| `test_a_winning_close_resets_the_streak_to_zero` | **B5 reset path, proven** |
| `test_a_loss_after_a_win_starts_a_fresh_streak_not_a_resumed_one` | the reset is real, not cosmetic — the next loss reads **1**, not 4 |
| `test_the_streak_is_scoped_to_today` | **FIX-183** — yesterday's losses do not count, and the rows still exist (so the test isn't proving itself by deleting data) |
| `test_at_the_threshold_the_next_signal_is_rejected_consecutive_losses` | **B3 POSITIVE** — `REJECTED_CONSECUTIVE_LOSSES` *specifically*, no multi-way accept |
| `test_at_threshold_minus_one_the_gate_does_not_fire` | **B4 NEGATIVE** — without it, a gate that rejects everything looks identical to one that works |
| `test_after_a_win_breaks_the_streak_trading_resumes` | the **operational** half of the reset: signals are accepted again |
| `TestSurvivesRestart` (×2) | **B1c** — the streak *and* the gate survive a restart |
| `TestParityAndReachability` (×3) | **B7** no mode branch · one data source · **B9** production algebra |

**Anti-vacuity (B6)** is asserted before every conclusion: the streak must **equal the threshold**
before a rejection is attributed to it — *"streak is 0, not 4 — a rejection here would be for some
other reason and the test would pass for the wrong one"*. The streak is read through the **same
production functions the gate uses**, never re-implemented.

---

## 5. §B7 — PARITY

Structural, as in every batch since 2: the streak reads the trades table and the threshold is one
config value — neither is mode-dependent. `test_the_gate_has_no_paper_or_live_branch` asserts on the
source so it fails the moment a mode branch appears, and
`test_the_streak_has_exactly_one_data_source` pins `recent_trade_pnls` to a single call site — the
same shared-source argument M-C1 rests on.

---

## 6. §B8 — PROVEN TO BITE

| Plant | Result |
|---|---|
| **1** — neuter the comparison (`consec >= max_consec + 100`) | **3 failures**, naming the gate and the count: *"with 4 consecutive losses (max=4) the signal terminated as 'PROCESSED' rather than REJECTED_CONSECUTIVE_LOSSES — the gate did not bind"* |
| **2** — `_count_trailing_losses` always returns 0 | **9 failures**, with the **anti-vacuity assertion catching it first**: *"streak is 0, not 4 — a rejection here would be for some other reason"* |

**`CapitalInvariantViolation` = 0 and `CapitalStateInconsistent` = 0** in both plants (measured by
grepping the run output) — production's own runtime guard never intervened, so the assertions are
what caught the break, exactly as required.

After restore: `capital/` and `orders/` show **0 modified files**; 13 passed.

---

## 7. ⭐ §B9 — REACHABILITY, measured against PRODUCTION

**Production config:** `max_consecutive_losses: 4`, `max_daily_trades: 10`, `daily_loss_limit_pct:
0.03`. Since `10 > 4`, DAILY_TRADES leaves room for the 5th entry attempt the gate needs — it is
**not** squeezed out the way batch 4's `qty_by_risk` was. `test_production_config_leaves_the_gate_reachable`
pins that relationship against the real config file.

**Empirical, from the live DB (read-only) — the streak per trading day:**

| Day | Max streak | Day total |
|---|---|---|
| 2026-06-24 | **5** | −29.30 |
| 2026-07-07 | **5** | −48.63 |
| 2026-07-08 | **6** | −33.08 |

**3 of 21 trading days (14%) reached or exceeded the threshold of 4.** So the precondition is
genuinely met in production, not merely conceivable.

**And yet: `REJECTED_CONSECUTIVE_LOSSES` = 0 across all 32,928 signals ever recorded.**

Two things had to be checked before concluding anything, because a streak of 6 against a threshold
of 4 looks like a bypass:

**(1) Is the gate bypassable?** No. On 2026-07-08 the streak reached 4 at exit **10:22:23**, but
**every one of the six trades had been ENTERED by 10:14:19** — all before the threshold was
reached. The gate is evaluated at **entry** while the streak only changes at **exit**, and with up
to 5 concurrent positions the streak can complete while several entries are already in flight.
Nothing was retro-blocked because nothing could be. **No bypass.**

**(2) Then why has it never fired?** Because by the time the streak completes, the day's entries
have already stopped for other reasons. On 2026-07-08 **all 63 signals arriving after 10:22:23 were
`SKIPPED_QUOTE_UNAVAILABLE`** — they died upstream of the risk engine, so the gate was never
consulted.

> **⚠️ CORRECTED 19-Jul-2026 (census `docs/audit/signal_mortality_census_19jul2026.md` claim 1).** The *reason* in (2) is **survivorship bias, not fact.** 2026-07-08 is in the **pre-09-Jul pruned era**, where `eod_cleanup.py:234` deletes `REJECTED_*` but keeps `SKIPPED_*`, so the surviving rows only *look* like "everything after 10:22 was skipped". On the unpruned accepted counts, acceptance **accelerated** after 10:22 (hourly 464→676→1,179→1,431→**1,462**, the day's busiest hour); the 63 skips are **1.21% of that day's 5,212 accepted — the 6th-lowest rate of 21 days.** Entries did **not** stop. **The VERDICT below (RE10 REACHABLE, never-binding) and point (1) (no bypass: entry-at-10:14 vs streak-at-exit-10:22 recomputation semantics) are UNAFFECTED and stand** — they rest on the gate's evaluation timing, verified independently, not on this day's quote history. The census in fact *strengthens* "reachable": the gate is consulted **~975×/day** (5,845 signals reach the risk engine in the complete era).

> **VERDICT: REACHABLE — live, correctly configured, precondition demonstrably met, but never yet
> the binding rejection.** This is a distinct category from batch 4's UNREACHABLE guards (dead by
> algebra or config). Nothing to fix; recorded so it is not mistaken for either a dead gate or a
> routinely-binding one.

---

## 8. §C — PRE-MONDAY VM CHECK (read-only)

| Check | Result |
|---|---|
| **C1** every module `main.py` imports | **94 modules, 0 failures** on the VM's deployed tree |
| `reports/signal_status.py` (new) + `daily_report.py` (changed) | **not on the boot path** — `main.py` imports neither; they are cron-only (16:05). They import cleanly anyway |
| **C2** stale instance lock | one found at `/tmp/trading-system.lock` — **my own artefact** (mtime Jul 19 **00:49**, from running the instance-lock tests on the VM). Named **PID 1491115, dead**; no process held the flock; port 5001 free ⇒ **would not have blocked a boot** (the OS flock is the authority, per `test_p2_restart_after_crash_is_not_blocked`). Removed, restoring the prior state |
| stray PID files | none |
| **C3** crontab | as expected. Pre-08:15 entries are all off-market housekeeping (backups 01:00–02:30, token delete 05:00, hourly disk monitor). **`15 8 * * 1-5 auto_refresh_token`** — the documented boot trigger — is present |
| service | `trading-system.service` **enabled**, `LoadState=loaded`, last `ExecMainStatus=0`; `token-watcher` enabled + active |
| token file | **absent**, as designed between the 05:00 delete and the 08:15 refresh |
| disk | 24G / 96G used (25%) |

**C4 — nothing found that would prevent a clean 08:15 boot.** Nothing was changed on the VM except
removing the lock file this session created.

---

## 9. HARNESS DEFECTS FOUND WHILE BUILDING (both recorded in source)

Planting and scenario design found **two more holes in the TESTS**, continuing the programme's
pattern:

1. **`_probe_signal_status` could settle on `"PASSED"`** — which is the **screening** verdict
   (`screening/secondary_screener.py:353,512`), reached **before** the risk engine rules. Any
   positive assertion on a specific rejection was therefore a **race**. It presented as a failure
   that **vanished when the test was run in isolation** — the signature of a race, not a logic
   error. Callers asserting a risk verdict now pass `ignore={"PASSED"}`; the default is unchanged.
2. **My own parity guard matched `"live"` inside `"deLIVEry"`** and failed on correct code. Now
   word-boundary matched. This is the same trap as the report fix's source guard firing on its own
   explanatory comment, and batch 4's tier multiplier — **a guard that is too broad is as useless as
   one that is too narrow.**
3. **⭐ The paper auto-fill raced my explicit fills — caught only by the FULL-SUITE regression.**
   The fixture default is `paper_auto_fill_delay_sec=0.05`: the adapter fills a placed order 50 ms
   later, **asynchronously**. These tests publish their own fills to control the exit price and
   therefore the **sign** of every close. Under full-suite load the 50 ms elapsed first, the adapter
   closed the trade at its own price, and the streak the module asserts on was wrong.

   It presented as **one attributable failure that passed in isolation, passed under `-k`
   selection, and passed across all 103 tests of `tests/integration`** — only the slower full run
   lost the race. Every other Q9 batch already sets `60.0`; this module had simply omitted it.

   > **This is why "STOP on any NEW failure" is the right rule.** Re-running until it looked green
   > would have "worked" — the test passes in isolation every time. The failure was real, the cause
   > was in my harness, and the full-suite regression was the only thing that could see it.

**Rule #4 was honoured:** `_drive_close` and `_probe_signal_status` were **generalised, not
duplicated**, with defaults that leave batches 1 and 5 behaviourally identical (verified: 14 passed
before this batch's tests were written).

---

## 10. REGRESSION + DEPLOY

### 10.1 Regression — same window, no midnight crossing

Both runs **Sun 19-Jul, 01:37–02:21 IST** — deliberately started early enough that a ~13-minute run
could not roll into Monday ([the fifth permanent rule](q9_coverage_matrix_final_18jul2026.md)).
Base taken with the standing rule: `git checkout 1fc95c1 -- <files>` in the **MAIN TREE**, the new
file moved aside, a stale `.pyc` found and removed, `git stash` unused (stash list verified empty).

**⭐ The prediction was stated BEFORE the run** (the brief's requirement): base 5030 collected with
**14–16** failures — 16 minus however the NTP coin flip landed — and mine 5045 collected with
**14**, a drop of exactly 2.

| Run | Failed | Passed | Skipped | xfail | **Collected** |
|---|---|---|---|---|---|
| **BASE** — `1fc95c1`, new file absent | 16 | 5008 | 5 | 1 | **5030** |
| **MINE** — `d271525` | **14** | 5025 | 5 | 1 | **5045** |

- **`comm` mine-only EMPTY ⇒ ZERO ATTRIBUTABLE.**
- **`comm` base-only = exactly the two `test_fix129_ntp_check` tests** ⇒ **the predicted 2-failure
  drop, confirmed, and attributed to the right cause** rather than assumed.
- Totals reconcile exactly: **5030 + 15 = 5045** (2 new NTP guards + 13 gate tests); passed
  5008 + 15 + 2 = 5025.
- **`xfailed = 1`, `XPASS = 0`** ⇒ batch 3's E4/W10 contract xfail is still genuinely xfailing.
- **0 failures from the new file.**

**⚠️ The first MINE run was NOT clean, and that is the point.** It carried **one attributable
failure** — `test_the_streak_is_identical_after_a_restart`. Rather than re-run, it was traced to
the paper auto-fill race (§9.3), fixed, and the full suite re-run. **The baseline is now 14**, down
from 16.

### 10.2 Deploy verification

| Step | Result |
|---|---|
| Tag `deploy-19jul-consecutive-losses` | → `d271525` |
| **Behaviour-neutral** | `git diff --name-only 1fc95c1..d271525` = **3 files, all under `tests/`** — nothing outside |
| Fresh VM backup `pre_deploy_consec_losses_20260719.db` | **SOUND**: `quick_check=ok` · schema **44** · **361** trades · **0** FK |
| Push `main` + tag | `1fc95c1..d271525`, post-receive checkout OK |
| Bare HEAD == local HEAD | `d271525` == `d271525` ✓ **PC == VM** |
| **New tests ON THE VM** | **22 passed** (13 gate + 9 NTP) — *including the two NTP tests that were failing on the VM before this batch* |
| **Live DB untouched** | mtime **`2026-07-19 02:00:02` before AND after** the VM test run |
| Schema / integrity / FK | **v44 unchanged** · `quick_check=ok` · **0** violations · 361 trades |
| Kill-switch state | **INACTIVE** (unchanged) |
| Services | `trading-system` inactive (expected — down since Fri) · token-watcher · alert-watcher · gui-dashboard **active**; trading-system + token-watcher **enabled** |
| Stale lock on VM | **none** (the one this session created was removed) |

---

## 11. NOT DONE (deliberately)

- No production code change; no schema, flag or config change.
- **The live-seed expression was NOT extracted from `main()`** — it is the right fix and it is
  queued, but it is a **boot-path** change and the boot path is exactly what S4 broke. After Monday,
  through the careful loop.
- E4/W10 untouched · `PerformanceAllocator` not wired (D1) · the three deferred `event_type`
  reporting sites and `build_taxonomy_map()`'s `--config-dir` bug left for Rama.
- The `test_rate_limiter` / `test_startup_checks` duration assertions — a different shape, recorded
  in §1.3 rather than swept.
- The PC-only stale instance lock — environmental, passes on the VM, not a code defect.

---

*Read-only investigation; test-only build. No production code, config, schema or flag changed.*
