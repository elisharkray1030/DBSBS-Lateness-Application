import io
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import ClassVar
from urllib.parse import urlparse

import pytest
from helpers import month_row, open_month_detail, record, seed_punishments
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


TAB_LABELS = {
    "reports": "View Reports in Database",
    "history": "Search Boarder History",
    "consequences": "Punishments",
    "boarders": "Boarders",
}


def panel_html(html, panel_id):
    match = re.search(rf'<section id="{panel_id}".*?</section>', html, re.S)
    assert match is not None, f"no panel found for {panel_id!r}"
    return match.group(0)


def open_month_picker(page):
    """Opens the Report Month popover from the reports tab."""
    page.locator('.tab-link[data-tab="reports"]').click()
    page.locator("#report-month-toggle").click()
    page.wait_for_selector("#month-picker-popover:not(.hidden)")


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
    def test_all_tabs_are_reachable_when_boarder_rows_are_rendered(self, fresh_client, browser_page):
        html = fresh_client.get("/").get_data(as_text=True)

        page = browser_page
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.set_content(html)

        for tab_name in ("history", "boarders", "reports"):
            page.locator(f'.tab-link[data-tab="{tab_name}"]').click()
            assert page.locator(f"#{tab_name}").evaluate(
                "panel => panel.classList.contains('active')"
            ), page_errors

        assert not page_errors


class TestImportMonthPicker:
    def _report_month_input(self):
        match = re.search(r'<input[^>]*id="report_month"[^>]*>', home_html())
        assert match is not None, "no Report Month input found"
        return match.group(0)

    def test_report_month_is_editable_text_with_pattern_hint_and_required(self):
        tag = self._report_month_input()
        assert 'type="text"' in tag
        assert 'name="report_month"' in tag
        assert "required" in tag
        assert 'placeholder="YYYY-MM"' in tag
        assert 'pattern="' in tag

    def test_report_month_has_calendar_toggle_button_wired_to_popover(self):
        html = home_html()
        match = re.search(r'<button[^>]*id="report-month-toggle"[^>]*>', html)
        assert match is not None, "no month picker toggle button found"
        tag = match.group(0)
        assert 'type="button"' in tag
        assert 'aria-haspopup="dialog"' in tag
        assert 'aria-expanded="false"' in tag
        assert 'aria-controls="month-picker-popover"' in tag

    def test_popover_renders_dialog_semantics_year_header_and_twelve_months(self):
        html = home_html()
        popover = re.search(r'<div[^>]*id="month-picker-popover"[^>]*>', html)
        assert popover is not None, "no month picker popover found"
        assert 'role="dialog"' in popover.group(0)
        assert "hidden" in popover.group(0)
        assert "month-grid" in html
        assert re.search(r'<button[^>]*aria-label="Previous year"', html)
        assert re.search(r'<button[^>]*aria-label="Next year"', html)
        assert len(re.findall(r'class="month-option', html)) == 12

    def test_month_picker_lives_inside_print_excluded_upload_panel(self):
        html = home_html()
        title_index = html.index("Import Monthly Log")
        upload_panel_start = html.rindex('<div class="upload-panel">', 0, title_index)
        reports_section_end = html.index("</section>", title_index)
        popover_index = html.index('id="month-picker-popover"')
        assert (
            upload_panel_start < popover_index < reports_section_end
        ), "popover must stay inside the upload panel that print styles exclude"

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
        assert '<th scope="col" class="boarder-actions"></th>' in panel
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
        assert '<th scope="col">Bed</th>' in html
        assert '<th scope="col">Boarder Name</th>' in html


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


