# Deployment Guide

Complete setup guide for the trading system VM.

## VM Requirements

- Ubuntu 22.04+ (recommended)
- 2 vCPU, 2 GB RAM minimum
- 20 GB disk
- Timezone: Asia/Kolkata (IST)
- Inbound: port 5000 (webhook), port 8080 (healthcheck)
- Outbound: HTTPS (Zerodha API, Telegram, Chartink, NSE, Gemini)

## Current Architecture

- **VM:** 161.118.188.171 (user: ubuntu)
- **Repo:** /home/ubuntu/systems/trading-system/
- **Venv:** /home/ubuntu/systems/venv/
- **SSH alias:** `trading-vm` (configured in ~/.ssh/config)

## Initial Setup

### 1. System Packages
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3-pip sqlite3 git
sudo timedatectl set-timezone Asia/Kolkata
```

### 2. Python Virtual Environment
```bash
mkdir -p ~/systems
python3.11 -m venv ~/systems/venv
source ~/systems/venv/bin/activate
pip install --upgrade pip
```

### 3. Clone Repository
```bash
cd ~/systems
git clone <repo-url> trading-system
cd trading-system
pip install -r requirements.txt
```

### 4. Configuration Files
```bash
# Copy config templates
cp config/system_config.yaml config/system_config.yaml  # already in repo

# Create .env file with secrets
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=<your-token>
TELEGRAM_CHANNEL_PRIMARY=<chat-id>
WEBHOOK_SECRET=<secret>
ZERODHA_API_KEY=<key>
ZERODHA_API_SECRET=<secret>
ZERODHA_TOTP_KEY=<totp-key>
EOF
chmod 600 .env

# Create data directories
mkdir -p data_store/backups data_store/session logs reports/output
```

### 5. Initialize Database
```bash
PYTHONPATH=. python3 -c "from core.state_store import StateStore; StateStore('data_store/trading_system.db')"
```

### 6. Refresh Instrument Master
```bash
PYTHONPATH=. python3 scripts/refresh_instruments.py --account <ACCOUNT_ID>
```

## Systemd Service

### trading-system.service
```ini
# /etc/systemd/system/trading-system.service
[Unit]
Description=Trading System v2
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/systems/trading-system
EnvironmentFile=/home/ubuntu/systems/trading-system/.env
ExecStart=/home/ubuntu/systems/venv/bin/python main.py --mode paper
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Enable and Start
```bash
sudo systemctl daemon-reload
sudo systemctl enable trading-system
sudo systemctl start trading-system
sudo systemctl status trading-system
```

### Gemini Watchman Service
```ini
# /etc/systemd/system/gemini-watchman.service
[Unit]
Description=Gemini Trading Watchman
After=trading-system.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/systems/trading-system
EnvironmentFile=/home/ubuntu/systems/trading-system/.env
ExecStart=/home/ubuntu/systems/venv/bin/python scripts/gemini_watchman.py
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
```

## Cron Schedule

Install from the canonical cron file:
```bash
crontab deploy/cron/trading-system.cron
crontab -l  # verify
```

Key cron jobs:
| Time | Job |
|------|-----|
| 01:00 | SQLite backup |
| 05:00 | Token cleanup |
| 08:00 | Auto token refresh |
| 08:30 | Premarket healthcheck |
| 08:55 | Premarket Gemini briefing |
| */5 9-15 | System metrics capture |
| 15:17 | EOD squareoff (via main.py) |
| 15:40-16:20 | EOD pipeline (candles → report → journal → metrics → Gemini) |
| 16:40 | Trade coaching (Gemini) |
| 17:00 | Data integrity check |
| 18:00 Sun | Instrument refresh + weekly patterns |
| Hourly | Disk monitor |
| Monthly 1st | Backup restore drill |

## Token Flow

```
Windows PC (08:00)
  │  zerodha_morning.bat
  │  → Opens browser, user logs in
  │  → Token saved to local file
  │
  ├─ copy_token_to_vm.bat
  │  → SCP token to VM data_store/session/zerodha_token.json
  │
  └─ OR: VM auto_refresh_token.py (TOTP-based, no browser)
      → Generates token programmatically
      → Saves to data_store/session/zerodha_token.json

VM (on startup)
  │  main.py reads zerodha_token.json
  │  → TokenMonitor watches for expiry
  │  → If token missing: exit code 6 (non-interactive) or prompt (interactive)
```

## Firewall (UFW)

```bash
sudo ufw allow ssh
sudo ufw allow 5000/tcp    # webhook
sudo ufw allow 8080/tcp    # healthcheck
sudo ufw enable
sudo ufw status
```

## Git Deploy Hook

The VM has a bare git repo at `~/trading-system.git` with a post-receive
hook that auto-deploys to the working directory:

```bash
# ~/trading-system.git/hooks/post-receive
#!/bin/bash
echo "Deploying main to /home/ubuntu/systems/trading-system..."
cd /home/ubuntu/systems/trading-system
git checkout main
echo "Deployment complete."
```

Push deploys: `git push origin main` → auto-deploys on VM.

## Backup and Restore

### Automatic Backups
- Daily at 01:00 IST: `data_store/backups/trading_system-YYYY-MM-DD.db`
- 7-day rolling cleanup at 02:00 IST

### Manual Backup
```bash
sqlite3 data_store/trading_system.db ".backup data_store/backups/manual_$(date +%Y%m%d_%H%M).db"
```

### Restore
```bash
sudo systemctl stop trading-system
cp data_store/backups/trading_system-2026-06-01.db data_store/trading_system.db
sudo systemctl start trading-system
```

### Monthly Drill
`scripts/backup_restore_drill.py` runs on the 1st of each month at 03:00 IST.
Validates schema, integrity, table count, FK consistency, and sample queries.

## Monitoring

### Health Endpoints
- `http://<vm-ip>:5000/health` — Webhook receiver health
- Queue depth, kill switch state, system status

### Log Files
- `logs/system_YYYY-MM-DD.log` — Main application log
- `logs/cron-*.log` — Per-cron-job logs
- `journalctl -u trading-system` — Systemd journal

### Telegram Alerts
All CRITICAL, WARNING, and INFO alerts sent to configured channels.
Daily summary at EOD.

## Switching Paper → Live

1. Stop the system
2. Revert TEMP config values: `python3 scripts/revert_temp_config.py --apply --confirm`
3. Update systemd: change `--mode paper` to `--mode live`
4. Verify capital: check Zerodha funds match config
5. Start the system
6. Monitor first 30 minutes closely
