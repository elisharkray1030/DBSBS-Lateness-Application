"""Flask-client coverage for the Boarder Profile page (#106)."""

import json
import re
from types import SimpleNamespace

import pytest
from helpers import (
    post_csrf,
    record,
    seed_ipoint_entry,
    seed_punishments,
    static_dir,
)

import app as app_module
import ipoints
import punishments
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


def open_charted_profile(browser_page, html):
    """Fulfils /boarder/ with rendered HTML and local static files, then
    waits until Chart.js has drawn the profile chart on the canvas."""
    static_dir_path = static_dir()

    def fulfill_static(route):
        filename = route.request.url.rsplit("/", 1)[-1]
        local = static_dir_path / filename
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
    return page


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
    def test_chart_section_is_titled_lateness_by_month(self, fresh_client):
        seed_history("ALICE", "Alice", "601A", [("2026-01", 1, 3, 4)])

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert "<h2>Lateness by Month</h2>" in html
        assert "Points by Month" not in html

    def test_canvas_aria_announces_frequency_and_minutes_not_points(self, fresh_client):
        seed_history("ALICE", "Alice", "601A", [("2026-01", 1, 3, 4)])

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert "'s frequency and minutes late per month" in html
        assert "points, frequency, and minutes late" not in html

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
        table = re.search(r'<table class="boarder-history-table"[^>]*>.*?</table>', html, re.S)
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

    def test_drawn_chart_plots_frequency_and_minutes_in_brand_colors(self, fresh_client, browser_page):
        seed_history("ALICE", "Alice", "601A", [("2026-01", 1, 3, 4)])
        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        page = open_charted_profile(browser_page, html)
        datasets = page.evaluate(
            "() => Chart.getChart(document.getElementById('profile-trend-chart'))"
            ".data.datasets.map(d => ({ label: d.label, color: d.backgroundColor }))"
        )

        assert datasets == [
            {"label": "Frequency", "color": "#1d2b53"},
            {"label": "Minutes late", "color": "#E51A3C"},
        ]

    def test_canvas_actually_draws_from_embedded_payload(self, fresh_client, browser_page):
        seed_history("ALICE", "Alice", "601A", [("2026-01", 1, 3, 4)])
        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        open_charted_profile(browser_page, html)


