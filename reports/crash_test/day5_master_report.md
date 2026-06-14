# Crash Test Day 5 — Master Report
**Date:** 2026-06-10
**System:** Paper Trading on Oracle Cloud VM (161.118.188.171)
**Model:** Opus 4.6

## Executive Summary

Day 5 focused on multi-failure combinations, full-day simulation, crash stress tests, AGY governance, and forensics. The system processed 2,400+ signals and managed 35+ trades under extreme chaos conditions.

## Test Results

### Multi-Failure Combinations (CT136-CT142)

| CT | Title | Result | Details |
|---|---|---|---|
| CT136 | Signal Flood + Network Drop | PASS | Earlier session |
| CT137 | Kill Switch + Active Positions | PASS | Earlier session |
| CT138 | SIGKILL + Restart + Immediate Flood | PASS | Earlier session |
| CT139 | Invariant Violation + Open Positions | PASS | fm_ledger corruption → CapitalInvariantViolation → HARD_KILL → 5 trades preserved, --resume required |
| CT140 | EOD Squareoff Failure | *PENDING* | Awaiting 15:14 IST execution |
| CT141 | Network Drop + SL Trail | DEFERRED | Paper mode — no real WebSocket, SmartTgt inoperative |
| CT142 | DB Busy + Signal Burst | PASS | 20s write lock, 10 signals queued, 0 deadlocks |

### Full Day Simulation (CT143)

| CT | Title | Result | Details |
|---|---|---|---|
| CT143 | Complete Trading Day | *PENDING* | 20 OPEN trades, 35+ total, awaiting EOD verification |

### Crash Stress Tests (CT144-CT146)

| CT | Title | Result | Details |
|---|---|---|---|
| CT144 | Maximum Dirty State SIGKILL | PASS_WITH_RISK | Paper mode lacks capital_snapshot; daily limit prevented maximum dirty state |
| CT145 | Rapid Crash Cycle 3x | PASS | 2 SIGKILL cycles, 0 corruption, 0 duplicate signals |
| CT146 | Kill Switch Marathon | PASS | SOFT→SOFT→HARD→resume, no accumulated damage |

### AGY Governance (CT151-CT154)

| CT | Title | Result | Details |
|---|---|---|---|
| CT151-CT153 | AGY Policy/Override/Log | DEFERRED | Require AGY/Gemini CLI invocation |
| CT154 | AGY Crash → Trading Unaffected | PASS | trading-watchman BindsTo is one-directional |

### Forensics (CT156-CT158)

| CT | Title | Result | Details |
|---|---|---|---|
| CT156 | Forensic Reconstruction | *PENDING* | Run after 16:30 IST |
| CT157 | State Machine + Exactly-Once | *PENDING* | Run after 16:30 IST |
| CT158 | Invariant Check | *PENDING* | Run after 16:30 IST |

### Gold Standard Certification (CT159)

| CT | Title | Result | Details |
|---|---|---|---|
| CT159 | Gold Standard | DEFERRED | Requires fresh trading day (recommend Jun 11) |

## Validator Fixes Applied

State machine validator updated with 5 new legitimate rejection statuses:
1. `REJECTED_STRATEGY_POSITION_LIMIT` — strategy position limit reached
2. `REJECTED_SIZING_POSITION_VALUE_CAP` — position value cap exceeded
3. `REJECTED_SIGNAL_AGE` — signal too old (expiry_sec)
4. `REJECTED_RESERVE_FAILED` — capital reservation failed
5. `REJECTED_CONSECUTIVE_LOSSES` — consecutive loss circuit breaker

## System Statistics

| Metric | Value |
|---|---|
| Total signals today | 2,400+ |
| Total trades today | 35+ |
| Open positions (pre-EOD) | 20 |
| Realized PnL | -Rs 3,607 |
| System restarts | Multiple (crash tests) |
| Kill switch activations | Multiple (CT139, CT146) |
| Data corruption events | 0 (engineered corruption for CT139 was reverted) |

## TEMP Config Changes (Must Revert)

| Config | Test Value | Production Value | Location |
|---|---|---|---|
| max_daily_trades | 500 | 20 | VM + Local system_config.yaml |
| max_consecutive_losses | 100 | 4 | VM system_config.yaml |
| min_pass_score | 30 | 60 | VM scoring_weights.yaml |

## Known Issues (Pre-existing)

Invariant checker reports 4 known issues (all pre-existing from Day 1-4 crash test chaos):
- **A:** Paper mode — no capital_snapshot table
- **B:** 8 stuck QUEUED signals from crash test interruptions
- **F:** Missing COMMIT entries from crash-induced restarts
- **G:** 2 orphan orders from Day 3 crash tests

## Conclusion

*To be completed after EOD window (15:17 IST)*
