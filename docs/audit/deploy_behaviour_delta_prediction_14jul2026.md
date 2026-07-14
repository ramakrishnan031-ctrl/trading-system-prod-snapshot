# Deploy Behaviour-Delta Prediction — Q4 + Q5 + P11 bundle (14-Jul-2026)

**Written BEFORE the push, on purpose.** The first deployed session must test a *prediction*, not
rationalise an outcome. If OBSERVED ≠ PREDICTED, something is wrong — and we would never see it if we
only looked afterwards. This doc is the PREDICTED half of the permanent Deployment-Evidence template
(the OBSERVED half — §7 — is filled after session-1).

- **Branch:** `q5-audit-backlog-14jul`, 27 commits, off `main` `bb9b1e7` (== deployed VM HEAD).
- **Schema:** branch `EXPECTED_SCHEMA_VERSION = 44`; live DB already v44; **no commit touches
  `core/schema.sql`** → **the first post-deploy boot performs NO migration.**
- **These are NOT default-OFF flags.** gate-8, M-S5, M-S6, M-S2, M-K1, M-K4, M-R3 and the migration
  guard are real code on the live path. "Stricter" *can* mean a trade placed today is not placed
  tomorrow. That is exactly what this doc quantifies in advance.
- **Method (per item):** old→new condition · is the pathway *active* in prod? · exact trigger for the
  delta · PREDICTED magnitude + reasoning · the read-only query that confirms it · the OBSERVED result
  that would FALSIFY the prediction → STOP.

---

## §0. BOOT PREDICTION — the first post-deploy boot starts CLEANLY

Three new things could stop or alter the boot. All three are predicted to pass under current prod config:

| New guard | Commit | Trigger to fire | Current prod config | Boot outcome |
|---|---|---|---|---|
| Schema migration | (n/a) | branch schema > live | `EXPECTED=44` == live v44; `schema.sql` untouched | **No migration** |
| Q4b kill_switch fail-fast | `8ea1a4e` | `kill_switch is None` in LIVE | `kill_switch:` block present (`system_config.yaml:214`) → wired non-None | **No abort** |
| Q4c structure∩trailing guard | `c1eea66` | `structure_exit_enabled` AND ≥1 enabled strategy trails | `structure_exit_enabled: false` (`:426`) → rule short-circuits | **No abort** |

**PREDICTED:** boot at the next off-market restart is clean (active/running, NRestarts=0, no migration,
no StartupCheckFailed / config-auditor return-3). **FALSIFY → STOP:** any boot abort, migration line in
the log, or `MigrationNotPermitted` sentinel.

---

## §A. TRADE-COUNT CHANGERS (the four the work order asks about)

### A1 — gate-8 SECTOR_EXPOSURE TOCTOU (`9050ba9`)  ·  ACTIVE

- **Old → new:** gate 8 compared `existing_sector_margin` (`StateStore.sector_exposure` — TRADE ROWS
  only) + this-trade margin vs `0.40 × total`. New gate compares `effective_sector_margin =
  max(existing, open_partial + reserved_live)` — i.e. it folds in **reserved-not-placed** reservations
  (`fund_manager.get_live_reservations`), `max()`-floored so it can **only harden, never loosen**
  (`capital/risk_engine.py:_effective_sector_margin`).
- **Trigger for a NEW rejection:** at `approve()` time there must exist a *reserved-not-placed*
  reservation **in the same sector** (a concurrent same-sector signal whose `reserve()` succeeded but
  whose `PENDING_FILL` row is not yet written), AND folding it in must cross `0.40 × total`.
- **Why it is rare here:** per-symbol concentration is capped at **10%** (`max_concentration_pct:0.10`)
  and per-sector at **40%**. Reaching the sector cap needs **~4 concurrent same-sector positions**
  already on the book; the TOCTOU fold-in adds at most one more. At ~12 trades/day spread across many
  sectors the book rarely holds 3–4 concurrent positions in one sector, let alone at the instant a
  same-sector reservation is mid-flight.
