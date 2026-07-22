# Orphan Adoption — Findings (22-Jul-2026)

**Scope:** §3 of the 22-Jul VS-Code batch. Read-only investigation of the
order-reconciler's "orphan adoption" path and the June `ORPHAN_ADOPTION`
events. **Nothing was changed, placed, or restarted.** All DB access was a
single `sqlite3 -readonly` SELECT batch against the live VM main DB
(`data_store/trading_system.db`); no service, EOD path, or order was touched.

**Linchpin invariant (from §1, established this batch):** the Zerodha account
(LFL836) is **system-only** — Rama trades manually on AngelOne and last touched
Zerodha ~3 years ago. Therefore **any position in the Zerodha book the system
did not open is an anomaly, not a manual trade.** This single fact flips the
central assumption of the reconciler's orphan policy, and is what makes §3.3
the lead finding.

---

## TL;DR

1. **§3.3 (LEAD) — "ORPHAN_ADOPTION" is a misnomer, and its human-order policy
   is inverted on a system-only account.** `_check2_orphan_adoption` does **not
   adopt**: post-FIX-182 it treats a broker position with no local trade as an
   *operator/human* order and deliberately does **not** manage it (no adopt, no
   protect, no flatten — log once/day). On a system-only broker, "untracked =
   human" is false: an untracked position is a **system tracking-loss**, and the
   reconciler mislabels it as someone else's and walks away. Two real post-fix
   instances exist (THELEELA 19-Jun, RAMCOIND 25-Jun); by the system-only
   invariant **neither was human**. RAMCOIND was later understood as the
   system's own dup-exit oversell (prompted the Layer-3 fix); **THELEELA remains
   unexplained.** Partial mitigations exist (naked→WARNING once/day;
   oversell→flatten) but leave a real residual gap. LATENT observability/safety
   finding, low historical frequency, small qty; documented, not fixed.

2. **§3.1 — The system does NOT adopt a position it did not open.** Both CHECK-2
   handlers only ever complete/flatten the system's **own** in-flight trade; a
   truly untracked position is explicitly left unmanaged. No "adoption of a
   foreign position" path exists.

3. **§3.2 — Orphan handling is NOT a second path into M-O4.** No orphan/untracked
   position (CNC or otherwise) can reach M-O4's phase-2 MIS squareoff. The
   phase-2 map iterates local trades only; the only broker-position EOD path (the
   residual sweep) is filtered to **MIS/CO + system-owned** and skips everything
   else. A not-opened CNC position is excluded twice over.

4. **§3.4 — The June events, authoritatively counted.** The prior "20 events, all
   16-Jun, GICRE+ITC" was a **severe undercount** — those 20 were only the alert
   *delivery failures* during the 16-22 Jun Telegram outage (`failed_alerts.log`).
   The authoritative persistent store (`reconciliation_log`, 180-day retention)
   holds **2,637 `ORPHAN_ADOPTION` rows across 8 symbols on 4 trading days
   (15-25 Jun), and NONE since 25-Jun** (27 days clean). The bulk (2,635) is
   pre-FIX-182 per-cycle spam on 15-16 Jun.

---

## §3.1 — Can the system adopt a position it did not open?

**Answer: No.** CHECK 2 (`order_reconciler.py:856-882`) routes a broker position
with no OPEN/PARTIAL local trade to one of two handlers:

- **`_check2_inflight_orphan` (`:1708-1767`)** — reached only when a **local
  in-flight trade (PENDING/PENDING_FILL) exists for the symbol** (`:874-880`).
  This is the system's **own** entry whose LIMIT filled at the broker before the
  local fill was recorded (the GICRE race, FIX-181). It creates **no new trade
  row**. Two outcomes: HARD_KILL active → flatten as a backstop
  (`INFLIGHT_ORPHAN_FLATTEN`, CRITICAL); otherwise → **no action**, the normal
  fill path (order_monitor) completes the *existing* trade (`INFLIGHT_ORPHAN`,
  COSMETIC). So the only "adoption" here is the fill path finishing a trade the
  system itself placed.

- **`_check2_orphan_adoption` (`:1496-1585`)** — reached only when there is **no
  local trade of any status** (`:1500-1503`). Per FIX-182 the system "manages
  system trades only": it does **not adopt, protect, or flatten** (`:1504-1505`).
  It logs once/day, adds the symbol to the human-order set (which widens the G3
  capital-drift tolerance), and — only if the position is *naked* — raises one
  WARNING/day (`:1546-1568`).

Neither handler ever ingests a position the system did not open into the trades
table. Confirmed empirically: every persisted `ORPHAN_ADOPTION` row has
`trade_id = NULL` and `action_taken` of "not adopted" / "manual intervention
required" (see §3.4). **No adoption of a foreign position occurred, ever.**

