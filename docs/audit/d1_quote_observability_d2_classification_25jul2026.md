# §D — Live-path quote observability (D1) + the deferred classification items (D2)

**25-Jul-2026. READ-ONLY: no code changed.** Both items were queued low-priority.
D2 is **CLOSED as superseded**, with production evidence. D1 is **REPORTED, not built** —
its premise needed correcting first.

---

## D2 — The three deferred free-text classification items: CLOSED AS SUPERSEDED

**What was deferred (18-Jul).** Point 6 of the free-text classification rule
(`docs/audit/daily_report_classification_fix_18jul2026.md`): three `event_type` sites used
`"CRITICAL" in event_type`, which counts the routine `KILL_AUTO_CLEARED`. They were
*reported, not changed* on the eve of a live boot because the intent was ambiguous rather
than the misclassification demonstrable.

**What happened since.** `5c70def` — *"fix(daily-report): C1 — routine KILL_AUTO_CLEARED
no longer counts as CRITICAL"* — **is merged into `main`** (`git merge-base --is-ancestor`
confirms). It did two things the 18-Jul rule prescribed:

1. **Collapsed three sites into one classifier.** `grep` for the pattern across current
   non-test source returns exactly **one** site: `reports/daily_report.py:272`, inside the
   single helper `_is_critical_event()`. "One classifier, not many" — satisfied.
2. **Moved the exclusion onto a structured set**, not a sentence:
   `_ROUTINE_SYSTEM_EVENT_TYPES = frozenset({"KILL_AUTO_CLEARED"})`, checked before the
   family match.

**Is the residual `"CRITICAL" in et or "KILL" in et` still a violation? No.** The rule
forbids branching on **free text where a structured status exists**; it explicitly permits
*"prefix/family matching on the enum — that is contractual."* `event_type` is
system-generated structured data, not a human sentence. The residual match is a deliberate,
commented, forward-compatible family match that keeps a genuine future `KILL_*`/`CRITICAL_*`
event counting — i.e. it is the **anti-vacuity** guard, not the defect.

**Premise verified against production** (VM, `mode=ro&immutable=1`):

```
select event_type, count(*) from system_events
 where upper(event_type) like '%CRITICAL%' or like '%KILL%'
  →  [('KILL_AUTO_CLEARED', 23)]        -- exactly one, as the code comment claims
```

The code comment asserts *"the only KILL/CRITICAL event_type ever written is the routine
KILL_AUTO_CLEARED"*. **Measured true.** ⇒ the fix removes a false daily
`CRITICAL Count >= 1`, and removes nothing real.

**Verdict: CLOSED — superseded by `5c70def`. No work to do.** Per §D2's own instruction,
closing as stale rather than re-doing it.

---

## D1 — Live-path quote observability: PREMISE CORRECTED, then REPORTED

**D1 asked: "what is unobservable today, and the minimal add."** The first half needed
correcting before the second could be answered.

### The live quote path is already instrumented

`ZerodhaAdapter.get_quote` emits **structured INFO** on both sides of every call:

```
zerodha_adapter.py:1574  log.info("get_quote call_start", extra={method, symbol_count})
zerodha_adapter.py:1587  log.info("get_quote call_end",   extra={method, duration_ms, result_summary})
zerodha_adapter.py:1639  (live branch — same shape)
```

plus rate-limiter `acquire()` on the `quote` category (`:1554`/`:1594`) and 429
attempt-reset (`:1561`/`:1606`), with broker exceptions translated and tagged
`"get_quote"` (`:1559`/`:1602`). Latency, fan-out size, outcome and throttling are **all
recorded, at INFO, in production** — not DEBUG, so they are actually present in the live
log stream.

Production consumers of the path are `EntryGate` (pre-placement), `order_monitor.py:323`,
`smart_tgt._startup_ltp_check`, `order_reconciler` (M-O1), `signal_processor` (M-S1 fresh
re-anchor).

⇒ **"Unobservable" is the wrong word. The correct gap is: not AGGREGATED.**

### The real gap

There is **no queryable store** of quote latency — `grep` for latency/`duration_ms`
persistence in `core/state_store.py` and `monitoring/` returns nothing for quotes. The data
exists only as log lines: greppable per-incident, but not trendable, and no threshold or
alert reads it. Contrast `screener_results.latencies`, which held **287,600** recorded step
latencies and is precisely what let M-S3 be settled as INERT by measurement rather than
argument (25-Jul). The quote path has no such table.

### Recommendation: REPORT, do not build (this session)

D1's own instruction is *"build only if small and off the critical path; otherwise
report."* It fails both tests:

- **Not off the critical path.** `get_quote` is called by `EntryGate` immediately before
  placement, with capital already reserved — the same hot path M-A2 was just bounded for.
  Adding a synchronous write there is the exact shape of defect this batch spent §A fixing.
  An async/batched recorder avoids that, but then it is not small.
- **Not small, and it is a design decision, not a chore.** Choosing sink (new analytics
  table vs `system_metrics`), cadence, retention and whether anything alerts on it is a
  metrics-design task. `docs/audit/batch1_done_16jul2026.md:87` already classified the
  sibling item (P5-2, B-1 observability) exactly this way: *"a metrics design task, not a
  chore, and it gates a Rama decision."*
- **No demonstrated problem.** Unlike M-S3 — where latencies existed and proved a defect
  inert — nothing indicates quote latency is hurting anything. Building the aggregation
  first and looking second is the wrong order.

**If Rama wants it later, the minimal honest version is:** reuse the M-S3 pattern — an
append-only latency column/table written by the *already-existing* `call_end` site,
batched off the request thread, with no gate reading it initially. Sized: small-to-medium,
one new table, one writer, zero control-flow change. **NOT BUILT.**

---

## Status

| Item | Verdict |
|---|---|
| **D2** — three free-text `event_type` sites | **CLOSED** — superseded by `5c70def` (merged); premise re-verified in production |
| **D1** — live-path quote observability | **REPORTED, NOT BUILT** — path is instrumented; gap is aggregation; on the critical path + a metrics-design call for Rama |
