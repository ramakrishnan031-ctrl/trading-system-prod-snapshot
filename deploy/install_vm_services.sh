#!/bin/bash
# deploy/install_vm_services.sh -- install trading-system + token-watcher
# systemd units on the VM.
#
# Idempotent: re-running replaces the unit files and reloads systemd.
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
