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

    So the answer is not to wake it. It is to leave it running, and that is
    what this script arranges:

      * plugged in, it stays awake with the lid open or closed: the lid does
        nothing, it never idles to sleep or hibernate, and the wireless adapter
        stops power-saving;
      * on battery, it stays awake while the lid is open -- it never idles to
        sleep or hibernate -- and closing the lid puts it to sleep. Set
        explicitly rather than left to whatever the laptop had, because a
        laptop that stays awake shut in a bag is a fire risk. Windows' own
        low-battery action still applies, so it does not run itself flat;
      * a scheduled task starts the app again at logon, because Windows Update
        will restart this machine sooner or later and an app that is not
        running posts nothing.

    Every change is recorded first and `-Revert` puts it all back. The user
    chose the battery half on 2026-09-28; before that the script was
    mains-only and left battery settings alone.

.PARAMETER Revert
    Undo everything: restore the recorded power settings and remove the task.
    With -SkipPower it only removes the task; with -SkipTask it only restores
    the power settings. That is how the app's Settings screen turns each one
    off on its own.

.PARAMETER TaskName
    Name of the scheduled task. Only change this if it collides with something.

.PARAMETER SkipTask
    Change the power settings but do not register the logon task.

.PARAMETER SkipPower
    Register the logon task but leave every power setting alone. This is what
    the installer passes when the client asked to start the app at logon but
    not to keep the machine awake -- a laptop that never sleeps is a decision
    they have to make deliberately.

.NOTES
    Exits 1 when anything it was asked to do did not happen, so a caller can
    tell without reading the output. The installer ignores the exit code; the
    app's Settings screen does not.

.PARAMETER AppPath
    Full path to the packaged FacebookAutoPoster.exe. Supplied by the installer,
    where there is no source checkout and no .venv to find: without it the task
    is built around pythonw.exe and main.py, which is right for a developer
    machine and wrong for a client's.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_always_on.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_always_on.ps1 -Revert
