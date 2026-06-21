# Pre-flight — AUTHENTIC Phase-A dry-run (real VM data) — for BINDING sign-off

Rendered from a **real run on the VM** (real DB, services, config, security state),
Sunday 21-Jun 11:17 IST, simulating `--as-of-date 2026-06-22` (Monday), `--dry-run`
(read-only; nothing sent, no mutation). The inert package is deployed on the VM
(commit `4fd3d3e`); **no cron entries, no schema migration, no activation**.

Open `preflight_phase_a_authentic.html` in a browser for the rendered report.

## Result: CRITICAL_FAILURE — 44 checks · 33 pass · 3 critical · 2 warn · 6 skip

### The 3 criticals — how to read them
| Check | Verdict |
|---|---|
| `kite_token_file_exists` | **Sunday-accurate** — token wiped 05:00 daily, `auto_refresh_token` is Mon-Fri so no weekend token. On a real **Monday** after the 08:15 refresh this PASSES. |
| `kite_token_fresh_today` | Same root cause (no token on Sunday). PASSES Monday. |
| `kite_instruments_fresh` | **🔎 REAL FINDING** — `config/instruments.csv` on the VM is stale (mtime **2026-05-03**, ~7 weeks). `refresh_instruments.py` writes this file (RI13) and runs 09:00 Mon-Fri, so it should be recent. **This is the pre-flight's first real catch — investigate `refresh_instruments` on the VM** (separate ops item, not a pre-flight bug). |

### The 2 warnings — both accurate
- `long_strategies_enabled` — 9 LONG strategy configs present (LONG underperforms; review pending). Visibility-only.
- `kill_switch_state` — SOFT_KILL from 2026-06-19 (prior-day) → the app auto-clears prior-day kills at startup. Accurate, non-blocking.

### The 6 skipped — all correct
- `kite_profile/margin/funds/orders` — no broker session on Sunday (no token) → SKIP (run Monday).
- `vm_ntp_sync` — `ntplib` not installed on the VM (optional install to enable).
- `log_rotation` — no prior-day system log (Sat was non-trading).

## What this proves
The pre-flight runs end-to-end on **real VM data**, renders correctly (HTML +
Telegram + sentinel), is **ALERT-ONLY** (exit 0, never blocks), made **no
mutations** (dry-run), and **already surfaced a real ops issue** (stale
instruments). The periodic-service false-FAIL found in the first pass is fixed
(commit `4fd3d3e`).

## Files
- `preflight_phase_a_authentic.html` — the rendered email report
- `preflight_telegram_authentic.txt` — Telegram MarkdownV2 + subject
- `sentinel_authentic.json` — the `data_store/preflight/today.json` payload
- `dryrun_authentic.log` — the terminal report

> On a real Monday (token present + `.env` sourced by cron), the expected result
> is the token criticals clearing → only the **instruments** critical (until
> `refresh_instruments` is fixed) + the 2 warnings remain.
