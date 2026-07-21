# DESIGN — The Midnight Day-Floor Hazard (M-C1 seed / rehydrate)

**21-Jul-2026. DESIGN ONLY — for ChatGPT review, no implementation.** A real but low-probability,
low-magnitude, self-correcting capital-path defect in the M-C1 live-seed machinery. Unblocked now
that E4/W10 is CONFIRMED.

---

## B1 — THE HAZARD, from code (pinned by symbol — line numbers may have drifted post-merge)

The live-seed cancellation (M-C1) is `_total = broker.net − Σ + Σ`: the seed **subtracts** today's
realized-PnL carryover, and rehydrate Phase 2 **re-adds** it, over the *same* rows, so they cancel
to `broker.net`. The two halves each derive their **own** day-floor from their **own** `now_ist()`:

- **Seed** — `main.py:2255-2258`: `_startup_capital = broker.get_margins().net −
  fund_manager.today_realized_pnl_carryover()` (**no argument**). `today_realized_pnl_carryover`
  (fund_manager.py:1821-1824): `if start_of_today_iso is None: start_of_today_iso =
  now_ist().replace(hour=0,…)` — **now_ist() call #1**.
- **Rehydrate** — `main.py:2286`: `fund_manager.rehydrate_from_open_trades()` (**no argument**).
  `rehydrate_from_open_trades` (fund_manager.py:1700-1704): `if start_of_today_iso is None: today =
  now_ist(); start_of_today_iso = today.replace(hour=0,…)` — **now_ist() call #2**.

Both feed the shared `_today_release_used_pnl_rows(start_of_today_iso)` (fund_manager.py:1790-1798),
`WHERE entry_type='RELEASE_USED' AND ts >= ?`. **Two independent floors. If they differ, the Σ's are
over different row sets and the cancellation does not cancel.** (Both call sites already *accept* an
optional `start_of_today_iso`; they derive their own only when `None` — which is how they are
called today.)

## B2 — THE WINDOW

`main.py:2257` (seed) and `main.py:2286` (rehydrate) are ~29 lines apart, with `initialize()` +
`check_paper_capital_consistency` between them. Measured on today's boot: `get_margins` at
`08:15:26.548`, `rehydrate_complete` at `08:15:26.592` — **~44 ms** apart. To diverge, `now_ist()`
call #1 must land at `23:59:59.9xx` (day D) and call #2 at `00:00:00.0xx` (day D+1) — a **~tens-of-ms
window at the exact midnight boundary**.

- The **scheduled 08:15 boot NEVER straddles midnight** — this cannot fire on the normal path.
- Only an **off-schedule boot at ~00:00** can hit it: a crash-restart, a manual restart, or a
  systemd retry happening to fire within tens of ms of midnight. Astronomically rare, but not
  provably impossible.

## B3 — WHAT BREAKS, and its sign

With `floor#1 = D` (seed, pre-midnight) and `floor#2 = D+1` (rehydrate, post-midnight):
- seed `= broker.net − Σ(RELEASE_USED, ts ≥ D 00:00) = broker.net − Σ(D)` (all of yesterday);
- Phase 2 re-adds `Σ(RELEASE_USED, ts ≥ D+1 00:00) = 0` (the fresh day has no trades yet);
- `_total = broker.net − Σ(D)`, vs the intended `broker.net`. **Residue = −Σ(D)**.

