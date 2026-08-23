"""Seeds a demo database with deterministic, namelist-accurate data.

Regenerates the local development database contents from namelist.csv:
Monthly Log CSVs for 2026-01..05 and 2026-07..08 (June is left to the real
import) are written to disk and ingested through the production parser, so
every stored figure equals what a staff import would have produced. A
hand-authored persona matrix then makes each implemented feature observable:

- Jason FONG Pak Hin stays at or above the watchlist threshold every seeded
  month, so his streak runs January to May (the missing June breaks it).
- Jasper CHAN Cheuk Yin qualifies February-April and dips in May — exactly
  the boundary case.
- Andy WU Yik Ham and James WONG Wang Hei post identical August totals for
  the Top-N tie-break.
- The remaining personas spread Points across every distribution bucket;
  all-zero boarders exercise the combined Best & worst month card.
- Punishments cover assigned (due and not yet due), submitted on time,
  overdue -> phone held -> submitted late, and voided with a reason; Jason's
  July punishment is assigned before a corrected July re-import raises his
  stored figure (ADR 0001 freeze).
- One quiet boarder is Removed afterwards so Current/Former views have a
  Former entry.

Run: python seed_demo_data.py [--db PATH] [--namelist PATH] [--log-dir PATH]
"""

import argparse
import csv
import io
import os
import sqlite3
from dataclasses import dataclass, field

import app as app_module
import parser as parser_module
import punishments
import storage
from records import BoarderRecord, WatchlistEntry, normalize_name

MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-07", "2026-08"]
REMOVED_BED = "603A"
INCIDENT_DAYS = [3, 9, 17, 23]

# display name -> month -> (incident count, total minutes late). Everyone
# absent stays at zero lateness; the parser still saves their zero row.
PERSONAS: dict[str, dict[str, tuple[int, int]]] = {
    # July starts at (2, 14) = 16 Points so the punishment assigned before
    # the corrected re-import freezes at 16.
    "Jason FONG Pak Hin": {**{m: (3, 15) for m in MONTHS}, "2026-07": (2, 14)},
    "Jasper CHAN Cheuk Yin": {
        "2026-01": (1, 4),
        "2026-02": (2, 10),
        "2026-03": (2, 14),
        "2026-04": (2, 10),
        "2026-05": (1, 5),
        "2026-08": (1, 3),
    },
    "Melvin YEUNG Cheng Ye Melvin": {
        "2026-01": (2, 4), "2026-02": (2, 5), "2026-03": (1, 8),
        "2026-04": (2, 6), "2026-05": (1, 5), "2026-07": (2, 4), "2026-08": (1, 6),
    },
    "Elvis WONG Yat Shun": {"2026-01": (1, 2), "2026-04": (1, 4), "2026-08": (1, 1)},
    # The boarder who will be Removed keeps one small month so their frozen
    # snapshots carry both history figures and a pre-removal punishment.
    "Navas YUEN Hiu Nok": {"2026-01": (1, 2)},
    "Klaus CHAN Klaus Fai Tai": {"2026-03": (1, 6)},
}
TIE_PAIR = ["Andy WU Yik Ham", "James WONG Wang Hei"]
for _name in TIE_PAIR:
    PERSONAS[_name] = {"2026-08": (2, 7)}

# Corrected July figures applied by the second (re-)import, after the July
# punishments were frozen: the punishment keeps 16 while the report shows 19.
JULY_CORRECTIONS: dict[str, tuple[int, int]] = {"Jason FONG Pak Hin": (3, 16)}

# Log-name spellings that differ from the Master List entry but share one
# Match Key, proving variant collapse on real ingestion. Applied only in
# VARIANT_MONTH so other months carry the exact Master List spelling.
VARIANT_MONTH = "2026-03"
NAME_VARIANTS: dict[str, str] = {
    "Lucas CHAVEZ MOCAN, Lucas": "LUCAS. CHAVEZ MOCAN,, Lucas",
}

UNKNOWN_NAME = "NEW PROSPECT VISITOR"
UNKNOWN_MONTH = "2026-03"

