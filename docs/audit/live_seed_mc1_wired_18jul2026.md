# THE LIVE CAPITAL SEED (M-C1), WIRED — closing Q9's last coverage gap

**Date:** built 18-Jul-2026 (Sat); **regression + deploy 19-Jul-2026 (Sun) 00:15–00:50 IST**
(off-market; system DOWN since Fri 17-Jul, book flat)
**Basis:** `78e83d4` (tag `deploy-18jul-report-classification` → `b3a960d`), schema v44
**Deployed:** `abf276a`, tag `deploy-19jul-q9-live-seed` — **PC == VM**
**Scope:** TEST-ONLY. No production file changed.
**Closes:** the M-C1 live-seed gap, and with §C the Q9 programme.

---

## 0. TL;DR

**The cancellation is exact, and it is now wired.** With a non-zero carryover on the books, the
live seed lands `_total` on `broker.net` to within float noise (5.8e-11). Without the
subtraction it lands **18,094.54 high** — exactly the carryover, counted twice.

Three results worth carrying forward:

> **§A2 — the cancellation is CONTRACT-INDEPENDENT, so it survives E4/W10.** Both sides sum
> `pnl_delta` from the same helper, so `net − Σ + Σ = net` holds whatever `pnl_delta` means. The
> daily-loss **reader** — the quantity E4/W10 changes — is *not involved at all*.

> **§1 — no test can reach a real broker, and no live-mode fixture was built.** The live seed
> needs exactly one number from a broker, so a 12-line local stub supplies it. A guard pins the
> whole test tree.

> **§A4 — the seed expression is inline in `main()` and is NOT callable.** The tests drive the
> machinery it depends on (all real, call-count proven); the two-line expression stays under a
> structural pin. Stated plainly rather than claimed away.

---

## 1. §A1 — THE MAP, AND HOW THE CANCELLATION WORKS

```
main.py:2243   PAPER seed = selected_account.paper_capital          static; excludes today's P&L
main.py:2255   LIVE  seed = broker_adapter.get_margins().net
                          - fund_manager.today_realized_pnl_carryover()          <-- M-C1
main.py:2259   fund_manager.initialize(_startup_capital)            ONE call site, both modes
main.py:2286   fund_manager.rehydrate_from_open_trades()            Phase 2 re-adds today's P&L
```

| Component | Site | Role |
|---|---|---|
| `today_realized_pnl_carryover()` | `fund_manager.py:1777` | Σ of today's `RELEASE_USED.pnl_delta` — the subtrahend |
| `_today_release_used_pnl_rows()` | `:1757` | **the SHARED row selection** — the whole mechanism |
| Phase 2 | `:1703-1711` | for each row: `bucket avail += pnl`, `_total += pnl` |

**The mechanism.** On a mid-day warm restart the broker's `net` **already includes** today's
realized P&L. Phase 2 then re-applies it. Seeding with the raw `net` therefore counts it twice
and **live reservable capital silently inflates** — the system would trade on capital it does not
have. Subtracting the carryover makes `seed + Phase 2 == broker.net` **by construction**:

```
_total = (net − Σ) + Σ = net
```

**What would break it:** the two sides ceasing to share `_today_release_used_pnl_rows`. Then Σ
on each side can differ, and **neither number looks wrong on its own** — the same silent shape as
E4/W10.

---

## 2. §A2 — CONTRACT-INDEPENDENCE (verdict: YES, it survives E4/W10)

Because both sides sum the **same field from the same rows**, the cancellation is algebraic and
independent of what `pnl_delta` means. Measured:

```
reader  (get_daily_realized_net_pnl) = -18,189.08     = SUM(pnl_delta) - SUM(costs)
carryover (the live-seed subtrahend) = -18,094.54     = SUM(pnl_delta)
difference                           =     -94.54     = exactly SUM(costs)
```

**The reader is not part of the cancellation.** E4/W10 changes the *reader's* contract; the seed
and Phase 2 never consult it. ⇒ the fix can land without disturbing live seeding.
`TestSharedHelper::test_the_reader_is_a_different_quantity_and_is_not_involved` pins this, and
fails if the two quantities ever converge (which would mean the independence argument had
quietly stopped holding).

---

## 3. 🔴 §1 — WHY NO TEST CAN REACH A REAL BROKER

**No live-mode fixture exists, and none was needed.** The live seed wants exactly one number
from a broker. So instead of parameterising `wired_system` into live mode — which would have put
adapter construction and credential loading one refactor away from a test — the tests use
`_StubBroker`: a local class with a single `get_margins()` returning a value the test chooses.
No adapter, no `kiteconnect` import, no credentials, no network.

