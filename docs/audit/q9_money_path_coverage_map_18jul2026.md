# Q9 — money-path + safety-layer wired-in coverage map (18-Jul-2026, READ-ONLY)

Applies the S4 lesson systematically: for each safety layer that protects real money, is it
proven to **fire through the real wired path**, or only in a unit test with mocks?

Nothing was built or changed. No flag flipped. No CTs run.

---

## ONE-LINE STEER

**Of 14 safety layers on the money path: 6 are WIRED-proven, 8 are UNIT-ONLY, 0 are wholly
untested.** The single most expensive silent failure is the **DAILY LOSS LIMIT** — *both*
halves are UNIT-ONLY, **no integration test has ever driven a realized loss past the
threshold**, and the two tests that appear to cover it are near-vacuous (they accept **any of
9** rejection reasons, so they prove "some gate rejected", never *which*). It is also the one
layer whose *input contract is currently known-wrong* (E4/W10, `pnl_delta` = gross not net,
unpushed) — so it is simultaneously the least-proven and the most-suspect layer, and it is the
thing that stops the account bleeding on a bad day.

---

## §0 — CT-invariant auto-discovery (ChatGPT's recommendation): **SATISFIED, no change needed**

Both properties already hold. Discovery is `CT_DIR.glob("*.py")`
(`test_ct_guard_invariant.py:69-70`) — a **glob, not a manual list** — so every future module
is covered the moment it lands. Proven live: dropping a brand-new
`ct_future_helper_TEMP.py` containing `sqlite3.connect("data_store/trading_system.db")` made
invariants **A and B fail immediately with zero allow-list edits** (2 failed / 3 passed);
removing it restored green (5 passed). It **fails closed**, and the allow-list holds exactly
**3 inherent entries** (the guard module, the test that asserts refusal, the invariant itself)
— each justified in-line. Recorded as satisfied; nothing committed for §0.

---

## Q1 — the money paths (file:line)

| Path | Site |
|---|---|
| Reserve margin | `capital/fund_manager.py:481 reserve()` |
| Commit reservation → used | `:854 commit_to_used()` |
| Adopted-entry commit (orphans) | `:1009 commit_adopted_entry()`, `:1101 restore_adopted_reservation()`, `:1129 release_adopted_reservation()` |
| Release (unused / used) | `:597 release()`, `:1161 release_used()`, `:1305 _committed_release_margin()`, `:649 release_slm_buffer()` |
| Top-up | `:741 top_up_reservation()` |
| **fm_ledger write (the money journal)** | `:2365 INSERT INTO fm_ledger` — **the single owner** |
| Broker balance sync | `:1340 sync_from_broker()` |
| Capital snapshot (the decision read) | `:1499 get_snapshot()` → `CapitalSnapshot` |
| Unrealized MTM | `:1521/:1533/:1543/:1558/:1566/:1580` |
| **Daily realized-P&L reset** | `:1593 reset_daily_pnl()` |
| **Restart capital restoration** | `:1626 rehydrate_from_open_trades()`, `:1777 today_realized_pnl_carryover()`, `:1806 _replay_open_trade()`, `:2076 _restore_reserve_from_ledger()` |
| **trades money columns** | `core/state_store.py:2268` — `gross_pnl = ?, charges = ?, net_pnl = ?` (**the single writer**) |
| Recent-losses read (gate input) | `core/state_store.py:870-888` (`net_pnl` of last N closed) |
| Position sizing | `capital/position_sizer.py` — qty-by-risk, tier multiplier, FIX-133 min-lot floor, M-C6 zero-multiplier skip, `max_position_value`, concentration |
| Required margin | `capital/fund_manager.py:242/:457 required_margin()` |
| Bucket split (intraday/positional) | `:111 resolve_bucket_allocation()`, `:2150 _bucket_for_intent()` |

---

## Q2/Q3 — safety layers, classified ⭐

