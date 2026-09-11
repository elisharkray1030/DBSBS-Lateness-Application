#requires -Version 5.1
<#
.SYNOPSIS
  One-command launcher for the Lateness app in Docker.

.DESCRIPTION
  Checks Docker, prepares the per-host .env, starts the app, and opens it in
  the browser. Data lives in a named Docker volume; backups and restores use
  the ./shared folder. Other office PCs reach the app over the LAN by default.

.EXAMPLE
  .\run.ps1                 # interactive menu
  .\run.ps1 up              # start (office LAN)
  .\run.ps1 up -Local       # start bound to 127.0.0.1 only
  .\run.ps1 up -NoSeed      # start without seeding the Master List from namelist.csv
  .\run.ps1 backup          # write a backup into .\shared\backups
  .\run.ps1 restore -Backup lateness-20260910-120000
  .\run.ps1 seed            # load deterministic demo data (destructive)
  .\run.ps1 reset           # stop and delete all live data

  (run.cmd accepts --local/--no-seed/--yes style and normalises them.)
#>
param(
    [string]$Command = '',
    [string]$Backup = '',
    [switch]$NoSeed,
    [switch]$Local,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$BaseCompose = @('-f', (Join-Path $PSScriptRoot 'compose.yaml'))
$BackupKeep = if ($env:BACKUP_KEEP) { $env:BACKUP_KEEP } else { '7' }

function Write-Info([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Warn([string]$Message) { Write-Host "warning: $Message" -ForegroundColor Yellow }
function Write-Err([string]$Message) { Write-Host "error: $Message" -ForegroundColor Red }

function Get-EnvValue([string]$Key) {
    $envPath = Join-Path $PSScriptRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath)) { return $null }
    $pattern = ('^\s*{0}\s*=\s*(.+?)\s*$' -f [regex]::Escape($Key))
    $match = Select-String -LiteralPath $envPath -Pattern $pattern | Select-Object -First 1
    if ($match) { return $match.Matches[0].Groups[1].Value }
    return $null
}

function Test-DockerInstalled {
    return [bool](Get-Command docker -ErrorAction SilentlyContinue)
}

function Test-DockerRunning {
    if (-not (Test-DockerInstalled)) { return $false }
    & docker info *> $null
    return ($LASTEXITCODE -eq 0)
}

function New-SecretKey {
    $bytes = New-Object 'System.Byte[]' 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Ensure-SecretKey {
    $envPath = Join-Path $PSScriptRoot '.env'
    if (Test-Path -LiteralPath $envPath) {
        $content = Get-Content -LiteralPath $envPath -Raw
        if ($content -match '(?m)^\s*SECRET_KEY\s*=\s*\S+') { return }
    }
    $line = "SECRET_KEY=$(New-SecretKey)"
    if (Test-Path -LiteralPath $envPath) {
        Add-Content -LiteralPath $envPath -Value $line
    }
    else {
        Set-Content -LiteralPath $envPath -Value $line -Encoding ASCII
    }
    Write-Info "Generated a per-host SECRET_KEY in .env"
}

function Find-DockerDesktop {
    $candidates = @()
    if ($env:ProgramFiles) {
        $candidates += Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    }
    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'
    }
    return ($candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1)
}

function Ensure-Docker {
    if (-not (Test-DockerInstalled)) {
        Write-Warn "Docker is not installed."
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            $answer = Read-Host "Install Docker Desktop now with winget (per-user, no admin)? [y/N]"
            if ($answer -match '^(y|yes)$') {
                winget install --id Docker.DockerDesktop --exact --accept-package-agreements --accept-source-agreements
                Write-Info "Docker Desktop installed. Sign out/in or reboot if prompted, then re-run this script."
                exit 1
            }
        }
        Write-Err "Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and re-run."
        exit 1
    }

    if (-not (Test-DockerRunning)) {
        Write-Warn "Docker is installed but the engine is not running."
        $desktop = Find-DockerDesktop
        if ($desktop) {
            $answer = Read-Host "Start Docker Desktop now? [y/N]"
            if ($answer -match '^(y|yes)$') {
                Start-Process -FilePath $desktop | Out-Null
                Write-Info "Waiting for the Docker engine (this can take a minute)..."
                for ($i = 0; $i -lt 60; $i++) {
                    Start-Sleep -Seconds 2
                    if (Test-DockerRunning) { break }
                }
            }
        }
        if (-not (Test-DockerRunning)) {
            Write-Err "Docker engine is still not reachable. Start Docker Desktop and re-run."
            exit 1
        }
    }

    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Docker Compose v2 is required ('docker compose'). Update Docker Desktop."
        exit 1
    }
}

