# MIS squareoff rejection — IDEA, 10-Sep-2026 (production)

**Second market-protection incident in two days.** Companion to
`2026-09-03_naked_position_ANANTRAJ.md` and the 09-Sep ORCHPHARMA record.

All figures below are 🔬 MEASURED read-only from production
(`/home/ubuntu/systems/trading-system`) on the evening of 10-Sep-2026.
⛔ Production was not changed, restarted or pushed to.

---

## 1. What happened

| time (IST) | event |
|---|---|
| 14:14:14 | INDOCO LONG opened by the **system** (`trd_f002cf21…`, MIS, qty 2) |
| 15:02:10 | INDOCO's **SL leg fills** (`SL` `COMPLETE`) → trade CLOSED, `SL_HIT`, `OWN_SL`. ⭐ Out of the picture 51 s before PASS_1. |
| 15:02:49 | `CHECK2 HUMAN_ORDER: symbol=IDEA broker_qty=1 avg_price=14.86 — no local trade; treating as operator/untracked order. System manages system trades only; not adopting` |
| 15:03:01.263 | `MIS_AUTO_SQUAREOFF_SCAN pass=PASS_1 mis_candidates=**1**` — ⚠️ IDEA counted as a candidate |
| 15:03:01.265 | `place_order call_start symbol=IDEA order_type=MARKET qty=1 side=SELL` |
| 15:03:01.314 | `InputException: Market orders without market protection are not allowed via API.` |
| 15:03:01.316 | **CRITICAL** `EXIT_REJECTED: PASS_1 IDEA: place_order raised: … ; RESTORE FAILED: original SL parameters not found` |
| 15:03:05.273 | **CRITICAL** `MIS_REMAINS: PASS_1: 1 MIS position(s) still open: ['IDEA']` |
| ~15:05 | 👤 **Rama closes IDEA by hand** |
| 15:06:04.133 | `MIS_AUTO_SQUAREOFF_SCAN pass=PASS_2 mis_candidates=**0**` → `PASS_2: FLAT (0 MIS positions)` |
| 15:15:01 | `SOFT_KILL ACTIVATED reason=circuit_breaker_force_close_15:15` — ⭐ the **routine** daily close, ⛔ not an incident trip |

🔬 5 "market protection" lines in today's logs. 🔬 **0 MARKET orders in the `orders` table, ever** — still, because
a rejected order is assigned no `order_id` and therefore persists no row.

---

## 2. §1.2 — Did PASS_2 fire?

⭐ **YES. PASS_2 ran on schedule at 15:06:04 and correctly found the book flat.**
`mis_candidates=0` → `PASS_2: FLAT (0 MIS positions)`.

⛔ It was **not** skipped and **not** suppressed. 👤 Rama's manual close at ~15:05 removed the position in the
three minutes between the passes, so PASS_2 had nothing to act on.

⚠️ **This differs from ORCHPHARMA (09-Sep), which failed BOTH passes.** Today only PASS_1 failed — and only
because a human intervened first. ⛔ Do not read "PASS_2 was fine" as evidence the second pass works: it has
still never successfully exited a position, because it has never been given one to exit.

---

## 3. §1.3 — Did the kill switch trip? **No — and "not adopted" did not suppress it**

🔬 `CHECK9` lines today: **0**. 🔬 `kill_switch_state` rows for 2026-09-10: exactly **one** —
`SOFT_KILL 15:15:01 circuit_breaker_force_close_15:15`, the routine daily close.
🔬 The four `MISSING_EXITS` strings in today's logs are **yesterday's ORCHPHARMA** SOFT_KILL being read at
startup and auto-cleared at 08:15:21. ⛔ None of them is today's event.

⭐ **THE MECHANISM — and it is NOT suppression.** CHECK 9 iterates **local** rows: it looks for a trade whose
`sl_order_id` is in the local DB but absent from the broker's open orders. 🔬 IDEA has
**0 local trades and 0 local orders, on any date**. ⇒ CHECK 9 had **no input to iterate**; it was structurally
blind to IDEA.

