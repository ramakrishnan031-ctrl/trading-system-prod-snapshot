# Slice 2.5 — the 7 built-but-never-run pieces, classified

**Date:** 2026-07-26 · **HEAD:** `bf8adf9` · ⛔ **CLASSIFICATION ONLY — nothing built.**
Companion to `slice25_decomposition_26jul2026.md` (which enumerates all 16 pieces).

---

## Headline

- ⭐ **None of the 7 is "never wired."** All are constructed in `main.py` and reachable on every
  cycle. That rules out the failure mode this project has hit most often.
- **The 7 split into three distinct causes**, which need opposite treatment.
- ⚠️ **"Never run" ≠ "never tested."** Two of the riskiest have **25 unit tests** driving the *real*
  paper adapter. That lowers the risk materially and the decomposition doc overstated it.
- ⭐ **§A unblocks the POSITION-based proofs, not the GTT lifecycle** — the GTT pieces were never
  blocked by the product hardcode. But a genuine gap remains, below.

---

## C1 — why each has never run

### Cause A — runs every cycle over an empty collection (4 pieces)

The machinery is warm; the **loop body has never executed once**. This is the largest group and the
one where first-run risk actually lives.

| piece | site | what it iterates | why empty |
|---|---|---|---|
| Reconciler delivery exclusion | `order_reconciler.py:801-806` | `get_active_gtt_states()` | live `gtt_state` has 0 rows, ever |
| FIX-183 adoption prepass | `order_reconciler.py:641` | broker GTT list | no GTT has ever existed on the account outside T2's own |
| `CncGttMonitor.reconcile` | `main.py:2686` → reconciler `:2713` | `get_active_gtt_states()` | same |
| Boot rehydration | `main.py:2616 hydrate_from_store()` | `gtt_state` | returns 0 at every boot in the system's history |

⭐ Note the shape: these run **thousands of times a day** and do nothing. They are not dormant code —
they are *exercised wrappers around cold payloads*. A health check that asserted "the adoption
prepass ran" would be green today and prove nothing.

### Cause B — runs, but the delivery branch is never entered (2 pieces)

Reached on every signal; the DELIVERY arm is unreachable because `force_intraday_only: true`
coerces every intent to INTRADAY **upstream**.

| piece | site | the branch |
|---|---|---|
| Delivery count caps (PHASE-3 A) | `risk_engine.py:431-449`, `:524-533` | guarded on `intent == DELIVERY` |
| `trade_type` master gate (PHASE-4) | `signal_processor.py:743` (LAYER 1) | its `REJECTED_TRADE_TYPE` arm never fires |

⚠️ **The `trade_type` gate itself DOES run** — it evaluates on every signal and passes them. Only its
reject arm is cold. Do not classify it as dead code.

### Cause C — gated OFF by config; the true branch has never been crossed (1 piece)

| piece | site | gate |
|---|---|---|
| Conditional capital allocation (PHASE-3 B) | `fund_manager.py:137` `if not conditional_enabled:` | `conditional_allocation_enabled: false` |

This is the only one of the 7 where a **config flag** is the reason. The other six are data/coercion.

---

## C2 — first-run risk order (what executes the moment delivery is enabled)

Ordered by **damage a first-run failure would do**, not by likelihood.

| # | piece | first-run damage | mitigations already in place |
|---|---|---|---|
| **1** | **Conditional capital allocation** | ⭐ **Affects the WHOLE book, intraday included** — it re-splits capital for every position, not just CNC. And the known foot-gun: `delivery_enabled=true` *without* this flag strands 70 % in the idle intraday bucket. | none beyond config review |
| **2** | **`CncGttMonitor`** | ⭐ **It can SOFT_KILL** — M2 (`>1 ACTIVE GTT for one trade`) halts trading on ownership ambiguity. A first-run false positive stops the day. | **13 unit tests** on the real paper adapter |
| **3** | **Reconciler delivery exclusion** | a carried CNC falls through to CHECK1 ⇒ mis-marked `CLOSED_MANUAL` + **capital released while still holding stock** | the exclusion + FIX-183 are belt-and-braces for each other |
| **4** | **FIX-183 adoption prepass** | if it fails, #3 has nothing to exclude on ⇒ same damage as #3 | **12 unit tests** on the real paper adapter |
| **5** | **Boot rehydration** | placer cache empty after restart ⇒ live GTTs unmanaged (broker still protects the position) | broker-side GTT survives independently |
| **6** | **Delivery count caps** | over-exposure, bounded by total capital and the concentration cap | capital caps still bind |
| **7** | **`trade_type` gate** | fails **closed** — signals rejected, nothing traded | lowest risk by construction |

