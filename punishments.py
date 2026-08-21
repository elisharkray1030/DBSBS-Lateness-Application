from dataclasses import dataclass
from datetime import date, datetime, timezone

from records import BoarderRecord

import storage

STATUSES = ("assigned", "overdue", "phone_held", "submitted", "voided")
IN_FLIGHT_STATUSES = ("assigned", "overdue", "phone_held")
NON_VOIDED_STATUSES = ("assigned", "overdue", "phone_held", "submitted")


@dataclass
class AssignmentSaved:
    """A batch of punishments was assigned."""

    count: int
    names: list[str]
    month: str
    deadline: str

    @property
    def message(self) -> str:
        boarder_word = "punishment" if self.count == 1 else "punishments"
        names = ", ".join(self.names)
        return (
            f"Assigned {self.count} {boarder_word} for {self.month} "
            f"(deadline {self.deadline}): {names}."
        )


@dataclass
class AssignmentRejected:
    """No punishments could be assigned."""

    month: str
    reason: str


VALID_TRANSITIONS: dict[str, set[str]] = {
    "assigned": {"overdue", "submitted", "voided"},
    "overdue": {"submitted", "phone_held", "voided"},
    "phone_held": {"submitted", "voided"},
    "submitted": {"voided"},
    "voided": set(),
}


@dataclass
class TransitionSaved:
    """A punishment moved to a new status."""

    status: str
    normalized_name: str
    month: str

    @property
    def message(self) -> str:
        display = self.status.replace("_", " ")
        return f"{self.normalized_name} ({self.month}) marked {display}."


@dataclass
class TransitionRejected:
    """The requested transition is not allowed."""

    current_status: str
    target: str
    normalized_name: str
    reason_message: str | None = None

    @property
    def reason(self) -> str:
        if self.reason_message is not None:
            return self.reason_message
        return (
            f"Cannot move {self.normalized_name} from '{self.current_status}' "
            f"to '{self.target}'. That transition is not allowed."
        )


def transition(
    conn,
    punishment_id: int,
    target: str,
    timestamp: str | None = None,
    void_reason: str | None = None,
) -> TransitionSaved | TransitionRejected:
    """Moves a punishment to a new status if the transition is legal."""
    if timestamp is None:
        timestamp = datetime.now(tz=timezone.utc).isoformat()

    punishment = storage.get_punishment(conn, punishment_id)
    if punishment is None:
        return TransitionRejected(
            current_status="unknown", target=target, normalized_name="?"
        )

    if target not in VALID_TRANSITIONS.get(punishment.status, set()):
        return TransitionRejected(
            current_status=punishment.status,
            target=target,
            normalized_name=punishment.display_name,
        )

    if target == "overdue" and punishment.status == "assigned":
        transition_date = _timestamp_date(timestamp)
        deadline = date.fromisoformat(punishment.deadline)
        if transition_date < deadline:
            return TransitionRejected(
                current_status=punishment.status,
                target=target,
                normalized_name=punishment.display_name,
                reason_message=(
                    f"Cannot mark {punishment.display_name} overdue before its "
                    f"deadline of {punishment.deadline}."
                ),
            )

    storage.transition_punishment(
        conn,
        punishment_id,
        target,
        timestamp=timestamp,
        void_reason=void_reason,
    )
    return TransitionSaved(
        status=target,
        normalized_name=punishment.display_name,
        month=punishment.month,
    )


def assign_batch(
    conn,
    month: str,
    boarders: list[BoarderRecord],
    exemptions: set[str],
    deadline: str,
    assigned_at: str | None = None,
) -> AssignmentSaved | AssignmentRejected:
    """Assigns one punishment per boarder with points, minus exemptions.

    Boarders who already have an active punishment for the month are left
    untouched, so re-importing a corrected log never re-issues a punishment.
    """
    if assigned_at is None:
        assigned_at = datetime.now(tz=timezone.utc).isoformat()

    already_assigned = {
        row.normalized_name
        for row in storage.list_punishments(conn, statuses=NON_VOIDED_STATUSES, month=month)
    }

    eligible = [
        boarder
        for boarder in boarders
        if boarder.total_points > 0
        and boarder.name not in exemptions
        and boarder.name not in already_assigned
    ]

    if not eligible:
        return AssignmentRejected(
            month=month,
            reason=(
                f"No boarders with points to assign for {month}. Either every "
                "boarder was exempted, already assigned, or has no points."
            ),
        )

    storage.assign_punishments(conn, month, eligible, deadline, assigned_at)
    return AssignmentSaved(
        count=len(eligible),
        names=[boarder.name for boarder in eligible],
        month=month,
        deadline=deadline,
    )


def _is_due(punishment, now: datetime) -> bool:
    if punishment.status != "assigned":
        return False
    deadline = date.fromisoformat(punishment.deadline)
    return now.date() >= deadline


def _was_late(punishment) -> bool:
    if not punishment.submitted_at:
        return False
    submitted_at = _timestamp_date(punishment.submitted_at)
    deadline = date.fromisoformat(punishment.deadline)
    return submitted_at > deadline


def _timestamp_date(timestamp: str) -> date:
    """Returns the calendar date represented by an ISO timestamp or date.

    A submission on the Deadline date is not late because comparison uses
    calendar dates rather than the stored timestamp string.
    """
    try:
        return datetime.fromisoformat(timestamp).date()
    except ValueError:
        return date.fromisoformat(timestamp)


def _status_rank(status: str) -> int:
    return IN_FLIGHT_STATUSES.index(status) if status in IN_FLIGHT_STATUSES else 99


def list_consequences(
    conn,
    show_all: bool = False,
    month: str | None = None,
    status: str | None = None,
    now: datetime | None = None,
):
    """Returns punishments for the Consequences view.

    Defaults to in-flight statuses; ``show_all`` lifts that. ``status`` further
    narrows to one status, ``month`` to one month. Attaches computed ``is_due``
    and ``was_late`` flags for each punishment.
    """
    statuses: tuple[str, ...] | None
    if status:
        statuses = (status,)
    elif show_all:
        statuses = None
    else:
        statuses = IN_FLIGHT_STATUSES
    punishments = storage.list_punishments(conn, statuses=statuses, month=month)
    if now is None:
        now = datetime.now(tz=timezone.utc)
    for punishment in punishments:
        punishment.is_due = _is_due(punishment, now)
        punishment.was_late = _was_late(punishment)
    return sorted(
        punishments,
        key=lambda p: (_status_rank(p.status), p.deadline, p.normalized_name),
    )
