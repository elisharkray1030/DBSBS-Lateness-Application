import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from punishments import OfferedAction


_NAME_KEY_NOISE = re.compile(r"(?:[^\w]|_)+")


def normalize_name(name: str) -> str:
    """Normalizes a boarder name to its punctuation-insensitive match key.

    Uppercases and collapses every run of punctuation and whitespace (commas,
    periods, apostrophes, newlines...) to a single space, so master-list
    entries like 'SURNAME, Given' match log rows like 'SURNAME Given'.
    """
    return _NAME_KEY_NOISE.sub(" ", name.strip().upper()).strip()


def boarder_sort_key(boarder: "Boarder | AllTimeEntry") -> tuple[tuple[int, int, str], str]:
    """Orders boarder-like rows by the shared Bed ordering rule, then display name."""
    return (bed_sort_key(boarder.bed), boarder.display_name)


_BED_NUMERIC_PATTERN = re.compile(r"^(\d+)(.*)$")


def bed_sort_key(bed: str) -> tuple[int, int, str]:
    """Orders beds by leading number then suffix, with a lexical fallback.

    The single server-side ordering rule for Bed values: '9A' sorts before
    '10', and '601A' before '601B' before '602A'. Beds that do not start with
    a number sort lexically after every numbered bed.
    """
    match = _BED_NUMERIC_PATTERN.match(bed)
    if match:
        return (0, int(match.group(1)), match.group(2))
    return (1, 0, bed)


def sort_boarder_records(records: "Iterable[BoarderRecord]") -> "list[BoarderRecord]":
    """Orders Monthly Report rows by the shared Bed rule, then normalized name."""
    return sorted(records, key=lambda r: (bed_sort_key(r.bed), r.name))


@dataclass
class Boarder:
    """One row of the master list: id, normalized name, display name, and bed."""

    normalized_name: str
    display_name: str
    bed: str
    id: int = 0


@dataclass
class BoarderRecord:
    """One boarder's month summary: normalized identity, canonical display
    name, bed, and the computed lateness values."""

    name: str
    display_name: str
    bed: str
    frequency: int
    total_minutes: int
    total_points: int


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
class AllTimeEntry:
    """One derived All-Time List row: every boarder ever recorded.

    Derived live from the Master List unioned with the Match Keys found in
    Boarder History and Punishments — never stored. ``is_current`` is
    likewise derived (True when the key sits on the Master List), and the
    seen-month/lifetime figures sum this key's history rows only.
    """

    normalized_name: str
    display_name: str
    bed: str
    is_current: bool
    first_month: str | None
    last_month: str | None
    total_frequency: int
    total_minutes: int
    total_points: int


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
    last_action: str | None = None
    actions: list["OfferedAction"] = field(default_factory=list)
