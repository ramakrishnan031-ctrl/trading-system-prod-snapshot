# F1 — Populate trades.sector at INSERT + gate-8 OBSERVE mode — 16-Jul-2026

**Status:** IMPLEMENTED + TESTED (local). **NOT pushed. NOT deployed.** Deploy + the observe→enforce
flip are the OFF-MARKET, Rama-gated Phase-3 runbook (§4). Touches the trade-INSERT path + a live
capital gate — shipped **behaviour-neutral** (gate-8 defaults to observe/log-only).

**Root cause (from the register B1 / Finding-1):** `trades.sector` was NULL on 100% of rows →
`StateStore.sector_exposure(sector)` (`... AND sector=?`) always summed 0 for the resting book →
the gate-8 SECTOR_EXPOSURE 40% concentration cap never summed open positions (a control that
reported PASS while doing nothing). **The insert path was already wired** (`create_trade` accepts +
writes `sector`); the caller hardcoded `sector=None`.

---

## Phase 1 — INVESTIGATE-FIRST (read-only) — RESULT: **PASS → implement**

### (a) Canonical sector source — SINGLE, AUTHORITATIVE, STABLE (for the live path)
`InstrumentCache.sector(symbol)` (`core/instrument_cache.py:257`) — reads the `sector` column of
`instruments.csv`, sourced from the NSE index-member CSVs (`scripts/refresh_instruments._load_sector_map`
over `config/reference_data/index_members/*.csv`). Never raises; returns `"UNKNOWN"` for a miss or a
blank. **This is exactly the source gate-8 uses live:** `main.py:2199`
`sector_lookup_fn=lambda sym: instrument_cache.sector(sym)` → `RiskEngine._resolve_sector`.
⚠️ **Two non-live discrepancies recorded (NOT fixed — out of scope, not the live path):**
- `capital/risk_engine.py:123` — an alternate factory wires `instrument_cache.sector_for(s)`, a method
  that does **not** exist on InstrumentCache (would raise → `_resolve_sector` catches → `"UNKNOWN"`).
  The LIVE engine is built directly at `main.py:2188` with the correct `sector()`, so this is a latent
  bug in an unused builder. **Recorded for a future cleanup.**
- `signals/signal_processor._sector_for` (`:1340`) looks for `sector_for`/`get_sector` (neither exists)
  → always returns `"UNKNOWN"`. It sets the *signal's* sector (dead → always UNKNOWN); not the trade
  insert. **Recorded.** The fix does NOT use it.

⇒ No ambiguity / no conflicting source **for the live path**. STOP-gate PASSES.

### (b) Availability at insert — YES
The prod insert is `orders/order_placer.py:890 → order_manager.create_trade(...)`. OrderPlacer holds
`self._instrument_cache` (wired at `main.py:2371 set_instrument_cache`), so `instrument_cache.sector`
is available exactly where the trade row is written.

### (c) Completeness — UNKNOWN is a real, handled bucket
`sector()` returns `"UNKNOWN"` for any symbol not in a tracked index or with a blank sector. The exact
live UNKNOWN proportion is a VM datum (measured during the observe soak); the data-quality alert (§3.1)
is the safety net for it.

### (d) Gate-8 NULL/UNKNOWN handling — fail-SAFE to a bucket (not fail-open, not fail-closed)
`_resolve_sector` → `"UNKNOWN"` on any fault. `sector_exposure("UNKNOWN")` sums trades stored as
`"UNKNOWN"`. Writing the string `"UNKNOWN"` at insert (never NULL) makes the resting-book bucket match
the live candidate's resolution — consistent, and pooled only with other UNKNOWNs.

### (e) Impact simulation (informational)
The cap binds when `effective_sector_margin + this_trade > 40% × total`. At the current book size
(~₹198 margin/position; peak all-sector margin ever ₹2,742.91 « ₹3,957 cap) it would need ~4+
concurrent same-sector positions to approach 40% → WOULD_REJECT events are expected to be **rare** in
the observe soak (matches the deploy-prediction "gate-8 sector reject ≈0"). The larger unknown is the
UNKNOWN proportion → the DQ alert measures it.

### Freeze invariant — VERIFIED
No production code does `UPDATE trades SET sector`; the row is written once at insert and
`sector_exposure` reads the stored value → **frozen for the trade's life** (no intraday re-lookup /
reclassification of an open position).

---

## Phase 2 — Implement + Test (local; NO deploy)

### 3.1 Populate trades.sector at INSERT (root-cause, no schema change)
- `orders/order_placer.py:895` `sector=None` → `sector=self._resolve_trade_sector(symbol)`.
- New `_resolve_trade_sector(symbol)` — resolves via `self._instrument_cache.sector(symbol)` (the SAME
  source as gate-8), returns `"UNKNOWN"` on miss / no-cache / lookup error (never raises → placement
  is never broken by sector data). **Frozen** on the row (create_trade already writes-once).
- **UNKNOWN bucket:** the string `"UNKNOWN"` — never NULL, never pooled into a real sector.
- **Data-quality alert (ChatGPT Q2):** in-memory session counters + a one-shot WARNING (Telegram via
  the existing notifier, else log-only) when the UNKNOWN fraction of inserts exceeds
  `risk.sector_unknown_alert_pct` (default 0.20), after a ≥10 min-sample. Never breaks placement.
