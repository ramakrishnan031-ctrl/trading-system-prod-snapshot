# Exit-Rejection → HARD_KILL — Forensics (thread 1, 22-Jul-2026)

**Scope:** Follow-up thread 1 flagged by `orphan_adoption_forensics_22jul2026.md`
("Not in scope", item 1). Question: the June clusters shared one root — an entry
filled, then `order_placer._place_limit_triple_exits` had its SL/TGT **rejected by
Zerodha** → position unprotected → **HARD_KILL** → kill-flatten stranded positions →
orphan debris. Were the **three rejection variants** (market-protection 15-Jun,
tick-size 16-Jun, circuit-limit 19-Jun) each **independently closed**, and is
**HARD_KILL the intended terminal response** vs a retry/repair? Asked now because the
strategy revision (hammer, morning/evening star, channel breakout) brings new
symbols, bands, ticks — the event most likely to surface a *fourth* rejection reason.

**Read-only.** Nothing changed, placed, restarted. Method: source trace of
`order_placer.py`, `order_protocol_limit.py`, `price_math.py`, `kill_switch.py`,
`zerodha_adapter.py`, `screening/{secondary_screener,hard_gate}.py`; `git log`
ancestry + dates against `HEAD`; targeted regression run (55 tests, green — below).
No service, DB write, or order path touched. No fix or design proposed (B4 enumerates
options only). Builds on the two 22-Jul orphan docs; does **not** re-open thread 2
(the pre-15-Jun large orphans).

---

## TL;DR — verdict

**All three variants are ACTIVELY PREVENTED, not merely un-recurred.** Each has a
named fix, deployed **on `main`** (code ledger empty ⇒ live on the VM), landed a
**same-day P0 hotfix** and then a **structural consolidation** in the FIX-190 wave
(19–24 Jun), and is **locked by a test that is green on HEAD** — including an
**incident-replay test that reconstructs the THELEELA cascade and asserts it cannot
recur** (`tests/integration/test_fix190_incident_replay.py`). This **upgrades** the
prior forensics doc's framing ("LATENT / historical … evidently handled") — the code
shows genuine, structural, symbol-agnostic prevention, not the absence of a trigger.

**HARD_KILL was a deliberate, pre-live design decision** (OP-NS5, locked ~24-Apr) —
and it has since been **deliberately softened**: a TGT-only failure no longer kills
(FIX-190 Bug C), and every kill is now preceded by a **reverse-aware emergency
flatten** (FIX-148 + Bug A). The June **kill-flatten oversell was NOT inherent** to
the flatten; it was a stale-side/stale-qty re-fire, now closed at all three flatten
sites by `determine_close_direction` (Bug A).

**The bounded tail that remains** (why this is not "fully closed, stop worrying"):
for any exit-rejection reason **outside the four now-handled** (LTP-validation, tick,
circuit-band, SL-M/market-protection), the path is still **best-effort emergency
flatten → HARD_KILL (full-day halt)**. The position is no longer left naked, but the
**day still ends**. Whether halting the whole day is still the right terminal *after
a successful single-position flatten* is the one genuinely open design question — and
the strategy revision is exactly what would exercise a novel reason (§C).

> **Resolution (22-Jul, later — Rama, Decision 7): the day-halt STAYS.** On a book
> running ≈ −0.1R/trade an unnecessary halt costs approximately nothing, and every
> live firing (15/16/19-Jun) was a multi-position storm — the case a halt is *for*.
> The **behaviour is retained**; only OP-NS5's *stated rationale* was corrected (a
> doc-only edit) — from "protects a naked position" (superseded by FIX-148, which
> flattens first) to "**anomaly circuit-breaker**." Landed in `orders/order_placer.py`
> (OP-NS5 + the adjacent stale OP-NS4 SL-M clause) and `docs/SYSTEM_MAP.md`. §B4's
> "flatten-only, keep trading" option was considered and **not** taken.

---

## §A — Are the three variants independently closed?

**A1 — fix, location, and side, per variant.** All three rejections were on the
**exit** legs of a filled entry; the fixes are predominantly **exit-side handling**,
with the circuit variant *also* gated **entry-side**.

