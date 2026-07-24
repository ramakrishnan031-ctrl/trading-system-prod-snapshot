# F2 — control-surface enumeration (C4, 24-Jul-2026)

**Built on** the raw live capture `f2_live_process_surfaces_24jul2026.md` + code-reading. **Scope (§3.5): ENUMERATE, do not choose.** No recommendation, no design. Each surface: exists / exposure / auth today / latency / what would have to be *added* to carry an authenticated operator kill-trigger. Any opinion is quarantined in the last section and is NOT part of the enumeration.

## The table

| # | Surface | Exists? | Exposure | Auth today | Latency as a control path | What would have to be ADDED |
|---|---|---|---|---|---|---|
| 1 | **`:8080` healthcheck HTTP** (`scripts/healthcheck_server.py`, in-process Flask/waitress daemon thread) | **Yes** | loopback `127.0.0.1` only | **None** — 3 GET routes (`/health`, `/metrics`, `/metrics/prometheus`); payload explicitly "unauth" (`:86`) | in-process HTTP, ~ms | a **mutation** route (all current routes are read-only GET) + **endpoint auth** (none exists) + a handle to the in-process `KillSwitch` |
| 2 | **`:5000` webhook receiver** (`signals/webhook_receiver.py`, in-process Flask, fd 23) | **Yes** | **`0.0.0.0` (external)** | shared-secret token per-route; `require_hmac=false` (C6 concern — per-route auth) | in-process HTTP, ~ms | a control route + auth hardening; **⚠️ it is the signal ingress + externally reachable** (C6 says refactor the auth first) |
| 3 | **`:5001` instance-lock socket** (`utils/instance_lock.py`) | Yes | loopback, backlog 1 | n/a — **never `accept()`s** (`:186`); a connect just sits in the backlog | not usable — it is a bind-only mutex, not a channel | everything (it is not a request/response surface at all) |
| 4 | **Unix socket / FIFO / inotify watch** | **No** (capture: process holds none) | — | — | — | the socket/FIFO + a **listener thread**, or an inotify watch + handler — all net-new machinery |
| 5 | **POSIX signals** | Partial | — | — | near-instant | `SIGINT`/`SIGTERM` are used (shutdown, `main.py:1208`); **`SIGHUP`/`SIGUSR1`/`SIGUSR2` are UNUSED**; `ExecReload` unset. A handler would have to be registered. Signals carry no auth/payload (operator identity + reason must come from elsewhere) |
| 6 | **Existing periodic ticks** (main loop; `order_monitor` ~2 s poll; `eod.check_and_fire` poll; `live_feed` watchdog) | Yes | in-process | — | up to the poll interval (e.g. ~2 s) | the check logic + a source to poll (a DB flag / file) — **no new thread needed**, but re-introduces the boot-only→runtime-read gap (A1.3): a DB/file poll is exactly the "new re-read machinery" the F2 finding flagged |
| 7 | **DB write to `kill_switch_state`** | Yes (writable) | — | — | **invisible to the running process** (A1.3: `is_active` reads memory; DB read only at boot) | a poller (#6) to bridge the external write into memory — net-new |
| 8 | **Ops dashboard** (`:8500`, PID 1749999, `gui-dashboard.service`, separate process) | Yes | loopback behind tailscale; has its OWN auth | reads DB **read-only** (SELECTs; reads `kill_switch_state` only to display) | — | **no channel to the trading process exists at all** (see finding below) |

## Structural finding (§3.3) — stated as a finding, not a preference

**The existing operator-facing UI cannot reach the running kill switch.** The ops dashboard is a separate process that only *reads* the shared DB (no HTTP/socket/IPC call to the trading process — grep of `ops_dashboard/backend` found none; it binds its own `127.0.0.1:8500` and reads `kill_switch_state` SELECT-only). Even a DB write would be invisible to the running trading process (A1.3). Therefore **F2 cannot be delivered by extending the dashboard** — the dashboard→process channel does not exist, and the DB is not a live channel. Any operator-facing control must reach the trading process *itself* (surfaces #1, #2, #5, or a net-new one), not the UI that displays its state.

## Two-mutex note (§3.2)

Single-instance uses **two** load-bearing mechanisms (both X5-tested): the `/tmp/trading-system.lock` **fcntl flock** is the *authoritative* kernel-owned guard (drops on death → cannot go stale), and the `:5001` socket *additionally reserves the port*. Neither is vestigial; the socket simply never accepts.

## — OPINION (quarantined; NOT part of the enumeration, NOT a recommendation) —

Nothing chosen here. The enumeration surfaces one fact the design phase will have to weigh: the only *in-process* HTTP listeners that already exist (#1 loopback read-only, #2 external signal-ingress) both belong to the trading process, so a design *could* avoid net-new socket/thread machinery — but #1 has no mutation route or auth and #2 is the externally-reachable ingress C6 wants hardened first. That is an input to the design (Web Claude's), not a decision. `schema_version=44` (from `schema_meta.value`; `PRAGMA user_version=0` is a rollback trap).
