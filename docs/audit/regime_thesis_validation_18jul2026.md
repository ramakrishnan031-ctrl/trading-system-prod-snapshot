# Q10 — Regime thesis validation (18-Jul-2026)

**Status: BLOCKED on §2 (no valid Kite token) — the backfill was NOT run.**
**But the headline verdict is already determinable without it, and it is NOT the answer we
were hoping for.**

Nothing was changed: no backfill, no code, no flag, no schema. All DB access was `mode=ro`.

---

## PLAIN-LANGUAGE ANSWER FOR RAMA

**(0) FIRST — I need you to refresh the Kite token.** It is Saturday, the system is down, and
the 05:00 cron deleted the token as designed; the 08:15 TOTP refresh does not run on a
non-trading day. There is no valid token on the VM (the PC copy is from 21-Jun and long dead),
so `kite.historical_data` cannot be called and the index backfill cannot run. I did **not**
attempt any workaround — no manual TOTP run, no credential handling. That is yours.

**(1) Does the NIFTY 09:15–09:59 move predict the day's outcome? → NOT DETERMINABLE, and the
backfill will not change that.** This is the important finding. I expected the blocker to be
the missing index data; it isn't. **The sample ceiling is set by the TRADE side, not the index
side.** The controlled question needs trading *days* per bucket, and we have 23 days total:

| Control cell | trades | **trading days** | days per bucket (3 buckets) |
|---|---|---|---|
| LONG / INTRADAY | 108 | **21** | **~7** |
| LONG / POSITIONAL | 33 | **15** | **~5** |
| SHORT / INTRADAY | 14 | **11** | **~3.7** — anecdotal |

Every cell lands in single digits — below the "unreliable" threshold (n<10) before the analysis
even starts, and the SHORT cell is anecdotal. **Backfilling the index gives me the x-axis for
23 points; it cannot manufacture more trading days.** So I can state the verdict now and be
confident it will survive the backfill: **not determinable at this sample size.**

**(2) Would it survive the strategy/direction controls? → Cannot be tested yet — and this is
exactly where the beta-vs-edge distinction lives.** The book is 91% long (141 of 155) and the
bleed is concentrated in intraday longs (108 trades, −25.57R, 36.1% win). With a 91%-long book,
*any* "Bull mornings did better" result is the null hypothesis — it is market beta, not a
ranking edge. The only test that separates them is within-cell (do Bull mornings beat Bear
mornings for the *same* strategy and direction?), and that is precisely the test with ~7/~5/~4
days per bucket. **The control that makes the question meaningful is the control that makes it
statistically empty at this sample size.**

**(3) What % bands does the real distribution suggest? → Genuinely pending the backfill.** This
is the one deliverable that truly requires the index data. I have exactly **one** observed
morning move (16-Jul: **−0.058%** vs the 09:15 open). I will not invent terciles from n=1.
Once backfilled, the 23-day distribution gives the tercile boundaries directly — that is the
Phase 3 calibration input, and it *is* worth having even though it will not settle the thesis.

**(4) How much more data is needed?** Grounded in the observed variance of daily P&L for the
only cell with depth (LONG/INTRADAY: 21 days, mean **−5.42**/day, **σ = 13.54**), using
`n_per_group = 2σ²(z₀.₉₇₅+z₀.₈)²/Δ²` (80% power, α=0.05, two-sided):

| Effect size to detect | days **per bucket** | total trading days (3 buckets) | ≈ calendar |
|---|---|---|---|
| **Large** (1.0 σ ≈ ₹13.5/day) | 16 | **47** | **~2.2 months** |
| **Moderate** (0.5 σ) | 63 | 188 | ~9 months |
| **Small** (0.25 σ) | 251 | 753 | ~36 months |

**Read this honestly:** only a *large* effect is detectable on a realistic horizon — about
**2.2 months** of live sessions (we have ~1 month, and only 21 usable days in the main cell).
A moderate effect needs ~9 months. If the regime edge is subtle, **this book cannot prove it in
any useful timeframe**, and the feature would be resting on faith rather than evidence. That is
a decision for you, not a conclusion I can compute away.

---

## §2 — Precondition: token check (the STOP)

| Check | Result |
|---|---|
| VM `data_store/session/zerodha_token.json` | **absent** |
| VM session dir mtime | `Jul 18 05:00` — the documented 05:00 delete cron |
| VM `cron_marks/token_cleanup.done` | `Jul 18 05:00` (cleanup ran) |
| 08:15 TOTP refresh today | **did not run** (Saturday = non-trading day; latest preflight marks are 17-Jul) |
| PC `data_store/session/zerodha_token.json` | exists but dated **21-Jun 12:47** — ~4 weeks stale (Kite tokens expire the next morning) |

