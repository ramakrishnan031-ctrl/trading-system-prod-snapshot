# P3-s14 — the webhook dedup claim is not released on a non-IntegrityError INSERT failure

**READ-ONLY investigation, 17-Jul-2026. Nothing fixed, nothing pushed.**

## STEER (one line)

**The claim is SEPARATE, not transactional** — it is an in-memory `TTLCache`
(`webhook_receiver.py:197`) while the INSERT is a SQLite transaction that "rolls back on any
exception" (`state_store.py:509`), and a DB rollback cannot touch an in-memory dict — **so the
fix must explicitly release it**; and **claim-before + release-on-failure is the safer shape**
than claim-after-insert, because the codebase already does exactly that twice for QUEUE_FULL
(`:874-876`, `:913-915`) and because double-entry is independently prevented by the in-flight
claim (`:811`) and the DB's `UNIQUE(fingerprint, fingerprint_date)` (`schema.sql:82-83`).

**Blast radius: ONE (symbol, scanner) pair, silently dropped, bounded to ≤300 s** (the TTLCache
expires the leaked claim; the in-flight leak is separately bounded to ≤120 s by the HIGH #9
sweeper). **Not** a whole-symbol or whole-strategy poison. **It has never fired: 0 × HTTP 500
in 89,794 audited POSTs over five weeks.**

**Two corrections to the finding are below** (§Q4-b and §Q5-c) — one makes it *worse* than
stated, one makes the obvious fix *insufficient*.

---

## Q1 — the exact flow

`_process_signal`, `signals/webhook_receiver.py:769-920`.

| Step | Line | What |
|---|---|---|
| validation (symbol/price/expiry) | `:782-803` | returns **before** any claim — nothing to roll back ✅ |
| **CLAIM #1 — in-flight** | `:811` | `if not self._claim_in_flight(symbol)` → in-memory `dict` (`:166`) |
| dedup check | `:818-822` | hit → `_release_in_flight` + `DUPLICATE` ✅ |
| **CLAIM #2 — dedup cache** | **`:824`** | `self._dedup_cache[dedup_key] = True` |
| fingerprint | `:829-831` | `sha256(scanner|symbol|floor(ts/window))` — the *second* dedup layer |
| **INSERT** | `:840-857` | `with self._store.transaction() as cur: INSERT INTO signals …` |
| **the only handler** | **`:858`** | `except sqlite3.IntegrityError:` |
| queue push | `:896-916` | on `queue.Full` → **releases both claims** (`:913-915`) ✅ |

The claim site, verbatim (`:817-824`):

```python
dedup_key = (symbol, scanner_name)
with self._dedup_lock:
    if dedup_key in self._dedup_cache:
        self._release_in_flight(symbol)
        return {"symbol": symbol, "status": "DUPLICATE"}
    self._dedup_cache[dedup_key] = True      # <-- CLAIMED HERE
```

and the INSERT's only handler (`:840`, `:858`):

```python
try:
    with self._store.transaction() as cur:
        cur.execute(""" INSERT INTO signals ... """, (...))
except sqlite3.IntegrityError:               # <-- ONLY IntegrityError
```

**There is no other handler.** Every reject path that exists *does* release correctly —
`:821` (DUPLICATE), `:877` (QUEUE_FULL re-accept), `:891` (IntegrityError), `:915`
(QUEUE_FULL). The gap is the path nobody enumerated: **any exception that is not
`IntegrityError`** escapes `_process_signal` with **both claims still held**.

Two corrections to the finding's framing:

- **The dedup key is `(symbol, scanner_name)`** (`:817`) — not symbol+strategy+bucket. The
  bucket (`epoch_bucket`, `:829`) belongs to the *other*, DB-side layer.
- **The window** is `webhook.dedup_window_seconds`, default 300, floored at 60 (`:193-197`),
  and it is the TTL of the cache itself (`:197`) — so it is self-expiring, which is what
  bounds the damage.

