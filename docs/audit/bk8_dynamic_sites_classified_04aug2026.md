# BK-8 — CLASSIFYING THE DYNAMIC `getattr` SITES — 04-Aug-2026

**Status: `<MEASURED — "CONSUMED" NOT DEFINED, NOTHING BUILT>`.** Measured at **`ac5259d`**.
Read-only; **0 `.py` changed.** Continues `bk8_step1_measurement_03aug2026.md` §3, whose
blocker was: *"every one of the 164 must be classified before a 'consumed' set can be
trusted."*

---

## 1. ⚠️ FIRST, HONESTLY: I COULD NOT REPRODUCE THE FIGURE **164**

Two AST censuses over the same tree (tests, `venv/`, `sats/`, `site-packages` excluded):

| population | count | non-literal |
|---|---|---|
| **every** `getattr()` call site in project code | **261** | **4** |
| `getattr()` whose base object name matches `cfg\|config\|sc\|settings\|conf` | **77** | **0** |

**164 sits between them and I cannot say which query produced it.** ⛔ Per the rule that
earned itself twice today, *when counts over one corpus disagree the model is wrong, not the
data* — so I am **not** reporting 77 or 261 as "the" number, and **not** claiming 164 is
wrong. The record's query is simply not recoverable from its text.

### ⭐⭐ AND IT DOES NOT MATTER — THE BOUND HOLDS REGARDLESS

The record's own concern is explicit: *"the blind spot is bounded to **non-literal**
`getattr`, not to all 164."* And **config-getattr ⊆ all-getattr.** ⇒ **the non-literal blind
spot is AT MOST 4 SITES under ANY definition of "config object"**, because there are only 4
non-literal `getattr` calls in the entire project. **The exact denominator is irrelevant to
the decision.**

---

## 2. THE FOUR NON-LITERAL SITES — CLASSIFIED

| # | site | object | how the name resolves | is it a config read? |
|---|---|---|---|---|
| 1 | `core/config_auditor.py:531` | a **module** (`importlib.import_module`) | `cls_name` from `_STALE_DEFAULT_GUARDS`, a module-level literal tuple | ⛔ **No** — resolves a class, statically enumerable |
| 2 | `core/config_validator.py:161` | any pydantic model | `for field_name in type(obj).model_fields` — **the model's own field registry** | ⚠️ **Yes, but see §3 — this is the whole problem** |
| 3 | `scripts/generate_crontab.py:133` | a `CronJob` (or dict) | `attr` passed by in-file callers of `_value()` | ⛔ Not `SystemConfig`; callers are finite and local |
| 4 | `scripts/preflight/startup_hook.py:54` | a **Sentinel** | f-string `f"phase_{phase.lower()}_status"` | ⛔ **No** — sentinel state, not config |

⇒ **NOT ONE of the four is a dynamic read of an arbitrary `SystemConfig` field.** Two are not
config objects at all; one is a cron model with local callers; one is the generic walker below.

⇒ **The "false unused" hazard the record feared — a missed dynamic read causing a live key to
be deleted — is essentially ABSENT.** The declared→consumed static derivation is far more
tractable than Step 1 could establish without this measurement.

---

## 3. ⭐⭐ THE REAL BLOCKER IS THE OPPOSITE ONE: **VACUITY**, AND IT IS SITE #2

`core/config_validator.py:156-163` is a **generic reflective walker**:

```
for field_name in type(obj).model_fields:
    val = getattr(obj, field_name)
    ...recurse if val is itself a model...
```

⛔⛔ **It touches EVERY declared field, by construction, on every run.** ⇒ if "consumed" is
defined as *"the field appears as an attribute/`getattr` read somewhere in production code"*,
**this single function marks all 446 fields consumed** and **BK-8's check can never report
anything.** That is the **vacuous** half of the record's own requirement — *"neither noisy nor
vacuous"* — and it would be **invisible in review**: the check would run green forever and be
read as "no config drift".

⭐ **This is the same shape as the §V rule *"a green check is evidence only if it could have
been red"*, and as the M6 zero** — a check whose passing condition is unconditionally
satisfied is not a check.

### ⇒ THE DEFINITION BK-8 NEEDS, STATED AS A CONSTRAINT (⛔ not adopted here)

> **"Consumed" must EXCLUDE reads performed by generic reflective walkers over
> `model_fields`.** A field is consumed when a *named* site reads it — not when a validator,
> serialiser, or dumper enumerates the whole model.

⚠️ **And that exclusion needs its own care**, which is why it is stated and not adopted: the
walker is *legitimate* (`config_validator` is presumably load-bearing), so the exclusion must
be **allow-listed by function**, not by heuristic — and an allow-list that grows silently
re-opens the vacuity.

---

## 4. WHAT CHANGES FOR BK-8

| Step-1 belief | after this measurement |
|---|---|
| *"the consumed half is the project"* — 164 sites each needing classification | ⛔ **overstated.** ≤4 non-literal sites exist tree-wide; all four are classified above and **none reads an arbitrary config field** |
| the risk is a **missed** dynamic read ⇒ false "unused" ⇒ deleting a live key | ⚠️ **largely dissolved** |
| — | 🔴 **NEW, and it replaces it: the risk is a generic walker making the check VACUOUS.** Not noise, not a false positive — **a check that can never fail** |

🔴 **STILL OPEN, ⛔ nothing decided:** the allow-list mechanism for reflective walkers; whether
`config_validator`'s walk is the only one (⚠️ **not measured — I checked the four non-literal
sites, NOT every reflective enumerator**; a `model_dump()`/`dict()` call would have the same
effect and would not appear as `getattr` at all); and the runtime-signal question from Step 1,
which this does not touch.

⛔ **HALT.** No definition adopted, no check built, no register edit beyond this record.
