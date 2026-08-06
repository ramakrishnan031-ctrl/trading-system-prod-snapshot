# FRIDAY MORNING — 07-Aug-2026 · ⛔ ONE SCREEN. RECORD, DO NOT ACT.

> # ⛔⛔ **STANDING INSTRUCTION — READ BEFORE ANYTHING ELSE**
> # **DO NOT MANUALLY BUY ATULAUTO while GTT `330657774` rests.**
> ⭐ **The system CANNOT re-enter** — the phantom `OPEN` row blocks the symbol through the
> product-blind gates. ⇒ **a manual buy is the ONLY path by which that stale sell can fire on real
> shares.** ⛔ This holds until the GTT is gone, not until the trade looks closed.

---

## 1. 🔴 THE MEASUREMENT OF THE WEEK — AND IT IS FREE

**At the first `cnc_gtt_monitor` cycle after boot: does the CNC `−1` positions row for ATULAUTO
still exist?**

| outcome | mechanism | what follows |
|---|---|---|
| **GONE** | `held = 0` ⇒ `:487` opens ⇒ **branch 4 fires** | ⭐ trade closes · **₹587.40 released** · resting GTT `330657774` deleted · slot freed · symbol unblocked |
| **SURVIVES** | `abs()` at `:464` ⇒ `held = 1` | `healthy:ATULAUTO`, **nothing changes**, the phantom persists another day |

⭐⭐ **It settles a broker-behaviour fact nobody could establish from source, and it decides how
urgent F6's build is.** ⛔ **Record it either way. DO NOT act on it.**

**Baseline to compare against (P, measured 06-Aug 19:08:38):** `get_positions` → **`2 positions`**;
`get_holdings` → **`0 holdings`** (15:23:40). Both trades still `OPEN`, both `gtt_state` `ACTIVE`.

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system
grep "get_positions call_end" logs/system_$(date +%F).log | tail -2
grep -E "cnc_gtt_monitor|healthy:ATULAUTO" logs/system_$(date +%F).log | head -20'
```

## 2. THE STANDARD BOOT CHECKS

- **Token file FIRST** — ⛔ a failed 08:15 refresh is **SILENT**: no token ⇒ no watcher start ⇒ no
  boot, **no error, no alert.** *"No order today"* has two causes; this distinguishes them.
- **Branch** · **kill cleared** (`clear_stale_state` runs at boot **only**).
- **The boot seed** — seed = `broker.net − carryover` (carryover is **0** at 08:15).
  ⚠️ **Both CNC reservations replay again:** ATULAUTO **₹587.40** (`ee9af41eae554c35`) — **the
  phantom** — plus DIFFNKG **₹446.10** (`0184b66d4c214416`) = **₹1,033.50**.
  ⛔ **KEY ON `reservation_id`, NEVER `trade_id`** — only 1.9 % of `fm_ledger` rows carry a
  `trade_id`, so a `trade_id` query returns **EMPTY**, which reads as *"capital released"*: a
  **certain false alarm on the one morning it matters.**
  **(P) 06-Aug:** both show `RESERVE → COMMIT`, **neither has a RELEASE row.**

## 3. ⚠️ IF THE STOP WAS **NOT** TAKEN — the Thursday process is still running

⭐ **The counters are FINE — this was measured at source on 06-Aug** (`STOP_PROCEDURE_06-Aug-2026.md`
§4): the daily-trade cap, both daily-loss halves, consecutive-losses and the strategy governor all
**re-read the clock per call** and query **by date**; FIX-051 removed the in-memory P&L accumulator
entirely, so **no stale P&L state exists to go wrong.**

⛔ **What IS boot-bound simply never re-runs — check these two, in this order:**
1. 🔴 **The kill switch did not auto-clear** (`main.py:1914`, boot-only). A prior-day kill is still
   sitting there with nothing to clear it. ⭐ Direction is **restrictive — entries blocked**, i.e.
   fail-safe, not permissive. **Check it before reading "no trades today" as anything else.**
2. **The capital seed is Thursday's** (`initialize()`, FM13 *"once at startup"*) — never re-seeded
   from `broker.net`. ⚠️ The SU6 holiday guard (`main.py:1754`) is also stale but **inert today**
   (Friday is a trading day).

## 4. DIFFNKG's CHECK1 GATE — it carries into Friday

⛔ **The same three checks**, and the one that matters: **an ACTIVE `gtt_state` row with a NULL or
mismatched `trade_id` DOES NOT SKIP.** **(P) 06-Aug 19:08:44:** `330658430 → trd_010f8e21…` matched
exactly. ⚠️ `_check_stuck_exiting` is **UNGUARDED**; the broker cancel (`:1264`) runs **before** the
product read (`:1271`).

---

⛔ **DO NOT:** act on §1 · manually buy ATULAUTO · close the phantom by hand · treat a quiet morning
as a healthy one without §2's token check.
