# G2 READINESS VERDICT — GUI deploy bundle gate (03-Jul-2026, market-hours read-only audit)

**VERDICT (P1.5): GUI READY + DISPLAY-ONLY CONFIRMED — safe to bundle tonight.**

Produced on branch `gui-deploy-03jul` (GUI chain rebased onto `origin/main == 9becf8c`).
Everything below is evidence, not assertion. No production file was modified by this audit
(this doc + the G3.0 doc are the only writes, committed on the GUI branch itself).

---

## P1.1 — Tests (fresh full run)

```
240 passed in 48.71s
```

`ops_dashboard/.venv` python, `pytest tests -q`, both DB fixtures (v41 + v42 via
`tests/conftest.py` parametrization). Zero failures, zero skips. Matches the expected 240
(G2b3's 238 + 2 G2c overlay tests).

## P1.2 — Display-only proof

### (a) Route audit — 29 routes, the ONLY POST is /login (+ /logout)

| Route | Methods | File:line |
|---|---|---|
| `/login` | GET / **POST** | `backend/auth.py:149,156` |
| `/logout` | **POST**, GET | `backend/auth.py:173` |
| `/`, `/<page>` | GET | `backend/app.py:122,140` |
| `/api/dashboard` · `/api/pipeline` · `/api/capacity` | GET | `api/dashboard.py:17`, `api/pipeline.py:12`, `api/capacity.py:12` |
| `/api/strategies`, `/api/strategies/<name>` | GET | `api/strategies.py:17,24` |
| `/api/signals` · `/api/orders` · `/api/positions` · `/api/holdings` | GET | `api/trading.py:30,62,79,104` |
| `/api/risk` · `/api/capital` · `/api/exposure` · `/api/pnl` | GET | `api/risk_capital.py:25,62,90,107` |
| `/api/services` · `/api/vm` · `/api/logs` · `/api/audit` · `/api/alerts` | GET | `api/system.py:28,55,70,102,125` |
| `/api/slippage` · `/api/execution` · `/api/statistics` · `/api/reports` · `/api/reports/download` · `/api/config` | GET | `api/analytics.py:31,79,106,140,164,176` |

No PUT/PATCH/DELETE anywhere. `/api/reports/download` is GET **and** hard-403 while
`reports_download_enabled=false` (`api/analytics.py:168`).

### (b) Grep audit (backend/, tests excluded)

- SQL mutations (`INSERT INTO|UPDATE … SET|DELETE FROM|CREATE/DROP/ALTER TABLE`): **0 occurrences**.
- `subprocess`: **only** `backend/readers/host_reader.py:16,34` — the whitelisted
  `systemctl is-active <unit>` (fixed argv, no shell, 2s timeout, nosec-annotated).
- `kiteconnect` / `broker` imports: **0 occurrences** (I4/I7 re-confirmed; the I7 AST
  no-prod-imports + readonly-write-raise + venv gates are also inside the 240 green tests).

### (c) Controls surface

**No backend controls module exists at all** — `controls` appears only as a static page
mapping (`backend/app.py:137` → `controls.html` served by the generic GET `/<page>` route).
`frontend/templates/controls.html`: **0** occurrences of `fetch(`/`method=post`/`<form` —
a display-only stub page. Stronger than "stub handlers": there are no handlers.

### (d) Config locks

- `reports_download_enabled: false` — committed base `backend/config/gui_config.yaml:33`,
  enforcement 403 at `backend/api/analytics.py:168`, VM overlay keeps `false`
  (`deployment/INSTALL.md:39`, "Q3 locked").
- `session_cookie_secure: true` in the VM overlay plan (`deployment/INSTALL.md:38` — G2a
  lock; TLS terminates at tailscaled).

## P1.3 — Diff gate (name-only vs merge-base `9becf8c`)

```
 1  PATHS.md
 1  docs/SYSTEM_MAP.md
 1  docs/gui_project/**
73  ops_dashboard/**
total: 76 files   (4 sanctioned buckets, nothing else)
```

Note: committing this verdict + the G3.0 investigation doc (both under `ops_dashboard/docs/`,
a sanctioned bucket) plus the SYSTEM_MAP/PATHS pointer lines (already-sanctioned files) moves
the count 76 → **78 files — buckets unchanged at 4**. Tonight's E1 final gate should expect
78/4.

## P1.4 — credentials.xlsx .gitignore rule

**Already satisfied — no new commit needed.** Commit `aa02f9f` ("fix(c1): remove hardcoded
api_key from 3 scripts + scan .xlsx + purge runbook"), an ancestor of tonight's C-1 tip
`678c592`, adds to `.gitignore`:

```
# Rotated-credential spreadsheet — plaintext seeds, must NEVER be committable
# (the C-1 secret scanner does not scan .xlsx; gitignore + deletion are the controls)
credentials.xlsx
```

It rides tonight's window inside the security batch (which pushes BEFORE the GUI merge).
Verified: `credentials.xlsx` is not in the repo root and has never been tracked
(`git ls-files` empty). The pending-sheet item is closed by the batch itself.

## P1.5 — VERDICT

**GUI READY + DISPLAY-ONLY CONFIRMED — safe to bundle tonight.**
