import io
import os
import sqlite3
from contextlib import closing
from urllib.parse import urlencode

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
    TransitionRejected,
    assign_batch,
    humanized_status,
    list_consequences,
    transition,
)
from records import normalize_name

app = Flask(__name__)
# Sessions carry only transient flash feedback (no auth, no secrets); the
# fallback key keeps single-process local deployments working without config.
app.secret_key = os.environ.get("SECRET_KEY", "dbs-lateness-dashboard-local")
app.jinja_env.globals["humanized_status"] = humanized_status

DB_PATH = os.environ.get("DB_PATH", "lateness_history.db")
NAMELIST_PATH = os.environ.get("NAMELIST_PATH", "namelist.csv")


def connect():
    """Opens a file-backed history store connection for the current call site."""
    return closing(sqlite3.connect(DB_PATH))


SEED_FLAG = "boarders_seeded"


def init_db():
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


def _consume_flashes():
    """Returns (message, error) from one-shot session flash feedback."""
    message = None
    error = None
    for category, text in get_flashed_messages(with_categories=True):
        if category == "error":
            error = text
        else:
            message = text
    return message, error


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
    history_results = None
    selected_tab = 'reports'
    message = None
    error = None

    with connect() as conn:
        all_months = storage.list_months(conn)
        boarders = storage.list_boarders(conn)
        punishment_months = _punishment_months(conn, all_months)

    if request.method == 'POST':
        if 'log_file' in request.files:
            file = request.files['log_file']
            month_label = request.form.get('report_month', '').strip()

            if not file or file.filename == '':
                error = "Error: No file selected."
            elif not month_label:
                error = "Please enter a valid month label for this report. Example: '2026-03'."
            else:
                with connect() as conn:
                    master_list = storage.boarder_master_list(conn)
                    log_stream = io.TextIOWrapper(file.stream, encoding='utf-8-sig')
                    try:
                        outcome = ingest_log(log_stream, month_label, master_list, conn)
                    finally:
                        log_stream.detach()

                    if isinstance(outcome, RejectedOutcome):
                        error = f"Error: {outcome.reason}"
                    else:
                        flash(outcome.message, "success")
                        query = urlencode({"month": month_label})
                        return redirect(f"/?{query}")

    # Boarder History search submits as a native GET form; every search
    # renders its results (or the neutral no-matches empty state) directly.
    search_name = request.args.get('search_name')
    if search_name is not None:
        selected_tab = 'history'
        search_name = search_name.strip()
        if not search_name:
            error = "Please enter a boarder name to search Boarder History."
        else:
            with connect() as conn:
                history_results = storage.search_history(conn, search_name)

    flash_message, flash_error = _consume_flashes()
    message = message or flash_message
    error = error or flash_error

    if request.args.get('month'):
        month_param = request.args['month']
        with connect() as conn:
            if storage.get_month_report(conn, month_param):
                current_month = month_param
                selected_tab = 'reports'

    return render_template(
        'index.html',
        history_results=history_results,
        selected_tab=selected_tab,
        message=message,
        error=error,
        all_months=all_months,
        current_month=current_month,
        boarders=boarders,
        punishment_months=punishment_months,
        punishments=[],
        consequences_show_all=False,
        consequences_month=None,
        consequences_status=None,
    )


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
    error = _validate_boarder(display_name, bed)
    if error:
        return _render_boarders(error=error)
    with connect() as conn:
        storage.add_boarder(conn, normalize_name(display_name), display_name, bed)
    return redirect('/boarders')


@app.route('/api/boarders/<int:boarder_id>', methods=['PATCH'])
def api_edit_boarder(boarder_id):
    data = request.get_json(silent=True) or {}
    display_name = str(data.get('name', '')).strip()
    bed = str(data.get('bed', '')).strip()
    error = _validate_boarder(display_name, bed, exclude_id=boarder_id)
    if error:
        return jsonify({'ok': False, 'error': error}), 400
    with connect() as conn:
        storage.update_boarder(conn, boarder_id, normalize_name(display_name), display_name, bed)
    return jsonify({'ok': True})


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

    try:
        with connect() as conn:
            storage.update_boarders(conn, updates)
    except ValueError as exc:
        return jsonify({'ok': False, 'error': f'Error: {exc}'}), 400
    return jsonify({'ok': True})


@app.route('/api/boarders/<int:boarder_id>', methods=['DELETE'])
def api_delete_boarder(boarder_id):
    with connect() as conn:
        storage.delete_boarder(conn, boarder_id)
    return jsonify({'ok': True})


@app.route('/boarders/import', methods=['POST'])
def import_boarders():
    file = request.files.get('boarder_csv')
    if not file or file.filename == '':
        return _render_boarders(error="Error: No CSV file selected.")

    with connect() as conn:
        log_stream = io.TextIOWrapper(file.stream, encoding='utf-8-sig')
        try:
            rows = parse_namelist_stream(log_stream)
        finally:
            log_stream.detach()

        try:
            storage.replace_boarders(conn, rows)
        except ValueError as exc:
            return _render_boarders(error=f"Error: {exc}")
    return redirect('/boarders')


@app.route('/boarders/export')
def export_boarders():
    with connect() as conn:
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
    with connect() as conn:
        boarders_list = storage.list_boarders(conn)
        all_months = storage.list_months(conn)
        punishment_months = _punishment_months(conn, all_months)
    return render_template(
        'index.html',
        history_results=None,
        selected_tab='boarders',
        message=message,
        error=error,
        all_months=all_months,
        current_month=None,
        boarders=boarders_list,
        punishment_months=punishment_months,
        punishments=[],
        consequences_show_all=False,
        consequences_month=None,
        consequences_status=None,
    )


@app.route('/api/month/<path:month>')
def api_month(month):
    with connect() as conn:
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
    with connect() as conn:
        boarders = storage.get_month_report(conn, month)
    if not boarders:
        return f"Error: No report found for {month}.", 404

    safe_month = month.replace('/', '-').replace(' ', '_')
    return build_csv_response(boarders, f"Monthly_Lateness_Report_{safe_month}.csv")


@app.route('/delete_month/<path:month>', methods=['DELETE'])
def delete_month(month):
    with connect() as conn:
        deleted_count = storage.delete_month(conn, month)

    if deleted_count == 0:
        return jsonify({'error': f'No report found for {month}.'}), 404

    return jsonify({'success': True, 'deleted': deleted_count})


@app.route('/assign/<path:month>', methods=['POST'])
def assign_month(month):
    deadline = request.form.get('deadline', '').strip()
    if not deadline:
        flash("Error: a deadline is required to assign punishments.", "error")
        return _consequences_redirect()

    # Positive consent: each checked box means "assign a punishment to this
    # boarder"; every eligible boarder not checked is exempted.
    selected = set(request.form.getlist('assign'))
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


@app.route('/consequences')
def consequences():
    show_all = request.args.get('show_all') == '1'
    month = request.args.get('month') or None
    status = request.args.get('status') or None
    with connect() as conn:
        punishments = list_consequences(conn, show_all=show_all, month=month, status=status)
        all_months = storage.list_months(conn)
        boarders = storage.list_boarders(conn)
        punishment_months = _punishment_months(conn, all_months)

    message, error = _consume_flashes()

    return render_template(
        'index.html',
        history_results=None,
        selected_tab='consequences',
        message=message,
        error=error,
        all_months=all_months,
        current_month=None,
        boarders=boarders,
        punishment_months=punishment_months,
        punishments=punishments,
        consequences_show_all=show_all,
        consequences_month=month,
        consequences_status=status,
    )


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


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
