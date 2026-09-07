"""Request/error hardening: upload cap, logging, 500 handler (#130).

Seam: HTTP via Flask test_client + application factory config.
"""

import io
import logging

import pytest

import app as app_module
import storage
from tests.helpers import delete_csrf, patch_csrf, post_csrf

# Small enough that oversize payloads stay cheap, large enough that the
# fixture's ordinary requests (cookies, small CSVs) pass through.
TEST_CAP_BYTES = 4096


def _hardening_client(tmp_path, **overrides):
    """Factory app with a tiny upload cap and visible (non-propagated) 500s."""
    db_path = tmp_path / "hardening.db"
    namelist = tmp_path / "namelist.csv"
    namelist.write_text("Bed,Name\n601A,ALICE\n", encoding="utf-8")
    config = {
        "DB_PATH": str(db_path),
        "NAMELIST_PATH": str(namelist),
        "SECRET_KEY": "hardening-test-secret",
        "TESTING": True,
        # TESTING would otherwise propagate exceptions past the 500 handler.
        "PROPAGATE_EXCEPTIONS": False,
        "MAX_CONTENT_LENGTH": TEST_CAP_BYTES,
        **overrides,
    }
    application = app_module.create_app(config)
    pushed = application.app_context()
    pushed.push()
    app_module.init_db()
    return application, application.test_client(), pushed


@pytest.fixture
def hardening_client(tmp_path):
    application, client, pushed = _hardening_client(tmp_path)
    yield application, client
    pushed.pop()


def _post_big_log(client):
    """POSTs an over-cap Monthly Log to the home Import."""
    payload = b"x" * (TEST_CAP_BYTES * 2)
    return client.post(
        "/",
        data={
            "report_month": "2026-03",
            "log_file": (io.BytesIO(payload), "big-log.csv"),
        },
    )


def _boom(*args, **kwargs):
    raise RuntimeError("boom")


