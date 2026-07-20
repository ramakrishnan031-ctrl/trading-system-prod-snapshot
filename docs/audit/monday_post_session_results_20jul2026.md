# MONDAY 20-Jul-2026 — POST-SESSION VERIFICATION RESULTS

**Run:** 20-Jul-2026 18:39–19:0x IST, read-only (`mode=ro` throughout), against
`docs/audit/MONDAY_POST_SESSION_CHECKLIST.md`.
**Verdict: ✅ CLEAN WITH NOTES.** No BAD item. **No STOP condition** — the forward shadow is
uncontaminated and recorded real `sim_R`. Nothing was fixed; nothing was changed.

> **One NEW LIVE finding**, outside the checklist's scope: preflight `webhook_responsive` fires a
> **false CRITICAL every trading day** on a healthy webhook — the *third* member of the S4
> `/health` 401-misread family, and the first one to be live rather than latent. See §NEW-1.
>
> **Three of the checklist's own expectations were wrong** (A3, A5, A7). All three were
> expectation-shaped, not query-shaped — the same root as the two already found (`is_active`,
> `eod_self_exit`). The system was right each time. See §CHECKLIST-DEFECTS.

---

## STEP 0 — forensic capture (18:39:47 +0530, host `trading-system`)

| artifact | sha256 (head) | size | mtime |
|---|---|---|---|
| `data_store/trading_system.db` | `19935aab0b198291` | 96,030,720 | 20-Jul 18:15:44 |
| `data_store/v3/forward_shadow_fs-v1.jsonl` | `76b647311f268c83` | 5,122,938 | 20-Jul 18:15:44 |
| `data_store/analytics.db` | `e849802b43cc6542` | 28,360,704 | 20-Jul 18:15:44 |

`max(cron_heartbeat.id)` = **2532**. All three differ from the 19-Jul baseline — **expected**: the
system traded, and the 18:15 recorder appended. Per the standing rule, every check below is
row-level, not hash-equality.

**Git parity:** PC `HEAD` == `origin/main` == VM bare == **`056963e3470b6de8363396924f432340aec8ea74`**.
Independently corroborated: every forward-shadow record written today stamps
`git_commit: 056963e3470b` — so the *running* tree is that commit, not merely the *pushed* one.
(The instruction sheet's expected `51e6759` does not exist here; `056963e` is the checklist-A1
correction commit and is the correct head.)

---

## A. THE SESSION

### A1 — DID IT BOOT, AND DID IT STAY UP? → ✅ GOOD

```
ActiveState=inactive  SubState=dead  Result=success  NRestarts=0
ExecMainStartTimestamp = Mon 2026-07-20 08:15:19 IST
ExecMainExitTimestamp  = Mon 2026-07-20 16:00:04 IST   ExecMainStatus=0
```
The exit is the **designed `eod_self_exit`**, confirmed by the log line at `16:00:00.002`:
*"past 16:00 IST and flat (0 active positions) — clean shutdown for the day; auto-restarts tomorrow
after the morning token refresh."* The same line exists for 10/13/14/15-Jul — a standing daily
pattern, not a Monday event. Boot 08:15:19 → exit 16:00:04 = **7h44m uptime across the whole
session**. This is the GOOD branch of the corrected A1, not the S4 signature (which is a ~09:00
exit with 0 trades + a LIVENESS DOWN alert; we have a 16:00 exit after 4 trades and no alert).

**Liveness probe — measured, not assumed.** `logs/cron-liveness.log` exists, **0 bytes**, created
09:00 → the probe ran and emitted nothing (silent = healthy). **No `LIVENESS … is DOWN` sentinel
exists at all.** That the 5-minute slot actually fired all day is proven by its schedule-twin:
`capture_metrics_baseline` shares the identical `*/5 9-15 * * 1-5` crontab expression and logged
**84 contiguous SUCCESS heartbeats, 09:00:02 → 15:55:01, with no gap**.

