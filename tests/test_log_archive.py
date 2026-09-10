"""Monthly Log Archive: every successful Import keeps its Monthly Log and snapshot.

The archive makes the Monthly Reports rebuildable if the database is lost:
the archived Monthly Logs re-import through the same ingestion path, and the
per-month Master List snapshot records the roster that produced each report.
Rejected imports must leave nothing behind, matching the "database untouched"
guarantee.
"""

import io
import os
import threading

import pytest

import app as app_module
import storage
from helpers import post_csrf
from records import Boarder


@pytest.fixture
def archive_client(tmp_path):
    """A client whose database and Monthly Log archive both live in tmp."""
    archive_dir = tmp_path / "logs"
    namelist = tmp_path / "namelist.csv"
    namelist.write_text("Bed,Name\n601A,ALICE\n601B,BOB\n", encoding="utf-8")
    application = app_module.create_app(
        {
            "DB_PATH": str(tmp_path / "archive.db"),
            "NAMELIST_PATH": str(namelist),
            "LOG_ARCHIVE_DIR": str(archive_dir),
            "SECRET_KEY": "test-secret-key",
            "TESTING": True,
        }
    )
    pushed = application.app_context()
    pushed.push()
    app_module.init_db()
    yield application.test_client(), archive_dir
    pushed.pop()


def _import(client, month, body):
    return post_csrf(
        client,
        "/",
        data={"report_month": month, "log_file": (io.BytesIO(body), "log.csv")},
        content_type="multipart/form-data",
    )


def test_successful_import_archives_the_monthly_log(archive_client):
    client, archive_dir = archive_client
    body = b"Name,Transaction Time\nALICE,07:42\n"

    response = _import(client, "2026-03", body)

    assert response.status_code == 302
    assert (archive_dir / "2026-03.csv").read_bytes() == body
    assert not list(archive_dir.glob("*.tmp"))


def test_rejected_import_archives_nothing(archive_client):
    client, archive_dir = archive_client
    body = b"Name,Transaction Time\nGHOST,07:42\n"

    response = _import(client, "2026-04", body)

    assert response.status_code == 200
    assert not (archive_dir / "2026-04.csv").exists()


def test_reimport_overwrites_the_archived_log(archive_client):
    client, archive_dir = archive_client
    first = b"Name,Transaction Time\nALICE,07:42\n"
    second = b"Name,Transaction Time\nBOB,07:55\n"

    _import(client, "2026-05", first)
    _import(client, "2026-05", second)

    assert (archive_dir / "2026-05.csv").read_bytes() == second


def test_successful_import_archives_a_master_list_snapshot(archive_client):
    client, archive_dir = archive_client
    body = b"Name,Transaction Time\nALICE,07:42\n"

    _import(client, "2026-03", body)

    snapshot = (archive_dir / "namelist-2026-03.csv").read_bytes()
    assert snapshot == b"Name,Bed\r\nALICE,601A\r\nBOB,601B\r\n"


def test_reimport_overwrites_the_master_list_snapshot(archive_client):
    client, archive_dir = archive_client
    body = b"Name,Transaction Time\nALICE,07:42\n"

    _import(client, "2026-05", body)
    with app_module.connect() as conn:
        storage.replace_boarders(conn, [Boarder("ALICE", "ALICE", "699Z")])
    _import(client, "2026-05", body)

    assert (archive_dir / "namelist-2026-05.csv").read_bytes() == (
        b"Name,Bed\r\nALICE,699Z\r\n"
    )


def test_concurrent_atomic_writes_do_not_collide(tmp_path, monkeypatch):
    destination = tmp_path / "2026-03.csv"
    payloads = [b"a" * 4096, b"b" * 4096]
    barrier = threading.Barrier(2)
    real_replace = os.replace

    def synchronized_replace(source, target):
        # Both writers reach their write-then-rename only after the other has
        # staged its bytes, so a shared staging path would be clobbered here.
        barrier.wait()
        real_replace(source, target)

    monkeypatch.setattr(app_module.os, "replace", synchronized_replace)
    errors = []

    def write(payload):
        try:
            app_module._write_bytes_atomically(destination, payload)
        except BaseException as exc:  # noqa: BLE001 - surfaced through `errors`
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(payload,)) for payload in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert destination.read_bytes() in payloads
    assert not list(tmp_path.glob("*.tmp"))


def test_import_saves_and_warns_when_archive_fails(tmp_path):
    namelist = tmp_path / "namelist.csv"
    namelist.write_text("Bed,Name\n601A,ALICE\n", encoding="utf-8")
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    application = app_module.create_app(
        {
            "DB_PATH": str(tmp_path / "broken.db"),
            "NAMELIST_PATH": str(namelist),
            "LOG_ARCHIVE_DIR": str(blocker / "logs"),
            "SECRET_KEY": "test-secret-key",
            "TESTING": True,
        }
    )
    pushed = application.app_context()
    pushed.push()
    app_module.init_db()
    try:
        client = application.test_client()
        body = b"Name,Transaction Time\nALICE,07:42\n"
        response = post_csrf(
            client,
            "/",
            data={"report_month": "2026-06", "log_file": (io.BytesIO(body), "log.csv")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        html = response.get_data(as_text=True)
        assert "Monthly report saved" in html
        assert "could not be archived" in html
        with app_module.connect(read_only=True) as conn:
            assert storage.get_month_report(conn, "2026-06")
    finally:
        pushed.pop()
