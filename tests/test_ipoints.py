"""Coverage for the I-Points ledger's first slice (#176).

Seams: the HTTP routes via the Flask test client (primary) and the new
``ipoints`` lifecycle module over the in-memory connection fixture
(secondary), mirroring the Punishments tests. Storage is exercised through
the lifecycle so no separate storage seam is introduced.
"""

import json
import sqlite3
from datetime import date

import pytest

from helpers import post_csrf, record, seed_punishments

import app as app_module
import ipoints
import punishments
import storage
from ipoints import (
    AdjustmentEdited,
    AdjustmentRejected,
    AdjustmentRemoved,
    AdjustmentSaved,
    ConfiscationEdited,
    ConfiscationRejected,
    ConfiscationReleased,
    ConfiscationRemoved,
    ConfiscationVoided,
    EntryRejected,
    EntrySaved,
    RedemptionConfirmed,
    RedemptionRejected,
    evaluate_month_close,
    log_entry,
    release_due_for,
)
from records import IPointEntry


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


# --- #179 Pending Redemption and Phone Confiscation confirmation -------------


def _materialise(conn, today="2026-09-12"):
    """Runs the month-close materialisation with an injected local date."""
    return ipoints.materialise_pending_redemptions(conn, today)


def _open_row(conn, name="ALICE"):
    return storage.get_open_ipoint_confiscation(conn, name)


class TestMonthCloseEvaluation:
    def _entry(
        self,
        points=14,
        occurred_on="2026-08-10",
        recorded_at="2026-08-10T09:00:00+00:00",
    ):
        return IPointEntry(
            id=1,
            normalized_name="ALICE",
            points=points,
            occurred_on=occurred_on,
            reason="x",
            recorded_at=recorded_at,
        )

    def test_below_the_lowest_tier_yields_no_pending(self, conn):
        seed_entry(conn, points=4)

        assert _materialise(conn) == 0
        assert _open_row(conn) is None

    def test_balance_at_the_lowest_tier_yields_a_pending(self, conn):
        seed_entry(conn, points=5)

        assert _materialise(conn) == 1
        row = _open_row(conn)
        assert row is not None
        assert row.status == "pending"
        assert row.tier == 5
        assert row.points_redeemed == 5

    @pytest.mark.parametrize(
        ("balance", "tier"),
        [(4, None), (5, 5), (9, 5), (10, 10), (14, 10), (15, 15), (20, 15), (99, 15)],
    )
    def test_tier_is_largest_at_or_below_balance_capped_at_15(self, balance, tier):
        pending = evaluate_month_close(
            [self._entry(points=balance)], [], [], "2026-09-12"
        )

        assert (pending.tier if pending else None) == tier

    def test_pending_does_not_debit_the_balance(self, conn):
        seed_entry(conn, points=14)

        _materialise(conn)

        alice = next(
            s for s in ipoints.boarder_balances(conn) if s.normalized_name == "ALICE"
        )
        assert alice.balance == 14
        assert alice.pending is not None
        assert alice.pending.points_redeemed == 10

    def test_materialisation_is_idempotent(self, conn):
        seed_entry(conn, points=14)

        assert _materialise(conn) == 1
        assert _materialise(conn) == 0
        assert len(storage.list_ipoint_confiscations(conn, "ALICE")) == 1

    def test_locked_tier_does_not_escalate_as_points_accrue(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)
        seed_entry(
            conn,
            points=8,
            occurred_on="2026-09-02",
            recorded_at="2026-09-02T09:00:00+00:00",
        )

        assert _materialise(conn, today="2026-10-05") == 0
        row = _open_row(conn)
        assert row is not None
        assert row.tier == 10

    def test_no_second_pending_while_one_is_open(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)
        seed_entry(
            conn,
            points=9,
            occurred_on="2026-09-02",
            recorded_at="2026-09-02T09:00:00+00:00",
        )

        assert ipoints.pending_redemptions(conn, "2026-10-05") == []

    def test_trigger_month_is_the_latest_closed_month(self, conn):
        seed_entry(conn, points=5)

        assert (
            ipoints.pending_redemptions(conn, "2026-09-01")[0][1].trigger_month
            == "2026-08"
        )
        assert (
            ipoints.pending_redemptions(conn, "2026-09-30")[0][1].trigger_month
            == "2026-08"
        )
        assert (
            ipoints.pending_redemptions(conn, "2026-10-01")[0][1].trigger_month
            == "2026-09"
        )

    def test_backdated_entry_never_reopens_a_closed_month(self, conn):
        seed_entry(conn, points=3)
        assert _materialise(conn, today="2026-09-12") == 0

        # An August incident entered after August closed.
        seed_entry(
            conn,
            points=5,
            occurred_on="2026-08-15",
            recorded_at="2026-09-05T09:00:00+00:00",
        )

        assert ipoints.pending_redemptions(conn, "2026-09-12") == []

    def test_backdated_entry_is_picked_up_at_the_next_close(self, conn):
        seed_entry(conn, points=3)
        seed_entry(
            conn,
            points=5,
            occurred_on="2026-08-15",
            recorded_at="2026-09-05T09:00:00+00:00",
        )

        pending = ipoints.pending_redemptions(conn, "2026-10-05")

        assert pending[0][1].trigger_month == "2026-09"
        assert pending[0][1].tier == 5

    def test_remainder_carries_forward(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)
        row = _open_row(conn)
        assert isinstance(
            ipoints.confirm_redemption(
                conn,
                row.id,
                today="2026-09-15",
                recorded_at="2026-09-15T09:00:00+00:00",
            ),
            RedemptionConfirmed,
        )
        # Release it (as #180 will offer) so the next close can be evaluated.
        conn.execute(
            "UPDATE confiscations SET status = 'released', released_at = ? WHERE id = ?",
            ("2026-09-20T09:00:00+00:00", row.id),
        )
        conn.commit()
        seed_entry(
            conn,
            points=6,
            occurred_on="2026-09-10",
            recorded_at="2026-09-10T09:00:00+00:00",
        )

        pending = ipoints.pending_redemptions(conn, "2026-10-05")

        assert pending[0][1].trigger_month == "2026-09"
        assert pending[0][1].tier == 10

    def test_a_voided_month_is_not_recreated(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)
        row = _open_row(conn)
        ipoints.void_confiscation(
            conn,
            row.id,
            reason="logged in error",
            recorded_at="2026-09-13T09:00:00+00:00",
        )

        assert ipoints.pending_redemptions(conn, "2026-09-20") == []


