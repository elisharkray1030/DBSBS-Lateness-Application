"""Flask-client coverage for the All-Time List and roster toggle (#105)."""

import re

from helpers import record

import app as app_module
import storage


def boarders_panel(html):
    match = re.search(r'<section id="boarders".*?</section>', html, re.S)
    assert match is not None, "no boarders panel found"
    return match.group(0)


def get_boarders(client, query=""):
    response = client.get(f"/boarders{query}")
    assert response.status_code == 200
    return boarders_panel(response.get_data(as_text=True))


def add_and_remove_boarder(name, bed, months):
    """Adds a boarder, records history months, then removes them."""
    with app_module.connect() as conn:
        boarder_id = storage.add_boarder(conn, name, name.title(), bed)
        for month, frequency, minutes, points in months:
            storage.save_month(
                conn, [record(name, bed, frequency, minutes, points)], month
            )
        storage.delete_boarder(conn, boarder_id)


class TestRosterToggle:
    def test_current_view_is_the_default(self, fresh_client):
        panel = get_boarders(fresh_client)

        assert 'id="boarders-table"' in panel
        assert 'id="alltime-table"' not in panel
        assert 'id="boarder-edit"' in panel

    def test_toggle_offers_current_and_all_time_views(self, fresh_client):
        panel = get_boarders(fresh_client)

        assert 'href="/boarders">Current</a>' in panel
        assert 'href="/boarders?view=all-time">All-time</a>' in panel

    def test_all_time_view_renders_read_only_listing(self, fresh_client):
        panel = get_boarders(fresh_client, "?view=all-time")

        assert 'id="alltime-table"' in panel
        assert "ALICE" in panel
        assert "BOB" in panel
        assert 'id="boarder-edit"' not in panel
        assert "/boarders/add" not in panel
        assert "/boarders/import" not in panel


class TestAllTimeListRendering:
    def test_removed_boarder_appears_once_as_former_with_tenure(self, fresh_client):
        add_and_remove_boarder("ZED", "601Z", [("2026-01", 2, 5, 7), ("2026-02", 1, 3, 4)])

        panel = get_boarders(fresh_client, "?view=all-time")

        assert panel.count("Zed") == 1
        row = re.search(r"<tr data-boarder-key=\"ZED\">.*?</tr>", panel, re.S)
        assert row is not None
        row_html = row.group(0)
        assert "badge-former" in row_html
        assert "Former" in row_html
        assert "<td>2026-01</td>" in row_html
        assert "<td>2026-02</td>" in row_html
        assert "<td>3</td>" in row_html  # incidents: 2 + 1
        assert "<td>8</td>" in row_html  # minutes: 5 + 3
        assert "<td>11</td>" in row_html  # points: 7 + 4

    def test_punishment_only_survivor_still_listed(self, fresh_client):
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn,
                month="2026-03",
                boarders=[record("CAROL", "602A", 1, 4, 9)],
                deadline="2026-04-10",
                assigned_at="2026-04-01T09:00:00+00:00",
            )

        panel = get_boarders(fresh_client, "?view=all-time")

        assert "Carol" in panel
        assert "badge-former" in panel

    def test_identity_resolves_from_freshest_snapshot(self, fresh_client):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ALICE", "101", 1, 1, 1, display_name="Old Alice")], "2026-01")
            storage.save_month(conn, [record("ALICE", "777", 1, 1, 1, display_name="Alicia")], "2026-05")
            storage.delete_boarder(conn, storage.list_boarders(conn)[0].id)

        panel = get_boarders(fresh_client, "?view=all-time")

        alice_row = re.search(r"<tr data-boarder-key=\"ALICE\">.*?</tr>", panel, re.S)
        assert alice_row is not None
        assert "<td>777</td>" in alice_row.group(0)
        assert "Alicia" in alice_row.group(0)

    def test_name_filter_narrows_all_time_view(self, fresh_client):
        panel = get_boarders(fresh_client, "?view=all-time&q=ali")

        assert "ALICE" in panel
        assert "BOB" not in panel

    def test_name_filter_no_matches_shows_clear_state(self, fresh_client):
        panel = get_boarders(fresh_client, "?view=all-time&q=zzz")

        assert "No boarders matched your filter." in panel

    def test_empty_database_shows_clear_empty_state(self, fresh_client):
        with app_module.connect() as conn:
            for boarder in storage.list_boarders(conn):
                storage.delete_boarder(conn, boarder.id)

        panel = get_boarders(fresh_client, "?view=all-time")

        assert 'id="alltime-table"' not in panel
        assert "No boarders stored yet." in panel
