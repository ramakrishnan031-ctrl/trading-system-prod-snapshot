# Silent-failure gaps — three jobs where "produced nothing" == "nothing to do"

**INVESTIGATE-ONLY.** 2026-07-25 (IST), deployed `a70793b`. Code read only; production DB opened `mode=ro&immutable=1` (zero-trace). Service `inactive` throughout. **Nothing built.** Every claim is MEASURED from quoted source or query output unless marked ASSUMED.

---

## Verdict table

| gap | current state | verdict |
|---|---|---|
| **C1** daily-report cron heartbeat | **19 SUCCESS heartbeats in production; SUCCESS *and* FAILED paths both instrumented** | 🟢 **PREMISE REFUTED — the gap does not exist.** One residual edge, §C1.3 |
| **C2** forward-shadow zero-signal day | heartbeat written on the normal path; the `if not rows` early return writes **nothing** | 🔴 **REAL.** One-line fix |
| **C3** PB-01 capture observability | boot line exists and is accurate; **after 17:00 there is nothing but INFO log lines** | 🔴 **REAL, and it goes live Monday.** Cheapest sound fix in §C3.4 |

---

## C1 — daily-report cron heartbeat: the recorded premise is WRONG

The standing note (`MEMORY`/ledger: *"the daily-report cron writes NO heartbeat — a silent report failure is invisible"*) is **false as of today**. Verified from source and from production data.

**Source** — `reports/daily_report.py`:

```
:1903   record_daily_report_heartbeat(db_path, "SUCCESS", time.perf_counter() - started)
:1919   record_daily_report_heartbeat(db_path, "FAILED", ...,
                                      message=f"{type(e).__name__}: {e}")     # except path
:1926   def record_daily_report_heartbeat(...)  -> utils.cron_heartbeat.record_heartbeat
```

The in-situ comment at `:1898-1902` even names the gap it closed — *"Without this the job ran but emitted no heartbeat/marker -> a recurring false 'daily_report missed' finding (the 23-Jun gap)"*. So this was fixed in Control Tower Phase 1d and the memory line was never retired.

**Production data** — `cron_heartbeat`, MEASURED:

```
daily_report  SUCCESS  2026-07-24T16:05:04.760879+05:30  dur=2.88
daily_report  SUCCESS  2026-07-23T16:05:05.605630+05:30  dur=3.87
daily_report  SUCCESS  2026-07-22T16:05:05.673844+05:30  dur=4.30
…  total daily_report heartbeats: 19
daily_trade_review  SUCCESS  2026-07-24T16:07:05  (18 total)
```

Both the 16:05 report and its 16:07 sibling report every run. **A crash writes `FAILED` with the exception type and message** — the failure is not silent.

### C1.3 — the one residual edge (small, and arguably already covered)

Three early returns in `main()` exit **before** the timer starts, so they write no heartbeat at all:

| line | condition | heartbeat |
|---|---|---|
| `:1870` | invalid `--date` | none |
| `:1877` | holiday/weekend skip | none — **correct**, `market_day_only: true` means cron never runs it then |
| `:1882` | **database not found** | **none** |

Only `:1882` is a genuine hole: the job exits 1, writes nothing, and the absence looks identical to "cron never fired". It is partly covered already — `daily_report` is `monitored: true` (`cron_registry.yaml:494`), and a monitored job with no heartbeat shows as `PENDING` to the Cron Officer, which is itself a signal.

**Minimal sound fix:** move `record_daily_report_heartbeat(db_path, "FAILED", 0.0, message="database not found: …")` into the `:1882` branch. **Effort: 2 lines.** Low value — recommend doing it only if someone is already in the file.

**⚠️ Action owed regardless: retire the false memory line.** A standing note asserting a monitoring gap that does not exist is worse than no note — it invites building a heartbeat that is already there.

---

## C2 — forward-shadow recorder: the zero-signal day is genuinely uninstrumented

**This is the real one of the three for data integrity**, because the forward shadow is the OOS dataset and it only ever grows forward — a silent stop cannot be backfilled.

