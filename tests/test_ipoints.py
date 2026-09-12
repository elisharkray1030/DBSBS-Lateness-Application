"""Coverage for the I-Points ledger's first slice (#176).

Seams: the HTTP routes via the Flask test client (primary) and the new
``ipoints`` lifecycle module over the in-memory connection fixture
(secondary), mirroring the Punishments tests. Storage is exercised through
the lifecycle so no separate storage seam is introduced.
"""

import json

from helpers import post_csrf

import app as app_module
import ipoints
import storage
from ipoints import (
    AdjustmentEdited,
    AdjustmentRejected,
    AdjustmentRemoved,
    AdjustmentSaved,
    EntryRejected,
    EntrySaved,
    log_entry,
)


def _entry_id(conn, name="ALICE"):
    return storage.list_ipoint_entries(conn, name)[0].id


def _seed_offset(conn, name, points):
    """Writes a raw negative Entry to stand in for a subtractive Adjustment.

    ADR 0006 ties the non-negative floor to a +N Entry offset by a −N debit.
    Until Adjustments land (#178) the only way to reach that offsetting state
    is a direct row, so the floor guard can be exercised now.
    """
    conn.execute(
        """
        INSERT INTO ipoint_entries
            (normalized_name, points, occurred_on, reason, recorded_at)
        VALUES (?, ?, '2026-08-03', 'offset', '2026-08-03T09:00:00+00:00')
        """,
        (name, points),
    )
    conn.commit()


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
        conn.execute(
            """
            INSERT INTO ipoint_adjustments
                (normalized_name, points, reason, recorded_at)
            VALUES ('CHEN, WEI', 3, 'x', '2026-08-01T09:00:00+00:00')
            """
        )

        storage.create_schema(conn)

        assert storage.list_ipoint_entries(conn)[0].normalized_name == "CHEN WEI"
        assert storage.list_ipoint_audit(conn)[0].normalized_name == "CHEN WEI"
        assert storage.list_ipoint_adjustments(conn)[0].normalized_name == "CHEN WEI"


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


class TestEditEntry:
    def test_edits_points_date_and_reason(self, conn):
        seed_entry(conn, name="ALICE", points=5, occurred_on="2026-08-01", reason="first")
        entry_id = _entry_id(conn)

        outcome = ipoints.edit_entry(
            conn, entry_id, points=3, occurred_on="2026-08-02", reason="corrected"
        )

        assert not isinstance(outcome, EntryRejected)
        entry = storage.list_ipoint_entries(conn)[0]
        assert entry.points == 3
        assert entry.occurred_on == "2026-08-02"
        assert entry.reason == "corrected"

    def test_edit_message_names_the_boarder(self, conn):
        seed_entry(conn, name="ALICE", points=5)
        entry_id = _entry_id(conn)

        outcome = ipoints.edit_entry(conn, entry_id, 2, "2026-08-05", "corrected")

        assert "ALICE" in outcome.message

    def test_edit_retains_the_prior_state_in_the_audit(self, conn):
        seed_entry(conn, name="ALICE", points=5, occurred_on="2026-08-01", reason="first")
        entry_id = _entry_id(conn)

        ipoints.edit_entry(conn, entry_id, points=3, occurred_on="2026-08-02", reason="corrected")

        audits = storage.list_ipoint_audit(conn, "ALICE")
        assert [audit.action for audit in audits] == ["edited", "created"]
        edit = audits[0]
        assert edit.entity_id == entry_id
        assert json.loads(edit.before_state) == {
            "points": 5,
            "occurred_on": "2026-08-01",
            "reason": "first",
        }
        assert json.loads(edit.after_state) == {
            "points": 3,
            "occurred_on": "2026-08-02",
            "reason": "corrected",
        }

    def test_edit_rejects_non_positive_points_without_writing(self, conn):
        seed_entry(conn, name="ALICE", points=5)
        entry_id = _entry_id(conn)

        outcome = ipoints.edit_entry(conn, entry_id, 0, "2026-08-01", "x")

        assert isinstance(outcome, EntryRejected)
        assert storage.list_ipoint_entries(conn)[0].points == 5
        assert [audit.action for audit in storage.list_ipoint_audit(conn)] == ["created"]

    def test_edit_rejects_a_blank_reason(self, conn):
        seed_entry(conn, name="ALICE", points=5)
        entry_id = _entry_id(conn)

        outcome = ipoints.edit_entry(conn, entry_id, 3, "2026-08-01", "  ")

        assert isinstance(outcome, EntryRejected)
        assert storage.list_ipoint_entries(conn)[0].points == 5

    def test_edit_rejects_an_invalid_date(self, conn):
        seed_entry(conn, name="ALICE", points=5)
        entry_id = _entry_id(conn)

        outcome = ipoints.edit_entry(conn, entry_id, 3, "not-a-date", "x")

        assert isinstance(outcome, EntryRejected)
        assert storage.list_ipoint_entries(conn)[0].occurred_on == "2026-08-01"

    def test_edit_unknown_entry_rejected(self, conn):
        outcome = ipoints.edit_entry(conn, 999, 5, "2026-08-01", "x")

        assert isinstance(outcome, EntryRejected)
        assert storage.list_ipoint_audit(conn) == []

    def test_edit_that_would_go_negative_is_refused(self, conn):
        seed_entry(conn, name="ALICE", points=5)
        _seed_offset(conn, "ALICE", -5)
        target = next(
            entry
            for entry in storage.list_ipoint_entries(conn, "ALICE")
            if entry.points == 5
        )

        outcome = ipoints.edit_entry(conn, target.id, 1, "2026-08-01", "x")

        assert isinstance(outcome, EntryRejected)
        assert "below zero" in outcome.reason.lower()
        assert target.id in [entry.id for entry in storage.list_ipoint_entries(conn, "ALICE")]
        assert [audit.action for audit in storage.list_ipoint_audit(conn, "ALICE")] == ["created"]


