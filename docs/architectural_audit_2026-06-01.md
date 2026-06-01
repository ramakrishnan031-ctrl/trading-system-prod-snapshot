# Architectural Audit — 14-Point Clarification Report

**Date:** 2026-06-01  
**Auditor:** Claude Code (investigation only, no code changes)  
**Codebase state:** commit 45a80bb (main)

---

## Point 1: Maximum Loss Calculation Base

### Current understanding:
There are **TWO independent daily-loss mechanisms**:

1. **FundManager absolute limit** (`capital.daily_loss_limit`): An absolute rupee value (currently Rs 100,000 — raised for paper). Checked inside `fund_manager.release_used()` after every trade close. Fires `on_daily_loss_breach` callback when `daily_realized_pnl <= -daily_loss_limit`.

2. **RiskEngine percentage gate** (`risk.daily_loss_limit_pct`): A percentage of **total capital** (currently 100% — raised for paper; production default was 5%). Checked inside `risk_engine.approve()` before every new trade. Formula: `abs(realized_pnl + unrealized_mtm) >= daily_loss_limit_pct * snap.total`. This is a **pre-trade gate** — blocks new entries.

### Base value:
- FundManager: absolute rupee cap against `daily_realized_pnl` (net realized P&L from SQL `fm_ledger`).
- RiskEngine: percentage of `snap.total` (total capital = opening balance + cumulative realized P&L).

### Hard stop or soft limit:
- **Hard stop.** On breach, the FundManager fires `on_daily_loss_breach` → `eod.fire_now()` → `kill_switch.soft_kill()`. All positions are squared off and new entries are blocked.

### Reset frequency:
- **Daily.** `fund_manager.reset_daily_pnl()` is called at EOD (writes RESET_PNL ledger entry bringing the SQL sum to 0).

### Is this correct? **Yes**

### Code location:
- `capital/fund_manager.py:1004` — breach check
- `capital/risk_engine.py:357-372` — DAILY_LOSS gate
- `config/system_config.yaml:71` — absolute limit
- `config/system_config.yaml:99` — percentage limit
- `main.py:576-619` — breach callback wiring

### Risk if wrong:
If the base is misunderstood, the limit may trigger too early (over-conservative) or never (under-conservative).

### Suggested fix:
Revert TEMP values to production: `daily_loss_limit: 1250` (5% of 25K live capital), `daily_loss_limit_pct: 0.05`.

---

## Point 2: Loss Per Order Calculation

### Current understanding:
- **Loss per order** = `(exit_price - entry_price) * qty` for LONG, `(entry_price - exit_price) * qty` for SHORT, minus broker costs. Calculated in `fund_manager.release_used()` at trade close.
- **Pre-trade risk estimate** = `abs(entry_price - sl_price) * qty`, stored in `trades.risk_amount` at trade creation.

### Is unrealized loss counted toward max loss?
- **Yes, in the RiskEngine gate only.** `risk_engine.approve()` line 360-361: `total_pnl = daily_pnl + unrealized_mtm`. The `get_total_unrealized_mtm()` call sums all live trades' mark-to-market.
- The FundManager breach check (at trade close) uses **realized P&L only**.

### Does TGT hit affect loss running total?
- **Yes.** A profitable TGT hit produces positive pnl_delta in `release_used()`, which reduces net daily_realized_pnl (makes it less negative).

### Is this correct? **Yes**

### Code location:
- `capital/fund_manager.py:950-956` — PnL computation
- `capital/risk_engine.py:357-361` — unrealized inclusion
- `orders/order_placer.py:640` — risk_amount at trade creation

### Risk if wrong: None — current logic is correct.

---

## Point 3: "Loss" Definition for Max Loss

### Current understanding:
- **Net P&L** (profits − losses), NOT sum of losing trades only.
- FundManager: `daily_pnl = self._store.get_daily_realized_net_pnl(today)` — SQL sum of all `pnl_delta` from fm_ledger for today's RELEASE_USED entries.
- RiskEngine: same net `daily_realized_pnl` + sum of all unrealized MTM.

