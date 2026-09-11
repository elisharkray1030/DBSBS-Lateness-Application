import io
import logging
import os
import secrets
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import quote, urlencode

try:
    from flask import (
        Blueprint,
        Flask,
        current_app,
        flash,
        get_flashed_messages,
        jsonify,
        redirect,
        render_template,
        request,
        send_file,
        session,
    )
except ModuleNotFoundError as exc:
    if exc.name != 'flask':
        raise
    raise SystemExit(
        'Flask is not installed in the current Python environment.\n'
        'Install dependencies with: python -m pip install -r requirements.txt\n'
        'On Windows, prefer: py -3 -m pip install -r requirements.txt\n'
        'Then start the app with: python -m flask --app app run'
    ) from exc

import defaults
import ipoints
import storage
from ipoints import EntryRejected
from parser import (
    RejectedOutcome,
    boarders_to_csv,
    ingest_log,
    load_namelist_rows,
    master_list_to_csv,
    parse_namelist_stream,
)
from punishments import (
    AssignmentRejected,
    NON_VOIDED_STATUSES,
    TransitionRejected,
    assign_batch,
    attach_display_flags,
    format_timestamp,
    humanized_status,
    list_punishments_view,
    transition,
)
from records import build_profile_summary, normalize_name

# Module logger: stdlib, so pure helpers below stay callable without an
# application context (request-scoped code uses current_app.logger).
logger = logging.getLogger(__name__)

bp = Blueprint("lateness", __name__)

# Built-in defaults; environment wins over these and inline factory config
# wins over environment. No database I/O happens at import time. The literals
# live in ``defaults`` so host tooling cannot drift from the app.
_DEFAULT_DB_PATH = defaults.DEFAULT_DB_PATH
_DEFAULT_NAMELIST_PATH = defaults.DEFAULT_NAMELIST_PATH
_DEFAULT_LOG_ARCHIVE_DIR = defaults.DEFAULT_LOG_ARCHIVE_DIR

# Repeat-offender watchlist: a boarder reaching this many Points for this
# many consecutive calendar months lands on the House Dashboard watchlist.
# Deliberate application constants (#103) — not staff-configurable yet.
WATCHLIST_POINTS_THRESHOLD = 12
WATCHLIST_MIN_STREAK_MONTHS = 3

# How many boarders the dashboard top-N widget ranks.
TOP_BOARDERS_DEFAULT_LIMIT = 10


# Shared office-LAN deployment: every staff PC runs against one NAS-hosted
# database file, so every connection waits on locks instead of failing
# instantly. WAL stays off — its shared-memory sidecar is unreliable across
# hosts on SMB — so the default rollback journal is retained deliberately.
NAS_BUSY_TIMEOUT_S = 30.0


# Request/error hardening: every request body is bounded, operational events
# go through the application logger, and unhandled failures answer cleanly.
_DEFAULT_MAX_CONTENT_LENGTH = 16 * 1024 * 1024
_DEFAULT_LOG_LEVEL = "INFO"
_SERVER_ERROR = (
    "An unexpected error occurred. Nothing may have been saved; please try again."
)


# Per-session CSRF token: single random value per session, sent as a hidden
# form field on HTML POSTs and as a custom header on fetch PATCH/DELETE.
CSRF_SESSION_KEY = "csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
_CSRF_ERROR = "Invalid CSRF token. Please reload the page and try again."


