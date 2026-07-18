# CT-guard anti-decay invariant + security-watcher assessment (18-Jul-2026)

Two items. **Item 1 was real and is fixed. Item 2 is NOT a bug — the premise is disproven,
and I did not change a working live security service on that basis.**

---

## LEAD SUMMARY

| Item | Outcome |
|---|---|
| **1 — CT-guard anti-decay invariant** | ✅ **DONE.** The single-route claim did **not** hold — 5 modules bypassed the guard with raw `sqlite3.connect()` on a hardcoded live path. All closed. An AST-based invariant now pins three properties, **proven to bite** on a planted bypass. Permanent rule recorded. |
| **2 — security-watcher "respawn loop"** | ⛔ **REFUSED WITH EVIDENCE — it is working exactly as designed.** 10 restarts in 10 minutes = the configured `RestartSec=60` heartbeat, documented in the unit file itself. The scan runs and alerts; nothing reads its systemd state. A timer conversion is offered as optional hygiene, **not applied**. |

---

# ITEM 1 — CT-guard anti-decay invariant

## 1a — the single-route claim: **FALSE, and now closed**

The claim was that every writable harness DB access already routes through
`assert_not_live_db`. A full scan of `tests/crash_test/` found **five modules that bypassed
it completely**, each with a hardcoded live path and a raw connect:

| Module | Route |
|---|---|
| `ct140_eod_failure.py:19,25` | `DB = os.path.expanduser("~/systems/trading-system/data_store/trading_system.db")` → `sqlite3.connect(DB)` |
| `ct143_verify.py:19,26` | same |
| `ct145_rapid_crash.py:53` | same |
| `vm_investigate.py:5` | same |
| `vm_investigate2.py:5` | same |

**Severity, stated honestly:** all five issue **SELECTs only — 0 write statements**, so no
writes were occurring. But `sqlite3.connect(path)` is **read-write**: they held *unguarded
writable handles on the live production database*, and a single future `UPDATE` would have
reached production silently. That is precisely the hazard class the guard exists to remove.
(It also means these paths could create `-wal`/`-shm` sidecars on the live DB.)

**Closed:** all five now use `get_db_connection(readonly=True)` — a `mode=ro` handle on the
live DB. Same data, no write capability, and it matches what they were already doing. After
the change: **0 raw `sqlite3.connect` outside `ct_utils.py`.**

## 1b — the invariant (ChatGPT #2/#3)

`tests/crash_test/test_ct_guard_invariant.py` statically scans every module in
`tests/crash_test/` **via AST** (so comments and string content cannot fool it) and enforces
three properties that are load-bearing *together*:

* **A** — no raw `sqlite3.connect()` outside `ct_utils.py`. Every open goes through the one
  guarded helper.
* **B** — no live-DB path literal (`trading_system.db` / `analytics.db`) outside
  `ct_utils.py`. **You cannot open what you are not allowed to name.**
* **C** — `StateStore(db_path=...)` is never handed `LIVE_DB_PATH` / `LIVE_ANALYTICS_DB_PATH`
  (it opens writable and can migrate the schema on open).

To obtain a writable live handle, a future helper would have to call `sqlite3.connect`
(blocked by **A**), name the live DB (blocked by **B**), or pass the live constant to a
writable opener (blocked by **C**, and by the runtime guard for `get_db_connection`). Every
route is closed, and the closure is now **checked automatically** rather than remembered.

**Allow-list — deliberately tiny, each entry justified in-line:** `ct_utils.py` (defines the
guard and the live constants), `test_ct_harness_safety.py` (asserts the guard *refuses* those
very paths), and the invariant file itself (the literals are its scan patterns).

## 1b(ii) — **PROOF THAT IT BITES** (a green check is evidence only if it could be red)

Planted a real bypass file in `tests/crash_test/`:

```python
import sqlite3
DB = "data_store/trading_system.db"
conn = sqlite3.connect(DB)   # unguarded WRITABLE handle on the live DB
```

```
1) WITH the planted bypass   → 2 failed, 3 passed   (invariants A and B both caught it)
2) bypass REMOVED            → 5 passed
   planted file cleaned up   → yes
```

The same proof is pinned as a test of its own (`test_invariant_detects_a_planted_bypass`),
plus a coverage assertion (`test_scan_actually_covers_the_harness`, ≥20 modules incl.
`cleanup.py`) so the scan can never pass by silently matching nothing.

**It earned its keep on day one:** during development the invariant caught a real leftover I
had missed — a dead live-path literal still sitting at `ct145_rapid_crash.py:10` after the
connect had been converted. That is exactly the decay mode it exists to prevent.

## 1c — the permanent engineering rule (ChatGPT #5)

Recorded in the invariant module's docstring (where it is *enforced*), in `docs/SYSTEM_MAP.md`,
and in memory:

> **A destructive / crash-test harness must NEVER hold a writable handle on a live database.
> All writable DB access goes through ONE guard that fails CLOSED — no override flag, no
> environment escape hatch. Resetting a live system is an OPERATOR tool in `scripts/` with a
> backup + confirmation gate, never part of a test harness.**

(#4 — keeping live-reset out of the harness — was already done on 18-Jul: `cleanup.py --live`
refuses. Whether it returns as a `scripts/` operator tool is **Rama's open decision**.)

---

# ITEM 2 — security-watcher: **NOT A BUG. Refused with evidence.**

The task premise was that `security-watcher` is stuck in `activating/auto-restart`, *"the same
pattern alert-watcher had before its `--loop` fix (which was a 104k-restart loop)"*. **That
analogy does not hold.** Four independent lines of evidence:

**1. The restart rate is the configured cadence, not a loop.**

| Measure | Value |
|---|---|
| restarts in the last **10 minutes** | **10** |
| restarts in the last **hour** | **59** |
| configured | `RestartSec=60` |

**≈1 restart per minute — exactly as configured.** A runaway loop restarts as fast as it can
(alert-watcher's real bug: 104,567 restarts, restarting *immediately*). Consecutive passes are
60s apart to the second: `13:52:23 → 13:53:23 → 13:54:24`.

**2. The design is deliberate and documented in the unit file itself:**

```
# Type=simple + Restart=always + RestartSec=60: ExecStart runs one monitoring
# pass and exits 0, systemd restarts it ~60s later → ~60s cadence. Rising
# NRestarts is NORMAL (it's a heartbeat, not a crash-loop). NB: systemd REFUSES
# Restart=always with Type=oneshot — must be Type=simple.
```

And the journal's **first ever entry** (19-Jun 23:19) proves the constraint was hit for real:
`Service has Restart= set to either always or on-success, which isn't allowed for Type=oneshot
services. Refusing.` The current shape is an **informed workaround**, not an oversight.

**3. The scan is running and healthy.** `pass complete (1 finding(s), 0 new alert(s))` every
60s; `ExecMainStatus=0`; `Deactivated successfully`. Its own health signal —
`data_store/security_state.json` — was **16.7 seconds old** when checked.

**4. Nothing is confused by the `activating` state.** All three monitors read **state-file
freshness**, not systemd's `ActiveState`:
`cron_officer.security_watcher_health` (*"rewrites security_state.json every ~60s pass, so a
stale (>5 min) or missing file means the watcher is likely DOWN"*), `system_manager` (`:825-829`),
and `scripts/preflight/checks/security.py` (`security_watcher_alive`). A grep for
`is-active` / `ActiveState` against anything security-related returns **nothing**.

⇒ **The `activating/auto-restart` observation is simply the transient between 60-second
passes.** Sampling `systemctl status` at a random moment will usually catch it mid-wait.

**Why I did not "fix" it.** Changing a working live security service — the weekend before
**Monday 20-Jul 08:15**, the first real boot after the S4 outage fix — would add risk and
variables for **zero functional gain**, on the basis of a premise I had just disproven. The
project's own standing rule applies: *an audit finding is a hypothesis; verify its premise
before implementing; refusing an item with evidence is a valid outcome.*

## The one real cost, and an option for Rama (NOT applied)

The current design's genuine downside is **journal noise: 5,720 lines in 24h** from this unit
(~2 start/stop pairs a minute), plus the operational-clarity cost that a human reading
`systemctl status` sees `NRestarts=20959` and reasonably suspects a crash loop — which is
exactly what generated this task.

**Optional cleanup, ready if you want it:** convert to a **systemd timer** —
`security-watcher.timer` (`OnUnitActiveSec=60`) driving a `Type=oneshot` service with
`Restart=no`. That is the canonical idiom for a scan-and-exit job, and **this VM already uses
timers** (`cron-watchdog.timer` is live), so it is consistent rather than novel. Benefits:
`NRestarts` stays 0, `ActiveState` becomes meaningful (`inactive` between runs), the journal
quiets down, and the "is it crash-looping?" ambiguity disappears permanently.

**Verification it would need:** after the switch, confirm `security_state.json` keeps
refreshing (~60s) — the same signal all three monitors already use — since a botched
conversion would silently stop security scanning.

**This is a hygiene improvement, not a bug fix, and it touches a live security capability, so
it is Rama's call — I have not applied it.**

---

## Regression + deploy

_(see the trailer section — filled after the run)_

## Not in scope / not done

The destructive CTs were not run. Live-reset was not re-added to the harness. No flag flipped
(`regime.enabled`, `v3_chain_mode`, `require_hmac`, F1 enforce all untouched). No schema
change. No production code changed — Item 1 is test-infra only; Item 2 changed nothing at all.