### A2 — DID IT TRADE, AND IS THE VOLUME SANE? → ✅ GOOD

Row growth vs the 19-Jul baseline: `signals` **+3,076** · `trades` **+8** · `orders` **+12** ·
`fm_ledger` **+29**.

| status | n |
|---|---|
| CLOSED | 3 |
| CLOSED_MANUAL | 1 |
| REJECTED | 4 |

**4 entered trades**, 4 rejected. `screener_results` scored = **1,028**. Entries by hour:
**hour 10 → 7 rows, hour 13 → 1 row (87.5% in hour 10)**.

Against the checklist band: `d_trades` 8 (BAD is 0, or ≳25) → inside. `scored` 1,028 (BAD is ≪500
or ≫3,000) → inside, below the ~1,450 central estimate but comfortably in band. Hour-10
concentration 87.5% matches the stated ~87% pattern exactly.

**Cross-check against the independently-supplied numbers holds exactly:** 4 entries — BEPL, PNB,
KROSS, HUHTAMAKI, all MIS, all entered in hour 10 — and 4 rejections. No disagreement.

### A3 — CAPITAL INTEGRITY → ✅ GOOD *(the checklist's query was defective; see §CHECKLIST-DEFECTS-1)*

As written, A3 reports `ledger_pnl` **+1.32** vs `trades_net` **−18.29** — an apparent break. It is
not one. The checklist's query sums `pnl_delta` over **all** rows *including the `RESET_PNL` row
itself*, which is the bookkeeping reversal of the very quantity being summed.

Row-level truth (`fm_ledger`, 20-Jul, the 5 rows with non-zero `pnl_delta`):

| ledger_id | entry_type | pnl_delta | costs | time |
|---|---|---|---|---|
| 9202 | RELEASE_USED | −4.74 | 0.46 | 10:10:01 |
| 9207 | RELEASE_USED | −3.49 | 0.40 | 10:18:26 |
| 9208 | RELEASE_USED | −4.02 | 0.46 | 11:55:04 |
| 9211 | RELEASE_USED | −6.04 | 0.00 | 13:52:21 |
| 9212 | **RESET_PNL** | **+19.61** | 0.00 | 15:17:05 |

**The invariant, computed correctly, holds to the paisa:**

```
Σ pnl_delta (trading rows, excl. RESET_PNL) = −18.29
Σ trades.net_pnl (CLOSED + CLOSED_MANUAL)   = −18.29    ✅ EXACT
```

And per trade, `fm_ledger.pnl_delta` equals `trades.net_pnl` **row for row**:

| symbol | gross_pnl | charges | net_pnl | ledger pnl_delta | ledger costs |
|---|---|---|---|---|---|
| KROSS | −4.28 | 0.46 | **−4.74** | **−4.74** | 0.46 |
| BEPL | −3.09 | 0.40 | **−3.49** | **−3.49** | 0.40 |
| PNB | −3.56 | 0.46 | **−4.02** | **−4.02** | 0.46 |
| HUHTAMAKI | −6.04 | 0.00 | **−6.04** | **−6.04** | 0.00 |

(HUHTAMAKI's `charges = 0.00` is the documented RMS/external-close behaviour — costs are passed as
0.0 on an RMS close. It was closed externally at 13:52:21, caught by the reconciler.)

**`RESET_PNL` is the expected Option-B signature, demonstrated numerically:**
```
RESET_PNL.pnl_delta          = +19.61
−(Σ trading pnl_delta − Σ costs) = −(−18.29 − 1.32) = +19.61   ✅ EXACT
```
E4/W10 has **not** shipped, so the gross-based reset is correct-as-designed, **not a defect** — the
checklist says so explicitly. The observable consequence: the day's P&L counter resets to **+1.32**
(= +Σcosts) rather than 0.00, because the reset subtracts costs a second time. This residue is
**date-scoped and cannot leak** — the daily-loss reader filters `WHERE date = <today>`, so 21-Jul
never sees these rows; and it lands at 15:17, after the 15:15 SOFT_KILL has already blocked new
entries. Under E4/W10 the reset would be `−Σpnl_delta` = **+18.29** and the counter would land on
0.00. **Today's E4/W10 impact: nil on outcomes; it changes one bookkeeping row by Rs 1.32.**

