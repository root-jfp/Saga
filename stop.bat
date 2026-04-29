@echo off
tasklist /fi "imagename eq pythonw.exe" | find /i "pythonw.exe" > nul 2>&1
if errorlevel 1 (
    echo No pythonw processes found.
    exit /b 0
)

taskkill /f /im pythonw.exe /t > nul 2>&1
echo Saga stopped.
