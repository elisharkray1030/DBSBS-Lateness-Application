# Spec — I-Points ledger, Redemption, and Phone Confiscation

Status: ready-for-agent

## Problem Statement

The dashboard tracks monthly lateness Points and the Punishments they trigger,
but repeated inappropriate behaviour is a separate disciplinary stream that
staff currently track off-system (paper, memory). There is no record of the
I-Points a Boarder has accumulated, no carry-over of unresolved points between
months, and no consistent way to turn accumulated points into the agreed
consequence — the Boarder's phone being confiscated for a fixed period. Staff
also need to fix and freely rework this record as situations change, without
losing the audit trail that makes the discipline defensible.

## Solution

Add **Irregularity Points (I-Points)** as a second, persisting disciplinary
currency. Staff log I-Point Entries (points, date, reason) and I-Point
Adjustments directly in a new I-Points view. A Boarder's I-Point Balance
carries across months and never falls below zero. At each local month's close,
a Boarder whose Balance is
at least 5 is shown a pending Redemption: the largest tier at or below the
Balance (5/10/15 → 1 day / 1 week / 1 calendar month) becomes a Phone
Confiscation when staff confirm it, and the remainder carries forward. Every
Entry, Adjustment, Redemption, and Confiscation is freely editable, voidable,
or removable, with every change retained in an I-Point Audit History. Phone
Confiscations surface alongside lateness Punishments: the Boarder Profile shows
both, and the I-Points view flags a Confiscation as Stacked when a lateness
Phone Hold applies at the same time. The phone is returned only once both gates
clear — the lateness Punishment is submitted and the Confiscation is released —
and the app never releases it on its own.

## User Stories

1. As staff, I want to log an I-Point Entry for a Boarder with points, a date,
   and a reason, so that repeated behaviour is recorded as it happens.
2. As staff, I want the Entry date to default to today but be editable, so I
   can record an Entry I missed.
3. As staff, I want a reason to be required on every Entry, so the record is
   defensible.
4. As staff, I want to log more than one point in a single Entry, so one
   serious occasion needn't be logged many times.
5. As staff, I want to edit an Entry's points, date, or reason, so I can correct
   a mistake.
6. As staff, I want to remove an Entry, so a mistaken log disappears from the
   Balance.
7. As staff, I want a removal to keep a record of what was removed, so nothing
   vanishes silently.
8. As staff, I want to add a signed I-Point Adjustment, so I can rebalance a
   Boarder's I-Points without rewriting history.
9. As staff, I want to edit or remove an Adjustment, so corrections stay easy.
10. As staff, I want to see every Boarder's I-Point Balance at a glance.
11. As staff, I want the Balance to combine Entries and Adjustments minus
    confirmed Redemptions, and never fall below zero, so it always reflects
    what's outstanding.
12. As staff, I want the Balance to carry across months, so unresolved points
    aren't lost.
13. As staff, I want a pending Redemption to appear once a Boarder's Balance
    reaches a tier at a month's close, so redemption follows the agreed cadence.
14. As staff, I want at most one Redemption per Boarder per month.
15. As staff, I want the pending Redemption to take the largest tier at or
    below the Balance, capped at 15, so severity maps to the agreed scale.
16. As staff, I want an ignored pending Redemption to stay locked at the tier it
    was created at, so severity never escalates silently.
17. As staff, I want the remainder after a Redemption to carry to the next
    month.
18. As staff, I want to confirm a pending Redemption, creating a Phone
    Confiscation for the Boarder.
19. As staff, I want to void a pending Redemption, so a Redemption that
    shouldn't proceed is cancelled.
20. As staff, I want a confirmed Phone Confiscation to keep the Boarder's name
    and bed as they were at confirmation, so later changes don't rewrite what
    was communicated.
21. As staff, I want the Confiscation period fixed by the tier — 1 day for 5, 1
    week for 10, 1 calendar month for 15.
22. As staff, I want confirming to record when the phone was taken.
23. As staff, I want a due-for-release flag once the period has elapsed, so I
    know it's actionable without the app acting on its own.