### Is this correct? **Yes**

### Code location:
- `capital/fund_manager.py:1003` — `get_daily_realized_net_pnl(today)`
- `core/state_store.py` — SQL aggregation

### Risk if wrong:
If it were "sum of losers only" without considering winners, the system would halt prematurely on volatile days with mixed results.

---

## Point 4: TGT Calculation & Smart Target Manager

### Current understanding:

**TGT Formula:**
- Primary: `tgt = entry ± (sl_distance × rr_ratio)` where `sl_distance = abs(entry - sl)`
- Computed twice: once in `signal_processor._derive_target()` using strategy's `tgt_risk_reward`, and validated/recalculated in `order_placer._compute_tgt()` as fallback.
- Per `orders/price_math.py:calc_tgt_price()` — canonical formula.

**Does smart_tgt_manager do partial booking?**
- **No.** SmartTgtManager only trails the CO bracket SL (trigger_price modification). It does NOT book partial profits, does NOT place new orders, does NOT modify the TGT LIMIT order.

**Trailing SL after TGT1?**
- There is no multi-TGT concept. SmartTgtManager trails SL in fixed steps: once price moves `trigger_pct` (0.5%) from entry, SL advances by `step_pct` (0.3%) increments.
- Algorithm: `steps_count = int((distance_pct - trigger_pct) / step_pct)` → `new_sl = entry * (1 + trigger_pct + steps * step_pct)` for LONG.

**Time-based exit?**
- **Not in SmartTgtManager.** EOD squareoff at 15:17 IST is the only time-based exit. There is no time-based target acceleration.

**TGT adjustment if fill price differs from signal price?**
- **Yes, TGT is recalculated from actual fill price** (see Point 5).

### Is this correct? **Yes**

### Code location:
- `orders/price_math.py:41-57` — calc_tgt_price
- `orders/smart_tgt_manager.py:487-523` — trail algorithm
- `config/system_config.yaml:192-195` — smart_tgt defaults
- Each strategy YAML has `tgt_risk_reward` (1.5 to 2.5 range)

### Risk if wrong:
SmartTgtManager only works for CO_PLUS_TGT protocol trades. LIMIT_TRIPLE trades use BreakevenManager instead (FIX-132 Item 8).

---

## Point 5: Entry Fill Price vs Signal Price Handling

### Current understanding:

**After actual fill, does SL/TGT get recalculated?**
- **TGT: YES.** Both `_place_limit_triple_exits()` and `_place_co_tgt_exit()` explicitly recalculate TGT from `avg_fill_price`:
  ```
  actual_tgt_price = calc_tgt_price(direction, avg_fill_price, fill_entry.sl_price, rr_ratio)
  ```
- **SL: NO.** SL stays anchored to the original strategy-requested level (`fill_entry.sl_price`). Per FIX-013 comment: "SL price remains anchored to the original strategy-requested level."

**Same absolute distance OR same % distance?**
- **Same R:R ratio** (absolute distance scales with fill price, effectively same percentage). TGT preserves the `rr_ratio` relative to the gap between fill price and SL.

**Entry slippage guard (FIX-128)?**
- `max_entry_slippage_pct: 1.0%` — before placement, if current LTP deviates >1% from signal trigger price, entry is aborted with `OrderRejectedError`.

**Does smart_tgt_manager react to fill price?**
- **Indirectly.** It receives `entry_price` at registration time, which is the actual avg_fill_price passed from `_handle_entry_fill`.

### Is this correct? **Yes**

### Code location:
- `orders/order_placer.py:2108-2115` — TGT recalc in LIMIT_TRIPLE
- `orders/order_placer.py:2353-2360` — TGT recalc in CO_PLUS_TGT
- `orders/order_placer.py:380` — max_entry_slippage_pct param

