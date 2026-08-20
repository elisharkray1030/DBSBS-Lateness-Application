import io

import pytest
from helpers import month_labels, record
from records import Boarder

import storage
from parser import RejectedOutcome, SavedOutcome, ingest_log

MASTER = {
    "ALICE": Boarder("ALICE", "Alice", "101"),
    "BOB": Boarder("BOB", "Bob", "102"),
    "CAROL": Boarder("CAROL", "Carol", "103"),
}
LOG_HEADER = "Name,Transaction Time\n"


def ingest(text, month="2026-03", master=MASTER, conn=None):
    return ingest_log(io.StringIO(text), month, master, conn)


class TestIngestLog:
    def test_fully_matched_log_saves(self, conn):
        outcome = ingest(
            LOG_HEADER + "ALICE,07:42\n" + "ALICE,07:44:30\n" + "BOB,08:00\n",
            conn=conn,
        )

        assert isinstance(outcome, SavedOutcome)
        assert outcome.diagnostics.rows_read == 3
        assert outcome.diagnostics.matched_rows == 3
        assert outcome.diagnostics.unmatched_names == []
        assert outcome.diagnostics.unparseable_rows == []
        assert outcome.boarders_count == 2

        by_name = {r.name: r for r in outcome.boarders}
        assert by_name["ALICE"].frequency == 2
        assert by_name["ALICE"].total_minutes == 5
        assert by_name["ALICE"].total_points == 7
        assert by_name["BOB"].total_minutes == 19
        assert by_name["BOB"].total_points == 20
        assert by_name["CAROL"].frequency == 0

        saved = storage.get_month_report(conn, "2026-03")
        assert {r.name for r in saved} == {"ALICE", "BOB", "CAROL"}

    def test_mixed_matched_and_unmatched_names(self, conn):
        outcome = ingest(
            LOG_HEADER
            + "ALICE,07:42\n"
            + "GHOST,07:43\n"
            + "BOB,07:45\n"
            + "GHOST,07:44\n"
            + "ALICE,07:50\n",
            conn=conn,
        )

        assert isinstance(outcome, SavedOutcome)
        assert outcome.diagnostics.rows_read == 5
        assert outcome.diagnostics.matched_rows == 3
        assert outcome.diagnostics.unmatched_names == ["GHOST"]
        by_name = {r.name: r for r in outcome.boarders}
        assert by_name["ALICE"].frequency == 2
        assert by_name["ALICE"].total_minutes == 10
        assert by_name["BOB"].total_minutes == 4

    def test_unmatched_only_rejected_with_names(self, conn):
        outcome = ingest(
            LOG_HEADER + "GHOST,07:43\n" + "GHOST,07:44\n" + "GHOST,07:45\n",
            conn=conn,
        )

        assert isinstance(outcome, RejectedOutcome)
        assert outcome.diagnostics.unmatched_names == ["GHOST"]

    def test_mixed_parseable_and_unparseable_times(self, conn):
        outcome = ingest(
            LOG_HEADER
            + "ALICE,07:42\n"
            + "BOB,7:45\n"
            + "CAROL,07:99\n"
            + "ALICE,not a time\n",
            conn=conn,
        )

        assert isinstance(outcome, SavedOutcome)
        assert outcome.diagnostics.rows_read == 4
        assert outcome.diagnostics.matched_rows == 4
        raw = [(r.name, r.raw_value) for r in outcome.diagnostics.unparseable_rows]
        assert raw == [("BOB", "7:45"), ("CAROL", "07:99"), ("ALICE", "not a time")]

        by_name = {r.name: r for r in outcome.boarders}
        assert by_name["ALICE"].frequency == 1
        assert by_name["ALICE"].total_minutes == 1

    def test_clean_month_with_zero_lateness_saves(self, conn):
        outcome = ingest(
            LOG_HEADER + "ALICE,07:40\n" + "BOB,07:41:00\n",
            conn=conn,
        )

        assert isinstance(outcome, SavedOutcome)
        assert outcome.diagnostics.matched_rows == 2
        by_name = {r.name: r for r in outcome.boarders}
        assert by_name["ALICE"].frequency == 0
        assert by_name["BOB"].total_minutes == 0

    def test_boarder_arriving_at_08_00_is_late(self, conn):
        outcome = ingest(LOG_HEADER + "ALICE,08:00\n", conn=conn)

        assert isinstance(outcome, SavedOutcome)
        by_name = {r.name: r for r in outcome.boarders}
        assert by_name["ALICE"].frequency == 1
        assert by_name["ALICE"].total_minutes == 19

    def test_empty_log_rejected(self, conn):
        outcome = ingest("", conn=conn)

        assert isinstance(outcome, RejectedOutcome)
        assert "empty or has no data rows" in outcome.reason
        assert storage.list_months(conn) == []

    def test_missing_master_list_rejected(self, conn):
        outcome = ingest(LOG_HEADER + "ALICE,07:42\n", master=None, conn=conn)

        assert isinstance(outcome, RejectedOutcome)
        assert "master list is missing or empty" in outcome.reason
        assert "Boarders tab" in outcome.reason
        assert "namelist.csv" not in outcome.reason
        assert storage.list_months(conn) == []

    def test_empty_master_list_rejected(self, conn):
        outcome = ingest(LOG_HEADER + "ALICE,07:42\n", master={}, conn=conn)

        assert isinstance(outcome, RejectedOutcome)
        assert "master list is missing or empty" in outcome.reason

    def test_no_rows_match_any_boarder_rejected(self, conn):
        outcome = ingest(
            LOG_HEADER + "GHOST,07:43\n" + "SPECTRE,07:44\n",
            conn=conn,
        )

        assert isinstance(outcome, RejectedOutcome)
        assert "GHOST" in outcome.reason
        assert "SPECTRE" in outcome.reason
        assert storage.list_months(conn) == []

    @pytest.mark.parametrize("bad_month", ["March 2026", "2026/03", "2026-3", "202603", "march-2026", ""])
    def test_rejects_non_canonical_month_label(self, conn, bad_month):
        outcome = ingest(
            LOG_HEADER + "ALICE,07:42\n",
            month=bad_month,
            conn=conn,
        )

        assert isinstance(outcome, RejectedOutcome)
        assert "YYYY-MM" in outcome.reason
        assert storage.list_months(conn) == []

    def test_accepts_canonical_month_label(self, conn):
        outcome = ingest(
            LOG_HEADER + "ALICE,07:42\n",
            month="2026-03",
            conn=conn,
        )

        assert isinstance(outcome, SavedOutcome)
        assert "2026-03" in month_labels(storage.list_months(conn))

    def test_header_mismatch_rejected(self, conn):
        outcome = ingest(
            "Employee,Clock Time\n" + "ALICE,07:42\n" + "BOB,08:00\n",
            conn=conn,
        )

        assert isinstance(outcome, RejectedOutcome)
        assert "Name" in outcome.reason
        assert "Transaction Time" in outcome.reason

    def test_all_times_unparseable_rejected(self, conn):
        outcome = ingest(LOG_HEADER + "ALICE,7:42\n" + "BOB,bad\n", conn=conn)

        assert isinstance(outcome, RejectedOutcome)
        assert "ALICE" in outcome.reason
        assert "7:42" in outcome.reason
        assert storage.list_months(conn) == []

    def test_four_failure_causes_produce_distinct_reasons(self, conn):
        cases = [
            ingest("", conn=conn),
            ingest(LOG_HEADER + "GHOST,07:43\n", conn=conn),
            ingest(LOG_HEADER + "ALICE,7:42\n", conn=conn),
            ingest(LOG_HEADER + "ALICE,07:42\n", master=None, conn=conn),
        ]
        reasons = {o.reason for o in cases}
        assert len(reasons) == 4
        assert all(isinstance(o, RejectedOutcome) for o in cases)

    def test_does_not_mutate_master_list(self, conn):
        master = {
            "ALICE": Boarder("ALICE", "Alice", "101"),
            "BOB": Boarder("BOB", "Bob", "102"),
        }
        outcome = ingest(LOG_HEADER + "ALICE,07:42\n", master=master, conn=conn)

        assert isinstance(outcome, SavedOutcome)
        assert master == {
            "ALICE": Boarder("ALICE", "Alice", "101"),
            "BOB": Boarder("BOB", "Bob", "102"),
        }

    def test_canonical_display_name_flows_into_saved_rows(self, conn):
        master = {"ALICE": Boarder("ALICE", "Alicia", "101")}
        outcome = ingest(LOG_HEADER + "ALICE,07:42\n", master=master, conn=conn)

        assert isinstance(outcome, SavedOutcome)
        assert outcome.boarders[0].display_name == "Alicia"
        saved = storage.get_month_report(conn, "2026-03")
        assert saved[0].display_name == "Alicia"

    def test_rejected_ingestion_leaves_store_untouched(self, conn):
        storage.save_month(
            conn,
            [record("ALICE", "101", 2, 5, 7)],
            "2026-01",
        )

        outcome = ingest(LOG_HEADER + "GHOST,07:43\n", month="2026-02", conn=conn)

        assert isinstance(outcome, RejectedOutcome)
        assert "2026-02" not in month_labels(storage.list_months(conn))
        assert "2026-01" in month_labels(storage.list_months(conn))
        saved = storage.get_month_report(conn, "2026-01")
        assert saved[0].frequency == 2

    def test_saved_outcome_message_reports_diagnostics(self, conn):
        outcome = ingest(
            LOG_HEADER + "ALICE,07:42\n" + "GHOST,07:43\n" + "BOB,7:45\n",
            conn=conn,
        )

        assert isinstance(outcome, SavedOutcome)
        assert "2026-03" in outcome.message
        assert "1 boarder recorded" in outcome.message
        assert "Unmatched names: GHOST." in outcome.message
        assert "Unparseable times: BOB ('7:45')." in outcome.message

    def test_clean_import_message_omits_diagnostic_sections(self, conn):
        outcome = ingest(LOG_HEADER + "ALICE,07:42\n" + "BOB,08:00\n", conn=conn)

        assert isinstance(outcome, SavedOutcome)
        assert "2 boarders recorded" in outcome.message
        assert "Unmatched names" not in outcome.message
        assert "Unparseable times" not in outcome.message

    def test_unmatched_only_saved_message_lists_each_name_once(self, conn):
        outcome = ingest(
            LOG_HEADER + "ALICE,07:42\n" + "GHOST,07:43\n" + "GHOST,07:44\n",
            conn=conn,
        )

        assert isinstance(outcome, SavedOutcome)
        assert outcome.message == (
            "Monthly report saved for '2026-03' with 1 boarder recorded as late. "
            "Unmatched names: GHOST."
        )

    def test_unparseable_only_saved_message_lists_each_row(self, conn):
        outcome = ingest(
            LOG_HEADER + "ALICE,07:42\n" + "BOB,7:45\n" + "CAROL,07:99\n",
            conn=conn,
        )

        assert isinstance(outcome, SavedOutcome)
        assert "Unparseable times: BOB ('7:45'), CAROL ('07:99')." in outcome.message

    def test_clean_month_saves_and_reports_zero_boarders(self, conn):
        outcome = ingest(
            LOG_HEADER + "ALICE,07:40\n",
            conn=conn,
        )

        assert isinstance(outcome, SavedOutcome)
        assert "0 boarders recorded" in outcome.message
        assert "2026-03" in month_labels(storage.list_months(conn))

    def test_only_late_boarders_counted_in_message(self, conn):
        outcome = ingest(
            LOG_HEADER + "ALICE,07:42\n" + "CAROL,08:00\n",
            conn=conn,
        )

        assert isinstance(outcome, SavedOutcome)
        assert "2 boarders recorded" in outcome.message

    def test_end_to_end_month_appears_in_list_and_get(self, conn):
        outcome = ingest(
            LOG_HEADER + "ALICE,07:42\n" + "BOB,08:00\n",
            month="2026-03",
            conn=conn,
        )

        assert isinstance(outcome, SavedOutcome)
        assert "2026-03" in month_labels(storage.list_months(conn))
        saved = storage.get_month_report(conn, "2026-03")
        assert {r.name for r in saved} == {"ALICE", "BOB", "CAROL"}
        assert {r.bed for r in saved} == {"101", "102", "103"}

