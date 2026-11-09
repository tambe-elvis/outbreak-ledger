@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 goto folder_error
set PYTHONUTF8=1
set PYTHONNOUSERSITE=1
set PIP_DISABLE_PIP_VERSION_CHECK=1
title Outbreak Ledger 0.5
if not exist "%~dp0launch_ledger.py" goto missing_files
if not exist "%~dp0requirements.txt" goto missing_files
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3.12 -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
if errorlevel 1 goto try_default_py
py -3.12 "%~dp0launch_ledger.py" %*
goto finished
:try_default_py
py -3 "%~dp0launch_ledger.py" %*
goto finished
:try_python
python "%~dp0launch_ledger.py" %*
:finished
if errorlevel 1 goto failed
exit /b 0
:missing_files
echo Files are missing. Right-click the ZIP and choose Extract All.
echo Open the extracted application folder and run this launcher there.
goto failed
:folder_error
echo The application folder could not be opened.
:failed
echo.
echo Send startup_log.txt if present, together with the error above.
pause
exit /b 1
