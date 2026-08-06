# THE CURRENT SIZING CHAIN — AS MEASURED

**06-Aug-2026 · Step 1 of the Delivery / Order-Sizing redesign**
⛔ **MEASUREMENT ONLY. No design · no proposed value · no new config key · nothing changed.**
All citations at the deployed SHA `0197923` (`capital/position_sizer.py`, 677 lines).

---

# PART 1 — THE PIPELINE IN PLAIN ENGLISH

**Read this part first. Every claim here is cited in Part 2.**

A signal arrives. The system decides how many shares to buy in **nine** steps.

### Step 1 — Pick which pocket the money comes from
Capital is split **70 % intraday / 30 % delivery**, fixed. An `INTRADAY`, `COVER_ORDER` or
`BRACKET_ORDER` signal draws on the intraday pocket; a `DELIVERY` signal draws on the delivery
pocket.

### Step 2 — Pick which rulebook applies
If this is a **delivery** trade *and* a delivery-specific value has been set, use it; otherwise use
the global one. **Today only two settings have a delivery version at all** — the risk percentage and
the position-value cap.
> 🔴 **There is NO delivery version of the concentration cap.** Delivery uses the global one. **And
> concentration is the cap that actually binds today** (§2.3 below).

### Step 3 — Work out three candidate quantities, independently

| candidate | the question it answers | what it divides |
|---|---|---|
| **by RISK** | *"How many shares before I lose more than X % of capital if the stop hits?"* | **real capital** ÷ stop distance |
| **by CAPITAL** | *"How many shares can this pocket actually pay for?"* | **pocket** ÷ (price ÷ **leverage**) |
| **by CONCENTRATION** | *"How much of my capital may sit in one stock?"* | **real capital** ÷ price |

> ⭐⭐ **THIS IS THE CRUX OF THE REDESIGN.** **Leverage appears in ONE of the three, and only there.**
> It makes the *capital* candidate bigger. It does **not** touch risk or concentration.

### Step 4 — Take the smallest of the three
The system takes whichever is smallest. Whichever won is recorded as the "binding constraint".
> ⚠️ **Nothing declares which one *should* win.** The order is **emergent** — an outcome of the
> arithmetic, not a rule anybody wrote.

### Step 5 — Apply the score-tier multiplier
HIGH ×1.0 · MEDIUM ×0.7 · **LOW ×0.5**, times a performance weight.
> 🔴 **Then the result is FLOORED AT 1 SHARE.** So the multiplier can shrink a position but can
> **never** shrink it to nothing — **the floor can RAISE the size back above what the multiplier
> asked for.** *(Exception: an exactly-zero multiplier skips the trade instead.)*

### Step 6 — *(alternative mode)* Flat rupees per order
If tier-sizing is switched off, a flat rupee amount is used as one more ceiling instead.
**No floor at 1 in this mode** — a flat below one share simply skips the trade.

### Step 7 — Round down to a whole lot
And reject if rounding threw away too large a fraction.

### Step 8 — The last gate: the position-value cap
If the final position is worth more than *X* % of capital, **the trade is REJECTED — not trimmed.**
> ⭐ **This is the one cap that genuinely supersedes everything before it.** It is a veto.

### Step 9 — Reject anything below the minimum
Then reserve the money: **the share price × quantity ÷ leverage, plus a 5 % buffer.**

---

# PART 2 — THE MEASUREMENTS

## §0.1 · Are SL and TGT placed simultaneously at the broker?

**INTRADAY — deferred, then yes, both rest together.**
**(S)** `orders/order_placer.py:116-117` (**OP-NS1**): *"LIMIT_TRIPLE is two-phase. `engine.execute`
places ENTRY only; SL + TGT are DEFERRED to fill time via `engine.place_deferred_exits`."*
⇒ nothing rests alongside the entry. **After the fill, `place_exits` places both** ⇒ **two opposite-side
exit orders DO rest simultaneously.** ⇒ the broker's "second order charged as fresh" condition is
**met after the fill**, not before it.

