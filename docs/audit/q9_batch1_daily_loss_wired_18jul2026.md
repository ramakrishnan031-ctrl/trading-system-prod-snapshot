# Q9 batch 1 — the daily loss limit, wired-proven (18-Jul-2026)

Q9's #1 gap by cost-of-silent-failure, closed. Plus the two near-vacuous assertions
tightened — which surfaced a real name/behaviour mismatch.

**Deployed:** tag **`deploy-18jul-q9-batch1` → `92d3c71`**. PC == VM bare == tag.
**Test-only — zero production files changed; no runtime behaviour change.**

---

## LEAD SUMMARY

| Item | Outcome |
|---|---|
| **1 — daily loss limit wired** | ✅ Both halves proven to FIRE through the real path, each with a **positive AND negative** case, full capital picture asserted at every transition, **and proven to bite** via a planted break in each half. |
| **2 — the two 9-way assertions** | ✅ Now assert the specific gate — and doing so **exposed that neither test ever exercised the global `max_open_positions` gate it was named for**. Fixed so it does. Also corrected a docstring advertising a scenario the suite never ran. |

---

# ITEM 1 — the daily loss limit, wired

`tests/integration/test_q9_daily_loss_limit_wired.py`, on the existing `wired_system`
fixture (no parallel harness).

## What it drives

Three **real** losing round-trips through the wired path — `_seed_signal_row` →
`fund_manager.reserve` → `order_placer.place` → ENTRY `OrderFilled` → SL `OrderFilled` →
close — until the limit is crossed. Not mocks: real reservations, real fills, real closes,
real `fm_ledger` rows.

## Both halves, each positive AND negative

| Half | Site | Negative (must NOT fire) | Positive (must fire) |
|---|---|---|---|
| **POST-CLOSE breach** | `fund_manager.py:1279-1290` | after loss #1, reader above the 2% limit → `on_daily_loss_breach` **not** called | crossing it → callback **dispatched** |
| **PRE-TRADE gate (RE7)** | `risk_engine.py:598-604` | reader above the 5% limit → `DAILY_LOSS` is **not** the rejection reason | past it → rejected **`REJECTED_DAILY_LOSS` specifically** |

The negative half matters as much as the positive: without it the test could not distinguish
a working gate from one that rejects everything.

## ⭐ Why the assertions are relationship-based (§1c)

The reader's contract is **mid-migration**. Today `get_daily_realized_net_pnl` computes
`SUM(pnl_delta) - SUM(costs)`; E4/W10 (`e4-w10-pnl-contract@ad34ee4`, UNPUSHED) changes it to
`SUM(pnl_delta)` because `pnl_delta` is already NET.

* A **literal rupee figure** would bake in today's double-subtracting behaviour and break the
  day that branch lands.
* An assertion phrased against the **true net loss** would be wrong *today*, because the
  current reader over-states the loss and therefore fires **earlier**.

