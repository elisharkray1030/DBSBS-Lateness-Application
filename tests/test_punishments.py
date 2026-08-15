import pytest
from datetime import datetime
from helpers import record

import storage
from punishments import (
    AssignmentRejected,
    AssignmentSaved,
    TransitionRejected,
    assign_batch,
    list_consequences,
    transition,
)


def assign_month(conn, boarders=None, exemptions=(), deadline="2026-04-10"):
    return assign_batch(
        conn,
        month="2026-03",
        boarders=boarders or [record("ALICE", "101", 2, 5, 7), record("BOB", "102", 1, 19, 20)],
        exemptions=set(exemptions),
        deadline=deadline,
        assigned_at="2026-04-01T09:00:00+00:00",
    )


class TestAssignBatch:
    def test_assigns_every_boarder_with_points(self, conn):
        outcome = assign_month(conn)

        assert isinstance(outcome, AssignmentSaved)
        assert outcome.count == 2
        rows = storage.list_punishments(conn, statuses=("assigned",))
        assert {r.normalized_name for r in rows} == {"ALICE", "BOB"}
        assert all(r.status == "assigned" for r in rows)
        assert all(r.deadline == "2026-04-10" for r in rows)

    def test_exempted_boarder_not_assigned(self, conn):
        outcome = assign_month(conn, exemptions=["BOB"])

        assert isinstance(outcome, AssignmentSaved)
        assert outcome.count == 1
        rows = storage.list_punishments(conn, statuses=("assigned",))
        assert {r.normalized_name for r in rows} == {"ALICE"}

    def test_boarder_with_zero_points_not_assigned(self, conn):
        outcome = assign_month(
            conn,
            boarders=[
                record("ALICE", "101", 2, 5, 7),
                record("CAROL", "103", 0, 0, 0),
            ],
        )

        assert isinstance(outcome, AssignmentSaved)
        assert outcome.count == 1
        rows = storage.list_punishments(conn, statuses=("assigned",))
        assert {r.normalized_name for r in rows} == {"ALICE"}

    def test_snapshot_freezes_points_bed_and_name(self, conn):
        assign_month(conn)

        row = storage.list_punishments(conn, statuses=("assigned",))[0]
        assert row.display_name == "Alice"
        assert row.bed == "101"
        assert row.points_owed == 7

    def test_no_boarders_with_points_is_rejected(self, conn):
        outcome = assign_month(conn, boarders=[record("CAROL", "103", 0, 0, 0)])

        assert isinstance(outcome, AssignmentRejected)
        assert "points" in outcome.reason.lower()
        assert storage.list_punishments(conn) == []

    def test_message_reports_how_many_assigned_and_to_whom(self, conn):
        outcome = assign_month(conn)

        assert isinstance(outcome, AssignmentSaved)
        assert "2" in outcome.message
        assert "ALICE" in outcome.message
        assert "BOB" in outcome.message

    def test_reimport_of_same_month_leaves_assignments_untouched(self, conn):
        assign_month(conn)

        assign_batch(
            conn,
            month="2026-03",
            boarders=[record("ALICE", "101", 5, 30, 35)],
            exemptions=set(),
            deadline="2026-04-20",
            assigned_at="2026-04-10T09:00:00+00:00",
        )

        rows = storage.list_punishments(conn, statuses=("assigned",))
        assert len(rows) == 2
        alice = next(r for r in rows if r.normalized_name == "ALICE")
        assert alice.points_owed == 7
        assert alice.deadline == "2026-04-10"

    def test_reimport_after_submitted_leaves_punishment_untouched(self, conn):
        outcome = assign_month(conn, boarders=[record("ALICE", "101", 2, 5, 7)])
        assert isinstance(outcome, AssignmentSaved)
        row = storage.list_punishments(conn, statuses=("assigned",))[0]
        transition(conn, row.id, "submitted", timestamp="2026-04-09T09:00:00+00:00")

        outcome = assign_batch(
            conn,
            month="2026-03",
            boarders=[
                record("ALICE", "101", 5, 30, 35),
                record("BOB", "102", 1, 19, 20),
            ],
            exemptions=set(),
            deadline="2026-04-20",
            assigned_at="2026-04-10T09:00:00+00:00",
        )

        assert isinstance(outcome, AssignmentSaved)
        assert outcome.count == 1
        assert outcome.names == ["BOB"]
        rows = storage.list_punishments(conn)
        alice = next(r for r in rows if r.normalized_name == "ALICE")
        assert alice.status == "submitted"
        assert alice.points_owed == 7


