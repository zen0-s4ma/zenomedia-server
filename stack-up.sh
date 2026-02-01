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

# Progreso visual
PROGRESS_BAR_WIDTH="${PROGRESS_BAR_WIDTH:-30}"      # ancho de la barra
EWMA_ALPHA="${EWMA_ALPHA:-0.25}"                    # suavizado ETA (0..1)

# Barra viva: reescribe en la misma línea
LIVE_PROGRESS="${LIVE_PROGRESS:-1}"                 # 1=on, 0=off

# Filtro opcional de grupos
ONLY_GROUPS=""     # ej: "db,vpn,vpn_clients"

# =========================
# Helpers
# =========================
START_TS="$(date +%s)"
now_ts() { date +%s; }
elapsed() { echo $(( $(now_ts) - START_TS )); }

# Progreso global
TOTAL_SERVICES=0
DONE_SERVICES=0

# EWMA del tiempo por servicio
EWMA_SEC_PER_SVC=""
LAST_SVC_START_TS=""

# Lista de servicios levantados
declare -a DONE_NAMES=()

fmt_hms() {
  local s="$1"
  local h=$((s/3600)); local m=$(((s%3600)/60)); local ss=$((s%60))
  if (( h > 0 )); then printf "%dh %02dm %02ds" "$h" "$m" "$ss"
  else printf "%dm %02ds" "$m" "$ss"
  fi
}

progress_bar() {
  local done="$1"
  local total="$2"
  local width="$3"

  if (( total <= 0 )); then
    printf "[%*s]" "$width" ""
    return 0
  fi

  local percent=$(( done * 100 / total ))
  local filled=$(( percent * width / 100 ))
  local empty=$(( width - filled ))

  local fill_char="█"
  local empty_char="░"
  local bar=""

  for ((i=0; i<filled; i++)); do bar+="${fill_char}"; done
  for ((i=0; i<empty; i++)); do bar+="${empty_char}"; done

  printf "[%s]" "$bar"
}

update_ewma() {
  local svc_seconds="$1"

  if [[ -z "${EWMA_SEC_PER_SVC}" ]]; then
    EWMA_SEC_PER_SVC="$svc_seconds"
    return 0
  fi

  # alpha en % entero (1..99)
  local alpha_pct
  alpha_pct="$(awk -v a="$EWMA_ALPHA" 'BEGIN{printf "%d", a*100 + 0.5}')"
  if (( alpha_pct < 1 )); then alpha_pct=1; fi
  if (( alpha_pct > 99 )); then alpha_pct=99; fi

  local ewma_old="$EWMA_SEC_PER_SVC"
  local ewma_new=$(( (alpha_pct * svc_seconds + (100 - alpha_pct) * ewma_old) / 100 ))
  EWMA_SEC_PER_SVC="$ewma_new"
}

progress_text() {
  local done="$DONE_SERVICES"
  local total="$TOTAL_SERVICES"

  local percent=0
  if (( total > 0 )); then percent=$(( done * 100 / total )); fi

  local e; e="$(elapsed)"

  local eta="?"
  if (( done > 0 && total > 0 )); then
    local remaining=$(( total - done ))
    if [[ -n "${EWMA_SEC_PER_SVC}" ]]; then
      eta="$(fmt_hms $(( EWMA_SEC_PER_SVC * remaining )))"
    else
      local avg=$(( e / done ))
      eta="$(fmt_hms $(( avg * remaining )))"
    fi
  fi

  local bar; bar="$(progress_bar "$done" "$total" "$PROGRESS_BAR_WIDTH")"

  local ewma_info=""
  if [[ -n "${EWMA_SEC_PER_SVC}" ]]; then
    ewma_info=" ~${EWMA_SEC_PER_SVC}s/svc"
  fi

  echo -n "📊 ${bar} ${percent}% | 📈 ${done}/${total} | ⏱️ $(fmt_hms "$e") | ⏳ ETA ~ ${eta}${ewma_info}"
}

