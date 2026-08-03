# LEDGER #2e — THE FLATTEN QUANTITY IS SYMBOL-NET, NOT PRODUCT-AWARE

**`<CARDED — DESIGN ONLY. ⛔ NOT IMPLEMENTED, NOT AUTHORISED.>`** 03-Aug-2026.

**Ruled by Rama 03-Aug ~10:30, on the Monday drill's RED cell:**
- ⛔ it does **NOT** block tonight's push;
- ⭐ it **IS a HARD GATE on the CARRY PILOT**;
- ⛔ it does **not** gate Wednesday's flip.

Evidence: `docs/audit/kill_drill_03aug2026.md` §4 (the measurement) @ `0c17056`.

---

## 1. THE DEFECT, IN ONE LINE

Under a HARD_KILL, the local-pass flatten sells the **symbol's net quantity across all
products**, so on a `(SYM, MIS 10) + (SYM, CNC 5)` book it sells **15** — and the extra
5 come out of the delivery position the buy-day filter deliberately **spared** one line
earlier.

**Measured:** `place_order('SYM','SELL',15,'INTRADAY','ks_hard_kill_exit')` with
`broker_net_qty('SYM') = 15`.

## 2. ROOT CAUSE — one function, two disagreeing call sites

`broker/position_helpers.py:34-39`:

```python
for p in positions or []:
    if getattr(p, "symbol", None) == symbol:
        net += int(getattr(p, "qty", 0) or 0)     # no product filter
```

| site | how it picks qty | verdict |
|---|---|---|
| `kill_switch.py:1613` — local pass | `determine_close_direction` → `broker_net_qty` (symbol-net) | ⛔ **wrong on a mixed book** |
| `kill_switch.py:1665` — broker sweep | the **per-row** `pqty` from `positions()` | ✅ **correct** |

⭐ Kite's `positions()` is per `(symbol, product)`. The sweep respects that; the local
pass collapses it. **The two sites disagree, and only the sweep had ever been measured.**

## 3. ⭐ WHY THIS IS A HARD GATE ON THE CARRY PILOT (the ruling's reasoning, recorded)

The carry pilot exists to **hold delivery while the system trades intraday**. That is
*precisely* the two-row `(symbol, MIS) + (symbol, CNC)` book above — the pilot does not
merely make the collision possible, it **manufactures it deliberately and repeatedly**.

- The **CNC half already exists**: T2 has held 5 CNC positions overnight since 29-Jul.
- The pilot supplies the **missing intraday half** on the same symbols, by design.
- ⇒ what is latent today becomes **routine** the moment the pilot runs.

**⇒ #2e must land BEFORE the carry pilot** — the same shape as Q4's standing rule that
*the buy-day filter lands before anything becomes holdings-aware*.

⛔ **It does NOT gate Wednesday's flip.** Flip and pilot were separated deliberately, and
the flip alone does not create the two-row book.

## 4. ⛔ WHY THIS IS NOT A ONE-LINER — read before touching it

`broker_net_qty` is **shared**, and its whole reason for existing is anti-oversell:

- **FIX-190 (Bug A)** created it after the 19-Jun **THELEELA** incident:
  `BUY 1 → SELL 1 (emergency) → SELL 1 (HARD_KILL) → short −1`. It exists so a second
  actor flattening an already-closed position does **not** fire another same-side order
  and open a naked opposite position.
- Callers: the kill switch's local pass **and** `order_placer`'s emergency exit.
- ⛔ **A careless product filter here re-opens the THELEELA class**, because "already
  flat" must still be detected correctly when the flattening actor and the closing actor
  disagree about product.

⇒ **CAREFUL-LOOP: design → review → implement.** It touches the kill path and the
quantity that reaches the broker.

## 5. DESIGN DIRECTIONS — recorded, ⛔ none chosen

| # | shape | note |
|---|---|---|
| **D-A** | give `broker_net_qty` an optional `product=` filter; the kill's local pass passes the trade's product, `order_placer` keeps today's behaviour | smallest blast radius; **but** two behaviours in one helper — the anti-oversell property must be re-argued for the filtered path |
| **D-B** | a separate `broker_net_qty_for_product()`; leave the existing function byte-untouched | no risk to existing callers; **but** adds a second near-identical function (the multiple-authority tax, debt-ledger #7) |
| **D-C** | make the local pass use the per-row qty like site 2 does, dropping `determine_close_direction` there | makes the two sites agree by construction; **but** loses the reverse-aware / already-flat protection that FIX-190 added |

⛔ **None of these is chosen here.** Step 1 must first establish what "already flat" means
per-product, because that is the property FIX-190 protects and every option above changes
how it is computed.

## 6. WHAT STEP 1 MUST MEASURE (before any code)

1. Does Kite report a **short MIS** and a **long CNC** on one symbol as two signed rows,
   and what does `positions()` actually return for that shape? (⛔ **paper cannot answer
   this** — V1: paper nets by symbol.)
2. `order_placer`'s emergency-exit caller: does a product filter change **its** outcome
   in any reachable case?
3. The already-flat detection: with a product filter, can the THELEELA sequence still be
   detected? **This is the acceptance criterion, not a side concern.**
4. Whether site 1 should call the helper at all, given site 2 does not.

## 7. TEST OBLIGATION — ⛔ the current suite would be vacuous

Any pin **must exercise the real `determine_close_direction`**.
`tests/unit/test_kill_switch_product_filter.py:40-41` stubs it to return the local qty by
construction, so a test added to that file as-is **cannot fail** on this defect. See
practices **§V4, second entry** — this is the incident that earned it.

## 8. LABEL & GATES

**`<CARDED — DESIGN ONLY>`.** No code, no tests, no register row (**231 stands**).
⛔ Implementation gated on: Rama's go **and** its own careful-loop.
🔴 **Blocks: THE CARRY PILOT.** ⛔ Does not block: tonight's push, Wednesday's flip.
