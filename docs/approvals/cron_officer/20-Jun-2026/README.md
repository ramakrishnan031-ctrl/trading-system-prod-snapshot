# Cron Officer — Rich-Format Approval Samples (locked snapshot, 20-Jun-2026)

These are the **approved** Cron Officer email/report layouts. Rama signed off on
these exact designs before the revision was activated (activation commits
`f8edc4a` + `2d67676`; crontab reinstalled 21-Jun-2026 with `crontab -l | diff`=0).

**Do NOT modify these files.** They are a frozen approval record. Any future
Cron Officer redesign must create a **new dated subdirectory** (e.g.
`docs/approvals/cron_officer/<DD-Mon-YYYY>/`) with its own samples — never
overwrite this one.

Open the `.html` files in any web browser to see what the approved emails look
like (no VM/SSH access needed).

| File | What it shows |
|---|---|
| `sample_morning_briefing.html` | The 09:20 IST morning briefing email (what's scheduled today). |
| `sample_clean_eod.html` | The 18:50 IST EOD report email on a clean day (all jobs OK). |
| `dryrun_friday_19jun.html` | A real dry-run rendered on the VM database, as-of Fri 19-Jun (17 done / 3 missed / 1 ⏸ pending) — the full per-task table. |

## Why these are tracked here (and not in `reports/cron_officer/`)
`reports/cron_officer/` is **gitignored** (it's a generated-output directory —
future dry-runs would otherwise pollute git, and a generated file already got
swept into a commit once). These three samples are the *approved* snapshot, so
they are copied here, into a tracked `docs/` location, to preserve the approval
record in git history permanently.

## Context
- Source of truth for cron jobs: `config/cron_registry.yaml`
- Renderer: `scripts/cron_report_render.py`
- Full activation record: `docs/SYSTEM_MAP.md` changelog (2026-06-21 entry)
