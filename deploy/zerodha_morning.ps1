# deploy/zerodha_morning.ps1 -- Trading System v2 morning starter (PC side)
#
# PowerShell equivalent of zerodha_morning.bat.
# Run once each trading morning from the project root.
#
# Steps:
#   1. Check if zerodha_token.json exists and is not expired (created today)
#   2. If expired or missing -> run zerodha_login.py (opens browser for TOTP)
#   3. SCP the fresh token file to the VM
#   4. Wait 15 seconds for the VM token-watcher to detect it
#   5. SSH to verify the trading-system systemd service is active
#   6. Report success or failure with next steps

$ErrorActionPreference = "Stop"
$ProjectRoot = "D:\Projects\trading-system"
$TokenFile = Join-Path $ProjectRoot "data_store\session\zerodha_token.json"
$VenvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
$VMTokenDest = "trading-vm:~/systems/trading-system/data_store/session/"

Write-Host ""
Write-Host ("=" * 60)
Write-Host "  Trading System v2 -- Morning Startup (PC)"
Write-Host ("=" * 60)
Write-Host ""

Set-Location $ProjectRoot

# Activate venv
if (-not (Test-Path $VenvActivate)) {
    Write-Host "ERROR: venv not found at $VenvActivate" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
& $VenvActivate

# Step 1: Check if token file exists and was created today
$NeedLogin = $false

if (-not (Test-Path $TokenFile)) {
    Write-Host "[!] Token file not found. Starting Zerodha login..." -ForegroundColor Yellow
    $NeedLogin = $true
} else {
    $TokenDate = (Get-Item $TokenFile).LastWriteTime.Date
    $Today = (Get-Date).Date
    if ($TokenDate -ne $Today) {
        Write-Host "[!] Token expired (last modified: $($TokenDate.ToString('dd-MMM-yyyy'))). Starting fresh login..." -ForegroundColor Yellow
        $NeedLogin = $true
    } else {
        Write-Host "[OK] Token file exists and appears current." -ForegroundColor Green
    }
}

# Step 2: Run Zerodha login if needed
if ($NeedLogin) {
    python scripts\zerodha_login.py --account LFL836
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "ERROR: zerodha_login.py failed. VM will NOT be started." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "[OK] Zerodha login successful." -ForegroundColor Green
}

# Step 3: SCP token to VM
Write-Host "[..] Copying token to VM..."
scp $TokenFile $VMTokenDest
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: SCP failed. Check SSH connectivity to trading-vm." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "[OK] Token copied to VM." -ForegroundColor Green

# Step 4: Wait for VM token-watcher
Write-Host "[..] Waiting 15s for VM to detect new token..."
Start-Sleep -Seconds 15

# Step 5: Check service status
Write-Host "[..] Checking VM service status..."
$ServiceStatus = ssh trading-vm "systemctl is-active trading-system" 2>$null
if ($ServiceStatus -ne "active") {
    Write-Host ""
    Write-Host "ERROR: trading-system service is NOT active on VM." -ForegroundColor Red
    Write-Host "       Check VM logs: ssh trading-vm `"journalctl -u trading-system -n 50`""
    Write-Host ""
    Write-Host "Opening SSH session for troubleshooting..."
    Start-Process ssh -ArgumentList "trading-vm"
    Read-Host "Press Enter to exit"
    exit 1
}

# Step 6: Success
Write-Host ""
Write-Host ("=" * 60)
Write-Host "  DONE - System Ready" -ForegroundColor Green
Write-Host ("=" * 60)
Write-Host "  Token: copied to VM"
Write-Host "  Service: ACTIVE"
Write-Host "  Check Telegram for SYSTEM START alert."
Write-Host ("=" * 60)
Write-Host ""
Read-Host "Press Enter to close"
