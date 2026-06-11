================================================================================
TRADING SYSTEM v2 — CRASH TEST SCENARIO CATALOG
===

Date: 04-Jun-2026 | Ref: 01\_framework\_bible.md
Total: \~210 scenarios (Phase 1: \~185, Phase 2: \~15, Phase 3: \~10)
===

FORMAT KEY (compact — every scenario follows this structure):
ID | Title | Priority
Setup: what state the system needs before this test
Steps: numbered actions
Assert: what must be true after
Invariants: which global invariants are especially relevant (ALL checked anyway)
Cleanup: how to reset after
Mid-trade: Y/N — does this have a mid-trade variant?
Notes: special considerations

================================================================================
DAY 1 (Mon Jun 8): COMPONENT INTEGRITY + SIGNAL LAYER
===

Session start: VS Code Claude reads 01\_framework\_bible.md + this file Day 1 section
Build: All 12 tools from framework PART 2 (if not already built)
Verify: Prerequisites checklist (framework PART 6)
Time: 09:30 — 15:30 IST

────────────────────────────────────────
CATEGORY 1A — COMPONENT INTEGRITY
────────────────────────────────────────
These are system-wide structural tests, run ONCE at start of Day 1.

CT001 | State Machine Validation — All Tables | P0
Setup: System running, has historical data from prior sessions
Steps:
1. Run state\_machine\_validator.py --all
2. Check orders, trades, signals tables for illegal transitions
Assert:
- Zero illegal transitions in entire DB history
- Every order follows: PENDING→SUBMITTED→COMPLETE|CANCELLED|REJECTED
- Every trade follows: PENDING\_FILL→OPEN→CLOSED|CANCELLED|FAILED
- Every signal has valid terminal status
Cleanup: None (read-only)

CT002 | Idempotency — System Restart 5x | P0
Setup: System running idle, clean state
Steps:
1. Snapshot state
2. Restart system 5 times (systemctl restart × 5, 30s apart)
3. Snapshot state after each restart
Assert:
- All 5 snapshots identical (capital, positions, orders, kill switch)
- No phantom signals, no duplicate fm\_ledger entries
- No thread count growth
Cleanup: None

CT003 | File-Level Responsibility Audit | P1
Setup: None (static analysis)
Steps:
1. For each core .py file (main.py, signal\_processor.py, fund\_manager.py,
order\_placer.py, order\_monitor.py, webhook\_receiver.py, zerodha\_adapter.py,
kill\_switch.py, secondary\_screener.py, eod\_squareoff.py, smart\_tgt\_manager.py,
entry\_gate.py, order\_reconciler.py, state\_store.py, position\_sizer.py,
risk\_engine.py):
- Document: responsibilities, dependencies, inputs, outputs, failure modes
2. Verify no circular dependencies
3. Verify each has error handling (no bare except:)
Assert:
- All core files documented
- No circular deps
- No bare except: statements
Output: reports/crash\_test/file\_audit.md

CT004 | Function Edge Cases — Fund Manager | P0
Setup: System running
Steps:
Test FundManager methods with:
1. reserve(amount=0), reserve(amount=-100), reserve(amount=NaN)
2. reserve(amount=999999999) (exceeds total)
3. release(reservation\_id="nonexistent")
4. release(reservation\_id=None)
5. commit(reservation\_id already committed)
Assert:
- Each: ValueError or appropriate exception raised
- No crash, no state corruption
- Invariant A holds after each attempt

CT005 | Function Edge Cases — Position Sizer | P0
Setup: System running
Steps:
Test PositionSizer.compute() with:
1. entry\_price=0, sl\_price=0
2. entry\_price=NaN
3. sl\_price > entry\_price (for LONG) — inverted SL
4. entry\_price=999999 (huge price, tiny qty)
5. capital=0
6. risk\_per\_trade=0
Assert:
- Each: returns qty=0 or raises exception
- No division-by-zero
- No negative qty

CT006 | Function Edge Cases — Webhook Receiver | P0
Setup: System running
Steps:
POST to /webhook/gap\_fade\_short with:
1. Body: None
2. Body: ""
3. Body: "not json"
4. Body: \[] (array, not object)
5. Body: {"stocks": null}
6. Body: {"stocks": \[{"symbol": null, "price": null}]}
7. Content-Type: text/plain (instead of application/json)
Assert:
- Each: HTTP 400 or graceful rejection
- No unhandled exception in logs
- System continues working after all 7

CT007 | Function Edge Cases — Kill Switch | P1
Steps:
1. soft\_kill() when already soft killed
2. hard\_kill() when already hard killed
3. resume() when not killed
4. clear\_stale\_state(date=None)
5. is\_active("nonexistent\_scope")
Assert:
- Each: no crash, idempotent behavior
- State consistent after each

CT008 | Exactly-Once Baseline | P0
Setup: Clean state
Steps:
1. Inject 5 signals, wait for processing
2. Run exactly\_once\_verifier.py
Assert:
- Each signal: exactly 1 row in signals table
- Each traded signal: exactly 1 RESERVE, 1 COMMIT in fm\_ledger
- Each order: placed exactly once
- Each Telegram: sent exactly once per trigger
Cleanup: cleanup.py --soft

────────────────────────────────────────
CATEGORY 1B — SIGNAL HAPPY PATH
────────────────────────────────────────

CT009 | Single Valid Signal | P0
Setup: Clean, system running
Steps: Inject 1 signal (RELIANCE, gap\_fade\_short, price=auto). Wait 30s.
Assert: Signal in DB with terminal status. If TRADED: trade+orders+fm\_ledger exist.
Cleanup: --soft

CT010 | 5 Different Symbols, 5 Different Strategies | P0
Setup: Clean
Steps: Inject RELIANCE/TCS/INFY/HDFCBANK/ICICIBANK to 5 different scanners, 2s apart. Wait 90s.
Assert: All 5 in signals table, each processed, capital correct, no double-reserve.
Invariants: A, B, F especially

CT011 | Same Symbol, Different Strategies | P1
Steps: Inject RELIANCE to gap\_fade\_short, wait 1s, inject RELIANCE to gap\_fade\_long.
Assert: IN\_PROCESS serialization works. Both process sequentially. No race.

