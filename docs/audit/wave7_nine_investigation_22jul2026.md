# Wave-7 — the 9 NEEDS-INVESTIGATION items, triaged (22-Jul-2026)

**Read-only. Nothing fixed, nothing designed.** Resolves the 9 items left open in
`docs/audit/wave7_triage_21jul2026.md` §"NEEDS INVESTIGATION". Every verdict was checked at its
symbol in the **current tree** (`f858778`; deployed `0fbfc84` = the docs record on top of `e21cf9e`
B1+midnight-floor), **not** the 04-Jul description. Live posture confirmed from the deployed config:
`force_intraday_only: true` + `delivery_enabled: false` (`config/system_config.yaml:81,94` — delivery
is double-locked OFF).

---

## Headline — the ordering worry is resolved

The instruction's risk was: *fix the 3 known careful-loop items, then discover a higher-severity 4th
hiding in the 9.* **That does not happen.** Of the 9:

- **1 already fixed** — M-DP1.
- **2 not a defect** — M-S7 (the pre-check is unwired), M-U1 (its premise is refuted / superseded).
- **2 careful-loop but INERT** under the delivery double-lock — M-C2, M-O7 (they join M-O4's tier).
- **4 ordinary / off the careful path** — M-K2, M-K3, M-A2, M-S3.

**Zero new live-every-day defects. Nothing among the 9 outranks M-O5.** M-O5 stays the top of the
fix queue. The two careful-loop survivors (M-C2, M-O7) are gated by the *same* assumption as M-O4
(delivery off), so they raise no live hazard today and all three arm together the day delivery is
enabled.

---

## The 9, verdicted

| Item | Verdict | Evidence (current tree) |
|---|---|---|
| **M-C2** delivery caps | 🔴 real · **INERT** (delivery-off) | Intraday gates got FIX-185 reservation hardening — OPEN uses `open_count+in_flight_count` (`risk_engine.py:282-283`), SECTOR uses `_effective_sector_margin` (`:256-259`). Delivery caps (`:241-244`) use **plain** `count_open_delivery_positions()`/`count_daily_delivery_trades()`, **no in-flight term** → TOCTOU overshoot. Gated `is_delivery_entry = bucket=="positional"` (`:240`) = False under `force_intraday_only`. |
| **M-O7** GTT-exit skip release | 🔴 real · **INERT** (delivery-off) | `cnc_gtt_monitor.py:451` `if entry_price>0 and qty>0:` still skips `release_used`, **no `else`** → reserved delivery capital not freed for adopted/reconstructed trades. E4/M-C7 added an always-on WARNING (`:494`) that says **"Capital released."** unconditionally — misleading in the skip case. CNC-only path; `delivery_enabled=false`. |
| **M-S3** per-step timeout + 1 worker | 🟡 real (residual) · latent | `step_executor.py:67` `max_workers=1` persists (intentional, FIX-100); `:121` `future.result(timeout=5.0)` (FIX-091) bounds **caller** blocking but does **not** cancel a hung worker thread → "poisons the shared pool / never recovers" root remains **iff a step hangs**. Steps run on pre-fetched `market_data` (pure arithmetic, no I/O) ⇒ a true hang is near-unreachable. Off the careful path. |
| **M-S7** rate-limiter requeue | 🟢 **not a defect** (unwired) | `SignalProcessor(...)` (`main.py:2950-2996`) passes **no `rate_limiter=`** ⇒ `self._rate_limiter is None` ⇒ the FIX-007 requeue/token block (`signal_processor.py:379-406`) **never runs**. The `RateLimiter` at `main.py:1882` is wired to the order placer/adapter (broker API pacing), not signal admission. Even if wired, FIX-007 (non-blocking `try_acquire`) + FIX-048 (non-blocking `put(timeout=1.0)`+`return`) already replaced the busy-spin. LATENT only. |
| **M-K2** G5 dead audit | 🟡 real · **confirmed dead** · lowest | `config_auditor.py:542-543` reads `getattr(s,"entry_start"/"entry_end",None)` but the strategy schema field is **`entry_start_time`/`entry_end_time`** (`strategies/schema.py:137-138`) ⇒ always None ⇒ the per-strategy-window WARN (`:545-554`) never fires. Missing WARN coverage, **no false positive, no runtime harm.** |
| **M-K3** connect() pragmas | 🟡 real · ordinary | `db_connect.py:107-111` sets `busy_timeout` on main + WAL/`synchronous=FULL` on the **attached analytics** DB, but **never `PRAGMA foreign_keys=ON`** on the main DB ⇒ raw-script writes via `connect()` skip FK enforcement (the app's `StateStore` path enforces it). The `synchronous=FULL` half is **moot** — SQLite's per-connection default is already FULL. |
| **M-A2** synchronous alert sends | 🟡 real (residual) · **mitigated** | Sends are still synchronous (`telegram_notifier.py` `_SlidingWindowRateLimiter.acquire()` blocks + the TG6 ~26s retry ladder; no async offload). But the "storm blocks reconnection" premise is **mitigated**: reconnect/stale alerts dedup to **once per episode** (`live_feed.py:88 _reconnect_notified`, `:116 _watchdog_alert_fired`, `:105`), and the stale alert runs on the dedicated `_watchdog_thread` (`:115`), not the ticker thread. Residual: one reconnect-callback send could stall ≤~26s worst-case (Telegram down), one-shot. |
| **M-U1** COLD scanner preflight | 🟢 **not a defect** — premise refuted / superseded | The `if scenario != COLD` skip exists (`startup_checks.py:1589`) but a normal morning runs the checks with scenario **CRASH**, not COLD — a today STARTUP row is written between the raw COLD detect and the effective detect (today's boot log: `startup_scenario=COLD new day` **08:15:21.942** → `CRASH` **08:15:21.959** → `run_all_startup_checks: OK scenario=CRASH` **08:15:29`). `check_scanner_connectivity` logs only on failure ⇒ the silent INFO log = "ran, all reachable." SC7 tests **outbound** Chartink URL reachability (not the signal path); preflight `webhook_responsive` covers the inbound "signals can arrive" path every morning. |
| **M-DP1** post-receive stale paths | ✅ **already fixed** | The stale `deploy/post-receive` duplicate was **deleted** (commit `3d03ff0` "delete the stale post-receive duplicate on the deploy path"); the only surviving hook `deploy/hooks/post-receive` uses correct `/systems/` paths (`TARGET=/home/ubuntu/systems/trading-system:23`, checkout `:31`). Today's push empirically deployed to the correct directory. |

---

## C2 — the WHOLE confirmed-open set, ordered by severity

**Severity = consequence × reachability, stated separately.** The confirmed-open set = the 5 from
the 21-Jul triage (M-O4/O5/O8 careful-loop + M-O9/M-D1 ordinary) **+** the survivors of the 9. Three
buckets — **fix-first (live careful-loop)**, **live but ordinary**, **inert (arms on delivery-enable)**.

### TIER A — fix first (live, careful-loop)

**1. M-O5** — `eod_squareoff.py:252` sets `_fired_for_date[today]=True` before `_fire`, no `except` to unset.
- *Consequence:* **SEVERE.** A failed manual/emergency `fire_now` leaves the day's flag stuck ⇒ the **15:17 scheduled squareoff silently skips** ⇒ leveraged MIS positions carried past squareoff = naked overnight ×5 exposure. The single worst EOD state the system can reach.
- *Reachability:* **LOW.** Only the manual/emergency `fire_now` path; operator-triggered; rare.
- **Which I weight, and why: consequence.** This is the textbook low-probability / high-severity / **undetected** case — the failure defeats the primary EOD safety net *and is silent* (no `except`, no operator signal that squareoff won't fire) *and* the exposure is unbounded (leverage + gap risk). A rare-but-silent-and-catastrophic failure outranks frequent-but-visible-and-bounded ones. **Top of the queue** — matches the instruction's read.

**2. M-O8** — `smart_tgt_manager.py:544` `co_row is None` increments `consecutive_failures` + logs ERROR, returns **without** `_maybe_fire_critical`.
- *Consequence:* MODERATE-HIGH — a CO position with a missing entry-order row stops escalating to CRITICAL; trail management degrades unprotected.
- *Reachability:* LOW-MODERATE — only adopted/reconstructed CO trades, **and it logs ERROR** (not silent). ⚠️ *Refinement:* if the live entry protocol is `LIMIT_TRIPLE` (memory: 361/361, CO not used), CO positions may not exist in the current book ⇒ effectively inert too. Worth confirming before it is scheduled.

### TIER B — live, but off the careful path (ordinary)

**3. M-O9** — `slippage_recorder.py:199,207` computes SL slippage vs `sl_initial`, not the trailed trigger.
- *Consequence:* LOW-MODERATE — corrupts **Phase-2/3 calibration data** (feeds the D2/D3 decisions), never live orders/capital.
- *Reachability:* MODERATE-HIGH — every trailed-SL exit.

**4. M-A2** — synchronous alert sends (mitigated). *Consequence:* LOW (≤~26s one-shot ticker-callback stall, worst case). *Reachability:* LOW (needs a reconnect **and** Telegram slow/down; dedup'd to one per episode).

**5. M-K3** — `connect()` no `foreign_keys=ON`. *Consequence:* LOW-MODERATE (raw cron-script writes skip FK; app path enforces it). *Reachability:* LOW-MODERATE (FK-relevant cron writes). Adjacent to Prune #09 — but note the 15-Jul `eod_cleanup` FK **crash** implies FK was *on* there, so that job likely does **not** use `connect()`; the two are related-but-distinct, not the same site.

**6. M-S3** — single-worker pool poison **iff** a step hangs (near-unreachable — pure arithmetic on pre-fetched data). *Consequence:* MODERATE (screening degrades process-wide). *Reachability:* VERY LOW.

**7. M-D1** — candle `volume` cumulative-vs-delta; *Consequence:* LOW-MODERATE (analytics); *Reachability:* LATENT (only if a token moves to MODE_FULL).

**8. M-K2** — dead G5 audit; *Consequence:* LOWEST (missing WARN coverage, no false positive, no harm); *Reachability:* the *consequence* is ~nil (all 16 strategies use the default window). Cheap one-line rename when convenient.

### TIER C — INERT now; **all three arm together if delivery is enabled** (careful-loop, high consequence)

Gated by the delivery double-lock (`force_intraday_only=true` **and** `delivery_enabled=false`). Flip
both and these move to **Tier A**.

**9. M-C2** — delivery cap TOCTOU overshoot. *Consequence:* HIGH (over-commit beyond the concurrent/daily delivery caps). *Reachability now:* ~0 (delivery off).

**10. M-O4** — EOD promotion naked reverse (spurious MIS MARKET). *Consequence:* HIGH (unwanted opposite-side position). *Reachability now:* ~0 (needs a CNC/NRML position on the same symbol; `force_intraday_only`).

**11. M-O7** — GTT-exit skips capital release + misleading "Capital released" alert. *Consequence:* MODERATE-HIGH (delivery-bucket capital leak, false operator reassurance). *Reachability now:* ~0 (`delivery_enabled=false`).

### CLOSED — not in the queue
- **M-DP1** fixed (`3d03ff0`) · **M-S7** unwired (inert) · **M-U1** premise refuted / preflight-superseded.

---

## C3 — reachability drift vs the 04-Jul assessment

The system changed a lot in 18 days; three drifts matter:

1. **The delivery double-lock is one switch, not three.** M-C2, M-O4, M-O7 are each individually
   "inert," but they share the **same** gate (`force_intraday_only=true` + `delivery_enabled=false`).
   Enabling delivery arms **all three at once** — a Tier-C→Tier-A jump of three careful-loop items in
   a single config flip. Treat "enable delivery" as its own gated work item that carries these three.
2. **M-U1 is *less* reachable than 04-Jul assumed, not more.** The premise "every morning is COLD"
   was already false: the effective boot scenario is CRASH. It is not a live gap.
3. **M-O8's reachability may be *lower* than the 21-Jul triage stated** — if the live entry protocol
   is `LIMIT_TRIPLE` (no CO placements), CO-monitored trades don't exist and the `co_row is None`
   path can't fire. This is the one item whose rank could drop below Tier A on a quick CO-usage check.

*No item became **more** reachable than its assessment assumed.* The operator-trigger findings this
week (persisted-kill = halt) touch the kill path, not `fire_now`, so they do not raise M-O5's
reachability — M-O5 stays low-reachability / weighted on consequence.

## C4 — M-U1 is the fifth "already-fixed-class" discovery this week

As the instruction anticipated: M-U1's 04-Jul framing ("scanner preflight skipped on COLD ⇒ never
runs on a normal morning") is **refuted by current behaviour** — the effective boot scenario is CRASH
(proven in today's boot log), so the `!= COLD` branch is taken, and the operationally-important
inbound path ("signals can arrive") is covered every morning by the preflight orchestrator's
`webhook_responsive` (phase A/B/C, incl. the B2′ 401-as-PASS fix). *Honest residual:* `check_scanner_connectivity`
only logs on failure, so I could not positively observe SC7's success output today — a "could-it-be-red"
gap. But SC7 tests outbound Chartink reachability, not the signal path, so the residual is low-stakes.

---

## Bottom line for the plan

The **9 unknowns reduce to 0 new live careful-loop work.** The fix queue Rama can now work down in
order is: **M-O5 (top) → M-O8** (live careful-loop) → the ordinary Tier-B items → and a **separate
"enable delivery" bundle** that carries M-C2/M-O4/M-O7 *iff* that switch is ever flipped. This is
consistent with the 21-Jul sizing — one to two supervised off-market sessions, not weeks.

*Read-only throughout; no code, config, schema, or state changed; the service was not touched.
Verified against `f858778` (deployed `0fbfc84`).*