class TestMigrationCollisionBanner:
    """The stored-key migration counts rows that keep their legacy Match Key;
    the home tab surfaces that count as a one-shot banner per session, and
    databases with nothing to report never see it."""

    def _client_over_seeded_db(self, tmp_path, monkeypatch, collide):
        db_path = tmp_path / "banner.db"
        monkeypatch.setattr(app_module, "DB_PATH", str(db_path))
        monkeypatch.setattr(app_module, "NAMELIST_PATH", str(tmp_path / "missing.csv"))
        with app_module.connect() as conn:
            storage.create_schema(conn)
            if collide:
                colliding_boarder_rows = [
                    ("CHEN, WEI", "Chen Wei A", "701A"),
                    ("CHEN  WEI", "Chen Wei B", "701B"),
                ]
                conn.executemany(
                    "INSERT INTO boarders (normalized_name, display_name, bed) VALUES (?, ?, ?)",
                    colliding_boarder_rows,
                )
            else:
                storage.add_boarder(conn, "ALICE", "Alice", "601A")
            conn.commit()
        app_module.init_db()
        return app_module.app.test_client()

    def test_nonzero_skips_flash_banner_once_per_session(self, tmp_path, monkeypatch):
        client = self._client_over_seeded_db(tmp_path, monkeypatch, collide=True)

        first = unescape(client.get("/").get_data(as_text=True))
        assert "legacy Match Key" in first
        assert "1 stored row kept" in first

        again = client.get("/").get_data(as_text=True)
        assert "legacy Match Key" not in again

    def test_zero_skips_never_flash_banner(self, tmp_path, monkeypatch):
        client = self._client_over_seeded_db(tmp_path, monkeypatch, collide=False)

        for _ in range(2):
            html = client.get("/").get_data(as_text=True)
            assert "legacy Match Key" not in html


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
            seed_punishments(conn, boarders=[record("ALICE", "601A", 2, 5, 7)])
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

    def test_empty_roster_empty_state_names_the_master_list(self, fresh_client):
        fresh_client.post(
            "/boarders/import",
            data={
                "boarder_csv": (io.BytesIO(b"Name,Bed\n"), "empty.csv"),
            },
            content_type="multipart/form-data",
        )
        html = fresh_client.get("/boarders").get_data(as_text=True)
        # Same sentence the client injects when the last boarder is removed
        # live, so both renderings stay glossary-clean.
        assert "Add a boarder here, or import a CSV to replace it." in html

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
        assert "message=" not in resp.headers["Location"]

    def test_redirect_target_renders_archive_with_message_and_auto_open(self, fresh_client):
        resp = self._import(fresh_client)

        page = fresh_client.get(resp.headers["Location"])
        html = unescape(page.get_data(as_text=True))
        assert page.status_code == 200
        assert "Monthly report saved for '2026-07'." in html
        assert "2 Boarders recorded, 1 with lateness." in html
        assert 'const initialMonthToOpen = "2026-07";' in html

    def test_mixed_import_redirect_shows_confirmation_only(self, fresh_client):
        resp = self._import(
            fresh_client,
            body="Name,Transaction Time\nALICE,07:45\nGHOST,07:46\nBOB,07:99\n",
        )

        assert resp.status_code == 302
        page = fresh_client.get(resp.headers["Location"])
        html = unescape(page.get_data(as_text=True))
        assert "Monthly report saved for '2026-07'." in html
        assert "1 log row matched no Boarder." in html
        assert "1 log row had an unreadable Transaction Time." in html
        assert "Unmatched names: GHOST." not in html
        assert "Unparseable times: BOB" not in html
        assert "07:99" not in html

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
    def test_page_restores_sortable_report_headers_and_indicators(self):
        html = home_html()
        for field in ("bed", "name", "frequency", "minutes", "points"):
            assert f"sortMonthDetail('{field}')" in html
        assert "sort-indicator" in html
        assert "sort-asc" in html
        assert "sort-desc" in html

    def test_sort_headers_are_keyboard_operable_buttons(self):
        html = home_html()
        assert html.count('<button type="button" class="sort-btn"') == 5
        assert "aria-sort" in html

    def test_server_and_client_tables_render_bare_numbers_units_in_headers(self):
        html = home_html()
        assert "<td>${row.total_minutes}</td>" in html
        assert " mins</td>" not in html

    def test_history_table_formats_numbers_like_month_report(self, fresh_client):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 19, 21)], "2026-03")
        html = fresh_client.get("/?search_name=ALICE").get_data(as_text=True)
        history_panel = re.search(r'<section id="history".*?</section>', html, re.S).group(0)
        assert "<td>19</td>" in history_panel
        assert "<td>19 mins</td>" not in history_panel

    def test_browser_sort_headers_reach_and_announce_direction(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(
                conn,
                [
                    record("ALICE", "101", 2, 5, 7),
                    record("BOB", "102", 4, 8, 12),
                ],
                "2026-07",
            )
        html = fresh_client.get("/").get_data(as_text=True)
        rows = [
            month_row("ALICE", "101", 2, 5, 7),
            month_row("BOB", "102", 4, 8, 12),
        ]

        page = browser_page
        page.set_content(html)
        open_month_detail(page, rows)

        frequency_header = page.locator("#month-detail-table thead th").nth(2)
        assert frequency_header.get_attribute("aria-sort") == "none"

        frequency_header.locator("button.sort-btn").focus()
        page.keyboard.press("Enter")
        names = page.locator("#month-detail-body tr td:nth-child(2)").all_text_contents()
        assert names == ["Alice", "Bob"]
        assert frequency_header.get_attribute("aria-sort") == "ascending"

        page.keyboard.press("Enter")
        names = page.locator("#month-detail-body tr td:nth-child(2)").all_text_contents()
        assert names == ["Bob", "Alice"]
        assert frequency_header.get_attribute("aria-sort") == "descending"

        bed_header = page.locator("#month-detail-table thead th").nth(0)
        assert bed_header.get_attribute("aria-sort") == "none"


    def test_report_sorting_keeps_server_fields_and_resets_for_each_month(self):
        html = home_html()
        assert "row.display_name" in html
        assert "row.total_points" in html
        assert "monthDetailSort = { field: 'bed', direction: 'asc' };" in html

    def test_client_renders_server_rows_and_display_names(self):
        html = home_html()
        assert "monthDetailRows = data.boarders;" in html
        assert "row.display_name" in html

    def test_report_headers_sort_rows_and_reset_for_a_new_month(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE")], "2026-07")
        html = fresh_client.get("/").get_data(as_text=True)
        rows = [
            month_row("BOB", bed="10", frequency=1, total_minutes=2, total_points=3),
            month_row("ALICE", bed="9A", frequency=4, total_minutes=8, total_points=12),
            month_row("CAROL", bed="101A", frequency=2, total_minutes=4, total_points=6),
        ]
        reset_rows = [month_row("DANA", bed="601A", frequency=1, total_minutes=1, total_points=2)]

        page = browser_page
        page.set_content(html)
        page.evaluate(
            """({ rows, resetRows }) => {
                window.fetch = url => Promise.resolve({
                    json: () => Promise.resolve({
                        boarders: url.includes('2026-08') ? resetRows : rows
                    })
                });
                viewMonth('2026-07');
            }""",
            {"rows": rows, "resetRows": reset_rows},
        )
        page.wait_for_function(
            "() => document.querySelectorAll('#month-detail-body tr').length === 3"
        )

        bed_cells = page.locator("#month-detail-body tr td:first-child")
        assert bed_cells.all_text_contents() == ["9A", "10", "101A"]

        page.locator("#month-detail-table thead th").nth(2).click()
        assert page.locator("#month-detail-body tr td:nth-child(2)").all_text_contents() == [
            "Bob",
            "Carol",
            "Alice",
        ]
        assert page.locator("#month-detail-table thead th").nth(2).get_attribute(
            "class"
        ) == "sort-asc"

        page.locator("#month-detail-table thead th").nth(2).click()
        assert page.locator("#month-detail-body tr td:nth-child(2)").all_text_contents() == [
            "Alice",
            "Carol",
            "Bob",
        ]

        page.locator("#month-detail-table thead th").nth(3).click()
        assert page.locator("#month-detail-body tr td:nth-child(2)").all_text_contents() == [
            "Bob",
            "Carol",
            "Alice",
        ]

        page.locator("#month-detail-table thead th").nth(4).click()
        assert page.locator("#month-detail-body tr td:nth-child(2)").all_text_contents() == [
            "Bob",
            "Carol",
            "Alice",
        ]

        page.locator("#month-detail-table thead th").nth(0).click()
        assert page.locator("#month-detail-body tr td:first-child").all_text_contents() == [
            "9A",
            "10",
            "101A",
        ]
        page.locator("#month-detail-table thead th").nth(0).click()
        assert page.locator("#month-detail-body tr td:first-child").all_text_contents() == [
            "101A",
            "10",
            "9A",
        ]

        page.locator("#month-detail-table thead th").nth(1).click()
        assert page.locator("#month-detail-body tr td:nth-child(2)").all_text_contents() == [
            "Alice",
            "Bob",
            "Carol",
        ]

        page.evaluate("() => viewMonth('2026-08')")
        page.wait_for_function(
            "() => document.querySelector('#month-detail-body td')?.textContent === '601A'"
        )
        assert page.locator("#month-detail-table thead th").nth(0).get_attribute(
            "class"
        ) == "sort-asc"

    def test_page_renders_import_copy_without_generate(self):
        html = home_html()
        assert "Import and Save" in html
        assert "Generate" not in html

    def test_history_button_uses_boarder_history_terminology(self):
        html = home_html()
        assert "Search Boarder History" in html
        assert ">Search History</button>" not in html

    def test_empty_history_uses_boarder_history_terminology(self, fresh_client):
        resp = fresh_client.get("/?search_name=ZZZ")
        html = resp.get_data(as_text=True)
        assert "No Boarder History entries matched your search." in html
        assert "No history records matched" not in html

    def test_search_submits_as_native_get_form(self):
        html = home_html()
        assert re.search(
            r'<form action="/" method="get"[^>]*>.*?name="search_name"', html, re.S
        )
        assert "performSearch" not in html

    def test_zero_hit_search_renders_neutral_empty_state_not_success_banner(self, fresh_client):
        html = fresh_client.get("/?search_name=ZZZ").get_data(as_text=True)
        history_panel = re.search(r'<section id="history".*?</section>', html, re.S).group(0)
        assert "No Boarder History entries matched your search." in history_panel
        assert "banner-success" not in history_panel

    def test_hit_search_renders_results_table(self, fresh_client):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-03")
        html = fresh_client.get("/?search_name=ALICE").get_data(as_text=True)
        history_panel = re.search(r'<section id="history".*?</section>', html, re.S).group(0)
        assert "Alice" in history_panel
        assert "<table>" in history_panel

    def test_blank_search_name_prompts_without_results_section(self, fresh_client):
        html = fresh_client.get("/?search_name=").get_data(as_text=True)
        assert "enter a boarder name" in html.lower()
        assert "No Boarder History entries matched your search." not in html

    def test_browser_first_search_after_load_shows_miss_feedback(self, fresh_client, browser_page):

        def fulfill_from_server(route):
            parsed = urlparse(route.request.url)
            if parsed.path.startswith("/static"):
                route.fulfill(
                    body=fresh_client.get(parsed.path).get_data(),
                    content_type="image/png",
                )
                return
            target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            response = fresh_client.get(target)
            route.fulfill(
                status=response.status_code,
                body=response.get_data(as_text=True),
                content_type="text/html",
            )

        page = browser_page
        page.route("**/*", fulfill_from_server)
        page.goto("http://dbs.local/")
        page.locator('.tab-link[data-tab="history"]').click()
        page.fill("#search_name", "ZZZ")
        page.locator("#history button[type=submit]").click()

        page.wait_for_selector("#history .empty-state")
        assert page.locator("#history .banner-success").count() == 0
        assert "No Boarder History entries matched your search." in (
            page.locator("#history .empty-state").text_content()
        )

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
        response = client.post(
            "/assign/2026-03",
            data={"deadline": "2026-04-10", "assign": ["ALICE", "BOB"]},
        )

        assert response.status_code == 302
        with app_module.connect() as conn:
            rows = storage.list_punishments(conn, statuses=("assigned",))
            assert {r.normalized_name for r in rows} == {"ALICE", "BOB"}

    def test_unchecked_boarders_are_not_assigned(self):
        response = client.post(
            "/assign/2026-03",
            data={"deadline": "2026-04-10", "assign": ["ALICE"]},
        )

        assert response.status_code == 302
        with app_module.connect() as conn:
            rows = storage.list_punishments(conn, statuses=("assigned",))
            assert {r.normalized_name for r in rows} == {"ALICE"}

    def test_checked_boarders_are_assigned(self):
        response = client.post(
            "/assign/2026-03",
            data={"deadline": "2026-04-10", "assign": ["ALICE", "BOB"]},
        )

        assert response.status_code == 302
        with app_module.connect() as conn:
            rows = storage.list_punishments(conn, statuses=("assigned",))
            assert {r.normalized_name for r in rows} == {"ALICE", "BOB"}

    def test_confirmation_names_boarders_by_display_name(self):
        response = client.post(
            "/assign/2026-03",
            data={"deadline": "2026-04-10", "assign": ["ALICE"]},
            follow_redirects=True,
        )

        html = unescape(response.get_data(as_text=True))
        assert "Alice" in html
        assert "ALICE" not in html

    def test_missing_deadline_redirects_to_consequences_with_error(self):
        response = client.post("/assign/2026-03", data={})

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/consequences")
        page = client.get(response.headers["Location"])
        html = page.get_data(as_text=True)
        assert "banner-error" in html
        assert "deadline" in html.lower()

    def test_rejected_assignment_preserves_consequences_filters(self):
        response = client.post(
            "/assign/2026-03",
            data={
                "deadline": "",
                "month": "2026-03",
                "status": "assigned",
                "show_all": "1",
            },
        )

        assert response.status_code == 302
        location = response.headers["Location"]
        assert location.startswith("/consequences")
        assert "month=2026-03" in location
        assert "status=assigned" in location
        assert "show_all=1" in location

    def test_assign_redirect_shows_message_and_opens_month(self):
        response = client.post(
            "/assign/2026-03",
            data={"deadline": "2026-04-10", "assign": ["ALICE", "BOB"]},
            follow_redirects=True,
        )

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Assigned 2 punishments" in html
        assert "report_month" in html

    def test_assign_success_url_carries_no_message_payload(self):
        response = client.post("/assign/2026-03", data={"deadline": "2026-04-10"})

        assert response.status_code == 302
        assert "message=" not in response.headers["Location"]

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
            seed_punishments(
                conn,
                boarders=[
                    record("ALICE", "101", 2, 5, 7),
                    record("BOB", "102", 1, 19, 20),
                ],
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

    def test_consequences_tab_links_to_server_view(self):
        html = client.get("/").get_data(as_text=True)
        assert re.search(
            r'<a class="tab-link [^"]*" data-tab="consequences" href="/consequences">',
            html,
        )

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

    def test_month_filter_includes_months_only_present_in_punishments(self):
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn,
                month="2026-04",
                boarders=[record("ALICE", "101", 2, 5, 7)],
                deadline="2026-05-10",
                assigned_at="2026-04-01T09:00:00+00:00",
            )

        html = client.get("/consequences").get_data(as_text=True)

        assert 'value="2026-04"' in html

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

    def test_default_view_excludes_submitted_punishments(self):
        with app_module.connect() as conn:
            row = storage.list_punishments(conn, statuses=("assigned",))[0]
            storage.transition_punishment(
                conn, row.id, "submitted", timestamp="2026-04-09T09:00:00+00:00"
            )

        html = client.get("/consequences").get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)
        assert "Alice" not in panel
        assert "Bob" in panel

    def test_show_all_and_status_filter_preserve_selected_filters(self):
        with app_module.connect() as conn:
            row = storage.list_punishments(conn, statuses=("assigned",))[0]
            storage.transition_punishment(
                conn, row.id, "submitted", timestamp="2026-04-09T09:00:00+00:00"
            )

        response = client.get(
            "/consequences?show_all=1&month=2026-03&status=submitted"
        )

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)
        assert "Alice" in panel
        assert "Bob" not in panel
        assert re.search(r'<option value="2026-03" selected>', panel)
        assert re.search(r'<option value="submitted" selected>', panel)
        assert 'name="month" value="2026-03"' in panel
        assert 'name="status" value="submitted"' in panel

    def test_empty_view_has_server_rendered_empty_state(self):
        with app_module.connect() as conn:
            conn.execute("DELETE FROM punishments")
            conn.commit()

        html = client.get("/consequences").get_data(as_text=True)
        assert "No punishments to show." in html

    def test_overdue_action_is_hidden_before_deadline(self):
        with app_module.connect() as conn:
            conn.execute("DELETE FROM punishments")
            seed_punishments(conn, deadline="2099-01-01", include_report=False)

        html = client.get("/consequences").get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)
        assert "Alice" in panel
        assert "Mark overdue" not in panel

    def test_filter_options_use_humanized_labels(self):
        html = client.get("/consequences").get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)
        assert re.search(r'<option value="phone_held" ?(selected)?>Phone held</option>', panel)
        assert re.search(r'<option value="voided" ?(selected)?>Voided</option>', panel)
        assert "<option>phone_held</option>" not in panel

    def test_group_headings_and_status_cells_use_humanized_labels(self):
        with app_module.connect() as conn:
            row = storage.list_punishments(conn, statuses=("assigned",))[0]
            storage.transition_punishment(
                conn, row.id, "phone_held", timestamp="2026-04-11T09:00:00+00:00"
            )

        html = client.get("/consequences").get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)
        assert '>Phone held</h4>' in panel
        assert "<td>Phone held</td>" in panel
        assert "<td>phone_held</td>" not in panel

    def test_consequences_table_has_last_action_column_with_timestamp(self):
        with app_module.connect() as conn:
            row = storage.list_punishments(conn, statuses=("assigned",))[0]
            storage.transition_punishment(
                conn, row.id, "overdue", timestamp="2026-04-11T10:30:00+00:00"
            )

        html = client.get("/consequences").get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)
        assert '<th scope="col">Last action</th>' in panel
        assert "2026-04-11 10:30" in panel

    def test_toolbar_has_no_punishments_subheading(self):
        html = client.get("/consequences").get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)
        assert "<h3>Punishments</h3>" not in panel

    def test_filter_button_is_gone_and_selects_auto_submit(self):
        html = client.get("/consequences").get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)

        assert ">Filter</button>" not in panel

        month_select = re.search(r'<select id="consequences-month"[^>]*>', panel)
        status_select = re.search(r'<select id="consequences-status"[^>]*>', panel)
        assert month_select is not None and status_select is not None
        assert 'onchange="this.form.submit()"' in month_select.group(0)
        assert 'onchange="this.form.submit()"' in status_select.group(0)

    def test_toolbar_uses_dedicated_consequences_class(self):
        html = client.get("/consequences").get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)
        assert '<div class="consequences-toolbar">' in panel
        assert "month-detail-toolbar" not in panel

    def test_result_count_reads_showing_x_of_y(self):
        html = client.get("/consequences").get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)
        assert re.search(
            r'<p class="consequences-count" aria-live="polite">Showing 2 of 2 punishments</p>',
            panel,
        )

    def test_result_count_reflects_active_filters(self):
        with app_module.connect() as conn:
            row = storage.list_punishments(conn, statuses=("assigned",))[0]
            storage.transition_punishment(
                conn, row.id, "submitted", timestamp="2026-04-09T09:00:00+00:00"
            )

        html = client.get("/consequences").get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)
        assert "Showing 1 of 2 punishments" in panel

    def test_empty_view_counts_zero_of_zero(self):
        with app_module.connect() as conn:
            conn.execute("DELETE FROM punishments")
            conn.commit()

        html = client.get("/consequences").get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)
        assert "Showing 0 of 0 punishments" in panel

    def test_voided_punishments_do_not_count_in_the_total(self):
        with app_module.connect() as conn:
            row = storage.list_punishments(conn, statuses=("assigned",))[0]
            storage.transition_punishment(
                conn, row.id, "voided", timestamp="2026-04-05T09:00:00+00:00"
            )

        html = client.get("/consequences").get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)
        assert "Showing 1 of 1 punishment</p>" in panel


