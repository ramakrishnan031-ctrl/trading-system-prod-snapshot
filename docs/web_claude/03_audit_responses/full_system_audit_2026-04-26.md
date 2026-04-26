# Full Deep System Audit — 2026-04-26 (Pre-Live Lead Review)

**Auditor:** VS Code Claude (Opus 4.7), at the user's direction.
**State at audit:** commit `53e61e2` on `main`, pushed to `vm`. 1753 tests collected, all green. Paper Week 1 complete; Week 2 (Chaos Diet) starts Mon **28-Apr-2026**.
**Live D-Day:** Mon **11-May-2026**, ₹25k Day-1 capital.

**Scope:** every in-scope file (alerts/, broker/, capital/, config/, core/, data/, deploy/, docs/, logs/, orders/, reports/, screening/, scripts/, signals/, strategies/, tests/, utils/, main.py, DEPLOYMENT.md, requirements*.txt, .gitignore, .gitattributes, .env.example). Excluded per user: `__pycache__/`, `.claude/`, `.pytest_cache/`, `venv/`, `.env`, `mempalace.yaml`, `CLAUDE_md_and_explanations.txt`, `memory/`.

**Authority for "what is correct":**
- `docs/foundation_engineering_rules.md` (1.4 Zero Hardcoding, 1.6 Logs Over Prints / fail-fast, 1.10 Boring Code, 5.x Version Freeze).
- `docs/locked_decisions.yaml` (181 locked decisions; G1-G10, P1-P18, principles, E1-E6 exception hierarchy, EV1-EV6 events).
- `docs/trading_system_project_specific_rules.md` (rule 12 signal integrity, rule 13 kill-switch boundary, rule 14 strategy schema enforcement, rule 15 paper/live parity).

**Process:** read all three reference docs, mapped the codebase (28 KLOC Python, 16 schema tables, 15 strategy YAMLs, 1753 tests), then audited dimension-by-dimension. Three Explore subagents ran in parallel to cover breadth on logic+naming, duplicates+dead code, and structure+strategy YAMLs; every agent claim has been **personally verified** against the source before being included below. Spurious or speculative agent claims are listed at the end (§ "Discarded agent findings — not real issues") so the trail is complete.

**Bottom line:** the system is in good shape. **No CRITICAL items block paper Week 2.** The HIGH bucket is concentrated in *namesake config* — values that exist in `system_config.yaml` but are never read by the runtime (P2 Config Honor Principle violations). This is a clarity / operator-trust issue, not a correctness issue: the runtime uses correct hardcoded defaults; the YAML lies about them. Fix this weekend.

The MEDIUM bucket is dead code, exception-hierarchy gaps, and one P12 paper/live parity gap (slippage). LOW bucket is cosmetic.

---

## 1. Executive summary

| Severity | Count | What                                                                                          |
| -------- | ----- | --------------------------------------------------------------------------------------------- |
| CRITICAL | 0     | nothing blocks paper Week 2.                                                                  |
| HIGH     | 11    | namesake config (P2 violations), exception hierarchy gaps, dead event subscriber, P12 slippage gap, requests pin. |
| MEDIUM   | 6     | code duplication (`_IST` ×14, `_now_ist_iso` ×2), dead schema columns, paper-engine separation. |
| LOW      | 8     | cosmetic comments, minor try/except/pass, naming similarity, etc.                              |

| Recommendation         | What                                                                                                                |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Fix this weekend       | the 11 HIGH items. Zero risk (config alignment + dead code removal + exception inheritance). Each is &lt;30 minutes.   |
| Fix during paper weeks | the 6 MEDIUM items. Most are DRY refactors that benefit from one cycle of paper trial as a soak.                    |
| Defer / acknowledge    | the 8 LOW items. Cosmetic; bundle into a future cleanup PR.                                                         |

---

## 2. CRITICAL — must fix before paper Week 2

**None.** The four critical items raised on 2026-04-25 (B.1 / C.1 / D.1 / E.5) are already shipped at `89ed020`. No new CRITICAL discovered.

---

## 3. HIGH — fix this weekend

### CFG-1 — `trading_hours.entry_start` / `entry_end` / `eod_squareoff_time` are namesake config