────────────────────────────────────────
CATEGORY 1C — SIGNAL REJECTION PATHS
────────────────────────────────────────

CT012 | Duplicate (within 5-min dedup) | P0
Steps: Inject signal. Wait 5s. Inject identical. Assert: second=DUPLICATE.

CT013 | Expired (triggered\_at 700s ago) | P0
Steps: Inject with --age-seconds 700. Assert: EXPIRED.

CT014 | Outside Market Hours | P1
Steps: After 15:20 IST, inject valid signal. Assert: OUTSIDE\_HOURS.

CT015 | Invalid Symbol | P0
Steps: Inject symbol=FAKESYMBOL123. Assert: rejected (INVALID\_SYMBOL or pipeline reject).

CT016 | Invalid Scanner | P1
Steps: POST to /webhook/nonexistent\_scanner. Assert: HTTP 404 or reject.

CT017 | Kill Switch Active | P0
Setup: Activate soft\_kill manually. Steps: Inject valid signal.
Assert: Rejected with KILL\_SWITCH. Cleanup: clear kill switch.

CT018 | IN\_PROCESS Symbol | P1
Steps: Inject slow-processing signal for RELIANCE. While processing: inject second RELIANCE.
Assert: Second gets IN\_PROCESS. First completes. Second released by sweeper or processes after.

────────────────────────────────────────
CATEGORY 1D — MALFORMED PAYLOADS
────────────────────────────────────────

CT019 | Missing Required Fields | P0
Steps: 4 malformed payloads (empty body, missing stocks, missing symbol, missing price)
Assert: Each rejected gracefully. No crash. System continues.

CT020 | Wrong Data Types | P0
Steps: price="text", price=-100, price=0, symbol=12345
Assert: Each rejected. No exception in main thread.

CT021 | Oversized Payload | P1
Steps: 1000 symbols in stocks array. 10MB body.
Assert: Waitress handles. No OOM. No crash.

CT022 | Extra/Unknown Fields | P2
Steps: Valid signal + extra junk fields. Assert: Accepted (extra ignored).

────────────────────────────────────────
CATEGORY 1E — STRESS \& BACKPRESSURE
────────────────────────────────────────

CT023 | Burst — 10 Signals in 2s | P0
Steps: Inject 10 unique symbols within 2s. Wait 120s.
Assert: All 10 accepted, all processed, capital correct, exactly-once holds.

CT024 | Burst — 50 Signals in 5s | P1
Steps: Inject 50 unique symbols within 5s.
Assert: Queue handles, X-Queue-Warning at 60%, capital exhaustion graceful.

CT025 | Queue Full — 300+ Signals (Backpressure) | P0
Steps: Inject 310 as fast as possible.
Assert: First \~300 accepted. Beyond 300: HTTP 503. System continues after flood.

CT026 | Sustained Load — 1/5s for 10 min (120 signals) | P1
Steps: Steady drip injection.
Assert: Queue depth stays low, no memory growth, all get terminal status.

CT027 | Idempotency Under Load | P0
Steps: Inject 20 signals. Wait for all to complete. Inject same 20 again.
Assert: All 20 duplicates get DUPLICATE status. No double processing.
Invariants: B, F

────────────────────────────────────────
CATEGORY 1F — TIMING VARIANTS
────────────────────────────────────────

CT028 | Signal at 09:16 (before entry window 09:25) | P1
Assert: Rejected by market\_windows check.

CT029 | Signal at 14:46 (in entry window, near EOD pre-alert) | P1
Assert: Accepted (entry window: 09:25-15:00). EOD alert independent.

CT030 | Signal at 15:14 (just before force\_close 15:15) | P0
Assert: Race with force\_close handled safely. Signal rejected or abandoned.

CT031 | Signal at 15:17 (during EOD squareoff) | P1
Assert: Rejected (kill switch active from 15:15).

────────────────────────────────────────
CATEGORY 1G — MID-TRADE SIGNAL VARIANTS
────────────────────────────────────────

CT032 | Signal While Position Open for Same Symbol | P0
Mid-trade: Y
Setup: Open RELIANCE position. Steps: Inject RELIANCE to different strategy.
Assert: Risk engine checks exposure. Correctly allowed or rejected.

CT033 | 3 Same-Symbol Signals Within 100ms | P0
Steps: Inject RELIANCE to 3 scanners simultaneously.
Assert: Only 1 processes at a time. No deadlock.

CT034 | Chartink Retry Simulation (503 → wait → retry) | P1
Steps: Fill queue to 300. Send 1 more (503). Wait 30s. Resend.
Assert: Retry accepted or DUPLICATE.

────────────────────────────────────────
CATEGORY 1H — WEBHOOK SERVER RESILIENCE
────────────────────────────────────────

CT035 | Webhook Thread Crash → Recovery | P0
Steps: Send maximally malformed HTTP to crash Waitress thread.
Assert: systemd restarts main.py. Post-restart signal accepted. No state corruption.

CT036 | 100 Concurrent HTTP Connections | P1
Steps: Open 100 simultaneous connections to /webhook/gap\_fade\_short.
Assert: connection\_limit=100 enforced. No crash. Excess connections refused.

\--- DAY 1 TOTAL: 36 scenarios ---
--- EOD: Run crash\_test\_reporter.py --day 1. Cleanup. Mempalace update. ---

================================================================================
DAY 2 (Tue Jun 9): BUSINESS LOGIC + CAPITAL + ORDERS
===

Session start: Read framework\_bible.txt + Day 2 section
Time: 09:30 — 15:30 IST

────────────────────────────────────────
CATEGORY 2A — SECONDARY SCREENING
────────────────────────────────────────

CT037 | Screening Happy Path | P0
Steps: Inject liquid stock. Wait for screening.
Assert: screener\_results row with score. Pipeline continues or rejects based on threshold.

CT038 | Zerodha Quote Timeout During Screening | P0
Steps: Block REST 15s → inject signal → wait 30s → unblock.
Assert: Signal REJECTED (timeout). Capital NOT reserved. System continues after unblock.

