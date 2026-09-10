"""Backups for the designated host: SQLite online copy plus archived Monthly Logs.

The designated host is the only writer, so backups run there and copy to the
NAS. The database copy uses SQLite's online backup API (safe while the app is
running), never a raw file copy that could capture a half-written page. The
Monthly Log Archive holds each imported Monthly Log and a matching
``namelist-<month>.csv`` Master List snapshot, so the whole archive is copied.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

import defaults


def backup(
    dest: "str | Path",
    *,
    db_path: str,
    log_archive_dir: str,
    keep: int,
    timestamp: "str | None" = None,
) -> Path:
    """Runs one backup into a fresh timestamped folder under ``dest``.

    Returns the folder. ``timestamp`` is injectable so callers (and tests)
    can name the folder deterministically. A failure part-way through removes
    the folder, so only a complete backup is ever left behind.
    """
    if keep < 1:
        raise ValueError("keep must be at least 1")
    if timestamp is None:
        # Microsecond precision keeps two runs in the same second from
        # colliding on the folder name.
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S%f")
    if not Path(db_path).is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    folder = dest / f"lateness-{timestamp}"
    folder.mkdir()
    try:
        with closing(sqlite3.connect(db_path)) as src, closing(
            sqlite3.connect(folder / "lateness_history.db")
        ) as dst:
            src.backup(dst)
        _copy_monthly_log_archive(log_archive_dir, folder)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    _prune(dest, keep)
    return folder


def _copy_monthly_log_archive(log_archive_dir: str, folder: Path) -> None:
    """Copies the Monthly Log Archive CSVs into ``folder/logs``.

    The archive holds each imported Monthly Log plus its ``namelist-<month>.csv``
    Master List snapshot. A missing directory is warned about, not fatal: a
    fresh deployment has no archive until the first Import. Only ``*.csv`` is
    copied.
    """
    logs_src = Path(log_archive_dir)
    if not logs_src.is_dir():
        print(
            f"warning: Monthly Log Archive not found: {log_archive_dir}",
            file=sys.stderr,
        )
        return
    logs_dst = folder / "logs"
    logs_dst.mkdir()
    for csv_file in sorted(logs_src.glob("*.csv")):
        shutil.copy2(csv_file, logs_dst / csv_file.name)


def _prune(dest: Path, keep: int) -> None:
    """Deletes all but the newest ``keep`` backup folders under ``dest``."""
    folders = sorted(
        (p for p in dest.iterdir() if p.is_dir() and p.name.startswith("lateness-")),
        key=lambda p: p.name,
    )
    for stale in folders[:-keep]:
        shutil.rmtree(stale)


def main(argv: "list[str] | None" = None) -> int:
    """CLI entry point: runs one backup, honouring the host's env defaults."""
    parser = argparse.ArgumentParser(
        description="Back up the SQLite database and Monthly Log Archive."
    )
    parser.add_argument(
        "--dest", required=True, help="Destination directory (the NAS share)."
    )
    parser.add_argument(
        "--db", default=os.environ.get("DB_PATH", defaults.DEFAULT_DB_PATH)
    )
    parser.add_argument(
        "--logs",
        default=os.environ.get(
            "LOG_ARCHIVE_DIR", defaults.DEFAULT_LOG_ARCHIVE_DIR
        ),
    )
    parser.add_argument(
        "--keep", type=int, default=7, help="Timestamped backups to keep."
    )
    args = parser.parse_args(argv)
    folder = backup(
        args.dest,
        db_path=args.db,
        log_archive_dir=args.logs,
        keep=args.keep,
    )
    print(f"Backed up to {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
