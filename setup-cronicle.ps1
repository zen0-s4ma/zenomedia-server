# setup-cronicle.ps1
# Monta TODO (persistencia + Custom-Dockerfiles + patch del compose)
# - NO toca .env
# - NO borra el docker-compose (hace backup .bkp1 y añade cronicle al final de services:)
# - Borra y recrea la persistencia de Cronicle (data/logs/plugins/artifacts)
# - Crea Dockerfile + runners: run-python, run-rust, run-shell, run-ps1

param(
  [string]$ProjectRoot  = "D:\Github-zen0s4ma\zenomedia-server",
  [string]$CronicleRoot = "E:\Docker_folders\cronicle",
  [string]$ScriptsRoot  = "E:\Docker_folders\_scripts"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-FileLF {
  param(
    [Parameter(Mandatory)] [string]$Path,
    [Parameter(Mandatory)] [string]$Content
  )
  $dir = Split-Path $Path -Parent
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }

  # Convertir CRLF -> LF (para scripts dentro del contenedor Linux)
  $contentLF = $Content -replace "`r`n", "`n"
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $contentLF, $utf8NoBom)
}

function Ensure-Dir {
  param([Parameter(Mandatory)] [string]$Path)
  if (-not (Test-Path $Path)) { New-Item -ItemType Directory -Force -Path $Path | Out-Null }
}

function Remove-DirIfExists {
  param([Parameter(Mandatory)] [string]$Path)
  if (Test-Path $Path) { Remove-Item -Recurse -Force $Path }
}

# ---------------------------
# Validaciones básicas
# ---------------------------
if (-not (Test-Path $ProjectRoot)) {
  throw "No existe ProjectRoot: $ProjectRoot"
}

# Detectar compose sin el bug de Join-Path (nada de comas en argumentos)
$composeFiles = @('docker-compose.yml','docker-compose.yaml','compose.yml','compose.yaml')
$composeCandidates = $composeFiles | ForEach-Object { Join-Path -Path $ProjectRoot -ChildPath $_ }

$ComposePath = $composeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $ComposePath) {
  throw "No encontré docker-compose.yml/.yaml ni compose.yml/.yaml en: $ProjectRoot"
}
Write-Host "Compose detectado: $ComposePath"

# ---------------------------
# Backup del compose
# ---------------------------
$backupPath = "$ComposePath.bkp1"
Copy-Item -Force $ComposePath $backupPath
Write-Host "Backup creado: $backupPath"

# ---------------------------
# Limpieza + creación de persistencia Cronicle
# ---------------------------
$persistDirs = @(
  (Join-Path -Path $CronicleRoot -ChildPath "data"),
  (Join-Path -Path $CronicleRoot -ChildPath "logs"),
  (Join-Path -Path $CronicleRoot -ChildPath "plugins"),
  (Join-Path -Path $CronicleRoot -ChildPath "artifacts")
)

Ensure-Dir $CronicleRoot

foreach ($d in $persistDirs) {
  if (Test-Path $d) {
    Write-Host "Borrando carpeta existente: $d"
    Remove-DirIfExists $d
  }
}

foreach ($d in $persistDirs) { Ensure-Dir $d }

# Subcarpetas útiles dentro de data (opcional, pero práctico)
Ensure-Dir (Join-Path -Path $CronicleRoot -ChildPath "data\python")
Ensure-Dir (Join-Path -Path $CronicleRoot -ChildPath "data\rust")

Write-Host "Persistencia lista en: $CronicleRoot"

# Asegurar carpeta de scripts (NO borrar contenido)
Ensure-Dir $ScriptsRoot
Write-Host "Carpeta scripts verificada: $ScriptsRoot (no se ha borrado nada)"

# ---------------------------
# Crear Custom-Dockerfiles (borra y recrea SOLO lo nuestro)
# ---------------------------
$customDir = Join-Path -Path $ProjectRoot -ChildPath "Custom-Dockerfiles\cronicle-python"
if (Test-Path $customDir) {
  Write-Host "Borrando Custom-Dockerfiles existente: $customDir"
  Remove-DirIfExists $customDir
}
Ensure-Dir $customDir

# ---------------------------
# Archivos a generar
# ---------------------------
$dockerfile = @'
ARG CRONICLE_TAG=0.9.80
FROM soulteary/cronicle:${CRONICLE_TAG}

SHELL ["/bin/sh", "-lc"]

ARG WITH_RUST=true
ARG WITH_POWERSHELL=true
ARG PWSH_VERSION=7.5.4

# Asegurar repo community (ffmpeg/rust suelen estar aquí)
RUN ALP_VER="$(cut -d. -f1,2 /etc/alpine-release)" \
 && grep -q "/community" /etc/apk/repositories || echo "https://dl-cdn.alpinelinux.org/alpine/v${ALP_VER}/community" >> /etc/apk/repositories \
 && apk update

