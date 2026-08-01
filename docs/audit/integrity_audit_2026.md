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

---

## PHASE 2 — SIGNAL → SECONDARY SCREENING (scorer + gates + S&R/R:R + PB-01, and the P2→P3 seam)

### P2.0 Measurement window & system state

| | |
|---|---|
| Session window | **Sat 01-Aug-2026 ~00:2x → ~00:5x IST** (post-midnight ⇒ every date-scoped query uses explicit dates) |
| Measurements taken | 01-Aug **00:37–00:4x IST**, from the VM |
| Deployed SHA (VM bare) | **`297b587`** — reflog shows push + checkout both at 31-Jul 21:44:48 (unchanged since P1) |
| PC tree read | `05595a7` = `297b587` + 3 commits; `git diff --name-only 297b587..HEAD` = **4 docs files, zero code/config** ⇒ every file:line cited below is the deployed code |
| Service | `inactive` at measurement time — designed nightly state |
| DB access | read-only only: `sqlite3.connect("file:…?mode=ro", uri=True)`; JSONL reads; log greps; **zero writes, no service touched**. `screener_results` lives in the MAIN DB (not analytics) |
| Primary window | 6 trading days **24→31-Jul** (17,893 `screener_results` rows); all-time = 12-Jun→31-Jul (49,209 rows); log width = 23 retained `system_*.log` (01→31-Jul) |
| Scope guard | 3-Aug/4-Aug sequence untouched; nothing fixed/tuned; forward-shadow JSONL not read, recorder not run |

Deployed-tree config ground truth (grepped on the VM checkout, not the PC):
`min_pass_score: 60` · tiers 80/65 · `v3_hardgate_mode: "shadow"` · `v3_chain_mode: "shadow"` ·
`allocator_mode: "shadow"` · `mis_filter` enabled **true**, shadow **true** · `regime.enabled: false` ·
`wait_for_retest_enabled: false` · `watchlist.enabled: true`.

### P2.1 Screening path as verified (current tree == deployed)

For every intraday signal the processor calls ONE live seam, `SecondaryScreener.screen()`
(`signal_processor.py:824`, `market_data=None` ⇒ the screener always fetches its own quote).
Order inside `screen()` on the deployed config (`v3_hardgate_mode=shadow` ⇒ the OLD path decides):
**(0)** mis_filter pre-drop — enabled, SHADOW ⇒ log-only fall-through (`secondary_screener.py:142-159`) →
**(1)** quote fetch; no quote ⇒ `SKIPPED_QUOTE_UNAVAILABLE` (silent absorption by design, :161-190) →
**(1b)** circuit-proximity hard reject at the **trigger price** vs quote bands ±2% margin
(`hard_gate.py:52-111`, `DEFAULT_CIRCUIT_MARGIN_PCT=0.02`, `price_math.py:256`) →
**(2-4)** 10 steps, all always run, never short-circuit (`step_executor.py:141-153`) →
**(5)** proportional score (`quality_scorer.py:59-129`) → **(5b)** v3 shadow compare, log-only
(`_log_v3_shadow_compare`, one `v3_shadow […] OLD … NEW … gate=…` line per scored signal) →
**(6)** `effective_min` = strategy `min_score` if >0 else global 60 — **all 16 YAMLs are `min_score: 0`**
⇒ 60 for everyone → **(7)** `signal_age==0.0` defense → **(8)** PASSED.
Downstream: entry/SL derived from trigger (`_derive_prices` :1508), momentum re-anchor
(FIX-067, :904-927), sizing (`:932` — receives **tier**, not score), v3-chain observe (:957),
allocator shadow observe (:968), fused admit. The screener persists every decision twice:
`signals.status` + a `screener_results` row (`_persist`, :592-626) — 0 persist failures in all
retained logs (`state_store write failed` = 0).

### P2.2 Headline re-measurements (mandated)

**(a) G2 — the dead scorer points, re-measured fresh: STILL EXACTLY 25/100, and the full
constant fraction is 45/100.** Window 24→31-Jul, all 16,705 step-bearing rows:

| step (weight) | live value | width |
|---|---|---|
| volume_surge (15) | **0.0 on 16,705/16,705** | `avg_volume_20d=None` → `return 0.0` (`step_executor.py:251-253`) |
| atr_filter (10) | **0.0 on 16,705/16,705** | `atr=None` → `return 0.0` (:277) |
| rsi_range (10) | **0.5 on 16,705/16,705** | `rsi=None` → neutral (:297) |
| sector_strength (10) | **0.5 on 16,705/16,705** | `sector=None` → 0.5 (:336) |

