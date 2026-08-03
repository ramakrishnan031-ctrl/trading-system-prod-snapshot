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

## 4a. ⭐ STEP-1 MEASUREMENT (03-Aug 12:0x) — THE SITE INVENTORY, REPO-WIDE

⛔ **CORRECTION TO §4 ABOVE, AND IT IS MINE:** §4 said the helper has *"two other
callers"* and named `order_placer`'s emergency exit as *"the other"*. **A repo-wide sweep
finds THREE production call sites, not two.** §4 was written from the drill's single site
plus memory; this is the M1 failure the practices name (*repo-wide, never file-wide*)
committed in my own record. Corrected here rather than quietly rewritten.

**`broker_net_qty` has exactly ONE production caller** — `determine_close_direction`
itself (`position_helpers.py:58`). Everything else is tests. So the helper's blast radius
is entirely mediated by `determine_close_direction`, which has **three**:

| site | location | what it is | product in scope? | status |
|---|---|---|---|---|
| **A** | `kill_switch.py:1613` | HARD_KILL local pass, **first attempt** | ✅ `raw_product` is read 40 lines above (`:1572`) | 🔴 **THE MEASURED DEFECT** |
| **B** | `kill_switch.py:1774` | **H-4 retry**, same method | ⚠️ only `intent` (from the `failed_trades` tuple) — **lossy: NULL/unknown collapsed to INTRADAY** | 🔴 **MUST MOVE WITH A** |
| **C** | `order_placer.py:3885` | `_emergency_market_exit` (FIX-148) | ⚠️ `fill_entry.intent` — cached on the **ENTRY** leg; the class docstring says exit legs are *"populated for symmetry but not consulted"* | 🟡 **SAME EXPOSURE, but DORMANT — "0 executions ever (audit P5.2)"** |

### ⛔⛔ 4b. THE COUPLING THAT DECIDES THE DESIGN — site B is not optional

Site B exists **precisely because the retry once diverged from the first pass.** H-4's own
words: *"The first pass is broker-net-aware (`determine_close_direction`); the retry was
not."* Its in-code comment says it re-derives **"to mirror the first pass"**.

⇒ **If A becomes product-aware and B does not, we re-create H-4's exact bug** — a first
pass and a retry computing different quantities for the same position. **A and B move
together or not at all.**

⚠️ **And B's product input is WEAKER than A's:** B carries `intent`, not `raw_product`, and
`_PRODUCT_TO_INTENT` maps NULL/unknown → `INTRADAY`. So at B a NULL-product trade is
**indistinguishable from a genuine MIS trade**. Any product-aware retry must either carry
the raw product forward in `failed_trades` or re-read it — **a real cost, and it is B's,
not A's.**

### ⭐⭐ 4c. THE INSIGHT THAT CHANGES THE FIX: the net is used for TWO questions

`determine_close_direction` uses one number to answer two different questions, and
**product-filtering has OPPOSITE correct answers for them:**

