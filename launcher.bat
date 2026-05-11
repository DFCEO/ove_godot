@echo off
cd /d "%~dp0"
echo Stopping old...
powershell -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
timeout /t 2 /nobreak >nul
echo Launching Ove...
start "Ove" /MIN powershell -ExecutionPolicy Bypass -File "%~dp0launcher.ps1"
echo Done. Robot starting...
timeout /t 1 /nobreak >nul
exit