24. As staff, I want to mark a Confiscation released, recording when the phone
    was returned.
25. As staff, I want to release a Confiscation early when I choose to.
26. As staff, I want to void a Confiscation with a reason when it was recorded
    in error.
27. As staff, I want to edit or remove a Confiscation, freely.
28. As staff, I want at most one active Confiscation per Boarder at a time, so a
    phone can't be confiscated twice.
29. As staff, I want a Confiscation flagged Stacked while the Boarder also has a
    lateness Phone Hold, so I know the phone is held for both reasons.
30. As staff, I want a Boarder's full I-Point history on their Boarder Profile.
31. As staff, I want the Boarder Profile to show the I-Point Balance.
32. As staff, I want to log I-Points quickly from the Boarder Profile.
33. As staff, I want Boarders known only through I-Points to appear in Find a
    Boarder and the All-Time List.
34. As staff, I want to review a Boarder's I-Point Audit History, showing what
    changed, when, and to what.
35. As staff, I want success and error feedback on the page after every action,
    not in the URL.
36. As staff, I want the I-Points view reachable from the main tab bar.
37. As staff, I want the I-Points view kept separate from lateness Punishments.
38. As staff, I want to see pending Redemptions together, so I can action them
    as a group.
39. As staff, I want to filter Confiscations by status, so I can see active,
    released, and voided sets.
40. As staff, I want server-side validation: Entry points are positive integers,
    Adjustments are non-zero signed integers whose deduction may not exceed the
    Balance, and required reasons are enforced.
41. As staff, I want month-close evaluation to use the local calendar date, so a
    month closes at local midnight.
42. As staff, I want back-dated Entries to change only the running Balance,
    never reopening an already-closed month.
43. As staff, I want a Removed Boarder to keep frozen I-Point history and accrue
    no new Entries.
44. As staff, I want to be able to confirm or void a pending Redemption for a
    Removed Boarder.
45. As staff, I want every I-Points write protected by CSRF, like the rest of
    the app.
46. As staff, I want reads to stay read-only and writes to keep the shared
    lock-retry behavior, so the shared-NAS deployment stays safe.
47. As staff, I want the I-Points view keyboard-operable with labelled controls,
    so it's accessible without a mouse.
48. As staff, I want deterministic demo/seed I-Point data, so the view isn't
    empty in development.
49. As staff, I want figures to stay correct when a Balance carries across
    several months.
50. As staff, I want every edit and removal to record the prior state, so the
    discipline stays reviewable.
51. As staff, I want any write that would take a Balance below zero to be
    refused, so a Boarder never shows negative I-Points.
52. As staff, I want the phone returned only once both the lateness Punishment
    is submitted and the I-Point Confiscation is released, so neither
    consequence is cut short.
53. As staff, I want a Confiscation flagged due for release without the app
    releasing it, so I stay in control of the phone's return.

## Implementation Decisions

- **New lifecycle module** alongside the Punishments module: owns Balance
  computation, pending-Redemption derivation, Redemption confirmation, the
  Confiscation lifecycle, edit/remove/void rules, and audit orchestration. The
  route layer stays a thin adapter; persistence stays in the storage module.
  No shared "lifecycle engine" is extracted from the Punishments module — two
  parallel modules is the established idiom, and a third lifecycle is the
  trigger to revisit that.
- **Typed records** join the existing discipline records: an I-Point Entry, an
  I-Point Adjustment, a Confiscation, and an audit entry, plus a per-Boarder
  summary carrying the Balance and the derived pending Redemption.
- **Schema — four tables**, created idempotently alongside the existing ones:
  - `ipoint_entries`: id, normalized_name (Match Key), points (positive int),
    occurred_on (date), reason, recorded_at.
  - `ipoint_adjustments`: id, normalized_name, points (non-zero signed int),
    reason, recorded_at.
  - `confiscations`: id, normalized_name, display_name, bed (both frozen at
    confirmation), trigger_month, points_redeemed, tier, status
    (`active | released | voided`), created_at, confirmed_at, release_due,
    released_at, voided_at, void_reason.
  - `ipoint_audit`: id, entity_type (`entry | adjustment | confiscation`),
    entity_id, normalized_name, action
    (`created | edited | removed | confirmed | released | voided`),
    before_state, after_state, changed_at.
