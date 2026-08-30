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
- Assign punishments to boarders with deadline and status tracking (pending, completed, overdue, voided)
- View a Statistics / House Dashboard with house-wide trend chart, top-N boarders, repeat-offender watchlist, and Points distribution histogram
- Look up any boarder's profile with all-time history and a Points trend chart
- Manage the boarder master list through the Boarders tab (view, add, edit, remove, import CSV, export CSV)

## Project Layout

- [app.py](app.py) - Flask app and routes; a thin adapter over the ingestion and storage seams
- [parser.py](parser.py) - the shared ingestion module (parse -> decide -> persist -> message), the single CSV writer, and the parser CLI
- [storage.py](storage.py) - SQLite persistence behind an injectable connection seam
- [records.py](records.py) - the typed boarder record shared by ingestion, storage, the CSV writer, and the JSON body
- [punishments.py](punishments.py) - punishment assignment, status transitions, deadline/overdue rules
- [seed_demo_data.py](seed_demo_data.py) - deterministic demo seed script (Jan–Aug, no June)
- [templates/layout.html](templates/layout.html) - base layout, tab navigation, pagination
- [templates/index.html](templates/index.html) - main content panels (reports, find-a-boarder, punishments, boarders)
- [templates/dashboard.html](templates/dashboard.html) - Statistics / House Dashboard view
- [templates/boarder.html](templates/boarder.html) - individual boarder profile with all-time history and charts
- [templates/macros.html](templates/macros.html) - shared Jinja macros (Current/Former badge, etc.)
- [compose.yaml](compose.yaml) - Docker Compose service definition
- [tests/](tests/) - pytest suite covering the ingestion and storage seams, plus Flask test-client route tests and Playwright browser tests for UI behavior (browser tests skip automatically when Playwright is not installed)
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
6. Use **Find a Boarder** to look up any boarder's all-time history and Points trend chart.
7. Open a month report and click **Assign Punishments** to issue punishments to boarders who were late; set a deadline, then track status transitions (completed, overdue, voided) on the Punishments tab.
8. Use the **Statistics** tab to view house-wide analytics: top-N boarders, repeat-offender watchlist, and Points distribution.
9. Use the **Boarders** tab to view, add, edit, or remove boarders, import a CSV roster, or download the current roster.

Printing is an intentional feature for Monthly Reports: with a report open, Print (or Ctrl/Cmd+P) outputs exactly that report — the application chrome, other tabs' content, and empty report skeletons are excluded from the printed page.

## Demo data

For development or testing, run `python seed_demo_data.py` to seed the database with deterministic demo months (January through August, excluding June). Accepts optional flags:

```bash
python seed_demo_data.py [--db PATH] [--namelist PATH] [--log-dir PATH]
```

## Data expectations

- `namelist.csv` should contain at least `Name` and `Bed` columns. It is read once at first startup to seed an empty boarders table; after that, manage the boarder list through the Boarders tab.
- Monthly log CSV files should contain at least `Name` and `Transaction Time` columns.
- `Transaction Time` values must be strict `HH:MM` or `HH:MM:SS` (24-hour) times. Anything else is rejected with the offending rows surfaced, never silently dropped.
- The SQLite database file is created automatically on first run if it does not already exist.

## Upload behaviour

A month report is only saved when the uploaded log produced at least one row for a known boarder with a parseable time. Uploads that match nothing, or whose times can't be read, are rejected with a specific error (master list missing/empty, no rows matched, or all times unparseable) and leave the database untouched. A clean month with matched rows still saves normally. A successful Import reports how many Boarders were recorded and how many had lateness, plus counts of unmatched or unparseable rows when present, so staff can correct bad source data. The upload stream is consumed directly by the ingestion module - it is never written to a temp file on disk.

The web upload and the parser CLI run the exact same ingestion module, so the two surfaces can't drift apart.

## Persistence and deployment notes

- The app stores month summaries in SQLite using the path from `DB_PATH`.
- The boarder master list is stored in the same SQLite database and managed through the Boarders tab; `namelist.csv` is read once at first startup to seed an empty boarders table.
- For Docker, keep the database file in a mounted folder so reports and the boarder list survive container restarts.
- Monthly uploads are consumed directly from the request stream and are never written to disk or stored permanently by the app.

## Development notes

