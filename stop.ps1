# stop.ps1 — Kill all Ove-related processes
Write-Output "Stopping Ove..."

# 1. Kill Godot first
Get-Process "Godot_v4.4-stable_win64" -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force
    Write-Output "  Killed Godot (PID $($_.Id))"
}

# 2. Kill Python processes by matching port or script name
$knownPorts = @(18776, 18777, 18778, 18779, 9881)
$knownScripts = @("ove_core", "ove_voice_agent", "tts_bridge", "gsv_infer_server")

Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    $pid = $_.Id
    $shouldKill = $false
    
    # Check if listening on known ports
    $conn = netstat -ano -p TCP 2>$null | Select-String ":9881|:1877[6-9]" | Select-String "LISTENING"
    if ($conn -match $pid) { $shouldKill = $true }
    
    # If not found by port, try command line match
    if (-not $shouldKill) {
        try {
            $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$pid" -ErrorAction Stop).CommandLine
            foreach ($script in $knownScripts) {
                if ($cmd -match $script) { $shouldKill = $true; break }
            }
        } catch { }
    }
    
    if ($shouldKill) {
        Stop-Process -Id $pid -Force
        Write-Output "  Killed python PID $pid"
    }
}

# 3. Wait for ports to release
Start-Sleep -Seconds 3
Write-Output "Ove stopped."
