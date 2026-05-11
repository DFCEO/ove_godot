# launcher.ps1 — Reliable Ove service launcher
# Uses PowerShell jobs (which provide proper console env for Python)
# Keeps running to maintain job lifecycle
$root = $PSScriptRoot

Write-Output "=== Ove Launcher ==="

# Stop any existing instances
& "$root\stop.ps1"
Start-Sleep -Seconds 2

# Start GSV voice server
$gsvDir = "$root\..\voice\GPT-SoVITS-v3lora-20250228"
$gsvJob = Start-Job -Name "ove-gsv" -ScriptBlock {
    Set-Location $using:gsvDir
    & "$using:gsvDir\runtime\python.exe" "$using:gsvDir\gsv_infer_server.py" *> "$env:TEMP\ove-gsv.log"
}
Write-Output "GSV started (job $($gsvJob.Id))"

# Start TTS bridge
$ttsJob = Start-Job -Name "ove-tts" -ScriptBlock {
    Set-Location $using:root
    & "C:\Users\zj199\AppData\Local\Programs\Python\Python310\python.exe" "$using:root\tts_bridge.py" *> "$env:TEMP\ove-tts.log"
}
Write-Output "TTS started (job $($ttsJob.Id))"

# Wait for services to be ready
Write-Output "Waiting for services..."
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    $tts = netstat -ano | Select-String "LISTENING.*18777"
    $gsv = netstat -ano | Select-String "LISTENING.*9881"
    if ($tts -and $gsv) {
        Write-Output "All services ready!"
        $ready = $true
        break
    }
}
if (-not $ready) {
    Write-Output "WARNING: Timeout waiting for services"
}

# Start Godot
$gp = Start-Process -FilePath "$root\Godot_v4.4-stable_win64.exe" `
    -ArgumentList "--rendering-driver opengl3", "--path", $root `
    -WorkingDirectory $root -PassThru
Write-Output "Godot started (PID $($gp.Id))"

# Keep running to maintain jobs
Write-Output "Ove running. Press Ctrl+C to stop."
try {
    while ($true) {
        Start-Sleep -Seconds 10
        # Check if Godot is still running
        if ($gp.HasExited) {
            Write-Output "Godot exited, stopping services..."
            break
        }
    }
} finally {
    Stop-Job -Name "ove-gsv", "ove-tts" -ErrorAction SilentlyContinue
    Remove-Job -Name "ove-gsv", "ove-tts" -Force -ErrorAction SilentlyContinue
    Write-Output "Ove stopped."
}