**DELIVERY — no exit orders exist at all. (P) MEASURED, not inferred.**
```sql
SELECT * FROM orders WHERE trade_id='trd_e66ee17b1844491db5d2e99afa6f104b';
→ exactly ONE row:  leg=ENTRY · product=CNC · status=COMPLETE
```
**Zero SL rows. Zero TGT rows.** Protection is a **broker-side GTT** (`gtt_state` 330456580).

> ### ✅ §0's DELIVERY CLAIM IS CONFIRMED BY PRODUCTION EVIDENCE — **the divisor is 1 for delivery.**
> A GTT is not an order until it triggers, and **the system places no exit orders on the CNC path at
> all.** ⛔ **Dividing delivery quantity by 3 would cut it to a third for a cost that does not exist.**
> ⚠️ **The INTRADAY half is NOT confirmed** — that both exits rest together is measured; that the
> broker *charges* for the second is **web-sourced and unverified against this account.**

## §0.2 · What does `margin_reserved` actually cover? — **entry + 5 %. Not ×3, not ×2.**

**(S)** `position_sizer.py` — the sizer's own arithmetic:
```
effective_entry_price = entry_price × (1 + entry_offset_pct)      # :417
margin_per_share      = effective_entry_price / leverage           # :418
margin_required       = final_qty × margin_per_share               # :~643
```
⇒ **ENTRY ONLY.** There is **no exit term anywhere in the sizer.**

**(S)** `capital/fund_manager.py:304` — `slm_margin_buffer_pct: float = 0.05` (**FIX-090**), deducted
at reserve time (`:710`).

**(P) The production arithmetic closes exactly:**
```
trades.margin_reserved   587.4228
fm_ledger RESERVE        616.79394        616.79394 / 587.4228 = 1.050000  ← EXACTLY 5 %
fm_ledger COMMIT         587.40           (the actual fill)
```
> 🔴 **THE ÷3 PREMISE IS REFUTED BY PRODUCTION ARITHMETIC.** The reservation is **1.05×** the entry
> value, not 3×. ⭐ **And the 5 % is an SL-MARKET buffer applied to a CNC trade that has no
> SL-Market order** — an intraday-shaped allowance inherited verbatim by delivery. Small and
> conservative, so **not a defect**; recorded because it is the same *coupled-by-omission* shape.

## §1.4 · 🔴 PER CAP: is the denominator REAL CAPITAL or PURCHASING POWER?

| # | cap | formula (verbatim) | **denominator** | leverage? |
|---|---|---|---|---|
| 1 | **RISK** | `total_capital × eff_risk_pct / sl_distance` | 🟢 **REAL CAPITAL** | ❌ no |
| 2 | **CAPITAL** | `avail / (effective_entry_price / leverage)` | 🔴 **PURCHASING POWER** | ✅ **yes** |
| 3 | **CONCENTRATION** | `total_capital × max_concentration_pct / entry_price` | 🟢 **REAL CAPITAL** | ❌ no |
| 4 | **POSITION-VALUE CAP** | `eff_max_position_value_pct × total_capital` | 🟢 **REAL CAPITAL** | ❌ no |
| 5 | **FLAT** *(OFF mode)* | `flat_value_rs / entry_price` | ⚪ flat rupees — neither | ❌ no |

> ### ⭐⭐⭐ **ONE OF FIVE USES PURCHASING POWER. THE OTHER FOUR USE REAL CAPITAL.**
> **The code DEFLATES the requirement; it does NOT inflate the base.** Because the three candidates
> are then combined with `min()`, **leverage can only ever RELAX the capital candidate — it can never
> relax risk, concentration or the value cap.**
>
> 🔴 **THE TWO MODELS ARE NOT EQUIVALENT, AND THE CARD PREDICTED THIS EXACTLY.**
> Rama's model — *"₹7k × 5 = ₹35k available"* — would inflate the **base**, so every percentage cap
> would be taken against ₹35k. The code takes them against ₹7k.
> ⇒ **A redesign that adopts the inflate-the-bucket model silently multiplies every percentage cap
> by the leverage factor.** For intraday at 5× that is a **5× larger** concentration and value cap.

**Leverage source (S) `:313-346`:** static `leverage_map` (`INTRADAY: 5.0`, `DELIVERY: 1.0`) with a
**live** override — `live_leverage = 1.0 / margin_pct` from the broker — falling back to static on
API failure or in paper mode.

