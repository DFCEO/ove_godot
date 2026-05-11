$root = $PSScriptRoot

Start-Process -FilePath "$root\..\voice\GPT-SoVITS-v3lora-20250228\runtime\python.exe" `
  -ArgumentList "$root\..\voice\GPT-SoVITS-v3lora-20250228\gsv_infer_server.py" `
  -WorkingDirectory "$root\..\voice\GPT-SoVITS-v3lora-20250228" `
  -WindowStyle Hidden

Start-Process python `
  -ArgumentList "$root\tts_bridge.py" `
  -WorkingDirectory $root `
  -WindowStyle Hidden

Start-Process -FilePath "$root\Godot_v4.4-stable_win64.exe" `
  -ArgumentList "--rendering-driver opengl3", "--path", $root `
  -WorkingDirectory $root
