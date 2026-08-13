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

- [app.py](app.py) - Flask app, routes, and SQLite persistence
- [parser.py](parser.py) - CSV parsing, lateness calculation, and the single CSV writer
- [templates/index.html](templates/index.html) - dashboard UI
- [tests/](tests/) - pytest suite for the ingestion module (no server or browser needed)
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

## Updating the namelist after setup

If you are using Docker Compose, update the root `namelist.csv` file and restart the stack. You do not need to rebuild the image because the app reads the path from `NAMELIST_PATH`.

If you are running locally, replace the `namelist.csv` in the project folder before restarting the app.

## Using the application

1. Go to the Reports tab.
2. Upload a monthly CSV log file.
3. Enter a month label such as `2026-03`.
4. Save the report.
5. Use the month cards to view, download, or delete saved reports.
6. Use the History tab to search boarder records by name.

## Data expectations

- `namelist.csv` should contain at least `Name` and `Bed` columns.
- Monthly log CSV files should contain at least `Name` and `Transaction Time` columns.
- `Transaction Time` values must be strict `HH:MM` or `HH:MM:SS` (24-hour) times. Anything else is rejected with the offending rows surfaced, never silently dropped.
- The SQLite database file is created automatically on first run if it does not already exist.

## Upload behaviour

A month report is only saved when the uploaded log produced at least one row for a known boarder with a parseable time. Uploads that match nothing, or whose times can't be read, are rejected with a specific error (master list missing/empty, no rows matched, or all times unparseable) and leave the database untouched. A clean month with matched rows still saves normally. A successful upload reports how many boarders were recorded.

## Persistence and deployment notes

- The app stores month summaries in SQLite using the path from `DB_PATH`.
- For Docker, keep the database file in a mounted folder so reports survive container restarts.
- For Docker, keep `namelist.csv` in that same mounted folder if it changes over time.
- Monthly upload files are temporary and are not stored permanently by the app.

## Development notes

- Install dev dependencies (pytest) with `python -m pip install -r requirements-dev.txt`.
- Run `python -m pytest tests` to run the ingestion test suite (synthetic CSVs, no server or browser required).
- Run `python parser.py` for a quick parser check: it reads `namelist.csv` plus `test_data.csv`, writes `lateness_final_report.csv`, and prints the diagnostics (rows read, matched rows, unmatched names, unparseable rows).
- The lateness window is hard-coded in `parser.py`.
- Lateness frequency, total minutes late, and total points are computed once in the ingestion module and carried in the ingestion result; the month view, the download, and the CSV export all use that one definition.
- The CSV export, the month download, and `export_to_csv` all share the single CSV writer in `parser.py`, so their output is identical.
- The app currently uses SQLite, so it is well suited to a single-user or small-team deployment.

## Files and Components

### `app.py` - Flask application and persistence layer

`app.py` exposes the web routes for uploading logs, searching history, rendering the dashboard, returning month JSON data, serving CSV downloads, and deleting month records.

Important behavior:

- `init_db()` creates the `boarder_history` table if it does not exist.
- `save_monthly_history(boarders_dict, month_label)` upserts each boarder row by month.
- `get_all_months()` returns the saved month list used in the UI.
- `get_month_report(month_label)` returns one month's stored boarder rows.
- `search_history(name_query)` performs a partial match against stored names.

### `parser.py` - CSV parsing, lateness calculation, and the CSV writer

`parser.py` loads the master boarder list and calculates lateness metrics from uploaded CSV logs. It is the single ingestion seam: one structured result carries per-boarder metrics (bed, frequency, total minutes late, total points) plus diagnostics (rows read, rows matched, unmatched names, unparseable rows, and whether any parseable data was produced), and the same module owns all CSV writing.

Important behavior:

- `load_namelist(namelist_filename)` reads the master list and normalizes names; returns `None` if the file is missing.
- `process_lateness(log_filename, master_list)` returns an `IngestionResult` with per-boarder metrics and diagnostics.
- `ingestion_rejection_message(result, master_list)` returns a specific error message for a log that cannot be saved, or `None` when it can.
- `boarders_to_csv(boarders)` renders a boarders mapping to CSV text (used by the download route and the export).
- `export_to_csv(output_filename, boarders)` writes the results to a CSV file.

### `templates/index.html` - User interface and client scripting

The template renders the dashboard, search tab, month cards, month detail table, delete confirmation modal, and client-side sorting and fetch behavior.

## Troubleshooting

- If the app starts but no boarders are found, confirm that the `namelist.csv` file has the expected column names.
- If Docker Compose starts but changes do not persist, check that the `data` folder is present and writable.
- If the container cannot find `namelist.csv`, confirm that the file exists in the project root and that `NAMELIST_PATH` points to the mounted file.
