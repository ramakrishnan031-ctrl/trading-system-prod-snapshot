# CLASSIFICATION LEAKAGE — **ONE architectural family, not three defects**

**06-Aug-2026 · DESIGN ITEM. ⛔ NOTHING BUILT. ⛔ No code, no config, no exit code changed.**
*Synthesis authored by ChatGPT, adopted. Filed as ONE item deliberately — see §0.*

---

## §0 · WHY THIS IS ONE ITEM AND NOT THREE

**Three defects surfaced on 06-Aug. They look unrelated and they are the same shape:**

> ⭐⭐ **A value from one CLASSIFICATION DOMAIN is being read as though it belonged to another.**
> The domains are **healthy state** · **business finding** · **execution failure**.

| # | defect | what leaked | into |
|---|---|---|---|
| **1** | **F6** — `cnc_gtt_monitor.py:464` `abs()` | a **runtime** reading (`held`) that is wrong | a **business** state (*trade open*) |
| **2** | **the tree diff** — `strategy_direction_registry.yaml` | a **healthy, self-healing** condition | a **violation** ⇒ the EOD report is CRITICAL **every day** |
| **3** | **`reconcile_positions` exit 2** | a **business finding** (*mismatches detected*) | an **execution failure** (`status=FAILED`) via the heartbeat wrapper |

> ⛔⛔ **FILED AS ONE ITEM ON PURPOSE.** ⭐ **One review may close several future
> alert-fatigue cases; three separate items will be fixed three separate ways** — and the third fix
> will not know it was solving the first problem again.

---

## §1 · MEASUREMENT TRUST — **separate RUNTIME truth from REPORTING truth** *(4.1)*

> **Any CLOSED broker execution missing from the accounting layer should INVALIDATE derived
> metrics — expectancy, win rate, daily P&L — until reconciled, rather than silently contributing
> stale statistics.**

⭐⭐ **This is the direct answer to F6 cost #7, and its shape is the important part: NOT *"fix the
number"* but *"refuse to publish a number you cannot vouch for."***

**(P) tonight's instance:** one broker execution (ATULAUTO SELL, filled), **zero** local closures ⇒
the day's P&L, the 483-trade expectancy corpus and `positional_momentum_long`'s 100 % win rate were
all published **as if complete**. ⛔ **None of them carried a caveat, because nothing knew to add one.**

## §2 · A MEASUREMENT-INTEGRITY GATE *(4.2)* — ⛔ **DESIGN ONLY, DO NOT BUILD**

**Assertion:** `broker executions == local realized trades`, **verified BEFORE** any analytics run,
corpus update, or strategy report is generated.

- ⭐ **Cheap — it is a count comparison**, not a reconciliation engine.
- ⭐⭐ **It would have caught tonight AUTOMATICALLY: one broker execution, zero local closures.**
- ⛔ **On failure it must BLOCK PUBLICATION, not warn** — a warning beside a published number is the
  very pattern §1 rejects.
- ⚠️ **Open question, not resolved here:** what it does about a legitimately-open carry (a T+1
  position is *supposed* to have no closure). ⭐ **The predicate is about CLOSED executions, so a
  carry should not trip it — but that is exactly the distinction F6 currently gets wrong**, so the
  gate must not be built on the same reading.

## §3 · EXIT-CODE CONVENTION, PROJECT-WIDE *(4.3)*

> **Reserve EXIT CODES for PROCESS HEALTH. Publish BUSINESS FINDINGS through STRUCTURED STATUS
> FIELDS.**

⛔ **Otherwise monitoring keeps confusing successful diagnosis with execution failure** — ⭐ exactly
defect 3, generalised. **(S)** `reconcile_positions.py:428-434` returns `2` for *mismatches found*
and `1` for *error*; the heartbeat wrapper maps **any non-zero** to `FAILED`, so **a job that did its
job correctly reports as broken**, and from there into a CRITICAL email.

🏷️ **Sibling of `feedback_never_classify_by_free_text`:** there the defect was branching on free
text where a structured status existed; here it is **encoding a structured finding into a scalar
whose meaning is owned by someone else.**

⚠️ **Scope note, unmeasured:** how many other cron jobs return a non-zero *finding* code is **NOT
measured here.** ⛔ **Do not assume `reconcile_positions` is the only one** — that sweep is part of
the item.

---

## §4 · WHAT THIS ITEM IS **NOT**

⛔ Not a proposal to suppress any check. ⛔ Not a proposal to change an exit code tonight.
⛔ Not a fix for F6 — F6 has its own design (`f6_delivery_exit_predicate_design_06aug2026.md`); this
item explains **why F6's failure mode keeps recurring in unrelated subsystems.**

*⛔ Nothing built. Every item above is a design entry awaiting Rama's ruling.*
