<#
.SYNOPSIS
    Set a laptop up to post on schedule with the lid closed.

.DESCRIPTION
    The app can hold off *idle* sleep while a batch is going out, and it does.
    What it cannot do is anything about a closed lid: SetThreadExecutionState
    suppresses the idle timer and nothing else, so shutting the laptop suspends
    the machine straight through it. Nor can a suspended machine be woken for a
    post -- Task Scheduler's wake timers reach a sleeping machine, never a shut
    down one, and the client here shuts theirs.

    So the answer is not to wake it. It is to leave it running with the lid
    shut, and that is what this script arranges:

      * on mains only, the lid does nothing, the machine never idles to sleep
        or hibernate, and the wireless adapter stops power-saving;
      * on battery, everything is left exactly as Windows had it, deliberately
        -- a laptop that refuses to sleep in a bag is a fire risk and a flat
        battery, and the app reports a missed slot rather than posting late;
      * a scheduled task starts the app again at logon, because Windows Update
        will restart this machine sooner or later and an app that is not
        running posts nothing.

    Every change is recorded first and `-Revert` puts it all back.

.PARAMETER Revert
    Undo everything: restore the recorded power settings and remove the task.

.PARAMETER TaskName
    Name of the scheduled task. Only change this if it collides with something.

.PARAMETER SkipTask
    Change the power settings but do not register the logon task.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_always_on.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_always_on.ps1 -Revert
#>
[CmdletBinding()]
param(
    [switch]$Revert,
    [switch]$SkipTask,
    [string]$TaskName = "FacebookLocalAutoPoster"
)

$ErrorActionPreference = "Stop"

$RepoPath   = Split-Path -Parent $PSScriptRoot
$BackupDir  = Join-Path $env:LOCALAPPDATA "FBAutomation"
$BackupFile = Join-Path $BackupDir "power-backup.json"

# Raw GUIDs rather than powercfg's short aliases: the aliases are not present
# on every build, and a typo in one silently does nothing.
$SUB_BUTTONS       = "4f971e89-eebd-4455-a8de-9e59040e7347"
$LIDACTION         = "5ca83367-6e45-459f-a27b-476b1d01c936"
$SUB_SLEEP         = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
$STANDBYIDLE       = "29f6c1db-86da-48c5-9fdb-f2b67b1f44da"
$HIBERNATEIDLE     = "9d7815a6-7ee4-497e-8888-515a05f02364"
$SUB_WIRELESS      = "19cbb8fa-5279-450e-9fac-8a3d5fedd0c1"
$POWERSAVEMODE     = "12bbebe6-58d6-4636-95bb-3217ef867c1a"

# What we want on mains. 0 for the timeouts means "never"; 0 for LIDACTION is
# "Do nothing"; 0 for POWERSAVEMODE is "Maximum Performance".
$Wanted = @(
    @{ Name = "Lid close action";      Sub = $SUB_BUTTONS;  Setting = $LIDACTION;     Value = 0; Required = $true  },
    @{ Name = "Sleep after";           Sub = $SUB_SLEEP;    Setting = $STANDBYIDLE;   Value = 0; Required = $true  },
    @{ Name = "Hibernate after";       Sub = $SUB_SLEEP;    Setting = $HIBERNATEIDLE; Value = 0; Required = $true  },
    @{ Name = "Wi-Fi power saving";    Sub = $SUB_WIRELESS; Setting = $POWERSAVEMODE; Value = 0; Required = $false }
)

# --- helpers ----------------------------------------------------------------