⭐ **#1 is the top risk and it is the one with no test coverage and no runtime guard.** It is also the
only one of the seven that touches intraday capital — i.e. the part of the system that is currently
working and earning.

⚠️ **Correction to the decomposition doc:** it implied all 7 were equally unproven. #2 and #4 carry
**25 tests between them driving the real paper adapter with `seed_paper_holding`**, not mocks. They
are the *best*-covered of the seven, not the worst.

---

## C3 — what §A (the paper `product` fix) unblocks

**§A fixes `get_positions()` in paper.** So it unblocks exactly the proofs keyed on a *position's*
product:

| unblocked by §A | mechanism | previously unprovable because |
|---|---|---|
| ⭐ **FIX-015 / EOD6 CNC exemption** | `eod_squareoff.py:1072` filters `("MIS","CO")` | paper reported every position as MIS ⇒ a paper CNC was always squared |
| ⭐ **H-5 HARD_KILL sweep product** | `kill_switch.py:1588` picks sweep intent from `pos.product` | same ⇒ paper always swept as INTRADAY, the exact bug H-5 exists to prevent |

⚠️ **Both were previously "tested" against hand-built objects** — `test_fix015…` uses
`Position(product="CNC")` off a `MagicMock`; `test_h5…` uses
`SimpleNamespace(product="CNC")`. Both prove the *consumer* is correct given CNC input; neither could
prove the paper *feed* ever produces it. ⭐ **The mechanisms were right and the feed was wrong, and
the fixtures could not tell the difference.**

**NOT unblocked by §A — and never was blocked by it:** the four GTT-lifecycle pieces (Cause A). Those
read `holdings()` and the broker GTT list, not `positions()`. `seed_paper_holding` was built exactly
to drive them and already does, in 25 tests.

### ⭐ The paper gap that remains after §A

`_paper_holdings` has **exactly one writer — `seed_paper_holding`, the test helper.** Nothing
promotes a filled paper CNC position into a holding, and `_paper_positions` is an in-memory `dict`
with no day-boundary carry.

⇒ **Paper can model the END STATE of a carry (injected) but not the TRANSITION into it.** The
sequence *"CNC buy fills → becomes a day position → survives 15:17 → becomes a holding overnight →
is rehydrated at the next boot"* cannot be produced in paper at any point. That sequence is the
lifecycle. **This is why the Mon→Tue live pair is genuinely irreducible and not merely conventional.**

**❓ Worth deciding (not proposed here):** whether to model the transition in the paper adapter
(a settlement step promoting CNC positions to holdings at day roll). It would make the full carry
paper-provable. It is also new simulation surface on the execution path — cost and risk, for a
proof that a single live Mon→Tue pair also yields.

---

## C4 — calendar, the binding constraint

| needs | pieces | why |
|---|---|---|
| **nothing — desk work, today** | §A itself; then FIX-015 + H-5 paper proofs | pure paper, once §A lands |
| **paper, after §A** | delivery caps, `trade_type` gate, conditional allocation | flag flips in a paper run; no market needed |
| 🔴 **one live day** | T2 `--arm-overnight` **placement** leg | real CNC buy + real GTT + a genuine overnight hold |
| 🔴 **Mon→Tue live pair** | DDPI/demat sell · Cause-A pieces #1–#4 end-to-end | a demat debit only exists after a real settlement boundary; rehydration only means something across a real restart |

⭐ **Only the last two rows cost calendar, and they are strictly sequential.** Everything above them
can be finished on any Sunday. The honest schedule statement remains: **three supervised market
windows, each gated on the previous passing** — not a block of hours.

---

## What was NOT done

⛔ No build, no design, no flag change. Read-only. `delivery_enabled: false`,
`force_intraday_only: true`, `trade_type: INTRADAY`, `conditional_allocation_enabled: false` — all
unchanged.