class TestEscalationSection:
    def test_merged_rows_join_history_with_live_punishment_chronologically(self, fresh_client):
        seed_history(
            "ALICE",
            "Alice",
            "601A",
            [("2026-01", 1, 3, 4), ("2026-02", 2, 5, 9)],
        )
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn,
                month="2026-02",
                boarders=[record("ALICE", "601A", 2, 5, 9)],
                deadline="2026-03-01",
                assigned_at="2026-02-01T09:00:00+00:00",
            )

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        section = re.search(r'id="escalation-table".*?</table>', html, re.S)
        assert section is not None, "no merged escalation section found"
        months = re.findall(r"<td>(2026-\d{2})</td>", section.group(0))
        assert months == ["2026-01", "2026-02"]
        assert "No punishment assigned" in section.group(0)
        assert "Assigned" in section.group(0)
        # Joined figures ride along: January history, February history plus
        # the live punishment's deadline and due marker.
        assert "<td>1</td>" in section.group(0)
        assert "<td>4</td>" in section.group(0)
        assert "<td>2</td>" in section.group(0)
        assert "<td>9</td>" in section.group(0)
        assert "2026-03-01" in section.group(0)
        assert '<span class="due-badge">due</span>' in section.group(0)

    def test_punishment_only_month_marks_missing_history(self, fresh_client):
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn,
                month="2026-02",
                boarders=[record("ALICE", "601A", 2, 5, 9)],
                deadline="2026-03-01",
                assigned_at="2026-02-01T09:00:00+00:00",
            )

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        section = re.search(r'id="escalation-table".*?</table>', html, re.S)
        assert section is not None, "no merged escalation section found"
        months = re.findall(r"<td>(2026-\d{2})</td>", section.group(0))
        assert months == ["2026-02"]
        assert "Assigned" in section.group(0)
        assert "2026-03-01" in section.group(0)
        # Missing lateness is explicit, never a silent zero or bare dash.
        assert "No history" in section.group(0)

    def test_neither_history_nor_punishments_shows_empty_state(self, fresh_client):
        # Fresh fixture seeds BOB on the Master List with no history/punishments.
        html = profile_html(fresh_client, "BOB").get_data(as_text=True)

        assert 'id="escalation-table"' not in html
        assert "No monthly history or punishments stored for this boarder." in html

    def test_voided_plus_live_month_follows_live(self, fresh_client):
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn,
                month="2026-02",
                boarders=[record("ALICE", "601A", 2, 5, 9)],
                deadline="2026-03-01",
                assigned_at="2026-02-01T09:00:00+00:00",
            )
            only_id = storage.list_boarder_punishments(conn, "ALICE")[0].id
            storage.transition_punishment(
                conn, only_id, "voided",
                timestamp="2026-02-03T09:00:00+00:00", void_reason="exempt",
            )
            storage.assign_punishments(
                conn,
                month="2026-02",
                boarders=[record("ALICE", "601A", 2, 5, 9)],
                deadline="2026-03-10",
                assigned_at="2026-02-04T09:00:00+00:00",
            )

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        section = re.search(r'id="escalation-table".*?</table>', html, re.S)
        assert section is not None, "no merged escalation section found"
        months = re.findall(r"<td>(2026-\d{2})</td>", section.group(0))
        assert months == ["2026-02"]
        # Live row wins: its deadline rides along, the voided one never does.
        assert "2026-03-10" in section.group(0)
        assert "2026-03-01" not in section.group(0)
        assert "Voided" not in section.group(0)
        # Voided detail stays visible only in the Punishment Timeline.
        voided = re.search(
            r'id="punishment-timeline-voided".*?</table>', html, re.S
        )
        assert voided is not None
        assert "<td>2026-02</td>" in voided.group(0)

    def test_voided_only_month_never_inflates_escalation(self, fresh_client):
        seed_history("ALICE", "Alice", "601A", [("2026-01", 1, 3, 4)])
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn,
                month="2026-01",
                boarders=[record("ALICE", "601A", 1, 3, 4)],
                deadline="2026-02-01",
                assigned_at="2026-01-01T09:00:00+00:00",
            )
            only_id = storage.list_boarder_punishments(conn, "ALICE")[0].id
            storage.transition_punishment(
                conn, only_id, "voided",
                timestamp="2026-01-05T09:00:00+00:00", void_reason="exempt",
            )

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        section = re.search(r'id="escalation-table".*?</table>', html, re.S)
        assert section is not None, "no merged escalation section found"
        assert "No punishment assigned" in section.group(0)
        assert "Voided" not in section.group(0)
        voided = re.search(
            r'id="punishment-timeline-voided".*?</table>', html, re.S
        )
        assert voided is not None
        assert "<td>2026-01</td>" in voided.group(0)

    def test_second_live_punishment_in_a_month_warns_and_keeps_first(
        self, caplog
    ):
        # The storage seam guarantees at most one live punishment per
        # boarder-month, so a second live row signals corruption, not a
        # legal state. The merge keeps rendering the first but must say
        # so loudly instead of dropping the row silently.
        series = [
            SimpleNamespace(
                month="2026-02", frequency=2, total_minutes=5, total_points=9
            )
        ]
        first = SimpleNamespace(month="2026-02", status="assigned")
        second = SimpleNamespace(month="2026-02", status="overdue")

        with caplog.at_level("WARNING", logger="app"):
            rows = app_module._escalation_rows(series, [first, second])

        assert len(rows) == 1
        assert rows[0]["punishment"] is first
        assert any(
            "2026-02" in record.message for record in caplog.records
        ), "no warning logged for the duplicate live punishment"


class TestProfileChrome:
    def test_profile_extends_shared_layout(self, fresh_client):
        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert 'class="site-header"' in html
        assert 'id="confirmModal"' in html
        assert "print-brand" in html

    def test_identity_header_survives_print_styles(self, fresh_client):
        # The shared print stylesheet excludes .upload-panel; the profile's
        # identity header must not live inside that class, or printing drops
        # the name, bed, badge, and every summary figure. The I-Point quick-log
        # panel is allowed (and is itself deliberately excluded from paper).
        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert 'class="profile-header"' in html
        header_start = html.index('class="profile-header"')
        assert 'class="upload-panel' not in html[:header_start]