function Test-Elevated {
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-AcValue {
    <#
      The AC index for one setting, as an integer, or $null.

      powercfg's labels are localised -- this machine's Windows may well not be
      in English -- so the text is never matched. The hex values are not
      localised, and powercfg always prints the AC index before the DC one, so
      the first 0x........ in the block is the answer.
    #>
    param([string]$Sub, [string]$Setting)
    try {
        $output = & powercfg /query SCHEME_CURRENT $Sub $Setting 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $hex = [regex]::Matches(($output -join "`n"), '0x[0-9a-fA-F]{8}')
        # The block opens with the scheme, subgroup and setting GUIDs, then the
        # possible values, then the two indices. The last two are AC then DC.
        if ($hex.Count -lt 2) { return $null }
        return [Convert]::ToInt32($hex[$hex.Count - 2].Value, 16)
    } catch {
        return $null
    }
}

function Set-AcValue {
    param([string]$Sub, [string]$Setting, [int]$Value)
    & powercfg /setacvalueindex SCHEME_CURRENT $Sub $Setting $Value 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Write-Step   { param([string]$m) Write-Host "  $m" }
function Write-Good   { param([string]$m) Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Warn   { param([string]$m) Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Write-Bad    { param([string]$m) Write-Host "  [fail] $m" -ForegroundColor Red }

function Find-Python {
    <#
      pythonw.exe, so the app runs without a console window sitting on the
      desktop for ever. print() is a no-op when stdout is None, so main.py's
      own output simply goes nowhere -- verified, not assumed.
    #>
    $candidates = @(
        (Join-Path $RepoPath ".venv\Scripts\pythonw.exe"),
        (Join-Path $RepoPath ".venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

# --- revert -----------------------------------------------------------------

if ($Revert) {
    Write-Host "`nUndoing the always-on setup." -ForegroundColor Cyan

    if (Test-Path $BackupFile) {
        $backup = Get-Content $BackupFile -Raw | ConvertFrom-Json
        foreach ($item in $Wanted) {
            $saved = $backup.PSObject.Properties[$item.Setting]
            if ($null -eq $saved -or $null -eq $saved.Value) {
                Write-Warn "$($item.Name): nothing recorded, left as it is"
                continue
            }
            if (Set-AcValue $item.Sub $item.Setting ([int]$saved.Value)) {
                Write-Good "$($item.Name): restored to $($saved.Value)"
            } else {
                Write-Bad "$($item.Name): could not be restored"
            }
        }
        & powercfg /setactive SCHEME_CURRENT | Out-Null
        Remove-Item $BackupFile -Force
    } else {
        Write-Warn "No backup file at $BackupFile -- power settings left alone."
        Write-Step "Change them by hand in Settings > System > Power if needed."
    }

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Good "Removed the '$TaskName' logon task."
    } else {
        Write-Step "No '$TaskName' logon task to remove."
    }

    Write-Host "`nDone. The laptop sleeps normally again.`n" -ForegroundColor Cyan
    exit 0
}

# --- apply ------------------------------------------------------------------

Write-Host "`nSetting this machine up to post with the lid closed." -ForegroundColor Cyan
Write-Host "Mains power only -- on battery nothing changes.`n"

if (-not (Test-Elevated)) {
    Write-Warn "Not running as Administrator. Power settings usually still apply;"
    Write-Step "if any come back [fail] below, re-run this from an admin PowerShell."
    Write-Host ""
}

# Record what is there now, before touching any of it. Written before the first
# change rather than after the last, so an interrupted run is still revertible.
if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}
if (-not (Test-Path $BackupFile)) {
    $backup = @{}
    foreach ($item in $Wanted) {
        $backup[$item.Setting] = Get-AcValue $item.Sub $item.Setting
    }
    $backup | ConvertTo-Json | Out-File -FilePath $BackupFile -Encoding utf8
    Write-Good "Recorded the current settings in $BackupFile"
} else {
    Write-Step "Keeping the existing backup at $BackupFile"
}
Write-Host ""

Write-Host "Power plan (AC):"
$failed = 0
foreach ($item in $Wanted) {
    # Presence first. powercfg returns 0 for a setting this machine does not
    # have, so acting on the exit code alone reports success for a change that
    # never happened -- a desktop has no lid, and plenty of machines have no
    # wireless adapter subgroup.
    if ($null -eq (Get-AcValue $item.Sub $item.Setting)) {
        Write-Step "$($item.Name): not present on this machine, skipped"
        continue
    }
    if (Set-AcValue $item.Sub $item.Setting $item.Value) {
        Write-Good "$($item.Name) -> $($item.Value)"
    } elseif ($item.Required) {
        Write-Bad "$($item.Name) could not be set"
        $failed++
    } else {
        Write-Warn "$($item.Name) could not be set; carrying on"
    }
}
& powercfg /setactive SCHEME_CURRENT | Out-Null

# Read it back rather than trusting the exit codes. powercfg is quite capable
# of returning 0 for a setting it did not change.
Write-Host "`nVerifying:"
foreach ($item in $Wanted) {
    $actual = Get-AcValue $item.Sub $item.Setting
    if ($null -eq $actual) {
        Write-Step "$($item.Name): not present, nothing to check"
    } elseif ($actual -eq $item.Value) {
        Write-Good "$($item.Name) is $actual"
    } else {
        Write-Bad "$($item.Name) is $actual, wanted $($item.Value)"
        $failed++
    }
}

# --- the logon task ---------------------------------------------------------

if (-not $SkipTask) {
    Write-Host "`nStart the app at logon:"
    $python = Find-Python
    if ($null -eq $python) {
        Write-Bad "No .venv found under $RepoPath -- create it first:"
        Write-Step "python -m venv .venv"
        Write-Step ".\.venv\Scripts\python.exe -m pip install -r requirements.txt"
        $failed++
    } else {
        $mainPy = Join-Path $RepoPath "main.py"
        $action = New-ScheduledTaskAction -Execute $python `
            -Argument "`"$mainPy`" start" -WorkingDirectory $RepoPath

        # A short delay so the desktop and the network are up first; Chrome
        # launched into a half-started session is the one thing that makes
        # main.py start give up on the browser.
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERNAME"
        $trigger.Delay = "PT45S"

        # Three of these settings are load-bearing:
        #   DontStopIfGoingOnBatteries -- the default kills a running task the
        #     moment the charger is pulled, which would be mid-batch;
        #   AllowStartIfOnBatteries -- the default refuses to start it at all;
        #   ExecutionTimeLimit 0 -- the default stops the task after 3 days,
        #     and this app is meant to sit there for months.
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 5)

        # Interactive: this starts a GUI, so it needs the desktop session. It
        # deliberately does not run with highest privileges -- nothing here
        # needs them, and Chrome would inherit them.
        $principal = New-ScheduledTaskPrincipal `
            -UserId "$env:USERDOMAIN\$env:USERNAME" `
            -LogonType Interactive -RunLevel Limited

        try {
            Register-ScheduledTask -TaskName $TaskName -Action $action `
                -Trigger $trigger -Settings $settings -Principal $principal `
                -Description "Starts the Facebook Local Auto-Poster after logon." `
                -Force | Out-Null
            Write-Good "Registered '$TaskName' (45s after logon)."
            Write-Step "Runs: $python `"$mainPy`" start"
        } catch {
            Write-Bad "Could not register the task: $($_.Exception.Message)"
            $failed++
        }
    }
}

# --- what the script cannot do ----------------------------------------------

Write-Host ""
if ($failed -gt 0) {
    Write-Host "Finished with $failed problem(s) above." -ForegroundColor Yellow
} else {
    Write-Host "All set." -ForegroundColor Green
}

Write-Host @"

Still up to you -- none of this can be scripted safely:

  1. Keep it plugged in. Every change above is mains-only on purpose. On
     battery the laptop sleeps as normal and a missed slot is reported, not
     fired late.
  2. Keep it in the open. Lid closed and awake means the fans are the only
     cooling it has, so not in a bag and not in a drawer.
  3. Turn on automatic sign-in (netplwiz, or Settings > Accounts) if the
     machine is ever restarted unattended. The task above fires at *logon*; at
     a locked sign-in screen nothing has logged on and nothing starts.
  4. Set Windows Update active hours to cover the posting window, so a restart
     does not land in the middle of a batch.
  5. Log into Facebook once in the automation profile: main.py setup

To undo everything: scripts\setup_always_on.ps1 -Revert

"@
