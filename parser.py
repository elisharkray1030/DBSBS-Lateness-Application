import csv
import io
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

import storage
from records import BoarderRecord, UnparsedTimeRow

START_SECONDS = (7 * 3600) + (41 * 60)
END_SECONDS = (8 * 3600) + (0 * 60)

TIME_PATTERN = re.compile(r"^\d{2}:\d{2}(?::\d{2})?$")

MONTH_LABEL_PATTERN = re.compile(r"^\d{4}-\d{2}$")

CSV_HEADERS = ['Bed', 'Name', 'Frequency', 'Total Minutes Late', 'Total Points']


def _format_unparseable_rows(unparseable_rows):
    return ', '.join(
        f"{row.name} ('{row.raw_value}')" for row in unparseable_rows
    )


@dataclass
class ParseDiagnostics:
    """Diagnostics collected while parsing a monthly log."""

    rows_read: int
    matched_rows: int
    unmatched_names: list[str] = field(default_factory=list)
    unparseable_rows: list[UnparsedTimeRow] = field(default_factory=list)
    has_parseable_data: bool = False


@dataclass
class SavedOutcome:
    """The month report was recorded; carries the saved boarders and diagnostics."""

    month_label: str
    boarders: list[BoarderRecord]
    diagnostics: ParseDiagnostics

    @property
    def boarders_count(self) -> int:
        return sum(1 for b in self.boarders if b.frequency > 0)

    @property
    def message(self) -> str:
        boarder_word = "boarder" if self.boarders_count == 1 else "boarders"
        return (
            f"Monthly report saved for '{self.month_label}' "
            f"with {self.boarders_count} {boarder_word} recorded as late."
        )


@dataclass
class RejectedOutcome:
    """The month report was not recorded; carries the exact rejection reason."""

    month_label: str
    reason: str
    diagnostics: ParseDiagnostics


def parse_time_seconds(value):
    """Parse a strict HH:MM or HH:MM:SS (24-hour) time into seconds, or None."""
    if not TIME_PATTERN.match(value):
        return None

    parts = value.split(':')
    hours, minutes = int(parts[0]), int(parts[1])
    seconds = int(parts[2]) if len(parts) == 3 else 0

    if hours > 23 or minutes > 59 or seconds > 59:
        return None

    return (hours * 3600) + (minutes * 60) + seconds


def parse_namelist_stream(namelist_stream):
    """Parses a namelist stream into (normalized_name, display_name, bed) rows.

    Preserves the display case of names so the master list can be shown as
    entered while still matching logs case-insensitively via the normalized name.
    """
    rows = []
    reader = csv.DictReader(namelist_stream)
    for row in reader:
        display_name = row.get('Name', '').strip()
        bed = row.get('Bed', '').strip()

        # Skip rows with missing name or bed information.
        if not display_name or not bed:
            continue

        rows.append((display_name.upper(), display_name, bed))
    return rows


def load_namelist_rows(namelist_filename):
    """Loads valid boarders as (normalized_name, display_name, bed) rows, or None."""
    try:
        with open(namelist_filename, mode='r', encoding='utf-8-sig') as file:
            return parse_namelist_stream(file)
    except FileNotFoundError:
        return None


def load_namelist(namelist_filename):
    """Loads valid boarders into a mapping of normalized name to bed, or None."""
    rows = load_namelist_rows(namelist_filename)
    if rows is None:
        return None
    return {normalized_name: bed for normalized_name, _display_name, bed in rows}


