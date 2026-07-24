# Make the service shutdown time configurable (17:35)

**BUILD report.** 2026-07-25 (IST, Saturday — market closed, book flat, service down).
Base HEAD: `bc75406`. Instruction: "BUILD — MAKE THE SERVICE SHUTDOWN TIME CONFIGURABLE,
SET IT TO 17:35" (25-Jul 00:31 IST).

Sections written **before** any edit: §1 (map), §2 (expected reds), §3 (design deviation).
Results filled in as they land.

---

## §1 — THE REGRESSION MAP (complete, verified at `bc75406`)

Grep: `SERVICE_WINDOW_END|SERVICE_WINDOW_START|_within_service_window|_eod_self_exit_due|_start_eod_self_exit_thread`.

### Code — behaviour
| Site | Role | Action |
|---|---|---|
| `main.py:1593` `SERVICE_WINDOW_START` | earliest the service may START | unchanged |
| `main.py:1594` `SERVICE_WINDOW_END` | **two roles** (start-guard + self-exit) | SPLIT — see §3 |
| `main.py:1597-1602` `_within_service_window` | boot-refusal guard | reads the start cutoff |
| `main.py:1712` guard call site | boot refusal | unchanged call, new bound |
| `main.py:1715-1716` refusal message | prints the window | must not lie — see §3.3 |
| `main.py:1116-1117` `window_end=None` default | self-exit fallback | REMOVED — see §3.4 |
| `main.py:3450` `window_end=SERVICE_WINDOW_END` | self-exit | reads the configured value |
| `core/config_loader.py:62-101` `TradingHoursConfig` | schema + chained validator | new key + bounds |
| `config/system_config.yaml:27+` `trading_hours` | the value | `service_window_end: "17:35"` |

### Code — prose only (stale-doc injury)
`main.py:1027`, `:1583-1592`, `:1599`, `:3433` · `scripts/liveness_probe.py:28,84,96` ·
`tests/unit/test_main.py:1128,1131,1157`.

### Docs
`docs/SYSTEM_MAP.md:155` (liveness window upper-bound rationale — **semantic**, not cosmetic:
"exact" becomes "conservative"), `:1816-1817` (self-exit description).

### Verified NO change needed
- **`PATHS.md:60`** — mentions `_eod_self_exit_due` only re: the EXITING/flatten race (M-C8).
  Carries no time value. Untouched.
- **`tests/unit/test_main.py:1162-1218`** (`TestFix189EodSelfExit`) — every case passes
  `window_end` explicitly (`self._W`, `_time_cls(0,0)`). Independent of the constant.
- **`tests/unit/test_mc8_async_hardkill.py:526-598`** — same; explicit `window_end` throughout.
- **Historical audit reports** (`liveness_alarm_17jul2026.md`, `mc8_*`,
  `service_window_extension_2026-07-24.md`, `eod_capture_safety_2026-07-24.md`) — dated
  records of what was true when written. NOT rewritten, by convention.

---

## §2 — ⭐ THE EXPECTED REDS, RECORDED BEFORE THEY WERE MADE RED

Declared here at 2026-07-25 ~00:50 IST, **before** the first source edit, so the
base-vs-merge gate cannot absorb them silently (Standing Rule 3).

1. **`tests/unit/test_main.py:1150`** —
   `assert _within_service_window(16:00) is False  # end exclusive`
   Goes red because 16:00 is now inside the start window. **Updated** to assert the
   boundary at the new start cutoff, keeping the FIX-189 intent (23:22 / 04:24 still refused).

2. **`tests/unit/test_liveness_probe.py:236`** —
   `assert _LIVENESS_END == main.SERVICE_WINDOW_END`
   Goes red by design. `==` is the **wrong invariant** once the two values diverge.
   Changed to `<=` against the **configured self-exit time** — "never alarm during a period
   when a clean exit is already legitimate."
   ⛔ NOT restored by pushing `_LIVENESS_END` to 17:35: the probe's cron is `*/5 09-15`
   (last run 15:55) and structurally cannot reach it. Reason recorded in the test body.

No other test is expected to move.

---

## §3 — ⭐⭐ DESIGN DEVIATION FROM THE INSTRUCTION (Rule #8: the source wins)

**§3.4 of the instruction says all three `main.py` sites read the configured value.
Two of three can. The third structurally cannot, and the source proves it.**

