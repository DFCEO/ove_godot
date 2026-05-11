$root = $PSScriptRoot

# 先停旧的
& "$root\stop.ps1"
Start-Sleep -Seconds 1

# 语音推理服务
$voiceDir = "$root\..\voice\GPT-SoVITS-v3lora-20250228"
Start-Process -FilePath "$voiceDir\runtime\python.exe" -ArgumentList "$voiceDir\gsv_infer_server.py" -WorkingDirectory $voiceDir -WindowStyle Hidden

# TTS 桥
$pythonPath = (Get-Command python).Source
Start-Process -FilePath $pythonPath -ArgumentList "$root\tts_bridge.py" -WorkingDirectory $root -WindowStyle Hidden

# Godot
Start-Process -FilePath "$root\Godot_v4.4-stable_win64.exe" -ArgumentList "--rendering-driver opengl3", "--path", $root -WorkingDirectory $root