### Risk if wrong:
The SL stays at the strategy-derived level even if fill is significantly different — risk:reward may differ from planned if entry slips toward SL (though the R:R gate at `min_effective_rr: 1.0` guards this).

---

## Point 6: Entry Cancellation if Price Reaches TGT

### Current understanding:
- **No such logic currently exists.** There is no pre-fill check that cancels an entry order if the price has already moved to where the TGT would be.
- The only pre-fill cancellation is `fill_timeout_sec: 60` — unfilled OPEN/SUBMITTED orders are cancelled after 60 seconds by `order_monitor`.
- The `max_entry_slippage_pct: 1.0%` guard only checks deviation from trigger price at placement time, not from TGT.

### Is this correct? **Partially** — the logic is missing, which may be intentional or an architectural gap.

### Code location:
- `broker/order_monitor.py:172` — fill_timeout_sec
- No file contains logic for "cancel entry if price hits TGT before fill"

### Risk if wrong:
A pending LIMIT entry order could fill at a price where the effective risk:reward is severely degraded (price already near TGT = minimal upside). The R:R gate at placement time doesn't cover post-placement drift during the 60s fill window.

### Suggested fix:
Add an optional "adverse fill zone" check in order_monitor: if LTP is within X% of TGT while entry is still pending, cancel the entry. Low priority — 60s timeout limits exposure.

---

## Point 7: Risk-Reward Ratio

### Current understanding:

**Where is R:R defined?**
Per-strategy YAML in `config/strategies/` under `tgt_risk_reward` key.

**Current values:**

| Strategy | R:R |
|----------|-----|
| first_pullback_long/short | 2.0 |
| gap_fade_long/short | 1.5 |
| gap_go_long/short | 2.5 |
| range_breakout_long/short | 2.0 |
| open_low_breakout_long | 2.0 |
| open_high_breakdown_short | 2.0 |
| vwap_bounce_long | 2.0 |
| vwap_rejection_short | 2.0 |
| positional_momentum_long | 2.0 |
| positional_swing_long | 2.0 |
| positional_sector_rotation | 2.0 |

