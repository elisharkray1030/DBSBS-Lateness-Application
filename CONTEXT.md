# Lateness Dashboard

Tracks lateness disciplinary records at DBS Boarding School. Monthly lateness logs are imported and aggregated into per-month reports, which staff search and review.

## Language

**Boarder**:
A pupil at DBS Boarding School who lives in the boarding house and is assigned a bed.
_Avoid_: student, pupil, resident

**Bed**:
The boarder's assigned bed, designated by a unique string (e.g., "601A"). Each bed is assigned to exactly one boarder; each boarder has exactly one bed.
_Avoid_: room, dorm

**Master List**:
The editable roster of Boarders staff maintain in the app; Monthly Log names match against it during an Import.
_Avoid_: roster, namelist, pupil list

**Match Key**:
A Boarder's punctuation-insensitive identity: the uppercased name with every run of punctuation and whitespace collapsed to a single space, so "SURNAME, Given" and "SURNAME Given" share one key. Used to match log rows, join stored records, and detect duplicates.
_Avoid_: normalized name, key, login

**Monthly Log**:
The CSV of lateness incidents for one month, imported to create or refresh that month's report.
_Avoid_: CSV, file, timesheet

**Monthly Log Archive**:
The on-disk copy of each imported Monthly Log, filed by month under `LOG_ARCHIVE_DIR` as `<YYYY-MM>.csv`, alongside a per-month Master List snapshot (`namelist-<YYYY-MM>.csv`) of the roster the report was built against. It is the source data that can rebuild the Monthly Reports; distinct from the Report Archive, which holds the reports themselves.
_Avoid_: source CSVs, logs, backup

**Expected Non-Boarder**:
A Monthly Log name known never to match a Boarder on the Master List: staff badge names carry the "M." prefix, guests check out numbered GUEST cards, houseparent-family cards read "[RTnn] HOUSEPARENT'S FAMILY", and a fixed set of shared/system cards (e.g. "BA1 DY", "STEPS GATE GUARD") belongs to the house. Hidden from the saved-Import count so a genuinely unknown name stands out; raw diagnostics keep every name.
_Avoid_: staff name, system card, ignored name

**Monthly Report**:
The saved aggregate for one month — each boarder's frequency, minutes late, and total points.
_Avoid_: historical report, record

**Report Archive**:
The collection of saved monthly reports. The UI tab deliberately reads "View Reports in Database" because staff use "the database" colloquially; the domain concept is the archive.
_Avoid_: database, historical reports

**All-Time List**:
Every boarder ever recorded: the Master List unioned with the distinct Match Keys found in Boarder History and Punishments, derived live at request time and never stored. Each entry's Current/Former status is likewise derived — Current when the key sits on the Master List, Former when it survives only in frozen snapshots. Identity fields resolve freshest-first: the current Master List entry wins; otherwise the latest snapshot (latest month, tie-broken by latest import time).
_Avoid_: historic roster, alumni list, everyone-ever

**Boarder History**:
The set of a boarder's lateness entries across all imported months, surfaced by search.
_Avoid_: search history, records

**Boarder Profile**:
The per-boarder page addressed by URL-encoded Match Key: identity resolved freshest-first (with a Former badge off the Master List), lifetime summary figures with best and worst month, the month-by-month Boarder History, and — as it exists — the Punishment timeline. Reached uniformly for current and Removed boarders, so name variants collapse to one page.
_Avoid_: student page, person record, individual view

**House Dashboard**:
The Statistics tab's home: house-wide lateness trend across the Report Archive, plus top-N boarders, monthly Points distribution, and the repeat-offender watchlist. Every figure derives live from stored data on each visit — nothing cached or stale.
_Avoid_: analytics page, stats screen

**Import**:
Loading a monthly log to create or refresh that month's report.
_Avoid_: upload, generate

**Remove**:
Drop a boarder from the master list. The boarder's Boarder History and Punishments persist as frozen snapshots (per ADR 0001) and are not affected. Future Monthly Log imports will no longer match a removed boarder.
_Avoid_: delete, archive, deactivate

## Discipline

**Points**:
A boarder's monthly lateness score, equal to the number of times the boarder must copy down the lateness rules as punishment.
_Avoid_: score, tally

**Punishment**:
The disciplinary task assigned to a boarder for a month — copying the lateness rules a number of times equal to that month's points, to be submitted by a staff-set deadline. Assigned manually after staff review the month's report; the points figure is frozen at assignment and is not changed by a later re-import of the month.
_Avoid_: sanction, penalty, consequence

**Deadline**:
The staff-set date by which a punishment must be submitted. Set per assignment batch when staff issue punishments to boarders.
_Avoid_: due date, cutoff

**Phone Hold**:
The consequence when a punishment passes its deadline unsubmitted: the boarder's phone is held until the punishment is submitted, then released. Tracked as a status on the punishment, not as a separate phone registry.
_Avoid_: confiscation, phone confiscation

**Irregularity Points (I-Points)**:
A persisting disciplinary score for repeated inappropriate behaviour, separate from a boarder's monthly lateness Points. Unlike lateness Points, I-Points accumulate across months until redeemed.
_Avoid_: IP, infraction points, demerits

**I-Point Entry**:
One logged occasion on which a boarder was given I-Points, carrying the points, the date, and a stated reason. Entries are freely editable and removable; every change is kept in the I-Point Audit History.
_Avoid_: award, incident, infraction, charge

**I-Point Adjustment**:
A manual staff correction to a boarder's I-Point Balance that adds or subtracts points without being tied to a specific Entry. Used to rebalance instead of rewriting history, and a subtraction may never take the Balance below zero.
_Avoid_: correction, override, manual entry

**I-Point Balance**:
A boarder's outstanding I-Points: every I-Point Entry plus every I-Point Adjustment minus every confirmed Redemption. It carries from month to month and is never negative — no Entry, Adjustment, or Redemption change may take it below zero.
_Avoid_: total, score

**I-Point Audit History**:
The retained record of every change to a boarder's I-Points — edited or removed Entries, Adjustments, and edited or voided Redemptions and Confiscations — so staff can see what changed, when, and to what.
_Avoid_: log, changelog, activity feed

**Redemption**:
The conversion of an I-Point Balance into a Phone Confiscation at a month's close: the largest tier at or below the balance (5, 10, or 15) is deducted and becomes the confiscation, and the remainder carries forward. At most one Redemption per boarder per month; a Redemption begins as pending and only takes effect when staff confirm it, and staff may edit or void one.
_Avoid_: deduction, cash-in, spend

**Phone Confiscation**:
The I-Point consequence: the boarder's phone is taken for a fixed period set by the redeemed tier — 1 day for 5, 1 week for 10, 1 calendar month for 15. Separate from a lateness Phone Hold; staff may edit, void, or remove one, and mark it released once its period has elapsed (or early at their discretion). The app never releases one on its own.
_Avoid_: phone hold, confiscation

**Stacked**:
Describes a Phone Confiscation and a lateness Phone Hold applying to the same boarder at once. The two are additive, not interchangeable: the phone is released only once both gates clear — the lateness Punishment is submitted and the Confiscation is released (after its period has elapsed, or early at staff discretion). The app never releases on its own.
_Avoid_: overlap, concurrent, combined
