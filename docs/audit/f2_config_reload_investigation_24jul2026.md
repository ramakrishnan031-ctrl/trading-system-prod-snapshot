# F2 config-reload investigation — does the running service re-read config, and does a runtime-mutable control surface already exist?

**Date:** 2026-07-24 (Friday, market hours). **Mode:** READ-ONLY code-reading + `mode=ro` DB reads. No process signals sent; no live experiment.
**Scope:** investigate-only, step one of four for F2 (operator soft-kill). A finding is reported, not fixed.

---

## ANSWER (A1.3 — the question that decides F2's shape)

1. **`is_active()` reads PROCESS MEMORY, not the DB.** `capital/kill_switch.py:461` returns `self._state` under an RLock; the `kill_switch_state` table is read **once at construction** (`_load_state_from_store`, `capital/kill_switch.py:839`, called from `__init__:264`) and written on every mutation (`_persist_state:818`, persist-first). Line 237 states it: *"In-memory state — authoritative after construction."*
2. **The only runtime-mutable control surface — the in-process `KillSwitch` object — is reachable ONLY by in-process code.** An external write to `kill_switch_state` (or a config edit) is **invisible** to the running service until it restarts.
3. **Therefore:** a config-flag or DB-flag F2 that must take effect **mid-session** needs NEW runtime-re-read machinery — the "simple config flag" framing is wrong for mid-session. **BUT** an F2 that triggers the **existing** in-process `KillSwitch` (`ks.soft_kill()`/`ks.resume()`) needs **no new state machinery at all** — only a trigger surface. That is the cheap path, and it is NOT config.

> The inherited hypothesis ("config is boot-loaded, so a config flag would need new runtime re-read machinery") is **CONFIRMED against source** — and it is true of the kill state too. What the hypothesis missed: the state machine F2 needs already exists (the `KillSwitch`); what is absent is an *operator-facing in-process trigger*.

---

## A1.1 — Config lifetime: boot-once, no singleton (measured)

Locked decision **CL1** (`docs/locked_decisions.yaml:730`): `load_all(config_dir) → AppConfig` is a **module-level function, no singleton**. `main.py` calls it once at Phase 0b, holds the `AppConfig`, and passes sub-configs as constructor parameters to each subsystem via DI. `AppConfig` is a Pydantic `BaseModel` (`core/config_loader.py:2046`).

- A `functools.cache` module-level singleton was **explicitly rejected** (CL1 `alternatives_considered`): *"same hidden state problem."*
- **Lifetime = process lifetime.** Each subsystem holds its own reference to the sub-config it was handed at construction. There is no cache to invalidate because there is no cache.

## A1.2 — Nothing re-reads config after boot (measured)

Searched the whole tree (non-test) for `signal.signal | SIGHUP | SIGUSR | watchdog | inotify | Observer( | .reload( | reload_config | config_version | watch_config`:

- **No SIGHUP/SIGUSR config handler.** The only signal handlers are `main.py:1208-1210` — `SIGINT`/`SIGTERM` → the **shutdown** handler (sets `_shutdown_event`). Not a reload.
- **No file watcher, no periodic re-read, no config-version check.** The `watchdog` hits are unrelated: the `cron-watchdog` systemd timer (watch-the-watcher) and the tick-age `watchdog` in `data/live_feed.py`. Neither re-reads config.
- **No reload/cache/singleton in `core/config_loader.py`** (grep returned nothing).
- **CL4** (`docs/locked_decisions.yaml:761`): `file_hashes` are computed at load and used by Phase 0b to detect config drift **between restarts** and queue a Telegram alert — confirming config is a **restart-boundary** concept, not a runtime-reloaded one.

**Conclusion:** config is immutable for the process lifetime. A config edit takes effect only at the next boot.

## A1.3 — The POST-time kill check reads memory (measured, detail)

- `signals/webhook_receiver.py:512` — `if self._ks and self._ks.is_active(): return 403`. `self._ks` is the injected in-process `KillSwitch` (`:157`). (Second call site `:319` for the status path, same object.)
- `capital/kill_switch.py:461` `is_active()` → `self._state in (SOFT_KILL, HARD_KILL)` under `self._lock`. **No DB access.**
- DB is touched only at: construction read (`_load_state_from_store:839`) and mutation write (`_persist_state:818`, `INSERT OR REPLACE` the single row). **KS9**: persist-first — write DB, then mutate memory; abort both on write failure.

So an operator/other-process writing `kill_switch_state` directly does **not** change what a running `is_active()` returns. This is corroborated operationally by A1.4.

## A1.4 — Every June mid-day clear was a RESTART, never an in-place resume (measured)

**`resume.sh` (FIX-188b, `deploy/resume.sh`) is restart-coupled *by design*:** (1) `systemctl stop` the service — release the instance lock; (2) run `scripts/clear_kill_switch.py` to clear the DB row **while the service is stopped**; (3) `systemctl start`. The header documents *why*: a standalone `main.py --resume` competes with the running service for the instance lock (port 5001) — *the 18-Jun collision*. Clearing the DB under a **running** service would do nothing anyway (A1.3), so the design stops it first and lets the boot re-read pick up `INACTIVE`.

**`system_events` has no resume/activation event type.** Distinct types (all-time): `STARTUP` (58), `SHUTDOWN` (56), `CONFIG_DIFF` (24), `KILL_AUTO_CLEARED` (23). There is **no** `RESUME`/`KILL_ACTIVATED` row — so neither operator resumes nor `soft_kill`/`hard_kill` *activations* are recorded there (only the boot-time auto-clear is).

