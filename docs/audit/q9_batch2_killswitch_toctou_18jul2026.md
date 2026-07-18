# Q9 batch 2 — the kill switch's last-mile re-check, wired (18-Jul-2026)

Q9 #3 closed: the TOCTOU window between the early kill check and broker dispatch is now
proven closed **through the real path**, positive and negative, with the gate order verified
and an independent planted-break proof.

**Deployed:** tag **`deploy-18jul-q9-batch2` → `4176c3f`**. PC == VM bare == tag.
**Test-only — zero production files changed; no runtime behaviour change.**

---

## LEAD SUMMARY

| Item | Outcome |
|---|---|
| **§A — the two daily-loss percentages** | ✅ **FIXTURE-ONLY. No finding, no action.** Production feeds *both* halves from one key at 3%; the 2%/5% split is the integration fixture's own constructor arguments. The recorded "single source" principle **holds**. |
| **§B — kill-switch last-mile re-check** | ✅ **WIRED-PROVEN.** The re-check exists (no STOP), the window was mapped in real code, the kill is armed *inside* it via a delegating wrapper on a real call, nothing reaches the broker, the reservation is released (no capital leak), parity is structural, and the test **bites**. |

**No STOP-and-report branch was triggered** (§A2, §B1c, §B5, §B6 all came back clean) — but each
was checked against evidence rather than assumed.

---

## §A — the two daily-loss percentages: **FIXTURE-ONLY**

Batch 1 observed the fixture running a 2% post-close limit against a 5% pre-trade limit, where
the recorded principle is a single 3% source. Traced to production:

| Half | Production argument | Source |
|---|---|---|
| POST-CLOSE breach (`fund_manager.py:1279-1290`) | `daily_loss_limit_pct=app_config.system.risk.daily_loss_limit_pct` | `main.py:2231` |
| PRE-TRADE gate (`risk_engine.py:598-604`) | `daily_loss_limit_pct=risk_cfg.daily_loss_limit_pct` | `main.py:2369` |

and `risk_cfg = app_config.system.risk` (`main.py:2357`) — **the same object, the same key**.
Production sets `daily_loss_limit_pct: 0.03`, annotated *"SOLE daily-loss authority (3% of
capital), PERMANENT"* (`config/system_config.yaml:193`).

**⇒ In live, both halves fire off ONE key at 3%.** The 2%/5% divergence exists only because the
integration fixture passes different literals to the two constructors (`conftest.py:235` and
`:257`). Neither half falls back to a hard-coded default in production.

**What the breach actually does** (this decides whether it is a warning or a halt):
`_make_daily_loss_cb` (`main.py:757-800`) logs CRITICAL → notifies → runs EOD `fire_now`
(closing positions) → **`kill_switch.soft_kill`**. It **halts**; it is not notify-only. So the
post-close half is the halting mechanism and the pre-trade gate is the admission guard, both
keyed to the same 3%.

**Verdict: FIXTURE-ONLY — recorded, no action.** The recorded single-source principle stands.
(A separate, smaller observation for whoever next touches the fixture: giving the two halves
*different* percentages there is convenient for testing — batch 1 exploited it to get a clean
negative case — but it does not mirror production, so no test should infer production
semantics from the split.)

---

## §B1 — the investigation (done before any test code)

### a/b. Every kill-switch check on the signal → dispatch path, in execution order

| # | Site | Scope | Role |
|---|---|---|---|
| 1 | `signals/webhook_receiver.py:512` | `is_active()` | admission — reject at the webhook |
| 2 | `signals/signal_processor.py:684` (+ `:1149/:1749/:1915/:2081/:2201`) | `is_active("entry")` | pipeline gates |
| 3 | `capital/risk_engine.py:270` | `is_active()` | RE5 check #1 (`KILL_SWITCH`) |
| 4 | **`orders/order_placer.py:1004` — OP-LM1** | `is_active("entry")` | last-mile, **before** the pre-submit work |
| 5 | **`orders/order_placer.py:1289` — A-3** | `is_active("entry")` | **THE TRUE LAST MILE** — immediately before `self._engine.execute(...)` |

