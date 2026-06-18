# DEPLOYMENT.md -- Trading System v2

Operational runbook for deploying the repo onto the Oracle Cloud VM
(`ubuntu@161.118.187.249`). This file is the single point of truth for the
post-F.1 deploy story.

Historical step-by-step bootstrap (VM provisioning, venv, firewall, first
paper run) lives under `docs/web_claude/05_deployment/`:

- `step2_vm_venv_and_first_run.txt`
- `step3_systemd_and_firewall.txt`
- `step4a_systemd_restart_test.txt`

This document covers what changed in **F.1**, plus the pre-trial deploy
steps for Monday 2026-04-21.

---

## 1. What F.1 committed

| Path                                  | Purpose                                              |
|---------------------------------------|------------------------------------------------------|
| `deploy/systemd/trading-system.service` | Main service unit (canonicalized from step3 doc)   |
| `deploy/systemd/alert-watcher.service`  | Sentinel-alert watcher unit                        |
| `deploy/logrotate/trading-system`       | Retention-only log cleanup (30 days)               |
| `deploy/cron/trading-system.cron`       | Nightly DB backup + weekly instrument refresh      |
| `scripts/copy_token_to_vm.bat`          | PC helper to SCP Zerodha token to VM (moved)       |
| `config/accounts.csv` edit              | `LFL836.paper_capital`: 5_000_000 → 50_000 (H-26)  |
| `config/broker_limits.yaml` edit        | `quote: burst/rate_per_sec`: 1 → 3 (H-24)          |
| `reports/daily_review.py` edits         | `--db` default fixed; `--unattended` flag; exit 2  |
| `utils/startup_checks.py` edit          | `check_paper_capital_consistency` (EF-7 guard)     |
| `main.py` edit                          | Call EF-7 guard after `fund_manager.initialize`    |

F.1 did **not** touch `/etc/systemd/system/` on the VM. The deploy step below
copies the committed unit files onto the VM and reloads systemd.

---

## 2. Pre-deploy diff check (MANDATORY)

Before copying any file from `deploy/` onto the VM, diff the committed
copy against what is actually running. Catches drift you did not know
about.

```bash
# from PC, comparing repo vs VM:
ssh trading-vm 'sudo cat /etc/systemd/system/trading-system.service' \
  | diff - deploy/systemd/trading-system.service

ssh trading-vm 'sudo cat /etc/systemd/system/alert-watcher.service' \
  | diff - deploy/systemd/alert-watcher.service

# for cron (ubuntu user):
ssh trading-vm 'crontab -l' | diff - deploy/cron/trading-system.cron

# for logrotate (may be absent pre-F.1):
ssh trading-vm 'sudo cat /etc/logrotate.d/trading-system 2>/dev/null' \
  | diff - deploy/logrotate/trading-system
```

If any diff is non-empty and unexpected: STOP. Investigate. Do not copy
over.

---

## 3. Deploy sequence (operator, post-diff)

Run in order. Each step is idempotent.

### 3.1 systemd units

```bash
scp -i ~/.ssh/trading_vm_secure \
    deploy/systemd/trading-system.service \
    deploy/systemd/alert-watcher.service \
    ubuntu@161.118.187.249:/tmp/

ssh trading-vm << 'EOF'
sudo mv /tmp/trading-system.service  /etc/systemd/system/
sudo mv /tmp/alert-watcher.service   /etc/systemd/system/
sudo chown root:root /etc/systemd/system/trading-system.service \
                     /etc/systemd/system/alert-watcher.service
sudo chmod 644       /etc/systemd/system/trading-system.service \
                     /etc/systemd/system/alert-watcher.service
sudo systemctl daemon-reload
sudo systemctl status trading-system.service --no-pager
sudo systemctl status alert-watcher.service  --no-pager
EOF
```

Units remain **disabled** and **inactive** at this point. Enable only
after Gate D of Monday's paper trial passes.

### 3.2 logrotate

```bash
scp -i ~/.ssh/trading_vm_secure \
    deploy/logrotate/trading-system \
    ubuntu@161.118.187.249:/tmp/

ssh trading-vm << 'EOF'
sudo mv /tmp/trading-system /etc/logrotate.d/trading-system
sudo chown root:root /etc/logrotate.d/trading-system
sudo chmod 644       /etc/logrotate.d/trading-system
# dry-run to verify:
sudo logrotate -d /etc/logrotate.d/trading-system
EOF
```

### 3.2b .env (secrets) — required before first run

I.5 (2026-04-25): the systemd units assume a `.env` file at
`/home/ubuntu/trading-system/.env` containing the secrets
(`ZERODHA_API_KEY_*`, `ZERODHA_API_SECRET_*`, `TELEGRAM_BOT_TOKEN`,
`WEBHOOK_SECRET`, `ALERT_SMTP_PASSWORD`, etc). Without it the service
will start but every broker/notify call fails with a cryptic
`KeyError` at first contact.