def boarder_list_to_csv(boarders):
    """Renders the master list to CSV text (Name, Bed) with deterministic order."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Name', 'Bed'])

    for boarder in sorted(boarders, key=lambda b: (b.bed, b.display_name)):
        writer.writerow([boarder.display_name, boarder.bed])

    return output.getvalue()


def parse_log_stream(log_stream, master_list):
    """Parses a monthly log stream into boarder records and diagnostics."""
    if master_list is None:
        master_list = {}

    boarders = {
        name: BoarderRecord(name=name, bed=bed, frequency=0, total_minutes=0, total_points=0)
        for name, bed in master_list.items()
    }

    rows_read = 0
    matched_rows = 0
    unmatched_names = []
    unparseable_rows = []
    has_parseable_data = False

    csv_reader = csv.DictReader(log_stream)

    for row in csv_reader:
        rows_read += 1
        name = row.get('Name', '').strip().upper()

        if not name:
            continue

        if name not in boarders:
            if name not in unmatched_names:
                unmatched_names.append(name)
            continue

        matched_rows += 1
        time_str = row.get('Transaction Time', '').strip()
        current_seconds = parse_time_seconds(time_str)

        if current_seconds is None:
            unparseable_rows.append(UnparsedTimeRow(name=name, raw_value=time_str))
            continue

        has_parseable_data = True

        if START_SECONDS < current_seconds <= END_SECONDS:
            seconds_late = current_seconds - START_SECONDS
            minutes_late = math.ceil(seconds_late / 60)

            boarders[name].frequency += 1
            boarders[name].total_minutes += minutes_late

    for record in boarders.values():
        record.total_points = record.frequency + record.total_minutes

    diagnostics = ParseDiagnostics(
        rows_read=rows_read,
        matched_rows=matched_rows,
        unmatched_names=unmatched_names,
        unparseable_rows=unparseable_rows,
        has_parseable_data=has_parseable_data,
    )
    return diagnostics, list(boarders.values())

def _rejection_reason(diagnostics: ParseDiagnostics, master_list):
    """Returns the exact rejection reason for a log that cannot be saved, else None."""
    if not master_list:
        return "The boarder master list is missing or empty. Check that 'namelist.csv' exists with 'Name' and 'Bed' columns."

    if diagnostics.rows_read == 0:
        return "The uploaded log file is empty or has no data rows."

    if diagnostics.matched_rows == 0:
        if not diagnostics.unmatched_names:
            return (
                "No log rows matched any known boarder and no names could be read "
                "from the file. Check that the log has 'Name' and 'Transaction Time' "
                "columns with a header row."
            )
        unmatched = ', '.join(diagnostics.unmatched_names)
        return (
            "No log rows matched any known boarder in the master list. "
            f"Unmatched names in the log: {unmatched}."
        )

    if not diagnostics.has_parseable_data:
        failing = _format_unparseable_rows(diagnostics.unparseable_rows)
        return (
            "Log rows matched boarders but no transaction time could be parsed. "
            f"Expected 'HH:MM' or 'HH:MM:SS' (24-hour). Failing rows: {failing}."
        )

    return None


def ingest_log(log_stream, month_label, master_list, conn):
    """Owns parse -> decide -> persist -> message for a monthly log.

    Takes the log as a text stream, the month label, the namelist mapping, and a
    history store connection. Returns one outcome: the report saved, or rejected
    with an exact reason. A rejected ingestion leaves the store untouched.
    """
    diagnostics, boarders = parse_log_stream(log_stream, master_list)

    if not MONTH_LABEL_PATTERN.match(month_label):
        return RejectedOutcome(
            month_label=month_label,
            reason=(
                f"Invalid month label '{month_label}'. Use a canonical YYYY-MM "
                "label, e.g. '2026-03'."
            ),
            diagnostics=diagnostics,
        )

    reason = _rejection_reason(diagnostics, master_list)
    if reason is not None:
        return RejectedOutcome(month_label=month_label, reason=reason, diagnostics=diagnostics)

    storage.save_month(conn, boarders, month_label)
    return SavedOutcome(month_label=month_label, boarders=boarders, diagnostics=diagnostics)


def boarders_to_csv(boarders):
    """Renders boarder records to CSV text with a deterministic row order."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)

    for record in sorted(boarders, key=lambda r: (r.bed, r.name)):
        writer.writerow([
            record.bed,
            record.name,
            record.frequency,
            record.total_minutes,
            record.total_points,
        ])

    return output.getvalue()


def export_to_csv(output_filename, boarders):
    """Writes boarder records to a CSV file using the shared CSV writer."""
    if not boarders:
        return

    with open(output_filename, mode='w', newline='', encoding='utf-8') as file:
        file.write(boarders_to_csv(boarders))


def cli_ingest(log_path, master_list, month_label=None):
    """Runs the shared ingestion path over a log file, returning the outcome.

    Defaults the month label to the current month so the CLI can stay on the
    canonical YYYY-MM path without asking the operator for a month.
    """
    if month_label is None:
        month_label = datetime.now().strftime("%Y-%m")
    with open(log_path, mode='r', encoding='utf-8-sig') as log_stream:
        conn = sqlite3.connect(':memory:')
        try:
            storage.create_schema(conn)
            return ingest_log(log_stream, month_label, master_list, conn)
        finally:
            conn.close()


def _fail(message: str) -> int:
    print(message)
    return 1


def cli_main(namelist_path='namelist.csv', log_path='test_data.csv', output_path='lateness_final_report.csv'):
    master_list = load_namelist(namelist_path)
    if master_list is None:
        return _fail(f"{namelist_path} not found in the project root.")

    try:
        outcome = cli_ingest(log_path, master_list)
    except FileNotFoundError:
        return _fail(f"{log_path} not found in the project root.")

    diagnostics = outcome.diagnostics
    print(f"Read {diagnostics.rows_read} log rows, matched {diagnostics.matched_rows}.")
    print(f"Unmatched names: {diagnostics.unmatched_names}")
    print(f"Unparseable rows: {[(r.name, r.raw_value) for r in diagnostics.unparseable_rows]}")

    if isinstance(outcome, RejectedOutcome):
        print(f"Rejected: {outcome.reason}")
        return 1

    export_to_csv(output_path, outcome.boarders)
    print(f"Generated '{output_path}'.")
    return 0


if __name__ == '__main__':
    raise SystemExit(cli_main())
