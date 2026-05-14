@echo off
REM scripts/copy_token_to_vm.bat -- FIX-040 atomic token copy
REM
REM Two-phase atomic copy to prevent token_watcher from reading
REM a partially-written file:
REM   1. SCP to zerodha_token.json.tmp
REM   2. SSH mv to zerodha_token.json (atomic on filesystem)

echo.
echo ============================================================
echo   Copying Zerodha token to Oracle VM (atomic)
echo ============================================================
echo.

REM Phase 1: Copy to .tmp
echo [1/2] Copying to zerodha_token.json.tmp...
scp -i "C:\Users\rama\.ssh\trading_vm_secure" ^
    "D:\Projects\trading-system\data_store\session\zerodha_token.json" ^
    ubuntu@161.118.188.171:/home/ubuntu/systems/trading-system/data_store/session/zerodha_token.json.tmp

if not %errorlevel% equ 0 (
    echo.
    echo   ERROR: SCP to .tmp failed ^(error code %errorlevel%^)
    echo   Check: VM reachable? token file exists on PC?
    echo.
    pause
    exit /b 1
)

REM Phase 2: Atomic rename via SSH
echo [2/2] Atomically moving .tmp to final location...
ssh -i "C:\Users\rama\.ssh\trading_vm_secure" ^
    ubuntu@161.118.188.171 ^
    "mv /home/ubuntu/systems/trading-system/data_store/session/zerodha_token.json.tmp /home/ubuntu/systems/trading-system/data_store/session/zerodha_token.json"

if %errorlevel% equ 0 (
    echo.
    echo   Token copied successfully (atomic).
    echo   VM ready at: ubuntu@161.118.188.171
) else (
    echo.
    echo   ERROR: mv command failed ^(error code %errorlevel%^)
    echo   Orphaned .tmp file may exist on VM.
)

echo.
pause
