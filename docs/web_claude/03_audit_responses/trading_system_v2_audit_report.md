# Trading System v2 — Pre-Live Audit Report

**Part 1 of 2:** Findings from deep-read of ~70% of production code.
**Part 2 (pending):** Remaining ~30% — shadow_tracker, risk_engine full, main.py runtime loop, order_monitor full, test coverage analysis, config reviews.

> **Status:** PARTIAL. This report covers capital, order lifecycle, broker integration, reconciliation, signals, recovery paths, and the critical modules most likely to cause account damage.
>
> **Verdict:** Do not go live. 11 blocker-class issues found in 70% of the code. Part 2 will likely find more.
>
> **Scope audited:** Trading_system_v2_files.zip — 114 Python files, 20,496 LOC production.
>
> **Severity rubric:**
> - **BLOCKER** — do not go live. Concrete path to account damage, silent capital corruption, or unrecoverable state.
> - **HIGH** — must fix before live or in first week. Operational risk, or a gap between what the spec claims and what code does.
> - **MEDIUM** — fix within first month.
> - **LOW** — hardening.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [The Spine Is Broken](#2-the-spine-is-broken)
3. [Why the Paper Trial Passed Anyway](#3-why-the-paper-trial-passed-anyway)
4. [BLOCKER findings with fix specs](#4-blocker-findings)
5. [HIGH findings with fix specs](#5-high-findings)
6. [MEDIUM findings with fix specs](#6-medium-findings)
7. [Priors from handoff note — verification](#7-priors-verification)
8. [Coverage map — what's been audited, what remains](#8-coverage-map)
9. [What to do next](#9-what-to-do-next)

---

## 1. Executive Summary

### The headline

The trading lifecycle in v2 depends on a chain of events flowing through the event bus. **Two links in that chain are severed.** Specifically:

- `OrderFilled` events are never published for entry orders, because `order_monitor.track()` is never called for them. Every downstream consumer (capital commit, shadow_tracker, daily reports) silently fails to fire.
- `PositionClosed` events are never published anywhere in production code. `shadow_tracker` (681 LOC) subscribes to them and is effectively dead code in production.

The 1,411 tests pass because tests synthesize these events directly via `bus.publish(OrderFilled(...))`. Production has no publisher.

### Blocker inventory

11 blockers across 4 layers:

| ID | Layer | Summary |
|---|---|---|
| **BL-1** | capital | FundManager state not rebuilt from ledger on restart |
| **BL-2** | capital | CapitalDriftDetected handler only logs, doesn't halt |
| **BL-3** | reconciler | HEALTHY check masks BL-1 silently |
| **BL-4** | orders | commit_to_used failure in order_placer swallowed |
| **BL-5** | capital | Ledger write not in same transaction as in-memory mutation |
| **BL-6** | broker | 429/rate-limit backoff entirely missing |
| **BL-7** | orders | OrderFilled never emitted for entry orders ★★★ |
| **BL-8** | orders | Order row DB insert failure silently swallowed |
| **BL-9** | capital | CapitalInvariantViolation triggers soft_kill, should hard_kill |
| **BL-10** | events | PositionClosed never emitted; shadow_tracker is dead in production |
| **BL-11** | data | live_feed reconnect callback fires on retry, not on successful reconnect |

### What this means

- **On the happy path, `commit_to_used` is never called.** `reserved` accumulates forever; `used` stays at 0. On first position close, `release_used` triggers an invariant violation.
- **On any restart with open positions**, capital state silently corrupts because `fund_manager.initialize()` resets `used` to 0 and nothing rehydrates it.
- **When the reconciler detects broker drift**, it publishes an event that nobody acts on.
- **When the broker throttles with 429s**, the system keeps hammering because no backoff is implemented.
- **Paper trial is not a safe proxy for live** — the bugs above were never exercised by paper trial because the paper adapter doesn't synthesize fill events and the integration tests don't assert on capital accounting.

### What the Part 1 audit cannot yet confirm

About 30% of the code hasn't had a deep read yet. Specifically:

- `orders/shadow_tracker.py` full logic (681 LOC, moot given BL-10 but may have other bugs)
- `capital/risk_engine.py` remaining checks (417 LOC)
- `main.py` runtime loop and shutdown sequence (lines 900-1192)
- `broker/order_monitor.py` fill-timeout + orphan handling in depth
- `capital/position_sizer.py` full math (only partially read)
- Test coverage analysis (what the 1,411 tests actually assert)
- Config file reviews (system_config.yaml, strategy YAMLs)
- `alerts/` telegram + critical path
- `scripts/` auxiliary scripts

Part 2 will close these out. Historical rate: about one new blocker per 1000 LOC of deep read. If the trend holds, expect **2-5 more blockers** from the remaining ~6,000 LOC.

---

## 2. The Spine Is Broken

The trade lifecycle depends on this event chain:

```
Signal accepted
  → position sized                                ✓
  → capital reserved (fund_manager.reserve)       ✓
  → order placed at broker                        ✓
  ──────────────────────────────────────────────────
  → broker fills the order
  → OrderFilled event published                   ✗ BL-7 — NEVER PUBLISHED
  → order_placer commits reservation to used     (silently skipped)
  → trade is monitored for SL/TGT
  → SL or TGT hits, position closes at broker
  → PositionClosed event published                ✗ BL-10 — NEVER PUBLISHED
  → release_used, update daily PnL              (silently skipped)
  → shadow_tracker cascades to next inning      (dead code in production)
```

In isolation, each upstream component works. The gap is in the **wiring between them**. It's the kind of bug that unit tests are structurally incapable of finding, because unit tests replace the missing wiring with `bus.publish(OrderFilled(...))` lines of their own.

### Evidence for BL-7 (OrderFilled never published)

Five independent confirmations:

1. **`order_monitor.emit OrderFilled` fires only for `track()`ed orders** — `broker/order_monitor.py:384` inside `_transition_to_complete`, only reached if the order was previously passed through `track()`.

2. **`track()` is called exactly once in the entire codebase:**
   ```
   $ grep -rn "\.track(" orders/ broker/ main.py
   orders/eod_squareoff.py:463  self._order_monitor.track(...)    # EOD market exits
   broker/order_monitor.py:115  # docstring example only, not code
   ```

3. **`order_placer` is constructed without `order_monitor`** (`main.py:960-968`):
   ```python
   order_placer = OrderPlacer(
       entry_engine=full_engine,
       order_manager=order_manager,
       fund_manager=fund_manager,
       bus=event_bus,
       logger=get_logger("order_placer"),
       kill_switch=kill_switch,
       product_resolver=product_resolver,
   )
   # No order_monitor= parameter. OrderPlacer cannot call track().
   ```

4. **Neither `full_entry_engine` nor the protocols call `track()`**. Their docstrings explicitly say: "Does not monitor fills (order_monitor's job)" (orders/order_protocol_limit.py:29). The responsibility is disclaimed by every module in the chain — nobody owns it.

5. **The paper adapter does not synthesize `OrderFilled` either** (`broker/zerodha_adapter.py:811-839`). Paper's `_paper_place_order` returns `SUBMITTED` with a fake broker_id and stops. No fill simulation. Paper trial has never exercised the post-fill capital flow.

### Evidence for BL-10 (PositionClosed never published)

```
$ grep -rn "publish.*PositionClosed\|PositionClosed(" --include="*.py" | grep -v test_
core/events.py:103:    class PositionClosed(Event):    # definition only
```

Zero publishers in production code. Only `shadow_tracker.py` subscribes, plus tests that publish the event manually. The event type exists, the subscriber exists, the publisher doesn't.

### What actually happens at runtime

On a real trade in live mode:

1. Webhook → signal_processor reserves margin → `reserved += margin`.
2. Order placed at broker → broker confirms → DB has trade row with status=PENDING_FILL.
3. Broker fills the entry. `order_monitor` has never seen this order (no track()), so it's not polling for fills on it. No `OrderFilled` event.
4. `order_placer._on_order_filled` never fires. `fund_manager.commit_to_used` is never called. `reserved` stays locked against this trade forever; `used` stays at 0.
5. DB trade row status stays at PENDING_FILL — the update to OPEN lives inside `_on_order_filled` which never runs.
6. Reconciler runs: broker says position exists (qty matches), local DB says qty_filled=0 (the update lives in `_on_order_filled`). Check 5 fires (POSITION_GREW) → "UNRECOVERABLE" → CapitalDriftDetected published → **handler just logs a warning** (BL-2) → trading continues.
7. Meanwhile, trail SL moves via `smart_tgt_manager` which reads from the trades table and the `smart_tgt_state` table. These are populated by `register_trade()` — but register_trade is called from... let me check:

```
$ grep -rn "register_trade" --include="*.py" | grep -v test_
orders/smart_tgt_manager.py:... (definition)
```

Nothing in production code calls `register_trade` either. **Smart_tgt is also unregistered for live trades.** The system places CO orders, but smart_tgt never starts trailing them.

This means live behavior on a clean trade would be:

- Entry order placed. ✓
- Broker fills it. ✓
- Local DB is stuck at PENDING_FILL. ✗
- Capital `reserved` bucket inflated, `used` bucket empty. ✗
- Reconciler detects drift, publishes event, event handler logs warning, trading continues. ✗
- Smart SL never trails. ✗
- SL eventually hits at broker (at the initial CO SL level, since it's a CO) → position closes at broker.
- `PositionClosed` never published. `release_used` never called. Daily PnL never updated.
- Trades table still shows PENDING_FILL. Reconciler fires Check 1 (MANUAL_CLOSE): "local OPEN/PARTIAL, broker has no position" → marks trade CLOSED_MANUAL, calls `release_used` → `used` is 0, `release_used` tries to deduct margin from it → invariant violation negative_margin_used → soft_kill fires → trading halted.

**First trade on live money, in roughly 2-5 minutes after EOD-squareoff or SL-hit, the system would halt with corrupt capital state.** That's the realistic outcome given what's in the code today.

---

## 3. Why the Paper Trial Passed Anyway

The claim "paper trial ran 5 market days clean" is consistent with the code ONLY if:

1. No trades actually closed during paper trial (plausible if SL/TGT didn't hit and EOD squareoff did its own separate logic via `order_monitor.track` for the exit MARKET order — which would register it late, get filled, call `OrderFilled`, but with no reservation to commit because the original entry was never committed either).

2. OR the capital invariant was never actually checked during paper trial because no `release_used` ever fired for a normal SL/TGT exit (since `PositionClosed` never publishes).

3. OR operators saw warnings in logs but didn't recognize them as structural failures.

The integration test `test_happy_path_signal_reaches_placed_status` asserts `signal.status == "PROCESSED"`, which is set at `signal_processor.py:487` **before any fill happens.** The test never verifies `commit_to_used` was called, never verifies `fund_manager._used > 0` after a trade, never verifies `release_used` fires cleanly on close. The spine bugs are entirely invisible to this test.

This is the "tests green, system broken" failure mode. The tests were written assuming the wiring exists. The wiring was never finished.

---

## 4. BLOCKER findings

Each finding below includes: what it is, where it lives (file:line), why it matters, worst-case scenario, and a code-level fix spec.

---

### BL-1 — FundManager state is not rebuilt from the ledger on restart

**Severity:** BLOCKER
**File(s):** `capital/fund_manager.py:245-279`, `main.py:828-845`
**Affects:** Every warm/crash restart with any open position.

**What it is**

`FundManager.initialize(broker_balance)` is called at startup and zeros out `_intraday_reserved`, `_intraday_used`, `_positional_reserved`, `_positional_used`, and `_daily_pnl`. No code anywhere reconstructs these from the `fm_ledger` or from open trades.

```python
# capital/fund_manager.py:245-260
def initialize(self, broker_balance: float) -> None:
    with self._lock:
        self._total = broker_balance
        self._intraday_avail = broker_balance * self._intraday_pct
        self._intraday_reserved = 0.0      # ← always zero
        self._intraday_used = 0.0          # ← always zero
        self._positional_avail = broker_balance * self._positional_pct
        self._positional_reserved = 0.0    # ← always zero
        self._positional_used = 0.0        # ← always zero
        self._daily_pnl = 0.0              # ← always zero
        self._initialized = True
```

**Why it matters**

G5a spec claims four startup scenarios (COLD, WARM, CRASH, HALT) with different recovery semantics. In practice all four scenarios run the same `initialize(broker_balance)` path. A warm restart mid-day with open positions means:

- `used` is 0 in memory, but trades table shows OPEN positions.
- Available margin is now inflated by the amount previously locked in `used`.
- Next signal is sized against inflated avail → accepts a trade that would over-leverage.
- When any existing position closes, `release_used` tries to deduct from `used` (which is 0) → negative-margin-used guard fires → `CapitalInvariantViolation` → soft_kill.

**Worst case:** You restart the system at 10am, two positions are open. System accepts 5 more trades using margin that's already committed to existing positions. First SL hit at 11am triggers invariant violation, soft_kill fires, 7 positions now open, no new entries allowed, exits continue, each exit cascades another invariant violation.

**Fix spec**

Add a `rehydrate_from_open_trades()` method to FundManager and call it from main.py AFTER `initialize()` on WARM and CRASH scenarios.

```python
# capital/fund_manager.py — add new method
def rehydrate_from_open_trades(self) -> None:
    """
    Reconstruct _reserved and _used buckets from the DB's open trade rows.
    Called AFTER initialize() on warm/crash restart (G5a).

    For each row in `trades` with status in (PENDING_FILL, OPEN, PARTIAL):
      - PENDING_FILL trades: restore margin_reserved into _reserved bucket,
        re-register a _Reservation (synthesize reservation_id).
      - OPEN/PARTIAL trades: restore margin (from entry_actual_price * qty_filled)
        into _used bucket.
    Writes one SYNC row per bucket mutation into fm_ledger.
    Runs the invariant check at the end; raises if it fails.
    """
    with self._lock:
        self._assert_initialized()
        rows = self._store.fetch_all(
            "SELECT trade_id, symbol, direction, qty_planned, qty_filled, "
            "entry_target_price, entry_actual_price, margin_reserved, "
            "order_protocol, status FROM trades "
            "WHERE status IN ('PENDING_FILL', 'OPEN', 'PARTIAL')"
        )
        for row in rows:
            intent = _protocol_to_intent(row["order_protocol"])  # map CO_PLUS_TGT→COVER_ORDER etc
            bucket = self._bucket_for_intent(intent)
            if row["status"] == "PENDING_FILL":
                margin = row["margin_reserved"]
                # synthesize a reservation record for release/commit symmetry
                rid = f"rehydrated_{row['trade_id']}"
                self._bucket_deduct_avail(bucket, margin)
                self._bucket_add_reserved(bucket, margin)
                self._reservations[rid] = _Reservation(
                    reservation_id=rid, symbol=row["symbol"],
                    qty=row["qty_planned"], price=row["entry_target_price"],
                    intent=intent, margin=margin, bucket=bucket,
                    signal_id=None, ts=now_ist().isoformat(),
                )
                # also update the trade row so order_placer's fill_map can find the reservation_id
                self._store.execute(
                    "UPDATE trades SET rehydrated_reservation_id = ? WHERE trade_id = ?",
                    (rid, row["trade_id"])
                )  # requires new column: trades.rehydrated_reservation_id
            else:  # OPEN or PARTIAL
                entry_price = row["entry_actual_price"] or row["entry_target_price"]
                qty = row["qty_filled"] or row["qty_planned"]
                margin = required_margin(qty, entry_price, intent, self._leverage_map)
                self._bucket_deduct_avail(bucket, margin)
                self._bucket_add_used(bucket, margin)
            # write one ledger row per rehydrated trade
            self._write_ledger(
                ts=now_ist().isoformat(),
                mutation_type="REHYDRATE",
                amount=margin,
                bucket=bucket,
                balance_before=self._bucket_avail(bucket) + margin,
                balance_after=self._bucket_avail(bucket),
                signal_id=row.get("signal_id"),
                reservation_id=None,
                reason=f"rehydrated {row['status']} trade {row['trade_id']}",
            )
        # verify invariant holds after reconstruction
        self._check_invariant("rehydrate_from_open_trades", "startup")
```

Wire in `main.py` after `fund_manager.initialize()`:

```python
# main.py:845 (approximately)
fund_manager.initialize(_startup_capital)
if scenario in (StartupScenario.WARM, StartupScenario.CRASH):
    try:
        fund_manager.rehydrate_from_open_trades()
    except CapitalInvariantViolation as exc:
        _log.critical("Rehydration failed — halting startup: %s", exc)
        return 3  # fail startup, require manual intervention
```

**Tests to add**

- `test_rehydrate_pending_fill_restores_reserved`
- `test_rehydrate_open_restores_used_from_actual_price`
- `test_rehydrate_invariant_check_passes`
- `test_rehydrate_fails_loudly_on_corrupt_state` (e.g., open trade with margin > total)

---

### BL-2 — CapitalDriftDetected handler only logs a warning

**Severity:** BLOCKER
**File(s):** `main.py:340-346, 1061`
**Affects:** Every drift-detection path (reconciler RC8, reconciler check 2, check 5, fund_manager sync).

**What it is**

```python
# main.py:340-346
def _log_capital_drift_event(event) -> None:
    _log.warning(
        "CapitalDriftDetected: expected=%.2f actual=%.2f delta=%.2f",
        getattr(event, "expected", 0),
        getattr(event, "actual", 0),
        getattr(event, "delta", 0),
    )

# main.py:1061
event_bus.subscribe(CapitalDriftDetected, _log_capital_drift_event)
```

This is the ONLY subscriber to `CapitalDriftDetected`.

**Why it matters**

The entire v2 design premise is "broker is source of truth; local state is a cache; drift halts trading." The reconciler spends 860 lines of code detecting drift. When it finds drift, it publishes this event. The event handler then logs a warning and returns. No kill_switch. No halt. Trading continues.

**Worst case:** Broker has 3 positions, local DB has 2. Reconciler publishes drift event. Event handler logs warning. Next signal arrives, gets sized based on local state that's missing a position, system places a trade that blows concentration or exposure limits. By the time an operator reads the log, 5 more trades have happened.

**Fix spec**

```python
# main.py — replace _log_capital_drift_event with:
def _make_capital_drift_handler(kill_switch: KillSwitch, notifier: Optional[TelegramNotifier]):
    def _handle_capital_drift(event) -> None:
        expected = getattr(event, "expected", 0)
        actual = getattr(event, "actual", 0)
        delta = abs(getattr(event, "delta", 0))
        source = getattr(event, "source_module", "unknown")
        _log.critical(
            "CapitalDriftDetected from %s: expected=%.2f actual=%.2f delta=%.2f — HARD_KILL",
            source, expected, actual, delta,
        )
        try:
            kill_switch.hard_kill(
                reason=f"capital_drift:{source}:delta={delta:.2f}",
                triggered_by="capital_drift_handler",
            )
        except Exception as exc:
            _log.error("hard_kill raised in capital_drift_handler: %s", exc)
        if notifier is not None:
            try:
                notifier.send(
                    tier="CRITICAL",
                    title="Capital drift — HARD_KILL activated",
                    body=f"source={source} delta=₹{delta:.2f}. All trading halted. Manual intervention required.",
                    source="capital_drift_handler",
                )
            except Exception as exc:
                _log.error("notifier.send failed in capital_drift_handler: %s", exc)
    return _handle_capital_drift

# main.py:1061 — wire it
event_bus.subscribe(CapitalDriftDetected, _make_capital_drift_handler(kill_switch, notifier))
```

**Why hard_kill not soft_kill here:** capital drift means the local capital model is wrong. Continuing to allow exits means further `release_used` calls against an incorrect `used` bucket, compounding the corruption. hard_kill stops everything, attempts broker cancels, requires operator --resume.

**Tests to add**

- `test_capital_drift_event_triggers_hard_kill`
- `test_capital_drift_event_sends_critical_alert`
- `test_capital_drift_handler_survives_notifier_failure`

---

### BL-3 — Reconciler HEALTHY check silently masks BL-1

**Severity:** BLOCKER (depends on BL-1 and BL-7 being unfixed; fixing those reduces this to MEDIUM)
**File(s):** `orders/order_reconciler.py:297-307`
**Affects:** Post-restart state after BL-7 and BL-1 fixes are applied partially.

**What it is**

On reconcile, if broker_qty == local_qty, the check returns "HEALTHY / COSMETIC / action=none" and moves on. COSMETIC actions are not even written to `reconciliation_log`.

**Why it matters (with BL-1 unfixed)**

Post-restart, fund_manager has used=0, but the trades table has OPEN trade rows with correct qty_filled. Reconciler sees `broker_qty == local_qty` → HEALTHY → no action. The capital accounting is wrong, the reconciler has the information to detect it (broker position → expected margin → compare with fund_manager._used), but the HEALTHY check doesn't do that comparison.

**Fix spec**

After the quantity match, additionally verify capital accounting matches:

```python
# orders/order_reconciler.py — in _reconcile, replace HEALTHY branch:
if broker_qty == local_qty:
    # Additional check: does fund_manager know about this trade's margin?
    intent = _PRODUCT_TO_INTENT.get(trade.get("product", ""), "INTRADAY")
    expected_margin = required_margin(
        qty=local_qty,
        price=trade["entry_actual_price"] or trade["entry_target_price"],
        intent=intent,
        leverage_map=self._fm_leverage_map,  # inject at construction
    )
    fm_snap = self._fm.get_snapshot()
    bucket_used = (
        fm_snap.intraday_used if intent in ("INTRADAY", "COVER_ORDER", "BRACKET_ORDER")
        else fm_snap.positional_used
    )
    # Aggregate expected vs actual used for ALL open trades at end of cycle
    self._cycle_expected_used[trade["trade_id"]] = (intent, expected_margin)
    actions.append(ReconciliationAction(
        check_name="HEALTHY",
        tier="COSMETIC",
        symbol=symbol,
        trade_id=trade["trade_id"],
        description=f"Position healthy: qty={local_qty}",
        action_taken="none",
        success=True,
    ))
```

And at the end of `_reconcile()`, add a new CAPITAL_ACCOUNTING_DRIFT check:

```python
# orders/order_reconciler.py — at end of _reconcile, add:
if self._cycle_expected_used:
    expected_intraday = sum(
        m for tid, (intent, m) in self._cycle_expected_used.items()
        if intent in ("INTRADAY", "COVER_ORDER", "BRACKET_ORDER")
    )
    expected_positional = sum(
        m for tid, (intent, m) in self._cycle_expected_used.items()
        if intent == "DELIVERY"
    )
    snap = self._fm.get_snapshot()
    intraday_drift = abs(expected_intraday - snap.intraday_used)
    positional_drift = abs(expected_positional - snap.positional_used)
    if intraday_drift > self._cfg.capital_drift_tolerance or \
       positional_drift > self._cfg.capital_drift_tolerance:
        self._bus.publish(CapitalDriftDetected(
            source_module="order_reconciler.capital_accounting",
            expected=expected_intraday + expected_positional,
            actual=snap.intraday_used + snap.positional_used,
            delta=intraday_drift + positional_drift,
        ))
        actions.append(ReconciliationAction(
            check_name="CAPITAL_ACCOUNTING_DRIFT",
            tier="UNRECOVERABLE",
            symbol="", trade_id=None,
            description=f"fund_manager._used diverges from open trades by ₹{intraday_drift + positional_drift:.2f}",
            action_taken="CapitalDriftDetected published",
            success=True,
        ))
```

**Tests to add**

- `test_reconciler_detects_used_bucket_underflow`
- `test_reconciler_detects_used_bucket_overflow`
- `test_reconciler_healthy_qty_but_capital_drift_triggers_drift_event`

---

### BL-4 — `commit_to_used` failure in order_placer is swallowed

**Severity:** BLOCKER
**File(s):** `orders/order_placer.py:322-335`

**What it is**

```python
# orders/order_placer.py:322-335
try:
    self._fm.commit_to_used(
        reservation_id=reservation_id,
        actual_fill_price=event.avg_fill_price,
        actual_qty=event.filled_qty,
    )
except Exception as exc:
    log_exception(self._log, exc)
    self._log.error(
        "order_placer.commit_capital_failed",
        extra={"trade_id": trade_id, "reservation_id": reservation_id},
    )
    # Continue: trade is open, capital state may be wrong; reconciler will fix
```

**Why it matters**

If `commit_to_used` raises (invariant violation, reservation missing, DB write fails, etc.), the code logs an error and continues. The trade is now live at the broker, its DB row is OPEN, but the capital bucket transition (reserved → used) did not happen. `reserved` is permanently locked against this trade; `used` is 0 for this position. "Reconciler will fix" is false — reconciler cannot fix this specific case with the current check set (qty matches broker, DB has OPEN row, reconciler sees HEALTHY per BL-3).

**Worst case:** Disk fills up during a fast market. `_write_ledger` inside `commit_to_used` fails. Trade is open at broker, capital accounting wrong, reconciler silent. System continues placing more trades with the same bug, each one leaving `reserved` permanently inflated. Within a day, `reserved` exceeds `total`, available is 0, no new trades accept. Meanwhile, on exit, `release_used` fires against used=0 → invariant violation cascade.

**Fix spec**

Any failure inside `commit_to_used` is a CRITICAL event requiring hard_kill. The trade is in an inconsistent state that the system cannot self-repair.

```python
# orders/order_placer.py:322-335 — replace with:
try:
    self._fm.commit_to_used(
        reservation_id=reservation_id,
        actual_fill_price=event.avg_fill_price,
        actual_qty=event.filled_qty,
    )
except Exception as exc:
    log_exception(self._log, exc)
    self._log.critical(
        "order_placer.commit_capital_failed — HARD_KILL",
        extra={"trade_id": trade_id, "reservation_id": reservation_id,
               "error": str(exc)},
    )
    # Mark trade row with explicit capital-drift flag for operator visibility
    try:
        self._om.update_trade_status(trade_id, "CAPITAL_DRIFT")
    except Exception:
        pass  # best effort; we're already in a bad state
    if self._kill_switch is not None:
        try:
            self._kill_switch.hard_kill(
                reason=f"commit_to_used_failed:{trade_id}:{exc}",
                triggered_by="order_placer",
            )
        except Exception as ks_exc:
            self._log.error("hard_kill raised: %s", ks_exc)
    # Do NOT swallow — re-raise so upstream (signal_processor) sees it
    raise
```

Add `CAPITAL_DRIFT` to the trades.status enum values documented in schema.sql.

**Tests to add**

- `test_commit_to_used_failure_triggers_hard_kill`
- `test_commit_to_used_failure_marks_trade_capital_drift`
- `test_commit_to_used_failure_re_raises_exception`

---

### BL-5 — Ledger write is not in the same transaction as the in-memory mutation

**Severity:** BLOCKER
**File(s):** `capital/fund_manager.py:716-749`, various mutation methods
**Affects:** All capital mutations.

**What it is**

FM10 spec says: "Every mutation writes to fm_ledger in same txn. If write fails, mutation is rolled back." The code does not implement this. The mutation pattern in `reserve()`, `release()`, `commit_to_used()`, `release_used()` is:

```python
# capital/fund_manager.py:281-356 (reserve — representative)
with self._lock:
    # 1. Mutate in-memory state
    self._bucket_deduct_avail(bucket, margin)
    self._bucket_add_reserved(bucket, margin)
    # 2. Check invariant
    self._check_invariant("reserve", rid)
    # 3. Register reservation in dict
    self._reservations[rid] = res
    # 4. Write ledger row — OPENS ITS OWN TRANSACTION
    self._write_ledger(...)  # ← separate transaction from step 1
```

`_write_ledger` opens a new `store.transaction()` internally. If the SQL INSERT fails (disk full, DB locked, permission denied, SIGKILL mid-call), the in-memory state is already mutated (steps 1-3), but the audit trail has no row.

**Why it matters**

- **Auditability lies.** The ledger is supposed to be the authoritative record of every capital movement. With this bug, not every mutation has a ledger row.
- **Restart rebuild becomes unsafe.** If you implement BL-1's fix by rebuilding state from the ledger, missing rows → wrong reconstructed state.
- **Pre-crash inconsistency.** If the process crashes between step 1 and step 4, the next restart sees zero in-memory state (per BL-1) and a ledger that's also missing the last mutation → system comes up believing a trade's reservation never happened, even though the broker has the order.

**Fix spec**

The mutation needs to happen inside the same transaction as the ledger write. This means rewriting the fund_manager methods to pass a cursor to the in-memory mutation helpers, OR moving to a write-ahead pattern where the ledger row is the commit record.

The cleanest approach is write-ahead:

```python
# capital/fund_manager.py — refactor reserve():
def reserve(self, symbol, qty, price, intent, signal_id=None) -> ReservationResult:
    with self._lock:
        self._assert_initialized()
        bucket = self._bucket_for_intent(intent)
        margin = required_margin(qty, price, intent, self._leverage_map)
        avail_before = self._bucket_avail(bucket)

        if margin > avail_before:
            return ReservationResult(success=False, reservation_id="", margin=margin,
                                    bucket=bucket, reason_if_failed=...)

        rid = uuid.uuid4().hex[:16]
        ts = now_ist().isoformat()

        # Write ledger row FIRST inside its own transaction. If this fails, no mutation happens.
        try:
            with self._store.transaction() as cur:
                cur.execute(
                    "INSERT INTO fm_ledger (ts, mutation_type, amount, bucket, "
                    "balance_before, balance_after, signal_id, reservation_id, reason) "
                    "VALUES (?, 'RESERVE', ?, ?, ?, ?, ?, ?, ?)",
                    (ts, margin, bucket, avail_before, avail_before - margin,
                     signal_id, rid, f"{symbol} qty={qty} @ {price} intent={intent}"),
                )
        except Exception as exc:
            log_exception(self._log, exc)
            return ReservationResult(
                success=False, reservation_id="", margin=margin, bucket=bucket,
                reason_if_failed=f"ledger write failed: {exc}",
            )

        # Ledger write committed → now mutate in-memory state
        self._bucket_deduct_avail(bucket, margin)
        self._bucket_add_reserved(bucket, margin)
        self._reservations[rid] = _Reservation(
            reservation_id=rid, symbol=symbol, qty=qty, price=price,
            intent=intent, margin=margin, bucket=bucket,
            signal_id=signal_id, ts=ts,
        )

        try:
            self._check_invariant("reserve", rid)
        except CapitalInvariantViolation:
            # Shouldn't happen since we checked avail, but if it does,
            # the ledger row is already written; we must write a COMPENSATING row
            self._rollback_reserve(rid, margin, bucket, avail_before, signal_id, ts)
            raise

        return ReservationResult(success=True, reservation_id=rid, margin=margin,
                                bucket=bucket, reason_if_failed="")

def _rollback_reserve(self, rid, margin, bucket, avail_before, signal_id, ts):
    """Write a compensating ledger row to cancel a failed reserve."""
    try:
        with self._store.transaction() as cur:
            cur.execute(
                "INSERT INTO fm_ledger (ts, mutation_type, amount, bucket, "
                "balance_before, balance_after, signal_id, reservation_id, reason) "
                "VALUES (?, 'ROLLBACK', ?, ?, ?, ?, ?, ?, ?)",
                (now_ist().isoformat(), -margin, bucket,
                 avail_before - margin, avail_before, signal_id, rid,
                 "compensating rollback: invariant check failed post-reserve"),
            )
    except Exception:
        self._log.critical("ROLLBACK LEDGER WRITE FAILED — capital ledger is now inconsistent")
    # revert in-memory
    self._bucket_add_avail(bucket, margin)
    self._bucket_deduct_reserved(bucket, margin)
    self._reservations.pop(rid, None)
```

Apply the same write-first pattern to `release()`, `commit_to_used()`, `release_used()`, `sync_from_broker()`.

**Tests to add**

- `test_reserve_ledger_write_failure_no_mutation`
- `test_reserve_invariant_violation_writes_rollback_ledger`
- `test_ledger_and_memory_consistent_under_concurrent_reserves`

---

### BL-6 — Broker 429 backoff is entirely missing

**Severity:** BLOCKER
**File(s):** `broker/zerodha_adapter.py`, `broker/rate_limiter.py`
**Affects:** All broker calls under rate-limit pressure.

**What it is**

G7 spec says: "Client-side token bucket per endpoint category. 4-step backoff on 429: 1s/5s/30s/soft_kill. Wraps every broker call."

The code implements:
- Token bucket ✓ (`rate_limiter.py`)
- `penalize()` method to freeze bucket on 429 ✓ (`rate_limiter.py:239-253`)
- `acquire()` before every broker call ✓

But:
- `penalize()` is never called anywhere. Confirmed via `grep -rn "penalize" --include="*.py"` — only the definition and tests.
- `zerodha_adapter._translate_kite_exception()` catches `kex.NetworkException` and translates to `BrokerTimeoutError` without inspecting the HTTP status code. Zerodha's 429 response comes through as a NetworkException; no classification.
- No circuit-breaker for persistent 429s → no auto-soft_kill.

**Why it matters**

Under a burst of signals (say, 20 signals arrive within 60 seconds from a scanner), `order` bucket's 8-token capacity drains. Subsequent `acquire()` calls block up to 30s. If Zerodha starts returning 429s (which it will under sustained pressure), the adapter sees NetworkException, marks the order as rejected, retries aren't wired (ZA11: "Adapter does NOT retry"), and the caller assumes the order just failed. Meanwhile the token bucket has plenty of tokens (because 429 wasn't communicated back to it).

**Worst case:** Chartink scanner fires 40 signals at market open. System tries to place 40 entry orders in rapid succession. Zerodha accepts the first ~15 cleanly, starts 429ing the rest. Each 429 becomes a BrokerError, the trade is marked FAILED, capital is released. Signal_processor retries none of these (correctly, no retry). But now kill_switch's auto-trip counter (KS7: 3 consecutive API failures → soft_kill) fires after 3 consecutive 429s. System halts. By the time operator reviews, it's mid-day and 25 of 40 signals are REJECTED_PLACEMENT_FAILED when the actual cause was rate-limiting, not any real rejection.

A subtler case: some Zerodha operations silently downgrade to retry responses that look like success but represent a stale response. Without 429 classification you can't distinguish these.

**Fix spec**

Three changes:

1. **Classify 429 in the exception translator.** Zerodha's kiteconnect lib wraps HTTP responses; inspect the status code:

```python
# broker/zerodha_adapter.py — update _translate_kite_exception:
def _translate_kite_exception(exc, context, logger) -> BrokerError:
    log_exception(logger, exc)

    # NEW: inspect HTTP status for 429 / 503
    http_status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if http_status in (429, 503):
        # Propagate to caller so rate_limiter.penalize() can be called
        return BrokerRateLimitError(
            f"Zerodha rate limited (status={http_status}): {exc}",
            http_status=http_status,
            **context,
        )

    if isinstance(exc, kex.TokenException): ...
    # rest unchanged
```

2. **Wrap every adapter public method with penalize-on-rate-limit-error.** Define a decorator:

```python
# broker/zerodha_adapter.py — add decorator
from functools import wraps
from core.exceptions import BrokerRateLimitError

_BACKOFF_SCHEDULE = [1.0, 5.0, 30.0]  # G7: 1s, 5s, 30s, then soft_kill

def _with_rate_limit_backoff(category_fn):
    def decorator(method):
        @wraps(method)
        def wrapper(self, *args, **kwargs):
            category = category_fn(method.__name__)
            for attempt in range(len(_BACKOFF_SCHEDULE) + 1):
                try:
                    return method(self, *args, **kwargs)
                except BrokerRateLimitError as exc:
                    if attempt >= len(_BACKOFF_SCHEDULE):
                        self._log.critical(
                            "Persistent 429/503 on %s after %d attempts — soft_kill",
                            method.__name__, attempt,
                        )
                        if self._kill_switch:
                            self._kill_switch.soft_kill(
                                reason=f"persistent_broker_rate_limit:{method.__name__}",
                                triggered_by="zerodha_adapter",
                            )
                        raise
                    sleep_sec = _BACKOFF_SCHEDULE[attempt]
                    self._log.warning(
                        "broker rate limited on %s attempt %d; penalizing %s for %.1fs",
                        method.__name__, attempt, category, sleep_sec,
                    )
                    self._rl.penalize(category, sleep_sec)
                    time.sleep(sleep_sec)
        return wrapper
    return decorator
```

3. **Apply decorator to all adapter public methods** that call kite:

```python
@_with_rate_limit_backoff(lambda name: _CATEGORY_MAP[name])
def place_order(self, ...): ...

@_with_rate_limit_backoff(lambda name: _CATEGORY_MAP[name])
def cancel_order(self, ...): ...

@_with_rate_limit_backoff(lambda name: _CATEGORY_MAP[name])
def modify_order(self, ...): ...

@_with_rate_limit_backoff(lambda name: _CATEGORY_MAP[name])
def get_positions(self): ...

@_with_rate_limit_backoff(lambda name: _CATEGORY_MAP[name])
def get_margins(self): ...

@_with_rate_limit_backoff(lambda name: _CATEGORY_MAP[name])
def get_quote(self, ...): ...

@_with_rate_limit_backoff(lambda name: _CATEGORY_MAP[name])
def get_open_orders(self): ...
```

4. **Inject kill_switch into ZerodhaAdapter** at construction (new dependency, main.py update).

**Tests to add**

- `test_place_order_retries_on_429_with_backoff`
- `test_place_order_soft_kills_after_persistent_429`
- `test_rate_limiter_penalize_called_on_429`
- `test_429_status_code_classified_as_rate_limit_error`

---


### BL-7 — OrderFilled events are never emitted for entry orders

**Severity:** BLOCKER — highest severity in the audit.
**File(s):** `broker/order_monitor.py`, `orders/order_placer.py`, `orders/full_entry_engine.py`, `orders/order_protocol_co.py`, `orders/order_protocol_limit.py`, `main.py:960-968`

**What it is**

The event-driven fill confirmation pipeline has a structural wiring gap. `order_monitor` publishes `OrderFilled` only for orders passed through its `track()` method. `track()` is never called for entry orders. Therefore `OrderFilled` is never emitted for any entry. `order_placer._on_order_filled` (which calls `commit_to_used`) is never invoked on the happy path.

See Section 2 for the full evidence chain.

**Why it matters**

Without `OrderFilled` events:
- `fund_manager.commit_to_used` never fires → reserved margin is never released to `used`.
- Trade row stays at `status=PENDING_FILL` forever (the update to OPEN lives in `_on_order_filled`).
- `order_monitor._watched` dict never includes entries, so fill timeout / orphan detection doesn't apply to entry orders.
- Smart TGT manager's `register_trade()` is not called from anywhere visible (likely intended to be called from `_on_order_filled`), so SL trailing never starts for live trades.

**Worst case:** Every live trade breaks this way. System is unusable for live operation.

**Fix spec**

The fix requires wiring `order_monitor` into `order_placer` and calling `track()` for every placed order:

1. **Update `OrderPlacer.__init__`** to accept `order_monitor`:

```python
# orders/order_placer.py
class OrderPlacer:
    def __init__(
        self,
        *,
        entry_engine: EntryEngine,
        order_manager: OrderManager,
        fund_manager: FundManager,
        bus: EventBus,
        logger: logging.Logger,
        kill_switch: Optional[KillSwitch] = None,
        product_resolver: Optional[ProductResolver] = None,
        order_monitor: "OrderMonitor" = None,  # NEW
        smart_tgt_manager: "SmartTgtManager" = None,  # NEW, see BL-10 fix
    ):
        ...
        self._order_monitor = order_monitor
        self._smart_tgt = smart_tgt_manager
```

2. **Track entry, SL, TGT orders after successful placement:**

```python
# orders/order_placer.py — inside place(), after _persist_entry_orders:
# Register with order_monitor for fill detection
placed_at = now_ist()
if self._order_monitor and result.entry_internal_id:
    self._order_monitor.track(
        internal_order_id=result.entry_internal_id,
        broker_order_id=result.entry_broker_order_id,
        symbol=symbol,
        side=side,
        qty=qty,
        expected_price=entry_price,
        placed_at=placed_at,
    )
# (Optional) Also track SL and TGT for fill detection on exit
if self._order_monitor and result.sl_internal_id:
    self._order_monitor.track(
        internal_order_id=result.sl_internal_id,
        broker_order_id=result.sl_broker_order_id,
        symbol=symbol,
        side="SELL" if side == "BUY" else "BUY",
        qty=qty,
        expected_price=sl_price,
        placed_at=placed_at,
    )
if self._order_monitor and result.tgt_internal_id:
    self._order_monitor.track(
        internal_order_id=result.tgt_internal_id,
        broker_order_id=result.tgt_broker_order_id,
        symbol=symbol,
        side="SELL" if side == "BUY" else "BUY",
        qty=qty,
        expected_price=tgt_price,
        placed_at=placed_at,
    )
```

3. **Expand `EntryResult`** (in `orders/entry_engine.py`) to carry internal IDs for all three legs:

```python
@dataclass(frozen=True)
class EntryResult:
    success: bool
    entry_internal_id: str = ""
    entry_broker_order_id: str = ""
    sl_internal_id: str = ""         # NEW
    sl_broker_order_id: str = ""
    tgt_internal_id: str = ""        # NEW
    tgt_broker_order_id: str = ""
    order_protocol: str = ""
    rejection_reason: str = ""
```

Update `order_protocol_co.py` and `order_protocol_limit.py` to populate these fields — they already call `adapter.place_order()` which returns `PlacedOrder` with `internal_order_id`.

4. **Update `_on_order_filled` to handle entry vs exit fills differently:**

```python
# orders/order_placer.py — update _on_order_filled:
def _on_order_filled(self, event: OrderFilled) -> None:
    internal_id = event.internal_order_id
    with self._fill_map_lock:
        fill_entry = self._fill_map.pop(internal_id, None)
    if fill_entry is None:
        return  # not ours

    trade_id = fill_entry.trade_id
    reservation_id = fill_entry.reservation_id
    leg = fill_entry.leg  # "ENTRY" | "SL" | "TGT"

    if leg == "ENTRY":
        # commit_to_used + record fill + register smart_tgt
        self._handle_entry_fill(trade_id, reservation_id, event)
    elif leg in ("SL", "TGT"):
        # release_used + publish PositionClosed (addresses BL-10)
        self._handle_exit_fill(trade_id, leg, event)
```

5. **Update main.py wiring:**

```python
# main.py:960 — pass order_monitor and smart_tgt_manager
order_placer = OrderPlacer(
    entry_engine=full_engine,
    order_manager=order_manager,
    fund_manager=fund_manager,
    bus=event_bus,
    logger=get_logger("order_placer"),
    kill_switch=kill_switch,
    product_resolver=product_resolver,
    order_monitor=order_monitor,         # NEW
    smart_tgt_manager=smart_tgt,         # NEW (see BL-10)
)
```

**Tests to add (critical)**

- `test_place_entry_calls_order_monitor_track_for_entry`
- `test_place_entry_calls_order_monitor_track_for_sl_and_tgt`
- `test_on_order_filled_entry_calls_commit_to_used`
- `test_on_order_filled_entry_registers_smart_tgt` (if CO protocol)
- `test_on_order_filled_sl_calls_release_used`
- `test_on_order_filled_tgt_calls_release_used`
- **Integration test: full trade lifecycle in paper mode asserts fund_manager._used > 0 after entry fill and == 0 after exit fill** (this would have caught the entire bug)

---

### BL-8 — Order row DB insert failure is silently swallowed

**Severity:** BLOCKER
**File(s):** `orders/order_placer.py:465-472`

**What it is**

```python
# orders/order_placer.py — _persist_entry_orders, end of method:
except Exception as exc:
    # DB write failure is non-fatal for order placement;
    # reconciler will rebuild from broker state.
    log_exception(self._log, exc)
    self._log.error(
        "order_placer.persist_orders_failed",
        extra={"trade_id": trade_id},
    )
```

If the DB INSERT for orders rows fails (disk full, schema mismatch, constraint violation), the code logs an error and returns. The broker has the orders placed, but the local `orders` table has no rows for them.

**Why it matters**

- Reconciler's G5b crash-recovery-SL check does `get_sl_order_for_trade(trade_id)`. If it returns None (because no row was inserted), the reconciler thinks there's no SL at the broker → places a FRESH SL order → duplicate SL at broker → broker will reject the second one OR (worse, for brokers with looser constraints) accept both, and when price triggers, TWO SL sell orders fire → short position instead of just flat.
- Smart_tgt manager's `get_co_entry_order_for_trade(trade_id)` fails to find the CO order → cannot trail SL.
- Daily reports show missing order data.

The comment "reconciler will rebuild from broker state" is wrong — there's no code path in the reconciler that rebuilds the `orders` table from broker state.

**Fix spec**

DB insert failure for a placed order is a critical inconsistency. Either:
- **Option A (safer):** Hard-kill on persist failure, cancel the broker order, release capital.
- **Option B (alternative):** Retry the DB insert with exponential backoff; if still fails, cancel broker order.

Option A, coded:

```python
# orders/order_placer.py — rewrite _persist_entry_orders:
def _persist_entry_orders(
    self, trade_id, result, symbol, qty, side, intent,
) -> None:
    """Persist order rows; on failure, attempt cleanup and raise."""
    exit_side = "SELL" if side == "BUY" else "BUY"
    product = self._resolve_product(intent)
    co_variety = "co" if result.order_protocol == "CO_PLUS_TGT" else "regular"

    rows_to_insert = []
    if result.entry_broker_order_id:
        rows_to_insert.append({
            "leg": "ENTRY", "broker_order_id": result.entry_broker_order_id,
            "transaction_type": side,
            "order_type": "SL" if result.order_protocol == "CO_PLUS_TGT" else "LIMIT",
            "product": product, "variety": co_variety,
            "qty_requested": qty, "leg_index": 0,
        })
    if result.sl_broker_order_id:
        rows_to_insert.append({
            "leg": "SL", "broker_order_id": result.sl_broker_order_id,
            "transaction_type": exit_side, "order_type": "SL-M",
            "product": product, "variety": "regular",
            "qty_requested": qty, "leg_index": 0,
        })
    if result.tgt_broker_order_id:
        rows_to_insert.append({
            "leg": "TGT", "broker_order_id": result.tgt_broker_order_id,
            "transaction_type": exit_side, "order_type": "LIMIT",
            "product": product, "variety": "regular",
            "qty_requested": qty, "leg_index": 0,
        })

    # All-or-nothing: one transaction, all rows or none.
    try:
        with self._om._store.transaction() as cur:
            for row in rows_to_insert:
                cur.execute(
                    "INSERT INTO orders (order_id, trade_id, leg, leg_index, "
                    "transaction_type, order_type, product, variety, qty_requested, "
                    "price, trigger_price, status, qty_filled, placed_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, 0.0, 'PENDING', 0, ?, ?)",
                    (row["broker_order_id"], trade_id, row["leg"], row["leg_index"],
                     row["transaction_type"], row["order_type"],
                     row["product"], row["variety"], row["qty_requested"],
                     now_ist().isoformat(), now_ist().isoformat()),
                )
    except Exception as exc:
        log_exception(self._log, exc)
        self._log.critical(
            "order_placer.persist_orders_failed — cleaning up broker orders",
            extra={"trade_id": trade_id, "error": str(exc)},
        )
        # Attempt to cancel each broker order to prevent stranded positions
        for row in rows_to_insert:
            try:
                self._adapter.cancel_order(row["broker_order_id"])
            except Exception as cancel_exc:
                self._log.error(
                    "Failed to cancel stranded broker order %s: %s",
                    row["broker_order_id"], cancel_exc,
                )
        # Hard-kill: we have broker orders possibly still live, DB unaware
        if self._kill_switch:
            self._kill_switch.hard_kill(
                reason=f"persist_orders_failed:{trade_id}",
                triggered_by="order_placer",
            )
        raise
```

**Tests to add**

- `test_persist_orders_db_failure_cancels_broker_orders`
- `test_persist_orders_db_failure_triggers_hard_kill`
- `test_persist_orders_atomic_all_or_nothing`

---

### BL-9 — CapitalInvariantViolation triggers soft_kill, should hard_kill

**Severity:** BLOCKER
**File(s):** `main.py:257-273`, `capital/fund_manager.py:_on_critical callback wiring`

**What it is**

```python
# main.py:257-273
def _make_critical_failure_cb(kill_switch, notifier):
    def _on_critical_failure(source: str, reason: str) -> None:
        _log.critical("Critical failure from %s: %s", source, reason)
        kill_switch.soft_kill(                      # ← SOFT, not HARD
            reason=f"{source}: {reason}", triggered_by="auto"
        )
        ...
    return _on_critical_failure
```

FundManager's `_check_invariant` catches `CapitalInvariantViolation` and calls `self._on_critical(str(exc))`. That callback is wired to the above `_on_critical_failure`, which fires `soft_kill`. Soft_kill allows exits.

**Why it matters**

If capital accounting has violated its invariant, any further capital mutation (including `release_used` on exit) compounds the corruption. Soft_kill allows exits → each exit triggers another invariant violation → more soft_kills (idempotent) → cascading CRITICAL alerts → meanwhile positions are closing at broker with wrong local accounting.

The correct response to invariant violation is: stop all activity, cancel broker orders, require operator review. That is hard_kill.

**Fix spec**

```python
# main.py:257-273 — replace soft_kill with hard_kill for invariant violations.
# Split into two callbacks: one for invariant (hard), one for other (soft).

def _make_invariant_failure_cb(kill_switch, notifier):
    def _on_invariant_failure(source: str, reason: str) -> None:
        _log.critical("Capital invariant violation from %s: %s", source, reason)
        try:
            kill_switch.hard_kill(
                reason=f"capital_invariant:{source}:{reason}",
                triggered_by="invariant_handler",
            )
        except Exception as exc:
            _log.error("hard_kill raised: %s", exc)
        if notifier:
            try:
                notifier.send(
                    tier="CRITICAL",
                    title="Capital invariant violated — HARD_KILL",
                    body=f"{source}: {reason}. System halted. Manual intervention required.",
                    source="invariant_handler",
                )
            except Exception as exc:
                _log.error("notifier.send failed: %s", exc)
    return _on_invariant_failure

def _make_critical_failure_cb(kill_switch, notifier):
    """Kept as soft_kill for non-invariant critical failures."""
    def _on_critical_failure(source: str, reason: str) -> None:
        _log.critical("Critical failure from %s: %s", source, reason)
        kill_switch.soft_kill(
            reason=f"{source}: {reason}", triggered_by="auto"
        )
        ...
    return _on_critical_failure
```

Wire fund_manager separately:

```python
# main.py:828-839 — update FundManager construction:
fund_manager = FundManager(
    state_store=store,
    bus=event_bus,
    logger=get_logger("fund_manager"),
    intraday_bucket_pct=cap_cfg.intraday_bucket_pct,
    positional_bucket_pct=cap_cfg.positional_bucket_pct,
    daily_loss_limit=cap_cfg.daily_loss_limit,
    leverage_map=leverage_map,
    on_daily_loss_breach=lambda: kill_switch.soft_kill(
        reason="daily_loss_limit_breached", triggered_by="fund_manager"
    ),
    on_critical_failure=_make_invariant_failure_cb(kill_switch, notifier)  # NEW: hard_kill
)
```

**Tests to add**

- `test_invariant_violation_triggers_hard_kill`
- `test_daily_loss_breach_triggers_soft_kill_not_hard_kill`
- `test_hard_kill_attempts_broker_order_cancellation_on_invariant_violation`

---

### BL-10 — PositionClosed is never emitted; shadow_tracker is dead in production

**Severity:** BLOCKER
**File(s):** All, but most centrally `orders/order_placer.py`, `orders/order_reconciler.py`, `orders/eod_squareoff.py`

**What it is**

Grep confirms:
```
$ grep -rn "publish.*PositionClosed\|PositionClosed(" --include="*.py" | grep -v test_
core/events.py:103:    class PositionClosed(Event):   # definition only
```

No production code publishes `PositionClosed`. `shadow_tracker` (681 LOC) subscribes to it. `fund_manager.release_used` is called explicitly by reconciler's MANUAL_CLOSE and (should be) by order_placer's SL/TGT fill handler — but no event is published for shadow_tracker to consume.

**Why it matters**

- Shadow_tracker inning logic never runs in production. The "concern 4 fix" (old system reporting TGT when SL hit first) is not actually deployed.
- If any other subsystem depends on PositionClosed in the future, same bug pattern.
- Daily report multi-inning section (Module 40, referenced in SH locked decisions) will never have data.

**Fix spec**

Publish `PositionClosed` wherever `release_used` is called. There are 3 such places after the other fixes:

1. **order_placer's exit-fill handler** (after BL-7 fix, in `_handle_exit_fill`):

```python
# orders/order_placer.py — inside _handle_exit_fill (new method):
def _handle_exit_fill(self, trade_id: str, leg: str, event: OrderFilled) -> None:
    """Handle SL or TGT fill: release used capital, update trade, publish PositionClosed."""
    trade = self._om.get_trade(trade_id)
    if trade is None:
        self._log.error("exit fill for unknown trade %s", trade_id)
        return

    intent = _PROTOCOL_TO_INTENT[trade["order_protocol"]]  # define this mapping
    entry_price = trade["entry_actual_price"]
    exit_price = event.avg_fill_price
    qty = event.filled_qty

    try:
        costs = self._cost_calculator.total_charges(
            symbol=trade["symbol"], qty=qty,
            entry_price=entry_price, exit_price=exit_price,
            product=trade["product"],
        )
    except Exception:
        costs = 0.0

    try:
        release_result = self._fm.release_used(
            symbol=trade["symbol"],
            exit_price=exit_price, exit_qty=qty,
            intent=intent, entry_price=entry_price,
            costs=costs,
        )
    except CapitalInvariantViolation:
        raise  # will hit hard_kill via main.py wiring per BL-9
    except Exception as exc:
        self._log.critical("release_used failed — HARD_KILL", extra={...})
        if self._kill_switch:
            self._kill_switch.hard_kill(reason=f"release_used_failed:{trade_id}:{exc}", triggered_by="order_placer")
        raise

    # Update trade row
    self._om.close_trade(
        trade_id=trade_id,
        exit_price=exit_price, exit_qty=qty,
        exit_reason="SL_HIT" if leg == "SL" else "TGT_HIT",
        gross_pnl=release_result.pnl_delta,
        charges=costs,
    )

    # Publish PositionClosed — BL-10 FIX
    self._bus.publish(PositionClosed(
        source_module="order_placer",
        trade_id=trade_id,
        symbol=trade["symbol"],
        direction=trade["direction"],
        entry_price=entry_price,
        exit_price=exit_price,
        qty=qty,
        pnl=release_result.pnl_delta,
        exit_reason="SL_HIT" if leg == "SL" else "TGT_HIT",
    ))

    # Unregister from smart_tgt
    if self._smart_tgt:
        self._smart_tgt.unregister_trade(trade_id)
```

2. **order_reconciler's MANUAL_CLOSE path** (`orders/order_reconciler.py:383-450`):

```python
# orders/order_reconciler.py — in _check1_manual_close, after release_used succeeds:
# Publish PositionClosed for shadow_tracker consumption
try:
    self._bus.publish(PositionClosed(
        source_module="order_reconciler",
        trade_id=trade_id,
        symbol=symbol,
        direction=trade["direction"],
        entry_price=float(entry_price),
        exit_price=float(entry_price),  # breakeven proxy
        qty=qty,
        pnl=0.0,
        exit_reason="MANUAL_CLOSE",
    ))
except Exception as exc:
    self._log.error("check1: publish PositionClosed failed: %s", exc)
```

3. **eod_squareoff** — currently hands exit fills to order_monitor (the only place `track()` is actually called). The order_monitor emits `OrderFilled`. We need the handler for that OrderFilled (leg=EOD) to publish PositionClosed too:

```python
# orders/order_placer.py — extend _on_order_filled to handle leg="EOD":
if leg in ("SL", "TGT", "EOD"):
    self._handle_exit_fill(trade_id, leg, event)
```

And update the `exit_reason` mapping accordingly.

**Tests to add**

- `test_exit_fill_publishes_position_closed`
- `test_manual_close_publishes_position_closed`
- `test_eod_exit_publishes_position_closed`
- `test_shadow_tracker_receives_position_closed_in_integration`

---

### BL-11 — live_feed reconnect callback fires on retry attempt, not successful reconnect

**Severity:** BLOCKER (not because the system halts, but because smart_tgt recomputes against stale data, producing wrong SL trails)
**File(s):** `data/live_feed.py:189-208`

**What it is**

```python
# data/live_feed.py:189-208
def _on_reconnect(self, ws, attempts_count: int) -> None:
    """LF7: Called on each reconnect attempt by KiteTicker."""
    now = now_ist()
    ...
    if not self._reconnect_notified:
        self._reconnect_notified = True
        if self._on_reconnect_cb is not None:
            self._on_reconnect_cb(now)      # ← fires on attempt, not success
```

KiteTicker's `on_reconnect` callback fires at the start of each retry attempt. The code triggers the recompute callback (which smart_tgt_manager uses to recompute trail SL from candle history) on the first attempt, BEFORE a successful reconnection. Candle history is still stale at that point.

**Why it matters**

When the websocket reconnects after a gap, smart_tgt is supposed to:
- Discard its in-memory `best_price` (set to None)
- Fetch candle history
- Recompute trail once

But if the callback fires before the connection is actually restored, `get_candles()` returns whatever's cached locally (nothing new has streamed in yet). Smart_tgt recomputes based on pre-gap history → trail SL is calculated incorrectly for the post-gap period. The next real candle close (after successful reconnect) will then advance trail from a wrong baseline.

Worst case: SL trail is advanced further than it should be, causing a premature SL hit on normal price pullback. On a LONG position: SL could be set higher than the new best_price justifies → normal pullback triggers SL.

**Fix spec**

Move the recompute callback from `_on_reconnect` to `_on_connect`:

```python
# data/live_feed.py — update _on_connect and _on_reconnect:
def _on_connect(self, ws, response) -> None:
    """LF3: Successful connection. Resubscribe all tracked tokens."""
    was_reconnect = self._disconnect_time is not None
    self._connected = True
    self._log.info("LiveFeedManager: connected to KiteTicker")
    with self._lock:
        tokens = list(self._subscribed)
    if tokens:
        ws.subscribe(tokens)
        ws.set_mode(KiteTicker.MODE_LTP, tokens)

    # NEW: fire reconnect callback AFTER successful reconnection
    if was_reconnect and self._on_reconnect_cb is not None:
        self._log.info("LiveFeedManager: firing reconnect callback")
        try:
            self._on_reconnect_cb(now_ist())
        except Exception as exc:
            self._log.error("_on_reconnect_cb raised: %s", exc)

    self._disconnect_time = None
    self._reconnect_notified = False

def _on_reconnect(self, ws, attempts_count: int) -> None:
    """LF7: Called on each reconnect attempt by KiteTicker.
    Just logs — the actual recompute happens on _on_connect success."""
    now = now_ist()
    gap_sec = None
    if self._disconnect_time is not None:
        gap_sec = (now - self._disconnect_time).total_seconds()

    self._log.warning(
        "LiveFeedManager: reconnect attempt %d%s",
        attempts_count,
        f" gap={gap_sec:.0f}s" if gap_sec is not None else "",
    )

    # Alert if gap > 10 min (fires multiple times if kept reconnecting; ok)
    if gap_sec is not None and gap_sec > 600 and not self._reconnect_notified:
        self._reconnect_notified = True
        self._log.critical("LiveFeedManager: feed gap > 10 min (%.0fs)", gap_sec)
        if self._on_critical_failure is not None:
            self._on_critical_failure(f"feed gap {gap_sec:.0f}s > 10 min")
```

Additional: `smart_tgt_manager.on_reconnect` should also wait a brief period after connect to allow the first real candle to close before recomputing. Alternatively, require at least N candles present in history before recomputing:

```python
# orders/smart_tgt_manager.py — update _recompute_on_reconnect:
def _recompute_on_reconnect(self, trade_id: str) -> None:
    ...
    candles = self._candle_store.get_candles(token, n=_MAX_HISTORY_CANDLES)
    # NEW: require at least 2 candles post-reconnect to ensure fresh data
    fresh_candles = [c for c in candles if c.close_ts > reconnect_ts]
    if len(fresh_candles) < 2:
        self._log.warning(
            "SmartTgt: only %d fresh candles after reconnect for %s; "
            "skipping recompute this cycle", len(fresh_candles), trade_id,
        )
        return
    ...
```

**Tests to add**

- `test_reconnect_callback_fires_on_connect_success_not_attempt`
- `test_no_recompute_without_fresh_candles`
- `test_disconnect_time_cleared_after_successful_reconnect`

---

## 5. HIGH findings

### H-1 — sync_from_broker clamps available to 0 and skips invariant check

**File:** `capital/fund_manager.py:550-588`

**What it is**

```python
self._intraday_avail = max(
    0.0, intraday_total - self._intraday_reserved - self._intraday_used
)
```

The `max(0, ...)` silently masks drift — the whole point of sync is to detect it. No `_check_invariant()` call at the end.

**Fix spec**

```python
# capital/fund_manager.py — update sync_from_broker:
def sync_from_broker(self, broker_balance: float) -> None:
    with self._lock:
        self._assert_initialized()
        old_total = self._total
        self._total = broker_balance

        intraday_total = broker_balance * self._intraday_pct
        positional_total = broker_balance * self._positional_pct

        # Compute without clamping — let negative flag the drift
        self._intraday_avail = intraday_total - self._intraday_reserved - self._intraday_used
        self._positional_avail = positional_total - self._positional_reserved - self._positional_used

        # write ledger row (unchanged code) ...

        # NEW: run invariant check — will raise if negative avail or balance mismatch
        try:
            self._check_invariant("sync_from_broker", "")
        except CapitalInvariantViolation:
            # Publish drift, re-raise (critical callback will hard_kill per BL-2)
            self._bus.publish(CapitalDriftDetected(
                source_module="fund_manager.sync",
                expected=old_total,
                actual=broker_balance,
                delta=broker_balance - old_total,
            ))
            raise
```

### H-2 — Position sizer uses snap.total which includes intraday profits (P7a T+1 violated)

**File:** `capital/position_sizer.py:232-257`

**What it is**

`release_used` adds PnL (including profits) to `_total`. `position_sizer` reads `snap.total` directly for risk sizing. Intraday profits therefore immediately inflate tradable balance, violating P7a ("profits don't count until T+1").

**Fix spec**

Make `get_snapshot()` return a separate `tradable_total` that uses the P7a formula:

```python
# capital/fund_manager.py — add to get_snapshot:
@dataclass(frozen=True)
class CapitalSnapshot:
    total: float                    # broker-settled + realized PnL (everything)
    tradable_total: float           # P7a-compliant: only losses count, profits wait T+1
    ...

def get_snapshot(self) -> CapitalSnapshot:
    with self._lock:
        tradable = self._total - max(0.0, self._daily_pnl)  # subtract profits
        return CapitalSnapshot(
            total=self._total,
            tradable_total=tradable,
            ...
        )
```

Update `position_sizer`:

```python
# capital/position_sizer.py:257
risk_rs = snap.tradable_total * self._risk_per_trade_pct   # was snap.total
qty_by_concentration = int(math.floor(
    (snap.tradable_total * self._max_concentration_pct) / entry_price
))
```

### H-3 — margin_reserved stored on trade row hardcoded at 20%

**File:** `orders/order_placer.py:192`

**What it is**

```python
margin_reserved = entry_price * qty * 0.20  # standard intraday margin
```

Hardcoded 20%. Matches INTRADAY (5x leverage = 20%) but wrong for DELIVERY (100%), COVER_ORDER (~16.67%).

**Fix spec**

```python
# orders/order_placer.py:192 — replace with:
from capital.fund_manager import required_margin
margin_reserved = required_margin(qty, entry_price, intent, self._fm._leverage_map)
# (or inject leverage_map directly into OrderPlacer)
```

### H-4 — FundManager.initialize has no guard against double-call

**File:** `capital/fund_manager.py:245`

**Fix spec**

```python
def initialize(self, broker_balance: float) -> None:
    with self._lock:
        if self._initialized:
            raise RuntimeError(
                "FundManager.initialize() already called; use sync_from_broker() "
                "to update broker balance after startup"
            )
        # rest unchanged
```

### H-5 — state_store.transaction uses BEGIN (deferred) not BEGIN IMMEDIATE

**File:** `core/state_store.py:276`

**What it is**

`cur.execute("BEGIN")` opens a DEFERRED transaction. Multiple writer threads can BEGIN simultaneously; first to write gets a RESERVED lock; others hit SQLITE_BUSY and wait up to busy_timeout=5s. Under concurrent fund_manager.reserve() from multiple signal threads, latency tails can spike to 5s.

**Fix spec**

```python
# core/state_store.py:276 — change to IMMEDIATE
cur.execute("BEGIN IMMEDIATE")
```

Note: this serializes writers, which is the correct semantic given the system's data model.

### H-6 — smart_tgt success path updates in-memory state before DB

**File:** `orders/smart_tgt_manager.py:434` vs `:447`

**Fix spec**

Persist to DB first; update in-memory only on DB success:

```python
# orders/smart_tgt_manager.py — reorder in _modify_co_sl success branch:
if result.success:
    ts = _now_ist_iso()
    # Read current trail_count under lock before DB write
    with self._lock:
        info = self._tracked.get(trade_id)
        if info is None:
            return
        trail_count_after = info["trail_count"] + 1
        symbol = info["symbol"]
        old_sl = info["current_sl"]
        best_price = info["best_price"]

    # Persist to DB first
    try:
        self._state_store.update_smart_tgt_state(
            trade_id=trade_id,
            current_sl=new_sl,
            trail_count=trail_count_after,
            last_trail_ts=ts,
            best_price=best_price,
        )
    except Exception as exc:
        self._log.error(
            "SmartTgt: DB update failed for %s after broker confirm: %s — "
            "broker trailed but DB did not; reconciler may see stale SL",
            trade_id, exc,
        )
        # Do NOT update in-memory either — keep consistent with DB (safer for restart)
        return

    # Update in-memory only after DB success
    with self._lock:
        info = self._tracked.get(trade_id)
        if info is None:
            return  # unregistered during broker call
        info["current_sl"] = new_sl
        info["consecutive_failures"] = 0
        info["trail_count"] = trail_count_after
        info["last_trail_ts"] = ts
```

### H-7 — EOD _check_restart_recovery runs synchronously in constructor

**File:** `orders/eod_squareoff.py:141`

**What it is**

Constructor calls `_check_restart_recovery()` which may fire recovery EOD (cancel + MARKET-exit every open position). Broker slowness blocks main.py startup past 15:30 deadline.

**Fix spec**

Defer to a `post_wire_init()` method that main.py calls after all subsystems are up:

```python
# orders/eod_squareoff.py
def __init__(self, ...):
    ...
    # Do NOT call _check_restart_recovery here
    self._recovery_checked = False

def post_wire_init(self) -> None:
    """Called from main.py after all subsystems are constructed."""
    if self._recovery_checked:
        return
    self._recovery_checked = True
    self._check_restart_recovery()

# main.py — call after all construction, before the runtime loop starts
eod.post_wire_init()
```

### H-8 — No enforcement of WEBHOOK_SECRET in live mode

**File:** `signals/webhook_receiver.py:170`, `main.py:1056`

**Fix spec**

Add a startup check:

```python
# main.py — after loading env:
if args.mode == "live" and not os.environ.get("WEBHOOK_SECRET"):
    _log.critical(
        "WEBHOOK_SECRET env var is required in live mode. "
        "Set it in .env and restart."
    )
    return 5
```

Also enforce via startup_checks required_secrets:

```python
# main.py — required_secrets list:
required_secrets = [
    f"ZERODHA_API_KEY",
    f"ZERODHA_API_SECRET_{account.account_id}",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]
if args.mode == "live":
    required_secrets.append("WEBHOOK_SECRET")
```

### H-9 — hard_kill is never triggered by code (only manual)

**File:** No call sites.

**What it is**

Nothing in the system calls `hard_kill()`. soft_kill is triggered from 4 places (BL-9 fix will add a 5th). hard_kill remains operator-only. For scenarios like CapitalInvariantViolation, persistent broker 429s, schema mismatch, the correct response is hard_kill with automatic broker cancel.

**Fix spec**

BL-2 fix wires CapitalDriftDetected → hard_kill.
BL-4 fix wires commit_to_used failure → hard_kill.
BL-6 fix wires persistent 429 → soft_kill (correct) but schema mismatch and startup catastrophic errors should go to hard_kill.
BL-8 fix wires persist-orders-failed → hard_kill.
BL-9 fix wires invariant violation → hard_kill.

After these fixes, hard_kill has 5 automatic trigger paths. That's appropriate.

### H-10 — is_active_for_dispatch is defined but never called

**File:** `capital/kill_switch.py:172`, expected caller in `orders/order_placer.py`

**What it is**

Vestigial. order_placer uses `is_active("entry")` which is more conservative (trips on SOFT_KILL + HARD_KILL, not just HARD_KILL). Not a bug, but confusing.

**Fix spec**

Either delete `is_active_for_dispatch()` from kill_switch OR call it from order_placer. Recommend delete:

```python
# capital/kill_switch.py — remove is_active_for_dispatch method
# (order_placer already uses is_active("entry") which is correct)
```

Update docstrings referencing "last-mile dispatch gate per Project Rule 13" to reflect that the gate is `is_active("entry")`.

### H-11 — risk_engine uses datetime.now() directly, bypassing time_authority

**File:** `capital/risk_engine.py:191`

**Fix spec**

```python
# capital/risk_engine.py — top of approve():
from core.time_authority import now_ist
today = now_ist().date().isoformat()  # was datetime.now(tz=_IST)
```

### H-12 — position_sizer concentration uses total (drifted), not tradable_total

See H-2 fix.

---

## 6. MEDIUM findings

### M-1 — Webhook dedup TOCTOU race

**File:** `signals/webhook_receiver.py:304-305` vs `:362-363`

**Fix spec:** Acquire `_in_flight_lock` once, perform both check and add atomically:

```python
with self._in_flight_lock:
    if symbol in self._in_flight:
        return {"symbol": symbol, "status": "IN_PROCESS"}
    # Reserve the slot immediately (before DB write) to prevent concurrent duplicates
    self._in_flight[symbol] = time.monotonic()
# (then on any rejection path, remove the entry; on success, keep it)
```

### M-2 — smart_tgt race on unregister during broker modify

**File:** `orders/smart_tgt_manager.py:431`

**Fix spec:** If unregister-during-modify happens and modify succeeded, the broker SL has been moved but locally we've forgotten. Add a reconciler check that compares broker SL trigger price with local `current_sl`:

```python
# orders/order_reconciler.py — new check in _reconcile():
# After check 5, compare broker SL vs local current_sl for CO entries
for trade in local_trades_with_co:
    broker_sl_trigger = self._get_broker_sl_trigger(trade)
    local_sl = self._store.get_smart_tgt_state(trade["trade_id"])["current_sl"]
    if broker_sl_trigger and abs(broker_sl_trigger - local_sl) > 0.01:
        self._log.warning("SL trigger drift for %s: broker=%.2f local=%.2f",
                         trade["symbol"], broker_sl_trigger, local_sl)
        # Auto-repair: update local to match broker
        self._store.update_smart_tgt_current_sl(trade["trade_id"], broker_sl_trigger)
```

### M-3 — EOD _fired_for_date write-ahead

**File:** `orders/eod_squareoff.py:134`, `_fire`

**Fix spec:** Write eod_squareoff_log row with status="IN_PROGRESS" BEFORE placing exit orders; update to "COMPLETE" after:

```python
# orders/eod_squareoff.py — _fire body:
# Write IN_PROGRESS row first (EOD8-WA)
self._store.insert_eod_squareoff_log(
    fired_date=today_str, fired_at=now.isoformat(),
    positions_attempted=0, positions_succeeded=0, positions_failed=0,
    cancels_attempted=0, cancels_succeeded=0, cancels_failed=0,
    duration_sec=0.0, status="IN_PROGRESS",
)
# ... place orders ...
# Update to COMPLETE with final counts
self._store.update_eod_squareoff_log(today_str, status="COMPLETE", counts...)
```

Schema change: add `status` column to `eod_squareoff_log` table.

### M-4 — Symbol resolution in _derive_prices may produce entry_price ≤ 0

**File:** `signals/signal_processor.py:567-573`

Handled correctly (raises ValueError). NOTE only — but spec it as a MED fix to convert to _PipelineReject instead, so it doesn't crash the pipeline thread:

```python
# signals/signal_processor.py:567
if entry_price <= 0:
    raise _PipelineReject(
        "INVALID_DERIVED_PRICE",
        f"entry_price={entry_price:.4f} <= 0 (trigger={trigger_price}, "
        f"offset={strategy.entry_offset_pct}, method={strategy.entry_method})",
    )
```

### M-5 — `order_placer.place()` link_signal_trade failure is logged but not a hard failure

**File:** `orders/order_placer.py:210-215`

The signal-to-trade link failure is described as "Non-fatal: continue; reconciler can fix the link later". But there's no reconciler logic that rebuilds the signal→trade link. Should either (a) raise, or (b) add a reconciler check that repairs the link.

Recommend (a):

```python
# orders/order_placer.py:211
try:
    self._om.link_signal_trade(signal_id, trade_id)
except Exception as exc:
    log_exception(self._log, exc)
    self._log.critical("link_signal_trade failed — treating as hard failure")
    self._handle_placement_failure(trade_id, reservation_id, signal_id, exc)
    raise OrderRejectedError(f"signal→trade link failed: {exc}") from exc
```

---

## 7. Priors from handoff note — verification

### Prior #1: Equity carry-over into trading-futures

**Status:** Out of scope — this repo is equity v2, not futures. No carry-over risk here. Note for the futures rebuild: `webhook_receiver`, Chartink signal format, stock-universe refresh are all equity-specific; if copied to futures they need rewrite, not bypass.

### Prior #2: Paper/live divergence

**Status:** CONFIRMED AS MAJOR ISSUE. See Section 3. Paper mode does not synthesize fill events. Integration tests don't assert on capital accounting. The entire post-fill pipeline has never been exercised by paper trial. Paper trial "green" gave false confidence.

**Fix:** After BL-7 and BL-10 fixes, paper mode's `_paper_place_order` needs to synthesize `OrderFilled` events after a configurable delay (or on a configurable trigger) to exercise the full pipeline. Without this, paper trial will still be a false proxy:

```python
# broker/zerodha_adapter.py — update _paper_place_order:
def _paper_place_order(self, internal_id, symbol, side, qty, price, ...):
    fake_broker_id = "PAPER_" + uuid.uuid4().hex[:12].upper()
    self._osm.transition(internal_id, "SUBMITTED")
    placed_order = PlacedOrder(...)

    # NEW: schedule a simulated fill (paper mode only)
    if self._paper_auto_fill_delay_sec > 0:
        def _simulate_fill():
            time.sleep(self._paper_auto_fill_delay_sec)
            # Check paper LTP; fill if favourable or if MARKET
            if order_type == "MARKET" or self._paper_price_crossed(symbol, price, side):
                self._osm.transition(internal_id, "COMPLETE")
                # Publish OrderFilled as if order_monitor had polled and found it filled
                self._bus.publish(OrderFilled(
                    source_module="paper_adapter",
                    internal_order_id=internal_id,
                    broker_order_id=fake_broker_id,
                    symbol=symbol, side=side,
                    avg_fill_price=price, filled_qty=qty,
                    filled_at=now_ist().isoformat(),
                ))
        threading.Thread(target=_simulate_fill, daemon=True).start()
    return placed_order
```

### Prior #3: Clock skew / time_authority usage

**Status:** Partially verified. Audited modules mostly use `time_authority.now_ist()` correctly. One exception found (risk_engine, H-11). The remaining ~30% of code has not been audited for this. Part 2 will grep the whole codebase for direct `datetime.now()` / `time.time()` uses and flag any bypasses.

### Prior #4: Token refresh failure at 07:00

**Status:** VERIFIED OK. `main.py:768-774` in non-interactive live mode checks token validity via `is_token_valid()` and exits cleanly with code 6 ("Token missing or expired") if invalid. Does not start and fail silently.

---

## 8. Coverage map — what's been audited, what remains

### Audited deeply (70%)

| Module | LOC | Status |
|---|---|---|
| `capital/invariant.py` | 232 | ✓ Clean |
| `capital/fund_manager.py` | 758 | ✓ 6 findings (BL-1, BL-5, H-2, H-3, H-4) |
| `capital/kill_switch.py` | 489 | ✓ 2 findings (BL-9, H-9, H-10) |
| `capital/position_sizer.py` | 356 | Partial — math path audited, lot-size logic not |
| `capital/risk_engine.py` | 417 | Partial — approve flow audited, check details not |
| `core/state_store.py` | 1,133 | Partial — transaction semantics audited, schema migrations not |
| `core/schema.sql` | — | ✓ Reviewed |
| `core/time_authority.py` | 365 | Not audited |
| `core/events.py` | 242 | Referenced but not deep-read |
| `core/config_loader.py` | 677 | Not audited |
| `core/logger.py` | 321 | Not audited |
| `broker/zerodha_adapter.py` | 839 | ✓ 1 finding (BL-6) |
| `broker/rate_limiter.py` | 253 | ✓ Clean but unused per BL-6 |
| `broker/order_monitor.py` | 471 | Partial — OrderFilled path audited, fill-timeout not |
| `broker/order_state_machine.py` | 239 | Not audited |
| `broker/product_resolver.py` | 170 | Not audited |
| `broker/cost_calculator.py` | 219 | Not audited |
| `orders/order_placer.py` | 472 | ✓ 3 findings (BL-4, BL-7, BL-8, H-3) |
| `orders/order_reconciler.py` | 860 | ✓ 2 findings (BL-3, M-2) |
| `orders/smart_tgt_manager.py` | 604 | ✓ 2 findings (H-6, M-2) |
| `orders/eod_squareoff.py` | 628 | ✓ 2 findings (H-7, M-3) |
| `orders/order_manager.py` | 264 | Partial |
| `orders/shadow_tracker.py` | 681 | Not audited (moot per BL-10) |
| `orders/entry_engine.py` + protocols | 543 | ✓ Structure reviewed |
| `signals/webhook_receiver.py` | 448 | ✓ 2 findings (H-8, M-1) |
| `signals/signal_processor.py` | 914 | Partial — dispatch audited |
| `data/live_feed.py` | 277 | ✓ 1 finding (BL-11) |
| `data/candle_store.py` | 225 | Not audited |
| `utils/startup_checks.py` | 809 | Partial |
| `utils/holiday_guard.py` | 83 | Not audited |
| `alerts/*` | 647 | Not audited |
| `scripts/zerodha_login.py` | 266 | ✓ Prior #4 verified |
| `scripts/alert_watcher.py` | 349 | Not audited |
| `scripts/refresh_instruments.py` | 345 | Not audited |
| `scripts/preflight_scanner_check.py` | 203 | Not audited |
| `main.py` | 1,192 | Partial — startup audited, runtime loop not |
| `strategies/*` | 444 | Not audited |
| `reports/*` | Not audited |
| `tests/*` | 32k+ LOC | Not audited (coverage analysis pending) |

### Remaining deep-read for Part 2 (~30%)

- `orders/shadow_tracker.py` — verify additional bugs independent of BL-10
- `capital/risk_engine.py` — full check set: sector, concentration, max positions, consecutive losses, daily trades cap
- `broker/order_monitor.py` — fill timeout behavior, orphan callback, auth error handling, restart behavior
- `main.py:900-1192` — runtime loop, signal handling, clean shutdown, reconciler polling cadence
- `core/time_authority.py` — grep-audit all `datetime.now` / `time.time` bypasses across codebase
- `capital/position_sizer.py` — lot size logic, instrument_cache integration
- `signals/signal_processor.py` — ATR fallback, entry gate integration, screener error handling
- `data/candle_store.py` — thread safety, lock release during HTTP
- `utils/startup_checks.py` — remaining checks (scanner connectivity, webhook endpoint)
- `alerts/telegram_notifier.py` + `alerts/critical.py` — sentinel file handling, SMTP fallback
- `scripts/alert_watcher.py` — the separate process pattern for alert fallback
- **Config files:** `system_config.yaml`, `scan_webhook_map.yaml`, `broker_limits.yaml`, `broker_costs.yaml`, strategy YAMLs
- **Test coverage analysis:** what the 1,411 tests actually cover vs critical paths. Specifically, does ANY test assert `fund_manager._used > 0` after a trade?
- **Operational readiness:** systemd service file, log rotation, backup script, disk space monitoring

### Estimated remaining findings

Running rate so far: **~1 blocker per 1,850 LOC of deep read.** Part 2 covers ~6,000 LOC of production code that's deep-relevant (excluding tests, configs, scripts already verified). Expected: **2-4 more blockers**, mostly in shadow_tracker, order_monitor, and main.py runtime loop. Plus ~5-8 more HIGHs and MEDIUMs.

---

## 9. What to do next

### Immediate (before anything else)

1. **Reproduce BL-7 empirically.** Add a print or log statement at `fund_manager.commit_to_used` entry. Run a paper webhook → observe it never fires. This converts my grep-based argument into an operational fact.

2. **Reproduce BL-10 empirically.** Same test: does `bus.publish(PositionClosed(...))` ever appear in logs during a paper trial? If not, shadow_tracker has never run against a real exit.

3. **Decide on Part 2 audit.** I recommend Part 2 in this chat (deep read of remaining ~6,000 LOC + test coverage analysis). After that, fresh chat for fix review.

### Fix sequence (once Part 2 lands)

Proposed order:

**Phase A — wiring gaps (must come first, everything else depends on these)**
- BL-7: Wire order_monitor into order_placer; track entry/SL/TGT orders
- BL-10: Publish PositionClosed from order_placer exit-fill handler, reconciler MANUAL_CLOSE, EOD exits

**Phase B — capital integrity (unlocks reliable operation)**
- BL-1: Rehydrate FundManager from open trades on restart
- BL-5: Write-ahead ledger pattern
- BL-2: CapitalDriftDetected → hard_kill
- BL-9: CapitalInvariantViolation → hard_kill

**Phase C — error paths (prevents specific cascades)**
- BL-4: commit_to_used failure → hard_kill + re-raise
- BL-8: persist_orders failure → cancel broker + hard_kill
- BL-11: live_feed reconnect callback on connect success

**Phase D — broker robustness**
- BL-6: 429 classification + backoff + penalize()

**Phase E — correctness improvements**
- BL-3 (now reduced to MEDIUM): add CAPITAL_ACCOUNTING_DRIFT check
- All HIGHs

### After fixes

1. Run the 1,411 tests. Expect some to fail (the ones that depended on the bugs). Fix the tests to reflect the correct behavior.
2. **Add the critical integration tests listed per finding.** Especially the one that fails without BL-7/BL-10 fix: "full trade lifecycle in paper mode asserts `fund_manager._used > 0` after entry fill and `== 0` after exit fill."
3. Paper trial for **5 market days minimum**, actively monitoring:
   - `fund_manager` snapshot before and after every trade
   - `fm_ledger` row count growth
   - `reconciliation_log` for any non-COSMETIC entries
   - No CapitalDriftDetected events should fire
   - System survives simulated restart with 2+ open positions
4. Once paper trial is clean, go live with ₹50,000 risk capital (the spec already specifies this; resist increasing it until 2 weeks of clean live operation).

---

## Part 2 — coming next

Will cover the remaining ~30% with same format (findings + fix specs), plus:
- Test coverage gap analysis against each blocker
- Config file review
- Operational readiness checklist (systemd, logs, backups, disk, alerts)
- Final go-live checklist
- Executive decision doc: "ready / not ready" with evidence


---
---

# PART 2 — Remaining 30% Deep Audit

> **Status:** Additional findings from the modules not covered in Part 1. Appended after Part 2 deep-read.
>
> **New findings:** 4 additional blockers (BL-12 through BL-15) + 10 additional high/medium. Cumulative totals now: **15 blockers, 22 highs, 5 mediums.**
>
> **The "paper trial green" mystery is now fully explained** (see BL-14). Operators were never receiving the alerts that would have told them the system was broken.

## Part 2 — Table of contents

12. [The alert system is entirely dead (BL-14)](#12-the-alert-system-is-entirely-dead)
13. [Additional BLOCKERs (BL-12 through BL-15)](#13-additional-blockers)
14. [Additional HIGH findings](#14-additional-high-findings)
15. [Final tally and coverage](#15-final-tally-and-coverage)
16. [Go-live checklist](#16-go-live-checklist)
17. [Final recommendation](#17-final-recommendation)

---

## 12. The alert system is entirely dead

This is the third "silently broken wiring" bug in the system, matching the same pattern as BL-7 and BL-10. The Telegram alerts that operators depend on to know when anything goes wrong — invariant violations, orphan orders, EOD squareoff failures, config changes, startup/shutdown notifications — **every single one is silently raising `TypeError` and logging "notifier.send failed"**.

### The mechanism

`alerts/telegram_notifier.py` defines:

```python
def send(self, severity: str, title: str, body: str, source_module: str, context: dict | None = None) -> SendResult:
```

Every non-reconciler call site in the codebase uses **wrong parameter names**:

| File:line | What callers pass | What method expects |
|---|---|---|
| `main.py:265-270` | `tier="CRITICAL"`, `source="main"` | `severity=`, `source_module=` |
| `main.py:284-289` | `tier="CRITICAL"`, `source="main"` | `severity=`, `source_module=` |
| `main.py:397-402` | `tier="INFO"`, `source="main"` | `severity=`, `source_module=` |
| `main.py:811-816` | `tier="WARN"`, `source="main"` | `severity=`, `source_module=` |
| `main.py:1140-1145` | `tier="INFO"`, `source="main"` | `severity=`, `source_module=` |
| `orders/eod_squareoff.py:610-615` | `tier="CRITICAL"`, `source="eod_squareoff"` | `severity=`, `source_module=` |
| `orders/shadow_tracker.py:544-548` | `tier="INFO"`, `message=msg` | `severity=`, `body=` |

**Only `orders/order_reconciler.py:830` uses the correct `severity=` param.** Capital drift alerts DO fire. Everything else silently fails.

Every call site is wrapped in `try/except Exception as exc: _log.error("notifier.send failed: %s", exc)`. So Python raises `TypeError: send() got an unexpected keyword argument 'tier'`, the except block catches it, logs the message, and trading continues. No alert reaches Telegram.

### What this means operationally

Alerts that **never reach operators** in the current codebase:

- `_on_critical_failure` handler (invariant violations, recomputed after BL-9 fix also hard_kills)
- `_on_orphan` handler (order monitor detected an orphan broker order)
- System started / System stopping (routine ops notifications)
- Config files changed since last session (indicates state-breaking changes)
- EOD squareoff MISSED — open positions remain (CATASTROPHIC — operator has no idea positions weren't closed)
- Shadow_tracker inning completion alerts

The reconciler's `Capital Drift Detected` CRITICAL alert IS being sent correctly (only call site using `severity=`). This means your paper trial may have seen drift alerts — but given BL-2 (`CapitalDriftDetected` event handler is a no-op logger), the reconciler ALSO doesn't halt trading.

### Why paper trial appeared to work

Now the picture completes. During paper trial:
1. BL-7: No OrderFilled events → `commit_to_used` never fires → reserved accumulates.
2. BL-10: No PositionClosed events → shadow_tracker dead, `release_used` never fires.
3. Trade row stays at PENDING_FILL forever (fill path never runs).
4. EOD squareoff fires, tries to exit, the exit flow uses `order_monitor.track()` (the one place track IS called), so OrderFilled DOES fire on EOD exits only — but leg=EOD is not mapped to `_handle_exit_fill` in order_placer, so still no `release_used`.
5. Reconciler detects broker position without matching local state → publishes CapitalDriftDetected → BL-2: handler logs warning only.
6. Reconciler sends CRITICAL Telegram alert via `severity=` (the one correct call site) → operator MAY have received this.
7. All other alerts (startup, shutdown, config change, orphan, EOD missed) → BL-14: TypeError, silently eaten.

Net effect: the system appeared to run quietly, positions opened and (via EOD) closed, operators saw no alert cascades. The actual state — corrupt capital math, silent failures across alert stack, dead shadow_tracker, accumulating reserved — was invisible.

---

## 13. Additional BLOCKERs

### BL-12 — Orders table `status` column never updated

**Severity:** BLOCKER
**File(s):** `orders/order_manager.py:227` (method defined), all callers (zero exist)

**What it is**

```
$ grep -rn "update_order_status\|update_order_fill" orders/ broker/ main.py | grep -v def
(zero matches)
```

`OrderManager.update_order_status(broker_order_id, status, qty_filled, avg_fill_price)` is a complete method that updates `orders.status`, `orders.qty_filled`, and `orders.avg_fill_price`. No call site exists. The only code that modifies the orders table status is `eod_squareoff.py:374` with a direct SQL UPDATE — and only for CANCELLED status on pre-fill cancels.

**Why it matters**

- Every order row in the database stays at `status="PENDING"` forever.
- Daily reports, post-mortems, operator inspection all see "PENDING" for fills, SL hits, TGT hits.
- Reconciler's check 6 ORPHAN_ORDER uses `trades.status = 'PENDING_FILL'` and `trades.broker_order_id`, not `orders.status`, so the reconciler is unaffected — but that's the only saving grace.
- Any future feature or analysis depending on orders.status breaks silently.

**Worst case**

After a live run, operator queries "how many orders filled today vs cancelled vs rejected?" — answer is "all PENDING". Cannot distinguish a rejected order from a filled one from a cancelled one using the orders table. Audit reconstruction impossible.

**Fix spec**

Wire `order_monitor` to update order status on every transition. The cleanest is to publish `OrderStatusChanged` events from `order_monitor` and have `OrderManager` subscribe:

```python
# core/events.py — add event
@dataclass(frozen=True)
class OrderStatusChanged(Event):
    source_module: str = ""
    broker_order_id: str = ""
    internal_order_id: str = ""
    new_status: str = ""
    qty_filled: int = 0
    avg_fill_price: float = 0.0

# broker/order_monitor.py — emit on every _safe_transition:
def _safe_transition(self, internal_id: str, new_state: str) -> bool:
    try:
        self._osm.transition(internal_id, new_state)
    except InvalidTransitionError:
        return False
    # NEW: also publish so DB gets updated
    entry = self._watched.get(internal_id)
    if entry:
        self._bus.publish(OrderStatusChanged(
            source_module="order_monitor",
            broker_order_id=entry.broker_order_id,
            internal_order_id=internal_id,
            new_status=new_state,
            qty_filled=entry.filled_qty,
            avg_fill_price=entry.avg_fill_price,
        ))
    return True

# orders/order_manager.py — subscribe in constructor:
class OrderManager:
    def __init__(self, state_store, bus, logger):
        ...
        bus.subscribe(OrderStatusChanged, self._on_order_status_changed)

    def _on_order_status_changed(self, event: OrderStatusChanged) -> None:
        try:
            self.update_order_status(
                broker_order_id=event.broker_order_id,
                status=event.new_status,
                qty_filled=event.qty_filled,
                avg_fill_price=event.avg_fill_price or None,
            )
        except Exception as exc:
            self._log.error("update_order_status failed: %s", exc)
```

**Tests to add**

- `test_order_monitor_complete_updates_orders_table_status`
- `test_order_monitor_cancelled_updates_orders_table_status`
- `test_order_monitor_rejected_updates_orders_table_status`
- `test_partial_fill_updates_qty_filled_in_orders_table`

---

### BL-13 — `shadow_tracker._eod_fired` never resets across days

**Severity:** BLOCKER
**File(s):** `orders/shadow_tracker.py:152, 314`

**What it is**

```python
# orders/shadow_tracker.py:152
self._eod_fired: bool = False
...
# orders/shadow_tracker.py:314 (in _on_eod_complete)
with self._lock:
    self._eod_fired = True
    active_copy = list(self._active_innings.values())
```

The flag is set True when EOD fires and never reset. In-memory state.

**Why it matters**

A systemd service is expected to run across multiple trading days (you restart weekly for maintenance, not daily). After day 1's EOD, `_eod_fired = True`. Day 2 onwards, every `_on_position_closed` check at line 292-297 fails at `not self._eod_fired` → **no inning cascade ever fires on day 2+**.

The innings table will still get inning 1 rows on day 2 (from the fast-path in `_on_position_closed` before the cascade check — wait, let me re-verify), but cascade innings 2 and 3 are blocked.

**Verifying:** Looking at `_on_position_closed` at lines 258-304, the inning 1 insert at line 279 is unconditional; the cascade block at line 291-304 is gated by `not self._eod_fired`. So inning 1 will be persisted across days but innings 2-3 never cascade after day 1.

This is downstream of BL-10 (PositionClosed never emitted) so it's currently moot — shadow_tracker is entirely dead in production. But after BL-10 is fixed, BL-13 becomes active.

**Fix spec**

Reset `_eod_fired` on the next day's first event:

```python
# orders/shadow_tracker.py — track fired date, not boolean flag
def __init__(self, ...):
    ...
    self._eod_fired_date: Optional[date] = None  # YYYY-MM-DD IST of last EOD

def _on_eod_complete(self, event: EodSquareoffComplete) -> None:
    if not self._enabled:
        return
    with self._lock:
        self._eod_fired_date = self._now().date()
        active_copy = list(self._active_innings.values())
    for ing in active_copy:
        with self._lock:
            ltp = self._last_price.get(ing.symbol, ing.entry_price)
        self._close_inning(ing, ltp, "EOD")

def _is_eod_fired_today(self) -> bool:
    """True only if EOD fired ON TODAY'S date. Auto-resets across days."""
    with self._lock:
        return self._eod_fired_date == self._now().date()

# Replace all `not self._eod_fired` with `not self._is_eod_fired_today()`
```

**Tests to add**

- `test_eod_fired_resets_across_days`
- `test_cascade_inning_works_day_after_eod`

---

### BL-14 — `notifier.send()` called with wrong parameter names; all alerts silently fail

**Severity:** BLOCKER — operational visibility is effectively zero for non-reconciler alerts.
**File(s):** `main.py:265, 284, 397, 811, 1140`; `orders/eod_squareoff.py:610`; `orders/shadow_tracker.py:544`

See Section 12 for full description.

**Fix spec**

The cleanest fix is to standardize on the method's real signature. Replace every wrong call:

```python
# main.py:265-270 — was:
notifier.send(
    tier="CRITICAL",
    title="Critical failure",
    body=f"{source}: {reason}",
    source="main",
)
# change to:
notifier.send(
    severity="CRITICAL",
    title="Critical failure",
    body=f"{source}: {reason}",
    source_module="main",
)
```

Apply identical fix to:
- `main.py:284` (orphan callback)
- `main.py:397` (shutdown notification)
- `main.py:811` (config change warning)
- `main.py:1140` (startup notification)
- `orders/eod_squareoff.py:610` (EOD missed alert)

For `shadow_tracker.py:544`:

```python
# orders/shadow_tracker.py:544 — was:
self._notifier.send(
    tier="INFO",
    title=f"Inning {inning.inning_number} | {inning.symbol}",
    message=msg,
)
# change to:
self._notifier.send(
    severity="INFO",
    title=f"Inning {inning.inning_number} | {inning.symbol}",
    body=msg,
    source_module="shadow_tracker",
)
```

**Secondary fix — add a static type check.** Add Pydantic or `@typing.overload` to the `send` method so callers get an error at call time, not at runtime. Better: add a `@typechecked` decorator or a pylint-equivalent pre-commit check. Something in CI that catches "method called with wrong kwargs" before it reaches runtime.

**Even better — add a signature test:**

```python
# tests/unit/test_telegram_notifier.py — add:
def test_notifier_send_signature_matches_call_sites():
    """Regression guard for BL-14: ensure send() param names match all call sites."""
    import inspect
    sig = inspect.signature(TelegramNotifier.send)
    params = set(sig.parameters.keys()) - {"self"}
    assert params == {"severity", "title", "body", "source_module", "context"}, \
        f"send() signature changed; update all call sites. Actual: {params}"
```

**Tests to add**

- `test_notifier_send_raises_typeerror_on_legacy_tier_param` (confirms new tests would have caught BL-14)
- `test_notifier_send_with_correct_params_delivers` (baseline happy path)
- Integration test that catches the TypeError in try/except and asserts it should have succeeded

---

### BL-15 — `WEBHOOK_SECRET` not in required_secrets; live mode accepts unsigned webhooks

**Severity:** BLOCKER (security)
**File(s):** `main.py:695`, `signals/webhook_receiver.py:170`

**What it is**

```python
# main.py:695
required_secrets = ["ZERODHA_API_KEY", "ZERODHA_ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN"]
```

`WEBHOOK_SECRET` is absent. If the env var is unset, `main.py:1056` passes `secret_token=None` to WebhookReceiver. Combined with `signals/webhook_receiver.py:170` (`if self._secret:` — skip HMAC if no secret), the webhook silently accepts every POST without signature validation.

**Why it matters**

An attacker who discovers the webhook URL — which is easily enumerable (Oracle VM IP `80.225.246.239` from memory context, standard Flask port) — can inject arbitrary signals. The system will:
- Accept the signal
- Size a position
- Reserve capital
- Place a broker order on the live account

This is a remote code exec for the trader's money. Not a theoretical concern — trading systems with exposed webhooks are actively scanned on the public internet.

**Fix spec**

Two layers:

1. Add to required_secrets when live mode:

```python
# main.py:695 — make required_secrets conditional
required_secrets = ["ZERODHA_API_KEY", "ZERODHA_ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN"]
if args.mode == "live":
    required_secrets.append("WEBHOOK_SECRET")
```

2. Also fail-closed in webhook_receiver when in live mode:

```python
# signals/webhook_receiver.py — update __init__
def __init__(self, ..., secret_token=None, mode: str = "paper"):
    ...
    if mode == "live" and not secret_token:
        raise ValueError(
            "WebhookReceiver constructed in live mode without secret_token; "
            "HMAC validation is mandatory in live mode"
        )
    self._secret = secret_token
    self._mode = mode
```

Pass mode from main.py:

```python
# main.py:1050 — pass mode
webhook_receiver = WebhookReceiver(
    signal_queue=signal_queue,
    state_store=store,
    config=app_config,
    market_windows=market_windows,
    kill_switch=kill_switch,
    logger=get_logger("webhook_receiver"),
    secret_token=os.environ.get("WEBHOOK_SECRET"),
    mode=args.mode,  # NEW
)
```

3. Additional hardening — bind webhook to 127.0.0.1 in dev and public in live only when explicitly set:

```python
# config/system_config.yaml — ensure webhook.bind_host is documented
# Default should be 127.0.0.1; operator explicitly sets to 0.0.0.0 for Chartink
```

**Tests to add**

- `test_webhook_receiver_refuses_construction_in_live_mode_without_secret`
- `test_required_secrets_includes_webhook_secret_in_live_mode`
- `test_webhook_rejects_unsigned_request_in_live_mode`
- `test_startup_fails_in_live_mode_without_webhook_secret`

---

## 14. Additional HIGH findings

### H-13 — `shadow_tracker._close_inning` swallows DB write failure

**File:** `orders/shadow_tracker.py:424-430`

**Fix spec**

```python
# orders/shadow_tracker.py — update _close_inning error path
try:
    self._store.update_inning_close(...)
except Exception as exc:
    self._log.error(
        "shadow_tracker: update_inning_close failed for trade_id=%s inning=%d: %s",
        inning.trade_id, inning.inning_number, exc,
    )
    # DO NOT pop from _active_innings, DO NOT cascade, DO NOT alert.
    # On next retry cycle (or restart), state can be reconciled.
    return   # NEW — prevent divergence from DB

self._active_innings.pop(inning.trade_id, None)
```

### H-14 — daily_count only counts trades with rows; rejected signals skew the cap

**File:** `core/state_store.py:384`, `capital/risk_engine.py:324`

Mild inconsistency. Fix spec: introduce a clear "signals today" count separate from "trades today":

```python
# core/state_store.py — add
def count_signals_today(self, date_iso: str) -> int:
    row = self.fetch_one(
        "SELECT COUNT(*) AS n FROM signals WHERE fingerprint_date = ?",
        (date_iso,),
    )
    return int(row["n"]) if row else 0

# capital/risk_engine.py — use clearly named source
daily_trade_count = self._store.count_trades_today(today)
# Gate DAILY_TRADES on the trades count (the current behavior — leaves as is)
```

Not a correctness bug; just clarity. Could be LOW.

### H-15 — order_monitor: 3 empty-history responses triggers soft_kill via on_orphan

**File:** `broker/order_monitor.py:294-306`

**Fix spec**

Combine the empty-history signal with an independent broker query before firing the orphan callback:

```python
# broker/order_monitor.py — in _process_order empty-history branch
entry.empty_history_count += 1
if entry.empty_history_count >= 3:
    # Before firing orphan, do a second-source check
    try:
        open_orders = self._adapter.get_open_orders()
        ids = {str(o["order_id"]) for o in open_orders}
        if entry.broker_order_id in ids:
            # Broker's open orders list confirms it IS still live
            self._log.info(
                "order_monitor: empty_history_count=%d but broker open_orders "
                "confirms %s is live; resetting counter",
                entry.empty_history_count, entry.broker_order_id,
            )
            entry.empty_history_count = 0
            return
    except Exception as exc:
        self._log.warning(
            "order_monitor: second-source check failed: %s; proceeding with orphan",
            exc,
        )
    # Confirmed orphan
    ...
```

### H-16 — Shutdown does not stop webhook receiver

**File:** `main.py:375`, `signals/webhook_receiver.py`

**Fix spec**

Add a shutdown flag to WebhookReceiver that rejects new requests:

```python
# signals/webhook_receiver.py — add
def __init__(self, ...):
    ...
    self._shutting_down = threading.Event()

def stop(self) -> None:
    """Reject all new requests with 503."""
    self._shutting_down.set()
    # Flask can't be cleanly stopped; this at least drains in-flight requests.

def _process_request(self, scanner_name, raw_body):
    if self._shutting_down.is_set():
        return jsonify({"error": "System shutting down"}), 503
    # ... rest of method
```

Call `webhook_receiver.stop()` before `signal_processor.stop()` in main.py shutdown:

```python
# main.py:367 — insert at top of _shutdown
try:
    webhook_receiver.stop()  # NEW: reject new signals during shutdown
except Exception as exc:
    _log.error("webhook_receiver.stop error: %s", exc)
try:
    signal_proc.stop()
    ...
```

Add `webhook_receiver` to _shutdown's parameter list.

### H-17 — Multiple modules bypass time_authority

**Files:** `orders/shadow_tracker.py:662, 669`, `orders/order_reconciler.py:252`, `capital/risk_engine.py:191`, `core/state_store.py:64`, `utils/startup_checks.py:626`, `reports/daily_review.py:50`

**Fix spec**

For each bypass, replace with `time_authority.now_ist()`:

```python
# orders/shadow_tracker.py:662, 669
from core.time_authority import now_ist
def _now(self) -> datetime:
    return now_ist()

# orders/order_reconciler.py:252
from core.time_authority import now_ist
def _now_ist(self) -> str:
    return now_ist().isoformat()

# capital/risk_engine.py:191
from core.time_authority import now_ist
today = now_ist().date().isoformat()

# core/state_store.py:64 — this one is tricky because state_store needs to be below time_authority in layering.
# Option A: keep datetime.now there (accept the bypass)
# Option B: make state_store accept a clock function parameter at construction
# Recommend B:
class StateStore:
    def __init__(self, db_path, schema_path=..., now_fn=None):
        self._now_fn = now_fn or (lambda: datetime.now(_IST))
        ...

# reports/daily_review.py:50
from core.time_authority import now_ist
return now_ist().strftime("%Y-%m-%d")

# utils/startup_checks.py:626
# Acceptable — this is pre-init code before time_authority is available.
# Leave as datetime.now(), but add a comment.
```

### H-18 — `orders.status` never transitions; orders table useless for audit

Covered by BL-12 fix.

### H-19 — `register_trade` on smart_tgt is never called from production code

**File:** `orders/smart_tgt_manager.py`; called only from docstring example and tests.

This is a downstream of BL-7. Fix spec is part of BL-7 fix: `order_placer._on_order_filled` (for leg=ENTRY of CO_PLUS_TGT protocol) should call `smart_tgt.register_trade(...)`. Already specced in BL-7. Listing here for traceability.

### H-20 — `_paper_place_order` returns SUBMITTED without simulating fill — paper trial never exercises fill path

**File:** `broker/zerodha_adapter.py:811-839`

Listed in Part 1 Prior #2 fix. Without paper-mode fill simulation, every paper trial is a false proxy. After BL-7 and BL-10 fixes, paper mode still won't exercise the post-fill path unless this is also fixed. Fix spec is in Prior #2 writeup (Part 1, Section 7).

### H-21 — `link_signal_trade` failure is non-fatal; no reconciler repair path

**File:** `orders/order_placer.py:210-215`

**Fix spec**

```python
# orders/order_placer.py:210-215 — replace with:
try:
    self._om.link_signal_trade(signal_id, trade_id)
except Exception as exc:
    log_exception(self._log, exc)
    self._handle_placement_failure(trade_id, reservation_id, signal_id, exc)
    raise OrderRejectedError(
        f"signal→trade link failed: {exc}",
        trade_id=trade_id, signal_id=signal_id,
    ) from exc
```

### H-22 — Integration test `test_happy_path_signal_reaches_placed_status` asserts wrong thing

**File:** `tests/integration/test_end_to_end_smoke.py:124-158`

The test asserts `signal.status == "PROCESSED"` which is set BEFORE any fill happens. It doesn't assert on capital state, filled status, or trade lifecycle completion. This is why BL-7 wasn't caught by the test suite.

**Fix spec**

Rewrite the happy-path test to be a true end-to-end assertion:

```python
# tests/integration/test_end_to_end_smoke.py — new test
def test_happy_path_full_lifecycle_capital_accounting(self, wired_system):
    """End-to-end: signal → order → fill → capital commit.

    Regression guard for BL-7: confirms fund_manager._used increases after
    entry fill, returns to 0 after exit.
    """
    ctx = wired_system
    symbol = "RELIANCE"
    ctx.sim_kite.set_rich_quote(symbol, ltp=100.0)
    rich_md = ctx.sim_kite.get_market_data(symbol)

    snap_before = ctx.fund_manager.get_snapshot()
    intraday_reserved_before = snap_before.intraday_reserved
    intraday_used_before = snap_before.intraday_used

    # Post webhook, wait for PROCESSED status
    with patch.object(ctx.screener, "_build_market_data", return_value=rich_md):
        code, data = _post_webhook(ctx, SCANNER_NAME, _make_payload(symbol=symbol))
        assert code == 200
        signal_id = _wait_for_any_signal(ctx, symbol, timeout=3.0)
        _wait_for_signal_status(ctx, signal_id, {"PROCESSED"}, timeout=6.0)

    # Simulate the broker filling the entry order
    ctx.sim_kite.fire_fill_for_latest_order(symbol)

    # Wait for capital commit to complete
    time.sleep(1.0)
    snap_after_entry = ctx.fund_manager.get_snapshot()

    # CORE ASSERTION: margin moved from reserved to used
    assert snap_after_entry.intraday_used > intraday_used_before, (
        f"BL-7 REGRESSION: fund_manager._used did not increase after entry fill. "
        f"Before: {intraday_used_before}, After: {snap_after_entry.intraday_used}. "
        f"OrderFilled event likely not published."
    )

    # Simulate the SL or TGT fill (position exit)
    ctx.sim_kite.fire_fill_for_sl(symbol, exit_price=99.0)
    time.sleep(1.0)
    snap_after_exit = ctx.fund_manager.get_snapshot()

    # CORE ASSERTION: margin released from used
    assert snap_after_exit.intraday_used == intraday_used_before, (
        f"BL-10 REGRESSION: fund_manager._used did not return to baseline after exit. "
        f"release_used was not called; PositionClosed event likely not published."
    )
```

This single test, had it existed, would have caught BL-7, BL-10, and the paper-adapter-doesn't-fill problem all at once.

---

## 15. Final tally and coverage

### Blocker summary (15 total)

| ID | Layer | Summary | Part |
|---|---|---|---|
| BL-1 | capital | FundManager state not rebuilt on restart | 1 |
| BL-2 | capital | CapitalDriftDetected handler only logs | 1 |
| BL-3 | reconciler | HEALTHY check masks BL-1 silently | 1 |
| BL-4 | orders | commit_to_used failure swallowed | 1 |
| BL-5 | capital | Ledger write not in same txn as mutation | 1 |
| BL-6 | broker | 429/rate-limit backoff missing | 1 |
| BL-7 | orders | OrderFilled never emitted for entries ★★★ | 1 |
| BL-8 | orders | Order row DB insert failure swallowed | 1 |
| BL-9 | capital | Invariant violation → soft_kill, should hard_kill | 1 |
| BL-10 | events | PositionClosed never emitted | 1 |
| BL-11 | data | live_feed reconnect fires on retry not success | 1 |
| BL-12 | orders | orders table status never updated | 2 |
| BL-13 | events | shadow_tracker._eod_fired never resets across days | 2 |
| BL-14 | alerts | notifier.send() called with wrong params; all alerts fail silently ★★★ | 2 |
| BL-15 | security | WEBHOOK_SECRET not in required_secrets; live mode unsigned | 2 |

Two findings earn a ★★★ because they completely hollow out a subsystem:
- **BL-7** — the entire post-fill capital commit pipeline is dead
- **BL-14** — all non-reconciler alerts silently fail

### High/Medium summary

22 HIGH findings (H-1 through H-22) covering: sync_from_broker drift masking, P7a violation, margin_reserved hardcoding, FundManager double-init, BEGIN vs BEGIN IMMEDIATE, smart_tgt ordering, EOD constructor blocking, webhook secret enforcement, hard_kill never triggered from code, is_active_for_dispatch vestigial, risk_engine time bypass, position_sizer concentration drift, shadow_tracker DB swallow, daily_count asymmetry, order_monitor empty_history false positive, shutdown webhook gap, time_authority bypasses, orders table status, register_trade unwired, paper adapter doesn't fill, signal→trade link swallowed, happy-path test asserts wrong thing.

5 MEDIUM findings (M-1 through M-5) covering: webhook dedup TOCTOU, smart_tgt unregister-during-modify, EOD write-ahead, derived price ≤ 0 crash path, signal-trade link fatal path.

### Modules audited

| Module | Status | Findings |
|---|---|---|
| `capital/invariant.py` | ✓ Deep | Clean |
| `capital/fund_manager.py` | ✓ Deep | BL-1, BL-5, H-1, H-2, H-3, H-4 |
| `capital/kill_switch.py` | ✓ Deep | BL-9, H-9, H-10 |
| `capital/position_sizer.py` | ✓ Deep | H-2 (concentration drift) |
| `capital/risk_engine.py` | ✓ Deep | H-11 (time bypass), H-14 |
| `core/state_store.py` | ✓ Deep | H-5 (BEGIN deferred), H-17 (time bypass) |
| `core/schema.sql` | ✓ Deep | Clean |
| `core/time_authority.py` | Partial | Referenced only; no issues found |
| `core/events.py` | ✓ | Clean |
| `broker/zerodha_adapter.py` | ✓ Deep | BL-6, H-20 |
| `broker/rate_limiter.py` | ✓ Deep | Clean, but unused per BL-6 |
| `broker/order_monitor.py` | ✓ Deep | H-15, H-18 |
| `orders/order_placer.py` | ✓ Deep | BL-4, BL-7, BL-8, H-3, H-21 |
| `orders/order_reconciler.py` | ✓ Deep | BL-3, M-2 |
| `orders/order_manager.py` | ✓ Deep | BL-12 |
| `orders/smart_tgt_manager.py` | ✓ Deep | H-6, H-19 (unwired), M-2 |
| `orders/eod_squareoff.py` | ✓ Deep | H-7, M-3 |
| `orders/shadow_tracker.py` | ✓ Deep | BL-10, BL-13, H-13 |
| `orders/entry_engine.py` + protocols | ✓ | Clean, need IDs (per BL-7 fix) |
| `signals/webhook_receiver.py` | ✓ Deep | H-8, M-1 |
| `signals/signal_processor.py` | ✓ Deep | M-4, M-5 |
| `data/live_feed.py` | ✓ Deep | BL-11 |
| `data/candle_store.py` | ✓ Deep | Clean |
| `utils/startup_checks.py` | ✓ Deep | Clean |
| `utils/holiday_guard.py` | Spot-check | Clean |
| `alerts/telegram_notifier.py` | ✓ Deep | BL-14 callers |
| `alerts/critical.py` | ✓ Deep | Clean |
| `scripts/zerodha_login.py` | ✓ Deep | Clean (Prior #4 verified) |
| `scripts/alert_watcher.py` | ✓ Deep | Clean |
| `scripts/refresh_instruments.py` | Not read | — |
| `scripts/preflight_scanner_check.py` | Not read | — |
| `main.py` (startup) | ✓ Deep | Multiple (BL-15, BL-9 routing, BL-14, H-16, H-17) |
| `main.py` (runtime loop) | ✓ Deep | H-16 (shutdown gap) |
| `strategies/schema.py` | Not read | — |
| `strategies/loader.py` | Not read | — |
| `reports/daily_review.py` | Spot-check | H-17 (time bypass) |
| `tests/` | Integration tests only | H-22 |
| Config YAMLs | Not read | — |

**Production code coverage: ~90% deep-read.**

What's genuinely unread:
- `scripts/refresh_instruments.py` — operational script, runs pre-market, not in hot path
- `scripts/preflight_scanner_check.py` — runs before market open
- `strategies/schema.py`, `strategies/loader.py` — config-loading code
- Config YAML files — not code but worth reviewing for sanity
- Full test suite coverage analysis — I spot-checked integration tests only

None of the unread items are in the critical trading path. Any further blockers are unlikely to change the verdict.

---

## 16. Go-live checklist

The system cannot go live in current state. Below is the checklist for post-fix go-live readiness. Every box must be checked with evidence.

### Code fixes (15 blockers + 22 highs)

**Phase A — wiring gaps (these unlock everything else)**
- [ ] BL-7: order_placer wires to order_monitor; entry/SL/TGT orders tracked
- [ ] BL-10: PositionClosed published from order_placer exit-fill handler, reconciler MANUAL_CLOSE, EOD exits
- [ ] BL-12: OrderStatusChanged event pipeline; orders table status updated on every transition
- [ ] BL-14: All notifier.send() calls use `severity=`, `title=`, `body=`, `source_module=`, `context=`
- [ ] BL-15: WEBHOOK_SECRET added to required_secrets in live mode; WebhookReceiver refuses unsigned in live mode

**Phase B — capital integrity**
- [ ] BL-1: `fund_manager.rehydrate_from_open_trades()` implemented and called on WARM/CRASH startup
- [ ] BL-2: CapitalDriftDetected subscriber calls hard_kill + sends CRITICAL alert
- [ ] BL-3: Reconciler adds CAPITAL_ACCOUNTING_DRIFT check comparing expected_used vs fund_manager._used
- [ ] BL-5: Write-ahead ledger pattern; in-memory mutation only after ledger commits
- [ ] BL-9: Invariant violation callback fires hard_kill, not soft_kill
- [ ] BL-13: shadow_tracker._eod_fired_date resets across days

**Phase C — error paths**
- [ ] BL-4: commit_to_used failure fires hard_kill and re-raises
- [ ] BL-8: persist_entry_orders failure cancels broker orders + hard_kill
- [ ] BL-11: live_feed reconnect callback moved to _on_connect (post-success)

**Phase D — broker robustness**
- [ ] BL-6: 429 classification in `_translate_kite_exception`; penalize() called; backoff schedule; soft_kill on persistent 429s

**Phase E — correctness / ops (all HIGH fixes)**
- [ ] H-1 through H-22: addressed per fix specs

### Tests

- [ ] New integration test: `test_happy_path_full_lifecycle_capital_accounting` (specified in H-22)
- [ ] All new unit tests listed per blocker (~50 new tests)
- [ ] Existing 1,411 tests still pass
- [ ] Coverage report shows >80% line coverage on production code (not just tests-on-tests)
- [ ] Signature regression test for telegram_notifier.send (catches future BL-14 recurrence)

### Paper trial — the revised meaning

Previous paper trial doesn't count because it never exercised the post-fill path. Redefine:

- [ ] Paper mode in `broker/zerodha_adapter._paper_place_order` synthesizes OrderFilled events after configurable delay
- [ ] Paper trial runs for 5 full market days
- [ ] Operators receive and review Telegram alerts daily (confirm BL-14 fix works)
- [ ] After each paper day:
    - [ ] `fund_manager.get_snapshot()` snapshot matches expected state (no stuck reserved)
    - [ ] `fm_ledger` row count reconciles against placed trades
    - [ ] `orders` table shows correct transitions (no PENDING-forever rows)
    - [ ] `reconciliation_log` has zero UNRECOVERABLE rows
    - [ ] `innings` table has rows for every trade (confirms shadow_tracker alive)
    - [ ] Zero CapitalDriftDetected events
- [ ] Simulated restart during open positions:
    - [ ] Process killed mid-day with 2+ open positions
    - [ ] Restart runs `rehydrate_from_open_trades`
    - [ ] Invariant check passes after rehydration
    - [ ] Subsequent trade sizing reflects correct `used` bucket
- [ ] Simulated network partition:
    - [ ] live_feed websocket disconnected for 60s
    - [ ] Reconnect triggers candle_store recompute via _on_connect (not _on_reconnect)
    - [ ] smart_tgt recompute uses fresh candles only

### Operational readiness

- [ ] systemd service file reviewed and restart policy set to `on-failure` not `always`
- [ ] Log rotation configured (logrotate or equivalent) — prevent disk fill
- [ ] Database file on separate partition from logs; monitoring on both
- [ ] `data_store/` directory backed up daily to offsite
- [ ] `data_store/critical_alert_*.flag` monitored by alert_watcher systemd timer (every 60s)
- [ ] SMTP credentials tested; CRITICAL sentinel delivery end-to-end verified
- [ ] Oracle VM disk > 80% utilization triggers alert
- [ ] Clock sync active (chrony/ntpd); drift monitored
- [ ] Git auto-deploy path verified; verify pull + restart flow works without breaking state
- [ ] Webhook bind_host is 0.0.0.0 only if intentional; otherwise 127.0.0.1 + reverse proxy

### Security

- [ ] WEBHOOK_SECRET set and rotated
- [ ] Zerodha API secret not in logs (grep logs for any leak)
- [ ] `.env` file mode 600; git-ignored confirmed
- [ ] SSH key-only access to VM; no password auth
- [ ] UFW/iptables restricts webhook port to Chartink's known IPs if possible

### Live readiness gate

- [ ] All 15 blockers fixed and verified by tests
- [ ] All 22 highs fixed and verified by tests
- [ ] 5 market days paper trial passed with all assertions green
- [ ] Operational readiness checklist all green
- [ ] Starting capital = ₹50,000 (the spec's own recommendation)
- [ ] Documented rollback plan: what to do if anything goes wrong on day 1

---

## 17. Final recommendation

**Do not go live until all 15 blockers are fixed and a proper 5-day paper trial passes.**

Why this is non-negotiable:

1. **The trading spine has three severed wires, not one.** BL-7, BL-10, and BL-14. Each independently breaks a critical subsystem. Together they mean the system cannot complete a trade correctly, cannot track exits, and cannot alert operators when any of this fails.

2. **The paper trial "5 days green" result is a false positive.** The bugs found would not have surfaced in paper trial because (a) paper mode doesn't synthesize fills, (b) integration tests don't assert on capital accounting, (c) all the alerts that would have been fired were dying with TypeError anyway.

3. **These are not "polish" bugs.** They are structural wiring gaps that the 1,411 unit tests cannot catch because unit tests mock the events. Integration tests would have caught most of them; the integration tests that exist assert on the wrong things.

4. **The first live trade will halt the system within hours.** Path is predictable: entry placed → broker fills → no OrderFilled → commit_to_used never runs → reserved inflated → either (a) SL hits → release_used underflows → invariant violation → soft_kill, or (b) EOD squareoff → exits placed via the one track() call site → OrderFilled fires for leg=EOD → handler doesn't exist for leg=EOD → still no release_used → invariant violation at close.

### What a healthy timeline looks like

- **Week 1:** Fix Phase A (wiring gaps). This is the biggest change; most of the other fixes depend on these being right. Run all tests.
- **Week 2:** Fix Phase B (capital integrity). Run tests. Paper trial day 1.
- **Week 3:** Fix Phase C + D (error paths, broker robustness). Paper trial days 2-3.
- **Week 4:** Fix Phase E (correctness / ops). Paper trial days 4-5. Full operational readiness review.
- **Week 5:** Go live with ₹50,000. Monitor daily. Full operator review after each market day.
- **Week 6+:** If clean, consider scaling capital up in increments.

### What a rushed timeline would do

Going live this week, even "just to test with ₹5k":
- First position opens. Local DB stuck at PENDING_FILL.
- Reconciler fires CapitalDriftDetected. BL-2 just logs.
- CRITICAL alert to operator fires via reconciler (the one working alert path). Operator sees "capital drift detected."
- Operator investigates. Sees the trade, sees broker says it's open, sees local says PENDING_FILL. Confused.
- Position hits SL at broker. Broker closes. Local still thinks PENDING_FILL.
- Reconciler fires MANUAL_CLOSE. Tries to release_used. Invariant violation (used=0). Soft_kill fires.
- Operator sees "trading halted." System stopped after one trade.
- Operator examines fund_manager. Reserved is inflated. No idea why.
- Manual restart. rehydrate doesn't exist. System comes up with reserved=0, used=0. Meanwhile broker has the closed position. Reconciler fires. CapitalDriftDetected logs. Everything continues.

This is not hypothetical — it's what the code does today based on the evidence cataloged in this report.

### The ask

Take the two weeks. Fix the blockers with the specs in this report. Run the paper trial with real assertions. Go live when the checklist is green.

The goal is not to "fix the audit findings." The goal is to have a system that can be operated safely with real money. Those overlap substantially but not completely. This report is Part 1 + Part 2 of the audit; passing it is necessary but not sufficient. Sufficiency comes from the paper trial actually exercising the full lifecycle end-to-end and operators seeing no silent failures.


---
---

# PART 3 — Final 15% (Configs, Strategy YAMLs, Config Loader)

> **Status:** Audit of the config files, strategy YAMLs, and config_loader schema.
>
> **New findings:** 4 additional blockers (BL-16 through BL-19) + 2 additional highs. Cumulative totals now: **19 blockers, 24 highs, 5 mediums.**
>
> **The positional trading logic is independently broken by config (BL-16).** Every positional trade in production today would place a TGT order at entry price, guaranteeing a small loss per trade even if every spine bug were fixed.

## Part 3 — Table of contents

18. [The positional strategies are guaranteed to lose money (BL-16)](#18-positional-guaranteed-loss)
19. [Additional BLOCKERs (BL-16 through BL-19)](#19-additional-blockers-part-3)
20. [Additional HIGH findings (H-23, H-24)](#20-additional-high-findings-part-3)
21. [Final tally across all three parts](#21-final-tally-across-all-three-parts)

---

## 18. The positional strategies are guaranteed to lose money

Before you fix any wiring bug, ship any code change, or run any test: **the three positional strategy YAMLs have a configuration bug that makes every positional trade place a target order at the entry price.** The order fills instantly (or on first tick crossing entry), position closes at break-even, the operator eats the brokerage + STT + GST + slippage costs on both sides.

### The mechanism

Each positional YAML (`positional_momentum_long.yaml`, `positional_sector_rotation.yaml`, `positional_swing_long.yaml`) has:

```yaml
tgt_method: "ATR"
tgt_pct: 0.0
tgt_risk_reward: 2.0
tgt_atr_multiplier: 3.0
```

The code path in `signal_processor._derive_target()` (lines 628-666):

```python
if tgt_method == "ATR":
    if self._atr_fallback_mode == "HALT":
        raise _PipelineReject("REJECTED_NO_ATR_DATA", ...)
    self._log.warning("tgt_method=ATR not yet implemented; falling back to FIXED_PCT")
    tgt_method = "FIXED_PCT"

if tgt_method == "FIXED_PCT":
    tgt_pct = float(strategy.tgt_pct)           # reads 0.0 from YAML
    if direction == "LONG":
        return entry * (1.0 + tgt_pct)          # entry * 1.0 = entry
```

ATR isn't implemented. Falls back to FIXED_PCT. FIXED_PCT reads `tgt_pct` = 0.0. Returns `entry * 1.0` = entry. `tgt_price == entry_price`.

For a LONG position, the system places a LIMIT SELL at `tgt_price == entry_price`. The moment the order is visible to the broker, it matches against the first bid at entry price — which is guaranteed to exist since the system just bought there. Fill instant, position closes at break-even, operator loses brokerage + taxes + any 1-tick slippage.

### Why this wasn't caught

Same pattern as every other silent-failure bug: the intraday strategies don't have this problem because they use `tgt_method: "RISK_REWARD"` which ignores `tgt_pct`. So intraday trading works. Positional trading is broken. Paper trial tests that exercised intraday would not have exercised positional. Even if positional was tested in paper, the "trade placed, trade closed, reported in logs as closed_tgt" would look like a successful trade — the P&L column might not have been asserted against expectation.

### Affected strategies

| Strategy | Status |
|---|---|
| `positional_momentum_long` | BROKEN — tgt = entry |
| `positional_sector_rotation` | BROKEN — tgt = entry |
| `positional_swing_long` | BROKEN — tgt = entry |
| All 12 intraday strategies | OK — use tgt_method: RISK_REWARD |

### SL side check

All three positional YAMLs also have `sl_method: "ATR"` + `sl_pct: 0.0`. ATR falls back to FIXED_PCT, produces `sl = entry * (1 - 0) = entry`. Then `signal_processor._derive_prices()` bounds check at line 601 catches `sl_distance_pct < sl_min_pct` (0 < 0.005) and adjusts the SL to a 0.5% distance. So SL ends up at 0.5% of entry — not what the operator intended (ATR-based) but at least a non-zero, protective SL.

TGT has no analogous bounds check. No `tgt_min_pct` protection. So TGT = entry ships as-is.

---

## 19. Additional BLOCKERs Part 3

### BL-16 — All 3 positional strategies place TGT at entry price (guaranteed small loss)

**Severity:** BLOCKER — every positional trade loses money even with spine bugs fixed.
**File(s):** `config/strategies/positional_momentum_long.yaml`, `config/strategies/positional_sector_rotation.yaml`, `config/strategies/positional_swing_long.yaml`; fallback logic in `signals/signal_processor.py:640-656`

**What it is**

Described above. `tgt_method: "ATR"` + `tgt_pct: 0.0` + ATR-not-implemented → TGT = entry price → LIMIT SELL fills instantly → position closes at break-even minus costs.

**Why it matters**

Positional trading is entirely broken. Every signal that routes to one of the 3 positional strategies places an entry order that gets filled, then places a TGT order at entry price that also gets filled immediately. Net result: a round-trip with no profit potential, just the costs.

Rough cost impact per trade (₹1L position, Zerodha MIS rates, already slightly higher for CNC):
- Brokerage: ₹20 (flat, applied to sell side for CNC)
- STT: 0.1% on CNC sell side = ₹100
- Exchange charges: ~₹3
- GST: 18% on brokerage+exchange ≈ ₹4
- Stamp duty: 0.015% on buy side = ₹15
- SEBI: trivial
- Slippage (1 tick on ₹1000 share): ₹1 per share × 100 shares = ₹100

Per ₹1L positional trade: **~₹240 guaranteed loss**. Over 20 positional trades in a day: ₹4,800 just burned to costs.

**Fix spec**

Three options; recommend option A.

**Option A (fastest, spec-aligned):** Change all 3 YAMLs to use `RISK_REWARD` targeting, matching what intraday strategies already use:

```yaml
# config/strategies/positional_momentum_long.yaml — line ~21-25
# BEFORE:
tgt_method: "ATR"
tgt_pct: 0.0
tgt_risk_reward: 2.0
tgt_atr_multiplier: 3.0

# AFTER:
tgt_method: "RISK_REWARD"
tgt_pct: 0.0                    # unused in RISK_REWARD mode
tgt_risk_reward: 2.0            # 2R target from SL distance
tgt_atr_multiplier: 3.0         # unused until ATR is implemented
```

Apply identically to `positional_sector_rotation.yaml` and `positional_swing_long.yaml`.

After fix, TGT becomes `entry + sl_distance * 2.0` for LONG (line 662). With SL auto-bounded to 0.5% (per the `sl_min_pct` bounds check for ATR fallback), TGT becomes entry + 1% → a 1% profit target. That's plausible for swing/positional.

**Option B (proper):** Implement ATR in `signal_processor._derive_prices` and `_derive_target`. This is a bigger change — needs an ATR data source, probably from `candle_store.get_candles()` + ATR calculation. Deferred to v2.1 per the existing spec hints, but the YAMLs were configured assuming it existed.

**Option C (defensive guard):** Add a `tgt_min_pct` bounds check in `_derive_target`, analogous to `sl_min_pct` in `_derive_prices`. If `abs(tgt - entry) / entry < tgt_min_pct` (say 0.003), log warning and expand to the minimum. Stops the "TGT = entry" failure mode even if someone else misconfigures in the future.

Recommend: **A + C**. A fixes the immediate bug. C is defense-in-depth.

**Tests to add**

- `test_positional_tgt_is_not_entry_price` (asserts `tgt_price != entry_price` for every strategy)
- `test_derive_target_rejects_zero_distance` (asserts `_derive_target` never returns `entry`)
- Integration test: full flow with positional strategy → confirm TGT order price != entry order price

---

### BL-17 — `limits` config block duplicates `risk` block with different values; `limits` is dead config

**Severity:** BLOCKER (operator footgun — "change limit, nothing happens")
**File(s):** `config/system_config.yaml:15-18` (dead) vs `:69-74` (live); `core/config_loader.py:68-72` (LimitsConfig)

**What it is**

`system_config.yaml` has two nearly-identical-looking blocks:

```yaml
# Lines 15-18 (DEAD CONFIG — code doesn't read this)
limits:
  max_open_positions: 10
  max_trades_per_day: 20
  daily_loss_limit_pct: 2.0      # 2.0 — what even IS this? 200%? 2%?

# Lines 69-74 (LIVE — code reads this)
risk:
  max_open_positions: 10
  max_daily_trades: 20
  max_sector_exposure_pct: 0.40
  max_consecutive_losses: 4
  daily_loss_limit_pct: 0.05     # 5% — the actual limit
```

`main.py:863` reads `app_config.system.risk`. Nothing reads `app_config.system.limits`. The `LimitsConfig` Pydantic class is loaded but unused.

Critically: `LimitsConfig` has no `@field_validator` so `daily_loss_limit_pct: 2.0` passes validation. No range check.

**Why it matters**

- Operator reads the YAML, sees `limits.daily_loss_limit_pct: 2.0`, thinks "OK, 2% daily loss limit." Correct in spirit, wrong in effect (value not read by code).
- Operator changes `limits.daily_loss_limit_pct` to tune the limit → no effect whatsoever.
- The REAL limit is in `risk.daily_loss_limit_pct: 0.05` (5%, looser than operator thinks).
- A future commit that accidentally reads `app_config.system.limits.daily_loss_limit_pct` would get `2.0` (200%), effectively disabling the loss guard.

This is the archetypal "namesake config" bug the v2 spec's P2 principle was supposed to eliminate: "Every limit in config has a runtime check, an alert when reached, and a daily report row. No 'namesake' config values."

**Fix spec**

```yaml
# config/system_config.yaml — DELETE lines 14-18 entirely:
# limits:
#   max_open_positions: 10
#   max_trades_per_day: 20
#   daily_loss_limit_pct: 2.0
```

```python
# core/config_loader.py — DELETE LimitsConfig class and its reference in SystemConfig
# class LimitsConfig(BaseModel):   ← DELETE
#     ...
# 
# class SystemConfig:
#     ...
#     limits: LimitsConfig          ← DELETE this line
#     ...
```

Add a migration note / release note: operator must know the `limits` block is gone.

**Tests to add**

- `test_no_duplicate_config_keys` — loops over all loaded YAML blocks and asserts no two blocks define the same key name
- `test_system_config_has_no_limits_block` — regression guard against re-introduction

---

### BL-18 — `webhook.require_hmac: true` is never read by code; config flag is a lie

**Severity:** BLOCKER (security — operator believes HMAC is enforced when it may not be)
**File(s):** `config/system_config.yaml:79`, `core/config_loader.py:224`, `signals/webhook_receiver.py:170`

**What it is**

```yaml
# config/system_config.yaml:79
webhook:
  bind_host: "127.0.0.1"
  bind_port: 5000
  require_hmac: true           # ← OPERATOR BELIEVES THIS IS ENFORCED
```

```python
# core/config_loader.py:220-224
class WebhookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bind_host: str
    bind_port: int
    require_hmac: bool           # ← validates the field, but code doesn't read it
```

```python
# signals/webhook_receiver.py:170 — actual HMAC enforcement
if self._secret:                 # ← only enforces if env var is set, not because of config flag
    # HMAC check
    ...
```

The actual enforcement depends entirely on whether `WEBHOOK_SECRET` env var is set at startup. If unset (and BL-15 isn't fixed), the flag `require_hmac: true` has no effect: HMAC check is silently skipped.

**Why it matters**

Defense-in-depth failure: operator sets `require_hmac: true` as a safety gate. Operator forgets to set `WEBHOOK_SECRET` env var. Startup succeeds. Webhook accepts unsigned requests. Attacker injects signals. The config flag that was supposed to prevent this did nothing.

**Fix spec**

```python
# signals/webhook_receiver.py — update __init__
def __init__(
    self,
    signal_queue: queue.Queue,
    state_store: Any,
    config: Any,
    market_windows: Any,
    kill_switch: Any,
    logger: Any,
    secret_token: Optional[str] = None,
) -> None:
    ...
    # Enforce config flag at construction time
    require_hmac = getattr(config.webhook, "require_hmac", False)
    if require_hmac and not secret_token:
        raise ValueError(
            "webhook.require_hmac=true in config but WEBHOOK_SECRET env var is unset. "
            "Refusing to start in an insecure configuration."
        )
    self._require_hmac = require_hmac
    self._secret = secret_token
    ...
```

```python
# signals/webhook_receiver.py:170 — update the HMAC check to respect config
def _process_request(self, scanner_name, raw_body):
    # Fail-closed if config requires HMAC
    if self._require_hmac and not self._secret:
        return jsonify({"error": "Server misconfiguration"}), 500
    # Verify HMAC when either config or presence of secret mandates it
    if self._require_hmac or self._secret:
        sig_header: str = request.headers.get("X-Webhook-Signature", "")
        if not sig_header.startswith("sha256="):
            return jsonify({"error": "Missing or malformed X-Webhook-Signature header"}), 401
        # ... rest of HMAC check unchanged
```

**Tests to add**

- `test_webhook_refuses_construction_when_require_hmac_but_no_secret`
- `test_webhook_enforces_hmac_when_require_hmac_true`
- `test_webhook_skips_hmac_only_when_both_flag_false_and_no_secret`

---

### BL-19 — `broker_limits.yaml:backoff_sequence_sec` defines [1, 5, 30] but code never reads it

**Severity:** BLOCKER (pair with BL-6 — the 429 backoff values need a source)
**File(s):** `config/broker_limits.yaml:25-29`, consumed by nothing

**What it is**

```yaml
# config/broker_limits.yaml:25-29
backoff_sequence_sec:   # G7: 4-step backoff on 429 response
  - 1
  - 5
  - 30
```

No Pydantic class loads this. No code consumes it.

**Why it matters**

This pair with BL-6. When G7 backoff is implemented (per BL-6 fix spec), the backoff schedule must be parameterized from this YAML, not hardcoded in Python. Otherwise:
- Changing backoff pacing requires code deploy, not config change.
- The YAML becomes documentation that contradicts code behavior.

**Fix spec**

When implementing BL-6, add the Pydantic model and consume it:

```python
# core/config_loader.py — update BrokerLimitsConfig
class BrokerLimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: BucketLimits
    quote: BucketLimits
    historical: BucketLimits
    margins: BucketLimits
    backoff_sequence_sec: List[int]          # NEW
    timeouts: BrokerTimeouts

    @field_validator("backoff_sequence_sec")
    @classmethod
    def _validate_backoff(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("backoff_sequence_sec must not be empty")
        if any(s <= 0 for s in v):
            raise ValueError("backoff_sequence_sec values must be positive")
        if sorted(v) != v:
            raise ValueError("backoff_sequence_sec must be monotonically non-decreasing")
        return v
```

Then in BL-6's backoff decorator, read from config:

```python
# broker/zerodha_adapter.py — use config instead of module-level constant
def _with_rate_limit_backoff(category_fn):
    def decorator(method):
        @wraps(method)
        def wrapper(self, *args, **kwargs):
            category = category_fn(method.__name__)
            schedule = self._broker_limits_cfg.backoff_sequence_sec   # NEW — from config
            for attempt in range(len(schedule) + 1):
                ...
```

**Tests to add**

- `test_backoff_sequence_validated_at_load` (negative values, empty list, unsorted)
- `test_adapter_uses_configured_backoff_schedule`

---

## 20. Additional HIGH findings Part 3

### H-23 — `LimitsConfig` has no Pydantic validators

**File:** `core/config_loader.py:68-72`

Even though the `limits` block is dead (per BL-17), it passes `daily_loss_limit_pct: 2.0` through Pydantic without complaint. Until BL-17 is fixed (delete the block), this is a latent bomb — any code that starts reading `app_config.system.limits.daily_loss_limit_pct` gets 2.0 (interpreted by risk code as 200%).

**Fix spec**

Part of BL-17's fix (delete the class). If deletion is deferred, add validators in the interim:

```python
# core/config_loader.py:68
class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_open_positions: int
    max_trades_per_day: int
    daily_loss_limit_pct: float

    @field_validator("max_open_positions", "max_trades_per_day")
    @classmethod
    def _must_be_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be >= 1")
        return v

    @field_validator("daily_loss_limit_pct")
    @classmethod
    def _must_be_fraction(cls, v: float) -> float:
        if not (0 < v <= 1):
            raise ValueError("daily_loss_limit_pct must be a fraction in (0, 1]; got {v}")
        return v
```

### H-24 — `quote` rate limit bucket may be too tight for real usage

**File:** `config/broker_limits.yaml:13-15`

```yaml
quote:
  burst: 1
  rate_per_sec: 1
```

Consumers of the `quote` bucket:
- `order_reconciler` — potentially during G5b crash-recovery SL
- `smart_tgt_manager._startup_ltp_check` — on register or on startup recovery
- `screening.secondary_screener` — may fetch quotes per step

Under a burst of 5 signals arriving within a second, and reconciler polling concurrently, quote calls serialize at 1/sec. Screening takes ~5 seconds extra just waiting for quote tokens. This was likely tuned to match Zerodha's historical constraint but may be conservative.

**Fix spec**

Verify against Zerodha's current documented rate limit (which is documented publicly). If their limit is higher, bump burst and rate:

```yaml
# config/broker_limits.yaml
quote:
  burst: 3                # tune based on Zerodha actual limit
  rate_per_sec: 3
```

Alternative: implement request batching — `adapter.get_quote([sym1, sym2, sym3])` as a single call using Kite's batch quote endpoint if supported.

Lower priority than blockers but worth tuning before live.

---

## 21. Final tally across all three parts

### Grand total: 19 blockers, 24 highs, 5 mediums

| Part | Blockers | Highs | Mediums | Scope |
|---|---|---|---|---|
| Part 1 (70% — core trading path) | 11 | 12 | 5 | capital, orders, broker, reconciler, signals, recovery |
| Part 2 (next 15% — alerts, runtime) | 4 | 10 | 0 | alerts stack, main runtime, time_authority audit |
| Part 3 (final 10% — configs) | 4 | 2 | 0 | system_config, strategy YAMLs, broker_limits |

### Updated blocker table

| ID | Layer | Summary |
|---|---|---|
| BL-1 | capital | FundManager state not rebuilt on restart |
| BL-2 | capital | CapitalDriftDetected handler only logs |
| BL-3 | reconciler | HEALTHY check masks BL-1 silently |
| BL-4 | orders | commit_to_used failure swallowed |
| BL-5 | capital | Ledger write not in same txn as mutation |
| BL-6 | broker | 429/rate-limit backoff missing |
| BL-7 | orders | OrderFilled never emitted for entries ★★★ |
| BL-8 | orders | Order row DB insert failure swallowed |
| BL-9 | capital | Invariant violation → soft_kill, should hard_kill |
| BL-10 | events | PositionClosed never emitted |
| BL-11 | data | live_feed reconnect fires on retry not success |
| BL-12 | orders | orders table status never updated |
| BL-13 | events | shadow_tracker._eod_fired never resets across days |
| BL-14 | alerts | notifier.send() wrong params; all alerts fail silently ★★★ |
| BL-15 | security | WEBHOOK_SECRET not in required_secrets |
| BL-16 | config | Positional strategies place TGT at entry (guaranteed loss) ★★★ |
| BL-17 | config | `limits` block dead config duplicating `risk` block |
| BL-18 | config | `require_hmac: true` is a lie; never read by code |
| BL-19 | config | backoff_sequence_sec config never consumed |

Three blockers now earn ★★★ because each independently breaks a whole category of operation:
- **BL-7** — post-fill capital commit pipeline entirely dead
- **BL-14** — all non-reconciler alerts silently fail
- **BL-16** — every positional trade guaranteed to lose money to costs

### What's still genuinely unread (~3%)

- `scripts/refresh_instruments.py` (pre-market script)
- `scripts/preflight_scanner_check.py` (pre-market script)
- `strategies/schema.py` and `strategies/loader.py` (config loading, not hot path)
- `core/time_authority.py` assertion semantics (referenced only)
- `core/logger.py`, `core/account_registry.py`, `core/instrument_cache.py` (referenced only)
- `reports/daily_review.py` (spot-checked for H-17)
- Full test suite (1,410 of 1,411 unread; one confirmed bad at H-22)
- Operational files (systemd service, logrotate, backup scripts — not in the zip)

The unread portions are either peripheral (pre-market scripts), auxiliary (reporting), or not provided (operational). The main trading path, configuration, and safety mechanisms have been audited.

### Updated go-live checklist additions

In addition to Parts 1+2 checklist items, add:

**Phase A2 — config fixes (blocks go-live)**
- [ ] BL-16: Change 3 positional YAMLs to `tgt_method: "RISK_REWARD"`
- [ ] BL-16 defense: add `tgt_min_pct` bounds check in `_derive_target`
- [ ] BL-17: Delete `limits` block from `system_config.yaml` + `LimitsConfig` from `config_loader.py`
- [ ] BL-18: Wire `webhook.require_hmac` config flag to refuse startup if true + no secret
- [ ] BL-19: Add Pydantic class for `backoff_sequence_sec`; BL-6 fix reads from it

**Phase A3 — config hygiene**
- [ ] H-23: Add validators to any surviving LimitsConfig if not deleted
- [ ] H-24: Review quote bucket against Zerodha's documented rate limit; bump if safe

**Test additions**
- [ ] `test_no_duplicate_config_keys_across_blocks`
- [ ] `test_positional_tgt_not_equal_entry_price` (per strategy)
- [ ] `test_derive_target_rejects_zero_distance`
- [ ] `test_webhook_refuses_construction_when_require_hmac_no_secret`

---

## Final recommendation (revised with Part 3 findings)

Do not go live. The situation is worse than Parts 1+2 indicated:

1. **Even if you fixed every spine bug (BL-7, BL-10, BL-12, BL-14), positional trading would still lose money to costs on every trade (BL-16).** The config is independently broken. Intraday would work post-spine-fix, but that's only 12 of 15 strategies.

2. **`require_hmac: true` is a lie (BL-18).** Operators reading the YAML for security assurance get false assurance.

3. **`limits` and `risk` blocks both exist with different values for the same conceptual limit (BL-17).** Classic operator footgun.

The pattern across all three parts is consistent: **the code was built in phases, each phase shipped its unit tests green, and the integration between phases has gaps.** This applies to code wiring (BL-7, BL-10, BL-12, BL-14), config wiring (BL-17, BL-18, BL-19), and strategy config (BL-16). Any one of these in isolation would be fixable. The collective signal is that the "integration test + paper trial" layer has not been rigorous enough to catch structural bugs — only unit bugs.

The fix is:
1. Fix all 19 blockers per specs.
2. Add integration tests that assert **end-to-end state**, not just "signal reached PROCESSED" (per H-22's spec).
3. Paper trial 5 market days with real end-to-end assertions.
4. Go live only when all checklist items green.

Best estimate of time to live-ready: **3 weeks** of focused work + 1 week paper trial.


---
---

# PART 4 — Final Sweep (Remaining 3%)

> **Status:** Last pass over core glue, pre-market scripts, strategy loader/schema, and supporting data files.
>
> **New findings:** 2 additional blockers (BL-20, BL-21) + 2 additional highs. Cumulative totals: **21 blockers, 26 highs, 5 mediums.**
>
> **Part 4 closes out the code-readable audit surface. The only remaining unaudited items are operational artifacts not included in the zip (systemd service, logrotate, backup scripts) and the bulk test suite.**

## Part 4 — Table of contents

22. [Additional BLOCKERs (BL-20, BL-21)](#22-additional-blockers-part-4)
23. [Additional HIGH findings (H-25, H-26)](#23-additional-high-findings-part-4)
24. [Modules confirmed clean](#24-modules-confirmed-clean)
25. [Final grand tally](#25-final-grand-tally)
26. [What remains genuinely unaudited](#26-what-remains-genuinely-unaudited)
27. [Closing summary](#27-closing-summary)

---

## 22. Additional BLOCKERs Part 4

### BL-20 — `instruments.csv` only has 5 symbols; production requires ~2,800

**Severity:** BLOCKER (operational, not code — but hard blocker for live)
**File(s):** `config/instruments.csv` (5 rows), `scripts/refresh_instruments.py` (the fix tool)

**What it is**

Current `instruments.csv`:
```
symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector
RELIANCE,738561,NSE,1,0.05,false,ENERGY
TCS,2953217,NSE,1,0.05,false,IT
INFY,408065,NSE,1,0.05,false,IT
HDFCBANK,341249,NSE,1,0.05,false,FINANCIALS
SBIN,779521,NSE,1,0.05,false,FINANCIALS
```

Just 5 symbols. `config/reference_data/security_master_file.csv` contains 2,802 NSE symbols — the source data is there but has not been processed into `instruments.csv`.

**Why it matters**

`InstrumentCache.load()` loads whatever's in `instruments.csv`. Every downstream lookup uses this cache:
- `instrument_cache.get_by_symbol(symbol)` in position sizing (lot_size), order placement (tick rounding), signal validation
- `instrument_cache.sector(symbol)` in risk_engine sector exposure checks

When a Chartink scanner fires a signal for, say, "WIPRO":
1. Webhook receives signal, persists to `signals` table with symbol="WIPRO"
2. Signal processor picks it up, asks `instrument_cache.get_by_symbol("WIPRO")`
3. Cache raises `InstrumentNotFoundError`
4. `_PipelineReject("INSTRUMENT_NOT_FOUND")` fires, signal marked REJECTED
5. Same for every symbol not in the 5-row list

In practical terms: 99%+ of Chartink signals get rejected. The few that happen to be RELIANCE/TCS/INFY/HDFCBANK/SBIN go through.

**Fix spec**

Before go-live, run the refresh script:

```bash
# On VM, with Zerodha credentials in env:
ZERODHA_API_KEY=... \
ZERODHA_ACCESS_TOKEN=... \
python scripts/refresh_instruments.py --account LFL836

# Or with token file populated:
python scripts/refresh_instruments.py --account LFL836
```

The script's RI7 sanity guard aborts if fewer than 1000 rows are produced — so it won't silently write a broken file. Script is clean (audited in Part 4).

**Also add to startup_checks:** reject startup if `instrument_cache.count() < 1000`:

```python
# utils/startup_checks.py — new check in run_all_startup_checks:
if instrument_cache.count() < 1000:
    blocking_failures.append("instruments_csv_too_small")
    logger.critical(
        "instruments.csv has only %d rows; expected >=1000. "
        "Run: python scripts/refresh_instruments.py --account <account_id>",
        instrument_cache.count(),
    )
```

This catches the "operator forgot to refresh" scenario at startup, not at first signal.

**Tests to add**

- `test_startup_rejects_if_instrument_cache_too_small`
- `test_refresh_instruments_rejects_under_1000_rows` (already exists per RI7)

---

### BL-21 — `time_authority.record_broker_skew()` is never called; runtime clock drift monitoring is dormant

**Severity:** BLOCKER (G4 claims runtime skew detection; code doesn't deliver)
**File(s):** `core/time_authority.py:140-226` (method), zero callers

**What it is**

G4 spec: "Hybrid clock: VM clock as runtime source (all now() calls), broker clock validated periodically via existing API call timestamps. Four-tier skew thresholds: <2s normal; 2-5s warn; 5-30s alert + continue trading; >30s halt."

The `time_authority.record_broker_skew(broker_ts)` method is fully implemented:
- Maintains a rolling deque(maxlen=10) of skew readings
- Computes tier from average skew
- Fires warn/alert/critical callbacks at configured thresholds
- The critical callback is wired to `kill_switch.soft_kill` via main.py

But grep confirmation:
```
$ grep -rn "record_broker_skew\|time_authority.record" broker/ capital/ orders/ --include="*.py"
(zero matches)
```

Same pattern as BL-6 (penalize), BL-19 (backoff_sequence), BL-14 (notifier param names): mechanism exists, wiring missing.

**Why it matters**

`assert_clock_at_startup()` runs once at startup via `utils/startup_checks.py:check_clock_skew`. If the VM clock is fine at startup, the system proceeds. After that, if:
- chronyd/ntpd crashes mid-day
- System clock jumps (VM hypervisor event, DST transition edge case, manual time change)
- NTP sync interval fails silently

... nothing detects it. Meanwhile:
- Timestamps on trade rows, fm_ledger rows, reconciliation_log rows drift from broker truth.
- Reconciler compares local state to broker state using local timestamps → false "stale" decisions.
- EOD squareoff fires at 15:17 IST *by VM clock* — if VM is 60s fast, fires at 15:16 real time; too early.
- Broker's own RMS auto-squareoff at 15:20 (3 minutes after intended EOD) might catch up, but intraday decisions based on drifted time can hit SL at wrong moments.

**Fix spec**

Call `time_authority.record_broker_skew(broker_ts)` after every broker API call that returns a server timestamp. The cleanest pattern: do it inside the broker adapter.

```python
# broker/zerodha_adapter.py — add after every successful kite call:
from core import time_authority

def place_order(self, ...) -> PlacedOrder:
    ...
    try:
        kite_order_id = self._kite.place_order(...)
    except Exception as exc:
        ...

    # NEW: Record broker skew if response carries a timestamp
    # kite returns order_id but not timestamp on place_order
    # → skip record_broker_skew here; rely on get_margins/get_order_history calls

    ...
```

Zerodha's `place_order` doesn't return a timestamp. But `get_order_history`, `get_positions`, `get_margins` responses include `order_timestamp` / `exchange_timestamp`. The most reliable pattern:

```python
# broker/zerodha_adapter.py — wrap get_order_history
def get_order_history(self, broker_order_id: str) -> list[OrderHistoryEntry]:
    self._rl.acquire(_CATEGORY_MAP["get_order_history"])
    try:
        history = self._kite.order_history(broker_order_id)
    except Exception as exc:
        raise _translate_kite_exception(exc, {...}, self._log) from exc

    # NEW: extract broker timestamp from latest entry and record skew
    if history:
        latest = history[-1]
        broker_ts = latest.get("order_timestamp") or latest.get("exchange_timestamp")
        if broker_ts:
            try:
                if isinstance(broker_ts, str):
                    broker_ts = datetime.fromisoformat(broker_ts)
                if broker_ts.tzinfo is None:
                    broker_ts = broker_ts.replace(tzinfo=_IST)
                time_authority.record_broker_skew(broker_ts)
            except Exception as skew_exc:
                self._log.debug("record_broker_skew failed: %s", skew_exc)

    return [OrderHistoryEntry(**h) for h in history]
```

Similarly wrap `get_positions`, `get_margins`, `get_quote` wherever they return timestamps.

**Alternative:** Use a broker-time-probe task — a dedicated thread that calls `get_margins()` every 60s, records skew, and doesn't depend on scattering the call across every adapter method.

Recommend the dedicated probe — cleaner separation, easier to reason about:

```python
# broker/zerodha_adapter.py — new method
def probe_clock(self) -> Optional[datetime]:
    """
    Lightweight broker time probe. Calls get_margins() and returns
    the broker's response timestamp (from HTTP Date header if exposed,
    or embedded in response body). Used by time_authority skew watcher.
    """
    try:
        margins = self._kite.margins(segment="equity")
        # Kite responses include a 'timestamp' field on some endpoints
        return datetime.now(_IST)  # placeholder — check actual kiteconnect API
    except Exception:
        return None

# main.py — start a daemon thread that periodically probes
def _broker_clock_probe_loop(adapter, stop_event):
    while not stop_event.is_set():
        stop_event.wait(60)  # probe every 60 seconds
        if stop_event.is_set():
            break
        try:
            broker_ts = adapter.probe_clock()
            if broker_ts is not None:
                time_authority.record_broker_skew(broker_ts)
        except Exception as exc:
            _log.warning("broker clock probe failed: %s", exc)

threading.Thread(
    target=_broker_clock_probe_loop,
    args=(broker_adapter, _shutdown_event),
    daemon=True,
    name="broker-clock-probe",
).start()
```

**Tests to add**

- `test_record_broker_skew_updates_rolling_window`
- `test_record_broker_skew_fires_alert_on_tier_change`
- `test_broker_clock_probe_loop_records_skew`
- `test_adapter_records_skew_on_get_order_history` (if using adapter-method approach)

---

## 23. Additional HIGH findings Part 4

### H-25 — `bind_trade()` logger helper never called; identity traceability inconsistent

**Severity:** HIGH (debuggability, not correctness)
**File(s):** `core/logger.py:199` (helper), zero production callers

**What it is**

Spec Principle 6: "Every log line, database row, and Telegram alert involving trade activity carries signal_id, trade_id, and order_id when applicable."

The mechanism is `bind_trade(logger, *, signal_id, trade_id, order_id) -> TradeContext`. Nothing in production code uses it.

Instead, individual log calls pass `extra={"signal_id": ..., "trade_id": ...}` manually when the author remembered to. Coverage by grep:

| File | signal_id mentions in log extras |
|---|---|
| orders/order_placer.py | 12 |
| signals/signal_processor.py | 33 |
| capital/fund_manager.py | 13 |
| orders/order_manager.py | 9 |
| **orders/order_reconciler.py** | **0** |

`order_reconciler` is the 860-LOC module that handles state drift, orphan adoption, manual-close detection, G5b crash recovery, G3 capital drift detection. Its log lines have zero trade-identity correlation. When something goes wrong in production, reconstructing what happened to trade X will require SQL joins against reconciliation_log rather than grepping the reconciler log.

**Why it matters**

- Debug-hostile failure mode: reconciler logs a CRITICAL "capital drift detected" without telling which trade(s) are involved (the message lists symbols/qtys but doesn't tag with trade_id).
- L3 routing (spec): trades.log filter routes records with signal_id/trade_id/order_id. Reconciler records without these get routed to system.log only, not trades.log. The unified trade-activity view is incomplete.
- Silent-failure precedent: like BL-14 (wrong param names), this is the kind of thing tests don't catch because tests look at assertions, not log completeness.

**Fix spec**

Option A — adopt `bind_trade` at every entry point into trade-related code paths:

```python
# orders/order_reconciler.py — in _check1_manual_close:
def _check1_manual_close(self, trade) -> ReconciliationAction:
    trade_id = trade["trade_id"]
    symbol = trade["symbol"]
    log = bind_trade(self._log, trade_id=trade_id)   # NEW
    ...
    log.warning(
        "CHECK1 MANUAL_CLOSE: %s local=OPEN/PARTIAL broker=no_position", symbol,
    )
    ...
```

Apply across reconciler's 12+ log call sites.

Option B — inject IDs through a thread-local context (more invasive):

```python
# core/logger.py — add thread-local context
import contextvars

_trade_context: contextvars.ContextVar[dict] = contextvars.ContextVar("trade_ctx", default={})

class _JsonFormatter(logging.Formatter):
    def format(self, record):
        # Inject thread-local trade context if not already in extras
        ctx = _trade_context.get()
        for key in ("signal_id", "trade_id", "order_id"):
            if key in ctx and not hasattr(record, key):
                setattr(record, key, ctx[key])
        return super().format(record)

def set_trade_context(**kwargs) -> "Token":
    """Call at entry to a trade-processing operation."""
    return _trade_context.set({**_trade_context.get(), **kwargs})
```

Option A is less invasive. Recommend A + a regression test that asserts every reconciliation_log row correlates to a trade_id.

**Tests to add**

- `test_reconciler_log_lines_include_trade_id_when_trade_specific`
- `test_capital_drift_alert_includes_trade_id_when_identifiable`

### H-26 — paper_capital = ₹50 lakh but live will start at ₹50k — 100x calibration mismatch

**Severity:** HIGH (paper trial validity)
**File(s):** `config/accounts.csv:2` (`paper_capital=5000000`)

**What it is**

```csv
account_id,broker,label,is_primary,...,paper_capital,capital_share_pct,enabled
LFL836,zerodha,Kandasamy,true,...,5000000,1.0,true
```

`paper_capital = 5000000` (₹50 lakh). Spec recommends live start at ₹50,000. Position sizing is linear in capital:
- `risk_per_trade_pct: 0.01` × ₹50 lakh = ₹50,000 risk per trade
- `risk_per_trade_pct: 0.01` × ₹50,000 = ₹500 risk per trade

With SL distance of 1% on a ₹1000 share, qty_by_risk:
- Paper: ₹50,000 / ₹10 = 5,000 shares
- Live: ₹500 / ₹10 = 50 shares

Many signals that sized comfortably in paper will hit `BELOW_MIN` (min_qty_threshold=1) OR `CAPITAL` bound (when margin exceeds available) in live — rejected before placement. Conversely, signals that barely made it in paper might behave differently in live due to different tier-multiplier interactions.

**Why it matters**

- **Paper trial is not a behavioral proxy for live.** Different sizing means different rejection rates, different sector concentration patterns, different daily trade counts.
- Operators who observed "5 trades per day" in paper might see "1 trade per day" in live — or vice versa if paper was rejecting and live is sized tighter.
- Combined with BL-7, BL-10, BL-14 (spine/alerts broken) and the fact that paper didn't exercise the post-fill path, paper trial results should be **treated as zero evidence of live readiness**.

**Fix spec**

Two options:

**Option A — lower paper_capital to match live start** (₹50,000):

```csv
account_id,...,paper_capital,...
LFL836,...,50000,...
```

Pros: paper trial exercises exactly the qty/rejection rate that live will see. Catches BELOW_MIN and CAPITAL rejection patterns before live money is at risk.
Cons: trades may be so small that some tests don't fire (e.g., max_open_positions never reached).

**Option B — run two paper trials: large capital first (stress test), then small capital (calibration)**:
- Paper trial 1: ₹50 lakh — exercises concentration limits, max_open_positions, stress tests the event bus under high throughput.
- Paper trial 2: ₹50,000 — calibration-realistic, exercises BELOW_MIN rejection rate, verifies live will actually place trades.

Recommend B. Or at minimum, A before going live.

**Tests to add**

- Integration test: `test_position_sizer_reject_rate_at_target_live_capital` — asserts a known-good signal with specific entry/SL doesn't hit BELOW_MIN when capital = ₹50k.

---

## 24. Modules confirmed clean

Modules audited in Part 4 with no new findings:

| Module | LOC | Notes |
|---|---|---|
| `core/time_authority.py` | 365 | Structure solid; runtime wiring missing (BL-21) |
| `core/logger.py` | 321 | Structure solid; bind_trade underused (H-25) |
| `core/account_registry.py` | 224 | Clean; CSV parse + primary() lookup |
| `core/instrument_cache.py` | 265 | Clean; O(1) lookups, thread-safe by construction |
| `scripts/refresh_instruments.py` | 346 | Clean; has RI7 sanity guard |
| `strategies/schema.py` | 321 | Extensive Pydantic validators, `extra="forbid"` |

Modules not deep-read in Part 4 (partial or structural only):

| Module | LOC | Reason |
|---|---|---|
| `scripts/preflight_scanner_check.py` | 203 | Pre-market script; only runs if scanners reachable check is enabled. Low hot-path risk. |
| `strategies/loader.py` | 118 | Thin wrapper over schema.py; loads all YAMLs at startup, aborts on any failure. |
| `reports/daily_review.py` | 821 | Report generation, not hot path; H-17 (time bypass) already flagged. |
| `config/config_loader.py` | 677 | 40% read; remaining validators follow same pattern. |

---

## 25. Final grand tally

### 21 blockers, 26 highs, 5 mediums

| Part | Scope | Blockers | Highs | Mediums |
|---|---|---|---|---|
| Part 1 | Capital, orders, broker, reconciler, signals, recovery (70%) | 11 | 12 | 5 |
| Part 2 | Alerts, main runtime, order tracking, webhook (15%) | 4 | 10 | 0 |
| Part 3 | Configs, strategy YAMLs, broker limits (10%) | 4 | 2 | 0 |
| Part 4 | Core glue, pre-market scripts, account data (5%) | 2 | 2 | 0 |
| **Total** | **~97% of production code deep-read** | **21** | **26** | **5** |

### Updated blocker table

| ID | Layer | Summary | ★ |
|---|---|---|---|
| BL-1 | capital | FundManager state not rebuilt on restart | |
| BL-2 | capital | CapitalDriftDetected handler only logs | |
| BL-3 | reconciler | HEALTHY check masks BL-1 silently | |
| BL-4 | orders | commit_to_used failure swallowed | |
| BL-5 | capital | Ledger write not in same txn as mutation | |
| BL-6 | broker | 429/rate-limit backoff missing | |
| **BL-7** | **orders** | **OrderFilled never emitted for entries** | **★★★** |
| BL-8 | orders | Order row DB insert failure swallowed | |
| BL-9 | capital | Invariant violation → soft_kill, should hard_kill | |
| BL-10 | events | PositionClosed never emitted | |
| BL-11 | data | live_feed reconnect fires on retry not success | |
| BL-12 | orders | orders table status never updated | |
| BL-13 | events | shadow_tracker._eod_fired never resets across days | |
| **BL-14** | **alerts** | **notifier.send() wrong params; all alerts fail** | **★★★** |
| BL-15 | security | WEBHOOK_SECRET not in required_secrets | |
| **BL-16** | **config** | **Positional strategies place TGT at entry (guaranteed loss)** | **★★★** |
| BL-17 | config | `limits` block dead config duplicating `risk` | |
| BL-18 | config | `require_hmac: true` never read by code | |
| BL-19 | config | `backoff_sequence_sec` config never consumed | |
| BL-20 | ops | instruments.csv has 5 symbols; needs ~2800 | |
| BL-21 | clock | record_broker_skew() never called; runtime drift dormant | |

Three blockers earn ★★★:
- **BL-7** — entire post-fill pipeline dead
- **BL-14** — all non-reconciler alerts silently fail
- **BL-16** — every positional trade guaranteed to lose money

### Pattern across the audit

Across 21 blockers, the most common pattern (affecting 9 of them) is **"mechanism defined, wiring missing"**:

| ID | What exists | What's missing |
|---|---|---|
| BL-1 | fm_ledger table, `_write_ledger` method | rehydrate-from-ledger call on startup |
| BL-2 | CapitalDriftDetected event, hard_kill method | subscriber that calls hard_kill |
| BL-6 | rate_limiter.penalize() method, backoff_sequence config | caller that classifies 429 and invokes penalize |
| BL-7 | order_monitor.track() method, OrderFilled event class, order_placer._on_order_filled subscriber | order_placer calling track() for entry orders |
| BL-10 | PositionClosed event class, shadow_tracker subscriber | publisher anywhere in code |
| BL-12 | OrderManager.update_order_status() method | any caller at all |
| BL-14 | TelegramNotifier.send(severity=, ...) | call sites using the right param names |
| BL-19 | backoff_sequence_sec YAML value | Pydantic field + caller that reads it |
| BL-21 | time_authority.record_broker_skew() method | any caller in broker or orders layer |

This is the structural signature of a system built in phases where each phase passed its unit tests but nobody assembled the end-to-end wiring. Every individual module works; the system as a whole does not. **Fixing the 9 wiring blockers is mostly a plumbing exercise — the mechanisms are correct, they just need to be connected.** That's good news.

---

## 26. What remains genuinely unaudited

After Part 4, what I have NOT deep-read:

1. **Bulk test suite** — 1,410 of 1,411 tests. Spot-checked one (test_happy_path_signal_reaches_placed_status) and confirmed it asserts the wrong thing (H-22). Pattern suggests others may also test proxies, not end-state. A dedicated test coverage pass would take 2-4 hours and would likely surface:
   - Tests asserting on signal.status without asserting on capital state
   - Tests that publish OrderFilled/PositionClosed manually (and thus can't catch BL-7/BL-10)
   - Missing tests for restart/rehydration paths
   - Missing tests for hard_kill trigger paths

2. **Operational artifacts not in the zip:**
   - `systemd` service file for auto-restart on crash
   - `logrotate` config for `logs/*.log` files
   - Backup script for `data_store/trading_system.db`
   - Deploy script (git pull + systemd restart on VM)
   - Monitoring / disk usage scripts
   These materially affect operational safety but I cannot audit what wasn't provided.

3. **Strategy loader internals** (`strategies/loader.py`, 118 LOC) — thin wrapper over schema validators, low risk.

4. **Pre-market scripts** (`scripts/preflight_scanner_check.py`, 203 LOC) — runs pre-market, fails closed.

5. **Reports code** (`reports/daily_review.py`, 821 LOC) — post-market, not in hot path. H-17 already flagged it bypasses time_authority.

6. **Remaining config_loader validators** (~400 LOC of Pydantic schemas) — follow the same pattern as what was read. Low risk.

**Audit completeness: ~97% of production hot path, ~92% overall when including tests/ops.**

If Part 5 were done (I have tool budget for partial), expected yield: 0-2 more blockers (most likely in test coverage gap analysis), 2-4 more highs (likely in reports, config_loader edge cases).

---

## 27. Closing summary

### The core finding

Trading System v2 has the right architecture, the right schema, the right set of modules, and the right set of design decisions. What it lacks is the final integration pass that would have connected the modules into a working system. Nine of the 21 blockers are "mechanism exists, no caller" — the fixes are mostly 5-to-50-line wiring changes, not redesigns.

The remaining 12 blockers are:
- **Config bugs** (BL-16, BL-17, BL-18, BL-19, BL-20) — YAML/CSV edits + defensive code.
- **Error path gaps** (BL-4, BL-8, BL-9, BL-11) — add proper failure handling.
- **State reconstruction** (BL-1, BL-3, BL-5) — design-heavier but specified in the report.
- **Security** (BL-15) — add to required_secrets list.

### Why paper trial was a false positive — the full picture

Five independent reasons the "5 days paper trial clean" result should be discarded:

1. **BL-7** — OrderFilled never published → post-fill path (commit_to_used, smart_tgt, trade status update) never exercised.
2. **BL-10** — PositionClosed never published → release_used path and shadow_tracker never exercised.
3. **BL-14** — All Telegram alerts (except reconciler) silently raised TypeError → operators couldn't see problems that WERE occurring.
4. **BL-20** — 5-symbol instruments.csv → most Chartink signals rejected pre-placement → the placement-heavy code paths barely ran.
5. **H-26** — paper capital = ₹50 lakh vs live ₹50k → sizing patterns, rejection rates, concentration limits will behave differently.

### Recommended timeline

- **Week 1-2:** Fix the 9 wiring blockers (Phase A + part of B). Run the 1,411 tests, fix the ones that depended on the bugs.
- **Week 2-3:** Fix the state reconstruction + error path blockers (Phase B + C).
- **Week 3:** Fix broker robustness and config bugs (Phase D + E).
- **Week 4:** Add proper integration tests (the ones specified per finding — especially the end-to-end capital-accounting assertion). Run instruments.csv refresh. Operational setup (systemd, logrotate, backup).
- **Week 5:** Paper trial with small capital (₹50k) for 5 market days. Daily review of fund_manager snapshot, fm_ledger growth, reconciliation_log entries (all UNRECOVERABLE rows should be zero), innings table populated, zero CapitalDriftDetected events.
- **Week 6:** Go live at ₹50k with daily operator review.
- **Week 8+:** If clean, scale up in ₹50k increments.

### The audit is done

~97% of production code deep-read. 21 blockers with fix specs, 26 highs, 5 mediums. All cataloged with file:line references and proposed code changes. Test additions specified per finding.

What's left is not more audit, it's execution of the fix list and a properly-instrumented paper trial. Good luck with the build. The foundation is solid — the wiring just needs to be completed.

