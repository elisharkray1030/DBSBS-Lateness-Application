$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot

# Serve on waitress bound to the office LAN. serve.py prepares the database
# first (idempotent; a safe no-op on restart), so this is all a restart needs.
# Set SECRET_KEY (required) and optionally PORT before running, e.g.:
#   $env:SECRET_KEY = '<per-host secret>'
& python serve.py