No capital-invariant error and no spurious `hard_kill` in the logs.

### A4 — THE DAILY-LOSS CONTROL → ✅ GOOD · **worst intraday cumulative = −Rs 18.29**

Threshold ≈ **−Rs 296** (3% × Rs 9,875.60 actual capital). **−18.29 is 6.18% of the limit.** No
DAILY-loss kill row (`COUNT = 0`). Prior record worst-ever was −56.47, so today did not approach it.

**Reconciling the two P&L figures — they do not disagree, they are different quantities:**
```
Σ trades.gross_pnl = −16.97   ← equals the broker account delta (9,875.60 → 9,858.63)
Σ trades.charges   = + 1.32
Σ trades.net_pnl   = −18.29   ← what the system's daily-loss control reads
```
The externally-supplied "~ −Rs 17" is the **gross**/broker figure and matches Σgross **exactly**
(−16.97). The system's −18.29 is **net of Rs 1.32 costs**. Both are right. *(Capital vocabulary:
all figures here are P&L against **actual capital** Rs 9,875.60; no leverage is involved — the
system sizes unlevered.)*

### A5 — KILL SWITCH & SQUAREOFF → ✅ GOOD *(checklist expectation wrong; see §CHECKLIST-DEFECTS-2)*

```
state = SOFT_KILL | reason = circuit_breaker_force_close_15:15
triggered_at = 2026-07-20T15:15:00.755414+05:30 | triggered_by = order_monitor
```
The checklist's GOOD clause expects `state = INACTIVE` at close. **It is SOFT_KILL — and that is
correct.** The 15:15 circuit-breaker force-close is the designed end-of-day cutoff; the state
persists overnight and **auto-clears at tomorrow's 08:15 boot** because `triggered_at`'s date
(20-Jul) differs from the boot date. That is the documented prior-day auto-clear rule; **no
`deploy/resume.sh` is needed.** A matching sentinel was emitted at 15:15:00.

**Every Monday trade is terminal** — the non-terminal query returned **0 rows**. Nothing
`OPEN`/`PARTIAL`/`EXITING` survived past squareoff (15:17:03, book flat).

### A6 — CRON COMPLETION → ✅ GOOD

All expected jobs present and **SUCCESS**:

| job | time | note |
|---|---|---|
| `auto_refresh_token` | 08:15:02 | the app started |
| `monitoring_canary` | 08:20:06 | `[func=OK]` |
| `cron_officer_briefing` | 09:20:02 | |
| `fetch_daily_candles` | 15:40:07 | |
| `reconcile_positions` | 15:45:02 | |
| `reconstruct_excursions` | 15:50:01 | `examined=4 written=4` |
| **`eod_cleanup`** | **15:50:01** | **SUCCESS, silent → 0 rows deleted** ✅ |
| `eod_verify` / `eod_broker_reconcile` | 15:55:02 / 15:58:03 | |
| `wal_checkpoint` | 16:00:01 | |
| `daily_report` / `trade_journal` | 16:05:05 / 16:10:01 | |
| `strategy_registry_officer` | 16:22:01 | |
| `check_cron_drift` | 18:00:01 | |
| **`forward_shadow_record`** | **18:15:44** | **`date=2026-07-20 wrote=1028 sim=962`** ⭐ |

`backup_retention` FAILED — the **known, benign safety-abort**, flagged by Control Tower at 17:05
as expected, not a Monday regression. `system_manager_eod` (18:45) and `cron_officer_eod` (18:50)
had not yet run at capture time (18:39) — pending, not missing.

> ⭐ **The 18:15 recorder fires normally on a self-exit day — now confirmed, not assumed.** This was
> the open risk: the recorder is a cron, independent of the service, and the service had been down
> since 16:00:04. It ran at 18:15:44 and wrote 1,028 records. Answered by observation.

