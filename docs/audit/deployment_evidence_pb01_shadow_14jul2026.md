# DEPLOYMENT EVIDENCE — Q4+Q5+P11 bundle + PB-01 SHADOW flip (14-Jul-2026, off-market)

**Deployed commit: `277d63e`** (`bb9b1e7..277d63e`, 35 commits = 34 reviewed core + 1 final PB-01 config flip).
**Window:** 14-Jul-2026, ~18:5x IST, market CLOSED, trading service inactive/dead (clean 16:00 self-exit).
**Authorisation:** Rama's explicit GO (both gates PASS: S1 empirical §D — duplicate-signal set EMPTY, QUEUE_FULL=0, 343-trade dataset; S2 V5 forward-shadow cron rc=0 @18:15:59, idempotency proven by MD5).

## What shipped
- **Q4 capital-safety hardenings** (5): gate-8 SECTOR_EXPOSURE TOCTOU (`_effective_sector_margin` folds reserved-not-placed, `max()`-floored) · kill_switch=None boot fail-fast in LIVE · structure_exit∩trailing_sl two-SL-owner boot guard.
- **Q5 Wave-7 audit backlog** (report/hardening path): M-K1 IST exit-date key · M-S6 RetestDiverter double-order · M-S5 shadow-inning guard hoisted to all 3 entry paths · M-A1 HTML-safe Telegram truncate · M-S2 QUEUE_FULL dedup recovery · P10 REJECTED_* prune · M-R3 CAPITAL recon close-date key · M-K4 skew-callback preserve · M-R1/M-R2 daily-report holiday/CLOSED_MANUAL · M-SC2/M-SC3/M-X2 · soak-report crash · quote-noise flood · P5 alert_watcher --loop · M-R4 tautological recon block deleted.
- **P11 migration-on-open guard** (`ed1c4b9`): only `main.py` boot (allow_migrate=True, off-market) may migrate the live DB; 23 other openers refuse + fire a CRITICAL sentinel.
- **Q7** wired hardening integration scenarios; **Q9** GUI state report (docs).
- **Final flip (`277d63e`, config-only 2-line):** `v3_chain_mode off→shadow` + `watchlist.enabled false→true`. PB-01 strategy `enabled` STAYS false (fail-closed).

## Pre-deploy gates (PC, read-only)
| Gate | Result |
|---|---|
| Git tree survived power outage | branch `q5-audit-backlog-14jul`, tip `ce9e4d3`, **clean** |
| deploy_preflight.py | **PASS** — PC/VM UTC skew 2s ≤120s; VM market CLOSED |
| Merge topology | `main`==`bb9b1e7`, merge-base==`bb9b1e7`, **pure fast-forward** (0-behind/34-ahead) |
| Schema at tip | EXPECTED_SCHEMA_VERSION = **44** (== live v44 → no migration) |
| Boot guard Q4b (kill_switch) | `KillSwitch()` constructed unconditionally `main.py:1590`; guard `:2177` → logs OK, no abort |
| Boot guard Q4c (structure_exit) | `structure_exit_enabled: false` (only value) → no two-SL-owner abort |
| PB-01 flip diff | exactly 2 lines (`2 2` numstat), config only, PB-01 strategy file untouched |
| config_loader.load_all() (PC) | **CONFIG_LOAD_OK** (extra=forbid) with flipped values |
| Cron | 34 commits touch ZERO cron files → crontab install is an idempotent no-op |
| Sizing | position_sizer / scoring_weights / leverage_map / fund_manager / quality_scorer **UNTOUCHED** |

## Post-deploy verification (VM, `277d63e`)
| S5 check | VM result |
|---|---|
| Bare HEAD == deploy tip | ✓ `277d63e` |
| PC == VM == bare | ✓ all `277d63e` |
| `v3_chain_mode` | ✓ **shadow** |
| `watchlist.enabled` | ✓ **true** |
| PB-01 strategy `enabled` (explicit) | ✓ **false** (fail-closed) |
| Delivery triple-lock | ✓ force_intraday_only:true · trade_type:INTRADAY · delivery_enabled:false |
| `min_pass_score` | ✓ **60** (unchanged) |
| Live DB schema_version (READ-ONLY sqlite3) | ✓ **44** (v44==v44, no migration) |
| config_loader.load_all() (VM) | ✓ **CONFIG_LOAD_OK** |
| post-receive | "crontab AUTO-INSTALLED from canonical" (VM generate==canonical; idempotent — no cron change) |

**No restart owed** — service is down for the day; the next **08:15 boot loads `277d63e`.**
**Rollback** = revert `main` to `bb9b1e7` + off-market restart, as ONE unit (config-only rollback alt: set both flags back to off).

## Tomorrow (15-Jul) — the supervised session
- **08:15 boot predicted CLEAN**: no migration (v44==v44) · no kill_switch abort (wired) · no config-auditor abort (structure_exit off). **ANY boot abort / migration line / MigrationNotPermitted sentinel → STOP AND REPORT.**
- **Falsifiable claim: session-1 trade count ≈ pre-deploy.** Fill §7 of `docs/audit/deploy_behaviour_delta_prediction_14jul2026.md` with observed values. **ANY reject not mappable to a §A/§B trigger → STOP, REPORT, be ready to revert.**
- **Most likely source of a session-1 delta = M-S5 shadow-inning guard** (Finding 2: 112 simulated innings / 257 → real activity). WATCH: >a couple `SHADOW_INNING_ACTIVE` rejects in one session, OR any such reject on a symbol with NO active inning (guard mis-firing) → STOP.
- **PB-01:** first EOD capture fires at tomorrow's close; first entry stage runs Thursday morning. Watch both.
- **Fast-follow (safe):** `watchlist.max_symbols` cap.

## Finding 1 — `trades.sector` is NULL on 100% of rows (latent, harmless today, blocks D1)
`StateStore.sector_exposure()` filters `... AND sector = ?`; with `trades.sector` NULL it always returns 0 for the resting book. **The 40% sector cap has never summed open positions — it reports PASS while doing nothing** (the 5th of this family: M-SC2, M-R1/M-SC3, M-R4, this). Gate-8 hardened the TOCTOU race but the resting-book term it races on is structurally 0 → gate-8 is INCOMPLETE, not wrong (it still weighs live candidate + in-flight). **Harmless today** (positions ~₹198 margin, concentration-capped; peak all-sector margin ever ₹2,742.91 « ₹3,957 cap). **Becomes critical the moment `max_concentration_pct` is raised** — bigger positions with a dead sector cap. **ACTION (own review+prediction+deploy, NOT tonight): populate `trades.sector` at insert from the same `_resolve_sector` gate-8 uses; make it a HARD PREREQUISITE of any sizing increase. D1 (raise concentration) is BLOCKED until `trades.sector` is populated.**