def _ensure_csrf_token() -> str:
    """Returns the session token, creating it lazily on first page view."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return str(token)


def _request_csrf_token() -> str:
    """Reads the submitted token from form field or fetch header."""
    form_token = request.form.get(CSRF_FORM_FIELD, "")
    header_token = request.headers.get(CSRF_HEADER, "")
    return str(form_token or header_token or "")


def _csrf_failure():
    """Rejects a mutation with a clear error, leaving the database untouched."""
    script = _script_error_response(_CSRF_ERROR, 403)
    if script is not None:
        return script
    flash(_CSRF_ERROR, "error")
    return render_template("403.html", **_page_context(error=_CSRF_ERROR)), 403


def _resolve_setting(name: str, default: str) -> str:
    """Resolves one application setting for the current application.

    Inside an application or request context the active application's
    config wins; a context without the key (or no context, for ad-hoc
    tooling) falls back to the environment, then the built-in default.
    Importing this module never touches the database.
    """
    try:
        return str(current_app.config.get(name, os.environ.get(name, default)))
    except RuntimeError:
        return os.environ.get(name, default)


def _env_setting(name: str) -> "str | None":
    """Reads one environment setting; a blank or missing value is unset.

    Shared by the typed resolvers below so "empty export means unset"
    lives in exactly one place; each resolver keeps its own parsing.
    """
    return os.environ.get(name, "").strip() or None


def _resolve_max_content_length(provided: "dict[str, Any]") -> int:
    """Resolves the request size cap in bytes: inline config beats environment.

    An unreadable environment value falls back to the built-in default so a
    typo never silently removes the guard; an inline value is trusted as-is
    since it is a programming-time choice, not operator input.
    """
    if "MAX_CONTENT_LENGTH" in provided:
        return int(provided["MAX_CONTENT_LENGTH"])
    raw = _env_setting("MAX_CONTENT_LENGTH")
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            pass
    return _DEFAULT_MAX_CONTENT_LENGTH


def _resolve_log_level(provided: "dict[str, Any]") -> str:
    """Resolves the logger level name: inline config beats environment.

    Unrecognized values fall back to INFO — a misspelled level must never
    prevent the app from booting.
    """
    if "LOG_LEVEL" in provided:
        raw = str(provided["LOG_LEVEL"])
    else:
        raw = _env_setting("LOG_LEVEL") or _DEFAULT_LOG_LEVEL
    normalized = raw.strip().upper()
    if normalized in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"):
        return normalized
    return _DEFAULT_LOG_LEVEL


def _describe_limit(limit: int) -> str:
    """Words a byte cap in the largest whole unit for staff-facing errors."""
    if limit >= 1024 * 1024 and limit % (1024 * 1024) == 0:
        return f"{limit // (1024 * 1024)} MB"
    if limit >= 1024 and limit % 1024 == 0:
        return f"{limit // 1024} KB"
    return f"{limit} bytes"


def _script_error_response(message: str, status: int):
    """Answers script endpoints with JSON in the established shape family.

    The shared prefix table: ``/api/`` payloads carry the ``ok`` flag,
    ``/delete_month/`` ones match that route's existing ``{"error": ...}``
    shape. Returns None for page routes so the caller falls through to its
    page rendering.
    """
    for prefix, payload in (
        ("/api/", {"ok": False, "error": message}),
        ("/delete_month/", {"error": message}),
    ):
        if request.path.startswith(prefix):
            return jsonify(payload), status
    return None


def _oversize_message(path: str, limit: str) -> str:
    """Words the over-cap rejection for the request surface at ``path``.

    Pure copy dispatch, extracted so the path → wording table is testable
    without issuing a request. The render target still branches at the call
    site: the Master List surface re-renders its own panel while every
    other page surface re-renders the dashboard.
    """
    if path == "/boarders/import":
        return (
            f"Error: This Master List file exceeds the {limit} limit on "
            "Imports. Nothing was imported."
        )
    if path == "/":
        return (
            f"Error: This Monthly Log exceeds the {limit} limit on "
            "Imports. Nothing was imported."
        )
    return (
        f"Error: This request exceeds the {limit} limit. "
        "Nothing was changed."
    )


def _db_path() -> str:
    """Resolves the database location for the current application."""
    return _resolve_setting("DB_PATH", _DEFAULT_DB_PATH)


def _namelist_path() -> str:
    """Resolves the seed-list location, with the same precedence as above."""
    return _resolve_setting("NAMELIST_PATH", _DEFAULT_NAMELIST_PATH)


def _log_archive_dir() -> str:
    """Resolves the Monthly Log Archive directory, same precedence."""
    return _resolve_setting("LOG_ARCHIVE_DIR", _DEFAULT_LOG_ARCHIVE_DIR)


def _write_bytes_atomically(destination: Path, payload: bytes) -> None:
    """Writes bytes so readers only ever see the complete file.

    A unique staging file per call means concurrent writers (waitress serves
    requests threaded) never share a path and clobber each other's bytes;
    ``os.replace`` then swaps it in atomically on the same filesystem.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=destination.parent,
        prefix=f"{destination.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            handle.write(payload)
        os.replace(handle.name, destination)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _archive_import(month_label: str, payload: bytes) -> None:
    """Files an Import's source Monthly Log and Master List snapshot by month.

    Only called once an Import has committed, so ``month_label`` has already
    passed ``MONTH_LABEL_PATTERN`` (canonical ``YYYY-MM``) — the filenames are
    safe by construction, and a re-Import overwrites that month's copies.
    ``namelist-<month>.csv`` records the Master List as it stood for this
    Import, so a restore can re-seed the roster that produced the report.
    """
    directory = Path(_log_archive_dir())
    _write_bytes_atomically(directory / f"{month_label}.csv", payload)
    with connect(read_only=True) as conn:
        boarders = storage.list_boarders(conn)
    _write_bytes_atomically(
        directory / f"namelist-{month_label}.csv",
        master_list_to_csv(boarders).encode("utf-8"),
    )


def connect(read_only: bool = False) -> "closing[sqlite3.Connection]":
    """Opens a file-backed history store connection for the current call site.

    Pure-read surfaces pass ``read_only=True`` so readers never contend for
    the write lock on the shared-NAS database. The mixed import view, all
    state-changing routes and startup init use the default read-write form.
    """
    db_path = _db_path()
    if read_only and db_path != ":memory:":
        conn = sqlite3.connect(
            Path(db_path).resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=NAS_BUSY_TIMEOUT_S,
        )
        return closing(conn)
    conn = sqlite3.connect(db_path, timeout=NAS_BUSY_TIMEOUT_S)
    conn.execute("PRAGMA journal_mode=DELETE")
    return closing(conn)


SEED_FLAG = "boarders_seeded"


# Bounded mutation retry for the shared office-LAN deployment: a second
# writer's momentary contention surfaces as an instantly-raised locked error
# despite the lock-wait, so each mutation re-runs its whole block (fresh
# connection per attempt) a few times with backoff. Reads stay out of this —
# they rely on the lock-wait alone.
RETRY_ATTEMPTS = 3
RETRY_DELAYS_S = (0.1, 0.2)
_LOCKED_MARKER = "database is locked"


class DatabaseBusy(Exception):
    """Sustained lock contention on a named staff action."""

    def __init__(self, action: str):
        super().__init__(action)
        self.action = action


def busy_message(action: str) -> str:
    """Words the sustained-contention error for a staff action."""
    return (
        f"Error: Could not {action} — the database is busy. "
        "Nothing was changed; please try again."
    )


