# Slice 2.5 (CNC delivery lifecycle) — decomposition

**Date:** 2026-07-26 (Sunday, market closed) · **HEAD:** `e808595`
**Status:** ⛔ **SCOPING ONLY — nothing built, nothing designed.**

---

## The three headline answers

1. **16 pieces.** ⭐ **4 are already PROVEN LIVE with real money** (10-Jul canary), 2 are already
   correct, **7 are built-but-never-executed** (all inside the live service), 1 is unproven-live,
   2 are not started.
2. **2 live-market-gated items**: the **`--arm-overnight` DDPI/demat proof** (needs a Mon→Tue pair)
   and the **live-carry pilot** of the service's own path. ⭐ Everything else is desk work.
3. ⚠️ **25–40 h is the wrong shape, not just the wrong number.** Honest desk work: **~14–22 h**;
   total **~25–45 h with an irreducible tail**. But ⭐ **the binding constraint is the CALENDAR, not
   hours** — the remaining live proofs are sequential and each needs a separate supervised window.

> ⭐⭐ **CORRECTION TO WHAT RAMA WAS LAST TOLD (and to my own 26-Jul report).** T2 was described as
> *"canary-ready."* It is further along than that: **the live same-day canary ALREADY PASSED on
> 10-Jul 14:26–14:41 IST with real orders on four symbols** — BUY LIMIT accepted and filled, a real
> OCO-GTT placed/verified/deleted on each, ending flat, ~₹0.2 total cost. What is *ready* is the
> **next** leg: `--arm-overnight`, which is the only way to prove the DDPI/demat sell.
> ⚠️ I previously read live `gtt_state = 0` as "the GTT lifecycle never ran." **That inference was
> wrong** — T2 writes to an isolated throwaway store by design. The zero means the *live service*
> has never placed a GTT, which is a different (and still true) claim.

---

## ⭐ C3 FIRST — the load-bearing constraint has CHANGED. Verify before planning on it.

**The instruction's premise:** *"delivery is blocked from live until the CNC lifecycle is built and
paper-proven, because CNC positions would still be force-squared at 15:15 and 15:17 without it."*

**Measured against source: that premise is REFUTED for LIVE mode.**

`orders/eod_squareoff.py:1064-1073` —

```python
# FIX-015: Filter to intraday products only (MIS/CO). EOD6 design
# mandates DELIVERY (CNC/NRML) positions are never touched.
broker_qty = {
    p.symbol: abs(int(p.qty))
    for p in broker_positions
    if int(p.qty) != 0 and p.product in ("MIS", "CO")
}
```

- The **15:17 EOD squareoff** filters to `("MIS", "CO")`. A CNC position is invisible to it.
- The **residual sweep** applies the same filter (`:1439`).
- The module docstring states it as a design invariant, twice (**EOD6**, `:23` and `:34`).
- The **15:15 circuit breaker** does not flatten at all — it `soft_kill`s and delegates
  ("EOD squareoff handles positions", `main.py:691`).

⇒ **A live CNC position would NOT be force-squared today.** The exemption is built, deliberate, and
documented. The slice does not exist to create it.

### ⚠️ But it IS true in PAPER — and that is the finding

`broker/zerodha_adapter.py:2158-2166`, the paper position map:

```python
self._paper_positions[symbol] = {
    "qty": new_qty,
    "avg_price": fill_price,
    "side": "BUY" if new_qty > 0 else "SELL",
    "product": "MIS",          # ← hardcoded, whatever was actually ordered
}
```

A paper CNC position is reported as `product="MIS"`, so the EOD filter at `:1072` **does not exempt
it** and the squareoff flattens it.

⭐⭐ **This is the single most consequential item in the slice, and it is not on anyone's list.**
The stated gate for going live is *"built and paper-proven"* — and **paper currently cannot prove
the thing that must be proven.** A paper carry test would flatten at 15:17 and be read either as a
lifecycle bug (it is not) or, worse, as evidence that the exemption "doesn't work" (it does — in the
only mode that matters). **The paper-proof gate is blocked by a one-line fidelity hole**, not by
missing lifecycle code.

**Restated constraint (what is actually true):** delivery is blocked from live because the CNC
lifecycle has **never executed once, in any mode** — not because the squareoff would eat it.

---

## C1 + C2 — the 16 pieces

Legend — **BUILT✓** verified working · **BUILT⚠** exists but has *never executed* · **PART** partial ·
**NOT** not started · **OPS** operator/broker action.

### ⭐⭐ The distinction that organises everything below

