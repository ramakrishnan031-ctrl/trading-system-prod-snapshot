# Q9 batch 3 — the capital invariant, wired (18-Jul-2026)

Q9 item (C) closed: the money identity is now asserted at **every** lifecycle stage on the real
path, anti-vacuity guarded, bite-proven — and the E4/W10 contract bug is documented in
**executable** form rather than prose.

**Test-only — zero production files changed; no runtime behaviour change.**

---

## LEAD SUMMARY

| | Outcome |
|---|---|
| **The invariant** | Asserted at fresh / reserve / fill / close / rejection / multi-position / **winning** close. `5 passed, 1 xfailed`. |
| **The hypothesis** | **Corrected in two places** — per-bucket identity does NOT hold, and P7a's `min(0, …)` form is a different quantity from `_total`. |
| **§A4 exact delta** | `reader − truth = −52.400000` and `−SUM(costs) = −52.400000` — **identical to 6 dp**, on both a losing and a winning close. |
| **Bite proof** | A naive skew was caught by *production's own* guard; the meaningful plant is internally consistent, fires the runtime guard **0 times**, and the test still catches it. |
| **§C — E4/W10** | ⭐ **On `ad34ee4` the contract invariant PASSES and the delta goes to EXACTLY `0.000000`.** Structural invariants stay green. |

---

## §A — the capital model, verified against real code and runtime

### A1 — the quantities and where they are authoritative

`CapitalSnapshot` (`capital/fund_manager.py:206-217`) exposes **eight**: `total`,
`intraday_avail/reserved/used`, `positional_avail/reserved/used`, `daily_realized_pnl`.
There are **two buckets** — batch 2 asserted only the intraday three, so the identity here sums
both. Initialised at `:440-446` (`_total = broker_balance`, avail split by bucket pct,
reserved/used = 0). Transitions: `reserve :481` · `release :597` · `commit_to_used :854` ·
`release_used :1161` (P&L applied at `:1259` `self._total += pnl`) · placement failure →
`_handle_placement_failure` → `_fm.release`.

### A2 — which invariants actually hold ⭐ (the hypothesis was wrong twice)

| | Hypothesis | Verdict |
|---|---|---|
| **I1** | `available + reserved + used == total` | ✅ **HOLDS — globally.** This is what *production itself* asserts: `fund_manager.py:2268` calls `assert_capital_invariant(cash_floor=_total, realized_pnl_today=0.0)`, so the RHS is `_total` (docstring `:2209`: "fund_manager tracks _total directly"). |
| **I1 per-bucket** | (implied by I1) | ❌ **DOES NOT HOLD — must not be asserted.** `fund_manager.py:2235` guards the per-bucket checks behind *"only when a partition is already negative"*, and its comment states a legitimate PnL-shifted per-bucket split (`avail+reserved+used != total*pct`) **is never reached**. |
| **I2** | `total == starting + realized_net_pnl` | ✅ **HOLDS**, and `_total` moves by the **full** P&L *including profits* — runtime-verified with a winning trade (`+1945.7` net → `_total` `500000 → 501945.70`). ⚠️ P7a's `cash_floor + min(0, realized)` (`capital/invariant.py:89`) is a **different quantity** — the *tradable balance*, where profits are withheld until T+1. **Do not conflate them.** |
| **I3/I4** | reserved/used == sums of outstanding items | ✅ consistent with I1; covered indirectly by the multi-position stage. |
| **I5** | reader == independent ground truth | ❌ **FALSE TODAY** by E4/W10 — see §B3. |

### A3 — the independent ground truth

`SUM(trades.net_pnl)` — a **different table**, written by a **different path**
(`core/state_store.py:2268`, on close) from the one the reader aggregates (`fm_ledger`, written
only at `fund_manager.py:2365`). Two computations sharing a source cannot cross-check each
other; these do not share one. The reader's defect is in its *aggregation formula*, which is
exactly what a trade-level recomputation exposes.

### A4 — ⭐ the E4/W10 delta, EXACT (not approximate)

Runtime-probed on the wired fixture:

```
LOSS close:  gross=-1000.00  charges=52.40  net=-1052.40
             reader=-1104.8000   truth=-1052.4000
             reader - truth = -52.400000        -SUM(costs) = -52.400000     ✅ identical
WIN  close:  gross=+2000.00  charges=54.30  net=+1945.70
             reader - truth = -54.300000        -SUM(costs) = -54.300000     ✅ identical
```