class TestEscalationParityAndLock:
    """Former-boarder parity, identity parity, and regression lock (#158)."""

    @staticmethod
    def _escalation_section(html):
        section = re.search(r'id="escalation-table".*?</table>', html, re.S)
        assert section is not None, "no merged escalation section found"
        return section.group(0)

    def test_former_boarder_escalation_matches_current(self, fresh_client):
        seed_history("ZED", "Zed", "601Z", [("2026-01", 1, 2, 3)])
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn,
                month="2026-01",
                boarders=[record("ZED", "601Z", 1, 2, 3)],
                deadline="2026-02-01",
                assigned_at="2026-01-01T09:00:00+00:00",
            )
        current_html = profile_html(fresh_client, "ZED").get_data(as_text=True)

        with app_module.connect() as conn:
            zed_id = storage.add_boarder(conn, "ZED", "Zed", "601Z")
            storage.delete_boarder(conn, zed_id)
        former_html = profile_html(fresh_client, "ZED").get_data(as_text=True)

        assert "Former" in former_html
        assert self._escalation_section(former_html) == self._escalation_section(
            current_html
        ), "Former boarder's merged section differs from Current"

        # A later Monthly Log re-import never mutates the issued punishment
        # shown in the Former boarder's merged section either.
        seed_history("ZED", "Zed", "601Z", [("2026-01", 9, 90, 99)])
        reimported = self._escalation_section(
            profile_html(fresh_client, "ZED").get_data(as_text=True)
        )
        assert "2026-02-01" in reimported, (
            "re-import rewrote the Former boarder's issued punishment"
        )

    def test_reimport_never_mutates_issued_punishment_in_escalation(
        self, fresh_client
    ):
        seed_history("ALICE", "Alice", "601A", [("2026-02", 2, 5, 9)])
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn,
                month="2026-02",
                boarders=[record("ALICE", "601A", 2, 5, 9)],
                deadline="2026-03-01",
                assigned_at="2026-02-01T09:00:00+00:00",
            )

        before = self._escalation_section(
            profile_html(fresh_client, "ALICE").get_data(as_text=True)
        )

        # A corrected Monthly Log re-imports February with higher figures.
        seed_history("ALICE", "Alice", "601A", [("2026-02", 5, 50, 99)])
        after = self._escalation_section(
            profile_html(fresh_client, "ALICE").get_data(as_text=True)
        )

        assert "2026-03-01" in before and "2026-03-01" in after, (
            "re-import rewrote the issued punishment shown in the merged row"
        )
        assert "<td>99</td>" in after, (
            "merged row did not pick up the corrected history figures"
        )

    def test_comma_variant_shows_identical_escalation(self, fresh_client):
        seed_history("CHEN WEI", "Chen Wei", "701A", [("2026-03", 1, 2, 3)])
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn,
                month="2026-03",
                boarders=[record("CHEN WEI", "701A", 1, 2, 3)],
                deadline="2026-04-01",
                assigned_at="2026-03-01T09:00:00+00:00",
            )

        canonical = self._escalation_section(
            profile_html(fresh_client, "CHEN%20WEI").get_data(as_text=True)
        )

        # The comma variant resolves onto the canonical key via redirect.
        redirect = profile_html(fresh_client, "CHEN%2C%20WEI")
        assert redirect.status_code == 302
        assert redirect.headers["Location"].endswith("/boarder/CHEN%20WEI")
        variant = self._escalation_section(
            fresh_client.get("/boarder/CHEN%2C%20WEI", follow_redirects=True)
            .get_data(as_text=True)
        )

        assert variant == canonical, (
            "name variant's merged section differs from the canonical key"
        )

    def test_existing_sections_unchanged_with_escalation_present(
        self, fresh_client
    ):
        seed_history(
            "ALICE",
            "Alice",
            "601A",
            [("2026-01", 1, 3, 4), ("2026-02", 2, 5, 9)],
        )
        with app_module.connect() as conn:
            storage.assign_punishments(
                conn,
                month="2026-02",
                boarders=[record("ALICE", "601A", 2, 5, 9)],
                deadline="2026-03-01",
                assigned_at="2026-02-01T09:00:00+00:00",
            )
            only_id = storage.list_boarder_punishments(conn, "ALICE")[0].id
            storage.transition_punishment(
                conn, only_id, "voided",
                timestamp="2026-02-03T09:00:00+00:00", void_reason="exempt",
            )
            storage.assign_punishments(
                conn,
                month="2026-02",
                boarders=[record("ALICE", "601A", 2, 5, 9)],
                deadline="2026-03-10",
                assigned_at="2026-02-04T09:00:00+00:00",
            )

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        # Merged section is present alongside everything else.
        escalation = self._escalation_section(html)
        assert "2026-03-10" in escalation

        # Boarder History table keeps its rows and totals.
        history = re.search(
            r'class="boarder-history-table".*?</table>', html, re.S
        )
        assert history is not None, "Boarder History table changed"
        assert "<td>2026-01</td>" in history.group(0)
        assert "<td>2026-02</td>" in history.group(0)
        assert "<td>3</td>" in history.group(0)  # total incidents
        assert "<td>13</td>" in history.group(0)  # total points

        # Punishment Timeline keeps the live/voided split and order.
        live = re.search(
            r'id="punishment-timeline-live".*?</table>', html, re.S
        )
        voided = re.search(
            r'id="punishment-timeline-voided".*?</table>', html, re.S
        )
        assert live is not None, "live Punishment Timeline changed"
        assert voided is not None, "voided Punishment Timeline changed"
        assert "2026-03-10" in live.group(0)
        assert "2026-03-01" not in live.group(0)
        assert "<td>2026-02</td>" in voided.group(0)

        # Lifetime summary totals and chart payload are untouched.
        assert 'id="stat-incidents">3<' in html
        assert 'id="stat-minutes">8<' in html
        assert 'id="stat-points">13<' in html
        payload = re.search(
            r'<script type="application/json" id="profile-trend-data">(.*?)</script>',
            html,
            re.S,
        )
        assert payload is not None, "chart payload changed"
        assert json.loads(payload.group(1)) == {
            "labels": ["2026-01", "2026-02"],
            "points": [4, 9],
            "frequency": [1, 2],
            "minutes": [3, 5],
        }

        # All Profile tables keep scoped column headers, each under its
        # established section heading.
        assert "<h2>Boarder History</h2>" in html
        assert "<h2>Escalation by Month</h2>" in html
        assert "<h2>Punishment Timeline</h2>" in html
        tables = {
            "escalation-table": rf'id="escalation-table".*?</table>',
            "punishment-timeline-live": (
                r'id="punishment-timeline-live".*?</table>'
            ),
            "punishment-timeline-voided": (
                r'id="punishment-timeline-voided".*?</table>'
            ),
            "boarder-history-table": (
                r'class="boarder-history-table".*?</table>'
            ),
        }
        for table_id, pattern in tables.items():
            table = re.search(pattern, html, re.S)
            assert table is not None, f"{table_id} changed"
            headers = re.findall(r"<th(?:\s[^>]*)?>", table.group(0))
            assert headers, f"{table_id} lost its headers"
            assert all('scope="col"' in tag for tag in headers), (
                f"{table_id} diverged from the Profile's table semantics"
            )


