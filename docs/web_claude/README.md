# docs/web_claude/ — Web Claude Instruction Archive

This folder contains the complete instruction trail from Web
Claude (claude.ai chat sessions) during the v2 build.

Web Claude is stateless between chats. VS Code Claude has
mempalace. This archive is the permanent record of
architectural decisions and build instructions that any
future Claude (or engineer) can read.

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

## How to use this archive

### For a new Claude session needing context:
1. Start with `00_source_documents/v2_design_spec.md`
2. Then `00_source_documents/locked_decisions.yaml`
3. Then relevant specific file from 01-04

### For debugging "what was decided":
- Check `locked_decisions.yaml` first
- If not there, search 01-04 by filename

### For rebuilding a module from scratch:
- Find `module_XX_*.txt` in 01
- Cross-reference with `locked_decisions.yaml`

### For re-running a specific audit fix:
- `03_audit_responses/full_audit_raw_dump.txt` has raw text
- `03_audit_responses/mega_memory_and_fix_plan.txt` has the
  triage table + fix plan

## Key facts about this build

- Build: Complete (v2 foundation)
- Test count: 1411/1411 green
- Schema version: 9
- Audit items: 54 addressed (32 fixed, 22 verified/closed/
  deferred)
- Architecture: Event-bus + state-store, single-process,
  multi-threaded, SQLite WAL
- Target market: NSE equity (intraday), via Zerodha broker
- Entry signals: Chartink webhooks
- Next phase: Paper trial (5-7 sessions), then chaos tests
  (Phase H), then live with small capital

## Archive maintenance

- When new Web Claude instructions arrive: add to appropriate
  category (01-04), update this README
- When a module is renamed/restructured: keep old instruction
  for historical trace; new file supersedes
- Do NOT delete files from this archive — they're the
  decision trail
- DO version major changes: add `_v2` suffix if redoing
  an older file

## Git note

This folder IS committed to git (unlike .env, logs, data_store).
It's project documentation, not secrets.
