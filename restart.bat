@echo off
cd /d "%~dp0"

echo Stopping Ove...
powershell -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
timeout /t 2 /nobreak >nul

echo Starting...
start "Voice" /MIN "..\voice\GPT-SoVITS-v3lora-20250228\runtime\python.exe" "..\voice\GPT-SoVITS-v3lora-20250228\gsv_infer_server.py"
start "TTS" /MIN python "%~dp0tts_bridge.py"
start "Godot" "%~dp0Godot_v4.4-stable_win64.exe" --rendering-driver opengl3 --path "%~dp0"
exit
