# A paper GTT can now fire — what that unblocks, and what it still does not

**Date:** 2026-07-26 · **Branch:** `hold-check1-w8-26jul` · ⛔ **UNPUSHED**
**Changed:** `broker/zerodha_adapter.py` — **production code, paper-only branch.**
Companion: `paper_fidelity_gaps_26jul2026.md` (the sweep this came out of).

---

## Why this mattered

Slice 2.5's stated gate is *"built and paper-proven."* **Every CNC exit runs through a
GTT.** Nothing in the codebase ever wrote a paper GTT status other than `"active"`, so a
GTT could never fire in paper — which made `CncGttMonitor`'s primary path
(triggered + flat → `GTT_EXIT`) and its F6 re-protect branch unreachable by a running
paper session. **The gate could not cover the exit path it exists to gate.**

The tell was already in the suite: the 25 tests credited to `CncGttMonitor` and the
FIX-183 prepass reach the state by writing the adapter's private dict —

```python
env.adapter._paper_gtts[str(gid)]["status"] = "triggered"
```

**A test that must reach past the public API is naming a gap.**

---

## What was built

Two additions, both inside `if self._paper` branches:

- **`_gtt_triggered_leg(trigger_values, ltp)`** — a **pure** predicate returning `0` (SL),
  `1` (TGT) or `None`. The trigger *rule* is the part that must be right, so it is
  separated from all I/O and can be driven to any price with no quote provider, clock or
  thread.
- **`_paper_settle_gtt_triggers()`** — called at the top of `get_gtt()` and `get_gtts()`.
  Snapshots the store, fetches LTP **outside** the lock, then re-locks to flip
  `active → triggered` and record the observed price.

**No new machinery.** It reuses `_fetch_ltp()` — already built for paper LTP-gated fills —
which returns `None` (never `0.0`) on no-data. That FIX-001 rule is load-bearing here: a
missing quote read as `0.0` would sit below every stop and fire **every SL in the book**.

**It is reachable in a real paper session, not just in tests.** `main.py:2012` injects
`_make_paper_quote_provider()`, which fetches **real Kite quotes** using the token file.

### Deliberate design choices

| choice | why |
|---|---|
| **Status flip only — places no order** | Placing the leg from a read path would take `_paper_fills_lock` while holding `_paper_gtts_lock` and spawn a thread from a getter. Not worth the re-entrancy. |
| **Bounds INCLUSIVE** | Zerodha fires when LTP *reaches* the trigger. An exclusive compare would silently under-protect at exactly the stop. |
| **SL checked first** | A poll can observe a price outside *both* bounds where a tick stream would have seen one crossed first. When the order is unknowable, the protective leg must win — guessing TGT on a collapsing price books a profit that never existed. |
| **Malformed condition never fires** | Fail closed: an unparseable trigger list must not read as a crossing. |
| **One quote per symbol per pass** | The read path runs every reconcile cycle; it must not fan out one network call per GTT. |

---

## B3 — the fidelity question: what is faithful, and what is not

**Achievable and now achieved:** trigger-on-LTP-cross with Zerodha's inclusive semantics,
OCO bound ordering, terminal `triggered` (no path back to active), and the
`triggered ≠ filled` distinction.

⛔ **NOT achievable, and each is pinned by a test so it cannot be quietly assumed away:**

1. **Overnight / while-down triggering — the entire point of a GTT.** Zerodha evaluates
   server-side on every tick even while we are stopped. This evaluates only when our own
   code asks, so a paper GTT **cannot fire while the paper service is down.**
2. **Tick-level path.** We poll; price can cross and return between polls. Paper
   therefore **under-triggers** relative to the broker — the safe direction, but it means
   paper cannot prove a GTT *would* have fired.
3. **Trigger ≠ fill.** Zerodha places a LIMIT leg on trigger, which may not fill. This
   places nothing, leaving the holding intact — which is exactly the F6 shape. Reading a
   triggered paper GTT as "position closed" would be the passes-wrongly failure this
   change is guarding against.

