# SIZING INTERACTION + IMPACT REPORT (Q3) — GATE 2 artifact

**Date:** 13-Jul-2026 (IST, off-market) · **Author:** VS Code Claude
**Basis:** read-only recompute of **269 sized historical trades** (corpus 2026-06-15 → 2026-07-13)
on a copy of the live DB (`/tmp/q3/copy.db`, v42). Faithful to `capital/position_sizer.calculate()`.
**Status:** RECOMMEND ONLY — **nothing changed**. The concentration/exposure decision is Rama's (GATE 2).

---

## 0. TL;DR (the one thing that matters)

The book is **net-NEGATIVE** (112 closed trades, 39.3% win rate, **total P&L −Rs92**). Every sizing
lever *multiplies* that edge:

| conc_pct | median pos value | median ACTUAL risk/trade | scaled total P&L (illustrative) |
|---|---|---|---|
| 0.10 (today) | Rs442 | **Rs4.9** (intended Rs99) | −Rs90 (1.0×) |
| 0.25 | Rs1,091 | Rs11.5 | **−Rs184 (2.0×)** |
| 0.50 | Rs2,349 | Rs24.4 | **−Rs376 (4.1×)** |

**Raising concentration on a negative-expectancy book scales the LOSS, not the profit.**
→ **Recommendation: do NOT raise `conc_pct` as a size lever until the edge is fixed** (Q2 M-S4 +
the long-book quality problem below). The scaled-P&L caveat is real (past fills ≠ future fills), but
the *sign* is not going to flip by resizing.

---

## 1. What the data proved (the binding truth)

- **`binding_constraint = concentration` in 269 / 269 (100%).** `qty_by_risk` and `qty_by_capital`
  bound **zero** times. The risk-based sizer is dead code — exactly the algebra in the Sizing Policy
  doc (concentration binds whenever `sl_distance < (risk/conc)·price = 10%·price`; intraday stops are
  1–3%, so it always binds).
