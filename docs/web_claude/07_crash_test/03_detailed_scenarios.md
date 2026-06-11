================================================================================
TRADING SYSTEM v2 — MASTER CRASH TEST PLAN
================================================================================
Date prepared: 04-Jun-2026 (IST)
Test window: 08-Jun-2026 (Mon) to 12-Jun-2026 (Fri)
System: Trading System v2 (Equity, NSE)
Mode: PAPER (account LFL836)
VM: Oracle Cloud Ubuntu ARM
Prepared by: Web Claude (Opus 4.6)
================================================================================

NOTE TO VS CODE CLAUDE:
- This is the DETAILED REFERENCE. Read via 00_index_and_addendum.md instructions. Reference by CT### ID when catalog scenario nee
- Every scenario must have a test script + pass/fail assertion.
- All test artifacts go under: tests/crash_test/
- All test results go under: reports/crash_test/
- Store progress in mempalace after each day.
- STANDING RULES: Permanent fixes only. Paper + Live parity always.

================================================================================
PART 0 — PREREQUISITES (Sunday Jun 7 or Monday Jun 8 morning before 09:15)
================================================================================
Model: Opus 4.6 (architecture-level setup)

0.1  REVERT ALL 7 TEMP CONFIG VALUES TO PRODUCTION:
     | Parameter                              | TEMP → Production    |
     | capital.daily_loss_limit               | 100,000 → 10,000    |
     | risk.max_consecutive_losses            | 20 → 4              |
     | risk.daily_loss_limit_pct              | 1.00 → 0.05         |
     | order_reconciler.capital_drift_tolerance| 100,000 → 50       |
     | drift_handler.log_only_threshold_rs    | 100,000 → 250       |
     | drift_handler.soft_kill_threshold_rs   | 200,000 → 1,000     |
     | drift_handler.hard_kill_threshold_rs   | 500,000 → 2,500     |

     WHY: Kill switch scenarios must fire at realistic thresholds.
     HOW: Edit system_config.yaml, commit, deploy to VM.

0.2  VERIFY SYSTEM STATE:
     - System starts clean: `sudo systemctl start trading-system`
     - Health endpoint responds: `curl http://localhost:5000/health`
     - DB schema v24 confirmed
     - Zero open trades, zero pending orders
     - Kill switch state CLEAR
     - Capital invariant holds: available + reserved + used == total
     - Zerodha API token valid (login fresh Monday morning)
     - All 13 strategies active in config

0.3  RESOLVE THE 13 UNCERTAIN ITEMS (run on VM):
     ```bash
     python3 --version
     crontab -l
     df -h && free -h && nproc
     sudo iptables -L -n
     timedatectl status
     ls -lh ~/systems/trading-system/data_store/trading_system.db
     ls -la ~/systems/trading-system/.env
     ulimit -a
     cat /proc/meminfo | grep -E "MemTotal|SwapTotal"
     ```
     Record all outputs in: reports/crash_test/day0_vm_baseline.md

0.4  BUILD TEST INFRASTRUCTURE (VS Code Claude builds these):

     A) Signal Injector: tests/crash_test/signal_injector.py
        - Sends POST to localhost:5000/webhook/<scanner_name>
        - Supports: single, burst (N signals in T seconds), flood (queue-filling)
        - Configurable: symbol, strategy/scanner, price, trigger_time, delay
        - Payload matches EXACT Chartink format
        - Supports malformed payloads (missing fields, wrong types, extra fields)
        - Supports expired signals (trigger_time in past)
        - Logs: HTTP status, response body, latency per request
        - CLI: python signal_injector.py --mode single --symbol RELIANCE --scanner gap_fade_short
        - CLI: python signal_injector.py --mode burst --count 10 --delay-ms 200
        - CLI: python signal_injector.py --mode flood --count 300

     B) State Inspector: tests/crash_test/state_inspector.py
        - Queries DB state: open trades, pending orders, capital snapshot,
          kill switch, fm_ledger tail, signal queue depth, in_flight dict
        - Outputs structured JSON snapshot
        - Used before/after each scenario for diff comparison
        - CLI: python state_inspector.py --snapshot before_CT001
        - CLI: python state_inspector.py --diff before_CT001 after_CT001

     C) Network Controller: tests/crash_test/network_controller.py
        - Wraps iptables to block/unblock specific hosts
        - Presets: block_zerodha_rest, block_zerodha_ws, block_all, block_telegram
        - Duration-based: --block-seconds 30
        - Safety: auto-unblock after max 5 minutes (failsafe timer)
        - Requires sudo — script checks permissions
        - CLI: python network_controller.py --preset block_zerodha_rest --duration 30

     D) Scenario Runner: tests/crash_test/scenario_runner.py
        - Reads scenario YAML definitions
        - Runs: pre-check → snapshot_before → execute_steps → wait → snapshot_after → assert → log
        - Generates per-scenario result: PASS / FAIL / PARTIAL + details
        - Supports manual pause ("press Enter after observing X")
        - CLI: python scenario_runner.py --scenario CT001 --day 1

     E) Crash Test Reporter: tests/crash_test/crash_test_reporter.py
        - Aggregates all scenario results for a day
        - Generates: reports/crash_test/day{N}_results_YYYY-MM-DD.md
        - Summary table: scenario_id | name | result | notes
        - Failure details with DB diffs
        - Telegram notification with day summary

     F) Cleanup Script: tests/crash_test/cleanup.py
        - Resets system to clean state between scenarios
        - Clears: open trades, pending orders, signals (today), kill switch
        - Resets capital to starting value
        - Restarts trading-system service
        - CLI: python cleanup.py --confirm

     G) Scenario Definitions: tests/crash_test/scenarios/day{N}.yaml
        - Each scenario in YAML with: id, name, category, steps, assertions
        - Scenario runner reads these

0.5  VERIFY TEST INFRASTRUCTURE:
     - Injector: send 1 valid signal → verify ACCEPTED in DB
     - Inspector: snapshot → verify JSON output
     - Cleanup: reset → verify clean state
     - Network controller: block 5s → unblock → verify connectivity restored

0.6  BACKUP:
     - Full DB backup before Day 1: cp trading_system.db trading_system_pre_crash_test.db
     - Config backup: cp -r config/ config_pre_crash_test/

================================================================================
PART 1 — DAY 1 (Mon Jun 8): SIGNAL & WEBHOOK LAYER
================================================================================
Model for VS Code Claude: Opus 4.6 (complex harness building + first-day setup)
Test window: 09:30 IST — 15:00 IST (market hours, live Zerodha feed)
Strategy: All 13 active

OBJECTIVE: Verify every signal ingestion path — valid, invalid, edge cases,
           stress, and mid-trade interference.

--- CATEGORY 1A: HAPPY PATH SIGNALS ---

CT001 | Single Valid Signal
  Steps:
    1. Inject 1 valid signal: RELIANCE, gap_fade_short, price=current LTP
    2. Wait 30s for pipeline completion
  Assert:
    - signals table: status=TRADED or REJECTED_<reason> (not stuck)
    - If TRADED: trades table has entry, fm_ledger has RESERVE+COMMIT
    - Telegram notification sent
    - HTTP response: 200, {"accepted": 1}
  Pass: Signal processed end-to-end, no errors in log

CT002 | Multiple Valid Signals — Different Symbols
  Steps:
    1. Inject 5 signals (RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK)
       each to different scanner, 2s apart
    2. Wait 60s
  Assert:
    - All 5 in signals table
    - Each processed (not stuck IN_PROCESS)
    - Capital allocation correct (no double-reserve)
    - fm_ledger entries balanced
  Pass: All 5 processed independently, capital invariant holds

CT003 | Multiple Valid Signals — Same Symbol, Different Strategies
  Steps:
    1. Inject RELIANCE to gap_fade_short
    2. Wait 1s
    3. Inject RELIANCE to gap_fade_long
  Assert:
    - First signal: processes normally
    - Second signal: either IN_PROCESS (if first still processing)
      or processes after first completes
    - No race condition on capital or state
  Pass: Sequential processing, no corruption

