import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from uuid import uuid4

from records import (
    AllTimeEntry,
    Boarder,
    BoarderMonth,
    HouseTrendPoint,
    BoarderRecord,
    HistoryEntry,
    MonthSummary,
    Punishment,
    boarder_sort_key,
    normalize_name,
    sort_boarder_records,
)

_BOARDERS_COLUMNS = """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    bed TEXT NOT NULL UNIQUE
"""

# Meta key holding how many stored rows kept their legacy Match Key because
# another row already claimed their new key (same pattern as boarders_seeded).
MIGRATION_SKIPS_KEY = "match_key_migration_skips"


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS boarders ({_BOARDERS_COLUMNS}
        )
        """
    )
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
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
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
    _migrate_boarders_bed_unique(conn)
    _migrate_normalized_name_keys(conn)
    conn.commit()


def _migrate_normalized_name_keys(conn: sqlite3.Connection) -> None:
    """Re-keys stored rows onto the punctuation-insensitive match key.

    Older builds stored match keys under uppercase-and-trim only, so
    master-list entries like 'SURNAME, Given' never matched log rows like
    'SURNAME Given'. Re-normalizes every stored key in place so joins between
    boarders, history, and punishments stay intact. When two rows collapse
    onto the same key, the first row (lowest id) claims it and later rows
    keep their previous key rather than failing startup; each kept key is
    counted under MIGRATION_SKIPS_KEY so the collision stays visible.
    """
    skipped = 0
    for table in ("boarders", "boarder_history", "punishments"):
        rows = conn.execute(
            f"SELECT id, normalized_name FROM {table} ORDER BY id"
        ).fetchall()
        for row_id, old_key in rows:
            new_key = normalize_name(old_key)
            if new_key == old_key:
                continue
            try:
                conn.execute(
                    f"UPDATE {table} SET normalized_name = ? WHERE id = ?",
                    (new_key, row_id),
                )
            except sqlite3.IntegrityError:
                skipped += 1
                continue

    _set_meta_row(conn, MIGRATION_SKIPS_KEY, str(skipped))


def _set_meta_row(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upserts one meta row without committing; callers own transaction scope."""
    conn.execute(
        """
        INSERT INTO meta (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def get_migration_skips(conn: sqlite3.Connection) -> int:
    """Returns how many rows kept a legacy Match Key in the last migration.

    The migration rewrites this count on every startup, so zero means the
    stored roster currently has no collapsed-key collisions at all.
    """
    raw = get_meta(conn, MIGRATION_SKIPS_KEY)
    try:
        return int(raw) if raw is not None else 0
    except ValueError:
        return 0


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Stores one key/value row in the meta table, overwriting an existing key."""
    _set_meta_row(conn, key, value)
    conn.commit()


def _boarders_table_sql(conn: sqlite3.Connection) -> str | None:
    """Returns the boarders table's CREATE statement, or None if absent."""
    cursor = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'boarders'"
    )
    row = cursor.fetchone()
    return row[0] if row is not None else None


def _bed_unique_in_schema(table_sql: str) -> bool:
    """True when the boarders CREATE statement declares a UNIQUE bed."""
    return "bed TEXT NOT NULL UNIQUE" in table_sql