## §2 · THE SUPERSEDE LADDER, AS MEASURED

| order | step | kind | supersedes by |
|---|---|---|---|
| 1 | three candidates computed **independently** | — | — |
| 2 | `raw_qty = min(risk, capital, concentration)` `:428` | ⚠️ **EMERGENT** | being smallest |
| 3 | tie-break: **CAPITAL** wins, then **RISK**, else **CONCENTRATION** `:429-437` | declared *(labelling only)* | — |
| 4 | `tiered_qty = floor(raw_qty × tier_mult × perf_weight)` `:527` | multiplier | scaling down |
| 5 | 🔴 `max(1, min(tiered_qty, raw_qty × 2))` `:530` | **FLOOR — raises size** | overriding steps 2-4 **upward** |
| 6 | *(OFF mode)* `min(raw_qty, qty_by_flat)` `:541` — **no floor** | ceiling | being smaller |
| 7 | `final_qty = (tiered_qty // lot_size) × lot_size` `:554` | rounding | — |
| 8 | lot-skew rejection `:558-575` | veto | — |
| 9 | ⭐ **POSITION_VALUE_CAP — REJECT, not clamp** `:586` | **TRUE VETO** | overriding everything |
| 10 | BELOW_MIN `:616` | veto | — |

> ⚠️ **`min()` IS AN EMERGENT ORDER, NOT A DECLARED ONE.** Nothing in the code states which cap
> *should* win — only which happens to be smallest. **Steps 5 and 9 are the only true supersedes**,
> and they point in **opposite directions**: step 5 raises quantity, step 9 vetoes it.

### §2.2 · Where enforced ≠ reported — 🔴 **TWO sites, and the second is new**

**(a) The known one — CONFIRMED at source:**
```
:585  max_position_value = eff_max_position_value_pct * total_capital   ← ENFORCES the effective
:596  "max_position_value_pct": self._max_position_value_pct            ← REPORTS the global
:609  f"({self._max_position_value_pct:.0%} of capital …)"              ← REPORTS the global
```
Identical **only** while `delivery_max_position_value_pct` is `null`. ⛔ Item 5 must close it.

**(b) 🔴 NEW, FOUND IN THIS MEASUREMENT — the concentration cap has no delivery twin at all:**
```
:381  risk_rs = total_capital * eff_risk_pct                      ← delivery-aware  ✅
:424  (total_capital * self._max_concentration_pct) / entry_price ← GLOBAL, RAW     🔴
```
The constructor accepts `delivery_risk_per_trade_pct` and `delivery_max_position_value_pct` —
**there is no `delivery_max_concentration_pct` parameter.**
> ⭐⭐ **And concentration is the cap that binds on every trade today.** ⇒ **the one lever that
> actually sets delivery size has no delivery-specific control.** ⛔ Named, not designed.

### §2.3 · The measured truth about today, which reframes the exercise
At ₹10k, **concentration binds strictly on every trade** and the tier floor then sets the quantity.
⇒ risk % and position-value % **cannot become binding**. The code says so itself at `:434`:
*"the risk-per-trade term actually bound a size (IA-P3-04 — **algebraically never today**)."*
⛔ **A redesign that adds delivery twins of inert knobs produces inert delivery knobs.**
⭐ **₹10,000 is a TESTING value — the knobs matter at scale; the ORDER matters now.**

## §3.1 · THE THREE QUANTITIES — which each consumer uses today

**Equity** = what the system calls `total`. **Purchasing power** = equity × leverage.
**Real cash** = what the broker deducts.

| consumer | quantity used TODAY | ⚠️ note — ⛔ naming only, no fix proposed |
|---|---|---|
| bucket allocation (70/30) | **equity** | split once at boot, never re-derived |
| `qty_by_risk` | **equity** | — |
| `qty_by_capital` | **purchasing power** | the only one |
| `qty_by_concentration` | **equity** | ⚠️ global cap even for delivery |
| position-value cap | **equity** | enforced effective, reported global |
| `reserve()` | **equity**, from the bucket | + 5 % SLM buffer |
| drift comparison | **equity** vs **broker free cash** | ⇒ differs by exactly what is deployed |

