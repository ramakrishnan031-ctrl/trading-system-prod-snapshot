# M-O9 — slippage SL reference: **HELD, not built** (25-Jul-2026)

**Instruction §D asked for a fix.** It is not soundly buildable as specified, for a reason that is not visible from the two lines it names. Read-only investigation; **no file changed**. Per E5: the clean items shipped, this one is held with the reason.

> **The one-line answer:** the trailed SL trigger **is not persisted anywhere at the moment the slippage roll-up runs**. It lives in `smart_tgt_state.current_sl`, that row is deleted when the trade closes, and the roll-up is an **async** subscriber racing that delete. There is nothing correct to point `sl_slip` at.

---

## 1. What §D asked for

`orders/slippage_recorder.py`:

```
:199   sl0, tgt0 = t.get("sl_initial"), t.get("tgt_initial")
:207   sl_slip = adverse_sl_slip(side, sl0, sl_fill) if (sl0 and sl_fill) else None
```

When a trailing stop has moved the SL, the trigger actually in force at `SL_HIT` is not `sl_initial`, so `sl_slip` would measure the whole trail distance as "slippage". D1: *"Fix the reference to the correct trailed-trigger value."*

**The diagnosis is correct.** The fix is what is unavailable.

## 2. Why it cannot be built — the trailed value does not exist at roll-up time

**Where the trailed SL lives.** `core/schema.sql`, `smart_tgt_state`:

```sql
current_sl   REAL NOT NULL,   -- updated after each confirmed trail
```

`SmartTgtManager` writes it only after the broker confirms the modify (`smart_tgt_manager.py`, ST5): `info["current_sl"] = new_sl` in memory, then `self._state_store.update_smart_tgt_state(..., current_sl=new_sl, ...)`.

**Where it does NOT live — all checked, all negative:**

| candidate | verdict |
|---|---|
| `trades.sl_initial` | the *initial* SL by definition — the value we are trying to move away from |
| a trailed-SL column on `trades` | **does not exist.** `trades` carries `sl_initial` and `sl_trail_count` (`schema.sql:132,183`) and nothing else SL-related |
| `orders.trigger_price` | exists (`schema.sql`, orders `:17-18`), but the trail path calls `adapter.modify_order(broker_order_id, trigger_price=new_sl)` — it modifies the order **at the broker**. The local `orders` row is not rewritten by that path |
| `smart_tgt_state.current_sl` | the real home — **but see below** |

**And it is deleted on close.** `orders/order_placer.py:2477-2495` copies exactly one field out before the row goes:

```python
# v14: copy sl_trail_count from smart_tgt_state before unregister deletes the row.
row = self._om._store.fetch_one(
    "SELECT trail_count FROM smart_tgt_state WHERE trade_id = ?", (trade_id,))
if row is not None:
    self._om._store.execute(
        "UPDATE trades SET sl_trail_count = ? WHERE trade_id = ?", (row["trail_count"], trade_id))
...
self._smart_tgt_manager.unregister_trade(trade_id)     # -> DELETE FROM smart_tgt_state
```

`unregister_trade` reaches `DELETE FROM smart_tgt_state WHERE trade_id = ?` (`core/state_store.py:2120`). **`trail_count` is preserved; `current_sl` is discarded.**

## 3. ⭐ The ordering looks survivable and is not — the subscriber is ASYNC

`PositionClosed` is published at `order_placer.py:2462`; the delete happens at `:2498`. So on a naive reading the roll-up runs first and could still read `current_sl`.

It cannot be relied on:

```python
# orders/slippage_recorder.py:114
bus.subscribe(PositionClosed, self._on_position_closed, async_dispatch=True)
```

and `core/events.py:309-311` — *"Async subscribers (async_dispatch=True): submitted to their dedicated executor; **publish() returns immediately without waiting**."*

⇒ `_on_position_closed` runs on an executor thread while the publishing thread walks straight on to `:2477` (copy) and `:2498` (delete). **Whether the row is still there is a race**, decided by thread scheduling. A fix that read `current_sl` there would produce the trailed value *sometimes* — the worst possible outcome for a calibration series, because the data would be silently bimodal.

## 4. What building it would actually require — and why that exceeds §D

Any sound fix must **persist the trailed SL before the row is deleted**:

1. add a column to `trades` (e.g. `sl_final`), **a schema change + migration** — not authorised, and the standing rule from the last batch is explicit that schema changes need their own authorisation; **or**
2. write it into the existing copy block at `order_placer.py:2489`, i.e. **a new write on the order-placement/close path**.

Option 2 is the cheaper one and is still the wrong thing to do under this instruction, because **D3 requires the change to be calibration-only**. `order_placer._on_position_closed` is the order path. A write there is not "calibration only" however small, and it is exactly the class of change the careful loop exists to slow down.

Making the subscriber synchronous instead would be worse: it puts an analytics DB write inline on the position-close path.

## 5. What is true today, and why nothing is at risk

- **Trailing has never fired: 0 of 421 trades have `sl_trail_count > 0`** (established 24-Jul, `distinct = [0]`; 84 SL_HIT exits, none trailed).
- Therefore `current_sl == initial_sl` for every trade that has ever existed, and `sl_slip` computed against `sl_initial` is **exactly correct on 100% of real data**.
- **The defect is UNREACHABLE**, not merely low-impact. It arms only if trailing starts firing.
- **D3 verified, not inherited:** `build_trade_slippage_row` is called only from `_on_position_closed` (`:190`), whose sole effect is `self._store.insert_trade_slippage_log(...)`. `trade_slippage_log` has no reader on the order, capital, sizing or kill path — it is calibration/analytics only. Confirmed by reading, as instructed, rather than carried over.

## 6. Recommendation

**Hold.** Pair the fix with the persistence it needs, as one reviewed change, at the time trailing is actually enabled:

1. persist the final trailed SL (a `trades` column, written in the existing `order_placer.py:2477-2495` copy block beside `sl_trail_count` — one authorised schema change, one line next to a write that already exists there for exactly this reason);
2. then point `sl_slip` at it, falling back to `sl_initial` when null (which is every historical row, so the series stays continuous);
3. RED-first against a constructed trailed case, which only becomes constructible once step 1 exists.

Doing step 2 alone — the literal §D — would either read a value that is not there (`sl_initial` again, i.e. no change) or read one that is there only sometimes (the race). **Neither is a fix.**

⛔ **Nothing built. No file changed.** If M-O9 is later cited as done, this is the record that it was not, and why.
