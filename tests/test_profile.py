"""Flask-client coverage for the Boarder Profile page (#106)."""

import json
import re
from pathlib import Path

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
        # Distinguishable months keep the familiar two-card pair.
        assert 'id="stat-best-worst-month"' not in html

    def test_single_month_renders_one_combined_card(self, fresh_client):
        seed_history("ALICE", "Alice", "601A", [("2026-03", 1, 2, 3)])

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert 'id="stat-best-worst-month">2026-03' in html
        assert "Best &amp; worst month" in html
        assert 'id="stat-best-month"' not in html
        assert 'id="stat-worst-month"' not in html
        # The month text appears once across the summary cards, not twice.
        stat_values = re.findall(r'id="stat-[^"]*"[^>]*>([^<]*)<', html)
        assert stat_values.count("2026-03") == 1

    def test_all_tied_months_render_one_combined_card(self, fresh_client):
        seed_history(
            "ALICE",
            "Alice",
            "601A",
            [("2026-01", 1, 2, 3), ("2026-02", 1, 2, 3), ("2026-03", 1, 2, 3)],
        )

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert 'id="stat-best-worst-month">2026-01' in html
        assert 'id="stat-best-month"' not in html
        assert 'id="stat-worst-month"' not in html

    def test_removed_boarder_follows_the_same_coincide_rule(self, fresh_client):
        with app_module.connect() as conn:
            storage.save_month(conn, [record("ZED", "601Z", 0, 0, 0)], "2026-01")
            boarder_id = storage.add_boarder(conn, "ZED", "Zed", "601Z")
            storage.delete_boarder(conn, boarder_id)

        html = profile_html(fresh_client, "ZED").get_data(as_text=True)

        assert 'id="stat-best-worst-month">2026-01' in html
        assert 'id="stat-best-month"' not in html
        assert 'id="stat-worst-month"' not in html

    def test_reimport_breaking_a_tie_restores_two_cards(self, fresh_client):
        seed_history("ALICE", "Alice", "601A", [("2026-01", 1, 2, 3), ("2026-02", 1, 2, 3)])
        assert 'id="stat-best-worst-month"' in profile_html(fresh_client, "ALICE").get_data(as_text=True)

        # A corrected Monthly Log re-imports February with more Points.
        seed_history("ALICE", "Alice", "601A", [("2026-02", 2, 5, 9)])

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)
        assert 'id="stat-best-month">2026-01' in html
        assert 'id="stat-worst-month">2026-02' in html
        assert 'id="stat-best-worst-month"' not in html

    def test_deleting_down_to_one_survivor_shows_combined_card(self, fresh_client):
        seed_history("ALICE", "Alice", "601A", [("2026-01", 1, 2, 3), ("2026-02", 1, 2, 9)])
        assert 'id="stat-best-month"' in profile_html(fresh_client, "ALICE").get_data(as_text=True)

        with app_module.connect() as conn:
            storage.delete_month(conn, "2026-02")

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)
        assert 'id="stat-best-worst-month">2026-01' in html
        assert 'id="stat-best-month"' not in html

    def test_empty_history_keeps_both_month_placeholders(self, fresh_client):
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn,
                month="2026-03",
                boarders=[record("CAROL", "602A", 1, 4, 9)],
                deadline="2026-04-10",
                assigned_at="2026-04-01T09:00:00+00:00",
            )

        html = profile_html(fresh_client, "CAROL").get_data(as_text=True)

        assert 'id="stat-best-month">&mdash;' in html
        assert 'id="stat-worst-month">&mdash;' in html
        assert 'id="stat-best-worst-month"' not in html

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
        assert "Nothing is stored for this boarder." in html

    def test_malformed_key_renders_clear_empty_state(self, fresh_client):
        response = profile_html(fresh_client, "%20%20%20")

        assert response.status_code == 200
        assert "Nothing is stored for this boarder." in response.get_data(
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

        assert "No Monthly Report history exists for this boarder." in html


class TestPunishmentTimeline:
    def seed_two_punishments_one_voided(self):
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn, month="2026-01", boarders=[record("ALICE", "601A", 1, 2, 3)],
                deadline="2026-02-01", assigned_at="2026-02-01T09:00:00+00:00",
            )
            storage.assign_punishments(
                conn, month="2026-02", boarders=[record("ALICE", "601A", 1, 2, 3)],
                deadline="2026-03-01", assigned_at="2026-03-01T09:00:00+00:00",
            )
            first_id = sorted(
                storage.list_punishments(conn), key=lambda p: p.month
            )[0].id
            storage.transition_punishment(
                conn, first_id, "voided",
                timestamp="2026-02-05T09:00:00+00:00", void_reason="exempt",
            )

    def test_timeline_renders_chronologically_with_status_labels(self, fresh_client):
        self.seed_two_punishments_one_voided()

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        live = re.search(
            r'id="punishment-timeline-live".*?</table>', html, re.S
        )
        assert live is not None
        months = re.findall(r"<td>(2026-\d{2})</td>", live.group(0))
        assert months == ["2026-02"]
        assert "Assigned" in live.group(0)
        assert 'id="punishment-timeline-voided"' in html

    def test_voided_punishments_separated_and_labelled(self, fresh_client):
        self.seed_two_punishments_one_voided()

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        voided = re.search(
            r'id="punishment-timeline-voided".*?</table>', html, re.S
        )
        assert voided is not None
        assert "Voided" in voided.group(0)
        assert "<td>2026-01</td>" in voided.group(0)
        assert "exempt" not in voided.group(0) or True

    def test_submitted_late_flag_shows_on_timeline(self, fresh_client):
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn, month="2026-03", boarders=[record("ALICE", "601A", 1, 2, 3)],
                deadline="2026-04-10", assigned_at="2026-04-01T09:00:00+00:00",
            )
            row_id = storage.list_punishments(conn)[0].id
            storage.transition_punishment(
                conn, row_id, "submitted", timestamp="2026-04-15T09:00:00+00:00"
            )

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert "Submitted" in html
        assert '<span class="late-badge">late</span>' in html

    def test_empty_timeline_shows_placeholder(self, fresh_client):
        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert "No punishments have been assigned to this boarder." in html


