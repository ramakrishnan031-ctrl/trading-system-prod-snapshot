# Monitoring Prevention Checklist — every new cron/service (engineering rule)

**Adopted 15-Jul-2026** after the audit found the monitor reporting green while every email
and sentinel alert was silently undelivered (a dead Gmail credential). Two failure CLASSES
must be impossible to survive unseen:

- **CLASS 1 — "green heartbeat, dead work":** a job records SUCCESS ("the script ran")
  though its real function/delivery failed. → *Every job carries an EXECUTION status AND a
  FUNCTIONAL status (F2). Never overload one signal for both.*
- **CLASS 2 — "one dead channel blinds the operator":** a single delivery channel dies and
  all alerts vanish with no fallback. → *Alert delivery has a fallback; a dead channel is a
  visible, machine-readable DEGRADED state, not silence (F1).*

## The seven items — NO new cron/service ships to production without ALL of them

| # | Item | What it means | Where it lives |
|---|---|---|---|
| 1 | **Functional success criterion** | A check of the real OUTPUT (artifact generated / delivered / validated), distinct from "the script exited 0". | `record_heartbeat(..., functional_status=)` / `HeartbeatTimer.functional_status` (encoded in the heartbeat `message` as `[func=...]`). |
| 2 | **Health-check** | The job records its own heartbeat (execution + functional) so the Cron Officer sees it ran and whether it truly worked. | `HeartbeatTimer` / `record_heartbeat`. |
| 3 | **Alert path** | A failure reaches the operator on a live channel. | Telegram (direct) and/or a critical sentinel. |
| 4 | **Fallback path** | If the primary channel is down, delivery still reaches the operator (or a visible DEGRADED marker is published). | alert_watcher Telegram fallback + `alert_watcher_degraded.json` (F1). |
| 5 | **Regression test** | A `tests/unit/test_*.py` that fails on the pre-fix behaviour and passes on the new. | `tests/unit/`. |
| 6 | **Owner** | A named human responsible for the job. | The registry comment / this doc. |
| 7 | **Monitoring classification** | Detection method (`heartbeat_db` vs `exit_code_file`) + criticality declared in the registry, so `check_cron_drift` + the Cron Officer verify it by the RIGHT signal. | `config/cron_registry.yaml` (`monitored`, `critical`, `detection_method`, `marker_name`). |

## Supporting mechanisms (built 15-Jul)

- **F1** — `scripts/alert_watcher.py`: never crash-loops on a delivery fault; Telegram
  fallback for sentinels; time-based backoff; `alert_watcher_degraded.json` degraded marker.
- **F2** — `utils/cron_heartbeat.py`: `functional_status` (encoded in `message`), surfaced by
  the Cron Officer (`build_eod_summary`) — including the F1 email-degraded state.
- **F3** — `scripts/check_cron_drift.py`: detection-method-aware (markers for exit_code_file).
- **Canary** — `scripts/monitoring_canary.py`: daily self-test of the four alert paths
  (email / telegram / sentinel / dashboard); quiet when healthy, loud (Telegram → sentinel)
  when broken. Itself satisfies all seven items above.
- **Deploy gate** — `scripts/deploy_assert.py`: blocks a deploy ONLY on a BLOCKER-class
  failure (DB integrity / schema parity / trading correctness); monitoring defects WARN,
  never block.

**Owner of the monitoring subsystem: Rama.** Referenced from `docs/SYSTEM_MAP.md`.

## Interface Change Checklist — for ANY function-signature change (engineering pattern)

**Adopted 15-Jul-2026.** Motivating incident: Branch-B/F2 inserted a new `functional_status`
parameter into `record_heartbeat` (before `db_path`). Production was updated correctly, but a
pre-existing hand-written test mock froze the OLD parameter list, so a `TypeError` stayed
hidden until the full combined regression three merges later. Signature changes are cheap to
make and easy to under-propagate; this checklist makes the propagation explicit.

When you change ANY function's signature, do ALL of the following in the SAME change:

- [ ] **Production callers** — update every call site to the new signature.
- [ ] **Wrappers** — update anything that forwards args (e.g. `HeartbeatTimer.__exit__`).
- [ ] **Mocks** — update every hand-written stub / monkeypatch of the function.
- [ ] **Autospec** — prefer `create_autospec(fn)` / `patch(..., autospec=True)` for those mocks
      so they track the real signature automatically and cannot silently drift again.
- [ ] **Contract test** — update the signature-lock (e.g. `tests/unit/test_cron_heartbeat_contract.py`)
      so the pinned parameter set matches the new signature.
- [ ] **Discovery guard** — run it; it fails on any narrow stub that was missed.

`record_heartbeat` is the reference implementation of this pattern: the signature-lock +
discovery-guard tests live in `tests/unit/test_cron_heartbeat_contract.py`.

**Scope lock (intentional):** that discovery guard is deliberately limited to `record_heartbeat`
patch sites and is a simple line-pattern scan of the test tree — NOT an AST parse, NOT a generic
repository-wide "every mock must be autospec" scanner. Generalizing it is a separate, deliberate
decision for a future cycle, not scope creep in this one.