## Q2 — what can fail between the claim and a committed signal

Everything inside `:840-857` that is not an `IntegrityError`:

| Failure | Realistic? |
|---|---|
| `sqlite3.OperationalError` — "database is locked" / `SQLITE_BUSY` | **Low.** `journal_mode=WAL` (`db_connect.py:85`) + `busy_timeout=30000` (`:87`) + `BEGIN IMMEDIATE` (`state_store.py:527`) mean a writer must stall **>30 s** to surface it. |
| `sqlite3.OperationalError` — disk full / disk I/O error | **Low, but `busy_timeout` does not help** — this is the most plausible real trigger. |
| `sqlite3.InterfaceError` — unsupported param type | Very low; the params are str/float. |
| any error from `self._store.transaction()` / `_get_conn()` | Very low. |
| `sqlite3.IntegrityError` | **Handled** (`:858`) — not a factor. |

**Not** a serialization/JSON risk: `webhook_payload` is already a sanitised string (`:606`).

Scheduling makes it rarer still: webhooks arrive ~09:15-15:30, while the backup runs 01:00 and
`wal_checkpoint` 16:00 — so the heavy writers barely overlap the webhook window.

**Empirical: it has never happened.** `webhook_audit` is the authoritative POST record
([[feedback-webhook-flow-diagnosis]]); over **89,794** POSTs, 12-Jun → 16-Jul:

```
200 | 63816
403 | 25960     <- the known pre-10:00 entry_start gate, not an error
503 |    18     <- QUEUE_FULL backpressure, the HANDLED path
500 |     0     <- the P3-s14 fingerprint: NEVER FIRED
```

This is a real defect on an unprotected path, with **zero observed occurrences**. That should
set its **priority**, not its correctness.

## Q3 — is the claim transactional with the INSERT? **NO — separate.** (This decides the fix.)

There are **two** dedup layers, and only one of them is transactional:

| Layer | Where | Transactional with the INSERT? |
|---|---|---|
| **1. in-memory TTLCache**, key `(symbol, scanner_name)`, TTL 300s | `webhook_receiver.py:197`, claimed `:824` | **NO.** A `cachetools.TTLCache` in process memory. |
| **2. DB fingerprint**, `UNIQUE(fingerprint, fingerprint_date)` | `schema.sql:82-83`, written `:854` | **YES** — rolled back with the txn. |

`StateStore.transaction()` "Commits on successful exit, **rolls back on any exception**"
(`state_store.py:509`). So on an `OperationalError` the **DB layer releases itself** (the row
was never committed) while the **in-memory layer keeps the claim** for the full TTL.

⇒ **The fix must explicitly release layer 1.** A rollback will never do it.

This asymmetry is exactly why the bug is invisible: the durable, inspectable layer is correct,
and the leak is in the layer that leaves no trace.

## Q4 — blast radius

**(a) Bounded, and smaller than "a symbol/strategy".** The key is one `(symbol, scanner_name)`
pair (`:817`), so only that pair is blocked — other scanners on the same symbol, and other
symbols on the same scanner, are unaffected. Both leaks self-heal:

| Leaked claim | Cleared by | Bound |
|---|---|---|
| dedup cache (`:824`) | `TTLCache` TTL (`:197`) | **≤300 s** (`dedup_window_seconds`) |
| in-flight (`:811`) | HIGH #9 sweeper thread (`:175-180`, `_run_sweeper` `:1030-1055`) | **≤120 s** |

The in-flight bound is **≤120 s, not 60 s**: an entry becomes evictable at `heartbeat_at + 60 s`
(`:1043`, `:168`) but the sweeper only wakes every 60 s (`:1037`), so the worst case is one full
stale period plus one full tick. It is still well inside the 300 s dedup window, so **the dedup
cache is the binding constraint** and the loss is **bounded to ~300 s** — it cannot persist
longer or survive into another window.