- **`tier_weight_applied = 0.5 (LOW) in 269 / 269 (100%).** `position_sizing.enabled = true`, so every
  trade was sized at **half**. The tier system is dead (M-S4 ceiling ~65 < MEDIUM 65) — confirmed empirically.
- **Actual risk/trade ≈ Rs5–7 vs the intended Rs99** (~1/13). The system is small **by accident**, not
  by design.

## 2. Stacking analysis (Q3a) — what binds as size scales up

Ordered walls as `conc_pct` rises (empirical, from the recompute):

1. **CONCENTRATION** — binds 100% at conc 0.10 and 0.25 (cap/pos Rs991 → Rs2,477).
2. **RISK** — begins binding at conc ~0.50 (53/269 ≈ 20% of trades flip to RISK-bound; the wider-stop
   trades, where `sl > 2%·price`). This is the intended risk sizer finally *starting* to govern.
3. **max_open_positions = 5** — the concurrency cap; peak concurrent is always 5 (below).
4. **POSITION_VALUE_CAP (40% = Rs3,963/pos)** and **RISK** dominate beyond conc ~0.5. The 40% cap does
   **not** bite today because the LOW-tier 0.5 halving keeps values down — **but if M-S4 lifts tier to
   1.0, values double and the 40% cap engages around conc 0.40.** (This is the multiplication trap.)

**Key:** conc is a "clean" lever up to ~0.25 (nothing else neutralises it — peak stays 16% of buying
power). At 0.50 it begins engaging RISK and the 40% cap starts to matter once tier lifts. Do not raise a
limiter another would immediately neutralise — but through 0.25 there is genuine headroom.

## 3. Before/After (Q3b)

Recompute (total_capital = Rs9,908 const; risk/capital arms from the stored per-trade breakdown; the
concurrency feedback is captured separately in Peak Exposure below — and validated harmless there):

| conc | cap/pos | admitted | med qty | med value | med risk | mean risk | binding |
|---|---|---|---|---|---|---|---|
| 0.10 | Rs991 | 266/269 | 2 | Rs442 | Rs4.9 | Rs5.9 | 100% CONC |
| 0.25 | Rs2,477 | 269/269 | 5 | Rs1,091 | Rs11.5 | Rs13.2 | 100% CONC |
| 0.50 | Rs4,954 | 269/269 | 10 | Rs2,349 | Rs24.4 | Rs28.7 | 80% CONC / 20% RISK |

**Peak concurrent exposure** (max_open_positions=5; buying power ~Rs34,678 notional / Rs6,935 margin):

| conc | peak positions | peak notional | peak margin |
|---|---|---|---|
| 0.10 | 5 | Rs3,420 (9.9% of BP) | Rs684 (9.9%) |
| 0.25 | 5 | Rs5,550 (16.0%) | Rs1,110 (16.0%) |
| 0.50 | 5 | Rs11,905 (34.3%) | Rs2,381 (34.3%) |

Even at conc 0.50 the peak margin is 34% of the intraday bucket → **capital is never scarce**, which is
*why* `qty_by_capital` never binds and why the per-trade recompute (which uses the stored capital arm)
is safe. Deployment on this axis is not the risk; the negative edge is.

## 4. The >Rs990 exclusion (Q3c) — a real, large, silent selection distortion

- **7,707 signals** rejected `REJECTED_SIZING_CONCENTRATION` (178 distinct symbols) — `floor(990/price)=0`
  for any price > ~Rs991.
- Price bands: **45% Rs990–1,500 · 37.8% Rs1,500–2,477 · 16.8% Rs2,477–4,954**.
- **23.3% of the ENTIRE signal universe** (32,197 of 138,087 priced signals) is > Rs990 → **silently
  untradeable today**, while the scanners screen Rs100–5,000.
- Recovery: **conc 0.25 makes 83.2% of the excluded band tradeable**; conc 0.50 makes 100%.

This is as much a **selection** problem as a size one: nearly a quarter of what the scanners surface can
never be traded, purely because of the concentration cap.

## 5. LONG underperformance (Q3d) — two causes tested

| direction | n | win rate | total P&L | median price |
|---|---|---|---|---|
| LONG | 124 | **37.1%** | **−Rs69.2** | Rs309 |
| SHORT | 10 | 60.0% | +Rs10.0 | Rs227 |

*(SHORT n=10 is too small to trust the 60%; LONG n=124 with a negative total is solid. This does not
reproduce the exact "13% vs 58%" figure — likely a different window/definition — but the direction holds:
LONG materially underperforms and is net-negative.)*

- **Cause (a) — >Rs990 restricts LONGs to cheap stocks: SUPPORTED.** >Rs990 rejections split **LONG 5,142
  vs SHORT 1,128** — the exclusion hits the long book **~4.5× harder**, and the traded LONG median price
  is Rs309 (cheap). The LONG book is constructionally skewed to sub-Rs990 names.
- **Cause (b) — M-S4 blind scorer picks bad longs: INCONCLUSIVE but consistent.** LONG winners and losers
  have the **identical median score (60/60)** — the scorer cannot separate them. That degeneracy is itself
  an M-S4 symptom (volume/ATR/RSI dead), but it can't be proven as *the* cause with degraded scores. Only
  a post-M-S4-fix recompute (Q2 §7) can test it honestly.

## 6. Recommendation (change nothing — Rama decides)

1. **Do NOT raise `conc_pct` as a size lever while book expectancy is negative.** Scaling multiplies the
   loss (2× at 0.25, 4× at 0.50). Fix the edge first.
2. **Treat the >Rs990 exclusion as a SELECTION problem**, addressed via Q2 (M-S4 fix — better ranking) and
   the long-book quality work — not primarily via a conc size increase (which couples size onto a negative
   book).
3. **Sequence:** Q2 M-S4 fix → re-measure expectancy on the fixed scorer (re-soak) → *then* revisit conc
   with a positive-expectancy baseline and the post-M-S4 tier as the new size baseline.
4. **If Rama wants a bounded sizing step now** despite (1): **OPT-B (conc 0.10 → 0.25)** is the only
   defensible one — it recovers 83% of the excluded band and un-restricts the long book, peak stays 16% of
   buying power, risk stays conservative. **Reject OPT-C (0.50) now** — 4× a negative book, engages RISK,
   and collides with the M-S4 tier lift.
5. **NEVER couple with M-S4** — `conc 0.25 × M-S4 tier(0.5→1.0) = 2.5× × 2× = 5×`. One change, one impact
   proof, one soak, one observed session between them. That is physics, not process.

---
*Read-only. No config/code/sizing changed. Script: `scratchpad/q3_sizing_analysis.py` → `/tmp/q3/analysis.py`.*
