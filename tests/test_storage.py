import sqlite3

import pytest
from helpers import month_labels, record
from records import Boarder, boarder_sort_key

import storage


def boarder(normalized_name, display_name, bed):
    return Boarder(normalized_name=normalized_name, display_name=display_name, bed=bed)


class TestCreateSchema:
    def test_create_schema_is_idempotent(self):
        connection = sqlite3.connect(":memory:")
        storage.create_schema(connection)
        storage.create_schema(connection)
        assert storage.list_months(connection) == []
        connection.close()

    def test_empty_store_lists_no_months(self, conn):
        assert storage.list_months(conn) == []


class TestBedUniqueMigration:
    def _legacy_boarders_sql(self):
        return """
        CREATE TABLE boarders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_name TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            bed TEXT NOT NULL
        )
        """

    def _create_legacy_schema(self, connection):
        connection.execute(self._legacy_boarders_sql())
        connection.execute(
            """
            INSERT INTO boarders (normalized_name, display_name, bed)
            VALUES ('ALICE', 'Alice', '601A'), ('BOB', 'Bob', '601B')
            """
        )
        connection.commit()

    def test_migration_applies_unique_bed_constraint(self):
        connection = sqlite3.connect(":memory:")
        self._create_legacy_schema(connection)
        storage.create_schema(connection)
        with pytest.raises(sqlite3.IntegrityError):
            storage.add_boarder(connection, "CAROL", "Carol", "601A")
        connection.close()

    def test_migration_preserves_existing_rows(self):
        connection = sqlite3.connect(":memory:")
        self._create_legacy_schema(connection)
        storage.create_schema(connection)
        boarders = storage.list_boarders(connection)
        assert [(b.normalized_name, b.bed) for b in boarders] == [
            ("ALICE", "601A"),
            ("BOB", "601B"),
        ]
        connection.close()

    def test_migration_is_idempotent(self):
        connection = sqlite3.connect(":memory:")
        self._create_legacy_schema(connection)
        storage.create_schema(connection)
        storage.create_schema(connection)
        assert [b.normalized_name for b in storage.list_boarders(connection)] == [
            "ALICE",
            "BOB",
        ]
        connection.close()


class TestNormalizedNameKeyMigration:
    """Older builds stored match keys under uppercase-and-trim only, so
    master-list entries like 'SURNAME, Given' could never match log rows like
    'SURNAME Given'. create_schema re-keys every stored row in place."""

    def _seed_legacy_keys(self, connection):
        storage.create_schema(connection)
        connection.execute(
            """
            INSERT INTO boarders (normalized_name, display_name, bed)
            VALUES ('LUCAS CHAVEZ MOCAN, LUCAS', 'Lucas CHAVEZ MOCAN, Lucas', '607B')
            """
        )
        connection.execute(
            """
            INSERT INTO boarder_history
                (normalized_name, display_name, bed, month, frequency,
                 total_minutes, total_points, imported_at)
            VALUES ('LUCAS CHAVEZ MOCAN, LUCAS', 'Lucas CHAVEZ MOCAN, Lucas',
                    '607B', '2026-04', 0, 0, 0, '2026-05-01T00:00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO punishments
                (normalized_name, display_name, bed, month, points_owed,
                 deadline, status, assigned_at)
            VALUES ('LUCAS CHAVEZ MOCAN, LUCAS', 'Lucas CHAVEZ MOCAN, Lucas',
                    '607B', '2026-04', 5, '2026-05-10', 'assigned',
                    '2026-05-02T00:00:00')
            """
        )
        connection.commit()

    def test_migration_rekeys_all_three_tables(self):
        connection = sqlite3.connect(":memory:")
        self._seed_legacy_keys(connection)
        storage.create_schema(connection)

        assert [b.normalized_name for b in storage.list_boarders(connection)] == [
            "LUCAS CHAVEZ MOCAN LUCAS"
        ]
        assert [(b.display_name, b.bed) for b in storage.list_boarders(connection)] == [
            ("Lucas CHAVEZ MOCAN, Lucas", "607B")
        ]
        history = storage.search_history(connection, "chavez")
        assert [entry.month for entry in history] == ["2026-04"]
        punishments = storage.list_punishments(connection)
        assert [p.normalized_name for p in punishments] == [
            "LUCAS CHAVEZ MOCAN LUCAS"
        ]
        connection.close()

    def test_migration_is_idempotent(self):
        connection = sqlite3.connect(":memory:")
        self._seed_legacy_keys(connection)
        storage.create_schema(connection)
        storage.create_schema(connection)
        assert [b.normalized_name for b in storage.list_boarders(connection)] == [
            "LUCAS CHAVEZ MOCAN LUCAS"
        ]
        connection.close()

    def test_migration_leaves_clean_keys_untouched(self, conn):
        assert [b.normalized_name for b in storage.list_boarders(conn)] == []

    def test_migration_keeps_first_row_when_keys_collapse(self):
        connection = sqlite3.connect(":memory:")
        storage.create_schema(connection)
        connection.executemany(
            "INSERT INTO boarders (normalized_name, display_name, bed) VALUES (?, ?, ?)",
            [
                ("CHEN WEI", "Chen Wei A", "701A"),
                ("CHEN, WEI", "Chen Wei B", "701B"),
            ],
        )
        connection.commit()
        storage.create_schema(connection)

        keys = sorted(b.normalized_name for b in storage.list_boarders(connection))
        assert keys == ["CHEN WEI", "CHEN, WEI"]
        beds = sorted(b.bed for b in storage.list_boarders(connection))
        assert beds == ["701A", "701B"]
        connection.close()


