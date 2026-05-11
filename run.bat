@echo off
cd /d "%~dp0"
powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0run.ps1"
exit
