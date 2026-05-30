# UptimeRobot Health Monitor Setup

External uptime monitoring for the trading system VM via the `/health` endpoint.

## Prerequisites

- Trading system running on VM (161.118.188.171)
- Port 8080 open in UFW firewall (`sudo ufw allow 8080/tcp`)
- Healthcheck server started by main.py at boot (FIX-132 Item 15)

## Setup Steps

1. Go to [uptimerobot.com](https://uptimerobot.com) and create a free account
2. Click **Add New Monitor**
3. Configure:
   - **Monitor Type:** HTTP(s)
   - **Friendly Name:** Trading System Health
   - **URL:** `http://161.118.188.171:8080/health`
   - **Monitoring Interval:** 1 minute
4. Under **Alert Contacts**, add your email address
5. Click **Create Monitor**

## Expected Response

```json
{
  "status": "ok",
  "uptime_seconds": 12345.6,
  "trades_today": 3,
  "timestamp": "2026-05-31T10:30:00+05:30"
}
```

## Firewall

If not already open:

```bash
ssh trading-vm "sudo ufw allow 8080/tcp"
```

## Verify Locally

```bash
curl http://161.118.188.171:8080/health
```
