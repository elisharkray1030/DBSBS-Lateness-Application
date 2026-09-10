# Windows host runbook

One designated, always-on Windows PC runs the app; every other staff PC just
opens a browser to it. The SQLite database and the Monthly Log archive live on
**this PC's local disk** — one writer, no SQLite over SMB. The NAS is used only
as a backup target (see `backup-and-restore.md` and `nas-share.md`).

The app is served with **waitress** (`serve.py`), not `flask run`, because the
dev server is single-threaded and not meant for shared use. `serve.py` runs the
idempotent `init-db` first, so a restart never needs the staff to do anything.

## Prerequisites

- Windows 10/11 on the designated host, always powered on during working hours.
- Python 3.11 or 3.12 (3.12 recommended) installed for all users.
- The repo checked out at a stable path, e.g. `C:\lateness-app`.
- `SECRET_KEY` for this host — generate one with:
  `python -c "import secrets; print(secrets.token_hex(32))"`

## 1. Install dependencies

```powershell
cd C:\lateness-app
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 2. Configure the host

Set these for the account that will run the app (System Properties → Environment
Variables, or `setx`). They must be visible to the service account from step 7.

| Variable | Required | Meaning |
| --- | --- | --- |
| `SECRET_KEY` | yes | Per-host session key. No default; startup aborts without one. |
| `LOG_ARCHIVE_DIR` | no | Where imported Monthly Log CSVs are kept. Defaults to `data\logs` under the app directory. |
| `PORT` | no | Listen port. Defaults to `8000`. |

```powershell
setx SECRET_KEY "<the generated secret>"
```

> The repo's root `.env` file is read by **Docker Compose only**. The native
> Windows host has no `.env` loader: use real environment variables (`setx`,
> or NSSM's `AppEnvironmentExtra` in step 7).

## 3. Smoke test before installing a service

```powershell
cd C:\lateness-app
.\.venv\Scripts\Activate.ps1
$env:SECRET_KEY = "<the generated secret>"   # until setx takes effect in a new shell
python serve.py
```

Open `http://localhost:8000/`. Import one Monthly Log to confirm the archive
appears (default `C:\lateness-app\data\logs\<YYYY-MM>.csv`). Then stop it with
`Ctrl+C`.

## 4. Give the host a stable address

Staff should bookmark a name, not an IP that can move.

1. On the router, reserve a DHCP lease for the host (or set a static IP).
2. Give the PC a friendly name, e.g. `lateness-host`.
3. Confirm from another PC: `http://lateness-host:8000/`.

## 5. Open the firewall port

```powershell
New-NetFirewallRule -DisplayName "Lateness app" -Direction Inbound `
  -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
```

Keep it scoped to the **Private** (office) profile. Do not expose the app
off-campus.

## 6. Keep the host awake

Settings → System → Power & sleep → set **Sleep** to **Never** while plugged in.
Also disable "hybrid sleep"/fast startup if staff must reach the app at any hour,
and set an auto-logon or a boot-time service (step 7) so a reboot recovers
without a person logging in.

## 7. Run it as a service (NSSM)

[NSSM](https://nssm.cc/) wraps `python serve.py` as a real Windows service: it
starts at boot, survives logoff, and restarts on crash.

```powershell
# Edit the paths to match this host.
nssm install LatenessApp "C:\lateness-app\.venv\Scripts\python.exe" "C:\lateness-app\serve.py"
nssm set LatenessApp AppDirectory "C:\lateness-app"
nssm set LatenessApp AppEnvironmentExtra SECRET_KEY=<the generated secret> PORT=8000
nssm set LatenessApp Start SERVICE_AUTO_START
nssm set LatenessApp AppExit Default Restart
nssm start LatenessApp
```

Use a **UNC path** (`\\NAS\share\...`) anywhere the service must reach the NAS;
a mapped drive letter is per-user and absent for a service. The backup task in
`backup-and-restore.md` already uses UNC.

Check it is up: `nssm status LatenessApp` and `http://lateness-host:8000/`.

## 8. Updating the app

```powershell
cd C:\lateness-app
git pull
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
nssm restart LatenessApp
```

`serve.py` re-runs the idempotent `init-db` on start, so no manual database step
is needed. Afterwards open the app and load one Monthly Report to confirm the
new code is live.

## 9. Backups

The host is the only writer, so backups run on the host and copy to the NAS.
See `backup-and-restore.md` for the script, the Task Scheduler registration,
and the restore drill, and `nas-share.md` for preparing the share.

## Trust boundary

Office LAN only, plain HTTP, no authentication. Any device on the LAN can reach
and change the data. Do not port-forward this service or put it on a network
you do not control.
