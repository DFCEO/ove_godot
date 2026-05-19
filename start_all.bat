@echo off
cd /d "%~dp0"
echo [1/3] Stopping old Ove...
powershell -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
timeout /t 2 /nobreak >nul

echo [2/3] Starting GSV + TTS...
start "GSV Server" /MIN "..\voice\GPT-SoVITS-v3lora-20250228\runtime\python.exe" "..\voice\GPT-SoVITS-v3lora-20250228\gsv_infer_server.py"
start "TTS Bridge" /MIN C:\Users\zj199\AppData\Local\Programs\Python\Python310\python.exe "%~dp0tts_bridge.py"

echo [3/4] Starting Ove Core (Brain + Ears + Mouth)...
start "Ove Core" /MIN C:\Users\zj199\AppData\Local\Programs\Python\Python310\python.exe "%~dp0ove_core.py" --device 3

echo [4/4] Starting Godot...
timeout /t 10 /nobreak >nul
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
start "Ove Godot" "%ROOT%\Godot_v4.4-stable_win64.exe" --rendering-driver opengl3 --path "%ROOT%"
echo Done. Ove Core running.
exit
