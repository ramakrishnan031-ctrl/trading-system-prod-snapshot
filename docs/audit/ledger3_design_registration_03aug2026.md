# LEDGER #3 — DESIGN REGISTRATION (03-Aug-2026)

**⛔ THIS IS A REGISTRATION, NOT AN IMPLEMENTATION.** No code was written, no design was
started, nothing is authorised by this file. It exists so that when #3 *is* taken up, the
implementer starts from **the register** rather than from a chat transcript — which is the
whole point of `campaign_practices.md` §0 (triggers **1** design completion, **3** rejected
option and why, **4** sequencing).

**Scope owner:** debt-ledger **#3** — *"Fill/cancel seam truth"*, IA-P5-01 · IA-P5-02.
⛔ **No new register row. 231 stands.** Everything below sits inside row 3's existing scope.

---

## 0. ⭐ PROVENANCE — and the distinction a future reader must not collapse

These six are **red-team DESIGN acceptances from ChatGPT**, recorded as such.

⛔ **They are NOT filed under practice §G2, and doing so would be a misreading.** §G2
restricts ChatGPT on **Rama's** decisions — deploy slots, gates, ride-or-hold, whether an
item ships. **Architecture review is precisely its remit**, and the practices file already
says so in G2's own text: *"its red-teaming has been genuinely valuable (the #2c-R Option-1
direction, the Q1-Q8 constraints), and that is precisely why the line matters."*

⇒ **R1-R5 (02-Aug) were Rama's and required his ratification. These six are not that
class and did not.** A future reader who collapses the two will either wrongly discount
sound architecture review, or wrongly promote advice into authority. Both are failures the
G1/G2 pair exists to prevent.

**What this file does NOT claim:** that any of the six has been measured. Item 2 in
particular is explicitly a *direction to test*, not a finding.

---

## 1. ⭐ ENDPOINT FRAMING — ACCEPTED

**IA-P5-02 (the cancel-race) and the CHECK6 route are two INDEPENDENT paths that arrive at
the SAME endpoint: the system's own position filed as a `HUMAN_ORDER`.**

**The reasoning, recorded — not just the conclusion:**

- The endpoint is a *state*, not an *event*: a live broker position that the system no
  longer believes it owns. Whatever produced that state, the consequence is identical —
  the position is unbooked, and nothing downstream distinguishes how it got there.
- The cancel-race reaches it by losing a race on a cancel.
- The CHECK6 route reaches it by a **timer**: FIX-B marks a `PENDING_FILL` trade FAILED and
  releases its reservation on the 3rd consecutive cycle, after which the caller's
  `if inflight:` goes False and the same position routes to `_check2_orphan_adoption`,
  which can label it `HUMAN_ORDER`.
- ⇒ **Fixing either path alone leaves the endpoint reachable by the other.** A fix that
  closes the race still leaves the timer; a fix that retunes the timer still leaves the
  race.

⛔ **Any design that treats these as two separate bugs is wrong by construction** — it will
produce a fix that measures as successful against the path it targeted while the endpoint
remains reachable. That is the *"a green check is evidence only if it could have been red"*
failure (§M2), pre-registered here so it is not discovered late.

**Cited, not re-derived:**
`docs/MASTER_PENDING_01-Aug-2026.md` §B.1 row 3 @ `07c7fc0` ·
`docs/audit/reconciler_product_filter_build_02aug2026.md` §R4 @ `07c7fc0`

---

## 2. ⭐ DIRECTION ACCEPTED — separate "release the capital reservation" from
##    "disown the position"

**Recorded as the direction to TEST at Step 1. ⛔ NOT as a settled mechanism.**

Today these are **one action**. FIX-B's single transition does both at once: it marks the
trade FAILED (disowning the position) *and* releases the capital reservation. The
acceptance is that **the collapse of two transitions into one may be the actual root
confusion** — more so than the timing of the release, which is where attention has gone.

Two different questions live inside the one action:

| transition | the question it answers |
|---|---|
| release the reservation | *may this capital be re-committed to something else?* |
| disown the position | *does the system still consider itself the owner of this position?* |

They are not the same question and they do not obviously have the same correct answer at
the same moment — a position can be live at the broker (so: still ours) while its
reservation is stale (so: releasable), which is exactly the state CHECK6 produces.