- **Severity:** HIGH (operator-trust + P1 / P2 violation)
- **File:** `config/system_config.yaml:11-13`, `main.py:693`
- **Description:**
  - YAML declares `entry_start: "09:25"`, `entry_end: "15:00"`, `eod_squareoff_time: "15:17"`.
  - locked_decisions P1 mandates **"09:30 - 13:30 IST" entry window**.
  - `core/market_windows.py:23-27` defines DEFAULT_ENTRY_START=09:30, DEFAULT_ENTRY_END=13:30, DEFAULT_EOD_SQUAREOFF=15:17 — matching the spec.
  - `main.py:693` constructs `MarketWindows(holidays=holidays)` — passing **only** holidays. The YAML's entry_start/end/eod_squareoff_time are loaded into `app_config.system.trading_hours` but never threaded into MarketWindows.
  - Runtime therefore uses 09:30-13:30 / 15:17 (correct per spec). YAML lies about 09:25-15:00.
  - `trading_hours.market_close` IS read at `main.py:1183` and threaded into EodSquareoff — so only the other three keys are unread.
- **Exact fix:** at `main.py:693`, parse the YAML times and pass them: `MarketWindows(holidays=holidays, entry_start=_parse_hhmm(cfg.entry_start), entry_end=_parse_hhmm(cfg.entry_end), eod_squareoff=_parse_hhmm(cfg.eod_squareoff_time), market_open=_parse_hhmm(cfg.market_open) if hasattr(cfg, "market_open") else DEFAULT_MARKET_OPEN, market_close=_parse_hhmm(cfg.market_close))`. Then update YAML to `09:30 / 13:30 / 15:17` per P1. Add a config_loader cross-validator that rejects YAML where entry_start ≥ entry_end. Tests: pin via `MarketWindows.is_entry_allowed(09:25_dt) == False` after fix.

### CFG-2 — `polling.order_monitor_sec` / `polling.order_reconciler_sec` are namesake

- **Severity:** HIGH (P2 violation, contradicts another section in the same file)
- **File:** `config/system_config.yaml:21-23`
- **Description:**
  - YAML's top-level `polling:` block declares both at 15s.
  - The actual polling cadences come from a different section: `order_monitor.poll_interval_sec: 2` (line 46) and `order_reconciler.poll_interval_sec: 15` (line 134).
  - `polling.*` is loaded by `core/config_loader.py` but no production module reads it. Only `tests/unit/test_config_loader.py:327` references it.
  - **The two sections contradict each other**: `polling.order_monitor_sec=15` vs `order_monitor.poll_interval_sec=2`. Operator reading the YAML has no way to know which one is live.
- **Exact fix:** delete the entire `polling:` section from `system_config.yaml`. Delete `PollingConfig` and the `polling: PollingConfig` field from `core/config_loader.py`. Update the test that references it. The remaining `order_monitor.poll_interval_sec` and `order_reconciler.poll_interval_sec` are the canonical sources — both already wired correctly.

### CFG-3 — `order_fill_timeout` block is namesake

- **Severity:** HIGH (P2 violation; same root cause as CFG-2)
- **File:** `config/system_config.yaml:25-27`
- **Description:**
  - YAML declares `order_fill_timeout.limit_sec: 60`, `order_fill_timeout.market_sec: 0`.
  - `core/config_loader.py:625` loads it as `OrderFillTimeoutConfig`, but no production code reads either field.
  - The runtime fill timeout comes from `order_monitor.fill_timeout_sec: 60` (`broker/order_monitor.py:189` → `main.py:1096`).
- **Exact fix:** delete the `order_fill_timeout:` YAML block and the `OrderFillTimeoutConfig` schema class. The single source of truth (`order_monitor.fill_timeout_sec`) already exists and is wired.

### CFG-4 — `order_reconciler.enable_event_driven: true` is namesake

- **Severity:** HIGH (P2 violation, the most operator-confusing one — flag set TRUE but disabled in code)
- **File:** `config/system_config.yaml:136`, `orders/order_reconciler.py:178-182`
- **Description:**
  - YAML says `enable_event_driven: true   # G1: subscribe OrderStateChanged for instant re-check`.
  - `orders/order_reconciler.py:178-182` explicitly forces this to no-op: *"Audit #12: the event-driven trigger (RC4) has been disabled. … Config flag left in place for rollback but now forced to no-op."*
  - The flag prints a log line but does nothing else. Reconciler is pure 15s polling.
