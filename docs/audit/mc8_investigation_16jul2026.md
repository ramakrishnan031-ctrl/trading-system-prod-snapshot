# M-C8 — deeper investigation of the hard_kill emergency-exit path (read-only) — 16-Jul-2026

**Scope:** resolve the design-pivotal unknowns BEFORE the sync→async redesign. **FIXES NOTHING** —
record only. Verified at `main`@`f68d15d` (the M-C4 branch changes no hard_kill code). Line refs are
`capital/kill_switch.py` unless stated.

## ⭐ ONE-LINE STEER (Q1)
**NO — nothing in production needs the `CancellationReport` synchronously: the async design CAN
fire-and-return.** All 6 prod callers invoke `hard_kill` for its EFFECT and **ignore the return** (no
call site assigns it). The only consumers of the report are 3 unit tests, and they exercise the
**legacy `cancel_fn` path that production never takes** — so a design that dispatches **only the adapter
path** to a worker needs **no result mechanism** (no future/poll/callback) and leaves those tests intact.

---

## Q1 — caller usage of the return value → **EFFECT-ONLY, 6/6**
| Caller | Line | Uses report? |
|---|---|---|
| `orders/order_placer.py` (persist_entry_orders failed after broker success) | `:1499` | **No** — bare call in try/except |
| `orders/order_placer.py` (`_place_limit_triple_exits` failed after ENTRY filled) | `:3750` | **No** |
| `capital/fund_manager.py` (`commit_to_used` BL-4) | `:974` | **No** |
| `capital/fund_manager.py` (`_check_invariant`) | `:2259` | **No** |
| `capital/drift_handler.py` (TIER_HARD) | `:231` | **No** |
| `main.py` (`_on_api_failure` circuit breaker, `triggered_by="order_monitor"`) | `:647` | **No** |

Grep for any assignment (`x = ....hard_kill(`) across `orders/ capital/ main.py signals/ scripts/ broker/
ops_dashboard/` → **NONE**. `_run_cancel` has exactly one caller (`:550`); `_exit_all_trades_indestructible`
exactly one prod caller (`_run_cancel:821`).

## Q2 — test assertions on the sync contract → **contents asserted, but only on the LEGACY path**
- `test_kill_switch.py:321-339` `test_hard_kill_invokes_cancel_fn` — `report = ks.hard_kill(...)`, asserts
  `isinstance(...)`, `.attempted == 3`, `.succeeded == 2`, `.failed == ["ord-007"]`.
- `:342-353` `test_hard_kill_no_callback_empty_report` — asserts `0/0/[]`.
- `:356-374` `test_hard_kill_reruns_cancel_when_already_hard_kill` — asserts `cancel_fn` ran **twice**.

**Crucial:** all three build the KS via `_make_ks` (`:113-133`) which passes `on_hard_kill_cancel_fn` and
**no adapter** → `self._adapter is None` → `_run_cancel` takes the **legacy branch** (`:824-846`, fast, no
retry loop). **Production ALWAYS sets the adapter** (`main.py:1983 kill_switch.set_adapter(broker_adapter)`)
→ `_run_cancel:820-821` always takes `_exit_all_trades_indestructible`. The retry loop's own tests call
**`_exit_all_trades_indestructible()` directly** (`tests/integration/test_hard_kill_flatten_chain.py:132`,
`tests/unit/test_fix181.py:402,438`).
⇒ **Design consequence:** dispatch ONLY the adapter path to the worker and keep
`_exit_all_trades_indestructible` a synchronous internal method → **all three sync-contract tests and both
direct-call test suites pass unchanged**; nothing needs an async return.

