"""NAS viability — connection lock-wait and journal policy (ticket #141).

Effective database settings asserted through the app's central connection
helper: every connection must wait on locks and the store must stay off WAL.
"""

import pytest

import app as app_module


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
