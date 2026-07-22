# Pre-15-Jun Large Orphans — Thread 2 (22-Jul-2026)

**Scope.** The second open thread from `orphan_adoption_forensics_22jul2026.md`
(§B3): journald recorded ~46,078 `CHECK2 ORPHAN_ADOPTION … no local trade found`
lines across **10-14 Jun**, including **HGS qty 107 @455.95 (~₹48.8k)** and
**AFCONS qty 150 @334.45 (~₹50.2k)** — each ≈5× the ~₹9.9k account capital. Are
these real unprotected system positions, a stale broker read, or a
config/pre-production artifact?

**Method.** Read-only: local `git` config history + a `sqlite3 -readonly` SELECT
batch against the live VM main DB. Nothing changed, placed, or restarted. Findings
only — no fix, no design.

---

## TL;DR — undetermined *by design*, and here is exactly why

**The large orphans predate the database.** The live DB begins **12-Jun** (the
live-switch re-launch); `trades` and `reconciliation_log` begin **15-Jun 11:34**;
HGS's orphan is **10-Jun** — before any of it. So **no `trades`/`orders`/
`reconciliation_log` row for HGS/AFCONS can ever have existed in this DB**, and none
does (verified). This is the honest, complete answer: *cannot be determined beyond
the DB horizon.* The remaining questions resolve as far as the evidence allows:

- **Capital was NOT larger in June (B4).** `config/accounts.csv` sets `LFL836`
  (the system-only Zerodha account) to **₹10,000** at go-live (`39481f3`, 12-Jun),
  ≈ the live ₹9,875.60. The ₹49-50k exposure is genuinely ~5× capital, not a
  large-account artifact.
- **Leverage was NOT configured differently (B2).** `system_config.yaml`
  `leverage_map: INTRADAY 5.0` has been **constant since v2 foundation** (`bcf03b5`);
  no June change. So the 5× magnitude is a **sizing OUTCOME**, not a config change.
- **The magnitude fits the system's own 5× sizing** — `qty 107 ≈ 50000/455.95`,
  `qty 150 ≈ 50000/334.45`. This is exactly what PositionSizer's **L4 margin cap**
  (`tradable_balance × leverage(5) / entry_price`) produces when it is the binding
  layer and no capital-relative cap intervenes. **Consistent with, but NOT proof of,
  a prior-era system run that placed them levered.**

**Best available reading:** the pre-12-Jun large orphans are most plausibly the
debris of an **earlier live/test era** (before the 12-Jun re-launch that created the
current DB) whose position sizing was **L4-binding at 5× MIS leverage** — placed,
lost track of, and left as broker positions the current DB has no record of. **This
is unconfirmable**: the prior-era DB was replaced on 12-Jun, and journald (the only
pre-12-Jun record, and only back to ~10-Jun) captured the reconciler's *orphan
complaint*, never the *placement*. Distinct from Thread 1's 15-25 Jun qty-1/2 debris
(the exit-rejection HARD_KILL cluster, now resolved).

---

## The evidence horizon (the decisive frame)

| Source | Earliest record | Note |
|---|---|---|
| journald (`trading-system.service`) | ~10-Jun | 30-day prune; the ONLY pre-DB record. Shows HGS 10-Jun orphan. |
| `webhook_audit` | **2026-06-12T09:17** | DB begins at the 12-Jun live switch (`39481f3` 12-Jun 07:50). |
| `fm_ledger` | **2026-06-12T07:39** | Capital ledger from 12-Jun. |
| `trades` | **2026-06-15T11:34:10** | First persisted trade. All 396 rows `mode=LIVE`. |
| `reconciliation_log` | **2026-06-15T11:34:16** | RC10 persistence turned on (per prior forensics), mid the 15-Jun restart storm. |

**HGS's 10-Jun orphan sits before the entire DB.** There is nothing in the DB to
attribute it to — not a missing row, an *absent table era*.

---

## B3 — are HGS/AFCONS real system positions? *No rows; undeterminable.*

- **`SELECT * FROM trades WHERE symbol IN ('HGS','AFCONS')` → 0 rows.** (`orders`
  has no `symbol` column — it joins via `trade_id`; with no trade there are no
  orders either.)
