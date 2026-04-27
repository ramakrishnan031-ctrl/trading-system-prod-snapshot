#!/bin/bash
# deploy/install_vm_services.sh -- install trading-system + token-watcher
# systemd units on the VM, plus the iptables ACCEPT for the webhook
# listener port.
#
# Idempotent: re-running replaces the unit files, reloads systemd, and
# inserts the firewall rule only if it is not already present.
# Does NOT start trading-system directly -- token-watcher handles that
# once a fresh token lands on the VM (token_watcher.sh detects +
# triggers systemctl start).

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/ubuntu/trading-system}"
cd "$PROJECT_DIR"

echo "=== Installing systemd units from $PROJECT_DIR/deploy/systemd/ ==="
sudo cp "$PROJECT_DIR/deploy/systemd/trading-system.service" /etc/systemd/system/
sudo cp "$PROJECT_DIR/deploy/systemd/token-watcher.service"  /etc/systemd/system/
sudo cp "$PROJECT_DIR/deploy/systemd/alert-watcher.service"  /etc/systemd/system/

echo "=== chmod +x deploy/token_watcher.sh ==="
chmod +x "$PROJECT_DIR/deploy/token_watcher.sh"

echo "=== systemctl daemon-reload ==="
sudo systemctl daemon-reload

echo "=== Enabling services (start on boot) ==="
sudo systemctl enable trading-system.service
sudo systemctl enable token-watcher.service
sudo systemctl enable alert-watcher.service

echo "=== Firewall: allow port 5000 (Chartink webhooks) ==="
# Oracle Cloud's default Ubuntu image ships an INPUT chain with a REJECT
# rule near the top (position 6 on this VM as of 2026-04-27) that drops
# anything not explicitly allowed before it. Insert an ACCEPT for
# tcp/5000 at position 5 (just before the REJECT) so the webhook
# listener (bound to 0.0.0.0:5000 since commit 2d1e580) is reachable
# on the external interface. iptables -C makes the insert idempotent.
if ! sudo iptables -C INPUT -p tcp --dport 5000 -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 5 -p tcp --dport 5000 -j ACCEPT
    echo "  iptables: INSERTED tcp/5000 ACCEPT at INPUT position 5"
else
    echo "  iptables: tcp/5000 ACCEPT already present; no insert needed"
fi
# Persist the live ruleset so it survives reboots and image rebuilds.
sudo netfilter-persistent save

echo "=== Starting token-watcher (trading-system starts only when fresh token lands) ==="
sudo systemctl restart token-watcher.service

echo
echo "============================================================"
echo "  Service status"
echo "============================================================"
sudo systemctl status token-watcher.service --no-pager || true
echo
sudo systemctl status trading-system.service --no-pager || true
echo
sudo systemctl status alert-watcher.service --no-pager || true
echo
echo "Install complete."
echo "  - token-watcher: active, polling every 30s"
echo "  - trading-system: waits for token_watcher trigger (or Restart=always if manually started)"
echo "  - alert-watcher: (start manually when ready: sudo systemctl start alert-watcher)"