| question | today | correct under a mixed book |
|---|---|---|
| **"how many shares do I sell?"** | symbol-net | ⛔ **must be PRODUCT-FILTERED** — else we sell into the spared CNC (the measured defect) |
| **"is it already flat — should I fire at all?"** (FIX-190's anti-oversell) | symbol-net | ⚠️ **must NOT be naively product-filtered** — a position held under an **unexpected/NULL** product would read as net 0 and we would **skip an exit that is genuinely needed** |

⇒ **a naive `product=` filter fixes the quantity and introduces a NEW failure mode in the
same call** — turning a real position invisible. **This is the THELEELA class re-opened
from the other direction**: FIX-190 stops us firing when flat; a filtered net could stop us
firing when *not* flat.

⛔ **Therefore any fix must answer BOTH questions — a filtered quantity AND an unfiltered
already-flat check — not one filtered number.** This is the design's acceptance criterion
and it disqualifies the simplest form of D-A as written.

## 5. DESIGN DIRECTIONS — re-scored against 4a-4c, ⛔ still none chosen

| # | shape | measured blast radius | verdict after 4a-4c |
|---|---|---|---|
| **D-A** | optional `product=` on `broker_net_qty`, threaded through `determine_close_direction`; `None` = today's behaviour | `broker_net_qty`: **1** production caller (contained). `determine_close_direction`: **3** sites, but a `None` default leaves **B and C byte-identical** unless they opt in | ⚠️ **VIABLE ONLY IN ITS TWO-ANSWER FORM.** As originally written (one filtered number) it **fails 4c** — it would fix the quantity and blind the already-flat check |
| **D-B** | a separate `broker_net_qty_for_product()` / parallel close-direction fn | **zero** change to existing callers | ⚠️ Duplicates the reverse-aware + already-flat logic ⇒ **debt-ledger #7's multiple-authority tax**, two functions that must never drift. And A+B still both have to switch, so it buys less isolation than it looks |
| **D-C** | site A uses the per-row qty like the sweep does, dropping `determine_close_direction` there | site A only — *apparently* the smallest | ⛔⛔ **DISQUALIFIED ON MEASURED GROUNDS.** It **re-opens H-4** (site B still calls `determine_close_direction`, so the first pass and the retry would compute quantities differently — the exact divergence H-4 was created to fix), **and** it drops FIX-190's already-flat protection at site A, which is what prevents THELEELA |

### ⭐ 5a. WHERE THE FIX BELONGS — helper, call site, or `determine_close_direction`?

- ⛔ **Not in `broker_net_qty` alone.** It is a pure summing primitive with one caller; a
  `product=` there fixes the quantity but cannot express 4c's *two* questions.
- ⛔ **Not at the call sites alone.** Sites A and B are **coupled** (4b) and site C has a
  weaker product input (4a); pushing the logic outward means writing it **three times** and
  guaranteeing the drift H-4 already punished once.
- ✅ **`determine_close_direction` is the right seam** — it is the single place that already
  owns *"should I fire, and for how much"*, all three sites go through it, and 4c's two
  answers belong to **one** decision. **The shape to design against: it returns a
  product-filtered QUANTITY while keeping the already-flat test on the UNFILTERED net.**

**Recommended direction (⛔ recommendation, not a decision): D-A in its two-answer form, at
`determine_close_direction`, with A and B opting in together and C left explicitly
deferred** (it is dormant — *0 executions ever* — and its product input is the least
trustworthy; fixing it needs plumbing the other two do not).

### 5b. ⛔ WHAT STEP 2 MUST NOT DO

- ⛔ Do **not** narrow `broker_net_qty` globally — its unfiltered answer is still the
  correct input to the already-flat test (4c).
- ⛔ Do **not** change A without B (4b).
- ⛔ Do **not** treat C as fixed because A and B are — record it as a known remaining site.
- ⛔ Do **not** rely on `intent` as a product at site B without confirming the NULL/unknown
  collapse (4a) — a NULL-product trade currently looks like MIS there.

## 6. STEP 1 — WHAT IS NOW MEASURED, AND WHAT REMAINS OPEN

**✅ ANSWERED 03-Aug (§4a-4c):** the full site inventory (**three**, not two) · the A↔B
coupling and why it is binding · site C's dormancy and its weaker product input ·
`broker_net_qty`'s single production caller · **and the two-questions insight, which is the
real acceptance criterion and was not visible before the sweep.**

**⛔ STILL OPEN — Step 2 may not begin until these are measured:**

1. **The broker's actual shape.** Does Kite report a **short MIS** and a **long CNC** on
   one symbol as two signed rows, and what exactly does `positions()` return? ⛔ **Paper
   cannot answer this** (§V1: paper nets by **symbol**, live Kite per **(symbol,
   product)**) ⇒ this must be answered from **live semantics or by construction**, never a
   paper drill.
2. **The already-flat acceptance test.** With the 4c split, can the **THELEELA sequence
   still be detected**? Concretely: MIS flat, CNC long ⇒ the MIS exit must fire **nothing**;
   and a position held under an **unexpected/NULL** product must **still** be seen by the
   already-flat check. ⛔ **This is the acceptance criterion, not a side concern.**
3. **Site B's product source.** Carry `raw_product` forward in `failed_trades`, or re-read
   it per retry? Both are real changes to the retry loop, and the NULL→INTRADAY collapse
   must not survive either.
4. **Site C's disposition.** Deferred-with-a-note, or fixed with `fill_entry.intent`? ⛔ The
   docstring's *"exit legs populated for symmetry but not consulted"* means its `intent` is
   **unverified on the leg that matters** — measure before relying on it.

## 7. TEST OBLIGATION — ⛔ the current suite would be vacuous, and here is the exact pin

Any pin **must exercise the real `determine_close_direction`**.
`tests/unit/test_kill_switch_product_filter.py:40-41` stubs it to return the local qty by
construction, so a test added **to that file as-is cannot fail** on this defect. See
practices **§V4, second entry** — this is the incident that earned it.

**The pin, stated now so Step 2 cannot drift into a comfortable test:**

| # | case | must assert |
|---|---|---|
| 1 | local **MIS 10** trade; broker `SYM MIS 10` + `SYM CNC 5` | site A sells **10**, not 15 — ⭐ **this is the RED-on-old case; it fails today** |
| 2 | same book, **retry** path (site B) | the retry also sells **10** — ⛔ **A and B must agree** (4b / H-4) |
| 3 | MIS **flat**, CNC long | the MIS exit fires **NOTHING** (already-flat still works per-product) |
| 4 | position under a **NULL/unknown** product | the already-flat check **still sees it** — ⛔ the 4c regression guard, and the one a naive filter breaks |
| 5 | THELEELA replay (`test_fix190_incident_replay.py`) | **unchanged and still green** — FIX-190's protection intact |
| 6 | single-product book (today's normal case) | byte-identical behaviour to pre-fix |

⛔ **Cases 3 and 4 are the ones that make the fix safe; case 1 alone would ship the new
failure mode described in 4c.** ⚠️ And the existing FIX-190 / H-12 / H-4 suites
(`test_fix190_exit_safety.py`, `test_h12_paper_positions_signed.py`,
`test_h4_killswitch_retry_rederive.py`) must be run as the **blast-radius set** — they are
the current owners of this helper's contract.

## 8. LABEL & GATES

**`<CARDED — DESIGN ONLY>`.** No code, no tests, no register row (**231 stands**).
⛔ Implementation gated on: Rama's go **and** its own careful-loop.
🔴 **Blocks: THE CARRY PILOT.** ⛔ Does not block: tonight's push, Wednesday's flip.
