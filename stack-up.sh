#!/usr/bin/env bash
set -u -o pipefail
# ^ quitamos -e para que no se corte el script ante un fallo puntual

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

# Si quieres volver al modo "corta al primer fallo": FAIL_FAST=true
FAIL_FAST="${FAIL_FAST:-false}"

# Filtro opcional de grupos
ONLY_GROUPS=""     # ej: "db,vpn,vpn_clients"

# =========================
# Estado / resumen
# =========================
declare -a OK_SERVICES=()
declare -a FAIL_SERVICES=()
declare -A FAIL_REASON=()

log()  { echo "$@"; }
warn() { echo "WARN: $*" >&2; }
err()  { echo "ERROR: $*" >&2; }

die() {
  err "$*"
  exit 1
}

fail() {
  # En modo normal: no aborta, solo devuelve error
  # En FAIL_FAST=true: aborta como antes
  local msg="$1"
  if [[ "${FAIL_FAST}" == "true" ]]; then
    die "$msg"
  else
    warn "$msg"
    return 1
  fi
}

mark_ok() {
  local svc="$1"
  OK_SERVICES+=("$svc")
}

mark_fail() {
  local svc="$1"
  local reason="$2"
  FAIL_SERVICES+=("$svc")
  FAIL_REASON["$svc"]="$reason"
}

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

    # Si el contenedor murió / salió, no esperes 420s para nada
    if [[ "$rs" == "exited" || "$rs" == "dead" ]]; then
      return 1
    fi

    if has_healthcheck "$svc"; then
      if [[ "$hs" == "healthy" ]]; then
        return 0
      fi
      if [[ "$hs" == "unhealthy" ]]; then
        # intento restart automático ante unhealthy
        dc restart "$svc" >/dev/null 2>&1 || true
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

  return 1
}

http_get_in_container() {
  local svc="$1"
  local url="$2"
  local cid; cid="$(container_id "$svc")"
  [[ -n "$cid" ]] || return 1

  docker exec "$cid" sh -lc "
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

  # primero: contenedor "ready"
  if ! wait_ready "$svc" "$timeout"; then
    return 1
  fi

  # luego: gate mullvad_exit_ip=true
  while (( SECONDS < end )); do
    local body
    body="$(http_get_in_container "$svc" "https://am.i.mullvad.net/json" 2>/dev/null || true)"

    if [[ "$body" == "NO_HTTP_CLIENT" ]]; then
      return 1
    fi

    if echo "$body" | grep -q '"mullvad_exit_ip"[[:space:]]*:[[:space:]]*true'; then
      return 0
    fi

    sleep "$SLEEP_STEP"
  done

  return 1
}

group_pause() { sleep "${INTER_GROUP_SLEEP}"; }
service_pause() { sleep "${INTER_SERVICE_SLEEP}"; }

should_run_group() {
  local group_name="$1"
  [[ -z "${ONLY_GROUPS}" ]] && return 0
  IFS=',' read -r -a only <<< "${ONLY_GROUPS}"
  for g in "${only[@]}"; do [[ "$g" == "$group_name" ]] && return 0; done
  return 1
}

# Arranque secuencial por grupo (continúa aunque haya fallos)
up_group_sequential_clean() {
  local group_label="$1"; shift
  local services=("$@")

  if ! should_run_group "$group_label"; then
    return 0
  fi

  log
  log "GRUPO ${group_label}:"

  local count="${#services[@]}"
  local i=0

  for s in "${services[@]}"; do
    i=$((i+1))
    log "${s} -> Arrancando..."

    local t0; t0="$(ts_now)"

    # ✅ SOLO up -d (si falla, no abortamos todo)
    if ! dc up -d "$s" >/dev/null 2>&1; then
      local t1; t1="$(ts_now)"
      local dt=$((t1 - t0))
      err "${s} -> FAIL (docker compose up falló) (t = $(fmt_ms "$dt"))"
      mark_fail "$s" "up_failed"
      [[ $i -lt $count ]] && service_pause
      continue
    fi

    # READY (si falla por timeout o exit, seguimos)
    if ! wait_ready "$s" "$TIMEOUT_DEFAULT"; then
      local hs rs
      hs="$(health_status "$s")"
      rs="$(run_status "$s")"
      local t1; t1="$(ts_now)"
      local dt=$((t1 - t0))
      err "${s} -> FAIL (no READY en ${TIMEOUT_DEFAULT}s | health=${hs} run=${rs}) (t = $(fmt_ms "$dt"))"
      mark_fail "$s" "timeout_ready health=${hs} run=${rs}"
      [[ $i -lt $count ]] && service_pause
      continue
    fi

    # Si es gluetun, además esperamos Mullvad (si falla, no aborta)
    if [[ "$s" == "gluetun-swt" ]]; then
      if ! wait_gluetun_mullvad "$TIMEOUT_DEFAULT"; then
        local t1; t1="$(ts_now)"
        local dt=$((t1 - t0))
        err "${s} -> FAIL (no confirmó Mullvad en ${TIMEOUT_DEFAULT}s) (t = $(fmt_ms "$dt"))"
        mark_fail "$s" "mullvad_gate_failed"
        [[ $i -lt $count ]] && service_pause
        continue
      fi
    fi

    local t1; t1="$(ts_now)"
    local dt=$((t1 - t0))
    log "${s} -> OK (t = $(fmt_ms "$dt"))"
    mark_ok "$s"

    [[ $i -lt $count ]] && service_pause
  done
}

usage() {
  cat <<EOF
Uso:
  ./stack-up.sh [--only 1,2,3...]

Env útiles:
  TIMEOUT_DEFAULT=420
  INTER_GROUP_SLEEP=90
  INTER_SERVICE_SLEEP=5
  COMPOSE_PROFILES=gpu-nvidia
  FAIL_FAST=false   # si lo pones en true, se para al primer fallo (como antes)
EOF
}

# =========================
# Args
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
# Grupos
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
  heimdall
)

GROUP_MEDIA=(
  tubearchivist
  ersatztv
  jfa-go
  webgrabplus
  nextpvr
  tubearchivist-audio
)

GROUP_JELLYFIN=( jellyfin )

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
# Ejecución
# =========================
up_group_sequential_clean "1" "${GROUP_DB[@]}"
up_group_sequential_clean "2" "${GROUP_NET[@]}"
up_group_sequential_clean "3" "${GROUP_VPN[@]}"
up_group_sequential_clean "4" "${GROUP_VPN_CLIENTS[@]}"
up_group_sequential_clean "5" "${GROUP_DB_APPS[@]}"
up_group_sequential_clean "6" "${GROUP_MEDIA[@]}"
up_group_sequential_clean "7" "${GROUP_JELLYFIN[@]}"
# up_group_sequential_clean "8" "${GROUP_ARR[@]}"
up_group_sequential_clean "9" "${GROUP_UI_MISC[@]}"
# up_group_sequential_clean "10" "${GROUP_UI_OTHERS[@]}"

log
log "FIN ✅"
log "Resumen:"
log "  OK:   ${#OK_SERVICES[@]}"
log "  FAIL: ${#FAIL_SERVICES[@]}"

if (( ${#FAIL_SERVICES[@]} > 0 )); then
  log
  log "Fallaron (pero el script siguió):"
  for s in "${FAIL_SERVICES[@]}"; do
    log "  - ${s}: ${FAIL_REASON[$s]}"
  done
fi

# No forzamos exit 1 por defecto (para que no “rompa” tu flujo)
# Si quieres que devuelva error cuando haya fallos:
# (( ${#FAIL_SERVICES[@]} > 0 )) && exit 2
exit 0
