# docs/web_claude/ — Web Claude Instruction Archive

This folder contains the complete instruction trail from Web
Claude (claude.ai chat sessions) across the entire v2 lifecycle:
**build → deployment → live operations**.

Web Claude is stateless between chats. VS Code Claude has
mempalace. This archive is the permanent record of
architectural decisions, build instructions, deployment steps,
and operational playbooks that any future Claude (or engineer)
can read.

## Folder structure

### 00_source_documents/
The authoritative project documents:
- `v2_design_spec.md` — full system design specification
- `locked_decisions.yaml` — all locked architectural decisions
  (~200 entries, governs everything)
- `g1_to_g10_summary.txt` — early design-phase decision
  buckets (historical context)

### 01_module_instructions/ (32 files)
Build instructions for each module (in build order):
- `module_11` through `module_41` — individual module specs
- `modules_38_39_40_final.txt` — combined Modules 38/39/40 spec
- `kickoff_module_38.txt`, `kickoff_module_39.txt` — build
  kickoff wrappers
- `consolidated_module_41_with_context.txt` — Module 41 with
  post-Phase-G context bundled

### 02_phase_instructions/ (7 files)
Multi-phase orchestration instructions:
- `phase_b_eod9_visibility.txt` — Phase B addendum
- `phase_g_medium_fixes.txt` — Phase G triage + fixes
- `phase_h_chaos_tests.txt` — Phase H deferred (post-paper)
- `phase_i_final_verdict.txt` — Phase I final verdict doc
- `consolidated_pre_phase_c.txt` — bundled Pre-C
  (scope ack + raw audit + EOD9 visibility)
- `scope_consolidation_ack.txt` — files subsumption clarifier
- `v2_foundation_wrapup.txt` — "v2 foundation complete"
  wrap-up checklist

### 03_audit_responses/ (7 files)
Handling of 3 external audits (54 total issues):
- `full_audit_raw_dump.txt` — verbatim text of all 3 audits
- `pre_live_audit_response.txt` — response to Audit 1
- `second_audit_response.txt` — response to Audit 2 (30 issues)
- `response_54_audit_accounts_nse.txt` — response to 54-point
  summary + accounts.csv + NSE files
- `mega_memory_and_fix_plan.txt` — consolidated fix plan
  (Phases A-I)
- `address_four_concerns.txt` — Rama's 4 concerns response
- `startup_flow_redesign_discussion.txt` — interactive startup
  redesign discussion doc

### 04_post_build_ops/ (5 files)
Post-build operator tasks and pending items:
- `task1_audit_closeout_mempalace.txt` — Task 1: mempalace
  audit closeout write
- `task2_accounts_setup.txt` — Task 2: accounts.csv setup
- `task2b_telegram_whitelist.txt` — Task 2b: Telegram
  whitelist enforcement
- `task3_nse_reference_data.txt` — Task 3: NSE refs
  integration
- `pending_items_snapshot.txt` — snapshot of everything
  remaining (paper trial prep + v2.1 deferred)

### 05_deployment/ (8 files) — NEW as of 18-Apr-2026
Infrastructure deployment phase (VM + systemd + firewall):
- `00_deployment_overview.md` — folder overview + status
- `step2_cleanup_env_files.txt` — env file reconciliation
  (prerequisite for Step 2)
- `step2_vm_venv_and_first_run.txt` — VM venv setup, first
  manual run, holiday guard validation
- `step3_systemd_and_firewall.txt` — systemd units, firewall
  rules, cron, status.sh helper
- `step4a_systemd_restart_test.txt` — 4 restart scenarios
  proven, revert verified
- `fixes/fix1_weekend_test_bug.txt` — 22 test failures on
  weekends, fixed before Step 2
- `fixes/fix2_scan_webhook_schema.txt` — ScanWebhookMapConfig
  schema mismatch, fixed before Step 3
- `fixes/fix3_holidays_schema_audit.txt` — nse_holidays_2026.
  yaml schema drift + full config audit, fixed before Step 3
- `fixes/fix4_requirements_split.txt` — missing pytest,
  pytest-cov, python-dotenv in requirements.txt, fixed
  post Step 4a

