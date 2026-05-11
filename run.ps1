$root = $PSScriptRoot

# 先停旧的
& "$root\stop.ps1"
Start-Sleep -Seconds 2

# 语音推理
$voicePy = "$root\..\voice\GPT-SoVITS-v3lora-20250228\runtime\python.exe"
$voiceScript = "$root\..\voice\GPT-SoVITS-v3lora-20250228\gsv_infer_server.py"
$voiceDir = "$root\..\voice\GPT-SoVITS-v3lora-20250228"
$voiceArgs = "`"$voiceScript`""
Start-Process -FilePath $voicePy -ArgumentList $voiceArgs -WorkingDirectory $voiceDir -WindowStyle Hidden

# TTS 桥
$ttsArgs = "`"$root\tts_bridge.py`""
Start-Process -FilePath "C:\Users\zj199\AppData\Local\Programs\Python\Python310\python.exe" -ArgumentList $ttsArgs -WorkingDirectory $root -WindowStyle Hidden

# Godot
Start-Process -FilePath "$root\Godot_v4.4-stable_win64.exe" -ArgumentList "--rendering-driver opengl3 --path `"$root`"" -WorkingDirectory $root
