@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
".venv\Scripts\python.exe" -m audiotransfer.scored_song --profile examples/chatang/profile.json --baseline examples/chatang/baseline-v5.json --analysis examples/chatang/song-analysis.npz --output out_chatang_v8_interlude --transpose 12 --sound-mode pulse --active-low
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m audiotransfer.player --source out_chatang_v8_interlude -o out_chatang_player
if errorlevel 1 goto failed
echo Complete. Build and flash out_chatang_player with PlatformIO.
goto done
:failed
echo Generation failed. Run setup.bat first and check the error above.
:done
pause
endlocal