function Get-UpComposeArgs {
    $files = @($BaseCompose)
    if (-not $NoSeed) {
        if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'namelist.csv'))) {
            Write-Err "namelist.csv not found. Add it to the project root, or run with --no-seed and import boarders in the app."
            exit 1
        }
        $files += @('-f', (Join-Path $PSScriptRoot 'compose.seed.yaml'))
    }
    return $files
}

function Invoke-Compose([string[]]$ComposeArgs, [string[]]$Arguments) {
    $combined = $ComposeArgs + $Arguments
    & docker compose @combined
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose exited with code $LASTEXITCODE"
    }
}

function Wait-Healthy([string[]]$ComposeArgs) {
    Write-Info "Waiting for the app to report healthy..."
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        $ids = @(& docker compose @ComposeArgs ps -q app 2>$null)
        if ($ids.Count -gt 0 -and $ids[0]) {
            $state = & docker inspect -f '{{.State.Health.Status}}' $ids[0] 2>$null
            if ($state -eq 'healthy') { return $true }
            if ($state -eq 'unhealthy') { return $false }
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Get-HostIPv4 {
    try {
        $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.IPAddress -ne '127.0.0.1' -and
                $_.IPAddress -notlike '169.254.*' -and
                $_.PrefixOrigin -ne 'WellKnown'
            } |
            Select-Object -First 1 -ExpandProperty IPAddress
        return $ip
    }
    catch {
        return $null
    }
}

function Test-PortInUse([string]$Port) {
    try {
        $conns = Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction Stop
        return [bool]$conns
    }
    catch {
        return $false
    }
}

function Get-RestoreFolder {
    $root = Join-Path $PSScriptRoot 'shared\restore'
    if (-not (Test-Path -LiteralPath $root)) { return $null }
    if ($Backup) {
        $candidate = Join-Path $root $Backup
        if (Test-Path -LiteralPath $candidate) { return $Backup }
        Write-Err "Backup folder not found: $candidate"
        exit 1
    }
    $folders = @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'lateness-*' })
    if ($folders.Count -eq 0) {
        Write-Err "No backup folders in shared\restore. Copy a lateness-* folder there first."
        exit 1
    }
    if ($folders.Count -eq 1) { return $folders[0].Name }
    Write-Info "Backups available to restore:"
    for ($i = 0; $i -lt $folders.Count; $i++) {
        Write-Host ("  [{0}] {1}" -f ($i + 1), $folders[$i].Name)
    }
    $choice = Read-Host "Choose a number"
    $index = 0
    if (-not [int]::TryParse($choice, [ref]$index) -or $index -lt 1 -or $index -gt $folders.Count) {
        Write-Err "Invalid choice."
        exit 1
    }
    return $folders[$index - 1].Name
}

function Confirm-Destructive([string]$Message) {
    if ($Yes) { return }
    $answer = Read-Host "$Message Type YES to continue"
    if ($answer -ne 'YES') {
        Write-Info "Cancelled."
        exit 0
    }
}

function Start-App {
    Ensure-Docker
    Ensure-SecretKey
    $upArgs = Get-UpComposeArgs
    $bindAddr = ''
    if ($Local) {
        $bindAddr = '127.0.0.1'
        $env:BIND_ADDR = $bindAddr
    }
    else {
        if ($env:BIND_ADDR) { $bindAddr = $env:BIND_ADDR }
        else {
            $fromEnv = Get-EnvValue 'BIND_ADDR'
            if ($fromEnv) { $bindAddr = $fromEnv } else { $bindAddr = '0.0.0.0' }
        }
    }
    $port = $env:APP_PORT
    if (-not $port) { $port = Get-EnvValue 'APP_PORT' }
    if (-not $port) { $port = '8000' }
    Invoke-Compose $upArgs @('up', '-d', '--build')
    if (Test-PortInUse $port) {
        Write-Warn "Port $port is already in use; if start fails, set APP_PORT in .env to another port."
    }
    if (Wait-Healthy $upArgs) {
        $url = "http://127.0.0.1:$port"
        Write-Info "App is up: $url"
        if ($bindAddr -eq '0.0.0.0') {
            $ip = Get-HostIPv4
            if ($ip) { Write-Info "On the office network: http://${ip}:$port" }
            Write-Info "For a stable URL, reserve this PC's IP, allow inbound TCP $port through Windows Firewall, and keep the PC awake."
        }
        Start-Process $url
    }
    else {
        Write-Err "App did not become healthy. Recent logs:"
        & docker compose @upArgs logs --tail 40
        exit 1
    }
}