class TestMeta:
    def test_set_and_get_meta(self, conn):
        storage.set_meta(conn, "k", "v")
        assert storage.get_meta(conn, "k") == "v"

    def test_get_unknown_key_returns_none(self, conn):
        assert storage.get_meta(conn, "missing") is None

    def test_set_meta_overwrites(self, conn):
        storage.set_meta(conn, "k", "1")
        storage.set_meta(conn, "k", "2")
        assert storage.get_meta(conn, "k") == "2"


class TestBoarders:
    def test_create_schema_creates_empty_boarders_table(self, conn):
        assert storage.list_boarders(conn) == []

    def test_replace_boarders_sets_the_list(self, conn):
        storage.replace_boarders(
            conn,
            [boarder("ALICE", "Alice", "601A"), boarder("BOB", "Bob", "601B")],
        )
        boarders = storage.list_boarders(conn)
        assert [(b.normalized_name, b.display_name, b.bed) for b in boarders] == [
            ("ALICE", "Alice", "601A"),
            ("BOB", "Bob", "601B"),
        ]

    def test_replace_boarders_replaces_not_appends(self, conn):
        storage.replace_boarders(conn, [boarder("ALICE", "Alice", "601A")])
        storage.replace_boarders(conn, [boarder("BOB", "Bob", "601B")])
        assert [b.normalized_name for b in storage.list_boarders(conn)] == ["BOB"]

    def test_list_boarders_orders_by_bed_then_display_name(self, conn):
        storage.replace_boarders(
            conn,
            [
                boarder("ALICE", "Alice", "102"),
                boarder("BOB", "Bob", "101"),
                boarder("CAROL", "Carol", "101A"),
            ],
        )
        assert [(b.normalized_name, b.bed) for b in storage.list_boarders(conn)] == [
            ("BOB", "101"),
            ("CAROL", "101A"),
            ("ALICE", "102"),
        ]

    def test_list_boarders_sql_order_matches_boarder_sort_key(self, conn):
        rows = [
            boarder("ALICE", "Alice", "102"),
            boarder("BOB", "Bob", "101"),
            boarder("CAROL", "Carol", "101A"),
        ]
        storage.replace_boarders(conn, rows)
        actual = storage.list_boarders(conn)
        expected = sorted(rows, key=boarder_sort_key)
        assert [(b.normalized_name, b.bed) for b in actual] == [
            (b.normalized_name, b.bed) for b in expected
        ]

    def test_boarder_master_list_maps_normalized_to_boarder(self, conn):
        storage.replace_boarders(
            conn, [boarder("ALICE", "Alice", "601A"), boarder("BOB", "Bob", "601B")]
        )
        assert storage.boarder_master_list(conn) == {
            "ALICE": boarder("ALICE", "Alice", "601A"),
            "BOB": boarder("BOB", "Bob", "601B"),
        }

    def test_boarder_master_list_empty_returns_empty_dict(self, conn):
        assert storage.boarder_master_list(conn) == {}


