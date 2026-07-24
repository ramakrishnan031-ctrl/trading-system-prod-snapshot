# SPANDANA net −1 (24-Jul-2026) — forensics

**Verdict (first lines):**
- **The −1 is arithmetically CORRECT.** The broker's own tally is `buy_qty=0, sell_qty=1` → net −1. One share short-sold, never bought back.
- **It is EXPECTED behaviour, NOT a defect and NOT an oversell.** It is a normal, still-open intraday **SHORT** from the `first_pullback_short` strategy, with live SL/TGT protection resting at the broker.
- **The 15:15/15:17 EOD squareoff will BUY 1 to cover → flat (0). It will NOT take the position to −2.** (A3.3 — the time-critical answer.)
- The instruction's framing premise ("the strategy set is long-only") is **incorrect**: the book runs intraday short strategies (`first_pullback_short`, `gap_go_short`, `gap_fade_short`, `vwap_rejection_short`). A net short is a designed outcome, not an anomaly.

**Mode:** READ-ONLY. Broker via `kite.orders()`/`kite.positions()` GETs (no place/cancel/modify); DB `mode=ro&immutable=1`. No orders touched.

---

## A1 — The order chain (broker is the ultimate truth; DB agrees)

**Broker (`kite.orders()` / `kite.positions()`, ~12:40 IST):**
| # | time | side | type/prod | qty | filled | status | px/trig | order_id | tag |
|---|---|---|---|---|---|---|---|---|---|
| ENTRY | 10:07:14 | SELL | LIMIT/MIS | 1 | 1 | COMPLETE | avg 269.90 | …241 | trd_5f31e1bd521d |
| SL | 10:07:15 | BUY | SL/MIS | 1 | 0 | TRIGGER PENDING | trig 273.60 | …268 | trd_5f31e1bd521d |
| TGT | 10:07:15 | BUY | LIMIT/MIS | 1 | 0 | OPEN | — | …273 | trd_5f31e1bd521d |

**Broker position:** `net qty −1, buy_qty 0, sell_qty 1, avg 269.90, ltp 267.75, pnl +2.15, product MIS`.

**DB (`trades`/`orders`) — consistent:** one trade `trd_5f31e1bd521d…`, `direction=SHORT`, `strategy=first_pullback_short`, `status=OPEN`, `qty_filled=1`, `order_protocol=LIMIT_TRIPLE`, entry_actual 269.90, sl_initial 273.61, tgt_initial 263.50. ENTRY order COMPLETE; SL + TGT `OPEN` (unfilled).

**A1.3 arithmetic:** BOUGHT(filled) = 0, SOLD(filled) = 1 → **NET = −1**. The broker's `buy_qty=0/sell_qty=1` independently confirms it. An oversell requires `buy_qty ≥ 1` with `sell_qty = buy_qty + 1`; that is not what happened.

**A1.4:** entry was a **SELL to OPEN a short**. `first_pullback_short` is a short strategy — the book is **not** long-only, so a short-capable path plainly exists. This changes the whole reading: the −1 is the intended position, not a leak.

## A2 — OCO / double-fill ruled out

- **A2.1:** Only one leg filled — the ENTRY (SELL). SL (TRIGGER PENDING) and TGT (OPEN) are both **resting, unfilled**. No exit leg has fired, so there is no double-exit.
- **A2.2:** `_cancel_oco_siblings` is not in play — no sibling filled, so no cancellation was due. (Nothing to prove-ran here; the trigger condition never occurred.)
- **A2.3:** **No flatten/emergency path executed on SPANDANA.** The broker order book contains exactly three orders (entry + the two resting legs) — no 4th/cover/reverse order. `reconciliation_log` has 0 SPANDANA rows today; no HARD_KILL/drift/orphan action fired.
- **A2.4 (control):** GODIGIT, DBL, SURYODAY all closed to 0 today. Notably **GODIGIT was also a SHORT (`gap_go_short`) and covered cleanly to 0** — the short-cover path works on the same day/code/session. SPANDANA differs only in that its SL/TGT have not yet been hit; it is simply still working.

## A3 — Does the system know, and what happens at 15:15?

- **A3.1 (divergence):** None material. System DB holds `SHORT / OPEN / qty 1`; broker holds `−1`. They agree on the position and on all three orders. *Minor label nuance:* the resting SL shows `OPEN` in the DB vs `TRIGGER PENDING` at the broker — the same armed-resting state (the OSM permits both forms for a resting SL); the SL is live and protecting the short. Not a defect.
- **A3.2 (reconciliation):** `reconciliation_log` = 0 SPANDANA rows today. Correct — nothing to flag.
- **A3.3 (THE question) — the EOD squareoff BUYS to cover; it will not worsen the short.** `orders/eod_squareoff.py::_exit_open_positions`:
  - `exit_side = "SELL" if direction == "LONG" else "BUY"` (`:1155`) → **BUY** for this SHORT.
  - qty from broker truth: `abs(int(p.qty))` (`:1071`) → 1; the E.5 broker filter (`:1090`) keeps SPANDANA (qty ≠ 0).
  - `exit_protocol = LIMIT_THEN_MARKET` (`system_config.yaml:239`): Pass-1 cancels the resting SL/TGT; then an aggressive **LIMIT BUY** (LTP·(1+1%), `:1262-1264`); after 120 s grace, promote unfilled to **MARKET BUY**; a residual broker-sweep backstop derives side from the qty sign (`_place_marketable_limit_exit:1509` → `qty<0 → BUY`).
  - **Every EOD exit sub-path buys to cover a short.** Reverse-awareness here comes from the trade's `direction` field (correctly `SHORT`, matching the broker −1), independent of the FIX-190 `determine_close_direction` sign-based helper used by the HARD_KILL/emergency paths. For a correctly-labelled short (this case) both agree: BUY to cover.
- **A3.4 (risk + backstops):** money at risk ≈ **1 share, ~₹268 notional**, and the position is in **profit (+₹2.15)**. Three independent closes protect it: (1) the resting **TGT BUY @ 263.50** may cover it for profit before EOD as SPANDANA falls; (2) the resting **SL BUY @ 273.60** caps the loss; (3) failing those, the **EOD squareoff BUYs to cover at 15:17**; (4) **Zerodha's own MIS auto-squareoff (~15:20)** covers any residual regardless. No panic warranted.

## Classification

**EXPECTED — a normal open intraday short, correctly recorded and protected. No defect, no oversell.** The only correction is to the premise: this book is not long-only. No fix arises from this investigation. (A mechanism that could take −1 → −2 would be a real defect regardless of size — but the EOD path is not that mechanism; verified above.)
