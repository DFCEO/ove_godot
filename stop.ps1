# stop.ps1 — Kill all Ove-related processes
Write-Output "Stopping Ove..."

Get-Process "Godot_v4.4-stable_win64" -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force
    Write-Output "  Killed Godot (PID $($_.Id))"
}

$services = @("ove_core", "ove_bridge", "stt_service", "tts_bridge", "gsv_infer_server", "voice_responder", "ove_voice_agent")
foreach ($svc in $services) {
    Get-WmiObject Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match $svc } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force
        Write-Output "  Killed $svc (PID $($_.ProcessId))"
    }
}

Start-Sleep -Seconds 2
Write-Output "Ove stopped."
