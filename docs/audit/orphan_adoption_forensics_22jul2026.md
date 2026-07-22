# Orphan Adoption — Forensics: THELEELA, the June clusters, and the standing defect (22-Jul-2026)

**Scope:** Follow-up to `orphan_adoption_22jul2026.md`, which flagged THELEELA
(19-Jun) as "an unexplained system tracking-loss." This document resolves it,
ties the 15-Jun cluster to a single root cause, and writes up the standing
"untracked = human" defect as a queue entry.

**Read-only.** Nothing was changed, placed, or restarted. Sources: the live VM
main DB via `sqlite3 -readonly` (SELECT only), and **systemd journald**
(`journalctl -u trading-system.service`, retained back to ~10-Jun — the daily
`logs/*.log` files are age-pruned at 30 days, so 19-Jun's are gone; journald is
the surviving 19-Jun record). M-O5 untouched.

**Linchpin (from §1 of the prior batch):** Zerodha (LFL836) is system-only, so any
Zerodha-book position the system did not open is a **system** anomaly, never a
human trade.

---

## TL;DR — the headline is bigger than THELEELA

**The June "orphan adoption" events were the debris of a recurring exit-placement
bug, not human orders and not (at root) an in-flight fill race.** Three times in
June the system entered a position and then `order_placer._place_limit_triple_exits`
had its SL/TGT **rejected by Zerodha**, leaving the position unprotected →
**HARD_KILL** → the kill-flatten (plus the entry) left broker positions the local
book could not match → the `ORPHAN_ADOPTION` / `CapitalDrift` storms:

| Date | HARD_KILL time | Broker rejection of the exit order | Orphan debris |
|---|---|---|---|
| 15-Jun | 11:35:20 | "Market orders without market protection are not allowed via API" | AVL, HARIOMPIPE, SETL (UNRECOVERABLE) |
| 16-Jun | 10:00:22 | "Tick size for this script is 0.05 … enter price in the multiple of tick size" | GICRE, ITC (UNRECOVERABLE) |
| 19-Jun | 10:00:36 | "Your order price is higher than the current upper circuit limit of 503.35" | THELEELA (RECOVERABLE) |

