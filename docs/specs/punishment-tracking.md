# Spec — Punishment tracking for the Lateness Dashboard

Status: ready-for-agent

## Problem Statement

The dashboard computes each Boarder's monthly Points but the workflow stops there. In reality, Points trigger a disciplinary consequence: the Boarder must copy the lateness rules N times and submit to staff by a Deadline; if not submitted on time, the Boarder's phone is held until the rules are handed in. Staff currently track this consequence offline (paper, memory), so there is no record of who owes what, whose phone is held, or whether deadlines were met. Two data-quality issues compound it: Month labels are free text (the same month can be saved under several labels and sort incorrectly), and the "X boarders recorded" figure counts the whole master list, not the Boarders actually late.

## Solution

After a Monthly Report is imported and reviewed, staff can **assign a Punishment** to every Boarder with Points (bulk, with per-Boarder exemptions) under one shared **Deadline**, then track each Punishment through its lifecycle — `assigned → overdue → phone held → submitted`, or `voided` — with a new **Consequences** view showing in-flight Punishments across all Months. Assignment **freezes** Points, Bed, and Name, so a later re-import of a corrected Monthly Log never mutates an issued Punishment. Also: enforce canonical `YYYY-MM` Month labels, and report the count of Boarders actually late.

## User Stories

1. As staff, I want to assign Punishments to every Boarder with Points in a Month with one action, so that I can issue consequences efficiently after reviewing the report.
2. As staff, I want to exempt individual Boarders from assignment (e.g. medical reasons), so that I can apply discretion.
3. As staff, I want to set one shared Deadline for a batch of assignments, so that all Boarders know the same due date.
4. As staff, I want to assign from the Month detail, where I already am after importing and reviewing, so that the flow stays natural.
5. As staff, I want an assigned Punishment to freeze Points, Bed, and Name at assignment time, so that later corrected imports don't change what I communicated to the Boarder.
6. As staff, I want confirmation of how many Punishments were assigned and to whom, so that I know the batch succeeded.
7. As staff, I want a Consequences view listing in-flight Punishments across all Months, so that I can see who owes what right now.
8. As staff, I want the Consequences view to default to in-flight Punishments (`assigned`, `overdue`, `phone held`), so that I see the actionable set first.
9. As staff, I want to toggle to see all Punishments including `submitted` and `voided`, so that I can review history.
10. As staff, I want the Consequences view sorted by Deadline (soonest first) with status grouping, so that urgent cases surface.
11. As staff, I want to filter the Consequences view by status and Month, so that I can narrow the set.
12. As staff, I want a Punishment to show as "due" once its Deadline passes but before I mark it overdue, so that I know it is now actionable without doing date arithmetic.
13. As staff, I want each Punishment to show Boarder, Bed, Points owed, Deadline, and current status, so that I can act on it.
14. As staff, I want to mark a Punishment `overdue` once its Deadline has passed unsubmitted, so that the record reflects reality.
15. As staff, I want to mark a Punishment `phone held` when I physically take the Boarder's phone, so that the record reflects who is holding a phone.
16. As staff, I want to mark a Punishment `submitted` when the Boarder hands in the rules, so that it becomes completed.
17. As staff, I want submitting a Punishment to release a held phone in the same action, so that the record stays consistent.
18. As staff, I want to void a Punishment with an optional reason when it was assigned by mistake or the Boarder was exempt, so that the audit trail shows why.
19. As staff, I want timestamps on every transition, so that I know when each action happened.
20. As staff, I want re-importing a corrected Monthly Log to leave issued Punishments untouched, so that consequences already communicated aren't silently changed.
21. As staff, I want to see whether a Punishment was submitted late, so that I can spot repeat deadline-missers.
22. As staff, I want a Boarder's Punishment history visible across Months, so that I can spot escalating patterns.
23. As staff, I want at most one active Punishment per Boarder per Month, so that consequences aren't double-counted.
24. As staff, I want to re-assign a Boarder's Punishment after it was voided, so that a corrected assignment can replace a mistake.
25. As staff, I want the Month field to accept only canonical `YYYY-MM`, so that the same Month can't be saved under different labels.
26. As staff, I want the Month input to be a date picker rather than free text, so that it's hard to enter a bad label.
27. As staff, I want Months to sort chronologically, so that the Report Archive reads naturally.
28. As staff, I want the "boarders recorded" message to count Boarders who were actually late, so that the number reflects reality.
29. As staff, I want a clean Month with zero lateness to still be saved, so that I have a record the Month was clean.

## Implementation Decisions

