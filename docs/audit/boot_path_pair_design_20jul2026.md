# BOOT-PATH PAIR — DESIGN FOR REVIEW (20-Jul-2026)

**Status: DESIGN ONLY. No code, config, schema, flag, cron or test was changed.**
**For ChatGPT review before any implementation.** Both items are boot-path; the boot path is
exactly what S4 broke, and its failure mode is silent.

**Gate satisfied:** `docs/audit/monday_post_session_results_20jul2026.md` — checklist verdict
**CLEAN WITH NOTES**, no BAD item, no STOP condition.

---

## ⚠️ SCOPE CORRECTION — the pair is not the pair I was given

The queue named two items: **B1** live-seed extraction, and **B2** `startup_checks.py:703` "add the
401 tolerance". Verifying B2's premise before designing it — per the standing rule that *an audit
finding is a hypothesis* — showed the premise is **defective**, and separately Monday's own alert
stream surfaced the **real** third sibling, which is **LIVE** rather than latent.

| # | item | status |
|---|---|---|
| **B1** | live-seed extraction from `main()` | ✅ design below, unchanged from the queue |
| **B2** | `utils/startup_checks.py:703` — add `== 401` | ❌ **REFUSED — premise defective.** Do not implement. |
| **B2′** | `scripts/preflight/checks/signals.py:28` — add `== 401` | ✅ **the real sibling. LIVE.** design below |

Net effect on risk: **B2 was the wrong target and would have made a correct check worse. B2′ is a
genuine live defect that nobody had on a list.** Both conclusions are evidenced below.

---

# B1 — LIVE-SEED EXTRACTION FROM `main()`

## Current code — `main.py:2241-2259`

```python
    # SU19: paper mode uses configured paper_capital; live uses broker margins
    if args.mode == "paper":
        _startup_capital = selected_account.paper_capital
    else:
        # M-C1 (2026-07-07): the broker's net margin on a mid-day warm restart
        # ALREADY includes today's realized PnL, but rehydrate Phase 2 re-applies
        # that same PnL (fm_ledger RELEASE_USED carryover) -> the live seed would
        # double-count it (inflated reservable capital + a phantom -today_pnl drift
        # on the next sync_from_broker). Subtract today's realized-PnL carryover
        # from the seed so seed + Phase 2 == broker.net BY CONSTRUCTION. The Sigma
        # is over the EXACT same fm_ledger rows Phase 2 walks (shared helper), so
        # the cancellation is exact incl. sign (loss day -> Sigma<0 -> seed rises).
        # Cold boot: 0 closed trades -> Sigma=0 -> seed = broker.net (unchanged).
        # PAPER is UNTOUCHED: its static paper_capital seed correctly excludes PnL.
        _startup_capital = (
            broker_adapter.get_margins().net
            - fund_manager.today_realized_pnl_carryover()
        )
    fund_manager.initialize(_startup_capital)
```

**The problem:** the expression is inline in `main()`'s body with no function boundary, so **no test
can invoke it without running `main()`**. Its only guard is a whole-file regex pin
(`tests/integration/test_q9_post_restart_capital_wired.py:667-674`) which proves the *text* exists,
not that it *computes correctly*. The integration fixture is `paper_mode=True`, so every wired test
exercises the paper seed; the live seed's coverage is a **reimplementation** in
`tests/integration/test_q9_live_seed_mc1_wired.py:123-126`:

```python
def _live_seed(broker, fm_probe):
    """The live seed exactly as main.py:2255-2258 composes it, using the REAL carryover."""
    carry = fm_probe.today_realized_pnl_carryover()
    return broker.get_margins().net - carry, carry
```

That test validates a **copy**. If production's line drifts, the copy still passes — the regex pin is
the only thing linking them, and it links text, not behaviour.

## Proposed change — module-level helper **in `main.py`**

