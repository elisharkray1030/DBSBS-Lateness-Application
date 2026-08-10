$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot

try {
    & python -c "import flask" | Out-Null
} catch {
    Write-Host 'Flask is not installed in the current Python environment.' -ForegroundColor Red
    Write-Host 'Install dependencies with: python -m pip install -r requirements.txt' -ForegroundColor Yellow
    exit 1
}

& python -m flask --app app run