**`TestNoBrokerReachable` covers the whole test tree**, and is deliberately **narrow**:

| Blocked | Allowed |
|---|---|
| constructing `KiteConnect(` | `from kiteconnect import exceptions as kex` |
| `os.environ["ZERODHA_API_KEY" / "ZERODHA_ACCESS_TOKEN"]` | `"ZERODHA_API_KEY_LFL836"` as a string literal in an assertion |
| `os.environ.get("ZERODHA_…")` | `MagicMock(spec=KiteConnect)` |

That precision was earned: **the first version of the guard was over-broad** and flagged seven
legitimate exception-class imports in `test_zerodha_adapter.py` and `test_h13_token_monitor_relatch.py`.
Importing kiteconnect's *exception types* carries no credential risk; constructing the *client*
does. The guard now distinguishes them, and a companion test asserts both halves — that it
matches the real doors, and that it stays quiet on the legitimate imports.

**Also pinned:** the integration fixture must keep `paper_mode=True` and `kite_client=None`
(`conftest.py:197-204`).

---

## 4. ⭐ §A4 — WHAT EXECUTES, AND WHAT IS REPLICATED

The trap this section exists for has fired three times (batch 4 twice, batch 5's reused
`KillSwitch`), and a "live mode" harness is the easiest of all to fake because the test
constructs the mode.

**Probed first, then asserted in the tests:**

| Probe | Result |
|---|---|
| `today_realized_pnl_carryover()` with real P&L | **−18,094.54** — NON-ZERO ✅ (the anti-vacuity gate) |
| `carryover == Σ(shared-helper rows)` | **True** (2 rows) |
| **`_today_release_used_pnl_rows` CALL COUNT** | **2** — one from the carryover, one from Phase 2 |
| `initialize(net − carry)` + `rehydrate()` → `_total` | **471,234.55999999994** vs net **471,234.56** (Δ 5.8e-11) |
| Seeding with raw `net` (no subtraction) | `_total` **453,140.02**, drift **−18,094.54** = the carryover |

**REAL production code driven by the tests:** `today_realized_pnl_carryover` ·
`_today_release_used_pnl_rows` · `initialize` · `rehydrate_from_open_trades`.

**REPLICATED, because it is not callable:** the two-line expression at `main.py:2255-2258` is
**inline in `main()`'s body with no function boundary**. No test can invoke it without running
`main()` — which in live mode is exactly what must never happen. It is instead held by the
structural regex pin added in batch 5
(`TestParity::test_the_live_seed_still_subtracts_the_carryover`), which fails if the subtraction
is removed or the two sides stop sharing the row helper.

⇒ **The part that can silently break is wired; the part that is two lines is pinned.** No claim
is made that "the real live-seed path executes end-to-end", because it does not.

---

## 5. §B — WHAT THE TESTS PROVE

`tests/integration/test_q9_live_seed_mc1_wired.py` — **13 tests**.

| Test | Proves |
|---|---|
| `test_live_seed_lands_total_exactly_on_broker_net` | **B1 POSITIVE** — carryover asserted non-zero **first**, then `_total == broker.net` |
| `test_without_the_subtraction_todays_pnl_is_counted_twice` | the failure M-C1 prevents: drift == exactly the carryover |
| `test_with_no_realized_pnl_the_subtraction_is_a_noop` | **B2 NEGATIVE** — cold boot: carryover 0, seed *is* `broker.net`, 0 P&L rows |
| `test_the_capital_picture_quantity_by_quantity` | **B3** — total/avail/reserved/used/reader each asserted; global identity holds |
| `test_paper_and_live_seeds_each_land_correctly` | **B4 PARITY BOTH WAYS** — live → `broker.net`; paper → `paper_capital + P&L`; and the two genuinely differ |
| `test_both_modes_use_the_same_restore_function` | one `rehydrate` with no mode branch; **one** `initialize` call site |
| `TestSharedHelper` (×3) | carryover ≡ Σ(rows Phase 2 replays) · reader is a different quantity · **both sides call the shared helper (2 calls)** |
| `TestNoBrokerReachable` (×4) | §1 |

**⭐ Anti-vacuity is the whole game here** and is asserted before anything else: with no realized
P&L the carryover is 0, the subtraction is a no-op, and **deleting M-C1 entirely would change
nothing**. Every positive test asserts `carry != 0.0` first, and the parity test additionally
asserts the two modes' totals differ.

---

## 6. §B5 — PROVEN TO BITE