--- CATEGORY 1B: REJECTION PATHS ---

CT004 | Duplicate Signal (within 5-min dedup window)
  Steps:
    1. Inject signal: RELIANCE, gap_fade_short
    2. Wait 5s
    3. Inject identical signal (same symbol, same scanner)
  Assert:
    - First: ACCEPTED
    - Second: DUPLICATE status in signals table
    - HTTP response for second: 200 but accepted=0
  Pass: Dedup works, no double processing

CT005 | Expired Signal
  Steps:
    1. Inject signal with triggered_at = now() - 700s (beyond 600s expiry)
  Assert:
    - signals table: status=EXPIRED
    - NOT queued for processing
  Pass: Expiry check works at webhook layer

CT006 | Signal Outside Market Hours
  Steps:
    1. Wait until 15:20 IST (after market close)
    2. Inject valid signal
  Assert:
    - signals table: status=OUTSIDE_HOURS
    - NOT queued
  Pass: Market hours gate works

CT007 | Invalid Symbol
  Steps:
    1. Inject signal with symbol="FAKESYMBOL123"
  Assert:
    - signals table: status=INVALID_SYMBOL
  Pass: Symbol validation works

CT008 | Invalid Scanner Name
  Steps:
    1. POST to /webhook/nonexistent_scanner
  Assert:
    - HTTP 404 or rejection
    - No signal inserted into DB
  Pass: Scanner validation works

CT009 | Signal While Kill Switch Active
  Steps:
    1. Activate SOFT_KILL manually (or via API failure simulation)
    2. Inject valid signal
  Assert:
    - Signal rejected at webhook layer OR at pipeline step 1
    - Status contains KILL_SWITCH reference
  Cleanup: Clear kill switch
  Pass: Kill switch blocks new signals

CT010 | Signal for IN_PROCESS Symbol
  Steps:
    1. Inject signal for RELIANCE (ensure it enters pipeline and stays processing)
    2. While processing: inject second signal for RELIANCE (same or different strategy)
  Assert:
    - Second signal: status=IN_PROCESS in signals table
    - First signal completes normally
    - Second signal: eventually released by sweeper (60s) or processed after first
  Pass: IN_PROCESS lock prevents concurrent processing of same symbol

--- CATEGORY 1C: MALFORMED PAYLOADS ---

CT011 | Missing Required Fields
  Steps:
    1. POST with empty body: {}
    2. POST with missing 'stocks' field
    3. POST with stocks but missing symbol
    4. POST with stocks but missing price
  Assert (each):
    - HTTP 400 or signal rejected
    - No crash, no unhandled exception in logs
    - System continues accepting valid signals after
  Pass: Graceful rejection of all malformed payloads

CT012 | Wrong Data Types
  Steps:
    1. Inject signal with price="not_a_number"
    2. Inject signal with price=-100
    3. Inject signal with price=0
    4. Inject signal with symbol=12345 (number instead of string)
  Assert:
    - Each rejected gracefully
    - No exception in main thread
    - status=INVALID_PRICE or equivalent
  Pass: Type validation works

CT013 | Oversized Payload
  Steps:
    1. POST with 1000 symbols in stocks array
    2. POST with 10MB body
  Assert:
    - Waitress handles gracefully (connection_limit or request size)
    - No OOM, no crash
  Pass: System survives oversized payloads

CT014 | Extra/Unknown Fields
  Steps:
    1. Inject valid signal with extra fields: {"stocks": [...], "junk": "data", "extra": 123}
  Assert:
    - Signal accepted (extra fields ignored)
    - No error in logs
  Pass: Tolerant parsing

--- CATEGORY 1D: STRESS & BACKPRESSURE ---

CT015 | Burst — 10 Signals in 2 Seconds
  Steps:
    1. Inject 10 different-symbol signals within 2s
  Assert:
    - All 10 accepted into queue
    - ThreadPoolExecutor (5 workers) processes in batches
    - No signal lost
    - Capital allocation handles concurrent reserves correctly
    - fm_ledger entries all balanced
  Pass: All 10 processed, invariant holds

CT016 | Burst — 50 Signals in 5 Seconds
  Steps:
    1. Inject 50 signals (unique symbols) within 5s
  Assert:
    - Queue fills but stays under 300 (no 503)
    - X-Queue-Warning header at 60% fill (>180)
    - Processing may take minutes but all complete
    - Capital exhaustion handled gracefully (REJECTED_CAPITAL_INSUFFICIENT)
  Pass: System handles heavy load without crash or data loss

CT017 | Queue Full — Backpressure (300+ signals)
  Steps:
    1. Inject 310 signals as fast as possible
  Assert:
    - First ~300: accepted (HTTP 200)
    - Beyond 300: HTTP 503 with backpressure response
    - X-Queue-Warning header appears at 180+ queue depth
    - System continues processing existing queue after flood stops
    - No crash, no OOM
  Pass: Backpressure fires correctly, system recovers

CT018 | Sustained Load — 1 Signal Every 5 Seconds for 10 Minutes
  Steps:
    1. Inject 120 signals over 10 minutes (steady drip)
  Assert:
    - Queue depth stays low (processing keeps up)
    - No memory growth
    - All signals get final status
  Pass: Steady-state processing stable

--- CATEGORY 1E: TIMING VARIANTS ---

CT019 | Signal at Market Open (09:15-09:20 IST)
  Steps:
    1. At 09:16: inject valid signal
  Assert:
    - Processed normally if within entry window (09:25+ per market_windows)
    - If before 09:25: rejected by market_windows check
  Pass: Entry window enforced correctly

CT020 | Signal at 14:45 IST (EOD Pre-Alert Window)
  Steps:
    1. At 14:46: inject valid signal
  Assert:
    - Should still be accepted (entry window: 09:25-15:00)
    - EOD pre-alert fires independently
  Pass: EOD alert doesn't block new signals

CT021 | Signal at 15:14 IST (Just Before Force Close)
  Steps:
    1. At 15:14: inject valid signal
  Assert:
    - Accepted into pipeline
    - But 15:15 force_close fires SOFT_KILL
    - Signal likely rejected at pipeline step 1 (kill switch) or abandoned
  Pass: Race between signal and force_close handled safely

CT022 | Signal During EOD Squareoff (15:17 IST)
  Steps:
    1. At 15:17: inject valid signal while EOD squareoff is running
  Assert:
    - Rejected (kill switch already active from force_close at 15:15)
    - EOD squareoff not disrupted
  Pass: No interference with EOD process

--- CATEGORY 1F: MID-TRADE VARIANTS ---

CT023 | Signal While Position Already Open for Same Symbol
  Steps:
    1. Inject RELIANCE signal → let it become OPEN position
    2. Inject another RELIANCE signal (different strategy)
  Assert:
    - Second signal: processed but risk engine should check existing exposure
    - If max_open_positions or sector exposure blocks: REJECTED
    - If allowed: second position opens independently
    - No corruption of first position's SL/TGT
  Pass: Multi-position for same symbol handled correctly (or correctly rejected)

CT024 | Rapid Same-Symbol Signals — Race Condition Probe
  Steps:
    1. Inject RELIANCE to 3 different scanners simultaneously (within 100ms)
  Assert:
    - Only 1 processes at a time (IN_PROCESS lock)
    - Others get IN_PROCESS status
    - No deadlock
    - System doesn't hang
  Pass: IN_PROCESS serialization works under pressure

--- CATEGORY 1G: CHARTINK RETRY SIMULATION ---

CT025 | 503 Response → Chartink Retry Behavior
  Steps:
    1. Fill queue to 300 (trigger backpressure)
    2. Send 1 more signal → get 503
    3. Wait 30s (let queue drain partially)
    4. Resend same signal
  Assert:
    - First attempt: 503
    - Retry: 200 (ACCEPTED or DUPLICATE depending on dedup window)
    - Signal eventually processed
  Pass: Retry-after-503 path works

