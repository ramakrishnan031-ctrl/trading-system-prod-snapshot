# PATHS — Quick Reference (Trading System v2)
# Full map + audit: docs/SYSTEM_MAP.md  ·  Last updated: 2026-06-29

> ⚠️ Read `docs/SYSTEM_MAP.md` before any VM/system work. **Deploy ≠ restart.**

> 📌 **Deferred board (30-Jun) — only TWO open** (else closed/deployed/dormant): **(1) S&R V1 calibration** — DEFERRED/collecting; reopen DATA-gated (~50 fills + ~8–10 BIR-filled W/L, checkpoint ≈14-Jul) → PASS→Phase A / RECALIBRATE→zone params. **(2) Delivery Slice 2.5 T2** — **DEFERRED (02-Jul attempt; live NEVER run — proof script bit-rotted)**. Pre-checks PASSED (deploy both fixes live; delivery-lock gate GREEN). Drift in `scripts/t2_cnc_gtt_realtest.py`: L71 `core.`→`broker.order_state_machine`, L83 `RateLimiter(cfg.broker_limits)`, L319 `store=None`→wire (else no `gtt_state` row). Reopen after an OFF-MARKET whole-script repair + full static audit vs main.py → clean dry-run → commit → push off-market → run COMPLETE T2. Parked `fix-t2-import-02jul`@b826ae0 (L71 only, unpushed). Detail: SYSTEM_MAP "Deferred items board" + memory `sr_v1_calibration_deferred_30jun` / `delivery_slice25_status_30jun`.

> ⏰ **Timezone (T4, 29-Jun) — NEVER `TZ='Asia/Kolkata' date` in Git Bash.** MSYS2 ships no
> zoneinfo, so that form silently returns **UTC** (off by 5:30 — the 29-Jun "13:47 vs 19:22"
> defect). For IST use `scripts/ist_now.sh` (authoritative, reuses `now_ist()`), or `now_ist()`,
> or `date -u` (UTC), or `TZ='IST-5:30' date`. **Before any deploy/restart:** run
> `python scripts/deploy_preflight.py` (anchors on `date -u` + the VM: PC↔VM UTC agreement ≤120s
> **+ VM-authoritative market-open gate** — refuses a mid-session deploy). `scripts/check_tz.sh`
> is the loud self-check (correctly FAILS on this MSYS2 PC). App + VM are unaffected (`now_ist()`
> = fixed +5:30, zoneinfo-free; VM NTP-synced) — this is an operator-tooling guard only.

## VM (161.118.187.249, user `ubuntu`, IST)
| What | Path |
|---|---|
| Project root (running tree) | `/home/ubuntu/systems/trading-system/` |
| Python venv (shared) | `/home/ubuntu/systems/venv/bin/python` (3.12.3) |
| Main DB (v41) | `data_store/trading_system.db` (v41 = +`config_snapshots`, W0 report-redesign foundation) |
| Analytics DB (ATTACHed) | `data_store/analytics.db` (holds `system_metrics`/`_daily`; T1 29-Jun: `disk_used_pct` now shutil-based — was −1.0 (psutil absent); cpu/mem still −1.0) |
| Broker token | `data_store/session/zerodha_token.json` |
| Secrets | `.env` (root) + systemd drop-in (NOT in git) |
| Logs | `logs/system_YYYY-MM-DD.log`, `reconciler_*.log`, `trades_*.log`, `cron-*.log` |
| Master config | `config/system_config.yaml` (T5 29-Jun: `trading_hours.entry_end` 15:15→**15:00** = the per-strategy reality; `eod_entry_cutoff` stays 15:15. **C-2 02-Jul:** `webhook.per_ip_{rate_limit_enabled,burst,refill_per_sec}` added [token-bucket, 429]; `WEBHOOK_SECRET` now required in BOTH modes [`main.required_startup_secrets`]; bind still `0.0.0.0`/`require_hmac:false` pending the Phase-3 network decision — memory `c2_webhook_lockdown_02jul`) |
| Cron source of truth | `config/cron_registry.yaml` (→ `core/cron_registry.py`; `officer:` block = Cron Officer settings) |
| Accounts | `config/accounts.csv` (primary: LFL836) |
| Reports | `reports/{daily,daily_review,flow_trace,system_manager,cron_officer,...}/` |
| Cron-job markers | `data_store/cron_marks/<job>.done` (exit-code markers the Officer reads) |
| Alert sentinels + retention | `data_store/critical_alert_*.flag`→`.delivered` (alert-watcher). `sentinel_retention` cron (02:05 daily) deletes `.delivered` >7d, **NEVER** `.flag`; no archive (email is the record). Tests must NOT use `sentinel_dir="data_store"` |
| DB backups + retention | `data_store/backups/` — daily `trading_system-*`/`analytics-*.db` (cron 01:00/01:05) + `pre_*` deploy/ad-hoc backups. **T2 retention** = `backup_retention.py --apply` (cron 02:00, monitored): category-aware keep-N (pre_*=20 / daily=14), dry-run default, never-delete-newest, scoped globs, >10-delete sanity cap. Replaced the old daily-only `find -mtime +7` |
| Cron audit | `data_store/cron_audit/` (Phase-1 findings + daily `job_list_<date>.json` snapshots) |
| Bare repo (deploy target) | `/home/ubuntu/trading-system.git/` (post-receive checks out tree) |
| Canonical cron | `deploy/cron/trading-system.cron` = `generate(cron_registry.yaml)` via `scripts/generate_crontab.py` (ASCII+LF). live==canonical==generate; **44 command-lines** (30-Jun: +`sr_detector_backfill` 15:58; 29-Jun: +`reconstruct_excursions` 15:50 +`control_tower` 17:05, superseding the standalone size-logger); post-receive auto-installs on push (regenerate the sha after a registry change) |
| Agent CLIs (outside project) | `~/tools/antigravity/agy` (Antigravity/`agy` — drives `gemini_*.py` AI-ops crons) · `~/tools/gemini/` (Gemini CLI, node) · `~/tools/claude/` (Claude Code; 4×/day heartbeat → `cron.log`). ✅ heartbeat now `cd`s into `~/tools/claude/` → governed by `AGENTS.md` + `.claude/settings.json` (no .py/DB/systemctl; verified 23-Jun). Live-crontab only (not in canonical cron) — see SYSTEM_MAP |

## Run a command on the VM
```bash
cd /home/ubuntu/systems/trading-system && . .env && \
  PYTHONPATH=. /home/ubuntu/systems/venv/bin/python <script.py>
```
Raw DB reads: use `core.db_connect.connect` (sets up the v28 ATTACH).

## Services (`systemctl`)
`trading-system.service` (main, `main.py --mode live`) · `token-watcher.service` ·
`alert-watcher.service` · `trading-watchman.service` · `security-watcher.service` ·
`cron-watchdog.timer` (19:30 daily watch-the-watcher, ARMED 23-Jun)

## PC (developer machine)
| What | Path / value |
|---|---|
| Local repo | `D:\Projects\trading-system` |
| Deploy | `git push origin main` → bare repo hook → VM working tree (**no scp; no auto-restart**) |
| SSH alias | `trading-vm` (key `trading_vm_secure`, passwordless) |

## Activate deployed code on VM (manual)
```bash
ssh trading-vm 'sudo systemctl restart trading-system.service'
```

## Top-level packages
`core/` infra · `broker/` integration+polling · `capital/` risk/kill-switch ·
`orders/` order lifecycle · `signals/` ingestion · `screening/` scoring ·
`data/` market data · `alerts/` telegram · `scripts/` ops+gemini ·
`ops/control_tower/` VM ops aggregator (Phase 1) · `tests/` (305)

