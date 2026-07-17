# BATCH-3 — X7 + X3 + X5 (17-Jul-2026)

**Scope:** the last three batch-safe items. **2 shipped, 1 deferred on its own mandatory
precondition.** System down, off-market waived, book flat.

| Item | Outcome | SHA |
|---|---|---|
| X7 — raw exec-log `trade_id` NULL | **SHIPPED** | `b842c7c` |
| X3 — retire the duplicate `daily_report` generator | **DEFERRED** — survivor does not cover it | — |
| X5 — `instance_lock` single-instance guard | **SHIPPED** | `e2f34a1` |

Two of the three items' stated premises were wrong. Both were checked before building,
per [[feedback-verify-the-finding-premise]]; the corrections are the substance of this
report.

---

## X7 — the exec log could never join to trades (`b842c7c`)

### Finding — the ticket understated it by five columns

`order_execution_log.parent_trade_id` is NULL on **262/262** live rows. The cause is not a
missing field, it is the **lookup key**.

`orders`' primary key is the **broker** order id. `orders/order_manager.py:290-291` says so
outright:

> Insert a new order row. broker_order_id is the PK (OMgr3).
> internal_order_id is NOT stored — kept in memory by order_placer.

`slippage_recorder._enrich_order` queried `FROM orders WHERE order_id=?` and passed
`ev.internal_order_id or ev.order_id` — the `ord_<hex32>` id, which is never in that column.
**The lookup matched nothing, every time, for every row.**

Live evidence (read-only, VM, per the migration-on-open rule):

```
exec-log order_id shape            : 262 internal-shaped (ord_%), 0 broker-shaped
rows joinable to orders            : 0
NULL parent_trade_id               : 262/262
NULL order_type / qty / signal_id  : 262/262 each
NULL strategy_name / order_timestamp: 262/262 each
is_partial = 0                     : 262/262
```

`trade_id` was **one of six** columns the same dead key silently dropped. Fixing only
`trade_id` would have left the dead lookup in place, so the key is the fix.

The sibling table written by the same handler is hit identically —
`market_execution_context`: **262/262 NULL `trade_id`, 262/262 NULL `leg`**, which made its
`idx_mec_trade` index an index over nothing. The one key fixes both tables.

### The `leg` column was worse than NULL

`leg or "ENTRY"` defaulted every unresolved lookup to `ENTRY`. Live: **155 ENTRY + 79 SL +
50 TGT + 21 EOD** completed orders, but **262/262 exec rows say ENTRY**. ~107 exit fills
were silently relabelled as entries — a **wrong answer, not a missing one**, and invisible
precisely because it looked plausible. Unresolved now writes `UNKNOWN` (the column is NOT
NULL, so it must write something; it must not write a lie).

### Who this unblocks — both consumers were dead, neither had adapted

| Consumer | State before |
|---|---|
| `db_reader.py:1397` — per-trade drill-down, `WHERE parent_trade_id = ?` | 0 rows for **every** trade |
| `db_reader.py:1063` — `execution_log_today`, `WHERE order_timestamp LIKE ?` | the recorder never wrote `order_timestamp` **at all** |

`order_timestamp` now comes from `orders.placed_at` — the same row the fixed lookup already
reads. Nothing routed *around* the bad data, so restoring real values changes no reader's
behaviour. This is the opposite of E4/W10, where the readers **had** adapted and a one-sided
fix would have regressed. Checked before building, not after.

### Scope / safety

Confined to `slippage_recorder.py`, the async best-effort observability subscriber that
"can never block or affect trade execution". **The order path is not touched**: `_WatchEntry`
carries no `trade_id`, so making `order_monitor` publish one would have meant editing the
order path for a reporting column — declined per the escalation valve. No schema change
(`_OEL_COLS` already whitelists `order_timestamp`). Parity: both publishers set
`broker_order_id` and paper orders take the same `orders` PK, so live and paper fix
identically, no mode fork.

### Tests — `tests/unit/test_slippage_recorder.py` (25, was 21)

The old `_FakeStore` answered **any** `"FROM orders"` query regardless of key, and every test
set `ev.trade_id` by hand — which no real publisher does. That is why a 100%-broken lookup
sat under a green suite. The fake is now key-aware, and fills are built by
`_production_fill()`, shaped exactly as `order_monitor` / paper-synth emit them.

**RED-on-old proven** on the true pre-change file (`git checkout HEAD -- <file>`,
grep-confirmed absent, never `git stash`): **4 failed / 21 passed, rc=1** — failing as
`assert None == 10` on qty, on the join key, and on the leg mislabel. **GREEN: 25 passed.**
84 pass across recorder + order_manager + order_monitor.

---

