# Start the three board-side services in separate interactive SSH windows.
# Run from any directory: powershell -ExecutionPolicy Bypass -File .\scripts\start_board.ps1

$ErrorActionPreference = "Stop"

# ==============================================================================
# Network settings -- normally only these two IP addresses need to be changed.
# ==============================================================================
$BoardIp = "192.168.7.2"       # i.MX93 board SSH address
$PcIp = "192.168.7.1"          # PC address that receives MQTT from the board

# Board settings.
$SshUser = "root"
$SshPort = 22
$BoardRepo = "/root/edge-gesture-control"
$MqttHz = 30
$AudioStartupDelaySeconds = 3

$ShellExe = (Get-Process -Id $PID).Path

function Assert-IPv4Address {
    param([string]$Name, [string]$Value)

    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($Value, [ref]$parsed) -or
        $parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        throw "$Name is not a valid IPv4 address: $Value"
    }
}

function Quote-PowerShellLiteral {
    param([string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

function Start-BoardSshWindow {
    param(
        [string]$Title,
        [string]$RemoteCommand
    )

    $titleLiteral = Quote-PowerShellLiteral $Title
    $sshLiteral = Quote-PowerShellLiteral $SshExe
    $targetLiteral = Quote-PowerShellLiteral "${SshUser}@${BoardIp}"
    $remoteLiteral = Quote-PowerShellLiteral $RemoteCommand
    $windowScript = @"
`$Host.UI.RawUI.WindowTitle = $titleLiteral
Write-Host 'SSH target: ${SshUser}@${BoardIp}' -ForegroundColor Cyan
Write-Host 'If SSH asks for a password, enter the board root password.' -ForegroundColor Yellow
& $sshLiteral -tt -p $SshPort -o ServerAliveInterval=15 -o ServerAliveCountMax=3 $targetLiteral $remoteLiteral
if (`$LASTEXITCODE -ne 0) {
    Write-Host "`nSSH process exited with code `$LASTEXITCODE" -ForegroundColor Red
}
"@
    $encoded = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($windowScript)
    )
    Start-Process -FilePath $ShellExe `
        -ArgumentList @("-NoExit", "-EncodedCommand", $encoded)
}

Assert-IPv4Address "BoardIp" $BoardIp
Assert-IPv4Address "PcIp" $PcIp

if ($SshUser -notmatch "^[a-zA-Z0-9_-]+$") {
    throw "SshUser contains unsupported characters: $SshUser"
}
if ($BoardRepo -notmatch "^/[a-zA-Z0-9_./-]+$") {
    throw "BoardRepo contains unsupported characters: $BoardRepo"
}
if ($MqttHz -le 0) {
    throw "MqttHz must be greater than zero."
}

$ssh = Get-Command "ssh" -ErrorAction SilentlyContinue
if ($null -eq $ssh) {
    throw "Windows OpenSSH client was not found. Enable OpenSSH Client in Windows Optional Features."
}
$SshExe = $ssh.Source

$handCommand = "cd $BoardRepo/board && exec python3 hand_cam.py --mqtt $PcIp --mqtt-hz $MqttHz"
$voiceCommand = "export MQTT_HOST=$PcIp; exec sh $BoardRepo/board/voice/run_voice.sh"
$audioCommand = "sleep $AudioStartupDelaySeconds; exec python3 $BoardRepo/board/audio_stream.py --device c270"

Write-Host "Opening board hand/camera SSH window..."
Start-BoardSshWindow "Edge Board - Hand Camera" $handCommand

Write-Host "Opening board VIT wake-word SSH window..."
Start-BoardSshWindow "Edge Board - Voice Wake" $voiceCommand

Start-Sleep -Milliseconds 500
Write-Host "Opening board audio-stream SSH window..."
Start-BoardSshWindow "Edge Board - Audio Stream" $audioCommand

Write-Host ""
Write-Host "Board-side SSH windows opened:" -ForegroundColor Green
Write-Host "  SSH board       : ${SshUser}@${BoardIp}:$SshPort"
Write-Host "  MQTT target PC  : ${PcIp}:1883"
Write-Host "  Hand/camera     : hand_cam.py -> edge/hand"
Write-Host "  Wake word       : run_voice.sh -> edge/voice"
Write-Host "  Audio stream    : audio_stream.py -> TCP 0.0.0.0:8765"
Write-Host "If prompted, accept the host key and enter the board root password in each window."
Write-Host "Press Ctrl+C in an individual window to stop that board service."
