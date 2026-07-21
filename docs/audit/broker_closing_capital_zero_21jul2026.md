# `[3_Capital]` "Closing Capital — Broker = ₹0" — trace + verdict (21-Jul-2026)

**Read-only investigation. Nothing fixed.** Tonight's `daily_report_2026-07-21.xlsx` `[3_Capital]` sheet
showed `Closing Capital — Broker = ₹0`, `Reconcile Variance = −9838.48`, `Reconcile Status = REVIEW`,
while the system-side capital was exact (opening 9857.30 → sys-close 9838.48 = −18.82, the day's net).

## Verdict (A5): **OBSERVABILITY GAP — recurring report-layer mislabel, NIL control-path blast radius.**
The field is a **misnomer**: it is not the broker's closing capital and never was a broker call. It is the
last `fm_ledger` row's bucket-scoped `balance_after`, which is the `RESET_PNL` counter-entry's hardcoded
`0.0` on most trading days. Nothing in any control/gate/sizing path consumes it (evidenced below). It is a
genuine report defect worth fixing — the `[3_Capital]` reconcile is uninformative and the label misleads —
but it is **not** a capital-integrity or control issue.

---

## A1 — RECURRING, not a one-off (and a correction I owe)
`Closing Capital — Broker` across every July daily report:

| ₹0 (REVIEW) | non-zero |
|---|---|
| 07-02, 03, 06, 07, 08, 09, 10, 13, 14, 15, **20, 21** — **11 days** | 07-01 (6873.49), 07-16 (6911.91), 07-17 (9875.6, the only **MATCH**) |

**₹0 on 11 of 15 days; REVIEW on 14 of 15.** So it is a standing daily state, **not** caused by today's
11:57 restart or the 16:00 self-exit.

⚠️ **Correction:** my own earlier note (and this evening's report) said *"Monday captured ₹9,858.63."*
**That was wrong — Monday's `[3_Capital]` cell was ₹0.** I had conflated the 20-Jul post-session doc's
**broker-account delta** (₹9,858.63, derived from `trades.gross_pnl`) with this report cell. Same shape as
the stale-constraint pattern (see `attribution_gloss_sweep_21jul2026.md` §5): a figure carried across
without re-checking which artifact it came from. The MATCH day (07-17) is coincidental — the S4 boot-outage
day had no trades, so the last ledger row was the `INIT` row and `balance_after` == opening == running.

## A2 — source, and both hypotheses REFUTED
`reports/daily_report.py:193-195`:
```python
sorted_ledger = sorted(fm_ledger, key=lambda r: r.get("ts", ""))
closing_capital_broker = sorted_ledger[-1].get("balance_after", opening_capital)
```
It is the **last fm_ledger row's `balance_after`**, read at report-generation time (the 16:05 `daily_report`
cron) from the persisted ledger. **No live broker call, no broker-funds snapshot, no token dependency.**

- **Hypothesis "broker-snapshot timing vs the 16:00 self-exit" — REFUTED.** There is no broker snapshot on
  this path.
- **Hypothesis "interaction with the 11:57 restart" — REFUTED.** 20-Jul (no restart) shows the same ₹0.

**Mechanism of the ₹0:** `reset_daily_pnl()` (`fund_manager.py:1632-1641`) writes the `RESET_PNL` row at
15:17 with `balance_after=0.0`, `bucket='both'`. On any day with no capital op after 15:17 (entry window
closes 15:00, book flat post-squareoff → the norm), `RESET_PNL` is the last row, so `sorted_ledger[-1]`
carries `balance_after=0.0`. Verified on the live ledger: 21-Jul last row = id 9283 `RESET_PNL` @15:17:08
`balance_after=0.0`; 20-Jul last row = id 9212 `RESET_PNL` @15:17:05 `balance_after=0.0`. The non-zero days
(07-01/16 ≈ 6900) are days whose last row was an intraday-bucket mutation — its `balance_after` is the
**intraday-bucket available** (~70% of ~9800), still not "broker closing capital."

## A3 — does anything CONSUME it? **No — evidenced, not asserted.**
- **The report field itself:** `closing_capital_broker` appears only in `reports/daily_report.py` (+ its
  tests). No control, gate, sizing, or alert reads it. Report display only.
- **Capital-relative thresholds** (daily-loss `pct × capital`, `max_position_value_pct × capital`) read
  **opening** capital via `state_store.get_day_opening_capital` (`state_store.py:2524`) = the **INIT** row's
  `balance_after` (9857.30 today), *not* the closing value — deliberately opening-basis so thresholds don't
  swing intraday. **Unaffected by the ₹0.**
- **The one non-report sibling reader** — `eod_broker_reconcile._local_capital_snapshot` (`:450`) reads the
  same last-row `balance_after` as `local.total` (→ 0.0), used in the margin dimension (`:177`,
  `|broker.margin_net − local.total| > tol`). It **cannot cause a control action or even an alert**, for two
  independent reasons: (1) `margin_reliable_now()` is **False at the 15:58 run** (reliable only inside
  09:00–15:45) → `margin_status = NOT_CHECKED`, so `local.total` is never compared; and (2)
  `config/system_config.yaml:303` `eod_reconcile.authoritative: false` — SHADOW, *"alerts INFO, gates
  nothing."* The ledger 3-balance invariant is a **separate** check (`invariant_ok` hardcoded `True` here;
  the real audit is CHECK7 in-session off live FM state), so ₹0 does not flip it either.
- **GUI** (`ops_dashboard/backend/readers/db_reader.py:317`, `SUM(balance_after)`): read-only loopback
  dashboard, display only.

## A4 — does the REVIEW status have downstream effects? **No.**
`Reconcile Status` ("MATCH"/"REVIEW", `daily_report.py:1212`) appears only in `daily_report.py`, which
*writes* the xlsx cell. Nothing reads, alerts on, or gates on it. It is a human-review display value. (The
Control Tower monitoring layer is separate and does not read the daily-report xlsx.)

---

## Direction / recommendation (NOT done here)
A real but cosmetic report defect: `Closing Capital — Broker` should either read a genuine broker-funds
figure (none is persisted per-day today — the boot `get_margins().net` is not snapshotted), or the field +
its `[3_Capital]` reconcile should be relabelled/removed rather than derived from the last ledger row. Until
then the near-permanent daily false-REVIEW makes the `[3_Capital]` reconcile status uninformative — the same
class as C1's daily false-CRITICAL and B2′'s daily false-401, and it should be queued with them, not fixed
tonight (read-only batch, and it gates nothing so there is no urgency).

*Read-only throughout (`mode=ro`); no code, config, schema, or state changed; the service was not touched.*
