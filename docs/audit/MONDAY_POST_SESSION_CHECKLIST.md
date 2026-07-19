# MONDAY POST-SESSION VERIFICATION CHECKLIST — run after 18:15 IST

**Written 19-Jul-2026 (Sunday). RUN Monday 20-Jul after 18:15 IST** (so the 18:15 forward-shadow recorder
has fired). **Read-only throughout. This checklist VERIFIES and REPORTS — it fixes nothing** (Monday's
fixes, if any, go through the careful loop afterwards, see §D).

**Why row-level, not hash-level:** by Monday evening the live DB will differ completely from the 19-Jul
baseline *because it traded* — a hash check would fire on a perfectly healthy day. So every check below is
**row-level / behavioural**, against the 19-Jul baseline.

### How to run
SSH to the VM; the service will still be **active** (it does not stop after squareoff), so query with
`mode=ro` (never `immutable` — that would miss the live WAL):
```
ssh trading-vm
cd ~/systems/trading-system
Q() { sqlite3 "file:$PWD/data_store/trading_system.db?mode=ro" "$1"; }   # helper; mode=ro, read-only
```
**NEVER** run `scripts/forward_shadow_record.py` (reading the JSONL is `python3`/`cat` only). **NEVER**
`scripts/*.py --db <copy>`.

### 19-Jul baseline to diff against (VM `data_store/`)
- live DB `a7a1de53a0e398a0` · 89,968,640 · mtime 18:00:17 — **row counts:** `signals` 32,928 · `trades`
  361 · `orders` 610 · `fm_ledger` 2,160 · `kill_switch_state` 1
- forward shadow `f7c964fd79dec961` · **7,827 lines** · 16-Jul 18:15:21
- analytics `bb229f4474bb37c0` · 24,133,632 · 02:30:04
- daily-loss threshold ≈ **Rs 296** (3% × Rs 9,875.60 actual capital); worst-ever intraday cum was **−56.47**.

### STEP 0 — capture first (forensic; the system is LIVE so this RECORDS state, it does not prove "untouched")
```
hostname; date +%Y-%m-%dT%H:%M:%S%z
for f in data_store/trading_system.db data_store/v3/forward_shadow_fs-v1.jsonl data_store/analytics.db; do sha256sum "$f"; stat -c '  %n size=%s mtime=%y' "$f"; done
Q "SELECT 'max_heartbeat_id', MAX(id) FROM cron_heartbeat;"
```
Keep this output — it is the reference for any later diagnosis.

---

## A. The session

### A1 — DID IT BOOT, AND DID IT STAY UP? (the S4 clean-death is the specific thing to rule out)
```
systemctl is-active trading-system.service; systemctl show trading-system.service -p ExecMainStartTimestamp -p ActiveState -p SubState -p NRestarts
grep -iE 'LIVENESS|is DOWN' logs/cron-liveness.log 2>/dev/null | tail
ls -la data_store/critical_alert_*liveness* 2>/dev/null; ls -la data_store/critical_alert_* 2>/dev/null | tail
```
- **GOOD:** `ActiveState=active` (running), `ExecMainStartTimestamp` = Mon ~08:15, `NRestarts=0`, and **NO
  `LIVENESS: … is DOWN` alert** anywhere in [09:00, 16:00). The liveness probe ran every 5 min and stayed
  SILENT (silent = healthy).
- **BAD (the S4 signature):** service `inactive`/`dead` with a **clean exit 0** and `NRestarts=0` — looks
  healthy to systemd but took 0 trades. The tell is a **`LIVENESS: trading-system.service is DOWN`
  CRITICAL** (Telegram + `critical_alert_*` sentinel) fired at ~09:00. A silent clean death, NOT a crash,
  is what to look for. (If BAD → §D; do not fix in a hurry.)