CT039 | Zerodha Returns Price=0 or Stale | P0
Steps: Inject for illiquid stock where LTP=0 possible.
Assert: No division-by-zero. No order with price=0.

CT040 | Pipeline Timeout (>30s screening) | P1
Steps: Block REST partially (slow, not dead) → signal hits pipeline\_timeout\_sec.
Assert: REJECTED/FAILED. Worker thread freed. No resource leak.

CT041 | Score at Exact Threshold | P1
Steps: Inject stock that scores exactly at min\_eligible\_score.
Assert: Document whether >= or >. Boundary behavior correct.

CT042 | Screening Edge — Missing Candles from Kite | P1
Steps: Inject for stock with no recent candle data (just listed, or illiquid).
Assert: Screening handles gracefully (reject, not crash).

CT043 | Screening Edge — Corporate Action Data (Bad OHLC) | P2
Steps: Inject for stock that recently had a split (OHLC may have gaps).
Assert: ATR/TA calculations don't produce garbage. Graceful reject if data invalid.

────────────────────────────────────────
CATEGORY 2B — CAPITAL \& FUND MANAGEMENT
────────────────────────────────────────

CT044 | Single Allocation Happy Path | P0
Steps: Clean state → inject signal → reaches reserve().
Assert: fm\_ledger RESERVE, available decreased, invariant A holds.

CT045 | 5 Concurrent Allocations | P0
Steps: Inject 5 signals within 200ms. Wait 60s.
Assert: 5 RESERVE entries (or fewer if exhausted), unique reservation\_ids, no overlap.
Invariants: A, G

CT046 | Capital Exhaustion | P0
Steps: Inject until available < min position size. Inject 1 more.
Assert: REJECTED\_CAPITAL\_INSUFFICIENT. No capital leak. Invariant A.

CT047 | Capital Invariant Violation (Forced) | P0
Steps: Corrupt DB (UPDATE capital\_snapshot). Inject signal.
Assert: HARD\_KILL fires. Telegram alert. No order placed.
Cleanup: Restore DB, clear kill switch, --resume.

CT048 | Double Release | P0
Steps: Complete a trade. Call release\_used() again with same reservation\_id.
Assert: ValueError. Capital unchanged. Invariant A.

CT049 | Reserve Then Cancel (Pipeline Abort) | P0
Steps: Signal passes screening, reserve() called, then kill switch before order.
Assert: release() called. fm\_ledger: RESERVE → RELEASE. Capital restored.

CT050 | Position Sizing Edges | P0
Steps: 4 variants:
a) Very wide SL (tiny qty)
b) Very tight SL (high qty, capped by concentration)
c) SL so wide → qty rounds to 0
d) Stock with lot\_size > 1 (lot rounding)
Assert: Each returns valid qty or 0. max\_position\_value\_rs enforced.

CT051 | Concurrent Reserve + Release | P1
Steps: Open 2 positions. While releasing one (SL hit): inject new signal (reserve).
Assert: RLock handles. Both operations complete. Invariant A.

────────────────────────────────────────
CATEGORY 2C — ORDER LIFECYCLE
────────────────────────────────────────

CT052 | Order Placement → Fill (Paper) | P0
Steps: Inject signal → OrderPlacer → paper fill daemon.
Assert: Entry SUBMITTED→COMPLETE. SL+TGT placed. Trade OPEN. fm\_ledger RESERVE→COMMIT.

CT053 | Fill Timeout (60s, LTP never reaches) | P0
Steps: Inject with entry price far from LTP. Wait 70s.
Assert: Entry order CANCELLED. Capital RELEASED (not COMMITTED). No open position.

CT054 | SL Hit Lifecycle | P0
Mid-trade: Y
Steps: Open position → simulate SL hit (LTP crosses SL).
Assert: Trade CLOSED (SL\_HIT). TGT cancelled. Capital released with negative PnL.

CT055 | TGT Hit Lifecycle | P0
Mid-trade: Y
Steps: Open position → simulate TGT hit.
Assert: Trade CLOSED (TGT\_HIT). SL cancelled. Capital released with positive PnL.

CT056 | Partial Fill → Timeout | P1
Steps: If paper supports partial fills: simulate. If not: CANNOT\_TEST\_PAPER.
Assert: 5-min timeout. Partial qty closed. Capital adjusted.

CT057 | Orphan Order — SL Missing | P0
Mid-trade: Y
Steps: Open position → remove SL order from DB → wait for reconciler (15s).
Assert: SL\_MISSING detected. Emergency exit. SOFT\_KILL. Telegram alert.
Cleanup: Clear kill switch.

CT058 | MANUAL\_CLOSE Detection | P0
Steps: Open position → mark order COMPLETE in DB → wait for reconciler.
Assert: MANUAL\_CLOSE detected. Trade closed. Capital released. Siblings cancelled.

CT059 | SmartTgtManager — SL Trail Success | P1
Mid-trade: Y
Steps: Open LONG → price moves up past trigger\_pct → wait for candle close.
Assert: SL modified (cancel old + place new). smart\_tgt\_state updated.

CT060 | SmartTgtManager — Cancel OK, Replace FAIL (Naked Position) | P0
Mid-trade: Y
Steps: Open position with SL trail → trigger trail → block REST during replace.
Assert: Naked position detected by reconciler. Emergency exit. SOFT\_KILL.
Cleanup: Unblock, clear kill switch.
NOTE: CRITICAL — risk area #13.

CT061 | Broker Rejection — Insufficient Funds | P1
Steps: Mock adapter to return OrderRejectedError. Inject signal.
Assert: Signal REJECTED. Capital released. No position.

CT062 | Order Reconciliation — Capital Drift | P0
Steps: Create small capital drift (system vs calculated). Wait for reconciler.
Assert: Drift detected. If > drift\_tolerance (Rs 50 production): appropriate action.

────────────────────────────────────────
CATEGORY 2D — MID-TRADE ORDER SCENARIOS
────────────────────────────────────────

CT063 | Capital Mutation During Active Positions | P0
Mid-trade: Y
Setup: 2 open positions. Steps: Inject 3 more signals (burst).
Assert: Reserves against REMAINING available. Invariant A holds.