### c. Does a last-mile re-check exist? **YES — no STOP.**

Two, in fact. A-3 is the true last mile: it sits immediately before the broker call, **inside**
the retry loop (so a kill arriving during a 429 backoff is caught on the retry) and is raised
**outside** the `try` so the loop's own `OrderRejectedError` handler cannot catch-and-retry it.
Its in-code comment documents exactly the window OP-LM1 leaves open.

### d. The real calls strictly between OP-LM1 and A-3 — **probed, not assumed**

Source order suggests `_fetch_ltp` (slippage read, drift re-quote) and `_check_liquidity`.
**Empirically, under this fixture's paper path, `_fetch_ltp` is never called** — a probe
recorded `_fetch_ltp CALLED 0 times`, so it is **not a usable arming seam** despite appearing
in the window. The call that *does* execute there is:

```
orders/order_placer.py:1016   self._om.update_trade_status(trade_id, "PENDING")
```

which is strictly after OP-LM1 (`:1004`) and strictly before A-3 (`:1289`). That is the seam
the test uses.

### e. SOFT vs HARD, entry vs exit (`capital/kill_switch.py:447-459`)

`is_active("entry")` → True for **SOFT_KILL or HARD_KILL**; `is_active("exit")` → True **only
for HARD_KILL** (a soft kill must still allow exits). Both last-mile checks use `"entry"`, so
they block **entries** under either kill and never block an exit under a soft kill — the
correct asymmetry.

### f. Earlier gates that could mask the kill gate

Webhook admission, the six signal-processor checks, and RE5's `KILL_SWITCH` all run **before**
`order_placer.place()`. Because the test arms the kill *inside* `place()`, every one of them has
already passed — reaching `place()` at all is the proof. No earlier gate can fire.

---

## §B2–B4 — positive, negative, capital

**POSITIVE** (`test_kill_armed_inside_toctou_window_blocks_before_broker`): a delegating
wrapper on `update_trade_status` arms a **soft kill** on the `PENDING` transition and then calls
through — it does not replace behaviour. Asserted:

* **nothing reached the broker** — against a delegating spy on `adapter.place_order` (the real
  boundary), corroborated by the absence of any `ENTRY` row in `orders`;
* the rejection names the kill switch **specifically**, and specifically the
  **`kill_switch_active_last_mile_presubmit`** message — i.e. **A-3**, not OP-LM1. No multi-way
  accept anywhere;
* the arming seam **actually fired** (`armed["armed"]`), so the test cannot pass vacuously.

**NEGATIVE** (`test_same_signal_dispatches_when_kill_is_clear`): the same symbol, qty and prices
with the kill clear **do** reach the broker. Without this half the test could not distinguish a
working re-check from one that blocks everything.

**DISTINGUISHER** (`test_kill_armed_before_place_is_caught_by_the_earlier_check`): arming the
kill *before* `place()` is caught by **OP-LM1** (`kill_switch_active_last_mile`, no
`presubmit`). This proves the two sites are genuinely distinct, which is what makes the positive
test's `_presubmit` assertion meaningful rather than incidental.

**Capital picture** (standing rule A) captured before and after: total / available / reserved /
used / realized P&L.

---

## §B5 — the orphan-reservation check: **no leak**

The kill fires *after* `fund_manager.reserve` but *before* dispatch, so the reservation must be
released or capital leaks on every kill until restart or reconciliation.

**Asserted and passing:** after a last-mile block, `intraday_reserved` and `intraday_avail`
return to their pre-signal values, `intraday_used` is unchanged, and realized P&L is unmoved.
Mechanism: `_handle_placement_failure` → `self._fm.release(reservation_id, …)`
(`order_placer.py`, within the failure handler) — the same path OP-LM1 uses. **No STOP.**

---

## §B6 — parity: **shared check, structurally upstream of the mode branch**

