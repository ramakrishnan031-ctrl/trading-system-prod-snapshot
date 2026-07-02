# P1 — EOD Broker-Sync / System Observer: Architecture + Gap Assessment (02-Jul-2026)

**Type:** READ-ONLY investigation. No code changed. Feeds a later off-market P1 build.
**Method:** 4 parallel source reads (archaeology · in-session reconciler · EOD-cron map · capital/observer/truth-source), crux claims independently re-verified against source.

---

## STEP 0 — What already EXISTS vs what is genuinely MISSING

**There is no artifact literally named "EOD-broker-sync" or "EOD-observer" that was closed 28-Jun.** A grep of the whole memory store + docs returns zero hits; the three 28-Jun commits are MFE/MAE excursion reconstruction, an `sr_detector` flag, and an SSH re-baseline. "P1 EOD broker-sync" is a **new name** for a capability that today is **spread across separately-named pieces**:

| Existing piece | When / how | Covers | Does NOT cover |
|---|---|---|---|
| `scripts/reconcile_positions.py` | **15:45** cron, **standalone** (own StateStore + creds) | Broker↔local **position** compare; ORPHAN_AT_BROKER / MISSING_AT_BROKER / QTY_MISMATCH → `position_reconciliation` + CRITICAL alert | **Detect+alert only (no remediation)**; **paper fabricates status=OK** (no broker call → no parity); positions only; its output gates nothing downstream |
| `orders/order_reconciler.py` 15s loop | in-process daemon, **15s** (P14) | Broker-authoritative CHECK1-9 + G5b recovery-SL + `_recover_in_flight_entries` (A-1/E-1 tag-correlation, now live in `9becf8c`); auto-fixes most position/order cases | **PROCESS-DEPENDENT** (dies with the service); capital checks are alert-only |
| `orders/eod_squareoff.py` | **15:17**, **in-process** poll (not cron) | Broker-authoritative flatten of MIS/CO (two-pass, residual sweep, broker-qty override) | **PROCESS-DEPENDENT**: if the service is down at 15:17, square-off never fires |
| `ops/control_tower/` runner | **17:05** cron | Read-only **observer**: `eod_squareoff_log` **freshness** (OK/OVERDUE/MISSING) + health/findings + Telegram delta | **No broker session** — observes DB freshness, not broker↔local truth |
| CHECK9 / `f6de000` (01-Jul) | in-session | SL-fill-race false-positive re-check (`_confirm_genuinely_naked`) + oversell guard | In-session only; not the EOD/cron layer |

**02-Jul audit** touches this territory but closed nothing here: A-1/E-1 (timeout/crash→naked) — **already FIXED + deployed in `9becf8c`** (the recovery prepass is live; Agent-confirmed); W3 (system↔broker P&L/position reconciliation wiring) **still open**; E-4 (no `/health` liveness for `order_reconciler`/`eod-scheduler`) **still open**; §5 confirms `eod_squareoff` + CHECK9 sound.

**Genuinely MISSING (= the P1 target):**
1. A **standalone, process-independent, broker-authoritative EOD reconcile** of positions **+ orders + P&L + capital** with **REAL** verification.
2. `eod_verify` today **false-VERIFYs** (local-DB only, never queries the broker) — see STEP 2.
3. **No scheduled EOD P&L reconcile** (`reconcile_pnl.py` is orphaned) and **no EOD capital reconcile** to broker margins.
4. **Paper parity** for a broker EOD reconcile.
5. Reconciler/eod-scheduler **liveness observation** (E-4).

---

## STEP 1 — Architecture map

