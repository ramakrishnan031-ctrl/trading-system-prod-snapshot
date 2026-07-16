# BATCH-SAFE vs LOOP-REQUIRED — classification of every open register item

**Date:** 16-Jul-2026 late / 17-Jul IST · **READ-ONLY.** Nothing executed, nothing
changed, nothing pushed. **This is a PROPOSAL for Web Claude to approve.**

**Sources:** `MASTER_PENDING_REGISTER_FINAL_16-Jul-2026.txt` (Rama's external
git-excluded working doc — read-only, **not** copied into the repo; only item IDs +
my classification appear here) · `docs/audit/full_system_audit_04july2026.md` (the four
LOW groups, ~55 line-level items) · `docs/audit/audit_05jul2026.md` · the code at the
deployed HEAD.

---

## 0. FIRST — the register is STALE (it predates today's two deploys)

It was written 16-Jul ~12:45. Verified at the **deployed** head (`06a61cb`), not assumed:

| Register item | Register says | Actual now |
|---|---|---|
| **B1** trades.sector NULL → sector cap dead | OPEN (blocks D1) | **CLOSED** — F1 deployed in `11abebb`; `_resolve_trade_sector` present. *(gate-8 enforce flip is a separate Rama decision, pending the observe soak)* |
| **B4** alert-watcher `--loop` | OPEN / pending tonight | **CLOSED** — deployed, `SubState=running` |
| **S3 / Q3** M-C4 · M-C5 · M-C6 · M-C8 | all OPEN, 0 fix commits | **CLOSED** — all four deployed tonight in `06a61cb` (tag `341cc57`), live at the 08:15 boot |
| **B2** M-S4 dead scorer | PARTIAL / live bug persists | **still OPEN** — `secondary_screener.py:407/408/410` still `"atr": None` / `"rsi": None` / `"prev_close": None` |
| **B3** P1 authoritative flip | OPEN | **still OPEN** — `system_config.yaml:292 authoritative: false` |

⇒ **Both original blockers (B1, B4) and the whole capital-safety cluster are gone.**
The register's Section-1/S3 rows should be re-verdicted CLOSED. **B2 is now the single
highest-leverage open engineering item.**

---

## 1. The rule as applied

**LOOP-REQUIRED** if it touches capital/fund_manager/balances/reservations · kill-switch/
emergency-exit · order placement/exit/lifecycle · schema/migrations/DB structure ·
position sizing/allocation/admission · regime/scoring/gate RUNTIME · **anything that
changes what or when the system trades.**

**BATCH-SAFE** only if clearly none of those. **UNSURE → LOOP.**

> **One boundary I am making explicit, because it decides several rows.**
> "Verification-only" items are BATCH-SAFE *as verifications* — they change nothing, so
> they cannot be the expensive error. But a verification of a capital path can *tempt* a
> fix mid-batch. So the batch rule for every Group-E item is: **produce a FINDING and
> STOP. Any fix that the finding implies exits the batch and goes to LOOP.** If that
> boundary is not acceptable, move all of Group E to LOOP.

---

## 2. BATCH-SAFE — **27 items**, grouped

### Group A — Docs / SYSTEM_MAP / PATHS (6)

| Item | Touches | Rationale | Size |
|---|---|---|---|
| **DEPLOYMENT.md stale paths** | docs only | `.env`/backups say `/home/ubuntu/trading-system/` (missing `systems/`). **Precondition: VM `ls` FIRST** — do not edit unverifiable paths | trivial |
| **P4-6** SYSTEM_MAP.md won't scale → split | docs only | It is now larger still (today added ~6 entries). Navigation only | medium |
| **P4-7** PATHS.md accretion + gui-untracked + pytest skew | docs, .gitignore, pytest cfg | Repo/test hygiene; no runtime | small |
| **P4-8** doc gaps (README absent, arch doc, docstrings, `ops/__init__`) | docs (+ an empty `__init__`) | Pure documentation | medium |
| **BK-3** 6 Word docs (incl. missing Doc4) | docs (gitignored binaries) | Rama-facing manuals; never deploys | medium |
| **BK-4** Windows Phase-1 doc updates | docs only | — | small |

