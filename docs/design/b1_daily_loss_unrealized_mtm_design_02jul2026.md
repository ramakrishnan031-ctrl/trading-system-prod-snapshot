# B-1 — Daily-loss unrealized-MTM dead control: investigation + permanent-fix DESIGN (02-Jul-2026)

**Type:** READ-ONLY investigate + design. No code changed. Audit finding **B-1 (HIGH)**.
**Parity rule:** one code path, paper + live. **Fix goal:** wire live per-position unrealized MTM into the daily-loss gate so it works as advertised (realized **+ unrealized**), not realized-only.

---

## STEP 1 — The risk, confirmed + quantified

`capital/risk_engine.py:497-512` (the pre-trade DAILY_LOSS gate, RE7/FIX-035):
```python
daily_pnl      = snap.daily_realized_pnl
unrealized_mtm = self._fm.get_total_unrealized_mtm()      # always 0.0 in prod
total_pnl      = daily_pnl + unrealized_mtm
if total_pnl < 0 and snap.total > 0 and abs(total_pnl) >= self._daily_loss_pct * snap.total:
    return reject("DAILY_LOSS", ...)
```
`get_total_unrealized_mtm()` sums `_unrealized_mtm` (`fund_manager.py:1435`), populated only by `update_unrealized_mtm` (`:1404`) / pruned by `remove_unrealized_mtm` (`:1416`). **Both writers have ZERO production callers** — grep shows them only in `tests/unit/test_fund_manager.py`. So `_unrealized_mtm` is permanently empty → `unrealized_mtm == 0.0` → **the gate is realized-only** despite advertising realized+unrealized.

**Quantified failure (real terms).** Daily-loss limit = 3% of capital. Suppose 3 open MIS positions, each sitting at −1.2% of capital unrealized, none closed yet:
- Reality: aggregate **open drawdown = −3.6%**, already past the 3% intent.
- Gate sees: `daily_pnl = 0` (nothing closed), `unrealized_mtm = 0` (dead) → `total_pnl = 0` → **does not reject** → keeps approving **new** entries, piling on more open risk.
- The gate only bites **after** positions close at a loss (realized). By then the drawdown may far exceed 3%.

