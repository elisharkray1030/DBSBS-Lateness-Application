# Persisted pending Confiscation, materialised on read

A pending Redemption is a persisted `confiscations` row with `status = 'pending'`,
not a value derived afresh on every read. This supersedes ADR 0005's "pending is
derived, never stored": the tier must stay **locked at creation** (story 16) and
an ignored pending must not duplicate or escalate in a later month, and those are
guarantees about a stored record, not a recomputation.

The tension is that the app's reads open the read-only connection (ADR 0004's
shared-NAS rationale, spec story 46), so materialising the row on
`GET /ipoints` is a write on a read path. The decision resolves it by keeping
the common case read-only: the GET evaluates the ledger on a read-only
connection, and only when that evaluation finds a Boarder whose month-close
pending is not yet stored does it open a read-write connection under the shared
mutation-retry seam, idempotently upsert the pending rows, and re-read once.
Reads stay read-only except the rare, bounded materialisation.

Confirming a pending moves it to `active`, freezes the Boarder's display name
and bed, and **debits** the Balance by `points_redeemed`. Voiding moves it to
`voided`. A pending Redemption reserves points but does **not** debit them
itself: the Balance subtracts only confirmed (`active`/`released`) Confiscations,
so "confirm refused when it would take the Balance below zero" (ADR 0006) stays a
reachable path when the Balance falls after materialisation.

## Considered Options

- **Materialise on read when needed.** Chosen — keeps stored, locked pending rows
  while preserving read-only reads in the ordinary case. The exceptional write is
  bounded to one upsert per Boarder per closed month.
- **Materialise on write paths only.** Rejected — a newly closed month would show
  no pending until the next staff write, so the pending list could be stale for
  an unbounded time, failing the "appears at a month's close" acceptance
  criterion.
- **Keep pending derived on read.** Rejected — the locked tier and the
  no-duplicate guarantee would be recomputed rather than stored; a back-dated
  Entry could shift the derived tier, which story 16 forbids.

## Consequences

- `confiscations.status` is `pending | active | released | voided`. The partial
  unique index that kept one active Confiscation per Boarder now covers
  `pending` and `active`, so at most one open Confiscation exists.
- The Balance subtracts `points_redeemed` for `active` and `released` rows only;
  pending is a reservation, and a voided row (including a confirmed one voided in
  a later slice) adds its points back.
- The month-close evaluator is a pure function with an injected `today`; the
  materialisation it feeds is idempotent, so repeated reads cannot duplicate a
  pending.
