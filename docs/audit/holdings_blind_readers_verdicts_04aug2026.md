# THE HOLDINGS-BLIND READERS — A VERDICT EACH — 04-Aug-2026

**Status: `<MEASURED — NO FIX, NOTHING AUTHORISED>`.** Measured at **`6a7cee7`**.
Read-only; **0 `.py` changed.** Follows `ledger7_multiauthority_measurement_04aug2026.md` §1
and the F1 correction.

⛔ **A holdings-aware change is exactly what Q4's ordering rule governs and is NOT
authorised here.** This issues verdicts, not fixes.

## 0. ⛔ MY OWN COUNT CORRECTED FIRST: **11 blind, not 12**

I reported *"12 of 13 readers go blind"*. **Measured per site, it is 11.** The twelfth
`get_positions()` call — `cnc_gtt_monitor.py:438` — sits in `_gather` **two lines below the
only `get_holdings()` call in the tree** (`:437`). It is the holdings-aware reader, not a
blind one. ⇒ **12 `get_positions()` call sites, of which 11 are positions-only.** The
class-not-one-reader finding is unchanged; the number was one too high.

## 1. 🔴 BITES THURSDAY — REPORTED FIRST, AS ASKED

**Wed 5-Aug** a CNC buy is T+0 and visible in `positions()`. **Thu 6-Aug it is T+1**: it
leaves `positions()` and appears only in `holdings()`. These two are the ones that change
behaviour that morning.

### 1a. ⛔⛔ `order_reconciler.py:839` → **CHECK1** — **CAPITAL PATH. THE ONE THAT MATTERS.**

**The code documents the hazard against itself** (`:850-852`):

> *"a carried CNC holding lives in `holdings()` not `positions()`, so **CHECK1 would wrongly
> mark it CLOSED_MANUAL** and G5b/CHECK9 would place a spurious SL."*

**(a) What it does with the position it cannot see.** `bp = broker_pos.get(symbol)` → `None`
→ **`CHECK 1: MANUAL_CLOSE` fires.** And CHECK1 is not a label — its own docstring
(`:1183-1192`): fetch exit price from broker, **cancel orphaned SL/TGT orders at the
broker**, CRITICAL alert, and **release capital**.

**(b) Correct / wrong / correct-by-accident?** ⇒ **PROTECTED, CONDITIONALLY — and the
condition is the finding.** `:855-859` skips delivery trades via
`get_active_gtt_states()`, i.e. `SELECT * FROM gtt_state WHERE status = 'ACTIVE'`
(`state_store.py:2250`). **The protection exists only if that trade has an ACTIVE
`gtt_state` row.**

⛔⛔ **THE GAP, MEASURED:** `orders/cnc_gtt.py:112-113` — `place_for_fill` *"**Raises
BrokerError** on a missing LTP or a C8 distance/straddle violation"*, and `insert_gtt_state`
is called only **after** placement succeeds (`:210`, `:217`). ⇒ **a CNC trade whose GTT
placement FAILED has no ACTIVE row, is not skipped, is invisible in `positions()` at T+1, and
CHECK1 then cancels its broker orders and releases its capital while the shares are still
held.**

⚠️ **A second route to the same state:** the GTT is placed fine Wednesday and then
**triggers** Wednesday (SL/TGT) → its row leaves `ACTIVE` → if the sell does not fully fill,
a residual holding exists Thursday **with no ACTIVE row**. Same outcome, no failure required.

⚠️ **Live precondition today:** `gtt_state` currently holds **0 rows** (measured 13:05).
Nothing is protected because nothing is held — but it means **Wednesday's first CNC trade is
the first row this table will ever carry in production**, and the protection is only as good
as that write.

**(c) Bites Thursday?** ✅ **YES — the first T+1 after the flip.**
**(d) Kill or capital path?** ✅ **CAPITAL PATH** (releases capital, cancels broker orders).

🔴 **This is the one that could warrant a decision TONIGHT rather than Thursday.** ⛔ No fix
proposed here — the honest options (harden the skip so it keys on *product*, not on GTT
liveness; or make CHECK1 refuse rather than close when it cannot confirm) are both
holdings-aware or reconciler-behaviour changes and are Q4/#2b territory.

### 1b. ⚠️ `order_reconciler.py:745` — the FIX-008 **CNC check**, blind to CNC

