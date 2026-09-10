"""Monthly Log archive: every successful Import keeps the source CSV.

The archive makes the Monthly Reports rebuildable if the database is lost:
the backed-up CSVs re-import through the same ingestion path. Rejected
imports must leave nothing behind, matching the "database untouched"
guarantee.
"""

import io

import pytest

import app as app_module
from helpers import post_csrf


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