### Layer A — In-session reconciler (`order_reconciler.py`, 15s daemon, PROCESS-DEPENDENT)
Non-reentrant `reconcile_once` → `_reconcile`. One `get_positions()` fetch feeds CHECK1-5; broker open-orders feed CHECK6/8/9; margins feed G3. Broker is truth for positions/orders. Actions:
- **Auto-fix:** CHECK1 MANUAL_CLOSE (broker flat → close local + release capital at broker exit price, `:1005`), CHECK4 PARTIAL_CLOSE (`:1763`), CHECK2 orphan adopt / system-oversell flatten / in-flight-flatten-under-HARD_KILL (`:1437`), STUCK_EXITING resolver (`:1170`), CHECK6 ORPHAN_ORDER after 3 cycles (`:1889`), G5b crash-recovery SL (`:2333`), DUPLICATE_EXITS dedupe (`:2646`), `_recover_in_flight_entries` A-1/E-1 (`:3133`).
- **Escalate (kill):** CHECK9 MISSING_EXITS → `soft_kill` + oversell-guarded emergency close (`:2015/:2133/:2234`); 3 consecutive auth-error cycles → `soft_kill` (`:647`).
- **Alert-only:** CHECK5 POSITION_GREW (`:1837`), G3 CAPITAL_DRIFT (`:2795`), CHECK7 capital-accounting self-check (`:2958`), CHECK8 CO_SL_DRIFT (`:3045`).
- **Broker-unreachable handling:** `get_positions` timeout → CHECK1-5 skipped; recovery prepass **defers the whole set** (never FAILED/release on a blind poll); **but** G5b places an SL blind (fail-safe) and CHECK9 `_confirm_genuinely_naked` returns naked=True when both broker queries fail (soft_kill can fire blind — though the emergency *sell* re-checks and skips if unconfirmed).

### Layer B — EOD cron chain
| Time | Job | Scheduled? | Process-dep? | Broker truth? |
|---|---|---|---|---|
| 15:17 | `eod_squareoff` | in-process | **YES** | YES (flatten) |
| 15:45 | `reconcile_positions.py` | **cron** | **NO (standalone)** | YES (positions) — but paper fabricates OK; detect+alert only |
| 15:50 | `eod_cleanup.py` | cron | NO | **NO** (DB housekeeping) |
| 15:55 | `eod_verify.py` | cron | NO | **NO — local DB only (false-VERIFY)** |
| 16:15 slot | ~~`reconcile_pnl.py`~~ | **NOT SCHEDULED** | — | orphaned/dead |
| 17:05 | `control_tower` runner | cron | NO | NO (freshness observer) |
| 18:45 | `system_manager.py` | cron | NO | NO (day capital from **local DB**, "no broker") |

### Layer C — Capital ledger + observer + EOD truth-source
- **Capital is LOCAL-authoritative.** `FundManager.sync_from_broker` (broker margins → local `_total`) fires only at **startup** and a **09:15 one-shot** — **never periodically, never at EOD**. `fm_ledger` (reserve/release/commit/release_used) is a purely local write-ahead ledger guarded by the 3-balance invariant.
- **Escalation is gated by `source_module`** (`drift_handler.py`): only `fund_manager` / `fund_manager_self_check` / `fund_manager_bucket_overflow` sources escalate to soft/hard kill. **`order_reconciler`-sourced drift (G3, CHECK5) NEVER kills** — INFO-log only. So the only capital conditions that auto-kill are drift **between the local ledger and itself** (invariant violation, commit failure, sync bucket-overflow, CHECK7) — **not** drift between local and **broker**.
- **EOD truth-source per dimension:** positions = **broker** (continuous, not a discrete EOD event); orders = **broker**; realized P&L = **broker-priced but locally-ledgered**; **capital/funds = LOCAL** (broker only sampled for *alerting* after 09:15, never for correction). No EOD checkpoint declares "broker is truth, overwrite local."

---

## STEP 2 — Gap list (confirmed against current code, post 28-Jun + 02-Jul + f6de000/9becf8c)

