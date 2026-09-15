# Spec — Shared Discipline Audit and vocabulary across Punishments and I-Points

Status: ready-for-agent

## Problem Statement

Staff can already see every change to a Boarder's I-Points, but a Punishment
leaves almost no provenance: once a row's status changes, only the current value
survives, and staff cannot see when it was assigned, marked overdue, phone-held,
submitted, or voided, or why. The two disciplinary lifecycles also keep separate
copies of shared vocabulary — I-Points mirrors the `phone_held` status by string —
so a rename in one module can silently break the other, and both `app.py` and the
templates hardcode status lists that should have one home. Because the two
lifecycles speak slightly different languages and disagree about what "due" means,
the Boarder Profile shows two disconnected histories and staff cannot get a single,
trustworthy account of what happened to a Boarder's discipline.

## Solution

One shared **Discipline Audit History** records every change across both
lifecycles. The existing I-Point audit table becomes the shared
`discipline_audit` table; it keeps its generic rows and gains an optional staff
**note**. Every Punishment assignment and status transition now leaves a row,
just as every I-Point write already does. The Boarder Profile shows one combined
Discipline Audit History, newest first, with each line saying what happened.

Alongside the shared table, the shared status vocabulary moves to one home
(`records.py`), so neither lifecycle imports the other and the hand-mirrored
`phone_held` string disappears. The I-Points Confiscation filter options and the
Punishments status filter are served from the lifecycle that owns them instead of
being reassembled in the adapter or hardcoded in a template. Finally, the two
lifecycles' "due" flags get distinct names — `deadline_passed` for a Punishment,
`due_for_release` for a Confiscation — so a template can no longer confuse them.

Upgrading an existing database is automatic and atomic: the table rename, the new
`note` column, and a synthetic `assigned` event for each pre-existing Punishment
happen in one migration step when the schema is opened, following the app's
existing idempotent-migration pattern.

## User Stories

1. As staff, I want every Punishment assignment to be recorded with who, when,
   and what was assigned, so that provenance does not begin only at the first
   status change.
2. As staff, I want every Punishment status transition recorded, so that I can
   see the full life of a Punishment rather than only its current state.
3. As staff, I want to attach a note to any Punishment transition, not only
   voids, so that I can record context for marking overdue, phone-held, or
   submitted.
4. As staff, I want to keep recording a reason when I void a Punishment, so that
   the existing void workflow is unchanged.
5. As staff, I want the void reason to remain visible as provenance after the
   void, so that the decision is defensible.
6. As staff, I want a single Discipline Audit History on the Boarder Profile
   covering both Punishments and I-Points, so that I do not have to reconcile two
   separate histories.
7. As staff, I want that history newest-first, so that the most recent action is
   the first thing I see.
8. As staff, I want each history line labelled with what happened in plain
   language, so that I can understand it without decoding status codes.
9. As staff, I want pre-existing Punishments to appear in the history with an
   `assigned` entry dated to when they were assigned, so that upgrading does not
   leave the history blank for live cases.
10. As staff, I want the Discipline Audit History to include edited, removed, and
    voided I-Point Entries, Redemptions, and Confiscations exactly as the I-Point
    Audit History does today, so that nothing is lost in the merge.
11. As staff, I want the existing Punishment Timeline (current state) to remain,
    so that I can still see the state of each Punishment at a glance.
12. As staff, I want the existing I-Point Entry and Confiscation tables to remain,
    so that the I-Points workflow is unaffected.
13. As staff, I want the I-Points tab's I-Point-specific audit history to keep
    working, so that the dedicated I-Points view is unchanged.
14. As staff, I want the Confiscation status filter options to be unchanged in
    name and behaviour, so that my muscle memory is not disrupted.
15. As staff, I want the Punishments status filter to keep offering every status,
    so that filtering still works.
16. As staff, I want a Punishment's "due" flag and a Confiscation's
    "due for release" flag to be unmistakably different, so that I never act on
    the wrong one.
