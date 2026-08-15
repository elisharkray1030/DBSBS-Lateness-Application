import sqlite3

import pytest
from helpers import month_labels, record

import storage


class TestCreateSchema:
    def test_create_schema_is_idempotent(self):
        connection = sqlite3.connect(":memory:")
        storage.create_schema(connection)
        storage.create_schema(connection)
        assert storage.list_months(connection) == []
        connection.close()

    def test_empty_store_lists_no_months(self, conn):
        assert storage.list_months(conn) == []


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