**OrderPlacer also has `rr_ratio=2.0`** as constructor default (fallback if signal_processor doesn't provide tgt_price).

**Entry gate also has `min_effective_rr: 1.0`** (FIX-136 Item 54) — rejects if post-slippage R:R drops below 1.0.

### Is this correct? **Yes**

### Code location:
- `config/strategies/*.yaml` — `tgt_risk_reward` key
- `orders/order_placer.py:366` — `rr_ratio=2.0` default
- `config/system_config.yaml:203` — `min_effective_rr: 1.0`

### Risk if wrong: None — well-defined per strategy.

---

## Point 8: Capital Allocation Sequence

### Current understanding:

**Formula:**
```
margin_required = qty * price / leverage
total_reservation = margin_required + (margin_required * slm_margin_buffer_pct)
```

Where:
- `leverage` comes from `leverage_map` (INTRADAY=5x, CO=6x, DELIVERY=1x)
- `slm_margin_buffer_pct = 0.05` (5% extra for SL-M margin uncertainty)

**Sequence:**
1. `position_sizer.size()` → computes qty from risk budget
2. `risk_engine.approve()` → pre-trade gate (checks bucket availability)
3. `fund_manager.reserve()` → atomically deducts from `available`, adds to `reserved`
4. `order_placer.place()` → creates trade, places broker orders
5. On fill: `fund_manager.commit_to_used()` → moves `reserved` → `used` (adjusts for actual fill price)
6. On exit: `fund_manager.release_used()` → moves `used` → `available` + P&L

**Margin considered?** Yes — leveraged margin, not notional.

**Capital reduced per leg?** Once at trade start (reserve). SL/TGT orders don't require additional margin.

**Updated after realized P&L?** Yes — `release_used()` adds/subtracts P&L to `available`, and adjusts `_total`.

**Reversed on order cancellation?** Yes — `fund_manager.release()` reverses the reservation (reserved → available) on failure/timeout.

### Is this correct? **Yes**

### Code location:
- `capital/fund_manager.py:413-449` — reserve()
- `capital/fund_manager.py:194-214` — required_margin()
- `config/system_config.yaml:68-78` — capital section

### Risk if wrong: None — well-designed with invariant checking.

---

## Point 9: DB as Single Source of Truth

### Current understanding:

**Does daily_report.py read ONLY from DB?**
- **Yes** (since FIX-124). `reports/daily_report.py:load_report_data()` calls only `state_store.get_*` methods. It also reads YAML configs (strategy min_scores, scoring_weights, broker_costs) but those are reference data, not trade data.

**Does daily_review.py read ONLY from DB?**
- **Yes.** `reports/daily_review.py:load_report_data()` (line 112-157) reads from `state_store` exclusively for trade/signal/order/ledger/screener data. Reads config YAMLs for display formatting only.

**Any other code path that bypasses DB?**
- **No.** The FIX-124 change deleted `_compute_cost_breakdown`, `_build_strategy_min_scores`, `_load_candle_data` and removed the `--candle-dir` flag. All cost/score/candle data now comes from the v14+ DB schema.

### Is this correct? **Yes**

### Code location:
- `reports/daily_report.py` — DB-only pipeline
- `reports/daily_review.py:112-157` — `load_report_data()` all from state_store

### Risk if wrong: None — architecture is clean.

---

## Point 10: Daily Order Cleanup at 15:30

### Current understanding:

**What's the actual cleanup time?**
- **EOD squareoff fires at 15:17 IST** (cancels pending + exits positions).
- **EOD cleanup cron runs at 15:50 IST** (marks stale signals EXPIRED, stale orders CANCELLED, prunes fingerprints).
- **EOD verify cron runs at 15:55 IST** (confirms positions closed).

**Does it distinguish INTRADAY vs DELIVERY?**
- **Yes.** EOD squareoff (`orders/eod_squareoff.py`) explicitly states EOD6: "DELIVERY (CNC) positions NOT touched. Only INTRADAY (MIS) and COVER_ORDER (CO) products exited."
- The EOD cleanup script (`scripts/eod_cleanup.py`) marks stale signals/orders for dates **before** today — it doesn't distinguish by product type (it's a session artifact cleanup).

**Any risk of cancelling non-intraday orders?**
- **No.** The EOD squareoff only touches MIS/CO products. The cleanup script only marks orders from **previous days** as cancelled (line 118: `placed_at < date_iso`), not today's orders.

### Is this correct? **Yes**

### Code location:
- `orders/eod_squareoff.py:11` — EOD6 locked decision
- `scripts/eod_cleanup.py:87-101` — stale signals cleanup
- `deploy/cron/trading-system.cron:30` — 15:50 IST cron

### Risk if wrong: Delivery positions are safe.

---

## Point 11: Data Retention (1 Calendar Month)

### Current understanding:

**Log retention:**
- Logrotate: `maxage 30` (delete logs older than 30 days).
- Cron: `find ... -mtime +30 -delete` at 00:00 IST daily.

**DB backups:**
- Nightly SQLite `.backup` at 01:00 IST.
- Backup files: `find ... -mtime +7 -delete` — **only 7 days of backups retained**.

**Signal fingerprints:**
- `eod_cleanup.py` prunes fingerprints older than 7 days (default `--fingerprint-days 7`).

**Reports:**
- No automatic deletion of report files (xlsx/md in `reports/output/`). These accumulate.

**DB itself:**
- No automatic purging of old trade/signal/order rows. DB grows indefinitely.

### Is this correct? **Partially**