render_progress() {
  if [[ "${LIVE_PROGRESS}" == "1" ]]; then
    local txt; txt="$(progress_text)"
    printf "\r%-160s" "$txt"
  else
    echo
    echo "$(progress_text)"
  fi
}

# Log que no rompe la barra: imprime línea permanente arriba y vuelve a pintar la barra abajo
log() {
  if [[ "${LIVE_PROGRESS}" == "1" ]]; then
    echo
  fi
  echo -e "[$(date +'%F %T')] $*"
  render_progress
}

die() {
  if [[ "${LIVE_PROGRESS}" == "1" ]]; then
    echo
  fi
  echo -e "ERROR: $*" >&2
  exit 1
}

load_env() {
  if [[ -f "${PROJECT_DIR}/.env" ]]; then
    log "Cargando .env"
    set -a
    # shellcheck disable=SC1090
    source <(sed 's/\r$//' "${PROJECT_DIR}/.env" | sed -n 's/^\([^#][^=]*\)=\(.*\)$/\1=\2/p')
    set +a
  else
    log "No hay .env en ${PROJECT_DIR} (continuo igualmente)."
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
  local start=$SECONDS
  local end=$((start + timeout))

  log "Esperando READY: ${svc} (timeout=${timeout}s)"

  while (( SECONDS < end )); do
    local hs rs
    hs="$(health_status "$svc")"
    rs="$(run_status "$svc")"

    if has_healthcheck "$svc"; then
      if [[ "$hs" == "healthy" ]]; then
        log "OK: ${svc} => healthy"
        return 0
      fi
      if [[ "$hs" == "unhealthy" ]]; then
        log "WARN: ${svc} => unhealthy (restart y reintento)"
        dc restart "$svc" >/dev/null || true
        sleep 5
      fi
    else
      if [[ "$rs" == "running" ]]; then
        log "OK: ${svc} => running (sin healthcheck)"
        return 0
      fi
    fi

    render_progress
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
  local start=$SECONDS
  local end=$((start + timeout))

  log "🔒 Esperando VPN real (Mullvad JSON) en ${svc}... (timeout=${timeout}s)"
  wait_ready "$svc" "$timeout"

  while (( SECONDS < end )); do
    local body
    body="$(http_get_in_container "$svc" "https://am.i.mullvad.net/json" 2>/dev/null || true)"

    if [[ "$body" == "NO_HTTP_CLIENT" ]]; then
      die "${svc}: no hay curl ni wget dentro del contenedor para consultar Mullvad"
    fi

    if echo "$body" | grep -q '"mullvad_exit_ip"[[:space:]]*:[[:space:]]*true'; then
      log "✅ VPN OK: mullvad_exit_ip=true (Mullvad confirmado)"
      return 0
    fi

    render_progress
    sleep "$SLEEP_STEP"
  done

  die "${svc} no confirmó Mullvad (mullvad_exit_ip=true) en ${timeout}s"
}

group_pause() {
  log "⏳ Pausa entre grupos: ${INTER_GROUP_SLEEP}s"
  local i
  for ((i=INTER_GROUP_SLEEP; i>0; i--)); do
    render_progress
    sleep 1
  done
  if [[ "${LIVE_PROGRESS}" == "1" ]]; then printf "\r%-160s" ""; fi
  render_progress
}

service_pause() {
  log "… pausa entre servicios: ${INTER_SERVICE_SLEEP}s"
  local i
  for ((i=INTER_SERVICE_SLEEP; i>0; i--)); do
    render_progress
    sleep 1
  done
  if [[ "${LIVE_PROGRESS}" == "1" ]]; then printf "\r%-160s" ""; fi
  render_progress
}

should_run_group() {
  local group_name="$1"
  [[ -z "${ONLY_GROUPS}" ]] && return 0
  IFS=',' read -r -a only <<< "${ONLY_GROUPS}"
  for g in "${only[@]}"; do [[ "$g" == "$group_name" ]] && return 0; done
  return 1
}

