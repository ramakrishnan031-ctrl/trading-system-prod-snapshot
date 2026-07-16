# M-C8 — Move the hard_kill exit-retry loop off the caller's thread

**Date:** 16-Jul-2026 (IST) · **Branch:** `mc8-async-hardkill-16jul` (off `main`@`16437ae`)
**Status:** IMPLEMENTED + TESTED, **UNPUSHED**. Deploys OFF-MARKET with the M-C cluster.
**Files:** `capital/kill_switch.py`, `main.py` (+ `tests/unit/test_mc8_async_hardkill.py`,
and a 3-line entry-point change in `tests/unit/test_p0_live_day1_fixes.py` — see §2.1).
**Schema:** unchanged. **Parity:** one mode-agnostic path, no mode branch.

**Commits (7):** `5744776` worker · `2d6d005` lifecycle · `ed91aa7` tests · `46ec78a`
full-suite repairs · `ae5bf74` thread-start guard · `52cbe92` drain honesty · `61f5977`
docstring correction.

> **Read §2.1, §6 and §6.3 before approving.** The design's stated test premise turned
> out to be false, the first "baseline" I took proved nothing, and two of my own tests
> initially could not have failed. All three are documented rather than smoothed over,
> because each is the kind of error that makes a green suite meaningless.

---

## 1. The defect

`hard_kill` (`kill_switch.py:550` → `_run_cancel` → `_exit_all_trades_indestructible`)
ran the entire indestructible exit loop — retry backoff 5/15/45s, bounded by
`_HARD_KILL_MAX_RETRY_HOURS = 2.0` — **inline on the caller's thread**.

Every production caller is on the fill/commit path:

| Caller | Site |
|---|---|
| `order_placer` | `:1499` (persist-entry-orders failed after broker success), `:3750` (LIMIT_TRIPLE exits failed after ENTRY filled) |
| `fund_manager` | `:974` (commit_to_used), `:2259` (capital invariant violated) |
| `drift_handler` | `:231` (TIER_HARD drift) |
| `main` | `:647` (circuit-breaker API failure) |

So a HARD_KILL could **starve the fill/event pipeline for up to two hours** — precisely
when the system most needs fills to process. The flatten itself was never the problem;
blocking the caller was.

---

## 2. Why fire-and-return is safe (the seam)

Verified in the code, not assumed:

- **All 6 production callers invoke `hard_kill(...)` as a bare statement** — no
  assignment, no use of the `CancellationReport`. They call it for effect. So no
  result mechanism is needed. **This is the load-bearing fact, and it holds.**
- **Production always takes the adapter path**: `main.py:1983` always calls
  `set_adapter`, so `_run_cancel`'s `self._adapter is not None` branch is the only
  one production ever reaches.
- `_run_cancel` has exactly one call site (`hard_kill:550`).

⇒ Dispatch **only** the adapter branch to a worker; keep the legacy branch and the
internal method synchronous. That split is load-bearing, not incidental — it is
documented in the code so a future refactor does not "tidy" it away.

### 2.1 ⚠️ CORRECTION — the design's test premise was WRONG

The approved design (and this report's first draft) asserted:

> *"only 3 unit tests read the report, and they use the LEGACY cancel_fn path, which
> production never takes … all existing tests pass unchanged."*

**That is false.** `tests/unit/test_p0_live_day1_fixes.py::TestBugC_KillSwitchExit`
(3 tests, `:228`–`:270`) calls `ks.hard_kill()` **with an adapter set** and asserts on
the outcome — `len(adapter.calls) == 1` (races the worker) and `report.succeeded == 1`
(now a dispatch marker). The original seam analysis grepped a **subset** of the test
tree and missed them. **The full-suite run caught it; the 10-suite baseline did not.**

**Does this invalidate the design? No.** The design rests on the *production* callers
ignoring the report — verified, unchanged. What broke were three tests that used
`hard_kill()` as a convenient entry point to exercise the **flatten's logic** (Bug C:
intent-derived-from-product, and success-detection via `broker_order_id`). Neither
asserts hard_kill's synchrony as a contract.

**Resolution:** those 3 now drive `_exit_all_trades_indestructible()` directly —
exactly as their siblings already do (`test_h4:109`, `test_h5:67`,
`test_hard_kill_flatten_chain:132`). **Every assertion is verbatim; nothing weakened
or deleted.** `hard_kill`'s dispatch-to-flatten path is covered by
`test_mc8_async_hardkill::test_f4`.

**So the honest claim is:** *86 pre-existing kill-switch tests pass unchanged; 3 tests
in one suite needed a one-line entry-point change to become async-aware.* Not "no test
rewritten".

