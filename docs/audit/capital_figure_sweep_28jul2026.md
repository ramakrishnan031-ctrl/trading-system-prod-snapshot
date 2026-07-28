# Capital-figure sweep — stale bases, and the absolute thresholds (28-Jul-2026)

**Scope.** Every place a capital figure is quoted or a rupee threshold is calibrated: docs, operator
artefacts, decision briefs, config, code. Asked of each: **decorative, or does something DEPEND on
it?** A stale figure in prose is untidy; a stale figure under a **threshold or a precondition** is a
defect.

> ## ⭐ THE PROPERTY, NOT THE NUMBER
> **ACTUAL CAPITAL IS A DAILY MEASUREMENT.** It is the broker's opening balance, written as the day's
> `INIT` row in `fm_ledger`, and it moved on **every single trading day** observed:
>
> | 19-Jul | 21-Jul | 22-Jul | 23-Jul | 24-Jul | 27-Jul | **28-Jul** |
> |---|---|---|---|---|---|---|
> | 9,875.60 | 9,858.73 | 9,838.00 | 9,852.30 | 9,865.30 | 9,871.80 | **9,872.30** |
>
> Read it, do not remember it:
> ```sql
> SELECT amount FROM fm_ledger WHERE entry_type='INIT' ORDER BY date DESC LIMIT 1;
> ```
> Or, cheapest of all, off the boot log: `fund_manager.rehydrate_complete → total`.
>
> ⛔ **The fix is never to re-pin the number to today's value** — that restarts the same decay. Treat
> ~Rs 9.8k as an order of magnitude, never as a value to compute against.

---

## 1. CODE — ✅ capital is READ, never assumed

Verified independently (not carried over from the 27-Jul pass):

| site | finding |
|---|---|
| `capital/fund_manager.py:351` | `self._total: float = 0.0` — initialises to zero, **no default capital** |
| `capital/fund_manager.py:440` | set from `broker_balance` in `initialize()` |
| `capital/fund_manager.py:1393` | set from `broker_balance` in `sync_from_broker()` |
| `core/state_store.py:2566` | `get_day_opening_capital()` reads the `fm_ledger` INIT row |

**Only two assignment sites, both from the broker. No hardcoded capital, no fallback default.** This
is *not* the `product="MIS"` / `_CONFIG_FILES` shape — there is nothing to un-bake.

The single capital literal in code is `fm.initialize(broker_balance=500000.0)` at
`capital/fund_manager.py:282`, inside the class **docstring usage example**. Decorative. (Worth
knowing it is 50× real capital, so it must never be read as a production figure.)

## 2. DOCS — the class a date does not freeze

The 27-Jul pass checked the operator artefacts and the **dated** audit reports and concluded
"nothing load-bearing." ⭐ **The class it missed is UNDATED, PRESENT-TENSE authorities.**

| where | verdict | action |
|---|---|---|
| `docs/SYSTEM_MAP.md:76` | ⚠️ **load-bearing** — pinned "ACTUAL CAPITAL Rs 9,875.60" as a *PERMANENT doc rule*; the line every other doc cites | ✅ **FIXED** — now states the property + the query, no number |
| `docs/decisions/02_d1_concentration_sizing.md:8` | ⚠️ **load-bearing** — D1 is an **OPEN** decision and its rupee ceiling is what Rama would decide against | ✅ **FIXED** — cap restated as a percentage; rupee figure dated and marked as moving |
| `docs/audit/slice25_execution_plan_27jul2026.md:244` | ⚠️ **load-bearing** — "caps *reviewed against* ₹9,875.60" is a **review basis**; a stale base makes the review stale | ✅ **FIXED** — now says re-read on the day of the review |
| `docs/audit/slice25_execution_plan_27jul2026.md:241` | decorative (the *70 %* is the claim) | ✅ dated |
| `docs/audit/MONDAY_POST_SESSION_CHECKLIST.md:29` | decorative — the doc **is** dated in its header; but its generic title invites reuse | ✅ dated inline + a re-read warning |
| ~10 dated audit reports (20-Jul, 21-Jul, 18-Jul, 25-Jul …) | ✅ correctly **FROZEN** — a fact about their date, not a claim about today | none |
| `docs/T2_RUNBOOK_29-JUL.txt`, `TUESDAY_28-JUL_CARD.txt` | ✅ quote **no** capital figure at all | none |

