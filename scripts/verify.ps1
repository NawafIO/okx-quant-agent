<#
.SYNOPSIS
    Local verification gate - the same checks CI runs (architecture §21).

.DESCRIPTION
    Runs lint, format check, strict type check, the safety guard tests, and the full suite
    with coverage. Exits non-zero on the first failure.

    Guard tests run before the full suite deliberately: a ZERO-LIVE-CAPITAL failure is the
    headline, not a line buried in a summary.

.EXAMPLE
    .\scripts\verify.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipCoverage
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $py)) {
    Write-Error "venv not found at $py. Run: uv venv --python 3.12; uv pip install -e `".[dev]`""
}

Set-Location $root

# The argument list must NOT be named $Args: that is a PowerShell automatic variable (the
# unbound arguments), so a parameter of that name is shadowed, '@Args' splats an EMPTY array
# and python.exe starts as an interactive REPL instead of running the check. That bug ran
# every step as a bare REPL on Windows until 2026-10-03. tests/guards/test_verify_ps1.py
# forbids automatic-variable parameter names in every .ps1.
function Invoke-Step {
    param([string]$Name, [string[]]$PyArgs)
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    if (-not $PyArgs -or $PyArgs.Count -eq 0) {
        # Never launch python.exe without arguments: that is an interactive REPL, not a check.
        Write-Host "FAILED: $Name - no arguments reached Invoke-Step" -ForegroundColor Red
        exit 2
    }
    & $py @PyArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Invoke-Step 'Lint (ruff)'            @('-m', 'ruff', 'check', '.')
Invoke-Step 'Format check (ruff)'    @('-m', 'ruff', 'format', '--check', '.')
Invoke-Step 'Type check (mypy strict)' @('-m', 'mypy')
Invoke-Step 'GUARD tests (ZERO-LIVE-CAPITAL)' @('-m', 'pytest', '-m', 'guard', '-v')

if ($SkipCoverage) {
    Invoke-Step 'Full suite' @('-m', 'pytest')
} else {
    Invoke-Step 'Full suite + coverage' @(
        '-m', 'pytest', '--cov', '--cov-report=term-missing'
    )
}

# Not skippable by -SkipCoverage: M5's 100% branch coverage is an acceptance criterion, not a
# report. This exact argument list is pinned by tests/guards/test_risk_enforcement.py.
Invoke-Step 'Risk Engine 100% branch coverage (M5)' @(
    '-m', 'pytest', 'tests/unit/risk', 'tests/guards/test_risk_policy_frozen.py', '-p', 'no:cacheprovider', '--cov=okxq.risk', '--cov-config=.coveragerc-risk', '--cov-branch', '--cov-fail-under=100', '--cov-report=term-missing'
)

Write-Host "`nALL CHECKS PASSED" -ForegroundColor Green
