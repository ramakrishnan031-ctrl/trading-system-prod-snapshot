# A-2 — `BrokerTimeoutError` retry → duplicate entry: DESIGN + IMPLEMENTATION

**Date:** 02-Jul-2026 · **Against:** `main @ 9becf8c` (POST A-1/E-1 deploy) · **Status:** IMPLEMENTED (committed, unpushed — deploy AFTER the 03-Jul 08:15 A-1/E-1 boot confirms clean).
**Audit:** `docs/audit/system_security_audit_02jul2026.md` § A-2 (HIGH, lead-verified).

---

## IMPLEMENTATION UPDATE (02-Jul, deltas from the original design below)

Two things changed during implementation, both improvements:

1. **Reused the existing `TIMEOUT` signal status — NOT a new `PLACEMENT_UNKNOWN`.**
   Implementation surfaced a **CHECK constraint on `signals.status`** (a multi-line `status IN (...)` the Phase-0 grep missed) that a new `PLACEMENT_UNKNOWN` would violate at the UPDATE (→ IntegrityError → fell through to `PLACEMENT_FAILED`; the new test caught it). `TIMEOUT` is **already in the CHECK constraint** AND already mapped in the report's `_KNOWN_OTHER_STATUSES` (→ "other" bucket, SIGNAL-STORAGE Δ=0), and is currently unused as a signal status. So the fix marks the signal `TIMEOUT` on a place() timeout → **zero schema migration, zero report change** (the `daily_trade_review` bucket edits were reverted). This is Phase-0 #6's "reuse an already-mapped status" option — strictly better than adding a new one.

2. **Applied to ALL THREE placement paths, not just `_process_one`.**
   Phase-0 #3 (singular reservation ownership) required tracing every release site. `continue_from_gate` and `continue_from_retest` never re-queued (no duplicate there), but they DID release the reservation on timeout via their outer `except Exception` → `PLACEMENT_FAILED` handler — the same double-owner inconsistency. The dedicated `except BrokerTimeoutError` handler was added to all three so ownership is truly singular in every entry path.

**Changed files:** `signals/signal_processor.py` (3 handlers) + tests (`test_signal_processor.py` main-path ×4, `test_sr_v2_continue.py` retest, `test_slice2_strategy_control.py` gate). NO order_placer/reconciler/schema/config/cron/report change. The trade status stays the already-mapped `UNKNOWN_IN_FLIGHT`; the reconciler recovery (`_recover_in_flight_entries`, 15 s) remains the sole resolver.

The original design (with `PLACEMENT_UNKNOWN`) is preserved below for the reasoning trail; substitute `TIMEOUT` for `PLACEMENT_UNKNOWN` throughout.

---

## 1. Verified root cause (CONFIRMED, still live in code — masked by throttle)

On `BrokerTimeoutError`, the **order-placement path defers correctly**, but the **caller retries**, re-running the whole pipeline and submitting a **second** `place_order`. A timeout does not mean the order failed — the first submission may already be live at the broker → **2× exposure**, each leg with its own SL/TGT. No idempotency key.

### Exact sequence (the duplicate)
1. `signal_processor._process_signal` → `self._placer.place(...)` — `signals/signal_processor.py:992`.
2. Broker place times out → `orders/order_placer.py:1291` `except BrokerTimeoutError`:
   - logs `CRITICAL place_timeout_UNKNOWN_IN_FLIGHT`,
   - `update_trade_status(trade_id, "UNKNOWN_IN_FLIGHT")` (`:1307`),
   - enqueues to `_timeout_recovery_queue` (`:1315-1321`) — keeps the reservation (FIX-068),
   - **`raise`s** (`:1323`) — order_placer itself does NOT retry (correct).
3. Caller catches it — `signal_processor.py:1009` `except (BrokerRateLimitError, BrokerTimeoutError)`:
   - `retry_count += 1`, `requeued = True` (`:1026-1027`),
   - **re-`put`s the signal on the queue** (`:1044`) → the pipeline re-runs → **second `place()` → second `kite.place_order`**,
   - releases the reservation with reason `requeued_transient_error` (`:1047`).
4. If the first submission had reached the broker, the retry places a **duplicate live order**.

This contradicts FIX-068's own comment (order_placer:1292-1295: "Do NOT mark FAILED… let reconciler poll").

### Why it does not fire today (masking, not prevention)
The re-queued signal re-hits the **entry throttle** — `signal_processor.py:981` `self._entry_throttle.admit(symbol)`. Config (`config/system_config.yaml`):
- `min_gap_between_entries_sec: 20` (global between any two PLACED entries),
- `per_symbol_cooldown_sec: 300` (no same-symbol re-entry within 5 min).