Algebraically: `pnl_delta = gross − costs` (already net), so
`reader = SUM(pnl_delta) − SUM(costs) = truth − SUM(costs)` ⇒ **`reader − truth ≡ −SUM(costs)`**.
This pins the discrepancy to E4/W10 specifically rather than to "some mismatch", and it holds
on both sides of zero.

### A5 — scenario hazards designed around

* **The daily-loss breach is NOT passive** — `_make_daily_loss_cb` (`main.py:757-800`)
  force-closes via EOD `fire_now` and arms a **soft kill**. Fixture limit = 2% × 500,000 =
  ₹10,000; the scenarios here realise ~₹1,000 each, and **every stage asserts the kill never
  armed**, so a future sizing change cannot silently wreck the picture.
* `max_consecutive_losses=4` precedes `DAILY_LOSS` in RE5 — no scenario drives 4 losses.
* The per-strategy cap (`signal_processor.py:624-640`) fires **before** the risk engine → the
  multi-position case seeds across **different strategies** (batch 2's fix).
* `paper_auto_fill_delay_sec=60` → an admitted order stays unfilled and holds a **live
  reservation**; the identity accounts for it.
* RMS/CHECK1 `costs=0.0` — these are ordinary SL/TGT closes, costed normally; nothing depends
  on the zero-cost path.

---

## §B — the test

**Stages (B1):** fresh → reserve → ENTRY fill → close (loss); a **rejected** signal that must
move nothing; **two concurrent positions across different strategies**; and a **winning**
round-trip. Nothing wired had ever driven a winner through the capital path — a P&L **sign
error is invisible to a losses-only test**, so the winner asserts `_total` *increases* by
exactly the net.

**Anti-vacuity (B2).** An identity like `avail + reserved + used == total` holds trivially on a
system that did nothing, so each stage additionally asserts the quantities **moved**, in the
right **direction**, and that the ones which should not move did not: reserve raises `reserved`
and lowers `avail` while leaving `used` and realized P&L untouched; the fill converts
`reserved → used`; the close returns `used` to baseline and moves realized P&L; two positions
hold strictly more `used` than one.

### B3 — the contract invariant: strict xfail + an exactness guard

I5 is false today and must not be "fixed" into passing. The pair below meets all four
requirements:

1. **green today** — `xfail(strict=True)` keeps the suite honest about a known, accepted bug;
2. **cannot silently start passing** — when E4/W10 lands the test XPASSes, and `strict=True`
   reports an XPASS as a **FAILURE**, forcing a deliberate flip during that migration rather
   than a stale test rotting in the suite (**verified in §C: it did exactly this**);
3. **no rewrite, no hard-coded figure** (rule D) — the reason string names
   `e4-w10-pnl-contract@ad34ee4`;
4. **a NEW bug cannot hide behind the old one** — a *separate, plain-green* test asserts the
   delta equals **exactly** `−SUM(costs)`. If the discrepancy ever becomes anything else, the
   suite breaks immediately even while I5 is still xfailed.

Alternatives rejected: a `skip` hides it; asserting current buggy behaviour bakes the bug in
and needs rewriting; a warning is ignorable.

### B4 — parity

The capital path is **shared, not duplicated**: `FundManager` takes no mode argument and
contains no paper/live branch; the divergence is downstream at
`broker/zerodha_adapter.py:348` (`paper_mode → _paper_place_order`). So the identity proven in
paper holds identically in live — the same structural argument batch 2 used for the kill check.

### B5 — ⭐ PROOF THAT IT BITES (and what the first attempt revealed)

**Attempt 1 — instructive failure.** Skewing `reserve` (deduct `margin*1.01`, book `margin`)
was caught by **production's own runtime guard** before the test's assertion ran:
`CapitalInvariantViolation: Capital invariant violated after 'reserve': lhs=499895.0000
rhs=500000.0000 delta=-105.0000`. Good news about the runtime guard — but it proves nothing
about *this test*.

**Attempt 2 — the meaningful plant.** Apply `pnl * 1.10` to **both** the bucket **and**
`_total`, so `available + reserved + used == _total` **still holds**:

| | Result |
|---|---|
| production's `CapitalInvariantViolation` | fired **0 times** — the runtime guard cannot see it |
| the new test | ❌ `AssertionError: I2 BROKEN: total moved by -1157.6400 but realized net P&L is -1052.4000` |
| restored | ✅ `5 passed, 1 xfailed`; `capital/` and `orders/` show **0** modified files |

**That is the whole point of this file:** an internally-consistent wrong VALUE — the E4/W10
shape — is invisible to correct column-level writes *and* to the structural runtime guard, and
only a cross-checked end-to-end value assertion catches it.

*(I5 needs no planted break: it is xfailing on the real bug today, and its §A4 exactness proof
is its evidence.)*

---

## §C — does E4/W10 actually fix it? ⭐ (read-only; evidence for Rama's sign-off)

Run in a **throwaway `git worktree`** on `e4-w10-pnl-contract`@`ad34ee4` (never `git stash`;
nothing merged, cherry-picked or committed; worktree removed afterwards).

```
[XPASS(strict)]  test_reader_equals_independent_ground_truth
ON e4-w10 @ad34ee4:  reader=-1052.4000  truth=-1052.4000  costs=52.4000
                     DELTA(reader-truth) = 0.000000
4 passed, 2 failed
```

**Reading it:**
* ✅ **The contract invariant PASSES on that branch** — the reader equals the independent
  ground truth. **The delta goes to EXACTLY zero** (vs `−52.40 == −costs` on `main`).
* ✅ **The structural invariants stay green** there — E4/W10 does not disturb I1/I2.
* ✅ The two "failures" are **the design working**: the strict xfail XPASSes (reported as a
  failure — the deliberate announcement), and the exactness guard correctly objects that the
  delta is no longer `−costs` because it is now `0`.

⇒ **E4/W10 is verified correct against an INDEPENDENT computation on the wired path**, not
merely against its own unit tests. This is evidence for the pending risk-posture sign-off; it
does **not** approve, merge or deploy anything.

**When E4/W10 lands, the migration is two edits in this file:** drop the `xfail` marker, and
change the exactness assertion from `−SUM(costs)` to `0`.

---

## Regression + deploy

**⚠️ One NEW failure appeared and was attributed, not waved through.**
`tests/unit/test_interactive_startup.py::test_holiday_guard_missing_yaml_proceeds` was not in
the morning baseline. Attribution, three ways:

1. it fails in **isolation** on my tree;
2. it fails **identically on the pre-change tree** (`git worktree` at `40db66a`, my file
   confirmed absent — never `git stash`);
3. **mechanism identified:** `main.py:1686-1695` — outside the service window `main()` prints
   *"Not starting (clean exit 0)"* and returns **0**, while the test expects **5**. It was
   **16:24 IST**; the morning baseline was captured at **10:55**, *inside* the window.

This is the documented **time-of-day-gated** PC-env class. Because that morning baseline was a
different window, a **fresh base run was taken in the SAME window** for an apples-to-apples
diff:

| Run (same window, ~16:2x IST) | Result |
|---|---|
| **BASE** `40db66a` | **42 failed, 4909 passed, 7 skipped** (774s) |
| **MINE** | **12 failed, 4946 passed, 5 skipped, 1 xfailed** (780s) |

* **New failures vs the same-window base (`comm -13`): EMPTY ⇒ ZERO ATTRIBUTABLE.** The
  service-window test **is** present in the base failure set, confirming the diagnosis.
* **Total collected reconciles EXACTLY:** base `42+4909+7 = 4958`; mine `12+4946+5+1 = 4964`;
  difference **6** = precisely this file's 6 tests (5 passing + 1 xfailed).
* **`xfailed = 1`** — the strict marker is visible in the totals, as required.
  **`XPASS = 0`** — it is genuinely xfailing, not silently passing.
* The base's *higher* failure count (42 vs 12) is the documented flaky heavy-`test_main.py`
  set moving in the **safe** direction, as in earlier batches.

**Deploy:** tag **`deploy-18jul-q9-batch3` → `5fac11a`**. Backup `pre_deploy_q9b3_20260718.db`
— **verified sound**: `quick_check=ok`, schema v44, 361 trades. Pushed → post-receive checkout
OK. **Verified PC == VM bare == tag == `5fac11a`**; schema **v44 unchanged**,
`integrity_check=ok`, **0 FK violations**; services correct (`trading-system` inactive as
expected on a down Saturday, `alert-watcher` + `gui-dashboard` active); **kill-switch state
INACTIVE**. **On the VM the file runs `5 passed, 1 xfailed`** — correctly XFAIL there too, not
XPASS.

## Not in scope / not done

E4/W10 not fixed, merged or deployed (§C is read-only evidence only). Q9 #4 sizing floors/caps
and #5 post-restart capital restoration are later batches, as is back-filling coverage-matrix
metadata for other layers. No production code change, no flag flip, no schema change,
destructive CTs not run, trading system not restarted.
