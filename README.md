# Lateness Application

#### Description

Lateness Application is a Flask-based disciplinary reporting dashboard used to track
boarder lateness. The system ingests monthly CSV transaction logs, matches each entry
against a canonical master list (`namelist.csv`), computes lateness occurrences and
penalty points (frequency + minutes late), and persists month-level summaries in an
SQLite database for review, search, and export.

Key features
- Upload monthly CSV attendance logs and generate a summarized monthly report
- Match entries against a master boarder list (`namelist.csv`) with normalization to
	reduce casing/spacing mismatches
- Compute lateness frequency and total late minutes per boarder
- Persist monthly summaries in `lateness_history.db` using SQLite
- Search historical boarder reports by partial or full name
- View, download (CSV), and delete saved monthly reports via the UI

---

## Files and Components

### `app.py` — Flask application and persistence layer

Overview
`app.py` is the primary backend for the application. It exposes web routes for
uploading logs, searching history, rendering the UI template, providing month JSON
data for client rendering, serving CSV downloads, and deleting month records.

How it works (important functions)
- `init_db()`
	- Ensures the SQLite table `boarder_history` exists with columns: `id`,
		`normalized_name`, `display_name`, `bed`, `month`, `frequency`, `total_minutes`,
		`total_points`, and `imported_at`.
	- Called at import and on app start to guarantee schema availability.
- `save_monthly_history(boarders_dict, month_label)`
	- Persists aggregated per-boarder data for a month.
	- Uses `INSERT ... ON CONFLICT(normalized_name, month) DO UPDATE` to upsert rows
		so an import can be safely re-run to replace a previous month summary.
- `get_all_months()` and `get_month_report(month_label)`
	- `get_all_months()` returns available month labels used to populate the UI list.
	- `get_month_report()` returns a mapping of normalized_name to bed, frequency, and
		total_minutes for the requested month.
- `search_history(name_query)`
	- Performs a case-insensitive partial-match search against `normalized_name`.
	- Returns entries with display name, bed, month, frequency, total minutes, and
		total points.

Routes and endpoints
- `GET /` — Renders `index.html`. The template receives `all_months`, current
	messages, and optionally `history_results` when a search is run.
- `POST /` — Handles two forms:
	- File upload (`log_file`) with `report_month` label: saves a temporary file,
		calls parser utilities to compute metrics, then persists via `save_monthly_history()`.
	- Search form (`search_name`): runs `search_history()` and shows results.
- `GET /api/month/<month>` — Returns JSON detail for the month used by client JS.
- `GET /download_month/<month>` — Streams a CSV file for the month using an in-memory
	buffer and `send_file()`.
- `DELETE /delete_month/<month>` — Deletes all rows for the given month label.

Behavioral notes
- Uploads are saved temporarily (e.g., `temp_monthly_log.csv`) and removed after
	processing.
- `normalized_name` (uppercase) is used as the primary join key between the master
	list and monthly logs to reduce matching errors.

### `parser.py` — CSV parsing and lateness calculation

Overview
`parser.py` encapsulates logic to load the master boarder list and compute lateness
statistics from CSV transaction logs.

Key functions and flow
- `load_namelist(namelist_filename)`
	- Loads a CSV (expected headers `Name` and `Bed`) and returns a dictionary keyed by
		`NAME.strip().upper()` with values `{"bed": <bed>, "frequency": 0, "total_minutes": 0}`.
	- Missing `namelist.csv` returns `None` and prints a helpful error message.
- `process_lateness(log_filename, boarders_dict)`
	- Scans the monthly log CSV (expects fields `Name` and `Transaction Time`).
	- Normalizes the `Name` from the log and skips entries not present in `boarders_dict`.
	- Parses `Transaction Time` supporting `HH:MM` and `HH:MM:SS` formats and converts
		to seconds.
	- Defines a lateness window with `START_SECONDS` (7:41) and `END_SECONDS` (8:00).
	- If a timestamp is later than `START_SECONDS` and up to `END_SECONDS`, computes
		minutes late using `math.ceil(seconds_late / 60)`, increments `frequency`, and
		accumulates `total_minutes` for that boarder.
	- Returns the updated `boarders_dict`.
- `export_to_csv(output_filename, boarders_dict)`
	- Simple helper to write computed results to a local CSV (used when running the
		parser module as a script).

Design considerations
- Name normalization (uppercasing and trimming) is critical to maintain consistent
	joins between the master list and transaction logs.
- The lateness window is hard-coded; consider parameterizing it for configurability.

### `templates/index.html` — User interface and client scripting

Overview
The template renders two main panels (History and Reports). It relies on Jinja2 to
populate server-side results and provides client-side JavaScript to fetch month
details, sort table columns, handle deletion with confirmation, and print reports.

Key UI features
- Upload form — Accepts a CSV and `report_month` label. Submits via POST to `/`.
- Months list — Displays month cards for each saved month (`all_months`). Cards
	provide `View` (AJAX fetch) and `Download` actions.
- Month detail area — Fetched via `/api/month/<month>`. Rendered client-side with
	sorting and print-friendly styles.
- History panel — Server-side rendered search results returned by posting `search_name`.

Client-side behavior
- Sorting: supports a natural comparator for bed identifiers (e.g., `601A, 601B`).
- Search: submits a hidden POST form to stay on the history tab and show results.
- Delete: shows modal confirmation, sends `DELETE` request to `/delete_month/<month>`,
	and reloads the page on successful deletion.

---

## Usage

Prerequisites
- Python 3.9+ and `pip`.

Install dependencies and run

```bash
python -m pip install flask
python Final_Project/app.py
# then open http://127.0.0.1:5000/
```

Data expectations
- `namelist.csv` — required master list of boarders. Expected columns: `Name`, `Bed`.
- Monthly logs — CSV files with at least `Name` and `Transaction Time` columns.

Workflow summary
1. Verify `namelist.csv` is present in the working directory.
2. Open the web UI and go to the Reports tab.
3. Upload a monthly log CSV and give it a `Report Month` label (e.g., `2026-03`).
4. Generate and save the report; view or download the month as needed.

---

## Development & Testing Recommendations

- Run `parser.py` directly for quick parsing checks:

```bash
python Final_Project/parser.py
```

- Add unit tests for `process_lateness()` to cover time parsing edge cases and
	partial name matches.
- Consider creating a `requirements.txt` and adding a small `docker` or `Makefile`
	workflow for reproducible execution.

---