class TestRemoveEntry:
    def test_remove_deletes_the_entry_and_its_points(self, conn):
        seed_entry(conn, name="ALICE", points=5)
        entry_id = _entry_id(conn)

        outcome = ipoints.remove_entry(conn, entry_id)

        assert not isinstance(outcome, EntryRejected)
        assert storage.list_ipoint_entries(conn) == []

    def test_remove_retains_the_prior_state_in_the_audit(self, conn):
        seed_entry(conn, name="ALICE", points=5, occurred_on="2026-08-01", reason="first")
        entry_id = _entry_id(conn)

        ipoints.remove_entry(conn, entry_id)

        audits = storage.list_ipoint_audit(conn, "ALICE")
        assert audits[0].action == "removed"
        assert audits[0].entity_id == entry_id
        assert audits[0].after_state is None
        assert json.loads(audits[0].before_state)["reason"] == "first"

    def test_remove_message_names_the_boarder(self, conn):
        seed_entry(conn, name="ALICE", points=5)
        entry_id = _entry_id(conn)

        outcome = ipoints.remove_entry(conn, entry_id)

        assert "ALICE" in outcome.message

    def test_remove_unknown_entry_rejected(self, conn):
        outcome = ipoints.remove_entry(conn, 999)

        assert isinstance(outcome, EntryRejected)
        assert storage.list_ipoint_audit(conn) == []

    def test_remove_that_would_go_negative_is_refused(self, conn):
        seed_entry(conn, name="ALICE", points=5)
        _seed_offset(conn, "ALICE", -5)
        target = next(
            entry
            for entry in storage.list_ipoint_entries(conn, "ALICE")
            if entry.points == 5
        )

        outcome = ipoints.remove_entry(conn, target.id)

        assert isinstance(outcome, EntryRejected)
        assert len(storage.list_ipoint_entries(conn, "ALICE")) == 2


class TestAuditHistory:
    def test_summary_carries_newest_first_audit_notes(self, conn):
        seed_entry(conn, name="ALICE", points=5, reason="first")
        entry_id = _entry_id(conn)
        ipoints.edit_entry(conn, entry_id, 3, "2026-08-02", "corrected")

        summary = next(
            s for s in ipoints.boarder_balances(conn) if s.normalized_name == "ALICE"
        )

        assert [note.action for note in summary.audits] == ["Edited", "Created"]
        assert "first" in summary.audits[0].description
        assert "corrected" in summary.audits[0].description

    def test_removed_entry_keeps_the_boarder_in_the_audit_view(self, conn):
        seed_entry(conn, name="ALICE", points=5, reason="first")
        ipoints.remove_entry(conn, _entry_id(conn))

        summary = next(
            s for s in ipoints.boarder_balances(conn) if s.normalized_name == "ALICE"
        )

        assert summary.entries == []
        assert summary.balance == 0
        assert summary.audits[0].action == "Removed"
        assert "first" in summary.audits[0].description

    def test_no_entries_or_audits_yields_no_summaries(self, conn):
        assert ipoints.boarder_balances(conn) == []


