# 06_live_operations — Daily Ops & Graduation Path

**Purpose:** Operational playbooks for running the system day-to-day, starting from first paper session through to full live trading.

**Status:** 🟡 IN PROGRESS. Week 1 (Full Diet Paper) begins Monday 20-Apr-2026.

---

## Documents

| File | When to Use | Audience |
|------|-------------|----------|
| `monday_paper_playbook.docx` | Monday 20-Apr-2026 — first paper session | Operator (Rama) |
| `monday_commands_runbook.docx` | Monday 20-Apr-2026 — copy-paste terminal commands | Operator (Rama) |
| `paper_to_live_transition_plan.docx` | Full 3-week paper → live graduation plan | Operator + Web Claude + VS Code Claude |

---

## Graduation Timeline

```
Week 1  (20-24 Apr)  — PAPER Full Diet       → Gate 1
Week 2  (27 Apr - 02 May)  — PAPER Chaos Diet     → Gate 2
Week 3  (04-08 May)  — PAPER Final Rehearsal (CODE FREEZE) → Gate 3
Live W1 (11-15 May)  — LIVE ₹25K Micro        → Gate 4
Live W2 (18-22 May)  — LIVE ₹1L Small         → Gate 5
Live W3+            — LIVE Full Ramp
```

Each gate has explicit pass/fail criteria in `paper_to_live_transition_plan.docx`.

---

## Daily Rhythm (once in paper mode)

1. **08:30** — Token refresh on PC → SCP to VM
2. **09:00** — systemd auto-starts main.py on VM
3. **09:15 – 15:30** — Passive monitor: Telegram + occasional health check
4. **15:45** — Download artifacts PC ← VM
5. **16:00** — Write daily debrief → `docs/paper_sessions/weekN_dayX.md`

---

## Emergency Contacts (in doc)

Every document has a dedicated emergency section:
- **Soft kill** — stop new signals, keep open positions safe
- **Hard kill** — cancel everything
- **Token expiry** — mid-day regeneration flow
- **VM unresponsive** — Kite app first, troubleshoot second

---

## Post-Paper Backlog

Items deferred to post-live (not blockers for graduation):
- SQLite migration for session_state (from JSON)
- Positional bucket config fix
- broker_costs.yaml explicit definition
- Daily review report wiring (smoke_test_review.xlsx format)
- Local AI assistant integration (llama3.1, deepseek-r1)

See Section 6 of `paper_to_live_transition_plan.docx` for full list.

---

*Folder last updated: 18-Apr-2026*