⭐ **No conclusion moved.** 3 % × 9,872.30 = **Rs 296.17** against 3 % × 9,875.60 = Rs 296.27 — ten
paise apart. These were defects in **form**, not in outcome. ⚠️ That is luck, not design: a deposit
or a bad week moves the base, and every one of the five would then have been wrong *and* consequential
at the same moment.

## 3. ⛔ THE HYPOTHESIS THAT LOOKS RIGHT AND IS **REFUTED** — read this before re-raising it

**The claim that fails:** *"`human_order_margin_tolerance: 5000.0` — 50.6 % of actual capital — widens
the capital-drift band above every drift_handler rung, so on any day a human order is present the
₹250 / ₹1,000 / ₹2,500 kill ladder cannot fire."*

It is a reasonable-looking chain. `order_reconciler.py:3397` adds the ₹5,000 allowance to
`effective_tolerance`, and the `return None` at `:3399` sits **above** the `CapitalDriftDetected`
publish — so the event that drift_handler consumes genuinely is suppressed on that path.

**Why it is wrong:** `capital/drift_handler.py:65-69`

```python
_ESCALATING_SOURCES = frozenset({
    "fund_manager", "fund_manager_self_check", "fund_manager_bucket_overflow",
})
```

**`order_reconciler` is not in it.** That publisher was never a kill path — it is alert-only.

> ⚠️ **CORRECTION (28-Jul, same day).** An earlier draft of this section corroborated the above with a
> config comment — *"Informational only — kill escalation is governed separately by drift_handler
> thresholds"* — cited as `config/system_config.yaml:345`. **Both halves were wrong.** The comment is
> in `core/config_loader.py:730-735`, and it documents **`capital_drift_alert_interval_sec`**
> (TASK-11, alert cadence) — a *different key*. It says nothing about
> `human_order_margin_tolerance` (FIX-182, `:725-729`), whose own comment makes no escalation claim.
> ⭐ **The refutation is unaffected and is single-sourced on CODE**, which is the stronger evidence
> anyway: `_ESCALATING_SOURCES` is a `frozenset` literal read directly by the handler. A comment
> could be stale; the frozenset is what executes. Recorded rather than silently edited — a citation
> that survives into a second document is how `:703` propagated on 20-Jul.

The ladder is fed by `fund_manager.sync_from_broker`,
which publishes on **any** total change `> ₹1.0` (`fund_manager.py:1423`), **un-gated by any
tolerance**, plus the BL-3 self-check and the bucket-overflow guard.

⇒ **The ₹5,000 allowance costs alert sensitivity on one non-escalating source. It does not cost kill
coverage.** Nothing to fix. Do not "harden" this.

## 4. ⚠️ RECORDED, NOT A DEFECT — the absolute thresholds

Five rupee thresholds are **absolute** while capital is ~Rs 9.87k:

| key | value | % of capital (28-Jul) | gates |
|---|---:|---:|---|
| `order_reconciler.human_order_margin_tolerance` | 5,000.0 | **50.6 %** | alert only (non-escalating source) |
| `drift_handler.hard_kill_threshold_rs` | 2,500.0 | 25.3 % | **HARD kill** |
| `drift_handler.soft_kill_threshold_rs` | 1,000.0 | 10.1 % | **SOFT kill** |
| `drift_handler.log_only_threshold_rs` | 250.0 | 2.5 % | log |
| `order_reconciler.capital_drift_tolerance` | 50.0 | 0.5 % | alert |

Being absolute is **correct** — a book-vs-broker *discrepancy* is not a risk fraction. But they were
calibrated against a much larger notional account. ⭐ **If capital ever changes materially these
change meaning silently**; they are the one place a capital move would be felt with nothing saying so.
Not changed here: re-tuning a kill ladder is a capital-posture decision, not a sweep item.

## 5. Adjacent finding — the invariant nothing checks

While tracing the deployed tree for the `.pyc` sweep: **there is no deployed-tree-vs-HEAD integrity
check anywhere in `scripts/`, `deploy/` or `ops/`.** The "deployed tree equals HEAD" invariant — the
one the 28-Jul cherry-pick decision was explicitly made to protect — is held by convention and
verified by nothing. Recorded here because it is the same shape as this report's subject: a property
everyone relies on and nobody reads. See `docs/audit/pyc_orphan_sweep_28jul2026.md`.

Related: memory `capital-vocabulary` · `dual-daily-loss-mechanism` · `feedback-no-fixed-test-baseline`