- **PREDICTED historical rejections: ≈ 0** (likely exactly 0 over the ~2-month history).
- **Confirm (read-only):** peak concurrent same-sector `margin_reserved` as a fraction of total. If the
  max ever observed is far below 40%, gate-8's delta is provably 0.
- **FALSIFY → STOP:** a live `SECTOR_EXPOSURE` reject whose DB-truth exposure was *below* the cap
  (i.e. the reject came purely from the reserved-not-placed fold-in) more than ~once a week, OR any day
  where sector exposure genuinely sat near 40% (a concentration problem in its own right).

### A2 — M-S5 shadow-inning re-entry guard, hoisted (`976fa51`,`b549c27`)  ·  PARTLY ACTIVE

- **Old → new:** the shadow-inning guard (reject a fresh entry when the symbol has an **active shadow
  inning** — a simulated 2nd/3rd inning on a *closed* trade whose `symbol_lock` was already released)
  lived ONLY in `_process_one`. Hoisted into `_reject_if_shadow_inning_active` and now also called from
  `continue_from_gate` (EntryGate pullback release) and `continue_from_retest` (RetestMonitor CONFIRMED).
- **Active in prod?** `shadow_tracker.enabled: true` (`:296`) → simulated innings ARE created, so
  `is_tracking()` can be True. **`continue_from_gate` is LIVE** (EntryGate always starts, `main.py:3116`).
  **`continue_from_retest` is DORMANT** (`wait_for_retest_enabled:false` → RetestMonitor not built). So
  the only *live* new enforcement point is the **EntryGate pullback path**.
- **Trigger for a NEW rejection:** a pullback entry, gated then released by EntryGate, on a symbol that
  **at that moment** has an active shadow inning — previously placed, now rejected `SHADOW_INNING_ACTIVE`.
- **Why it is rare:** it needs the coincidence of (a) a signal parked in pullback-wait and released, and
  (b) an active simulated inning on that *exact* symbol at release time. `_process_one` already enforced
  this; only the minority of entries that route through EntryGate release were unguarded.
- **PREDICTED blocked re-entries: 0 to low-single-digits** over the whole history. Magnitude depends on
  how much shadow-inning activity exists (the one number I cannot derive from code alone).
- **Confirm (read-only):** `COUNT(innings WHERE is_real=0)` = simulated innings ever created. If 0, the
  guard has never had anything to fire on → delta 0. If >0, cross-reference gate-release events against
  active innings on the same symbol.
- **FALSIFY → STOP:** more than a couple of `SHADOW_INNING_ACTIVE` rejects on the gate path in one
  session, or any such reject on a symbol with **no** active inning (would mean the guard mis-fires).

### A3 — M-S6 RetestDiverter double-order (`b3a5c94`)  ·  **DORMANT → delta = 0 (certain)**

- **Old → new:** `maybe_divert()`'s bare `except: return False` after a successful `register()` let a
  post-register bookkeeping exception fall through to placement → `_process_one` ALSO placed the original
  order → a real double entry. New: `register()` success is authoritative (`return divert_committed`).
- **Active in prod?** **NO.** `wait_for_retest_enabled: false` (`:321` — "divert never fires; zero
  pipeline change") → `RetestDiverter` is **not even constructed** (`main.py:2923` is inside the
  `_v2_on=false` block). `maybe_divert()` never runs.
- **PREDICTED double-entries prevented: 0.** The work order's sharp worry — *"if it is MANY, we've been
  placing duplicate orders"* — is answered from config alone: **this path is dormant, so it cannot have
  produced duplicates.** The fix is correct defence-in-depth for the day retest is enabled; it changes
  nothing today.
- **Confirm (read-only, and worth doing anyway):** duplicate *real* trades per `signal_id` across **all**
  paths — expect exactly 0. This is the honest cross-check that we are not double-ordering via some
  *other* path either.
- **FALSIFY → STOP:** any `signal_id` with >1 non-rejected/non-cancelled trade row (a duplicate order via
  any path — a finding regardless of M-S6).