The near-immediate retry is rejected as `ENTRY_THROTTLED`. **This is a rate-limiter, not an idempotency guard.** Disable the throttle (all gates → 0 ⇒ `EntryThrottle.enabled` False) or delay the retry past the gap and the double-submit goes live. Idempotency must not depend on a throttle.

### Secondary (same path) — capital coordination
On re-queue, signal_processor **releases** the reservation (`:1047`), while order_placer's FIX-068 deliberately **kept** it for the `UNKNOWN_IN_FLIGHT` trade. If that trade's order actually fills, capital was already released → **under-count** for a real position (until the reconciler reconstructs it).

---

## 2. `BrokerRateLimitError` vs `BrokerTimeoutError` — only one is retry-safe

- **`BrokerRateLimitError`** — raised by `broker/rate_limiter.py:191/:215` inside `acquire()`, **before any network I/O**. Nothing was sent to the broker → re-running is idempotent → **retry-safe**.
- **`BrokerTimeoutError`** — raised inside the live `place_order` network call; the order **may** have reached the broker → **NOT retry-safe**.

The current combined `except (BrokerRateLimitError, BrokerTimeoutError)` conflates them.

---

## 3. The A-1/E-1 interaction (decisive) — the recovery already OWNS a timed-out entry

`main.py:2712` runs a synchronous `reconcile_once()` at startup; `main.py:2785` `order_reconciler.start()` launches the **background daemon poll** — **RC3: every `poll_interval_sec` = 15 s** (`order_reconciler.py:576 _poll_loop`). Each cycle runs `_reconcile` → **`_recover_in_flight_entries` (`order_reconciler.py:3133`)** — **startup AND mid-session**.

That prepass:
- pulls the timeout queue (`get_timeout_recovery_trades`) + the crash set (`get_orphaned_pending_trades`), de-duped (`:3158`);
- does **one** `get_all_orders()`; if the broker is unreachable → **defers the whole set** (no FAILED, no release — `:3170-3180`);
- per trade: `correlate_entry_by_tag(trade_id, direction, symbol, qty, all_orders)` (`:3244`) →
  - **MATCH + FILLED** → `_recovery_adopt_filled` → `adopt_recovery_trade_to_open` (state→OPEN + backfill in one txn) + **crash-aware capital commit** (restore lost reserve, then reserved→used; exactly once — `:3400/:3408`); G5b places the protective SL (created_at used as entry-time proxy so the same-cycle recovery-SL isn't deferred — `:3382-3385`);
  - **MATCH + RESTING** → `_recovery_defer_resting` → `restore_adopted_reservation` (`:3354`) + defer;
  - **ABSENT** → `_recovery_absent` → FAILED + release **only on confirmed broker-absence** (poll budget);
  - under **HARD_KILL** → flatten instead of adopt.

**Therefore the correct behaviour on `BrokerTimeoutError` is: do NOT retry. Set `UNKNOWN_IN_FLIGHT` (already done) and let the reconciler own it** — adopt+protect if the order reached the broker, FAILED+release only if confirmed absent. The retry is not just unsafe (duplicate) — it is **redundant**: the recovery already covers both outcomes.

**What the code does TODAY:** it STILL retries (`signal_processor.py:1009` includes `BrokerTimeoutError`) — creating the duplicate *before* the reconciler runs. The deferral machinery (order_placer queue + reconciler) is fully deployed and idle for this case; the caller just needs to stop retrying.

---

## 4. Design (propose — no code)

**Split the catch in `signal_processor._process_signal`:**

- **Keep** `BrokerRateLimitError` (and `BrokerRateLimit429Error` if surfaced here) in the retry/re-queue branch — pre-submission, idempotent.
- **Add a dedicated `except BrokerTimeoutError`** that:
  1. `self._ks.record_api_failure(err)` — keep (kill-switch API-health tracking; the whitelist already counts `BrokerTimeoutError`).
  2. **Does NOT re-queue** (no retry → no duplicate).
  3. **Does NOT release the reservation** — order_placer kept it (FIX-068) and the reconciler reconstructs/commits it; set `reservation_id = None` locally so the outer/`finally` handlers don't release it either. (This also closes the § 1 secondary capital under-count — the reserve stays continuously held, no transient over-count window.)
  4. Sets the **signal** status honestly — **not** `PLACEMENT_FAILED` (misleading; placement is unknown, possibly live). Propose `PLACEMENT_UNKNOWN` (new signal-status string; the *trade* is already `UNKNOWN_IN_FLIGHT`). Cosmetic/bookkeeping only.
  5. `requeued = False` → the `finally` releases the in-flight symbol lock (`:1148`) — correct: the same signal is not re-admitted, and a *different* signal on the symbol is still governed by the unchanged re-entry gate + throttle.
  6. Returns (no raise needed) — the trade is `UNKNOWN_IN_FLIGHT`, the reconciler (~15 s) owns it.

**One path, parity-safe:** paper simulates the broker; a paper timeout drives the same `UNKNOWN_IN_FLIGHT` → reconciler → `get_all_orders()` (paper implements it, incl. the paper tag from A-1 Phase A) path. No live-only branch.

**Preserved:** RAMCOIND (exit dedupe — untouched), CHECK9 (naked-position flatten backstop — still fires if recovery ever fails), G5b (recovery-SL on adopt — the design routes into it), FIX-068 (reservation retention — now honoured on both ends), the throttle (stays as a burst-limiter, no longer load-bearing for idempotency).

**Removes the duplicate risk without re-introducing naked/lost:** no second submit; the single (possibly-live) order is adopted+protected or FAILED-only-on-confirmed-absence by the now-deployed recovery.

---

## 5. Risk

- **Residual window:** between the timeout and the next reconcile (≤15 s) the position, if live, is briefly unmanaged by a local SL — **identical to the existing A-1 timeout window**, not new; CHECK9 + the reconciler cover it. The retry never shortened this window (it *added* a duplicate).
- **Mid-session dependency on the daemon:** the fix relies on `order_reconciler.start()` running. If the reconciler thread is dead, a timed-out entry stays `UNKNOWN_IN_FLIGHT` unprotected until restart. Mitigation: the audit's separate `/health` `is_alive()` recommendation (D-1) — track, not block. (Today's retry did NOT protect this case either.)
- **Signal-status change** (`PLACEMENT_UNKNOWN`) — verify no report/metric keys off the exact `PLACEMENT_FAILED` string for this path; keep the trade-level `UNKNOWN_IN_FLIGHT` authoritative.
- **`record_api_failure` on every timeout** — unchanged from today; kill-switch escalation thresholds already tuned for it.

