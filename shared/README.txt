Lateness App - shared folder
============================

This folder is the bridge between the app (which runs inside Docker) and
your Windows desktop. You do not need to touch it for day-to-day work:
adding Monthly Logs and boarders happens in the app itself.

backups/
    Every time you run "Backup" (or `run.cmd backup`), the app writes a
    timestamped copy of its database and archived Monthly Logs here, named
    lateness-<date>-<time>. Copy these to the NAS or a USB drive for
    safekeeping. The app keeps the most recent 7 (set BACKUP_KEEP to change).

restore/
    To restore, copy a lateness-* backup folder from backups/ into this
    folder, then run "Restore" (or `run.cmd restore`). The app must be
    stopped; the launcher handles that.

The live database itself is NOT here. It lives in a Docker volume
(lateness-data) so SQLite sees a normal local disk. That keeps it fast and
safe; this folder is only for backups and restores.
