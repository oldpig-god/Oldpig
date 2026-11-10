@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" goto missing
.venv\Scripts\python.exe -X utf8 scripts\run_local.py %*
exit /b %errorlevel%
:missing
echo Run setup-windows.cmd first, or follow foragent.md.
pause
exit /b 1