def with_lock_retry(action: str, work):
    """Runs ``work`` while it fails with the locked-database signal.

    Each attempt re-runs the whole block on a fresh connection; ``work`` is
    a zero-argument callable so ``return`` statements inside the block behave
    normally. Sustained contention raises :class:`DatabaseBusy`.
    """
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return work()
        except sqlite3.OperationalError as exc:
            locked = _LOCKED_MARKER in str(exc).lower()
            if not locked or attempt == RETRY_ATTEMPTS - 1:
                if locked:
                    raise DatabaseBusy(action) from exc
                raise
            time.sleep(RETRY_DELAYS_S[attempt])


def _mutate_with_retry(
    action: str,
    work: "Callable[[], Any]",
    on_busy: "Callable[[DatabaseBusy], Any]",
    log_message: str,
    *log_args: Any,
) -> Any:
    """Runs ``work`` under lock retry, answering sustained contention once.

    The ``try/except DatabaseBusy`` shape repeats at every mutation route
    while only the log line and the busy response differ per route, so both
    ride along as arguments. ``on_busy`` receives the exception so the
    response can name the staff action via :func:`busy_message`.
    """
    try:
        return with_lock_retry(action, work)
    except DatabaseBusy as exc:
        current_app.logger.warning(log_message, *log_args)
        return on_busy(exc)


def init_db(app: "Flask | None" = None) -> None:
    """Prepares the schema and once-only Master List seed.

    Runs against the given application (pushing its context) or, without
    one, against the active application context. Never runs at import.
    """
    if app is not None:
        with app.app_context():
            _init_db_impl()
    else:
        _init_db_impl()


def _init_db_impl() -> None:
    with connect() as conn:
        storage.create_schema(conn)
        if storage.get_meta(conn, SEED_FLAG) is not None:
            return
        if storage.list_boarders(conn):
            storage.set_meta(conn, SEED_FLAG, "1")
            return
        rows = load_namelist_rows(_namelist_path())
        if rows:
            storage.replace_boarders(conn, rows)
        storage.set_meta(conn, SEED_FLAG, "1")


def _punishment_months(conn, report_months):
    return sorted(
        {month.month for month in report_months}
        | set(storage.list_punishment_months(conn)),
        reverse=True,
    )


def _chart_payload(labels, **series):
    """Builds a chart's plain-data payload from labels plus named series.

    The one server contract for every embedded chart: JavaScript reads
    ``labels`` and each series by name; an always-readable table backs the
    same figures without JavaScript.
    """
    return {"labels": list(labels), **series}


def _page_context(selected_tab: str = '', message: str | None = None,
                  error: str | None = None, **extra):
    """Shared template context for every full page.

    Fills the layout chrome and the home-template panel defaults; routes
    pass only their own values as keyword overrides. ``panels_in_page``
    stays False unless a route rendering the four-panel home template
    overrides it, which flips the tab bar between buttons and deep links.
    """
    context = {
        'panels_in_page': False,
        'selected_tab': selected_tab,
        'message': message,
        'error': error,
        'history_results': None,
        'all_months': [],
        'current_month': None,
        'boarders': [],
        'punishment_months': [],
        'punishments': [],
        # 0 keeps the never-shown Punishments count line harmless on pages
        # that don't track the archive total (previously rendered blank).
        'punishments_total': 0,
        'punishments_show_all': False,
        'punishments_month': None,
        'punishments_status': None,
        'boarders_view': 'current',
        'all_time_boarders': None,
        'all_time_query': '',
        'current_year': datetime.now().astimezone().year,
    }
    context.update(extra)
    return context


def _consume_flashes() -> tuple[str | None, str | None]:
    """Returns (message, error) from one-shot session flash feedback."""
    message: str | None = None
    error: str | None = None
    # Flask guarantees (category, text) pairs when with_categories=True;
    # the stubs type the result loosely, so the cast records that contract.
    flashes = cast(
        "list[tuple[str, str]]", get_flashed_messages(with_categories=True)
    )
    for category, text in flashes:
        if category == "error":
            error = text
        else:
            message = text
    return message, error


def _migration_banner(skip_count: int) -> str:
    """Words the legacy-Match-Key banner in glossary vocabulary."""
    noun = "row" if skip_count == 1 else "rows"
    pronoun = "its" if skip_count == 1 else "their"
    key_word = "Match Key" if skip_count == 1 else "Match Keys"
    return (
        f"{skip_count} stored {noun} kept {pronoun} legacy {key_word} because "
        "another row claims the same identity. "
        "Review duplicates in the Boarders tab."
    )


def _flash_migration_skips(conn):
    """Flashes the legacy-Match-Key banner once per session when nonzero.

    The stored-key migration persists its skip count on every startup; the
    session flag keeps that standing fact from re-nagging on every visit,
    while a count of zero stays completely silent.
    """
    skip_count = storage.get_migration_skips(conn)
    if skip_count <= 0 or session.get("match_key_skips_reported"):
        return
    session["match_key_skips_reported"] = True
    flash(_migration_banner(skip_count), "error")


def build_csv_response(boarders, download_name):
    csv_bytes = io.BytesIO(boarders_to_csv(boarders).encode('utf-8'))
    csv_bytes.seek(0)
    return send_file(
        csv_bytes,
        as_attachment=True,
        download_name=download_name,
        mimetype='text/csv',
    )


