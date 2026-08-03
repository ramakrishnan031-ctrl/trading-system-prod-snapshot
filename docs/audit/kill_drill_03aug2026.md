# MONDAY PC GATES (03-Aug-2026) — (b) KILL DRILL **9/10, ONE RED** · (c) REGRESSION **PASS** · (a) **GREEN** (shared composition; one live-only ctor unproven)

**⛔ FINDINGS-ONLY. NOTHING WAS FIXED, and nothing here authorises a fix.**
The RED cell needs its own card (G3: disclose, don't expand).

**Gate status (03-Aug): (b) RED · (c) PASS · (a) BLOCKED.**
**Rama's ruling on the RED (10:30):** it does **NOT** block tonight's push — the defective
helper is already live and the push is a strict improvement — but it **IS a hard gate on
the CARRY PILOT**, carded as **#2e**. It does **not** gate Wednesday's flip: the flip alone
does not create the two-row book. Gate (a) is **BLOCKED, not RED** (§6) and must run in the
15:30 → 18:15 window, before the push decision.

---

## 1. SAFETY — what the drill could and could not touch

Harness: `scratchpad/kill_drill_03aug.py` — **validation tooling, deliberately NOT
committed** (same posture as the 16-Jul `mc_cluster_drill.py`).

- **No network, no Zerodha, no real orders** — the adapter is a `MagicMock`.
- **The live DB was never opened.** The store is a `MagicMock`, so no sqlite path
  exists in the run at all. Proven externally as well: `data_store/trading_system.db`
  mtime **`2026-07-27 15:37:27.138808200` before AND after** — byte-for-byte the same
  timestamp.
- PC only. **The VM stays on `297b587`;** nothing was deployed.

## 2. WHAT THIS DRILL ADDS OVER THE UNIT SUITE — and why it found something

`tests/unit/test_kill_switch_product_filter.py:40-41` monkeypatches the broker seam:

```python
monkeypatch.setattr(ks_mod, "determine_close_direction",
                    lambda _a, _s, side, qty: (side, qty))
```

⇒ the stub **returns the local qty by construction**, so **no test in that file can
observe what the real helper computes from broker truth.** The eight tests there are
sound about *which* positions are flattened; they are structurally blind to *how many
shares* site 1 sells.

⭐ This is the campaign's own named class — *a mock whose return value you order cannot
test the thing you ordered* — and it is why the drill uses the **real**
`determine_close_direction` / `broker_net_qty`.

**Scope honesty (V1):** this drill proves the **system-side decision path**. It does
**not** prove broker-side netting semantics, and `<VERIFIED LIVE>` remains unreachable
(HARD_KILL has never fired).

## 3. CASE A — the card's mixed book (distinct symbols): **9/9 PASS**

Local trades: `AAA` MIS 10 · `BBB` CNC 5 · `CCC` NULL 7.
Broker rows: those three plus sweep-only `DDD` MIS 3 · `EEE` CNC 4 · `FFF` "" 6.

Orders actually placed: `AAA SELL 10`, `CCC SELL 7` (local pass) · `DDD SELL 3`,
`FFF SELL 6` (sweep).

| # | check | result |
|---|---|---|
| A1 | local MIS flattened | ✅ |
| A2 | **local CNC SPARED — no order at all** | ✅ |
| A3 | local NULL flattened | ✅ |
| A4 | sweep MIS flattened | ✅ |
| A5 | **sweep CNC SPARED — no order at all** | ✅ |
| A6 | sweep NULL flattened | ✅ |
| A7 | spared positions LOGGED at both sites | ✅ 3 `SPARED delivery position` CRITICALs |
| A8 | NULL product raised CRITICAL at both sites | ✅ 2 `UNKNOWN PRODUCT` CRITICALs |
| A9 | the CRITICAL actually escalated to the notifier | ✅ `notifier.send` = 2 |

⇒ **The buy-day product filter does exactly what #2/#2b claim, on the book the card
specified.**

## 4. CASE B — SAME-SYMBOL MIXED BOOK: 🔴 **RED**

**Setup:** one local MIS trade on `SYM` qty **10**; the broker holds **`SYM` MIS 10
AND `SYM` CNC 5** (Kite `positions()` is per `(symbol, product)`, so this is a normal
two-row book, not a contrived one).

**Q4 says the CNC 5 must survive. Measured:**

```
place_order: ('SYM', 'SELL', 15, 'INTRADAY', 'ks_hard_kill_exit')
broker_net_qty('SYM') = 15      # sums ALL products for the symbol
```

⛔ **Site 1 sells 15 against a 10-share intraday position — the extra 5 come out of the
SPARED delivery holding.** The position is spared by the filter and then partially sold
by the exit that follows it.

### 4a. Root cause — measured, one function

`broker/position_helpers.py:34-39` — `broker_net_qty` sums every position row whose
**symbol** matches, with **no product filter**:

```python
for p in positions or []:
    if getattr(p, "symbol", None) == symbol:
        net += int(getattr(p, "qty", 0) or 0)
```

`kill_switch.py:1613` (site 1, the local pass) consumes it via
`determine_close_direction` to choose `(side, qty)`.

⭐ **Site 2 (the broker sweep) is CORRECT and shows the contrast:** it uses the
**per-row** `pqty` (`:1665`) and never calls the helper — which is exactly why
`test_site2_spared_cnc_does_not_suppress_same_symbol_mis_row` can assert `qty == 4` and
pass. **The two sites disagree, and only one of them was measured.**

### 4b. This is PRE-EXISTING and ALREADY LIVE — it is not introduced by tonight's push

- `git diff 297b587..HEAD -- broker/position_helpers.py` = **EMPTY** ⇒ the helper is
  **byte-identical to what is running on the VM right now**. Its last edits were
  `b49764f` / `870bbc9` (FIX-190), long before this campaign.
- `determine_close_direction` has been called at site 1 since FIX-190; the #2 work did
  not add it.

⭐⭐ **AND THE PUSH IS A STRICT IMPROVEMENT, MEASURED:** `297b587` (deployed) contains
**0** occurrences of `SPARED delivery position`; HEAD contains **2**. So **production
today flattens the ENTIRE CNC holding under a HARD_KILL**. After tonight's push it
spares all of it *except* the same-symbol overlap quantity.

⇒ **Not a reason to hold the push.** It is a reason to correct the *claim* the push
makes, and to card the residual gap.

### 4c. Reachability — LATENT, but the conditions are half-present already

Needs **all** of: a HARD_KILL fires · the same symbol holds a live intraday position
· that symbol also holds CNC.

- **HARD_KILL has never fired** (measured, standing fact) ⇒ latent today.
- ⚠️ **The CNC half already exists:** the T2 basket has held **5 CNC positions
  overnight since 29-Jul**. The collision needs only an intraday signal on one of those
  same symbols.
- 🔴 **The carry pilot raises this materially** — it exists to hold delivery while the
  system trades intraday, which is precisely the two-row book above.

Per the live-vs-latent rule: **LATENT ⇒ document + pin with a test that fails when it
becomes reachable.** ⛔ The pin is **NOT written here** — it belongs to the card that
owns the fix.

### 4d. What is owed (⛔ recorded, NOT done)

1. **A card for the fix.** The obvious shape — make the flatten qty product-aware
   rather than symbol-net — touches a **shared** helper with **two** other callers
   (`order_placer`'s emergency exit is the other; FIX-190's whole purpose was
   anti-oversell), so it is careful-loop work, not a one-liner. ⛔ Do not narrow
   `broker_net_qty` in passing: FIX-190 exists to *prevent* an oversell, and a careless
   product filter there could re-open the THELEELA class.
2. **A label correction.** #2/#2b's "delivery survives a HARD_KILL" is true **except on
   a same-symbol mixed book**. The claim should carry that qualifier.
3. **A test that is not blind.** Any pin must exercise the REAL
   `determine_close_direction`; the current file's stub would make it vacuously green.

---

## 5. GATE (c) — CALM REGRESSION CONFIRM: **PASS**, with two PROVEN artifacts

**Run:** `pytest tests/unit tests/integration -q` (the campaign invocation — ⛔ not
`run_tests.py`), main tree, 09:54:03 → 10:08:54 IST, **879.33s**. No midnight crossing.

**Result: 9 failed · 5,504 passed · 4 skipped.**

### 5a. This is a true CONFIRM, not a re-validation — the tree is code-identical

Both `.py` edits since the last code commit (`42db913`) are **comment-only, proven not
asserted**: `core/constants.py` (@ `07c7fc0`) and `orders/order_reconciler.py`
(@ `bb25fa6`) are both **AST-IDENTICAL with docstrings stripped** (M2). The constant's
value was asserted, not eyeballed: `EMERGENCY_FLATTEN_PRODUCTS == frozenset({'CO','MIS'})`
— CO present (the #2c-R refusal is site-local; CO was never removed), CNC absent (Q4).

### 5b. The 7 standing failures — ALL present, by name

`test_fix181` inflight-orphan LIMIT-vs-MARKET (the T3/AR3 item) · `test_closure_source_contract`
vocabulary scanner · `test_main` ×4 (3 × `TestContinueFromGate` + 1 × BL15) ·
`test_phase17_batch2` flask max-content-length. **Identical to the set named in the #2b
and #2c-R stamps.** The q9 consecutive-losses oscillator did **not** fire.

### 5c. The 2 extra failures — PROVEN artifacts of the TEST INTERPRETER, not of code

`tests/unit/test_instance_lock.py::TestSingleInstanceAcrossProcesses::test_p1_…` and
`::test_p2_…`. ⛔ Not labelled "env" — **diagnosed**:

| step | measurement |
|---|---|
| deterministic, not flake | 2F/4P on **4 consecutive** isolated runs, 0.23-0.25s each |
| not concurrency with the drill | fails identically with nothing else running |
| not a stale port | port 59321 free; `Get-NetTCPConnection` empty |
| not a stale PID | the PID named in the refusal did **not exist** when checked |
| **root cause** | **`venv/Scripts/python.exe` is a LAUNCHER STUB that re-spawns the real interpreter.** Measured: `Popen.pid = 4060` vs the child's own `os.getpid() = 30908`. Under `C:\Python314\python.exe` they are **the same number**. |
| the two assertions that fail | `assert str(holder.pid) in reason` (the lock correctly names the *grandchild*), and p2's "crash" kills only the stub, so the grandchild keeps the OS lock |
| **the guard itself WORKS** | p1's first two assertions PASSED — the second instance **was** refused with `"Another instance is running"`. Only the PID-*naming* assertion failed. |
| **direct confirmation** | the same class under `C:\Python314\python.exe`: **6 passed in 0.18s** |

⛔ **The full suite CANNOT be run under the non-stub interpreter** — `C:\Python314` lacks
the project deps (`ModuleNotFoundError: cachetools` at collection). So the venv is the
only interpreter that can run the gate, and V3's *prove-and-subtract* is the correct
standard here rather than artifact-free running.

### 5d. The arithmetic closes on BOTH axes (V3)

- failures: **9 − 2 artifacts = 7** = the named standing set, exactly;
- passes: **5,504 + 2 = 5,506** = the #2c-R stamp;
- collection: **9 + 5,504 + 4 = 5,517**, and the 02-Aug pair was **7 + 5,506 + 4 = 5,517**
  — *identical collection*, so nothing appeared or vanished.

⇒ **NEW-FAILURE SET ATTRIBUTABLE TO CODE = EMPTY. Gate (c) PASSES.**

### 5e. ⚠️ A GATE-INTEGRITY FINDING, recorded not fixed

**V2 pins the pytest ARGUMENTS but not the INTERPRETER**, and the build-record stamps do
not name which interpreter produced them. Two runs that both honestly claim "the campaign
invocation" **differ by two failures** depending on whether pytest is launched by the venv
stub or a real interpreter. ⛔ Left for Rama: V2 should name the interpreter as part of the
baseline. ⚠️ Secondary consequence: under the venv these two tests fail for a fixed reason,
so **the single-instance guard is effectively unexercised by the gate** — they cannot mask
a delta (they fail on both sides) but they can no longer detect a real regression.

## 6-RESULT. GATE (a) RE-RUN 15:33 → **GREEN**, with the honest label

**Run:** PC paper composition boot, 15:33:31 → force-stopped 16:08:22. The
`market_open` guard was clear after 15:30, so the **v44 → v45 migration ran on the
sanctioned path** and the boot proceeded. **VM untouched, still `297b587`.**

### 6-R(a). B2 PASSED — and it logged its own verdict

```
2026-08-03T15:34:40.334 INFO main — effect_telemetry: composition OK (61 registered, 62 expected)
```

⛔ **Not inferred from "the process was alive".** Three independent confirmations:

1. **the line above** — `composition OK`, emitted by `assert_composition` itself;
2. **zero** occurrences of `EffectCompositionError` / ghost / missing / unknown in
   `logs/system_2026-08-03.log`;
3. ⭐ **it reached the runtime loop, which is only reachable PAST the assertion:**
   `assert_composition` is `main.py:3744`, `_shutdown_event.wait()` is `:3749`, and the
   log shows **134 monitoring cycles** (`get_positions`/`get_margins` every 15 s) running
   steadily to **16:06:07**. A failed assertion raises and the boot dies; this one idled
   for 35 minutes.

⚠️ **`EXIT=127` in the wrapper log is MY force-kill at 16:08, not the boot's verdict.**
Recorded so nobody later reads it as a failure.

### 6-R(b). THE 61 vs 62 DELTA IS NAMED — it is not a defect

`config/expected_managers.yaml:385`:

> `clock_skew_probe … ctor: "main.py:3384-3386", state: infra,`
> `modes: "live-only ctor BY DESIGN (paper skew always ~0) — the ONE mode-conditional entry"`

⇒ 62 expected − **1 declared mode-exemption** = 61 registered, which is why `_mode_exempt`
returns `composition OK` rather than a mismatch. **The registry itself asserts this is the
ONLY mode-conditional entry**, so the unproven surface is not "some live-only ctors" — it
is **exactly one, by name.**

⛔ **The card's RED discriminator ("is the offending manager paper-only?") does not
arise** — there is no offending manager. Composition is clean.

### 6-R(c). THE HONEST LABEL — ⛔ not a bare "PASS"

> **SHARED COMPOSITION PROVEN** (61/61 non-exempt managers registered; no ghosts, no
> unknown, no missing). **THE ONE LIVE-ONLY CTOR — `clock_skew_probe` — REMAINS UNPROVEN
> UNTIL THE TUESDAY 08:15 VM BOOT.**

✅ **And the residual is bounded:** on the VM, B2 is **CRITICAL-and-continue, never
fail-fast**, so if `clock_skew_probe` failed to register it surfaces Tuesday morning as an
**alert**, not as a system that will not start.

### 6-R(d). SIDE-EFFECTS, all PC-local and all expected

| effect | status |
|---|---|
| `data_store/trading_system.db` migrated **v44 → v45** (the `trades` REBUILD) | ✅ completed; `schema_version = 45`. Backup `trading_system.db.pre_gate_a_v44.bak` retained but **not needed** |
| `kill_switch_state` = **SOFT_KILL**, `circuit_breaker_force_close_15:15`, 15:34:38 | ⚠️ PC-local. It is a **prior-day** kill by tomorrow ⇒ **auto-clears at the next 08:15 boot** (`clear_stale_state`). ⛔ No `resume.sh` needed |
| `CRITICAL eod_squareoff — EOD squareoff was NOT fired today … Manual intervention required` | ⚠️ artifact of booting **after** the close on a PC instance that never ran the 15:17 job. **Not a finding**, and **not** true of the VM |

### 6-R(e). ⭐ `expected_alarms.md` VALIDATED ITSELF ON THE DAY IT WAS WRITTEN

The boot emitted **exactly** the four-line chorus documented this morning in
`expected_alarms.md` §1 — `order_monitor.force_close_triggered` (WARNING) →
`circuit_breaker.force_close_triggered` (CRITICAL) → `KillSwitchActivated` (CRITICAL) →
`SOFT_KILL ACTIVATED` (CRITICAL) — one designed event in three severity vocabularies,
precisely as described. **The doc predicted its own first real sighting.**

---

## 6. GATE (a) — FIRST ATTEMPT 10:30: ⛔ **BLOCKED — NOT RED, NOT A PASS**

Authorised and attempted 03-Aug 10:30:03 IST. **The boot refused to start**, and the
thing that refused it was **working correctly**.

```
CRITICAL state_store.migrations — MIGRATION_REFUSED schema v44 -> v45 pending;
  this process may not migrate the live DB (allow_migrate=True, market_open=True).
  It applies at the next OFF-MARKET boot of the trading app.
core.state_store.MigrationNotPermitted   (main.py:1875 -> state_store.py:427 -> :355)
EXIT=1
```

### 6a. What happened, and why it is the guard doing its job

The PC's `data_store/trading_system.db` is at **schema_version 44**; the tree expects
**45**. **AC2 of the migration-on-open guard** (`ed1c4b9`) refuses a migration **even on
the sanctioned `main.py` boot path** while the market is open — measured here at 10:30
IST on a trading Monday. The boot exits **before** composition, so `assert_composition`
(`main.py:3744`, the last step before the runtime wait) is **never reached**.

⇒ **The B2 assertion was not exercised.** ⛔ **Per this campaign's own rule — an
unexercised check is NO EVIDENCE, never a pass** (the same logic the observation card
applies to zero rows). Gate (a) is therefore **BLOCKED**, which is **distinct from RED**:
RED would mean the composition claim is false; BLOCKED means it was never put to the
question.

### 6b. No artifact was touched — proven, not asserted

`data_store/trading_system.db` **byte-identical before and after**: md5
`27df20d51e966f88fa1f04952a3887bc`, mtime `2026-07-27 15:37:27.138808200`, 995,328
bytes, `schema_version` still **44**. The refusal happens *before* any write. A
pre-boot backup was taken anyway (`scratchpad/trading_system.db.pre_gate_a_v44.bak`,
same md5) and was not needed.

⛔ **The PC DB was NOT hand-migrated to unblock the gate.** The error says *"No manual
DB surgery"* and the standing rule forbids side-door DB writes — the guard is not an
obstacle to route around.

### 6c. ⭐ The real window for gate (a), and why this reshapes tonight

A PC composition boot needs a **trading day** (a weekend trips the holiday/weekend gate
— that is what #1 measured) **and off-market hours** (during the session, AC2 refuses
the pending migration). Those two constraints leave exactly one slot today:

> **after 15:30 and before the 18:15 push** — `market_open` is then False, the boot
> migrates v44 → v45 on the sanctioned path, and composition proceeds to B2.

⇒ **Gate (a) should run in that window, and its result is known before the push
decision** — which preserves the card's rule that a RED (a) holds the push, without
requiring the push to wait on anything else.

⚠️ Note for that run: it will migrate the PC DB **v44 → v45**, and v45 is a **REBUILD of
`trades`** (`MIGRATION_TABLES[45]`), not a pure addition — atomic on failure, 134 ms on
a production-copy dry-run. The backup above should be kept until it completes.

### 6d. ⭐⭐ HOW TO READ THE RE-RUN — the verdict is ASYMMETRIC (Rama, 03-Aug ~11:30)

⛔ **RED and GREEN do not carry the same weight, and conflating them is the trap.**

**RED → HOLDS THE PUSH, subject to ONE discriminator.** B2 asserts *constructed-set ==
registered-set*, which is a property of the **WIRING, not the database** — the DB decides
schema and state, never which managers `main.py` constructs. So an unregistered
constructed manager is a **code-level** defect that is equally true on the VM.
⛔ **The discriminator, to be MEASURED before concluding: is the offending manager
paper-only?** A gap in a ctor that fires only in paper is a real finding but **not** a
push-blocker. A gap on a **shared** path blocks — and shared is the likely case, since the
composition root is shared. **Report which; do not assume.**

**GREEN → does NOT fully clear the live path, and must not be recorded as if it did.**
A **PC PAPER** boot cannot exercise **live-only ctors** — `clock_skew_probe` is the known
one (registered inside its existing `if not is_paper`), and any other live-conditional
construction is equally unproven.
⇒ the honest label for a green result is: **"SHARED composition proven; live-only ctors
UNPROVEN until the Tuesday 08:15 VM boot."**
⚠️ **Recording it as a bare "gate (a) PASS" would commit, inside our own evidence, exactly
the declared-vs-effective gap this campaign exists to close.**

✅ **The residual is BOUNDED, and saying so stops it being over-read:** on the VM, B2 is
**CRITICAL-and-continue by design, never fail-fast**. An unproven live-only registration
therefore surfaces Tuesday morning as an **alert**, not as a system that will not start —
the deliberate mode split from ledger #1, working as intended.

## 6. LABEL

**`<MEASURED — ONE RED, NOT FIXED>`.** No code changed. No register row (231 stands).
The RED cell is a disclosure awaiting its own card and authorisation.
