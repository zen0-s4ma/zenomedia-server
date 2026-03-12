<#  stack-up.ps1 (PowerShell 5.1 compatible)
    Arranque secuencial por grupos con readiness + gate Mullvad para gluetun-stable.
    Uso:
      powershell -ExecutionPolicy Bypass -File .\stack-up.ps1
      powershell -ExecutionPolicy Bypass -File .\stack-up.ps1 --only "1,3,4"
      set COMPOSE_PROFILES=gpu-nvidia  (cmd)  /  $env:COMPOSE_PROFILES="gpu-nvidia" (pwsh)
#>

[CmdletBinding()]
param(
  [string]$only = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Coalesce([object]$val, [object]$fallback) {
  if ($null -eq $val) { return $fallback }
  $s = [string]$val
  if ($s -eq "") { return $fallback }
  return $val
}

# =========================
# Config general
# =========================
$COMPOSE_FILE        = Coalesce $env:COMPOSE_FILE "docker-compose.yml"
$PROJECT_DIR         = Coalesce $env:PROJECT_DIR (Get-Location).Path
$COMPOSE_PROFILES    = Coalesce $env:COMPOSE_PROFILES ""

$TIMEOUT_DEFAULT     = [int](Coalesce $env:TIMEOUT_DEFAULT 420)   # segundos
$SLEEP_STEP          = [int](Coalesce $env:SLEEP_STEP 2)
$INTER_GROUP_SLEEP   = [int](Coalesce $env:INTER_GROUP_SLEEP 90)
$INTER_SERVICE_SLEEP = [int](Coalesce $env:INTER_SERVICE_SLEEP 5)

$FAIL_FAST           = ((Coalesce $env:FAIL_FAST "false").ToString().ToLower() -eq "true")

# filtro opcional de grupos ("1,3,4")
$ONLY_GROUPS = $only

# =========================
# Estado / resumen
# =========================
$OK_SERVICES   = New-Object System.Collections.Generic.List[string]
$FAIL_SERVICES = New-Object System.Collections.Generic.List[string]
$FAIL_REASON   = @{}

function Log([string]$m)  { Write-Host $m }
function Warn([string]$m) { Write-Warning $m }
function Err([string]$m)  { Write-Host ("ERROR: " + $m) -ForegroundColor Red }

function Die([string]$m) { Err $m; exit 1 }

function Mark-Ok([string]$svc) { $OK_SERVICES.Add($svc) | Out-Null }
function Mark-Fail([string]$svc, [string]$reason) {
  $FAIL_SERVICES.Add($svc) | Out-Null
  $FAIL_REASON[$svc] = $reason
}

function Ts-Now {
  return [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
}

function Fmt-Duration([int]$seconds) {
  $m = [int]($seconds / 60)
  $s = [int]($seconds % 60)
  return ("{0}:{1:00}" -f $m, $s)
}

# =========================
# .env loader
# =========================
function Load-Env {
  $envPath = Join-Path $PROJECT_DIR ".env"
  if (-not (Test-Path $envPath)) { return }

  $lines = Get-Content $envPath -Raw
  foreach ($line in ($lines -split "`n")) {
    $l = $line.TrimEnd("`r")
    if (-not $l) { continue }
    if ($l.TrimStart().StartsWith("#")) { continue }

    if ($l -match '^\s*([^=]+)=(.*)\s*$') {
      $k = $matches[1].Trim()
      $v = $matches[2]
      [Environment]::SetEnvironmentVariable($k, $v, "Process")
    }
  }
}

# =========================
# docker compose wrapper
# =========================
function DC {
  param(
    [Parameter(Mandatory=$true)][string[]]$Args
  )

  $full = @("compose", "-f", $COMPOSE_FILE)

  if ($COMPOSE_PROFILES) {
    foreach ($p0 in ($COMPOSE_PROFILES -split ",")) {
      $p = $p0.Trim()
      if ($p) { $full += @("--profile", $p) }
    }
  }

  $full += $Args
  & docker @full
}

function Container-Id([string]$svc) {
  try {
    $out = DC @("ps","-q",$svc) 2>$null
    $id = ($out | Select-Object -First 1)
    if ($id) { return $id.Trim() }
    return ""
  } catch { return "" }
}

function Has-Healthcheck([string]$svc) {
  $cid = Container-Id $svc
  if (-not $cid) { return $false }
  try {
    $x = & docker inspect -f '{{if .State.Health}}yes{{else}}no{{end}}' $cid 2>$null
    return ($x.Trim() -eq "yes")
  } catch { return $false }
}

function Health-Status([string]$svc) {
  $cid = Container-Id $svc
  if (-not $cid) { return "missing" }
  try {
    $x = & docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}nohealth{{end}}' $cid 2>$null
    return $x.Trim()
  } catch { return "unknown" }
}

function Run-Status([string]$svc) {
  $cid = Container-Id $svc
  if (-not $cid) { return "missing" }
  try {
    $x = & docker inspect -f '{{.State.Status}}' $cid 2>$null
    return $x.Trim()
  } catch { return "unknown" }
}

function Wait-Ready {
  param(
    [Parameter(Mandatory=$true)][string]$svc,
    [int]$timeout = 420
  )

  $deadline = (Get-Date).AddSeconds($timeout)

  while ((Get-Date) -lt $deadline) {
    $hs = Health-Status $svc
    $rs = Run-Status $svc

    if ($rs -eq "exited" -or $rs -eq "dead") { return $false }

    if (Has-Healthcheck $svc) {
      if ($hs -eq "healthy") { return $true }
      if ($hs -eq "unhealthy") {
        try { DC @("restart",$svc) | Out-Null } catch {}
        Start-Sleep -Seconds 5
      }
    } else {
      if ($rs -eq "running") { return $true }
    }

    Start-Sleep -Seconds $SLEEP_STEP
  }

  return $false
}

function Http-Get-In-Container {
  param(
    [Parameter(Mandatory=$true)][string]$svc,
    [Parameter(Mandatory=$true)][string]$url
  )

  $cid = Container-Id $svc
  if (-not $cid) { throw "missing_container" }

  $cmd = @"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL '$url'
elif command -v wget >/dev/null 2>&1; then
  wget -qO- '$url'
else
  echo 'NO_HTTP_CLIENT'
  exit 127
fi
"@

  $out = & docker exec $cid sh -lc $cmd
  return $out
}

function Wait-Gluetun-Mullvad {
  param(
    [int]$timeout = 420
  )

  $svc = "gluetun-stable"   # ✅ tu cambio
  if (-not (Wait-Ready -svc $svc -timeout $timeout)) { return $false }

  $deadline = (Get-Date).AddSeconds($timeout)

  while ((Get-Date) -lt $deadline) {
    $body = ""
    try { $body = (Http-Get-In-Container -svc $svc -url "https://am.i.mullvad.net/json" 2>$null) } catch {}

    if ($body -match "NO_HTTP_CLIENT") { return $false }
    if ($body -match '"mullvad_exit_ip"\s*:\s*true') { return $true }

    Start-Sleep -Seconds $SLEEP_STEP
  }

  return $false
}

function Service-Pause { Start-Sleep -Seconds $INTER_SERVICE_SLEEP }

function Should-Run-Group([string]$groupName) {
  if (-not $ONLY_GROUPS) { return $true }
  $onlyArr = @()
  foreach ($x in ($ONLY_GROUPS -split ",")) {
    $t = $x.Trim()
    if ($t) { $onlyArr += $t }
  }
  return ($onlyArr -contains $groupName)
}

function Up-Group-Sequential-Clean {
  param(
    [Parameter(Mandatory=$true)][string]$groupLabel,
    [Parameter(Mandatory=$true)][string[]]$services
  )

  if (-not (Should-Run-Group $groupLabel)) { return }

  Log ""
  Log ("GRUPO {0}:" -f $groupLabel)

  $count = $services.Count
  $i = 0

  foreach ($s in $services) {
    $i++
    Log ("{0} -> Arrancando..." -f $s)
    $t0 = Ts-Now

    $upOk = $true
    try { DC @("up","-d",$s) | Out-Null } catch { $upOk = $false }

    if (-not $upOk) {
      $dt = (Ts-Now) - $t0
      Err ("{0} -> FAIL (docker compose up falló) (t = {1})" -f $s, (Fmt-Duration $dt))
      Mark-Fail $s "up_failed"
      if ($i -lt $count) { Service-Pause }
      continue
    }

    if (-not (Wait-Ready -svc $s -timeout $TIMEOUT_DEFAULT)) {
      $hs = Health-Status $s
      $rs = Run-Status $s
      $dt = (Ts-Now) - $t0
      Err ("{0} -> FAIL (no READY en {1}s | health={2} run={3}) (t = {4})" -f $s, $TIMEOUT_DEFAULT, $hs, $rs, (Fmt-Duration $dt))
      Mark-Fail $s ("timeout_ready health={0} run={1}" -f $hs, $rs)
      if ($i -lt $count) { Service-Pause }
      continue
    }

    if ($s -eq "gluetun-stable") {
      if (-not (Wait-Gluetun-Mullvad -timeout $TIMEOUT_DEFAULT)) {
        $dt = (Ts-Now) - $t0
        Err ("{0} -> FAIL (no confirmó Mullvad en {1}s) (t = {2})" -f $s, $TIMEOUT_DEFAULT, (Fmt-Duration $dt))
        Mark-Fail $s "mullvad_gate_failed"
        if ($i -lt $count) { Service-Pause }
        continue
      }
    }

    $dt = (Ts-Now) - $t0
    Log ("{0} -> OK (t = {1})" -f $s, (Fmt-Duration $dt))
    Mark-Ok $s

    if ($i -lt $count) { Service-Pause }
  }
}

# =========================
# Main
# =========================
Set-Location $PROJECT_DIR
Load-Env

Log ""
Log "STOP GLOBAL: parando TODOS los contenedores de Docker..."
$allIds = (& docker ps -q 2>$null)
if ($allIds) {
  try {
    foreach ($id in $allIds) {
      $tid = ($id + "").Trim()
      if ($tid) { & docker stop -t 20 $tid | Out-Null }
    }
    Log "✅ Todos los contenedores parados."
  } catch {
    Warn "No se pudieron parar todos los contenedores (continuo igualmente)."
  }
} else {
  Log "ℹ️ No había contenedores corriendo."
}

# =========================
# Grupos
# =========================
$GROUP_DB = @(
  "postgres",
  "romm-db",
  "archivist-redis",
  "archivist-es",
  "archivist-redis-audio",
  "archivist-es-audio"
)

$GROUP_NET = @(
  "nginx-proxy-manager",
  "cloudflare-ddns",
  "cloudflared"
)

$GROUP_VPN = @("gluetun-stable")

$GROUP_VPN_CLIENTS = @("dispatcharr")

$GROUP_DB_APPS = @(
  "n8n",
  "jellystat",
  "cloudbeaver",
  "guacd",
  "guacamole",
  "bitwarden-lite",
  "romm",
  "heimdall"
)

$GROUP_MEDIA = @(
  "tubearchivist",
  "ersatztv",
  "jfa-go",
  "webgrabplus",
  "nextpvr",
  "tubearchivist-audio"
)

$GROUP_JELLYFIN = @("jellyfin")

$GROUP_ARR = @(
  "qbittorrent",
  "prowlarr",
  "sonarr",
  "radarr",
  "bazarr",
  "lidarr",
  "readarr",
  "jackett",
  "jellyseerr",
  "recyclarr",
  "notifiarr"
)

$GROUP_UI_MISC = @(
  "uptime-kuma",
  "dozzle",
  "netdata",
  "swagger-ui",
  "swagger-editor",
  "gpodder",
  "sftpgo",
  "filestash_wopi",
  "filestash",
  "filezilla"
)

$GROUP_UI_OTHERS = @(
  "homarr",
  "komga",
  "calibre",
  "searxng",
  "qdrant",
  "rss-bridge",
  "unmanic",
  "beets",
  "iptvnator-backend",
  "iptvnator"
)

# =========================
# Ejecución
# =========================
Up-Group-Sequential-Clean -groupLabel "1"  -services $GROUP_DB
Up-Group-Sequential-Clean -groupLabel "2"  -services $GROUP_NET
Up-Group-Sequential-Clean -groupLabel "3"  -services $GROUP_VPN
Up-Group-Sequential-Clean -groupLabel "4"  -services $GROUP_VPN_CLIENTS
Up-Group-Sequential-Clean -groupLabel "5"  -services $GROUP_DB_APPS
Up-Group-Sequential-Clean -groupLabel "6"  -services $GROUP_MEDIA
Up-Group-Sequential-Clean -groupLabel "7"  -services $GROUP_JELLYFIN
# Up-Group-Sequential-Clean -groupLabel "8"  -services $GROUP_ARR
Up-Group-Sequential-Clean -groupLabel "9"  -services $GROUP_UI_MISC
# Up-Group-Sequential-Clean -groupLabel "10" -services $GROUP_UI_OTHERS

Log ""
Log "FIN ✅"
Log "Resumen:"
Log ("  OK:   {0}" -f $OK_SERVICES.Count)
Log ("  FAIL: {0}" -f $FAIL_SERVICES.Count)

if ($FAIL_SERVICES.Count -gt 0) {
  Log ""
  Log "Fallaron (pero el script siguió):"
  foreach ($s in $FAIL_SERVICES) {
    Log ("  - {0}: {1}" -f $s, $FAIL_REASON[$s])
  }
}

exit 0