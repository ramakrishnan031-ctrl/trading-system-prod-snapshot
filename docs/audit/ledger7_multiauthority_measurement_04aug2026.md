# LEDGER #7 — MULTI-AUTHORITY CONCEPTS — MEASUREMENT — 04-Aug-2026

**Status: `<MEASURED — NOTHING CHANGED, NOTHING DECIDED>`.** Measured at **`7b69d42`**.
Read-only; **0 `.py` changed.** Register row: `MASTER_PENDING_01-Aug-2026.md:628`
(*"Multi-authority concepts ("held" ×4, status ×34 sites)"*) · IA-XARCH-03 · IA-XDUP-02.

⛔ **M4 applied throughout: the audit's description is a HYPOTHESIS, not a finding.** Three
of its numbers are wrong and one of its sub-claims is refuted. Each is stated with what
was measured and how.

---

## 1. "HELD" — the audit says ×4 and **3× positions()-only readers**. ⛔ MEASURED: **12**.

**Method:** AST walk (not grep) over `broker/ capital/ orders/ core/ strategies/ scanners/
utils/ main.py`, excluding tests, collecting real `ast.Call` nodes. ⭐ Grep first returned
**15** because it cannot distinguish a call from a **docstring mention** — three of those
"sites" (`state_store.py:1093`, `eod_squareoff.py:1054`, `order_reconciler.py:742`) are
prose. The AST number is the honest one.

| | count | sites |
|---|---|---|
| `get_positions()` | **12** | `position_helpers:37` · `kill_switch:1277` · `kill_switch:1854` · `cnc_gtt_monitor:438` · `eod_squareoff:1072` · `eod_squareoff:1447` · `eod_squareoff:1611` · `order_reconciler:745` · `order_reconciler:839` · `order_reconciler:2913` · `order_reconciler:2966` · `structure_exit_manager:625` |
| `get_holdings()` | **1** | `cnc_gtt_monitor:437` |
| **files reading BOTH** | **1** | `orders/cnc_gtt_monitor.py` (`_gather`, `:432`) |

⇒ The audit's shape is right — one holdings-aware reader, the rest positions-only — but the
positions-only count is **12, not 3: a 4× undercount.** `_gather` is correctly identified
as the sole both-reader.

### 1a. ⛔⛔ WHY THIS IS FLIP-CRITICAL, AND IT IS NOT A STYLE POINT

`broker/zerodha_adapter.py:929` defines the boundary in one line:

> *"Carried (**T+1+**) CNC delivery holdings."*

⇒ A delivery position sits in `positions()` on its **buy day (T+0)** and moves to
`holdings()` at **T+1**. **12 of the 13 readers therefore go blind to a delivery position
exactly one day after it is bought** — including:

- `capital/kill_switch.py:1854` — the HARD_KILL broker sweep;
- `orders/eod_squareoff.py` ×3 — the EOD squareoff;
- `broker/position_helpers.py:37` — `broker_net_qty`, which backs
  `determine_close_direction`, used by the kill path **and** order_placer's emergency exit.

⚠️ **This is already recorded for ONE reader** (`reconcile_positions` is blind to T+1;
"15:45 SUCCESS ≠ book flat"). **The measurement shows it is not one reader — it is a class
of twelve**, and the flip is what makes the class reachable.

⛔ **NOT a claim that all twelve are defects.** Q4 ruled a HARD_KILL flattens intraday only,
so the kill's blindness is *right* — but right **by accident**, which is already on record,
and the standing rule is that the buy-day product filter must land before anything makes
the kill holdings-aware. **Each of the twelve needs its own verdict; none is issued here.**

---

## 2. STATUS — the audit says ×34 and "~30 inline + 4 named, ≥3 live memberships"

**Measured: the 34 is real but attached to the wrong thing, and two of three counts are low.**

| audit claim | measured | verdict |
|---|---|---|
| "~30 inline SQL literals" | **34 inline `status IN (…)` sites** | the **34** is the INLINE count *alone* |
| "4 named sets" | **9 named sets** | ⛔ **low by 5** |
| "≥3 distinct live memberships" | **5** | floor holds; real number is 5 |
| total authorities | **43** (34 + 9) | the register's "×34" understates it |

### 2a. The five "live" memberships — all answering *different* questions

| membership | where |
|---|---|
| `OPEN, PARTIAL` | **14** inline sites + `ACTIVE_TRADE_STATUSES` (`preflight/checks/state.py:25`) |
| `OPEN, PARTIAL, PENDING_FILL` | **5** inline + `_FLATTEN_LIVE_TRADE_STATUSES` (`kill_switch.py:93`) |
| `OPEN, PARTIAL, EXITING` | **3** inline + `_OPEN_POSITION_STATES` |
| `OPEN, PARTIAL, EXITING, PENDING_FILL` | `OPEN_STATES` (`db_reader.py:22`) |
| `OPEN, PARTIAL, EXITING, CLOSED, CLOSED_MANUAL` | `_EXECUTED` (`system_manager.py:102`) |

