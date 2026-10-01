# Lateness Application

Flask-based disciplinary reporting dashboard for tracking boarder lateness from CSV logs.

The app matches imported Monthly Logs against a boarder Master List, calculates lateness frequency and minutes late, stores month summaries in SQLite, and lets you view, search, download, and delete saved reports.

One designated, always-on Windows PC runs the app; every other staff PC just opens a browser to it. The database and Monthly Log Archive live on **that PC's local disk** — a single writer, so there is no SQLite-over-SMB corruption risk ([ADR 0004](docs/adr/0004-single-host-deployment.md)). A NAS is used as a **backup target only**.

## What you need

### Software on the host

- A Windows 10/11 PC to act as the **host** — always powered on during working hours.
- **Docker Desktop** installed on the host and **running** (the launcher can install it for you).
- **Docker Compose v2** (`docker compose`) — bundled with current Docker Desktop.

Normal use needs no Python on the host — everything runs in a container. Python is only needed for development (see [CONTRIBUTING.md](CONTRIBUTING.md)).

### Files to put in the project folder

A `git clone` brings the application code only. Some things the app expects are **gitignored — they are not in the repo** — so add them yourself:

| File / folder | Needed? | What it is |
| --- | --- | --- |
| `namelist.csv` | Yes for the default start | The seed Master List (a `Bed` column plus either a `Name` column or the roster's `Surname` / `Given Names` / `Common Name` columns). Put it in the project root. Without it the default start refuses to run; use `--no-seed` to skip it and import boarders in the app instead. |
| `.env` | Created for you | Holds the per-host `SECRET_KEY`. The launcher generates it on first start. To make it yourself, copy `.env.example` to `.env` and set `SECRET_KEY`. |
| `shared/backups/` | Only for backups | Backup output. Created automatically by `run.cmd backup`. |
| `shared/restore/` | Only for restores | Stage a `lateness-*` backup folder here before `run.cmd restore`. |

Optional:

- A **NAS share** (or another folder/drive) to hold backups. The runbooks assume `\\NAS\lateness-backups`.

> **In a hurry?** Start Docker Desktop → put `namelist.csv` in the project folder → run `run.cmd up` (PowerShell: `.\run.cmd up`) → open <http://127.0.0.1:8000>. No `namelist.csv` yet? Run `run.cmd up --no-seed` and import the Master List from the Boarders tab.

## Quick start (first-time setup)

### 1. Put the files on the host

Clone or copy the project to a stable path such as `C:\lateness-app`, then put your `namelist.csv` in that same folder. If you don't have a Master List yet, skip it for now and start with `.\run.cmd up --no-seed` (step 3), then import it from the Boarders tab.

### 2. Install Docker Desktop

If Docker Desktop is missing, the launcher offers a per-user `winget` install. If you prefer to do it yourself, install from <https://www.docker.com/products/docker-desktop/> and sign out/in or reboot if prompted.

### 3. Start the app

On Windows, double-click **`run.cmd`**, or run it from a terminal and choose **Start**. The command is `run.cmd up` in Command Prompt, or `.\run.cmd up` in PowerShell — not `./run …`:

```powershell
.\run.cmd                 # interactive menu
.\run.cmd up              # start and open the browser (office LAN)
.\run.cmd up --local      # start bound to 127.0.0.1 only
.\run.cmd up --no-seed    # start without seeding the Master List
```

`run.cmd` accepts bash-style flags such as `--local` and `--no-seed`; `run.ps1` uses the PowerShell spellings `-Local` and `-NoSeed`.

> **`--local` is not `--no-seed`.** `--local` only changes the network binding to loopback; it does **not** skip the `namelist.csv` requirement. To start without a Master List, use `--no-seed`. Combine them if you need both: `run.cmd up --local --no-seed`.

The launcher checks Docker, generates a per-host `SECRET_KEY` in `.env` if missing, builds and starts the stack, waits until it is healthy, and opens `http://127.0.0.1:8000`. By default it listens on all interfaces so other office PCs can reach it; use `--local` to keep it private.

### 4. Make it reachable on the office network

1. **Give the host a stable address.** On the router, reserve a DHCP lease for the host (or set a static IP), and give the PC a friendly name such as `lateness-host` so staff bookmark a name, not an IP.
2. **Open the firewall port** (admin PowerShell, scoped to the **Private**/office profile):

   ```powershell
   New-NetFirewallRule -DisplayName "Lateness app" -Direction Inbound `
     -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   ```

3. **Keep the host awake.** Settings → System → Power & sleep → set **Sleep** to **Never** while plugged in. Disable hybrid sleep / fast startup if staff must reach the app at any hour.
4. **Keep Docker running across reboots.** Set Docker Desktop to start on login and enable **auto-logon** (or leave the host logged in). Docker Desktop runs inside a signed-in user session, so the app is only up while that user is logged on.
5. **Confirm from another PC:** `http://lateness-host:8000/` (or `http://<host-ip>:8000/`).

> **Trust boundary:** the app is plain HTTP with no login. Any device on the office LAN can view and change the data, so keep it on the trusted LAN and do not expose it off-campus.

### 5. Load the Master List

The launcher seeds the Master List once, only on the first start, and only if `namelist.csv` is present. After that the file is never read again — all changes happen in the Boarders tab. If you don't have a `namelist.csv`, start with `.\run.cmd up --no-seed` and import the Master List from the Boarders tab (or add boarders by hand).

Adding boarders and Monthly Logs is done **in the browser**, not by copying files: use the Reports tab's Import and the Boarders tab's Master List Import.

### 6. Set up backups

Set up the NAS backup before you rely on the app — see [Backups and recovery](#backups-and-recovery).

### 7. Run the restore drill once

Prove a backup can be restored on this host, and note the date. See [Restore drill](docs/deployment/backup-and-restore.md#restore-drill).

## Backups and recovery

Backups run on the host (the only writer) and copy to the NAS. The NAS is storage only; it never holds the live database. A backup contains the database and the Monthly Log Archive (`logs/`), and is all-or-nothing: if any copy fails, the partial folder is removed so only complete backups are ever left on the share.

### Back up now

```powershell
.\run.cmd backup     # write a backup into .\shared\backups
```

Every run creates a timestamped folder `lateness-YYYYMMDD-HHMMSSffffff` under `shared\backups`. `BACKUP_KEEP` (default 7) sets how many timestamped folders to retain; older folders are deleted after a successful run.

### Mirror backups to the NAS

The launcher writes backups to `shared\backups` on the host. Mirror that folder to the NAS on a schedule.

1. **Create the share** `\\NAS\lateness-backups` and give the host write access. See [docs/deployment/nas-share.md](docs/deployment/nas-share.md) for permissions and vendor-specific steps.
2. **Schedule the backup + mirror.** Run these from an elevated Command Prompt, replacing the time and path as needed. `/IT` runs the task only while the host user is logged on — which is exactly when Docker Desktop is reachable:

   ```bat
   schtasks /Create /TN "Lateness backup" /SC DAILY /ST 21:00 /IT /TR "cmd /c \"C:\lateness-app\run.cmd backup && robocopy C:\lateness-app\shared\backups \\NAS\lateness-backups /MIR /R:2 /W:5\""
   ```

   Mirror again when the host user logs on (so a host rebooted after hours is backed up without waiting for the next evening):

   ```bat
   schtasks /Create /TN "Lateness backup (logon)" /SC ONLOGON /IT /TR "cmd /c \"C:\lateness-app\run.cmd backup && robocopy C:\lateness-app\shared\backups \\NAS\lateness-backups /MIR /R:2 /W:5\""
   ```

   `robocopy /MIR` mirrors the source exactly, so extra files on the NAS are removed — it holds the same newest `BACKUP_KEEP` folders as `shared\backups`.

3. **Verify the first run.** Run `run.cmd backup` and the `robocopy` command once by hand, confirm a `lateness-*` folder appears on the NAS, then check again after the scheduled task has fired.

### Restore

```powershell
.\run.cmd restore    # restore a backup from .\shared\restore
```

1. Copy a `lateness-*` folder from `shared\backups` into `shared\restore`.
2. Run `.\run.cmd restore`. The launcher stops the app, restores the database and archive, and restarts the app. You will be asked to confirm, since this replaces the live database.
3. Open the app and confirm a known month and its totals.

For rebuilding from the archive and the restore drill, see [docs/deployment/backup-and-restore.md](docs/deployment/backup-and-restore.md).

> Punishments live **only** in the database, not in the CSVs. Protect the database copies accordingly.

## Using the application

The app imports Monthly Log CSVs, matches them against the Master List, and stores monthly reports you can review, search, download, or delete.

1. Go to the Reports tab.
2. Import a Monthly Log CSV file.
3. Enter a month label such as `2026-03`.
4. Save the report.
5. Use the month cards to view, download, or delete saved reports.
6. Use **Find a Boarder** to look up any boarder's all-time history and Points trend chart.
7. Open a month report and click **Assign Punishments** to issue punishments to boarders who were late; set a deadline, then track status transitions (completed, overdue, voided) on the Punishments tab.
8. Use the **Statistics** tab to view house-wide analytics: top-N boarders, repeat-offender watchlist, and Points distribution.
9. Use the **Boarders** tab to view, add, edit, or remove boarders, import a CSV Master List, or download the current one.

## Updating the app

On the Docker host, pull the new code and rebuild. Your data lives in the `lateness-data` volume, so it is preserved.

```powershell
.\run.cmd down
git pull
.\run.cmd up
```

The launcher rebuilds the image on every start, so no extra flag is needed. Afterwards open the app and load one Monthly Report to confirm the new code is live. On a native Windows host, follow [docs/deployment/windows-host.md](docs/deployment/windows-host.md) instead.

## Troubleshooting

- **App starts but no boarders are found, or the container cannot find `namelist.csv` on first startup** — confirm `namelist.csv` is in the project root with at least `Name` and `Bed` columns, or start with `.\run.cmd up --no-seed` and add boarders through the Boarders tab (`NAMELIST_PATH` points at the file).
- **Changes do not persist after a restart** — confirm the container has the `lateness-data` volume mounted (`docker volume ls`). Data lives in a named volume, not a folder in the repo.
- **The app is unreachable on port 8000** — another process may already hold the port. Set `APP_PORT` in `.env` to a free port (and allow that port through the firewall), then retry.
- **The launcher reports Docker is not running** — start Docker Desktop and retry.
- **Other office PCs cannot reach the app** — confirm the firewall rule (step 4), that `BIND_ADDR` is not `127.0.0.1`, and that you are using the host's LAN address.
- **Backups are not appearing on the NAS** — confirm the scheduled task ran while the host user was logged on, and that the task's account can write to the share. Test the `robocopy` line by hand from the host.

## Alternative: native Windows host (waitress + NSSM)

Instead of Docker, the designated host can run the app directly with waitress (`serve.py`) as a Windows service via [NSSM](https://nssm.cc/). This avoids Docker Desktop but adds manual steps: a Python venv, machine environment variables including `SECRET_KEY`, and the service install. Follow [docs/deployment/windows-host.md](docs/deployment/windows-host.md). Use a UNC path (`\\NAS\share\...`) anywhere the service must reach the NAS; a mapped drive letter is per-user and absent for a service.

## Reference

### Configuration

Recognized environment variables:

| Variable | Purpose | Default |
|---|---|---|
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

`compose.yaml` fixes `DB_PATH`, `NAMELIST_PATH`, and `LOG_ARCHIVE_DIR` to `/data` paths (the `lateness-data` volume) and interpolates `SECRET_KEY` from the root `.env` file. `compose.seed.yaml` optionally mounts `namelist.csv` read-only for the first-start seed.

Under Docker, the root `.env` file is read by **Docker Compose only** — the launcher creates it and fills in `SECRET_KEY`. The native Windows host has no `.env` loader: use real environment variables. To generate a secret yourself:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### Data and Import rules

- **Master List (`namelist.csv`)** needs a `Bed` column and a name: either an explicit `Name` column (the app's export shape) or the roster's `Surname` / `Given Names` / `Common Name` columns, which the importer composes into `Common Surname Given` (e.g. `Jason FONG Pak Hin`). It seeds an empty Master List on the first `init-db`; after that, manage the list in the Boarders tab.
- **Monthly Log CSV** needs at least `Name` and `Transaction Time` columns. `Transaction Time` must be strict `HH:MM` or `HH:MM:SS` (24-hour); anything else is rejected with the offending rows surfaced, never silently dropped.
- Imports are capped at 16 MB (`MAX_CONTENT_LENGTH`); an over-cap Import is rejected with a staff-readable error and nothing is stored.
- A month report is saved only when the Import matched at least one known boarder with a parseable time. Otherwise the Import is rejected with a specific reason and the database is left untouched.
- Each successful Import files the log and a Master List snapshot under `LOG_ARCHIVE_DIR` (default `data/logs`), so Monthly Reports can be rebuilt from the archive.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, tests, and conventions, and [docs/architecture.md](docs/architecture.md) for the module map.
