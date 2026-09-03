# INCIDENT — 03-Sep-2026 · ANANTRAJ naked position

**Written 03-Sep-2026 evening, market closed. Read-only investigation under FILE 125 §6.**
⛔ Nothing was edited, restarted, resumed or committed. ⛔ No fix is designed here.
Every line is marked **MEASURED** or **HYPOTHESIS**.

Sources: `logs/system_2026-09-03.log` on the VM · `data_store/trading_system.db`
(read-only, `mode=ro`) · deployed source at `2d08436` · Kite Connect docs (cited).
Engine PID **1101999**, started 03-Sep 08:15:29, `NRestarts=0` — the process that
produced all of this is still the one that booted this morning.

---

## 1 · SUMMARY

**MEASURED.** Trade `trd_b7d0f9ce3b31405490f5a521380d9256`, ANANTRAJ, LONG, qty 1,
entry 10:01:33 @ **629.50**, exit 15:12:25 @ **628.05**, **net −₹2.13**.

🔴 The MIS auto-squareoff **cancelled the position's protective orders and then
refused to place the exit**, leaving the position naked. The emergency fallback
behind it was **rejected by the broker 8 times**. The position was ultimately
closed **manually by Rama**.

⚠️ **The money lost is trivial. The exposure was not, and it ended only because a
human intervened.** The SL trigger was 620.08 and price never reached it — the
naked window cost nothing **by luck, not by design**.

---

## 2 · TIMELINE — MEASURED, from the log

| time | event |
|---|---|
| 10:01:33 | ENTRY `260903170309107` COMPLETE. SL `…432` (trigger 620.077) and TGT `…434` (643.63) both placed. |
| 15:07:04.489 | `MIS_AUTO_SQUAREOFF_SCAN` **PASS_1**, `mis_candidates=1` |
| 15:07:04.535 | `cancel_order …432` → **`success=True`** (43 ms) |
| 15:07:04.574 | `cancel_order …434` → **`success=True`** (38 ms) |
| 15:07:04.588 | `get_order_history …432` → **7 entries** |
| 15:07:04.601 | `get_order_history …434` → **4 entries** (unchanged from 15:07:03.696) |
| 15:07:04.601 | 🔴 **`CANCEL_FAILED` PASS_1 … "exit NOT submitted"** |
| | ⇒ **both protections cancelled, no exit → NAKED** |
| 15:07:05.716 / .734 | `order_placer.terminal_status_exit_leg_skipped` ×2 — both orders now show **7 entries**, i.e. terminal **~1.1 s later** |
| 15:07:16.002 | `order_reconciler` **G5b CRASH_RECOVERY_SL**: "ANANTRAJ LONG has no active SL order — placing recovery order" |
| 15:07:16.060 | recovery SL **`260903171147099`** placed ⇒ **naked window 1 ends: ≈ 11.5 s** |
| 15:10:03.422 | **PASS_2**, `mis_candidates=1` |
| 15:10:03.459 | `cancel_order …7099` → **`success=True`** (36 ms) |
| 15:10:03.475 | `get_order_history …7099` → **4 entries** ⇒ 🔴 **`CANCEL_FAILED` PASS_2 … "exit NOT submitted"** |
| 15:10:17.512 | `get_order_history …7099` → **7 entries** |
| 15:10:17.528 | 🔴 `CHECK9 MISSING_EXITS … NOT in broker open orders — naked position` |
| 15:10:17.585 | 🔴 **emergency MARKET exit REJECTED** — *"Market orders without market protection are not allowed via API."* |
| 15:10:17.588 | `SOFT_KILL` — `MISSING_EXITS` |
| 15:10:17 → 15:12:09 | 🔴 `CHECK9 MISSING_EXITS` **×8**, emergency exit **FAILED ×8** |
| 15:12:25.377 | `cancel_order …7099` → **"Order cannot be cancelled as it is being processed. Try later."** |
| 15:12:25.452 | `check1: exit_price resolved from broker trades: 628.05` |
| 15:12:26.197 | `CHECK1 OWN_LEG_CLOSE … closure_source=OWN_SL` |
| 16:30–16:31 | `get_positions` → **`0 positions`** (flat, sustained) |

**MEASURED counts:** `CHECK9 MISSING_EXITS` **8** · `EMERGENCY MARKET EXIT FAILED`
**8** · `"Market orders without market protection"` **16** occurrences.

