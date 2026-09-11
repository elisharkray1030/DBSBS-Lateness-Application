# Lateness Application

Flask-based disciplinary reporting dashboard for tracking boarder lateness from CSV logs.

The app matches imported Monthly Logs against a boarder Master List, calculates lateness frequency and minutes late, stores month summaries in SQLite, and lets you view, search, download, and delete saved reports.

## Requirements

- **Normal use:** Docker Desktop. The launcher runs everything in a container.
- **Development only:** Python 3.11+ (CI tests 3.11 and 3.12; Docker uses 3.12-slim).

## Run it

One command runs the app in a container, isolated from the rest of your machine. The database and Monthly Log Archive live in a Docker volume (`lateness-data`); backups and restores use the `shared/` folder.

On Windows, double-click **`run.cmd`** (or run it from a terminal). On macOS/Linux/WSL run **`./run.sh`**. With no arguments, both show a menu.

```powershell
.\run.cmd            # menu
.\run.cmd up         # start and open the browser (office LAN)
.\run.cmd up --local # start bound to 127.0.0.1 only
.\run.cmd backup     # write a backup into .\shared\backups
.\run.cmd restore    # restore a backup from .\shared\restore
```

```bash
./run.sh            # menu
./run.sh up         # start and open the browser (office LAN)
./run.sh up --local # start bound to 127.0.0.1 only
./run.sh backup     # write a backup into ./shared/backups
./run.sh restore    # restore a backup from ./shared/restore
```

The launcher checks Docker (offering a per-user install if missing), generates a per-host `SECRET_KEY` in `.env`, starts the stack, waits until it is healthy, and opens `http://127.0.0.1:8000`. By default it listens on all interfaces so other office PCs can reach it at `http://<host-ip>:8000`; use `--local` to keep it private.

Adding boarders and Monthly Logs is done **in the browser**, not by copying files: use the Reports tab's Import and the Boarders tab's Master List Import. The `shared/` folder is only for backups and restores — see [shared/README.txt](shared/README.txt).

If `namelist.csv` is present in the project root, the launcher seeds the Master List from it once on the first start; if it is absent, import the Master List in the Boarders tab.

### Sharing it with the office

1. Give the host PC a static IP (a DHCP reservation) so the URL never changes.
2. Allow inbound TCP 8000 through Windows Firewall (Docker may prompt the first time).
3. Keep Docker Desktop running on the host and set the PC not to sleep.

Everything is plain HTTP with no login; keep it on the trusted office LAN and off-campus.

Stop, inspect, and recover with `run.cmd down`, `logs`, and `status`. Data lives in the named `lateness-data` Docker volume, not in the repo; back up with `run.cmd backup` and restore with `run.cmd restore` (see [shared/README.txt](shared/README.txt)).

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

## Data and Import rules

**Input files**

- `namelist.csv` (the seed Master List) should contain at least `Name` and `Bed` columns. It seeds an empty Master List on the first `init-db`; after that, manage the list through the Boarders tab.
- Monthly Log CSV files should contain at least `Name` and `Transaction Time` columns.
- `Transaction Time` values must be strict `HH:MM` or `HH:MM:SS` (24-hour) times. Anything else is rejected with the offending rows surfaced, never silently dropped.
- Imports are capped at 16 MB (`MAX_CONTENT_LENGTH`, in bytes). An Import over the cap is rejected with a staff-readable error and nothing is stored.
- The SQLite database file is created automatically on first run if it does not already exist.

**What gets saved**

- A month report is saved only when the Import produced at least one row for a known boarder with a parseable time.
- Imports that match nothing, or whose times can't be read, are rejected with a specific error (Master List missing/empty, no rows matched, or all times unparseable) and leave the database untouched. A clean month with matched rows saves normally.
- A successful Import reports how many boarders were recorded and how many had lateness, plus counts of unmatched or unparseable rows when present, so staff can correct bad source data.
- The request stream is consumed directly by the ingestion module — it is never written to a temp file on disk.
- The web Import and the parser CLI run the exact same ingestion module, so the two surfaces can't drift apart.