---

## 3. The design as built

### 3.1 Worker dispatch
`_run_cancel`'s adapter branch calls `_dispatch_flatten_worker()` and returns
immediately with `CancellationReport(..., dispatched=True)`. The kill **state** is
still tripped synchronously *before* dispatch, so `is_active()` blocks new orders the
instant the caller regains control — that did **not** go async.

`CancellationReport` gained `dispatched: bool = False` so the async return declares
itself instead of masquerading as `attempted=0` ("nothing to do"). Frozen dataclass,
new field last with a default ⇒ every existing construction is unaffected.

### 3.2 State machine
`IDLE → RUNNING → DRAINING → COMPLETE`, guarded by a **dedicated `threading.Lock`**,
deliberately **not** the KS4 RLock. Reusing the RLock would re-create the **M-C4**
defect in a worse place: a 2h join under the lock that gates `is_active()` would block
the last-mile order check for the whole flatten. Lock order is documented: a holder of
`_flatten_lock` never acquires `self._lock`. Critical sections are a state read/write
and a thread handle — never a publish, send, or join.

`COMPLETE` is set in a **`finally`**. This is safety-critical, not tidiness: a worker
that died leaving the state at `RUNNING` would make `is_flatten_in_progress()` answer
True forever, and the eod-self-exit gate would hold the process open all night waiting
on a flatten that is not running. Covered by test (g2).

**Thread-start failure (found in self-review, not by a test).** `t.start()` can raise
`RuntimeError("can't start new thread")` under thread exhaustion — and thread
exhaustion is most likely **exactly when a HARD_KILL fires**, because the process is
already in distress, which is why the kill is firing. Unguarded, that left the state
at `RUNNING` with a thread that never ran (⇒ `is_flatten_in_progress()` True forever,
the eod gate wedged, and `drain_flatten` join()ing a dead handle and raising) **and**
dropped the flatten entirely. Now: catch, clear the handle, log CRITICAL, and run the
flatten **INLINE** (the pre-M-C8 behaviour). That re-introduces caller blocking in
this one impossible-to-spawn case, and that is the right trade — **an unflattened book
is far worse than a blocked caller**. Covered by test (g4).

### 3.3 Single-flight (adapter path only)
A repeat `hard_kill` — re-entrant or cross-thread — no-ops instead of starting a second
flatten. Two loops would race each other placing exits for the same positions, and a
re-run is pointless anyway: the running loop re-derives from broker truth on **every**
retry (H-4), so it already covers positions that appear after it started. Because
`RUNNING` is set **under the lock before `start()`**, a racing second call always
observes it — there is no dispatch window.

The **LEGACY path keeps KS6 re-run semantics unchanged** (`test_kill_switch.py:356`
still passes). KS6's "re-runs cancellation" is therefore now path-specific: preserved
for legacy, single-flight for the adapter. Production uses the adapter path.

Single-flight gates only what is **in flight** — after `COMPLETE`, a later emergency
dispatches a fresh flatten (test b2). Otherwise the first hard_kill of the day would
permanently disarm the mechanism.

### 3.4 Conditional writes (ChatGPT Q2 = A + B; no global lock)
Broker truth stays authoritative — every decision is re-derived from `get_positions()`,
so the flatten is self-healing against DB races. The residual exposure was **write
ordering** against the fill thread. Each write is now conditioned on the expected prior
status, so the losing side of a race is a clean no-op.

**A correction to the design's own premise, found while proving the tests RED-on-old:**

| Write | Pre-existing protection | What the conditional actually adds |
|---|---|---|
| `trades` → `EXITING` (`_mark_trade_exiting`) | `schema.sql:278` `trg_trades_terminal_status_guard` **ABORTs** transitions out of `CLOSED`/`CLOSED_MANUAL`/`FAILED`/`CANCELLED`/`REJECTED*` | The **terminal case was already safe.** The conditional closes the statuses the trigger does *not* cover — above all **`UNKNOWN_IN_FLIGHT`**, the A-2 "the ack timed out, we do not know if it filled" state. An unconditional write erased that uncertainty and replaced it with a confident lie. It also turns the terminal case from *trigger ABORT → caught → logged CRITICAL "DB write failed"* into a quiet no-op + INFO, so routine concurrency stops imitating a real DB fault. |
| `orders` → `CANCELLED` (`_cancel_trade_resting_exits`) | **none — there is no trigger on `orders` at all** | The **only** guard. An SL that FILLED inside the race window was recorded as `CANCELLED`: an exit that never happened, and a reconciler left believing a closed position was still open. |

