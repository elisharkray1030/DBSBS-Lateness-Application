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
    IPointAdjustment,
    IPointAudit,
    IPointAuditDraft,
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
class ChangeRejected:
    """An I-Point ledger write was refused; ``reason`` is user-facing."""

    reason: str


@dataclass
class EntryRejected(ChangeRejected):
    """The submitted I-Point Entry is not valid."""


@dataclass
class AdjustmentSaved:
    """An I-Point Adjustment was added."""

    normalized_name: str
    display_name: str
    points: int
    reason: str

    @property
    def message(self) -> str:
        return (
            f"Added an adjustment of {points_phrase(self.points)} "
            f"for {self.display_name}."
        )


@dataclass
class AdjustmentEdited:
    """An I-Point Adjustment's points or reason was corrected."""

    normalized_name: str
    display_name: str
    points: int
    reason: str

    @property
    def message(self) -> str:
        return (
            f"Updated {self.display_name}'s adjustment to "
            f"{points_phrase(self.points)}."
        )


@dataclass
class AdjustmentRemoved:
    """An I-Point Adjustment was removed; its prior state stays in the audit."""

    normalized_name: str
    display_name: str
    points: int
    reason: str

    @property
    def message(self) -> str:
        return (
            f"Removed {self.display_name}'s adjustment of "
            f"{points_phrase(self.points)}."
        )


@dataclass
class AdjustmentRejected(ChangeRejected):
    """The submitted I-Point Adjustment is not valid."""


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


def _coerce_signed_points(points) -> "int | None":
    """Returns a signed integer candidate, or None for a non-whole-number value.

    Like ``_coerce_points`` but admitting a leading sign, since an Adjustment
    may subtract. ``str.isdecimal`` still gates the digits, so a fractional or
    digit-class value (``5.9``, ``²``) can never slip through.
    """
    if isinstance(points, bool):
        return None
    if isinstance(points, int):
        return points
    if isinstance(points, str):
        text = points.strip()
        body = text[1:] if text[:1] in "+-" else text
        if not body.isdecimal():
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


def _adjustment_fields(points: int, reason: str) -> dict[str, object]:
    """The auditable snapshot of an Adjustment's user-editable fields."""
    return {"points": points, "reason": reason}


def _adjustment_state(adjustment: IPointAdjustment) -> dict[str, object]:
    """The auditable snapshot of one stored Adjustment's editable fields."""
    return _adjustment_fields(adjustment.points, adjustment.reason)


def _dump_state(state: dict[str, object] | None) -> str | None:
    """Encodes one auditable snapshot as sort-keyed JSON, or None when absent."""
    return json.dumps(state, sort_keys=True) if state is not None else None


def _audit_draft(
    entity_type: str,
    entity_id: int,
    normalized_name: str,
    action: str,
    before_state: dict[str, object] | None,
    after_state: dict[str, object] | None,
    changed_at: str,
) -> IPointAuditDraft:
    """Builds one Audit draft, encoding both snapshots as JSON."""
    return IPointAuditDraft(
        entity_type=entity_type,
        entity_id=entity_id,
        normalized_name=normalized_name,
        action=action,
        before_state=_dump_state(before_state),
        after_state=_dump_state(after_state),
        changed_at=changed_at,
    )


def _balance_for(conn, normalized_name: str) -> int:
    """Derives one boarder's current I-Point Balance from the ledger."""
    entries = sum(
        entry.points for entry in storage.list_ipoint_entries(conn, normalized_name)
    )
    adjustments = sum(
        adjustment.points
        for adjustment in storage.list_ipoint_adjustments(conn, normalized_name)
    )
    return entries + adjustments


def _validate_adjustment(
    normalized_name: str, points, reason: str
) -> tuple[str, int, str] | AdjustmentRejected:
    """Validates the shared Adjustment fields: name, non-zero points, reason."""
    name = (normalized_name or "").strip()
    if not name:
        return AdjustmentRejected(reason="A boarder name is required.")

    points_value = _coerce_signed_points(points)
    if points_value is None or points_value == 0:
        return AdjustmentRejected(
            reason="Adjustment points must be a non-zero whole number."
        )

    clean_reason = (reason or "").strip()
    if not clean_reason:
        return AdjustmentRejected(reason="A reason is required.")

    return name, points_value, clean_reason


def add_adjustment(
    conn,
    normalized_name: str,
    points,
    reason: str,
    recorded_at: str | None = None,
) -> AdjustmentSaved | AdjustmentRejected:
    """Validates and adds one signed I-Point Adjustment, auditing it atomically.

    A positive Adjustment adds to the Balance; a subtraction may never take the
    Balance below zero (ADR 0006), so it is capped at the current Balance. A
    rejected submission writes nothing.
    """
    validated = _validate_adjustment(normalized_name, points, reason)
    if isinstance(validated, AdjustmentRejected):
        return validated
    name, points_value, clean_reason = validated

    if _balance_for(conn, name) + points_value < 0:
        return AdjustmentRejected(reason=_OVERDRAW)

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, name)

    with conn:
        adjustment_id = storage.stage_ipoint_adjustment(
            conn, name, points_value, clean_reason, stamp
        )
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "adjustment",
                adjustment_id,
                name,
                "created",
                None,
                _adjustment_fields(points_value, clean_reason),
                stamp,
            ),
        )

    return AdjustmentSaved(
        normalized_name=name,
        display_name=display_name,
        points=points_value,
        reason=clean_reason,
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
            _audit_draft(
                "entry",
                entry_id,
                name,
                "created",
                None,
                _entry_fields(points_value, resolved_date, clean_reason),
                stamp,
            ),
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
            _audit_draft(
                "entry",
                entry_id,
                entry.normalized_name,
                "edited",
                _entry_state(entry),
                _entry_fields(points_value, resolved_date, clean_reason),
                stamp,
            ),
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
            _audit_draft(
                "entry",
                entry_id,
                entry.normalized_name,
                "removed",
                _entry_state(entry),
                None,
                stamp,
            ),
        )

    return EntryRemoved(
        normalized_name=entry.normalized_name,
        display_name=display_name,
        points=entry.points,
        occurred_on=entry.occurred_on,
    )


