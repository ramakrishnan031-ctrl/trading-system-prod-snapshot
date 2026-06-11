# System Assumptions Audit

Date: 05-Jun-2026 | Crash Test Day 0 (offline pre-tests)

| # | Assumption | Where in Code | What If False | Tested | CT Ref | Status |
|---|-----------|---------------|---------------|--------|--------|--------|
| A1 | Chartink payload format never changes | webhook_receiver.py:parse_chartink_payload | Webhook parser fails, signals rejected | YES | CT019, CT020, CT021 | ASSUMPTION_HOLDS |
| A2 | Zerodha returns valid OHLC candles | screening/quality_scorer.py | Screening produces garbage scores | NO | CT039, CT042, CT043 | NOT_TESTED_YET |
| A3 | Zerodha returns valid LTP (non-zero, non-negative) | orders/position_sizer.py | Division-by-zero, paper fill never triggers | YES | CT005 | ASSUMPTION_HOLDS |
| A4 | System clock is accurate (< 30s skew) | core/time_authority.py:now_ist() | Signal expiry wrong, market windows wrong, EOD timing wrong | YES | CT126, CT127 | PENDING_VM |
| A5 | SQLite DB is reliable (no silent corruption) | core/state_store.py | All state lost or incorrect | YES | CT117, CT118 | ASSUMPTION_HOLDS |
| A6 | Telegram API is available (eventually) | alerts/telegram_notifier.py | No alerts (email fallback exists) | NO | CT154 | NOT_TESTED_YET |
| A7 | Internet eventually recovers within session | main.py connectivity checks | System stuck in SOFT_KILL until manual resume | NO | CT107, CT108 | NOT_TESTED_YET |
| A8 | Oracle VM survives reboot with data intact | systemd + filesystem | Need backup restore | YES | CT096 | ASSUMPTION_HOLDS |
| A9 | Zerodha API rate limits are as documented (10/sec) | adapters/zerodha_adapter.py | More aggressive throttling needed | NO | CT023, CT024 | NOT_TESTED_YET |
| A10 | Cron runs on schedule (crontab reliable) | crontab (ubuntu user) | EOD jobs missed | YES | CT130 | PENDING_VM |
| A11 | Broker fill callbacks are reliable (paper: 5s poll) | orders/order_monitor.py | Fill detection delayed, orphan SL/TGT timing wrong | NO | CT052, CT053 | NOT_TESTED_YET |
| A12 | fm_ledger writes survive crash (WAL + FULL synchronous) | capital/fund_manager.py:_write_ledger | Capital state lost on crash | YES | CT117 | ASSUMPTION_HOLDS |
| A13 | Thread.start() threads don't silently die | main.py thread setup | order_monitor, reconciler, signal_processor stop working | NO | ST011-ST014 | NOT_TESTED_YET |
| A14 | Chartink retries on 503 (external behavior) | External (Chartink) | Signals permanently lost during backpressure | NO | CT034 | NOT_TESTED_YET |
| A15 | No other process writes to trading_system.db | data_store/ | DB corruption, lock contention | YES | CT116 | ASSUMPTION_HOLDS |
| A16 | Strategy YAML configs don't change during market hours | config/strategies/*.yaml | Mid-session behavior change | YES | CT132 | PENDING_VM |

## Additional Assumptions Discovered During Day 0

| # | Assumption | Where in Code | What If False | Tested | CT Ref | Status |
|---|-----------|---------------|---------------|--------|--------|--------|
| A17 | FundManager rejects NaN/Inf inputs | capital/fund_manager.py:reserve() | IntegrityError on fm_ledger write (NULL) | YES | CT004 | ASSUMPTION_HOLDS (fixed FIX-154) |
| A18 | Kill switch state persists across restarts | kill_switch_state table | System runs without safety protection | YES | CT007, CT093 | ASSUMPTION_HOLDS |
| A19 | data_store/ directory is writable | data_store/ | All writes fail, signals lost | YES | CT120 | ASSUMPTION_HOLDS |
| A20 | Circuit breaker fires at 15:15 daily (SOFT_KILL) | orders/order_monitor.py | Trades left open overnight | YES | CT093 | ASSUMPTION_HOLDS |
| A21 | Scheduled kills (force_close, EOD) auto-clear on restart | capital/kill_switch.py:auto_clear_scheduled_kill | System blocked next day by normal EOD kill | YES | FIX-154 | ASSUMPTION_HOLDS |

Updated: 07-Jun-2026 — A17 FM NaN guard + A21 scheduled kill auto-clear (FIX-154)