**Status:** COMPLETE. 1413/1413 tests green on both PC and VM.

### 06_live_operations/ (4 files) — NEW as of 18-Apr-2026
Daily ops playbooks + paper -> live graduation path:
- `00_operations_overview.md` — folder overview + graduation
  timeline + daily rhythm
- `monday_paper_playbook.docx` — Monday 20-Apr-2026 first
  paper session playbook (gates, mindset, emergency)
- `monday_commands_runbook.docx` — 34-step copy-paste terminal
  runbook (PC <-> VM)
- `paper_to_live_transition_plan.docx` — 3-week paper -> live
  graduation plan with chaos drills, capital graduation
  (Rs 25K -> Rs 1L -> full)

**Status:** IN PROGRESS. Week 1 (Full Diet Paper) begins
Monday 20-Apr-2026.

## How to use this archive

### For a new Claude session needing context:
1. Start with `00_source_documents/v2_design_spec.md`
2. Then `00_source_documents/locked_decisions.yaml`
3. Then relevant specific file from 01-06

### For debugging "what was decided":
- Check `locked_decisions.yaml` first
- If not there, search 01-06 by filename

### For rebuilding a module from scratch:
- Find `module_XX_*.txt` in 01
- Cross-reference with `locked_decisions.yaml`

### For re-running a specific audit fix:
- `03_audit_responses/full_audit_raw_dump.txt` has raw text
- `03_audit_responses/mega_memory_and_fix_plan.txt` has the
  triage table + fix plan

### For deployment re-runs or VM recovery:
- Start with `05_deployment/00_deployment_overview.md`
- Follow `step2` -> `step3` -> `step4a` in order
- Check `05_deployment/fixes/` for bugs encountered during
  original deployment

### For daily operations / first paper session:
- Start with `06_live_operations/00_operations_overview.md`
- Open `monday_paper_playbook.docx` the night before
- Keep `monday_commands_runbook.docx` open during session

## Key facts about this build

- Build: Complete (v2 foundation)
- Test count: 1413/1413 green (both PC and VM)
- Schema version: 9
- Audit items: 54 addressed (32 fixed, 22 verified/closed/
  deferred)
- Architecture: Event-bus + state-store, single-process,
  multi-threaded, SQLite WAL
- Target market: NSE equity (intraday), via Zerodha broker
- Entry signals: Chartink webhooks
- Deployment: VM (Oracle Ampere 2 OCPU / 8GB RAM / Ubuntu
  22.04) + PC (Win 11 / 32GB / NVMe)
- Deployment status: Complete (18-Apr-2026)
- First paper session: Monday 20-Apr-2026
- Target live date: Monday 11-May-2026 (3 weeks paper +
  Week 1 micro-capital Rs 25K)

## Graduation path

```
Build (complete, 01-04)
  |
  v
Deployment (complete, 05)
  |
  v
Paper Week 1: Full Diet       (20-24 Apr)       -> Gate 1
  |
  v
Paper Week 2: Chaos Diet      (27 Apr - 02 May) -> Gate 2
  |
  v
Paper Week 3: Code Freeze     (04-08 May)       -> Gate 3
  |
  v
Live Week 1: Rs 25K Micro     (11-15 May)       -> Gate 4
  |
  v
Live Week 2: Rs 1L Small      (18-22 May)       -> Gate 5
  |
  v
Live Week 3+: Full Ramp
```

## Archive maintenance

- When new Web Claude instructions arrive: add to appropriate
  category (01-06), update this README
- When a module is renamed/restructured: keep old instruction
  for historical trace; new file supersedes
- Do NOT delete files from this archive — they're the
  decision trail
- DO version major changes: add `_v2` suffix if redoing
  an older file
- Paper session debriefs should go in a new
  `docs/paper_sessions/` folder (not this archive) — those
  are daily operational notes, not instruction archive

## Git note

This folder IS committed to git (unlike .env, logs, data_store).
It's project documentation, not secrets.

## Last updated

18-Apr-2026 — added `05_deployment/` and `06_live_operations/`
folders. All test counts verified. Monday 20-Apr paper
session playbooks finalised.
