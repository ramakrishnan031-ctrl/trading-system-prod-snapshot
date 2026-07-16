# 16-Jul-2026 Morning Verify + Pre-Market Soft-Kill (Planned Pause)

**Date (IST):** 2026-07-16 (Thursday) · **Author:** VS Code Claude (operator-directed)
**Scope:** Verify the first live boot on the 15-Jul combined deploy; then, absent a genuine market-open reason to run, apply a soft-kill so the system does not trade today (Rama pauses for pending fix/review work).
**Outcome:** **PLANNED PAUSE APPLIED.** SOFT_KILL active (operator, logged reason); trading service HALTED (designed exit-4); **book verified FLAT at the broker.** No code changes; no push.

> ⚠️ **The pre-market window was MISSED.** Task was picked up at **09:56 IST**; SSH round-trips + discovering the correct VM path consumed the ~4-minute runway and the market opened (10:00) before a clean verify+kill could complete. The system therefore ran a **live session from 10:00** and took **3 positions** (all closed cleanly on their own). The soft-kill was ultimately applied **in-market at 10:54**, with Rama's explicit authorization, after the desk had returned flat.

---

## Timeline (IST)

| Time | Event |
|---|---|
| 09:56 | Task picked up. VM/bare HEAD = `c9fb298`, tag `deploy-15jul-combined` confirmed. |
| 10:00:47 | **Market opened during probing.** Discovered live working-tree path is `/home/ubuntu/systems/trading-system` (not `~/trading-system`). alert-watcher anomaly spotted. |
| 10:00:24 | Engine attempted a live MRPL entry → **rejected by slippage guard** (₹0.83 > ₹0.31). No fill. |
| 10:02–10:07 | Verified: canary green, clean boot, token fresh. Engine opened **ACI** (10:07:10) and **HUHTAMAKI** (10:07:41). |
| ~10:08 | Briefed Rama; decision #1 → "Soft-kill + flatten ACI". |
| 10:15–10:24 | Found: **no live halt/flatten API** (kill switch in-memory, loaded at boot only). **ACI had already stopped out** (SL filled; trade-status lag). HUHTAMAKI was the lone open position (LIMIT_TRIPLE, engine-enforced OCO). A 3rd position **PRAVEG** entered 10:39. |
| ~10:45 | Decision #2 → "flatten in Kite, now continue". |
| 10:48 | **HUHTAMAKI hit its target (win)**; **PRAVEG closed**. All three positions now closed by the engine's own management. |
| 10:51–10:52 | Book flat; **zero orphan resting orders**. |
| 10:54:28 | **SOFT_KILL applied** (stop → set → start), reason = *"planned pause for pending fix/review work — no trading issue"*, triggered_by = `operator`. |
| 10:55:22 | Boot → `startup_scenario=HALT` → **exit-4** (designed halt-on-active-kill). Service settled `failed` (restart-prevented). |
| 11:01–11:04 | Verified halt is stable (won't flap), monitoring crons intact, and **broker account FLAT** (direct `positions()` read). |

---

## STEP 1 — Boot verification (first live boot on new code)

| Check | Result | Evidence |
|---|---|---|
| 08:15 boot CLEAN — no migration / MigrationNotPermitted / boot-abort | ✅ | `trading-system.service` active since **08:15:03**, `ExecMainStatus=0`, `NRestarts=0`; boot log has **no migration line**. |
| Schema still v44 | ✅ | `schema_meta.schema_version = 44` (the `PRAGMA user_version=0` is a red herring — app tracks version in its own table). |
| Deployed code == `c9fb298`; tag `deploy-15jul-combined` present | ✅ | `git --git-dir=~/trading-system.git rev-parse HEAD` = `c9fb298…`; `describe` = `deploy-15jul-combined-3-gc9fb298` (HEAD = tag + 3 docs commits). |
| Token auto-refresh succeeded | ✅ | `data_store/session/zerodha_token.json` @ **08:15**; `token_watcher.log`: "Fresh token detected (daily start)… Starting service." |
| Pre-market checks completed normally | ✅ (inferred) | System booted to full live trading and correctly took/managed 3 trades; `preflight.log`, `cron-refresh-instruments.log` present. |
| **Monitoring fixes working (canary 08:20)** | ✅ | `✅ email: SMTP login OK` · `✅ telegram: bot token valid` · `✅ sentinel: 0 pending (ok)` · `✅ dashboard: gui-dashboard=active`. **F0 SMTP restore holds — email GREEN.** F4 date-stamped `alert_watcher_2026-07-16.log` present. |
| eod_cleanup / generate_screened_csv anomaly (pre-market) | ✅ none | Those run at EOD (15:50/15:55 cron); no morning error. |
| alert-watcher NOT crash-looping | ⚠️ **Deviation** | See Finding 1 — it is in a benign 10-second *clean-exit* respawn loop (unit `Restart=always`), not a crash. Pre-existing; non-trading. |
| No open positions at start | ✅ at boot | Kill switch INACTIVE after auto-clear of yesterday's stale kill; book flat at boot. (System then traded post-open — see below.) |
| Kill-switch state NORMAL | ✅ | Boot auto-cleared yesterday's stale `SOFT_KILL` (`circuit_breaker_force_close_15:15`, by `order_monitor`, from 15-Jul) → INACTIVE (headless new-day behavior). |

**Step-1 verdict:** the deploy is **healthy** and yesterday's monitoring fixes are **working** on the first real boot (canary all-green incl. email). The only deviation is the pre-existing alert-watcher respawn loop (non-trading).

---

## STEP 2 — Conditional decision

**Was there a genuine market-open reason the system needed to be ACTIVE today?** **No.** The three trades were normal within-caps operation and all closed cleanly; no open positions require management; no corporate action or instrument issue surfaced. → **Case A (planned pause).**

Because the pre-market window was missed and a Step-1 deviation (alert-watcher) plus a materially-changed live state (open positions) were present, the kill decision was **referred to Rama in-market** (per the instruction's "report before acting in market hours / on anomaly"). Rama authorized the pause. The soft-kill was then applied.

### Kill action taken
- **Mechanism:** `systemctl stop` → set SOFT_KILL in DB via `KillSwitch.soft_kill()` (engine down = race-free) → `systemctl start`.
- **Timestamp:** 2026-07-16 **10:54:28** IST.
- **Reason (logged verbatim):** `planned pause for pending fix/review work — no trading issue`
- **triggered_by:** `operator`
- **Result:** on restart, `main.py` detected the active same-day operator kill → `startup_scenario=HALT` → **exited code 4** (the designed "refuse to run under an active kill; operator must `--resume`" safety, Audit Issue #18). `RestartPreventExitStatus=3 4` means systemd does **not** restart on exit-4 → service settled cleanly `failed` (NRestarts=0), **no hammer-loop**.

> **Important nuance:** this system has **no "running-but-soft-killed" state at boot** — any active kill at startup HALTS the service. So "soft-kill applied" here necessarily means the **trading service is halted/down**, not running-and-blocking-entries. Because the book is flat, no reconciler backstop is needed, so the halt is a safe, correct end-state that fully satisfies "no trading today."

---

## Final state (verified)

- **Broker account: FLAT** — `positions()` net nonzero = **0**; day symbols balanced (ACI 1/1, HUHTAMAKI 2/2, PRAVEG 1/1, all net 0); holdings = 0. **No manual Kite short** (buys = sells per symbol).
- **DB: FLAT** — 0 non-terminal trades; no orphan resting orders.
- **trading-system.service: HALTED** (exit-4, `failed`, stable, restart-prevented).
- **SOFT_KILL active** — `kill_switch_state`: SOFT_KILL / operator / 10:54:28, reason as above.
- **Independent monitoring & EOD crons intact** — canary (ran 08:20 ✓), cron_officer 09:20, eod_cleanup 15:50, eod_verify 15:55, backups — all run regardless of the trading service → **EOD emails + visibility continue**.

### Today's trades (all closed cleanly by the engine)
| Symbol | Entry | Exit | Outcome |
|---|---|---|---|
| ACI | 10:07:10 | 10:17:49 | stopped out (SL); TGT cancelled via OCO |
| HUHTAMAKI | 10:07:41 | 10:48:21 | **hit target (win)**; SL cancelled via OCO |
| PRAVEG | 10:39:19 | 10:48:06 | closed cleanly |
| MRPL | — | — | 2 entry attempts, both rejected (slippage guard) — no position |

---

## How to resume trading
- **Automatic (tomorrow):** the 08:15 token-watcher start will boot and `clear_stale_state` auto-clears the (by-then prior-day) kill → clean start. No action needed if pausing only today.
- **Manual (today, if desired):** `deploy/resume.sh` (stop → clear kill → start), or `python scripts/clear_kill_switch.py` then `sudo systemctl start trading-system`.

---

## Findings / notes (for follow-up, not fixed here)

1. **alert-watcher respawn loop (pre-existing, non-trading).** Unit `alert-watcher.service` is `Restart=always`; the script now exits **cleanly (status 0)** every ~10s → systemd respawns it forever (~101,570+ restarts). Yesterday's F1 fixed the *crash* (exit 2→0) but the **unit-level restart loop persists** in a benign form. Canary confirms alert *paths* are green (email/telegram/sentinel), so functional alerting is OK. Recommend a monitoring-hardening follow-up (make it a `oneshot`+timer, or long-running daemon, or `RemainAfterExit`). **Not touched today (no code changes in scope).**
2. **Path correction.** The live working tree is **`/home/ubuntu/systems/trading-system`** (venv `/home/ubuntu/systems/venv`); bare repo `~/trading-system.git`. Recorded to memory; verify PATHS/SYSTEM_MAP reflect `systems/`.
3. **DB status can lag broker truth.** ACI showed `OPEN` for ~minutes after its SL had actually filled (`SL` leg COMPLETE) — a naive "flatten ACI" off the DB status would have opened a **naked short**. Any manual flatten must be done against **broker truth**, with the engine's exit-manager stopped (it enforces software-OCO for LIMIT_TRIPLE; Zerodha regular orders have no broker-side OCO).
4. **Yesterday's stale kill.** Boot auto-cleared a 15-Jul `SOFT_KILL (circuit_breaker_force_close_15:15, by order_monitor)`. Cleared as designed; worth a glance in the 15-Jul EOD review that it was benign.
