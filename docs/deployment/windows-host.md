# Windows host runbook

One designated, always-on Windows PC runs the app; every other staff PC just
opens a browser to it. The SQLite database and the Monthly Log Archive live on
**this PC's local disk** — one writer, no SQLite over SMB. The NAS is used only
as a backup target (see `backup-and-restore.md` and `nas-share.md`).

The app runs in **Docker Desktop**, started with the `run.cmd` launcher (which
wraps `run.ps1`). Gunicorn serves it inside the container, and the launcher runs
the idempotent `init-db` first, so a restart never needs the staff to do
anything. For the launcher commands (`up`, `down`, `backup`, `restore`), see
[README.md](../../README.md).

## Prerequisites

- Windows 10/11 on the designated host, always powered on during working hours.
- **Docker Desktop** installed and running.
- The repo checked out at a stable path, e.g. `C:\lateness-app`.
- A `SECRET_KEY` in the root `.env` file. `run.cmd` generates one on first start
  if it is missing; to create one by hand:

  ```powershell
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

## 1. Start the app

```powershell
cd C:\lateness-app
.\run.cmd up             # build and start on the office LAN; seeds the Master List
```

Put `namelist.csv` in the project root first so the first start seeds the Master
List, or start with `.\run.cmd up --no-seed` and import the Master List from the
Boarders tab afterwards.

## 2. Configure the host

Docker Compose reads the root `.env` file; the launcher writes `SECRET_KEY` into
it. Other settings can be set there too (all optional):

| Variable | Meaning | Default |
| --- | --- | --- |
| `SECRET_KEY` | Per-host session key. Generated on first start if unset. | — |
| `BIND_ADDR` | Host bind address. `127.0.0.1` hides the app from other PCs. | `0.0.0.0` (the LAN) |
| `APP_PORT` | Host port the app listens on. | `8000` |
| `BACKUP_KEEP` | Timestamped backups kept in `shared\backups`. | `7` |
| `LOG_LEVEL` | Application log level. | `INFO` |

`DB_PATH`, `NAMELIST_PATH`, and `LOG_ARCHIVE_DIR` are fixed by `compose.yaml` to
paths on the `lateness-data` Docker volume, so the live data survives restarts
and updates.

## 3. Give the host a stable address

Staff should bookmark a name, not an IP that can move.

1. On the router, reserve a DHCP lease for the host (or set a static IP).
2. Give the PC a friendly name, e.g. `lateness-host`.
3. Confirm from another PC: `http://lateness-host:8000/`.

## 4. Open the firewall port

The launcher creates the rule for you — it is port-aware and idempotent, and
asks for admin (UAC):

```powershell
.\run.cmd firewall
```

Verify from a client PC using the host's name **or IP** — prefer the IP if the
name does not resolve:

```powershell
Test-NetConnection <host-ip> -Port 8000
```

`TcpTestSucceeded : True` means the port is open. Ignore `PingSucceeded:` — the
rule allows TCP 8000, not ICMP. To undo the rule:

```powershell
Remove-NetFirewallRule -DisplayName "Lateness app*"
```

### Create the rule by hand

The host may sit on a **Domain** network (an AD-joined school PC, e.g. with a
`staff.dbs.local` suffix), a **Private** workgroup network, or **Public**. The
rule below uses `-Profile Any`, so it applies whatever the active profile is —
Domain included. **Do not reclassify a domain NIC.** To see which profile is
active:

```powershell
Get-NetConnectionProfile
```

Then allow the port (admin PowerShell). Use the app's `APP_PORT` (default
`8000`):

```powershell
New-NetFirewallRule -DisplayName "Lateness app (TCP 8000)" -Direction Inbound `
  -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any `
  -RemoteAddress LocalSubnet
```

`-RemoteAddress LocalSubnet` limits access to the office subnet, so you are not
exposing the app off-campus. If you changed `APP_PORT`, use that port in
`-LocalPort` and in the rule name. For a tighter rule, list the staff PCs' IPs
instead of `LocalSubnet` (e.g. `-RemoteAddress 192.168.13.50,192.168.13.51`);
hostnames and `ip:port` are not accepted.

> **Workgroup host only:** if Windows classifies the network as **Public** and
> you want to scope the rule to one profile, set it to Private and use
> `-Profile Private`:
>
> ```powershell
> Set-NetConnectionProfile -InterfaceAlias "<adapter>" -NetworkCategory Private
> ```
>
> Do not do this on a domain-joined PC.

## 5. Keep the host awake

Settings → System → Power & sleep → set **Sleep** to **Never** while plugged in.
Also disable "hybrid sleep"/fast startup if staff must reach the app at any hour,
and set Docker Desktop to start at login so a reboot recovers without a person
launching the app by hand.

## 6. Updating the app

```powershell
cd C:\lateness-app
.\run.cmd down
git pull
.\run.cmd up
```

Data lives in the `lateness-data` volume and is preserved across updates. After
updating, open the app and load one Monthly Report to confirm the new code is
live.

## 7. Backups

The host is the only writer, so backups run on the host and copy to the NAS. Use
`.\run.cmd backup` to write into `shared\backups`, then mirror that to the NAS.
See `backup-and-restore.md` for the mirror task and the restore drill, and
`nas-share.md` for preparing the share.

## Trust boundary

Office LAN only, plain HTTP, no authentication. Any device on the LAN can reach
and change the data. Do not port-forward this service or put it on a network
you do not control.
