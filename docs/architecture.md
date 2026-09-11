# Architecture

The app is a Flask dashboard over one SQLite database. Two seams carry the
data: ingestion (`parser.py`) and storage (`storage.py`). A typed boarder
record (`records.py`) crosses both. The app layer (`app.py`) is a thin adapter
that opens a connection per request and renders outcomes; templates and
`static/app.js` are the browser surface.

See [CONTEXT.md](../CONTEXT.md) for domain vocabulary and [docs/adr/](adr/) for
the decisions behind these boundaries.

## Ingestion seam — `parser.py`

`parser.py` owns the whole of monthly-report ingestion: parse rows → decide
reject-or-save → persist → build the message. The web Import route and the
parser CLI both call `ingest_log`, so the two surfaces share one path and can't
drift. It is also the single CSV writer, and it hosts the CLI
(`python parser.py`).

Important behavior:

- `load_namelist(namelist_filename)` reads the Master List into a
  normalized-name-to-Boarder mapping; returns `None` if the file is missing.
- `ingest_log(log_stream, month_label, master_list, conn)` takes the log as a
  stream, the month label, the Master List (each entry carrying the canonical
  display name and bed), and a history-store connection, and returns one
  outcome — either the report saved, or rejected with an exact reason. A
  rejected ingestion leaves the store untouched.
- `SavedOutcome` carries the saved `BoarderRecord` list plus diagnostics (rows
  read, matched rows, unmatched names, unparseable rows) and builds the
  user-facing saved-month confirmation.
- `RejectedOutcome` carries the exact rejection reason (Master List
  missing/empty, empty log, no rows matched any boarder, or no parseable time).
- `boarders_to_csv(boarders)` renders a boarder record list to CSV text (used
  by the download route and the export).
- `export_to_csv(output_filename, boarders)` writes the results to a CSV file.
- `cli_ingest` / `cli_main` run the shared ingestion path over a log file.

## Storage seam — `storage.py`

`storage.py` owns all persistence. Every function takes the connection, so the
same module works against a file-backed connection in production and an
in-memory (`:memory:`) connection in tests. No storage function reads a
`DB_PATH` module global.

Important behavior:

- `create_schema(conn)` creates the `boarder_history` table if it does not
  exist.
- `save_month(conn, boarders, month_label)` upserts each boarder row by month.
- `list_months(conn)` returns the month summaries used in the UI (month label,
  boarder count, total minutes late), ordered newest-first.
- `get_month_report(conn, month_label)` returns one month's stored
  `BoarderRecord` rows, ordered by the server's single Bed ordering rule
  (numeric part then suffix, lexical fallback). The report table may change
  display order without changing these stored values.
- `search_boarders(conn, name_query)` performs a partial Match Key match and
  returns one entry per boarder over the All-Time List population (Master List
  plus Boarder History and Punishments keys), sharing its freshest-first
  identity resolution and sort order.
- `delete_month(conn, month_label)` removes a month and returns the deleted row
  count.
- `replace_boarders(conn, rows)` replaces the Master List after resolving
  duplicate normalized names last-row-wins and validating that no two different
  boarders share a Bed, raising a `ValueError` otherwise.

## Records — `records.py`

`records.py` defines the `BoarderRecord` (normalized identity, canonical
display name, bed, frequency, total minutes late, total points) once, shared by
the ingestion module, the CSV writer, the storage module, and the JSON body. It
also holds the `Boarder` Master List row and the `UnparsedTimeRow` record, and
the `bed_sort_key` rule that orders Monthly Report rows.

## Punishments — `punishments.py`

`punishments.py` owns punishment lifecycle management: assigning punishments to
boarders after a monthly report, enforcing deadline/overdue rules, and
transitioning punishments through statuses (pending, completed, overdue,
voided).

Important behavior:

- Imports from `storage` and `records` to read and write punishment rows
  against the shared connection seam.
- Handles deadline validation, overdue detection, and status transitions.
- The Assign Punishments flow lives in `app.py` (`/assign/<month>`) and
  delegates to this module.

## Demo seeding — `seed_demo_data.py`

`seed_demo_data.py` populates the database with deterministic demo data
(January through August, excluding June) for development or testing. It reads
the seed Master List, generates synthetic lateness logs, and ingests them
through the same `ingest_log` path the web Import uses. Run with
`python seed_demo_data.py [--db PATH] [--namelist PATH] [--log-dir PATH]`.

## App layer — `app.py`

`app.py` exposes the web routes for importing Monthly Logs, searching history,
rendering the dashboard, returning month JSON data, serving CSV downloads,
deleting month records, and managing punishments and the Master List. It is a
thin adapter: each route opens a file-backed connection, delegates to the
ingestion and storage modules, and renders the outcome. No storage or parsing
logic lives here.

Important behavior:

- Each route opens its own connection and passes it into the storage functions;
  nothing reads a `DB_PATH` module global inside the storage layer.
- The Import route forwards the request stream straight into `ingest_log` — no
  temp file on disk.
- `api_month()` returns the month's rows as an ordered collection of explicit
  fields (name, display name, bed, frequency, total minutes, total points), so
  the wire format matches the stored rows and the CSV writer and carries the
  canonical display name.
- A Master List Import validates Bed uniqueness before replacing the list: a
  CSV that assigns one Bed to two different boarders shows an actionable error
  and leaves the existing list untouched, while duplicate normalized names
  still resolve last-row-wins.

## UI — `templates/` and `static/app.js`

- `templates/layout.html` renders the tab bar, pagination controls, flash
  messages, and application chrome. All page routes render through this layout.
- `templates/index.html` renders the Find a Boarder search, Reports panel,
  Punishments panel, and Boarders management panel. The month detail table
  renders canonical display names and typed values supplied by the server,
  starts in the server-defined Bed order, and supports display-only sorting
  without changing the data used for printing, downloading, or Punishment
  assignment.
- `templates/dashboard.html` is the Statistics view: house-wide trend chart,
  Top Boarders ranking, repeat-offender watchlist, and Points distribution
  histogram.
- `templates/boarder.html` is the boarder profile: all-time record, Points
  trend chart, and all punishments. Reached by clicking a boarder name anywhere
  in the app or via Find a Boarder search.
- `templates/macros.html` holds shared Jinja macros (for example, the
  Current/Former status badge).
- `static/app.js` holds browser-side behavior: table sorting, charts, and
  punishment actions.

## Invariants

- **Single ingestion path.** The web Import and the parser CLI both call
  `ingest_log`; there is no second parser.
- **Single CSV writer.** The CSV export, the month download, and
  `export_to_csv` all share one writer in `parser.py`, so their output is
  identical.
- **One computation.** Lateness frequency, total minutes late, and total points
  are computed once in the ingestion module and carried on the typed boarder
  record; the month view, the download, and the CSV export all use that one
  definition.
- **Injectable connection.** Storage functions take the connection as an
  argument, so tests run against `:memory:` and no storage function reads a
  `DB_PATH` global.
- **No DB writes at import.** Importing the modules opens no database and
  writes nothing; schema creation is an explicit `init-db` step.