_NOISE_ROWS = [
    ("1/1/2026", "0:30:24", "In", "DBS", "3/F Staff Entrance", "M.Neo NG"),
    ("2/2/2026", "21:14:02", "Out", "DBS", "3/F Staff Entrance", "GUEST123"),
    ("3/3/2026", "6:34:50", "Out", "DBS", "3/F Staff Entrance", "[RT15] Houseparent's family"),
    ("4/4/2026", "0:39:22", "In", "DBS", "3/F Staff Entrance", "BA2 (SL)"),
    ("5/5/2026", "7:50:10", "In", "DBS", "STEPS GATE", "STEPS GATE GUARD"),
]

CSV_HEADERS = [
    "Transaction Date", "Transaction Time", "Transaction Type", "Panel",
    "Door", "Name", "Other Name", "Staff Code", "Department", "Position",
    "Card ID", "Transaction Log",
]


@dataclass
class MonthOutcome:
    """What ingesting one seeded month produced."""

    month: str
    message: str
    unmatched_names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PunishmentAssignment:
    """One punishment the seeder assigns, with its fixed timestamps."""

    display_name: str
    month: str
    deadline: str
    assigned_at: str


@dataclass(frozen=True)
class PunishmentMove:
    """One lifecycle transition applied to a seeded punishment."""

    display_name: str
    month: str
    target: str
    timestamp: str
    void_reason: str | None = None


@dataclass
class SeedReport:
    """Summary of one seeding run, for the operator to eyeball."""

    month_outcomes: list[MonthOutcome]
    watchlist: list[WatchlistEntry]
    removed_display_name: str | None


def _minutes_split(total_minutes: int, incidents: int) -> list[int]:
    """Splits total minutes across incidents, each at least one minute late."""
    if incidents <= 0:
        return []
    base = max(total_minutes // incidents, 1)
    parts = [base] * incidents
    remainder = total_minutes - base * incidents
    if remainder > 0:
        parts[0] += remainder
    elif remainder < 0:
        # More incidents than minutes requested: clamp so every part >= 1.
        parts = [1] * incidents
    return parts


def _late_time(minutes_late: int) -> str:
    """One gate swipe inside the lateness window: 07:41 < t <= 08:00."""
    seconds = 7 * 3600 + 41 * 60 + minutes_late * 60
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}:{minutes:02d}:00"


def build_log_rows(month: str, overrides: dict[str, tuple[int, int]] | None = None) -> list[list[str]]:
    """Builds one month's access-control rows in the real column layout."""
    rows: list[list[str]] = []
    for noise in _NOISE_ROWS:
        date, time, kind, panel, door, name = noise
        rows.append([date, time, kind, panel, door, name, "", "", "", "", "", ""])
    rows.append(["6/6/2026", "0:39:20", "Invalid Card ID [In]", "DBS", "3/F Staff Entrance", "", "", "", "", "", "E0D362B", ""])
    if month == UNKNOWN_MONTH:
        for day in (11, 24):
            rows.append([
                f"{day}/3/2026",
                "7:52:00", "In", "DBS", "3/F Staff Entrance", UNKNOWN_NAME,
                "", "", "", "", "", "",
            ])
    for display_name, per_month in PERSONAS.items():
        figures = overrides.get(display_name) if overrides else None
        if figures is None:
            figures = per_month.get(month, (0, 0))
        incidents, total_minutes = figures
        if month == VARIANT_MONTH:
            spelling = NAME_VARIANTS.get(display_name, display_name)
        else:
            spelling = display_name
        for index, minutes_late in enumerate(_minutes_split(total_minutes, incidents)):
            day = INCIDENT_DAYS[index % len(INCIDENT_DAYS)]
            rows.append([
                f"{day}/{int(month[5:])}/2026",
                _late_time(minutes_late),
                "In",
                "DBS",
                "3/F Staff Entrance",
                spelling,
                "", "", "", "Student", "", "",
            ])
    return rows


