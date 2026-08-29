@echo off
REM ============================================================
REM  SATS - Bandit security scan (on-demand, manual)
REM  Static analysis of the trading-system Python source.
REM  Output: console + timestamped report in sats\reports\
REM  Tool venv is used directly via absolute path (no activate).
REM ============================================================
chcp 65001 >nul
setlocal enableextensions

REM PYTHONUTF8=1: force Python (Bandit) to write the -o report as UTF-8. Without
REM it, Bandit can crash on Windows (cp1252) when a finding's code snippet holds
REM non-ASCII text. chcp sets only the CONSOLE code page, not Python's file writes.
set "PYTHONUTF8=1"

set "BANDIT_EXE=D:\Projects\trading-system\sats\bandit-env\Scripts\bandit.exe"
set "TARGET=D:\Projects\trading-system"
set "REPORT_DIR=D:\Projects\trading-system\sats\reports"
set "EXCLUDE=D:\Projects\trading-system\venv,D:\Projects\trading-system\sats,D:\Projects\trading-system\.git"

REM --- ensure reports directory exists (report write never fails) ---
if not exist "%REPORT_DIR%" mkdir "%REPORT_DIR%"

REM --- locale-independent timestamp: yyyyMMdd_HHmmss ---
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
set "REPORT=%REPORT_DIR%\bandit_%TS%.txt"

echo ============================================================
echo  SATS / Bandit security scan
echo  Target  : %TARGET%
echo  Exclude : venv, sats, .git
echo  Report  : %REPORT%
echo ============================================================
echo.
echo Scanning... (please wait)
echo.

REM --- run ONCE: write txt report to file ---
"%BANDIT_EXE%" -r "%TARGET%" -x "%EXCLUDE%" -f txt -o "%REPORT%"

REM --- echo the saved report to the console ---
echo.
echo ----------------------- RESULTS ----------------------------
if exist "%REPORT%" (
    type "%REPORT%"
) else (
    echo [ERROR] No report was generated - check the Bandit output above.
)
echo ------------------------------------------------------------
echo  Report saved to: %REPORT%
echo.

endlocal
pause
