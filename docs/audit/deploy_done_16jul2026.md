# DEPLOY DONE — 16-Jul-2026 (alert-watcher `--loop` + F1 sector, consolidated)

**Executed:** 16-Jul-2026, 18:42–18:50 IST (OFF-MARKET — past the 15:30–17:05 EOD-cron
window, before 08:15). Executor: VS Code Claude, on Rama's handed instruction
(`VSCODE_INSTRUCTION_EXECUTE_DEPLOY_16-Jul-2026_FINAL.txt`, v2).
**Outcome: DEPLOYED + VERIFIED.** No code changed during the deploy. No schema change.

---

## 1. What was deployed

`c9fb298..11abebb` — one push, two tracks, both behaviour-neutral to the trading path:

- **alert-watcher `--loop` fix** (monitoring-only; does not touch trading-system) — the
  service was `--once` + `Restart=always` + `RestartSec=10`, so systemd respawned it every
  ~10s. Now the long-lived `--loop` daemon + `Restart=on-failure` + `TimeoutStopSec=35` +
  `StartLimitIntervalSec=300`/`StartLimitBurst=5`. Ships with the canary's 5th probe
  (`respawn`) so a churning service can never read HEALTHY again.
- **F1 — `trades.sector` populate-at-insert + gate-8 `observe`** — `trades.sector` was NULL on
  100% of rows, so `sector_exposure()` summed 0 and the 40% sector cap never saw the resting
  book. Now resolved at insert from `InstrumentCache.sector` (`orders/order_placer.py:955`),
  and gate-8 runs in `sector_cap_mode: observe` (logs `WOULD_REJECT`, **does not reject**).

**Deployed behaviour change today: none.** F1 is log-only until the Rama-gated enforce flip;
alert-watcher is monitoring.

---

## 2. The one deviation from the instruction — approved

The instruction (§3/§9) pinned local `main` at **`f68d15d`** and made any mismatch a STOP.
Actual local `main` at run time was **`11abebb`** — two *docs-only* commits had landed after
the instruction was drafted (`63dbb38` M-C8 investigation report, `11abebb` SYSTEM_MAP note).

Proven inert before proceeding:

```
git diff --name-only 1d5337d..11abebb
  PATHS.md
  docs/SYSTEM_MAP.md
  docs/audit/consolidation_16jul2026.md
  docs/audit/mc8_investigation_16jul2026.md
  docs/audit/mc_cluster_investigation_16jul2026.md
→ 5 files, +366 lines, ALL markdown. Non-markdown delta since the validated tag: EMPTY.
```

So the **code** at `11abebb` is byte-identical to the regression-green tag `1d5337d`. This is
the same pattern the instruction's own §0 established (code == the tag, plus inert docs on
top) and §4b anticipated ("F1+alert-watcher code + docs land"). **Rama was asked and
explicitly approved pushing `main` @ `11abebb`.** Deployed bare HEAD is therefore `11abebb`,
not the instruction's expected `f68d15d`.

> Process note: two verification commands initially mis-reported because PowerShell parses
> `^{commit}` as a scriptblock and `$var..HEAD` as a range operator — git errored, the empty
> output read as a pass, and one check printed a **false** "TAG NOT IN MAIN => STOP". Both
> were re-run under bash before any push. Verification that "passes" on an errored command is
> not a pass; check the rc, not just the output.

---

## 3. Push

```
git push origin main   → c9fb298..11abebb  main -> main
  remote: Deploying main to /home/ubuntu/systems/trading-system...
  remote: post-receive: crontab AUTO-INSTALLED from canonical.
  remote: Deployment complete.
git push origin deploy-16jul-alertwatcher-f1  → [new tag]
```

Pre-deploy backup taken first: `data_store/backups/pre_deploy_16jul.db` — 357,146,624 bytes,
`integrity_check` **ok**, 361 trades, schema_meta v44.

---

## 4. Deploy verification (§4c / §7) — ALL PASS

| Check | Result |
|---|---|
| bare HEAD == expected | `11abebb` ✅ (Rama-approved; instruction expected `f68d15d`) |
| tag `deploy-16jul-alertwatcher-f1` (`1d5337d`) in deployed history | ✅ ancestor of HEAD |
| M-C4 `6c77525` absent from main | ✅ absent (deploys separately) |
| post-receive checkout landed | ✅ `orders/order_placer.py:955` `sector=self._resolve_trade_sector(symbol)`; `capital/risk_engine.py` observe-mode; `--loop` in the unit file; canary `_classify_respawn` |
| schema | ✅ **v44**, unchanged — no migration (v44 == `EXPECTED_SCHEMA_VERSION`) |
| DB integrity / FK | ✅ `integrity_check` ok, `foreign_key_check` clean |
| `sector_cap_mode` | ✅ `observe` in `config/system_config.yaml:187` — behaviour-neutral |
| working tree clean / no code modified | ✅ zero modifications |
| services | ✅ token-watcher `active`, gui-dashboard `active`, alert-watcher `active/running` |

---

## 5. alert-watcher track — fixed, soak started

