# 02-Jul Audit — Remediation Status + P1/P2 Dependency Map (as of 02-Jul ~21:20 IST)

**Source:** `docs/audit/system_security_audit_02jul2026.md` (1 CRITICAL + 4 HIGH, lead-verified).
**Deployed baseline:** `origin/main = 9becf8c` (the security batch — C-1 scrub, C-2 phase-2, A-1/E-1 — is LIVE; the audit's own baseline was the earlier `59f83b5`).
**Type:** read-only reconciliation. No code changed.

---

## 1. Status table — the 1 CRITICAL + 4 HIGH

| # | Sev | Status | Evidence | P1 / P2 / outside |
|---|-----|--------|----------|-------------------|
| **C-1** creds in `.env.example` | CRITICAL | **Live exposure CLOSED; residuals PARTIAL; purge OPEN** | Scrub `7bc3367` **deployed in `9becf8c`** (`.env.example` = `FILL_*` placeholders at HEAD); all live secrets **rotated 02-Jul**; script api_key fix + `.xlsx` scanner + purge **runbook** committed `aa02f9f` (`fix-c1-completion-02jul`, **unpushed**); history-purge = runbook ready, **not executed** | **OUTSIDE P1** |
| **A-1** timeout → naked position | HIGH | **CLOSED (deployed)** | Tag-correlation recovery `_recover_in_flight_entries` **deployed in `9becf8c`**; live at HEAD | **OUTSIDE P1** (fixed; P1 reuses the same reconcile infra but A-1 itself is done) |
| **A-2** timeout-retry → double entry | HIGH | **PARTIAL (built, unpushed)** | Dedicated `except BrokerTimeoutError` in all 3 placement paths, committed `fd09a38` (`fix-a2-timeout-no-retry-02jul`); 6 tests + 408 regression green; **deploy AFTER the 03-Jul 08:15 boot** | **OUTSIDE P1** |
| **B-1** daily-loss `unrealized_mtm` dead code | HIGH | **OPEN (untouched)** | `risk_engine.py:497-510` sums `get_total_unrealized_mtm()` into the gate, but the writers `update_unrealized_mtm`/`remove_unrealized_mtm` (`fund_manager.py:1404/1416`) have **zero production callers** (only `test_fund_manager.py`) → dict always empty → gate is realized-only | **OUTSIDE P1 & P2** |
| **C-2** webhook `0.0.0.0` + no HMAC | HIGH | **PARTIAL** | Phase-2 (WEBHOOK_SECRET required BOTH modes + per-IP rate limit) committed `d922007`, **deployed in `9becf8c`**; **but** the network exposure is unchanged — `bind_host:"0.0.0.0"` + `require_hmac:false` (`system_config.yaml:179,187`), auth still `?token=` over plaintext HTTP. Phase-3 (localhost bind / IP allowlist / reverse-proxy+TLS / HMAC) = Rama's network call, **OPEN** | **OUTSIDE P1 & P2** |

---

## 2. Explicit answers

**Which HIGHs are already CLOSED/deployed?**
- **A-1 — fully CLOSED & deployed** (`9becf8c`). (A-2 is *built* but unpushed → deployed only after tomorrow's boot; C-1's live exposure is closed by rotation + the deployed scrub, but its script/scanner residual is unpushed and the history-purge is not executed.)

**Which HIGHs are represented by P1 (or P2)?**
- **None of the four HIGHs is a P1 or P2 deliverable.** P1/P2 address *adjacent, lower-tier* audit items, not the headline HIGHs:
  - **W3** (system↔broker P&L/position reconciliation *wiring* — a work-item, not a HIGH; A-1 only *partially* overlapped it) → **P1**.
  - The P1-assessment's **`eod_verify` false-VERIFY** finding (not one of the 5 audit findings; surfaced in the P1 assessment) → **P1**.
  - **E-4** (LOW-MEDIUM — no `/health` liveness for `order_reconciler`/`eod_scheduler`/`live_feed`) → **P2**.
- So P1/P2 do **not** close any of C-1/A-1/A-2/B-1/C-2.

**Which HIGHs remain OPEN and OUTSIDE P1/P2 entirely?**
- **B-1** — fully OPEN, untouched, outside P1/P2.
- **C-2 (network layer)** — partially OPEN (auth hardened+deployed; bind/HMAC/plaintext still open), outside P1/P2.
- (A-2 is "unpushed" but built+queued; C-1's residual is unpushed+purge-pending — both are *in flight*, not un-started.)

---

## 3. Sequencing for the OPEN HIGHs outside P1/P2

**B-1 — recommend BEFORE or ALONGSIDE P1 (not blocked behind it).**
- **Risk:** the daily-loss pre-trade gate advertises `abs(realized + unrealized) ≥ 3%` but silently counts **realized only** → while multiple positions sit in unrealized drawdown, the gate won't block new entries, so concurrent open risk can overshoot the 3% daily limit before anything closes. Bounded by per-position SLs + the 15:17 MIS square-off, and the realized control still trips on close — so it's a **degraded, not absent** control. Still a genuine non-functioning advertised safety control (HIGH as rated).
- **Independence:** zero dependency on P1 (different subsystem — `risk_engine`/`fund_manager`, not EOD reconcile). The writer method already exists; the fix is to **wire a live per-position MTM source** (LTP → `update_unrealized_mtm` on the monitor/poll path) and `remove_unrealized_mtm` on close. Smaller than the P1 build.
- **Recommendation:** do B-1 as a **standalone fix before/with P1** so we are not building the larger P1 while an advertised capital control sits dead. It is the highest-value quick win in the open set.

**C-2 (network) — recommend AFTER P1.**
- **Risk:** MEDIUM — the deployed Phase-2 already forces the (rotated) WEBHOOK_SECRET in both modes + per-IP rate-limit, so signal-injection now needs the secret. Residual is network-level: `0.0.0.0` bind + no HMAC + plaintext `?token=` → sniff/replay if an attacker already has network reach to the VM (internal-only; firewall-dependent). It is a **network-architecture decision** (IP allowlist vs reverse-proxy+TLS), not a code defect.
- **Recommendation:** **after P1**; it doesn't block P1 and the worst of the auth gap is already deployed.

**Net:** the only thing that should reorder the plan is **B-1** — slot it before/with P1. Nothing higher-risk than P1's target is sitting un-started except B-1, and B-1 is a smaller fix.

---

## 4. Unpushed-branch / deploy picture (next off-market push)

`origin/main = local main = 9becf8c` (deployed). Ahead of it, unpushed:

| Branch | Tip | Contents | Deploy gate |
|---|---|---|---|
| `fix-a2-timeout-no-retry-02jul` | `fd09a38` | A-2 fix (1 commit) | after 03-Jul 08:15 boot confirms clean |
| **`fix-c1-completion-02jul`** | `aa02f9f` | **STACKED: `fd09a38` (A-2) + `aa02f9f` (C-1 scripts + `.xlsx` scanner + purge runbook + `.gitignore` credentials.xlsx)** | with/after A-2 (shares the boot gate) |
| `fix-t2-import-02jul` | `b826ae0` | T2 proof-script import fix — **parked/superseded** (T2 live-validation deferred; memory `t2_attempt_02jul`) | not queued |

- The practical push = **`fix-c1-completion-02jul`** (brings A-2 + C-1 together to `main`). If A-2 and C-1 must deploy separately, cherry-pick `aa02f9f` onto `main`; otherwise deploy the stack after the boot.
- **Uncommitted working-tree docs** (this session): the P1 assessment doc + this reconciliation doc + SYSTEM_MAP/PATHS pointers — doc-only, on `fix-c1-completion-02jul`, for a doc commit at push time.
- **Not yet on any branch (future work):** B-1 fix, P1/P2/P3 build, C-2 Phase-3 network, C-1 history purge, the optional api_key rotation.

---
**No code changed.** This map is the go/no-go input for starting P1: A-1 done, A-2+C-1 queued for the boot-gated push, C-2 auth deployed (network deferred), and **B-1 is the one open HIGH to fix before/with P1.**
