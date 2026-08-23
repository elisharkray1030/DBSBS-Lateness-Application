"""Storage-layer tests for the statistics read paths (#103 suite).

Covers derivation edge cases the HTML assertions express awkwardly:
Former derivation, punishment-only survivors, freshest-first identity
resolution, min/max seen months, and the empty database.
"""

from helpers import record

import storage


def make_boarder(normalized_name, display_name, bed):
    return storage.Boarder(
        normalized_name=normalized_name, display_name=display_name, bed=bed
    )


def save_history(conn, name, bed="101", frequency=1, total_minutes=2,
                 total_points=3, display_name=None, **kwargs):
    """Saves one history row, returning the stored month label."""
    month = kwargs.pop("month", "2026-03")
    assert not kwargs
    storage.save_month(
        conn,
        [
            record(
                name,
                bed,
                frequency,
                total_minutes,
                total_points,
                display_name=display_name,
            )
        ],
        month,
    )
    return month


def entry_by_key(entries, key):
    return next(e for e in entries if e.normalized_name == key)


class TestAllTimeListEmptyDatabase:
    def test_empty_database_derives_no_entries(self, conn):
        assert storage.list_all_time_boarders(conn) == []


class TestAllTimeListUnion:
    def test_master_list_boarder_without_history_is_current(self, conn):
        storage.replace_boarders(conn, [make_boarder("ALICE", "Alice", "601A")])

        entries = storage.list_all_time_boarders(conn)

        assert len(entries) == 1
        assert entries[0].normalized_name == "ALICE"
        assert entries[0].display_name == "Alice"
        assert entries[0].bed == "601A"
        assert entries[0].is_current is True
        assert entries[0].first_month is None
        assert entries[0].last_month is None
        assert entries[0].total_points == 0

    def test_removed_boarder_with_history_appears_once_as_former(self, conn):
        boarder_id = storage.add_boarder(conn, "BOB", "Bob", "601B")
        save_history(conn, "BOB", bed="601B", frequency=2, total_minutes=5,
                     total_points=7, month="2026-01")
        save_history(conn, "BOB", bed="601B", frequency=1, total_minutes=3,
                     total_points=4, month="2026-02")
        storage.delete_boarder(conn, boarder_id)

        entries = storage.list_all_time_boarders(conn)

        assert len(entries) == 1
        entry = entries[0]
        assert entry.is_current is False
        assert (entry.first_month, entry.last_month) == ("2026-01", "2026-02")
        assert entry.total_frequency == 3
        assert entry.total_minutes == 8
        assert entry.total_points == 11

    def test_punishment_only_survivor_still_appears(self, conn):
        storage.assign_punishments(
            conn,
            month="2026-03",
            boarders=[record("CAROL", "602A", 1, 4, 9)],
            deadline="2026-04-10",
            assigned_at="2026-04-01T09:00:00+00:00",
        )

        entries = storage.list_all_time_boarders(conn)

        assert len(entries) == 1
        entry = entries[0]
        assert entry.normalized_name == "CAROL"
        assert entry.is_current is False
        assert (entry.first_month, entry.last_month) == (None, None)
        assert entry.total_points == 0

    def test_voided_punishment_keeps_its_boarder_on_the_list(self, conn):
        storage.assign_punishments(
            conn,
            month="2026-03",
            boarders=[record("DAN", "602B", 1, 4, 9)],
            deadline="2026-04-10",
            assigned_at="2026-04-01T09:00:00+00:00",
        )
        punishment_id = storage.list_punishments(conn)[0].id
        storage.transition_punishment(
            conn, punishment_id, "voided",
            timestamp="2026-04-05T09:00:00+00:00", void_reason="exempt",
        )

        entries = storage.list_all_time_boarders(conn)

        assert [e.normalized_name for e in entries] == ["DAN"]
        assert entries[0].is_current is False

    def test_punctuation_variants_collapse_to_one_entry(self, conn):
        save_history(conn, "CHEN WEI", bed="701A", month="2026-01",
                     display_name="Chen Wei")
        save_history(conn, "CHEN WEI", bed="701A", month="2026-02",
                     display_name="CHEN, Wei")

        entries = storage.list_all_time_boarders(conn)

        assert len(entries) == 1
        assert entries[0].normalized_name == "CHEN WEI"

    def test_current_and_former_coexist(self, conn):
        storage.replace_boarders(conn, [make_boarder("ALICE", "Alice", "601A")])
        save_history(conn, "ALICE", bed="601A", month="2026-02")
        boarder_id = storage.add_boarder(conn, "BOB", "Bob", "601B")
        save_history(conn, "BOB", bed="601B", month="2026-01")
        storage.delete_boarder(conn, boarder_id)

        entries = storage.list_all_time_boarders(conn)

        assert [(e.normalized_name, e.is_current) for e in entries] == [
            ("ALICE", True),
            ("BOB", False),
        ]


