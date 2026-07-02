# A-1 + E-1 — Naked-Orphan Root-Cause Fix — DESIGN + PLAN (no code)

**Date:** 2026-07-02 · **Status:** DESIGN for review; code lands in a separate pass.
**Severity:** the most critical fix in the system (unmonitored naked position on live capital).
**Audit source:** `docs/audit/system_security_audit_02jul2026.md` findings A-1 (HIGH) + E-1 (MEDIUM) — the same root, found independently.

---

## 1. VERIFIED ROOT CAUSE (Phase 1 — re-confirmed against current source 02-Jul)

Both a timed-out entry (A-1) and a crashed-mid-place entry (E-1) end the same way: the entry order **is live at the broker**, the local `orders` row was never persisted, so recovery **marks the trade FAILED + releases capital without ever confirming the broker holds no order**; when it fills, `_check2_orphan_adoption` disowns it as a **human order → naked, no SL**.

**Root enabler (confirmed):** the broker `tag` (`truncate_tag_for_broker(trade_id)`) is **written on every order but never read** to correlate a broker order back to a local trade. Grep of `orders/` for `.get("tag")`/`["tag"]` → **zero matches**; `zerodha_adapter.py` has **no `"tag"` string at all** → `get_open_orders()` drops the tag during normalization.

Exact evidence:
- **Timeout entry point** `orders/order_placer.py:1291-1323` — `BrokerTimeoutError` → `update_trade_status(trade_id,"UNKNOWN_IN_FLIGHT")` + push to `_timeout_recovery_queue` + `raise`. (Persist at `~:1357` never runs → no `orders` row, no `broker_order_id`.)
- **Timeout recovery** `orders/order_reconciler.py:3053-3186` (`_check_unknown_in_flight`): builds `broker_orders` from **`self._adapter.get_open_orders()`** (line 3079) keyed by **`broker_order_id`** (3081); correlates by iterating **`get_orders_for_trade(trade_id)`** (empty on timeout) → `found_at_broker` always False → after **3 polls (~45s)** marks **FAILED + releases capital** (3155-3186). **Doubly broken:** (a) keys on a `broker_order_id` we never learned; (b) `get_open_orders()` only returns OPEN orders — a **filled** entry is COMPLETE (a position), not in that list. (Docstring says "get_orders()" but code calls `get_open_orders()`.)
- **Crash entry point** `orders/order_placer.py:1203` (`_engine.execute()` places at broker) → persist `~:1357`. In-process persist *failure* is handled (cancel + `hard_kill`, 1355-1385); a true **crash** (SIGKILL/OOM/reboot) between them runs no code.
- **Crash recovery** `broker/order_monitor.py:336-391` (`_cleanup_orphaned_pending_trades`): on restart, `get_orphaned_pending_trades()` (PENDING trades w/ no `orders` rows) → **marks FAILED + fires `on_orphan` (capital release) with NO broker check whatsoever** (comment even assumes "they cannot be monitored … must be marked FAILED").
- **Disown** `orders/order_reconciler.py:1366-1455` (`_check2_orphan_adoption`): a broker position whose only local record is FAILED → not PENDING/PENDING_FILL → routed here → logged as human/untracked, **not adopted, not protected** (once/day WARNING; NAKED). Unchanged since the audit.
- **Tag mechanics** `core/ids.py:104-124`: `truncate_tag_for_broker(t)=t[:16]`; `trade_id="trd_"+32hex` → tag = `trd_`+**first 12 hex** = 48 random bits.

**No recent fix altered UNKNOWN_IN_FLIGHT / orphan-adoption** (current source == audit description).

---

## 2. LIFECYCLE TRACES (Phase 2)