**Bounding controls (why it's degraded, not absent):** each position has its own SL (caps single-position loss); the 15:17 MIS square-off caps duration; the realized arm of this gate **and** the separate post-close absolute daily-loss control (`fund_manager`, the dual mechanism) both trip once losses realize. So the gap = **excess concurrent open risk during the un-realized window**, not a total loss of control. Still a HIGH — an advertised safety control is non-functioning.

**Reject-only blast radius:** the gate returns `ApprovalResult(approved=False)` — it **denies the new entry**; it does **not** kill the switch or force-close open positions (those have SLs). So wiring unrealized only makes the gate **stop opening new risk earlier** during drawdown — exactly the advertised behavior, no new force-close path.

---

## STEP 2 — Cleanest live MTM source + hook point

**Source = `broker_adapter.get_quote([symbols]) → quote.last_price`.** This is the ONE parity-clean LTP source (see STEP 4). Compute per open trade:
```
unrealized_i = (ltp_i − avg_fill_price_i) × qty_filled_i × (+1 if LONG else −1)
```
(`avg_fill_price`/`qty_filled` from the reconciled open-trade row; broker-reconciled by CHECK4.)

**Why not the broker's own `pnl`:** Zerodha's `positions().net` rows carry `pnl`/`unrealised`/`last_price`, but the adapter's `Position` model **drops them** (`zerodha_adapter.py:1105-1112` keeps only symbol/qty/avg_price/product/side), and `_paper_positions` never had them → broker-pnl is **not parity-clean**. Computing from LTP is identical in both modes.

**Hook = the `order_reconciler` 15s cycle.** It already (a) fetches `get_positions()` every 15s, (b) enumerates OPEN/PARTIAL trades, (c) holds `quote_fn = get_quote`, (d) matches symbol↔trade. Add one step `_refresh_unrealized_mtm()` to `_reconcile()`: **batch** `get_quote` for all open-trade symbols (one call), compute each `unrealized_i`, `fm.update_unrealized_mtm(trade_id, unrealized_i)`, then **prune** any `trade_id` in the FM map that is no longer in the open set.

**Frequency = 15s (the reconciler cadence).** The gate is evaluated **pre-trade** (only when a new signal is sized) — 15s-stale portfolio MTM at signal time is more than adequate (per-position stops are the SLs, not this gate). Reuses the existing loop → **no new thread**, no extra broker-poll infrastructure. (Per-tick / 2s were considered and rejected: unnecessary freshness for a per-signal gate, higher quote-rate cost.)

*Design note (alternative considered):* computing unrealized **lazily at gate-eval time** (iterate open trades + get_quote when the gate runs) avoids the dict entirely, but adds a synchronous network quote to the signal hot-path and puts `quote_fn` into `risk_engine`. The reconciler-refresh keeps the gate **O(1)** (reads the pre-populated dict) and off the hot path — preferred.

---

## STEP 3 — Close/removal lifecycle + interactions

**Removal is SET-BASED, not per-close-path (the key robustness choice).** Each 15s refresh rebuilds the MTM map from the **current open-trade set**: present trades get `update_unrealized_mtm`; any FM-map `trade_id` absent from the open set gets `remove_unrealized_mtm`. So a trade closed by **any** path (SL / TGT / manual / EOD square-off / reconciler) drops out automatically on the next cycle — **no need to hook every close path**, and **no stale-MTM risk** from a missed removal (a lingering stale entry would wrongly inflate the loss and mis-trip the gate — the set-reconcile prevents it). (Optionally *also* `remove_unrealized_mtm` on the `PositionClosed` event for ≤15s-faster pruning, but the set-reconcile is the correctness backstop, not the event.)

**Interactions (verified — none change capital accounting):**
- **3-balance invariant / buckets:** `_unrealized_mtm` is a **separate dict** (`fund_manager.py:368`), never part of `available+reserved+used==total`. MTM is **advisory, read-only** for the gate — wiring it does **not** touch reservations, `_total`, or the invariant.
- **Daily-loss KILL:** unaffected — the pre-trade gate is reject-only; it never trips the kill-switch. The separate post-close **absolute** daily-loss control (`fund_manager`) stays realized-only by design (it acts after close).
- **EOD square-off / reconciler:** closed trades pruned next cycle; adopted/recovered trades (A-1/E-1) enter the open set → get MTM next cycle.
- **Behavior change (intended, bounded):** the gate will REJECT new entries earlier when `realized + unrealized ≤ −3%` — i.e., it stops adding risk during open drawdown. No force-close, no new kill.

---

## STEP 4 — Parity (paper + live), ONE code path

`quote_fn = broker_adapter.get_quote` (wired at `main.py:2303` for the reconciler):
- **LIVE:** `get_quote` → real Kite quotes → `.last_price`.
- **PAPER:** the paper adapter's quote provider is `_make_paper_quote_provider()` (`main.py:1669`), which fetches **REAL Kite quotes** from the token file (`main.py:377-482`, `last_price` at `:482`). So `get_quote(...).last_price` returns a **usable, real LTP in paper too**.

The compute `(ltp − avg) × qty × sign` and the refresh/prune are **mode-agnostic** — one code path, no `if paper` branch. **Confirmed: the paper simulation yields a usable unrealized MTM** (real LTP × paper fill qty/avg). No paper/live divergence. (Paper `avg_fill_price` comes from the simulated fill; live from the real fill — same field, same formula.)

---

## STEP 5 — Failure modes + fail-safe behavior

| Mode | Behavior | Design response |
|---|---|---|
| **Stale LTP** (feed lag) | `get_quote` returns last-known price | Accept last-known; **stamp each MTM refresh with a monotonic ts**. If the map is older than **2 cycles (~30s)**, mark unrealized **STALE**. |
| **Feed/API outage** (get_quote fails) | can't compute unrealized | **Fail-safe = degrade to realized-only + ALERT**, and mark the term UNAVAILABLE. The gate must NOT block all trading on a quote hiccup, but it must **log/alert that it ran realized-only** so the degradation is visible (not silent like today). (An outage usually coincides with the reconciler's auth-error → soft_kill path anyway.) The daily-loss gate reads a **freshness flag**: if UNAVAILABLE/STALE → use realized-only + WARN; else realized+unrealized. |
| **Process restart** (dict cleared) | `_unrealized_mtm` empty | The startup `reconcile_once()` + recovery prepass re-adopt open positions; the **first refresh (~15s)** repopulates from the open set. Gap bounded to ≤15s of realized-only, self-healing. |
| **Orphan / adopted position** (A-1/E-1) | recovered to OPEN | Enters the open-trade set → gets an MTM entry next refresh. A pre-adoption `UNKNOWN_IN_FLIGHT`/`PENDING` trade has no confirmed fill → correctly no MTM until adopted. |