**MEASURED exposure:** naked window 1 ≈ **11.5 s**; protection effectively absent
again from 15:10:03 until the manual close at 15:12:25 ≈ **2 m 22 s**.

---

## 3 · FILE 125 §4 HYPOTHESIS — 🔴 **CONFIRMED**

The hypothesis was: *the squareoff's own cancels succeeded while the confirmation
read failed, so the routine created the naked position it exists to prevent.*

**MEASURED, and it is confirmed:**
- both PASS_1 cancels returned **`success=True`** at 15:07:04.535 / .574;
- the DB records SL `…432` and TGT `…434` as **`CANCELLED`**, `qty_filled=0`,
  `updated_at 15:07:05`;
- the exit was **not submitted**;
- the same orders read as **terminal 1.1 s later** (15:07:05.7).

⇒ **The MIS auto-squareoff destroyed the protection and placed no exit.**

⚠️ **CORRECTION TO FILE 125 §3.** §3 states the guard *"decides on the return
value of the cancel call"*. **MEASURED: it does not — it does re-read the broker.**
`_verify_cancelled` calls `get_order_history` for every resting order. The real
defect is **timing, not source of truth** (see D2). This matters because it
changes the fix.

---

## 4 · DEFECTS

### 🔴 D1 — the emergency exit places a raw MARKET order the broker refuses
**MEASURED.** `orders/order_reconciler.py` `_check9_missing_exits`, ~line 2996:
```python
placed = self._adapter.place_order(
    symbol=symbol, side=side, qty=qty,
    price=0.0,
    order_type="MARKET",              # ← rejected, every time
    intent=intent,
    tag=truncate_tag_for_broker(trade_id),
)
```
⛔ No LTP fetch. ⛔ No `market_protection` parameter. ⛔ No limit fallback.

🔴 **The correct implementation already exists ~680 lines above, in the same
file** (`order_reconciler.py` ~2317):
```python
if ltp and ltp > 0:
    price = marketable_limit_price(exit_side, ltp, EMERGENCY_EXIT_BUFFER_PCT, DEFAULT_TICK)
    order_type = "LIMIT"
else:
    price = 0.0
    order_type = "MARKET"
```
whose own comment reads *"MARKET, defeating the FIX-181 cap."*

🔴 **THE CONSTRAINT WAS ALREADY KNOWN — SINCE JULY.** `scripts/t2_cnc_gtt_realtest.py`
documents *"the 10-Jul canary block"*: *"Zerodha's API refuses MARKET without
market-protection … so T2 places a marketable LIMIT instead — **identical to the
live emergency-exit path**"*. ⚠️ **MEASURED: that parity claim is FALSE.** CHECK9 —
described in its own code comment as *"the naked-position last line of defense"* —
never received FIX-181.

⚠️ **This path has now failed at broker validation TWICE in production**, for two
different payload reasons. Its own comment records the first: *"FIX (01-Jul-2026):
truncate the tag — a full trade_id … exceeded Zerodha's 20-char tag limit and
REJECTED this emergency exit (the naked-position last line of defense; 1-Jul
BANSALWIRE)."* Each time only the specific symptom was fixed.

**MEASURED — MARKET order call sites (40 matches; the exit-path ones):**
`order_reconciler.py` 1945 · 2323 · 3000 · 3017 · 3179 · 3196 —
`eod_squareoff.py` 1294 · 1297 · 1548 · 1668 —
`mis_autosquareoff.py` **71** (`PASS_2_EXIT_PROTOCOL = "MARKET"`) · **546** —
`sl_breach_monitor.py` 220 · `structure_exit_manager.py` 442 ·
`order_placer.py` 3910 · `kill_switch.py` 1327.
⭐ Some of these (2317, 1542, 214, 438, 3904) reach `marketable_limit_price` first;
⛔ CHECK9's does not. ⛔ No call site was changed.

🔴 **COMPOUNDING, MEASURED:** `mis_autosquareoff.py:71` sets
`PASS_2_EXIT_PROTOCOL = "MARKET"` and line 546 uses `"MARKET"` for **both** passes.
⇒ **Even if the cancel guard had passed, the squareoff's own exit would have been
rejected by the same rule.** There were two independent reasons this position
could not be auto-exited.