class TestConfiscationIndex:
    def test_one_open_confiscation_per_boarder_is_enforced(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)

        with pytest.raises(sqlite3.IntegrityError):
            with conn:
                storage.stage_ipoint_confiscation(
                    conn,
                    "ALICE",
                    "2026-09",
                    5,
                    5,
                    "pending",
                    "2026-10-01T09:00:00+00:00",
                )

        assert len(storage.list_ipoint_confiscations(conn, "ALICE")) == 1


class TestConfirmRedemption:
    def test_confirm_creates_an_active_confiscation_and_debits(self, conn):
        storage.replace_boarders(
            conn,
            [storage.Boarder(normalized_name="ALICE", display_name="Alice", bed="601A")],
        )
        seed_entry(conn, points=14)
        _materialise(conn)
        row = _open_row(conn)

        outcome = ipoints.confirm_redemption(
            conn,
            row.id,
            today="2026-09-15",
            recorded_at="2026-09-15T10:00:00+00:00",
        )

        assert isinstance(outcome, RedemptionConfirmed)
        stored = storage.get_ipoint_confiscation(conn, row.id)
        assert stored.status == "active"
        assert stored.display_name == "Alice"
        assert stored.bed == "601A"
        assert stored.confirmed_at == "2026-09-15T10:00:00+00:00"
        assert stored.release_due == "2026-09-22"
        assert stored.points_redeemed == 10
        alice = next(
            s for s in ipoints.boarder_balances(conn) if s.normalized_name == "ALICE"
        )
        assert alice.balance == 4

    def test_confirm_is_refused_when_it_would_go_below_zero(self, conn):
        seed_entry(conn, points=10)
        _materialise(conn)
        row = _open_row(conn)
        ipoints.add_adjustment(
            conn,
            "ALICE",
            -6,
            "offset",
            recorded_at="2026-09-01T09:00:00+00:00",
        )

        outcome = ipoints.confirm_redemption(conn, row.id, today="2026-09-15")

        assert isinstance(outcome, RedemptionRejected)
        assert "below zero" in outcome.reason
        assert storage.get_ipoint_confiscation(conn, row.id).status == "pending"

    def test_confirm_rejects_a_non_pending_row(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)
        row = _open_row(conn)
        ipoints.confirm_redemption(conn, row.id, today="2026-09-15")

        again = ipoints.confirm_redemption(conn, row.id, today="2026-09-16")

        assert isinstance(again, RedemptionRejected)

    def test_confirm_writes_an_audit_row(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)
        row = _open_row(conn)
        ipoints.confirm_redemption(
            conn,
            row.id,
            today="2026-09-15",
            recorded_at="2026-09-15T10:00:00+00:00",
        )

        audits = [
            audit
            for audit in storage.list_ipoint_audit(conn, "ALICE")
            if audit.entity_type == "confiscation"
        ]
        assert [audit.action for audit in audits] == ["confirmed", "created"]


