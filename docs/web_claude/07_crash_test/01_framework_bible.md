================================================================================
TRADING SYSTEM v2 — CRASH TEST FRAMEWORK BIBLE
================================================================================
Date: 04-Jun-2026 (IST)
Version: 2.0 (merged Web Claude plan + Rama's certification framework)
Status: AUTHORITATIVE REFERENCE — VS Code Claude reads this EVERY session
================================================================================

VS CODE CLAUDE INSTRUCTIONS:
  Model: Opus 4.6 for ALL crash test work
  1. Read this file FIRST at every session start
  2. Build all tools in PART 2 before any testing
  3. Check ALL global invariants after EVERY scenario
  4. Use the standard schema for every result
  5. Store daily results + progress in mempalace
  STANDING RULES: Permanent fixes only. Paper + Live parity always.

================================================================================
PART 1 — GLOBAL INVARIANTS (CHECKED AFTER EVERY SINGLE SCENARIO)
================================================================================

After EVERY test scenario completes (pass or fail), run the invariant
checker. ANY invariant violation = CRITICAL finding regardless of what
the scenario was testing.

INVARIANT A — CAPITAL
  available + reserved + used == total
  Source: FundManager state + fm_ledger replay
  Check: Both in-memory AND reconstructed from DB must match

INVARIANT B — SIGNAL ACCOUNTING
  signals_received == traded + rejected_* + expired + duplicate + queued + failed + in_process
  Source: signals table GROUP BY status
  Check: No unaccounted signals (SUM of all statuses == total rows)

INVARIANT C — ORDER PARITY
  system_open_orders == broker_open_orders
  Source: orders table (status IN OPEN,SUBMITTED,TRIGGER_PENDING) vs broker.get_open_orders()
  Check: Count match AND order_id match
  NOTE: Paper mode — broker is simulated, so check paper adapter's internal state

INVARIANT D — POSITION PARITY
  system_open_positions == broker_open_positions
  Source: trades table (status=OPEN) vs broker.get_positions()
  Check: Symbol + qty + direction match
  NOTE: Paper mode — check _paper_positions dict in zerodha_adapter

INVARIANT E — STATE INTEGRITY
  No illegal state transitions exist in DB history
  Check: For each order/trade/signal — verify state sequence is legal
  Illegal examples:
    Order:  COMPLETE → SUBMITTED, CANCELLED → COMPLETE
    Trade:  CLOSED → OPEN, FAILED → OPEN
    Signal: TRADED → REJECTED, DUPLICATE → ACCEPTED

INVARIANT F — AUDIT TRAIL
  Every OPEN or CLOSED trade has complete chain:
    signal → screener_result → fm_ledger(RESERVE) → order(ENTRY) →
    order(SL) → order(TGT) → fm_ledger(COMMIT) → [exit] →
    fm_ledger(RELEASE_USED)
  Check: JOIN across tables, flag any broken chain

INVARIANT G — NO ORPHAN RESOURCES
  - No orphan reservations (fm_ledger RESERVE without matching COMMIT or RELEASE)
    older than 10 minutes
  - No orphan orders (orders in OPEN/SUBMITTED without matching trade)
  - No stuck IN_PROCESS symbols (in_flight dict entries older than 60s)
  - No zombie threads (all named threads alive and responsive)
  Check: Query each, flag any orphans

INVARIANT H — RECOVERY INTEGRITY
  After any restart: system can prove exactly:
    - What happened (committed to DB)
    - What did NOT happen (in-memory lost, logged)
    - What remains uncertain (flag for reconciliation)
  Check: Verify reconcile_once() runs on restart, logs clear

--- IMPLEMENTATION ---
Build as: tests/crash_test/invariant_checker.py
  CLI: python invariant_checker.py --full
  CLI: python invariant_checker.py --quick (capital + orders only, for speed)
  Output: JSON with per-invariant PASS/FAIL + details
  Auto-run: scenario_runner calls this after every scenario

================================================================================
PART 2 — TEST INFRASTRUCTURE (BUILD BEFORE DAY 1)
================================================================================

All tools under: tests/crash_test/
All results under: reports/crash_test/
All scenario definitions: tests/crash_test/scenarios/

--- TOOL 1: INVARIANT CHECKER ---
File: invariant_checker.py
  Checks all 8 invariants (A through H)
  Returns: {invariant: {status, details, timestamp}}
  Exit code: 0 if all pass, 1 if any fail

--- TOOL 2: SIGNAL INJECTOR ---
File: signal_injector.py
  Sends POST to localhost:5000/webhook/<scanner_name>
  Modes:
    --mode single --symbol RELIANCE --scanner gap_fade_short --price 2500
    --mode burst --count 10 --delay-ms 200 --symbols "RELIANCE,TCS,INFY,..."
    --mode flood --count 300 (fills queue to backpressure)
    --mode malformed --type missing_fields|wrong_types|oversized|extra_fields
    --mode expired --symbol RELIANCE --age-seconds 700
    --mode duplicate --symbol RELIANCE --count 3 --delay-seconds 2
  Payload: exact Chartink format from scan_webhook_map.yaml
  Logs: HTTP status, response body, latency → reports/crash_test/injector_log.jsonl
  Price source: current LTP from Zerodha quote API (or --price override)

--- TOOL 3: STATE INSPECTOR ---
File: state_inspector.py
  Captures full system state snapshot:
    - Capital: available, reserved, used, total (in-memory + DB)
    - Open trades: count, symbols, directions, PnL
    - Open orders: count, types, statuses
    - Kill switch: state, reason, timestamp
    - Signal queue: depth (from /health endpoint)
    - fm_ledger: last 10 entries
    - system_events: last 5 entries
    - in_flight: current dict contents
    - Thread status: all named threads alive/dead
  Commands:
    --snapshot <label>     → saves to reports/crash_test/snapshots/<label>.json
    --diff <before> <after> → compares two snapshots, outputs diff
    --live                 → prints current state to stdout

--- TOOL 4: NETWORK CONTROLLER ---
File: network_controller.py (requires sudo)
  Presets:
    --preset block_zerodha_rest    (blocks api.kite.trade)
    --preset block_zerodha_ws      (blocks wss.kite.trade)
    --preset block_zerodha_all     (blocks both)
    --preset block_telegram        (blocks api.telegram.org)
    --preset block_all_outbound    (blocks all OUTPUT)
    --preset block_dns             (blocks UDP 53)
  Options:
    --duration <seconds>           (auto-unblock after N seconds)
    --latency <ms>                 (add latency via tc netem instead of block)
  Safety:
    - Max duration: 300s (hard cap, auto-unblock)
    - Never blocks SSH (port 22 always exempt)
    - Log file: reports/crash_test/network_log.jsonl

--- TOOL 5: SCENARIO RUNNER ---
File: scenario_runner.py
  Reads YAML scenario definitions
  Flow per scenario:
    1. Print scenario header (id, title, objective)
    2. Run precondition checks
    3. Snapshot BEFORE
    4. Execute test steps (calls injector, network_controller, etc.)
    5. Wait observation period
    6. Snapshot AFTER
    7. Run assertions (compare before/after + specific checks)
    8. Run invariant_checker (ALL invariants)
    9. Classify result (7-tier)
    10. Write result to reports/crash_test/results/<scenario_id>.json
    11. Run cleanup if specified
  Commands:
    --scenario CT001
    --day 1 (runs all scenarios for day 1)
    --category 1A (runs all in category 1A)
    --dry-run (prints plan without executing)
  Supports:
    --pause (wait for Enter between steps, for timing-critical tests)
    --mid-trade (sets up active position before running scenario)

--- TOOL 6: CRASH TEST REPORTER ---
File: crash_test_reporter.py
  Aggregates results for a day:
    Input: reports/crash_test/results/CT*.json
    Output: reports/crash_test/day{N}_report_YYYY-MM-DD.md
  Sections:
    - Summary table (scenario | result | classification | notes)
    - Invariant violations (if any)
    - Critical findings
    - Known limitations documented
    - Recommendations
  Also:
    - Sends Telegram summary
    - Updates master tracker: reports/crash_test/master_tracker.json
  Command: --day 1 | --day all

--- TOOL 7: CLEANUP SCRIPT ---
File: cleanup.py
  Resets system to clean state between scenarios:
    --soft: clear today's signals, reset kill switch, release stuck reservations
    --hard: above + close all trades, cancel all orders, reset capital
    --nuclear: above + restart trading-system service
  Always:
    - Verifies invariant A (capital) after cleanup
    - Logs cleanup action

--- TOOL 8: IDEMPOTENCY TESTER ---
File: idempotency_tester.py
  Runs any scenario N times (1, 2, 10, 100)
  Compares state after each run — must be identical
  Command: --scenario CT001 --runs 10
  Output: IDEMPOTENT or DRIFT_DETECTED with diff

--- TOOL 9: EXACTLY-ONCE VERIFIER ---
File: exactly_once_verifier.py
  For a given time window, verifies:
    - Each signal processed exactly once
    - Each capital reserve/commit/release exactly once
    - Each order placed exactly once
    - Each SL/TGT placed exactly once
    - Each Telegram alert sent exactly once (per trigger)
  Source: DB tables + telegram_alerts + fm_ledger
  Command: --window "09:30-10:00" | --scenario CT015

--- TOOL 10: STATE MACHINE VALIDATOR ---
File: state_machine_validator.py
  Reads all orders, trades, signals from DB
  Validates every state transition is legal
  Reports illegal transitions with row IDs
  Command: --table orders | --table trades | --table signals | --all

--- TOOL 11: FORENSIC RECONSTRUCTOR ---
File: forensic_reconstructor.py
  Reconstructs trade lifecycle from single source:
    --source db      (reconstruct from DB only)
    --source logs    (reconstruct from log files only)
    --source broker  (reconstruct from broker API only)
  Compares reconstruction vs actual recorded state
  Flags gaps or mismatches
  Command: --trade-id T123 --source db

--- TOOL 12: RESOURCE MONITOR ---
File: resource_monitor.py
  Continuous monitoring during test runs:
    - CPU%, RAM%, disk%, swap%
    - Thread count, FD count, socket count
    - SQLite connection count
    - Signal queue depth (via /health)
    - Per-metric growth rate (leak detection)
  Runs as background daemon during test day
  Output: reports/crash_test/resources/day{N}_resources.csv (1-second samples)
  Alerts: if memory growth > 10% over baseline, thread count increases, FD leak
  Command: --start | --stop | --report

================================================================================
PART 3 — TEST RESULT CLASSIFICATION (7-TIER)
================================================================================

PASS
  System behaved exactly as designed. All invariants hold.

PASS_WITH_RISK
  System handled the scenario but exposed a risk that
  should be addressed before live trading.
  Example: EOD squareoff failure leaves position open — system doesn't crash
  but capital is at risk overnight.

FAIL
  System violated an invariant, crashed unexpectedly, lost data,
  or produced incorrect state.
  Action: MUST FIX before paper trading.

KNOWN_LIMITATION
  System doesn't handle this scenario but it's documented and
  the risk is accepted.
  Example: Chartink signals lost on crash (in-memory queue).
  Action: Document in operational runbook.

DESIGN_GAP
  System has no mechanism to handle this scenario. Needs design work.
  Example: No corporate action handling.
  Action: Add to v2.1 backlog with priority.

CANNOT_TEST
  Cannot be tested in current environment (paper mode, no broker,
  Oracle Cloud limitation, etc.)
  Action: Document what would need to change to test it.
  Example: Partial fills not simulatable in paper mode.

NOT_APPLICABLE
  Scenario doesn't apply to current system configuration.
  Example: CNC position handling when system only trades INTRADAY.

================================================================================
PART 4 — STANDARD SCENARIO SCHEMA
================================================================================

Every scenario in YAML follows this EXACT format:

```yaml
scenario_id: CT001
title: "Single Valid Signal — Happy Path"
category: "1A_HAPPY_PATH"
day: 1
priority: P0   # P0=critical, P1=important, P2=nice-to-have

environment:
  mode: paper
  market_hours: required  # required | not_required | post_market
  positions_needed: 0     # how many open positions before test
  kill_switch: clear      # clear | soft_kill | hard_kill

objective: "Verify single valid signal flows through entire pipeline"

preconditions:
  - "System running and healthy"
  - "No open positions"
  - "Kill switch clear"
  - "Zerodha API accessible"

test_steps:
  - step: 1
    action: "snapshot_before"
  - step: 2
    action: "inject_signal"
    params: {symbol: RELIANCE, scanner: gap_fade_short, price: auto}
  - step: 3
    action: "wait"
    params: {seconds: 30}
  - step: 4
    action: "snapshot_after"

expected_behavior:
  - "Signal accepted at webhook (HTTP 200)"
  - "Pipeline processes signal through all 12 steps"
  - "If score passes: order placed, trade opened"
  - "If score fails: rejected with reason"
  - "Telegram notification sent"

assertions:
  - "signals table: new row with status IN (TRADED, REJECTED_*)"
  - "No status stuck as ACCEPTED after 30s"
  - "fm_ledger: balanced (if TRADED: RESERVE+COMMIT exist)"
  - "invariant_checker --full: ALL PASS"

recovery_requirements: "None — happy path"

failure_impact:
  technical: "Signal processing pipeline broken"
  business: "Cannot trade — system useless"

pass_criteria: "Signal processed end-to-end, all invariants hold"

post_cleanup:
  - "cleanup.py --soft"

notes: ""
```

================================================================================
PART 5 — PHASE OVERVIEW
================================================================================

PHASE 1: CRASH TEST (Jun 8-12, market hours)
  5 days, ~200 scenarios
  Zerodha API active, Chartink NOT subscribed (injected signals)
  TEMP config reverted to production values
  Goal: Prove system is SAFE, DETERMINISTIC, RECOVERABLE

  Day 1 (Mon): Component Integrity + Signal Layer
    - File-level responsibility verification
    - Function-level edge cases (None/NaN/Infinity/negative/wrong types)
    - State machine validation
    - Signal ingestion: happy path, rejections, malformed, stress, timing
    - Idempotency proof for signal processing

  Day 2 (Tue): Business Logic + Capital + Orders
    - Screening edge cases
    - Capital management: allocation, concurrency, invariant, exhaustion
    - Order lifecycle: placement, fill, SL/TGT, orphan, reconciliation
    - Exactly-once verification
    - Position sizing edge cases

  Day 3 (Wed): Safety Systems + Kill Switches
    - Every soft kill trigger
    - Every hard kill trigger
    - Kill switch persistence, recovery, stale clear
    - Every circuit breaker at production thresholds
    - EOD squareoff: happy path + failure
    - Mid-trade kill switch scenarios
    - Sequential kill switch marathon

  Day 4 (Thu): Infrastructure + Recovery
    - SIGINT/SIGKILL in every system state (idle, mid-trade, mid-order)
    - Restart recovery: cold/warm/crash/halt
    - Rehydration with dirty state
    - Network: 30s/5min, partial (WS only, REST only)
    - Disk: full, permissions, read-only
    - DB: lock contention, WAL recovery, corruption
    - CPU/RAM/FD stress
    - Clock skew, token expiry
    - Cron failures
    - Oracle Cloud events (if testable)
    - Systemd restart loop protection
    - Human error: delete config, wrong YAML, wrong token

  Day 5 (Fri): Chaos + Certification
    - Multi-failure combinations (flood+network, kill+EOD, crash+flood)
    - Full day trading simulation (CT112)
    - Maximum dirty state SIGKILL recovery
    - Rapid crash cycles
    - Kill switch marathon
    - Market anomalies (circuit limits, gap risk)
    - AGY governance verification
    - GOLD STANDARD SCENARIO (production certification)
    - Forensic reconstruction test

PHASE 2: SOAK TESTS (Jun 15-26, during paper trading)
  Runs alongside real paper trading with Chartink signals
  Resource monitor running continuously
  Goal: Prove system is STABLE over time

  - 1-day stability baseline (Day 1 of paper)
  - 3-day memory/thread/FD growth check
  - 7-day endurance checkpoint
  - 14-day full soak completion
  - Zombie thread detection (per component)
  - Silent failure detection (hung subsystems)
  - DB growth rate tracking
  - Log growth rate tracking
  - Query performance baseline

PHASE 3: PRE-LIVE CERTIFICATION (weekend before live)
  Goal: SIGN-OFF for live trading

  - Gold Standard re-run with real market data
  - Full forensic reconstruction of paper trading period
  - PnL reconciliation: DB vs broker vs reports
  - Retention test (all paper data queryable)
  - SPOF review: every dependency documented (continue/degrade/pause/kill/stop)
  - Final invariant check
  - Operational runbook verified
  - TEMP values: decide keep production or adjust

================================================================================
PART 6 — PREREQUISITES CHECKLIST (Complete before Day 1 09:15 IST)
================================================================================

[ ] 1. Revert all 7 TEMP config values to production (see values below)
       capital.daily_loss_limit:               100,000 → 10,000
       risk.max_consecutive_losses:            20 → 4
       risk.daily_loss_limit_pct:              1.00 → 0.05
       order_reconciler.capital_drift_tolerance: 100,000 → 50
       drift_handler.log_only_threshold_rs:    100,000 → 250
       drift_handler.soft_kill_threshold_rs:   200,000 → 1,000
       drift_handler.hard_kill_threshold_rs:   500,000 → 2,500

[ ] 2. Commit + deploy config changes to VM

[ ] 3. Zerodha API: fresh token on Monday morning

[ ] 4. Verify system starts clean:
       curl http://localhost:5000/health
       state_inspector.py --live (all clean)
       invariant_checker.py --full (all PASS)

[ ] 5. Resolve 13 UNCERTAIN items from info gathering (run VM commands)

[ ] 6. Build all 12 tools (PART 2) + verify each works

[ ] 7. Create scenario YAML files for Day 1

[ ] 8. Full DB backup: cp trading_system.db trading_system_pre_crash_test.db

[ ] 9. Config backup: cp -r config/ config_pre_crash_test/

[ ] 10. Start resource_monitor.py --start

[ ] 11. Verify Telegram bot responds (send test message)

[ ] 12. Verify all 13 strategies active in config

[ ] 13. Store in mempalace: "crash_test_phase1_started_08jun2026"

================================================================================
PART 7 — EXACTLY-ONCE PROCESSING RULES
================================================================================

The following operations MUST be exactly-once. The exactly_once_verifier
checks all of these after relevant scenarios:

  1. Signal intake (same fingerprint → one signal row)
  2. Capital reserve (one RESERVE per signal_id)
  3. Capital commit (one COMMIT per reservation_id)
  4. Capital release (one RELEASE per reservation_id)
  5. Order placement (one place_order call per leg)
  6. Order cancellation (one cancel per order_id)
  7. SL placement (one SL per trade)
  8. TGT placement (one TGT per trade)
  9. Telegram alert (one per trigger event, deduplicated)
  10. Report generation (one daily report per date)
  11. Reconciliation adjustment (one adjustment per mismatch)
  12. EOD squareoff (one MARKET exit per open position)

================================================================================
PART 8 — SILENT FAILURE DEFINITIONS
================================================================================

A subsystem is FAILED (even if process alive) if:

  - Alive but no heartbeat for > 2x expected interval
  - Queue depth growing but no processing (stuck)
  - No progress (same state for > 5 minutes during market hours)
  - Deadlocked (thread alive but blocked indefinitely)
  - Infinite retry loop (same error > 10 times in 1 minute)
  - Stale timestamps (last action > 5 minutes ago during market)

Required response (system should do automatically):
  1. Log WARNING with subsystem name
  2. Telegram alert
  3. If critical subsystem (order_monitor, reconciler): SOFT_KILL
  4. If non-critical (telegram, cron): continue with degraded mode

================================================================================
PART 9 — RECOVERY VALIDATION FRAMEWORK
================================================================================

After ANY restart, system must establish and log:

  LAST_KNOWN_SAFE_POINT:
    - Last committed DB transaction timestamp
    - Last fm_ledger entry
    - Last system_event

  LAST_COMMITTED_ACTION:
    - Last order placed/cancelled (from orders table)
    - Last trade state change (from trades table)

  LAST_BROKER_STATE:
    - Current broker positions (reconcile_once)
    - Current broker orders (order_monitor rehydrate)

  LAST_CAPITAL_STATE:
    - Rebuilt from fm_ledger replay

  UNCERTAINTY_LOG:
    - Signals in queue at crash: LOST (count estimated from signals table gap)
    - In-flight symbols at crash: CLEARED (logged)

  If ANY uncertainty exists → RECONCILE, NEVER ASSUME.

================================================================================
PART 10 — DEPENDENCY FAILURE MATRIX
================================================================================

| Dependency | Down Behavior | Classification |
|------------|---------------|----------------|
| Zerodha REST API | Screening fails, orders fail, 3-strike kill | DEGRADE → PAUSE |
| Zerodha WebSocket | Candle data stops, SL trail pauses | DEGRADE |
| Zerodha token | Auth error, soft kill | PAUSE |
| Chartink webhooks | No signals arrive (external) | DEGRADE (no action) |
| Telegram | Alerts queued, email fallback | DEGRADE |
| AGY/Antigravity | No watchman, no reports | CONTINUE |
| Cron | Jobs missed, detected at 18:00 | CONTINUE |
| SQLite DB | Fatal — all operations fail | STOP |
| Internet (full) | All external calls fail | PAUSE → KILL |
| Oracle VM | System dead | STOP |
| NTP | Clock skew detected | PAUSE |
| Disk space | Startup blocks if < 2GB | STOP |

================================================================================
PART 11 — AUDIT TRAIL REQUIREMENTS
================================================================================

Every trade must be fully reconstructable from DB:

  Signal → signal_id, scanner, symbol, price, triggered_at, fingerprint
  Screen → screener_results: steps, scores, eligible_score, tier
  Risk → risk engine approval (logged in signal pipeline)
  Capital → fm_ledger: RESERVE(amount, bucket) → COMMIT → RELEASE_USED(pnl)
  Order → orders: entry, SL, TGT — each with broker_order_id, timestamps
  Fill → orders: fill_price, fill_qty, fill_timestamp
  Protection → smart_tgt_state: SL modifications with timestamps
  Exit → trades: exit_reason, exit_price, exit_timestamp
  PnL → trades: gross_pnl, net_pnl, costs breakdown
  Report → daily report Excel: trade row matches DB
  Alert → telegram_alerts: message, sent_at, status

  forensic_reconstructor.py verifies this chain for any trade.

================================================================================
END OF FRAMEWORK BIBLE
================================================================================
