import csv
import io

import pytest

from parser import (
    export_to_csv,
    ingestion_rejection_message,
    load_namelist,
    parse_time_seconds,
    process_lateness,
)


def write_log(tmp_path, content, filename="log.csv"):
    log_path = tmp_path / filename
    log_path.write_text(content, encoding="utf-8")
    return str(log_path)


MASTER = {
    "ALICE": {"bed": "101"},
    "BOB": {"bed": "102"},
    "CAROL": {"bed": "103"},
}

LOG_HEADER = "Name,Transaction Time\n"


class TestLoadNamelist:
    def test_loads_valid_boarders(self, tmp_path):
        path = tmp_path / "namelist.csv"
        path.write_text("Name,Bed\nalice,101\nBOB,102\ncarol smith,103\n", encoding="utf-8")
        result = load_namelist(str(path))
        assert result == {
            "ALICE": {"bed": "101"},
            "BOB": {"bed": "102"},
            "CAROL SMITH": {"bed": "103"},
        }

    def test_missing_file_returns_none(self, tmp_path):
        assert load_namelist(str(tmp_path / "does-not-exist.csv")) is None

    def test_empty_master_returns_empty_dict(self, tmp_path):
        path = tmp_path / "namelist.csv"
        path.write_text("Name,Bed\n", encoding="utf-8")
        assert load_namelist(str(path)) == {}


class TestParseTimeSeconds:
    def test_accepts_hh_mm(self):
        assert parse_time_seconds("07:41") == (7 * 3600) + (41 * 60)

    def test_accepts_hh_mm_ss(self):
        assert parse_time_seconds("07:41:30") == (7 * 3600) + (41 * 60) + 30

    def test_accepts_24_hour_boundaries(self):
        assert parse_time_seconds("00:00") == 0
        assert parse_time_seconds("23:59:59") == (23 * 3600) + (59 * 60) + 59

    @pytest.mark.parametrize(
        "value",
        [
            "7:41",
            "5:41 PM",
            "07:60",
            "07:41:60",
            "24:00",
            "07:4",
            "0741",
            "seven o'clock",
            "8:00:00 AM",
            "",
        ],
    )
    def test_rejects_non_strict_values(self, value):
        assert parse_time_seconds(value) is None


