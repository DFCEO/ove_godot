$root = $PSScriptRoot

# 先停旧的
& "$root\stop.ps1"

Start-Sleep -Seconds 1

# 启动语音推理服务
Start-Process -FilePath "$root\..\voice\GPT-SoVITS-v3lora-20250228\runtime\python.exe" `
  -ArgumentList "$root\..\voice\GPT-SoVITS-v3lora-20250228\gsv_infer_server.py" `
  -WorkingDirectory "$root\..\voice\GPT-SoVITS-v3lora-20250228" `
  -WindowStyle Hidden

# 启动 TTS 桥
Start-Process python `
  -ArgumentList "$root\tts_bridge.py" `
  -WorkingDirectory $root `
  -WindowStyle Hidden

# 启动 Godot
Start-Process -FilePath "$root\Godot_v4.4-stable_win64.exe" `
  -ArgumentList "--rendering-driver opengl3", "--path", $root `
  -WorkingDirectory $root
