# The overnight carry — what the paper gate can prove, and what only a live pair can

**26-Jul-2026.** Report + specification. **No code was changed by this section.**
Companion to `paper_fidelity_gaps_26jul2026.md` (the sweep) and
`slice25_never_run_pieces_26jul2026.md` (the classification).

---

## Headline — state it before anything else

> ⚠️ **SLICE 2.5's PAPER GATE CANNOT COVER THE OVERNIGHT CARRY, AND THE OVERNIGHT CARRY IS
> WHAT DELIVERY *IS*.** Every intraday property of a CNC trade is rehearsable in paper. The
> one property that distinguishes delivery from intraday — that the position, its broker-side
> OCO GTT, and the capital reserved against it all survive a night and a process restart — is
> not.
>
> 🔴 **And it does not fail by being silent. It fails by printing a PASS.** A paper Mon→Tue
> ends with a clean `GTT_EXIT`, a booked P&L at Tuesday's LTP, a released capital reservation
> and a `CLEANED` `gtt_state` row — the exact artefacts a successful carry produces — while
> the position was never carried and the protection was never there. **A green paper carry is
> not weak evidence. It is evidence pointing the wrong way.**

---

## B1 — the fraction: what paper can and cannot prove

The CNC delivery lifecycle, stage by stage. "Paper" means *a paper session can produce the
input that drives this stage*, not *a unit test exists* — those are different claims and only
the first one is what a paper gate asserts.

| # | Lifecycle stage | Paper? | Note |
|---|---|---|---|
| 1 | Signal → CNC intent (`ProductResolver`) | ✅ | |
| 2 | Delivery-bucket capital reservation (conditional allocation) | ✅ | config-gated, never yet crossed |
| 3 | CNC entry order placed | ✅ | |
| 4 | CNC entry **fills** | ⚠️ | `_synth_fill` writes only `COMPLETE`/full qty ⇒ **never REJECTED, never PARTIAL** |
| 5 | OCO GTT placed on fill (`place_for_fill`) | ✅ | |
| 6 | `gtt_state` durable row written | ✅ | real DB, real row |
| 7 | 15-min reconcile: active GTT + holding + qty match ⇒ healthy | ⚠️ | only via `seed_paper_holding()`, a **test seam** — never from a real fill |
| 8 | 15:17 EOD squareoff EXEMPTS CNC (EOD6 / FIX-015) | ✅ | un-blinded by `f7eedd3` |
| 9 | HARD_KILL sweep does not sweep CNC as MIS (H-5) | ✅ | un-blinded by `f7eedd3` |
| 10 | **Position → holding transition (T+1 settlement)** | 🔴 **NO** | `_paper_holdings` has exactly one writer and it is the test helper |
| 11 | Nightly restart: the protection record survives it | 🔴 **NO** | the restart is real, but it destroys every broker-side fact the carry would be verified against |
| 12 | **Tuesday 08:15 startup reconcile re-verifies the carry** | 🔴 **FALSE PASS** | the dangerous one |
| 13 | GTT triggers at the broker | ⚠️ | rehearsable **only on branch `hold-check1-w8-26jul`** (`687b958`); on `main` a paper GTT still cannot fire |
| 14 | Selling a demat holding with **no manual CDSL TPIN prompt** (DDPI) | 🔴 **NO** | live-only by construction |

**The fraction, stated plainly:** of the 14 stages, **7 are genuinely paper-provable (1–3, 5, 6,
8, 9); 3 only in a degraded form or through a test seam (4, 7, 13); and 4 not at all (10, 11,
12, 14).** The four are consecutive but for DDPI, and together they are the definition of
delivery. ⭐ **And one of them is worse than "not provable": stage 12 does not fail to prove —
it prints a PASS.** Paper can prove that a CNC trade is opened and protected correctly. It
cannot prove that it is still protected the next morning, and it will say that it is.

---

## The mechanism, measured — why stage 12 is a FALSE PASS and not merely a gap

Three adapter stores are plain in-memory `dict`s on the adapter instance, so all three are
destroyed by the nightly restart:

- `broker/zerodha_adapter.py:424` `_paper_positions`
- `broker/zerodha_adapter.py:429` `_paper_gtts`
- `broker/zerodha_adapter.py:432` `_paper_holdings` — and its **only** writer is
  `seed_paper_holding()` (`:874`), a declared test/paper helper. Nothing in production
  promotes a filled CNC position into a holding.

`gtt_state` is the opposite: a real table (`core/schema.sql:1206`), durable by design — it
exists *precisely* so a restart cannot lose the protection record.

So on Tuesday's 08:15 boot the reconcile (`orders/order_reconciler.py:376` → 
`CncGttMonitor.reconcile()`) gathers:

```
get_gtts()     -> []        (_paper_gtts erased)      => bg is None
get_holdings() -> []        (_paper_holdings erased)  => held = 0
get_positions()-> []        (_paper_positions erased) => held = 0
get_active_gtt_states() -> [the Monday row]           <- survived, it is in the DB
```