---

## §3.2 — Is orphan adoption a second path into M-O4?

**Answer: No.** M-O4 is the EOD phase-2 defect where a human **CNC** position
present at 15:17 could receive a spurious MIS MARKET squareoff (missing product
filter, `eod_squareoff.py:1560-1567`, established in the M-O4 finding). For orphan
handling to be a *second* path, an orphan/untracked (possibly CNC) position would
have to reach that phase-2 map. It cannot:

- **The phase-2 map iterates LOCAL trades only.** The code's own comment
  (`eod_squareoff.py:1361-1367`) states: "the loop above only acts on local
  OPEN/PARTIAL trades. A position can be live at the broker with NO local OPEN
  trade …". An orphan has no local trade → it never enters the phase-2 map,
  regardless of M-O4's internal filter bug.

- **The only EOD path that reads broker positions is the residual sweep**
  (`_sweep_residual_broker_positions`, `:1379-1446`, FIX-182). It is filtered to
  `product in ("MIS","CO")` (`:1408-1413`) **and** `symbol in system_symbols` —
  a local trade for the symbol exists **today** (`:1417-1446`). A residual with
  no local trade today is **skipped as human/untracked** (`:1439-1446`). The
  separate FIX-015 broker-qty filter (`:1036-1045`) likewise excludes CNC/NRML
  ("DELIVERY positions are never touched").

**A not-opened CNC position is therefore excluded twice** — once by the MIS/CO
product filter, once by the no-local-trade skip. And the in-flight adoption path
(§3.1) only ever completes the system's own **MIS** trade (system entries are
`force_intraday`); it never produces a CNC row. **There is no orphan route into
M-O4's phase-2 squareoff.**

---

## §3.3 — LEAD: the system-only mislabel

`_check2_orphan_adoption`'s docstring (`:1500-1509`) is explicit: reaching it
"means the system has no record of this symbol at all — it is an operator-placed
(human) order in Kite. Per Rama's decision the system manages only system trades:
we do NOT adopt, protect, or flatten it."

**On a system-only account this assumption is inverted.** Zerodha is system-only
(§1), so a position the system has no record of is **not** a human order — it is a
position the **system opened and then lost track of** (a fill the local state
never recorded, an entry whose trade row was deleted/mismarked, or a residual
from a crashed exit). The reconciler labels it "Human/untracked … not managed by
system" and disowns it. **A genuinely lost system position can thus be left
un-managed and unprotected, tagged as someone else's problem.**

This is not merely theoretical — it has happened, and the persistent log shows the
mislabel in the exact words:

- **RAMCOIND, 25-Jun 10:13, qty −1, avg 334.00** — persisted as `RECOVERABLE`,
  description *"Human/untracked broker position RAMCOIND qty=-1 … no local trade —
  not managed by system"*, action *"logged once; added to human-order set; not
  adopted."* This is the documented **system dup-exit oversell** (a duplicate SELL
  exit leg filled → a naked short). The Layer-3 fix (`_detect_system_oversell`,
  `:1528-1536`, "RAMCOIND fix, 25-Jun") was written **in response**, to catch this
  shape and flatten it as `SYSTEM_OVERSELL`/CRITICAL instead of disowning it. The
  25-Jun row is the pre-fix behavior: **the system's own naked short, labeled a
  human order.**

- **THELEELA, 19-Jun 10:01, qty −1, avg 483.68** — persisted as `RECOVERABLE`,
  same "Human/untracked … not adopted" wording. **Unexplained** — there is no
  code comment, test, or incident note tying it to a known cause. By the
  system-only invariant it cannot have been a manual trade, so on 19-Jun the
  system held a −1 THELEELA short at the broker it had no record of and walked
  away from it. **This has never been investigated.**

**What partially covers the gap (so it is not a silent void):**

- If the untracked position is **naked** (no protective stop at the broker) and a
  notifier + broker-order source are available, one WARNING/day fires
  (`:1546-1568`).
- If it matches the **oversell** shape (inverts a recent system exit), Layer-3
  flattens it CRITICAL (`:1528-1536`).

**Residual gap that remains:** a lost system position that (a) still has a resting
protective stop at the broker (→ `is_naked=False`), or (b) whose nakedness cannot
be confirmed because the broker order source is unavailable (`_position_is_naked`
returns `False` on any error, `:1705-1706` — fail-quiet), or (c) does not match
the oversell shape, is **silently logged as "human/untracked, not managed"** and
left to ride. The EOD residual sweep does not rescue it either: with no local
trade today it is skipped as human (`eod_squareoff.py:1439-1446`).

