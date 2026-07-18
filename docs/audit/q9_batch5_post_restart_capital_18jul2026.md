# Q9 BATCH 5 — POST-RESTART CAPITAL RESTORATION, WIRED

**Date:** 18-Jul-2026 (Saturday, IST, off-market; system DOWN since Fri 17-Jul, book flat)
**Basis:** `7f0bf45` (tag `deploy-18jul-q9-batch4` → `f177c60`), schema v44
**Scope:** TEST-ONLY. No production file changed.
**Closes:** Q9 #5 — and with it, the Q9 coverage programme (see §10).

---

## 0. TL;DR

**Restoration is sound.** The two highest-severity questions both come back clean, and both for
structural reasons rather than luck:

| # | Question | Answer |
|---|---|---|
| **a** | Does the day's realized P&L survive a restart? | ✅ **YES — by construction.** It is not held in memory at all |
| **b** | Does kill state survive a restart? | ✅ **YES** — via `KillSwitch.__init__` → `_load_state_from_store()` |
| **c** | Are reservations / used capital restored? | ✅ OPEN/PARTIAL yes; **PENDING deliberately not** (correct — see §4) |
| **d** | Is restoration idempotent? | ✅ **Boot-level yes** (the case that exists). Within one instance, no — unreachable |

**No STOP-AND-REPORT.** Nothing in this layer can fire under current production config.

Two things worth Rama's attention anyway:

> ⚠️ **Monday's 08:15 boot exercises only the TRIVIAL case.** Book flat, kill clear, fresh day
> ⇒ Phase 1 replays 0 trades, Phase 2 carries 0 P&L rows. Rehydrate is a **no-op**. Everything
> proven below stays unproven *in production* until a mid-day restart with real state. (§9)

> ⚠️ **Production runs `--mode live`, but every wired test here runs in paper.** The LIVE capital
> seed — `broker.get_margins().net - today_realized_pnl_carryover()` (M-C1) — has **no
> integration coverage at all**, only unit coverage. Now pinned structurally. (§7)

And one process result: **for the second batch running, the planted breaks caught a vacuous
harness of my own making** — the first draft reused the fixture's `KillSwitch`, which made every
kill assertion meaningless (§8).

---

## 1. §A1 — THE RESTORE MAP

Boot order, with file:line:

```
main.py:1768   kill_switch.clear_stale_state(today)        prior-day kills auto-clear
main.py:2243   PAPER seed = selected_account.paper_capital
main.py:2255   LIVE  seed = broker.get_margins().net - fund_manager.today_realized_pnl_carryover()
main.py:2259   fund_manager.initialize(seed)               H-4 guard: 2nd call = WARNING + no-op
main.py:2286   fund_manager.rehydrate_from_open_trades()   CapitalStateInconsistent -> exit 3
main.py:2299   paper adapter capital re-sync (FIX-156)
main.py:3207   order_reconciler.reconcile_once()           SYNCHRONOUS, against BROKER TRUTH
```

`rehydrate_from_open_trades` (`capital/fund_manager.py:1626`) has three phases:

| Phase | What it does | Line |
|---|---|---|
| 1 | Per-open-trade replay of the RESERVE/COMMIT ledger chain, for **OPEN/PARTIAL only** | `:1685-1689` |
| 2 | Today's realized-P&L carryover: for each `RELEASE_USED` row, `bucket avail += pnl`, `_total += pnl` | `:1703-1711` |
| 3 | Invariant check **once** (per-step checks would false-positive on mid-flight states) | `:1718` |

Quantity by quantity:

| Quantity | Restored from | By | On missing/stale |
|---|---|---|---|
| `total` | seed + Phase 2 P&L | `initialize` + `:1710` | seed is broker (live) or config (paper) |
| `available` | bucket split, then Phase 1/2 | `initialize` + `:1708` | — |
| `reserved` | fm_ledger RESERVE replay | Phase 1 `:1688` | trade skipped → logged anomaly |
| `used` | fm_ledger COMMIT replay | Phase 1 `:1688` | trade skipped → logged anomaly |
| **day's realized P&L** | **NOTHING — never in memory** | `get_daily_realized_net_pnl` recomputes from `fm_ledger` per read (`state_store.py:2432`) | n/a |
| open positions | `trades` (OPEN/PARTIAL) | `get_all_open_trades()` | — |
| reservations | **OPEN/PARTIAL only** | Phase 1 | PENDING → not replayed, resolved by the reconciler |

---

## 2. §A2a — THE DAY'S REALIZED P&L (highest severity)

**It survives, and the reason is that there is nothing to lose.**

`FIX-051` removed the in-memory `_daily_pnl` float. `get_daily_realized_net_pnl(today)` recomputes
from `fm_ledger` on every read, and **both** halves of the daily loss limit read it that way:

- `fund_manager.py:1279` — the pre-trade gate
- `fund_manager.py:1507` — `get_snapshot().daily_realized_pnl` (`FIX-051: read from SQL`)

Runtime-confirmed across a restart: `-1104.8000000000002` → `-1104.8000000000002`, identical.

**Proven further, not just preserved:** the limit still *enforces* on the restored value — a
signal driven after a restart terminates `REJECTED_DAILY_LOSS` **specifically**, with the
negative half (below the limit ⇒ not that reason) also asserted. Every threshold is expressed
**relative to the reader's own returned value** (batch 1's discipline), never as a rupee figure,
because the reader's contract changes under E4/W10.

⚠️ **Two quantities that must not be conflated.** Phase 2 applies `SUM(pnl_delta)` to `_total`;
the reader returns `SUM(pnl_delta) - SUM(costs)`. Today those differ by exactly the E4/W10
double-subtraction (observed: `_total` moved by −1052.40 while the reader read −1104.80, a
difference of 52.40 = costs). Both behaviours are correct for their own purpose; a test that
equated them would be wrong.

---

## 3. §A2b — KILL STATE

**It survives — but not via the function one would expect.**

`clear_stale_state` (`kill_switch.py:252`) cannot be the restorer: its first act is
`if self._state == KillState.INACTIVE: return False`, and a freshly-constructed `KillSwitch` has
exactly that. The restoring mechanism is **`KillSwitch.__init__` → `_load_state_from_store()`**
(`kill_switch.py:250`, KS3, "Audit Issue #18 fix"), which reads the single `kill_switch_state`
row, logs CRITICAL if non-INACTIVE, and populates `_state` / `_triggered_at`.

Only then does `clear_stale_state` have anything to act on, and it implements the asymmetry:

- **same-day** kill → `triggered_date >= today` → **persists** ✅
- **prior-day** kill → cleared, audited to `system_events` as `KILL_AUTO_CLEARED` (Rama's
  2026-06-20 headless decision: a new trading day always starts clean)

Both halves are asserted. The negative half matters: without it, "the kill survives a restart"
would be indistinguishable from "the kill can never be cleared", which would block every
subsequent trading day.

**Also proven:** the restored kill still *blocks* a real placement — driven through
`order_placer` directly (with a kill armed the receiver rejects the POST outright, an earlier
and coarser gate, which would prove the wrong check), asserting `kill_switch_active` and an
**empty broker call record**, with no capital left reserved.

---

## 4. §A2c — RESERVATIONS AND USED CAPITAL

`get_all_open_trades()` returns **only OPEN or PARTIAL**. A `PENDING_FILL` trade — an order
placed but not confirmed filled — is therefore **not replayed**, and its reservation is **not
restored**.

**This is correct, and it is the safer direction.** From the DB alone the system cannot know
whether the broker filled that order. Restoring the reservation would leave capital committed to
an order the broker may never have accepted. Instead the capital is **returned to available**,
and `main.py:3207`'s synchronous `reconcile_once()` adopts-or-fails the in-flight entry against
**broker truth** — consistent with the standing rule that positions come from the broker, never
from DB state.

