# Ledger #2d — STEP 1 MEASUREMENT — 03-Aug-2026

**Status: `<MEASURED — NOT IMPLEMENTED, NOT AUTHORISED>`. Halted at the card's gate.**
Measured against deployed code `d6c298d` / `8519289`. Read-only; no `.py` changed by this record.

---

## 0. ⛔ FIRST FINDING: THERE IS NO #2d CARD

The instruction was *"run Step 1 only from the #2d card."* **No such card exists**, and this
was already known but never acted on — the #3 design registration recorded it verbatim:
*"`#2d` still has NO register row."* There is also **no `docs/audit/` design doc** for it.

Its entire definition is one clause in `MASTER_PENDING_01-Aug-2026.md:579`:

> *"Carries two riders: the §R7 `kill_switch` three-site CO defect (**needs its own card**)…"*

pointing at `docs/audit/reconciler_product_filter_build_02aug2026.md` **§R7**. This record
therefore works from §R7 plus the four questions supplied with the instruction. **A future
reader must not mistake this record for the missing card** — a card carries authorisation and
a decision; this carries measurements only.

## 1. THE THREE SITES — §R7's LINE NUMBERS STILL HOLD

Re-verified at `d6c298d` (they were measured 02-Aug and a 65-commit push has landed since):

| Site | Line | Context | `intent` source |
|---|---|---|---|
| **A** | `capital/kill_switch.py:1629` | local pass over open trades | `:1599` `_PRODUCT_TO_INTENT.get(raw_product, "INTRADAY")` |
| **B** | `capital/kill_switch.py:1708` | broker-position sweep | `:1698` `_PRODUCT_TO_INTENT.get(sweep_product, "INTRADAY")` |
| **C** | `capital/kill_switch.py:1789` | retry loop | enclosing scope |

✅ **`variety` appears NOWHERE in `capital/kill_switch.py` — grep count 0**, so every one of the
three defaults to `variety="regular"`.

## 2. Q1 — A CO POSITION UNDER THE BREAKER, ON AND OFF

**Both settings are wrong. Only the failure mode differs.**

- **Coercion ON (today):** `CO` is coerced to `MIS` → the broker **accepts** the order, but it
  **does not net the CO position** → **naked MIS short**. Silent, and it costs money.
- **Coercion OFF:** sends `product="CO", variety="regular"` → **the broker rejects it**
  (Audit 3.1). Loud, and the position stays open.

⇒ There is no configuration of the existing flags that closes a CO position correctly from
these three sites. This is a **missing capability**, not a mis-set flag.

## 3. Q2 — IS `entry_broker_order_id` REACHABLE PER SITE? ⭐ **NOT UNIFORMLY — THE KEY FINDING**

| Site | Reachable? | Why |
|---|---|---|
| **A** `:1629` | ✅ **Yes — one column away.** | The open-trades query (`:1528-1536`) already runs a correlated subquery over `orders o WHERE o.trade_id = t.trade_id AND o.leg IN ('ENTRY','CO')` to fetch `product`. Adding `o.order_id` is one more column in a scan that already happens. `orders.order_id` **is** the broker id (`:1461-1462`, `schema.sql:271`). |
| **C** `:1789` | ✅ Yes | Operates on retry entries that carry `trade_id`. |
| **B** `:1708` | ⛔ **Structurally NO** | It iterates **broker** positions. `:1543-1546` states the reason it exists: *"a broker position can exist with no local OPEN/PARTIAL/PENDING_FILL trade."* For exactly that case there is **no local row to join**, so no parent id. |

⛔⛔ **THE DESIGN CONSEQUENCE: "mirror `eod_squareoff` at all three sites" IS NOT ACHIEVABLE AS
STATED.** Sites A and C can adopt the reference implementation; **Site B cannot** for the
orphan-at-broker case and needs a *different* answer. Any design that assumes one uniform fix
across the three is wrong by construction. This is the same class of Step-1 finding that moved
#2c, #2c-R and #2e.

## 4. Q3 — CAN EOD'S CO PATH BE EXTRACTED WITHOUT CHANGING `eod_squareoff`?

**Only a narrow seam, and the boundary matters.**

- ✅ **Extractable:** the `is_co` determination (`:1165`), the broker call
  `cancel_order(entry_broker_id, variety="co")` (`:1188-1190`), and the success/reason verdict
  (`:1191-1198`).
- ⛔ **NOT extractable:** the bookkeeping around it is EOD-specific — it calls
  `self._mark_exit_failed(trade_id)`, increments EOD counters, and writes an
  `INSERT OR IGNORE INTO orders … leg='EOD'` marker row (`:1210-1219`). Dragging that into the
  kill path would file a **kill** exit as an **EOD** exit.

⇒ A shared helper must stop at **"cancel the CO bracket, report success/reason"**, leaving each
caller its own bookkeeping. Under that boundary `eod_squareoff`'s behaviour is unchanged.

## 5. Q4 — CANCEL-DURING-KILL FAILURE MODES

`_cancel_trade_resting_exits` (`:1465`) calls `self._adapter.cancel_order(oid)` — **no
`variety`**, so `"regular"`. For a CO bracket that is the wrong variety.

The failure path (`:1466-1471`) logs a **WARNING** and `continue`s; the flatten then proceeds to
`place_order`. ⇒ **a failed cancel does not stop the reverse order.** Combined with §2 that
gives: resting exit possibly still live **and** a reverse order placed. The degrade is
deliberate (`:1451-1453`: *"must never crash the flatten"*), so this is a **severity** question,
not a crash — but it is not visible above WARNING.

## 6. ⚠️ A NEAR-MISS, RECORDED BECAUSE IT WOULD HAVE BEEN A FALSE ALARM

`entry_broker_order_id` is **absent from `core/schema.sql`** and the **live VM DB rejects it**
(`no such column: entry_broker_order_id`), while `eod_squareoff.py:1157` reads exactly that key.
That looks like §R7's "reference implementation" being dead in the same way `kill_switch`'s old
`broker_order_id` was (`:1461-1464`).

✅ **It is not.** It is an **ALIAS**: `core/state_store.py:1106` — `o.order_id AS
entry_broker_order_id` (also `:1326`, `:1366`), via `get_open_intraday_positions()`.
**§R7 corollary 2 HOLDS: `eod_squareoff` is clean.**

⭐ Recorded because the check is the point: a column-existence grep would have "proved" a
load-bearing claim false. **Verify the premise before reporting the finding.**

## 7. WHAT IS STILL OPEN (⛔ none of it decided here)

1. **Site B's answer** — the only genuinely unsolved piece. A broker position with no local
   trade row has no parent id; options are not enumerated here.
2. **Whether the coercion should change at all** — §2 shows both settings are wrong for CO, so
   this is not a flag flip.
3. **`_cancel_trade_resting_exits`'s variety** — whether it should learn `variety="co"`, and
   whether a failed CO cancel should still permit the reverse order (§5).
4. **Reachability today** — CO is doubly dormant (§R8's unpark trigger is unmet), so this is
   **LATENT**, not live. It is a **hard gate on enabling CO**, not on the flip.

⛔ **HALT. No implementation. The extraction shape needs review before any code, and §3 changes
what that shape can be.**