### 🔴 D2 — `_verify_cancelled` polls once, immediately, and never re-reads
**MEASURED.** `orders/mis_autosquareoff.py:565-579`:
```python
def _verify_cancelled(self, resting) -> bool:
    """Confirm at the broker. 'Cancel accepted' is not 'cancel effective'."""
    for row in resting:
        ...
        hist = self._adapter.get_order_history(str(oid))
        ...
        last = hist[-1]
        status = str(getattr(last, "status", "")).upper()
        if status not in ("CANCELLED", "REJECTED", "COMPLETE"):
            return False
    return True
```
⛔ No retry. ⛔ No sleep. ⛔ No settle window. A single read **~15 ms** after the
cancel. The docstring states the right principle and the code does not implement
it.

🔴 **The ordering is what makes this dangerous.** The cancels are sent **first**
and succeed; verification runs **after**. So a verification miss leaves the
position with its protection **already destroyed** and no exit. The guard's own
comment — *"A double exit is worse than a late one. Do NOT submit blindly."* — is
sound, but by the time it fires the protection is already gone.

⚠️ FILE 125 §3's states (a)/(b)/(c): **(a) is handled** — `CANCELLED / REJECTED /
COMPLETE` are all accepted. **(c) is the gap** — a non-terminal or unknown status
returns `False` with no re-read.

### D3 — the SOFT_KILL alert asserts a protection its own trigger disproves
**MEASURED (alert body, per FILE 125 §5).** A `MISSING_EXITS` kill fires *because*
the exits are missing, then tells the operator *"Intraday positions: managed to
SL/TGT/EOD."* False by construction on that specific kill.

### 🔴 D4 — NEW, not in FILE 125: the closure is mis-attributed
**MEASURED** — `trades` row, single record, self-contradictory:
```
exit_reason    = MANUAL
status         = CLOSED_MANUAL
closure_source = OWN_SL        ← contradicts both of the above
exit_mechanism = (empty)
exits_verified = 1
exits_verify_detail = ok
```
**MEASURED, and it is provably false:** all three protective orders ended
`CANCELLED` with **`qty_filled = 0`** — **no SL ever filled**:

| order | leg | type | status | qty_filled | updated |
|---|---|---|---|---|---|
| 260903170309107 | ENTRY | LIMIT | COMPLETE | — | 10:01:33 |
| 260903170310432 | SL | SL | **CANCELLED** | **0** | 15:07:05 |
| 260903170310434 | TGT | LIMIT | **CANCELLED** | **0** | 15:07:05 |
| 260903171147099 | SL | SL | **CANCELLED** | **0** | 15:17:04 |

⇒ ⭐ **Rama's account is confirmed by the record: he closed it manually.**
⇒ 🔴 `closure_source=OWN_SL` would tell any later analysis **the stop-loss worked**.
It did not. ⇒ 🔴 `exits_verified=1 / "ok"` marks a trade that spent ~2.5 minutes
without working exits as verified.
⚠️ A post-trade review reading this row would see a normal SL exit and find nothing
to investigate.

---

## 5 · THE OTHER §6 ITEMS

**6.7 · watchman — MEASURED, and ⛔ NOT causally linked.**
`trading-watchman`: `ActiveState=inactive`, `SubState=dead`, `MainPID=0`,
**`Result=success`, `ExecMainStatus=0`**, started **08:15:29**.
⇒ It did **not** crash — it exited **cleanly**. `Description=Gemini Log Watchman
(live AI log monitor during market hours)`, `Type=simple`.
⇒ ⭐ It is a **log monitor**, ⛔ not part of the exit path. Its absence did **not**
cause this. ⚠️ **But a "live market-hours monitor" that exits at 08:15:29 is not
monitoring** — a **separate** fault, recorded separately, ⛔ not merged with this one.

**6.8 · flat at broker — MEASURED: YES.** `get_positions` → **`0 positions`**,
sustained through 16:31:15. ⛔ Not merely a local-DB belief.

**6.9 · schedule — MEASURED; the system behaved exactly per its config.**
`config/system_config.yaml`: `entry_end: "15:00"` · `mis_squareoff_cutoff: "15:12"`
(*"source: Zerodha intraday auto-square-off timing page"*) ·
`mis_squareoff_first_offset: 5m` ⇒ **CHECK_1 15:07** ·
`mis_squareoff_second_offset: 2m` ⇒ **CHECK_2 15:10** · `eod_squareoff_time 15:17`.
`core/mis_squareoff_timing.py:86-87` pins the invariant
`15:00 < 15:07 < 15:10 < 15:12 < 15:17`.
⇒ 🔴 **The project record claiming "15:15 entry cutoff" is STALE** — T5 (29-Jun)
moved `entry_end` 15:15 → 15:00. And 15:17 is the **EOD** squareoff, ⛔ not the MIS
passes. ⭐ The observed 15:07/15:10 are correct, ⛔ not a drift.

**6.6 · what Kite actually requires — CITED, ⛔ not from memory.**
`https://kite.trade/docs/connect/v3/orders/`:
- parameter **`market_protection`**;
- values: **greater than 0 up to 100** (percent), or **`-1`** = *"Automatic market
  protection applied by the system as per market protection guidelines"*;
