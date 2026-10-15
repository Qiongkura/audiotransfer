@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
set "TEMP=%CD%\.cache\tmp"
set "TMP=%TEMP%"
set "PIP_CACHE_DIR=%CD%\.cache\pip"
if not exist "%TEMP%" mkdir "%TEMP%"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m audiotransfer.gui %*
) else (
  python -m audiotransfer.gui %*
)
set EXIT_CODE=%ERRORLEVEL%
endlocal & exit /b %EXIT_CODE%
