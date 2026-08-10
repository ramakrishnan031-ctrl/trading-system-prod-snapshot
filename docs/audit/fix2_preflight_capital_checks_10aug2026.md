# FIX 2 — THE TWO TAUTOLOGICAL PREFLIGHT CHECKS (10-Aug-2026)

**`<BUILT · TESTED · ⛔ NOT PUSHED · ⛔ NOT DEPLOYED>`** — branch `fix/preflight-capital-coherence`
off the **DEPLOYED** ref `645728d`, seventh worktree `D:\Projects\trading-system-fix2`.

**PROVENANCE, recorded because it matters (`G1`).** The line `start Fix 2` in the card was an
**AUTO-FILL, not Rama.** It proceeds because Rama said *"start to fix"* and the scope was authorised
in the card itself. ⛔ The auto-filled text is not the instruction; the authorisation is.

**SCOPE — the two preflight checks and nothing more.** ⛔ Not in Fix 2: the 3-slot ceiling,
`max_open_delivery_positions`, any new percentage or rupee limit, and the book-growth problem
(that is Fix 3 and must not be mixed in).

---

## §1 — `capital_deployment` · **BUILT**

### The defect, as measured

On the 10-Aug 08:15 boot the check printed **`capital deployed 432.3%`** and returned **PASS**, on the
same boot the capital invariant hard-killed on `NEGATIVE_MARGIN_AVAILABLE: -844.08`.

**(P)** Every reachable path below the div-0 guard returned `self._passed()`. There was **no
predicate at all** — not a threshold set too loosely, but no comparison of any kind. It could not
have gone non-green for any input whatsoever.

### ⭐ What 432.3% actually is — and why this changes the fix

| operand | source | what it is |
|---|---|---|
| denominator `opening` | `fm_ledger` first `INIT` row, `balance_after` | **broker CASH** — `fund_manager.initialize()` writes `broker_balance` verbatim (`fund_manager.py:468-476`) |
| numerator `margin_used` | `SUM(trades.margin_reserved)` over `OPEN/PARTIAL/EXITING` | margin on open positions — **including a carried delivery position, whose money the broker already converted into a holding and removed from that cash** |

> **The two operands sit on DIFFERENT BASES. `432.3%` is that mismatch printed as a number, not a
> deployment level.** Same class as the `5.25% of TOTAL` vs `6% of SEGMENT` error: every percentage
> carries its base or it is not a number.

⚠️ **And Fix 1 does not change this reading.** Fix 1 lifts `_total` to cash+carry **in memory only** —
it writes no ledger row for the carry — so the `INIT` row still holds CASH and this check would still
print 432.3% on the same day. The two fixes are genuinely independent.

### The predicate — **no number was invented**

```
if used > opening:  ->  _warn(...)      # else _passed(...)
```

The bound is **the one the system already enforces**, not a new risk appetite:

- every reservation is made against a bucket;
- the buckets partition the same total — `intraday_bucket_pct + positional_bucket_pct = 0.70 + 0.30 = 1.0`;
- `_check_invariant` holds `avail + reserved + used == cash_floor` with all four non-negative (INV6).

⇒ **Deployed margin can never exceed the capital it was reserved from.** `used > opening` is reachable
**only** when `used` counts a position whose money is not in `opening` — i.e. a carry. The bound is
100%, and 100% is the sum of the split. ⛔ Nothing was chosen, tuned, or recalled.

**Policy has no deployment threshold to use instead, and this was checked rather than assumed.**
`portfolio_allocator.max_portfolio_deployment_pct` is the one key in the tree with the right shape
(*"A3 shared concentration cap (deployed-margin %)"*) — it is **`null` = INERT**, belongs to a
component in `allocator_mode: "shadow"`, and its validator only constrains it to `(0, 1]` when set.
⛔ **It is not an authored ceiling and was not treated as one.**

### Severity is UNCHANGED — the 25-Jul ruling is preserved exactly

The check stays `Criticality.WARN`. **(P) `report.py:44-51`**: `is_blocking` is
`Status.FAIL AND Criticality.CRITICAL`, so a WARN **can never block a run or make it CRITICAL**; it
lands in `warnings`, making `overall_status` `READY_WITH_WARNINGS` and `severity` `"WARN"`. The
25-Jul decision — *"the deployment % is an observability number, not a readiness gate"* — is intact.
**What changed is only that the check CAN now be non-green.**

### RED-first — measured on both trees

Run with `scripts/preflight/checks/engine.py` reverted to `645728d`, tests unchanged:

| test | pre-fix | post-fix |
|---|---|---|
| `..._the_live_10aug_shape_is_not_green` (opening 209.80, legs 446.106 + 460.91632) | 🔴 **PASS returned for 432.3%** — the tautology, demonstrated | ✅ WARN |
| `..._warns_the_moment_used_passes_opening` (1000.01 vs 1000.00, and exactly 1000.00) | 🔴 | ✅ |
| `..._pass_text_names_its_base` | 🔴 (wording is new) | ✅ |
| `..._an_ordinary_carried_book_still_passes` (2,000 carry / 10,000 cash = 20%) | ✅ **control** | ✅ |
| `..._flat_book_passes` | ✅ **control** | ✅ |
| `..._emits_a_real_nonzero_pct` · `..._div0_states_a_reason` · `..._is_alert_only_never_critical` · `..._degrades_and_never_raises` | ✅ **controls** | ✅ |

**`3 failed / 14 passed` pre-fix · `17 passed` post-fix · all preflight suites `175 passed`.**

