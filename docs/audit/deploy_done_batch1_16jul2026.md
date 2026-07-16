# DEPLOY DONE — BATCH-1 (16/17-Jul-2026) · **PC == VM, 100% synced**

**Executed:** 16-Jul-2026 23:54–23:55 IST (OFF-MARKET — past 17:05, before 08:15).
Executor: VS Code Claude, on Rama's handed instruction.
**Outcome: DEPLOYED + VERIFIED.** No code changed during the deploy. No schema change.

**VM == bare == PC `main` == `eb9cffa`** (was `06a61cb`) · **tag `deploy-17jul-batch1` →
`eb9cffa`** · schema **v44 unchanged (no migration)**.

> ## ✅ THE GOAL: PC == VM
> ```
> PC main : eb9cffaae657
> VM bare : eb9cffaae657
> ==> 100% SYNCED
> ```
> For the first time in this cycle there are **no unpushed commits** — not even docs.
> The batch tag IS the deployed HEAD, so the code-identity delta is not merely
> markdown-only, it is **empty**.

---

## 1. What was deployed — 15 batch-safe items

Full detail: `docs/audit/batch1_done_16jul2026.md`. Nothing here changes **what or when
the system trades**. Highlights:

- A **1.44 MB base64-XLSX containing real trade data**, committed past the `*.xlsx`
  ignore — deleted, and the encoded-form gap closed.
- The **live access token** was printed to the cron log every run — stopped.
- **DEPLOYMENT.md pointed at a directory that does not exist** (`/home/ubuntu/trading-system/.env`);
  the near-miss `trading-system.git` IS real, which is why it read as correct.
- The **missing README** (585 files, live since 11-May, `ls README*` empty).
- **M-K5**: populated secrets no longer reach `config_snapshots` (a durable table that
  rides into every backup).
- The **GUI logged Rama out on every restart** (`secret_key` configured nowhere).
- Two different **win rates on one report** (61W/91L/1 breakeven → 39.87% vs 40.13%).
- **`daily_trade_review` had no non-trading-day guard** — `market_day_only` is metadata;
  nothing enforced it.
- An **unreadable cron registry silently downgraded a critical FAILED alert**, losing the
  email fallback exactly when the config was broken.
- A **test vacuous on Windows** that was also creating the register's NR-4 "stray D:\ folders".

---

## 2. Verification — all gates pass

| Check | Result |
|---|---|
| Off-market clock | ✅ 23:54 IST |
| Starting VM/bare (live, not cached) | ✅ `06a61cb` |
| Batch tag exists + ancestor of HEAD | ✅ `deploy-17jul-batch1` → `eb9cffa` |
| **Code-identity gate (pre-push)** | ✅ **EMPTY** — the tag *is* HEAD |
| Pre-deploy backup | ✅ `pre_deploy_batch1.db` — 357,146,624 B, `integrity_check` **ok**, 361 trades, v44 |
| Push | ✅ `06a61cb..eb9cffa` + tag; post-receive checkout + crontab auto-install |
| **Deployed bare HEAD** | ✅ **`eb9cffa` == the confirmed local HEAD ⇒ PC == VM** |
| Code-identity gate (post-push, on bare) | ✅ empty vs the tag |
| Checkout landed | ✅ README.md / DEPLOYMENT.md mtime 23:54:45 |
| Schema | ✅ **v44 unchanged — no migration** |
| DB integrity / FK | ✅ `integrity_check` ok · `foreign_key_check` clean |
| Services | ✅ token-watcher, alert-watcher, gui-dashboard all active |
| `trading-system.service` | ✅ `failed`/exit-**4** = the designed halt-on-active-kill (planned pause), **not a fault** |
| All 15 items in the checked-out tree | ✅ verified individually (see §2.1) |
| Deployed modules compile · config validates | ✅ COMPILE OK · CONFIG_LOAD_OK |
| PC tree clean / no code modified during deploy | ✅ 0 modified |

### 2.1 The 15, verified in the deployed tree

```
B1  base64-XLSX gone + ignore gap closed             OK
C1  no token slice in the cron log                   OK
A1  DEPLOYMENT.md .env path corrected                OK
A2  SYSTEM_MAP HOW-TO-READ + INDEX                   OK
A3  PATHS honest header                              OK
A4  README + ops/__init__                            OK
F1  sqlite3 hoisted to module level                  OK
F4  candle date via today_ist()                      OK
F5  entry_start_time explained (value UNCHANGED)     OK
F3  M-K5 config-snapshot redaction                   OK
F2  GUI session key persisted                        OK
F6a headline win% uses the one denominator           OK
F6b daily_trade_review holiday guard                 OK
D1  portable unwritable-path test                    OK
C2  cron_alerts tri-state escalation                 OK
```