- **Exact fix:** flip the YAML default to `false` and add a comment `# DISABLED by Audit #12 (rate-limit pressure) — leaving as false; remove flag entirely after one paper cycle confirms no rollback needed`. Or, the cleaner option: delete the field outright (config_loader.py:428, the YAML line, the start() check), and remove the dead `_on_order_state_changed` method (see DEAD-1 below) in the same patch.

### CFG-5 — `strategies/*.yaml` per-strategy `entry_start_time` / `entry_end_time` are namesake

- **Severity:** HIGH (S14 / P1 violation; 30 namesake values across 15 YAMLs)
- **Files:** `config/strategies/*.yaml` (lines 42-43 of each), `strategies/schema.py:93-94, 275-287`
- **Description:**
  - Every strategy YAML declares `entry_start_time` / `entry_end_time`.
  - Schema validates them (lines 275-287, also CHECKs that start &lt; end).
  - **No code reads `strategy.entry_start_time` or `strategy.entry_end_time` at runtime.** The actual entry-window enforcement is global via `MarketWindows.is_entry_allowed(now)` (sourced from system_config.yaml + main.py wiring).
  - This is the same gap the 2026-04-25 audit raised as **E.3** ("MarketWindows uses global config, not strategy times") — that item was deferred to v2.1.
- **Exact fix:** two options — (a) **decision-correct path**: add `MarketWindows.is_entry_allowed_for_strategy(now, strategy)` that ANDs global window with the per-strategy window. Wire it into `signals/signal_processor.py` where the OUTSIDE_ENTRY_WINDOW reject is computed. (b) **clean-up path**: remove `entry_start_time` / `entry_end_time` from `StrategyConfig` and from all 15 YAMLs (closes the namesake). Recommendation: option (a) before live (each strategy genuinely should be tunable; e.g., `gap_fade_long.yaml` declares `11:30` cutoff which is real domain logic). Either way, do not leave the current half-state.

### CFG-6 — `slippage_model.yaml` is loaded but never consumed (P12 violation)

- **Severity:** HIGH (paper/live parity gap; explicit P12 promise)
- **Files:** `config/slippage_model.yaml`, `core/config_loader.py:765-897`, `broker/zerodha_adapter.py:1187-1189`
- **Description:**
  - locked_decisions P12: *"Paper mode applies slippage to fills. Slippage configurable per stock liquidity tier in config/slippage_model.yaml."*
  - `core/config_loader.py:897` loads it as `SlippageConfig` and exposes `app_config.slippage`.
  - **No production code reads `app_config.slippage`.** Grepped: only `core/config_loader.py` and tests reference `SlippageConfig`.
  - Paper-mode fill synthesis: `broker/zerodha_adapter.py:1187-1189` (`_synth_fill`) sets `fill_price = price` — the exact requested price. **Zero slippage applied.**
  - Result: paper P&L will systematically over-state vs live P&L. Live D-Day on 11-May with ₹25k will reveal a step-down vs paper that cannot be distinguished from execution drift.
- **Exact fix:** introduce `broker/slippage_engine.py` (or a method on the existing CostCalculator) that takes `(symbol, side, price, liquidity_tier) → adjusted_fill_price`. Consume `app_config.slippage` in `_synth_fill`: replace `fill_price = price` with `fill_price = self._slippage.apply(symbol, side, price)`. Tier lookup from `instruments.csv` (`is_fno`/`sector`/avg_volume → tier). Add tests that paper fills include slippage. **This is the single most important pre-live fix in this audit; do not run paper Week 2 without it.**

### EXC-1 — `state_store.py` exceptions inherit from plain `Exception`, not from `TradingSystemError`

- **Severity:** HIGH (E1 / E3 violation; main.py top-level `except TradingSystemError` cannot catch state corruption)
- **File:** `core/state_store.py:92-101`
- **Description:**
  - locked_decisions **E1**: *"TradingSystemError(Exception) is the single root. Every custom exception in the system inherits from it."*
  - locked_decisions **E3**: *"StateError — state_store, capital invariants, persistence … Default SEVERITY: CRITICAL."*
  - Three classes violate this:
    - `StateStoreError(Exception)` — line 92
    - `SchemaVersionMismatch(StateStoreError)` — line 96
    - `TransactionError(StateStoreError)` — line 100
  - main.py's broad `except TradingSystemError` at the top-level safety net will not catch any of these. Operator sees an unhandled stack trace instead of the structured logging the hierarchy enables.