# Base tools + Python + ffmpeg/ffprobe
RUN apk add --no-cache \
      bash ca-certificates curl jq coreutils tzdata \
      python3 py3-pip py3-virtualenv \
      ffmpeg \
 && python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel

# Rust toolchain + build essentials
RUN if [ "${WITH_RUST}" = "true" ]; then \
      apk add --no-cache \
        rust cargo \
        build-base musl-dev \
        openssl-dev pkgconfig \
        libffi-dev ; \
    fi

# PowerShell en Alpine (tar.gz musl)
RUN if [ "${WITH_POWERSHELL}" = "true" ]; then \
      apk add --no-cache \
        ca-certificates less ncurses-terminfo-base krb5-libs libgcc libintl libssl3 libstdc++ tzdata userspace-rcu zlib icu-libs curl ; \
      apk -X https://dl-cdn.alpinelinux.org/alpine/edge/main add --no-cache lttng-ust openssh-client || true ; \
      mkdir -p /opt/microsoft/powershell/7 ; \
      curl -L "https://github.com/PowerShell/PowerShell/releases/download/v${PWSH_VERSION}/powershell-${PWSH_VERSION}-linux-musl-x64.tar.gz" -o /tmp/powershell.tar.gz ; \
      tar zxf /tmp/powershell.tar.gz -C /opt/microsoft/powershell/7 ; \
      chmod +x /opt/microsoft/powershell/7/pwsh ; \
      ln -sf /opt/microsoft/powershell/7/pwsh /usr/bin/pwsh ; \
      rm -f /tmp/powershell.tar.gz ; \
    fi

# Runners
COPY Custom-Dockerfiles/cronicle-python/run-python /usr/local/bin/run-python
COPY Custom-Dockerfiles/cronicle-python/run-rust   /usr/local/bin/run-rust
COPY Custom-Dockerfiles/cronicle-python/run-shell  /usr/local/bin/run-shell
COPY Custom-Dockerfiles/cronicle-python/run-ps1    /usr/local/bin/run-ps1

RUN chmod +x /usr/local/bin/run-python \
             /usr/local/bin/run-rust \
             /usr/local/bin/run-shell \
             /usr/local/bin/run-ps1 \
 && mkdir -p /scripts /artifacts /opt/cronicle/data/python /opt/cronicle/data/rust
'@

$runPython = @'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT="${1:-}"
if [[ -z "${SCRIPT}" ]]; then
  echo "[run-python] Usage: run-python /scripts/your_script.py [args...]" >&2
  exit 2
fi
shift || true

if [[ ! -f "${SCRIPT}" ]]; then
  echo "[run-python] ERROR: Script not found: ${SCRIPT}" >&2
  exit 2
fi

PY_RUNNER_MODE="${PY_RUNNER_MODE:-venv}"  # venv | system
VENV_DIR="${PY_VENV_DIR:-/opt/cronicle/data/python/venv}"
PIP_CACHE_DIR="${PY_PIP_CACHE_DIR:-/opt/cronicle/data/python/pip-cache}"
STATE_DIR="${PY_STATE_DIR:-/opt/cronicle/data/python/state}"
PIP_EXTRA_ARGS="${PIP_EXTRA_ARGS:-}"

mkdir -p "${PIP_CACHE_DIR}" "${STATE_DIR}"
export PIP_CACHE_DIR
export PYTHONUNBUFFERED=1

SCRIPT_DIR="$(cd "$(dirname "${SCRIPT}")" && pwd)"

REQ_FILE=""
if [[ -n "${PY_REQUIREMENTS_FILE:-}" && -f "${PY_REQUIREMENTS_FILE}" ]]; then
  REQ_FILE="${PY_REQUIREMENTS_FILE}"
elif [[ -f "${SCRIPT}.requirements.txt" ]]; then
  REQ_FILE="${SCRIPT}.requirements.txt"
elif [[ -f "${SCRIPT_DIR}/requirements.txt" ]]; then
  REQ_FILE="${SCRIPT_DIR}/requirements.txt"
fi

PY_BIN="python3"
if [[ "${PY_RUNNER_MODE}" == "venv" ]]; then
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "[run-python] Creating venv at: ${VENV_DIR}"
    mkdir -p "$(dirname "${VENV_DIR}")"
    python3 -m venv --copies "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install --upgrade pip setuptools wheel
  fi
  PY_BIN="${VENV_DIR}/bin/python"
fi