class TestReplaceBoardersSafety:
    def test_duplicate_bed_across_names_rejected_and_roster_unchanged(self, conn):
        storage.replace_boarders(conn, [boarder("ALICE", "Alice", "601A")])
        with pytest.raises(ValueError):
            storage.replace_boarders(
                conn,
                [boarder("ALICE", "Alice", "601A"), boarder("BOB", "Bob", "601A")],
            )
        assert [(b.normalized_name, b.bed) for b in storage.list_boarders(conn)] == [
            ("ALICE", "601A")
        ]

    def test_duplicate_bed_error_names_both_boarders(self, conn):
        with pytest.raises(ValueError) as excinfo:
            storage.replace_boarders(
                conn,
                [boarder("ALICE", "Alice", "601A"), boarder("BOB", "Bob", "601A")],
            )
        message = str(excinfo.value)
        assert "601A" in message
        assert "Alice" in message
        assert "Bob" in message

    def test_duplicate_name_last_row_wins(self, conn):
        storage.replace_boarders(
            conn,
            [boarder("ALICE", "Alice", "601A"), boarder("ALICE", "Alicia", "602A")],
        )
        assert [(b.display_name, b.bed) for b in storage.list_boarders(conn)] == [
            ("Alicia", "602A")
        ]

    def test_same_name_same_bed_duplicates_are_not_a_conflict(self, conn):
        storage.replace_boarders(
            conn,
            [boarder("ALICE", "Alice", "601A"), boarder("ALICE", "Alice", "601A")],
        )
        assert [(b.display_name, b.bed) for b in storage.list_boarders(conn)] == [
            ("Alice", "601A")
        ]

    def test_valid_replacement_still_replaces(self, conn):
        storage.replace_boarders(conn, [boarder("ALICE", "Alice", "601A")])
        storage.replace_boarders(conn, [boarder("BOB", "Bob", "601B")])
        assert [b.normalized_name for b in storage.list_boarders(conn)] == ["BOB"]


class TestAddBoarder:
    def test_add_boarder_returns_new_id(self, conn):
        boarder_id = storage.add_boarder(conn, "ALICE", "Alice", "601A")
        boarders = storage.list_boarders(conn)
        assert [(b.id, b.normalized_name, b.display_name, b.bed) for b in boarders] == [
            (boarder_id, "ALICE", "Alice", "601A")
        ]

    def test_add_boarder_rejects_duplicate_normalized_name(self, conn):
        storage.add_boarder(conn, "ALICE", "Alice", "601A")
        with pytest.raises(sqlite3.IntegrityError):
            storage.add_boarder(conn, "ALICE", "alice", "601B")

    def test_add_boarder_orders_with_existing_list(self, conn):
        storage.add_boarder(conn, "BOB", "Bob", "601B")
        storage.add_boarder(conn, "ALICE", "Alice", "601A")
        assert [b.display_name for b in storage.list_boarders(conn)] == ["Alice", "Bob"]