function Stop-App {
    Ensure-Docker
    Ensure-SecretKey
    Invoke-Compose $BaseCompose @('down')
    Write-Info "Stopped. Your data is kept in the lateness-data volume."
}

function Show-Logs {
    Ensure-Docker
    Ensure-SecretKey
    Invoke-Compose $BaseCompose @('logs', '-f', '--tail', '100')
}

function Show-Status {
    Ensure-Docker
    Ensure-SecretKey
    & docker compose @BaseCompose ps
}

function Save-Backup {
    Ensure-Docker
    Ensure-SecretKey
    $dest = Join-Path $PSScriptRoot 'shared\backups'
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Invoke-Compose $BaseCompose @(
        'run', '--rm', 'backup',
        'python', 'backup_db.py', '--dest', '/backup', '--keep', $BackupKeep
    )
    Write-Info "Backup written under shared\backups."
}

function Restore-Backup {
    Ensure-Docker
    Ensure-SecretKey
    $folder = Get-RestoreFolder
    if (-not $folder) {
        Write-Err "No backup selected."
        exit 1
    }
    Confirm-Destructive "Restoring '$folder' replaces the live database."
    Invoke-Compose $BaseCompose @('down')
    Invoke-Compose $BaseCompose @(
        'run', '--rm', 'restore',
        'python', 'restore_db.py', '--from', "/restore/$folder", '--force'
    )
    Invoke-Compose (Get-UpComposeArgs) @('up', '-d')
    Write-Info "Restored '$folder' and restarted the app."
}

function Seed-Demo {
    Ensure-Docker
    Ensure-SecretKey
    Confirm-Destructive "Seeding demo data wipes imported reports, history, and punishments in the live volume."
    $upArgs = Get-UpComposeArgs
    Invoke-Compose $BaseCompose @('down')
    Invoke-Compose $upArgs @(
        'run', '--rm', 'seed',
        'python', 'seed_demo_data.py',
        '--db', '/data/lateness_history.db',
        '--namelist', '/data/namelist.csv',
        '--log-dir', '/data/raw/seed'
    )
    Invoke-Compose $upArgs @('up', '-d')
    Write-Info "Demo data seeded."
}

function Reset-App {
    Ensure-Docker
    Ensure-SecretKey
    Confirm-Destructive "This deletes the lateness-data volume, including the database and archive. Backups in shared\backups are kept."
    Invoke-Compose $BaseCompose @('down', '-v')
    Write-Info "All live data deleted."
}

function Invoke-Action([string]$Name) {
    switch ($Name) {
        'up' { Start-App }
        'down' { Stop-App }
        'logs' { Show-Logs }
        'status' { Show-Status }
        'backup' { Save-Backup }
        'restore' { Restore-Backup }
        'seed' { Seed-Demo }
        'reset' { Reset-App }
        'help' { Get-Help $PSCommandPath -Detailed }
        default {
            Write-Err "Unknown command '$Name'. Use: up, down, logs, status, backup, restore, seed, reset."
            exit 2
        }
    }
}

function Show-Menu {
    Write-Host ''
    Write-Host '  Lateness App (Docker)' -ForegroundColor Cyan
    Write-Host '  ---------------------'
    Write-Host '  [1] Start            [2] Stop'
    Write-Host '  [3] Logs             [4] Status'
    Write-Host '  [5] Backup           [6] Restore'
    Write-Host '  [7] Seed demo data   [8] Reset (delete all data)'
    Write-Host '  [9] Quit'
    Write-Host ''
}

if ([string]::IsNullOrWhiteSpace($Command)) {
    while ($true) {
        Show-Menu
        $choice = Read-Host 'Choose an option'
        switch ($choice) {
            '1' { Start-App }
            '2' { Stop-App }
            '3' { Show-Logs }
            '4' { Show-Status }
            '5' { Save-Backup }
            '6' { Restore-Backup }
            '7' { Seed-Demo }
            '8' { Reset-App }
            '9' { exit 0 }
            default { Write-Warn "Unknown option '$choice'." }
        }
        Read-Host 'Press Enter to continue' | Out-Null
    }
}
else {
    Invoke-Action $Command.ToLowerInvariant()
}