#>
[CmdletBinding()]
param(
    [switch]$Revert,
    [switch]$SkipTask,
    [switch]$SkipPower,
    [string]$AppPath = "",
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

# One row per value changed: the setting, which power source, and the value.
# 0 for the timeouts means "never". LIDACTION: 0 is "Do nothing", 1 is
# "Sleep". POWERSAVEMODE 0 is "Maximum Performance"; on battery it is left
# alone, since it decides nothing about sleeping.
$Wanted = @(
    @{ Name = "Lid close action, plugged in";   Sub = $SUB_BUTTONS;  Setting = $LIDACTION;     Source = "AC"; Value = 0; Required = $true  },
    @{ Name = "Sleep after, plugged in";        Sub = $SUB_SLEEP;    Setting = $STANDBYIDLE;   Source = "AC"; Value = 0; Required = $true  },
    @{ Name = "Hibernate after, plugged in";    Sub = $SUB_SLEEP;    Setting = $HIBERNATEIDLE; Source = "AC"; Value = 0; Required = $true  },
    @{ Name = "Wi-Fi power saving, plugged in"; Sub = $SUB_WIRELESS; Setting = $POWERSAVEMODE; Source = "AC"; Value = 0; Required = $false },
    @{ Name = "Lid close action, on battery";   Sub = $SUB_BUTTONS;  Setting = $LIDACTION;     Source = "DC"; Value = 1; Required = $true  },
    @{ Name = "Sleep after, on battery";        Sub = $SUB_SLEEP;    Setting = $STANDBYIDLE;   Source = "DC"; Value = 0; Required = $true  },
    @{ Name = "Hibernate after, on battery";    Sub = $SUB_SLEEP;    Setting = $HIBERNATEIDLE; Source = "DC"; Value = 0; Required = $true  }
)

# --- helpers ----------------------------------------------------------------

function Test-Elevated {
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-PowerValue {
    <#
      The AC or DC index for one setting, as an integer, or $null.

      powercfg's labels are localised -- this machine's Windows may well not be
      in English -- so the text is never matched. The hex values are not
      localised, and powercfg always prints the AC index before the DC one, so
      the last two 0x........ in the block are AC then DC.

      /qh first, not /query. /query leaves out any setting Windows has marked
      hidden, and on many laptops the lid close action is one: it printed the
      scheme header and nothing else, exit code 0. That read as "this machine
      has no lid", so the one setting that matters most was skipped and the
      laptop went on sleeping with the lid shut. Found on 2026-09-28 on a
      notebook whose lid action was hidden and set to Sleep on AC. /query is
      kept as a fallback for a build that does not know /qh.
    #>
    param([string]$Sub, [string]$Setting, [string]$Source = "AC")
    foreach ($verb in @("/qh", "/query")) {
        try {
            $output = & powercfg $verb SCHEME_CURRENT $Sub $Setting 2>$null
            if ($LASTEXITCODE -ne 0) { continue }
            $hex = [regex]::Matches(($output -join "`n"), '0x[0-9a-fA-F]{8}')
            # The block opens with the scheme, subgroup and setting GUIDs, then
            # the possible values, then the two indices. The last two are AC
            # then DC.
            if ($hex.Count -ge 2) {
                $index = if ($Source -eq "DC") { $hex.Count - 1 } else { $hex.Count - 2 }
                return [Convert]::ToInt32($hex[$index].Value, 16)
            }
        } catch {
            continue
        }
    }
    return $null
}

function Set-PowerValue {
    param([string]$Sub, [string]$Setting, [string]$Source, [int]$Value)
    $verb = if ($Source -eq "DC") { "/setdcvalueindex" } else { "/setacvalueindex" }
    & powercfg $verb SCHEME_CURRENT $Sub $Setting $Value 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Get-BackupKey {
    <#
      Where one row's original value is kept in the backup file. AC rows use
      the bare setting GUID, which is all the file held before the battery
      half existed -- so a backup written by an older copy of this script
      still restores correctly.
    #>
    param($Item)
    if ($Item.Source -eq "DC") { return "$($Item.Setting):dc" }
    return $Item.Setting
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
    $failed = 0

    if ($SkipPower) {
        Write-Step "Leaving the power settings as they are."
    } elseif (Test-Path $BackupFile) {
        $backup = Get-Content $BackupFile -Raw | ConvertFrom-Json
        foreach ($item in $Wanted) {
            $saved = $backup.PSObject.Properties[(Get-BackupKey $item)]
            if ($null -eq $saved -or $null -eq $saved.Value) {
                Write-Warn "$($item.Name): nothing recorded, left as it is"
                continue
            }
            if (Set-PowerValue $item.Sub $item.Setting $item.Source ([int]$saved.Value)) {
                Write-Good "$($item.Name): restored to $($saved.Value)"
            } else {
                Write-Bad "$($item.Name): could not be restored"
                $failed++
            }
        }
        & powercfg /setactive SCHEME_CURRENT | Out-Null
        # Kept when a setting could not be put back, so a second attempt still
        # knows what to restore it to.
        if ($failed -eq 0) { Remove-Item $BackupFile -Force }
    } else {
        Write-Warn "No backup file at $BackupFile -- power settings left alone."
        Write-Step "Change them by hand in Settings > System > Power if needed."
    }

    if ($SkipTask) {
        Write-Step "Leaving the logon task as it is."
    } else {
        $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($null -ne $existing) {
            try {
                Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
                Write-Good "Removed the '$TaskName' logon task."
            } catch {
                Write-Bad "Could not remove the task: $($_.Exception.Message)"
                $failed++
            }
        } else {
            Write-Step "No '$TaskName' logon task to remove."
        }
    }

    if ($failed -gt 0) {
        Write-Host "`nFinished with $failed problem(s) above.`n" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "`nDone.`n" -ForegroundColor Cyan
    exit 0
}

# --- apply ------------------------------------------------------------------

# Counted across both halves; the exit code at the end is read from it.
$failed = 0

if ($SkipPower) {
    Write-Host "`nLeaving every power setting exactly as it is." -ForegroundColor Cyan
} else {

Write-Host "`nSetting this machine up to keep posting." -ForegroundColor Cyan
Write-Host "Plugged in: stays awake, lid open or closed."
Write-Host "On battery: stays awake while the lid is open, sleeps when it is closed.`n"

if (-not (Test-Elevated)) {
    Write-Warn "Not running as Administrator. Power settings usually still apply;"
    Write-Step "if any come back [fail] below, re-run this from an admin PowerShell."
    Write-Host ""
}

# Record what is there now, before touching any of it. Written before the first
# change rather than after the last, so an interrupted run is still revertible.
#
# A value already recorded is never overwritten: it is the one from before the
# first run, and the current value may be ours. Anything not yet recorded is
# added -- which is how a backup written by the mains-only version gains the
# battery values before they are changed. A recorded $null is treated as not
# recorded: the old /query reader wrote $null for a hidden lid setting it had
# skipped, and a setting is only ever changed after a real value is on file.
if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}
$backup = @{}
if (Test-Path $BackupFile) {
    $existing = Get-Content $BackupFile -Raw | ConvertFrom-Json
    foreach ($property in $existing.PSObject.Properties) {
        $backup[$property.Name] = $property.Value
    }
}
$recorded = 0
foreach ($item in $Wanted) {
    $key = Get-BackupKey $item
    if ($backup.ContainsKey($key) -and $null -ne $backup[$key]) { continue }
    $backup[$key] = Get-PowerValue $item.Sub $item.Setting $item.Source
    $recorded++
}
if ($recorded -gt 0) {
    $backup | ConvertTo-Json | Out-File -FilePath $BackupFile -Encoding utf8
    Write-Good "Recorded the current settings in $BackupFile"
} else {
    Write-Step "Keeping the existing backup at $BackupFile"
}
Write-Host ""

Write-Host "Power plan:"
foreach ($item in $Wanted) {
    # Presence first. powercfg returns 0 for a setting this machine does not
    # have, so acting on the exit code alone reports success for a change that
    # never happened -- a desktop has no lid, and plenty of machines have no
    # wireless adapter subgroup.
    if ($null -eq (Get-PowerValue $item.Sub $item.Setting $item.Source)) {
        Write-Step "$($item.Name): not present on this machine, skipped"
        continue
    }
    if (Set-PowerValue $item.Sub $item.Setting $item.Source $item.Value) {
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
    $actual = Get-PowerValue $item.Sub $item.Setting $item.Source
    if ($null -eq $actual) {
        Write-Step "$($item.Name): not present, nothing to check"
    } elseif ($actual -eq $item.Value) {
        Write-Good "$($item.Name) is $actual"
    } else {
        Write-Bad "$($item.Name) is $actual, wanted $($item.Value)"
        $failed++
    }
}

}  # end of the power half

# --- the logon task ---------------------------------------------------------

if (-not $SkipTask) {
    Write-Host "`nStart the app at logon:"
    # A packaged install has no .venv and no main.py; the installer hands over
    # the .exe instead. Falling back to Python keeps this script working
    # unchanged on a developer checkout.
    if ($AppPath -ne "") {
        if (-not (Test-Path $AppPath)) {
            Write-Bad "No such application: $AppPath"
            $failed++
            $python = $null
        } else {
            $python = $AppPath
        }
    } else {
        $python = Find-Python
    }
    if ($null -eq $python) {
        if ($AppPath -eq "") {
            Write-Bad "No .venv found under $RepoPath -- create it first:"
            Write-Step "python -m venv .venv"
            Write-Step ".\.venv\Scripts\python.exe -m pip install -r requirements.txt"
            $failed++
        }
    } else {
        if ($AppPath -ne "") {
            # The packaged .exe opens the window and starts Chrome itself, so
            # it takes no arguments at all.
            $action = New-ScheduledTaskAction -Execute $python `
                -WorkingDirectory (Split-Path -Parent $python)
            $runs = $python
        } else {
            $mainPy = Join-Path $RepoPath "main.py"
            $action = New-ScheduledTaskAction -Execute $python `
                -Argument "`"$mainPy`" start" -WorkingDirectory $RepoPath
            $runs = "$python `"$mainPy`" start"
        }

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
            Write-Step "Runs: $runs"
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

  1. Keep it plugged in if the lid will be shut. On battery, closing the lid
     puts it to sleep on purpose, and a missed slot is reported, not
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

if ($failed -gt 0) { exit 1 }
exit 0
