$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot

& python -m flask --app app run
