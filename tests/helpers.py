from records import BoarderRecord

import storage


def _display_name(name, display_name=None):
    return display_name if display_name is not None else name.title()


def record(name, bed="101", frequency=0, total_minutes=0, total_points=0, display_name=None):
    return BoarderRecord(
        name=name,
        display_name=_display_name(name, display_name),
        bed=bed,
        frequency=frequency,
        total_minutes=total_minutes,
        total_points=total_points,
    )


def month_labels(months):
    return [month.month for month in months]


def month_row(name, bed="101", frequency=0, total_minutes=0, total_points=0, display_name=None):
    """One mocked /api/month report row, matching the wire format."""
    return {
        "name": name,
        "display_name": _display_name(name, display_name),
        "bed": bed,
        "frequency": frequency,
        "total_minutes": total_minutes,
        "total_points": total_points,
    }


def seed_punishments(conn, boarders=None, month="2026-03", deadline="2026-04-10",
                     assigned_at="2026-04-01T09:00:00+00:00", include_report=True):
    """Assigns Punishments (optionally saving their Monthly Report first).

    Pass include_report=False where the caller already saved the month and
    must not touch boarder_history. Returns every stored punishment.
    """
    boarders = list(boarders) if boarders is not None else [record("ALICE", "101", 2, 5, 7)]
    if include_report:
        storage.save_month(conn, boarders, month)
    storage.assign_punishments(
        conn,
        month=month,
        boarders=boarders,
        deadline=deadline,
        assigned_at=assigned_at,
    )
    return storage.list_punishments(conn)
