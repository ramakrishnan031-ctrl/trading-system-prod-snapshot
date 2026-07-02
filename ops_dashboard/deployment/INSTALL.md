# ops_dashboard — VM Deployment Runbook (G2c — living document)

**Access decision (locked):** Tailscale only (Q1) — no public 443, no Oracle
security-list change. `:8080` untouched, read-only consumption (Q2).
**Reports download:** `reports_download_enabled: false` stays (Q3 locked
03-Jul; V1 view-only — revisit post-soak).

## 0. Prerequisites
- Tree on the VM at `/home/ubuntu/systems/trading-system/ops_dashboard/`
  (lands via the normal `git push origin main` → bare-repo post-receive
  checkout; the hook starts nothing — the unit below is manual).
- VM measured healthy (G2c STEP 0): ≥500 MB MemAvailable, ≥5 GB root free.

## 1. Isolated venv (I4 — no kiteconnect, ever)
```bash
cd /home/ubuntu/systems/trading-system/ops_dashboard
python3 -m venv venv
venv/bin/pip install -r backend/requirements.txt
venv/bin/pip show kiteconnect && echo "FAIL I4" || echo "OK: no kiteconnect"
```

## 2. Production config — the LOCAL OVERLAY (checkout-f-safe)
The committed `backend/config/gui_config.yaml` ships PC-dev values and EMPTY
auth. Production values + secrets live in **`backend/config/gui_config.local.yaml`**
(git-ignored; deep-merged over the base at app start) so future pushes can
never clobber them.

```bash
cat > backend/config/gui_config.local.yaml <<'YAML'
paths:
  main_db:      "/home/ubuntu/systems/trading-system/data_store/trading_system.db"
  analytics_db: "/home/ubuntu/systems/trading-system/data_store/analytics.db"
  logs_dir:     "/home/ubuntu/systems/trading-system/logs"
  reports_dir:  "/home/ubuntu/systems/trading-system/reports/output"
  config_dir:   "/home/ubuntu/systems/trading-system/config"
  data_store:   "/home/ubuntu/systems/trading-system/data_store"
server:
  session_cookie_secure: true    # G2a lock — TLS terminates at tailscaled
reports_download_enabled: false  # Q3 locked
YAML
chmod 600 backend/config/gui_config.local.yaml
```

## 3. Auth setup (Rama present — types the password; QR to his phone)
```bash
venv/bin/python -m backend.auth --setup --username <user> \
  --config backend/config/gui_config.local.yaml
# (omit --password → interactive prompt; NEVER echo secrets to logs/reports)
chmod 600 backend/config/gui_config.local.yaml
```

## 4. Smoke (manual, then stop)
```bash
venv/bin/python -m backend.app &   # binds 127.0.0.1:8500 only
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" http://127.0.0.1:8500/   # → 302 /login
kill %1
```

## 5. Tailscale + HTTPS (no public listener)
```bash
curl -fsSL https://tailscale.com/install.sh | sh     # official repo installer
sudo tailscale up                                    # Rama authenticates node → HIS tailnet
# Rama (admin console, one-time): DNS → enable MagicDNS; then
#   DNS → HTTPS Certificates → Enable HTTPS.
sudo tailscale serve --bg https / http://127.0.0.1:8500
tailscale serve status                               # record https://<machine>.<tailnet>.ts.net
```
Verify: PC browser (same tailnet) → URL → login+TOTP → dashboard; phone
(Tailscale app) same; any non-tailnet network → unreachable. Once, after
18:00 IST: page loads (time-lock is copy-gate-only — empirical check).

## 6. systemd unit (manual install; survives trader restarts by design)
```bash
sudo cp deployment/gui-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gui-dashboard
systemctl status gui-dashboard --no-pager
```
Reboot drill (OFF-market only): `sudo reboot` → confirm trading stack AND
gui-dashboard return; `tailscale serve status` persists.

## 7. Rollback
```bash
sudo systemctl disable --now gui-dashboard
sudo rm /etc/systemd/system/gui-dashboard.service && sudo systemctl daemon-reload
sudo tailscale serve reset          # stops HTTPS proxying (node may stay in tailnet)
# tree + venv are inert once the unit is gone; remove venv/ if reclaiming disk
```

## PC dev quickstart (unchanged)
```bash
cd ops_dashboard && python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt
.venv/Scripts/python -m backend.auth --setup --username <you> --password <pw>
.venv/Scripts/python -m backend.app        # http://127.0.0.1:8500
```
Tests: `.venv/Scripts/python -m pytest tests -q` (v41 + v42 fixtures).