All three `triggered_by=order_placer._place_limit_triple_exits`. **This bug has not
recurred since 19-Jun — 0 occurrences from 20-Jun to 22-Jul (33 days).** The
entry-side constraints that caused the rejections (near-circuit entries, tick
rounding, market-protection) were evidently handled in the following weeks (e.g.
the secondary-screener `REJECTED_CIRCUIT_PROXIMITY` gate now blocks near-circuit
entries — exactly THELEELA's case). **Classification: LATENT / historical.** The
system is currently healthy (`kill_switch_state = INACTIVE`).

**What FIX-181/182 actually did:** it changed how the *reconciler classifies the
debris* (in-flight routing + the "human/untracked" policy), which is why the
pre-fix events (15-16 Jun) are per-cycle `UNRECOVERABLE` storms while the post-fix
event (THELEELA 19-Jun) is a single `RECOVERABLE` row. **FIX-181/182 did not touch
the exit-placement root** — that was closed separately on the entry side.

---

## §A — THELEELA, 19-Jun: RESOLVED

**It was a real system trade, killed for lack of protection, then over-sold by the
kill-flatten and mislabeled an orphan for one cycle.**

### A1/A2 — the trade existed (it was never "no local trade")

`trades` row **`trd_f8014d0c6ca34ba189503bcd6b0fbddd`**: direction **LONG**,
strategy `positional_sector_rotation`, order_protocol `LIMIT_TRIPLE`, mode **LIVE**,
`created_at` **2026-06-19T10:00:25**, `entry_actual_price` **484.6**, status
**CLOSED_MANUAL**, `exit_time` 13:24:16, `exit_price` **483.65**, `exit_reason`
MANUAL, **`net_pnl` −0.95**, `recovered_flag` 0.

So THELEELA is **not** "a trade row never written." The 10:01 `ORPHAN_ADOPTION`
("no local trade") was a **transient one-cycle mismatch** — the broker's −1 short
could not be matched to the +1 long local record during the kill chaos.

### Timeline (journald + reconciliation_log + orders)

| Time (IST) | Event |
|---|---|
| 10:00:20 | `signal_processor` FIX-067: THELEELA live quote `None`, **entered on a stale webhook price** |
| 10:00:25 → 10:00:28 | ENTRY **BUY LIMIT MIS** +1 placed → **filled at 484.6** (position +1, LONG) |
| 10:00:28 | `order_placer` CRITICAL **`LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED`** — SL/TGT placement **raised**; position has no protection |
| 10:00:31 → 10:00:39 | an "EOD" **SELL LIMIT MIS** (478.85) placed → COMPLETE (a fallback/flatten sell) |
| 10:00:31 | (same boot) `CHECK2 INFLIGHT_ORPHAN: RCF qty=3` — a second unprotected position in the same storm |
| 10:00:32 | G5b crash-recovery **SL SELL MIS** placed (broker_order_id 260619170408225) → later **CANCELLED** |
| **10:00:36** | **HARD_KILL ACTIVATED** — reason: "LIMIT_TRIPLE exits failed after ENTRY filled: Zerodha rejected order: price higher than the upper circuit limit of 503.35" |
| 10:01:03 | `CHECK2 ORPHAN_ADOPTION`: broker **−1** avg 483.68, no local trade → disowned (**the mislabel**) |
| 13:24:16 / 13:24:26 | `CHECK1 MANUAL_CLOSE`: local trade OPEN but **broker flat** → closed at 483.65, **pnl −0.95** |

### A3 — short from over-exit? Yes, but not the RAMCOIND mechanism

The −1 is **over-sell residue of the HARD_KILL flatten**: the entry BUY (+1) plus
at least two competing SELLs during the kill (the "EOD" fallback sell that
completed, and the emergency broker-position flatten) drove the book net short.
The extra sell was **not linked to `trd_f80`**, which is precisely why the
reconciler saw an unmatched −1 and cried "orphan."

**Correction to the batch's A3 hypothesis:** THELEELA is **not** the same
mechanism as RAMCOIND. RAMCOIND (25-Jun) was a **duplicate-exit-leg oversell** (a
second exit SELL filling on an already-flat position) — and there was **no**
LIMIT_TRIPLE-exit-failed HARD_KILL on 25-Jun. THELEELA (19-Jun) was an
**exit-rejection → HARD_KILL → kill-flatten oversell**. Same *outcome* (a
system-created naked short mislabeled orphan), **different trigger**. So THELEELA
does **not** make RAMCOIND "the second occurrence"; they are two sub-shapes of the
same debris class, and RAMCOIND's dup-exit shape stands on its own.

*Honest boundary:* the exact order-level double-sell cannot be fully
reconstructed — `orders.qty_filled`/`avg_fill_price` are not reliably populated
for this trade, `order_execution_log` has no THELEELA rows, and the broker-side
flatten order was never linked to `trd_f80`. What is established: a real MIS long
existed; its exits were rejected; a HARD_KILL fired; the book went briefly −1;
it was reconciled flat the same day.

### A4 — the fate (the one that mattered most)

**Product MIS** (all three order legs are `product=MIS`). The broker was **flat by
13:24:26** ("broker=no_position"), well before the 15:20 MIS auto-square. Exposure
was **bounded and brief**, and the realised loss was **−0.95** (95 paise, ~all
slippage/charges on a wash). **The −1 short did not sit.** This is the benign
outcome the prior doc hoped for: observability noise, not a naked position left
overnight.

### A5 — sources exhausted

Resolved from: `reconciliation_log` (3 THELEELA rows), the `trades` row, the
`orders` legs, and journald 19-Jun (signal → entry → exit-failure → HARD_KILL →
manual close). Not "unexplained" — explained.

---

## §B — the 15-Jun cluster

### B1 — same root cause as 16-Jun? **Yes.**

Both are `order_placer._place_limit_triple_exits` HARD_KILLs (15-Jun 11:35:20
market-protection; 16-Jun 10:00:22 tick-size), i.e. **entry filled, exits rejected,
position unprotected → kill → orphan debris.** The 15-Jun symbols confirm the
shape: their `trades` rows are **FAILED/CANCELLED locally, with a live position at
the broker** —

- AVL: `trd_2e49…` LONG **FAILED** (11:34:16) + `trd_b6bc…` LONG **CANCELLED** (11:35:19); broker showed qty +1 @565.45
- HARIOMPIPE: `trd_accc…` LONG **CANCELLED** (11:35:18); broker qty +1 @440.40
- SETL: `trd_aa6d…` LONG **CANCELLED** (11:34:26); broker qty +2 @168.11

No local OPEN trade + a real broker position = the classic "broker filled, local
did not" → `ORPHAN_ADOPTION` (per-cycle, `UNRECOVERABLE`, 11:35→11:53, 60-72×
each). Same family as GICRE/ITC on 16-Jun.