### Code location:
- `deploy/logrotate/trading-system:8` — `maxage 30`
- `deploy/cron/trading-system.cron:12` — log cleanup
- `deploy/cron/trading-system.cron:18` — backup cleanup (7 days)
- `scripts/eod_cleanup.py:43` — fingerprint_days=7

### Risk if wrong:
DB and report files will grow indefinitely. For a 25K capital system this is likely manageable for months, but eventually disk space could become an issue.

### Suggested fix:
Add a cron job to prune signals/orders/trades older than 90 days to an archive table or export, keeping the main DB lean. Add report file rotation (delete reports >60 days).

---

## Point 12: Disk Space Management

### Current understanding:

**Startup check:**
- `utils/startup_checks.py:check_disk_space()` checks free disk >= `min_free_disk_gb: 2.0 GB` before startup. If below, startup is blocked with CRITICAL log.

**Runtime monitoring:**
- **None.** There is no periodic disk space check during runtime. Only the startup gate.
- WAL checkpoint runs at 16:00 IST (`scripts/wal_checkpoint.py`) which reclaims WAL space.

### Is this correct? **Partially** — startup check exists, but no runtime monitoring.

### Code location:
- `utils/startup_checks.py:1128-1195` — check_disk_space
- `config/system_config.yaml:180` — min_free_disk_gb: 2.0
- `deploy/cron/trading-system.cron:44` — WAL checkpoint

### Risk if wrong:
If disk fills during the trading day (unlikely with 30-day log rotation, but possible with DB growth or runaway logging), the system won't detect it until next restart.

### Suggested fix:
Add a periodic cron job (e.g., hourly) that checks `df` output and sends a Telegram alert if free space drops below 5GB. Low priority given log rotation is active.

---

## Point 13: Gemini AI for EOD Log Review

### Current understanding:

**Feasibility:** Yes, fully feasible.

**Current log format:** JSON structured logs (one JSON object per line). Easily parseable.

**Integration approach:**
- A post-EOD cron script (e.g., at 16:20 IST) could:
  1. `tail -n 1000` today's log file
  2. Pipe through Gemini CLI: `gemini "Review these trading logs for anomalies: $(cat today.log)"`
  3. Save output to `reports/output/ai_review_YYYY-MM-DD.txt`
  4. Optionally send summary via Telegram

**Constraints:**
- Gemini CLI context window limits may require filtering logs to CRITICAL/ERROR/WARNING only.
- Log files per day are typically 1-5MB — may need truncation.

### Is this correct? **Yes** (feasibility confirmed)

### Code location:
- `core/logger.py` — log format configuration
- `deploy/cron/trading-system.cron` — cron schedule reference

### Risk if wrong: None — this is additive functionality.

### Suggested fix:
Create `scripts/ai_log_review.py` that filters today's WARNING+ logs, pipes to Gemini CLI, saves review output. Add to cron at 16:20 IST.

---

## Point 14: Old Logs Deleted, Only DB Exists

### Current understanding:

**Does any report/feature depend on log files?**
- **No.** Since FIX-124, all reporting reads exclusively from the SQLite database. The reports module does NOT open or parse log files.
- `daily_review.py` — DB only (confirmed in Point 9).
- `daily_report.py` — DB only (FIX-124).
- `scripts/eod_verify.py` — reads from state_store (DB).
- `scripts/reconcile_positions.py` — reads from state_store + broker API.

**Safe to delete old logs?**
- **Yes.** Log deletion (via logrotate/cron) is safe. No production feature reads from `.log` files.

### Is this correct? **Yes**

### Code location:
- `reports/daily_report.py` — confirmed no log file reads
- `reports/daily_review.py` — confirmed no log file reads

### Risk if wrong: None — architecture is clean since FIX-124.

---

## Additional Findings

During investigation, the following issues were identified that were NOT on the 60-point list:

### AF-1: TEMP Config Values Still Active (CRITICAL for live)

The following TEMP values from paper testing are still in `system_config.yaml`:

| Parameter | Current (TEMP) | Production Default | Line |
|-----------|---------------|--------------------|------|
| `capital.daily_loss_limit` | 100,000 | 1,250 (5% of 25K) | 71 |
| `risk.max_consecutive_losses` | 20 | 4 | 98 |
| `risk.daily_loss_limit_pct` | 1.00 (100%) | 0.05 (5%) | 99 |
| `order_reconciler.capital_drift_tolerance` | 100,000 | 50 | 184 |
| `drift_handler.log_only_threshold_rs` | 100,000 | 250 | 216 |
| `drift_handler.soft_kill_threshold_rs` | 200,000 | 1,000 | 217 |
| `drift_handler.hard_kill_threshold_rs` | 500,000 | 2,500 | 218 |

**Risk:** With live money, all safety limits are effectively disabled. A runaway loss would not be caught until total capital is exhausted.

**Fix:** Create a `config/system_config_live.yaml` overlay or a pre-live script that reverts these values.

### AF-2: Duplicate Cron Entry

`deploy/cron/trading-system.cron` has **two identical entries** for daily_review.py at 16:00 IST (lines 39 and 48). The report will run twice.

**Risk:** Double execution wastes resources and may produce confusing logs. Not harmful (report is idempotent), but indicates cron file wasn't fully deduped.

**Fix:** Remove line 48 (duplicate).

### AF-3: Logrotate Path Mismatch

The logrotate config (`deploy/logrotate/trading-system`) uses path `/home/ubuntu/trading-system/logs/` but the actual VM path is `/home/ubuntu/systems/trading-system/logs/` (per `project_vm_architecture_locked.md`).

**Risk:** Logrotate may not match any files, meaning old logs accumulate past 30 days. The cron `find` at 00:00 (which does use the correct path) provides a backup cleanup, so impact is limited.

**Fix:** Update logrotate path to `/home/ubuntu/systems/trading-system/logs/`.

### AF-4: No Partial Profit Booking

The system has no mechanism for partial profit booking (e.g., exit 50% at TGT1, trail remainder). SmartTgtManager only trails SL; it never places exit orders. The only exits are: full TGT hit, SL hit, or EOD squareoff.

**Risk:** In trending markets, trailing SL alone may give back significant profits before trail catches up (especially with step_pct=0.3% granularity).

**Fix:** Future enhancement. Could add a TGT1/TGT2 split in strategy YAML + BreakevenManager extension. Not urgent.

### AF-5: BreakevenManager Only for LIMIT_TRIPLE

The BreakevenManager (FIX-132 Item 8) is only registered for `LIMIT_TRIPLE` protocol trades with `trailing_sl_enabled=True`. The `CO_PLUS_TGT` trades rely solely on SmartTgtManager for trail. Since most strategies use `CO_PLUS_TGT`, the BreakevenManager coverage is limited.

**Risk:** Low — SmartTgtManager provides the trail for CO trades. Just noting the asymmetry.

### AF-6: No DB Retention/Archive Policy for Core Tables

While signal fingerprints are pruned after 7 days, the core tables (`trades`, `orders`, `signals`, `fm_ledger`) have **no retention policy**. After a year of trading, the DB could grow significantly (especially `fm_ledger` and `signals` which get multiple rows per trade).

**Risk:** SQLite performance degrades with very large tables. Unlikely to be a problem within 6 months at current volume (~5-10 trades/day), but should be planned.

**Fix:** Quarterly archive script that moves rows older than 90 days to `trading_archive.db`.

### AF-7: webhook.require_hmac Still False

`config/system_config.yaml:111` has `require_hmac: false`. This was acceptable for Chartink (which doesn't support HMAC signing), but means any entity that can reach the webhook port can inject signals.

**Risk:** On the VM with UFW + ngrok, the surface is limited. But if the webhook is ever exposed more broadly, this is a trivial injection vector.

**Fix:** Consider IP-allowlisting at the application level or validate Chartink's source IP range.
