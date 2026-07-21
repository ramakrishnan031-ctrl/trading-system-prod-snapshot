# Wave-7 audit backlog — full triage (21-Jul-2026)

**Read-only. Nothing fixed, nothing designed.** Answers: what "Wave-7" actually is, how many items it
holds, and — the number Rama needs — **how much real work remains before the fix backlog is clear.**

## B1 — what it is, counted
- **"Wave-7" = the 45 M-items of `docs/audit/full_system_audit_04july2026.md`** (04-Jul, against `main`
  around that date). Confirmed: exactly **45 unique `M-<letter><digit>` IDs** (M-A/C/D/DP/K/O/R/S/SC/U/X).
- The tracking doc `docs/audit/q5_wave7_backlog_triage_14jul2026.md` (14-Jul, off `bb9b1e7`) only triaged
  **16** of them (7 "fixed", 9 "remaining"). The other ~29 were closed by *other* work streams
  (Q-batches, numbered FIX-NNN, the M-C*/M-S* hardening waves) and were never reconciled into one list —
  which is why "~45, unsized for weeks" persisted.
- **The 7 q5 "fixed" commits are all in `main`** (`git merge-base --is-ancestor`): `9550dfd c22a25c
  522da32 a8d8164 501d14f aea9bb6 9a303ee`. So they are deployed, not just written.

## Method (B2/B3 — verified against CURRENT code, not the item's own description)
Each item was checked at its cited symbol in today's tree (`0fbfc84`). "FIXED" requires either a passing
dedicated test (all ran green tonight — 5014 passed, only the 11 known PC-env/time-gated failures) **or**
the fix visibly present in the current code, with the commit/code fact named. Line numbers from the 04-Jul
audit have drifted; I matched by function.

---

## The 45 items, bucketed

### ✅ ALREADY FIXED — 25 (evidence named)
| Item | Fixed by (code fact / test) |
|---|---|
| **M-C1** live-seed double-count | `compute_live_seed` + `today_realized_pnl_carryover` cancellation; extended tonight (B1, `e21cf9e`); `test_mc1_live_seed_rehydrate`, `test_q9_live_seed_mc1_wired` |
| **M-C3** per-bucket invariant | `fund_manager.py:2251` H-1+M-C3 per-bucket INV6 non-negativity guard (NEGATIVE_MARGIN_*) |
| **M-C4** kill-switch lock through send | `kill_switch.py` M-C4 lock released before publish/send; `test_kill_switch.py:476,521` |
| **M-C5** commit_adopted CAS race | `fund_manager.py:1030` M-C5 compare-and-swap; `test_mc5_commit_adopted_cas` |
| **M-C7** release_used recompute-from-leverage | `get_entry_commit_margin` persisted margin (`state_store.py:2483`); `test_mc7_release_committed_margin` |
| **M-C8** synchronous hard_kill freeze | async hard_kill (`kill_switch.py` M-C8 ×many); `test_mc8_async_hardkill` |
| **M-O1** double-prefixed quote → raw MARKET | `order_reconciler.py:1784` M-O1 now passes bare symbol to `get_quote` |
| **M-O2** CHECK4 partial-close no capital/PnL | `order_reconciler.py:1834` M-O2; `test_mo2_check4_partial_capital` |
| **M-S1** fresh-quote anchors to stale price | `signal_processor.py` M-S1; `test_fix067_ms1_fresh_anchor` |
| **M-S2** QUEUE_FULL poisons dedup | `webhook_receiver.py` M-S2 (dedup after push); `test_hardening_scenarios` |
| **M-S4** steps 1&3 always 0.0 | ATR/vol populated via `daily_stats`/`forward_shadow` (M-S4 ×many); `test_daily_stats`, `test_forward_shadow` |
| **M-S5** re-entry guard only in _process_one | guard hoisted (`signal_processor.py` M-S5); `test_hardening_scenarios` |
| **M-S6** RetestDiverter double-entry | `retest_monitor.py` M-S6 register authoritative; `test_hardening_scenarios` |
| **M-K1** get_today_closed_pnl date key | `state_store.py:3003` keys `COALESCE(exit_time,updated_at)` (M-K1, 14-Jul) |
| **M-K4** configure() wipes skew callbacks | `time_authority.py:121` `_UNSET` preserve-on-None |
| **M-K5** config persisted unredacted | `config_snapshotter.py` M-K5 redaction; `test_config_snapshotter.py:200` |
| **M-R1** holiday guard broken | `aea9bb6`, delegates to `holiday_guard`; `test_daily_report.py:260` |
| **M-R2** excludes CLOSED_MANUAL | `9a303ee` `_CLOSED_STATUSES`; `test_daily_report.py:378` |
| **M-R3** recon Block-4 false FAIL | `daily_trade_review.py:1037` keys by close date |
| **M-R4** recon Block-2 tautology | tautological block DELETED (`daily_trade_review.py:955`) |
| **M-A1** Telegram entity/tag truncation | `telegram_notifier.py:59` entity/tag-safe truncation + 4xx body logged |
| **M-X2** silent-swallow notifier sends | `a8d8164` ERROR-log (still swallow); `sl_breach_monitor.py:197`, `breakeven_manager.py:380` |
| **M-SC1** eod_verify exits 0 on ISSUES | `eod_verify.py` M-SC1; `test_eod_verify_honest` |
| **M-SC2** screened-CSV wrong DB path | `522da32`; `test_generate_screened_csv_readonly` |
| **M-SC3** reaper filters IN_PROCESS | `501d14f` reaps `PROCESSING`; +test |