### 3.1 What the source says
```
main.py:1712   boot-refusal guard   _within_service_window(_svc_now)  -> return 0
main.py:1723   setup_logging
main.py:1728   acquire_instance_lock
main.py:1743   app_config = load_all(config_dir)      <-- CONFIG LOADS HERE
main.py:1767   StateStore(...)                        <-- first DB work
```
**The boot guard runs ~31 lines and one instance-lock acquisition before config exists.**
Neither design round caught this.

### 3.2 Why config was not moved earlier (rejected option)
Moving `load_all` above the guard would let the guard read the configured value, but it
changes the failure semantics of a broken config on an off-hours start: today the guard
returns **exit 0** (systemd `Restart=on-failure` does not retry). After the move it would
return **exit 5** — a restart loop against a broken config, off-hours, unattended.
That is a NEW failure mode on the boot path, traded for 40 minutes of guard precision.
Rejected.

### 3.3 What was built instead — the trap is closed BY CONSTRUCTION, not by a value
The two roles are split and locked together through a single schema bound:

- `config_loader.SERVICE_WINDOW_END_MAX` — the exclusive upper bound for the configured
  stop time, derived from the 18:15 forward-shadow job (§4.2).
- The schema requires `market_close <= service_window_end < SERVICE_WINDOW_END_MAX`.
- `main.SERVICE_START_CUTOFF = SERVICE_WINDOW_END_MAX` — the latest the service may START.

⇒ **every legal configured stop time is strictly inside the start window**, so the
F1a crash-restart trap cannot reappear for *any* config value, including a revert to 16:00.
A test would only catch drift after the fact; this makes drift unrepresentable.

**Accepted cost, stated plainly:** FIX-189's anti-overnight start window widens from
16:00 to **18:15 — 135 minutes, not the 95 the instruction anticipated.** The extra 40
minutes are judged immaterial: a stray start inside it arms the self-exit thread, whose
`target_dt` is already in the past, so it goes flat-checks-then-exits within one poll
(<=60 s). The FIX-189 incidents (23:22 start, 04:24 alerts) are still refused.

**Revert remains config-only** for the thing that matters: setting
`service_window_end: "16:00"` restores the 16:00 stop. The start cutoff stays wide and
harmless (a stray start self-exits immediately, as above).

**The refusal message (`:1715-1716`) prints what the guard actually enforces** — the start
window — and names the configured stop separately. The instruction asked it to print the
configured value; it cannot see one. Printing the bound it enforces is the non-lying form.

### 3.4 `window_end=None` default (`main.py:1116-1117`) — decision + why
**REMOVED; the parameter is now required.** No caller relies on it: prod passes it
(`:3450`), and every test passes it explicitly (`test_main.py:1198,1212`;
`test_mc8_async_hardkill.py`). Keeping a default that silently resolves to a module
constant is exactly the class of defect §0.1 of the instruction describes — after the
split it would have resolved to the *start cutoff*, i.e. the wrong role. Fail loud instead.

---

## §4 — RESULTS

### 4.1 The configured value is read (proven with a distinct value, not by inspection)
`service_window_end: "17:35"` → the eod-self-exit thread. A distinct **17:05** is used in
`test_configured_value_is_read_not_the_old_constant` and
`test_self_exit_honours_the_configured_value` (at 16:30 — past the OLD constant — the
self-exit is NOT due and does not even query the store; at 17:06 it is due).
Live wiring, printed from the built tree:
```
configured stop     : 17:35
SERVICE_START_CUTOFF: 18:15:00
trap closed (stop < cutoff): True
start permitted at 15:59/16:30/17:34/18:14: True   18:15/23:22/04:24: False
```

### 4.2 The deliberate break — REJECTED AT CONFIG LOAD (shown RED first)
Run against a **copy** of `config/` so the live file was never at risk:

| value | result |
|---|---|
| `18:20` | REJECTED at `load_all` |
| `23:59` | REJECTED at `load_all` |
| `15:00` (below `market_close`) | REJECTED at `load_all` |
| `17:35` (control) | loads clean |

Raised by the pydantic model validator — before `StateStore` (`main.py:1767`) and before
any broker handle, as required.

