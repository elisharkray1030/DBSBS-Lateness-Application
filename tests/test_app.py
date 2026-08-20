import io
import os
import re
import tempfile

import pytest
from helpers import record
from records import Boarder

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


class TestTabNavigation:
    def test_all_tabs_are_reachable_when_boarder_rows_are_rendered(self, fresh_client):
        playwright_api = pytest.importorskip("playwright.sync_api")
        html = fresh_client.get("/").get_data(as_text=True)

        with playwright_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            try:
                page.set_content(html)

                for tab_name in ("history", "consequences", "boarders", "reports"):
                    page.locator(f'.tab-link[data-tab="{tab_name}"]').click()
                    assert page.locator(f"#{tab_name}").evaluate(
                        "panel => panel.classList.contains('active')"
                    ), page_errors

                assert not page_errors
            finally:
                browser.close()


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


class TestBoardersTab:
    def test_boarders_tab_button_is_rendered(self):
        html = home_html()
        assert 'data-tab="boarders"' in html

    def test_boarders_route_renders_boarders_panel(self, fresh_client):
        response = fresh_client.get("/boarders")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'id="boarders"' in html
        assert "ALICE" in html
        assert "601A" in html
        assert "BOB" in html
        assert "601B" in html

    def test_boarders_route_is_active_tab(self, fresh_client):
        html = fresh_client.get("/boarders").get_data(as_text=True)
        assert "active" in tab_button_class(html, "boarders").split()

    def test_boarders_table_hides_actions_heading_and_keeps_trash_control(self, fresh_client):
        html = fresh_client.get("/boarders").get_data(as_text=True)
        boarders_panel = re.search(r'<section id="boarders".*?</section>', html, re.S)
        assert boarders_panel is not None
        panel = boarders_panel.group(0)
        assert '<th class="boarder-actions"></th>' in panel
        assert '>Actions<' not in panel
        assert 'id="boarder-edit"' in panel
        assert 'icon-trash' in html

    def test_boarders_rows_render_static_no_inputs(self, fresh_client):
        html = fresh_client.get("/boarders").get_data(as_text=True)
        table = re.search(r'<table class="boarders-table".*?</table>', html, re.S)
        assert table is not None
        assert '<input' not in table.group(0)
        assert 'boarder-edit-name' not in table.group(0)
        assert 'boarder-edit-bed' not in table.group(0)

    def test_boarders_table_has_bed_and_name_columns(self, fresh_client):
        html = fresh_client.get("/boarders").get_data(as_text=True)
        assert '<th>Bed</th>' in html
        assert '<th>Boarder Name</th>' in html


