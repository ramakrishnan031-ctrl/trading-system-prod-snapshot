# The closure vocabulary — canonical contract

**Created:** 2026-07-26 · **Code single-source:** `core/closure_source.py` ·
**Pinned by:** `tests/unit/test_closure_source_contract.py`

⭐ **This document and `core/closure_source.py` are the ONLY two places these values may
appear.** A third restatement — a comment in `order_reconciler`, a docstring in
`eod_squareoff` — *is* the divergence W8 exists to retire. `reports/daily_trade_review.py:34-46`
already records the 4-way collision (daily-EOD / operator-manual / RMS / kill-flatten) that the
absence of this vocabulary caused.

---

## 0. First principle

> ⭐ **Suppress the CRITICAL only on POSITIVE evidence that one of our own legs accounts for the
> close. NEVER on absence of evidence.**

Broker unreachable · `orders` unreadable · cancel reason unrecognised · match ambiguous ⇒
`EXTERNAL_UNATTRIBUTED` at **CRITICAL**. Every unknown resolves *towards* the alert.

⚠️ The acceptance test is **not** "no more false CRITICALs". It is: *a genuine external close still
fires, on the first occurrence, with every evidence source degraded.*

---

## 1. Two axes — do not merge them

| field | question | example |
|---|---|---|
| `closure_source` | **WHO/WHAT** closed it (the reason) | `OWN_TGT` |
| `exit_mechanism` | **HOW** the order reached the broker (the venue) | `GTT` |

**Why separate (Q1).** A GTT leg *is* an SL or a TGT for classification; that it was
broker-managed is a mechanism. Merging them means that in Slice 2.5 — where **every** CNC exit runs
through a GTT — delivery exits become unattributable by reason, or `OWN_SL` silently means something
different in MIS and CNC. Mixing the two axes is how the current collision started.

---

## 2. `closure_source`

| value | meaning | written by |
|---|---|---|
| `OWN_SL` | our stop-loss leg filled | `order_placer` exit path |
| `OWN_TGT` | our target leg filled | `order_placer` exit path |
| `OWN_EOD` | our EOD square-off leg filled | `eod_squareoff` |
| `OWN_KILL` | kill-switch flatten / orphan sweep | `kill_switch` |
| `EXTERNAL_UNATTRIBUTED` | **nothing of ours accounts for it** | `order_reconciler` CHECK1 |

`OWN_CLOSURE_SOURCES` = the four `OWN_*` values — i.e. "not an external close".

⛔ **Deliberately absent: `BROKER_RMS` and `OPERATOR_MANUAL` as separate values.** The 05-Jul audit
established they are indistinguishable in-data. Inventing values we cannot populate re-creates the
over-claim somewhere new. **One honest bucket beats two confident guesses.** If Kite ever exposes an
RMS marker it becomes a *refinement* of `EXTERNAL_UNATTRIBUTED`, never a retro-fit of the four
`OWN_*`.

## 3. `exit_mechanism`

`LIMIT` · `MARKET` · `GTT` (broker-managed OCO, delivery) · `CO` (broker-managed cover-order SL) ·
`UNKNOWN` (not determinable — **never guessed**).

---

## 4. The precedence ladder — strongest identity first

| # | source | why it ranks here |
|---|---|---|
| 1 | **`get_trades()` `order_id` matches one of our legs** | the broker *names the order that filled*. Nothing beats direct identity. |
| 2 | **local leg `COMPLETE` AND broker corroborates** | two independent sources agreeing |
| 3 | **local leg `COMPLETE` alone** | a *cached broker fact* (our row only reaches COMPLETE when `order_monitor` observed a real fill) — not a guess, but ranked below anything read live |
| 4 | **the mid-fill cancel refusal** | the broker refused the cancel *because our leg was filling* — positive, but indirect |

**First match wins. Ties are impossible because the sources are ORDERED, not scored.**

⚠️ **Row 1 is live-only.** `zerodha_adapter.get_trades()` returns `[]` in paper mode, so a paper run
can never exercise it and will land on row 2/3. That is why row 3 must exist: dropping it would make
paper classify every own-leg close as `EXTERNAL_UNATTRIBUTED`.

---

## 5. ⚠️⚠️ The contradiction rule — the safety property

> **If two sources DISAGREE about which leg closed the position, that is NOT a tie, and it MUST NOT
> resolve to the higher-precedence source. It resolves to `EXTERNAL_UNATTRIBUTED` at CRITICAL.**

Precedence orders sources that are **silent**. It never overrules a source that **spoke**.

*Example:* `get_trades()` names our SL order, and our local TGT row is `COMPLETE`. Both cannot be
true. Taking row 1 because it outranks row 3 would report `OWN_SL` with total confidence while the
book says otherwise. **Silent disagreement between evidence sources is precisely how a wrong answer
looks confident** — and it is indistinguishable, downstream, from a correct one.

---

## 6. What this vocabulary does NOT do

- ⛔ Distinguish RMS from operator-manual (§2).
- ⛔ Replace FIX-183's adoption prepass. A carried CNC has **no leg to find**, so every source above
  is silent and the first principle correctly lands it on CRITICAL — the wrong answer *without* the
  prepass. The vocabulary is **dependent** on it, not a replacement.
- ⛔ Consult `holdings()`. Recorded as a **trigger, not a rejection**: revisit if Slice 2.5's CNC
  carry proves the ordered sources insufficient for delivery positions. It would add a broker call to
  a hot path to answer what the ordered sources already answer, and a failed call would itself
  resolve to CRITICAL under the first principle — *adding a new false-alarm source to fix a
  false-alarm problem*.
