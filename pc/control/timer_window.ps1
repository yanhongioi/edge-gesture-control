param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 86400)]
    [int]$Seconds,

    [Parameter(Mandatory = $true)]
    [ValidateLength(1, 80)]
    [string]$Label
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$alarmPath = Join-Path $PSScriptRoot "assets\timer_sound.mp3"
$alarmPlayer = $null
try {
    Add-Type -AssemblyName PresentationCore
    $alarmPlayer = New-Object System.Windows.Media.MediaPlayer
    $alarmPlayer.Volume = 1.0
}
catch {
    # PresentationCore 不可用時，倒數結束仍會播放 Windows 系統提示音。
    $alarmPlayer = $null
}

$windowTitle = -join @(
    [char]0x8A08, [char]0x6642, [char]0x5668
)
$cancelText = -join @(
    [char]0x53D6, [char]0x6D88, [char]0x8A08, [char]0x6642
)
$expiredText = -join @(
    [char]0x6642, [char]0x9593, [char]0x5230, [char]0x4E86, [char]0xFF01
)
$closeText = -join @([char]0x95DC, [char]0x9589)

$form = New-Object System.Windows.Forms.Form
$form.Text = $windowTitle
$form.Size = [System.Drawing.Size]::new(380, 190)
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.TopMost = $true
$form.BackColor = [System.Drawing.Color]::FromArgb(23, 25, 31)
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual

$workingArea = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$form.Location = [System.Drawing.Point]::new(
    ($workingArea.Right - $form.Width - 24),
    ($workingArea.Top + 40)
)

$nameLabel = New-Object System.Windows.Forms.Label
$nameLabel.Text = $Label
$nameLabel.ForeColor = [System.Drawing.Color]::FromArgb(242, 242, 242)
$nameLabel.Font = [System.Drawing.Font]::new("Microsoft JhengHei UI", 12)
$nameLabel.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
$nameLabel.Location = [System.Drawing.Point]::new(20, 12)
$nameLabel.Size = [System.Drawing.Size]::new(325, 30)
$form.Controls.Add($nameLabel)

$timeLabel = New-Object System.Windows.Forms.Label
$timeLabel.ForeColor = [System.Drawing.Color]::FromArgb(112, 214, 255)
$timeLabel.Font = [System.Drawing.Font]::new("Segoe UI", 30, [System.Drawing.FontStyle]::Bold)
$timeLabel.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
$timeLabel.Location = [System.Drawing.Point]::new(20, 42)
$timeLabel.Size = [System.Drawing.Size]::new(325, 58)
$form.Controls.Add($timeLabel)

$button = New-Object System.Windows.Forms.Button
$button.Text = $cancelText
$button.Font = [System.Drawing.Font]::new("Microsoft JhengHei UI", 10)
$button.UseVisualStyleBackColor = $false
$button.BackColor = [System.Drawing.Color]::White
$button.ForeColor = [System.Drawing.Color]::FromArgb(23, 25, 31)
$button.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(210, 210, 210)
$button.Location = [System.Drawing.Point]::new(126, 108)
$button.Size = [System.Drawing.Size]::new(112, 32)
$button.Add_Click({ $form.Close() })
$form.Controls.Add($button)

$deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
$state = @{
    Expired = $false
    Closed = $false
    FallbackPlayed = $false
}

function Start-FallbackAlarm {
    if ($state.FallbackPlayed) {
        return
    }
    $state.FallbackPlayed = $true
    1..3 | ForEach-Object {
        [System.Media.SystemSounds]::Exclamation.Play()
        Start-Sleep -Milliseconds 250
    }
}

if ($null -ne $alarmPlayer) {
    $alarmPlayer.Add_MediaEnded({
        if ($state.Expired -and -not $state.Closed) {
            $alarmPlayer.Position = [TimeSpan]::Zero
            $alarmPlayer.Play()
        }
    })
    $alarmPlayer.Add_MediaFailed({
        $alarmPlayer.Stop()
        Start-FallbackAlarm
    })
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 100
$timer.Add_Tick({
    $remaining = [Math]::Max(0, [Math]::Ceiling(($deadline - [DateTime]::UtcNow).TotalSeconds))
    if ($remaining -ge 3600) {
        $hours = [Math]::Floor($remaining / 3600)
        $minutes = [Math]::Floor(($remaining % 3600) / 60)
        $secs = $remaining % 60
        $timeLabel.Text = "{0:00}:{1:00}:{2:00}" -f $hours, $minutes, $secs
    }
    else {
        $minutes = [Math]::Floor($remaining / 60)
        $secs = $remaining % 60
        $timeLabel.Text = "{0:00}:{1:00}" -f $minutes, $secs
    }

    if ($remaining -eq 0 -and -not $state.Expired) {
        $state.Expired = $true
        $timer.Stop()
        $timeLabel.Text = $expiredText
        $timeLabel.Font = [System.Drawing.Font]::new(
            "Microsoft JhengHei UI", 23, [System.Drawing.FontStyle]::Bold
        )
        $timeLabel.ForeColor = [System.Drawing.Color]::FromArgb(255, 207, 112)
        $button.Text = $closeText
        $form.Activate()
        if ($null -ne $alarmPlayer -and (Test-Path -LiteralPath $alarmPath -PathType Leaf)) {
            try {
                $alarmPlayer.Open([System.Uri]::new($alarmPath))
                $alarmPlayer.Position = [TimeSpan]::Zero
                $alarmPlayer.Play()
            }
            catch {
                Start-FallbackAlarm
            }
        }
        else {
            Start-FallbackAlarm
        }
    }
})

$form.Add_Shown({
    $timer.Start()
    $form.Activate()
})
$form.Add_FormClosed({
    $state.Closed = $true
    $timer.Stop()
    if ($null -ne $alarmPlayer) {
        $alarmPlayer.Stop()
        $alarmPlayer.Close()
    }
})
[System.Windows.Forms.Application]::Run($form)