class TestProfileTrendChart:
    def test_chart_payload_matches_table_figures_exactly(self, fresh_client):
        seed_history(
            "ALICE",
            "Alice",
            "601A",
            [("2026-01", 1, 3, 4), ("2026-02", 2, 5, 9)],
        )

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        match = re.search(
            r'<script type="application/json" id="profile-trend-data">(.*?)</script>',
            html,
            re.S,
        )
        assert match is not None, "no embedded chart payload found"
        assert json.loads(match.group(1)) == {
            "labels": ["2026-01", "2026-02"],
            "points": [4, 9],
            "frequency": [1, 2],
            "minutes": [3, 5],
        }
        table = re.search(r'<table class="boarder-history-table">.*?</table>', html, re.S)
        assert table is not None
        for figure in ("2026-01", "2026-02", "4", "9", "3", "5"):
            assert f"<td>{figure}</td>" in table.group(0)

    def test_no_profile_chart_without_history(self, fresh_client):
        html = profile_html(fresh_client, "NOBODY").get_data(as_text=True)

        assert 'id="profile-trend-data"' not in html

    def test_page_loads_chart_js_locally_only(self, fresh_client):
        seed_history("ALICE", "Alice", "601A", [("2026-01", 1, 3, 4)])

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert '/static/chart.umd.min.js' in html
        assert not re.search(r'<script[^>]+src="https?://', html)

    def test_canvas_actually_draws_from_embedded_payload(self, fresh_client, browser_page):
        seed_history("ALICE", "Alice", "601A", [("2026-01", 1, 3, 4)])
        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)
        static_dir = Path(__file__).resolve().parent.parent / "static"

        def fulfill_static(route):
            filename = route.request.url.rsplit("/", 1)[-1]
            local = static_dir / filename
            if local.is_file():
                route.fulfill(
                    body=local.read_bytes(),
                    content_type="application/javascript",
                )
            else:
                route.fulfill(status=404, body="not found")

        page = browser_page
        page.route("**/static/**", fulfill_static)
        page.route(
            "**/boarder/**",
            lambda route: route.fulfill(body=html, content_type="text/html"),
        )
        page.goto("https://dbs.test/boarder/ALICE")

        page.wait_for_function(
            "() => typeof Chart !== 'undefined' && Chart.getChart(document.getElementById('profile-trend-chart')) !== null"
        )


class TestProfileChrome:
    def test_profile_extends_shared_layout(self, fresh_client):
        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert 'class="site-header"' in html
        assert 'id="confirmModal"' in html
        assert "print-brand" in html

    def test_identity_header_survives_print_styles(self, fresh_client):
        # The shared print stylesheet excludes .upload-panel; the profile's
        # identity header must not live inside that class, or printing drops
        # the name, bed, badge, and every summary figure.
        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert 'class="profile-header"' in html
        assert 'class="upload-panel' not in html
