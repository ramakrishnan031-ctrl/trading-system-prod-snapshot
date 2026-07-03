# G3.0 — CONTROL-PLANE INVESTIGATION (facts only, 03-Jul-2026)

Read-only investigation on `gui-deploy-03jul` (core files ≡ deployed `main == 9becf8c`) + live
VM reads. Every claim cited `file:line`. **No design, no recommendations** — this is the fact
base for Web Claude's G3 design. Parity is stated per mechanism (expected and confirmed: paper
and live share every path below unless noted).

---

## Q1 — Kill-switch runtime semantics (the critical unknown)

**The in-memory `KillSwitch` object is the SOLE authority while the process runs. The DB row is
read exactly once — at construction. An external DB write does NOT take effect live. → NO.**

- `is_active(intent)` is a pure in-memory check of `self._state` under the RLock —
  `capital/kill_switch.py:394-406`. No DB access.
- The **only** read of `kill_switch_state` inside the class is the startup-recovery load
  (KS3) at `capital/kill_switch.py:745-748` (constructor path). There is no periodic re-read,
  no refresh method, no polling anywhere in the class.
- Production `is_active()` call sites: `main.py:2726` (startup gate — aborts boot if still
  active after startup reconciliation), `orders/order_placer.py:932` (entry-path check;
  OP-LM1), `orders/tgt_retry_manager.py:271`. All read the same in-memory object.
- Writes go memory-ward through `_persist_state` (KS9, persist-BEFORE-memory,
  `capital/kill_switch.py:717+`) — i.e. **the process overwrites the DB row on every
  in-process kill/clear event**. Consequence (factual): an external `SOFT_KILL` row written
  by another process is (a) invisible to the running trader, and (b) silently overwritten by
  the next in-process state change.
- Who else reads the row: the in-process healthcheck (`scripts/healthcheck_server.py:86`
  `_check_kill_switch(state_store, …)` — reads the **DB**, not the object), the ops dashboard
  (read-only, `ops_dashboard/backend/readers/db_reader.py`), and `scripts/clear_kill_switch.py`.
- Parity: identical in both modes (same object, same table).

## Q2 — Clear-while-running

