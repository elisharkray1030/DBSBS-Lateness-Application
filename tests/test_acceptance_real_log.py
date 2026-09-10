"""End-to-end acceptance over the repository's real Monthly Log artifact.

Pins the recovery figures that were previously verified only by hand: the
607B Boarder whose 'SURNAME, Given' master-list spelling once dropped him
from every report must record exactly 12 lateness events, 142 minutes, and
154 points, and every unmatched name the diagnostics report must classify as
an expected non-Boarder. Any drift in name normalization fails here loudly
instead of silently emptying a boarder's month again.
"""

import sqlite3
from pathlib import Path

import pytest

import storage
from parser import SavedOutcome, ingest_log, is_expected_non_boarder, load_namelist

REPO_ROOT = Path(__file__).resolve().parents[1]
MASTER_LIST_PATH = REPO_ROOT / "namelist.csv"
MONTHLY_LOG_PATH = REPO_ROOT / "data" / "raw" / "Test Monthly Log (Month) .csv"
TARGET_BED = "607B"

# Both artifacts are real Boarder data, gitignored for privacy (see .gitignore:
# /namelist.csv, data/raw/). They exist on staff dev machines but never on CI,
# so skip there — same conditional-skip pattern as the Playwright browser seam.
pytestmark = pytest.mark.skipif(
    not MASTER_LIST_PATH.exists() or not MONTHLY_LOG_PATH.exists(),
    reason="needs private real-data artifacts (gitignored, absent on CI)",
)


@pytest.fixture(scope="module")
def real_outcome():
    """Streams the real log against the real master list through ingest_log.

    The Monthly Log artifact carries a space before '.csv'; keep it exact.
    """
    assert MASTER_LIST_PATH.exists(), f"missing master list: {MASTER_LIST_PATH}"
    assert MONTHLY_LOG_PATH.exists(), (
        f"missing Monthly Log artifact (mind the space before '.csv'): "
        f"{MONTHLY_LOG_PATH}"
    )

    master_list = load_namelist(str(MASTER_LIST_PATH))
    assert master_list, "the real master list parsed to no boarders"

    connection = sqlite3.connect(":memory:")
    try:
        storage.create_schema(connection)
        with open(MONTHLY_LOG_PATH, mode="r", encoding="utf-8-sig") as log_stream:
            outcome = ingest_log(log_stream, "2026-05", master_list, connection)
    finally:
        connection.close()

    assert isinstance(outcome, SavedOutcome), outcome.reason
    return outcome


class TestRealMonthlyLogAcceptance:
    def test_607b_boarder_records_the_recovered_figures(self, real_outcome):
        record = next(
            (r for r in real_outcome.boarders if r.bed == TARGET_BED), None
        )
        assert record is not None, (
            f"no boarder on bed {TARGET_BED} was recovered from the real log; "
            "name normalization may have dropped them again"
        )

        assert record.frequency == 12, (
            f"{TARGET_BED} frequency drifted from the pinned figure: "
            f"expected 12 lateness events, got {record.frequency}"
        )
        assert record.total_minutes == 142, (
            f"{TARGET_BED} minutes drifted from the pinned figure: "
            f"expected 142 total minutes, got {record.total_minutes}"
        )
        assert record.total_points == 154, (
            f"{TARGET_BED} points drifted from the pinned figure: "
            f"expected 154 total points, got {record.total_points}"
        )

    def test_every_unmatched_name_is_an_expected_non_boarder(self, real_outcome):
        diagnostics = real_outcome.diagnostics
        unexpected = [
            name
            for name in diagnostics.unmatched_names
            if not is_expected_non_boarder(name)
        ]

        assert unexpected == [], (
            f"{len(unexpected)} unmatched names are not expected non-Boarders: "
            f"{unexpected}. A Boarder may be silently unmatched again "
            f"(unmatched boundary moved; {diagnostics.matched_rows} rows matched)."
        )
