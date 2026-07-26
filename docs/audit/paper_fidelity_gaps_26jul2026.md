# Paper-mode fidelity gaps — how much of the paper adapter is a STUB rather than a SIMULATION

**Date:** 2026-07-26 · **Branch:** `hold-check1-w8-26jul` · **Subject:**
`broker/zerodha_adapter.py` (the only broker adapter with a paper branch)
⛔ **SWEEP ONLY — NOTHING FIXED.** Several of these are legitimate by design; deciding which
is a design conversation, not a sweep.

---

## The headline

**Of the 19 adapter methods that answer a broker question, 9 return a constant outcome and 5
more are state-backed but carry a field that can never vary — 14 of 19.** Two of them gate
mechanisms Slice 2.5 depends on, and one gates the §D deferral built this weekend.

*(22 methods contain an `if self._paper` branch; 3 are paper-only seams or setters —
`seed_paper_holding`, `set_paper_capital`, `set_slippage_engine` — and are excluded, leaving
the 19 counted above.)*

**The three that gate Slice 2.5, named:**

| # | stub | what it structurally prevents paper from rehearsing |
|---|---|---|
| **1** | `_paper_gtts` status is only ever written `"active"` | ⭐ **A GTT never fires in paper.** `CncGttMonitor`'s PRIMARY path (GTT triggered + holding flat → `GTT_EXIT`) and its F6 re-protect branch cannot be reached by a running paper service. This is Slice 2.5 risk **#2** — the piece that can `SOFT_KILL`. |
| **2** | `_paper_holdings` is written **only** by `seed_paper_holding()`, which **no production code calls** | ⭐ **A paper position never becomes a paper holding.** `get_holdings()` returns `[]` forever in a paper *run*. This is the mechanism behind "paper cannot model the overnight position→holding transition" — the Mon→Tue live pair is irreducible because of this line, not in general. |
| **3** | `cancel_order` returns `success=True` unconditionally | The §D mid-fill deferral is unreachable in paper. See `check1_deferral_26jul2026.md` Limit 1. |

⚠️ **And the sharpest one, because it is easy to misread as coverage.** The never-run-pieces
doc credits `CncGttMonitor` and the FIX-183 prepass with **25 unit tests driving the real
paper adapter**. That is true and the credit stands — but those tests reach the `triggered`
state by writing the adapter's **private dict** directly:

```python
env.adapter._paper_gtts[str(gid)]["status"] = "triggered"   # test_cnc_gtt_monitor.py:110
```

**A test that must reach past the public API to create a state is telling you the simulation
cannot produce that state.** So "25 tests on the real paper adapter" means *the branch is
unit-covered*; it does **not** mean a paper session can ever demonstrate it. Those are
different claims and only the first is true here.

---

## The full sweep

### A — returns a constant outcome (9)

| method | line | paper returns | can it ever be otherwise? |
|---|---|---|---|
| `cancel_order` | 909 | `success=True, reason=""` | **no** — succeeds for unknown ids too |
| `modify_order` | 982 | `success=True, reason=""` | **no** — never validates anything |
| `place_gtt` | 691 | a mock id, always | **no** — cannot fail |
| `modify_gtt` | 737 | `str(gtt_id)`, always | **no** — unknown id silently no-ops and still reports success; live raises |
| `delete_gtt` | 817 | `str(gtt_id)`, always | **no** — same asymmetry: `pop(…, None)` then success |
| `get_trades` | 1678 | `[]` | **no** |
| `get_quote_raw` | 1653 | `{}` | **no** |
| `get_live_margin_pct` | 1398 | raises `BrokerError` | **no** — deliberate: the caller falls back to the static table |
| `get_server_time` | 1547 | `now_ist()` | **no** — so measured clock skew is **always exactly 0** |

### B — state-backed, but with a field that never varies (5)

| method | line | the frozen part |
|---|---|---|
| `get_gtts` | 802 | `status` is only ever `"active"` — nothing writes `triggered`/`expired`/`rejected` |
| `get_gtt` | 787 | same store, same frozen `status` |
| `get_margins` | 1330 | `used=0.0` **hardcoded**; `available == net == _paper_capital`, so headroom never shrinks as positions are taken |
| `get_order_history` | 1032 | `rejection_reason=""` hardcoded, and **always exactly one entry** — live returns the full transition history |
| `get_open_orders` | 1720 | `"status": "OPEN"` hardcoded — a resting paper SL never shows as `"TRIGGER PENDING"`. Three production sites branch on that *broker* status (`order_monitor.py:84`, `zerodha_adapter.py:1745`, `scripts/eod_broker_reconcile.py:237`); the other matches in the tree read our own `orders.status`, which is written by us and unaffected |