### A7 — THE 403 POPULATION → ✅ GOOD *(checklist expectation incomplete; see §CHECKLIST-DEFECTS-3)*

| hour | code | n |
|---|---|---|
| 09 | 403 | 179 |
| 10–14 | 200 | 387 / 457 / 473 / 560 / 695 (**2,572 total**) |
| 15 | 403 | 373 |

Boundaries: 200s run **10:00:19 → 14:59:13**; 403s run **09:16:19 → 15:29:16**, with the hour-15
403s beginning at **15:00:00**.

The checklist's BAD clause is *"403s after 10:00 → the entry gate stuck closed"*. There are 373 such
403s — but the gate was demonstrably **not** stuck: 2,572 POSTs returned 200 across the five-hour
window and 4 trades were entered. The post-15:00 403s are the **closing** half of the entry window,
which the checklist modelled only on its opening half. Confirmed in config
(`config/system_config.yaml`): `entry_start: "10:00"`, and comment T5/29-Jun records
*"aligned entry_end 15:15→15:00 to match the per-strategy entry_end_time=15:00 … makes 15:00 the
global backstop"*. **Observed window [10:00, 15:00) matches the configured window exactly.**

---

## B. ⭐ THE FORWARD SHADOW — the check that mattered most → ✅ GOOD ON ALL FOUR

### B1 — exactly ONE new date → ✅ **NO CONTAMINATION**
```
dates: ['2026-07-13', '2026-07-14', '2026-07-15', '2026-07-16', '2026-07-20']
total_lines: 8855
```
Exactly one new date, **`2026-07-20`**. Line count **7,827 + 1,028 = 8,855** — exact, no
over-append. Per-date: 07-13 → 3,467 · 07-14 → 1,644 · 07-15 → 2,535 · 07-16 → 181 · **07-20 →
1,028**. **The one urgent STOP condition of the day did not occur.**

### B2 — `sim_R` REAL or NULL? → ✅ **REAL**
```
2026-07-20: 1028 records | sim_R NULL=66 (6.4%) | ms4_stats_ok=962/1028
```
**Null fraction 6.4%** — the recorder simulated **93.6%** of records. Cross-checked against the
recorder's own heartbeat: `wrote=1028 sim=962` — **M=962 ≈ wrote=1028, not M=0**. The token was
present (refreshed cleanly 08:15:02) and the candle-fetch path ran. **The silent-degradation mode
did not occur.** `wrote=1028` also equals `screener_results` scored=1,028 exactly.

### B3 — records carry what they should → ✅ GOOD
`missing: []` — every required field present. Sample record: `old_score=0`, `old_band='0-34'`,
`ms4_score=45`, `ms4_band='45-49'`, `decision='REJECTED_CIRCUIT_PROXIMITY'`,
`reject_reason='circuit_proximity'`, `sim_R=-1.0`, `realized_pnl=None` (correct — a rejected signal
has no realized P&L), provenance stamped `git_commit='056963e3470b'`,
`scoring_weights_sha='6a0bd4817243'`.

### B4 — running out-of-sample total → **3 days → 4 days** ✅
Genuine OOS window is now **07-14, 07-15, 07-16, 07-20 = 4 days** (07-13 remains the basis day, not
OOS). As predicted. Day 4 is still deep in the underpowered zone for **D4** (~17–34 trading days
needed); **D3** is roughly powered at 3 and day 4 only firms it.

### B5 — **D3 was NOT re-run.** Per the checklist: one extra OOS day does not move a verdict.

---

## C. WHAT MONDAY PROVES → ✅ GOOD