### B2 — was there an incident on 15-16 Jun? **Yes.**

journald shows a **restart storm** the morning of 15-Jun — the service
Started/Stopped repeatedly (09:02:54, 09:03:49, 09:22:17, 11:20:00, 11:33:35),
and the G5b crash-recovery path **threw a traceback** (`order_reconciler.py:1445`,
on SULA `trd_7638…`) during it. Coupled with the recurring exit-placement
rejections, 15-16 Jun was a period of genuine instability that repeatedly left
positions unprotected and tripped kills. (Two additional kills in the window —
16-Jun 04:30 and 18-Jun 07:59 — were a **different** family: `circuit_breaker_api_failure`
from `order_monitor`, i.e. pre-market broker-auth failures, unrelated to the
exit-placement bug.)

### B3 — is "first occurrence 15-Jun" real, or a logging artifact? **Largely an artifact.**

The **entire `reconciliation_log` table begins 2026-06-15T11:34:16** — not just
`ORPHAN_ADOPTION` but the common `CAPITAL_DRIFT` too (earliest of each is the same
timestamp), right inside the 15-Jun restart storm. A reconciler that had been
running-and-persisting earlier would have left earlier `CAPITAL_DRIFT` rows; their
absence indicates the table's **persistence began (or the DB was re-seeded) on
15-Jun**. `schema_meta` shows only the current version (v44) and no migration
history, so it cannot date the table. **Conclusion:** the 15-Jun start is
substantially a **persistence-start artifact for this table** — it does not prove
the exit-placement root cause began on 15-Jun. Whether it occurred before 15-Jun
could only be answered from pre-15-Jun journald (a scan that did not complete in
the read-only window); stated here as not-established rather than assumed.

### B4 — priority

Lower than §A, and now closed: the cluster is the same exit-placement-rejection
root cause as 16-Jun and THELEELA, and has not recurred since 19-Jun.

---

## §C — the standing defect (queue entry, no fix designed)

**Name:** `ORPHAN_ADOPTION` "untracked = human" mislabel on a system-only account.

**Where:** `orders/order_reconciler.py:1496-1585` (`_check2_orphan_adoption`),
docstring `:1500-1509`.

**Condition under which it is wrong:** a **single-user, system-only broker
account** (this deployment — Zerodha is system-only; Rama trades AngelOne). There,
a broker position with no local trade is **never** a human/operator order; it is a
**system position the reconciler failed to match** (this batch showed the real
source: positions left by exit-rejection HARD_KILLs and kill-flatten over-sells).

**Consequence:** the reconciler labels it "Human/untracked … not managed by
system" and **disowns it** — does not adopt, protect, or flatten. On this account
that means a genuinely lost/unprotected **system** position can be left un-managed,
tagged as someone else's.

**Mitigations that DO exist (so this is latent, not a silent void):**
- Naked position (no protective stop at the broker) → **one WARNING/day**
  (`:1546-1568`) — but only when a notifier and the broker-order source are
  available; `_position_is_naked` fails **quiet** (`:1705-1706`) when it cannot
  confirm.