## X3 — DEFERRED: the survivor does not cover the generator (no commit)

The instruction made this conditional: *"If it is NOT provably dead / the survivor doesn't
cover it → STOP and defer (do not delete on suspicion)."* **The condition is not met.**

### The two-generator overlap

| | `daily_report` (16:05) | `daily_trade_review` (16:07) |
|---|---|---|
| Sheets | Dashboard, Signals, Orders, **Capital**, **Candles**, **Telegram**, Strategy | Dashboard, Reconciliation, Orders, Signals, Strategies, Slippage, Config |

Not covered by the survivor: **Capital** (no dedicated sheet, only a Dashboard summary),
**Candles** (`candle`/`Candle` — **0 hits** in the whole module), **Telegram**.

### The survivor says so itself

`reports/daily_trade_review.py:2112` emits, in its own Dashboard coverage panel:

```python
{"section": "Telegram", "coverage": "0%", "note": "not in DB → W1"}
```

and line 2222 lists `"coverage gaps: MFE/MAE …% (W6), Telegram 0% (W1), tier (W12)"`. The
survivor **self-reports 0% coverage** of what the retiring generator's sheet 5 provides,
pending work-item W1. Its own module docstring (lines 17-18) is equally explicit:

> Runs PARALLEL to reports/daily_report.py + reports/daily_review.py — it does NOT edit,
> retire, or re-cron them.

### It is also not dead

- **Live and healthy**: `daily_report` ran `SUCCESS` at 16:05 on 14-, 15- and 16-Jul,
  producing `daily_report_2026-07-16.xlsx`. Both reports are being generated daily.
- It sends a Telegram "Daily report ready" on completion (`daily_report.py:1865-1868`).
- The GUI `/api/reports` lists `reports/output` and serves downloads — currently 403-gated
  (`reports_download_enabled=false`, **Q3 pending**). That is a *pending gate*, not a
  decision that the artifact is unwanted.

### One near-miss worth recording

`scripts/gemini_weekly_patterns.py:110` reads
`DAILY_REPORT_DIR / f"daily_report_{d_iso}.{ext}"` for `ext in ("md", "txt")` — the right
directory, but `daily_report` only ever writes `.xlsx`, and `_load_file` is a `read_text()`.
**That read has never found a file**: a dormant pre-existing bug, not a live consumer. It
looked like a blocker and is not one — but it is also not evidence of deadness, and it is
worth its own item.

### Cascade the instruction did not anticipate

`scripts/fetch_daily_candles.py` exists **solely** to feed this generator — its docstring:
*"Generates candle_data_YYYY-MM-DD.csv consumed by reports/daily_report.py --candle-dir."*
Retiring `daily_report` orphans that job too. Any future retirement must retire both, or
knowingly keep a producer with no consumer.

### To retire it later, in this order

1. Resolve **W1** (Telegram alert log into the DB) so the survivor can cover sheet 5.
2. Decide whether **Candles** and **Capital** are wanted at all — if they are, they are a
   build on the survivor; if not, that is a deliberate, recorded decision to lose them.
3. Retire `daily_report` **and** `fetch_daily_candles` together, cron + docs + SYSTEM_MAP in
   one commit.
4. Fix or delete the `gemini_weekly_patterns` `.md`/`.txt` read either way.

**Nothing deleted, nothing disabled, no cron touched.**

---

## X5 — the lock could refuse a legitimate restart (`e2f34a1`)

### The premise is backwards for production

Raised as *"SO_REUSEADDR lets a second bind → two instances → double-trade risk"*. Measured,
second bind of a **listening** `127.0.0.1` port:

| Platform | with `SO_REUSEADDR` | without |
|---|---|---|
| **Linux** (prod VM) | **REFUSED** `EADDRINUSE(98)` | REFUSED |
| **Windows** (dev PC) | **SUCCEEDS** | REFUSED |

On Linux `SO_REUSEADDR` only permits rebinding a `TIME_WAIT` port, and a lock socket that
never `accept()`s never leaves a connection to make one. **The premise is false on the only
platform that trades.** It is moot besides: the PID-file layer refuses a second start
*before* the socket is reached. Proven cross-process against the old code, property only, no
message matching — the second start was refused on both platforms. **P1 was never broken.**

That vacuous test in `tests/unit/test_phase17_batch3.py:47` is where the belief came from —
it notes *"SO_REUSEADDR allows multiple sockets to bind if one is listening"* (a Windows
behaviour, stated as universal), concludes *"the real test is whether the code PATHS exist"*,
and then asserts `inspect.getsource()` contains `"bind("`. It cannot fail.

### The real defect, and prod had it

> a stale lock file naming a **recycled PID** refuses a legitimate start

