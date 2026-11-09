$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & py -3 -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE) { throw 'Python environment creation failed.' }
}
& $python -m pip install -r (Join-Path $projectRoot 'requirements-dev.txt')
if ($LASTEXITCODE) { throw 'Dependency installation failed.' }
Write-Host "Development interpreter: $python"
Write-Host 'Validate the Linux services on the Jetson with .\deploy\jetson.ps1 Test.'