def _migrate_boarders_bed_unique(conn: sqlite3.Connection) -> None:
    """Upgrades a pre-UNIQUE boarders table to the bed-UNIQUE schema.

    Uses create-copy-swap: rename the old table, create the new table with
    the UNIQUE constraint, copy rows across preserving ids, and drop the old
    table. Safe because the current database contains no duplicate beds.
    """
    table_sql = _boarders_table_sql(conn)
    if table_sql is None or _bed_unique_in_schema(table_sql):
        return

    conn.execute("ALTER TABLE boarders RENAME TO boarders_old")
    conn.execute(
        f"""
        CREATE TABLE boarders ({_BOARDERS_COLUMNS}
        )
        """
    )
    conn.execute(
        """
        INSERT INTO boarders (id, normalized_name, display_name, bed)
        SELECT id, normalized_name, display_name, bed
        FROM boarders_old
        """
    )
    conn.execute("DROP TABLE boarders_old")
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Returns the stored value for a meta key, or None if the key is absent."""
    cursor = conn.execute("SELECT value FROM meta WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row is not None else None


def list_boarders(conn: sqlite3.Connection) -> list[Boarder]:
    """Returns the current master list, ordered by bed then display name."""
    cursor = conn.execute(
        """
        SELECT id, normalized_name, display_name, bed
        FROM boarders
        ORDER BY bed ASC, display_name ASC
        """
    )
    return [
        Boarder(id=row[0], normalized_name=row[1], display_name=row[2], bed=row[3])
        for row in cursor.fetchall()
    ]


def boarder_master_list(conn: sqlite3.Connection) -> dict[str, Boarder]:
    """Returns {normalized_name: Boarder} for log ingestion matching.

    Each Boarder carries the canonical display name alongside the bed, so
    ingestion can preserve normalized identity without losing the display
    name staff see in the Boarders tab.
    """
    cursor = conn.execute(
        """
        SELECT normalized_name, display_name, bed
        FROM boarders
        """
    )
    return {
        row[0]: Boarder(normalized_name=row[0], display_name=row[1], bed=row[2])
        for row in cursor.fetchall()
    }


def add_boarder(
    conn: sqlite3.Connection,
    normalized_name: str,
    display_name: str,
    bed: str,
) -> int:
    """Adds one boarder to the master list, returning the new row id."""
    cursor = conn.execute(
        """
        INSERT INTO boarders (normalized_name, display_name, bed)
        VALUES (?, ?, ?)
        """,
        (normalized_name, display_name, bed),
    )
    conn.commit()
    lastrowid = cursor.lastrowid
    if lastrowid is None:
        raise RuntimeError("Insert succeeded but no row id was returned.")
    return lastrowid


def boarder_exists(
    conn: sqlite3.Connection,
    normalized_name: str,
    exclude_id: int | None = None,
) -> bool:
    """True if a boarder with this normalized name is on the list.

    Pass exclude_id to ignore one row (used when renaming that row to its
    own current name).
    """
    if exclude_id is None:
        cursor = conn.execute(
            "SELECT 1 FROM boarders WHERE normalized_name = ?",
            (normalized_name,),
        )
    else:
        cursor = conn.execute(
            "SELECT 1 FROM boarders WHERE normalized_name = ? AND id != ?",
            (normalized_name, exclude_id),
        )
    return cursor.fetchone() is not None


def bed_exists(
    conn: sqlite3.Connection,
    bed: str,
    exclude_id: int | None = None,
) -> bool:
    """True if another boarder is already assigned this bed.

    Pass exclude_id to ignore one row (used when editing that row and
    keeping its own bed).
    """
    if exclude_id is None:
        cursor = conn.execute(
            "SELECT 1 FROM boarders WHERE bed = ?",
            (bed,),
        )
    else:
        cursor = conn.execute(
            "SELECT 1 FROM boarders WHERE bed = ? AND id != ?",
            (bed, exclude_id),
        )
    return cursor.fetchone() is not None


def update_boarder(
    conn: sqlite3.Connection,
    boarder_id: int,
    normalized_name: str,
    display_name: str,
    bed: str,
) -> None:
    """Updates one boarder's name and bed; no-op if the id is unknown."""
    conn.execute(
        """
        UPDATE boarders
        SET normalized_name = ?, display_name = ?, bed = ?
        WHERE id = ?
        """,
        (normalized_name, display_name, bed, boarder_id),
    )
    conn.commit()