- **New module for the Punishment lifecycle**, parallel to the ingestion module: owns assign → transition → status, builds user-facing messages, and calls the persistence layer. This keeps domain rules out of the routes (which stay thin adapters).
- **Persistence in the storage layer**: schema creation, saving a batch, listing (with in-flight default + status/Month filters + deadline ordering), and status transitions. Every function takes the connection, matching the existing injectable-connection pattern.
- **Schema — `punishments` table** (from the settled model):
  - `id` PK, `normalized_name`, `display_name` (frozen), `bed` (frozen), `month`, `points_owed` (frozen), `deadline` (staff-set), `status`, `assigned_at`, `overdue_at`, `phone_held_at`, `submitted_at`, `voided_at`, `void_reason`
  - **Partial unique index**: one active Punishment per `(normalized_name, month)`, excluding `voided` — so voiding doesn't block re-assignment (U24).
- **State machine** — statuses `assigned | overdue | phone_held | submitted | voided`; all transitions **manual** (the app records reality; it never acts on a clock). Valid paths: `assigned → submitted` (on time) · `assigned → overdue → submitted` (late, phone never taken) · `assigned → overdue → phone_held → submitted` (phone held, released on submission) · any → `voided`. **"due" is computed at display time** (`now >= deadline` and status `assigned`), not stored. `was_late` is derived (`submitted_at > deadline`).
- **Snapshot at assignment** (ADR 0001): `points_owed`, `bed`, `display_name` frozen; a later re-import may *surface* a diff but never mutates an issued Punishment.
- **No batch table** (ADR 0001): `deadline` + `assigned_at` on each row; a batch is reconstructed by shared `(assigned_at, deadline)`.
- **Voided is a soft-delete** with optional `void_reason`, kept for audit (ADR 0001).
- **New routes** (thin): bulk assign (selected Boarders + shared Deadline), list Consequences (filters), per-Punishment transitions (overdue / phone held / submitted / void). Transitions reuse the existing fetch/form patterns in the UI.
- **UI**: a new **Consequences** tab (third tab) — server-rendered table, in-flight default, status/Month filters, "show all" toggle, Deadline-ascending + status grouping. An **Assign** action on the Month-detail view (bulk + exemptions + one Deadline). **The Month-detail lateness table is unchanged**; no printable per-Boarder slip.
- **Month canonicalization**: `input type="month"` in the UI plus server-side validation against `^\d{4}-\d{2}$`; anything else rejected. Fixes the lexicographic-sort and duplicate-label issues.
- **Boarder count fix**: the saved-report and success-message counts reflect Boarders with `frequency > 0`; zero-frequency rows are still saved so a clean Month persists.

## Testing Decisions

- A good test asserts **external behavior**: the outcome (assigned / rejected / transitioned) and the resulting persisted state — not which function got called or how rows are written.
- **Punishment module** is tested against the same in-memory SQLite `conn` fixture as `test_storage.py`, with synthetic inputs — prior art is `test_ingestion.py`/`test_storage.py`. This covers: batch assignment (with and without exemptions), the full state-machine transition matrix (every legal path + every illegal jump rejected), the computed "due" flag, snapshot freezing, and voided re-assignment.
- **Persistence functions** get their own storage-seam tests (assign persists rows; each transition persists status + its timestamp), mirroring `test_storage.py`'s structure.
- **Routes** are tested via the Flask `test_client` (prior art `test_app.py`): assign success/error, list filtering, and each transition endpoint.
- **Month validation** tested server-side (bad labels rejected, canonical labels accepted) and the overcount expectation updated at `test_ingestion.py:200`.

## Out of Scope

- Incident-level (day/time) detail and editing a single incident; Boarder History stays aggregate.
- Term/year roll-up.
- CSV dry-run / preview before Import.
- UI polish: pagination, bulk delete, history→Month links.
- Trust: auth, audit log, CSRF, container hardening (parked).
- Printable per-Boarder Punishment slips; phone asset registry (explicitly rejected).
- Automatic transitions / scheduler — transitions stay manual by design.

## Further Notes

- ADR `0001` (`docs/adr/0001-punishment-tracking-model.md`) records the model decisions: manual transitions, snapshot-at-assignment, denormalized Deadline, soft-delete `voided`, boarder+month grain.
- `CONTEXT.md` gained the **Discipline** glossary section (Points, Punishment, Deadline, Phone Hold).
- The "security file" **is** the Monthly Log; no new ingestion step is needed.
- The state machine and schema above come from the grilling session (no prototype was built); treat them as the settled contract.
