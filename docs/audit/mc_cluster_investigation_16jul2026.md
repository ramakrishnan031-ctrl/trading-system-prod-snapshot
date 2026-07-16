# Capital-safety cluster (M-C4 / C5 / C6 / C8) — read-only investigation — 16-Jul-2026

**Scope:** verify each item's STATUS at HEAD (`a658ba7`) — runtime beats register text — with
location · issue · failure scenario · candidate fix direction · regression surface. **FIXED NOTHING**
(record only). Source of the original findings: `docs/audit/full_system_audit_04july2026.md:116-120`.

**Headline:** two are **OPEN + reachable** (M-C4, M-C8); two are **not currently reachable** — M-C5 is
**mitigated** by the caller's atomic gate, M-C6 is **latent** behind the allocator's weight clamp. The
register's flat "OPEN" is stale on reachability for C5/C6.

---

## M-C4 — KillSwitch lock held THROUGH the Telegram send — **OPEN (confirmed at HEAD)**
- **Location:** `capital/kill_switch.py` — `record_api_failure` `:649-663` acquires `self._lock` (RLock)
  and calls `self.soft_kill(...)` at `:657` **inside** that block; `soft_kill` then runs `_publish_event`
  (`:483`) + `_notifier.send(...)` (`:495-503`) — but those run while `record_api_failure`'s **outer**
  RLock acquisition is still held (soft_kill's own `with self._lock` at `:456` only releases the reentrant
  inner acquisition at `:480`; the outer is held until `:663`).
- **Issue:** the DIRECT `soft_kill()`/`hard_kill()` paths deliberately publish + send OUTSIDE the lock
  (documented, `:482`) — but the **AUTO-TRIP path** (record_api_failure → soft_kill within the lock)
  defeats that: the kill-switch lock is held across a bus publish (could block on a slow subscriber) AND
  a Telegram network send (up to the notifier timeout).
- **Failure scenario:** 3 consecutive transient API failures (BrokerTimeout/RateLimit) → auto-trip
  soft_kill. During its publish+send, ANY thread calling `is_active(intent)` (`:402`) or `current_state()`
  (`:412`) — the **last-mile order gate, checked on every entry/exit** — blocks until the send completes.
  This fires exactly during a broker-connectivity wobble, when the trading path is already fragile.
  Bounded by the notifier's send timeout, not unbounded; impact = added latency/stall on is_active across
  threads, not a permanent freeze.
- **Fix direction:** in `record_api_failure`, decide the trip INSIDE the lock (set a local flag), then call
  `soft_kill(...)` AFTER exiting `with self._lock` — mirroring the mutate-inside / publish-outside pattern
  soft_kill/hard_kill already use. (The RLock KS4-reentrancy exists FOR this path; after the fix it's
  no longer needed here but stays harmless.)
- **Regression surface:** `record_api_failure` auto-trip logic; RLock reentrancy (KS4); callers that report
  API failures (broker/order paths); tests `test_kill_switch.py` (auto-trip / `api_cascade` `:331`).
- **Priority: MEDIUM** (real, but a bounded stall on a rare trigger).

## M-C5 — commit_adopted_entry race → hard_kill — **PARTIAL / mitigated (race NOT reachable at HEAD)**
- **Location:** `capital/fund_manager.py` `commit_adopted_entry` `:1029-1053` — the exactly-one-commit guard
  (`_commit_exists`, `:1035`) runs INSIDE `self._lock`, but `commit_to_used(rid, ...)` (the actual commit
  that writes the COMMIT ledger row + can fire BL-4 hard_kill) runs OUTSIDE the lock at `:1053`. So the
  guard-then-act is not self-contained atomic — as the audit flagged.
