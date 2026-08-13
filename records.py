from dataclasses import dataclass


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
