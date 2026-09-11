"""Coverage for the I-Points ledger's first slice (#176).

Seams: the HTTP routes via the Flask test client (primary) and the new
``ipoints`` lifecycle module over the in-memory connection fixture
(secondary), mirroring the Punishments tests. Storage is exercised through
the lifecycle so no separate storage seam is introduced.
"""

import json

from helpers import post_csrf

import ipoints
import storage
from ipoints import EntryRejected, EntrySaved, log_entry


def seed_entry(
    conn,
    name="ALICE",
    points=5,
    occurred_on="2026-08-01",
    reason="Repeated disruption",
    recorded_at="2026-08-01T09:00:00+00:00",
):
    outcome = log_entry(
        conn,
        normalized_name=name,
        points=points,
        occurred_on=occurred_on,
        reason=reason,
        recorded_at=recorded_at,
    )
    assert isinstance(outcome, EntrySaved)
    return outcome


class TestLogEntry:
    def test_logs_entry_and_reports_success(self, conn):
        outcome = seed_entry(conn)

        assert isinstance(outcome, EntrySaved)
        assert "5" in outcome.message
        assert "ALICE" in outcome.message
        assert "2026-08-01" in outcome.message

    def test_entry_is_persisted(self, conn):
        seed_entry(conn, name="ALICE", points=3, reason="Late to prep")

        entries = storage.list_ipoint_entries(conn, "ALICE")

        assert len(entries) == 1
        assert entries[0].points == 3
        assert entries[0].occurred_on == "2026-08-01"
        assert entries[0].reason == "Late to prep"

    def test_records_the_provided_timestamp(self, conn):
        seed_entry(conn, recorded_at="2026-08-02T10:30:00+00:00")

        assert (
            storage.list_ipoint_entries(conn)[0].recorded_at
            == "2026-08-02T10:30:00+00:00"
        )

    def test_success_message_pluralizes_one_point(self, conn):
        outcome = seed_entry(conn, points=1)

        assert "1 I-Point " in outcome.message
        assert "I-Points" not in outcome.message

    def test_blank_reason_rejected(self, conn):
        outcome = log_entry(
            conn, normalized_name="ALICE", points=5, occurred_on="2026-08-01", reason="  "
        )

        assert isinstance(outcome, EntryRejected)
        assert "reason" in outcome.reason.lower()
        assert storage.list_ipoint_entries(conn) == []

    def test_zero_points_rejected(self, conn):
        outcome = log_entry(
            conn, normalized_name="ALICE", points=0, occurred_on="2026-08-01", reason="x"
        )

        assert isinstance(outcome, EntryRejected)
        assert storage.list_ipoint_entries(conn) == []

    def test_negative_points_rejected(self, conn):
        outcome = log_entry(
            conn, normalized_name="ALICE", points=-3, occurred_on="2026-08-01", reason="x"
        )

        assert isinstance(outcome, EntryRejected)
        assert storage.list_ipoint_entries(conn) == []

    def test_non_integer_points_rejected(self, conn):
        outcome = log_entry(
            conn, normalized_name="ALICE", points="five", occurred_on="2026-08-01", reason="x"
        )

        assert isinstance(outcome, EntryRejected)
        assert storage.list_ipoint_entries(conn) == []

    def test_fractional_points_rejected(self, conn):
        for points in ("5.9", 5.9, True):
            outcome = log_entry(
                conn, normalized_name="ALICE", points=points, occurred_on="2026-08-01", reason="x"
            )

            assert isinstance(outcome, EntryRejected)
        assert storage.list_ipoint_entries(conn) == []

    def test_non_decimal_digit_class_points_rejected(self, conn):
        for points in ("²", "³", "½"):
            outcome = log_entry(
                conn, normalized_name="ALICE", points=points, occurred_on="2026-08-01", reason="x"
            )

            assert isinstance(outcome, EntryRejected)
        assert storage.list_ipoint_entries(conn) == []

    def test_string_positive_integer_accepted(self, conn):
        outcome = log_entry(
            conn, normalized_name="ALICE", points="7", occurred_on="2026-08-01", reason="x"
        )

        assert isinstance(outcome, EntrySaved)
        assert storage.list_ipoint_entries(conn)[0].points == 7

    def test_blank_boarder_rejected(self, conn):
        outcome = log_entry(
            conn, normalized_name="  ", points=5, occurred_on="2026-08-01", reason="x"
        )

        assert isinstance(outcome, EntryRejected)
        assert storage.list_ipoint_entries(conn) == []

    def test_blank_date_defaults_to_injected_today(self, conn):
        outcome = log_entry(
            conn,
            normalized_name="ALICE",
            points=5,
            occurred_on="",
            reason="x",
            today="2026-09-11",
        )

        assert isinstance(outcome, EntrySaved)
        assert storage.list_ipoint_entries(conn)[0].occurred_on == "2026-09-11"

    def test_invalid_date_rejected(self, conn):
        outcome = log_entry(
            conn, normalized_name="ALICE", points=5, occurred_on="not-a-date", reason="x"
        )

        assert isinstance(outcome, EntryRejected)
        assert storage.list_ipoint_entries(conn) == []

    def test_audit_row_written_with_entry(self, conn):
        seed_entry(conn, name="ALICE", points=5, reason="Repeated disruption")

        audits = storage.list_ipoint_audit(conn, "ALICE")

        assert len(audits) == 1
        audit = audits[0]
        assert audit.entity_type == "entry"
        assert audit.action == "created"
        assert audit.normalized_name == "ALICE"
        assert audit.before_state is None
        after = json.loads(audit.after_state)
        assert after["points"] == 5
        assert after["reason"] == "Repeated disruption"
        entry_id = storage.list_ipoint_entries(conn)[0].id
        assert audit.entity_id == entry_id

    def test_audit_lists_newest_first(self, conn):
        seed_entry(conn, points=1, reason="first")
        seed_entry(conn, points=2, reason="second")

        reasons = [
            json.loads(audit.after_state)["reason"]
            for audit in storage.list_ipoint_audit(conn)
        ]

        assert reasons == ["second", "first"]

    def test_rejected_entry_writes_no_audit_row(self, conn):
        log_entry(
            conn, normalized_name="ALICE", points=0, occurred_on="2026-08-01", reason="x"
        )

        assert storage.list_ipoint_audit(conn) == []