---

## 3. Go-live — nothing owed, and when each item actually bites

**No restart was owed or performed.** Deploy ≠ restart: a push checks code out, it does
not reload a running service.

| Item | Takes effect |
|---|---|
| **GUI session key (F2)** | at the GUI's **next restart**. `gui-dashboard` is `active/running` on the OLD code right now, so its next restart is the **last one that will log Rama out**. `data_store/session/gui_secret_key` does not exist yet — correct: the fix is lazy and writes on first use. |
| **Cron items** (F6a/F6b win% + holiday guard, C1 token, F1/F4 candles, C2 alert escalation) | on each job's **next scheduled run**. |
| **M-K5 redaction (F3)** | at the next `main.py` boot (the snapshot is written at startup). Verified byte-identical on the real config ⇒ no spurious snapshot row. |
| **Docs (A1–A4)** | immediately. |
| **trading-system** | halted on the planned operator SOFT_KILL. |

**Boot-readiness, verified rather than assumed:**
- deployed `EXPECTED_SCHEMA_VERSION = 44` == live `schema_version = 44` ⇒ the 08:15 boot
  **skips migration**. No migration sentinel has fired.
- `kill_switch_state` = `SOFT_KILL dated 2026-07-16` ⇒ at the 17-Jul 08:15 boot that is a
  **prior-day** kill and `clear_stale_state` (`main.py:1607`) wipes it. **No operator
  action.**

---

## 4. Owed / carried forward (for the LOOP + backlog queue)

- **🔴 E4 CAPITAL FINDING — fix is LOOP.** CHECK1/RMS/manual closes pass `costs=0.0` into
  `release_used`, where `pnl = gross_pnl - costs` ⇒ the **daily-loss limit's input**
  (`fm_ledger.pnl_delta`) **and available capital** are credited with **gross, not net**.
  The operational note calls this "slightly optimistic"; this book is **gross −0.007R vs
  net −0.100R**, so costs *are* the loss and the control is fed the wrong number.
  Recorded in `capital_operational_note`.
- **P3-s14 — ESCALATED to LOOP.** The webhook "300s dark window" is not missing logging:
  `_process_signal` has no generic handler around the INSERT, so the **dedup claim is
  never rolled back** and retries are DUPLICATE-bounced. The fix is that rollback =
  **control flow deciding whether a signal enters at all**.
- **6 deferred** — A5 (gitignored Word binaries, Rama's to author) · A6 (**underspecified**
  — one table row, no content) · pytest skew (a decision: root `>=9.0.3` vs GUI `==8.3.4`)
  · C4 (F2 functional-criterion tail: ~20 per-job decisions) · C5 (B-1 metrics design) ·
  **D2 (impossible as specified: `ct_utils.py:63` hardcodes the LIVE DB, opened writable,
  as a module constant)**.
- **Group-E outcomes:** **M-SC2 CLOSED, runtime-proven** (screened CSV 1 row/40 B →
  **25 rows/2,033 B** on 16-Jul) · **BK-8 core CLEAN** (no hardcoded config defaults in
  capital/orders/signals/screening/core — previously unverified; residual = 5 scripts
  defaulting `TRADING_MODE` to `"live"`) · **F4 auto-clear benign** (clean daily
  `KILL_AUTO_CLEARED` cadence) · **E3/P3-r8 blocked on Rama** (the contract note lives
  outside the repo).

**Still owed from earlier today:** the F1 gate-8 **observe soak** (first data Fri 17-Jul)
→ Rama-gated enforce flip · the **alert-watcher 24h soak** re-sample · **the first live
HARD_KILL is M-C8's real test** (the drill was mock-broker only).

---

## 5. Rollback (all schema-free)

- **L1** — revert an individual item commit (each of the 15 is independent).
- **L2** — reset `main` to the pre-batch base `362e166` (= the deployed M-C cluster
  `06a61cb` + the classification docs commit).
- **DB** — `data_store/backups/pre_deploy_batch1.db` (pre-deploy, verified).

Any rollback needs an off-market push; no restart is owed while the engine is halted.
</content>
