"""Flask-client coverage for the House Dashboard (#110)."""

import json
import re

from helpers import record

import app as app_module
import storage


def dashboard(client, query=""):
    response = client.get(f"/statistics{query}")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def save_month_totals(month, rows):
    """rows: iterable of (name, bed, frequency, minutes, points)."""
    with app_module.connect() as conn:
        storage.save_month(
            conn,
            [record(name, bed, frequency, minutes, points) for name, bed,
             frequency, minutes, points in rows],
            month,
        )


class TestStatisticsTabRouting:
    def test_statistics_tab_highlights_on_direct_visit(self, fresh_client):
        html = dashboard(fresh_client)

        match = re.search(r'<a class="([^"]*)" href="/statistics">', html)
        assert match is not None, "no Statistics tab link found"
        assert "active" in match.group(1).split()

    def test_home_page_shows_statistics_tab_link(self, fresh_client):
        html = fresh_client.get("/").get_data(as_text=True)

        assert 'href="/statistics">Statistics</a>' in html


class TestHouseTrendPayload:
    def test_trend_payload_matches_stored_months_exactly(self, fresh_client):
        save_month_totals("2026-01", [("ALICE", "101", 2, 5, 7), ("BOB", "102", 1, 4, 3)])
        save_month_totals("2026-02", [("ALICE", "101", 1, 2, 3)])

        html = dashboard(fresh_client)

        match = re.search(
            r'<script type="application/json" id="house-trend-data">(.*?)</script>',
            html,
            re.S,
        )
        assert match is not None
        assert json.loads(match.group(1)) == {
            "months": ["2026-01", "2026-02"],
            "incidents": [3, 1],
            "minutes": [9, 2],
        }

    def test_table_fallback_lists_per_month_totals(self, fresh_client):
        save_month_totals("2026-03", [("ALICE", "101", 4, 11, 12)])

        html = dashboard(fresh_client)

        row = re.search(
            r"<tr>\s*<td>\s*2026-03\s*</td>\s*<td>\s*4\s*</td>\s*<td>\s*11\s*</td>",
            html,
        )
        assert row is not None, "per-month totals table missing a stored month"

    def test_deleting_a_month_changes_the_next_visit(self, fresh_client):
        save_month_totals("2026-01", [("ALICE", "101", 2, 5, 7)])
        save_month_totals("2026-02", [("ALICE", "101", 1, 2, 3)])
        before = json.loads(re.search(
            r'id="house-trend-data">(.*?)</script>', dashboard(fresh_client), re.S
        ).group(1))

        with app_module.connect() as conn:
            storage.delete_month(conn, "2026-01")
        after = json.loads(re.search(
            r'id="house-trend-data">(.*?)</script>', dashboard(fresh_client), re.S
        ).group(1))

        assert before["months"] == ["2026-01", "2026-02"]
        assert after["months"] == ["2026-02"]

    def test_re_importing_a_month_updates_figures(self, fresh_client):
        save_month_totals("2026-01", [("ALICE", "101", 2, 5, 7)])
        save_month_totals("2026-01", [("ALICE", "101", 5, 9, 10)])

        payload = json.loads(re.search(
            r'id="house-trend-data">(.*?)</script>', dashboard(fresh_client), re.S
        ).group(1))

        assert payload["incidents"] == [5]
        assert payload["minutes"] == [9]


class TestDashboardEmptyState:
    def test_empty_archive_shows_clear_empty_state(self, fresh_client):
        html = dashboard(fresh_client)

        assert "No reports have been imported yet." in html
        assert 'id="house-trend-chart"' not in html
