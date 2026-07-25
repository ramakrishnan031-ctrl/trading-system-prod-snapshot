# Was `threads=1` on the healthcheck server deliberate? And would raising it be safe?

**25-Jul-2026. READ-ONLY — nothing changed.** This one fact decides F2's shape: if the
serialisation is load-bearing, a synchronous confirmed halt is off the table.

---

## The two answers

1. **Was it deliberate? NO — as far as the record goes, it is an unexamined default.**
   `threads: 1` was present in the **file's creation commit** (`a9ccaef`, 31-May-2026),
   carries **no comment**, appears in **no design note or locked decision**, and survived a
   later security pass that edited the very same call **without touching it**.
2. **Would raising it be safe? YES — and there is no hidden dependency.** Every shared thing
   the app touches is either explicitly thread-safe by construction or a read-only snapshot.
   **`threads=1` is not what has been preventing a concurrency hazard.**

---

## A1 — Ask git about absence

```
git log -S "threads"  -- scripts/healthcheck_server.py
  a9ccaef  FIX-132b: Items 10,11,15 -- email fallback, morning scripts, healthcheck server
  dd4f3f4  fix(infra): low-sev security/config hardening batch — C-3 / C-4 / F-1 / E-4

git log -S "waitress" -- scripts/healthcheck_server.py
  a9ccaef   (only)
```

**`a9ccaef` is the file's birth commit.** Its diff introduces the value already in place:

```
+        kwargs={"host": host, "port": port, "threads": 1},
```

The commit message names three unrelated items and **says nothing about threading,
serialisation or concurrency**. The module's own design notes list `HC3 -- Uses waitress
(already in requirements) for production WSGI` — a note about *choosing waitress*, not about
thread count. **No commit ever changed the value; there is nothing to have "revisited".**

**⭐ The strongest evidence is a contrast within one call.** `dd4f3f4` (the C-3 hardening
pass) edited this exact function and left a deliberate, dated comment on the argument it
*did* care about:

```python
port: int = 8080,
host: str = "127.0.0.1",  # C-3: loopback-only — /health + /metrics expose P&L / capital / kill-switch posture. Was 0.0.0.0.
...
kwargs={"host": host, "port": port, "threads": 1},     # ← no comment, then or now
```

**In this codebase, deliberate choices on this call got comments. `threads=1` did not.**

## A2 — Is it named in a decision record?

`docs/locked_decisions.yaml` — **no entry for the healthcheck server, waitress, `:8080`, or
its thread count.**

⚠️ **One near-miss, checked and rejected as a premise:** `EV1`'s rationale says *"No
threading complexity for a system that already runs single-threaded per the G9 decision."*
**G9 is about inter-module communication** (direct calls vs a tiny EventBus) — not about
worker threads, and not about this server. The phrase is an architectural aside, and the
system is demonstrably *not* single-threaded (`signal_processor` max 5, entry-gate pool,
`smart_tgt` max 2, plus feed consumer/watchdog daemons). **It does not constitute a decision
to serialise `:8080`.**

⇒ **Verdict: undetermined-but-almost-certainly-incidental.** I cannot prove intent from a
negative, so the honest phrasing is: *nothing in the record shows it was chosen, and
everything about how this codebase annotates deliberate choices suggests it was not.*

## A3 — What would newly become concurrent?

| what `_create_app` receives | concurrent-safe? | evidence |
|---|---|---|
| **`state_store`** | ✅ **safe by construction** | `state_store.py:154-155`: *"this object is safe to share across threads. Each thread gets its own SQLite connection (via `threading.local`) on first use."* |
| `metrics_provider` = `signal_processor.get_runtime_metrics` | ✅ **explicitly lock-guarded** | `:557` `with self._rt_metrics_lock:` then `:560` `with self._stats_lock:`; returns `dict(...)` **copies**, not live references |
| `tgt_retry_provider` = `tgt_retry_manager.health_snapshot` | ✅ **read-only** | `:217-227` reads `_enabled`, `_thread.is_alive()`, `_consecutive_failures`; computes locals; returns a fresh dict. **No mutation.** |
| `daemon_liveness_provider` = `_daemon_liveness` | ✅ **read-only** | `main.py:3462-3473` calls four `is_alive()`s inside `try/except`; `Thread.is_alive()` is thread-safe; `live_feed.is_connected` is a plain bool read |

**And the routes themselves:**
- **No route writes to the DB** — `grep -E "INSERT|UPDATE|DELETE|commit\(" scripts/healthcheck_server.py` → **empty**. All three endpoints are read-only.
- **No module-level mutable state** — the only `append()`s are to a `lines` list that is
  local to the prometheus route, per request.

**One honest caveat, not a blocker:** raising to N threads means up to N additional
per-thread SQLite connections for this server. The codebase already reasoned about exactly
this (`state_store.py:202-213`, Audit 4.3, closed as INFO): pool threads are recycled, so
connections are *bounded by pool size*, ~12 for the whole process. A waitress pool of 4
would add ≤4 more — same bounded pattern, not a leak.

## A4 — The specific hazard: are the bound-method providers safe concurrently?

**This was the thing to rule out, and it is ruled out.** None of the three mutates shared
state:

- `get_runtime_metrics` is the only one that touches genuinely shared mutable state, and it
  is **already protected by two locks and returns copies** — i.e. it was *written* to be
  called from another thread. That is a strong signal it was never relying on `threads=1`.
- `health_snapshot` and `_daemon_liveness` perform **reads only**.

⚠️ **The one real (minor) effect, stated rather than glossed:** `health_snapshot` reads
`_enabled`, `_thread`, and `_consecutive_failures` as separate unsynchronised attribute
reads. Concurrently with the retry worker mutating them, a caller could observe a
*momentarily inconsistent composite* — e.g. `alive=True` sampled just before a crash and
`in_crash_loop` just after — yielding a health string that was never simultaneously true.
**That is a cosmetic reporting artefact, not corruption**, it is already possible today
(the worker mutates concurrently with the single healthcheck thread), and raising `threads`
does not create it.

---

## What this means for F2 (stated, not decided)

**The synchronous-confirmed design is not blocked by `threads=1`.** It can stay at 1 (and
accept that a slow halt blocks `/health` — see the companion busy-timeout report), or be
raised, without a hidden dependency being disturbed.

⛔ **NOT CHANGED.** Anyone who does change it should note that `threads=1` also currently
provides *incidental* serialisation of `/metrics`, which nothing depends on for correctness
but which does mean concurrent scrapes have never actually happened in production. The
evidence above says they would be fine; it has simply never been exercised.
