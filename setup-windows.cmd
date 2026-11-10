@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
py -3.11 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -X utf8 -m pip install --upgrade pip
if errorlevel 1 goto failed
.venv\Scripts\python.exe -X utf8 -m pip install -r requirements-research.txt
if errorlevel 1 goto failed
echo Setup complete. Run start-windows.cmd
pause
exit /b 0
:failed
echo Setup failed. See foragent.md for Python 3.11 and manual installation.
pause
exit /b 1
