import io
import os
import sqlite3
from contextlib import closing
from urllib.parse import urlencode

try:
    from flask import Flask, jsonify, redirect, render_template, request, send_file
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
from parser import RejectedOutcome, boarders_to_csv, ingest_log, load_namelist
from punishments import (
    AssignmentRejected,
    TransitionRejected,
    assign_batch,
    list_consequences,
    transition,
)

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "lateness_history.db")
NAMELIST_PATH = os.environ.get("NAMELIST_PATH", "namelist.csv")


def connect():
    """Opens a file-backed history store connection for the current call site."""
    return closing(sqlite3.connect(DB_PATH))


def init_db():
    with connect() as conn:
        storage.create_schema(conn)


def serialize_boarders(boarders):
    return {
        record.name: {
            'bed': record.bed,
            'frequency': record.frequency,
            'total_minutes': record.total_minutes,
            'total_points': record.total_points,
        }
        for record in boarders
    }


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

    if request.method == 'POST':
        if 'log_file' in request.files:
            file = request.files['log_file']
            month_label = request.form.get('report_month', '').strip()

            if not file or file.filename == '':
                error = "Error: No file selected."
            elif not month_label:
                error = "Please enter a valid month label for this report. Example: '2026-03'."
            else:
                master_list = load_namelist(NAMELIST_PATH)
                with connect() as conn:
                    log_stream = io.TextIOWrapper(file.stream, encoding='utf-8-sig')
                    try:
                        outcome = ingest_log(log_stream, month_label, master_list, conn)
                    finally:
                        log_stream.detach()

                    if isinstance(outcome, RejectedOutcome):
                        error = f"Error: {outcome.reason}"
                    else:
                        current_month = month_label
                        selected_tab = 'reports'
                        all_months = storage.list_months(conn)
                        message = outcome.message

        elif request.form.get('search_name') is not None:
            selected_tab = 'history'
            search_name = request.form.get('search_name', '').strip()
            if not search_name:
                error = "Please enter a boarder name to search the history."
            else:
                with connect() as conn:
                    history_results = storage.search_history(conn, search_name)
                if not history_results:
                    message = f"No history found for '{search_name}'."
        else:
            with connect() as conn:
                all_months = storage.list_months(conn)

    if request.args.get('message'):
        message = request.args['message']
    if request.args.get('month'):
        current_month = request.args['month']
        selected_tab = 'reports'

    return render_template(
        'index.html',
        history_results=history_results,
        selected_tab=selected_tab,
        message=message,
        error=error,
        all_months=all_months,
        current_month=current_month,
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

    return jsonify({'month': month, 'boarders': serialize_boarders(boarders)})


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
        return "Error: a deadline is required to assign punishments.", 400

    exempted = set(request.form.getlist('exempt'))
    with connect() as conn:
        boarders = storage.get_month_report(conn, month)
        if not boarders:
            return f"Error: No report found for {month}.", 404

        outcome = assign_batch(
            conn,
            month=month,
            boarders=boarders,
            exemptions=exempted,
            deadline=deadline,
        )

    if isinstance(outcome, AssignmentRejected):
        return f"Error: {outcome.reason}", 400

    query = urlencode({'month': month, 'message': outcome.message})
    return redirect(f"/?{query}")


@app.route('/consequences')
def consequences():
    show_all = request.args.get('show_all') == '1'
    month = request.args.get('month') or None
    status = request.args.get('status') or None
    with connect() as conn:
        punishments = list_consequences(conn, show_all=show_all, month=month, status=status)
        all_months = storage.list_months(conn)

    return render_template(
        'index.html',
        history_results=None,
        selected_tab='consequences',
        message=None,
        error=None,
        all_months=all_months,
        current_month=None,
        punishments=punishments,
        consequences_show_all=show_all,
        consequences_month=month,
        consequences_status=status,
    )


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
        return f"Error: {outcome.reason}", 400

    return redirect("/consequences")


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
