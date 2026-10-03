<#
.SYNOPSIS
    Archive OKX realised funding rates. Intended to run on a schedule.

.DESCRIPTION
    Closes advisor finding F-8 / prerequisite P-11.

    OKX retains only ~95 days of realised funding history and the window ROLLS, so every
    week this does not run is a week of ground truth permanently lost - it cannot be
    recovered later at any price short of a commercial data vendor. M2's backtester
    accrues funding across a multi-year walk-forward and validates its modelled funding
    against this realised overlap, so the archive's depth directly bounds how well that
    model can be checked.

    Public endpoints only. No credential is read or transmitted.

    Cadence: weekly is comfortable, monthly is the outer limit. Anything slower risks
    losing coverage between runs.

.PARAMETER Install
    Register a weekly Scheduled Task instead of running the archive now. This modifies the
    machine's scheduled tasks, so it is opt-in and never done implicitly.

.PARAMETER Symbols
    Universe size to archive funding for. Default 20, matching the M1 research universe.

.EXAMPLE
    .\scripts\archive_funding.ps1
    Run the archive once, now.

.EXAMPLE
    .\scripts\archive_funding.ps1 -Install
    Register the weekly task (Sundays 03:00). Requires an elevated shell.
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [int]$Symbols = 20,
    [ValidateSet('DEMO', 'PAPER', 'LIVE')]
    [string]$Env = 'PAPER'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $py)) {
    Write-Error "venv not found at $py"
}

if ($Install) {
    $taskName = 'okxq-archive-funding'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Env $Env -Symbols $Symbols" `
        -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3am
    # Run whether or not the user is logged on would need stored credentials; this keeps it
    # credential-free and simply runs at the next opportunity if the machine was off.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Settings $settings -Description 'Archive OKX realised funding (okx-quant P-11)' -Force | Out-Null

    Write-Host "Registered scheduled task '$taskName' (weekly, Sundays 03:00)." -ForegroundColor Green
    Write-Host "Verify with: Get-ScheduledTask -TaskName $taskName"
    Write-Host "Remove with: Unregister-ScheduledTask -TaskName $taskName -Confirm:`$false"
    exit 0
}

Set-Location $root
Write-Host "Archiving realised funding ($Env, top $Symbols instruments)..." -ForegroundColor Cyan

# Funding only: no OHLCV, no mark/index. Cheap - roughly 3 pages per instrument.
#
# Note the absence of `2>&1`: structured logs go to stderr, and in Windows PowerShell 5.1
# redirecting a native command's stderr wraps each line in an ErrorRecord and trips
# $ErrorActionPreference='Stop' even on a clean exit. The exit code is the signal.
$ErrorActionPreference = 'Continue'
& $py -m okxq.data.cli backfill --env $Env --symbols $Symbols --funding-only
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'

if ($code -ne 0) {
    Write-Error "funding archive failed with exit code $code"
}
Write-Host 'Funding archive complete.' -ForegroundColor Green