**Current state.** `scripts/forward_shadow_record.py` instruments three of its four exits:

| exit | signal |
|---|---|
| normal write | `record_heartbeat(JOB_NAME, status="SUCCESS", message=f"date={date_iso} wrote={written} sim={simulated}")` (`:238-239`) |
| crash | `write_critical_sentinel(...)` → Telegram, *fail-LOUD* by design |
| dry-run | prints only (correct — a rehearsal must not write state) |
| **`if not rows:` early return** | **NOTHING** ← the gap |

```python
if not rows:
    print(f"forward_shadow: date={date_iso} nothing new ({len(seen)} present)"); return 0
```

Exit code 0, no heartbeat, no row. So **"zero signals today"** and **"the recorder stopped working"** are indistinguishable to every monitor.

**Production evidence it matters:** heartbeats exist for 24, 23, 22, 21, 20, 16, 15, 14-Jul — **17-Jul is absent**. That absence is explained (the 17-Jul S4 boot outage), but the point stands: an absent heartbeat is currently the *only* signal, and it cannot distinguish "no signals" from "dead".

```
forward_shadow_record  SUCCESS  2026-07-24T18:16:05  date=2026-07-24 wrote=1855 sim=1855
forward_shadow_record  SUCCESS  2026-07-23T18:16:08  date=2026-07-23 wrote=2159 sim=2140
…  (9 total)
```

### Minimal sound fix

Record a heartbeat on the empty path too, using the **functional-status vocabulary that already exists** — `utils/cron_heartbeat._encode_functional` encodes `[func=…]` into the message, and `EMPTY_NO_DATA` is an established value (`cron_officer.py:192`, and `generate_screened_stocks_csv.py:330` uses exactly this pattern for "legitimately empty").

```python
if not rows:
    print(f"forward_shadow: date={date_iso} nothing new ({len(seen)} present)")
    try:
        from utils.cron_heartbeat import record_heartbeat
        record_heartbeat(JOB_NAME, status="SUCCESS",
                         functional_status="EMPTY_NO_DATA",
                         message=f"date={date_iso} nothing new ({len(seen)} present)")
    except Exception:
        pass
    return 0
```

**Effort: ~6 lines, one file, no schema change.** Execution status stays `SUCCESS` (the job did its job); the *functional* status says the day was legitimately empty. After this, a missing heartbeat means **dead**, unambiguously.

⭐ **This is the pattern to copy** the brief asks for: the recorder's own `:238` heartbeat, extended to its quiet path.

---

## C3 ⭐ — PB-01 capture: correct at boot, blind after it. **Live Monday.**

### C3.1 The boot line exists and is trustworthy — with one correction

`main.py:3206`:

```
_log.info("V3 Step 10b PB-01 watchlist: ENABLED — capture + entry stage started "
          "(SHADOW / ANALYSIS-ONLY, NO order path)")
```

**It is accurate**, and better than it needed to be: it is emitted **after** `pb01_capture_worker.start()` and `pb01_entry_stage.start()`, so its presence proves the wiring *completed*, not merely that it was attempted. The failure path logs `_log.error("pb01 watchlist wiring failed (continuing without it): %s", exc)` (`:3209`).

📌 **Correction to the brief and to the C6 scoping note:** the boot log prints **`ENABLED` or nothing** — there is **no `DISABLED` line**. When `_watchlist_on` is false (`main.py:2939`, from `watchlist.enabled`) the whole block at `:3171` is skipped and **nothing is logged at all**. So absence of the line is ambiguous between *"disabled by config"* and *"the process never reached that point"* — though a wiring *failure* is unambiguous, because it logs an ERROR. `watchlist.enabled: true` is currently set (`system_config.yaml:442`), so Monday should print the line.

### C3.2 After 17:00 there is no success/failure signal at all — only INFO lines

Traced end to end:

| stage | what it emits |
|---|---|
| receiver `_handle_eod` | `INFO "EOD alert %s: %d symbol(s) → %d queued for capture"`, returns `{"accepted": N, "captured": N}` 200 |
| no capture worker wired | `WARNING "…watchlist disabled (no capture worker) → fail-safe miss"`, **still returns 200** |
| worker, per symbol captured | `INFO "pb01 captured %s LEVEL=%.4f …"` (`watchlist_capture.py:174`) |
| worker, per symbol deduped | `INFO "…duplicate … → deduped"` |
| worker, too few sessions | `WARNING "… < N prior daily sessions … → skip"` |
| worker, fetch failed | `WARNING "pb01 capture %s: fetch failed"` |

**No heartbeat, no sentinel, no Telegram, no aggregate count, no zero-capture alarm anywhere.** `webhook_audit` records the POST (so *delivery* is provable), but nothing records the *outcome*.

Compounding it: the receiver returns **200 to Chartink even when the capture worker is absent**, so Chartink never retries, and the heavy work happens off the request thread — "queued for capture" is not "captured".

### C3.3 Why that is worse than it sounds on Tuesday morning

`pb01_watchlist` exists and holds **0 rows** today (MEASURED; columns `symbol, trading_date, level, breakout_date, source, sr_zone_json, status, outcome_json, captured_at, created_at, consumed_at`). An empty table on Tuesday is **identical** under all of:

- no stock actually broke out (the legitimate case),
- Chartink never fired the 17:00 alert,
- the alert arrived but `_eod_capture` was `None` (boot wiring failed),
- every symbol was skipped for too-few-sessions or a fetch failure,
- the service died between 16:20 and 17:00.

Only the fourth is currently distinguishable, and only by grepping the *morning's* boot log.

### C3.4 Minimal sound fix — recommended, cheapest first

**Tier 1 (do this one): a per-alert heartbeat from `_handle_eod`.** One `record_heartbeat` at the end of the EOD branch, with the counts already in hand:

```python
record_heartbeat("pb01_capture", status="SUCCESS",
                 functional_status=("EMPTY_NO_DATA" if captured == 0 else None),
                 message=f"scanner={scanner_name} symbols={len(symbols)} captured={captured}")
```

and `status="FAILED"` + `functional_status="DISABLED"` on the `_eod_capture is None` branch. **Effort: ~8 lines in `signals/webhook_receiver.py`, no schema change.** This alone collapses four of the five ambiguities above into a readable row, and it reuses the existing mechanism rather than inventing one.

> ⚠️ It records *queued*, not *captured* — the worker is asynchronous. Honest and still decisive: a row saying `symbols=7 captured=7` proves the alert arrived and was accepted; the watchlist row count then proves the worker finished.

**Tier 2 (if Tier 1 proves insufficient): a Telegram line on a zero-capture evening.** The alert machinery already exists (`write_critical_sentinel` is used by the forward-shadow recorder). Deliberately *not* recommended first: PB-01 is `enabled: false` shadow/analysis-only, so a nightly Telegram for a legitimately quiet evening trains the operator to ignore it.

**Tier 3 (later, only with a real series): a morning count check.** A Phase-A preflight check that reads `SELECT COUNT(*) FROM pb01_watchlist WHERE trading_date = today` and WARNs on 0. Cheap, but worthless until there is a baseline of what a normal night produces — it would WARN every day of week one.

**Free, zero-code mitigation for Monday itself** (already recorded, restated because it is the only thing available before any build): grep the 08:15 boot log for `"V3 Step 10b PB-01 watchlist: ENABLED"`. Its presence proves the wiring; the `"pb01 watchlist wiring failed"` ERROR is the negative. Both are hours before 17:00, with time to act.

---

## Summary — effort vs value

| gap | fix | effort | value |
|---|---|---|---|
| C2 forward-shadow empty day | heartbeat with `EMPTY_NO_DATA` on the `if not rows` return | ~6 lines, 1 file | **high** — protects the OOS dataset; a missing heartbeat becomes unambiguous |
| C3 PB-01 capture | per-alert heartbeat from `_handle_eod` (Tier 1) | ~8 lines, 1 file | **high, and time-boxed** — goes live Monday |
| C1 daily report | already done; optionally 2 lines for the db-missing branch | 2 lines | low |
| C1 memory | **retire the false "no heartbeat" note** | 1 line | medium — a wrong note invites duplicate work |

