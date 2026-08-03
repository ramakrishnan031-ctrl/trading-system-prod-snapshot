# G2c SOAK EVIDENCE — Mon 06 → Wed 08 Jul 2026 (S1–S8, locked format)

Fill one day-block per market day. PASS bar = **3 clean days**; any FAIL → stop,
root-cause, report — **no patches during soak**. Never fabricate an entry: a check
that could not run is recorded as `NOT RUN (<why>)`; a rejected trade that never
occurred is recorded exactly as "none occurred".

Checks per the locked G2c-continue file + the pre-GO addendum:
S1 spot-check values vs DB truth · S2 page-latency medians vs prior week ·
S3 SQLITE_BUSY / read-error count · S4 RSS + CPU of gui-dashboard 3×/day ·
S5 freshness 3×/day (~09:40 / 12:30 / 15:10): dashboard Last Signal / Last Order /
Last Trade vs `SELECT max(...)` spot queries — must match and keep advancing ·
S6 trade traces: (a) one SUCCESSFUL full lifecycle signal→strategy→order→fill→exit
AND (b) one REJECTED/FAILED trace (failure shown at the RIGHT pipeline stage + in
the strategy failure strip) — or the explicit "none occurred" line ·
S7 capacity accuracy 2×/day: Configured/Used/Remaining for Orders · Positions ·
Capital · Risk vs DB/ledger truth (ONE drift = FAIL) ·
S8 strategy-badge truth daily: one silent + one active strategy cross-checked
against the signals table.

---

## DAY 1 — Mon 06-Jul-2026

| # | Check | When (IST) | Dashboard value | Truth (source + value) | Match? |
|---|---|---|---|---|---|
| S1 | spot-check 1: <field/screen> | | | DB: | ☐ |
| S1 | spot-check 2: <field/screen> | | | DB: | ☐ |
| S2 | latency medians (dashboard / strategies / pnl / logs) | | ms: | prior-week median: | ☐ |
| S3 | SQLITE_BUSY + reader errors (journal + app log) | EOD | count: | expect 0 | ☐ |
| S4 | RSS / CPU gui-dashboard | ~09:40 | | `systemctl status` / `ps` | ☐ |
| S4 | RSS / CPU | ~12:30 | | | ☐ |
| S4 | RSS / CPU | ~15:10 | | | ☐ |
| S5 | Last Signal / Order / Trade | ~09:40 | | `max(created_at)` etc.: | ☐ |
| S5 | Last Signal / Order / Trade | ~12:30 | | | ☐ |
| S5 | Last Signal / Order / Trade | ~15:10 | | | ☐ |
| S6a | SUCCESS trace: trade_id=______ | | signal→strategy→order→fill→exit values | DB row values | ☐ |
| S6b | REJECTED trace: signal_id=______ (or "none occurred") | | rejection stage + failure-strip entry | DB status/reason | ☐ |
| S7 | capacity: Orders/Positions/Capital/Risk (C/U/R ×4) | AM | | DB/ledger: | ☐ |
| S7 | capacity ×4 | PM | | | ☐ |
| S8 | badge truth: silent=______ active=______ | | badges/silence tier | signals table | ☐ |

**Day 1 incidents/notes:** (Telegram/logs/DB/Excel consulted? why?)
**Day 1 verdict:** CLEAN / FAIL(<what>)

## DAY 2 — Tue 07-Jul-2026
*(same table — copy the Day-1 skeleton)*

**Day 2 incidents/notes:**
**Day 2 verdict:** CLEAN / FAIL

## DAY 3 — Wed 08-Jul-2026
*(same table)*

**Day 3 incidents/notes:**
**Day 3 verdict:** CLEAN / FAIL

---

## FINAL SOAK VERDICT (locked format — replaces free-form)

**Primary question: "Did Rama need Telegram / logs / DB queries / Excel to
understand something the dashboard could not show?"**

**IF YES — one row per instance:**

| # | (a) exactly what was missing | (b) screen that SHOULD have shown it | (c) proposed enhancement → F-backlog # |
|---|---|---|---|
| 1 | | | F10 |
| 2 | | | F11 |

*(append each (c) to the F-backlog — `ops_dashboard/docs/F_BACKLOG.md` — with F10+ numbering)*
*(⚠️ this said "in mempalace" until 04-Aug-2026; that file is retired and F1–F9 moved to the
path above. Appending is not scheduling — see the note at the foot of `F_BACKLOG.md`.)*

**IF NO — state explicitly:**
> "The dashboard became the primary operational interface."

**PASS/FAIL:** ☐ 3 clean days achieved → soak PASS → G3.0 handoff (control-plane
design by Web Claude) · ☐ FAIL → stopped at Day __, root-cause report attached.