def update_boarders(
    conn: sqlite3.Connection,
    updates: Iterable[tuple[int, str, str, str]],
) -> None:
    """Atomically updates several master-list rows after validating the final roster."""
    updates = list(updates)
    if len({boarder_id for boarder_id, _, _, _ in updates}) != len(updates):
        raise ValueError("A boarder was included more than once.")

    conn.execute("BEGIN IMMEDIATE")
    try:
        current = {boarder.id: boarder for boarder in list_boarders(conn)}
        proposed = dict(current)

        for boarder_id, normalized_name, display_name, bed in updates:
            if boarder_id not in current:
                raise ValueError("A boarder could not be found.")
            if not display_name:
                raise ValueError("A boarder name is required.")
            if not bed:
                raise ValueError("A bed is required.")
            proposed[boarder_id] = Boarder(
                id=boarder_id,
                normalized_name=normalized_name,
                display_name=display_name,
                bed=bed,
            )

        names: dict[str, Boarder] = {}
        beds: dict[str, Boarder] = {}
        for boarder in proposed.values():
            other = names.get(boarder.normalized_name)
            if other is not None and other.id != boarder.id:
                raise ValueError(
                    f"A boarder named '{boarder.display_name}' is already on the list."
                )
            names[boarder.normalized_name] = boarder

            other = beds.get(boarder.bed)
            if other is not None and other.id != boarder.id:
                raise ValueError(
                    f"Bed '{boarder.bed}' is already assigned to another boarder."
                )
            beds[boarder.bed] = boarder

        token = uuid4().hex
        for boarder_id, _, _, _ in updates:
            conn.execute(
                """
                UPDATE boarders
                SET normalized_name = ?, bed = ?
                WHERE id = ?
                """,
                (
                    f"__pending_name_{token}_{boarder_id}",
                    f"__pending_bed_{token}_{boarder_id}",
                    boarder_id,
                ),
            )
        for boarder_id, normalized_name, display_name, bed in updates:
            conn.execute(
                """
                UPDATE boarders
                SET normalized_name = ?, display_name = ?, bed = ?
                WHERE id = ?
                """,
                (normalized_name, display_name, bed, boarder_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def delete_boarder(conn: sqlite3.Connection, boarder_id: int) -> None:
    """Removes one boarder from the master list; no-op if the id is unknown."""
    conn.execute(
        "DELETE FROM boarders WHERE id = ?",
        (boarder_id,),
    )
    conn.commit()


def replace_boarders(
    conn: sqlite3.Connection,
    rows: Iterable[Boarder],
) -> None:
    """Replaces the entire master list with the given boarders.

    Duplicate normalized names resolve last-row-wins before any validation.
    When two different boarders claim the same Bed, a ValueError is raised and
    the existing roster is left untouched, so a bad CSV can never partially
    replace the master list.
    """
    deduped = {boarder.normalized_name: boarder for boarder in rows}
    by_bed: dict[str, Boarder] = {}
    for boarder in deduped.values():
        other = by_bed.get(boarder.bed)
        if other is not None and other.normalized_name != boarder.normalized_name:
            raise ValueError(
                f"Bed '{boarder.bed}' is assigned to both '{other.display_name}' "
                f"and '{boarder.display_name}'. Assign each Bed to one Boarder "
                "and Import again."
            )
        by_bed[boarder.bed] = boarder

    conn.execute("DELETE FROM boarders")
    for boarder in deduped.values():
        conn.execute(
            """
            INSERT INTO boarders (normalized_name, display_name, bed)
            VALUES (?, ?, ?)
            """,
            (boarder.normalized_name, boarder.display_name, boarder.bed),
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
                display_name = excluded.display_name,
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


def get_boarder_series(
    conn: sqlite3.Connection, normalized_name: str
) -> list[BoarderMonth]:
    """Returns one boarder's saved month rows in chronological order.

    Reads frozen history snapshots by Match Key, so a Removed boarder's
    series survives removal untouched.
    """
    cursor = conn.execute(
        """
        SELECT month, frequency, total_minutes, total_points
        FROM boarder_history
        WHERE normalized_name = ?
        ORDER BY month ASC
        """,
        (normalized_name,),
    )
    return [
        BoarderMonth(month=row[0], frequency=row[1], total_minutes=row[2],
                     total_points=row[3])
        for row in cursor.fetchall()
    ]


def resolve_boarder_identity(
    conn: sqlite3.Connection, normalized_name: str
) -> AllTimeEntry | None:
    """Resolves one Match Key to its All-Time entry, or None if unknown.

    Reuses the derived All-Time List so the profile header inherits the same
    freshest-first identity resolution and Current/Former derivation.
    """
    for entry in list_all_time_boarders(conn):
        if entry.normalized_name == normalized_name:
            return entry
    return None


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


def house_trend(conn: sqlite3.Connection) -> list[HouseTrendPoint]:
    """Returns per-month house-wide lateness totals, chronological ascending.

    Derived live from stored history on every call, so re-imports and month
    deletions are reflected immediately.
    """
    cursor = conn.execute(
        """
        SELECT month, SUM(frequency), SUM(total_minutes)
        FROM boarder_history
        GROUP BY month
        ORDER BY month ASC
        """
    )
    return [
        HouseTrendPoint(month=row[0], incidents=row[1], minutes_late=row[2])
        for row in cursor.fetchall()
    ]


def list_all_time_boarders(conn: sqlite3.Connection) -> list[AllTimeEntry]:
    """Derives the All-Time List: every boarder ever recorded, read-only.

    Unions the Master List with the distinct Match Keys found in Boarder
    History and Punishments (voided included, so audit-only survivors stay
    traceable). An entry is Current when its key sits on the Master List;
    otherwise Former. Identity fields resolve freshest-first: the current
    Master List entry wins; otherwise the freshest snapshot (latest month,
    tie-broken by latest snapshot timestamp). Seen months and lifetime
    totals come from history rows only, since Punishments freeze their own
    points rather than reporting lateness. Current rows sort before Former
    rows, each group by the shared Bed ordering rule then display name.
    """
    master = {boarder.normalized_name: boarder for boarder in list_boarders(conn)}

    seen_months: dict[str, set[str]] = {}
    lifetime: dict[str, list[int]] = {}
    freshest: dict[str, tuple[str, str, str, str]] = {}

    def absorb_snapshot(key: str, display: str, bed: str, month: str, stamp: str) -> None:
        candidate = (month, stamp)
        if key not in freshest or candidate > freshest[key][:2]:
            freshest[key] = (month, stamp, display, bed)

    cursor = conn.execute(
        """
        SELECT normalized_name, display_name, bed, month,
               frequency, total_minutes, total_points, imported_at
        FROM boarder_history
        """
    )
    for key, display, bed, month, freq, minutes, points, imported_at in cursor.fetchall():
        seen_months.setdefault(key, set()).add(month)
        running = lifetime.setdefault(key, [0, 0, 0])
        running[0] += freq
        running[1] += minutes
        running[2] += points
        absorb_snapshot(key, display, bed, month, imported_at)

    cursor = conn.execute(
        "SELECT normalized_name, display_name, bed, month, assigned_at FROM punishments"
    )
    for key, display, bed, month, assigned_at in cursor.fetchall():
        absorb_snapshot(key, display, bed, month, assigned_at)

    entries: list[AllTimeEntry] = []
    for key in set(master) | set(freshest):
        current = master.get(key)
        if current is not None:
            display, bed = current.display_name, current.bed
        else:
            _, _, display, bed = freshest[key]
        months = sorted(seen_months.get(key, ()))
        freq, minutes, points = lifetime.get(key, [0, 0, 0])
        entries.append(
            AllTimeEntry(
                normalized_name=key,
                display_name=display,
                bed=bed,
                is_current=current is not None,
                first_month=months[0] if months else None,
                last_month=months[-1] if months else None,
                total_frequency=freq,
                total_minutes=minutes,
                total_points=points,
            )
        )
    entries.sort(key=lambda entry: (not entry.is_current, boarder_sort_key(entry)))
    return entries


def list_punishment_months(conn: sqlite3.Connection) -> list[str]:
    """Returns Months represented by Punishments, newest first."""
    cursor = conn.execute(
        """
        SELECT DISTINCT month
        FROM punishments
        ORDER BY month DESC
        """
    )
    return [row[0] for row in cursor.fetchall()]


def get_month_report(conn: sqlite3.Connection, month_label: str) -> list[BoarderRecord]:
    if not month_label:
        return []

    cursor = conn.execute(
        """
        SELECT normalized_name, display_name, bed, frequency, total_minutes, total_points
        FROM boarder_history
        WHERE month = ?
        """,
        (month_label,),
    )
    records = [
        BoarderRecord(
            name=row[0],
            display_name=row[1],
            bed=row[2],
            frequency=row[3],
            total_minutes=row[4],
            total_points=row[5],
        )
        for row in cursor.fetchall()
    ]
    return sort_boarder_records(records)


def search_history(conn: sqlite3.Connection, name_query: str) -> list[HistoryEntry]:
    if not name_query:
        return []

    normalized_query = f"%{normalize_name(name_query)}%"
    cursor = conn.execute(
        """
        SELECT normalized_name, display_name, bed, month, frequency, total_minutes, total_points
        FROM boarder_history
        WHERE normalized_name LIKE ?
        ORDER BY display_name, month ASC
        """,
        (normalized_query,),
    )
    return [
        HistoryEntry(
            normalized_name=row[0],
            display_name=row[1],
            bed=row[2],
            month=row[3],
            frequency=row[4],
            total_minutes=row[5],
            total_points=row[6],
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


def list_boarder_punishments(
    conn: sqlite3.Connection, normalized_name: str
) -> list[Punishment]:
    """Returns every Punishment for one Match Key, chronological by month.

    Reads frozen punishment snapshots by key, so discipline assigned before
    a boarder's removal stays visible on their profile.
    """
    cursor = conn.execute(
        f"""
        SELECT {_PUNISHMENT_COLUMNS}
        FROM punishments
        WHERE normalized_name = ?
        ORDER BY month ASC, assigned_at ASC
        """,
        (normalized_name,),
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
