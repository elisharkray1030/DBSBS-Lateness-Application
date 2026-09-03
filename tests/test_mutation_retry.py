"""NAS viability — bounded mutation retry (#143).

Mutations tolerate brief lock contention via bounded retry with backoff;
sustained contention surfaces a clean staff-facing error with the database
unchanged. Concurrency is simulated with injected faults, not real
multi-process contention.
"""

import sqlite3

import pytest

import app as app_module
import storage as storage_module
from tests.helpers import record


def test_brief_contention_on_add_succeeds(fresh_client, monkeypatch):
    real_add = storage_module.add_boarder
    calls = {"count": 0}

    def flaky_add(conn, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_add(conn, *args, **kwargs)

    monkeypatch.setattr(storage_module, "add_boarder", flaky_add)

    response = fresh_client.post(
        "/boarders/add", data={"name": "Cara", "bed": "602A"}
    )

    assert response.status_code == 302
    assert calls["count"] == 2
    with app_module.connect(read_only=True) as conn:
        assert storage_module.boarder_exists(conn, "CARA")


def _always_locked(calls):
    def fault(*args, **kwargs):
        calls["count"] += 1
        raise sqlite3.OperationalError("database is locked")

    return fault


def test_sustained_contention_on_add_fails_clean(fresh_client, monkeypatch):
    calls = {"count": 0}
    monkeypatch.setattr(
        storage_module, "add_boarder", _always_locked(calls)
    )

    response = fresh_client.post(
        "/boarders/add", data={"name": "Cara", "bed": "602A"}
    )

    assert response.status_code == 200
    assert calls["count"] == 3
    html = response.get_data(as_text=True)
    assert "database is busy" in html
    assert "Nothing was changed" in html
    with app_module.connect(read_only=True) as conn:
        assert not storage_module.boarder_exists(conn, "CARA")


def test_sustained_contention_on_month_delete_is_503(fresh_client, monkeypatch):
    with app_module.connect() as conn:
        storage_module.save_month(
            conn, [record("ALICE", "601A", 1, 2, 3)], "2026-03"
        )
    calls = {"count": 0}
    monkeypatch.setattr(
        storage_module, "delete_month", _always_locked(calls)
    )

    response = fresh_client.delete("/delete_month/2026-03")

    assert response.status_code == 503
    assert calls["count"] == 3
    assert "busy" in response.get_json()["error"]
    with app_module.connect(read_only=True) as conn:
        assert storage_module.get_month_report(conn, "2026-03")


def test_retried_assignment_creates_no_duplicates(fresh_client, monkeypatch):
    with app_module.connect() as conn:
        storage_module.save_month(
            conn, [record("ALICE", "601A", 2, 5, 7)], "2026-03"
        )
    real_assign = app_module.assign_batch
    calls = {"count": 0}

    def flaky_assign(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_assign(*args, **kwargs)

    monkeypatch.setattr(app_module, "assign_batch", flaky_assign)

    response = fresh_client.post(
        "/assign/2026-03",
        data={"deadline": "2026-04-10", "assign": ["ALICE"]},
    )

    assert response.status_code == 302
    assert calls["count"] == 2
    with app_module.connect(read_only=True) as conn:
        rows = storage_module.list_punishments(conn, statuses=("assigned",))
        assert len(rows) == 1
        assert rows[0].normalized_name == "ALICE"


def test_sustained_contention_on_transition_flashes_and_redirects(
    fresh_client, monkeypatch
):
    with app_module.connect() as conn:
        storage_module.save_month(
            conn, [record("ALICE", "601A", 2, 5, 7)], "2026-03"
        )
        stored = storage_module.assign_punishments(
            conn,
            "2026-03",
            [record("ALICE", "601A", 2, 5, 7)],
            deadline="2026-04-10",
            assigned_at="2026-04-01T09:00:00+00:00",
        )
        punishment_id = storage_module.list_punishments(conn)[0].id
    calls = {"count": 0}
    monkeypatch.setattr(
        app_module, "transition", _always_locked(calls)
    )

    response = fresh_client.post(
        f"/punishment/{punishment_id}/transition", data={"to": "submitted"}
    )

    assert response.status_code == 302
    assert calls["count"] == 3
    assert "/consequences" in response.headers["Location"]
    with app_module.connect(read_only=True) as conn:
        rows = storage_module.list_punishments(conn, statuses=("assigned",))
        assert len(rows) == 1