- *"Market protection is only applicable for MARKET and SL-M order types."*
- 🔴 *"**Market protection converts market orders to limit orders**, but execution
  remains subject to exchange-defined LPP ranges. Orders exceeding LPP limits may
  be rejected."*
⇒ ⭐ **MEASURED-relevant:** market protection converts a MARKET order into a limit
order **anyway**. ⛔ No fix is designed here.

**6.1 · ⛔ NOT DONE, and why.** A direct broker pull of the order history for
`260903171147099` and the TGT order was **not** performed: it requires a live
authenticated API call against the trading account, which is outside a read-only
investigation. ⭐ The question it was meant to settle (§4) **was settled instead by
the log**, which records the cancel calls, their `success=True` returns and their
timings directly. ⏸ The broker-side exchange timestamps remain **unpulled**.

---

## 6 · ROUND 2 — THE BROKER BOOK (FILE 126 §1). ⭐ PULLED, ⭐ EVERYTHING SETTLED.

**MEASURED.** Four read-only GET calls (`orders()`, `trades()`, `order_history()` ×3)
against the live session. ⛔ No place/cancel/modify anywhere in the script.
Raw dump: `2026-09-03_ANANTRAJ_broker_book.json` (sha256 `6b76425e…`).
⚠️ Token `expires_at 2026-09-04T05:00` — **this evidence would have been gone at
tomorrow's boot.** FILE 126's urgency was correct.

### 6.1 · §2 — 🔴 **READING A CONFIRMED. The position WAS naked.**
`order_history('260903171147099')`, exchange timestamps:
```
15:07:16  PUT ORDER REQ RECEIVED        15:07:16  TRIGGER PENDING   (exch 15:07:16)
15:07:16  VALIDATION PENDING            15:10:03  CANCEL PENDING    (exch 15:07:16)
15:07:16  OPEN PENDING                  15:10:03  CANCELLED         (exch 15:10:03)
                                        15:10:03  CANCELLED         (exch 15:10:03)
```
⇒ **The exchange cancelled it at 15:10:03.** ⛔ Reading B is dead.
⇒ ⭐ **CHECK9's naked detection at 15:10:17 was CORRECT** — ⛔ not a false positive.
⛔ **There is no sixth defect.** The detector can be trusted.
⭐ The 15:12:25 *"cannot be cancelled as it is being processed"* is a **misleading
broker message for a re-cancel of an already-terminal order** — ⛔ not evidence the
order was live. ⚠️ The system read it as *"may fill"*, which was wrong.

### 6.2 · 🔴 WHAT ACTUALLY FLATTENED THE POSITION
`trades()` + `orders()`:
| order | side | type | status | filled | price | fill ts | tag |
|---|---|---|---|---|---|---|---|
| 260903170309107 | BUY | LIMIT | COMPLETE | 1 | 629.50 | 10:01:32 | `trd_b7d0f9ce3b31` |
| 260903170310432 | SELL | SL | CANCELLED | 0 | — | — | `trd_b7d0f9ce3b31` |
| 260903170310434 | SELL | LIMIT | CANCELLED | 0 | — | — | `trd_b7d0f9ce3b31` |
| 260903171147099 | SELL | SL | CANCELLED | 0 | — | — | `rc_recovery_sl` |
| **260903171167107** | **SELL** | **MARKET** | **COMPLETE** | **1** | **628.05** | **15:12:11** | **`None`** |