### (a) TIMEOUT lifecycle (LIVE only — paper never times out; no real network)
1. `place()` reserves capital, `create_trade` → `trades.status=PENDING` (FIX-071 Part A), mints `trade_id`.
2. `_engine.execute()` submits ENTRY to Kite with `tag=truncate(trade_id)`. **Kite receives it**, but the HTTP call times out → `BrokerTimeoutError`.
3. `order_placer` (1291-1323): `status=UNKNOWN_IN_FLIGHT`, add to `_timeout_recovery_queue`, **capital still reserved** (correct), re-raise. (Note: `signal_processor` A-2 currently *retries* this — out of scope here, see A-2 fix.)
4. Reconciler cycle → `_check_unknown_in_flight`: `get_open_orders()` + `get_orders_for_trade(trade_id)` (empty) → not found → poll_count++.
5. After 3 polls (~45s): **`status=FAILED`, capital released** — regardless of the order's real state.
6. The resting ENTRY **fills** → broker position. `get_all_open_trades()` excludes FAILED → CHECK2 sees an orphan position → `_check2_orphan_adoption` → **human/untracked → NAKED, no SL** until the 15:17 EOD sweep flattens it (intraday-bounded, but unprotected all session).
   - Capital-release points: step 5 (`_check_unknown_in_flight` FAILED path). Orphaning point: step 6 (CHECK2 human disown).

### (b) CRASH lifecycle (LIVE dangerous; paper self-corrects)
1-2. As above through `execute()` — **Kite accepts the ENTRY**.
3. Process **crashes** before `_persist_entry_orders` → `trades.status=PENDING`, no `orders` row, no `broker_order_id`.
4. Restart → `order_monitor.rehydrate_from_store` → `_cleanup_orphaned_pending_trades`: **`status=FAILED` + capital released, no broker check** (336-391).
5. The ENTRY fills → orphan position → CHECK2 human disown → **NAKED** (same as timeout step 6).
   - Capital-release point: step 4. Orphaning point: step 5.

### PAPER behaviour (parity oracle)
- Paper `place_order` synthesizes a fill: writes `_paper_fills[fake_broker_id] = {status,filled_qty,avg_price,symbol,side,qty,price,trigger_price}` (`zerodha_adapter.py:1882-1886`) — **no tag stored** — then a daemon fires the synth fill after a delay (ZA16a). **Paper never raises `BrokerTimeoutError`** → the TIMEOUT path is live-only.
- `_paper_fills` is **in-memory** → a paper *crash* loses it → on restart the paper "broker" has no order → recovery correctly finds "no matching order" → FAILED is **correct** (paper has no real position). So E-1's naked danger is a LIVE phenomenon; paper's amnesia is self-correcting.
- **Parity approach:** the recovery calls the *same* adapter query + correlation in both modes; live Kite is the durable oracle, paper's `_paper_fills` is the oracle within a process. Tests exercise the adopt-and-protect path in **paper** by *seeding* `_paper_fills` with a tagged order + an UNKNOWN_IN_FLIGHT/PENDING trade → same code path, no real broker. One path, two oracles.

---

## 3. DESIGN — permanent tag-correlation recovery (Phase 3)

### 3.1 Surface the tag at the broker boundary (adapter)
- **NEW `ZerodhaAdapter.get_all_orders() -> list[dict]`** — ALL of today's orders (not just open). Live: `kite.orders()` unfiltered, normalized to include **`tag`**, `order_id`, `status`, `symbol`, `transaction_type`, `quantity`, `filled_quantity`, `average_price`, `trigger_price`. Paper: return **all** `_paper_fills` entries (any status) with their stored `tag`. (The raw Kite order already carries `tag`; we stop dropping it.)
- **Paper parity:** store `tag` in the `_paper_fills` record at both write sites (`:1882`, `:2033`) so paper's oracle carries it.
- (Optional) add `tag` to `get_open_orders()` too; not required if `get_all_orders()` is used by recovery.
- `get_positions()` stays tag-less (Kite positions have no tag) — correlation is **order-based**, using positions only as a secondary "is a position actually held" confirmation.

