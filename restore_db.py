"""Restore a backup onto the designated host: database plus Monthly Log Archive.

The counterpart to :mod:`backup_db`. It puts a ``lateness-<timestamp>`` backup
folder's database and archived Monthly Logs back where the app reads them, so a
lost or corrupted host can be rebuilt from the NAS. The application must be
stopped first: the live database is a file on local disk and this replaces it.
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

import defaults

_BACKUP_DB_NAME = "lateness_history.db"
_STALE_SUFFIXES = ("-journal", "-wal", "-shm")


def restore(
    source: "str | Path",
    *,
    db_path: str,
    log_archive_dir: str,
    force: bool = False,
) -> Path:
    """Restores one backup folder into the live database and archive.

    ``source`` is a folder produced by :func:`backup_db.backup`. Returns the
    folder. Refuses to overwrite an existing live database unless ``force`` is
    set, so a stray restore cannot silently discard the current data. The
    database is swapped in atomically and any stale SQLite journal is cleared,
    so a reader never sees a half-written file.
    """
    source = Path(source)
    backup_db = source / _BACKUP_DB_NAME
    if not backup_db.is_file():
        raise FileNotFoundError(f"backup database not found: {backup_db}")

    destination = Path(db_path)
    if destination.exists() and not force:
        raise FileExistsError(
            f"live database already exists: {destination} (pass --force to overwrite)"
        )

    # Copy the archive first: a failure there leaves the live database
    # untouched. The database swap is the last, atomic step.
    _copy_monthly_log_archive(source / "logs", Path(log_archive_dir))
    _replace_file(backup_db, destination)
    _clear_stale_journals(destination)
    return source


def _replace_file(source: Path, destination: Path) -> None:
    """Copies ``source`` over ``destination`` via a same-directory temp file.

    ``os.replace`` swaps the finished copy into place atomically, so the live
    path always points at a complete database.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.restore.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _clear_stale_journals(db_path: Path) -> None:
    """Removes rollback/WAL sidecars the replaced database might leave behind."""
    for suffix in _STALE_SUFFIXES:
        stale = Path(f"{db_path}{suffix}")
        if stale.exists():
            stale.unlink()


def _copy_monthly_log_archive(source: Path, destination: Path) -> None:
    """Copies the backup's archived CSVs back into the Monthly Log Archive.

    A missing archive in the backup is warned about, not fatal: a pre-archive
    backup is still a valid database restore. Only ``*.csv`` is copied, each
    via an atomic replace so a partial copy never overwrites a good file.
    """
    if not source.is_dir():
        print(f"warning: no Monthly Log Archive in backup: {source}", file=sys.stderr)
        return
    destination.mkdir(parents=True, exist_ok=True)
    for csv_file in sorted(source.glob("*.csv")):
        _replace_file(csv_file, destination / csv_file.name)


def main(argv: "list[str] | None" = None) -> int:
    """CLI entry point: restores one backup, honouring the host's env defaults."""
    parser = argparse.ArgumentParser(
        description="Restore the SQLite database and Monthly Log Archive from a backup."
    )
    parser.add_argument(
        "--from", dest="source", required=True, help="Backup folder to restore."
    )
    parser.add_argument(
        "--db", default=os.environ.get("DB_PATH", defaults.DEFAULT_DB_PATH)
    )
    parser.add_argument(
        "--logs",
        default=os.environ.get("LOG_ARCHIVE_DIR", defaults.DEFAULT_LOG_ARCHIVE_DIR),
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing live database."
    )
    args = parser.parse_args(argv)
    folder = restore(
        args.source,
        db_path=args.db,
        log_archive_dir=args.logs,
        force=args.force,
    )
    print(f"Restored from {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