### A4 — M-S2 QUEUE_FULL dedup poison (`735a6c3`)  ·  ACTIVE, backpressure-only

- **Old → new:** on `queue.Full` the receiver returned QUEUE_FULL(503) but left BOTH dedup layers
  claiming the signal (fast TTL cache + durable `UNIQUE(fingerprint,fingerprint_date)` row) → the
  sender's 503 retry inside the 300s window was bounced as DUPLICATE and the signal was permanently lost.
  New: on QUEUE_FULL roll back the cache claim; on the retry, recognise the existing QUEUE_FULL row and
  **re-queue** it (flip to QUEUED, reuse `signal_id`) so backpressure recovery works.
- **Trigger:** the signal queue actually fills — a burst of **>512 pending** (`max_queue:512`) arriving
  faster than the pipeline drains — followed by a sender retry.
- **Why it is rare:** the queue is 512-deep and drained continuously; ~3.5k signals/day arrive spread
  across the session, nowhere near 512 simultaneously pending.
- **PREDICTED signals re-queued (that were previously lost): ≈ 0** (0 unless a genuine backpressure burst
  ever occurred). Where it *does* fire, the delta is a *recovered* (formerly-dropped) signal — i.e. it can
  only ADD a trade that should have happened, never remove one.
- **Confirm (read-only):** `COUNT(signals WHERE status='QUEUE_FULL')`. 0 → the queue never filled →
  delta 0. >0 → each was a genuinely dropped signal the fix would now recover.
- **FALSIFY → STOP:** a live QUEUE_FULL storm (would indicate a pipeline stall, separate from this fix),
  or a re-queued signal that produces a *duplicate* (the fix reuses `signal_id` precisely to avoid that).

---

## §B. NON-TRADE-COUNT BEHAVIOUR CHANGES (predicted trade delta = 0, with reason)

### B1 — M-K1 `get_today_closed_pnl` IST exit-date key (`4ec9a9c`)
Reporting/recon only. Keys realised P&L on `substr(COALESCE(exit_time,updated_at),1,10)` instead of
`DATE(updated_at)`. **Byte-identical for the normal intraday flow** (row updates land 08:15–16:00 IST,
whose UTC date == IST date). Bites only 00:00–05:30 IST exits (system down) and rows re-touched days
later. Callers: `eod_broker_reconcile.py` (15:58 cron), `reconcile_pnl.py`. **Trade-count delta = 0.**
Effect: slightly more-correct reported realised P&L on edge-case timestamps. This also retires the
"flaky `test_fix156`" (it was a 00:00–05:30-IST time-of-day failure, not flakiness).

### B2 — M-K4 `time_authority.configure()` preserve-on-unset (`618222e`)
Preserves un-passed skew callbacks instead of nulling them on a 2nd `configure()`. Only **one** caller
(`main.py:293`, at boot). Latent. **Trade-count delta = 0.**

