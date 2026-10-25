# Launch the interactive FR-8 runner from any working directory.
[CmdletBinding()]
param(
    [string]$Url,
    [string]$ResultsDir,
    [switch]$InstallDependencies
)

$ErrorActionPreference = 'Stop'
$taskPython = Get-Command python -ErrorAction SilentlyContinue
if (-not $taskPython) {
    Write-Error 'Python is required. Install Python 3.10+ and make it available on PATH.'
    exit 1
}

& $taskPython.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
if ($LASTEXITCODE -ne 0) {
    Write-Error 'Python 3.10 or newer is required.'
    exit 1
}

if ($InstallDependencies) {
    & $taskPython.Source -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $taskPython.Source -c 'import requests'
if ($LASTEXITCODE -ne 0) {
    Write-Error 'Missing dependencies. Run this script once with -InstallDependencies.'
    exit 1
}

$taskArguments = @((Join-Path $PSScriptRoot 'run_benchmarks.py'))
if ($Url) { $taskArguments += @('--url', $Url) }
if ($ResultsDir) { $taskArguments += @('--results-dir', $ResultsDir) }
& $taskPython.Source @taskArguments
exit $LASTEXITCODE
