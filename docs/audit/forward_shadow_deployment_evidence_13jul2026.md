# FORWARD-SHADOW — DEPLOYMENT EVIDENCE (baseline before any forward-shadow-driven decision)

**Date:** 13-Jul-2026 (off-market) · deploy `5a71fb6 → d23239c` · recorder `scripts/forward_shadow_record.py`
(`method_version fs-v1`). Verified on the DEPLOYED VM (branch tests do not prove a cron runs). **RECORDS ONLY —
no orders, no production config change; M-S4 stays OFF.**

## V1 — cron installed on the VM (live crontab, not the registry)
```
# forward_shadow_record  [18:15 Mon-Fri]
15 18 * * 1-5 cd /home/ubuntu/systems/trading-system && set -a && . ./.env && set +a && \
  /home/ubuntu/systems/venv/bin/python scripts/forward_shadow_record.py >> logs/cron-forward-shadow.log 2>&1; \
  rc=$?; ... echo "$rc $(date -Iseconds)" > data_store/cron_marks/forward_shadow_record.done
```
Present in `crontab -l`. **Next scheduled execution: tomorrow (14-Jul) 18:15 IST**, market days only.

## V2 — the Telegram failure alert FIRES (the most important check) — VERIFIED
Forced a real failure (`--db /tmp/q3` — a directory sqlite cannot open) → the recorder crashed
(`sqlite3.OperationalError: unable to open database file`, exit **1**) → **P2 caught it and wrote a critical
sentinel** `data_store/critical_alert_20260713_233122_2506c85a.flag` titled *"FORWARD SHADOW recorder FAILED
(research job; no trading impact)"* → the alert-watcher **delivered it** (the flag is now `...233122....delivered`,
i.e. Telegram + email sent). **A crash in the recorder alerts and never affects trading.**

*Alert-watcher note (a false alarm I raised then corrected):* `alert-watcher.service` shows `activating /
auto-restart` with a huge `NRestarts` — this is **NOT a crash loop**. `alert_watcher.py` runs `--once` by
default (poll one pass, exit 0); the unit is `Restart=always, RestartSec=10`, so it polls every ~10s via
systemd restart. It works (it delivered the V2 sentinel). A `--loop` mode would cut the restart churn — a
low-priority cleanup, not a bug.

## V3 — rate-limiting exercised in a real run — VERIFIED
A real run (`--date 2026-07-13 --db <copy>`) fetched daily + 1-min candles for the day's signalled symbols,
**every fetch routed through the SAME broker `RateLimiter` the live `OhlcFetcher` uses** (`rate_limiter.acquire
("historical")`, ≤3 req/sec — no second throttle). Completed in **111 s** (paced, not instantaneous),
**wrote 3467 records, 3218 simulated**. The rate limiter is genuinely in the path.

## V4 — append-only + idempotency across executions — VERIFIED
Ran the recorder twice for the same date. Run 1: wrote 3467 records. Run 2: *"nothing new (3467 present)"* —
skipped every seen `signal_id`, appended nothing (record count 3467 → **3467, unchanged**). No duplication, no
truncation, no rewrite. The file is append-only and immutable (provenance-stamped: method_version/git_commit/
weights_sha/config_sha).

## No production impact — VERIFIED
The verification runs used `--db <copy>` — the **live `trading_system.db` was untouched** (mtime unchanged),
live analytics candles untouched, **no orders placed, no production config changed** (`min_pass_score: 60`,
scorer weights, M-S4 OFF, shadow×2 all confirmed unchanged post-deploy). 166 branch tests pass.

## PENDING (tomorrow) — will be appended to this report
- **V5 — the first SCHEDULED run (14-Jul 18:15):** confirm it lands cleanly (records produced, no errors).
- **Schema v43 → v44 migration at the 08:15 boot:** drill-proven on a live-DB clone + inert empty table, BUT a
  failed migration blocks startup and loses a trading day — **must be checked at tomorrow's boot and reported.**

## Baseline established
The forward shadow is live and verified. From 14-Jul it records every scored signal's old + M-S4 score/band,
live decision, true-path sim outcome, and realised P&L — the **out-of-sample evidence** required (with Rama's
approval) before any `min_pass_score` decision. **The current-dataset band-inversion finding is small
(min_pass≈50 → ~breakeven, NOT an edge) and unconfirmed forward; the system needs a better signal, not a better
cutoff.**

---
*Read-only verification. Production trading config unchanged. M-S4 OFF.*