class TestBalance:
    def test_balance_sums_entries_for_one_boarder(self, conn):
        seed_entry(conn, name="ALICE", points=2)
        seed_entry(conn, name="ALICE", points=3)

        summaries = ipoints.boarder_balances(conn)

        alice = next(s for s in summaries if s.normalized_name == "ALICE")
        assert alice.balance == 5

    def test_balance_isolated_per_boarder(self, conn):
        seed_entry(conn, name="ALICE", points=2)
        seed_entry(conn, name="BOB", points=7)

        balances = {s.normalized_name: s.balance for s in ipoints.boarder_balances(conn)}

        assert balances == {"ALICE": 2, "BOB": 7}

    def test_summary_groups_entries_chronologically(self, conn):
        seed_entry(conn, name="ALICE", points=1, occurred_on="2026-09-01")
        seed_entry(conn, name="ALICE", points=2, occurred_on="2026-07-01")

        alice = ipoints.boarder_balances(conn)[0]

        assert [e.occurred_on for e in alice.entries] == ["2026-07-01", "2026-09-01"]

    def test_summary_resolves_display_name_and_bed(self, conn):
        storage.replace_boarders(
            conn,
            [
                storage.Boarder(
                    normalized_name="ALICE", display_name="Alice", bed="601A"
                )
            ],
        )
        seed_entry(conn, name="ALICE", points=5)

        alice = ipoints.boarder_balances(conn)[0]

        assert alice.display_name == "Alice"
        assert alice.bed == "601A"

    def test_no_entries_yields_no_summaries(self, conn):
        assert ipoints.boarder_balances(conn) == []


class TestMatchKeyMigration:
    def test_migration_rekeys_ipoint_tables(self, conn):
        conn.execute(
            """
            INSERT INTO ipoint_entries
                (normalized_name, points, occurred_on, reason, recorded_at)
            VALUES ('CHEN, WEI', 5, '2026-08-01', 'x', '2026-08-01T09:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO ipoint_audit
                (entity_type, entity_id, normalized_name, action, changed_at)
            VALUES ('entry', 1, 'CHEN, WEI', 'created', '2026-08-01T09:00:00+00:00')
            """
        )

        storage.create_schema(conn)

        assert storage.list_ipoint_entries(conn)[0].normalized_name == "CHEN WEI"
        assert storage.list_ipoint_audit(conn)[0].normalized_name == "CHEN WEI"