| # | Safety layer | Site | Class | Evidence |
|---|---|---|---|---|
| 1 | Kill switch @ webhook admission | `signals/webhook_receiver` | **WIRED** | `test_end_to_end_smoke.py:357 test_kill_switch_rejects_webhook` |
| 2 | Hard-kill flatten chain | `capital/kill_switch.py` | **WIRED** | `test_hard_kill_flatten_chain.py:114` — ⚠️ **mock broker only; the first live HARD_KILL is still M-C8's real test** |
| 3 | Emergency-exit chain | `orders/` | **WIRED** | `test_emergency_exit_chain.py:136` |
| 4 | Sector concentration (gate-8) **enforce** | `capital/risk_engine.py` | **WIRED** | `test_hardening_scenarios.py:86/:111` — the fixture forces `_sector_cap_mode="enforce"` (`:120`), so the enforcing branch **does** execute ✅ |
| 5 | Capital reserve→commit→release lifecycle | `fund_manager` | **WIRED** | `test_end_to_end_smoke.py:694/:746` (long + short, EF-3 direction-aware release) |
| 6 | Capital release on broker rejection | `fund_manager` | **WIRED** | `test_full_signal_flow.py:441` |
| 7 | **DAILY LOSS LIMIT — pre-trade (RE7)** | `risk_engine.py:25` (`get_snapshot().daily_realized_pnl`) | 🔴 **UNIT-ONLY** | **no integration test drives it**; `REJECTED_DAILY_LOSS` appears only inside 9-way accepted-status sets (`smoke:289`, `flow:401`) |
| 8 | **DAILY LOSS LIMIT — post-close breach** | `fund_manager.py:1280` (`loss_limit = pct * total`) | 🔴 **UNIT-ONLY** | unit only (`test_fix128_daily_loss_sequence.py`) |
| 9 | Kill-switch **last-mile re-check** (TOCTOU) | `order_placer.py:1003 OP-LM1`, `:1274 A-3` | 🔴 **UNIT-ONLY** | `tests/unit/test_a3_entry_kill_recheck.py` only |
| 10 | `max_position_value` | `position_sizer.py` | **UNIT-ONLY** | no integration reference |
| 11 | Tier multiplier | `position_sizer.py` | **UNIT-ONLY** | `test_fix133_dynamic_sizing.py` |
| 12 | FIX-133 min-lot floor | `position_sizer.py` | **UNIT-ONLY** | `test_fix133_dynamic_sizing.py` |
| 13 | M-C6 zero-multiplier SKIP | `position_sizer.py` | **UNIT-ONLY** | `test_mc6_zero_multiplier_skip.py` |
| 14 | Consecutive-losses gate | `risk_engine.py` (RE10) | **UNIT-ONLY** | appears only in the 9-way accepted set |

**Tally: 6 WIRED · 8 UNIT-ONLY · 0 wholly untested.**

### 🔴 The near-vacuous assertion (a finding in its own right)

`test_risk_rejection_max_open_positions` exists in **both** integration files
(`smoke:265`, `flow:376`) and each asserts the signal reached **any one of nine** statuses:

```
REJECTED_OPEN_POSITIONS · REJECTED_DAILY_TRADES · REJECTED_DAILY_LOSS ·
REJECTED_KILL_SWITCH · REJECTED_DUPLICATE_SYMBOL · REJECTED_CONSECUTIVE_LOSSES ·
REJECTED_SIZING_VALID · REJECTED_CAPITAL · REJECTED_STRATEGY_POSITION_LIMIT
```

So the test proves **"the risk engine rejected the signal for some reason"** — not that the
`OPEN_POSITIONS` gate works, and certainly not that `DAILY_LOSS` does. If the intended gate
silently broke and a different one happened to fire, **the test still passes**. This is the
same shape as the fixture-blindness class: green, and blind.

Compounding it: `test_full_signal_flow.py:9`'s module docstring still advertises
*"Sad path 1: Signal rejected by risk (**daily_loss_limit reached**)"* — but the implemented
test is `test_risk_rejection_max_open_positions`. **The suite documents a daily-loss scenario
it does not actually run**, which is precisely how a gap survives review.

---

## Q4 — raw-SQL money writes: **the good news**

This is genuinely well-encapsulated — the feared class is largely absent:

* `INSERT INTO fm_ledger` occurs at exactly **one** production site
  (`fund_manager.py:2365`). The only other hits are `ops_dashboard/tests/conftest.py`
  fixtures.
* `trades.gross_pnl / charges / net_pnl` are written at exactly **one** site
  (`core/state_store.py:2268`).
* `kill_switch.py:1254` issues a raw `UPDATE trades SET status = ?` — **status only, no money
  column**.

**Residual risk is therefore about the CONTRACT, not scattered writers**: one wrong value
handed to that single writer propagates everywhere, and the suite would not notice because the
column-level write itself is correct. That is exactly how E4/W10 (gross-vs-net) and X7 (a
broken lookup key) hid. **A schema-level test cannot catch it; only an end-to-end assertion on
the resulting number can.**

---

## Q5 — fixture realism (the fixture-blindness lesson)

**Mostly good, with one decisive gap.**

