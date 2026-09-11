# I-Point ledger and month-end redemption

Added Irregularity Points (I-Points) as a second, persisting disciplinary
currency alongside monthly lateness Points. Staff log I-Point Entries (boarder,
points, date, reason); at each local month's close a boarder whose I-Point
Balance is at least 5 redeems the largest tier at or below the balance
(5/10/15 → 1 day / 1 week / 1 calendar month Phone Confiscation), capped at 15,
at most one redemption per month, remainder carrying forward. Evaluations are
derived on read and materialise only when staff confirm — there is no
scheduler — and a Phone Confiscation is a separate entity from the lateness
Punishment, surfaced in its own I-Points view. The ledger is staff-editable —
Entries can be edited or removed and Adjustments made — with every change kept
in an I-Point Audit History; the I-Points tab is the editing hub, with a
quick-log action on the Boarder Profile.

## Considered Options

- **Automatic redemption vs staff-confirmed pending.** Rejected auto
  (scheduler/cron) — it would have the app act on a clock, which ADR 0001
  rejected for Punishment transitions. The pending redemption is computed as a
  display flag; confirming, releasing, and voiding are manual staff acts.
- **Extend `punishments` with a kind vs a separate entity.** Rejected extending
  — a lateness Punishment is task-shaped (copy N, deadline, submit) while a
  Phone Confiscation is duration-shaped (a fixed period). Forcing both through
  one state machine would distort both, and the "(normalized_name, month)"
  active-punishment invariant would not carry the second kind cleanly.
- **Locked tier vs recompute an open pending.** Locked at creation — an ignored
  pending does not silently escalate as more I-Points accrue; the balance drains
  one redemption per month.
- **Editable ledger with audit vs append-only with void.** Chose editable with
  an audit history — staff need to fix and remove Entries and rebalance freely,
  but the discipline must stay defensible, so every edit, removal, and
  adjustment is retained rather than overwritten (contrast voided Punishments,
  which stay append-only).
- **Redemption recompute vs point-in-time confirmation.** Chose point-in-time —
  editing Entries after a Redemption is confirmed never recomputes it; staff
  rebalance with an explicit I-Point Adjustment instead. Recompute would rewrite
  history.
- **Calendar-driven vs import-driven evaluation.** Calendar — I-Points are
  independent of Monthly Log imports, so evaluation follows elapsed calendar
  months, not saved reports.

## Consequences

- Applies ADR 0001's "the app never acts on a clock": pending redemption and
  due-for-release are computed display flags; confirm / release / void are
  manual.
- Exactly one open pending redemption per boarder; it is labelled with the month
  it first triggered and does not duplicate in later months.
- Back-dated I-Point Entries never reopen a closed month; they enter the running
  balance and are picked up at the next month's close.
- Edits and removals are allowed on Entries, Adjustments, Redemptions, and
  Confiscations; the I-Point Audit History retains the prior state and the
  change. A confirmed Redemption stays fixed, so entry edits affect only the
  running balance, never a past Confiscation.
- Confirming a pending freezes the boarder's display name and bed on the
  confiscation (matching the Punishment snapshot rationale); entries themselves
  store only the Match Key and resolve identity freshest-first.
- "1 month" means one calendar month. A Stacked Confiscation adds to a lateness
  Phone Hold rather than replacing it.
- Removed boarders accrue no new entries; existing balance and confiscations
  stay frozen, and their Match Key joins the All-Time List union.