| # | Candidate gap | Status | Evidence |
|---|---|---|---|
| 1 | No scheduled broker-authoritative EOD **P&L/capital** reconcile | **OPEN** | `reconcile_pnl` not in crontab/registry; `system_manager` capital = local DB ("no broker"); no EOD `sync_from_broker` |
| 2 | `eod_verify` **false-VERIFY** | **OPEN (severe)** | `eod_verify.py:44-81` — counts local OPEN/PARTIAL trades + PENDING orders only; **zero broker calls**; VERIFIED whenever local DB looks clean |
| 2b | `eod_verify` P&L arm is **dead code (column bug)** | **OPEN (new find)** | queries `system_net_pnl - broker_net_pnl` (`:61`); real cols are `system_pnl`/`broker_pnl` (`schema.sql:783-785`) → `OperationalError` swallowed by bare `except` → `pnl_variance` stuck `0.0`; the source row is also never produced (reconcile_pnl orphaned) |
| 3 | Process-down dependency | **OPEN (partial)** | the *safety actions* (15:17 square-off, 15s reconciler) are process-dependent; the *standalone* jobs that still run (reconcile_positions 15:45, eod_verify 15:55) can **detect but not remediate**, and eod_verify **false-passes** exactly when the process was down |
| 4 | Orphan **orders** at broker invisible to cron | **OPEN (partial)** | reconcile_positions is **positions-only**; orphan-*order* detection (CHECK6) is in-session (process-dependent) → not covered by any cron job |
| 5 | Capital-leak **detect-only** | **OPEN** | G3/CHECK5 publish `CapitalDriftDetected(source=order_reconciler)` → non-escalating → INFO-log; no kill, no correction |
| 6 | Reconciler-drift **never auto-kills** | **OPEN** | same — reconciler-sourced drift can never escalate; only fund_manager-internal inconsistency kills |
| 7 | TGT-only-cancel / SL-live (exit-leg asymmetry) unflagged | **MOSTLY HANDLED** | DUPLICATE_EXITS dedupes >1 live leg; CHECK9 catches SL-absent naked; the genuine gap is the **manual-TARGET-hit exit-reason label** (deferred, `order_placer.py:2092`) — fold into P1 |
| 8 | ~2s orphan window (fill→local ingest) | **MITIGATED** | order_monitor 2s poll lag; f6de000 re-checks broker truth so the window no longer causes false-positive kills; the genuine window is bounded + covered next cycle |
| 9 | A-1/E-1 timeout/crash → naked (tag correlation) | **CLOSED** | fixed + deployed `9becf8c` (`_recover_in_flight_entries` live) |
| 10 | Reconciler/eod-scheduler `/health` liveness | **OPEN** | audit E-4 — if the reconciler thread dies, CHECK9/G5b/orphan/drift all silently cease with no signal |
| 11 | `reconcile_positions` no **paper parity** | **OPEN** | paper fabricates `status=OK` with no broker call |
| 12 | `eod_verify` VERIFIED can **contradict** reconcile_positions | **OPEN (new find)** | eod_verify never reads `position_reconciliation`; reconcile_positions can CRITICAL-alert ORPHAN at 15:45 while eod_verify emits VERIFIED at 15:55 |

---

## STEP 3 — Failure scenarios (protected vs gap)

| Scenario | What happens today | Verdict |
|---|---|---|
| **Manual close** (operator flattens at broker) | CHECK1 (15s) detects broker-flat → closes local + releases capital at broker exit price + CRITICAL alert | **PROTECTED** (in-session) |
| **Manual TARGET hit** (operator/RMS) | CHECK1 closes it; exit-reason **label** may mis-read (the deferred `TGT_HIT` vs `MANUAL_CLOSE` relabel, `order_placer.py:2092`) | **PROTECTED financially; label refinement pending → fold into P1** |
| **API outage — mid-session** | reconciler skips CHECK1-5, defers recovery (no blind FAILED); 3 consecutive auth cycles → soft_kill; order_monitor 3 fails → hard_kill | **PROTECTED** (fail-safe defer + auth-kill) |
| **API outage — at EOD** | reconcile_positions **skips** (returns "skipped_api_unavailable", no false-pass); **but eod_verify still emits VERIFIED** (local-only) | **GAP** — EOD "all clear" with the broker unverifiable |
| **Process restart — mid-session** | startup `reconcile_once()` + recovery prepass adopt/close/reconstruct-capital before pipeline; EOD9 restart-recovery | **PROTECTED** |
| **Process restart — overnight / down at 15:17** | square-off never fires → positions **naked at broker overnight**; reconcile_positions (15:45) CRITICAL-alerts ORPHAN/MISSING (detect only); eod_verify (15:55) **VERIFIED** | **GAP (highest)** — naked overnight, alert-but-no-remediate, and a contradictory "VERIFIED" |
| **Missed / duplicate webhook** | webhook dedup + one-trade-per-signal; reconciler adopts/flattens broker orphans | **PROTECTED** (in-session) |
| **Stale position cache** | reconciler re-fetches `get_positions` every 15s (no cache); CHECK4/5 reconcile qty to broker | **PROTECTED** (in-session); **GAP at EOD if process down** |