class TestReleaseDue:
    def test_each_tier_has_its_fixed_period(self):
        assert release_due_for(5, date(2026, 9, 15)).isoformat() == "2026-09-16"
        assert release_due_for(10, date(2026, 9, 15)).isoformat() == "2026-09-22"
        assert release_due_for(15, date(2026, 9, 15)).isoformat() == "2026-10-15"

    def test_one_month_clamps_to_the_shorter_month(self):
        assert release_due_for(15, date(2026, 1, 31)).isoformat() == "2026-02-28"


class TestVoidRedemption:
    def test_void_cancels_without_debiting(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)
        row = _open_row(conn)

        outcome = ipoints.void_confiscation(
            conn,
            row.id,
            reason="logged in error",
            recorded_at="2026-09-13T09:00:00+00:00",
        )

        assert isinstance(outcome, ConfiscationVoided)
        assert outcome.was_pending is True
        stored = storage.get_ipoint_confiscation(conn, row.id)
        assert stored.status == "voided"
        assert stored.void_reason == "logged in error"
        assert stored.voided_at == "2026-09-13T09:00:00+00:00"
        alice = next(
            s for s in ipoints.boarder_balances(conn) if s.normalized_name == "ALICE"
        )
        assert alice.balance == 14

    def test_void_rejects_a_settled_row(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)
        row = _open_row(conn)
        ipoints.confirm_redemption(conn, row.id, today="2026-09-15")
        ipoints.release_confiscation(
            conn, row.id, released_at="2026-09-20T09:00:00+00:00"
        )

        outcome = ipoints.void_confiscation(conn, row.id, reason="too late")

        assert isinstance(outcome, ConfiscationRejected)


class TestRedemptionRoutes:
    def _seed_pending(self, fresh_client, points=14):
        with app_module.connect() as conn:
            outcome = ipoints.log_entry(
                conn,
                "ALICE",
                points,
                "2020-08-01",
                "Repeated disruption",
                recorded_at="2020-08-01T09:00:00+00:00",
            )
        assert isinstance(outcome, EntrySaved)
        fresh_client.get("/ipoints")  # materialises the pending
        return fresh_client.get("/ipoints").get_data(as_text=True)

    def _open_id(self):
        with app_module.connect() as conn:
            row = storage.get_open_ipoint_confiscation(conn, "ALICE")
        assert row is not None
        return row.id

    def test_get_materialises_a_pending_redemption(self, fresh_client):
        html = self._seed_pending(fresh_client)

        assert "Pending redemption" in html
        assert "Balance: 14" in html

    def test_confirm_route_activates_the_confiscation_and_debits(self, fresh_client):
        self._seed_pending(fresh_client)
        row_id = self._open_id()

        response = post_csrf(
            fresh_client, f"/ipoints/confiscations/{row_id}/confirm"
        )

        assert response.status_code == 302
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-success" in html
        assert "Phone Confiscation" in html
        assert "Balance: 4" in html

    def test_void_route_cancels_the_pending(self, fresh_client):
        self._seed_pending(fresh_client)
        row_id = self._open_id()

        response = post_csrf(
            fresh_client,
            f"/ipoints/confiscations/{row_id}/void",
            data={"void_reason": "logged in error"},
        )

        assert response.status_code == 302
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-success" in html
        assert "Balance: 14" in html
        with app_module.connect() as conn:
            stored = storage.get_ipoint_confiscation(conn, row_id)
        assert stored.status == "voided"
        assert stored.void_reason == "logged in error"

    def test_confirm_route_requires_csrf(self, fresh_client):
        self._seed_pending(fresh_client)
        row_id = self._open_id()

        response = fresh_client.post(f"/ipoints/confiscations/{row_id}/confirm")

        assert response.status_code == 403

    def test_confirm_route_surfaces_an_overdraw_refusal(self, fresh_client):
        self._seed_pending(fresh_client, points=10)
        row_id = self._open_id()
        with app_module.connect() as conn:
            ipoints.add_adjustment(
                conn,
                "ALICE",
                -6,
                "offset",
                recorded_at="2026-09-01T09:00:00+00:00",
            )

        response = post_csrf(
            fresh_client, f"/ipoints/confiscations/{row_id}/confirm"
        )

        assert response.status_code == 302
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-error" in html
        assert "below zero" in html
        with app_module.connect() as conn:
            assert storage.get_ipoint_confiscation(conn, row_id).status == "pending"


