param([switch]$Scheduled, [switch]$Full, [switch]$Worker)
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Install the project virtual environment first.' }
Push-Location -LiteralPath $projectPath
try {
    if ($Worker) { & $pythonPath -m investment_assistant company worker --limit 5 --scheduled }
    elseif ($Scheduled) { & $pythonPath -m investment_assistant company scheduled }
    elseif ($Full) { & $pythonPath -m investment_assistant company ingest --full }
    else { & $pythonPath -m investment_assistant company ingest }
    exit $LASTEXITCODE
}
finally { Pop-Location }