```diff
+# ─────────────────────────────────────────────────────────────────────────────
+# M-C1 (2026-07-07): the broker's net margin on a mid-day warm restart ALREADY
+# includes today's realized PnL, but rehydrate Phase 2 re-applies that same PnL
+# (fm_ledger RELEASE_USED carryover) -> the live seed would double-count it
+# (inflated reservable capital + a phantom -today_pnl drift on the next
+# sync_from_broker). Subtract today's realized-PnL carryover from the seed so
+# seed + Phase 2 == broker.net BY CONSTRUCTION. The Sigma is over the EXACT same
+# fm_ledger rows Phase 2 walks (shared helper), so the cancellation is exact incl.
+# sign (loss day -> Sigma<0 -> seed rises). Cold boot: 0 closed trades -> Sigma=0
+# -> seed = broker.net (unchanged). PAPER is UNTOUCHED: its static paper_capital
+# seed correctly excludes PnL.
+#
+# Extracted from main() 20-Jul-2026 SOLELY to make it invocable by a test. The
+# expression is byte-identical to the inline original; see the parity note in
+# docs/audit/boot_path_pair_design_20jul2026.md.
+def compute_live_startup_capital(broker_adapter, fund_manager) -> float:
+    return (
+        broker_adapter.get_margins().net
+        - fund_manager.today_realized_pnl_carryover()
+    )
+
+
 def main() -> int:
     ...
     # SU19: paper mode uses configured paper_capital; live uses broker margins
     if args.mode == "paper":
         _startup_capital = selected_account.paper_capital
     else:
-        # M-C1 (2026-07-07): ...12 lines of comment...
-        _startup_capital = (
-            broker_adapter.get_margins().net
-            - fund_manager.today_realized_pnl_carryover()
-        )
+        _startup_capital = compute_live_startup_capital(broker_adapter, fund_manager)
     fund_manager.initialize(_startup_capital)
```

**Signature:** `compute_live_startup_capital(broker_adapter, fund_manager) -> float`. Positional,
duck-typed, no annotations on the collaborators — deliberately, so a test can pass the existing
stub broker and a real `FundManager` without importing adapter types into the test module.

**Where it lives: `main.py`, module level, defined immediately above `main()`.** Three reasons, in
order of weight:

1. **The existing structural pin stays green with zero test edits.** The pin is a whole-file regex
   over `main.py` for `broker_adapter\.get_margins\(\)\.net\s*\n\s*-\s*fund_manager\.today_realized_pnl_carryover\(\)`.
   Keeping the expression *in `main.py`* — and keeping its two-line layout — satisfies it verbatim.
   Moving the helper to `capital/` or `utils/` would break it (loudly, but it is one more thing to
   get right on the boot path, for no gain).
2. **`main.py` is already import-safe and already imported by tests.** Everything executable is
   under `if __name__ == "__main__":`, and `tests/unit/test_fix164_market_open_margin_sync.py:26`
   already does `from main import _start_market_open_margin_sync_thread`. **Precedent exists; no new
   import risk is being taken.**
3. It introduces no new module, no new import edge, and no packaging change.

**Not proposed:** extracting the whole `if paper/else` seed selection. It would make both branches
testable, but it enlarges a boot-path diff for a branch (paper) that is already covered. Minimal
diff wins on the boot path.

## The new test

In `tests/integration/test_q9_live_seed_mc1_wired.py`, **replace the reimplementation with the real
function** — the whole point of the extraction:

```diff
+from main import compute_live_startup_capital
+
 def _live_seed(broker, fm_probe):
-    """The live seed exactly as main.py:2255-2258 composes it, using the REAL carryover."""
-    carry = fm_probe.today_realized_pnl_carryover()
-    return broker.get_margins().net - carry, carry
+    """The live seed AS PRODUCTION COMPOSES IT — this now calls the real function,
+    not a copy of it (extraction, 20-Jul-2026)."""
+    carry = fm_probe.today_realized_pnl_carryover()
+    return compute_live_startup_capital(broker, fm_probe), carry
```

Every assertion downstream of `_live_seed` (`test_live_seed_lands_total_exactly_on_broker_net`,
`test_without_the_subtraction_todays_pnl_is_counted_twice`,
`test_with_no_realized_pnl_the_subtraction_is_a_noop`,
`test_the_capital_picture_quantity_by_quantity`) then exercises **production code** instead of a
copy — with no new assertions written. That is the entire value of B1.