- The system's own over-sell shape → **Layer-3 flatten** CRITICAL (`:1528-1536`,
  the RAMCOIND fix).
- In practice the June cases were MIS and either kill-flattened or MIS
  auto-squared at 15:20, and each was reconciled-closed the same day — so realised
  harm was small (THELEELA −0.95).

**Why not just invert the assumption (and why this needs investigate-first, like
M-O5):** naively treating every untracked position as "adopt/protect/flatten"
would make the system **act on positions it does not understand** — the opposite
failure, and a directly capital-touching one. The right response is not obvious
(escalate-and-halt? adopt-and-protect? flatten?) and interacts with the kill path.
**No fix is designed here.** This entry records the defect, its condition, its
consequence, and its existing mitigations, for prioritisation alongside M-O4/M-O5.

**Also worth recording (never-proven paths):** `INFLIGHT_ORPHAN_FLATTEN` and
`SYSTEM_OVERSELL` — both CRITICAL — have **0 rows ever** in `reconciliation_log`.
They have never fired in production; treat them as *unproven*, not *known-good*.

---

## Corrections to the prior record

1. **"In-flight fill race" was the symptom, not the root.** The prior doc (and the
   `eod_squareoff.py:1363` comment) attribute GICRE 16-Jun to "an entry that filled
   at the broker while the local trade was still PENDING." True at the surface — but
   the *root* was the LIMIT_TRIPLE exit rejection → HARD_KILL that stranded the
   position. The unifying cause across 15/16/19-Jun is the exit-placement failure.
2. **THELEELA ≠ RAMCOIND mechanism** (see §A3). The batch's hypothesis that they
   share a mechanism (making RAMCOIND "the second occurrence") is **not supported**.
3. **THELEELA is fully explained** — remove it from the "unexplained" list.

---

## Not in scope / not changed

M-O5 (owns 15:30, untouched); M-O4 (inert, prior batch). No fix, design, deploy, or
order. Verifying that **each** of the three exit-rejection variants
(market-protection, tick-size, circuit-limit) is independently closed — and whether
"unprotected position → HARD_KILL" is the intended terminal response vs a
retry/repair — is a **separate thread**, not opened here. This document is
findings-only.

---

## Appendix — evidence

**HARD_KILL family (journald, 15-20 Jun), all `triggered_by=order_placer._place_limit_triple_exits`:**
```
2026-06-15T11:35:20  … Market orders without market protection are not allowed via API …
2026-06-16T10:00:22  … Tick size for this script is 0.05 … multiple of tick size …
2026-06-19T10:00:36  … order price is higher than the current upper circuit limit of 503.35 …
```
Distinct family (auth, not exit-placement): `2026-06-16T04:30` and `2026-06-18T07:59`
`circuit_breaker_api_failure` (`order_monitor`).
Recurrence of the exit-placement family **20-Jun → 22-Jul: 0**.

**THELEELA `trd_f8014d0c` orders:**
```
ENTRY  BUY  LIMIT  MIS  COMPLETE   placed 10:00:25.9  filled 10:00:28.6
EOD    SELL LIMIT  MIS  COMPLETE   placed 10:00:31.9  filled 10:00:39.5  price 478.85
SL     SELL SL     MIS  CANCELLED  placed 10:00:32.6                     trig 471.9
```

**reconciliation_log THELEELA (3 rows):** CRASH_RECOVERY_SL (10:00:37) →
ORPHAN_ADOPTION (10:01:03, −1 avg 483.68) → MANUAL_CLOSE (13:24:26, exit 483.65,
pnl −0.95).

**Current state:** `kill_switch_state = INACTIVE` (last trigger the routine 21-Jul
15:15 circuit-breaker SOFT_KILL, auto-cleared 22-Jul 08:15).

**Read-only access:** `sqlite3 -readonly` for all DB reads; `journalctl` read-only;
no `scripts/*.py --db`; nothing placed or restarted.
