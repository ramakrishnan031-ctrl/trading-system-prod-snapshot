# ============================================================================
# Audit 2026-05-18 Deployment Script
# ============================================================================
# Deploy 18 audit fixes (commits a4ef762 → b52ef90) to VM
#
# CRITICAL: Run AFTER market close (post 15:30 IST)
# Service restart required after deployment
# ============================================================================

param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$VM_HOST = "trading-vm"
$VM_PATH = "~/systems/trading-system"
$LOCAL_BASE = "D:\Projects\trading-system"

Write-Host "=== Audit 2026-05-18 Deployment ===" -ForegroundColor Cyan
Write-Host "Target: $VM_HOST" -ForegroundColor Yellow
Write-Host ""

# Verify market is closed (unless Force flag used)
if (-not $Force) {
    $currentTime = Get-Date
    $hour = $currentTime.Hour
    $minute = $currentTime.Minute

    if ($hour -lt 15 -or ($hour -eq 15 -and $minute -lt 30)) {
        Write-Host "ERROR: Market still open (current time: $($currentTime.ToString('HH:mm')))" -ForegroundColor Red
        Write-Host "Deployment allowed only after 15:30 IST" -ForegroundColor Red
        Write-Host "Use -Force to override this check" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "[OK] Market closed - proceeding with deployment" -ForegroundColor Green
} else {
    Write-Host "[FORCED] Skipping market hours check" -ForegroundColor Yellow
}

Write-Host "[OK] Market closed - proceeding with deployment" -ForegroundColor Green
Write-Host ""

# Files to deploy (18 Python files + 1 config)
$files = @(
    # Core modules (3 files)
    "core/config_loader.py",
    "core/logger.py",

    # Broker modules (6 files)
    "broker/cost_calculator.py",
    "broker/order_state_machine.py",
    "broker/product_resolver.py",
    "broker/rate_limiter.py",
    "broker/slippage_engine.py",

    # Alerts (1 file)
    "alerts/critical.py",

    # Capital (1 file)
    "capital/fund_manager.py",

    # Data (1 file)
    "data/live_feed.py",

    # Screening (3 files)
    "screening/quality_scorer.py",
    "screening/secondary_screener.py",
    "screening/step_executor.py",

    # Signals (1 file)
    "signals/signal_processor.py",

    # Utils (3 files)
    "utils/holiday_guard.py",
    "utils/instance_lock.py",
    "utils/startup_checks.py",

    # Scripts (1 file)
    "scripts/alert_watcher.py",

    # Config (1 file)
    "config/system_config.yaml"
)

Write-Host "Files to deploy: $($files.Count)" -ForegroundColor Cyan
Write-Host ""

# Deploy each file
$successCount = 0
$failCount = 0

foreach ($file in $files) {
    $localPath = Join-Path $LOCAL_BASE $file
    $remotePath = "${VM_HOST}:${VM_PATH}/${file}"

    if (-not (Test-Path $localPath)) {
        Write-Host "[SKIP] $file (not found locally)" -ForegroundColor Yellow
        continue
    }

    try {
        Write-Host "[COPY] $file" -ForegroundColor Gray
        scp $localPath $remotePath
        if ($LASTEXITCODE -eq 0) {
            $successCount++
        } else {
            throw "SCP failed with exit code $LASTEXITCODE"
        }
    }
    catch {
        Write-Host "[FAIL] $file - $_" -ForegroundColor Red
        $failCount++
    }
}

Write-Host ""
Write-Host "=== Deployment Summary ===" -ForegroundColor Cyan
Write-Host "Success: $successCount files" -ForegroundColor Green
Write-Host "Failed:  $failCount files" -ForegroundColor $(if ($failCount -gt 0) { "Red" } else { "Gray" })
Write-Host ""

if ($failCount -gt 0) {
    Write-Host "ERROR: Deployment incomplete" -ForegroundColor Red
    exit 1
}

Write-Host "[OK] All files deployed successfully" -ForegroundColor Green
Write-Host ""

# Restart service
Write-Host "=== Service Restart ===" -ForegroundColor Cyan
Write-Host "Run the following commands on VM:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  ssh $VM_HOST" -ForegroundColor White
Write-Host "  sudo systemctl restart trading-system" -ForegroundColor White
Write-Host "  sudo systemctl status trading-system" -ForegroundColor White
Write-Host "  sudo journalctl -u trading-system -n 50 --no-pager" -ForegroundColor White
Write-Host ""

# Verification commands
Write-Host "=== Post-Deployment Verification ===" -ForegroundColor Cyan
Write-Host "After restart, verify:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  # Check for exceptions" -ForegroundColor White
Write-Host "  grep -i 'exception\|error' ~/systems/trading-system/logs/trading_system.log | tail -20" -ForegroundColor White
Write-Host ""
Write-Host "  # Verify audit fixes loaded" -ForegroundColor White
Write-Host "  grep -i 'FIX-10' ~/systems/trading-system/logs/trading_system.log" -ForegroundColor White
Write-Host ""

Write-Host "=== Deployment Complete ===" -ForegroundColor Green