class TestTransition:
    def _assign_one(self, conn, name="ALICE", points=7):
        outcome = assign_month(conn, boarders=[record(name, "101", 2, 5, points)])
        assert isinstance(outcome, AssignmentSaved)
        return storage.list_punishments(conn, statuses=("assigned",))[0]

    def test_assigned_can_submit_on_time(self, conn):
        punishment = self._assign_one(conn)
        result = transition(conn, punishment.id, "submitted", timestamp="2026-04-09T09:00:00+00:00")

        assert isinstance(result, TransitionRejected) or hasattr(result, "message")
        row = storage.get_punishment(conn, punishment.id)
        assert row.status == "submitted"
        assert row.submitted_at == "2026-04-09T09:00:00+00:00"

    def test_assigned_can_become_overdue_then_submitted(self, conn):
        punishment = self._assign_one(conn)
        transition(conn, punishment.id, "overdue", timestamp="2026-04-10T09:00:00+00:00")
        transition(conn, punishment.id, "submitted", timestamp="2026-04-12T09:00:00+00:00")

        row = storage.get_punishment(conn, punishment.id)
        assert row.status == "submitted"
        assert row.overdue_at == "2026-04-10T09:00:00+00:00"
        assert row.submitted_at == "2026-04-12T09:00:00+00:00"

    def test_overdue_can_become_phone_held_then_submitted_releases_phone(self, conn):
        punishment = self._assign_one(conn)
        transition(conn, punishment.id, "overdue", timestamp="2026-04-10T09:00:00+00:00")
        transition(conn, punishment.id, "phone_held", timestamp="2026-04-11T09:00:00+00:00")
        transition(conn, punishment.id, "submitted", timestamp="2026-04-13T09:00:00+00:00")

        row = storage.get_punishment(conn, punishment.id)
        assert row.status == "submitted"
        assert row.phone_held_at == "2026-04-11T09:00:00+00:00"
        assert row.submitted_at == "2026-04-13T09:00:00+00:00"

    def test_any_state_can_void_with_optional_reason(self, conn):
        punishment = self._assign_one(conn)
        result = transition(conn, punishment.id, "voided", timestamp="2026-04-02T09:00:00+00:00", void_reason="exempt")

        assert hasattr(result, "message")
        row = storage.get_punishment(conn, punishment.id)
        assert row.status == "voided"
        assert row.voided_at == "2026-04-02T09:00:00+00:00"
        assert row.void_reason == "exempt"

    @pytest.mark.parametrize(
        ("from_status", "to"),
        [
            ("submitted", "phone_held"),
            ("submitted", "overdue"),
            ("phone_held", "overdue"),
            ("voided", "submitted"),
            ("voided", "assigned"),
        ],
    )
    def test_illegal_transitions_rejected(self, conn, from_status, to):
        punishment = self._assign_one(conn)

        if from_status == "phone_held":
            transition(conn, punishment.id, "overdue", timestamp="2026-04-03T09:00:00+00:00")
            transition(conn, punishment.id, "phone_held", timestamp="2026-04-04T09:00:00+00:00")
        elif from_status != "assigned":
            transition(conn, punishment.id, from_status, timestamp="2026-04-03T09:00:00+00:00")

        result = transition(conn, punishment.id, to, timestamp="2026-04-05T09:00:00+00:00")

        assert isinstance(result, TransitionRejected)
        row = storage.get_punishment(conn, punishment.id)
        assert row.status == from_status

    def test_late_submission_distinguishable_from_on_time(self, conn):
        punishment = self._assign_one(conn)
        transition(conn, punishment.id, "submitted", timestamp="2026-04-11T09:00:00+00:00")

        row = storage.get_punishment(conn, punishment.id)
        assert row.submitted_at > row.deadline


