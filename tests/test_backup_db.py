"""Backup script: a run must capture the database and the archived Monthly Logs."""

import sqlite3

import pytest

import backup_db
import storage
from records import Boarder


def _seed_db(path):
    with sqlite3.connect(path) as conn:
        storage.create_schema(conn)
        storage.replace_boarders(conn, [Boarder("ALICE", "Alice", "601A")])


def test_backup_copies_database_with_its_data(tmp_path):
    db_path = tmp_path / "live.db"
    _seed_db(db_path)
    dest = tmp_path / "backups"

    folder = backup_db.backup(
        dest,
        db_path=str(db_path),
        log_archive_dir=str(tmp_path / "logs"),
        keep=7,
        timestamp="20260910-120000",
    )

    copied = folder / "lateness_history.db"
    assert copied.exists()
    with sqlite3.connect(copied) as conn:
        assert [b.normalized_name for b in storage.list_boarders(conn)] == ["ALICE"]


def test_backup_copies_the_monthly_log_archive(tmp_path):
    db_path = tmp_path / "live.db"
    _seed_db(db_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    body = b"Name,Transaction Time\nALICE,07:42\n"
    (logs / "2026-03.csv").write_bytes(body)
    dest = tmp_path / "backups"

    folder = backup_db.backup(
        dest,
        db_path=str(db_path),
        log_archive_dir=str(logs),
        keep=7,
        timestamp="20260910-120000",
    )

    assert (folder / "logs" / "2026-03.csv").read_bytes() == body


def test_backup_copies_the_master_list_snapshots(tmp_path):
    db_path = tmp_path / "live.db"
    _seed_db(db_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    snapshot = b"Name,Bed\r\nALICE,601A\r\n"
    (logs / "namelist-2026-03.csv").write_bytes(snapshot)
    dest = tmp_path / "backups"

    folder = backup_db.backup(
        dest,
        db_path=str(db_path),
        log_archive_dir=str(logs),
        keep=7,
        timestamp="20260910-120000",
    )

    assert (folder / "logs" / "namelist-2026-03.csv").read_bytes() == snapshot


def test_backup_copies_only_csv_from_the_archive(tmp_path):
    db_path = tmp_path / "live.db"
    _seed_db(db_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-03.csv").write_bytes(b"Name,Transaction Time\nALICE,07:42\n")
    (logs / "notes.txt").write_text("not source data")
    dest = tmp_path / "backups"

    folder = backup_db.backup(
        dest,
        db_path=str(db_path),
        log_archive_dir=str(logs),
        keep=7,
        timestamp="20260910-120000",
    )

    assert (folder / "logs" / "2026-03.csv").exists()
    assert not (folder / "logs" / "notes.txt").exists()


def test_backup_retention_keeps_the_newest_folders(tmp_path):
    db_path = tmp_path / "live.db"
    _seed_db(db_path)
    dest = tmp_path / "backups"

    for timestamp in ("20260910-120000", "20260910-130000", "20260910-140000"):
        backup_db.backup(
            dest,
            db_path=str(db_path),
            log_archive_dir=str(tmp_path / "logs"),
            keep=2,
            timestamp=timestamp,
        )

    remaining = sorted(path.name for path in dest.iterdir())
    assert remaining == ["lateness-20260910-130000", "lateness-20260910-140000"]


def test_backup_refuses_a_missing_database(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_db.backup(
            tmp_path / "backups",
            db_path=str(tmp_path / "missing.db"),
            log_archive_dir=str(tmp_path / "logs"),
            keep=7,
            timestamp="20260910-120000",
        )


def test_backup_is_a_point_in_time_copy(tmp_path):
    db_path = tmp_path / "live.db"
    _seed_db(db_path)
    dest = tmp_path / "backups"

    folder = backup_db.backup(
        dest,
        db_path=str(db_path),
        log_archive_dir=str(tmp_path / "logs"),
        keep=7,
        timestamp="20260910-120000",
    )
    with sqlite3.connect(db_path) as conn:
        storage.replace_boarders(
            conn, [Boarder("ALICE", "Alice", "601A"), Boarder("BOB", "Bob", "601B")]
        )

    with sqlite3.connect(folder / "lateness_history.db") as conn:
        assert [b.normalized_name for b in storage.list_boarders(conn)] == ["ALICE"]


def test_backup_rejects_non_positive_keep_before_writing(tmp_path):
    db_path = tmp_path / "live.db"
    _seed_db(db_path)
    dest = tmp_path / "backups"

    with pytest.raises(ValueError):
        backup_db.backup(
            dest,
            db_path=str(db_path),
            log_archive_dir=str(tmp_path / "logs"),
            keep=0,
            timestamp="20260910-120000",
        )

    assert not dest.exists()


def test_backup_leaves_no_folder_when_a_copy_fails(tmp_path):
    db_path = tmp_path / "live.db"
    _seed_db(db_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    # A directory named like a Monthly Log makes the archive copy raise, which
    # must not leave a partial folder that retention would count as restorable.
    (logs / "2026-03.csv").mkdir()
    dest = tmp_path / "backups"

    with pytest.raises(OSError):
        backup_db.backup(
            dest,
            db_path=str(db_path),
            log_archive_dir=str(logs),
            keep=7,
            timestamp="20260910-120000",
        )

    assert not list(dest.glob("lateness-*"))


def test_cli_runs_one_backup_into_the_destination(tmp_path):
    db_path = tmp_path / "live.db"
    _seed_db(db_path)
    dest = tmp_path / "backups"

    exit_code = backup_db.main(
        [
            "--dest", str(dest),
            "--db", str(db_path),
            "--logs", str(tmp_path / "logs"),
            "--keep", "3",
        ]
    )

    assert exit_code == 0
    folders = [path for path in dest.iterdir() if path.is_dir()]
    assert len(folders) == 1
    assert (folders[0] / "lateness_history.db").exists()