class TestRedemptionBrowser:
    def test_pending_redemption_confirm_and_void_are_keyboard_operable(
        self, fresh_client, browser_page
    ):
        with app_module.connect() as conn:
            ipoints.log_entry(
                conn,
                "ALICE",
                14,
                "2020-08-01",
                "Repeated disruption",
                recorded_at="2020-08-01T09:00:00+00:00",
            )
        fresh_client.get("/ipoints")  # materialises the pending
        html = fresh_client.get("/ipoints").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        _stub_form_submit(page)
        page.evaluate(
            """() => {
                window.__voidAction = null;
                document.querySelector('form.ipoint-void-form').addEventListener('submit', event => {
                    event.preventDefault();
                    window.__voidAction = event.target.getAttribute('action');
                });
            }"""
        )

        confirm = page.locator('button[aria-label^="Confirm redemption"]')
        assert confirm.count() == 1
        confirm.focus()
        page.keyboard.press("Enter")

        assert page.locator("#confirmModal.show").count() == 1
        message = page.locator("#confirm-modal-message").text_content()
        assert "ALICE" in message.upper()
        page.keyboard.press("Enter")
        assert page.evaluate("() => window.__submitCalled").endswith("/confirm")

        void = page.locator('button[aria-label^="Void redemption"]')
        assert void.count() == 1
        void.focus()
        page.keyboard.press("Enter")
        assert page.evaluate("() => window.__voidAction").endswith("/void")


# --- #180 Phone Confiscation lifecycle and Stacked flag ---------------------


def _seed_active(
    conn,
    name="ALICE",
    points=14,
    confirmed_on="2026-09-15",
    recorded_at="2026-09-15T10:00:00+00:00",
):
    """Logs points and confirms the open pending Redemption into active."""
    seed_entry(conn, name=name, points=points)
    _materialise(conn)
    row = _open_row(conn, name)
    outcome = ipoints.confirm_redemption(
        conn, row.id, today=confirmed_on, recorded_at=recorded_at
    )
    assert isinstance(outcome, RedemptionConfirmed)
    return storage.get_ipoint_confiscation(conn, row.id)


def _balance(conn, name="ALICE"):
    return next(
        s.balance
        for s in ipoints.boarder_balances(conn)
        if s.normalized_name == name
    )


def _hold_phone(conn, name="ALICE"):
    """Drives a lateness Punishment for ``name`` to ``phone_held``."""
    rows = seed_punishments(
        conn,
        boarders=[record(name, "101", 2, 5, 7)],
        month="2026-03",
        deadline="2026-03-10",
    )
    punishment = rows[0]
    punishments.transition(
        conn, punishment.id, "overdue", timestamp="2026-03-11T09:00:00+00:00"
    )
    punishments.transition(
        conn, punishment.id, "phone_held", timestamp="2026-03-12T09:00:00+00:00"
    )
    return storage.get_punishment(conn, punishment.id)


def _phone_is_held(conn, name="ALICE"):
    """The two-gate return condition: held while either gate remains open."""
    active = any(
        row.status == ipoints.STATUS_ACTIVE
        for row in storage.list_ipoint_confiscations(conn, name)
    )
    lateness = any(
        row.status == "phone_held" and row.normalized_name == name
        for row in storage.list_punishments(conn, statuses=("phone_held",))
    )
    return active or lateness


class TestReleaseConfiscation:
    def test_release_records_when_the_phone_returned(self, conn):
        active = _seed_active(conn)

        outcome = ipoints.release_confiscation(
            conn, active.id, released_at="2026-09-18T09:00:00+00:00"
        )

        assert isinstance(outcome, ConfiscationReleased)
        stored = storage.get_ipoint_confiscation(conn, active.id)
        assert stored.status == "released"
        assert stored.released_at == "2026-09-18T09:00:00+00:00"

    def test_release_is_allowed_before_the_due_date(self, conn):
        active = _seed_active(conn, confirmed_on="2026-09-15")  # due 2026-09-22

        outcome = ipoints.release_confiscation(
            conn, active.id, released_at="2026-09-16T09:00:00+00:00"
        )

        assert isinstance(outcome, ConfiscationReleased)
        assert storage.get_ipoint_confiscation(conn, active.id).status == "released"

    def test_release_keeps_the_points_debited(self, conn):
        active = _seed_active(conn)
        assert _balance(conn) == 4

        ipoints.release_confiscation(conn, active.id)

        assert _balance(conn) == 4

    def test_release_writes_an_audit_row(self, conn):
        active = _seed_active(conn)

        ipoints.release_confiscation(conn, active.id)

        audits = [
            audit
            for audit in storage.list_ipoint_audit(conn, "ALICE")
            if audit.entity_type == "confiscation"
        ]
        assert [audit.action for audit in audits] == [
            "released",
            "confirmed",
            "created",
        ]

    def test_release_rejects_a_pending_row(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)
        row = _open_row(conn)

        outcome = ipoints.release_confiscation(conn, row.id)

        assert isinstance(outcome, ConfiscationRejected)
        assert storage.get_ipoint_confiscation(conn, row.id).status == "pending"

    def test_release_rejects_an_already_released_row(self, conn):
        active = _seed_active(conn)
        ipoints.release_confiscation(conn, active.id)

        outcome = ipoints.release_confiscation(conn, active.id)

        assert isinstance(outcome, ConfiscationRejected)