### 🟠 NOT A DEFECT — latent / mitigated / clamped — 6
| Item | Why |
|---|---|
| **M-C6** max(1) floor defeats zero multiplier | **Latent** — allocator clamps `min_weight≥0.5`, so a zero multiplier can't occur; pinned by `test_fix133`, `test_q9_sizing_floors_caps` |
| **M-O3** exit-retry no time escalation | **Addressed** — `_EXIT_RETRY_TTL_SEC=180` + `_fire_hard_kill_for_unprotected_position` + explicit reconciler/G5b ownership |
| **M-O6** _recreate flips ACTIVE before place | **Fail-loud now** — on failure sets `needs_review` + CRITICAL alert (no longer silent permanent-retire); residual flip-before-place window is alerted |
| **M-S8** sync INSERT amplifies flood | **Mitigated** — rate-limit runs BEFORE auth (`:285/:418` → 429), so a flood is rejected before the audit write; the per-request `webhook_audit` INSERT is intentional (WR13) |
| **M-K6** DDL extractor comment-blind | **Latent** — only bites on a future stray `(`/`;` inside a migration comment |
| **M-X1** duplicate tick-rounding impls | **Latent** — outputs agree today; drift-risk only on a future tick-policy change |

### 🔴 STILL REAL — careful-loop (capital/boot/order/signal) — 3
| Item | Verified at | Reachability caveat |
|---|---|---|
| **M-O4** EOD promotion sweep has no product filter | `eod_squareoff.py:1560` `current_qty` keyed by **symbol only** — a CNC/NRML position on an intraday symbol makes a filled MIS LIMIT read `remaining>0` → spurious MIS MARKET (naked reverse) | Needs a CNC+MIS position on the SAME symbol; **inert under `force_intraday_only`** |
| **M-O5** fire_now leaves fired-flag set on `_fire` exception | `eod_squareoff.py:252` sets `_fired_for_date[today]=True` BEFORE `_fire`, no except to unset → a failed manual/emergency fire blocks the 15:17 scheduled squareoff for the day | Only the manual/emergency `fire_now` path (operator-trigger reachability like soft_kill — likely rare) |
| **M-O8** missing-CO trail path never escalates | `smart_tgt_manager.py:544` `co_row is None` → increments `consecutive_failures` + logs ERROR, **returns without `_maybe_fire_critical`** | Only when the CO entry-order row is missing (adopted/reconstructed trades) — logs ERROR, so not fully silent |

### 🟡 STILL REAL — ordinary / off the careful path — 2
| Item | Note |
|---|---|
| **M-O9** trailed-SL slippage uses `sl_initial` | `slippage_recorder.py:199,207` computes SL slippage vs `sl_initial`, not the trailed trigger → corrupts Phase-2/3 **calibration data** (not live orders/capital) |
| **M-D1** candle `volume` semantics | q5: PARTIALLY (FIX-135 Item 46 accumulates from ticks); cumulative-vs-delta open, **only bites if a token moves to MODE_FULL** |