CT064 | Order + Reconciler Concurrent | P1
Steps: Time signal injection during reconciler's 15s poll.
Assert: No lock contention timeout. No DB deadlock.

CT065 | 3 Rapid SL Hits | P0
Mid-trade: Y
Setup: 3 positions. Steps: Simulate all 3 SL within 5s.
Assert: All 3 closed. 3 TGTs cancelled. Capital released 3x. No double-release.
If daily loss breached: SOFT\_KILL.

CT066 | Exactly-Once After Full Trading Cycle | P0
Steps: Run 5 signals → 3 trades → 1 SL → 1 TGT → 1 open.
Then: exactly\_once\_verifier.py
Assert: Every operation exactly once.

\--- DAY 2 TOTAL: 30 scenarios ---

================================================================================
DAY 3 (Wed Jun 10): SAFETY SYSTEMS + KILL SWITCHES
===

Time: 09:30 — 15:30 IST

────────────────────────────────────────
CATEGORY 3A — SOFT KILL TRIGGERS
────────────────────────────────────────

CT067 | Daily Loss Limit (Rs 10,000) | P0
Steps: Simulate losses totaling Rs 10,001 via multiple SL hits.
Assert: SOFT\_KILL. EOD squareoff triggered. Telegram. Signals rejected.

CT068 | Daily Loss % (5%) | P0
Steps: Losses = 5.1% of total capital.
Assert: SOFT\_KILL fires.

CT069 | 3 Consecutive API Failures | P0
Steps: Block REST → trigger 3 broker calls.
Assert: After 3: SOFT\_KILL. After 1-2: logged but continues.

CT070 | Clock Skew > 30s | P1
Steps: sudo date -s "+60 seconds". Check probe or startup.
Assert: SOFT\_KILL or startup failure.
NOTE: clock\_skew\_probe only in LIVE mode. Paper: test via startup check.

CT071 | WebSocket 10 Reconnect Failures | P1
Steps: Block wss.kite.trade. Wait through 10 reconnect attempts.
Assert: SOFT\_KILL after attempt 10.
NOTE: Only if live\_feed active in paper mode.

CT072 | Orphan Order → Soft Kill | P0
Steps: Same as CT057.
Assert: Orphan callback fires SOFT\_KILL.

CT073 | Token Expiry | P1
Steps: Delete/corrupt zerodha\_token.json. Trigger API call.
Assert: BrokerAuthError → SOFT\_KILL. Telegram alert.

CT074 | 15:15 Force Close | P0
Mid-trade: Y
Setup: Open positions at 15:14. Steps: Wait for 15:15.
Assert: SOFT\_KILL. Entry orders cancelled. EOD squareoff at 15:17.

────────────────────────────────────────
CATEGORY 3B — HARD KILL TRIGGERS
────────────────────────────────────────

CT075 | Capital Invariant Violation → Hard Kill | P0
Steps: Corrupt capital state in DB. Trigger mutation.
Assert: HARD\_KILL. ALL orders cancelled. Requires --resume.

CT076 | 3 Consecutive Circuit Breaker Failures | P0
Steps: Block REST during order operations. 3 failures.
Assert: HARD\_KILL. All orders cancelled.

────────────────────────────────────────
CATEGORY 3C — KILL SWITCH PERSISTENCE \& RECOVERY
────────────────────────────────────────

CT077 | Kill Switch Survives Restart | P0
Steps: SOFT\_KILL → restart (without --resume).
Assert: Kill switch loaded from DB. Signals rejected. System in killed state.

CT078 | Stale Kill Switch Auto-Clear | P0
Steps: SOFT\_KILL → simulate next-day restart (mock date or test clear\_stale\_state).
Assert: Previous-day kill cleared. System starts normally.

CT079 | --resume Recovery | P0
Steps: HARD\_KILL → stop → restart with --resume.
Assert: Kill cleared. Capital correct. System normal.

CT080 | Soft→Resume→Soft→Hard Sequence | P0
Steps: 5-step sequence (soft kill, resume, soft kill, resume, hard kill).
Assert: Each transition correct. Final hard kill: full stop. State accurate.

CT081 | Kill Switch Mid-Order (In-Flight Kite Call) | P0
Mid-trade: Y
Steps: Signal at OrderPlacer → activate SOFT\_KILL simultaneously.
Assert: FIX-070 blocks if pre-call. If mid-call: completes, fill reconciled.

CT082 | Kill Switch Idempotency | P1
Steps: Fire soft\_kill() 5 times in sequence.
Assert: State remains SOFT\_KILL. No side effects from repeated calls.

────────────────────────────────────────
CATEGORY 3D — CIRCUIT BREAKERS
────────────────────────────────────────

CT083 | Max Open Positions (10) | P0
Steps: Open 10 positions. Inject 11th.
Assert: 11th REJECTED by risk engine.

CT084 | Max Daily Trades (20) | P1
Steps: 20 open+close cycles. Inject 21st.
Assert: 21st REJECTED.
NOTE: Time-consuming. Use aggressive SLs for fast cycles.

CT085 | Max Consecutive Losses (4) | P0
Steps: 4 SL hits in sequence. Inject 5th.
Assert: 5th REJECTED. Counter resets on next win.

CT086 | Max Sector Exposure (40%) | P1
Steps: Open same-sector positions totaling >40%.
Assert: REJECTED\_RISK\_ENGINE.
NOTE: CANNOT\_TEST if sector data not available for paper symbols.

CT087 | Strategy Circuit Breaker | P1
Steps: 1 strategy with loss > 2x avg before 12:00.
Assert: Strategy paused until 12:00. Signal rejected.

CT088 | Partial Fill Timeout (5 min) | P1
Steps: Same as CT056.

────────────────────────────────────────
CATEGORY 3E — EOD SQUAREOFF
────────────────────────────────────────

CT089 | EOD Squareoff Happy Path | P0
Mid-trade: Y
Setup: 2 INTRADAY positions. Steps: Wait for 15:17.
Assert: Both closed via MARKET. eod\_squareoff\_log entries. Capital released.

