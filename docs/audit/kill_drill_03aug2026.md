# #2 KILL DRILL — MONDAY PC GATE (03-Aug-2026) — **9/10 PASS, ONE RED**

**⛔ FINDINGS-ONLY. NOTHING WAS FIXED, and nothing here authorises a fix.**
The RED cell needs its own card (G3: disclose, don't expand).

**Gate status: (b) kill drill = RED.** Per the day's card, a RED gate outranks every
backlog item, so the work sequence stopped here and was reported.

---

## 1. SAFETY — what the drill could and could not touch

Harness: `scratchpad/kill_drill_03aug.py` — **validation tooling, deliberately NOT
committed** (same posture as the 16-Jul `mc_cluster_drill.py`).

- **No network, no Zerodha, no real orders** — the adapter is a `MagicMock`.
- **The live DB was never opened.** The store is a `MagicMock`, so no sqlite path
  exists in the run at all. Proven externally as well: `data_store/trading_system.db`
  mtime **`2026-07-27 15:37:27.138808200` before AND after** — byte-for-byte the same
  timestamp.
- PC only. **The VM stays on `297b587`;** nothing was deployed.

## 2. WHAT THIS DRILL ADDS OVER THE UNIT SUITE — and why it found something

`tests/unit/test_kill_switch_product_filter.py:40-41` monkeypatches the broker seam:

```python
monkeypatch.setattr(ks_mod, "determine_close_direction",
                    lambda _a, _s, side, qty: (side, qty))
```

⇒ the stub **returns the local qty by construction**, so **no test in that file can
observe what the real helper computes from broker truth.** The eight tests there are
sound about *which* positions are flattened; they are structurally blind to *how many
shares* site 1 sells.

⭐ This is the campaign's own named class — *a mock whose return value you order cannot
test the thing you ordered* — and it is why the drill uses the **real**
`determine_close_direction` / `broker_net_qty`.

**Scope honesty (V1):** this drill proves the **system-side decision path**. It does
**not** prove broker-side netting semantics, and `<VERIFIED LIVE>` remains unreachable
(HARD_KILL has never fired).

## 3. CASE A — the card's mixed book (distinct symbols): **9/9 PASS**

Local trades: `AAA` MIS 10 · `BBB` CNC 5 · `CCC` NULL 7.
Broker rows: those three plus sweep-only `DDD` MIS 3 · `EEE` CNC 4 · `FFF` "" 6.

Orders actually placed: `AAA SELL 10`, `CCC SELL 7` (local pass) · `DDD SELL 3`,
`FFF SELL 6` (sweep).

| # | check | result |
|---|---|---|
| A1 | local MIS flattened | ✅ |
| A2 | **local CNC SPARED — no order at all** | ✅ |
| A3 | local NULL flattened | ✅ |
| A4 | sweep MIS flattened | ✅ |
| A5 | **sweep CNC SPARED — no order at all** | ✅ |
| A6 | sweep NULL flattened | ✅ |
| A7 | spared positions LOGGED at both sites | ✅ 3 `SPARED delivery position` CRITICALs |
| A8 | NULL product raised CRITICAL at both sites | ✅ 2 `UNKNOWN PRODUCT` CRITICALs |
| A9 | the CRITICAL actually escalated to the notifier | ✅ `notifier.send` = 2 |

⇒ **The buy-day product filter does exactly what #2/#2b claim, on the book the card
specified.**

## 4. CASE B — SAME-SYMBOL MIXED BOOK: 🔴 **RED**

**Setup:** one local MIS trade on `SYM` qty **10**; the broker holds **`SYM` MIS 10
AND `SYM` CNC 5** (Kite `positions()` is per `(symbol, product)`, so this is a normal
two-row book, not a contrived one).

**Q4 says the CNC 5 must survive. Measured:**

```
place_order: ('SYM', 'SELL', 15, 'INTRADAY', 'ks_hard_kill_exit')
broker_net_qty('SYM') = 15      # sums ALL products for the symbol
```

⛔ **Site 1 sells 15 against a 10-share intraday position — the extra 5 come out of the
SPARED delivery holding.** The position is spared by the filter and then partially sold
by the exit that follows it.

### 4a. Root cause — measured, one function

`broker/position_helpers.py:34-39` — `broker_net_qty` sums every position row whose
**symbol** matches, with **no product filter**:

```python
for p in positions or []:
    if getattr(p, "symbol", None) == symbol:
        net += int(getattr(p, "qty", 0) or 0)
```

`kill_switch.py:1613` (site 1, the local pass) consumes it via
`determine_close_direction` to choose `(side, qty)`.

⭐ **Site 2 (the broker sweep) is CORRECT and shows the contrast:** it uses the
**per-row** `pqty` (`:1665`) and never calls the helper — which is exactly why
`test_site2_spared_cnc_does_not_suppress_same_symbol_mis_row` can assert `qty == 4` and
pass. **The two sites disagree, and only one of them was measured.**

### 4b. This is PRE-EXISTING and ALREADY LIVE — it is not introduced by tonight's push

- `git diff 297b587..HEAD -- broker/position_helpers.py` = **EMPTY** ⇒ the helper is
  **byte-identical to what is running on the VM right now**. Its last edits were
  `b49764f` / `870bbc9` (FIX-190), long before this campaign.
- `determine_close_direction` has been called at site 1 since FIX-190; the #2 work did
  not add it.

⭐⭐ **AND THE PUSH IS A STRICT IMPROVEMENT, MEASURED:** `297b587` (deployed) contains
**0** occurrences of `SPARED delivery position`; HEAD contains **2**. So **production
today flattens the ENTIRE CNC holding under a HARD_KILL**. After tonight's push it
spares all of it *except* the same-symbol overlap quantity.

⇒ **Not a reason to hold the push.** It is a reason to correct the *claim* the push
makes, and to card the residual gap.

### 4c. Reachability — LATENT, but the conditions are half-present already

Needs **all** of: a HARD_KILL fires · the same symbol holds a live intraday position
· that symbol also holds CNC.

- **HARD_KILL has never fired** (measured, standing fact) ⇒ latent today.
- ⚠️ **The CNC half already exists:** the T2 basket has held **5 CNC positions
  overnight since 29-Jul**. The collision needs only an intraday signal on one of those
  same symbols.
- 🔴 **The carry pilot raises this materially** — it exists to hold delivery while the
  system trades intraday, which is precisely the two-row book above.

Per the live-vs-latent rule: **LATENT ⇒ document + pin with a test that fails when it
becomes reachable.** ⛔ The pin is **NOT written here** — it belongs to the card that
owns the fix.

### 4d. What is owed (⛔ recorded, NOT done)

1. **A card for the fix.** The obvious shape — make the flatten qty product-aware
   rather than symbol-net — touches a **shared** helper with **two** other callers
   (`order_placer`'s emergency exit is the other; FIX-190's whole purpose was
   anti-oversell), so it is careful-loop work, not a one-liner. ⛔ Do not narrow
   `broker_net_qty` in passing: FIX-190 exists to *prevent* an oversell, and a careless
   product filter there could re-open the THELEELA class.
2. **A label correction.** #2/#2b's "delivery survives a HARD_KILL" is true **except on
   a same-symbol mixed book**. The claim should carry that qualifier.
3. **A test that is not blind.** Any pin must exercise the REAL
   `determine_close_direction`; the current file's stub would make it vacuously green.

---

## 5. THE OTHER TWO GATES

- **(c) calm regression confirm** — run separately, reported with this gate.
- **(a) composition boot** — ⛔ **NOT RUN.** Deliberately withheld: it starts a paper
  session on a live trading day, and it was not run while an open finding on the kill
  path was unreported. It is Rama's call whether it proceeds today.

## 6. LABEL

**`<MEASURED — ONE RED, NOT FIXED>`.** No code changed. No register row (231 stands).
The RED cell is a disclosure awaiting its own card and authorisation.