`order_placer.place()` contains **no paper/live branch** between the checks and dispatch —
`self._mode` appears only in alert titles (`:691`, `:710`, `:964`, `:1130`). The paper/live
divergence lives **downstream**, inside `broker/zerodha_adapter.py` (`paper_mode=True` →
`_paper_place_order`, `:348`, ZA10).

**⇒ One shared check, not duplicated per mode.** Proving the block in the fixture's paper mode
therefore proves it for live, and there is no second copy that could drift — which is precisely
what caused the P1 bug on the duplicated `/health` auth path. **No STOP.**

---

## §B7 — ⭐ PROOF THAT IT BITES

| State | Result |
|---|---|
| A-3 (`order_placer.py:1289`) neutered | ❌ `assert ['RELIANCE'] == []` — *"AN ORDER REACHED THE BROKER AFTER THE KILL WAS ARMED MID-FLIGHT … the last-mile re-check did not hold"* |
| restored | ✅ 3 passed; `orders/` and `capital/` show **0** modified files |

The assertion order is deliberate: the broker record is asserted **before** the exception, so a
broken re-check fails with *"an order reached the broker"* rather than the far less informative
`DID NOT RAISE`. (An earlier draft failed with `DID NOT RAISE`; it was restructured so the
failure names what actually went wrong.)

Note the break is meaningful precisely because OP-LM1 cannot cover for it: the kill is armed
*after* OP-LM1 has already passed, so with A-3 neutered the order genuinely reaches the broker.

---

## §B8 — Q9 coverage-matrix row (kill switch)

| Field | Value |
|---|---|
| Layer | Kill switch — last-mile re-check (entry path, TOCTOU) |
| Class | **WIRED** (was UNIT-ONLY) |
| Positive proof | kill armed inside the (OP-LM1, A-3) window → no broker call, no ENTRY row |
| Negative proof | same signal, kill clear → order reaches the broker |
| Planted-break proof | A-3 neutered → `['RELIANCE'] == []` fails; restored → green |
| Gate order verified | ✅ arms *inside* `place()`, so webhook / signal-processor / RE5 have all already passed; `_presubmit` message proves A-3 and not OP-LM1 |
| Production path verified | ✅ shared check upstream of the paper/live branch (`order_placer` has no mode branch; divergence is in `zerodha_adapter:348`) |

*(Back-filling these fields for the other layers is a separate later item and was not done.)*

---

## Regression + deploy

**Full suite (NOT scoped): 11 failed, 4942 passed, 5 skipped (781s).** New-failure set versus
the 18-Jul base baseline is **EMPTY ⇒ ZERO ATTRIBUTABLE**; the 11 are the known PC-env /
Saturday calendar-gated set (`daily_trade_review`, `order_placer_fix061` ×4, `fix181`,
`phase17_batch2`, heavy `test_main.py` classes). **Counts reconcile exactly: 4939 (batch 1) + 3
new tests = 4942.** **No integration failures** (full integration suite 36 passed).

**Deploy:** backup `pre_deploy_q9b2_20260718.db` — **verified sound, not merely present**:
`quick_check=ok`, schema v44, 361 trades. Pushed → post-receive checkout OK.
**Verified: PC == VM bare == tag == `4176c3f`**; the new test is present on the VM and **passes
there (3 passed)**. Schema **v44 unchanged**, `integrity_check=ok`, **0 FK violations**;
services correct (`trading-system` inactive as expected on a down Saturday, `alert-watcher` and
`gui-dashboard` active); **kill-switch state INACTIVE** (the test's soft kills are confined to
the fixture's tmp_path DB and never touched the live one).

## Not in scope / not done

HARD-kill order cancellation and flatten-worker verification (separate, larger item; the first
live hard kill is its real test). Q9 #4 sizing floors/caps, Q9 #5 post-restart capital
restoration, and the (C) capital-invariant test. No production code change, no flag flip, no
schema change, destructive CTs not run, E4/W10 not merged.