⇒ **No valid access token exists.** `kite.historical_data` would fail, so
`scripts/fetch_daily_candles.py --backfill` cannot run. **STOPPED per §2.** I deliberately did
**not** run `scripts/auto_refresh_token.py` myself: it handles live credentials and the TOTP
seed, and §2 reserves the refresh for Rama.

---

## §3 — Backfill: NOT RUN (blocked). Prepared so it is one command.

**The exact 23 trading days** needing index candles (derived from the closed book, so this is
the authoritative verification list):

```
2026-06-15 16 17 18 19 · 06-22 23 24 25 · 06-29 30
2026-07-01 02 03 · 07-06 07 08 09 10 · 07-13 14 15 16
```
(Note 26-Jun is absent from the book — no trades that day. 17-Jul had 0 trades: the S4 outage.)

**Current index coverage:** NIFTY 256265 has **1 day only — 16-Jul (375 rows)**; the other 9
index tokens likewise. So **22 of 23 days are missing.**

**The command, when the token is live** (proven path, data-only, idempotent
`INSERT OR IGNORE`, `_fetch_indices` runs first and fail-safe):
```
PYTHONPATH=. python3 scripts/fetch_daily_candles.py --backfill --from 2026-06-15 --to 2026-07-16
```
**Before running:** fresh `analytics.db` backup (per §1). **After:** verify NIFTY rows landed
for all 23 days, 0 stock rows changed, `integrity_check=ok`.

---

## §4 — Analysis: pipeline PROVEN on real data, awaiting the other 22 days

**The morning-move computation is not hypothetical — it works today on the one stored day:**

```
2026-07-16 · NIFTY 256265 · 375 one-minute candles (09:15:00 → 15:29:00)
window 09:15–09:59  →  45 bars (09:15:00 … 09:59:00)
open(09:15) = 24142.10   close(09:59) = 24128.10
MORNING MOVE = -0.058%   (window range 24097.05 – 24167.40)
```
So step (a) is a solved problem; only the data breadth is missing. Steps (b) terciles and (c)
cross-tabs are then mechanical.

**The outcome side is fully computed already** (needs no index data) — and it independently
reproduces BK-1:

| DIRECTION | BOOK | n | net P&L | win% | sum R | reliability |
|---|---|---:|---:|---:|---:|---|
| LONG | INTRADAY | 108 | −113.8 | 36.1% | **−25.57** | ok (but 21 days) |
| LONG | POSITIONAL | 33 | +10.4 | 39.4% | +4.50 | thin |
| SHORT | INTRADAY | 14 | +28.6 | 64.3% | +3.87 | **anecdotal (n<15, 11 days)** |
| **TOTAL** | | **155** | **−74.8** | | | |

The shorts again look best and again are too few to trust — same caution as BK-1.

---

## Recommendation

1. **Refresh the token, then run the backfill anyway.** It is cheap, data-only, and proven. It
   is the Phase 3 calibration asset, it yields the real tercile bands, and it **starts the
   clock** — every future session then accumulates a usable observation. Do it *because* the
   answer needs months of data, not instead of it.
2. **Do not build Phase 1 on the expectation that this test will bless it.** The honest
   position: the thesis is **unvalidated and not validatable on this book for ~2+ months**, and
   only if the effect is large.
3. **If you want the feature anyway**, build it explicitly as a *hypothesis under measurement* —
   shadow-only, logged daily, with the verdict deferred — rather than as a validated edge. That
   is a legitimate choice; it just needs to be a conscious one.
4. **⚠️ Standing warning (unchanged):** do **NOT** flip `regime.enabled` while the V3/F1 shadow
   soak is accumulating — the V3 chain consumes regime via `gate_extreme` + `regime_fraction`
   (8/40 Context), so flipping mid-soak changes the score distribution and makes pre/post rows
   non-comparable.

---

## Recorded for the future Phase 1 design (ChatGPT's recommendation — NOT built)

The opening-regime calculation should be a **configurable plug-in inside the existing `regime/`
module** (never a parallel module): a **configurable opening window** (default 09:15–09:59),
**tunable thresholds**, **pluggable additional regime algorithms**, and **versioned regime
logic** so future variants can be compared against each other on the same logged history. The
versioning matters most given the finding above — if the verdict needs months of data, the
logged rows must record *which* algorithm version produced them.

---

## What was NOT done

No backfill (token). No Phase 1 code, no new axis, no window config, no weighting. No flag
flipped (`regime.enabled` and `v3_chain_mode` untouched). No schema change. No code pushed.
No token refresh attempted.
