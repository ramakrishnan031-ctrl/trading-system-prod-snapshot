# S1 + S3 + S5 — the three sweep-escalated ops/security items · DONE + DEPLOYED

**Date (IST):** 2026-07-17. **Deployed:** `ada0caa` — tag **`deploy-17jul-s1s3s5`** → `b61a776`.
**PC == origin == VM bare == `ada0caa`** (worktree md5 verified). **Schema v44, no migration.**
System not trading today (Rama's call); service `inactive`, kill switch clear, **0 open positions**.

| Item | Commit | What it actually was |
|---|---|---|
| **S1** | `09ea725` | `market_day_only` enforced at the cron entry — **30/30** (was 16/30) |
| **S3** | `c8d3621` | login **throttle** replaces the account lockout — the operator can no longer be DoS'd |
| **S5** | `ada0caa` | the **benign functional-status set** settled; per-job criteria triaged + deferred |

**Regression:** `10 failed / 4837 passed / 4 skipped`, **rc=1**, 12m44s — the 10 are the known
time-gated PC-env set (in-window; the 11th is the FIX-189 clock-guard, after 16:00 only).
**0 attributable, PROVEN**: the four affected suites re-run against the TRUE pre-change tree
(`git checkout 4c148fb -- scripts/ ops_dashboard/backend/auth.py` + grep-confirm absent — **never
`git stash`**) gave the **identical** 10 (`comm -23` = 0, `diff` = IDENTICAL).
Dashboard suite: **367 pass** in its own venv (was 357 + the 10 new S3 tests).

---

## ⭐ Three premises in the instruction that did NOT survive verification

Recorded first, because they were the stated reasons these were escalated — and two of them
would have led to a much larger, riskier change than the problem justified.

### 1. S1 needed NO new machinery, and NO crontab-generator change
The instruction said to *build* "ONE central guard … that reads the authoritative calendar + the
registry `market_day_only` flag". **The central guard already existed**:
`utils.cron_heartbeat.skip_if_non_trading_day` ("TASK #3 Layer 6") — already reading
`config/nse_holidays_<year>.yaml` via `utils.holiday_guard`, already used by 16 jobs, already
tested (`test_cron_alerts.py`), and **already failing open**. S1 was therefore *calling the
existing guard from the entry points that never called it* — not writing calendar logic.

The alternative considered and rejected: making `market_day_only` mechanical inside
`scripts/generate_crontab.py` (it sits in `_META_FIELDS`, i.e. documentation, while only
`_GEN_FIELDS` reach the command — which is *why* it is decorative). That is the tempting
"root-cause" fix, but it would have rewired the artifact that installs **all 47 cron jobs**,
behind a `parse`→`compose` byte-exactness selftest, a drift-check and the pre-receive hook — to
fix pointless holiday runs. **Blast radius wildly disproportionate to the bug.** The guard sits
behind each script's existing `__main__` instead, so **the crontab is byte-identical**
(verified live: `crontab -l` == canonical; the generator selftest still passes all 47 lines
byte-for-byte).

### 2. S5 was NEVER a prerequisite for S1
The instruction blocked S5 on "marking a job SKIPPED raises a functional issue", and sequenced
S1 behind it. **A heartbeat whose STATUS is `SKIPPED` never reaches the functional check** —
`build_eod_summary`'s `elif status == "SKIPPED"` catches it first and counts it under "⏭ Skipped".
Only a job that RAN, exited SUCCESS, and reported `functional_status=SKIPPED` is affected. Two
different paths. Since `skip_if_non_trading_day` records **status**=`SKIPPED`, S1 is untouched by
the benign set. The dependency did not exist.

### 3. S1 is waste-elimination, NOT a safety fix — and the FIX is the dangerous part
The Officer never false-alarmed on holidays: `core/cron_registry.py:257` `is_due_on()` returns
`_is_trading_day()` for `market_day` cadence, so those jobs are correctly dropped from
`expected_heartbeat_jobs`. The real cost of the bug: **14 jobs did pointless work on ~15 NSE
holidays a year** (Zerodha/Gemini API calls, junk artifacts). Nothing broke.

The asymmetry that governs the whole design:

| | |
|---|---|
| skip a real holiday | saves a pointless run — **trivial upside** |
| wrongly skip a TRADING day | `auto_refresh_token` never runs → no token → the token-watcher never starts the app → **THE SYSTEM CANNOT TRADE** |

**The fix carries more risk than the bug.** Hence: reuse the existing fail-open guard, write no
new calendar logic, change no cron lines.

---

## S1 — `market_day_only` enforced at the cron entry (`09ea725`)

**Root cause.** `market_day_only` is declared on 30 registry jobs and on the CronJob model
(`core/cron_registry.py:75`) and is **read by no code**. The codebase already knew:
`daily_trade_review.py:2448` and `test_daily_trade_review.py:971` both say *"that field is
METADATA — nothing enforces it"*. `cadence: market_day` (which agrees with it on all 47 jobs,
**0 mismatches**) does the Officer's half correctly; cron simply fired the job anyway.