Both status sets are single module constants (`_FLATTEN_LIVE_TRADE_STATUSES`,
`_FLATTEN_TERMINAL_ORDER_STATUSES`) **shared with the SELECTs that feed them**. If query
and write-condition drifted, the write would silently no-op and leave the trade
re-selectable — a double-sell risk. The retry loop's duplicate inline `EXITING` write
now routes through the one conditional helper (it was the same write in two places, and
only one of them would have received the fix).

### 3.5 Lifecycle (main.py) — closes a race that is latent TODAY

`_eod_self_exit_due` decided the service could exit when
`store.count_active_positions() == 0`. That query counts only
`OPEN/PARTIAL/PENDING_FILL`. The flatten marks trades `EXITING` **early** — before the
retry confirms the fill — and `EXITING` is in none of those three. **So a flatten still
retrying reads as "0 active positions", i.e. as FLAT**, and past 16:00 the eod-self-exit
could take the process down with trades mid-exit. This is latent **today**, independent
of the async worker; the worker only widens it.

- `_eod_self_exit_due` now takes an optional `flatten_in_progress_fn`
  (`KillSwitch.is_flatten_in_progress`) and refuses to fire while it answers True,
  reporting `_ACTIVE_FLATTEN_IN_PROGRESS` (`-2`, distinct from the `-1`
  "not due/unknown" sentinel) so the caller logs "deliberately holding the process
  open" apart from silence. **Fail-safe**: if the gate raises, assume a flatten IS
  running and stay up — never exit on an unknown. The parameter is optional ⇒ the old
  call contract is unchanged.
- `_shutdown` drains the worker **first, before any teardown**. Ordering is the point:
  the worker places exits through the adapter and writes through `store`, and
  `store.close()` is at the bottom of `_shutdown` — draining later would pull the DB
  out from under a live flatten. Nothing below the drain is needed *by* the flatten.
- The worker is **non-daemon**: a daemon thread is killed the instant the process
  decides to exit — mid-flatten, positions open. Non-daemon is the backstop behind the
  gate.

### 3.6 The two shutdown paths (ChatGPT Q5) — why they differ
- **INTERNAL eod-self-exit**: waits indefinitely (bounded in practice by the flatten's
  own 2h deadline). Its gate already waited, so it never reaches `_shutdown`
  mid-flatten.
- **EXTERNAL SIGTERM**: `deploy/systemd/trading-system.service` sets
  `TimeoutStopSec=30` (`KillSignal=SIGINT`) — systemd SIGKILLs at 30s no matter what we
  want. So the drain takes a bounded **15s** grace (`_FLATTEN_DRAIN_GRACE_SEC`, leaving
  the other half of the budget for the WAL checkpoint + close) and escalates
  **CRITICAL** if the flatten outlives it: positions may remain open, and that is the
  operator's call. Pretending we could hold on for 2h would just get us killed
  mid-write.

---

## 4. Invariants preserved (Q6)

The worker runs the **same** `_exit_all_trades_indestructible` — only *where* it runs
changed, plus the write conditions. Preserved **by construction** and evidenced by the
pre-existing suites passing **unchanged**:

- **FIX-180** — 2h bound + 5/15/45 backoff + per-trade 5-min dedup alert (constants
  asserted in test f).
- **FIX-181** — broker-truth sweep + marketable exits + H-5 (`test_h5`, flatten-chain).
- **FIX-190** — reverse-aware close / skip-if-flat (Bug A) + cancel-resting-first
  (Bug E) (`test_p1`, flatten-chain).
- **H-4** — re-derive close side/qty from the current signed broker net every retry
  (`test_h4`, 3 tests).
- **Legacy `cancel_fn` path + KS6 re-run** — unchanged and explicitly re-tested (f3).

---

## 5. Test evidence

**New:** `tests/unit/test_mc8_async_hardkill.py` — **26 tests**, covering (a)–(g).

**9 tests PROVEN RED-on-old** by surgically reverting each behaviour and re-running —
not asserted by inspection:

| Test | Failure on the old code |
|---|---|
| `test_a_hard_kill_returns_immediately` | `hard_kill blocked the caller for 10.03s` |
| `test_a2_the_fill_thread_keeps_working` | fill thread never returned to work |
| `test_c_..._unknown_in_flight` | `UNKNOWN_IN_FLIGHT` overwritten with `EXITING` |
| `test_c1b_..._clean_noop_on_terminal` | trigger ABORT logged as CRITICAL noise |
| `test_c3_..._sl_that_filled_in_the_race_window` | a FILLED SL recorded as `CANCELLED` |
| `test_e_..._flatten_in_progress` | `the process would have exited mid-flatten` |
| `test_e3_..._failsafe` | same |
| `test_e5_real_killswitch_gate` | same, driving the REAL kill switch + REAL flatten |
| `test_g4_worker_cannot_start` | `RuntimeError: can't start new thread` escapes `hard_kill`; flatten dropped |

