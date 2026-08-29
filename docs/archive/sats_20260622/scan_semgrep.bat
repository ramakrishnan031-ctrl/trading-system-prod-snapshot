@echo off
REM ============================================================
REM  SATS - Semgrep security scan (on-demand, manual)
REM  Rulesets : p/python + p/security-audit (community, login-free)
REM  Output   : console + timestamped report in sats\reports\
REM  Baseline : if sats\semgrep_baseline.txt exists, ONLY findings
REM             NEW since that commit are reported (delete/rename
REM             the file for a full scan). Pinned at 2026-06-22.
REM  NOTE: the FIRST run downloads the rulesets from the registry
REM        (needs internet once); later runs work from cache.
REM ============================================================
chcp 65001 >nul
setlocal enableextensions

REM PYTHONUTF8=1: force Python (Semgrep) to write the --output report as UTF-8.
REM Without it, Semgrep CRASHES on Windows (cp1252) when findings contain
REM non-ASCII text. chcp sets only the CONSOLE code page, not Python's file writes.
set "PYTHONUTF8=1"

set "SEMGREP_EXE=D:\Projects\trading-system\sats\semgrep-env\Scripts\semgrep.exe"
set "TARGET=D:\Projects\trading-system"
set "REPORT_DIR=D:\Projects\trading-system\sats\reports"
set "BASELINE_FILE=D:\Projects\trading-system\sats\semgrep_baseline.txt"

REM --- ensure reports directory exists (report write never fails) ---
if not exist "%REPORT_DIR%" mkdir "%REPORT_DIR%"

REM --- optional baseline: report only findings NEW since the pinned commit ---
set "BASELINE_ARG="
set "BASELINE_MODE=FULL (all findings)"
if exist "%BASELINE_FILE%" for /f "usebackq eol=# delims=" %%h in ("%BASELINE_FILE%") do (
    set "BASELINE_ARG=--baseline-commit %%h"
    set "BASELINE_MODE=BASELINE - new since %%h"
)

REM --- locale-independent timestamp: yyyyMMdd_HHmmss ---
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
set "REPORT=%REPORT_DIR%\semgrep_%TS%.txt"

echo ============================================================
echo  SATS / Semgrep security scan
echo  Target   : %TARGET%
echo  Rulesets : p/python + p/security-audit
echo  Exclude  : venv, sats, .git
echo  Mode     : %BASELINE_MODE%
echo  Report   : %REPORT%
echo ============================================================
echo.
echo Scanning... (first run downloads rulesets, please wait)
echo.

REM --- run ONCE from the repo root so --baseline-commit git diff resolves ---
pushd "%TARGET%"
"%SEMGREP_EXE%" scan --config p/python --config p/security-audit %BASELINE_ARG% --exclude venv --exclude sats --exclude .git --output "%REPORT%" "%TARGET%"
popd

REM --- echo the saved report to the console ---
echo.
echo ----------------------- RESULTS ----------------------------
if exist "%REPORT%" (
    type "%REPORT%"
) else (
    echo [ERROR] No report was generated - check the Semgrep output above.
)
echo ------------------------------------------------------------
echo  Mode   : %BASELINE_MODE%
echo  Report : %REPORT%
echo.

endlocal
pause