## Q3 — process-exit / shutdown during a flatten → **THE #1 HAZARD; a latent race exists TODAY**
Mechanics: main thread parks on `_shutdown_event.wait()` (`main.py:3303`). SIGINT/SIGTERM →
`_shutdown_event.set()` (`main.py:1070-1079`) → reverse-order `_shutdown(...)` (`main.py:1099`; incl.
`order_monitor.cancel_all_entry_orders()` `:1221` + `order_monitor.stop()` `:1227`). The **eod-self-exit is
a daemon thread** (`main.py:1062`) that sets the shutdown event once due (`:1012-1035`).
- **What holds the process today:** the caller thread blocks inside `_run_cancel`, and the main thread is
  parked on the event. `_shutdown` **joins no flatten worker** (none exists).
- 🔴 **The EXITING blind spot:** `_eod_self_exit_due` (`main.py:964-978`) is due iff
  `store.count_active_positions() == 0`, and that counts **only `OPEN`/`PARTIAL`/`PENDING_FILL`**
  (`core/state_store.py:639-651`) — **`EXITING` is NOT counted**. The flatten marks trades `EXITING`
  EARLY (`_mark_trade_exiting` `:955-969`, called at `:1110`/`:1134`, i.e. right after placing the exit —
  **before** the retry loop confirms it filled). ⇒ once the first pass marks EXITING, `active` can hit 0
  and, past 16:00, the eod-self-exit fires `shutdown_event.set()` → `_shutdown` → **process exits while
  trades are still in the retry loop**. The "staying up to manage them" guard (`:1036-1042`) does NOT
  cover EXITING trades. This hazard is **latent today** (the sync block narrows but does not close it) and
  becomes **acute** if the flatten moves off the caller thread.
- ⇒ **The async design MUST** (a) make the worker **non-daemon** and/or explicitly **join/drain it in
  `_shutdown`** (bounded by the 2h deadline), and (b) make "flatten in progress" visible to the
  eod-self-exit / shutdown gate — do **not** rely on `count_active_positions()`, which is EXITING-blind.

## Q4 — re-entrant hard_kill → **re-runs the FULL loop; NO in-progress state to join**
`:518-551`: inside the lock, if already HARD_KILL → WARNING + `do_publish = False` (`:535-540`); then
**`report = self._run_cancel()` runs unconditionally at `:550`** (outside the lock). So a 2nd hard_kill —
including from another thread — starts a **second full flatten**, today synchronously on that caller.
**No flag, no worker handle, no in-flight marker exists** for a fix to join. Existing partial mitigations
(not a single-flight): `_mark_trade_exiting` (`:955-969`; comment `:958` — EXITING stops a *concurrent
flatten path* re-selecting via the `OPEN/PARTIAL/PENDING_FILL` query and double-selling) and
`determine_close_direction` re-deriving from broker truth (skip-if-flat). ⇒ the fix should add an explicit
**single-flight** (in-progress flag + worker handle) so a repeat hard_kill **joins/no-ops** rather than
spawning a 2nd worker — and the KS6 "re-runs cancellation" semantic must be consciously re-specified.

## Q5 — thread-safety of `_exit_all_trades_indestructible` (the core async risk)
**Reads:** trades `WHERE status IN ('OPEN','PARTIAL','PENDING_FILL')` + product subquery (`:1051-1058`);
resting SL/TGT orders (`:976-981`); `adapter.get_positions()` (`:1151`, `:870`, + inside
`determine_close_direction`); `adapter.get_quote_raw` LTP (`:895`).
**Mutates:** `UPDATE trades SET status='EXITING'` (`:960-963`, `:1273-1277`); `UPDATE orders SET
status='CANCELLED'` (`:1012-1017`); **broker** `place_order` (`:1118`, `:1178`, `:1259`) and
`cancel_order` (`:1004`); instance dict `self._exit_alert_ts` (`:190`, RMW at `:936-939`).
**Not touched:** `fund_manager` — capital release is deferred to CHECK1/`_check_stuck_exiting`
(`order_reconciler.py:3657`) ⇒ **no capital race** from the loop itself.
**Races introduced by moving to a worker (it becomes CONCURRENT with the fill thread/reconciler, which
today are the very threads it runs *on*):**
1. 🔴 **`trades.status` EXITING vs a late fill** — the fill thread writes trade/order status for the same
   trades; today the flatten is serialized on that thread, on a worker it is not. A late fill landing
   during the flatten could overwrite EXITING (re-exposing the trade to the re-select query) or vice versa.