`scripts/clear_kill_switch.py` (FIX-188b) constructs its **own** `KillSwitch` on the DB (its
constructor reads the persisted row) and clears → writes `INACTIVE` to the row **without
touching the running process** (module docstring: "WITHOUT starting the trading loop or
acquiring the instance lock (port 5001)"). Run while the trader is up, the result is
divergence: **DB = INACTIVE, trader memory = still killed**; the trader keeps refusing
entries, and its next in-process persist re-overwrites the row.

**`deploy/resume.sh` sequencing is load-bearing for exactly this reason** (its own header says
so): ① `systemctl stop` + `reset-failed` (release the port-5001 instance lock, halt any
restart loop) → ② `clear_kill_switch.py` (refuses HARD_KILL without `--force`; non-zero rc
**aborts before start**) → ③ `systemctl start` (boot re-reads the now-INACTIVE row) —
`deploy/resume.sh:26-41`. The standalone `main.py --resume` alternative collides with the
service on the instance lock (the 18-Jun collision, per both file headers).

## Q3 — Strategy enable/disable runtime semantics

- Runtime state = **boot-time YAML load into an in-memory dict**: `main.py:2328` →
  `strategies/loader.py::load_all_strategies` → `StrategyConfig` map. Per-strategy `enabled`
  comes from the YAML at that moment.
- **Hot reload DOES NOT EXIST** — explicit by design: `core/config_loader.py:38` ("Does not
  support hot reload — load-once at startup") and `strategies/loader.py:27` ("Strategies are
  loaded ONCE; no hot reload"). No SIGHUP handler, no config watcher, no mid-session re-read
  of strategy YAML or system config anywhere.
- **Operator "pause" as a command DOES NOT EXIST.** However an **automatic** pause exists:
  `capital/strategy_governor.py` — the intraday strategy circuit breaker.
  `_paused_today: set[str]` is **in-memory only, resets on process restart / new day**
  (`:9,42`); `check()` (`:49`) pauses a strategy for the rest of the day on a per-strategy
  daily-loss breach (with a time cutoff after which it never pauses, `:63`); enforcement is in
  the signal pipeline (`signals/signal_processor.py:729-733,1470-73` →
  `STRATEGY_CIRCUIT_BREAKER` reject). There is **no operator-facing API** to add/remove a
  strategy from that set, and no persistence of pause state.
- Parity: same loader/governor path both modes.

## Q4 — In-process admin surface (two Flask apps, same process)

- **`:5000` webhook app** (`signals/webhook_receiver.py`): `GET /health` (`:255`),
  `POST /webhook/<scanner_name>` (`:268`, auth = `WEBHOOK_SECRET` token compare + C-2 per-IP
  rate limit). The POST mutates state **in the ingest sense only** (enqueues signals into the
  pipeline); no admin/state-mutation route exists.
- **`:8080` health app** (`scripts/healthcheck_server.py`): `GET /health` (`:62`),
  `GET /metrics` (`:110`), `GET /metrics/prometheus` (`:206`). All GET, all read-only
  (DB + token file reads). **No mutating route.**
- **Architectural fact:** both apps run as daemon threads **inside the trading process**
  (started from `main.py` — webhook `main.py:~2800`, health `main.py:2834-36`), so either
  could technically host a loopback admin endpoint with **direct access to the live in-memory
  objects** (`kill_switch`, the governor's `_paused_today`, …) — the only surface in this
  inventory that can touch running state without a restart. (Fact of capability only; no
  assertion that we should.)
- Parity: both servers run identically in paper and live.

## Q5 — OS-level facts (shutdown, systemd)

- **Signals:** `main.py:1051-1060` (MAIN13) installs `_handler` for **SIGINT and SIGTERM** →
  sets `_shutdown_event` → worker loops observe it and exit; clean shutdown path (threads
  joined, DB flushed). **Nothing is flattened on shutdown** — positions persist; broker-side
  protection (CO SL / GTT / resting SL legs) remains at the broker; restart reconciles.
- **Unit** (`deploy/systemd/trading-system.service`): `Type=simple`, `User=ubuntu`,
  **`KillSignal=SIGINT`** ("so main.py's Ctrl+C handler runs — clean shutdown, SHUTDOWN
  event, DB flush"), **`TimeoutStopSec=30`**, no `ExecStop` (stop = the signal),
  `Restart=on-failure`, `RestartSec=10`, **`RestartPreventExitStatus=3 4`** — exit 3 =
  startup-check failure (would loop), **exit 4 = HALT: kill switch active, manual resume
  required** (prevents the 18-Jun hammer-restart-on-SOFT_KILL loop); exit 0 (holiday/EOD) no
  restart; exit 1/2 (crash) restarts.
- **Watchman:** `trading-watchman.service` is `BindsTo=trading-system.service` (stops with
  it); it is a log **monitor**, not a restarter — restarts are systemd's `Restart=on-failure`
  only. (Operational note from the 03-Jul boot check: watchman exits 0 at the 08:15 start and
  nothing re-starts it during market hours — pre-existing, unrelated to controls.)
- `systemctl stop` while trading: graceful SIGINT path as above; the position/protection
  story is the broker-resident orders + next-boot reconciliation (A-1/E-1 prepass, CHECK
  suite).

## Q6 — sudo / permission facts (live VM, 03-Jul)

- `sudo -n -l` for `ubuntu`: **`(ALL : ALL) ALL` + `(ALL) NOPASSWD: ALL`** — full passwordless
  sudo already exists. A GUI-invoked restart needs exactly
  `sudo systemctl restart trading-system.service` (the documented activation command,
  `PATHS.md`), and **no sudoers change is required** for the ubuntu user.
- `/etc/sudoers.d/`: only stock Oracle-cloud entries (090-oca-vss-plugin, 100-oracle-cloud-
  agent-users, 90-cloud-init-users, README) — no custom entries.
- **auditd watches sudoers** (live `auditctl -l`): `-w /etc/sudoers -p wa -k sudoers_change`
  and `-w /etc/sudoers.d -p wa -k sudoers_change` (matches G0 §6.2,
  `docs/gui_project/G0_BACKEND_INVESTIGATION_REPORT.md:193-196`). **Operational consequence:**
  any future scoped-sudoers entry (e.g. a restricted gui user) is itself a watched
  `sudoers_change` event — such a change must be made deliberately and will be recorded.
  auditd is record-only (forensic), not a blocker.
- Also relevant (G0 §6.2): the 4 systemd unit files and `config/` + `.env` are `-w`-watched;
  `data_store/`, `logs/`, `reports/` are **not** watched.

## Q7 — OPTIONS INVENTORY (facts table — no recommendation)

Mechanisms discovered in Q1–Q6, per control. "Effect latency" = time from action to the
running system honouring it. Parity = paper/live shared path (all rows: **yes**).

| Control | Mechanism (exists?) | Effect latency | Restart required? | Race surface | New-code footprint |
|---|---|---|---|---|---|
| **Emergency Halt** | `systemctl stop trading-system` (EXISTS; SIGINT graceful, Q5) | seconds (≤30s) | is a stop; start to resume | none (whole process ends; broker SL/GTT stand) | none |
| | in-process loopback endpoint → `kill_switch.soft_kill()/hard_kill()` (DOES NOT EXIST — capability fact Q4) | immediate (same-process, RLock-guarded) | no | KS locking already designed for cross-thread trips | small (new route + auth) |
| | external DB write of SOFT_KILL row (mechanically possible) | **never while running** (Q1); next boot only | yes (to take effect) | overwritten by next in-process persist | none (but ineffective live) |
| **Halt Clear / Resume** | `deploy/resume.sh` = stop → `clear_kill_switch.py` → start (EXISTS, Q2; `--force` for HARD_KILL) | ~1–2 min (stop 30s + boot) | yes (inherent to the design) | none — process is down during the clear; script aborts start on failed clear | none |
| | `clear_kill_switch.py` alone while trader up (EXISTS but…) | **never** — memory stays killed; DB diverges until next persist (Q2) | yes | divergence + silent overwrite | none (ineffective live) |
| | in-process endpoint → `resume()` (DOES NOT EXIST — capability fact Q4) | immediate | no | must respect KS semantics (same-day emergency kill etc.) | small |
| **Restart Service** | `sudo systemctl restart trading-system.service` (EXISTS; ubuntu NOPASSWD ALL, Q6) | ~30–60s (SIGINT drain + boot + reconcile prepass) | n/a (is the restart) | none new — boot path is the daily-tested one | none (invocation only; from GUI = small subprocess/ssh wrapper) |
| **Pause Strategy** | edit strategy YAML `enabled:false` + restart (EXISTS — the only operator path today, Q3) | at next boot | **yes** (no hot reload — explicit `config_loader.py:38`, `loader.py:27`) | none; `config/` edits are auditd-watched (Q6) | none |
| | governor `_paused_today` set via in-process endpoint (set EXISTS `strategy_governor.py:42`; endpoint DOES NOT) | immediate | no | in-memory only — lost on restart / new day (matches "pause today" semantics factually) | small |
| | hot YAML/config re-read | — | — | — | **DOES NOT EXIST** (Q3) |
| **Resume Strategy** | restart with YAML `enabled:true` (EXISTS) | at next boot | yes | none | none |
| | remove from `_paused_today` via in-process endpoint (DOES NOT EXIST) | immediate | no | governor may legitimately re-pause on its own breach math (self-consistent) | small |
| *(cross-cutting)* | file-flag polling by a live loop | poll interval | no | new poll path | **DOES NOT EXIST today** for any control (sentinel files are outbound-alert delivery only); would be new code |

---
*Companion facts: G0 §3 (control plane ≈ clear-kill-switch + restart only), §6.2 (auditd).
Written for the Web-Claude G3 design pass; nothing here alters tonight's GO sequence.*