**(a)** A T+1 delivery holding is absent from `positions()` ⇒ the check sees nothing.
**(b)** ⛔ **WRONG — and pointedly so: a check whose entire purpose is spotting CNC positions
the system did not open reads `positions()` only, so it goes blind to carried CNC from T+1.**
It is the one reader whose *stated purpose* the blindness defeats.
**(c)** ✅ Bites Thursday. **(d)** ⛔ Not kill, not capital — **advisory only** (its docstring:
*"the check is advisory (WARNING log + alert) — it does NOT place exits or call soft_kill"*).
⇒ Real, but it fails **quiet**, not dangerous.

## 2. ✅ CORRECT BY CONSTRUCTION — the blindness is redundant, not load-bearing (3)

These filter on **product** explicitly, so they would exclude a delivery position even if
they could see it. ⛔ **Not "correct by accident" — correct by design, and they stay correct
if a neighbour is fixed.**

| site | the filter |
|---|---|
| `eod_squareoff.py:1072` | `EMERGENCY_FLATTEN_PRODUCTS` — *"EOD6 design mandates DELIVERY (CNC/NRML) positions are never touched"* |
| `eod_squareoff.py:1447` | residual sweep, `product in EMERGENCY_FLATTEN_PRODUCTS` (ledger #2 shared source) |
| `structure_exit_manager.py:625` | `_broker_mis_qty` — `product == "MIS"` |

## 3. ⚠️ CORRECT **BY ACCIDENT** — right today, fragile by construction (5 + 1)

**This is the deliverable of the exercise.** Each treats an unseen position as **flat/zero**
and therefore does nothing — which happens to be the wanted outcome for delivery, but **for
the wrong reason**: none of them knows the position is delivery. ⛔ **Every one of them
changes behaviour the moment something upstream is made holdings-aware.**

| site | unseen ⇒ | why it is right anyway | path |
|---|---|---|---|
| `position_helpers.py:37` `broker_net_qty` | net `0` ⇒ `determine_close_direction` → `(None, 0)` = *"broker confirms flat, do NOT fire another exit"* | no exit fired on a delivery position | 🔴 **kill + order_placer emergency exit** |
| `kill_switch.py:1277` `_is_position_flat` | symbol absent ⇒ `return True` (**flat**) | kill skips it; Q4 says delivery survives | 🔴 **kill** |
| `kill_switch.py:1854` Site B sweep | not in the list ⇒ never swept | Q4 again | 🔴 **kill** |
| `order_reconciler.py:2913` check9 naked-confirm | `held == 0` ⇒ **NOT naked** | refuses to call it naked ⇒ **no spurious SL** | reconciler |
| `order_reconciler.py:2966` check9 emergency sell | `live_held <= 0` ⇒ **skip the sell** | oversell guard ⇒ never sells what it cannot see | reconciler |
| `eod_squareoff.py:1611` phase-2 LIMIT promotion | qty absent ⇒ treated as filled/closed | ⚠️ **by SCOPE, not by filter** — it only ever promotes LIMITs EOD itself placed, which are intraday. ⛔ It has **no product filter**, so it is the weakest of the six | EOD |

⭐ **Three of these six sit on the kill path**, which is why the standing rule — *the buy-day
product filter must land BEFORE anything makes the kill holdings-aware* — is not a
preference. Make `broker_net_qty` holdings-aware without it and a HARD_KILL starts seeing
delivery positions it is required by Q4 **not** to flatten.

## 4. TALLY, AND WHAT IS OPEN

| verdict | count | sites |
|---|---|---|
| 🔴 **wrong, capital path, bites Thursday** | **1** | `order_reconciler:839` → CHECK1 |
| ⚠️ wrong, advisory, bites Thursday | 1 | `order_reconciler:745` |
| ✅ correct **by construction** | 3 | `eod_squareoff:1072` · `:1447` · `structure_exit_manager:625` |
| ⚠️ correct **by accident** | 6 | the §3 table |
| ✅ **not blind** (reads holdings) | 1 | `cnc_gtt_monitor:438` (+`:437`) |
| | **12** | (11 blind + 1 paired) |

🔴 **OPEN, and the only one with a Thursday deadline: §1a.** Whether Wednesday's CNC path
can be relied on to write an ACTIVE `gtt_state` row, or whether CHECK1's delivery skip must
key on **product** instead of on GTT liveness. ⛔ **Not decided, not built.**

⛔ **HALT.**