⭐ **Pure duplication, measured:** `_OPEN_POSITION_STATES` is defined **verbatim in two
files** (`db_reader.py:28`, `preflight/checks/engine.py:92`) with no shared source; and the
membership `CLOSED, CLOSED_MANUAL` carries **three different names in three files**
(`_CLOSED_STATES`, `_CLOSED_STATUSES`, `_CLOSED`) plus **10** inline sites.

### 2b. ⭐⭐ THE ONE-GUARD-TWO-JOBS **INVERSE** — found, and it is the root of §2

#3's design warned to look for the inverse shape — *two concepts carried by one authority* —
and not to assume separability because it would be convenient. **It is here, and it explains
why there are five memberships rather than one wrong one.**

`trades.status` is a **single column carrying at least two independent facts**:

| the real question a caller has | which statuses answer it | who asks |
|---|---|---|
| *is this trade LIVE at the broker?* (something to flatten) | OPEN, PARTIAL, **PENDING_FILL** | kill switch |
| *does this trade HOLD a position right now?* | OPEN, PARTIAL, **EXITING** | position/exit readers |
| *did this trade EXECUTE today?* | + CLOSED, CLOSED_MANUAL | the symbol+direction rule, system_manager |

⇒ `PENDING_FILL` is **live but holds nothing**; `EXITING` **holds but is not enterable**;
`CLOSED` **executed but holds nothing.** No single membership can be right for all three
questions, so every caller re-derives its own boolean from one overloaded column — as a
literal, with no shared source. **The five memberships are not five mistakes; they are three
concepts wearing one column.**

⛔ **Therefore "unify the status sets" is the WRONG fix and would be actively harmful** — it
would force three genuinely different questions onto one answer. The separable thing is the
*concepts*, not the literals. ⛔ **Not decided here.**

⭐ **The counter-example is in-repo and contracted:** `core/closure_source.py` +
`docs/closure_source_contract.md`, enforced by a **tree-scan test** — two axes deliberately
kept separate (`closure_source` = WHO, `exit_mechanism` = HOW). That is the same shape this
would need, and it proves the idiom works in this codebase.

---

## 3. ⛔ SUB-CLAIM **REFUTED**: ORDER states do **not** have a "correctly imported" canonical

The audit states: *"ORDER states have a partial canonical (OSM `TERMINAL_STATES`, **correctly
imported**) while TRADE states have none."* The second half holds. **The first half does not.**

`broker/order_state_machine.py:76` — `TERMINAL_STATES = ("COMPLETE","CANCELLED","FAILED","EXPIRED")`.

| consumer | how it gets the set | agrees? |
|---|---|---|
| `broker/order_monitor.py:45` | **imports** `TERMINAL_STATES` | ✅ the only importer |
| `capital/kill_switch.py:98` | local copy — same 4 members | ⚠️ agrees, **duplicated** |
| `orders/order_reconciler.py:103` | local copy — same 4 members | ⚠️ agrees, **duplicated** |
| `orders/order_placer.py:434` | local copy **+ `REJECTED`** | ⛔ **DIVERGES (5 members)** |

⇒ **1 of 4 consumers imports it; 2 duplicate it; 1 diverges.** "Correctly imported"
describes a quarter of the surface.

### 3a. Reachability — **LATENT, not live** (classified, per the live-vs-latent rule)

Live VM `orders` table, all-time: **`COMPLETE` 417 · `CANCELLED` 415 — and nothing else.**
`REJECTED`, `FAILED` and `EXPIRED` have **never** occurred.

⇒ The `order_placer` divergence **cannot bite today**, and this independently corroborates
the standing rule *"`orders.status` is NEVER `REJECTED` — count from `trades.status`"*.
⇒ **LATENT ⇒ document + pin, ⛔ do not stop-and-fix.**

⚠️ **But note what the same number says about the agreement:** only **2 of the 4** canonical
terminal states have ever occurred, so the three copies' agreement on `FAILED`/`EXPIRED` has
**never been exercised by production**. They agree on paper. That is the **agree-by-luck**
shape this audit already named elsewhere — ⛔ it is not evidence of correctness.

---

## 4. WHAT IS OPEN — ⛔ nothing decided, nothing built

1. **Twelve positions-only readers each need a verdict** once delivery is live: blind-and-correct
   (the kill, per Q4), blind-and-wrong, or blind-and-latent. ⛔ Not triaged here.
2. **Whether `trades.status` should be split** (§2b) — a schema/vocabulary decision on the
   money path, and a CAREFUL-LOOP item by any reading.
3. **Whether the 3 duplicate terminal-order sets should import the OSM canonical** — cheap and
   safe-looking, but it changes `order_placer`'s membership (drops `REJECTED`), so it is a
   behaviour change on a latent path, not a refactor.
4. **The register line should be corrected**: "held ×4, status ×34" → **held ×13 (12 blind),
   status ×43 (34 inline + 9 named)**.

⛔ **HALT.** No code, no de-duplication, no register edit beyond recording this measurement.