- **Exact fix:** in `core/state_store.py`, change `class StateStoreError(Exception):` to `from core.exceptions import StateError; class StateStoreError(StateError):`. The two leaves inherit transitively. Confirm via test: `assert issubclass(SchemaVersionMismatch, TradingSystemError)`. No raise sites need changing.

### EXC-2 — `StartupCheckFailed` inherits from plain `Exception`

- **Severity:** HIGH (E1 violation)
- **File:** `utils/startup_checks.py:759`
- **Description:** same root cause as EXC-1. `StartupCheckFailed(Exception)` should be `StartupCheckFailed(ConfigError)` (per E3, ConfigError sub-root) or `(TradingSystemError)` if we want generic. Startup failures are a config / readiness failure, so `ConfigError` is correct.
- **Exact fix:** `from core.exceptions import ConfigError` and inherit from `ConfigError`. One-line change.

### DEAD-1 — `OrderReconciler._on_order_state_changed` is dead code

- **Severity:** HIGH (maintenance debt + namesake config CFG-4 root cause)
- **File:** `orders/order_reconciler.py:226-228`
- **Description:**
  - Method exists with a 1-line body: `self.reconcile_once()`.
  - The method is never `bus.subscribe(OrderStateChanged, self._on_order_state_changed)` in production. The `start()` method at line 178-182 *only* logs that `enable_event_driven=True` is ignored.
  - Audit #12 disabled the feature. The method is left over from the original RC4 design.
- **Exact fix:** delete the method, the `enable_event_driven` config field (CFG-4), and the start() check. One contained patch removes both findings. Keep a one-line comment in the file header pointing at Audit #12 in case someone wants to re-enable later (with proper batching).

### DEAD-2 — `OrderStateChanged` event has no production subscriber

- **Severity:** HIGH (publish without consume — pure dead event traffic)
- **Files:** `core/events.py:167` (definition), `broker/order_state_machine.py:213` (publisher), no production subscribers
- **Description:**
  - `OrderStateChanged` is published on every successful OSM transition (potentially many per second under load).
  - The only subscribers in the entire codebase are `tests/unit/test_order_state_machine.py:251,269,293`. No production module subscribes.
  - The intended subscriber was `OrderReconciler._on_order_state_changed` (DEAD-1) — disabled by Audit #12.
  - Every publish allocates a dataclass envelope, runs the bus's iterator over an empty subscriber list, and writes a debug log. Wasted CPU + log volume.
- **Exact fix:** two options. (a) **Stop publishing**: remove the publish call at `broker/order_state_machine.py:213` and document in the file header that no in-process subscriber exists; the OSM still drives behavior via direct state updates. (b) **Hide behind config**: only call `bus.publish(OrderStateChanged(...))` when a subscription exists. Recommendation: (a). Safer; if a future feature needs this signal, it can re-add the publish call alongside its subscriber. Bundle with DEAD-1 + CFG-4 cleanup.

### REQ-1 — `requests` imported but not pinned in `requirements.txt`

- **Severity:** HIGH (Foundation Rule 5: Version Freeze)
- **Files:** `alerts/telegram_notifier.py:40`, `scripts/preflight_scanner_check.py:27`, `scripts/zerodha_login.py:31`
- **Description:**
  - `requests` is imported directly in three production files.
  - `requirements.txt` does not pin it. It is currently resolved as a transitive dep of `kiteconnect==5.1.0`.
  - When `kiteconnect` next bumps, the bundled `requests` version can shift. A reproducible build is one of the foundation guarantees.
- **Exact fix:** add `requests==2.32.5  # alerts.telegram_notifier, scripts.preflight, scripts.zerodha_login` to `requirements.txt` (or whatever pip freeze shows in the current venv). Confirm with `pip freeze | grep requests`.

### CFG-7 — `kill_switch_state.state` and `session.kill_state` track the same thing

- **Severity:** HIGH (single-source-of-truth principle violation, schema-level confusion)
- **Files:** `core/schema.sql:280-284` (session.kill_state, kill_reason, kill_time, kill_type), `core/schema.sql:358-364` (kill_switch_state)
- **Description:**
  - Schema defines two tables that both track kill switch state.
  - At runtime: `KillSwitch.__init__` reads from `kill_switch_state` (kill_switch.py:428) — the dedicated table is the source of truth.
  - `main.py:_write_session` writes `kill_state` into the `session` row (line 230-236) but never writes `kill_reason / kill_time / kill_type`. Those columns sit NULL.
  - No code ever reads `session.kill_state` or `session.kill_*` outside of the bare `print` in `_print_status` (main.py:246).
