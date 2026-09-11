"""I-Point ledger lifecycle: logging Entries, derived Balance, and audit.

Stands beside ``punishments.py`` as the second disciplinary lifecycle. The
route layer stays a thin adapter: this module validates a submission, writes
the ledger and its audit row in one transaction, and derives each boarder's
Balance from the ledger rather than storing it.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone

from records import IPointEntry, IPointSummary, bed_sort_key

import storage


@dataclass
class EntrySaved:
    """An I-Point Entry was logged."""

    normalized_name: str
    display_name: str
    points: int
    occurred_on: str

    @property
    def message(self) -> str:
        noun = "I-Point" if self.points == 1 else "I-Points"
        return (
            f"Logged {self.points} {noun} for {self.display_name} "
            f"on {self.occurred_on}."
        )


@dataclass
class EntryRejected:
    """The submitted I-Point Entry is not valid."""

    reason: str


def today_iso() -> str:
    """Returns today's local calendar date as an ISO string."""
    return datetime.now().astimezone().date().isoformat()


def resolve_display_name(conn, normalized_name: str) -> str:
    """Resolves a Match Key to its freshest display name, or the key itself."""
    identity = storage.resolve_boarder_identity(conn, normalized_name)
    return identity.display_name if identity is not None else normalized_name


def _coerce_points(points) -> "int | None":
    """Returns an integer candidate, or None for a non-whole-number value.

    Only integers and digit-only strings qualify, so a fractional value can
    never be silently truncated into an Entry.
    """
    if isinstance(points, bool):
        return None
    if isinstance(points, int):
        return points
    if isinstance(points, str):
        text = points.strip()
        if not text.isdigit():
            return None
        return int(text)
    return None


def _resolve_occurred_on(occurred_on: str | None, today: str | None) -> "str | None":
    raw = (occurred_on or "").strip()
    if not raw:
        return today or today_iso()
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def _write_audit(
    conn,
    *,
    entity_type: str,
    entity_id: int,
    normalized_name: str,
    action: str,
    before_state: str | None,
    after_state: str | None,
    changed_at: str,
) -> None:
    """Stages one audit row on the open transaction; the caller owns the block."""
    storage.stage_ipoint_audit(
        conn,
        entity_type=entity_type,
        entity_id=entity_id,
        normalized_name=normalized_name,
        action=action,
        before_state=before_state,
        after_state=after_state,
        changed_at=changed_at,
    )


def log_entry(
    conn,
    normalized_name: str,
    points,
    occurred_on: str | None,
    reason: str,
    recorded_at: str | None = None,
    today: str | None = None,
) -> EntrySaved | EntryRejected:
    """Validates and logs one I-Point Entry, writing its audit row atomically.

    Points must be a positive whole number and the reason must be present;
    a blank date falls back to the injected (or machine-local) today. A
    rejected submission writes nothing.
    """
    name = (normalized_name or "").strip()
    if not name:
        return EntryRejected(reason="A boarder name is required.")

    points_value = _coerce_points(points)
    if points_value is None or points_value <= 0:
        return EntryRejected(
            reason="Entry points must be a positive whole number."
        )

    clean_reason = (reason or "").strip()
    if not clean_reason:
        return EntryRejected(reason="A reason is required.")

    resolved_date = _resolve_occurred_on(occurred_on, today)
    if resolved_date is None:
        return EntryRejected(reason="Enter a valid date.")

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, name)

    with conn:
        entry_id = storage.stage_ipoint_entry(
            conn, name, points_value, resolved_date, clean_reason, stamp
        )
        _write_audit(
            conn,
            entity_type="entry",
            entity_id=entry_id,
            normalized_name=name,
            action="created",
            before_state=None,
            after_state=json.dumps(
                {
                    "points": points_value,
                    "occurred_on": resolved_date,
                    "reason": clean_reason,
                },
                sort_keys=True,
            ),
            changed_at=stamp,
        )

    return EntrySaved(
        normalized_name=name,
        display_name=display_name,
        points=points_value,
        occurred_on=resolved_date,
    )


def boarder_balances(conn) -> list[IPointSummary]:
    """Derives each boarder's I-Point Balance from the stored ledger.

    Balance is the sum of that Match Key's Entries — never stored, so it
    cannot drift. Identity fields resolve freshest-first through the
    All-Time List, falling back to the Match Key for a boarder known only
    through I-Points. Summaries sort by the shared Bed rule then name.
    """
    entries = storage.list_ipoint_entries(conn)
    identity = {
        entry.normalized_name: entry
        for entry in storage.list_all_time_boarders(conn)
    }
    grouped: dict[str, list[IPointEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.normalized_name, []).append(entry)

    summaries: list[IPointSummary] = []
    for key, rows in grouped.items():
        who = identity.get(key)
        summaries.append(
            IPointSummary(
                normalized_name=key,
                display_name=who.display_name if who else key,
                bed=who.bed if who else "",
                balance=sum(row.points for row in rows),
                entries=sorted(rows, key=lambda row: (row.occurred_on, row.id)),
            )
        )
    summaries.sort(key=lambda summary: (bed_sort_key(summary.bed), summary.display_name))
    return summaries