Plus one new test that could not previously exist, pinning the double-count guard *behaviourally*
rather than textually:

```python
def test_the_extracted_seed_is_the_production_expression(self, wired_system):
    """The subtraction is REAL, not textual: a non-zero carryover must move the seed
    by exactly that carryover. Before extraction this could only be regex-pinned."""
    ctx = wired_system
    broker, fm = _stub_broker(BROKER_NET), _fm_over_same_store(ctx)
    carry = fm.today_realized_pnl_carryover()
    assert carry != 0.0, "fixture must have realized P&L or this test is vacuous"
    assert compute_live_startup_capital(broker, fm) == pytest.approx(
        BROKER_NET - carry, abs=TOL)
```

Note the explicit `carry != 0.0` guard — without it the test passes trivially on a flat book, which
is precisely the vacuous-green failure mode the standing rule warns about.

**Keep the regex pin.** It now guards something narrower but still real: that the expression has not
been rewritten in place. Update only its docstring line reference (`main.py:2255`), which will move.

## Parity

**Identical, by construction.** Same expression, same two calls, same order, same operand order, no
new state, no new imports, no exception handling introduced or removed. The only runtime difference
is one additional stack frame. Paper mode is untouched — the `if args.mode == "paper"` branch is not
edited, and `fund_manager.initialize(_startup_capital)` remains the single shared call site (the
pin at `test_q9_live_seed_mc1_wired.py:382` asserting `count(...) == 1` is unaffected).

## What could go wrong

| risk | assessment |
|---|---|
| **Helper defined after its use** | Cannot fail: Python resolves module globals at call time, and `main()` runs only under `__main__`. Placing the `def` above `main()` anyway, for readability. |
| **The regex pin breaks** | It does not — the two-line expression stays in `main.py` in its two-line form. **This is the reason for the placement choice, not a happy accident.** Verify by running that test *before* pushing. |
| **Name collision in `main.py`** | `compute_live_startup_capital` does not currently appear anywhere in the repo. Confirm with a repo-wide grep at implementation time. |
| **`import main` at test-collection time has a side effect** | Ruled out: module level is imports + defs only; all execution is under `if __name__ == "__main__":`. Precedent already exists in `test_fix164`. |
| **⭐ The real risk: this is the boot path, and its failure is silent** | A wrong seed does **not** crash. It produces an inflated or deflated reservable capital that looks plausible and is only visible as a slow drift. **Mitigation: this change must land in an off-market window, with the live-seed tests run before push, and the first boot after it must be checked against `fund_manager.rehydrate_complete → total`** — on Monday that read `9875.6`, matching actual capital exactly. That single log line is the acceptance test in production. |

**Stale reference to sweep in the same commit:** `tests/unit/test_mc1_live_seed_rehydrate.py:11`
documents the seed as `main.py:2007` — already wrong today. Fix the line references in that
docstring and in `test_q9_post_restart_capital_wired.py:655` rather than leaving three different
stale numbers behind.

---

# B2 — `utils/startup_checks.py:703` — ❌ **REFUSED. Do not add the 401 tolerance.**

The queued instruction was: *"add the `== 401` tolerance (like `:808`), do NOT escalate
`scanner_unreachable` to blocking."* **The second half is right and important. The first half rests
on a false premise.**

## The premise, and why it fails

The queue treats `:703` as the same kind of check as `:808`. It is not. The `:808` fix is justified
by a rationale that is **specific to the local webhook `/health`** — its own comment says so:

> *"AB-910 §1.7 (S4, `84cee3e`) put **/health** behind the webhook secret, and this in-process
> self-check calls it UNAUTHENTICATED by design (main.py:3238) … So a 401 is an EXPECTED answer
> here, and it proves the one thing this check exists to prove: Flask is listening."*

`check_scanner_connectivity` (`utils/startup_checks.py:652`) does not call `/health`. Following
`_resolve_url` → `config/chartink_scanners.yaml`, it calls **external public Chartink screener
pages**:

```yaml
open_low_breakout_long: "https://chartink.com/screener/open-low-breakout-long"
first_pullback_long:    "https://chartink.com/screener/first-pullback-long"
vwap_bounce_long:       "https://chartink.com/screener/vwap-bounce-long"
…
```

**There is no secret in front of chartink.com, and nothing calls it unauthenticated-by-design.** A
401 from a public screener URL is not an expected answer proving the endpoint is alive — it is an
anomaly meaning Chartink began demanding authentication. Tolerating it would make the check report
`reachable=True` for a scanner that may genuinely have stopped serving, i.e. **it would degrade a
correct check into a silently-permissive one.** That is the S4 failure shape pointed the other way.

**The line is also correct in production today, measured:** Monday's boot logged
`run_all_startup_checks: OK warnings=[]`, so every Chartink URL answered 2xx. The 2xx-only test at
`:703` is not misfiring and has nothing to tolerate.

```python
# utils/startup_checks.py:703 — CORRECT AS WRITTEN, leave alone
reachable = status_code is not None and 200 <= status_code < 300
```

## What to keep from the item

The **warning-not-blocking** half stands and should be recorded as a standing constraint:
`scanner_unreachable` must remain a WARNING. Escalating it to blocking would manufacture an
S4-class risk where none exists — a transient chartink.com blip would halt a trading day. **Nothing
to implement; the current behaviour is already correct.** Close the queue item as *refused with
evidence*, not as *done*.

---

# B2′ — `scripts/preflight/checks/signals.py:28` — the REAL sibling, and it is **LIVE**

Found 20-Jul from the day's `critical_alert_*` sentinels. Full evidence in
`monday_post_session_results_20jul2026.md` §NEW-1.

**This one genuinely is `:808`'s sibling:** it calls the *same endpoint* (`127.0.0.1:5000/health`),
*unauthenticated*, with the *same 2xx-only misread*. Unlike `:703` it is not latent — it fired a
CRITICAL at 09:19:46 today on a completely healthy webhook (2,572 × HTTP 200, 1,028 signals scored,
4 trades entered), and it will fire **every trading day** from now on.

Historical proof from `logs/preflight.log` — 13 consecutive PASSes, then the AB-910 change:
```
30-Jun … 16-Jul   ✅ PASS   "webhook /health 200 (signals can arrive)"
17-Jul            🔴 FAIL   "webhook unreachable — Connection refused"   ← S4, app was dead
20-Jul            🔴 FAIL   "webhook /health HTTP 401"                   ← NEW, first trading day since AB-910
```

## Current code — `scripts/preflight/checks/signals.py:26-33`

```python
    def run(self, ctx: CheckContext) -> CheckResult:
        status, body = engine._http_get_json(WEBHOOK_HEALTH_URL)
        if status == 200:
            return self._passed("webhook /health 200 (signals can arrive)")
        if status == 0:
            return self._failed(f"webhook unreachable — Chartink signals cannot arrive "
                                f"({body.get('error', '')})")
        return self._failed(f"webhook /health HTTP {status}")
```

`engine._http_get_json` returns `(exc.code, {})` for an `HTTPError` with a non-JSON body and
`(0, {"error": …})` when genuinely unreachable — so **401 arrives as `status == 401`, and `0`
remains the true-unreachable signal.** (Confirmed by the alert text, which rendered the code.)

## Proposed change — one line, modelled on `:808`

```diff
     def run(self, ctx: CheckContext) -> CheckResult:
         status, body = engine._http_get_json(WEBHOOK_HEALTH_URL)
-        if status == 200:
-            return self._passed("webhook /health 200 (signals can arrive)")
+        # AB-910 §1.7 (S4, 84cee3e) put /health behind the webhook secret; this check
+        # calls it UNAUTHENTICATED by design, so 401 is an EXPECTED answer and proves
+        # the one thing the check exists to prove -- Flask is listening and Chartink
+        # signals can physically arrive. Mirrors utils/startup_checks.py:807-808.
+        # Anything else (404, 5xx) is still a real failure; status 0 is still unreachable.
+        if status == 200 or status == 401:
+            return self._passed(f"webhook /health HTTP {status} (signals can arrive)")
         if status == 0:
             return self._failed(f"webhook unreachable — Chartink signals cannot arrive "
                                 f"({body.get('error', '')})")
         return self._failed(f"webhook /health HTTP {status}")
```