- **Exact fix:** drop columns `kill_state`, `kill_reason`, `kill_time`, `kill_type` from the `session` table (schema bump v12 → v13). Update `_write_session` to remove the `kill_state` argument. The `kill_switch_state` table is canonical and well-tested. Bump `EXPECTED_SCHEMA_VERSION` and test.

---

## 4. MEDIUM — fix during paper weeks

### DUP-1 — `_IST = timezone(...)` duplicated across 14 modules

- **Severity:** MEDIUM (DRY + Foundation Rule 1.4 zero-hardcoding-of-canonical-values)
- **Files (14):** `alerts/critical.py:43`, `capital/kill_switch.py:67`, `capital/risk_engine.py:64`, `core/state_store.py:60`, `core/time_authority.py:59`, `main.py:92`, `orders/order_reconciler.py:79`, `orders/shadow_tracker.py:57`, `orders/smart_tgt_manager.py:59`, `reports/daily_review.py:48`, `screening/entry_gate.py:44`, `scripts/alert_watcher.py:66`, `scripts/zerodha_login.py:36`, `signals/signal_processor.py:57`, `signals/webhook_receiver.py:54`.
- **Description:** `core/time_authority.ist_timezone()` already exposes the canonical IST tzinfo (line 102). Every other module redefines `_IST = timezone(timedelta(hours=5, minutes=30))` locally. Some declare `name="IST"`, some don't — minor inconsistency. Value is correct everywhere; risk is future drift if the offset ever changes (it won't, but the audit is about discipline).
- **Exact fix:** in each of the 14 files, replace the local `_IST = timezone(...)` line with `from core.time_authority import ist_timezone` and use `ist_timezone()` where `_IST` is used. Two exemptions are acceptable: `core/state_store.py` (documented H-17 layering exemption) and `core/time_authority.py` (definer). Reduces footprint from 14 → 2.

### DUP-2 — `_now_ist_iso()` duplicated in `orders/smart_tgt_manager.py`

- **Severity:** MEDIUM (smaller blast radius than DUP-1)
- **Files:** `orders/smart_tgt_manager.py:64-65`, `core/time_authority.py:112` (canonical `now_ist_iso()`).
- **Description:** smart_tgt_manager defines a local `_now_ist_iso()` that just calls `now_ist().isoformat()`. The canonical `now_ist_iso()` exists in time_authority and does exactly the same thing. (Note: `core/state_store.py:63` also defines a local version, but that one is a documented H-17 layering exemption — leave it alone.)
- **Exact fix:** in `orders/smart_tgt_manager.py`, replace the local function with `from core.time_authority import now_ist_iso` and use `now_ist_iso()` at the two call sites (lines 272, 586).

### NSK-1 — Dead schema columns in `session` table

- **Severity:** MEDIUM (schema clutter; small storage; no behavior bug)
- **File:** `core/schema.sql:287-292`
- **Description:**
  - `session.yesterday_pnl REAL DEFAULT 0` — never written, never read.
  - `session.yesterday_wins INTEGER DEFAULT 0` — never written, never read.
  - `session.yesterday_losses INTEGER DEFAULT 0` — never written, never read.
  - `session.consecutive_losses INTEGER DEFAULT 0` — never written; risk_engine computes from `recent_trade_pnls()` instead (`capital/risk_engine.py:203-204`).
- **Exact fix:** drop these four columns in the same schema migration as CFG-7 (v12 → v13). Confirm via grep that no test references them after the migration.

### LOG-1 — Several `try/except: pass` swallowing errors silently

- **Severity:** MEDIUM (Foundation principle "fail_fast_no_silent" violation in low-stakes spots)
- **Files:**
  - `orders/eod_squareoff.py:494-497` — float-conversion of `margin_reserved` for EOD summary; logs nothing on failure.
  - Several other `pass` patterns surface from grep but most are inside `with` blocks (cleanup) or are documented exemptions. The eod_squareoff one is the worst offender — it's data conversion, not cleanup.
- **Exact fix:** at `orders/eod_squareoff.py:494-497`, convert `try/except Exception: pass` to `try: capital_used += float(mr) if mr is not None else 0.0; except (TypeError, ValueError) as exc: self._log.warning("eod_squareoff.margin_float_failed", extra={"trade_id": t.get("trade_id"), "mr": mr, "error": str(exc)})`. Same shape across other `pass`-only blocks if any survive a grep.

