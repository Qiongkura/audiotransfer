@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" echo Install failed. Please check the message above.
endlocal & exit /b %EXIT_CODE%
