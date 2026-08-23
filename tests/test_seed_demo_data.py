"""Coverage for the demo seed matrix (seed_demo_data.py).

The seeder runs against a throwaway database with a synthetic namelist
carrying the persona boarders, so these tests never depend on the local
real-world namelist.csv or lateness_history.db.
"""

import csv
import sqlite3

import pytest

import app as app_module
import parser as parser_module
import seed_demo_data
import storage


def repeat_offenders(conn):
    return storage.repeat_offenders(
        conn,
        threshold=app_module.WATCHLIST_POINTS_THRESHOLD,
        required_months=app_module.WATCHLIST_MIN_STREAK_MONTHS,
    )


NAMELIST_ROWS = [
    ("Bed", "Name"),
    ("601A", "Melvin YEUNG Cheng Ye Melvin"),
    ("601D", "Jason FONG Pak Hin"),
    ("602D", "Elvis WONG Yat Shun"),
    ("603A", "Navas YUEN Hiu Nok"),
    ("604A", "Klaus CHAN Klaus Fai Tai"),
    ("604B", "Dick CHAN Cheuk Wing"),
    ("605C", "Andy WU Yik Ham"),
    ("605E", "James WONG Wang Hei"),
    ("607B", "Lucas CHAVEZ MOCAN, Lucas"),
    ("701B", "Jasper CHAN Cheuk Yin"),
]


def write_namelist(path):
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerows(NAMELIST_ROWS)


@pytest.fixture()
def seeded(tmp_path):
    """Schema'd in-memory database seeded by the demo seeder."""
    namelist_path = tmp_path / "namelist.csv"
    write_namelist(namelist_path)
    conn = sqlite3.connect(":memory:")
    storage.create_schema(conn)
    # Mirror production init: the Master List lives in the database.
    storage.replace_boarders(conn, parser_module.load_namelist_rows(str(namelist_path)))
    # A fake leftover row that the clean-slate step must remove.
    storage.save_month(
        conn,
        [parser_module.BoarderRecord("ALICE", "Alice", "101", 2, 5, 7)],
        "2026-03",
    )
    report = seed_demo_data.seed(conn, str(namelist_path))
    yield conn, report
    conn.close()


def months_stored(conn):
    return sorted({row[0] for row in conn.execute("SELECT DISTINCT month FROM boarder_history")})


class TestSeededMonthsAndCleanup:
    def test_seeds_every_month_except_june(self, seeded):
        conn, _ = seeded

        assert months_stored(conn) == [
            "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-07", "2026-08",
        ]

    def test_clean_slate_removes_fake_rows(self, seeded):
        conn, _ = seeded

        leftovers = conn.execute(
            "SELECT COUNT(*) FROM boarder_history WHERE normalized_name = 'ALICE'"
        ).fetchone()[0]

        assert leftovers == 0


class TestWatchlistMatrix:
    def test_chronic_offender_streaks_january_to_may(self, seeded):
        conn, _ = seeded

        jason = next(
            o for o in repeat_offenders(conn) if o.normalized_name == "JASON FONG PAK HIN"
        )

        assert jason.months == [
            "2026-01", "2026-02", "2026-03", "2026-04", "2026-05",
        ]

    def test_recovering_offender_boundary_streak_feb_to_apr(self, seeded):
        conn, _ = seeded

        jasper = next(
            o for o in repeat_offenders(conn) if o.normalized_name == "JASPER CHAN CHEUK YIN"
        )

        assert jasper.months == ["2026-02", "2026-03", "2026-04"]
        may_row = next(
            row for row in storage.get_boarder_series(conn, "JASPER CHAN CHEUK YIN")
            if row.month == "2026-05"
        )
        assert may_row.total_points == 6

    def test_only_the_two_personas_qualify(self, seeded):
        conn, _ = seeded

        offenders = repeat_offenders(conn)

        assert {o.normalized_name for o in offenders} == {
            "JASON FONG PAK HIN",
            "JASPER CHAN CHEUK YIN",
        }


class TestSpreadMatrix:
    def test_tie_pair_has_identical_august_totals(self, seeded):
        conn, _ = seeded
        series = {
            key: {row.month: row for row in storage.get_boarder_series(conn, key)}
            for key in ("ANDY WU YIK HAM", "JAMES WONG WANG HEI")
        }

        august_andy = series["ANDY WU YIK HAM"]["2026-08"]
        august_james = series["JAMES WONG WANG HEI"]["2026-08"]

        assert (august_andy.frequency, august_andy.total_minutes, august_andy.total_points) == (
            august_james.frequency, august_james.total_minutes, august_james.total_points,
        )
        assert august_andy.total_points > 0

    def test_distribution_buckets_populate_in_january(self, seeded):
        conn, _ = seeded

        buckets = {b.label: b.count for b in storage.points_distribution(conn, "2026-01")}

        # Only the first two decade bins fill. One incident always adds at
        # least one minute, so the smallest possible Points figure is 2,
        # while the largest seeded persona tops out at 18 Points.
        assert buckets["≤10"] > 0
        assert buckets["11–20"] > 0
        assert buckets["21–30"] == 0
        assert buckets["31–40"] == 0
        assert buckets["41–50"] == 0
        assert buckets["51+"] == 0

    def test_figures_derive_from_real_ingestion_path(self, seeded):
        """Points equal frequency plus minutes because logs went through the parser."""
        conn, _ = seeded

        bad = conn.execute(
            """
            SELECT COUNT(*) FROM boarder_history
            WHERE frequency > 0 AND total_points != frequency + total_minutes
            """
        ).fetchone()[0]

        assert bad == 0