class TestTransitionRoute:
    @pytest.fixture(autouse=True)
    def _seed_punishment(self):
        with app_module.connect() as conn:
            conn.execute("DELETE FROM punishments")
            conn.execute("DELETE FROM boarder_history")
            conn.commit()
            seed_punishments(conn)

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

    def test_submitted_punishment_can_be_voided(self):
        punishment_id = self._alice_id()
        with app_module.connect() as conn:
            storage.transition_punishment(
                conn, punishment_id, "submitted", timestamp="2026-04-09T09:00:00+00:00"
            )

        html = client.get("/consequences?show_all=1").get_data(as_text=True)
        panel = re.search(r'<section id="consequences".*?</section>', html, re.S).group(0)
        row = re.search(
            rf'<tr data-punishment-id="{punishment_id}">.*?</tr>', panel, re.S
        )
        assert row is not None
        assert 'name="to" value="voided"' in row.group(0)

        response = client.post(
            f"/punishment/{punishment_id}/transition",
            data={"to": "voided", "void_reason": "later exempted"},
        )

        assert response.status_code == 302
        with app_module.connect() as conn:
            row = storage.get_punishment(conn, punishment_id)
        assert row.status == "voided"
        assert row.submitted_at == "2026-04-09T09:00:00+00:00"
        assert row.void_reason == "later exempted"

    def test_early_overdue_transition_is_rejected(self):
        with app_module.connect() as conn:
            conn.execute("DELETE FROM punishments")
            seed_punishments(conn, deadline="2099-01-01", include_report=False)
            punishment_id = storage.list_punishments(conn)[0].id

        response = client.post(
            f"/punishment/{punishment_id}/transition", data={"to": "overdue"}
        )

        assert response.status_code == 302
        page = client.get(response.headers["Location"])
        assert b"deadline" in page.data.lower()
        with app_module.connect() as conn:
            assert storage.get_punishment(conn, punishment_id).status == "assigned"

    def test_overdue_transition_succeeds_on_deadline(self):
        deadline = datetime.now(tz=timezone.utc).date().isoformat()
        with app_module.connect() as conn:
            conn.execute("DELETE FROM punishments")
            seed_punishments(conn, deadline=deadline, include_report=False)
            punishment_id = storage.list_punishments(conn)[0].id

        response = client.post(
            f"/punishment/{punishment_id}/transition", data={"to": "overdue"}
        )

        assert response.status_code == 302
        with app_module.connect() as conn:
            assert storage.get_punishment(conn, punishment_id).status == "overdue"

    def test_overdue_transition_succeeds_after_deadline(self):
        deadline = (datetime.now(tz=timezone.utc).date() - timedelta(days=1)).isoformat()
        with app_module.connect() as conn:
            conn.execute("DELETE FROM punishments")
            seed_punishments(conn, deadline=deadline, include_report=False)
            punishment_id = storage.list_punishments(conn)[0].id

        response = client.post(
            f"/punishment/{punishment_id}/transition", data={"to": "overdue"}
        )

        assert response.status_code == 302
        with app_module.connect() as conn:
            assert storage.get_punishment(conn, punishment_id).status == "overdue"

    def test_illegal_transition_rejected(self):
        with app_module.connect() as conn:
            row = storage.list_punishments(conn, statuses=("assigned",))[0]
            storage.transition_punishment(
                conn, row.id, "submitted", timestamp="2026-04-09T09:00:00+00:00"
            )
        response = client.post(f"/punishment/{self._alice_id()}/transition", data={"to": "phone_held"})

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/consequences")
        page = client.get(response.headers["Location"])
        assert b"not allowed" in page.data.lower()


def stub_form_submit(page):
    """Replaces native form.submit() so tests can observe submission without navigation."""
    page.evaluate(
        """() => {
            window.__submitCalled = null;
            HTMLFormElement.prototype.submit = function() {
                window.__submitCalled = this.getAttribute('action');
            };
        }"""
    )


class TestDestructiveActionsNameTarget:
    def test_void_opens_confirm_dialog_naming_boarder_and_month(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            seed_punishments(conn)
        html = fresh_client.get("/consequences").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        stub_form_submit(page)
        page.locator("form.void-form button[type=submit]").click()

        assert page.locator("#confirmModal.show").count() == 1
        message = page.locator("#confirm-modal-message").text_content()
        assert "Alice" in message
        assert "2026-03" in message

        page.locator("#confirmModal .btn-danger").click()
        submitted = page.evaluate("() => window.__submitCalled")
        assert submitted is not None
        assert "/transition" in submitted

    def test_void_reason_stays_in_form_after_confirm(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            seed_punishments(conn)
        html = fresh_client.get("/consequences").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        stub_form_submit(page)
        page.locator("form.void-form input[name=void_reason]").fill("left school")
        page.locator("form.void-form button[type=submit]").click()
        page.locator("#confirmModal .btn-danger").click()

        submitted = page.evaluate("() => window.__submitCalled")
        assert submitted is not None

    def test_delete_report_confirmation_names_exact_month_and_punishment_impact(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 1, 1, 2)], "2026-07")
        html = fresh_client.get("/").get_data(as_text=True)
        rows = [month_row("ALICE", "101", 1, 1, 2)]

        page = browser_page
        page.set_content(html)
        open_month_detail(page, rows)
        page.locator("#month-detail-delete").click()

        message = page.locator("#confirm-modal-message").text_content()
        assert "2026-07" in message
        assert "Punishments" in message


def _relative_luminance(rgb):
    def channel(value):
        value = value / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(rgb1, rgb2):
    lighter = max(_relative_luminance(rgb1), _relative_luminance(rgb2))
    darker = min(_relative_luminance(rgb1), _relative_luminance(rgb2))
    return (lighter + 0.05) / (darker + 0.05)


def _parse_rgb(text):
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    return tuple(int(float(n)) for n in numbers[:3])


class TestConfirmModalDialogSemantics:
    def _consequences_html(self, fresh_client):
        with app_module.connect() as conn:
            seed_punishments(conn)
        return fresh_client.get("/consequences").get_data(as_text=True)

    def _open_modal(self, page):
        stub_form_submit(page)
        page.locator("form.void-form button[type=submit]").click()
        page.wait_for_selector("#confirmModal.show")

    def test_modal_has_dialog_role_accessible_name_and_initial_focus(self, fresh_client, browser_page):
        html = self._consequences_html(fresh_client)

        page = browser_page
        page.set_content(html)
        self._open_modal(page)

        modal = page.locator("#confirmModal")
        assert modal.get_attribute("role") == "dialog"
        assert modal.get_attribute("aria-modal") == "true"
        assert modal.get_attribute("aria-labelledby") == "confirm-modal-title"

        focused = page.evaluate(
            "() => document.activeElement.className"
        )
        assert "btn-danger" in focused

    def test_tab_is_trapped_esc_cancels_and_focus_is_restored(self, fresh_client, browser_page):
        html = self._consequences_html(fresh_client)

        page = browser_page
        page.set_content(html)
        self._open_modal(page)

        for _ in range(4):
            page.keyboard.press("Tab")
            contained = page.evaluate(
                """() => document.getElementById('confirmModal').contains(document.activeElement)"""
            )
            assert contained

        page.keyboard.press("Escape")
        assert page.locator("#confirmModal.show").count() == 0

        restored = page.evaluate(
            """() => document.activeElement.closest('form.void-form') !== null"""
        )
        assert restored


class TestUnsavedEditGuard:
    def _enter_dirty_edit(self, page):
        page.locator("#boarder-edit").click()
        page.locator(".boarder-edit-name").first.fill("Alicia")

    def _fulfill_navigation(self, page):
        page.route(
            "**/*",
            lambda route: route.fulfill(
                body="<html><body>elsewhere</body></html>",
                content_type="text/html",
            ),
        )

    def test_beforeunload_warns_with_unsaved_edits(self, fresh_client, browser_page):
        html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        dialogs = []
        page.on(
            "dialog",
            lambda dialog: (dialogs.append(dialog.type), dialog.accept()),
        )
        self._fulfill_navigation(page)
        page.set_content(html)
        self._enter_dirty_edit(page)
        page.goto("http://dbs.local/elsewhere")

        assert len(dialogs) == 1
        assert dialogs[0] == "beforeunload"

    def test_no_beforeunload_warning_without_unsaved_edits(self, fresh_client, browser_page):
        html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        dialogs = []
        page.on(
            "dialog",
            lambda dialog: (dialogs.append(dialog.type), dialog.accept()),
        )
        self._fulfill_navigation(page)
        page.set_content(html)
        page.locator("#boarder-edit").click()
        page.goto("http://dbs.local/elsewhere")

        assert dialogs == []

    def test_tab_click_guard_unchanged_for_unsaved_edits(self, fresh_client, browser_page):
        html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        self._enter_dirty_edit(page)
        page.locator('.tab-link[data-tab="reports"]').click()

        assert page.locator("#confirmModal.show").count() == 1
        assert page.locator("#boarders.active").count() == 1


class TestAccessibilityPolish:
    def _seed_punishments(self, fresh_client):
        with app_module.connect() as conn:
            seeded = seed_punishments(
                conn,
                boarders=[
                    record("ALICE", "101", 2, 5, 7),
                    record("BOB", "102", 1, 19, 20),
                ],
            )
            bob = next(r for r in seeded if r.normalized_name == "BOB")
            storage.transition_punishment(
                conn, bob.id, "submitted", timestamp="2026-04-11T09:00:00+00:00"
            )

    def test_void_reason_input_has_programmatic_label(self, fresh_client):
        self._seed_punishments(fresh_client)
        html = fresh_client.get("/consequences").get_data(as_text=True)
        assert re.search(
            r'<input type="text" name="void_reason"[^>]*aria-label=', html
        )

    def test_data_table_headers_carry_scope(self, fresh_client):
        self._seed_punishments(fresh_client)
        home = home_html()
        for table_id in ("boarders-table", "month-detail-table"):
            table = re.search(rf'<table[^>]*id="{table_id}".*?</table>', home, re.S)
            assert table is not None
            assert 'scope="col"' in table.group(0)

        history_html = fresh_client.get("/?search_name=ALICE").get_data(as_text=True)
        history_section = re.search(
            r'<section id="history".*?</section>', history_html, re.S
        ).group(0)
        assert '<th scope="col">' in history_section

        consequences_html = fresh_client.get("/consequences").get_data(as_text=True)
        consequences_section = re.search(
            r'<section id="consequences".*?</section>', consequences_html, re.S
        ).group(0)
        assert '<th scope="col">' in consequences_section

    def test_tab_bar_wraps_at_narrow_width(self, browser_page):
        html = home_html()

        page = browser_page
        page.set_viewport_size({"width": 360, "height": 800})
        page.set_content(html)
        wrap = page.evaluate(
            """() => getComputedStyle(document.querySelector('.tabs')).flexWrap"""
        )
        assert wrap == "wrap"
        overflow = page.evaluate(
            """() => {
                const tabs = document.querySelector('.tabs');
                return tabs.scrollWidth > tabs.clientWidth;
            }"""
        )
        assert not overflow

    def test_month_input_shows_visible_focus_indicator(self, browser_page):
        html = home_html()

        page = browser_page
        page.set_content(html)
        page.locator('.tab-link[data-tab="reports"]').click()
        page.focus("#report_month")
        outline_width = page.evaluate(
            """() => getComputedStyle(document.getElementById('report_month')).outlineWidth"""
        )
        outline_style = page.evaluate(
            """() => getComputedStyle(document.getElementById('report_month')).outlineStyle"""
        )
        assert outline_style != "none"
        assert outline_width not in ("", "0px")

    def test_badge_and_disabled_styles_meet_aa_contrast(self, fresh_client, browser_page):
        self._seed_punishments(fresh_client)
        consequences_html = fresh_client.get("/consequences?show_all=1").get_data(as_text=True)
        boarders_html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        page.set_content(consequences_html)

        def computed_colors(selector):
            return page.locator(selector).first.evaluate(
                """el => [
                    getComputedStyle(el).backgroundColor,
                    getComputedStyle(el).color
                ]"""
            )

        for selector in (".due-badge", ".late-badge"):
            background, foreground = computed_colors(selector)
            ratio = _contrast_ratio(_parse_rgb(background), _parse_rgb(foreground))
            assert ratio >= 4.5, f"{selector} contrast {ratio:.2f} < 4.5"

        page.set_content(boarders_html)
        disabled_background, disabled_foreground = computed_colors("#boarder-save")
        ratio = _contrast_ratio(
            _parse_rgb(disabled_background), _parse_rgb(disabled_foreground)
        )
        assert ratio >= 4.5, f"disabled contrast {ratio:.2f} < 4.5"

        enabled_background, _ = computed_colors(".btn-primary:not(:disabled)")
        assert _parse_rgb(disabled_background) != _parse_rgb(enabled_background), (
            "disabled controls must be visually distinct from enabled ones"
        )


