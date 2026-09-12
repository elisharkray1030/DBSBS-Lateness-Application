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

- `create_schema(conn)` creates the app's tables if they do not exist,
  including `boarder_history`, `ipoint_entries`, `ipoint_adjustments`,
  `ipoint_audit`, and `confiscations`.
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
- `stage_ipoint_entry(conn, ...)`, `stage_ipoint_adjustment(conn, ...)`,
  `stage_ipoint_confiscation(conn, ...)`, and `stage_ipoint_audit(conn, audit)`
  stage one I-Point ledger row and one audit row on the open transaction; the
  I-Points lifecycle owns the commit, so the ledger row and its audit travel
  together or not at all.
- `list_ipoint_entries(conn[, name])`, `list_ipoint_adjustments(conn[, name])`,
  `list_ipoint_confiscations(conn[, name])`, and
  `list_ipoint_audit(conn[, name])` read the ledger and its history, optionally
  for one Match Key.
- `freshest_identity_map(conn)` maps every known Match Key to its freshest-first
  identity, derived from the All-Time List and shared by the House Dashboard and
  the I-Point Balance.

## Records — `records.py`

`records.py` defines the `BoarderRecord` (normalized identity, canonical
display name, bed, frequency, total minutes late, total points) once, shared by
the ingestion module, the CSV writer, the storage module, and the JSON body. It
also holds the `Boarder` Master List row and the `UnparsedTimeRow` record, and
the `bed_sort_key` rule that orders Monthly Report rows.

It also carries the I-Point records: `IPointEntry` (one logged occasion),
`IPointAdjustment` (one signed, manual correction), `Confiscation` (one Phone
Confiscation, pending through released or voided), `IPointAuditDraft` (the
fields of one retained change, before its id, staged into storage),
`IPointAudit` (a stored change with its prior state), and `IPointSummary` (one
boarder's derived Balance plus their Entries, Adjustments, pending Redemption,
and Audit History).

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

## I-Points — `ipoints.py`

`ipoints.py` owns the I-Points ledger, standing beside `punishments.py` as the
second disciplinary lifecycle: it validates and logs Entries, edits and removes
them, adds, edits, and removes signed Adjustments, evaluates month-close
Redemptions, confirms them into Phone Confiscations, writes each ledger row and
its audit row in one transaction, and derives every boarder's Balance from the
stored ledger rather than storing it.

Important behavior:

- `log_entry(conn, ...)` validates a submission (positive whole points, a
  required reason, a valid date) and writes the Entry plus its `created` audit
  row in one connection block; a rejected submission writes nothing. A blank
  date falls back to the injected `today` or the machine-local date.
- `edit_entry(conn, ...)` and `remove_entry(conn, ...)` validate the same fields
  and write an `edited`/`removed` audit row in the same connection block. A
  removal leaves the Balance but its prior state survives in the audit history.
  Any change that would take the Balance below zero is refused and writes
  nothing (ADR 0006).
- `add_adjustment(conn, ...)`, `edit_adjustment(conn, ...)`, and
  `remove_adjustment(conn, ...)` manage the signed Adjustments that rebalance a
  boarder without rewriting history. Points must be a non-zero whole number; a
  positive Adjustment adds to the Balance, and a subtraction is capped at the
  current Balance so it can never take the Balance below zero (ADR 0006). Each
  change writes its `created`/`edited`/`removed` audit row (entity type
  `adjustment`) in the same connection block.
- `boarder_balances(conn)` derives each boarder's Balance as the sum of
  their live Entries plus Adjustments minus the `points_redeemed` of their
  confirmed (`active` or `released`) Confiscations, resolving identity
  freshest-first through the shared All-Time List and falling back to the Match
  Key for a boarder known only through I-Points. It also attaches each boarder's
  live Entries and Adjustments, their pending Redemption, and their newest-first
  Audit History notes, and keeps a boarder whose Entries were all removed in
  that view.
- `evaluate_month_close(entries, adjustments, confiscations, today)` is where
  month-close logic lives — a pure function taking an injected `today` —
  returning a pending Redemption at the largest tier at or below the
  Balance (5/10/15, capped at 15), locked at creation. `pending_redemptions(conn, today)`
  detects the rows to write and `materialise_pending_redemptions(conn, today)`
  persists them idempotently (ADR 0007).
- `confirm_redemption(conn, id, ...)` freezes display name and bed, sets the
  Confiscation `active` with its `release_due`, and debits the Balance, refusing
  when the Balance has fallen below the pending's `points_redeemed`; `void_redemption`
  cancels a pending row. Each writes a `confirmed`/`voided` audit row
  (entity type `confiscation`) in the same connection block.
- The I-Points tables (`ipoint_entries`, `ipoint_adjustments`, `confiscations`,
  `ipoint_audit`) are created idempotently by `create_schema` and re-keyed with
  the other tables by the Match-Key migration. A partial unique index keeps one
  open (`pending` or `active`) Confiscation per Match Key.
- The web routes live in `app.py` (`GET /ipoints`, `POST /ipoints/entries`,
  `POST /ipoints/entries/<id>/edit`, `POST /ipoints/entries/<id>/remove`,
  `POST /ipoints/adjustments`, `POST /ipoints/adjustments/<id>/edit`,
  `POST /ipoints/adjustments/<id>/remove`,
  `POST /ipoints/confiscations/<id>/confirm`,
  `POST /ipoints/confiscations/<id>/void`) and delegate here; the route layer
  stays a thin adapter. The GET opens a read-only connection, materialising due
  pending Redemptions through a read-write pass only when needed (ADR 0007).

## Demo seeding — `seed_demo_data.py`

`seed_demo_data.py` populates the database with deterministic demo data
(January through August, excluding June) for development or testing. It reads
the seed Master List, generates synthetic lateness logs, and ingests them
through the same `ingest_log` path the web Import uses. Run with
`python seed_demo_data.py [--db PATH] [--namelist PATH] [--log-dir PATH]`.

## App layer — `app.py`

`app.py` exposes the web routes for importing Monthly Logs, searching history,
rendering the dashboard, returning month JSON data, serving CSV downloads,
deleting month records, and managing punishments, I-Points, and the Master List.
It is a thin adapter: each route opens a file-backed connection, delegates to
the ingestion and storage modules, and renders the outcome. No storage or
parsing logic lives here.

Important behavior:

- Each route opens its own connection and passes it into the storage functions;
  nothing reads a `DB_PATH` module global inside the storage layer.
- The Import route forwards the request stream straight into `ingest_log` — no
  temp file on disk.
- The I-Points routes (`GET /ipoints`, `POST /ipoints/entries`,
  `POST /ipoints/entries/<id>/edit`, `POST /ipoints/entries/<id>/remove`,
  `POST /ipoints/adjustments`, `POST /ipoints/adjustments/<id>/edit`,
  `POST /ipoints/adjustments/<id>/remove`,
  `POST /ipoints/confiscations/<id>/confirm`,
  `POST /ipoints/confiscations/<id>/void`)
  delegate to `ipoints.py`; the GET opens a read-only connection (materialising
  due pending Redemptions through a read-write pass only when needed), the POSTs
  the read-write one behind CSRF and the shared mutation-retry wrapper.
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
- `templates/ipoints.html` is the I-Points view: the log-Entry and
  add-Adjustment forms and the per-boarder ledger with each boarder's Balance,
  pending Redemption (confirm/void), Entries and Adjustments distinguished by a
  Type column with inline edit/remove controls, and a collapsible per-boarder
  Audit History. Reached from a tab-bar entry beside Punishments.
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
