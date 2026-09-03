"""CSRF protection (#145-#147).

Seam: HTTP via Flask test_client + application factory config.
"""

import re

import pytest

import app as app_module
import storage


class TestSecretKeyHardFail:
    def test_missing_secret_aborts_startup(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(SystemExit, match="SECRET_KEY"):
            app_module.create_app({"DB_PATH": str(tmp_path / "x.db")})

    def test_empty_secret_aborts_startup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "")
        with pytest.raises(SystemExit, match="SECRET_KEY"):
            app_module.create_app({"DB_PATH": str(tmp_path / "x.db")})

    def test_empty_inline_secret_aborts_even_with_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "env-secret-value")
        with pytest.raises(SystemExit, match="SECRET_KEY"):
            app_module.create_app(
                {"DB_PATH": str(tmp_path / "x.db"), "SECRET_KEY": ""}
            )

    def test_env_secret_boots(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "env-secret-value")
        application = app_module.create_app({"DB_PATH": str(tmp_path / "x.db")})
        assert application.secret_key == "env-secret-value"

    def test_inline_secret_boots(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        application = app_module.create_app(
            {"DB_PATH": str(tmp_path / "x.db"), "SECRET_KEY": "inline-secret"}
        )
        assert application.secret_key == "inline-secret"


class TestSessionCookieFlags:
    def test_lan_cookie_flags(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "cookie-secret")
        application = app_module.create_app({"DB_PATH": str(tmp_path / "x.db")})
        assert application.config["SESSION_COOKIE_HTTPONLY"] is True
        assert application.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert application.config["SESSION_COOKIE_SECURE"] is False


def _bare_csrf_client(tmp_path):
    """Isolated app + client over a bare store (unlike the shared fixture,
    which seeds a fixed Master List, the matrix tests own their seed)."""
    import app as app_module

    db_path = tmp_path / "csrf.db"
    namelist = tmp_path / "namelist.csv"
    namelist.write_text("Bed,Name\n601A,ALICE\n", encoding="utf-8")
    application = app_module.create_app(
        {
            "DB_PATH": str(db_path),
            "NAMELIST_PATH": str(namelist),
            "SECRET_KEY": "csrf-test-secret",
            "TESTING": True,
        }
    )
    pushed = application.app_context()
    pushed.push()
    app_module.init_db()
    client = application.test_client()
    client.get("/")
    return application, client, pushed


def _session_token(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token")


class TestHtmlFormCsrf:
    def test_token_seeded_and_rendered(self, tmp_path):
        _, client, pushed = _bare_csrf_client(tmp_path)
        try:
            token = _session_token(client)
            assert token, "GET should seed a session CSRF token"
            html = client.get("/boarders").get_data(as_text=True)
            assert 'name="csrf_token"' in html
            assert token in html
        finally:
            pushed.pop()

    def test_add_boarder_without_token_rejected(self, tmp_path):
        application, client, pushed = _bare_csrf_client(tmp_path)
        try:
            with application.app_context():
                with app_module.connect() as conn:
                    before = len(storage.list_boarders(conn))
            response = client.post(
                "/boarders/add", data={"name": "Carol", "bed": "602A"}
            )
            assert response.status_code == 403
            with application.app_context():
                with app_module.connect() as conn:
                    names = [b.display_name for b in storage.list_boarders(conn)]
            assert "Carol" not in names
        finally:
            pushed.pop()

    def test_add_boarder_with_wrong_token_rejected(self, tmp_path):
        application, client, pushed = _bare_csrf_client(tmp_path)
        try:
            response = client.post(
                "/boarders/add",
                data={"name": "Carol", "bed": "602A", "csrf_token": "wrong"},
            )
            assert response.status_code == 403
            with application.app_context():
                with app_module.connect() as conn:
                    names = [b.display_name for b in storage.list_boarders(conn)]
            assert "Carol" not in names
        finally:
            pushed.pop()

    def test_add_boarder_with_token_succeeds(self, tmp_path):
        _, client, pushed = _bare_csrf_client(tmp_path)
        try:
            token = _session_token(client)
            response = client.post(
                "/boarders/add",
                data={"name": "Carol", "bed": "602A", "csrf_token": token},
            )
            assert response.status_code in (200, 302)
            html = client.get("/boarders").get_data(as_text=True)
            assert "Carol" in html
        finally:
            pushed.pop()

    def test_master_list_import_with_token_succeeds(self, tmp_path):
        import io

        _, client, pushed = _bare_csrf_client(tmp_path)
        try:
            token = _session_token(client)
            payload = "Name,Bed\nCAROL,602A\n"
            response = client.post(
                "/boarders/import",
                data={"csrf_token": token,
                      "boarder_csv": (io.BytesIO(payload.encode()), "m.csv")},
                content_type="multipart/form-data",
            )
            assert response.status_code in (200, 302)
            html = client.get("/boarders").get_data(as_text=True)
            assert "CAROL" in html or "Carol" in html
        finally:
            pushed.pop()

    def test_rejected_form_renders_error_page(self, tmp_path):
        _, client, pushed = _bare_csrf_client(tmp_path)
        try:
            response = client.post(
                "/boarders/add", data={"name": "Carol", "bed": "602A"}
            )
            assert response.status_code == 403
            html = response.get_data(as_text=True)
            assert "banner-error" in html
            assert "Invalid CSRF token" in html
            assert "reload" in html.lower()
        finally:
            pushed.pop()

    def test_rejected_form_flash_visible_on_next_visit(self, tmp_path):
        _, client, pushed = _bare_csrf_client(tmp_path)
        try:
            assert client.post(
                "/boarders/add", data={"name": "Carol", "bed": "602A"}
            ).status_code == 403
            html = client.get("/").get_data(as_text=True)
            assert "Invalid CSRF token" in html
        finally:
            pushed.pop()

class TestFetchCsrf:
    def _boarder_id(self, client):
        html = client.get("/boarders").get_data(as_text=True)
        match = re.search(r'data-boarder-id="(\d+)"', html)
        assert match, "no boarder row rendered"
        return int(match.group(1))

    def test_api_patch_without_header_rejected(self, tmp_path):
        application, client, pushed = _bare_csrf_client(tmp_path)
        try:
            bid = self._boarder_id(client)
            response = client.patch(
                f"/api/boarders/{bid}", json={"name": "Alice", "bed": "602A"}
            )
            assert response.status_code == 403
            assert response.get_json()["ok"] is False
            with application.app_context():
                with app_module.connect() as conn:
                    kept = storage.list_boarders(conn)
            assert all(b.bed != "602A" for b in kept)
        finally:
            pushed.pop()

    def test_api_patch_with_header_succeeds(self, tmp_path):
        _, client, pushed = _bare_csrf_client(tmp_path)
        try:
            bid = self._boarder_id(client)
            token = _session_token(client)
            response = client.patch(
                f"/api/boarders/{bid}",
                json={"name": "Alicia", "bed": "602A"},
                headers={"X-CSRF-Token": token},
            )
            assert response.status_code == 200
            assert response.get_json() == {"ok": True}
        finally:
            pushed.pop()

    def test_api_delete_and_month_delete_require_header(self, tmp_path):
        application, client, pushed = _bare_csrf_client(tmp_path)
        try:
            bid = self._boarder_id(client)
            with application.app_context():
                with app_module.connect() as conn:
                    storage.save_month(
                        conn,
                        [storage.BoarderRecord(
                            name="ALICE", display_name="Alice",
                            bed="601A", frequency=1,
                            total_minutes=2, total_points=3,
                        )],
                        "2026-03",
                    )
                    conn.commit()
            assert client.delete(f"/api/boarders/{bid}").status_code == 403
            assert client.delete("/delete_month/2026-03").status_code == 403
            with application.app_context():
                with app_module.connect() as conn:
                    assert storage.boarder_exists(conn, "ALICE")
                    assert storage.get_month_report(conn, "2026-03")
            token = _session_token(client)
            headers = {"X-CSRF-Token": token}
            assert client.delete(f"/api/boarders/{bid}", headers=headers).status_code == 200
            assert (
                client.delete("/delete_month/2026-03", headers=headers).status_code
                == 200
            )
        finally:
            pushed.pop()

    def test_form_field_also_accepted_on_api(self, tmp_path):
        # Uniformity: either transport works on every mutation. The JSON
        # endpoint ignores form fields for data, so a form-field token plus
        # empty JSON body must fail validation (400) — never CSRF (403).
        _, client, pushed = _bare_csrf_client(tmp_path)
        try:
            bid = self._boarder_id(client)
            token = _session_token(client)
            response = client.patch(
                f"/api/boarders/{bid}",
                data={"name": "Alicia", "bed": "602A", "csrf_token": token},
                content_type="multipart/form-data",
            )
            assert response.status_code == 400
            assert "CSRF" not in response.get_json().get("error", "")
        finally:
            pushed.pop()


class TestFullMatrix:
    """Every mutation x missing/bad/good token (#148)."""

    def test_monthly_log_import_matrix(self, tmp_path):
        import io

        application, client, pushed = _bare_csrf_client(tmp_path)
        try:
            payload = (
                "Name,Bed,Date,Minutes\nALICE,601A,2026-03-01,5\n"
            )
            bad = {"report_month": "2026-03", "csrf_token": "wrong"}
            # Missing token.
            response = client.post(
                "/",
                data={"report_month": "2026-03",
                      "log_file": (io.BytesIO(payload.encode()), "log.csv")},
                content_type="multipart/form-data",
            )
            assert response.status_code == 403
            # Bad token.
            response = client.post(
                "/",
                data={**bad,
                      "log_file": (io.BytesIO(payload.encode()), "log.csv")},
                content_type="multipart/form-data",
            )
            assert response.status_code == 403
            # Neither reject may save the month.
            with application.app_context():
                with app_module.connect() as conn:
                    assert storage.list_months(conn) == []
            # Good token.
            token = _session_token(client)
            response = client.post(
                "/",
                data={"report_month": "2026-03", "csrf_token": token,
                      "log_file": (io.BytesIO(payload.encode()), "log.csv")},
                content_type="multipart/form-data",
            )
            assert response.status_code in (200, 302)
        finally:
            pushed.pop()

    def test_assign_and_bulk_patch_matrix(self, tmp_path):
        application, client, pushed = _bare_csrf_client(tmp_path)
        try:
            with application.app_context():
                with app_module.connect() as conn:
                    from tests.helpers import record

                    storage.save_month(
                        conn, [record("ALICE", "601A", 2, 5, 7)], "2026-03"
                    )
                    conn.commit()
            # Assign without token.
            assert client.post(
                "/assign/2026-03", data={"deadline": "2026-04-10"}
            ).status_code == 403
            with application.app_context():
                with app_module.connect() as conn:
                    assert storage.list_punishments(conn) == []
            # Assign with token.
            token = _session_token(client)
            response = client.post(
                "/assign/2026-03",
                data={"deadline": "2026-04-10",
                      "assign": ["ALICE"], "csrf_token": token},
            )
            assert response.status_code in (200, 302)
            # Bulk Master List update without header.
            assert client.patch(
                "/api/boarders", json={"boarders": []}
            ).status_code == 403
            with application.app_context():
                with app_module.connect() as conn:
                    assert len(storage.list_boarders(conn)) == 1
            # Bulk update with header.
            with application.app_context():
                with app_module.connect() as conn:
                    boarders = storage.list_boarders(conn)
            updates = [
                {"id": b.id, "name": b.display_name, "bed": b.bed}
                for b in boarders
            ]
            response = client.patch(
                "/api/boarders",
                json={"boarders": updates},
                headers={"X-CSRF-Token": token},
            )
            assert response.status_code == 200
        finally:
            pushed.pop()
    def test_import_and_punishment_routes_require_token(self, tmp_path):
        from tests.helpers import seed_punishments

        application, client, pushed = _bare_csrf_client(tmp_path)
        try:
            token = _session_token(client)
            with application.app_context():
                with app_module.connect() as conn:
                    punishments = seed_punishments(conn)
                    pid = punishments[0].id
            # Master List import without token.
            response = client.post("/boarders/import", data={})
            assert response.status_code == 403
            # Punishment transition without token.
            response = client.post(
                f"/punishment/{pid}/transition", data={"to": "submitted"}
            )
            assert response.status_code == 403
            with application.app_context():
                with app_module.connect() as conn:
                    kept = storage.list_punishments(conn, statuses=("assigned",))
            assert len(kept) == 1
            # Same transition with token succeeds (redirects back).
            response = client.post(
                f"/punishment/{pid}/transition",
                data={"to": "submitted", "csrf_token": token},
            )
            assert response.status_code in (200, 302)
        finally:
            pushed.pop()


class TestWrongTokenMatrix:
    """Every mutation rejects a wrong token with stored data unchanged."""

    def test_assign_with_wrong_token_rejected(self, tmp_path):
        from tests.helpers import record

        application, client, pushed = _bare_csrf_client(tmp_path)
        try:
            with application.app_context():
                with app_module.connect() as conn:
                    storage.save_month(
                        conn, [record("ALICE", "601A", 2, 5, 7)], "2026-03"
                    )
                    conn.commit()
            response = client.post(
                "/assign/2026-03",
                data={"deadline": "2026-04-10",
                      "assign": ["ALICE"], "csrf_token": "wrong"},
            )
            assert response.status_code == 403
            with application.app_context():
                with app_module.connect() as conn:
                    assert storage.list_punishments(conn) == []
        finally:
            pushed.pop()

    def test_bulk_patch_with_wrong_header_rejected(self, tmp_path):
        application, client, pushed = _bare_csrf_client(tmp_path)
        try:
            with application.app_context():
                with app_module.connect() as conn:
                    before = storage.list_boarders(conn)
            response = client.patch(
                "/api/boarders",
                json={"boarders": []},
                headers={"X-CSRF-Token": "wrong"},
            )
            assert response.status_code == 403
            with application.app_context():
                with app_module.connect() as conn:
                    after = storage.list_boarders(conn)
            assert [b.id for b in after] == [b.id for b in before]
        finally:
            pushed.pop()

    def test_transition_with_wrong_token_rejected(self, tmp_path):
        from tests.helpers import seed_punishments

        application, client, pushed = _bare_csrf_client(tmp_path)
        try:
            with application.app_context():
                with app_module.connect() as conn:
                    pid = seed_punishments(conn)[0].id
            response = client.post(
                f"/punishment/{pid}/transition",
                data={"to": "submitted", "csrf_token": "wrong"},
            )
            assert response.status_code == 403
            with application.app_context():
                with app_module.connect() as conn:
                    kept = storage.list_punishments(conn, statuses=("assigned",))
            assert len(kept) == 1
        finally:
            pushed.pop()

    def test_single_patch_and_deletes_with_wrong_header_rejected(self, tmp_path):
        from tests.helpers import record

        application, client, pushed = _bare_csrf_client(tmp_path)
        try:
            html = client.get("/boarders").get_data(as_text=True)
            bid = int(re.search(r'data-boarder-id="(\d+)"', html).group(1))
            with application.app_context():
                with app_module.connect() as conn:
                    storage.save_month(
                        conn,
                        [storage.BoarderRecord(
                            name="ALICE", display_name="Alice",
                            bed="601A", frequency=1,
                            total_minutes=2, total_points=3,
                        )],
                        "2026-03",
                    )
                    conn.commit()
            headers = {"X-CSRF-Token": "wrong"}
            assert client.patch(
                f"/api/boarders/{bid}",
                json={"name": "Alice", "bed": "602A"}, headers=headers,
            ).status_code == 403
            assert client.delete(
                f"/api/boarders/{bid}", headers=headers,
            ).status_code == 403
            assert client.delete(
                "/delete_month/2026-03", headers=headers,
            ).status_code == 403
            with application.app_context():
                with app_module.connect() as conn:
                    assert storage.boarder_exists(conn, "ALICE")
                    assert storage.get_month_report(conn, "2026-03")
        finally:
            pushed.pop()

    def test_master_list_import_with_wrong_token_rejected(self, tmp_path):
        import io

        application, client, pushed = _bare_csrf_client(tmp_path)
        try:
            with application.app_context():
                with app_module.connect() as conn:
                    before = [b.normalized_name for b in storage.list_boarders(conn)]
            payload = "Name,Bed\nCAROL,602A\n"
            response = client.post(
                "/boarders/import",
                data={"csrf_token": "wrong",
                      "boarder_csv": (io.BytesIO(payload.encode()), "m.csv")},
                content_type="multipart/form-data",
            )
            assert response.status_code == 403
            with application.app_context():
                with app_module.connect() as conn:
                    after = [b.normalized_name for b in storage.list_boarders(conn)]
            assert after == before
        finally:
            pushed.pop()