---

## B4 — does this unblock Slice 2.5? **Partly. Say so precisely.**

⚠️ **My first read of this was wrong and I checked it rather than shipping it.** I
concluded that `held` came only from `get_holdings()` — which is permanently empty in
paper — and therefore that every paper CNC GTT would be torn down as an orphan on cycle 1.

**`_gather()` (`cnc_gtt_monitor.py:353-367`) sums holdings AND CNC *positions*.** Paper
positions are a genuine simulation (`_paper_positions`, fed by `_synth_fill`), and since
`f7eedd3` they carry the order's **real product** — so a paper CNC position reports
`product="CNC"` and is counted. ⭐ **`f7eedd3` and this change are complementary: without
the product fix, a paper CNC position would report `MIS` and `_gather` would skip it.**

**So, corrected:**

| lifecycle | paper-reachable now? |
|---|---|
| CNC entry → position → GTT placed → **healthy** (`held == row_qty`) | ✅ yes |
| GTT fires while we are running → **`GTT_EXIT`** | ✅ **yes — this change** |
| GTT fires with the position still open → **F6 re-protect** | ✅ **yes — this change** |
| Position closed externally → orphan-active-GTT → delete + finalise | ✅ yes |
| **Overnight carry: position → holding across a restart** | ⛔ **NO — still blocked** |

**The remaining blocker is the carry, and it is not the GTT.** Paper position state lives
in memory and the service restarts nightly (17:35 stop → 08:15 start), while
`_paper_holdings` has **no production writer** — so on Tuesday morning a paper CNC carry
has neither a position nor a holding and simply vanishes. The **Mon→Tue live pair remains
irreducible**, exactly as recorded.

> **Answer: this removes the intraday CNC blocker in full. It does not remove the
> overnight one, and nothing built here should be read as evidence about a carry.**

---

## B6 — the proof the fix is real

`test_paper_gtt_triggers.py::test_the_triggered_state_no_longer_needs_the_private_dict`
produces the identical observable by **moving the price and reading the public path**, with
no hand-written state anywhere. **A test that no longer has to cheat is the difference
between a simulation and a stub.**

### Evidence

- **17 new tests.** RED-first was measured twice, because the first attempt was weak:
  removing the whole change gives a *collection error* (the symbol is absent), which proves
  nothing about behaviour. Neutering only the settle pass gives the informative result —
  **5 of 17 fail on behaviour**, exactly the five asserting that triggering happens. The 12
  that stay green are the pure predicate, the invariants, and the two limit tests, which
  **must** be green both ways because they assert what does *not* happen.
- **Blast radius 133/133** (`cnc_gtt_monitor`, `cnc_gtt_adoption_fix183`,
  `cnc_gtt_slice25_p2`, `zerodha_adapter`, plus the new file).

### ⚠️ Three existing fixtures had to change — and the reason is the point

`test_unknown_human_gtt_left_alone`, `test_50_cap_warning` and
`test_unreadable_active_gtt_warns_once_no_insert` seeded **active** GTTs at `[100,110]` or
`[1,2]` while the fixture's quote provider serves **337.0 for every symbol**. Those are
states production cannot produce — an untriggered GTT three times below the traded price —
and they only survived because a paper GTT could never fire. Re-pointed to `[320, 360]`,
matching the same file's own `_rowless_gtt` convention around 337.

> **The fixtures were asserting against a state the real system cannot reach.** Same class
> as the 25-Jul fixture findings: check the fixture against production shape.

---

## ⚠️ Production-code notice

This changes `broker/zerodha_adapter.py`. The changed code is **doubly paper-only** —
`_paper_settle_gtt_triggers()` returns immediately unless `self._paper`, and both call
sites are already inside `if self._paper:` branches, with a test pinning the live no-op.
Same structural argument that justified shipping `f7eedd3` into this branch.

**It does not alter Tuesday's single variable.** v45 is the migration; this touches no
schema, no capital path, and cannot execute in live mode.