⭐ **This is where measuring beat assuming.** The rejection was real but the REASON was
invisible: `main.py` logged only `str(exc)` = *"Schema validation failed for
system_config.yaml"*. No key, no value. A mistyped shutdown time would have killed the
08:15 boot saying nothing about why. The detail was being carried the whole time in
`.context["errors"]` — the channel `TradingSystemError` documents as "intended for
structured logging" — and discarded. Added `_config_error_detail()` (extracted as a
helper so it is testable, as `compute_live_seed` was). The boot log now reads:
```
Config load failed: Schema validation failed for system_config.yaml | trading_hours:
Value error, trading_hours.service_window_end out of range; require market_close <=
service_window_end < 18:15 (...), got market_close=15:30, service_window_end=18:20
```
⚠️ **This is beyond the letter of the instruction and is Rama's to reject.** It is
additive logging at one boot-path site; no exception type or control flow changed.

### 4.3 A value inside the range boots cleanly
`15:30 / 16:00 / 17:35 / 18:14` all accepted (parametrised).

### 4.4 The boot log states the configured window
```
service window: may START in [08:00, 18:15) IST; EOD self-exit at 17:35 IST
(configured: trading_hours.service_window_end)
```

### 4.5 Parity — re-confirmed by test, not re-investigated
`test_shutdown_decision_is_mode_agnostic` parametrised PAPER/LIVE: identical decisions.
No mode branch exists in the shutdown path; `count_active_positions()` is mode-agnostic
(`main.py:1032`).

### 4.6 The two expected reds — went red exactly as declared, then fixed
- `test_main.py:1150` → `assert True is False`. Rewritten to assert the boundary
  **against `SERVICE_START_CUTOFF`** rather than a fresh frozen number, so it keeps
  testing the boundary and not a literal.
- `test_liveness_probe.py:236` → `AttributeError: module 'main' has no attribute
  'SERVICE_WINDOW_END'`. Now `_LIVENESS_END <= <configured stop>`, with the reason for the
  `==`→`<=` change recorded in the test body and an explicit ⛔ against restoring `==`.

### 4.8 Upper-bound drift guard — shown RED first
Bound temporarily forced to 18:30; the test failed **reading 18:15 out of
`cron_registry.yaml`**, not out of a literal:
> `service_window_end's upper bound (18:30:00) is at/after the forward-shadow recorder
> (18:15:00)`

Reverted; `git diff` confirms only `18:15` present.

### 4.7 ⭐ THE GATE — and it caught a real breakage

**First MERGE run FAILED: 44 failures vs BASE's 12 — 32 merge-only**, all in `test_main.py`
(boot-sequencing, shutdown, start-sequence, subsystem-wiring, status-mode).

Cause: `_make_mock_app_config()` (`test_main.py:86`) builds `trading_hours` as a
`MagicMock` with only the six fields main() used to read. The new boot-time
`_parse_hhmm(...service_window_end)` received a bare mock attribute → `.split(":")`
returned `[]` → `ValueError` at `main.py:1829`.

⛔ **Fixed the FIXTURE, not the production path.** The tempting fix — a
`getattr(..., "16:00")` fallback in `main.py` — would have been a silent fallback on the
shutdown path, i.e. a missing/misconfigured key would quietly revert the service to 16:00
and lose the 17:00 alert again with nothing to show for it. That is the exact defect class
this change exists to remove.

Two lessons worth keeping:
- **§2.4's declare-in-advance mechanism paid off immediately.** Because both expected reds
  were named beforehand, these 32 were unambiguously *not* them — no judgement call and no
  temptation to wave them through as "probably the known ones".
- **Targeted runs cannot substitute for the full gate.** While those 32 were broken, the
  new file, both expected reds and their neighbours were all green (76 passed).

**Final gate (same window, same shell, same session):**

| | BASE | MERGE |
|---|---:|---:|
| failed | 12 | 12 |
| passed | 5028 | **5049** |
| skipped | 4 | 4 |

`comm -23` (merge-only) **EMPTY** · `comm -13` (base-only) **EMPTY** · failure sets
**byte-identical**. `+21 passed` = the 21 tests in `test_service_window_config.py`, exactly.
The 12 are the known pre-existing PC-env set (NOT a remembered number — freshly measured in
this same session, per the no-fixed-baseline rule).

---

## §5 — §3.8 POST-MARKET BOOT CHECK (what was actually checked)

The start guard now admits 16:00–18:15, so a boot can happen there. Checked, read-only:

- **09:15 margin re-sync** (`main.py:975-1018`) — `wait_sec <= 0` → logs *"started after
  09:15, skipping re-sync"* and returns. No-op. ✓
