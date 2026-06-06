import csv
import io
import os
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, render_template, request, send_file

from parser import load_namelist, process_lateness

app = Flask(__name__)

DB_PATH = "lateness_history.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS boarder_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            bed TEXT NOT NULL,
            month TEXT NOT NULL,
            frequency INTEGER NOT NULL,
            total_minutes INTEGER NOT NULL,
            total_points INTEGER NOT NULL,
            imported_at TEXT NOT NULL,
            UNIQUE(normalized_name, month)
        )
        """
    )
    conn.commit()
    conn.close()


def save_monthly_history(boarders_dict, month_label):
    if not boarders_dict or not month_label:
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    imported_at = datetime.utcnow().isoformat()

    for boarder_name, data in boarders_dict.items():
        display_name = boarder_name.title()
        total_points = data['frequency'] + data['total_minutes']

        cursor.execute(
            """
            INSERT INTO boarder_history (
                normalized_name,
                display_name,
                bed,
                month,
                frequency,
                total_minutes,
                total_points,
                imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_name, month) DO UPDATE SET
                bed = excluded.bed,
                frequency = excluded.frequency,
                total_minutes = excluded.total_minutes,
                total_points = excluded.total_points,
                imported_at = excluded.imported_at
            """,
            (
                boarder_name,
                display_name,
                data['bed'],
                month_label,
                data['frequency'],
                data['total_minutes'],
                total_points,
                imported_at,
            ),
        )

    conn.commit()
    conn.close()


def get_all_months():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT DISTINCT month
        FROM boarder_history
        ORDER BY month DESC
        """
    )
    months = [row[0] for row in cursor.fetchall()]
    conn.close()
    return months


def get_month_report(month_label):
    if not month_label:
        return {}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT normalized_name, bed, frequency, total_minutes
        FROM boarder_history
        WHERE month = ?
        ORDER BY bed ASC, display_name ASC
        """,
        (month_label,),
    )
    rows = cursor.fetchall()
    conn.close()

    return {
        row[0]: {
            'bed': row[1],
            'frequency': row[2],
            'total_minutes': row[3],
        }
        for row in rows
    }


def build_csv_response(boarders_dict, download_name):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Bed', 'Name', 'Frequency', 'Total Minutes Late', 'Total Points'])

    for boarder_name, data in boarders_dict.items():
        freq = data['frequency']
        mins = data['total_minutes']
        writer.writerow([data['bed'], boarder_name, freq, mins, freq + mins])

    csv_bytes = io.BytesIO(output.getvalue().encode('utf-8'))
    csv_bytes.seek(0)
    return send_file(
        csv_bytes,
        as_attachment=True,
        download_name=download_name,
        mimetype='text/csv',
    )


def search_history(name_query):
    if not name_query:
        return []

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    normalized_query = f"%{name_query.strip().upper()}%"
    cursor.execute(
        """
        SELECT display_name, bed, month, frequency, total_minutes, total_points
        FROM boarder_history
        WHERE normalized_name LIKE ?
        ORDER BY display_name, month ASC
        """,
        (normalized_query,),
    )
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            'display_name': row[0],
            'bed': row[1],
            'month': row[2],
            'frequency': row[3],
            'total_minutes': row[4],
            'total_points': row[5],
        }
        for row in rows
    ]


# Ensure database schema exists before handling any requests.
init_db()


@app.route('/', methods=['GET', 'POST'])
def home():
    boarders_data = {}
    current_month = None
    history_results = None
    selected_tab = 'upload'
    message = None
    error = None
    all_months = get_all_months()

    if request.method == 'POST':
        if 'log_file' in request.files:
            file = request.files['log_file']
            month_label = request.form.get('report_month', '').strip()

            if not file or file.filename == '':
                error = "Error: No file selected."
            elif not month_label:
                error = "Please enter a valid month label for this report. Example: '2026-03'."
            else:
                temp_log_path = "temp_monthly_log.csv"
                file.save(temp_log_path)

                master_list = load_namelist("namelist.csv")
                boarders_data = process_lateness(temp_log_path, master_list)

                if boarders_data:
                    save_monthly_history(boarders_data, month_label)
                    current_month = month_label
                    all_months = get_all_months()
                    message = f"Monthly report saved for '{month_label}'."
                else:
                    error = "No late boarders were found in that log file."

                if os.path.exists(temp_log_path):
                    os.remove(temp_log_path)

        elif request.form.get('search_name') is not None:
            selected_tab = 'history'
            search_name = request.form.get('search_name', '').strip()
            if not search_name:
                error = "Please enter a boarder name to search the history."
            else:
                history_results = search_history(search_name)
                if not history_results:
                    message = f"No history found for '{search_name}'."
        all_months = get_all_months()

    if not boarders_data and request.method == 'GET' and current_month is None:
        current_month = all_months[0] if all_months else None

    return render_template(
        'index.html',
        boarders=boarders_data,
        history_results=history_results,
        selected_tab=selected_tab,
        message=message,
        error=error,
        all_months=all_months,
        current_month=current_month,
    )


@app.route('/api/month/<path:month>')
def api_month(month):
    boarders = get_month_report(month)
    if not boarders:
        return jsonify({'error': f'No report found for {month}.'}), 404

    return jsonify({'month': month, 'boarders': boarders})


@app.route('/download_month/<path:month>')
def download_month(month):
    boarders = get_month_report(month)
    if not boarders:
        return f"Error: No report found for {month}.", 404

    safe_month = month.replace('/', '-').replace(' ', '_')
    return build_csv_response(boarders, f"Monthly_Lateness_Report_{safe_month}.csv")


@app.route('/delete_month/<path:month>', methods=['DELETE'])
def delete_month(month):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM boarder_history WHERE month = ?", (month,))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted_count == 0:
        return jsonify({'error': f'No report found for {month}.'}), 404

    return jsonify({'success': True, 'deleted': deleted_count})


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
