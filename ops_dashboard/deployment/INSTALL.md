# ops_dashboard — Installation

> **Placeholder — populated in G2c.**

> **G2c MUST-DO (locked by the G2a review):** access = **Tailscale** (Q1; no
> public 443). Behind Tailscale HTTPS, flip `server.session_cookie_secure: true`
> in `backend/config/gui_config.yaml` — the Secure cookie flag is config-gated
> off only for loopback-HTTP PC dev. `:8080` stays untouched/read-only (Q2;
> VM-hardening owns it). The GUI venv must never contain kiteconnect.

G2c will cover: VM deployment paths (override `gui_config.yaml` paths to the VM
DB/logs/reports/config), the isolated venv provisioning, the systemd unit for
the dashboard (loopback :8500), and the access decision (Q1 Tailscale-vs-443 /
Q2 :8080 exposure / Q3 reports download) — none of which is pre-built here.

## Local (PC) dev quickstart — G2a
```bash
cd ops_dashboard
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt   # Windows
# configure the single user (writes the auth block into backend/config/gui_config.yaml):
.venv/Scripts/python -m backend.auth --setup --username <you> --password <pw>
# run (binds 127.0.0.1:8500 only):
.venv/Scripts/python -m backend.app
```
Tests: `.venv/Scripts/python -m pytest tests -q` (runs on both v41 and v42 fixtures).