class TestProcessLateness:
    def test_fully_matched_log(self, tmp_path):
        log = write_log(
            tmp_path,
            LOG_HEADER
            + "ALICE,07:42\n"
            + "ALICE,07:44:30\n"
            + "BOB,08:00\n",
        )
        result = process_lateness(log, MASTER)

        assert result.rows_read == 3
        assert result.matched_rows == 3
        assert result.unmatched_names == []
        assert result.unparseable_rows == []
        assert result.has_parseable_data is True

        assert result.boarders["ALICE"] == {
            "bed": "101",
            "frequency": 2,
            "total_minutes": 5,
            "total_points": 7,
        }
        assert result.boarders["BOB"] == {
            "bed": "102",
            "frequency": 1,
            "total_minutes": 19,
            "total_points": 20,
        }
        assert result.boarders["CAROL"] == {
            "bed": "103",
            "frequency": 0,
            "total_minutes": 0,
            "total_points": 0,
        }

    def test_mixed_matched_and_unmatched_names(self, tmp_path):
        log = write_log(
            tmp_path,
            LOG_HEADER
            + "ALICE,07:42\n"
            + "GHOST,07:43\n"
            + "BOB,07:45\n"
            + "GHOST,07:44\n"
            + "ALICE,07:50\n",
        )
        result = process_lateness(log, MASTER)

        assert result.rows_read == 5
        assert result.matched_rows == 3
        assert result.unmatched_names == ["GHOST"]
        assert result.has_parseable_data is True
        assert result.boarders["ALICE"]["frequency"] == 2
        assert result.boarders["ALICE"]["total_minutes"] == 10
        assert result.boarders["BOB"]["frequency"] == 1
        assert result.boarders["BOB"]["total_minutes"] == 4

    def test_unmatched_names_reported_once_each(self, tmp_path):
        log = write_log(
            tmp_path,
            LOG_HEADER + "GHOST,07:43\n" + "GHOST,07:44\n" + "GHOST,07:45\n",
        )
        result = process_lateness(log, MASTER)
        assert result.unmatched_names == ["GHOST"]

    def test_mixed_parseable_and_unparseable_times(self, tmp_path):
        log = write_log(
            tmp_path,
            LOG_HEADER
            + "ALICE,07:42\n"
            + "BOB,7:45\n"
            + "CAROL,07:99\n"
            + "ALICE,not a time\n",
        )
        result = process_lateness(log, MASTER)

        assert result.rows_read == 4
        assert result.matched_rows == 4
        assert result.has_parseable_data is True

        raw = [(row.name, row.raw_value) for row in result.unparseable_rows]
        assert raw == [("BOB", "7:45"), ("CAROL", "07:99"), ("ALICE", "not a time")]

        assert result.boarders["ALICE"]["frequency"] == 1
        assert result.boarders["ALICE"]["total_minutes"] == 1

    def test_entirely_unparseable_times(self, tmp_path):
        log = write_log(
            tmp_path,
            LOG_HEADER + "ALICE,7:42\n" + "BOB,bad\n",
        )
        result = process_lateness(log, MASTER)

        assert result.rows_read == 2
        assert result.matched_rows == 2
        assert result.has_parseable_data is False
        assert len(result.unparseable_rows) == 2
        assert result.boarders["ALICE"]["frequency"] == 0

    def test_clean_month_with_zero_lateness(self, tmp_path):
        log = write_log(
            tmp_path,
            LOG_HEADER + "ALICE,07:40\n" + "BOB,07:41:00\n",
        )
        result = process_lateness(log, MASTER)

        assert result.has_parseable_data is True
        assert result.matched_rows == 2
        assert result.boarders["ALICE"] == {
            "bed": "101",
            "frequency": 0,
            "total_minutes": 0,
            "total_points": 0,
        }
        assert result.boarders["BOB"]["frequency"] == 0

    def test_empty_log(self, tmp_path):
        log = write_log(tmp_path, "")
        result = process_lateness(log, MASTER)
        assert result.rows_read == 0
        assert result.matched_rows == 0
        assert result.has_parseable_data is False

    def test_missing_log_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            process_lateness(str(tmp_path / "missing.csv"), MASTER)

    def test_missing_master_list(self, tmp_path):
        log = write_log(tmp_path, LOG_HEADER + "ALICE,07:42\n")
        result = process_lateness(log, None)
        assert result.boarders == {}
        assert result.matched_rows == 0
        assert result.has_parseable_data is False
        assert result.rows_read == 1

    def test_does_not_mutate_master_list(self, tmp_path):
        master = {"ALICE": {"bed": "101"}, "BOB": {"bed": "102"}}
        log = write_log(tmp_path, LOG_HEADER + "ALICE,07:42\n")
        process_lateness(log, master)
        assert master == {"ALICE": {"bed": "101"}, "BOB": {"bed": "102"}}


class TestIngestionRejectionMessage:
    def test_returns_none_when_saveable(self, tmp_path):
        log = write_log(tmp_path, LOG_HEADER + "ALICE,07:42\n")
        result = process_lateness(log, MASTER)
        assert ingestion_rejection_message(result, MASTER) is None

    def test_master_list_missing_or_empty(self, tmp_path):
        log = write_log(tmp_path, LOG_HEADER + "ALICE,07:42\n")
        result = process_lateness(log, None)
        message = ingestion_rejection_message(result, None)
        assert message is not None
        assert "master" in message.lower()

    def test_no_rows_matched_any_boarder(self, tmp_path):
        log = write_log(tmp_path, LOG_HEADER + "GHOST,07:43\n" + "SPECTRE,07:44\n")
        result = process_lateness(log, MASTER)
        message = ingestion_rejection_message(result, MASTER)
        assert message is not None
        assert "GHOST" in message
        assert "SPECTRE" in message

    def test_header_mismatch(self, tmp_path):
        log = write_log(
            tmp_path,
            "Employee,Clock Time\n"
            + "ALICE,07:42\n"
            + "BOB,08:00\n",
        )
        result = process_lateness(log, MASTER)
        message = ingestion_rejection_message(result, MASTER)
        assert message is not None
        assert "Name" in message
        assert "Transaction Time" in message

    def test_all_times_unparseable(self, tmp_path):
        log = write_log(tmp_path, LOG_HEADER + "ALICE,7:42\n" + "BOB,bad\n")
        result = process_lateness(log, MASTER)
        message = ingestion_rejection_message(result, MASTER)
        assert message is not None
        assert "ALICE" in message
        assert "7:42" in message

    def test_empty_log_rejected(self, tmp_path):
        log = write_log(tmp_path, "")
        result = process_lateness(log, MASTER)
        message = ingestion_rejection_message(result, MASTER)
        assert message is not None

    def test_three_failure_causes_produce_distinct_messages(self, tmp_path):
        empty_log = write_log(tmp_path, "", "empty.csv")
        no_match_log = write_log(tmp_path, "Name,Transaction Time\nGHOST,07:43\n", "no-match.csv")
        unparseable_log = write_log(tmp_path, "Name,Transaction Time\nALICE,7:42\n", "unparseable.csv")

        messages = {
            ingestion_rejection_message(process_lateness(empty_log, MASTER), MASTER),
            ingestion_rejection_message(process_lateness(no_match_log, MASTER), MASTER),
            ingestion_rejection_message(process_lateness(unparseable_log, MASTER), MASTER),
            ingestion_rejection_message(process_lateness(empty_log, None), None),
        }
        assert len(messages) == 4