CT026 | Webhook Server Thread Crash Recovery
  Steps:
    1. Identify how to crash the webhook-server thread
       (e.g., malformed HTTP that bypasses Flask but crashes Waitress)
    2. After crash: verify systemd restarts main.py
    3. After restart: inject valid signal
  Assert:
    - Restart happens within RestartSec (10s)
    - Post-restart signal accepted
    - No state corruption from crash
  Pass: Webhook thread failure → clean restart → recovery

--- DAY 1 TOTAL: 26 scenarios ---
--- End-of-Day 1 actions:
    - Run crash_test_reporter.py --day 1
    - Cleanup to clean state
    - Store Day 1 results in mempalace
    - If any FAIL: log as bug, decide fix-now vs fix-after-all-tests

================================================================================
PART 2 — DAY 2 (Tue Jun 9): SCREENING, CAPITAL & ORDER LIFECYCLE
================================================================================
Model for VS Code Claude: Opus 4.6 (capital race conditions need deep analysis)
Test window: 09:30 IST — 15:00 IST

OBJECTIVE: Verify secondary screening, capital management, position sizing,
           order placement, fill handling, and SL/TGT lifecycle.

--- CATEGORY 2A: SECONDARY SCREENING ---

CT027 | Screening Happy Path
  Steps:
    1. Inject signal for liquid stock (RELIANCE)
    2. Wait for screening completion
  Assert:
    - screener_results table: row created with score, components
    - If score >= threshold: pipeline continues
    - If score < threshold: REJECTED_SCREENER_SCORE
  Pass: Screening produces valid scored result

CT028 | Zerodha Quote API Timeout During Screening
  Steps:
    1. Block Zerodha REST API (network_controller --preset block_zerodha_rest --duration 15)
    2. Inject valid signal
    3. Wait for pipeline timeout (30s)
    4. Unblock API
  Assert:
    - Signal status: REJECTED (BrokerTimeoutError or pipeline timeout)
    - Capital NOT reserved (screening happens before reserve)
    - No capital leak
    - System continues accepting signals after unblock
  Pass: API failure during screening = clean rejection

CT029 | Zerodha Quote Returns Stale/Zero Price
  Steps:
    1. Inject signal for illiquid stock where LTP might be 0 or stale
    2. OR: mock adapter to return price=0 for a specific symbol
  Assert:
    - Screening rejects or handles zero-price gracefully
    - No division-by-zero in position sizing
    - No order placed with price=0
  Pass: Zero/stale price handled

CT030 | Pipeline Timeout (>30s total screening)
  Steps:
    1. Block Zerodha REST partially (add 20s latency via tc netem if possible,
       OR inject signal while API is slow)
    2. Signal should hit pipeline_timeout_sec: 30
  Assert:
    - Signal status: REJECTED or FAILED (timeout)
    - Capital not reserved
    - Worker thread freed (not stuck)
  Pass: Pipeline timeout works, no resource leak

CT031 | Score at Exact Threshold Boundary
  Steps:
    1. Inject signal for stock that scores exactly at min_eligible_score
  Assert:
    - Verify >= comparison (not >) — score AT threshold should PASS
    - OR verify < comparison — document which is implemented
  Pass: Boundary behavior matches code intent

--- CATEGORY 2B: CAPITAL & FUND MANAGEMENT ---

CT032 | Capital Allocation Happy Path
  Steps:
    1. Clean state (full capital available)
    2. Inject signal → let it reach FundManager.reserve()
  Assert:
    - fm_ledger: RESERVE entry with correct amount
    - available decreased by reserve amount
    - invariant holds: available + reserved + used == total
  Pass: Single allocation correct

CT033 | 5 Concurrent Capital Allocations
  Steps:
    1. Inject 5 signals simultaneously (within 200ms)
    2. Wait 60s for all to process
  Assert:
    - fm_ledger: 5 RESERVE entries (or fewer if capital exhausted)
    - Each allocation gets unique reservation_id
    - No two allocations overlap capital (total reserved ≤ total available at start)
    - Invariant holds at every point
    - RLock serialization confirmed (no concurrent mutation)
  Pass: Concurrent reserves don't corrupt capital

CT034 | Capital Exhaustion
  Steps:
    1. Inject signals until available capital < minimum position size
    2. Inject one more signal
  Assert:
    - Last signal: REJECTED_CAPITAL_INSUFFICIENT
    - No capital leak (all prior allocations accounted for)
    - Invariant holds
  Pass: Graceful exhaustion, no negative available

CT035 | Capital Invariant Violation (Forced)
  Steps:
    1. CAREFULLY corrupt capital state:
       - Direct DB UPDATE to fm_ledger or capital_snapshot to make
         available + reserved + used ≠ total
    2. Inject signal that triggers reserve()
  Assert:
    - CapitalInvariantViolation raised
    - HARD_KILL fires
    - Telegram alert sent
    - No order placed
  Cleanup: Restore DB, clear kill switch, cleanup
  Pass: Invariant violation → hard kill (critical safety test)

CT036 | Double Release Attempt
  Steps:
    1. Let a trade complete (SL or TGT hit)
    2. Manually call release_used() again with same reservation_id
       (via test script that accesses FundManager directly)
  Assert:
    - ValueError raised on second release
    - Capital state unchanged after double attempt
    - Invariant holds
  Pass: Double-release guard works

CT037 | Reserve Then Cancel (Pipeline Rejection After Reserve)
  Steps:
    1. Inject signal that passes screening + risk but fails at EntryGate timeout
    2. OR: inject signal, then activate kill switch before order placement
  Assert:
    - FundManager.release(reservation_id, reason) called
    - fm_ledger: RESERVE then RELEASE entries
    - available restored to pre-signal value
    - No capital leak
  Pass: Pipeline abort releases reserved capital

CT038 | Position Sizing Edge Cases
  Steps:
    1. Signal with very wide SL (low qty due to risk sizing)
    2. Signal with very tight SL (high qty, capped by concentration)
    3. Signal where qty rounds to 0 (SL too wide for capital)
    4. Signal for stock with lot_size > 1 (verify lot rounding)
  Assert (each):
    - Position sizer returns valid qty or 0
    - qty=0 → REJECTED_CAPITAL_INSUFFICIENT
    - lot_skew_rejection_threshold (0.25) enforced
    - max_position_value_rs (50000) enforced
  Pass: All sizing edge cases handled

--- CATEGORY 2C: ORDER PLACEMENT & FILL ---

CT039 | Order Placement Happy Path (Paper)
  Steps:
    1. Inject signal → let it reach OrderPlacer
    2. Wait for paper fill (LTP daemon polls every 5s)
  Assert:
    - orders table: entry order SUBMITTED → COMPLETE
    - SL and TGT orders placed after entry fill
    - trades table: status=OPEN
    - fm_ledger: RESERVE → COMMIT
    - Telegram: trade opened notification
  Pass: Full entry lifecycle works in paper mode

CT040 | Paper Fill — LTP Never Reaches Limit Price
  Steps:
    1. Inject signal with entry price far from current LTP
       (e.g., LIMIT BUY at LTP - 5%, stock trending up)
    2. Wait 60s (past fill_timeout_sec)
  Assert:
    - Entry order: CANCELLED after 60s timeout
    - Capital released (RESERVE → RELEASE, not COMMIT)
    - No open position
    - No orphan SL/TGT orders
  Pass: Fill timeout cancellation works

CT041 | SL Hit Lifecycle
  Steps:
    1. Open a position (inject signal, wait for fill)
    2. Simulate SL hit:
       - Paper mode: manipulate LTP to cross SL price
       - OR: directly modify order status in DB to COMPLETE (with SL leg)
    3. Wait for order_monitor to detect fill
  Assert:
    - Trade status: CLOSED, exit_reason=SL_HIT
    - TGT order: CANCELLED
    - Capital: release_used() called with PnL
    - fm_ledger: RELEASE_USED entry with negative PnL
    - Telegram: trade closed notification
  Pass: SL → close → cancel sibling → release capital

