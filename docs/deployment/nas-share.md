# NAS share runbook

The NAS holds **backups only**; the live database and Monthly Log archive stay
on the designated host's local disk. This runbook prepares the share the host
writes backups into.

## 1. Create the share

Create a folder and share it, e.g. `\\NAS\lateness-backups` (the exact path
depends on the NAS vendor: Synology "Shared Folder", QNAP "Shared Folder",
Windows "New Share"). The host's `backup_db.py --dest` points at this UNC path.

## 2. Grant the host write access

The backup runs from the host under a specific account:

- If the scheduled task runs as **SYSTEM**, SYSTEM needs write access to the
  share. Share permissions and filesystem permissions must both allow it.
- Simpler to reason about: create a dedicated local or NAS account
  (e.g. `lateness-backup`) with write access and run the task as that account.

Read access is enough for restoring; grant write for running backups.

## 3. Do not map a drive letter

Use the **UNC path** everywhere (`\\NAS\lateness-backups`). A mapped drive
(`Z:`) is per-user and is absent for a task that runs whether or not the user
is logged on, which is exactly how the backup task should run.

## 4. Verify the first backup

From the host:

```powershell
cd C:\lateness-app
.\.venv\Scripts\python.exe backup_db.py --dest "\\NAS\share\lateness-backups"
```

Confirm a `lateness-<timestamp>\` folder appears containing
`lateness_history.db`, `logs\`, and (when present) `namelist.csv`. Then check
again after the scheduled task has fired at least once.

## 5. Retention and capacity

`backup_db.py` keeps the newest 7 timestamped folders by default (`--keep`).
The database is a few megabytes and the CSVs are tiny, so seven copies are
negligible; still, monitor free space on the share.

If the NAS supports **snapshots**, schedule them as a second line of defence
against a bad write or accidental deletion. They complement the copy-based
backups; they do not replace them.

## 6. Restore

See [backup-and-restore.md](backup-and-restore.md) for the database restore,
the CSV rebuild path, and the restore drill.

## Trust boundary

This share lives on the office LAN and only the host needs write access. Keep
it scoped to the LAN; do not expose it off-campus.