| Plant | Break | Result |
|---|---|---|
| **A** | `today_realized_pnl_carryover()` → `0.0` (M-C1 removed) | **14 failures.** *"the carryover is ZERO — the subtraction is a no-op and this test would pass with M-C1 deleted"*, and *"the live-seed subtrahend (0.0) is not the sum of the rows Phase 2 replays (−18094.54)"* |
| **B** | the two sides compute rows **independently** (different filter, ×0.97) | **14 failures.** *"LIVE SEED DID NOT CANCEL: `_total`=470691.7238 but broker.net=471234.5600 (drift −542.8362; carryover was −17551.7038)"* |
| **C** | a real client construction planted in a test file | the credential guard fires, naming **file:line and both doors** |

**⭐ Plant B is the meaningful one — the §A2 silent-drift mode.** Every value stays plausible
and internally consistent: the carryover is a reasonable number, the seed is a reasonable number,
the total is a reasonable number, and the global capital identity still holds. So
**`CapitalInvariantViolation` = 0 and `CapitalStateInconsistent` = 0** (measured) — production's
own runtime guards see nothing — yet the cross-check catches it. That is the E4/W10 class, in the
live seed.

After restore: `capital/` and `orders/` show **0 modified files**; 13 passed.

---

## 7. §B6 — REACHABILITY, AND WHAT MONDAY EXERCISES

| Element | Verdict |
|---|---|
| The live seed path (`main.py:2255`) | ✅ **REACHABLE** — production runs `main.py --mode live` (verified in the systemd unit: `ExecStart … main.py --mode live`) |
| The subtraction having a **non-zero** effect | ⚠️ **CONDITIONAL** — only when today's books already hold realized P&L, i.e. a **mid-day warm restart** |

**⚠️ Monday 20-Jul 08:15 exercises only the trivial case.** It is a cold boot on a flat book, so
today's `RELEASE_USED` rows are empty ⇒ **carryover = 0** ⇒ the seed is simply `broker.net` and
Phase 2 carries nothing. **The cancellation this batch proves will not have run in production.**
It first runs on a genuine mid-day restart after at least one close.

---

## 8. REGRESSION + DEPLOY

