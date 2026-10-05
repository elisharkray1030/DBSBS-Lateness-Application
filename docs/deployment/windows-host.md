# Windows host runbook

One designated, always-on Windows PC runs the app; every other staff PC just
opens a browser to it. The SQLite database and the Monthly Log Archive live on
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

Set these as **machine** environment variables (admin PowerShell, `setx /M`)
so the service account inherits them. They can also be entered on NSSM's
Environment tab in step 7, one `KEY=value` per line.

| Variable | Required | Meaning |
| --- | --- | --- |
| `SECRET_KEY` | yes | Per-host session key. No default; startup aborts without one. |
| `DB_PATH` | no | Database file. Defaults to `lateness_history.db` under the app directory. |
| `NAMELIST_PATH` | no | Seed Master List. Defaults to `namelist.csv` under the app directory. |
| `LOG_ARCHIVE_DIR` | no | Where imported Monthly Log CSVs are kept. Defaults to `data\logs` under the app directory. |
| `PORT` | no | Listen port. Defaults to `8000`. |

```powershell
# /M sets a machine variable, which the service inherits.
setx /M SECRET_KEY "<the generated secret>"
```

> The repo's root `.env` file is read by **Docker Compose only**. The native
> Windows host has no `.env` loader: use real environment variables.

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

The host may sit on a **Domain** network (an AD-joined school PC, e.g. with a
`staff.dbs.local` suffix), a **Private** workgroup network, or **Public**. The
rule below uses `-Profile Any`, so it applies whatever the active profile is —
Domain included. **Do not reclassify a domain NIC.** To see which profile is
active:

```powershell
Get-NetConnectionProfile
```

Then allow the port (admin PowerShell). Use the app's `PORT` (default `8000`):

```powershell
New-NetFirewallRule -DisplayName "Lateness app (TCP 8000)" -Direction Inbound `
  -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any `
  -RemoteAddress LocalSubnet
```

`-RemoteAddress LocalSubnet` limits access to the office subnet, so you are not
exposing the app off-campus. If you changed `PORT`, use that port in `-LocalPort`
and in the rule name. For a tighter rule, list the staff PCs' IPs instead of
`LocalSubnet` (e.g. `-RemoteAddress 192.168.13.50,192.168.13.51`); hostnames and
`ip:port` are not accepted.

> **Workgroup host only:** if Windows classifies the network as **Public** and
> you want to scope the rule to one profile, set it to Private and use
> `-Profile Private`:
>
> ```powershell
> Set-NetConnectionProfile -InterfaceAlias "<adapter>" -NetworkCategory Private
> ```
>
> Do not do this on a domain-joined PC.

### Use the launcher instead (Docker path)

On the Docker path the `run.ps1` launcher creates the same rule for you — it is
port-aware and idempotent, and asks for admin (UAC):

```powershell
.\run.cmd firewall
```

Verify from a client PC using the host's name **or IP** — prefer the IP if the
name does not resolve:

```powershell
Test-NetConnection <host-ip> -Port 8000
```

`TcpTestSucceeded : True` means the port is open. Ignore `PingSucceeded:` — this
rule allows TCP 8000, not ICMP. To undo the rule:

```powershell
Remove-NetFirewallRule -DisplayName "Lateness app*"
```

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
nssm set LatenessApp Start SERVICE_AUTO_START
nssm set LatenessApp AppExit Default Restart
nssm start LatenessApp
```

The service inherits the machine environment variables from step 2 (`SECRET_KEY`,
and optionally `DB_PATH`, `NAMELIST_PATH`, `LOG_ARCHIVE_DIR`, `PORT`). To set
them per-service instead, use the NSSM GUI **Environment** tab — one `KEY=value`
per line — rather than a space-separated command line.

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