class TestBoarderExists:
    def test_known_normalized_name_is_found(self, conn):
        storage.add_boarder(conn, "ALICE", "Alice", "601A")
        assert storage.boarder_exists(conn, "ALICE") is True

    def test_unknown_normalized_name_is_not_found(self, conn):
        assert storage.boarder_exists(conn, "ALICE") is False

    def test_exclude_id_ignores_the_row_itself(self, conn):
        boarder_id = storage.add_boarder(conn, "ALICE", "Alice", "601A")
        assert storage.boarder_exists(conn, "ALICE", exclude_id=boarder_id) is False

    def test_exclude_id_still_finds_other_rows(self, conn):
        storage.add_boarder(conn, "ALICE", "Alice", "601A")
        other_id = storage.add_boarder(conn, "BOB", "Bob", "601B")
        assert storage.boarder_exists(conn, "ALICE", exclude_id=other_id) is True


class TestBedExists:
    def test_known_bed_is_found(self, conn):
        storage.add_boarder(conn, "ALICE", "Alice", "601A")
        assert storage.bed_exists(conn, "601A") is True

    def test_unknown_bed_is_not_found(self, conn):
        assert storage.bed_exists(conn, "601A") is False

    def test_exclude_id_ignores_the_row_itself(self, conn):
        boarder_id = storage.add_boarder(conn, "ALICE", "Alice", "601A")
        assert storage.bed_exists(conn, "601A", exclude_id=boarder_id) is False

    def test_exclude_id_still_finds_other_rows(self, conn):
        storage.add_boarder(conn, "ALICE", "Alice", "601A")
        other_id = storage.add_boarder(conn, "BOB", "Bob", "601B")
        assert storage.bed_exists(conn, "601A", exclude_id=other_id) is True


class TestUpdateBoarder:
    def test_update_boarder_changes_name_and_bed(self, conn):
        boarder_id = storage.add_boarder(conn, "ALICE", "Alice", "601A")
        storage.update_boarder(conn, boarder_id, "ALICIA", "Alicia", "602A")
        boarders = storage.list_boarders(conn)
        assert [(b.normalized_name, b.display_name, b.bed) for b in boarders] == [
            ("ALICIA", "Alicia", "602A")
        ]

    def test_update_boarder_rejects_duplicate_normalized_name(self, conn):
        storage.add_boarder(conn, "ALICE", "Alice", "601A")
        other = storage.add_boarder(conn, "BOB", "Bob", "601B")
        with pytest.raises(sqlite3.IntegrityError):
            storage.update_boarder(conn, other, "ALICE", "Alice", "601B")

    def test_update_unknown_boarder_is_noop(self, conn):
        storage.update_boarder(conn, 999, "ALICE", "Alice", "601A")
        assert storage.list_boarders(conn) == []


class TestUpdateBoarders:
    def test_updates_can_swap_unique_values(self, conn):
        alice_id = storage.add_boarder(conn, "ALICE", "Alice", "601A")
        bob_id = storage.add_boarder(conn, "BOB", "Bob", "601B")

        storage.update_boarders(
            conn,
            [
                (alice_id, "ALICE", "Alice", "601B"),
                (bob_id, "BOB", "Bob", "601A"),
            ],
        )

        assert {(boarder.display_name, boarder.bed) for boarder in storage.list_boarders(conn)} == {
            ("Alice", "601B"),
            ("Bob", "601A"),
        }

    def test_rejects_conflict_without_partial_updates(self, conn):
        alice_id = storage.add_boarder(conn, "ALICE", "Alice", "601A")
        bob_id = storage.add_boarder(conn, "BOB", "Bob", "601B")

        with pytest.raises(ValueError):
            storage.update_boarders(
                conn,
                [
                    (alice_id, "BOB", "Bob", "601A"),
                    (bob_id, "BOB", "Bob", "601B"),
                ],
            )

        assert {(boarder.display_name, boarder.bed) for boarder in storage.list_boarders(conn)} == {
            ("Alice", "601A"),
            ("Bob", "601B"),
        }