### B3 — M-R3 CAPITAL reconciliation close-date key (`342b871`)
`daily_trade_review` recon keys trades by close date, not `created_at`. Report/recon path, dormant in the
normal flow. **Trade-count delta = 0.** (Note: M-R3/M-R4 were flagged as possibly superseded by the
reworked reconciler — the M-R4 tautology block is handled separately in the work order, item #2.)

### B4 — Migration guard (`ed1c4b9`)
`StateStore(allow_migrate=, market_open=)` + `MigrationNotPermitted` + CRITICAL sentinel: only
`main.py`'s boot path may migrate; 23 other openers refuse + alert. **On THIS deploy it never triggers**
(v44 == v44, no pending migration). It is pure future-safety: the day a schema bump ships, a non-boot
opener (cron/monitor/report) refuses to migrate mid-session and fires a CRITICAL alert instead of
silently migrating the live DB under a running `main.py`. **Trade-count delta = 0.**

---

## §C. NET PREDICTION

> **The first deployed session should look essentially IDENTICAL in trade count to a pre-deploy session.**

Because every trade-count changer closes a **rare race / backpressure / exception / dormant** window
that the current low-volume, concentration-bound regime almost never hits:

| Item | Live? | Predicted daily trade-count delta |
|---|---|---|
| gate-8 sector TOCTOU | yes | ≈ 0 (0 expected) |
| M-S5 shadow-inning (gate path) | yes | 0 to low-single-digits |
| M-S6 divert double-order | **dormant** | **0 (certain)** |
| M-S2 QUEUE_FULL dedup | yes (backpressure) | ≈ 0; if it fires it *recovers* a dropped signal |
| M-K1 / M-K4 / M-R3 / migration guard | mixed | 0 (not trade-count paths) |
| **NET** | | **≈ 0 — session-1 ≈ pre-deploy session** |

**This is the falsifiable claim.** If session-1 shows a materially different trade count, or *any*
rejection that cannot be mapped to one of the triggers above, **STOP, report, be ready to revert**
(revert = off-market restart on `bb9b1e7`; these are interlocking safety fixes, revert as one unit).

**Directional note:** every delta is a *hardening* — it can reject a would-be-unsafe trade (gate-8,
M-S5) or *recover* a wrongly-dropped one (M-S2); none loosens a control. So a *lower* trade count is
explainable-if-rare; a *higher* count is only explainable via M-S2 recovery and must be traced.

---

## §D. EMPIRICAL CONFIRMATION — read-only queries (run in the OFF-MARKET pre-push window)

**Safety:** run with `sqlite3 -readonly` (or against a DB **copy**), NEVER via branch/StateStore code —
a newer-code StateStore open would migrate the live DB (the documented corollary). These are
COUNT/GROUP queries on small/indexed tables; zero write, zero migration, WAL-safe.

```sql
-- A4 · M-S2: did the queue ever fill?  EXPECT 0
SELECT COUNT(*) FROM signals WHERE status = 'QUEUE_FULL';

-- A3 · M-S6 sanity: duplicate REAL trades per signal (any path).  EXPECT empty
SELECT signal_id, COUNT(*) c FROM trades
 WHERE status NOT GLOB 'REJECTED*' AND status NOT IN ('CANCELLED','FAILED')
 GROUP BY signal_id HAVING c > 1;

-- A2 · M-S5: simulated innings ever created (magnitude of shadow activity)
SELECT COUNT(*) total, SUM(CASE WHEN is_real=0 THEN 1 ELSE 0 END) simulated FROM innings;

-- A1 · gate-8: peak per-day per-sector reserved margin (compare top rows vs 0.40 × total capital)
SELECT substr(created_at,1,10) d, sector, COUNT(*) n, ROUND(SUM(margin_reserved),2) m
 FROM trades WHERE sector IS NOT NULL AND status NOT GLOB 'REJECTED*'
 GROUP BY d, sector ORDER BY m DESC LIMIT 15;

-- context: dataset size + span
SELECT COUNT(*) trades, MIN(created_at) first, MAX(created_at) last FROM trades;
```

Expected: QUEUE_FULL = 0 · duplicate-signal set = empty · simulated innings small · peak sector margin
far below 40% of total. Any deviation is itself a finding to write up before the push.

---

## §7. OBSERVED (fill AFTER the first deployed session — reusable template)

| Field | Value |
|---|---|
| Session date | _pending_ |
| Boot | _clean? migration? guards?_ |
| PREDICTED vs OBSERVED trade count | _pending_ |
| Signals rejected (by reason) | _pending_ |
| Trades prevented (gate-8 / M-S5) | _pending_ |
| Duplicates prevented (M-S6) | _pending — expect 0, dormant_ |
| Sector rejections | _pending — expect 0_ |
| Signals recovered (M-S2 re-queue) | _pending_ |
| **UNEXPECTED REJECTS** | _any reject not mapped to §A/§B → STOP_ |
| Rollback decision + reasoning | _pending_ |

**Any UNEXPECTED REJECT → STOP, REPORT, BE READY TO REVERT.**