Root: `_build_market_data` hardcodes `atr/rsi/sector/prev_close/avg_volume_20d = None` on
every quote ("Still not in Kite quote API; would need instruments cache",
`secondary_screener.py:406-411`); measured **null on 17,714/17,714** snapshot-bearing window
rows, while `vwap/upper_circuit/lower_circuit/bid/ask` are populated on 17,714/17,714 (the
Kite quote does carry them, `zerodha_adapter.py:1724-1729`). ⇒ **25 points dead-at-0.0
(G2's figure CONFIRMED) + 20 points pinned at half = 45/100 of the score is a constant on
every live signal.** ⚠️ G2's input list is imprecise: the 0.0-pair is `avg_volume_20d`+`atr`;
**`prev_close` is read by NO step at all** (grep width: all 10 step bodies) — it is a dead
snapshot field, not a dead score input.

**(b) The reachable-score geometry this creates (NEW precision).** Max achievable live score
= 10(vwap) + 5(rsi·½) + 15(price_action) + 5(sector·½) + 5(time) + 5(spread) + 10(circuit)
+ 10(age) = **65**. Measured: MAX(score) over all 49,209 rows ever = **65; scores >65 = zero
ever.** Against `min_pass_score=60` the entire pass band is **[60,65] — 5 points of a nominal
100**, and it decomposes almost entirely into TIME-OF-DAY × PRICE-LEVEL, because the other
variable steps are near-constant in practice (window: vwap 1.0 on 98.0%, price_action 1.0 on
74.8% (mean 0.902 — `min(1,body_pct×2)` saturates), circuit 1.0 on 100%, age 1.0 on 99.3%):
- the saturated profile scores **40 + time + spread**: after 12:15 (time=0.5) → 57.5 →
  **57** (float: `57.5/100*100`=57.4999…); 10:15–12:15 (0.8) → **59**; 10:00–10:15 (1.0) → **60 = pass**.
- `spread_check` adds its 5 only on 166/16,705 rows (1.0%) — see IA-P2-02.
Measured wall: window rejects mass at **57 ×8,958 and 59 ×3,321** (the two "perfect signal,
wrong time band" scores = 69% of all window score-rejects); passes all-time: 60 ×1,010 ·
62 ×266 · 64 ×101 · 65 ×5 (+ sub-60 passes only in the 55-floor eras, below). **Tier:
HIGH (≥80) is unreachable — 0 rows ever; MEDIUM (≥65) = exactly-65 only — 5 rows ever;
49,204/49,209 rows are LOW.** Traded book: every joinable trade since 24-Jul scored
**60 ×47 · 61 ×1 · 62 ×18 · 63 ×1** (all-time trades span 55–64; 0 unjoinable).

**(c) The min_pass floor is NOT a constant of the sample (context for D3, not re-litigated).**
Measured `eligible_score` eras: 55 on 13,082 rows (15-Jun→10-Jul) · 60 on 30,544 rows
(19-Jun→now) · NULL 5,583 (skip paths, by design). Git history of the global floor:
55 → **60** (`6450ee9`, 18-Jun) → **55** (`8a3e0b7`, 06-Jul "min-score floor 60->55") →
**60** (`61ae9cc`, 10-Jul 10:04 — a mid-morning flip). Last sub-60 PASS = 10-Jul (4,303 that
day; ~70/day pass since vs ~4,300/day under the 55 floor — **the floor edit changed the pass
population ~60×**). Any band analysis pooling across 06→10-Jul straddles two regimes.

**(d) Band-inversion mechanism — CONFIRMED present, and located.** The scorer still computes
exactly the shape the 5-sigma finding described; nothing was changed (⛔ and nothing is
proposed here — no band rules, no re-tune). WHERE it enters, mechanically: (1) 45/100 of the
score is constant ⇒ ranking power lives only in vwap-position, body-saturation, freshness,
time-band and the spread/price proxy — all **extension/chase proxies**, so a higher score
selects the more-extended entry, not the better setup; (2) the pass gate sits at the TOP of
the reachable range (60 of 65) ⇒ "PASSED" ≡ "maximally chase-profiled", which is the
measured-worst band (M-S4: 60-65 = 31% win, −0.27R; rho +0.003); (3) within-pass variance is
mostly time-of-day and the ₹1000+ spread artifact (b), neither a quality signal. The scorer
cannot rank inside the band it admits — consistent with the throttle finding (19-Jul: score
gap 0.21pt, non-monotone).

**(e) R:R — compute-then-gate CONFIRMED; no ratio manufacturing anywhere (lens H).**
- The live path has **no S&R and no R:R gate at screening**: TGT is constructed as
  `sl_distance × tgt_risk_reward` (=1.5 on all 15 YAMLs, `_derive_prices`/`_derive_target`,
  `signal_processor.py:1508-1701`) — a fixed ratio by definition, not a manufactured one.
- The V3 shadow `gate_rr` (`hard_gate.py:233-287`) selects zone edges → derives SL/TGT →
  computes RR → compares to `rr_floor=2.0`. No branch adjusts TGT/SL toward the floor;
  missing S&R **fails** (must-have), and the conservative edges are used (LONG: SL below
  support band_low − 0.20×ATR30 buffer; TGT = near edge of resistance above). PB-01's runner
  (`pb01_runner.py:127-140`) uses the deterministic SL = min(retest_low, LEVEL) − buffer and
  the nearest resistance as TGT — same compute-then-gate shape.
- Measured live shadow output (474 records since 12-Jul): **WOULD_REJECT_RR 385 (81%)** ·
  WOULD_REJECT_HTF 73 · WOULD_PASS_GATES 16 (3.4%); `v3_rr` NULL (no zone) on 179/474;
  G-RR passed 21/474. The S&R R:R floor would reject ~4 of every 5 signals the live book
  actually sized — the Kalyan-rule shadow is live and accruing.

**(f) PB-01 — live values recorded (G4 stands: values ≠ ratified spec).** Deployed values
verified on the VM tree: `watchlist.enabled true` · `level_lookback_sessions 20` ·
`capture_fetch_lookback_days 60` · entry window 09:20–11:00 @5min · `gap_guard_pct 0.03` ·
`poll_interval_sec 20` (system_config.yaml:517-529) + v3_chain gate seeds `rr_floor 2.0` ·
`sl_buffer_atr_mult 0.20` · `confirm_min_body_frac 0.50` · `confirm_volume_mult 1.20` ·
`baseline_candles_per_session 75` · `pullback_proximity_pct 0.005` /
`_atr_mult 0.50` · `hold_buffer_atr_mult 0.20` · `atr30_period 14` (:474-487). Missing-data
rules as documented: G-CONFIRM/G-PULLBACK must-have→FAIL, G-HTF/G-EXTREME fail-OPEN
(`hard_gate.py:218-222` comment; behavior verified in the four gate bodies). Live state:
`pb01_watchlist` per trading_date = 25 (28-Jul: 6 CONSUMED/1 EXPIRED_WINDOW/3 INVALIDATED/
15 SKIPPED_GAP) · 12 (29-Jul) · 15 (30-Jul) · 14 (31-Jul) · **18 PENDING for Mon 03-Aug**
(last night's capture); `pb01_would_be.jsonl` = **17 records == the 17 CONSUMED rows**
(16 WOULD_REJECT_RR · 1 WOULD_PASS_GATES) — capture→confirm→record reconciles exactly.

**(g) Regime — PREFERENCE only, and today not even that; it cannot disable a scan.**
`regime.enabled: false` ⇒ the runner is never constructed (`main.py:2999-3024`), VM
`data_store/regime/` is **empty** (never ran once), `regime_asof_unavailable` on **474/474**
v3 records, `regime_preference` contribution 0.0 on 474/474. Structural: in the LIVE 10-step
path regime appears nowhere; in the V3 shadow it enters only as 8/40 Context preference +
`gate_extreme`, which is fail-OPEN on None/UNKNOWN (`hard_gate.py:333-345`) and log-only.
Even if enabled, `extreme_flag` has **no feed** (`exchange_status_fn=None`, `main.py:3013`)
⇒ the one regime condition that could ever block is unreachable by construction today.

### P2.3 NEW findings

---
**IA-P2-01**
- **WHAT:** The pullback entry gate (`EntryGate`, P11a) is constructed, started and cleared
  at EOD on every boot — **and nothing has ever put an entry into it.** The divert that
  should route `pullback_wait_enabled` strategies into the gate was never wired (EG14/EG15:
  "integration deferred to Module 33" — never done), so **6 of the 10 live strategies**
  (first_pullback_long/short, open_high_breakdown_short, open_low_breakout_long,
  vwap_bounce_long, vwap_rejection_short) place IMMEDIATELY from trigger-derived prices while
  their YAML + the locked decision say they wait for a pullback.
- **EVIDENCE:** repo-wide: the only `WatchEntry(` constructions are `entry_gate.py:204`
  (rehydrate) and tests; no production `EntryGate.add()` caller exists;
  `pullback_wait_enabled`'s only pipeline consumer is `signal_processor.py:904` — the FIX-067
  re-anchor SKIP ("EntryGate already waits for current price", :901 — a false premise).
  Runtime, all-time widths: `signals.status LIKE 'GATE_%'` = **0** (12-Jun→); `gate_state`
  rows = **0** (no sqlite_sequence row ever); across all 23 retained logs `EntryGate.add:` =
  0 · `Gate PRICE_HIT` = 0 · `EntryGate: released` = 0 — while `EntryGate started` = **26**
  (it runs every boot). The 6 strategies trade daily via the direct path (trades exist).
- **CLASS:** Reachability (built-and-never-fed — G9's class, on the entry path) /
  Consistency (config-vs-behavior) / Documentation (docs model it as live).
- **NEW or KNOWN:** **NEW.** Adjacent KNOWNs believed the opposite: the 14-Jul deploy
  prediction calls `continue_from_gate` "LIVE (EntryGate always starts)"; 5-Jul §4.5 counts
  it among "three near-duplicate pipeline bodies" to keep in sync; `crash_test_info` lists
  `EntryGate.add()` as pipeline Step 10; a V3 plan claims the resume paths "route through
  screen()" (they bypass it, :1776). None of the July audits state the gate is unfed.
- **ROOT CAUSE:** the wiring module was deferred and the deferral was lost; every later
  document inferred liveness from construction ("it starts" ≠ "it is fed").
- **RECOMMENDATION (described, not applied):** decision first — either wire the divert
  (careful-loop: it changes entry semantics for 60% of the live book) or set the 6 YAMLs
  `pullback_wait_enabled: false` and retire the gate+resume body; in EITHER case re-scope the
  FIX-067 re-anchor to cover pullback strategies (today they are the only entries placed from
  a stale trigger with no re-anchor — the M-S1 class, excluded from the fix on this premise).
- **SEVERITY-BY-IMPACT:** **MED-HIGH.** No number is computed wrongly, but declared entry
  semantics are silently absent for 6/10 strategies; their entries carry the stale-trigger
  basis (bounded by the FIX-128/slippage-guard, which is fail-open on quote outage — 5-Jul
  [MED]); ~200 lines of unreachable resume path (`continue_from_gate`, :1770-2000) must be
  kept in sync forever; and the gap invalidates the premise under which M-S1's fix scope and
  several audits reasoned.

---
**IA-P2-02**
- **WHAT:** The spread step's threshold is off by ~100×: strategy YAMLs supply
  `max_spread_pct: 0.005` following the schema-wide FRACTION convention (0.5%), but the step
  compares it against a PERCENT value — so the gate demands spread ≤ 0.005% (0.5bp), which a
  1-tick book can only satisfy at mid ≳ ₹1000. The 5-point step has become a price-level
  selector.
- **EVIDENCE:** `step_executor.py:393-395` (`spread_pct = ((ask-bid)/mid)*100`;
  `1.0 if spread_pct <= max_spread`), YAML/schema value 0.005 (`strategies/schema.py:119`,
  all 16 YAMLs); every sibling `*_pct` in that schema is a fraction (sl_pct 0.008-0.02,
  tolerance 0.005...); the OTHER spread knob of the same name is percent-units
  (`entry_gate.max_spread_pct: 0.5`, system_config.yaml:554) — same name, two files, 100×
  apart. Measured (window): spread_check = 0.0 on 16,539/16,705, 1.0 on **166 (1.0%)**;
  crosstab: 1.0 occurs on 134/4,542 rows ≥₹1000 and 32/7,952 <₹500 (locked/crossed books) —
  0 in ₹500-999. Effect on the geometry: without the spread 5, the ceiling is 60 ⇒ sub-₹1000
  symbols can pass ONLY in 10:00-10:15 with a perfect profile; the 62/64/65 passes (372
  all-time) are almost exactly the spread-1.0 population.
- **CLASS:** Correctness (units/config-vs-code) / Consistency (two same-named knobs, two
  unit conventions).
- **NEW or KNOWN:** NEW (neither July audit nor the census flags the units; the schema table
  in 5-Jul §5 records the value without the unit mismatch).
- **ROOT CAUSE:** `*_pct` names carry no unit contract; the step author used percent, the
  schema convention is fraction. Same class as `DEFAULT_CIRCUIT_MARGIN_PCT = 0.02` (a
  fraction named PCT — consistent internally, but the naming invites the next instance).
- **RECOMMENDATION (described, not applied):** decide the intended unit (OQ-P2-1 — Rama);
  then fix ONE side and add a unit suffix convention (`_frac` vs `_pct`) checked at load.
  ⛔ Not applied here: re-unitizing to 0.5% would multiply the pass population and silently
  re-tune the entry gate — the same trap as populating the dead inputs (careful-loop).
- **SEVERITY-BY-IMPACT:** MED — it actively shapes today's selection (one of the two live
  discriminators in (b)); it also means the knob's per-strategy tuning intent is
  unimplementable at the current unit.

---
**IA-P2-03**
- **WHAT:** The tier ladder — the ONLY quality channel screening hands to sizing — is
  degenerate in live: HIGH is unreachable (80 > the 65 ceiling), MEDIUM is reachable only at
  exactly 65, so **every trade the system has ever sized ran at the LOW multiplier 0.5**
  (415/415 v34 trades; the 5 MEDIUM screener rows never became trades). `tier_multipliers
  HIGH: 1.0 / MEDIUM: 0.70` are dead config in effect; "full size" does not exist.
- **EVIDENCE:** tiers 80/65 (`scoring_weights.yaml:28-29`); ceiling 65 measured in P2.2(b);
  `screener_results` tier all-time: LOW 49,204 · MEDIUM 5 · HIGH 0; `trades.tier_weight_applied`
  = **0.5 on 415/415** rows that carry it (63 NULL = pre-v34/recovered); sizing receives the
  tier string only (QS7; `signal_processor.py:939`, `position_sizer.py:443-446`);
  `position_sizing.enabled: true` (system_config.yaml:161) so the multiplier is applied.
- **CLASS:** Consistency (E-lens: the scorer's output range vs the consumer's thresholds) /
  Reachability (dead tiers) / Config-vs-code.
- **NEW or KNOWN:** the ceiling is NEW (P2.2(b)); the consequence sharpens KNOWN G2 ("min_pass
  calibrated against the broken instrument" — the tier thresholds are too). The sizing-side
  multiplier itself is P3 scope; this finding is the seam fact.
- **ROOT CAUSE:** tier thresholds were set for a 0-100 scorer; the input universe shrank to
  0-65 when the dead inputs froze, and nothing checks reachability of config thresholds
  against the achievable range.
- **RECOMMENDATION (described, not applied):** any future re-tune of scorer inputs/floor must
  treat tier thresholds + multipliers as the SAME calibration object (they bind sizing);
  the class fix is an invariant/startup assertion that each configured threshold is
  reachable given the current live input set. ⛔ No values changed here.
- **SEVERITY-BY-IMPACT:** MED — every position is sized at half the nominal full size as a
  side effect of dead inputs (interacts with min-qty floors and the concentration cap that
  binds 415/415 — P3's territory); and the tier vocabulary in every report ("LOW") carries
  no information (constant).

---
**IA-P2-04**
- **WHAT:** Two per-strategy screening knobs are dead config: `min_volume_surge` (tuned
  per-strategy 1.2/1.3/1.5/1.8/2.0 across the YAMLs) and `min_adr_pct` (0.005 everywhere)
  have ZERO effect — their steps return 0.0 on the missing input before ever consulting the
  threshold.
- **EVIDENCE:** `step_executor.py:251-256` (`if not avg_vol: return 0.0` precedes the
  `min_surge` read) and :277-281 (`if not atr or not ltp: return 0.0` precedes `min_adr`);
  inputs None on 17,714/17,714 (P2.2(a)). The visible tuning variation (gap_go 2.0 vs
  positional_swing 1.2) implies intent that has never executed. (`min_adr_pct` would ALSO be
  units-suspect under IA-P2-02's reading — `adr_pct` is percent, 0.005 would be 0.005% ≈
  always-true — but that branch is unreachable today.)
- **CLASS:** Config-vs-code (F-lens: knobs with no effect).
- **NEW or KNOWN:** NEW at this precision (G2 records the dead points; not that the
  per-strategy thresholds are thereby inert).
- **ROOT CAUSE:** same as G2 — the inputs were never wired; the knobs assume them.
- **RECOMMENDATION (described):** when G2's inputs are ever populated (a decision, not a
  chore — it re-tunes the gate), these thresholds re-arm SILENTLY at their tuned values —
  that re-arming must be part of that decision's blast-radius list.
- **SEVERITY-BY-IMPACT:** LOW today (inert), MED at the moment anyone fixes G2 — a silent
  simultaneous re-arm of 30 threshold instances.

---
**IA-P2-05**
- **WHAT:** The V3 shadow evidence base inherits the same dead inputs it is meant to help
  replace, on both of its legs: (i) the 8-step re-scale (`v3_hardgate_mode: shadow`) has the
  same time-band cliff — NEW score mode 47 (×2,042) / 49 (×706) / max 50 on 31-Jul, i.e.
  `v3_min_pass_score: 50` is reachable ONLY by the 10:00-10:15 or spread-1.0 populations, and
  v3-MEDIUM (56) needs the spread artifact; (ii) the compose_score Context/Execution record
  is constant on 4 of its 9 inputs across ALL 474 records (regime_preference 0.0,
  sector_strength 4.0, exec volume_surge 0.0, exec atr 0.0) and near-constant on a 5th
  (momentum_position 6.0 on 474/474 — every SIZED signal has vwap=1.0+rsi=0.5 by selection);
  (iii) the shadow hard-gate leg has measured ZERO divergence power — `gate=PASS` on
  3,536/3,536 compares (31-Jul) because the OLD path pre-rejects at-circuit/proximity BEFORE
  the compare runs and queue-expiry eats stale signals upstream.
- **EVIDENCE:** log distribution + `would_be.jsonl` component counters (P2.0 battery);
  `compose_score` reuses `step_results` verbatim (`v3_chain/score.py:104-154` — by design,
  "NOT a second scorer"); shadow compare placement `secondary_screener.py:304-308` (after
  the circuit-proximity reject at :207).
- **CLASS:** Correctness-of-evidence / Reachability (the soak cannot observe what it exists
  to measure: gate divergence + threshold fit on live-quality inputs).
- **NEW or KNOWN:** the inheritance is KNOWN-by-design at the reuse level (anti-duplication
  was deliberate); NEW is the measured consequence: the soak distributions on which
  `v3_min_pass/56/75 MUST be data-fit before enforce` (scoring_weights.yaml:36-38) are 45%+
  constants, and the gate-leg comparison is structurally vacuous as instrumented.
- **ROOT CAUSE:** the shadow compare observes the OLD path's SURVIVORS, not its INPUTS; and
  the score re-composition inherits whatever the step layer could not compute.
- **RECOMMENDATION (described):** before any enforce discussion, the threshold fit needs
  either populated inputs (the G2 decision) or an explicit statement that v3 thresholds are
  being fit to the SAME two live discriminators (time band, price level); the gate leg's soak
  claim should be reworded to what it can show (freshness parity only). ⛔ Nothing reworded
  here — this register is the record.
- **SEVERITY-BY-IMPACT:** MED — research-integrity: 19 days of soak accrue evidence with
  much less information content than the soak design assumes.

---
**IA-P2-06**
- **WHAT:** The two resume entry paths skip the V3 observe hook: `continue_from_gate`
  (:1770-2000) and `continue_from_retest` (:2106+) size and admit WITHOUT
  `self._v3_chain.observe(...)` — only `_process_one` has the hook (:957-964). Today both
  paths are production-unreachable (IA-P2-01; `wait_for_retest_enabled: false`, RETEST_%
  statuses = 0 all-time), so the shadow dataset is complete — but wiring either path arms a
  silent sampling hole (their entries would vanish from the V3 evidence).
- **EVIDENCE:** grep width: `_v3_chain.observe` has exactly one call site
  (`signal_processor.py:959`); resume-path sizing at :1868/:2191 with no observe between
  sizing and admit.
- **CLASS:** Invariant coverage (the "observe every sized signal" intent, :955-956 comment,
  is enforced in one of three bodies) / latent Consistency.
- **NEW or KNOWN:** NEW (folds INTO the 5-Jul "three near-duplicate bodies" debt item as one
  more divergence).
- **ROOT CAUSE:** triplicated pipeline bodies; the hook landed in one.
- **RECOMMENDATION (described):** whichever future decision touches IA-P2-01 (wire vs
  retire), add the hook (or delete the body) so the invariant holds by construction.
- **SEVERITY-BY-IMPACT:** LOW, doubly-latent today.

---
**IA-P2-07**
- **WHAT:** A step TIMEOUT yields a neutral 0.5 for ANY step — including the two
  safety-shaped ones: a timed-out `circuit_check` contributes 0.5 instead of rejecting, and a
  timed-out `signal_age` (0.5) also slips past the `==0.0` defense-in-depth (:334). The
  at-circuit backstop then rests solely on the circuit-proximity pre-check, which is
  fail-open when the quote carries no bands.
- **EVIDENCE:** `step_executor.py:168-181` (TIMEOUT → 0.5, any step); reject-on-timeout
  exists nowhere; incidence measured **ZERO all-time** (`timed out after` = 0 lines across
  all 23 retained logs; pool rotations = 0; M-S3's 25-Jul measurement of 287,600 latencies,
  max 66ms, still holds shape). Bands measured present on 17,714/17,714 window rows ⇒ the
  proximity pre-check is currently never blind.
- **CLASS:** Safety posture (fail-open on infrastructure jitter for gate-shaped steps),
  fully latent.
- **NEW or KNOWN:** the timeout-neutral design is KNOWN (FIX-091); NEW is the observation
  that it spans the two steps the v3 design itself classifies as GATES, plus the fresh
  zero-incidence width.
- **ROOT CAUSE:** one uniform timeout policy across steps with two different roles.
- **RECOMMENDATION (described):** if ever revisited (only with a live incident to justify
  it): gate-shaped steps should fail toward reject/skip, score-shaped toward neutral. The v3
  ENFORCE design already fixes this by construction (gate before steps) — noted, not
  advanced.
- **SEVERITY-BY-IMPACT:** LOW (0 occurrences ever; steps are pure arithmetic).

---
**IA-P2-08** (hygiene bundle, one ID)
- **WHAT:** (a) `REJECTED_SCORE_{n}` embeds a variable in the status column (28 distinct
  statuses in the window) — the open-set CHECK admits it and the census must prefix-match;
  data belongs in a column, not the enum (same family as the free-text classification
  class, though nothing BRANCHES on the suffix — grep width: no consumer parses it).
  (b) `watchlist_capture._market_closed` returns True ("settled") when it cannot read the
  clock — the comment calls that conservative, but for a SETTLED-ONLY defense the
  conservative direction is skip (`watchlist_capture.py:212-217`); unreachable in practice
  (market_close is config-backed). (c) The signal-age defense (:333-348) is shadowed by the
  upstream 600s receiver expiry + 60s queue expiry — `REJECTED_SIGNAL_AGE` = 0 in the window
  and `signal_age`=0.0 occurred 0 times; it is belt-over-belt, fine, but its test-visible
  purpose should not be mistaken for live incidence.
- **CLASS:** Consistency / Documentation.
- **NEW or KNOWN:** (a) folds into the KNOWN P1.6(3) open-set status observation; (b)(c) NEW-trivial.
- **RECOMMENDATION (described):** none urgent; (a) any future status-vocabulary cleanup
  should move the score into `screener_results` only (it already lives there).
- **SEVERITY-BY-IMPACT:** LOW.

### P2.4 KNOWN items re-verified — status updates (no re-numbering)

| Known ID | Status on the current system (fresh evidence) |
|---|---|
| **G2** (B2/M-S4: 25/100 constant 0.0) | **CONFIRMED by fresh measurement, and sharpened**: 25/100 dead-at-0.0 (vol 15 + atr 10) on 16,705/16,705 window rows + 20 more points pinned at half ⇒ 45/100 constant; ceiling 65 (measured, 49,209 rows, 0 ever above); pass band [60,65]; input-list correction: the 0.0 pair is `avg_volume_20d`+`atr`, `prev_close` is unread by any step. Stays OPEN (money path; the fix is a decision, not a patch — populating inputs re-tunes gate+tiers+knobs together: IA-P2-03/04) |
| Board note "screener score is 40% constant — 4/10 steps (1/3 always 0.0, 4/6 always 0.5)" | Head claim right (4/10 steps constant); parenthetical superseded: it is **2 steps at 0.0 + 2 steps at 0.5** (weights 15+10 / 10+10) |
| M-S4 band inversion (60-65 worst; rho +0.003) | Mechanism CONFIRMED present + located (P2.2(d)); ⛔ not fixed, no band rule built. D3's OOS status unchanged (unresolved) — with the NEW caveat that the floor flapped 60→55→60 on 06/10-Jul inside the sample window (P2.2(c)) |
| **G3** (sector resolution) | Second dead consumer found: `_sector_for` (`signal_processor.py:1389-1401`) probes `sector_for`/`get_sector` — **no such method exists anywhere** (repo-wide grep) ⇒ v3/allocator records carry sector="UNKNOWN" constantly while the screener's own `sector` is None→0.5. Two sources, both empty, one emission (G3 unchanged, evidence widened) |
| **G4** (PB-01 spec absent) | Values re-recorded fresh from the deployed tree (P2.2(f)); statuses reconcile capture→confirm→would-be exactly (17==17); the DECISION remains owed — nothing invented |
| Ph-4 gap: no LTP-vs-trigger sanity | Re-confirmed with new precision: `screen()` holds BOTH the quote LTP and the trigger price in one scope and no step compares them; the proximity check uses trigger-vs-bands only |
| `SKIPPED_QUOTE_UNAVAILABLE` silent absorption (by design) | Re-measured: 179 in window (0/8/6/80/85/0 per day) — the 29/30-Jul bulge coincides with the sr_detector token-lookup ETF cluster (G6 note); class unchanged |
| mis_filter (G9 built-never-run half) | On the deployed tree: `enabled: true, shadow: true`; `REJECTED_NOT_MIS_TRADABLE` lines = 0 ever (blocklist empty); first reachable Mon 3-Aug as the OBSERVATION-day WARNING — consistent with the card |
| M-S3 (step-timeout pool poisoning; fixed by rotation) | Rotation code in place; **0 timeouts / 0 rotations all-time** (23 logs) — fix remains DEPLOYED-not-VERIFIED-LIVE, and the step bodies stay pure arithmetic |
| 5-Jul §4.5 "three near-duplicate pipeline bodies" | Sharpened: one of the three (`continue_from_gate`) is production-UNREACHABLE (IA-P2-01), a second (`continue_from_retest`) is flag-dormant (RETEST_% = 0 all-time) — the sync burden protects paths that never run |
| SNR-V2 retest divert (Phase A) | Dormant CONFIRMED: `wait_for_retest_enabled: false` on the deployed tree + RETEST statuses 0 all-time + diverter constructor-gated (`main.py:3245-3280`) |
| S&R V1 shadow detector (calibration deferred, data-gated) | Alive and accruing: `sr_detector_results` 270 rows, 29-Jun→31-Jul (observes sized candidates only — the calibration data grows at ~trades-rate, still power-bound) |
| FIX-042 proportional scoring (missing-step denominator) | The degraded branch has NEVER fired: `quality_scorer.missing_step` 0 lines; `effective_weights=100 / total_weights=100` on 3,536/3,536 scores (31-Jul) — all 10 steps always present |
| Screener persist trail (P18) | 0 `state_store write failed` lines all-time; window rows internally reconcile: 17,893 = 16,705 scored + 1,009 circuit-proximity (steps empty) + 179 quote-skips (snapshot empty) |
| Kill-switch / throttle / symdir / H-7 (processor-side, post-screen) | Out of P2 scope (P1 mapped; P3 audits sizing/risk seams). Symdir observation-day preconditions re-confirmed in passing: 0 `REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT` rows pre-Monday |

### P2.5 Open questions (not guessed into findings)

- **OQ-P2-1:** Is `max_spread_pct` MEANT as a fraction (schema convention: 0.005 = 0.5%) or
  as percent (the step's units, = 0.5bp)? Only intent settles it (P9b notes / Rama). The
  answer decides whether IA-P2-02 is "gate 100× too tight" or "schema convention violated" —
  either way the unit contract is absent. ⚠️ Changing it re-tunes the entry gate (pass
  population multiplies) — careful-loop, post-audit, Rama-gated.
- **OQ-P2-2:** Was the 06→10-Jul floor flap (60→55→60, `8a3e0b7`/`61ae9cc`) a deliberate
  calibration experiment, and is its existence recorded anywhere the D3 analysis reads? If
  not, D3's pooled sample silently mixes two admission regimes. Settles: Rama / decision
  notes; the commit messages alone carry no rationale.
- **OQ-P2-3 (curiosity):** 32 sub-₹500 rows scored spread=1.0 — locked/crossed books at
  capture, or a quote artifact? Settles: sample those snapshots' bid/ask. No defect implied.

### P2.6 SEAM SUMMARY — can screening and sizing disagree, and where

Mostly no — because almost nothing crosses the seam. What sizing receives from a passed
signal is exactly: (symbol, side, entry_price, sl_price, intent, **tier**, lot_size,
perf_weight) (`signal_processor.py:932-942`). The SCORE does not cross (persisted to
analytics only); the LEVELS the screener saw (quote LTP, vwap, bands) do not cross; S&R
does not exist on the live path. So screening and sizing cannot disagree about score or
levels — the seam carries three narrower hazards instead. (1) **The tier is a constant**:
LOW→0.5 on 415/415 sized trades ever (IA-P2-03) — the one quality channel is carrying no
information, and P3 should audit the sizer knowing its tier input never varies. (2) **The
BASIS can diverge**: for the 4 momentum strategies FIX-067 re-anchors entry+SL to a fresh
LTP AFTER screening — the screened snapshot (and the circuit-proximity verdict, taken at
trigger price) describes a price the order may no longer use; bounded by the slippage abort
+ post-fill placeability gate, both fail-open on quote outage. For the 6 pullback strategies
the OPPOSITE holds (IA-P2-01): no gate, no re-anchor — they size and place on the raw
trigger-derived basis. (3) **Parallel observers, not deciders**: the v3 chain and the
shadow allocator receive the full (score, tier, sizing, sector="UNKNOWN") candidate
post-sizing; both are log-only, and the allocator's enforce scope (`v3_only`) governs zero
strategies. Where P3 starts: the sizer's internals under a constant 0.5 tier ×
concentration binding 415/415 (the dead risk-sizer class), the risk engine's approve()
inputs, and whether anything downstream re-derives or re-reads the score (nothing found
in P2 scope does).

**Phase 2 done** = findings above; G2 re-measured fresh (still 25/100 dead-at-0.0, plus the
45-constant/65-ceiling geometry); band-inversion mechanism confirmed + located, not fixed;
R:R confirmed compute-then-gate on every path; PB-01 values recorded, no spec invented;
nothing fixed; nothing pushed; the 3-Aug/4-Aug sequence untouched.
*(Phase 3 — sizing/risk — appends below this line.)*

---

## PHASE 3 — SCREENING → RISK / SIZING (sizer + caps + sector + leverage, and the P3→P4 seam)

### P3.0 Measurement window & system state

| | |
|---|---|
| Session window | **Sat 01-Aug-2026 ~01:0x → ~01:2x IST** (measure+draft; killed by a power cut pre-commit) **+ ~01:3x–01:5x** (resume: review, re-verify, commit) |
| Measurements taken | 01-Aug **01:07–01:1x IST**, from the VM; resume re-checks **01:4x** (note below) |
| Deployed SHA (VM bare) | **`297b587`** (reflog checkout 31-Jul 21:44:48 — unchanged since P1/P2) |
| PC tree read | `f2a277b` = `297b587` + 4 docs-only commits ⇒ code read == deployed |
| Service | `inactive` (designed nightly state) |
| DB access | read-only (`mode=ro` URIs); log greps; **zero writes** |
| Primary windows | trades: the v34 sizing-audit slice (**415 all-time**, 67 in 24→31-Jul); signals all-time (12-Jun→) with prune caveat as P1; logs = 23 retained `system_*.log` (01→31-Jul) |
| Scope guard | 3-Aug/4-Aug untouched; nothing fixed/tuned; no multiplier computed; July audits read-only |

Deployed-tree config ground truth (VM grep): `risk_per_trade_pct 0.01` · `max_concentration_pct
0.10` · `max_position_value_pct 0.40` · tier 1.0/0.70/0.50 · `slm_margin_buffer_pct 0.05` ·
leverage_map INTRADAY 5.0/CO 6.0/DELIVERY 1.0/BO 5.0 · risk: `max_open_positions 5` ·
`max_daily_trades 10` · `max_sector_exposure_pct 0.40` · `max_consecutive_losses 4` ·
`daily_loss_limit_pct 0.03` · `sector_cap_mode: observe` · `daily_loss_include_unrealized false`.
Day-capital (fm_ledger INIT) across the window: **9,865.30 / 9,871.80 / 9,872.30 / 9,997.40 /
9,359.80 / 9,360.00**.

Resume verification (01:4x, after the power cut, before commit): re-measured read-only from
the VM — conc-binding **415/415** unchanged, trades 478 total / 415 v34, closed = 161 CLOSED +
45 CLOSED_MANUAL — and re-read every load-bearing citation on the PC tree
(`position_sizer.py:402-404/:425-440/:506` · `main.py:2428-2448` ·
`signal_processor.py:198/:1219-1244` · `risk_engine.py:260-266/:613-639` ·
`fund_manager.py:518-523`): all exact. One P3.4 cell (costs) was made precise from
re-measurement; nothing else changed on resume.

### P3.1 Path as verified (current tree == deployed)

One sizer, one call site per entry path: `PositionSizer.calculate()` (pure, PS1-PS13) computes
`qty_by_risk = floor(total×0.01 / sl_dist)` · `qty_by_capital = floor(bucket_avail /
(entry/leverage))` · `qty_by_concentration = floor(total×0.10 / entry)` → `raw = min(...)` →
`× tier_mult × perf_weight` (floor 1, cap 2×raw) → lot rounding (lot_size 1 everywhere) →
value-cap check (40%) → SizingResult (`position_sizer.py:176-647`). Then, inside
`portfolio_lock`: H-7 strategy cap → symdir gate → `risk_engine.approve()` — 10 checks in
order: KILL_SWITCH → SIZING_VALID → CAPITAL → OPEN_POSITIONS → DAILY_TRADES →
CONSECUTIVE_LOSSES → DAILY_LOSS → SECTOR_EXPOSURE (observe) → CONTRARY_POSITION →
DUPLICATE_SYMBOL (`risk_engine.py:408-679`) → `fm.reserve()` (recomputes margin static +5%
SL-M buffer, `fund_manager.py:518-523`) → RESERVED → entry throttle (post-reserve, releases on
reject, `signal_processor.py:1219-1227`) → `place(qty=sizing.qty, …)` verbatim (:1230-1244).
The placer re-derives only the margin METADATA for the trade row via `fm.required_margin`
(same static function, `order_placer.py:960`) — no second qty computation exists (grep width:
`required_margin` callers = FM.reserve, placer row metadata, FIX-075 drift top-up :1213-1214;
all one formula, one leverage map).

### P3.2 Headline re-measurements (mandated)

**(a) The dead risk sizer — re-measured fresh: the cap still binds 100%, and the risk path is
unreachable BY ALGEBRA, not just empirically.** Fresh v34 evidence (415 trades all-time, 67 in
window): `binding_constraint = 'concentration'` on **415/415 and 67/67**; trades where
`qty_by_risk ≤ qty_by_concentration` (risk would bind): **0**; where `qty_by_capital <
qty_by_concentration`: **0**. The margin: `qty_by_risk/qty_by_concentration` = 0.01/(0.10 ×
sl_pct) = **0.1/sl_pct** — measured mean **11.1×**, median 10.75×, **minimum ever 5.00×**
(= the sl_pct=2% strategies). For risk to bind needs sl_pct > 10%, and `sl_max_pct = 0.05`
caps every strategy at 5% ⇒ **given (risk 0.01, conc 0.10, sl_max 0.05), the risk-based
formula (PS2) cannot bind for ANY config-legal SL — the intended risk path is structurally
dead, not merely never-observed.** Actual vs intended risk, fresh: intended = 1% × INIT =
**₹93.60–99.97/day**; actual `trades.risk_amount` window = **mean ₹4.63, median ₹4.04**
(all-v34 mean 5.52, max ever 17.64) ⇒ **~20× below intent; effective risk ≈ 0.048% of
capital per trade vs the configured 1%.** Decomposition: concentration allows ≤10% of total
as position value (measured mean ₹459, max ₹999) → × sl_pct (0.8–2%) → × tier 0.5 (67/67,
415/415) — the tier constant from P2 halves what the cap already shrank. `perf_weight_applied`
distinct values all-time = **[1.0]** (see IA-P3-03).

**(b) SECTOR — the UNKNOWN handling, determined definitively from source: UNKNOWN does NOT
wave through; it is POOLED as a bucket — but the cap is inert three independent ways.**
Mechanism (`risk_engine.py:260-266, 613-639, 695-712`): `_resolve_sector` maps every
failure/blank to the literal "UNKNOWN"; gate 8 then computes `sector_exposure('UNKNOWN') +
margin > 40% × total` like any sector — the fail-CLOSED direction (pooling unrelated symbols
can only make the cap bind EARLIER, never later; there is no skip-on-UNKNOWN branch). What
actually neutralizes it: **(1) mode** — `sector_cap_mode: observe` ⇒ a breach only logs
`sector_cap_would_reject` (enforce is the Rama-gated F1 flip); **(2) input** — trades.sector
all-time: NULL 361 (pre-16-Jul rows) · **UNKNOWN 113 · real 4** (PNB/PSU_BANK 20-Jul,
SAIL/METAL + JIOFIN/FIN_SERVICES 29-Jul, SYNGENE/HEALTHCARE 30-Jul) = 96.6% UNKNOWN among
populated; source coverage `instruments.csv` = **142/2,228 symbols (6.4%) carry a sector**
(identical PC and VM) — index-member large caps the breakout universe rarely touches;
**(3) reachability** — max book margin = 5 positions × ~₹92 ≈ ₹460 ≈ 4.7% of capital vs the
40% threshold (₹3,744–3,999) ⇒ **even in enforce, even fully pooled, the cap cannot fire at
current sizing — measured `sector_cap_would_reject` lines all-time: 0** (23 logs). The
data-quality alert IS alive: `order_placer.sector_data_quality` fired **7×** all-time
(window: 24/28/29/31-Jul ×1; 27/30-Jul 0 — the ≥10-inserts session gate), sample: "10/10
(100%) trades this session resolved to UNKNOWN sector". RE9's engine-side WARNING is
unreachable (`InstrumentCache.sector` never raises — 0 lines ever), so the engine resolves
UNKNOWN fully silently; the placer alert is the only loudness (IA-P3-06a).

**(c) G8 — unlevered sizing CONFIRMED, and the leverage surface mapped (no multiplier
computed).** The binding constraint (concentration) is computed on **notional** —
`total × 0.10 / entry_price` — with no leverage term (`position_sizer.py:402-404`), so the
position the system takes is sized as if leverage were 1× while MIS buying power is 5×.
Leverage enters only: `qty_by_capital` (never bound: 0/415), `margin_required` bookkeeping,
`fm.reserve` (static map + 5% SL-M buffer; window avg RESERVE ₹93.36 vs row margin ₹86.26 —
populations differ by released reservations; the ×1.05 mechanism is code-verified), and the
CAPITAL gate. The FIX-072 live-broker-margin branch is **DEAD in production**: main.py's
`PositionSizer(...)` construction (:2428-2448) passes **no `broker_adapter`** ⇒ static map
always; measured `live_margin_used` + `live_margin_fallback` = **0 lines across all 23
retained logs** (IA-P3-02). What a leverage recalibration would touch (mapped, ⛔ not
computed): the three candidate-qty formulas' relative order, both margin computations, the
SL-M buffer base, the CAPITAL gate, and — dominant — the concentration/value-cap notional
semantics; the recalibration is the work, exactly as G8 states.

**(d) The units sweep (H lens) — every sizing/risk %-knob checked against its consuming
expression: NO new fraction-vs-percent mismatch found.** Width: 13 knobs traced end-to-end —
`risk_per_trade_pct` (×capital ✓), `max_concentration_pct` (×capital ✓),
`max_position_value_pct` (×capital ✓), `max_sector_exposure_pct` (×total ✓),
`daily_loss_limit_pct` (×total ✓), `sector_unknown_alert_pct` (fraction vs fraction ✓,
`order_placer.py:732-733`), `lot_skew_rejection_threshold` (fraction vs fraction ✓),
`slm_margin_buffer_pct` (×margin ✓), `entry_offset_pct` (fraction ✓), `tgt_min_pct`
(fraction vs |tgt−entry|/entry ✓), `price_drift_threshold` (fraction, consumed by the
FIX-075 top-up), tier multipliers (dimensionless), `min_tick_size` (rupees ✓). Two NAMING
hazards of the P2-02 class recorded, both internally consistent today:
`DEFAULT_CIRCUIT_MARGIN_PCT = 0.02` (a fraction named PCT) and the schema comment
`margin_reserved -- qty * entry * 0.20` (describes 5× leverage as a hardcoded 0.20;
the code uses the map).

### P3.3 NEW findings

---
**IA-P3-01**
- **WHAT:** The concentration cap's integer floor is a hard PRICE CEILING on the tradeable
  universe: any symbol whose entry price exceeds 10% of live total capital sizes to
  `qty_by_concentration = 0` and dies as `REJECTED_SIZING_CONCENTRATION` — at current
  capital, **everything above ~₹936–1,000 is unsizeable** — and this couples destructively
  with P2's spread artifact: the ONLY score slack the live screener grants (spread +5,
  needing mid ≳ ₹1000) is granted to exactly the price class sizing cannot size.
- **EVIDENCE:** `floor((total × 0.10)/entry)` = 0 ⇔ entry > 0.10×total
  (`position_sizer.py:402-404`); raw_qty=0 early-exit with constraint=CONCENTRATION
  (:425-440). Measured: `REJECTED_SIZING_CONCENTRATION` = **3,340 all-time — the ONLY
  `REJECTED_SIZING_*` status that has ever occurred** (full LIKE split); window 8–74/day
  (184 total); trigger-price of those signals: **min 986.1, p10 1,125/1,281, median
  1,496/2,087 (all-time/window), max 4,763** — the min sits exactly at 0.10× that day's
  live total (INIT 9,865 → cutoff ~986; 30/31-Jul INIT 9,360 → cutoff ~936). Cross-phase:
  the 62/64/65-score passes (P2.2(b)) need mid ≳ ₹1000 (1-tick book) — above every window
  cutoff — so the spread-bonus route to a pass mostly terminates here; the surviving 62s
  trade via the locked-book (bid==ask) sub-₹1000 route. Census B1's price bias (>₹990:
  24% of admissions → 0.14% at the risk engine) is this mechanism, now named at its site.
- **CLASS:** Correctness/Consistency (an emergent interaction of two configs and one
  screener artifact — none of the three documents it) / Architecture.
- **NEW or KNOWN:** the census price-bias observation is KNOWN (B1, 19-Jul); **NEW is the
  mechanism identification** (the one-share floor at 0.10×total), its exact price cutoff,
  its status vocabulary (`REJECTED_SIZING_CONCENTRATION` ⇔ this and only this), and the
  coupling with IA-P2-02.
- **ROOT CAUSE:** integer sizing at small capital: the cap is a percentage rule whose floor
  becomes a binary price gate when 10% of capital ≈ one share.
- **RECOMMENDATION (described, not applied):** record it as universe selection in the
  strategy docs (or decide it away when capital scales); any D1 sizing decision should
  treat "the tradeable price band is capital-dependent" as an input. ⛔ No config change
  proposed — money-path, Rama's.
- **SEVERITY-BY-IMPACT:** MED — a silent, undocumented universe filter (~8-74 signals/day)
  that also interacts with the screener's price selector; no money is computed wrongly.

---
**IA-P3-02**
- **WHAT:** FIX-072 (live broker margin in sizing) is built-and-never-wired: `PositionSizer`
  accepts `broker_adapter` and implements the live-margin branch, but main.py never passes
  it — the static leverage map decides every live sizing. Two sibling knobs on the same
  constructor — `min_tick_size`, `max_single_order_qty` — are also NOT passed: the YAML
  values (0.05 / 10000) are dead config that merely COINCIDE with the code defaults.
- **EVIDENCE:** ctor call `main.py:2428-2448` (argument list verified complete — no
  broker_adapter/min_tick_size/max_single_order_qty); branch `position_sizer.py:298-327`;
  measured `position_sizer.live_margin_used` = 0 and `live_margin_fallback` = 0 across all
  23 retained logs (the branch logs on BOTH outcomes, so 0 lines ⇒ 0 executions).
- **CLASS:** Reachability (built-never-run — the IA-P2-01/EntryGate class, sizing edition) /
  Config-vs-code.
- **NEW or KNOWN:** NEW (the July audits treat FIX-072 as an active mechanism; the adapter
  side — `get_live_margin_pct`, TTL cache, 16388 invalidation — is real and reachable only
  from order_placer's cache-invalidation path).
- **ROOT CAUSE:** the fix wired the adapter into `order_placer` (which got `broker_adapter`)
  but the sizer construction predates it and was never revisited; the YAML knobs were added
  to config without adding ctor pass-throughs.
- **RECOMMENDATION (described):** decide which margin source sizing SHOULD use (static map
  is arguably the safer, deterministic choice — but then delete the dead branch/knobs or
  mark them inert); if live margin is wanted, wiring it changes qty_by_capital only (never
  binding today) — low behavioural risk but careful-loop by policy.
- **SEVERITY-BY-IMPACT:** LOW-MED — today's behaviour is consistent and deterministic; the
  hazard is the false belief (docs/FIX list) that live margins already protect sizing, plus
  two YAML knobs that silently do nothing if ever edited.

---
**IA-P3-03**
- **WHAT:** The performance-weight channel is connected to nothing: `dynamic_by_winrate:
  true` and the boot banner ("tier weights … x perf_weights") advertise performance-weighted
  sizing, but no production code ever populates `SignalProcessor._perf_weights` — it is `{}`
  for the life of every session, so `perf_weight = 1.0` on every sizing call ever made.
- **EVIDENCE:** the only writer is the constructor param (`signal_processor.py:198`);
  repo-wide grep for `perf_weights=`/`set_perf_weights`: production callers = **none**
  (tests only, and `test_q9_sizing_floors_caps_wired.py:871` ASSERTS the production value
  is `{}`); measured `trades.perf_weight_applied` distinct values all-time = **[1.0]**
  (415 rows). Config: `dynamic_by_winrate: true` + `min_multiplier 0.5` / `max_multiplier
  2.0` (system_config.yaml:182-184) — three knobs governing a multiplier that never varies.
- **CLASS:** Config-vs-code / Reachability.
- **NEW or KNOWN:** the emptiness is KNOWN to the test layer (Q9 pinned it) and the M-C6
  comment ("performance_allocator clamps min_weight=0.5" — describing a wiring that does
  not exist); **NEW is the finding-level statement**: FIX-132 Item 9 + FIX-133 Item 21
  shipped plumbing whose SOURCE (a PerformanceAllocator feeding weights at boot or runtime)
  was never built — decision #06 (PerfAllocator) is still open, and until it lands these
  config knobs are dead.
- **ROOT CAUSE:** the multiplier plumbing and its data source were split across work items;
  the source half never shipped, and `dynamic_by_winrate: true` reads as if it did.
- **RECOMMENDATION (described):** fold into decision #06 — either build the source (Rama's
  open decision, D2/D3-gated) or set `dynamic_by_winrate: false` to make config truthful.
  ⛔ Neither done here.
- **SEVERITY-BY-IMPACT:** LOW-MED — no wrong number (1.0 is neutral); the cost is a config
  surface that misdescribes live behaviour, in the money path's sizing formula.

---
**IA-P3-04**
- **WHAT:** Third instance of the "config-legal ranges make a documented constraint
  unreachable" class: the risk-per-trade path (PS2's headline formula) cannot bind for any
  legal strategy config — `qty_by_risk/qty_by_concentration = 0.1/sl_pct ≥ 2` for every
  sl_pct the schema admits under `sl_max_pct ≤ 0.05` (measured floor of the ratio: 5.0) —
  joining IA-P2-03 (tier HIGH ≥ 80 vs ceiling 65) and the v3-medium/spread case (P2.2). No
  startup or test invariant checks that configured constraints are reachable given the
  other knobs' ranges.
- **EVIDENCE:** algebra + measurement in P3.2(a); the three knobs live in three files
  (risk_per_trade_pct system_config:166 · max_concentration_pct :167 · sl_max_pct
  strategy schema/YAMLs) with no cross-validation (config_loader validates each in
  isolation; grep width: no validator references two of them together).
- **CLASS:** Invariant coverage (G-lens) / Architecture.
- **NEW or KNOWN:** the dead-sizer FACT is KNOWN (P2-carry, board); NEW is (i) the
  upgrade from empirical to structural (algebraic unreachability), and (ii) the named
  CLASS with its three instances.
- **ROOT CAUSE:** per-knob validation without cross-knob reachability checks.
- **RECOMMENDATION (described):** one boot-time (or test-time) reachability assertion per
  documented constraint — "there exists a config-legal input for which this constraint
  binds" — would have caught all three instances. ⛔ Not built.
- **SEVERITY-BY-IMPACT:** MED as a class (each instance silently retires a documented
  protection or intent; the next knob edit can create a fourth instance unnoticed).

---
**IA-P3-05**
- **WHAT:** The F1 sector-cap observe soak is structurally EVENTLESS: the flip condition
  ("observe soak ≥1 session → evidence → Rama-gated enforce") assumed would-reject events
  would accrue, but at current sizing the cap cannot be approached — so the soak has
  produced, and can produce, zero evidence, while the flip decision waits on it.
- **EVIDENCE:** threshold arithmetic P3.2(b)(3): whole-book margin ceiling ≈ ₹460 ≈ 4.7% of
  capital vs the 40% trigger — an ~8× gap that `max_open_positions=5` makes unbridgeable;
  measured `sector_cap_would_reject` = **0 lines all-time** (23 logs, gate live since
  16-Jul); `risk_engine.sector_toctou_degraded` = 0 (the hardened read runs).
- **CLASS:** Reachability / Decision-process (a soak that cannot discriminate).
- **NEW or KNOWN:** F1 and G3 are KNOWN; NEW is the quantified vacuousness of the soak —
  the same shape as IA-P2-05's shadow-gate leg (an observation channel whose event rate is
  structurally zero).
- **ROOT CAUSE:** the cap % was chosen for a larger book; nobody re-derived the trigger's
  reachability at ₹9.9k capital with a 5-position cap.
- **RECOMMENDATION (described):** the enforce-flip decision should be re-framed: at current
  sizing the flip is FREE (it cannot reject anything) and therefore also USELESS — the real
  prerequisites remain sector coverage (G3: 142/2,228 source symbols) and a sizing scale at
  which 40% is reachable. Record that in the F1/R2/D1 decision context; ⛔ nothing flipped.
- **SEVERITY-BY-IMPACT:** MED for decision-hygiene (a gate that cannot fire is soaking
  toward a flip that cannot matter — while reading as "protection being validated").

---
**IA-P3-06** (hygiene bundle, one ID)
- **WHAT:** (a) The engine-side sector resolution is FULLY silent: RE9's WARNING fires only
  on exception/non-string, and `InstrumentCache.sector()` never raises (returns "UNKNOWN")
  — measured 0 warning lines ever; the only loudness is the placer's DQ alert, which is
  one-shot per session AND sample-gated (≥10 inserts) — 2 of 6 window days stayed silent
  (27/30-Jul). (b) Margin is computed at three sites from one function (sizer inline,
  `fm.reserve` ×1.05 buffer, placer row metadata) — agreeing by construction today, but the
  sizer's dead live-margin branch (IA-P3-02) is exactly the code that would have made them
  disagree if ever wired. (c) Schema comment drift: `margin_reserved -- qty * entry * 0.20`
  hardcodes an old 5× assumption in prose. (d) The `_track_sector_dq` counters reset per
  process, so the "one-shot" alert re-fires each session — adequate, but its absence on a
  <10-trade day is indistinguishable from a fixed data source.
- **CLASS:** Consistency / Documentation / Silent-failure (posture notes).
- **NEW or KNOWN:** NEW-trivial; (b) sharpens the E-lens answer (one formula, three
  invocations, no independent recomputation ⇒ sizing and placement CANNOT disagree on
  margin while the branch stays dead).
- **RECOMMENDATION (described):** none urgent; fold (c) into any future schema-comment pass.
- **SEVERITY-BY-IMPACT:** LOW.

### P3.4 KNOWN items re-verified — status updates (no re-numbering)

| Known ID | Status on the current system (fresh evidence) |
|---|---|
| Dead risk-sizer (P2-carry; "₹7 vs ₹99") | **CONFIRMED and now measured tighter: median actual risk ₹4.04 (window) vs intended ₹93.60–99.97 — ~20×; conc binds 415/415 + 67/67; risk-binds 0 ever (ratio floor 5.0×); upgraded from empirical to ALGEBRAIC (IA-P3-04)** |
| **G3** sector resolution | Status moved: **4 real-sector trades now** (PNB 20-Jul; SAIL, JIOFIN 29-Jul; SYNGENE 30-Jul), not 1 — the 29/30-Jul entries post-date the G-register text. Populated split 113 UNKNOWN / 4 real (96.6%); root quantified: `instruments.csv` sector coverage **142/2,228 (6.4%)**, identical PC and VM. The DQ alert works (7 fires; sample-gated). R2/D1 remain blocked on coverage, not on code |
| **G8** unlevered sizing | CONFIRMED — the binding constraint is notional (no leverage term); leverage touches only never-binding/bookkeeping surfaces (P3.2(c)); ⛔ no multiplier computed |
| Sector-cap gate 8 (F1, observe since 16-Jul) | Mode verified `observe` on the deployed tree; would-reject 0 ever; UNKNOWN handling = pooled fail-closed direction (P3.2(b)); the soak is eventless by arithmetic (IA-P3-05) |
| B-1 daily-loss unrealized (shadow) | `would_reject_with_unrealized` = **0 lines ever**; `mtm_unavailable` = 0 — the shadow has logged no event; the flip decision has accumulated no evidence either way |
| Q9 sizing-guard unreachability verdicts | Re-confirmed by absence, all-time widths: `zero_multiplier_skip` 0 · `qty_explosion_guard` 0 · `invalid_sl_distance` 0 · `position_value_cap_exceeded` 0 · `REJECTED_LOT_SKEW` 0 · `sl_direction_warning` 0 (23 logs; plus signals: only `REJECTED_SIZING_CONCENTRATION` has ever occurred among SIZING_*) |
| M-C6 ZERO_MULTIPLIER + FIX-133 2× ceiling (`position_sizer.py:506`, board latent) | Both latent as recorded: effective_mult = tier 0.5 × perf 1.0 = 0.5 always ⇒ the ≤0 branch and the 2× cap are unreachable until a weight source exists (IA-P3-03) |
| Risk-gate reachability census (fresh, all-time signals) | Fired-ever: STRATEGY_CONTROL 17,521 · DAILY_TRADES 5,146 (window 0) · SHADOW_INNING 4,168 (off since 25-Jul) · SIZING_CONCENTRATION 3,340 · CIRCUIT_PROXIMITY 2,408 · STRATEGY_CIRCUIT_BREAKER 1,560 · OPEN_POSITIONS 453 · ENTRY_THROTTLED 300 · STRATEGY_POSITION_LIMIT 275 · DUPLICATE_SYMBOL 107 · **CONSECUTIVE_LOSSES 1 (20-Jul 14:39, COMSYN — the gate is reachable)**. Never-fired (0 rows ever): KILL_SWITCH (+_LATE), CAPITAL, DAILY_LOSS, SECTOR_EXPOSURE, CONTRARY_POSITION, RESERVE_FAILED, every SIZING_* except CONCENTRATION |
| Throttle time-selection (19-Jul census) | Alive at the same seam (post-reserve, releases reservation): 4–27 REJECTED_ENTRY_THROTTLED/day in the window |
| Costs-dominate (43.5% breakeven; size-independent cost_R) | Fresh interaction datapoint, ⛔ no scaling proposed: closed v34 trades (CLOSED+CLOSED_MANUAL, n=181) charges median **₹0.45**, risk_amount median ₹4.68 in this population (the ₹4.04 in P3.2(a) is the open-window slice — a different population); per-trade charges/risk **median 8.9%**, ratio-of-medians 9.6%, sum/sum 7.4% (all three aggregates re-measured 01:4x) ⇒ **≈8–10% of at-risk rupees consumed by charges at current size** (29 zero-charge rows = the known CHECK1 costs=0 class) |
| Funnel (window) | screener PASSED 410 → trades created 67 (16%); the gap decomposes into SIZING_CONCENTRATION 184 · ENTRY_THROTTLED 88 · DUPLICATE_SYMBOL 42 · OPEN_POSITIONS 4 · placement-path remainder |
| approve() volume | 20–49 calls/day in the window, approved=False 2–12/day — the gates are consulted at sized-signal rate (post-P1's census inversion, still true) |

### P3.5 Open questions (not guessed into findings)

- **OQ-P3-1:** Is the ~₹936–1,000 price ceiling (IA-P3-01) INTENDED universe selection?
  Nothing documents it; it emerged from `max_concentration_pct` × small capital. Only Rama
  can ratify it as intent or queue it as a D1 input. (It also bounds every backtest's
  comparability as capital changes.)
- **OQ-P3-2:** What evidence should now gate the F1 enforce flip, given the observe soak is
  structurally eventless (IA-P3-05)? The original "≥1 session soak" cannot produce data.
  Rama/design; blocked-with is G3 coverage.
- **OQ-P3-3 (curiosity):** `REJECTED_DAILY_TRADES` = 5,146 all-time but 0 in the window —
  the histogram implies a pre-throttle era when the daily cap did the throttling. Settle
  (if ever needed) with a by-month slice; no defect implied.

### P3.6 SEAM SUMMARY — can sizing and the order placer disagree about qty/risk

Almost nowhere, by construction. `place()` receives `qty=sizing.qty` VERBATIM
(`signal_processor.py:1233`) plus (entry, SL, TGT, trigger, the sizing breakdown for the
audit row, and the strategy R:R frozen for fill-time TGT recalc); the placer computes no
second quantity — repo-wide, the only qty-shaping after sizing is execution truth
(qty_filled vs qty_planned on partial fills, P4's territory). Margin exists in three figures
that agree by construction today (one static formula: sizer's check, FM's reservation ×1.05
SL-M buffer, placer's row metadata) — the one mechanism that could split them (live broker
margin in the sizer) is dead code (IA-P3-02), and the FIX-075 drift top-up recomputes with
the same function. Risk exists in two figures: `sizing.risk_amount` = `trades.risk_amount`
(same number, persisted), and realized risk can exceed it only through execution-side
slippage (the entry-slippage budget guards it — P4). So the P3→P4 seam carries: an exact
qty, a frozen (entry, SL, TGT) basis, a margin already reserved (+5% buffer the placer
does not know about), and an audit breakdown. Where P4 starts: whether execution preserves
that basis — fill-price slippage vs the sized SL distance, partial-fill handling of
qty_planned/qty_filled, the drift top-up path, and the placement-failure statuses that
release the reservation.

**Phase 3 done** = findings above; the dead-sizer gap re-measured fresh (conc 415/415 and
67/67; actual ₹4.63 mean vs intended ~₹95; upgraded to algebraic unreachability); the sector
UNKNOWN handling determined definitively (pooled, fail-closed direction — inert by mode ×
input × arithmetic); unlevered sizing confirmed and the leverage surface mapped with no
multiplier computed; every sizing %-knob units-checked (no new mismatch, width stated);
nothing fixed; nothing pushed; the 3-Aug/4-Aug sequence untouched.
*(Phase 4 — order construction/execution — appends below this line.)*

---

## PHASE 4 — RISK → ORDER CONSTRUCTION / PLACEMENT (placer + protocols + exit engines + GTT, and the P4→P5 seam)

### P4.0 Measurement window & system state

| | |
|---|---|
| Session window | **Sat 01-Aug-2026 ~01:5x → ~03:0x IST** |
| Measurements taken | 01-Aug **~02:1x–02:4x IST**, from the VM (`mode=ro` DB reads + log greps; zero writes) |
| Deployed SHA (VM bare) | **`297b587`** — unchanged since P1 |
| PC tree read | `413c957` = `297b587` + 5 docs-only commits ⇒ code read == deployed |
| Service | `inactive` (designed nightly state) |
| Primary windows | trades all-time **478** / orders all-time **805** / 24→31-Jul window (67 trades); logs = **23 retained** `system_*.log`; T2 artifacts read-only |
| Scope guard | 3-Aug/4-Aug untouched; nothing fixed/tuned; no live order or GTT touched; July audits read-only |

Deployed-tree config ground truth (VM grep == PC): `entry_gate`: `slippage_buffer 2.0`(₹) ·
`max_entry_slippage_pct 1.0`(%) · `max_spread_pct 0.5` + `min_depth_qty 500` +
`liquidity_check_enabled true` · `min_effective_rr 1.0` · `min_pending_rr 1.0` ·
`circuit_proximity_reject_enabled true` · `slippage_control {enabled, mode sl_fraction,
max_slippage_fraction 0.22, absolute_cap_rs 5, hard_max_slippage_rs 10, also_apply_pct_check
true, overrides all empty}` · `capital`: `sl_limit_offset_pct 0.005` · `gtt_sl_limit_offset_pct
0.03` · `emergency_exit_buffer_pct 0.01` · `order_monitor`: poll 2s / `fill_timeout_sec 60` ·
`smart_tgt {enabled true, trigger_pct 0.005, step_pct 0.003}` · `tgt_retry {enabled, 30s ×5,
backoff ×2}` · `structure_exit_enabled false` · `delivery_enabled false` +
`force_intraday_only true` · strategy YAMLs: `order_protocol` = 12/15 CO_PLUS_TGT + 3
LIMIT_TRIPLE; `tgt_risk_reward 1.5` on all 15.

### P4.1 Path as verified (current tree == deployed)

One placement pipeline, `OrderPlacer.place()` (`order_placer.py:864`): FIX-025 gate-release
buffer (**dead branch** — `release_ltp` arrives only from the never-fed EntryGate, IA-P2-01;
measured `slippage_protection` lines = 0 ever) → FIX-136 R:R gate (**armed**,
`min_effective_rr 1.0`, :927-947) → **OP9 `order_protocol = self._default_protocol`
:949-950 — unconditional** → trade row (risk_amount/margin/sector/sizing-audit, :985-1004) →
link (hard-fail, :1016-1026) → OP-LM1 kill check :1039 → PENDING → FIX-073 EOD-cutoff :1065 →
FIX-128/Phase-3a slippage guard :1092-1181 (`sl_fraction`: tolerance = min(0.22 × SL-distance,
₹5), hard ₹10, plus the 1% flat check; **fail-open when no LTP**) → FIX-075 drift top-up
:1183-1278 (threshold 0.005; **fail-open on quote failure**) → FIX-134 liquidity check
:1282-1294 (**never runs — IA-P4-01**) → A-3 kill re-check INSIDE the BL-19 429-retry loop
:1324 + FIX-072 16388 retry :1348-1391 → `FullEntryEngine.execute` (string-routed, unknown →
ValueError, `full_entry_engine.py:93-103`) → `LimitTripleProtocol.execute` = **ENTRY LIMIT
only** (:167-241; SNR-V2 MARKET variant dormant) → the adapter chokepoint
(`zerodha_adapter.place_order:478`): ZA13 validation (**`qty > 0` and nothing above it**,
:2028-2031) → authoritative fail-safe tick-snap (:532, :1361-1415; cache wired `main.py:2140`)
→ **Option-A intent coercion** :543-549 → ProductResolver :552 → **CNC master lock** :558-567 →
`kite.place_order` :604-615 (`exchange="NSE"` hardcoded; **no `validity` argument — DAY is the
implicit broker default**; tag truncated at the chokepoint) → atomic persist (OP-BL8) → track →
ORDER PLACED alert :1731-1752. Fill side: OrderMonitor (poll 2s; `fill_timeout_sec 60` →
zero-fill terminal → release+FAILED :1950-1985; FIX-141 pending-R:R cancel
`order_monitor.py:1143-1198`) → OrderFilled → TGT recalc from the ACTUAL fill with the FROZEN
strategy R:R (:2861-2868; fallback 2.0+WARN — latent) → `place_deferred_exits` (:2886): **the
intent gate** — `intent == "DELIVERY"` + placer wired → **ONE two-leg OCO GTT**
(`full_entry_engine.py:151-177` → `cnc_gtt.py:94-157`: triggers tick-snapped, C8
straddle + 0.25% distance validation, durable `gtt_state` persist best-effort) — else
LIMIT_TRIPLE `place_exits` (`order_protocol_limit.py:263-495`): circuit-band clamp gate with
the NOCIL polarity check (:297-360) → **SL FIRST**, stop-limit `"SL"` with limit =
trigger ∓ 0.5% (:362-419) → TGT LIMIT (:458-495); TGT-unplaceable/rejected → SL-only partial +
TGTRetryManager (FIX-190 Bug C). Exit-failure ladder (:2898-2934): LTP-validation error →
retry queue (FIX-061); anything else → **emergency marketable-LIMIT exit (LTP ∓ 1%, FIX-181)
+ HARD_KILL**; SL-unplaceable → `SLUnplaceableError` → same ladder. After-check:
`record_exits_verification` per trade (Slice 1 Part B).

### P4.2 Headline re-measurements (mandated)

**(a) order_protocol / LIMIT_TRIPLE / the exit engine — re-measured fresh: all three verdicts
HOLD, at current line numbers and in current data.** The 5-Jul adjudication chain re-verified
link-by-link on the deployed tree: `place()` still has no protocol parameter (:864-882); OP9
assigns `self._default_protocol` unconditionally (:949-950); main.py constructs OrderPlacer
**without** `default_order_protocol` (:2639-2665) ⇒ the ctor default `"LIMIT_TRIPLE"` (:570)
decides every trade. Measured: `trades.order_protocol` = **LIMIT_TRIPLE on 478/478 all-time
and 67/67 in the window** — the YAML `order_protocol` field (12/15 declare CO_PLUS_TGT)
remains dead config, and the CO surface remains dormant AND activation-unsafe (`modify_order`
still hardcodes `variety="regular"` :1093; the CO entry is `order_type="SL"`
`order_protocol_co.py:121`, never live-validated). The exit engines, fresh: **`smart_tgt_state`
0 rows ever · `trades.sl_trail_count>0` on 0/478 · StructureExitManager flag-off ·
BreakevenManager not constructed (no reference in main.py, repo grep)** — X6's
three-dark-engines verdict CONFIRMED, **plus a fourth dark module found (IA-P4-01b:
`sl_breach_monitor`)**. SmartTgtManager is constructed EVERY boot with hardcoded
`enabled=True` (`main.py:2557`; 26 boot lines / 23 logs, 0 action lines) and its registration
gate requires `order_protocol == "CO_PLUS_TGT"` (:2193-2197) ⇒ **structurally unreachable
under the forced LIMIT_TRIPLE** — running-but-starved. **Every ORDER PLACED Telegram still
claims "Smart TGT monitoring: ACTIVE (FIXED mode)"** (`smart_on` keys on global config only,
:1735-1743, and yaml `smart_tgt.enabled: true`) — the 5-Jul [HIGH] alert-truth divergence is
live today.

**(b) The order-size cap — DETERMINED from source: NO upper bound exists between sizing and
the broker.** ZA13 `_validate_place_order` checks `qty > 0` (positive integer) and nothing
above it (:2028-2031) — the July-audit UNCERTAIN ("qty>0 verified by ZA13 docstring only") is
settled by source read: the docstring was accurate, and the check is exactly that. Grep widths:
`max_single_order_qty` = **0 references in orders/ + broker/**; no notional cap at placement
(the 40% value cap is sizer-internal). The ONLY ceiling anywhere is the sizer's internal guard
at its **code default 10000** — whose YAML twin is dead config (IA-P3-02). Measured exposure
context: **max qty ever submitted = 6 (`orders.qty_requested`), max ever sized = 9
(`trades.qty_planned`)** — three orders of magnitude below the unconfigured default.
Mitigating structure (E-lens): qty flows VERBATIM sizer→placer→protocol→adapter→kite (P3.6:
no second qty computation = no corruption site), and notional is bounded upstream by
CAPITAL/reserve. → IA-P4-02.

**(c) GTT-OCO — reconciled against the T2 live evidence: the construction path IS the deployed
code, live-proven; and the orphan-GTT machinery moved from "never-run" to LIVE-PROVEN — 
exercised by the T2 basket itself.** Construction: `scripts/t2_cnc_gtt_realtest.py` drives
**the deployed `CncGttPlacer.place_for_fill`** (:375/:490; import :581-584) with a throwaway
store (`gtt_state.trade_id` FKs to trades ⇒ the prod store cannot be used — the §4.1 trap,
correct) → `adapter.place_gtt` (`_gtt_legs` :654-661: both legs exit-side LIMIT, product CNC,
`trigger_values [sl, tgt]` ascending, NSE) — the broker accepted **5/5 on 29-Jul, every field
== the `gtt_state` mirror** (T2 record). Service side, fresh: live `gtt_state` **0 rows ever**
+ `cnc_gtt.placed` **0 lines** — correct, no DELIVERY fill has ever occurred in the service
(entry double-locked). **Status moved — the Slice-2.5 record said "FIX-183 prepass never-run";
it RAN, on the T2 GTTs, and behaved exactly as designed:** `cnc_gtt_monitor.forensic`
`unknown_gtt_left_alone` ×**220** (29→31-Jul, the 5 T2 GTTs, "no gtt_state row → treated as
human/external"), **5× WARNING "Orphan GTT — no open delivery trade" at 31-Jul 08:15:27-29 —
SJVN/MSUMI/SOUTHBANK/TRIDENT/IOB, exactly the T2 five, exactly as the Slice-2.5 record
predicted**; the adoption branch untriggered (correct: no open delivery trade in the live DB).
One blemish: `cnc_gtt_adoption: get_gtts failed: ReadTimeout` **1× (08-Jul), ERROR-swallowed,
no alert** (IA-P4-05b). Deletion: service-side GTT deletes exist ONLY in
`cnc_gtt_monitor._safe_delete_gtt` (:344 heal / :497 / :632 wrong-qty; failure → ERROR only)
— **never executed in production**; the T2 GTTs were deleted by the close script's direct
kite calls, not by the service.

**(d) Units sweep (H lens) — every order-construction %-knob checked against its consuming
expression: NO fraction-vs-percent defect found.** Width, 14 knobs: `sl_limit_offset_pct
0.005` → ×(1∓pct) ✓ · `gtt_sl_limit_offset_pct 0.03` ✓ (deep GTT-SL floor; the GTT TGT leg
deliberately reuses 0.005, `main.py:2608`) · `emergency_exit_buffer_pct 0.01` ✓ ·
`price_drift_threshold 0.005` fraction vs fraction ✓ (⚠️ the yaml key is DEAD — not passed;
the code default is coincidentally equal — IA-P4-01 note) · `max_entry_slippage_pct 1.0` =
**percent**, consumed `/100` ✓ (the one percent-unit knob among fraction siblings — internally
consistent, flagged as a naming hazard only) · `slippage_control.max_slippage_fraction 0.22`
fraction-of-SL-distance ✓ · `absolute_cap_rs 5` / `hard_max_slippage_rs 10` / tier table =
rupees ✓ · `entry_gate.slippage_buffer 2.0` = **RUPEES**, name carries no unit (dead FIX-025
path anyway) · `entry_gate.max_spread_pct 0.5` percent (dead — IA-P4-01) ·
`smart_tgt.trigger_pct 0.005` / `step_pct 0.003` fractions ✓ (starved consumer) ·
`_MIN_TRIGGER_DISTANCE_PCT 0.0025` fraction ✓ · `DEFAULT_CIRCUIT_MARGIN_PCT 0.02`
fraction-named-PCT (P3-recorded naming hazard, consistent) · tick 0.05 rupees ✓. The units
discipline held at this layer; what the sweep surfaced is dead KNOBS, not wrong units.

### P4.3 NEW findings

---
**IA-P4-01**
- **WHAT:** FIX-134 shipped dark — BOTH halves. **(a)** The pre-entry liquidity check is
  configured ON and has never run: `entry_gate.liquidity_check_enabled: true`
  (system_config.yaml:556, with `max_spread_pct 0.5` :554 and `min_depth_qty 500` :555), and
  the schema even defaults it True (`config_loader.py:1523-1524`) — but main.py passes NONE of
  the three liquidity args to OrderPlacer (:2639-2665), so the ctor default **False** rules
  (`order_placer.py:587`) and `_check_liquidity` returns before any work (:4053-4055).
  **(b)** `orders/sl_breach_monitor.py` (FIX-134 Item 39 — the tick-level BACKUP SL monitor
  that fires an emergency exit when the broker SL is missing AND LTP breaches the stop) has
  **no importer anywhere in the repo**; its own docstring claims "wired into the tick
  dispatcher in main.py" — no such wiring exists. Had it been wired, the dormant tick feed
  would starve it anyway — two independent disablers, the X6 shape. Sibling note: the
  `price_drift_threshold` yaml key (:235) is also not passed — the code default 0.005 is
  coincidentally equal (the IA-P3-02 "coincide" pattern).
- **EVIDENCE:** grep widths — `liquidity_check_enabled`/`liquidity_max_spread`/`min_depth_qty`
  consumers = placer ctor + config schema + tests only (repo-wide, *.py);
  `sl_breach_monitor` importers outside the module = **0** (repo-wide, tests excluded).
  Measured: `insufficient_liquidity` + ANY `liquidity` log line = **0 across all 23 retained
  logs**; `sl_breach` lines = **0 ever**.
- **CLASS:** Config-vs-code / Reachability / Silent-failure.
- **NEW or KNOWN:** NEW (the FIX list and config read as delivered protections; the yaml says
  `true`).
- **ROOT CAUSE:** identical to IA-P3-02 — config keys and module shipped without the
  constructor pass-through / construction site; instances 4 and 5 of the IA-P3-04
  "no reachability assertion" class.
- **RECOMMENDATION (described, ⛔ not applied):** decide wire-or-delete per half. The
  liquidity check is a 3-arg ctor plumb; the SL monitor is tick-fed and therefore ⛔ GATED
  BEHIND the dormant-feed decision (do not reopen it for this). Until decided, set the yaml
  key false so config tells the truth.
- **SEVERITY-BY-IMPACT:** MED — two documented pre-trade/position protections silently absent
  while config claims one of them ON; the belief hazard, not a wrong number.

---
**IA-P4-02**
- **WHAT:** The B-lens determination: no order-size ceiling exists between the sizer and the
  broker — a defective qty entering `place()` would travel to Kite unchecked. With the
  sizer's `max_single_order_qty` yaml dead (IA-P3-02), the effective ceiling for the entire
  money path is one unconfigured code default (10000) inside the sizer.
- **EVIDENCE:** P4.2(b): ZA13 = `qty > 0` only (:2028-2031); `max_single_order_qty` 0 refs in
  orders/+broker/; measured max ever = 6 requested / 9 sized.
- **CLASS:** Safety / Invariant coverage.
- **NEW or KNOWN:** NEW (sharpens IA-P3-02's consequence to the placement layer; settles the
  5-Jul UNCERTAIN).
- **ROOT CAUSE:** the guard was placed in the sizer only; its config wire then broke, and
  nothing downstream re-checks.
- **RECOMMENDATION (described):** if a ceiling is wanted at the money chokepoint, ZA13 is the
  single site (one comparison against a config value); pairs with IA-P3-04's
  reachability-assertion idea. ⛔ Not built.
- **SEVERITY-BY-IMPACT:** LOW-MED — latent defense-in-depth gap; today's exposure is bounded
  by upstream capital math (~6-9 shares), but the shape is single-point-of-failure.

---
**IA-P4-03**
- **WHAT:** Order validity is never constructed: `kite.place_order` is called with no
  `validity` argument (:604-615), no config key exists, and the orders table has no validity
  column — every order ever placed relied on Zerodha's implicit DAY default.
- **EVIDENCE:** the kite call site (verified complete argument list); orders pragma (no such
  column); repo grep `validity` in orders/+broker/ = no order-construction consumer.
- **CLASS:** Correctness-by-default / Documentation.
- **NEW or KNOWN:** NEW-trivial.
- **ROOT CAUSE:** DAY-only was always the intent; never stated anywhere.
- **RECOMMENDATION (described):** record it as a one-line contract (optionally pass
  `validity="DAY"` explicitly). ⛔ Not done.
- **SEVERITY-BY-IMPACT:** LOW — correct today by broker contract.

---
**IA-P4-04**
- **WHAT:** `orders.qty_filled` is written by nothing: **0 of 805 rows** carry qty_filled > 0
  — including all 405 COMPLETE orders and the 145 real entry fills. Fill truth lives on
  `trades.qty_filled` (max 6) + events. Anyone auditing fills from the orders table concludes
  nothing ever filled.
- **EVIDENCE:** measured (A7o); writer sweep: the only `UPDATE orders` sites set
  status/updated_at (`state_store.py:1030` day-rollover cancel) and reconciliation_status
  (:3054) — neither touches qty_filled. (Reader sweep not exhaustive; the WRITER absence is
  the measured fact.)
- **CLASS:** Consistency / Documentation (schema promises data it never receives).
- **NEW or KNOWN:** NEW.
- **ROOT CAUSE:** fill accounting was built on trades+events; the orders column predates it
  and was never wired or removed.
- **RECOMMENDATION (described):** document the column dead in schema.sql, or drop it
  (schema change ⇒ careful-loop). ⛔ Neither done.
- **SEVERITY-BY-IMPACT:** LOW — misleads audits/tools; no runtime consumer found.

---
**IA-P4-05** (hygiene bundle, one ID)
- **WHAT:** (a) **R:R fallback boundary**: `tgt_risk_reward_applied` = {**2.0 ×36 rows,
  23→24-Jun** · **1.5 ×365 rows, 24-Jun→31-Jul**; window 1.5 on 67/67} — the 2.0 era is
  exactly pre-Slice-1 (the placer's ctor default `rr_ratio=2.0`); the fallback (2.0 + WARN,
  `_resolve_fill_rr`) is LATENT today and DISAGREES with the universal strategy 1.5 — a loud
  but wrong number if a future strategy omits R:R. (b) the `cnc_gtt_adoption` boot sweep
  failure is ERROR-swallowed (1× 08-Jul ReadTimeout; no alert; one blind morning per
  failure). (c) `_safe_delete_gtt` swallows to ERROR — a failed delete would leave a live GTT
  standing with only a log line (never yet executed). (d) `snap_to_tick.missing_tick_size`
  WARN fired **8×** (SEIL, LIQUID, …) — the FIX-170 fail-safe works; those instruments were
  absent from the cache (the G6-adjacent ETF/token cluster). (e) OP-NS4 docstring drift
  (5-Jul finding): **CLOSED 22-Jul** — a SUPERSEDED note was added doc-only, original left
  legible (:131-137).
- **CLASS:** Silent-failure posture / Documentation. **SEVERITY:** LOW.

### P4.4 KNOWN items re-verified — status updates (no re-numbering)

| Known ID | Status on the current system (fresh evidence) |
|---|---|
| X6 / 5-Jul protocol adjudication (LIMIT_TRIPLE forced; 3 exit engines dark) | **CONFIRMED fresh, every link, current lines** (P4.2(a)); a FOURTH dark exit-safety module added (IA-P4-01b) |
| 5-Jul [HIGH] "loaded config gun" (CO not activation-safe) | Unchanged: `modify_order` hardcodes `variety="regular"` (:1093); CO entry `order_type="SL"` (`order_protocol_co.py:121`); 12/15 YAMLs still declare CO_PLUS_TGT |
| 5-Jul [HIGH] alert-truth divergence (Smart TGT claimed on every alert) | **LIVE today** — `smart_on` = global-config-true (:1735-1743; yaml `smart_tgt.enabled: true`) |
| FIX-141 pending-R:R cancel (`min_pending_rr 1.0`) | **STATUS UPGRADE → VERIFIED LIVE: `pending_rr_cancel` 15 · `pending_rr_cancelled` 15 · `_failed` 0** (23 logs) — an armed, working order-construction guard |
| FIX-128 + Phase-3a slippage guard | Alive and binding: `entry_slippage_observed` **305 == place_start 305 (1:1)**; `slippage_guard_exceeded` **45 all-time, 11 in window == the window's 11 REJECTED trades exactly** (per-day 1/1/2/1/2/4); the 2×ERROR-per-rejection double-log (30-Jul note) stands |
| FIX-075 drift top-up | Fired **once ever** in retained logs (detected 1 · top-up 1 · rejected 0) |
| FIX-072 16388 margin retry | **Never fired** — 0 real occurrences (the 3 grep hits are signal-ID substring false positives) |
| BL-19 429 placer retry · FIX-068 UNKNOWN_IN_FLIGHT | 0 · 0 ever — both recovery paths unexercised (trades UNKNOWN% = 0 rows) |
| NOCIL clamp gate + FIX-190 Bug C | 6 benign band-clamps; `sl_unplaceable` 0 · `tgt_unplaceable` 0 · SL-only partials 0 · `skipped_unplaceable` 0 ⇒ TGTRetryManager constructed-idle (26 boot lines, 0 retries ever) |
| FIX-148/181 emergency exit + hard-kill ladder | **0 executions ever** — width: the 17.5k raw "emergency" log matches are all strategy_control breaker TEXT; no order_placer emergency marker exists in any retained log. Consistent with HARD_KILL-never-fired (Q4 record) |
| FIX-017 zero-fill / 60s fill timeout | Working: `entry_cancelled_zero_fill` 96 all-time; ENTRY terminal = CANCELLED 160/805; orders statuses = **{COMPLETE 405, CANCELLED 400} ONLY** — `orders.status` never REJECTED (memory rule re-confirmed in data) |
| P0 SL-M removal (OPL7) | In DATA: leg×type = ENTRY LIMIT 365 · SL "SL" 213 · TGT LIMIT 202 · EOD LIMIT 25; **SL-M 0 · MARKET 0**; variety regular 805/805 |
| Option-A coercion + SLICE2.5 CNC master lock | 0 coercions · 0 CNC refusals ever (nothing non-INTRADAY survives strategy control — both locks are unexercised backstops); product MIS 802 / CNC 3 (the 3 = pre-Option-A CANCELLED entries) |
| M-C4 / M-C8 (lock held through Telegram send; retry starves fill thread) | Placement touches the pair at the ORDER PLACED / slippage alerts (:1745, :1163) — noted per brief, ⛔ NOT re-opened (careful-loop, gated) |
| F5 / G9 (broker MIS-block class + learned blocklist) | The 400-handler records into the shared `mis_blocklist` (main.py:2633-2637); P1's measured PLACEMENT_FAILED rows carry broker text verbatim; signals PLACEMENT_FAILED = 113 all-time |
| `gtt_state` trap (MASTER_PENDING §4.1) | Re-confirmed: live `gtt_state` 0 rows AND that is correct (the T2 store was throwaway) |
| check1_mid_fill_defer_sec | Still 0.0 = OFF on the deployed tree (KNOWN; paper cannot exercise it) |

### P4.5 Open questions (not guessed into findings)

- **OQ-P4-1:** Is implicit DAY validity a deliberate contract (IA-P4-03)? One-line locked
  decision settles it.
- **OQ-P4-2:** FIX-134 wire-or-delete, per half (IA-P4-01) — Rama's; the SL-monitor half is
  tick-feed-gated (⛔ the dormant-feed reopen trigger governs, not this audit).
- **OQ-P4-3 (accounting curiosity):** place_start 305 − place_complete 241 = 64 aborted
  placements; measured aborts: slippage 45 · EOD-cutoff 0 · kill last-mile 0 · link 0 ·
  empty-broker-id 0 · liquidity 0 · drift 0 · 16388 0 ⇒ residual **19 = broker-side rejects
  raised by the engine** (the F5 class — PYRAMID/ASAHISONG/GALLANTT examples). Settle exactly
  (if ever needed) by sweeping `_handle_placement_failure` reasons; no defect implied.

### P4.6 SEAM SUMMARY — can the placer and the broker adapter disagree about the order

Three fields are ADAPTER-owned and can legitimately differ from what the placer asked — all
by design, all logged: (1) **product** — the placer sends INTENT only; the adapter coerces
intent under `force_intraday_only`, resolves the product code, and enforces the CNC master
lock (:543-567). The placer separately derives product for the DB rows via the SAME
ProductResolver, so rows and broker agree by construction. (2) **price/trigger** — the
adapter's authoritative fail-safe tick-snap (:532) may move any price the protocol computed
(protocol-side rounding pre-aligns SL/GTT limits; the entry LIMIT relies on the snap alone).
(3) **tag** — truncated at the chokepoint. One field NOBODY owns: **validity** (IA-P4-03).
Everything else crosses VERBATIM: qty (checked only `> 0` — IA-P4-02), side, order_type;
exchange is fixed NSE. Reverse direction: `PlacedOrder` echoes what was SENT (post-snap
price, resolved product), and an empty broker_order_id is a hard failure (OP-LM3) — no silent
divergence path found in P4 scope. What P4 hands P5: the adapter internals behind this seam —
rate limiter, order state machine, OrderMonitor polling truth (fill_timeout, FIX-141),
paper-parity (the 14/19 constant-branch class), the reconciler CHECK battery + external-close
classification — plus two measured zeros worth re-proving there (the 429 path and the
UNKNOWN_IN_FLIGHT recovery, both never exercised).

**Phase 4 done** = findings above; the exit-engine / order_protocol / LIMIT_TRIPLE state
re-measured fresh (still dead config · still forced 478/478 + 67/67 · still never fires, with
a fourth dark module found); the order-size cap DETERMINED (none exists placer→broker; ZA13 =
qty>0 only); the GTT-OCO construction+cancel path reconciled against T2 (deployed code
live-proven; prepass status moved to live-proven; service deletes never executed); 14
order-construction knobs units-checked (no new mismatch — the yield was dead knobs);
committed incrementally (`f0ea74f` + this commit); ⛔ nothing fixed, nothing pushed, the
3-Aug/4-Aug sequence untouched.
*(Phase 5 — broker adapter / execution truth — appends below this line.)*

---

## PHASE 5 — ORDER → BROKER / EXECUTION TRUTH (adapter + fill ingestion + intraday reconcile, and the P5→P6 seam)

### P5.0 Measurement window & system state

| | |
|---|---|
| Session window | **Sat 01-Aug-2026 ~08:34 → ~09:1x IST** |
| Measurements taken | 01-Aug **~08:54–09:0x IST**, from the VM (`mode=ro` DB reads + log greps; zero writes) |
| Deployed SHA (VM bare) | **`297b587`** — unchanged since P1 (re-verified `git -C ~/trading-system.git rev-parse`) |
| PC tree read | `fa7c45c` = `297b587` + 7 docs-only commits ⇒ code read == deployed |
| Service | `inactive` (designed weekend state) |
| Primary windows | orders all-time **805** ({COMPLETE 405, CANCELLED 400}) / trades all-time **478** / reconciliation_log all-time **7,804** rows / logs = **23 retained** `system_*.log` (≈10-Jul→31-Jul) |
| Config ground truth (VM grep == PC) | `broker_limits.yaml`: order **8/8** · quote 3/3 · historical 2/2 · margins **8/8** · `timeouts {connect_sec 5, read_sec 10}` · `backoff_sequence_sec [1,5,30]` · `rate_limit_backoff {0.2s, ×2, cap 5.0s, jitter 0.05, max_placer_retries 3}` — `order_monitor {poll 2s, fill_timeout 60s}` · `circuit_breaker {partial_fill_timeout 5m, force_close "15:15", max_api_failures 3}` · `order_reconciler {poll 15s, drift tol ₹50 / 10% in-session, human_order_margin_tolerance ₹5,000, check1_mid_fill_defer_sec **0.0 = OFF**}` · `entry_gate.min_pending_rr 1.0` |
| Scope guard | 3-Aug/4-Aug untouched; nothing fixed/tuned; no live order, GTT or cancel issued; July audits read-only |

### P5.1 Path as verified (current tree == deployed) — HOW THE SYSTEM LEARNS WHAT FILLED

**Fill detection is polling, and only polling.** Repo-wide width: `on_order_update`/postback
consumers = **0** (one comment mention, `state_store.py:2294`). The mechanism:
`OrderMonitor` (`broker/order_monitor.py`) runs ONE daemon thread, every **2s** snapshots its
`_watched` dict (keyed `(broker_order_id, symbol, date)` — FIX-086) and calls
`adapter.get_order_history(id)` per order (rides the **"order" rate bucket**, ZA3 map
`zerodha_adapter.py:238-253`). Status = `history[-1]` mapped via OM5 sets (:84-88) →
`_handle_open` (fill-timeout 60s + FIX-141 pending-R:R, ENTRY legs only; SL/TGT/EOD exempt) ·
`_handle_partial` (FIX-130: ENTRY cancelled on first PARTIAL) · `_handle_complete` → OSM
transition → **`OrderFilled`** (the authoritative fill event: qty/avg from the poll) ·
terminal → `OrderStatusChanged` (+ `OrderPartiallyTerminated` first when `entry.filled_qty>0`,
FIX-028). Consumers of fill truth: `order_placer._on_order_filled` → `commit_to_used` +
`record_entry_fill` (trades.qty_filled/entry_actual_price/status=OPEN, `order_manager.py:393`)
→ exits placed; `_on_order_status_changed` zero-fill → `release` + trade FAILED
(`order_placer.py:1950-2004`); **`OrderManager` (OMgr10, constructed with `bus=event_bus`,
main.py:2624-2628) persists every `OrderStatusChanged` into the orders row via
`update_order_status` — including `qty_filled`/`avg_fill_price` unconditionally
(`order_manager.py:726-752`)**. Behind the monitor: the 15s `OrderReconciler` cycle
(prepasses: GTT-adoption, A-1/E-1 in-flight-entry recovery by broker **tag** via
`get_all_orders()`; then CHECK1 MANUAL_CLOSE w/ closure classifier · CHECK2
inflight/oversell/HUMAN_ORDER · CHECK4/5 qty deltas · CHECK6 orphan orders · CHECK9 missing
exits · G5b recovery SL · duplicate-exit net · G3/CHECK7/CHECK8). Escalation wiring: monitor
auth×3 or api×3 → `on_critical` = HARD_KILL callback + self-stop (`is_alive()` exposed to
/health, E-4); cancel-fail orphan → `_make_orphan_cb` = **soft_kill + CRITICAL Telegram**
(main.py:803-826). Rate limiter: token buckets, `max_wait_sec` 30 (ctor default, main.py:1966
passes none), 429 → `penalize` freeze ≤5s + typed raise; **adapter never retries (ZA11)** —
BL-19 in the placer retries 429 only (≤3), timeout → **UNKNOWN_IN_FLIGHT** + in-memory
recovery queue (FIX-068, `order_placer.py:1422-1454`), rejection → single attempt.

### P5.2 Headline re-measurements (mandated)

**(a) IA-P4-04 follow-through — RE-MEASURED, and the P4 root cause is CORRECTED: `orders.qty_filled`
is not "written by nothing"; it is written by a LIVE writer on every fill, with a STALE payload.**
Fresh, all-time: COMPLETE rows with `qty_filled>0` = **0/405** · with `avg_fill_price` NOT NULL =
**0/405** · with `filled_at` NOT NULL = **405/405**. `filled_at` is set in the SAME
`update_order_status` UPDATE (`order_manager.py:740-752`, `filled_at` passed only on COMPLETE,
:155) ⇒ the writer provably RAN on all 405 COMPLETE rows and in that same statement wrote
`qty_filled=0, avg_fill_price=NULL`. Mechanism: `OrderStatusChanged` is published by
`_safe_transition` with `qty_filled = entry.filled_qty` / `avg_fill_price = entry.avg_fill_price or
None` (`order_monitor.py:1310-1314`) — and `_handle_complete` computes `final_qty`/`final_price`
but **never writes them back into `entry` before transitioning** (:985-988), so for every
straight-to-COMPLETE fill (all 205 ever — no PARTIAL has ever been observed) the event carries the
pre-fill snapshot (0/None). The real fill data travels only in `OrderFilled` — which OrderManager
does not subscribe to. → IA-P5-01. Corrected sub-figure: **entry fills all-time = 205**
(ENTRY∧COMPLETE orders == distinct trade_ids == trades with qty_filled>0, cross-validated 3 ways;
P4's "145 real entry fills" matches neither all-time nor the July window (88) — the 0-of-N
conclusion is unchanged, the denominator is corrected). Consequence: **the orders table carries NO
true fill quantity or price for any leg, ever** — exit-leg fill prices exist locally ONLY in
`trades.exit_price` when our own finalize path wrote them (see (c)).

**(b) The T2 question generalised — CAN the system's fill belief diverge from the broker's?
YES — one measured production instance, one measured proxy-P&L instance, and one latent
silent path; enumerated exhaustively (D lens):**
1. **Measured precedent (June era): AGARIND 16-Jun** — CHECK5 POSITION_GREW "broker qty=2 >
   local qty_filled=1" ×14 cycles (13:27:48→13:31:06) — the system's book UNDER-recorded a live
   position for ~3.5 min. All 14 POSITION_GREW rows ever = this ONE incident. It sits in the
   June storm era (same day: ORPHAN_ADOPTION + a G5b re-place storm — the 2,647 ORPHAN_ADOPTION
   and 915 CRASH_RECOVERY_SL rows, success 11/915, are **100% June-2026**; last-14-days = 0 of
   each) — the RAMCOIND/FIX-181/182 layers were built against exactly this; fresh window shows
   zero recurrence.
2. **Measured current-era (28-Jul): SWIGGY** — CHECK1 finalized the 15:17 EOD exit at
   `exit_price=267.82 (entry_proxy)`, net −0.30 (= charges only): `kite.trades()` had not yet
   surfaced the seconds-old fill, the FIX-148 fallback booked entry-as-exit, and the monitor's
   real fill price arrived moments later only to be DISCARDED by the double-close guard
   (`order_placer.py:2368-2374`). fm_ledger and trades carry the proxy number; the broker's true
   exit price exists nowhere locally (compounded by (a): orders.avg_fill_price is NULL). Width:
   exit-price resolution all-time = broker_trades **42** / entry_proxy **2**. → IA-P5-03.
3. **Latent silent path — the entry-cancel finalize-from-last-poll race** (the phase's core
   finding): all 4 entry-cancel sites (fill-timeout :1103 · pending-RR :1188 · force-close :827
   · shutdown :550) finalize belief from the LAST pre-cancel poll and never re-read the
   post-cancel state. Zerodha cancels the REMAINDER — a partial fill landing in the poll→cancel
   window stands at the broker while the system releases the reservation and marks the trade
   FAILED (zero-fill path). The resulting position is dispatched by CHECK2 to **HUMAN_ORDER**
   (deliberately unmanaged — FAILED is outside both the inflight statuses :915-917 and
   `_RECOVERY_STATES` :3734) ⇒ un-SL'd, invisible to daily-loss, P&L never booked; the EOD
   residual sweep WILL flatten it (a today-trade row exists ⇒ "system-owned",
   `eod_squareoff.py:1449-1481`) but books nothing. The full-fill variant makes the cancel FAIL
   → **loud** (orphan CRITICAL + soft_kill, main.py:808-826). Exposure to date: 96
   `entry_cancelled_zero_fill` + 15 `pending_rr_cancelled` = **111 windows, 0 hits** (0
   `orphan_detected` ever in retained logs; July HUMAN_ORDER rows = exactly the T2 ten; EOD
   residual flattens = 0 ever). → IA-P5-02.
4. **Latent, crash-shaped:** UNKNOWN_IN_FLIGHT recovery is not restart-durable (queue
   in-memory; the crash feed selects `status='PENDING'` only, `state_store.py:1012`; CHECK2's
   dispatch omits the status) → IA-P5-04. And CHECK6 declares a PENDING_FILL order "orphaned"
   by its ABSENCE FROM `get_open_orders()` — a COMPLETE (filled) order is also absent ⇒
   FAILED+release after 3 cycles if the monitor is dead; bounded because every monitor-death
   path also fires HARD_KILL → flatten-all. 0 CHECK6 rows ever. → IA-P5-09.
5. **Working as designed (fresh evidence):** fill_timeout 80 == timeout_cancelled 80 (100%
   cancel success in window) · FIX-141 15/15/0 (VERIFIED LIVE, P4) · INFLIGHT_ORPHAN benign
   branch fired 13× (reconciler sees the fill before the monitor — correctly no-action) ·
   CHECK1 all-time 44, closure_source on CLOSED_MANUAL = OWN_EOD 25 / OWN_SL 9 / OWN_TGT 5 /
   NULL 6 (the KNOWN six) — **zero genuinely-EXTERNAL closes have ever occurred**; every CHECK1
   close was our own leg racing our own finalize.

**(c) Qty-verbatim / no-cap seam (IA-P4-02 follow-through) — CONFIRMED at the adapter, worst
case traced to the wire.** `_validate_place_order` = symbol/side/type/price/trigger sanity +
`qty > 0` and nothing above (:2028-2046); qty then crosses into `kite.place_order(quantity=qty)`
verbatim (:604-615). No notional or qty ceiling exists anywhere in `broker/` (re-grep, 0 refs);
the sizer's internal 10000 default remains the money path's only ceiling (IA-P3-02/IA-P4-02
stand). Mitigating chokepoint behaviors verified in the same call: fail-safe tick-snap →
Option-A intent coercion → ProductResolver (fail-loud on unknown intent/broker,
`product_resolver.py:111-130`) → CNC master lock → tag truncation. Adapter-side additions this
phase: **cancel_order/modify_order collapse EVERY failure to `success=False` + free-text reason**
(:1029-1035, :1102-1108 — including timeouts; and per the July audit, without 429
penalize/reset — KNOWN :175 stands at current lines); callers branch on `success` alone except
the reconciler's FIX-186 marker classifier (`order_reconciler.py:117-125`), whose "already
gone" set contains no "complete" variant — coverage of Zerodha's cancel-of-COMPLETE refusal
text is UNVERIFIED (→ OQ-P5-2; ambiguous falls to the safe no-mark/retry branch).

**(d) Cache→token resolution (I lens) — failure modes confirmed LOUD or FAIL-SAFE, and
placement is never cache-blocked.** `InstrumentCache.load` is fail-fast (ConfigMissingError /
ConfigSchemaError abort the boot via startup gate BL-20 `instrument_cache_too_small`);
per-symbol lookups raise typed `InstrumentNotFoundError` (IC2); the adapter's only
placement-path consumer is `_resolve_tick` → **DEFAULT_TICK 0.05 fallback + once-per-symbol
WARN** (FIX-170, `zerodha_adapter.py:1335-1359`) — measured fired 8× ever (P4.5d), orders stay
PLACEABLE. Order placement itself is symbol-keyed (`exchange="NSE"`, tradingsymbol) — **tokens
are never used to place**; the NIFTY-token class (absent instrument = live failure) lives in
the quote/candle/sr paths (P1 territory), not the order path. `get_quote` returns whatever
subset Kite returned — a missing symbol is indistinguishable from no-data at the adapter
(KNOWN, main.py:546-549 census note), and every placement-path quote consumer (slippage guard,
drift top-up, FIX-141, liquidity-if-wired) fails OPEN on absence ⇒ one quote outage silently
disables all price-sanity guards simultaneously — each individually documented (P4.1), the
aggregation noted here.

**(e) Units + config-vs-code sweep at this layer (F/H lens).** Units: the adapter/monitor
layer's knobs are seconds/counts/rupees (poll 2s · fill-timeout 60s · partial 5m · penalize
0.2-5s · max_wait 30s · drift ₹50/₹5,000); consuming expressions verified — **no
fraction-vs-percent defect found** (width: every numeric knob in `broker_limits.yaml` +
`order_monitor`/`circuit_breaker`/`order_reconciler` sections, 19 knobs). Config-vs-code: TWO
dead keys found — `timeouts.connect_sec` (schema+tests only; main.py:380 passes only
`read_sec` as KiteConnect's single timeout) and `backoff_sequence_sec [1,5,30]` (the G7 ladder
its comment still promises — "then soft_kill" — was superseded by BL-6; production consumers =
0, schema+tests only) → IA-P5-08. Declared-reserved (not counted): adapter ctor
`cost_calculator` (`self._cc` write-only) and `account_id` (IC9). Stale comment: the D.3 note
in `get_server_time` still describes the margins bucket as "burst=1, 1/sec" — it is 8/8.

### P5.3 NEW findings

---
**IA-P5-01**
- **WHAT:** The fill-truth event payload is stale at source, and a live writer faithfully
  persists it: `_safe_transition` publishes `OrderStatusChanged` with `entry.filled_qty` /
  `entry.avg_fill_price` (`order_monitor.py:1310-1314`), but `_handle_complete` never updates
  `entry` with the poll's fill data before transitioning (:985-988) — so OrderManager's OMgr10
  subscriber (live in production, `bus=event_bus` main.py:2624-2628) overwrites every COMPLETE
  orders row with `qty_filled=0, avg_fill_price=NULL` (`order_manager.py:740-752`, both columns
  unconditional).
- **EVIDENCE:** measured all-time: COMPLETE 405 → qty_filled>0 **0**, avg NOT NULL **0**,
  filled_at NOT NULL **405** (same UPDATE ⇒ the writer ran 405/405 and wrote the zeros);
  `on_order_status_changed_failed` = 0 in 23 logs.
- **CLASS:** Correctness / Consistency. **NEW-or-KNOWN:** KNOWN-CORRECTED — **IA-P4-04's root
  cause was wrong** ("written by NOTHING… never wired or removed"): the column is wired and
  written on every fill; the PAYLOAD is stale. The July audit knew the partial-path staleness
  (:174); the complete-path staleness and its persistence were not registered. P4's
  recommendation ("document the column dead, or drop it") is superseded: the third option —
  fix the 2-line payload — makes the column TRUE.
- **ROOT CAUSE:** `_handle_complete` computes `final_qty/final_price` locally and hands them
  only to `OrderFilled`; the BL-12 snapshot reads the un-updated watch entry.
- **RECOMMENDATION (described, ⛔ not applied):** update `entry.filled_qty/avg_fill_price`
  from the poll before `_safe_transition` in `_handle_complete` (2 lines) — orders rows then
  carry real fill truth; also gives IA-P5-02's finalize sites a fresher snapshot for free.
- **SEVERITY-BY-IMPACT:** MED — every fill audit from the orders table is wrong today
  (205 fills read as zero-fill); no runtime consumer reads the column (P4 sweep), so the
  damage is to audits/tooling/forensics — the exact surface this campaign runs on.

---
**IA-P5-02**
- **WHAT:** Entry-cancel finalization trusts the last pre-cancel poll and never re-reads the
  broker after the cancel — across ALL FOUR entry-cancel sites (60s fill-timeout, FIX-141
  pending-R:R, 15:15 force-close, shutdown sweep). Zerodha's cancel kills only the REMAINDER:
  shares filled between the last 2s poll and the broker's cancel processing stand. Believed
  zero-fill ⇒ reservation released + trade FAILED ⇒ the standing position is dispatched by
  CHECK2 to **HUMAN_ORDER** (FAILED is outside the inflight statuses and `_RECOVERY_STATES`)
  — the A-1/E-1 objective "never call a system order human" (`order_reconciler.py:3766-3770`)
  is structurally unreachable for this path. The position sits without SL, outside daily-loss
  and fm_ledger, until the EOD residual sweep flattens it unbooked (`eod_squareoff.py:1449-1481`
  treats any today-trade symbol as system-owned) — the realized P&L never enters the books and
  is absorbed silently by the next boot's broker-net capital seed (the T2 shared-cash seam
  shape). The full-fill-in-window variant fails the cancel and is LOUD (orphan CRITICAL +
  soft_kill; July-audit :173's "backstop catches it" is hereby made precise: the backstop
  ALERTS and BLOCKS ENTRIES — it does not adopt, protect, or book).
- **EVIDENCE:** cancel sites and their finalize-from-`entry.filled_qty` publishes
  (`order_monitor.py:1103-1122, 1188-1203, 823-854, 542-586`); CHECK2 dispatch
  (`order_reconciler.py:903-923`); recovery-states gate (:3734); EOD sweep ownership test
  (`eod_squareoff.py:1447-1474`). Exposure width: 111 cancel windows to date (96 zero-fill +
  15 pending-RR), 0 hits (orphan_detected 0 ever; July HUMAN_ORDER rows = exactly the T2 ten;
  EOD residual flattens 0 ever; partial evidence 0 — no PARTIAL status ever observed live).
- **CLASS:** Safety / Silent-failure. **NEW-or-KNOWN:** NEW as an endpoint trace — sharpens
  two KNOWN LOWs (July :173 cancel-fail race, :174 partial-cancel stale qty) into the policy
  endpoint (HUMAN_ORDER + unbooked P&L + tolerance-widening, see P5.6) and extends them to the
  cancel-SUCCESS variant, which is the silent one. ⚠️ Paper CANNOT exercise any of it (paper
  cancel always succeeds, no PARTIAL synth — the 14/19 constant-branch class).
- **ROOT CAUSE:** cancel result carries no fill data at Zerodha; nothing re-reads
  `get_order_history` after the cancel; the FIX-186 refusal classifier exists only
  reconciler-side.
- **RECOMMENDATION (described, ⛔ not applied):** one post-cancel history re-read (on success
  AND failure) before finalizing, routing any discovered fill through the existing
  OrderPartiallyTerminated machinery; and/or include FAILED-with-broker-fills in the A-1/E-1
  correlator. Careful-loop: touches the live order path.
- **SEVERITY-BY-IMPACT:** MED-HIGH latent / LOW likelihood-to-date — the surviving shape is a
  naked, unbooked intraday position for up to ~5h with only a once-daily "Naked untracked"
  WARNING mislabelled as operator action; capital math self-heals at next seed, the P&L record
  never does.

---
**IA-P5-03**
- **WHAT:** When CHECK1 wins the finalize race against our own fill callback (~0.9s, KNOWN)
  and `kite.trades()` has not yet surfaced the seconds-old execution, the FIX-148 fallback
  books the EXIT AT ENTRY PRICE (`entry_proxy`) — and the monitor's real fill price, arriving
  moments later, is discarded by the double-close guard (`order_placer.py:2368-2374`).
  trades.net_pnl and fm_ledger.pnl_delta then disagree with the broker's cash by the full
  price move; with orders.avg_fill_price NULL (IA-P5-01) no local record of the true exit
  price exists anywhere.
- **EVIDENCE:** measured live — SWIGGY 28-Jul 15:17:17: `exit_price=267.82(entry_proxy)`,
  closure_source=OWN_EOD, net_pnl −0.30 (charges only). Width: exit-price resolution all-time
  = broker_trades 42 / **entry_proxy 2**.
- **CLASS:** Correctness / Consistency. **NEW-or-KNOWN:** the substrate is KNOWN (D-1
  close-race + §C/§D CHECK1-wins + FIX-148 proxy-by-design); the measured price-truth
  consequence and the discarded-real-price mechanism are NEW evidence.
- **ROOT CAUSE:** trades()-lag at T+seconds; the double-close guard returns before comparing
  its (real) price against the (proxy) booked one.
- **RECOMMENDATION (described, ⛔ not applied):** in the double-close-guard branch, when the
  discarded event carries a real `avg_fill_price`, COALESCE-update the proxy financials (exit
  price + P&L delta) — bounded, evidence-based, no new close path. (§D deferral would also
  prevent it but is the shipped-OFF knob with its own paper-rehearsal blocker — KNOWN, not
  reopened.)
- **SEVERITY-BY-IMPACT:** LOW-MED by rupees to date (2/44, EOD-shaped closes near entry);
  MED by principle — it is the one MEASURED live divergence between booked P&L and broker
  truth in the current era.

---
**IA-P5-04**
- **WHAT:** UNKNOWN_IN_FLIGHT (place-timeout) recovery is not restart-durable: the recovery
  queue is in-memory only (`order_placer.py:1446-1452`, getter :750-756), the crash feed
  selects `status='PENDING'` only (`state_store.py:1012`), and CHECK2's in-flight dispatch
  lists `("PENDING_FILL","PENDING")` only (`order_reconciler.py:915-917`) — so a restart
  between the timeout and its resolution strands the trade in a state no feed re-produces,
  even though `_RECOVERY_STATES` itself includes it (:205). If the ambiguous entry FILLED, the
  position lands in the IA-P5-02 HUMAN_ORDER endpoint; if not, a non-terminal
  UNKNOWN_IN_FLIGHT row lingers.
- **EVIDENCE:** the three feeds cited; `place_timeout_UNKNOWN_IN_FLIGHT` 0 ever (23 logs);
  trades status census: 0 UNKNOWN_IN_FLIGHT rows ever (P1's non-terminal strays = 2 RESERVED,
  18-Jun, re-confirmed disjoint).
- **CLASS:** Reachability / Correctness. **NEW.** **ROOT CAUSE:** FIX-068 pre-dates the
  A-1/E-1 unification; the crash feed was never widened to the status FIX-068 introduced.
- **RECOMMENDATION (described):** widen `get_orphaned_pending_trades` (or a sibling feed) to
  `('PENDING','UNKNOWN_IN_FLIGHT')` and add the status to CHECK2's in-flight tuple — pure
  SELECT-widening, but careful-loop (recovery path).
- **SEVERITY-BY-IMPACT:** LOW-MED — needs timeout ∧ crash inside ~45s ∧ fill (triple
  coincidence, never yet); consequence when it lands is the -02 endpoint.

---
**IA-P5-05**
- **WHAT:** OM5's Kite status vocabulary is incomplete — measured live: `kite_status="CANCEL
  PENDING"` hit the `unknown_status` WARN branch **2×** (28-Jul 13:21, 29-Jul 12:03). Same
  class: "PUT ORDER REQ RECEIVED", "VALIDATION PENDING", "AMO REQ RECEIVED". An unmapped
  status gets WARN-only cycles: no OSM transition AND no fill-timeout check (the timeout runs
  only inside `_handle_open`), so an order PARKED in an unmapped status is exempt from the
  cancel machinery for as long as it stays there.
- **EVIDENCE:** `order_monitor.py:84-88` (the five sets) vs the 2 measured lines; :766-786
  (the dispatch; no timeout call on the else branch).
- **CLASS:** Correctness / Documentation. **NEW-trivial** (both live instances were 1-cycle
  transients of our own cancels). **RECOMMENDATION (described):** map the transient statuses
  into `_KITE_STATUS_OPEN` (they are open-equivalents) so the timeout clock keeps running.
- **SEVERITY-BY-IMPACT:** LOW.

---
**IA-P5-06**
- **WHAT:** CHECK2's naked-position detector is GTT-blind and delivery-sell-blind:
  `_position_is_naked` greps regular open orders for an opposite-side `trigger_price>0`
  (`order_reconciler.py:1976-1980`) — GTT-based protection (the ONLY protection delivery
  trades have) is invisible to it, and a delivery SELL day surfaces as an untracked "short"
  with no stop. Measured: **5/5 false "Naked untracked position" WARNINGs = exactly the T2
  five (IOB/TRIDENT/SOUTHBANK/MSUMI/SJVN), 31-Jul** — an alert artifact of the T2 seam the
  Slice-2.5 record did NOT predict (it predicted the Orphan-GTT WARNINGs, which also fired,
  P4.2c).
- **EVIDENCE:** the grep + `system_2026-07-31.log`; July ORPHAN_ADOPTION rows = exactly the
  T2 ten (5 symbols × buy-day 29-Jul + sell-day 31-Jul), zero non-T2.
- **CLASS:** Silent-failure posture (false-alarm side). **NEW.** **ROOT CAUSE:** the
  protective-stop probe predates GTTs. **RECOMMENDATION (described):** consult `get_gtts()`
  in `_position_is_naked` (or suppress the naked probe for CNC-product positions). Matters
  BEFORE any delivery go-live: every legitimately GTT-protected overnight holding will
  otherwise emit a daily false "naked" WARNING — desensitization against the one alert that
  flags the -02 endpoint.
- **SEVERITY-BY-IMPACT:** LOW today (alert noise); rises with delivery.

---
**IA-P5-07**
- **WHAT:** Rate-pressure coupling at the "order" bucket (8 burst / 8 per-sec): per-order 2s
  history polls share it with place/cancel/modify — each open trade contributes 2 watched exit
  legs, so ~5 open trades + pending entries ≈ 6 req/s SUSTAINED of the 8/s refill, and a
  broker-429 `penalize` freeze (≤5s) pauses SL/TGT placement behind the same gate. The
  client-side `BrokerRateLimitError` raised by a saturated `acquire` inside
  `get_order_history` (:1151, outside the try) lands in the monitor's generic handler and
  counts toward the ×3 HARD_KILL breaker — self-inflicted pacing can escalate to flatten-all
  (July-audit :176, KNOWN, stands).
- **EVIDENCE:** ZA3 category map :238-253; bucket values `broker_limits.yaml:9-23`; measured
  zeros — broker_429 0 ever, bucket freezes 0, rate-limit timeouts 0, api_failure_counted 0,
  timeout_skipped 5 (transient), across 23 logs.
- **CLASS:** Architecture / Coupling. **KNOWN-composed** (fresh arithmetic + fresh zeros; the
  July :176 escalation line re-confirmed at current lines). **RECOMMENDATION (described):**
  none urgent at today's scale (positions capped ~5); if position count ever scales, move
  history polling to its own bucket or batch via `orders()` (one call for all watched).
  Hygiene: the `get_server_time` D.3 comment still says margins "burst=1, 1/sec" — it is 8/8.
- **SEVERITY-BY-IMPACT:** LOW today; scales with open-position count, not sizing.

---
**IA-P5-08**
- **WHAT:** Dead config at the broker layer: **(a)** `broker_limits.timeouts.connect_sec: 5`
  — consumed by the config schema + tests only; main.py:380 passes only `read_sec` to
  KiteConnect (single timeout param) — the TCP-connect knob has never had an effect. **(b)**
  `backoff_sequence_sec: [1,5,30]` — the G7 429-ladder its comment still promises ("4-step
  backoff then soft_kill") was superseded by BL-6 penalize/BL-19; production consumers = 0
  (schema + tests only).
- **EVIDENCE:** repo-wide greps (`connect_sec`, `backoff_sequence`) — consumers =
  config_loader + tests only.
- **CLASS:** Config-vs-code. **NEW** (IA-P3-02 family, instances 6-7).
- **RECOMMENDATION (described):** delete both keys or wire them; until then the YAML claims
  429 behavior the system does not have.
- **SEVERITY-BY-IMPACT:** LOW — belief hazard only.

---
**IA-P5-09**
- **WHAT:** CHECK6 declares a PENDING_FILL order "orphaned" by its absence from
  `get_open_orders()` (OPEN/TRIGGER-PENDING only) — a FILLED (COMPLETE) order is equally
  absent, so if the trade were still PENDING_FILL (only possible with the monitor dead),
  CHECK6 would mark it FAILED + release after 3 cycles (~45s) while the position stands. The
  A-1/E-1 recovery already models the correct source (`get_all_orders`, any status, with
  tags); CHECK6 predates it. Bounded: every monitor-death path (auth×3 / api×3) also fires
  HARD_KILL → broker-position flatten sweeps.
- **EVIDENCE:** `order_reconciler.py:2350-2434`; adapter :1796-1855 (open statuses only);
  measured: **CHECK6 has never logged a row, ever** (0 in 7,804).
- **CLASS:** Consistency / Reachability. **NEW-latent.** **RECOMMENDATION (described):**
  point CHECK6 at `get_all_orders` and branch on the real status (COMPLETE → hand to the
  fill/recovery path, not FAILED). Careful-loop.
- **SEVERITY-BY-IMPACT:** LOW (double-shielded by kill coupling; never fired).

---
**IA-P5-10** (hygiene bundle, one ID)
- (a) **Timeout-class non-escalation**: monitor `BrokerTimeoutError` = skip-and-retry forever
  (auth escalates ×3, api ×3, timeout ∞ — :651-658); a sustained network partition suspends
  fill detection with only per-cycle WARNINGs (5 transient in window). Posture note.
- (b) OSM docstring says "all 8 valid state names"; STATES has 9 (UNKNOWN_IN_FLIGHT added,
  `order_state_machine.py:64-74,131-133`). Doc drift.
- (c) `rehydrate_from_store` re-tracks with `expected_price=row["price"]` — a MARKET leg
  re-tracks at 0.0 with no LTP fallback (the `track()` fallback is not on this path;
  :448-468); slippage analytics only.
- (d) Paper `cancel_order` overwrites a COMPLETE paper fill to CANCELLED (:1004-1010) —
  July-audit :177 KNOWN, re-confirmed; it is the reason the -02 branches are
  paper-unexercisable.
- (e) `_on_order_filled` claims via `get()` not `pop()` (:1764-1769) — July :179 KNOWN,
  stands, still single-poll-thread-safe.
- (f) Fill handlers run ON the single poll thread — `_handle_entry_fill` places SL/TGT and
  sends the ORDER PLACED alert synchronously, stalling all other order polls for its duration
  (M-A2 bounds the alert leg at 8s; M-C4/M-C8 family — noted per brief, ⛔ not re-opened).
- **CLASS:** Silent-failure posture / Documentation. **SEVERITY:** LOW.

### P5.4 KNOWN items re-verified — status updates (no re-numbering)

| Known ID | Status on the current system (fresh evidence) |
|---|---|
| IA-P4-04 (orders.qty_filled written by nothing) | **ROOT CAUSE CORRECTED → IA-P5-01**: written by OMgr10 on all 405 COMPLETE rows (filled_at proves it), payload stale (0/NULL). Data conclusion unchanged; "document dead or drop" superseded by the 2-line payload fix option. Sub-figure: entry fills all-time = **205**, not 145 |
| July-audit LOW :173 (fill-timeout cancel-fail → FAILED+orphan, "backstop catches it") | STANDS at :1103-1122, **made precise by IA-P5-02**: the backstop = CRITICAL + soft_kill only; adoption is impossible from FAILED; 0 occurrences ever |
| July-audit LOW :174 (partial immediate-cancel publishes stale filled_qty) | STANDS at :907-935; broadened by IA-P5-02 to all 4 cancel sites + the cancel-success zero-fill variant; **no PARTIAL has ever occurred live** (0 log lines, 0 qty mismatches, 0 CANCELLED-with-fill rows) — the entire partial machinery rests on tests |
| July-audit LOW :175 (429 backoff bypassed on cancel/modify) | STANDS at :1029-1035 / :1102-1108 (blanket except → success=False; no penalize, no counter reset) |
| July-audit LOW :176 (client-side rate-limit error counts toward HARD_KILL breaker) | STANDS (acquire outside the try at :1151 → generic handler :659-685); composed with fresh arithmetic in IA-P5-07; 0 occurrences ever |
| July-audit LOW :177 (paper cancel overwrites COMPLETE; synth drift) | STANDS at :1004-1010; is the paper-cannot-exercise root for the -02 branches |
| July-audit :200/:215 (terminal-set divergence; flatten ×3; pending_rr ERROR-no-escalation; in-memory _fill_map; CO SL broker-managed) | ALL STAND; pending-RR measured 15/15/0 = VERIFIED LIVE (P4); flatten trio re-confirmed (eod :1495 / reconciler :2046 / structure-exit) |
| D-1 (CHECK1 vs _handle_exit_fill double-release race) | STANDS as designed-mitigated (CAS in close_trade; §D deferral shipped-OFF); its PRICE consequence measured fresh → IA-P5-03 (SWIGGY 28-Jul) |
| check1_mid_fill_defer_sec (memory: default 0.0 OFF; paper cannot exercise) | RE-VERIFIED on the VM today: 0.0 = OFF; deferral bookkeeping tripwire-asserted untouched at bound 0 |
| FIX-068 / UNKNOWN_IN_FLIGHT · BL-19 429 retry | 0 · 0 ever (fresh: place_timeout 0 lines, broker_429 0, trades status census 0) — both recovery paths remain production-unexercised; **+ the crash-durability hole → IA-P5-04** |
| FIX-186 cancel-refusal markers | Present and reconciler-only; marker coverage of the COMPLETE-refusal text unverified → OQ-P5-2 |
| H-15 empty-history second source | Verified; open-orders-only source with documented fail-toward-orphan direction; 0 firings ever (all three empty-history counters 0) |
| M-O2 / E4 (CHECK1/CHECK4 costs) | CLOSED-confirmed in code: `round_trip_costs_or_zero` at :1333 (CHECK1) and :2213 (CHECK4) — the July ":215 costs=0.0" list is stale for these two sites |
| CHECK2 HUMAN_ORDER policy + ₹5,000 G3 widening (T2 seam memory) | RE-VERIFIED: policy at :1773-1862, widening at :3396-3397; G3 in-session tolerance = max(₹50, 10%·expected) + ₹5,000 when human set non-empty; G3 publishes CapitalDriftDetected → BL-2 ladder (soft ₹1,000 / hard ₹2,500, single-sample — K-ladder record) — "non-escalating" holds for T2-sized deltas, not unconditionally |
| Reconciler June storms (context for RC counters) | ORPHAN_ADOPTION 2,647 · CRASH_RECOVERY_SL 915 (success 11) · CAPITAL_DRIFT 4,183 — **100% June-2026**; last-14-days = 0/0/0. The RAMCOIND/FIX-181/182/190 layers hold in the current era |
| AGARIND 16-Jun (POSITION_GREW) | The ONE measured production fill-belief divergence (broker 2 vs local 1, 14 cycles, ~3.5 min) — June era, no recurrence since |
| Paper-cannot-exercise class (14/19 adapter methods) | This phase adds the enumeration for fill ingestion: PARTIAL, REJECTED, empty-history, unknown-status, cancel-failure, and all four -02 races are constant-in-paper — the monitor's divergence machinery is live-only |

### P5.5 Open questions (not guessed into findings)

- **OQ-P5-1:** June-storm archaeology (why G5b success=11/915; what resolved AGARIND) — June
  register territory; post-fix window is clean; not re-opened here.
- **OQ-P5-2:** Zerodha's literal refusal text for cancelling a COMPLETE order vs the FIX-186
  "already gone" markers (no "complete" variant present). Settled only by a live probe or
  vendor docs; ambiguity currently falls to the safe no-mark/retry branch.
- **OQ-P5-3:** Source of P4's "145 real entry fills" sub-figure (all-time is 205, July window
  88, om.complete-in-window 290) — the 0-of-N conclusion is unaffected either way.
- **OQ-P5-4:** `force_close_triggered` = 40 lines vs ~20 trading days in window — likely the
  in-memory once-per-day latch re-arming on intraday restarts; benign; not chased.

### P5.6 SEAM SUMMARY — can the adapter's fill belief and the capital state disagree (P6's starting point)

**Yes — and this seam is the isolated-DB / shared-cash boundary, generalised.** What crosses
into capital: `commit_to_used(actual_fill_price, actual_qty)` on entry fills,
`release(reservation)` on zero-fill terminals, `release_used(exit_price, exit_qty, costs)` on
exits — every argument sourced from the MONITOR's last-poll snapshot or a reconciler broker
read. Three divergence channels cross it: (1) a stale-snapshot finalize (IA-P5-02) releases a
reservation while broker cash is actually deployed in a standing position — fm_ledger books
nothing, daily-loss is blind to the position's P&L, and the discrepancy is absorbed SILENTLY
at the next boot's `broker.net` seed (FM9 syncs once, 09:15 — the T2 seam mechanism, now with
an in-system trigger rather than an external basket); (2) a proxy-priced close (IA-P5-03)
books pnl_delta ≠ broker cash delta — measured live once (SWIGGY, −0.30 booked vs the real
exit); (3) an untracked HUMAN_ORDER position **widens G3's tolerance by ₹5,000**
(`order_reconciler.py:3396-3397`) — on a ~₹10k book, the mislabelling of a SYSTEM orphan as
human does not merely skip management, it **suppresses the one broker-cash check that could
have caught the resulting drift** (≈50% of capital of headroom), and G3's CapitalDriftDetected
→ BL-2 ladder (soft ₹1,000 / hard ₹2,500) therefore never arms. The only broker-truth checks
on this seam are G3 (cash, 15s, throttled 30-min alerts, tolerance as above) and the
15:45/15:58 EOD jobs (P8 scope). **What P5 hands P6:** verify the capital internals against
this seam — the fm_ledger INIT/seed semantics (does the boot seed silently absorb unbooked
P&L, and is that visible anywhere), reservation-vs-used lifecycle under the -02/-03 event
orderings, the daily-loss base's exposure to unbooked positions, and whether the ₹5,000
human-order widening is bounded per-day or compounding. Fill-side ground truth for P6:
entry fills all-time 205, all one-shot full-qty (0 partials ever); the orders table carries
no fill truth (IA-P5-01) — trades + fm_ledger are the only local fill record, and both are
written by the paths whose divergences this phase enumerated.

**Phase 5 done** = fill detection re-measured fresh (polling-only, mechanism + payload traced
to source; the IA-P4-04 root cause corrected by a 3-way measurement); every silent-divergence
path enumerated with its endpoint and exposure width (one June precedent, one measured
current-era proxy-P&L instance, two latent races, four cancel sites, the HUMAN_ORDER endpoint
+ ₹5,000 widening); the qty-verbatim/no-cap seam confirmed at the adapter chokepoint; the
cache→token failure mode confirmed loud/fail-safe and placement-independent; 19 knobs
units-checked (no mismatch) with two dead config keys found; the CHECK battery given its
first all-time reachability census (7,804 rows: 3 checks carry 99% of rows, all June-era; 6
checks have never fired); committed incrementally; ⛔ nothing fixed, nothing pushed, the
3-Aug/4-Aug sequence untouched.
*(Phase 6 — fund/capital state — appends below this line.)*

---

## PHASE 6 — FUND / CAPITAL / STATE (fund_manager + reservation ledger + drift topology, and the P6→P7 seam)

### P6.0 Measurement window & system state

| | |
|---|---|
| Session window | **Sat 01-Aug-2026 ~09:22 → ~10:1x IST** |
| Measurements taken | 01-Aug **~09:4x–09:5x IST**, from the VM (`mode=ro` DB reads + log greps; zero writes) |
| Deployed SHA (VM bare) | **`297b587`** — unchanged since P1 (re-verified in P5, same session) |
| PC tree read | `5420709` = `297b587` + 8 docs-only commits ⇒ code read == deployed |
| Service | `inactive` (designed weekend state) |
| Primary windows | fm_ledger all-time **2,997 rows** (RESERVE 1,234 · RELEASE 1,019 · RELEASE_USED 205 · COMMIT 205 · INIT 63 · SYNC 32 · RESET_PNL 32 · TOP_UP 7) / 23 retained `system_*.log` / reconciliation_log 7,804 (P5 census, same session) |
| Config ground truth (VM == PC, P5-verified + fresh greps) | `capital {intraday 0.70 / positional 0.30, conditional_allocation_enabled false, slm_margin_buffer_pct 0.05, leverage_map {INTRADAY 5.0, CO 6.0, DELIVERY 1.0, BO 5.0}}` · `risk.daily_loss_limit_pct 0.03` (SOLE authority; limit = 3% × current `_total`) · `max_open_positions 5` · `order_reconciler {capital_drift_tolerance ₹50, _pct 0.10 in-session, human_order_margin_tolerance ₹5,000, alert throttle 1800s}` · `drift_handler {log_only ₹250, soft ₹1,000, hard ₹2,500, consecutive 3}` |
| Scope guard | 3-Aug/4-Aug untouched; nothing fixed/tuned; FIX-182 not tuned; kill internals not audited (P7); reconcile jobs not audited (P8); July audits read-only |

### P6.1 Path as verified — THE CAPITAL MODEL AND ITS TRUTH SOURCES

**One capital authority** (`capital/fund_manager.py`): 3-balance invariant per bucket pair
(avail+reserved+used == total, tolerance ₹1.0, INV6/M-C3 per-bucket non-negativity guards —
the Q9-settled BL9 triggers, confirmed at source :2264-2291: they fire only on a NEGATIVE
partition, never on the split), write-ahead `fm_ledger` (BL-5), violations → hard_kill
(FM19/BL-9) + on_critical. **Boot sequence (LIVE):** 08:15 `initialize(compute_live_seed)`
where seed = `broker.net − today_realized_pnl_carryover` (M-C1; cold boot Σ=0 ⇒ seed =
broker.net verbatim, main.py:1692-1696, :2373-2376) → `rehydrate_from_open_trades` (replays
RESERVE/COMMIT chains of OPEN trades + today's RELEASE_USED PnL) → **09:15 FM9 one-shot
sync** (`main.py:955-1018`: fires only if the service started BEFORE 09:15; holiday-gated; a
single `sync_from_broker(broker.net)`; INFO alert only when |Δ|>1; **nothing re-syncs broker
cash for the rest of the day**). Bucket split: `resolve_bucket_allocation` computed ONCE in
main.py:2317 — with `conditional_allocation_enabled: false` it returns the config split
verbatim; the 1.0/0.0 conditional legs are unreachable (KNOWN, R10 on the MASTER_PENDING
board — the flip is Rama's 4-Aug decision). **Drift escalation topology**
(`capital/drift_handler.py`): `_ESCALATING_SOURCES = {fund_manager (FM9),
fund_manager_self_check (CHECK7), fund_manager_bucket_overflow (H-1)}` — BL-2 tiers log
₹250 / soft ₹1,000 / hard ₹2,500 (#11 K-ladder: DECIDED, do-not-retune) apply to those three
ONLY; **every reconciler-sourced event (G3, CHECK5, CHECK7-actions aside) is INFO-only by DH1
design** — G3's CRITICAL Telegram (30-min throttle) is its terminal escalation. Daily loss:
post-close check in `release_used` (:1302-1314) — Σ(fm_ledger.pnl_delta today) ≤ −(3% ×
current _total) → callback = EodSquareoff.fire_now (late-bound) + soft_kill. Pre-trade gate
reads the same pct + the B-1 unrealized-MTM term (fresh-gated, realized-only fallback).

### P6.2 Headline re-measurements (mandated)

**(a) THE P5→P6 STING CHAIN — TRACED END-TO-END AND PROVEN, with a sharpening: the ₹5,000
widening is the THIRD blindness layer; the first two absorb a real divergence on their own,
and did, measurably, in the T2 window.** The chain in code: a broker position with no local
trade → CHECK2 HUMAN_ORDER (`order_reconciler.py:1815-1822`; IA-P5-02's FAILED-trade orphan
lands here) → `_human_order_symbols` non-empty → G3's effective tolerance += ₹5,000
(:3396-3397). The three layers, measured:
1. **In-session base tolerance = max(₹50, 10% × expected)** (FIX-190 Bug I, :3393-3395) ≈
   **₹987-1,000** on this book — the T2 basket's real blocked cash (−₹637.6) rode UNDER it
   for three sessions: **G3 fired ZERO times in the entire T2 window** (last G3 firing ever:
   2×, 06-Jul, `expected=0.00 actual=10000.00` — a startup-order artifact, pre-current-era).
2. **The overnight seed absorb, measured to the rupee:** fm_ledger INIT 29-Jul 08:15 =
   **9,997.4** → 30-Jul 08:15 = **9,359.8** (−₹637.6, the basket's cash) → 31-Jul 9,360.0.
   No alert exists on this boundary — the seed is DESIGNED to re-base to `broker.net`
   (anything unbooked is inside broker.net and becomes the new total silently).
3. **The escalating comparison is structurally behind the absorb:** FM9's 09:15
   `sync_from_broker` — the ONLY broker-truth publisher the BL-2 kill ladder listens to —
   measured **delta = 0.0 on ALL 8 retained SYNC days including both T2 mornings**
   (`old==new` at 09:15:00.0xx each day): it compares broker@09:15 against a total that was
   seeded FROM broker@08:15 the same morning. It cannot see accumulated divergence, ever, by
   ordering.
4. The ₹5,000 widening (armed live 29/31-Jul — the T2 ten HUMAN_ORDER rows, P5 census) lifts
   the blind band to ≈ ₹6,000 ≈ **60% of the ₹9,997 book** — and IA-P5-02 guarantees a
   SYSTEM-caused orphan can open it.
⇒ **VERDICT: a real broker-cash divergence of ~6.4% of the book crossed three sessions with
zero alerts of any kind, and a divergence of up to ~60% would do the same while any
untracked order exists. The one check that could catch it (G3) is non-escalating by DH1
design; the one escalating check (FM9) is blind by ordering; the seed absorbs the rest
nightly.** → IA-P6-01/-02. (The 30-Jul redesign doc's B6 "artefact" verdict stands for
LEGITIMATE delivery trades — those write live-DB rows and never classify HUMAN_ORDER — but
it predates IA-P5-02 and does NOT cover the mislabelled-system-orphan entry route.)

**(b) The internal-capital == broker-cash invariant: ENFORCEMENT exists, VERIFICATION does
not (G-lens crux, confirmed).** CHECK7 re-measured fresh (:3491-3574): it compares FM
in-memory reservation margins against fm_ledger margin_delta sums — **internal vs internal**
(28-Jul note CONFIRMED); its ledger-orphan direction is explicitly "deferred to Phase E"
(:3503-3504). The 3-balance invariant, BL-9/BL-4 kill wiring, INV6 guards: all enforce
INTERNAL consistency. The only broker-truth comparisons are G3 (non-escalating, tolerance as
above) and FM9 (once, post-seed, measured 0.0 forever). **Nothing verifies the system's
capital belief against broker cash in a way that can reach a kill rung** — the P6→P7 seam
headline.

**(c) RESERVE→COMMIT→RELEASE integrity — measured, with one structural surprise.**
**True leaks (RESERVE with NO terminal row ever): 10 all-time, Σ ₹1,628.13 — ALL June-era**
(15-18 Jun: 8 × MIS ₹62-188 incl. GICRE 16-Jun; HARIOMPIPE ₹462.44 + SETL ₹353.04 DELIVERY —
the June delivery attempts), **0 since 18-Jun** — the leak class died with the June fixes;
each leaked in-memory only until its process restart (the next seed re-based; the ledger
chains remain permanently open). TOP_UP 7 rows (FIX-075's one firing + June). **The
surprise: the per-reservation ledger chain cannot express a CLOSED lifecycle** — COMMIT rows
write `margin_delta=0.0` (the excess-return leg moves money with no delta) and RELEASE_USED
rows carry **`reservation_id=NULL`** (release_used's `_write_ledger` call passes trade_id
only, fund_manager.py:1257-1277) ⇒ **every committed reservation's signed sum ends at the
FULL original margin: measured 205/205 committed rids, residual avg ₹98.66 / max ₹202.29**
(sample chain: RESERVE +51.29 → COMMIT amount=48.85 delta=0.0 → nothing). The
`sum_fm_ledger_margin_delta` docstring contract ("for a closed reservation: sum is 0…
RESERVE and RELEASE_USED net out exactly", state_store.py:1126-1134) is **false for every
fill the system has ever made**. CHECK7's live-only iteration is unaffected (a live rid's
sum == its reserved margin, correct); anything built on the stated contract — including the
deferred Phase-E orphan detection — would be wrong on arrival. → IA-P6-03.

**(d) Units + config-vs-code sweep at this layer (F/H lens).** Units: 16 capital knobs
checked against consuming expressions — bucket pcts (fractions, FM12 sum-validated) ·
`daily_loss_limit_pct 0.03` fraction × `_total` ✓ (:1304) · `slm_margin_buffer_pct 0.05`
fraction ✓ · leverage divisors ✓ · drift trio rupees ✓ · `capital_drift_tolerance` ₹50 /
`_pct 0.10` fraction (`max(tol, |expected|×pct)` ✓) · human ₹5,000 rupees ✓ · invariant
tolerances ₹1.0 / ₹0.01 ✓ — **no fraction-vs-percent mismatch found** (width: every knob in
`capital:`, `risk:` daily-loss, `drift_handler:`, `order_reconciler:` tolerance family).
Config-vs-code yield, as in every phase, is DEAD or HALF-DEAD knobs, not wrong units:
`conditional_allocation_enabled false` ⇒ the 1.0/0.0 legs never run (KNOWN, R10 flip
pending); **`slm_margin_buffer_pct` is HALF-dead** — the 5% buffer is still levied on every
reservation (:520-523) while its named release path (`release_slm_buffer`, fired on SL-M
accept) is unreachable since P0 removed SL-M — the buffer silently rides into commit-excess
or release instead (money-effect live, stated purpose dead) → IA-P6-07b. The 30/70 split
itself: with delivery double-locked, the positional 30% of every seed idles by design
(no-borrow) — the system trades on ~70% of ACTUAL (KNOWN allocation; R10 is the tracked
decision; sized here only for the record: ₹9,360 seed ⇒ ₹6,552 intraday spendable).

**(e) The leverage asymmetry, stated once so nobody re-derives it (E lens).** The FM is
leverage-AWARE: reservations = notional/5 for MIS (yaml `leverage_map.INTRADAY: 5.0`,
consumed :518, :901); the SIZER is leverage-BLIND (binds on notional/concentration — G8,
P3-confirmed). Both are internally consistent (reserve ≈ broker margin; size ≈ conservative
notional) but "capital consumed" differs ×5 between the two layers by design, and the
capital-vocabulary memory line "system UNAWARE of leverage" is imprecise at the FM layer.
Documentation-only. → folded into IA-P6-05.

### P6.3 NEW findings

---
**IA-P6-01**
- **WHAT:** The invariant that matters most — internal capital == broker cash — has no
  verification that can escalate, and three stacked mechanisms guarantee a real divergence
  stays alarm-free: (1) G3's in-session tolerance max(₹50, 10%·expected) ≈ ₹1,000; (2) the
  FIX-182 human-order widening +₹5,000 (armed by exactly the IA-P5-02 mislabel class, and
  live 29/31-Jul via T2) ⇒ blind band ≈ 60% of the book; (3) DH1's source filter makes G3
  INFO-only to the kill ladder regardless of magnitude, while the only ESCALATING
  broker-truth publisher (FM9) runs once a day at 09:15, one hour AFTER the 08:15 seed
  re-based the books to the same broker number — measured delta 0.0 on all 8 retained SYNC
  days. The kill ladder is structurally deaf to broker-truth capital divergence.
- **EVIDENCE:** P6.2(a) — the measured T2 trace (INIT 9,997.4→9,359.8; SYNC 0.0×8; G3
  firings in window = 0, last ever 06-Jul); code sites `order_reconciler.py:3388-3397`,
  `drift_handler.py:65-69,140-153`, `main.py:955-1018,1692-1696`.
- **CLASS:** Safety / Invariant-coverage. **NEW-or-KNOWN:** KNOWN-COMPOSED → PROVEN — the
  T2 seam memory, FIX-182 concern, CHECK7-internal note and P5's sting hypothesis each held
  a piece; the end-to-end chain with live measurements is new, as is the sharpening that
  layers (1)+(3) suffice without the widening.
- **ROOT CAUSE:** each layer is individually deliberate (FIX-190 noise fix; FIX-182 operator
  policy; DH1 unit-safety; seed-as-truth) — the blindness is their COMPOSITION, which no
  single design reviewed.
- **RECOMMENDATION (described, ⛔ not applied):** one escalating broker-truth rung: G3 above
  a hard ceiling (e.g. >X% of book, UNCONDITIONAL — not widenable by the human set) publishes
  under an escalating source; and/or an 08:15 seed-delta check (see IA-P6-02). ⛔ FIX-182
  itself not tuned (out of scope per brief).
- **SEVERITY-BY-IMPACT:** MED-HIGH — measured: 6.4% of the book crossed silently; bounded
  only by the EOD sweeps' inventory checks (P8) and the operator's own broker statement.

---
**IA-P6-02**
- **WHAT:** The boot seed silently absorbs ANY unbooked P&L or external cash movement — by
  construction (seed = broker.net − booked carryover; the unbooked part is inside broker.net)
  — and NO day-over-day comparison exists on that boundary: nothing persists yesterday's
  expected closing capital to compare the seed against (the CASH sibling of the 30-Jul
  redesign doc's B2 inventory gap).
- **EVIDENCE:** measured live: INIT 30-Jul = 9,359.8 vs 29-Jul = 9,997.4 (−₹637.6, the T2
  cash) with no alert artifact of any kind (the only trace is the INIT ledger row + an INFO
  boot log); `compute_live_seed` main.py:1692-1696; the 09:15 INFO alert fires only on the
  SYNC delta, which is 0.0 by ordering.
- **CLASS:** Silent-failure. **NEW** (the 30-Jul doc registered the INVENTORY version; the
  cash version was implicit in "the FM baseline self-heals" and is here named as the gap it
  is).
- **ROOT CAUSE:** seed-as-truth is correct for RE-BASING; it was never paired with a
  "was this the number we expected?" check.
- **RECOMMENDATION (described):** at initialize, compare the seed against (prior INIT +
  Σ subsequent pnl_delta − known cash flows) and emit INFO/WARNING on unexplained delta —
  one query, no schema; pairs with the redesign doc's D-2/D-3 morning-check shape.
- **SEVERITY-BY-IMPACT:** MED — it is the terminal absorber in the IA-P5-02/IA-P6-01 chain;
  every upstream miss becomes permanent and invisible here.

---
**IA-P6-03**
- **WHAT:** The reservation ledger cannot express a closed lifecycle: COMMIT rows carry
  margin_delta=0.0 while moving the excess, and RELEASE_USED rows carry reservation_id=NULL
  (keyed by trade_id only) — so every committed reservation's signed margin_delta sum
  permanently equals its FULL original margin, and `sum_fm_ledger_margin_delta`'s stated
  contract ("closed ⇒ sum 0") is false for every fill ever made.
- **EVIDENCE:** measured: 205/205 committed rids, residual avg ₹98.66 / max ₹202.29; sample
  chain RESERVE +51.29 → COMMIT 0.0 → (end); writer sites fund_manager.py:914-928 (COMMIT,
  margin_delta=0.0) and :1257-1277 (RELEASE_USED, no reservation_id passed); the contract
  text state_store.py:1122-1137.
- **CLASS:** Consistency / Documentation. **NEW.** **ROOT CAUSE:** E4 keyed RELEASE_USED by
  trade_id and nobody re-derived the per-rid arithmetic the docstring still promises.
- **RECOMMENDATION (described):** either pass reservation_id through release_used's ledger
  write (1 arg; restores per-rid conservation) or rewrite the docstring + mark Phase-E
  orphan detection as needing a different key. Current CHECK7 (live-only) is unaffected
  either way.
- **SEVERITY-BY-IMPACT:** LOW-MED — no runtime consumer is wrong today; the next consumer
  built on the stated contract would be wrong on arrival (and the deferred Phase-E is
  exactly that consumer).

---
**IA-P6-04**
- **WHAT:** The committed quantity — an arithmetic input to every close's margin release —
  round-trips through free text: `get_entry_commit_margin` regex-parses `qty=(\d+)` out of
  the COMMIT row's human-readable `reason` string (written by commit_to_used as
  `"fill: qty={n} price={p} …"`), guarded only by a "keep that token parse-stable" comment.
  M-C7's leverage-change-invariance rests on prose.
- **EVIDENCE:** state_store.py:2573-2588 (the regex); fund_manager.py:922-926 (the writer);
  fallback = the exact current-leverage recompute M-C7 exists to avoid, loud
  (`release_used_commit_unresolved` WARN) — measured **0 occurrences ever** (23 logs).
- **CLASS:** Architecture / Consistency (the free-text-dependency class, here as data
  carriage on the money path rather than classification). **NEW.**
- **ROOT CAUSE:** fm_ledger deliberately carries no qty column (EF-5 "each table owns what
  it owns"); M-C7 needed qty and took it from the only place it existed.
- **RECOMMENDATION (described):** persist committed qty structurally (a column, or parse-at-
  write into an existing numeric field) — schema-touch ⇒ careful-loop; until then the
  parse-stability comment is the guard.
- **SEVERITY-BY-IMPACT:** LOW — fail direction is loud + fallback-correct-at-current-config;
  it becomes real the day someone edits the reason format or the leverage map.

---
**IA-P6-05**
- **WHAT:** Documentation bundle on the capital model, recorded to stop re-derivation:
  (a) the FM reserves LEVERED margin (notional/5 MIS; yaml leverage_map is live config)
  while the sizer binds UNLEVERED — two coherent capital models ×5 apart by design; the
  memory line "system unaware of leverage" is imprecise at the FM layer. (b) the daily-loss
  limit is fully DYNAMIC — ₹ = 3% × current `_total`, and `_total` moves with every net
  close (both the base and the threshold move intraday; the dual-daily-loss memory rule
  re-confirmed at :1302-1305). (c) profits are excluded from the tradable floor until T+1
  (INV2 `min(0, pnl)` — invariant.py:75-89) while `release_used` credits them to avail
  immediately — the invariant's rhs and the bucket arithmetic use different conventions,
  reconciled only because `_check_invariant` passes `cash_floor=self._total` and
  `realized_pnl_today=0.0` (:2292-2297), making the INV2 profit-exclusion DEAD CODE at the
  FM call sites (compute_rhs returns _total unchanged — stated in the FM's own comment).
- **CLASS:** Documentation / Reachability (c: the INV2 P7a term is structurally inert in
  production). **NEW-as-registered.** **SEVERITY:** LOW.

---
**IA-P6-06**
- **WHAT:** Escalation-reachability census — the capital layer's entire alarm surface is
  production-unexercised: invariant violations **0 ever** (window grep; `capital_invariant`
  0), commit hard-kill **0**, bucket_overflow **0**, daily_loss_breach **0** (and 0/23
  outcomes by the E4 study — worst day −₹56 vs limit ≈₹296), CHECK7 rows **0 ever** (7,804
  census), drift-handler escalating CRITICAL **0** in window / SOFT / HARD / SOFT_ESCALATED
  never, M-C7 fallback 0, rehydrate anomalies 0, `reserve_restored_for_recovery` 0, FM9
  drift events ≈ never (delta 0.0 ×8 measured; INFO "Capital Updated" alert unexercised).
  Every capital guard except the pre-trade gates has enforcement but no verification-by-fire
  — the "alive but never measured / built but never run" sibling pattern, now quantified for
  this layer.
- **EVIDENCE:** the zero table above (each grep across 23 logs; DB censuses this section).
- **CLASS:** Reachability. **KNOWN-COMPOSED** (individual zeros scattered across prior
  records; the census is new). **RECOMMENDATION (described):** none to build — but any
  future capital change should state which of these zeros it expects to move, since none of
  them can currently distinguish "correct" from "dead".
- **SEVERITY-BY-IMPACT:** LOW as a finding; HIGH as context for P7 (the kill ladder's
  capital triggers have never fired — see seam).

---
**IA-P6-07** (hygiene bundle, one ID)
- (a) The only G3 firings ever in retained logs (2×, 06-Jul) read `expected=0.00
  actual=10000.00` — G3 ran against a zero FM total (startup-order artifact; drift_handler
  even carries a "~zero expected ⇒ possible publisher bug" WARN for the escalating twin).
  None since; not re-opened.
- (b) `slm_margin_buffer_pct 0.05` — HALF-dead: levied on every reserve, releasable only by
  the SL-M-accept path that P0 removed; the buffer rides into commit-excess/release. Config
  claims a purpose the system no longer has.
- (c) The daily-loss breach callback's position-close leg (late-bound EodSquareoff.fire_now)
  has never executed (`eod_not_wired` 0 = wired-or-never-invoked; breach count 0 ⇒
  unexercised either way).
- (d) June-era ledger hygiene, now closed: RELEASE_USED with costs=0 (36) and with
  trade_id=NULL (159) are 100% 2026-06; the E4 17-Jul fix is VERIFIED IN DATA (0 since).
- (e) INIT 63 vs SYNC 32 vs RESET_PNL 32 — the INIT surplus = mid-day warm restarts (each
  re-constructs the FM; H-4 guards double-INIT per process); not chased row-by-row (OQ-P6-2).
- **CLASS:** Documentation / Config-vs-code. **SEVERITY:** LOW.

### P6.4 KNOWN items re-verified — status updates (no re-numbering)

| Known ID | Status on the current system (fresh evidence) |
|---|---|
| T2 isolated-DB / shared-cash seam (memory, 29-Jul) | **NOW FULLY MEASURED**: blocked cash = −₹637.6 (INIT 9,997.4→9,359.8), held flat 31-Jul (9,360.0), zero alerts across 3 sessions; the ₹643.98 memory figure ≈ the ledger's 637.6 + rounding/fees. The seam's mechanism (FM synced 09:15, T2 armed 11:37, nothing re-reads broker cash) confirmed at source |
| FIX-182 ₹5,000 human tolerance | Re-measured: default 5000.0 (:379-383), applied :3396-3397; ARMED live 29/31-Jul (T2's HUMAN_ORDER rows); ⛔ not tuned (per brief). The 30-Jul B6 "test artefact" verdict STANDS for legitimate delivery and DOES NOT cover the IA-P5-02 system-orphan route (sharpened, not contradicted) |
| CHECK7 internal-only (28-Jul note) | CONFIRMED at :3491-3574 — fm-memory vs fm_ledger, unidirectional, ledger-orphan direction deferred; 0 rows ever |
| G3 non-escalating (30-Jul doc §A1(4) + memory) | CONFIRMED at source — DH1 `_ESCALATING_SOURCES` excludes "order_reconciler"; G3's terminal escalation is the throttled CRITICAL Telegram; **P5.6's wording "G3 → BL-2 ladder therefore never arms" is REFINED: the ladder never arms for G3 by SOURCE FILTER, not merely by tolerance** |
| FM9 one-shot (30-Jul doc §A1(7)) | CONFIRMED + sharpened: skip-if-started-after-09:15; holiday-gated; measured delta 0.0 on all 8 retained days — the escalating comparison is structurally post-seed |
| Q9 BL9 (conditional_allocation does not reach FM; BL9 = non-negativity) | CONFIRMED from source (resolve_bucket_allocation consumed in main.py:2317 only; INV6/M-C3 triggers = negative partitions, :2264-2291) — not re-derived, verified |
| Dual daily-loss memory (ONE limit, both sides move) | CONFIRMED live at :1302-1314: limit = 0.03 × current `_total`; base = Σ(pnl_delta) via the indexed date column; ≈₹296 at the July book — never breached (0/23 outcomes, E4) |
| W10 double-subtract (July :211) | **CLOSED-confirmed in current SQL**: `get_daily_realized_net_pnl` sums pnl_delta only (state_store.py:2542-2548, E4 contract in the docstring); the July-audit line is stale for this reader |
| M-C1 live-seed carryover | Verified in code (compute_live_seed) and in data (INIT values consistent with post-close re-seeds; no phantom drift on any retained SYNC) |
| RMS/GTT closes pass costs=0 (July :212) | For CHECK1/CHECK4: E4-closed (P5.4). For `cnc_gtt_monitor` finalize: the path has never run live (0 delivery closes in the live DB) — stands as LATENT |
| K-ladder #11 (drift thresholds vs ~₹9.9k book: soft 10%, hard 25.3%) | Values re-read unchanged (₹250/1,000/2,500); DECIDED do-not-retune (28-Jul); this phase adds: those rungs listen only to sources measured at delta 0.0 — see seam |
| B3/G5 (daily-loss blind to unrealized overnight drawdown) — OPEN Q3 | Not re-litigated; noted: the B-1 unrealized-MTM term covers OPEN intraday trades when fresh (45s staleness gate, realized-only fallback); the overnight-delivery case remains Rama's Q3 |
| R10 (conditional-allocation flip, 4-Aug board item) | Confirmed BUILT-not-flipped (`resolve_bucket_allocation` unit-tested, flag false); the 30% positional strand TODAY / 70% intraday strand POST-FLIP asymmetry is R10's documented subject |
| FIX-075 top-up | TOP_UP rows = 7 all-time (June cluster + the one P4-cited firing) — consistent |

### P6.5 Open questions (not guessed into findings)

- **OQ-P6-1:** The 06-Jul `expected=0.00` G3 pair — did G3 run before initialize() then, and
  can it still (startup ordering today puts initialize at :2376, reconciler start later —
  looks closed by ordering, not proven). One boot-sequence read settles it; LOW.
- **OQ-P6-2:** INIT 63 vs SYNC 32 — assumed mid-day restarts + holidays; a per-day partition
  would prove it. Not chased.
- **OQ-P6-3 (= the redesign doc's Q3, restated for Rama):** is the daily-loss limit's
  blindness to unrealized OVERNIGHT drawdown a scope choice or a gap? Decides whether
  delivery positions can breach 3% unnoticed. Owner: Rama.

### P6.6 SEAM SUMMARY — can a wrong capital picture drive the kill ladder to act, or fail to act (P7's starting point)

**Fail-to-act: PROVEN.** The kill ladder's capital-drift inputs are exactly three escalating
sources, and each is structurally quiet: FM9 (measured delta 0.0 on every retained day —
post-seed by ordering), CHECK7 (internal-vs-internal, 0 rows ever), bucket_overflow
(sync-window only, 0 ever). G3 — the only continuous broker-truth check — is INFO-only to
the ladder by DH1, tolerance-widened by FIX-182, and throttled; the T2 window proved a real
−₹637.6 divergence crosses three sessions without any of soft/hard/alert firing. **A wrong
capital picture therefore cannot summon the kill ladder — the ladder is insulated from
broker truth in both directions** (it also cannot false-fire on phantom drift, DH1's stated
rationale). **Act-on-wrong-picture: the remaining capital triggers into the kill are the
3-balance invariant (BL-9), commit-failure (BL-4) and the daily-loss breach — all internal
computations over the same possibly-wrong total (a seed-absorbed total shrinks the 3% limit
proportionally: after the T2 absorb the daily-loss ₹ limit silently moved 9,997→9,360 ×3% ≈
₹300→₹281, i.e. a wrong-but-conservative direction THIS time; an unbooked PROFIT would
loosen it instead).** What P7 inherits: audit the kill ladder knowing (1) its capital rungs
have NEVER fired (IA-P6-06 census — every trigger zero), (2) its drift inputs are
structurally near-zero, so the ladder's real-world firing surface is the SCHEDULED kills +
operator paths (the K-ladder record), and (3) the Q4/30-Jul linkage stands: HARD_KILL's
flatten is delivery-blind from T+1 (G3/G4 of the redesign doc) with the buy-day product
filter a dated pre-4-Aug commitment — noted for P7, not audited here.

**Phase 6 done** = the sting chain traced end-to-end in code and PROVEN with the T2 window
as the live experiment (three blindness layers, each measured; the seed absorb measured to
the rupee); the internal-vs-broker invariant confirmed enforcement-only (CHECK7 internal;
the single escalating broker comparison structurally post-seed at measured delta 0.0);
reservation-ledger integrity measured (10 true leaks Σ₹1,628, all June, 0 since; the
205/205 committed-rid residual disproving the ledger's own closed-sum contract); the
isolated-DB/shared-cash seam mapped with rupee-exact evidence; 16 knobs units-clean; the
P6→P7 seam written (fail-to-act proven; act-on-wrong-total bounded and direction-analyzed);
committed incrementally; ⛔ nothing fixed, nothing pushed, FIX-182 untuned, the 3-Aug/4-Aug
sequence untouched.
*(Phase 7 — kill/safety ladder — appends below this line.)*

---

## PHASE 7 — STATE → KILL / SAFETY (the kill ladder, its firing surface, and the P7→P8 seam)

### P7.0 Measurement window & system state

| | |
|---|---|
| Session window | **Sat 01-Aug-2026 ~09:46 → ~10:3x IST** |
| Measurements taken | 01-Aug **~10:0x–10:1x IST**, from the VM (`mode=ro` DB reads + log greps; zero writes) |
| Deployed SHA (VM bare) | **`297b587`** — unchanged since P1 (same session) |
| PC tree read | `0a9b469` = `297b587` + 9 docs-only commits ⇒ code read == deployed |
| Service | `inactive`; `kill_switch_state` row = **SOFT_KILL / circuit_breaker_force_close_15:15 / 31-Jul 15:15 / order_monitor** — the routine scheduled kill persisted over the weekend BY DESIGN, will clear at Monday's 08:15 `clear_stale_state` |
| Primary windows | **23 retained logs = 01-Jul → 31-Jul** (the full retained history); `system_events` KILL_AUTO_CLEARED **28 all-time**; #11's prior measure (29-Jun→28-Jul, 1.54M lines) as the KNOWN baseline |
| Config ground truth (VM == PC) | `SCHEDULED_KILL_REASONS = {circuit_breaker_force_close_15:15, EOD_SQUAREOFF}` (code constant, kill_switch.py:114-117) · `circuit_breaker {force_close "15:15", max_api_failures 3}` · KS7 `api_failure_threshold 3, enable_auto_trip true` · drift ladder ₹250/1,000/2,500 ×3-cycle (#11 CLOSED — not reopened) · `emergency_exit_buffer_pct 0.01` · systemd `RestartPreventExitStatus=3 4` (deploy/systemd/trading-system.service:35, no SuccessExitStatus) |
| Scope guard | 3-Aug/4-Aug untouched; nothing fixed; the buy-day filter and GTT-verify NOT built (owed, out of scope); #11 values not re-litigated; reconcile internals deferred to P8; July audits read-only |

### P7.1 The kill topology as verified (current tree == deployed)

**One switch, three states** (`capital/kill_switch.py`): INACTIVE / SOFT (blocks entries,
allows exits — `is_active("entry")` ✓ / `"exit"` only on HARD, :472-488) / HARD (blocks all +
dispatches the async flatten). Persist-first (KS9, single-row `kill_switch_state`, timed with
a ≥1s WARN — 0 firings); publish+alert outside the lock (M-C4); scheduled kills alert WARNING,
emergency CRITICAL (SK-B). **Clears:** `clear_stale_state` at boot wipes EVERY prior-day kill
(headless guarantee, audited to system_events); `auto_clear_scheduled_kill` clears a SAME-day
scheduled kill only when 0 open positions; HARD never auto-clears same-day ⇒ HALT (exit 4,
`main.py:1901`; same-day emergency SOFT ⇒ HALT too; the unit's `RestartPreventExitStatus=3 4`
makes exit 4 a one-shot `failed`, no loop — **K1 re-confirmed from the repo unit file**,
VM-side verified 21-Jul). **Production callers measured (repo census, tests excluded):
17 soft_kill sites, 6 hard_kill sites** — the six HARD: monitor api×3 cb (main.py:710-725),
drift HARD (drift_handler.py:231), FM BL-4 commit-fail (:982) + BL-9 invariant (:2329),
placer persist-fail-post-placement (order_placer.py:1534) + exit-ladder final failure
(:3792). **The HARD flatten** (M-C8): async single-flight non-daemon worker (inline fallback
on thread-exhaustion), per-trade: cancel resting SL/TGT with terminal-status write guards
(Bug E) → reverse-aware close from CURRENT broker net (`determine_close_direction`, re-derived
per retry) → marketable LIMIT (LTP∓1%, tick-snapped) with MARKET fallback → conditional
EXITING write → LAYER A sweep flattens any residual broker position (H-5 product-mapped
intent) → retry 5/15/45s to a 2h deadline → per-trade CRITICAL alerts (5-min dedup).
`record_api_failure`: whitelist = BrokerTimeoutError + BrokerRateLimitError only (FIX-191);
BrokerAuthError → the 1/hr actionable IP-403 alert, never counted.

### P7.2 Headline re-measurements (mandated)

**(a) THE RUNG CENSUS — every kill rung classified, with its firing history (the phase's
core deliverable).** Window: the full retained history 01→31-Jul; #11's 29-Jun→28-Jul measure
as corroboration. **Fired ever: exactly TWO rungs.**

| Rung (reason / trigger) | Class | Verdict |
|---|---|---|
| 15:15 `circuit_breaker_force_close_15:15` (order_monitor cb) | SOFT, scheduled | **FIRING — 20/20 trading days in window**; persists overnight by design; 28 lifetime auto-clears audited |
| `EOD_SQUAREOFF` (eod fire step 2) | SOFT, scheduled | REACHABLE, **0 in window** — fires only when nothing is already active, and the 15:15 kill always was; its resume-half (step 8) equally unexercised in window |
| CHECK9 `MISSING_EXITS` (reconciler :2594) | SOFT, emergency | **FIRED ONCE — the only emergency kill in the retained history** (BANSALWIRE, 01-Jul 11:31, see (d)) |
| `daily_loss_limit_breached` (FM cb → main:794) | SOFT, emergency | UNFIRED-BUT-REACHABLE — pure algebra on realized Σpnl_delta ≤ −3%·total (≈−₹296); worst day ever −₹56.47 (margin ₹243); its position-close leg (late-bound EodSquareoff.fire_now) has never run |
| `Auto-trip` (KS7, 3× transient) | SOFT, emergency | UNFIRED-BUT-REACHABLE — narrowed to Timeout+RateLimit (FIX-191, after the 23-Jun false trip); needs a genuine connectivity outage; window transients: 5 non-consecutive |
| `orphan_order` (monitor orphan cb) | SOFT, emergency | UNFIRED-BUT-REACHABLE — the IA-P5-02 loud variant / H-15 confirm are its inputs; 0 ever |
| Reconciler auth×3 (RC12 :708) · token-expiry (main:2875) · clock-skew (main:361) | SOFT, emergency | UNFIRED-BUT-REACHABLE (token death / NTP disaster); clock-skew carries the M-K4 KNOWN (a second `configure()` silently disarms it) |
| LIVEFEED trio (:303/:474/:648) | SOFT, emergency | UNFIRED; QUEUE_FULL/CONSUMER_DEAD ~unreachable while the tick feed is dormant (no ticks, no consumer load); RECONNECT_EXHAUSTED reachable on a WS outage — a feed nothing consumes can halt entries (noted; ⛔ feed thread stays closed) |
| `cnc_gtt_monitor` kills (:695) | SOFT, emergency | UNFIRED — arms with delivery |
| `system_manager_eod` (18:45 cron :914) | SOFT, emergency | UNFIRED in window; fires against an already-exited service (17:35) ⇒ would persist to next boot, cleared as prior-day |
| `scripts/reconcile_pnl.py:276` | SOFT | **UNREACHABLE — dead script, never scheduled** (July-audit :215 KNOWN, stands) |
| Drift SOFT / SOFT_ESCALATED / HARD (drift_handler) | SOFT+HARD, emergency | **UNFIRED-BECAUSE-STRUCTURALLY-NEAR-UNREACHABLE** — IA-P6-01 carried: the three escalating sources measure delta 0.0 (post-seed FM9), internal-only (CHECK7), sync-window-only (bucket overflow). The ladder's own rungs cannot be summoned by real broker divergence |
| Monitor api×3 → HARD (`circuit_breaker_api_failure`) | HARD, emergency | UNFIRED-BUT-REACHABLE — sustained non-auth/non-timeout broker failures; includes the client-side BrokerRateLimitError class (July :176 KNOWN) |
| FM BL-9 invariant · BL-4 commit-fail · placer persist-fail · exit-ladder exhaustion | HARD, emergency | UNFIRED, reachable ONLY by internal bug / DB failure / broker-reject cascade — 0 ever, each (IA-P6-06 census) |

**⇒ The definitive answer to "can any capital-LOSS condition reach a kill": exactly one path
— the realized daily-loss SOFT rung (3% of current total, post-close, fm_ledger-based),
reachable and never fired. No broker-truth divergence can reach any rung (IA-P6-01), no
UNREALIZED loss reaches any rung (the B-1 MTM term gates new ENTRIES only), and every HARD
rung is bug-triggered rather than loss-triggered.** The last-line layer is armed against
internal corruption and connectivity death; it is NOT armed against losing money at the
broker beyond the single realized-3% line.

**(b) HARD_KILL flatten scope vs the Q4/Q6 decision — re-verified at `297b587`: the owed
state is exactly as recorded, and one NEW gap found (no post-flatten verification).**
Current code: the local-pass SELECT has **no product filter** (:1471-1480, every
OPEN/PARTIAL/PENDING_FILL trade); product NULL → INTRADAY **silently** (:1510 — Q6's owed
CRITICAL is not built); the LAYER A sweep flattens **any** non-zero `positions()` entry
(:1567-1617, H-5 product-mapped intent :1588-1590) ⇒ on a delivery BUY DAY both sites would
sell the position Rama ruled must survive; from T+1 both are holdings-blind (the Q4-2B
accident). **The owed delta (Q4 §9, confirmed still-owed):** restrict both sites to
`product in ("MIS","CO")` (the EOD6 vocabulary) + CRITICAL-on-NULL-fallback — a dated
pre-4-Aug commitment, ⛔ not built here. **NEW (G lens): the flatten verifies PLACEMENT, not
FLATNESS** — a trade leaves the failed list when its exit order is ACCEPTED
(broker_order_id non-empty); `succeeded = attempted` by construction (:1712-1714) and the
completion CRITICAL says "all N attempted position(s) flat" with no post-flatten
`get_positions()` assertion. A marketable-LIMIT exit that rests unfilled (gap through the
1% band, circuit lock) counts as success; the de-facto verifier is the reconciler's
stuck-EXITING path 30 minutes later. → IA-P7-01.

**(c) The scheduled kills — the real firing surface — audited, with the T2 window as a live
natural experiment.** The 15:15 breaker: SOFT-only, closes nothing (the 30-Jul B1 refutation
re-confirmed at source — `is_blocked("exit")` only on HARD); carried by the order_monitor
poll thread (`_check_force_close` → main.py:684-700 cb) — a single carrier, bounded because
every monitor-death path also fires HARD_KILL (P5); fired 20/20 window days at 15:15:00±;
persists overnight by design; cleared at each 08:15 boot (24 "ACTIVE AT STARTUP" sightings /
28 lifetime KILL_AUTO_CLEARED audits, recent 4 all = the 15:15 kill). The 15:17 EOD fire:
sets its own kill only if none active (**0× in window** — the breaker always won),
step-8 resume gated on `_we_set_soft_kill` only, then RESET_PNL (the 32 ledger rows).
**Delivery exemption LIVE-PROVEN by T2:** on buy-day 29-Jul the five CNC positions sat in
`positions()` through the 15:15 kill (closes nothing), the 15:17 MIS/CO-filtered pass
(EOD6/FIX-015), and the FIX-182 residual sweep (CNC excluded before the human check even
runs) — **5/5 survived to be carried overnight, all three scheduled sites behaving exactly
as designed** — while the same-day EMERGENCY path (HARD flatten, filter absent) would have
sold them. The asymmetry the Q4 decision exists to close, measured in one window.

**(d) K1 + the one emergency firing, reconstructed end-to-end.** K1 re-confirmed fresh:
`RestartPreventExitStatus=3 4` in the repo unit (:35, no SuccessExitStatus ⇒ exit 4 =
one-shot `failed`); HALT = HARD any-day, emergency-SOFT same-day (startup_checks :242-274 →
main.py:1901 → exit 4); scheduled kills auto-clear (same-day needs 0 open positions);
`resume()` unused in the entire window (0 RESUMED lines). **The BANSALWIRE firing (01-Jul
11:31:26):** CHECK9 found the local SL order absent at the broker → CRITICAL → soft_kill
(MISSING_EXITS) fired within 15ms — **the detection and kill rungs WORKED** — but the
emergency close itself was **REJECTED by Zerodha: "Invalid tags: max allowed tag length
is 20"** (`reconciliation_log`: `emergency_exit=failed(...)`) — the last line of defense
failed to place on its only live firing, for a mundane validation bug. The fix is the
01-Jul adapter chokepoint truncation (P5-verified at :601-602, since idempotent for every
caller). No same-day restart followed ⇒ the K1 HALT window was not entered; the service
continued under SOFT (entries blocked) as designed. → IA-P7-02.

**(e) Config-vs-code + units at this layer (F/H lens).** Consumed and correct: force_close
"15:15" (20 firings) · max_api_failures 3 · KS7 threshold 3 / auto_trip true ·
emergency_exit_buffer_pct 0.01 (fraction, ∓LTP ✓) · drift trio (₹, #11) · retry ladder
5/15/45s + 2h deadline + 5-min dedup + 1h IP-403 throttle (code constants, each consumed).
**One dead knob found: `eod_squareoff.auto_resume_kill_switch: true`** — consumers = config
schema + tests only; the step-8 resume is gated on `_we_set_soft_kill` alone
(eod_squareoff.py:530-538) — the YAML claims a configurability that does not exist
(IA-P3-02 family, instance 8) → IA-P7-04. (The G7 `backoff_sequence_sec` "then soft_kill"
dead-config sibling was already IA-P5-08.) Units: no fraction-vs-percent defect (width:
every knob named in this cell).

### P7.3 NEW findings

---
**IA-P7-01**
- **WHAT:** HARD_KILL's flatten claims an invariant it never verifies: success is counted at
  order ACCEPTANCE (empty-broker_order_id is the only non-exception failure), the final
  report hard-codes `succeeded = attempted`, and the worker's completion CRITICAL announces
  "all N attempted position(s) flat" without a closing `get_positions()` check. A marketable
  LIMIT that rests unfilled (gap through the ∓1% band, circuit freeze — precisely the
  regimes in which HARD_KILLs happen) reads as success; the trade sits EXITING until the
  reconciler's stuck-EXITING pass ~30 minutes later becomes the de-facto verifier.
- **EVIDENCE:** kill_switch.py:1549-1560 (accept == success), :1712-1714 (succeeded :=
  attempted), :1160-1169 (the "flat" log); no positions() read exists after the retry loop
  (width: the whole `_exit_all_trades_indestructible` body). Retry re-derivation (H-4)
  applies only to trades whose PLACEMENT failed.
- **CLASS:** Safety / Invariant-coverage. **NEW** (the Q4 doc narrowed the invariant's
  SCOPE; nobody checked whether it is VERIFIED at all).
- **ROOT CAUSE:** the loop's unit of work is "an exit order placed", not "a position gone";
  the two coincide except in the market conditions a HARD_KILL selects for.
- **RECOMMENDATION (described, ⛔ not applied):** one closing sweep — after the loop, re-read
  positions(); any residual (product-filtered once Q7 lands) re-enters the retry set or
  escalates the existing per-trade CRITICAL. Pairs naturally with the owed buy-day filter
  edit. Careful-loop.
- **SEVERITY-BY-IMPACT:** MED — latent (HARD has never fired); when it fires it will be in
  exactly the conditions that widen the placement-vs-fill gap, and the operator will read
  "all flat" from a claim nothing checked.

---
**IA-P7-02**
- **WHAT:** The emergency-kill layer's entire live evidence base is ONE firing, and on it
  the last line failed: CHECK9→soft_kill worked (01-Jul BANSALWIRE, 15ms detect-to-kill),
  but the emergency close was rejected by the broker for tag length — the class "last-line
  order rejected by mundane validation" has exactly one datapoint, one fix (the 01-Jul
  chokepoint truncation, P5-verified), and no rehearsal path (the G13 drill was mocked; the
  flatten and the emergency closes are production-unexercised since).
- **EVIDENCE:** the 01-Jul log triplet + `reconciliation_log` row
  (`emergency_exit=failed(Zerodha rejected order: Invalid tags…)`); rung census (a): 2 of
  ~20 rungs have ever fired; HARD_KILL ACTIVATED = 0 all-time (window + #11's 1.54M-line
  measure).
- **CLASS:** Reachability / Safety-posture. **KNOWN-COMPOSED → sharpened** (the incident
  produced the 01-Jul fix; that its firing was the ONLY live exercise of any emergency
  close, and what that implies about the rest of the unfired surface, was not registered).
- **ROOT CAUSE:** emergency paths are exercised only by emergencies; nothing rehearses them
  (paper cannot: its broker never rejects — the P5 constant-branch class).
- **RECOMMENDATION (described):** none buildable in-audit; the honest posture line is that
  every unfired rung in table (a) carries BANSALWIRE-class risk — correct wiring, unproven
  last mile — and any future drill design should target the flatten/emergency-close bodies,
  not the kill-state machine (which IS proven, 21 firings).
- **SEVERITY-BY-IMPACT:** LOW-MED as a finding (the known instance is fixed); HIGH as
  context — it is the phase's base-rate calibration.

---
**IA-P7-03**
- **WHAT:** Kill enforcement is consultation-based, and the reconciler's own order-placing
  paths mostly do not consult it: `is_active` is read at exactly two reconciler sites
  (inflight-orphan :2004, recovery :3717) — G5b crash-recovery SL placement, the CHECK9
  emergency close, and the duplicate-exit cancels place/cancel regardless of kill state.
  Under an active HARD_KILL the state machine can chain: flatten marks EXITING → exit rests
  unfilled (IA-P7-01) → stuck-EXITING resurrects the trade to OPEN after 30 min → G5b
  places a fresh SL — while the single-flight flatten worker still owns the position. Two
  writers, broker-truth-mediated, never exercised together.
- **EVIDENCE:** the is_active grep (2 hits, width: order_reconciler.py); G5b places via
  adapter directly (RC18); the flatten's Bug-E cancel + re-derive would contend with a G5b
  SL on the next retry.
- **CLASS:** Architecture / Coupling. **NEW** (the individual pieces are each designed;
  their composition under an active HARD is unexamined). ⛔ M-C4/M-C8 family — noted, not
  re-opened.
- **RECOMMENDATION (described):** decide and document the intended precedence (simplest: the
  reconciler's protective placements early-return under `is_active("exit")` — the flatten
  owns the book during HARD); verify against the IA-P7-01 closing-sweep idea so the two
  don't fight.
- **SEVERITY-BY-IMPACT:** LOW-MED latent — requires HARD (never fired) ∧ a resting exit ∧
  30 minutes; the failure shape is duplicate exit orders, which Layer-2/oversell nets then
  have to catch.

---
**IA-P7-04**
- **WHAT:** `eod_squareoff.auto_resume_kill_switch: true` is dead config — the schema
  defines it (config_loader.py:467), tests assert it, and the YAML comments promise it, but
  `eod_squareoff.py` never reads it: step-8 resume is unconditional-when-we-set-it
  (:530-538). The real semantics (resume only the kill EOD itself set; never the 15:15
  breaker's) are GOOD — the knob just doesn't govern them.
- **EVIDENCE:** repo-wide grep `auto_resume_kill_switch` = config_loader + tests only.
- **CLASS:** Config-vs-code. **NEW** (IA-P3-02 family, instance 8).
- **RECOMMENDATION (described):** delete the key (and fix the test that asserts a
  behavior-coupling that does not exist), or wire it. LOW.
- **SEVERITY-BY-IMPACT:** LOW — belief hazard only; the behavior itself is correct.

---
**IA-P7-05** (hygiene bundle, one ID)
- (a) The 15:15 breaker fires ON the order_monitor poll thread — the soft_kill persist +
  WARNING send run inside the poll cycle (M-A2 bounds the send at 8s; M-C4 family, noted
  not re-opened). Single-carrier, bounded by the monitor-death→HARD coupling.
- (b) LIVEFEED_RECONNECT_EXHAUSTED can halt entries on behalf of a feed nothing consumes
  (dormant-by-decision, 0 tokens) — a false-positive-shaped rung; ⛔ the feed decision is
  gated, recorded only.
- (c) `system_manager_eod`'s 18:45 kill always lands on an already-exited service (17:35
  self-exit) ⇒ its only effect is a persisted row cleared as prior-day at the next boot —
  a rung whose action cannot reach a running process on a normal day.
- (d) The K1 documentation debt stands (RUNBOOK exit-4 text wrong; resume.sh unnamed in
  incident docs — K1 registered 28-Jul, unchanged; `resume()` unused ever in window).
- (e) `_persist_state` timing: 0 slow-warns against 21 live kills in window (the 25-Jul
  instrumentation is alive and quiet).
- **CLASS:** Documentation / Posture. **SEVERITY:** LOW.

### P7.4 KNOWN items re-verified — status updates (no re-numbering)

| Known ID | Status on the current system (fresh evidence) |
|---|---|
| Q4/Q6/Q7 (HARD_KILL flattens only MIS; buy-day filter pre-4-Aug; NULL→flatten+CRITICAL) | **CONFIRMED STILL-OWED at `297b587`**: no product filter at either flatten site (:1471-1480, :1567-1617), NULL→INTRADAY silent (:1510). The decision's premise re-verified line-by-line; the filter's exact delta = the Q4 §9 one-liner |
| Q4-2B (T+1 holdings-blindness = the protective accident) | STANDS — the sweep reads `get_positions()` only; the ordering constraint (filter BEFORE anything holdings-aware) remains binding |
| K1 (emergency kill + same-day restart ⇒ exit 4, no reboot) | **RE-CONFIRMED from repo source** (unit :35; startup_checks :242-274; main :1901); scheduled-vs-emergency partition = the 2-literal frozenset; window evidence: 24 startup-active sightings / 28 auto-clears / 0 HALTs / 0 resumes |
| K2 (06-Jul ₹10k drift, expected=0.00, unresolved) | Unchanged; P6's IA-P6-07a carries it; not re-opened |
| K3 (sub-₹250 drift invisible at DEBUG ⇒ ladder un-retunable on evidence) | STANDS — #11 §2a's reopen trigger (c) remains unobtainable |
| #11 (ladder values CLOSED; single-sample SOFT/HARD; 3-cycles gates only LOG_ONLY→SOFT) | Values re-read unchanged; single-sample semantics re-confirmed at drift_handler.py:169-183; ⛔ not re-litigated |
| 15:15 = SOFT, closes nothing; 15:17 excludes CNC at all three sites (30-Jul B1) | **LIVE-PROVEN via T2** (P7.2c): 5/5 CNC survived buy-day through all three scheduled sites |
| HARD_KILL never fired (Q4 base rate) | RE-MEASURED: 0 in the full retained history (01→31-Jul) + #11's 1.54M-line window |
| G13 (kill drill was mocked) | STANDS — composed into IA-P7-02's posture line |
| FIX-191 auto-trip whitelist (23-Jun false trip) | Verified at :741-775; the breaker is connectivity-only; BrokerAuthError → IP-403 alert path (0 firings) |
| M-K4 (second time_authority.configure() silently disarms the skew kill) | STANDS (July-audit KNOWN); the clock-skew rung's reachability caveat in table (a) |
| KS9 persist-first + busy_timeout posture (25-Jul memory) | Instrumentation verified live and quiet (0 slow-warns / 21 kills); `busy_timeout` untouched as decided |
| IA-P6-01/-06 (capital rungs structurally quiet; alarm surface unexercised) | CARRIED — table (a) inherits the P6 verdicts unchanged, now placed inside the full rung census |
| July-audit :215 (`reconcile_pnl.py` dead/unscheduled) | STANDS — its soft_kill site is the census's one UNREACHABLE rung |

### P7.5 Open questions (not guessed into findings)

- **OQ-P7-1:** After BANSALWIRE's rejected emergency close (01-Jul 11:31), by what path did
  the position actually flatten that day? (Predates several fixes; archaeology only — the
  chokepoint fix is verified regardless.)
- **OQ-P7-2:** The 12 window log lines matching 'flatten' with 0 HARD_KILLs — assumed
  wording from non-kill modules (oversell/eod text); not itemized. A 5-minute grep settles
  it if ever needed.
- **OQ-P7-3:** Should the reconciler's protective placements consult `is_active("exit")`
  under HARD (IA-P7-03's precedence question)? Design decision — Rama's, post-4-Aug.

### P7.6 SEAM SUMMARY — can a kill leave state the reconciler mis-reads (P8's starting point)

**Yes — three specific shapes, none exercised.** (1) **EXITING is kill-authored state with a
reconciler-owned afterlife:** the flatten marks trades EXITING on placement (not fill); the
stuck-EXITING pass resolves them ≥30 min later by broker truth (flat → CLOSED_MANUAL +
release; still-held → resurrect OPEN) — under an active HARD that resurrection re-arms G5b
against the flatten's single-flight worker (IA-P7-03), and the CLOSED_MANUAL finalize prices
the exit via the same get_trades/entry-proxy chain whose lag P5 measured (IA-P5-03). (2)
**The flatten's success claim is placement-level (IA-P7-01):** P8 must treat a
"flatten complete" log as unverified — the reconciler IS the verification layer, 30 minutes
late, and nothing labels it as such. (3) **A kill's cancel side writes CANCELLED into orders
under terminal-guards** (Bug E, correct) but its EXITING trades still hold released-vs-used
capital state that CHECK1/stuck-exiting finalize with `costs` via the E4 path — the
kill→reconcile capital handoff has never run end-to-end (0 HARD ever). Also handed to P8:
the 15:45 `reconcile_positions` job's delivery-blindness (F1/step-1, the 30-Jul doc's G1 —
the false MISSING_AT_BROKER CRITICAL from T+1) is P8's headline inherited item, now with the
P7 addendum that the SCHEDULED kills are delivery-correct while the EMERGENCY path awaits
its filter — reconciliation must not "fix" what the filter's absence merely exposes.

**Phase 7 done** = every rung classified with firing history (2 fired ever: the daily 15:15
+ one emergency; 3 structurally near-unreachable, all capital-drift; 1 dead; the rest
reachable-unfired with the BANSALWIRE base-rate caveat); the HARD flatten traced against
Q4/Q6 (owed state confirmed at the deployed SHA; placement-vs-flatness verification gap
found); the scheduled kills audited with the T2 natural experiment proving the delivery
exemption live; K1 re-confirmed from repo source with fresh window evidence (24/28/0/0);
"can a capital loss reach a kill" answered definitively (one realized-3% path, nothing
else); committed incrementally; ⛔ nothing fixed, nothing pushed, the 3-Aug/4-Aug sequence
untouched.
*(Phase 8 — reconciliation / EOD jobs — appends below this line.)*

---

## PHASE 8 — STATE → RECONCILIATION / EOD (the truth arbiters, the EOD chain, and the P8→P9 seam)

### P8.0 Measurement window & system state

| | |
|---|---|
| Session window | **Sat 01-Aug-2026 ~10:06 → ~10:5x IST** |
| Measurements taken | 01-Aug **~10:2x–10:3x IST**, from the VM (`mode=ro` DB reads + log greps + one report-file read; zero writes) |
| Deployed SHA (VM bare) | **`297b587`** — unchanged since P1 (same session) |
| PC tree read | `2859d48` = `297b587` + 10 docs-only commits ⇒ code read == deployed |
| Service | `inactive`; the 15:45 job self-skips on weekends (`is_broker_api_available` gate, verified :362-365) |
| Primary windows | `position_reconciliation` **15 rows all-time** / `eod_broker_reconciliation` **16 rows** (16→31-Jul) / `eod_verification` **31 rows** / EOD-chain heartbeats: reconcile_positions 34 (4 non-SUCCESS) · eod_broker_reconcile 16 (0) · eod_verify 32 (**0**) · eod_cleanup 36 (6) · system_manager_eod 38 (6) |
| Scope guard | 3-Aug/4-Aug untouched; nothing fixed; the redesign audited NOT built; F1/F4 not fixed; the authoritative flip not touched; report internals deferred to P9; July audits read-only |

### P8.1 The reconciliation surface as verified (current tree == deployed)

Four arbiters compare DB/memory/broker: **(1)** the in-service 15s battery (P5-read in full:
CHECK1..9, G3, G5b, recovery prepasses, duplicate-exit net, stuck-EXITING); **(2)** the 15:45
`scripts/reconcile_positions.py` cron — `kite.positions()["net"]` vs OPEN/PARTIAL trades
(qty>0, signed, `created_at <= date` cumulative), verdicts OK / ORPHAN_AT_BROKER /
MISSING_AT_BROKER / QTY_MISMATCH, exit 2 on mismatch + ERROR Telegram, exit 1 + heartbeat-only
alert on broker-fetch failure (:244-254 — the script's own alert fires on mismatch only);
**(3)** the 15:58 `scripts/eod_broker_reconcile.py` shadow — own creds, five dimensions
(positions/orders/pnl REQUIRED · ledger · margin reliability-gated), never-false-VERIFIED
roll-up, persists a per-day verdict row + the eod_verify shadow comparison, INFO alerts while
`eod_reconcile.authoritative: false`; **(4)** the 15:55 `eod_verify` (M-SC1 KNOWN: exits 0 +
SUCCESS heartbeat even on ISSUES). EOD chain jobs are independent cron processes — **abort
does NOT propagate** (measured: 29-Jul reconcile_positions FAILED exit 2 while the 15:50/
15:55/15:58/18:45 jobs all still ran). The kill-adjacent afterlife: `_check_stuck_exiting`
(:1477-1528) resolves EXITING trades older than 30 min against real broker positions — flat →
`_check1_manual_close` finalize; held → revert OPEN + WARNING (:1530-1585).

### P8.2 Headline re-measurements (mandated)

**(a) F1 — the delivery-blind 15:45 job: all THREE faces now measured in one window, and the
sell-day face is NEW evidence.** Source confirmed: positions()-only (:158-168, no holdings()
anywhere — width: the whole file), system side has no product filter (:181-189) ⇒ a REAL
delivery holding produces the false **MISSING_AT_BROKER** CRITICAL daily from T+1 (:267-269,
the 30-Jul doc's B5, confirmed at source). Measured via T2: **29-Jul buy-day = 5×
ORPHAN_AT_BROKER (+3) exit 2 · 30-Jul T+1 = zero rows, exit 0 (the empty-book silent face —
the green check that cannot go red, §0 of the design doc, re-confirmed in data) · 31-Jul
sell-day = 5× ORPHAN_AT_BROKER (−3) exit 2** — the sell-side face the design doc did not
predict (the CNC sells sat in positions() net at 15:45). All-time table: 15 rows, 3 firing
days, ALL T2; the 11 pre-T2 runs were empty-book successes (KNOWN §A1(6), re-measured). ⛔
Redesign posture re-stated: D-4(a) scope-to-intraday is the audited answer; the reconciler
must NOT learn holdings() before the buy-day product filter lands (the Q4 ordering
constraint) — reconcile must not "fix" what the missing filter merely exposes.

**(b) ⭐ THE AUTHORITATIVE-FLIP EVIDENCE STREAM IS DOUBLE-BROKEN — the phase's top NEW
finding.** (i) **`eod_verify`'s verdict has been stuck at PENDING for 18 consecutive trading
days**: last VERIFIED = **07-Jul**, PENDING every day since **08-Jul** — while its own
heartbeat reports SUCCESS **32/32, zero failures** (the M-SC1 exit-0 family, now with a
stuck verdict on top). (ii) The 15:58 shadow comparison consumes that lifecycle status as a
verdict: `ev_clean := (status == "VERIFIED")` (:293) ⇒ PENDING reads as "eod_verify
disagrees" ⇒ **`mismatch=1` on ALL 14 P1-VERIFIED days and 0 on the 2 ISSUES days** —
inverted noise, persisted daily, alerted at INFO, noticed by nobody. **The B3/G23 flip gate
("a clean shadow WEEK") is unservable while this holds — the gate's evidence column cannot
go clean.** The shadow verdicts themselves behaved correctly all window: VERIFIED 14 /
ISSUES 2 (29+31-Jul, exactly the T2 positions dimension, which also correctly folded in the
15:45 job's non-OK rows). → IA-P8-01.

**(c) The flagship never-false-VERIFIED job carries two structural soft spots of its own.**
(i) **The LEDGER dimension is vacuous**: `_local_capital_snapshot` returns
`invariant_ok=True` on BOTH paths (:455, :458 — "treat a readable ledger as OK") ⇒
`ledger_status` can NEVER be ISSUES while the docstring sells it as "fm_ledger 3-balance
invariant" — a green check that cannot go red inside the job built to abolish that class.
(ii) **`total` = the last fm_ledger row's `balance_after`** (:450-452) — which at every
15:58 run is the RESET_PNL row's **0.0** (measured: last ledger row 31-Jul 15:19 RESET_PNL
balance_after=0.0; second-last is a BUCKET-avail figure, never the capital total). Masked
today ONLY because the margin dimension is always NOT_CHECKED at 15:58 (the FIX-189
reliability gate); it arms the moment the job time, the margin window, or an in-session
authoritative run changes. → IA-P8-02/-03.

**(d) "A green check that only ran on an empty book is not a check" — the census,
generalised across the surface.** Checks that have EVER gone red on real input:
CHECK1 (44) · CHECK2-family (June storms + the T2 ten) · CHECK4/CHECK5 (June; AGARIND) ·
CHECK9 (once, BANSALWIRE) · the 15:45 job (3 days, all T2) · the 15:58 positions dimension
(2 days, T2). **Everything else is green-only or never-run**: CHECK6/CHECK7/CHECK8/
SYSTEM_OVERSELL/STUCK_EXITING/INFLIGHT_ORPHAN_FLATTEN/RECOVERY_* = 0 rows ever (7,804-row
census, P5); the duplicate-exit net, the EOD residual sweep flatten, G5b post-June — quiet in
the current era; the LEDGER dimension = cannot-go-red (c); eod_verify = stuck-PENDING (b);
paper reconcile_positions = all-OK by construction (:225-242); paper eod_broker_reconcile =
self-consistency mirror (honest label). The battery's real-input track record rests almost
entirely on the June storms and the T2 experiment.

**(e) HARD_KILL flatness verification — answered: it is NEVER positively verified at kill
time; the stuck-EXITING pass is the only positive verifier, 30 minutes late, and mislabeled
as recovery.** Characterised precisely: timeout = `stuck_exiting_timeout_minutes: 30`
(consumed ✓); runs inside the raw_positions guard (never blind); flat → CHECK1 finalize —
which prices the exit via the get_trades→entry-proxy chain (IA-P5-03) ⇒ **a kill-flatten's
booked P&L inherits the trades()-lag proxy class**; held → revert OPEN + WARNING — under a
still-active HARD that resurrect re-arms G5b against the single-flight flatten worker
(IA-P7-03, carried). 0 STUCK_EXITING rows ever — the verifier itself is production-untested.

**(f) The 30-Jul redesign doc, audited item-by-item against `297b587` (§H mandate).** Every
§A1 mechanism claim re-verified at source this session: (1) reconcile_positions
positions-only ✓ (a) · (2) eod_broker_reconcile has NO holdings/delivery dimension ✓ (grep
width: the file; its false-alarm face from T+1 would DUPLICATE F1's, 13 min apart — the
doc's two-jobs-one-axis point stands) · (3) **`CncGttMonitor._gather` (:422-455) merges
holdings() + CNC-filtered positions() with Y4 defer-on-failure ✓ — the correct reader
exists; ⚠️ ONE lift-review nuance found: the positions leg adds `abs(qty)` (:454), so a
sell-day CNC net of −3 ADDS 3 to held-qty — whether that double-counts against a
not-yet-settled holding on sell day is unmeasurable today (→ OQ-P8-2), and must be settled
before D-8 step 2 lifts this as THE shared definition of "held"** · (4) G3 ✓ (P6) · (5)
CHECK1/CHECK2 + ₹5,000 ✓ (P5/P6, B6 artefact verdict stands WITH the IA-P5-02 caveat) · (6)
position_reconciliation ✓ (now 15 rows/3 red days) · (7) FM9 hook ✓ (P6, delta 0.0×8) · (8)
Slice-2.5 scaffolding ✓ (P4/P5/P7). Hypotheses: B1 ✓ (P7, scheduled kills delivery-correct,
LIVE-proven via T2) · B2 ✓ (nothing persists a closing inventory — width: every reconcile
reader this session) · B3 = OQ-P6-3, open · B4 ✓ artefact (P4 prepass evidence) · B5 ✓
source-confirmed (a) · B6 ✓ sharpened (P6). Gaps G1-G7 all stand. **Verdict on
buildability: the redesign is buildable as stated EXCEPT (α) the flip gate needs IA-P8-01
fixed first (the shadow evidence stream cannot go clean), (β) its D-1 host job needs the
vacuous ledger dimension and the wrong `total` corrected (IA-P8-02/-03) before a delivery
dimension makes it MORE load-bearing, and (γ) IA-P6-03 stands — the deferred Phase-E
reservation-orphan direction cannot be built on per-rid ledger sums as stated.**

### P8.3 NEW findings

---
**IA-P8-01**
- **WHAT:** The authoritative-flip evidence stream is double-broken: (i) `eod_verify`'s
  persisted verdict has been stuck at PENDING for every trading day since 08-Jul (18 days;
  last VERIFIED 07-Jul) while its heartbeat reports SUCCESS 32/32 — the verdict-finalize
  path silently stopped; (ii) the 15:58 shadow comparison consumes that lifecycle status as
  a verdict (`ev_clean := status=="VERIFIED"`, eod_broker_reconcile.py:293) ⇒ the persisted
  `mismatch` flag reads **1 on every clean day, 0 on every ISSUES day** — structurally
  inverted noise — and the B3/G23 flip gate ("clean shadow week") is unservable.
- **EVIDENCE:** eod_verification per-date census (PENDING 18, boundary exactly
  07/08-Jul); eod_broker_reconciliation: mismatch=1 on all 14 VERIFIED days, 0 on both
  ISSUES days; heartbeats eod_verify 32/0-failures.
- **CLASS:** Correctness / Silent-failure. **NEW** (B3/G23 recorded the flip as "stuck in
  shadow, needs a clean week" — that the gate's own evidence column is broken, and since
  when, was not known).
- **ROOT CAUSE:** two composed: whatever stopped eod_verify finalizing on 08-Jul (the
  FIX-181/GICRE deploy era — → OQ-P8-1, archaeology), and the shadow reading a 3-state
  lifecycle field as a boolean verdict (the classify-by-the-wrong-field family).
- **RECOMMENDATION (described, ⛔ not applied):** treat PENDING as "no eod_verify verdict"
  (mismatch=NULL — the code already models None for absent), and separately find/fix why
  eod_verify stopped finalizing; only then can a shadow week mean anything.
- **SEVERITY-BY-IMPACT:** MED-HIGH for the flip programme (its gate cannot be satisfied and
  the INFO alerting hid that for 3+ weeks); LOW for daily safety (the shadow verdicts
  themselves behaved correctly).

---
**IA-P8-02**
- **WHAT:** The LEDGER dimension of the never-false-VERIFIED job is vacuous:
  `_local_capital_snapshot` returns `invariant_ok=True` unconditionally on both its paths
  (:455 readable-ledger, :458 exception) ⇒ `ledger_status` can never be ISSUES, while the
  module docstring advertises "fm_ledger 3-balance invariant" as a checked dimension.
- **EVIDENCE:** eod_broker_reconcile.py:441-458 + :165-167; 16/16 rows ledger=VERIFIED.
- **CLASS:** Reachability / Documentation — the green-check-that-cannot-go-red class,
  inside the job built against it. **NEW.**
- **ROOT CAUSE:** the standalone job has no live FM to ask; the honest "deeper invariant
  audit left to CHECK7" note in the comment never made it into the dimension's status
  (NOT_CHECKED exists and is the truthful value).
- **RECOMMENDATION (described):** report ledger=NOT_CHECKED (or compute the 3-balance sum
  from fm_ledger directly — one query); update the docstring either way.
- **SEVERITY-BY-IMPACT:** LOW-MED — no wrong verdict yet (CHECK7 territory is quiet), but
  the dimension's green is currently unfalsifiable, and the flip would promote it to a
  CRITICAL-grade claim.

---
**IA-P8-03**
- **WHAT:** `_local_capital_snapshot.total` = the LAST fm_ledger row's `balance_after` —
  semantically never the capital total (per-bucket avail for RESERVE/RELEASE/COMMIT rows;
  **0.0 for the RESET_PNL row that is last on every trading day by 15:19**). The margin
  dimension would compare broker.net against 0.0 — masked today ONLY by the FIX-189
  NOT_CHECKED gate at 15:58.
- **EVIDENCE:** :450-452; measured last-ledger rows 31-Jul: RESET_PNL balance_after=0.0
  (15:19), prior RELEASE_USED 6,532.89 (a bucket figure); margin NOT_CHECKED 16/16 rows.
- **CLASS:** Correctness (latent, gate-masked). **NEW** — and note the code COMMENT already
  documents fixing a previous always-raised bug here ("no such column: id" → silent 0.0);
  the fix made the query run, but the VALUE it fetches is the wrong semantic.
- **ROOT CAUSE:** fm_ledger's balance_after is per-entry-scope, not a running total; no
  ledger row carries "total".
- **RECOMMENDATION (described):** derive total the way rehydrate does (INIT/SYNC base +
  Σpnl_delta), or mark margin permanently NOT_CHECKED in this job and delete the
  pseudo-total. Must precede any in-session/authoritative use of the margin dimension.
- **SEVERITY-BY-IMPACT:** LOW today (fully masked); MED the day the margin window or job
  schedule moves.

---
**IA-P8-04**
- **WHAT:** F1's sell-day face: a CNC sell (from holdings) sits in `positions()` net at
  15:45 as a NEGATIVE quantity with no local trade ⇒ **ORPHAN_AT_BROKER (−qty)** — a third
  false-alarm face beyond the doc's buy-day ORPHAN and T+1 MISSING. Measured: 31-Jul, 5
  rows, broker_qty=−3 each, exit 2, ERROR Telegram.
- **EVIDENCE:** position_reconciliation 31-Jul rows; the 15:58 positions dimension
  correctly went ISSUES the same evening (double coverage of the same artefact).
- **CLASS:** Correctness (KNOWN-F1, widened). **KNOWN → SHARPENED** — the redesign's D-4(a)
  fix (scope to intraday product) covers this face too, PROVIDED the system-side filter
  also excludes the delivery trade's exit day; noted for the design review, not designed
  here.
- **SEVERITY-BY-IMPACT:** folds into F1's (a false CRITICAL per delivery lifecycle event —
  buy day, every held day, sell day: the full delivery lifecycle alarms daily somewhere).

---
**IA-P8-05** (hygiene bundle, one ID)
- (a) **F4 measured in production output**: `reports/system_manager/2026-07-31.txt` carries
  "⚠️ Kill switch: SOFT_KILL — needs deploy/resume.sh before market open" — fires EVERY
  evening against the persisted scheduled 15:15 kill, and the instruction is wrong on both
  sites (:698 nightly; :855's own-kill case ALSO auto-clears overnight under the headless
  guarantee — resume.sh is needed only for a SAME-day restart). KNOWN-F4, now
  frequency-measured (nightly) and widened (both sites overstate).
- (b) reconcile_positions' broker-fetch-ERROR path (exit 1) alerts only via the cron
  heartbeat FAILED alert — the script's own Telegram fires on mismatch only (:300).
- (c) The `--db`-after-heartbeat trap (⛔ memory rule) has the same shape here: `_cron_main`
  heartbeats to the LIVE DB before `main()` parses `--db` (:437-450).
- (d) The 15:58 pnl dimension inherits M-K1 (`get_today_closed_pnl` keys on
  DATE(updated_at) — July-audit :144, KNOWN): a later touch of a closed row makes a
  spurious next-day variance.
- (e) The positions dimension double-fires on the same artefact (its own diff + the 15:45
  flag) — consistent, but one T2-class event produces 2 dimensions' worth of ISSUES text.
- (f) eod_cleanup 6 / system_manager_eod 6 non-SUCCESS heartbeats all-time (incl. the
  15-Jul FK crash, KNOWN #09-adjacent) — chain independence means none of these stopped
  later jobs (measured 29-Jul).
- **CLASS:** Documentation / Posture. **SEVERITY:** LOW.

### P8.4 KNOWN items re-verified — status updates (no re-numbering)

| Known ID | Status on the current system (fresh evidence) |
|---|---|
| F1 (positions-only reconciler, delivery-blind) | CONFIRMED at source (:158-168, :181-189, :267-269); all three faces measured (buy +3 / T+1 silent / sell −3); MISSING_AT_BROKER remains REAL-but-latent for a genuine delivery holding — T2 could only produce the ORPHAN faces (isolated DB) |
| The 29-Jul "first real failure; 11 prior empty-book successes" | RE-MEASURED: now 15 rows / 3 red days (29/31-Jul + the 5-row history); 30-Jul's exit-0 was the empty-book silent face, exactly as the 30-Jul doc's §0 self-correction predicted |
| B3/G23 (authoritative flip stuck in shadow) | **SHARPENED → IA-P8-01**: the gate's evidence column is structurally broken (stuck-PENDING comparand since 08-Jul + inverted mismatch flag); a "clean shadow week" is currently impossible |
| M-SC1 (eod_verify exits 0 / SUCCESS heartbeat on ISSUES) | STANDS + WORSE: the verdict itself no longer finalizes (PENDING×18 since 08-Jul) while heartbeats stay SUCCESS |
| eod_verify dead P&L-variance branch (July :214) | Superseded in practice by the 15:58 job's pnl dimension writing `pnl_reconciliation` with correct columns (:304-312) — the dead branch stands, its function is duplicated correctly elsewhere |
| IA-P7-01/-03 (flatten verified late; reconciler places without kill consult) | CHARACTERISED in (e): stuck-EXITING is the only positive flatness verifier — 30 min late, CHECK1-proxy-priced, 0 rows ever, revert-side re-arms G5b under HARD |
| IA-P6-03 (Phase-E orphan detection unbuildable on per-rid sums) | STANDS — restated as redesign-buildability caveat (γ) in (f) |
| IA-P5-03 (CHECK1 entry-proxy pricing) | COMPOSED: the kill-flatten finalize path (stuck-EXITING → CHECK1) inherits it |
| The reconciler battery census (P5) + June storms | CARRIED unchanged; (d) generalises it into the green-only/never-run partition |
| CHECK2 HUMAN_ORDER / ₹5,000 / seed-absorb (P6) | CARRIED — the reconciliation layer is where the classification happens; nothing here re-litigated |
| ⛔ `scripts/*.py --db <copy>` memory rule | Shape re-confirmed in both P8 scripts (heartbeat-before-parse) |
| Q4 ordering constraint (filter before holdings-awareness) | RE-STATED as binding on D-8 step 2/4 and on any F1 fix — reconcile must not learn holdings() first |

### P8.5 Open questions (not guessed into findings)

- **OQ-P8-1:** What stopped `eod_verify` finalizing its verdict on 08-Jul? (The FIX-181/
  GICRE deploy era; the job heartbeats SUCCESS throughout.) One log-diff of the 07-Jul vs
  08-Jul runs settles it. Prerequisite to IA-P8-01's fix.
- **OQ-P8-2:** Does `CncGttMonitor._gather`'s `abs(qty)` on the CNC-positions leg (:454)
  double-count held stock on a SELL day (sell −3 in positions + holding still visible until
  settlement)? Unmeasurable until a real delivery sell day with the service up; MUST be
  settled before the D-8 step-2 lift makes this THE shared "held" definition.
- **OQ-P8-3:** The 6 non-SUCCESS eod_cleanup and 6 system_manager_eod heartbeats — dates
  and causes not itemized here (the 15-Jul FK crash is one); archaeology if ever needed.

### P8.6 SEAM SUMMARY — can reconcile's truth and the report's claim disagree (P9's starting point)

**They already do, in three measured ways.** (1) `eod_verify`'s persisted claim is PENDING
×18 days while its heartbeat — the thing the cron officer and the daily health view consume —
says SUCCESS: the report layer has been telling the operator "EOD verification ran fine"
about a job whose verdict never finalizes (IA-P8-01). (2) The nightly system_manager report
instructs a recovery action (deploy/resume.sh) that the next boot performs automatically —
a standing false instruction in the operator's most-read artifact (F4, nightly, measured).
(3) The shadow INFO alerts have carried an inverted mismatch flag for three weeks — a
correct verdict wrapped in a wrong comparison. What P9 inherits: the report layer consumes
`trades`/`fm_ledger` P&L that P5/P6 showed can carry proxy-priced and seed-absorbed values
(SWIGGY's −0.30; the unbookable IA-P5-02 class); `exit_reason` free text that memory rules
say must never be grouped on (`closure_source` is canonical); the W-placeholder honesty
markers; and the three disagreement instances above. P9's question is whether the
reports/alerts layer ADDS divergence of its own on top of a reconciliation layer that — in
the current era — is measurably quiet on real input but green-by-construction across most of
its surface.

**Phase 8 done** = F1 re-confirmed at source with all three faces measured (the sell-day
face new); the green-check class generalised across the surface (the ever-went-red list vs
the green-only/never-run partition); HARD_KILL flatness verification answered (never at kill
time; stuck-EXITING characterised as the 30-min mislabeled verifier, itself untested); the
30-Jul redesign audited item-by-item (all §A1 claims verified at source; buildable with
three named caveats α/β/γ); the flip gate found double-broken (IA-P8-01) with the vacuous
ledger dimension (IA-P8-02) and masked wrong-total (IA-P8-03) in the host job; the EOD
chain's abort-independence measured; the P8→P9 seam written with three already-measured
truth/claim disagreements; committed incrementally; ⛔ nothing fixed, nothing pushed, the
3-Aug/4-Aug sequence untouched.
*(Phase 9 — reports / alerts — appends below this line.)*

---

## PHASE 9 — REPORTS / ALERTS / OBSERVABILITY (told-vs-real, the census, delivery proof, and the P9→P10 seam)

### P9.0 Measurement window & system state

| | |
|---|---|
| Session window | **Sat 01-Aug-2026 ~10:23 → ~11:0x IST** |
| Measurements taken | 01-Aug **~10:4x–10:5x IST**, from the VM (`mode=ro` + log greps + sentinel-dir listing; zero writes) |
| Deployed SHA (VM bare) | **`297b587`** — unchanged since P1 (same session) |
| PC tree read | `1d2b9cb` = `297b587` + 11 docs-only commits ⇒ code read == deployed |
| Service | `inactive`; `gui-dashboard.service` **active** (loopback:8500, read-only GUI — my earlier unit-name guess "ops-dashboard" was wrong, corrected) |
| Primary windows | `alert_send` audit lines: **49 on 31-Jul (first full day; all outcome=delivered; INFO 31 / WARNING 18 / ERROR 0 / CRITICAL 0)** — service-log stream; cron-side sends audit into their own `logs/cron-*.log` (verified present in cron-control-tower.log) · sentinels: **0 pending / 39 `.delivered` all-time** (`data_store/`) · `failed_alerts.log`: last entry **02-Jul** (both tail entries = the pre-rotation chat-id era) · CRITICAL log lines: **exactly 4/day** (29/30/31-Jul) |
| Scope guard | 3-Aug/4-Aug untouched; nothing fixed (not the ACTIVE claim, not resume.sh, not the inverted flag); security-monitor redesign and channel-2 not built; deploy/recovery internals deferred to P10; July audits read-only |

### P9.1 The observability topology as verified

**Send path** (`alerts/telegram_notifier.py`): tier routing (G8/TG3-5) — **CRITICAL** →
sentinel FIRST (`data_store/`, TG5) → Telegram all-chats → `failed_alerts.log` on failure →
notifier-side email fallback only if ALL chats failed (:519-523 — INERT in production:
`ALERT_EMAIL_*` absent from the VM env, KNOWN board item; the WORKING email path is the
alert-watcher reading the sentinel); **ERROR** → attempt; `failed_alerts.log` on failure —
no sentinel, no email; **INFO/WARN** → attempt, drop silently (by design). Every send —
including suppressed/disabled — now emits a structured **`alert_send` audit line** (G5's
`_audit_send`, :398-444: severity/source/outcome ∈ {delivered, failed, suppressed,
suppressed_disabled}/delivered_to/failed_to/sentinel_written). **Email leg**
(`scripts/alert_watcher.py`, LIVE systemd service): consumes pending sentinels → SMTP →
rename `.delivered` (earned — send confirmed first, KNOWN 25-Jul), with Telegram-fallback,
SMTP-state tracking, degraded-marker and presence-based re-alert (26-Jul rebuild).
**Liveness**: probe window `[09:00, 16:00)` (`liveness_probe.py:108`) vs
`service_window_end: "17:35"` (yaml:59) — **G1's 95-minute blind spot re-verified at
current lines**; compensating control unchanged (the manual ~17:10 check). **Dashboard**:
`gui-dashboard.service` active, `backend.app` under its own venv, 127.0.0.1:8500 behind
Tailscale, read-only; kill state read from the persisted DB row (accurate — KS9
persist-first makes the row authoritative). The July-audit GUI LOWs (ephemeral secret_key,
lockout DoS, `verify_totp` fail-open on empty secret, prod TOTP seed in the dev tree) stand
un-re-audited here (KNOWN; P9 scope is what it asserts, and read-only-from-DB asserts
truthfully).

### P9.2 Headline re-measurements (mandated)

**(a) THE TOLD-vs-REAL MAP — every operator-facing assertion that is measurably false or
misleading on the current system (the phase's core deliverable).**
| # | The claim the operator sees | The reality | Status |
|---|---|---|---|
| 1 | Every ORDER PLACED Telegram: **"Smart TGT monitoring: ACTIVE (FIXED mode)"** | The exit engine is structurally starved (registration requires CO_PLUS_TGT; LIMIT_TRIPLE forced 478/478) — it has NEVER acted | KNOWN (5-Jul HIGH), re-verified P4.2(a), LIVE on every placement alert |
| 2 | Nightly system_manager report: **"Kill switch: SOFT_KILL — needs deploy/resume.sh before market open"** | Prior-day kills ALWAYS auto-clear at boot; resume.sh is same-day-emergency only | KNOWN-F4, P8-measured NIGHTLY (:698; :855's own-kill case also auto-clears) |
| 3 | eod_verify heartbeat: **SUCCESS 32/32** | Its verdict has been stuck PENDING for 18 trading days (since 08-Jul) | IA-P8-01 — the health view certifies a dead verdict |
| 4 | Shadow INFO alerts: **"⚠️ shadow mismatch vs eod_verify"** on clean days | The flag is inverted by the PENDING-as-verdict read; 14/14 clean days flagged | IA-P8-01 |
| 5 | **"Naked untracked position"** WARNING | GTT-protected/delivery flows read as naked (detector is GTT-blind); 5/5 false on 31-Jul | IA-P5-06 |
| 6 | **"POSITION RECONCILIATION MISMATCH"** ERROR | All 3 firing days ever were healthy delivery-lifecycle artefacts (T2); a real delivery book would alarm EVERY day of every position's life | KNOWN-F1 + IA-P8-04 |
| 7 | The daily **4-line CRITICAL log chorus** (ACTIVE-AT-STARTUP · force_close · KillSwitchActivated · SOFT_KILL ACTIVATED) | All four describe the DESIGNED 15:15 scheduled kill; the same event's Telegram is WARNING (SK-B) and writes no sentinel — **three severity vocabularies on one routine event** | NEW-observation → IA-P9-01 |
| 8 | `sl_breach_monitor` docstring: "wired into the tick dispatcher in main.py" | Zero importers repo-wide | KNOWN IA-P4-01b (doc-claim) |
| 9 | HARD_KILL completion: **"all N attempted position(s) flat"** | Placement-verified only; no positions() re-check | IA-P7-01 (latent — never yet emitted live) |
| 10 | EOD report "System Manager EOD — clean" era-alerts | Carried five CRITICAL-labeled scheduled events pre-SK-B (fixed 26-Jul); 16 of 85 sentinels self-identified INFO | KNOWN (26-Jul review), improved since |
**Closed as stale while building this map: M-R2** (daily_report legacy P&L excluding
CLOSED_MANUAL) — the current tree uses `_CLOSED_STATUSES = ("CLOSED","CLOSED_MANUAL")`
(daily_report.py:58); the July-audit citation no longer holds.

**(b) Can the system prove an alert was actually sent? — ANSWERED, with a status upgrade.**
**G5 → VERIFIED LIVE**: the send-side audit trail is producing in production — 49
`alert_send` lines on 31-Jul (the first full day after the 30-Jul deploy), every send's
outcome recorded; cron-side senders audit into their own cron logs (width: verified in
cron-control-tower.log; the 49 is the SERVICE stream only). The proof ladder as it now
stands: **attempt + HTTP-accept** = audited per send, every tier, forward-only from 30-Jul ·
**delivery to the operator's inbox** = provable only for CRITICAL (sentinel → alert-watcher
SMTP → `.delivered` earned; measured drained: 0 pending / 39 delivered) · **Telegram-outage
behaviour by tier**: CRITICAL falls back to the watcher email (working) + failed-log; ERROR
degrades to `failed_alerts.log` ONLY (an ERROR during an outage reaches nobody until that
file is read — last real failures 22-Jun/02-Jul, the old-chat-id era); INFO/WARN drop
silently by design. M-A2's 8s shared budget: KNOWN, shipped, not re-measured (no send
failures in the window to time).

**(c) The CRITICAL census — NEITHER ledger is complete, both directions now measured.**
The two counting surfaces disagree structurally: (i) **system-log CRITICAL lines**: exactly
4/day, and ALL FOUR are the scheduled-kill chorus — a log census counts pure routine noise
and would count it forever; (ii) **sentinels**: ~0/day current-era (the chorus's alert is
WARNING and writes none), 39 all-time all-delivered; (iii) the **27-Jul schema-refusal
class** (KNOWN M2): cron-process CRITICALs write sentinels + reach email but never appear
in system_*.log — so the log census MISSES a real class while over-counting a routine one.
⇒ **"no new CRITICAL in the census" is trustworthy ONLY as "no new sentinel", and only for
notifier-routed CRITICALs** — a raw `logger.critical` with no accompanying notifier call
reaches no one (the chorus proves the class exists; BANSALWIRE's CHECK9 CRITICAL had its
soft_kill alert accompany it, so the one real emergency did page). → IA-P9-01.

**(d) Alert fatigue, quantified from the first audited day.** 31-Jul (a healthy, profitable
trading day, zero real incidents): **49 alerts — 18 at WARNING**, of which **≥10 were
false-or-artefact** (5 GTT-blind "naked" + 5 predicted orphan-GTT artefacts) plus the
cron-side recon ERROR ×5 symbols (sell-day F1 face) — **the majority of elevated-severity
traffic on a clean day described non-problems**, and a real delivery book would add F1's
daily ERROR. The fatigue mechanism is not hypothetical: the T2 week trained the operator
that "POSITION RECONCILIATION MISMATCH — ERROR" and "Naked untracked position — WARNING"
mean *everything is fine*. → IA-P9-02.

### P9.3 NEW findings

---
**IA-P9-01**
- **WHAT:** The system has three uncoordinated severity vocabularies for the same events —
  log level, alert severity, sentinel-worthiness — and its CRITICAL accounting is complete
  in none of them: the routine 15:15 kill emits 4 CRITICAL log lines + 1 WARNING alert + 0
  sentinels daily, while the schema-refusal class emits 0 system-log lines + sentinels +
  email (27-Jul, KNOWN), and a raw `logger.critical` without a notifier call reaches
  no external channel at all. Any census over one surface is wrong about the others.
- **EVIDENCE:** the 4-line chorus identified verbatim (31-Jul); sentinel inventory 0
  pending/39 delivered; SK-B routing (kill_switch.py:596-611); the 27-Jul instance (KNOWN).
- **CLASS:** Consistency / Architecture. **NEW as a named, measured class** (the pieces —
  SK-B, M2, the noise review — were individually known).
- **ROOT CAUSE:** log-level and alert-severity were never contracted to each other;
  sentinel-writing is a property of the notifier path, not of severity.
- **RECOMMENDATION (described, ⛔ not applied):** one written contract: "CRITICAL log line
  ⟺ operator-pageable ⟺ sentinel" — demote the chorus lines to WARNING/INFO (they have a
  WARNING alert already) or accept and DOCUMENT that log-CRITICAL is not a pageable
  category; either way the census must be defined as the sentinel ledger.
- **SEVERITY-BY-IMPACT:** MED for auditability (this campaign itself had to discover which
  ledger to trust); LOW for daily safety (the working pageable path — sentinel→email — is
  drained and verified).

---
**IA-P9-02**
- **WHAT:** Alert fatigue is a measured, present defect, not a risk: on the first fully
  audited day, ≥10 of 18 WARNING-tier alerts were false-or-artefact and the only ERROR-tier
  traffic (recon mismatch ×5) described a healthy delivery event; ALL elevated-severity
  traffic in the T2 window described non-problems. Delivery go-live multiplies it (F1 daily
  per held position: ERROR Telegram + exit-2 + heartbeat FAILED). The operator is being
  conditioned to skim exactly the severities that must never be skimmed — while the one
  real emergency ever (BANSALWIRE) arrived in the same WARNING/CRITICAL stream.
- **EVIDENCE:** the 31-Jul alert_send histogram (49/18/0/0) + per-alert classification
  (P5-06 naked ×5 false; orphan-GTT ×5 artefact; F1 faces ERROR); the K1/F4 nightly false
  instruction compounds it in the report channel.
- **CLASS:** Safety (posture). **KNOWN-COMPOSED → quantified** (SK-B and the 26-Jul noise
  review addressed earlier layers; the delivery-artefact layer is new traffic).
- **ROOT CAUSE:** each false stream is individually explained (isolated-DB artefact,
  GTT-blind probe, missing product filter) — their SUM is what the operator experiences,
  and nothing owns the sum.
- **RECOMMENDATION (described):** the fixes are already owed elsewhere (the buy-day filter
  + D-4(a) kill F1's stream; the GTT-aware naked probe kills P5-06's; IA-P8-01's fix kills
  the inverted flag) — the P9-specific ask is only: after those land, re-measure this
  histogram and set a standing budget (e.g. "an elevated-severity alert on a clean day is
  itself a defect").
- **SEVERITY-BY-IMPACT:** MED — it is the multiplier on every other phase's "the alert
  fired" mitigation.

---
**IA-P9-03** (hygiene bundle, one ID)
- (a) The notifier's OWN email fallback (:519-523) remains INERT (`ALERT_EMAIL_*` absent
  on the VM — KNOWN board item) — the code path that reads `enabled: true` is the dead one;
  the alert-watcher is the real email leg. Config-vs-code standing note, re-confirmed.
- (b) An ERROR-tier alert during a Telegram outage reaches no channel (failed-log only) —
  by-design TG4/TG8, but worth one line in the operator docs; the audit line now at least
  records `outcome=failed` (G5).
- (c) `_build_email`'s CRITICAL-inference for absent severity — LATENT, pinned by test
  (KNOWN 26-Jul), unchanged.
- (d) The 22-Jun/02-Jul `failed_alerts.log` tail entries are pre-rotation chat-id failures
  — the file has been silent for 30 days; its emptiness since is itself evidence the
  current chat/token pair works (composes with the 25-Jul `getMe` verification).
- (e) The dashboard unit is `gui-dashboard.service` (active, loopback-only); the July GUI
  LOWs stand unre-audited; its kill-state panel reads the persisted row, which KS9 makes
  authoritative — no dashboard-specific false assertion found (width: unit identity +
  listening socket + the KS9 reasoning; panel-by-panel content NOT audited).
- (f) G1 re-verified at current lines (16:00 probe vs 17:35 service); the compensating
  manual check stands; ⛔ not a 1-line fix (KNOWN).
- **CLASS:** Documentation / Posture. **SEVERITY:** LOW.

### P9.4 KNOWN items re-verified — status updates (no re-numbering)

| Known ID | Status on the current system (fresh evidence) |
|---|---|
| G5 (send-side audit trail, `214a878`, FORWARD-only) | **STATUS UPGRADE → VERIFIED LIVE**: 49 structured `alert_send` lines on 31-Jul, outcomes recorded, both service and cron streams |
| 5-Jul [HIGH] alert-truth divergence ("Smart TGT ACTIVE") | LIVE — map row 1; unchanged since P4's fresh verification |
| F4 (resume.sh in the EOD report) | Map row 2 — P8-measured nightly, both sites |
| G1 (liveness 16:00 vs 17:35 blind spot) | RE-VERIFIED at current lines (`liveness_probe.py:108` / yaml:59); compensating control unchanged |
| M-A2 (8s shared send budget) | KNOWN, shipped `488e7e3`; not re-measured (zero send failures in window to time); the R8 shared-budget caveat stands |
| M2 / schema-refusal census bypass (27-Jul) | CONFIRMED as one direction of IA-P9-01's two-way incompleteness |
| SK-B (scheduled kills route WARNING) + the 26-Jul 85-sentinel noise review | Working as designed — and jointly the CAUSE of the log-vs-alert vocabulary split IA-P9-01 names |
| Out-of-band email path (25-Jul production verification) | Re-confirmed operational by artifact: 39/39 sentinels `.delivered`, 0 pending, SMTP state clean |
| M-R1 (daily_report legacy holiday guard broken) | NOT re-verified this phase (report-internals width limited to the M-R2 check + F4; state honestly) |
| M-R2 (legacy P&L excludes CLOSED_MANUAL) | **CLOSED-STALE**: `_CLOSED_STATUSES` includes CLOSED_MANUAL (daily_report.py:58) — the July citation no longer holds |
| ~249/day silent-drop class | ⛔ Not re-opened (P1 re-measured it dead). The P9 answer to "would a recurrence be visible": YES — the drop pattern's absence signals (webhook_audit counts, the daily xlsx W9 counter) plus the P1-measured zero-baselines are all operator-visible artifacts |
| Test-suite real-alert leakage (~50 since 6-May) | KNOWN CLOSED 27-Jul (in-process guards); the subprocess hole stands as recorded |
| W1-W9 report placeholders | Unchanged honesty markers (KNOWN); not re-audited |
| require_hmac /health oracle (AB-910 §1.7) | CLOSED by P1-17-Jul (KNOWN); dashboard/health surfaces not re-probed (no live probe on a weekend) |

### P9.5 Open questions (not guessed into findings)

- **OQ-P9-1:** The 28-Jul-cited 04:29/04:36 same-hour double-fire (dedup question) — not
  reconstructed this phase (the presence-based re-alert rebuild post-dates it; whether the
  current dedup would collapse it needs a planted-sentinel test, not a weekend log read).
- **OQ-P9-2:** Panel-by-panel dashboard content audit (what each panel asserts vs DB truth)
  — deferred; the read-only-from-DB architecture bounds the risk, but "no false panel" was
  NOT verified here (width stated in IA-P9-03e).
- **OQ-P9-3:** Whether any OTHER raw `logger.critical` sites lack an accompanying notifier
  call on a REAL emergency path (the chorus is benign; the width of the "unaccompanied
  CRITICAL" class was not swept repo-wide).

### P9.6 SEAM SUMMARY — can deploy/recovery's true state differ from what is reported (P10's starting point)

**The instruments that would tell the operator about a deploy or recovery are themselves
the ones this phase found lying in both directions.** What P10 inherits: (1) the
**deploy-record ground truth exists and is complete** (`~/trading-system.git/logs/HEAD` —
801 entries, both halves of every deploy, KNOWN 28-Jul) but NOTHING reports it — deploy
verification is a manual ritual, and the reflog is consulted only when someone remembers
it exists; (2) **recovery instructions in the operator's channel are wrong in the easy
direction** (F4's nightly resume.sh line; the RUNBOOK's exit-4 "restart loop" text — K1) —
a real HALT would be handled against documentation that misdescribes both the failure and
the fix; (3) the **boot window itself is the least-observed moment**: the 08:15 chorus
writes 4 CRITICAL log lines every day (noise), the schema-refusal class emails without
logging (M2), and the liveness probe doesn't start until 09:00 — a failed boot's
observability rests on the boot guard + token-watcher chain P10 must audit; (4) the
**green signals P10 will meet are now calibrated**: SUCCESS heartbeats can sit over dead
verdicts (IA-P8-01), `.delivered` is earned (trustworthy), `alert_send` outcomes are
trustworthy-forward-from-30-Jul, and a quiet `failed_alerts.log` means the CURRENT
credentials work. P10's question — can a deploy/crash/recovery leave the system in a state
the operator was never told about — already has one measured YES from this session's
archive (the 27-Jul missing EOD report), and the reporting layer's blind spots mapped here
say where to look for more.

**Phase 9 done** = the told-vs-real map built (10 rows, each with evidence and status; one
KNOWN closed stale in the process); delivery-proof answered with G5 upgraded to VERIFIED
LIVE on production artifacts (49 audited sends; 39/39 sentinels delivered; the per-tier
outage behaviour stated); the census question settled — neither ledger is complete, the
trustworthy statement is sentinel-scoped, and the three-vocabulary split is named
(IA-P9-01); alert fatigue quantified from the first audited day (≥10/18 WARNING false —
IA-P9-02); G1/M-A2/M2 re-verified or carried at current lines; the P9→P10 seam written;
committed incrementally; ⛔ nothing fixed, nothing pushed, the 3-Aug/4-Aug sequence
untouched.
*(Phase 10 — deploy / recovery / boot — appends below this line.)*

---

## PHASE 10 — RECOVERY / DEPLOYMENT / HEADLESS (boot, deploy, self-restart — and the loop closure)

### P10.0 Measurement window & system state

| | |
|---|---|
| Session window | **Sat 01-Aug-2026 ~11:0x → ~11:4x IST** |
| Measurements taken | 01-Aug **~11:1x–11:2x IST**, from the VM (md5/crontab/systemctl/grep reads; zero writes) |
| Deployed SHA (VM bare) | **`297b587`** — unchanged since P1 (same session) |
| PC tree read | `94af692` = `297b587` + 12 docs-only commits ⇒ code read == deployed |
| Fresh identity evidence | live hook `~/trading-system.git/hooks/post-receive` == tracked `deploy/hooks/post-receive` (**md5 `b7166732…` both**) · crontab 50 active entries incl. `15 8 * * 1-5 auto_refresh_token.py` · `token-watcher` + `trading-system` both **enabled** · `stray_pyc` present in the DEPLOYED system_manager (2 refs) · `deploy/post-receive` (the M-DP1 stale duplicate) **GONE from the tree** |
| Scope guard | 3-Aug/4-Aug untouched; nothing fixed/built (no tree-vs-HEAD check, no degrade path, no 2027 file); cross-cutting phases NOT started; July audits read-only |

### P10.1 The boot/deploy/recovery topology as verified

**Deploy**: push to the bare repo → post-receive (the Option-A hook, md5-verified identical
to the tracked copy) → `git checkout -f main` into `/home/ubuntu/systems/trading-system`
(no .git; untracked files NEVER removed — KNOWN) → crontab regen-verify-install (belt-and-
suspenders diff against the deployed canonical; WARNING-not-install on mismatch). The
reflog (`logs/HEAD`, 801+ entries, both halves, expiry=never) remains the complete deploy
record nothing reports (KNOWN 28-Jul). **Boot (the headless chain, all links fresh-verified
or session-verified)**: 08:15 cron `auto_refresh_token.py` (its failures alert via the cron
path) → token file → `token-watcher.service` (root, 30s poll, `Restart=always`) sees a
fresh today-token + `within_service_window` [08,16) → `systemctl start trading-system` →
main.py: start-cutoff guard **[08:00, 18:15) hardcoded** (:1613-1624 — cannot read config,
KNOWN, fresh-verified) → StateStore open with `allow_migrate=True` (**the ONLY migrating
opener; AC2 refuses even boot while the market is open**, state_store.py:137,182-183) →
`clear_stale_state` (:1878, BEFORE the checks — every prior-day kill clears) →
`run_all_startup_checks` (:2069; blocking failures ⇒ exit 3) → capital seed
(`compute_live_seed` = broker.net − carryover) → rehydrate (FM chains, monitor watch-list,
placer fill-map) → reconciler startup cycle (RC14) → 09:15 FM9 one-shot. **Recovery**: the
two-layer restart matrix below; `deploy/resume.sh` (stop → reset-failed → clear → start)
for same-day emergencies; `--resume` in main.

### P10.2 Headline re-measurements (mandated)

**(a) The self-restart matrix — EVERY failure class, both layers (systemd unit
:27-35 + token_watcher.sh :125-191), with the stays-down-silent assessment.**
| Failure class | systemd | token-watcher | Net coverage |
|---|---|---|---|
| Crash (exit 1/2, signals, OOM) | `Restart=on-failure`, 10s | + backoff cap 3/hr, WARNING-alert once/day at the cap | ✓ restarts; alerted on hammer |
| Clean exit 0 (EOD 17:35 / holiday) | no restart | no restart if exited TODAY; next fresh token → daily start | ✓ by design |
| Exit 3 (startup checks) | `RestartPreventExitStatus` | today: NO restart + **WARNING-tier Telegram once/day**; prior-day: ONE retry | stays down (correct — would loop); alert is WARNING-tier only |
| Exit 4 (HALT, kill active) | `RestartPreventExitStatus` | today: NO restart + **WARNING-tier Telegram once/day**; prior-day: one clean start (matches `clear_stale_state`) | ✓ K1 semantics EXACTLY re-verified — and the watcher's prior-day retry half AGREES with the headless guarantee |
| Hung-but-active | no `WatchdogSec` | sees `active` ⇒ nothing | ⚠️ **NO external detector** — the liveness probe reads UNIT STATE only (its own docstring: a deliberate stop is indistinguishable from silent death) and cannot see a hang; bounded only by in-app self-checks (→ OQ-P10-1) |
| Token never arrives | — | waits silently (no watcher alert) | covered one layer up: the 08:15 cron job's own failure alert |
| Start attempted after window | — | won't start ≥16:00 | main's own [08:00,18:15) guard is the backstop — two windows, coherent (starter narrower) |
**The named silent-ish case (K1×G1 composition, NEW-named): an emergency kill + same-day
restart AFTER 16:00 → exit 4 → the only automatic notification is the watcher's
WARNING-tier Telegram (TG4: no sentinel, no email, drop-on-failure) and the liveness probe
is already past its window — no CRITICAL-grade page until 09:00 the next day.** Inside
[09:00,16:00) the probe's ~5-min CRITICAL covers every down-state (K1's "detection is
fine" re-confirmed). → IA-P10-03.

**(b) The deployed-tree-vs-HEAD invariant — enforcement NONE, verification ONE NARROW
SLICE; the meta-example characterised (G lens).** Fresh width: the ONLY HEAD-adjacent
verification in the entire tooling is the stray-`.pyc` detector (system_manager.py:996-1061
— its docstring names the invariant verbatim: code "not in HEAD… silently breaks
deployed-tree-equals-HEAD"), DEPLOYED and measured-0 nightly; no rev-parse/manifest/diff
check exists anywhere in scripts/ or preflight (grep width stated). What holds the
invariant today: the hook's `checkout -f` (which never deletes untracked files), the
cherry-pick discipline, and manual SHA rituals — this campaign itself performed the ritual
ten times ("code read == deployed") against an invariant the SYSTEM never checks. The
reflog supplies the known-commit half for free (KNOWN). **Closing mechanism (described, ⛔
not built):** one boot-or-18:45 check — `git --git-dir=~/trading-system.git
--work-tree=$TARGET diff --stat HEAD` (empty = clean; plus the untracked-.py sweep the
.pyc detector already approximates) — a read-only two-liner that turns ten manual rituals
into a nightly artifact. → IA-P10-01.

**(c) The schema-migration window — traced at source; v46 WILL repeat it; NEITHER M1
direction exists in code.** The guard (`ed1c4b9`, state_store.py:137/:182-183/:326/:427):
`allow_migrate=True` is passed by the main.py boot path alone; AC2 makes even boot refuse
while the market is open; **every other opener** — all 33 heartbeat-writing crons, the
monitors, reports — hits `_refuse_migration` → CRITICAL sentinel ("Schema migration
refused (non-boot process)") → process aborts. ⇒ an evening schema push opens a
refuse-window for every cron until the next off-market 08:15 boot **by construction** —
the 27-Jul night (missing EOD report; forward-shadow safe by 71s of luck) is the measured
instance (KNOWN M1/M2). Today's code supports NEITHER registered M1 direction:
push-immediately-before-boot is a PROCEDURE (calendar rule), not code; a degrade-read mode
does not exist. The no-push-before-18:15 forward-shadow rule is likewise procedural — the
hook has no time gate; a 18:10 push deploys (the calendar is the protection). → IA-P10-02.

**(d) Loop closure — does the boot re-establish what the flow assumes? (J lens — the
audit's final synthesis.)** Verified-matching: the capital seed is EXACTLY P6's measured
mechanism (re-base to broker.net − carryover — with its measured absorb); kill-state
persistence/clearing is EXACTLY P7's (24 startup-active sightings, 28 audited clears; the
watcher's prior-day-retry agrees); OPEN-trade state rehydrates on all three surfaces (FM
chains, monitor watch-list, placer fill-map) and the reconciler runs a synchronous startup
cycle before polling. **The named NON-guarantees — start-of-day assumptions the flow makes
that the boot does not honor:** (1) *capital continuity* — the boot guarantees
re-base-to-broker, not continuity; unbooked P&L is absorbed silently (IA-P6-02, measured
−₹637.6); (2) *in-flight completeness* — only PENDING-status crash trades are re-fed;
UNKNOWN_IN_FLIGHT strands across a restart (IA-P5-04); (3) *schema access for non-boot
processes* — guaranteed only AFTER the boot ran (the (c) window); (4) *deployed-tree ==
HEAD* — every phase's own premise, held by ritual not code ((b)); (5) *the 2027 New-Year
boot* — `nse_holidays_2027.yaml` absent ⇒ the first 2027 boot does NOT start (KNOWN
MEASURED; the 15-Dec mailer is deployed; ⛔ file not created here); (6) *rate/burst
limiter continuity* — the entry throttle, burst window and 429 counters are in-memory and
start COLD after a mid-day restart (a restart-burst is bounded only by the DB-side
fingerprint dedup and caps). → IA-P10-04.

### P10.3 NEW findings

---
**IA-P10-01**
- **WHAT:** The deployed-tree-vs-HEAD invariant has no enforcement and only one
  narrow-slice verification (the .pyc detector); the system cannot itself answer "is the
  running code the audited code" — the question this audit manually re-answered at every
  phase.
- **EVIDENCE:** P10.2(b) — grep width (system_manager + preflight; the sole HEAD reference
  is the .pyc check); `checkout -f` semantics (untracked files survive, KNOWN); the ten
  manual SHA verifications this campaign performed as the de-facto mechanism.
- **CLASS:** Invariant-coverage / Architecture. **KNOWN-COMPOSED → characterised** (the
  gap was registered; the .pyc slice shipped 28-Jul; the precise enforcement-vs-
  verification statement and the two-liner closing mechanism are this phase's addition).
- **ROOT CAUSE:** the deploy path was built for speed (hook checkout) and its integrity
  was inherited from discipline, never from a check.
- **RECOMMENDATION (described, ⛔ not built):** the P10.2(b) two-liner as a 12th
  system_manager check (read-only, nightly artifact, no schema).
- **SEVERITY-BY-IMPACT:** MED — the failure it would catch (partial checkout, stray
  untracked .py, hook drift) is exactly the class that looks right while wrong, at the
  layer everything else trusts.

---
**IA-P10-02**
- **WHAT:** The schema-migration refuse-window is confirmed at source as a PROPERTY of the
  guard design: only the off-market boot migrates; every other opener aborts with a
  CRITICAL sentinel until that boot happens. v46 (or any future bump) repeats the 27-Jul
  night by construction. Neither registered M1 direction exists in code — the
  push-before-boot rule and the no-push-before-18:15 forward-shadow rule are both
  procedural (calendar), with no code/time gate in the hook.
- **EVIDENCE:** state_store.py:137, :182-183 (AC1/AC2), :326-346 (_refuse_migration +
  sentinel), :427 (the call); the 27-Jul measured instance (KNOWN M1/M2); the hook's lack
  of any time/schema gate (deploy/hooks/post-receive, 41 lines, read in full).
- **CLASS:** Architecture / Coupling. **KNOWN (M1) → source-confirmed + sharpened** ("the
  code supports neither direction" is now a verified statement, not an inference).
- **ROOT CAUSE:** the guard was built to stop mid-market corruption (correct) and its
  off-market blast radius was accepted implicitly.
- **RECOMMENDATION (described):** M1's decision stands OPEN for Rama; the cheapest
  code-side half is a refuse-window HEARTBEAT suppression note (the refusing process
  already sentinels — the gap is that its JOB shows FAILED with no "this is the migration
  window" context). ⛔ Not designed further here.
- **SEVERITY-BY-IMPACT:** MED on any schema-push evening (one night of CRITICALs + missing
  artifacts, some unregenerable); zero between schema changes.

---
**IA-P10-03**
- **WHAT:** Two composed observability gaps in the recovery layer, named: (i) an
  emergency HALT after 16:00 has no CRITICAL-grade notification until 09:00 next day (the
  watcher's exit-3/4 alerts are WARNING-tier Telegram — TG4: no sentinel, no email,
  drop-on-failure — and the liveness probe window has closed: the K1×G1 composition);
  (ii) a HUNG-but-active process has NO external detector at all (probe = unit-state
  only, by its own docstring; no systemd WatchdogSec) — bounded only by in-app
  self-checks whose current end-to-end wiring was not traced (OQ-P10-1).
- **EVIDENCE:** token_watcher.sh:144-179 (`send_telegram` → `send_alert(level="WARNING")`
  :74-90); liveness_probe.py:9,40,204-218 (systemctl-show basis); the unit (no Watchdog);
  G1 re-verified P9.
- **CLASS:** Safety-posture / Coupling. **NEW-COMPOSED** (K1, G1, TG4 each KNOWN; the
  composition and the hung-class width are the additions).
- **RECOMMENDATION (described):** route the watcher's exit-4 alert through a CRITICAL
  sentinel (one flag change lands it in the working email leg); the hung-class fix is the
  G1 probe-window question, already registered (⛔ not a 1-line cron edit — KNOWN).
- **SEVERITY-BY-IMPACT:** LOW-MED — both need an emergency in a specific window; both
  degrade detection, not action.

---
**IA-P10-04**
- **WHAT:** The loop-closure ledger: six start-of-day assumptions the flow makes that the
  boot does not guarantee — capital continuity (re-base absorbs), UNKNOWN_IN_FLIGHT
  recovery (PENDING-only feed), pre-boot schema access for crons, deployed-tree==HEAD,
  the 2027 holiday file, and warm rate/burst-limiter state after a mid-day restart.
- **EVIDENCE:** each item's own registered evidence (IA-P6-02, IA-P5-04, IA-P10-02,
  IA-P10-01, the 2027 MEASURED boot-block, signal_processor in-memory throttle state).
- **CLASS:** Consistency (synthesis). **KNOWN-COMPOSED** — the enumeration is the
  deliverable; items 1-5 are individually registered; item 6 (cold limiters post-restart)
  is the one previously unregistered corner (LOW: bounded by DB-side dedup + caps).
- **SEVERITY-BY-IMPACT:** the ledger itself is LOW; its value is that the cross-cutting
  phases and any future boot-path change now have the explicit list.

---
**IA-P10-05** (hygiene bundle, one ID)
- (a) **M-DP1 CLOSED-BY-REMOVAL**: the stale `deploy/post-receive` duplicate is gone from
  the tree; the live hook is md5-identical to the tracked `deploy/hooks/post-receive`
  (`b7166732…` both) — the KEEP-IN-SYNC identity proof holds today.
- (b) token-watcher `STATE_DIR=/tmp` — crash counters and once-per-day alert flags are
  lost on a VM reboot (worst case: one duplicate alert / a reset backoff window; benign).
- (c) token-watcher runs as root (needed for systemctl start; surface noted, loopback
  host, no listener).
- (d) The pre-receive hook remains UNARMED and must stay so as-is (KNOWN 20-Jul
  false-reject bug; not re-tested here).
- (e) The crontab auto-install's mismatch branch prints a WARNING to the PUSH OUTPUT only
  (the pusher's terminal) — a drifted registry-vs-canonical crontab would deploy code but
  keep the OLD crontab with no persistent alert (narrow; the generate-diff has matched on
  every observed push).
- **CLASS:** Documentation / Posture. **SEVERITY:** LOW.

### P10.4 KNOWN items re-verified — status updates (no re-numbering)

| Known ID | Status on the current system (fresh evidence) |
|---|---|
| Headless chain (token→watcher→service; 20-Jun guarantee) | **CONFIRMED end-to-end**: crontab entry present, both units enabled, the 31-Jul session-verified 08:15:17 start (KNOWN) + the watcher's full state machine read; the guarantee HOLDS for crash classes and degrades to WARNING-alerts for exit-3/4 (correct — those must not loop) |
| K1 (exit 4, no reboot, resume.sh; runbook text wrong) | RE-CONFIRMED from repo unit + watcher (:145-156 agrees with clear_stale_state); the doc-debt stands (P9 map row 2) |
| Boot capital seed == P6's measured mechanism | CONFIRMED — same `compute_live_seed` call chain (main.py:2373-2376), no drift since P6's read hours earlier |
| Kill persistence across boot == P7's evidence | CONFIRMED — the weekend-persisted 15:15 row + the :1878 clear site |
| nse_holidays_2027 missing = a measured 2027 boot-blocker | CARRIED (⛔ file NOT created); the 15-Dec reminder mailer is deployed (`cbcad2c`) |
| Stray-.pyc detector | **DEPLOYED-CONFIRMED on the VM tree** (2 refs in the deployed system_manager); measured-0 via its nightly runs |
| Reflog protections (expiry=never) + the complete deploy record | CARRIED (28-Jul DONE); still unreported by any tool (folded into IA-P10-01's closing mechanism) |
| Pre-receive hook DO-NOT-ARM | STANDS (not re-tested) |
| M-DP1 (stale tracked hook) | **CLOSED-BY-REMOVAL** (P10.3-05a) |
| Boot guard cannot read config; cutoff 18:15 | Fresh-verified at main.py:1613-1624 (hardcoded window, documented in-line) |
| 27-Jul schema-refusal night (M1/M2) | The measured instance behind IA-P10-02; nothing re-run |
| IA-P7-04 (`auto_resume_kill_switch` dead) | The F-lens sweep here found NO additional dead boot/recovery knobs (width: the unit files, token_watcher.sh env knobs — all consumed; SLEEP_SEC/LONG_SLEEP/MAX_CRASH_PER_HOUR live) |

### P10.5 Open questions (not guessed into findings)

- **OQ-P10-1:** The in-app hang mitigation — does a failed /health self-check still fire
  `_shutdown_event` (the S4-era history in liveness_probe.py:9), and is that path alive on
  the current tree? One trace settles the hung-class bound.
- **OQ-P10-2:** token_watcher's `exit_status` parse when systemd reports an empty
  `ExecMainStatus` (first-boot edge) — falls into the `*` crash arm; benign-looking, not
  chased.

### P10.6 LOOP CLOSURE — the flow phases are complete

**P1–P10 are done.** The signal→screen→size→place→execute→capitalize→kill→reconcile→
report→boot loop has been walked end-to-end on the deployed `297b587`, every phase
findings-only, every seam written, all in one campaign register with 12 local commits.
The boot re-establishes what P6/P7 measured (seed and kill-state confirmed matching), and
the six start-of-day assumptions it does NOT guarantee are now an explicit ledger
(IA-P10-04).

**Bridge to the cross-cutting phases (X-ARCH · X-DUP · X-CONFIG · X-DOCS · X-TEST ·
X-SEC · X-EVOLVE):** the flow map those phases need is now complete — including the
recurring classes they will formalise (the IA-P3-02 dead-knob family now at 8 instances;
the green-check-that-cannot-go-red census; the three severity vocabularies; the
free-text-dependency instances; the enforcement-without-verification meta-pattern that
P10's tree-vs-HEAD finding exemplifies). ⛔ They are NOT started here — this note only
records that the flow half of the audit is closed and the cross-cutting half has its
baseline.

**Phase 10 done** = the self-restart matrix built for every failure class with the silent
cases named (K1×G1 composition; the hung-class no-detector); the tree-vs-HEAD invariant
characterised (enforcement none / verification one slice / closing two-liner described);
the migration window source-confirmed with v46's repeat and the neither-direction status;
the headless chain end-to-end confirmed with fresh identity evidence (hook md5, crontab,
enablement); the loop-closure ledger written; M-DP1 closed-by-removal; committed
incrementally; ⛔ nothing fixed, nothing pushed, nothing armed, the 3-Aug/4-Aug sequence
untouched.
*(The flow phases P1–P10 end here. Cross-cutting phases append below when commissioned.)*

---

# CROSS-CUTTING PHASES

## PHASE X-ARCH — ARCHITECTURE (structure, ownership, and the dead-subsystem common cause)

### XA.0 Measurement window & evidence base

| | |
|---|---|
| Session window | **Sat 01-Aug-2026 ~11:0x → ~11:1x IST** *(corrected in the X-DUP session: the original stamp here was extrapolated, not read from a clock — an F6-class slip caught when the next phase's `date` returned 11:14; the measurements themselves are unaffected)* |
| Measurements taken | 01-Aug **~11:0x–11:1x IST** — a fresh AST import-graph over the PC tree (== deployed `297b587` + docs; **208 modules, 679 internal edges**, production packages + scripts + main) + targeted greps; zero writes anywhere |
| Evidence base | the completed P1–P10 register (this file) · the July audits' architecture section (G20 prior art: P4-1..P4-9 + M-K5 + M-X1 + ~55 line LOWs — **cited, not re-derived**) · BK-8 (the pre-existing config-drift/CI-hardcoded-default backlog item, status UNVERIFIED) |
| Scope guard | findings only; no refactor, no config-contract check built; other X-phases not started (hand-offs noted); 3-Aug/4-Aug untouched |

### XA.1 Headline determinations

**(a) THE DEAD-SUBSYSTEM COMMON CAUSE — determined: THREE mechanisms, not one, sharing a
single missing meta-mechanism (the phase's core question, answered).** The flow's
inert-machinery findings partition cleanly:
- **Family α — the constructor-pass-through gap** (config schema defines it, YAML sets it,
  the CONSTRUCTION SITE never passes it ⇒ the code default silently wins): `order_protocol`
  (X6/P4.2a — the forced LIMIT_TRIPLE) · `liquidity_check_enabled`+2 siblings (IA-P4-01a) ·
  `price_drift_threshold` (IA-P4-01n) · `auto_resume_kill_switch` (IA-P7-04) ·
  `max_single_order_qty` + `min_tick_size` (IA-P3-02) · `connect_sec` +
  `backoff_sequence_sec` (IA-P5-08) · `dynamic_by_winrate` (IA-P3-03) — **9 measured
  instances**.
- **Family β — structural starvation** (built, constructed, often STARTED — but the feed or
  gate never delivers): EntryGate (IA-P2-01, never fed) · SmartTgtManager (registration
  gated on the protocol α killed) · `sl_breach_monitor` (zero importers + dormant feed) ·
  TGTRetryManager (constructed-idle) · BreakevenManager (never constructed) · the CO
  protocol surface · FIX-072 live-margin — **~7 instances**.
- **Family γ — algebraic unreachability** (the knob is wired and live, but its configured
  value puts the trigger outside the reachable envelope): the risk-sizer floor (~20×,
  IA-P3-01/-04) · tier HIGH (IA-P2-03) · the sector cap ×3 (IA-P3-05) · the drift-ladder
  rungs against structurally-zero inputs (IA-P6-01) · the score ceiling 65 vs pass 60 (G2)
  — **~6 instances**.
**⇒ ~22 instances across three families ≈ half the campaign's headline defects — and all
three families share ONE meta-cause: the system has NO mechanism that verifies a declared
thing has an effect.** No dead-knob detector, no "constructed component acted ≥once or
declares itself dormant" telemetry, no constraint-bindability check (IA-P3-04 named this
locally for sizing; this generalises it system-wide). The fix idea ALREADY EXISTS as
backlog: **BK-8** ("config drift audit + CI hardcoded-default check", UNVERIFIED) covers
family α; families β/γ need the acted-telemetry sibling. → IA-XARCH-01.

**(b) The import graph, measured (B/F lens).** In-degree god-objects (true AST imports,
production+scripts): `core.time_authority` **79** · `core.logger` 55 · **`core.state_store`
35** (raw-reference widths: 63 non-test files, 149 test files — reconciling G20-P4-1's
"126 importers" figure, which is the all-tree grep) · `utils.cron_heartbeat` 32 ·
`alerts.telegram_notifier` 30 · `core.config_loader` 28. **Cycles: ONE 12-module SCC**
spanning core↔broker↔orders (config_loader/config_auditor ↔ zerodha_adapter/rate_limiter/
slippage_engine/cost_calculator/order_monitor ↔ order_placer/protocols/full_entry_engine/
smart_tgt_manager) **closed by exactly ONE lazy edge** (`core/config_auditor.py:494`,
function-local import of order_placer) — so the graph is **acyclic at import time** (no
boot-crash hazard) while the change-ripple coupling is real: a rename in order_placer
breaks the config auditor at RUNTIME mid-audit, not at import. One 2-module scripts SCC
(check_cron_drift↔cron_officer). **Top-level layering is CLEAN**: core imports NOTHING
above it at module load — every upward reach is one of **8 lazy call-site imports**
(candle_math→sr_detector ×2, config_auditor→strategies ×2 +→orders, cron_registry→utils,
daily_stats→sr_detector, **state_store:344→alerts.critical** — the migration-refusal
sentinel, load-bearing) — refining G20-P4-3's "layering erosion ×5" to: one genuine
top-level upward edge exists, `broker/zerodha_adapter.py:121 → orders.price_math`
(DEFAULT_TICK — M-X1's duplication sibling). → IA-XARCH-02.

**(c) The ownership census — every concept with more than one authority (C lens).**
| Concept | Authorities | Agreement status |
|---|---|---|
| "held" / a position | **4 definitions**: `positions()`-only (reconcile_positions, eod_broker_reconcile, the kill sweep) vs holdings+CNC-positions (`CncGttMonitor._gather`) | Measured disagreement = F1's three false-alarm faces (P8.2a); the correct reader exists and is used by ONE consumer |
| Fill truth | 3 stores: the OrderFilled event (authoritative) / `orders` row / `trades` row | orders row systematically WRONG (zeroed by the stale payload, IA-P5-01); trades row correct for entries |
| Order state | 3 models: OSM (memory) / orders.status (DB) / broker — the July "three order-state models" | PERSISTS; data agreement OK except the fill fields |
| Capital total | 3 computations: FM (authoritative) / broker.net (compared by G3) / ebr's last-ledger-row | The third is semantically WRONG (0.0 at 15:58 — IA-P8-03), masked by a gate |
| "Flatness" after a kill | 4 verifiers: kill flatten (placement-level) / stuck-EXITING (broker, +30min) / 15:45 job / EOD residual sweep | None at kill time (IA-P7-01); the 30-min one is the only positive check |
| Alert severity | 3 vocabularies: log level / alert tier / sentinel-worthiness | Never contracted (IA-P9-01); measured divergence daily (the kill chorus) |
| Daily P&L | fm_ledger `pnl_delta` (canonical, E4) / trades.net_pnl / broker day-realized | CONTRACTED + cross-compared (the ebr pnl dimension); one reader quirk (M-K1) |
| The clock | time_authority / 5 direct-read sites | KNOWN class (26-Jul), unchanged |
| **The counter-example** | `closure_source`: ONE canonical vocabulary + a tree-scan test that fails on any restatement (v45) | **The model** — proof the codebase knows how to contract a concept when it decides to |
→ IA-XARCH-03.

**(d) Intended-vs-actual architecture (H lens).** The intended-architecture DOCUMENT no
longer exists as a map: SYSTEM_MAP.md is a 597KB newest-first changelog (its own P4-7-era
header says so honestly), PATHS.md self-describes as "no longer a quick reference", and
the real intended structure lives in per-module docstring layer numbers (Layer 0-5) —
which the measured graph shows are HONORED at top level (b). ⇒ the drift is DOCUMENTARY,
not structural: the code kept the layering; the map of it became a log. G20-P4-6/P4-7
already own the restructure decision (⛔ neither file split here — both are pointed into
by section from every report and memory). Hand-off to X-DOCS. **One causal link is new:
main.py-as-sole-composer is WHY family α exists** — every α instance lives at one of
main.py's ~25 hand-maintained constructor calls, with no completeness check between the
config schema and the ctor argument lists; the composition root is the single point where
declared config silently detaches from running code. → folded into IA-XARCH-01/-04.

### XA.2 Findings

---
**IA-XARCH-01**
- **WHAT:** The dead-subsystem common cause: three families (α pass-through gap ×9 ·
  β starvation ×7 · γ algebraic unreachability ×6) covering ~22 register findings ≈ half
  the campaign's headline defects — unified by the absence of any effect-verification
  mechanism, and family α unified further by the composition root (main.py's
  hand-maintained ctor calls, no schema↔ctor completeness check).
- **EVIDENCE:** the family inventories above, each item citing its flow ID; main.py ctor
  sites (P4.2a :2639-2665 is the exemplar — three liquidity args simply absent).
- **CLASS:** Architecture / Config-vs-code. **SYNTHESIS** (unifies X6, IA-P2-01/-03,
  IA-P3-01/-02/-03/-04/-05, IA-P4-01, IA-P5-08, IA-P6-01, IA-P7-04, G2).
- **ROOT CAUSE:** config was grown schema-first with hand-plumbed consumption; nothing
  closes the loop from "declared" to "took effect".
- **RECOMMENDATION (described, ⛔ not built):** (i) family α: execute BK-8 (already on the
  backlog — a config-key→consumer assertion or CI hardcoded-default check), anchored at
  the composition root; (ii) families β/γ: one EOD telemetry line per constructed manager
  — "acted N times today | declared dormant" — turning silent starvation into a daily
  visible zero (the IA-P6-06 census, automated).
- **SEVERITY-BY-IMPACT (future-defects weight):** **HIGH — the highest-leverage finding in
  the register**: it is the class that produced the forced exit-protocol, the dead risk
  floor, the unfed gate and the dark liquidity check, and it will produce the next one
  the day another knob ships.

---
**IA-XARCH-02**
- **WHAT:** The measured dependency structure: 208 modules / 679 edges; god-objects
  time_authority(79)/logger(55)/state_store(35 imports · 63 files · 149 test files)/
  cron_heartbeat(32)/telegram_notifier(30)/config_loader(28); ONE 12-module
  core↔broker↔orders SCC closed by a single lazy edge (runtime-coupled, import-time
  acyclic); top-level layering otherwise clean with 8 lazy upward reaches and one genuine
  upward edge (zerodha_adapter→orders.price_math). Blast-radius ranking for change safety:
  state_store (every layer + 46 tables) > main.py (sole composer, ~25 ctor sites — the α
  locus) > order_placer (4,686 LOC) > order_reconciler (4,118) > kill_switch (1,714).
- **EVIDENCE:** the AST graph run (scratchpad tool, this session); the lazy-edge greps
  (config_auditor:494; the 8 core call-site imports; zerodha_adapter:121).
- **CLASS:** Architecture / Coupling. **KNOWN-REFINED** (G20-P4-1/P4-2/P4-3 held the
  qualitative claims; the graph, the SCC, the lazy-vs-top-level distinction and the
  reconciled importer counts are new precision).
- **RECOMMENDATION (described):** none urgent — the two structural risks worth naming for
  X-EVOLVE: the lazy-import idiom hides dependencies from static layering (fine until
  someone "cleans it up" into a top-level import and creates the real cycle), and the
  composition root has no completeness check (see -01).
- **SEVERITY-BY-IMPACT:** MED as context; the SCC is the one concrete change-hazard
  (12 modules, money path included).

---
**IA-XARCH-03**
- **WHAT:** The ownership census: 8 concepts with 2-4 authorities each (table above), of
  which three have a MEASURED wrong-or-divergent authority today (fill truth, capital
  total, "held") and one has a measured daily vocabulary split (severity) — against ONE
  contracted counter-example (closure_source) proving the pattern is fixable in this
  codebase's own idiom (canonical module + tree-scan test).
- **EVIDENCE:** each row cites its flow measurement; the closure_source contract
  (`core/closure_source.py` + the scanning test, v45 — KNOWN).
- **CLASS:** Consistency / Architecture. **SYNTHESIS** (unifies IA-P5-01, IA-P8-03/-04,
  IA-P7-01, IA-P9-01, F1, the July three-models class).
- **RECOMMENDATION (described):** the closure_source pattern, applied in priority order:
  "held" first (the redesign's D-8 step 2 shared reader IS this — one definition, all
  consumers), severity second (IA-P9-01's contract), fill-fields third (IA-P5-01's 2-line
  fix makes the orders row true).
- **SEVERITY-BY-IMPACT:** HIGH for future defects — every multi-authority concept in the
  table has already produced at least one measured flow finding.

---
**IA-XARCH-04**
- **WHAT:** The intended-vs-actual verdict: the CODE honors its layer scheme (measured);
  the ARCHITECTURE DOCUMENTS have inverted into changelogs (SYSTEM_MAP 597KB / PATHS
  ~120KB, both self-aware of it) — so the system's structure is currently better than its
  map, and a new reader would learn the architecture faster from module docstrings than
  from the designated documents.
- **EVIDENCE:** XA.1(d); the files' own honest headers; G20-P4-6/P4-7 (the owned
  restructure decisions, deliberately not executed pending an agreed shape).
- **CLASS:** Documentation / Architecture. **KNOWN-CONFIRMED** (P4-6/P4-7), with the
  measured layering-is-clean half NEW.
- **RECOMMENDATION (described):** none beyond the existing G20 items — hand-off to X-DOCS
  with one added input: the layer docstrings are the de-facto intended-architecture record
  and any future map should be generated FROM them, not written beside them.
- **SEVERITY-BY-IMPACT:** LOW-MED (a doc problem — but it taxes every audit and onboarding,
  including this one).

### XA.3 Open questions

- **OQ-XARCH-1:** The `scripts→main` import edge (which script imports main, and does it
  execute main-module side effects at import?) — flagged by the package table, not chased.
- **OQ-XARCH-2:** G20-P4-4's "19 raw sqlite sites outside db_connect" — not re-counted
  this phase (the July figure carried; a fresh count belongs to X-DUP/X-CONFIG).

### XA.4 Hand-offs to the remaining cross-cutting phases

**X-DUP** inherits the ownership census (-03) + M-X1's tick-math duplication (with the
measured zerodha_adapter→price_math edge showing the partial dedup) + G20-P4-5 (two report
generators). **X-CONFIG** inherits the family-α quantification (9 dead/half-dead knobs,
the BK-8 anchor, the composition-root cause) — its job is the exhaustive knob-by-knob
sweep the flow phases sampled. **X-EVOLVE** inherits the blast-radius ranking and the
SCC/lazy-import hazards (-02) — "how safe is a new feature" now has numbers. **X-DOCS**
inherits -04 and the P9 told-vs-real map. **X-TEST** inherits the green-check census
(P8.2d) and the paper-cannot-exercise class as its reachability baseline.

**X-ARCH done** = the import graph measured fresh (god-objects, one SCC, lazy-vs-top-level
layering verdict); the dead-subsystem common cause DETERMINED (three families, one missing
meta-mechanism, ~22 instances, the composition-root causal link); the ownership census
enumerated with the contracted counter-example; intended-vs-actual settled (code better
than map); hand-offs written; committed incrementally; ⛔ nothing fixed, nothing pushed,
the 3-Aug/4-Aug sequence untouched.
*(X-DUP / X-CONFIG / X-DOCS / X-TEST / X-SEC / X-EVOLVE append below when commissioned.)*

---

## PHASE X-DUP — DUPLICATION / MULTIPLE TRUTH (the copies behind the census, agree-or-diverge)

### XD.0 Measurement window & evidence base

| | |
|---|---|
| Session window | **Sat 01-Aug-2026 ~11:14 → ~11:4x IST** (clock read at start: 11:14:57) |
| Measurements taken | 01-Aug **~11:15–11:2x IST** — targeted PC-tree greps/reads (== deployed `297b587` + docs); zero writes |
| Evidence base | IA-XARCH-03 (the 8-concept ownership census — this phase's primary target list) · the P1–P10 register · July-audit :200-201 duplication rows + M-X1 + G20-P4-5 (KNOWN prior art) |
| Scope guard | findings only; nothing de-duplicated; other X-phases hand-off-only; 3-Aug/4-Aug untouched |

### XD.1 Headline determinations

**(a) THE AGREE-BY-LUCK EXEMPLAR, MEASURED — the emergency-exit buffer has TWO sources and
four consumers split across them.** `orders/price_math.py:124` defines the CONSTANT
`EMERGENCY_EXIT_BUFFER_PCT = 0.01`; `config/system_config.yaml` defines
`capital.emergency_exit_buffer_pct: 0.01`. The four "flatten a position" sites split:
**kill_switch** (main.py:1868) and **order_placer** (main.py:2662) receive the CONFIG value;
**eod residual sweep** (eod_squareoff.py:1522) and **reconciler flatten**
(order_reconciler.py:2077) call `marketable_limit_price(…, EMERGENCY_EXIT_BUFFER_PCT, …)` —
the CONSTANT. Today 0.01 == 0.01, so all four agree — **by value coincidence, not by
construction. The future fix that misses copies is exact: re-tuning
`capital.emergency_exit_buffer_pct` moves the kill and placer exits and silently leaves the
EOD and reconciler flattens at the old constant** — four emergency paths, two behaviours,
no test that would notice. The same constant-shadows-config pattern:
`DEFAULT_SL_LIMIT_OFFSET_PCT = 0.005` (:159) == `capital.sl_limit_offset_pct: 0.005` — live
consumers get config via main; the (dead) BreakevenManager and any future direct caller get
the constant. → IA-XDUP-01.

**(b) The live/terminal TRADE-status vocabulary is spelled out ~34 times.** Fresh census:
**30 inline SQL literals** across 9 production files (state_store 13 · eod_cleanup 5 ·
sl_breach_monitor 3 · healthcheck_server 3 · signal_processor 2 · order_manager /
kill_switch / eod_verify / reconcile_positions 1 each) **+ 4 named sets**
(`_FLATTEN_LIVE_TRADE_STATUSES`, `_SECTOR_EXPOSURE_STATUSES`, `_RECOVERY_STATES`, OSM's
`TERMINAL_STATES` — the last being the one PARTIAL canonical, correctly imported by the
monitor). At least **three distinct "live" memberships** coexist — (OPEN,PARTIAL) ·
(OPEN,PARTIAL,PENDING_FILL) · recovery's (UNKNOWN_IN_FLIGHT,PENDING,PENDING_FILL) — each
individually defensible, none derived from a shared vocabulary. The codebase KNOWS the
hazard locally (kill_switch's M-C8 comment: the SELECT and the write-condition "MUST agree…
derive, don't duplicate" — and does, within that one file); it lacks the same contract
globally. **The fix that misses copies: any new trade status (or a change to EXITING
handling) must find ~34 sites**; IA-P5-04 (CHECK2's dispatch omitting UNKNOWN_IN_FLIGHT) is
this class's already-measured casualty. → IA-XDUP-02.

**(c) The multi-owner concepts traced to code — agree/diverge verdicts consolidated.**
| Concept (IA-XARCH-03) | The copies | Verdict |
|---|---|---|
| "held" | 3× positions()-only readers vs `_gather`'s holdings+CNC | **DIVERGE, measured** (F1's three faces) — B4's "no third definition" rule already names the fix |
| Fill truth | OrderFilled payload vs orders row vs trades row | **DIVERGE, measured** (orders row zeroed — IA-P5-01) |
| Capital total | FM vs broker.net vs ebr's last-ledger-row | **DIVERGE, measured** (0.0 vs 9,360 — IA-P8-03) |
| Flatness | kill / stuck-EXITING / 15:45 / residual sweep | **Complementary layers, NOT code copies** — except their flatten IMPLEMENTATIONS, which are the -01 quartet |
| Severity | log level / alert tier / sentinel | **DIVERGE daily** (the kill chorus — IA-P9-01) |
| R:R value | strategy yaml 1.5 vs placer ctor default 2.0 | **Historical divergence measured in data** (36 rows at 2.0 pre-Slice-1; fallback latent — IA-P4-05a) |
| Daily P&L | fm_ledger / trades / broker | **CONTRACTED (E4) + cross-compared** — the healthy pattern |
| Clock | time_authority vs 5 direct sites | KNOWN class, not re-swept (width stated) |

**(d) The tick-rounding twins (M-X1) — sharpened.** Both implementations are Decimal-based
with the same rounding modes (price_math `round_to_tick` ROUND_CEILING/FLOOR/HALF_UP;
slippage_engine `_round_*_to_tick` FIX-014 same trio) — **agreement by parallel
implementation, not by sharing**. The adapter's own import block mixes provenance
(`zerodha_adapter.py:115-121`: the rounding trio FROM slippage_engine, DEFAULT_TICK FROM
price_math); smart_tgt uses the slippage copy on the money path (July :160). The fix that
misses copies: any tick-policy change (per-exchange tick tables, a new rounding rule) lands
in one family and silently not the other.

### XD.2 Findings

---
**IA-XDUP-01**
- **WHAT:** Four implementations of "flatten a position with a marketable LIMIT", fed by
  TWO sources of the same 1% buffer that agree only by value coincidence (config
  `capital.emergency_exit_buffer_pct` → kill_switch + order_placer; constant
  `EMERGENCY_EXIT_BUFFER_PCT` → eod residual + reconciler flatten; kill additionally
  inlines its own `ltp*(1±buf)` formula instead of the shared `marketable_limit_price`).
- **EVIDENCE:** XD.1(a) — six file:line sites; values 0.01 == 0.01 verified.
- **CLASS:** Duplication / Correctness-latent (agree-by-luck). **SYNTHESIS** (July :200's
  "flatten ×3" + P7's kill read, now with the buffer-source split and the exact
  fix-miss).
- **ROOT CAUSE:** FIX-181 hardened each site in place; the buffer was constant-ised in
  price_math and config-ised in capital at different times.
- **RECOMMENDATION (described, ⛔ not applied):** one flatten helper on the closure_source
  template — single function, single buffer source (config), all four call it; until
  then, a comment on the constant naming its config twin would at least fail loudly in
  review.
- **SEVERITY-BY-IMPACT (fix-miss weight):** **HIGH** — the missed copies are emergency
  paths; a tuned buffer diverging silently across kill-vs-EOD flattens is a
  worst-moment surprise.

---
**IA-XDUP-02**
- **WHAT:** The trade-status vocabulary exists as ~30 inline SQL literals + 4 named sets
  with ≥3 distinct "live" memberships and no shared source; ORDER states have a partial
  canonical (OSM TERMINAL_STATES, correctly imported) while TRADE states have none.
- **EVIDENCE:** XD.1(b) census (9 files, counts per file); the three memberships;
  kill_switch's own local derive-don't-duplicate comment as the in-repo statement of the
  rule.
- **CLASS:** Duplication / Consistency. **NEW-as-measured** (the July audits flagged the
  terminal-set divergence pair; the 34-site width and the membership taxonomy are new).
- **ROOT CAUSE:** statuses accreted (EXITING, UNKNOWN_IN_FLIGHT, PENDING_FILL) after the
  original queries were written; each addition patched the sites its author knew.
- **RECOMMENDATION (described):** a `core/trade_status.py` on the closure_source template
  — the named sets (LIVE, LIVE_WITH_INFLIGHT, RECOVERY, TERMINAL) + a tree-scan test
  banning inline restatement; IA-P5-04 is the measured defect it would have prevented.
- **SEVERITY-BY-IMPACT (fix-miss weight):** **HIGH** — a new status touches ~34 sites
  today; the one already-missed site produced a registered latent naked-position path.

---
**IA-XDUP-03**
- **WHAT:** M-X1 sharpened: the two Decimal tick-rounding families are same-semantics
  parallel implementations; the adapter mixes their provenance in one import block; the
  money path (smart_tgt SL trail, adapter snap) rides the slippage_engine copy while
  price_math is the nominal home.
- **EVIDENCE:** XD.1(d); zerodha_adapter.py:115-121; price_math.py:27-70 vs
  slippage_engine.py:147-175.
- **CLASS:** Duplication. **KNOWN (M-X1) → sharpened** (implementation-level agreement
  verified; the mixed import block is the new tell).
- **RECOMMENDATION (described):** fold one family into the other (price_math as home,
  slippage_engine imports) — mechanical, but money-path ⇒ careful-loop; G20 already
  holds it.
- **SEVERITY-BY-IMPACT:** MED — agreement is real today; the fix-miss (a tick-policy
  change) is plausible (BSE support is on BK-7).

---
**IA-XDUP-04**
- **WHAT:** T4 line-verified: `scripts/backfill_closure_source_w8.py:92` restates the
  closure vocabulary as string literals (`{"SL": "OWN_SL", …}`) with NO import from
  `core.closure_source` (import block :53-60 checked) — a restatement surviving INSIDE
  the one concept that HAS a canonical contract and a tree-scan test, which means the
  guard's width does not cover this shape/location.
- **EVIDENCE:** the file's import block + :92; the canonical contract (`bc19aab`, KNOWN).
- **CLASS:** Duplication / Consistency. **KNOWN (T4) → line-verified + widened** (the
  contract-test width gap is the new half).
- **RECOMMENDATION (described):** import the literals; and extend the scanning test's
  width to scripts/ mapping-shapes — the counter-example should enforce itself
  everywhere.
- **SEVERITY-BY-IMPACT:** LOW-MED — the backfill is a one-shot tool, but it is exactly
  the tool someone re-runs after a vocabulary change, with the stale copy.

---
**IA-XDUP-05** (positives + carried, one ID)
- The dedup idiom WORKS where applied — recorded so the fix pattern is visible:
  `_resolve_credentials` shared (ebr imports reconcile_positions') ·
  `round_trip_costs_or_zero` shared by CHECK1/CHECK4 (E4) · `marketable_limit_price`
  shared by 3 of the 4 flatten sites · `correlate_entry_by_tag` pure+shared · OSM
  `TERMINAL_STATES` imported by the monitor · kill_switch's M-C8 derive-don't-duplicate
  constants · `closure_source` itself. Carried-not-re-measured (width stated):
  G20-P4-5 (two report generators in parallel) · July :201 (~8 IST re-implementations;
  naive `datetime.now()` sites) · eod_verify's dead P&L-variance branch vs ebr's live
  `pnl_reconciliation` writer (duplicate-then-supersede; the dead copy remains — KNOWN
  :214 + P8.4).
- **CLASS:** Documentation. **SEVERITY:** n/a (inventory).

### XD.3 Open questions

- **OQ-XDUP-1:** Does the closure_source tree-scan test intentionally exclude scripts/ or
  only ban re-DEFINITIONS (not mappings)? One test-read settles the -04 width question.
- **OQ-XDUP-2:** G20-P4-4's "19 raw sqlite sites outside db_connect" — still deferred to
  X-CONFIG (not re-counted here; stated as carried width).

### XD.4 Hand-offs

**X-CONFIG** inherits the constant-shadows-config class (-01's pattern: every price_math /
module-level default that shadows a config key — the sweep completing family α from the
other side) and OQ-XDUP-2. **X-DOCS** inherits the -04 contract-width gap (a documented
canonical whose enforcement doesn't reach all restatements). **X-TEST** inherits the
"which duplication has a divergence test" question — today: none of -01/-02/-03 does.

**X-DUP done** = the 8 census concepts traced to code with agree/diverge verdicts (3
measured divergences, 1 daily vocabulary split, 1 historical data divergence, 1 healthy
contract); the agree-by-luck exemplar measured to the value (two 0.01s, four consumers,
the exact missed-copy list); the status vocabulary counted (~34 sites, 3 memberships);
the tick twins sharpened; T4 line-verified with the guard-width gap; positives inventoried
so the template is visible; committed incrementally; ⛔ nothing fixed, nothing pushed, the
3-Aug/4-Aug sequence untouched. *(Also in this session: the X-ARCH window stamp was
corrected — it had been extrapolated rather than clock-read; an F6-class slip, caught by
the next phase's `date` call and amended in place.)*
*(X-CONFIG / X-DOCS / X-TEST / X-SEC / X-EVOLVE append below when commissioned.)*

---

## PHASE X-CONFIG — CONFIGURATION INTEGRITY (the full-surface classification)

### XC.0 Measurement window & evidence base

| | |
|---|---|
| Session window | **Sat 01-Aug-2026, clock-read start 11:27:47 IST** (per the F6 rule — read, not extrapolated) |
| Measurements taken | 01-Aug ~11:28–11:3x IST — an automated key-sweep tool (scratchpad; extracts every LEAF key from `system_config.yaml` (651 lines) + `broker_limits.yaml` (40), git-greps the production tree per key) + targeted verifications + a VM config-identity check; zero writes |
| Evidence base | IA-XARCH-01 (family α ×9 + BK-8) · IA-XDUP-01 (constant-shadows-config) · the per-phase config ground-truth tables (P1–P10 — ~70 money-path knobs individually verified live across the flow) |
| Scope guard | findings only; BK-8 not built; no knob removed; G4's spec decision stays Rama's; 3-Aug/4-Aug untouched |

### XC.1 Headline determinations

**(a) THE FULL-SURFACE CLASSIFICATION (the phase's core deliverable).** 280 deduped leaf
keys across the two config files. Automated verdicts: **3 ORPHANS** (zero references
anywhere, including the loader and tests — they survive `load_all` only because their
sections are extra-tolerant containers): `multi_account_mode` · `playbook_scanner` ·
`entry_tf`. **7 SCHEMA/TEST-ONLY** (loader + tests, no other production file):
`pipeline_timeout_sec` · `auto_resume_kill_switch`† · `personal_chat_id_env` ·
`whitelist_only` · `reconnect_backoff_base_seconds` · `reconnect_backoff_max_seconds` ·
`connect_sec`† († = the tool independently re-derived two register findings — IA-P7-04 and
IA-P5-08 — cross-validating both the tool and the findings). **270 keys have ≥1 non-loader
reference — an UPPER bound on live**, because name-grep cannot see the semantic deads: the
register adds ~13 keys with consumers but no (or wrong) effect (the α set: `order_protocol`,
the liquidity trio, `price_drift_threshold`, `max_single_order_qty`, `min_tick_size`,
`dynamic_by_winrate`; plus `backoff_sequence_sec` (its only "consumer" is a docstring
mention), `slm_margin_buffer_pct` (half-dead), `min_volume_surge` (per-strategy, zero
effect), `entry_gate.max_spread_pct` (units-100× — consumer live, effect≠intent), the
smart_tgt trio (β-starved consumers)). **⇒ the consolidated dead/ineffective inventory =
~23 distinct keys ≈ 8% of the surface; nominal-live ≈ 92% as an upper bound, with ~70
money-path knobs POSITIVELY verified live by the flow phases and the residual ~187 carrying
name-grep-only assurance** (the honest three-tier statement). → IA-XCFG-01.

**(b) SIX NEW dead keys found by the sweep** (beyond the register's set): **(1)
`pipeline_timeout_sec: 30`** — the SP15 "hard per-signal processing deadline" is consumed
by NOTHING (0 hits in signal_processor; verified directly) — a claimed processing-safety
mechanism that does not exist (M-S3's per-step-timeout finding was its sibling; the
per-signal deadline turns out to be config fiction); **(2,3) `reconnect_backoff_base_seconds`
+ `reconnect_backoff_max_seconds`** — live_feed contains no backoff consumer (0 'backoff'
hits in the file; `max_reconnect_attempts` IS live) — the FIX-134 Item-37 yaml comment
promises tunable reconnect pacing that is not wired; **(4) `personal_chat_id_env` · (5)
`whitelist_only`** (alerts section, schema-only); **(6) the pb01 orphans** (`playbook_scanner`,
`entry_tf` — the G4 no-ratified-spec section's own keys, unread even by the code they
describe) + `multi_account_mode` (FIX-133, declared for an untested future).

**(c) The verification gap — CONFIRMED, and the closing set is now precise.** Nothing in
the system catches any class above: `config_auditor` covers strategy-YAML validity, the
go-live date, slippage-override validation and the config-block gate — NOT key consumption;
pydantic `extra="forbid"` catches unknown keys only in MODELED sections (the 3 orphans
prove dict-typed sections escape it); no test fails on a dead knob (→ X-TEST). **BK-8
assessed against the classes: as named ("config drift audit + CI hardcoded-default check")
it targets exactly family α + the ctor-default shadows — it would catch the 9 α instances
and the schema-only set, but NOT the orphans-in-dict-sections (needs extra=forbid closure),
comment-only consumers, units-wrong, or family γ.** The full closing set = BK-8 + forbid
everywhere + a name-sweep of this phase's shape (the scratchpad tool is a working
prototype of that third leg — noted, ⛔ not shipped). → IA-XCFG-02.

**(d) Constant-shadows-config — the sweep completed (from X-DUP's hand-off).** Exactly
**TWO true shadows exist** (a config key AND a module constant for the same tunable, equal
by luck): the X-DUP pair (`emergency_exit_buffer_pct` ↔ `EMERGENCY_EXIT_BUFFER_PCT`;
`sl_limit_offset_pct` ↔ `DEFAULT_SL_LIMIT_OFFSET_PCT`). The OTHER direction —
**hardcoded-governs-live with NO config authority** — inventories ~15 notable constants:
`DEFAULT_TICK` 0.05 (exchange fact — acceptable) · `_MIN_TRIGGER_DISTANCE_PCT` 0.0025 ·
`DEFAULT_CIRCUIT_MARGIN_PCT` 0.02 · `_G5B_SETTLING_WINDOW_SEC` 10 ·
`_HARD_KILL_MAX_RETRY_HOURS` 2 + the 5/15/45 retry ladder · `_EXIT_ALERT_DEDUP_SEC` 300 ·
`_IP403_ALERT_THROTTLE_SEC` 3600 · `_INVARIANT_TOLERANCE` ₹1.0 ·
`_RECOVERY_ABSENCE_POLL_BUDGET` 3 · `_OVERSELL_LOOKBACK/TOL` 300s/2% · the liveness
[09:00,16:00) and watcher [08,16) windows · the [08:00,18:15) boot cutoff — each
single-source (no divergence risk) but unratified (the G4 class; PB-01 remains the
flagship authority gap, KNOWN, Rama's decision). → IA-XCFG-03.

**(e) The delivery-flag dependency graph — COHERENT; no unguarded contradictory combination
found.** The four flags and their verified consumers: `delivery_enabled` (adapter CNC lock +
GTT-ungated-by-design + eod ctor) · `force_intraday_only` (adapter intent coercion — runs
BEFORE the CNC lock, so both-true is well-defined) · `conditional_allocation_enabled`
(main.py:2317 resolver ONLY — Q9-confirmed it never reaches FM; the (1.0, 0.0) leg makes a
delivery reserve fail gracefully, so enabled+no-delivery-strategies is defined) ·
`trade_type` (strategy control + the resolver's intraday_active input). #16a's
two-condition inertness (P4-verified) and the §7.1 gate's premise hold. Width: these four
flags × their session-verified consumers; not every flag pair in the file. **And the §7.1
premise's other half measured fresh: VM config == repo config, md5-identical ×3**
(system_config / broker_limits / scoring_weights) — config drift today rides only the
deployed-tree channel (IA-P10-01's scope). → IA-XCFG-04.

### XC.2 Findings

---
**IA-XCFG-01**
- **WHAT:** The config surface classified: 280 leaf keys → 3 orphan + 7 schema-only
  (automated) + ~13 semantically dead/ineffective (register) ≈ **23 dead ≈ 8%**; ~70
  positively-verified live (the flow tables); ~187 name-grep-only. Six of the dead keys
  are NEW this phase — headlined by `pipeline_timeout_sec` (a nonexistent per-signal
  safety deadline) and the two live_feed backoff knobs.
- **EVIDENCE:** the sweep output (counts + names); the three direct verifications
  (signal_processor 0-hit, live_feed 0-'backoff', the yaml orphan contexts); the register
  cross-validation (the tool re-found IA-P7-04 + IA-P5-08 blind).
- **CLASS:** Configuration / Correctness. **SYNTHESIS + NEW** (unifies IA-XARCH-01's α
  set with 6 new instances and produces the surface-wide numbers).
- **ROOT CAUSE:** family α's (keys ship schema-first; nothing closes declared→effect) +
  dict-typed sections escaping `extra=forbid`.
- **RECOMMENDATION (described, ⛔ not built):** the XC.1(c) closing set — BK-8 + forbid
  everywhere + the name-sweep as a nightly/CI leg; the scratchpad tool demonstrates the
  third leg costs ~80 lines.
- **SEVERITY-BY-IMPACT:** MED-HIGH — 8% of the config surface is instructions the
  operator can turn with no effect, two of them safety-flavored (a processing deadline, a
  reconnect pacing), and the class regrows with every schema-first addition.

---
**IA-XCFG-02**
- **WHAT:** The verification gap confirmed as total: no mechanism (config_auditor,
  pydantic, tests, CI) catches a dead knob, an orphan key in a dict section, a
  comment-only consumer, or a constant-shadow divergence; BK-8 as named would close
  family α only.
- **EVIDENCE:** config_auditor's actual coverage (session reads: strategy validation /
  go-live / slippage overrides / raise_if_blocked — width stated, not a fresh full-file
  audit); the 3 orphans surviving load_all (measured); X-TEST hand-off for the test claim.
- **CLASS:** Configuration / Invariant-coverage. **SYNTHESIS** (IA-XARCH-01's meta-cause,
  now with the exact per-class closing map).
- **RECOMMENDATION (described):** execute BK-8 for α; add `extra="forbid"` to the
  dict-typed sections (closes orphans loudly at load); adopt the name-sweep for the rest.
- **SEVERITY-BY-IMPACT:** HIGH as leverage (same rationale as IA-XARCH-01 — this is the
  mechanism that stops the class regrowing).

---
**IA-XCFG-03**
- **WHAT:** The single-source-of-truth sweep: exactly TWO config-vs-constant shadows
  (both equal-by-luck today, both on the emergency-exit path — IA-XDUP-01); ~15 notable
  behavior-governing constants with no config authority (the ratified-spec gap, G4 class)
  — single-source so no divergence risk, but untunable and unratified.
- **EVIDENCE:** XC.1(d) inventory with values; the X-DUP measurements.
- **CLASS:** Configuration / Consistency. **SYNTHESIS (IA-XDUP-01) + NEW width** (the
  "exactly two" bound and the ~15-constant inventory).
- **RECOMMENDATION (described):** the two shadows: one authority each (config), constant
  deleted or derived; the ~15 constants: no action needed EXCEPT a one-line ratification
  note each (G4's pattern) so a future tuner knows they are policy, not accident.
- **SEVERITY-BY-IMPACT:** MED (the two shadows are the live hazard; the rest is
  documentation).

---
**IA-XCFG-04**
- **WHAT:** The delivery-flag graph verdict (coherent; no unguarded combination) + the
  deploy-consistency verdict (VM config md5-identical to repo ×3 today; drift possible
  only via the deployed-tree channel).
- **EVIDENCE:** XC.1(e); the fresh md5 pairs.
- **CLASS:** Configuration / Safety. **KNOWN-COMPOSED, verdict NEW** (each edge was
  verified in a flow phase; the graph-level "no contradiction" statement and the config
  identity check are this phase's).
- **RECOMMENDATION (described):** none — record that the 4-Aug flip's flag semantics are
  internally consistent as configured; the flip plan's own sequencing rules (Q4 ordering)
  remain the binding constraints.
- **SEVERITY-BY-IMPACT:** LOW (a clean bill, recorded so the flip doesn't re-derive it).

### XC.3 Open questions

- **OQ-XCFG-1:** Why do the three orphan keys' sections tolerate unknown keys (dict-typed
  vs extra-allowed models)? One config_loader read per section settles it — folded into
  the -02 recommendation's forbid-everywhere step.
- **OQ-XCFG-2:** Of the ~187 name-grep-only keys, how many would the BK-8 check upgrade
  to verified-live for free? (Answerable only by building it — deliberately left there.)

### XC.4 Hand-offs

**X-TEST** inherits: no test fails on a dead knob (the α test shape = "every schema field
is either passed at the composition root or explicitly defaulted-with-intent"); the two
shadow constants have no divergence test. **X-DOCS** inherits the comment-vs-behavior
drift inventory (yaml comments promising dead mechanisms: the SP15 deadline, the Item-37
backoff, `auto_resume_kill_switch`, `backoff_sequence_sec`'s "then soft_kill") — the
config file is itself a document that lies in places. **X-SEC** inherits nothing new
(`whitelist_only`/`personal_chat_id_env` being dead is alert-plumbing, not an exposure).

**X-CONFIG done** = 280 keys classified with the live-% stated honestly in three tiers
(8% dead · ~25% positively-verified · the rest name-grep-only); six new dead keys found
(one safety-flavored); the verification gap confirmed with a per-class closing map and
BK-8 assessed against it; the shadow sweep completed (exactly two, both known); ~15
unratified governing constants inventoried; the flag graph and VM-config identity given
clean bills; committed incrementally; ⛔ nothing fixed, nothing pushed, the 3-Aug/4-Aug
sequence untouched.
*(X-DOCS / X-TEST / X-SEC / X-EVOLVE append below when commissioned.)*
