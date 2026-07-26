# Clock-dependent code — three axes, and the one that stops the boot

**Date:** 2026-07-26 · **Branch:** `hold-check1-w8-26jul` · **Scope:** the test suite, plus
every production site that reads the clock outside `core.time_authority`.

---

## Headline

Two clock-dependent tests were found on consecutive days, on **different axes**. That made it
a class, so the suite was swept for a third. **The third exists, it is on a third axis
(year), and it is not a test bug — it is a measured boot-blocker.**

| axis | instance | status |
|---|---|---|
| **hour-of-day** | `test_interactive_startup::test_holiday_guard_missing_yaml_proceeds` — past the 18:15 `SERVICE_START_CUTOFF`, `main()` returns 0 at the window check instead of reaching the config load the test asserts on | fixed 25-Jul (`570b3e8` era) |
| **day-of-week** | `test_daily_trade_review::test_main_defaults_date_to_today_and_records_heartbeat` — fails every Saturday and Sunday | **fixed 26-Jul**, §B below |
| **year** 🔴 | `core/config_loader.py:2159` — `_CONFIG_FILES` resolves `f"nse_holidays_{date.today().year}.yaml"` | ⚠️ **OPEN — pinned, not fixed** |

---

## 1. Why a clock-dependent test is not a nuisance

It corrupts the only regression instrument the project has.

The gate discipline is: take a **BASE** run and a **MERGE** run in the same session, and
`comm -23` the failure **sets** — merge-only must be empty. That comparison is meaningless if
the suite answers a different question depending on when it runs.

> **A suite that answers differently on a Saturday than on a Tuesday makes a weekend baseline
> non-comparable to a weekday one.** And weekend evenings are precisely when baselines get
> taken in this project.

The 25-Jul instance already demonstrated the cost on the hour axis: the same unchanged tree
gave 32 failures at 17:35 and 34 at 18:20, and the difference was read as a possible
regression until it was measured.

---

## 2. §B — the day-of-week instance, fixed

`main()` with no `--date` defaults to today IST, by design, so the cron line needs no date
substitution. On a weekend `is_holiday_or_weekend()` correctly short-circuits: the job logs
*"Skipping — non-trading day"*, records a `SUCCESS` heartbeat with `functional_status=SKIPPED`,
and returns 0 **without writing a report**. The test then asserts the xlsx exists. It cannot.

**The fix pins the clock — it does not weaken the assertion.**

- `main()` does `from core.time_authority import now_ist` *inside the function*, so patching
  the report module's namespace is a no-op; the patch must land on the source module.
- The clock is frozen to **Tuesday 2026-07-14**, and the test **asserts that date is still a
  real NSE trading day** before using it. The pin is self-verifying: if the holiday YAML ever
  lists it, the test says so instead of failing for an unrelated reason.
- The **real** holiday guard still runs against the frozen clock. Control the input; do not
  disable the check.

**RED-first, proven under a simulated calendar** (an autouse fixture patching `now_ist` /
`today_ist` / `now_ist_iso`):

| simulated day | OLD code | NEW code |
|---|---|---|
| Tue 2026-07-14 | pass | pass |
| Thu 2026-07-16 | pass | pass |
| Sat 2026-07-18 | **FAIL** | pass |
| Sun 2026-07-19 | **FAIL** | pass |

The stash/restore around the old-code run was verified **md5-identical, 56,216 bytes both
ways** — not by `git diff`, which cannot see a line-ending rewrite (see
`foundation_engineering_rules.md` §5.1).

**A companion test was added** for the other half of the same clock: pinned to a Sunday, it
asserts the guard skips the report *and still heartbeats* `SUCCESS`/`SKIPPED`, so the Cron
Officer does not raise a false "no heartbeat" alarm every weekend. That behaviour was
previously "covered" only by the calendar happening to be a Saturday — which is not coverage,
and is what broke the test above.

---

## 3. §B3 — the sweep, and the third instance

### 3.1 The sweep that would have missed it

The empirical sweep re-runs the suite under a simulated calendar and diffs the failure sets.
It patches `core.time_authority`. ⚠️ **That is not the only clock.** Five production sites
read the clock directly:

| site | reads | axis |
|---|---|---|
| `core/config_loader.py:2159` | `date.today().year` | **year** 🔴 |
| `utils/holiday_guard.py:78` | `date.today().year` | year |
| `utils/startup_checks.py:863` | `datetime.now(timezone.utc)` → year | year |
| `main.py:1718` | `date.today()` | date / weekday (the boot guard) |
| `core/state_store.py:73` | `datetime.now(_IST)` | **documented exemption** |

⭐ **Any sweep that patches only `time_authority` is blind to all five.** That is exactly how
the year axis stayed hidden, and it is the transferable lesson: *a sweep is only as broad as
the seam it patches.* Foundation rule 3.8 ("Single Source of Time") has four exceptions, only
one of which is acknowledged.

### 3.2 The third instance — MEASURED

`_CONFIG_FILES` is a **module-level tuple**, so the filename is baked in **at import time**:

```python
("nse_holidays", f"nse_holidays_{_date.today().year}.yaml", NseHolidaysConfig),
```

`config/` contains exactly one such file: `nse_holidays_2026.yaml`.