**Pull from your local repo (do not commit secrets):**

```bash
# Create on your laptop first if you don't have one already (template
# in repo: .env.example -- copy and fill in real values, then ship).
scp -i ~/.ssh/trading_vm_secure \
    .env \
    ubuntu@161.118.187.249:/home/ubuntu/trading-system/.env

ssh trading-vm 'chmod 600 /home/ubuntu/trading-system/.env'
```

Verify systemd can read it (the unit file must reference it via
`EnvironmentFile=`):

```bash
ssh trading-vm 'sudo systemctl cat trading-system.service | grep -i environment'
# Expect: EnvironmentFile=/home/ubuntu/trading-system/.env
```

If the systemd unit lacks the `EnvironmentFile=` line, edit
`deploy/systemd/trading-system.service`, redo the diff in §2, and
re-deploy via §3.1.

### 3.3 cron (ubuntu user)

```bash
scp -i ~/.ssh/trading_vm_secure \
    deploy/cron/trading-system.cron \
    ubuntu@161.118.187.249:/tmp/

ssh trading-vm << 'EOF'
# install: replaces the entire ubuntu crontab with the committed file.
# Verify no other lines are lost first:
crontab -l > /tmp/crontab.before
cp /tmp/trading-system.cron /tmp/crontab.after
diff /tmp/crontab.before /tmp/crontab.after || true

# If diff is acceptable, install:
crontab /tmp/trading-system.cron
crontab -l
systemctl status cron --no-pager | head -5

# Ensure backup directory exists:
mkdir -p /home/ubuntu/trading-system/data_store/backups
EOF
```

---

## 4. Day-1 paper trial checklist (Monday 2026-04-21)

Morning (operator at PC):

1. Rotate Zerodha credentials (Kite dev console) + SCP fresh `.env`:
   ```bash
   scp -i ~/.ssh/trading_vm_secure .env ubuntu@161.118.187.249:~/systems/trading-system/.env
   ```
2. Refresh instruments (run on PC, not VM, so the PC CSV stays in sync):
   ```bash
   python scripts/refresh_instruments.py --account LFL836
   ```
3. Zerodha login:
   ```bash
   python scripts/zerodha_login.py --account LFL836
   ```
4. Push token to VM:
   ```bash
   scripts/copy_token_to_vm.bat
   ```
5. Start the service on VM (manual for Day 1):
   ```bash
   ssh trading-vm 'sudo systemctl start trading-system.service'
   ssh trading-vm 'journalctl -u trading-system -f'
   ```

During market hours (09:15 - 15:30 IST): unattended. Watch journal.

Post-market:
```bash
ssh trading-vm 'cd trading-system && venv/bin/python -m reports.daily_review --unattended'
scp -i ~/.ssh/trading_vm_secure \
    ubuntu@161.118.187.249:~/systems/trading-system/reports/daily/*.xlsx \
    reports/daily/
```

Gates A/B/C/D live in `docs/web_claude/06_live_operations/monday_paper_playbook.docx`.

---

## 5. Rollback

If F.1 deploy introduces a regression, revert is straightforward:

| Change           | Rollback                                             |
|------------------|------------------------------------------------------|
| systemd unit     | `git checkout HEAD~1 -- deploy/systemd/*`; redeploy  |
| logrotate        | `sudo rm /etc/logrotate.d/trading-system`            |
| cron             | `crontab /tmp/crontab.before` (saved in 3.3)         |
| accounts.csv     | `git checkout HEAD~1 -- config/accounts.csv`         |
| broker_limits    | `git checkout HEAD~1 -- config/broker_limits.yaml`   |

Python-side changes (daily_review, startup_checks, main.py) revert via
standard `git revert <F.1-commit-hash>` on PC then `git push vm main`.

---

## 6. Deferred (FUTURE-1)

Auto-start `trading-system.service` on token arrival is **NOT** implemented
in F.1. Current operational flow is:

```
PC: zerodha_login.py -> SCP via .bat -> ssh + manual systemctl start
```

Three implementation variants surfaced in F.1 pre-work (`.path` unit with
idempotency guard, `.timer` unit with fixed 09:00 IST kickoff, or keep
manual). The choice should be informed by actual operational patterns
observed during Week 1 of paper trial. Tracked as FUTURE-1 in
`docs/web_claude/03_audit_responses/extra_findings.md`.

---

## 7. NTP — slew mode required (Phase D / Audit 6.4)

`core/time_authority.py` issues every wall-clock read via `now_ist()` and
`candle_store.py` uses those timestamps for candle `close_ts`. If the
system clock takes a discontinuous **step** mid-session, candle ordering
and gate timeouts can briefly invert. The cheap mitigation is to require
NTP to operate in **slew mode** (gradual frequency adjustment) rather
than step mode.