class TestEditEntryRoute:
    def _seed(self, fresh_client):
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
        with app_module.connect() as conn:
            return _entry_id(conn)

    def test_edit_updates_the_entry_and_flashes_success(self, fresh_client):
        entry_id = self._seed(fresh_client)

        response = post_csrf(
            fresh_client,
            f"/ipoints/entries/{entry_id}/edit",
            data={"points": "2", "occurred_on": "2026-08-09", "reason": "corrected"},
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/ipoints")
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert 'value="corrected"' in html
        assert "Balance: 2" in html
        assert "banner-success" in html

    def test_edit_shows_the_prior_state_in_the_audit_view(self, fresh_client):
        entry_id = self._seed(fresh_client)

        post_csrf(
            fresh_client,
            f"/ipoints/entries/{entry_id}/edit",
            data={"points": "2", "occurred_on": "2026-08-09", "reason": "corrected"},
        )

        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "Audit History" in html
        assert "Repeated disruption" in html
        assert "corrected" in html

    def test_edit_invalid_points_shows_error_and_keeps_the_entry(self, fresh_client):
        entry_id = self._seed(fresh_client)

        post_csrf(
            fresh_client,
            f"/ipoints/entries/{entry_id}/edit",
            data={"points": "0", "occurred_on": "2026-08-01", "reason": "x"},
        )

        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-error" in html
        assert 'value="Repeated disruption"' in html

    def test_edit_without_csrf_is_rejected(self, fresh_client):
        entry_id = self._seed(fresh_client)

        response = fresh_client.post(
            f"/ipoints/entries/{entry_id}/edit",
            data={"points": "2", "occurred_on": "2026-08-09", "reason": "corrected"},
        )

        assert response.status_code == 403
        with app_module.connect() as conn:
            assert storage.list_ipoint_entries(conn)[0].points == 5


class TestRemoveEntryRoute:
    def _seed(self, fresh_client):
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
        with app_module.connect() as conn:
            return _entry_id(conn)

    def test_remove_deletes_the_entry_and_keeps_the_audit(self, fresh_client):
        entry_id = self._seed(fresh_client)

        response = post_csrf(
            fresh_client, f"/ipoints/entries/{entry_id}/remove"
        )

        assert response.status_code == 302
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "Balance: 0" in html
        assert "Audit History" in html
        assert "Removed" in html
        assert "Repeated disruption" in html

    def test_remove_without_csrf_is_rejected(self, fresh_client):
        entry_id = self._seed(fresh_client)

        response = fresh_client.post(f"/ipoints/entries/{entry_id}/remove")

        assert response.status_code == 403
        with app_module.connect() as conn:
            assert len(storage.list_ipoint_entries(conn)) == 1

    def test_overdraw_refusal_is_surfaced_as_page_feedback(self, fresh_client):
        entry_id = self._seed(fresh_client)
        with app_module.connect() as conn:
            _seed_offset(conn, "ALICE", -5)

        post_csrf(fresh_client, f"/ipoints/entries/{entry_id}/remove")

        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-error" in html
        assert "below zero" in html.lower()
        with app_module.connect() as conn:
            assert len(storage.list_ipoint_entries(conn, "ALICE")) == 2


class TestIPointsEditingControls:
    def test_page_renders_labelled_edit_and_remove_controls(self, fresh_client):
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
        with app_module.connect() as conn:
            entry_id = _entry_id(conn)

        html = fresh_client.get("/ipoints").get_data(as_text=True)

        assert f'action="/ipoints/entries/{entry_id}/edit"' in html
        assert f'action="/ipoints/entries/{entry_id}/remove"' in html
        assert f'form="ipoint-edit-{entry_id}"' in html
        assert f'form="ipoint-remove-{entry_id}"' in html
        assert 'aria-label="Reason for entry' in html
        assert 'aria-label="Remove entry from' in html


def _stub_form_submit(page):
    page.evaluate(
        """() => {
            window.__submitCalled = null;
            HTMLFormElement.prototype.submit = function() {
                window.__submitCalled = this.getAttribute('action');
            };
        }"""
    )


class TestIPointsEditRemoveBrowser:
    def test_inline_edit_and_remove_are_keyboard_operable(self, fresh_client, browser_page):
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

        page = browser_page
        page.set_content(html)
        _stub_form_submit(page)
        page.evaluate(
            """() => {
                window.__editPayload = null;
                window.__editAction = null;
                document.querySelector('form[id^="ipoint-edit-"]').addEventListener('submit', event => {
                    event.preventDefault();
                    const form = event.target;
                    window.__editAction = form.getAttribute('action');
                    window.__editPayload = Object.fromEntries(new FormData(form).entries());
                });
            }"""
        )

        reason = page.locator('input[aria-label^="Reason for entry"]')
        reason.fill("corrected")
        page.locator('button[form^="ipoint-edit-"]').focus()
        page.keyboard.press("Enter")

        payload = page.evaluate("() => window.__editPayload")
        assert payload is not None
        assert payload["reason"] == "corrected"
        assert payload["points"] == "5"
        assert page.evaluate("() => window.__editAction").endswith("/edit")

        page.locator('button[form^="ipoint-remove-"]').focus()
        page.keyboard.press("Enter")

        assert page.locator("#confirmModal.show").count() == 1
        message = page.locator("#confirm-modal-message").text_content()
        assert "ALICE" in message.upper()
        page.keyboard.press("Enter")
        assert page.evaluate("() => window.__submitCalled").endswith("/remove")


class TestAddAdjustment:
    def test_adds_a_positive_adjustment_and_reports_success(self, conn):
        outcome = ipoints.add_adjustment(
            conn, normalized_name="ALICE", points=5, reason="Correction"
        )

        assert isinstance(outcome, AdjustmentSaved)
        assert "5" in outcome.message
        assert "ALICE" in outcome.message

    def test_a_positive_adjustment_is_persisted(self, conn):
        ipoints.add_adjustment(conn, "ALICE", 5, "Correction")

        adjustments = storage.list_ipoint_adjustments(conn, "ALICE")

        assert len(adjustments) == 1
        assert adjustments[0].points == 5
        assert adjustments[0].reason == "Correction"

    def test_a_signed_string_value_is_accepted(self, conn):
        seed_entry(conn, name="ALICE", points=5)

        outcome = ipoints.add_adjustment(conn, "ALICE", "-3", "Correction")

        assert isinstance(outcome, AdjustmentSaved)
        assert storage.list_ipoint_adjustments(conn, "ALICE")[0].points == -3

    def test_adjustment_writes_its_audit_row(self, conn):
        ipoints.add_adjustment(conn, "ALICE", 5, "Correction")

        audits = storage.list_ipoint_audit(conn, "ALICE")

        assert len(audits) == 1
        assert audits[0].entity_type == "adjustment"
        assert audits[0].action == "created"
        assert audits[0].before_state is None
        assert json.loads(audits[0].after_state) == {
            "points": 5,
            "reason": "Correction",
        }
        assert audits[0].entity_id == storage.list_ipoint_adjustments(conn, "ALICE")[0].id

    def test_zero_points_rejected(self, conn):
        outcome = ipoints.add_adjustment(conn, "ALICE", 0, "Correction")

        assert isinstance(outcome, AdjustmentRejected)
        assert storage.list_ipoint_adjustments(conn) == []

    def test_blank_reason_rejected(self, conn):
        outcome = ipoints.add_adjustment(conn, "ALICE", 5, "  ")

        assert isinstance(outcome, AdjustmentRejected)
        assert "reason" in outcome.reason.lower()
        assert storage.list_ipoint_adjustments(conn) == []

    def test_non_integer_points_rejected(self, conn):
        for points in ("five", "1.5", 1.5, True, "²"):
            outcome = ipoints.add_adjustment(conn, "ALICE", points, "x")

            assert isinstance(outcome, AdjustmentRejected)

        assert storage.list_ipoint_adjustments(conn) == []

    def test_blank_boarder_rejected(self, conn):
        outcome = ipoints.add_adjustment(conn, "  ", 5, "x")

        assert isinstance(outcome, AdjustmentRejected)
        assert storage.list_ipoint_adjustments(conn) == []

    def test_rejected_adjustment_writes_no_audit_row(self, conn):
        ipoints.add_adjustment(conn, "ALICE", 0, "Correction")

        assert storage.list_ipoint_audit(conn) == []


class TestAdjustmentBalance:
    def _summary(self, conn, name="ALICE"):
        return next(
            s for s in ipoints.boarder_balances(conn) if s.normalized_name == name
        )

    def test_balance_combines_entries_and_adjustments(self, conn):
        seed_entry(conn, name="ALICE", points=5)
        ipoints.add_adjustment(conn, "ALICE", -2, "Correction")

        assert self._summary(conn).balance == 3

    def test_a_positive_adjustment_raises_the_balance_alone(self, conn):
        ipoints.add_adjustment(conn, "ALICE", 4, "Goodwill")

        assert self._summary(conn).balance == 4

    def test_summary_carries_the_adjustments(self, conn):
        ipoints.add_adjustment(conn, "ALICE", 2, "Goodwill")

        adjustments = self._summary(conn).adjustments

        assert len(adjustments) == 1
        assert adjustments[0].points == 2
        assert adjustments[0].reason == "Goodwill"

    def test_a_subtraction_equal_to_the_balance_is_allowed(self, conn):
        seed_entry(conn, name="ALICE", points=5)

        outcome = ipoints.add_adjustment(conn, "ALICE", -5, "Clear")

        assert isinstance(outcome, AdjustmentSaved)
        assert self._summary(conn).balance == 0

    def test_a_subtraction_beyond_the_balance_is_refused(self, conn):
        seed_entry(conn, name="ALICE", points=5)

        outcome = ipoints.add_adjustment(conn, "ALICE", -6, "Too much")

        assert isinstance(outcome, AdjustmentRejected)
        assert "below zero" in outcome.reason.lower()
        assert storage.list_ipoint_adjustments(conn) == []
        assert [a.action for a in storage.list_ipoint_audit(conn, "ALICE")] == [
            "created"
        ]


class TestEditAdjustment:
    def test_edits_points_and_reason(self, conn):
        ipoints.add_adjustment(conn, "ALICE", 3, "first")
        adjustment_id = _adjustment_id(conn)

        outcome = ipoints.edit_adjustment(conn, adjustment_id, 2, "corrected")

        assert isinstance(outcome, AdjustmentEdited)
        adjustment = storage.list_ipoint_adjustments(conn)[0]
        assert adjustment.points == 2
        assert adjustment.reason == "corrected"

    def test_a_signed_edit_can_flip_the_sign(self, conn):
        seed_entry(conn, name="ALICE", points=5)
        ipoints.add_adjustment(conn, "ALICE", 2, "first")

        outcome = ipoints.edit_adjustment(conn, _adjustment_id(conn), -4, "flip")

        assert isinstance(outcome, AdjustmentEdited)
        assert storage.list_ipoint_adjustments(conn)[0].points == -4

    def test_edit_retains_the_prior_state_in_the_audit(self, conn):
        ipoints.add_adjustment(conn, "ALICE", 3, "first")
        adjustment_id = _adjustment_id(conn)

        ipoints.edit_adjustment(conn, adjustment_id, 2, "corrected")

        audits = storage.list_ipoint_audit(conn, "ALICE")
        assert [audit.action for audit in audits] == ["edited", "created"]
        assert audits[0].entity_type == "adjustment"
        assert audits[0].entity_id == adjustment_id
        assert json.loads(audits[0].before_state) == {"points": 3, "reason": "first"}
        assert json.loads(audits[0].after_state) == {
            "points": 2,
            "reason": "corrected",
        }

    def test_edit_message_names_the_boarder(self, conn):
        ipoints.add_adjustment(conn, "ALICE", 3, "first")

        outcome = ipoints.edit_adjustment(conn, _adjustment_id(conn), 2, "x")

        assert "ALICE" in outcome.message

    def test_edit_rejects_zero_points_without_writing(self, conn):
        ipoints.add_adjustment(conn, "ALICE", 3, "first")

        outcome = ipoints.edit_adjustment(conn, _adjustment_id(conn), 0, "x")

        assert isinstance(outcome, AdjustmentRejected)
        assert storage.list_ipoint_adjustments(conn)[0].points == 3
        assert [audit.action for audit in storage.list_ipoint_audit(conn)] == ["created"]

    def test_edit_rejects_a_blank_reason(self, conn):
        ipoints.add_adjustment(conn, "ALICE", 3, "first")

        outcome = ipoints.edit_adjustment(conn, _adjustment_id(conn), 2, "  ")

        assert isinstance(outcome, AdjustmentRejected)
        assert storage.list_ipoint_adjustments(conn)[0].reason == "first"

    def test_edit_unknown_adjustment_rejected(self, conn):
        outcome = ipoints.edit_adjustment(conn, 999, 2, "x")

        assert isinstance(outcome, AdjustmentRejected)
        assert storage.list_ipoint_audit(conn) == []

    def test_edit_that_would_go_negative_is_refused(self, conn):
        seed_entry(conn, name="ALICE", points=5)
        ipoints.add_adjustment(conn, "ALICE", -3, "first")
        adjustment_id = _adjustment_id(conn)

        outcome = ipoints.edit_adjustment(conn, adjustment_id, -6, "too far")

        assert isinstance(outcome, AdjustmentRejected)
        assert storage.list_ipoint_adjustments(conn)[0].points == -3


class TestRemoveAdjustment:
    def test_remove_deletes_the_adjustment_and_keeps_the_audit(self, conn):
        ipoints.add_adjustment(conn, "ALICE", 3, "first")

        outcome = ipoints.remove_adjustment(conn, _adjustment_id(conn))

        assert isinstance(outcome, AdjustmentRemoved)
        assert storage.list_ipoint_adjustments(conn) == []
        audits = storage.list_ipoint_audit(conn, "ALICE")
        assert [audit.action for audit in audits] == ["removed", "created"]
        assert json.loads(audits[0].before_state) == {"points": 3, "reason": "first"}
        assert audits[0].entity_type == "adjustment"

    def test_remove_message_names_the_boarder(self, conn):
        ipoints.add_adjustment(conn, "ALICE", 3, "first")

        outcome = ipoints.remove_adjustment(conn, _adjustment_id(conn))

        assert "ALICE" in outcome.message

    def test_remove_unknown_adjustment_rejected(self, conn):
        outcome = ipoints.remove_adjustment(conn, 999)

        assert isinstance(outcome, AdjustmentRejected)
        assert storage.list_ipoint_audit(conn) == []

    def test_removing_a_positive_adjustment_that_covers_a_debt_is_allowed(self, conn):
        seed_entry(conn, name="ALICE", points=5)
        ipoints.add_adjustment(conn, "ALICE", -5, "offset")

        outcome = ipoints.remove_adjustment(conn, _adjustment_id(conn))

        assert isinstance(outcome, AdjustmentRemoved)
        assert ipoints.boarder_balances(conn)[0].balance == 5

    def test_removing_a_positive_adjustment_that_would_go_negative_is_refused(self, conn):
        _seed_offset(conn, "ALICE", -2)
        ipoints.add_adjustment(conn, "ALICE", 3, "first")

        outcome = ipoints.remove_adjustment(conn, _adjustment_id(conn))

        assert isinstance(outcome, AdjustmentRejected)
        assert "below zero" in outcome.reason.lower()
        assert len(storage.list_ipoint_adjustments(conn)) == 1


class TestAdjustmentAuditHistory:
    def test_summary_carries_newest_first_adjustment_notes(self, conn):
        ipoints.add_adjustment(conn, "ALICE", 3, "first")
        adjustment_id = storage.list_ipoint_adjustments(conn, "ALICE")[0].id
        ipoints.edit_adjustment(conn, adjustment_id, 2, "corrected")

        summary = next(
            s for s in ipoints.boarder_balances(conn) if s.normalized_name == "ALICE"
        )

        assert [note.action for note in summary.audits] == ["Edited", "Created"]
        assert "first" in summary.audits[0].description
        assert "corrected" in summary.audits[0].description


def _adjustment_id(conn, name="ALICE"):
    return storage.list_ipoint_adjustments(conn, name)[0].id


class TestAdjustmentForm:
    def _page(self, client):
        response = client.get("/ipoints")
        assert response.status_code == 200
        return response.get_data(as_text=True)

    def test_page_has_a_labelled_adjustment_form(self, fresh_client):
        html = self._page(fresh_client)

        assert 'action="/ipoints/adjustments"' in html
        assert 'for="adjustment-boarder"' in html
        assert 'for="adjustment-points"' in html
        assert 'for="adjustment-reason"' in html

    def test_ledger_has_a_type_column(self, fresh_client):
        post_csrf(
            fresh_client,
            "/ipoints/adjustments",
            data={"boarder": "Alice", "points": "4", "reason": "Goodwill"},
        )
        html = self._page(fresh_client)
        header = html[html.index("<thead>") : html.index("</thead>")]

        assert "Type" in header
        assert "Entry" in html
        assert "Adjustment" in html


class TestAddAdjustmentRoute:
    def test_adding_redirects_and_lists_the_adjustment(self, fresh_client):
        response = post_csrf(
            fresh_client,
            "/ipoints/adjustments",
            data={"boarder": "Alice", "points": "4", "reason": "Goodwill"},
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/ipoints")
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "Goodwill" in html
        assert "Balance: 4" in html
        assert "Adjustment" in html

    def test_zero_points_shows_error_and_saves_nothing(self, fresh_client):
        post_csrf(
            fresh_client,
            "/ipoints/adjustments",
            data={"boarder": "Alice", "points": "0", "reason": "x"},
        )

        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-error" in html
        assert "No I-Point Entries" in html

    def test_subtraction_beyond_the_balance_shows_error(self, fresh_client):
        post_csrf(
            fresh_client,
            "/ipoints/entries",
            data={
                "boarder": "Alice",
                "points": "5",
                "occurred_on": "2026-08-01",
                "reason": "x",
            },
        )
        post_csrf(
            fresh_client,
            "/ipoints/adjustments",
            data={"boarder": "Alice", "points": "-6", "reason": "Too much"},
        )

        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-error" in html
        assert "below zero" in html.lower()

    def test_adding_without_csrf_is_rejected(self, fresh_client):
        response = fresh_client.post(
            "/ipoints/adjustments",
            data={"boarder": "Alice", "points": "4", "reason": "Goodwill"},
        )

        assert response.status_code == 403
        with app_module.connect() as conn:
            assert storage.list_ipoint_adjustments(conn) == []


class TestEditAdjustmentRoute:
    def _seed(self, fresh_client):
        post_csrf(
            fresh_client,
            "/ipoints/entries",
            data={
                "boarder": "Alice",
                "points": "5",
                "occurred_on": "2026-08-01",
                "reason": "entry",
            },
        )
        post_csrf(
            fresh_client,
            "/ipoints/adjustments",
            data={"boarder": "Alice", "points": "4", "reason": "Goodwill"},
        )
        with app_module.connect() as conn:
            return _adjustment_id(conn)

    def test_edit_updates_the_adjustment_and_flashes_success(self, fresh_client):
        adjustment_id = self._seed(fresh_client)

        response = post_csrf(
            fresh_client,
            f"/ipoints/adjustments/{adjustment_id}/edit",
            data={"points": "-2", "reason": "corrected"},
        )

        assert response.status_code == 302
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "corrected" in html
        assert "Balance: 3" in html
        assert "banner-success" in html

    def test_edit_invalid_points_shows_error_and_keeps_the_adjustment(self, fresh_client):
        adjustment_id = self._seed(fresh_client)

        post_csrf(
            fresh_client,
            f"/ipoints/adjustments/{adjustment_id}/edit",
            data={"points": "0", "reason": "x"},
        )

        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-error" in html
        assert 'value="Goodwill"' in html

    def test_edit_without_csrf_is_rejected(self, fresh_client):
        adjustment_id = self._seed(fresh_client)

        response = fresh_client.post(
            f"/ipoints/adjustments/{adjustment_id}/edit",
            data={"points": "2", "reason": "corrected"},
        )

        assert response.status_code == 403
        with app_module.connect() as conn:
            assert storage.list_ipoint_adjustments(conn)[0].points == 4

    def test_overdraw_refusal_is_surfaced_as_page_feedback(self, fresh_client):
        adjustment_id = self._seed(fresh_client)

        post_csrf(
            fresh_client,
            f"/ipoints/adjustments/{adjustment_id}/edit",
            data={"points": "-6", "reason": "too far"},
        )

        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-error" in html
        assert "below zero" in html.lower()
        with app_module.connect() as conn:
            assert storage.list_ipoint_adjustments(conn)[0].points == 4


class TestRemoveAdjustmentRoute:
    def _seed(self, fresh_client):
        post_csrf(
            fresh_client,
            "/ipoints/adjustments",
            data={"boarder": "Alice", "points": "4", "reason": "Goodwill"},
        )
        with app_module.connect() as conn:
            return _adjustment_id(conn)

    def test_remove_deletes_the_adjustment_and_keeps_the_audit(self, fresh_client):
        adjustment_id = self._seed(fresh_client)

        response = post_csrf(
            fresh_client, f"/ipoints/adjustments/{adjustment_id}/remove"
        )

        assert response.status_code == 302
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "Balance: 0" in html
        assert "Audit History" in html
        assert "Goodwill" in html
        assert "adjustment" in html.lower()

    def test_remove_without_csrf_is_rejected(self, fresh_client):
        adjustment_id = self._seed(fresh_client)

        response = fresh_client.post(f"/ipoints/adjustments/{adjustment_id}/remove")

        assert response.status_code == 403
        with app_module.connect() as conn:
            assert len(storage.list_ipoint_adjustments(conn)) == 1

    def test_overdraw_refusal_is_surfaced_as_page_feedback(self, fresh_client):
        with app_module.connect() as conn:
            _seed_offset(conn, "ALICE", -2)
        post_csrf(
            fresh_client,
            "/ipoints/adjustments",
            data={"boarder": "Alice", "points": "3", "reason": "Goodwill"},
        )
        with app_module.connect() as conn:
            adjustment_id = _adjustment_id(conn)

        post_csrf(fresh_client, f"/ipoints/adjustments/{adjustment_id}/remove")

        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-error" in html
        assert "below zero" in html.lower()
        with app_module.connect() as conn:
            assert len(storage.list_ipoint_adjustments(conn, "ALICE")) == 1


class TestAdjustmentEditingControls:
    def test_page_renders_labelled_adjustment_edit_and_remove_controls(self, fresh_client):
        post_csrf(
            fresh_client,
            "/ipoints/adjustments",
            data={"boarder": "Alice", "points": "4", "reason": "Goodwill"},
        )
        with app_module.connect() as conn:
            adjustment_id = _adjustment_id(conn)

        html = fresh_client.get("/ipoints").get_data(as_text=True)

        assert f'action="/ipoints/adjustments/{adjustment_id}/edit"' in html
        assert f'action="/ipoints/adjustments/{adjustment_id}/remove"' in html
        assert f'form="ipoint-adjustment-edit-{adjustment_id}"' in html
        assert f'form="ipoint-adjustment-remove-{adjustment_id}"' in html
        assert 'aria-label="Reason for adjustment' in html
        assert 'aria-label="Remove adjustment from' in html


class TestAdjustmentBrowser:
    def test_adjustment_inline_edit_and_remove_are_keyboard_operable(
        self, fresh_client, browser_page
    ):
        post_csrf(
            fresh_client,
            "/ipoints/adjustments",
            data={"boarder": "Alice", "points": "4", "reason": "Goodwill"},
        )
        html = fresh_client.get("/ipoints").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        _stub_form_submit(page)
        page.evaluate(
            """() => {
                window.__adjustmentPayload = null;
                window.__adjustmentAction = null;
                document.querySelector('form[id^="ipoint-adjustment-edit-"]').addEventListener('submit', event => {
                    event.preventDefault();
                    const form = event.target;
                    window.__adjustmentAction = form.getAttribute('action');
                    window.__adjustmentPayload = Object.fromEntries(new FormData(form).entries());
                });
            }"""
        )

        reason = page.locator('input[aria-label^="Reason for adjustment"]')
        reason.fill("corrected")
        page.locator('button[form^="ipoint-adjustment-edit-"]').focus()
        page.keyboard.press("Enter")

        payload = page.evaluate("() => window.__adjustmentPayload")
        assert payload is not None
        assert payload["reason"] == "corrected"
        assert payload["points"] == "4"
        assert page.evaluate("() => window.__adjustmentAction").endswith("/edit")

        page.locator('button[form^="ipoint-adjustment-remove-"]').focus()
        page.keyboard.press("Enter")

        assert page.locator("#confirmModal.show").count() == 1
        message = page.locator("#confirm-modal-message").text_content()
        assert "ALICE" in message.upper()
        page.keyboard.press("Enter")
        assert page.evaluate("() => window.__submitCalled").endswith("/remove")
