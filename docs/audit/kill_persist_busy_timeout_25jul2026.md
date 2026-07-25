# The 30 s `busy_timeout` inside the kill lock

**25-Jul-2026. READ-ONLY — nothing changed.** Independent of F2: this dominates *every* kill
path, including the automated ones that fire with nobody watching.

---

## The two answers

1. **Has 30 s ever been approached in production? THE CEILING HAS NEVER BEEN HIT — and the
   actual wait is UNMEASURED.** Across the entire log history there are **zero** occurrences
   of `database is locked`, `SQLITE_BUSY`, `database table is locked`, or `OperationalError`
   — so no write has ever *exhausted* the 30 s budget. ⚠️ **But that only bounds the tail:**
   a 5 s wait succeeds silently and logs nothing, and **`_persist_state` is not timed
   anywhere**. So: *the ceiling is theoretical; the distribution below it is simply unknown.*
2. **What is blocked while it waits? ⭐ THE WHOLE `KillSwitch` API — INCLUDING
   `is_active()`, WHICH THE ORDER PATH CALLS AT THE LAST MILE.** `soft_kill()` holds
   `self._lock` **across** `_persist_state()` (`:523-541`), and `is_active()` acquires the
   same `self._lock` (`:470`). This is *not* confined to `/health`.

---

## B1 — Measured vs theoretical

```
grep across ALL logs, entire history:
  "database is locked"        0 files, 0 lines
  "SQLITE_BUSY"               0 files, 0 lines
  "database table is locked"  0 files, 0 lines
  "OperationalError"          0 files, 0 lines
kill activations actually recorded:   25
```

**25 real kill persists, zero timeouts.** The 30 s ceiling has never been reached.

⚠️ **Anti-vacuity, stated plainly:** absence of a `SQLITE_BUSY` error proves only that the
timeout was never *exceeded*. It says nothing about whether waits of 1 s, 5 s or 20 s have
occurred — those succeed silently. `capital/kill_switch.py` contains no `perf_counter`/
`monotonic` timing around `_persist_state`. **⇒ "It has never blocked" is NOT established;
what is established is "it has never blocked for ≥30 s."** Those are different claims and
only the second is evidenced.

## B2 — Is there actually a competing writer?

**Yes — and across processes, which is the part that matters.** SQLite WAL lets readers
proceed during a write, so readers are irrelevant here; only **writer-vs-writer** contends.

| writer | process | when |
|---|---|---|
| the trading service | one process, many threads (`signal_processor` ≤5, entry-gate pool, `smart_tgt` ≤2, feed consumer, watchdogs) | market hours |
| **33 monitored cron jobs** writing `cron_heartbeat` via `insert_cron_heartbeat` | **separate processes** | throughout |
| ⭐ **two jobs on `*/5 9-15 * * 1-5`** | **separate processes, every 5 minutes, 09:00–15:59** | **exactly overlapping the window in which automated kills fire** |
| `scripts/reconcile_pnl.py`, `scripts/system_manager.py` — which **themselves call a kill** | **separate cron processes** | EOD |
| ops dashboard | separate process | **read-only** (`_ro(cfg)`) — does not contend |

⇒ **This is not a single-writer system, so the 30 s ceiling is reachable in principle.** It
is not reached in practice because the competing writes are single-row heartbeat `INSERT`s
that complete in milliseconds. **The exposure is real but the observed contention is
negligible** — which is exactly why it has never surfaced.

## B3 — ⭐ The automated paths, and what is blocked behind them

**25 kill call sites across 12 modules, and effectively ALL are automated** — none requires
an operator:

```
broker/token_monitor.py      token expiry
capital/drift_handler.py ×2  capital drift (SOFT and HARD)
capital/fund_manager.py  ×3
data/live_feed.py        ×3  LIVEFEED_QUEUE_FULL · LIVEFEED_RECONNECT_EXHAUSTED
                             LIVEFEED_CONSUMER_THREAD_DEAD  ← re-armed by B1 today
orders/order_reconciler.py ×3 · orders/order_placer.py ×2
orders/eod_squareoff.py · orders/cnc_gtt_monitor.py
scripts/reconcile_pnl.py · scripts/system_manager.py   (separate cron processes)
main.py ×7                   boot/wiring paths
```

**What blocks behind a slow persist:**

- **`is_active()` — the last-mile entry check.** `soft_kill` holds `self._lock` from the
  state test through `_persist_state` (the DB write) and only releases before publishing.
  `is_active()` takes that same lock. **So for the duration of the write, every thread
  asking "may I place this order?" waits.**
- `current_state()`, `status()`, and the other lock-taking readers — same lock.
- (`/health` on `:8080` is a *third-party* casualty via `threads=1`, covered in the
  companion F2 report — it is the least of the three.)

**⭐ The codebase already identified this exact hazard — for a different caller.** The M-C8
comment at `kill_switch.py:252-258` explains why the async-flatten worker gets its **own**
lock:

> *"Reusing `self._lock` would re-create the M-C4 defect in a worse place — a 2h join under
> the lock that gates `is_active()` would block the last-mile order check for the entire
> flatten."*

**So "the lock that gates `is_active()` must not be held across slow work" is an
already-articulated principle here.** `_persist_state` is a DB write with a 30 s ceiling
held under precisely that lock. The 2 h join was engineered out; the 30 s write was not.

⚠️ **Is that actually a defect? Not obviously — and this is the honest part.** KS9 requires
*persist-first*: if the write fails, in-memory state must NOT change, or a kill could be
believed-active while unrecorded. Moving the write outside the lock reintroduces that race.
**The current shape trades worst-case latency for atomicity, deliberately.**

**And the failure mode is arguably fail-safe:** while `is_active()` blocks, no order can be
placed — the entry path stalls rather than proceeding unchecked. A kill that is slow to
record also stalls what it was trying to stop. *Slow, not dangerous.* The cost is bounded
delay, not an unguarded entry.

## B4 — Is 30 s the right value?

`PRAGMA busy_timeout = 30000` is set for **every** connection
(`core/db_connect.py:87`, `:108`, `:135`) — it is a global default, not a kill-path choice.

**What it protects against:** a writer transiently losing the race to another writer.
Without it, SQLite returns `SQLITE_BUSY` immediately and the caller fails. 30 s is generous
for a system whose competing writes are single-row heartbeat inserts — it is sized for "never
fail spuriously," not for "fail fast."

**Does the kill path specifically want a shorter one?** There is a genuine argument that it
does: a kill that cannot record itself within, say, 2 s is in an abnormal state, and failing
loudly may beat blocking `is_active()` for 30 s. **But** a shorter timeout means the persist
can *fail*, and under KS9 a failed persist means **the kill does not take effect at all** —
trading the current "slow halt" for a possible "no halt". ⛔ **That is a real trade-off and
explicitly NOT decided here.** Note it is not a one-line change either: `busy_timeout` is
per-connection and global, so a kill-specific value needs a distinct connection or a
per-call override.

---

## Verdict

**CLOSES AS THEORETICAL-BUT-REAL — and it should not be closed as "no issue".**

- The ceiling has **never been hit** in 25 real kills. ✅
- Competing cross-process writers **do exist**, including every 5 minutes during market
  hours, so it is **not** structurally impossible. ⚠️
- The blast radius, if it ever were hit, is **larger than the /health block that prompted
  this** — it reaches `is_active()` on the order path.
- The wait is **uninstrumented**, so today nobody would know if it were degrading.

**The cheapest thing that would convert this from unknown to known is not a fix at all —
it is one timing log around `_persist_state` (a WARNING above, say, 1 s).** ⛔ Not built,
not recommended as urgent; recorded because "we would not know" is the actual finding.