class TestBoarderSeeding:
    def test_startup_seeds_boarders_from_namelist(self, tmp_path, monkeypatch):
        db_path = tmp_path / "seed.db"
        monkeypatch.setattr(app_module, "DB_PATH", str(db_path))
        namelist = tmp_path / "namelist.csv"
        namelist.write_text("Bed,Name\n601A,Alice\n", encoding="utf-8")
        monkeypatch.setattr(app_module, "NAMELIST_PATH", str(namelist))
        app_module.init_db()
        with app_module.connect() as conn:
            assert storage.boarder_master_list(conn) == {"ALICE": Boarder("ALICE", "Alice", "601A")}

    def test_seeding_is_skipped_when_boarders_exist(self, tmp_path, monkeypatch):
        db_path = tmp_path / "seed.db"
        monkeypatch.setattr(app_module, "DB_PATH", str(db_path))
        app_module.init_db()
        with app_module.connect() as conn:
            storage.replace_boarders(conn, [Boarder("ALICE", "Alice", "601A")])
        namelist = tmp_path / "namelist.csv"
        namelist.write_text("Bed,Name\n601A,BOB\n", encoding="utf-8")
        monkeypatch.setattr(app_module, "NAMELIST_PATH", str(namelist))
        app_module.init_db()
        with app_module.connect() as conn:
            assert storage.boarder_master_list(conn) == {"ALICE": Boarder("ALICE", "Alice", "601A")}

    def test_no_namelist_leaves_boarders_empty(self, tmp_path, monkeypatch):
        db_path = tmp_path / "seed.db"
        monkeypatch.setattr(app_module, "DB_PATH", str(db_path))
        monkeypatch.setattr(app_module, "NAMELIST_PATH", str(tmp_path / "missing.csv"))
        app_module.init_db()
        with app_module.connect() as conn:
            assert storage.boarder_master_list(conn) == {}

    def test_seed_does_not_resurrect_after_emptying_roster(self, tmp_path, monkeypatch):
        db_path = tmp_path / "seed.db"
        monkeypatch.setattr(app_module, "DB_PATH", str(db_path))
        namelist = tmp_path / "namelist.csv"
        namelist.write_text("Bed,Name\n601A,Alice\n", encoding="utf-8")
        monkeypatch.setattr(app_module, "NAMELIST_PATH", str(namelist))
        app_module.init_db()
        with app_module.connect() as conn:
            assert storage.boarder_master_list(conn) == {"ALICE": Boarder("ALICE", "Alice", "601A")}
            conn.execute("DELETE FROM boarders")
            conn.commit()
        app_module.init_db()
        with app_module.connect() as conn:
            assert storage.boarder_master_list(conn) == {}

    def test_no_namelist_forfeits_seed_forever(self, tmp_path, monkeypatch):
        db_path = tmp_path / "seed.db"
        monkeypatch.setattr(app_module, "DB_PATH", str(db_path))
        monkeypatch.setattr(app_module, "NAMELIST_PATH", str(tmp_path / "missing.csv"))
        app_module.init_db()
        namelist = tmp_path / "namelist.csv"
        namelist.write_text("Bed,Name\n601A,Alice\n", encoding="utf-8")
        monkeypatch.setattr(app_module, "NAMELIST_PATH", str(namelist))
        app_module.init_db()
        with app_module.connect() as conn:
            assert storage.boarder_master_list(conn) == {}

    def test_populated_deployment_marks_seeded_without_wipe(self, tmp_path, monkeypatch):
        db_path = tmp_path / "seed.db"
        monkeypatch.setattr(app_module, "DB_PATH", str(db_path))
        with app_module.connect() as conn:
            storage.create_schema(conn)
            storage.replace_boarders(conn, [Boarder("ALICE", "Alice", "601A")])
        namelist = tmp_path / "namelist.csv"
        namelist.write_text("Bed,Name\n601A,BOB\n", encoding="utf-8")
        monkeypatch.setattr(app_module, "NAMELIST_PATH", str(namelist))
        app_module.init_db()
        with app_module.connect() as conn:
            assert storage.boarder_master_list(conn) == {"ALICE": Boarder("ALICE", "Alice", "601A")}