def create_app(config: "dict[str, Any] | None" = None) -> Flask:
    """Builds the Flask application without touching the database.

    Precedence per key: inline ``config`` mapping beats environment beats
    built-in defaults. ``DB_PATH``, ``NAMELIST_PATH``, ``LOG_ARCHIVE_DIR``,
    and ``SECRET_KEY`` always land in ``app.config``; any extra keys (e.g.
    ``TESTING``) pass
    through untouched. Database preparation is explicit via the
    ``init-db`` command or :func:`init_db`, never an import side effect.
    ``SECRET_KEY`` has no default: startup aborts with a clear message
    when neither inline config nor environment provides one.
    """
    app = Flask(__name__)
    provided = config or {}
    if "SECRET_KEY" in provided:
        secret = provided["SECRET_KEY"]
    else:
        secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        raise SystemExit(
            "SECRET_KEY environment variable is required to start the app. "
            "Set it in the environment or compose file before running."
        )
    settings = {
        "DB_PATH": os.environ.get("DB_PATH", _DEFAULT_DB_PATH),
        "NAMELIST_PATH": os.environ.get("NAMELIST_PATH", _DEFAULT_NAMELIST_PATH),
        "LOG_ARCHIVE_DIR": os.environ.get(
            "LOG_ARCHIVE_DIR", _DEFAULT_LOG_ARCHIVE_DIR
        ),
        **provided,
        "SECRET_KEY": secret,
        # Resolved after the inline spread so the normalized value wins over
        # a raw inline/environment string; precedence stays inline > env.
        "MAX_CONTENT_LENGTH": _resolve_max_content_length(provided),
        "LOG_LEVEL": _resolve_log_level(provided),
    }
    app.config.update(settings)
    # Operational visibility: the application logger carries Import outcomes
    # and 500 stacktraces; CLI entry points keep their plain print() output.
    app.logger.setLevel(app.config["LOG_LEVEL"])
    # Sessions carry only transient flash feedback (no auth, no secrets).
    app.secret_key = app.config["SECRET_KEY"]
    # Plain-HTTP office LAN: HttpOnly + SameSite=Lax, deliberately no
    # Secure flag (browsers would drop a Secure cookie over HTTP). The LAN
    # itself is the trust boundary; Secure returns with HTTPS later.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False
    app.jinja_env.globals["humanized_status"] = humanized_status
    app.jinja_env.globals["format_timestamp"] = format_timestamp
    app.register_blueprint(bp)

    @app.before_request
    def _csrf_guard():
        """Seeds the session token on reads; gates every mutation on it."""
        if request.method in ("GET", "HEAD", "OPTIONS"):
            if CSRF_SESSION_KEY not in session:
                _ensure_csrf_token()
            return None
        session_token = session.get(CSRF_SESSION_KEY, "")
        submitted = _request_csrf_token()
        if not session_token or not submitted or submitted != session_token:
            return _csrf_failure()
        return None

    @app.context_processor
    def _inject_csrf_token():
        return {"csrf_token": session.get(CSRF_SESSION_KEY, "")}

    @app.errorhandler(413)
    def _oversize_request(error):
        """Rejects over-cap requests with a staff-readable error.

        Registered at app level (not on the blueprint) because the 413 can
        surface inside the app-level CSRF guard while it reads the submitted
        form — a blueprint handler would miss that path. Nothing is stored.
        """
        limit = _describe_limit(int(app.config["MAX_CONTENT_LENGTH"]))
        app.logger.warning(
            "Rejected oversize %s to %s (%s cap)",
            request.method, request.path, limit,
        )
        message = _oversize_message(request.path, limit)
        script = _script_error_response(message, 413)
        if script is not None:
            return script
        if request.path == "/boarders/import":
            return _render_boarders(error=message), 413
        with connect(read_only=True) as conn:
            all_months = storage.list_months(conn)
            boarders = storage.list_boarders(conn)
            punishment_months = _punishment_months(conn, all_months)
        return render_template('index.html', **_page_context(
            panels_in_page=True,
            selected_tab='reports',
            error=message,
            all_months=all_months,
            boarders=boarders,
            punishment_months=punishment_months,
        )), 413

    @app.errorhandler(500)
    def _server_error(error):
        """Logs the stacktrace and answers cleanly per surface.

        Page routes render a shared-layout error page; script endpoints get
        structured JSON in the same shape family as the CSRF rejection so
        script clients never receive HTML where they expect JSON.
        """
        app.logger.exception(
            "Unhandled %s %s", request.method, request.path
        )
        script = _script_error_response(_SERVER_ERROR, 500)
        if script is not None:
            return script
        return render_template("500.html", **_page_context(error=_SERVER_ERROR)), 500

    @app.cli.command("init-db")
    def init_db_command() -> None:
        """Creates the schema and once-only Master List seed."""
        # Bound to this application explicitly: the surrounding context
        # (test runners, nested shells) must never redirect initialization
        # at another database.
        init_db(app)
        print("Database initialized.")

    return app


