"""NAS viability — connection lock-wait, journal policy (#141) and
read-only readers (#142).

Effective database settings asserted through the app's central connection
helper: every connection must wait on locks and the store must stay off WAL.
Pure-read surfaces must serve through read-only connections so readers never
contend for the write lock.
"""

import sqlite3

import pytest

import app as app_module
import storage
from tests.helpers import record


@pytest.fixture
def file_db(tmp_path, monkeypatch):
    db_path = tmp_path / "nas.db"
    monkeypatch.setattr(app_module, "DB_PATH", str(db_path))
    return db_path


def test_every_connection_waits_on_locks(file_db):
    with app_module.connect() as conn:
        (timeout,) = conn.execute("PRAGMA busy_timeout").fetchone()
    assert timeout == 30000


def test_journal_stays_rollback_never_wal(file_db):
    with app_module.connect() as conn:
        (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
    assert mode == "delete"


def test_read_only_connection_carries_lock_wait(file_db):
    with app_module.connect() as conn:
        conn.execute("CREATE TABLE t (a TEXT)")
        conn.commit()
    with app_module.connect(read_only=True) as conn:
        (timeout,) = conn.execute("PRAGMA busy_timeout").fetchone()
    assert timeout == 30000


def test_write_through_read_only_is_refused(file_db):
    with app_module.connect() as conn:
        conn.execute("CREATE TABLE t (a TEXT)")
        conn.commit()
    with app_module.connect(read_only=True) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE u (a TEXT)")


@pytest.fixture
def read_client(tmp_path, monkeypatch):
    """Flask client over a throwaway file DB with one saved Monthly Report."""
    import app as app_module

    db_path = tmp_path / "readers.db"
    monkeypatch.setattr(app_module, "DB_PATH", str(db_path))
    namelist = tmp_path / "namelist.csv"
    namelist.write_text("Bed,Name\n601A,ALICE\n601B,BOB\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "NAMELIST_PATH", str(namelist))
    app_module.init_db()
    with app_module.connect() as conn:
        storage.save_month(
            conn, [record("ALICE", "601A", 2, 5, 7)], "2026-03"
        )
        conn.commit()
    return app_module.app.test_client()


def test_month_report_loads_while_write_holds_db(read_client):
    with app_module.connect() as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            response = read_client.get("/api/month/2026-03")
        finally:
            writer.rollback()
    assert response.status_code == 200
    assert response.get_json()["month"] == "2026-03"


def test_house_dashboard_loads_while_write_holds_db(read_client):
    with app_module.connect() as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            response = read_client.get("/statistics")
        finally:
            writer.rollback()
    assert response.status_code == 200


def test_pure_read_routes_open_read_only(read_client, monkeypatch):
    calls = []
    original = app_module.connect

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(app_module, "connect", spy)
    for path in (
        "/boarders",
        "/boarders/export",
        "/api/month/2026-03",
        "/download_month/2026-03",
        "/consequences",
        "/statistics",
        "/boarder/ALICE",
    ):
        calls.clear()
        response = read_client.get(path)
        assert response.status_code == 200, path
        assert calls, f"no DB connection recorded for {path}"
        assert all(call.get("read_only") is True for call in calls), path
