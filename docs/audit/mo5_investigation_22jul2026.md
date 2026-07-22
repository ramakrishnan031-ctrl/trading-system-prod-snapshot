# M-O5 — reachability + idempotency investigation (22-Jul-2026)

**Read-only. No design, no fix, nothing triggered.** Establishes whether M-O5 is reachable and
whether the obvious fix (wrap `fire_now`'s `_fire` in try/except and unset the flag) is safe —
**before** anyone designs it. Verified against the deployed tree; VM logs (30 days) read-only; no
part of the EOD path exercised. Symbols: `orders/eod_squareoff.py`, `main.py`.

## The defect, confirmed

`fire_now` (`eod_squareoff.py:232-254`) sets `_fired_for_date[today]=True` (`:252`) **before** calling
`_fire` (`:254`), with **no `except` to unset it**. `check_and_fire` — the 15:17 scheduled path
(`:196-229`) — reads the **same** flag (`:216`) and returns `False` if set. So a `fire_now` whose
`_fire` raises leaves the flag stuck, and the scheduled squareoff silently skips. **The shared-flag
premise (§B5) is confirmed:** both paths use `self._fired_for_date`.

The asymmetry is the whole bug: `check_and_fire` sets the same flag but **resets it on exception and
re-raises** (`:223-229`, "reset flag so the next poll can retry"); `fire_now` does not. `_check_restart_recovery`
also does not reset (`:1739-1744`) — but that is **deliberate** ("do NOT reset … we don't want a
polling loop to retry the same broken path"). So `fire_now`'s missing reset is the lone inconsistency.

---

## §A — Reachability: wired to an AUTOMATIC trigger, but never fired

**A1/A2 — every caller of `fire_now` (deployed tree):**
- **`main.py:780` — the ONLY production caller.** Inside `_on_daily_loss_breach` (`:757`), the
  daily-loss-limit-breach callback: `fire_now(reason="daily_loss_limit_breached", triggered_by="fund_manager")`.
  **Automatic**, fired by FundManager when realized daily loss crosses 3% × capital (≈ **Rs 295**).
- **No operator trigger at all.** No CLI wrapper, no GUI/ops-dashboard call, no signal handler
  (`docs/gui_project/G0_BACKEND_INVESTIGATION_REPORT.md:94-95`: "no CLI wrapper for bulk fire_now").
  The 15:15 circuit-breaker SOFT_KILL is a *different* path (`order_monitor`), not `fire_now`.
- Everything else is tests.

⭐ **This corrects the audit's framing.** M-O5 is **not** "manual/emergency, operator-trigger, likely
rare." `fire_now` has **no** operator trigger — it is purely **automatic on a daily-loss breach**. So
the reachability question is not "will an operator run it" but "will the daily-loss path fire it, and
will `_fire` then raise."

**A3 — has it ever fired? NO.** 30 days of VM logs (`system_2026-06-22 … 2026-07-22`, 22 files):
- `daily_loss_limit.breach` / `eod_fire_now` / `daily_loss_limit_breached` = **0 occurrences.**
- "EOD square-off triggered" = **exactly 1 per trading day** (07-02…07-21) — all the **scheduled**
  `check_and_fire` at 15:17, none via `fire_now`.
- Consistent with the record: the daily-loss limit has never been close — the worst day was ~19% of it.

**A3′ — and `_fire` is raise-resistant, so even a breach rarely triggers M-O5.** The routine failures
are all caught *inside* `_fire` and do **not** propagate:
- The per-position exit loop catches **both `BrokerError` and any `Exception`** (`:1312`, `:1321`) →
  `_mark_exit_failed` + `continue`. Placement + DB recording sit in the *same* try (`:1252` place,
  `:1285` INSERT), so there is **no "placed-but-unrecorded" gap** and a broker error never raises `_fire`.
- `get_positions` (`:1046`) and `get_quote` (`:1102`) are caught → fall back. The M-3 write-ahead
  (`:334`) and the COMPLETE update (`:430`) are caught. `reset_daily_pnl` is caught (`:496-500`).
- `_fire` therefore raises only on an **unexpected/infra** error — the initial `get_open_intraday_positions()`
  DB read (`:1026`, unwrapped), `soft_kill` (`:349`, unwrapped, in-process), or an uncaught error in a
  helper — **not** a routine broker failure.

**A4/A5 verdict — neither "unreachable" nor "urgent."** M-O5 is **not** structurally unreachable like
M-U1/M-O8 (it is wired to a live automatic path, so Tier A is **not** empty). But it is **not** urgent
either: the trigger (daily-loss breach) has fired **zero** times in 30 days, and it additionally needs
an **unexpected** exception in a function engineered to swallow routine failures. **Net: a real latent
hazard on a live automatic path, never triggered, requiring a compound low-probability event —
"reachability LOW" stands, but the mechanism the audit gave ("manual/rare operator") is wrong.** It
stays the sole Tier-A item; its priority does not escalate.

---

## §B — Idempotency: the obvious fix is SAFE (earned, not assumed)

**B2/B4 — `_exit_open_positions` is guarded against double-square.** The **E.5 broker-position filter**
(2026-04-25, `:1062-1078`) runs on **every** fire, not just recovery: it fetches `adapter.get_positions()`
(`:1035`) and **skips any DB row whose symbol the broker reports flat** (`:1064-1065`) — the comment is
explicit: *"firing a MARKET reverse on [a stale row] creates a naked short … the filter is now also a
guard against this stale-DB-row class on the regular fire."* So a retry re-queries broker truth and does
**not** re-exit an already-closed position. That is the primary guard against the M-O4-class hazard §B2
feared, and it is **broker-side**, not flag-side (answering B4: the flag is *not* the only guard).

**B1/B3 — so mirroring `check_and_fire` is safe, and it is the right shape.** Because the exit loop
cannot raise mid-placement (per-position catch), a `_fire` that raises did so **either before any order
was placed** (the initial DB read → nothing to double-fire) **or after the loop** (a helper → the E.5
filter guards the retry). And `check_and_fire` **already** uses reset-on-exception + retry (`:223-229`),
so making `fire_now` do the same introduces **no novel hazard** — it adopts a pattern already in
production on the higher-frequency path. The residual double-fire window (a retry running before a prior
*in-flight-unfilled* exit has settled — the same phantom-fill window FIX-063's 2 s sleep covers within
one `_fire`) is **avoided by the realistic timing**: a mid-day daily-loss breach and the 15:17 scheduled
retry are hours apart.

**B3 — the ordering question, answered.** Setting the flag **before** the work is **intentional and
correct**: it is the single-execution / concurrency guard (`:218-221`), and on **success** it must stay
set so the 15:17 fire does not re-square what `fire_now` already squared (`:236-237`). The bug is **not**
"before vs after" — it is the missing reset on the *failure* branch. The preferable failure mode for a
safety squareoff is **fail-open (retry)**, which is what `check_and_fire` already chose and which the
E.5 filter makes safe; `fire_now`'s current **fail-closed** (block the backstop) is the wrong choice for
this path. So the correct fix is to unset the flag **only when `_fire` failed** — mirroring `check_and_fire`
— which preserves the intended "stays set on success."

**B — one sibling gap the fix should not miss.** A `fire_now` that **partially fails without raising**
(`_fire` returns normally with `positions_failed>0`) *also* leaves the flag set and blocks the 15:17
retry of the un-squared positions. "Fired" is not "successfully squared everything." The raise case and
the partial-failure case are the same family; a fix that only catches the exception addresses half of it.

**B — the consequence is bounded today (a fourth delivery coupling).** Under `force_intraday_only`
(MIS-only), the broker **auto-squares MIS at ~15:20** with a ₹50+GST penalty (the code knows this,
`:1008-1010`, `:1057-1059`). So a skipped 15:17 system squareoff of MIS positions → the broker flattens
them at 15:20 at a worse price, **not overnight**. The "unbounded overnight exposure" is real **only for
DELIVERY (CNC)**, which is disabled — so M-O5's worst case, like M-C2/M-O4/M-O7, **worsens the day
delivery is enabled**. Today its worst case is degraded-price broker-forced exits + un-cancelled SL/TGT
legs (Audit-#6 naked-reverse risk), not overnight carry.

---

## §C — Parity and coverage

**C1 — the squareoff DIFFERS paper vs live, and M-O5's raise is live-only.** In paper, `get_positions()`
returns `[]` (per `test_end_to_end_smoke.py:386`), so the E.5 filter skips **all** rows and
`_exit_open_positions` places **zero** exits — a position no-op. Paper exercises the flag / soft_kill /
logging but not real order placement or the broker-error paths, so the realistic `_fire` raise
(infra/broker) will **not** reproduce naturally in paper. **Any fix's test must inject a mock
adapter/store that raises** to drive the exception path; paper mode alone cannot cover it (Rule #5).

**C3 — the exception path is UNTESTED, which is why this survived.** `fire_now`'s **success** path is
well covered (marks flag, blocks `check_and_fire` `:704-707`, `reset_daily_pnl`, gate clear, checkpoint).
But there is **no** test that forces `_fire` to raise out of `fire_now`, **no** assertion that a raised
`fire_now` leaves the flag stuck and the 15:17 fire skips (the M-O5 bug), and **no** test of
`check_and_fire`'s own reset-on-exception + retry. The asymmetry between the two paths' failure handling
was never asserted — the coverage gap *is* part of the cause.

---

## Bottom line

- **The obvious fix is SAFE** (mirror `check_and_fire`: reset the flag only when `_fire` failed). The
  double-square hazard is mitigated by the E.5 broker-position filter + the per-position catch + the
  realistic hours-apart timing, and `check_and_fire` already runs this exact pattern. This is an
  **earned** "the obvious fix is fine," not an assumed one — with two riders: (1) also handle the
  partial-failure-without-raise sibling; (2) the fix's **test must simulate a raising adapter** because
  paper cannot.
- **Reachability is LOW but for a corrected reason:** `fire_now` is wired to an **automatic** daily-loss
  trigger (not manual), which has fired **0 times in 30 days**, and M-O5 additionally needs an
  **unexpected** exception in a raise-resistant `_fire`. It stays the sole Tier-A item; priority
  unchanged (routine, not urgent, not dissolved).
- **Consequence is bounded today** by the broker's 15:20 MIS auto-square; it becomes overnight-unbounded
  only if delivery is enabled — a fourth item behind the delivery flip.

*Read-only throughout; no code, config, schema, or state changed; nothing on the EOD path was
triggered; the service was not touched. Design and implementation are off-market, after review.*