- This is **not** the "system opened them and lost the row" signature *within* the
  DB — because the DB does not reach back to when they were opened. The 10-Jun HGS
  orphan is 2 days before the DB's first row (12-Jun) and 5 days before `trades`
  begins (15-Jun).
- By the system-only invariant (Zerodha = system-only; Rama last touched it ~3 yrs
  ago), they were **not manual**. So the live options are: (a) a **prior-era system
  run** (11-May–11-Jun live/paper, DB since replaced) placed them — the 5× magnitude
  fits; or (b) some non-trade process left real broker positions. **(a) is the
  simplest fit; neither is confirmable from surviving evidence.**

## B2 / B4 / B5 — leverage, capital, and the 15-Jun boundary

- **B2 leverage:** constant `INTRADAY 5.0` since foundation — the magnitude is a 5×
  sizing outcome, not a config change. *If yes-a-leverage-change had been the answer,
  it would have been evidence for the locked 5× leverage decision; it is not — the
  config never moved, so the finding is instead about the sizing OUTCOME.*
- **B4 capital:** ₹10,000 at go-live, never materially larger. The anomaly does
  **not** shrink under a bigger-account hypothesis.
- **B5 the 15-Jun coincidence:** `trades` + `reconciliation_log` both begin
  **15-Jun 11:34** — the first trade + RC10 persistence turning on, inside the 15-Jun
  restart storm. The **capital-relative position cap that would actually prevent a
  ₹50k position** (`max_position_value_rs ₹2500 → max_position_value_pct 0.40`, i.e.
  "cap = 0.40 × capital ≈ ₹4k") landed in **BUILD 1 on 24-Jun** (`c69f1fd`) — *nine
  days later*, not at the 15-Jun boundary. So no single 15-Jun sizing change explains
  the magnitude drop; the drop coincides with **real live trading on the ₹10k account
  from 12-Jun onward** producing small (qty-1/2) positions, versus the pre-12-Jun
  large residuals.

## The 12→15-Jun gap (why `trades` starts 3 days after the DB)

12-14 Jun: **3,675 `webhook_audit` rows** (signals pouring in) but only **8
`fm_ledger` rows and 0 `trades`.** The current-DB-era system was **ingesting signals
but essentially not trading** for three days after the live switch. So `trades`
beginning 15-Jun is **the first real trade**, not a wipe of 12-14 Jun trades — there
were none to wipe. (This also means the 12-14 Jun journald orphans were not the
current-DB system's own fresh trades.)

---

## Conclusion (B6 — thin evidence is the honest outcome)

**Cannot be determined beyond ~12-Jun**, having checked: the full `trades`/`orders`
tables (no HGS/AFCONS rows; table starts 15-Jun), every key table's earliest row
(DB starts 12-Jun), the account capital (₹10k, `accounts.csv` history), the leverage
map (constant 5.0), the sizing-cap history (capital-relative cap = 24-Jun), and the
12-14 Jun activity (ingesting, not trading). The pre-12-Jun origin is **behind the
DB horizon and behind journald's ~10-Jun reach.**

The **least-strained reading** — a prior-era live run sizing at L4-binding 5×
leverage (₹10k × 5 ≈ ₹50k; qty ≈ ₹50k/entry), orphaned and then invisible after the
12-Jun DB re-launch — is **consistent with every surviving number** but **provable by
none of them.** It should be recorded as *plausible, unconfirmed*, not asserted.

**This is a different order of concern from Thread 1** and does not reopen it:
Thread 1's 15-25 Jun orphans are qty-1/2 exit-rejection debris with `trades` rows and
a named, fixed root cause; these are pre-DB, large, and rowless.

## What could still resolve it (none available now)

- A **pre-12-Jun DB backup** would carry the prior-era `trades`. Memory records **no
  off-site backup** and the on-VM backups postdate the re-launch — so almost
  certainly gone. (Not searched exhaustively here; a backup hunt is the only lever
  left, and low-probability.)
- **Pre-10-Jun journald** is 30-day-pruned — gone.

## Not in scope / unchanged

Thread 1 (resolved); the 15-25 Jun cluster; any fix, sizing change, or config edit.
No `scripts/*.py --db`; `sqlite3 -readonly` only; nothing placed or restarted.
Findings-only.