### A2 — DID IT TRADE, AND IS THE VOLUME SANE? (surprising in EITHER direction)
```
Q "SELECT (SELECT COUNT(*) FROM signals) - 32928 AS d_signals, (SELECT COUNT(*) FROM trades) - 361 AS d_trades, (SELECT COUNT(*) FROM orders) - 610 AS d_orders, (SELECT COUNT(*) FROM fm_ledger) - 2160 AS d_fm_ledger;"
Q "SELECT status, COUNT(*) FROM trades WHERE date(created_at)='2026-07-20' GROUP BY status;"
Q "SELECT COUNT(*) scored FROM screener_results WHERE date(ts)='2026-07-20';"
Q "SELECT substr(created_at,12,2) hr, COUNT(*) FROM trades WHERE date(created_at)='2026-07-20' GROUP BY hr ORDER BY hr;"
```
- **GOOD (record shape):** `d_trades` ≈ **6** entered (~3 CLOSED winners), `scored` ≈ **1,450**,
  ~975 risk-engine approvals, **~87% of entries in the 10:00 hour**. A quiet afternoon is normal.
- **BAD, either direction:** **`d_trades = 0`** → it did not trade (check A1 first — did it boot?).
  **`d_trades` ≳ 25**, or `scored` ≪ 500 or ≫ 3,000 → anomalous volume; capture and report, do not act.

### A3 — CAPITAL INTEGRITY
```
Q "SELECT ROUND((SELECT COALESCE(SUM(pnl_delta),0) FROM fm_ledger WHERE date='2026-07-20'),2) AS ledger_pnl, ROUND((SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE date(exit_time)='2026-07-20' AND status IN ('CLOSED','CLOSED_MANUAL')),2) AS trades_net;"
Q "SELECT entry_type, ROUND(amount,2) amount, ts FROM fm_ledger WHERE date='2026-07-20' AND entry_type='RESET_PNL';"
Q "SELECT ROUND(SUM(pnl_delta),2) sum_pnl, ROUND(SUM(costs),2) sum_costs, ROUND(-(SUM(pnl_delta)-SUM(costs)),2) AS expected_reset FROM fm_ledger WHERE date='2026-07-20';"
grep -iE 'invariant|hard_kill|CAPITAL.*mismatch' logs/system-manager.log logs/*.log 2>/dev/null | grep -i 2026-07-20 | tail
```
- **GOOD:** `ledger_pnl == trades_net` (the ledger reconciles to the trades). Exactly **one `RESET_PNL`**
  row, and its `amount == expected_reset` = **−(Σpnl_delta − Σcosts)**. This gross-based reset is the
  **EXPECTED Option-B signature** — E4/W10 has NOT shipped, so `−(Σpnl_delta − Σcosts)` is correct, *not*
  a defect. No capital-invariant error in the logs (production asserts `available+reserved+used==total`
  itself at `fund_manager.py:2268`; a break would have hard-killed).