Measured across a restart with one OPEN position and one unfilled order:

```
before : total 498947.60  avail 492427.60  reserved 2520.00  used 4000.00
after  : total 498947.60  avail 494947.60  reserved    0.00  used 4000.00
delta  : total    ±0      avail +2520.00   reserved -2520.00 used    ±0
```

`used` (the OPEN position) restored exactly; the PENDING reservation released, not lost; `total`
untouched. Asserted **quantity by quantity** — an aggregate match can hide two compensating
errors.

---

## 5. §A2d — IDEMPOTENCY

**The production case is idempotent.** A systemd restart loop means a new process, hence a new
`FundManager`, a fresh `initialize()` (which resets the buckets) and a fresh replay of the same
unchanged DB rows. Two full boot cycles produce **identical** state across every quantity —
asserted field by field.

**Within one instance it is not**, and that asymmetry is real: `initialize()` has an explicit
double-call guard (**H-4**: WARNING + no-op, `fund_manager.py:414-420`); `rehydrate_from_open_trades()`
has none. Calling it twice on one instance re-applies Phase 1 and Phase 2 (observed: `used`
4000 → 8000).

Applying the **§0.1 LIVE/LATENT test**: `main.py:2286` is the sole call site, there is no retry
loop, and a restart is a new process ⇒ **not reachable**. Recorded and pinned by a test (which
also asserts the single call site), so a second invocation cannot be added silently.

---

## 6. §A3 — BROKER TRUTH

The design splits cleanly along what the DB can and cannot know:

- **Settled state** (OPEN/PARTIAL positions, today's realized P&L) → replayed from the DB. It is
  settled precisely because the broker already confirmed it.
- **Uncertain state** (in-flight/PENDING entries) → **not** replayed; resolved by
  `order_reconciler.reconcile_once()` against the broker.
- **Live capital seed** → the broker (`get_margins().net`), not the DB.

**Can restoration act on DB state alone in a way that drives an order? No.** Rehydrate is pure
in-memory capital arithmetic — it places nothing, cancels nothing, flattens nothing. Its only
escalation is defensive: a failed invariant raises `CapitalStateInconsistent`, which `main.py`
catches and turns into **exit 3** (fail-closed, trading does not resume).

---

## 7. §B6 — PARITY, AND A REAL COVERAGE BOUNDARY

**One restore path, two seeds — not two implementations.** Both modes call the same
`initialize()` and the same `rehydrate_from_open_trades()`; `rehydrate` contains no
`paper_mode`/`is_paper`/`live_mode` branch. This is **not** the P1 `/health` shape: there is no
duplicated logic that could drift, and `TestParity` pins that structurally.

The seeds differ, deliberately:

| Mode | Seed | Why |
|---|---|---|
| PAPER | `selected_account.paper_capital` (`main.py:2243`) | static; correctly excludes today's P&L |
| LIVE | `broker.get_margins().net - today_realized_pnl_carryover()` (`main.py:2255`) | broker's net **already includes** today's P&L, and Phase 2 re-adds it — M-C1 |

The subtraction and Phase 2's re-addition share **one** row-selection helper
(`_today_release_used_pnl_rows`, `fund_manager.py:1757`), so they cancel to `broker.net` **by
construction**. `today_realized_pnl_carryover`'s docstring names paper as "the parity reference".

> ⚠️ **THE BOUNDARY.** Production runs `main.py --mode live`, but the `wired_system` fixture is
> `paper_mode=True`. **Every wired test in this batch exercises the PAPER seed.** The LIVE seed
> has **no integration coverage**; its only coverage is unit-level
> (`tests/unit/test_mc1_live_seed_rehydrate.py`).
>
> Removing the M-C1 subtraction would silently inflate live reservable capital on a warm
> restart, and no wired test would notice. It is therefore now **pinned structurally** — a test
> fails if the subtraction disappears or if the two stop sharing the row-selection helper.
> Verified to bite (§8, Plant D).

---

## 8. §B8 — PROVEN TO BITE

Value-shaped plants, not crashes. Each: plant → run → failure naming the quantity → restore →
green.

| Plant | Break | Result |
|---|---|---|
| **A** | Phase 2 carries no P&L rows | 2 failures — `Phase 2 replayed no P&L rows`, `expected >= 2 P&L rows carried, got 0` |
| **A2** | Phase 2 carries **90%** of the P&L | **1 failure** — `TOTAL not restored: 490952.73 -> 491857.46` |
| **B** | Phase 1 replays no open trades | 3 failures — `Phase 1 replayed 0 trades, expected 1` |
| **C** | `_load_state_from_store()` deleted (KS3) | 3 failures — `the REBUILT KillSwitch loaded KillState.INACTIVE from the store, expected SOFT_KILL — a restart would clear a same-day kill and BE the bypass` |
| **D** | M-C1 carryover subtraction removed from the live seed | 1 failure — names M-C1 and the double-count |

**⭐ A2 is the meaningful one.** Carrying 90% of the P&L keeps every internal relationship
intact — the global identity still holds, no column is malformed — so **production's own runtime
guards fired 0 times** (measured: `CapitalInvariantViolation` = 0, `CapitalStateInconsistent` =
0) while the test failed naming the quantity and both values. That is the E4/W10 class, in
restart restoration. (Plant A, by contrast, tripped a row-count guard before the value assertion
ran — informative, but a weaker proof, which is why A2 exists.)

**⭐⭐ PLANT C EXPOSED A VACUOUS HARNESS OF MY OWN.** The first draft of `_restart()` rebuilt the
`FundManager` but **reused the fixture's `KillSwitch`**. Since `is_active()` reads only the
in-memory `_state`, every kill assertion was checking a flag the test had never restored — the
tests passed, and **would have passed with the KS3 load deleted**. Rebuilding the `KillSwitch`
inside `_restart()` fixed it, and Plant C now fails 3 tests. This is the second consecutive batch
in which planting found a hole in the tests rather than in production.

After restore: `capital/`, `orders/` and `main.py` all show **0 modified files**; 13 passed.

---

## 9. §C — REACHABILITY

| Element | Verdict | Evidence |
|---|---|---|
| `rehydrate_from_open_trades()` | ✅ **REACHABLE** | `main.py:2286`, unconditional (inside a try only for the fail-closed exit-3 path). Not flag-gated |
| `initialize()` | ✅ **REACHABLE** | `main.py:2259`, every boot |
| Phase 1 (open-trade replay) | ⚠️ **CONDITIONAL** | Runs only when OPEN/PARTIAL trades exist at boot — i.e. a **mid-day restart with a position open** |
| Phase 2 (P&L carryover) | ⚠️ **CONDITIONAL** | Runs only when today has `RELEASE_USED` rows — a **mid-day restart after at least one close** |
| Phase 3 (invariant → exit 3) | ✅ REACHABLE | Runs every boot; fires only on corruption |
| KS3 kill-state load | ✅ **REACHABLE** | `KillSwitch.__init__:250`, every boot |
| `clear_stale_state` clearing | ⚠️ **CONDITIONAL** | Only with a **prior-day** kill persisted (has occurred: the 17-Jul boot auto-cleared a 16-Jul SOFT_KILL) |
| LIVE seed + M-C1 carryover | ✅ **REACHABLE** (production is `--mode live`) | `main.py:2255`. **Not covered by any integration test** — §7 |
| Double-rehydrate double-count | ❌ **UNREACHABLE** | One call site, no retry loop; a restart is a new process |

**§C2 — what would have to change:** Phase 1/2 need a restart while the book is non-flat or after
a close (an operator restart, a crash, or a systemd auto-restart during market hours). The
double-count needs a second `rehydrate_from_open_trades()` call to be added to `main.py`. *Stated,
not recommended.*

### ⭐ §C3 — WHAT MONDAY'S 08:15 BOOT DOES AND DOES NOT EXERCISE

Monday is a **cold boot on a fresh trading day with a flat book** (verified: 0 open positions,
kill-switch INACTIVE, book flat since Fri 17-Jul). Therefore:

| Path | Monday |
|---|---|
| `initialize(seed)` | ✅ runs — live seed = `broker.net - 0` |
| Phase 1 | **0 replays** (no open trades) |
| Phase 2 | **0 P&L rows** (no closes yet today) |
| Phase 3 invariant | ✅ runs, trivially |
| KS3 load | ✅ runs, finds INACTIVE |
| `clear_stale_state` | no-op (already INACTIVE) |

⇒ **Rehydrate is a no-op on Monday.** Monday proves the boot does not *crash* on the restore
path; it proves nothing about restoration itself.

**Remaining UNPROVEN in production after Monday:** Phase 1 replay of a real open position ·
Phase 2 realized-P&L carryover · the M-C1 live-seed cancellation · the same-day-kill-survives
path. All are proven in the fixture; none will have run for real until a **mid-day restart with
live state**.

---

## 10. Q9 — FINAL TALLY

With #5 closed, the Q9 coverage programme is **complete** except the coverage-matrix metadata
back-fill.

| Layer | Wired? | Reachable in production? |
|---|---|---|
| Daily loss limit (both halves) | ✅ batch 1 | ✅ |
| Global `max_open_positions` gate | ✅ batch 1 | ✅ |
| Kill-switch last-mile re-check (TOCTOU) | ✅ batch 2 | ✅ |
| Capital invariant, every lifecycle stage | ✅ batch 3 | ✅ (production asserts it itself) |
| E4/W10 value contract | ✅ batch 3 (strict xfail) | ⛔ fix unpushed, sign-off-gated |
| Sizing: concentration · >cap rejection · tier multiplier · min-lot floor | ✅ batch 4 | ✅ (4 of 15 guards) |
| Sizing: risk · capital · value-cap · lot-skew · BELOW_MIN · FLAT · explosion · zero-multiplier · 2× ceiling | ✅ batch 4 | ❌ dead by algebra/config |
| Post-restart capital replay (Phases 1–3) | ✅ **batch 5** | ⚠️ conditional (mid-day restart) |
| Post-restart kill state (KS3 + stale-clear) | ✅ **batch 5** | ✅ / ⚠️ conditional |
| Post-restart LIVE seed (M-C1) | ⚠️ **unit only**, pinned structurally | ✅ |

---

## 11. CARRY-OVER CORRECTIONS

**C-1 — DONE.** `q9_batch4_sizing_floors_caps_18jul2026.md` §10 cited the superseded **7.34×**
LONG skew; corrected to **9.54×** (authoritative, via `StrategyConfig.direction`) with the
supersession noted so it cannot propagate into D1. The remaining "7.34×" in memory is an
intentional explanation of the earlier under-count, not a live figure.

**C-2 — DONE, RESULT UNCHANGED.** Batch 3's E4/W10 evidence was re-verified in a worktree
**seeded** with the git-ignored runtime data (`config/instruments.csv`), on
`e4-w10-pnl-contract@ad34ee4`:

```
reader(-1052.4000) - truth(-1052.4000) = 0.0000     costs = 52.4000
2 failed, 4 passed        (main control: 5 passed, 1 xfailed — XFAIL, not XPASS)
```

**Delta still EXACTLY `0.000000`**; the structural invariants still pass; the 2 "failures" are
the design working (the strict xfail XPASSes, and the exactness guard correctly objects the delta
is no longer `−costs`). Identical to the original bare-worktree run.

**Why the defective environment did not affect it:** the missing `config/instruments.csv` breaks
`main.py`'s InstrumentCache, which only boot-path *unit* tests build. The E4/W10 evidence runs on
the `wired_system` integration fixture over a `tmp_path` DB and never touches it. Read-only
throughout; nothing merged, cherry-picked or committed to that branch; worktree removed.

**⇒ The evidence under Rama's pending E4/W10 risk-posture decision stands unchanged.**

---

## 12. REGRESSION + DEPLOY

### 12.1 Regression — same window, correctly-seeded base

Both runs 20:32–21:0x IST. Baseline taken with the **sharpened rule** (batch 4's finding, now
standing): the base ran **in the MAIN TREE** with the new file moved aside, so all git-ignored
runtime data (`config/instruments.csv`) was present. `git stash` was not used (stash list
verified empty).

*One trap worth recording:* after moving the source aside, a stale
`__pycache__/test_q9_post_restart_capital_wired*.pyc` still matched a grep for the module name.
It was removed before the run, so "my file is absent" is a fact rather than a half-truth.

| Run | Failed | Passed | Skipped | xfail | **Collected** |
|---|---|---|---|---|---|
| **BASE** — main tree, ignored data present, new file absent | 12 | 4969 | 5 | 1 | **4987** |
| **MINE** — `a9758b3` | 12 | 4982 | 5 | 1 | **5000** |

- **`comm -13` EMPTY ⇒ ZERO ATTRIBUTABLE.** `comm -23` also empty — the two failure sets are
  **identical**, the cleanest attribution in the programme so far.
- **Totals reconcile exactly: 4987 + 13 = 5000.**
- **`xfailed = 1`, `XPASS = 0`** ⇒ batch 3's E4/W10 contract xfail is still genuinely xfailing.
- **Zero failures from the new file.**
- All 12 are the documented PC-env / calendar-gated set: `test_main.py` (4),
  `test_order_placer_fix061` (4), `test_daily_trade_review` (Saturday),
  `test_interactive_startup` (`main.py:1686-1695` service window), `test_fix181`,
  `test_phase17_batch2`.

### 12.2 Deploy verification

| Step | Result |
|---|---|
| Tag `deploy-18jul-q9-batch5` | → `a9758b3` |
| Fresh VM backup `pre_deploy_q9b5_20260718.db` | **SOUND**: `quick_check=ok` · schema **44** · **361** trades · **0** FK |
| Push `main` + tag | `7f0bf45..a9758b3`, post-receive checkout OK |
| Bare HEAD == re-derived local HEAD | `a9758b3` == `a9758b3` ✓ **PC == VM** |
| Code-identity delta vs tag | **0 non-markdown files** |
| Schema | **v44 unchanged — no migration** |
| Integrity / FK | `quick_check=ok` · **0** violations · 361 trades unchanged |
| Kill-switch state | **INACTIVE** (unchanged) |
| Services | `trading-system` inactive (expected) · `alert-watcher` active · `gui-dashboard` active |
| **New tests ON THE VM** | **13 passed** |

Test-only: **no runtime behaviour change**. ⚠️ Monday 20-Jul 08:15 boots this tree — see §9 for
what that boot does and does not exercise.

---

## 13. WHAT WAS NOT DONE (deliberately)

- **No production code change.** No schema change, no flag flip, no destructive CTs, no restart
  of the production system.
- **E4/W10 not fixed, merged or deployed** — C-2 was read-only evidence.
- **`PerformanceAllocator` not wired** (D1) · **`reports/daily_report.py` not touched** (queued
  separately: `:464` reports 3,098 "CAPITAL" rejections against a true count of 0).
- **The rehydrate double-call asymmetry left as-is** — unreachable; documented and pinned.

---

*Read-only investigation; test-only build. No production code, config, schema or flag changed.*