```
BEFORE: ActiveState=activating  SubState=auto-restart  NRestarts=104,567 (climbing)
AFTER:  ActiveState=active      SubState=running       NRestarts=0
        MainPID=1318632  ExecMainStartTimestamp=Thu 2026-07-16 18:47:03 IST
        /home/ubuntu/systems/venv/bin/python .../scripts/alert_watcher.py --loop
```

Unit installed to `/etc/systemd/system/`, `daemon-reload`, `reset-failed` (clears the stale
104k counter so the soak baseline starts at 0), `restart`.

**Single-instance PROVEN** via the unit cgroup — `TasksCurrent=1`, one PID in
`systemd-cgls -u alert-watcher.service`. (A raw `pgrep -fc alert_watcher.py` returns **2**;
that is the ssh shell self-matching its own command line, not a second daemon. Confirmed with
the bracket trick: `ps -eo args | grep -c '[a]lert_watcher.py'` → **1**.)

### 24h soak baseline — 2026-07-16 18:47:50 IST, PID 1318632

| metric | baseline |
|---|---|
| RSS | 37,548 kb (~36.7 MB) |
| VSZ | 50,264 kb |
| threads (nlwp) | 1 |
| fd count | 4 |
| DB fds | 0 |

**Re-sample ≥24h (≈17-Jul 19:00 IST) and compare. ANY sustained growth in RSS / fd / threads /
DB handles → ROLLBACK** (revert the unit to `--once` + `Restart=always`; the F1 crash-loop
cause is already fixed, so a revert is loop-only). Also confirm `NRestarts` stays flat.

### Canary — ALL GREEN including the new respawn probe

```
🐤 [LFL836] Monitoring Canary — 16-Jul 18:49
✅ email: SMTP login OK
✅ telegram: telegram bot token valid
✅ sentinel: 0 pending (ok)
✅ dashboard: gui-dashboard=active
✅ respawn: alert-watcher.service: NRestarts=0 (baseline)
rc=0
```

Run via `main()` (not `_cron_main()`) so it did **not** write an off-schedule cron_heartbeat
row. It sent nothing — `_deliver_report` is quiet when `overall_ok` (`monitoring_canary.py:290`).
The probe wrote its baseline to `data_store/canary_service_state.json`; tomorrow's 08:20 canary
compares against this sample.

---

## 6. F1 track — live in observe; soak starts with the next session

F1 is live and behaviour-neutral: `trades.sector` fills at insert; gate-8 logs
`sector_cap_would_reject … WOULD_REJECT` **without rejecting**. **Enforce was NOT flipped.**

**The observe soak collects on the next trading session (Fri 17-Jul)** — see §7: the engine is
currently paused, so no data accrues tonight.

Collect + archive over ≥1 full session: UNKNOWN-sector % · WOULD_REJECT count · sector
distribution · DQ-alert count · any unexpected concentration. Extend to a 2nd session only on
anomalies. Then → short evidence report → **Rama's explicit approval** → `sector_cap_mode:
observe→enforce` as a SEPARATE off-market step. Instant de-fang = set back to `observe`.

---

## 7. State of the trading engine (context — not a deploy fault)

`trading-system.service` reads `ActiveState=failed`, `ExecMainStatus=4`. **This is the designed
halt-on-active-kill, not a failure**: Rama's planned pause from this morning.

```
kill_switch_state: SOFT_KILL | 2026-07-16T10:54:28+05:30 | operator
                 | "planned pause for pending fix/review work — no trading issue"
```

**Tomorrow's 08:15 boot auto-clears it and trades normally** — verified in code, not assumed:
`main.py:1607` calls `kill_switch.clear_stale_state(today)`, which clears **any** kill
triggered on a *previous calendar day* regardless of type (the 2026-06-20 HEADLESS
GUARANTEE, `kill_switch.py:199-248`). The 10:55 journal line "Manual --resume required" came
from the *same-day* path (`auto_clear_scheduled_kill`, `:273-279`), which refuses non-scheduled
reasons — it does not apply to a next-day boot. Both the ledger's "auto-clears at 08:15" and
the journal's "manual resume" are correct, for different paths.

No action needed for the F1 soak to begin. (`deploy/resume.sh` would resume it sooner.)

---

## 8. Rollback

- **L1** — per-branch revert of the merge; off-market restart for the unit.
- **L2** — reset main to `3b9041a` (pre-merge) or to the tag `1d5337d` (code point).
- **F1 sector cap** — instant CONFIG de-fang: `sector_cap_mode` → `observe` (already there).
- **alert-watcher** — revert the unit to `--once` + `Restart=always`.
- **DB** — `data_store/backups/pre_deploy_16jul.db` (pre-deploy, verified).

---

## 9. Owed next

1. **alert-watcher 24h soak** — re-sample ≥17-Jul ~19:00 vs the §5 baseline.
2. **F1 observe soak** — ≥1 full session (starts Fri 17-Jul) → evidence report → Rama-gated
   enforce flip.
3. **M-C4** (`mc4-killswitch-lock-16jul` @ `6c77525`) — still UNPUSHED, its own later deploy.
4. **DEPLOYMENT.md** stale `.env`/backups paths (missing `systems/`) — off-market `ls` verify
   then correct.
</content>