⇒ ⭐ **Every system order carries a tag; this one has none** ⇒ 👤 **Rama's manual
flatten, confirmed by the broker book.**
⇒ 🔴 ⭐ **AND IT WAS A `MARKET` ORDER THAT FILLED.** ⭐ The broker accepts MARKET from
the UI (protection applied for you) and **refuses it from the API** without an
explicit `market_protection`. ⇒ ⭐ The exit path is the only actor that could not act.

### 6.3 · CORRECTED EXPOSURE
⭐ The naked state ends at the **fill (15:12:11)**, ⛔ not at the system's observation
(15:12:25).
- window 1: 15:07:04.6 → 15:07:16.06 = **11.5 s**
- window 2: 15:10:03 → 15:12:11 = **2 m 08 s**
- ⇒ **total ≈ 2 m 20 s.** ⛔ Supersedes the "2 m 22 s" figure in §2.

### 6.4 · 🔴 §3 — MY FIRST QUERY WAS VACUOUS. ⭐ THE CORRECTED ANSWER IS THE OPPOSITE.
🔬 The obvious query (`OWN_SL` trades with no SL `qty_filled > 0`) returned **74 of
74** — which looked like a systemic contamination of every exit-attribution analysis.
🔴 **It could not have returned anything else.** 🔬 Across **all 1315 orders**:
`qty_filled > 0` count = **0**; `avg_fill_price > 0` count = **0**.
⇒ ⛔ **Both columns are never populated.** ⭐ The query was incapable of a negative.
🔬 **Re-run against `status='COMPLETE'`** (which IS populated — 623 orders, 159 SL
legs): ⇒ **exactly 1 trade** — **ANANTRAJ**.
⇒ ⭐ **D4 IS NARROW, ⛔ NOT SYSTEMIC.** 73 of 74 `OWN_SL` trades have a COMPLETE SL
order. ⛔ The exit-attribution corpus is **not** contaminated.
⚠️ **Separate finding:** `orders.qty_filled` and `orders.avg_fill_price` are dead
columns. ⛔ Any analysis reading them is reading zeros.

### 6.5 · 🔴 §6 — THE MIS AUTO-SQUAREOFF EXIT HAS **NEVER** EXECUTED
🔬 All 27 `leg='EOD'` orders ever placed: **26 at 15:17**, **1 at 10:00**, **all
`LIMIT`**. 🔬 In the MIS squareoff window 15:07–15:12: **ZERO**.
⇒ ⭐ Every EOD exit the system has ever completed came from the **15:17
`eod_squareoff`** path, using **LIMIT** — ⭐ which is precisely why it works.
⇒ 🔴 ⭐ The MIS squareoff belongs to the *configured, built, started yet inert*
family. ⭐ Its MARKET defect never surfaced because **its exit has never run.**