- **A confirmed Redemption is the Confiscation row.** There is no separate
  Redemption table: the Confiscation carries `points_redeemed`, and the Balance
  is derived as Entries + Adjustments − the `points_redeemed` of non-voided
  Confiscations. Pending Redemptions are **derived, never stored** (ADR 0005).
- **Derived Balance, never stored**, so it cannot drift from the ledger.
- **The Balance is floored at zero** (ADR 0006): the slices that introduce
  subtractive writes (#177–#179) reject any Entry or Adjustment change that
  would leave the Balance below zero. As of #176 the only write is additive
  logging, which cannot break the floor. A subtractive Adjustment is additionally
  capped at the current Balance; confirming a Redemption is capped at the
  Balance and voiding one adds points back, so neither can break the floor on
  its own.
- **I-Point Entries carry the date under `occurred_on`** — the date the Entry
  records, distinct from the `recorded_at` audit timestamp. The user-visible
  field is labelled "Date".
- **The phone's return has two gates** (ADR 0005): the lateness Phone Hold
  clears on Punishment submission and the Confiscation clears on release at or
  after `release_due`. A Stacked boarder's phone returns only once both clear; a
  Confiscation past `release_due` raises a due-for-release flag, and the app
  never releases on its own.
- **One active Confiscation per Boarder** enforced by a partial unique index on
  `normalized_name` where `status = 'active'`.
- **Month-close evaluation is one pure function** taking the ledger and an
  injected `today`. It inspects the latest fully-elapsed local calendar month;
  when the Balance as of that month's end is at least 5 and no active
  Confiscation exists, a pending Redemption is produced at the largest tier at
  or below the Balance, capped at 15, locked at creation. Injecting `today`
  keeps the boundary deterministic in tests (the same pattern the Punishment
  module uses for its computed flags). Local date comes from the machine clock
  (consistent with the existing `current_year` derivation); this is the only
  place locale-aware time matters.
- **Point-in-time confirmed Redemption.** Editing Entries after a Redemption is
  confirmed never recomputes it; staff rebalance with an explicit Adjustment.
- **Transactional audit.** Every Entry, Adjustment, and Confiscation mutation
  writes its audit row in the same connection block, so the live ledger and its
  history cannot diverge. Live rows may be hard-deleted where "remove" is
  offered; the audit row preserves the prior state.
- **Editability per entity**, matching the agreed scope: Entries and Adjustments
  may be edited and removed; a confirmed Redemption may be edited or voided; a
  Confiscation may be edited, voided, or removed. Forcing a Redemption before a
  month's close and resetting a Balance are not offered. An edit or removal is
  refused when it would take the Balance below zero (ADR 0006).
- **Routes** (thin, following the GET-render / POST-flash-redirect pattern and
  the shared mutation-retry wrapper): a GET I-Points page; POST routes to log,
  edit, and remove an Entry; to add, edit, and remove an Adjustment; to confirm
  or void a pending Redemption; and to confirm/edit/void/release/remove a
  Confiscation. Every POST carries a CSRF token; reads open the read-only
  connection, writes the read-write one.
- **UI — one dedicated I-Points page** (not a panel on the home template),
  rendered through the shared layout, with a new tab-bar entry next to
  Punishments. The page holds: the Balance list with each Boarder's Balance and
  pending Redemption, grouped pending Redemptions for batch action, one
  per-Boarder ledger listing Entries and Adjustments together under a Type
  column with inline edit/remove, the Confiscation list with status
  filtering and Stacked/due-for-release flags, and per-Boarder audit history.
  Controls are server-rendered native form elements with labels and `aria-live`
  feedback; removal/edit actions reuse the shared confirm dialog.
- **Boarder Profile integration**: the profile route loads the Boarder's Balance
  and I-Point history, and the profile template gains an I-Point section and a
  quick-log action. The Stacked flag is derived where an active Confiscation
  coexists with a `phone_held` Punishment for the same Match Key.
- **All-Time List union gains I-Point keys**, so a Boarder known only through
  I-Points is discoverable in Find a Boarder and the All-Time List, sharing the
  existing freshest-first identity resolution.
- **The Match-Key migration re-keys the new tables** along with the existing
  ones, so legacy keys stay consistent.
- **Seeding**: deterministic demo I-Point data (Entries, at least one Adjustment,
  and one active Confiscation) is added to the demo seeder so the view and
  browser tests have data.
- **Naming cleanup**: the Punishments `TransitionSaved` message currently prints
  the Match Key while its sibling success message prints display names; align it
  while paralleling the modules.
- **Pre-factor — rename the Entry date field**: the shipped `awarded_on` column
  and its Python/route/template/seeder uses are renamed to `occurred_on` in their
  own commit before the Adjustments slice, since "awarded" contradicts the
  glossary (`_Avoid_: award`) and implies the points are a positive thing.
- **Documentation**: the architecture doc (which still describes "two seams" and
  covers Punishments only) is extended with the I-Points module, tables, and
  view. `CONTEXT.md`, ADR 0005, and ADR 0006 carry the vocabulary and model.

## Testing Decisions

- A good test asserts **external behavior** — the persisted outcome, the
  rendered page, or the computed figure — never which helper was called or how
  a row was written.
- **Primary seam: HTTP routes** via the Flask test client, matching the existing
  Punishments route tests. Covered: logging, editing, and removing Entries;
  adding/editing/removing Adjustments; the Balance and pending Redemption
  rendering; confirming and voiding a Redemption; each Confiscation action
  (confirm, edit, void, release, release early, remove); status filtering; the
  Stacked flag; CSRF rejection; a rejected overdraw surfaced as feedback; and the
  Boarder Profile I-Point section.
- **Secondary seam: the new lifecycle module**, tested with the in-memory
  connection fixture and an injected `today`, mirroring the Punishments module
  tests. Covered: Balance across Entries, Adjustments, and redemptions; tier
  selection (largest ≤ Balance, cap 15, locked); carry-forward across multiple
  months; no second pending while one is open; back-dated Entries not reopening
  a closed month; one active Confiscation; the non-negative floor enforced on
  every mutation; the two-gate phone release; and the audit row written per
  mutation. Storage functions are exercised through these tests, so no separate
  storage seam is introduced.
- **One browser test** for the interactive and accessibility requirements:
  inline edit/remove flows and keyboard-operable, labelled controls, matching
  the existing click-through tests. The route and module seams cannot prove
  these.
- **Prior art**: the Punishments route tests, the Punishments module tests, the
  storage punishment tests for the connection fixture, and the click-through
  browser tests. Test helpers extend the existing seed helpers.

## Out of Scope

- Forcing a Redemption before a month's close, and resetting a Balance.
- Automatic transitions or any scheduler — Confirm, Release, and Void stay
  manual (ADR 0001, ADR 0005).
- Detail beyond the Entry's date and reason (no time of day).
- Extracting a shared lifecycle engine from the Punishments and I-Points
  modules.
- Authentication and per-staff attribution — the app has no auth, so audit rows
  carry a timestamp, not an author.
- Folding I-Points into the Punishments view; the two stay separate.
- Bulk import of I-Points, pagination, and CSV export of the I-Point ledger.

## Further Notes

- ADR 0005 records the model and its rejected alternatives (scheduler,
  extending `punishments`, pending escalation, editable vs append-only ledger,
  import-driven evaluation). ADR 0006 records the non-negative Balance floor.
- `CONTEXT.md` carries the glossary: Irregularity Points, I-Point Entry,
  I-Point Adjustment, I-Point Balance, I-Point Audit History, Redemption, Phone
  Confiscation, and Stacked.
- "Stacked" is additive, not interchangeable: the phone is released only once
  both gates clear — the lateness Punishment is submitted and the Confiscation
  is released. This is the recorded interpretation of the grilling decision.
- "1 month" means one calendar month, not 30 days.
- Existing databases gain the new tables through the idempotent schema step; no
  backfill is required because the feature has no prior data.
