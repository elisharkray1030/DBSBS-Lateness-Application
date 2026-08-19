# Lateness Application

Flask-based disciplinary reporting dashboard for tracking boarder lateness from CSV logs.

The app matches uploaded monthly attendance logs against a boarder master list, calculates lateness frequency and minutes late, stores month summaries in SQLite, and lets you view, search, download, and delete saved reports.

## What it does

- Upload monthly CSV attendance logs from the web UI
- Match boarder names against a canonical master list
- Calculate lateness frequency, total minutes late, and total points
- Save month summaries in SQLite for later review
- Search historical boarder records by name
- View, download, and delete saved month reports

## Project Layout

- [app.py](app.py) - Flask app and routes; a thin adapter over the ingestion and storage seams
- [parser.py](parser.py) - the shared ingestion module (parse -> decide -> persist -> message), the single CSV writer, and the parser CLI
- [storage.py](storage.py) - SQLite persistence behind an injectable connection seam
- [records.py](records.py) - the typed boarder record shared by ingestion, storage, the CSV writer, and the JSON body
- [templates/index.html](templates/index.html) - dashboard UI
- [tests/](tests/) - pytest suite covering the ingestion and storage seams (no server or browser needed)
- [namelist.csv](namelist.csv) - master boarder list used for matching (local-only: gitignored for privacy, not in the repo)
- [requirements.txt](requirements.txt) - runtime dependencies
- [requirements-dev.txt](requirements-dev.txt) - development dependencies (pytest), includes runtime deps
- [Dockerfile](Dockerfile) - container image definition

## Setup From Scratch

### Local Python setup

1. Install Python 3.9+.
2. Open a terminal in the project folder.
3. Install dependencies in the Python environment you will use to run the app:

```bash
python -m pip install -r requirements.txt
```

On Windows, `python3` may point to the Microsoft Store stub instead of a real interpreter. If that happens, use `py -3 -m pip install -r requirements.txt` instead.

4. Start the app:

```bash
python -m flask --app app run
```

If you are using the Windows launcher, `py -3 -m flask --app app run` is also a safe option.

On Windows, you can also run the bundled launcher script from the project root:

```powershell
./start-windows.ps1
```

5. Open `http://127.0.0.1:5000/` in your browser.

### Docker setup

1. Install and start Docker Desktop.
2. Keep `namelist.csv` in the project root.
3. Start the stack:

```bash
docker compose up -d --build
```

4. Open `http://127.0.0.1:8000/` in your browser.

5. Stop the stack when you are done:

```bash
docker compose down
```

## Updating the boarder list

The boarder master list lives in the SQLite database (`boarders` table). The **Boarders** tab lets staff view, add, edit, and remove boarders inline, replace the whole roster by uploading a CSV, and download the current roster as a CSV.

On first startup, if the boarders table is empty and a `namelist.csv` exists at `NAMELIST_PATH`, the app seeds the table from that file once, then sets a seed flag in a `meta` table. After that the file is no longer read — all changes happen through the Boarders tab or a CSV upload. If the roster is later emptied (every Boarder deleted), it stays empty across restarts; the seed never runs again. A fresh start with no `namelist.csv` forfeits the one-time seed — a `namelist.csv` appearing later never silently seeds a roster you did not ask for. (Deployments that emptied every Boarder before this change get one final seed on the first restart after upgrading, then stay stable thereafter.)

If you are using Docker Compose, the root `namelist.csv` is only consulted for that initial seed; edits made in the app persist in the mounted database volume and survive restarts. You do not need to rebuild the image because the app reads the path from `NAMELIST_PATH`.

## Using the application

1. Go to the Reports tab.
2. Upload a monthly CSV log file.
3. Enter a month label such as `2026-03`.
4. Save the report.
5. Use the month cards to view, download, or delete saved reports.
6. Use the History tab to search boarder records by name.

## Data expectations

- `namelist.csv` should contain at least `Name` and `Bed` columns. It is read once at first startup to seed an empty boarders table; after that, manage the boarder list through the Boarders tab.
- Monthly log CSV files should contain at least `Name` and `Transaction Time` columns.
- `Transaction Time` values must be strict `HH:MM` or `HH:MM:SS` (24-hour) times. Anything else is rejected with the offending rows surfaced, never silently dropped.
- The SQLite database file is created automatically on first run if it does not already exist.

## Upload behaviour

A month report is only saved when the uploaded log produced at least one row for a known boarder with a parseable time. Uploads that match nothing, or whose times can't be read, are rejected with a specific error (master list missing/empty, no rows matched, or all times unparseable) and leave the database untouched. A clean month with matched rows still saves normally. A successful upload reports how many boarders were recorded. The upload stream is consumed directly by the ingestion module - it is never written to a temp file on disk.

The web upload and the parser CLI run the exact same ingestion module, so the two surfaces can't drift apart.

## Persistence and deployment notes

