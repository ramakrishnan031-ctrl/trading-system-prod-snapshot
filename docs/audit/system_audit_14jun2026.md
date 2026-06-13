# Deep System Audit — 14 Jun 2026

**Auditor:** Claude (automated)  
**Date:** 2026-06-13 / 2026-06-14  
**Scope:** Full codebase audit (Parts 1-14)  
**Standing rules:** Permanent fixes only. Paper + Live parity always.

---

## Summary

| Priority | BUG | RISK | INCONSISTENCY | DUPLICATION | DESIGN_GAP | DEAD_CODE | CONFIG_GAP | Total |
|----------|-----|------|---------------|-------------|------------|-----------|------------|-------|
| P0       | 6   | 0    | 0             | 0           | 0          | 0         | 0          | 6     |
| P1       | 2   | 1    | 1             | 1           | 2          | 0         | 1          | 8     |
| P2       | 4   | 5    | 5             | 2           | 2          | 1         | 2          | 21    |
| **Total**| 12  | 6    | 6             | 3           | 4          | 1         | 3          | 35    |

**Fixes applied:** FIX-165a through FIX-165h (8 fixes — all P0 BUGs + 1 P1 BUG + 1 P1 INCONSISTENCY)
**Test suite:** 2876 passed, 0 failed, 12 skipped

---

## FIXES APPLIED THIS SESSION

| Fix ID   | Finding | Priority | File | Description |
|----------|---------|----------|------|-------------|
| FIX-165a | F01 | P0 | fund_manager.py | top_up_reservation: invariant check race + missing handler + slm_buffer drop |
| FIX-165b | F03 | P0 | kill_switch.py | _exit_all_trades_indestructible: wrong columns/statuses/directions |
| FIX-165c | F04 | P0 | signal_processor.py | _in_flight_count goes negative (both paths) |
| FIX-165d | F07 | P0 | order_placer.py | Exit retry success path dead code (wrong indentation) |
| FIX-165e | F05 | P0 | signal_processor.py | Gate path missing FIX-070 kill-switch check before placement |
| FIX-165f | F19 | P1 | signal_processor.py | Rate-limiter queue-full abandon: signal status + in_flight release |
| FIX-165g | F20 | P0 | order_reconciler.py | FIX-068 timeout recovery calls non-existent StateStore methods |
| FIX-165h | F09 | P1 | order_placer.py | Emergency exit uses _LEG_SL instead of _LEG_EOD in fill_map |
| — | F21 | P1 | 5 scripts | DB filename `trading.db` → `trading_system.db` |

---

## PART 1: Capital & Fund Management

### F01 — FIX-165a: `top_up_reservation` race + slm_buffer drop *(P0 BUG — FIXED)*
- **File:** `capital/fund_manager.py:746-770`
- Three bugs: invariant check outside lock, missing violation handler, slm_buffer dropped.

### F02 — Capital modules: CLEAN
- `position_sizer.py`, `performance_allocator.py`, `strategy_governor.py`, `invariant.py`, `risk_engine.py`, `drift_handler.py` — all clean.

---

## PART 2: Kill Switch & Safety

### F03 — FIX-165b: `_exit_all_trades_indestructible` wrong column names *(P0 BUG — FIXED)*
- **File:** `capital/kill_switch.py:663-686`
- Wrong columns, statuses, and direction mapping vs schema.

### F22 — KillSwitch not wired to adapter in main.py *(P1 DESIGN_GAP — DISCUSS)*
- **File:** `main.py:1222`
- Neither `adapter` nor `on_hard_kill_cancel_fn` passed. **Hard_kill is toothless** — sets state but cannot exit positions or cancel orders. FIX-165b fixed the method itself but it remains unreachable.
- **Recommendation:** Add `kill_switch._adapter = broker_adapter` after adapter construction at main.py:1356. Discuss with Rama — has operational implications.

---

## PART 3: Signal Pipeline