**Note — a regression in loudness across the fix boundary.** Before FIX-182
(15-16 Jun) an untracked position was tiered `UNRECOVERABLE` with action
*"CapitalDriftDetected published; manual intervention required"* (see §3.4). For a
system-only account that older, louder treatment was arguably **closer to
correct** than the current "human order, not my problem." FIX-181 correctly split
off the in-flight race (that half of the noise was genuine and is now handled
silently by the fill path); but FIX-182 also softened the response to the
*genuinely untracked* remainder — which, on this account, is exactly the case that
warrants attention.

**Classification:** LATENT observability/safety finding. Reachability is real
(2 confirmed post-fix instances) but low-frequency and, historically, small
(qty −1, no documented capital harm). Reported, not fixed (batch is read-only).
The correct framing of a fix — recognise that on a system-only broker "untracked"
implies "system anomaly," and escalate/track accordingly rather than disown — is
noted only to scope the finding; **no fix is proposed here.**

---

## §3.4 — The June `ORPHAN_ADOPTION` events: authoritative census

**Source:** `reconciliation_log` (RC10 persists every **non-COSMETIC**
reconciliation action, `order_reconciler.py:36-37,1005-1022`; retained 180 days,
`scripts/db_retention.py:68`). Window present in the live DB:
`2026-06-15T11:34 → 2026-07-20T13:52`, 7,787 rows total. **16-Jun is well inside
the 180-day window — this is not a pruning artifact.**

### Correction to the prior "20 events" figure

The earlier resume note recorded "20 `ORPHAN_ADOPTION`, all 16-Jun, GICRE+ITC,
~10× each, via `failed_alerts.log`." That file records **alerts that failed to
deliver** — during the 16-22 Jun Telegram outage. It is a tiny, biased sample.
The authoritative persisted count is **2,637 rows across 8 symbols on 4 days.**
The "20" should not be quoted as the event count.

### Full breakdown

| Date (IST) | Symbol | qty | Tier | Rows | Era |
|---|---|---|---|---|---|
| 2026-06-15 | AVL | — | UNRECOVERABLE | 67 | pre-FIX-182 |
| 2026-06-15 | HARIOMPIPE | +1 | UNRECOVERABLE | 72 | pre-FIX-182 |
| 2026-06-15 | SETL | — | UNRECOVERABLE | 61 | pre-FIX-182 |
| 2026-06-16 | AGARIND | −1 | UNRECOVERABLE | 1 | pre-FIX-182 |
| 2026-06-16 | **GICRE** | — | UNRECOVERABLE | **1,246** | pre-FIX-182 |
| 2026-06-16 | **ITC** | — | UNRECOVERABLE | **1,188** | pre-FIX-182 |
| 2026-06-19 | THELEELA | −1 | RECOVERABLE | 1 | post-FIX-182 |
| 2026-06-25 | RAMCOIND | −1 | RECOVERABLE | 1 | post-FIX-182 |
| | | | **Total** | **2,637** | |

**Two regimes, split by the FIX-181/182 deploy (~18-19 Jun):**

- **Pre-fix (15-16 Jun): `UNRECOVERABLE`, per-cycle spam.** GICRE (1,246) and ITC
  (1,188) are the **same positions re-flagged on every reconciliation cycle all
  day** — there was no once/day suppression and no in-flight routing yet, so the
  action was "CapitalDriftDetected published; manual intervention required" each
  cycle. GICRE/ITC are the documented **16-Jun in-flight fill race** (an entry
  that filled at the broker while the local trade was still PENDING —
  `eod_squareoff.py:1363-1364`). AVL/HARIOMPIPE/SETL (15-Jun) and AGARIND (16-Jun)
  are single-share untracked positions flagged the same loud way.

- **Post-fix (19, 25 Jun): `RECOVERABLE`, once/day.** THELEELA and RAMCOIND are a
  single row each — FIX-182's once/day suppression working as designed. Both are
  the §3.3 mislabel (see above).

### What did NOT fire

- **`INFLIGHT_ORPHAN_FLATTEN` (CRITICAL): 0 rows ever.** The kill-time in-flight
  flatten backstop has never triggered in production.
- **`SYSTEM_OVERSELL` (CRITICAL): 0 rows ever.** The Layer-3 oversell flatten has
  never fired since it was added (either the fix holds and no dup-exit has
  recurred, or none has occurred). RAMCOIND (25-Jun) predates it.
- `INFLIGHT_ORPHAN` (COSMETIC) is **not persisted** (RC10 skips COSMETIC), so its
  in-production frequency cannot be read from `reconciliation_log`.

### Recurrence

**Not recurring.** The last `ORPHAN_ADOPTION` of any kind was **25-Jun** (27 days
ago). This is a strong statement, not a log-pruning inference: `reconciliation_log`
retains 180 days and its window extends to 20-Jul with zero orphan rows after
25-Jun.

### Context (other reconciler activity, same window)