class TestUploadCapConfig:
    def test_default_cap_is_16mb(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MAX_CONTENT_LENGTH", raising=False)
        application = app_module.create_app({"DB_PATH": str(tmp_path / "x.db")})
        assert application.config["MAX_CONTENT_LENGTH"] == 16 * 1024 * 1024

    def test_inline_cap_beats_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAX_CONTENT_LENGTH", "8192")
        application = app_module.create_app(
            {"DB_PATH": str(tmp_path / "x.db"), "MAX_CONTENT_LENGTH": 1234}
        )
        assert application.config["MAX_CONTENT_LENGTH"] == 1234

    def test_environment_beats_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAX_CONTENT_LENGTH", "8192")
        application = app_module.create_app({"DB_PATH": str(tmp_path / "x.db")})
        assert application.config["MAX_CONTENT_LENGTH"] == 8192

    def test_invalid_environment_falls_back_to_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAX_CONTENT_LENGTH", "not-a-number")
        application = app_module.create_app({"DB_PATH": str(tmp_path / "x.db")})
        assert application.config["MAX_CONTENT_LENGTH"] == 16 * 1024 * 1024


class TestLogLevelConfig:
    def test_default_level_is_info(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        application = app_module.create_app({"DB_PATH": str(tmp_path / "x.db")})
        assert application.logger.getEffectiveLevel() == logging.INFO

    def test_environment_level_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        application = app_module.create_app({"DB_PATH": str(tmp_path / "x.db")})
        assert application.logger.getEffectiveLevel() == logging.DEBUG

    def test_inline_level_beats_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        application = app_module.create_app(
            {"DB_PATH": str(tmp_path / "x.db"), "LOG_LEVEL": "WARNING"}
        )
        assert application.logger.getEffectiveLevel() == logging.WARNING

    def test_invalid_level_falls_back_to_info(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "NOPE")
        application = app_module.create_app({"DB_PATH": str(tmp_path / "x.db")})
        assert application.logger.getEffectiveLevel() == logging.INFO


class TestOversizeUpload:
    def test_monthly_log_over_cap_is_rejected(self, hardening_client):
        _, client = hardening_client
        response = _post_big_log(client)
        assert response.status_code == 413
        assert b"Monthly Log" in response.data

    def test_oversize_import_stores_nothing(self, hardening_client):
        _, client = hardening_client
        _post_big_log(client)
        with app_module.connect() as conn:
            assert storage.list_months(conn) == []

    def test_master_list_over_cap_names_master_list(self, hardening_client):
        _, client = hardening_client
        payload = b"x" * (TEST_CAP_BYTES * 2)
        response = client.post(
            "/boarders/import",
            data={"boarder_csv": (io.BytesIO(payload), "big-namelist.csv")},
        )
        assert response.status_code == 413
        assert b"Master List" in response.data

    def test_under_cap_upload_still_imports(self, hardening_client):
        _, client = hardening_client
        csv_text = "Name,Transaction Time\nALICE,07:42\n"
        assert len(csv_text.encode("utf-8")) < TEST_CAP_BYTES
        response = post_csrf(
            client,
            "/",
            data={
                "report_month": "2026-08",
                "log_file": (io.BytesIO(csv_text.encode("utf-8")), "log.csv"),
            },
        )
        assert response.status_code == 302
        with app_module.connect() as conn:
            assert [m.month for m in storage.list_months(conn)] == ["2026-08"]

    def test_oversize_api_body_is_json(self, hardening_client):
        _, client = hardening_client
        response = patch_csrf(
            client,
            "/api/boarders",
            json={"boarders": [{"id": 1, "name": "X" * TEST_CAP_BYTES, "bed": "101"}]},
        )
        assert response.status_code == 413
        payload = response.get_json()
        assert payload["ok"] is False
        assert "KB" in payload["error"]

    def test_oversize_elsewhere_uses_generic_message(self, hardening_client):
        _, client = hardening_client
        response = client.post(
            "/boarders/add",
            data={"name": "Y" * (TEST_CAP_BYTES * 2), "bed": "101"},
        )
        assert response.status_code == 413
        assert b"Nothing was changed." in response.data
        assert b"Nothing was imported." not in response.data


class TestOversizeMessage:
    def test_master_list_path_names_master_list(self):
        message = app_module._oversize_message("/boarders/import", "4 KB")
        assert "Master List" in message
        assert "Nothing was imported." in message

    def test_home_path_names_monthly_log(self):
        message = app_module._oversize_message("/", "4 KB")
        assert "Monthly Log" in message
        assert "Nothing was imported." in message

    def test_other_paths_use_generic_message(self):
        message = app_module._oversize_message("/boarders/add", "4 KB")
        assert "Nothing was changed." in message
        assert "Nothing was imported." not in message


class TestServerErrorPage:
    def test_unhandled_exception_renders_clean_page(
        self, hardening_client, monkeypatch, caplog
    ):
        _, client = hardening_client
        monkeypatch.setattr(app_module.storage, "list_months", _boom)
        with caplog.at_level(logging.ERROR, logger=app_module.app.logger.name):
            response = client.get("/")
        assert response.status_code == 500
        assert b"Something went wrong" in response.data
        assert b"Back to dashboard" in response.data

    def test_unhandled_exception_logs_traceback(
        self, hardening_client, monkeypatch, caplog
    ):
        _, client = hardening_client
        monkeypatch.setattr(app_module.storage, "list_months", _boom)
        with caplog.at_level(logging.ERROR, logger=app_module.app.logger.name):
            client.get("/")
        assert any(
            record.levelno >= logging.ERROR and record.exc_info is not None
            for record in caplog.records
        )


class TestServerErrorJson:
    def test_api_500_is_json(self, hardening_client, monkeypatch):
        _, client = hardening_client
        monkeypatch.setattr(app_module.storage, "get_month_report", _boom)
        response = client.get("/api/month/2026-03")
        assert response.status_code == 500
        payload = response.get_json()
        assert payload["ok"] is False
        assert payload["error"]

    def test_delete_month_500_matches_existing_shape(
        self, hardening_client, monkeypatch
    ):
        _, client = hardening_client
        monkeypatch.setattr(app_module.storage, "delete_month", _boom)
        response = delete_csrf(client, "/delete_month/2026-03")
        assert response.status_code == 500
        payload = response.get_json()
        assert "ok" not in payload
        assert payload["error"]