**Seed semantics**

On first database preparation — `init-db`, which `serve.py` and the Docker image run automatically before serving and which local development runs manually — if the boarders table is empty and a `namelist.csv` exists at `NAMELIST_PATH`, the app seeds the table from that file once, then sets a seed flag in a `meta` table. After that the file is no longer read — all changes happen through the Boarders tab or a CSV import. If the Master List is later emptied (every boarder removed), it stays empty across restarts; the seed never runs again. A fresh start with no `namelist.csv` forfeits the one-time seed — a `namelist.csv` appearing later never silently seeds a list you did not ask for.

**Monthly Log Archive**

Each successful Import files the Monthly Log and a Master List snapshot under `LOG_ARCHIVE_DIR` (default `data/logs`): the log as `<YYYY-MM>.csv` and a `namelist-<YYYY-MM>.csv` snapshot of the list that produced the report. Both are written atomically, and a re-Import overwrites that month. A rejected Import writes nothing. Monthly Reports can be rebuilt from the archive.

## Configuration

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

## Deployment

One designated, always-on PC runs the app; staff reach it over the office LAN. The SQLite database and the Monthly Log Archive live on that PC's **local disk** — a single writer, so there is no SQLite-over-SMB corruption risk. The NAS is a **backup target only** ([ADR 0004](docs/adr/0004-single-host-deployment.md)).

- **Docker (canonical):** `compose.yaml` runs gunicorn with the database and archive in the named `lateness-data` volume. The [launcher scripts](#run-it) are the one-command entry point; `BIND_ADDR=0.0.0.0` (the default) exposes it to the office LAN.
- **Windows host (native):** run `serve.py` (waitress) as a service, bound to the LAN. Follow [docs/deployment/windows-host.md](docs/deployment/windows-host.md).
- **Backups:** `backup_db.py` copies the database plus the archived Monthly Logs and their per-month Master List snapshots to the NAS. Follow [docs/deployment/backup-and-restore.md](docs/deployment/backup-and-restore.md) and [docs/deployment/nas-share.md](docs/deployment/nas-share.md).
- **Trust boundary:** office LAN only, plain HTTP, no auth, no `Secure` cookies. Do not expose it off-campus.

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
| [data/](data/) | Local runtime data (SQLite database and Monthly Log Archive) when not using Docker |
| [shared/](shared/README.txt) | Host bridge for Docker backups and restores (gitignored except its README) |
| [namelist.csv](namelist.csv) | Seed Master List used for first-start matching (local-only: gitignored for privacy, not in the repo) |
| [CONTEXT.md](CONTEXT.md) | Domain glossary and language |
| [AGENTS.md](AGENTS.md) | Contributor and agent instructions |
| [pyproject.toml](pyproject.toml) | mypy and pytest configuration |
| [docs/](docs/architecture.md) | Architecture note, ADRs, deployment runbooks, specs, and reviews |
| [.env.example](.env.example) | Docker Compose environment template; copy to `.env` and set `SECRET_KEY` |
| `lateness_history.db` | Default local SQLite database (created on first run; gitignored) |

## Troubleshooting

- If the app starts but no boarders are found, confirm `namelist.csv` has the expected column names on first start, or add boarders through the Boarders tab.
- If Docker Compose starts but changes do not persist, confirm the container has the `lateness-data` volume mounted (`docker volume ls`). Data lives in a named volume, not a folder in the repo.
- If the container cannot find `namelist.csv` on first startup, confirm the file exists in the project root and that `NAMELIST_PATH` points to it; you can also add boarders through the Boarders tab without a seed file.
- If the app is unreachable on port 8000, another process may already hold the port — set `APP_PORT` to a free port and try again.
- If the launcher reports that Docker is not running, start Docker Desktop and retry.
