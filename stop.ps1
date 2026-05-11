# stop.ps1 — Kill all Ove-related processes
$root = $PSScriptRoot

Write-Output "Stopping Ove..."

# Kill Godot
Get-Process "Godot_v4.4-stable_win64" -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force
    Write-Output "  Killed Godot (PID $($_.Id))"
}

# Kill tts_bridge.py processes
Get-WmiObject Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'tts_bridge' } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force
    Write-Output "  Killed tts_bridge (PID $($_.ProcessId))"
}

# Kill voice server
Get-WmiObject Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'gsv_infer_server' } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force
    Write-Output "  Killed voice server (PID $($_.ProcessId))"
}

# Wait for ports to release
Start-Sleep -Seconds 2

Write-Output "Ove stopped."