| Variant (date) | Broker rejection | Root | Fix (named) | Side | Commit / date | Prevention? |
|---|---|---|---|---|---|---|
| Market-protection (15-Jun 11:35) | "Market orders without market protection are not allowed via API" | SL leg placed as **SL-M** (stop-market); Kite refuses SL-M via API | **P0 FIX-179**: every SL leg → `order_type="SL"` (stop-limit) with a limit past the trigger (`calc_sl_limit_price`). Emergency/kill exits → **marketable LIMIT** not MARKET (FIX-181). | Exit | `7614941` **15-Jun 20:45** (same day); `7f93c2f` 16-Jun | **Active** (structural: the rejected order type is no longer used on primary paths) |
| Tick-size (16-Jun 10:00) | "Tick size … enter price in the multiple of tick size" | SL limit = trigger×(1±offset) rarely lands on a tick; Kite rejects off-tick | **FIX-181 tick-snap**: `zerodha_adapter._snap_to_tick` is the **authoritative net for ALL placements** (entry/SL/TGT/emergency/kill) — "no caller can submit an off-tick price"; `DEFAULT_TICK` fail-safe + WARN. Redundant `order_placer._round_to_tick` later removed (`1313a08`) so the adapter is the **sole** chokepoint. | Exit | `7f93c2f` **16-Jun 16:46** (same day); consolidated `478673f` 23-Jun | **Active, CLASS-level** (single chokepoint snaps every symbol) |
| Circuit-limit (19-Jun 10:00) | "order price is higher than the current upper circuit limit of 503.35" | TGT recalc'd from fill sat **above the upper circuit** | **Exit-side** FIX-190 Bug D + NOCIL: `clamp_exit_into_band` clamps into `[lower×1.02, upper×0.98]` **and** checks polarity vs the fill (`8812026`, 23-Jun). **Entry-side** `circuit_proximity_reason` → `REJECTED_CIRCUIT_PROXIMITY` pre-empts doomed entries with the **same margin constant** (`fb68806`, 23-Jun). | **Both** | `8812026`/`fb68806` **23-Jun**; after-check `04f8dd4` 24-Jun | **Active** (two-sided; the most-covered variant) |

**A2 — actively prevented vs happened-not-to-occur.** Each is *actively prevented*,
and each prevention **post-dates the incident it answers** (so it is causal, not
coincidence):

- **Market-protection & tick-size**: the *same-day* P0 hotfixes (FIX-179 15-Jun,
  FIX-181 16-Jun) removed the offending order shapes entirely (SL-M gone; off-tick
  impossible). These are structural, not conditional — the condition **cannot** recur
  because the input that produced it is no longer emitted.
