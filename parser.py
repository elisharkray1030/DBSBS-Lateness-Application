import csv
import io
import math
import re
from dataclasses import dataclass

START_SECONDS = (7 * 3600) + (41 * 60)
END_SECONDS = (8 * 3600) + (0 * 60)

TIME_PATTERN = re.compile(r"^\d{2}:\d{2}(?::\d{2})?$")

CSV_HEADERS = ['Bed', 'Name', 'Frequency', 'Total Minutes Late', 'Total Points']


@dataclass
class UnparsedTimeRow:
    name: str
    raw_value: str


@dataclass
class IngestionResult:
    boarders: dict
    rows_read: int
    matched_rows: int
    unmatched_names: list
    unparseable_rows: list
    has_parseable_data: bool


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


def load_namelist(namelist_filename):
    """Loads valid boarders into a dictionary, ignoring casing and spacing errors."""
    boarders_master = {}

    try:
        with open(namelist_filename, mode='r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            for row in reader:
                name = row.get('Name', '').strip().upper()
                bed = row.get('Bed', '').strip()

                # Skip rows with missing name or bed information.
                if not name or not bed:
                    continue

                boarders_master[name] = {
                    "bed": bed,
                }
        return boarders_master
    except FileNotFoundError:
        return None


def process_lateness(log_filename, master_list):
    """Parses the monthly log and returns an IngestionResult with metrics and diagnostics."""
    if master_list is None:
        master_list = {}

    boarders = {
        name: {
            "bed": info["bed"],
            "frequency": 0,
            "total_minutes": 0,
            "total_points": 0,
        }
        for name, info in master_list.items()
    }

    rows_read = 0
    matched_rows = 0
    unmatched_names = []
    unparseable_rows = []
    has_parseable_data = False

    with open(log_filename, mode='r', encoding='utf-8-sig') as file:
        csv_reader = csv.DictReader(file)

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

                boarders[name]["frequency"] += 1
                boarders[name]["total_minutes"] += minutes_late

    for data in boarders.values():
        data["total_points"] = data["frequency"] + data["total_minutes"]

    return IngestionResult(
        boarders=boarders,
        rows_read=rows_read,
        matched_rows=matched_rows,
        unmatched_names=unmatched_names,
        unparseable_rows=unparseable_rows,
        has_parseable_data=has_parseable_data,
    )


def ingestion_rejection_message(result, master_list):
    """Returns a specific error message when a log cannot be saved, else None."""
    if not master_list:
        return "The boarder master list is missing or empty. Check that 'namelist.csv' exists with 'Name' and 'Bed' columns."

    if result.rows_read == 0:
        return "The uploaded log file is empty or has no data rows."

    if result.matched_rows == 0:
        if not result.unmatched_names:
            return (
                "No log rows matched any known boarder and no names could be read "
                "from the file. Check that the log has 'Name' and 'Transaction Time' "
                "columns with a header row."
            )
        unmatched = ', '.join(result.unmatched_names)
        return (
            "No log rows matched any known boarder in the master list. "
            f"Unmatched names in the log: {unmatched}."
        )

    if not result.has_parseable_data:
        failing = ', '.join(
            f"{row.name} ('{row.raw_value}')" for row in result.unparseable_rows
        )
        return (
            "Log rows matched boarders but no transaction time could be parsed. "
            f"Expected 'HH:MM' or 'HH:MM:SS' (24-hour). Failing rows: {failing}."
        )

    return None


def boarders_to_csv(boarders):
    """Renders a boarders mapping to CSV text with a deterministic row order."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)

    for boarder_name in sorted(boarders, key=lambda n: (boarders[n]['bed'], n)):
        data = boarders[boarder_name]
        writer.writerow([
            data['bed'],
            boarder_name,
            data['frequency'],
            data['total_minutes'],
            data['total_points'],
        ])

    return output.getvalue()


def export_to_csv(output_filename, boarders):
    """Writes a boarders mapping to a CSV file using the shared CSV writer."""
    if not boarders:
        return

    with open(output_filename, mode='w', newline='', encoding='utf-8') as file:
        file.write(boarders_to_csv(boarders))


if __name__ == '__main__':
    # --- RUNNING THE ENTIRE PIPELINE ---
    master_list = load_namelist("namelist.csv")
    if master_list is None:
        raise SystemExit("namelist.csv not found in the project root.")

    try:
        result = process_lateness("test_data.csv", master_list)
    except FileNotFoundError:
        raise SystemExit("test_data.csv not found in the project root.")

    export_to_csv("lateness_final_report.csv", result.boarders)

    print(f"Read {result.rows_read} log rows, matched {result.matched_rows}.")
    print(f"Unmatched names: {result.unmatched_names}")
    print(f"Unparseable rows: {[(r.name, r.raw_value) for r in result.unparseable_rows]}")
    print("Generated 'lateness_final_report.csv'.")
