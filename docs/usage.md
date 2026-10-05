# Using the application

The app imports Monthly Log CSVs, matches them against the Master List, and stores monthly
reports you can review, search, download, or delete. This page covers the day-to-day tabs.
For the data model behind them, see [architecture.md](architecture.md) and
[CONTEXT.md](../CONTEXT.md).

## Reports

1. Open the **Reports** tab.
2. Import a Monthly Log CSV and give it a month label such as `2026-03`.
3. Save the report.
4. Use the month cards to view, download, or delete a saved report.
5. Open a month and click **Assign Punishments** to issue punishments to the boarders who
   were late; set a deadline and track them afterwards.

Each Import files the Monthly Log and a Master List snapshot, so a month report can be
rebuilt from the archive.

## Finding a boarder

Use **Find a Boarder** to search any boarder's all-time history, their Points trend chart,
their punishments, and — where relevant — their I-Point Balance.

## Punishments

The **Punishments** tab tracks each assigned punishment through its statuses: assigned,
overdue, phone held, submitted, and voided. A phone is held when a punishment passes its
deadline unsubmitted, and released once the punishment is submitted.

## I-Points

The **I-Points** tab manages Irregularity Points, separate from a boarder's monthly
lateness Points:

- Log an Entry (points, date, reason) for a boarder.
- Edit or remove an Entry; every change is kept in the boarder's Audit History.
- At a month's close, the largest tier at or below the balance (5, 10, or 15) becomes a
  pending **Redemption**. Confirm it to turn it into a Phone Confiscation, or void it.
- Manage Confiscations (release, void, edit, remove), filtered by status. A Confiscation
  that coincides with a lateness Phone Hold is shown as **Stacked** — the phone is released
  only once both clear.

## Statistics

The **Statistics** tab is the house dashboard: the lateness trend, the top boarders, the
repeat-offender watchlist, and the monthly Points distribution. Every figure is derived
live from stored data on each visit.

## Boarders (Master List)

The **Boarders** tab is the Master List: view, add, edit, or remove boarders, import a CSV
Master List, or download the current one. Removing a boarder keeps their history and
punishments as frozen snapshots; future Imports simply stop matching them. A successful
import confirms with the boarder count; an empty or unreadable CSV is refused and leaves
the list untouched. **Clear Master List** empties the whole list behind a confirmation —
again keeping history and punishments — and is the deliberate way to start over.
