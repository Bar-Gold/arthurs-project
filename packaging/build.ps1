<#
.SYNOPSIS
    Build the client deliverable: FacebookAutoPoster-Setup-x.y.z.exe

.DESCRIPTION
    Two stages. PyInstaller turns the source tree into dist\FacebookAutoPoster\
    (a folder with the .exe and everything it needs), then Inno Setup wraps
    that folder into a single Setup.exe.

    Stage two is skipped with a clear message if Inno Setup is not installed --
    the folder from stage one is still a working app, so a missing compiler is
    an inconvenience rather than a failed build.

.PARAMETER SkipInstaller
    Build the .exe folder only. Useful while iterating.

.PARAMETER Clean
    Delete build\ and dist\ first. PyInstaller caches aggressively and a stale
    cache is the usual reason a change does not show up in the .exe.

.PARAMETER Force
    Package the bundle even though it failed its own checks. For looking at a
    broken build, never for something a client is going to install.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [switch]$Clean,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$Root    = Split-Path -Parent $PSScriptRoot
$Python  = Join-Path $Root ".venv\Scripts\python.exe"
$Spec    = Join-Path $Root "packaging\fbposter.spec"
$Iss     = Join-Path $Root "packaging\installer.iss"
$DistApp = Join-Path $Root "dist\FacebookAutoPoster"

function Invoke-Native {
    <#
      Windows PowerShell 5.1 wraps every stderr line from a native .exe in an
      ErrorRecord and sets $? to $false, so with $ErrorActionPreference = Stop
      a tool that merely *logs* to stderr -- pytest, pip, PyInstaller all do --
      blows the script up while reporting success. Exit code is the only
      trustworthy signal, so stderr is allowed through untouched and only
      $LASTEXITCODE decides.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Arguments = @(),
        [string]$FailMessage = "Command failed"
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($LASTEXITCODE -ne 0) { throw "$FailMessage (exit $LASTEXITCODE)" }
}