class TestDeleteBoarder:
    def test_delete_boarder_removes_it(self, conn):
        boarder_id = storage.add_boarder(conn, "ALICE", "Alice", "601A")
        storage.delete_boarder(conn, boarder_id)
        assert storage.list_boarders(conn) == []

    def test_delete_unknown_boarder_is_noop(self, conn):
        storage.delete_boarder(conn, 999)
        assert storage.list_boarders(conn) == []

    def test_delete_boarder_leaves_history_intact(self, conn):
        boarder_id = storage.add_boarder(conn, "ALICE", "Alice", "601A")
        storage.save_month(conn, [record("ALICE", "601A", 2, 5, 7)], "2026-03")
        storage.delete_boarder(conn, boarder_id)
        assert storage.list_boarders(conn) == []
        saved = storage.get_month_report(conn, "2026-03")
        assert {r.name for r in saved} == {"ALICE"}


class TestSaveMonth:
    def test_upsert_replaces_boarder_row_for_month(self, conn):
        storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-03")
        storage.save_month(conn, [record("ALICE", "101", 3, 8, 11)], "2026-03")

        saved = storage.get_month_report(conn, "2026-03")
        assert len(saved) == 1
        assert saved[0].frequency == 3
        assert saved[0].total_minutes == 8
        assert saved[0].total_points == 11

    def test_same_boarder_kept_across_months(self, conn):
        storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-03")
        storage.save_month(conn, [record("ALICE", "101", 1, 2, 3)], "2026-04")

        assert [r.frequency for r in storage.get_month_report(conn, "2026-03")] == [2]
        assert [r.frequency for r in storage.get_month_report(conn, "2026-04")] == [1]

    def test_save_month_persists_canonical_display_name(self, conn):
        storage.save_month(conn, [record("ALICE", "101", 2, 5, 7, display_name="Alicia")], "2026-03")
        assert storage.get_month_report(conn, "2026-03")[0].display_name == "Alicia"

    def test_upsert_refreshes_display_name_for_that_month(self, conn):
        storage.save_month(conn, [record("ALICE", "101", 2, 5, 7, display_name="Alice")], "2026-03")
        storage.save_month(conn, [record("ALICE", "101", 3, 8, 11, display_name="Alicia")], "2026-03")

        assert storage.get_month_report(conn, "2026-03")[0].display_name == "Alicia"

    def test_refresh_does_not_rewrite_other_months(self, conn):
        storage.save_month(conn, [record("ALICE", "101", 2, 5, 7, display_name="Alice")], "2026-03")
        storage.save_month(conn, [record("ALICE", "101", 2, 5, 7, display_name="Alicia")], "2026-04")

        assert storage.get_month_report(conn, "2026-03")[0].display_name == "Alice"
        assert storage.get_month_report(conn, "2026-04")[0].display_name == "Alicia"


class TestListMonths:
    def test_returns_saved_months_newest_first(self, conn):
        storage.save_month(conn, [record("ALICE")], "2026-01")
        storage.save_month(conn, [record("ALICE")], "2026-03")
        storage.save_month(conn, [record("ALICE")], "2026-02")

        summaries = storage.list_months(conn)
        assert month_labels(summaries) == ["2026-03", "2026-02", "2026-01"]

    def test_distinct_months_only(self, conn):
        storage.save_month(conn, [record("ALICE")], "2026-03")
        storage.save_month(conn, [record("BOB")], "2026-03")

        summaries = storage.list_months(conn)
        assert month_labels(summaries) == ["2026-03"]

    def test_summarizes_boarder_count_across_boarders(self, conn):
        storage.save_month(
            conn,
            [record("ALICE", frequency=2), record("BOB", frequency=1), record("CAROL")],
            "2026-03",
        )

        summaries = storage.list_months(conn)
        assert len(summaries) == 1
        assert summaries[0].month == "2026-03"
        assert summaries[0].boarder_count == 2

    def test_zero_lateness_boarders_not_counted(self, conn):
        storage.save_month(
            conn,
            [record("ALICE"), record("BOB")],
            "2026-03",
        )

        summaries = storage.list_months(conn)
        assert summaries[0].boarder_count == 0

    def test_summarizes_total_minutes_across_boarders(self, conn):
        storage.save_month(
            conn,
            [
                record("ALICE", total_minutes=5),
                record("BOB", total_minutes=19),
                record("CAROL", total_minutes=0),
            ],
            "2026-03",
        )

        summaries = storage.list_months(conn)
        assert summaries[0].total_minutes == 24

    def test_orders_multiple_month_summaries_newest_first(self, conn):
        storage.save_month(
            conn,
            [
                record("ALICE", frequency=1, total_minutes=5),
                record("BOB", frequency=2, total_minutes=19),
            ],
            "2026-03",
        )
        storage.save_month(conn, [record("ALICE", frequency=3, total_minutes=3)], "2026-04")

        summaries = storage.list_months(conn)
        assert month_labels(summaries) == ["2026-04", "2026-03"]
        assert summaries[0].boarder_count == 1
        assert summaries[0].total_minutes == 3
        assert summaries[1].boarder_count == 2
        assert summaries[1].total_minutes == 24