**Corrected counts** (the audit said "20 of 25"; a looser regex of mine said 21):
**30 declare · 16 already self-guarded · 14 unguarded.**

Now guarded (a new `_cron_main` per script; **`main()` deliberately left unguarded so manual/ad-hoc
runs on a non-trading day still work** — mirrors the established `eod_cleanup` shape):
`auto_refresh_token`(critical) · `eod_verify`(critical) · `eod_broker_reconcile`(critical) ·
`gemini_premarket_brief` · `capture_metrics` · `metrics_summary` · `reconstruct_excursions` ·
`forward_shadow_record` · `sr_detector_backfill` · `wal_checkpoint` · `gemini_log_review` ·
`gemini_trade_coach` · `gemini_data_integrity_check` · `check_cron_drift`.

`capture_metrics_baseline.py` backs **two** jobs (`capture_metrics`, and `metrics_summary` via
`--summarize`); the skip is attributed to the job that actually ran, or the heartbeat lands under
the wrong name and the Officer reports a phantom miss for the other.

**Fail-open, and why it matters.** `is_trading_day()` **raises** `FileNotFoundError` when
`nse_holidays_<year>.yaml` is absent (e.g. 01-Jan-2027 before the new calendar is added).
`skip_if_non_trading_day` catches **any** calendar error and degrades to a plain weekday check →
**the job RUNS**. A missing calendar can never starve the token.

**Tests — `tests/unit/test_s1_market_day_guard.py` (10).** The addendum's §1 property is covered
twice over: `auto_refresh_token` RUNS on a trading day **mocked** *and* **against the REAL shipped
calendar** (a mocked-only test cannot catch a bad calendar file), plus RUNS when the calendar is
**unreadable**. Skipped only on a genuine listed holiday. Plus an **anti-decay pin**: every
`market_day_only` job must resolve to a guarded script — the invariant whose absence let this rot
in the first place; a new unguarded job now fails there instead of running on 15 holidays a year.
**RED-on-old:** the two wiring tests fail `AttributeError` on `4c148fb`, rc=1.

**Live verification:** VM `is_trading_day(2026-07-17)` → **True** (runs); `(2026-10-02)` → **False**
(Gandhi Jayanti, skips). Crontab byte-identical; `auto_refresh_token`'s cron line unchanged.

---

## S3 — throttle, not lockout (`c8d3621`)

**The control WAS the DoS.** `LoginAttemptTracker` locked an **account** for 15 min after 5
failures, keyed on the **submitted username**, and `authenticate()` checked `is_locked()` **before**
verifying credentials. Anyone knowing Rama's username could send 5 bad passwords and lock **him**
out of his own trading dashboard — repeatably, i.e. indefinitely.

**Why the audit's "per-IP" fix is strictly WORSE** (and why this was escalated, not swept): the GUI
is Waitress on `127.0.0.1:8500` behind the tailscaled proxy → `remote_addr` is **always
127.0.0.1** → per-IP collapses to **one shared bucket** and any failure locks out everyone,
Rama included. X-Forwarded-For is caller-settable, so a lockout keyed on it is spoofable both ways.
Neither is a sound basis for a decision; both are now **logged and trusted for nothing**.

**The fix — a throttle that gates nothing.** Credentials are verified **first and
unconditionally**; there is no state in which a correct login is refused ⇒ **the DoS is
structurally impossible, not merely shortened**. Only the failure path is delayed: typos free
below `max_failures`, then 1→2→4→8s, capped.

**Why a delay and not a fast rejection:** telling a correct password from a wrong one *requires*
verifying it. Any rule refusing attempts without checking would refuse Rama's correct password
too — that is a lockout again. Verify always + delay failures is the only mechanism that slows
guessing while keeping the operator's access unconditional.

**Residual (accepted, documented in the class):** each delayed failure holds a Waitress worker
thread for ≤ `throttle_max_seconds`. The cap bounds it; an *unbounded* delay would have traded the
old DoS for a new one. Reachable only from Rama's tailnet, not the internet.

**Addendum §2:** every throttle activation emits a WARNING with consecutive-failure count, applied
delay, username and source (`remote_addr` + XFF, both marked untrusted). Below the threshold
nothing is throttled and nothing is logged — the line means *"backoff engaged"*, not *"a login
failed"*.