**Common shape:** all three are the same defect class — *an exit path that produces nothing and says nothing*. In each case the mechanism to fix it already exists in the codebase (`record_heartbeat` + the `functional_status` vocabulary); none needs a new subsystem, a schema change, or a new alert channel.

⛔ **Nothing was built. No file changed by this investigation.**

---

# ✅ SHIPPED — 25-Jul-2026 (appended after the fact; everything above is the investigation as written)

## C3 — PB-01 capture heartbeat. **SHIPPED.**

`signals/webhook_receiver.py` — a new `_record_eod_heartbeat` called from both exits of `_handle_eod`, plus the missing `DISABLED` boot line on the `else` of `main.py:3171`.

| alert outcome | `status` | `functional_status` |
|---|---|---|
| symbols queued | `SUCCESS` | *(none)* |
| alert arrived, **0 queued** | `SUCCESS` | `EMPTY_NO_DATA` |
| `_eod_capture is None` (boot wiring never happened) | **`FAILED`** | **`DISABLED`** |

⚠️ **IT RECORDS `queued=`, NOT `captured=`, AND THE DISTINCTION IS LOAD-BEARING.** `submit()` hands the symbol to the capture worker, which does the LEVEL fetch/compute **off the request thread** (WR1). So the row proves the alert **arrived, authenticated, parsed and was accepted** — it does **not** prove any row reached `pb01_watchlist`. **The watchlist row count remains the only proof the worker finished.** The message deliberately says `queued=` so no future reader can take it for capture confirmation; a test asserts `"captured=" not in message`.

⛔ **This is the signal ingress**, so the heartbeat is swallowed twice over — `record_heartbeat` already returns `False` rather than raising, and the call site adds its own `except`. Two tests assert that a heartbeat which *raises* still leaves the response a byte-identical `200`. Do not "clean that up".

**Boot ambiguity closed too:** config-off previously logged **nothing**, so the absence of the `ENABLED` line could mean either "disabled by config" or "boot never got here". Every boot now states which of three it was: `ENABLED` / `DISABLED` / the wiring-failed `ERROR`.

**Not instrumented (deliberate, unchanged scope):** the malformed-payload 400s earlier in `_handle_eod`. Those answer non-200 to the sender; they are not the silent case.

## C2 — forward-shadow empty-day heartbeat. **SHIPPED.**

`scripts/forward_shadow_record.py` — the `if not rows:` exit now records `SUCCESS` + `functional_status="EMPTY_NO_DATA"` carrying the date and the already-present count, wrapped in `try/except` so a heartbeat failure can never turn a clean empty day into a failure.

⭐ **THIS CHANGES HOW A FUTURE ABSENCE MUST BE READ.** All four exits of the recorder now emit a signal (normal write → `SUCCESS`; empty day → `SUCCESS`+`EMPTY_NO_DATA`; crash → CRITICAL sentinel; `--dry-run` → deliberately nothing, and it is never the cron path). **Therefore, from 25-Jul-2026, a MISSING `forward_shadow_record` heartbeat on a trading day means the recorder is DEAD — it no longer means "a quiet day".** That is the whole value of the fix: the forward shadow is the OOS evidence path, it only grows forward, and a silent stop cannot be backfilled.

⛔ **The recorder was never run to test this.** `tests/unit/test_forward_shadow_empty_day.py` stubs `core.state_store.StateStore`, `OUT_PATH` and `record_heartbeat` before calling `main()` — the module imports `StateStore` *inside* `main()`, which is what makes the module-level stub intercept it. One test asserts the real JSONL's mtime is unchanged **and** that the only path the recorder could write to was inside the tmp sandbox; the mtime half degrades to `None == None` on a box where the real dataset does not exist, so it cannot carry that claim alone.