## Strategy control — 3-layer (Slice 2, 24-Jun)
| What | Location |
|---|---|
| Resolver (single source of truth) | `strategies/control.py::strategy_will_trade(strategy, *, trade_type, force_intraday_only)` → `Verdict(will_trade, reason, product)` |
| LAYER 1 master gate | `system_config.yaml` `trade_type: INTRADAY|DELIVERY|BOTH` (default INTRADAY) → `core/config_loader.py` SystemConfig + validator |
| LAYER 3 per-strategy switch | `config/strategies/*.yaml` `enabled: true` (all 15) → `strategies/schema.py` `enabled: bool = True` |
| LAYER 0 breaker (unchanged) | `force_intraday_only` (load-time intent→INTRADAY rewrite in `strategies/loader.py`) |
| Entry gate (2 sites) | `signals/signal_processor.py` `_process_one` + `continue_from_gate` → `_PipelineReject("STRATEGY_CONTROL")`; placement path UNCHANGED (gate only rejects) |
| Status table | `scripts/strategy_status.py` (build + html/telegram/plain) → folded into Cron Officer 09:20 briefing (`CronReport.strategy_status`); Type=true intent, Verdict=gate |
| Default state | trade_type INTRADAY + force_intraday_only true + all 15 enabled → 15 WILL TRADE / 0 WON'T (zero behaviour change). Delivery (CNC) parked → Slice 2.5 |

## Order-exit safety — circuit-band placeability gate (NOCIL fix, 23-Jun)
| What | Location |
|---|---|
| Placeability primitive + result | `orders/price_math.py` → `clamp_exit_into_band()` / `ClampResult` (margin `DEFAULT_CIRCUIT_MARGIN_PCT`) |
| SL-unplaceable exception | `core/exceptions.py` → `SLUnplaceableError` (→ emergency-close + hard_kill) |
| The **only** 3 clamp call sites | `orders/order_protocol_limit.py` — `place_exits` (SL+TGT), `place_tgt_only` (TGT) |
| Pre-fill reject + fast-disable flag | `screening/secondary_screener.py` · config `entry_gate.circuit_proximity_reject_enabled` (default true) |
| Never-clamp exceptions (test-enforced) | `orders/order_protocol_co.py` (CO-TGT), `orders/order_reconciler.py` (G5b SL) |
| Tests | `tests/unit/test_nocil_clamp_fix.py` (E.1–E.9), `tests/unit/test_price_math.py` |

Detail: `docs/SYSTEM_MAP.md` → "Circuit-band placeability gate".

**Tick-size snap (single chokepoint, fail-safe 23-Jun):** every broker submission is tick-snapped at `broker/zerodha_adapter.py` → `_snap_order_to_tick` / `_resolve_tick` (fallback `DEFAULT_TICK` 0.05 + throttled WARN on a missing `tick_size`; covers `place_order` **and** `modify_order`, parity). `orders/order_placer._round_to_tick` was **removed** — the adapter is the sole snap point. Tests: `tests/unit/test_tick_failsafe_snap.py`.

**Order-tag length guard (single chokepoint, 01-Jul — branch `fix-emergency-exit-tag-01jul`, NOT pushed):** `broker/zerodha_adapter.py::place_order` truncates the tag (`if tag: tag = truncate_tag_for_broker(tag)`, ≤16 chars) right before `kite.place_order`, so **NO caller can submit an over-length tag** (Zerodha rejects >20 → "Invalid tags: max allowed tag length is 20"). Root-caused from the naked-position CHECK9 EMERGENCY exit (`order_reconciler.py:2133` passed the full 36-char `trade_id` → the last line of defense was rejected every time; 1-Jul BANSALWIRE). Call-site also fixed at :2133. The 4 `order_placer.py` `tag=trade_id` sites are safe (truncated downstream by the order protocols). Matching is by `order_id` not tag. Tests: `tests/unit/test_emergency_exit_tag_fix.py` (fail-on-old / pass-on-fix).

## MIS Learned Blocklist (source-free, 30-Jun) — NO_FILL Mechanism B
| What | Location |
|---|---|
| Store (JSON, no DB) | `data_store/mis_blocklist.json` `{SYMBOL: last_blocked_date}` (git-ignored runtime; atomic write) |
| Module | `core/mis_blocklist.py` — `MisLearnedBlocklist` (record/is_blocked + TTL) + `is_mis_block_rejection(exc)` |
| Record (always-on) | `orders/order_placer.py::_handle_placement_failure` (ONE guarded `record_block` on MIS-block 400; existing steps unchanged) |
| Drop (flag-gated) | `screening/secondary_screener.py::screen()` → `REJECTED_NOT_MIS_TRADABLE` (only when product==MIS) |
| Flag | `config/system_config.yaml` `mis_filter` (enabled:false/shadow:true/ttl_days:5) → `core/config_loader.py` MisFilterConfig |
| Tests | `tests/unit/test_mis_blocklist.py` (22). STAGED branch `mis-tradability-filter-30jun`, NOT pushed. NO schema/cron/external-source |

## Duplicate-exit safety — RAMCOIND fix (25-Jun): G5b can no longer over-sell
| What | Location |
|---|---|
| Root | G5b crash-recovery placed a 2nd SL outside the OCO via a TOCTOU race on the lagging local `orders` table → both filled on the stop-hit → −1 over-sell (RAMCOIND 25-Jun; also IRFC 17-Jun / NIACL 22-Jun) |
| **L2 keystone** invariant | `orders/order_reconciler.py` `_check_duplicate_exits` / `_dedupe_exit_leg` — exactly one live SL (LIMIT_TRIPLE only; CO excluded via `variety='co'`) + one live TGT per open trade; cancels the non-canonical (later-placed) extra, never drops to zero |
| **L1** G5b guard | `_g5b_crash_recovery_sl` → 10s settling window (`_entry_fill_age_seconds`) + authoritative broker/`_fill_map` check (`_already_has_live_sl`, `_G5B_SETTLING_WINDOW_SEC`) instead of the lagging table |
| **L3** over-sell / naked | `_check2_orphan_adoption` → `_detect_system_oversell` (CRITICAL + auto-flatten) · `_position_is_naked` (naked → WARNING, never silent; protected human → silent, FIX-182) |
| **L4** after-check | `orders/order_placer.py` `_verify_exits_placed` → flags `>1` live SL/TGT (`DUPLICATE_SL/TGT`) |
| Tests | `tests/unit/test_ramcoind_dup_exit_fix.py` · acceptance gate `tests/crash_test/test_ramcoind_oversell_prevented.py` (paper+live: position ends FLAT, never −1) |

No DB schema; parity (shared paper+live, no mode branch). Detail: `docs/SYSTEM_MAP.md` Changelog 2026-06-25 · memory `ramcoind_duplicate_sl_incident_25jun`.