**Pattern:** every "PROTECTED" relies on the **in-session** reconciler being alive. The gaps concentrate where the **process is down** or where verification is **local-only** (eod_verify) — exactly the EOD/cron layer P1 targets.

---

## STEP 4 — Risk ranking + build sequence

**Risk = capital/safety impact × likelihood:**
1. **HIGHEST — `eod_verify` false-VERIFY + process-down-at-EOD.** A naked broker position overnight while the operator is told "EOD VERIFIED: all clear" (and reconcile_positions' contradictory CRITICAL can be lost in noise). Impact: uncapped overnight exposure; Likelihood: low-moderate (needs the service down at 15:17 or an untracked broker fill). This is the core P1 justification.
2. **HIGH — No EOD P&L/capital broker reconcile.** Capital drift or a P&L mismatch accrues undetected after close (in-session G3 only covers while running, and only alerts). The daily-loss-limit reads local ledger — a broker-vs-local capital gap is never reconciled.
3. **MEDIUM — Capital drift never auto-kills** (G3/reconciler non-escalating). A genuine broker-vs-local divergence alerts but trading continues.
4. **MEDIUM — Reconciler/eod-scheduler no liveness (E-4).** Low likelihood, high blast radius (all in-session safety silently stops).
5. **LOWER — paper parity for the EOD reconcile; orphan-orders-at-broker via cron; exit-reason label.**

**Build sequence (as scoped):**
- **P1 — Standalone broker-authoritative EOD reconcile** (process-independent cron; **positions + orders + P&L + capital**; REAL verification replacing `eod_verify`'s false-VERIFY; **broker-unreachable ⇒ UNVERIFIED, never false-pass**). Concretely: a new/rewritten job that queries the broker (`get_positions`, `get_all_orders`, `get_margins`, day realized-P&L), compares to local, and emits VERIFIED **only** on broker-confirmed clean, ISSUES on divergence, UNVERIFIED on broker-unreachable. It **subsumes** the orphaned `reconcile_pnl` (fix the column bug + the generic-env-var creds bug), **consumes** `reconcile_positions`' output (or absorbs it) so the VERIFIED verdict can't contradict it, and adds the missing **capital-vs-margins** check. **Paper parity** via the adapter simulation. **Fold in manual-close handling + the exit-reason label refinement.**
- **P2 — Divergence observer / alerting.** Escalate (not just alert) on genuine broker divergence at EOD; add `/health` liveness for `order_reconciler` + `eod-scheduler` (E-4); make the EOD reconcile a Control-Tower freshness/finding source.
- **P3 — In-session edge auto-fixes.** Make sustained/large G3 capital drift escalate (currently never kills); optional guarded auto-flatten of a confirmed orphan-at-broker at EOD.

**Parity note:** reconciliation is broker-authoritative — a live concept. In paper, `reconcile_positions` fabricates OK and the adapter's simulated state is the "broker," so any P1 EOD reconcile in paper is a **self-consistency (parity) check**, not an independent authority. P1 must keep one code path and simulate the broker via the adapter (as the in-session reconciler already does), flagging paper explicitly.

---
**No code changed. Awaiting review → the P1 build in a later off-market session.**
