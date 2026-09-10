# Spec — Storage-seam guard: reject unknown Punishment transition statuses

## Problem Statement

The persistence seam that moves a Punishment through its lifecycle maps the requested status to a timestamp column via a fixed dictionary. A status outside that map (e.g. a typo, a tampered form, or a future caller that bypasses the domain layer) falls through to an unhandled `KeyError` and surfaces as a generic 500 to staff. Today the domain layer happens to validate every status before the seam is reached, so the crash is unreachable through the UI — but the seam itself is unguarded, and defense-in-depth says the last writer should never corrupt the request into a bare crash.

## Solution

The persistence seam validates its status argument itself: an unknown status is rejected with a clear, typed `ValueError` naming the offending value and the valid set, instead of leaking a `KeyError`. Known statuses continue to persist exactly as today. The domain layer's validation is unchanged and remains the primary gate; this is the seam's own backstop.

## User Stories

1. As staff, I want a malformed Punishment transition request to never crash the app, so that a bad request can't take down the page.
2. As a developer, I want the persistence seam to reject unknown statuses with a clear, typed error, so that callers who bypass the domain layer fail loudly and understandably rather than with a raw key-lookup exception.
3. As a developer, I want the error message to list the valid statuses, so that a caller can self-correct without reading the source.
4. As staff, I want valid transitions to behave exactly as before, so that the hardening doesn't change how Punishments move through their lifecycle.

## Implementation Decisions

- **Where the guard lives**: the storage-layer function that applies a Punishment transition (`transition_punishment`). It currently indexes a status → column dictionary directly; the change validates membership before that lookup.
- **Error type**: `ValueError`, matching the storage layer's existing convention for invalid input (e.g. unknown Boarder, duplicate in batch). No new exception type; a typed, catchable error is enough.
- **Error message**: names the offending status and the set of valid transition statuses (the dictionary's own keys, so the guard and the column mapping can never drift apart).
- **No schema change**, no change to the `punishments` table, no new columns.
- **No change to the domain layer** (`punishments.transition`) or the route: both already validate via the state machine (`VALID_TRANSITIONS`) and return `TransitionRejected` for illegal moves. The seam guard is defense-in-depth only.
- **The valid set** is derived from the existing status → column mapping keys (`overdue`, `phone_held`, `submitted`, `voided`), not a second hand-maintained list — keeping one source of truth so the two can't diverge.
- **Unknown-status calls must not write anything**: the seam rejects before any `UPDATE`, so a bad request leaves the row untouched.

## Testing Decisions

- A good test asserts **external behavior**: an unknown status is rejected with a typed `ValueError` and no row mutation, and each known status still persists its status + timestamp column. Not which internal branch fired.
- **Module tested**: the storage layer, at the storage seam — the highest seam at which this guard is observable. The domain and route layers already validate, so a test above the seam would never reach the guard; below the seam there is no behavior left to observe. One seam, the existing one.
- **Prior art**: the storage test suite's `TestTransitionPunishment` class (mirroring `TestAssignPunishments`) — the seam tests just added for exactly this function. `pytest.raises(ValueError)` patterns already exist throughout the storage tests.
- New cases to add:
  - An unknown status raises `ValueError` (asserting the type, not `KeyError`).
  - The error message names the offending status and lists the valid set.
  - After a rejected call, the row's status and all timestamp columns are unchanged.
  - Each of the four known statuses still persists (already covered by the existing `TestTransitionPunishment` cases — they guard the no-regression half).

## Out of Scope

- Enforcing the state machine at the storage seam — legal-path checks stay in the domain layer (ADR 0001: manual transitions, recorded rules).
- Any change to the domain layer, routes, or UI copy.
- Schema or migration changes.
- The `_was_late` on-deadline-day boundary (already shipped via #122).
- Converting this to a user-facing 4xx page — the guard makes the failure typed and catchable; the current reachability (blocked upstream) means no UI path changes.

## Further Notes

- Originated from the code review of the storage-seam transition tests (#126): the seam's bare dictionary subscript was flagged as the one place an unknown status produces an unhandled crash.
- Follows the storage layer's established "raise `ValueError` on invalid input" convention.
- No interaction with `docs/adr/0001-punishment-tracking-model.md`: manual-only machine and status vocabulary are unchanged; the seam gains validation, not new transitions.