### 6.6 · 🔴 §4 — WHY G5b NEVER RE-FIRED: A SOURCE-OF-TRUTH SPLIT
🔬 G5b fired **exactly once** today (15:07:16). ⛔ Never again.
⭐ Neither (a) SOFT_KILL nor (b) CHECK9 precedence explains it — ⭐ **(d):**
- 🔬 G5b's loop is gated by `if sl_row is None`, where `sl_row =
  get_sl_order_for_trade()` — 🔬 which reads the **LOCAL orders table**.
- 🔬 The local row for `…7099` was **not marked CANCELLED until 15:17:04** — ⭐ seven
  minutes after the exchange cancelled it. 🔬 The reconciler said so explicitly at
  15:12:25: *"leaving local status for order_monitor"*.
- ⇒ ⭐ `sl_row` was **not None** ⇒ 🔴 **`_g5b_crash_recovery_sl` was never even called.**

⇒ 🔴 ⭐ **The system simultaneously believed the position was NAKED (CHECK9, reading
the broker) and PROTECTED (G5b, reading the local DB).** ⭐ The remedy that had
actually worked 3 minutes earlier was suppressed by stale local state; ⭐ the remedy
that has never worked is the one that ran, 8 times, for 111 seconds.
⚠️ ⭐ Note the design intent in the code comment: *"Runs before G5b so naked positions
are caught before recovery attempts."* ⇒ ⭐ CHECK9 (force-exit) is **deliberately
preferred** over G5b (re-protect). ⭐ Here it did not even matter — ⛔ G5b was
unreachable — but the ordering is a design question in its own right.

### 6.7 · D5 — eight retries of a permanently invalid order
🔬 8 identical rejections over **111.4 s** (15:10:17.585 → 15:12:09.406).
⭐ *"Market orders without market protection"* is a **validation** error — ⭐ it is
deterministic and will fail identically forever. ⇒ ⭐ The retry loop does not
classify broker errors as retryable vs terminal. ⛔ Not fixed.

### 6.8 · §7 — the two SHAs
🔬 bare `refs/heads/main` = **`2d084364b830aca83b6d5cd3f2db21100372f446`**;
🔬 deployed work-tree vs that tree = **0 differing tracked files** ⇒ ⭐ the running
code **is** `2d08436`. ⭐ `39292d3` is the recorded TREE/rollback point.
⇒ ⚠️ 🔴 **The rollback point is not the running code.** ⭐ Reverting to `39292d3`
would unship 29 backend modules, and ⛔ nobody has established what that does to the
exit path. ⇒ ⭐ The TREE advance is now a **prerequisite of the exit-path fix**,
⛔ not a GUI leftover. 👤 Rama's call; ⛔ not performed.

---

## 7 · ROUND 3 — THE CLOSING LIST (FILE 127 §4). ⭐ MEASUREMENT IS CLOSED.

### 7.1 · §4.1 — 🔴 BOTH OF FILE 127's HYPOTHESES ARE KILLED
🔬 At **15:17:04.310**: `order_reconciler` — *"Sweep: marked **1 stale orders** as
CANCELLED (parent trade terminal)"*.
⇒ ⭐ The local row was made terminal by a **stale-order sweep keyed on the parent
trade being terminal** — ⛔ not by per-order polling.
- ⛔ **1.1 (the `rc_recovery_sl` tag) is NOT the mechanism.** 🔬 The local row
  carries the **correct `trade_id`** (it is returned by `WHERE trade_id='trd_…'`).
  ⭐ Association was never broken. ⚠️ The static tag remains a **broker-side**
  traceability gap, ⛔ but it did not cause this.
- ⛔ **1.2 (`pending_quantity=1`) is NOT the mechanism.** 🔬 The string
  `pending_quantity` appears in **0 files** in the codebase. ⭐ Nothing reads it.
⇒ 🔴 ⭐ **The real mechanism: nothing polls a non-entry order's status between
placement and a terminal-parent sweep.** 🔬 The reconciler said so itself at
15:12:25 — *"leaving local status for order_monitor"* — and 🔬 `order_monitor`
produced exactly **1** log line for that order all day.

### 7.2 · §4.2 — 🔴 THE "FOUR MANUAL INTERVENTIONS" READING IS WRONG
🔬 What `trades` says closed each untagged fill:
| symbol | product | exit_reason | closure_source |
|---|---|---|---|
| KIRIINDUS | CNC | **GTT_EXIT** | (empty) |
| HIKAL | CNC | **GTT_EXIT** | (empty) |
| RBLBANK | CNC | **GTT_EXIT** | (empty) |
| **ANANTRAJ** | **MIS** | **MANUAL** | 🔴 **OWN_SL** |
⇒ ⭐ **Three of the four are GTT exits — a normal system mechanism.** ⭐ A broker GTT
is placed by Zerodha's own engine and carries **no tag**, so *"untagged ⇒ manual"*
is **false**. ⛔ 👤 Rama did not intervene four times.
⇒ ⭐ **D4 remains NARROW: ANANTRAJ is the only mis-attributed trade**, and the only
untagged fill that is `MANUAL`.
⇒ ⭐ It also explains RBLBANK's successful **MARKET** fill: 🔬 placed by the broker's
**GTT engine**, ⛔ not through the API's `place_order`.

### 7.3 · §4.3 — 🔴 THE SYSTEM HAS NEVER PLACED A MARKET ORDER AT ALL
🔬 `SELECT … FROM orders WHERE order_type='MARKET'` ⇒ **0 rows**, out of **1315**.
⇒ ⭐ Not "never filled" — ⭐ **never even successfully recorded as placed.** ⭐ Every
order in the system's entire history is LIMIT / SL / SL-M.
⇒ ⭐ Final wording, confirmed: *"no API-placed MARKET order has ever been recorded;
the two MARKET fills in today's book came from the broker's GTT engine and from
👤 Rama's UI, ⛔ neither through `place_order`."*

### 7.4 · §4.4 — 🔴 D6 IS A DATA-HYGIENE GAP, ⛔ NOT A LIVE DEFECT
🔬 Populated vs dead:
| column | rows | `>0` |
|---|---|---|
| `trades.qty_filled` | 844 | **335** ✅ |
| `trades.entry_actual_price` | 844 | **335** ✅ |
| `orders.qty_filled` | 1315 | **0** 💀 |
| `orders.avg_fill_price` | 1315 | **0** 💀 |
🔬 Of 286 references, **0** read `orders.qty_filled`/`avg_fill_price` (no
`FROM orders`, no `o.qty_filled`); 🔬 **36** read the **trades** row, which **is**
populated. 🔬 The single candidate (`structure_exit_manager.py:490`) is an
**INSERT** hardcoding `qty_filled=0` — ⭐ a writer.
⇒ ⛔ **FILE 127 §3's claim that quantity/partial-fill/exit-sizing logic "is
computing from zero" is NOT supported.** ⭐ The columns are written-zero and
never-read. ⭐ 335 matches the 335 COMPLETE ENTRY orders exactly.
⇒ ⭐ D6 = **two dead columns**, ⛔ no live consumer. ⭐ Fix by populating or dropping;
⛔ it is not on the critical path.

### 7.5 · §4.5 — THE MARKET CONSTRUCTION SITES, CLASSIFIED
| module | imports `marketable_limit_price` | verdict |
|---|---|---|
| `orders/mis_autosquareoff.py` | **0** | 🔴 **DEFECT — cannot convert. Raw MARKET on BOTH passes** (`:71`, `:546`) |
| `orders/order_reconciler.py` (**check9 ~3000**) | (file imports, ⛔ this site does not use it) | 🔴 **DEFECT — no LTP fetch, no conversion.** Fired 8× today |
| `orders/order_reconciler.py` (~2317) | ✅ | ⚠️ converts; MARKET only as a no-LTP fallback |
| `capital/kill_switch.py` (~1325) | 0 — ⭐ has its **own inline** conversion | ⚠️ returns `LIMIT` at LTP±buffer; MARKET only when LTP missing |
| `orders/eod_squareoff.py` | ✅ | ⭐ converts — 🔬 the 26 successful 15:17 exits are **LIMIT** |
| `orders/sl_breach_monitor.py` · `structure_exit_manager.py` · `order_placer.py` | ✅ | ⭐ convert |
⇒ 🔴 ⭐ **Exactly TWO genuine defect sites**: check9's emergency exit and the MIS
auto-squareoff exit.
⇒ ⚠️ ⭐ **And one residual class:** every converting site falls back to raw MARKET
when LTP is unavailable — ⭐ a fallback that is **guaranteed to be rejected**.
⭐ "No quote" therefore means "no exit", silently.

---

## 8 · WHAT IS NOT ESTABLISHED

- **HYPOTHESIS, unproven:** why `get_order_history` for `…434` still showed 4
  entries 39 ms after a successful cancel while `…432` already showed 7. Most
  likely broker-side propagation delay; ⛔ not measured.
- ~~whether `…7099` was genuinely cancelled at 15:10:03~~ — ✅ **RESOLVED §6.1:
  cancelled at the exchange at 15:10:03. Reading A.**
- **⛔ Not established:** whether any other MARKET call site has ever executed
  successfully in live. ⭐ On this evidence, treat every one as **UNPROVEN**.
- **HYPOTHESIS, unproven:** why `order_monitor` took until **15:17:04** to mark
  `…7099` terminal when the broker cancelled it at 15:10:03. ⭐ A 7-minute local
  lag is the mechanism that disabled G5b (§6.6) — ⛔ its cause is not measured.
- **⛔ Not established:** whether the CHECK9-before-G5b ordering is right. ⭐ FILE 126
  §4's design note stands unexamined: **re-protecting a naked position cannot
  double-sell and cannot be rejected for market protection, whereas force-exiting
  can do both.** ⛔ Design, ⛔ not decided here.
- **⛔ Not established:** whether `t2_cnc_gtt_realtest.py`'s parity claim is tested
  anywhere. 🔬 It is prose. ⇒ ⭐ RULE: a docstring asserting parity between two code
  paths is a claim requiring a **test**.