- The app stores month summaries in SQLite using the path from `DB_PATH`.
- The boarder master list is stored in the same SQLite database and managed through the Boarders tab; `namelist.csv` is read once at first startup to seed an empty boarders table.
- For Docker, keep the database file in a mounted folder so reports and the boarder list survive container restarts.
- Monthly uploads are consumed directly from the request stream and are never written to disk or stored permanently by the app.

## Development notes

- Install dev dependencies (pytest, mypy) with `python -m pip install -r requirements-dev.txt`.
- Run `python -m pytest tests` to run the suite across the ingestion and storage seams (synthetic CSVs and an in-memory SQLite connection, no server or browser required).
- Run `python -m mypy app.py parser.py storage.py records.py punishments.py` for typechecking.
- Run `python parser.py` for a quick parser check: it streams `namelist.csv` plus `test_data.csv` through the same ingestion module the web upload uses, writes `lateness_final_report.csv`, and prints the diagnostics (rows read, matched rows, unmatched names, unparseable rows). The web route and the CLI share one ingestion path, so they can't drift.
- The lateness window is hard-coded in `parser.py`.
- Lateness frequency, total minutes late, and total points are computed once in the ingestion module and carried on the typed boarder record; the month view, the download, and the CSV export all use that one definition.
- The CSV export, the month download, and `export_to_csv` all share the single CSV writer in `parser.py`, so their output is identical.
- The app currently uses SQLite, so it is well suited to a single-user or small-team deployment.

## Files and Components

### `app.py` - Flask application and routes

`app.py` exposes the web routes for uploading logs, searching history, rendering the dashboard, returning month JSON data, serving CSV downloads, and deleting month records. It is a thin adapter: each route opens a file-backed connection, delegates to the ingestion and storage modules, and renders the outcome. No storage or parsing logic lives here.

Important behavior:

- Each route opens its own connection and passes it into the storage functions; nothing reads a `DB_PATH` module global inside the storage layer.
- The upload route forwards the request stream straight into `ingest_log` - no temp file on disk.
- `serialize_boarders()` derives the JSON body from the shared `BoarderRecord`, so the wire format matches the stored rows and the CSV writer.

### `parser.py` - the shared ingestion module, CSV writer, and CLI

`parser.py` owns the whole of monthly-report ingestion: parse rows -> decide reject-or-save -> persist -> build the message. The web route and the parser CLI both call `ingest_log`, so the two surfaces share one path and can't drift. It is also the single CSV writer, and it hosts the CLI (`python parser.py`).

Important behavior:

- `load_namelist(namelist_filename)` reads the master list and normalizes names; returns `None` if the file is missing.
- `ingest_log(log_stream, month_label, master_list, conn)` takes the log as a stream, the month label, the namelist, and a history store connection, and returns one outcome - either the report saved, or rejected with an exact reason. A rejected ingestion leaves the store untouched.
- `SavedOutcome` carries the saved `BoarderRecord` list plus diagnostics (rows read, matched rows, unmatched names, unparseable rows) and builds the user-facing message.
- `RejectedOutcome` carries the exact rejection reason (master list missing/empty, empty log, no rows matched any boarder, or no parseable time).
- `boarders_to_csv(boarders)` renders a boarder record list to CSV text (used by the download route and the export).
- `export_to_csv(output_filename, boarders)` writes the results to a CSV file.
- `cli_ingest` / `cli_main` run the shared ingestion path over a log file.

### `storage.py` - SQLite persistence behind an injectable connection seam

`storage.py` owns all persistence. Every function takes the connection, so the same module works against a file-backed connection in production and an in-memory (`:memory:`) connection in tests. No storage function reads a `DB_PATH` module global.

Important behavior:

- `create_schema(conn)` creates the `boarder_history` table if it does not exist.
- `save_month(conn, boarders, month_label)` upserts each boarder row by month.
- `list_months(conn)` returns the month summaries used in the UI (month label, boarder count, total minutes late), ordered newest-first.
- `get_month_report(conn, month_label)` returns one month's stored `BoarderRecord` rows.
- `search_history(conn, name_query)` performs a partial match against stored names.
- `delete_month(conn, month_label)` removes a month and returns the deleted row count.

### `records.py` - the typed boarder record

`records.py` defines the `BoarderRecord` (named fields for bed, frequency, total minutes late, total points, plus the normalized boarder name) once, shared by the ingestion module, the CSV writer, the storage module, and the JSON body. It also holds the `UnparsedTimeRow` and `HistoryEntry` records.

### `templates/index.html` - User interface and client scripting

The template renders the dashboard, search tab, month cards, month detail table, delete confirmation modal, and client-side sorting and fetch behavior.

## Troubleshooting

- If the app starts but no boarders are found, confirm that the `namelist.csv` file has the expected column names, or add boarders through the Boarders tab.
- If Docker Compose starts but changes do not persist, check that the `data` folder is present and writable.
- If the container cannot find `namelist.csv` on first startup, confirm that the file exists in the project root and that `NAMELIST_PATH` points to the mounted file; you can also add boarders through the Boarders tab without a seed file.
