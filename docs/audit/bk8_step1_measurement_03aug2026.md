# BK-8 (family α) — STEP 1 MEASUREMENT — 03-Aug-2026

**Status: `<MEASURED — NOT BUILT, NOT AUTHORISED>`. Halted.**
Measured against `8519289`. Read-only; no `.py` changed by this record.
⛔ Deliberately not built: BK-8's third part is a **startup assertion that can abort boot**, and
anything committed now would first execute on **Wednesday, the flip morning**.

---

## 0. THE QUESTION THAT WAS ASKED FIRST, AND WHY IT WAS THE RIGHT ONE

> *"What is the defensible authority for **declared**? The audit found the intended-architecture
> map has degraded into a changelog, so if no trustworthy authority exists, BK-8 is a much
> larger item than 'a CI check' — and that finding is itself the deliverable."*

**The premise is half right, and the half that is wrong is the good half.**

## 1. ✅ THE AUTHORITY EXISTS, IT IS EXECUTABLE, AND IT IS FREE

The authority for "declared config" is **not** a document and therefore **not** subject to the
degradation the audit found. It is the **pydantic model tree in `core/config_loader.py`**:

| Measured at `8519289` | |
|---|---|
| pydantic models | **64** |
| total declared fields | **446** |
| largest | `SRDetectorConfig` 45 · `V3ChainConfig` 42 · `SystemConfig` 42 · `RegimeConfig` 17 |

Why it is *defensible* where `SYSTEM_MAP.md` is not: the models are **load-bearing and
executable**. Every boot parses config *through* them, and `_cross_field_sanity_checks` runs on
construction. **A field that is not in the model is not parsed; a field in the model is.** The
declaration therefore **cannot drift from reality** — drift is what a *document* does.

⇒ **BK-8 is NOT blocked on the missing architecture map**, and the declared set needs **no
hand-maintenance**: it is machine-enumerable from the models at any moment.

## 2. CAN IT EXTEND #1's REGISTRY + `assert_composition`? — **SPLIT ANSWER**

### 2a. ✅ The CONTRACT and SEVERITY MODEL — yes, and it should

`core/effect_telemetry.py::assert_composition` (`:152`) already implements precisely the shape α
needs, and it is now deployed and proven live (61/61 on the 03-Aug boot):

- declared-vs-observed in **both directions** — `missing` / `ghosts` / `unknown` (`:170-182`)
- **`unknown` is the C2 enforcement leg** — observed-but-undeclared, which is exactly α's
  "a key is read that nothing declares"
- severity split: **dev/paper raise · live CRITICAL-and-continue** (`:162`)
- a declared **mode-exemption** mechanism (`_mode_exempt`) for legitimately-conditional entries

Reusing this contract means α inherits a severity model that has already survived a live boot.

### 2b. ⛔ The hand-maintained REGISTRY mechanism — **no**

`config/expected_managers.yaml` holds **52 entries** under the rule *"a new manager ctor in
main.py REQUIRES an entry here IN THE SAME DIFF."* That rule is enforceable by review at 52.

At **446 fields it is ~9× larger.** A 446-row hand-maintained file under a same-diff rule
**becomes the changelog-degradation failure the audit already found** — the same disease in a
new file.

⭐ **And deriving the declared set DISSOLVES the family-α concern rather than managing it.** The
worry was that a second "single source" would be a self-inflicted family-α defect. A **derived**
set introduces **no second source at all** — there is nothing to fall out of sync, because the
models *are* the source.

⇒ **Extend the contract; derive the registry. Do not transcribe it.**

## 3. 🔴 "CONSUMED" IS WHERE THE SIZE ACTUALLY LIVES

This is the asymmetry that decides BK-8's cost, and it is **not** the "declared" side:

- **Managers self-register.** `assert_composition` works because construction calls an explicit
  `register()`, so `_constructed` is an *observed runtime fact*.
- **Config fields have no equivalent.** There is **no runtime signal for "this field was read."**
  Nothing to compare the declared set against.

Measured obstacles to deriving it statically:

- **164 dynamic `getattr(...)` config accesses** in production code (tests excluded).
- The sampled ones use **string literals** — e.g. `getattr(cfg, "enabled", False)`,
  `getattr(cfg, "smtp_host", "smtp.gmail.com")` (`alerts/telegram_notifier.py:802-831`) — which
  **AST analysis can recover**. So the blind spot is bounded to *non-literal* `getattr`, not to
  all 164. ⚠️ But every one of the 164 must be **classified** before a "consumed" set can be
  trusted, because a single missed dynamic read produces a **false "unused"** — and a false
  positive here is worse than no check, since it invites deleting a live config key.
- ✅ **Anti-duplication measured: no config-drift / unused-key checker exists** anywhere in
  `scripts/` or `core/`. BK-8 would be the first, so there is nothing to extend or collide with.

## 4. THE DELIVERABLE, STATED PLAINLY

**BK-8 is a larger item than "a CI check" — but for the OPPOSITE reason to the one assumed.**

- ⛔ *Not* because the authority for "declared" is untrustworthy. It is trustworthy, executable,
  and costs nothing to enumerate.
- 🔴 *Because* the **consumed** side requires instrumentation that does not exist, over a
  446-field surface with 164 dynamic-access sites that must each be classified.

Restating the register's framing with the measurement attached: BK-8 is *"half of §B#1's one
mechanism"* — and the measurement shows **which half is cheap and which is not.** The declared
half is nearly free by reusing §2a and deriving §1. The consumed half is the project.

## 5. ⛔ OPEN — NOT DECIDED HERE

1. **How "consumed" is observed** — AST-only (static, cheap, blind to non-literal `getattr`) vs
   runtime attribute instrumentation (accurate, costs a hook on a hot path) vs a hybrid. **Not
   chosen.**
2. **What a false "unused" costs**, and therefore how conservative the first version must be.
   The safe default is to report **`unknown` (read-but-undeclared) only** — the direction that
   *cannot* produce a delete-a-live-key error — and defer "declared-but-unused" entirely.
3. **Whether α runs at startup at all.** BK-8's third part is a boot-aborting assertion; §2a's
   live behaviour is CRITICAL-and-continue, which is the safer inheritance.
4. **Deploy slot.** A startup-time mechanism must not first execute on flip morning.

⛔ **HALT. Nothing built.**