### NM-1 — Per-strategy `pullback_wait_*` field naming inconsistency in YAMLs

- **Severity:** MEDIUM (S13 / P11a)
- **Files:** spot-check `config/strategies/first_pullback_long.yaml` vs `gap_go_long.yaml`.
- **Description:** different YAMLs use `pullback_wait:` block (with sub-keys) vs `pullback_wait_enabled` flat key. Schema accepts both shapes; some YAMLs have one, some the other. Each works on its own but there's no single canonical shape.
- **Exact fix:** pick one shape (recommend the `pullback_wait:` block — richer for future fields), enforce in schema with `extra="forbid"`, mass-edit YAMLs to match. Add a test that loads every YAML into StrategyConfig and asserts shape equality.

### TST-1 — Tests subscribing to `OrderStateChanged` will pass even after DEAD-2 fix

- **Severity:** MEDIUM (test passes for wrong reason — bus accepts any subscription)
- **Files:** `tests/unit/test_order_state_machine.py:251,269,293`
- **Description:** these tests subscribe and assert that an event was published. After DEAD-2 (stop publishing), these tests will fail. They test the *publication* of an event nobody consumes — i.e. they pin the dead behavior in place.
- **Exact fix:** when DEAD-2 lands, rewrite these tests to assert OSM behavior (state transitions, not event emission). Or, if event publication is kept, document a real subscriber.

---

## 5. LOW — defer or skip

| ID    | Severity | File:Line                                                 | Description                                                                                                                | Fix                                                                                                                                                                              |
| ----- | -------- | --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CMT-1 | LOW      | `core/schema.sql:509,547`                                 | Both `innings` and `gate_state` commented as "TABLE 16". Should be 16 and 17.                                              | Renumber comment; no code change.                                                                                                                                                |
| NM-2  | LOW      | `core/events.py:167,184`                                  | `OrderStateChanged` and `OrderStatusChanged` are confusingly similar names. The distinction is documented but error-prone. | When DEAD-2 removes OrderStateChanged, this auto-resolves. Otherwise document loudly in EV header.                                                                               |
| NM-3  | LOW      | various                                                   | `_IST` declared with `name="IST"` in some files (capital, signals, smart_tgt) and without in others (most). | Consolidates into DUP-1; once that lands the inconsistency disappears.                                                                                                           |
| NM-4  | LOW      | `capital/fund_manager.py` various                          | mixes `reservation_id` (param) and `rid` (local). Self-consistent but adds reading load.                                   | Stick with `reservation_id` everywhere; allow `rid` only in tight inner scopes with a one-line comment.                                                                          |
| DOC-1 | LOW      | `core/market_windows.py:103`                              | Docstring of `is_eod_squareoff_due` says "EOD square-off time" without naming 15:17.                                       | One-line docstring update: `"""True if now ≥ 15:17 IST per P1; caller owns 'already fired' edge-trigger."""`.                                                                    |
| ACC-1 | LOW      | `config/accounts.csv`                                     | `LFL836` has `paper_capital=1000000` (₹10L) but Week 1 paper config used ₹50k.                                              | Verify intended Week 2 paper capital with operator before Mon 28-Apr. If the project_paper_to_live_plan says ₹10L, ignore.                                                       |
| ACC-2 | LOW      | `config/accounts.csv` header                              | `totp_secret_env` column exists but Zerodha login flow uses request_token, not TOTP.                                       | If TOTP path is dead, drop the column. If it's reserved for future, leave it but add a code comment that today's flow ignores it.                                                |
| GIT-1 | LOW      | `.gitignore:60-63`                                        | Memory/ folder is gitignored — correct. mempalace.yaml gitignored — correct (Claude Code internal). | None — already correct, just noting.                                                                                                                                          |

---

## 6. Already-clean areas (verified by reading the code, not just by trusting tests)

