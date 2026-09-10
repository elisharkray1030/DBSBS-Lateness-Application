"""Built-in configuration defaults shared by the app and host tooling.

Environment variables override these and inline factory config overrides the
environment; keeping the literals here means the app and ``backup_db.py``
cannot drift apart on what "unset" means.
"""

DEFAULT_DB_PATH = "lateness_history.db"
DEFAULT_NAMELIST_PATH = "namelist.csv"
DEFAULT_LOG_ARCHIVE_DIR = "data/logs"
