"""CSRF protection (#145-#147).

Seam: HTTP via Flask test_client + application factory config.
"""

import re

import app as app_module
import storage


class TestSecretKeyHardFail:
    def test_missing_secret_aborts_startup(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        try:
            app_module.create_app({"DB_PATH": str(tmp_path / "x.db")})
        except SystemExit as exc:
            assert "SECRET_KEY" in str(exc)
        else:
            raise AssertionError("create_app should SystemExit without SECRET_KEY")

    def test_empty_secret_aborts_startup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "")
        try:
            app_module.create_app({"DB_PATH": str(tmp_path / "x.db")})
        except SystemExit as exc:
            assert "SECRET_KEY" in str(exc)
        else:
            raise AssertionError("create_app should SystemExit with empty SECRET_KEY")

    def test_empty_inline_secret_aborts_even_with_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "env-secret-value")
        try:
            app_module.create_app(
                {"DB_PATH": str(tmp_path / "x.db"), "SECRET_KEY": ""}
            )
        except SystemExit as exc:
            assert "SECRET_KEY" in str(exc)
        else:
            raise AssertionError("empty inline SECRET_KEY should SystemExit")

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


def _csrf_client(tmp_path):
    """Isolated app + client seeded with the Master List."""
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
        _, client, pushed = _csrf_client(tmp_path)
        try:
            token = _session_token(client)
            assert token, "GET should seed a session CSRF token"
            html = client.get("/boarders").get_data(as_text=True)
            assert 'name="csrf_token"' in html
            assert token in html
        finally:
            pushed.pop()

    def test_add_boarder_without_token_rejected(self, tmp_path):
        application, client, pushed = _csrf_client(tmp_path)
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
        _, client, pushed = _csrf_client(tmp_path)
        try:
            response = client.post(
                "/boarders/add",
                data={"name": "Carol", "bed": "602A", "csrf_token": "wrong"},
            )
            assert response.status_code == 403
        finally:
            pushed.pop()

    def test_add_boarder_with_token_succeeds(self, tmp_path):
        _, client, pushed = _csrf_client(tmp_path)
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

class TestFetchCsrf:
    def _boarder_id(self, client):
        html = client.get("/boarders").get_data(as_text=True)
        match = re.search(r'data-boarder-id="(\d+)"', html)
        assert match, "no boarder row rendered"
        return int(match.group(1))

    def test_api_patch_without_header_rejected(self, tmp_path):
        _, client, pushed = _csrf_client(tmp_path)
        try:
            bid = self._boarder_id(client)
            response = client.patch(
                f"/api/boarders/{bid}", json={"name": "Alice", "bed": "602A"}
            )
            assert response.status_code == 403
            assert response.get_json()["ok"] is False
        finally:
            pushed.pop()

    def test_api_patch_with_header_succeeds(self, tmp_path):
        _, client, pushed = _csrf_client(tmp_path)
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
        application, client, pushed = _csrf_client(tmp_path)
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
        _, client, pushed = _csrf_client(tmp_path)
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

        _, client, pushed = _csrf_client(tmp_path)
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
        application, client, pushed = _csrf_client(tmp_path)
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

        application, client, pushed = _csrf_client(tmp_path)
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
            # Same transition with token succeeds (redirects back).
            response = client.post(
                f"/punishment/{pid}/transition",
                data={"to": "submitted", "csrf_token": token},
            )
            assert response.status_code in (200, 302)
        finally:
            pushed.pop()
