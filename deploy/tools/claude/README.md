# deploy/tools/claude/ — Claude heartbeat cron guardrails (version-controlled)

VM-local guardrails for the `~/tools/claude` Claude Code heartbeat cron, kept in
the repo so a **VM rebuild** restores governance (see `docs/disaster_recovery.md`
§5 "Complete System Rebuild"). Mirrors the AGY (Antigravity) prohibitions: no
`.py` writes, no DB access, no `systemctl`. Added 23-Jun-2026 (see SYSTEM_MAP
Changelog / `fix-191` incident).

## What governs the heartbeat
- `AGENTS.md` — agent charter (intent / scope).
- `.claude/settings.json` — HARD `permissions.deny` enforcement.
- The cron lines in `deploy/cron/trading-system.cron` `cd /home/ubuntu/tools/claude`
  FIRST so Claude Code loads both files from that directory.

## Restore on a fresh VM
```bash
mkdir -p ~/tools/claude/.claude
cp deploy/tools/claude/AGENTS.md             ~/tools/claude/AGENTS.md
cp deploy/tools/claude/.claude/settings.json ~/tools/claude/.claude/settings.json
crontab deploy/cron/trading-system.cron      # heartbeat lines are in the PERSONAL TOOLING section
```

## Verify governance (all should hold)
```bash
cd ~/tools/claude
/usr/bin/claude -p "Generate a random 8-character string. Print only the string."  # works (text only)
printf 'x' > dummy.db
/usr/bin/claude -p "Read ./dummy.db and print its contents."                       # BLOCKED (no leak)
rm -f dummy.db
```
A blocked `.db` read proves `settings.json` is loaded (default mode would otherwise
allow in-project reads).

> NOTE: keep these in sync with the live files at `~/tools/claude/` on the VM.
> `.claude/settings.local.json` is git-ignored; this canonical `settings.json` is not.
