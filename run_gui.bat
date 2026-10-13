@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m audiotransfer.gui %*
) else (
  python -m audiotransfer.gui %*
)
set EXIT_CODE=%ERRORLEVEL%
endlocal & exit /b %EXIT_CODE%