> ### 🔒 §3.2 · THE INVARIANT A REDESIGN MUST SATISFY — ⛔ RECORDED, NOT IMPLEMENTED
> **The SUM of all reservations across BOTH pipelines can never exceed REAL capital, whatever
> leverage says.**
> ⭐ Rama reached this from the broker side — *"orders deduct from ₹10k, not from ₹38k of purchasing
> power"* — independently of the drift finding, which is the same fact seen from the ledger side.

## §1.5 · Bucket routing — the two definitions AGREE today

```
capital/position_sizer.py:52   _INTRADAY_INTENTS = {INTRADAY, COVER_ORDER, BRACKET_ORDER}
capital/fund_manager.py:100    _INTRADAY_INTENTS = {INTRADAY, COVER_ORDER, BRACKET_ORDER}
capital/fund_manager.py:101    _POSITIONAL_INTENTS = {DELIVERY}
```
✅ **Identical membership.** ⚠️ **But they are two independent literals, not one shared constant** —
the latent debt already on record. A third intent added to one and not the other diverges silently.

---

# PART 3 — §4 · WHAT RAMA HAS ALREADY DECIDED

⛔ **Recorded, not re-opened, not designed.** Each is listed with the sizing fact it depends on.

| decision | the sizing fact it rests on |
|---|---|
| Separate Telegram vocabulary for delivery vs intraday | the `capital_at_risk` / `sl_distance_pct` vocabulary work (§C.2 G18(b)) |
| Remove the 10-minute GTT timeout | intraday-shaped; delivery horizons are days |
| Skip per-strategy R:R *(already per-strategy)* | — |
| Skip GTT auto-squareoff *(tested)* | — |
| No EOD reconcile failure on carry | `reconcile_positions` reads `positions()` only (F1) |
| Previous-day positions carried into today's book | the T+1 `holdings()` transition |
| **Max carry-days limit + Telegram countdown** | there is **no** delivery holding-period or max-hold setting anywhere in config today |
| **Telegram alert when a delivery position cannot be taken for want of capital** — strategy · symbol · qty · entry/SL/TGT · score | the sizer already computes and returns **`constraint` + `reason` + full `breakdown`** on every rejection path *(`:449-464`, `:600-614`, `:616+`)* — ⭐ **the data this alert needs already exists and is currently discarded** |

> ### ⭐⭐ THE CONNECTION §4 ASKS TO BE RECORDED
> The capital-starvation alert is **the first proposed instrument that would make *"no signals
> qualified"* distinguishable from *"starved"*.** That is **exactly the silence the F6 phantom
> produces** — a stranded reservation shrinks the delivery bucket every morning, and the symptom is
> indistinguishable from a quiet day *(see `../f6_delivery_exit_predicate_design_06aug2026.md` §15)*.
> ⛔ **Recorded as a connection, not as a justification to build either one.**

---

# OPEN QUESTIONS FOR THE DESIGN STEP — ⛔ UNANSWERED, DELIBERATELY

1. **Should the `min()` order become DECLARED rather than emergent?** If so, what is the intended
   precedence, and what happens when the intended winner is not the smallest?
2. **Which model is correct — deflate the requirement, or inflate the base?** They differ by the
   leverage factor on **every percentage cap**. ⛔ This is a Rama ruling, not a code question.
3. **Should concentration get a delivery twin?** It is the only binding cap today and the only one
   with no delivery-specific control.
4. **Should the floor-at-1 apply to delivery?** It can raise a delivery position above what risk,
   capital and concentration jointly allowed — at ₹10k that is the difference between one share and
   none.
5. **Does the 5 % SLM buffer belong on a CNC trade** that has no SL-Market order?
6. **Is the broker's "second exit order is charged" behaviour real on THIS account?** ⛔ Unverified;
   it decides whether the intraday divisor is 1 or 2.
7. **Should the two `_INTRADAY_INTENTS` literals become one shared constant?**
8. **What is the max-carry-days limit measured against** — calendar days, trading days, or settlement
   days?
9. **Where should the "cannot take a position for want of capital" alert fire** — the sizer already
   has the data at each rejection, but firing there would alert on every rejected signal, not only
   capital-starved ones.