`_pid_is_alive()` cannot tell "the trading system" from "whatever now holds PID 4711". A
crash leaves the file behind (release never runs); once the OS reuses that PID for anything
at all, the check reports "another instance is running" and the boot is refused. `main()`
returns 1 on refusal, and the unit's `RestartPreventExitStatus` is `3 4` — **so exit 1 is
restarted, and the refusal repeats: a restart loop, i.e. the system silently does not
trade.** A guard whose failure mode is a total outage was resting on a heuristic that is
wrong in both directions.

### The fix — let the kernel own the lock

Enforcement moves to an OS-owned exclusive non-blocking lock on the lock file
(`fcntl.flock` on Unix, `msvcrt` byte-range on Windows), held for the process lifetime. The
kernel drops it when the process dies **by any means** — clean exit, SIGKILL, OOM-kill,
power loss. A stale lock is **impossible by construction** rather than unlikely by
inspection. Both properties then hold structurally: a second concurrent start cannot take a
lock a live process holds; a dead instance cannot keep one. The PID in the file becomes
informational — it names the holder in the operator message, and nothing branches on it.

Two smaller holes closed in passing:

- `SO_REUSEADDR` removed (`SO_EXCLUSIVEADDRUSE` on Windows = the Linux default, spelled out).
- **The lock file is no longer unlinked.** The lock lives on the *inode* via an open fd, so
  unlinking a path another instance holds would let the next start lock a **fresh inode** and
  run alongside it — flock's classic unlink race.

Fail-closed on an unwritable lock dir is unchanged. `_pid_is_alive()` is **kept** —
`scripts/reconstruct_excursions.py:363` imports it for its own lock.

### Both properties tested, both platforms

The old suite proved neither. It drove a single process, and `test_fails_when_same_pid_alive`
wrote **pytest's own PID** and demanded the start be REFUSED — the recycling outage encoded
as a requirement. Both properties are now driven **across processes** with a real subprocess
holder:

- **P1** second concurrent instance refused, and the refusal names the holder
- **P1** a *different* `lock_port` does not bypass the guard (layer 1 is authoritative)
- **P2** restart works after a clean exit
- **P2** restart works after **SIGKILL**, with no release having run
- **P2** a live-but-unrelated PID in the file does not block the start
- `SO_REUSEADDR` asserted OFF **on the live socket** — not grepped from source, which is the
  old style and would pass on a comment (as my own first attempt did, until I checked)
- a port conflict must not leave layer 1 held

Contract inversions, deliberate: release now **leaves** the file (the unlink race); a live
foreign PID no longer blocks (the whole point).

Verified on both platforms, because the fix takes different code paths on each and
Windows-green proves nothing about prod:

```
Linux   (VM, fcntl path)   : old 4 failed / 15 passed  ->  new 19 passed
Windows (PC, msvcrt path)  :                               new 19 passed
```

23 pass with `test_phase17_batch3`. **No trading-path change** — startup guard only. Takes
effect on the next boot; the system is down today and was **not** started to apply it.

---

## Regression — full suite, not scoped

```
10 failed / 4849 passed / 4 skipped, rc=1, 13m14s   (run 13:40-13:53 IST = in-window)
```

**0 attributable.** The 10 are exactly the known time-gated PC-env set, matched by **name**,
not merely by count ([[pc-test-env-hygiene]]: `test_main` ×4 · `test_order_placer_fix061` ×4 ·
`test_fix181` ×1 · `test_phase17_batch2` ×1 = 10 in-window; the 11th, the FIX-189 clock-guard,
fails only outside 08:00-16:00 IST — this run was in-window, so 10 is the right expectation).

| File | This run | Known |
|---|---|---|
| `test_main.py` | 4 | 4 |
| `test_order_placer_fix061.py` | 4 | 4 |
| `test_fix181.py` | 1 | 1 |
| `test_phase17_batch2.py` | 1 | 1 |

None of the 10 is in a file this batch touched. `test_phase17_batch3` (which carries the
FIX-081 instance-lock test) **passes**; the failing sibling is `batch2`, a known mock artifact.

The pass count reconciles exactly, which is the second half of the attribution: the prior
baseline was `10 failed / 4837 passed / 4 skipped` ([[s1s3s5-done-17jul]]), and **4837 + 12 =
4849** — the 12 tests this batch adds (X7 +5: 20→25; X5 +7: 12→19). No test was lost, none
was silently skipped, and the failure set did not move.

Per-item RED-on-old evidence is in each section above; both were proven on the **true**
pre-change tree (`git checkout HEAD -- <file>` + grep-confirm absent + check rc — never
`git stash`, which misses committed work).

## Deploy

_(filled in below after deploy)_