function Write-Head { param([string]$m) Write-Host "`n$m" -ForegroundColor Cyan }
function Write-Good { param([string]$m) Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Step { param([string]$m) Write-Host "  $m" }
function Write-Warn { param([string]$m) Write-Host "  [warn] $m" -ForegroundColor Yellow }

if (-not (Test-Path $Python)) {
    throw "No virtual environment at $Python. Create it first: python -m venv .venv"
}

if ($Clean) {
    Write-Head "Cleaning."
    foreach ($dir in @((Join-Path $Root "build"), (Join-Path $Root "dist"))) {
        if (Test-Path $dir) {
            Remove-Item -Recurse -Force $dir
            Write-Good "Removed $dir"
        }
    }
}

# --- stage 0: the suite must pass before anything is shipped -----------------

Write-Head "Running the tests."
Invoke-Native -Exe $Python -Arguments @("-m", "pytest", (Join-Path $Root "tests"), "-q") `
    -FailMessage "Tests failed. Not building a client release from a red suite."
Write-Good "Suite green."

# --- stage 1: PyInstaller ----------------------------------------------------

Write-Head "Checking the build tool."
$probe = "import importlib.util,sys; sys.stdout.write('yes' if importlib.util.find_spec('PyInstaller') else 'no')"
$present = (& $Python -c $probe) -join ""
if ($present -ne "yes") {
    Write-Step "PyInstaller is not installed in the venv. Installing it."
    Invoke-Native -Exe $Python `
        -Arguments @("-m", "pip", "install", "--disable-pip-version-check", "-q", "pyinstaller") `
        -FailMessage "Could not install PyInstaller"
}
$pyiVersion = (& $Python -c "import PyInstaller; print(PyInstaller.__version__)") -join ""
Write-Good "PyInstaller $pyiVersion"

Write-Head "Building the application folder. This takes a few minutes."
Push-Location $Root
try {
    Invoke-Native -Exe $Python -Arguments @(
        "-m", "PyInstaller", $Spec, "--noconfirm",
        "--distpath", (Join-Path $Root "dist"),
        "--workpath", (Join-Path $Root "build")
    ) -FailMessage "PyInstaller failed"
} finally {
    Pop-Location
}

if (-not (Test-Path (Join-Path $DistApp "FacebookAutoPoster.exe"))) {
    throw "PyInstaller reported success but produced no .exe at $DistApp"
}

# The two things most likely to be missing, and both fail silently at runtime:
# the Playwright driver (the app cannot reach Chrome without it) and tzdata
# (every posting-window decision moves by hours).
Write-Head "Checking what actually landed in the bundle."
$checks = @{
    "Playwright driver (node.exe)" = "_internal\playwright\driver\node.exe"
    "Checkbox tick (check.svg)"    = "_internal\fbposter\qtui\assets\check.svg"
    "Qt SVG plugin"                = "_internal\PySide6\plugins\imageformats\qsvg.dll"
}
$missing = 0
foreach ($name in $checks.Keys) {
    if (Test-Path (Join-Path $DistApp $checks[$name])) {
        Write-Good $name
    } else {
        Write-Warn "$name NOT FOUND at $($checks[$name])"
        $missing++
    }
}
# tzdata lands under a version-stamped folder name, so it is matched loosely.
if (Get-ChildItem -Path (Join-Path $DistApp "_internal") -Filter "tzdata" -Recurse -Directory -ErrorAction SilentlyContinue) {
    Write-Good "Time zone database (tzdata)"
} else {
    Write-Warn "tzdata NOT FOUND -- Asia/Jerusalem will not resolve"
    $missing++
}

$sizeMb = [math]::Round((Get-ChildItem $DistApp -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Step "Folder size: $sizeMb MB"

# Ask the bundle itself. Checking that files exist proves they were copied;
# running the .exe proves it can actually boot Python, import Qt and Playwright,
# and resolve Asia/Jerusalem -- which is the part that fails silently later.
# A GUI-subsystem .exe does not block the shell, so this has to Start-Process
# and wait for it explicitly.
Write-Head "Asking the built .exe to check itself."
$exe = Join-Path $DistApp "FacebookAutoPoster.exe"
$run = Start-Process -FilePath $exe -ArgumentList "--selftest", "--quiet" -Wait -PassThru
if ($run.ExitCode -eq 0) {
    Write-Good "Self test passed inside the bundle."
} else {
    Write-Warn "Self test FAILED inside the bundle (exit $($run.ExitCode))."
    $missing++
}
$report = Join-Path $env:LOCALAPPDATA "FBAutomation\selftest.txt"
if (-not (Test-Path $report)) { $report = "C:\FBAutomation\selftest.txt" }
if (Test-Path $report) {
    Get-Content $report | ForEach-Object { Write-Step $_ }
}

# The same rule as the suite, for the same reason. Every one of these
# failures is silent at runtime -- the app opens and simply never reaches
# Chrome, or resolves the wrong time zone, or draws a checkbox with no tick --
# and none of them is describable by a non-technical client. Warning and
# packaging anyway put the discovery of a broken build on the person least able
# to diagnose it.
if ($missing -gt 0) {
    if ($Force) {
        Write-Warn "$missing check(s) failed -- packaging anyway because -Force was given."
    } else {
        throw ("$missing check(s) failed. Not shipping a bundle that fails its own " +
               "self test -- these failures are silent for the client. Fix it, or " +
               "pass -Force to package it regardless.")
    }
}

# --- stage 2: Inno Setup -----------------------------------------------------

if ($SkipInstaller) {
    Write-Head "Skipping the installer (-SkipInstaller)."
    Write-Step "The app folder is ready at $DistApp"
    return
}

Write-Head "Building the installer."
$iscc = $null
foreach ($candidate in @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    # winget installs per-user when it is not run elevated, which is the
    # common case and lands here rather than in Program Files.
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)) {
    if (Test-Path $candidate) { $iscc = $candidate; break }
}
if ($null -eq $iscc) {
    $iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
}

if ($null -eq $iscc) {
    Write-Warn "Inno Setup is not installed, so no Setup.exe was built."
    Write-Step "Install it from https://jrsoftware.org/isdl.php and re-run this,"
    Write-Step "or hand the client the folder at $DistApp instead."
    return
}

Invoke-Native -Exe $iscc -Arguments @($Iss) -FailMessage "Inno Setup failed"

$setup = Get-ChildItem (Join-Path $Root "dist") -Filter "FacebookAutoPoster-Setup-*.exe" |
         Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Head "Done."
Write-Good "Give the client: $($setup.FullName)"
Write-Step  "Size: $([math]::Round($setup.Length / 1MB, 1)) MB"