CT042 | TGT Hit Lifecycle
  Steps:
    1. Open a position
    2. Simulate TGT hit (LTP crosses target price)
  Assert:
    - Trade status: CLOSED, exit_reason=TGT_HIT
    - SL order: CANCELLED
    - Capital: release_used() with positive PnL
    - fm_ledger: RELEASE_USED entry
    - Telegram notification
  Pass: TGT → close → cancel sibling → release capital

CT043 | Partial Fill Then Timeout
  Steps:
    1. Inject signal, get entry order placed
    2. Simulate partial fill (if paper mode supports it —
       if not, document that partial fills are NOT tested in paper)
    3. Wait for partial_fill_timeout_minutes (5 min)
  Assert:
    - Order cancelled after 5 min
    - Partially filled qty: goes through close flow
    - Capital: adjusted for partial fill only
  Pass: Partial fill timeout triggers correct cleanup
  NOTE: If paper mode doesn't support partial fills, mark as CANNOT_TEST_PAPER
        and create a unit test instead

CT044 | Orphan Order Detection
  Steps:
    1. Open position, let SL + TGT orders be placed
    2. Cancel SL order directly via DB manipulation (simulate broker-side cancel)
    3. Wait for order_reconciler (15s cycle)
  Assert:
    - Reconciler detects SL_MISSING
    - Emergency exit triggered (_emergency_exit MARKET close)
    - SOFT_KILL activated (orphan callback)
    - Telegram alert
  Cleanup: Clear kill switch
  Pass: SL_MISSING → emergency exit → soft kill

CT045 | Order Reconciliation — MANUAL_CLOSE Detection
  Steps:
    1. Open position
    2. Mark trade as COMPLETE in orders table directly (simulate broker-side fill)
    3. Wait for order_reconciler (15s)
  Assert:
    - Reconciler detects MANUAL_CLOSE
    - Trade closed with exit_reason=MANUAL_CLOSE
    - Capital released
    - Sibling orders cancelled
  Pass: External close detected and handled

CT046 | SmartTgtManager — SL Trail Success
  Steps:
    1. Open LONG position
    2. Simulate price moving UP past trigger_pct threshold
    3. Wait for candle close (1-min candle)
  Assert:
    - SmartTgtManager modifies SL order (cancel old + place new)
    - smart_tgt_state table updated
    - SL price moved up
    - Log shows SL_TRAIL event
  Pass: Trailing SL advances correctly

CT047 | SmartTgtManager — Cancel Succeeds, Replace Fails
  Steps:
    1. Open position with trailing SL active
    2. Trigger SL trail
    3. During replace: block Zerodha REST API (network_controller)
  Assert:
    - SL cancel succeeds (already sent before block)
    - Replace fails (BrokerTimeoutError)
    - NAKED POSITION detected by order_reconciler
    - Emergency exit triggered
    - SOFT_KILL
  Cleanup: Unblock API, clear kill switch
  Pass: SL trail failure → naked position → emergency exit
  NOTE: This is risk area #13 from assessment. Critical test.

CT048 | Broker Rejection — Insufficient Funds (Simulated)
  Steps:
    1. Inject signal
    2. Mock adapter to return OrderRejectedError("Insufficient funds")
       OR: if paper mode can't simulate this, use unit test
  Assert:
    - Signal: REJECTED
    - Capital released
    - No position opened
  Pass: Broker rejection handled

--- CATEGORY 2D: MID-TRADE SCENARIOS ---

CT049 | Capital Mutation During Active Position
  Steps:
    1. Open 2 positions (RELIANCE, TCS)
    2. While both open: inject 3 more signals (burst)
  Assert:
    - New reserves calculated against REMAINING available
    - Invariant holds with 2 used + N reserved + remaining available
    - No corruption of existing positions
  Pass: Capital accounting correct under concurrent state

CT050 | Order Placement While Reconciler Running
  Steps:
    1. Time signal injection to land during reconciler's 15s poll
    2. (May need to inject at exact right moment — repeat if needed)
  Assert:
    - No lock contention causing timeout
    - Both operations complete
    - No DB deadlock
  Pass: Concurrent order + reconciliation don't conflict

CT051 | Multiple SL Hits in Quick Succession
  Steps:
    1. Open 3 positions
    2. Simulate all 3 SL hits within 5 seconds
  Assert:
    - All 3 closed correctly
    - All 3 TGT orders cancelled
    - Capital released 3 times (no double-release)
    - Invariant holds
    - If daily loss limit breached: SOFT_KILL fires
  Pass: Rapid multiple closes handled correctly

--- DAY 2 TOTAL: 25 scenarios ---
--- End-of-Day 2 actions: same as Day 1

================================================================================
PART 3 — DAY 3 (Wed Jun 10): KILL SWITCH & CIRCUIT BREAKERS
================================================================================
Model for VS Code Claude: Opus 4.6 (safety-critical paths)
Test window: 09:30 IST — 15:30 IST (includes EOD window)

OBJECTIVE: Verify every kill switch trigger, every circuit breaker,
           persistence, recovery, and edge cases.

--- CATEGORY 3A: SOFT KILL TRIGGERS ---

CT052 | SOFT_KILL — Daily Loss Limit (Rs 10,000)
  Steps:
    1. Open positions and simulate losses totaling Rs 10,001
    2. (Via multiple SL hits in paper mode)
  Assert:
    - SOFT_KILL fires
    - EOD squareoff triggered
    - kill_switch_state table: SOFT_KILL
    - Telegram alert
    - New signals rejected
  Cleanup: Clear kill switch
  Pass: Daily loss limit works at production threshold

CT053 | SOFT_KILL — Daily Loss % (5%)
  Steps:
    1. Simulate losses = 5.1% of total capital
  Assert:
    - SOFT_KILL fires
    - Same as CT052 assertions
  Pass: Percentage-based limit works

CT054 | SOFT_KILL — 3 Consecutive API Failures
  Steps:
    1. Block Zerodha REST API
    2. Trigger 3 actions that call broker (order placement or quote)
    3. After 3 failures: verify soft kill
  Assert:
    - After failure 1 and 2: logged, but system continues
    - After failure 3: SOFT_KILL
    - kill_switch_state persisted
  Cleanup: Unblock API, clear kill switch
  Pass: API failure auto-trip works

CT055 | SOFT_KILL — Clock Skew > 30s
  Steps:
    1. Change system clock: sudo date -s "+60 seconds"
    2. Wait for clock_skew_probe (60s cycle, live mode only)
    3. If paper mode skips probe: verify cold-start NTP check instead
  Assert:
    - SOFT_KILL fires (if probe active)
    - OR: startup check fails with clock skew (if testing cold start)
  Cleanup: Restore clock via NTP
  Pass: Clock skew detection works
  NOTE: clock_skew_probe only runs in LIVE mode. For PAPER, test via startup check.

CT056 | SOFT_KILL — WebSocket 10 Reconnect Failures
  Steps:
    1. Block wss.kite.trade (WebSocket)
    2. Wait for reconnect attempts (exponential backoff: 1s→30s)
    3. After 10 failures: verify soft kill
  Assert:
    - Reconnect attempts logged with backoff
    - After attempt 10: SOFT_KILL
  Cleanup: Unblock, clear kill switch
  Pass: WebSocket failure escalation works
  NOTE: Only applicable if live_feed is active in paper mode. If not, mark N/A.

CT057 | SOFT_KILL — Orphan Order Detected
  Steps:
    1. Same as CT044 (manipulate DB to create SL_MISSING)
    2. Verify orphan callback fires soft kill
  Assert:
    - SOFT_KILL via orphan callback
    - Emergency exit triggered
  Pass: Orphan → soft kill path

CT058 | SOFT_KILL — Token Expiry
  Steps:
    1. Delete or corrupt data_store/session/zerodha_token.json
    2. Wait for token_monitor check (1800s cycle) — too long for test
    3. Alternative: call token validation directly or restart system
  Assert:
    - Token invalid detected
    - SOFT_KILL (or exit code 6 in live mode)
    - Telegram alert
  Pass: Token expiry handled
  NOTE: token_monitor checks every 1800s. May need to trigger manually.