class TestIPointsPage:
    def _page(self, client):
        response = client.get("/ipoints")
        assert response.status_code == 200
        return response.get_data(as_text=True)

    def test_tab_links_to_the_ipoints_page(self, fresh_client):
        html = fresh_client.get("/").get_data(as_text=True)

        assert 'href="/ipoints"' in html
        assert "I-Points" in html

    def test_page_extends_the_shared_layout(self, fresh_client):
        html = self._page(fresh_client)

        assert 'class="site-header"' in html
        assert 'id="confirmModal"' in html

    def test_page_has_a_log_entry_form_with_labelled_controls(self, fresh_client):
        html = self._page(fresh_client)

        assert 'action="/ipoints/entries"' in html
        assert 'for="ipoint-boarder"' in html
        assert 'for="ipoint-points"' in html
        assert 'for="ipoint-occurred-on"' in html
        assert 'for="ipoint-reason"' in html

    def test_date_field_defaults_to_today(self, fresh_client):
        from datetime import datetime

        today = datetime.now().astimezone().date().isoformat()
        html = self._page(fresh_client)

        assert f'value="{today}"' in html

    def test_empty_page_shows_empty_state(self, fresh_client):
        html = self._page(fresh_client)

        assert "No I-Point Entries" in html


class TestLogEntryRoute:
    def test_logging_redirects_and_lists_the_entry(self, fresh_client):
        response = post_csrf(
            fresh_client,
            "/ipoints/entries",
            data={
                "boarder": "Alice",
                "points": "5",
                "occurred_on": "2026-08-01",
                "reason": "Repeated disruption",
            },
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/ipoints")
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "Repeated disruption" in html
        assert "Balance: 5" in html

    def test_ledger_lists_only_the_entry_fields(self, fresh_client):
        post_csrf(
            fresh_client,
            "/ipoints/entries",
            data={
                "boarder": "Alice",
                "points": "5",
                "occurred_on": "2026-08-01",
                "reason": "Repeated disruption",
            },
        )

        html = fresh_client.get("/ipoints").get_data(as_text=True)
        header = html[html.index("<thead>") : html.index("</thead>")]

        assert "Date" in header
        assert "Points" in header
        assert "Reason" in header
        assert "Logged" not in header

    def test_logging_shows_success_feedback(self, fresh_client):
        post_csrf(
            fresh_client,
            "/ipoints/entries",
            data={
                "boarder": "Alice",
                "points": "5",
                "occurred_on": "2026-08-01",
                "reason": "Repeated disruption",
            },
        )

        html = fresh_client.get("/ipoints").get_data(as_text=True)

        assert "banner-success" in html
        assert "Logged 5" in html

    def test_balance_accumulates_across_entries(self, fresh_client):
        for points in ("2", "3"):
            post_csrf(
                fresh_client,
                "/ipoints/entries",
                data={
                    "boarder": "Alice",
                    "points": points,
                    "occurred_on": "2026-08-01",
                    "reason": "x",
                },
            )

        html = fresh_client.get("/ipoints").get_data(as_text=True)

        assert "Balance: 5" in html

    def test_blank_reason_shows_error_and_saves_nothing(self, fresh_client):
        post_csrf(
            fresh_client,
            "/ipoints/entries",
            data={
                "boarder": "Alice",
                "points": "5",
                "occurred_on": "2026-08-01",
                "reason": "",
            },
        )

        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-error" in html
        assert "reason" in html.lower()
        assert "No I-Point Entries" in html

    def test_non_positive_points_shows_error(self, fresh_client):
        post_csrf(
            fresh_client,
            "/ipoints/entries",
            data={
                "boarder": "Alice",
                "points": "0",
                "occurred_on": "2026-08-01",
                "reason": "x",
            },
        )

        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-error" in html
        assert "No I-Point Entries" in html

    def test_non_decimal_digit_points_shows_error(self, fresh_client):
        response = post_csrf(
            fresh_client,
            "/ipoints/entries",
            data={
                "boarder": "Alice",
                "points": "²",
                "occurred_on": "2026-08-01",
                "reason": "x",
            },
        )

        assert response.status_code == 302
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-error" in html
        assert "No I-Point Entries" in html

    def test_missing_csrf_rejected_and_saves_nothing(self, fresh_client):
        response = fresh_client.post(
            "/ipoints/entries",
            data={
                "boarder": "Alice",
                "points": "5",
                "occurred_on": "2026-08-01",
                "reason": "x",
            },
        )

        assert response.status_code == 403
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "No I-Point Entries" in html
