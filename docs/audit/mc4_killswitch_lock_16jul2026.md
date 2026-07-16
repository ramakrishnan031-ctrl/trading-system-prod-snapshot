# M-C4 — release the kill-switch lock before the auto-trip publish + send — 16-Jul-2026

**Status:** IMPLEMENTED + TESTED (local). **NOT pushed. NOT deployed.** One isolated commit
`6c77525` on branch `mc4-killswitch-lock-16jul` (off `main`@`f68d15d`). Deploy off-market (§6).
**Lock scope only — no behaviour change.** M-C8 / M-C5 / M-C6 untouched (separate cycles).

**Root cause (confirmed):** `capital/kill_switch.py` `record_api_failure` (`:649-663`) held `self._lock`
(RLock) and called `soft_kill(...)` from INSIDE that `with` block. `soft_kill` releases only its OWN
reentrant acquisition (`:456→480`), never the caller's — so the outer lock stayed held across
`soft_kill`'s `bus.publish` (`:483`, a slow subscriber) AND its Telegram send (`:495`, network I/O).
`is_active()` (`:402`) / `current_state()` (`:412`) — the last-mile order gate checked on **every**
entry/exit from every thread — blocked for the duration of that I/O, exactly during a broker wobble
(the auto-trip only fires on 3 consecutive BrokerTimeout/RateLimit failures). **Measured pre-fix:
concurrent gate calls blocked 20.02s.**

---

## STEP 1 — verify, don't assume (both findings clear → no guard needed)

### 2a — is `soft_kill` idempotent? **YES → no extra guard added**
`kill_switch.py:456-468`: the check runs **inside** the lock —
- `:457-461` already `SOFT_KILL` → DEBUG log + `return`: **no republish, no renotify, no re-mutation**.
- `:462-468` already `HARD_KILL` → WARNING + `return` (cannot downgrade).

⇒ The release-then-call double-trip window is **closed by soft_kill itself**: if two threads both
compute `should_trip=True` and both call `soft_kill`, the first mutates to SOFT_KILL and the second
returns at `:461` — exactly ONE publish + send. Per the instruction's 2a branch, **no "trip already
initiated" flag was added** (and no second locking mechanism).

### 2b — state-before-send + no-rollback? **YES → no guard needed**
- State is mutated at `:477-480` **inside** the lock, **before** the publish (`:483`) and send (`:495`).
- `_publish_event` (`:783-806`) wraps `bus.publish` in try/except → logs ERROR, never raises.
- The send is wrapped at `:493-505` → logs ERROR on failure.

⇒ **Activation never depends on notification success**; a publish/send exception cannot roll back or
delay the kill state. (Persist-first `:474` aborts on a *DB* failure — that is KS9 atomicity, unrelated
to notify.) The new test also asserts `is_active()` is already True *while* the send is blocked.

---

## STEP 2 — the change (lock scope only)
`record_api_failure`:
```
with self._lock:
    self._api_failure_count += 1
    should_trip  = auto_trip and count >= threshold and state == INACTIVE
    trip_reason  = f"Auto-trip: {count} consecutive API failures (threshold={threshold})" if should_trip else ""
# lock RELEASED
if should_trip:
    self.soft_kill(reason=trip_reason, triggered_by="auto_trip")
```
- Counting + the threshold decision stay **in-lock**; the reason is built **in-lock** so it reports the
  count at the moment of decision — **byte-identical message** to the pre-fix code.
- The whole `soft_kill` call is now outside, so **both** the `bus.publish` and the Telegram send are.
- Public interfaces unchanged. RLock **retained** (the stale class-docstring claim that RLock exists for
  the now-removed `record_api_failure → soft_kill` reentrancy was corrected).
- **Parity:** `kill_switch` is one shared path (paper == live); no mode-branch introduced.
- No schema change.

## STEP 3 — strengthened regression test (RED-on-old / GREEN-on-new, proven)
`tests/unit/test_kill_switch.py::test_mc4_autotrip_does_not_hold_the_lock_across_publish_and_send`
— BOTH a subscriber whose publish handler blocks on an event AND a notifier whose send blocks on an
event. Two phases: the trip thread is blocked **first inside `bus.publish`**, then **inside
`notifier.send`**; at *each* point it asserts the concurrent
`is_active("entry")` / `current_state()` / a **second** `record_api_failure()` all return in **<2s**
with the kill already active + state consistent, and finally that the trip completes as SOFT_KILL.
- **GREEN on new:** `test_kill_switch.py` → **48 passed**.
- **RED on old** (fix stashed, test kept): FAILS —
  `publish: kill-switch lock held across the blocked I/O — concurrent is_active/current_state/
  record_api_failure blocked 20.02s (M-C4)`.

## STEP 4 — regression
- **Regression map (callers) — 318 passed:** `signal_processor` (17 `record_api_failure` sites: 897,
  1081, 1094, 1214, 1226, 1270, 1831, 1860, 1873, 1971, 1979, 2146, 2168, 2178, 2240, 2248),
  `order_reconciler` (658, 2264), `eod_squareoff` (349), `cnc_gtt_monitor` (584), `p0_live_day1`,
  `h4_killswitch_retry_rederive`, `kill_switch`.
- **FULL suite: 4700 passed / 11 failed / 15 skipped — ZERO new failures.** 10 are the known PC-env set;
  the **11th** (`test_interactive_startup.py::test_holiday_guard_missing_yaml_proceeds`) is a
  **PRE-EXISTING, TIME-GATED env flake**, proven two ways:
  1. it fails **identically on the pre-fix base** (M-C4 stashed) — *"Outside service window
     [08:00-16:00 IST]; current IST 16:19 … assert 0 == 5"*;
  2. it **passes** with `TS_IGNORE_MARKET_WINDOW=1` (bypassing the FIX-189 clock guard).
  ⇒ It is the **FIX-189 startup guard** (`main()` exits 0 outside 08:00–16:00 IST), not a code defect.
  It was invisible in the earlier baseline only because that run finished ~15:30 (inside the window);
  this one finished 16:19. **The known-PC-env set is time-of-day dependent — 10 in-window, 11 after
  16:00 IST.** (Worth folding into the PC-test-env hygiene note.)

## §6 — off-market deploy (NOT executed here)
`mc4-killswitch-lock-16jul` is its OWN branch off `main`@`f68d15d`. At deploy time, EITHER fold it into
the pending F1 + alert-watcher deploy (**re-consolidate + re-run the combined regression**) OR land it as
its own increment AFTER that deploy. **Do NOT disturb the already-validated F1+alert-watcher
consolidation (tag `deploy-16jul-alertwatcher-f1` → `1d5337d`) just to add this.** Decide at deploy time.
**Rollback:** revert `6c77525` — record_api_failure returns to calling soft_kill under the lock
(restoring the stall). No schema/data/interface change.

**Done = 2a/2b verified (no guard needed) · lock released before the trip · the two-phase concurrency
test passes (RED-on-old 20.02s) · callers 318 pass · full suite zero-new · one isolated commit + report ·
NOTHING pushed.** Remaining open: **M-C8** (sync→async hard_kill retry — next), M-C5 (mitigated),
M-C6 (latent).