CT059 | SOFT_KILL — 15:15 Force Close
  Steps:
    1. Have open positions at 15:14
    2. Wait for 15:15 force_close_time
  Assert:
    - SOFT_KILL fires
    - Entry orders cancelled
    - EOD squareoff triggered at 15:17
    - Positions closed via MARKET orders
  Pass: Force close → EOD squareoff chain works

--- CATEGORY 3B: HARD KILL TRIGGERS ---

CT060 | HARD_KILL — Capital Invariant Violation
  Steps:
    - Same as CT035 (forced invariant corruption)
  Assert:
    - HARD_KILL fires (not soft kill)
    - ALL open orders cancelled at broker
    - kill_switch_state: HARD_KILL
    - System requires --resume to restart
  Pass: Invariant violation → hard kill → full stop

CT061 | HARD_KILL — 3 Consecutive Circuit Breaker API Failures
  Steps:
    1. Block Zerodha REST during order operations
    2. Trigger 3 consecutive failures in circuit_breaker category
  Assert:
    - HARD_KILL fires
    - All open orders cancelled
    - Requires --resume
  Cleanup: Unblock, --resume
  Pass: Circuit breaker hard kill works

--- CATEGORY 3C: KILL SWITCH PERSISTENCE & RECOVERY ---

CT062 | Kill Switch Survives Restart
  Steps:
    1. Trigger SOFT_KILL
    2. sudo systemctl restart trading-system (without --resume)
  Assert:
    - After restart: kill switch state loaded from DB
    - System starts in killed state
    - New signals rejected
  Pass: Kill switch persisted across restart

CT063 | Kill Switch Auto-Clear — Stale State
  Steps:
    1. Trigger SOFT_KILL
    2. Wait for system to log kill_switch_state with today's date
    3. Change system date to tomorrow (or wait until next day)
    4. Restart system
  Assert:
    - clear_stale_state(today) clears previous-day kill switch
    - System starts normally (no kill switch active)
  Pass: Stale kill switch cleared on new day
  NOTE: Changing system date is invasive. Alternative: directly test
        clear_stale_state() with a mocked date via test script.

CT064 | Kill Switch Recovery — --resume Flag
  Steps:
    1. Trigger HARD_KILL
    2. Stop system
    3. Restart with --resume flag
  Assert:
    - Kill switch cleared
    - System starts normally
    - Capital state correct (rehydrated)
    - Ready to accept signals
  Pass: --resume clears kill switch and allows normal operation

CT065 | Soft Kill → Resume → Soft Kill → Hard Kill Sequence
  Steps:
    1. Trigger SOFT_KILL (API failure)
    2. Resume (--resume)
    3. Trigger another SOFT_KILL (different reason)
    4. Resume
    5. Trigger HARD_KILL (invariant violation)
  Assert:
    - Each transition correct
    - State tracked accurately at each step
    - Final HARD_KILL: full stop behavior
    - kill_switch_state table reflects latest state
  Pass: Sequential kill switch transitions work

CT066 | Kill Switch Mid-Order — In-Flight Kite Call
  Steps:
    1. Inject signal that reaches OrderPlacer
    2. Activate SOFT_KILL simultaneously (tight timing)
    3. Observe: does the HTTP call to Kite complete?
  Assert:
    - If call already dispatched: completes naturally, fill reconciled
    - If call not yet dispatched: FIX-070 second kill switch check blocks it
    - No orphan order created without tracking
  Pass: Mid-pipeline kill doesn't create untracked state
  NOTE: This is risk area #9. May need multiple attempts for timing.

--- CATEGORY 3D: CIRCUIT BREAKERS ---

CT067 | Max Open Positions (10)
  Steps:
    1. Open 10 positions (inject 10 signals for different symbols)
    2. Inject 11th signal
  Assert:
    - 11th: REJECTED by risk engine (max_open_positions)
    - First 10: all tracked correctly
  Pass: Position cap enforced

CT068 | Max Daily Trades (20)
  Steps:
    1. Execute 20 trades (open + close cycles)
    2. Inject 21st signal
  Assert:
    - 21st: REJECTED by risk engine
  Pass: Daily trade cap enforced
  NOTE: This requires 20 full cycles — time consuming. May need fast
        paper fills and aggressive SL placement.

CT069 | Max Consecutive Losses (4)
  Steps:
    1. Open 4 positions with tight SLs that will hit
    2. After 4 SL hits: inject 5th signal
  Assert:
    - After loss 4: consecutive_losses counter = 4
    - 5th signal: REJECTED (max_consecutive_losses)
    - Counter resets on next win
  Pass: Consecutive loss circuit breaker works at production value (4)

CT070 | Max Sector Exposure (40%)
  Steps:
    1. Open positions in same sector totaling 40%+ of capital
    2. Inject another same-sector signal
  Assert:
    - REJECTED_RISK_ENGINE (sector exposure)
  Pass: Sector exposure cap works
  NOTE: Requires sector classification data in instruments. If not
        available for paper symbols, mark as CANNOT_TEST and document.

CT071 | Strategy Circuit Breaker (Loss > 2x Avg Before 12:00)
  Steps:
    1. Run 1 strategy with losses > 2x average
    2. Before 12:00 IST: inject signal for same strategy
  Assert:
    - Strategy paused until 12:00
    - Signal REJECTED with strategy_circuit_breaker reason
    - After 12:00: strategy resumes
  Pass: Per-strategy circuit breaker works

CT072 | Partial Fill Timeout (5 Minutes)
  Steps:
    - Same as CT043
  Assert:
    - After 5 min: stuck partial fill order cancelled
    - Partial qty handled via close flow
  Pass: Partial fill timeout fires

--- CATEGORY 3E: EOD SQUAREOFF ---

CT073 | EOD Squareoff — Happy Path
  Steps:
    1. Open 2 INTRADAY positions
    2. Let system reach 15:17 IST
  Assert:
    - SOFT_KILL at 15:15
    - Entry orders cancelled
    - MARKET exit for both positions at 15:17
    - eod_squareoff_log entries
    - Telegram notification
    - Trades: status=CLOSED, exit_reason=EOD_SQUAREOFF
    - Capital released for both
  Pass: Normal EOD squareoff works

