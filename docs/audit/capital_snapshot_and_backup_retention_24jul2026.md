# Afternoon read-only batch — `capital_snapshot` finding + backup-retention calibration

**Date:** 2026-07-24 (Friday, market hours). **Mode:** READ-ONLY (`mode=ro` DB reads; pure `build_plan()` for the retention count — no CLI run, no writes). Findings reported, nothing fixed.

---

## A2 — `capital_snapshot`: 0 rows ever, no production writer (CONFIRMED), and the data already lives in `fm_ledger`+`trades`

### A2.1 — 0 rows, in both snapshots (measured)
- **Live** `data_store/trading_system.db`: `SELECT COUNT(*) FROM capital_snapshot` = **0**.
- **19-Jul backup** `backups/trading_system-2026-07-19.db`: **0**.
Empty in both — the stronger statement. It has been empty for the table's whole life.

### A2.2 — Writers: only tests (grepped, not asserted)
`INSERT/UPDATE … capital_snapshot` across the whole repo hits **only test code**: `ops_dashboard/tests/conftest.py:375`, `tests/crash_test/test_capital_invariant_violation.py:45,65`, `tests/crash_test/cleanup.py:181`, `tests/unit/test_preflight_engine.py:27`, `tests/unit/test_state_store.py:269,283`. **Zero production INSERT/UPDATE.** `core/state_store.py` mentions the table only in a docstring (`:38`, "P7a capital_snapshot 3-balance model") — it is not even a reader/writer method; the three production readers query it directly.

### A2.3 — Anti-duplication: every field is already sourced elsewhere (measured, from schemas)
`capital_snapshot` columns vs the sound sources:

| `capital_snapshot` field | Already available as | Match |
|---|---|---|
| `margin_used` — *schema comment:* "sum of (qty·entry·0.20) over open positions" | `SUM(trades.margin_reserved)` over OPEN/PARTIAL/PENDING_FILL — `trades.margin_reserved` is defined as "qty·entry·0.20" | **identical formula** |
| `realized_pnl_today` | today's `Σ fm_ledger.pnl_delta` — the daily-loss limit already reads exactly this | **exact** |
| `margin_reserved` (pending) | pending-order margin, derivable from `trades`/`fm_ledger.margin_delta` | derivable |
| `cash_floor` (broker-settled cash) | the broker `net`/margin-sync figure the capital seed already uses (e.g. today's ₹9,865.30) | same source |

**Correct fix = point the three readers at the existing sources, NOT add a producer.** Writing a `capital_snapshot` producer would create a *second* source of truth that can drift from `fm_ledger` — the exact anti-pattern Foundation Rule 1 ("derive, don't duplicate") forbids. The table looks like an abandoned denormalization (the P7a "3-balance model") that the system never wired, because it derives capital from `fm_ledger`+`trades` instead.

### A2.4 — The two consequences (confirmed)
- **(a) Permanent daily false WARN.** `scripts/preflight/checks/engine.py:100-101`: `if not row: return self._warn("no capital_snapshot row yet (app may still be initialising)")`. The row is *always* absent, so this WARN fires on **every** preflight run and **can never clear** — the same family as the three false signals just retired (B2′ 401, C1 CRITICAL count, `[3_Capital]`). This is a **fourth** member of that family.
- **(b) `/metrics.capital_deployed_pct` is permanently 0.0.** `scripts/healthcheck_server.py:186-193` computes it **only** `if row:` (the `capital_snapshot` row). The row never exists, so the value stays at its default `0.0` (`:143`) forever — it never reflects real deployment (today there were 2 open positions with real margin; the metric still reads 0.0). The key is emitted; the number is fiction.

### A2.5 — Sizing (one sentence)
Killing **only** the false WARN is ~1 line in `preflight/checks/engine.py` (treat empty as OK / derive from the real source); doing it **right** (anti-dup) is a small **reader-redirect** across ~3 read-sites (preflight engine, `/metrics`, `ops_dashboard/backend/readers/db_reader.py`) onto `trades`+`fm_ledger` — **not** a new producer.

---

## A3 — Backup retention: one number, not a project (implementation-verified)

### A3.1 — `--max-delete` is an ABORT threshold, not a delete count (read from `scripts/backup_retention.py`)
`Plan.abort = total_delete > max_delete` (`:111`, field `:68`). `execute()` returns immediately, deleting **nothing**, when `plan.abort` (`:117`). `main()` on abort logs ERROR, sends a Telegram WARNING, records heartbeat `FAILED`, returns 2 (`:205-213`). So it is **all-or-nothing**: if a run *would* delete more than the cap, it refuses the whole run — it does **not** delete `cap` files and stop. Default `DEFAULT_MAX_DELETE = 10` (`:40`). The cron runs `scripts/backup_retention.py --apply` with **no `--max-delete` override** (`config/cron_registry.yaml:63`) → **cap = 10** every night.

### A3.2 — Current backlog + threshold, read live (via pure `build_plan()`)
**TOTAL delete candidates = 62** (inherited "55" is stale) → `62 > 10` → **ABORT every night** (this is the "nightly-abort" in memory; the sanity cap is doing its job — refusing to mass-delete 62 files unattended):

| category | present | keep-N | delete candidates | freed |
|---|---|---|---|---|
| `pre_*` | 70 | 20 | **50** | ~7.5 GB |
| `trading_system-*.db` | 19 | 14 | **5** | ~1.4 GB |
| `analytics-*.db` | 21 | 14 | **7** | ~55 MB |

### A3.3 — "Anchors" (correction to the inherited claim)
The script has **no anchor concept** — protection is purely **newest-N-by-mtime**. So an in-scope backup is protected only by being *recent enough* — i.e. by "luck of ordering," which A3.3 explicitly warns is not a structural guarantee. Nuances:
- The `pre_*` files present are all **routine per-deploy rollback snapshots** (`pre_deploy_mo5_20260722`, `pre_deploy_report_classification_20260718`, `pre_snr_v2_deploy_20260627…`, …) — transient by nature; reaping the oldest is safe.
- A **genuine long-term anchor is safe only by living OUTSIDE `data_store/backups`** (the reaper's only scope) — e.g. `/home/ubuntu/backups/pre_deploy_02jul_securitybatch/` is structurally safe because it is out of scope, **not** because of keep-N.
- Minor: `keep 20` for `pre_*` is **diluted** — the `pre_*` glob (no `.db` suffix) also matches each backup's own `-wal`/`-shm` sidecars, so 20 slots hold ~6–7 *logical* backups, not 20.

### A3.4 — Verdict (one line)
**One number + a one-time clear, NOT a redesign.** Run *once* manually with `--max-delete 62` (or higher) to drain the 62-file backlog to keep-N; thereafter steady-state is ~2 deletes/day (one per daily category beyond keep-14), well under the default cap of 10, so the nightly `--apply` succeeds unattended. The policy (category-aware keep-N, abort-cap, dry-run default, scoped patterns, never-delete-newest) is sound. **Do not change it today.**

### A3.x — Secondary observation (not the abort issue; do not let it hold anything)
The `.db`-suffixed categories (`trading_system-*.db`, `analytics-*.db`) do **not** match their own `-wal`/`-shm` sidecars, so those land in `unmatched` ("refused, never deleted") and accumulate unreaped (~38 standing, dated 06-Jul/09-Jul/12-13-Jul; `-wal` = 0 B, `-shm` = 32 KB — orphaned, harmless). A future retention tweak could reap a sidecar when its base `.db` is reaped. Reported, not fixed.

> **Self-disclosure:** 14 of the currently-present sidecars (`analytics-2026-07-10…07-15.db-wal/shm` @ 11:49, `trading_system-2026-07-19.db-wal/shm` @ 11:50) were created **by my own `mode=ro` opens this session** — opening a WAL-mode SQLite DB with `?mode=ro` (without `immutable=1`) still creates a `-shm`. The `.db` backups are intact (`-wal` = 0 B). Cleanup available on request; not deleted under the read-only mandate. Lesson: use `?mode=ro&immutable=1` (or copy-first) to read a WAL backup with zero trace. (All subsequent reads this session used `immutable=1`.)

---

## §B — Exact delete list, survivors, disk (24-Jul midday; moved here from the evening file's §B4)

Produced via **pure `build_plan()`** — nothing deleted, no CLI run, no `--max-delete` override.

**B3 — Disk MEASURED (was inherited "safe"; now measured):** the filesystem holding backups is **75.9 GB free / 102.9 GB total (26.2% used)**; backups footprint **15.29 GB across 162 files**. ⇒ **Disk is comfortable; NO urgency.** The 62-file clear can wait for a considered decision.

**B2 — ⭐ `pre_*` keep-20 holds only 7 LOGICAL backups** (the `-wal`/`-shm` sidecars occupy 13 of the 20 slots):
`pre_softkill_21jul` · `pre_deploy_b1_midnight_floor_21jul` · `pre_deploy_mo5_20260722` · `pre_deploy_e4_w10_20260720` · `pre_deploy_consec_losses_20260719` · `pre_deploy_q9_live_seed_20260719` · `pre_deploy_report_classification_20260718`.
⚠️ Worse, the keep/delete boundary **splits** one backup: `pre_deploy_report_classification_20260718.db` is a DELETE candidate while its `-wal`/`-shm` are KEPT — an incoherent survivor. So a clear Rama expects to leave "20 rollback points" actually leaves ~6 intact + 1 half-deleted. This is the concrete cost of the sidecar-dilution noted in §A3.x.

**B1 — Delete list (62 candidates):**
- **`trading_system-*.db` (5, 1.4 GB):** `-2026-07-06-postscrub`, `-07-07`, `-07-08`, `-07-09`, `-07-10`.
- **`analytics-*.db` (7, 55 MB):** `-2026-07-04` … `-2026-07-10`.
- **`pre_*` (50, 7.5 GB):** the real `.db` files — `pre_p1_v42_migration_0709`, `pre_deploy_combined_0716`, `pre_prune_0716`, `pre_deploy_16jul`, `pre_deploy_mc_cluster`, `pre_deploy_batch1`, `pre_deploy_s1s3s5_0717`, `pre_deploy_batch3_0717`, `pre_deploy_s4_p3s14_0717`, `pre_deploy_liveness_0717`, `pre_deploy_p1_health_hmac_0717`, `pre_deploy_regime_p0_0717` (+`_analytics`), `pre_deploy_strat_registry_0718`, `pre_deploy_missing_direction_alert_0718`, `pre_deploy_ct_harness_0718` (+`_analytics`), `pre_deploy_ct_invariant_0718`, `pre_deploy_q9b1`..`q9b5_0718`, `pre_deploy_report_classification_0718` — plus their `-wal`/`-shm` sidecars. All routine per-deploy rollback snapshots.

**B4 — Anchor CONFIRMED (measured, not inherited):** `/home/ubuntu/backups/pre_deploy_02jul_securitybatch/` is present and **outside `data_store/backups`** (build_plan scope check = False) → structurally out of the reaper's reach. The only real long-term protection, verified rather than assumed.

**§B verdict:** disk comfortable → **no urgency**; the delete list is entirely routine deploy/daily snapshots; but a clear leaves only **~7 real `pre_*` rollback points, not 20** — Rama should decide the clear knowing that, and the sidecar-dilution deserves a one-line retention fix when that code is next touched.