- **Circuit-limit**: honest nuance — THELEELA was 19-Jun; the **entry-side gate and
  exit-side placeability gate landed 23-Jun** (4 days later). For that 4-day window
  the circuit case was *un-recurred* (conditions didn't arise); from 23-Jun it is
  *actively prevented* (now **29 days** live). Both a real prevention **and** an
  absence-of-trigger apply, split by 23-Jun.
- Contrast with the prior doc's "evidently handled in the following weeks" — the
  record is more specific and stronger than that phrasing implied.

**A3 — tick-size: class or instance?** **Class.** Tick handling is not a per-symbol
patch — it is `round_to_tick` (decimal-exact) applied at a **single authoritative
chokepoint**, the adapter's `_snap_to_tick`, which snaps price *and* trigger for
**every** order type on **every** placement path, resolving the instrument's real
tick via `InstrumentCache` with a `DEFAULT_TICK=0.05` fail-safe + throttled WARN. GICRE
was an *instance*; the fix is the *class*. A new symbol with a 0.01/0.10/0.50 tick is
handled with no new code. (`test_tick_failsafe_snap.py`, `test_price_math.py` — green.)

**A4 — market-protection: passed on MARKET orders, or path not taken?** **Neither —
the path is structurally avoided, not made compliant.** The adapter's `place_order`
sends **no `market_protection` kwarg** to Kite. Instead, the system **stopped emitting
bare market orders** on the paths that were rejected: SL legs are SL (stop-limit) with
a price; emergency and kill-flatten exits are **marketable LIMIT** (LTP ± 1%,
tick-snapped). A bare **MARKET** order survives only as a **last-resort fallback when
LTP is genuinely unavailable** (`order_placer._emergency_market_exit:3851-3853`;
`kill_switch._marketable_exit_params:1198-1200` → `("MARKET", 0.0)`). **Residual:** that
fallback still carries no market protection and could be rejected on a
protection-requiring instrument — narrow (requires a just-entered symbol with no
fetchable LTP) and un-exercised, but real. See §C/Residual.

---

## §B — Is HARD_KILL the right response?

**B1 — the current response to an exit-placement rejection, end to end.** It is a
**layered ladder**, not an immediate kill. In `order_protocol_limit.place_exits` →
`order_placer._place_limit_triple_exits`:

1. **Pre-empt (entry-side).** `REJECTED_CIRCUIT_PROXIMITY` rejects an entry that sits
   at/beyond the exit-clamp ceiling before it can fill — the doomed-entry case never
   opens a position (`hard_gate.circuit_proximity_reason`).
2. **Clamp (exit-side, pre-broker).** `clamp_exit_into_band` moves SL/TGT inside the
   band and validates each leg's polarity vs the fill (3 sites, no-bypass test).
3. **`place_deferred_exits` raises:**
   - **LTP-validation error** (code 16418 / "trigger price" / "ltp cannot be
     validated") → **retry queue**, up to `MAX_RETRIES=3` on live LTP ticks,
     TTL-bounded, re-guarded each attempt (status/SL-exists/idempotency, H-3). On
     exhaustion → step 5.
   - **`SLUnplaceableError`** (SL would land wrong-side of the fill — the position
     *cannot* be stopped) or **any other BrokerError** → step 5 directly (never the
     retry queue; explicitly guarded).
4. **`place_deferred_exits` returns, `tgt_placed=False`** (TGT rejected/unplaceable,
   SL live) → **`_persist_sl_only_protected`** (FIX-190 Bug C): the SL protects, so
   persist SL only, queue the TGT to `TGTRetryManager`, **and do NOT kill**. This is
   the single most common single-position failure and it **no longer halts the day**.
5. **Emergency flatten, then kill.** `_emergency_market_exit` (FIX-148) runs **first**:
   cancels this trade's resting exits (Bug E), reads the **actual broker net** and
   fires a reverse-aware marketable-LIMIT close (Bug A) — or nothing if already flat —
   marks the trade `EXITING` so the kill-flatten can't double-sell. **Then**
   `_fire_hard_kill_for_unprotected_position` (OP-NS5). The kill's own flatten sweep is
   a second, independent reverse-aware backstop with bounded retry.

**So: retries exist (LTP), a repair exists (emergency flatten before every kill), and
a no-kill fallback exists (SL-only).** HARD_KILL is now the **terminal for the
genuinely-unprotectable case (SL cannot be placed) and for DB/broker-drift** — not the
immediate response to any rejection.

**B2 — deliberate or last-resort?** **Deliberate, established from the record.**
OP-NS5 in the `order_placer` module header ("On exit placement failure AFTER ENTRY
fill: position is live with no SL … Fire `kill_switch.hard_kill`") is a **locked rule**
from the pre-live Naked-Short Fix (Phase A/2.1+3.4, ~24-Apr; audit-fixes commit
`89ed020` 26-Apr). `_fire_hard_kill_for_unprotected_position`'s docstring states the
rationale plainly: "exactly the capital-safety condition kill_switch.hard_kill exists
for." The reasoning is defensible on its own terms: a live position with no stop is a
capital breach and halting new entries is a reasonable circuit-breaker. **It was also
deliberately *narrowed* after June** — Bug C removed the TGT-only case from the kill,
FIX-148 inserted a flatten before the kill. The design was revisited, not left inert.

**B3 — the kill-flatten oversell: inherent or incident-specific?** **Incident-specific
and now closed.** In June the flatten fired a **stale-side / stale-qty** order without
reading the actual broker net: entry BUY +1 → competing SELLs during the kill drove the
book to −1 (the THELEELA naked short). **FIX-190 Bug A** made every flatten site
reverse-aware via `determine_close_direction` — first pass, broker-position sweep, and
the retry loop (`kill_switch.py:1425/1482/1567`) — returning `(None,0)` and **skipping
the re-fire** when the broker is already flat. H-4 re-derives (side,qty) from the
*current* net on every retry (`5f116c0`), H-5 exits under the same product to avoid a
Kite netting mismatch, Bug E cancels resting exits first. The incident-replay test
asserts a reverse-aware flatten of an already-flat position "fires NOTHING (no second
SELL → no naked short)". The oversell was a property of the *old* flatten, not of
flattening.

**B4 — alternatives to a full-day kill, enumerated with failure modes (NOT a
recommendation; no option chosen).** Note the system already occupies a *spectrum*, so
some of these are partly live:

| Option | What it does | Failure mode / cost | Status today |
|---|---|---|---|
| **Retry with corrected params** | Re-place the exit after clamp/tick/type correction | If the correction is wrong or the reject reason is novel, retries burn time while the position rides unprotected; needs a bounded count + a fresh-state re-guard to avoid a naked-reverse on a since-closed trade | **Live for the LTP class** (3×, TTL, re-guarded); not attempted for other reasons |
| **Fall back to a plain SL / SL-only** | Keep a protective stop even when the full bracket can't be placed | Only valid when *a* protective stop is placeable; a genuinely wrong-side SL (SLUnplaceableError) has no plain-SL fallback | **Live for TGT-only failure** (Bug C); inapplicable when the SL itself is the reject |
| **Flatten just that position, keep trading** | Emergency-close the one position; leave the day open | If the reject reason is *systemic* (auth, margin regime, a restart storm) the next entry hits the same wall — the kill is what stops that cascade; per-position handling would let it repeat | **Partially live**: the flatten happens (FIX-148), **but the kill still fires after it**. The open question in §B/TL;DR is precisely whether to *stop* at flatten when the flatten succeeded |
| **Alert and hold (no auto-action)** | Page a human, leave the position | Reintroduces exactly the naked-position window OP-NS5 was written to close; unacceptable during unattended live hours | Not used; contrary to the locked invariant |

The honest framing: post-FIX-148 the position is flattened first, so HARD_KILL's *stated*
justification ("the position is naked") no longer holds at kill time. The kill now
functions as an **anomaly circuit-breaker** ("an exit rejection is a canary for a bad
condition — halt the day"), which is a *different* rationale than OP-NS5's original one.
Surfacing that shift is the finding; choosing between "halt the day" and "stop at a
successful flatten" is a design decision left to Rama.

**B5 — how often would per-position handling have avoided a full-day kill?** From the
persisted record, the June kills fired during **multi-position storms**, not against a
single failure with an otherwise-healthy book:

- **15-Jun** — three symbols stranded (AVL, HARIOMPIPE, SETL) *plus* a G5b
  crash-recovery traceback, inside a **restart storm** (service Started/Stopped 5×
  that morning; FIX-185 later capped the burst).
- **16-Jun** — GICRE + ITC (the in-flight fill race) + AGARIND.
- **19-Jun** — THELEELA *plus* a second unprotected position in the same cycle
  (`CHECK2 INFLIGHT_ORPHAN RCF qty=3` at 10:00:31).

So in each incident a per-position response would **not** obviously have kept the day
safely running — the failures were **correlated** (a systemic condition), which is the
case the day-halt is *for*. Meanwhile the most common *isolated* single-position case
(TGT-only) already avoids the kill via Bug C. **Boundary (carried from the prior
forensics):** an exact "healthy open positions at the kill instant" count is not
reliably reconstructable — `orders.qty_filled`/`avg_fill_price` are not dependably
populated for these trades and the kill-time book must be inferred from journald. If a
precise per-incident number is wanted, it needs a journald timeline reconstruction
(out of scope here, read-only).

---

## §C — Forward-looking: what the strategy revision changes

**C1 — which rejection conditions are symbol-dependent (vs logic-dependent)?** **Tick
size, circuit bands, and price-range/market-protection eligibility are all
per-instrument.** A new strategy trading a different universe meets *different* values
of the same conditions. The reassuring part: the three fixes are **keyed off the
instrument's own data**, not hard-coded thresholds — tick from `InstrumentCache`, bands
from the live quote's `upper/lower_circuit`, product from the resolver — so they **scale
to new symbols with no new code**. The known three causes are symbol-agnostic by
construction.

**C2 — is there pre-trade validation of exit params *before* the entry is placed?**
**Partially, and structurally.** `REJECTED_CIRCUIT_PROXIMITY` (entry-side) is exactly
this for the circuit class: it rejects an entry whose fill would leave **no in-band
profitable TGT or valid SL**, using the *same* margin constant as the post-fill exit
gate — so the exit-unplaceable condition is checked *before* the position opens. It does
**not** pre-validate tick or market-protection eligibility (those are handled by making
the exit always-valid instead — snap-to-tick, no SL-M — rather than by pre-screening).
There is **no general "simulate the exit before entering" gate**; the design bet is
"make every exit shape structurally placeable" plus "pre-reject the one geometric case
(circuit) that can't be made placeable." That bet holds for the four known reasons and
is blind to a *fifth*.

**C3 — does the strategy revision raise this risk, lower it, or is it neutral?**
**Neutral for the three known causes; marginally *raising* for the residual tail.**
The known causes (tick, circuit, market-protection) are handled symbol-agnostically and
scale to the new universe automatically — no added risk there. But a new universe and
new patterns (channel breakout near bands, low-priced stars with wide ticks) are exactly
what would surface a **novel exit-rejection reason** (freeze-quantity, a new
per-instrument margin/exposure rule, an instrument-specific order restriction) — and any
reason outside the handled four still routes to **emergency flatten → full-day
HARD_KILL**. **Rama should know before changing the strategy set:** the *specific*
guards travel to new symbols for free; the *terminal* response to anything they don't
anticipate is still to halt the day (after a best-effort flatten). The lever that most
reduces this tail is the §B open question (stop at a successful flatten?), not more
per-cause filters.

---

## Residual open surface (findings, not fixes)

1. **The class has a tail.** Any exit-rejection reason ∉ {LTP-validation, tick,
   circuit-band, SL-M} → best-effort emergency flatten → **HARD_KILL (full-day halt)**.
   The position is no longer left naked (FIX-148), but the day ends. **LATENT** — no
   such novel reason has occurred; the strategy revision is the event most likely to
   produce one. Pin: the incident-replay + clamp/tick/retry tests cover the *four*, not
   a *fifth*.
2. **MARKET fallback carries no market protection.** `_emergency_market_exit` and
   `_marketable_exit_params` fall back to a bare `MARKET` order only when LTP is
   unavailable; such an order could be rejected on a protection-requiring instrument,
   momentarily failing the last-resort flatten (the kill retry loop would re-attempt).
   **LATENT**, narrow.
3. **SLUnplaceableError → emergency flatten + HARD_KILL is by design**, not a gap: a SL
   that can only land on the wrong side of the fill means the position is genuinely
   un-stoppable; flatten-then-halt is the intended terminal. Recorded so a future reader
   does not "fix" it into a naked-TGT.

None are **LIVE** (all require a condition that has not arisen on the current symbol
set). Per the LIVE-vs-LATENT rule: documented + pinned, investigation continues, no
stop-the-line.

---

## Answers to "what DONE means"

- **Each variant marked, with the fix named** — §A table: market-protection = **active**
  (FIX-179, SL-M→SL); tick-size = **active, class-level** (FIX-181, adapter sole
  snap-point); circuit-limit = **active, two-sided** (FIX-190 Bug D + entry-side
  `REJECTED_CIRCUIT_PROXIMITY`). None are "merely un-recurred" today (circuit was, for
  the 19–23 Jun window only).
- **Current response to an exit-placement rejection, traced end to end** — §B1 ladder:
  entry pre-reject → band clamp → LTP-retry / SL-only / emergency-flatten-then-kill.
- **Whether HARD_KILL was deliberate, from the record** — **yes**, OP-NS5 locked
  pre-live (~24-Apr), and deliberately softened post-June (Bug C, FIX-148).
- **Alternatives enumerated with failure modes** — §B4 (4 options; none chosen; several
  already partly live).
- **§C3 answered** — neutral for the known three; marginally risk-*raising* for the
  novel-reason tail; the specific guards scale to new symbols for free.

**Most-valuable-finding check (from the brief):** the outcome landed *mostly* on the
predicted "second most valuable" — a structural guard exists and the three named causes
are closed better than the prior doc implied (the M-U1/M-O8/M-O9 pattern). It is **not**
the "class still wide open" outcome. The one non-closed piece is the design question of
whether the day-halt should survive a successful single-position flatten (§B), plus the
two narrow LATENT residuals above.

---

## Evidence appendix

**Deployment (all ancestors of `HEAD`; code ledger empty ⇒ live on VM):**
```
7614941  15-Jun 20:45  FIX-179  P0: SL-M → SL stop-limit (market-protection), MIS-only, EXITING
7f93c2f  16-Jun 16:46  FIX-181  tick-snap on SL/TGT, marketable-LIMIT emergency exits
de4dd31  16-Jun 20:57  FIX-182  EOD broker-driven squareoff + human-order reclassification
6cb29b0  18-Jun 12:01  FIX-185  hard position cap during restart burst
3af8310  19-Jun 12:09  FIX-190  incident replay test (THELEELA cascade cannot recur)
ed8b27a  19-Jun 16:14  Task 4   reconciler resolves trades stuck in EXITING (closes 19-Jun gap)
8812026  23-Jun 02:06  FIX-190  circuit-band placeability gate (Bug D) + leg-asymmetric exit + P2 tgt_retry
fb68806  23-Jun 02:06  FIX-190  pre-fill circuit-proximity entry reject (both legs)
478673f  23-Jun 23:11  FIX-181  fail-safe tick snap (DEFAULT_TICK fallback) + snap modify_order
1313a08  ~23-Jun       refactor drop redundant order_placer._round_to_tick (adapter = sole snap point)
04f8dd4  24-Jun 11:57  FIX-190  circuit-clamp-aware SL/TGT after-check (no false-flag on clamped exits)
5f116c0  05-Jul 16:13  Wave-2   HARD_KILL retry re-derives (side,qty) per attempt (no oversell, H-4)
```

**Regression run on HEAD (read-only; no service/DB/order touched):**
```
tests/integration/test_fix190_incident_replay.py   (Bug D / Bug C / Bug A replay of THELEELA)
tests/unit/test_tick_failsafe_snap.py
tests/unit/test_nocil_clamp_fix.py
tests/unit/test_h3_exit_retry_guard.py
tests/unit/test_price_math.py
=> 55 passed in 1.49s
```

**Key code references:**
- Exit failure ladder: `orders/order_placer.py:2850-2899` (raise → LTP-retry / emergency+kill),
  `:2909-2929` (Bug C SL-only), `:3445-3602` (retry machinery), `:3734-3762` (hard_kill),
  `:3807-3962` (emergency market exit, reverse-aware).
- Exit protocol (SL-first, leg-asymmetric, placeability gate):
  `orders/order_protocol_limit.py:297-360` (gate + SLUnplaceableError), `:362-372` (SL stop-limit,
  P0 15-Jun), `:421-495` (TGT → `_partial_sl_only` on failure).
- Price math: `orders/price_math.py` — `round_to_tick`, `calc_sl_limit_price` (P0 dates in
  docstring), `marketable_limit_price`, `clamp_exit_into_band` (Bug D + NOCIL polarity).
- Adapter tick chokepoint: `broker/zerodha_adapter.py:1270-1320` (`_snap_to_tick`, "authoritative
  net for ALL placements"); `place_order:604-615` (no `market_protection` kwarg).
- Reverse-aware kill-flatten: `capital/kill_switch.py:1400-1600` (Bug A/E, H-4/H-5, FIX-181 sweep);
  `_marketable_exit_params:1187-1204` (MARKET fallback).
- Entry-side gate: `screening/hard_gate.py:52-111` (`circuit_proximity_reason`);
  `screening/secondary_screener.py:202-218` (`REJECTED_CIRCUIT_PROXIMITY`).
- Design lock: `orders/order_placer.py:102-125` (OP-NS1..NS5); SYSTEM_MAP `docs/SYSTEM_MAP.md:1006-1030`.

**Not in scope / unchanged:** thread 2 (pre-15-Jun large orphans); the six ordinary
Tier-B items; retention/prune/watchman/F1/F2; the strategy revision itself; any fix,
design, deploy, or order. Read-only throughout. Findings-only.