## Naked-orphan ROOT-CAUSE fix — A-1 (timeout) + E-1 (crash), 02-Jul (branch `fix-a1e1-orphan-recovery-02jul`, UNPUSHED)
| What | Location |
|---|---|
| Root | An ENTRY that reached the broker but whose local `orders` row never persisted (A-1 place-timeout / E-1 crash-mid-place) was marked FAILED + capital released WITHOUT confirming broker absence → filled → disowned as human → **naked position** till 15:17 EOD. Enabler: the broker `tag` was written but never read |
| Broker oracle | `broker/zerodha_adapter.py` `get_all_orders()` — ALL of today's orders (any status) WITH `tag`+`product`; live `kite.orders()`, paper `_paper_fills` (tag+product retained across the synth-fill merge) |
| Correlation (PURE) | `orders/order_reconciler.py` `correlate_entry_by_tag()` — recompute `truncate_tag_for_broker(trade_id)`, side-filter to the ENTRY leg → MATCH / ABSENT / AMBIGUOUS (48-bit collision narrowed by symbol+qty) |
| Unified recovery PREPASS | `_recover_in_flight_entries()` → `_adopt_or_fail()` — runs at the TOP of every `_reconcile()` (startup sync `reconcile_once()` + 15-min poll), BEFORE CHECK1/G5b, so an adopted-OPEN trade is SL'd (G5b) + TGT-retried the SAME cycle. Both feeds (timeout `UNKNOWN_IN_FLIGHT` queue + crash orphaned-`PENDING`) → ONE path |
| Decisions | terminal+filled→ADOPT (protect; partial-then-CANCEL included) · terminal+zero-fill→FAILED+release (only evidence-based release) · non-terminal→DEFER (reserve kept) · ABSENT→FAILED after 3-poll budget · broker-unreachable→DEFER set · AMBIGUOUS→CRITICAL alert+defer · HARD_KILL+fill→FLATTEN (commit→EXITING→oversell-guarded emergency close) |
| Crash-cleanup reroute | `broker/order_monitor.py` `_cleanup_orphaned_pending_trades` — now DETECT-ONLY (WARNING; NO blind FAILED / NO orphan-release), defers to the reconciler recovery |
| Capital (crash-aware, exactly-once) | `capital/fund_manager.py` `commit_adopted_entry` / `restore_adopted_reservation` / `release_adopted_reservation` (+ `_restore_reserve_from_ledger`/`_resolve_reservation_id`/`_commit_exists`) — reconstruct a crash-lost reservation from the durable `fm_ledger` RESERVE row, then commit/release; three-balance invariant holds |
| State transitions | `core/state_store.py` `adopt_recovery_trade_to_open` (extended: atomic status flip + fill-field backfill) · `mark_recovery_trade_exiting` (HARD_KILL) · `fail_recovery_trade` |
| Preserved | RAMCOIND L1-L4 · CHECK9 (FACET-1 race-aware + FACET-2 oversell) · G5b (entry_time backfilled to `created_at` so the 10s settling window doesn't defer the recovery SL). Old `_check_unknown_in_flight` (blind FAILED) REMOVED. NO schema |
| Tests | `tests/unit/test_a1e1_recovery_matrix.py` (7 capital-proof cases + 13-row matrix incl. same-cycle-G5b integration + paper-parity) · `tests/unit/test_a1e1_orphan_recovery.py` (units) · `tests/unit/test_fix068_timeout_recovery.py` (order-placer side kept) |

Parity (one recovery path, both modes; live Kite / paper `_paper_fills` oracle). Detail: `docs/design/a1_e1_orphan_fix_design_02jul2026.md` · `docs/SYSTEM_MAP.md` Changelog 2026-07-02 · memory `a1_e1_orphan_fix_impl_02jul`.

## Delivery (CNC) — SLICE2.5-P1+P2 (25-Jun): durable GTT overnight protection (delivery_enabled=false)
| What | Location |
|---|---|
| Master lock (R2 split) | `system_config.yaml` `delivery_enabled` (default **false**). **P2 guard split:** gates ONLY the CNC **entry** (`zerodha_adapter.place_order`); protective GTT ops (`place_gtt`/`modify_gtt`/`delete_gtt`/`get_gtt(s)`/`get_holdings`) are NOT gated — protection survives disablement. Real CNC entry needs delivery_enabled=true AND force_intraday_only=false AND trade_type∈{DELIVERY,BOTH} |
| GTT placer | `orders/cnc_gtt.py` `CncGttPlacer` — OCO legs (SL limit = trigger − `capital.gtt_sl_limit_offset_pct` 3%; TGT limit = trigger − `sl_limit_offset_pct`), C8 validate, ONE GTT/trade. **P2:** persists to `gtt_state` (durable), `hydrate_from_store()` rebuilds the hot cache on boot, `forget()` for recreate |
| Durable state (v36) | `core/schema.sql` **TABLE 37 `gtt_state`** (gtt_id INTEGER PK, status ACTIVE→TRIGGERED→CLEANED/…, needs_review, FK→trades) = the one-per-trade source of truth (replaces P1's in-memory map). `core/state_store.py` DAO (insert/update/get_active/by_id/status/needs_review/verified + `mark_trade_closed_gtt`/`record_gtt_close_financials`) |
| Reconcile + GTT_EXIT | `orders/cnc_gtt_monitor.py` `CncGttMonitor.reconcile()` — re-verifies each GTT vs broker (get_gtts + holdings + positions), K6 ladder (healthy / **GTT_EXIT** finalise+capital-release / F6 re-protect / recreate / qty-mismatch CRITICAL-once+needs_review / orphan delete / >1-ACTIVE soft-kill) + 50-cap. Wired into `order_reconciler` (startup [4a] + 15-min in-hours [4b]); delivery trades EXCLUDED from the position/SL/exit checks |
| **Orphan-GTT ADOPTION (FIX-183, 26-Jun)** | `cnc_gtt_monitor.adopt_orphan_gtts()` — reconstructs a LIVE broker GTT with NO `gtt_state` row (SL/TGT split by trigger **magnitude**) + correlates to the open delivery trade (product-aware `get_all_open_trades` by symbol; 0/>1/already-ACTIVE → WARN-once, never adopt; never deletes a live GTT). Wired as a NARROW prepass at the **top of `order_reconciler._reconcile()`** (`_run_gtt_adoption_prepass`) so it runs BEFORE CHECK1 in BOTH startup+15-min — closes C2.1 (a carried row-less CNC GTT was mis-marked CLOSED_MANUAL because its holding is in holdings() not positions()). Alerts `source_module="cnc_gtt_adoption"` |
| Adapter GTT/holdings I/O | `broker/zerodha_adapter.py` `place_gtt`/`modify_gtt`/`get_gtt`/`get_gtts`/`delete_gtt`/`get_holdings` — live=Kite, paper=in-memory store (`_paper_gtts`/`_paper_holdings`, `seed_paper_holding`). Paper gtt ids are **NUMERIC** (`_PAPER_GTT_ID_BASE`) since gtt_state.gtt_id is INTEGER PK |
| Limit math | `orders/price_math.py` `calc_gtt_limit_price` (dedicated; not the intraday 0.5% path) |
| Centralized gate | `orders/full_entry_engine.py` `place_deferred_exits` (intent==DELIVERY → GTT, `ExitLegsResult.is_gtt`); `order_placer._finalize_cnc_gtt` (logs gtt_id; no day legs). INTRADAY unchanged |
| Tests / T2 | `tests/unit/test_cnc_gtt_slice25_p1.py` · `test_cnc_gtt_slice25_p2.py` · `test_cnc_gtt_monitor.py` · `test_cnc_gtt_step4_wiring.py` · `test_cnc_gtt_adoption_fix183.py` (FIX-183: incl. the C2.1 + MIS-regression tests) · `scripts/t2_cnc_gtt_realtest.py` (market-hours real-API/TPIN proof — the blocker before enabling delivery) |

P2 = durability + safety (schema v36 `gtt_state`, reconcile, GTT_EXIT, 15-min monitor); delivery_enabled stays **false** (no activation). **DEPLOYED to main `bad0aad` 25-Jun ~22:23 (one-time authorized; rule restored); schema v36 applies at the Fri 08:15 boot.** **FIX-183 (26-Jun) adds orphan-GTT adoption** (no schema) — **DEPLOYED to main `af4b784` 26-Jun ~11:11 (one-time authorized; rule restored); 11:11 restart self-exited 0 at the HOLIDAY guard (Muharram), real boot Mon 29-Jun 08:15; verified broker-session-free 5/5 (schema=36, Auditor 0 BLOCK, delivery_enabled=false).** Detail: SYSTEM_MAP Changelog 2026-06-25/26 · memory `slice25_p2_gtt_durability_25jun` · `fix_183_gtt_adoption_26jun`.

## Delivery (CNC) — SLICE2.5-PHASE-3 (26-Jun): delivery count caps + conditional capital (INERT)
| What | Location |
|---|---|
| (A) Delivery count caps | `system_config.yaml` `risk.max_open_delivery_positions`(3)/`max_daily_delivery_trades`(5) → `core/config_loader.py` RiskConfig. Product-keyed counts `core/state_store.py` `count_open_delivery_positions`/`count_daily_delivery_trades` (JOIN orders `leg='ENTRY' AND product='CNC'`, DISTINCT trade_id). Enforced `capital/risk_engine.py::_run_checks` — OPEN_POSITIONS/DAILY_TRADES **branch on `sizing_result.bucket=="positional"`** (else = global check, byte-unchanged; counts read lazily in `approve()`). **A5**: intraday branch unchanged → asymmetric coupling (delivery counts toward intraday cap, not vice-versa) — intentional, keeps live intraday cap byte-identical |
| (B) Conditional allocation | `system_config.yaml` `capital.conditional_allocation_enabled`(**false**) → `core/config_loader.py` CapitalConfig. Pure `capital/fund_manager.resolve_bucket_allocation()` → effective `(intraday_pct, positional_pct)` (off=fixed split; on: only-intraday 100/0, only-delivery 0/100, both=split, neither=100/0). `main.py` computes `delivery_active`/`intraday_active` + passes effective pcts to FundManager ctor + logs `capital.bucket_allocation`. **FundManager UNCHANGED**; no-borrow already in `reserve()` (consults only the intent's bucket) |
| SHARED (untouched) | `max_concentration_pct` (position_sizer, total-relative), SECTOR_EXPOSURE, `max_position_value_pct`, the whole intraday cap path |
| Tests | `tests/unit/test_phase3_delivery_caps_conditional_capital.py` (17: allocation cases + dormancy + delivery caps + ★inertness + ★MIS-regression + no-borrow) |

INERT by construction: flag default false → 70/30 unchanged; force_intraday_only=true coerces every strategy to intent=INTRADAY → bucket=intraday → delivery cap branch never hit + delivery counts 0. No schema; parity. 3854 unit tests / 0 regressions. **DEPLOYED to main `37b3db3` 26-Jun ~13:16 (one-time authorized batch w/ FIX-183-log + Phase 4; rule restored; Muharram holiday self-exit, real boot Mon 08:15; verified DORMANT — fixed 70/30, caps inert).** Detail: SYSTEM_MAP Changelog 2026-06-26 · memory `slice25_phase3_delivery_caps_conditional_capital_26jun`.

## Delivery (CNC) — SLICE2.5-PHASE-4 (26-Jun): trade_type reject-by-intent gate (CONFIRM + label split)
| What | Location |
|---|---|
| The gate (ALREADY existed, Slice 2) | `strategies/control.py` `strategy_will_trade` LAYER 1×2 — `trade_type` INTRADAY→reject DELIVERY-intent / DELIVERY→reject INTRADAY-intent / BOTH→accept; keyed on EFFECTIVE post-coercion intent. Wired into `signals/signal_processor.py` `_process_one` (~:625) + `continue_from_gate` (~:1330), BEFORE sizing. Dormant under current config |
| PHASE-4 label split (the one live touch) | `Verdict.cause` field on `control.py` (`CAUSE_OK`/`CAUSE_DISABLED`/`CAUSE_FORCE_BREAKER`/`CAUSE_TRADE_TYPE`). Gate maps `cause==CAUSE_TRADE_TYPE` → `_PipelineReject("TRADE_TYPE")` → `REJECTED_TRADE_TYPE` + own `_stats["rejected"]["TRADE_TYPE"]` tally; all other control rejects stay `STRATEGY_CONTROL`. Keyed on cause, NOT the message string; reason strings + accept/reject logic UNCHANGED |
| Tests | `tests/unit/test_phase4_trade_type_gate.py` (9: cause-per-layer, reason-unchanged guard, go-live matrix, ★dormancy, ★label split, ★label-bleed, contradictory-combo Auditor BLOCK) |
| Option B (declared-intent gating) | **PARKED** — would stop the 3 live `positional_*` declared-DELIVERY strategies (trade as coerced-intraday today); a strategy/perf decision, `strategy.enabled` is the cleaner mechanism. Declared intent recoverable via `scripts/strategy_status.py:74` (raw `validate_strategy`), not on the loaded object |

Dormant (INTRADAY+force=true → coerced → accept → split never fires). No schema; parity. 3863 unit tests / 0 regressions. **DEPLOYED to main `37b3db3` 26-Jun ~13:16 (one-time authorized batch w/ FIX-183-log + Phase 3; rule restored; Muharram holiday self-exit, real boot Mon 08:15; verified DORMANT — gate no-op, Auditor 0 BLOCK).** **Completes the Slice 2.5 build side**; next = T2 + C1-watch. Detail: SYSTEM_MAP Changelog 2026-06-26 · memory `slice25_phase4_trade_type_gate_26jun`.

## S&R V2 Phase A (SNR-V2, 27-Jun) — WAIT_FOR_RETEST entry side, SYMMETRIC LONG+SHORT (default-OFF, schema v38)
| What | Location |
|---|---|
| Master flag (default OFF) | `system_config.yaml` `sr_detector.wait_for_retest_enabled: false` (extends the V1 block) → `SRDetectorConfig`. ONE flag covers BOTH sides. Off ⇒ ZoneWarmer/RetestMonitor/Diverter NOT constructed ⇒ divert + enqueue are no-ops ⇒ byte-identical |
| Divert (pre-placement) | `signals/signal_processor.py` `_process_one` AFTER side, BEFORE sizing/reserve(`:818`) → `RetestDiverter.maybe_divert` (`screening/retest_monitor.py`). Reads `ZoneCache` SYNC — LONG matches a HIGH **resistance** zone, SHORT a HIGH **support** zone (miss/unknown-side/no-HIGH → fall through); parks into RetestMonitor; `signals.status=RETEST_WAITING`; audit row to `sr_detector_results` (BUYING_INTO_RESISTANCE / SELLING_INTO_SUPPORT). NO capital held |
| Confirm state machine | `sr_detector/retest_confirm.py` `evaluate(direction=…)` — ONE pure direction-parameterised FOLD. LONG: WAIT_BREAKOUT→WAIT_RETEST(close>band_high)→WAIT_CONFIRM(re-enter band)→CONFIRMED(close>band_high + strong UPPER close); REJECT timeout / close<band_low·(1−max_away%) [break_down]. SHORT mirror: break=close<band_low, confirm strong LOWER close, REJECT on close>band_high·(1+max_away%) [reclaim_up] |
| Monitor (restart-safe) | `screening/retest_monitor.py` `RetestMonitor` daemon — polls fresh 1m (OhlcFetcher lookback=`onem_lookback_days`, no cache), passes `parked.direction` into evaluate; CONFIRMED→`continue_from_retest`, REJECT→release; rehydrates from `retest_state`; `has_symbol` dedup |
| Resume (MARKET entry) | `signals/signal_processor.py` `continue_from_retest` (sibling of continue_from_gate) — re-check KS/window/control/governor (NOT 60s expiry); size+reserve (capital HERE); place MARKET (side from `parked.direction`), structure SL = band_low·(1−`sl_buffer_pct`%) LONG / band_high·(1+`sl_buffer_pct`%) SHORT, R:R-preserving TGT (direction-aware `_derive_target`) |
| MARKET route | `entry_order_type` (default LIMIT) threaded `order_placer.place → full_entry_engine.execute → {LimitTriple,CoPlusTgt}.execute → adapter.place_order` (MARKET→price=0). Adapter already supports MARKET; paper `_synth_fill` fills at LTP (parity) |
| Zone cache + warmer | `sr_detector/zone_cache.py` ZoneCache (resistance+support, TTL) · `sr_detector/zone_warmer.py` ZoneWarmer (daemon; enqueued at `signal_processor._dispatcher_loop._warm_zones`) · `sr_detector/zone_builder.py` (DRY zone-build shared with V1 detector) |
| Schema (v37→v38) | `core/schema.sql` TABLE 39 `retest_state` (pure add, FK→signals) + signals.status CHECK `OR GLOB 'RETEST_*'`; `MIGRATION_TABLES[38]=["signals"]` (FK-safe rebuild). DAO `core/state_store.py` insert/update/release/clear_all/get_all_retest_state. EOD clear via `eod_squareoff.set_retest_monitor` |
| Tests | `tests/unit/test_sr_v2_{retest_confirm,zone_cache,divert,monitor,market_path,continue}.py` (55: every LONG branch unchanged + full SHORT mirror) |
| Side-neutral knobs | one set serves both sides: `near_zone_buffer_pct` (was near_resistance_buffer_pct), `confirm_strong_close_frac` (was reclaim_strong_close_frac, + RetestParams field), `require_confidence`, `retest_max_away_pct`, `breakout_margin_pct`, `sl_buffer_pct` |

SHADOW→ACTIVE only when the flag is on; SYMMETRIC LONG+SHORT (ONE flag, ONE set of side-neutral knobs; NO schema change — direction already persisted). branch `snr-v2-phaseA-27jun` (on V1), NOT pushed; schema awaiting nod. Detail: `docs/SYSTEM_MAP.md` header · memory `snr_v2_phaseA_27jun`.

## S&R V2 Phase B (SNR-V2, 27-Jun) — Structure-Aware Exit, MIS LIMIT_TRIPLE only (default-OFF, NO schema)
| What | Location |
|---|---|
| Master flag (default OFF) | `system_config.yaml` `structure_exit.structure_exit_enabled: false` → `core/config_loader.py` `StructureExitConfig` (`SystemConfig.structure_exit`). Off ⇒ `StructureExitManager` NOT constructed, nothing subscribes ⇒ byte-identical. Knobs: `sl_buffer_pct`(0.2), `break_buffer_pct`(0.0), `require_strong_close`(true), `break_strong_close_frac`(0.6), `min_zone_confidence`(HIGH) |
| Controller | `orders/structure_exit_manager.py` `StructureExitManager` — RetestMonitor-shape lifecycle (start/stop + RLock); `start()` subscribes `on_1m_close` to `CandleStore.register_on_candle_close` (push 1m feed, multi-subscriber, paper-parity). Stopped in `main.py::_shutdown` |
| Feed + trade scope | per 1m close: `state_store.get_open_intraday_positions()` filtered to `order_protocol=='LIMIT_TRIPLE'` (MIS/CO-scoped query + LIMIT_TRIPLE ⇒ MIS LIMIT_TRIPLE; CO=CO_PLUS_TGT excluded; CNC never in the query). `ZoneCache.get(symbol)` SYNC — LONG→`.support`, SHORT→`.resistance`, HIGH-confidence only; miss/no-zone → leave SL |
| Zone selection | nearest HIGH zone the candle traded into, keyed on the candle EXTREME (`high` LONG / `low` SHORT) not the close — so the breaking candle still selects the zone it broke (close<band_low would exclude it). LONG=highest support with `band_low<high`; SHORT=lowest resistance with `band_high>low`. Already-broken-and-left-behind zone excluded |
| ACTION B (break, first) | a 1m CLOSE beyond the band (`±break_buffer_pct`) + a STRONG close (`sr_detector/retest_confirm._strong_close`: LONG support-break ⇒ `is_long=False` strong-LOWER; SHORT ⇒ `is_long=True` strong-UPPER — opposite polarity to the entry confirm). Exit in the MANDATORY order: (1) `trades.status='EXITING'` (mirror `kill_switch.py:956`), (2) cancel resting SL+TGT (`leg IN ('SL','TGT')`, mirror `kill_switch._cancel_trade_resting_exits`), (3) reverse-aware flatten — marketable LIMIT (`marketable_limit_price`, LTP±`EMERGENCY_EXIT_BUFFER_PCT`)/MARKET fallback, broker-flat ⇒ skip (no double-exit). EXITING-first ⇒ reconciler CHECK1/G5b never re-arm (`order_reconciler.py:781,1106`) |
| ACTION A (trail, else) | new SL = `support.band_low×(1−sl_buffer_pct/100)` LONG / `resistance.band_high×(1+…)` SHORT, `round_to_tick` nearest. TWO real **pre-modify** guards (root-cause, NOT broker-rejection): ONLY-TIGHTEN (LONG new>cur / SHORT new<cur) + WRONG-SIDE vs LTP (LONG ltp>new / SHORT ltp<new — mirrors `smart_tgt_manager.py:861`). LIMIT moves with the trigger (`calc_sl_limit_price`). `adapter.modify_order(order_id, price, trigger_price, symbol)` (`order_id`=broker PK). Debounce: ≤1 move per (trade, `candle.ts`) — armed only on modify success |
| Single SL owner | the structure-exit controller is the ONLY SL-leg writer — do NOT co-enable any `strategy.trailing_sl_enabled` (BreakevenManager stays dormant/unwired). No DB schema (stateless: EXITING covers in-flight exits; debounce map in-memory, re-derives on boot) |
| Wiring (shared infra) | `main.py`: `_struct_exit_on = system.structure_exit.structure_exit_enabled`; the ZoneCache/ZoneWarmer block builds when `_v2_on OR _struct_exit_on`; `StructureExitManager` built + `start()`ed (warmer `start()` idempotent) after the RetestMonitor block; `_shutdown(structure_exit_manager=…)` unsubscribes |
| Tests | `tests/unit/test_structure_exit_manager.py` (23: trail only-tighten/wrong-side/snap/limit-moves/static/debounce/modify-fail; break LONG+SHORT in-order, weak/wick no-exit, strong-off; gap backstop ×2; zone HIGH-only/nearest/already-broken; CO excluded; **real paper==live** parity for modify/cancel/flatten + mode-agnostic; **dormancy**) |

NO schema change; default-OFF dormant; paper==live (real-adapter parity test). branch `snr-v2-phaseB-27jun` (on Phase A), NOT pushed; PAUSED for review. Detail: `docs/SYSTEM_MAP.md` header · memory `snr_v2_phaseB_27jun`.

## S&R Detector V1 (SNR-DETECTOR-V1, 27-Jun) — shadow support/resistance detector (default-OFF)
| What | Location |
|---|---|
| Master flag (default OFF) | `system_config.yaml` `sr_detector.enabled: false` → `core/config_loader.py` `SRDetectorConfig` (`SystemConfig.sr_detector`). Disabled ⇒ detector NOT constructed (`main.py` passes `sr_detector=None`) ⇒ zero pipeline change. Other fields = confluence/zone tuning knobs |
| Pure package | `sr_detector/` (imports only core/+stdlib): `models`, `fetch` (token + 3-TF windows + session cache + fail-safe), `pivots`, `zones` (clusters + volume profile), `confluence` (multi-method/multi-TF → HIGH/MED/LOW + evidence), `flags` (BUYING_INTO_RESISTANCE / WEAK_BREAKOUT / NO_VOLUME_CONFIRMATION / LOW_CONFIDENCE_STRUCTURE / NO_CLEAR_STRUCTURE + retest PROPOSAL), `detector` (single bg worker; observe/start/stop) |
| Seam (async, non-gating) | `signals/signal_processor.py` `_sr_observe(...)` AFTER a successful `place()` in BOTH `_process_one` + `continue_from_gate`; enqueues + returns immediately; never blocks/raises. Built+injected in `main.py` (ctor param `sr_detector`) only when enabled; stopped in `_shutdown` |
| Fetch (rate-limited) | `main.py` `_make_sr_fetch_fn(market_kite, rate_limiter)` → `rate_limiter.acquire("historical")` then `kite.historical_data(...)`; `_build_market_data_kite(is_paper, kite_client)` (live=kite_client; paper=read-only kite from token file — api_key NOT hardcoded). Adapter UNTOUCHED |
| Table (schema v37) | `core/schema.sql` TABLE 38 `sr_detector_results` (MAIN db; FK→signals; pure addition). DAO `core/state_store.py` `insert_sr_detector_result`/`get_sr_results_for_backfill`/`update_sr_outcome`. .backup-tested v36→v37 clean — **AWAITING Rama nod before live apply** |
| EOD backfill | `scripts/sr_detector_backfill.py` (join sr_detector_results→trades by signal_id; fills actual_*/win_loss/pnl; terminal-only). **Cron-wired 30-Jun @ 15:58 Mon-Fri** (`sr_detector_backfill`, after `eod_verify`; marker-verified `exit_code_file`). `scripts/sr_corp_action_spotcheck.py` = one-time split-adjust check (VM/live) |
| Tests | `tests/unit/test_sr_detector_{units,fetch,observer,backfill}.py` (37; incl. ★non-blocking + ★dormancy/parity + real-store v37 + seam) |

SHADOW-only (no reject/SL/TGT/STM/entry change). Parity (paper+live; row tagged `mode`). branch `snr-detector-v1-27jun`, NOT pushed. Detail: `docs/SYSTEM_MAP.md` header · memory `snr_detector_v1_27jun`.

## MFE/MAE excursions — Option B post-EOD reconstruction (28-Jun, DEPLOYED, schema v39)
| What | Location |
|---|---|
| Root cause | `candles.ts` (space/naive, 15:40 `fetch_daily_candles`) vs `trades.*_time` (ISO-T+05:30) compared LEXICALLY in `compute_trade_excursions` → 0 candles → empty `trade_excursions`; compute also ran on the hot intraday-exit path before candles exist |
| Datetime-safe compare | `core/state_store.py` `compute_trade_excursions` (parse both → tz-aware IST via `_parse_ist_dt`, pre-filter the indexed `date` col; storage format UNCHANGED; **direction math verbatim**) |
| Reconstruction job | `scripts/reconstruct_excursions.py` — post-EOD; PID lock `data_store/locks/`, cron_heartbeat, `ensure_candles` fetch-if-missing (token-file kite, paper+live parity), `INSERT OR REPLACE` idempotent. Modes `--daily/--date/--all-closed/--dry-run` |
| Taxonomy / guard | WRITTEN / WOULD_FETCH (dry-run preview) / SKIPPED_UNRECONSTRUCTABLE (NULL-exit OR sub-minute<1m) / FAILED (→ CRITICAL sentinel). Guard `written+would_fetch+skipped+failed==examined` |
| Audit table (v39) | `core/schema.sql` TABLE 40 `excursion_reconstruction_runs` (pure addition) + DAO `insert_excursion_reconstruction_run` |
| Removed | the dead compute + silent DEBUG swallow in `orders/order_placer.py::_handle_exit_fill` (`_persist_candle` UNTOUCHED — separate ticket) |
| Cron | `reconstruct_excursions --daily` **15:50 Mon-Fri** (`50 15 * * 1-5`, own marker) + `fetch_daily_candles` now emits `cron_marks/fetch_daily_candles.done` |
| **SIGN convention** | **SIGNED** — `mfe_pct` = best favourable move vs entry (MAY be negative if never-favourable); `mae_pct` = worst adverse (MAY be positive if never-adverse); direction-signed, NOT floored. 1-min reconstruction (sub-minute trades get no row) |
| Tests | `tests/unit/test_reconstruct_excursions.py` (15) |

3B/3C historical backfill (`--all-closed`) DEFERRED → next trading day + live token (expect written=41 / skipped=5 / failed=0). Detail: `docs/SYSTEM_MAP.md` header · memory `mfe_mae_excursions_empty_28jun`.

## C-1 credential-exposure inventory (02-Jul, read-only)
| What | Location |
|---|---|
| Full inventory + remediation plan | `docs/audit/c1_credential_exposure_inventory_02jul2026.md` (names/locations only, no values) |
| Source audit | `docs/audit/system_security_audit_02jul2026.md` (§ C-1) |
| Remote (exposure surface) | `origin = trading-vm:~/trading-system.git` (VM bare repo, SSH) — **NO public forge** → internal-only |
| Only real-secret commit | `9f58848` (`.env.example`, 10 vars) — scrubbed by `7bc3367`; HEAD = `FILL_WHEN_READY` placeholders |
| Live api_key in tracked scripts (HEAD) | `scripts/fetch_daily_candles.py:30`, `scripts/gemini_data_integrity_check.py:45`, `scripts/reconstruct_excursions.py:61` (LFL836 api_key — ACTIVE, non-authenticating alone) |
| Prevention (LIVE) | placeholder `.env.example` · gitignored+untracked `.env` · secret-scan hook `deploy/hooks/{pre-commit,secret_scan.py}` |
| Status | 6 exposed secrets DEAD (rotated 02-Jul) · 2 api_keys ACTIVE · history-purge PLANNED (memory `c1_secret_remediation_02jul`) |

Detail: memory `c1_secret_remediation_02jul` + `post_rotation_creds_02jul`.

## SSH key baseline / re-baseline (security_monitor) — 28-Jun
| What | Location |
|---|---|
| Monitor + check | `scripts/security_monitor.py` `check_authorized_keys` (list-aware) — CRITICAL on a fingerprint not in the allowed set or count > baseline |
| Committed default baseline | `config/security.yaml` `security.expected_key_fingerprint` (single) — git-tracked; a deploy's `checkout -f` resets it |
| **Durable operator override** | `data_store/security/ssh_key_baseline.json` (NOT git-tracked) — `apply_operator_ssh_baseline` overlays it (wins when present); survives deploys |
| **Re-baseline after a legit rotation** | `python scripts/approve_ssh_keys.py` (dry-run diff) → `--apply` (writes the override + re-seeds `security_state.json` + ALWAYS Telegrams). Operator-run only; never auto/monitor-triggered |
| Current authorized key (28-Jun) | `SHA256:uDRN8BJTmfNGFfCLofbnXkIGtWF6qTtrREQrQJGKduk` (ED25519 `oracle-vm-2026`) — Rama's legit rotation, NOT a compromise |

Detail: `docs/SYSTEM_MAP.md` security-watcher row · memory `ssh_key_rebaseline_28jun`.

## SATS — static analysis (PC-only, manual; `sats/` is git-ignored, never deploys)
| What | Path |
|---|---|
| SATS root | `D:\Projects\trading-system\sats\` |
| Bandit venv exe | `sats\bandit-env\Scripts\bandit.exe` (1.9.4) |
| Semgrep venv exe | `sats\semgrep-env\Scripts\semgrep.exe` (1.167.0) |
| Scan scripts | `sats\scripts\scan_bandit.bat`, `sats\scripts\scan_semgrep.bat` |
| Scan reports | `sats\reports\{bandit,semgrep}_<yyyyMMdd_HHmmss>.txt` |
| Semgrep baseline | `sats\semgrep_baseline.txt` (pinned `65439ff`; only NEW findings reported) |

On-demand only (no hooks/automation) — double-click a `.bat`. Both scan the repo root,
exclude `venv,sats,.git`, write a timestamped txt report **and** echo it to the console.
Both `.bat`s set `PYTHONUTF8=1` (else Semgrep/Bandit crash writing the report on Windows cp1252).
Semgrep rulesets `p/python` + `p/security-audit` (login-free; first run downloads, cached after);
`scan_semgrep.bat` honours `semgrep_baseline.txt` (delete it for a full scan).
Bandit reports all severities — add `-ll` for medium+. **Do not** touch the two venvs.

## Pre-flight (`scripts/preflight/`) — daily pre-market readiness, ALERT-ONLY
3 phases via cron Mon-Fri: **A 08:30** (infra) · **B 09:14** (engine readiness) · **C 09:15→09:20**
(signal warmup). Sentinel `data_store/preflight/today.json` · log `logs/preflight.log` · markers
`data_store/cron_marks/preflight_phase_{a,b,c}.done`. Run: `python -m scripts.preflight.orchestrator
--phase A|B|C [--dry-run --as-of-date YYYY-MM-DD]`. Schema v33 (preflight_runs / preflight_check_results
/ preflight_autofix_log). Cron Officer briefing embeds its sentinel banner. (Replaced premarket_healthcheck.)

## Config Sanity Auditor — BUILD 2 (25-Jun), the Config Authority enforcement layer
| What | Location |
|---|---|
| Rule engine (single source) | `core/config_auditor.py` → `audit()` / `audit_system_config()` (startup subset `ACG`) / `audit_app_config()` (full A–G) → `ConfigAuditReport` (`AuditFinding`: PASS/INFO/WARN/BLOCK) |
| Check groups | **A** contradictions (BLOCK, incl. #10) · **B** single-source regression guards · **C** capital-relative sanity (conc < pos-cap ladder) · **D** active-override listing · **E** launch-phase reminders · **F** stale-default guard · **G** cross-field |
| STARTUP caller (fail-fast BLOCK) | `core/config_loader.py` `SystemConfig._cross_field_sanity_checks` → `raise_if_blocked()` (ValidationError, "CONTRADICTORY CONFIG") |
| PRE-FLIGHT caller (Phase A → 09:20) | `scripts/preflight/checks/config_sanity.py` (7 rows, group "Config Sanity"; one memoised audit run) |
| Tests | `tests/unit/test_config_auditor.py` (37) |

Detail: `docs/SYSTEM_MAP.md` Changelog 2026-06-25 · memory `build2_config_sanity_auditor_25jun`. No DB schema; parity (no mode branch).

## Control Tower (`ops/control_tower/`) — VM ops aggregator (Phase 1, 29-Jun)
| What | Location |
|---|---|
| Daily runner | `ops/control_tower/runner.py` — cron **17:05 Mon-Fri**; size-logger FIRST (trend size cols) → aggregator (detection + findings lifecycle + health + status + reporter). **Skips non-trading days** (SKIPPED heartbeat — no false alerts; `--force` bypasses). `marker_name`/heartbeat `control_tower`; superseded the standalone 1a size-logger cron |
| Aggregator | `ops/control_tower/aggregator.py` `run_aggregation(...)` — 4 read-only adapters (security `last_run.json` / cron heartbeats+markers / `config_auditor` / excursion runs) + freshness engine (`freshness.py`) + disk findings (`disk.py`); `self_job="control_tower"` excludes the in-flight runner from its OWN cron freshness check (check_cron_drift @18:00 is its external monitor) |
| Reporter | `ops/control_tower/reporter.py` — (A) noise-controlled Telegram DELTA (CRITICAL always unless acked; HIGH only if NEW/reopened; MED/LOW/INFO never) via the **REAL** `TelegramNotifier`; (B) pull report `data_store/control_tower/report_<date>.{html,csv}` (full picture, never pushed) |
| ACK CLI | `ops/control_tower/cli.py` → `ct list` / `ct ack <id>` / `ct unack <id>` (ACK suppresses a finding from the health score; unack reopens) |
| Severity / health | `ops/control_tower/severity.py` — security WARNING→MEDIUM (dynamic-IP fatigue), CRITICAL→CRITICAL; config BLOCK→CRITICAL, WARN→HIGH. Bands (`health.py`, OPEN-only): ≤50 HEALTHY / ≤150 WARNING / ≤300 ATTENTION / else CRITICAL (weights C100/H50/M20/L5). Disk thresholds (`disk.py`): HIGH ≥85% / CRITICAL ≥95%; backup-growth ≥500MB, log-growth ≥300MB (history-gated) |
| Tables (schema v40) | `control_tower_findings` (dedup category/resource/reason; OPEN→ACKNOWLEDGED→RESOLVED) · `_runs` · `_trends` (per-date sizes+health) · `_status` (per run_date roll-up + `last_successful_run`) · `_freshness` (per stage) |
| Security input | `data_store/security/last_run.json` (written by `scripts/security_monitor.py`; the aggregator's security adapter reads it) |
| Tests | `tests/unit/test_control_tower_phase1{a,b,c,d}.py` |

`daily_report` now records a `cron_heartbeat("daily_report")` on successful xlsx write (Phase 1d source-fix for the 23-Jun false "daily_report missed"). Detail: `docs/SYSTEM_MAP.md` Changelog 2026-06-29 · memory `control_tower_phase1a_29jun`.

## Config snapshots — W0 (daily-report redesign foundation, 01-Jul, schema v41)
| What | Location |
|---|---|
| Table (v41, pure addition) | `core/schema.sql` `config_snapshots` (snapshot_id/date/ts/account_id/mode/trade_type/config_hash/config_json) + `idx_config_snapshots_date`. Records the FULL resolved runtime config per date so the report reads config-as-it-was DB-purely |
| Writer (one entry point) | `core/config_snapshotter.py::snapshot_config(store, app_config, *, snapshot_date, snapshot_ts, account_id, mode, trade_type)` — serializes `AppConfig.model_dump(mode="json")` → stable/sorted JSON; sha256 → `config_hash`; **idempotent per (date, config_hash)** (same-config restart = no-op; same-day config change = new row). No secrets (CL5: env-var NAMES only; `smtp.password` empty in prod) |
| Wiring (parity) | `main.py` after mode is finalized (post interactive flip, after `--status`/`--dry-run` return; ~L1824) → fires in BOTH paper + live (mode is a column); non-fatal (never blocks startup) |
| Version bump | `core/state_store.py` `EXPECTED_SCHEMA_VERSION = 41`; `core/schema.sql` trailing INSERT → `'41'`; `core/migrations.py` v36→v41 pure-addition note (NO MIGRATION_TABLES entry) |
| Data contract | `docs/report_data_contract.md` (NEW) — the permanent SYSTEM-writer ↔ REPORT contract; Config rows seeded (per-strategy params = NEEDS_CAPTURE follow-up, NOT in AppConfig) |
| Tests | `tests/unit/test_config_snapshotter.py` (13: canonical-json stable, hash deterministic, same-config→1 row, changed→2 rows, round-trip, real `load_all` → 1 row + idempotent) |

STAGED — NOT pushed (Rama pushes off-market). NO cron / NO flags / NO trading-code. Report reads only `config_snapshots`, never YAML. Detail: memory `w0_config_snapshots_01jul`.

## Daily Trade Review — ORDERS forensic sheet (report redesign, 01-Jul)
| What | Location |
|---|---|
| Generator (NEW, DB-pure) | `reports/daily_trade_review.py` — `main(--db,--date,--output-dir)` → `reports/output/daily_trade_review_report_<date>.xlsx`. Sheet 1 = `1_Orders` (one row/trade, 69 cols/9 groups A–I); sheet 2 = `2_Signals` (one row/STORED-signal, 21 cols); sheet 3 = `3_Reconciliation` (5 integrity blocks + OVERALL); sheet 4 = `4_Config` (sectioned key/value of the day's AppConfig from W0 config_snapshots); sheet 5 = `5_Strategies` (5 tables: per-strategy/time-bucket/direction/RR daily + trailing confidence-weighted composite ranking); sheet 6 = `Slippage` (6 blocks: summary+3-leg decomposition, band analysis, strategy/stock-wise, worst-20, 10-day trend); + `Dashboard` (FINAL, placed FIRST — summarizes all six). **FINAL TAB ORDER: Dashboard · Reconciliation · Orders · Signals · Strategies · Slippage · Config** (numeric prefixes dropped; sheets built-then-rendered-in-order). **TIER-1 report COMPLETE (7 sheets).** **Phase C cutover DEPLOYED (01-Jul ~21:37, main `01e07b7`)**: LIVE @ **16:07 Mon-Fri** (`cron_registry.yaml` job `daily_trade_review`, monitored → heartbeat); `daily_review` **DELETED** (git rm module+test+entry; git history=rollback); `daily_report` kept in parallel for a ~3–5-day bake-in. `main()` `--date` defaults to today IST. v41 migration applied; manual-run gate PASSED. Rollback/steps: `docs/phase_c_cutover_runbook.md` |
| Signals sheet basis (HONEST) | one row per signal that REACHED STORAGE. Webhook dupes are dropped at dedup BEFORE any INSERT (`webhook_receiver` → `status='DUPLICATE'`, no row) → per-signal `Received=Qual+Rej+Dup` CANNOT hold. Totals = **funnel-first** (Received webhook → storage/18.3% → Qualified → Traded) + storage partition **Δ=0**. `build_signal_records`/`render_signals_sheet`; `_signal_bucket`(unmapped-aware)/`_signal_stage` |
| Reconciliation sheet (integrity backbone) | `build_reconciliation`/`render_reconciliation_sheet` — 5 blocks + OVERALL verdict. CAPITAL = REAL cross-source check `Σ fm_ledger.RELEASE_USED.pnl_delta` == `Σ trades.net_pnl` ≤ ₹1 (NOT `get_daily_realized_net_pnl` — **W10**: it double-subtracts costs; the `RESET_PNL` row is a by-design daily EOD reset, NOT pollution). Partition blocks (1,3) FAIL on an unmapped status. BROKER = PENDING_CAPTURE (W2/W3). **FAIL-injection proven** (capital-drift → CAPITAL+OVERALL flip to FAIL). `_trade_bucket` |
| Config sheet (sheet 4, first W0 consumer) | `build_config_data`/`render_config_sheet` — sectioned two-column key/value of the day's `AppConfig` from `config_snapshots.config_json` (latest by `snapshot_ts`); nested → indented sub-rows; NO snapshot → `— pending W0` placeholder. Sections SYSTEM/RISK/SCORING/SLIPPAGE/BROKER COSTS/STRATEGY(pending W0.1). `_cfg_get`/`_cfg_val`. Build-gate spot-check PASS (min_pass_score=60 / daily_loss_limit_pct=0.03 / leverage INTRADAY=5.0 / entry_end=15:00) |
| Strategies sheet (sheet 5, first analytics) | `build_strategy_data`/`render_strategies_sheet` — 5 tables from the `trades` truth layer (reuses the day's Orders `records` + Signals `srecords`; `strategy_metrics` NOT used — thin/CLOSED-only). win=net>0. T1 per-strategy · T2 30-min entry-bucket · T3 long/short · T4 RR (planned vs filled + `actual_rr`) · T5 RANKING over a **trailing N sessions** (default 20). Composite `(0.40·win%+0.40·ROI+0.20·RR)_norm × min(n/20,1)` — confidence stops a low-sample strat topping a high-sample one. `_build_t5_ranking`/`_minmax_norm`/`_bucket_for`. Build-gate PASS (independent T1 recompute + raw-SQL cross-check + composite sanity on real 30-Jun VM backup) |
| Dashboard sheet (FINAL, placed FIRST) | `build_dashboard_data`/`render_dashboard_sheet` — summarizes all six. **Summary-agrees-with-detail** (every headline from the same sources the detail sheets use → cannot disagree). 7 sections: recon banner · coverage panel · yesterday-vs-today deltas (prior day = data-driven `MAX(date)<today`) · trading · profitability · capital (`fm_ledger` opening/peak/drift + `_max_concurrent` sweep) · deterministic highlights (rule-based, NOT AI). `_prior_trading_day`/`_day_summary`/`_max_concurrent`. Build-gate = full cross-agreement (net==Orders TOTALS, banner==Recon OVERALL, long/short/closure==Strategies/Orders, slip==Slippage, MFE/MAE==independent, yesterday==independent prior-day, tab order) ALL PASS on real 30-Jun VM backup |
| Slippage sheet (sheet 6, last analytics) | `build_slippage_data`/`render_slippage_sheet` — 6 blocks from `trade_slippage_log` (roll-up `orders/slippage_recorder`) + Orders `records`. **Decomposition = 3 legs** `(entry+sl−tgt)×qty` (matches `calc_rr_damage_pct`; ENTRY/SL adverse+, TGT favourable−; sums to total). **Tier NOT persisted** (`slippage.tiers` resolved at runtime by `broker/slippage_engine.tier_for` via InstrumentCache, paper-fill model) → Block 2 buckets by recorded `price_band`; configured tier bps shown as reference (W12 = persist tier per trade). Full-precision aggregate, round-once (no rounding leakage). `_slip_tiers_for_date`. Build-gate PASS (total==Σindependent + decomposition-sums-to-total + band-recompute on real 30-Jun VM backup) |
| Closure classifier (FLAG 2, HONEST) | `daily_trade_review.classify_closure(trade, recon_rows)` — precedence order preserved but RECON_CLOSE narrowed to reconciler-INITIATED only (`STUCK_EXITING`). Bare `MANUAL_CLOSE` = reconciler *detecting* an external close (daily-EOD/operator/RMS collapse, no per-trade trigger) → honest **SYSTEM_CLOSE**, NOT RECON_CLOSE. Positive EOD marker→EOD_SQUAREOFF; `%ORPHAN%`/`SYSTEM_OVERSELL`→ORPHAN_RECOVERY; RECON_EOD_CLOSE only if both. W8 = permanent fix (per-trade closure_source) |
| Sources | `trades` spine + joins `orders`/`signals`/`screener_results`/`trade_slippage_log`/`trade_excursions`/`sr_detector_results`/`reconciliation_log`/`config_snapshots` (all DB; via StateStore getters + `fetch_all`) |
| Pending captures (labels) | `— pending W2` broker-margin · `— pending W5` exit-trigger ts (FLAG 1 absent) · `— pending W6` MFE/MAE provenance (FLAG 3) · `— pending W7` exchange-order-id · **W8** per-trade closure_source (FLAG 2 — split SYSTEM_CLOSE into EOD vs manual/RMS) · **W9** persist per-signal drops / raw receiver log (Signals) · **W0.1** strategy-config snapshot writer (fills Config STRATEGY section) · **W10** `get_daily_realized_net_pnl` double-cost fix (safe direction, non-urgent) · **W11** NULL-exit CLOSED_MANUAL trades lack net_pnl → can FAIL CAPITAL honestly on such days · **W12** persist the resolved slippage tier per trade (enables true per-tier actual-vs-expected; also EOD/manual-exit slippage capture) |
| Tests / gate | `tests/unit/test_daily_trade_review.py` (**57**: 51 sheet-build + Phase-B.1 6 = win%-helper/win%-denominator/skew-skip/coverage%/filled-vs-entered + Phase-C entrypoint default-date+heartbeat); build-gates = Orders forensic + Signals totals + Config spot-check + Strategies aggregate/composite + Slippage totals/decomposition + Dashboard cross-agreement, all vs real 30-Jun VM-backup, ALL PASS. Cutover also: `test_cron_registry.py::test_phase_c_cutover_expectations` |
| Phase C cutover (01-Jul) | `config/cron_registry.yaml` (daily_review `enabled:false`; new `daily_trade_review` 16:07 Mon-Fri) → `deploy/cron/trading-system.cron` regenerated (post-receive auto-installs on push). `core/cron_registry.py::expected_heartbeat_jobs` now skips disabled jobs (no false MISSED for the retired daily_review). `scripts/system_manager.py` EOD check added for the new xlsx. Runbook: `docs/phase_c_cutover_runbook.md`. W13 guardrail: keep `shadow_tracker.enabled` + `innings` table (Multi-Inning view deferred, backfillable) |

Phase B (parallel-run, 6 dates ×2 model runs) + Phase-B.1 fixes = double-confirmed GO. Phase C cutover PREPARED — Rama pushes OFF-MARKET (branch carries W0 v41 migration → DB backup first). Detail: memory `phase_b_parallel_run_01jul` + `phase_c_cutover_01jul`; contract `docs/report_data_contract.md`; runbook `docs/phase_c_cutover_runbook.md`.

## Self-maintaining cron (registry → canonical → auto-install, ARMED 23-Jun)
| What | Path / fact |
|---|---|
| Source of truth | `config/cron_registry.yaml` (executable) |
| Generator | `scripts/generate_crontab.py --generate [--out FILE]` → `deploy/cron/trading-system.cron` (ASCII+LF, deterministic). Also `--gate` (zero-drops proof), `--selftest` (byte round-trip), `--bootstrap`, `--check` |
| Equality | live `crontab -l` == canonical == `generate(registry)` (43 command-lines as of 29-Jun; regenerate the sha after a registry change) |
| **post-receive** (ARMED) | `~/trading-system.git/hooks/post-receive` (from `deploy/hooks/post-receive`) — auto-installs the crontab on every push **iff** `generate==canonical`, else WARN+skip |
| **pre-receive** (DEFERRED) | `deploy/hooks/pre-receive` — **NOT installed** (by choice 23-Jun); would hard-reject a push whose canonical != generate. Arm: install + inject `CRON_GUARD_DRYRUN=1` (dry-run) → clear to enforce. Break-glass: `rm` the hook |
| pre-commit (local clones) | `deploy/hooks/pre-commit` — cron regen on registry change **+ C-1 secret scan** (`deploy/hooks/secret_scan.py`, 02-Jul): blocks a real credential / real `.env` / non-placeholder `.env.example`. Install: `cp deploy/hooks/pre-commit .git/hooks/ && chmod +x`; tests `tests/unit/test_secret_scan.py` |
| **cron-watchdog** (ARMED) | `/etc/systemd/system/cron-watchdog.{service,timer}` — systemd (NOT cron); **19:30 IST daily**; asserts `cron_officer_eod`+`check_cron_drift` heartbeated → CRITICAL sentinel (cron-independent) if not. 1st run 24-Jun |

Detail: `docs/SYSTEM_MAP.md` (Deploy + Cron Jobs + Systemd) · memory `cron_framework_armed_23jun`.