Neither leak needs a restart: contrary to the general [[feedback-in-flight-memory]] rule
("restart to clear"), the FIX-011 sweeper covers this case, and the dedup cache self-expires.

**A second fingerprint:** the sweeper logs **CRITICAL** on every eviction (`:1050-1055`,
*"evicted STALLED symbol … signal_processor worker likely crashed or deadlocked"*). So a leaked
in-flight claim announces itself too — though note it would name a **misleading cause**, blaming
signal_processor for what was actually a receiver-side INSERT failure. Worth knowing before
that line is ever read in anger.

**(b) CORRECTION — it is worse than "one signal", for one request.** The finding treats this as
a single-signal loss. But `_process_signal` is called inside a **`for` loop over every symbol in
the payload** (`:614`, `:637`) **with no try/except**, so the escaping exception abandons the
**rest of the batch**:

```python
for raw_symbol, price_str in zip(symbols, price_strs):     # :614
    ...
    item = self._process_signal(...)                        # :637  <-- unguarded
```

A Chartink payload carries many symbols. If symbol #3 raises: #1-2 are already accepted, **#3
leaks its claim**, and **#4-10 are never processed at all**. On the retry, #4-10 *do* recover
(they were never claimed, so they re-enter cleanly) — only **#3** stays bounced as DUPLICATE
for the window. So the permanent loss is still one signal per raise, but the immediate batch
damage is wider than stated.

**(c) The escape path and its fingerprint.** The exception is caught at
`_handle_webhook:427`, which logs **CRITICAL** `"Unhandled exception processing /webhook/…"`
and returns 500; the `finally` at `:431` still writes the audit row with `response_code=500`
(there is also an `@app.errorhandler(500)` at `:312-315` as a second net). So the bug is
**loud in the logs and durable in `webhook_audit`** — it is silent only in its *effect* (the
dropped signal), never in its *occurrence*. That is what makes the 0/89,794 above trustworthy
as evidence rather than absence of instrumentation.

## Q5 — fix direction (NOT an implementation)

**Recommended: (b) claim-before + explicit release on failure.** Add an `except Exception`
beside the existing `except sqlite3.IntegrityError` (`:858`) that releases **both** claims —
the dedup cache *and* in-flight — before returning.

**(a) Why not claim-after-successful-insert?** It is superficially attractive (nothing to roll
back), but it restructures a signal-entry path for no added safety: the QUEUE_FULL re-accept
path (`:869-890`) and the QUEUE_FULL pop (`:913`) both depend on the current ordering, and the
cache-claim is deliberately placed *before* the DB so a duplicate is rejected without touching
it. Reordering buys nothing the release does not, and costs a redesign of the path that decides
whether a real signal enters the system.

**(b) Does releasing on failure re-open a double-process risk? No** — two independent guards
survive, and neither is the cache:

1. `_claim_in_flight(symbol)` (`:811`) serialises concurrent same-symbol requests → the second
   gets `IN_PROCESS` before the dedup cache is even consulted.
2. `UNIQUE(fingerprint, fingerprint_date)` (`schema.sql:82-83`) is the **authoritative**
   within-window dedup: a retry inside the same 300 s bucket recomputes the *same* fingerprint
   (`:829`, `floor(ts/window)`), so a double INSERT raises `IntegrityError` → `DUPLICATE`
   (`:892`). A retry landing in the *next* bucket gets a new fingerprint — but that is ≥300 s
   later, which is precisely when the cache would have expired anyway. **No new risk.**

The cache is a fast-path optimisation, not the control. That is what makes releasing it safe.

**(c) CORRECTION — releasing the claim is necessary but NOT sufficient.** This is the trap in
the obvious fix. If the handler catches the exception and returns an ordinary per-symbol status,
the batch response becomes **200** (`:650`, only `QUEUE_FULL` triggers 503) — and **a 200 tells
Chartink the POST succeeded, so no retry is ever sent** and the signal is lost *permanently*,
which is worse than today's bounded 300 s. Releasing the claim only helps if a retry actually
arrives.

