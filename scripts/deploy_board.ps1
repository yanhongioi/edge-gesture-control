# Copy board/ to the FRDM-i.MX93 over SSH.
# Usage: .\scripts\deploy_board.ps1 [-BoardIp 192.168.7.2] [-Dest /root/edge-gesture-control]
# Default IP is the USB direct link (board/usb_net.sh). Ethernet: 192.168.10.2
param(
    [string]$BoardIp = "192.168.7.2",
    [string]$Dest = "/root/edge-gesture-control"
)
$ErrorActionPreference = "Stop"
$board = Join-Path $PSScriptRoot "..\board"

ssh "root@$BoardIp" "mkdir -p $Dest"
if ($LASTEXITCODE -ne 0) { throw "ssh failed (is the board at $BoardIp reachable?)" }
scp -r "$board" "root@${BoardIp}:$Dest/"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }

Write-Host "Deployed to ${BoardIp}:$Dest/board"
Write-Host "On the board: cd $Dest/board ; python3 hand_cam.py"
