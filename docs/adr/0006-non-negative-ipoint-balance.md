# Non-negative I-Point Balance

The I-Point Balance is derived, never stored: every I-Point Entry plus every
I-Point Adjustment minus every confirmed Redemption. Staff may subtract an
Adjustment, edit or remove an Entry, and void a Redemption, so the arithmetic
can naturally fall below zero — an Entry of +5 offset by an Adjustment of −5
becomes −5 the moment that Entry is removed. A negative Balance is meaningless:
I-Points are outstanding behaviour owed, and a Redemption can never exceed what
is there to redeem. The derived formula alone does not express that floor.

## Decision

Enforce zero as a floor on the derived I-Point Balance at every write path that
can move it: logging, editing, and removing an Entry; adding, editing, and
removing an Adjustment; and confirming and voiding a Redemption. A write that
would leave the Balance below zero is rejected by the I-Points lifecycle before
it commits. A subtractive Adjustment is further capped at the current Balance.

## Considered Options

- **Validate Adjustments only.** Rejected — editing or removing an Entry, or
  voiding a Redemption, can still drive the Balance negative, so a partial guard
  only slows the same failure.
- **Clamp at display time.** Rejected — it hides a genuinely invalid ledger
  behind the UI and leaves the stored figure inconsistent with the domain.
- **Allow negative Balances.** Rejected — meaningless in the domain; a Boarder
  cannot owe a negative number of I-Points, and pending-Redemption tiers have no
  meaning below zero.

## Consequences

- The floor is a cross-cutting invariant owned by the I-Points lifecycle, not a
  condition on any single write.
- Staff cannot remove or shorten an Entry if an Adjustment has already offset it
  and no other Entry covers the difference; they must remove or edit the
  Adjustment first. This is the accepted cost of a Balance that always reads as
  outstanding I-Points.
- Redemption confirmation already debits at most the Balance, and voiding adds
  the points back, so neither can break the floor on its own.
