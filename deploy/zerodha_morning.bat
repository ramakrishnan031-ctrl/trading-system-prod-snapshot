@echo off
REM deploy/zerodha_morning.bat -- Trading System v2 morning starter (PC side)
REM
REM Run once each trading morning. It performs the full manual step
REM (Zerodha login + TOTP), copies the fresh token to the VM, and
REM exits. The VM's token-watcher picks up the new token within 60
REM seconds and auto-starts trading-system headlessly.

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

python scripts\zerodha_login.py --account LFL836
if errorlevel 1 (
    echo.
    echo ERROR: zerodha_login.py failed. VM will NOT be started.
    pause
    exit /b 1
)

call scripts\copy_token_to_vm.bat

echo.
echo ============================================================
echo   Done. VM will auto-start within 60 seconds.
echo   Check Telegram for SYSTEM START alert.
echo ============================================================
echo.
pause