* ✅ The safety layers **are enabled** in the integration fixture — `daily_loss_limit_pct=0.02`
  (`conftest.py:235`) and `=0.05` (`:257`), `max_open_positions=2` (`:253`, deliberately low),
  `max_consecutive_losses=4` (`:256`). Unlike the webhook `secret_token=None` case, the values
  are not permissive-by-default.
* ✅ The sector gate's **enforce** branch is explicitly exercised
  (`test_hardening_scenarios.py:120` sets `_sector_cap_mode="enforce"`) even though production
  runs `observe` (`system_config.yaml:198`) — a good pattern worth copying.
* 🔴 **But being configured is not being exercised.** The daily-loss limit is *set* and never
  *reached*: no integration test moves `daily_realized_pnl` below `-0.02 × total`. The gate's
  rejecting branch therefore never executes in the suite. **Configured ≠ covered** — that is
  the refinement this investigation adds to the fixture-blindness lesson.

---

## Q6 — E4/W10 (NET contract) interaction

The branch `e4-w10-pnl-contract`@`ad34ee4` (UNPUSHED, awaiting Rama's risk-posture sign-off)
makes `fm_ledger.pnl_delta` **NET**. Its coverage is `tests/unit/test_e4_w10_pnl_contract.py`
(598 lines) — **unit-only; no integration test**.

**Any Q9 daily-loss assertion must be written against the NET contract, not today's
behaviour.** Concretely:
* the threshold test must drive **net** realized loss past the limit and assert the gate fires;
* it must **not** hard-code today's gross-based arithmetic, or it will bake in the very defect
  E4/W10 fixes and then fail spuriously when that branch merges;
* the safest formulation asserts the **relationship** (`gate fires once cumulative net loss
  ≤ −pct × total`) rather than a literal figure, so it is correct under both contracts and
  becomes a merge-readiness check for E4/W10.
* Related known input distortion (memory): **RMS closes pass `costs=0.0`**, so some closes
  contribute gross even after E4/W10 — worth asserting separately rather than assuming.

---

## Q7 — prioritised build plan (recommendation only; nothing built)

Ordered by **cost of silent failure**, not by ease.

| # | Assertion to build | Why it ranks here | Size |
|---|---|---|---|
| **1** | **Daily loss limit fires through the wired path — both halves.** Drive real closes until net realized loss breaches the limit; assert the next signal is rejected with **`REJECTED_DAILY_LOSS` specifically**, and that it is *not* rejected just below the threshold. | The layer that stops the account bleeding. Currently unit-only, with a known-wrong input contract. A silent failure is **unbounded daily loss** — nothing else on this list can cost as much. | M (1 test, ~80 lines, reuses `wired_system`) |
| **2** | **Tighten the two 9-way assertions** to assert the *specific* expected gate. | Cheap, and it converts two near-vacuous tests into real ones. Also stops #1 from being able to pass for the wrong reason. | S (2 edits) |
| **3** | **Kill-switch last-mile re-check, wired.** Activate the kill between approval and dispatch; assert no order reaches the broker and the reservation is released. | A TOCTOU window on the entry path: a silent failure places an entry *after* a kill — real money, and precisely the seam class S4 taught. | M |
| **4** | **Sizing floors/caps, wired**: min-lot floor, M-C6 zero-multiplier SKIP, `max_position_value`. | Silent failure sizes a position wrongly (too large = over-risk; the M-C6 case previously floored a zero multiplier to 1 lot, i.e. capital against intent). Bounded per trade, so below #1/#3. | M (batchable as one test module) |
| **5** | **Post-restart capital restoration.** Kill mid-lifecycle, rehydrate, assert reservations/used/realized match pre-restart. | Failure mode is mis-stated available capital after a restart — serious, but the 08:15 boot is off-market so there is recovery time, and `rehydrate_from_open_trades` has unit coverage. | L |

**Batching:** #2 rides with #1 (same files). #4 is one new module. #3 and #5 stand alone.

**Honestly low-value — do NOT build:** more unit tests for layers already unit-covered (they
add no wiring evidence); a test for the sector gate's enforce branch (**already wired**, #4 in
the table above); raw-SQL column-name assertions (Q4 shows a single writer for each — a
schema test would not have caught E4/W10 anyway, only an end-to-end value assertion would).

---

## Not done / not in scope

No tests built. No production change. No flag flipped (`regime.enabled`, `v3_chain_mode`,
`require_hmac`, F1 enforce all untouched). No schema change. Destructive CTs not run. E4/W10
not merged. DB consulted read-only where consulted at all.
