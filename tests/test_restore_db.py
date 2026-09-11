"""Restore script: a run must put a backup's database and archive back in place."""

import sqlite3

import pytest

import backup_db
import restore_db
import storage
from records import Boarder


def _seed_db(path, names):
    with sqlite3.connect(path) as conn:
        storage.create_schema(conn)
        storage.replace_boarders(
            conn, [Boarder(name, name, f"60{i}A") for i, name in enumerate(names, 1)]
        )


def _backup_folder(tmp_path, names=("BOB",), with_logs=True):
    db_path = tmp_path / "snapshot.db"
    _seed_db(db_path, names)
    logs = tmp_path / "snapshot-logs"
    logs.mkdir()
    if with_logs:
        (logs / "2026-03.csv").write_bytes(b"Name,Transaction Time\nBOB,07:42\n")
    dest = tmp_path / "backups"
    return backup_db.backup(
        dest,
        db_path=str(db_path),
        log_archive_dir=str(logs),
        keep=7,
        timestamp="20260910-120000",
    )


def test_restore_replaces_the_database_with_the_backup(tmp_path):
    source = _backup_folder(tmp_path, names=("BOB",))
    live = tmp_path / "live.db"
    _seed_db(live, ("ALICE",))

    restore_db.restore(
        source, db_path=str(live), log_archive_dir=str(tmp_path / "logs"), force=True
    )

    with sqlite3.connect(live) as conn:
        assert [b.normalized_name for b in storage.list_boarders(conn)] == ["BOB"]


def test_restore_copies_the_monthly_log_archive(tmp_path):
    source = _backup_folder(tmp_path, with_logs=True)
    live_logs = tmp_path / "live-logs"

    restore_db.restore(
        source, db_path=str(tmp_path / "live.db"), log_archive_dir=str(live_logs), force=True
    )

    assert (live_logs / "2026-03.csv").read_bytes() == b"Name,Transaction Time\nBOB,07:42\n"


def test_restore_copies_only_csv_from_the_backup_archive(tmp_path):
    source = _backup_folder(tmp_path, with_logs=True)
    (source / "logs" / "notes.txt").write_text("not source data")
    live_logs = tmp_path / "live-logs"

    restore_db.restore(
        source, db_path=str(tmp_path / "live.db"), log_archive_dir=str(live_logs), force=True
    )

    assert (live_logs / "2026-03.csv").exists()
    assert not (live_logs / "notes.txt").exists()


def test_restore_refuses_to_overwrite_a_live_database_without_force(tmp_path):
    source = _backup_folder(tmp_path, names=("BOB",))
    live = tmp_path / "live.db"
    _seed_db(live, ("ALICE",))

    with pytest.raises(FileExistsError):
        restore_db.restore(
            source, db_path=str(live), log_archive_dir=str(tmp_path / "logs")
        )

    with sqlite3.connect(live) as conn:
        assert [b.normalized_name for b in storage.list_boarders(conn)] == ["ALICE"]


def test_restore_refuses_a_backup_without_a_database(tmp_path):
    empty = tmp_path / "lateness-empty"
    empty.mkdir()

    with pytest.raises(FileNotFoundError):
        restore_db.restore(
            empty,
            db_path=str(tmp_path / "live.db"),
            log_archive_dir=str(tmp_path / "logs"),
            force=True,
        )


def test_restore_clears_stale_journal_files(tmp_path):
    source = _backup_folder(tmp_path, names=("BOB",))
    live = tmp_path / "live.db"
    _seed_db(live, ("ALICE",))
    for suffix in ("-journal", "-wal", "-shm"):
        (tmp_path / f"live.db{suffix}").write_bytes(b"stale")

    restore_db.restore(
        source, db_path=str(live), log_archive_dir=str(tmp_path / "logs"), force=True
    )

    for suffix in ("-journal", "-wal", "-shm"):
        assert not (tmp_path / f"live.db{suffix}").exists()


def test_restore_leaves_the_database_untouched_when_the_archive_fails(tmp_path):
    source = _backup_folder(tmp_path, names=("BOB",))
    # A directory named like a Monthly Log makes the archive copy raise before
    # the database is swapped, so the live database must be untouched.
    (source / "logs" / "2026-04.csv").mkdir()
    live = tmp_path / "live.db"
    _seed_db(live, ("ALICE",))

    with pytest.raises(OSError):
        restore_db.restore(
            source, db_path=str(live), log_archive_dir=str(tmp_path / "logs"), force=True
        )

    with sqlite3.connect(live) as conn:
        assert [b.normalized_name for b in storage.list_boarders(conn)] == ["ALICE"]


def test_restore_into_a_fresh_volume_needs_no_force(tmp_path):
    source = _backup_folder(tmp_path, names=("BOB",))
    live = tmp_path / "fresh" / "live.db"

    restore_db.restore(
        source, db_path=str(live), log_archive_dir=str(tmp_path / "logs")
    )

    with sqlite3.connect(live) as conn:
        assert [b.normalized_name for b in storage.list_boarders(conn)] == ["BOB"]


def test_restore_round_trips_a_database_through_backup(tmp_path):
    live = tmp_path / "live.db"
    _seed_db(live, ("ALICE", "BOB"))
    folder = backup_db.backup(
        tmp_path / "backups",
        db_path=str(live),
        log_archive_dir=str(tmp_path / "logs"),
        keep=7,
        timestamp="20260910-120000",
    )

    _seed_db(live, ("CHANGED",))
    restore_db.restore(
        folder, db_path=str(live), log_archive_dir=str(tmp_path / "logs"), force=True
    )

    with sqlite3.connect(live) as conn:
        assert [b.normalized_name for b in storage.list_boarders(conn)] == ["ALICE", "BOB"]


def test_cli_runs_one_restore(tmp_path):
    source = _backup_folder(tmp_path, names=("BOB",))
    live = tmp_path / "live.db"
    _seed_db(live, ("ALICE",))

    exit_code = restore_db.main(
        [
            "--from", str(source),
            "--db", str(live),
            "--logs", str(tmp_path / "logs"),
            "--force",
        ]
    )

    assert exit_code == 0
    with sqlite3.connect(live) as conn:
        assert [b.normalized_name for b in storage.list_boarders(conn)] == ["BOB"]