class TestConfiscationDueFlag:
    def test_due_on_the_release_date(self, conn):
        _seed_active(conn, confirmed_on="2026-09-15")  # due 2026-09-22

        on_due = ipoints.confiscation_list(
            conn, statuses=("active",), today="2026-09-22"
        )
        before = ipoints.confiscation_list(
            conn, statuses=("active",), today="2026-09-21"
        )

        assert on_due[0].is_due is True
        assert before[0].is_due is False

    def test_due_after_the_release_date(self, conn):
        _seed_active(conn, confirmed_on="2026-09-15")

        rows = ipoints.confiscation_list(
            conn, statuses=("active",), today="2026-09-30"
        )

        assert rows[0].is_due is True

    def test_released_rows_are_never_due(self, conn):
        active = _seed_active(conn, confirmed_on="2026-09-15")
        ipoints.release_confiscation(conn, active.id)

        rows = ipoints.confiscation_list(
            conn, statuses=("released",), today="2026-10-30"
        )

        assert rows[0].is_due is False

    def test_pending_rows_are_never_due(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)

        rows = ipoints.confiscation_list(
            conn, statuses=("pending",), today="2026-10-30"
        )

        assert rows[0].is_due is False


class TestStackedFlag:
    def test_active_confiscation_with_a_phone_hold_is_stacked(self, conn):
        _seed_active(conn)
        _hold_phone(conn)

        rows = ipoints.confiscation_list(conn, statuses=("active",))

        assert rows[0].stacked is True

    def test_phone_hold_without_a_confiscation_flags_nothing(self, conn):
        _hold_phone(conn)

        assert ipoints.confiscation_list(conn, statuses=("active",)) == []

    def test_submitting_the_punishment_clears_stacked(self, conn):
        _seed_active(conn)
        punishment = _hold_phone(conn)
        punishments.transition(
            conn, punishment.id, "submitted", timestamp="2026-03-13T09:00:00+00:00"
        )

        rows = ipoints.confiscation_list(conn, statuses=("active",))

        assert rows[0].stacked is False

    def test_releasing_the_confiscation_clears_stacked(self, conn):
        active = _seed_active(conn)
        _hold_phone(conn)
        ipoints.release_confiscation(conn, active.id)

        rows = ipoints.confiscation_list(conn, statuses=("released",))

        assert rows[0].stacked is False

    def test_assigned_punishment_is_not_a_phone_hold(self, conn):
        _seed_active(conn)
        seed_punishments(
            conn,
            boarders=[record("ALICE", "101", 2, 5, 7)],
            month="2026-03",
            deadline="2026-03-10",
        )

        rows = ipoints.confiscation_list(conn, statuses=("active",))

        assert rows[0].stacked is False

    def test_two_gate_return_waits_for_both_to_clear(self, conn):
        _seed_active(conn)
        punishment = _hold_phone(conn)
        assert _phone_is_held(conn) is True

        punishments.transition(
            conn, punishment.id, "submitted", timestamp="2026-03-13T09:00:00+00:00"
        )
        assert _phone_is_held(conn) is True  # the Confiscation still holds it

        active = storage.get_open_ipoint_confiscation(conn, "ALICE")
        ipoints.release_confiscation(conn, active.id)
        assert _phone_is_held(conn) is False

    def test_due_confiscation_still_waits_for_the_lateness_gate(self, conn):
        _seed_active(conn, confirmed_on="2026-09-15")
        _hold_phone(conn)

        rows = ipoints.confiscation_list(
            conn, statuses=("active",), today="2026-09-30"
        )

        assert rows[0].is_due is True
        assert rows[0].stacked is True
        assert _phone_is_held(conn) is True


class TestVoidActiveConfiscation:
    def test_void_active_requires_a_reason(self, conn):
        active = _seed_active(conn)

        outcome = ipoints.void_confiscation(conn, active.id)

        assert isinstance(outcome, ConfiscationRejected)
        assert storage.get_ipoint_confiscation(conn, active.id).status == "active"

    def test_void_active_returns_the_points(self, conn):
        active = _seed_active(conn)
        assert _balance(conn) == 4

        outcome = ipoints.void_confiscation(
            conn,
            active.id,
            reason="recorded in error",
            recorded_at="2026-09-16T09:00:00+00:00",
        )

        assert isinstance(outcome, ConfiscationVoided)
        assert outcome.was_pending is False
        stored = storage.get_ipoint_confiscation(conn, active.id)
        assert stored.status == "voided"
        assert stored.void_reason == "recorded in error"
        assert _balance(conn) == 14

    def test_void_pending_allows_a_blank_reason(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)
        row = _open_row(conn)

        outcome = ipoints.void_confiscation(conn, row.id)

        assert isinstance(outcome, ConfiscationVoided)
        assert outcome.was_pending is True


