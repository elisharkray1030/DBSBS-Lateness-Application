# Backup and restore

Backups run on the designated host (the only writer) and copy to the NAS. The
NAS is storage only; it never holds the live database.

## What each backup contains

Every run creates a timestamped folder `lateness-YYYYMMDD-HHMMSS` under the
destination, holding:

- `lateness_history.db` — a consistent copy taken with SQLite's **online backup
  API**. A raw file copy of a live database can capture a half-written page and
  corrupt the backup; the backup API copies under the database lock instead, so
  it is safe while staff use the app.
- `logs/` — every imported Monthly Log CSV. These are the source data: the
  Monthly Reports can be rebuilt by re-importing them.
- `namelist.csv` — the Master List, when present.

Punishments live **only** in the database, not in the CSVs. Protect the database
copies accordingly.

## Run it manually

```powershell
cd C:\lateness-app
.\.venv\Scripts\python.exe backup_db.py --dest "\\NAS\share\lateness-backups"
```

Defaults come from the host environment: `DB_PATH`, `LOG_ARCHIVE_DIR`,
`NAMELIST_PATH`. `--keep` sets how many timestamped folders to retain (default
7); older folders are deleted after a successful run. The script refuses to run
if the database is missing.

## Schedule it (Task Scheduler)

Use a **UNC path** for the destination, not a mapped drive letter: a task that
runs whether or not the user is logged on has no drive mappings. Replace
`NAS`, `share`, and the paths with your own.

```powershell
schtasks /Create /TN "Lateness backup" /SC DAILY /ST 21:00 /RU SYSTEM ^
  /TR "\"C:\lateness-app\.venv\Scripts\python.exe\" \"C:\lateness-app\backup_db.py\" --dest \"\\NAS\share\lateness-backups\""
```

SYSTEM must have write access to the share; grant it on the NAS, or run the
task as a dedicated account that does. Confirm the first folder appears on the
NAS before trusting the schedule, and check it again after a reboot.

Run it again when the machine starts, so a host rebooted after hours is
snapshotted without waiting for the next evening:

```powershell
schtasks /Create /TN "Lateness backup (startup)" /SC ONSTART /RU SYSTEM ^
  /TR "\"C:\lateness-app\.venv\Scripts\python.exe\" \"C:\lateness-app\backup_db.py\" --dest \"\\NAS\share\lateness-backups\""
```

## Restore (database)

1. Stop the service: `nssm stop LatenessApp`.
2. Copy the chosen `lateness_history.db` from the backup folder over the live
   database file (`DB_PATH`, default `C:\lateness-app\lateness_history.db`).
3. Copy the backup's `logs\` folder over `LOG_ARCHIVE_DIR` so future backups
   keep the source CSVs.
4. Start the service: `nssm start LatenessApp`. Open the app and confirm a known
   month and its totals.

## Rebuild (database lost, CSVs kept)

1. Stop the service and start from a fresh database (`serve.py` re-runs
   `init-db`, which seeds the Master List from `namelist.csv`).
2. Re-import each `logs\<YYYY-MM>.csv` on the Import page, using the month in
   the filename.
3. Punishments are not recoverable this way and must be re-issued.

## Restore drill

The repeatable half of a restore is covered by `tests/test_backup_db.py`, which
reopens a backup database through the storage seam and confirms the rows are
present. Run the full manual drill on the real host once after setup and note
the date in your change log:

1. Take a backup.
2. Move the live database aside, restore the backup database, start the app, and
   confirm a known month and its totals.
3. Restore the original database.