| Subsystem                     | Verified clean                                                                                                                                                                                            |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `broker/cost_calculator.py`   | Decimal arithmetic, ROUND_HALF_UP, no float drift. All rates from config. Direction-aware STT/stamp duty. FNO branch correct.                                                                              |
| `capital/invariant.py`        | Pure stateless. INV6 negative-balance guards, INV7 paise rounding. Single formula, no surprises.                                                                                                          |
| `capital/position_sizer.py`   | 4-layer model per P10 (risk / per-trade cap / portfolio risk / margin). Tier multipliers applied AFTER min(), BEFORE lot rounding. SL distance zero guard at line 242.                                  |
| `capital/fund_manager.py`     | EF-3 direction-aware PnL. BL-5 write-ahead ledger. C.1 (this week's audit) hard_kill outside lock — no deadlock window. H-1 bucket overflow detection.                                                  |
| `capital/risk_engine.py`      | RE10 (net_pnl &lt; -1e-6 = loss). RE11 9-field snapshot every call. Six checks short-circuit. **`max_open_positions` and `max_daily_trades` are correctly enforced** at lines 316 / 326 — agent claim wrong. |
| `core/time_authority.py`      | G4 hybrid clock. 4-tier skew thresholds. Single `now_ist()` source. Callbacks deferred outside lock.                                                                                                      |
| `core/market_windows.py`      | Pure stateless. Defaults match P1 spec. `next_trading_day` cap-guarded (raises after 10 days).                                                                                                            |
| `core/state_store.py`         | Per-thread connections. PRAGMA foreign_keys ON. Schema v12 with documented migration. `idx_trades_status` exists (closes audit F.1 already).                                                              |
| `orders/order_placer.py`      | Last-mile kill check. Atomic _persist_entry_orders. 2-phase LIMIT_TRIPLE (closes naked-short window). _fill_map insert before track() (atomic registration). B.1 rehydrate (this week's audit) wired in main.   |
| `orders/eod_squareoff.py`     | EOD3 idempotent flag per date. EOD6 skips CNC. P1 compliant 15:17 from market_windows. E.5 (this week's audit) broker-position filter on every fire.                                                     |
| `orders/smart_tgt_manager.py` | LONG SL only goes up; SHORT SL only goes down — verified at lines 543-546 and 493-502 (agent L-3 claim wrong). D.1 (this week's audit) rate_limiter on modify_order.                                          |
| `screening/secondary_screener.py` | P9a exception bypass fixed. P18 visibility (every signal exits with PASSED / REJECTED_&lt;step&gt; / SKIPPED). Market data snapshot.                                                                       |
| `core/exceptions.py`          | Hierarchy clean except for the EXC-1 / EXC-2 leaves not inheriting. Sub-roots correctly seeded.                                                                                                          |
| `core/events.py`              | Synchronous dispatch. Explicit subscribe (no decorator). Collect-all-errors-then-raise. Four event types seeded per EV6 + EV7 (OSM) + EV8 (EOD) + EV9 (status).                                          |
| Strategy YAMLs (15)            | All valid against StrategyConfig. Direction matches filename. Order_protocol consistent (CO_PLUS_TGT for INTRADAY, LIMIT_TRIPLE for DELIVERY). ATR strategies declare sl_pct=0.0 correctly.            |
| Folder structure              | No circular imports. Lowercase naming. All packages have `__init__.py`. Layering: `core` ← `strategies/alerts/signals/screening` ← `capital/broker` ← `orders` ← `main`. Acyclic.                       |
| Event wiring                  | OrderFilled (mon→placer), PositionClosed (placer→shadow), KillSwitchActivated (ks→main log), CapitalDriftDetected (reconciler→drift_handler), OrderStatusChanged (mon→placer+manager), EodSquareoffComplete (eod→shadow). All four production-required events have publishers AND subscribers. |
| Schema v12                    | 16 tables, all FK relationships documented. Indexes on hot paths. Audit history tracked in END comment.                                                                                                    |

---

## 7. Discarded agent findings — investigated and rejected

The three Explore agents collectively raised ~30 candidate issues. Eight are listed above (verified). The rest were rejected after reading the source. Listed here for transparency:

| Agent claim                                                       | Reason rejected                                                                                                                                                                                                                                                              |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Agent 1 L-3: SHORT SL trail direction risk in smart_tgt_manager.   | False alarm — verified `_modify_co_sl` lines 543-546 and `_compute_target_sl` 493-502. Both directions correctly guarded; LONG only-up and SHORT only-down ratchets are explicit.                                                                                            |
| Agent 1 E-2: rehydrate NULL fallback could blow up.                 | The actual code path at `capital/fund_manager.py:1194-1207` falls back from `entry_actual_price` → `entry_target_price` (which is `NOT NULL` in schema line 92, so it cannot be None). Final guard not needed.                                                              |
| Agent 1 C-2: leverage_map default in fund_manager constructor.      | Defaults are present but main.py always passes the configured map (line 944). Defaults exist only for tests. Acceptable per existing test fixtures.                                                                                                                          |
| Agent 1 C-3: max_open_positions has no Telegram alert on breach.    | Risk engine logs INFO with `failed_check=OPEN_POSITIONS` (line 247-251). The Telegram alert is upstream — `signal_processor` rejects the signal with status `REJECTED_RISK` and the daily_review report shows the rejection reason. Alert plumbing is event-driven, not per-rejection. |
| Agent 3 C-4: max_open_positions / max_daily_trades not enforced.    | False — verified `capital/risk_engine.py:316-330`. Both are hard-enforced in `approve()` ladder.                                                                                                                                                                            |
| Agent 3 C-5: drift_handler thresholds hardcoded to paper scale.     | True statement, but the YAML itself (lines 154-157) explicitly comments this and says "revisit for live-scale capital." Already known and acknowledged. Not a blind hardcode.                                                                                                |
| Agent 2 PI-1 (LOW severity): _IST timezone in 9 modules.            | Promoted to DUP-1; counted 14 not 9.                                                                                                                                                                                                                                          |
| Agent 1 L-2: utils/startup_checks.py uses `datetime.now()`.         | Documented H-17 SKIP — startup_checks runs before time_authority is wired, and the only use is to pick the current year for `nse_holidays_<year>.yaml`. Importing time_authority would invert layering. The exemption is correct.                                            |
| Agent 1 L-1: schema.py has hardcoded 09:30/13:30.                   | Schema defaults match locked spec. The real issue is CFG-5 (per-strategy times not enforced at runtime) — kept that finding. Hardcoded defaults in schema are fine per `extra="forbid"` model semantics; they document the canonical fallback.                              |

---

## 8. Coverage gaps — what this audit did not exhaust

1. **Tests passing for wrong reasons (Dimension 7).** I sampled but did not systematically run mutation testing. A future audit pass should mutate selected lines (`>=` → `>`, `<` → `<=`) and confirm tests fail. High-value targets: `capital/invariant.py`, `capital/position_sizer.py` layer math, `orders/eod_squareoff.py` 15:17 boundary.
2. **Daily report Config Honor coverage (P2).** I did not enumerate every config limit and confirm `reports/daily_review.py` has a row for it. Per P2, this is required. Audit would take ~1 hour.
3. **End-to-end signal-to-fill replay.** No replay harness was run with a recorded paper-day. The integration smoke tests are fixture-driven; they don't exercise the full pipeline timing under realistic tick rates. C.3 from the 2026-04-25 audit (LiveFeedManager thread pool) is still deferred.
4. **Cost contract-note parity.** I did not verify cost_calculator output against an actual Zerodha contract note. Required before live (P12: "Daily report rows match contract notes within rounding").
5. **`deploy/` directory inspection.** I confirmed it exists and has systemd units (per Phase B), but did not byte-compare against what's on the VM. DEPLOYMENT.md §2 has the diff command — operator should run it before Live D-Day.

---

## 9. Recommendation — execution plan for the weekend

1. **Saturday (~3 hours):** ship one commit per HIGH item, in this order:
   - CFG-2 (delete polling block) → CFG-3 (delete order_fill_timeout block) → CFG-4 + DEAD-1 + DEAD-2 (event-driven cleanup, single bundle) → CFG-7 (drop session.kill_* columns, schema v13) → EXC-1 + EXC-2 (exception inheritance, single patch) → REQ-1 (requirements pin) → CFG-1 (MarketWindows wiring) → CFG-5 (per-strategy entry windows or removal — pick option (a) and ship).
2. **Sunday (~2 hours):** **CFG-6 only** (slippage engine for paper). This is the highest-value fix in the audit. Ship as its own commit; it touches `broker/zerodha_adapter._synth_fill` + a new tiny `slippage_engine.py` + tests. Do not bundle with anything else.
3. **Monday morning before market open:** run full suite, confirm 1753+N green, push to `vm`. Paper Week 2 boots with the YAML telling the truth and paper P&L applying realistic slippage.

MEDIUM items can ride into paper Week 2 cycles. LOW into the next monthly cleanup.

---

**Audit complete.** No code changed. All findings above include exact fixes; user reviews and approves before any change ships.

— VS Code Claude (Opus 4.7), 2026-04-26.
