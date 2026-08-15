import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone

from records import BoarderRecord, HistoryEntry, MonthSummary, Punishment


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS punishments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            bed TEXT NOT NULL,
            month TEXT NOT NULL,
            points_owed INTEGER NOT NULL,
            deadline TEXT NOT NULL,
            status TEXT NOT NULL,
            assigned_at TEXT NOT NULL,
            overdue_at TEXT,
            phone_held_at TEXT,
            submitted_at TEXT,
            voided_at TEXT,
            void_reason TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_punishments_active
        ON punishments(normalized_name, month)
        WHERE status != 'voided'
        """
    )
    conn.commit()


def save_month(
    conn: sqlite3.Connection, boarders: Iterable[BoarderRecord], month_label: str
) -> None:
    """Upserts each boarder's month summary, replacing the row for a boarder+month."""
    boarders = list(boarders)
    if not boarders or not month_label:
        return

    imported_at = datetime.now(tz=timezone.utc).isoformat()

    for record in boarders:
        conn.execute(
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
                record.name,
                record.display_name,
                record.bed,
                month_label,
                record.frequency,
                record.total_minutes,
                record.total_points,
                imported_at,
            ),
        )
    conn.commit()


def list_months(conn: sqlite3.Connection) -> list[MonthSummary]:
    cursor = conn.execute(
        """
        SELECT month, SUM(CASE WHEN frequency > 0 THEN 1 ELSE 0 END), SUM(total_minutes)
        FROM boarder_history
        GROUP BY month
        ORDER BY month DESC
        """
    )
    return [
        MonthSummary(month=row[0], boarder_count=row[1], total_minutes=row[2])
        for row in cursor.fetchall()
    ]


def get_month_report(conn: sqlite3.Connection, month_label: str) -> list[BoarderRecord]:
    if not month_label:
        return []

    cursor = conn.execute(
        """
        SELECT normalized_name, bed, frequency, total_minutes, total_points
        FROM boarder_history
        WHERE month = ?
        ORDER BY bed ASC, display_name ASC
        """,
        (month_label,),
    )
    return [
        BoarderRecord(
            name=row[0],
            bed=row[1],
            frequency=row[2],
            total_minutes=row[3],
            total_points=row[4],
        )
        for row in cursor.fetchall()
    ]


def search_history(conn: sqlite3.Connection, name_query: str) -> list[HistoryEntry]:
    if not name_query:
        return []

    normalized_query = f"%{name_query.strip().upper()}%"
    cursor = conn.execute(
        """
        SELECT display_name, bed, month, frequency, total_minutes, total_points
        FROM boarder_history
        WHERE normalized_name LIKE ?
        ORDER BY display_name, month ASC
        """,
        (normalized_query,),
    )
    return [
        HistoryEntry(
            display_name=row[0],
            bed=row[1],
            month=row[2],
            frequency=row[3],
            total_minutes=row[4],
            total_points=row[5],
        )
        for row in cursor.fetchall()
    ]


def delete_month(conn: sqlite3.Connection, month_label: str) -> int:
    cursor = conn.execute(
        "DELETE FROM boarder_history WHERE month = ?", (month_label,)
    )
    conn.commit()
    return cursor.rowcount


def assign_punishments(
    conn: sqlite3.Connection,
    month: str,
    boarders: Iterable[BoarderRecord],
    deadline: str,
    assigned_at: str,
) -> None:
    """Saves one punishment per boarder, snapshotting name, bed, and points."""
    for boarder in boarders:
        conn.execute(
            """
            INSERT INTO punishments (
                normalized_name, display_name, bed, month, points_owed,
                deadline, status, assigned_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'assigned', ?)
            """,
            (
                boarder.name,
                boarder.display_name,
                boarder.bed,
                month,
                boarder.total_points,
                deadline,
                assigned_at,
            ),
        )
    conn.commit()


def _punishment_from_row(row) -> Punishment:
    return Punishment(
        id=row[0],
        normalized_name=row[1],
        display_name=row[2],
        bed=row[3],
        month=row[4],
        points_owed=row[5],
        deadline=row[6],
        status=row[7],
        assigned_at=row[8],
        overdue_at=row[9],
        phone_held_at=row[10],
        submitted_at=row[11],
        voided_at=row[12],
        void_reason=row[13],
    )


_PUNISHMENT_COLUMNS = """
    id, normalized_name, display_name, bed, month, points_owed,
    deadline, status, assigned_at, overdue_at, phone_held_at,
    submitted_at, voided_at, void_reason
"""


def list_punishments(
    conn: sqlite3.Connection,
    statuses: Iterable[str] | None = None,
    month: str | None = None,
) -> list[Punishment]:
    """Lists punishments, soonest deadline first; filters by statuses/month."""
    conditions = []
    params: list[str] = []
    if statuses is not None:
        placeholders = ", ".join("?" for _ in statuses)
        conditions.append(f"status IN ({placeholders})")
        params.extend(statuses)
    if month is not None:
        conditions.append("month = ?")
        params.append(month)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    cursor = conn.execute(
        f"""
        SELECT {_PUNISHMENT_COLUMNS}
        FROM punishments
        {where}
        ORDER BY deadline ASC, normalized_name ASC
        """,
        params,
    )
    return [_punishment_from_row(row) for row in cursor.fetchall()]


def get_punishment(conn: sqlite3.Connection, punishment_id: int) -> Punishment | None:
    cursor = conn.execute(
        f"""
        SELECT {_PUNISHMENT_COLUMNS}
        FROM punishments
        WHERE id = ?
        """,
        (punishment_id,),
    )
    row = cursor.fetchone()
    return _punishment_from_row(row) if row is not None else None


def transition_punishment(
    conn: sqlite3.Connection,
    punishment_id: int,
    status: str,
    timestamp: str,
    void_reason: str | None = None,
) -> None:
    """Applies a new status and stamps the matching timestamp column."""
    column = {
        "overdue": "overdue_at",
        "phone_held": "phone_held_at",
        "submitted": "submitted_at",
        "voided": "voided_at",
    }[status]

    if status == "voided":
        conn.execute(
            f"""
            UPDATE punishments
            SET status = ?, {column} = ?, void_reason = ?
            WHERE id = ?
            """,
            (status, timestamp, void_reason, punishment_id),
        )
    else:
        conn.execute(
            f"""
            UPDATE punishments
            SET status = ?, {column} = ?
            WHERE id = ?
            """,
            (status, timestamp, punishment_id),
        )
    conn.commit()