if [[ -n "${REQ_FILE}" ]]; then
  echo "[run-python] requirements: ${REQ_FILE}"
  SCRIPT_ID="$(printf "%s" "${SCRIPT}" | sha256sum | awk '{print $1}')"
  HASH_FILE="${STATE_DIR}/${SCRIPT_ID}.requirements.sha256"

  REQ_HASH="$(sha256sum "${REQ_FILE}" | awk '{print $1}')"
  PREV_HASH=""
  [[ -f "${HASH_FILE}" ]] && PREV_HASH="$(cat "${HASH_FILE}" || true)"

  if [[ "${REQ_HASH}" != "${PREV_HASH}" ]]; then
    echo "[run-python] Installing/updating deps (hash changed)"
    if [[ "${PY_RUNNER_MODE}" == "venv" ]]; then
      "${VENV_DIR}/bin/pip" install ${PIP_EXTRA_ARGS} -r "${REQ_FILE}"
    else
      python3 -m pip install ${PIP_EXTRA_ARGS} -r "${REQ_FILE}"
    fi
    echo "${REQ_HASH}" > "${HASH_FILE}"
  else
    echo "[run-python] Deps OK (hash match)"
  fi
fi

echo "[run-python] Running: ${PY_BIN} ${SCRIPT} $*"
exec "${PY_BIN}" "${SCRIPT}" "$@"
'@

$runRust = @'
#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-}"
if [[ -z "${TARGET}" ]]; then
  echo "[run-rust] Usage:"
  echo "  run-rust /scripts/my_project_dir [-- cargo-args]"
  echo "  run-rust /scripts/tool.rs [-- args]"
  exit 2
fi
shift || true

CACHE_DIR="${RUST_CACHE_DIR:-/opt/cronicle/data/rust}"
mkdir -p "${CACHE_DIR}/cargo" "${CACHE_DIR}/target" "${CACHE_DIR}/bin"

export CARGO_HOME="${CACHE_DIR}/cargo"
export CARGO_TARGET_DIR="${CACHE_DIR}/target"

if [[ -d "${TARGET}" && -f "${TARGET}/Cargo.toml" ]]; then
  echo "[run-rust] Cargo project detected: ${TARGET}"
  cd "${TARGET}"
  exec cargo run --release -- "$@"
fi

if [[ -f "${TARGET}" && "${TARGET}" == *.rs ]]; then
  echo "[run-rust] Rust file detected: ${TARGET}"
  BIN_ID="$(printf "%s" "${TARGET}" | sha256sum | awk '{print $1}')"
  OUT="${CACHE_DIR}/bin/${BIN_ID}"

  SRC_HASH="$(sha256sum "${TARGET}" | awk '{print $1}')"
  HASH_FILE="${OUT}.sha256"
  PREV_HASH=""
  [[ -f "${HASH_FILE}" ]] && PREV_HASH="$(cat "${HASH_FILE}" || true)"

  if [[ ! -x "${OUT}" || "${SRC_HASH}" != "${PREV_HASH}" ]]; then
    echo "[run-rust] Compiling (changed/new)"
    rustc -O "${TARGET}" -o "${OUT}"
    echo "${SRC_HASH}" > "${HASH_FILE}"
  else
    echo "[run-rust] Binary OK (cached)"
  fi

  exec "${OUT}" "$@"
fi

echo "[run-rust] ERROR: Not a Cargo project nor a .rs file: ${TARGET}" >&2
exit 2
'@

$runShell = @'
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "[run-shell] Usage:"
  echo "  run-shell /scripts/do_something.sh"
  echo "  run-shell -- 'echo hello && ls -la'"
  exit 2
fi

if [[ "${1}" == "--" ]]; then
  shift
  exec bash -lc "$*"
fi

SCRIPT="${1}"
shift || true

if [[ ! -f "${SCRIPT}" ]]; then
  echo "[run-shell] ERROR: Script not found: ${SCRIPT}" >&2
  exit 2
fi

chmod +x "${SCRIPT}" || true
exec bash "${SCRIPT}" "$@"
'@

$runPs1 = @'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT="${1:-}"
if [[ -z "${SCRIPT}" ]]; then
  echo "[run-ps1] Usage: run-ps1 /scripts/your_script.ps1 [args...]" >&2
  exit 2
fi
shift || true

if ! command -v pwsh >/dev/null 2>&1; then
  echo "[run-ps1] ERROR: pwsh not installed in this image (WITH_POWERSHELL=false?)" >&2
  exit 2
fi

if [[ ! -f "${SCRIPT}" ]]; then
  echo "[run-ps1] ERROR: Script not found: ${SCRIPT}" >&2
  exit 2
fi

exec pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "${SCRIPT}" "$@"
'@

