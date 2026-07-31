# SYSTEM INTEGRITY AUDIT 2026 — REGISTER

**Campaign artifact.** One file for the whole audit; each phase appends a section.
⛔ **FINDINGS ONLY** — nothing is fixed, tuned, configured or deployed by this audit;
a fix mid-audit changes the ground under later phases. ⛔ The two July audits
(`full_system_audit_04july2026.md`, `audit_05jul2026.md`) are **READ-ONLY sources of
record (G20)** and are never edited by this campaign.

**Finding IDs:** `IA-P<phase>-NN` for NEW findings. An ALREADY-KNOWN finding **cites its
existing ID** (G#/K#/T#/M#/F#/X#, or the July-audit IDs H-#/M-S#/M-SC#/…) and is not
re-numbered. Every figure below was **re-measured on the current system** — inherited
figures are context, not evidence. Labels follow the 27-Jul rule: BUILT · DEPLOYED ·
VERIFIED LIVE · PENDING · DEFERRED — "fixed" is not used.

---

## PHASE 1 — MARKET DATA → SIGNAL GENERATION (entry path + P1→P2 seam)

### P1.0 Measurement window & system state

| | |
|---|---|
| Session window | **Fri 31-Jul-2026 23:36 IST → Sat 01-Aug ~00:1x IST** (straddles midnight ⇒ every date-scoped query below uses explicit dates, never `date('now')`) |
| Measurements taken | 31-Jul **23:48–23:59 IST** — AFTER Friday's close, AFTER the 15:45/15:58 recon jobs, AFTER the T2 basket close (morning), AFTER the 21:44:48 symdir deploy |
| Deployed SHA (VM bare) | **`297b587`** — verified from the VM at 23:48:55 IST; == the symdir deploy |
| PC tree read | `cbe9fab` = `297b587` + 2 docs-only commits (`git diff --stat 297b587..cbe9fab` = 2 docs files) ⇒ **code read == deployed code == Monday's boot SHA** |
| Service | `inactive (dead)` at measurement time — the designed nightly state after the 17:35 self-exit |
| DB access | read-only only: `sqlite3.connect("file:…?mode=ro", uri=True)` on `trading_system.db` and `analytics.db`; log greps; **zero writes, zero POSTs, no service touched** |
| Data coverage | `webhook_audit` + `signals` both begin **2026-06-12**. ⚠️ `signals` carries the census measurement boundary (16-Jul one-off prune removed pre-09-Jul `REJECTED*/EXPIRED/DUPLICATE` rows); `webhook_audit` is unpruned ⇒ **arrival claims rest on `webhook_audit`, not `signals`** |
| Scope guard | 3-Aug/4-Aug scheduling untouched (F6 read-only context); G6 not re-opened; no live test webhooks sent |

### P1.1 Path map as verified (delta vs the July audits)

The 5-Jul Phase-4 flow map (stages 1–3) remains structurally accurate. Gate order at
ingress, verified in `signals/webhook_receiver.py` (current tree): shutting-down 503 →
per-IP 429 → auth 401 (`_authenticate`, C6 single site :267) → unknown-scanner 404
(:521-523) → **EOD routing** (`scanner_type=="eod"` → `_handle_eod`, never the intraday
queue, :531-533) → kill-switch 403 → backpressure 503 → entry-window 403
(`[10:00,15:00)`, holiday-aware, `market_windows.py:143-150`) → parse/cast/field 400s →
per-symbol: alias → excluded → INVALID_* → EXPIRED(600s) → in-flight claim → TTL dedup
(300s, (symbol,scanner)) → INSERT (UNIQUE(fingerprint,fingerprint_date), epoch-bucket
300s on `triggered_at`) → queue push. `webhook_audit` is written in `_handle_webhook`'s
`finally` for **every** outcome incl. 401/404/429 (WR13) — a change since 5-Jul, see
the Phase-5 status update in P1.4. Processor (`signal_processor.py`): PROCESSING →
kill/window/expiry/shadow-inning → **strategy lookup :759-779 (three `.get()` paths,
each a persisted loud `REJECTED_UNKNOWN_STRATEGY`)** → control gate (`strategy_will_trade`;
`REJECTED_TRADE_TYPE` split from `REJECTED_STRATEGY_CONTROL` by machine cause) →
per-strategy window → governor → **screener (writes its own PASSED/REJECTED_*/SKIPPED_*)**
→ price derivation → FIX-067 fresh-LTP full re-anchor → sizing → (v3 shadow hook,
allocator hook) → inside `portfolio_lock`: H-7 strategy cap → **symdir gate**
(`_enforce_one_trade_per_symbol_direction` :645-690, flag
`risk.one_trade_per_symbol_direction_per_day: true` on the deployed tree, writes
`REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT`; zero rows exist as of 31-Jul — correct, first
reachable Mon 3-Aug) → risk approve → reserve.

Config-vs-code (lens E): **16 strategy YAMLs** = 15 Chartink-mapped intraday/positional
strategies + `pb01_breakout_retest` (EOD-routed, `enabled:false`, fail-closed, never
enqueued — creates **no** `signals` rows by design). `scan_webhook_map.yaml`: 16
entries, **all identity-mapped** (scanner name == strategy name). This reconciles the
"15 strategies" framing (R1 decision) with X6's "13/16 CO_PLUS_TGT". Loader is
fail-fast; boot alerts on invalid YAML (18-Jul deploy). S10 cross-validation: still
**unwired in production** (no caller passes `scan_webhook_map_path`; re-verified by
repo-wide grep — only `strategies/loader.py` internals and tests reference it).

### P1.2 Headline re-measurements (mandated)

**(a) The "~249/day silent KeyError drops" figure — re-measured: the class is DEAD on
the current system.**
- `grep -c KeyError logs/system_<d>.log` for all 6 trading days 24→31-Jul: **0, 0, 0, 0,
  0, 0**. Same width, same days: `Unhandled exception in pipeline` = **0×6**;
  `signal store FAILED` (STORE_ERROR CRITICAL) = **0×6**; in-flight sweeper
  `evicted STALLED` = **0 in every retained system log** (all-time grep).
- `signals` where status LIKE `%UNKNOWN_STRATEGY%`: **0 rows all-time.**
  `webhook_audit` response_code 404 (unknown scanner): **0 rows all-time.**
- Structural reason it cannot silently recur: the scanner→strategy lookup is three
  `.get()` branches each raising a **persisted** `_PipelineReject`
  (`signal_processor.py:759-779`), and `_process_one_safe` catches everything else with
  ERROR+traceback+`PLACEMENT_FAILED` (:412-422).
- Verdict: the 19-Jul census's inversion ("never silent — ERROR + traceback; closed by
  `c22a25c` 14-Jul") is **CONFIRMED on the current system by fresh measurement**. The
  4-Jul-era figure is dead. What REMAINS silent at the entry path is the known W9 shape
  (see (c)) — per-symbol receiver drops are counted but reason-less.

**(b) Strategy firing census vs config (lens A/E).** All-time (12-Jun→31-Jul):
- `webhook_audit` POSTs per scanner: 13 intraday scanners with **4,941–12,005 POSTs
  each**, + `pb01_breakout_retest` **5 POSTs (27→31-Jul, 1/night)**.
- **`range_breakout_long` and `range_breakout_short`: 0 POSTs, 0 signals, 0 trades —
  EVER.** → finding IA-P1-01.
- `signals` per scanner: 13 scanners, 488–20,666 rows. `trades` per strategy: 13
  strategies have traded (6–95 trades). The 3 positional strategies traded historically
  (46/20/8) but are **dormant since Option A** (10-Jul, `strategies/control.py:11-17`):
  the loader no longer rewrites DELIVERY intent, so under `force_intraday_only: true`
  the LAYER-0 branch (`control.py:90-97`) rejects them per-signal —
  **measured 24→31-Jul: every one of the day's ~360–2,562 `REJECTED_STRATEGY_CONTROL`
  rows belongs to the 3 positional scanners** (e.g. 31-Jul: sector_rotation 1,073 +
  momentum 393 + swing 122). ⇒ **currently-tradeable population = 12 strategies;
  ever-fired = 13 of 15 mapped; dead-at-source = 2** (IA-P1-01).
- Onboarding was staggered (audit MIN dates: 11 scanners 12-Jun, `gap_go_short` +
  `open_high_breakdown_short` 15-Jun) — context for the silence-detection gap.

**(c) The daily entry funnel, current shape (24→31-Jul, from `webhook_audit` +
`signals`).** Per day: 2,853–3,507 in-window 200-POSTs; 548–692 designed pre/post-window
403s; **zero** 400/401/404/429/500/503 (503 last seen 1-Jul; 18 all-time). Symbol-level:
accepted 2,358–5,637/day (== `signals` rows created, after subtracting the EOD scanner —
see IA-P1-03); rejected-at-receiver 11,273–25,187/day, dedup-dominated (the census's
~80% expected TTL-dedup share) — **counted in `webhook_audit.signals_rejected`, reasons
still response-only (W9, unchanged)**. Downstream statuses are fully terminal: no
QUEUED/PROCESSING/QUEUE_FULL strays in the window; all-time non-terminal residue =
**2 RESERVED rows, both 2026-06-18 11:13:11** (REDINGTON, NYKAA, gap_go_long) — the
pre-hardening era; zero recurrence in 6 weeks. `PLACEMENT_FAILED` 1–4/day, fully
attributed: `slippage_exceeded` (designed guard) + the F5 broker MIS-block class
(PYRAMID ×2 on 27-Jul — G9's blocklist example — ASAHISONG/GALLANTT 30-Jul).
`REJECTED_SHADOW_INNING_ACTIVE` 143 on 24-Jul → 0 after — cross-confirms the 25-Jul
ShadowTracker disable held.

**(d) Tick→candle dormancy (mandated confirm).** CONFIRMED dormant, fresh evidence:
`subscribed batch` log lines 24→31-Jul = **0×6**; `analytics.candles` = 468,888 rows,
all `interval_sec=60`, **`is_synthetic=1` count = 0** (every row is the 15:40 historical
fetch's true-delta data; the live writer has never written). X7's mechanism unchanged:
no boot path calls `live_feed.subscribe()`. **What would have to be true for it to
matter** → finding IA-P1-06 (there IS one runtime waker).

### P1.3 NEW findings

---
**IA-P1-01**
- **WHAT:** Two of the 15 mapped strategies — `range_breakout_long`,
  `range_breakout_short` — are enabled, validated and loaded at every boot, and have
  **never received a single webhook since data begins (12-Jun), and nothing noticed.**
- **EVIDENCE:** `webhook_audit` GROUP BY scanner_name over 127,272 all-time rows
  (12-Jun→31-Jul, every response code incl. 403/404): both names absent (all 13 other
  intraday scanners: 4,941–12,005 POSTs). 404s all-time = 0 (no near-name variant ever
  arrived either). `signals`: 0 rows; `trades`: 0 rows. YAMLs `enabled: true`
  (`config/strategies/range_breakout_{long,short}.yaml`); map entries present.
  *Width:* every POST outcome path writes `webhook_audit` (WR13 `finally`,
  `webhook_receiver.py:498-503`); the only unaudited path (pre-`try` abort, IA-P1-04)
  is not scanner-selective. Service-down loss cannot explain it: these are market-hours
  breakout scans and the service runs 08:15–17:35.
- **CLASS:** Reachability (config-vs-reality) / Silent failure (the B-lens shape:
  no error, no alert, no empty-result complaint — G9's exact signature, upstream of
  the system).
- **NEW or KNOWN:** **NEW as a measured fact.** The *mechanism gap* is KNOWN — 5-Jul
  Phase-5 [HIGH] "Chartink-side rename/desync is silent" + gap "no per-scanner silence
  detection" + S10 dead (this finding is that gap biting, with 7 weeks of width).
- **ROOT CAUSE:** Chartink-side scanner/webhook arming is manual and has no in-system
  reconciliation; the only silence check (`SignalsArrivedCheck`) is aggregate-only, so
  13 loud scanners mask 2 dead ones. `chartink_scanners.yaml:12-13` still says "URLs
  are placeholders" — the committed URL set has never been verified against the live
  Chartink account (5-Jul UNCERTAIN, still unresolved).
- **RECOMMENDATION (described, not applied):** (1) Rama checks the Chartink account:
  do these two scanners exist, and is the webhook armed at the correct
  `/webhook/range_breakout_{long,short}` path? (2) The class fix is the already-named
  Phase-5 rec: per-scanner last-arrival age from `webhook_audit` (the data already
  exists — `MAX(date) GROUP BY scanner_name`), surfaced in the daily report or cron
  officer, alerting after N trading days silent. No new capture infrastructure needed.
- **SEVERITY-BY-IMPACT:** MED. No money is wrong; the cost is **coverage silently ≠
  configured coverage** (2/15 of intended strategy universe dark at the source for 7
  weeks) — material in the R1/§8 context where entry quality is the system's binding
  problem, and a live demonstration that a renamed/unarmed scanner would go unnoticed
  indefinitely.

---
**IA-P1-02**
- **WHAT:** `signals.strategy` is populated with the **scanner name at INSERT** and is
  never resolved or corrected to the mapped strategy — correct today only because the
  map is 16/16 identity.
- **EVIDENCE:** `webhook_receiver.py:929-943` (INSERT passes `scanner_name` for both
  `scanner` and `strategy` columns); repo-wide grep: no `UPDATE signals SET strategy`
  anywhere; strategy resolution happens only in-memory at
  `signal_processor.py:759-779`. Map identity: `config/scan_webhook_map.yaml` (16/16).
- **CLASS:** Consistency (D-lens: the row the screener/reports read vs what the
  receiver believed it wrote) — latent.
- **NEW or KNOWN:** NEW (the identity-coupling is recorded nowhere; the 17-Jul
  strategy-direction investigation documented adjacent facts but not this).
- **ROOT CAUSE:** WR16 ("receiver does not look up strategies") plus a schema that
  wants the strategy per row — satisfied by writing the scanner name and relying on
  the map being identity.
- **RECOMMENDATION (described):** either have the processor write the resolved name
  once at PROCESSING, or add a startup assertion that the map is identity (cheap,
  fail-fast, preserves WR16). Until then, any future non-identity map entry silently
  mislabels every `signals` row for that scanner (and every census/report grouping by
  `signals.strategy`).
- **SEVERITY-BY-IMPACT:** LOW, latent — zero effect today; becomes a silent
  attribution corruption the day a scanner is remapped.

---
**IA-P1-03**
- **WHAT:** `webhook_audit.signals_accepted` conflates two meanings since the pb01 EOD
  route went live: intraday-accepted (creates a `signals` row) and EOD-accepted
  (queued to the watchlist worker, **no** `signals` row) — so "accepted == rows
  created" fails by exactly the EOD symbol counts.
- **EVIDENCE:** `_handle_eod` returns `{"accepted": len(symbols), …}`
  (`webhook_receiver.py:768`) and the `finally` audit extracts it like any 200
  (:483-486). Measured daily deltas accepted-vs-rows-created: 24-Jul **0** (no pb01
  POST yet), then **+27/+12/+16/+16/+19** (27→31-Jul) == the pb01 heartbeat `symbols=`
  series (27/12/16/16) and tonight's capture (18 rows for trading_date 2026-08-03,
  +1 skipped).
- **CLASS:** Consistency / Documentation (seam bookkeeping).
- **NEW or KNOWN:** NEW (the 19-Jul census reconciled accepted==rows cleanly because
  it predates the first pb01 POST, 27-Jul — the invariant it used has since quietly
  narrowed).
- **ROOT CAUSE:** one audit column reused across two routes with different
  row-creation semantics.
- **RECOMMENDATION (described):** none needed in code for correctness; record the
  reconciliation rule — *"accepted == signals rows only after excluding
  `scanner_name='pb01_breakout_retest'` (and any future `scanner_type: eod`)"* —
  wherever the census method is next used.
- **SEVERITY-BY-IMPACT:** LOW — it costs an analyst a reconciliation loop (it cost
  this audit one); no runtime effect.

---
**IA-P1-04**
- **WHAT:** WR13's "audit row per POST regardless of outcome" has one hole: an
  exception raised **before** `_handle_webhook`'s try/finally — concretely
  `request.get_data()` at `webhook_receiver.py:449` raising 413 on a >1MB body
  (FIX-077 `MAX_CONTENT_LENGTH`), or a mid-read connection abort — produces a response
  with **no `webhook_audit` row**.
- **EVIDENCE:** `raw_body = request.get_data()` at :449 precedes the `try:` at :479;
  the `finally` audit (:498-503) covers only the try's scope. Flask's 413 handler
  answers without re-entering the route. Measured incidence: zero (no 4xx besides the
  designed classes has ever been recorded; Chartink bodies are KB-scale).
- **CLASS:** Invariant coverage (F-lens: the enforcement exists, the invariant's edge
  is narrower than its documentation) / Input safety.
- **NEW or KNOWN:** NEW (the July audits recorded "webhook_audit every POST" as fact).
- **ROOT CAUSE:** the audit write was scoped to the handler body; body-read failures
  happen before it.
- **RECOMMENDATION (described):** move the `get_data()` inside the try, or accept and
  document the narrower invariant ("every POST whose body was readable"). Not worth
  more than a line.
- **SEVERITY-BY-IMPACT:** LOW, latent — an attacker-shaped or truncated POST is
  invisible to the audit trail, but such a POST has never occurred and cannot create
  signals.

---
**IA-P1-05**
- **WHAT:** FIX-074's critical-field cast/reject branch is unreachable on real
  Chartink payloads: it casts top-level `price`/`entry_price`/`qty` fields that
  Chartink's shape does not contain (prices arrive comma-joined inside
  `trigger_prices` and are parsed per-symbol at :874).
- **EVIDENCE:** `_cast_numeric_fields` (`webhook_receiver.py:388-440`) vs the required
  Chartink fields (`stocks`,`trigger_prices`,`triggered_at`, :577-584). A Chartink
  body can never hit the critical-field 400 (:569-575); the branch fires only on
  hand-crafted payloads.
- **CLASS:** Reachability (dead validation branch) / Documentation drift.
- **NEW or KNOWN:** NEW at this precision (the July audits list the cast stage as an
  active defense in the flow map).
- **ROOT CAUSE:** FIX-074 was written for a payload shape (per-signal dicts) the
  sender never adopted; the loop shape survived.
- **RECOMMENDATION (described):** none operationally; a comment marking it
  non-Chartink-reachable would stop the flow map over-crediting it. Do not delete
  mid-campaign.
- **SEVERITY-BY-IMPACT:** LOW — inert code implying protection that isn't exercised;
  zero runtime risk.

---
**IA-P1-06**
- **WHAT:** The tick→candle path's dormancy ("DORMANT BY DECISION", e754c7e) is
  **un-enforced**: one live runtime caller can silently end it — `order_placer.py:3393`
  calls `live_feed.subscribe([instrument_token])` on the exit-retry path — and the
  candle-persist consumer is armed at every boot
  (`candle_store.register_on_candle_close(_persist_candle)`, `main.py:3399-3415`).
  One exit-retry event would: wire the first token → ticks flow → minute candles close
  → `_persist_candle` becomes a **second writer** to `analytics.candles` (the table
  the 15:40 fetch owns), and the documented synthetic-candle stickiness begins (one
  real tick ⇒ a synthetic carry-forward candle + DB insert every 60s for the process
  lifetime, surviving `unsubscribe()`); persist failures log at DEBUG only
  (`main.py:3414`). It would also arm the tick-gated H-3 stale-retry hazard.
- **EVIDENCE:** file:line above; `live_feed.subscribe()` immediately subscribes
  MODE_LTP when connected (`live_feed.py:167-188` — the ticker IS connected every
  session); dormancy today measured in P1.2(d) (0 subscriptions ever, 0 synthetic
  candles).
- **CLASS:** Coupling / Reachability (a decided-dormant state reachable by a side
  effect of an unrelated recovery path).
- **NEW or KNOWN:** the pieces are KNOWN (X7 dormancy; M-O3 names the subscribe
  fallback; the 28-Jul PARKED note documents the stickiness hazard "for whoever
  eventually wires it"); **NEW is the synthesis**: the wake-up needs no decision and
  no deploy — it is one production event away, and the hazard consumer is
  pre-registered. This is Phase 1's answer to "map WHAT would have to be true for the
  dormant path to matter": *an exit LTP-validation failure entering the retry queue.*
- **ROOT CAUSE:** the dormancy decision was recorded in docs/memory but not expressed
  as a guard anywhere in code.
- **RECOMMENDATION (described):** smallest honest class fix: assert/log-CRITICAL on
  first `subscribe()` call (making the wake-up loud), or gate `_persist_candle`
  registration behind a config flag documenting the fetch job as the table's sole
  writer. ⛔ Not applied; touches the order path ⇒ careful-loop if ever built.
- **SEVERITY-BY-IMPACT:** LOW today (the trigger has never fired: 0 subscriptions
  ever), MED if it fires — a silent second writer corrupting the candle table's
  provenance (and volume, under any future MODE_FULL — refusal E's M-D1 ordering)
  plus an unbounded 1-write/min/token leak, all below the alerting waterline.

---
**IA-P1-07**
- **WHAT:** On the M-S2 QUEUE_FULL-retry path, the processor consumes the **retry
  POST's** price/triggered_at while the DB row keeps the **original's** — the row the
  screener's audit trail describes and the tuple the pipeline prices can diverge if a
  retry ever differs in content. Same path: the fix itself is DEPLOYED but
  production-unexercised.
- **EVIDENCE:** `webhook_receiver.py:956-977` (`requeue = (existing["signal_id"], …,
  price, triggered_at)` from the current request; no UPDATE of `trigger_price` /
  `triggered_at`). Exercised count: 503s all-time = 18, none since **01-Jul** — the
  M-S2 rollback/requeue code (17-Jul era) has therefore **never run in production**
  ⇒ label: DEPLOYED, not VERIFIED LIVE.
- **CLASS:** Consistency (D-lens), latent.
- **NEW or KNOWN:** M-S2 itself is KNOWN (4-Jul) and its remediation is a **status
  update** (present in deployed code — cache rollback on QUEUE_FULL :1024-1031, on
  store-error :980-1008, requeue-on-retry :956-977). The tuple/row divergence nuance
  is NEW.
- **ROOT CAUSE:** the requeue path reuses the existing row for dedup identity but the
  fresh request for queue payload.
- **SEVERITY-BY-IMPACT:** LOW, doubly-latent (needs backpressure AND a
  content-diverging retry; Chartink retries resend the same alert).
- **RECOMMENDATION (described):** none now; if backpressure returns (503s recur),
  re-verify this path live before trusting it — it has never executed.

---
**IA-P1-08** (hygiene bundle, one ID)
- **WHAT:** Three small ingress-hygiene drifts. (a) `_load_symbol_aliases` opens
  CWD-relative `Path("config/symbol_aliases.yaml")` (`webhook_receiver.py:234`) — the
  same class as 4-Jul H-11's CWD bug; alias loss degrades to a WARNING. (b) Symbol
  case normalization is inconsistent: alias lookup and excluded-check uppercase, but
  an alias-miss keeps the symbol **as sent** (:665) — a lowercase Chartink symbol
  would flow un-normalized into signals/dedup/in-flight keys. (c) `schema.sql:74`'s
  fingerprint comment still says "minute precision" — the actual bucket is 300s
  epoch-aligned on `triggered_at` (FIX-131).
- **EVIDENCE:** file:line above; measured incidence of (a)/(b): none (service CWD is
  the repo root under systemd; Chartink sends uppercase).
- **CLASS:** Duplication/Consistency (C-lens) + Documentation.
- **NEW or KNOWN:** (a) NEW instance of a KNOWN class (H-11); (b) folds into the
  KNOWN "symbols are unvalidated strings" LOW (4-Jul); (c) NEW-trivial.
- **ROOT CAUSE:** convention (paths resolved from CWD; normalization applied
  per-consumer instead of once at the edge).
- **RECOMMENDATION (described):** anchor the path to the repo root like config
  loading does; normalize `symbol = symbol.upper()` once after alias resolution;
  fix the comment. All trivial; none applied.
- **SEVERITY-BY-IMPACT:** LOW/latent — today's inputs never exercise any of the three.

---
**IA-P1-09** (observation, not a defect)
- **WHAT:** ~30–40% of all `signals` rows written each day are foregone-conclusion
  bookkeeping: the 3 positional scanners still fire all day and every signal is
  rejected at the control gate (Option A dormancy), each costing an INSERT + 2 status
  UPDATEs on the single-writer DB during market hours, plus census noise.
- **EVIDENCE:** measured 24→31-Jul: `REJECTED_STRATEGY_CONTROL` = 360/1,915/969/
  1,521/1,017/1,588 per day, 100% attributed to
  positional_{momentum,sector_rotation,swing} (P1.2(b)); day-total rows 2,358–5,610.
- **CLASS:** Architecture/Efficiency (plus a labeling nuance: dormancy-by-breaker
  arrives under the generic `REJECTED_STRATEGY_CONTROL` label — cause
  `FORCE_BREAKER` is machine-readable in the verdict but not persisted in the status).
- **NEW or KNOWN:** the mechanism is KNOWN-by-design (Option A, 10-Jul; the
  SLICE2.5-PHASE-4 label split kept `TRADE_TYPE` distinct on purpose). The measured
  volume share is NEW context.
- **ROOT CAUSE:** deliberate fail-closed posture: scanners left armed on Chartink
  while their strategies are dormant in-system.
- **RECOMMENDATION (described, decision is Rama's):** either pause the 3 Chartink
  scanners until Slice-2.5 (removes ~1–2k rows/day at the source), or accept the
  churn as the price of keeping the arrival signal alive. ⛔ No ingress-level gate is
  proposed — that would add entry-path code for an efficiency win (careful-loop
  territory, and the current shape is the safer one).
- **SEVERITY-BY-IMPACT:** LOW — write amplification and analyst noise only; the
  rejects are correct.

### P1.4 KNOWN items re-verified — status updates (no re-numbering)

| Known ID | Status on the current system (fresh evidence) |
|---|---|
| "~249/day KeyError" (4-Jul era; census-inverted 19-Jul) | **DEAD — re-measured 0 across 6 days** (P1.2(a)); closed by `c22a25c` (14-Jul), confirmed structurally unreachable at the lookup |
| 5-Jul Ph-5 [HIGH] silent scanner rename/desync | **Half-mitigated, half-open.** The "no persistence" half is stale: 404s now land in `webhook_audit` (WR13 `finally`; 0 all-time). The detection half stands: no log/metric/silence alert — and IA-P1-01 is that gap live |
| S10 map cross-check (5-Jul Ph-5 [MED]) | still unwired in production — re-verified by grep (only loader internals + tests) |
| M-S2 QUEUE_FULL dedup poisoning (4-Jul) | remediation **DEPLOYED** in current code (rollbacks at :980-1008, :1024-1031; requeue :956-977) — **not VERIFIED LIVE** (0 backpressure events since 01-Jul; 18×503 all-time) |
| P3-s14 residual tail (board: "recorded, not widened") | **still present** — `fetch_one`/UPDATE inside the `except IntegrityError` handler (`webhook_receiver.py:951-975`) escapes with dedup-cache + in-flight claims held (sibling `except` clauses don't catch handler-raised exceptions). Line drifted :875→:951. Never fired: 0 `evicted STALLED` lines in all retained logs |
| M-S5 shadow re-entry guard gap (4-Jul) | closed in code: `_reject_if_shadow_inning_active` enforced in all 3 pipeline paths (:754, :1808, :2139) |
| M-S1 / FIX-067 stale-SL anchor (4-Jul HIGH-adjacent MED) | closed in code: full basis re-anchor (entry+SL, and TGT/sizing/reservation derive downstream), incl. the D1 quote-plumbing repair (`signal_processor.py:884-927`) |
| H-9 candle late-tick dead code + landmine (4-Jul HIGH) | **CLOSED BY REMOVAL** (Wave-3): the FIX-049 machinery was deleted; `on_tick`'s `ts` documented informational-only (`candle_store.py:170-186`) — the landmine cannot detonate by construction |
| M-S7 rate-limiter requeue spin (4-Jul) | unchanged (`signal_processor.py:376-406`) |
| M-S8 rejected-request synchronous DB INSERT (4-Jul) | unchanged **by design** — WR13 audits every outcome; per-IP limiter now has idle eviction (`webhook_receiver.py:101-107`), narrowing the 4-Jul unbounded-growth LOW |
| W9 / G15 (drop reasons never persisted) | unchanged — per-symbol reject reasons remain response-only; counts live in `webhook_audit.signals_rejected` (11k–25k/day measured) |
| X7 tick→candle never wired | re-confirmed live (P1.2(d)); see IA-P1-06 for the un-enforced boundary |
| 5-Jul Ph-5 [MED] positional trio trades on delivery-calibrated params | **no longer true** — Option A (10-Jul) preserves intent and the LAYER-0 breaker dormants all 3 (measured daily); the exposure this MED described is gone while `force_intraday_only=true` |
| M-SC3 dead IN_PROCESS reaper + Ph-4 stranded-QUEUED gap | reaper still dead; measured stray population = **2 RESERVED rows (18-Jun)**, zero recurrence — the gap is real but its production incidence is historic-only |
| Refusal A (`require_hmac` KEEP FALSE) | posture verified in code (`_authenticate` token fallback, timing-safe); 0×401 all-time |
| G6 (PB-01 25→12, CLOSED BENIGN) | not re-opened. Consistent fresh datapoint only: tonight's capture wrote 18 rows for trading_date 2026-08-03; 5 pb01 POSTs since 27-Jul, 1/night |
| F5 (broker MIS-block error class) | its signal-path signature measured: `PLACEMENT_FAILED` rows carry the broker message verbatim (27-Jul PYRAMID ×2 = G9's blocklist example; 30-Jul ASAHISONG/GALLANTT) |
| 30-Jul sr_detector token-lookup note ("worth a note if climbing") | **not climbing**: 1 (27-Jul) / 7 (30-Jul) / **3 (31-Jul)** |
| Entry-window 403 gate (designed, not a defect) | confirmed: 548–692/day, 32,365 all-time (census's 25,960 @19-Jul + ~710/day since) |
| Mon 3-Aug observation preconditions (context, untouched) | symdir flag `true` on deployed tree; `REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT` rows = 0 (correct pre-Monday); mis_filter `enabled:true, shadow:true` |

### P1.5 Open questions (not guessed into findings)

- **OQ-P1-1:** Do the two range_breakout scanners exist on Chartink, and is their
  webhook armed at the right URL? Only Rama's Chartink dashboard can settle it (the
  system-side evidence — 0 POSTs, 0 404s — is complete).
- **OQ-P1-2:** Does anything front `:5000` (nginx/proxy)? 5-Jul left it UNCERTAIN; it
  decides whether the per-IP limiter buckets real IPs or one proxy IP. Settle:
  `ss -ltn` + nginx config on the VM (read-only), or the Chartink-side URL Rama pasted.
- **OQ-P1-3:** `open_high_breakdown_short` POSTs from 15-Jun but first surviving
  signal row 02-Jul — real late Chartink arming, or the 16-Jul prune boundary eating
  its early rejects? Settle: `SELECT date, SUM(signals_accepted) FROM webhook_audit
  WHERE scanner_name='open_high_breakdown_short' GROUP BY date` (accepted counts are
  prune-immune). Curiosity only; no defect implied either way.

### P1.6 SEAM SUMMARY — can the entry path and the screener disagree, and where

Yes, in bounded and mostly-latent ways. (1) **The screener never reads the `signals`
row** — it consumes the queue tuple (symbol/price/triggered_at from the receiver's
parse) plus the in-memory StrategyConfig; the row is only a status sink. The one path
where tuple and row diverge is the never-yet-exercised QUEUE_FULL-retry (IA-P1-07).
(2) **`signals.strategy` is the scanner name** (IA-P1-02) — identity today, mislabeling
the day the map isn't. (3) **Three modules write one status column** (receiver:
QUEUED/QUEUE_FULL; processor: PROCESSING/RESERVED/REJECTED_*/PLACEMENT_FAILED/…;
screener: PASSED/REJECTED_*/SKIPPED_*) under an open-set CHECK — measured clean (all
rows terminal; 2 ancient RESERVED strays), but ordering discipline is by convention,
and the screener's writes are the P2 audit surface. (4) **The only price the screener
sees is Chartink's trigger price** (or the FIX-067 fresh LTP for momentum paths — an
in-memory re-anchor the row never learns about): no LTP-vs-trigger sanity exists at
ingress (KNOWN Ph-4 gap), so a fat-fingered trigger reaches screening intact.
(5) **Population asymmetries:** the 3 positional scanners die at the control gate and
never reach the screener (their strategy params are dead config from P2's viewpoint);
pb01 bypasses the pipeline entirely (EOD route → watchlist, no signals row); 2 mapped
scanners produce nothing at all (IA-P1-01) — so P2's effective input universe is 10
strategies' signals, not 15. (6) **From Mon 3-Aug** two additive reject sources go
live around the seam: mis_filter shadow (screener-side, log-only, expect
`REJECTED_NOT_MIS_TRADABLE (shadow)` WARNINGs with nothing dropped) and the symdir
gate (processor-side, post-screen, inside portfolio_lock) — both can only ADD
rejections. P2 starts from: the screener writes its own statuses; its score is 40%
constants (G2/M-S4: 25/100 points structurally 0.0 with `min_pass_score=60` calibrated
against that instrument); its `sector` emission is `None` end-to-end (G3); and its
quote-outage path (`SKIPPED_QUOTE_UNAVAILABLE`, 0–85/day measured) silently absorbs
whole signals by design.

**Phase 1 done** = findings above; nothing fixed; nothing pushed; live sequence
untouched. *(Phase 2 — screening — appends below this line.)*
