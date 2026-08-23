from dataclasses import dataclass
from datetime import date, datetime, timezone

from records import BoarderRecord, Punishment

import storage

STATUSES = ("assigned", "overdue", "phone_held", "submitted", "voided")
IN_FLIGHT_STATUSES = ("assigned", "overdue", "phone_held")
NON_VOIDED_STATUSES = ("assigned", "overdue", "phone_held", "submitted")

# One shared humanized-label map driving status wording everywhere staff
# see it: filter options, group headings, and table cells.
STATUS_LABELS = {
    "assigned": "Assigned",
    "overdue": "Overdue",
    "phone_held": "Phone held",
    "submitted": "Submitted",
    "voided": "Voided",
}

_TRANSITION_STAMPS = (
    "assigned_at",
    "overdue_at",
    "phone_held_at",
    "submitted_at",
    "voided_at",
)


def humanized_status(status: str) -> str:
    """Returns the staff-facing label for a punishment status code."""
    return STATUS_LABELS.get(status, status)


def last_action_at(punishment) -> str | None:
    """Returns the most recent transition timestamp, or None."""
    stamps = [
        getattr(punishment, field)
        for field in _TRANSITION_STAMPS
        if getattr(punishment, field)
    ]
    if not stamps:
        return None
    return max(stamps, key=_stamp_moment)


def _stamp_moment(stamp: str) -> datetime:
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def format_timestamp(stamp: str) -> str:
    """Renders a stored timestamp as 'YYYY-MM-DD HH:MM' for table display."""
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    return moment.strftime("%Y-%m-%d %H:%M")


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


@dataclass(frozen=True)
class OfferedAction:
    """One transition button the Punishments view offers on a row.

    ``reason_input`` marks the void variant: its form gains the optional
    reason field and the data attributes feeding the confirm dialog.
    """

    target: str
    label: str
    style: str = "primary"
    reason_input: bool = False


# Which actions each status offers on the Punishments view, defined once
# beside the POST-time legality table so UI offers cannot drift from what
# the server accepts. Targets in _DUE_GATED_TARGETS appear only when the
# server flags the row due. VALID_TRANSITIONS remains the POST-time
# authority for what a submission may do (ADR 0001: manual-only machine).
_OFFERED_TRANSITIONS = {
    "assigned": (
        ("overdue", "Mark overdue"),
        ("submitted", "Submitted"),
    ),
    "overdue": (
        ("phone_held", "Phone held"),
        ("submitted", "Submitted"),
    ),
    "phone_held": (("submitted", "Submitted (release phone)"),),
    "submitted": (),
}

_DUE_GATED_TARGETS = frozenset({"overdue"})

_VOID_ACTION = OfferedAction(
    target="voided",
    label="Void",
    style="neutral",
    reason_input=True,
)


def offered_actions(punishment: Punishment) -> list[OfferedAction]:
    """Returns the ready-to-render action list for one Punishments row."""
    actions = [
        OfferedAction(target=target, label=label)
        for target, label in _OFFERED_TRANSITIONS.get(punishment.status, ())
        if target not in _DUE_GATED_TARGETS or punishment.is_due
    ]
    if punishment.status in NON_VOIDED_STATUSES:
        actions.append(_VOID_ACTION)
    return actions


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
        names=[boarder.display_name for boarder in eligible],
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


def attach_display_flags(punishments: list[Punishment], now: datetime | None = None) -> list[Punishment]:
    """Attaches computed ``is_due``/``was_late`` flags, last action, and the
    offered action list to each punishment, for any surface listing them."""
    if now is None:
        now = datetime.now(tz=timezone.utc)
    for punishment in punishments:
        punishment.is_due = _is_due(punishment, now)
        punishment.was_late = _was_late(punishment)
        last_action = last_action_at(punishment)
        punishment.last_action = (
            format_timestamp(last_action) if last_action else None
        )
        punishment.actions = offered_actions(punishment)
    return punishments


def list_consequences(
    conn,
    show_all: bool = False,
    month: str | None = None,
    status: str | None = None,
    now: datetime | None = None,
):
    """Returns punishments for the Punishments view.

    Defaults to in-flight statuses; ``show_all`` lifts that. ``status`` further
    narrows to one status, ``month`` to one month.
    """
    statuses: tuple[str, ...] | None
    if status:
        statuses = (status,)
    elif show_all:
        statuses = None
    else:
        statuses = IN_FLIGHT_STATUSES
    punishments = storage.list_punishments(conn, statuses=statuses, month=month)
    attach_display_flags(punishments, now=now)
    return sorted(
        punishments,
        key=lambda p: (_status_rank(p.status), p.deadline, p.normalized_name),
    )