- Install dev dependencies (pytest, mypy, playwright) with `python -m pip install -r requirements-dev.txt`.
- Run `python -m pytest tests` to run the suite across the ingestion and storage seams, the Flask test-client seam, and the Playwright browser seam (synthetic CSVs and an in-memory SQLite connection; browser tests need Playwright's Chromium and skip automatically when it is unavailable).
- Run `python -m mypy app.py parser.py storage.py records.py punishments.py seed_demo_data.py` for typechecking.
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
- `api_month()` returns the month's rows as an ordered collection of explicit fields (name, display name, bed, frequency, total minutes, total points), so the wire format matches the stored rows and the CSV writer and carries the canonical display name.
- A roster Import validates Bed uniqueness before replacing the master list: a CSV that assigns one Bed to two different boarders shows an actionable error and leaves the existing roster untouched, while duplicate normalized names still resolve last-row-wins.

### `parser.py` - the shared ingestion module, CSV writer, and CLI

`parser.py` owns the whole of monthly-report ingestion: parse rows -> decide reject-or-save -> persist -> build the message. The web route and the parser CLI both call `ingest_log`, so the two surfaces share one path and can't drift. It is also the single CSV writer, and it hosts the CLI (`python parser.py`).

Important behavior:

- `load_namelist(namelist_filename)` reads the master list into a normalized-name-to-Boarder mapping; returns `None` if the file is missing.
- `ingest_log(log_stream, month_label, master_list, conn)` takes the log as a stream, the month label, the master list (each entry carrying the canonical display name and bed), and a history store connection, and returns one outcome - either the report saved, or rejected with an exact reason. A rejected ingestion leaves the store untouched.
- `SavedOutcome` carries the saved `BoarderRecord` list plus diagnostics (rows read, matched rows, unmatched names, unparseable rows) and builds the user-facing saved-month confirmation.
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
- `get_month_report(conn, month_label)` returns one month's stored `BoarderRecord` rows, ordered by the server's single Bed ordering rule (numeric part then suffix, lexical fallback). The report table may change display order without changing these stored values.
- `search_boarders(conn, name_query)` performs a partial Match Key match and returns one entry per boarder over the All-Time List population (Master List plus Boarder History and Punishments keys), sharing its freshest-first identity resolution and sort order.
- `delete_month(conn, month_label)` removes a month and returns the deleted row count.
- `replace_boarders(conn, rows)` replaces the master list after resolving duplicate normalized names last-row-wins and validating that no two different boarders share a Bed, raising a ValueError otherwise.

### `records.py` - the typed boarder record

`records.py` defines the `BoarderRecord` (normalized identity, canonical display name, bed, frequency, total minutes late, total points) once, shared by the ingestion module, the CSV writer, the storage module, and the JSON body. It also holds the `Boarder` master-list row and the `UnparsedTimeRow` record, and the `bed_sort_key` rule that orders Monthly Report rows.

### `punishments.py` - punishment assignment, status transitions, and deadline rules

`punishments.py` owns punishment lifecycle management: assigning punishments to boarders after a monthly report, enforcing deadline/overdue rules, and transitioning punishments through statuses (pending, completed, overdue, voided).

Important behavior:

- Imports from `storage` and `records` to read and write punishment rows against the shared connection seam.
- Handles deadline validation, overdue detection, and status transitions.
- The Assign Punishments flow lives in `app.py` (`/assign/<month>`) and delegates to this module.

### `seed_demo_data.py` - deterministic demo data seeding

`seed_demo_data.py` populates the database with deterministic demo data (January through August, excluding June) for development or testing. It reads the namelist, generates synthetic lateness logs, and ingests them through the same `ingest_log` path the web UI uses. Run with `python seed_demo_data.py [--db PATH] [--namelist PATH] [--log-dir PATH]`.

### `templates/index.html` - main content panels

The template renders the Find a Boarder search, Reports database, Punishments panel, and Boarders management panel. The month detail table renders canonical display names and typed values supplied by the server, starts in the server-defined Bed order, and supports display-only sorting without changing the data used for printing, downloading, or Punishment assignment.

### `templates/layout.html` - base layout and tab navigation

The base layout template renders the tab bar, pagination controls, flash messages, and the application chrome. All page routes render through this layout.

### `templates/dashboard.html` - Statistics / House Dashboard

The Statistics view displays house-wide analytics: a house-wide trend chart, Top Boarders ranking, repeat-offender watchlist, and a Points distribution histogram.

### `templates/boarder.html` - individual boarder profile

The boarder profile page shows a boarder's all-time record, Points trend chart, and all punishments. Reached by clicking a boarder name anywhere in the app or via Find a Boarder search.

### `templates/macros.html` - shared Jinja macros

Shared template macros (e.g. the Current/Former status badge) used across the other templates.

## Troubleshooting

- If the app starts but no boarders are found, confirm that the `namelist.csv` file has the expected column names, or add boarders through the Boarders tab.
- If Docker Compose starts but changes do not persist, check that the `data` folder is present and writable.
- If the container cannot find `namelist.csv` on first startup, confirm that the file exists in the project root and that `NAMELIST_PATH` points to the mounted file; you can also add boarders through the Boarders tab without a seed file.
