@echo off
echo.
echo ============================================================
echo   Copying Zerodha token to Oracle VM
echo ============================================================
echo.

scp -i "C:\Users\rama\.ssh\trading_vm_secure" ^
    "D:\Projects\trading-system\data_store\session\zerodha_token.json" ^
    ubuntu@129.154.253.244:/home/ubuntu/trading-system/data_store/session/zerodha_token.json

if %errorlevel% equ 0 (
    echo.
    echo   Token copied successfully.
    echo   VM ready at: ubuntu@129.154.253.244
) else (
    echo.
    echo   ERROR: Token copy failed ^(error code %errorlevel%^)
    echo   Check: VM reachable? token file exists on PC?
)

echo.
pause
