# Lateness Application

Flask-based disciplinary reporting dashboard for tracking boarder lateness from CSV logs.

The app matches imported Monthly Logs against a boarder Master List, calculates lateness frequency and minutes late, stores month summaries in SQLite, and lets you view, search, download, and delete saved reports.

One designated, always-on PC runs the app; every other staff PC just opens a browser to it. The database and Monthly Log Archive live on **that PC's local disk** — a single writer, so there is no SQLite-over-SMB corruption risk ([ADR 0004](docs/adr/0004-single-host-deployment.md)). A NAS is used as a **backup target only**.

## What you need

- A Windows PC to act as the **host** — always powered on during working hours.
- **Docker Desktop** installed on the host (the launcher can install it for you).
- A **`namelist.csv`** Master List (at least `Name` and `Bed` columns). Put it in the project root before the first start, or import it later from the Boarders tab.
- A **NAS share** (or another folder/drive) to hold backups. The runbooks assume `\\NAS\lateness-backups`.

Normal use needs no Python on the host — everything runs in a container. Python is only needed for development.

## Quick start: set up the host (one time)

### 1. Put the files on the host

Put the project at a stable path such as `C:\lateness-app`, and put `namelist.csv` in that folder.

### 2. Install Docker Desktop

If Docker Desktop is missing, the launcher offers a per-user `winget` install. If you prefer to do it yourself, install from <https://www.docker.com/products/docker-desktop/> and sign out/in or reboot if prompted.

### 3. Start the app

On Windows, double-click **`run.cmd`**, or run it from a terminal, and choose **Start**:

```powershell
.\run.cmd            # menu
.\run.cmd up         # start and open the browser (office LAN)
.\run.cmd up --local # start bound to 127.0.0.1 only
```

On macOS/Linux/WSL, use **`./run.sh`** with the same commands.

`run.cmd` and `run.sh` accept bash-style flags such as `--local` and `--no-seed`; `run.ps1` uses the PowerShell spellings `-Local` and `-NoSeed`. `--no-seed` skips seeding the Master List from `namelist.csv` on first start.

The launcher checks Docker, generates a per-host `SECRET_KEY` in `.env`, builds and starts the stack, waits until it is healthy, and opens `http://127.0.0.1:8000`. By default it listens on all interfaces so other office PCs can reach it; use `--local` to keep it private.

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

If `namelist.csv` is in the project root, the launcher seeds the Master List once on the first start. If you don't have one yet, start with `.\run.cmd up --no-seed` and import the Master List from the Boarders tab (or add boarders by hand). See [Seed semantics](#seed-semantics) for what "seed once" means.

Adding boarders and Monthly Logs is done **in the browser**, not by copying files: use the Reports tab's Import and the Boarders tab's Master List Import.

### 6. Set up backups

