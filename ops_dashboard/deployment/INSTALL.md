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

## 2. Production config — the LOCAL OVERLAY (checkout-f-safe, **PC-created pre-GO**)
The committed `backend/config/gui_config.yaml` ships PC-dev values and EMPTY
auth. Production values + secrets live in **`backend/config/gui_config.local.yaml`**
(git-ignored — `ops_dashboard/.gitignore:9`; deep-merged over the base at app
start) so future pushes can never clobber them.

**The overlay is created ON THE PC before the deploy** (03-Jul decision): the
file holds only machine-independent strings (salted password hash + TOTP
secret + path/flag values), so PC-create + PC→VM transfer ≡ VM-create. The
copy-protection gate is VM→PC only; PC→VM scp is unrestricted.

### 2a. PC, pre-GO (Rama at the keyboard, ~5 min)
```powershell
cd D:\Projects\trading-system\ops_dashboard
# 1) credentials — Rama TYPES the password at the prompt (never echoed);
#    the command prints the TOTP secret + otpauth:// URI: scan into the
#    phone authenticator IMMEDIATELY; never paste either into chats/commits.
.venv\Scripts\python -m backend.auth --setup --username <user> --config backend\config\gui_config.local.yaml

# 2) local login smoke (overlay is auth-only at this point → base PC-dev
#    paths + secure=false apply → deterministic browser check):
.venv\Scripts\python -m backend.app
#    browser http://127.0.0.1:8500 → login user+password+6-digit TOTP →
#    dashboard shell renders → Ctrl+C. (A working login IS the TOTP
#    confirmation.)

# 3) append the production block below to backend\config\gui_config.local.yaml
#    (editor paste — the block contains no secrets; ordering vs --setup does
#    not matter: _write_auth_block merges and never clobbers other keys):
```
```yaml
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
```
Windows note: NTFS has no chmod 600 — PC-side protection = the file stays
inside the project tree, is git-ignored, and its contents are never pasted
anywhere. The VM copy gets the real `chmod 600` in 2b.

### 2b. VM, deploy night (after the GUI push lands — replaces the old
interactive step 3; Rama's live involvement is now only the §5 Tailscale
login confirmation)
```bash
scp D:/Projects/trading-system/ops_dashboard/backend/config/gui_config.local.yaml \
    trading-vm:/home/ubuntu/systems/trading-system/ops_dashboard/backend/config/
ssh trading-vm 'cd /home/ubuntu/systems/trading-system/ops_dashboard && \
  chmod 600 backend/config/gui_config.local.yaml && \
  grep -cE "^(paths:|server:|reports_download_enabled: false)" backend/config/gui_config.local.yaml && \
  grep -cE "(session_cookie_secure: true|main_db:|username:|password_hash:|totp_secret:)" backend/config/gui_config.local.yaml'
# expect 3 + 5 — key NAMES only; never cat the file into a terminal you share.
```

## 3. Auth — rotation / recovery (VM, over SSH; the ONLY reasons to re-run)
Credentials were created in 2a. Re-run setup **on the VM** only to rotate:
```bash
cd /home/ubuntu/systems/trading-system/ops_dashboard
venv/bin/python -m backend.auth --setup --username <user> \
  --config backend/config/gui_config.local.yaml
chmod 600 backend/config/gui_config.local.yaml
```
- **Suspected leak** (overlay contents ever exposed): re-run → NEW hash + NEW
  TOTP secret; the old pair is dead immediately.
- **Lost phone**: same re-run via SSH — scan the fresh QR into the new phone.
- SECURITY (permanent): never commit the overlay (git-ignored by design);
  never paste its contents — `password_hash`, `totp_secret`, the otpauth URI —
  into chats, reports, logs or commit messages; treat the printed TOTP secret
  as burned the moment it appears on any shared screen.

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
.venv/Scripts/python -m backend.auth --setup --username <you>   # interactive password prompt
.venv/Scripts/python -m backend.app        # http://127.0.0.1:8500
```
Tests: `.venv/Scripts/python -m pytest tests -q` (v41 + v42 fixtures).