@bp.route('/', methods=['GET', 'POST'])
def home():
    current_month = None
    search_results = None
    selected_tab = 'reports'
    message = None
    error = None

    with connect() as conn:
        all_months = storage.list_months(conn)
        boarders = storage.list_boarders(conn)
        punishment_months = _punishment_months(conn, all_months)
        _flash_migration_skips(conn)

    if request.method == 'POST':
        if 'log_file' in request.files:
            file = request.files['log_file']
            month_label = request.form.get('report_month', '').strip()

            if not file or file.filename == '':
                error = "Error: No Monthly Log selected."
                current_app.logger.info("Monthly Log import with no file selected")
            elif not month_label:
                error = "Please enter a valid month label for this report. Example: '2026-03'."
                current_app.logger.info("Monthly Log import with no month label")
            else:
                # Buffer the request body before retrying: each attempt re-reads
                # these bytes on a fresh connection.
                payload = file.read()

                def attempt():
                    log_stream = io.TextIOWrapper(io.BytesIO(payload), encoding='utf-8-sig')
                    try:
                        with connect() as conn:
                            master_list = storage.boarder_master_list(conn)
                            outcome = ingest_log(log_stream, month_label, master_list, conn)
                    finally:
                        log_stream.detach()

                    if isinstance(outcome, RejectedOutcome):
                        current_app.logger.warning(
                            "Rejected Monthly Log import for month %s: %s",
                            month_label, outcome.reason,
                        )
                        return f"Error: {outcome.reason}"
                    current_app.logger.info(
                        "Imported Monthly Log for month %s", month_label
                    )
                    flash(outcome.message, "success")
                    try:
                        _archive_import(month_label, payload)
                    except (OSError, sqlite3.Error):
                        # The Import is already committed, so a failed archive
                        # copy must not turn a successful save into an error —
                        # but it must not be silent either: without the archived
                        # Monthly Log the report cannot be rebuilt.
                        current_app.logger.exception(
                            "Could not archive Monthly Log for month %s",
                            month_label,
                        )
                        flash(
                            "Warning: the Monthly Log was saved but its source "
                            "file could not be archived. Keep the original file.",
                            "error",
                        )
                    query = urlencode({"month": month_label})
                    return redirect(f"/?{query}")

                # Deliberately not _mutate_with_retry: the busy outcome feeds
                # the shared error variable and falls through to the page
                # render below instead of returning a response directly.
                try:
                    result = with_lock_retry("import the Monthly Log", attempt)
                except DatabaseBusy as exc:
                    current_app.logger.warning(
                        "Monthly Log import for month %s hit sustained contention",
                        month_label,
                    )
                    error = busy_message(exc.action)
                else:
                    if isinstance(result, str):
                        error = result
                    else:
                        return result

    # Find-a-Boarder search submits as a native GET form; every search
    # renders its results (or the neutral no-matches empty state) directly.
    search_name = request.args.get('search_name')
    if search_name is not None:
        selected_tab = 'history'
        search_name = search_name.strip()
        if not search_name:
            error = "Please enter a boarder name to search Boarder History."
        else:
            with connect() as conn:
                search_results = storage.search_boarders(conn, search_name)

    flash_message, flash_error = _consume_flashes()
    message = message or flash_message
    error = error or flash_error

    # Deep-linkable in-page tabs (?tab=history etc.); a month parameter
    # still wins below so report links keep opening their month.
    tab_param = request.args.get('tab')
    if tab_param in ('reports', 'history', 'boarders'):
        selected_tab = tab_param

    if request.args.get('month'):
        month_param = request.args['month']
        with connect() as conn:
            if storage.get_month_report(conn, month_param):
                current_month = month_param
                selected_tab = 'reports'

    return render_template('index.html', **_page_context(
        panels_in_page=True,
        selected_tab=selected_tab,
        message=message,
        error=error,
        search_results=search_results,
        all_months=all_months,
        current_month=current_month,
        boarders=boarders,
        punishment_months=punishment_months,
    ))


@bp.route('/boarders')
def boarders():
    return _render_boarders()


def _validate_boarder(display_name, bed, exclude_id=None):
    if not display_name:
        return "Error: A boarder name is required."
    if not bed:
        return "Error: A bed is required."
    if _boarder_name_taken(display_name, exclude_id=exclude_id):
        return f"Error: A boarder named '{display_name}' is already on the list."
    if _boarder_bed_taken(bed, exclude_id=exclude_id):
        return f"Error: Bed '{bed}' is already assigned to another boarder."
    return None


@bp.route('/boarders/add', methods=['POST'])
def add_boarder():
    display_name = request.form.get('name', '').strip()
    bed = request.form.get('bed', '').strip()

    def attempt():
        error = _validate_boarder(display_name, bed)
        if error:
            return _render_boarders(error=error)
        with connect() as conn:
            storage.add_boarder(conn, normalize_name(display_name), display_name, bed)
        current_app.logger.info("Added boarder %s to Master List", display_name)
        return redirect('/boarders')

    return _mutate_with_retry(
        "add the boarder",
        attempt,
        lambda exc: _render_boarders(error=busy_message(exc.action)),
        "Add-boarder hit sustained contention",
    )


@bp.route('/api/boarders/<int:boarder_id>', methods=['PATCH'])
def api_edit_boarder(boarder_id):
    data = request.get_json(silent=True) or {}
    display_name = str(data.get('name', '')).strip()
    bed = str(data.get('bed', '')).strip()

    def attempt():
        error = _validate_boarder(display_name, bed, exclude_id=boarder_id)
        if error:
            return jsonify({'ok': False, 'error': error}), 400
        with connect() as conn:
            storage.update_boarder(conn, boarder_id, normalize_name(display_name), display_name, bed)
        current_app.logger.info("Updated boarder %s on Master List", display_name)
        return jsonify({'ok': True})

    return _mutate_with_retry(
        "update the boarder",
        attempt,
        lambda exc: (jsonify({'ok': False, 'error': busy_message(exc.action)}), 503),
        "Boarder update hit sustained contention",
    )