So every threshold assertion is phrased **relative to the reader's own returned value**:
*"whatever `get_daily_realized_net_pnl()` returns, once it is ≤ −(pct × total) the next signal
is `REJECTED_DAILY_LOSS`; while it is above, `DAILY_LOSS` is not the reason."* Correct under
**both** contracts, it proves the **wiring** (Q9's job), and it stays green when E4/W10 merges.
The module docstring states this at length so nobody "helpfully" hard-codes a figure.

> **Q9 proves the layer is wired and fires. E4/W10 fixes whether the number it reads is right.
> Do not conflate them.**

The known `costs=0.0` distortion on RMS/CHECK1 closes is deliberately **not** depended upon —
the scenario drives ordinary SL closes, which are costed normally.

## Two subtleties the scenario had to respect

1. **Gate order.** RE5 runs `… DAILY_TRADES, CONSECUTIVE_LOSSES, DAILY_LOSS …` and the fixture
   sets `max_consecutive_losses=4`. The limit therefore had to be crossed in **at most three**
   closes — a fourth would trip `CONSECUTIVE_LOSSES` first and the test would pass for the
   wrong reason.
2. **The limit moves.** Both halves compute `pct × the CURRENT total`, and `total` **shrinks**
   as realized losses accrue. The run showed the gate enforcing **23,642**, not the 25,000 a
   start-of-day figure would have given. Limits are re-derived at each check.

Every polarity assertion is preceded by an explicit **precondition assert on the reader**, so
the test can never pass vacuously by failing to reach the threshold.

## Full capital picture at each transition (§1d)

`_capital_picture()` captures total / available / reserved / used / `daily_realized_pnl` /
reader, asserted before and after **every** step: a reservation must raise `reserved` and must
**not** move realized P&L; a fill must move reserved → used; a close must release both and move
realized P&L down; `snapshot.daily_realized_pnl` must agree with the reader (or the two halves
would enforce on different numbers); a **rejected** signal must reserve nothing.

## ⭐ PROOF THAT IT BITES (§1e)

| Planted break | Result |
|---|---|
| `risk_engine.py:598` neutered | ❌ `PRE-TRADE GATE DID NOT FIRE: reader -27283.62 <= limit -23642.91 but the signal terminated as 'PROCESSED'` (log: `approved=True failed_check=none`) |
| `fund_manager.py:1281` neutered | ❌ `POST-CLOSE HALF DID NOT FIRE: reader -18189.08 <= limit -9638.11 but on_daily_loss_breach was never called` |
| both restored | ✅ 1 passed; `capital/` shows **0** modified files |

Each half is **independently** proven to bite. RED-on-old by absence is not sufficient on its
own; this is what counts.

---

# ITEM 2 — the two near-vacuous assertions ⭐ (and what tightening them exposed)

`test_risk_rejection_max_open_positions` existed in **both** integration files, each accepting
**any of nine** rejection statuses — proving "the risk engine rejected for some reason", never
which gate.

## 🔴 The finding: the test never tested what it was named for

Tightening it to `REJECTED_OPEN_POSITIONS` made it **fail**, with
`got 'REJECTED_STRATEGY_POSITION_LIMIT'`.

Cause: it seeded both OPEN positions on the **same strategy** as the incoming signal, which
trips the **per-strategy** cap at `signals/signal_processor.py:624-640` — a gate that runs
**before the risk engine is reached at all** (it is not part of the RE5 sequence). So the
**global `max_open_positions` gate was never exercised**, and the nine-way accept had hidden
that for as long as it existed.

**Neither gate is broken** — this is a test-quality finding, not a live capital finding, so the
§1 "STOP and report" clause did not apply. But the global cap had **no wired proof**, and the
test that appeared to supply it was proving something else.

## The fix — make the scenario match the name

`_seed_open_trades` now takes a `strategy` (defaulting to the previous value), and both tests
seed `gap_go_long`. The incoming `vwap_bounce_long` signal then sits at 0/2 of its own cap
while the **global** cap is saturated → `OPEN_POSITIONS` is the gate that fires, and both tests
assert exactly that. **The global cap is now wired-proven** — coverage that did not previously
exist.

**Proven to bite (§2c):** neutering the global comparison (`risk_engine.py:490`) makes both
tests fail with `expected the OPEN_POSITIONS gate specifically, got 'PROCESSED'`; restoring
returns them to green with `capital/` clean.

## The misleading docstring (§2b)

`test_full_signal_flow.py:9` advertised *"Sad path 1: Signal rejected by risk
(daily_loss_limit reached)"* — a scenario the suite **did not run**. That is precisely how the
gap survived review. It now describes what the test does and points at the real daily-loss
coverage in `test_q9_daily_loss_limit_wired.py`.

---

## Regression + deploy

**Full suite (NOT scoped): 11 failed, 4939 passed, 5 skipped (779s).** New-failure set versus
the 18-Jul base baseline is **EMPTY ⇒ ZERO ATTRIBUTABLE**; the 11 are the known PC-env /
Saturday calendar-gated set. Counts reconcile: 4938 + 1 new test = 4939. **No integration
failures** (full integration suite 33 passed).

**Deploy:** backup `pre_deploy_q9b1_20260718.db` — verified sound (`quick_check=ok`, v44, 361
trades). Pushed → post-receive checkout OK. **PC == VM bare == tag == `92d3c71`**; the new test
is present on the VM and **passes there** (1 passed). Schema **v44 unchanged**, integrity ok,
**0 FK violations**; services correct (`trading-system` inactive as expected on a down
Saturday). The push also carried the pending docs-only Q9 map commit (`04ffc4d`) per the
standing fold-into-next-deploy pattern.

---

## Q9 status after this batch

| Layer | Before | After |
|---|---|---|
| Daily loss limit — pre-trade (RE7) | UNIT-ONLY | ✅ **WIRED** (pos + neg, bite-proven) |
| Daily loss limit — post-close | UNIT-ONLY | ✅ **WIRED** (pos + neg, bite-proven) |
| Global `max_open_positions` | *believed* wired (it was not) | ✅ **WIRED** (bite-proven) |
| Per-strategy position cap | accidentally proven, ambiguously | still covered, now unambiguous |

**Remaining Q9 queue (later batches):** #3 kill-switch last-mile re-check (TOCTOU) · the
capital-invariant integration test (the E4/W10-class catcher) · #4 sizing floors/caps
(batchable) · #5 post-restart capital restoration.

## Not in scope / not done

E4/W10 not merged or deployed. No production code changed. No flag flipped. Destructive CTs not
run. No schema change.
