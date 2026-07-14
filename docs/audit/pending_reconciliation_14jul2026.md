# Pending-List Reconciliation — 14-Jul-2026 (read-only census)

**Task:** Reconcile `MASTER_PENDING_TRUE_FINAL_10JUL.txt` against the actual repo/ledger/audits at HEAD.
**HEAD:** `2dc69d5` (deployed). **Landed batch since 10-Jul:** `bb9b1e7..2dc69d5` (36 commits = Q4+Q5+P11+deploy).
**Method:** commit-log M-ID map (`git log --all`), code spot-checks at HEAD, config flag reads, VM read-only checks. No code/config/VM changed.
**Anti-dup (Section 7):** no prior reconciliation/census doc existed — this is the first.

> **Verdict key:** CLOSED (evidence pointer) · OPEN (still pending) · PARTIAL (half shipped — which half stated) · STALE (premise false / subsystem gone) · UNKNOWN (evidence named). Where an item has both paper+live paths, CLOSED requires both.

---

## Section A — VERDICT TABLE (every item)

### COMPLETED block (10-Jul list's "for reference") — re-verified
| ID | Item | Verdict | Evidence | Note |
|---|---|---|---|---|
| C-a | Waves 0-6 all closed; 12/13 Audit-A HIGH | CLOSED | H-1..H-13 fix commits (`git log --all`); H-6 excepted | H-6 delivery-gated = OPEN (below) |
| C-b | Fresh post-remediation audit | CLOSED | `full_system_audit_04july2026.md`, `audit_05jul2026.md` | Phases 9-10 never finished (Section I) |
| C-c | Wave-6 capital cluster M-O1/O2/C7/C3 | CLOSED | M-O1`959badf` M-O2`…` M-C7`…` M-C3`…` (all have fix commits) | deployed `d6e3302` |
| C-d | P1 broker-reconcile DEPLOYED SHADOW | PARTIAL | deployed `f135fee`; schema now v44 (`state_store.py:102`) | still `authoritative: false` — flip is OPEN (P0-3) |
| C-e | Schema-version fail-fast | CLOSED | `0b816dc` | |
| C-f | PUSH-1 infra C-3/C-4/F-1/E-4 + A-3 | CLOSED | `dd4f3f4`,`9bcc1eb` | |
| C-g | trade_type/intent Option A | CLOSED | `8116b74`; Block-1 verified 13-Jul (12 WILL/3 WON'T) | ledger |
| C-h | Risk-config 55→60 / 5→4 | CLOSED | `61ae9cc`; Block-1 verified (`min_pass_score:60`,`max_consecutive_losses:4`) | |
| C-i | T2 same-day validation 4/4 REAL | CLOSED | ledger T2 canary 10-Jul (GTT ids 327073639…) | overnight DDPI leg still OPEN |
| C-j | "PC=VM=bare @ 8116b74; ledger EMPTY" | STALE | HEAD now `2dc69d5`; ledger NOT empty (T2 + Q8-uncommitted) | premise superseded by 4 deploys |

### P0 — NEXT ACTIONS
| ID | Item | Verdict | Evidence | Note |
|---|---|---|---|---|
| P0-1 | Mon 13-Jul 08:15 runtime verification | CLOSED | ledger "BLOCK-1 BOOT VERIFICATION — PASS (13-Jul ~09:25)" | 12 WILL/3 WON'T, MIS-only 0 CNC, 60/4, shadow×2 |
| P0-2 | T2 --arm-overnight DDPI/holdings-sell proof | OPEN | ledger DEFERRED: "DDPI/holdings-sell still UNPROVEN" | last T2 piece; Rama-supervised |
| P0-3 | P1 authoritative flip | OPEN | `system_config.yaml:286 authoritative: false` | soak ongoing; ≥ several sessions needed |
| P0-4 | Merge T2 (854112b) to main | OPEN | `git log main..854112b` = 3 commits, only on `fix-t2-repair-07jul` | gated on P0-2 |
| P0-5 | Resume Wave-7 | CLOSED | executed as Q5 (`7f53ffb` triage + 14 fix commits) | remainder individually tracked below |

### P1 — HIGH / OPEN
| ID | Item | Verdict | Evidence | Note |
|---|---|---|---|---|
| P1-1 | H-6 CNC exit-day (last Audit-A HIGH) | OPEN | coverage-matrix DEL gate; `delivery_enabled:false` | delivery-gated |
| P1-2 | S-3 public SSH lockdown | OPEN | Q8 report §C2 (proposed, not applied); `sshd 0.0.0.0:22` | = Q8 control C2 |
| P1-3 | VM security hardening (foreign-IP SSH incident, time-lock RC, key rotation) | PARTIAL+STALE | Q8 report `q8_vm_security_breach_forensics_14jul2026.md` | investigation CLOSED; **"foreign-IP SSH incident" premise STALE (no breach; time-lock = copy_gate.py, never SSH)**; controls OPEN-deferred |
| P1-4 | Backups-all-on-one-disk (Audit-B DR HIGH) | OPEN | crontab backups → `data_store/backups/` (same disk) | no off-disk/offsite remediation found |
| P1-5 | M-DP1 deploy-hook stale VM paths | PARTIAL | LIVE hook + `deploy/hooks/post-receive` = correct `/systems/`; **stale duplicate `deploy/post-receive:23 CHECKOUT=/home/ubuntu/trading-system`** | operational risk RESOLVED; footgun file remains (record-don't-fix) |

### P2 — DELIVERY TRACK (gated)
| ID | Item | Verdict | Evidence | Note |
|---|---|---|---|---|
| P2-1 | T2 --arm-overnight DDPI proof | OPEN | = P0-2 | ACTIVE next T2 step |
| P2-2 | Slice 2.5 / CNC delivery lifecycle | OPEN | ledger "active delivery TRADING PARKED" | parked until triggered |
| P2-3 | DEL gate: M-C2 | OPEN | 0 fix commits | delivery-only |
| P2-4 | DEL gate: M-O4 | OPEN | 0 fix commits | EOD promotion sweep no product filter |
| P2-5 | DEL gate: M-O6 | OPEN | 0 fix commits | GTT _recreate flips ACTIVE before place |
| P2-6 | DEL gate: M-O7 | OPEN | 0 fix commits | GTT finalize skips release on missing entry |

### P3 — WAVE-7 (full MED batch, every ID)
| ID | Item | Verdict | Evidence | Note |
|---|---|---|---|---|
| M-K1 | get_today_closed_pnl date-key | CLOSED | `4ec9a9c` | IST exit-date key |
| M-R1 | legacy holiday guard broken | CLOSED | `aea9bb6` | |
| M-R2 | legacy P&L excludes CLOSED_MANUAL | CLOSED | `9a303ee` | |
| M-R3 | Block-4 false FAIL overnight CNC | CLOSED | `342b871` | keys by close date |
| M-R4 | Block-2 identity tautology | CLOSED | `23ddd29` + `mr4_block2_order_reconciliation_14jul2026.md` | block deleted |
| M-SC2 | screened_stocks_csv → non-existent DB | CLOSED | `522da32` | |
| M-SC3 | eod_cleanup reaper filters dead status | CLOSED | `501d14f` | IN_PROCESS→PROCESSING |
| P3-r8 | charges computed, never validated vs contract note (Ph8) | OPEN | 0 fix commits | manual parity task |
| P3-r9 | raw exec-log unlinkable, trade_id NULL | OPEN | 0 fix commits | set OrderFilled.trade_id |
| P3-r10 | W8 closure-source blindness | OPEN | 0 fix commits | add closure_source column |
| M-C4 | KillSwitch lock held through Telegram send | OPEN | 0 fix commits (`git log --all`) | analyst-flagged; nudged by A-3 |
| M-C5 | commit_adopted_entry race → hard_kill | OPEN | 0 fix commits | |
| M-C6 | zero-multiplier floored to 1 lot | OPEN | 0 fix commits | |
| M-C8 | hard_kill retry starves fill thread | OPEN | 0 fix commits | safety-adjacent |
| W10 | double-cost | OPEN | 0 fix commits | fail-safe today; cheap early-pull candidate |
| P3-c6 | flip daily_loss_include_unrealized | OPEN | 0 fix commits | Audit-B Ph6, post-soak |
| P3-c7 | _on_loss_breach invoked in-lock | OPEN | 0 fix commits | Audit-B Ph6 |
| P3-c8 | auto_resume_kill_switch wire-or-delete | OPEN | 0 fix commits | Audit-B Ph6 |
| P3-c9 | HARD_KILL exception in clear_stale_state | OPEN | 0 fix commits | SOFT_KILL family |
| P3-c10 | G3 broker-drift escalate on persistence | OPEN | 0 fix commits | Audit-B Ph6 |
| M-O3 | exit-retry no time escalation | OPEN | 0 fix commits | coupled to H-3 |
| M-O5 | fire_now leaves fired-flag on raise | OPEN | 0 fix commits | |
| M-O9 | trailed-SL bogus slippage rows | OPEN | 0 fix commits | feeds slippage Ph2/3 |
| M-S2 | QUEUE_FULL poisons dedup cache | CLOSED | `735a6c3` | backpressure recovers |
| M-S3 | per-step timeout broken (shared pool) | OPEN | 0 fix commits | |
| M-S4 | screening steps 1&3 always 0.0 (25/100 dead) | PARTIAL | substrate shipped OFF (schema v44 `daily_symbol_stats`, `candle_math.rsi`, forward-shadow recorder — 5 commits); **live bug PERSISTS: `secondary_screener.py:407 "atr": None`** | flags: `v3_hardgate_mode:"shadow"`, `min_pass_score:60`; fix needs re-parity+re-soak before enforce |
| M-S5 | re-entry guard only in _process_one | CLOSED | `976fa51`+`b549c27`; `signal_processor.py`; `test_hardening_scenarios.py` | hoisted to 3 entry paths |
| M-S6 | RetestDiverter double-entry | CLOSED | `b3a5c94` | + dormant (`wait_for_retest_enabled:false`) |
| M-S7 | rate-limiter busy-spin | OPEN | 0 fix commits | |
| M-S8 | unauth INSERT contends writer lock | OPEN | 0 fix commits | |
| P3-s11 | slippage guard best-effort fail-open (Ph4) | OPEN | 0 fix commits | |
| P3-s12 | DUPLICATE_SYMBOL blind spot (Ph4) | OPEN | 0 fix commits | |
| P3-s13 | CHECK1/RMS costs=0.0 (Ph4) | OPEN | 0 fix commits; `capital_operational_note` memory (RMS closes pass costs=0.0 by design) | verify intended-vs-bug |
| P3-s14 | webhook insert-fail dark window (=M-S2 area) | PARTIAL | M-S2 dedup-poison fixed `735a6c3`; insert-fail observability not separately addressed | |
| M-K2 | config-auditor per-strategy-window dead | OPEN | 0 fix commits | |
| M-K3 | db_connect no FK / synchronous=FULL | OPEN | 0 fix commits | cron writes FK-off; overlaps P4 raw-sqlite |
| M-K4 | time_authority.configure wipes skew cbs | CLOSED | `618222e` | preserves un-passed callbacks |
| M-K6 | migrations DDL extractor comment-blind | OPEN | 0 fix commits | |
| M-D1 | candle volume semantics wrong | STALE | ledger: "M-D1 fact SETTLED — volume_traded CUMULATIVE, latent under MODE_LTP, no change" | investigated → not-a-bug-in-practice |
| M-A1 | Telegram truncation splits entity | CLOSED | `c0cf3ee` | HTML-safe truncate + 4xx log |
| M-A2 | alert sends block reconnect/ticker | OPEN | 0 fix commits | |
| M-U1 | scanner preflight skipped on cold start | OPEN | 0 fix commits | |
| M-X2 | emergency notifier `except: pass` | CLOSED | `a8d8164` | failures no longer swallowed |
| P3-sys | schema-backed integration tests per raw-SQL money path + wired-in assertion per safety layer | PARTIAL | Q7 `4fe9188` wired scenarios for the LIVE Q4/Q5 hardenings only (gate-8×2, M-S5, M-S2) | NOT "every raw-SQL money path" |

### P4 — WAVE-8 / ARCHITECTURE + LOW (all deferred beyond Wave-7)
| ID | Item | Verdict | Evidence | Note |
|---|---|---|---|---|
| P4-1 | state_store god-object (126 importers) | OPEN | 0 commits; Audit-B Ph1 HIGH (arch) | plan-only |
| P4-2 | main.py drifting to god-file | OPEN | 0 commits (main.py grew: +check_kill_switch etc.) | |
| P4-3 | layering erosion ×5 (incl. private import) | OPEN | 0 commits | import-boundary test |
| P4-4 | 19 raw sqlite sites outside db_connect | OPEN | 0 commits | overlaps M-K3 |
| P4-5 | two report generators in parallel | OPEN | 0 commits | sunset date; overlaps M-R1/R2 (fixed) |
| P4-6 | SYSTEM_MAP.md size won't scale | OPEN | 0 commits; SYSTEM_MAP now LARGER | split needed |
| P4-7 | PATHS.md accretion / gui untracked / pytest skew | OPEN | 0 commits | |
| P4-8 | Gaps: README, arch doc, docstrings, ops/__init__ | OPEN | 0 commits | |
| P4-9 | Debt: overdue deletions, orphans, gemini_common dup | OPEN | 0 commits; **+ new instance: stale `deploy/post-receive` dup (Section G)** | |
| M-K5 | config snapshot persists unredacted password | OPEN | 0 fix commits | security-hygiene |
| M-X1 | duplicate tick-rounding implementations | OPEN | 0 fix commits | |
| P4-low | LOW groups ×4 (correctness/latent, fail-quiet, security/hygiene, consistency/dup) | OPEN | 0 commits; source = `full_system_audit_04july2026.md` (~55 items) | group-level |
| P4-op | operator review of policy numbers (Ph6) | OPEN | Rama decision | |

### CO GATE (only if Cron Officer enabled)
| ID | Item | Verdict | Evidence | Note |
|---|---|---|---|---|
| CO-1 | order_protocol dead-config / modify_order variety / CO entry never validated / M-O8 CO trail-dead / CO single-backstop | OPEN | 0 commits; CO inactive | **corroborated 13-Jul**: `order_protocol` IS dead → 100% LIMIT_TRIPLE → no active exit engine (`naked_trades_order_protocol_dead_13jul`) |

### P5 — QUICK / INDEPENDENT
| ID | Item | Verdict | Evidence | Note |
|---|---|---|---|---|
| P5-1 | .gitignore / credentials.xlsx commit rule | PARTIAL | prevention infra live (secret-scan hook, `.env` gitignored, `.env.example` placeholder — SYSTEM_MAP); credentials.xlsx-specific rule unverified | |
| P5-2 | B-1 observability follow-up (heartbeat/metrics) before B-1 flip | OPEN | 0 commits | |
| P5-3 | daily_report retirement | OPEN | 0 commits; two-generator overlap (P4-5) | |
| P5-4 | test_control_tower_phase1a.py:154-155 POSIX-path fix | OPEN | 0 commits referencing it | trivial; PC-env only |

### DATA-GATED
| ID | Item | Verdict | Evidence | Note |
|---|---|---|---|---|
| DG-1 | S&R V2 calibration (~50 filled + 8-10 BIR, ~14-Jul) | OPEN (data-gated) | fills MET: **146 closed trades** (VM `trades` ≥50); 99 `trade_excursions`; `intraday_anchors_enabled:false` | **BIR W/L count = UNKNOWN** (needs Rama's manual zone-marking via `sr_level_export.py`) |
| DG-2 | Slippage Phase-3b | OPEN (data-gated) | 0 commits | |
| DG-3 | B-1 ENFORCE flip | OPEN (data-gated) | `authoritative:false`; needs clean shadow week + MTM spot-check | tied to P0-3 |

### NEEDS RAMA'S INPUT
| ID | Item | Verdict | Evidence | Note |
|---|---|---|---|---|
| NR-1 | C-2 network hardening — Option B (reverse proxy + HMAC) | OPEN | `require_hmac:false`; token-auth only | Rama-owned |
| NR-2 | credentials.xlsx deletion (PC action) | OPEN | Rama PC action | gitignored already |
| NR-3 | GUI Tailscale / external access setup | CLOSED | Tailscale serve LIVE `https://trading-system.tail1cdc6d.ts.net`; VM `tailscaled` on `100.74.84.44:443` (Q8 `ss`) | completed-not-struck-off |
| NR-4 | Stray D:\ folders (c, home, nonexistent_xyz) | OPEN | Rama PC action | confirmed safe to delete |

### BACKLOG
| ID | Item | Verdict | Evidence | Note |
|---|---|---|---|---|
| BK-1 | LONG-strategy review (13% vs 58% SHORT) | OPEN | 0 commits; unscheduled | |
| BK-2 | Live test scenarios (design+build) | OPEN | Section 10 = not-in-scope now | |
| BK-3 | 6 Word docs (incl. missing Doc4) | OPEN | non-code | |
| BK-4 | Windows Phase-1 doc updates | OPEN | non-code | |
| BK-5 | CT crash-test drills | OPEN | off-hours sandbox | |
| BK-6 | Remaining DB schema cleanups (~2 of 9) | OPEN | 0 commits | |

### AUDIT B PHASES 9-10
| ID | Item | Verdict | Evidence | Note |
|---|---|---|---|---|
| AB-910 | Confirm Phases 9 (Ops) + 10 (Security) completed/received | OPEN (absent) | `audit_05jul2026.md:819` ends "Batch 5 running… Phases 1–8 consolidated"; coverage-matrix §4 confirms absent | **NEVER completed** — PENDING, not clean (Section I) |

### SEPARATE TRACK
| ID | Item | Verdict | Evidence | Note |
|---|---|---|---|---|
| ST-1 | ICSI Paper 5 notes | OUT-OF-SCOPE | per instruction §10 | non-trading |

---

## Section B — CLOSED (with SHAs)
Landed in `bb9b1e7..2dc69d5` (36 commits) unless noted:
- **Q4 capital-safety:** gate-8 sector-TOCTOU `330fa38`+`9050ba9`+`7c76408` (verified `capital/risk_engine.py:254/313/351`); kill_switch=None boot fail-fast `8ea1a4e` (verified `main.py:2177`); structure_exit∩trailing_sl guard `c1eea66`.
- **Q5 Wave-7:** M-K1 `4ec9a9c` · M-R1 `aea9bb6` · M-R2 `9a303ee` · M-R3 `342b871` · M-R4 `23ddd29` · M-SC2 `522da32` · M-SC3 `501d14f` · M-S2 `735a6c3` · M-S5 `976fa51`+`b549c27` · M-S6 `b3a5c94` · M-A1 `c0cf3ee` · M-K4 `618222e` · M-X2 `a8d8164` · P10 eod_cleanup `2e61fad` · P5 alert-watcher --loop `34c2993` · quote-noise `c22a25c` · soak-report `9550dfd`.
- **P11:** migration-on-open guard `ed1c4b9`.
- **Earlier (≤bb9b1e7, still at HEAD):** Wave-6 M-O1/O2/C7/C3, P1 SHADOW `f135fee`, schema fail-fast `0b816dc`, PUSH-1 `dd4f3f4`/`9bcc1eb`, trade_type `8116b74`, risk-config `61ae9cc`, all H-1..H-13. GUI Tailscale (G2c). P0-1 boot verify (ledger). P0-5 Wave-7 resumed.

## Section C — OPEN, re-prioritised by CURRENT risk
1. **Finding-1 — `trades.sector` NULL → 40% sector cap never fires** (DISCOVERED, Section G). Blocks D1. Highest latent capital-safety gap.
2. **M-S4 (PARTIAL)** — 25/100 score points dead in the LIVE scorer; distorts every selection. Fix gated on re-parity+re-soak.
3. **P0-3 / DG-3 P1 authoritative flip** — the last Audit-B P&L HIGH stays unclosed while SHADOW.
4. **M-C4 / M-C8 / M-K4-family capital+kill safety MEDs** (M-C4 lock-held-through-send, M-C8 fill-thread starvation) — safety-adjacent, unfixed.
5. **Audit-B Phases 9-10 never completed** — an entire Operations+Security audit slice is unaudited (not "clean").
Then: H-6 + DEL cluster (delivery-gated), backups-DR HIGH, M-S3/S7/S8, M-O3/O5/O9, M-K2/K3/K6, Phase-6 recs, Phase-8 data-quality, all P4 arch/LOW, CO gate, backlog.

## Section D — PARTIAL (which half shipped)
| Item | Shipped | NOT shipped | Flag + value |
|---|---|---|---|
| M-S4 | substrate: schema v44 `daily_symbol_stats`, `candle_math.rsi`, `core.daily_stats`, forward-shadow recorder+cron (5 commits, collecting out-of-sample) | the live scorer fix — `secondary_screener.py:407 "atr": None` unchanged; steps 1&3 still 0.0 | `v3_hardgate_mode:"shadow"`, `min_pass_score:60` (live), `v3_min_pass_score:50` (inert) |
| M-DP1 | LIVE + canonical `deploy/hooks/post-receive` = `/systems/` (deploys land) | stale duplicate `deploy/post-receive` still `CHECKOUT=/home/ubuntu/trading-system` | — |
| P1-3 VM security | Q8 investigation complete (no breach) | controls C1/C2/C3/C5 (deferred by Rama) | `authoritative`-style: n/a |
| P3-sys integration tests | Q7 `4fe9188` wired scenarios for gate-8×2/M-S5/M-S2 | coverage of every raw-SQL money path + per-safety-layer wired-in assertion | — |
| P3-s14 webhook insert-fail | M-S2 dedup-poison `735a6c3` | insert-fail observability/dark-window logging | — |
| C-d P1 deploy | deployed SHADOW, schema v44 | authoritative flip | `authoritative:false` |
| P5-1 credentials rule | secret-scan hook, `.env` gitignored, `.env.example` placeholder | credentials.xlsx-specific rule (unverified) | — |

## Section E — STALE (premise false / subsystem gone)
- **P1-3 "foreign-IP SSH incident + time-lock failed"** — Q8 proved NO breach; the 18:00–08:00 "time-lock" is `scripts/copy_gate.py` (VM→PC copies), was **never an SSH control**; the "foreign IP" was a brute-force bot with 0 successes; the "unexpected key" was Rama's own un-baselined rotation. *(The exact precedent Section 5e warns about — we nearly hardened a fiction.)*
- **M-D1 candle volume semantics** — investigated → `volume_traded` is CUMULATIVE and only latent under MODE_LTP (unused); no change needed (ledger).
- **C-j "PC=VM=bare @ 8116b74 / ledger EMPTY"** — superseded by 4 deploys (`c1ad82e`→`5a71fb6`→`277d63e`→`2dc69d5`); ledger now carries T2 + Q8.
- **M-O1** (already self-noted false-positive in the 10-Jul list; NSE:NSE: double-prefix was a non-bug) — confirmed, has a defensive commit but premise was false.

## Section F — UNKNOWN (evidence needed)
- **DG-1 S&R V2 BIR-outcome count** — fills gate MET (146 closed ≥ ~50), but the 8-10 "BIR-filled W/L" count cannot be auto-derived. *Need:* Rama's manual zone-marking pass over `scripts/sr_level_export.py` output joined to filled-trade outcomes; or a BIR-classification query on `sr_detector_results`. Until then the calibration PASS/RECALIBRATE decision is unresolvable.

## Section G — DISCOVERED (pending, never on any list)
1. **Finding-1 — `trades.sector` is NULL on 100% of rows → `StateStore.sector_exposure()` returns 0 for the resting book → the 40% sector cap has NEVER summed open positions** (a control that reports PASS while doing nothing). Harmless today (~₹198/pos « ₹3,957 cap) but **D1 (raise concentration) is BLOCKED until `trades.sector` is populated at insert.** gate-8's TOCTOU fix is real but its resting-book term is structurally 0 = INCOMPLETE. Source: `pb01_shadow_deploy_14jul` / Q8. **Not on the 10-Jul list.**
2. **No active exit-management engine** — `order_protocol` dead-config → 100% LIMIT_TRIPLE → naked SL/TGT/15:17-squareoff (`sl_trail_count=0/134`); corrects the "Step-8 SmartTgt = sole live SL owner" claim. Source: `naked_trades_order_protocol_dead_13jul`. Partially overlaps CO-GATE but the "no exit engine live" framing was never tracked.
3. **Stale `deploy/post-receive` duplicate** with wrong `/home/ubuntu/trading-system` path — a silent-no-op footgun if ever installed (the canonical `deploy/hooks/post-receive` is correct). Cleanup item, on no list.
4. **Q8 docs uncommitted** — `docs/audit/q8_vm_security_breach_forensics_14jul2026.md` (untracked) + `PATHS.md`/`SYSTEM_MAP.md` (modified) sit in the working tree, unpushed (Section 4 answer).

## Section H — FLAG / SCAFFOLDING INVENTORY (current values at HEAD)
| Flag | Value | Migration status |
|---|---|---|
| `scoring.v3_hardgate_mode` | `"shadow"` | INCOMPLETE — soak ≥5 sessions before enforce; scaffolding legitimately present |
| `system.portfolio_allocator.allocator_mode` | `"shadow"` | INCOMPLETE — regret-observer only; enforce gated |
| `system.v3_chain_mode` | `"shadow"` | ACTIVE (binary off\|shadow; no enforce in 10a by design) |
| `eod_broker_reconcile.authoritative` | `false` | INCOMPLETE — P1 SHADOW; flip pending (P0-3) |
| `watchlist.enabled` | `true` | ACTIVE — PB-01 capture/entry, analysis-only (no order) |
| `strategies/pb01_breakout_retest.enabled` | `false` (+`v3_playbook:true`) | fail-closed by design |
| `regime.enabled` | `false` | off (not constructed) |
| `sr_detector.intraday_anchors_enabled` | `false` | off (awaits manual marking) |
| `shadow_tracker.enabled` | `true` | active (multi-inning) |
| `smart_tgt.enabled` | `true` | active (live SL owner) |
| `structure_exit_enabled` | `false` | off (single-SL-owner rule; Q4c guard now enforces) |
| `wait_for_retest_enabled` | `false` | off (M-S6 path dormant) |
| `force_intraday_only` / `trade_type` / `delivery_enabled` | `true` / `INTRADAY` / `false` | delivery TRIPLE-LOCKED |
| `require_hmac` | `false` | C-2 Option-B (reverse-proxy+HMAC) not yet built |
| `min_pass_score` / `v3_min_pass_score` | `60` / `50` | live 60; v3 50 inert until enforce |
| `EXPECTED_SCHEMA_VERSION` | `44` | live DB migrated (Q8-confirmed) |
| P5 alert-watcher `--loop` | default-off | shipped `34c2993`, opt-in |

**Scaffolding that will need removal once migration proven:** `v3_hardgate_mode`, `allocator_mode` (both shadow → enforce → remove FCFS/OLD path); `authoritative` (→ retire eod_verify). None are removable yet (soak incomplete). First-party TODO/FIXME/XXX/HACK census: **1 / 0 / 1 / 0** (venv-excluded) — codebase is markedly clean of debt markers.

## Section I — Audit-B Phases 9-10
**ABSENT / NEVER COMPLETED.** `audit_05jul2026.md` ends at line 819: *"Batch 5 running (Agent-I: Operations …; Agent-J: Security posture). Phases 1–8 are consolidated in the report."* No Phase-9 or Phase-10 deliverable exists in the repo (searched `docs/audit/`). Coverage-matrix §4 corroborates ("Cannot confirm — Phases 9 and 10 are not in the uploaded file"). **Consequence:** an Operations + Security audit slice was never produced. Some subject matter was addressed piecemeal since (the stuck alert-watcher/security-watcher — Phase-9's stated priority — are now live and delivering; Q8 covered part of the Security-posture ground and found no breach), **but the formal Phases 9-10 remain PENDING, not clean.** Treat as OPEN.

## Section J — Coverage-matrix integrity
The `coverage_matrix_05jul2026.md` map remains **accurate for the audits it covers** (Audit A + Audit B Phases 1-8): every labelled finding maps to a wave/gate/track, and §2 "Uncovered findings: None" still holds — no Audit A/B finding is unmapped. **Caveat:** three material issues discovered *after* 05-Jul fall outside the matrix by construction and are tracked only in the ledger/memory (now also Section G here): Finding-1 (`trades.sector` NULL), the no-active-exit-engine framing, and the Q8 security findings. The matrix's own §4 correctly flagged Phases 9-10 as absent — that flag is still live (Section I).

---

## Honest closure statement (§9)
Every item on the 10-Jul list has a verdict with an evidence pointer. **If the two source files were deleted tomorrow, nothing pending would be lost** — this document + the ledger + memory carry it — **with two explicit exceptions to name:** (1) the **DG-1 S&R BIR-outcome count is UNKNOWN** and depends on Rama's manual zone-marking (not recoverable from code alone); (2) **Audit-B Phases 9-10 were never produced**, so their unaudited findings are unknowable until that audit is run. Both are stated, not hidden.
