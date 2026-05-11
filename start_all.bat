@echo off
cd /d "%~dp0"
echo Stopping old Ove...
powershell -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
timeout /t 2 /nobreak >nul

echo Starting Ove services...
start "GSV Server" /MIN "..\voice\GPT-SoVITS-v3lora-20250228\runtime\python.exe" "..\voice\GPT-SoVITS-v3lora-20250228\gsv_infer_server.py"
start "TTS Bridge" /MIN C:\Users\zj199\AppData\Local\Programs\Python\Python310\python.exe "%~dp0tts_bridge.py"
timeout /t 5 /nobreak >nul
start "Ove Godot" "%~dp0Godot_v4.4-stable_win64.exe" --rendering-driver opengl3 --path "%~dp0"
echo Done.
exit