class TestMonthPickerPopover:
    def test_open_pick_writes_value_closes_and_restores_focus(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        open_month_picker(page)

        year = datetime.now().astimezone().year
        page.locator(
            f'#month-picker-popover .month-option[data-month-value="{year}-03"]'
        ).click()

        assert page.input_value("#report_month") == f"{year}-03"
        assert page.locator("#month-picker-popover.hidden").count() == 1
        assert page.evaluate("() => document.activeElement.id") == "report_month"
        assert (
            page.get_attribute("#report-month-toggle", "aria-expanded") == "false"
        )

    def test_opening_shows_typed_year_when_field_holds_valid_month(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        page.fill("#report_month", "2023-07")
        open_month_picker(page)

        assert page.text_content("#month-picker-year") == "2023"

    def test_opening_defaults_to_current_year_for_invalid_text(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        page.fill("#report_month", "March 2026")
        open_month_picker(page)

        assert page.text_content("#month-picker-year") == str(
            datetime.now().astimezone().year
        )

    def test_picking_uses_browsed_year_from_typed_value(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        page.fill("#report_month", "2023-07")
        open_month_picker(page)
        page.locator(
            '#month-picker-popover .month-option[data-month-value="2023-11"]'
        ).click()

        assert page.input_value("#report_month") == "2023-11"

    def test_escape_closes_the_popover(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        open_month_picker(page)
        page.keyboard.press("Escape")

        assert page.locator("#month-picker-popover.hidden").count() == 1

    def test_clicking_outside_closes_the_popover(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        open_month_picker(page)
        page.locator("h3.upload-title", has_text="Import Monthly Log").click()

        assert page.locator("#month-picker-popover.hidden").count() == 1


class TestMonthPickerVisualPolish:
    def _computed(self, page, selector, properties):
        return page.locator(selector).first.evaluate(
            """(el, names) => Object.fromEntries(
                names.map(name => [name, getComputedStyle(el)[name]])
            )""",
            properties,
        )

    def _wait_for_hover_settle(self, page):
        """The month-option hover wash animates in over 0.15s; block until it
        lands so computed-style sampling never catches a mid-transition frame."""
        page.wait_for_function(
            """() => {
                const el = document.querySelector('.month-option:hover');
                return el && getComputedStyle(el).backgroundColor === 'rgb(248, 249, 250)';
            }"""
        )

    def test_selected_month_is_solid_navy_and_current_month_outlined(self, browser_page):
        now = datetime.now().astimezone()
        year = now.year
        picked = 12 if now.month == 12 else now.month + 1

        page = browser_page
        page.set_content(home_html())
        page.fill("#report_month", f"{year}-{picked:02d}")
        open_month_picker(page)

        selected = self._computed(
            page,
            ".month-option.selected",
            ["backgroundColor", "color"],
        )
        assert selected["backgroundColor"] == "rgb(29, 43, 83)"
        assert selected["color"] == "rgb(255, 255, 255)"

    def test_unpicked_current_calendar_month_is_outlined(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        open_month_picker(page)

        current_border = self._computed(
            page, ".month-option.current", ["borderColor"]
        )["borderColor"]
        assert current_border == "rgb(29, 43, 83)"

    def test_hover_state_matches_month_card_styling(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        open_month_picker(page)

        option = page.locator(".month-option:not(.selected)").first
        option.hover()
        self._wait_for_hover_settle(page)
        hover_styles = option.evaluate(
            """el => [
                getComputedStyle(el).backgroundColor,
                getComputedStyle(el).borderColor
            ]"""
        )
        assert hover_styles[0] == "rgb(248, 249, 250)"  # --page-bg
        assert hover_styles[1] == "rgb(29, 43, 83)"  # --navy

    def test_popover_radius_and_shadow_match_modal_family(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        open_month_picker(page)

        popover_styles = self._computed(
            page,
            "#month-picker-popover",
            ["borderRadius", "boxShadow"],
        )
        modal_styles = self._computed(
            page,
            "#confirmModal .modal-content",
            ["borderRadius", "boxShadow"],
        )
        assert popover_styles["borderRadius"] == modal_styles["borderRadius"]
        assert popover_styles["boxShadow"] == modal_styles["boxShadow"]

    def test_popover_fits_360px_viewport_without_overflow(self, browser_page):
        page = browser_page
        page.set_viewport_size({"width": 360, "height": 800})
        page.set_content(home_html())
        open_month_picker(page)

        overflow = page.evaluate(
            """() => ({
                horizontal: document.documentElement.scrollWidth > document.documentElement.clientWidth,
                left: document.getElementById('month-picker-popover').getBoundingClientRect().left,
                right: document.getElementById('month-picker-popover').getBoundingClientRect().right
            })"""
        )
        assert not overflow["horizontal"]
        assert overflow["left"] >= 0
        assert overflow["right"] <= 360

    def test_month_picker_text_meets_aa_contrast(self, fresh_client, browser_page):
        page = browser_page
        page.set_content(home_html())

        popover_background = self._computed(
            page, "#month-picker-popover", ["backgroundColor"]
        )["backgroundColor"]

        pairs = []
        for selector in (".month-option", ".month-picker-year"):
            styles = self._computed(page, selector, ["color"])
            pairs.append(
                (f"{selector} on surface", popover_background, styles["color"])
            )

        year = datetime.now().astimezone().year
        page.fill("#report_month", f"{year}-01")
        open_month_picker(page)
        selected = self._computed(
            page,
            ".month-option.selected",
            ["backgroundColor", "color"],
        )
        pairs.append((".month-option.selected", selected["backgroundColor"], selected["color"]))

        page.locator(".month-option:not(.selected)").first.hover()
        self._wait_for_hover_settle(page)
        hover_styles = self._computed(
            page,
            ".month-option:hover",
            ["backgroundColor", "color"],
        )
        pairs.append((".month-option:hover", hover_styles["backgroundColor"], hover_styles["color"]))

        # Walk to the year bound so a chevron actually renders disabled
        # (prior art: the badge test's disabled-control pair).
        prev_button = page.locator("#month-picker-prev-year")
        while not prev_button.is_disabled():
            prev_button.click()
        disabled_styles = self._computed(
            page,
            "#month-picker-prev-year:disabled",
            ["backgroundColor", "color"],
        )
        pairs.append(
            (
                ".month-chevron:disabled",
                disabled_styles["backgroundColor"],
                disabled_styles["color"],
            )
        )

        for label, background, foreground in pairs:
            ratio = _contrast_ratio(_parse_rgb(background), _parse_rgb(foreground))
            assert ratio >= 4.5, f"{label} contrast {ratio:.2f} < 4.5"


class TestMonthPickerKeyboardAccess:
    def test_popover_announces_dialog_semantics_and_labelled_controls(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        open_month_picker(page)

        popover = page.locator("#month-picker-popover")
        assert popover.get_attribute("role") == "dialog"
        assert popover.get_attribute("aria-label")
        assert page.get_attribute("#report-month-toggle", "aria-expanded") == "true"
        assert page.get_attribute(".month-grid", "role") == "grid"
        assert (
            page.locator('#month-picker-popover [role="gridcell"]').count() == 12
        )

        for name in ("Previous year", "Next year"):
            assert page.get_by_role("button", name=name).count() == 1

    def test_focus_is_trapped_while_popover_is_open(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        open_month_picker(page)

        def focus_inside_popover():
            return page.evaluate(
                """() => document.getElementById('month-picker-popover')
                    .contains(document.activeElement)"""
            )

        for _ in range(6):
            page.keyboard.press("Tab")
            assert focus_inside_popover()
        for _ in range(6):
            page.keyboard.press("Shift+Tab")
            assert focus_inside_popover()

    def test_arrow_keys_move_between_months_and_enter_selects(self, browser_page):
        year = datetime.now().astimezone().year
        page = browser_page
        page.set_content(home_html())
        page.fill("#report_month", f"{year}-05")
        open_month_picker(page)

        # Opening focuses the picked month; arrows move through the grid.
        assert (
            page.evaluate(
                "() => document.activeElement.getAttribute('data-month-value')"
            )
            == f"{year}-05"
        )
        page.keyboard.press("ArrowRight")
        assert (
            page.evaluate(
                "() => document.activeElement.getAttribute('data-month-value')"
            )
            == f"{year}-06"
        )
        page.keyboard.press("Enter")

        assert page.input_value("#report_month") == f"{year}-06"
        assert page.locator("#month-picker-popover.hidden").count() == 1
        assert page.evaluate("() => document.activeElement.id") == "report_month"

    def test_escape_restores_focus_to_the_field(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        open_month_picker(page)
        page.keyboard.press("Escape")

        assert page.evaluate("() => document.activeElement.id") == "report_month"

    def test_chevrons_are_real_buttons_disabled_at_year_bounds(self, browser_page):
        min_year = 2020
        max_year = datetime.now().astimezone().year + 1

        page = browser_page
        page.set_content(home_html())
        open_month_picker(page)

        prev_button = page.locator("#month-picker-prev-year")
        next_button = page.locator("#month-picker-next-year")
        assert prev_button.evaluate("el => el.tagName") == "BUTTON"
        assert next_button.evaluate("el => el.tagName") == "BUTTON"
        assert not next_button.is_disabled()

        for expected_year in range(datetime.now().astimezone().year + 1, max_year + 1):
            next_button.click()
            assert page.text_content("#month-picker-year") == str(expected_year)
        assert next_button.is_disabled(), "Next year must disable at the bound"

        for expected_year in range(max_year - 1, min_year - 1, -1):
            prev_button.click()
            assert page.text_content("#month-picker-year") == str(expected_year)
        assert prev_button.is_disabled(), "Previous year must disable at 2020"


class TestPrintOutputsActiveView:
    ROWS: ClassVar = [
        month_row("ALICE", "101", 2, 5, 7),
        month_row("BOB", "102", 1, 19, 20),
    ]

    def _open_report(self, page):
        open_month_detail(page, self.ROWS)

    def _printed_text(self, page):
        page.emulate_media(media="print")
        return page.evaluate("() => document.body.innerText")

    def test_printing_open_month_report_yields_only_that_report(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-07")
        html = fresh_client.get("/").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        self._open_report(page)
        printed = self._printed_text(page)

        assert "Alice" in printed
        assert "101" in printed
        assert "View Reports in Database" not in printed
        assert "Search Boarder History" not in printed
        assert "Assign Punishments" not in printed
        assert "Import Monthly Log" not in printed

    def test_empty_month_detail_skeleton_never_prints(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-07")
        html = fresh_client.get("/").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        printed = self._printed_text(page)
        assert "Report for" not in printed

        display = page.evaluate(
            """() => getComputedStyle(document.getElementById('month-detail')).display"""
        )
        assert display == "none"

    def test_printing_consequences_tab_excludes_other_views_and_skeleton(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            seed_punishments(conn)
        html = fresh_client.get("/consequences").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        printed = self._printed_text(page)

        assert "Alice" in printed
        assert "Points Owed" in printed
        assert "Report for" not in printed
        assert "Add Boarder" not in printed
        assert "View Reports in Database" not in printed

    def test_printing_history_tab_shows_results_not_search_form(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-03")
        html = fresh_client.get("/?search_name=ALICE").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        page.locator('.tab-link[data-tab="history"]').click()
        printed = self._printed_text(page)

        assert "Alice" in printed
        assert "Minutes Late" in printed
        assert "Enter name or partial name" not in printed
        assert "Report for" not in printed

    def test_printing_boarders_tab_shows_roster_not_other_views(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.replace_boarders(conn, [Boarder("ALICE", "Alice", "601A")])
        html = fresh_client.get("/").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        page.locator('.tab-link[data-tab="boarders"]').click()
        printed = self._printed_text(page)

        assert "Alice" in printed
        assert "601A" in printed
        assert "Boarder Name" in printed
        assert "Add Boarder" not in printed
        assert "Replace Master List from CSV" not in printed
        assert "Report for" not in printed
        assert "Points Owed" not in printed
        assert "Minutes Late" not in printed
        assert "View Reports in Database" not in printed

    def test_print_strips_card_chrome_and_pre_scroll_header_shadow(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-07")
        rows = [month_row(f"S{i:02d}", f"{600 + i}A", 1, 10, 10) for i in range(30)]
        html = fresh_client.get("/").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        open_month_detail(page, rows)

        # Scroll first so the sticky header carries its on-screen lift cue,
        # then confirm printing suppresses it along with the card chrome.
        page.evaluate(
            """() => {
                const scroller = document.querySelector('#month-detail .table-scroll');
                scroller.scrollTop = 40;
            }"""
        )
        page.wait_for_selector("#month-detail .table-scroll.scrolled")
        page.emulate_media(media="print")

        chrome = page.evaluate(
            """() => {
                const scroller = document.querySelector('#month-detail .table-scroll');
                const header = document.querySelector('#month-detail .table-scroll.scrolled thead th');
                const styles = getComputedStyle(scroller);
                return {
                    borderStyle: styles.borderTopStyle,
                    borderWidth: styles.borderTopWidth,
                    radius: styles.borderRadius,
                    headerShadow: getComputedStyle(header).boxShadow,
                    headerBackground: getComputedStyle(header).backgroundColor,
                };
            }"""
        )
        assert chrome["borderStyle"] == "none"
        assert chrome["borderWidth"] == "0px"
        assert chrome["radius"] == "0px"
        assert chrome["headerShadow"] == "none"
        # Printed output matches the pre-pass baseline: white header band,
        # not the on-screen page tint (print-color-adjust is forced exact).
        assert chrome["headerBackground"] == "rgb(255, 255, 255)"


class TestAsyncActionsNeverFailSilently:
    ROWS: ClassVar = [month_row("ALICE", "101", 2, 5, 7)]

    def _watch_alerts(self, page):
        page.evaluate("() => { window.__alerted = false; window.alert = () => { window.__alerted = true; }; }")

    def test_month_detail_fetch_shows_loading_indicator(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-07")
        html = fresh_client.get("/").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        page.evaluate(
            """({ rows }) => {
                window.fetch = url => new Promise(resolve => setTimeout(() => resolve({
                    json: () => Promise.resolve({ boarders: rows })
                }), 300));
                viewMonth('2026-07');
            }""",
            {"rows": self.ROWS},
        )
        page.wait_for_selector("#month-detail-loading:not(.hidden)")
        page.wait_for_selector("#month-detail-loading.hidden", state="attached")
        assert page.locator("#month-detail-loading").is_hidden()

    def test_failed_month_fetch_renders_inline_error_not_alert(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-07")
        html = fresh_client.get("/").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        self._watch_alerts(page)
        page.evaluate(
            """() => {
                window.fetch = url => Promise.resolve({
                    json: () => Promise.resolve({ error: 'No report found for 2026-07.' })
                });
                viewMonth('2026-07');
            }"""
        )
        page.wait_for_selector("#month-detail-error:not(.hidden)")
        assert "No report found" in page.locator("#month-detail-error").text_content()
        assert not page.evaluate("() => window.__alerted")

    def test_double_click_save_sends_one_request(self, fresh_client, browser_page):
        html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        page.evaluate(
            """() => {
                window.__saveCount = 0;
                window.fetch = (url, opts) => {
                    if (url.includes('/api/boarders') && opts && opts.method === 'PATCH') {
                        window.__saveCount++;
                        return new Promise(resolve => setTimeout(() => resolve({
                            json: () => Promise.resolve({ ok: true })
                        }), 300));
                    }
                    return Promise.resolve({ json: () => Promise.resolve({ ok: true }) });
                };
            }"""
        )
        page.locator("#boarder-edit").click()
        page.locator(".boarder-edit-name").first.fill("Alicia")
        save_button = page.locator("#boarder-save")
        save_button.click()
        page.evaluate("() => document.getElementById('boarder-save').click()")
        page.wait_for_timeout(600)
        assert page.evaluate("() => window.__saveCount") == 1

    def test_remove_button_disabled_while_delete_in_flight(self, fresh_client, browser_page):
        html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        page.evaluate(
            """() => {
                window.fetch = (url, opts) => {
                    if (opts && opts.method === 'DELETE') {
                        return new Promise(resolve => setTimeout(() => resolve({
                            json: () => Promise.resolve({ ok: true })
                        }), 300));
                    }
                    return Promise.resolve({ json: () => Promise.resolve({ ok: true }) });
                };
            }"""
        )
        page.locator("#boarder-edit").click()
        remove_button = page.locator(".boarder-remove").first
        remove_button.click()
        page.locator("#confirmModal .btn-danger").click()
        assert remove_button.is_disabled()
        page.wait_for_timeout(600)


class TestAssignPanelPositiveConsent:
    def _open_assign_panel(self, page, rows):
        open_month_detail(page, rows)
        page.locator("#month-detail-assign-btn").click()

    def test_panel_prechecks_eligible_boarders_with_positive_labels(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(
                conn,
                [
                    record("ALICE", "101", 2, 5, 7, display_name="Alice"),
                    record("BOB", "102", 1, 19, 20, display_name="Bob"),
                    record("CAROL", "103", 0, 0, 0, display_name="Carol"),
                ],
                "2026-07",
            )
        html = fresh_client.get("/").get_data(as_text=True)
        rows = [
            month_row("ALICE", "101", 2, 5, 7),
            month_row("BOB", "102", 1, 19, 20),
            month_row("CAROL", "103", 0, 0, 0),
        ]

        page = browser_page
        page.set_content(html)
        self._open_assign_panel(page, rows)

        checkboxes = page.locator('#assign-boarders input[type="checkbox"]')
        assert checkboxes.count() == 2
        assert all(checkboxes.nth(i).is_checked() for i in range(2))
        assert all(
            checkboxes.nth(i).get_attribute("name") == "assign"
            for i in range(2)
        )
        labels = page.locator("#assign-boarders label").all_text_contents()
        assert any("Assign punishment to Alice (7 pts)" in text for text in labels)
        assert any("Assign punishment to Bob (20 pts)" in text for text in labels)

        counter = page.locator("#assign-counter")
        assert counter.text_content().strip() == "2 punishments will be assigned."

        checkboxes.nth(0).uncheck()
        assert counter.text_content().strip() == "1 punishment will be assigned."

    def test_submitting_sends_only_checked_boarders(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(
                conn,
                [
                    record("ALICE", "101", 2, 5, 7, display_name="Alice"),
                    record("BOB", "102", 1, 19, 20, display_name="Bob"),
                ],
                "2026-07",
            )
        html = fresh_client.get("/").get_data(as_text=True)
        rows = [
            month_row("ALICE", "101", 2, 5, 7),
            month_row("BOB", "102", 1, 19, 20),
        ]

        page = browser_page
        page.set_content(html)
        self._open_assign_panel(page, rows)
        page.locator("#assign-deadline").fill("2026-08-10")
        page.evaluate(
            """() => {
                window.__submitted = null;
                document.getElementById('assign-form').addEventListener('submit', event => {
                    event.preventDefault();
                    window.__submitted = new URLSearchParams(
                        new FormData(event.target)
                    ).toString();
                });
            }"""
        )
        page.locator('#assign-boarders input[type="checkbox"]').first.uncheck()
        page.locator("#assign-form button[type=\"submit\"]").click()

        submitted = page.evaluate("() => window.__submitted")
        assert "assign=BOB" in submitted
        assert "assign=ALICE" not in submitted
        assert "deadline=2026-08-10" in submitted


class TestTransitionFeedbackLivesOnPage:
    @pytest.fixture(autouse=True)
    def _seed_punishment(self):
        with app_module.connect() as conn:
            conn.execute("DELETE FROM punishments")
            conn.execute("DELETE FROM boarder_history")
            conn.commit()
            seed_punishments(conn)

    def _alice_id(self):
        with app_module.connect() as conn:
            return storage.list_punishments(conn)[0].id

    def _post_transition(self, data):
        return client.post(f"/punishment/{self._alice_id()}/transition", data=data)

    def test_rejected_transition_redirects_to_consequences_preserving_filters(self):
        response = self._post_transition(
            {
                "to": "phone_held",
                "month": "2026-03",
                "status": "assigned",
                "show_all": "1",
            }
        )

        assert response.status_code == 302
        location = response.headers["Location"]
        assert location.startswith("/consequences")
        assert "month=2026-03" in location
        assert "status=assigned" in location
        assert "show_all=1" in location
        assert "message=" not in location

    def test_rejected_transition_renders_inline_error_on_consequences(self):
        response = self._post_transition({"to": "phone_held"})

        page = client.get(response.headers["Location"])
        html = page.get_data(as_text=True)
        assert "banner-error" in html
        assert "not allowed" in html

    def test_successful_transition_preserves_filters_and_flashes_message(self):
        response = self._post_transition(
            {"to": "overdue", "month": "2026-03", "show_all": "1"}
        )

        assert response.status_code == 302
        location = response.headers["Location"]
        assert "month=2026-03" in location
        assert "show_all=1" in location

        page = client.get(location)
        html = page.get_data(as_text=True)
        assert "banner-success" in html
        assert "marked overdue" in html

    def test_punishment_status_unchanged_after_rejected_transition(self):
        self._post_transition({"to": "phone_held"})

        with app_module.connect() as conn:
            assert storage.get_punishment(conn, self._alice_id()).status == "assigned"


class TestChromeConsistency:
    def test_every_panel_opens_with_h2_matching_its_tab_label(self):
        html = home_html()
        for panel_id, label in TAB_LABELS.items():
            panel = panel_html(html, panel_id)
            assert f"<h2>{label}</h2>" in panel, (
                f"{panel_id} panel lacks an <h2> matching its tab label"
            )

    def test_section_subheadings_nest_one_level_beneath_panel_titles(self):
        html = home_html()
        subheadings = {
            "reports": "Import Monthly Log",
            "boarders": "Add Boarder",
        }
        for panel_id, subheading in subheadings.items():
            panel = panel_html(html, panel_id)
            assert re.search(rf"<h3[^>]*>{subheading}</h3>", panel), (
                f"{panel_id} panel lost its {subheading!r} section sub-heading"
            )

    def test_consequences_panel_has_no_section_subheading(self):
        html = home_html()
        panel = panel_html(html, "consequences")
        assert "<h3" not in panel

    def test_history_results_subheading_nests_beneath_panel_title(self, fresh_client):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-03")
        html = fresh_client.get("/?search_name=ALICE").get_data(as_text=True)

        panel = panel_html(html, "history")
        title_index = panel.index("<h2>Search Boarder History</h2>")
        results_index = panel.index("<h3>Search Results</h3>")
        assert title_index < results_index

    def test_all_four_tabs_share_identical_computed_typography(self, fresh_client, browser_page):
        html = fresh_client.get("/").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        typography = page.evaluate(
            """() => [...document.querySelectorAll('.tab-link')].map(tab => {
                const style = getComputedStyle(tab);
                return style.fontSize + '|' + style.fontFamily;
            })"""
        )

        assert len(typography) == 5
        assert len(set(typography)) == 1, typography


class TestVisualConsistencyPass:
    """Acceptance sweep for the whole-UI visual-consistency pass (#97):
    every story re-proven through the browser seam via computed styles,
    sampled only after animations settle."""

    def test_panel_headings_are_navy_but_modal_title_keeps_its_own_color(self, browser_page):
        page = browser_page
        page.set_content(home_html())

        colors = page.evaluate(
            """() => ({
                panels: [...document.querySelectorAll('.panel > h2')].map(
                    h => getComputedStyle(h).color
                ),
                modalTitle: getComputedStyle(document.getElementById('confirm-modal-title')).color,
            })"""
        )

        assert len(colors["panels"]) == 4, colors
        assert set(colors["panels"]) == {"rgb(29, 43, 83)"}, colors  # --navy
        assert colors["modalTitle"] == "rgb(26, 26, 26)", colors  # --text

    def test_table_scrollbar_chrome_is_navy_tinted_from_shared_token(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.replace_boarders(conn, [Boarder("ALICE", "Alice", "601A")])
        html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        page.set_content(html)

        tint = page.evaluate(
            """() => {
                const scroller = document.querySelector('.table-scroll');
                return getComputedStyle(scroller).scrollbarColor;
            }"""
        )
        # Chromium reports the transparent track as rgba(0, 0, 0, 0).
        assert tint == "rgba(29, 43, 83, 0.35) rgba(0, 0, 0, 0)"

    def test_month_picker_popover_centers_on_its_field(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        open_month_picker(page)

        offset = page.evaluate(
            """() => {
                const field = document.querySelector('.month-field-controls');
                const popover = document.getElementById('month-picker-popover');
                const fld = field.getBoundingClientRect();
                const pop = popover.getBoundingClientRect();
                return Math.abs((fld.left + fld.right) / 2 - (pop.left + pop.right) / 2);
            }"""
        )
        assert offset < 1

    def test_month_grid_cells_fill_the_popover_edge_to_edge(self, browser_page):
        page = browser_page
        page.set_content(home_html())
        open_month_picker(page)

        fill = page.evaluate(
            """() => {
                const grid = document.querySelector('.month-grid');
                const cells = [...grid.querySelectorAll('.month-option')];
                const g = grid.getBoundingClientRect();
                const widths = cells.map(c => c.getBoundingClientRect().width);
                return {
                    count: cells.length,
                    widthSpread: Math.max(...widths) - Math.min(...widths),
                    leftGap: cells[0].getBoundingClientRect().left - g.left,
                    rightGap: g.right - cells[cells.length - 1].getBoundingClientRect().right,
                };
            }"""
        )
        assert fill["count"] == 12
        assert fill["widthSpread"] < 0.5
        assert abs(fill["leftGap"]) < 1 and abs(fill["rightGap"]) < 1

    def test_selects_drop_os_chrome_for_house_style(self, fresh_client, browser_page):
        html = fresh_client.get("/consequences").get_data(as_text=True)

        page = browser_page
        page.set_content(html)

        style = page.evaluate(
            """() => {
                const s = getComputedStyle(document.querySelector('#consequences-status'));
                return {
                    appearance: s.appearance,
                    image: s.backgroundImage,
                    radius: s.borderRadius,
                };
            }"""
        )
        assert style["appearance"] == "none"
        assert "svg+xml" in style["image"], style
        assert "%231d2b53" in style["image"] or "#1d2b53" in style["image"], style
        assert style["radius"] == "6px"

    def test_consequences_toolbar_sits_left_with_toggle_on_select_baseline(self, fresh_client, browser_page):
        html = fresh_client.get("/consequences").get_data(as_text=True)

        page = browser_page
        page.set_content(html)

        edges = page.evaluate(
            """() => {
                const rect = el => el.getBoundingClientRect();
                const heading = rect(document.querySelector('#consequences h2'));
                const monthLabel = rect(document.querySelector('label[for="consequences-month"]'));
                const statusSelect = rect(document.querySelector('#consequences-status'));
                const toggle = rect(document.querySelector('.consequences-toolbar .btn-neutral'));
                return {
                    leftDrift: monthLabel.left - heading.left,
                    baselineDelta: toggle.bottom - statusSelect.bottom,
                };
            }"""
        )
        assert abs(edges["leftDrift"]) < 2, edges
        assert abs(edges["baselineDelta"]) <= 2, edges

    def test_count_sits_clear_of_the_group_heading_it_follows(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            seed_punishments(conn)
        html = fresh_client.get("/consequences").get_data(as_text=True)

        page = browser_page
        page.set_content(html)

        gap = page.evaluate(
            """() => {
                const rect = el => el.getBoundingClientRect();
                const title = rect(document.querySelector('.consequences-group-title'));
                const count = rect(document.querySelector('.consequences-group .consequences-count'));
                return count.top - title.bottom;
            }"""
        )
        assert gap >= 0, gap

    def test_empty_view_count_keeps_its_toolbar_coupling(self, fresh_client, browser_page):
        html = fresh_client.get("/consequences").get_data(as_text=True)

        page = browser_page
        page.set_content(html)

        gap = page.evaluate(
            """() => {
                const rect = el => el.getBoundingClientRect();
                const toolbar = rect(document.querySelector('.consequences-toolbar'));
                const count = rect(document.querySelector('.consequences-toolbar + .consequences-count'));
                return count.top - toolbar.bottom;
            }"""
        )
        assert abs(gap - 8) <= 1, gap

    def test_consequences_toolbar_controls_share_one_height(self, fresh_client, browser_page):
        html = fresh_client.get("/consequences").get_data(as_text=True)

        page = browser_page
        page.set_content(html)

        heights = page.evaluate(
            """() => [
                document.querySelector('#consequences-month'),
                document.querySelector('#consequences-status'),
                document.querySelector('.consequences-toolbar .btn-neutral'),
            ].map(el => el.getBoundingClientRect().height)"""
        )
        assert max(heights) - min(heights) < 1, heights

    def test_consequences_toolbar_stacks_on_mobile_with_shared_heights(self, fresh_client, browser_page):
        html = fresh_client.get("/consequences").get_data(as_text=True)

        page = browser_page
        page.set_viewport_size({"width": 375, "height": 800})
        try:
            page.set_content(html)

            layout = page.evaluate(
                """() => {
                    const toolbar = document.querySelector('.consequences-toolbar');
                    const els = [
                        document.querySelector('#consequences-month'),
                        document.querySelector('#consequences-status'),
                        toolbar.querySelector('.btn-neutral'),
                    ];
                    const rects = els.map(el => el.getBoundingClientRect());
                    return {
                        direction: getComputedStyle(toolbar).flexDirection,
                        heightSpread: Math.max(...rects.map(r => r.height))
                            - Math.min(...rects.map(r => r.height)),
                    };
                }"""
            )
            assert layout["direction"] == "column", layout
            assert layout["heightSpread"] < 1, layout
        finally:
            page.set_viewport_size({"width": 1280, "height": 720})

    def test_checkboxes_render_as_navy_tiles_with_scale_in_checks(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-07")
        html = fresh_client.get("/").get_data(as_text=True)
        rows = [month_row("ALICE", "101", 2, 5, 7)]

        page = browser_page
        page.set_content(html)
        open_month_detail(page, rows)
        page.locator("#month-detail-assign-btn").click()

        checkbox = page.locator('#assign-boarders input[type="checkbox"]').first
        checked = checkbox.evaluate(
            """cb => {
                const s = getComputedStyle(cb);
                return {
                    appearance: s.appearance,
                    size: s.width + 'x' + s.height,
                    radius: s.borderRadius,
                    background: s.backgroundColor,
                    checkTransform: getComputedStyle(cb, '::before').transform,
                };
            }"""
        )
        assert checked["appearance"] == "none"
        assert checked["size"] == "16pxx16px"
        assert checked["radius"] == "4px"
        assert checked["background"] == "rgb(29, 43, 83)"
        assert checked["checkTransform"] == "matrix(1, 0, 0, 1, 0, 0)"

        checkbox.uncheck()
        page.wait_for_function(
            """() => getComputedStyle(
                document.querySelector('#assign-boarders input[type="checkbox"]')
            ).backgroundColor === 'rgb(255, 255, 255)'"""
        )
        cleared = checkbox.evaluate(
            "cb => [getComputedStyle(cb).backgroundColor, getComputedStyle(cb, '::before').transform]"
        )
        assert cleared[0] == "rgb(255, 255, 255)"
        assert cleared[1] == "matrix(0, 0, 0, 0, 0, 0)"

    def test_file_picker_button_wears_primary_style(self, fresh_client, browser_page):
        html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        page.set_content(html)

        button = page.evaluate(
            """() => {
                const s = getComputedStyle(document.querySelector('input[type="file"]'),
                    '::file-selector-button');
                return {background: s.backgroundColor, color: s.color, radius: s.borderRadius};
            }"""
        )
        assert button["background"] == "rgb(29, 43, 83)"
        assert button["color"] == "rgb(255, 255, 255)"
        assert button["radius"] == "6px"

    def test_tables_read_as_cards_with_tinted_header_band(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.replace_boarders(conn, [Boarder("ALICE", "Alice", "601A")])
        html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        page.set_content(html)

        card = page.evaluate(
            """() => {
                const scroller = document.querySelector('.table-scroll');
                const header = scroller.querySelector('thead th');
                const s = getComputedStyle(scroller);
                return {
                    borderWidth: s.borderTopWidth,
                    borderStyle: s.borderTopStyle,
                    borderColor: s.borderTopColor,
                    radius: s.borderRadius,
                    background: s.backgroundColor,
                    headerBackground: getComputedStyle(header).backgroundColor,
                };
            }"""
        )
        assert card["borderWidth"] == "1px"
        assert card["borderStyle"] == "solid"
        assert card["borderColor"] == "rgb(226, 230, 234)"  # --border
        assert card["radius"] == "8px"
        assert card["background"] == "rgb(255, 255, 255)"
        assert card["headerBackground"] == "rgb(248, 249, 250)"  # --page-bg

    def test_sticky_header_lift_appears_only_while_rows_pass_beneath(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-07")
        rows = [month_row(f"S{i:02d}", f"{600 + i}A", 1, 10, 10) for i in range(30)]
        html = fresh_client.get("/").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        open_month_detail(page, rows)

        def shadow_state():
            return page.evaluate(
                """() => {
                    const scroller = document.querySelector('#month-detail .table-scroll');
                    return {
                        scrolled: scroller.classList.contains('scrolled'),
                        shadow: getComputedStyle(scroller.querySelector('thead th')).boxShadow,
                    };
                }"""
            )

        initial = shadow_state()
        assert not initial["scrolled"]
        assert initial["shadow"] == "none"

        page.evaluate("() => { document.querySelector('#month-detail .table-scroll').scrollTop = 40; }")
        page.wait_for_function(
            """() => {
                const scroller = document.querySelector('#month-detail .table-scroll');
                if (!scroller.classList.contains('scrolled')) return false;
                const shadow = getComputedStyle(scroller.querySelector('thead th')).boxShadow;
                return shadow !== 'none' && shadow.includes('rgba(29, 43, 83');
            }"""
        )

        page.evaluate("() => { document.querySelector('#month-detail .table-scroll').scrollTop = 0; }")
        page.wait_for_function(
            """() => !document.querySelector('#month-detail .table-scroll')
                .classList.contains('scrolled')"""
        )
        assert shadow_state()["shadow"] == "none"

    def test_popups_enter_with_sub_200ms_fade_and_rise(self, browser_page):
        page = browser_page
        page.set_content(home_html())

        page.evaluate("() => showConfirmModal({ title: 'Confirm', message: 'm' })")
        entrances = page.evaluate(
            """() => ({
                backdropName: getComputedStyle(document.getElementById('confirmModal')).animationName,
                backdropDuration: parseFloat(
                    getComputedStyle(document.getElementById('confirmModal')).animationDuration
                ),
                contentName: getComputedStyle(
                    document.querySelector('#confirmModal .modal-content')
                ).animationName,
                contentDuration: parseFloat(
                    getComputedStyle(document.querySelector('#confirmModal .modal-content'))
                        .animationDuration
                ),
            })"""
        )
        page.keyboard.press("Escape")
        open_month_picker(page)
        picker = page.evaluate(
            """() => ({
                name: getComputedStyle(document.getElementById('month-picker-popover')).animationName,
                duration: parseFloat(
                    getComputedStyle(document.getElementById('month-picker-popover')).animationDuration
                ),
            })"""
        )

        assert entrances["backdropName"] == "fade-in"
        assert entrances["contentName"] == "rise-in"
        assert picker["name"] == "picker-pop"
        assert entrances["backdropDuration"] <= 0.2
        assert entrances["contentDuration"] <= 0.2
        assert picker["duration"] <= 0.2

    def test_loading_state_shows_a_spinner(self, browser_page):
        page = browser_page
        page.set_content(home_html())

        spinner = page.evaluate(
            """() => {
                const s = getComputedStyle(document.querySelector('#month-detail-loading .spinner'));
                return {name: s.animationName, duration: parseFloat(s.animationDuration)};
            }"""
        )
        assert spinner["name"] == "spin"
        assert spinner["duration"] > 0

    def test_buttons_give_pressed_feedback_while_held(self, fresh_client, browser_page):
        html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        page.set_content(html)

        button = page.locator('#boarders form[action="/boarders/add"] button[type="submit"]')
        box = button.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        try:
            page.wait_for_function(
                """() => getComputedStyle(
                    document.querySelector('#boarders form[action="/boarders/add"] button[type="submit"]')
                ).transform === 'matrix(1, 0, 0, 1, 0, 1)'"""
            )
        finally:
            page.mouse.up()

    def test_reduced_motion_neutralises_entrances(self, browser_page):
        page = browser_page
        page.emulate_media(reduced_motion="reduce")
        page.set_content(home_html())

        page.evaluate("() => showConfirmModal({ title: 'Confirm', message: 'm' })")
        modal_durations = page.evaluate(
            """() => [
                getComputedStyle(document.getElementById('confirmModal')).animationDuration,
                getComputedStyle(document.querySelector('#confirmModal .modal-content'))
                    .animationDuration,
            ]"""
        )
        page.keyboard.press("Escape")
        open_month_picker(page)
        picker_duration = page.evaluate(
            "() => getComputedStyle(document.getElementById('month-picker-popover')).animationDuration"
        )

        for duration in modal_durations + [picker_duration]:
            assert float(duration.rstrip("s")) < 0.001, duration

    def test_danger_and_neutral_buttons_deepen_on_hover_like_primary(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.replace_boarders(conn, [Boarder("ALICE", "Alice", "601A")])
        html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        page.set_content(html)

        neutral = page.locator("#boarder-edit")
        neutral.hover()
        page.wait_for_function(
            "() => getComputedStyle(document.getElementById('boarder-edit')).backgroundColor"
            " === 'rgb(97, 105, 112)'"
        )
        assert neutral.evaluate("el => getComputedStyle(el).filter") == "none"

        page.locator("#boarder-edit").click()
        page.locator(".boarder-remove").first.click()
        page.wait_for_selector("#confirmModal.show")
        danger = page.locator("#confirmModal .btn-danger")
        danger.hover()
        page.wait_for_function(
            "() => getComputedStyle(document.querySelector('#confirmModal .btn-danger'))"
            ".backgroundColor === 'rgb(206, 23, 54)'"
        )
        danger_filter = danger.evaluate("el => getComputedStyle(el).filter")
        assert danger_filter == "none"


class TestBoardersToolbarRegroup:
    def _toolbar(self, fresh_client):
        html = fresh_client.get("/boarders").get_data(as_text=True)
        panel = panel_html(html, "boarders")
        start = panel.index('<div class="month-detail-toolbar">')
        end = panel.index('<div id="boarder-edit-actions"')
        return panel[start:end]

    def test_import_form_holds_only_label_file_input_and_submit(self, fresh_client):
        toolbar = self._toolbar(fresh_client)
        form = re.search(r'<form action="/boarders/import"[^>]*>.*?</form>', toolbar, re.S)
        assert form is not None, "roster-import form is missing"

        form_html = form.group(0)
        assert '<label for="boarder_csv">' in form_html
        assert 'type="file"' in form_html
        assert ">Import</button>" in form_html
        assert "boarder-edit" not in form_html

    def test_edit_sits_outside_the_form_beside_download_roster(self, fresh_client):
        toolbar = self._toolbar(fresh_client)
        form_end = toolbar.index("</form>")
        edit_index = toolbar.index('id="boarder-edit"')
        download_index = toolbar.index("Download Master List")
        assert form_end < edit_index < download_index

    def test_download_roster_carries_the_shared_download_icon(self, fresh_client):
        toolbar = self._toolbar(fresh_client)
        link = re.search(r'<a[^>]*href="/boarders/export".*?</a>', toolbar, re.S)
        assert link is not None, "Download Master List link is missing"
        assert 'href="#icon-download"' in link.group(0)

    def test_toolbar_copy_uses_master_list_vocabulary(self, fresh_client):
        toolbar = self._toolbar(fresh_client)
        assert "Replace Master List from CSV" in toolbar
        assert "Download Master List (CSV)" in toolbar
        assert "roster" not in toolbar.lower()

    def test_edit_button_stays_wired_after_regroup(self, fresh_client, browser_page):
        html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        page.locator("#boarder-edit").click()

        assert page.locator("#boarders-table .boarder-remove").count() >= 1


MONTH_TOOLBAR_IDS = [
    "month-detail-print",
    "month-detail-download",
    "month-detail-assign-btn",
    "month-detail-close",
    "month-detail-delete",
]


class TestMonthReportToolbarOrdering:
    def test_toolbar_dom_order_puts_close_before_delete(self, fresh_client):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-07")
        html = fresh_client.get("/").get_data(as_text=True)
        indices = [html.index(f'id="{control_id}"') for control_id in MONTH_TOOLBAR_IDS]
        assert indices == sorted(indices), (
            f"toolbar order must be {MONTH_TOOLBAR_IDS}, got {indices}"
        )

    def test_delete_is_rightmost_and_only_danger_control_when_report_open(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-07")
        html = fresh_client.get("/").get_data(as_text=True)
        rows = [month_row("ALICE", "101", 2, 5, 7)]

        page = browser_page
        page.set_content(html)
        open_month_detail(page, rows)

        boxes = page.evaluate(
            """ids => ids.map(id => {
                const rect = document.getElementById(id).getBoundingClientRect();
                return { id, left: rect.left, right: rect.right };
            })""",
            MONTH_TOOLBAR_IDS,
        )
        lefts = [box["left"] for box in boxes]
        assert lefts == sorted(lefts), boxes
        delete_box = boxes[-1]
        assert delete_box["id"] == "month-detail-delete"
        assert delete_box["right"] == max(box["right"] for box in boxes)

        danger_controls = page.evaluate(
            """() => [...document.querySelectorAll('#month-detail .btn-danger')]
                .map(el => el.id)"""
        )
        assert danger_controls == ["month-detail-delete"]


class TestConsequencesRowActionContract:
    """Pins exactly what /consequences renders as row actions per status.

    Characterization safety net for the row-action decomposition: every
    assertion here must keep passing unmodified when the duplicated form
    blocks collapse into one macro backed by server-owned offered actions.
    """

    PRIMARY = "btn btn-primary btn-sm"
    NEUTRAL = "btn btn-neutral btn-sm"

    def _seed(self, fresh_client, deadline="2099-01-01"):
        with app_module.connect() as conn:
            seeded = seed_punishments(
                conn,
                boarders=[record("ALICE", "101", 2, 5, 7)],
                deadline=deadline,
                include_report=False,
            )
            return seeded[0]

    def _move(self, punishment_id, target, timestamp="2026-04-11T09:00:00+00:00"):
        with app_module.connect() as conn:
            storage.transition_punishment(
                conn, punishment_id, target, timestamp=timestamp
            )

    def _row(self, fresh_client, punishment_id, query=""):
        html = fresh_client.get(f"/consequences{query}").get_data(as_text=True)
        panel = panel_html(html, "consequences")
        match = re.search(
            rf'<tr data-punishment-id="{punishment_id}">.*?</tr>', panel, re.S
        )
        assert match is not None, f"row {punishment_id} is not rendered"
        return match.group(0)

    def _forms(self, row):
        return re.findall(r"<form\b.*?</form>", row, re.S)

    def _contract(self, form):
        """Returns (hidden 'to' value, button label, button class) for one action form."""
        to = re.search(r'<input type="hidden" name="to" value="([^"]*)"', form)
        button = re.search(r'<button type="submit" class="([^"]*)">([^<]*)</button>', form)
        assert to is not None and button is not None, form
        return to.group(1), button.group(2), button.group(1)

    def _contracts(self, row):
        return [self._contract(form) for form in self._forms(row)]

    def test_assigned_row_offers_submission_and_void_only_before_deadline(self, fresh_client):
        punishment = self._seed(fresh_client, deadline="2099-01-01")

        row = self._row(fresh_client, punishment.id)

        assert self._contracts(row) == [
            ("submitted", "Submitted", self.PRIMARY),
            ("voided", "Void", self.NEUTRAL),
        ]

    def test_due_assigned_row_offers_mark_overdue_first(self, fresh_client):
        today = datetime.now(tz=timezone.utc).date().isoformat()
        punishment = self._seed(fresh_client, deadline=today)

        row = self._row(fresh_client, punishment.id)

        assert self._contracts(row) == [
            ("overdue", "Mark overdue", self.PRIMARY),
            ("submitted", "Submitted", self.PRIMARY),
            ("voided", "Void", self.NEUTRAL),
        ]

    def test_overdue_row_offers_phone_held_submission_and_void(self, fresh_client):
        punishment = self._seed(fresh_client)
        self._move(punishment.id, "overdue")

        row = self._row(fresh_client, punishment.id)

        assert self._contracts(row) == [
            ("phone_held", "Phone held", self.PRIMARY),
            ("submitted", "Submitted", self.PRIMARY),
            ("voided", "Void", self.NEUTRAL),
        ]

    def test_phone_held_row_offers_release_submission_and_void(self, fresh_client):
        punishment = self._seed(fresh_client)
        self._move(punishment.id, "overdue")
        self._move(punishment.id, "phone_held")

        row = self._row(fresh_client, punishment.id)

        assert self._contracts(row) == [
            ("submitted", "Submitted (release phone)", self.PRIMARY),
            ("voided", "Void", self.NEUTRAL),
        ]

    def test_submitted_row_offers_only_void(self, fresh_client):
        punishment = self._seed(fresh_client)
        self._move(punishment.id, "submitted", timestamp="2026-04-09T09:00:00+00:00")

        row = self._row(fresh_client, punishment.id, query="?status=submitted")

        assert self._contracts(row) == [("voided", "Void", self.NEUTRAL)]

    def test_voided_row_offers_no_actions(self, fresh_client):
        punishment = self._seed(fresh_client)
        with app_module.connect() as conn:
            storage.transition_punishment(
                conn,
                punishment.id,
                "voided",
                timestamp="2026-04-12T09:00:00+00:00",
                void_reason="exempt",
            )

        row = self._row(fresh_client, punishment.id, query="?show_all=1&status=voided")

        assert self._forms(row) == []

    def test_every_action_form_posts_to_its_own_punishment(self, fresh_client):
        punishment = self._seed(fresh_client)

        row = self._row(fresh_client, punishment.id)

        for form in self._forms(row):
            assert f'action="/punishment/{punishment.id}/transition"' in form
            assert 'method="post"' in form

    def test_void_form_keeps_reason_input_target_data_and_styling(self, fresh_client):
        punishment = self._seed(fresh_client)

        row = self._row(fresh_client, punishment.id)

        void_form = next(form for form in self._forms(row) if 'value="voided"' in form)
        assert 'class="void-form"' in void_form
        assert f'data-boarder="{punishment.display_name}"' in void_form
        assert f'data-month="{punishment.month}"' in void_form
        assert (
            '<input type="text" name="void_reason" placeholder="Reason (optional)" '
            'aria-label="Void reason (optional)">'
        ) in void_form
        assert '<button type="submit" class="btn btn-neutral btn-sm">Void</button>' in void_form

    def test_every_action_form_preserves_active_month_status_and_show_all_filters(self, fresh_client):
        punishment = self._seed(fresh_client)

        filtered_row = self._row(
            fresh_client, punishment.id, query="?month=2026-03&status=assigned"
        )
        for form in self._forms(filtered_row):
            assert '<input type="hidden" name="month" value="2026-03">' in form
            assert '<input type="hidden" name="status" value="assigned">' in form

        show_all_row = self._row(fresh_client, punishment.id, query="?show_all=1")
        for form in self._forms(show_all_row):
            assert '<input type="hidden" name="show_all" value="1">' in form

    def test_action_forms_carry_no_filter_fields_when_no_filter_is_active(self, fresh_client):
        punishment = self._seed(fresh_client)

        row = self._row(fresh_client, punishment.id)

        for form in self._forms(row):
            assert 'name="month"' not in form
            assert 'name="status"' not in form
            assert 'name="show_all"' not in form

    def test_whole_page_exposes_the_expected_action_set_per_status_at_once(self, fresh_client):
        today = datetime.now(tz=timezone.utc).date().isoformat()
        names = ["ALICE", "BOB", "CAROL", "DANA", "ELLE", "FRAN"]
        with app_module.connect() as conn:
            seeded = seed_punishments(
                conn,
                boarders=[
                    record(name, str(101 + i), 1, 2, 3) for i, name in enumerate(names)
                ],
                deadline=today,
                include_report=False,
            )
            alice = next(p for p in seeded if p.normalized_name == "ALICE")
            conn.execute(
                "UPDATE punishments SET deadline = '2099-01-01' WHERE id = ?", (alice.id,)
            )
            conn.commit()
        ids = {p.normalized_name: p.id for p in seeded}
        self._move(ids["CAROL"], "overdue")
        self._move(ids["DANA"], "overdue")
        self._move(ids["DANA"], "phone_held")
        self._move(ids["ELLE"], "submitted", timestamp="2026-04-09T09:00:00+00:00")
        self._move(ids["FRAN"], "voided", timestamp="2026-04-12T09:00:00+00:00")

        html = fresh_client.get("/consequences?show_all=1").get_data(as_text=True)
        panel = panel_html(html, "consequences")
        rows = {}
        for match in re.finditer(r'<tr data-punishment-id="(\d+)">.*?</tr>', panel, re.S):
            rows[int(match.group(1))] = match.group(0)

        expected = {
            ids["ALICE"]: [
                ("submitted", "Submitted", self.PRIMARY),
                ("voided", "Void", self.NEUTRAL),
            ],
            ids["BOB"]: [
                ("overdue", "Mark overdue", self.PRIMARY),
                ("submitted", "Submitted", self.PRIMARY),
                ("voided", "Void", self.NEUTRAL),
            ],
            ids["CAROL"]: [
                ("phone_held", "Phone held", self.PRIMARY),
                ("submitted", "Submitted", self.PRIMARY),
                ("voided", "Void", self.NEUTRAL),
            ],
            ids["DANA"]: [
                ("submitted", "Submitted (release phone)", self.PRIMARY),
                ("voided", "Void", self.NEUTRAL),
            ],
            ids["ELLE"]: [("voided", "Void", self.NEUTRAL)],
            ids["FRAN"]: [],
        }
        assert {pid: self._contracts(row) for pid, row in rows.items()} == expected


class TestConsequencesRowActionTidiness:
    def _consequences_html_with_punishment(self, fresh_client):
        with app_module.connect() as conn:
            seed_punishments(conn)
        return fresh_client.get("/consequences").get_data(as_text=True)

    def test_action_forms_sit_in_one_row_actions_wrapper_without_inline_styles(self, fresh_client):
        html = self._consequences_html_with_punishment(fresh_client)
        panel = panel_html(html, "consequences")

        assert 'style="display:inline"' not in panel
        rows = re.findall(r'<tr data-punishment-id="\d+">.*?</tr>', panel, re.S)
        assert rows, "expected at least one rendered punishment row"
        for row in rows:
            assert row.count('<div class="row-actions">') == 1, row
            before_wrapper = row.split('<div class="row-actions">')[0]
            assert "<form" not in before_wrapper

    def test_row_actions_container_is_wrapping_evenly_gapped_and_centred(self, fresh_client, browser_page):
        html = self._consequences_html_with_punishment(fresh_client)

        page = browser_page
        page.set_content(html)
        style = page.evaluate(
            """() => {
                const style = getComputedStyle(document.querySelector('.row-actions'));
                return {
                    display: style.display,
                    flexWrap: style.flexWrap,
                    justifyContent: style.justifyContent,
                    gap: parseFloat(style.rowGap),
                };
            }"""
        )
        assert style["display"] == "flex"
        assert style["flexWrap"] == "wrap"
        assert style["justifyContent"] == "center"
        assert style["gap"] > 0

    def test_void_reason_input_is_compact_like_neighbouring_buttons(self, fresh_client, browser_page):
        html = self._consequences_html_with_punishment(fresh_client)

        page = browser_page
        page.set_content(html)
        metrics = page.evaluate(
            """() => {
                const input = document.querySelector('form.void-form input[name="void_reason"]');
                const button = input.closest('form').querySelector('button[type="submit"]');
                const inputStyle = getComputedStyle(input);
                const buttonStyle = getComputedStyle(button);
                return {
                    width: parseFloat(inputStyle.width),
                    inputPadding: inputStyle.paddingTop + '/' + inputStyle.paddingRight
                        + '/' + inputStyle.paddingBottom + '/' + inputStyle.paddingLeft,
                    buttonPadding: buttonStyle.paddingTop + '/' + buttonStyle.paddingRight
                        + '/' + buttonStyle.paddingBottom + '/' + buttonStyle.paddingLeft,
                    buttonWidth: parseFloat(buttonStyle.width),
                };
            }"""
        )
        assert 0 < metrics["width"] < 220, metrics
        assert metrics["inputPadding"] == metrics["buttonPadding"], metrics


class TestUiTidinessHoldsEverywhere:
    def test_conditional_controls_share_the_button_system(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-07")
        html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        page.locator("#boarder-edit").click()

        assert (
            page.locator("#boarders-table .boarder-remove").count()
            == page.locator("#boarders-table tbody tr").count()
        )

        page.locator("#boarders-table .boarder-remove").first.click()
        page.wait_for_selector("#confirmModal.show")
        page.keyboard.press("Escape")

        typography = page.evaluate(
            """() => {
                const tiers = {
                    regular: [
                        '#boarders form button[type="submit"]',
                        '#confirmModal .btn-danger',
                        '#confirmModal .btn-neutral',
                        '#assign-form button[type="submit"]',
                    ],
                    small: [
                        '#boarder-save',
                        '#boarder-cancel',
                        '.boarder-remove',
                    ],
                };
                const measured = {};
                for (const [tier, selectors] of Object.entries(tiers)) {
                    measured[tier] = selectors.map(selector => {
                        const el = document.querySelector(selector);
                        if (!el) return null;
                        const style = getComputedStyle(el);
                        return {
                            selector,
                            family: style.fontFamily,
                            size: style.fontSize,
                        };
                    });
                }
                return measured;
            }"""
        )
        entries = typography["regular"] + typography["small"]
        assert all(entry is not None for entry in entries), typography
        families = {entry["family"] for entry in entries}
        assert len(families) == 1, families
        for tier in ("regular", "small"):
            sizes = {entry["size"] for entry in typography[tier]}
            assert len(sizes) == 1, typography[tier]

    def test_narrow_viewport_stacks_open_report_toolbar_without_overflow(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 2, 5, 7)], "2026-07")
        html = fresh_client.get("/").get_data(as_text=True)
        rows = [month_row("ALICE", "101", 2, 5, 7)]

        page = browser_page
        page.set_viewport_size({"width": 360, "height": 800})
        page.set_content(html)
        open_month_detail(page, rows)

        toolbar_direction = page.evaluate(
            """() => {
                const toolbar = document.querySelector('#month-detail .month-detail-toolbar');
                return getComputedStyle(toolbar).flexDirection;
            }"""
        )
        assert toolbar_direction == "column"
        overflow = page.evaluate(
            """() => document.documentElement.scrollWidth > document.documentElement.clientWidth"""
        )
        assert not overflow

    def test_no_rendered_copy_uses_the_master_list_avoid_term(self, fresh_client):
        for route in ("/", "/boarders", "/consequences", "/statistics", "/boarder/ALICE"):
            html = fresh_client.get(route).get_data(as_text=True)
            assert "roster" not in html.lower(), (
                f"{route} renders the Master List avoid-term"
            )
            assert "record" not in html.lower(), (
                f"{route} renders a Boarder History avoid-term"
            )

    def test_static_empty_states_carry_an_icon(self, fresh_client):
        icon = '<use href="#icon-inbox"/>'
        # The app auto-seeds the Master List, so clear it to reach the
        # boarders empty state.
        fresh_client.post(
            "/boarders/import",
            data={"boarder_csv": (io.BytesIO(b"Name,Bed\n"), "empty.csv")},
            content_type="multipart/form-data",
        )
        boarders = panel_html(fresh_client.get("/boarders").get_data(as_text=True), "boarders")
        consequences = panel_html(
            fresh_client.get("/consequences").get_data(as_text=True), "consequences"
        )
        reports = panel_html(fresh_client.get("/").get_data(as_text=True), "reports")
        history = panel_html(
            fresh_client.get("/?search_name=NOSUCHNAME").get_data(as_text=True), "history"
        )
        for name, panel in (
            ("boarders", boarders),
            ("consequences", consequences),
            ("reports", reports),
            ("history", history),
        ):
            assert 'class="empty-state"' in panel, f"{name} panel lacks an empty state"
            assert icon in panel, f"{name} empty state lacks the shared inbox icon"

    def test_runtime_injected_empty_state_carries_an_icon(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.replace_boarders(conn, [Boarder("ALICE", "Alice", "601A")])
        html = fresh_client.get("/boarders").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        page.evaluate(
            """() => {
                window.fetch = (url, opts) => new Promise(resolve => setTimeout(() =>
                    resolve({ json: () => Promise.resolve({ ok: true }) }), 100));
            }"""
        )
        page.locator("#boarder-edit").click()
        page.locator(".boarder-remove").first.click()
        page.locator("#confirmModal .btn-danger").click()

        injected = page.wait_for_selector("#boarders .table-scroll .empty-state svg use")
        assert injected.get_attribute("href") == "#icon-inbox"