### Group B — Repo hygiene (1)

| Item | Touches | Rationale | Size |
|---|---|---|---|
| **`reports/output/daily_report_b64.txt`** (1.44 MB base64-XLSX **embedding trade data**, committed to bypass the `*.xlsx` ignore) + stray `audit-1.jpg` | repo files | Delete + close the ignore gap. No runtime. Mild data-exposure win | trivial |

### Group C — Logging / observability, non-behavioural (5)

| Item | Touches | Rationale | Size |
|---|---|---|---|
| **`fetch_daily_candles.py:189`** prints 8 chars of the live access token to the cron log | one log line | Remove; no control flow | trivial |
| **`cron_alerts.py:33-41`** registry read error silently downgrades a critical FAILED alert to ERROR | alerting escalation | Changes when an **email** fires, never what trades | small |
| **P3-s14** webhook insert-fail dark-window observability | log lines only | Dedup-poison half already fixed (`735a6c3`); **log-only, no control-flow change** | small |
| **Q10** F2 heartbeat functional-criterion long tail | cron heartbeat metadata | Per-job functional criteria = monitoring status only | small ×N (long tail) |
| **P5-2** B-1 observability follow-up (heartbeat/metrics) | metrics only | Prerequisite *evidence* for the B3 flip; adds no gate | small |

### Group D — Test hygiene, no capital-logic change (3)

| Item | Touches | Rationale | Size |
|---|---|---|---|
| **P5-4** `test_control_tower_phase1a.py:154-155` POSIX-path | test only | PC-env only; trivial | trivial |
| **BK-5** CT crash-test drills | tests | **Run existing drills against scratch only — never live** | medium |
| *(pytest skew — folded into P4-7)* | | | |

### Group E — Verification-only → **finding only; any fix exits to LOOP** (6)

