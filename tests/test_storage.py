import sqlite3

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
            [record("ALICE"), record("BOB"), record("CAROL")],
            "2026-03",
        )

        summaries = storage.list_months(conn)
        assert len(summaries) == 1
        assert summaries[0].month == "2026-03"
        assert summaries[0].boarder_count == 3

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
            [record("ALICE", total_minutes=5), record("BOB", total_minutes=19)],
            "2026-03",
        )
        storage.save_month(conn, [record("ALICE", total_minutes=3)], "2026-04")

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
