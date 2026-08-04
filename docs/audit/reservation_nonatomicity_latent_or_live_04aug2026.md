# IS THE RESERVATION NON-ATOMICITY **LATENT** OR **LIVE**? — MEASURED — 04-Aug-2026

**Status: `<MEASURED — NOTHING REPAIRED>`.** Measured at **`4e1a991`**. Read-only.
⛔ **No divergence was corrected — a correction to capital state is its own carded decision.**

Follows `ledger3b_separability_step1_04aug2026.md` §4, which recorded the non-atomicity and
**refused to call it live** until measured. Two checks; the second is the one that matters.

---

## (a) DID IT FIRE? — **NO, and the stronger statement is that the path never ran**

| | |
|---|---|
| `FIX-B: update trade status FAILED` | **0** |
| `FIX-B: capital release failed` | **0** |
| `FIX-B: no reservation_id found` | **0** |
| ⭐ **ANY `FIX-B:` line at all** (incl. the SUCCESS `log.info`) | **0** |

⭐⭐ **The last row is the finding, not the first three.** The success line
(`FIX-B: capital released for reservation_id=…`) is also zero ⇒ **the orphan auto-close block
has never executed**, so its two error branches are not "never failed" — they are
**unreached**. A zero from an error branch inside a block that never ran says nothing about
the error branch.

⚠️ **WIDTH, STATED BESIDE THE ZERO (§M5): 22 trading-day logs, `system_2026-07-06` →
`system_2026-08-04`.** ⇒ this means **"not in 22 retained trading days"**, ⛔ **never
"never"**. Log retention is ~32 days; **anything before 06-Jul is unobservable**, which
matters because §(b) finds the divergence is **older than that**.

⇒ **The #3b non-atomicity is LATENT** — structurally real, never exercised.

---

## (b) IS THERE DIVERGENCE **NOW**? — ⚠️ **YES, 10 rows / ₹1,628.13 — and it is NOT retention-bounded**

### ⛔ FIRST, MY OWN FALSE FINDING, RECORDED BECAUSE THE CATCH IS THE METHOD

The obvious query — *RESERVE with no `RELEASE`/`RELEASE_USED`* — returned **221**. **That is
WRONG.** It contradicted the naive arithmetic (1327 − 1106 − 211 = **10**), and **when two
counts over one corpus disagree, the model is wrong, not the data.**

Measured cause: **`RELEASE_USED` carries NO `reservation_id` — 0 of 211** (its signature
`release_used(symbol, exit_price, …, trade_id)` never takes one), while **`COMMIT` carries it
211 of 211.** ⇒ a **filled** trade terminates `RESERVE → COMMIT → RELEASE_USED`, and a
**cancelled** one `RESERVE → RELEASE`. Keying on RELEASE alone marks every filled trade as a
divergence.

✅ **Corrected query — RESERVE with neither `RELEASE` nor `COMMIT` — returns 10, which
reconciles exactly with the independent arithmetic.** Two methods agreeing is the check that
the model is now right.

### The 10

| | |
|---|---|
| count | **10** |
| total | **₹1,628.13** |
| dates | **15-Jun · 16-Jun · 18-Jun — and nothing after** |
| trade status | 9 × `CANCELLED`, 1 × `CLOSED_MANUAL` — **all terminal** |
| symbols | SETL · HARIOMPIPE · AVL · GICRE · POWERICA · BEPL · INDOFARM · GLOBUSSPR · BAJAJHCARE · NYKAA |

⭐ **All ten fall in a 4-day window, 15–18 Jun, and the mechanism has not recurred in ~47
days.** ⚠️ **The cause is NOT diagnosable** — `system_*.log` begins 06-Jul, so the window
predates every retained log. This is the same **June instability era** already on record as
permanently unknowable, and ⛔ it is **not** the FIX-B path, which §(a) proves never ran.

### ✅ THE MATERIAL QUESTION: IS THE ₹1,628 STRANDED FROM TODAY'S CAPITAL? — **NO**

`fund_manager.rehydrate_from_open_trades:1669` — *"Walks every **OPEN/PARTIAL** trade, looks
up its reservation_id … then replays the ordered RESERVE/COMMIT chain for that
reservation."*

⇒ **Rehydrate is keyed on TRADE STATUS, not on ledger completeness.** All ten belong to
`CANCELLED`/`CLOSED_MANUAL` trades ⇒ **never replayed** ⇒ **the in-memory capital state has
never held them** and **no rupee is stranded from live buying power.** ⚠️ This matters: on a
~₹9,871 book, ₹1,628 would have been **16.5%** of capital.

⭐⭐ **AND NOTE WHAT MAKES IT HARMLESS — IT IS THE SAME OVERLOADED COLUMN AGAIN.** Rehydrate
survives a corrupt ledger only because it keys on **OPEN/PARTIAL**, i.e. on the
*"does this hold a position?"* fact inside `trades.status` (#7 §2b). **Correct — by accident,
in the capital layer this time**, and it would stop being correct the moment reconstruction
were keyed on the ledger instead.

⚠️ **THE RESIDUAL RISK IS THEREFORE NOT CAPITAL — IT IS TOOLING.** `fm_ledger` is **not
self-consistent**: any future audit/reporting tool that reconstructs capital **from the
ledger alone** inherits **₹1,628.13 of phantom reservations**. That is a live trap for
exactly the kind of tooling this campaign keeps building.

---

## VERDICT

| question | answer |
|---|---|
| Has the #3b non-atomicity fired? | ⛔ **No — the path has never run** (22 trading days; ⛔ not "never") |
| Is it structurally real? | ✅ Yes — two mutations, two independent `try/except`, no compensating action |
| Is there divergence in the books now? | ⚠️ **Yes — 10 rows, ₹1,628.13, all 15–18 Jun, cause unknowable** |
| Does it affect live capital? | ✅ **No** — rehydrate keys on OPEN/PARTIAL, so they are never replayed |
| Does it affect anything? | ⚠️ **Ledger-based reconstruction only** — `fm_ledger` is not self-consistent |

🔴 **OPEN, ⛔ nothing decided:** whether the 10 rows should be reconciled, annotated, or left
as history. ⛔ **A correction to capital state is its own carded decision and was NOT made.**

⛔ **HALT.**
