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

**Expected Non-Boarder**:
A Monthly Log name known never to match a Boarder on the Master List: staff badge names carry the "M." prefix, guests check out numbered GUEST cards, houseparent-family cards read "[RTnn] HOUSEPARENT'S FAMILY", and a fixed set of shared/system cards (e.g. "BA1 DY", "STEPS GATE GUARD") belongs to the house. Hidden from the saved-Import count so a genuinely unknown name stands out; raw diagnostics keep every name.
_Avoid_: staff name, system card, ignored name

**Monthly Report**:
The saved aggregate for one month — each boarder's frequency, minutes late, and total points.
_Avoid_: historical report, record

**Report Archive**:
The collection of saved monthly reports. The UI tab deliberately reads "View Reports in Database" because staff use "the database" colloquially; the domain concept is the archive.
_Avoid_: database, historical reports

**Boarder History**:
The set of a boarder's lateness entries across all imported months, surfaced by search.
_Avoid_: search history, records

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
