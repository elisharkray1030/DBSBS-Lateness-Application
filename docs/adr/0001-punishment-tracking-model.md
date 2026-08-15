# Punishment tracking model

We added a Punishment entity tracking the disciplinary consequence of monthly
lateness (copy-the-rules N times, staff-set deadline, phone hold if unsubmitted,
release on submission). States — assigned, overdue, phone_held, submitted,
voided — all transition manually: the app is record-keeping, not
workflow-driving. "Due" is a computed display flag, not a stored state. Points,
bed, and display_name are frozen at assignment so re-importing a corrected month
never mutates an in-flight or completed punishment. Deadline lives on each
punishment row (no batch table); batches are reconstructed by shared
assigned_at + deadline. Voided is a soft-delete kept for audit. Incident grain
was deferred; punishment lives at boarder+month grain matching the Monthly
Report.

## Considered Options

- **Transitions: manual vs automatic.** Rejected auto (scheduler/cron) — taking
  a phone is a physical act, and marking overdue is a staff act; a clock-driven
  state machine would race reality. Manual keeps the app honest.
- **Points at assignment: snapshot vs live-follow-report.** Rejected live —
  re-importing a corrected CSV mid-punishment would silently change a punishment
  already communicated to the boarder. Snapshot means re-import surfaces a diff
  but never mutates an issued punishment.
- **Batch: first-class table vs denormalized.** Rejected batch table — adds a
  join and an entity whose meaning is fully derivable from shared
  (assigned_at, deadline) on the punishment rows.
- **Voided: soft vs hard delete.** Rejected hard delete — punishments drive
  real-world consequences; the audit trail of "assigned then voided because X"
  matters.