class TestRemovedBoarder:
    def test_removed_boarder_leaves_master_list_but_survives_as_former(self, seeded):
        conn, _ = seeded
        removed_key = "NAVAS YUEN HIU NOK"

        master_keys = {b.normalized_name for b in storage.list_boarders(conn)}
        entries = storage.list_all_time_boarders(conn)
        navas = next(e for e in entries if e.normalized_name == removed_key)

        assert removed_key not in master_keys
        assert navas.is_current is False
        assert navas.total_points == 3
        assert navas.first_month == "2026-01"
        assert navas.last_month == "2026-08"

    def test_removed_boarder_keeps_pre_removal_punishment(self, seeded):
        conn, _ = seeded

        punishments = list(conn.execute(
            """
            SELECT status, points_owed FROM punishments
            WHERE normalized_name = 'NAVAS YUEN HIU NOK' AND month = '2026-01'
            """
        ))

        assert punishments == [("assigned", 3)]


class TestPunishmentMatrix:
    def test_lifecycle_states_covered(self, seeded):
        conn, _ = seeded

        statuses = {
            row[0] for row in conn.execute("SELECT DISTINCT status FROM punishments")
        }

        assert {"assigned", "submitted", "voided"} <= statuses

    def test_submitted_after_phone_hold_carries_late_flag(self, seeded):
        conn, _ = seeded

        february = [
            p for p in conn.execute(
                """
                SELECT status, submitted_at, overdue_at FROM punishments
                WHERE normalized_name = 'JASON FONG PAK HIN' AND month = '2026-02'
                """
            )
        ]

        assert len(february) == 1
        status, submitted_at, overdue_at = february[0]
        assert status == "submitted"
        assert overdue_at is not None
        assert submitted_at > overdue_at

    def test_voided_punishment_keeps_reason(self, seeded):
        conn, _ = seeded

        march = list(conn.execute(
            """
            SELECT status, void_reason FROM punishments
            WHERE normalized_name = 'JASON FONG PAK HIN' AND month = '2026-03'
            """
        ))

        assert march[0][0] == "voided"
        assert march[0][1]

    def test_reimport_leaves_assigned_points_frozen(self, seeded):
        conn, _ = seeded
        points_owed = conn.execute(
            """
            SELECT points_owed FROM punishments
            WHERE normalized_name = 'JASON FONG PAK HIN' AND month = '2026-07'
            """
        ).fetchone()[0]
        stored_points = conn.execute(
            """
            SELECT total_points FROM boarder_history
            WHERE normalized_name = 'JASON FONG PAK HIN' AND month = '2026-07'
            """
        ).fetchone()[0]

        # The corrected re-import raised the stored figure; the punishment
        # keeps its frozen snapshot per ADR 0001.
        assert stored_points > points_owed
        assert points_owed == 16


class TestGeneratedLogs:
    def test_log_files_written_for_each_month(self, tmp_path):
        namelist_path = tmp_path / "namelist.csv"
        write_namelist(namelist_path)
        log_dir = tmp_path / "logs"
        conn = sqlite3.connect(":memory:")
        try:
            storage.create_schema(conn)
            seed_demo_data.seed(conn, str(namelist_path), log_dir=str(log_dir))
        finally:
            conn.close()

        written = sorted(p.name for p in log_dir.glob("*.csv"))
        assert written == [
            "monthly-log-2026-01.csv", "monthly-log-2026-02.csv",
            "monthly-log-2026-03.csv", "monthly-log-2026-04.csv",
            "monthly-log-2026-05.csv", "monthly-log-2026-07.csv",
            "monthly-log-2026-08.csv",
        ]
        header = (log_dir / "monthly-log-2026-01.csv").open(encoding="utf-8")
        reader = csv.reader(header)
        assert next(reader)[:6] == [
            "Transaction Date", "Transaction Time", "Transaction Type",
            "Panel", "Door", "Name",
        ]
        header.close()

    def test_unknown_name_reported_on_march_import(self, seeded):
        conn, report = seeded

        march = next(o for o in report.month_outcomes if o.month == "2026-03")

        assert "NEW PROSPECT VISITOR" in march.unmatched_names

    def test_punctuation_variant_applied_in_march_only(self, seeded):
        conn, report = seeded

        distinct_keys = conn.execute(
            "SELECT COUNT(DISTINCT normalized_name) FROM boarder_history WHERE normalized_name LIKE '%CHAVEZ%'"
        ).fetchone()[0]
        march_unmatched = next(
            o for o in report.month_outcomes if o.month == "2026-03"
        ).unmatched_names

        # The mangled March spelling matched the one canonical key and was
        # never reported as unmatched.
        assert distinct_keys == 1
        assert not any("CHAVEZ" in name for name in march_unmatched)