### 🔍 NEEDS INVESTIGATION — 9 (cannot classify cheaply; sized as a range in B5)
| Item | What to check |
|---|---|
| **M-C2** delivery caps hardening | FIX-185/Bug-B hardening is present broadly (`risk_engine.py:256/467/501`); confirm it covers the delivery-cap path specifically vs only intraday. Gated by `force_intraday_only`. |
| **M-O7** _finalize_gtt_exit skips release on missing entry | reworked (E4/M-C7); the `if entry_price>0 and qty>0` guard remains — confirm whether the skip now ALERTS |
| **M-S3** per-step timeout + single worker | FIX-091 added `future.result(timeout=5)`, but `max_workers=1` remains — a genuinely-hung step's thread still occupies the pool |
| **M-S7** rate-limiter requeue backoff | `signal_processor.py:379` — confirm whether requeue has backoff (audit: busy-spin) and whether a token is consumed per dequeue |
| **M-K2** G5 per-strategy-window audit dead code | `config_auditor.py` `getattr(s,"entry_start")` vs `entry_start_time` — confirm still dead (low severity: a dead audit = missing coverage, no false positive) |
| **M-K3** connect() no FK/synchronous pragma | cron-script durability; related to the 15-Jul `eod_cleanup` FK crash — confirm current `db_connect.connect` pragmas |
| **M-A2** synchronous alert sends block reconnection | `_on_reconnect` from the ticker thread — confirm whether alert sends are still synchronous in caller/ticker threads |
| **M-U1** scanner preflight skipped on COLD | may be superseded by the S4/preflight rework (B2′ etc.) — confirm `startup_checks` scanner-connectivity runs on a normal COLD morning |
| **M-DP1** post-receive stale paths | the LIVE hook works (tonight's deploy checked out to `/systems/` fine); reconcile the TRACKED `deploy/post-receive` against `deploy/hooks/post-receive` |

## B4 — duplicates against the register
- **M-C1 = tonight's B1** (live seed) — same item, now CLOSED; do not double-count.
- **The `eod_cleanup` REJECTED_* prune note** (q5 doc "Notes") **= decision #09 (Prune)** on the DECISIONS
  BOARD — one item, not two. M-K3 is adjacent (cron FK durability).
- **M-C8 (async hard_kill) is FIXED**, but "first live HARD_KILL proven" remains a *proving* task on the
  careful-loop queue — that is verification, not an open defect.
- No other Wave-7 survivor duplicates a register decision (D1–D4, PerfAllocator, Regime, Freeze, Throttle
  are strategy/config *decisions*, not audit defects).

---

## B5 — ⭐ how much real work remains before the fix backlog is clear

**The backlog is far smaller than "~45" implies.** Of the 45 audit items:
- **31 are closed** — 25 fixed (evidence named) + 6 not-a-defect/latent/mitigated. **~69%.**
- **5 are confirmed still-open** — **3 careful-loop** (all order-path, all with reachability caveats) +
  **2 ordinary** (one calibration-data, one MODE_FULL-latent).
- **9 need investigation** before they can be sized.

**Sized estimate (rough; ranges where investigation is owed):**

| Track | Confirmed | + if investigation confirms them | Rough effort |
|---|---|---|---|
| **Careful-loop** (capital/boot/order/signal) | 3 (M-O4, M-O5, M-O8) | + up to ~4 of the NI set (M-C2, M-O7, M-S3, M-S7) | **~0.5–1 day each, supervised, off-market** — call it **3–7 items, ~3–6 focused sessions** |
| **Ordinary** | 2 (M-O9, M-D1) | + up to ~4 NI (M-K2, M-K3, M-A2, M-U1, M-DP1) | mostly small; **~2–6 items, ~1–2 sessions** |

So: **the fix-backlog is roughly 3 confirmed careful-loop defects and ~2 ordinary, plus ~9 items to
triage (est. one focused session to reduce the NI bucket to a firm count).** All 3 confirmed careful-loop
items carry reachability caveats — two are inert under the current `force_intraday_only`/no-operator-trigger
posture (M-O4, M-O5), and M-O8 logs ERROR (not silent). **None is a live-every-day hazard.**

**Beyond the audit**, the register's remaining fix-shaped items are cosmetic or decisions, not careful-loop
defects: the 3 W10 re-labels (docs), the broker-capital ₹0 report mislabel (tonight's §A — observability,
gates nothing), the security-watcher CRITICAL 1→2 glance, and C3 leftovers. Prune #09 and D1–D4 are Rama's
decisions, not fixes.

**Bottom line for the plan (clear backlog → revise strategies → wipe → restart):** the fix backlog is
**one or two supervised off-market sessions of confirmed careful-loop work, plus one triage session to
firm up the 9 unknowns** — not weeks. The large already-fixed bucket is the real finding.

*Read-only throughout; no code, config, schema, or state changed; the service was not touched.*