| check_name | rows | first | last |
|---|---|---|---|
| CAPITAL_DRIFT | 4,183 | 2026-06-15 | 2026-07-06 |
| ORPHAN_ADOPTION | 2,637 | 2026-06-15 | 2026-06-25 |
| CRASH_RECOVERY_SL | 915 | 2026-06-15 | 2026-07-14 |
| MANUAL_CLOSE | 37 | 2026-06-15 | 2026-07-20 |
| POSITION_GREW | 14 | 2026-06-16 | 2026-06-16 |
| MISSING_EXITS | 1 | 2026-07-01 | 2026-07-01 |

(These are out of scope; listed only to show the reconciler is active and healthy,
and that the orphan family specifically has been quiet since 25-Jun.)

---

## Answers to §5 ("what done means")

- **Can the system adopt a position it did not open?** No — from code (§3.1) and
  confirmed empirically (every orphan row: `trade_id` NULL, "not adopted").
- **Could an adopted CNC/NRML position reach M-O4's phase-2 map?** No — the map is
  local-trade-only; the residual sweep is MIS/CO + system-owned only; a not-opened
  CNC position is excluded twice (§3.2).
- **The June `ORPHAN_ADOPTION` events — counted and explained?** Yes — 2,637 rows,
  8 symbols, 4 days (15-25 Jun), two regimes across the FIX-181/182 boundary, none
  since 25-Jun (§3.4). Root causes: GICRE/ITC = in-flight fill race; the
  single-share rows = untracked positions the system lost track of, two of which
  (THELEELA, RAMCOIND) are the §3.3 mislabel.
- **Was anything changed or placed?** No. Read-only throughout.

## Not in scope / unchanged

M-O4 itself (answered INERT in §1's batch — still a low-priority real defect, still
not to be bundled into Slice 2.5); M-O5 (owns 15:30, untouched); any fix, design,
or deploy. This document is findings-only. The one open thread worth a future
look, independent of M-O4, is **THELEELA (19-Jun): an unexplained system
tracking-loss the reconciler disowned as human.**

---

## Appendix — evidence

**Read-only query** (SQL fed via stdin to `sqlite3 -readonly` over SSH; no writes):

```sql
SELECT MIN(ts), MAX(ts), COUNT(*) FROM reconciliation_log;
SELECT DATE(ts) d, check_name, tier, symbol, COUNT(*) n
  FROM reconciliation_log
 WHERE check_name IN ('ORPHAN_ADOPTION','INFLIGHT_ORPHAN',
                      'INFLIGHT_ORPHAN_FLATTEN','SYSTEM_OVERSELL')
 GROUP BY d, check_name, tier, symbol ORDER BY d, check_name, symbol;
SELECT ts, tier, symbol, trade_id, success, description, action_taken
  FROM reconciliation_log WHERE check_name='ORPHAN_ADOPTION' ORDER BY ts;
SELECT check_name, COUNT(*) n, MIN(DATE(ts)), MAX(DATE(ts))
  FROM reconciliation_log GROUP BY check_name ORDER BY n DESC;
```

**Representative persisted rows** (verbatim):

```
2026-06-16T10:00:23 | UNRECOVERABLE | AGARIND  | - | Broker has position in AGARIND qty=-1 avg_price=572.55 but no local trade | CapitalDriftDetected published; manual intervention required
2026-06-19T10:01:03 | RECOVERABLE   | THELEELA | - | Human/untracked broker position THELEELA qty=-1 avg_price=483.68; no local trade — not managed by system | logged once; added to human-order set; not adopted
2026-06-25T10:13:04 | RECOVERABLE   | RAMCOIND | - | Human/untracked broker position RAMCOIND qty=-1 avg_price=334.00; no local trade — not managed by system | logged once; added to human-order set; not adopted
```

**Key code references:**

- Routing: `orders/order_reconciler.py:856-882`
- `_check2_orphan_adoption` (no adoption; FIX-182 human policy): `:1496-1585`;
  once/day reset `:1488-1494`; naked WARNING `:1546-1568`; Layer-3 oversell
  `:1528-1536`; fail-quiet nakedness `:1705-1706`
- `_check2_inflight_orphan` (system's own in-flight fill): `:1708-1767`
- RC10 persistence (non-COSMETIC only): `:36-37, 1005-1022`
- EOD phase-2 map is local-trade-only (comment): `orders/eod_squareoff.py:1361-1367`
- EOD residual sweep MIS/CO + system-owned: `:1379-1446` (filter `:1408-1413`,
  system-owned skip `:1439-1446`); FIX-015 broker-qty MIS/CO filter `:1036-1045`
- GICRE 16-Jun in-flight incident (documented): `:1363-1364`
- `reconciliation_log` retention 180d: `scripts/db_retention.py:68`
