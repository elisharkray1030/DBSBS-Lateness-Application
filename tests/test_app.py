import io
import os
import re
import tempfile

import pytest
from helpers import record

import app as app_module
import storage

_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)
os.environ["DB_PATH"] = _db_path

client = app_module.app.test_client()


@pytest.fixture(scope="session", autouse=True)
def _cleanup_temp_db():
    yield
    if os.path.exists(_db_path):
        os.unlink(_db_path)
    os.environ.pop("DB_PATH", None)


def home_html():
    response = client.get("/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def tab_button_class(html, tab_name):
    match = re.search(f'<button class="([^"]*)" data-tab="{tab_name}">', html)
    assert match is not None, f"no tab button found for {tab_name!r}"
    return match.group(1)


class TestHomeRender:
    def test_report_archive_tab_precedes_history_tab(self):
        html = home_html()
        assert html.index("View Reports in Database") < html.index("Search Boarder History")

    def test_report_archive_tab_is_active_by_default(self):
        html = home_html()
        assert "active" in tab_button_class(html, "reports").split()
        assert "active" not in tab_button_class(html, "history").split()

    def test_page_contains_no_historical_reports_text(self):
        html = home_html()
        assert "Historical Reports" not in html

    def test_page_contains_no_subtitle_text(self):
        html = home_html()
        assert "Search boarder history and manage saved reports." not in html

    def test_history_panel_keeps_its_heading(self):
        html = home_html()
        assert "<h2>Search Boarder History</h2>" in html


class TestImportMonthPicker:
    def test_report_month_input_is_a_month_picker(self):
        html = home_html()
        assert 'name="report_month"' in html
        assert re.search(r'<input[^>]*type="month"', html) is not None

    def test_bad_month_label_import_is_rejected_with_error(self):
        data = {
            "report_month": "March 2026",
            "log_file": (io.BytesIO(b"Name,Transaction Time\nALICE,07:42\n"), "log.csv"),
        }
        response = client.post("/", data=data, content_type="multipart/form-data")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Invalid month label" in html
        assert "YYYY-MM" in html


class TestAssignRoute:
    @pytest.fixture(autouse=True)
    def _seed_month(self):
        with app_module.connect() as conn:
            conn.execute("DELETE FROM punishments")
            conn.execute("DELETE FROM boarder_history")
            conn.commit()
            storage.save_month(
                conn,
                [
                    record("ALICE", "101", 2, 5, 7),
                    record("BOB", "102", 1, 19, 20),
                    record("CAROL", "103", 0, 0, 0),
                ],
                "2026-03",
            )

    def test_assign_creates_punishments_for_late_boarders(self):
        response = client.post("/assign/2026-03", data={"deadline": "2026-04-10"})

        assert response.status_code == 302
        with app_module.connect() as conn:
            rows = storage.list_punishments(conn, statuses=("assigned",))
            assert {r.normalized_name for r in rows} == {"ALICE", "BOB"}

    def test_exemptions_excluded(self):
        response = client.post(
            "/assign/2026-03",
            data={"deadline": "2026-04-10", "exempt": ["BOB"]},
        )

        assert response.status_code == 302
        with app_module.connect() as conn:
            rows = storage.list_punishments(conn, statuses=("assigned",))
            assert {r.normalized_name for r in rows} == {"ALICE"}

    def test_missing_deadline_is_an_error(self):
        response = client.post("/assign/2026-03", data={})

        assert response.status_code == 400
        assert b"deadline" in response.data.lower()

    def test_assign_redirect_shows_message_and_opens_month(self):
        response = client.post(
            "/assign/2026-03",
            data={"deadline": "2026-04-10"},
            follow_redirects=True,
        )

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Assigned 2 punishments" in html
        assert "report_month" in html

    def test_month_with_no_report_is_an_error(self):
        response = client.post("/assign/2026-99", data={"deadline": "2026-04-10"})

        assert response.status_code == 404


class TestConsequencesRoute:
    @pytest.fixture(autouse=True)
    def _seed_punishment(self):
        with app_module.connect() as conn:
            conn.execute("DELETE FROM punishments")
            conn.execute("DELETE FROM boarder_history")
            conn.commit()
            storage.save_month(
                conn,
                [
                    record("ALICE", "101", 2, 5, 7),
                    record("BOB", "102", 1, 19, 20),
                ],
                "2026-03",
            )
            storage.assign_punishments(
                conn,
                month="2026-03",
                boarders=[
                    record("ALICE", "101", 2, 5, 7),
                    record("BOB", "102", 1, 19, 20),
                ],
                deadline="2026-04-10",
                assigned_at="2026-04-01T09:00:00+00:00",
            )

    def test_consequences_page_lists_in_flight_punishments(self):
        response = client.get("/consequences")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Alice" in html
        assert "Bob" in html
        assert "2026-04-10" in html

    def test_consequences_tab_is_rendered(self):
        html = client.get("/").get_data(as_text=True)
        assert "data-tab=\"consequences\"" in html

    def test_show_all_includes_submitted(self):
        with app_module.connect() as conn:
            row = storage.list_punishments(conn, statuses=("assigned",))[0]
            storage.transition_punishment(
                conn, row.id, "submitted", timestamp="2026-04-09T09:00:00+00:00"
            )

        response = client.get("/consequences?show_all=1")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "submitted" in html

    def test_month_filter_dropdown_lists_saved_months(self):
        html = client.get("/consequences").get_data(as_text=True)

        assert 'value="2026-03"' in html

    def test_status_filter_shows_only_that_status(self):
        with app_module.connect() as conn:
            row = storage.list_punishments(conn, statuses=("assigned",))[0]
            storage.transition_punishment(
                conn, row.id, "submitted", timestamp="2026-04-09T09:00:00+00:00"
            )

        response = client.get("/consequences?status=submitted")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Alice" in html
        assert "Bob" not in html


class TestTransitionRoute:
    @pytest.fixture(autouse=True)
    def _seed_punishment(self):
        with app_module.connect() as conn:
            conn.execute("DELETE FROM punishments")
            conn.execute("DELETE FROM boarder_history")
            conn.commit()
            storage.save_month(
                conn,
                [record("ALICE", "101", 2, 5, 7)],
                "2026-03",
            )
            storage.assign_punishments(
                conn,
                month="2026-03",
                boarders=[record("ALICE", "101", 2, 5, 7)],
                deadline="2026-04-10",
                assigned_at="2026-04-01T09:00:00+00:00",
            )

    def _alice_id(self):
        with app_module.connect() as conn:
            return storage.list_punishments(conn)[0].id

    def test_mark_overdue(self):
        response = client.post(f"/punishment/{self._alice_id()}/transition", data={"to": "overdue"})

        assert response.status_code == 302
        with app_module.connect() as conn:
            row = storage.get_punishment(conn, self._alice_id())
            assert row.status == "overdue"
            assert row.overdue_at is not None

    def test_void_with_reason(self):
        response = client.post(
            f"/punishment/{self._alice_id()}/transition",
            data={"to": "voided", "void_reason": "exempt"},
        )

        assert response.status_code == 302
        with app_module.connect() as conn:
            row = storage.get_punishment(conn, self._alice_id())
            assert row.status == "voided"
            assert row.void_reason == "exempt"

    def test_illegal_transition_rejected(self):
        with app_module.connect() as conn:
            row = storage.list_punishments(conn, statuses=("assigned",))[0]
            storage.transition_punishment(
                conn, row.id, "submitted", timestamp="2026-04-09T09:00:00+00:00"
            )
        response = client.post(f"/punishment/{self._alice_id()}/transition", data={"to": "phone_held"})

        assert response.status_code == 400
        assert b"not allowed" in response.data.lower()
