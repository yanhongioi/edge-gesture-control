# Start the three Windows-side processes used by the demo.
# Run from any directory: powershell -ExecutionPolicy Bypass -File .\scripts\start_pc.ps1

$ErrorActionPreference = "Stop"

# ==============================================================================
# Network settings -- edit these values when the board or PC network changes.
# ==============================================================================
$BoardIp = "172.20.10.4"       # i.MX93 board: audio_stream.py TCP server
$BrokerIp = "127.0.0.1"        # This PC: address used by the two PC clients

# Usually these do not need to change.
$MosquittoExe = "C:\Program Files\mosquitto\mosquitto.exe"
$PythonLauncher = "py"
$BrokerPort = 1883

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$MosquittoConfig = Join-Path $RepoRoot "pc\mosquitto.conf"
$GestureControl = Join-Path $RepoRoot "pc\gesture_control.py"
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

function Start-DemoWindow {
    param(
        [string]$Title,
        [string]$Command
    )

    $titleLiteral = Quote-PowerShellLiteral $Title
    $rootLiteral = Quote-PowerShellLiteral $RepoRoot
    $windowScript = @"
`$Host.UI.RawUI.WindowTitle = $titleLiteral
Set-Location -LiteralPath $rootLiteral
`$ErrorActionPreference = 'Stop'
$Command
if (`$LASTEXITCODE -ne 0) {
    Write-Host "`nProcess exited with code `$LASTEXITCODE" -ForegroundColor Red
}
"@
    $encoded = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($windowScript)
    )
    Start-Process -FilePath $ShellExe -WorkingDirectory $RepoRoot `
        -ArgumentList @("-NoExit", "-EncodedCommand", $encoded)
}

function Test-TcpPort {
    param([string]$HostName, [int]$Port)

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(200)) {
            return $false
        }
        $client.EndConnect($pending)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

Assert-IPv4Address "BoardIp" $BoardIp
Assert-IPv4Address "BrokerIp" $BrokerIp

foreach ($requiredFile in @($MosquittoExe, $MosquittoConfig, $GestureControl)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required file not found: $requiredFile"
    }
}

$python = Get-Command $PythonLauncher -ErrorAction SilentlyContinue
if ($null -eq $python) {
    throw "Python launcher '$PythonLauncher' was not found."
}

$mosquittoCommand = "& $(Quote-PowerShellLiteral $MosquittoExe) -v -c $(Quote-PowerShellLiteral $MosquittoConfig)"
$gestureCommand = "& $(Quote-PowerShellLiteral $python.Source) " +
                  "$(Quote-PowerShellLiteral $GestureControl) --broker $(Quote-PowerShellLiteral $BrokerIp) --port $BrokerPort"
$voiceCommand = "& $(Quote-PowerShellLiteral $python.Source) -m pc.voice_app " +
                "--board-audio $(Quote-PowerShellLiteral $BoardIp) " +
                "--mqtt-wake $(Quote-PowerShellLiteral $BrokerIp) --execute"

if (Test-TcpPort $BrokerIp $BrokerPort) {
    Write-Host "MQTT broker is already listening on ${BrokerIp}:$BrokerPort; reusing it." -ForegroundColor Yellow
}
else {
    Write-Host "Starting MQTT broker..."
    Start-DemoWindow "Edge AI - MQTT Broker" $mosquittoCommand

    $brokerReady = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        if (Test-TcpPort $BrokerIp $BrokerPort) {
            $brokerReady = $true
            break
        }
    }
    if (-not $brokerReady) {
        throw "MQTT broker did not listen on ${BrokerIp}:$BrokerPort within 10 seconds. Check its window for errors."
    }
}

Write-Host "Starting gesture control..."
Start-DemoWindow "Edge AI - Gesture Control" $gestureCommand

Write-Host "Starting voice assistant in execute mode..."
Start-DemoWindow "Edge AI - Voice Assistant" $voiceCommand

Write-Host ""
Write-Host "PC-side services started:" -ForegroundColor Green
Write-Host "  MQTT broker   : ${BrokerIp}:$BrokerPort"
Write-Host "  Gesture input : edge/hand"
Write-Host "  Voice input   : board $BoardIp (--execute enabled)"
Write-Host "  Python command: $PythonLauncher"
Write-Host "Press Ctrl+C in an individual window to stop that process."