---

## 6. Deployment-safe implementation plan

1. **Code (one file):** `signal_processor._process_signal` — split the `except`; add `except BrokerTimeoutError` before the rate-limit branch (dedicated handling above). No order_placer/reconciler change (they already defer + own).
2. **Optional (cleanliness):** add `PLACEMENT_UNKNOWN` to the signal-status vocabulary + any report bucket; else reuse an existing neutral status.
3. **Tests:** the matrix in § 7 (fail-on-old / pass-on-new) + full regression; exercise **paper** for parity.
4. **Deploy:** off-market, same fast-forward push + post-receive pattern as `9becf8c`; no schema, no config, no cron. First live exercise observed at the next real timeout (rare) — watch for `UNKNOWN_IN_FLIGHT` → recovery adopt/fail with **exactly one** broker order.

---

## 7. Test matrix

| # | Scenario | Expected (NEW) | Fails on OLD? |
|---|----------|----------------|---------------|
| 1 | Timeout, first order **DID reach** broker & FILLED | ONE order; trade `UNKNOWN_IN_FLIGHT` → reconciler adopts→OPEN + G5b SL; **no second submit** | OLD retries → 2 orders |
| 2 | Timeout, first order **DID reach** broker & RESTING | ONE order; reconciler defers (reserve ensured) → adopts on fill | OLD retries → 2 orders |
| 3 | Timeout, first order **never reached** broker | trade `UNKNOWN_IN_FLIGHT` → reconciler `ABSENT` → FAILED + release (after poll budget) | OLD retries → a NEW (possibly real) order |
| 4 | `BrokerRateLimitError` (pre-submission) | still re-queued + retried (≤3); nothing double-submitted | unchanged (regression guard) |
| 5 | Reservation accounting on timeout | reserve held continuously (not released by signal_processor) → reconciler commits reserved→used exactly once | OLD releases on re-queue → under-count if it fills |
| 6 | Throttle disabled (all gates 0) + timeout | still ONE order (idempotency no longer throttle-dependent) | OLD → immediate duplicate |
| 7 | **Paper** timeout (parity) | same `UNKNOWN_IN_FLIGHT` → recovery path via paper `get_all_orders()` | — |
| 8 | HARD_KILL active at recovery time | adopted position flattened, not naked | — |

---

**Next:** review → implement per § 6 (permanent fix, paper/live parity). No code until approved.