def render_log(rows: list[list[str]]) -> str:
    """Renders log rows as CSV text with the production header."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(CSV_HEADERS)
    writer.writerows(rows)
    return output.getvalue()


def _require_personas(master_list: dict[str, "parser_module.Boarder"]) -> None:
    missing = [
        name for name in PERSONAS if normalize_name(name) not in master_list
    ]
    if missing:
        raise ValueError(
            "namelist.csv is missing persona boarders: " + ", ".join(missing)
        )


def _boarder_records(
    conn: sqlite3.Connection,
    master_list: dict[str, "parser_module.Boarder"],
    display_name: str,
    month: str,
) -> list[BoarderRecord]:
    key = normalize_name(display_name)
    boarder = master_list[key]
    series_row = next(
        (
            row
            for row in storage.get_boarder_series(conn, key)
            if row.month == month
        ),
        None,
    )
    if series_row is None:
        raise ValueError(f"{display_name} has no stored row for {month}.")
    return [
        BoarderRecord(
            name=key,
            display_name=boarder.display_name,
            bed=boarder.bed,
            frequency=series_row.frequency,
            total_minutes=series_row.total_minutes,
            total_points=series_row.total_points,
        )
    ]


_PUNISHMENT_PLAN = [
    PunishmentAssignment("Jason FONG Pak Hin", "2026-01", "2026-02-10", "2026-02-01T09:00:00+00:00"),
    PunishmentAssignment("Jason FONG Pak Hin", "2026-02", "2026-03-10", "2026-03-01T09:00:00+00:00"),
    PunishmentAssignment("Jason FONG Pak Hin", "2026-03", "2026-04-10", "2026-04-01T09:00:00+00:00"),
    PunishmentAssignment("Jason FONG Pak Hin", "2026-04", "2026-05-10", "2026-05-01T09:00:00+00:00"),
    PunishmentAssignment("Jason FONG Pak Hin", "2026-05", "2026-09-30", "2026-06-01T09:00:00+00:00"),
    PunishmentAssignment("Jasper CHAN Cheuk Yin", "2026-04", "2026-05-31", "2026-05-01T09:00:00+00:00"),
    # Assigned before the removal below, so the Removed boarder's profile
    # still shows their discipline record (pre-removal visibility).
    PunishmentAssignment("Navas YUEN Hiu Nok", "2026-01", "2026-02-10", "2026-02-01T09:00:00+00:00"),
    PunishmentAssignment("Jason FONG Pak Hin", "2026-07", "2026-09-10", "2026-08-01T09:00:00+00:00"),
]

_TRANSITIONS = [
    PunishmentMove("Jason FONG Pak Hin", "2026-01", "submitted", "2026-02-08T10:00:00+00:00"),
    PunishmentMove("Jason FONG Pak Hin", "2026-02", "overdue", "2026-03-11T09:00:00+00:00"),
    PunishmentMove("Jason FONG Pak Hin", "2026-02", "phone_held", "2026-03-12T09:00:00+00:00"),
    PunishmentMove("Jason FONG Pak Hin", "2026-02", "submitted", "2026-03-20T09:00:00+00:00"),
    PunishmentMove(
        "Jason FONG Pak Hin", "2026-03", "voided",
        "2026-04-05T09:00:00+00:00", "Excused - boarding duty clash",
    ),
    PunishmentMove("Jasper CHAN Cheuk Yin", "2026-04", "submitted", "2026-05-20T09:00:00+00:00"),
]


def _punishment_id(conn: sqlite3.Connection, key: str, month: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM punishments WHERE normalized_name = ? AND month = ?",
        (key, month),
    ).fetchone()
    return row[0] if row else None


def _apply_punishments(conn, master_list) -> None:
    for plan in _PUNISHMENT_PLAN:
        storage.assign_punishments(
            conn,
            month=plan.month,
            boarders=_boarder_records(conn, master_list, plan.display_name, plan.month),
            deadline=plan.deadline,
            assigned_at=plan.assigned_at,
        )
    for move in _TRANSITIONS:
        key = normalize_name(move.display_name)
        punishment_id = _punishment_id(conn, key, move.month)
        if punishment_id is None:
            raise ValueError(f"No punishment found for {move.display_name} ({move.month}).")
        outcome = punishments.transition(
            conn,
            punishment_id,
            move.target,
            timestamp=move.timestamp,
            void_reason=move.void_reason,
        )
        if isinstance(outcome, punishments.TransitionRejected):
            raise ValueError(outcome.reason)


def _remove_demo_former(conn: sqlite3.Connection) -> str | None:
    match = next(
        (b for b in storage.list_boarders(conn) if b.bed == REMOVED_BED), None
    )
    if match is None:
        return None
    storage.delete_boarder(conn, match.id)
    return match.display_name


def clean_slate(conn: sqlite3.Connection) -> None:
    """Drops every derived row, keeping the Master List untouched."""
    conn.execute("DELETE FROM punishments")
    conn.execute("DELETE FROM boarder_history")
    conn.commit()


def _ingest_month(
    conn: sqlite3.Connection,
    master_list: dict[str, "parser_module.Boarder"],
    month: str,
    text: str,
    log_dir: str | None,
) -> MonthOutcome:
    """Writes one month's log (when a directory is given) and ingests it."""
    if log_dir:
        with open(f"{log_dir}/monthly-log-{month}.csv", "w", encoding="utf-8", newline="") as file:
            file.write(text)
    outcome = parser_module.ingest_log(io.StringIO(text), month, master_list, conn)
    if isinstance(outcome, parser_module.RejectedOutcome):
        raise RuntimeError(f"{month} was rejected: {outcome.reason}")
    return MonthOutcome(
        month=month,
        message=outcome.message,
        unmatched_names=outcome.diagnostics.unmatched_names,
    )


