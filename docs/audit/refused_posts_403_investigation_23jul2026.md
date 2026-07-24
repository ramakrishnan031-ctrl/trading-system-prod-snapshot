# The 403 "Blind Spot" — Investigation (READ-ONLY)

**Date:** 2026-07-23 (evening, off-market, service inactive, book flat)
**Scope:** INVESTIGATE ONLY. No code, no design, no instrumentation, no deploy. Read-only VM access
(`immutable=1` on the chmod-444 19-Jul snapshot; `mode=ro` on the live DB for the recent delta).
**Question (§A first, loudly):** are the ~25,960 refused POSTs *correct refusals*, or is genuine
signal being lost? The standing register calls this "the one genuine blind spot" and the 19-Jul
signal-mortality census *assumed* "correctly refused (outside entry window)." This report verifies
that inherited assumption rather than inheriting it.

---

## ⭐ VERDICT — stated first, stated loudly

> **THE REFUSALS ARE ALL CORRECT. There is NO lost-signal bug. No RED case exists.**
>
> **But the inherited framing is wrong on two counts, and both matter:**
>
> 1. **"Outside entry window" is only ~half the story.** 46.5% of the historical 403s were
>    emitted *inside* [10:00, 15:00) — they are **kill-switch refusals**, not window refusals.
>    They are still correct (the system was halted and correctly refused to trade), but the
>    *reason* nobody had established is a different one, and it ties this directly to the
>    late-June HARD_KILL / circuit-breaker incidents.
>
> 2. **"Blind spot" overstates it.** Every refused POST **is** recorded — `webhook_audit` has a
>    row per POST with timestamp, source IP, scanner, response code, and payload size. What is
>    *not* stored is the count of *inner signals* inside each refused POST (the JSON body is not
>    parsed before the gate). That is a much smaller gap, and it is **estimable from the stored
>    `payload_size_bytes` with no code change at all.**
>
> **Consequence for priority:** this is **not** ~338k lost signals. It is ~28.7k correctly-refused
> POSTs, fully audited at the POST level, of which the inner-signal *tally* is uncounted-but-
> estimable. **No signal-path change is warranted.** §B collapses to "the number already exists."

---

## A. Are the refusals correct? (the crux)

### A2 — Where the 403 is emitted (code, current source)

`signals/webhook_receiver.py`, `_process_request` — **exactly two** 403 return points:

| Line | Guard | Condition |
|------|-------|-----------|
| `:513` | `if self._ks and self._ks.is_active()` | **kill switch active** |
| `:528` | `if not self._mw.is_entry_allowed(now)` | **outside entry window / holiday** |

Gate order in `_process_request`: **auth → 404(unknown scanner) → EOD-route → KILL(403 `:513`) →
503(backpressure) → WINDOW(403 `:528`) → 400(parse)**. Auth (401) precedes *both* 403s. Backpressure
between them returns **503, not 403**. `is_entry_allowed` (`core/market_windows.py:143`) =
`holiday → False; else entry_start ≤ t < entry_end`. The receiver calls the **global**
`is_entry_allowed`, **not** `is_entry_allowed_for_strategy` (`:152`) — per-strategy windows are
enforced downstream and surface as *rejected inner signals inside a 200*, never as a 403.

**Structural linchpin:** an in-window, non-holiday POST passes `:527`, so its *only* remaining 403
path is `:513` (kill). **There is no code path that emits an in-window 403 with the kill switch
inactive.** The RED case is not merely absent from the data — it is not expressible in the code.

