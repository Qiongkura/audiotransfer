@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
if "%~1"=="" (
  echo 用法: run_cli.bat input.mp3 -o out_dir [其他参数]
  exit /b 2
)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m audiotransfer.cli %*
) else (
  python -m audiotransfer.cli %*
)
set EXIT_CODE=%ERRORLEVEL%
endlocal & exit /b %EXIT_CODE%
