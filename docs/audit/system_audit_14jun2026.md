# Deep System Audit — 14 Jun 2026

**Auditor:** Claude (automated)  
**Date:** 2026-06-13 / 2026-06-14  
**Scope:** Full codebase audit (Parts 1-14)  
**Standing rules:** Permanent fixes only. Paper + Live parity always.

---

## Summary

| Priority | BUG | RISK | INCONSISTENCY | DUPLICATION | DESIGN_GAP | DEAD_CODE | CONFIG_GAP | Total |
|----------|-----|------|---------------|-------------|------------|-----------|------------|-------|
| P0       | 4   | 0    | 0             | 0           | 0          | 0         | 0          | 4     |
| P1       | 0   | 1    | 0             | 1           | 1          | 0         | 1          | 4     |
| P2       | 0   | 0    | 2             | 1           | 0          | 0         | 0          | 3     |
| **Total**| 4   | 1    | 2             | 2           | 1          | 0         | 1          | 11    |

**Fixes applied this session:** FIX-165a through FIX-165e (5 fixes, all P0 BUGs)

---

## PART 1: Capital & Fund Management

### Finding F01 — FIX-165a: `top_up_reservation` race + slm_buffer drop
- **File:** `capital/fund_manager.py` lines 746-770
- **Type:** BUG | **Priority:** P0 | **Status:** FIXED
- **Description:** Three bugs in `top_up_reservation`:
  1. `_check_invariant()` was called outside the RLock — race condition under concurrent top-ups.
  2. `_handle_invariant_violation()` was never called on failure.
  3. `slm_buffer` field was dropped when creating new `_Reservation` (overwritten by default).
- **Fix:** Moved invariant check inside lock, added violation handling outside lock, preserved `slm_buffer=res.slm_buffer`.

### Finding F02 — Capital modules: CLEAN
- **Files:** `capital/position_sizer.py`, `capital/performance_allocator.py`, `capital/strategy_governor.py`, `capital/invariant.py`, `capital/risk_engine.py`, `capital/drift_handler.py`
- **Status:** No issues found. Pure calculation modules, thread-safe, fail-open where appropriate.

---

## PART 2: Kill Switch & Safety

### Finding F03 — FIX-165b: `_exit_all_trades_indestructible` wrong column names
- **File:** `capital/kill_switch.py` lines 663-686
- **Type:** BUG | **Priority:** P0 | **Status:** FIXED
- **Description:** Hard-coded SQL used wrong column names and status values:
  - `quantity` → should be `qty_filled`
  - `side` → should be `direction`
  - `PENDING` → should be `PENDING_FILL` + `PARTIAL`
  - Direction mapping `BUY/SELL` → should be `LONG/SHORT`
