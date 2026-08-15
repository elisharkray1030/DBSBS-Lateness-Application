# Lateness Dashboard

Tracks lateness disciplinary records at DBS Boarding School. Monthly lateness logs are imported and aggregated into per-month reports, which staff search and review.

## Language

**Boarder**:
A pupil at DBS Boarding School who lives in the boarding house and is assigned a bed.
_Avoid_: student, pupil, resident

**Bed**:
The boarder's assigned room designation (e.g., "601A").
_Avoid_: room, dorm

**Monthly Log**:
The CSV of lateness incidents for one month, imported to create or refresh that month's report.
_Avoid_: CSV, file, timesheet

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