`test_c2`/`test_c4`/`test_e2`/`test_e4`/`test_b2` are positive controls — they pass on
both, by design (the conditionals must still do their job; back-compat must hold).

> **Process note (worth keeping).** The first draft of `test_a2` measured `is_active()`
> only *after* `hard_kill` returned — so it **passed on the old code** and proved
> nothing: on the old code `hard_kill` returns having already finished the flatten. It
> was rewritten to watch the caller's *next* unit of work, which is the only place the
> starvation is visible. Likewise the first `test_c` asserted the `CLOSED` case, which
> `trg_trades_terminal_status_guard` already protected — it passed on old code too, and
> was rewritten onto `UNKNOWN_IN_FLIGHT`, the genuinely unguarded status. **Both were
> found only by actually running the tests against reverted code.** A test that has not
> been seen to fail is not evidence.

**Results:**

| Suite | Result |
|---|---|
| `test_mc8_async_hardkill.py` (new) | **26 pass** |
| 10 pre-existing kill-switch suites | **86 pass — unchanged** |
| `test_p0_live_day1_fixes.py` | **25 pass** — back to its exact pre-M-C8 count after the §2.1 entry-point change |
| Combined (the 12 kill-switch-touching suites) | **136 pass** |
| `test_main.py` | 4 failures — identical to the pre-change baseline ⇒ **zero new** |
| Full suite | see §6 |

> **The 10-suite baseline was green the whole time and was not enough.** It did not
> contain `test_p0_live_day1_fixes`, which is where the only real regression lived.
> Always run the **full** suite before believing a cross-cutting change — especially
> one that changes a method's synchrony, whose blast radius is every caller in the
> tree, not just the module's own tests.

---

## 6. Full regression — and what it caught

The first full run (19:31–19:45, 13m42s) returned **15 failed / 4,743 passed / 15
skipped**. The known time-gated PC-env set after 16:00 IST is **11**. So **4 were
real** — and none of them were visible in the 10-suite baseline I had been running,
which was green throughout development.

**Attribution — proven by reverting `capital/kill_switch.py` + `main.py` to
`main@16437ae` and re-running each suite, not by assumption:**

| Suite | pre-M-C8 | with M-C8 | Verdict |
|---|---|---|---|
| `test_main` (×4) | 4 F | 4 F | pre-existing |
| `test_fix181` | 1 F | 1 F | pre-existing (uses a `MagicMock` kill switch) |
| `test_interactive_startup` | 1 F | 1 F | pre-existing |
| `test_order_placer_fix061` (×4) | 4 F | 4 F | pre-existing (mock kill switch) |
| `test_phase17_batch2` | 1 F | 1 F | pre-existing |
| **`test_p0_live_day1_fixes`** | **25 pass** | **3 F** | **REAL REGRESSION → fixed (§2.1)** |
| **`test_mc8_async_hardkill::test_d3`** | n/a | flaky | **mine, order-dependent → fixed** |

11 pre-existing + 4 mine = 15. The arithmetic closes.

> **⚠️ A "baseline" that proved nothing.** My first attempt at this attribution used
> `git stash` — but `git stash` only stashes **uncommitted** work, and the M-C8 fixes
> were already committed. Both sides of that comparison therefore contained the
> change, the numbers matched perfectly, and I nearly concluded "all pre-existing".
> A baseline must be taken against the **actual pre-change tree**
> (`git checkout main -- <files>`), and must be *seen* to lack the change
> (`grep -c _dispatch_flatten_worker` → 0).

### 6.1 A pre-existing pollution bug found on the way (NOT fixed — out of scope)

`test_d3` passed alone and failed after `test_main`. Cause:
`main._main_locked` declares `global _log` (`main.py:1538`) and rebinds it
(`_log = get_logger("main")`, `main.py:1626`). `tests/unit/test_main.py` patches
`main.get_logger` to return a **MagicMock** — and when the patch context exits it
restores `get_logger`, **not `_log`**. So once `test_main` has run, **`main._log` is a
MagicMock for the rest of the session**: `addHandler()` is a no-op mock call,
`_log.critical()` records nowhere, and `caplog` reads empty.

