@echo off
REM deploy/zerodha_morning.bat -- Trading System v2 morning starter (PC side)
REM
REM Run once each trading morning. Steps:
REM   1. Check if zerodha_token.json exists and is not expired
REM   2. If expired or missing -> open Zerodha login URL in browser
REM   3. After login, copy token to VM via SCP
REM   4. Wait 15s for VM to pick up token
REM   5. Verify trading-system service is active on VM
REM   6. Report status

echo.
echo ============================================================
echo   Trading System v2 -- Morning Startup (PC)
echo ============================================================
echo.

cd /d D:\Projects\trading-system

call venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: failed to activate venv. Check D:\Projects\trading-system\venv exists.
    pause
    exit /b 1
)

REM Step 1: Check if token exists and is valid
set TOKEN_FILE=data_store\session\zerodha_token.json
if not exist "%TOKEN_FILE%" (
    echo [!] Token file not found. Starting Zerodha login...
    goto :do_login
)

REM Check if token was created today (locale-independent via Python)
python -c "import json,sys,datetime; d=json.load(open(r'%TOKEN_FILE%')); sys.exit(0 if d.get('date')==datetime.date.today().isoformat() else 1)" 2>nul
if errorlevel 1 (
    echo [!] Token expired (not from today). Starting fresh login...
    goto :do_login
)
echo [OK] Token file exists and appears current.
goto :copy_token

:do_login
REM Step 2: Run Zerodha login (opens browser for TOTP)
python scripts\zerodha_login.py --account LFL836
if errorlevel 1 (
    echo.
    echo ERROR: zerodha_login.py failed. VM will NOT be started.
    pause
    exit /b 1
)
echo [OK] Zerodha login successful.

:copy_token
REM Step 3: SCP token to VM
echo [..] Copying token to VM...
scp data_store\session\zerodha_token.json trading-vm:~/systems/trading-system/data_store/session/
if errorlevel 1 (
    echo ERROR: SCP failed. Check SSH connectivity to trading-vm.
    pause
    exit /b 1
)
echo [OK] Token copied to VM.

REM Step 4: Wait for VM token-watcher to pick up
echo [..] Waiting 15s for VM to detect new token...
timeout /t 15 /nobreak >nul

REM Step 5: Check if trading-system service is active
echo [..] Checking VM service status...
ssh trading-vm "systemctl is-active trading-system" 2>nul | findstr /c:"active" >nul
if errorlevel 1 (
    echo.
    echo ERROR: trading-system service is NOT active on VM.
    echo        Check VM logs: ssh trading-vm "journalctl -u trading-system -n 50"
    echo.
    echo Opening SSH session for troubleshooting...
    start ssh trading-vm
    pause
    exit /b 1
)

REM Step 6: Success
echo.
echo ============================================================
echo   DONE - System Ready
echo ============================================================
echo   Token: copied to VM
echo   Service: ACTIVE
echo   Check Telegram for SYSTEM START alert.
echo ============================================================
echo.
pause
