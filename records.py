from dataclasses import dataclass


def normalize_name(name: str) -> str:
    """Normalizes a boarder name to the uppercased match key."""
    return name.strip().upper()


def boarder_sort_key(boarder: "Boarder") -> tuple[str, str]:
    """Orders boarders by bed then display name, matching the SQL ordering."""
    return (boarder.bed, boarder.display_name)


@dataclass
class Boarder:
    """One row of the master list: id, normalized name, display name, and bed."""

    normalized_name: str
    display_name: str
    bed: str
    id: int = 0


@dataclass
class BoarderRecord:
    """One boarder's month summary, keyed by normalized name."""

    name: str
    bed: str
    frequency: int
    total_minutes: int
    total_points: int

    @property
    def display_name(self) -> str:
        return self.name.title()


@dataclass
class UnparsedTimeRow:
    """A log row whose transaction time could not be parsed."""

    name: str
    raw_value: str


@dataclass
class HistoryEntry:
    """One stored row returned from a history search."""

    display_name: str
    bed: str
    month: str
    frequency: int
    total_minutes: int
    total_points: int


@dataclass
class MonthSummary:
    """One month's dashboard card: how many boarders and total minutes late."""

    month: str
    boarder_count: int
    total_minutes: int


@dataclass
class Punishment:
    """One disciplinary task assigned to a boarder for a month."""

    id: int
    normalized_name: str
    display_name: str
    bed: str
    month: str
    points_owed: int
    deadline: str
    status: str
    assigned_at: str
    overdue_at: str | None = None
    phone_held_at: str | None = None
    submitted_at: str | None = None
    voided_at: str | None = None
    void_reason: str | None = None
    is_due: bool = False
    was_late: bool = False