count_group_services() {
  local group_name="$1"; shift
  local services=("$@")
  if should_run_group "$group_name"; then
    TOTAL_SERVICES=$((TOTAL_SERVICES + ${#services[@]}))
  fi
}

up_group_sequential() {
  local group_name="$1"; shift
  local services=("$@")

  if ! should_run_group "$group_name"; then
    log "Saltando grupo '${group_name}' (no está en --only)"
    return 0
  fi

  log "=============================="
  log "GRUPO: ${group_name}"
  log "Servicios: ${services[*]}"
  log "=============================="

  local count="${#services[@]}"
  local i=0

  for s in "${services[@]}"; do
    i=$((i+1))
    log "🚀 (${group_name}) Arrancando servicio ${s} (${i}/${count})"

    render_progress
    LAST_SVC_START_TS="$(now_ts)"

    # ✅ SOLO UP -D (SIN PULL, SIN BUILD)
    dc up -d "$s"

    wait_ready "$s" "$TIMEOUT_DEFAULT"

    local svc_elapsed=$(( $(now_ts) - LAST_SVC_START_TS ))
    update_ewma "$svc_elapsed"

    DONE_SERVICES=$((DONE_SERVICES + 1))
    DONE_NAMES+=("$s")

    # Línea acumulada permanente de "UP"
    log "🟢 UP (${DONE_SERVICES}/${TOTAL_SERVICES}) ${s}  (t=${svc_elapsed}s)"

    render_progress

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
  PROGRESS_BAR_WIDTH=30
  EWMA_ALPHA=0.25
  LIVE_PROGRESS=1
EOF
}

# =========================
# Args (sin --pull ni --build)
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
  homarr
  komga
  calibre
  uptime-kuma
  dozzle
  netdata
  openvscode
  swagger-ui
  swagger-editor
  github-desktop
  searxng
  qdrant
  rss-bridge
  gpodder
  sftpgo
  filestash_wopi
  filestash
  filezilla
  unmanic
  beets
  webgrabplus
  iptvnator-backend
  iptvnator
  nextpvr
)

# =========================
# Total para progreso
# =========================
count_group_services db "${GROUP_DB[@]}"
count_group_services net "${GROUP_NET[@]}"
count_group_services vpn "${GROUP_VPN[@]}"
count_group_services vpn_clients "${GROUP_VPN_CLIENTS[@]}"
count_group_services db_apps "${GROUP_DB_APPS[@]}"
count_group_services media "${GROUP_MEDIA[@]}"
count_group_services tubearchivist "${GROUP_TUBEARCHIVIST[@]}"
count_group_services arr "${GROUP_ARR[@]}"
count_group_services misc "${GROUP_UI_MISC[@]}"

log "📌 Total servicios planificados: ${TOTAL_SERVICES}"
render_progress
echo

# =========================
# Ejecución
# =========================
up_group_sequential db "${GROUP_DB[@]}"
group_pause

up_group_sequential net "${GROUP_NET[@]}"
group_pause

up_group_sequential vpn "${GROUP_VPN[@]}"
wait_gluetun_mullvad "$TIMEOUT_DEFAULT"
group_pause

up_group_sequential vpn_clients "${GROUP_VPN_CLIENTS[@]}"
group_pause

up_group_sequential db_apps "${GROUP_DB_APPS[@]}"
group_pause

up_group_sequential media "${GROUP_MEDIA[@]}"
group_pause

up_group_sequential tubearchivist "${GROUP_TUBEARCHIVIST[@]}"
group_pause

up_group_sequential arr "${GROUP_ARR[@]}"
group_pause

up_group_sequential misc "${GROUP_UI_MISC[@]}"

if [[ "${LIVE_PROGRESS}" == "1" ]]; then echo; fi
log "🎉 Todo levantado. Tiempo total: $(fmt_hms "$(elapsed)")"
render_progress
echo
