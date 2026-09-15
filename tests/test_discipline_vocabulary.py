"""Vocabulary-coverage guards for the shared discipline status vocabulary.

These assert that the policy and storage tables track `records.py` rather than
re-typing status strings: adding, removing, or renaming a status without
updating a table becomes a failing test instead of a silent runtime oddity.
"""

from records import (
    PUNISHMENT_ASSIGNED,
    PUNISHMENT_STATUSES,
    PUNISHMENT_VOIDED,
)

import storage
from punishments import (
    _DUE_GATED_TARGETS,
    _OFFERED_TRANSITIONS,
    _VOID_ACTION,
    VALID_TRANSITIONS,
)


class TestPunishmentVocabularyCoverage:
    def test_transition_table_covers_every_status(self):
        assert set(VALID_TRANSITIONS) == set(PUNISHMENT_STATUSES)

    def test_every_transition_target_is_a_known_status(self):
        known = set(PUNISHMENT_STATUSES)
        for targets in VALID_TRANSITIONS.values():
            assert targets <= known

    def test_offered_actions_cover_every_status_but_voided(self):
        # Void is appended to every non-voided row rather than listed here.
        assert set(_OFFERED_TRANSITIONS) == set(PUNISHMENT_STATUSES) - {
            PUNISHMENT_VOIDED
        }

    def test_every_offered_target_is_a_known_status(self):
        known = set(PUNISHMENT_STATUSES)
        for offered in _OFFERED_TRANSITIONS.values():
            for target, _label in offered:
                assert target in known
        assert _VOID_ACTION.target in known

    def test_due_gated_targets_are_known_statuses(self):
        assert _DUE_GATED_TARGETS <= set(PUNISHMENT_STATUSES)

    def test_storage_columns_cover_every_status_but_assigned(self):
        # `assigned` is insert-only, so it stamps no transition column.
        assert set(storage._PUNISHMENT_STATUS_COLUMNS) == set(
            PUNISHMENT_STATUSES
        ) - {PUNISHMENT_ASSIGNED}