CT090 | EOD Squareoff — Order Fails | P0
Mid-trade: Y
Steps: Open position → block REST at 15:16 → 15:17 fires → unblock.
Assert: Position remains open (KNOWN\_LIMITATION). Warning logged.

CT091 | EOD Pre-Alert 14:45 | P2
Steps: Open positions at 14:44. Wait.
Assert: Telegram alert at 14:45. No position action.

CT092 | Force Close 15:15 + EOD 15:17 Overlap | P0
Mid-trade: Y
Steps: Open positions. Observe both events fire.
Assert: No conflict. Both logged. Positions closed by 15:17.

\--- DAY 3 TOTAL: 26 scenarios ---

================================================================================
DAY 4 (Thu Jun 11): INFRASTRUCTURE + RECOVERY
===

Time: 09:30 — 16:30 IST (includes cron tests)

────────────────────────────────────────
CATEGORY 4A — PROCESS SIGNALS
────────────────────────────────────────

CT093 | SIGINT — Idle | P0
Steps: systemctl stop (idle system).
Assert: 17-step graceful shutdown. SHUTDOWN event. WAL checkpoint. Clean exit.

CT094 | SIGINT — Active Trade | P0
Mid-trade: Y
Setup: 2 open positions. Steps: systemctl stop.
Assert: Webhook stopped. Signal processor stopped. Entry orders cancelled.
Positions NOT squaredoff (survive for restart). SHUTDOWN marker.

CT095 | SIGINT — During Signal Processing | P0
Mid-trade: Y
Steps: Inject 10-signal burst → stop within 5s.
Assert: Queue signals lost (acceptable). Mid-pipeline signal abandoned.
Reserved capital released on cleanup. No corruption.

CT096 | SIGKILL — Idle | P0
Steps: kill -9. Restart.
Assert: CRASH scenario. Rehydration runs. System starts normally.

CT097 | SIGKILL — Active Trade | P0
Mid-trade: Y (CRITICAL)
Setup: 2 positions with SL/TGT at broker. Steps: kill -9. Restart.
Assert: Positions rehydrated. Orders rehydrated + reconciled. Capital rebuilt.
SmartTgt rehydrated. EntryGate rehydrated. All invariants hold.

CT098 | SIGKILL — During Order Placement | P0
Mid-trade: Y
Steps: Signal at OrderPlacer → kill -9 during HTTP call → restart.
Assert: If order placed at broker: detected by reconciler. If not: signal lost.
Capital: orphan RESERVE handled by rehydration. Invariant A.

CT099 | SIGKILL — During Capital Mutation | P0
Steps: 5-signal burst → kill -9 during processing → restart.
Assert: fm\_ledger (WAL+FULL sync) intact. Rehydration correct. Invariant A.

────────────────────────────────────────
CATEGORY 4B — RESTART RECOVERY
────────────────────────────────────────

CT100 | Cold Start (fresh, no prior session) | P0
Steps: Fresh DB (or delete session row). Start.
Assert: COLD scenario. All 14+ startup checks run. Capital from accounts.csv.

CT101 | Warm Start (normal previous shutdown) | P0
Steps: SIGINT → restart.
Assert: WARM scenario. Open positions rehydrated.

CT102 | Crash Start (SIGKILL recovery) | P0
Steps: kill -9 → restart.
Assert: CRASH scenario. Full rehydration. reconcile\_once() fires.

CT103 | HALT Start (exit code 3) | P0
Steps: Delete required config file → start.
Assert: Exit 3. systemd does NOT restart. No restart loop.

CT104 | Rehydration with Dirty State | P0
Steps: Open 2 positions → insert orphan fm\_ledger RESERVE → kill -9 → restart.
Assert: Orphan handled. Capital invariant holds. Open positions correct.

CT105 | Systemd Restart Loop Protection | P0
Steps: Crash 6 times in 10s (exceed StartLimitBurst default=5).
Assert: Systemd refuses restart after 5th. No infinite loop.

CT106 | Recovery Validation — Prove Last Known State | P0
Steps: kill -9 with 2 open + 1 pending. Restart.
Assert: System logs LAST\_KNOWN\_SAFE\_POINT, LAST\_COMMITTED\_ACTION,
LAST\_BROKER\_STATE, UNCERTAINTY\_LOG (per framework PART 9).

────────────────────────────────────────
CATEGORY 4C — NETWORK
────────────────────────────────────────

CT107 | Network Drop — 30s All Outbound | P0
Mid-trade: Y
Setup: 1 open position. Steps: block\_all 30s. Unblock.
Assert: WebSocket disconnects → reconnects. REST timeouts. Position safe. Recovers.

CT108 | Network Drop — 5 min Sustained | P0
Steps: block\_all 5 min.
Assert: WebSocket 10-reconnect → SOFT\_KILL. REST failures logged. Recovery after unblock.

CT109 | Partial Network — WebSocket Only Down | P1
Steps: block\_zerodha\_ws. REST works.
Assert: Candle data stops. Paper fill daemon (REST) still works. Orders still work.

CT110 | Partial Network — REST Only Down | P1
Steps: block\_zerodha\_rest. WebSocket works.
Assert: Screening fails. Orders fail. API failure counter rises. 3 → SOFT\_KILL.

CT111 | Network Drop During SL Trail | P0
Mid-trade: Y
Steps: Open position + trailing SL active → block REST during modify.
Assert: Naked position detected. Emergency exit.

CT112 | DNS Resolution Failure | P1
Steps: block\_dns 30s.
Assert: All external calls fail. Same as full outage.

CT113 | Internet — 5s Micro-Outage | P1
Steps: block\_all 5s.
Assert: Most operations retry or timeout gracefully. No kill switch (too short for 3 failures).

────────────────────────────────────────
CATEGORY 4D — DISK \& DB
────────────────────────────────────────

CT114 | Disk Full (<2GB) | P0
Steps: Fill disk to <2GB free → inject signal.
Assert: DB write may fail. Startup would have caught this. No DB corruption.
Cleanup: rm filler file.

CT115 | Disk Completely Full | P1
Steps: Fill to \~500MB free → inject signal → DB write.
Assert: SQLite error. System degrades. No corruption. Restart after cleanup.