### C1 — rehydrate was the predicted no-op, confirmed rather than assumed
```
fund_manager.rehydrate_complete  anomaly_count=0  daily_pnl=0.0
                                 replayed_pnl_rows=0  replayed_trades=0  total=9875.6
order_monitor rehydrated 0 orders from state_store
order_placer.rehydrate_fill_map  rehydrated=0
```
Phase 1 replayed **0** trades, Phase 2 carried **0** P&L rows, M-C1 live-seed carryover **0** (the
subtraction a no-op), seed landed on **9,875.60**. Monday proves the boot survives the restore path
on a flat book — **nothing more**. C2's rare-event paths (mid-day restart with live state, first
live HARD_KILL) remain unproven in production, as expected.

### C3 — nothing genuinely novel in the error stream beyond §NEW-1 below.

---

## NEW-1 ⚠️ **LIVE FINDING — preflight `webhook_responsive`: a false CRITICAL every trading day**

**Not a checklist item. Found by reading the day's `critical_alert_*` sentinels, which A1's grep
(scoped to `*liveness*`) would not have surfaced.**

At **09:19:46** the system emitted a **CRITICAL_FAILURE** sentinel + Telegram alert:
```
Pre-flight — Phase C — 20-Jul-2026 [CRITICAL_FAILURE]   Readiness: 33%
CRITICAL FAILURES (1)
  - Signals/webhook_responsive: webhook /health HTTP 401
```

**The webhook was fine.** Same day: 2,572 successful 200 POSTs, 1,028 signals scored, 4 trades
entered. This is a **false CRITICAL on a healthy system.**

**Root cause — the identical misread that caused S4, in a third location:**

`scripts/preflight/checks/signals.py:26-33` calls `http://127.0.0.1:5000/health` — the **exact same
endpoint** as the S4 fix — and accepts **only** `status == 200`:
```python
status, body = engine._http_get_json(WEBHOOK_HEALTH_URL)
if status == 200:  return self._passed(...)
if status == 0:    return self._failed("webhook unreachable — ...")
return self._failed(f"webhook /health HTTP {status}")     # <-- 401 lands here
```
AB-910 §1.7 (commit `84cee3e`, 17-Jul) put `/health` behind the webhook secret. `utils/startup_checks.py:807-808`
was updated to tolerate 401. **This preflight check was not**, and it calls `/health`
unauthenticated.

**Proven historically from `logs/preflight.log`** — the check's verdict, every trading day:
```
30-Jun … 16-Jul   ✅ PASS   "webhook /health 200 (signals can arrive)"   (13 consecutive days)
17-Jul            🔴 FAIL   "webhook unreachable — Connection refused"   ← S4: the app was dead
20-Jul            🔴 FAIL   "webhook /health HTTP 401"                   ← NEW failure mode
```
The 200→401 transition is exactly the AB-910 change; today is simply the **first trading day since**
it landed, so this is its first observation. It will now recur **every trading day**.

**Severity: LIVE, but not dangerous.** Preflight is a cron; its CRITICAL does **not** gate the boot
or block trading (the system traded normally today). The harm is **alert fatigue and a corrupted
oracle** — a permanently-red Phase C at 33% readiness is one that nobody can read a real webhook
outage out of. Per the LIVE-vs-LATENT rule this is reportable-now; per D2 it is **not** fixed
tonight.

**Captured, not fixed.** Design in `docs/audit/boot_path_pair_design_20jul2026.md` §B2′.

---

## NEW-2 (recorded, historical — NOT today) — `eod_cleanup` FK failure on 15-Jul, silently

`logs/cron-eod-cleanup.log` contains:
```
eod_cleanup.unexpected_error: FOREIGN KEY constraint failed
  File "scripts/eod_cleanup.py", line 201, in _cleanup_old_fingerprints
    cur.execute(f"DELETE FROM signals WHERE {where}", (cutoff,))
sqlite3.IntegrityError: FOREIGN KEY constraint failed
```
**This is NOT from today.** The log's **mtime is 15-Jul 15:50** and today's run did not append to it
(the job only writes on error). Today `eod_cleanup` succeeded silently at 15:50:01. Heartbeat
history: 15-Jul `SUCCESS` (00:20:45, a separate invocation) · 16-Jul `SUCCESS` · 17-Jul `SUCCESS` ·
19-Jul `SKIPPED` (weekend) · **20-Jul `SUCCESS`**.