and walks the K6 ladder in `_handle_row` (`orders/cnc_gtt_monitor.py:370`):

- `triggered` False, `present_active` False → falls past rungs 1–3;
- rung 4, `held == 0`, `present_active` False → **`cnc_gtt_monitor.py:412`**:
  `return self._finalize_gtt_exit(r, reason="GTT_EXIT")`, whose own comment reads
  *"GTT gone + flat -> the GTT did its job (fired + aged out)"*.

`_finalize_gtt_exit` (`:428`) then does the full, correct, successful-exit sequence:
marks the trade CLOSED, resolves an exit price via `_resolve_exit_price` (`:576` — in paper
`get_trades()` returns `[]`, so it falls to **Tuesday's live LTP**), computes real CNC
round-trip costs, calls `release_used()` to reverse the committed margin, writes
`record_gtt_close_financials`, publishes `PositionClosed`, sets the row `CLEANED`, and alerts
*"Delivery position X closed via GTT … Capital released."*

⭐ **Every one of those artefacts is indistinguishable from a real carried exit.** The exit
price is a plausible live number. The P&L is arithmetically correct given that price. Nothing
anywhere records that the position was never held and the GTT never existed.

> ⚖️ **LIVE vs LATENT: this is LATENT — and it arms itself at exactly the wrong moment.**
> `reconcile()` has no `delivery_enabled` gate (`cnc_gtt_monitor.py:90`), but with delivery
> off there are no `gtt_state` rows, so the loop runs over an empty collection and the defect
> is unreachable *today*. **It becomes reachable the instant `delivery_enabled` is flipped in
> paper — which is the same instant somebody starts treating the paper run as the gate.** It
> is not reachable while nobody is watching and reachable when they are; it is the reverse.

---

## B2 — the live pair: exactly what it must demonstrate

Two different things are being conflated under "T2", and separating them is most of the work.

**`scripts/t2_cnc_gtt_realtest.py --arm-overnight` / `--close-overnight` proves the BROKER
side.** It builds its own adapter, places its own orders and drives them by hand. It answers:
*can this account buy 1 share CNC, keep an OCO GTT resting on it overnight, and sell the
resulting demat holding the next day with no manual CDSL TPIN prompt?* That is the DDPI
question, it is real, and it is still open. **It is not the system question.** The canary
bypasses `CncGttMonitor`, `gtt_state`, the reconciler and the fund manager entirely — a green
canary says nothing about whether *the system* survives the same night.

**The system pair is a second, separate proof, and it does not exist in any form yet.**
Specification:

### Monday (market hours, supervised)

| # | Action | Observation that constitutes proof |
|---|---|---|
| M1 | Enter ONE real CNC position through the **normal signal path** (not the canary) | `trades` row with the delivery product; `orders` row shows product CNC |
| M2 | OCO GTT placed automatically on the fill | broker `get_gtt` shows product=CNC, qty matching, two SELL legs, `[SL, TGT]` ascending |
| M3 | The durable mirror is written | `gtt_state` row: `status='ACTIVE'`, `gtt_id` == the broker trigger id, `qty` == filled qty, `created_at` = Monday |
| M4 | Capital is reserved in the delivery bucket | `fm_ledger` shows the reservation; the intraday bucket is untouched |
| M5 | **15:17 EOD squareoff leaves it alone** | squareoff log names the symbol as EXEMPT (CNC); broker net qty is still > 0 at 15:30 |
| M6 | 15-min reconcile before close reports healthy | action label `healthy:<symbol>`, `last_verified_at` advanced |
| M7 | Service self-exits at `service_window_end` with the position OPEN | clean exit; **this is the first thing paper has never done** |

### Overnight

| # | Observation |
|---|---|
| N1 | The broker GTT is still ACTIVE after hours (query it from a separate process, e.g. the canary's read path) |
| N2 | The position has become a **holding** at the broker (`holdings()` shows it, `positions()` may not) — the T+1 transition that has no paper equivalent |

### Tuesday (08:15 boot, supervised)

| # | Action | Observation that constitutes proof |
|---|---|---|
| T1 | Boot; the startup reconcile runs (`order_reconciler.py:376`) | log line from `cnc_gtt_monitor` |
| T2 | `_gather()` sees the real world | `get_holdings()` returns the carried qty; `get_gtts()` contains the Monday `gtt_id` with status `active` |
| T3 | **The ladder takes rung 2, not rung 4** | action label is **`healthy:<symbol>`** — ⭐ *this single label is the whole point of the pair;* it is the observation paper structurally cannot produce, because in paper the same code emits `gtt_exit:<symbol>` |
| T4 | Nothing was finalised | the `trades` row is still OPEN; `gtt_state` still `ACTIVE`; **no** `PositionClosed`, **no** capital release, **no** `record_gtt_close_financials` |
| T5 | Then let the GTT do its job (or trigger it): the OCO fires | broker GTT status → `triggered`; holding goes to 0 |
| T6 | The next reconcile finalises correctly | rung 1: `held == 0` + `triggered` ⇒ `GTT_EXIT`; exit price resolves from a **real broker trade**, not from an LTP fallback |
| T7 | The demat sell completed with **no manual TPIN** | the DDPI answer, now inside the system rather than beside it |
| T8 | Capital returns exactly once | `fm_ledger` net for the trade reconciles; no double release |

**PASS = T3 and T4 together.** If Tuesday's first reconcile says `healthy` and finalises
nothing, the carry works. If it says `gtt_exit`, the system has just done in live what it
does in paper, and the difference is that this time it was real money.

⛔ **The two canaries are strictly ordered.** `--arm-overnight` first: if DDPI is not
authorised, the system pair cannot complete and would strand a real unprotected holding.
Only after the broker question is answered green does the system pair make sense.

---

## B3 — can the paper run announce its own blind spot? Yes, and cheaply

> ⭐ **The dangerous version of this gap is silence.** If nothing says otherwise, a green
> paper carry will be read as coverage — that is what a gate is *for*. So the paper run must
> assert its own limit, in the run, not only in a document nobody re-reads in three months.

> ✅ **BUILT 26-Jul (`ea65581`) — option (a) below.** `CncGttMonitor._announce_paper_carry_blind_spot`,
> paper-only by a MODE check, fires BEFORE the misleading `GTT_EXIT` (pinned by a test on
> the ORDER of the two alerts), names the symbols and the rows, and points here. It
> **announces without diverging** — the fabricated exit still happens, so paper and live
> still run the same ladder. Proven silent in LIVE against an identical backdated row,
> silent on a same-day row, and silent when there are no rows at all (today's state).

Three candidates, cheapest first:

**(a) The one-line startup announcement — RECOMMENDED, and now BUILT.**
At the startup reconcile, in paper mode only, before `_handle_row` acts on anything: if any
ACTIVE `gtt_state` row has `created_at[:10] < today`, emit ONE CRITICAL —

> *"PAPER CANNOT CARRY. N delivery GTT row(s) written on <date> survived this restart in the
> database, but the paper broker's holdings, positions and GTTs did not. Whatever this
> reconcile does with them next is NOT evidence about the overnight carry — expect a
> `GTT_EXIT` that did not happen. The overnight path is live-only; see the Mon→Tue pair."*

Cost: one date comparison against a column that already exists (`gtt_state.created_at`,
`core/schema.sql:1221`), inside an `if self._mode == "PAPER"` branch. It needs no new state,
no new table, no simulation, and it cannot alter a live run because the condition is
mode-gated. It fires exactly once per carried row per boot, and it fires **before** the
misleading `GTT_EXIT` alert rather than after it.

**(b) Refuse instead of announce** — same detection, but skip `_finalize_gtt_exit` and leave
the row ACTIVE with `needs_review=1`. Stronger (no false artefact is ever written) but it
changes paper behaviour into something live does not do, which is its own parity lie. **Not
recommended without a decision.**

**(c) Put it in the paper gate's checklist rather than the code** — a required line in the
Slice 2.5 sign-off: *"overnight carry: NOT COVERED BY THIS RUN."* Zero code, but it is a
document, and documents are exactly what fails here.

⭐ **(a) and (c) are complements, not alternatives.** (a) makes the run say it; (c) makes the
sign-off say it.

---

## B4 — what must NOT be built, and why the reasoning stands

⛔ **Do not persist paper positions/holdings across the restart.** To carry a paper CNC
position you must decide when a position becomes a holding, what happens to it over a
weekend, over a holiday, on a settlement failure, and what the qty is on T+1 vs T+2. That is
a **settlement model** — new simulated surface on the execution path, invented by us, with no
authority behind it. Every rule it gets wrong becomes a paper "proof" of something the broker
does differently. The gap is honest; the model would be confidently wrong.

⛔ **Do not call `seed_paper_holding()` from production code.** It is a declared test seam
(`zerodha_adapter.py:874`). A production caller of a test seam makes the seam load-bearing
and erases the very distinction — paper input vs real broker answer — that this whole report
is about. **Worse than the gap.**

✅ **The honest position, unchanged: the Mon→Tue live pair is irreducible.** Not conventional,
not a formality — structurally irreducible, because the transition at stage 10 has no paper
producer and the proof at stage 12 is a *different code path taken*, which only a real broker
answer can select.

---

## What this section changed

**When written: nothing** — report and specification only. **B3(a) was subsequently approved
and built** (`ea65581`); everything else here stands unchanged, and in particular §B4 still
holds: no settlement model, no production caller of `seed_paper_holding`, and the Mon→Tue live
pair remains irreducible.
