================================================================================
CRASH TEST — INDEX \& ADDENDUM (READ THIS FIRST)
===

Date: 04-Jun-2026 | Version: 2.1
This file is the ENTRY POINT. VS Code Claude reads this BEFORE anything else.
===

# VS CODE CLAUDE: MANDATORY READING ORDER

EVERY session, read files in this EXACT order. Do NOT skip.

FILE 1 (this file):
Path: docs/web\_claude/07\_crash\_test/00\_index\_and\_addendum.md
Contains: Reading order, 4 additional audit requirements, session checklist
When: ALWAYS first

FILE 2 (framework bible):
Path: docs/web\_claude/07\_crash\_test/01\_framework\_bible.md
Contains: 8 global invariants, 12 tools, classification, schema, prerequisites
When: ALWAYS second (skim if already read today — focus on PART 1 invariants)

FILE 3 (scenario catalog):
Path: docs/web\_claude/07\_crash\_test/02\_scenario\_catalog.md
Contains: All 182 scenarios across 3 phases
When: Read ONLY your current day's section. Do NOT read other days.

Day 1 → search "DAY 1 (Mon Jun 8)" → read until "DAY 2"
Day 2 → search "DAY 2 (Tue Jun 9)" → read until "DAY 3"
Day 3 → search "DAY 3 (Wed Jun 10)" → read until "DAY 4"
Day 4 → search "DAY 4 (Thu Jun 11)" → read until "DAY 5"
Day 5 → search "DAY 5 (Fri Jun 12)" → read until "PHASE 2"

FILE 4 (detailed scenarios — reference only):
Path: docs/web\_claude/07\_crash\_test/03\_detailed\_scenarios.md
Contains: Original 119 scenarios with FULLER step-by-step details
When: ONLY when a scenario in catalog needs more detail.
Look up by CT### ID. Catalog CT001-CT036 ≈ Plan CT001-CT026.
Not all IDs match — use title to cross-reference.

SESSION START CHECKLIST (do this EVERY new session):
\[ ] Read 00\_index (this file)
\[ ] Read 01\_framework\_bible (skim if same day, full if new day)
\[ ] Read 02\_scenario\_catalog (current day section ONLY)
\[ ] Check mempalace for: crash\_test progress, previous day results
\[ ] Verify: what scenarios are done, what's next
\[ ] If new day: run resource\_monitor.py --report (check overnight)

SESSION END CHECKLIST:
\[ ] Run crash\_test\_reporter.py --day N
\[ ] Run invariant\_checker.py --full (final check)
\[ ] Run cleanup.py --soft (prepare for next day)
\[ ] Store in mempalace:
Key: crash\_test\_day{N}\_results\_DDMMMYYYY
Value: "Day N complete. X PASS / Y FAIL / Z other. Critical: \[list].
Next: Day N+1 starts with CT###."
\[ ] If any FAIL: create FIX-### entry in tasks/todo.md

================================================================================
ADDENDUM A — SYSTEM ASSUMPTIONS AUDIT (Do on Day 1, before CT001)
===

Create: docs/SYSTEM\_ASSUMPTIONS.md

List EVERY assumption the system makes. For each:

* Assumption statement
* Where in code it's assumed (file:line or module)
* What happens if false
* Is it tested in crash test? (Y/N, with CT### reference)
* Mitigation if assumption fails

MANDATORY assumptions to document (not exhaustive — add more):

A1. Chartink payload format never changes
If false: webhook parser fails, signals rejected
Tested: CT019, CT020, CT021

A2. Zerodha returns valid OHLC candles
If false: screening produces garbage scores
Tested: CT039, CT042, CT043, CT150

A3. Zerodha returns valid LTP (non-zero, non-negative)
If false: position sizing division-by-zero, paper fill never triggers
Tested: CT005, CT149

A4. System clock is accurate (< 30s skew)
If false: signal expiry wrong, market windows wrong, EOD timing wrong
Tested: CT126, CT127

A5. SQLite DB is reliable (no silent corruption)
If false: all state lost or incorrect
Tested: CT117, CT118, CT119

A6. Telegram API is available (eventually)
If false: no alerts (email fallback exists)
Tested: CT154 indirectly, Gold Standard CT159

A7. Internet eventually recovers within session
If false: system stuck in SOFT\_KILL until manual resume
Tested: CT107, CT108

A8. Oracle VM survives reboot with data intact
If false: need backup restore
Tested: CT096-CT099 (SIGKILL as proxy)

A9. Zerodha API rate limits are as documented (10/sec)
If false: more aggressive throttling needed
Tested: CT023, CT024

A10. Cron runs on schedule (crontab reliable)
If false: EOD jobs missed
Tested: CT130

A11. Broker fill callbacks are reliable (paper: 5s poll)
If false: fill detection delayed, orphan SL/TGT timing wrong
Tested: CT052, CT053

A12. fm\_ledger writes survive crash (WAL + FULL synchronous)
If false: capital state lost on crash
Tested: CT099, CT117

A13. Thread.start() threads don't silently die
If false: order\_monitor, reconciler, signal\_processor stop working
Tested: ST011, ST012, ST014 (Phase 2 soak)

A14. Chartink retries on 503 (external behavior)
If false: signals permanently lost during backpressure
Tested: CT034 (simulated)

A15. No other process writes to trading\_system.db
If false: DB corruption, lock contention
Tested: CT116

A16. Strategy YAML configs don't change during market hours
If false: mid-session behavior change
Tested: CT132

After creating, store in mempalace:
Key: system\_assumptions\_audit\_complete
Value: "SYSTEM\_ASSUMPTIONS.md created with N assumptions. All cross-referenced to crash test scenarios."

================================================================================
ADDENDUM B — SINGLE SOURCE OF TRUTH AUDIT (Do on Day 1, after assumptions)
===

Create: docs/SSOT\_AUDIT.md

For EVERY business object, define and verify the single source of truth.
Format per object:

Object: \[name]
Truth source: \[table/system]
NOT truth (common mistakes): \[list what ISN'T authoritative]
Verification: \[how to confirm truth source is correct]
Crash test coverage: \[CT### references]

MANDATORY objects:

1. CAPITAL STATE
Truth: fm\_ledger (replay all entries to reconstruct)
NOT truth: capital\_snapshot table (cache only), FundManager in-memory
(lost on crash), Excel report (derived), Telegram message (derived)
Verify: Replay fm\_ledger == capital\_snapshot == in-memory. If not: invariant A fails.
Coverage: CT044-CT051, CT047 (invariant violation)
2. POSITION
Truth: Broker (Zerodha) + Reconciliation Layer
NOT truth: trades table alone (may lag), in-memory position dict (lost on crash)
Verify: order\_reconciler.reconcile\_once() compares system vs broker.
Coverage: CT057, CT058, CT062, CT097
3. ORDER
Truth: Broker Orderbook (Zerodha order status)
NOT truth: orders table alone (may not reflect latest broker state)
Verify: order\_monitor polls broker, updates orders table.
Coverage: CT044, CT052-CT061
4. SIGNAL
Truth: signals table in DB
NOT truth: in-memory queue (lost on crash), webhook response (transient)
Verify: Every webhook POST → row in signals table with terminal status.
Coverage: CT008, CT009, CT027
5. KILL SWITCH STATE
Truth: kill\_switch\_state table in DB
NOT truth: in-memory kill\_switch flag (lost on crash)
Verify: CT077 (survives restart), CT078 (stale clear)
Coverage: CT067-CT082
6. TRADE LIFECYCLE
Truth: trades table + orders table (joined)
NOT truth: Telegram notification (may fail), Excel report (derived)
Verify: forensic\_reconstructor.py traces full chain.
Coverage: CT156, CT157
7. SYSTEM CONFIGURATION
Truth: system\_config.yaml + strategy YAMLs (on disk)
NOT truth: in-memory config objects (may be stale after file edit)
Verify: Startup re-reads all configs. No hot-reload (by design).
Coverage: CT133

After creating, store in mempalace:
Key: ssot\_audit\_complete
Value: "SSOT\_AUDIT.md created. 7 business objects audited. All truth sources verified."

================================================================================
ADDENDUM C — RECOVERY TIME OBJECTIVE (RTO) MEASUREMENT
===

Integrate into scenario\_runner.py — add RTO measurement for recovery scenarios.

For each recovery scenario, measure and record:

* failure\_timestamp: when failure was injected
* recovery\_start\_timestamp: when system began recovery
* recovery\_complete\_timestamp: when system is fully operational
* recovery\_time\_seconds: total elapsed
* expected\_rto\_seconds: what we expect
* rto\_met: true/false

RTO TARGETS (define these, adjust based on actuals):

|Failure Type|Expected RTO|Max Acceptable|Scenarios|
|-|-|-|-|
|Signal processor crash|10s|30s|CT035|
|SIGINT graceful shutdown|5s|15s|CT093|
|SIGKILL + restart|15s|45s|CT096-CT099|
|VM reboot|60s|120s|(manual)|
|Internet loss 30s|35s|60s|CT107|
|Internet loss 5 min|305s|360s|CT108|
|Token expiry|manual|N/A|CT128|
|DB lock release|30s|35s|CT116|
|Kill switch resume|5s|10s|CT079|
|Hard kill + --resume|10s|30s|CT079|
|Full rehydration (crash)|15s|60s|CT097, CT144|

Output: reports/crash\_test/rto\_measurements.md
Table: failure\_type | expected | actual | met? | scenario\_id

After Day 4 (all recovery scenarios done), store in mempalace:
Key: rto\_measurements\_complete
Value: "RTO measured for N scenarios. X met target, Y exceeded. Worst: \[type] at \[Xs]."

================================================================================
ADDENDUM D — OPERATIONS RUNBOOK (Create during/after crash test)
===

Create: docs/RUNBOOK.md

This must be written so that ANOTHER operator (not Rama) can run the system.
Build this incrementally — add sections as crash tests verify each procedure.

REQUIRED SECTIONS:

1. DAILY STARTUP

   * Token generation (manual browser step)
   * token-watcher auto-start verification
   * Health check: curl http://localhost:5000/health
   * Verify: no stale kill switch, capital correct, cron active
2. DAILY MONITORING

   * agy watchman: what to look for in Telegram
   * Log file locations and what's normal
   * Expected signal volume per day
   * Health endpoint interpretation
3. GRACEFUL SHUTDOWN

   * sudo systemctl stop trading-system
   * Verify: SHUTDOWN event in logs
   * Verify: no orphan orders at broker (manual Kite check)
4. EMERGENCY SHUTDOWN

   * SOFT: sudo systemctl stop trading-system
   * HARD: sudo kill -9 $(pgrep -f main.py)
   * NUCLEAR: kill + login to Kite web + manually cancel all orders
5. KILL SWITCH RECOVERY

   * Check: python state\_inspector.py --live (see kill switch state)
   * If SOFT\_KILL: fix root cause → restart with --resume
   * If HARD\_KILL: verify capital + positions → restart with --resume
   * Post-resume: inject test signal → verify processing
6. TOKEN REFRESH

   * Login to Kite web → generate token
   * Place token file at correct path
   * token-watcher auto-detects and starts system
   * Verify: health check passes
7. BROKER RECONCILIATION (manual)

   * Login to Kite web → check positions
   * Compare with: python state\_inspector.py --live
   * If mismatch: investigate orders table vs broker
   * If position exists at broker but not system: CRITICAL
8. EOD VERIFICATION

   * After 17:00: check reports/daily/ for today's report
   * Check Telegram for EOD summary
   * Check: all positions closed (no overnight for INTRADAY)
9. DISASTER RECOVERY

   * DB backup: cp trading\_system.db trading\_system\_backup\_$(date +%F).db
   * Full restore: stop system → copy backup → start system
   * Config restore: cp -r config\_backup/ config/
   * Verify after restore: invariant\_checker.py --full
10. CRON VERIFICATION

    * Check: crontab -l (expected entries)
    * Check: system\_events table for cron heartbeats
    * If missing: check cron logs (/var/log/syslog grep CRON)
11. COMMON ERRORS \& FIXES
(Build this from crash test findings — add each FAIL scenario's fix)
12. ESCALATION

    * Level 1: Check logs, restart system
    * Level 2: Check broker manually, reconcile
    * Level 3: Stop trading, investigate, involve developer (VS Code Claude)

After creating base runbook, store in mempalace:
Key: runbook\_created
Value: "docs/RUNBOOK.md created with 12 sections. Will update with crash test findings."

================================================================================
EXECUTION TIMELINE (REVISED)
===

SUNDAY Jun 7 (evening):

* Rama: Subscribe Zerodha API + Chartink (if doing on Monday, adjust)
* VS Code Claude: Read all 4 files. Build 12 tools. Run prerequisites checklist.
* Create: SYSTEM\_ASSUMPTIONS.md, SSOT\_AUDIT.md, base RUNBOOK.md
* Verify: system starts clean, all tools work
* Mempalace: "crash\_test\_prep\_complete"

MONDAY Jun 8 — Day 1 (Components + Signals):
New session. Read: 00\_index → 01\_framework → 02\_catalog (Day 1 only).
Check mempalace for prep status.
36 scenarios. Run. Report. Mempalace update.

TUESDAY Jun 9 — Day 2 (Capital + Orders):
New session. Read: 00\_index → 01\_framework (skim) → 02\_catalog (Day 2).
Check mempalace for Day 1 results.
30 scenarios. Run. Report. Mempalace update.

WEDNESDAY Jun 10 — Day 3 (Kill Switch + Safety):
New session. Same pattern.
26 scenarios. Run. Report. Mempalace update.

THURSDAY Jun 11 — Day 4 (Infrastructure + Recovery):
New session. Same pattern. RTO measurement active.
43 scenarios. Run. Report. RTO report. Mempalace update.

FRIDAY Jun 12 — Day 5 (Chaos + Gold Standard):
New session. Same pattern.
24 scenarios. Gold Standard is LAST.
Final: state\_machine\_validator --all, invariant\_checker --full.
Master report: crash\_test\_reporter.py --day all.
Mempalace: full crash test summary.
Update RUNBOOK.md with findings.

POST-CRASH-TEST (Jun 13-14 weekend):

* Fix all FAIL items (FIX-### with permanent fix + parity)
* Retest fixed scenarios
* Decision: revert TEMP config or keep production values
* Transition to paper trading (real Chartink signals)

================================================================================
ANTI-HALLUCINATION RULES FOR VS CODE CLAUDE
===

1. NEVER assume a test passed without running it.
Every PASS must have evidence (snapshot diff, log excerpt, or assertion output).
2. NEVER skip a scenario. If a scenario can't run (environment issue),
classify as CANNOT\_TEST with detailed reason.
3. NEVER guess system behavior. If uncertain: read the code, run a test,
or grep the logs. "I believe..." is not acceptable — "I verified by..." is.
4. After EVERY context limit restart:

   * Re-read 00\_index (this file)
   * Check mempalace for current progress
   * Verify: which scenario was last completed
   * Resume from next scenario, don't repeat
5. If a tool or script fails: fix it before continuing.
Don't work around broken tools.
6. If a scenario result is ambiguous: classify as PASS\_WITH\_RISK
and document what's ambiguous. Never force a PASS.
7. Store progress in mempalace after EVERY 5 scenarios (not just end of day).
Format: "crash\_test\_progress: Day N, CT### through CT### complete.
X PASS, Y FAIL. Next: CT###"
8. If you run out of context mid-day: write a handoff note to
reports/crash\_test/handoff\_dayN\_sessionM.md with:

   * Last completed scenario
   * Current system state
   * Any open issues
   * Next scenario to run
Then tell Rama: "Context limit. Save handoff note. Start new session."

================================================================================
END OF INDEX \& ADDENDUM
===