Two things worth keeping:
1. On 15-Jul at 15:50 the job **crashed and wrote no heartbeat at all** — so it appears as *MISSED*,
   never as *FAILED*. A failure that leaves no failure record is the same shape as S4 and as the
   forward-shadow null mode.
2. The signal-fingerprint prune **can** violate a foreign key against a child table. That is direct
   evidence for **decision #09 (prune-retention)**, which is on the decidable-now list.

Recorded only. It has not recurred in 3 subsequent runs.

---

## CHECKLIST-DEFECTS — three more expectation-shaped errors (all mine to note, none the system's)

The checklist's SQL was schema-validated on 20-Jul; its **prose expectations** were not. Three more
failed today, and all three failed the same way as `is_active` and `eod_self_exit` — *written
against a mental model of the system rather than against the system.*

**1. A3's `expected_reset` query is vacuous — it cannot fail.**
```sql
ROUND(-(SUM(pnl_delta)-SUM(costs)),2) AS expected_reset   -- over ALL rows for the date
```
The sum includes the `RESET_PNL` row whose `pnl_delta` *is* the reversal of that sum, so the
expression collapses to `0.0`; it is then compared against `RESET_PNL.amount`, which is `0.0` because
a P&L reset moves no capital. **`0.0 == 0.0` for structural reasons — a green check that could not
have been red.** The real signature lives in `RESET_PNL.pnl_delta` (+19.61), not `amount`. And the
companion invariant `ledger_pnl == trades_net` can *never* hold on a day with trades, because it
sums the reversal row into the day's P&L. Correct forms are given in §A3.

**2. A5 expects `state = INACTIVE` at close.** The designed 15:15 circuit-breaker leaves
**SOFT_KILL** set overnight; it auto-clears at the next boot. As written, every normal trading day
reads BAD.

**3. A7 expects 403s only before 10:00.** The entry window is `[10:00, 15:00)` — the *closing* gate
produces 373 legitimate 403s daily. As written, every normal trading day reads BAD.

**The pattern, stated once:** validating queries validates queries. **Three of these five defects
were in prose, and prose is the part that was never validated.** A checklist item is only as good
as the last time its *expectation* was checked against a running system — and expectations, unlike
column names, fail silently in the direction of crying wolf.

---

## VERDICT

| item | verdict |
|---|---|
| A1 boot / uptime / liveness | ✅ GOOD |
| A2 traded, volume sane | ✅ GOOD |
| A3 capital integrity | ✅ GOOD (checklist query defective) |
| A4 daily-loss control | ✅ GOOD — **worst intraday −Rs 18.29 (6.18% of the −296 limit)** |
| A5 kill switch / squareoff | ✅ GOOD (checklist expectation wrong) |
| A6 cron completion | ✅ GOOD |
| A7 403 population | ✅ GOOD (checklist expectation incomplete) |
| B1 one new date | ✅ **GOOD — no contamination** |
| B2 `sim_R` real | ✅ **GOOD — 6.4% null, `sim=962/1028`** |
| B3 record fields | ✅ GOOD |
| B4 OOS total | **3 → 4 days** |
| C1 rehydrate no-op | ✅ GOOD |
| NEW-1 preflight 401 | ⚠️ **LIVE — false CRITICAL daily** (captured, not fixed) |
| NEW-2 eod_cleanup FK | recorded — historical (15-Jul), not today |

**Overall: CLEAN WITH NOTES.** The S4 class did not recur; the liveness probe and the boot fix are
both now proven across a full production trading day; the out-of-sample artifact grew by exactly one
uncontaminated day with real simulated outcomes. §B of the evening's instruction is therefore
unblocked.

*Read-only throughout. `scripts/forward_shadow_record.py` was never invoked; no `scripts/*.py --db`
was used; no code, config, schema or state was modified.*
