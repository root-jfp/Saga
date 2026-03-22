@echo off
cd /d "%~dp0"

tasklist /fi "imagename eq pythonw.exe" | find /i "pythonw.exe" > nul 2>&1
if not errorlevel 1 (
    echo Book Reader is already running.
    exit /b 0
)

echo Starting Book Reader in background...
start "BookReader" /B pythonw run.py 1>>server.log 2>&1
echo Done. Logs: server.log