**The residue is purely the divergence** — whenever the two floors *match* (both D or both D+1) the
cancellation is exact regardless of the value. Direction: on a **loss day** (Σ(D) < 0, this book's
norm) the residue is **positive → `_total` INFLATED** by `|Σ(D)|` ⇒ the daily-loss threshold
(3% × `_total`) and reservable capital both rise slightly ⇒ **fails PERMISSIVE.** Magnitude =
`|yesterday's realized net|` ≈ tens of Rs (e.g. 18.82 on ~Rs 9,850 ≈ 0.2%). (Reverse straddle —
call #1 post-midnight, #2 pre — gives `+Σ(D)`, restrictive; less likely since the seed fires first.)

## B4 — ⚠️ DEPENDS ON F1/§A2 (resolved: the reader is date-scoped)

F1 established `get_daily_realized_net_pnl` is `WHERE date = ?` (date-scoped;
`docs/audit/f1_daily_loss_after_halt_21jul2026.md`). Therefore the residue is a **one-boot capital
seed error**: the next boot re-seeds from `broker.net` (which reflects true settled capital) with a
consistent floor, and the date-scoped daily-loss counter never compounds it. **Blast radius = one
session, self-correcting** — not a persistent corruption. This bounds severity to: one off-schedule
midnight boot × ~0.2% capital × permissive, for one day. Real, but minor.

## B5 — THE FIX (compute once, pass to both) + ORDERING vs B1 live-seed extraction

**Fix:** compute `start_of_today_iso` **once** in `main()` (a single `now_ist()`), pass it to
**both** `today_realized_pnl_carryover(start_of_today_iso=floor)` and
`rehydrate_from_open_trades(start_of_today_iso=floor)`. Both already accept the parameter, so no
signature change — only the two call sites. Make the unsafe state unrepresentable by removing the
`None`-default derivation at the *seed/rehydrate boot call sites* (a shared floor is the only thing
passed); keep the internal `None`-fallback for standalone/test callers, or route all boot callers
through one helper that takes the floor.

**⭐ ORDERING — do it WITH B1 live-seed extraction, not before/after.** B1 extraction moves the seed
(`main.py:2255-2258`) into a testable function; that function should take `start_of_today_iso` as a
parameter. The midnight fix and the extraction **touch the same seed lines** — doing them
separately means editing those lines twice and re-testing twice. Sequence: extract
`compute_live_seed(broker, fund_manager, start_of_today_iso)` **with the floor as a param**, compute
the floor once in `main()`, and pass the same floor to both the extracted seed and rehydrate. One
edit, one design, one regression.

## B6 — THE FALSIFYING TEST (must be shown able to fail)

Inject a `now_fn` (as the regime harness does) rather than wait for midnight. On a scratch DB with
RELEASE_USED rows dated day D:
- **Plant the divergence:** drive the seed with `now_fn → D 23:59:59.9` (floor#1 = D) and rehydrate
  with `now_fn → D+1 00:00:00.1` (floor#2 = D+1). Assert `_total == broker.net − Σ(D)` — i.e. the
  cancellation is BROKEN (residue = −Σ(D) ≠ 0). **This is RED on the current two-`now_ist()` path.**
- **With the fix** (single floor passed to both): assert `_total == broker.net` (residue 0).
- **Anti-vacuity guard:** assert `Σ(D) != 0` first (a zero-PnL day collapses the two forms and
  proves nothing — the same trap as the E4/W10 no-cost-day case), and confirm the planted-divergence
  assertion actually fails on `HEAD` before trusting the fixed version.

Do not simulate against the live DB; scratch only.

## B7 — REGRESSION MAP

Imports/callers touched: `main.py` (the two boot call sites), `fund_manager.today_realized_pnl_carryover`
+ `rehydrate_from_open_trades` (already parameterised — no signature change). Consumers of `_total`
at boot: the daily-loss threshold and reservable capital (bounded by B4). Existing coverage:
`test_q9_live_seed_mc1_wired.py` (M-C1 cancellation) and `test_q9_post_restart_capital_wired.py`
(rehydrate) — the new test slots beside them. Deploy window: boot-path change ⇒ **off-market**, and
it is precisely the boot path S4 broke, so it ships with a boot self-check pass and the usual
same-window `comm -23` regression. **No implementation in this batch.**

---

*Read-only investigation + design; no code, no service touched. Depends on and cross-links
`f1_daily_loss_after_halt_21jul2026.md` (F1 date-scoping). For ChatGPT review.*