class TestImportUsesDbBoarders:
    def test_import_matches_boarder_known_only_to_db(self, fresh_client):
        with app_module.connect() as conn:
            storage.replace_boarders(
                conn,
                [Boarder("ALICE", "Alice", "601A"), Boarder("GHOST", "Ghost", "999")],
            )
        resp = fresh_client.post(
            "/",
            data={
                "report_month": "2026-08",
                "log_file": (io.BytesIO(b"Name,Transaction Time\nGHOST,07:42\n"), "log.csv"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 302
        with app_module.connect() as conn:
            months = storage.list_months(conn)
        assert "2026-08" in [m.month for m in months]

    def test_import_does_not_match_csv_only_boarder(self, fresh_client):
        with app_module.connect() as conn:
            storage.replace_boarders(conn, [Boarder("ALICE", "Alice", "601A")])
        resp = fresh_client.post(
            "/",
            data={
                "report_month": "2026-08",
                "log_file": (io.BytesIO(b"Name,Transaction Time\nBOB,07:42\n"), "log.csv"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Unmatched names in the log: BOB" in html


@pytest.fixture()
def fresh_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_module, "DB_PATH", str(db_path))
    namelist = tmp_path / "namelist.csv"
    namelist.write_text(
        "Bed,Name\n601A,ALICE\n601B,BOB\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "NAMELIST_PATH", str(namelist))
    app_module.init_db()
    return app_module.app.test_client()


class TestBoarderAdd:
    def test_add_boarder_appears_in_list(self, fresh_client):
        resp = fresh_client.post("/boarders/add", data={"name": "Carol", "bed": "601C"})
        assert resp.status_code == 302
        html = fresh_client.get("/boarders").get_data(as_text=True)
        assert "Carol" in html
        assert "601C" in html

    def test_add_empty_name_is_rejected_inline(self, fresh_client):
        resp = fresh_client.post("/boarders/add", data={"name": "", "bed": "601C"})
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "name is required" in html.lower()

    def test_add_empty_bed_is_rejected_inline(self, fresh_client):
        resp = fresh_client.post("/boarders/add", data={"name": "Carol", "bed": ""})
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "bed is required" in html.lower()

    def test_add_duplicate_name_is_rejected_inline(self, fresh_client):
        resp = fresh_client.post("/boarders/add", data={"name": "alice", "bed": "601C"})
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "already" in html.lower()

    def test_add_duplicate_bed_is_rejected_inline(self, fresh_client):
        resp = fresh_client.post("/boarders/add", data={"name": "Carol", "bed": "601A"})
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "601A" in html
        assert "already" in html.lower()


class TestBoarderEditApi:
    def _alice_id(self, fresh_client):
        with app_module.connect() as conn:
            return storage.list_boarders(conn)[0].id

    def _boarder(self, fresh_client, normalized_name):
        with app_module.connect() as conn:
            return next(
                b for b in storage.list_boarders(conn) if b.normalized_name == normalized_name
            )

    def test_patch_updates_name_and_bed(self, fresh_client):
        boarder_id = self._alice_id(fresh_client)
        resp = fresh_client.patch(
            f"/api/boarders/{boarder_id}", json={"name": "Alicia", "bed": "602A"}
        )
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}
        html = fresh_client.get("/boarders").get_data(as_text=True)
        assert "Alicia" in html
        assert "602A" in html
        assert "ALICE" not in html

    def test_patch_rejects_name_taken_by_another(self, fresh_client):
        fresh_client.post("/boarders/add", data={"name": "Carol", "bed": "601C"})
        boarder_id = self._alice_id(fresh_client)
        resp = fresh_client.patch(
            f"/api/boarders/{boarder_id}", json={"name": "carol", "bed": "601A"}
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["ok"] is False
        assert self._boarder(fresh_client, "ALICE").bed == "601A"

    def test_patch_rejects_bed_taken_by_another(self, fresh_client):
        fresh_client.post("/boarders/add", data={"name": "Carol", "bed": "601C"})
        boarder_id = self._alice_id(fresh_client)
        resp = fresh_client.patch(
            f"/api/boarders/{boarder_id}", json={"name": "Alice", "bed": "601C"}
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["ok"] is False
        assert self._boarder(fresh_client, "ALICE").bed == "601A"

    def test_patch_keeping_own_bed_is_not_a_conflict(self, fresh_client):
        boarder_id = self._alice_id(fresh_client)
        resp = fresh_client.patch(
            f"/api/boarders/{boarder_id}", json={"name": "Alicia", "bed": "601A"}
        )
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}

    def test_patch_rejects_empty_name(self, fresh_client):
        boarder_id = self._alice_id(fresh_client)
        resp = fresh_client.patch(
            f"/api/boarders/{boarder_id}", json={"name": "", "bed": "601A"}
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["ok"] is False
        assert "name" in body["error"].lower()
        assert self._boarder(fresh_client, "ALICE").bed == "601A"

    def test_patch_rejects_empty_bed(self, fresh_client):
        boarder_id = self._alice_id(fresh_client)
        resp = fresh_client.patch(
            f"/api/boarders/{boarder_id}", json={"name": "Alice", "bed": ""}
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["ok"] is False
        assert "bed" in body["error"].lower()
        assert self._boarder(fresh_client, "ALICE").bed == "601A"

    def test_bulk_patch_updates_all_boarders_atomically(self, fresh_client):
        with app_module.connect() as conn:
            boarders = storage.list_boarders(conn)
        by_name = {boarder.normalized_name: boarder for boarder in boarders}

        resp = fresh_client.patch(
            "/api/boarders",
            json={
                "boarders": [
                    {"id": by_name["ALICE"].id, "name": "Alice", "bed": "601B"},
                    {"id": by_name["BOB"].id, "name": "Bob", "bed": "601A"},
                ]
            },
        )

        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}
        with app_module.connect() as conn:
            updated = storage.list_boarders(conn)
        assert {(boarder.display_name, boarder.bed) for boarder in updated} == {
            ("Alice", "601B"),
            ("Bob", "601A"),
        }

    def test_bulk_patch_rejects_conflict_without_partial_updates(self, fresh_client):
        with app_module.connect() as conn:
            boarders = storage.list_boarders(conn)
        by_name = {boarder.normalized_name: boarder for boarder in boarders}

        resp = fresh_client.patch(
            "/api/boarders",
            json={
                "boarders": [
                    {"id": by_name["ALICE"].id, "name": "Bob", "bed": "601A"},
                    {"id": by_name["BOB"].id, "name": "Bob", "bed": "601B"},
                ]
            },
        )

        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False
        with app_module.connect() as conn:
            unchanged = storage.list_boarders(conn)
        assert {(boarder.display_name, boarder.bed) for boarder in unchanged} == {
            ("ALICE", "601A"),
            ("BOB", "601B"),
        }


class TestBoarderDeleteApi:
    def _alice_id(self, fresh_client):
        with app_module.connect() as conn:
            return storage.list_boarders(conn)[0].id

    def test_delete_removes_from_list(self, fresh_client):
        boarder_id = self._alice_id(fresh_client)
        resp = fresh_client.delete(f"/api/boarders/{boarder_id}")
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}
        html = fresh_client.get("/boarders").get_data(as_text=True)
        assert "ALICE" not in html
        assert "BOB" in html

    def test_delete_keeps_history_and_punishments_frozen(self, fresh_client):
        with app_module.connect() as conn:
            boarder_id = storage.list_boarders(conn)[0].id
            storage.save_month(
                conn,
                [record("ALICE", "601A", 2, 5, 7)],
                "2026-03",
            )
            storage.assign_punishments(
                conn,
                month="2026-03",
                boarders=[record("ALICE", "601A", 2, 5, 7)],
                deadline="2026-04-10",
                assigned_at="2026-04-01T09:00:00+00:00",
            )
        resp = fresh_client.delete(f"/api/boarders/{boarder_id}")
        assert resp.status_code == 200
        with app_module.connect() as conn:
            saved = storage.get_month_report(conn, "2026-03")
            puns = storage.list_punishments(conn)
        assert {r.name for r in saved} == {"ALICE"}
        assert {p.normalized_name for p in puns} == {"ALICE"}
        assert puns[0].bed == "601A"
        assert puns[0].display_name == "Alice"

    def test_delete_unknown_boarder_is_noop(self, fresh_client):
        resp = fresh_client.delete("/api/boarders/999")
        assert resp.status_code == 200
        with app_module.connect() as conn:
            assert len(storage.list_boarders(conn)) == 2

    def test_import_matches_after_edit(self, fresh_client):
        with app_module.connect() as conn:
            boarder_id = storage.list_boarders(conn)[0].id
        fresh_client.patch(
            f"/api/boarders/{boarder_id}", json={"name": "Alicia", "bed": "602A"}
        )
        resp = fresh_client.post(
            "/",
            data={
                "report_month": "2026-08",
                "log_file": (io.BytesIO(b"Name,Transaction Time\nALICIA,07:42\n"), "log.csv"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 302
        with app_module.connect() as conn:
            saved = storage.get_month_report(conn, "2026-08")
        assert {r.name for r in saved} == {"ALICIA", "BOB"}


class TestRemovedPostRoutes:
    def test_old_edit_post_route_404s(self, fresh_client):
        with app_module.connect() as conn:
            boarder_id = storage.list_boarders(conn)[0].id
        resp = fresh_client.post(
            f"/boarders/{boarder_id}/edit", data={"name": "Alicia", "bed": "602A"}
        )
        assert resp.status_code == 404

    def test_old_delete_post_route_404s(self, fresh_client):
        with app_module.connect() as conn:
            boarder_id = storage.list_boarders(conn)[0].id
        resp = fresh_client.post(f"/boarders/{boarder_id}/delete")
        assert resp.status_code == 404


class TestBoarderBulkImport:
    def test_import_csv_replaces_roster(self, fresh_client):
        resp = fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (io.BytesIO(b"Name,Bed\nCarol,601C\nDana,601D\n"), "roster.csv"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 302
        html = fresh_client.get("/boarders").get_data(as_text=True)
        assert "Carol" in html
        assert "Dana" in html
        assert "ALICE" not in html

    def test_import_empty_csv_replaces_roster_with_empty(self, fresh_client):
        resp = fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (io.BytesIO(b"Name,Bed\n"), "empty.csv"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 302
        with app_module.connect() as conn:
            assert storage.list_boarders(conn) == []

    def test_import_all_skipped_rows_replaces_roster_with_empty(self, fresh_client):
        resp = fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (io.BytesIO(b"Name,Bed\n,bad\nNoBed,\n"), "skipped.csv"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 302
        with app_module.connect() as conn:
            assert storage.list_boarders(conn) == []

    def test_import_exact_duplicate_names_collapse_last_wins(self, fresh_client):
        resp = fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (
                    io.BytesIO(b"Name,Bed\nCarol,601C\nCarol,602C\n"),
                    "roster.csv",
                ),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 302
        with app_module.connect() as conn:
            boarders = storage.list_boarders(conn)
        assert [(b.display_name, b.bed) for b in boarders] == [("Carol", "602C")]

    def test_import_case_variant_duplicate_names_collapse_last_wins(self, fresh_client):
        resp = fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (
                    io.BytesIO(b"Name,Bed\nCarol,601C\ncarol,602C\n"),
                    "roster.csv",
                ),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 302
        with app_module.connect() as conn:
            boarders = storage.list_boarders(conn)
        assert [(b.display_name, b.bed) for b in boarders] == [("carol", "602C")]

    def test_import_rejects_missing_file(self, fresh_client):
        resp = fresh_client.post("/boarders/import", data={})
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "file" in html.lower()

    def test_empty_roster_empty_state_points_at_tab(self, fresh_client):
        fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (io.BytesIO(b"Name,Bed\n"), "empty.csv"),
            },
            content_type="multipart/form-data",
        )
        html = fresh_client.get("/boarders").get_data(as_text=True)
        assert "Add a boarder" in html
        assert "namelist.csv" not in html

    def test_empty_roster_rejects_monthly_log_import(self, fresh_client):
        fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (io.BytesIO(b"Name,Bed\n"), "empty.csv"),
            },
            content_type="multipart/form-data",
        )
        resp = fresh_client.post(
            "/",
            data={
                "report_month": "2026-08",
                "log_file": (io.BytesIO(b"Name,Transaction Time\nZED,07:42\n"), "log.csv"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "master list is missing or empty" in html

    def test_import_roster_used_by_ingestion(self, fresh_client):
        fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (io.BytesIO(b"Name,Bed\nZed,999\n"), "roster.csv"),
            },
            content_type="multipart/form-data",
        )
        resp = fresh_client.post(
            "/",
            data={
                "report_month": "2026-08",
                "log_file": (io.BytesIO(b"Name,Transaction Time\nZED,07:42\n"), "log.csv"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 302
        with app_module.connect() as conn:
            saved = storage.get_month_report(conn, "2026-08")
        assert {r.name for r in saved} == {"ZED"}


class TestBoarderBulkImportDuplicateBed:
    def test_duplicate_bed_import_shows_inline_error_and_keeps_roster(self, fresh_client):
        resp = fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (io.BytesIO(b"Name,Bed\nCarol,601A\nDana,601A\n"), "roster.csv"),
            },
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "assigned to both" in html
        assert "601A" in html
        with app_module.connect() as conn:
            boarders = storage.list_boarders(conn)
        assert [(b.normalized_name, b.bed) for b in boarders] == [
            ("ALICE", "601A"),
            ("BOB", "601B"),
        ]

    def test_duplicate_bed_import_does_not_partially_replace(self, fresh_client):
        resp = fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (
                    io.BytesIO(b"Name,Bed\nCarol,601A\nDana,601A\nEve,601B\n"),
                    "roster.csv",
                ),
            },
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        with app_module.connect() as conn:
            boarders = storage.list_boarders(conn)
        assert [(b.normalized_name, b.bed) for b in boarders] == [
            ("ALICE", "601A"),
            ("BOB", "601B"),
        ]

    def test_duplicate_bed_import_after_prior_import_keeps_prior_roster(self, fresh_client):
        fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (io.BytesIO(b"Name,Bed\nCarol,601C\nDana,601D\n"), "roster.csv"),
            },
            content_type="multipart/form-data",
        )
        resp = fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (io.BytesIO(b"Name,Bed\nZed,601C\nWye,601C\n"), "roster.csv"),
            },
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        with app_module.connect() as conn:
            boarders = storage.list_boarders(conn)
        assert [(b.normalized_name, b.bed) for b in boarders] == [
            ("CAROL", "601C"),
            ("DANA", "601D"),
        ]

    def test_import_matching_existing_bed_is_still_valid(self, fresh_client):
        resp = fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (io.BytesIO(b"Name,Bed\nCarol,601A\n"), "roster.csv"),
            },
            content_type="multipart/form-data",
        )

        assert resp.status_code == 302
        with app_module.connect() as conn:
            boarders = storage.list_boarders(conn)
        assert [(b.normalized_name, b.bed) for b in boarders] == [("CAROL", "601A")]


class TestBoarderExport:
    def test_export_returns_csv_download(self, fresh_client):
        resp = fresh_client.get("/boarders/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("Content-Type", "")
        body = resp.get_data(as_text=True)
        rows = [line.split(",") for line in body.strip().splitlines()]
        assert rows[0] == ["Name", "Bed"]
        assert ["ALICE", "601A"] in rows
        assert ["BOB", "601B"] in rows

    def test_export_matches_import_roundtrip(self, fresh_client):
        fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (io.BytesIO(b"Name,Bed\nCarol,601C\n"), "roster.csv"),
            },
            content_type="multipart/form-data",
        )
        resp = fresh_client.get("/boarders/export")
        body = resp.get_data(as_text=True)
        rows = [line.split(",") for line in body.strip().splitlines()]
        assert rows == [["Name", "Bed"], ["Carol", "601C"]]


LOG_CSV = "Name,Transaction Time\nALICE,07:45\n"


class TestImportPostRedirectGet:
    def _import(self, client, month="2026-07", body=LOG_CSV, filename="log.csv"):
        return client.post(
            "/",
            data={
                "report_month": month,
                "log_file": (io.BytesIO(body.encode("utf-8")), filename),
            },
            content_type="multipart/form-data",
        )

    def test_successful_import_redirects_with_month_query(self, fresh_client):
        resp = self._import(fresh_client)

        assert resp.status_code == 302
        assert "month=2026-07" in resp.headers["Location"]

    def test_redirect_target_renders_archive_with_message_and_auto_open(self, fresh_client):
        resp = self._import(fresh_client)

        page = fresh_client.get(resp.headers["Location"])
        html = page.get_data(as_text=True)
        assert page.status_code == 200
        assert "Monthly report saved for" in html
        assert "with 1 boarder recorded as late" in html
        assert 'const initialMonthToOpen = "2026-07";' in html

    def test_mixed_import_redirect_shows_diagnostics_in_page(self, fresh_client):
        resp = self._import(
            fresh_client,
            body="Name,Transaction Time\nALICE,07:45\nGHOST,07:46\nBOB,7:47\n",
        )

        assert resp.status_code == 302
        page = fresh_client.get(resp.headers["Location"])
        html = page.get_data(as_text=True)
        assert "Unmatched names: GHOST." in html
        assert "Unparseable times: BOB" in html
        assert "7:47" in html

    def test_following_redirect_does_not_duplicate_the_import(self, fresh_client):
        resp = self._import(fresh_client)
        fresh_client.get(resp.headers["Location"])

        with app_module.connect() as conn:
            months = storage.list_months(conn)
        assert len(months) == 1
        assert months[0].month == "2026-07"

    def test_rejected_import_renders_inline_without_redirect(self, fresh_client):
        resp = self._import(fresh_client, body="")

        assert resp.status_code == 200
        assert "Error" in resp.get_data(as_text=True)
        with app_module.connect() as conn:
            assert storage.list_months(conn) == []

    def test_missing_month_label_renders_inline_without_redirect(self, fresh_client):
        resp = self._import(fresh_client, month="")

        assert resp.status_code == 200
        assert "Error" in resp.get_data(as_text=True)
        with app_module.connect() as conn:
            assert storage.list_months(conn) == []

    def test_stale_month_param_loads_gracefully(self, fresh_client):
        page = fresh_client.get("/?month=2020-01")
        html = page.get_data(as_text=True)

        assert page.status_code == 200
        assert "const initialMonthToOpen = null;" in html

    def test_browser_refresh_of_redirect_target_shows_no_import_form_resubmit(self, fresh_client):
        resp = self._import(fresh_client)
        location = resp.headers["Location"]

        fresh_client.get(location)
        again = fresh_client.get(location)

        assert again.status_code == 200
        with app_module.connect() as conn:
            months = storage.list_months(conn)
        assert len(months) == 1


class TestMonthApi:
    def test_api_returns_ordered_explicit_row_list(self, fresh_client):
        with app_module.connect() as conn:
            storage.save_month(
                conn,
                [
                    record("ALICE", "102", 2, 5, 7, display_name="Alice"),
                    record("BOB", "101", 1, 19, 20, display_name="Bob"),
                ],
                "2026-07",
            )
        response = fresh_client.get("/api/month/2026-07")

        assert response.status_code == 200
        body = response.get_json()
        assert body["month"] == "2026-07"
        assert isinstance(body["boarders"], list)
        assert body["boarders"][0] == {
            "name": "BOB",
            "display_name": "Bob",
            "bed": "101",
            "frequency": 1,
            "total_minutes": 19,
            "total_points": 20,
        }
        assert body["boarders"][1]["name"] == "ALICE"

    def test_api_rows_carry_all_typed_fields(self, fresh_client):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7, display_name="Alicia")], "2026-07")
        body = fresh_client.get("/api/month/2026-07").get_json()

        assert set(body["boarders"][0].keys()) == {
            "name",
            "display_name",
            "bed",
            "frequency",
            "total_minutes",
            "total_points",
        }

    def test_api_returns_server_ordered_rows_by_bed_rule(self, fresh_client):
        with app_module.connect() as conn:
            storage.save_month(
                conn,
                [
                    record("A", bed="10"),
                    record("B", bed="9A"),
                    record("C", bed="101A"),
                    record("D", bed="101"),
                ],
                "2026-07",
            )
        body = fresh_client.get("/api/month/2026-07").get_json()

        assert [row["name"] for row in body["boarders"]] == ["B", "A", "D", "C"]

    def test_api_unknown_month_returns_404(self, fresh_client):
        response = fresh_client.get("/api/month/1999-01")
        assert response.status_code == 404
        assert response.get_json()["error"]


class TestServerOwnedReportRows:
    def test_page_no_longer_contains_client_sorting_helpers(self):
        html = home_html()
        assert "toTitleCase" not in html
        assert "bedComparator" not in html
        assert "sortMonthDetail" not in html
        assert "sort-indicator" not in html

    def test_client_renders_server_rows_and_display_names(self):
        html = home_html()
        assert "monthDetailRows = data.boarders;" in html
        assert "row.display_name" in html

    def test_page_renders_import_copy_without_generate(self):
        html = home_html()
        assert "Import and Save" in html
        assert "Generate" not in html

    def test_history_button_uses_boarder_history_terminology(self):
        html = home_html()
        assert "Search Boarder History" in html
        assert ">Search History</button>" not in html

    def test_empty_history_uses_boarder_history_terminology(self, fresh_client):
        resp = fresh_client.post("/", data={"search_name": "ZZZ"})
        html = resp.get_data(as_text=True)
        assert "No Boarder History entries matched your search." in html
        assert "No history records matched" not in html

    def test_empty_reports_copy_uses_import(self, fresh_client):
        html = fresh_client.get("/").get_data(as_text=True)
        assert "Import a Monthly Log to get started!" in html
        assert "Upload a monthly log" not in html


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