- **`_market_hours_fn`** (`:2600`) — a call-time lambda; post-market it returns False, so
  the CNC-GTT 15-min in-hours cadence simply stays quiet. Startup [4a] reconcile still
  runs, as it does on a 15:45 boot today. ✓
- **`StateStore(market_open=...)`** (`:1760-1771`) — AC2 refuses migration only while the
  market is OPEN; post-market evaluates False, so a boot migrates exactly as it may at
  15:45 today. Unchanged in kind. ✓
- **Config-hash change** (`:1828`) — a detector returning `changed`, not a halt. Changing
  `system_config.yaml` is the normal path. ✓

⚠️ **Pre-existing, NOT introduced here, but it belongs in Monday's watch list:** a
**same-day** kill still exits **4** at `main.py:1824` (HALT scenario). So a reconciler
SOFT_KILL at ~16:30 followed by a crash-restart the same evening would refuse to come up
and the 17:00 capture would be lost. A *prior-day* kill auto-clears at the next 08:15 boot,
which is the ordinary case. Recorded, not fixed — out of scope.

---

## §6 — MONDAY

### 6.1 Rama's action — confirm the service is alive at ~17:10
This is the **compensating control for a real blind spot**, not a formality. The liveness
probe's last run is **15:55** (cron `*/5 09-15`), so between 16:00 and 17:35 the service
can die and **nothing alarms**. If it died at 16:20, Tuesday's empty `pb01_watchlist` would
look exactly like "no breakouts" and the wrong conclusion would be drawn.

### 6.2 Report these TWO results SEPARATELY — either can fail without the other
1. **Did the service survive 16:00→17:35, and was the order reconciler healthy?**
   It is the only always-on, non-market-gated, kill-capable component (15 s poll,
   `order_reconciler.py:13`), making ~380 extra broker calls in a state that has never
   existed. Worst case is a **SOFT** halt that auto-clears at the next boot — and it leaves
   a CRITICAL sentinel that **Tuesday's preflight will flag**. That is the expected SHAPE of
   the failure, written down before it happens, not a new problem.
2. **Did the 17:00 alert land and produce the first `pb01_watchlist` rows ever?**
   Read-only check: `SELECT symbol, level, breakout_date, trading_date, status FROM
   pb01_watchlist WHERE trading_date = <date>;`
   ⚠️ Capture failure is **SILENT** — log line only, no Telegram, no heartbeat, no sentinel
   (`eod_capture_safety_2026-07-24.md` §5.2). Absence of rows does not distinguish "no
   breakouts" from "capture failed" without reading `logs/`.

### 6.3 Cheap and worth it — on the first captured evening
Count how many captured symbols have held 1-minute candles. The held candles cover only the
482 momentum-tracked symbols and PB-01 names *different* stocks, so that single number says
whether the evidence this change buys is exercisable **at all**.

### 6.4 ⚠️ THE FRAMING — so nobody misreads Monday
**A successful capture is a PRECONDITION, not a win.** PB-01 is `enabled: false` with **no
live twin** (`pb01_and_v3_gate_scoping_2026-07-24.md` §2.4): it never trades, so a captured
row has no live outcome to compare against and cannot be evaluated as a would-be shadow even
if switched on. The evaluation machinery does not exist.

What this change buys is **OPTION VALUE on future evidence**. The reason to do it now rather
than later is that the capture is the only **irreversible** half: backfilling from Chartink's
trigger history is a backtest by construction — a row written today for a 13-Jul breakout can
never be out-of-sample. The out-of-sample clock starts once and cannot be backfilled.

### 6.5 ⭐ TRIGGERED FOLLOW-UP (a trigger, not a vague note)
**IF the extended window is retained beyond the first week, automated liveness monitoring
MUST be extended.** A manual daily check is a temporary safeguard and will not be sustained.

⚠️ **It is NOT a one-line cron edit.** Extending the cron alone would alarm every day after
the 17:35 exit. Both must move together:
- `cron_registry.yaml` → `liveness_probe.schedule` (`*/5 09-15` → e.g. `*/5 09-17`)
- `scripts/liveness_probe.py` → `_LIVENESS_END`, to a value **at or below** the configured
  service stop (the `<=` invariant now pinned by
  `test_window_end_not_after_a_legitimate_exit`)

That is its own small design. The `<=` drift guard will keep passing meanwhile — it permits
the probe stopping early; it only forbids it stopping late.

