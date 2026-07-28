# 11 — The absolute rupee kill ladder (drift handler + reconciler tolerances)

**Type:** capital-posture · **Status:** OPEN — **Rama's decision, nobody else's**
**Registered:** 28-Jul-2026. Promoted onto this board so it is not re-discovered a third time.

> ⛔ **This file recommends nothing**, per this board's convention. It states the options, the
> evidence for each, what is unknown, the exposure in each direction, and what would settle it.
> ⛔ **Do not re-tune these values as a side-effect of other work.** That is the specific failure
> this registration exists to prevent — it has now been noticed twice from two different directions.

## The thing being decided

Five rupee thresholds are **absolute** while ACTUAL capital is ~₹9.87k. Evidence, the full table and
the derivation live in [`../audit/capital_figure_sweep_28jul2026.md` §4](../audit/capital_figure_sweep_28jul2026.md)
— **not duplicated here**. The load-bearing summary:

| key | value | % of ACTUAL capital | gates |
|---|---:|---:|---|
| `order_reconciler.human_order_margin_tolerance` | 5,000.0 | **50.6 %** | alert only (non-escalating) |
| `drift_handler.hard_kill_threshold_rs` | 2,500.0 | **25.3 %** | **HARD kill** |
| `drift_handler.soft_kill_threshold_rs` | 1,000.0 | **10.1 %** | **SOFT kill** |
| `drift_handler.log_only_threshold_rs` | 250.0 | 2.5 % | log |
| `order_reconciler.capital_drift_tolerance` | 50.0 | 0.5 % | alert |

⚠️ **The base is a DAILY figure, not a constant** (memory: `capital-vocabulary`). The percentages
above are against the **28-Jul-2026 `fm_ledger` INIT = ₹9,872.30** (bucket `both`), read from the
live DB, not from a document. On 27-Jul it was ₹9,871.80 and on 22-Jul ₹9,838.00. **Any figure here
is only as current as its date**, which is part of what makes the item worth deciding rather than
adjusting.

**Source lines:** `config/system_config.yaml:598-601` (drift ladder), `:344`
(`human_order_margin_tolerance`). Consumed at `capital/drift_handler.py:127-129` and
`orders/order_reconciler.py:3397`.

## Why absolute is DEFENSIBLE and not simply a bug

A book-vs-broker **discrepancy** is not a risk fraction. If the ledger and the broker disagree by
₹2,500, that is a reconciliation failure of a fixed rupee size — its seriousness does not shrink
because the account is small. **Making these capital-relative is not obviously right.** That is why
this is a posture decision and not a defect.

## Why it is nonetheless worth a decision

They were calibrated against a **much larger notional account**. At today's capital the ladder reads:
- a SOFT kill at **10.1 %** of the book,
- a HARD kill at **25.3 %**,
- and an alert tolerance (`human_order_margin_tolerance`) at **50.6 %** — *half the account* —
  though note this one is an **alert-only, non-escalating** source (see below).

⭐ **The sharp edge is silent re-meaning:** if capital moves materially, every row's meaning changes
and **nothing announces it**. These are the one place a capital move is felt with no signal.

## Options (no recommendation)

- **A — Leave as-is.** Absolute values unchanged.
  *For:* discrepancy semantics are genuinely absolute; zero change risk; the ladder has never
  misfired in production. *Against:* the percentages keep drifting as capital moves, unannounced.
- **B — Re-tune the absolute numbers** to today's capital, staying absolute.
  *For:* keeps the correct semantics, restores the intended severity spacing. *Against:* needs
  re-tuning again after any material capital change — the same silent-drift problem, deferred.
- **C — Make them capital-relative (percentages).** *For:* self-maintaining. *Against:* changes
  what the threshold *means*; a small account would then tolerate only a tiny absolute discrepancy,
  which may fire on ordinary rounding/fee noise.
- **D — Hybrid:** relative with an absolute floor/ceiling. *For:* covers both failure modes.
  *Against:* the most code, and two numbers to justify instead of one.

## Constraints any change must respect

- ⛔ **Ordering is enforced at config load:** `log_only_threshold_rs < soft_kill_threshold_rs <
  hard_kill_threshold_rs` (`core/config_loader.py:1599-1620`). A re-tune violating it **fails
  startup**, which on this system means a boot that does not start.
- `consecutive_cycles_before_escalate: 3` sits in front of the ladder — escalation is not
  single-sample. Any severity reasoning must include it.
- **PARITY:** these load identically in paper and live. A change lands in both at once.
- ⚠️ **Deploy timing:** these are boot-path config. Per the deploy calendar, a config change of this
  class needs its own single-variable boot — it must not ride a sequence evening.

## What is UNKNOWN

- The **empirical distribution of observed drift** — has `log_only` (₹250) ever tripped in
  production, and how often? Without it, every option is calibration by argument. **This is
  measurable from the existing logs/DB and nobody has measured it.**
- Whether the original calibration targeted a specific notional (and which), or was inherited.

## What would SETTLE it

Measure the realised drift distribution over the live history, then choose the rung positions
against observed noise rather than against a remembered account size. That measurement is
**not blocked by anything** — it is the cheapest next step and it does not commit to any option.

## ⛔ Do not re-raise the refuted part

A neighbouring hypothesis — *"`human_order_margin_tolerance: 5000.0` widens an escalation path"* —
is **REFUTED on code evidence**, written up at
[`../audit/capital_figure_sweep_28jul2026.md` §3](../audit/capital_figure_sweep_28jul2026.md).
It is an **alert-only, non-escalating** source (`orders/order_reconciler.py:3397` adds it to an
alert tolerance; the drift *ladder* is a separate mechanism). Read §3 before re-raising it.

**Related:** memory `capital-vocabulary` · `dual-daily-loss-mechanism` · decision
[01](01_e4_w10_pnl_contract.md) (the other capital-posture item).