- **Impact:** Kill switch emergency exit would fail to exit any positions. Currently unreachable (main.py doesn't pass adapter to KillSwitch), but the method exists as a safety net.
- **Fix:** Corrected all column names, status values, and direction mapping. Added `qty == 0` skip guard.
- **Tests updated:** `tests/unit/test_phase18_batch2.py` — all 3 FIX-087 test mocks updated.

---

## PART 3: Signal Pipeline

### Finding F04 — FIX-165c: `_in_flight_count` goes negative
- **File:** `signals/signal_processor.py` — `_process_one` (line 423) and `continue_from_gate` (line 1147)
- **Type:** BUG | **Priority:** P0 | **Status:** FIXED (both paths)
- **Description:** `_in_flight_count` was unconditionally decremented in the `finally` block, but the increment happens mid-pipeline after several checks that can raise `_PipelineReject`. If a reject occurs before the increment, the count goes negative, corrupting the TOCTOU protection.
- **Fix:** Added `in_flight_incremented = False` flag at method start. Set to `True` after increment. `finally` block only decrements when flag is `True`.

### Finding F05 — FIX-165e: `continue_from_gate` missing FIX-070 second kill-switch check
- **File:** `signals/signal_processor.py` lines 1292+ (gate path)
- **Type:** BUG | **Priority:** P0 | **Status:** FIXED
- **Description:** `_process_one` has FIX-070 — a second kill-switch + shutdown check immediately before `self._placer.place()`. The gate path was missing this TOCTOU protection. Kill switch could activate during sizing/risk/reservation but placement would proceed.
- **Fix:** Added kill-switch and shutdown checks before `self._placer.place()` in gate path, mirroring FIX-070.

### Finding F06 — `continue_from_gate` missing strategy governor check
- **File:** `signals/signal_processor.py` (gate path)
- **Type:** DESIGN_GAP | **Priority:** P1 | **Status:** DISCUSS
- **Description:** `_process_one` calls `self._strategy_governor.check_cooldown()` before placement. The gate path skips this. If a strategy hits its cooldown while a WatchEntry is pending at the gate, the cooldown is not enforced.
- **Recommendation:** Add governor check in gate path. Discuss with Rama first — may be intentional (gate entries are pre-qualified).

---

## PART 4: Order Lifecycle

### Finding F07 — FIX-165d: `_retry_limit_triple_exits` success path dead code
- **File:** `orders/order_placer.py` lines 2786-2897
- **Type:** BUG | **Priority:** P0 | **Status:** FIXED
- **Description:** The success path after `place_deferred_exits()` was lexically inside the `except` block due to wrong indentation (12 spaces instead of 8). Since every branch in the except block ends with `return`, the success path was unreachable. When exit retry succeeds, orders were placed at broker but never persisted to DB (`insert_orders_atomic`) and never tracked by `order_monitor`.
- **Impact:** Position appears unprotected — order_monitor won't poll, reconciler won't check, EOD squareoff won't know about the exit orders. Could trigger CHECK9 alerts, duplicate exits, or capital drift.
- **Fix:** Dedented success path from 12-space to 8-space indent, making it reachable after the try/except.

### Finding F08 — `_check_liquidity` bypasses adapter API
- **File:** `orders/order_placer.py` line 3074
- **Type:** RISK | **Priority:** P1 | **Status:** DOCUMENT
- **Description:** Directly calls `self._adapter._kite.quote()`, bypassing rate limiting, 429 detection, error translation, and paper mode handling. Guarded by `self._mode == "LIVE"` so no paper mode crash, but rate limiting bypass is a concern.
- **Recommendation:** Route through adapter's public API with rate limiting.

### Finding F09 — `_emergency_market_exit` uses wrong leg label
- **File:** `orders/order_placer.py` line 3007
- **Type:** INCONSISTENCY | **Priority:** P2 | **Status:** DOCUMENT
- **Description:** Uses `_LEG_SL` for emergency MARKET exit fill_map entry. This order is not a stop-loss — it's an emergency exit. Mislabeling could cause spurious OCO cancel attempts. Low severity: emergency exits are rare and cancelling a non-existent TGT is a no-op.

### Finding F10 — `get_trades()` wrong rate limit category
- **File:** `broker/zerodha_adapter.py` line 1196
- **Type:** INCONSISTENCY | **Priority:** P2 | **Status:** DOCUMENT
- **Description:** Uses `_CATEGORY_MAP["get_margins"]` as rate limit key but calls `self._kite.trades()`. Comment says intentional bucket sharing. Zero functional impact but misleading.

### Finding F11 — Order state machine, SL/TGT timing, orphan detection, CHECK9, partial fills, EOD squareoff, OCO cancellation, rate limiting: CLEAN
- **Status:** All checked and verified correct.

---

## PART 5: Database & Persistence

### Finding F12 — Database layer: CLEAN
- **File:** `core/state_store.py`
- **Status:** Thread-safe per-thread connections via `threading.local()`, WAL mode, `BEGIN IMMEDIATE` for writer serialization. Schema matches code column references (verified against `core/schema.sql`). No SQL injection risks (1 whitelisted dynamic table name in `row_count()`).

---

## PART 6: Configuration

### Finding F13 — `email_fallback_config` not wired
- **Type:** CONFIG_GAP | **Priority:** P1 | **Status:** DOCUMENT
- **Description:** `email_fallback_config` exists in config but is not wired to `TelegramNotifier` in `main.py`. If Telegram fails, there's no email fallback.
- **Recommendation:** Discuss with Rama whether email fallback is needed for production.

---

## PART 7: AGY / Antigravity

### Finding F14 — AGY: CLEAN
- **Status:** Model cascade (Tier 1: Sonnet→Pro for deep analysis, Tier 2: Flash for routine), governance boundaries all passing (CT151/152/153), automation fully working with dotenv and watchman.

---

## PART 8: Code Quality

### Finding F15 — Code quality checks: CLEAN
- **Checks performed:**
  - No bare `except:` statements (all use `except Exception`)
  - No `# TEMP` or `# TODO_TEMP` markers in production code
  - No `print()` statements in production code
  - No SQL injection risks (parameterized queries throughout)
  - Consistent use of `now_ist()` from `core.time_authority` (no raw `datetime.now()` in production)

---

## PART 9: Startup Sequence

### Finding F16 — Startup: CLEAN (from Agent B)
- **Status:** Startup sequence in `main.py` correctly initializes all components in dependency order. KillSwitch hydration, FundManager rehydration, and strategy loading all verified.

---

## PART 10: Duplicate Code

### Finding F17 — `_PRODUCT_TO_INTENT` triplication
- **Files:** `orders/order_placer.py:241`, `orders/order_reconciler.py:85`, `capital/fund_manager.py:123`
- **Type:** DUPLICATION | **Priority:** P1 | **Status:** DOCUMENT
- **Description:** Identical dict `{MIS→INTRADAY, CO→COVER_ORDER, CNC→DELIVERY, NRML→DELIVERY}` in 3 files. Comment in order_placer.py acknowledges: "Keep in lockstep with order_reconciler._PRODUCT_TO_INTENT." If any copy drifts, capital calculations silently break.
- **Recommendation:** Extract to shared constants module. Discuss with Rama.

### Finding F18 — `_is_market_hours()` duplication
- **Files:** `broker/token_monitor.py:142`, `scripts/gemini_watchman.py:118`
- **Type:** DUPLICATION | **Priority:** P2 | **Status:** DOCUMENT
- **Description:** Two independent market hours implementations. Neither uses `MarketWindows`. Low priority — different runtime contexts (daemon thread vs. standalone script).

---

## PART 11: Report Generation

This document.

---

## PART 12: Fix Prioritization

### Fixed this session (P0 BUGs):
| Fix ID   | Finding | File | Description |
|----------|---------|------|-------------|
| FIX-165a | F01     | fund_manager.py | top_up_reservation race + slm_buffer drop |
| FIX-165b | F03     | kill_switch.py | _exit_all_trades_indestructible wrong columns |
| FIX-165c | F04     | signal_processor.py | _in_flight_count goes negative (both paths) |
| FIX-165d | F07     | order_placer.py | Exit retry success path dead code |
| FIX-165e | F05     | signal_processor.py | Gate path missing kill-switch check before placement |

### Discuss with Rama before fixing (P1):
| Finding | Type | File | Description |
|---------|------|------|-------------|
| F06     | DESIGN_GAP | signal_processor.py | Gate path missing strategy governor check |
| F08     | RISK | order_placer.py | _check_liquidity bypasses adapter API |
| F13     | CONFIG_GAP | main.py | email_fallback_config not wired |
| F17     | DUPLICATION | 3 files | _PRODUCT_TO_INTENT triplication |

### Low priority / Document only (P2):
| Finding | Type | File | Description |
|---------|------|------|-------------|
| F09     | INCONSISTENCY | order_placer.py | Emergency exit uses wrong leg label |
| F10     | INCONSISTENCY | zerodha_adapter.py | get_trades wrong rate limit category |
| F18     | DUPLICATION | 2 files | _is_market_hours() duplication |

---

## PART 13: Post-Fix Testing

Test suite run after all FIX-165 changes: **2876 passed, 0 failed, 12 skipped** (388s)

---

## PART 14: Deployment

SCP to VM: **[PENDING — after test suite passes]**
