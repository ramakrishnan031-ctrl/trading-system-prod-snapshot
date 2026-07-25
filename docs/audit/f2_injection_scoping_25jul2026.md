# F2 — injection scoping: what gets injected, what it blocks, what the payload must carry

**25-Jul-2026. READ-ONLY: no design, no build.** Follows `f2_surface_recheck_25jul2026.md`,
which established that the blocker is not auth — the `:8080` app has no reference to the
live `KillSwitch`, and `is_active()` returns `self._state` with no runtime re-read, so a
route writing the DB row would change what `/health` **displays** and halt **nothing**.

---

## The three headline answers

1. **What gets injected: TWO BOUND CALLABLES, not the object.** `_create_app` already takes
   three providers; a fourth slot taking `on_halt` / `on_resume` bound methods exposes
   exactly two verbs. Passing the `KillSwitch` object would hand a web app `hard_kill()`,
   `_persist_state`, and the lock — a far larger surface for no benefit.
2. **Worst-case `/health` block: ~38 s, and the Telegram leg is the SMALLEST of the three
   terms.** MEASURED from source: the DB persist runs **inside the lock** with
   `busy_timeout = 30000` (**up to 30 s**), the bus publish is **synchronous** (EV1), and
   the CRITICAL notifier send is now bounded at **8 s** (M-A2). ⚠️ **The brief's premise —
   "up to 8 seconds of /health being dead" — is understated by roughly 4×.**
3. **The payload must be body-bound AND replay-bound.** `/health` HMACs `b""`, so its
   signature is a constant for a given secret and indefinitely replayable. A halt trigger
   must sign a body carrying at minimum a **nonce/timestamp** and the **intended action**.

---

## C1 — What gets injected

`_create_app(state_store, logger, metrics_provider=None, tgt_retry_provider=None,
daemon_liveness_provider=None)` is already a provider-injection point, and `main.py:3476`
already passes three bound methods into it (`signal_processor.get_runtime_metrics`,
`tgt_retry_manager.health_snapshot`, `_daemon_liveness`). **The idiom exists; F2 adds one
more slot.**

| option | what the web app can reach | verdict |
|---|---|---|
| **the `KillSwitch` object** | `soft_kill`, `hard_kill`, `resume`, `_persist_state`, `_lock`, `_state`, `set_notifier` … the whole class | ⛔ **No.** `hard_kill` is reachable from a web route by accident of injection. Nothing needs that. |
| ⭐ **two bound callables** — `on_halt=ks.soft_kill`, `on_resume=ks.resume` | exactly two verbs, each already idempotent and audited | ✅ **Narrowest thing that works.** Mirrors the existing provider pattern exactly. |
| a thin façade object | two verbs + future room | more code than (2) for the same reach today |

**Recommendation stated, not decided: two bound callables.** Note `soft_kill` is already
idempotent (no-op when already SOFT_KILL) and already refuses to downgrade a `HARD_KILL`
— so the narrow surface is also the safe one, with no extra guarding needed at the route.

## C2 — ⚠️ The `threads=1` hazard, costed. It is bigger than the brief assumed.

`start_healthcheck_server` serves with **waitress, `threads=1`** (`:320`). Any request that
blocks blocks **every** other request — and `/health` is what external monitoring polls.

**`soft_kill()` has THREE blocking legs, in this order (source-verified):**

| # | leg | bound | notes |
|---|---|---|---|
| 1 | `self._persist_state(...)` — **inside `self._lock`** | ⚠️ **up to 30 s** | `PRAGMA busy_timeout = 30000` (`core/db_connect.py:87/108/135`). Persist-first is deliberate (KS9 atomicity): if it fails, in-memory state is NOT changed. |
| 2 | `self._publish_event(...)` — outside the lock | **unbounded in principle** | EV1: *"bus invokes all subscribers in the same thread and returns when all complete."* Today the only production subscriber to `KillSwitchActivated` is `_log_kill_switch_event` (`main.py:3303`), a logging handler ⇒ ~0 ms. **But this is an open slot: any future subscriber lands directly in the halt path.** |
| 3 | `self._notifier.send(CRITICAL, …)` | **8 s** | M-A2, deployed today. Was unbounded before this weekend. |

⇒ **Worst case ≈ 30 + ~0 + 8 ≈ 38 s of `/health` returning nothing.**

⭐ **The consequence worth stating before any design:** external monitoring polls `/health`.
**Pressing the halt button could itself look like the system dying** — a ~38 s stall is well
past most healthcheck timeouts, so the operator's halt may fire a liveness alarm. That is a
design input, not a detail. Mitigations exist (dispatch the halt to a worker and return 202
immediately; or raise `threads`), **but neither is chosen here.**

*Correction to the brief, stated plainly: M-A2's 8 s bound made the Telegram leg the
best-behaved of the three. The dominant term is the SQLite `busy_timeout`, which nobody had
costed.*

## C3 — What the payload must carry

**The hazard, from `_authenticate`'s own docstring:** `/health` passes `b""` as
`hmac_payload`, so *"a valid /health signature is therefore a CONSTANT for a given secret
and indefinitely replayable"*. `/webhook`'s is body-bound and is not.

**A halt trigger that copies the `/health` call site inherits an indefinitely replayable
halt.** Anyone who ever observes one valid request can replay it forever.

**Minimum the signed body must carry:**

- **a nonce or timestamp** — with server-side rejection of stale/repeated values. A
  timestamp alone needs a replay window plus clock tolerance; a nonce needs short-lived
  server state. *(The system already has `time_authority` with broker-skew tracking, so a
  timestamp+window is the cheaper of the two here.)*
- **the intended action** (`halt` vs `resume`) — so a captured halt cannot be replayed as a
  resume, or vice versa.
- **a reason string** — `soft_kill(reason, triggered_by)` already records both, and the
  audit trail is only as good as what the caller supplies. `triggered_by` should identify
  the operator route, not "system".

### ⭐ C3 secret separation — CONFIRMED

**§0 rotated `TELEGRAM_BOT_TOKEN` and `ZERODHA_TOTP_LFL836`. It did NOT touch
`WEBHOOK_SECRET`.** Verified structurally: a key-level diff of the VM `.env` before/after
showed **exactly two changed lines**, both named above, with key order and line count
identical (68/68). ⇒ **the auth secret behind any future F2 route is unchanged**, and the
`require_hmac=False` posture (which must stay false for Chartink — READ-FIRST A) is
likewise untouched.

⚠️ **A consequence worth flagging for the design:** because `require_hmac=False`, the
`?token=` fallback is currently accepted. A control route that reuses `_authenticate`
as-is would therefore accept **a bearer token in a URL** to halt trading — which is both
the easiest thing to use from a phone (the B4 reachability fork) *and* the weakest
(nginx logs URLs). **That tension is the reachability decision, restated in concrete
terms — it is not resolved here.**

---

## Not done / not decided

No design, no build, no config. ⛔ **The overnight auto-clear remains untouched** — it is
Rama's explicit 2026-06-20 HEADLESS GUARANTEE, four options remain tabled in
`f2_surface_recheck_25jul2026.md`, and nothing here narrows them.