class TestListConsequences:
    def _assign(self, conn, month="2026-03"):
        outcome = assign_batch(
            conn,
            month=month,
            boarders=[
                record("ALICE", "101", 2, 5, 7),
                record("BOB", "102", 1, 19, 20),
            ],
            exemptions=set(),
            deadline="2026-04-10",
            assigned_at="2026-04-01T09:00:00+00:00",
        )
        assert isinstance(outcome, AssignmentSaved)

    def test_defaults_to_in_flight_only(self, conn):
        self._assign(conn)
        alice = storage.list_punishments(conn, statuses=("assigned",))[0]
        transition(conn, alice.id, "submitted", timestamp="2026-04-09T09:00:00+00:00")

        rows = list_consequences(conn)
        assert {r.normalized_name for r in rows} == {"BOB"}
        assert rows[0].status == "assigned"

    def test_show_all_includes_submitted_and_voided(self, conn):
        self._assign(conn)
        rows = storage.list_punishments(conn, statuses=("assigned",))
        transition(conn, rows[0].id, "submitted", timestamp="2026-04-09T09:00:00+00:00")
        transition(conn, rows[1].id, "voided", timestamp="2026-04-03T09:00:00+00:00", void_reason="exempt")

        all_rows = list_consequences(conn, show_all=True)
        assert len(all_rows) == 2

    def test_filters_by_month(self, conn):
        self._assign(conn, month="2026-03")
        self._assign(conn, month="2026-04")

        rows = list_consequences(conn, month="2026-04")
        assert {r.normalized_name for r in rows} == {"ALICE", "BOB"}
        assert all(r.month == "2026-04" for r in rows)

    def test_sorted_soonest_deadline_first(self, conn):
        outcome = assign_batch(
            conn,
            month="2026-03",
            boarders=[record("ALICE", "101", 2, 5, 7)],
            exemptions=set(),
            deadline="2026-04-20",
            assigned_at="2026-04-01T09:00:00+00:00",
        )
        assert isinstance(outcome, AssignmentSaved)
        outcome = assign_batch(
            conn,
            month="2026-03",
            boarders=[record("BOB", "102", 1, 19, 20)],
            exemptions=set(),
            deadline="2026-04-05",
            assigned_at="2026-04-01T09:00:00+00:00",
        )
        assert isinstance(outcome, AssignmentSaved)

        rows = list_consequences(conn)
        assert [r.normalized_name for r in rows] == ["BOB", "ALICE"]

    def test_due_flagged_once_deadline_passes_while_assigned(self, conn):
        self._assign(conn)

        before = datetime.fromisoformat("2026-04-09T12:00:00+00:00")
        on_day = datetime.fromisoformat("2026-04-10T12:00:00+00:00")
        after = datetime.fromisoformat("2026-04-11T12:00:00+00:00")

        assert all(r.is_due is False for r in list_consequences(conn, now=before))
        assert all(r.is_due for r in list_consequences(conn, now=on_day))
        assert all(r.is_due for r in list_consequences(conn, now=after))

    def test_submitted_never_due_even_after_deadline(self, conn):
        self._assign(conn)
        row = storage.list_punishments(conn, statuses=("assigned",))[0]
        transition(conn, row.id, "submitted", timestamp="2026-04-09T09:00:00+00:00")

        after = datetime.fromisoformat("2026-04-30T12:00:00+00:00")
        rows = list_consequences(conn, show_all=True, now=after)
        submitted = next(r for r in rows if r.status == "submitted")
        assert submitted.is_due is False

    def test_submission_after_deadline_flagged_late(self, conn):
        self._assign(conn)
        row = storage.list_punishments(conn, statuses=("assigned",))[0]
        transition(conn, row.id, "submitted", timestamp="2026-04-11T09:00:00+00:00")

        rows = list_consequences(conn, show_all=True)
        submitted = next(r for r in rows if r.status == "submitted")
        assert submitted.was_late is True

    def test_on_time_submission_not_flagged_late(self, conn):
        self._assign(conn)
        row = storage.list_punishments(conn, statuses=("assigned",))[0]
        transition(conn, row.id, "submitted", timestamp="2026-04-09T09:00:00+00:00")

        rows = list_consequences(conn, show_all=True)
        submitted = next(r for r in rows if r.status == "submitted")
        assert submitted.was_late is False

    def test_status_filter_narrows_results(self, conn):
        self._assign(conn)
        rows = storage.list_punishments(conn, statuses=("assigned",))
        transition(conn, rows[0].id, "submitted", timestamp="2026-04-09T09:00:00+00:00")

        assigned = list_consequences(conn, status="assigned")
        submitted = list_consequences(conn, status="submitted")
        assert {r.normalized_name for r in assigned} == {"BOB"}
        assert {r.normalized_name for r in submitted} == {"ALICE"}

    def test_show_all_groups_by_status_soonest_deadline_first(self, conn):
        self._assign(conn)
        alice = storage.list_punishments(conn, statuses=("assigned",), month="2026-03")[0]
        transition(conn, alice.id, "submitted", timestamp="2026-04-09T09:00:00+00:00")

        rows = list_consequences(conn, show_all=True)
        assert [r.normalized_name for r in rows] == ["BOB", "ALICE"]
