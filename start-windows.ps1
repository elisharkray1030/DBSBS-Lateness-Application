$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot

# Explicit one-time database preparation (safe no-op on restart):
# importing the application performs no database I/O.
& python -m flask --app app init-db

& python -m flask --app app run
