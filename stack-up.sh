#!/usr/bin/env bash
set -euo pipefail

# =========================
# Config general
# =========================
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
PROJECT_DIR="${PROJECT_DIR:-$(pwd)}"
COMPOSE_PROFILES="${COMPOSE_PROFILES:-}"

# Timeouts / sleeps
TIMEOUT_DEFAULT="${TIMEOUT_DEFAULT:-420}"           # timeout por servicio (segundos)
SLEEP_STEP="${SLEEP_STEP:-2}"                       # polling interno readiness
INTER_GROUP_SLEEP="${INTER_GROUP_SLEEP:-90}"        # 1m30 entre grupos
INTER_SERVICE_SLEEP="${INTER_SERVICE_SLEEP:-5}"     # 5s entre servicios (dentro del grupo)

# Filtro opcional de grupos
ONLY_GROUPS=""     # ej: "db,vpn,vpn_clients"

# =========================
# Helpers
# =========================
ts_now() { date +%s; }

fmt_ms() {
  # input: seconds
  local s="$1"
  local m=$((s/60))
  local ss=$((s%60))
  printf "%d:%02d" "$m" "$ss"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

load_env() {
  if [[ -f "${PROJECT_DIR}/.env" ]]; then
    # tolera CRLF en Windows
    set -a
    # shellcheck disable=SC1090
    source <(sed 's/\r$//' "${PROJECT_DIR}/.env" | sed -n 's/^\([^#][^=]*\)=\(.*\)$/\1=\2/p')
    set +a
  fi
}

dc() {
  local args=()
  args+=(-f "${COMPOSE_FILE}")
  if [[ -n "${COMPOSE_PROFILES}" ]]; then
    IFS=',' read -r -a profs <<< "${COMPOSE_PROFILES}"
    for p in "${profs[@]}"; do args+=(--profile "$p"); done
  fi
  docker compose "${args[@]}" "$@"
}

container_id() { dc ps -q "$1" 2>/dev/null || true; }

has_healthcheck() {
  local cid; cid="$(container_id "$1")"
  [[ -n "$cid" ]] || return 1
  docker inspect -f '{{if .State.Health}}yes{{else}}no{{end}}' "$cid" 2>/dev/null | grep -q '^yes$'
}

health_status() {
  local cid; cid="$(container_id "$1")"
  [[ -n "$cid" ]] || { echo "missing"; return 0; }
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}nohealth{{end}}' "$cid" 2>/dev/null || echo "unknown"
}

run_status() {
  local cid; cid="$(container_id "$1")"
  [[ -n "$cid" ]] || { echo "missing"; return 0; }
  docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null || echo "unknown"
}

wait_ready() {
  local svc="$1"
  local timeout="${2:-$TIMEOUT_DEFAULT}"
  local start="$SECONDS"
  local end=$((start + timeout))

  while (( SECONDS < end )); do
    local hs rs
    hs="$(health_status "$svc")"
    rs="$(run_status "$svc")"

    # Si hay healthcheck, exigimos healthy
    if has_healthcheck "$svc"; then
      if [[ "$hs" == "healthy" ]]; then
        return 0
      fi
      if [[ "$hs" == "unhealthy" ]]; then
        # Mantengo la lógica anterior: intento restart automático ante unhealthy
        dc restart "$svc" >/dev/null || true
        sleep 5
      fi
    else
      # Sin healthcheck: mínimo running
      if [[ "$rs" == "running" ]]; then
        return 0
      fi
    fi

    sleep "$SLEEP_STEP"
  done

  die "${svc} no alcanzó READY en ${timeout}s (health=$(health_status "$svc"), run=$(run_status "$svc"))"
}

http_get_in_container() {
  local svc="$1"
  local url="$2"
  docker exec "$svc" sh -lc "
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL '$url'
    elif command -v wget >/dev/null 2>&1; then
      wget -qO- '$url'
    else
      echo 'NO_HTTP_CLIENT'
      exit 127
    fi
  "
}

wait_gluetun_mullvad() {
  local svc="gluetun-swt"
  local timeout="${1:-$TIMEOUT_DEFAULT}"
  local start="$SECONDS"
  local end=$((start + timeout))

  # primero: contenedor "ready" (running/healthy)
  wait_ready "$svc" "$timeout"

  # luego: gate mullvad_exit_ip=true
  while (( SECONDS < end )); do
    local body
    body="$(http_get_in_container "$svc" "https://am.i.mullvad.net/json" 2>/dev/null || true)"
    if [[ "$body" == "NO_HTTP_CLIENT" ]]; then
      die "${svc}: no hay curl ni wget dentro del contenedor para consultar Mullvad"
    fi

    if echo "$body" | grep -q '"mullvad_exit_ip"[[:space:]]*:[[:space:]]*true'; then
      return 0
    fi

    sleep "$SLEEP_STEP"
  done

  die "${svc} no confirmó Mullvad (mullvad_exit_ip=true) en ${timeout}s"
}

group_pause() {
  sleep "${INTER_GROUP_SLEEP}"
}

