param(
    [ValidateSet('offline', 'real', 'baseline')]
    [string]$Mode = 'offline',
    [string]$Baseline = '',
    [string]$Python = '',
    [string]$Output = ''
)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskBackend = Join-Path $taskRoot 'fastapi_demo'
if ([string]::IsNullOrWhiteSpace($Python)) {
    $taskVenvPython = if ($IsLinux -or $IsMacOS) {
        Join-Path $taskRoot '.venv/bin/python'
    } else {
        Join-Path $taskRoot '.venv/Scripts/python.exe'
    }
    $Python = if (Test-Path -LiteralPath $taskVenvPython) { $taskVenvPython } else { 'python' }
}
# Resolve relative paths before changing to the backend working directory.
if ($Python -ne 'python' -and (Test-Path -LiteralPath $Python)) {
    $Python = (Resolve-Path -LiteralPath $Python).Path
}
if ($Baseline) { $Baseline = [System.IO.Path]::GetFullPath($Baseline) }
if ($Output) { $Output = [System.IO.Path]::GetFullPath($Output) }
$taskArguments = if ($Mode -eq 'offline') {
    @('-m', 'evaluation.local_checks')
} else {
    @('-m', 'evaluation.performance_runner', '--mode', $Mode)
}
if ($Baseline) { $taskArguments += @('--baseline', $Baseline) }
if ($Output) { $taskArguments += @('--output', $Output) }
$taskExit = 2
Push-Location $taskBackend
try {
    & $Python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 2)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required. Use -Python to select it.' }
    & $Python @taskArguments
    $taskExit = $LASTEXITCODE
} catch {
    Write-Error -ErrorRecord $_ -ErrorAction Continue
} finally {
    Pop-Location
}
exit $taskExit
