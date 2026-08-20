import csv
import io

import pytest
from helpers import record
from records import Boarder, normalize_name

from parser import (
    cli_ingest,
    cli_main,
    export_to_csv,
    load_namelist,
    load_namelist_rows,
    master_list_to_csv,
    parse_namelist_stream,
    parse_time_seconds,
)


class TestLoadNamelist:
    def test_loads_valid_boarders(self, tmp_path):
        path = tmp_path / "namelist.csv"
        path.write_text("Name,Bed\nalice,101\nBOB,102\ncarol smith,103\n", encoding="utf-8")
        assert load_namelist(str(path)) == {
            "ALICE": Boarder(normalized_name="ALICE", display_name="alice", bed="101"),
            "BOB": Boarder(normalized_name="BOB", display_name="BOB", bed="102"),
            "CAROL SMITH": Boarder(normalized_name="CAROL SMITH", display_name="carol smith", bed="103"),
        }

    def test_missing_file_returns_none(self, tmp_path):
        assert load_namelist(str(tmp_path / "does-not-exist.csv")) is None

    def test_empty_master_returns_empty_dict(self, tmp_path):
        path = tmp_path / "namelist.csv"
        path.write_text("Name,Bed\n", encoding="utf-8")
        assert load_namelist(str(path)) == {}


class TestLoadNamelistRows:
    def test_preserves_display_case(self, tmp_path):
        path = tmp_path / "namelist.csv"
        path.write_text("Name,Bed\nAlice,101\nbob smith,102\n", encoding="utf-8")
        assert load_namelist_rows(str(path)) == [
            Boarder(normalized_name="ALICE", display_name="Alice", bed="101"),
            Boarder(normalized_name="BOB SMITH", display_name="bob smith", bed="102"),
        ]

    def test_missing_file_returns_none(self, tmp_path):
        assert load_namelist_rows(str(tmp_path / "does-not-exist.csv")) is None

    def test_empty_master_returns_empty_list(self, tmp_path):
        path = tmp_path / "namelist.csv"
        path.write_text("Name,Bed\n", encoding="utf-8")
        assert load_namelist_rows(str(path)) == []


class TestParseNamelistStream:
    def test_parses_stream_preserving_case(self):
        stream = io.StringIO("Name,Bed\nAlice,101\nbob smith,102\n")
        assert parse_namelist_stream(stream) == [
            Boarder(normalized_name="ALICE", display_name="Alice", bed="101"),
            Boarder(normalized_name="BOB SMITH", display_name="bob smith", bed="102"),
        ]

    def test_skips_rows_with_missing_name_or_bed(self):
        stream = io.StringIO("Name,Bed\nAlice,101\n,bad\nNoBed,\n")
        assert parse_namelist_stream(stream) == [
            Boarder(normalized_name="ALICE", display_name="Alice", bed="101")
        ]

    def test_empty_stream_returns_empty_list(self):
        assert parse_namelist_stream(io.StringIO("Name,Bed\n")) == []


class TestNormalizeName:
    def test_strips_and_uppercases(self):
        assert normalize_name("  carol smith  ") == "CAROL SMITH"

    def test_leaves_uppercase_untouched(self):
        assert normalize_name("CAROL") == "CAROL"


class TestMasterListToCsv:
    def test_header_and_rows(self):
        text = master_list_to_csv(
            [
                Boarder(normalized_name="ALICE", display_name="Alice", bed="601A"),
                Boarder(normalized_name="BOB", display_name="Bob", bed="601B"),
            ]
        )
        rows = list(csv.reader(io.StringIO(text)))
        assert rows[0] == ["Name", "Bed"]
        assert rows[1] == ["Alice", "601A"]
        assert rows[2] == ["Bob", "601B"]

    def test_deterministic_order(self):
        text = master_list_to_csv(
            [
                Boarder(normalized_name="BOB", display_name="Bob", bed="601B"),
                Boarder(normalized_name="ALICE", display_name="Alice", bed="601A"),
            ]
        )
        names = [row[0] for row in csv.reader(io.StringIO(text))][1:]
        assert names == ["Alice", "Bob"]

    def test_empty_list_writes_only_header(self):
        text = master_list_to_csv([])
        rows = list(csv.reader(io.StringIO(text)))
        assert rows == [["Name", "Bed"]]


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


class TestBoarderRecord:
    def test_exposes_named_fields(self):
        rec = record(name="ALICE", bed="101", frequency=2, total_minutes=5, total_points=7)
        assert rec.name == "ALICE"
        assert rec.bed == "101"
        assert rec.frequency == 2
        assert rec.total_minutes == 5
        assert rec.total_points == 7

    def test_exposes_canonical_display_name(self):
        rec = record(name="ALICE", display_name="Alicia", bed="101")
        assert rec.display_name == "Alicia"

    def test_display_name_defaults_to_title_case(self):
        assert record("ALICE").display_name == "Alice"
        assert record("CAROL SMITH").display_name == "Carol Smith"


