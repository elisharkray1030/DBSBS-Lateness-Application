import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import NamedTuple
from uuid import uuid4

from records import (
    AllTimeEntry,
    Boarder,
    BoarderIdentity,
    BoarderMonth,
    BoarderRecord,
    Confiscation,
    DistributionBucket,
    HouseTrendPoint,
    IPointAudit,
    IPointAuditDraft,
    IPointAdjustment,
    IPointEntry,
    MonthSummary,
    Punishment,
    TopBoarderEntry,
    WatchlistEntry,
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

# Named bucket edges for the House Dashboard Points-distribution histogram;
# an upper bound of None means unbounded. Labels use en dashes.
POINTS_DISTRIBUTION_BUCKETS = (
    ("≤10", 0, 10),
    ("11–20", 11, 20),
    ("21–30", 21, 30),
    ("31–40", 31, 40),
    ("41–50", 41, 50),
    ("51+", 51, None),
)


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
        CREATE TABLE IF NOT EXISTS ipoint_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_name TEXT NOT NULL,
            points INTEGER NOT NULL,
            occurred_on TEXT NOT NULL,
            reason TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ipoint_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_name TEXT NOT NULL,
            points INTEGER NOT NULL,
            reason TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ipoint_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            normalized_name TEXT NOT NULL,
            action TEXT NOT NULL,
            before_state TEXT,
            after_state TEXT,
            changed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS confiscations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_name TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            bed TEXT NOT NULL DEFAULT '',
            trigger_month TEXT NOT NULL,
            points_redeemed INTEGER NOT NULL,
            tier INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            confirmed_at TEXT,
            release_due TEXT,
            released_at TEXT,
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
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_confiscations_open
        ON confiscations(normalized_name)
        WHERE status IN ('pending', 'active')
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
    for table in (
        "boarders",
        "boarder_history",
        "punishments",
        "ipoint_entries",
        "ipoint_adjustments",
        "ipoint_audit",
        "confiscations",
    ):
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


def freshest_identity_map(conn: sqlite3.Connection) -> dict[str, BoarderIdentity]:
    """Maps every known Match Key to its freshest-first identity."""
    return {
        entry.normalized_name: BoarderIdentity(
            normalized_name=entry.normalized_name,
            display_name=entry.display_name,
            bed=entry.bed,
        )
        for entry in list_all_time_boarders(conn)
    }


def top_boarders(
    conn: sqlite3.Connection,
    month: str | None = None,
    *,
    limit: int,
) -> list[TopBoarderEntry]:
    """Returns the highest-Points boarders within a range, ready for the
    House Dashboard top-N widget.

    ``month`` narrows to one stored month; None means all-time, summing each
    boarder's months. Zero-point boarders never rank. Ties break on total
    frequency then Match Key so the ordering is deterministic. ``limit`` is
    supplied by the caller so the widget's N lives in one place. Identity
    fields resolve freshest-first like everywhere else in the app.
    """
    identity = freshest_identity_map(conn)
    if month is None:
        cursor = conn.execute(
            """
            SELECT normalized_name,
                   SUM(total_points), SUM(frequency), SUM(total_minutes)
            FROM boarder_history
            GROUP BY normalized_name
            HAVING SUM(total_points) > 0
            ORDER BY SUM(total_points) DESC, SUM(frequency) DESC, normalized_name ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
    else:
        cursor = conn.execute(
            """
            SELECT normalized_name, total_points, frequency, total_minutes
            FROM boarder_history
            WHERE month = ? AND total_points > 0
            ORDER BY total_points DESC, frequency DESC, normalized_name ASC
            LIMIT ?
            """,
            (month, limit),
        )
        rows = cursor.fetchall()

    entries = []
    for row in rows:
        who = identity.get(row[0])
        entries.append(
            TopBoarderEntry(
                normalized_name=row[0],
                display_name=who.display_name if who else row[0],
                bed=who.bed if who else "",
                points=row[1],
                frequency=row[2],
                minutes=row[3],
            )
        )
    return entries


def points_distribution(conn: sqlite3.Connection, month: str) -> list[DistributionBucket]:
    """Counts a chosen month's Points values into named buckets.

    Every boarder recorded that month lands in exactly one bucket — zeros
    included — so bucket counts always reconcile against the month's rows.
    """
    counts = {label: 0 for label, _, _ in POINTS_DISTRIBUTION_BUCKETS}
    cursor = conn.execute(
        "SELECT total_points FROM boarder_history WHERE month = ?",
        (month,),
    )
    for (value,) in cursor.fetchall():
        for label, lower, upper in POINTS_DISTRIBUTION_BUCKETS:
            if value >= lower and (upper is None or value <= upper):
                counts[label] += 1
                break
    return [
        DistributionBucket(label=label, lower=lower, upper=upper, count=counts[label])
        for label, lower, upper in POINTS_DISTRIBUTION_BUCKETS
    ]


def repeat_offenders(
    conn: sqlite3.Connection,
    threshold: int,
    required_months: int,
) -> list[WatchlistEntry]:
    """Finds boarders at or above a Points threshold across consecutive months.

    A streak is consecutive calendar months (crossing year boundaries); a
    month re-imported below the threshold breaks it. The longest qualifying
    run is reported. ``threshold`` and ``required_months`` come from the
    application layer's named constants. Identity fields resolve
    freshest-first.
    """
    identity = freshest_identity_map(conn)
    months_above: dict[str, list[str]] = {}
    cursor = conn.execute(
        """
        SELECT normalized_name, month, total_points
        FROM boarder_history
        ORDER BY normalized_name ASC, month ASC
        """
    )
    for key, month_label, points in cursor.fetchall():
        if points >= threshold:
            months_above.setdefault(key, []).append(month_label)

    offenders: list[WatchlistEntry] = []
    for key, months in months_above.items():
        best_run: list[str] = []
        run: list[str] = []
        previous_ordinal: int | None = None
        for month_label in months:
            ordinal = _month_ordinal(month_label)
            if previous_ordinal is not None and ordinal == previous_ordinal + 1:
                run.append(month_label)
            else:
                run = [month_label]
            if len(run) > len(best_run):
                best_run = list(run)
            previous_ordinal = ordinal
        if len(best_run) >= required_months:
            who = identity.get(key)
            offenders.append(
                WatchlistEntry(
                    normalized_name=key,
                    display_name=who.display_name if who else key,
                    bed=who.bed if who else "",
                    months=best_run,
                )
            )
    offenders.sort(key=lambda e: (-len(e.months), e.months[-1], e.display_name))
    return offenders


def _month_ordinal(month_label: str) -> int:
    """Returns a YYYY-MM label as a monotonic month index."""
    year, mon = month_label.split("-")
    return int(year) * 12 + int(mon)


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


def search_boarders(conn: sqlite3.Connection, name_query: str) -> list[AllTimeEntry]:
    """Returns one entry per Match Key whose key contains the query.

    A person lookup, not a history listing: shares the All-Time List's
    derivation wholesale — same union of Master List, Boarder History and
    Punishments keys, freshest-first identity resolution, Current/Former
    status, and sort order — narrowed to keys containing the normalized
    query substring.
    """
    needle = normalize_name(name_query)
    if not needle:
        return []
    return [
        entry
        for entry in list_all_time_boarders(conn)
        if needle in entry.normalized_name
    ]


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
    """Applies a new status and stamps the matching timestamp column.

    Rejects an unknown status with a ValueError before any write, so a
    malformed request can never leak a raw key-lookup crash.
    """
    column_map = {
        "overdue": "overdue_at",
        "phone_held": "phone_held_at",
        "submitted": "submitted_at",
        "voided": "voided_at",
    }
    if status not in column_map:
        raise ValueError(
            f"Unknown punishment status {status!r}; "
            f"valid statuses: {', '.join(column_map)}"
        )
    column = column_map[status]

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


def stage_ipoint_entry(
    conn: sqlite3.Connection,
    normalized_name: str,
    points: int,
    occurred_on: str,
    reason: str,
    recorded_at: str,
) -> int:
    """Stages one I-Point Entry on the open transaction; it does not commit.

    The I-Points lifecycle owns the transaction so an entry and its audit row
    are written together. A standalone caller must commit the connection, or
    the staged row is discarded when it closes.
    """
    cursor = conn.execute(
        """
        INSERT INTO ipoint_entries (
            normalized_name, points, occurred_on, reason, recorded_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (normalized_name, points, occurred_on, reason, recorded_at),
    )
    lastrowid = cursor.lastrowid
    if lastrowid is None:
        raise RuntimeError("Insert succeeded but no row id was returned.")
    return lastrowid


def stage_update_ipoint_entry(
    conn: sqlite3.Connection,
    entry_id: int,
    points: int,
    occurred_on: str,
    reason: str,
) -> None:
    """Stages one I-Point Entry edit on the open transaction; it does not commit.

    The I-Points lifecycle owns the transaction so the ledger and its audit
    row are written together. ``recorded_at`` is the logging timestamp and is
    deliberately left untouched by an edit.
    """
    conn.execute(
        """
        UPDATE ipoint_entries
        SET points = ?, occurred_on = ?, reason = ?
        WHERE id = ?
        """,
        (points, occurred_on, reason, entry_id),
    )


def stage_delete_ipoint_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    """Stages one I-Point Entry removal on the open transaction; it does not commit.

    The prior state survives in the I-Point Audit row written beside this
    delete in the same transaction.
    """
    conn.execute("DELETE FROM ipoint_entries WHERE id = ?", (entry_id,))


def stage_ipoint_adjustment(
    conn: sqlite3.Connection,
    normalized_name: str,
    points: int,
    reason: str,
    recorded_at: str,
) -> int:
    """Stages one I-Point Adjustment on the open transaction; it does not commit.

    The I-Points lifecycle owns the transaction so an Adjustment and its audit
    row are written together. A standalone caller must commit the connection.
    """
    cursor = conn.execute(
        """
        INSERT INTO ipoint_adjustments (
            normalized_name, points, reason, recorded_at
        ) VALUES (?, ?, ?, ?)
        """,
        (normalized_name, points, reason, recorded_at),
    )
    lastrowid = cursor.lastrowid
    if lastrowid is None:
        raise RuntimeError("Insert succeeded but no row id was returned.")
    return lastrowid


def stage_update_ipoint_adjustment(
    conn: sqlite3.Connection,
    adjustment_id: int,
    points: int,
    reason: str,
) -> None:
    """Stages one I-Point Adjustment edit on the open transaction; it does not commit.

    ``recorded_at`` is the logging timestamp and is deliberately left untouched
    by an edit.
    """
    conn.execute(
        """
        UPDATE ipoint_adjustments
        SET points = ?, reason = ?
        WHERE id = ?
        """,
        (points, reason, adjustment_id),
    )


def stage_delete_ipoint_adjustment(
    conn: sqlite3.Connection, adjustment_id: int
) -> None:
    """Stages one I-Point Adjustment removal on the open transaction; it does not commit.

    The prior state survives in the I-Point Audit row written beside this
    delete in the same transaction.
    """
    conn.execute("DELETE FROM ipoint_adjustments WHERE id = ?", (adjustment_id,))


def stage_ipoint_audit(
    conn: sqlite3.Connection, audit: IPointAuditDraft
) -> None:
    """Stages one I-Point Audit row on the open transaction; it does not commit.

    Called beside every ledger mutation so the live ledger and its history
    share one transaction and cannot diverge; the I-Points lifecycle commits.
    """
    conn.execute(
        """
        INSERT INTO ipoint_audit (
            entity_type, entity_id, normalized_name, action,
            before_state, after_state, changed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit.entity_type,
            audit.entity_id,
            audit.normalized_name,
            audit.action,
            audit.before_state,
            audit.after_state,
            audit.changed_at,
        ),
    )


def stage_ipoint_confiscation(
    conn: sqlite3.Connection,
    normalized_name: str,
    trigger_month: str,
    points_redeemed: int,
    tier: int,
    status: str,
    created_at: str,
) -> int:
    """Stages one Confiscation row on the open transaction; it does not commit.

    The I-Points lifecycle owns the commit so the row and its audit row are
    written together; a standalone caller must commit the connection.
    """
    cursor = conn.execute(
        """
        INSERT INTO confiscations (
            normalized_name, trigger_month, points_redeemed, tier, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (normalized_name, trigger_month, points_redeemed, tier, status, created_at),
    )
    lastrowid = cursor.lastrowid
    if lastrowid is None:
        raise RuntimeError("Insert succeeded but no row id was returned.")
    return lastrowid


def stage_confirm_ipoint_confiscation(
    conn: sqlite3.Connection,
    confiscation_id: int,
    display_name: str,
    bed: str,
    confirmed_at: str,
    release_due: str,
) -> None:
    """Stages a pending Confiscation's confirmation on the open transaction.

    Freezes the Boarder's display name and bed and records the fixed release
    date; the status moves from ``pending`` to ``active``. It does not commit.
    """
    conn.execute(
        """
        UPDATE confiscations
        SET status = 'active', display_name = ?, bed = ?,
            confirmed_at = ?, release_due = ?
        WHERE id = ?
        """,
        (display_name, bed, confirmed_at, release_due, confiscation_id),
    )


def stage_void_ipoint_confiscation(
    conn: sqlite3.Connection,
    confiscation_id: int,
    voided_at: str,
    void_reason: str | None,
) -> None:
    """Stages a pending or active Confiscation's voidance on the open transaction.

    It does not commit; the prior state survives in the Confiscation's audit row.
    The lifecycle owns the status guard, so storage only applies the transition.
    """
    conn.execute(
        """
        UPDATE confiscations
        SET status = 'voided', voided_at = ?, void_reason = ?
        WHERE id = ?
        """,
        (voided_at, void_reason, confiscation_id),
    )


def stage_release_ipoint_confiscation(
    conn: sqlite3.Connection,
    confiscation_id: int,
    released_at: str,
) -> None:
    """Stages an active Confiscation's release on the open transaction.

    Records when the phone was returned; the status moves from ``active`` to
    ``released``. It does not commit; the lifecycle owns the commit.
    """
    conn.execute(
        """
        UPDATE confiscations
        SET status = 'released', released_at = ?
        WHERE id = ?
        """,
        (released_at, confiscation_id),
    )


def stage_update_ipoint_confiscation(
    conn: sqlite3.Connection,
    confiscation_id: int,
    points_redeemed: int,
    tier: int,
    release_due: str,
) -> None:
    """Stages an edit of an active Confiscation's redeemable amount and period.

    The tier fixes both ``points_redeemed`` and the recomputed ``release_due``;
    the frozen display name and bed are not touched. It does not commit.
    """
    conn.execute(
        """
        UPDATE confiscations
        SET points_redeemed = ?, tier = ?, release_due = ?
        WHERE id = ?
        """,
        (points_redeemed, tier, release_due, confiscation_id),
    )


def stage_delete_ipoint_confiscation(
    conn: sqlite3.Connection, confiscation_id: int
) -> None:
    """Hard-deletes a live Confiscation on the open transaction.

    The prior state survives in the Confiscation's audit row; it does not commit.
    """
    conn.execute("DELETE FROM confiscations WHERE id = ?", (confiscation_id,))


class _IPointListing(NamedTuple):
    columns: str
    table: str
    order_by: str
    status_column: str | None = None


_IPOINT_ENTRY_LISTING = _IPointListing(
    "id, normalized_name, points, occurred_on, reason, recorded_at",
    "ipoint_entries",
    "occurred_on ASC, id ASC",
)
_IPOINT_ADJUSTMENT_LISTING = _IPointListing(
    "id, normalized_name, points, reason, recorded_at",
    "ipoint_adjustments",
    "id ASC",
)
_IPOINT_AUDIT_LISTING = _IPointListing(
    "id, entity_type, entity_id, normalized_name, action, "
    "before_state, after_state, changed_at",
    "ipoint_audit",
    "id DESC",
)
_IPOINT_CONFISCATION_LISTING = _IPointListing(
    "id, normalized_name, display_name, bed, trigger_month, points_redeemed, "
    "tier, status, created_at, confirmed_at, release_due, released_at, "
    "voided_at, void_reason",
    "confiscations",
    "id ASC",
    status_column="status",
)


def _select_ipoint_rows(
    conn: sqlite3.Connection,
    listing: _IPointListing,
    normalized_name: str | None = None,
    statuses: tuple[str, ...] | None = None,
) -> list[tuple]:
    """Fetches rows through the one WHERE shape every I-Point listing repeats.

    Columns, table, sort order, and whether the table has a status column
    travel together in the listing, so this seam owns the optional Match Key
    equality and the optional status set membership. Passing ``statuses`` for a
    listing that declares no status column is a programming error.
    """
    if statuses is not None and listing.status_column is None:
        raise ValueError(f"{listing.table} has no status column to filter on")
    clauses: list[str] = []
    params: list[str] = []
    if normalized_name is not None:
        clauses.append("normalized_name = ?")
        params.append(normalized_name)
    if statuses is not None:
        placeholders = ", ".join("?" for _ in statuses)
        clauses.append(f"{listing.status_column} IN ({placeholders})")
        params.extend(statuses)
    sql = f"SELECT {listing.columns} FROM {listing.table}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += f" ORDER BY {listing.order_by}"
    return conn.execute(sql, tuple(params)).fetchall()


def _ipoint_entry_from_row(row) -> IPointEntry:
    return IPointEntry(
        id=row[0],
        normalized_name=row[1],
        points=row[2],
        occurred_on=row[3],
        reason=row[4],
        recorded_at=row[5],
    )


def get_ipoint_entry(
    conn: sqlite3.Connection, entry_id: int
) -> IPointEntry | None:
    """Returns one I-Point Entry by id, or None when it is absent."""
    cursor = conn.execute(
        f"SELECT {_IPOINT_ENTRY_LISTING.columns} FROM ipoint_entries WHERE id = ?",
        (entry_id,),
    )
    row = cursor.fetchone()
    return _ipoint_entry_from_row(row) if row is not None else None


def list_ipoint_entries(
    conn: sqlite3.Connection, normalized_name: str | None = None
) -> list[IPointEntry]:
    """Lists I-Point Entries chronologically, optionally for one Match Key."""
    rows = _select_ipoint_rows(conn, _IPOINT_ENTRY_LISTING, normalized_name)
    return [_ipoint_entry_from_row(row) for row in rows]


def _ipoint_adjustment_from_row(row) -> IPointAdjustment:
    return IPointAdjustment(
        id=row[0],
        normalized_name=row[1],
        points=row[2],
        reason=row[3],
        recorded_at=row[4],
    )


def get_ipoint_adjustment(
    conn: sqlite3.Connection, adjustment_id: int
) -> IPointAdjustment | None:
    """Returns one I-Point Adjustment by id, or None when it is absent."""
    cursor = conn.execute(
        f"SELECT {_IPOINT_ADJUSTMENT_LISTING.columns} FROM ipoint_adjustments WHERE id = ?",
        (adjustment_id,),
    )
    row = cursor.fetchone()
    return _ipoint_adjustment_from_row(row) if row is not None else None


def list_ipoint_adjustments(
    conn: sqlite3.Connection, normalized_name: str | None = None
) -> list[IPointAdjustment]:
    """Lists I-Point Adjustments, optionally for one Match Key, oldest first."""
    rows = _select_ipoint_rows(conn, _IPOINT_ADJUSTMENT_LISTING, normalized_name)
    return [_ipoint_adjustment_from_row(row) for row in rows]


def _ipoint_audit_from_row(row) -> IPointAudit:
    return IPointAudit(
        id=row[0],
        entity_type=row[1],
        entity_id=row[2],
        normalized_name=row[3],
        action=row[4],
        before_state=row[5],
        after_state=row[6],
        changed_at=row[7],
    )


def list_ipoint_audit(
    conn: sqlite3.Connection, normalized_name: str | None = None
) -> list[IPointAudit]:
    """Lists I-Point Audit rows, optionally for one Match Key, newest first."""
    rows = _select_ipoint_rows(conn, _IPOINT_AUDIT_LISTING, normalized_name)
    return [_ipoint_audit_from_row(row) for row in rows]


def _ipoint_confiscation_from_row(row) -> Confiscation:
    return Confiscation(
        id=row[0],
        normalized_name=row[1],
        display_name=row[2],
        bed=row[3],
        trigger_month=row[4],
        points_redeemed=row[5],
        tier=row[6],
        status=row[7],
        created_at=row[8],
        confirmed_at=row[9],
        release_due=row[10],
        released_at=row[11],
        voided_at=row[12],
        void_reason=row[13],
    )


def get_ipoint_confiscation(
    conn: sqlite3.Connection, confiscation_id: int
) -> Confiscation | None:
    """Returns one Confiscation by id, or None when it is absent."""
    cursor = conn.execute(
        f"SELECT {_IPOINT_CONFISCATION_LISTING.columns} "
        "FROM confiscations WHERE id = ?",
        (confiscation_id,),
    )
    row = cursor.fetchone()
    return _ipoint_confiscation_from_row(row) if row is not None else None


def get_open_ipoint_confiscation(
    conn: sqlite3.Connection, normalized_name: str
) -> Confiscation | None:
    """Returns a Match Key's open Confiscation (pending or active), if any.

    The partial unique index guarantees at most one; this returns the oldest
    should a legacy database carry more.
    """
    cursor = conn.execute(
        f"SELECT {_IPOINT_CONFISCATION_LISTING.columns} "
        "FROM confiscations "
        "WHERE normalized_name = ? AND status IN ('pending', 'active') "
        "ORDER BY id ASC LIMIT 1",
        (normalized_name,),
    )
    row = cursor.fetchone()
    return _ipoint_confiscation_from_row(row) if row is not None else None


def list_ipoint_confiscations(
    conn: sqlite3.Connection,
    normalized_name: str | None = None,
    statuses: tuple[str, ...] | None = None,
) -> list[Confiscation]:
    """Lists Confiscations, optionally for one Match Key and/or a status set.

    Oldest first; ``statuses=None`` lists every status.
    """
    rows = _select_ipoint_rows(
        conn, _IPOINT_CONFISCATION_LISTING, normalized_name, statuses
    )
    return [_ipoint_confiscation_from_row(row) for row in rows]