### 3.2 One correlation helper (reconciler)
`_correlate_entry_by_tag(trade, all_orders_by_tag) -> MatchResult`:
- Compute `expected = truncate_tag_for_broker(trade_id)`.
- Candidate broker orders = those whose `tag == expected` **AND** whose `transaction_type == entry_side(trade.direction)` (LONG→BUY / SHORT→SELL). *(Entry, SL, TGT all share the tag; filter to the ENTRY leg by side — in the recovery window only the entry exists, but be precise.)*
- **0 candidates** → `ABSENT` (no order at broker for this trade).
- **1 candidate** → `MATCH(order)`.
- **>1 candidate** (tag collision — 48-bit, astronomically rare) → do NOT adopt blindly: fall back to `symbol+side+qty` and a placed-time window; if still ambiguous → `AMBIGUOUS` → CRITICAL alert + leave for manual (never mis-adopt).
- **Tag-uniqueness answer:** `trd_`+12hex = 48 bits. For ~100 trades/day, P(any collision) ≈ 100²/2 / 2⁴⁸ ≈ 1.8e-11 — negligible but *not guaranteed*; the `AMBIGUOUS` branch makes a collision fail safe, not silently wrong. (If ever deemed insufficient, widen truncation toward Kite's real 20-char limit — but 16 is the empirically safe zone; keep it + the fallback.)
- **Human vs system:** a broker order is "ours" only if its tag matches the `trd_…` shape **and** resolves to a real local trade in the recovery set. No tag, or a tag matching no local trade → **genuine human/untracked** → unchanged `_check2_orphan_adoption` human handling (flag, never adopt).

### 3.3 One adoption action (reconciler) — reused by both entry points
`_adopt_or_fail(trade, match_result, *, hard_kill: bool)`:
- `ABSENT` **and broker reachable** and confirmed over the poll budget → **FAILED + release capital** (the only capital-release point; unchanged outcome, now *evidence-based*).
- `ABSENT` **but broker unreachable/poll failed** → **defer** (stay in recovery, no FAILED, no release) — never release on blind absence.
- `MATCH(order)`:
  - **Backfill** the missing `orders` ENTRY row with the matched `broker_order_id` (restores traceability).
  - order **OPEN/TRIGGER PENDING** → register with `order_monitor` so its fill is tracked; exits are placed on fill by the existing deferred-exit path (RAMCOIND L1-L4 / CHECK9 / G5b intact).
  - order **COMPLETE (filled)** → treat as a just-filled entry: set trade OPEN at the **filled qty** (partial-safe), then **place exits via the existing guarded exit protocol** (same path used on a normal fill → circuit-band clamp, tick-snap, SL-first, `_verify_exits_placed`). **If `hard_kill`** → **flatten** instead via the existing reverse-aware, oversell-guarded emergency close (CHECK9 FACET-2 `min(tracked,live_held)`), never adopt into a kill.
  - order **REJECTED/CANCELLED** → FAILED + release (genuinely didn't fill). Safe.
- **Idempotency (one adoption path, no duplicates):** adoption is guarded by the trade's own state transition — once backfilled/registered/EXITING, a re-run sees a non-recovery state and no-ops. The `_adopt_or_fail` transition uses the atomic `WHERE … AND status IN (recovery states)` + rowcount pattern (mirrors `mark_trade_manually_closed`) so only the first cycle acts.

### 3.4 Unify the two feeds into the one path
- The reconciler's recovery processes **both** sources through §3.2/§3.3: (a) `order_placer.get_timeout_recovery_trades()` (UNKNOWN_IN_FLIGHT) and (b) `state_store.get_orphaned_pending_trades()` (crash PENDING-no-rows).
- **`order_monitor._cleanup_orphaned_pending_trades` STOPS marking FAILED directly.** Instead it hands the crashed PENDING trades to the same reconciler recovery (e.g., enqueues them into the recovery set / leaves them for the reconciler prepass), so there is exactly **one** correlation + one capital-release decision. (Startup ordering already runs a synchronous reconcile before the pipeline starts — `main.py:2701` — so the crash trades are correlated before any new trading.)

### 3.5 Invariants preserved (explicit)
- **RAMCOIND L1-L4:** adoption never places exits directly — it calls the existing exit protocol which carries the dedupe/settling-window/oversell/`_verify_exits_placed` layers.
- **CHECK9:** the sl_row partition and FACET-2 oversell guard are reused for the HARD_KILL flatten; adoption of a filled entry places a *single* SL via the same owner.
- **G5b:** crash-recovery SL still applies to OPEN trades; adoption backfills the row so G5b's broker-authoritative + settling-window checks operate normally (no double SL).

---

## 4. IMPLEMENTATION PLAN (Phase 4 — for the coding pass)

**Change order (each step compiles + tests green before the next):**
1. `broker/zerodha_adapter.py` — add `get_all_orders()` (live + paper, incl. `tag`); store `tag` in `_paper_fills` writes. *(pure addition; nothing consumes it yet.)*
2. `orders/order_reconciler.py` — add `_correlate_entry_by_tag` + `_adopt_or_fail` helpers (pure logic; unit-tested in isolation).
3. `orders/order_reconciler.py` — rewrite `_check_unknown_in_flight` to use `get_all_orders` + the helpers; make FAILED evidence-based (absence confirmed + broker reachable).
4. `orders/order_reconciler.py` + `broker/order_monitor.py` — feed crash PENDING-no-rows trades into the same recovery; `_cleanup_orphaned_pending_trades` no longer marks FAILED unilaterally.
5. Wire the recovery prepass into the startup + 15-min reconcile (both already call the reconciler).

**No schema change** (correlation recomputes `truncate(trade_id)` over the small recovery set; `broker_order_id` is backfilled into the existing `orders` row). Rollback = revert the commit → old FAILED-on-timeout/crash behaviour returns.

**Deployment impact:** touches the live order path (adapter + reconciler + order_monitor) → **off-market deploy + restart** (deploy≠restart). Paper unaffected structurally (same code; paper oracle seeded only in tests). Behaviour change is confined to the recovery states (UNKNOWN_IN_FLIGHT / crashed-PENDING) — normal fills/exits untouched.

**TEST MATRIX (fail-on-old / pass-on-fix):**
| # | Scenario | Expected (new) | Old behaviour |
|---|---|---|---|
| 1 | timeout, entry OPEN at broker | adopted + registered (not FAILED) | FAILED + released |
| 2 | timeout, entry FILLED (COMPLETE) | adopted + exits placed (protected) | FAILED → naked orphan |
| 3 | crash, entry FILLED, restart | adopted + protected | FAILED → naked |
| 4 | timeout/crash, entry never reached broker | FAILED + released **after** absence confirmed | FAILED (unconfirmed) |
| 5 | genuine human order (no/again-tag) | CHECK2 human-flag, NOT adopted | (same) — must stay |
| 6 | HARD_KILL + matched filled entry | flatten (reverse-aware, oversell-guarded) | naked |
| 7 | two cycles / monitor+reconciler both see it | adopted exactly once (idempotent) | n/a |
| 8 | broker poll fails / unreachable | stay in recovery; no FAILED, no release | FAILED + released (crash path) |
| 9 | partial fill of entry | adopt at filled qty; exits at filled qty | FAILED → naked partial |
| 10 | tag collision (>1 match) | no blind adopt → fallback / CRITICAL manual | n/a |
| 11 | **paper parity**: seeded `_paper_fills` tagged order + UNKNOWN_IN_FLIGHT | same correlation adopts (one path) | n/a |
| 12 | RAMCOIND/CHECK9/G5b intact | single SL, no oversell, no double-exit | (regression guard) |

**Edge cases:** trade already terminal when a late broker tag appears → idempotent no-op; entry + resting SL/TGT sharing the tag → filter to the entry leg by side; broker order tag present but qty ≠ trade qty → treat as partial/ambiguous per §3.2; adoption during the 15:17 EOD window → EOD sweep + adoption must not double-act (adoption sets a managed state the sweep respects).

---

## 5. OBJECTIVES CHECK
- ✅ Never classify a system order as human → tag-shape + local-trade resolution gates adoption; human path only when no match.
- ✅ Never release capital before broker-absence confirmed → FAILED only on confirmed `ABSENT` + broker reachable; unreachable → defer.
- ✅ Never leave a filled position unmanaged → MATCH+COMPLETE → place exits (or flatten under kill).
- ✅ No duplicate adoption → single reconciler path + atomic state-guarded transition; order_monitor defers to it.

*DESIGN ONLY — no code changed this pass. Implementation follows after review.*