**T2 is a STANDALONE OPERATOR SCRIPT, not the live system's delivery path.**
`scripts/t2_cnc_gtt_realtest.py` builds its **own** `ZerodhaAdapter` + `CncGttPlacer` against a
**throwaway** store. So a T2 pass proves **broker mechanics** (CNC accepted, OCO-GTT places/verifies/
deletes, DDPI sell works) — it proves **nothing** about the live service's own delivery wiring.

⚠️ **Do not let a T2 pass be read as "the lifecycle works."** They are different systems exercising
the same broker.

| # | piece | state | evidence | gate |
|---|---|---|---|---|
| **BROKER MECHANICS — proven by the T2 script** | | | | |
| 1 | CNC entry placement, real money | ⭐ **PROVEN LIVE** | 10-Jul 14:26–14:41: BUY LIMIT accepted+filled on IDEA/JIOFIN/NTPC/ONGC | — |
| 2 | OCO GTT place → verify → delete | ⭐ **PROVEN LIVE** | 4 real GTTs `327073639`/`327076809`/`327077052`/`327077301` ACTIVE+verified, deleted once flat | — |
| 3 | `gtt_state` durability, schema v36 | **EXERCISED (isolated)** | rows written ACTIVE→CANCELLED in the throwaway store; ⚠️ **live `gtt_state` = 0 rows BY DESIGN, not by failure** | — |
| 4 | Marketable-LIMIT placement (`8773053`) | ⭐ **PROVEN LIVE** | fixes the 10-Jul `InputException` MARKET rejection | — |
| 5 | **DDPI / holdings-sell (demat debit)** | ❌ **UNPROVEN** | same-day square is a *day position* — no demat debit ever occurred | 🔴 **live, overnight** |
| **LIVE-SERVICE DELIVERY PATH — none of this has ever run** | | | | |
| 6 | `CncGttMonitor` 15-min lifecycle (P2) | **BUILT⚠** | never fired; needs an ACTIVE row in the **live** store | flags |
| 7 | Reconciler delivery exclusion (P2) | **BUILT⚠** | `get_active_gtt_states()` has always returned `[]` | flags |
| 8 | FIX-183 orphan-GTT adoption (`af4b784`) | **BUILT⚠** | never adopted anything | flags |
| 9 | Boot rehydration of a carried CNC | **BUILT⚠** | `main.py:2616 hydrate_from_store()` — always 0 | flags |
| 10 | Delivery count caps (PHASE-3 `29f669e`) | **BUILT⚠** | `max_open_delivery_positions: 3`, inert | flags |
| 11 | Conditional capital allocation (PHASE-3) | **BUILT⚠** | `conditional_allocation_enabled: false` | flags |
| 12 | `trade_type` gate + reject label (PHASE-4 `37b3db3`) | **BUILT⚠** | — | flags |
| **ALREADY CORRECT — no work** | | | | |
| 13 | **EOD/15:17 CNC exemption (EOD6/FIX-015)** | ⭐ **BUILT✓** | verified from source today; live-correct | — |
| 14 | Triple-lock config safety net | ⭐ **BUILT✓** | `config_auditor.py:196` + `raise_if_blocked()` | — |
| **NOT STARTED** | | | | |
| 15 | **Paper CNC product fidelity** | ❌ **NOT** | `zerodha_adapter.py:2165` hardcodes MIS | desk |
| 16 | §4c config foot-gun + §4d GTT-linkage-loss edge | ❌ **NOT** | reported 30-Jun, never hardened | desk |

### ⚠️ The built-and-never-run check (C2)