### F04 — FIX-165c: `_in_flight_count` goes negative *(P0 BUG — FIXED)*
- **File:** `signals/signal_processor.py` — both `_process_one` and `continue_from_gate`

### F05 — FIX-165e: Gate path missing kill-switch check *(P0 BUG — FIXED)*
- **File:** `signals/signal_processor.py:1292+`

### F06 — Gate path missing strategy governor check *(P1 DESIGN_GAP — DISCUSS)*
- **File:** `signals/signal_processor.py`
- `_process_one` checks `strategy_governor.check()` but gate path skips it.

### F19 — FIX-165f: Rate-limiter queue-full abandon path *(P1 BUG — FIXED)*
- **File:** `signals/signal_processor.py:305-312`
- Signal status stayed QUEUED forever; in_flight lock never released.

### F23 — `_derive_target` raises ValueError instead of _PipelineReject *(P2 BUG)*
- **File:** `signals/signal_processor.py:1090`

### F24 — `avg_pipeline_ms` denominator mismatch *(P2 INCONSISTENCY)*
- **File:** `signals/signal_processor.py:1410-1422`
- Numerator includes all signals, denominator only counts successes.

### F25 — `continue_from_gate` runs synchronously on gate worker thread *(P2 DESIGN_GAP)*
- Blocks LTP polling when 2 entries trigger simultaneously.

---

## PART 4: Order Lifecycle

### F07 — FIX-165d: Exit retry success path dead code *(P0 BUG — FIXED)*
- **File:** `orders/order_placer.py:2786-2897`

### F09 — FIX-165h: Emergency exit wrong leg label *(P1 BUG — FIXED)*
- **File:** `orders/order_placer.py:3002`
- Used `_LEG_SL` in fill_map but `"EOD"` in DB/monitor → exit_reason would be `SL_HIT` instead of `EOD_SQUAREOFF`.

### F08 — `_check_liquidity` bypasses adapter API *(P1 RISK)*
- **File:** `orders/order_placer.py:3074`
- Directly calls `_kite.quote()` — skips rate limiting.

### F20 — FIX-165g: Reconciler timeout recovery broken *(P0 BUG — FIXED)*
- **File:** `orders/order_reconciler.py:1836,1894,1911`
- Three calls to non-existent StateStore methods (`get_trade_by_id`, `update_trade_status`). FIX-068 feature was silently broken. Fixed to use `_order_mgr`.

### F26 — Lock release/acquire inside `with` block is fragile *(P2 RISK)*
- **File:** `orders/order_placer.py:2619-2673`

### F27 — Stuck-partial cancel skips OrderPartiallyTerminated event *(P2 RISK)*
- **File:** `broker/order_monitor.py:960-967`

### F28 — `get_trades()` wrong rate limit category name *(P2 INCONSISTENCY)*
- **File:** `broker/zerodha_adapter.py:1196`

### F29 — Order lifecycle CLEAN sections
- State machine, SL/TGT timing, orphan detection, CHECK1-CHECK9 SQL, partial fills, EOD squareoff (2-pass + LIMIT_THEN_MARKET), OCO, rate limiting — all verified correct.

---

## PART 5: Database & Persistence

### F12 — Database layer: CLEAN
- WAL mode, BEGIN IMMEDIATE, per-thread connections, proper cursor cleanup, schema version check, whitelisted table name in `row_count()`.

---

## PART 6: Configuration

### F13 — `email_fallback_config` not wired *(P1 CONFIG_GAP — DISCUSS)*
- **File:** `main.py:1544`
- Config exists, notifier accepts it, but main.py never passes it.

### F30 — `nse_holidays_2026.yaml` hardcoded *(P2 CONFIG_GAP)*
- **File:** `config_loader.py:1205`, `scripts/premarket_healthcheck.py:54`
- Will break on Jan 1 2027.

---

## PART 7: AGY / Antigravity

### F14 — AGY: CLEAN
- Model cascade, governance boundaries, automation — all verified.

---