Config (`config/system_config.yaml`): `entry_start "10:00"` [launch-phase; documented "relax
toward 09:20 as the account scales"], `entry_end "15:00"`, `market_open "09:15"`. Secret is wired
live (`main.py:2813 secret_token=os.environ.get("WEBHOOK_SECRET")`) and is a **required, fail-fast**
startup secret in every mode (`main.py:217/226`) ⇒ auth is always enforced.

### A3 — 401 vs 403 is correct

401 = **pre-auth**, unauthenticated (`:481/:489/:492/:494`). 403 = **post-auth**, authenticated-
but-forbidden (kill/window). The codes are used correctly and the two conditions are genuinely
distinct. This is **not** a repeat of the `/health` 401 imprecision episode.

### A4 — Who is sending them (source characterised, not assumed)

403s by source IP (snapshot 12-Jun → 16-Jul, 25,960 rows):

| source_ip | posts | note |
|-----------|------:|------|
| **23.106.53.213** | **25,957** | Chartink / Leaseweb (AS59253) — Rama's scanner source |
| 23.106.53.222 | 1 | same Chartink /24 |
| 23.106.53.196 | 1 | same Chartink /24 |
| 127.0.0.1 | 1 | localhost (self/health) |

**99.99% from a single Chartink IP. Zero from random-internet IPs.** All 13 `scanner_name`s are
Rama's own strategies (`positional_sector_rotation`, `vwap_bounce_long`, `gap_go_long`, …).

**Background noise (hypothesis b) is refuted twice over:**
- by source IP (all Chartink), and
- by the **zero-401 result** — see below.

### A5 — Correlation with trading days / hours (this settles A4)

403s by hour-of-day (IST):

| hour | 09 | 10 | 11 | 12 | 13 | 14 | 15 | 21 |
|------|---:|---:|---:|---:|---:|---:|---:|---:|
| posts | 7,462 | 1,405 | 1,913 | 2,694 | 3,129 | 2,928 | 6,427 | 2 |

**All 403s fall on weekdays; none on weekends.** The distribution is *not* uniform-around-the-clock
(which would indicate noise) — it tracks Chartink's scan cadence: a pre-window burst at the 09:15
open, and a post-window tail at/after 15:00.

403 split by window × weekday:

| bucket | posts | share | verdict |
|--------|------:|------:|---------|
| **out-of-window**, weekday | **13,891** | 53.5% | correct — entry-window policy |
| **in-window [10:00,15:00)**, weekday | **12,069** | 46.5% | correct — **kill-switch** (see A-proof) |
| weekend | 0 | — | — |

Out-of-window splits ≈ evenly: **7,462 before 10:00** (the launch-phase `entry_start` discarding
open-bell scans) and **6,429 at/after 15:00** (Chartink still scanning after the window closes).

### A6 — Refusal *rate* (interpretable, not a raw count)

| window | total POSTs | 200 | 403 | 503 | 401/404/429/400/500 | **403 rate** |
|--------|------------:|----:|----:|----:|--------------------:|-------------:|
| snapshot 12-Jun → 16-Jul | 89,794 | 63,816 | 25,960 | 18 | **0** | **28.9%** |
| live delta 17-Jul → 23-Jul 15:29 | 15,117 | 12,397 | 2,720 | 0 | 0 | **18.0%** |
| **total to 23-Jul** | **104,911** | 76,213 | **28,680** | 18 | 0 | 27.3% |

The 25,960 matches the census figure exactly. **Zero 401 rows across 89,794 audited POSTs** — and
401s *are* audited (the `finally` at `_handle_webhook:453-458` writes a row for every return from
`_process_request`, 401 paths included). So zero-401 is a *real* result: **no unauthenticated POST
ever reached the receiver.** Only Chartink (which holds the token) is hitting the endpoint.

### PROOF that the 12,069 in-window 403s are correct kill-switch refusals

Confirmed **three independent ways**:

**1. Code (above):** in-window ⇒ passes `:527` ⇒ the only 403 left is `:513` (kill). RED case is
inexpressible.

**2. Data — block structure.** In-window 403s occur on **exactly 7 dates**: 15/16/17/18/19-Jun,
23-Jun, 1-Jul. The "latch test" (200-POSTs occurring *after* the first in-window 403 of the day)
splits them cleanly:

| date | first in-window 403 | 200s after it | shape |
|------|---------------------|--------------:|-------|
| 15-Jun | 11:36 | 0 | clean latch (kill, nothing after) |
| 16-Jun | 10:01 | 0 | clean latch |
| 17-Jun | 13:43 | 0 | clean latch |
| 18-Jun | 10:38 | 921 | **two** kill→resume cycles |
| 19-Jun | 10:01 | 1,430 | kill → 3-h service-down gap → resume |
| 23-Jun | 10:08 | 1,789 | one kill→resume cycle |
| 01-Jul | 11:32 | 143 | one kill→resume cycle |

The 10-minute block view shows **no fine-grained interleaving** — accepted and refused POSTs form
*contiguous blocks* bounded by kill/resume events (e.g. 18-Jun: accept → kill ~10:35 → resume ~11:05
→ accept → kill ~12:35 → refused to close). A global kill switch is a latch; the day-level
"interleaving" is simply multiple latch cycles. Every in-window 403 sits inside a kill-bounded
block. (The presence of thousands of *accepted* 200s on each of these dates also proves they are
trading days, not holidays — eliminating the only other in-window-403 explanation.)

**3. Events — `system_events` corroboration.** Restart/clear timestamps align exactly with the
block boundaries:
- 18-Jun: SHUTDOWN/STARTUP at 10:36 and 10:58→11:13 — the block edges.
- 19-Jun: SHUTDOWN **10:07:54** → STARTUP **13:24:33** — the 3-hour POST gap exactly (service down
  = HARD_KILL halt; POSTs during it get connection-refused, no audit row).
- 23-Jun: restart **12:26** — the resume point.
- 01-Jul: restart **13:59** — the resume point.
- `KILL_AUTO_CLEARED` fires **every trading morning 22–25, 29–30 Jun, 1–2 Jul**, each
  `previous_state: SOFT_KILL, reason: circuit_breaker_force_close…` — the circuit breaker was
  tripping almost daily through late June.

This is the debris of the **known** late-June instability (orphan / `_place_limit_triple_exits`
reject → HARD_KILL forensics, `orphan_adoption_forensics_22jul2026.md`). The 403s are a *downstream
symptom* of those halts, correctly refusing to trade a killed system.

**Ongoing check:** the live delta (17-Jul → 23-Jul) has **zero** in-window weekday 403s. The
phenomenon is **historical only — it has not recurred since 1-Jul.** Currently 100% of 403s are
out-of-window (the census's original assumption is fully correct *for the present*).

---

## B. Counting cost — since the refusals are correct

**B2 first — is the count already available read-only? YES, at the level that matters.**

- **POST-level: already fully counted.** `webhook_audit` stores one row per refused POST with
  `ts, scanner_name, source_ip, payload_size_bytes, response_code, duration_ms`. Every query in this
  report is that count, obtained read-only with **zero** code change. The "blind spot" at POST
  granularity **does not exist** — it was already instrumented.
- **Inner-signal count: not stored, but estimable read-only.** Refused POSTs carry
  `signals_accepted = signals_rejected = 0` because the body is never parsed pre-gate. But
  `payload_size_bytes` **is** stored, and the accepted side gives a calibratable ratio
  (~12.4 inner/POST in June, ~6 in July — so the census's "~13/POST → ~338k" is a *rough,
  period-sensitive upper-ish estimate*, not a measurement). A `payload_size`-based estimate needs
  **no receiver change** whatsoever.

**B1 (insertion point), B3 (storage), B4 (anti-dup):** moot as a fix.
- B4: `webhook_audit` already counts POSTs (no double-count risk; nothing to add there).
- B3: no new rows are needed — the rows already exist.
- B1: the only thing genuinely missing is a *precise* inner-signal tally on refused POSTs, which
  would require parsing an **untrusted body before the auth/kill/window gate** — i.e. doing work on
  the signal ingress for POSTs we are (correctly) refusing. That is the one place the careful loop
  says *don't touch*, and it buys only a marginal precision gain over the `payload_size` estimate.

**Sizing conclusion:** **no code change is warranted.** If a precise "signals-sent-while-refused"
number is ever wanted, derive it read-only from `payload_size_bytes` (calibrated against the
accepted-side bytes→signals ratio) — never by parsing bodies at the ingress.

---

## Peripheral observations (surfaced here, NOT in scope to act on)

1. **`entry_start = 10:00` discards ~7,462 open-bell POSTs** (09:15–10:00) over the snapshot window
   — the quantified cost of the launch-phase start. Decision-relevant to the already-open "relax
   toward 09:20" question; not a defect.
2. **Circuit breaker tripped a SOFT_KILL nearly every trading day in late June**
   (`KILL_AUTO_CLEARED` 22–25, 29–30 Jun, 1–2 Jul, `reason=circuit_breaker_force_close`). This is
   the *known* instability period and post-window (15:15); it is the *cause* of the in-window 403s,
   not a new finding — recorded for the trail.
3. **`kill_switch_state` is a single-row STATE table, not a history log** — it cannot be joined
   against `webhook_audit` for point-in-time kill state; the timeline had to be reconstructed from
   `system_events` + the 403 block structure. Noted for any future "was the kill active at time T"
   question.

---

## What "done" means (per the handover) — all satisfied

- Refusals established **correct**, with evidence (code + block-structure + events). ✅
- Source **characterised** (Chartink 99.99%, not assumed). ✅
- 403-vs-401 choice **checked** (correct). ✅
- Counting options **sized** — including the outcome that **no code change is needed** (the count
  already exists at POST level; inner tally is estimable read-only). ✅
- The most-valuable outcome (a RED case) was **searched for and shown not to exist** — both in data
  and by construction. The second-most-valuable (framing correction) **was found**: it is not "the
  one genuine blind spot," and ~half the refusals were mis-attributed to the entry window.

## Constraints honoured

Read-only throughout: `immutable=1` snapshot for the historical window, `mode=ro` (WAL-aware, no
`immutable`) for the live delta. No `scripts/*.py --db`. `webhook_receiver.py` and the signal path
untouched. Service not restarted. No test POST sent. Nothing deployed.

---

### Appendix — evidence sources
- Snapshot (chmod-444, zero-risk): `/home/ubuntu/preserved/signal_census_19jul2026/trading_system_snapshot_20260719.db`
  (`webhook_audit`: 89,794 rows, 2026-06-12T09:17 → 2026-07-16T10:54).
- Live delta (read-only): `…/data_store/trading_system.db?mode=ro` (`webhook_audit`: 104,911 rows to
  2026-07-23T15:29).
- Code: `signals/webhook_receiver.py` (`_handle_webhook`, `_process_request`, `_write_audit`),
  `core/market_windows.py:143`, `config/system_config.yaml`, `main.py:217/226/2813`.
- Corroboration: `system_events` (STARTUP/SHUTDOWN/KILL_AUTO_CLEARED), `kill_switch_state`.