**Fail-safe principle:** unavailable/stale MTM ⇒ the gate falls back to the **current** (realized-only) behavior **with an explicit WARN** — never worse than today, never silently. It never *fabricates* an unrealized term.

---

## STEP 6 — Permanent-fix design + rollout + tests

**The wiring (permanent):**
1. `order_reconciler._refresh_unrealized_mtm()` in the 15s `_reconcile()`: batch `get_quote(open_symbols)`; for each OPEN/PARTIAL trade compute `unrealized_i`; `fm.update_unrealized_mtm(trade_id, unrealized_i)`; **prune** FM-map trade_ids not in the open set; stamp `_mtm_refreshed_at` + an availability flag.
2. `fund_manager` exposes MTM freshness (e.g., `get_unrealized_mtm_status() → (total, is_fresh)`), or the reconciler sets a shared freshness flag.
3. `risk_engine` DAILY_LOSS gate: `total_pnl = realized + (unrealized if fresh else 0)`; when not fresh, WARN "daily-loss ran realized-only (MTM unavailable)". Gated by a config flag (rollout below).

**ROLLOUT — shadow first (it changes a capital gate):**
- **Phase S (shadow):** flag `daily_loss_include_unrealized = false`. The refresh **populates** the MTM map and the gate **logs** `would_reject_with_unrealized` (using realized+unrealized) but **enforces on realized-only**. Observe a few sessions: (a) MTM **correctness** — spot-compare computed unrealized to broker `pnl` (add a debug read of the dropped broker field) on live; (b) **how often** the unrealized term would have flipped the gate to reject (over-strict? correct?).
- **Phase E (enforce):** flip `daily_loss_include_unrealized = true` → the gate rejects on realized+unrealized. Reversible via the flag.

**TEST PLAN (fail-on-old / pass-on-new):**
1. Refresh **populates** `_unrealized_mtm` for each open position (computed from a mocked `get_quote` LTP) — asserts non-zero where old = 0.
2. **Set-reconcile prunes** a trade the moment it leaves the open set (closed via SL/TGT/manual/EOD) — no stale entry lingers.
3. Gate **sums** realized+unrealized and rejects at the 3% threshold that realized-only would have missed (the quantified scenario).
4. **Staleness/outage fallback:** `get_quote` raises → MTM marked unavailable → gate uses realized-only + WARN (never blocks-all, never fabricates).
5. **Restart:** empty dict → first refresh repopulates from re-adopted open trades.
6. **Parity:** paper `get_quote` (real Kite quote) yields a usable MTM; same code path asserts identical compute in paper + live.
7. **Shadow flag:** off → logs `would_reject` but enforces realized-only; on → enforces total. No invariant/bucket change (assert `_total`/reservations untouched by MTM writes).

---

## STEP 7 — Sequencing + open HIGH/CRIT confirmation

**Recommendation: build B-1 STANDALONE, BEFORE P1.**
- It is **smaller + self-contained** (one refresh step in the existing reconciler loop + the gate already sums the term + a shadow flag) and **independent** of P1 (B-1 is the pre-trade *risk gate*; P1 is *EOD broker reconcile*).
- It **closes a dead advertised HIGH capital control** — do not run the larger P1 build while this sits non-functioning.
- It is **additive to the reconciler cycle** that P1 later extends, so doing B-1 first is clean (P1 builds on a reconciler that already refreshes MTM). No dependency inversion.
- Shadow rollout is quick (populate + log, then flip a flag).
- *Not bundled with P1* because bundling would delay B-1 behind P1's larger scope and mix a risk-gate fix with an EOD-reconcile build.

**Open HIGH/CRITICAL set — confirmed unchanged:** the only open items are **{A-2 (built, unpushed), C-1 (residual unpushed + history-purge pending), B-1 (this — design only), C-2 (network layer)}**. A-1 is CLOSED/deployed (`9becf8c`). Lower-tier: W3 → P1, E-4 → P2, E-1 closed with A-1; C-3/C-4/C-5/A-3/E-2/F-1/D-1 + the `get_daily_realized` double-cost are MEDIUM/LOW hardening, none HIGH/CRITICAL. Nothing higher-risk than P1's target sits un-started except B-1 — which this design slots **before** P1.

---
**No code changed.** Awaiting review → implement B-1 standalone (shadow → enforce) before the P1 build.