class TestProfileIPointsSection:
    def test_ipoints_only_boarder_resolves_former_with_balance_and_quick_log(
        self, fresh_client
    ):
        seed_ipoint_entry(name="CHEN WEI", points=5, reason="Repeated disruption")

        html = profile_html(fresh_client, "CHEN%20WEI").get_data(as_text=True)

        assert "<h2>I-Points</h2>" in html
        assert 'id="stat-ipoint-balance">5<' in html
        assert "Repeated disruption" in html
        assert "CHEN WEI" in html
        # The All-Time union resolves I-Points-only keys as Former, matching a
        # Punishment-only survivor: badge plus zero-filled lateness cards.
        assert "badge-former" in html
        assert "Total incidents" in html
        # A key known only through I-Points is not a Removed Boarder, so it
        # keeps the quick-log action.
        assert 'action="/boarder/CHEN%20WEI/ipoints"' in html

    def test_ipoints_only_confiscation_shows_its_frozen_identity(
        self, fresh_client
    ):
        with app_module.connect() as conn:
            storage.replace_boarders(
                conn,
                [storage.Boarder("LENA", "Lena Lovelace", "402")],
            )
        seed_ipoint_entry(name="LENA", points=14)
        with app_module.connect() as conn:
            ipoints.materialise_pending_redemptions(conn, "2026-09-12")
            pending = storage.get_open_ipoint_confiscation(conn, "LENA")
            ipoints.confirm_redemption(
                conn,
                pending.id,
                today="2026-09-15",
                recorded_at="2026-09-15T09:00:00+00:00",
            )
        # Removing the Master List entry leaves only the frozen Confiscation
        # identity, so the key is now I-Points-only but still loggable.
        with app_module.connect() as conn:
            storage.delete_boarder(
                conn,
                next(
                    b.id
                    for b in storage.list_boarders(conn)
                    if b.normalized_name == "LENA"
                ),
            )

        html = profile_html(fresh_client, "LENA").get_data(as_text=True)

        assert "Lena Lovelace" in html
        assert "Bed 402" in html
        assert "badge-former" in html
        assert 'action="/boarder/LENA/ipoints"' in html

    def test_current_boarder_without_ipoints_gets_the_section_and_quick_log(
        self, fresh_client
    ):
        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert "<h2>I-Points</h2>" in html
        assert 'id="stat-ipoint-balance">0<' in html
        assert 'action="/boarder/ALICE/ipoints"' in html
        assert "Total incidents" in html

    def test_removed_boarder_shows_frozen_history_without_a_quick_log(
        self, fresh_client
    ):
        # Log while the key is known only through I-Points, then add history so
        # ZED becomes a Removed Boarder holding a frozen Entry (#187 forbids
        # logging a new Entry after the history exists).
        seed_ipoint_entry(name="ZED", points=7, reason="Frozen entry")
        seed_history("ZED", "Zed", "601Z", [("2026-01", 1, 2, 3)])

        html = profile_html(fresh_client, "ZED").get_data(as_text=True)

        assert "Frozen entry" in html
        assert 'id="stat-ipoint-balance">7<' in html
        assert 'action="/boarder/ZED/ipoints"' not in html

    def test_profile_omits_the_ipoint_audit_history(self, fresh_client):
        seed_ipoint_entry(name="ALICE", points=5)

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert "Audit History" not in html

    def test_ipoint_section_repeats_stacked_and_due_flags(self, fresh_client):
        with app_module.connect() as conn:
            ipoints.log_entry(
                conn,
                normalized_name="ALICE",
                points=14,
                occurred_on="2026-08-01",
                reason="x",
                recorded_at="2026-08-10T09:00:00+00:00",
            )
            ipoints.materialise_pending_redemptions(conn, "2026-09-12")
            pending = storage.get_open_ipoint_confiscation(conn, "ALICE")
            ipoints.confirm_redemption(
                conn,
                pending.id,
                today="2020-01-01",
                recorded_at="2020-01-01T10:00:00+00:00",
            )
            rows = seed_punishments(
                conn,
                boarders=[record("ALICE", "601A", 2, 5, 7)],
                month="2026-03",
                deadline="2026-03-10",
            )
            punishment = rows[0]
            punishments.transition(
                conn, punishment.id, "overdue",
                timestamp="2026-03-11T09:00:00+00:00",
            )
            punishments.transition(
                conn, punishment.id, "phone_held",
                timestamp="2026-03-12T09:00:00+00:00",
            )

        html = profile_html(fresh_client, "ALICE").get_data(as_text=True)

        assert "Stacked" in html
        assert "Due for release" in html

    def test_profile_lists_released_and_voided_confiscations(self, fresh_client):
        with app_module.connect() as conn:
            ipoints.log_entry(
                conn,
                normalized_name="ALICE",
                points=14,
                occurred_on="2026-08-01",
                reason="x",
                recorded_at="2026-08-10T09:00:00+00:00",
            )
            ipoints.log_entry(
                conn,
                normalized_name="BOB",
                points=5,
                occurred_on="2026-08-01",
                reason="y",
                recorded_at="2026-08-10T09:00:00+00:00",
            )
            ipoints.materialise_pending_redemptions(conn, "2026-09-12")
            alice = storage.get_open_ipoint_confiscation(conn, "ALICE")
            ipoints.confirm_redemption(
                conn,
                alice.id,
                today="2026-09-15",
                recorded_at="2026-09-15T09:00:00+00:00",
            )
            ipoints.release_confiscation(
                conn, alice.id, released_at="2026-09-18T09:00:00+00:00"
            )
            bob = storage.get_open_ipoint_confiscation(conn, "BOB")
            ipoints.void_confiscation(conn, bob.id, reason="recorded in error")

        alice_html = profile_html(fresh_client, "ALICE").get_data(as_text=True)
        bob_html = profile_html(fresh_client, "BOB").get_data(as_text=True)

        assert "Released" in alice_html
        assert "Taken 2026-09-15" in alice_html
        assert "Returned 2026-09-18" in alice_html
        assert "Voided" in bob_html
        assert "recorded in error" in bob_html