⚠️ So the reconciler's *"not auto-managed (operator policy)"* decision did **not** switch CHECK 9 off. Both
behaviours share the **same root cause** — no local linkage for a broker position — and neither causes the
other. ⛔ Do not record this as "the operator-policy branch suppressed the naked-position check".

🔴 **The consequence is real regardless:** a naked MIS position existed for ~2 minutes and **no kill switch
tripped**, where ORCHPHARMA's did. The difference is entirely whether the position had a local row.

---

## 4. §1.4 — A SECOND, INDEPENDENT RESTORE FAILURE MODE

| incident | restore outcome | cause |
|---|---|---|
| ORCHPHARMA 09-Sep | side is empty | `state_store.py:1741`'s SELECT **omits `transaction_type`**, so `side=''` always — the known dead-code defect |
| **IDEA 10-Sep** | `RESTORE FAILED: original SL parameters not found` | 🔬 **there is no local trade and no SL row to read at all** (0 trades, 0 orders for IDEA on any date) |

⭐ **THE PATH.** `_restore_protection` → `_restore_protection_inner` recovers the original SL parameters by
looping over `resting` (from `get_open_mis_exit_orders_for_symbol`) for a row with `leg == 'SL'`. With no local
trade, that query returns **empty**, the loop never assigns `sl_row`, and the method returns
`"RESTORE FAILED: original SL parameters not found"`.

🔴 **⇒ REGISTERED, NOT FIXED: fixing the `state_store` SELECT alone leaves this case broken.** The Block B
restore work must handle **both** — the malformed-side case *and* the no-local-trade case. They are independent:
one is a bad read of a row that exists, the other is the absence of any row.

⚠️ Note the two failures are also differently *reachable*: the ORCHPHARMA mode fires for **system** trades, the
IDEA mode only for **operator** positions. A fix validated on system trades will not exercise the second at all.

---

## 5. §1.5 — Two subsystems disagreed about the same position, 12 seconds apart

🔬 15:02:49 `order_reconciler` CHECK 2: *"no local trade; treating as operator/untracked order. System manages
system trades only; not adopting"*.
🔬 15:03:01 `mis_autosquareoff` PASS_1: `mis_candidates=1` → attempts a MARKET exit on that same position.

⭐ **THE MECHANISM.** `_find_open_mis_positions_for_auto_squareoff` decides eligibility from the **broker
position alone** — symbol, explicit `product == "MIS"`, `qty != 0`. It never consults `trades`/`orders` for local
linkage, and it has no notion of an operator position. Its own docstring is explicit that the filter is
product-and-quantity based.

⚠️ **Both behaviours are individually defensible.** The reconciler is right not to adopt an untracked position
into system state. The squareoff is arguably right to flatten *any* MIS position before the broker's own
auto-square-off penalty. ⛔ **What is not defensible is that neither knows the other's verdict** — the system
declared a position out of scope and then acted on it anyway, and the failure of that action produced a
CRITICAL that reads as a system-trade failure.

🔴 **REGISTERED AS ITS OWN ITEM. ⛔ NOT FIXED.** Any repair has to choose a policy deliberately — flatten
operator positions or leave them — because today the answer differs between two subsystems in the same minute.

---

## 6. What this changes about Block A

⭐ Block A's protected MARKET would have let the **exit** succeed: the band is applied at
`zerodha_adapter.place_order`'s single chokepoint and PASS_1 submits 1.5 %.
⛔ **It would NOT have fixed §4 or §5.** The restore would still have failed (no SL row to restore), and the
two subsystems would still have disagreed. ⇒ ⛔ do not let "Block A shipped" close either item.

🔬 Production is **untouched** and still runs the unprotected path: 5 rejections today, 0 MARKET rows ever.

---

## 7. Provenance

🔬 All figures read-only from production on 10-Sep-2026 evening: `logs/system_2026-09-10.log`,
`logs/reconciler_2026-09-10.log`, `logs/debug_2026-09-10.log`, and `data_store/trading_system.db`
(`trades`, `orders`, `kill_switch_state`) via `sqlite3 -readonly`.
⛔ No production change, no restart, no push, no broker order.