**7 pieces (#6–#12) have never executed once**, and they are precisely the ones inside the live
trading service. Proven, not assumed:

- `orders` with `product='CNC'` — **3 in all history**, all from the 15-Jun first-day incident that
  *created* the lock. `MIS`: **710**.
- Live `gtt_state` — **0 rows ever.** ⚠️ **This is NOT evidence that GTT code is broken or unrun** —
  T2 deliberately wrote to an isolated throwaway DB. It IS evidence that **the live service has never
  placed or tracked a GTT**, because only the live path writes the live store.
- Every component triggered solely by an ACTIVE row in the **live** `gtt_state` (#6, #7, #8, #9) has
  therefore provably never done work.

⭐ **This project's own record says that matters.** FIX-029's consumer-death detector had never run
and was found switched off by another module's wiring gap; ShadowTracker ran daily and *fabricated*
141 rows; the T2 proof script bit-rotted into three signature drifts plus a broker rejection while
sitting unexecuted. **"Built" is not a state that survives contact with first execution here.**

### ⭐ Cross-link to §A (same defect, already worked around)

`orders/cnc_gtt_monitor.py:136-140` states plainly why FIX-183 exists:

> *"an adopted row excludes the carried CNC trade from CHECK1 BEFORE CHECK1 can mis-mark it
> CLOSED_MANUAL (the C2.1 gap: a carried holding lives in holdings(), not positions(), so the
> reconciler's bp is None)."*

**Slice 2.5 already had to build a workaround for the CHECK1 over-claim investigated in
`check1_external_close_26jul2026.md`.** A carried CNC holding is the *permanent* form of the same
false premise ("no position ⇒ closed externally"). Piece #6 is load-bearing **only because CHECK1 is
wrong**; fix CHECK1 properly and #6 becomes belt-and-braces. ⚠️ Do not schedule them as unrelated.

---

## C4 — live-gated vs desk work

### 🔴 Requires a live market day + Rama (the schedule constraint)

| piece | why | duration |
|---|---|---|
| ~~T2 same-session round-trip~~ | ✅ **DONE 10-Jul** — real orders, 4 symbols, ended flat | — |
| **T2 `--arm-overnight` (#5, DDPI/demat)** | a same-day square is a *day position*; only a genuine overnight hold produces a demat debit | **1 Mon→Tue pair**, supervised at both ends; script ready |
| **Live-carry pilot (#6–#9)** | the *service's own* path must place, monitor, survive a restart, and rehydrate a real GTT | **≥2 consecutive trading days** after flags flip |

⚠️ **Strictly sequential.** The live-carry pilot cannot start until the DDPI leg passes *and* the
flags are flipped. Minimum wall-clock from today to a completed carry pilot is **4 trading days**,
and that assumes nothing surfaces — optimistic with 7 never-run components.

⭐ **Note the asymmetry:** the DDPI leg is *low-risk* (one share, standalone script, ~₹0.2, cannot
touch the live service) but *high-latency* (needs two consecutive days). The carry pilot is the
reverse — it runs the real service with real flags flipped. Do not schedule them as one thing.

### 🟢 Desk work (no market needed — can be done any time, including today)

- **#15 paper CNC product fidelity** ⭐ **do this first — it unblocks all paper proving**
- #16a config foot-gun hardening
- #16b GTT-linkage-loss edge
- paper-carry validation (blocked on #15)
- a bit-rot re-read of #6–#9 against current main — **exactly what T2 turned out to need**, and those
  four have sat unexecuted even longer

---

## C5 — honest estimate

| bucket | h | confidence |
|---|---|---|
| #15 paper CNC fidelity fix + parity tests | 2–4 | **high** — one field, but on the paper fill path |
| paper-carry validation (setup + observe + read) | 3–5 | medium |
| #16a config foot-gun | 2–3 | high |
| #16b GTT-linkage-loss edge | 4–6 | **low** — a genuine design question, not a patch |
| bit-rot re-validation of #6–#9 vs current main | 3–4 | medium — T2 needed exactly this and it took a session |
| **desk subtotal** | **14–22** | |
| T2 `--arm-overnight` supervision (2 windows) | 1–2 | high — script re-validated 26-Jul, VM copy byte-identical |
| live-carry pilot supervision | 2–4 | medium |
| ⚠️ **first-run defect fallout, 7 never-run components** | **?** | ⭐ **not estimable** |
| **total** | **~25–45** | wide tail |

### ⚠️ On the 25–40 h figure already given to Rama

**The number is defensible; the shape is misleading, and the shape is what he is planning around.**

- It reads as "25–40 hours of building." It is not. **The building is ~90 % done** — 14 of 16 pieces
  exist, and **4 of them are already proven with real money.** Only 2 genuinely need writing, and one
  (#15) is a single field.
- The cost is **not** in construction. It is in **first execution of 7 components that have never
  run**, plus **≥4 trading days of strictly-sequential supervised live windows** that no amount of
  desk effort can compress.
- ⭐ **The binding constraint is calendar, not hours.** Rama should plan around *"3 separate
  supervised market windows, sequential, each gated on the previous passing"* — a schedule shape —
  not a block of hours he can allocate on a weekend.

**Three corrections to the scope as previously understood:**
1. ⬇️ **#13 (EOD exemption) is not work** — already built, verified from source today.
2. ⬇️ **T2's same-day leg is not work** — it passed live on 10-Jul. Only `--arm-overnight` remains.
3. ⬆️ **#15 (paper CNC fidelity) was on nobody's list** and blocks the entire paper-proof gate.

---

## What was NOT done

⛔ No build, no design, no change to any delivery path. Read-only throughout; the live DB was opened
`mode=ro&immutable=1`. Flags remain `delivery_enabled: false`, `force_intraday_only: true`,
`trade_type: INTRADAY`, `conditional_allocation_enabled: false`.