class TestEditConfiscation:
    def test_edit_lowers_the_tier_and_recomputes_the_period(self, conn):
        active = _seed_active(conn, points=20, confirmed_on="2026-09-15")
        assert active.tier == 15
        assert active.release_due == "2026-10-15"

        outcome = ipoints.edit_confiscation(conn, active.id, 5)

        assert isinstance(outcome, ConfiscationEdited)
        stored = storage.get_ipoint_confiscation(conn, active.id)
        assert stored.tier == 5
        assert stored.points_redeemed == 5
        assert stored.release_due == "2026-09-16"
        assert _balance(conn) == 15  # 20 - 5

    def test_edit_recomputes_from_the_confirmation_date(self, conn):
        active = _seed_active(conn, points=20, confirmed_on="2026-09-15")

        ipoints.edit_confiscation(conn, active.id, 10)

        stored = storage.get_ipoint_confiscation(conn, active.id)
        assert stored.release_due == "2026-09-22"
        assert stored.points_redeemed == 10

    def test_edit_up_is_refused_when_it_would_go_below_zero(self, conn):
        active = _seed_active(conn)  # 14 - 10 = 4
        assert _balance(conn) == 4

        outcome = ipoints.edit_confiscation(conn, active.id, 15)

        assert isinstance(outcome, ConfiscationRejected)
        assert "below zero" in outcome.reason
        assert storage.get_ipoint_confiscation(conn, active.id).tier == 10

    def test_edit_keeps_the_frozen_display_name_and_bed(self, conn):
        storage.replace_boarders(
            conn,
            [
                storage.Boarder(
                    normalized_name="ALICE", display_name="Alice", bed="601A"
                )
            ],
        )
        active = _seed_active(conn, points=20)

        ipoints.edit_confiscation(conn, active.id, 5)

        stored = storage.get_ipoint_confiscation(conn, active.id)
        assert stored.display_name == "Alice"
        assert stored.bed == "601A"

    def test_edit_rejects_a_pending_row(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)
        row = _open_row(conn)

        outcome = ipoints.edit_confiscation(conn, row.id, 5)

        assert isinstance(outcome, ConfiscationRejected)
        assert storage.get_ipoint_confiscation(conn, row.id).tier == 10

    def test_edit_rejects_an_unknown_tier(self, conn):
        active = _seed_active(conn)

        outcome = ipoints.edit_confiscation(conn, active.id, 7)

        assert isinstance(outcome, ConfiscationRejected)

    def test_edit_writes_an_audit_row(self, conn):
        active = _seed_active(conn, points=20)

        ipoints.edit_confiscation(conn, active.id, 5)

        audits = [
            audit
            for audit in storage.list_ipoint_audit(conn, "ALICE")
            if audit.entity_type == "confiscation"
        ]
        assert [audit.action for audit in audits] == [
            "edited",
            "confirmed",
            "created",
        ]


class TestRemoveConfiscation:
    def test_remove_active_returns_the_points(self, conn):
        active = _seed_active(conn)
        assert _balance(conn) == 4

        outcome = ipoints.remove_confiscation(conn, active.id)

        assert isinstance(outcome, ConfiscationRemoved)
        assert storage.get_ipoint_confiscation(conn, active.id) is None
        assert _balance(conn) == 14

    def test_remove_pending_leaves_the_balance_untouched(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)
        row = _open_row(conn)

        outcome = ipoints.remove_confiscation(conn, row.id)

        assert isinstance(outcome, ConfiscationRemoved)
        assert storage.get_ipoint_confiscation(conn, row.id) is None
        assert _balance(conn) == 14

    def test_remove_released_returns_the_points(self, conn):
        active = _seed_active(conn)
        ipoints.release_confiscation(conn, active.id)

        outcome = ipoints.remove_confiscation(conn, active.id)

        assert isinstance(outcome, ConfiscationRemoved)
        assert _balance(conn) == 14

    def test_removed_pending_month_is_not_recreated(self, conn):
        seed_entry(conn, points=14)
        _materialise(conn)
        row = _open_row(conn)
        ipoints.remove_confiscation(conn, row.id)

        assert ipoints.pending_redemptions(conn, "2026-09-20") == []

    def test_removed_active_month_is_not_recreated(self, conn):
        active = _seed_active(conn)
        ipoints.remove_confiscation(conn, active.id)

        assert ipoints.pending_redemptions(conn, "2026-09-20") == []

    def test_remove_writes_an_audit_row(self, conn):
        active = _seed_active(conn)

        ipoints.remove_confiscation(conn, active.id)

        audits = [
            audit
            for audit in storage.list_ipoint_audit(conn, "ALICE")
            if audit.entity_type == "confiscation"
        ]
        assert audits[0].action == "removed"
        assert audits[0].after_state is None

    def test_remove_rejects_a_missing_row(self, conn):
        outcome = ipoints.remove_confiscation(conn, 999)

        assert isinstance(outcome, ConfiscationRejected)


