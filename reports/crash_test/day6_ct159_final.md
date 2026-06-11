# CT159 Gold Standard — Day 6 Final Report
**Date:** 2026-06-11  
**Mode:** Paper  
**Verdict:** PASS ✓

---

## CT159 Timeline Execution

| Time (IST) | Event | Result |
|------------|-------|--------|
| 07:00 | System startup (TEMP configs active) | PASS — COLD startup, all modules init |
| 09:15 | Market open, signal processing begins | PASS |
| ~09:30 | Telegram block via iptables | PASS — alerts logged to failed_alerts.log, .flag files created |
| ~09:40 | Signal flood (10 signals, 500ms) | PASS — all 200 OK, no queue overflow |
| ~09:55 | SIGKILL (kill -9) | PASS — process killed cleanly |
| ~09:57 | WARM restart via systemctl | PASS — WARM scenario, fund_manager rehydrated (25 rows, 0 anomalies) |
| ~09:58 | Post-restart signal injection (5 signals) | PASS — all PROCESSED |
| ~10:01 | Telegram unblock | PASS — iptables rule removed cleanly |
| 10:08 | Mid-day invariant/validator checks | PASS — state machine CLEAN, exactly-once CLEAN, G PASS |
| 14:45:00 | EOD pre-alert | **PASS — fired at exactly 14:45:00.635 for 18 positions** |
| 15:15:01 | Force close (circuit_breaker) | **PASS — 7 entries cancelled, SOFT_KILL activated** |
| 15:17:00 | EOD squareoff Pass 1 | PASS — 28 SL/TGT orders cancelled |
| 15:17:04 | EOD squareoff Pass 2 | **PASS — 14/14 LIMIT exits placed** |
| 15:19:08 | EOD squareoff complete | **PASS — 14/14 filled in grace window, 0 MARKET promoted, 0 failed** |
| 15:20:29 | Daily PnL reset | PASS |

---

## CT075 Retest — EOD Pre-Alert
**Result: PASS**  
- Fired: `2026-06-11T14:45:00.635+05:30` — exactly at 14:45
- Message: `eod_pre_alert: sent for 18 open position(s)`

## CT089/CT140 Retest — EOD Squareoff
**Result: PASS**  
- Pass 1: 28 orders cancelled  
- Pass 2: 14/14 positions exited via LIMIT_THEN_MARKET protocol  
- MARKET promotions needed: 0  
- Duration: 128.56s  
- 3 CNC/DELIVERY positions correctly skipped (overnight hold by design)

---

## Final Assertions

| Assertion | Result | Notes |
|-----------|--------|-------|
| State machine validator | **CLEAN** | 4691 records checked, 0 violations |
| Exactly-once — signal_intake | CLEAN | |
| Exactly-once — capital_reserve | CLEAN | |
| Exactly-once — capital_commit | CLEAN | |
| Exactly-once — capital_release | CLEAN | |
| Exactly-once — order_placement | 14 violations | FALSE POSITIVE: EOD SL replacement (CANCELLED→EOD PENDING) |
| Exactly-once — eod_squareoff | CLEAN | |
| Exactly-once — telegram_alerts | CLEAN | |
| Invariant G (orphan resources) | **PASS** | 2 cleanup rounds (SIGKILL + normal trading) |
| Invariant A (capital snapshot) | FAIL | Known: capital_snapshot unused; system uses fm_ledger |
| Invariant F (audit trail) | FAIL | Expected: FAILED trades have no COMMIT |
| Forensic reconstructor | **PASS** | 218 trades, 0 DB/log discrepancies |

---

## Day 6 Activity Stats

| Metric | Value |
|--------|-------|
| Signals processed | 4,691 |
| Trades created | 218 |
| Trades closed | 52 |
| OPEN (CNC overnight) | 3 |
| Daily PnL (paper) | ₹-4,960.75 |

---

## Retests Completed Day 6

| CT | Title | Result |
|----|-------|--------|
| CT032 | Signal while position open | PASS |
| CT083 | Max open positions | PASS |
| CT084 | Max daily trades | PASS |
| CT085 | Max consecutive losses | PASS_WITH_RISK |
| CT075 | EOD pre-alert | PASS |
| CT089 | EOD squareoff | PASS |
| CT140 | EOD stale order cleanup | PASS |

---

## Config Reverts

All TEMP configs reverted after CT159 completion:

| Parameter | TEMP Value | Production Value |
|-----------|-----------|-----------------|
| `capital.daily_loss_limit` | 9,999,999.0 | **10,000.0** |
| `risk.max_daily_trades` | 9,999 | **20** |
| `risk.max_consecutive_losses` | 9,999 | **4** |
| `risk.daily_loss_limit_pct` | 1.0 | **0.05** |
| `scoring_weights.min_pass_score` | 30 | **60** |

---

## CT159 Zero-Tolerance Assertions

| Assertion | Pass/Fail |
|-----------|-----------|
| No duplicate trades for same signal | PASS |
| No OPEN positions after EOD squareoff (intraday) | PASS |
| Kill switch inactive at EOD (auto_resume) | PASS |
| WARM recovery after SIGKILL — no data loss | PASS |
| Telegram failure → .flag file created | PASS |
| Capital drift detected and logged | PASS (natural ₹102.25 drift) |
| Signal flood (10/500ms) — no crash | PASS |
| Malformed signal → 400 reject | PASS |
| Force close at 15:15 → entries cancelled | PASS |

---

## Final Verdict

**CT159: PASS**  
**System authorized for paper trading (full production schedule)**

Crash test phase complete. 6 days, 159+ scenarios, system hardened.
