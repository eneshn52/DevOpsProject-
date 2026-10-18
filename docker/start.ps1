# Initialize a private local configuration once, then build and start Compose.
[CmdletBinding()]
param([switch]$InitializeOnly)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskEnvironment = Join-Path $taskRoot '.env.docker'
if (-not (Test-Path -LiteralPath $taskEnvironment)) {
    $taskBytes = New-Object byte[] 48
    $taskRandom = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $taskRandom.GetBytes($taskBytes) } finally { $taskRandom.Dispose() }
    $taskSecret = [Convert]::ToBase64String($taskBytes)
    $taskExample = Get-Content -LiteralPath (Join-Path $taskRoot '.env.docker.example') -Raw
    $taskContents = $taskExample.Replace('DJANGO_SECRET_KEY=replace-with-generated-secret', "DJANGO_SECRET_KEY=$taskSecret")
    [System.IO.File]::WriteAllText($taskEnvironment, $taskContents, (New-Object System.Text.UTF8Encoding $false))
    Write-Host 'Created .env.docker with a generated Django secret.'
}
if ($InitializeOnly) { return }
Push-Location -LiteralPath $taskRoot
try {
    docker compose --env-file .env.docker up --build -d --wait
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose startup failed. Inspect docker compose logs.' }
} finally { Pop-Location }
