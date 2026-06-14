# Crash Test Day 5 — Handoff Document
Date: 2026-06-10 | Updated: 11:55 IST | Model: Opus 4.6

## COMPLETED TODAY (Day 5)

| CT | Title | Result | Notes |
|---|---|---|---|
| CT136 | Signal Flood + Network Drop | PASS | Earlier session |
| CT137 | Kill Switch + Active Positions | PASS | Earlier session |
| CT138 | SIGKILL + Restart + Immediate Flood | PASS | Earlier session |
| CT139 | Invariant Violation + Open Positions | PASS | HARD_KILL fired, 5 trades preserved, --resume required |
| CT142 | DB Busy + Signal Burst | PASS | 20s lock, 10 signals queued and processed, no deadlock |
| CT144 | Maximum Dirty State SIGKILL | PASS_WITH_RISK | No capital_snapshot in paper; daily limit prevented dirty state |
| CT145 | Rapid Crash Cycle 3x | PASS | 2 SIGKILL cycles, no corruption, 0 duplicate signals |
| CT146 | Kill Switch Marathon | PASS | SOFT→SOFT→HARD→resume, no accumulated damage |
| CT154 | AGY Crash → Trading Unaffected | PASS | BindsTo is one-directional |

## IN PROGRESS

| CT | Title | Status | Notes |
|---|---|---|---|
| CT143 | Full Day Simulation | 12 OPEN trades, 55 total, signals injected at 11:00/11:30/12:00 | Awaiting 13:00 injection, 14:45 pre-alert, 15:17 squareoff |
| CT140 | EOD Squareoff Failure | Script deployed, awaiting 15:14 IST | Paper adapter bypasses REST — block is no-op, still validates EOD flow |

## DEFERRED

| CT | Title | Reason |
|---|---|---|
| CT141 | Network Drop + SL Trail | Paper mode — no real WebSocket, SmartTgt inoperative |
| CT151-153 | AGY Governance | Require AGY/Gemini CLI invocation |
| CT159 | Gold Standard Certification | Requires fresh trading day (recommend Jun 11) |

## FIX APPLIED
- state_machine_validator.py: Added REJECTED_STRATEGY_POSITION_LIMIT and REJECTED_SIZING_POSITION_VALUE_CAP to known statuses

## SYSTEM STATE (11:55 IST)
- Health: ok, kill switch INACTIVE
- Open trades: 12 (SILVERAG, SHANTIGOLD, RIIL, BHAGCHEM, MUNJALAU, GICHSGFIN, CLEANMAX, AFCONS, CARYSIL, HERANBA, INTENTECH, CCL)
- PENDING trades: 4
- Today's trades: 26
- DB integrity: ok

## TEMP CONFIGS (MUST REVERT AFTER 15:17)
- VM system_config.yaml: max_daily_trades=500 (→20), max_consecutive_losses=100 (→4)
- VM scoring_weights.yaml: min_pass_score=30 (→60)
- Local system_config.yaml: max_daily_trades=500 (→20)

## TIMELINE — REMAINING
1. ~13:00 IST: Inject 2 afternoon signals (ct143_afternoon_inject.py)
2. 14:30 IST: Verify 3+ OPEN for CT140
3. 14:45 IST: Watch for EOD pre-alert in logs
4. 15:14 IST: Run CT140 (block REST 120s — note: paper mode limitation)
5. 15:17 IST: Watch EOD squareoff of 12+ positions
6. After 15:17: Revert all TEMP configs
7. After 16:30: Run CT156-CT158 forensics (ct156_158_forensics.sh)
8. After 17:00: Run ct143_verify.py, generate master report, commit artifacts, store mempalace

## SCRIPTS DEPLOYED ON VM
- tests/crash_test/ct140_eod_failure.py
- tests/crash_test/ct143_afternoon_inject.py
- tests/crash_test/ct143_verify.py
- tests/crash_test/ct156_158_forensics.sh