**FULL GATE** (Git Bash, `pytest tests/unit tests/integration`, working tree clean):
**`PYTEST_RC=1` · 9F / 5,562P / 4S · 871.91 s.** ✅ **SET-IDENTICAL to the nine ids recorded at
`645728d`** (`docs/audit/rulings_1_2_verification_07aug2026.md`) — **0 new, 0 disappeared.**
⭐ The arithmetic **decomposes** rather than merely adding up: `5,557 baseline + 5 new = 5,562`, and
the 5 are verified as `test_preflight_engine.py` going **12 → 17** test definitions.

⚠️ **A provisioning trap, diagnosed rather than labelled *"PC-env"*:** a freshly-created worktree
fails `kite_instruments_fresh` because **`config/instruments.csv` is gitignored (`.gitignore:39`)** —
it is a downloaded artifact, not a tracked file. Copy it in when creating a worktree. The failing
check is Phase-A `kite_instruments_fresh`; `capital_deployment` is a Phase-B Engine check and does
not appear in that run at all. ⛔ **Not a regression, and it would have been the wrong answer.**

⚠️ **One correction made during the build, recorded rather than quietly fixed:** the
"ordinary carried book" guard first also asserted the new wording, which made it red on the old tree
for a **cosmetic** reason — i.e. it stopped being a two-tree control. The wording assertion was split
into its own post-fix-only test. This is the same defect found in Fix 1's own test file the same day;
a guard that cannot reach its assertion on the old tree is not a guard.

### ⛔ What was NOT done, named rather than left silent

**The denominator is still CASH.** Correcting it means re-deriving Fix 1's carry rule inside preflight
— a **second site for one semantic**, which is precisely the defect Fix 1 removed — and it would need
a product split that `trades` cannot answer (**there is no `trades.product` column**; it lives on
`orders` via an `ENTRY`-leg join, and a missing `ENTRY` row reads `NULL`, invisible to any filter).
The operands are printed **with their bases** instead, so the number cannot be read as something it
is not. **Correcting the base is a separate decision, not Fix 2.**

---

## §2 — `kite_funds_available` · 🔴 **STOPPED — THE THRESHOLD IS A DECISION THAT DOES NOT EXIST**

`broker.py:26` `EXPECTED_MIN_CASH = 0.0`; `:327` `if float(cash) > EXPECTED_MIN_CASH: _passed(...)`,
else `_failed(...)`. Criticality `CRITICAL`.

**This one is not tautological in the strict sense — it CAN go red** (cash ≤ 0 fails). Its defect is
narrower and exactly as the card names it: **it is a LIVENESS check on the field, not an ADEQUACY
check on the amount.** On 10-Aug it saw `net = 209.80 > 0.0` and passed, while the account could not
cover a `907.02` book.

### Making it an adequacy check requires a number, and policy does not define one

**(P) with the search width stated, because an absence is only established by a check wide enough to
have found the thing:**

- **A.** Every key in `config/` (recursive, all YAML) whose name contains `cash|fund|balance|capital|
  minimum`, excluding the known sizing/risk percentages → **the only hit is the `capital:` section
  header itself.** There is no minimum-cash key.
- **B.** Every `.py` outside `tests/` for `MIN_CASH|MINIMUM_CASH|CASH_FLOOR_MIN|MIN_CAPITAL|
  MIN_BALANCE|min_funds` → **the only hit is `EXPECTED_MIN_CASH = 0.0` itself.** Nothing else defines
  one anywhere.

**And the obvious derivations do not survive contact:**

- `cash >= margin_used + margin_reserved` — ⛔ **WRONG after Fix 1.** A carried delivery position makes
  broker cash legitimately *lower* than `used`, because the money is in stock. This would fire a false
  alarm every morning a position is held — the exact class of alarm Fix 1 removed.
- `cash + carry >= used + reserved` — ⛔ **tautological by Fix 1's construction.**
- *"enough to fund the smallest tradable position"* — ⛔ not derivable: `min_qty_threshold: 1` is one
  share of any price, and there is no per-symbol rupee allocation (that was refuted as a config value).
- `fund_manager_balance` already asks *"is free cash sane?"* on the whole-account base. Duplicating it
  here would make two checks carry one question.

### 🔴 THE MISSING DECISION, NAMED — for Rama, ⛔ no number proposed

> **How much broker cash is too little to begin a trading day?**

Two candidate **control FORMS** (they are different instruments, not two values of one):

1. **An absolute rupee floor** — simple, but it is a `₹10k`-era figure that will silently rot as the
   account grows. The campaign has already retired two absolute limits for exactly this.
2. **A capital-relative floor** — a fraction of the day's opening or expected capital; permanent by
   construction, consistent with `daily_loss_limit_pct`, `max_position_value_pct` and
   `max_concentration_pct`, all of which were deliberately moved to this form.

⛔ **NO VALUE IS PROPOSED, AND NONE MAY BE INFERRED FROM THIS DOCUMENT.** This campaign has three
unauthored figures on record; an unsourced value does not age into being sourced. Only a quoted,
timestamped line from Rama closes this.

**Until it is answered, `kite_funds_available` is UNCHANGED** — it keeps doing correctly the narrower
job it actually does.

---

## §3 — Not touched

⛔ `max_open_delivery_positions` · the 3-slot ceiling · any new percentage or rupee limit · the
book-growth problem (Fix 3) · `fund_manager.py` (Fix 1's logic is frozen) · the sizing split · F6.