CT116 | DB Lock Contention (30s busy) | P0
Steps: External script holds BEGIN IMMEDIATE 35s → inject signal.
Assert: busy\_timeout (30s) exceeded → OperationalError. Signal FAILED. No crash.

CT117 | WAL Recovery After SIGKILL | P0
Steps: Insert signal → kill -9 before checkpoint → restart.
Assert: WAL replayed. Data intact.

CT118 | DB Corruption — Missing Table | P1
Steps: DROP TABLE candles → restart.
Assert: Startup check detects → exit code 3 (HALT). No restart loop.
Cleanup: Restore DB from backup.

CT119 | DB Corruption — Missing Index | P2
Steps: DROP INDEX idx\_signals\_fingerprint\_today → restart.
Assert: System may start but dedup fails. Document behavior.
Cleanup: Restore index or restore DB.

CT120 | Read-Only Filesystem | P1
Steps: chmod -w on data\_store/ directory → inject signal.
Assert: DB write fails. System handles gracefully (or exits).
Cleanup: chmod +w.

CT121 | Permission Failure — Token File | P1
Steps: chmod 000 zerodha\_token.json → restart.
Assert: Startup fails (can't read token). Exit 3 or 6.

────────────────────────────────────────
CATEGORY 4E — CPU/RAM/RESOURCES
────────────────────────────────────────

CT122 | CPU Stress — 90%+ | P1
Steps: stress-ng --cpu $(nproc) --timeout 60. Inject signal during.
Assert: Signal processing slows but completes. No timeout crash.

CT123 | RAM Pressure — 90%+ | P1
Steps: stress-ng --vm 1 --vm-bytes 90% --timeout 60. Inject signal during.
Assert: System survives. No OOM kill.

CT124 | FD Exhaustion | P2
Steps: Open thousands of FDs from external script. System tries to open DB connection.
Assert: Error handled. No crash.

CT125 | inode Exhaustion | P2
Steps: Create millions of small files in /tmp. System tries to create log/report.
Assert: Error handled.

────────────────────────────────────────
CATEGORY 4F — CLOCK \& TOKEN
────────────────────────────────────────

CT126 | Clock Forward +60s | P1
Steps: sudo date -s "+60 seconds".
Assert: Clock skew detected (>30s). SOFT\_KILL or startup fail.

CT127 | Clock Backward -60s | P1
Same as CT126 but backward.

CT128 | Token Expiry During Market | P0
Steps: Delete token file while running. Trigger API call.
Assert: BrokerAuthError → SOFT\_KILL. token-watcher detects.

CT129 | Token Refresh → Auto-Start | P1
Steps: Write fresh valid token JSON. token-watcher detects.
Assert: trading-system starts. Startup checks pass.

────────────────────────────────────────
CATEGORY 4G — CRON \& HUMAN ERROR
────────────────────────────────────────

CT130 | Cron Silent Failure | P0
Steps: Rename eod\_verify.py → .bak. Wait for scheduled time. Wait for check\_cron\_drift.
Assert: Missing heartbeat detected. Telegram alert. Cleanup: rename back.

CT131 | Cron + Trading Concurrent | P1
Steps: Inject 20 signals while capture\_metrics\_baseline runs.
Assert: No interference. No DB lock.

CT132 | Human Error — Delete Config File Mid-Session | P1
Steps: rm a strategy YAML while system running.
Assert: Next signal for that strategy fails gracefully. No crash.

CT133 | Human Error — Bad YAML Config | P1
Steps: Write invalid YAML to a strategy config. Restart.
Assert: Startup schema validation catches it. Exit 3 (HALT).

CT134 | Human Error — Wrong Account in .env | P2
Steps: Change ZERODHA\_API\_KEY to wrong value. Restart.
Assert: API auth fails at startup. Exit 6 or SOFT\_KILL.

CT135 | Human Error — Cleanup During Trading | P1
Steps: Run cleanup.py --hard while system has open positions.
Assert: cleanup should REFUSE if system active (or require --force).
If no safety: DESIGN\_GAP.

\--- DAY 4 TOTAL: 43 scenarios ---

================================================================================
DAY 5 (Fri Jun 12): CHAOS + CERTIFICATION
===

Time: 09:30 — 16:30 IST

────────────────────────────────────────
CATEGORY 5A — MULTI-FAILURE COMBINATIONS
────────────────────────────────────────

CT136 | Signal Flood + Network Drop | P0
Steps: 50 signals → at #25 block\_all 30s → unblock.
Assert: Some processed, API failures, possible SOFT\_KILL. No corruption. Invariants.

CT137 | Kill Switch + Active Positions + EOD Squareoff | P0
Mid-trade: Y
Steps: 3 positions → SOFT\_KILL (loss limit) → wait for 15:17 EOD.
Assert: Kill prevents new entries. EOD still squaresoff positions.

CT138 | SIGKILL + Restart + Immediate Flood | P0
Steps: 2 positions → kill -9 → restart → inject 20 signals during startup.
Assert: Startup rehydration completes first. Signals queue or reject until ready.

CT139 | Invariant Violation + Open Positions | P0
Steps: 2 positions → corrupt capital → trigger mutation.
Assert: HARD\_KILL. Orders cancelled. Positions remain at broker. --resume required.

CT140 | EOD Squareoff Failure — All Positions | P0
Mid-trade: Y
Steps: 3 positions → block REST at 15:16 → EOD fires → unblock.
Assert: All 3 fail to close. KNOWN\_LIMITATION. System doesn't crash.

CT141 | Network Drop + SL Trail + Candle Gap | P0
Mid-trade: Y
Steps: Position with trail → block WS (REST works) → price moves → unblock.
Assert: Trail pauses (no candles). After reconnect: resumes. order\_monitor via REST catches.

CT142 | DB Busy + Signal Burst | P1
Steps: External 15s transaction → inject 10 signals.
Assert: Signals wait (busy\_timeout 30s). Process after txn commits. Or fail gracefully.

────────────────────────────────────────
CATEGORY 5B — FULL DAY SIMULATION
────────────────────────────────────────

CT143 | Complete Trading Day Simulation | P0 (MOST IMPORTANT)
Steps:
09:16: System starts (fresh token)
09:25: Startup checks pass
09:30: Inject 5 signals (morning burst)
09:35: 3 pass screening, 2 rejected (verify rejection reasons)
09:40: 3 orders placed, fills within 30s
10:00: Simulate 1 SL hit (loss)
10:30: Inject 3 more signals
11:00: Simulate 1 TGT hit (profit)
11:30: Inject 2 signals (mid-day)
12:00: SL trail advances on 1 position
13:00: Inject 1 signal (afternoon)
14:45: EOD pre-alert fires
15:15: Force close
15:17: EOD squareoff for remaining positions
15:40-17:00: EOD cron chain runs
17:00: Verify ALL reports generated
Assert:
- Capital at end = start + sum(PnL) - sum(costs)
- All tables consistent
- Every Telegram notification sent
- Daily report correct
- fm\_ledger audit trail complete
- exactly\_once\_verifier: PASS
- state\_machine\_validator: PASS
- invariant\_checker --full: ALL PASS
- forensic\_reconstructor --source db: all trades reconstructable
Budget: 2-3 hours for this scenario alone.

────────────────────────────────────────
CATEGORY 5C — REHYDRATION \& CRASH STRESS
────────────────────────────────────────

CT144 | Maximum Dirty State SIGKILL | P0
Setup: Build max state:
- 3 open positions (different symbols)
- 2 pending entries
- 1 active gate watch
- 1 active SL trail
- 5 signals in queue
Steps: kill -9 → restart.
Assert: All rehydrated correctly. Capital rebuilt. Invariants hold.

CT145 | Rapid Crash Cycle — 3x | P0
Steps: Start → inject 2 signals → kill -9 (10s) → restart → inject 2 → kill -9 → restart → inject 1 → process normally.
Assert: No state corruption. No phantoms. Invariant A after each restart.

CT146 | Kill Switch Marathon | P0
Steps: Soft(API)→resume→Soft(loss)→resume→Hard(invariant)→resume→verify healthy.
Assert: Each transition correct. No accumulated damage. System clean at end.

────────────────────────────────────────
CATEGORY 5D — MARKET ANOMALIES
────────────────────────────────────────

CT147 | Upper Circuit Stock | P1
Steps: Inject signal for stock at upper circuit (if detectable).
Assert: Order may fail or fill at circuit price. Handled gracefully.
NOTE: Paper mode may not simulate circuits. CANNOT\_TEST if not simulatable.

CT148 | Gap Down — SL Breached at Open | P1
Steps: Open position with SL. Simulate next-day gap (price opens below SL).
Assert: For INTRADAY: positions closed at EOD, not overnight. N/A.
For future POSITIONAL: SL triggers at open price, not SL price. PnL correct.
NOTE: Current system = INTRADAY only. Mark NOT\_APPLICABLE or DESIGN\_GAP for positional.

CT149 | Broker Data — LTP=0 | P0
Steps: Mock adapter to return LTP=0 for a symbol.
Assert: Position sizing: no division-by-zero. ShadowTracker: graceful.

CT150 | Broker Data — High < Low | P1
Steps: Mock candle data with High=100, Low=200 (inverted).
Assert: TA calculations reject. Screening rejects signal.

────────────────────────────────────────
CATEGORY 5E — AGY GOVERNANCE
────────────────────────────────────────

CT151 | AGY Boundary — Write .py File | P0
Steps: Via agy: attempt to create/edit a .py file.
Assert: Blocked by AGENTS.md. No modification occurs.

CT152 | AGY Boundary — Modify DB | P0
Steps: Via agy: attempt to write to trading\_system.db.
Assert: Blocked. No modification.

CT153 | AGY Boundary — Run systemctl | P0
Steps: Via agy: attempt systemctl restart.
Assert: Blocked.

CT154 | AGY Crash → Trading System Impact | P1
Steps: Kill trading-watchman.service.
Assert: Trading system unaffected (BindsTo is one-directional).

CT155 | AGY Alert Storm | P1
Steps: Generate 100 WARNING lines in log rapidly.
Assert: Watchman batches alerts. No Telegram flood. Rate limit enforced.

────────────────────────────────────────
CATEGORY 5F — FORENSICS \& AUDIT
────────────────────────────────────────

CT156 | Forensic Reconstruction — DB Only | P0
Steps: After CT143 (full day sim): forensic\_reconstructor.py --source db for each trade.
Assert: Every trade fully reconstructable: signal→screen→risk→capital→order→fill→exit→pnl.

CT157 | Report Integrity — Cross-Check | P0
Steps: After CT143: compare daily\_report.xlsx vs DB vs Telegram.
Assert: All numbers match. No discrepancy.

CT158 | State Machine Validation — Post All Tests | P0
Steps: state\_machine\_validator.py --all (run at end of Day 5).
Assert: Zero illegal transitions in entire DB after 5 days of testing.

────────────────────────────────────────
CATEGORY 5G — GOLD STANDARD CERTIFICATION
────────────────────────────────────────

CT159 | GOLD STANDARD SCENARIO | P0 (PRODUCTION CERTIFICATION)
This is the FINAL exam. System must pass this to proceed to paper trading.

Steps:
09:15: Market open. System running.
09:16: Inject 20 signals (burst — simulates heavy scanner output)
09:20: Block Telegram (outage)
09:25: 10 signals pass screening → orders placed
09:30: Internet drop 30s (block\_all)
09:35: Internet restores. WebSocket reconnects.
09:40: 2 SL hits within 5s (rapid close)
09:45: Inject 10 more signals
09:50: 1 TGT hit
10:00: RAM stress to 85% (stress-ng 60s)
10:05: kill -9 (SIGKILL with open positions)
10:06: Restart. Rehydration under load.
10:10: Inject 5 signals during/after rehydration
10:15: Unblock Telegram. Alert backlog delivered (email fallback).
10:30: Force capital drift (+Rs 100 in DB) → reconciler detects
10:35: If drift > tolerance → appropriate action
10:45: Restore capital state
11:00: Normal trading continues (inject 5 signals)
14:45: EOD pre-alert
15:15: Force close
15:17: EOD squareoff
15:40-17:00: Full cron chain

Assert:
✓ No duplicate trades (exactly-once verified)
✓ No orphan orders (invariant G)
✓ No lost capital (invariant A)
✓ No unreconciled positions (invariant D)
✓ No illegal state transitions (invariant E)
✓ No missing audit trail (invariant F)
✓ Deterministic recovery after SIGKILL (invariant H)
✓ Full forensic reconstruction possible (CT156)
✓ All reports generated correctly
✓ Telegram alerts delivered (with email fallback during outage)
✓ Capital at end = calculated expected value

Classification: PASS = system certified for paper trading
ANYTHING ELSE = fix and re-run before paper

\--- DAY 5 TOTAL: 24 scenarios ---

================================================================================
PHASE 2 — SOAK TESTS (Jun 15-26, during paper trading)
===

Model: Sonnet 4.6 (monitoring, no code changes)

These run ALONGSIDE real paper trading. resource\_monitor.py runs continuously.
Check daily. Report weekly.

ST001 | Memory Leak — 1 Day | P0
Assert: Memory growth < 10% over baseline after full trading day.

ST002 | Memory Leak — 3 Day | P0
Assert: Memory growth < 15% over 3 days.

ST003 | Memory Leak — 7 Day | P0
Assert: Memory growth < 20% over 7 days. No unbounded growth.

ST004 | Thread Leak | P0
Assert: Thread count = baseline (±0). No zombie threads.

ST005 | FD Leak | P0
Assert: File descriptor count stable. No growth.

ST006 | Socket Leak | P1
Assert: Socket count stable. No unclosed connections.

ST007 | Queue Growth | P0
Assert: Signal queue returns to 0 within 5 minutes of last signal.

ST008 | DB Growth Rate | P1
Assert: Predictable growth. No explosion.

ST009 | Log Growth Rate | P1
Assert: Predictable. No unbounded growth per signal.

ST010 | Query Performance | P1
Assert: No degradation over 14 days. Key queries < 100ms.

ST011 | Silent Failure Detection — Order Monitor | P0
Assert: order\_monitor polls every 2s. If frozen > 10s → alert.

ST012 | Silent Failure Detection — Reconciler | P0
Assert: reconciler polls every 15s. If frozen > 45s → alert.

ST013 | Silent Failure Detection — Telegram | P1
Assert: If send fails 3x consecutively → alert\_watcher triggers email.

ST014 | Zombie Thread Scan | P0
Assert: All named threads respond to heartbeat. Dead threads → alert.

ST015 | Weekend Idle → Monday Resume | P0
Assert: System handles weekend gap. Monday 09:30 signal processes normally.

================================================================================
PHASE 3 — PRE-LIVE CERTIFICATION (weekend before live)
===

Model: Opus 4.6 (certification review)

PC001 | Gold Standard Re-Run | P0
Re-run CT159 with real market data context from paper period.
Must PASS clean.

PC002 | Full PnL Reconciliation | P0
Paper trading period: DB PnL vs broker PnL vs daily reports.
All must match within Rs 1 tolerance.

PC003 | Forensic Reconstruction — All Paper Trades | P0
Every trade from paper period: fully reconstructable from DB.

PC004 | Data Retention Test | P1
All paper trading data queryable. Startup time acceptable.

PC005 | SPOF Review | P0
Every dependency documented: continue/degrade/pause/kill/stop.
(Framework PART 10 verified against actual paper experience)

PC006 | TEMP Config Decision | P0
Decide: keep production values or adjust based on paper data.
Document reasoning.

PC007 | Final Invariant Check | P0
invariant\_checker.py --full on production DB.
ALL 8 invariants must PASS.

PC008 | Operational Runbook Verified | P0
Every operational procedure tested during paper period.
Runbook matches actual system behavior.

================================================================================
SCENARIO COUNT SUMMARY
===

|Phase|Section|Scenarios|
|-|-|-|
|1|Day 1: Components + Signals|36|
|1|Day 2: Capital + Orders|30|
|1|Day 3: Kill Switch + Safety|26|
|1|Day 4: Infrastructure|43|
|1|Day 5: Chaos + Certification|24|
||**Phase 1 Total**|**159**|
|-------|--------------------------------|-----------|
|2|Soak Tests (during paper)|15|
|3|Pre-Live Certification|8|
||||
|**GRAND TOTAL**|**182**||

================================================================================
FINAL AUDIT OUTPUT FORMAT (per scenario)
===

Every scenario result JSON includes:

scenario\_id: "CT001"
title: "..."
classification: PASS | PASS\_WITH\_RISK | FAIL | KNOWN\_LIMITATION |
DESIGN\_GAP | CANNOT\_TEST | NOT\_APPLICABLE
root\_cause: "..." (if not PASS)
failure\_type: "..." (if FAIL)
business\_impact: "..."
technical\_impact: "..."
recovery\_status: "recovered | not\_recovered | N/A"
fix\_required: true/false
fix\_id: "FIX-XXX" (if fix created)
retest\_required: true/false
invariant\_results: {A: PASS, B: PASS, ..., H: PASS}
timestamp: "2026-06-08T10:30:00+05:30"

================================================================================
FINAL SUCCESS CRITERIA — PROCEED TO PAPER TRADING ONLY IF:
===

1. ALL P0 scenarios: PASS or PASS\_WITH\_RISK
2. No capital invariant violations (zero tolerance)
3. No unreconciled broker positions
4. No orphan orders after any recovery
5. No unrecoverable crashes
6. No audit trail gaps
7. No silent failures undetected
8. No duplicate processing
9. Deterministic restart recovery proven
10. Gold Standard (CT159): PASS
11. Full forensic reconstruction possible
12. All FAIL items fixed and retested

================================================================================
END OF SCENARIO CATALOG
Prepared: 04-Jun-2026 | Author: Web Claude (Opus 4.6)
Next: Monday Jun 8 — VS Code Claude (Opus 4.6) builds tools + starts Day 1
===