- **Why NOT reachable:** both production callers gate on an **atomic trade-state transition BEFORE** calling
  it, so only the winner ever calls commit_adopted_entry per trade:
  `order_reconciler.py:3603` `adopt_recovery_trade_to_open(...)` (→ OPEN) and `:3660`
  `mark_recovery_trade_exiting(...)` (→ EXITING) — both "if not …: return None". A second cycle sees a
  non-recovery state and no-ops. The two paths transition FROM the same recovery-state so they're mutually
  exclusive at the DB. No other prod caller exists (grep: only `:3614` + `:3668`).
- **Failure scenario (were the caller gate absent):** two concurrent recovery cycles both pass
  `_commit_exists` (neither has written the COMMIT row yet), both call commit_to_used; the second finds the
  reservation already popped → unknown-reservation `ValueError` → BL-4 `hard_kill` (full halt) for a benign
  duplicate. **Not reachable today** because the caller gate admits only one.
- **Fix direction (harden the method itself, low urgency):** make the `_commit_exists` guard + the commit
  atomic (do the existence check + `commit_to_used` inside one transaction / one lock hold), OR keep the
  caller-gate dependency but ADD a regression test that locks it in (a second commit_adopted_entry is a
  no-op — `test_a1e1_recovery_matrix.py:152` already asserts this at the method level).
- **Regression surface:** `commit_adopted_entry` + `_commit_exists`/`_restore_reserve_from_ledger`/
  `commit_to_used`/BL-4; the reconciler recovery paths; `test_a1e1_recovery_matrix.py`.
- **Priority: LOW** (not reachable; the exploitable spurious-hard_kill path is closed by the atomic caller gate).

## M-C6 — zero multiplier floored to 1 lot — **OPEN but LATENT (not reachable at HEAD)**
- **Location:** `capital/position_sizer.py:446` — `tiered_qty = max(1, min(tiered_qty, raw_qty * 2))` (in
  the tier-multiplier-ON path). `:443` `effective_mult = tier_mult * max(0.0, perf_weight)`; `:444`
  `tiered_qty = floor(raw_qty * effective_mult)`. When `effective_mult == 0` → tiered_qty 0 → `max(1, 0) = 1`.
- **Issue:** a **zero size multiplier** (intent: trade nothing) is floored to **1 lot** — the sizer's implicit
  contract "multiplier 0 ⇒ skip" is not honoured. The OFF_FLAT path (`:455`) correctly does NOT floor
  ("a flat below 1 lot → BELOW_MIN skip"); only the ON path floors.
- **Why NOT reachable today:** the only zero-multiplier source is `perf_weight = 0`, but
  `capital/performance_allocator.py` clamps EVERY weight to `min_weight = 0.5` (PA3/PA8; `:46`, `:102`,
  `:114`). So perf_weight ≥ 0.5 and effective_mult ≥ 0.25 > 0 — never exactly 0. (Exactly the audit's
  "Latent — allocator clamps min_weight 0.5".) The floor-at-1 (FIX-133 Item 21) still fires for a small
  POSITIVE multiplier that rounds below 1 lot — arguably intended (don't lose a valid HIGH-tier trade to
  rounding); that is a separate, defensible behaviour.
- **Reachable IF:** `min_weight` is ever lowered toward 0, OR a `perf_weight = 0` (or negative → clamped to
  0 at `:443`) reaches the sizer via a new path (e.g., a "disable this strategy's sizing" feature), OR a
  direct caller passes 0. Then a strategy meant to trade nothing silently trades 1 lot (capital vs intent).
- **Fix direction:** split the two cases — `if effective_mult <= 0: tiered_qty = 0` (→ BELOW_MIN skip);
  `else: tiered_qty = max(1, min(tiered_qty, raw_qty * 2))`. Preserves the FIX-133 floor for positive
  multipliers; honours "0 ⇒ skip".
- **Regression surface:** `position_sizer` sizing math; `test_position_sizer.py`,
  `test_fix133_dynamic_sizing.py`, `test_diary4_tier_multiplier.py`; the allocator's weight range invariant.
- **Priority: LOW** (latent; make it a hard PREREQUISITE if `min_weight` is ever reduced or a zero-weight path added).