## PART 8: Code Quality

### F15 — Code quality: CLEAN
- No bare `except:` in production, no `# TEMP` markers, no `print()`, no SQL injection, consistent `now_ist()` usage, no `eval/exec`, no `os.system`, all production threads are daemon threads.

---

## PART 9: Startup Sequence

### F31 — Signal handlers installed after threads started *(P2 RISK)*
- **File:** `main.py:2174`
- SIGINT during startup bypasses clean shutdown.

### F32 — Partial startup failure leaks DB connection *(P2 RISK)*
- **File:** `main.py:1201-2193`
- No try/finally around Phase 0e subsystem construction.

### F33 — `StartupReport` omits `temp_config` result *(P2 BUG)*
- **File:** `utils/startup_checks.py:1594`

### F34 — `check_disk_space()` and `check_db_permissions()` never called *(P2 DESIGN_GAP)*
- **File:** `utils/startup_checks.py`
- Functions exist but `run_all_startup_checks()` doesn't call them.

---

## PART 10: Duplicate Code

### F17 — `_PRODUCT_TO_INTENT` in 3 files *(P1 DUPLICATION — DISCUSS)*
- `order_placer.py:241`, `order_reconciler.py:85`, `fund_manager.py:123`

### F18 — `_is_market_hours()` in 2 files *(P2 DUPLICATION)*
- `token_monitor.py:142`, `gemini_watchman.py:118`

---

## PART 11: Scripts

### F21 — 5 scripts use wrong DB filename *(P1 INCONSISTENCY — FIXED)*
- `eod_cleanup.py`, `reconcile_pnl.py`, `reconcile_positions.py`, `compute_strategy_metrics.py`, `fetch_fno_ban.py` — `trading.db` → `trading_system.db`

### F35 — `reconcile_pnl.py` and `reconcile_positions.py` wrong ZerodhaAdapter constructor *(P2 BUG)*
- Pass `api_key`/`access_token`/`paper` kwargs that don't match constructor signature.

### F36 — `premarket_healthcheck.py` calls non-existent TelegramNotifier methods *(P2 BUG)*
- `from_config()` and `.send_alert()` don't exist.

---

## PART 12: Entry Gate + Screening

### F37 — `updated_extras` dead code *(P2 DEAD_CODE)*
- **File:** `entry_gate.py:520`

### F38 — Frozen dataclass mutation via mutable dict interior *(P2 RISK)*
- **File:** `entry_gate.py:523`

### F39 — `clear_all` doesn't clear `_quote_failures` *(P2 INCONSISTENCY)*
- **File:** `entry_gate.py:255-273`

### F40 — Single step error rejects entire signal *(P2 RISK — by design)*
- **File:** `secondary_screener.py:141-161`

### F41 — Signal age docstring mismatch *(P2 INCONSISTENCY)*
- **File:** `step_executor.py:354-358`

### F42 — Screening + dedup CLEAN sections
- Dedup fingerprinting (collision-resistant), quality_scorer NaN handling, weight normalization, WatchEntry lifecycle, price-hit detection — all verified correct.

---

## PART 13: Post-Fix Testing

Test suite after all fixes: **2876 passed, 0 failed, 12 skipped** (389s)

---

## PART 14: Deployment

Pushed to VM via `git push origin main` — auto-deployed.

---

## Items for Rama's Review

| # | Priority | Type | Description |
|---|----------|------|-------------|
| 1 | P1 | DESIGN_GAP | Wire KillSwitch to broker_adapter so hard_kill can exit positions (F22) |
| 2 | P1 | DESIGN_GAP | Add strategy governor check to gate path (F06) |
| 3 | P1 | RISK | Route `_check_liquidity` through adapter public API (F08) |
| 4 | P1 | CONFIG_GAP | Wire `email_fallback_config` to TelegramNotifier (F13) |
| 5 | P1 | DUPLICATION | Extract `_PRODUCT_TO_INTENT` to shared module (F17) |