The pass message now renders the actual code so a 401-pass is distinguishable from a 200-pass in
`logs/preflight.log` — otherwise the fix would erase the very evidence that made it findable.

## The tests

New unit tests against `WebhookResponsiveCheck.run`, monkeypatching `engine._http_get_json`
(the docstring already advertises it as *"Monkeypatched in tests"*):

```python
@pytest.mark.parametrize("status", [200, 401])
def test_webhook_responsive_passes_on_200_and_on_401(monkeypatch, status):
    """401 = /health behind the webhook secret (AB-910 §1.7), called unauthenticated
    by design. It proves Flask is listening, which is all this check asserts."""
    monkeypatch.setattr(engine, "_http_get_json", lambda *a, **k: (status, {}))
    assert WebhookResponsiveCheck().run(_ctx()).passed

@pytest.mark.parametrize("status", [404, 500, 503])
def test_webhook_responsive_still_fails_on_real_errors(monkeypatch, status):
    monkeypatch.setattr(engine, "_http_get_json", lambda *a, **k: (status, {}))
    assert not WebhookResponsiveCheck().run(_ctx()).passed

def test_webhook_responsive_still_fails_when_truly_unreachable(monkeypatch):
    """status 0 is the S4 signature — the app is down. Must stay CRITICAL."""
    monkeypatch.setattr(engine, "_http_get_json",
                        lambda *a, **k: (0, {"error": "Connection refused"}))
    assert not WebhookResponsiveCheck().run(_ctx()).passed
```

The third test is the one that matters: it pins that **the fix does not blind the check to the
actual S4 condition** (17-Jul's `Connection refused`, which this check correctly caught).

## Parity

**Behaviour is identical for every status except 401**, which is the intended and only change. The
unreachable branch (`status == 0`) and the generic-failure branch are untouched. `criticality`
stays `CRITICAL` — the check's severity is not being lowered; its *predicate* is being corrected.

## What could go wrong

| risk | assessment |
|---|---|
| **The 401 masks a genuine auth misconfiguration** | It cannot mask an *availability* problem, which is all this check claims to measure. A 401 still proves Flask is listening on :5000. Whether the secret is right is a different check's job — and is already proven daily by ~2,500 authenticated 200s. |
| **Blinding the check to S4** | Explicitly guarded by the `status == 0` test above. S4 presented as `Connection refused` → status 0 → still CRITICAL. |
| **Alert-fatigue argument used to lower severity instead** | Rejected. Do **not** downgrade `criticality` to WARN — that would hide a real future outage. Fix the predicate, keep the severity. |
| **Phase C is a passive re-sampling watch** | The orchestrator re-samples 09:15–09:20; the change is stateless and idempotent, so re-sampling is unaffected. |

**Blast radius: nil on the trading path.** Preflight is a cron and gates nothing — the system traded
normally all day *while* this check read CRITICAL. This is an observability fix, not a control fix,
which is why it is safe to ship alongside B1 but must not be conflated with it.

---

# RECOMMENDED SEQUENCING

1. **B2′ first.** One line + three tests, zero trading-path blast radius, and it stops a daily false
   CRITICAL that is actively degrading the Phase C oracle. Landing it first also means the next
   boot's preflight is a clean signal against which B1 can be judged.
2. **B1 second, in a dedicated off-market window**, tests run before push, and the first boot after
   verified against `fund_manager.rehydrate_complete → total == actual capital`.
3. **B2: nothing to implement.** Close as refused-with-evidence; record the standing constraint that
   `scanner_unreachable` stays a WARNING.

**Do not batch B1 with anything else.** It is the capital path at boot, its failure is silent, and
the whole lesson of S4 is that the boot path gets exactly one chance per day to be right.

---

*Design only — nothing implemented. Goes to ChatGPT for review before any code change.
Evidence base: `docs/audit/monday_post_session_results_20jul2026.md`.*
