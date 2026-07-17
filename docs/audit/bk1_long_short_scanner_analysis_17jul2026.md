# BK-1 — LONG-vs-SHORT + per-scanner performance (D2 data package)

**Date (IST):** 17-Jul-2026 · **Mode:** READ-ONLY (sqlite3 `-readonly` on the live DB; nothing
changed, nothing pushed) · **Base:** `7b3ecad`

**Book definition:** `trades` where `status IN ('CLOSED','CLOSED_MANUAL') AND qty_filled>0` =
**155 trades**, window **2026-06-15 → 2026-07-16 (23 trading days, ~1 month)**. R = `risk_amount`
(avg ₹6.1, range ₹2–18, no nulls/zeros). Expectancy in R = mean of per-trade `pnl/risk_amount`.
*(The earlier "134 closed trades" used a different/narrower cut; this report states its own clean
definition. `analytics.db` has no trades table — the book is in `trading_system.db`.)*

> **⚠️ COST DATA CAVEAT (load-bearing).** `charges` is the authoritative total cost and
> `net_pnl = gross_pnl − charges` for all 119 `CLOSED` trades. But **all 36 `CLOSED_MANUAL`
> trades have `charges = 0`** (the RMS/manual-close `costs=0.0` path, [[capital-operational-note]]),
> so their recorded "net" is really **gross**. Every NET figure below is therefore *optimistic* by
> roughly one trade's cost on ~23% of the book; a cost-imputed variant is shown where it matters.

> **⚠️ SAMPLE-SIZE LEGEND** used throughout: **n≥30 usable · 10–29 thin · <10 unreliable · <5
> anecdotal.** Only **3** scanners clear n≥24; **7 of 11** are under n=10. Treat every per-scanner
> and every SHORT number as directional, not conclusive.

---

## PLAIN-LANGUAGE SUMMARY (for Rama)

**1. Do LONGs underperform SHORTs, net?** In this data, yes — but the honest framing is different
from the question. LONGs (n=141, reliable) are **net −0.15R**; SHORTs (n=14, **too small to
trust**) are net **+0.30R**. The *reliable* finding is not "shorts beat longs" — it is that **the
loss is concentrated in INTRADAY longs (−0.24R, n=108)**, while **positional longs were net
positive (+0.14R, n=33)** and the 14 shorts happened to win. Without the 14 shorts the book is
−103R instead of −75R, i.e. **the shorts are propping the book up, not dragging it down.**

**2. Is the short tilt deliberate or a scanner artifact?** **Neither — the premise is inverted.**
The executed book is **91% LONG (141/155), 9% short.** No population in the DB is "58% short"
(all-status 20% short; FAILED orders 32% short; closed book 9% short). The LONG dominance is a
**scanner-mix artifact**: long scanners generate ~4× more signals (289 vs 72 trade-rows), and that
tilt is then *amplified* because **short orders fill at only 19% vs 49% for longs.**

**3. Ranked keep/kill DATA (not a verdict).** Worst drain by far: **`vwap_bounce_long`
(−12.1R, n=35)** — the one loser with a large, trustworthy sample. Then `gap_fade_long`,
`gap_go_long`, `open_low_breakout_long`. Best: `positional_sector_rotation (+4.46R, n=19)`.
**Key nuance: the big losers are negative on GROSS too — they are negative-*edge*, not
cost-killed. Cost tuning cannot save them.** (Full ranked table in §C.)

**4. What the data CANNOT answer.** (a) **The regime confound is unresolved** — the DB has **no
NIFTY/index candles** (token 256265 = 0 rows), so I cannot confirm whether July's long losses were
a market downturn or bad entries. Evidence is *mixed* (see §D). (b) SHORTs (n=14) and 7 of 11
scanners (n<10) are **too small for any per-cell verdict**. (c) One regime, 23 days, no forward
confirmation.

---

## A. DIRECTION SPLIT (n=155)