### C — genuine simulations (5) — listed so the sweep is honest about what *is* modelled

`place_order` → `_paper_place_order` (state store, tick snapping, injected slippage engine,
threaded async fill) · `get_positions` (signed net qty from `_paper_positions`) ·
`get_holdings` (from `_paper_holdings` — real, but see gap #2) · `get_all_orders` (from
`_paper_fills`, tag- and product-correlated for parity) · `get_quote` (delegates to an
injected `quote_provider`; raises if none — a seam, not a stub).

**Reconciliation: 9 + 5 + 5 = 19.** Every method is in exactly one bucket.

### D — states the order simulator cannot produce at all

Not a method, but the same class of gap and arguably the largest:

- `_synth_fill` writes exactly one terminal state: `{"status": "COMPLETE", "filled_qty": qty}`.
- ⇒ **paper can never produce a REJECTED order** (margin shortfall, circuit limit, freeze
  quantity, RMS block, invalid tag — every real rejection cause).
- ⇒ **paper can never produce a PARTIAL fill.** `filled_qty` is always the full `qty`.
- The paper status vocabulary is `SUBMITTED → COMPLETE`, plus `CANCELLED`. Live's is far
  wider.

⭐ This one is worth stating plainly: **every order placed in paper fills, completely, always.**
Any code path conditioned on a rejection or a partial is unreachable in a paper session.

---

## What this means for the phrase "paper-proven"

"Paper-proven" is the literal gate on Slice 2.5 going live with real capital, so the list
above **is** the honest scope of that phrase. Stated precisely:

> A paper session proves the **happy path** and the **bookkeeping around it**. It cannot
> prove any behaviour whose trigger is a broker *refusal*, a broker *partial*, a *triggered*
> GTT, a *holding*, a nonzero *margin utilisation*, or a nonzero *clock skew* — because the
> adapter has no mechanism to produce any of those.

That is not a criticism of the adapter. A stub that always succeeds is the right default for
most of these; `get_live_margin_pct` raising is *documented, intended* behaviour with a
designed fallback. The defect would be believing paper covered something it structurally
cannot.

## The pattern this belongs to

Three instances were found independently this weekend, on three unrelated investigations:

1. **`product="MIS"` hardcoded in the paper book** (fixed `f7eedd3`) ⇒ paper could not
   exercise the CNC EOD exemption — *the stated gate for delivery going live*.
2. **`get_trades()` returns `[]`** ⇒ paper cannot reach CHECK1 rungs 1-2, so the
   contradiction rule — the *safety* property — can never fire there.
3. **`cancel_order` returns success unconditionally** ⇒ paper cannot exercise §D's deferral
   at all.

Three independent finds in one weekend is a class, not a coincidence. Named:

> ⛔ **PAPER CANNOT EXERCISE IT.** Before calling a mechanism "paper-proven", ask what INPUT
> triggers it and whether the paper adapter can produce that input. A paper branch that
> returns a constant cannot produce a *variable* input, so every behaviour keyed to that
> variation is untested no matter how many paper sessions run. **Paper must be able to
> rehearse what live relies on** — and where it cannot, that is a live-only path and must be
> labelled one.

⭐ The diagnostic that finds these cheaply: **a test that reaches past the public API to set
up state is naming a gap.** Gap #1 was found exactly that way.

---

## Recommendation

⛔ **Fix nothing on this pass.** Two observations for whoever takes the design conversation:

- The gaps split cleanly into **"legitimately a stub"** (`get_live_margin_pct`,
  `get_quote_raw`, `get_server_time` — no caller needs fidelity) and **"a stub standing where
  a gate depends on fidelity"** (gaps #1-#3 above). Only the second group is worth spending
  on.
- The cheapest fix for the second group is not a richer simulation — it is a **refusal seam**:
  a way for a test or a paper session to make one call fail on demand. That converts
  "structurally unreachable" into "reachable when asked" without changing any default
  behaviour, and it is one seam serving `cancel_order`, `modify_order`, `place_order` and the
  GTT trio alike.
