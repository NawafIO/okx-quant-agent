<#
.SYNOPSIS
    Collect OKX order-book spread snapshots. Intended to run hourly on a schedule.

.DESCRIPTION
    Cycle 2, decision D2 (docs/CYCLE2_DESIGN.md section 3). slip-v2's spread term is an
    estimator that reads ~500x the live BTC spread; this records what the book actually quoted
    so the spread term can be calibrated against measurement.

    Each run polls the 8 research instruments every 60 s for 59 minutes and appends to
    data\<env>\spreads\YYYY-MM-DD.jsonl. The task repeats hourly, so a crash loses at most an
    hour. Collection must run for the pre-registered minimum (CYCLE2_DESIGN section 3) before
    any calibration reads the files.

    Public endpoints only. No credential is read or transmitted.

    Evidence: every run appends to logs\<env>\collect_spreads.log, and a SUCCESSFUL run (some
    samples, no more gaps than samples) writes state\<env>\collect_spreads.last_ok.
    -Status trusts that stamp, not the task's existence.

.PARAMETER Install
    Register (or re-register) the hourly task. Requires an ELEVATED shell. S4U logon (no
    password stored), wakes the machine, catches up a missed run, never overlaps itself.

.PARAMETER RunNow
    Start the registered task immediately and wait for it (up to ~60 minutes).

.PARAMETER Status
    Report the task's last result, next run, the age of the last success and today's sample
    count. Exits 1 if the task is missing or the last success is missing or older than 2 hours.

.EXAMPLE
    .\scripts\collect_spreads.ps1 -Install        # elevated shell
    .\scripts\collect_spreads.ps1 -Status
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$RunNow,
    [switch]$Status,
    [ValidateSet('DEMO', 'PAPER')]
    [string]$Env = 'PAPER'
)

$ErrorActionPreference = 'Stop'
$taskName = 'okxq-collect-spreads'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
$slug = $Env.ToLower()
$logDir = Join-Path $root "logs\$slug"
$stateDir = Join-Path $root "state\$slug"
$dataDir = Join-Path $root "data\$slug\spreads"
$logFile = Join-Path $logDir 'collect_spreads.log'
$stampFile = Join-Path $stateDir 'collect_spreads.last_ok'
$staleAfterHours = 2

# STRING keys on purpose (see archive_funding.ps1): Int32 keys never match an Int64 lookup.
$resultNames = @{
    '0'          = 'success'
    '1'          = 'the collector exited 1: no samples or more gaps than samples (see the log)'
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
    }
    if (Test-Path $stampFile) {
        $last = [datetime]::Parse((Get-Content $stampFile -Raw).Trim()).ToUniversalTime()
        $age = ((Get-Date).ToUniversalTime() - $last).TotalHours
        Write-Host ("last success: {0:u} ({1:N1} hours ago)" -f $last, $age)
        if ($age -gt $staleAfterHours) {
            Write-Host "STALE: older than $staleAfterHours hours - see $logFile" -ForegroundColor Red
            $rc = 1
        }
    } else {
        Write-Host "last success: NEVER - no evidence it has run ($stampFile)" -ForegroundColor Red
        $rc = 1
    }
    $today = Join-Path $dataDir ((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd') + '.jsonl')
    if (Test-Path $today) {
        $lines = @(Get-Content $today)
        $gaps = @($lines | Where-Object { $_ -match '"gap":' }).Count
        Write-Host ("today (UTC): {0} lines, {1} gaps  ({2})" -f $lines.Count, $gaps, $today)
    } else {
        Write-Host "today (UTC): no file yet ($today)"
    }
    $days = @(Get-ChildItem -Path $dataDir -Filter '*.jsonl' -ErrorAction SilentlyContinue).Count
    Write-Host "days with data: $days"
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
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Env $Env" `
        -WorkingDirectory $root
    # Hourly, starting a minute from now. With no -RepetitionDuration the repetition does not
    # expire on Windows 10/11; if it ever does, -Status reports STALE within two hours.
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Hours 1)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType S4U -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 70) -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description 'Collect OKX order-book spreads (okx-quant cycle 2 D2)' -Force | Out-Null

    Write-Host "Registered '$taskName': hourly, S4U, wake-to-run, 3 retries, no overlap." -ForegroundColor Green
    Write-Host "Check after the first hour:  .\scripts\collect_spreads.ps1 -Status"
    exit 0
}

if ($RunNow) {
    Start-ScheduledTask -TaskName $taskName
    Write-Host "Started '$taskName'; a run lasts ~59 minutes. Waiting..."
    Start-Sleep -Seconds 5
    $deadline = (Get-Date).AddMinutes(75)
    while ((Get-ScheduledTask -TaskName $taskName).State -eq 'Running' -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 30
    }
    & $PSCommandPath -Status -Env $Env
    exit $LASTEXITCODE
}

# --- the collection run itself ------------------------------------------------------------

Set-Location $root
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
Write-Log "START env=$Env"

# Start-Process with separate redirect files, not `2>&1` (PowerShell 5.1 wraps native stderr
# in ErrorRecords and trips $ErrorActionPreference='Stop' even on a clean exit).
$out = Join-Path $logDir 'collect_spreads.last.stdout.txt'
$err = Join-Path $logDir 'collect_spreads.last.stderr.txt'
$proc = Start-Process -FilePath $py `
    -ArgumentList @('-m', 'okxq.data.spreads', '--env', $Env) `
    -WorkingDirectory $root -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput $out -RedirectStandardError $err
$code = $proc.ExitCode
Get-Content $out -ErrorAction SilentlyContinue | Where-Object { $_ -match '^spreads ' } |
    ForEach-Object { Write-Log "  $_" }

if ($code -ne 0) {
    Write-Log "FAIL exit=$code (stderr: $err)"
    exit $code
}
# The Python collector writes the success stamp itself; nothing to do here but log.
Write-Log 'OK'
exit 0