@bp.route('/api/boarders', methods=['PATCH'])
def api_edit_boarders():
    data = request.get_json(silent=True) or {}
    raw_updates = data.get('boarders')
    if not isinstance(raw_updates, list):
        return jsonify({'ok': False, 'error': 'A boarder list is required.'}), 400

    updates = []
    for item in raw_updates:
        if not isinstance(item, dict):
            return jsonify({'ok': False, 'error': 'Invalid boarder data.'}), 400
        try:
            boarder_id = int(item['id'])
        except (KeyError, TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'A valid boarder id is required.'}), 400

        display_name = item.get('name')
        bed = item.get('bed')
        if not isinstance(display_name, str) or not isinstance(bed, str):
            return jsonify({'ok': False, 'error': 'Boarder name and Bed are required.'}), 400
        display_name = display_name.strip()
        bed = bed.strip()
        updates.append((boarder_id, normalize_name(display_name), display_name, bed))

    def attempt():
        try:
            with connect() as conn:
                storage.update_boarders(conn, updates)
        except ValueError as exc:
            return jsonify({'ok': False, 'error': f'Error: {exc}'}), 400
        current_app.logger.info("Updated Master List (%d boarders)", len(updates))
        return jsonify({'ok': True})

    return _mutate_with_retry(
        "update the Master List",
        attempt,
        lambda exc: (jsonify({'ok': False, 'error': busy_message(exc.action)}), 503),
        "Master List update hit sustained contention",
    )


@bp.route('/api/boarders/<int:boarder_id>', methods=['DELETE'])
def api_delete_boarder(boarder_id):
    def attempt():
        with connect() as conn:
            storage.delete_boarder(conn, boarder_id)
        current_app.logger.info("Removed boarder id %d from Master List", boarder_id)
        return jsonify({'ok': True})

    return _mutate_with_retry(
        "remove the boarder",
        attempt,
        lambda exc: (jsonify({'ok': False, 'error': busy_message(exc.action)}), 503),
        "Boarder removal hit sustained contention",
    )


@bp.route('/boarders/import', methods=['POST'])
def import_boarders():
    file = request.files.get('boarder_csv')
    if not file or file.filename == '':
        current_app.logger.info("Master List import with no file selected")
        return _render_boarders(error="Error: No CSV file selected.")
    # Buffer the request body before retrying: each attempt re-reads these bytes on
    # a fresh connection, since the request stream is single-shot.
    payload = file.read()

    def attempt():
        with connect() as conn:
            log_stream = io.TextIOWrapper(io.BytesIO(payload), encoding='utf-8-sig')
            try:
                rows = parse_namelist_stream(log_stream)
            finally:
                log_stream.detach()

            try:
                storage.replace_boarders(conn, rows)
            except ValueError as exc:
                current_app.logger.warning(
                    "Rejected Master List import from %s: %s",
                    file.filename, exc,
                )
                return _render_boarders(error=f"Error: {exc}")
        current_app.logger.info("Replaced Master List from %s", file.filename)
        return redirect('/boarders')

    return _mutate_with_retry(
        "import the Master List",
        attempt,
        lambda exc: _render_boarders(error=busy_message(exc.action)),
        "Master List import hit sustained contention",
    )


@bp.route('/boarders/export')
def export_boarders():
    with connect(read_only=True) as conn:
        boarders = storage.list_boarders(conn)
    csv_text = master_list_to_csv(boarders)
    csv_bytes = io.BytesIO(csv_text.encode('utf-8'))
    csv_bytes.seek(0)
    return send_file(
        csv_bytes,
        as_attachment=True,
        download_name='boarders.csv',
        mimetype='text/csv',
    )


def _boarder_name_taken(display_name, exclude_id=None):
    with connect() as conn:
        return storage.boarder_exists(conn, normalize_name(display_name), exclude_id=exclude_id)


def _boarder_bed_taken(bed, exclude_id=None):
    with connect() as conn:
        return storage.bed_exists(conn, bed, exclude_id=exclude_id)


def _render_boarders(error=None, message=None):
    boarders_view = 'all-time' if request.args.get('view') == 'all-time' else 'current'
    all_time_query = request.args.get('q', '').strip()
    with connect(read_only=True) as conn:
        boarders_list = storage.list_boarders(conn)
        all_months = storage.list_months(conn)
        punishment_months = _punishment_months(conn, all_months)
        all_time_entries = (
            storage.list_all_time_boarders(conn) if boarders_view == 'all-time' else None
        )
    if all_time_entries is not None and all_time_query:
        needle = all_time_query.lower()
        all_time_entries = [
            entry for entry in all_time_entries if needle in entry.display_name.lower()
        ]
    return render_template('index.html', **_page_context(
        panels_in_page=True,
        selected_tab='boarders',
        message=message,
        error=error,
        all_months=all_months,
        boarders=boarders_list,
        punishment_months=punishment_months,
        boarders_view=boarders_view,
        all_time_boarders=all_time_entries,
        all_time_query=all_time_query,
    ))


@bp.route('/api/month/<path:month>')
def api_month(month):
    with connect(read_only=True) as conn:
        boarders = storage.get_month_report(conn, month)
    if not boarders:
        return jsonify({'error': f'No report found for {month}.'}), 404

    return jsonify({
        'month': month,
        'boarders': [
            {
                'name': record.name,
                'display_name': record.display_name,
                'bed': record.bed,
                'frequency': record.frequency,
                'total_minutes': record.total_minutes,
                'total_points': record.total_points,
            }
            for record in boarders
        ],
    })


@bp.route('/download_month/<path:month>')
def download_month(month):
    with connect(read_only=True) as conn:
        boarders = storage.get_month_report(conn, month)
    if not boarders:
        return f"Error: No report found for {month}.", 404

    safe_month = month.replace('/', '-').replace(' ', '_')
    return build_csv_response(boarders, f"Monthly_Lateness_Report_{safe_month}.csv")