Per-date sequences (`system_events`, IST), showing every clear was a stop+start:
- **18-Jun:** SHUTDOWN/STARTUP pairs at 07:58, 08:09, **10:36**, **10:58→11:13** (+11:14), 15:35; evening restarts.
- **19-Jun:** 09:28 restart; **down 10:07 → 13:24** (long outage), then STARTUP; 16:00 shutdown.
- **23-Jun:** 08:15 `KILL_AUTO_CLEARED` (prior-day SOFT_KILL) + STARTUP; **12:26 & 12:28** restarts; 16:00 shutdown.
- **1-Jul:** 08:15 `KILL_AUTO_CLEARED` + STARTUP; **13:59** restart; 14:11 shutdown.

**Decisive, in the "every one was a restart" direction:** no in-place mid-session clear has ever occurred. There is **no demonstrated mid-session control surface** in the operational history. The only runtime kill-flip callers are internal/automated — `data/live_feed.py` (queue-full / reconnect-exhausted / consumer-dead), `broker/token_monitor.py` (token expiry), the auto-trip (`:764`), `capital/fund_manager.py` & `capital/drift_handler.py` (capital drift → hard/soft), `orders/order_reconciler.py`, `orders/eod_squareoff.py` (`:377` soft / `:533` resume — the automated EOD path), `orders/cnc_gtt_monitor.py`. **None is operator-facing on a running process.**

## A1.5 — What flipping the kill flag mid-session requires to be SAFE (hazards; not a design)

- **In-flight orders are not unwound by SOFT_KILL.** SOFT_KILL blocks *new* entries and allows exits/monitoring by design; an entry already submitted (`SUBMITTED`/`UNKNOWN_IN_FLIGHT`) runs to its SL/TGT. Correct, but the operator must read F2 as "halt from now," not "flatten." (HARD_KILL is the flatten path.)
- **Staleness is a property of the *bridge*, not the object.** An in-process `ks.soft_kill()` is instant and lock-safe (no staleness). A config/DB-poll bridge introduces a poll-interval staleness window **and** risks the DB row and in-memory `_state` diverging.
- **Persist-first + event + audit (KS9/KS8) must not be bypassed.** Any path that flips state must persist before mutating, publish `KillSwitchActivated`, and (ideally) audit. An external DB writer bypasses all three; the in-process `ks.soft_kill()` already does them.
- **HARD_KILL downgrade guard:** `soft_kill()` is ignored while HARD_KILL (`:529`). An operator soft-kill trigger must surface "already HARD_KILL" rather than appear to succeed.
- **`resume()` re-trip:** clearing to INACTIVE while the *cause* persists (e.g. broker outage that auto-tripped) re-trips immediately — `resume.sh`'s header warns exactly this. An in-process operator resume needs the same "fix root cause first" discipline.
- **EOD latch interaction (F1 link):** route an operator soft-kill through **`ks.soft_kill()` directly**, NOT through `eod_squareoff._fire()` — the latter carries the `_fired_for_date` latch and the `reset_daily_pnl()` path. Note the standing F1 consequence: any day that ends **halted** writes no `RESET_PNL` row (`reset_daily_pnl` is reachable only from `eod_squareoff._fire`), so an operator SOFT_KILL left un-cleared at EOD forfeits `RESET_PNL` exactly like a circuit-breaker kill.

## A1.6 — History gap: an operator F2 soft-kill would inherit it (measured)

`kill_switch_state` is a **single-row** table (`id=1 CHECK`, `INSERT OR REPLACE`) — no history; "was the kill active at time T" is not answerable from it (had to be reconstructed from `system_events` + 403 block structure). Worse: `soft_kill`/`hard_kill` **activations are not written to `system_events` at all** (only `KILL_AUTO_CLEARED` is). An operator F2 that reuses the existing `KillSwitch` **inherits this gap** — it would update the single row, publish a bus event, and log CRITICAL, but leave no durable "operator kill at T" record. *Cheap* closure (not built here): one `system_events` row on operator soft-kill/resume. Reported per instruction; not built.

---

## F2 synthesis (the decision this unblocks)

| F2 shape | New machinery? | Mid-session? | Notes |
|---|---|---|---|
| **In-process trigger → existing `KillSwitch`** (auth'd control on the running receiver, which already holds `self._ks`; calls `ks.soft_kill()`/`ks.resume()`) | **None (state).** Only a trigger surface + auth. | **Yes, instant.** | Reuses persist-first (KS9), restart-recovery (KS3), event (KS8), idempotency. **Cheapest live F2.** |
| **Config flag, honored at restart** | None. | **No** — needs a restart. | Cheap, but it is a restart-time posture, not a live control. `resume.sh` already is essentially this shape for *clearing*. |
| **Config/DB flag, honored mid-session** | **Yes** — a poller/watcher bridging external-write → in-process refresh, re-implementing KS9/KS8/audit or bypassing them. | Yes, with poll latency. | The **expensive** path. This is what "config-controlled + live" actually costs. |

**Conflict to report (Standing Rule F7 — source wins):** Rama's recorded intent is "F2 should be a *config-controlled* mechanism." The source says a config lever is either **not mid-session** (boot-only) or **expensive** (new re-read machinery). If mid-session operator soft-kill is the goal, the cheap lever is the **existing in-process `KillSwitch`**, reached by a trigger surface — **not** config. If "honored at next restart" is acceptable, a config flag is cheap and needs no new machinery. **This is the fork the F2 design should turn on; it is Rama's call, not mine.**

**The valuable outcome, stated loudly:** F2 needs **no new *state* machinery** — the halt state machine already exists, is already runtime-mutable in-process, and already persists/recovers/audits. What it lacks is an operator trigger. Reframed from "config flag" to "in-process trigger," F2 is an afternoon, not a week.