| Item | Touches | Rationale | Size |
|---|---|---|---|
| **M-SC2 runtime proof** | read-only | One live EOD confirming the screened CSV has rows — the last outstanding proof (code CLOSED `d3499b9`) | trivial |
| **F4 confirm-benign** | read-only | Confirm the 16-Jul boot's auto-clear of the 15-Jul SOFT_KILL was by design, no unexpected re-arm | trivial |
| **P3-r8** charges vs contract note | read-only | Compare + report. **Any correction = LOOP (capital)** | small |
| **P3-s13** CHECK1/RMS `costs=0.0` | read-only | `capital_operational_note` says by-design; confirm intended-vs-bug. **Any change = LOOP (capital)** | small |
| **BK-8** config drift audit + CI hardcoded-default check | read-only + CI tooling | The audit is read-only; the CI check never runs in prod. **(BK-8's third part — the startup assertion — is LOOP, see §3)** | small |
| **Confirm 16-Jul EOD emails arrived** (F0 proof) | read-only | Rama-observable | trivial |

### Group F — Trivial code, provably off the trading path (6)

| Item | Touches | Rationale | Size |
|---|---|---|---|
| **`fetch_daily_candles.py:239`** `sqlite3.IntegrityError` with no module import | one import | Latent NameError, masked by `INSERT OR IGNORE`. Adding the import is behaviour-neutral | trivial |
| **`ops_dashboard/backend/app.py:86`** ephemeral Flask `secret_key` | GUI only | Logs the operator out every restart; read-only dashboard | small |
| **M-K5** config snapshot persists an unredacted password | snapshot writer | Security hygiene. **Precondition: confirm nothing READS the snapshot expecting the password** | small |
| **Naive `datetime.now()`** → `fetch_daily_candles.py:173`, `sr_detector_backfill.py:112` | 2 cron scripts | Wrong day on a non-IST host; prod IS IST ⇒ behaviour-neutral today. *(The broader ~8-site IST dedup is LOOP)* | small |
| **Strategy `entry_start_time: 09:25` vs global `entry_start: 10:00`** | config value | **Impact: NONE — the global floor binds** (stated explicitly, as the rule requires). Cosmetic coherence | trivial |
| **`daily_trade_review`** win-rate denominator + missing non-trading-day guard | report only | Reported metric + a non-trading-day guard (Foundation Rule: no real alerts on non-trading days) | small |

**BATCH-SAFE TOTAL: 27** (A6 · B1 · C5 · D3 · E6 · F6)

### Batch-safe by RISK but too LARGE for a quick-win batch → own dedicated run (3)

| Item | Why separate |
|---|---|
| **S1 / AB-910** Audit-B Phases 9 (Ops) + 10 (Security) never produced | Read-only ⇒ zero trading risk, but it is a **two-phase audit project**, not a quick win |
| **Q9** P3-sys integration coverage ("every raw-SQL money path + per-safety-layer wired-in assertion") | Test-only ⇒ no prod change, but large and demands deep capital-path understanding |
| **BK-1** LONG-strategy review (13% LONG vs 58% SHORT) | Read-only analysis, but it **feeds D2 — the highest-leverage open decision.** Deserves its own thinking, not a batch slot |

---

## 3. LOOP-REQUIRED

**Blockers / decisions**
- **B2 / Q1** M-S4 live scorer fix — scoring runtime; 25/100 points dead. *Gated on re-parity + re-soak.* **← the top open engineering item now**
- **B3 / DG-3 / P0-3** P1 authoritative flip — P&L authority; data-gated + Rama
- **D1** sizing (its B1 data prereq is now cleared, but D1 stays HOLD) · **D2** direction · **D3** min_pass inversion · **D4** unknown 4th decision *(text lives only in Rama's .txt — Rama to confirm)*
- **F1 gate-8 enforce flip** (observe → enforce) — Rama-gated after the soak

**Capital / kill-switch**
- **W10** double-cost in `get_daily_realized_net_pnl` · **P3-c6** flip `daily_loss_include_unrealized` · **P3-c7** `_on_loss_breach` in-lock · **P3-c8** `auto_resume_kill_switch` wire-or-delete · **P3-c9** HARD_KILL exception in `clear_stale_state` · **P3-c10** G3 broker-drift escalate-on-persistence
- **D-1** `close_trade` double-release race · **RMS/manual/GTT closes pass `costs=0.0`** *(the fix; the verify is E)*
- LOW: `kill_switch.py:315-325` `auto_clear_scheduled_kill` ignores EXITING + `:222-224` unparseable `triggered_at` makes a prior-day kill **immortal** (silent no-trade day) · `order_monitor.py:652-678` client-side rate-limit errors count toward the HARD_KILL breaker · rejected last-line emergency exit only SOFT_KILLs
- **`startup_checks.py:1408-1411`** FIX-151 "block TEMP config when capital>25K" documented but not implemented — implementing it can **block boot**

**Order placement / exit / lifecycle**
- **S4 CO-GATE**: `order_protocol` dead config (→ D4 "make it real or delete") · `modify_order` variety · CO entry never validated · **M-O8** CO trail-dead · CO single-backstop. *Corroborated: 100% LIMIT_TRIPLE ⇒ **no active exit engine** (sl_trail_count 0/134)*
- **Q4** M-O3 exit-retry no time escalation · M-O5 `fire_now` leaves the fired-flag on raise · M-O9 trailed-SL bogus slippage rows
- **Q8** P3-s11 slippage guard fail-open · P3-s12 DUPLICATE_SYMBOL blind spot
- LOW: `order_monitor.py:1096-1115` fill-timeout cancel-failure marks FAILED+orphan · `:899-929` immediate-cancel of a PARTIAL publishes stale `filled_qty` · `order_placer.py:1606-1618` `_on_order_filled` uses `get()` not `pop()` · terminal-status sets diverge across cancel paths + marketable-limit flatten duplicated ×3 + exit-leg machinery ×4 · **M-X1** duplicate tick-rounding impls (**dedup could change order prices**) · `pending_rr_cancel` logs ERROR without escalation · in-memory-only state (`in_flight`/`_fill_map`/`_pending_exit_retry`)
- **`zerodha_adapter.py`** 429 backoff bypassed on `cancel_order`/`modify_order` · paper cancel overwrites COMPLETE→CANCELLED and `_synth_fill` corrupts avg_price (**paper capital drift ⇒ parity**)

**Signal entry / webhook (what enters the pipeline)**
- `webhook_receiver.py:466,538,255-266` malformed input → 500 + error-echo + CRITICAL spam; unvalidated symbols (log-line forgery); `/health` unauthenticated + bypasses the rate limiter
- `webhook_receiver.py:79-97` `_PerIpRateLimiter` unbounded growth with O(n) eviction under the request lock
- **M-U1** scanner preflight skipped on cold start · **strategies/control.py** `strategy_will_trade` fails **OPEN** on an unrecognized `trade_type`

**Schema / DB structure**
- **P3-r10** W8 closure-source blindness (**adds a `closure_source` column**) · **BK-6** remaining DB schema cleanups (~2 of 9) · **M-K6** migrations DDL extractor comment-blind · **M-K3** `db_connect` no-FK/`synchronous=FULL` (overlaps P4-4) · `state_store.py:348-376` schema guard cannot detect a **newer** DB (silently stamps *down*) · **P4-4** 19 raw sqlite sites outside `db_connect`

**Runtime/concurrency on live paths**
- **S5** M-S3 per-step timeout broken (shared pool) · M-S7 rate-limiter busy-spin · M-S8 unauth INSERT contends the writer lock
- **Q6** M-A2 alert sends block reconnect/ticker *(the M-C4 class — blocking a live path)*
- `live_feed.py:202-223` weakref callback registry refactor landmine · `main.py:2752-2784` SIGINT window before handlers + `_gate_release_pool`/healthcheck never shut down *(same `_shutdown` area M-C8 just changed)*
- **BK-8** startup assertion `loaded == used` — **a new startup assertion can abort boot**
- **4.11** hot config reload (SIGHUP) — runtime config mutation
- **Q5** M-K2 config-auditor per-strategy-window dead — **making it live changes gating**

**Delivery-gated** (triple-locked; not build-now)
- H-6 CNC exit-day · P2-1/P0-2 T2 `--arm-overnight` DDPI proof · P0-4 merge T2 branch · P2-2 Slice 2.5 · M-C2 · M-O4 · M-O6 · M-O7 · `t2_cnc_gtt_realtest.py` 3 drifts

**Infra / security / Rama-owned**
- **S2** backups all on one disk — **blocked: needs Rama to provision an off-disk/offsite target**
- Q8 controls: C1 re-baseline SSH key (**do FIRST**) · C2 SSH→Tailscale-only · C3 key passphrase · C5 rpcbind:111 · C7 rootkit scan · NR-1/P1-2 C-2 network hardening (`require_hmac:false`)
- `gui_config.local.yaml` — **prod TOTP seed + password hash in the dev tree** (regenerate seed, keep VM-only) — Rama
- **PB-01 Chartink alert not wired** (Rama; EOD, must fire before 16:00) · NR-2 credentials.xlsx · NR-4 stray D:\ folders · delete the Q6 app-password decision sheet · **P4-op** operator policy-number review

**Architecture (plan-only)**
- P4-1 state_store god-object (126 importers) · P4-2 main.py god-file · P4-3 layering erosion ×5 · **P4-9** debt *(the `deploy/post-receive` dup touches the DEPLOY path)*
- **4.4** candle backfill on startup · **4.6** BSE · **4.7** multi-account routing · **4.8** AngelOne adapter · **4.12** Telegram `/status` **`/kill`** ← *kill-switch surface*

**Data-gated**
- DG-1 S&R V2 BIR W/L count (needs Rama's manual zone-marking) · DG-2 slippage Phase-3b

**Separate final project**
- LIVE-TEST CERT LT001-LT103 + GOLD (= BK-2) — after both pipelines complete; never mixed into a deploy

---

## 4. AMBIGUOUS — classified **LOOP by the default rule**, flagged for Web Claude

Each is arguably batch-safe (no trading path) but has a real second-order risk. **Web
Claude's call — I applied "unsure → LOOP".**

| # | Item | Why it looks BATCH | Why I defaulted it to LOOP |
|---|---|---|---|
| X1 | `auth.py:62-64` **`verify_totp` returns True on an empty secret (fail-open 2FA)** + `:98-102` username-keyed lockout DoS | GUI only; zero trading path | A wrong fix **locks Rama out of the dashboard**; it is a live security control. *(Note: fail-open 2FA is the most serious item on this whole list by security severity)* |
| X2 | `eod_verify.py:60-68` P&L-variance check queries the wrong columns → branch silently dead | A one-line column-name correction in a verification script | Fixing it **makes a dormant P&L check live** ⇒ new alerts, and it interacts with the B3 shadow-mismatch story |
| X3 | **P5-3 / P4-5** daily_report retirement (two-generator overlap) | Reports only | It is a **deletion of a cron-invoked generator** — real EOD operational impact; needs a decision on which survives |
| X4 | **P4-9** debt: overdue deletions, orphans, `gemini_common` dup, stale `deploy/post-receive` dup | If truly dead, deleting is inert | Needs **dead-proof per item**, and the `post-receive` dup is on the **deploy path** |
| X5 | `instance_lock.py:90` SO_REUSEADDR defeats the socket lock on Windows | PC-only; prod Linux is safe | It is a **startup singleton guard** — a mistake permits two instances |
| X6 | `cron_heartbeat.py:157-162` holiday-calendar error degrades to a weekday-only check | Looks like alert hygiene | It changes **whether jobs run on an NSE holiday** — collides with the "no real alerts on non-trading days" Foundation Rule |
| X7 | **P3-r9** raw exec-log `trade_id` NULL | Observability/data quality | The exec log is **written from the order path** |
| X8 | **4.9** charts in daily review | Report feature; no trading path | A feature build, not a quick win — belongs in the feature backlog |

---

## 5. Proposed BATCH EXECUTION SHAPE (a proposal — nothing started)

1. **One continuous run per group** (A → B → C → D → E → F). Stop only for a genuine
   Q&A. Groups are ordered lowest-risk-first, so an early abort loses the least.
2. **One commit per item**, each with the 4-field message. Never bundle across items —
   it keeps L1 rollback per-item and keeps the reviewer's diff small.
3. **Preconditions that must hold before a group starts** (each is cheap and read-only):
   - Group A: **VM `ls` the real `.env` + backups paths first** — do not edit unverifiable paths.
   - Group E: **finding-only.** A fix implied by any finding **exits the batch to LOOP.**
   - Group F: M-K5 — confirm nothing reads the snapshot's password first.
   - Group D/BK-5: crash-test drills run **against scratch only, never live**.
4. **Regression discipline** — the scoped-suite trap has already bitten twice today:
   run the **full suite** (not the touched suites) **before the batch deploys**, and
   attribute any anomaly against the **true pre-change tree** (`git checkout <base> -- <files>`
   + `grep -c` to confirm the change is *absent*) — **never `git stash`**, which silently
   leaves the change on both sides.
5. **Deploy** — one off-market push at the end, on Rama's go, tagged as the rollback
   point; re-derive the SHA at deploy time (**the tag is the code identity**).
6. **A batch item that turns out to touch a listed area STOPS being a batch item** — it
   exits to LOOP immediately rather than being finished "since it's nearly done".
7. **Suggested batch order by value**: B + C1 (data-exposure + token-in-log first — small
   and genuinely useful), then A (docs, incl. the DEPLOYMENT.md path fix), then F, D, E.

---

## 6. Summary

| | Count |
|---|---|
| **BATCH-SAFE** | **27** (A docs 6 · B hygiene 1 · C logging 5 · D tests 3 · E verify-only 6 · F trivial-code 6) |
| Batch-safe by risk but **too large** → own run | 3 (AB-910 · Q9 · BK-1) |
| **LOOP-REQUIRED** | the remainder (~70+ across capital/kill · order/exit · webhook · schema · runtime · delivery · infra/Rama · architecture) |
| **AMBIGUOUS** (LOOP by default, flagged) | 8 (X1–X8) |
| Register rows now **STALE→CLOSED** | B1 · B4 · S3/Q3 (M-C4/C5/C6/C8) — see §0 |

**Top open engineering item after tonight: B2 / M-S4** — a quarter of the selection
score is a constant 0.0 in live, confirmed still `None` at the deployed head.

**STOPPED for Web Claude's approval. Nothing executed. Nothing pushed.**
</content>