CT074 | EOD Squareoff — Market Order Fails
  Steps:
    1. Open position
    2. At 15:16: block Zerodha REST API
    3. 15:17: EOD squareoff fires, MARKET order fails
    4. Unblock after 30s
  Assert:
    - Position REMAINS OPEN (documented risk area #8)
    - order_monitor continues watching
    - Next reconciler cycle may detect
    - Warning logged
    - This is a KNOWN LIMITATION — document behavior
  Pass: Failure is handled gracefully (no crash), even if position stays open

CT075 | EOD Pre-Alert at 14:45
  Steps:
    1. Have open positions at 14:44
    2. Wait for 14:45
  Assert:
    - Telegram pre-alert sent
    - No action taken (just notification)
    - Positions remain open
  Pass: Pre-alert fires without side effects

CT076 | EOD + Force Close Overlap
  Steps:
    1. Open positions
    2. Observe 15:15 force_close → 15:17 squareoff sequence
  Assert:
    - Force close fires SOFT_KILL at 15:15
    - EOD squareoff fires at 15:17 (2 minutes later)
    - No conflict between the two
    - Both logged correctly
  Pass: 2-min sequence works without race condition
  NOTE: This is risk area #15 from assessment.

--- DAY 3 TOTAL: 25 scenarios ---

================================================================================
PART 4 — DAY 4 (Thu Jun 11): INFRASTRUCTURE STRESS
================================================================================
Model for VS Code Claude: Opus 4.6 (system-level scenarios)
Test window: 09:30 IST — 16:30 IST (includes post-market cron tests)

OBJECTIVE: Verify system behavior under infrastructure failures —
           network, disk, memory, process signals, restart recovery.

--- CATEGORY 4A: PROCESS SIGNALS ---

CT077 | SIGINT — Graceful Shutdown (Idle)
  Steps:
    1. System running, no open positions, no pending signals
    2. sudo systemctl stop trading-system (sends SIGINT)
  Assert:
    - Graceful shutdown sequence completes (all 17 steps in P.6)
    - SHUTDOWN event in system_events table
    - WAL checkpoint done
    - DB connection closed
    - Telegram "System Stopping" sent
    - Exit code 0
  Pass: Clean shutdown when idle

CT078 | SIGINT — Graceful Shutdown During Active Trade
  Mid-trade variant:
    1. Open 2 positions
    2. sudo systemctl stop trading-system
  Assert:
    - Shutdown stops webhook (503 for new signals)
    - Stops signal processor
    - Cancels ENTRY orders (FIX-129 Item 45)
    - Stops order_monitor (SL/TGT remain at broker)
    - DOES NOT squareoff open positions (positions survive restart)
    - SHUTDOWN marker written
    - Next startup: WARM scenario, rehydrates open positions
  Pass: Graceful shutdown preserves open positions safely

CT079 | SIGINT — Graceful Shutdown During Signal Processing
  Mid-trade variant:
    1. Inject burst of 10 signals
    2. While processing (within 5s): sudo systemctl stop trading-system
  Assert:
    - Signals in queue: lost (acceptable — in-memory)
    - Signal mid-pipeline: abandoned at current step
    - Capital reserved but not committed: RELEASED on cleanup
    - No partial state corruption
  Pass: Mid-processing shutdown doesn't corrupt state

CT080 | SIGKILL — During Idle
  Steps:
    1. System running, idle
    2. sudo kill -9 $(pgrep -f main.py)
  Assert:
    - Immediate death (no graceful shutdown)
    - No SHUTDOWN marker in system_events
    - Next startup: CRASH scenario detected
    - All rehydration paths run
    - System starts normally
  Pass: SIGKILL idle → clean crash recovery

CT081 | SIGKILL — During Active Trade
  Mid-trade variant:
    1. Open 2 positions with active SL/TGT orders at broker
    2. sudo kill -9 $(pgrep -f main.py)
    3. Wait 10s, then: sudo systemctl start trading-system
  Assert:
    - CRASH scenario on startup
    - Open positions rehydrated from trades table
    - SL/TGT orders rehydrated from orders table + broker reconciliation
    - Capital rebuilt from fm_ledger
    - Kill switch: loaded from DB (should be clear if no kill before crash)
    - SmartTgtManager: rehydrated from smart_tgt_state
    - EntryGate: rehydrated from gate_state
    - System resumes monitoring positions
    - Telegram: "System Starting (CRASH recovery)"
  Pass: Full state recovery after SIGKILL with open positions

CT082 | SIGKILL — During Order Placement
  Mid-trade variant (hardest timing):
    1. Inject signal, wait for it to reach OrderPlacer
    2. SIGKILL at exact moment of HTTP call to Kite
    3. Restart
  Assert:
    - If order was placed at broker: detected by order_reconciler on restart
    - If order was NOT placed: signal is lost, no orphan
    - Capital: if RESERVE was written to fm_ledger but COMMIT wasn't,
      rehydration should handle this (orphan reservation)
    - Invariant holds after rehydration
  Pass: No untracked state after crash during order placement
  NOTE: Timing is critical. May need multiple attempts.

CT083 | SIGKILL — During Capital Mutation
  Steps:
    1. Inject 5 signals (burst) to cause multiple FM mutations
    2. SIGKILL during processing
    3. Restart
  Assert:
    - fm_ledger: all committed transactions survive (WAL + FULL sync)
    - Rehydration rebuilds from fm_ledger
    - No partial/corrupt capital state
    - Invariant holds
  Pass: Capital safe under crash during mutations

--- CATEGORY 4B: RESTART RECOVERY ---

CT084 | Cold Start — No Prior Session
  Steps:
    1. Delete session table entry (or use fresh DB)
    2. Start system
  Assert:
    - COLD scenario detected
    - All 14+ startup checks run
    - Capital initialized from accounts.csv
    - System ready to trade
  Pass: Fresh start works

CT085 | Warm Start — Normal Previous Shutdown
  Steps:
    1. Graceful shutdown (SIGINT)
    2. Restart
  Assert:
    - WARM scenario detected
    - Faster startup (some checks skipped or lighter)
    - Open positions rehydrated if any survived shutdown
    - Kill switch: clear
  Pass: Normal restart cycle works

CT086 | Crash Start — SIGKILL Recovery
  Steps:
    - Covered by CT081. Verify CRASH scenario specifically.
  Assert:
    - "CRASH" scenario in logs
    - All rehydration paths exercised
  Pass: CRASH detection and recovery

CT087 | HALT Start — Exit Code 3
  Steps:
    1. Cause startup check failure (e.g., delete required config file)
    2. System exits with code 3
    3. Verify systemd does NOT restart (RestartPreventExitStatus=3)
  Assert:
    - Exit code 3
    - systemd status: inactive (dead), NOT restarting
    - No restart loop
  Cleanup: Restore config
  Pass: Startup failure → clean exit without restart loop

CT088 | Rehydration with Dirty State
  Steps:
    1. Open 2 positions
    2. Insert orphan fm_ledger RESERVE row (no matching COMMIT or RELEASE)
    3. SIGKILL, restart
  Assert:
    - Rehydration handles orphan reservation
    - Capital invariant holds (orphan reservation accounted for or cleaned)
    - Open positions correct
  Pass: Dirty fm_ledger handled on recovery
  NOTE: Risk area #14 from assessment.

CT089 | Systemd Restart Loop Protection
  Steps:
    1. Cause system to crash 6 times within 10 seconds
       (StartLimitBurst default = 5 starts in 10s)
    2. After 5th crash: systemd should refuse to restart
  Assert:
    - First 5 restarts: happen with RestartSec=10
    - 6th: systemd says "start request repeated too quickly"
    - No infinite restart loop
  Pass: Systemd rate limits restarts
  NOTE: Risk area #6. StartLimitBurst not explicitly set.

--- CATEGORY 4C: NETWORK FAILURES ---

CT090 | Network Drop — 30s (All Outbound)
  Steps:
    1. System running with open position
    2. sudo iptables -A OUTPUT -j DROP (or network_controller --preset block_all --duration 30)
    3. After 30s: unblock
  Assert:
    - WebSocket disconnects → reconnect attempts start
    - REST API calls timeout (10s each)
    - Position state preserved (SL/TGT at broker unaffected)
    - After unblock: WebSocket reconnects, system resumes
    - No data loss
  Pass: 30s network outage recoverable

CT091 | Network Drop — 5 Minutes Sustained
  Steps:
    1. System with open position
    2. Block all outbound for 5 min
  Assert:
    - WebSocket: multiple reconnect attempts (exponential backoff)
    - If 10 reconnects fail: SOFT_KILL
    - REST: multiple timeouts logged
    - After unblock: recovery depends on kill switch state
    - If SOFT_KILL fired: needs --resume after unblock
  Pass: Extended outage triggers safety mechanisms

CT092 | Network Drop — Zerodha WebSocket Only (REST Works)
  Steps:
    1. Block only wss.kite.trade
    2. REST API (api.kite.trade) still works
  Assert:
    - Live feed drops: candle updates stop
    - Paper fill daemon uses REST quote — still works
    - Order operations via REST: still work
    - WebSocket reconnect attempts begin
  Pass: Partial network failure handled

CT093 | Network Drop — Zerodha REST Only (WebSocket Works)
  Steps:
    1. Block only api.kite.trade (REST)
    2. WebSocket feed continues
  Assert:
    - New signal screening fails (quote API needed)
    - Order placement fails (BrokerTimeoutError)
    - Existing positions: SL/TGT at broker, not affected
    - API failure counter increments
    - After 3 failures: SOFT_KILL
  Pass: REST-only failure triggers safety after threshold

CT094 | Network Drop During Trailing SL Update
  Mid-trade variant:
    1. Open position with active SL trail
    2. SL trail triggers (price moves past threshold)
    3. Block Zerodha REST during modify_order call
  Assert:
    - SL cancel/modify fails
    - Naked position or stale SL detected
    - order_reconciler catches SL_MISSING
    - Emergency exit
  Pass: Network drop during SL modification handled

--- CATEGORY 4D: DISK & DB ---

CT095 | Disk Full Simulation
  Steps:
    1. Fill disk to < 2GB free (dd if=/dev/zero of=/tmp/filler bs=1G count=X)
    2. Inject signal → triggers DB write
  Assert:
    - Disk check at startup would have caught this
    - Mid-session: SQLite write fails with "disk full"
    - System logs error, degrades gracefully
    - No DB corruption
  Cleanup: rm /tmp/filler
  Pass: Disk full doesn't corrupt DB
  NOTE: Be careful — don't fill to 0 bytes. Leave ~500MB.

CT096 | DB Lock Contention
  Steps:
    1. Open a long-running SQLite transaction from external script
       (e.g., BEGIN IMMEDIATE; SLEEP 35s; COMMIT;)
    2. Inject signal (system tries to write)
  Assert:
    - busy_timeout (30s) exceeded → OperationalError
    - Signal marked FAILED
    - System doesn't crash
    - After external transaction commits: system resumes
  Pass: DB lock handled without crash

CT097 | WAL Recovery After Crash
  Steps:
    1. Insert signal (generates WAL entries)
    2. SIGKILL immediately after (before checkpoint)
    3. Restart
  Assert:
    - WAL replayed on next connection
    - Signal data intact
    - No data loss
  Pass: WAL crash recovery works

--- CATEGORY 4E: CLOCK & TIME ---

CT098 | Clock Skew — Forward 60s
  Steps:
    1. sudo date -s "+60 seconds"
    2. Inject signal
    3. Observe clock_skew_probe (if live mode) or startup check
  Assert:
    - Clock skew detected (>30s threshold)
    - SOFT_KILL or startup failure
  Cleanup: sudo ntpdate pool.ntp.org
  Pass: Forward clock skew detected

CT099 | Clock Skew — Backward 60s
  Steps:
    1. sudo date -s "-60 seconds"
    2. Same as above
  Assert:
    - Also detected (absolute skew check)
  Pass: Backward clock skew detected

--- CATEGORY 4F: TOKEN & AUTH ---

CT100 | Token Expiry During Market Hours
  Steps:
    1. Delete zerodha_token.json while system running
    2. Inject signal (triggers API call)
  Assert:
    - BrokerAuthError on API call
    - SOFT_KILL + token invalidation
    - Telegram alert
    - token-watcher.service: detects missing token, waits for refresh
  Pass: Token expiry mid-session handled

CT101 | Token Refresh Cycle
  Steps:
    1. Simulate fresh token file creation (write valid token JSON)
    2. token-watcher detects → starts trading-system
  Assert:
    - trading-system starts with fresh token
    - Startup checks pass
    - System ready to trade
  Pass: Token refresh → auto-start works

--- CATEGORY 4G: CRON JOBS ---

CT102 | Cron Silent Failure
  Steps:
    1. Rename a cron script (e.g., mv eod_verify.py eod_verify.py.bak)
    2. Wait for its scheduled time
    3. Wait for check_cron_drift at 18:00 IST
  Assert:
    - Cron job fails (script not found)
    - cron_heartbeat table: missing entry for this job
    - check_cron_drift detects missing heartbeat → Telegram alert
  Cleanup: Rename back
  Pass: Cron drift detection catches missing jobs

CT103 | Cron Overlap — metrics_baseline During Heavy Signal Processing
  Steps:
    1. Inject burst of 20 signals
    2. While processing: capture_metrics_baseline runs (*/5 minute)
  Assert:
    - Metrics script: read-only DB queries, no interference
    - Signal processing not slowed
    - No DB lock contention
  Pass: Concurrent cron + trading doesn't conflict

--- CATEGORY 4H: LOG & MONITORING ---

CT104 | Log File Growth — Full Day Simulation
  Steps:
    1. Inject 50 signals over 2 hours
    2. Check log file sizes
  Assert:
    - Logs grow predictably
    - No unbounded growth per signal
    - Structured logging format maintained
  Pass: Logging doesn't cause disk issues

--- DAY 4 TOTAL: 28 scenarios ---

================================================================================
PART 5 — DAY 5 (Fri Jun 12): CHAOS DAY — COMBINED MULTI-FAILURE
================================================================================
Model for VS Code Claude: Opus 4.6 (complex multi-failure analysis)
Test window: 09:30 IST — 16:30 IST

OBJECTIVE: Test system under realistic combined failures. Real chaos —
           multiple things going wrong simultaneously.

--- CATEGORY 5A: MULTI-FAILURE COMBINATIONS ---

CT105 | Signal Flood + Network Drop Simultaneously
  Steps:
    1. Start injecting 50 signals (flood)
    2. At signal #25: block all outbound (network_controller --duration 30)
    3. Unblock after 30s
    4. Wait for system to stabilize
  Assert:
    - First 25 signals: some processed, some in queue
    - During block: screening failures (API timeout), API failure counter rises
    - If 3+ failures: SOFT_KILL fires
    - After unblock: queue continues draining (if no kill switch) or rejects all (if killed)
    - No crash, no data corruption
    - Capital invariant holds
  Pass: Concurrent flood + network failure handled

CT106 | Kill Switch + Active Positions + EOD Squareoff
  Steps:
    1. Open 3 positions
    2. Trigger SOFT_KILL manually (daily loss limit)
    3. Verify: no new signals accepted
    4. Wait for 15:17 EOD squareoff
  Assert:
    - Kill switch prevents new entries
    - EOD squareoff still fires (squareoff runs despite kill switch)
    - All 3 positions closed
    - Capital released
    - Invariant holds
  Pass: Kill switch + EOD cooperate (kill doesn't prevent squareoff)

CT107 | SIGKILL + Restart + Immediate Signal Flood
  Steps:
    1. Open 2 positions
    2. SIGKILL
    3. Immediately restart system
    4. During startup rehydration: inject 20 signals to webhook
  Assert:
    - Startup checks run (system not ready during rehydration)
    - Webhook should reject signals during startup OR queue them
    - After startup complete: pending signals processed
    - Rehydrated positions intact
    - No double-counting of capital
  Pass: Crash → restart → immediate load handled

CT108 | Capital Invariant Violation + Open Positions + Hard Kill
  Steps:
    1. Open 2 positions
    2. Force capital invariant violation (corrupt DB)
    3. Trigger mutation (inject signal)
  Assert:
    - HARD_KILL fires
    - Open broker orders cancelled
    - Positions remain at broker (not squaredoff by hard kill)
    - System halted
    - --resume required
  Cleanup: Fix DB, --resume
  Pass: Hard kill with open positions doesn't lose track

CT109 | Multiple Positions + EOD Squareoff Failure + Position Remains
  Steps:
    1. Open 3 positions
    2. At 15:16: block Zerodha REST API
    3. 15:17: EOD squareoff fires → MARKET orders fail for all 3
    4. Unblock after 60s
  Assert:
    - All 3 squareoff attempts fail
    - Positions remain open
    - Logged as WARNING
    - order_monitor continues watching
    - Positions survive if system stays running
    - What happens next day? (Document behavior)
  Pass: Multiple EOD failures don't crash system

CT110 | Network Drop + WebSocket Reconnect + SL Trail + Candle Gap
  Steps:
    1. Open position with trailing SL active
    2. Block WebSocket only (REST works)
    3. SL trail should trigger but candle data stale (no WebSocket)
    4. Unblock after 60s
  Assert:
    - SmartTgtManager: no candle updates during block
    - SL trail paused (no candle close events)
    - After reconnect: candle data resumes
    - If price gapped through SL during block: order_monitor detects via REST poll
  Pass: Candle gap during WebSocket failure doesn't miss SL

CT111 | DB Busy + Signal Burst + Capital Allocation
  Steps:
    1. Start long-running DB transaction (external script, 15s)
    2. Inject 10 signals simultaneously
  Assert:
    - busy_timeout (30s) allows signals to wait
    - Signals process after external txn commits
    - If timeout exceeded (>30s): signals fail gracefully
    - No deadlock
  Pass: DB contention under load handled

--- CATEGORY 5B: FULL DAY SIMULATION ---

CT112 | Complete Trading Day Simulation
  Steps:
    1. 09:16: System starts (token fresh)
    2. 09:20: Pre-market healthcheck passes
    3. 09:30: Inject 5 signals (morning burst)
    4. 09:35: 3 pass screening, 2 rejected
    5. 09:40: 3 orders placed, fills within 30s
    6. 10:00: 1 SL hit (loss)
    7. 10:30: Inject 3 more signals
    8. 11:00: 1 TGT hit (profit)
    9. 12:00: Inject 2 signals (afternoon)
    10. 13:00: SL trail advances on 1 position
    11. 14:45: EOD pre-alert
    12. 15:15: Force close
    13. 15:17: EOD squareoff for remaining positions
    14. 15:40-17:00: EOD cron chain runs
    15. 17:00: Verify all reports generated
  Assert:
    - Every step produces expected state
    - Capital at end = start + sum(PnL) - sum(costs)
    - All tables consistent
    - All Telegram notifications sent
    - Daily report generates correctly
    - fm_ledger audit trail complete
    - No errors in logs (except expected rejections)
  Pass: Full day simulation end-to-end correct
  NOTE: This is the MOST IMPORTANT test. Budget 2-3 hours.

--- CATEGORY 5C: REHYDRATION STRESS ---

CT113 | Maximum State SIGKILL Recovery
  Steps:
    1. Build maximum dirty state:
       - 3 open positions (RELIANCE, TCS, INFY)
       - 2 pending entry orders (HDFCBANK, ICICIBANK)
       - 1 active gate watch (SBIN)
       - 1 active SL trail (RELIANCE)
       - 5 signals in queue
    2. SIGKILL
    3. Restart
  Assert:
    - 3 open positions rehydrated from trades table
    - SL/TGT orders rehydrated from orders table
    - SL trail state from smart_tgt_state table
    - Gate watch from gate_state table
    - 2 pending entries tracked via order_monitor
    - Capital rebuilt correctly from fm_ledger
    - 5 queued signals: LOST (acceptable)
    - Invariant holds
    - order_reconciler.reconcile_once() detects any drift
  Pass: Maximum dirty state fully recovered
  NOTE: This is the ultimate recovery test.

--- CATEGORY 5D: RAPID RESTART CYCLE ---

CT114 | Start → Crash → Start → Crash — State Corruption Check
  Steps:
    1. Start system (clean state)
    2. Inject 2 signals
    3. SIGKILL after 10s
    4. Restart immediately
    5. Inject 2 more signals
    6. SIGKILL after 10s
    7. Restart
    8. Inject 1 signal
    9. Let it process normally
  Assert:
    - No state corruption after 2 crash cycles
    - All committed data preserved
    - Signal counts correct (no phantom signals)
    - Capital invariant holds
    - No duplicate fm_ledger entries
  Pass: Rapid crash cycles don't corrupt data

--- CATEGORY 5E: KILL SWITCH MARATHON ---

CT115 | All Kill Switches in Sequence
  Steps:
    1. Trigger SOFT_KILL (API failure) → resume
    2. Trigger SOFT_KILL (daily loss) → resume
    3. Trigger SOFT_KILL (clock skew if testable) → resume
    4. Trigger HARD_KILL (invariant violation) → resume
    5. Verify system healthy after full sequence
  Assert:
    - Each kill switch fires correctly
    - Each resume clears correctly
    - No accumulated state from previous kills
    - System fully functional at end
    - kill_switch_state table shows latest state only
  Pass: Sequential kill switch marathon — no accumulated damage

--- CATEGORY 5F: EDGE CASES ---

CT116 | Signal at Exact Dedup Boundary (300s)
  Steps:
    1. Inject signal at time T
    2. Inject identical signal at time T + 299s (inside window)
    3. Inject identical signal at time T + 301s (outside window)
  Assert:
    - Signal at T+299: DUPLICATE
    - Signal at T+301: ACCEPTED (new dedup window)
  Pass: Dedup boundary exact

CT117 | System Running Over Weekend (No Market)
  Steps:
    1. Leave system running from Friday EOD
    2. On Monday: inject signal at 09:30
  Assert:
    - System handles weekend gracefully (no errors from idle time)
    - Monday signal processes normally
    - Cron jobs: only Mon-Fri jobs run; weekend: only check_cron_drift (daily)
    - No accumulated state issues
  Pass: Weekend idle → Monday resume seamless
  NOTE: This tests whether the system handles multi-day idle.
        May need to simulate by changing date.

CT118 | Concurrent EOD Scripts Accessing DB
  Steps:
    1. At 16:15 IST: compute_strategy_metrics AND reconcile_pnl both run
       (per cron schedule — same minute)
  Assert:
    - Both complete successfully
    - No DB lock contention (per-thread connections)
    - Data integrity in both outputs
  Pass: Concurrent post-market scripts don't conflict

CT119 | AGY Isolation Verification
  Steps:
    1. Manually try to write a .py file from agy context
    2. Manually try to modify DB from agy context
    3. Manually try to run systemctl from agy context
  Assert:
    - All 3 blocked by AGENTS.md charter
    - No actual modification possible
    - agy respects boundaries
  Pass: AGY cannot modify system
  NOTE: This tests the AGENTS.md enforcement.
        If agy ignores charter: CRITICAL finding.

--- DAY 5 TOTAL: 15 scenarios ---

================================================================================
PART 6 — POST-CRASH-TEST ACTIONS
================================================================================

6.1  AGGREGATE RESULTS:
     - Merge all 5 day reports into: reports/crash_test/master_results.md
     - Summary: X PASS / Y FAIL / Z PARTIAL / W CANNOT_TEST
     - Critical failures list with root cause

6.2  BUG FIX PHASE (Jun 13-14 weekend or Jun 15-16):
     - Priority: FAIL items first, PARTIAL items second
     - STANDING RULES apply: permanent fix, paper+live parity
     - Each fix: new FIX-XXX number, tests added, committed

6.3  REVERT CONFIG FOR PAPER TRADING:
     - Put back TEMP values for paper trading phase
     - OR: if crash test proved production values are safe, keep them

6.4  MEMPALACE UPDATE:
     VS Code Claude must store:
     - crash_test_master_results: summary of all 119 scenarios
     - crash_test_critical_findings: any FAIL items
     - crash_test_config_changes: what was changed and reverted
     - crash_test_infrastructure: test scripts location and usage

6.5  TRANSITION TO PAPER TRADING:
     - Enable Chartink subscription (real signals)
     - Keep all 13 strategies active
     - Paper trade for 2 weeks (Jun 15 - Jun 26)
     - Daily review via existing EOD reports

================================================================================
SCENARIO COUNT SUMMARY
================================================================================

| Day | Theme                        | Scenarios |
|-----|------------------------------|-----------|
| 1   | Signal & Webhook Layer       | 26        |
| 2   | Screening, Capital & Orders  | 25        |
| 3   | Kill Switch & Circuit Breakers| 25        |
| 4   | Infrastructure Stress        | 28        |
| 5   | Chaos Day                    | 15        |
|     |                              |           |
| **TOTAL** |                        | **119**   |

================================================================================
END OF MASTER CRASH TEST PLAN
================================================================================
Prepared: 04-Jun-2026 | Author: Web Claude (Opus 4.6)
Next step: Split into 5 day-specific files on Monday (after quota reset)
VS Code Claude model: Opus 4.6 for all 5 days
================================================================================
