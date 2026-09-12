"""I-Point ledger lifecycle: logging Entries, derived Balance, and audit.

Stands beside ``punishments.py`` as the second disciplinary lifecycle. The
route layer stays a thin adapter: this module validates a submission, writes
the ledger and its audit row in one transaction, and derives each boarder's
Balance from the ledger rather than storing it.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone

from records import (
    IPointAudit,
    IPointAuditNote,
    IPointEntry,
    IPointSummary,
    bed_sort_key,
)

import storage

_OVERDRAW = "That change would take the Boarder's I-Point Balance below zero."


def points_phrase(points: int) -> str:
    """Words a point count with its singular/plural I-Point noun."""
    noun = "I-Point" if points == 1 else "I-Points"
    return f"{points} {noun}"


@dataclass
class EntrySaved:
    """An I-Point Entry was logged."""

    normalized_name: str
    display_name: str
    points: int
    occurred_on: str

    @property
    def message(self) -> str:
        return (
            f"Logged {points_phrase(self.points)} for {self.display_name} "
            f"on {self.occurred_on}."
        )


@dataclass
class EntryRejected:
    """The submitted I-Point Entry is not valid."""

    reason: str


@dataclass
class EntryEdited:
    """An I-Point Entry's points, date, or reason was corrected."""

    normalized_name: str
    display_name: str
    points: int
    occurred_on: str

    @property
    def message(self) -> str:
        return (
            f"Updated {self.display_name}: {points_phrase(self.points)} "
            f"on {self.occurred_on}."
        )


@dataclass
class EntryRemoved:
    """An I-Point Entry was removed; its prior state stays in the audit."""

    normalized_name: str
    display_name: str
    points: int
    occurred_on: str

    @property
    def message(self) -> str:
        return (
            f"Removed {points_phrase(self.points)} for {self.display_name} "
            f"({self.occurred_on})."
        )


def today_iso() -> str:
    """Returns today's local calendar date as an ISO string."""
    return datetime.now().astimezone().date().isoformat()


def resolve_display_name(conn, normalized_name: str) -> str:
    """Resolves a Match Key to its freshest display name, or the key itself."""
    identity = storage.resolve_boarder_identity(conn, normalized_name)
    return identity.display_name if identity is not None else normalized_name


def _coerce_points(points) -> "int | None":
    """Returns an integer candidate, or None for a non-whole-number value.

    Only integers and decimal-digit strings qualify, so a fractional value can
    never be silently truncated into an Entry. ``str.isdecimal`` (not
    ``str.isdigit``) is required because digit-class characters such as a
    superscript ``²`` pass ``isdigit`` but have no ``int`` value.
    """
    if isinstance(points, bool):
        return None
    if isinstance(points, int):
        return points
    if isinstance(points, str):
        text = points.strip()
        if not text.isdecimal():
            return None
        return int(text)
    return None