## M-C8 — hard_kill retry starves the fill thread — **OPEN (confirmed at HEAD)**
- **Location:** `capital/kill_switch.py:550` `hard_kill` → `_run_cancel()` (synchronous) →
  `_exit_all_trades_indestructible` retry loop `:1204-1290` with `time.sleep(5/15/45)` bounded by
  `_HARD_KILL_MAX_RETRY_HOURS = 2.0` (`:1201`). The whole loop runs on the **caller's thread**.
- **Issue:** `hard_kill()` blocks its caller synchronously for up to **2 hours** (if a trade won't exit —
  broker down, illiquid, IP-403). Several callers are on the **fill / capital-commit path**:
  `orders/order_placer.py:1499` + `:3750` (fill/exit handlers), `capital/fund_manager.py:974` + `:2259`
  (BL-4 on the commit path), `capital/drift_handler.py:231`, `main.py:647`. (FIX-180's 2h bound stops an
  INFINITE freeze but not a long one.)
- **Failure scenario:** a capital-invariant violation or an exit failure on the fill/commit thread trips
  hard_kill → that thread enters the retry loop and is frozen up to 2h → the fill/event pipeline (order
  status updates, reconciler, other symbols' exit bookkeeping) is starved for the duration of the emergency.
  The flatten itself proceeds (the loop re-reads broker positions directly), but the thread that would
  process fills/events is stuck.
- **Fix direction:** trip the kill STATE synchronously (block new orders immediately — cheap, inside the
  brief lock) but run the exit-retry loop on a **dedicated worker thread**, so the fill/reconciler caller
  returns at once. hard_kill returns immediately; the CancellationReport becomes async (a future / a
  status the caller polls) — this is the non-trivial part.
- **Regression surface:** `hard_kill`'s synchronous `CancellationReport` contract (callers + all
  `test_kill_switch.py` hard_kill tests expect it synchronously); emergency-exit ordering + idempotency; the
  FIX-180 retry-bound + FIX-181/FIX-190 sweep semantics must be preserved; a re-entrant second hard_kill()
  (idempotent, `:535`) also re-runs the loop synchronously (part of the same fix).
- **Priority: MEDIUM-HIGH** (safety-adjacent; the longest starvation, and on the most critical thread).

---

## §3 — other capital/kill observations (recorded, not fixed)
- **(M-C4 completeness)** the same auto-trip lock also spans `_publish_event` → `bus.publish` (`:793`) — a
  slow/blocking KillSwitchActivated subscriber stalls the lock too, not only Telegram. The fix (call
  soft_kill outside the lock) covers both.
- **(M-C8 completeness)** an already-HARD_KILL re-invocation (`:535`, do_publish=False) still runs the full
  synchronous `_run_cancel` retry loop — same starvation; the async-worker fix must cover the re-entrant call.
- `_count_open_positions` (`:315`) fail-safes to `1` on a DB error (won't auto-clear when it can't count) —
  correct, noted for completeness. No new critical issue found beyond the four.

## Verdict table
| Item | STATUS | Reachable now? | Location | Priority |
|---|---|---|---|---|
| M-C4 | OPEN | **YES** (auto-trip path) | kill_switch.py:649-663 / 495 | MEDIUM |
| M-C5 | PARTIAL / mitigated | **NO** (atomic caller gate) | fund_manager.py:1029-1053 · reconciler:3603/3660 | LOW |
| M-C6 | OPEN / latent | **NO** (allocator min_weight 0.5) | position_sizer.py:446 | LOW |
| M-C8 | OPEN | **YES** (fill/commit-thread callers) | kill_switch.py:550 / 1204-1290 | MED-HIGH |

**Read-only — changed NO code/config/schema/DB. Web Claude designs the fixes next (M-C8 + M-C4 first, as
the reachable ones), each design → ChatGPT red-team → implement (parity paper+live, one fix per commit,
off-market deploy).**
