"""Flask-client coverage for boarder-name click-throughs (#109)."""

import re

from helpers import month_row, open_month_detail, record

import app as app_module
import storage


def master_list_table(html):
    match = re.search(r'<table class="boarders-table" id="boarders-table">.*?</table>', html, re.S)
    assert match is not None, "no master list table found"
    return match.group(0)


def alltime_table(html):
    match = re.search(r'<table class="boarders-table alltime-table".*?</table>', html, re.S)
    assert match is not None, "no all-time table found"
    return match.group(0)


class TestMasterListClickThrough:
    def test_master_list_names_link_to_profiles(self, fresh_client):
        html = fresh_client.get("/boarders").get_data(as_text=True)

        table = master_list_table(html)
        assert '<a class="boarder-link" href="/boarder/ALICE">' in table
        assert '<a class="boarder-link" href="/boarder/BOB">' in table

    def test_rows_carry_the_match_key_for_edit_round_trips(self, fresh_client):
        html = fresh_client.get("/boarders").get_data(as_text=True)

        assert 'data-boarder-key="ALICE"' in master_list_table(html)


class TestAllTimeClickThrough:
    def test_all_time_rows_link_including_former(self, fresh_client):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ZED", "601Z", 1, 2, 3)], "2026-01")
            boarder_id = storage.add_boarder(conn, "ZED", "Zed", "601Z")
            storage.delete_boarder(conn, boarder_id)

        html = fresh_client.get("/boarders?view=all-time").get_data(as_text=True)

        table = alltime_table(html)
        assert '<a class="boarder-link" href="/boarder/ALICE">' in table
        assert '<a class="boarder-link" href="/boarder/ZED">' in table


class TestMonthDetailClickThrough:
    def test_detail_names_link_and_survive_sorting(self, fresh_client, browser_page):
        with app_module.connect() as conn:
            storage.save_month(
                conn,
                [
                    record("ALICE", "601A", 1, 2, 9),
                    record("BOB", "601B", 1, 2, 3),
                ],
                "2026-03",
            )

        html = fresh_client.get("/").get_data(as_text=True)
        page = browser_page
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.set_content(html)

        open_month_detail(
            page,
            [
                month_row("ALICE", "601A", 1, 2, 9),
                month_row("BOB", "601B", 1, 2, 3),
            ],
        )

        links = page.locator("#month-detail-body a.boarder-link")
        assert links.count() == 2
        assert links.first.get_attribute("href") == "/boarder/ALICE"

        # Sorting rebuilds the rows; the links must survive the interaction.
        page.locator("#month-detail-table th").nth(4).locator("button").click()
        page.wait_for_function(
            "count => document.querySelectorAll('#month-detail-body a.boarder-link').length === count",
            arg=2,
        )
        # First click sorts ascending by points: BOB (3) leads.
        assert "3" in page.locator("#month-detail-body tr").first.inner_text(), (
            "rows were not re-sorted by points"
        )
        page.locator("#month-detail-table th").nth(4).locator("button").click()
        page.wait_for_function(
            "count => document.querySelectorAll('#month-detail-body a.boarder-link').length === count",
            arg=2,
        )
        assert "9" in page.locator("#month-detail-body tr").first.inner_text(), (
            "second click did not flip to descending"
        )
        assert not page_errors
