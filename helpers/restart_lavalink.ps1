# Restart Lavalink cleanly (kill old instance, start fresh, verify port 2333)
$ErrorActionPreference = 'Stop'
$dir = 'd:\Program\Python_Bot\DiscordBot\lavalink'

# Kill any existing Lavalink process
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*Lavalink.jar*' } | ForEach-Object {
    Write-Host "Stopping old Lavalink (PID $_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

# Start fresh
Write-Host "Starting Lavalink..."
Start-Process -FilePath 'java' -ArgumentList '-jar', "$dir\Lavalink.jar" -WorkingDirectory $dir -RedirectStandardOutput "$dir\logs\stdout.log" -RedirectStandardError "$dir\logs\stderr.log" -WindowStyle Hidden

# Startup can take more than six seconds on a cold JVM. Poll for up to 30
# seconds so a healthy slow start is not reported as a failure.
$listener = $null
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort 2333 -ErrorAction SilentlyContinue
    if ($listener) { break }
    Start-Sleep -Seconds 1
}

# Verify
if ($listener) {
    Write-Host "LAVALINK UP (port 2333, owner PID $($listener.OwningProcess))"
} else {
    Write-Host "LAVALINK FAILED TO START"; exit 1
}
Write-Host "--- last lines of lavalink stdout ---"
Get-Content "$dir\logs\stdout.log" -Tail 5 -ErrorAction SilentlyContinue
