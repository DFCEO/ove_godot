@echo off
cd /d "%~dp0"
echo [1/4] Stopping old Ove...
powershell -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
timeout /t 3 /nobreak >nul

echo [2/5] Starting GSV + TTS...
start "GSV Server" /MIN cmd /c ""..\voice\GPT-SoVITS-v3lora-20250228\runtime\python.exe" "..\voice\GPT-SoVITS-v3lora-20250228\gsv_infer_server.py" >"%~dp0gsv_stdout.log" 2>"%~dp0gsv_stderr.log""
start "TTS Bridge" /MIN cmd /c "C:\Users\zj199\AppData\Local\Programs\Python\Python310\python.exe "%~dp0tts_bridge.py" >"%~dp0tts_stdout.log" 2>"%~dp0tts_stderr.log""

echo [3/5] Starting Ove Core...
:: --device 可选，不传则自动扫描最佳麦克风
start "Ove Core" /MIN cmd /c "C:\Users\zj199\AppData\Local\Programs\Python\Python310\python.exe "%~dp0ove_core.py" >"%~dp0core_stdout.log" 2>"%~dp0core_stderr.log""

echo [4/5] Starting Voice Agent...
timeout /t 5 /nobreak >nul
start "Voice Agent" /MIN cmd /c "C:\Users\zj199\AppData\Local\Programs\Python\Python310\python.exe "%~dp0ove_voice_agent.py" >"%~dp0agent_stdout.log" 2>"%~dp0agent_stderr.log""

echo [5/5] Starting Godot...
timeout /t 8 /nobreak >nul
start "Ove Godot" "%~dp0Godot_v4.4-stable_win64.exe" --rendering-driver opengl3 --path "%~dp0"
echo Done. Ove running.
