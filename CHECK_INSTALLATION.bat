@echo off
setlocal
cd /d "%~dp0"
call "%~dp0START_OUTBREAK_LEDGER.bat" --check
pause
endlocal
