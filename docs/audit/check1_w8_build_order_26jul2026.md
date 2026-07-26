# CHECK1 + W8 — build order, and what is held

**Date:** 2026-07-26 · **HEAD:** `08d300b`+ · Companion to `check1_w8_design_26jul2026.md`
and the canonical `docs/closure_source_contract.md`.

---

## Delivered now

**§A — the canonical vocabulary.** `core/closure_source.py` (code single-source, following the
repo's existing `core/constants.py` pattern that all five modules already import
`PRODUCT_TO_INTENT` from) + `docs/closure_source_contract.md` (the human contract) + a test pinning
them together and scanning for any third restatement.

## ⛔ Held: §B, §C, §D — with reasons, per F4

---

## 1. ⭐ §0's own logic applies to §B's schema migration — and more strongly

§0 made the deferral configurable and **default-0** so Monday stays readable, because Monday is
already a stack of first-runs: the 17:35 service window, PB-01's first-ever capture, the `soft_kill`
severity routing, the paper product fidelity fix.

**§B cannot be defaulted off.** `trades.closure_source` + `exit_mechanism` are new columns, and this
schema rebuilds `trades` to add columns — the established pattern for every prior trades column
addition:

```
MIGRATION_TABLES = {
    ...
    34: ["trades"],  # Diary #4: add sizing-audit columns; rebuild copies old cols, new ones -> NULL
    35: ["trades"],  # Slice 1: add tgt_risk_reward_applied / exits_verified / exits_verify_detail
}
```

⇒ **v45 would rebuild the live `trades` table during Monday's 08:15 boot** (migrations run
on DB-OPEN, and only main.py's boot path is permitted to run them — the P11 guard). That is a
*structural* change to the capital-bearing table, on the one morning §0 exists to keep readable, and
**unlike the deferral there is no flag that makes it a no-op.**

⭐ **This is the same argument §0 made, applied to the piece §0 did not consider.** Holding §B until
Monday is observed clean is the consistent decision, not a more cautious one.

---

## 2. ⚠️ §0.3's separation does NOT hold literally — reported, as instructed

> *"VERIFY THAT SEPARATION HOLDS: if the restored 'TARGET HIT' alert depends on the deferral to fire,
> then step 3 without step 4 is exactly what the design forbids."*

**It depends on it.** With the bound at `0` there is no deferral, so CHECK1 still wins the race and
still finalizes; `close_trade` still raises `ValueError`; `order_placer._handle_exit_fill` still
returns at `:2374`. **The literal `order_placer` "🎯 TARGET HIT" emitter cannot fire at bound 0.**

**But the operator-visible requirement can be met without the deferral, and should be.** The
requirement is *"a normal TGT fill must produce an INFO alert, not a false CRITICAL"* — not *"that
specific line of code must execute"*. Under §A's precedence ladder CHECK1 can classify the close as
`OWN_TGT` from evidence it already holds and emit the **INFO** attribution itself. Then:

| bound | who finalizes | who alerts | operator sees |
|---|---|---|---|
| `0` (default) | CHECK1 | CHECK1, as **INFO `OWN_TGT`** | correct INFO, capital timing **unchanged** |
| `> 0` | `order_placer` | `order_placer`, as **INFO "TARGET HIT"** | correct INFO, capital released a moment later |

**Exactly one alert either way, correct content either way.** ⭐ That is what makes the separation
real: the *correctness* fix (never a false CRITICAL) ships un-gated; only the *timing* fix is behind
the bound. ⚠️ It must be built that way deliberately — if CHECK1's INFO is not implemented, then
shipping §C without §D leaves the alert deleted, which is exactly what F1 forbids.

---

## 3. What is ready to build, in order

1. **§B** — `closure_source` + `exit_mechanism`, schema **v45**, `MIGRATION_TABLES[45] = ["trades"]`.
   ⭐ **Five writer sites, four `OWN_*` values** (a GTT leg is an SL or a TGT by *reason*; `GTT` is
   its *mechanism*). Note `orders/order_manager.close_trade` is the single central finalizer and
   already takes a validated `exit_reason` from `{TGT_HIT, SL_HIT, MANUAL_CLOSE, EOD_SQUAREOFF}` —
   so most of the derivation belongs there, at one site, rather than scattered across five.
   ⚠️ `MANUAL_CLOSE` is the ambiguous one and needs the caller to disambiguate `OWN_KILL`.
2. **§C** — D1 (return the distinction, not a count) + D2 (gather → classify → act, **claim then
   cancel** per Q2) + the pure classifier implementing §A's ladder and contradiction rule.
3. **§D** — the wall-clock bound (config, default `0`), expiry logging, CHECK1's INFO attribution,
   and the restored `order_placer` alert at bound > 0.

⛔ **§C must not ship without §D** (F1), and per §2 above that specifically means CHECK1's INFO must
land with the classifier.

---

## 4. Why not build §C/§D now and hold only §B

They were considered separately. §C rewrites `_check1_manual_close` — the function that releases
capital on the backstop close path — into three phases, and §D changes *when* that release happens.
Both are order/capital path, both need RED-first coverage of the contradiction rule and the
one-path-releases invariant, and both want their own full regression gate.

⭐ **Splitting them across sessions is safer than compressing them into one.** The vocabulary they
both depend on is now fixed and committed, so neither can drift while it waits — which was the
entire purpose of doing §A first.
