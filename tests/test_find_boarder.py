"""Flask-client coverage for the Find-a-Boarder person lookup (#116)."""

import re

from helpers import record

import app as app_module
import storage
from records import Boarder


def history_panel(html):
    match = re.search(r'<section id="history".*?</section>', html, re.S)
    assert match is not None, "no history panel found"
    return match.group(0)


def seed_history(key, display_name, bed, months):
    """months: iterable of (month, frequency, minutes, points)."""
    with app_module.connect() as conn:
        for month, frequency, minutes, points in months:
            storage.save_month(
                conn,
                [record(key, bed, frequency, minutes, points,
                        display_name=display_name)],
                month,
            )


class TestOneRowPerBoarder:
    def test_multi_month_boarder_renders_one_result_row(self, fresh_client):
        seed_history(
            "ALICE",
            "Alice",
            "601A",
            [("2026-01", 1, 2, 3), ("2026-02", 1, 2, 3), ("2026-03", 1, 2, 3)],
        )

        panel = history_panel(fresh_client.get("/?search_name=alice").get_data(as_text=True))

        assert panel.count('href="/boarder/ALICE"') == 1

    def test_spelling_variants_resolve_to_one_row(self, fresh_client):
        with app_module.connect() as conn:
            storage.save_month(
                conn, [record("CHEN WEI", "701A", 1, 2, 3)], "2026-03"
            )
            storage.save_month(
                conn, [record("CHEN, WEI", "701A", 2, 5, 9)], "2026-04"
            )

        panel = history_panel(
            fresh_client.get("/?search_name=chen wei").get_data(as_text=True)
        )

        assert panel.count('href="/boarder/CHEN%20WEI"') == 1

    def test_comma_variant_query_matches_the_collapsed_identity(self, fresh_client):
        seed_history("CHEN WEI", "Chen Wei", "701A", [("2026-03", 1, 2, 3)])

        panel = history_panel(
            fresh_client.get("/?search_name=chen,%20wei").get_data(as_text=True)
        )

        assert 'href="/boarder/CHEN%20WEI"' in panel

    def test_whitespace_padded_query_matches(self, fresh_client):
        seed_history("ALICE", "Alice", "601A", [("2026-03", 1, 2, 3)])

        padded = history_panel(
            fresh_client.get("/?search_name=%20%20alice%20%20").get_data(as_text=True)
        )
        unpadded = history_panel(
            fresh_client.get("/?search_name=alice").get_data(as_text=True)
        )

        assert 'href="/boarder/ALICE"' in padded
        assert padded.count('href="/boarder/ALICE"') == unpadded.count(
            'href="/boarder/ALICE"'
        )


class TestIdentityColumns:
    def test_current_boarder_row_shows_link_badge_and_bed(self, fresh_client):
        with app_module.connect() as conn:
            storage.replace_boarders(conn, [Boarder("ALICE", "Alice", "601A")])
        seed_history("ALICE", "Alice", "601A", [("2026-02", 0, 0, 0)])

        panel = history_panel(fresh_client.get("/?search_name=ALICE").get_data(as_text=True))

        assert 'href="/boarder/ALICE"' in panel
        assert "badge-current" in panel
        assert ">Current</span>" in panel
        assert "<td>601A</td>" in panel

    def test_removed_boarder_row_shows_former_badge(self, fresh_client):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ZED", "601Z", 1, 2, 3)], "2026-01")
            boarder_id = storage.add_boarder(conn, "ZED", "Zed", "601Z")
            storage.delete_boarder(conn, boarder_id)

        panel = history_panel(fresh_client.get("/?search_name=zed").get_data(as_text=True))

        assert "badge-former" in panel
        assert ">Former</span>" in panel

    def test_former_identity_resolves_freshest_snapshot(self, fresh_client):
        seed_history("BOB", "Old Bob", "101", [("2026-01", 1, 1, 1)])
        seed_history("BOB", "New Bob", "202", [("2026-02", 1, 1, 1)])
        with app_module.connect() as conn:
            bob = next(b for b in storage.list_boarders(conn) if b.normalized_name == "BOB")
            storage.delete_boarder(conn, bob.id)

        panel = history_panel(fresh_client.get("/?search_name=bob").get_data(as_text=True))

        assert "New Bob" in panel
        assert "<td>202</td>" in panel


class TestWidenedMatchPool:
    def test_master_list_only_boarder_is_found(self, fresh_client):
        with app_module.connect() as conn:
            storage.replace_boarders(conn, [Boarder("CAROL", "Carol", "602C")])

        panel = history_panel(fresh_client.get("/?search_name=carol").get_data(as_text=True))

        assert 'href="/boarder/CAROL"' in panel

    def test_punishment_only_boarder_is_found(self, fresh_client):
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn,
                month="2026-03",
                boarders=[record("CAROL", "602C", 1, 4, 9)],
                deadline="2026-04-10",
                assigned_at="2026-04-01T09:00:00+00:00",
            )

        panel = history_panel(fresh_client.get("/?search_name=carol").get_data(as_text=True))

        assert 'href="/boarder/CAROL"' in panel


class TestResultOrdering:
    def test_current_rows_sort_before_former_rows(self, fresh_client):
        with app_module.connect() as conn:
            storage.replace_boarders(conn, [Boarder("ABE", "Abe", "701")])
            storage.save_month(conn, [record("ADA", "702", 1, 1, 1)], "2026-01")
            ada = storage.add_boarder(conn, "ADA", "Ada", "702")
            storage.delete_boarder(conn, ada)

        panel = history_panel(fresh_client.get("/?search_name=a").get_data(as_text=True))

        assert panel.index('href="/boarder/ABE"') < panel.index('href="/boarder/ADA"')


class TestLookupStates:
    def test_blank_query_prompts_without_results_section(self, fresh_client):
        html = fresh_client.get("/?search_name=").get_data(as_text=True)

        assert "enter a boarder name" in html.lower()
        assert "results-section" not in history_panel(html)

    def test_zero_hit_query_renders_neutral_empty_state(self, fresh_client):
        panel = history_panel(fresh_client.get("/?search_name=ZZZ").get_data(as_text=True))

        assert "No boarders matched your search." in panel
        assert "banner-success" not in panel

    def test_panel_heading_announces_person_lookup(self, fresh_client):
        html = fresh_client.get("/").get_data(as_text=True)

        assert "<h2>Find a Boarder</h2>" in html

    def test_per_month_columns_are_gone_from_results_table(self, fresh_client):
        seed_history("ALICE", "Alice", "601A", [("2026-03", 2, 19, 21)])

        panel = history_panel(fresh_client.get("/?search_name=alice").get_data(as_text=True))

        assert '<th scope="col">Month</th>' not in panel
        assert '<th scope="col">Frequency</th>' not in panel
        assert '<th scope="col">Minutes Late</th>' not in panel
        assert '<th scope="col">Total Points</th>' not in panel