Write-FileLF (Join-Path -Path $customDir -ChildPath "Dockerfile")  $dockerfile
Write-FileLF (Join-Path -Path $customDir -ChildPath "run-python")  $runPython
Write-FileLF (Join-Path -Path $customDir -ChildPath "run-rust")    $runRust
Write-FileLF (Join-Path -Path $customDir -ChildPath "run-shell")   $runShell
Write-FileLF (Join-Path -Path $customDir -ChildPath "run-ps1")     $runPs1

Write-Host "Custom-Dockerfiles creados en: $customDir"

# ---------------------------
# Patch del docker-compose: insertar servicio cronicle al final de services:
# ---------------------------
$composeText = Get-Content -LiteralPath $ComposePath -Raw

if ($composeText -match '(?m)^\s*cronicle:\s*$') {
  Write-Host "El servicio 'cronicle' ya existe en el compose. No se añade nada."
}
else {
  $lines = Get-Content -LiteralPath $ComposePath
  $servicesMatch = $lines | Select-String -Pattern '^\s*services:\s*$' | Select-Object -First 1
  if (-not $servicesMatch) { throw "No encontré 'services:' en el compose. No sé dónde insertar cronicle." }

  $servicesIndex = $servicesMatch.LineNumber - 1

  # Buscar el primer top-level key (sin indent) después del bloque services:
  $insertIndex = $lines.Count
  for ($i = $servicesIndex + 1; $i -lt $lines.Count; $i++) {
    $line = $lines[$i]
    if ($line -match '^[A-Za-z0-9_.-]+\s*:' -and $line -notmatch '^\s') {
      $insertIndex = $i
      break
    }
  }

  $cronicleBlock = @(
    ""
    "  # --- Cronicle (orquestador de scripts) ---"
    "  cronicle:"
    "    build:"
    "      context: ."
    "      dockerfile: ./Custom-Dockerfiles/cronicle-python/Dockerfile"
    "      args:"
    "        CRONICLE_TAG: ""0.9.80"""
    "        WITH_RUST: ""true"""
    "        WITH_POWERSHELL: ""true"""
    "        PWSH_VERSION: ""7.5.4"""
    "    image: cronicle-python:0.9.80"
    "    container_name: cronicle"
    "    hostname: cronicle"
    "    ports:"
    "      - ""3012:3012"""
    "    environment:"
    "      TZ: ""Europe/Madrid"""
    "      CRONICLE_base_app_url: ""http://localhost:3012"""
    "      PY_RUNNER_MODE: ""venv"""
    "      PY_VENV_DIR: ""/opt/cronicle/data/python/venv"""
    "      PY_PIP_CACHE_DIR: ""/opt/cronicle/data/python/pip-cache"""
    "      PY_STATE_DIR: ""/opt/cronicle/data/python/state"""
    "      RUST_CACHE_DIR: ""/opt/cronicle/data/rust"""
    "      ARTIFACTS_DIR: ""/artifacts"""
    "      PYTHONUNBUFFERED: ""1"""
    "    volumes:"
    "      - ""E:/Docker_folders/cronicle/data:/opt/cronicle/data"""
    "      - ""E:/Docker_folders/cronicle/logs:/opt/cronicle/logs"""
    "      - ""E:/Docker_folders/cronicle/plugins:/opt/cronicle/plugins"""
    "      - ""E:/Docker_folders/cronicle/artifacts:/artifacts"""
    "      - ""E:/Docker_folders/_scripts:/scripts"""
    "      - ""D:/:/host/D"""
    "      - ""E:/:/host/E"""
    "      - ""F:/:/host/F"""
    "    restart: unless-stopped"
    "    healthcheck:"
    "      test: [""CMD-SHELL"", ""curl -fsS http://localhost:3012/api/app/ping >/dev/null || exit 1""]"
    "      interval: 30s"
    "      timeout: 5s"
    "      retries: 5"
  )

  $before = @()
  if ($insertIndex -gt 0) { $before = $lines[0..($insertIndex - 1)] }

  $after = @()
  if ($insertIndex -lt $lines.Count) { $after = $lines[$insertIndex..($lines.Count - 1)] }

  $newLines = @($before + $cronicleBlock + $after)

  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllLines($ComposePath, $newLines, $utf8NoBom)

  Write-Host "Servicio 'cronicle' añadido correctamente dentro de 'services:'"
}

Write-Host ""
Write-Host "LISTO."
Write-Host "1) Backup: $backupPath"
Write-Host "2) Levanta Cronicle:"
Write-Host "   cd `"$ProjectRoot`""
Write-Host "   docker compose up -d --build cronicle"
Write-Host ""
Write-Host "Recuerda: en Docker Desktop comparte D:, E:, F: (File Sharing) para que los mounts funcionen."
