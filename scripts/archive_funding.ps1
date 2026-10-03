<#
.SYNOPSIS
    Archive OKX realised funding rates. Intended to run on a schedule.

.DESCRIPTION
    Closes advisor finding F-8 / prerequisite P-11.

    OKX retains only ~95 days of realised funding history and the window ROLLS, so every
    week this does not run is a week of ground truth permanently lost - it cannot be
    recovered later at any price short of a commercial data vendor.

    Public endpoints only. No credential is read or transmitted.

    Each run archives the current top-N instruments PLUS every instrument already in the
    store, so an instrument that drops out of the turnover ranking keeps being archived.

    Evidence: every run appends to logs\<env>\archive_funding.log, and a SUCCESSFUL run writes
    state\<env>\archive_funding.last_ok. -Status trusts that stamp, not the task's existence.

.PARAMETER Install
    Register (or re-register) the weekly task. Requires an ELEVATED shell. Runs whether or
    not you are logged on (S4U logon: no password is stored), wakes the machine, catches up
    if a run was missed, and retries 3 times on failure.

.PARAMETER RunNow
    Start the registered task immediately and wait for its result - the way to prove the
    configuration works without waiting for Sunday.

.PARAMETER Status
    Report the task's last result (decoded), next run, and the age of the last success.
    Exits 1 if the task is missing or the last success is missing or older than 8 days.

.EXAMPLE
    .\scripts\archive_funding.ps1 -Install        # elevated shell
    .\scripts\archive_funding.ps1 -RunNow
    .\scripts\archive_funding.ps1 -Status
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$RunNow,
    [switch]$Status,
    [int]$Symbols = 20,
    [ValidateSet('DEMO', 'PAPER')]
    [string]$Env = 'PAPER'
)

$ErrorActionPreference = 'Stop'
$taskName = 'okxq-archive-funding'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
$slug = $Env.ToLower()
$logDir = Join-Path $root "logs\$slug"
$stateDir = Join-Path $root "state\$slug"
$logFile = Join-Path $logDir 'archive_funding.log'
$stampFile = Join-Path $stateDir 'archive_funding.last_ok'
$staleAfterDays = 8

# Task Scheduler result codes worth naming (decimal as Get-ScheduledTaskInfo prints them).
# STRING keys on purpose: a .NET hashtable does not match an Int32 key against an Int64
# lookup, so numeric keys would silently never resolve.
$resultNames = @{
    '0'          = 'success'
    '1'          = 'the archive script exited 1 (see the log)'
    '267008'     = 'SCHED_S_TASK_READY (0x41300)'
    '267009'     = 'SCHED_S_TASK_RUNNING (0x41301): running now'
    '267011'     = 'SCHED_S_TASK_HAS_NOT_RUN (0x41303): the task has never run yet'
    '267014'     = 'SCHED_S_TASK_TERMINATED (0x41306): stopped before it finished'
    '2147943645' = '0x800710E0: the operator or administrator refused the request'
}

function Write-Log([string]$msg) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $line = "{0} {1}" -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'), $msg
    Add-Content -Path $logFile -Value $line
    Write-Host $line
}

if ($Status) {
    $rc = 0
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Host "task: MISSING - register with -Install (elevated)" -ForegroundColor Red
        $rc = 1
    } else {
        $info = Get-ScheduledTaskInfo -TaskName $taskName
        $code = [int64]$info.LastTaskResult
        $name = $resultNames["$code"]
        if (-not $name) { $name = ('0x{0:X}' -f $code) }
        Write-Host "task: $($task.State); logon: $($task.Principal.LogonType); wake: $($task.Settings.WakeToRun)"
        Write-Host "last run:    $($info.LastRunTime)  result: $code = $name"
        Write-Host "next run:    $($info.NextRunTime)"
        if ($info.LastRunTime.Year -lt 2000) {
            Write-Host "(a 1999 last-run time is Windows' placeholder for 'never run')" -ForegroundColor Yellow
        }
    }
    if (Test-Path $stampFile) {
        $last = [datetime]::Parse((Get-Content $stampFile -Raw).Trim()).ToUniversalTime()
        $age = ((Get-Date).ToUniversalTime() - $last).TotalDays
        Write-Host ("last success: {0:u} ({1:N1} days ago)" -f $last, $age)
        if ($age -gt $staleAfterDays) {
            Write-Host "STALE: older than $staleAfterDays days - see $logFile" -ForegroundColor Red
            $rc = 1
        }
    } else {
        Write-Host "last success: NEVER - no evidence it has run ($stampFile)" -ForegroundColor Red
        $rc = 1
    }
    exit $rc
}

if (-not (Test-Path $py)) {
    Write-Error "venv not found at $py"
}

if ($Install) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Write-Error 'Registering an S4U task needs an elevated shell (Run as administrator).'
    }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Env $Env -Symbols $Symbols" `
        -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3am
    # S4U: runs whether or not the user is logged on, without storing a password. It has no
    # credentials for authenticated network shares, which this public-endpoint job never needs.
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType S4U -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 15) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description 'Archive OKX realised funding (okx-quant P-11)' -Force | Out-Null

    Write-Host "Registered '$taskName': weekly Sundays 03:00, S4U, wake-to-run, 3 retries." -ForegroundColor Green
    Write-Host "Prove it works now:  .\scripts\archive_funding.ps1 -RunNow"
    Write-Host "Check any time:      .\scripts\archive_funding.ps1 -Status"
    exit 0
}

if ($RunNow) {
    Start-ScheduledTask -TaskName $taskName
    Write-Host "Started '$taskName'; waiting for it to finish (funding-only takes a few minutes)..."
    Start-Sleep -Seconds 5
    $deadline = (Get-Date).AddMinutes(30)
    while ((Get-ScheduledTask -TaskName $taskName).State -eq 'Running' -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 10
    }
    & $PSCommandPath -Status -Env $Env
    exit $LASTEXITCODE
}

# --- the archive run itself ---------------------------------------------------------------

Set-Location $root
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
Write-Log "START env=$Env symbols=$Symbols"

# Start-Process with separate redirect files, not `2>&1`: structured logs go to stderr, and
# in Windows PowerShell 5.1 redirecting a native command's stderr wraps each line in an
# ErrorRecord and trips $ErrorActionPreference='Stop' even on a clean exit.
$out = Join-Path $logDir 'archive_funding.last.stdout.txt'
$err = Join-Path $logDir 'archive_funding.last.stderr.txt'
$proc = Start-Process -FilePath $py `
    -ArgumentList @('-m', 'okxq.data.cli', 'backfill', '--env', $Env, '--symbols', "$Symbols", '--funding-only') `
    -WorkingDirectory $root -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput $out -RedirectStandardError $err
$code = $proc.ExitCode
Get-Content $out -ErrorAction SilentlyContinue | Where-Object { $_ -match 'funding history|instruments=|errors=' } |
    ForEach-Object { Write-Log "  $_" }

if ($code -ne 0) {
    Write-Log "FAIL exit=$code (stderr: $err)"
    exit $code
}
# Written only on success: -Status trusts this file, not the task's existence.
(Get-Date).ToUniversalTime().ToString('o') | Set-Content -Path $stampFile
Write-Log 'OK'
exit 0
