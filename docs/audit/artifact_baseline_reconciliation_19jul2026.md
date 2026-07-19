# Artifact baseline reconciliation — 19-Jul-2026

**READ-ONLY. No recommendation. No change to any cron.** The live-DB fingerprint moved between
today's batches (`6df0c09a…` at ~16:05 → `a7a1de53…` at ~20:30) while a batch reported "all three
artifacts byte-identical." Both statements are true — "byte-identical before→after" is a *within-batch*
check — but the *cross-batch* baseline moved and nothing flagged it. This batch finds the writer,
confirms it benign, and re-establishes a clean baseline for Monday. System DOWN; book flat; nothing that
boots was touched.

---

## A. What wrote to the live DB, and when?

### A1. Fingerprints — host and path stated explicitly

| Host:path | sha256 (first 16) | size | mtime |
|---|---|---|---|
| **VM** `trading-system:/home/ubuntu/systems/trading-system/data_store/trading_system.db` | **`a7a1de53a0e398a0`** | 89,968,640 | **2026-07-19 18:00:17** |
| VM `…/data_store/v3/forward_shadow_fs-v1.jsonl` | `f7c964fd79dec961` | 4,526,565 (**7,827 lines**) | 2026-07-16 18:15:21 |
| VM `…/data_store/analytics.db` | `bb229f4474bb37c0` | 24,133,632 | 2026-07-19 02:30:04 |
| PC stub `D:\…\data\trading_system.db` (**not** the baseline) | `e3b0c44298fc1c14` | 0 | — |
| PC stub `D:\…\data_store\trading_system.db` (**not** the baseline) | `aff1642cd56a0d63` | 884,736 | — |

- **The live DB is `a7a1de53…` — it matches the ~20:30 value, NOT the earlier `6df0c09a…`.** The earlier
  batches fingerprinted the **same VM path** (89,968,640 bytes = the production file); the value simply
  moved. The PC stubs (an empty file and an 884 KB dev DB) match neither — fingerprinting one of them
  would have been vacuous, and was not done.
- **My probe wrote nothing:** the live DB fingerprint was `a7a1de53…`@`18:00:17` **before and after** the
  read (I `cp`-snapshotted to `/tmp` and queried the copy; `cp` only reads). There is no `-wal`/`-shm`.

### A2. What changed inside it — exactly one table

Diffed row counts of **every** table in the live DB against the preserved census snapshot
`/home/ubuntu/preserved/signal_census_19jul2026/trading_system_snapshot_20260719.db` (taken 11:12:37,
`mode=ro`):