2. `orders.status='CANCELLED'` vs the fill thread's order-status writes.
3. Two concurrent flattens (Q4) — mitigated by EXITING + broker-truth, but no single-flight.
4. `_exit_alert_ts` read-modify-write — at worst a duplicate alert (minor).
**Strong mitigating property:** every *decision* is re-derived from **broker truth**, not DB state —
`determine_close_direction` on every retry (`:1230-1246`) and the sweep from `get_positions()`
(`:1151`) — and the DB writes are explicitly best-effort ("Broker truth > DB truth", `:1279`). So the
flatten is resilient to DB races by construction; the exposure is the **status-write ordering** vs the fill
thread, which the design must reason about explicitly.

## Q6 — invariants the async version MUST preserve
- **FIX-180 (bounded retry):** `_HARD_KILL_MAX_RETRY_HOURS = 2.0` (`:65`), `_EXIT_ALERT_DEDUP_SEC = 300.0`
  (`:67`); deadline computed `:1201`; backoff 5/15/45s (`:1199`, `:1221-1226`); on deadline → CRITICAL log
  + `_alert_exit_failed` (`:925-953`, per-trade 5-min dedup) + return report with the remainder as failed
  (`:1207-1219`) — **never loop forever, never freeze silently**.
- **FIX-181 (broker-truth sweep + marketable exits):** do NOT early-return on an empty local set
  (`:1065-1069`); sweep any non-zero broker position with no local trade (`:1145-1195`), deduped via
  `handled_symbols` (`:1074`/`:1155-1157`); **H-5** sweep exits under the position's OWN product→intent
  (`:1160-1168`); marketable LIMIT (LTP±buffer) with MARKET fallback (`_marketable_exit_params` `:906-923`,
  `_fetch_ltp` `:885-904`).
- **FIX-190:** **Bug A** reverse-aware close + skip-if-already-flat (`determine_close_direction` `:1102-1111`)
  → never a second SELL → no naked short; **Bug E** cancel resting SL/TGT BEFORE flattening
  (`_cancel_trade_resting_exits` `:971-1025`) → no orphan re-fire; `_mark_trade_exiting` (`:955-969`).
- **H-4 (`:1230-1246`):** re-derive `(close_side, close_qty)` from the CURRENT signed broker net on EVERY
  retry — never re-fire the stale first-pass qty (prevents overselling into a naked reverse after a
  partial fill).

---

## Design inputs, distilled (for the Web Claude design → ChatGPT red-team)
1. **Fire-and-return is viable** — no result mechanism needed (Q1/Q2).
2. **Seam:** dispatch only the **adapter** path (`_exit_all_trades_indestructible`) to a worker; keep the
   method synchronous internally and the legacy `cancel_fn` path synchronous ⇒ all existing tests unchanged.
3. **Lifecycle is the hard part (Q3):** a daemon worker would be killed at shutdown; `count_active_positions()`
   is EXITING-blind so the eod-self-exit can fire mid-flatten (**latent even today**). Needs a non-daemon
   worker + explicit join/drain in `_shutdown` + a flatten-in-progress gate.
4. **Add single-flight (Q4)** so a repeat hard_kill joins instead of spawning a 2nd worker; re-specify KS6's
   "re-runs cancellation".
5. **Main new race (Q5):** EXITING/order-status writes now concurrent with the fill thread; decisions are
   broker-anchored (safe by construction), the DB writes are the exposure.
6. **Preserve FIX-180/181/190 + H-4/H-5 exactly (Q6).**

**Read-only — changed NO code/config/schema/DB. Fixed nothing.**