class TestListPunishmentMonths:
    def test_returns_distinct_punishment_months_newest_first(self, conn):
        storage.assign_punishments(
            conn,
            month="2026-01",
            boarders=[record("ALICE", "101", 1, 1, 2)],
            deadline="2026-02-01",
            assigned_at="2026-01-01T09:00:00+00:00",
        )
        storage.assign_punishments(
            conn,
            month="2026-03",
            boarders=[record("BOB", "102", 1, 1, 2)],
            deadline="2026-04-01",
            assigned_at="2026-03-01T09:00:00+00:00",
        )

        assert storage.list_punishment_months(conn) == ["2026-03", "2026-01"]


class TestGetMonthReport:
    def test_returns_saved_record_set(self, conn):
        storage.save_month(
            conn,
            [record("ALICE", "102", 2, 5, 7), record("BOB", "101", 1, 19, 20)],
            "2026-03",
        )

        saved = storage.get_month_report(conn, "2026-03")
        assert {r.name for r in saved} == {"ALICE", "BOB"}
        by_name = {r.name: r for r in saved}
        assert by_name["ALICE"].total_points == 7
        assert by_name["BOB"].total_minutes == 19

    def test_orders_by_bed_then_display_name(self, conn):
        storage.save_month(
            conn,
            [record("ALICE", "102"), record("BOB", "101"), record("CAROL", "101")],
            "2026-03",
        )

        saved = storage.get_month_report(conn, "2026-03")
        assert [r.name for r in saved] == ["BOB", "CAROL", "ALICE"]

    def test_orders_beds_by_number_then_suffix(self, conn):
        storage.save_month(
            conn,
            [
                record("A", bed="10"),
                record("B", bed="9A"),
                record("C", bed="101A"),
                record("D", bed="101"),
            ],
            "2026-03",
        )

        saved = storage.get_month_report(conn, "2026-03")
        assert [r.name for r in saved] == ["B", "A", "D", "C"]

    def test_unknown_month_returns_empty(self, conn):
        assert storage.get_month_report(conn, "nope") == []


class TestSearchHistory:
    def test_partial_name_match(self, conn):
        storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-03")
        storage.save_month(conn, [record("ALICIA", "102", 1, 2, 3)], "2026-04")

        results = storage.search_history(conn, "ali")
        assert len(results) == 2
        assert {r.display_name for r in results} == {"Alice", "Alicia"}
        assert {r.month for r in results} == {"2026-03", "2026-04"}

    def test_case_insensitive_query(self, conn):
        storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-03")

        results = storage.search_history(conn, "aLiCe")
        assert [r.month for r in results] == ["2026-03"]

    def test_whitespace_padded_query_matches(self, conn):
        storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-03")

        padded = storage.search_history(conn, "  alice  ")
        unpadded = storage.search_history(conn, "alice")
        assert [r.month for r in padded] == [r.month for r in unpadded] == ["2026-03"]

    def test_empty_query_returns_nothing(self, conn):
        assert storage.search_history(conn, "") == []