17. As a maintainer, I want one `discipline_audit` table shared by both
    lifecycles, so that the combined history is a single read rather than a union
    of two tables at every call site.
18. As a maintainer, I want the shared audit types named `DisciplineAudit`,
    `DisciplineAuditDraft`, and `DisciplineAuditNote`, so that the names describe
    the shared domain rather than one lifecycle.
19. As a maintainer, I want the audit staging and reading functions named
    `stage_discipline_audit` and `list_discipline_audit`, so that the storage seam
    has one shared vocabulary.
20. As a maintainer, I want no compatibility aliases for the old `IPoint*`
    names, so that there is exactly one way to refer to each thing.
21. As a maintainer, I want the audit row to carry an optional `note`, so that a
    staff note has a home that is not overloaded onto a JSON snapshot.
22. As a maintainer, I want the punishment status write and its audit row to be
    staged in one transaction, so that a crash can never leave a status change
    without provenance.
23. As a maintainer, I want the punishment storage writes to stop committing on
    their own and the lifecycle to own the transaction, so that the audit pairing
    is provable in one place.
24. As a maintainer, I want a failing audit stage to roll back the status write,
    so that the atomicity guarantee is tested, not just asserted.
25. As a maintainer, I want all shared status strings and their human labels in
    `records.py`, so that vocabulary lives in one module.
26. As a maintainer, I want neither lifecycle to import the other, so that the
    two modules stay independent.
27. As a maintainer, I want the `phone_held` string mirror in `ipoints.py` to be
    deleted, so that a rename in `punishments.py` cannot silently break Stacked.
28. As a maintainer, I want the Confiscation filter definition to live behind the
    I-Points interface, so that the adapter does not reassemble lifecycle
    vocabulary.
29. As a maintainer, I want the Punishments status filter list to come from the
    punishments vocabulary rather than a literal in the template, so that adding
    a status cannot leave the dropdown stale.
30. As a maintainer, I want a Punishment's due flag renamed to `deadline_passed`
    and a Confiscation's to `due_for_release`, so that the two are never confused.
31. As a maintainer, I want the rekey migration to point at `discipline_audit`, so
    that Match Key normalisation keeps covering audit rows after the rename.
32. As a maintainer, I want the table rename to preserve audit row ids, so that
    the All-Time List's audit-only Match Keys survive exactly.
33. As a maintainer, I want the upgrade to run as one atomic step in schema setup,
    so that a partial upgrade is impossible.
34. As a maintainer, I want the upgrade to be idempotent, so that opening the
    database repeatedly is safe.
35. As a maintainer, I want a fresh database to come up with the `note` column
    already present, so that the migration is not a special case for new installs.
36. As a maintainer, I want the backfill to insert one `assigned` event per
    pre-existing Punishment only, so that re-running never duplicates history.
37. As a maintainer, I want an old-shape database fixture in the tests, so that
    the rename and backfill are exercised against a real legacy shape.
38. As a maintainer, I want the existing All-Time List behaviour to keep passing,
    so that renaming the audit table does not drop discoverable Boarders.
39. As a maintainer, I want a single `describe_audit` helper per lifecycle that
    turns a raw audit row into one labelled line or nothing, so that each
    lifecycle owns its own wording.
40. As a maintainer, I want the combined history assembled from one shared read
    and two describers merged newest-first, so that the merge policy lives in one
    place.
41. As a maintainer, I want the change recorded as ADR-0010, so that the shared
    table and vocabulary decision is not re-litigated.
42. As a maintainer, I want ADR-0010 to state that the ADR-0005/0007 ledger tables
    are not restructured, so that the audit rename is not mistaken for a ledger
    change.
43. As a maintainer, I want the type checker to stay clean across all eleven
    modules, so that the renames do not leave dangling references.
44. As a maintainer, I want the full test suite green before the work is
    considered done, so that the broad rename is verified end to end.