service_pause() {
  sleep "${INTER_SERVICE_SLEEP}"
}

should_run_group() {
  local group_name="$1"
  [[ -z "${ONLY_GROUPS}" ]] && return 0
  IFS=',' read -r -a only <<< "${ONLY_GROUPS}"
  for g in "${only[@]}"; do [[ "$g" == "$group_name" ]] && return 0; done
  return 1
}

# Arranque secuencial con salida "limpia" por grupo
up_group_sequential_clean() {
  local group_label="$1"; shift
  local services=("$@")

  if ! should_run_group "$group_label"; then
    return 0
  fi

  echo
  echo "GRUPO ${group_label}:"

  local count="${#services[@]}"
  local i=0

  for s in "${services[@]}"; do
    i=$((i+1))
    echo "${s} -> Arrancando..."

    local t0; t0="$(ts_now)"

    # ✅ SOLO up -d. SIN pull, SIN build.
    dc up -d "$s" >/dev/null

    # READY
    wait_ready "$s" "$TIMEOUT_DEFAULT"

    # Si es gluetun, además esperamos Mullvad antes de marcar OK
    if [[ "$s" == "gluetun-swt" ]]; then
      wait_gluetun_mullvad "$TIMEOUT_DEFAULT"
    fi

    local t1; t1="$(ts_now)"
    local dt=$((t1 - t0))

    echo "${s} -> OK (t = $(fmt_ms "$dt"))"

    # pausa entre servicios salvo el último
    if [[ $i -lt $count ]]; then
      service_pause
    fi
  done
}

usage() {
  cat <<EOF
Uso:
  ./stack-up.sh [--only db,vpn,vpn_clients,...]

Env útiles:
  TIMEOUT_DEFAULT=420
  INTER_GROUP_SLEEP=90
  INTER_SERVICE_SLEEP=5
  COMPOSE_PROFILES=gpu-nvidia
EOF
}

# =========================
# Args (sin pull/build)
# =========================
while [[ $# -gt 0 ]]; do
  case "$1" in
    --only) ONLY_GROUPS="${2:-}"; shift 2;;
    -h|--help) usage; exit 0;;
    *) die "Argumento desconocido: $1";;
  esac
done

cd "$PROJECT_DIR"
load_env

# =========================
# Grupos (misma lógica que antes)
# =========================
GROUP_DB=(
  postgres
  romm-db
  archivist-redis
  archivist-es
  archivist-redis-audio
  archivist-es-audio
)

GROUP_NET=(
  nginx-proxy-manager
  cloudflare-ddns
  cloudflared
)

GROUP_VPN=( gluetun-swt )

GROUP_VPN_CLIENTS=(
  dispatcharr
  vpn-ip-check
  firefox
  tor-browser
)

GROUP_DB_APPS=(
  n8n
  jellystat
  cloudbeaver
  guacd
  guacamole
  bitwarden-lite
  romm
)

GROUP_MEDIA=(
  jellyfin
  ersatztv
  jfa-go
)

GROUP_TUBEARCHIVIST=(
  tubearchivist
  tubearchivist-audio
)

GROUP_ARR=(
  qbittorrent
  prowlarr
  sonarr
  radarr
  bazarr
  lidarr
  readarr
  jackett
  jellyseerr
  recyclarr
  notifiarr
)

GROUP_UI_MISC=(
  heimdall
  uptime-kuma
  dozzle
  netdata
  openvscode
  swagger-ui
  swagger-editor
  github-desktop
  gpodder
  sftpgo
  filestash_wopi
  filestash
  filezilla
  webgrabplus
  nextpvr
)

GROUP_UI_OTHERS=(
  homarr
  komga
  calibre
  searxng
  qdrant
  rss-bridge
  unmanic
  beets
  iptvnator-backend
  iptvnator
)



# =========================
# Ejecución (90s entre grupos)
# =========================
up_group_sequential_clean "1" "${GROUP_DB[@]}"
group_pause

up_group_sequential_clean "2" "${GROUP_NET[@]}"
group_pause

# Grupo VPN (gluetun) — el OK incluye Mullvad gate
up_group_sequential_clean "3" "${GROUP_VPN[@]}"
group_pause
group_pause

# Los clientes pegados ya solo arrancan cuando Mullvad fue OK en el grupo 3
up_group_sequential_clean "4" "${GROUP_VPN_CLIENTS[@]}"
group_pause

up_group_sequential_clean "5" "${GROUP_DB_APPS[@]}"
group_pause

up_group_sequential_clean "6" "${GROUP_MEDIA[@]}"
group_pause

up_group_sequential_clean "7" "${GROUP_TUBEARCHIVIST[@]}"
group_pause

# up_group_sequential_clean "8" "${GROUP_ARR[@]}"
# group_pause

up_group_sequential_clean "9" "${GROUP_UI_MISC[@]}"
group_pause

# up_group_sequential_clean "9" "${GROUP_UI_OTHERS[@]}"

echo
echo "FIN ✅"