| Direction | n | % book | WR (net) | Gross exp R | **Net exp R** | Cost R | Avg win R | Avg loss R | Tot net ₹ | MFE% | MAE% |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **LONG** | 141 | 91% | 36.9% | −0.075 | **−0.150** | 0.076 | +1.33 | −1.02 | −103.3 | +1.14 | −0.96 |
| **SHORT** | 14 ⚠️ | 9% | 64.3% | +0.345 | **+0.298** | 0.047 | +0.86 | −0.95 | +28.5 | +1.03 | −0.55 |

Cost-imputed net (add the CLOSED-book avg cost ₹0.51 to each manual close): **LONG −0.170R ·
SHORT +0.247R** — the gap survives the cost caveat.

**Cleaner cut than long/short — the loss is an INTRADAY-LONG problem:**

| Bucket | Dir | n | Net exp R | Tot net R |
|---|---|---|---|---|
| **INTRADAY** | LONG | 108 | **−0.239** | **−25.6** |
| POSITIONAL | LONG | 33 | +0.136 | +4.5 |
| INTRADAY | SHORT | 14 ⚠️ | +0.298 | +3.9 |

Reading: LONGs win as big as they lose (+1.33R vs −1.02R) — the problem is **frequency, not
payoff**: only 44 target-hits vs **69 stop-hits** (49% of longs stopped out). SHORTs' edge (n=14)
comes from a 64% hit rate and a smaller adverse excursion (MAE −0.55% vs longs' −0.96%), but the
sample is far too small to lean on.

---

## B / C. PER-SCANNER BREAKDOWN + KEEP/KILL RANKING (D2 input — small-sample, one regime)

Ranked by **total net-R contribution** (net exp R × frequency — the risk-normalised P&L impact).
Rupee P&L shown too; they diverge because scanners use different position sizes.

| # | Scanner (strategy) | Dir | n | WR | Gross exp R | Net exp R | Cost R | **Tot net R** | Tot net ₹ | Flag |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | positional_sector_rotation | LONG | 19 (thin) | 42% | +0.277 | +0.235 | 0.042 | **+4.46** | +13.6 | best contributor |
| 2 | first_pullback_short | SHORT | 5 ⚠️⚠️ | 80% | +0.773 | +0.746 | 0.028 | **+3.73** | +26.8 | anecdotal |
| 3 | positional_momentum_long | LONG | 4 ⚠️⚠️ | 50% | +0.452 | +0.425 | 0.026 | **+1.70** | +24.4 | anecdotal |
| 4 | first_pullback_long | LONG | 24 (thin) | 42% | +0.100 | +0.045 | 0.055 | **+1.03** | +7.5 | **cost knife-edge** |
| 5 | vwap_rejection_short | SHORT | 3 ⚠️⚠️ | 67% | +0.270 | +0.183 | 0.088 | **+0.55** | +1.4 | anecdotal |
| 6 | gap_fade_short | SHORT | 6 ⚠️ | 50% | −0.038 | −0.080 | 0.042 | **−0.40** | +0.3 | unreliable |
| 7 | positional_swing_long | LONG | 10 (thin) | 30% | −0.134 | −0.166 | 0.032 | **−1.66** | −27.6 | neg edge |
| 8 | open_low_breakout_long | LONG | 28 (thin) | 36% | −0.063 | −0.150 | 0.086 | **−4.19** | −17.4 | neg edge, high cost |
| 9 | gap_go_long | LONG | 13 (thin) | 31% | −0.303 | −0.391 | 0.088 | **−5.09** | −37.3 | neg edge |
| 10 | gap_fade_long | LONG | 8 ⚠️ | 25% | −0.574 | −0.653 | 0.078 | **−5.22** | −20.5 | worst per-trade |
| 11 | **vwap_bounce_long** | LONG | **35** ✓ | 37% | −0.233 | −0.346 | 0.112 | **−12.10** | −46.2 | **biggest drain, only reliable loser** |

**Cost-killed check (question C):** *none* of the losers is "gross-positive but net-negative." Every
net-negative scanner is **already negative on gross** — their entry edge is the problem, and cost
merely deepens it. The **only** cost-marginal case is **`first_pullback_long`** (gross +0.10 →
net +0.045; cost 0.055 nearly eats a real but thin gross edge). `vwap_bounce_long` also carries the
**highest cost/R (0.112)**, but even at zero cost it would be −0.233R, so cost is not what kills it.

**What the sample can actually support:**
- **`vwap_bounce_long`** — the one loser with n≥30. −0.346R net, −0.233R **gross**, biggest drain
  (−12R / −₹46). The clearest data-driven KILL/redesign candidate.
- **`open_low_breakout_long`** (n=28) and **`gap_go_long`** (n=13) — negative on gross, consistent
  losers; KILL/redesign candidates, moderate confidence.
- **`first_pullback_long`** (n=24) — ~breakeven, gross-positive but cost-marginal; the one to
  WATCH/tune rather than kill.
- **Everyone else (n≤19), and every SHORT** — too small to rank as fact. `positional_sector_rotation`
  looks like the best long, but n=19 in one regime is suggestive, not proven.

---

## D. LONG-SPECIFIC: why do longs underperform? (a) scanners, (b) cost, or (c) regime?

The DB lets me *partially* separate these:

- **(a) Scanner-specific — YES, this is real and the strongest signal.** Long performance is
  *dispersed*, not uniformly bad: `positional_sector_rotation` (+0.235R) and `positional_momentum_long`
  (+0.425R) were positive in the **same window** that `vwap_bounce_long` (−0.346R) bled. Winners and
  losers **overlap in time** (both ran 06-17 → mid-July). If longs failed purely for market reasons,
  they would fail together — they did not. **The bleed is specifically INTRADAY longs (−0.24R, n=108)
  vs positional longs (+0.14R, n=33).**

- **(b) Cost asymmetry — real but small.** LONG cost 0.076R vs SHORT 0.047R per trade. Longs do cost
  ~0.03R/trade more (longer holds / lower price bands / the winning shorts had larger-R moves diluting
  their cost ratio). Modest; it is not the driver.

- **(c) Regime — SUGGESTIVE but UNCONFIRMABLE from this DB.** Weekly long net-R was **+4.8 (W24),
  −6.4 (W25), +3.8 (W26), −13.8 (W27), −9.5 (W28)** — choppy, with the worst damage concentrated in
  **early-mid July (W27–W28)**. That *pattern* is consistent with a July headwind for longs. **But I
  cannot confirm the market actually fell** — the DB holds **no index/NIFTY candles** (token 256265 =
  0 rows in `analytics.db`). And the time-overlap of winning and losing long scanners argues the
  regime cannot be the *whole* story.

**Honest bottom line on D:** the underperformance is **at least partly scanner-quality (a)** — proven
by same-window dispersion — with a **plausible but unmeasured July regime headwind (c)** on top, and a
**minor cost tilt (b)**. This DB **cannot cleanly quantify the regime split**; that needs index data
the system does not currently store.

---

## Structural note carried from the data (for D2)

- **Short orders fill at 19% vs 49% for longs** (56 of 72 short signals never became positions). The
  *reason* is not in the `trades` table (no `rejection_reason` column here — it would live in
  order-level logs). If the strategy intent is to run shorts, this fill gap is itself a problem worth a
  separate look; if shorts are incidental, it explains why the book is structurally long.
- **The book is 91% long BEFORE any judgment about whether that is desirable.** Any "we should be more
  balanced" conclusion is a *strategy* choice for D2, not something the current data argues for — the
  14 shorts we did take were (weakly) the best-performing slice.

---

## Explicit limits (do not over-read)

One market regime · 23 trading days · 155 trades · most per-scanner cells n<10 · all SHORT stats n=14
· NET understated on 36 manual closes (costs=0) · **no index data to test the regime confound** · no
forward/out-of-sample confirmation. This is **"what the current data suggests," a D2 input — not
proof, and not a keep/kill verdict.**