def _parse_occurred_on(occurred_on: str | None) -> "str | None":
    """Returns the normalized ISO date, or None when blank or unparseable."""
    raw = (occurred_on or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def _resolve_occurred_on(occurred_on: str | None, today: str | None) -> "str | None":
    raw = (occurred_on or "").strip()
    if not raw:
        return today or today_iso()
    return _parse_occurred_on(raw)


def _entry_fields(points: int, occurred_on: str, reason: str) -> dict[str, object]:
    """The auditable snapshot of an Entry's user-editable fields."""
    return {"points": points, "occurred_on": occurred_on, "reason": reason}


def _entry_state(entry: IPointEntry) -> dict[str, object]:
    """The auditable snapshot of one stored Entry's editable fields."""
    return _entry_fields(entry.points, entry.occurred_on, entry.reason)


def _balance_for(conn, normalized_name: str) -> int:
    """Derives one boarder's current I-Point Balance from the ledger."""
    return sum(
        entry.points for entry in storage.list_ipoint_entries(conn, normalized_name)
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
        storage.stage_ipoint_audit(
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


def edit_entry(
    conn,
    entry_id: int,
    points,
    occurred_on: str | None,
    reason: str,
    recorded_at: str | None = None,
) -> "EntryEdited | EntryRejected":
    """Validates and applies an Entry correction, auditing the prior state.

    Points must remain a positive whole number, the reason present, and the
    date valid. A blank date is rejected rather than defaulted, since the form
    always carries the entry's current date. A change that would drive the
    Balance below zero is refused (ADR 0006), writing nothing.
    """
    entry = storage.get_ipoint_entry(conn, entry_id)
    if entry is None:
        return EntryRejected(reason="That I-Point Entry no longer exists.")

    points_value = _coerce_points(points)
    if points_value is None or points_value <= 0:
        return EntryRejected(reason="Entry points must be a positive whole number.")

    clean_reason = (reason or "").strip()
    if not clean_reason:
        return EntryRejected(reason="A reason is required.")

    resolved_date = _parse_occurred_on(occurred_on)
    if resolved_date is None:
        return EntryRejected(reason="Enter a valid date.")

    if _balance_for(conn, entry.normalized_name) - entry.points + points_value < 0:
        return EntryRejected(reason=_OVERDRAW)

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, entry.normalized_name)

    with conn:
        storage.stage_update_ipoint_entry(
            conn, entry_id, points_value, resolved_date, clean_reason
        )
        storage.stage_ipoint_audit(
            conn,
            entity_type="entry",
            entity_id=entry_id,
            normalized_name=entry.normalized_name,
            action="edited",
            before_state=json.dumps(_entry_state(entry), sort_keys=True),
            after_state=json.dumps(
                _entry_fields(points_value, resolved_date, clean_reason),
                sort_keys=True,
            ),
            changed_at=stamp,
        )

    return EntryEdited(
        normalized_name=entry.normalized_name,
        display_name=display_name,
        points=points_value,
        occurred_on=resolved_date,
    )


def remove_entry(
    conn,
    entry_id: int,
    recorded_at: str | None = None,
) -> "EntryRemoved | EntryRejected":
    """Removes an Entry from the live ledger, auditing the prior state.

    The row leaves the Balance but its snapshot survives in the I-Point Audit
    History. A removal that would drive the Balance below zero is refused
    (ADR 0006), writing nothing.
    """
    entry = storage.get_ipoint_entry(conn, entry_id)
    if entry is None:
        return EntryRejected(reason="That I-Point Entry no longer exists.")

    if _balance_for(conn, entry.normalized_name) - entry.points < 0:
        return EntryRejected(reason=_OVERDRAW)

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, entry.normalized_name)

    with conn:
        storage.stage_delete_ipoint_entry(conn, entry_id)
        storage.stage_ipoint_audit(
            conn,
            entity_type="entry",
            entity_id=entry_id,
            normalized_name=entry.normalized_name,
            action="removed",
            before_state=json.dumps(_entry_state(entry), sort_keys=True),
            after_state=None,
            changed_at=stamp,
        )

    return EntryRemoved(
        normalized_name=entry.normalized_name,
        display_name=display_name,
        points=entry.points,
        occurred_on=entry.occurred_on,
    )


def _describe_change(action: str, before, after) -> str:
    """Words one audit row's prior and new state for the history table."""
    if action == "created" and after:
        return f"Created {after['points']} on {after['occurred_on']}: {after['reason']}"
    if action == "removed" and before:
        return f"Removed {before['points']} on {before['occurred_on']}: {before['reason']}"
    if action == "edited" and before and after:
        return (
            f"{before['points']} -> {after['points']} points; "
            f"{before['occurred_on']} -> {after['occurred_on']}; "
            f"{before['reason']} -> {after['reason']}"
        )
    return action


def _audit_note(audit: IPointAudit) -> IPointAuditNote:
    """Renders one stored Audit row into a display line."""
    before = json.loads(audit.before_state) if audit.before_state else None
    after = json.loads(audit.after_state) if audit.after_state else None
    return IPointAuditNote(
        action=audit.action.capitalize(),
        changed_at=audit.changed_at,
        description=_describe_change(audit.action, before, after),
    )


def boarder_balances(conn) -> list[IPointSummary]:
    """Derives each boarder's I-Point Balance from the stored ledger.

    Balance is the sum of that Match Key's Entries — never stored, so it
    cannot drift. Identity fields resolve freshest-first through the
    All-Time List, falling back to the Match Key for a boarder known only
    through I-Points. Summaries sort by the shared Bed rule then name.
    """
    entries = storage.list_ipoint_entries(conn)
    audits = storage.list_ipoint_audit(conn)
    identity = storage.freshest_identity_map(conn)

    grouped: dict[str, list[IPointEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.normalized_name, []).append(entry)

    grouped_audits: dict[str, list[IPointAudit]] = {}
    for audit in audits:
        grouped_audits.setdefault(audit.normalized_name, []).append(audit)

    summaries: list[IPointSummary] = []
    for key in grouped.keys() | grouped_audits.keys():
        rows = grouped.get(key, [])
        who = identity.get(key)
        summaries.append(
            IPointSummary(
                normalized_name=key,
                display_name=who.display_name if who else key,
                bed=who.bed if who else "",
                balance=sum(row.points for row in rows),
                entries=sorted(rows, key=lambda row: (row.occurred_on, row.id)),
                audits=[_audit_note(audit) for audit in grouped_audits.get(key, [])],
            )
        )
    summaries.sort(key=lambda summary: (bed_sort_key(summary.bed), summary.display_name))
    return summaries
