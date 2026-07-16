# E4 — the daily-loss limit + available capital are fed the wrong P&L · INVESTIGATION (READ-ONLY)

**Date (IST):** 2026-07-17. **READ-ONLY** — nothing fixed, nothing changed, nothing pushed.
DB reads via `sqlite3 mode=ro` only. This is the investigate-first step of the careful loop.

---

## ⭐ ONE-LINE STEER

> **Do NOT "just pass the real costs" — that alone makes it WORSE.** The normal exit path
> **already passes real costs** (`order_placer.py:2410 costs=charges`), so E4 is scoped to
> **3 backstop paths**. But the reader **double-subtracts** costs (the known, still-OPEN
> **W10**): `pnl_delta` is *already* net, and `get_daily_realized_net_pnl` subtracts `costs`
> again — so the `costs=0.0` rows are the only ones the reader happens to treat correctly.
> **Passing real costs into those 3 paths without fixing W10 converts an understatement into
> a double-count on every close.** E4 and W10 are **one bug — a `pnl_delta` contract mismatch
> — and must be fixed together, as one change.**

**Normal exits already correct?** **YES for the cost input** (`costs=charges`) — and available
capital is consequently correct there. **NO for the loss limit**, which double-subtracts on
exactly those rows. **Cost source?** It already exists and is mode-agnostic:
`broker/cost_calculator.py` → `CostCalculator.round_trip_breakdown(...).total`, built at
`main.py:1786` **before** the paper/live branch. The reconciler/GTT monitor simply never had
it injected — which is *why* they pass `0.0`.

---

## Q1 — The mechanism, end to end

**Call site** (`orders/order_reconciler.py:1102-1110`, CHECK1):
```python
release_result = self._fm.release_used(
    symbol=symbol, exit_price=float(exit_price), exit_qty=qty, intent=intent,
    entry_price=float(entry_price), direction=direction,
    costs=0.0,                      # ← :1109
    trade_id=trade_id,
)
```
The file already **admits** it (`order_reconciler.py:1118-1120`):
> *"RMS/manual closes pass costs=0.0 above, so gross==net and charges=0.0."*

**Inside `capital/fund_manager.py:release_used` (:1161):**
```python
:1225   gross_pnl = (exit_price - entry_price) * exit_qty     # LONG (SHORT mirrored, :1227)
:1228   pnl = gross_pnl - costs                               # costs=0.0 ⇒ pnl IS GROSS
:1230   avail_before = self._bucket_avail(bucket)
:1231   projected_after = avail_before + margin + pnl         # (b) available capital
:1251       pnl_delta=pnl,                                    # (a) → fm_ledger.pnl_delta
:1252       costs=costs,                                      #     costs stored ALONGSIDE
:1257   self._bucket_add_avail(bucket, margin + pnl)          # in-memory avail
:1259   self._total += pnl                                    # total capital
```

**The reader** (`core/state_store.py:2432-2451`), which the loss limit uses:
```sql
SELECT COALESCE(SUM(pnl_delta) - SUM(COALESCE(costs, 0.0)), 0.0) AS net_pnl
FROM fm_ledger WHERE date = ?
```
Its docstring (`:2434-2437`) asserts *"Sums fm_ledger.pnl_delta (**gross** PnL from trade
closes) and subtracts fm_ledger.costs"*. **That premise is false**: `:1228` writes `gross −
costs` into `pnl_delta`. **Writer and reader disagree — this is the root cause.**

---

## Q2 — Which close paths pass `costs=0.0`? ⭐ (normal-exit correctness settled)

**Every production caller of `release_used` — all 4:**

| # | Caller | Path | `costs` passed | Verdict |
|---|---|---|---|---|
| 1 | `orders/order_placer.py:2403` (`:2410`) | **normal exit** (fill-driven) | **`costs=charges`** | ✅ **REAL COSTS** |
| 2 | `orders/order_reconciler.py:1102` (`:1109`) | **CHECK1** backstop | `costs=0.0` | ❌ gross |
| 3 | `orders/order_reconciler.py:1894` (`:1901`) | **partial close** (M-O2) | `costs=0.0` | ❌ gross |
| 4 | `orders/cnc_gtt_monitor.py:447` (`:450`) | **CNC/GTT** close | `costs=0.0` | ❌ gross |

**⇒ Gross is NOT fed everywhere. The normal exit path is already correct.** E4 is scoped to
**3 backstop paths**. EOD squareoff / kill-switch flatten / RMS / manual all land on the
broker-fill path and therefore reach `release_used` via **one of these four** — the reconciler
ones are the backstops that fire when a close is detected rather than driven by our own fill.

**Live proof of the split** (`fm_ledger`, `mode=ro`):

| Path | rows | Σ pnl_delta | Σ costs |
|---|---|---|---|
| `costs != 0` (normal exit) | **119** | −71.77 | **60.24** |
| `costs = 0` (CHECK1/RMS/GTT) | **36** | −24.68 | **0.00** |

**Root cause of the `0.0`, stated plainly: neither `order_reconciler` nor `cnc_gtt_monitor`
holds a `CostCalculator` at all** (grep: zero hits in both). It is missing *wiring*, not
broken arithmetic.

---

## Q3 — Where are the real costs? (an existing source — do NOT compute a new one)

**`broker/cost_calculator.py`** — `CC1: brokerage + STT + exchange_txn + GST + SEBI + stamp_duty`.

The normal path already uses it (`orders/order_placer.py:2297-2307`):
```python
cost_breakdown = self._cost_calculator.round_trip_breakdown(
    qty=exit_qty, entry_price=entry_price, exit_price=exit_price, product=product)