class TestBoardersToCsv:
    def test_header_and_rows_with_carried_points(self):
        boarders = [
            record("ALICE", bed="101", frequency=2, total_minutes=5, total_points=7),
            record("BOB", bed="102", frequency=1, total_minutes=19, total_points=20),
        ]
        rows = list(csv.reader(io.StringIO(boarders_to_csv(boarders))))

        assert rows[0] == ["Bed", "Name", "Frequency", "Total Minutes Late", "Total Points"]
        assert rows[1] == ["101", "Alice", "2", "5", "7"]
        assert rows[2] == ["102", "Bob", "1", "19", "20"]

    def test_name_column_uses_canonical_display_name(self):
        boarders = [record("ALICE", display_name="Alicia", bed="101")]
        rows = list(csv.reader(io.StringIO(boarders_to_csv(boarders))))
        assert rows[1] == ["101", "Alicia", "0", "0", "0"]

    def test_points_come_from_carried_value_not_recomputed(self):
        boarders = [record("X", bed="1", frequency=1, total_minutes=2, total_points=99)]
        rows = list(csv.reader(io.StringIO(boarders_to_csv(boarders))))
        assert rows[1] == ["1", "X", "1", "2", "99"]

    def test_deterministic_ordering(self):
        boarders = [
            record("BOB", bed="102", frequency=1, total_minutes=19, total_points=20),
            record("ALICE", bed="101", frequency=2, total_minutes=5, total_points=7),
        ]
        text = boarders_to_csv(boarders)
        names = [row[1] for row in csv.reader(io.StringIO(text))][1:]
        assert names == ["Alice", "Bob"]

    def test_orders_beds_by_number_then_suffix(self):
        boarders = [
            record("A", bed="10"),
            record("B", bed="9A"),
            record("C", bed="101A"),
            record("D", bed="101"),
        ]
        text = boarders_to_csv(boarders)
        names = [row[1] for row in csv.reader(io.StringIO(text))][1:]
        assert names == ["B", "A", "D", "C"]


class TestExportToCsv:
    def test_export_matches_shared_writer(self, tmp_path):
        boarders = [
            record("ALICE", bed="101", frequency=2, total_minutes=5, total_points=7),
            record("BOB", bed="102", frequency=1, total_minutes=19, total_points=20),
        ]
        output = tmp_path / "report.csv"
        export_to_csv(str(output), boarders)

        with open(output, encoding="utf-8", newline="") as file:
            written = file.read()
        assert written == boarders_to_csv(boarders)

    def test_export_download_identical_for_same_records(self, tmp_path):
        boarders = [
            record("ALICE", bed="101", frequency=2, total_minutes=5, total_points=7),
            record("BOB", bed="102", frequency=1, total_minutes=19, total_points=20),
        ]
        export_path = tmp_path / "export.csv"
        export_to_csv(str(export_path), boarders)
        with open(export_path, encoding="utf-8", newline="") as file:
            exported = file.read()

        assert exported == boarders_to_csv(boarders)


class TestCli:
    def test_cli_ingest_shares_ingestion_path(self, tmp_path):
        namelist = tmp_path / "namelist.csv"
        namelist.write_text("Bed,Name\n601A,alice\n602A,Bob\n", encoding="utf-8")
        master = load_namelist(str(namelist))
        log = tmp_path / "log.csv"
        log.write_text(
            "Name,Transaction Time\nALICE,07:42\nGHOST,07:43\n", encoding="utf-8"
        )

        outcome = cli_ingest(str(log), master)

        from parser import SavedOutcome

        assert isinstance(outcome, SavedOutcome)
        assert outcome.diagnostics.rows_read == 2
        assert outcome.diagnostics.matched_rows == 1
        assert outcome.diagnostics.unmatched_names == ["GHOST"]

    def test_cli_main_writes_report_via_shared_writer(self, tmp_path, capsys):
        namelist = tmp_path / "namelist.csv"
        namelist.write_text("Bed,Name\n601A,alice\n", encoding="utf-8")
        log = tmp_path / "log.csv"
        log.write_text("Name,Transaction Time\nALICE,07:42\n", encoding="utf-8")
        output = tmp_path / "report.csv"

        code = cli_main(str(namelist), str(log), str(output))

        assert code == 0
        with open(output, encoding="utf-8", newline="") as file:
            written = file.read()
        assert written == boarders_to_csv(
            [record("ALICE", bed="601A", frequency=1, total_minutes=1, total_points=2, display_name="alice")]
        )
        captured = capsys.readouterr()
        assert "Read 1 log rows, matched 1." in captured.out
        assert "Monthly report saved for" in captured.out
        assert "with 1 boarder recorded as late" in captured.out

    def test_cli_prints_saved_message_with_diagnostics(self, tmp_path, capsys):
        namelist = tmp_path / "namelist.csv"
        namelist.write_text("Bed,Name\n601A,alice\n", encoding="utf-8")
        log = tmp_path / "log.csv"
        log.write_text(
            "Name,Transaction Time\nALICE,07:42\nGHOST,07:43\nALICE,7:45\n", encoding="utf-8"
        )
        output = tmp_path / "report.csv"

        code = cli_main(str(namelist), str(log), str(output))

        assert code == 0
        captured = capsys.readouterr()
        assert "Unmatched names: GHOST." in captured.out
        assert "Unparseable times: ALICE ('7:45')." in captured.out
        assert "Wrote report to" in captured.out
        assert "Generated" not in captured.out

    def test_cli_main_rejects_missing_namelist(self, tmp_path, capsys):
        missing = tmp_path / "nope.csv"
        code = cli_main(str(missing), "whatever.csv", "out.csv")
        assert code == 1
        captured = capsys.readouterr().out
        assert str(missing) in captured
        assert "not found in the project root." in captured

    def test_cli_main_returns_1_for_missing_log(self, tmp_path, capsys):
        namelist = tmp_path / "namelist.csv"
        namelist.write_text("Bed,Name\n601A,alice\n", encoding="utf-8")
        missing_log = tmp_path / "missing.csv"
        code = cli_main(str(namelist), str(missing_log), "out.csv")
        assert code == 1
        captured = capsys.readouterr().out
        assert str(missing_log) in captured
        assert "not found in the project root." in captured


def boarders_to_csv(boarders):
    from parser import boarders_to_csv as writer

    return writer(boarders)