class TestRemovedBoarderLifecycle:
    def test_lifecycle_actions_are_allowed_for_a_removed_boarder(self, conn):
        storage.replace_boarders(
            conn,
            [
                storage.Boarder(
                    normalized_name="ALICE", display_name="Alice", bed="601A"
                )
            ],
        )
        active = _seed_active(conn, points=20)
        storage.replace_boarders(conn, [])  # drop ALICE from the Master List

        assert isinstance(
            ipoints.edit_confiscation(conn, active.id, 5), ConfiscationEdited
        )
        assert isinstance(
            ipoints.release_confiscation(conn, active.id), ConfiscationReleased
        )


class TestConfiscationStatusFilter:
    def test_filter_returns_only_the_requested_status(self, conn):
        alice = _seed_active(conn, name="ALICE", confirmed_on="2026-09-15")
        ipoints.release_confiscation(conn, alice.id)
        bob = _seed_active(conn, name="BOB", confirmed_on="2026-09-16")

        active = ipoints.confiscation_list(conn, statuses=("active",))
        released = ipoints.confiscation_list(conn, statuses=("released",))

        assert [row.id for row in active] == [bob.id]
        assert [row.id for row in released] == [alice.id]

    def test_all_statuses_excludes_pending(self, conn):
        active = _seed_active(conn)
        ipoints.release_confiscation(conn, active.id)
        seed_entry(conn, name="BOB", points=5)
        _materialise(conn)

        rows = ipoints.confiscation_list(
            conn, statuses=("active", "released", "voided")
        )

        assert [row.status for row in rows] == ["released"]


