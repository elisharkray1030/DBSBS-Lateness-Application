import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone

from records import BoarderRecord, HistoryEntry, MonthSummary


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
        SELECT month, COUNT(*), SUM(total_minutes)
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
