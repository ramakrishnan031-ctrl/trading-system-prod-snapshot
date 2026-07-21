# F1 — What happens to the daily-loss counter after a day with no RESET_PNL

**21-Jul-2026, read-only. VERDICT: HARMLESS — the daily-loss reader is date-scoped; a missing
`RESET_PNL` cannot leak a halted day's loss into the next day. F1 is a documentation item, not a
capital-safety defect.** No recommendation on Rama's option A/B.

---

## A1 — `reset_daily_pnl()` call sites (re-confirmed from code)

Production caller: **exactly one** — `orders/eod_squareoff.py:497` (`self._fm.reset_daily_pnl()`),
inside `eod_squareoff._fire()`. `test_phase17_batch3.py:93` pins "ONLY called from
`eod_squareoff._fire()`". No cron, no second production path. So F1's premise (in-process only)
holds — a halted afternoon writes no `RESET_PNL`.

## A2 — ⭐ THE DECISIVE QUESTION: is the reader date-scoped? YES (from the actual SQL)

`StateStore.get_daily_realized_net_pnl` (`core/state_store.py:2474-2478`):

```sql
SELECT COALESCE(SUM(pnl_delta), 0.0) AS net_pnl FROM fm_ledger WHERE date = ?
```

It sums `pnl_delta` **`WHERE date = ?`** — the passed date only. There is **no running balance**.
So on day *N* the daily-loss control calls `get_daily_realized_net_pnl(N)` → sums only day-*N* rows;
day *N-1*'s rows are `WHERE date = N-1`, invisible. **A missing `RESET_PNL` on a halted day *N-1*
cannot enter day *N*'s query.** Every production caller passes `now_ist().date()` /
`today` (fund_manager.py:1303, 1531, 1627, 1760) — all date-scoped.

## A3 — Why does the reset exist at all? (the better question)

The reader has **no `entry_type` filter** (only `RELEASE_USED` and `RESET_PNL` ever carry a
non-zero `pnl_delta`), so it sees the `RESET_PNL` row. `reset_daily_pnl` (fund_manager.py:1640)
writes `pnl_delta = -old_pnl` where `old_pnl = get_daily_realized_net_pnl(today)`, so
`SUM(pnl_delta)` for **today** becomes 0 *after* the 15:17 EOD. Since the reader is date-scoped,
**tomorrow already reads 0 without it** — the reset is a **same-day, post-squareoff zeroing**
(defensive/cosmetic: any same-day evaluation after 15:17 reads 0), **not** the mechanism that
isolates days. Date-scoping is that mechanism.

**Falsifier checked — "does anything consume the ledger pnl WITHOUT date scoping?"** No. The only
other `SUM(pnl_delta)` reader, `reports/daily_trade_review.py:1034-1035`, is
`... WHERE date=? AND entry_type='RELEASE_USED'` — date-scoped *and* a per-date report (display,
not a control). There is no non-date-scoped **control** reader. So nothing falsifies "harmless."

## A4 — Real halted days in the record (empirical > reasoning)

Two trading days traded but wrote **no `RESET_PNL`** (service down before the 15:17 EOD):

| halted day | closed trades | RESET_PNL rows | that day's realized (release-only) | next trading day | next day's own realized |
|---|---|---|---|---|---|
| **2026-07-01** | 5 | **0** | −3.48 | 2026-07-02 | −3.45 (its own trades) |
| **2026-07-16** | 3 | **0** | −2.62 | 2026-07-20 | −18.29 (its own trades) |

On both next days the daily-loss figure = that day's **own** date-scoped rows; **neither inherited
the prior halted day's loss** (02-Jul did not carry −3.48; 20-Jul did not carry −2.62). Observed,
not just reasoned. (Aside: the per-date `SUM(pnl_delta)` residue is `+Σcosts` pre-E4/W10 and
**exactly 0.0 on 21-Jul** — E4/W10 corroborated a third way.)

## A5 — VERDICT and direction of failure

**HARMLESS.** Two independent confirmations (date-scoped SQL + two observed halted days) agree.
There is no defect: a missing reset leaves only the halted day's **own** counter non-zero, for that
day, with no next-day consumer (the service was down that day) — so it is **neither over-permissive
nor over-restrictive**; the next day starts on its own date-scoped rows. F1 is a **documentation
item**: option A (no cron fallback for correctness) is safe on the evidence — the reset's job is
same-day post-EOD hygiene, and its absence on a halted day harms nothing. *(No A/B recommendation —
Rama's call; this informs it.)*

**What would overturn HARMLESS:** a **control** (not a report) reading the ledger pnl without a
date filter — a running balance the reset corrects. Verified absent. If one is ever added, this
verdict must be re-checked.

## Relevance to §B (the midnight day-floor hazard)

Because the reader is date-scoped (A2), §B's blast radius is bounded to **one boot's capital seed**,
not a persistent daily-loss corruption — the §B4 dependency, resolved. See
`docs/decisions/DESIGN_midnight_day_floor.md`.

*Read-only throughout (`mode=ro`); no code, no service touched; no halted day created.*
