# Trading System v2 — Config Guide

This is Rama's reference for changing config settings.
**Read the section you need; don't try to read it all at once.**

> Created 2026-06-19 (Task #8). Cross-referenced from `docs/SYSTEM_MAP.md`.
> If a setting here ever disagrees with the actual YAML, the **YAML file wins** —
> this doc describes the files, it does not control the system.

---

## How to Edit Config

- Config files live in `/home/ubuntu/systems/trading-system/config/` on the VM.
- Edit with: `nano <file>` (e.g. `nano config/system_config.yaml`).
- **Changes apply on the next service restart — NOT immediately.** Editing a file
  only changes the file on disk. The running process keeps its old values until
  it restarts. (`git push` deploys code/config to the VM but also does NOT restart.)
- Restart to apply: `deploy/resume.sh` (clean systemd restart) — see Quick Commands.
- The service auto-exits at EOD (16:00 IST once flat) and auto-starts ~08:30 next
  trading day, so an edit made in the evening is normally live by next morning.
- **Test-mode caps override base config.** Always check the *effective* value
  (Section 12) — the number you edited may be overridden by a higher-priority one.
- **YAML is whitespace-sensitive.** Use spaces, never tabs. Keep the existing
  indentation. A bad indent can make the whole file fail to load → service won't start.

---

## Currently Effective Values (as of 2026-06-19)

| Setting | Effective | Source |
|---|---|---|
| Trading mode | **live** | systemd `ExecStart` (`main.py --mode live`) — *not* a config key |
| Min signal score | **60** | `scoring_weights.yaml → min_pass_score` |
| Max open positions | **5** | `system_config.yaml → risk.max_open_positions` |
| Max daily trades | **10** | `system_config.yaml → risk.max_daily_trades` |
| Capital | **₹10,000** | `accounts.csv` (paper) / live broker funds (live) |
| Daily loss limit | **3%** (₹300 on ₹10k) | `system_config.yaml → risk.daily_loss_limit_pct` (BUILD 1: sole authority; absolute ₹ key deleted) |
| Max position value / trade | **40%** (₹4,000 on ₹10k) | `system_config.yaml → position_sizing.max_position_value_pct` (capital-relative reject cap) |
| Max concentration / symbol | **10%** (₹1,000 on ₹10k) | `system_config.yaml → position_sizing.max_concentration_pct` |
| Risk per trade | **1%** | `system_config.yaml → position_sizing.risk_per_trade_pct` |
| Entry window | **10:00 – 15:15** | `system_config.yaml → trading_hours` |
| EOD square-off | **15:17** | `system_config.yaml → trading_hours.eod_squareoff_time` |
| Entry throttle (min gap) | **20 s** | `system_config.yaml → signal_processor.min_gap_between_entries_sec` |
| Entry burst cap | **3 / 60 s** | `system_config.yaml → signal_processor.entry_burst_max` |
| Per-symbol cooldown | **5 min** | `system_config.yaml → signal_processor.per_symbol_cooldown_sec` |
| Consecutive-loss halt | **4 losses** | `system_config.yaml → risk.max_consecutive_losses` |
| Reconciler cadence | **15 s** | `system_config.yaml → order_reconciler.poll_interval_sec` |
| Capital-drift alert interval | **30 min** | `system_config.yaml → order_reconciler.capital_drift_alert_interval_sec` |
| Kill-switch API-fail threshold | **3** | `system_config.yaml → kill_switch.api_failure_threshold` |
| Telegram alerts | **ON** | `system_config.yaml → alerts.telegram.enabled` |
| Force intraday-only (MIS) | **ON** | `system_config.yaml → force_intraday_only` |

---

## Section 1: Capital & Risk

These control how much money is at risk. **Read Section 6's safety warnings before raising any of them.**

### daily_loss_limit_pct  ⭐ BUILD 1 (24-Jun): now the SOLE daily-loss authority
**File:** `system_config.yaml → risk.daily_loss_limit_pct`
**Current:** `0.03` · **Type:** float (fraction) · **Valid:** 0–1

**What it does:** The single daily-loss limit, expressed as a fraction of capital.
`0.03` = 3% (₹300 on ₹10k; ₹3,000 on ₹1L — it scales with the account). It is enforced
on **two bases** (deliberate, not a duplicate):
- **Pre-trade** (RiskEngine): blocks new entries once **realised + unrealised** loss hits
  `daily_loss_limit_pct × capital`.
- **Post-close** (FundManager, after a trade settles): trips a SOFT_KILL once **realised**
  loss hits `daily_loss_limit_pct × current capital`.

**BUILD 1 change:** the old absolute `capital.daily_loss_limit` (₹300) was **DELETED**.
Both checks now derive their ₹ figure from this one pct key, so there is a single
source of truth and the limit scales with capital automatically. See
`[[dual_daily_loss_mechanism]]` and `[[build1_config_authority_fixes_24jun]]`.

**Example:** `0.03` → stop after a 3%-of-capital loss. `0.05` → allow 5%.

### intraday_bucket_pct / positional_bucket_pct
**File:** `system_config.yaml → capital.intraday_bucket_pct` / `positional_bucket_pct`
**Current:** `0.70` / `0.30` · **Type:** float (fraction) · **Must sum to 1.0**

**What it does:** Splits total capital between intraday (MIS/CO) and positional (CNC)
buckets. With `force_intraday_only: true` (current) only the intraday bucket is used.

### leverage_map
**File:** `system_config.yaml → capital.leverage_map`
**Current:** `INTRADAY: 5.0`, `COVER_ORDER: 6.0`, `DELIVERY: 1.0`, `BRACKET_ORDER: 5.0`
**Type:** float multiplier per product

**What it does:** How much margin leverage each product assumes when sizing. This affects
*margin* math, not the cash-notional concentration caps. ⚠️ Raising it borrows more from
the broker — see Section 6.

### risk_per_trade_pct
**File:** `system_config.yaml → position_sizing.risk_per_trade_pct`
**Current:** `0.01` · **Type:** float (fraction) · **Valid:** 0–1

**What it does:** Fraction of total capital risked (entry→SL distance × qty) on a single
trade. `0.01` = 1% (₹100 on ₹10k). One of the three sizing caps; the *smallest* wins.

### max_concentration_pct
**File:** `system_config.yaml → position_sizing.max_concentration_pct`
**Current:** `0.10` · **Type:** float (fraction) · **Valid:** 0–1

**What it does:** Max fraction of capital in any one symbol (qty × price). `0.10` =
₹1,000 on ₹10k. **On a ₹10k account this is usually the BINDING cap** and is what pins
qty to 1–2 shares. Raise to ~0.25 if you want qty≈4. See `[[capital_sizing_audit_18jun]]`.

**Example:** `0.10` → ≤₹1,000/symbol. `0.25` → ≤₹2,500/symbol.

### max_position_value_pct  ⭐ BUILD 1 (24-Jun): capital-relative (was fixed ₹)
**File:** `system_config.yaml → position_sizing.max_position_value_pct`
**Current:** `0.40` · **Type:** float (fraction) · **Valid:** 0–1

**What it does:** Hard ceiling on qty × price for one order, as a fraction of capital —
`cap = max_position_value_pct × capital` (₹4,000 on ₹10k; ₹40,000 on ₹1L). It **REJECTS**
(does not shrink) an order whose value exceeds the cap. It's a catastrophic-loss / bug
guard that fires on an **anomaly**, not routine sizing — on ₹10k the 10% concentration
cap (₹1,000) and 1% risk bite first, so 40% rarely binds.

**BUILD 1 change:** was a fixed `max_position_value_rs` (₹2,500). That created a hard
scaling cliff (every order rejected once the account grew past ~₹25k). The pct form
scales with the account and removes the cliff.

### risk.max_open_positions / max_daily_trades
**File:** `system_config.yaml → risk.max_open_positions` / `max_daily_trades`
**Current:** `5` / `10`

**What it does:** Portfolio caps on concurrent open positions and total entries per day.
**BUILD 1 (#3):** these are now the **sole** authority in both paper and live — the old
`live_test_*` override keys were deleted (they had been set equal to these, so the swap
did nothing). Edit these directly to change the caps.

### max_sector_exposure_pct
**File:** `system_config.yaml → risk.max_sector_exposure_pct`
**Current:** `0.40` · **Type:** float (fraction)

**What it does:** Max fraction of capital concentrated in one sector. `0.40` = 40%.

### max_consecutive_losses
**File:** `system_config.yaml → risk.max_consecutive_losses`
**Current:** `4` · **Type:** integer

**What it does:** After this many losing trades **in a row (today only)**, new signals
are halted for the day. Day-scoped since FIX-183 (yesterday's losses no longer block
today). See `[[fix_183_consecutive_losses_dayscope]]`.

### tier_multipliers / dynamic_by_winrate
**File:** `system_config.yaml → position_sizing.tier_multipliers`, `dynamic_by_winrate`
**Current:** `HIGH 1.0 / MEDIUM 0.70 / LOW 0.50`; `dynamic_by_winrate: true`

**What it does:** After the base size is computed, it's scaled by the signal's quality
tier and (if `dynamic_by_winrate`) by the strategy's recent win-rate (floor `min_multiplier`
0.5, cap `max_multiplier` 2.0).

---

## Section 2: ~~Live Test Mode~~ — REMOVED in BUILD 1 (24-Jun)

> **The `live_test_mode` / `live_test_max_open_positions` / `live_test_max_entries_per_day`
> keys were DELETED.** They had been set equal to the base caps (`max_open_positions: 5`,
> `max_daily_trades: 10`) for parity, so the live-mode swap in `main.py` was a no-op that
> only *looked* active — misleading dead config (audit conflict #3). The base caps in
> Section 1 (`risk.max_open_positions` / `max_daily_trades`) are now the sole authority in
> **both** paper and live. The deliberate small-exposure bug-hunting posture is unchanged —
> it's just driven by the base caps directly now. The pre-flight `caps_config_drift` check
> alerts (CRITICAL) if these drift from 5 / 10. See `[[build1_config_authority_fixes_24jun]]`.

---

## Section 3: Trading Hours & Windows

All times are `"HH:MM"` strings interpreted as **IST**.

### entry_start / entry_end
**File:** `system_config.yaml → trading_hours.entry_start` / `entry_end`
**Current:** `10:00` / `15:15`

**What it does:** The window during which new entries may be placed. `entry_start` is
10:00 (conservative — avoids opening-hour volatility). No entries before `entry_start`
or after `entry_end`. (Per-strategy `entry_start_time`/`entry_end_time` can narrow this
further, never widen it.)

### eod_entry_cutoff
**File:** `system_config.yaml → trading_hours.eod_entry_cutoff`
**Current:** `15:15`

**What it does:** Absolute last moment an order may be placed, regardless of anything
else (guards against rate-limiter delays sneaking a late entry in).

### eod_squareoff_time
**File:** `system_config.yaml → trading_hours.eod_squareoff_time`
**Current:** `15:17` · (3-min buffer before Zerodha RMS auto-square-off ~15:20)

**What it does:** Time the system squares off all intraday positions. Keep it before the
broker's own auto-square-off so the system exits on its own terms.

### market_open / market_close
**File:** `system_config.yaml → trading_hours.market_open` / `market_close`
**Current:** `09:15` / `15:30`

**What it does:** NSE regular-session bounds. Used for market-window checks and the
service start/stop window. Don't change unless NSE changes session hours.

### special_sessions
**File:** `system_config.yaml → special_sessions`
**Current:** empty (commented examples only)

**What it does:** Per-date overrides of market hours for special sessions (e.g. Muhurat
trading). Uncomment and fill the date block when one is scheduled.

---

## Section 4: Order Placement & Protocols

### force_intraday_only
**File:** `system_config.yaml → force_intraday_only`
**Current:** `true` · **Type:** boolean

**What it does:** Forces **every** strategy to `INTRADAY` (MIS) at load time, so the
system can never place CNC/DELIVERY (overnight) orders. Set `false` only when positional
trading is deliberately re-enabled and re-tested. ⚠️ A safety control — leave ON.

### order_monitor.poll_interval_sec / fill_timeout_sec
**File:** `system_config.yaml → order_monitor`
**Current:** `2` / `60`

**What it does:** How often (sec) the broker is polled for fills, and how long an unfilled
OPEN/SUBMITTED order waits before being cancelled. Lower poll = faster reaction, more API
calls.

### order placement price buffers
**File:** `system_config.yaml → capital.*`
- `slm_margin_buffer_pct: 0.05` — 5% margin buffer for stop orders (unknown fill price).
- `sl_limit_offset_pct: 0.005` — SL legs are stop-limit; limit = trigger ±0.5% so triggered
  stops actually fill (Zerodha rejects SL-M via API).
- `emergency_exit_buffer_pct: 0.01` — emergency/kill exits use a marketable LIMIT (LTP ±1%)
  so they fill but cap worst-case slippage.

### broker_limits.yaml (rate limits)
**File:** `config/broker_limits.yaml`

**What it does:** Client-side token-bucket caps so we never exceed Zerodha's API limits.
`order` 8/s, `quote` 3/s, `historical` 2/s, `margins` 8/s. On HTTP 429 the backoff
sequence (`1, 5, 30 s` then soft_kill) kicks in. `timeouts` = HTTP connect/read seconds.
**Don't raise these above Zerodha's documented limits** or you'll get throttled/banned.

### entry_gate (slippage & liquidity protection)
**File:** `system_config.yaml → entry_gate`
- `max_entry_slippage_pct: 1.0` — the flat % cap (used by `slippage_control` mode `pct`, and as the
  belt-and-suspenders / fallback check).
- `slippage_control:` — **entry-slippage abort, default = % of the SL distance.** Aborts the entry if
  `|LTP − signal price|` exceeds a `tolerance`. The default `sl_fraction` mode caps slippage at a
  fraction of your risk (SL distance) — because the SL is fixed at the signal level (TGT recalcs from the
  fill, FIX-013), entry slippage eats straight into the risk budget. **Calibrate** `max_slippage_fraction`
  + `absolute_cap_rs` (then restart).
  ```yaml
  slippage_control:
    enabled: true
    mode: sl_fraction            # sl_fraction (default) | flat_tiers | pct
    max_slippage_fraction: 0.22  # slippage <= 22% of the SL distance (|signal - sl|)
    absolute_cap_rs: 5.00        # backstop: tolerance never exceeds this (smaller wins)
    hard_max_slippage_rs: 10.0   # absolute ceiling, ALWAYS applied (any mode)
    also_apply_pct_check: true   # also apply max_entry_slippage_pct (belt + suspenders)
    # flat_tiers mode (kept for A/B): per-price-band Rs, lower bound exclusive
    default_max_slippage_rs: 2.00
    tiers: [ {max_price: 100, max_slippage_rs: 1.00}, {max_price: 200, max_slippage_rs: 1.25},
             {max_price: 500, max_slippage_rs: 2.00}, {max_price: 999999, max_slippage_rs: 3.00} ]
  ```
  **Tune from real data:** every entry logs `entry_slippage_observed` with `fraction_of_sl_used` (how
  much of the SL the slippage ate). Too many aborts → raise `max_slippage_fraction` (e.g. 0.25–0.30).
  NB at 0.22, a ₹0.43 slip on a ₹83 stock with a 2% SL = 26% of SL → aborts; raise to ~0.27 to allow it.
  - `slippage_control.overrides:` — **Phase 3a: per-symbol / per-strategy / per-band tolerance.** The
    global `max_slippage_fraction` (0.22) is the default; you can override it for specific names. The
    effective fraction is resolved **MOST-SPECIFIC-WINS: Symbol > Strategy > Price Band > Global** (the
    first match wins). Applies to the `sl_fraction` mode only. **All three maps SHIP EMPTY → pure global
    0.22 baseline everywhere** (clean data collection first; add overrides later from real evidence) →
    behaves exactly as the global until you add an override. Fractions
    must be in `(0, 1]`; a value that looks extreme (`<0.05` or `>0.50`) or a `by_symbol`/`by_strategy`
    typo (a key that matches no instrument/strategy → the override is silently ignored) is **warned**
    about at startup. The rule that applied is logged per entry (`tolerance_source`) and recorded on the
    trade (so the System Manager EOD report and Phase-2 reports can show it). Restart to apply.
    ```yaml
    slippage_control:
      overrides:
        enabled: true                # master switch for the whole hierarchy
        by_price_band: {}            # ships empty; e.g. { "0-100": 0.18 } (keys = `slippage_bands` labels)
        by_strategy:   {}            # ships empty; e.g. gap_fade: 0.25  (keys = loaded strategy names)
        by_symbol:     {}            # ships empty; e.g. IDEA: 0.15, RELIANCE: 0.30 (most specific; wins)
    ```
    Examples (global 0.22, band `0-100`=0.18, strategy `gap_fade`=0.25, symbol `IDEA`=0.15):
    IDEA on any strategy → **0.15**; `gap_fade` on a ₹500 stock → **0.25**; `vwap` on a ₹80 stock →
    **0.18** (band); `vwap` on a ₹300 stock → **0.22** (global); `gap_fade` on IDEA → **0.15** (symbol
    beats strategy).
- `max_spread_pct: 0.5` / `min_depth_qty: 500` / `liquidity_check_enabled: true` —
  liquidity gate: skip illiquid names (wide spread / thin book).
- `min_effective_rr: 1.0` — abort if reward:risk < 1.0 after slippage.
- `min_pending_rr: 1.0` — cancel a still-pending entry if its remaining R:R drops below this.

### order protocol (per-strategy)
**File:** `config/strategies/<strategy>.yaml → order_protocol`
**Values:** `CO_PLUS_TGT` (Cover Order with broker SL + our target — intraday),
`LIMIT_TRIPLE` (entry + SL + TGT as three LIMIT/SL orders with software OCO — positional).

---

## Section 5: Signal Processing

### min_pass_score
**File:** `config/scoring_weights.yaml → min_pass_score`
**Current:** `60` · **Type:** integer · **Valid:** 0–100

**What it does:** Minimum quality score a signal must reach (sum of passed-step weights,
max 100) to be traded. **This is your single biggest "be more/less selective" knob.**

**Example:** `60` → only signals scoring ≥60 trade. `80` → only very strong signals.
`30` → most signals pass (**not recommended**).

**Related:** step weights below; `high_score_threshold`/`medium_score_threshold` set the
size tiers.

### scoring step weights
**File:** `config/scoring_weights.yaml → steps`
**Current:** volume_surge 15, price_action 15, vwap_position 10, atr_filter 10,
rsi_range 10, sector_strength 10, circuit_check 10, signal_age 10, time_of_day 5,
spread_check 5 (sum = 100).

**What it does:** Points awarded for each screening step a signal passes. Raising a
weight makes that factor matter more toward the `min_pass_score` bar. **No weights are
hardcoded — this file is the only source.**

### tier thresholds
**File:** `config/scoring_weights.yaml`
**Current:** `high_score_threshold: 80`, `medium_score_threshold: 65`,
`tier_multipliers: high 1.0 / medium 0.75 / low 0.5`

**What it does:** A passing signal's score maps to a size tier: ≥80 = high (full size),
65–79 = medium (75%), 60–64 = low (50%).

### entry throttle (anti-burst)
**File:** `system_config.yaml → signal_processor`
- `min_gap_between_entries_sec: 20` — ≥20 s between any two *placed* entries (global).
- `entry_burst_window_sec: 60` / `entry_burst_max: 3` — ≤3 placed entries per rolling 60 s
  (`0` = off).
- `per_symbol_cooldown_sec: 300` — no re-entry of the *same symbol* within 5 min.

**What it does:** Prevents a Chartink spike from firing many entries at once (the 19-Jun
5-in-5s incident). Throttled signals are **dropped**, not queued. See `[[bug_g_entry_throttle]]`.

### tgt_min_pct / signal pipeline
**File:** `system_config.yaml → signal_processor`
- `tgt_min_pct: 0.003` — reject a signal whose target is <0.3% from entry (no edge).
- `worker_count: 5` — signal-processing thread pool size.
- `pipeline_timeout_sec: 30` — hard deadline per signal.

### webhook & dedup
**File:** `system_config.yaml → webhook`, `signal_queue`
- `webhook.dedup_window_seconds: 300` — identical signals within 5 min are de-duplicated.
- `webhook.require_hmac: false` — Chartink can't sign payloads; auth is via `?token=`
  (`WEBHOOK_SECRET`). `bind_port: 5000`.
- `signal_queue.capacity: 300`, `backpressure_pct: 0.80` — queue returns HTTP 503 above
  80% full; `expiry_sec: 600` drops signals older than 10 min.

### excluded_symbols
**File:** `system_config.yaml → excluded_symbols`
**Current:** `E2E, GVPIL, BIRLACABLE, SHANKARA, MCLEODRUSS`

**What it does:** Symbols rejected at the webhook edge (DEBUG log only). Add a symbol here
to stop trading it entirely.

---

## Section 6: Reconciliation & Safety

### kill_switch
**File:** `system_config.yaml → kill_switch`
- `api_failure_threshold: 3` — consecutive broker API failures before an auto SOFT_KILL.
- `enable_auto_trip: true` — if `false`, API failures never auto-trip (⚠️ removes a guard).

(Note: a broker **auth** 403 is excluded from this counter since FIX-185 — an IP-allowlist
issue won't crash-loop the kill switch.)

### order_reconciler
**File:** `system_config.yaml → order_reconciler`
- `poll_interval_sec: 15` — how often local vs broker state is reconciled.
- `capital_drift_tolerance: 50.0` — out-of-session ₹ drift tolerated before alert.
- `capital_drift_tolerance_pct: 0.10` — in-session tolerance = max(₹50, expected×10%),
  silences normal deployed-capital drift during trading (FIX-190 Bug I).
- `human_order_margin_tolerance: 5000.0` — extra ₹ allowance when untracked/human Kite
  orders are present.
- `capital_drift_alert_interval_sec: 1800` — min 30 min between repeat drift alerts (TASK-11).
- `stuck_exiting_timeout_minutes: 30` — an EXITING trade older than this is auto-resolved
  (flat→CLOSED_MANUAL, still-held→OPEN). See `[[followup_reconciler_exiting_gap]]`.

### drift_handler (escalation ladder)
**File:** `system_config.yaml → drift_handler`
**Current:** `log_only 250`, `soft_kill 1000`, `hard_kill 2500` (₹),
`consecutive_cycles_before_escalate: 3`

**What it does:** As capital drift grows, escalates: log → SOFT_KILL → HARD_KILL. Requires
3 consecutive cycles at a level before escalating (avoids one-off blips). ⚠️ HARD_KILL
flattens everything — don't lower these casually.

### strategy_circuit_breaker / circuit_breaker
**File:** `system_config.yaml → strategy_circuit_breaker`, `circuit_breaker`
- Strategy-level: pause a strategy if its daily loss > `loss_multiplier`(2×) the avg daily
  loss; no new pause after `cutoff_time` 12:00.
- Position-level: `partial_fill_timeout_minutes: 5` cancels stuck partial fills;
  `force_close_time: 15:15` force-closes everything; `max_api_failures: 3` → HARD_KILL.

### tgt_retry
**File:** `system_config.yaml → tgt_retry`
**Current:** `enabled: true`, `poll_interval_sec: 30`, `max_attempts: 5`, `backoff_base_sec: 30`

**What it does:** Re-places a target that failed to place while the SL is live (backoff
30/60/120/240/480 s, give up after 5 → position stays SL-protected). See `[[tgt_retry_mechanism]]`.

### clock skew
**File:** `system_config.yaml → clock`
**Current:** `warn 2.0`, `alert 5.0`, `halt 30.0` s; `startup_max_skew_sec: 30.0`

**What it does:** If the VM clock drifts from the broker server beyond these thresholds:
log → Telegram alert → SOFT_KILL. Refuses to start if startup skew > 30 s.

### live_feed (WebSocket)
**File:** `system_config.yaml → live_feed`
**Current:** `max_reconnect_attempts: 10`, backoff base 1 / max 30 s

**What it does:** After 10 consecutive tick-stream reconnect failures, trips a SOFT_KILL
(in-session; downgraded to WARNING outside market hours per FIX-189).

---

## Section 7: Alerts & Notifications

### telegram.enabled  (master switch)
**File:** `system_config.yaml → alerts.telegram.enabled`
**Current:** `true` · **Type:** boolean

**What it does:** **Master ON/OFF for ALL Telegram alerts.** Set `false` for total silence
(TASK-10). Honored by every send path. See `[[task_10_telegram_master_switch]]`.

### telegram (other)
**File:** `system_config.yaml → alerts.telegram`
- `telegram_alerts_in_paper_mode: true` — also alert in paper mode.
- `max_retries: 3`, `retry_backoff_seconds: 2.0`, `rate_limit_per_minute: 20`.
- `channels:` — primary channel enabled, secondary disabled; chat IDs come from env vars.
- Tokens/chat IDs live in `.env` + the systemd drop-in, never in this file.

### smtp (email — alert-watcher)
**File:** `system_config.yaml → alerts.smtp`
**Current:** Gmail `smtp.gmail.com:587` TLS, user/from/to = `ramakrishnan031@gmail.com`,
password via env `ALERT_SMTP_PASSWORD`.

**What it does:** Where CRITICAL sentinel flags are emailed (subject `[LFL836] <SEVERITY> — …`,
digest when >`alert_digest_threshold`=3 pending). The password is **never** in the file —
it's the `ALERT_SMTP_PASSWORD` env var (a Gmail App Password). See `[[smtp_alert_watcher_config]]`.

### email_fallback
**File:** `system_config.yaml → alerts.email_fallback`
**Current:** `enabled: true`, Gmail TLS, addresses via env vars.

**What it does:** Emails CRITICAL alerts when Telegram delivery fails.

### alerts (sentinel plumbing)
**File:** `system_config.yaml → alerts`
- `sentinel_dir: data_store` — where CRITICAL `*.flag` files are written.
- `watcher_max_attempts: 5`, `alert_digest_threshold: 3`, log/lock paths.

---

## Section 8: Cron Jobs

### Source of truth
**File:** `config/cron_registry.yaml` — **the authoritative list of all cron jobs.**

**What it does:** Defines every scheduled job (script, schedule, critical flag,
market-day-only, cadence, monitored). The live `crontab` is **generated** from this file
(`deploy/cron/trading-system.cron`) — never hand-edit the crontab. See `[[task_3_cron_officer]]`.

**To change a job:** edit `cron_registry.yaml` → regenerate the `.cron` file →
`crontab deploy/cron/trading-system.cron`.

### Per-job fields
- `critical: true` → a SUCCESS run also alerts (and FAILED always alerts).
- `market_day_only: true` → the script self-skips on NSE holidays (emits a SKIPPED heartbeat).
- `cadence` → daily / market_day / intraday / hourly / weekly / monthly (drives "is it due today").
- `monitored: true` → the Cron Officer / drift checker expect a heartbeat when due.

### ⚠️ FIX-189 rule (don't break this on regeneration)
The generated `.cron` **must** keep a leading `SHELL=/bin/bash` line **and** source via
`. ./.env` (with the `./`). cron's default `/bin/sh` is dash, which won't source a bare
relative path → every job dies silently. Enforced by a unit test.

### Holiday calendar
**File:** `config/nse_holidays_2026.yaml` — NSE trading-holiday dates. Market-day jobs and
the entry engine consult this. Update it at year-end / when NSE revises the calendar.

---

## Section 9: Database & Storage

> ⚠️ Most DB/storage settings are **paths and retention windows**, not trading knobs.
> Changing a path can break the running system — only touch with care.

- **Main DB:** `data_store/trading_system.db` (schema v30). **Raw sqlite access must use
  `core.db_connect.connect`** (it ATTACHes `analytics.db`). See `[[db_schema_v28_split]]`.
- **Analytics DB:** `data_store/analytics.db` (ATTACHed at runtime).
- **Backups:** nightly `.backup` to `data_store/backups/`, 7-day retention (cron 01:00 /
  01:05 / 02:00). DB row pruning + Sunday VACUUM via `db_retention.py` (02:30).
- **Session token:** `data_store/session/zerodha_token.json` — wiped 05:00 daily so a fresh
  login is forced.
- **Logs:** `logs/system_YYYY-MM-DD.log` etc.; a cron deletes `*.log` older than 30 days.
- `logging.min_free_disk_gb: 2.0` (`system_config.yaml`) — refuse to start if free disk < 2 GB.
- `accounts.csv → paper_capital` — the paper-mode starting capital per account (LFL836 =
  ₹10,000). Live capital comes from the broker, not this file.

---

## Section 10: System Health & Monitoring

- **`/health`** (port 8080) → `{db, token, kill_switch}`, returns 200 healthy / 503 unhealthy.
- **`/metrics`** → runtime counters incl. the Bug B broker-quota gauges
  (`broker_in_flight`, `broker_filled_today`, `broker_quota_used/max/available`) and the
  signal funnel (`signals_processed`, `entries_placed`, `entries_throttled`, `entries_rejected`).
  `broker_quota_max` reflects the *effective* daily cap (`risk.max_daily_trades`, now 10
  — BUILD 1 removed live test mode) — handy to confirm what the system is actually enforcing.
- **Heartbeats:** every monitored cron emits a heartbeat; `check_cron_drift.py` (18:00) and
  the Cron Officer alert if an expected job goes silent.
- **System Manager** (`scripts/system_manager.py`, 18:45) runs 8 deep EOD cross-checks
  (config-vs-actual, order quality, report integrity, DB integrity, strategy/risk health,
  vs-yesterday, tomorrow-ready) and can trip tomorrow's SOFT_KILL on a real violation. See
  `[[task_5_system_manager]]`.
- `clock.probe` / `disk_monitor` (hourly cron) round out health monitoring.

---

## Section 10b: Config Sanity Auditor ⭐ BUILD 2 (25-Jun)

**What it is:** an automatic referee that checks your config makes *sense as a whole*
(not just that each value is individually valid). It is the enforcement layer of the
Config Authority work — BUILD 1 cleaned the config, BUILD 2 keeps it clean. It
**validates and alerts only**; it never changes how trading behaves (the one exception
is the contradiction BLOCK, which already refused to boot before BUILD 2).

**Where you see it:** the **09:20 pre-flight email + Telegram**, in a section titled
**Config Sanity** with 7 rows (A–G). It also runs at **startup** — a BLOCK-level
contradiction stops the app from booting (fail fast, rather than trade nothing silently).

**What each verdict means:**
- **✅ PASS** — that group is clean.
- **⚠️ WARN** — boots and trades fine, but something looks off; read it. (e.g. a daily
  loss limit set above 10%, a slippage override with a typo'd symbol, a deleted key that
  reappeared in the YAML.)
- **🔴 BLOCK** — a logical contradiction that would make the system trade *nothing*. The
  app **refuses to start** until you fix it. Only group A can BLOCK.

**The 7 groups:**

| Row | Checks | Worst verdict |
|---|---|---|
| **A — Contradictions** | `force_intraday_only=true` + `trade_type=DELIVERY` (the #10 dead-system guard); trade_type out of domain; "0 strategies would trade" | **BLOCK** |
| **B — Single-source** | a key BUILD 1 deleted has reappeared (`capital.daily_loss_limit`, `max_position_value_rs`, `live_test_*`, scoring `tier_multipliers`) | WARN |
| **C — Capital-relative** | pct values in sane ranges + the ladder `max_concentration_pct < max_position_value_pct` (the catastrophe cap must be looser than the routine concentration cap) | WARN |
| **D — Active overrides** | lists every non-empty slippage override so you SEE what's active each morning; warns on a typo'd/extreme override | INFO/WARN |
| **E — Launch-phase** | reminds you which params are `[LAUNCH-PHASE]` conservative (today: `entry_start` 10:00) — review as the account scales | INFO |
| **F — Stale-default** | a component's built-in default has drifted from the YAML (would bite if something is constructed without the config) | WARN |
| **G — Cross-field** | `entry_end` too close to square-off; leverage > 10×; micro tick size; per-strategy window outside the global one | WARN |

**Sample (the shipped config):**
```
Group — Config Sanity  (7/7 ok)
  A_contradictions   ✅ PASS  no contradictions
  B_single_source    ✅ PASS  no resurrected deleted keys
  C_capital_relative ✅ PASS  caps sane (loss 3% <= 10%, conc 10% < pos-cap 40%, risk/trade 1%)
  D_active_overrides  ✅ PASS  no active slippage overrides — pure global fraction everywhere
  E_launch_phase      ✅ PASS  entry_start=10:00 [LAUNCH-PHASE] — relax toward 09:20 as the account scales
  F_stale_defaults    ✅ PASS  component defaults match config intent
  G_cross_field       ⚠️ WARN  entry_end (15:15) within 15min of eod_squareoff_time (15:17)
```
The lone WARN (G) is intentional and long-standing — `entry_end` and square-off are 2
minutes apart by design. **Code:** `core/config_auditor.py` (rules),
`scripts/preflight/checks/config_sanity.py` (the 7 rows). No DB schema; same in paper + live.

---

## Section 11: Strategy & Signals Source

### Per-strategy config
**File:** `config/strategies/<strategy>.yaml` (one per strategy)

**Key fields (per file):**
- `direction` (LONG/SHORT), `intent` (INTRADAY/DELIVERY — overridden to INTRADAY while
  `force_intraday_only` is ON), `order_protocol` (CO_PLUS_TGT / LIMIT_TRIPLE).
- `entry_method`/`entry_offset_pct` — how the entry limit price is set.
- `sl_method`/`sl_pct`/`sl_min_pct`/`sl_max_pct`/`sl_atr_multiplier` — stop-loss sizing.
- `tgt_method`/`tgt_risk_reward`/`tgt_pct` — target sizing (RISK_REWARD = R multiple).
  - **Standardized baseline (24-Jun, Part C):** ALL 15 strategies trade at
    `tgt_risk_reward: 1.5`. To retune one strategy's target, edit `tgt_risk_reward`
    in its YAML → commit → push → restart; the new value now reaches the broker
    correctly (the fill-time R:R fix, Slice 1, 22-Jun: the broker TGT uses each
    strategy's own R:R, frozen at placement into `trades.tgt_risk_reward_applied`
    and re-read from the actual fill — was previously a hardcoded 2.0).
- `smart_tgt_enabled` + trail trigger/step — trailing-target behaviour.
- `min_volume_surge`, `min_adr_pct`, `max_spread_pct` — per-strategy entry filters.
- `lot_size`, `max_concurrent_positions` — per-strategy sizing/caps. (BUILD 1: per-strategy
  `max_risk_pct` was removed — it was dead in live; sizing uses the global `risk_per_trade_pct`.)
- `entry_start_time`/`entry_end_time`, `active_days` — per-strategy time window (can only
  narrow the global `trading_hours` window, never widen it).

### Strategy Control — ON/OFF + product type (Slice 2, 24-Jun)

Three plain-English layers decide whether a strategy trades today. A strategy
**WILL TRADE only if** it is enabled **AND** its product type is allowed by the
master **AND** the emergency breaker doesn't block it.

- **`enabled:` (per strategy YAML)** — the simple ON/OFF switch. `true` = the
  strategy can trade; `false` = it never trades (regardless of anything else).
  Default `true`. **To turn a strategy OFF:** set `enabled: false` in its YAML →
  commit → push → restart. (This replaces the old "remove the scanner mapping"
  workaround.) All 15 currently ship `enabled: true`.
- **`trade_type:` (system_config.yaml)** — the master product gate: `INTRADAY`
  (default — only intraday strategies trade), `DELIVERY` (only delivery), or
  `BOTH`. It only GATES which strategies trade; it does **not** itself place CNC.
- **`force_intraday_only:` (system_config.yaml)** — the emergency breaker (top
  priority). While `true` (current default), every strategy is forced to trade
  intraday (MIS); no CNC/delivery order can ever be placed. This is why the three
  `positional_*` "delivery" strategies currently trade **as intraday**.

**Status table:** the Cron Officer 09:20 briefing (email + Telegram) shows a
STRATEGY STATUS table — each strategy's Type (its true INTRADAY/DELIVERY nature),
the master mode, its switch, and the WILL/WON'T TRADE verdict (computed by the
exact same logic the live entry gate uses, so the table never lies).

⚠️ **Delivery (CNC) is not live yet.** Even with `trade_type: BOTH` + a delivery
strategy enabled, real overnight delivery needs the **Delivery lifecycle project
(Slice 2.5)** — square-off exemption, EOD reconcile, overnight carry, GTT — before
it should go live. Until then keep `trade_type: INTRADAY` (delivery strategies
keep running safely as intraday).

### Scanner → strategy mapping
**File:** `config/scan_webhook_map.yaml`

**What it does:** Maps each Chartink scanner name → a strategy YAML. Every strategy named
here must have a file in `config/strategies/`, or startup fails CRITICAL. This is how an
inbound webhook becomes a strategy decision.

### Scanner URLs (pre-flight check)
**File:** `config/chartink_scanners.yaml`

**What it does:** Canonical Chartink URLs the startup pre-flight HEAD-checks for
reachability. (Signals actually arrive via inbound webhooks, so a 403 here is cosmetic —
see `[[fix_184_scanner_ua_403]]`.)

### Other reference data
- `config/instruments.csv` — instrument master (lot sizes, tokens).
- `config/symbol_aliases.yaml` — symbol-name normalisation.
- `config/reference_data/index_members/*.csv` — index/sector membership.
- `config/broker_costs.yaml` — brokerage/STT/GST/stamp-duty model (verify vs Zerodha
  contract notes each season; `_pct` fields are **percentages**, e.g. `0.03` = 0.03%).
- `config/slippage_model.yaml` — per-liquidity-tier slippage in bps (liquid 5 / mid 15 /
  small 30) applied to paper fills and cost math.

---

## Section 12: Override Precedence (CRITICAL)

> This is the **most important section**. When multiple settings could control one
> behaviour, this is who wins. The *effective* value is what the running system uses.

### Trading mode (paper vs live)
1. systemd `ExecStart` flag → `main.py --mode live` ← **how it's set today**
2. (interactive start can flip it, but the service is non-interactive)
> **Effective: live.** ⚠️ There is **no `mode:` key in any YAML.** To run paper, change
> the systemd unit's `ExecStart` to `--mode paper` (or launch manually with `--mode paper`).

### Max open positions  (BUILD 1: single source)
1. `risk.max_open_positions` ← **5**
> **Effective: 5.** The `live_test_*` override was removed (#3).

### Max daily trades  (BUILD 1: single source)
1. `risk.max_daily_trades` ← **10**
> **Effective: 10.** Reservation-aware → bursts can't overshoot. `live_test_*` removed (#3).

### Daily loss limit (one pct source, two bases — *both* active)  ⭐ BUILD 1
1. `risk.daily_loss_limit_pct` (0.03), **pre-trade** — includes **unrealised** P&L.
2. `risk.daily_loss_limit_pct` (0.03), **post-close** — **realised** only (FundManager).
> **Effective: 3% × capital on whichever trips first** (₹300 on ₹10k). One source, two
> bases — not redundant (different timing + P&L basis). The old absolute
> `capital.daily_loss_limit` was deleted. See `[[dual_daily_loss_mechanism]]`.

### Position size (smallest cap wins)
Computed size = min of:
1. `risk_per_trade_pct` (1% → ~₹100 risk)
2. `max_concentration_pct` (10% → ₹1,000 notional) ← **usually binds on ₹10k**
3. `max_position_value_pct` (40% → ₹4,000 on ₹10k) — capital-relative reject backstop
…then × quality-tier multiplier × win-rate multiplier (floor 0.5 / cap 2.0).
> **Effective binding cap today: concentration (₹1,000/symbol)** → qty 1–2 shares.

### Strategy intent (product)
1. `force_intraday_only: true` → forces **INTRADAY/MIS** for every strategy ← **wins**
2. per-strategy `intent:` (INTRADAY/DELIVERY)
> **Effective: INTRADAY (MIS) for all** while `force_intraday_only` is ON.

### Entry time window
1. per-strategy `entry_start_time`/`entry_end_time` (can only **narrow**)
2. global `trading_hours.entry_start`/`entry_end` (10:00–15:15)
3. `eod_entry_cutoff` (15:15) — absolute hard stop, beats everything
> **Effective: the *narrowest* of the three.**

### Telegram alerts
1. `alerts.telegram.enabled` (master) ← **true**
2. per-channel `enabled` flag
3. per-alert severity / paper-mode flag
> **Effective: ON** (master true; primary channel enabled).

### Max consecutive losses (day scope)
`risk.max_consecutive_losses` (4) — counts **today's** losses only (FIX-183); a prior
day's losses never carry over.

---

## Common Scenarios

### I want to test with smaller risk today
Edit `system_config.yaml` (BUILD 1: edit the base caps directly — `live_test_*` removed):
```
risk:
  max_daily_trades: 1
  max_open_positions: 1
```
Then restart: `deploy/resume.sh`

### I want to run paper only (no real orders)
`mode` is **not** a config key — it's the `--mode` flag in the systemd unit.
- Quick/manual: stop the service and run `PYTHONPATH=. venv/bin/python main.py --mode paper`.
- Durable: edit `trading-system.service` `ExecStart` to `--mode paper`, then
  `sudo systemctl daemon-reload && sudo systemctl restart trading-system`.
- (Base caps `max_open_positions` / `max_daily_trades` apply in both paper and live.)

### I want to silence all alerts temporarily
Edit `system_config.yaml → alerts.telegram.enabled: false`, then restart.
(CRITICAL email sentinels are a separate path; disable those via the alert-watcher if needed.)

### I want to be more conservative on entries
- **Best knob:** raise `scoring_weights.yaml → min_pass_score` (e.g. 60 → 70).
- Or lower `risk.max_daily_trades`.
- Or tighten `entry_gate.max_spread_pct` / raise `min_effective_rr`.

### I want bigger position sizes (qty > 1–2)
Raise `position_sizing.max_concentration_pct` (the binding cap on ₹10k), e.g. 0.10 → 0.25.
⚠️ This increases per-symbol exposure — read Section 6 warnings. See
`[[config_change_needed_concentration]]`.

### I want to stop trading a specific symbol
Add it to `system_config.yaml → excluded_symbols`. Restart.

### I want to stop a specific strategy
Remove its entry from `config/scan_webhook_map.yaml` (no signals route to it). Restart.
(Confirm the method first — there is no per-file `enabled:` toggle.)

---

## ⚠️ Dangerous Changes — Read Before Touching

Changes here can cause **REAL MONEY LOSS** or disable safety nets:

- `risk.max_open_positions` / `max_daily_trades` — more concurrent / total risk.
- `risk.daily_loss_limit_pct` — raising lets the account lose more before it halts (3% × capital).
- `scoring_weights.yaml → min_pass_score` — lowering admits weaker signals.
- `position_sizing.max_concentration_pct` / `max_position_value_pct` — raising increases
  per-symbol exposure.
- `capital.leverage_map` — raising borrows more margin from the broker.
- `force_intraday_only: false` — allows overnight (CNC) positions.
- `kill_switch.enable_auto_trip: false` — disables auto SOFT_KILL on API failures.
- `drift_handler.*_threshold_rs` — lowering/raising changes when HARD_KILL flattens everything.
- `broker_limits.yaml` rates — raising past Zerodha's limits risks throttling/ban.

**If unsure, ASK before changing.** And remember: nothing takes effect until a restart.

---

## Quick Commands

```bash
# View a setting (with context)
grep -A 2 'min_pass_score' config/scoring_weights.yaml
grep -A 6 'risk:' config/system_config.yaml

# Edit a config file
nano config/system_config.yaml

# Restart cleanly to apply changes (preferred — handles kill switch + systemd)
deploy/resume.sh
#   …or a plain restart if the service is already healthy:
sudo systemctl restart trading-system

# Verify the service is healthy after restart
curl -s http://localhost:8080/health | jq

# Check the effective daily cap right now (base caps are the sole authority — BUILD 1)
grep -A 6 'risk:' config/system_config.yaml   # max_open_positions / max_daily_trades
```

---

## See Also
- `docs/SYSTEM_MAP.md` — authoritative paths / ops / cron / services.
- `PATHS.md` — one-screen quick path reference.
- `docs/03_daily_operations_runbook.md` / `docs/RUNBOOK.md` — daily ops.
- `docs/04_db_schema_reference.md` — DB schema.