def edit_adjustment(
    conn,
    adjustment_id: int,
    points,
    reason: str,
    recorded_at: str | None = None,
) -> "AdjustmentEdited | AdjustmentRejected":
    """Validates and applies an Adjustment correction, auditing the prior state.

    The new signed value must be non-zero and its reason present. A change that
    would drive the Balance below zero is refused (ADR 0006), writing nothing.
    """
    adjustment = storage.get_ipoint_adjustment(conn, adjustment_id)
    if adjustment is None:
        return AdjustmentRejected(reason="That I-Point Adjustment no longer exists.")

    validated = _validate_adjustment(adjustment.normalized_name, points, reason)
    if isinstance(validated, AdjustmentRejected):
        return validated
    _, points_value, clean_reason = validated

    projected = _balance_for(conn, adjustment.normalized_name) - adjustment.points
    if projected + points_value < 0:
        return AdjustmentRejected(reason=_OVERDRAW)

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, adjustment.normalized_name)

    with conn:
        storage.stage_update_ipoint_adjustment(
            conn, adjustment_id, points_value, clean_reason
        )
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "adjustment",
                adjustment_id,
                adjustment.normalized_name,
                "edited",
                _adjustment_state(adjustment),
                _adjustment_fields(points_value, clean_reason),
                stamp,
            ),
        )

    return AdjustmentEdited(
        normalized_name=adjustment.normalized_name,
        display_name=display_name,
        points=points_value,
        reason=clean_reason,
    )


def remove_adjustment(
    conn,
    adjustment_id: int,
    recorded_at: str | None = None,
) -> "AdjustmentRemoved | AdjustmentRejected":
    """Removes an Adjustment from the ledger, auditing the prior state.

    A removal that would drive the Balance below zero is refused (ADR 0006),
    writing nothing.
    """
    adjustment = storage.get_ipoint_adjustment(conn, adjustment_id)
    if adjustment is None:
        return AdjustmentRejected(reason="That I-Point Adjustment no longer exists.")

    projected = _balance_for(conn, adjustment.normalized_name) - adjustment.points
    if projected < 0:
        return AdjustmentRejected(reason=_OVERDRAW)

    stamp = recorded_at or datetime.now(tz=timezone.utc).isoformat()
    display_name = resolve_display_name(conn, adjustment.normalized_name)

    with conn:
        storage.stage_delete_ipoint_adjustment(conn, adjustment_id)
        storage.stage_ipoint_audit(
            conn,
            _audit_draft(
                "adjustment",
                adjustment_id,
                adjustment.normalized_name,
                "removed",
                _adjustment_state(adjustment),
                None,
                stamp,
            ),
        )

    return AdjustmentRemoved(
        normalized_name=adjustment.normalized_name,
        display_name=display_name,
        points=adjustment.points,
        reason=adjustment.reason,
    )


def _describe_change(action: str, before, after, entity_type: str) -> str:
    """Words one audit row's prior and new state for the history table."""
    if entity_type == "adjustment":
        if action == "created" and after:
            return f"Created adjustment of {after['points']}: {after['reason']}"
        if action == "removed" and before:
            return f"Removed adjustment of {before['points']}: {before['reason']}"
        if action == "edited" and before and after:
            return (
                f"{before['points']} -> {after['points']} points; "
                f"{before['reason']} -> {after['reason']}"
            )
        return action
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
        description=_describe_change(audit.action, before, after, audit.entity_type),
    )


def boarder_balances(conn) -> list[IPointSummary]:
    """Derives each boarder's I-Point Balance from the stored ledger.

    Balance is the sum of that Match Key's Entries plus Adjustments — never
    stored, so it cannot drift. Identity fields resolve freshest-first through
    the All-Time List, falling back to the Match Key for a boarder known only
    through I-Points. Summaries sort by the shared Bed rule then name.
    """
    entries = storage.list_ipoint_entries(conn)
    adjustments = storage.list_ipoint_adjustments(conn)
    audits = storage.list_ipoint_audit(conn)
    identity = storage.freshest_identity_map(conn)

    grouped: dict[str, list[IPointEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.normalized_name, []).append(entry)

    grouped_adjustments: dict[str, list[IPointAdjustment]] = {}
    for adjustment in adjustments:
        grouped_adjustments.setdefault(adjustment.normalized_name, []).append(adjustment)

    grouped_audits: dict[str, list[IPointAudit]] = {}
    for audit in audits:
        grouped_audits.setdefault(audit.normalized_name, []).append(audit)

    summaries: list[IPointSummary] = []
    for key in grouped.keys() | grouped_adjustments.keys() | grouped_audits.keys():
        rows = grouped.get(key, [])
        adjustment_rows = grouped_adjustments.get(key, [])
        who = identity.get(key)
        summaries.append(
            IPointSummary(
                normalized_name=key,
                display_name=who.display_name if who else key,
                bed=who.bed if who else "",
                balance=sum(row.points for row in rows)
                + sum(row.points for row in adjustment_rows),
                entries=sorted(rows, key=lambda row: (row.occurred_on, row.id)),
                adjustments=adjustment_rows,
                audits=[_audit_note(audit) for audit in grouped_audits.get(key, [])],
            )
        )
    summaries.sort(key=lambda summary: (bed_sort_key(summary.bed), summary.display_name))
    return summaries