### VM (Oracle Cloud, systemd-timesyncd)

`systemd-timesyncd` slews drift < 1s by default. Verify it is the active
time source on the VM:

```bash
ssh trading-vm 'timedatectl status'
# Expect:
#   System clock synchronized: yes
#   NTP service: active
```

If `chronyd` or `ntpd` was installed instead, ensure step mode is
disabled. For chronyd, in `/etc/chrony/chrony.conf`:

```
# Allow slewing for offsets up to 1s; never step at runtime.
makestep 1.0 -1
```

`-1` as the second argument disables stepping after the initial sync.

### PC (Windows, paper trial)

Windows Time service (`w32time`) defaults to slew within 128ms and step
beyond. Paper trial runs on the PC, so confirm `w32time` is healthy
before each session:

```cmd
w32tm /query /status
w32tm /query /source
```

If a step is unavoidable (e.g. laptop sleep/wake spans a large drift),
restart the trading-system process before the next entry-window opens.

### Why this is INFO and not a code change

Per the audit closure (`docs/web_claude/03_audit_responses/deep_system_audit_2026-04-24_closure.md`
Section 6.4): on a tuned server the step risk is rare and bounded.
Adding a `time.monotonic()` fallback inside `time_authority` would force
a refactor across every consumer of `now_ist()` for marginal benefit.
The deployment-side mitigation is sufficient.

---

## 8. Nginx Reverse Proxy Recommendation (DOCUMENT-001)

The webhook receiver listens on port 5000. **Do not expose this port directly
to the internet.** Instead, place Nginx (or another reverse proxy) in front to:

1. **Terminate SSL/TLS on port 443** — Chartink webhooks and browser access
   require HTTPS. Let Nginx handle certificates (via Let's Encrypt/Certbot)
   rather than embedding SSL into the Python application.
2. **Proxy to localhost:5000** — The trading-system process binds to
   `127.0.0.1:5000` (localhost only). Nginx forwards requests from the
   public-facing HTTPS listener to the local application.
3. **Add request validation** — Nginx can enforce rate limits, header checks,
   or IP allowlists before traffic reaches the application.

### Minimal Nginx configuration

```nginx
# /etc/nginx/sites-available/trading-system
# (symlink to sites-enabled/)

server {
    listen 443 ssl http2;
    server_name trading.yourdomain.com;  # Replace with your domain

    # SSL certificate (obtain via certbot)
    ssl_certificate     /etc/letsencrypt/live/trading.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/trading.yourdomain.com/privkey.pem;

    # Modern SSL config
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256';
    ssl_prefer_server_ciphers off;

    # Proxy to localhost:5000
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Optional: rate limiting (10 requests/second)
        # Protect against webhook floods or accidental loops
        limit_req zone=webhook_limit burst=20 nodelay;
    }
}

# Rate limit zone definition (add to http block in nginx.conf)
# limit_req_zone $binary_remote_addr zone=webhook_limit:10m rate=10r/s;

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name trading.yourdomain.com;
    return 301 https://$server_name$request_uri;
}
```

### Deployment steps

1. **Install Nginx** (if not already present):
   ```bash
   sudo apt update
   sudo apt install nginx certbot python3-certbot-nginx
   ```

2. **Obtain SSL certificate**:
   ```bash
   sudo certbot --nginx -d trading.yourdomain.com
   ```
   Certbot auto-configures the `ssl_certificate` paths and adds a renewal cron.

3. **Deploy the config above** to `/etc/nginx/sites-available/trading-system`,
   symlink to `sites-enabled/`, and reload:
   ```bash
   sudo ln -s /etc/nginx/sites-available/trading-system \
               /etc/nginx/sites-enabled/
   sudo nginx -t  # validate syntax
   sudo systemctl reload nginx
   ```

4. **Update firewall** to allow 443, deny 5000:
   ```bash
   sudo ufw allow 443/tcp
   sudo ufw deny 5000/tcp  # block direct access to webhook port
   sudo ufw status
   ```

5. **Update Chartink webhook URL** from `http://IP:5000/webhook` to
   `https://trading.yourdomain.com/webhook`.

### Why this matters

- **Security**: Exposing port 5000 directly means any HTTP client can hit the
  webhook endpoint without encryption. Nginx enforces HTTPS and can validate
  headers/IP before passing traffic to the app.
- **Certificate management**: Certbot auto-renews Let's Encrypt certs. Python
  application code stays certificate-agnostic.
- **Operational visibility**: Nginx logs (`/var/log/nginx/access.log`) separate
  external HTTP traffic from application logs, making incident triage easier.

### Current state (as of F.1)

The VM firewall currently allows port 5000 for direct testing. This is
acceptable for early paper trials but **must** be locked down before
production use. Add the Nginx layer before Week 2 of live trading.