- **BAD:** `ledger_pnl ≠ trades_net`; RESET_PNL missing, duplicated, or ≠ `expected_reset`; OR any
  `available+reserved+used` invariant / spurious `hard_kill` in the logs. (If `RESET_PNL == −Σpnl_delta`
  *instead* — E4/W10 would have shipped, which it hasn't — that too is unexpected: report it.)

### A4 — THE DAILY-LOSS CONTROL (report the number regardless — it feeds decision 01)
```
Q "SELECT ROUND(MIN(cum),2) AS worst_intraday_cum FROM (SELECT SUM(pnl_delta) OVER (ORDER BY ts) AS cum FROM fm_ledger WHERE date='2026-07-20' AND pnl_delta != 0);"
Q "SELECT COUNT(*) FROM kill_switch_state WHERE date(triggered_at)='2026-07-20' AND reason LIKE '%DAILY%';"
```
- **GOOD:** `worst_intraday_cum` is well above **−296** (on the record the worst ever was −56.47) and **no
  DAILY-loss kill** fired. **Record the number** either way — it is the ongoing input to decision 01
  (E4/W10) and to the leverage question.
- **BAD:** `worst_intraday_cum ≤ −296` → the daily-loss limit's threshold was reached; confirm it actually
  tripped (a DAILY kill row). If it reached −296 and did NOT trip → a control failure, capture and report.

### A5 — KILL SWITCH & SQUAREOFF
```
Q "SELECT is_active, reason, triggered_at FROM kill_switch_state ORDER BY rowid DESC LIMIT 3;"
Q "SELECT status, COUNT(*) FROM trades WHERE date(created_at)='2026-07-20' GROUP BY status;"
Q "SELECT trade_id, symbol, status, exit_reason, exit_time FROM trades WHERE date(created_at)='2026-07-20' AND status NOT IN ('CLOSED','CLOSED_MANUAL','CANCELLED','REJECTED','FAILED');"
grep -iE 'squareoff|cutoff|reconcil' logs/*.log 2>/dev/null | grep 2026-07-20 | tail
```
- **GOOD:** kill switch **INACTIVE** at close; every Monday trade is terminal (`CLOSED`/`CLOSED_MANUAL`/
  `CANCELLED`) — the third query returns **0 rows** (nothing `OPEN`/`EXITING` survived past 15:17); the
  15:15 cutoff and 15:17 squareoff logged normally; reconciler flagged nothing.
- **BAD:** an `OPEN`/`PARTIAL`/`EXITING` trade after 15:17 (a position survived squareoff); a kill active
  for a non-operator reason; a reconciler mismatch. Capture broker truth (§D) — never flatten from DB state.

### A6 — CRON COMPLETION
```
Q "SELECT job_name, substr(executed_at,12,8) t, status, message FROM cron_heartbeat WHERE date(executed_at)='2026-07-20' ORDER BY id;"
```
- **GOOD:** all expected jobs present and `SUCCESS`: the **08:15 token refresh** (the app started), **08:20
  monitoring_canary** (`[func=OK]`), **09:20 cron_officer_briefing**, **15:50 eod_cleanup — SUCCESS with 0
  rows deleted** (90d retention; nothing ≥90d until ~10-Sep), **16:22 strategy_registry_officer**, **18:15
  forward_shadow_record** (`wrote=N sim=M` — see §B). The cron-officer report should show no MISSED/FAILED.
- **BAD:** any expected job **missing** or `FAILED`; eod_cleanup deleting **>0** rows (retention changed —
  investigate); a MISSED in the officer report. (`backup_retention` FAILED is a *known, benign* safety-abort
  — see the reconciliation report — not a Monday regression.)

### A7 — THE 403 POPULATION (so nobody reads Monday's log as an outage retrospectively)
```
Q "SELECT substr(ts,12,2) hr, response_code, COUNT(*) n, SUM(payload_size_bytes) bytes FROM webhook_audit WHERE date='2026-07-20' GROUP BY hr, response_code ORDER BY hr, response_code;"
```
- **GOOD:** POSTs **before 10:00** return **403** (the `entry_start:10:00` gate rejects every pre-10:00
  POST — ~25,960 of them across the window), then **200** from 10:00 on. This is CORRECT behaviour, not an
  outage. (The pre-10:00 403 payloads carry ~338k uncounted signals — a known, tracked gap, not a fault.)
- **BAD:** 403s **after 10:00** (the entry gate stuck closed), or **0 rows at all** (the webhook receiver
  never came up → cross-check A1).

---

## B. ⭐ THE FORWARD SHADOW — the check that matters most (sole confirmation path for D3/D4/#10/#06)

**The silent-degradation mode (found 19-Jul):** if the Kite token is absent, `_build_kite` returns None
(`forward_shadow_record.py:94-95`, "recording score/decision only") → no candle fetch → empty path →
**`sim_R = None`, and the cron exits 0**, so fail-LOUD never fires. **A file that grew but recorded nulls
is a failure that looks like success — the same shape as S4, one layer over.**

### B1 — exactly ONE new date (>1 = contamination = STOP)
```
python3 -c "import json; d=sorted({json.loads(l)['date'] for l in open('data_store/v3/forward_shadow_fs-v1.jsonl',encoding='utf-8') if l.strip()}); print('dates:',d,'| total_lines:',sum(1 for _ in open('data_store/v3/forward_shadow_fs-v1.jsonl',encoding='utf-8')))"
```
- **GOOD:** the date set is the existing OOS dates **plus exactly one new date, `2026-07-20`**; line count
  = 7,827 + Monday's records (a few hundred).
- **BAD — STOP-and-report (§D3, the one urgent condition):** **more than one new date** appeared (e.g. a
  backfill `--date` run) → the append-only out-of-sample artifact is **contaminated** and the OOS evidence
  for D3/D4 is void. Do not touch the file further; preserve it and report immediately.

### B2 — are the `sim_R` values REAL or NULL? (the S4-shaped failure)
```
python3 -c "
import json
rows=[json.loads(l) for l in open('data_store/v3/forward_shadow_fs-v1.jsonl',encoding='utf-8') if l.strip()]
d=[r for r in rows if r['date']=='2026-07-20']; n=len(d)
nn=sum(1 for r in d if r.get('sim_R') is None); ok=sum(1 for r in d if r.get('ms4_stats_ok'))
print(f'2026-07-20: {n} records | sim_R NULL={nn} ({100*nn/max(n,1):.0f}%) | ms4_stats_ok={ok}/{n}')"
# cross-check against the recorder's own heartbeat (message = 'date=… wrote=N sim=M'):
Q "SELECT message FROM cron_heartbeat WHERE job_name='forward_shadow_record' AND date(executed_at)='2026-07-20';"
```
- **GOOD:** **most `sim_R` are non-null** (null fraction low — some signals legitimately have no post-entry
  path, but the majority should simulate), `ms4_stats_ok` mostly True, and the heartbeat shows `sim=M` with
  **M ≈ wrote**, not M=0.
- **BAD (looks like success):** **`sim_R` NULL ≈ 100%** / `ms4_stats_ok = 0/N` / heartbeat `sim=0` — the
  **token was absent and the recorder ran degraded** ("recording score/decision only"). The file grew, the
  cron exited 0, and nothing alarmed — but the day recorded no out-of-sample outcome. Report it; the day's
  OOS evidence is not usable.

### B3 — the records carry what they should
```
python3 -c "
import json
d=[json.loads(l) for l in open('data_store/v3/forward_shadow_fs-v1.jsonl',encoding='utf-8') if l.strip() and json.loads(l)['date']=='2026-07-20']
r=d[0] if d else {}; import sys
need=['old_score','old_band','ms4_score','ms4_band','decision','reject_reason','sim_R','realized_pnl','git_commit','scoring_weights_sha']
print('sample:',{k:r.get(k) for k in need}); print('missing:',[k for k in need if k not in r])"
```
- **GOOD:** every field present; `old_score`/`ms4_score` numeric, `decision` a real status, `realized_pnl`
  populated for traded signals (non-null where `decision` admitted + a trade_id existed), provenance
  (`git_commit`, `scoring_weights_sha`) stamped.
- **BAD:** `missing` non-empty, or `old_score`/`ms4_score` null across the board (a scorer/config wiring
  problem), or provenance null (can't pin which scorer produced the day).

### B4 — the running out-of-sample total
- After a good Monday the genuine OOS window goes **3 days → 4** (07-14/15/16 → +07-20). Note the new total.
- Capacity, for reference (do not compute anything): **D4** needs ~**17–34 trading days** of winners
  (~50–100) — day 4 is still deep in the underpowered zone. **D3** is roughly powered at 3 days; day 4
  firms the estimate but does not change the picture.

### B5 — do NOT re-run the D3 analysis on day 4
One extra OOS day does not change a verdict — a re-run is worth it only when the number of days *materially*
changes the power picture (a regime change, or reaching ~double the current days). **Note the day-4 total
and move on.** Re-running D3 on every new day is how a noise wobble gets mistaken for a signal.

---

## C. What Monday proves — and what to CAPTURE if a rare event occurs

### C1 — What Monday proves: that the boot does not crash. Nothing more.
Monday 08:15 is a **cold boot on a flat book**, so rehydrate is a **no-op**. Confirm each is in fact 0
(don't assume):
```
grep -iE 'rehydrate|phase 1|phase 2|replay|carryover' logs/system-manager.log 2>/dev/null | grep 2026-07-20 | tail
```
- **GOOD:** Phase 1 replayed **0** trades, Phase 2 carried **0** P&L rows, the M-C1 live-seed carryover was
  **0** (the subtraction a no-op). Monday proves the boot survives the restore path — nothing more.
- **BAD:** any non-zero replay/carryover on a book that was flat since Fri (would mean stale open state) —
  capture and report.

### C2 — Still unproven in production (needs a mid-day restart with live state — probably NOT Monday). If one occurs, the evidence is rare — CAPTURE FIRST, do not clean up:
| Event (if it happens) | Preserve IMMEDIATELY, before any restart/cleanup |
|---|---|
| Phase 1 replay of a **real open position** | `cp` the live DB to a timestamped forensic copy; dump the open `trades` rows + the boot's `system-manager.log` around the restart |
| Phase 2 **realized-P&L carryover** | the `fm_ledger` rows for the day (esp. `RELEASE_USED` + `RESET_PNL`) + the pre- and post-restart in-memory snapshot lines from the log |
| **M-C1 live-seed cancellation with a NON-ZERO carryover** | the boot log line showing `net − Σ + Σ` and the `_today_release_used_pnl_rows` call; the `fm_ledger` carryover rows |
| Same-day **kill survival across a restart** | `kill_switch_state` rows + the boot's `clear_stale_state` decision + the KS3 load log |
| **The first live HARD_KILL** (M-C8's real test; the drill was mock-broker) | the `flatten dispatched to worker thread` line vs `UNEXITED`/`CRASHED`/`SHUTDOWN WITH FLATTEN STILL RUNNING`; **verify broker truth** (the 8-step runbook), the `trades` EXITING rows, timestamps |

### C3 — anything genuinely novel (a new rejection status, an unhandled error, a first-time code path):
**Capture it, do not diagnose it on the spot.** `grep -iE 'ERROR|CRITICAL|Traceback|unhandled' logs/*.log |
grep 2026-07-20` → save the output + fingerprint the DB. Diagnosis is a considered-window job (§D).

---

## D. If something is wrong — the triage order (decided now, not under pressure)

**D1 — order of operations for a bad Monday:**
1. **CAPTURE STATE FIRST** — fingerprint the 3 artifacts, save the relevant logs, record DB row counts +
   `cron_heartbeat` for the day, note the service state. (State is perishable; a restart or cleanup destroys
   it.)
2. **DIAGNOSE** — read-only, against the captured copy, not the live system.
3. **REPORT to Rama** — findings + captured evidence.
4. **Only if Rama directs it, FIX** — through the careful loop, in a considered off-market window.

**D2 — NOTHING GETS FIXED ON MONDAY EVENING IN A HURRY.** State it plainly: the system being down for a day
costs a day; a rushed fix to the boot path costs a day **and** the trust in the boot path — which is exactly
what S4 did. A boot-path change is never made tired, after a long live day, without review.

**D3 — the ONE condition that IS urgent:** **forward-shadow contamination** (§B1 — more than one new date).
Preserve the JSONL immediately and report; it is the only append-only immutable artifact, and a backfill
`--date` write cannot be undone. Everything else can wait for a considered window.

---
*Read-only checklist. It verifies and reports; it does not fix. Baseline: 19-Jul, live DB `a7a1de53…`.*
