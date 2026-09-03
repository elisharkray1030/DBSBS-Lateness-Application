import io
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import cast
from urllib.parse import quote, urlencode

try:
    from flask import (
        Flask,
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

import storage
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
    humanized_status,
    list_consequences,
    transition,
)
from records import build_profile_summary, normalize_name

app = Flask(__name__)
# Sessions carry only transient flash feedback (no auth, no secrets); the
# fallback key keeps single-process local deployments working without config.
app.secret_key = os.environ.get("SECRET_KEY", "dbs-lateness-dashboard-local")
app.jinja_env.globals["humanized_status"] = humanized_status

DB_PATH = os.environ.get("DB_PATH", "lateness_history.db")
NAMELIST_PATH = os.environ.get("NAMELIST_PATH", "namelist.csv")

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


def connect(read_only: bool = False) -> "closing[sqlite3.Connection]":
    """Opens a file-backed history store connection for the current call site.

    Pure-read surfaces pass ``read_only=True`` so readers never contend for
    the write lock on the shared-NAS database. The mixed import view, all
    state-changing routes and startup init use the default read-write form.
    """
    if read_only and DB_PATH != ":memory:":
        conn = sqlite3.connect(
            Path(DB_PATH).as_uri() + "?mode=ro",
            uri=True,
            timeout=NAS_BUSY_TIMEOUT_S,
        )
        return closing(conn)
    conn = sqlite3.connect(DB_PATH, timeout=NAS_BUSY_TIMEOUT_S)
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


def init_db() -> None:
    with connect() as conn:
        storage.create_schema(conn)
        if storage.get_meta(conn, SEED_FLAG) is not None:
            return
        if storage.list_boarders(conn):
            storage.set_meta(conn, SEED_FLAG, "1")
            return
        rows = load_namelist_rows(NAMELIST_PATH)
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
        'consequences_total': 0,
        'consequences_show_all': False,
        'consequences_month': None,
        'consequences_status': None,
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


# Ensure database schema exists before handling any requests.
init_db()


@app.route('/', methods=['GET', 'POST'])
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
                error = "Error: No file selected."
            elif not month_label:
                error = "Please enter a valid month label for this report. Example: '2026-03'."
            else:
                # Buffer the upload before retrying: each attempt re-reads
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
                        return f"Error: {outcome.reason}"
                    flash(outcome.message, "success")
                    query = urlencode({"month": month_label})
                    return redirect(f"/?{query}")

                try:
                    result = with_lock_retry("import the Monthly Log", attempt)
                except DatabaseBusy as exc:
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


@app.route('/boarders')
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


@app.route('/boarders/add', methods=['POST'])
def add_boarder():
    display_name = request.form.get('name', '').strip()
    bed = request.form.get('bed', '').strip()

    def attempt():
        error = _validate_boarder(display_name, bed)
        if error:
            return _render_boarders(error=error)
        with connect() as conn:
            storage.add_boarder(conn, normalize_name(display_name), display_name, bed)
        return redirect('/boarders')

    try:
        return with_lock_retry("add the boarder", attempt)
    except DatabaseBusy as exc:
        return _render_boarders(error=busy_message(exc.action))


@app.route('/api/boarders/<int:boarder_id>', methods=['PATCH'])
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
        return jsonify({'ok': True})

    try:
        return with_lock_retry("update the boarder", attempt)
    except DatabaseBusy as exc:
        return jsonify({'ok': False, 'error': busy_message(exc.action)}), 503


@app.route('/api/boarders', methods=['PATCH'])
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
        return jsonify({'ok': True})

    try:
        return with_lock_retry("update the Master List", attempt)
    except DatabaseBusy as exc:
        return jsonify({'ok': False, 'error': busy_message(exc.action)}), 503