@bp.route('/delete_month/<path:month>', methods=['DELETE'])
def delete_month(month):
    def attempt():
        with connect() as conn:
            deleted_count = storage.delete_month(conn, month)

        if deleted_count == 0:
            return jsonify({'error': f'No report found for {month}.'}), 404

        current_app.logger.info("Deleted Monthly Report for month %s", month)
        return jsonify({'success': True, 'deleted': deleted_count})

    return _mutate_with_retry(
        "delete the month's report",
        attempt,
        lambda exc: (jsonify({'error': busy_message(exc.action)}), 503),
        "Monthly Report deletion for month %s hit sustained contention",
        month,
    )


@bp.route('/assign/<path:month>', methods=['POST'])
def assign_month(month):
    deadline = request.form.get('deadline', '').strip()
    if not deadline:
        flash("Error: a deadline is required to assign punishments.", "error")
        return _punishments_redirect()

    # Positive consent: each checked box means "assign a punishment to this
    # boarder"; every eligible boarder not checked is exempted.
    selected = set(request.form.getlist('assign'))

    def attempt():
        with connect() as conn:
            boarders = storage.get_month_report(conn, month)
            if not boarders:
                return f"Error: No report found for {month}.", 404

            exemptions = {
                boarder.name
                for boarder in boarders
                if boarder.total_points > 0 and boarder.name not in selected
            }
            outcome = assign_batch(
                conn,
                month=month,
                boarders=boarders,
                exemptions=exemptions,
                deadline=deadline,
            )

        if isinstance(outcome, AssignmentRejected):
            flash(f"Error: {outcome.reason}", "error")
            return _punishments_redirect()

        current_app.logger.info("Assigned Punishments for month %s", month)
        flash(outcome.message, "success")
        query = urlencode({'month': month})
        return redirect(f"/?{query}")

    def _busy_redirect(exc):
        flash(busy_message(exc.action), "error")
        return _punishments_redirect()

    return _mutate_with_retry(
        "assign punishments",
        attempt,
        _busy_redirect,
        "Punishment assignment for month %s hit sustained contention",
        month,
    )


@bp.route('/punishments')
def punishments():
    show_all = request.args.get('show_all') == '1'
    month = request.args.get('month') or None
    status = request.args.get('status') or None
    with connect(read_only=True) as conn:
        punishments = list_punishments_view(conn, show_all=show_all, month=month, status=status)
        punishments_total = len(storage.list_punishments(conn, statuses=NON_VOIDED_STATUSES))
        all_months = storage.list_months(conn)
        boarders = storage.list_boarders(conn)
        punishment_months = _punishment_months(conn, all_months)

    message, error = _consume_flashes()

    return render_template('index.html', **_page_context(
        panels_in_page=True,
        selected_tab='punishments',
        message=message,
        error=error,
        all_months=all_months,
        boarders=boarders,
        punishment_months=punishment_months,
        punishments=punishments,
        punishments_total=punishments_total,
        punishments_show_all=show_all,
        punishments_month=month,
        punishments_status=status,
    ))


@bp.route('/consequences')
def punishments_legacy_redirect():
    """Legacy address for the Punishments view: temporary redirect.

    Preserves the full query string so bookmarked month/status/show-all
    filters survive the move to the canonical punishments address.
    """
    query = request.query_string.decode("utf-8")
    return redirect(f"/punishments{('?' + query) if query else ''}")


@bp.route('/ipoints')
def ipoints_view():
    """Renders the dedicated I-Points page: log form plus per-boarder ledger."""
    with connect(read_only=True) as conn:
        summaries = ipoints.boarder_balances(conn)
        boarder_options = sorted(
            {boarder.display_name for boarder in storage.list_boarders(conn)}
            | {summary.display_name for summary in summaries}
        )

    message, error = _consume_flashes()

    return render_template('ipoints.html', **_page_context(
        selected_tab='ipoints',
        message=message,
        error=error,
        ipoint_summaries=summaries,
        boarder_options=boarder_options,
        today=ipoints.today_iso(),
    ))


@bp.route('/ipoints/entries', methods=['POST'])
def log_ipoint_entry():
    boarder = request.form.get('boarder', '').strip()
    points = request.form.get('points', '').strip()
    occurred_on = request.form.get('occurred_on', '').strip()
    reason = request.form.get('reason', '').strip()

    def attempt():
        with connect() as conn:
            outcome = ipoints.log_entry(
                conn,
                normalized_name=normalize_name(boarder),
                points=points,
                occurred_on=occurred_on,
                reason=reason,
            )

        if isinstance(outcome, EntryRejected):
            flash(f"Error: {outcome.reason}", "error")
        else:
            current_app.logger.info(
                "Logged I-Point Entry for %s", outcome.normalized_name
            )
            flash(outcome.message, "success")
        return redirect('/ipoints')

    def _busy_redirect(exc):
        flash(busy_message(exc.action), "error")
        return redirect('/ipoints')

    return _mutate_with_retry(
        "log the I-Point Entry",
        attempt,
        _busy_redirect,
        "I-Point Entry logging hit sustained contention",
    )