## Implementation Decisions

- **One shared audit table.** Rename the existing `ipoint_audit` table to
  `discipline_audit`. Keep the generic shape (`entity_type`, `entity_id`,
  `normalized_name`, `action`, `before_state`, `after_state`, `changed_at`) and
  add a nullable `note` column. The table is the provenance sidecar for both
  lifecycles.

- **Shared audit types.** In `records.py`, rename `IPointAuditDraft` to
  `DisciplineAuditDraft` and add `note: str | None = None`; rename `IPointAudit`
  to `DisciplineAudit`; rename `IPointAuditNote` to `DisciplineAuditNote`. No
  aliases remain. `DisciplineAuditNote` is shared by both lifecycles and carries
  `action`, `changed_at`, and `description`.

- **Shared storage seam.** Rename `stage_ipoint_audit` to
  `stage_discipline_audit` and `list_ipoint_audit` to `list_discipline_audit`.
  `list_discipline_audit(conn, normalized_name=None)` keeps its optional Match Key
  filter; entity-type filtering stays with the lifecycle that owns the entity
  (as `_recorded_trigger_months` does today). The staging function continues to
  not commit — the lifecycle owns the transaction.

- **Punishment audit rows.** A Punishment audit row uses
  `entity_type = "punishment"`, `action` equal to the target status
  (`assigned`, `overdue`, `phone_held`, `submitted`, `voided`), `before_state`
  and `after_state` as JSON snapshots of the mutable lifecycle columns (status
  and the transition timestamps, plus `void_reason`) with the frozen
  `points_owed`/`deadline` present on the assignment snapshot, and `note` set to
  the staff note when one was given. This mirrors the existing I-Point snapshot
  convention.

- **Punishment writes own their transaction.** `assign_punishments` and
  `transition_punishment` in the storage layer stop committing and become staging
  writes; `punishments.assign_batch` and `punishments.transition` open one
  transaction that stages the status write and the audit row(s). This matches the
  I-Points convention (`stage_*` plus `with conn:`) and is what makes the
  audit pairing provable. `assign_batch` builds one `assigned` event per inserted
  Punishment (using the new row id); `transition` builds one event for the
  transition.

- **Note on every transition; `void_reason` retained.** `transition` gains an
  optional `note` parameter accepted for every target. The transition form gains
  the optional note input on every row, not just voids. On a void, the same input
  also continues to populate the existing `void_reason` domain field, so existing
  behaviour and validation are preserved and no information is lost.

- **Atomic migration inside schema setup.** A new migration step, run before the
  `CREATE TABLE IF NOT EXISTS` for the shared table, renames `ipoint_audit` to
  `discipline_audit` when the old table exists and the new one does not, and adds
  the `note` column when missing. On a fresh database the create produces the
  table with `note` directly and the migration is a no-op. The rekey migration's
  table list points at `discipline_audit`. After the rekey, a backfill inserts one
  synthetic `assigned` event per Punishment that has no `punishment` audit row
  yet, dated to its `assigned_at`. The whole step is atomic and idempotent.

- **Shared status vocabulary in `records.py`.** The status strings and human
  labels for both lifecycles move to `records.py`, as do the grouped status
  tuples the surfaces need. Punishment transition legality and the offered-action
  table stay in `punishments.py` as policy; tier tables and Confiscation status
  groups stay in `ipoints.py`. `ipoints.py` deletes its `_PHONE_HELD_PUNISHMENT`
  mirror and reads the shared constant. Neither lifecycle imports the other.

- **Filter ownership.** The I-Points lifecycle exposes the Confiscation filter
  definition (default key, status groups, labelled options) and the adapter
  unpacks it. The Punishments route passes the status options built from the
  shared vocabulary into the template context, replacing the hardcoded status
  list in the template.

- **`is_due` disambiguation.** `Punishment.is_due` becomes
  `Punishment.deadline_passed`; `Confiscation.is_due` becomes
  `Confiscation.due_for_release`. The two attach-flags functions and every
  template site update in lockstep. `was_late` and `stacked` are unchanged. This
  is a pure rename with no alias.