Base taken with the standing rule: **MAIN TREE**, new file moved aside, `__pycache__` cleared
(a stale `.pyc` *was* present and removed — batch 5's trap, caught again), `git stash` unused
(stash list verified empty).

**Environment note (material to reproducing these numbers).** The §B build session was ended by a
power outage (Ctrl+C, then an unclean shutdown) after `abf276a` was committed but before the
regression ran. Two things were lost and repaired before any measurement:

1. `tests/integration/test_q9_live_seed_mc1_wired.py` was **deleted from the working tree** while
   the git object survived — restored with `git checkout --`, verified **byte-identical to the
   commit** (`git diff HEAD` empty).
2. The venv **lost `pyotp` and `waitress`**, both pinned in `requirements.txt`. This is not
   cosmetic: `tests/unit/test_gui_secret_key.py` imports `ops_dashboard.backend.app` → `pyotp`, so
   collection **aborted for the entire suite** (`Interrupted: 1 error during collection`).
   Reinstalled at pinned versions (`waitress==3.0.2`, `pyotp==2.9.0`); `pip check` clean.
   Proof the repair restored the declared state rather than changing it: collection returned to
   **5017** for base — exactly the documented pre-outage baseline.

*The restored copy was re-proven to bite before it was trusted (see §6): planting `carryover → 0.0`
against the restored file failed 7 tests with the anti-vacuity gate firing first
(`assert 0.0 != 0.0`).*

### ⚠️ 8.0 THE FIRST ATTEMPT WAS INVALID — A NEW, REUSABLE HAZARD

The first pair of runs was **discarded**, and the reason is worth carrying forward as a rule.

MINE ran 23:30–23:56 (Sat 18-Jul); BASE ran 23:57–00:13 — **crossing midnight**. The comparison
came back with **both `comm` directions non-empty**, which is impossible for a test-only file
addition: a new test file cannot make `test_risk_engine::test_consecutive_losses_at_limit` *pass*.

Cause, verified in source rather than assumed:

```
tests/unit/test_risk_engine.py:49                      _TODAY = now_ist().date().isoformat()
tests/unit/test_phase3_delivery_caps_...py:35          _TODAY = now_ist().date().isoformat()
```

`_TODAY` is captured at **module import (collection) time**; the engine computes the current date
at **execution time**. A run that collects on one date and executes on the next seeds trades that
the engine then reads as *yesterday's* — so date-scoped tests fail for a reason that has nothing
to do with the change under test.

> **📌 RULE (new): the standing "base and mine in the SAME TIME WINDOW" requirement needs
> "— and NEITHER RUN MAY CROSS MIDNIGHT."** At ~15 min/run, any regression started after ~23:15
> IST is unsafe. This is the same family as the git-ignored-`instruments.csv` masking rule: an
> environmental asymmetry that silently corrupts attribution rather than announcing itself.

### 8.1 Regression — same window, no midnight crossing

Both runs **Sun 19-Jul, 00:15–00:44 IST**.

| Run | Failed | Passed | Skipped | xfail | **Collected** |
|---|---|---|---|---|---|
| **BASE** — new file absent (verified: no source, no stale `.pyc`) | 16 | 4995 | 5 | 1 | **5017** |
| **MINE** — `abf276a` | 16 | 5008 | 5 | 1 | **5030** |

- **Both `comm` directions EMPTY ⇒ the failure sets are IDENTICAL ⇒ ZERO ATTRIBUTABLE.**
- **Totals reconcile exactly: 5017 + 13 = 5030**; passed 4995 + 13 = 5008.
- **`xfailed = 1`, `XPASS = 0`** ⇒ batch 3's E4/W10 contract xfail is still genuinely xfailing.
- **Zero failures from the new file** (13/13 pass; grep of the failure list returns 0).

### ⚠️ 8.1a THE PC BASELINE MOVED 12 → 16 — measured, and NOT from this change

All four are present in **BASE**, so attribution is unaffected. They are recorded because the next
batch's baseline must expect them.

| New failure | Count | Diagnosis | Scope |
|---|---|---|---|
| `test_fix129_ntp_check` `test_drift_within_warn_passes` · `test_drift_between_warn_and_block` | 2 | **Float64 precision.** `drift_sec` lands exactly **one half-ULP below** the bound: `assert 0.9999997615814209 >= 1.0` (and `2.999999761581421 >= 3.0`). At epoch ≈1.78e9 the ULP is ≈4.8e-7, so whether `local + 1.0` rounds up or down is decided by the low bits of the current clock. The fetcher is a **stub** — no network, no real NTP | 🔴 **NOT PC-only — fails identically ON THE VM.** Both machines crossed the same epoch threshold. Wants `pytest.approx`; **left for Rama, out of scope here** |
| `test_instance_lock` `test_p1_second_concurrent_instance_is_refused` · `test_p2_restart_after_crash_is_not_blocked` | 2 | Stale `%TEMP%\trading-system.lock` naming **PID 8708, which is not running**; the refusal names a different PID than the spawned one. Port 5001 free | ✅ **PC-only — all `test_instance_lock` tests PASS on the VM** |

*Evidence that these are coin-flip rather than steady: the discarded first pair caught **one** of
the NTP pair in base and **the other** in mine — the signature of a rounding race, not a defect in
the code under test.*

### 8.2 Deploy verification

| Step | Result |
|---|---|
| Tag `deploy-19jul-q9-live-seed` | → `abf276a` |
| **Change is behaviour-neutral** | `git diff --name-only abf276a~1 abf276a` = **one file, `tests/integration/`** — nothing outside `tests/` |
| Fresh VM backup `pre_deploy_q9_live_seed_20260719.db` | **SOUND**: `quick_check=ok` · schema **44** · **361** trades · **0** FK |
| Push `main` + tag | `78e83d4..abf276a`, post-receive checkout OK, crontab auto-installed |
| Bare HEAD == local HEAD | `abf276a` == `abf276a` ✓ **PC == VM** |
| File deployed to worktree | present, **459 lines** |
| **New tests ON THE VM** | **13 passed** |
| **Live DB untouched** | mtime **`2026-07-18 09:20:01` before AND after** the VM test run |
| Schema / integrity / FK | **v44 unchanged** · `quick_check=ok` · **0** violations · 361 trades |
| Kill-switch state | **INACTIVE** (unchanged; prior-day auto-clear of 17-Jul) |
| Services | `trading-system` inactive (expected — system down since Fri 17-Jul) · token-watcher · alert-watcher · gui-dashboard **active** |

---

## 9. NOT DONE (deliberately)

- No production code change; no schema, flag or config change.
- **No live broker connection** — this batch exercises the live SEEDING arithmetic only.
- E4/W10 not touched · `PerformanceAllocator` not wired (D1) · the three deferred `event_type`
  reporting sites and `build_taxonomy_map()`'s `--config-dir` bug left for Rama.
- **Layer 14 (consecutive-losses gate) not wired** — see the final matrix §3. It needs ≥4
  consecutive losing closes, which collides with the daily-loss limit and deserves its own
  design rather than being smuggled in here.

---

*Read-only investigation; test-only build. No production code, config, schema or flag changed.*