def seed(
    conn: sqlite3.Connection,
    namelist_path: str,
    log_dir: str | None = None,
) -> SeedReport:
    """Runs the whole matrix against an open connection."""
    master_list = parser_module.load_namelist(namelist_path)
    if not master_list:
        raise ValueError(f"Could not load a Master List from '{namelist_path}'.")
    _require_personas(master_list)

    clean_slate(conn)

    month_outcomes: list[MonthOutcome] = []
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    for month in MONTHS:
        text = render_log(build_log_rows(month))
        month_outcomes.append(_ingest_month(conn, master_list, month, text, log_dir))

    _apply_punishments(conn, master_list)

    # The corrected July re-import happens after July's punishment was
    # frozen, demonstrating that assignment snapshots never change.
    corrected = render_log(build_log_rows("2026-07", overrides=JULY_CORRECTIONS))
    _ingest_month(conn, master_list, "2026-07", corrected, log_dir)

    removed_display_name = _remove_demo_former(conn)

    watchlist = storage.repeat_offenders(
        conn,
        threshold=app_module.WATCHLIST_POINTS_THRESHOLD,
        required_months=app_module.WATCHLIST_MIN_STREAK_MONTHS,
    )
    return SeedReport(
        month_outcomes=month_outcomes,
        watchlist=watchlist,
        removed_display_name=removed_display_name,
    )


def main() -> int:
    argument_parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argument_parser.add_argument("--db", default="lateness_history.db")
    argument_parser.add_argument("--namelist", default="namelist.csv")
    argument_parser.add_argument("--log-dir", default="data/raw/seed")
    args = argument_parser.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        storage.create_schema(conn)
        report = seed(conn, args.namelist, log_dir=args.log_dir)
    finally:
        conn.close()

    for outcome in report.month_outcomes:
        print(f"{outcome.month}: {outcome.message}")
    print("Watchlist preview:")
    for entry in report.watchlist:
        print(f"  - {entry.display_name}: {len(entry.months)} months")
    if not report.watchlist:
        print("  (none)")
    if report.removed_display_name:
        print(f"Removed for the Former demo: {report.removed_display_name}")
        print("(undo anytime: Boarders tab -> Import -> choose namelist.csv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