- **Parity:** the resolution is mode-agnostic (`instrument_cache.sector` is identical paper/live); the
  trade's `mode` field is separate. No mode-branch.

### 3.2 Gate-8 OBSERVE mode (ships WITH 3.1)
- New `risk.sector_cap_mode: observe | enforce` (default **observe**) — `AlertsConfig`-style declared
  field in `RiskConfig` (validated), wired `main.py:2188 sector_cap_mode=risk_cfg.sector_cap_mode`.
- `capital/risk_engine.py` SECTOR_EXPOSURE check: on a breach — **enforce** → reject as designed;
  **observe** (default) → LOG `risk_engine.sector_cap_would_reject … verdict=WOULD_REJECT` (symbol,
  sector, current %, projected %, 40% threshold) and **CONTINUE** (do not reject). `sector` is threaded
  into `_run_checks` for the log.
- **Default observe ⇒ the deploy is behaviour-neutral:** trades.sector now fills; the cap only logs.

### 3.3 Tests (fail-on-old / pass-on-new) — all green
- `test_order_placer_sector.py` (NEW): `_resolve_trade_sector` populates from instrument_cache /
  UNKNOWN on miss / UNKNOWN on no-cache / degrades on lookup error; the DQ alert fires **once** past
  the threshold, not below it, not before the min-sample. (Fail-on-old: `create_trade` got `None`.)
- `test_risk_engine.py`: `…observe_mode_logs_would_reject_not_rejects` (observe approves + logs;
  enforce rejects the same breach) · `…sums_multi_position_resting_book_by_sector` (ChatGPT Q5 — real
  per-sector sums; a NULL row is in no bucket = the old no-op).
- `test_config_loader.py`: `sector_cap_mode` defaults `observe`, `sector_unknown_alert_pct` `0.20`.
- Harness updates (enforce-path tests opt into enforce; prod default stays observe):
  `test_risk_engine._make_engine` + `test_gate8_sector_toctou._make_engine` default `enforce`;
  `test_hardening_scenarios` flips its engine to enforce test-locally.

**Results:** `test_risk_engine · test_gate8_sector_toctou · test_order_placer_sector · test_config_loader ·
test_order_placer` = **226 passed**; `test_hardening_scenarios` = **4 passed**; `test_main` = 71 passed /
**4 pre-existing PC-env failures (identical on base — ZERO new)**; all 4 modules compile.

---

## 4. OFF-MARKET DEPLOY + OBSERVE SOAK + ENFORCE (runbook — NOT executed now)
1. **Off-market: push + deploy.** Behaviour-neutral (sector fills; gate-8 observe = log-only). No
   schema change. Same 08:15-boot / off-market rules.
2. **OBSERVE SOAK ≥ 1 full trading session.** Collect the `sector_cap_would_reject … WOULD_REJECT`
   logs + confirm SANE: sectors bucket correctly, UNKNOWN proportion is acceptable (no DQ-alert storm),
   any WOULD_REJECT matches the Phase-1 expectation (rare at the current book size). Report the summary.
3. **ENFORCE FLIP — Rama-gated.** ONLY after the observe evidence is reviewed and Rama EXPLICITLY
   approves: set `risk.sector_cap_mode: observe → enforce`, off-market. **This activates the live sector
   cap** (never flip on simulation alone — activating a live capital control = a gate-class change).
**ROLLBACK:** set `sector_cap_mode` back to `observe` (instant de-fang), off-market; or revert the
commits. The populate-at-insert is data-only and harmless.

## Relation to D1
This clears **D1's data prerequisite** (trades.sector populated). **D1 (raise `max_concentration_pct`)
stays HOLD** — a separate Rama decision, NOT part of this fix.

---

## Files changed (UNPUSHED, branch `f1-trades-sector-observe-16jul`)
- `core/config_loader.py` — `risk.sector_cap_mode` (observe) + `sector_unknown_alert_pct` (0.20) + validators.
- `config/system_config.yaml` — the two fields (observe default; the enforce flip point).
- `capital/risk_engine.py` — `sector_cap_mode` param + observe/enforce branch on SECTOR_EXPOSURE + `sector` in `_run_checks`.
- `orders/order_placer.py` — `_resolve_trade_sector` + `_track_sector_dq` + `sector=…` at insert + DQ counters.
- `main.py` — wire `sector_cap_mode` (RiskEngine) + `sector_unknown_alert_pct` (OrderPlacer).
- `tests/unit/test_order_placer_sector.py` (NEW) + `test_risk_engine.py` + `test_gate8_sector_toctou.py` +
  `test_config_loader.py` + `tests/integration/test_hardening_scenarios.py`.
- `docs/audit/f1_trades_sector_16jul2026.md` (this report).

**Done = Phase-1 PASS (clean canonical source available at insert) · trades.sector populated at insert
(frozen, UNKNOWN-bucketed + DQ-alerted, parity) · gate-8 observe/enforce flag defaulting to observe ·
multi-position resting-book-sum + insert + observe/enforce tests pass · Phase-3 runbook (deploy →
≥1-session observe soak → Rama-gated enforce flip) ready · NOTHING pushed.** The dormant sector cap is
now fixable-and-visible, and activates only under review.
