# Lateness Application

Flask dashboard for tracking boarder lateness at DBS Boarding School. It imports monthly
lateness logs, matches them against the Master List, and stores per-month reports that
staff review and search.

One designated, always-on Windows PC runs the app; every other staff PC just opens a
browser to it. The database and Monthly Log Archive live on **that PC's disk** — a single
writer, so there is no SQLite-over-network risk
([ADR 0004](docs/adr/0004-single-host-deployment.md)). The app is plain HTTP with **no
login** — keep it on the trusted office LAN and never expose it off-campus.

## Quick start (Windows host)

Install Docker Desktop and make sure it is running, then put `namelist.csv` in the project
folder:

```powershell
cd C:\lateness-app
.\run.cmd up            # start on the office LAN; seeds the Master List on first run
.\run.cmd firewall      # allow other office PCs through Windows Firewall (admin/UAC)
```

Then open `http://<host>:8000/` from any office PC — use the IP `run.cmd up` prints if the
hostname does not resolve.

- No `namelist.csv` yet? Start with `.\run.cmd up --no-seed` and import the Master List
  from the Boarders tab.
- Give the host a stable address and keep it awake so staff can rely on the URL — see
  [docs/deployment/windows-host.md](docs/deployment/windows-host.md).

> Do not start with `--local`: it binds to `127.0.0.1` only and hides the app from other
> PCs.

## Using the app

Import a Monthly Log, review/search/download saved reports, assign Punishments, log
I-Points, and browse the Statistics and Boarders tabs. See
[docs/usage.md](docs/usage.md).

## Operating it

- **Back up:** `.\run.cmd backup` writes to `shared\backups/`; mirror that to the NAS. See
  [docs/deployment/backup-and-restore.md](docs/deployment/backup-and-restore.md) and
  [docs/deployment/nas-share.md](docs/deployment/nas-share.md).
- **Restore:** `.\run.cmd restore` replaces the live database and asks you to confirm.
- **Update:** `.\run.cmd down`, `git pull`, `.\run.cmd up`. Data lives in the
  `lateness-data` volume and is preserved.

## Documentation

| Doc | What it covers |
| --- | --- |
| [docs/usage.md](docs/usage.md) | Day-to-day use: imports, reports, punishments, I-Points, statistics |
| [docs/architecture.md](docs/architecture.md) | How the app works: modules, seams, invariants |
| [docs/deployment/windows-host.md](docs/deployment/windows-host.md) | Host setup: stable address, firewall detail, keep-awake, native (NSSM) host |
| [docs/deployment/backup-and-restore.md](docs/deployment/backup-and-restore.md) | Backups, scheduling, restore drill |
| [docs/deployment/nas-share.md](docs/deployment/nas-share.md) | Preparing the NAS share |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Developer setup, tests, conventions |
| [CONTEXT.md](CONTEXT.md) | Domain vocabulary |

## Troubleshooting

- **No boarders on first start** — put `namelist.csv` in the project root (a name column
  and a `Bed` column), or start with `.\run.cmd up --no-seed` and import it from the
  Boarders tab.
- **The app is unreachable on port 8000** — another process may hold the port. Set
  `APP_PORT` in `.env` to a free port, allow that port through the firewall, then
  `.\run.cmd up`.
- **Reachable on the host but not from other PCs** — run `.\run.cmd firewall` on the host,
  confirm the app was not started with `--local`, and use the host's LAN address (or IP).
  From a client, `Test-NetConnection <host-ip> -Port 8000` should report
  `TcpTestSucceeded : True`.
- **The launcher reports Docker is not running** — start Docker Desktop and retry.

## Reference

### Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `SECRET_KEY` | Required; the app aborts at startup without one. | — |
| `DB_PATH` | SQLite database file. | `lateness_history.db` |
| `NAMELIST_PATH` | Seed Master List read once by `init-db`. | `namelist.csv` |
| `LOG_ARCHIVE_DIR` | Monthly Log Archive folder. | `data/logs` |
| `MAX_CONTENT_LENGTH` | Request size cap for Imports, in bytes. | `16777216` (16 MB) |
| `LOG_LEVEL` | Application log level. | `INFO` |
| `PORT` | Listen port for `serve.py` only. | `8000` |
| `BIND_ADDR` | Docker host bind address. | `0.0.0.0` (the office LAN); set `127.0.0.1` for loopback only |
| `APP_PORT` | Docker host port. | `8000` |
| `BACKUP_KEEP` | Timestamped backups to keep in `shared/backups`. | `7` |

`compose.yaml` fixes `DB_PATH`, `NAMELIST_PATH`, and `LOG_ARCHIVE_DIR` to `/data` paths (the
`lateness-data` volume) and interpolates `SECRET_KEY` from the root `.env` file.
`compose.seed.yaml` optionally mounts `namelist.csv` read-only for the first-start seed.

### Data and Import rules

- **Master List (`namelist.csv`)** needs a `Bed` column and a name: either an explicit
  `Name` column (the app's export shape) or the roster's `Surname` / `Given Names` /
  `Common Name` columns, composed into `Common Surname Given`. It seeds an empty Master
  List on the first `init-db`; after that, manage the list in the Boarders tab.
- **Monthly Log CSV** needs at least `Name` and `Transaction Time` columns.
  `Transaction Time` must be strict `HH:MM` or `HH:MM:SS`; anything else is rejected with
  the offending rows surfaced, never silently dropped.
- Imports are capped at 16 MB; an over-cap Import is rejected and nothing is stored.
- A month report is saved only when the Import matched at least one known boarder with a
  parseable time; otherwise it is rejected with a reason and the database is left untouched.
- Each successful Import files the log and a Master List snapshot under `LOG_ARCHIVE_DIR`.