charges = cost_breakdown.total          # :2307   → passed as costs= at :2410
```
On exception it degrades to `charges = 0.0` (`:2314`) — i.e. **the same silent-gross failure
mode exists on the good path too**, and a fix should decide whether that should stay silent.

**Best existing source:** `CostCalculator.round_trip_breakdown(qty, entry_price, exit_price,
product).total` (`broker/cost_calculator.py:243+`). **Availability at the reconciler close
point:** it has `symbol, exit_price, qty, intent, entry_price, direction, trade_id` — it needs
only `product` (derivable from `intent` via the existing `ProductResolver`, `main.py:1785`)
and an injected calculator instance. **Nothing needs to be computed from scratch.**

---

## Q4 — The daily-loss limit ⭐ (and the gap, quantified)

**Confirmed reader** — `capital/fund_manager.py:1279` (post-close breach, inside `release_used`):
```python
daily_pnl = self._store.get_daily_realized_net_pnl(today)     # :1279
loss_limit = self._daily_loss_limit_pct * self._total          # :1280
if self._total > 0 and daily_pnl <= -loss_limit:  → fund_manager.daily_loss_breach → _on_loss_breach()
```
**And the PRE-TRADE gate too** — `capital/risk_engine.py:25`: *"RE7 — DAILY_LOSS uses
`fund_manager.get_snapshot().daily_realized_pnl`"*, and `get_snapshot()` reads the same
function (`fund_manager.py:1507`). **Both halves of the documented dual daily-loss mechanism
are fed by the same reader.**

**Intended semantic: NET** — the function is *named* `..._net_pnl` and its docstring says
"gross PnL minus costs". ✅ Intent confirmed.

**⚠️ THE GAP IS NOT WHAT E4 ASSUMED — it is two errors in opposite directions:**

| Row class | `pnl_delta` | `costs` | Reader computes | vs TRUE net | Effect |
|---|---|---|---|---|---|
| **119** normal exit | gross − charges (**net**) | charges | `net − charges` | **gross − 2×charges** | **loss OVERSTATED** |
| **36** CHECK1/RMS/GTT | gross (costs never applied) | 0 | `gross − 0` | **gross** | **loss UNDERSTATED** ← E4 |

**Concrete, from the live DB (16-Jul, `mode=ro`):**
```
Σ pnl_delta = −2.62   Σ costs = 1.39   →  loss check sees −4.01
TRUE net    = −2.62 (already net in pnl_delta)   implied gross = −1.23
⇒ the control saw a 53% LARGER loss than reality, on the normal path.
```
**Proof `pnl_delta` is NET** — `release_used:1246` writes the reason string as
`pnl={pnl} costs={costs}` where `pnl = gross − costs`, and it matches the column exactly:

| pnl_delta | costs | reason_tail |
|---|---|---|
| 2.43 | 0.49 | `pnl=2.43 costs=0.49` |
| −5.15 | 0.60 | `pnl=-5.15 costs=0.60` |

**Cumulative scale** (all 155 rows; illustrative only — the control is *per day*, and
`RESET_PNL` zeroes it nightly): Σ pnl_delta −96.45, Σ costs 60.24 ⇒ the reader would see
−156.69. The 119 rows are overstated by **₹60.24**; the 36 rows are understated by their
**never-recorded** real costs (≈ 36 × the ₹0.506 mean cost/close ≈ **₹18** — an *estimate*,
because those costs were never computed, let alone stored). **The two errors do not cancel.**

**`RESET_PNL` is built on the bug**: `reset_daily_pnl:1603` reads the *same* reader and writes
`pnl_delta = −old_pnl` (`:1616`), which makes the reader return 0 afterwards — visible in the
data as `Σ pnl_delta == Σ costs` on every completed day. **Self-consistent with the
double-subtraction, so any fix to either side must update the reset or the daily zeroing breaks.**

---

## Q5 — Available capital / buying power

**Credited with `pnl`, ONCE** — `fund_manager.py:1231` (`projected_after = avail_before +
margin + pnl`), `:1257` (`_bucket_add_avail(bucket, margin + pnl)`), `:1259` (`self._total += pnl`).

**⇒ Available capital does NOT double-subtract** (it uses `pnl` directly, not the SQL). So:

| Path | Available capital |
|---|---|
| normal exit (`costs=charges`) | ✅ **correct** — credited NET |
| CHECK1/RMS/GTT (`costs=0.0`) | ❌ **over-credited by the real costs** ⇒ the system believes it has more buying power than it does, and `_total` (the loss-limit denominator) drifts up too |

**This is the one place E4's original framing holds exactly** — and note the two consumers
**disagree with each other**: on the normal path available capital is right while the loss
limit is wrong; on the backstop paths both are wrong, in different directions.

---

## Q6 — Parity (paper vs live)

**Both modes use the SAME modelled cost — there is no paper/live fork to reconcile.**
`main.py:1786` `cost_calculator = CostCalculator(app_config.broker_costs)` is constructed
**BEFORE** `if args.mode == "paper":` (`:1788`) — one mode-agnostic instance from config rates,
not from broker-reported charges. `cost_calculator.py:246` notes `total_round_trip_cost` is
*"Used by the paper engine for realistic P&L accounting (Project Rule 15)"*.

**⇒ Parity is free**: injecting the same instance into the reconciler/GTT monitor feeds NET in
both modes with no branch. **Design question for the design step:** live currently uses the
*modelled* cost, not the broker's actual charges — a deliberate simplification worth ratifying
rather than silently inheriting.

---

## Q7 — Blast radius

**Writers of `pnl_delta`:** `fund_manager.py:1251` (`release_used`, the only trade-close writer)
· `:1616` (`reset_daily_pnl`). **Writer of `costs`:** `:1252` only.

**Readers of `get_daily_realized_net_pnl` (the W10 function) — 4, all in fund_manager:**

| Site | Consumer | Impact of a gross→net change |
|---|---|---|
| `:1279` | **post-close daily-loss breach** → `_on_loss_breach()` | **CONTROL — trips earlier/later** |
| `:1507` | `CapitalSnapshot.daily_realized_pnl` → **`risk_engine` RE7 pre-trade gate** | **CONTROL — blocks entries** |
| `:1603` | `reset_daily_pnl` | **must change in lockstep or the EOD zeroing breaks** |
| `:1736` | rehydrate logging only | cosmetic |

**Consumers that already ROUTE AROUND W10 (do not "fix" these — they are correct today):**
- `ops_dashboard/backend/services/capacity.py:15-17` — *"deliberately NOT
  get_daily_realized_net_pnl, which double-subtracts costs (W10). **Decision D2, approved
  permanent by the G2a review.**"*
- `ops_dashboard/backend/api/risk_capital.py:37-38` · `readers/db_reader.py:300`, `:840`
- `reports/daily_trade_review.py:1068-1071` — states the whole bug verbatim.
> **These prove the intended contract**: `RELEASE_USED.pnl_delta` **is** "the clean trade-close
> realized". The reporting layer was fixed; **the control was not.** A fix should make
> fund_manager agree with the already-approved D2 reading, not invent a third convention.

**Rehydrate** (`fund_manager.py:1706-1708`) replays `pnl = float(row["pnl_delta"])` →
`_bucket_add_avail(bucket, pnl)` — **uses `pnl_delta` directly and does NOT subtract `costs`.**
✅ Consistent with "pnl_delta is net" — so rehydrate is correct today and would **break** if a
fix switched `pnl_delta` to gross without updating it.

**Observability gap found:** `release_used` receives `trade_id` (`:1170`) but **never passes it
to `_write_ledger`** (`:1238-1253`) — all **155/155** `RELEASE_USED` rows have `trade_id NULL`,
so the ledger cannot be joined to `trades`. Not E4, but it blocks per-trade reconciliation of
exactly this bug.

**Regression surface:** `test_fund_manager.py` (`:2318` asserts on `get_daily_realized_net_pnl`)
· `test_fix128_daily_loss_sequence.py` · `test_mo2_check4_partial_capital.py` (`:200`, `:253`,
`:278` — all assert the reader's value) · `test_migrations.py:234` (expects `450.0` from the
reader) · `test_daily_trade_review.py:499` (**names W10 explicitly**) ·
`test_h2_exiting_close_release.py` · `test_cnc_gtt_monitor.py` · `test_emergency_exit_chain.py`
· `test_fix148_broker_gaps.py`. **Several encode the CURRENT (double-subtracting) semantic and
will need deliberate updating — expect a contract inversion, like M-C6/FIX-133.**

**Prior art — this is not new:** `docs/audit/audit_05jul2026.md:572` — *"**[MED] W10 confirmed
unfixed at HEAD**… costs subtracted twice; the docstring wrongly claims pnl_delta is gross…
Direction SAFE-conservative (loss overstated → earlier trips)… CHECK1/RMS closes pass
costs=0.0 so those rows are exempt but slightly overstate net."*
`docs/audit/pending_reconciliation_14jul2026.md:73` — `W10 | double-cost | **OPEN** | 0 fix
commits | fail-safe today; cheap early-pull candidate`.
`docs/audit/batch_classification_16jul2026.md:130` — W10 classified **LOOP**.

---

## Fix DIRECTION (for Web Claude to design — NOT an implementation)

**Treat E4 + W10 as ONE change. Shipping either half alone is a regression:**
- **E4 alone** (pass real costs into the 3 paths) ⇒ those 36 rows become net, and the reader
  then double-subtracts them too ⇒ **understatement becomes double-count on every close**.
- **W10 alone** (stop double-subtracting) ⇒ the 36 backstop rows stay **gross** ⇒ the loss
  limit keeps under-counting exactly the closes that matter most (RMS/CHECK1 = forced exits).

**One coherent direction (recommended):** make `pnl_delta` mean **NET, always** — the contract
the writer, rehydrate, the GUI, the reports and D2 **already** assume — and then:
1. **Reader:** `get_daily_realized_net_pnl` → `SUM(pnl_delta)`; fix the false docstring. This
   aligns the control with the **already-approved-permanent D2** reading.
2. **Writers:** inject the existing `CostCalculator` (`main.py:1786`) into `order_reconciler`
   + `cnc_gtt_monitor` and pass `round_trip_breakdown(...).total`, as `order_placer:2410`
   already does. `product` via the existing `ProductResolver`.
3. **`reset_daily_pnl`** must move in lockstep (it reads the same function).
4. Decide whether `charges = 0.0`-on-exception (`order_placer:2314`, and any new equivalent)
   should stay **silent** — a silent fallback to gross is how this class of bug persists.
5. Consider persisting `trade_id` on `RELEASE_USED` rows so ledger↔trades reconciles.

**Direction-of-risk note for the designer:** today's net error is **conservative on the normal
path** (loss overstated ⇒ trips *earlier*) and **unsafe on the backstop path** (loss
understated ⇒ trips *later*). A fix removes the accidental conservatism, so the **loss limit
will trip later than it does today** on normal exits — that is *correct*, but it is a live
risk-posture change and should be stated to Rama explicitly, not slipped in.

**Not decided here** (design step): whether live should use broker-reported charges instead of
the modelled cost (Q6).

---

## Method note

Read-only throughout: no code/config/schema/DB change, nothing pushed. All DB reads via
`sqlite3 "file:…?mode=ro"`. Every claim above is anchored to `file:line` or to a query result
reproduced in-line. The one estimated figure (≈₹18 of unrecorded backstop costs) is labelled as
an estimate because those costs were never computed and therefore cannot be recovered from the DB.
