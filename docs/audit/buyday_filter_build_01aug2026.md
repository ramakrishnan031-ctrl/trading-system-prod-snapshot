# LEDGER #2 — BUY-DAY PRODUCT FILTER: BUILD RECORD
**Executed 01-Aug-2026 ~20:0x–20:5x IST (Saturday late; the card is dated 02-Aug).**
**Status: `<BUILT — NOT DEPLOYED, NOT PUSHED>`** — commits `43043a2` (code+tests) +
`15adf75` (superseded h5 test). Authority: `docs/audit/integrity_audit_2026.md` P7.2(b),
Q4/Q6/Q7; register §XE.3 #2; ChatGPT red-team G1–G5 binding.

---

## 1. STEP-1 FINDINGS (all verified before editing; line-shift +9 vs audit cites = our own telemetry commits, content byte-faithful)

| item | finding |
|---|---|
| The two sites | SITE 1 local pass (query :1481-89, loop :1508+, silent NULL→INTRADAY at :1519) · SITE 2 broker sweep (:1597 H-5 mapping) — both verbatim vs the audit's :1471-80/:1510/:1588-90 at `297b587` |
| Local product domain (G3) | code writers: `_PROTOCOL_TO_PRODUCT` {CO_PLUS_TGT→CO, LIMIT_TRIPLE→MIS} + "" default (order_placer :2327→order_manager INSERTs) + the CNC path; audit live-measured {MIS×802, CNC×3} ⇒ ⊆ {MIS, CO, CNC, NULL} ✓ |
| Broker domain (G3) | adapter passes RAW Kite `product` through (`broker_code`); Kite equities {MIS, CNC, NRML, CO(+MTF)}; anything beyond-set hits the loud-unknown path by construction — the sweep predicate reads the raw broker string, never the local vocabulary |
| EOD6 vocabulary (1d) | was TWO inline `("MIS","CO")` tuples (eod :1079 EOD6 + :1446 FIX-182) — not importable ⇒ shared source = `core.constants.EMERGENCY_FLATTEN_PRODUCTS` (leaf module, no cycle); both eod sites now read the ONE name (membership identical, predicates untouched) — **drift structurally impossible: no second copy exists to assert against** (meets G1's intent; stronger than an assertion) |
| Telemetry (1e) | kill flatten sells via the adapter directly — `placer.emergency_exit` tripwire and the kill counter untouched; **NO registry edit** ✓ |
| Non-unified literals (honesty) | `cost_calculator` `("MIS","CO")` ×3 = cost-tier math, a DIFFERENT semantic — deliberately not unified |

## 2. ⚠️ THE THIRD SITE — found by a failing fix181 test, DISCLOSED not patched (G5)

**`order_reconciler._check2_inflight_orphan` (:2023 → `_flatten_broker_position`)
flattens an in-flight-entry orphan WHILE HARD_KILL IS ACTIVE — a real sell-under-kill
path outside the card's two sites, and it is product-blind.** Ruling applied:

- **Reachability TODAY: CNC-unreachable.** It needs a delivery ENTRY in flight during a
  kill; `delivery_enabled: false` ⇒ no CNC entry can be in flight before the flip. Same
  latency class as F1 (gate = the first live delivery trade).
- **Family: the D-8-BLOCKED reconciler workstream** (§3.3: reconciler steps are blocked
  *until the filter lands* — this filter). Extending it here would be silent scope
  expansion (G5) against the card's own OUT-list; the card's STOP doctrine says report.
- **The owed follow-up (#2b, ~6 lines + tests):** apply `EMERGENCY_FLATTEN_PRODUCTS` +
  spare-CNC + loud-unknown inside `_check2_inflight_orphan`/`_flatten_broker_position`.
  🔴 **Rama's call: ride it into the Monday-evening deploy stack (15-min card), or name
  it a CARRY-PILOT blocker** (it does not gate the flag flip — no delivery entries exist
  until the pilot trades).
- Also noted for the #1 record: the B-2 amendment's `place_order()` dispersal list
  missed kill_switch's own two adapter calls (:1549/:1609) — the error-class lesson
  (repo-wide, never file-wide) applied twice now.

## 3. WHAT WAS BUILT

- **`core/constants.py`** — `EMERGENCY_FLATTEN_PRODUCTS = frozenset({"MIS","CO"})`, THE
  single source; Q4 rationale in-line.
- **SITE 1 (local pass):** CNC → **SPARED** (CRITICAL-loud log; `attempted -= 1` so the
  completion report stays honest — "all N attempted" can no longer claim a spared
  position was flattened); NULL/NRML/unknown → **FLATTEN + CRITICAL** via the shared
  `_alert_unknown_product` (one emitter, both sites; replaces the silent fallback);
  known products exit under their own H-5 intent (NRML → DELIVERY, loud, per 2e).
- **SITE 2 (broker sweep):** raw-broker-product predicate (G3); CNC excluded +
  spared-log; missing/unknown included + CRITICAL.
- **⭐ THE PER-PRODUCT-ROW HAZARD (found in design, test-pinned both directions):** Kite
  `positions()` is per (symbol, product) — sparing a CNC row must never suppress a
  same-symbol MIS row. Spared symbols are deliberately NOT marked `handled` at either
  site; site-1's qty-0 branch keeps its pre-existing mark-handled semantics.
- **eod_squareoff:** the two inline tuples now read the shared name — zero behaviour
  change, scheduled predicates untouched.

## 4. VALIDATION

| check | result |
|---|---|
| Targeted tests (`test_kill_switch_product_filter.py`, 8) | site-1 MIS/CO quiet-in · CNC spared+honest-count · NULL loud (CRITICAL send + INTRADAY fallback) · NRML loud under DELIVERY intent · site-2 CNC out/unknown loud · **both per-product-row hazard directions** · single-source scanner tripwire (re-inlining the set anywhere trips it) — all green |
| h5 sweep tests | CNC test updated to the Q4 contract (superseded-but-legible, `15adf75`); NRML/MIS/absent tests pass UNCHANGED — H-5's wrong-product concern preserved |
| Kill-adjacent files (6 files, 124 tests) | green except the two adjudicated: h5-CNC (the feature, fixed) + `test_fix181` LIMIT-vs-MARKET (**in the standing base set** — the known T3 item, untouched) |
| Manual hunk read | predicates + spare-logs + one shared CRITICAL emitter + `attempted -= 1`; control-flow changes = the two intended `continue` spares + the handled-symbols repositioning, nothing else |
| Full regression | see §4a stamp |
| Kill drill (PAPER, composed boot, mixed MIS+CNC+NULL book) | **MONDAY PC** — weekend gate blocks composition (measured in #1); PC only, VM stays `297b587` |
| Revert | `git revert 43043a2` restores product-blind behaviour exactly |

### 4a. Regression stamp — *(filled post-run)*

## 5. DEPLOY GATES (unchanged from the card — ⛔ nothing deploys from this build)

Mon 3-Aug evening, stacked with the flip flags, ONLY after: **(i)** Rama ratifies D1–D3
(D1 before Monday's close); **(ii)** Monday observation query CLEAN; **(iii)** Monday PC
gates — this item's kill drill + #1's composition boot + the calm regression confirm.
Plus now: **(iv) the #2b ruling above (reconciler third site: ride Monday or name it a
carry-pilot blocker).** GATE-Q7 flip-rider unchanged. Label discipline: this item can
only ever reach `<DEPLOYED>` + `dormant-armed` — `<VERIFIED LIVE>` requires a real
HARD_KILL with a mixed book, which has never occurred (HARD has never fired).