@app.route('/api/boarders/<int:boarder_id>', methods=['DELETE'])
def api_delete_boarder(boarder_id):
    def attempt():
        with connect() as conn:
            storage.delete_boarder(conn, boarder_id)
        return jsonify({'ok': True})

    try:
        return with_lock_retry("remove the boarder", attempt)
    except DatabaseBusy as exc:
        return jsonify({'ok': False, 'error': busy_message(exc.action)}), 503


@app.route('/boarders/import', methods=['POST'])
def import_boarders():
    file = request.files.get('boarder_csv')
    if not file or file.filename == '':
        return _render_boarders(error="Error: No CSV file selected.")
    # Buffer the upload before retrying: each attempt re-reads these bytes on
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
                return _render_boarders(error=f"Error: {exc}")
        return redirect('/boarders')

    try:
        return with_lock_retry("import the Master List", attempt)
    except DatabaseBusy as exc:
        return _render_boarders(error=busy_message(exc.action))


@app.route('/boarders/export')
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


@app.route('/api/month/<path:month>')
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


@app.route('/download_month/<path:month>')
def download_month(month):
    with connect(read_only=True) as conn:
        boarders = storage.get_month_report(conn, month)
    if not boarders:
        return f"Error: No report found for {month}.", 404

    safe_month = month.replace('/', '-').replace(' ', '_')
    return build_csv_response(boarders, f"Monthly_Lateness_Report_{safe_month}.csv")


@app.route('/delete_month/<path:month>', methods=['DELETE'])
def delete_month(month):
    def attempt():
        with connect() as conn:
            deleted_count = storage.delete_month(conn, month)

        if deleted_count == 0:
            return jsonify({'error': f'No report found for {month}.'}), 404

        return jsonify({'success': True, 'deleted': deleted_count})

    try:
        return with_lock_retry("delete the month's report", attempt)
    except DatabaseBusy as exc:
        return jsonify({'error': busy_message(exc.action)}), 503


@app.route('/assign/<path:month>', methods=['POST'])
def assign_month(month):
    deadline = request.form.get('deadline', '').strip()
    if not deadline:
        flash("Error: a deadline is required to assign punishments.", "error")
        return _consequences_redirect()

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
            return _consequences_redirect()

        flash(outcome.message, "success")
        query = urlencode({'month': month})
        return redirect(f"/?{query}")

    try:
        return with_lock_retry("assign punishments", attempt)
    except DatabaseBusy as exc:
        flash(busy_message(exc.action), "error")
        return _consequences_redirect()


@app.route('/consequences')
def consequences():
    show_all = request.args.get('show_all') == '1'
    month = request.args.get('month') or None
    status = request.args.get('status') or None
    with connect(read_only=True) as conn:
        punishments = list_consequences(conn, show_all=show_all, month=month, status=status)
        consequences_total = len(storage.list_punishments(conn, statuses=NON_VOIDED_STATUSES))
        all_months = storage.list_months(conn)
        boarders = storage.list_boarders(conn)
        punishment_months = _punishment_months(conn, all_months)

    message, error = _consume_flashes()

    return render_template('index.html', **_page_context(
        panels_in_page=True,
        selected_tab='consequences',
        message=message,
        error=error,
        all_months=all_months,
        boarders=boarders,
        punishment_months=punishment_months,
        punishments=punishments,
        consequences_total=consequences_total,
        consequences_show_all=show_all,
        consequences_month=month,
        consequences_status=status,
    ))


@app.route('/statistics')
def statistics():
    """Renders the House Dashboard: the Statistics tab's home.

    Every figure derives live from stored data on each visit, so     re-imports
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


@app.route('/boarder/<path:key>')
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
    ))


def _consequences_redirect():
    """Builds the /consequences redirect, preserving submitted filter fields."""
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
    return redirect(f"/consequences{query}")


@app.route('/punishment/<int:punishment_id>/transition', methods=['POST'])
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
            flash(outcome.message, "success")

        return _consequences_redirect()

    try:
        return with_lock_retry("update the punishment", attempt)
    except DatabaseBusy as exc:
        flash(busy_message(exc.action), "error")
        return _consequences_redirect()


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
