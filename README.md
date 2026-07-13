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
- [parser.py](parser.py) - CSV parsing and lateness calculation logic
- [templates/index.html](templates/index.html) - dashboard UI
- [namelist.csv](namelist.csv) - master boarder list used for matching
- [requirements.txt](requirements.txt) - runtime dependencies
- [Dockerfile](Dockerfile) - container image definition

## Setup From Scratch

### Local Python setup

1. Install Python 3.9+.
2. Open a terminal in the project folder.
3. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

4. Start the app:

```bash
python -m flask --app app run
```

5. Open `http://127.0.0.1:5000/` in your browser.

### Docker setup

1. Install and start Docker Desktop.
2. Create a host folder for persistent data, for example `C:\lateness-data`.
3. Put your current `namelist.csv` in that folder.
4. Build the image:

```bash
docker build -t lateness-app .
```

5. Run the container with the data folder mounted. In PowerShell, use backticks for line continuation:

```bash
docker run --rm -p 8000:8000 `
  -e DB_PATH=/data/lateness_history.db `
  -e NAMELIST_PATH=/data/namelist.csv `
  -v C:/lateness-data:/data `
  lateness-app
```

6. Open `http://127.0.0.1:8000/` in your browser.

## Updating the namelist after setup

If you are using Docker, update the `namelist.csv` file in the mounted data folder and restart the container. You do not need to rebuild the image because the app reads the path from `NAMELIST_PATH`.

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
- The SQLite database file is created automatically on first run if it does not already exist.

## Persistence and deployment notes

- The app stores month summaries in SQLite using the path from `DB_PATH`.
- For Docker, keep the database file in a mounted folder so reports survive container restarts.
- For Docker, keep `namelist.csv` in that same mounted folder if it changes over time.
- Monthly upload files are temporary and are not stored permanently by the app.

## Development notes

- Run `python parser.py` for a quick parser check.
- The lateness window is hard-coded in `parser.py`.
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

### `parser.py` - CSV parsing and lateness calculation

`parser.py` loads the master boarder list and calculates lateness metrics from uploaded CSV logs.

Important behavior:

- `load_namelist(namelist_filename)` reads the master list and normalizes names.
- `process_lateness(log_filename, boarders_dict)` scans transaction times and updates boarder totals.
- `export_to_csv(output_filename, boarders_dict)` writes the results to a CSV file.

### `templates/index.html` - User interface and client scripting

The template renders the dashboard, search tab, month cards, month detail table, delete confirmation modal, and client-side sorting and fetch behavior.

## Troubleshooting

- If the app starts but no boarders are found, confirm that the `namelist.csv` file has the expected column names.
- If Docker starts but changes do not persist, check that the volume mount path is correct and writable.
- If the container cannot find `namelist.csv`, confirm that `NAMELIST_PATH` points to the mounted file.