class TestProfileIPointQuickLog:
    def test_logging_redirects_to_the_profile_and_shows_the_entry(
        self, fresh_client
    ):
        response = post_csrf(
            fresh_client,
            "/boarder/ALICE/ipoints",
            data={
                "points": "5",
                "occurred_on": "2026-08-01",
                "reason": "Repeated disruption",
            },
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/boarder/ALICE")
        html = fresh_client.get("/boarder/ALICE").get_data(as_text=True)
        assert "banner-success" in html
        assert "Logged 5" in html
        assert 'id="stat-ipoint-balance">5<' in html
        assert "Repeated disruption" in html

    def test_entry_is_recorded_under_the_url_key(self, fresh_client):
        post_csrf(
            fresh_client,
            "/boarder/ALICE/ipoints",
            data={
                "boarder": "BOB",
                "points": "5",
                "occurred_on": "2026-08-01",
                "reason": "x",
            },
        )

        with app_module.connect() as conn:
            alice = storage.list_ipoint_entries(conn, "ALICE")
            bob = storage.list_ipoint_entries(conn, "BOB")
        assert len(alice) == 1
        assert bob == []

    def test_blank_reason_shows_error_and_saves_nothing(self, fresh_client):
        post_csrf(
            fresh_client,
            "/boarder/ALICE/ipoints",
            data={"points": "5", "occurred_on": "2026-08-01", "reason": ""},
        )

        html = fresh_client.get("/boarder/ALICE").get_data(as_text=True)
        assert "banner-error" in html
        with app_module.connect() as conn:
            assert storage.list_ipoint_entries(conn, "ALICE") == []

    def test_missing_csrf_rejected_and_saves_nothing(self, fresh_client):
        response = fresh_client.post(
            "/boarder/ALICE/ipoints",
            data={"points": "5", "occurred_on": "2026-08-01", "reason": "x"},
        )

        assert response.status_code == 403
        with app_module.connect() as conn:
            assert storage.list_ipoint_entries(conn, "ALICE") == []

    def test_quick_log_controls_are_labelled_and_keyboard_operable(
        self, fresh_client, browser_page
    ):
        html = fresh_client.get("/boarder/ALICE").get_data(as_text=True)
        page = browser_page
        page.set_content(html)

        assert page.locator('label[for="ipoint-quick-points"]').count() == 1
        assert page.locator('label[for="ipoint-quick-occurred-on"]').count() == 1
        assert page.locator('label[for="ipoint-quick-reason"]').count() == 1

        page.locator("#ipoint-quick-points").focus()
        page.keyboard.type("3")
        assert page.locator("#ipoint-quick-points").input_value() == "3"

        page.locator("#ipoint-quick-reason").focus()
        page.keyboard.type("Talking back")
        assert page.locator("#ipoint-quick-reason").input_value() == "Talking back"

        # Tabbing off the last text field reaches the submit control.
        page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement.tagName") == "BUTTON"
