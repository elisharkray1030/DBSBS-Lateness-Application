# Confiscation edit, release, and remove semantics

Story 27 says staff may edit or remove a Confiscation "freely", but three other
decisions constrain what that can mean: a pending Redemption's tier is **locked
at creation** (story 16), a Confiscation's period is **fixed by its tier**
(story 21), and a confirmed Confiscation **freezes the Boarder's display name
and bed** (story 20). This ADR records what an edit changes and how the
subtractive row's other lifecycle actions (release, void, remove) behave, so the
model cannot be quietly rewritten later.

## Decision

- **Edit changes the tier, and nothing else.** The tier is the single lever that
  fixes both `points_redeemed` and the period (story 21), so an edit recomputes
  `points_redeemed = tier` and `release_due = release_due_for(tier, confirmed_at)`
  from the **original** `confirmed_at` date. The frozen display name and bed are
  not editable — editing them would rewrite what was communicated (story 20).
- **Only an active Confiscation may be edited.** A pending row's tier stays
  locked (story 16), and a settled (`released`/`voided`) row should not silently
  re-debit the Balance; corrections there go through remove.
- **An edit that raises the tier is floor-guarded.** Because the row is already
  subtracted from the Balance, the projected Balance is `balance + old_points −
  new_points`; a result below zero is refused (ADR 0006). Lowering the tier is
  always safe.
- **Release marks the phone returned and keeps the debit.** `released` remains a
  confirmed status, so the points stay debited; release may happen at any time,
  including early, and never waits on the lateness gate.
- **Void returns the points.** Voiding a pending Redemption cancels the
  reservation (reason optional); voiding an active Confiscation returns its
  points to the Balance (reason required, story 26). Both are additive, so no
  floor check is needed.
- **Remove deletes the live row and returns a confirmed row's points.** Any
  status may be removed; the prior state survives in the I-Point Audit History,
  matching Entries and Adjustments (story 27).
- Every action writes its `released`/`voided`/`edited`/`removed` audit row in the
  same transaction as the live change.

## Considered Options

- **Edit only `release_due`.** Rejected — it invents an arbitrary-date field the
  tier-fixes-period model does not have and lets `points_redeemed` drift from
  its tier.
- **Edit tier plus a manual date override.** Rejected for the same drift; staff
  who need a different period pick the tier that buys it.
- **Edit any status, recomputing after release.** Rejected — a settled row is a
  record of what was communicated; re-debiting it after the phone returned is
  hard to defend and easy to misread.
- **Void/remove instead of edit.** Rejected — story 27 explicitly offers edit as
  the light-touch correction, distinct from the auditable removal.

## Consequences

- The `tier == points_redeemed` and `release_due = tier period from
  confirmed_at` invariants hold for every stored Confiscation.
- Editing downward can bring a Boarder's Balance back above zero only by
  returning points; editing upward is a real debit and shares the floor guard
  with Adjustments and confirmation.
- The I-Points view's Confiscation list filters by `active`, `released`, and
  `voided`, and shows the derived due-for-release and Stacked flags; the app
  still never releases or returns a phone on its own (ADR 0005).
