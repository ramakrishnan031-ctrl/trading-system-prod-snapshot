# Full System Security & Integrity Audit — 2026-07-02

**Scope:** entire repository (`D:\Projects\trading-system`, branch `fix-check9-false-positive-01jul`, HEAD `59f83b5`).
**Type:** INVESTIGATION ONLY — no code/config/cron was modified. Findings feed later fix decisions (each fix = investigate-then-act, PERMANENT, paper+live PARITY).
**Method:** 5 parallel category audits (deepest on A–C = money + exploitation), then **every Critical/High + the material Mediums personally re-verified against source by the lead auditor** (verification status noted per finding).
**Parity rule:** every finding states PAPER / LIVE / BOTH.
**Known-fixed items** (RAMCOIND 4-layer, emergency-exit tag, CHECK9 race-aware, FIX-190 cascade, tick-snap, circuit-band, Config Authority arc, kill-switch last-mile, report redesign) were verified present and are NOT re-reported.

---

## 1. TRIAGE SUMMARY — Critical & High (ranked, all lead-verified)

| # | Sev | Confidence | Title | file:line | Affects |
|---|-----|-----------|-------|-----------|---------|
| **C-1** | **CRITICAL** | CONFIRMED | Live account credentials (api_key + api_secret + **TOTP 2FA seed** for LFL836 & DR6114) + Telegram bot token + WEBHOOK_SECRET committed in git-tracked `.env.example` (and git history) | `.env.example:14-16,19-21,39,47` | BOTH |
| **A-1** | **HIGH** | CONFIRMED (gap) / LIKELY (naked) | Timed-out entry that DID reach the broker → marked FAILED + capital released without confirming broker absence → fills later as an **unprotected naked position** | `order_reconciler.py:3113-3186` | LIVE |
| **A-2** | **HIGH** | CONFIRMED (path); today masked by throttle | `BrokerTimeoutError` is retried → pipeline re-runs → **second `kite.place_order` = double entry / 2× exposure** (no idempotency key; contradicts FIX-068) | `signal_processor.py:1009` + `order_placer.py:1291-1323` | LIVE |
| **B-1** | **HIGH** | CONFIRMED | Daily-loss pre-trade gate's `unrealized_mtm` term is **dead code** (writers called only by tests) → both loss controls are realized-only → concurrent open drawdown can overshoot the 3% limit | `risk_engine.py:500` + `fund_manager.py:1265-1296` | BOTH |
| **C-2** | **HIGH** | CONFIRMED | Webhook bound `0.0.0.0` with `require_hmac:false`; sole live auth = the (committed) `?token=` over plaintext HTTP — the config's own stated precondition is violated | `system_config.yaml:179,187` + `webhook_receiver.py:345-367` | BOTH |

**One-line takeaway:** C-1 is the emergency — the webhook's only live auth control (WEBHOOK_SECRET) plus both live-account API keys and their TOTP 2FA seeds are published in a committed file, so signal-injection and 2FA protection currently rest entirely on an unverifiable network firewall. **Rotate every credential in `.env.example` immediately**, then lock down the webhook bind/HMAC (C-2). A-1/A-2 are the top *code* risks (naked position / double entry on the live order path); B-1 is a non-functioning advertised safety control.

---

## 2. FINDINGS BY CATEGORY

### A. ORDER / EXECUTION INTEGRITY