class TestConfiscationLifecycleRoutes:
    def _seed_active(self, fresh_client, points=14):
        with app_module.connect() as conn:
            ipoints.log_entry(
                conn,
                "ALICE",
                points,
                "2020-08-01",
                "Repeated disruption",
                recorded_at="2020-08-01T09:00:00+00:00",
            )
        fresh_client.get("/ipoints")  # materialise the pending
        with app_module.connect() as conn:
            row = storage.get_open_ipoint_confiscation(conn, "ALICE")
        post_csrf(fresh_client, f"/ipoints/confiscations/{row.id}/confirm")
        with app_module.connect() as conn:
            stored = storage.get_ipoint_confiscation(conn, row.id)
        assert stored.status == "active"
        return row.id

    def test_release_route_marks_the_phone_returned(self, fresh_client):
        row_id = self._seed_active(fresh_client)

        response = post_csrf(
            fresh_client, f"/ipoints/confiscations/{row_id}/release"
        )

        assert response.status_code == 302
        html = fresh_client.get(
            "/ipoints?confiscation_status=released"
        ).get_data(as_text=True)
        assert "banner-success" in html
        with app_module.connect() as conn:
            assert storage.get_ipoint_confiscation(conn, row_id).status == "released"

    def test_release_route_requires_csrf(self, fresh_client):
        row_id = self._seed_active(fresh_client)

        response = fresh_client.post(
            f"/ipoints/confiscations/{row_id}/release"
        )

        assert response.status_code == 403

    def test_void_active_route_requires_a_reason(self, fresh_client):
        row_id = self._seed_active(fresh_client)

        response = post_csrf(
            fresh_client, f"/ipoints/confiscations/{row_id}/void"
        )

        assert response.status_code == 302
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-error" in html
        assert "reason" in html.lower()
        with app_module.connect() as conn:
            assert storage.get_ipoint_confiscation(conn, row_id).status == "active"

    def test_void_active_route_returns_the_points(self, fresh_client):
        row_id = self._seed_active(fresh_client, points=14)

        response = post_csrf(
            fresh_client,
            f"/ipoints/confiscations/{row_id}/void",
            data={"void_reason": "recorded in error"},
        )

        assert response.status_code == 302
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-success" in html
        assert "Balance: 14" in html
        with app_module.connect() as conn:
            assert storage.get_ipoint_confiscation(conn, row_id).status == "voided"

    def test_edit_route_lowers_the_tier(self, fresh_client):
        row_id = self._seed_active(fresh_client, points=20)

        response = post_csrf(
            fresh_client,
            f"/ipoints/confiscations/{row_id}/edit",
            data={"tier": "5"},
        )

        assert response.status_code == 302
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-success" in html
        with app_module.connect() as conn:
            stored = storage.get_ipoint_confiscation(conn, row_id)
        assert (stored.tier, stored.points_redeemed) == (5, 5)

    def test_edit_route_surfaces_an_overdraw_refusal(self, fresh_client):
        row_id = self._seed_active(fresh_client, points=14)

        response = post_csrf(
            fresh_client,
            f"/ipoints/confiscations/{row_id}/edit",
            data={"tier": "15"},
        )

        assert response.status_code == 302
        html = fresh_client.get("/ipoints").get_data(as_text=True)
        assert "banner-error" in html
        assert "below zero" in html.lower()
        with app_module.connect() as conn:
            assert storage.get_ipoint_confiscation(conn, row_id).tier == 10

    def test_edit_route_requires_csrf(self, fresh_client):
        row_id = self._seed_active(fresh_client)

        response = fresh_client.post(
            f"/ipoints/confiscations/{row_id}/edit", data={"tier": "5"}
        )

        assert response.status_code == 403

    def test_remove_route_removes_the_row(self, fresh_client):
        row_id = self._seed_active(fresh_client)

        response = post_csrf(
            fresh_client, f"/ipoints/confiscations/{row_id}/remove"
        )

        assert response.status_code == 302
        with app_module.connect() as conn:
            assert storage.get_ipoint_confiscation(conn, row_id) is None

    def test_removed_confiscation_is_not_re_materialised(self, fresh_client):
        row_id = self._seed_active(fresh_client)
        post_csrf(fresh_client, f"/ipoints/confiscations/{row_id}/remove")

        fresh_client.get("/ipoints")

        with app_module.connect() as conn:
            assert storage.get_open_ipoint_confiscation(conn, "ALICE") is None

    def test_status_filter_defaults_to_active(self, fresh_client):
        row_id = self._seed_active(fresh_client)
        post_csrf(fresh_client, f"/ipoints/confiscations/{row_id}/release")

        default_html = fresh_client.get("/ipoints").get_data(as_text=True)
        released_html = fresh_client.get(
            "/ipoints?confiscation_status=released"
        ).get_data(as_text=True)

        assert "No active Phone Confiscations." in default_html
        assert "Returned" in released_html

    def test_invalid_status_filter_falls_back_to_active(self, fresh_client):
        self._seed_active(fresh_client)

        html = fresh_client.get(
            "/ipoints?confiscation_status=bogus"
        ).get_data(as_text=True)

        assert "No active Phone Confiscations." not in html
        assert "Due back" in html

    def test_stacked_badge_renders_with_a_phone_hold(self, fresh_client):
        self._seed_active(fresh_client)
        with app_module.connect() as conn:
            _hold_phone(conn)

        html = fresh_client.get("/ipoints").get_data(as_text=True)

        assert "Stacked" in html

    def test_stacked_badge_absent_without_a_phone_hold(self, fresh_client):
        self._seed_active(fresh_client)

        html = fresh_client.get("/ipoints").get_data(as_text=True)

        assert "Stacked" not in html

    def test_due_flag_renders_for_a_past_release_due(self, fresh_client):
        with app_module.connect() as conn:
            ipoints.log_entry(
                conn,
                "ALICE",
                14,
                "2020-08-01",
                "Repeated disruption",
                recorded_at="2020-08-01T09:00:00+00:00",
            )
        fresh_client.get("/ipoints")  # materialise the pending
        with app_module.connect() as conn:
            row = storage.get_open_ipoint_confiscation(conn, "ALICE")
            ipoints.confirm_redemption(conn, row.id, today="2020-09-15")

        html = fresh_client.get("/ipoints").get_data(as_text=True)

        assert "Due for release" in html


class TestConfiscationLifecycleBrowser:
    def test_release_control_is_keyboard_operable_with_a_confirm(
        self, fresh_client, browser_page
    ):
        with app_module.connect() as conn:
            ipoints.log_entry(
                conn,
                "ALICE",
                14,
                "2020-08-01",
                "Repeated disruption",
                recorded_at="2020-08-01T09:00:00+00:00",
            )
        fresh_client.get("/ipoints")  # materialise the pending
        with app_module.connect() as conn:
            row = storage.get_open_ipoint_confiscation(conn, "ALICE")
        post_csrf(fresh_client, f"/ipoints/confiscations/{row.id}/confirm")
        html = fresh_client.get("/ipoints").get_data(as_text=True)

        page = browser_page
        page.set_content(html)
        _stub_form_submit(page)

        release = page.locator('button[aria-label^="Release the Confiscation"]')
        assert release.count() == 1
        release.focus()
        page.keyboard.press("Enter")

        assert page.locator("#confirmModal.show").count() == 1
        message = page.locator("#confirm-modal-message").text_content()
        assert "ALICE" in message.upper()
        page.keyboard.press("Enter")
        assert page.evaluate("() => window.__submitCalled").endswith("/release")