**Any later test asserting on main's logging is silently VACUOUS** — it does not fail,
it just stops testing anything. `test_d3` now installs its own probe logger into
`main` for its duration and restores it. **The underlying leak in `test_main` is not
fixed here** (outside M-C8's scope) and is worth its own small cleanup.

### 6.2 Definitive run — on the final tree

Run against `61f5977` with **no uncommitted `.py`** (verified in the run itself — an
earlier run was invalidated because code landed after it started):

```
12 failed, 4748 passed, 15 skipped in 1054.66s (0:17:34)   [20:04–20:22 IST]
```

**Both regressions are gone**: `test_p0_live_day1_fixes` ×3 and `test_mc8::test_d3` no
longer appear. **11 of the 12 are the known time-gated PC-env set** (after 16:00 IST),
each individually attributed against the true pre-M-C8 tree in the table above.

**The 12th — `test_end_to_end_smoke::TestScenario1HappyPath` — is NOT attributable to
M-C8**, and this was established rather than assumed:

- It **passes in isolation on BOTH** my branch and the reverted pre-M-C8 tree (11 pass).
- It **passes with the full preceding collection order** (`tests/core` + `tests/crash_test`
  + `tests/integration` → 54 pass), so no earlier suite is poisoning it.
- It **passed in full run #1**, which already contained the M-C8 worker, lifecycle and
  tests. Only the thread-start guard and drain-honesty fix landed after — neither is on
  a happy path.
- **Decisively: the test never calls `hard_kill`.** Its only kill-switch use is
  `soft_kill` / `resume` / `is_active` (`:362`, `:369`, `:744`). Every line M-C8 changed
  is reachable *only* via `hard_kill` (dispatcher, worker, drain, and both conditional
  writes) or via optional parameters this test does not pass (`_eod_self_exit_due`,
  `_shutdown`). **M-C8's code is unreachable from it.**
- The flake shape is explicit in the test: `_wait_for_any_signal(timeout=3.0)` and
  `_wait_for_signal_status(timeout=6.0)` — hard wall-clock timeouts on async pipeline
  processing. Run #1 finished in **13m42s** and it passed; this run took **17m34s**
  (~28% slower under load) and it timed out.

⇒ A **pre-existing, load-sensitive timing flake**. Not reproduced in isolation, so it is
reported as an observation, not a diagnosis — but it cannot be caused by this change.
Worth its own look (raise the timeouts or make the wait event-driven); **not an M-C8
blocker**.

**Bottom line: ZERO failures attributable to M-C8.**

---

## 6.3 ⚠️ DEVIATIONS FROM THE APPROVED INSTRUCTION — need ratification

Author ≠ approver. Three things differ from what was signed off. None weakens a
safety property, but they are the approver's to accept, not mine:

1. **"All existing tests pass unchanged" is FALSE** (§2.1). 3 tests in
   `test_p0_live_day1_fixes.py` needed a one-line entry-point change because the
   design's premise about the test tree was wrong. Assertions verbatim; the change
   aligns them with their own siblings. **This is the deviation that matters — please
   confirm the reasoning.**
2. **Single-flight uses a DEDICATED lock, not the KS4 RLock.** The instruction said
   "INSIDE the lock … set the state machine + worker handle; run the single-flight
   check". Reusing the RLock would put a join and a state gate that `is_active()`
   depends on behind the same lock M-C4 just fixed. The instruction's actual
   requirement — the kill STATE is tripped before the worker spawns — is preserved
   (state is tripped under `self._lock` at `:527`, dispatch happens at `:550`).
3. **A thread-start fallback that runs the flatten INLINE** (§3.2, test g4). Not in
   the design; found in self-review. It re-introduces caller blocking in the
   can't-spawn-a-thread case only.

Also worth a decision, but out of M-C8's scope: the **`test_main` mock-logger leak**
(§6.1) silently voids any later test's assertions on `main`'s logging. Small fix,
should be its own commit.

## 7. Deploy

**NOTHING PUSHED.** `mc8-async-hardkill-16jul` deploys **off-market**, consolidated with
the M-C cluster (M-C4 `6c77525` + M-C8, then M-C5/M-C6) into one validated deploy — or
as its own increment; decide at deploy time. Do **not** disturb the already-deployed
`11abebb`.

This is the emergency-exit path: the cluster deploy should get a **fresh combined
regression** and, ideally, a **sandbox hard_kill drill** before any reliance on it.

**Rollback:** revert the two fix commits (`5744776`, `2d6d005`); schema-free, no data
migration.

## 8. Not in scope
M-C5 / M-C6 (still open — mitigated/latent respectively). The F1 enforce flip. Any
change to the legacy `cancel_fn` path.
</content>
