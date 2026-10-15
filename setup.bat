@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
set "TEMP=%CD%\.cache\tmp"
set "TMP=%TEMP%"
set "PIP_CACHE_DIR=%CD%\.cache\pip"
if not exist "%TEMP%" mkdir "%TEMP%"
if not exist "%PIP_CACHE_DIR%" mkdir "%PIP_CACHE_DIR%"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" echo Install failed. Please check the message above.
endlocal & exit /b %EXIT_CODE%