@bp.route('/statistics')
def statistics():
    """Renders the House Dashboard: the Statistics tab's home.

    Every figure derives live from stored data on each visit, so re-imports
    and month deletions are reflected immediately.
    """
    with connect(read_only=True) as conn:
        trend = storage.house_trend(conn)
        stored_months = [summary.month for summary in storage.list_months(conn)]

        top_month = request.args.get('top_month', '')
        if top_month not in stored_months:
            top_month = ''
        top_entries = storage.top_boarders(
            conn, month=top_month or None, limit=TOP_BOARDERS_DEFAULT_LIMIT
        )

        distribution_month: str | None = request.args.get('distribution_month', '')
        if distribution_month not in stored_months:
            distribution_month = stored_months[0] if stored_months else None
        distribution = (
            storage.points_distribution(conn, distribution_month)
            if distribution_month
            else []
        )

        watchlist = storage.repeat_offenders(
            conn,
            threshold=WATCHLIST_POINTS_THRESHOLD,
            required_months=WATCHLIST_MIN_STREAK_MONTHS,
        )

    return render_template('dashboard.html', **_page_context(
        selected_tab='statistics',
        trend=trend,
        trend_payload=_chart_payload(
            [point.month for point in trend],
            incidents=[point.incidents for point in trend],
            minutes=[point.minutes_late for point in trend],
        ),
        stored_months=stored_months,
        top_month=top_month,
        top_entries=top_entries,
        top_payload=_chart_payload(
            [entry.display_name for entry in top_entries],
            points=[entry.points for entry in top_entries],
        ),
        distribution_month=distribution_month,
        distribution=distribution,
        distribution_payload=_chart_payload(
            [bucket.label for bucket in distribution],
            counts=[bucket.count for bucket in distribution],
        ),
        watchlist=watchlist,
        watchlist_threshold=WATCHLIST_POINTS_THRESHOLD,
        watchlist_min_streak=WATCHLIST_MIN_STREAK_MONTHS,
    ))


def _escalation_rows(series, live_punishments):
    """Joins one boarder's lateness series with live punishments by month.

    One merged row per distinct month, chronological ascending. Months from
    either read appear. The storage seam guarantees at most one live
    punishment per boarder-month (partial unique index), so a month holding
    more than one is corruption, not a legal state: the first row still
    renders, but the collision is logged loudly instead of passing
    silently. Voided-only months stay out — their detail lives in the
    Punishment Timeline so voided rows never inflate the escalation signal.
    """
    rows: dict[str, dict[str, Any]] = {}
    for entry in series:
        rows[entry.month] = {
            "month": entry.month,
            "frequency": entry.frequency,
            "total_minutes": entry.total_minutes,
            "total_points": entry.total_points,
            "punishment": None,
        }
    for punishment in live_punishments:
        row = rows.setdefault(punishment.month, {
            "month": punishment.month,
            "frequency": None,
            "total_minutes": None,
            "total_points": None,
            "punishment": None,
        })
        if row["punishment"] is None:
            row["punishment"] = punishment
        else:
            logger.warning(
                "Multiple live punishments for month %s; "
                "rendering the first",
                punishment.month,
            )
    return [rows[month] for month in sorted(rows)]


@bp.route('/boarder/<path:key>')
def boarder_profile(key):
    """Renders one boarder's profile, addressed by URL-encoded Match Key.

    The key is normalized defensively so punctuation variants collapse to
    the same profile; unknown or empty keys render a clear empty state.
    """
    normalized = normalize_name(key)
    if normalized != key and normalized:
        return redirect(f"/boarder/{quote(normalized)}")

    identity = None
    series = []
    punishments = []
    with connect(read_only=True) as conn:
        if normalized:
            identity = storage.resolve_boarder_identity(conn, normalized)
            series = storage.get_boarder_series(conn, normalized)
            punishments = attach_display_flags(
                storage.list_boarder_punishments(conn, normalized)
            )
    live_punishments = [p for p in punishments if p.status != 'voided']
    voided_punishments = [p for p in punishments if p.status == 'voided']
    escalation_rows = _escalation_rows(series, live_punishments)

    return render_template('boarder.html', **_page_context(
        identity=identity,
        series=series,
        summary=build_profile_summary(series),
        chart_payload=_chart_payload(
            [row.month for row in series],
            points=[row.total_points for row in series],
            frequency=[row.frequency for row in series],
            minutes=[row.total_minutes for row in series],
        ),
        live_punishments=live_punishments,
        voided_punishments=voided_punishments,
        escalation_rows=escalation_rows,
    ))


def _punishments_redirect():
    """Builds the /punishments redirect, preserving submitted filter fields."""
    params = {}
    month = request.form.get('month', '').strip()
    status = request.form.get('status', '').strip()
    show_all = request.form.get('show_all', '').strip()
    if month:
        params['month'] = month
    if status:
        params['status'] = status
    if show_all == '1':
        params['show_all'] = show_all
    query = f"?{urlencode(params)}" if params else ""
    return redirect(f"/punishments{query}")


@bp.route('/punishment/<int:punishment_id>/transition', methods=['POST'])
def transition_punishment(punishment_id):
    target = request.form.get('to', '').strip()
    void_reason = request.form.get('void_reason', '').strip() or None

    def attempt():
        with connect() as conn:
            outcome = transition(
                conn,
                punishment_id=punishment_id,
                target=target,
                void_reason=void_reason,
            )

        if isinstance(outcome, TransitionRejected):
            flash(f"Error: {outcome.reason}", "error")
        else:
            current_app.logger.info(
                "Updated Punishment %d to %s", punishment_id, target
            )
            flash(outcome.message, "success")

        return _punishments_redirect()

    def _busy_redirect(exc):
        flash(busy_message(exc.action), "error")
        return _punishments_redirect()

    return _mutate_with_retry(
        "update the punishment",
        attempt,
        _busy_redirect,
        "Punishment update %d hit sustained contention",
        punishment_id,
    )


# Module-level instance keeps the existing entrypoints working
# (``gunicorn app:app``, ``flask --app app run``); importing it performs
# no database I/O.
app = create_app()


if __name__ == '__main__':
    init_db(app)
    app.run(debug=True)