- **Combined Discipline Audit History.** The Boarder Profile gains a section built
  from one `list_discipline_audit` read for the Match Key. Each lifecycle exposes
  a pure `describe_audit(audit)` that returns a `DisciplineAuditNote` for the
  entity types it owns and `None` otherwise. The profile keeps the non-`None`
  notes, merges them, and sorts newest-first by `changed_at`. The existing
  Punishment Timeline and I-Point tables remain.

## Testing Decisions

- Test external behaviour only: the persisted audit rows, the public lifecycle
  outcomes, the rendered history, and the migrated database shape. Do not assert
  on private helper internals or SQL text.

- **Storage seam.** Test `stage_discipline_audit` / `list_discipline_audit` and
  the schema directly against the in-memory `conn` fixture: the `note` column
  round-trips, ordering is newest-first, and the optional Match Key filter works.
  Prior art: the existing I-Point audit and schema tests in the storage suite.

- **Punishment lifecycle.** Test `assign_batch` and `transition` through their
  public returns and the persisted audit rows: assignment writes one `assigned`
  event per new Punishment; each transition writes exactly one event with the
  right action, before/after snapshots, and note; a rejected transition writes
  nothing. Prior art: the I-Point entry/confiscation audit tests.

- **Atomicity.** Inject a failure in audit staging and assert the Punishment's
  status and audit history are unchanged, proving the transaction rolls back.
  Prior art: the I-Points lifecycle owns `with conn:` the same way.

- **Migration.** Build an old-shape database with a populated `ipoint_audit`
  table and pre-existing Punishments using raw SQL, run schema setup (twice, for
  idempotency), and assert: the old table is gone, the new table holds every row
  with ids and Match Keys preserved, the `note` column exists, exactly one
  `assigned` event exists per pre-existing Punishment, and re-running adds
  nothing. Prior art: the drop-adjustments and Match Key migration tests.

- **All-Time List.** Keep the existing audit-only-discoverability tests passing
  against the renamed table. Prior art: the statistics-storage audit tests.

- **HTTP.** Through the existing fresh-client fixture, assert: the Boarder Profile
  renders a combined history newest-first covering both lifecycles; a note
  submitted on a non-void transition is retained and shown; the Confiscation
  filter options and the Punishments status filter still render. Prior art: the
  profile and I-Points route tests.

- **Vocabulary.** Assert behaviour rather than definitions: the I-Points
  Confiscation filter and Punishments filter still offer their expected options
  after the constants move. The absence of the `phone_held` mirror is implied by
  the lifecycle tests passing with the shared constant.

## Out of Scope

- The one write-applier for the ten mutation routes (#263); this spec only makes
  the punishment writes own their transaction, not the route consolidation.
- The internal I-Points audited-transition combinator (#264); this spec lays the
  shared table and types it will target, but does not build the combinator.
- The dashboard join and page-context bundle work (#267).
- The static JavaScript split (#265).
- Any change to Punishment transition legality, offered actions, or the
  manual-only state machine.
- Restructuring the ADR-0005/0007 ledger tables (`ipoint_entries`,
  `confiscations`); only the audit table is renamed.
- Any new automatic transition or release behaviour; the app still never acts on
  a clock.

## Further Notes

- Recorded as [ADR-0010](../adr/0010-shared-discipline-audit.md), which pairs with
  ADR 0001 (frozen snapshots) and explicitly records that the ADR-0005/0007 ledger
  tables are not touched.
- Known landmines: the rekey migration's table list references the old audit table
  name; the All-Time List derivation reads audit rows for audit-only Match Keys;
  the punishment audit must share the status write's transaction; and the broad
  `IPoint*` rename touches many test call sites.
- Follows the existing seed/demo-data conventions: no real names in fixtures or
  tests.
