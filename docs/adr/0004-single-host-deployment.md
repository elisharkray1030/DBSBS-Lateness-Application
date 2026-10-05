# Single-host deployment with local SQLite

One designated, always-on host runs the application. The SQLite database and
the Monthly Log Archive live on **that host's local disk**; staff on other PCs
reach the app over the office LAN through a browser. The app runs in Docker
Desktop (`run.cmd`/`run.sh`), served by gunicorn inside the container. The
NAS is a **backup target only** — it never holds the live database.

## Context

The app is used by a small team with moderate, occasionally concurrent edits
(Monthly Log Imports plus day-to-day Boarder and Punishment changes). The
original plan for issue #1 had every staff PC run the app against one SQLite
file on an SMB share. SQLite's own guidance is that this is the wrong shape:
its database engine must run on the same machine as the database file, and
network-filesystem locking is unreliable enough to corrupt the file. The prior
hardening (busy timeout, read-only readers, no WAL, bounded retry) reduces the
symptoms but cannot fix locking that the underlying SMB server gets wrong.

A single host serialises every write through one process, so SQLite's
single-writer model is satisfied by construction and the file never travels
across the network.

## Considered Options

- **Per-PC apps against NAS-hosted SQLite (rejected).** Puts the high-traffic
  database-engine-to-file link on the network and lets multiple machines write
  one SQLite file over SMB, the exact case SQLite documents as corruption-prone.
- **Client/server database (PostgreSQL) (rejected for now).** Removes the
  write-concurrency ceiling and tolerates a network boundary, but adds a
  service to run, secure, and administer for a single boarding house. Revisit
  if write concurrency or data volume outgrows one host.
- **Host location: NAS vs dedicated PC.** The NAS lacks compute to run the app,
  so the host is a designated always-on staff PC (a dedicated mini-PC would be
  equivalent and is preferred if one becomes available).
- **Windows entrypoint: Docker Desktop (chosen).** The staff host runs the app
  in Docker Desktop via the `run.cmd`/`run.sh` launcher. It costs the WSL2 and
  licensing overhead of Docker Desktop on a staff PC, but gives one uniform,
  reproducible deployment across Windows, Linux, and macOS, with backups and
  restores handled by the same launcher. A native waitress/NSSM entrypoint
  (`serve.py`) was considered and has been retired — the host has always been
  deployed this way, so the fallback was dead weight.
