#!/usr/bin/env bash
# One-command launcher for the Lateness app in Docker (Linux / macOS / WSL).
#
#   ./run.sh                 # interactive menu
#   ./run.sh up              # start (office LAN, 0.0.0.0)
#   ./run.sh up --local      # start bound to 127.0.0.1 only
#   ./run.sh up --no-seed    # start without seeding the Master List from namelist.csv
#   ./run.sh backup          # write a backup into ./shared/backups
#   ./run.sh restore --backup lateness-20260910-120000
#   ./run.sh seed            # load deterministic demo data (destructive)
#   ./run.sh reset           # stop and delete all live data
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BASE_COMPOSE=(-f "$SCRIPT_DIR/compose.yaml")
UP_COMPOSE=()
NO_SEED=false
LOCAL=false
ASSUME_YES=false
BACKUP_NAME=""

info() { printf '==> %s\n' "$1"; }
warn() { printf 'warning: %s\n' "$1" >&2; }
err() { printf 'error: %s\n' "$1" >&2; }

usage() {
    sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

env_value() {
    local key="$1" file="$SCRIPT_DIR/.env"
    [ -f "$file" ] || return 1
    sed -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*\(.*\)/\1/p" "$file" | tail -n 1
}

gen_secret() {
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import secrets; print(secrets.token_hex(32))'
    elif command -v python >/dev/null 2>&1; then
        python -c 'import secrets; print(secrets.token_hex(32))'
    elif command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    elif command -v docker >/dev/null 2>&1; then
        docker run --rm python:3.12-slim python -c 'import secrets; print(secrets.token_hex(32))'
    else
        return 1
    fi
}

ensure_secret() {
    local env_file="$SCRIPT_DIR/.env"
    if [ -f "$env_file" ] && grep -qE '^[[:space:]]*SECRET_KEY[[:space:]]*=[[:space:]]*[^[:space:]]' "$env_file"; then
        return
    fi
    local secret
    if ! secret="$(gen_secret)"; then
        err "Could not generate a SECRET_KEY. Install python3 or openssl."
        exit 1
    fi
    if [ -f "$env_file" ]; then
        printf 'SECRET_KEY=%s\n' "$secret" >> "$env_file"
    else
        printf 'SECRET_KEY=%s\n' "$secret" > "$env_file"
    fi
    info "Generated a per-host SECRET_KEY in .env"
}

ensure_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        warn "Docker is not installed."
        if [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
            read -r -p "Install Docker Desktop with Homebrew now? [y/N] " answer
            case "$answer" in
                y|Y|yes|YES)
                    brew install --cask docker
                    info "Start Docker Desktop, then re-run this script."
                    exit 1
                    ;;
            esac
        fi
        err "Install Docker: https://docs.docker.com/engine/install/"
        exit 1
    fi

    if ! docker info >/dev/null 2>&1; then
        warn "Docker is installed but the engine is not running."
        if [ "$(uname -s)" = "Darwin" ] && command -v open >/dev/null 2>&1; then
            read -r -p "Start Docker Desktop now? [y/N] " answer
            case "$answer" in
                y|Y|yes|YES) open -a Docker ;;
            esac
        fi
        info "Waiting for the Docker engine (this can take a minute)..."
        local elapsed=0
        while [ "$elapsed" -lt 120 ]; do
            if docker info >/dev/null 2>&1; then break; fi
            sleep 2
            elapsed=$((elapsed + 2))
        done
        if ! docker info >/dev/null 2>&1; then
            err "Docker engine is not reachable. Start Docker and re-run."
            exit 1
        fi
    fi

    if ! docker compose version >/dev/null 2>&1; then
        err "Docker Compose v2 is required ('docker compose'). Update Docker."
        exit 1
    fi
}

get_up_compose() {
    UP_COMPOSE=(-f "$SCRIPT_DIR/compose.yaml")
    if [ "$NO_SEED" = false ]; then
        if [ ! -f "$SCRIPT_DIR/namelist.csv" ]; then
            err "namelist.csv not found. Add it to the project root, or run with --no-seed and import boarders in the app."
            exit 1
        fi
        UP_COMPOSE+=(-f "$SCRIPT_DIR/compose.seed.yaml")
    fi
}

run_compose() {
    docker compose "$@"
}