So the direction must pair the release with a **retryable response**, reusing the machinery
that already exists for exactly this shape:

- a distinct terminal status (QUEUE_FULL's sibling — "the store failed, this is not a
  duplicate, retry me"), released on both claims;
- folded into the 503 predicate at `:649-650` (`any_queue_full` → `any(retryable)`), so the
  sender retries and the release is meaningful;
- which also lets the loop **continue to the remaining symbols** instead of abandoning the
  batch (§Q4-b), because the exception no longer crosses `:637`.

This mirrors M-S2's QUEUE_FULL design decision exactly (`:908-914`: *"QUEUE_FULL is
backpressure, NOT a duplicate. Roll back the fast-path dedup CACHE claim … Without this the
retry is bounced as DUPLICATE for the whole dedup window and backpressure recovery is
impossible."*) — **the identical reasoning, applied to store failure instead of queue
fullness.** The precedent, the idiom, and the wording are already in the file.

**Idempotency / backpressure semantics to preserve:** the 503-at-queue-full contract
(`:648-650`), the `X-Queue-Depth` / `X-Queue-Warning` headers (`:656-660`), the QUEUE_FULL row
re-accept + `signal_id` reuse (`:869-890`), and in-flight release-on-every-reject (`:821`,
`:877`, `:891`, `:915`) with `signal_processor` releasing the accepted path (`:918-919`).

**Parity (Rule #5): free.** `webhook_receiver.py` contains **zero** paper/live references — it
is signal *entry*, upstream of any mode branch. The fix holds identically for both, no fork.

## Q6 — regression surface

| Area | Where |
|---|---|
| the function | `_process_signal` `:769-920` |
| the claims | `_claim_in_flight` / `_release_in_flight` `:922-944`; `release_in_flight` `:950-956`; `update_heartbeat` `:958-968` |
| the sweeper (bounds the in-flight leak) | `:166-168`, `:175-180`, `_run_sweeper` |
| the dedup cache | `:189-198` (TTL config) |
| the batch loop + 503 predicate | `_process_request` `:614-661`, esp. `:649-650` |
| the escape path | `_handle_webhook` `:412-436`; `@app.errorhandler(500)` `:312-315` |
| DB dedup layer | `schema.sql:74-83` (`idx_signals_fingerprint_today`) |
| **primary test file** | `tests/unit/test_webhook_receiver.py` — **109** dedup/in-flight/QUEUE_FULL assertions |
| integration | `tests/integration/test_full_signal_flow.py`, `test_end_to_end_smoke.py`, `test_hardening_scenarios.py` (M-S2 QUEUE_FULL, real receiver + Flask + store) |

**No test anywhere simulates a non-`IntegrityError` INSERT failure** — no `OperationalError`
injection exists in any webhook test. That is precisely why a 100 %-unprotected path sits under
a green suite, and it means the fix **needs a new RED-on-old test** that injects
`sqlite3.OperationalError` at the INSERT and asserts (1) the dedup claim is released, (2) the
in-flight claim is released, (3) an immediate retry of the same signal is **ACCEPTED, not
DUPLICATE**, and (4) the response is retryable (5xx), not 200.

## Summary for the design step

- **Separate, not transactional** ⇒ the release must be explicit. Layer 2 (DB fingerprint) is
  already correct and keeps double-entry impossible; only layer 1 (the in-memory cache) leaks.
- **claim-before + release** beats claim-after-insert: the idiom exists twice already, the
  guards that prevent double-entry are elsewhere, and re-ordering a signal-entry path is the
  larger risk.
- **The release alone is a trap** — it must be paired with a retryable (5xx) response, or the
  sender never retries and a bounded 300 s loss becomes a permanent one.
- **Never fired**: 0 × 500 in 89,794 POSTs / 5 weeks. Real, bounded, loud when it happens,
  and low priority — but the fix is small and the precedent is already written.