**Tests — `ops_dashboard/tests/test_s3_login_throttle.py` (10):** correct password still works
after a **50-failure flood**; a successful login is never delayed; a wrong-username flood cannot
touch the real account; no lockout state exists after 5 or 100 failures; 1,2,4,8,8,8 capped; quiet
period decays; every activation logged. `test_auth.py::test_lockout` →
`test_throttle_replaces_the_lockout`: a **deliberate contract inversion** (it asserted the DoS),
rewritten rather than deleted so the change is explicit in history.

**⚠️ The GUI had to be RESTARTED.** `gui-dashboard` had been up since **10-Jul 04:05**, serving the
old auth from memory — S3 was on disk but **not live** until restart. Restarted 12:31:22, new PID,
`NRestarts=0`, login page HTTP 200, and verified **in the running process**: 100 failures →
`is_locked=False`, `throttle_delay=8.0s`. (S1/S5 need no restart — cron spawns fresh processes.)

---

## S5 — the benign functional-status set (`ada0caa`)

**The real culprit was `EMPTY_NO_DATA`, not `SKIPPED`.** The benign set was
`("OK","SUCCESS","DELIVERED")`, so `generate_screened_csv`'s own criterion — whose docstring
(`generate_screened_stocks_csv.py:312`) already calls it *"legitimate on a no-trade day"* — was
flagged as a functional issue on **every quiet trading day**.

That is the actual harm: **a control that cries wolf is a disabled control.** An EOD line that
false-alarms on quiet days trains the operator to ignore the functional section — which is exactly
how the 14-Jul "green heartbeat, empty CSV" silent failure survived.

**The rule settled:** *benign == "the job did its job, and an empty/absent result is the CORRECT
answer for today"*. `_BENIGN_FUNCTIONAL` is now a named, documented `frozenset`
(`OK, SUCCESS, DELIVERED, SKIPPED, EMPTY_NO_DATA`) so widening it is a deliberate decision with a
written rationale, never an accident. **Still flagged, deliberately:** `FAILED`, `MISSING`,
`DEGRADED`, and especially `UNKNOWN` (the criterion could not be evaluated — silence about
silence, precisely what F2 exists to surface).

**Second half — DEFERRED, with the triage recorded.** Measured: **32 monitored jobs · 5 have a
criterion · 27 execution-only** (the audit said ~20).
- **Deferred to the CAREFUL LOOP per the instruction's own escalation valve** — the criterion would
  read reconciliation/CAPITAL state: `eod_verify` · `eod_broker_reconcile` · `reconcile_positions` ·
  `daily_report` · `trade_journal` · `compute_strategy_metrics` · `forward_shadow_record`.
- **Deferred as not-clear-enough-to-be-worth-noise:** the `gemini_*` jobs · preflight phases ·
  `control_tower` · `backup_*` · `refresh_instruments` · `fetch_*` · `wal_checkpoint` ·
  `eod_cleanup` · `sr_detector_backfill` · `reconstruct_excursions` · `system_manager_eod` ·
  `auto_refresh_token`.

Each needs its own *"what does DONE mean for this job"* decision. Inventing 27 in bulk is how a
criterion becomes noise — the very failure this commit fixes. The benign set had to be settled
first regardless: it is the predicate every future criterion is judged against.

**Tests — `tests/unit/test_s5_benign_functional.py` (9):** `EMPTY_NO_DATA`/`SKIPPED` benign
(RED-on-old: both were flagged); `FAILED/MISSING/DEGRADED/UNKNOWN` still flagged (the set is not a
mute button); the set is pinned exactly; keys upper-case (the Officer compares `func.upper()`, so a
lower-case key would silently never match); every emitted value is classified.

---

## Deploy verification

| Check | Result |
|---|---|
| Fresh VM backup | `pre_deploy_s1s3s5_20260717_122924.db` (341M), `integrity_check=ok` |
| Preconditions | service `inactive`; kill switch clear; **0 open positions** |
| Code identity | `git diff --name-only deploy-17jul-s1s3s5..HEAD` = **empty** (HEAD *is* the tag) |
| PC == VM | PC `ada0caa` == origin `ada0caa` == bare `ada0caa`; worktree md5 identical |
| Schema | **44**, no migration |
| Integrity / FK | `ok` / clean |
| **Crontab** | **byte-identical** to canonical; generator selftest passes all 47 lines |
| S1 live | `is_trading_day(17-Jul)`=True (runs) · `(02-Oct)`=False (skips) |
| S3 live | GUI restarted 12:31:22; in-process: 100 failures → `is_locked=False`, delay 8.0s |
| Services | trading-system `inactive` (down today, by design) · token-watcher `active` · gui-dashboard `active` |

**Rollback:** revert any single commit (all independent, schema-free), or reset `main` to
`4c148fb`. S3 additionally needs a `gui-dashboard` restart to take effect either way.

**Not pushed:** branch `e4-w10-pnl-contract` (`ad34ee4`) — the E4/W10 fix, still gated on Rama's
explicit risk-posture sign-off.
