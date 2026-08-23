"""Flask-client coverage for the Boarder Profile page (#106)."""

import re

import pytest
from helpers import record

import app as app_module
import storage


def profile_html(client, key):
    response = client.get(f"/boarder/{key}")
    return response


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


class TestSearchResultLinks:
    def test_search_results_link_names_to_profiles(self, fresh_client):
        seed_history("CHEN WEI", "Chen Wei", "701A", [("2026-03", 1, 2, 3)])

        html = fresh_client.get("/?search_name=chen").get_data(as_text=True)

        assert 'href="/boarder/CHEN%20WEI"' in html

    def test_following_a_search_link_opens_the_profile(self, fresh_client):
        seed_history("CHEN WEI", "Chen Wei", "701A", [("2026-03", 1, 2, 3)])

        response = fresh_client.get("/boarder/CHEN%20WEI")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Chen Wei" in html
        assert "<td>2026-03</td>" in html


class TestProfileIdentity:
    def test_removed_boarder_renders_with_former_badge(self, fresh_client):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ZED", "601Z", 1, 2, 3)], "2026-01")
            boarder_id = storage.add_boarder(conn, "ZED", "Zed", "601Z")
            storage.delete_boarder(conn, boarder_id)

        response = profile_html(fresh_client, "ZED")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "badge-former" in html
        assert "Former" in html
        assert "<td>2026-01</td>" in html

    def test_current_boarder_shows_current_badge(self, fresh_client):
        seed_history("ALICE", "Alice", "601A", [("2026-02", 0, 0, 0)])

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert "badge-current" in html

    def test_identity_resolves_freshest_snapshot_for_former(self, fresh_client):
        seed_history("BOB", "Old Bob", "101", [("2026-01", 1, 1, 1)])
        seed_history("BOB", "New Bob", "202", [("2026-02", 1, 1, 1)])
        with app_module.connect() as conn:
            bob = next(b for b in storage.list_boarders(conn) if b.normalized_name == "BOB")
            storage.delete_boarder(conn, bob.id)

        html = profile_html(fresh_client, "BOB").get_data(as_text=True)

        assert "New Bob" in html
        assert "202" in html

    def test_punctuation_variants_resolve_to_one_profile(self, fresh_client):
        seed_history("CHEN WEI", "Chen Wei", "701A", [("2026-03", 1, 2, 3)])

        comma_response = fresh_client.get("/boarder/CHEN%2C%20WEI")

        # Defensive normalization redirects variants onto the canonical key.
        assert comma_response.status_code == 302
        assert comma_response.headers["Location"].endswith("/boarder/CHEN%20WEI")
        followed = fresh_client.get("/boarder/CHEN%2C%20WEI", follow_redirects=True)
        assert followed.status_code == 200
        assert "Chen Wei" in followed.get_data(as_text=True)


class TestProfileSummary:
    def test_summary_figures_and_best_worst_month(self, fresh_client):
        seed_history(
            "ALICE",
            "Alice",
            "601A",
            [
                ("2026-01", 1, 3, 4),
                ("2026-02", 2, 5, 9),
                ("2026-03", 2, 5, 9),
                ("2026-04", 0, 0, 0),
            ],
        )

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        incidents = re.search(r'id="stat-incidents"[^>]*>(\d+)<', html)
        minutes = re.search(r'id="stat-minutes"[^>]*>(\d+)<', html)
        points = re.search(r'id="stat-points"[^>]*>(\d+)<', html)
        assert incidents and incidents.group(1) == "5"
        assert minutes and minutes.group(1) == "13"
        assert points and points.group(1) == "22"
        # Best: fewest points, earliest month breaking ties; worst: most
        # points, earliest month breaking ties.
        assert 'id="stat-best-month">2026-04' in html
        assert 'id="stat-worst-month">2026-02' in html

    def test_monthly_table_totals_match_the_series(self, fresh_client):
        seed_history(
            "ALICE",
            "Alice",
            "601A",
            [("2026-01", 1, 3, 4), ("2026-02", 2, 5, 9)],
        )

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert '<td>3</td>' in html  # total incidents
        assert '<td>8</td>' in html  # total minutes
        assert '<td>13</td>' in html  # total points


class TestProfileEmptyStates:
    def test_unknown_key_renders_clear_empty_state(self, fresh_client):
        response = profile_html(fresh_client, "NOBODY")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "No records are stored for this boarder." in html

    def test_malformed_key_renders_clear_empty_state(self, fresh_client):
        response = profile_html(fresh_client, "%20%20%20")

        assert response.status_code == 200
        assert "No records are stored for this boarder." in response.get_data(
            as_text=True
        )

    def test_punishment_only_boarder_has_no_history_placeholder(self, fresh_client):
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn,
                month="2026-03",
                boarders=[record("CAROL", "602A", 1, 4, 9)],
                deadline="2026-04-10",
                assigned_at="2026-04-01T09:00:00+00:00",
            )

        html = profile_html(fresh_client, "CAROL").get_data(as_text=True)

        assert "No Monthly Report history is recorded for this boarder." in html


class TestProfileChrome:
    def test_profile_extends_shared_layout(self, fresh_client):
        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert 'class="site-header"' in html
        assert 'id="confirmModal"' in html
        assert "print-brand" in html