class TestAllTimeFreshestIdentity:
    def test_identity_resolves_from_latest_month_snapshot(self, conn):
        save_history(conn, "ALICE", bed="101", month="2026-01",
                     display_name="Alice")
        save_history(conn, "ALICE", bed="202", month="2026-02",
                     display_name="Alicia")

        entries = storage.list_all_time_boarders(conn)

        assert (entries[0].display_name, entries[0].bed) == ("Alicia", "202")

    def test_same_month_tie_breaks_on_latest_import_time(self, conn):
        conn.execute(
            """
            INSERT INTO boarder_history
                (normalized_name, display_name, bed, month, frequency,
                 total_minutes, total_points, imported_at)
            VALUES ('ALICE', 'Old Alice', '101', '2026-03', 0, 0, 0,
                    '2026-04-01T09:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO punishments
                (normalized_name, display_name, bed, month, points_owed,
                 deadline, status, assigned_at)
            VALUES ('ALICE', 'Snapshot Alice', '505', '2026-03', 5,
                    '2026-04-10', 'assigned', '2026-04-02T09:00:00+00:00')
            """
        )
        conn.commit()

        entries = storage.list_all_time_boarders(conn)

        assert (entries[0].display_name, entries[0].bed) == (
            "Snapshot Alice",
            "505",
        )

    def test_current_master_entry_wins_over_fresher_snapshots(self, conn):
        save_history(conn, "ALICE", bed="202", month="2026-02",
                     display_name="History Alice")
        storage.update_boarder(
            conn, storage.add_boarder(conn, "ALICE", "Master Alice", "601A"),
            "ALICE", "Master Alice", "601A",
        )

        entries = storage.list_all_time_boarders(conn)

        assert (entries[0].display_name, entries[0].bed) == (
            "Master Alice",
            "601A",
        )


class TestAllTimeOrdering:
    def test_current_rows_precede_former_rows(self, conn):
        storage.replace_boarders(conn, [make_boarder("ZARA", "Zara", "901")])
        save_history(conn, "AMY", bed="100", month="2026-01")
        boarder_id = storage.add_boarder(conn, "BOB", "Bob", "102")
        save_history(conn, "BOB", bed="102", month="2026-01")
        storage.delete_boarder(conn, boarder_id)

        entries = storage.list_all_time_boarders(conn)

        assert [(e.display_name, e.is_current) for e in entries] == [
            ("Zara", True),
            ("Amy", False),
            ("Bob", False),
        ]

    def test_current_rows_order_by_bed_then_name(self, conn):
        storage.replace_boarders(
            conn,
            [
                make_boarder("ALICE", "Alice", "102"),
                make_boarder("BOB", "Bob", "101"),
                make_boarder("CAROL", "Carol", "9A"),
                make_boarder("DAN", "Dan", "10"),
            ],
        )

        entries = storage.list_all_time_boarders(conn)

        assert [e.display_name for e in entries] == ["Carol", "Dan", "Bob", "Alice"]


class TestGetBoarderSeries:
    def test_returns_monthly_series_chronologically(self, conn):
        save_history(conn, "ALICE", frequency=2, total_minutes=5, total_points=7,
                     month="2026-02")
        save_history(conn, "ALICE", frequency=1, total_minutes=3, total_points=4,
                     month="2026-01")

        series = storage.get_boarder_series(conn, "ALICE")

        assert [(row.month, row.frequency, row.total_minutes, row.total_points)
                for row in series] == [
            ("2026-01", 1, 3, 4),
            ("2026-02", 2, 5, 7),
        ]

    def test_unknown_key_returns_empty(self, conn):
        assert storage.get_boarder_series(conn, "NOBODY") == []

    def test_removed_boarder_series_survives_removal(self, conn):
        save_history(conn, "ALICE", month="2026-03")
        conn.execute("DELETE FROM boarders")
        conn.commit()

        assert [row.month for row in storage.get_boarder_series(conn, "ALICE")] == [
            "2026-03"
        ]


class TestResolveBoarderIdentity:
    def test_current_master_entry_wins(self, conn):
        storage.replace_boarders(conn, [make_boarder("ALICE", "Master Alice", "601A")])
        save_history(conn, "ALICE", bed="999", display_name="Snapshot", month="2026-01")

        resolved = storage.resolve_boarder_identity(conn, "ALICE")

        assert (resolved.display_name, resolved.bed, resolved.is_current) == (
            "Master Alice",
            "601A",
            True,
        )

    def test_former_resolves_freshest_snapshot(self, conn):
        save_history(conn, "BOB", bed="101", display_name="Old Bob", month="2026-01")
        save_history(conn, "BOB", bed="202", display_name="New Bob", month="2026-02")

        resolved = storage.resolve_boarder_identity(conn, "BOB")

        assert (resolved.display_name, resolved.bed, resolved.is_current) == (
            "New Bob",
            "202",
            False,
        )

    def test_unknown_key_returns_none(self, conn):
        assert storage.resolve_boarder_identity(conn, "NOBODY") is None


class TestSearchHistoryCarriesMatchKey:
    def test_search_results_carry_normalized_name_for_links(self, conn):
        save_history(conn, "CHEN WEI", display_name="Chen Wei", month="2026-03")

        results = storage.search_history(conn, "chen")

        assert [entry.normalized_name for entry in results] == ["CHEN WEI"]