wait_healthy() {
    info "Waiting for the app to report healthy..."
    local elapsed=0
    while [ "$elapsed" -lt 120 ]; do
        local id state
        id="$(docker compose "${UP_COMPOSE[@]}" ps -q app 2>/dev/null | head -n 1 || true)"
        if [ -n "$id" ]; then
            state="$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || true)"
            if [ "$state" = "healthy" ]; then return 0; fi
            if [ "$state" = "unhealthy" ]; then return 1; fi
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    return 1
}

host_ip() {
    if command -v hostname >/dev/null 2>&1 && hostname -I >/dev/null 2>&1; then
        hostname -I 2>/dev/null | awk '{print $1}'
    elif command -v ipconfig >/dev/null 2>&1; then
        ipconfig getifaddr en0 2>/dev/null || true
    fi
}

open_browser() {
    local url="$1"
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$url" >/dev/null 2>&1 &
    elif command -v open >/dev/null 2>&1; then
        open "$url" >/dev/null 2>&1 &
    else
        info "Open $url in your browser."
    fi
}

confirm_destructive() {
    if [ "$ASSUME_YES" = true ]; then return; fi
    printf '%s Type YES to continue: ' "$1"
    read -r answer
    if [ "$answer" != "YES" ]; then
        info "Cancelled."
        exit 0
    fi
}

select_restore() {
    local root="$SCRIPT_DIR/shared/restore"
    if [ -n "$BACKUP_NAME" ]; then
        if [ -d "$root/$BACKUP_NAME" ]; then
            printf '%s\n' "$BACKUP_NAME"
            return
        fi
        err "Backup folder not found: $root/$BACKUP_NAME"
        exit 1
    fi
    local folders=()
    if [ -d "$root" ]; then
        while IFS= read -r dir; do
            [ -n "$dir" ] && folders+=("$(basename "$dir")")
        done < <(find "$root" -maxdepth 1 -type d -name 'lateness-*' 2>/dev/null | sort)
    fi
    if [ "${#folders[@]}" -eq 0 ]; then
        err "No backup folders in shared/restore. Copy a lateness-* folder there first."
        exit 1
    fi
    if [ "${#folders[@]}" -eq 1 ]; then
        printf '%s\n' "${folders[0]}"
        return
    fi
    info "Backups available to restore:"
    local i=1
    for folder in "${folders[@]}"; do
        printf '  [%d] %s\n' "$i" "$folder"
        i=$((i + 1))
    done
    read -r -p "Choose a number: " choice
    if ! [[ "$choice" =~ ^[0-9]+$ ]] || [ "$choice" -lt 1 ] || [ "$choice" -gt "${#folders[@]}" ]; then
        err "Invalid choice."
        exit 1
    fi
    printf '%s\n' "${folders[$((choice - 1))]}"
}

app_port() {
    if [ -n "${APP_PORT:-}" ]; then printf '%s' "$APP_PORT"; return; fi
    local from_env
    from_env="$(env_value APP_PORT || true)"
    printf '%s' "${from_env:-8000}"
}

port_in_use() {
    local port="$1"
    if command -v lsof >/dev/null 2>&1; then
        lsof -iTCP:"$port" -sTCP:LISTEN -nP >/dev/null 2>&1
    elif command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}\$"
    else
        return 1
    fi
}

start_app() {
    ensure_docker
    ensure_secret
    get_up_compose
    local bind_addr
    if [ "$LOCAL" = true ]; then
        bind_addr=127.0.0.1
        export BIND_ADDR="$bind_addr"
    else
        bind_addr="${BIND_ADDR:-$(env_value BIND_ADDR || true)}"
        bind_addr="${bind_addr:-0.0.0.0}"
    fi
    run_compose "${UP_COMPOSE[@]}" up -d --build
    local port
    port="$(app_port)"
    if port_in_use "$port"; then
        warn "Port $port is already in use; if start fails, set APP_PORT in .env to another port."
    fi
    if wait_healthy; then
        local url="http://127.0.0.1:${port}"
        info "App is up: $url"
        if [ "$bind_addr" = "0.0.0.0" ]; then
            local ip
            ip="$(host_ip || true)"
            if [ -n "$ip" ]; then info "On the office network: http://${ip}:${port}"; fi
            info "For a stable URL, reserve this PC's IP, allow inbound TCP ${port} through the firewall, and keep the PC awake."
        fi
        open_browser "$url"
    else
        err "App did not become healthy. Recent logs:"
        run_compose "${UP_COMPOSE[@]}" logs --tail 40 || true
        exit 1
    fi
}