#### A-1 · HIGH · CONFIRMED (code gap) / LIKELY (naked outcome) · LIVE · **lead-verified**
**Timed-out entry that reached the broker becomes an unmanaged naked position.**
- **file:line:** `orders/order_reconciler.py:3113-3186` (`_check_unknown_in_flight`); enabled by `orders/order_placer.py:1291-1323` (timeout handler) + `_persist_entry_orders` running only *after* `execute()` returns (`order_placer.py:1203` then ~`:1357`).
- **Scenario:** `kite.place_order` for the ENTRY times out but the order actually reached Zerodha (the exact case FIX-068's `UNKNOWN_IN_FLIGHT` exists for). Because the timeout fires before `execute()` returns, no `orders` row / `broker_order_id` is ever persisted. The recovery loop matches broker open-orders only against `get_orders_for_trade(trade_id)` (line 3113) — which is empty — so `found_at_broker` is永 False and after 3 polls (~45s) the trade is marked **FAILED and capital released** (3155-3186) *without ever confirming the broker has no such order*. When the resting order later fills, `get_all_open_trades()` excludes the FAILED trade, so CHECK2 `_check2_orphan_adoption` classifies the broker position as a **"human/untracked order" and does NOT protect or flatten it** (once/day WARNING only). HARD_KILL's inflight-orphan flatten matches only `PENDING_FILL`/`PENDING`, not `FAILED`.
- **Evidence:** loop keys on a persisted `broker_order_id` that does not exist for timeouts:
  ```python
  # order_reconciler.py:3113-3118
  trade_orders = self._store.get_orders_for_trade(trade_id)
  found_at_broker = False
  for order_row in (trade_orders or []):
      broker_order_id = order_row.get("order_id", "")
      if broker_order_id in broker_orders:   # never true when no row was persisted
  ```
  Inline comment 3128-3132 admits it is "a simplified recovery." **Root enabler (lead-verified):** the broker `tag` (`truncate_tag_for_broker(trade_id)`) is placed on every order but is **never read for correlation** in the reconciler (grep: `tag` appears only on order *placement* lines 1521/1680/2229/2438, never matching an inbound broker order).
- **Backstop:** the EOD residual sweep (`eod_squareoff._sweep_residual_broker_positions`, ~15:17) flattens the symbol, so exposure is bounded to intraday — but it is a genuinely unprotected (no broker SL) position for the rest of the session.
- **Direction:** correlate the broker order/position by **tag** (and/or symbol+side+qty+time-window), not by a `broker_order_id` that doesn't exist for timeouts; adopt+protect on a positive match, flatten under HARD_KILL; do not mark FAILED + release until the broker is *confirmed* to have no such order. See also E-1 (crash variant, same root).

#### A-2 · HIGH · CONFIRMED path (today mitigated by the entry throttle) · LIVE · **lead-verified**
**Retry on `BrokerTimeoutError` re-submits the entry (double entry / 2× exposure, no idempotency key).**
- **file:line:** `signals/signal_processor.py:1009` (catches `BrokerRateLimitError` **and** `BrokerTimeoutError`, re-queues) → retry re-runs pipeline → `orders/order_placer.py:878` `create_trade` mints a fresh `trade_id` → `orders/order_manager.py:640-646` `link_signal_trade` blind UPDATE (no one-trade-per-signal enforcement) → `order_placer.py:1203` second `kite.place_order`. `order_placer.py:1291-1323` marks `UNKNOWN_IN_FLIGHT` then `raise`s (propagates the timeout up to the retry catch).
- **Scenario:** on an ambiguous timeout (order may already be live), the signal is retried and a second entry is submitted → if the first order was actually placed, 2× intended exposure, each leg getting its own SL/TGT. This contradicts FIX-068's own comment ("Do NOT mark FAILED… let reconciler poll").
- **Why not firing today:** the retry re-hits `entry_throttle.admit(symbol)` (`signal_processor.py:981`); `min_gap` 20s + `per_symbol_cooldown` 300s (`system_config.yaml:196-199`) reject the near-immediate retry. **This is a rate-limiter, not a purpose-built idempotency guard** — set all three throttle gates to 0 (→ `EntryThrottle.enabled` False), or delay the retry past the gap, and the double-submit goes live.
- **Secondary (same path):** on re-queue signal_processor releases the reservation (`:1046-1048`) while order_placer's FIX-068 deliberately kept it for the `UNKNOWN_IN_FLIGHT` trade → capital-coordination inconsistency; if that trade's order fills, capital was already released (under-count for a real position).
- **Direction:** do NOT retry on `BrokerTimeoutError` — only client-side `BrokerRateLimitError`/429 (raised pre-submission by `rate_limiter.acquire`, nothing sent) is retry-safe. Let the timeout propagate and let the `UNKNOWN_IN_FLIGHT` trade + reconciliation (A-1) own it. Don't rely on the throttle for idempotency.

#### A-3 · LOW-MEDIUM · LIKELY · BOTH (materially LIVE)
**Kill-switch TOCTOU window inside `place()` (network I/O between check and submit).**
- **file:line:** `orders/order_placer.py:932` (`is_active("entry")` last-mile) vs actual submit `order_placer.py:1203`; between them are `_fetch_ltp`, `get_quote` (drift), `_check_liquidity` — all network calls.
- **Scenario:** a `SOFT_KILL` activated by another thread after :932 but before :1203 does not stop this entry, and `soft_kill()` has no order-cancellation backstop (only `hard_kill` cancels). Net: one entry can "leak" past a just-activated kill. Bounded — the leaked entry is still SL/TGT-protected (one extra position, not a naked one).
- **Direction:** re-check `is_active("entry")` (cheap, in-memory) immediately before `engine.execute()`, after the best-effort network I/O.

### B. CAPITAL / RISK EXPOSURE

#### B-1 · HIGH · CONFIRMED · BOTH · **lead-verified (grep decisive)**
**Daily-loss controls are realized-only — the unrealized-MTM term is permanently 0, so open drawdown can overshoot the 3% limit.**
- **file:line:** `capital/risk_engine.py:497-512` (gate reads `total_pnl = daily_pnl + get_total_unrealized_mtm()`); `capital/fund_manager.py:1265-1296` (the only writers, `update_unrealized_mtm`/`remove_unrealized_mtm`).
- **Evidence:** whole-tree grep shows the MTM writers are called **only** in `tests/unit/test_fund_manager.py`; the private dict `self._unrealized_mtm` is written only inside those two setters (lines 1275/1285). No monitor/order-path/main.py caller. Therefore `get_total_unrealized_mtm()` returns `0.0` on every production call → the pre-trade DAILY_LOSS gate is realized-only, and the post-close control also reads realized-only (`fund_manager.py:1071`). The 01-Jun architectural-audit doc (`docs/architectural_audit_2026-06-01.md:52`) *incorrectly* claims this gate "sums all live trades' mark-to-market" — the gap was not understood.
- **Scenario:** N concurrent open positions each drift adversely (0 realized) — the pre-trade gate sees `total_pnl=0` and keeps admitting; nothing reacts to open drawdown; a correlated gap stop-out realizes all at once, overshooting `daily_loss_limit_pct` before any halt. Overshoot ≈ (concurrent positions × per-trade risk). Bounded (not removed) by per-position SLs, the consecutive-loss halt, the post-close absolute gate, and current tiny-capital / max_open=4 config. **This is NOT W10** (W10 is a separate cost double-count on the realized term).
- **Direction:** feed `update_unrealized_mtm`/`remove_unrealized_mtm` from the existing open-position LTP monitor (order_monitor), or add a periodic unrealized-drawdown soft_kill — so the advertised control actually functions.

#### B-2 · LOW-MEDIUM · LIKELY · BOTH
**SECTOR_EXPOSURE check is not reservation-aware (residual TOCTOU), unlike OPEN_POSITIONS / DAILY_TRADES.**
- **file:line:** `capital/risk_engine.py:514-518` reads `existing_sector_margin` from `state_store.sector_exposure()` (DB, `status IN ('PENDING_FILL','OPEN','PARTIAL')`). The new trade's row is written by `order_placer.place()` *outside* `portfolio_lock` (`signal_processor.py:855-885` holds the lock only for approve+reserve; `place()` at ~:992 after release), so two same-sector signals can both read a total that omits the other. OPEN_POSITIONS/DAILY_TRADES were hardened for this with `count_live_reservations()` (FIX-185/Bug B); SECTOR was not (reservations carry no sector).
- **Impact today: LOW** — at max_open=5 and 5× INTRADAY leverage, per-position margin ≈2% of capital, so ~20 same-sector positions would be needed to approach the 40% cap → unreachable. Becomes relevant only if leverage drops (DELIVERY 1×) or caps rise.
- **Direction:** attach sector to reservations and make the check reservation-aware, or document that the cap is unreachable at current config.

### C. SECURITY / EXPLOITATION

#### C-1 · CRITICAL · CONFIRMED · BOTH · **lead-verified**
**Live production secrets committed to git in `.env.example`.**
- **file:line:** `.env.example:14-16, 19-21, 39, 47` (git-tracked — `git ls-files` confirms; last touched commit `9f58848`; present in history).
- **Evidence:** real, non-placeholder values (redacted here): `ZERODHA_API_KEY_LFL836` / `_API_SECRET_LFL836` / `_TOTP_LFL836` (api key + secret + **TOTP 2FA seed** for the PRIMARY live account, real capital); the same trio for the **ENABLED** DR6114 account; a real-format `TELEGRAM_BOT_TOKEN`; and `WEBHOOK_SECRET` (64-hex, with the comment "Do NOT regenerate" — i.e. the in-use value). These stand in deliberate contrast to the `FILL_WHEN_READY` placeholders on the disabled accounts in the same file, proving they are real.
- **Who can do what:** anyone with read access to the checkout, the VM bare repo, or any clone gets: (a) the webhook secret → inject signals (C-2); (b) the Telegram bot token → read/spoof operator alerts; (c) Zerodha api_key/secret + **TOTP seed** → the exposed seed permanently collapses the account's 2FA to 1FA and enables OAuth/request_token abuse. Secrets are in git **history**, so editing the file is insufficient.
- **Direction:** rotate ALL exposed credentials now (Zerodha keys + regenerate TOTP, Telegram bot token, WEBHOOK_SECRET); replace `.env.example` with placeholders; purge history (git-filter-repo/BFG) + force-push; add a pre-commit/CI secret scanner (the existing `deploy/hooks/pre-commit` did not catch this).

#### C-2 · HIGH · CONFIRMED · BOTH · **lead-verified**
**Webhook on `0.0.0.0` with `require_hmac:false`; sole live auth is the committed URL token over plaintext HTTP.**
- **file:line:** `config/system_config.yaml:179` (`bind_host:"0.0.0.0"`), `:187` (`require_hmac:false`); server `main.py:2800-2813` (Waitress, plaintext); auth `signals/webhook_receiver.py:345-367`; live-required-secret `main.py:1689-1690` (`if not is_paper: required_secrets.append("WEBHOOK_SECRET")`).
- **Evidence:** the config's own comment (`system_config.yaml:181-185`) says the all-interfaces bind "relies on **require_hmac=true** plus firewall" — but line 187 is **false**. So in live the only accepted auth is `?token=<WEBHOOK_SECRET>` (constant-time compared, but the token rides the URL query string, is logged by any proxy, and is committed per C-1). In **paper**, `WEBHOOK_SECRET` is not required → if unset, `if self._secret:` is skipped entirely → unauthenticated signal injection.
- **Who can do what:** anyone able to reach `VM:5000` who knows the token (public via C-1) can POST `/webhook/<scanner>` → real trades in live. Residual protection is only the Oracle Cloud security group + iptables (not verifiable from the repo).
- **Direction:** prefer `bind_host:127.0.0.1` behind a TLS reverse proxy + IP allowlist (Chartink egress); or set `require_hmac:true` (constructor already enforces a secret must exist) with signing at a proxy. Add per-IP rate limiting.

#### C-3 · MEDIUM · CONFIRMED · BOTH · **lead-verified**
**Health/metrics HTTP server on `0.0.0.0:8080` with no auth leaks P&L, capital, kill-switch state, account_id.**
- **file:line:** `scripts/healthcheck_server.py:244` (`host="0.0.0.0"` default; `main.py:2824` doesn't override); `/metrics` (`:110`) returns `daily_pnl`, `capital_deployed_pct`, `open_positions`, `kill_switch_state`, `trades_today`, etc.; `/health` returns `account_id`, token `expires_at`, kill-switch reason, and raw DB error strings.
- **Who can do what:** anyone reaching `VM:8080` reads live P&L/capital/posture (reconnaissance; knowing when the kill switch is active). No credential exposure.
- **Direction:** bind `127.0.0.1` (or token/mTLS); drop raw exception strings and `account_id` from the unauthenticated payload.

#### C-4 · LOW-MEDIUM · CONFIRMED (code) · BOTH · **lead-verified**
**Broker token file written without restrictive permissions.**
- **file:line:** `scripts/zerodha_login.py:159-160` (`save_token`, used by the headless `auto_refresh_token.py` path): `open(token_path,"w")` + `json.dump` — no `os.chmod(0o600)`, relies on umask (typically 0644 = world-readable). `record` holds `access_token` + `api_key`.
- **Direction:** `os.chmod(token_path, 0o600)` after write; create `session/` dir `0o700`. Mitigated by the VM being single-user (not verifiable from repo).

#### C-5 · LOW · CONFIRMED · BOTH
**Partial secret fragments printed to stdout/logs.**
- **file:line:** `scripts/fetch_daily_candles.py:176` (`access_token[:8]`); `scripts/auto_refresh_token.py:371` (`totp[:3]***`, masked). Low value, avoidable.
- **Direction:** log booleans (`token_loaded=True`), not prefixes.

### D. DATA INTEGRITY

#### D-1 · MEDIUM · CONFIRMED (TOCTOU pattern) · BOTH · **lead-verified**
**`close_trade` uses a non-atomic (check-outside-txn) close guard → narrow double-`release_used` window.**
- **file:line:** `orders/order_manager.py:533-542` reads status via `get_trade()` (own statement, autocommit) then checks `not in ('OPEN','PARTIAL')`; the commit at `:587-616` runs `UPDATE trades SET status='CLOSED' … WHERE trade_id = ?` with **no status guard**. This is the sole protection against a double capital release because `FundManager.release_used` (`fund_manager.py:965`) is **not idempotent** (keyed on symbol+intent, writes a `RELEASE_USED` row every call).
- **Scenario:** cross-thread with the reconciler (which *is* atomic via `mark_trade_manually_closed` `WHERE … AND status IN (…)` + rowcount, and skips release if it didn't win): if order_placer reads `OPEN`, the reconciler then fully closes+releases, and order_placer's unconditional UPDATE overwrites `CLOSED_MANUAL`→`CLOSED` and calls `release_used` **again** → double release, corrupting available capital and the `daily_realized_pnl` that gates the daily-loss kill. Fill dispatch itself is serial (single order_monitor poll thread), so pure OCO double-fills are safe; the exposure is the reconciler-vs-placer window (narrow, but real, on live capital).
- **Direction:** make the UPDATE conditional — `… WHERE trade_id=? AND status IN ('OPEN','PARTIAL')` and raise "already closed" on `rowcount==0`, mirroring the atomic pattern already used by `mark_trade_manually_closed` / `mark_trade_closed_gtt` / `revert_exiting_to_open`.

### E. RESILIENCE / SURVIVAL

#### E-1 · MEDIUM · LIKELY · BOTH · **lead-verified (ordering)**
**Crash between broker order-accept and local order-row persist → naked, unmanaged position intraday (same root as A-1, crash variant).**
- **file:line:** `orders/order_placer.py:1203` (`_engine.execute()` places at broker) then `_persist_entry_orders` at ~`:1357`. A *process-alive* persist failure is handled (cancel-all + hard_kill, ~1358-1381), but an unclean crash (SIGKILL/OOM/power/VM-reboot) between those runs no code.
- **Scenario:** broker holds a live ENTRY; trade row is `PENDING`, no `orders` row. On restart `order_monitor._cleanup_orphaned_pending_trades` (`broker/order_monitor.py:336-391`) marks it FAILED + releases capital (capital correct). If that broker order later fills, CHECK2 sees a position whose only local record is FAILED → `_check2_orphan_adoption` disowns it as human → naked, no SL, once/day WARNING until the EOD sweep (15:17) flattens it. Bounded to intraday.
- **Direction:** shared with A-1 — in CHECK2, before disowning as "human," check for a same-day FAILED/PENDING/UNKNOWN trade for that symbol (correlate by tag); adopt+protect or flatten; or don't mark FAILED until the broker is polled for a matching fill.

#### E-2 · LOW-MEDIUM · CONFIRMED · BOTH · **lead-verified** — (by-design; scoped residual)
**SOFT/HARD-kill auto-clear on restart is SOUND, but has no reason-allowlist.**
- **file:line:** `capital/kill_switch.py:199-248` (`clear_stale_state`) + `:250-288` (`auto_clear_scheduled_kill`); callers `main.py:1519,1524`.
- **Assessment (verified):** same-day kills always persist (`triggered_date >= today` → return False, line 220-221); `auto_clear_scheduled_kill` refuses HARD_KILL and only clears *scheduled* SOFT reasons with zero open positions; fail-safe on unparseable timestamp / query error (persists). A genuine same-day emergency kill **cannot** be auto-cleared. **Residual:** `clear_stale_state` clears **every** prior-day kill regardless of type (incl. HARD_KILL/operator-manual) with no allowlist — relying on startup reconcile re-deriving still-valid halts. Safe for states reconcile re-inspects (naked/orphan/drift/missing-SL) and for transient reasons (daily-loss/token/API), but a reason reconcile does *not* re-derive — most notably an operator's manual end-of-day "halt until I investigate" HARD_KILL — is silently lifted at the next 08:15 boot. Every clear is audited (`KILL_AUTO_CLEARED` system_event).
- **Direction (optional stricter posture):** keep an allowlist of auto-clearable reasons (scheduled/daily/connectivity) and require `--resume` for `triggered_by="operator"` manual HARD_KILLs; else document that manual overnight halts do not survive the morning boot.

#### E-3 · INFO · CONFIRMED · LIVE — (positive: fail-safe)
**Kite session expiry mid-session FAILS SAFE (halts).** `TokenException → BrokerAuthError`; halt paths: order_monitor 3 consecutive auth-fails → HARD_KILL (~6s), reconciler → soft_kill (~45s), token_monitor `profile()` fail → soft_kill(token_expired) (≤30 min); entries with an expired token raise (rejected, not placed blind); open positions stay protected by the **resting broker SL**. Minor note: a TOKEN_EXPIRED-classified error from the *placement path* emits only a CRITICAL log (no distinct alert like IP-403) — consider adding one so the operator learns of expiry immediately.

#### E-4 · LOW-MEDIUM · CONFIRMED · BOTH
**Core daemon threads have no liveness signal on `/health`.**
- **file:line:** `scripts/healthcheck_server.py:82-97` covers db/token/kill_switch/tgt_retry but **not** `order_monitor`, `order_reconciler`, `eod-scheduler`, or `live_feed`. Each loop is individually hardened against silent death, but `order_monitor` deliberately **stops itself** on 3 failures (`order_monitor.py:653,688`) and is only revived by a full process restart; its stoppage is invisible to external uptime monitors except indirectly via the kill_switch it trips. If the reconciler ever stopped, crash-recovery-SL / CHECK9 / orphan cleanup / capital-drift all silently cease.
- **Direction:** add `is_alive()`/last-cycle-timestamp checks for order_monitor, order_reconciler, eod_scheduler, live_feed to `/health` (mirror the tgt_retry pattern).

### F. CONFIG INTEGRITY

#### F-1 · LOW-MEDIUM · CONFIRMED · BOTH · **lead-verified**
**Configured `min_free_disk_gb` (2 GB) silently ignored — startup uses the hardcoded 1 GB default.**
- **file:line:** `main.py:1728` reads `getattr(app_config.system, "min_free_disk_gb", 1.0)`, but the field is defined once on the nested logging config (`core/config_loader.py:685`; YAML `config/system_config.yaml:262` under `logging:`). `app_config.system` (Pydantic, `extra="forbid"`) has no such attribute → the getattr always returns `1.0`. The operator-configured 2 GB startup disk floor (`utils/startup_checks.py:1589` `check_disk_space`) is never applied; the repo's own docs (CONFIG_GUIDE, architectural_audit) believe it is 2 GB.
- **Direction:** read `app_config.system.logging.min_free_disk_gb`.

---

## 3. HARDENING SUGGESTIONS (lower priority, not live bugs today)

- **agy subprocess gets the full environment** — `scripts/agy_runner.py:116` passes all of `os.environ` (every Zerodha/Telegram/webhook secret) to the third-party `agy` CLI. Pass a minimal allow-listed env. (Also: `agy` receives untrusted log/DB text as prompt input → prompt-injection surface, but output is observe-only.)
- **No per-IP webhook rate limiting** (only capacity backpressure) — a token-bearing or paper-mode caller can flood `/webhook`.
- **Webhook dedup set before DB insert** (`webhook_receiver.py:596`) — a non-duplicate DB failure suppresses a legitimate retry for the window (robustness, not security).
- **Entry slippage guard fails *open* when LTP unavailable** — `order_placer.py:985-991`: if both `release_ltp` and `_fetch_ltp()` are None, the guard block is skipped and the order proceeds with no entry-slippage bound (matters most for market/CO entries). Consider rejecting (or a conservative % cap) when LTP can't be fetched.
- **`release_slm_buffer()` is dead code** (`fund_manager.py:615`, no caller); the 5% SL-M buffer is reclaimed only implicitly at `commit_to_used`. Harmless but over-reserves during PENDING_FILL. Wire or remove.
- **Cross-store PnL asymmetry** — `close_trade` writes `net_pnl` to `trades`; `release_used` writes `pnl_delta` to `fm_ledger` (the daily-loss controls read only `fm_ledger`). If `release_used` raises after `close_trade` commits (`order_placer.py:2290-2299` swallows → reconciler repairs), that loss is invisible to the daily-loss gate until repair. Relates W3/W11.
- **`retest_state` lacks `UNIQUE(signal_id)`** (`schema.sql:1249-1270`, non-unique index only) — idempotency relies on an app-level INSERT-OR-REPLACE trick; two concurrent diverts could create two rows → zombie on release. **Dormant** (`wait_for_retest_enabled=False`). Add the UNIQUE index.
- **`daily_loss_limit_pct` schema permits up to `1.0`** (`config_loader.py:473-478`), which effectively disables the post-close kill; only a config_auditor WARN (C1, alert-only) catches it. Consider a hard cap (≤0.10) or promote C1 to BLOCK.
- **`special_sessions` has no cross-field validation** (`config_loader.py:1183`, bare dict); a per-date `market_close < market_open` loads silently (unlike `TradingHoursConfig._validate_window_ordering`). Add the ordering check.
- **Two different "daily" bases for realized P&L** — `get_today_closed_pnl` filters `DATE(updated_at)` (`state_store.py:2649-2653`) vs `get_daily_realized_net_pnl` filters `fm_ledger.date`. A re-touched `updated_at` shifts the P&L day in one but not the other. Prefer `exit_time`/`fm_ledger.date` consistently.
- **`orders/sl_breach_monitor.py` is dormant** (not wired in main.py despite its docstring). If ever wired: it fires exits as `leg='EOD'` (invisible to G5b guards), doesn't set `EXITING` / register in `_fill_map`, and sells `qty_filled` with **no oversell guard** (unlike CHECK9 FACET 2). Coordinate with G5b + add a live-held re-check before enabling.
- **Kill-switch engagement depends on DB writability** — KS9 persist-first: if `_persist_state` raises (DB locked/full/corrupt), `soft_kill`/`hard_kill` abort with no state change (`kill_switch.py:456-486,717-736`). Correct fail-closed for consistency, but the primary safety cannot engage during a DB outage. Consider an in-memory degraded-halt latch.
- **`live_feed` watchdog thread is itself unsupervised** (`live_feed.py:611-652`); a dead watchdog silences both stale-feed detection and consumer restart.
- **Startup reconcile runs twice** (`main.py:2701` and inside `order_reconciler.start()` `:299`) — idempotent/harmless, but redundant.
- **`_on_order_filled` get-then-pop asymmetry** (`order_placer.py:1607-1608` vs pop-first siblings) — safe only because dispatch is fully serial today; pop-first is strictly safer if any handler ever becomes `async_dispatch=True`.

---

## 4. ALREADY-TRACKED (mapped to existing W-items — not new)

- **W10** — `get_daily_realized_net_pnl` double-subtracts costs (`state_store.py:2261` `SUM(pnl_delta) - SUM(costs)` while `RELEASE_USED.pnl_delta` is already `gross−costs`). Consumed by both loss gates. Confirmed still present, safe direction (over-negative → trips early). *(Independently re-confirmed by both the B and D audits; NOT the same as B-1.)*
- **W11** — CLOSED_MANUAL trades can carry NULL `net_pnl` (two-step reconciler: `mark_trade_manually_closed` then `record_manual_close_financials`; if step 2 isn't reached, NULL persists). Relates to the cross-store PnL asymmetry above.
- **W2** — `trades.broker_margin_blocked` not persisted (no column; report placeholder "— pending W2").
- **W3** — system↔broker pnl/position reconciliation wiring. **A-1's tag-correlation gap partially overlaps W3 but is a distinct, actionable defect** (timeout-recovery correlation), not just reporting wiring.
- **W8 / W9 / W12 / W13** — closure_source / per-signal drops / slippage-tier persistence / shadow_tracker+innings — no new overlap surfaced.
- **Operator-flagged resilience items — CONFIRMED as described:** (1) a rejected last-line-of-defense emergency exit only SOFT_KILLs (a *placement failure* is retried next ~15s cycle but never escalated to HARD_KILL's indestructible flatten — worth escalation); (2) one naked symbol SOFT_KILLs the whole system (`order_reconciler.py:2062`, coarse but defensible).

---

## 5. CHECKED-AND-OK (verified safe — coverage evidence)

**Order path:** 429 retry is idempotent (limiter raises pre-submission; HTTP-429 = order rejected). Entry dedup/cap races closed (approve+reserve atomic under `portfolio_lock`; `_in_flight_count` pre-increment; `EntryThrottle.admit` atomic check-and-record). Fill handlers idempotent + OCO sibling-cancel; dispatch fully serial (single poll thread, SYNC subscribe). RAMCOIND L1–L4 layers intact. CHECK9 FACET-2 oversell guard present; CHECK9 vs G5b cleanly partitioned by `sl_row is None`. EOD squareoff two-pass cancel→settle→market + broker-authoritative qty filter (no MARKET-reverse naked short). TGT retry guards SL-standing + no-dup-live-TGT. Naked-short fix (exits deferred to fill qty, SL-first, tag through truncation chokepoint) intact.

**Capital:** reserve/release/commit/release_used under a single RLock, write-ahead ledger; two concurrent signals cannot both reserve (re-check under lock); crash between reserve and place doesn't leak (no trade row pre-place; rehydrate replays only OPEN/PARTIAL); reservation released on every failure path; `release()` idempotent; commit fires once per fill (partial returns excess); three-balance invariant checked after every mutation → hard_kill on violation. Position sizing clamps (sl floor/ZeroDiv, qty-explosion cap, concentration 10%, position-value 40%, lot-skew, BELOW_MIN) present; zero/negative qty rejected. Leverage applied exactly once, single shared `leverage_map`; live-margin schism DORMANT. Both loss controls read the same key; post-close breach → square-off + soft_kill. Slippage tolerance `min(SL_dist×0.22, ₹5)` then `min(·,₹10)` — ₹10 ceiling never binds, cannot be exceeded (pre-trade reject); recorder is record-only/async/non-blocking.

**Security:** webhook auth logic sound when a secret is set (constant-time compare both paths; constructor refuses `require_hmac=True` without a secret). Replay/dedup robust (3 layers: atomic `_claim_in_flight`, TTLCache 300s under lock, DB `sha256(scanner|symbol|epoch_bucket)` UNIQUE → IntegrityError→DUPLICATE; expiry precedes dedup). **No SQL injection** in production (all `execute()` parameterized; only identifier interpolation from internal constants/`sqlite_master`). **No command/shell injection** (list-argv subprocess, no `shell=True` outside a test harness). **No unsafe deserialization** (only `yaml.safe_load`; no `eval`/`exec`/`pickle`; `__import__` uses hardcoded stdlib names). Telegram token not logged. `accounts.csv` stores env-var names only. **No env/config safety-bypass switch** for kill-switch/daily-loss/throttle. `.gitignore` excludes `.env`/`*.db`/`data_store/`/`logs/` (gap is only `.env.example`). Token freshness/forgery: account+same-day+expiry checked, wiped 05:00 daily.

**Data/config:** schema version pinning consistent (EXPECTED=41 == schema.sql trailing INSERT == migrations map); migrations v36→v41 pure additions except the guarded v38 signals.status CHECK widening (in MIGRATION_TABLES, 12-step FK-checked rebuild). State-store status transitions atomic (`WHERE … AND status IN (…)` + rowcount; `BEGIN IMMEDIATE`). Capital ledger write-ahead + transactional; `release()` idempotent. Duplicate prevention (signals UNIQUE fingerprint, innings UNIQUE, candles INSERT OR IGNORE, eod_squareoff_log UNIQUE). Trades timestamps consistently tz-aware; reconciler `fromisoformat` wrapped fail-safe; candles lexical bug fixed via `_parse_ist_dt`. Risk-critical config fields required (no defaults) with range validators + `extra="forbid"` + cross-field validators. config_auditor fail-fast at startup + full A–G at pre-flight; config_snapshotter idempotent + secret-safe.

**Resilience:** mid-trade crash (entry filled, SL missing) → G5b recovery SL with settling-window + broker-authoritative + dup guards. EOD idempotent (write-ahead IN_PROGRESS→COMPLETE + broker-authoritative qty; no double-square). Websocket drop → auto-reconnect + re-subscribe + tick-age watchdog + max-reconnect soft_kill (positions stay protected by broker SL). LiveFeed consumer thread immortal + watchdog-restarted. Stuck-EXITING resolved against broker truth. In-flight fill orphan (order row exists) → CHECK2 handles. API timeout (non-auth) → skip+retry / UNKNOWN_IN_FLIGHT (no capital released on unknown). Startup reconcile + stale-order sweep run before the signal pipeline. DB durability WAL + `synchronous=FULL` + `busy_timeout=30000` + `BEGIN IMMEDIATE` + per-thread connections. Clean shutdown reverse-order + WAL checkpoint + token invalidation on auth error. Overnight self-exit only once flat.

---

## 6. COVERAGE NOTE

- **Deepest (as directed):** A (order/execution), B (capital/risk), C (security/exploitation) — full read of the order lifecycle, capital engine, kill-switch, webhook/auth/token/secret surfaces; every Critical/High personally re-verified against source (grep-decisive for B-1; file-level for C-1/C-2/C-3/C-4, A-1/A-2, D-1, F-1, E-2, and the tag-correlation gap).
- **Solid:** D (data integrity), E (resilience) — state-store atomicity, migrations, IST handling, crash/restart recovery, SOFT/HARD-kill auto-clear soundness, session-expiry fail-safe, SPOFs. E's two priority questions answered definitively (auto-clear = sound; session-expiry = fail-safe).
- **Lighter (follow-up candidates):** (1) the AI-ops `scripts/gemini_*.py` + `agy` prompt-injection surface was assessed at a boundary level only (output is observe-only, so bounded) — a deeper look is warranted if any AI-ops output ever becomes actionable. (2) `data/` market-data ingestion internals and `screening/` scorer numerics were touched only where they intersect A/B; a dedicated correctness pass on scoring math was out of scope. (3) The `ops/control_tower/` and `scripts/` cron fleet were covered for security/subprocess but not for individual job-logic correctness. (4) Broker adapter paper/live parity was spot-checked (not exhaustively diffed) — the parity rule holds on the paths audited.
- **Convergence signal:** two independent agents (A and E) found the **same root weakness** (recovery keys on a `broker_order_id`/order-row that doesn't exist for a timed-out or crashed entry; tag never used for correlation; orphan-adoption then disowns as "human") from two entry points — this is the highest-value *code* fix to prioritize after the C-1 credential rotation.

---

## 7. RECOMMENDED FIX ORDER (for the later act-phase — not done here)

1. **C-1 (now, ops):** rotate all exposed credentials + purge git history. Nothing else in this report matters if the keys are public.
2. **C-2:** lock the webhook bind/HMAC.
3. **A-1 + E-1 (shared fix):** tag/symbol correlation in timeout+crash recovery; don't disown a same-day system order as "human"; don't mark FAILED before broker-confirmed absence.
4. **A-2:** stop retrying `BrokerTimeoutError` (idempotency, not throttle-reliance).
5. **B-1:** wire the unrealized-MTM term (or a periodic unrealized-drawdown soft_kill).
6. **D-1:** conditional `close_trade` UPDATE + rowcount.
7. **C-3 / C-4 / F-1 / E-4 / A-3 / E-2:** lower-severity hardening as capacity allows.

*Every fix above must be implemented PERMANENT with paper+live parity and its own fail-on-old / pass-on-fix tests, per the standing rules. This document is investigation-only; no code was changed.*