class TestBoardersToCsv:
    def test_header_and_rows_with_carried_points(self):
        boarders = {
            "ALICE": {"bed": "101", "frequency": 2, "total_minutes": 5, "total_points": 7},
            "BOB": {"bed": "102", "frequency": 1, "total_minutes": 19, "total_points": 20},
        }
        text = _boarders_to_csv(boarders)
        rows = list(csv.reader(io.StringIO(text)))

        assert rows[0] == ["Bed", "Name", "Frequency", "Total Minutes Late", "Total Points"]
        assert rows[1] == ["101", "ALICE", "2", "5", "7"]
        assert rows[2] == ["102", "BOB", "1", "19", "20"]

    def test_points_come_from_carried_value_not_recomputed(self):
        boarders = {
            "X": {"bed": "1", "frequency": 1, "total_minutes": 2, "total_points": 99},
        }
        rows = list(csv.reader(io.StringIO(_boarders_to_csv(boarders))))
        assert rows[1] == ["1", "X", "1", "2", "99"]

    def test_deterministic_ordering(self):
        boarders = {
            "BOB": {"bed": "102", "frequency": 1, "total_minutes": 19, "total_points": 20},
            "ALICE": {"bed": "101", "frequency": 2, "total_minutes": 5, "total_points": 7},
        }
        text = _boarders_to_csv(boarders)
        names = [row[1] for row in csv.reader(io.StringIO(text))][1:]
        assert names == ["ALICE", "BOB"]

    def test_download_and_export_identical_for_same_month(self, tmp_path):
        log = write_log(
            tmp_path,
            LOG_HEADER + "ALICE,07:42\n" + "BOB,08:00\n" + "GHOST,07:43\n",
        )
        result = process_lateness(log, MASTER)

        export_path = tmp_path / "export.csv"
        export_to_csv(str(export_path), result.boarders)
        with open(export_path, encoding="utf-8", newline="") as file:
            exported = file.read()

        saved_boarders = {
            name: {
                "bed": data["bed"],
                "frequency": data["frequency"],
                "total_minutes": data["total_minutes"],
                "total_points": data["total_points"],
            }
            for name, data in result.boarders.items()
        }
        downloaded = _boarders_to_csv(saved_boarders)

        assert downloaded == exported
        assert "GHOST" not in exported


class TestExportToCsv:
    def test_export_matches_shared_writer(self, tmp_path):
        boarders = {
            "ALICE": {"bed": "101", "frequency": 2, "total_minutes": 5, "total_points": 7},
            "BOB": {"bed": "102", "frequency": 1, "total_minutes": 19, "total_points": 20},
        }
        output = tmp_path / "report.csv"
        export_to_csv(str(output), boarders)

        with open(output, encoding="utf-8", newline="") as file:
            written = file.read()
        assert written == _boarders_to_csv(boarders)


def _boarders_to_csv(boarders):
    from parser import boarders_to_csv

    return boarders_to_csv(boarders)