Set up the NAS backup before you rely on the app — see [Backups and recovery](#backups-and-recovery).

### 7. Run the restore drill once

Prove a backup can be restored on this host, and note the date. See [Restore drill](#restore-drill).

## Backups and recovery

Backups run on the host (the only writer) and copy to the NAS. The NAS is storage only; it never holds the live database.

### Back up now

```powershell
.\run.cmd backup     # write a backup into .\shared\backups
```

Every run creates a timestamped folder `lateness-YYYYMMDD-HHMMSSffffff` under `shared\backups`. `BACKUP_KEEP` (default 7) sets how many timestamped folders to retain; older folders are deleted after a successful run. The launcher handles the container plumbing for you.

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

On a **native Windows host** (waitress, no Docker) `backup_db.py --dest "\\NAS\lateness-backups"` writes straight to the UNC share; see [docs/deployment/backup-and-restore.md](docs/deployment/backup-and-restore.md).

### Restore

```powershell
.\run.cmd restore    # restore a backup from .\shared\restore
```

1. Copy a `lateness-*` folder from `shared\backups` into `shared\restore`.
2. Run `.\run.cmd restore`. The launcher stops the app, restores the database and archive, and restarts the app. You will be asked to confirm, since this replaces the live database.
3. Open the app and confirm a known month and its totals.

On the native host, `restore_db.py` does the same; see [docs/deployment/backup-and-restore.md](docs/deployment/backup-and-restore.md).

### What a backup contains

- `lateness_history.db` — a consistent copy taken with SQLite's **online backup API**. A raw file copy of a live database can capture a half-written page; the backup API copies under the database lock instead, so it is safe while staff use the app.
- `logs/` — every CSV in the Monthly Log Archive: each imported Monthly Log (`<YYYY-MM>.csv`), paired with a `namelist-<YYYY-MM>.csv` Master List snapshot of the roster as it stood for that Import.

A backup is all-or-nothing: if any copy fails, the partial folder is removed so only complete backups are ever left on the share.

> Punishments live **only** in the database, not in the CSVs. Protect the database copies accordingly.

### Rebuild (database lost, archive kept)

1. Stop the app and start from a fresh database. Starting the app runs `init-db`, which seeds the Master List from `NAMELIST_PATH`. Copy the newest `logs\namelist-<YYYY-MM>.csv` over that path first, so the rebuild starts from the roster of the latest month you hold.
2. Re-import each `logs\<YYYY-MM>.csv` on the Import page, using the month in the filename. Each Import also rewrites its `namelist-<YYYY-MM>.csv` snapshot.
3. Punishments are not recoverable this way and must be re-issued.

### Restore drill

Run this once after setup and note the date in your change log:

1. Take a backup (`.\run.cmd backup`).
2. Copy the newest `lateness-*` folder from `shared\backups` into `shared\restore`.
3. Run `.\run.cmd restore`.
4. Open the app and confirm a known month and its totals.
5. Record the date the drill passed.

The repeatable half of this is covered by `tests/test_backup_db.py` and `tests/test_restore_db.py`, which reopen a backup database through the storage seam and restore one into a live path.

## Using the application

What it does:

- Import Monthly Log CSV files from the web UI
- Match boarder names against the canonical Master List
- Calculate lateness frequency, total minutes late, and total points
- Save month summaries in SQLite for later review
- Search historical boarder records by name
- View, download, and delete saved month reports
- Assign punishments to boarders with deadline and status tracking (pending, completed, overdue, voided)
- View a Statistics / House Dashboard with house-wide trend chart, top-N boarders, repeat-offender watchlist, and Points distribution histogram
- Look up any boarder's profile with all-time history and a Points trend chart
- Manage the Master List through the Boarders tab (view, add, edit, remove, import CSV, export CSV)

How to use it:

1. Go to the Reports tab.
2. Import a Monthly Log CSV file.
3. Enter a month label such as `2026-03`.
4. Save the report.
5. Use the month cards to view, download, or delete saved reports.
6. Use **Find a Boarder** to look up any boarder's all-time history and Points trend chart.
7. Open a month report and click **Assign Punishments** to issue punishments to boarders who were late; set a deadline, then track status transitions (completed, overdue, voided) on the Punishments tab.
8. Use the **Statistics** tab to view house-wide analytics: top-N boarders, repeat-offender watchlist, and Points distribution.
9. Use the **Boarders** tab to view, add, edit, or remove boarders, import a CSV Master List, or download the current one.

Printing is intentional for Monthly Reports: with a report open, Print (or Ctrl/Cmd+P) outputs exactly that report — the application chrome, other tabs' content, and empty report skeletons are excluded.

## Updating the app

On the Docker host, pull the new code and rebuild. Your data lives in the `lateness-data` volume, so it is preserved.

```powershell
.\run.cmd down
git pull
.\run.cmd up
```

The launcher rebuilds the image on every start, so no extra flag is needed.

Afterwards open the app and load one Monthly Report to confirm the new code is live. On a native Windows host, follow [docs/deployment/windows-host.md](docs/deployment/windows-host.md) instead.

## Troubleshooting

- **App starts but no boarders are found** — confirm `namelist.csv` has the expected column names on first start, or add boarders through the Boarders tab.
- **Changes do not persist after a restart** — confirm the container has the `lateness-data` volume mounted (`docker volume ls`). Data lives in a named volume, not a folder in the repo.
- **Container cannot find `namelist.csv` on first startup** — confirm the file exists in the project root and that `NAMELIST_PATH` points to it; you can also add boarders through the Boarders tab without a seed file.
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

#### Input files

- `namelist.csv` (the seed Master List) should contain at least `Name` and `Bed` columns. It seeds an empty Master List on the first `init-db`; after that, manage the list through the Boarders tab.
- Monthly Log CSV files should contain at least `Name` and `Transaction Time` columns.
- `Transaction Time` values must be strict `HH:MM` or `HH:MM:SS` (24-hour) times. Anything else is rejected with the offending rows surfaced, never silently dropped.
- Imports are capped at 16 MB (`MAX_CONTENT_LENGTH`, in bytes). An Import over the cap is rejected with a staff-readable error and nothing is stored.
- The SQLite database file is created automatically on first run if it does not already exist.

#### What gets saved

- A month report is saved only when the Import produced at least one row for a known boarder with a parseable time.
- Imports that match nothing, or whose times can't be read, are rejected with a specific error (Master List missing/empty, no rows matched, or all times unparseable) and leave the database untouched. A clean month with matched rows saves normally.
- A successful Import reports how many boarders were recorded and how many had lateness, plus counts of unmatched or unparseable rows when present, so staff can correct bad source data.
- The request stream is consumed directly by the ingestion module — it is never written to a temp file on disk.
- The web Import and the parser CLI run the exact same ingestion module, so the two surfaces can't drift apart.

#### Seed semantics

On first database preparation — `init-db`, which `serve.py` and the Docker image run automatically before serving and which local development runs manually — if the boarders table is empty and a `namelist.csv` exists at `NAMELIST_PATH`, the app seeds the table from that file once, then sets a seed flag in a `meta` table. After that the file is no longer read — all changes happen through the Boarders tab or a CSV import. If the Master List is later emptied (every boarder removed), it stays empty across restarts; the seed never runs again. A fresh start with no `namelist.csv` forfeits the one-time seed — a `namelist.csv` appearing later never silently seeds a list you did not ask for.

#### Monthly Log Archive

Each successful Import files the Monthly Log and a Master List snapshot under `LOG_ARCHIVE_DIR` (default `data/logs`): the log as `<YYYY-MM>.csv` and a `namelist-<YYYY-MM>.csv` snapshot of the list that produced the report. Both are written atomically, and a re-Import overwrites that month. A rejected Import writes nothing. Monthly Reports can be rebuilt from the archive.

## Development

- Install Python 3.11+ (CI tests 3.11 and 3.12; Docker uses 3.12-slim).
- Install runtime dependencies in the environment you will run the app from: `python -m pip install -r requirements.txt`. On Windows, if `python3` points at the Microsoft Store stub instead of a real interpreter, use `py -3 -m pip install -r requirements.txt`.
- Install dev dependencies (pytest, mypy, playwright, and types-waitress, pinned in `requirements-dev.txt`) with `python -m pip install -r requirements-dev.txt`.
- Set `SECRET_KEY` (required: the app aborts at startup without one). `flask run` does not read `.env` (python-dotenv is not installed), so set it directly. Generate a per-host secret, then export it:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

```bash
# macOS / Linux
export SECRET_KEY="<the generated secret>"
```

```powershell
# Windows PowerShell
$env:SECRET_KEY = "<the generated secret>"
```

- Prepare the database (first start only; a safe no-op afterwards):

```bash
python -m flask --app app init-db
```

- Start the app for local development, then open `http://127.0.0.1:5000/`:

```bash
python -m flask --app app run
```

- Run the test suite: `python -m pytest tests`. It covers the ingestion and storage seams, the Flask test-client seam, and the Playwright browser seam (synthetic CSVs and an in-memory SQLite connection; browser tests need `python -m playwright install chromium` and skip automatically when it is unavailable).
- Typecheck: `python -m mypy app.py parser.py storage.py records.py punishments.py seed_demo_data.py serve.py backup_db.py restore_db.py defaults.py` (config in `pyproject.toml`; CI runs both pytest and mypy on push/PR).
- Run a quick parser check with `python parser.py`: it streams `namelist.csv` plus `test_data.csv` through the same ingestion module the web Import uses, writes `lateness_final_report.csv`, and prints diagnostics (rows read, matched rows, unmatched names, unparseable rows). Both files are local samples (`test_data.csv` is gitignored and not in the repo), so supply them first. The web route and the CLI share one ingestion path, so they can't drift.
- Seed demo data with `python seed_demo_data.py` — deterministic months (January through August, excluding June). Optional flags: `python seed_demo_data.py [--db PATH] [--namelist PATH] [--log-dir PATH]`. Under Docker, run the equivalent with `docker compose run --rm seed`.

Architectural invariants:

- The lateness window is hard-coded in `parser.py`.
- Lateness frequency, total minutes late, and total points are computed once in the ingestion module and carried on the typed boarder record; the month view, the download, and the CSV export all use that one definition.
- The CSV export, the month download, and `export_to_csv` all share the single CSV writer in `parser.py`, so their output is identical.
- The app currently uses SQLite, so it is well suited to a single-user or small-team deployment.

See [docs/architecture.md](docs/architecture.md) for how the modules fit together.

## Project layout

| Path | What it is |
|---|---|
| [app.py](app.py) | Flask app and routes; a thin adapter over the ingestion and storage seams |
| [parser.py](parser.py) | The shared ingestion module (parse → decide → persist → message), the single CSV writer, and the parser CLI |
| [storage.py](storage.py) | SQLite persistence behind an injectable connection seam |
| [records.py](records.py) | The typed boarder record shared by ingestion, storage, the CSV writer, and the JSON body |
| [punishments.py](punishments.py) | Punishment assignment, status transitions, deadline/overdue rules |
| [defaults.py](defaults.py) | Built-in configuration defaults shared by the app and host tooling |
| [seed_demo_data.py](seed_demo_data.py) | Deterministic demo seed script (Jan–Aug, no June) |
| [serve.py](serve.py) | Windows host entry point (waitress) |
| [start-windows.ps1](start-windows.ps1) | PowerShell launcher for the Windows host (run after setting `SECRET_KEY`) |
| [backup_db.py](backup_db.py) | Host backup script (SQLite online copy plus archived Monthly Logs and Master List snapshots) |
| [restore_db.py](restore_db.py) | Host restore script (puts a backup's database and archive back in place) |
| [run.cmd](run.cmd) / [run.ps1](run.ps1) / [run.sh](run.sh) | One-command Docker launcher (Windows / PowerShell / Linux-macOS-WSL) |
| [compose.yaml](compose.yaml) | Docker Compose service definition (app plus profiled backup/restore/seed tooling) |
| [compose.seed.yaml](compose.seed.yaml) | Optional override that seeds the Master List from `namelist.csv` on first start |
| [Dockerfile](Dockerfile) | Container image definition |
| [requirements.txt](requirements.txt) | Runtime dependencies |
| [requirements-dev.txt](requirements-dev.txt) | Development dependencies (pytest, mypy, playwright, types-waitress); includes runtime deps |
| [templates/](templates/) | Jinja templates: base layout and tabs, main panels, dashboard, boarder profile, macros, error pages |
| [static/app.js](static/app.js) | Browser-side behaviour (table sorting, charts, punishment actions) |
| [tests/](tests/) | pytest suite covering the ingestion and storage seams, Flask test-client routes, and Playwright browser tests (browser tests skip automatically when Playwright is unavailable) |
| data/ | Local runtime data when not using Docker (Monthly Log Archive and parser scratch files); gitignored |
| [shared/](shared/README.txt) | Host bridge for Docker backups and restores (gitignored except its README) |
| namelist.csv | Seed Master List used for first-start matching (local-only: gitignored for privacy, not in the repo) |
| [CONTEXT.md](CONTEXT.md) | Domain glossary and language |
| [AGENTS.md](AGENTS.md) | Contributor and agent instructions |
| [pyproject.toml](pyproject.toml) | mypy and pytest configuration |
| [docs/](docs/) | Architecture note, ADRs, deployment runbooks, specs, and reviews |
| [.env.example](.env.example) | Docker Compose environment template; copy to `.env` and set `SECRET_KEY` |
| `lateness_history.db` | Default local SQLite database (created on first run; gitignored) |