Patching `date.today` to 2027 **before** importing `config_loader`:

```
registry filename resolved to : nse_holidays_2027.yaml
exists on disk                : False
load_all()                    : ConfigMissingError: Required config file not found: nse_holidays_2027.yaml
```

`load_all()` is on the boot path (`main.py:1743`) and its contract is *"all files must
succeed — any failure raises immediately."*

> 🔴 **THE FIRST 08:15 BOOT OF 2027 DOES NOT START.**

⚠️ **And the suite actively hides it.** Every existing `config_loader` test writes stubs named
`nse_holidays_2026.yaml`, while the registry would be asking for `nse_holidays_2027.yaml` —
so they would **all** fail on the same morning, for a reason that is not a bug in the code
under test. The noise would arrive at the same moment as the signal.

**Classification: LATENT, with a date-certain trigger.** Not reachable today; reachable with
certainty on 1-Jan-2027. Per the standing rule for latent findings — document, and pin with a
test that fails when it becomes reachable.

### 3.3 Pinned, not fixed

`test_config_loader::test_every_registered_config_file_exists_for_the_CURRENT_year` asserts
that **every** filename in `_CONFIG_FILES` resolves to a file in `config/`.

- Green today (2026).
- **Proven RED under a 2027 clock** — it names `nse_holidays_2027.yaml` exactly.
- Stated as the property, not the one filename, so it also catches any future registry entry
  added without its file.
- ⛔ The failure message says **add the file, do not edit this test.** Red here is an action,
  not a stale expectation.

**🔴 RAMA-ACTION: commit `config/nse_holidays_2027.yaml` before 31-Dec-2026.** That is the
fix; the test is only the alarm. The alternative — resolving the filename lazily instead of at
import — is a real code change on the boot path and is not being made on this pass.

---

## 4. What was NOT found

**No fourth clock-dependent test exists on the `time_authority` seam.** Measured, with two
corrections to the experiment along the way — both worth recording, because each would have
produced a wrong answer.

### 4.1 The runs

| run | clock | instrument | failures |
|---|---|---|---|
| A | real Sunday 26-Jul | none | 30 |
| B | simulated Tue 2026-07-14 16:07 | `simclock` plugin | 50 |
| C | simulated Tue 2026-07-14 19:00 | `simclock` plugin | 53 |

### 4.2 The answer that matters: `A \ B` is EMPTY

A weekend-dependent test fails on a real Sunday and passes on a Tuesday, so it would appear
in **A but not B**. `A \ B` is empty ⇒ **no test in the suite fails because today is a
weekend**, beyond the one fixed in §2 (which is absent from A — the fix holds under a full
run).

### 4.3 ⚠️ Correction 1 — `B \ A` = 20 is the INSTRUMENT, not a finding

The naive read of "50 vs 30" is *twenty new weekday-dependent tests*. That is wrong. The
plugin patches `core.time_authority`, and most of the 20 are tests **of that module** or of
callers that compare a patched clock against a real one (`test_time_authority.py` ×7,
`test_zerodha_adapter::test_fix009_paper_mode_returns_now_ist`, `test_time_authority_sweep`,
and others that stamp a row with one clock and query it with the other).

**Distinguished by measurement, not by argument:** the same 10 files were re-run under the
**same plugin** on a simulated Tuesday *and* a simulated Sunday.

```
SIM Tuesday : 20 failed, 416 passed
SIM Sunday  : 20 failed, 416 passed
Tuesday-only: (empty)      Sunday-only: (empty)
```

Identical sets ⇒ **day-invariant ⇒ instrument artifacts.** Comparing a no-plugin run against
a plugin run confounds two variables at once; the fix is to vary only the day and hold the
instrument constant.

### 4.4 ⚠️ Correction 2 — the three "19:00-only" failures were MY OWN parallelism

`C \ B` looked alarming: it contained
`test_interactive_startup::test_holiday_guard_missing_yaml_proceeds`, **the very hour-of-day
bomb recorded as fixed on 25-Jul**. Re-running it alone at a simulated 19:00: **it passes.**
The full-run failure reads

```
CRITICAL main:main.py:1803 Instance lock failed: Another instance is running
(PID 26588, lock=C:\Users\rama\AppData\Local\Temp\trading-system.lock)
```

Three suites were running **in parallel**, contending for the machine-global `%TEMP%` lock —
the known `test_instance_lock` flake, and the other two entries in `C \ B` are that test
itself. The 25-Jul fix is intact: the test pins its clock with `_at_ist(10, 0)` and its
docstring says so.

> ⛔ **RULE EARNED HERE: never run suites in parallel on this machine.** The instance lock is
> machine-global, so concurrent runs corrupt each other's failure sets — and a corrupted set
> is worse than a slow one, because it looks like a finding. The BASE/MERGE gate for this
> change was therefore re-taken with the runs **sequential and alone**.

### 4.5 Scope of the claim

⚠️ Bounded, and the bound is the point: this covers only tests whose clock arrives through
`core.time_authority`. The four direct-clock production sites in §3.1 are **not** reachable
by this instrument — the year bomb was found by **reading, not by running**, and no amount of
re-running this sweep would have surfaced it.