- **`cron_heartbeat`: 2,398 → 2,403 (Δ +5). Every other table is identical.** `tables_with_delta = 1`.
- The +5 rows are heartbeat ids **2417–2421**: four `eod_cleanup` SKIPPED at 11:13–11:14 (the prior
  **throttle** batch's disclosed `eod_cleanup.py --db copy` incident, before this window) + one
  `gemini_weekly_patterns` at 18:00. **In the 16:05→20:30 window specifically, exactly one row was
  added.**

### A3. The writer

**`gemini_weekly_patterns`** — `cron_heartbeat` id **2421**, `SUCCESS`, `executed_at 2026-07-19
18:00:17.330`; the live DB mtime is `18:00:17.332`, a millisecond later. Schedule (from
`config/cron_registry.yaml:672-686`): **`cron_expression: 0 18 * * 0` — 18:00 every Sunday**,
`cadence: weekly`, `monitored: true`, `market_day_only: false`. Being `monitored: true`, it records a
`cron_heartbeat` row on completion — which is the write that advanced `6df0c09a…` → `a7a1de53…`.

### A4. Benign / not-benign — **BENIGN**

- Moved: **only `cron_heartbeat`** (+1 in-window; +5 since the 11:12 snapshot) — an ordinary
  housekeeping/monitoring row.
- **Not moved** (all identical to the snapshot): `signals` = 32,928 · `trades` = 361 · `orders` = 610 ·
  `fm_ledger` = 2,160 · `kill_switch_state` = 1 — the entire capital/trading-path set is static since Fri.
- **No schema change** (no table added/removed; the diff enumerated the full table list).
- **No session token:** `data_store/session/` holds only `gui_secret_key` (17-Jul 12:31) — **no
  `zerodha_token.json`** (deleted 05:00, refreshes Mon 08:15), so the system cannot have started.
- **Service `inactive`.**
- ⇒ Nothing in the not-benign list (signals/trades/orders/fm_ledger/kill_switch_state/session/schema)
  moved. **No STOP. The write is an expected weekly Gemini heartbeat.**

### A5. Forward-shadow JSONL — confirmed unchanged

`data_store/v3/forward_shadow_fs-v1.jsonl` = **7,827 lines**, mtime **16-Jul 18:15:21**, `f7c964fd…`. Not
grown — **no contamination.** (The 17-Jul recorder run left its done-mark but appended nothing.)

---

## B. The re-established baseline for Monday

### B1. Clean dated baseline (host/path-qualified) — the Monday reference

**As of 2026-07-19 ~20:35 IST, VM `trading-system:/home/ubuntu/systems/trading-system/`:**

| Artifact | sha256 (first 16) | size | mtime | last writer |
|---|---|---|---|---|
| `data_store/trading_system.db` | `a7a1de53a0e398a0` | 89,968,640 | 2026-07-19 18:00:17 | `gemini_weekly_patterns` heartbeat (id 2421) |
| `data_store/v3/forward_shadow_fs-v1.jsonl` | `f7c964fd79dec961` | 4,526,565 (7,827 ln) | 2026-07-16 18:15:21 | 16-Jul recorder |
| `data_store/analytics.db` | `bb229f4474bb37c0` | 24,133,632 | 2026-07-19 02:30:04 | `db_retention` Sun `--vacuum` |

### B2. The rule this exposed (recorded)

> **Within-batch identity is NOT cross-batch identity.** "byte-identical before→after" proves only that
> *this batch* did not write to the artifact. It does **not** prove the artifact is unchanged since the
> last *recorded* baseline. **Both checks are needed**, and a moved cross-batch baseline must be
> **flagged and reconciled** (writer identified, benignity verified), never silently adopted as the new
> "unchanged" value. The correct phrasing is "unchanged *by this session*; the standing baseline advanced
> from X to Y via <writer>, benign."

### B3. The live DB is EXPECTED to keep changing while the system is down

A changed live-DB hash while the system is down is **normal**, from two mechanisms:
1. **`monitored: true` crons write a `cron_heartbeat` row on completion** — even on non-trading days for
   `market_day_only: false` jobs. Observed today: `backup_retention` (02:00), `monitoring_canary` (08:20),
   `cron_officer_briefing` (09:20), `gemini_weekly_patterns` (18:00 Sun). More will fire before Monday
   (00:00 `log_cleanup`, 01:00 `db_backup`, 01:05 `analytics_backup`, 02:00/02:05 retention, 05:00
   `token_cleanup`, hourly `disk_monitor`).
2. **The Sunday `db_retention --vacuum` (02:30) rewrites the file** — changing the sha256 with **no** row
   change (this is why `analytics.db` mtime is 02:30).

⇒ **A changed hash is benign iff it is explained by (1) or (2) AND no sensitive table moved.** It is only
worth investigating from scratch when a sensitive table moves or an unexpected process appears.
**Consequence for Monday:** by Monday afternoon the live DB will differ completely (the system trades);
the post-session check must be **logical/row-level** (did signals/trades/orders grow as expected; did the
JSONL gain exactly one OOS day with real `sim_R`) — **not hash-equality against this Sunday baseline.**

---

## C. Name the gloss class (from the previous batch's §B) — sweep queued, not run

**The class — "attribution gloss" (a manufactured inference wearing a citation):** a summary or decision
file states an inference its cited source **never made**, and cites that source as its authority. The
instance: decision file `06_performance_allocator.md` asserted *"a multiplier applied before a cap is
masked by that cap … the Q9 batch-4 finding applies here"* — but batch-4 had reasoned the **opposite,
correctly** (`perf_weight=2.0 → 2× the concentration arm; latent only because perf ≡ 1.0`). The gloss
inverted the source and borrowed its credibility.

**Why it is dangerous in THIS project specifically:** the decision files and briefs are deliberately
designed to be read **instead of** the audits they cite (that is their purpose — one screen per choice).
So a gloss introduced at *summary* time becomes the **operative record**, and the correct original is
never re-read — the error propagates every time the summary is consulted, which is often.

**Not swept this batch** (per instruction — a bigger job, no reason to start it the night before a live
boot). **Queued for after Monday:** a sweep of the decision files / briefs for other inferences attributed
to an audit that the audit did not make. The tell is a summary that is *sharper or more actionable* than
its source — verify against the cited section, not the summary.

---

## Deploy note
Docs-only. PC == origin == VM bare after the push; delta vs `d271525` markdown-only; `AUTO-INSTALLED` is
the invariant no-op reinstall. All three artifacts proven byte-identical before/after this session
(live DB `a7a1de53…` before==after the `cp`). Nothing here touches anything that boots Monday.