⛔⛔ **WHETHER THEY ARE SEPARABLE IN THIS SYSTEM IS A MEASUREMENT NOBODY HAS MADE.** It is
recorded here as the **first thing Step 1 must establish**, not as a conclusion. If they
turn out to be structurally welded (one status field carrying both meanings, one writer,
one consumer contract), that is itself the finding and the design changes shape.

⚠️ Registered against a known trap: this system has a documented history of **one concept
carried by multiple authorities** (debt-ledger #7, *"held" ×4, status ×34 sites*). Step 1
should expect the inverse here — **two concepts carried by one authority** — and must not
assume separability just because it would be convenient.

---

## 3. THE SPLIT — ACCEPTED

**#3a — `orders.qty_filled` zeroed on COMPLETE orders.** Standalone, low-risk, **ships
alone**.

⭐ **The measured width (M1 — stated in the same sentence as the number):** all-time,
`orders` holds **805** rows = {COMPLETE **405**, CANCELLED **400**}. Of the 405 COMPLETE
rows: `qty_filled > 0` on **0/405**, `avg_fill_price` NOT NULL on **0/405**, `filled_at`
NOT NULL on **405/405** — so the writer provably ran on every one of them and wrote the
quantity as zero in the same statement. The 400 CANCELLED rows carry zero **correctly**;
**405 is the wrongly-zeroed population, not 805.**

⚠️⚠️ **THE AUDIT'S "~2 LINES" IS AN ESTIMATE, NOT A MEASUREMENT.** Recorded explicitly
because **every item this campaign touched grew on contact**: #2 scoped one doc and found
three · `EMERGENCY_FLATTEN_PRODUCTS` claimed four readers and had five · #2 → #2b → #2c →
#2c-R → #2d. ⛔ **The estimate must not be quoted as a size commitment**, and #3a's real
cost is unknown until its own Step 1.

**#3b — the `HUMAN_ORDER` endpoint**, covering **BOTH paths together** (per §1).

⛔ **Do NOT bundle #3a into #3b merely because they share a ledger row.** They share a row
because they share a *subject* (fill/cancel seam truth), not because they share a fix.
⛔ **Do NOT split the two paths inside #3b** — that is the §1 error restated.

**Cited:** the "2-line fix for one half" estimate — `docs/MASTER_PENDING_01-Aug-2026.md`
§B.1 row 3 @ `f9582e9` · the 405 evidence — `docs/audit/integrity_audit_2026.md` §Phase-5
(IA-P5-01, `:1394-1400`, `:1540-1543`) @ `5420709`

---

## 4. R-4 RULED — FIX-FORWARD BY DEFAULT

**Backfilling the 405 historical rows is NOT part of #3a.**

It is considered **only** on operational evidence that those rows are **consumed by current
production logic** — and even then it is a **SEPARATE, EXPLICITLY APPROVED ACTIVITY**,
because:

- it **touches the live DB**, which is a different risk class from a code fix;
- it is a **write to historical evidence** — the class this campaign has twice refused to
  do casually (the 141 fabricated `innings` rows were marked VOID and **kept**, never
  deleted; the forward-shadow JSONL must never be hand-produced);
- ⛔ a backfill that is wrong is **indistinguishable from correct data afterwards**, which
  is the property that makes it a different decision, not a bigger one.

⇒ **Default: fix forward. Backfill only on evidence + its own approval.**

---

## 5. ⭐ R-3 CONSTRAINT — BINDING ON ANY FUTURE CHECK6 WORK

**CHECK6 currently BOUNDS two shipped items.** `_check6_orphan_orders` (wired,
`main.py:2739`) marks the trade FAILED and releases the reservation on the **3rd
consecutive cycle**, which is what turns both of these into **~3 CRITICALs instead of an
unbounded stream**:

| shipped item | what CHECK6 bounds |
|---|---|
| **#2b** — the CNC spare (`INFLIGHT_ORPHAN_SPARED_DELIVERY`, `6495baa`) | its re-alert cadence |
| **#2c-R** — the CO refusal (`INFLIGHT_ORPHAN_REFUSED_CO`, `42db913`) | its re-alert cadence |

⛔⛔ **THEREFORE: ANY CHECK6 REDESIGN MUST EXPLICITLY DOCUMENT WHETHER IT CHANGES (a) ALERT
COUNT, (b) ESCALATION BEHAVIOUR, or (c) REFUSAL SEMANTICS** — and state the answer for
**each of #2b and #2c-R by name**, so that neither silently regresses.

**Why this is a constraint and not a note:** the coupling runs the wrong way for
discovery. Nothing in `#2b`'s or `#2c-R`'s code mentions CHECK6; the bound is an emergent
property of cycle ordering (CHECK6 runs *after* CHECK2). A CHECK6 author retuning a counter
would have **no local signal** that two other items depend on it. ⇒ the constraint has to
be written where they will *land*, not where it was *discovered*.

**Placed at three sites so a future CHECK6 card cannot miss it** (M1 — the same
one-site-is-not-permanent lesson as ledger #8's three docs):
1. this record (§5);
2. `docs/MASTER_PENDING_01-Aug-2026.md` §B.1 row 3 — the register index a card is written from;
3. `docs/audit/reconciler_product_filter_build_02aug2026.md` §R4 — the section that measured the bound.

**Cited:** the 3-cycle bound, first recorded —
`docs/audit/reconciler_product_filter_build_02aug2026.md` §R4 @ `bb25fa6`;
extended to #2b and forward-pointed @ `07c7fc0`.

⚠️ **Not duplicated here:** the *content* of the bound (the cycle-ordering trace, the
`if inflight:` transition, the once-a-day suppression) lives in §R4 and is **cross-linked,
not restated** — per the card's own anti-duplication gate.

---

## 6. QUEUE ORDER — ASKED, AND ANSWERED: **UNCHANGED**

**#8b Step 2 → #2d → #3.** Reordering was considered. **No reason to reorder was found.**

Recorded because *"we considered reordering and didn't"* is a **§0 trigger (6 — review
conclusion, including 'nothing to change')**. A review that leaves no trace cannot later be
relied on as having happened, and an unrecorded non-decision gets re-asked at the next
opportunity — usually under time pressure.

⚠️ **MEASURED WHILE WRITING THIS, and recorded rather than smoothed: the queue order was
not written down ANYWHERE before this file.** Repo-wide search over `*.md` + `*.txt` for
the sequence found **nothing** — it existed only as shared understanding. That is the exact
condition §0 was adopted to end, so it is worth naming.

⚠️ **Related honesty note on `#2d`:** it is **carded but has NO register row.** Repo-wide,
`#2d` appears only in `docs/campaign_practices.md:93` (the G3 disclosure chain),
`:290-293` (inside AR5), and `PATHS.md:112`. Its substance is
`docs/audit/reconciler_product_filter_build_02aug2026.md` §R7 — *`kill_switch`'s three CO
sell sites*. ⛔ **Stated, not fixed:** creating a row is a register change and would move
the 231, so it is left as-is and disclosed here.

**Cited:** `docs/campaign_practices.md` §G3 @ `0b2ab45`

---

## 7. ⛔ WHAT THIS FILE DOES NOT AUTHORISE

- **No implementation of #3, #3a or #3b** — not now, and not as a follow-on to this file.
- **No CHECK6 change.**
- **No backfill.**
- **No register row**, no change to the 231, no change to queue order.

**Still gated, unchanged:** #8b Step 2 (after Monday's critical path, per R5(b)) · #2d ·
BK-8 implementation · the `--cleanup-*` flags · **all of #3**.

---

## 8. CITATION INVENTORY — every SHA determined by `git log -S`, never from memory (§M3)

| claim | citation | SHA verified |
|---|---|---|
| CHECK6 route registered to #3 | `MASTER_PENDING_01-Aug-2026.md` §B.1 row 3 | `07c7fc0` ✅ |
| forward pointer / endpoint shared | `reconciler_product_filter_build_02aug2026.md` §R4 | `07c7fc0` ✅ |
| 3-cycle bound, first recorded | `reconciler_product_filter_build_02aug2026.md` §R4 | `bb25fa6` ✅ |
| the "~2 lines" estimate | `MASTER_PENDING_01-Aug-2026.md` §B.1 row 3 | `f9582e9` ✅ |
| 405/805 qty_filled evidence | `integrity_audit_2026.md` Phase 5 (IA-P5-01) | `5420709` ✅ |
| G3 chain naming `#2d` | `campaign_practices.md` §G3 | `0b2ab45` ✅ |
| #2b CNC spare (bounded item) | code commit | `6495baa` ✅ |
| #2c-R CO refusal (bounded item) | code commit | `42db913` ✅ |

All eight resolve to real commits — checked with `git cat-file -t` **before** this file was
written, not after.