class TestDeleteMonth:
    def test_removes_month(self, conn):
        storage.save_month(conn, [record("ALICE")], "2026-03")

        assert storage.delete_month(conn, "2026-03") == 1
        assert month_labels(storage.list_months(conn)) == []

    def test_returns_zero_for_unknown_month(self, conn):
        assert storage.delete_month(conn, "nope") == 0

    def test_delete_removes_only_that_month(self, conn):
        storage.save_month(conn, [record("ALICE")], "2026-03")
        storage.save_month(conn, [record("ALICE")], "2026-04")

        storage.delete_month(conn, "2026-03")
        assert month_labels(storage.list_months(conn)) == ["2026-04"]


class TestAssignPunishments:
    def test_assigns_one_row_per_boarder_with_frozen_fields(self, conn):
        storage.assign_punishments(
            conn,
            month="2026-03",
            boarders=[
                record("ALICE", "101", 2, 5, 7),
                record("BOB", "102", 1, 19, 20),
            ],
            deadline="2026-04-10",
            assigned_at="2026-04-01T09:00:00+00:00",
        )

        rows = storage.list_punishments(conn)
        assert len(rows) == 2
        alice = next(r for r in rows if r.normalized_name == "ALICE")
        assert alice.display_name == "Alice"
        assert alice.bed == "101"
        assert alice.points_owed == 7
        assert alice.month == "2026-03"
        assert alice.deadline == "2026-04-10"
        assert alice.status == "assigned"
        assert alice.assigned_at == "2026-04-01T09:00:00+00:00"

    def test_partial_unique_index_rejects_second_active_per_month(self, conn):
        storage.assign_punishments(
            conn,
            month="2026-03",
            boarders=[record("ALICE", "101", 2, 5, 7)],
            deadline="2026-04-10",
            assigned_at="2026-04-01T09:00:00+00:00",
        )

        with pytest.raises(sqlite3.IntegrityError):
            storage.assign_punishments(
                conn,
                month="2026-03",
                boarders=[record("ALICE", "101", 3, 8, 11)],
                deadline="2026-04-20",
                assigned_at="2026-04-02T09:00:00+00:00",
            )

    def test_reassignment_allowed_after_void(self, conn):
        storage.assign_punishments(
            conn,
            month="2026-03",
            boarders=[record("ALICE", "101", 2, 5, 7)],
            deadline="2026-04-10",
            assigned_at="2026-04-01T09:00:00+00:00",
        )
        row = storage.list_punishments(conn)[0]
        storage.transition_punishment(
            conn, row.id, "voided", timestamp="2026-04-05T09:00:00+00:00", void_reason="exempt"
        )

        storage.assign_punishments(
            conn,
            month="2026-03",
            boarders=[record("ALICE", "101", 3, 8, 11)],
            deadline="2026-04-20",
            assigned_at="2026-04-06T09:00:00+00:00",
        )

        rows = storage.list_punishments(conn, statuses=("assigned",))
        assert len(rows) == 1
        assert rows[0].points_owed == 11

    def test_voiding_submitted_punishment_preserves_audit_fields(self, conn):
        storage.assign_punishments(
            conn,
            month="2026-03",
            boarders=[record("ALICE", "101", 2, 5, 7)],
            deadline="2026-04-10",
            assigned_at="2026-04-01T09:00:00+00:00",
        )
        row = storage.list_punishments(conn)[0]
        storage.transition_punishment(
            conn, row.id, "submitted", timestamp="2026-04-09T09:00:00+00:00"
        )

        storage.transition_punishment(
            conn,
            row.id,
            "voided",
            timestamp="2026-04-10T09:00:00+00:00",
            void_reason="later exempted",
        )

        saved = storage.get_punishment(conn, row.id)
        assert saved.status == "voided"
        assert saved.submitted_at == "2026-04-09T09:00:00+00:00"
        assert saved.voided_at == "2026-04-10T09:00:00+00:00"
        assert saved.void_reason == "later exempted"