stop_app() {
    ensure_docker
    ensure_secret
    run_compose "${BASE_COMPOSE[@]}" down
    info "Stopped. Your data is kept in the lateness-data volume."
}

show_logs() {
    ensure_docker
    ensure_secret
    run_compose "${BASE_COMPOSE[@]}" logs -f --tail 100
}

show_status() {
    ensure_docker
    ensure_secret
    docker compose "${BASE_COMPOSE[@]}" ps
}

save_backup() {
    ensure_docker
    ensure_secret
    mkdir -p "$SCRIPT_DIR/shared/backups"
    run_compose "${BASE_COMPOSE[@]}" run --rm backup \
        python backup_db.py --dest /backup --keep "${BACKUP_KEEP:-7}"
    info "Backup written under shared/backups."
}

restore_backup() {
    ensure_docker
    ensure_secret
    local folder
    folder="$(select_restore)"
    confirm_destructive "Restoring '$folder' replaces the live database."
    run_compose "${BASE_COMPOSE[@]}" down
    run_compose "${BASE_COMPOSE[@]}" run --rm restore \
        python restore_db.py --from "/restore/$folder" --force
    get_up_compose
    run_compose "${UP_COMPOSE[@]}" up -d
    info "Restored '$folder' and restarted the app."
}

seed_demo() {
    ensure_docker
    ensure_secret
    confirm_destructive "Seeding demo data wipes imported reports, history, and punishments in the live volume."
    get_up_compose
    run_compose "${BASE_COMPOSE[@]}" down
    run_compose "${UP_COMPOSE[@]}" run --rm seed \
        python seed_demo_data.py \
        --db /data/lateness_history.db \
        --namelist /data/namelist.csv \
        --log-dir /data/raw/seed
    run_compose "${UP_COMPOSE[@]}" up -d
    info "Demo data seeded."
}

reset_app() {
    ensure_docker
    ensure_secret
    confirm_destructive "This deletes the lateness-data volume, including the database and archive. Backups in shared/backups are kept."
    run_compose "${BASE_COMPOSE[@]}" down -v
    info "All live data deleted."
}

menu() {
    while true; do
        printf '\n'
        printf '  Lateness App (Docker)\n'
        printf '  ---------------------\n'
        printf '  [1] Start            [2] Stop\n'
        printf '  [3] Logs             [4] Status\n'
        printf '  [5] Backup           [6] Restore\n'
        printf '  [7] Seed demo data   [8] Reset (delete all data)\n'
        printf '  [9] Quit\n\n'
        read -r -p 'Choose an option: ' choice
        case "$choice" in
            1) start_app ;;
            2) stop_app ;;
            3) show_logs ;;
            4) show_status ;;
            5) save_backup ;;
            6) restore_backup ;;
            7) seed_demo ;;
            8) reset_app ;;
            9) exit 0 ;;
            *) warn "Unknown option '$choice'." ;;
        esac
        read -r -p 'Press Enter to continue...' _ || true
    done
}

COMMAND="${1:-}"
if [ $# -gt 0 ]; then shift; fi
while [ $# -gt 0 ]; do
    case "$1" in
        --no-seed) NO_SEED=true ;;
        --local) LOCAL=true ;;
        --yes|-y) ASSUME_YES=true ;;
        --backup) BACKUP_NAME="${2:-}"; shift ;;
        -h|--help) COMMAND=help ;;
        *) err "Unknown option: $1"; exit 2 ;;
    esac
    shift
done

case "$COMMAND" in
    '') menu ;;
    up) start_app ;;
    down) stop_app ;;
    logs) show_logs ;;
    status) show_status ;;
    backup) save_backup ;;
    restore) restore_backup ;;
    seed) seed_demo ;;
    reset) reset_app ;;
    help) usage ;;
    *)
        err "Unknown command '$COMMAND'. Use: up, down, logs, status, backup, restore, seed, reset."
        exit 2
        ;;
esac
