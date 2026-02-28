# Zenomedia Server — stack multimedia/IPTV + automatización + IA (Docker Compose)

> **Estado del repo:** pensado para correr en un host Windows (Docker Desktop + WSL2) con un layout de discos tipo `E:\Docker_folders\...` (configs) y `F:\...` (media).

Este repositorio es un **monorepo operativo**: contiene el `docker-compose.yml` “hardened”, scripts auxiliares (Python/PowerShell/Bash) y utilidades específicas para tu flujo (Jellyfin, IPTV/Dispatcharr, TubeArchivist, Cronicle, VPN Mullvad, etc.).

## Objetivo y arquitectura

La idea es tener un **homelab media-centric** con:

- **Streaming:** Jellyfin (+ jfa-go, Jellystat).
- **Ingesta/automatización:** ecosistema `*arr` + qBittorrent + Recyclarr/Notifiarr.
- **YouTube:** TubeArchivist (video y audio/podcast) + herramientas de export/retag.
- **IPTV / TV:** Dispatcharr (curación) + ErsatzTV (canales virtuales) + NextPVR (PVR) + WebGrab+Plus (EPG).
- **Automatización general:** Cronicle (scheduler) + n8n.
- **Reverse proxy y exposición controlada:** Nginx Proxy Manager + Cloudflare Tunnel/DDNS.
- **Observabilidad:** Netdata + Dozzle + Uptime Kuma + healthchecks + autoheal.
- **IA local:** Ollama (CPU/NVIDIA/AMD) + Qdrant + SearXNG.

Diagrama conceptual (alto nivel):

```text
                 ┌──────────────┐
                 │  Descargas   │  (qBittorrent) 
                 └──────┬───────┘
                        │
     ┌───────────┐  ┌───▼─────────────────┐
     │ Indexers   │  │  Automatización     │
     │ Prowlarr/  │  │  Sonarr/Radarr/...  │
     │ Jackett    │  └───┬─────────────────┘
     └───────────┘      │ organiza/renombra
                         ▼
                   ┌───────────┐
                   │  Media en  │ (F:/..., E:/...)
                   │  discos    │
                   └────┬──────┘
                        │
                  ┌─────▼──────┐
                  │  Jellyfin  │  ←→ Jellystat
                  └────────────┘

 IPTV:  M3U/XMLTV ──► Dispatcharr ──(netns)──► Gluetun(Mullvad) ──► proveedor
                           │
                           ├──► Jellyfin Live TV (tuner URL)
                           ├──► NextPVR / clientes
                           └──► ErsatzTV (canales virtuales) + WebGrab+Plus (EPG)

 Automatización: Cronicle ↔ scripts (Python/Bash/Pwsh) + artifacts + Docker socket
```

## Requisitos previos (host)

- **Docker Desktop** con Compose v2 (Windows 11 + WSL2).
- **Rutas de disco** adaptadas a tu máquina. Este stack asume:
  - `E:\Docker_folders\...` para persistencia de apps (configs/db/artifacts).
  - `F:\...` para bibliotecas multimedia (Series, Películas, Anime, etc.).
  - `D:\Descargas` (o similar) para descargas.
- **GPU (opcional pero recomendado):**
  - NVIDIA: Jellyfin/Unmanic/Ollama-gpu usan reservas GPU (`deploy.resources.reservations.devices`).
  - AMD: `ollama-gpu-amd` usa mounts de dispositivos (`/dev/kfd`, `/dev/dri`) y requiere Linux/WSL2 con soporte.

## Filosofía de hardening de red

Este compose intenta que la mayoría de UIs queden expuestas **solo a loopback** (`127.0.0.1`) o a la **IP LAN** del host (`192.168.1.113`) para servicios que sí quieres ver desde otros dispositivos.

**Importante:** hay servicios que siguen escuchando en `0.0.0.0` (todas las interfaces). Los más relevantes en el compose actual son:

- Puertos sin IP explícita (expuestos en todas las interfaces):
  - `qbittorrent` → `6881:6881`
  - `qbittorrent` → `6881:6881`
  - `ersatztv` → `8409:8409`
  - `nextpvr` → `8866:8866`
  - `nextpvr` → `16891:16891`
  - `nextpvr` → `8026:8026`
  - `nginx-proxy-manager` → `80:80`
  - `nginx-proxy-manager` → `443:443`
  - `nginx-proxy-manager` → `81:81`
  - `gpodder` → `33131:3000`
  - `cronicle` → `3012:3012`
  - `vpn-stable` → `9191:9191`
  - `vpn-stable` → `8000:8000`

Si tu objetivo es hardening estricto, cambia estos mappings a `127.0.0.1:` o a la IP LAN concreta.

## Estructura del repositorio

Árbol (sin `.git`):

```text
zenomedia-server/
  .git/ (excluded)
  Custom-Dockerfiles/
    cronicle-python/
      Dockerfile
      run-ps1
      run-python
      run-rust
      run-shell
    Dockerfile.env-crypto
  Custom-Tools-Scripts/
    arbol-de-contenidos.py
    filter-m3u.py
    inventario_anime.txt
    m3u-selection.py
    Massive-copy-by-date.py
    Massive-date-change.py
    Massive-mp4-to-mkv-converter.py
    Massive-rename-files.py
    Mkv-Converter.py
    png-blanco-y-negro.py
    Recortar-video.py
    scan-m3u-to-csv.py
    tag-mp3-ons.py
    transcode-needed-or-not.py
    Unir-videos-secuencial.py
  Docker-compose-Backups/
    docker-compose-BACKUP-20260124.yml
    docker-compose-BACKUP-20260209yml
    docker-compose-BACKUP-prehardening-20260125.yml
    docker-compose-PENDIENTES-ADD.yml
  env-backups/
    .env.bkp.20260125162957
    .env.enc.bkp.20260125162932
    .env.enc.bkp.20260204132641
    .env.general_backup
  IPTV-API/
    m3u-purge-fhd.py
    review-channel.py
  Scripts/
    env-crypto.sh
    poscast-exporter.py
  Style/
    jellyfin-style.css
  watchdog/
    Dockerfile
    README.md
    requirements.txt
    watchdog.py
  Youtube-tools/
    export-youtube-video-to-mp3-renamed.py
    list-youtube-channels-from-id.py
  .env
  .env.enc
  .env.example
  .gitignore
  compose-healcheck-review.py
  docker-compose.yml
  README.md
  repair-compose.ps1
  seed-cronicle-demo-scripts.ps1
  setup-cronicle.ps1
  stack-up.sh
  vpn_autotest.log
  vpn_autotest.py
  vpn_autotest.sqlite
```

### ¿Qué es cada carpeta/archivo?

- **`docker-compose.yml`**: stack principal (versión hardened). Contiene ~70 servicios y perfiles para CPU/GPU y tareas de provisioning.
- **`.env.example`**: plantilla de variables (tokens, passwords, URLs). Copia a `.env` (o descifra `.env.enc`) y rellena.
- **`.env.enc`**: versión cifrada del `.env` (se versiona en git).
- **`env-backups/`**: backups automáticos de `.env` y `.env.enc` generados por `Scripts/env-crypto.sh`.
- **`Custom-Dockerfiles/`**: imágenes propias:
  - `cronicle-python/`: Cronicle extendido con Python+venv, ffmpeg, Rust y PowerShell + runners.
  - `Dockerfile.env-crypto`: Alpine + OpenSSL para cifrar/descifrar `.env` desde un contenedor.
- **`Custom-Tools-Scripts/`**: utilidades ad-hoc (media, M3U, filesystem, tagging, etc.).
- **`IPTV-API/`**: scripts enfocados a Dispatcharr/M3U (purga, review con ffprobe, cuarentena).
- **`Youtube-tools/`**: scripts para exportación de audio/metadata y consultas a YouTube Data API.
- **`Scripts/`**: scripts “core” del repo (gestión `.env`, export podcast avanzado, etc.).
- **`Style/`**: CSS de Jellyfin (tema ElegantFin + ajustes propios).
- **`watchdog/`**: mini-proyecto Python (contenedor) para vigilar `vpn-stable` y recrear dependientes cuando cambia el netns.
- **`Docker-compose-Backups/`**: snapshots/variantes de compose (pre-hardening, pendientes, etc.).
- **`vpn_autotest.py` + `vpn_autotest.sqlite` + `vpn_autotest.log`**: harness de pruebas automatizadas de servidores WireGuard + resultados persistidos.
- **`.git/`**: historia del repositorio incluida en el zip; útil si quieres mantener commits/branches locales.

## Gestión de secretos (.env / .env.enc)

La repo está diseñada para que **no versionas el `.env` en claro**, pero sí puedas versionar un `.env.enc` cifrado.

### Flujo recomendado

1) Partir de `.env.example` y crear `.env` con tus valores.
2) Cifrar a `.env.enc` y eliminar el `.env` del repo (queda ignorado por `.gitignore`).

### Herramientas incluidas

- **Script local:** `Scripts/env-crypto.sh` (usa OpenSSL AES-256-CBC + PBKDF2).
- **Contenedor:** `env-crypto` (perfil `tools`) para ejecutar lo anterior sin instalar OpenSSL en el host.

Ejemplos:

```bash
# Ejecutar menú interactivo (desde Linux/WSL o Git Bash)
bash ./Scripts/env-crypto.sh

# Descifrar (te pedirá passphrase)
bash ./Scripts/env-crypto.sh decrypt

# Cifrar (te pedirá passphrase + confirmación)
bash ./Scripts/env-crypto.sh encrypt
```

Desde Docker (perfil tools):

```bash
docker compose --profile tools run --rm env-crypto sh -lc 'env-crypto.sh decrypt'
```

## Operación básica del stack (Compose)

### Comandos base

```bash
# Validar que el compose parsea bien
docker compose -f docker-compose.yml config

# Arrancar todo
docker compose -f docker-compose.yml up -d

# Pull + recreación (ejemplo)
docker compose -f docker-compose.yml pull
docker compose -f docker-compose.yml up -d --force-recreate

# Ver estado
docker compose -f docker-compose.yml ps
```

### Compose profiles (CPU/GPU/Provision/Tools)

- `--profile cpu`: activa `ollama-cpu`.
- `--profile gpu-nvidia`: activa `ollama-gpu` (NVIDIA).
- `--profile gpu-amd`: activa `ollama-gpu-amd` (AMD).
- `--profile provision`: tareas de seed/init DB (Guacamole initdb, pg-provisioner).
- `--profile tools`: `env-crypto`.

Ejemplo: arrancar IA con NVIDIA + herramientas:

```bash
docker compose -f docker-compose.yml --profile gpu-nvidia --profile tools up -d
```

### Arranque ordenado con `stack-up.sh` (opcional)

`stack-up.sh` intenta levantar servicios por grupos y esperar healthchecks. **Nota:** en la versión actual del compose, el script referencia algunos servicios que solo existen en backups (`firefox`, `tor-browser`, `iptvnator`, etc.).

Qué hacer si lo usas:

- Ajusta los grupos del script para que coincidan con los servicios reales de `docker-compose.yml`.
- O usa el script solo como plantilla (la lógica de `wait_ready()` es útil).

## Observabilidad y autorecuperación

### Healthchecks

Muchos servicios tienen `healthcheck` definido. Esto te da tres ventajas:

- Puedes esperar readiness (scripts).
- Puedes clasificar rápidamente fallos.
- Puedes permitir auto-restart basado en salud (con `autoheal`).

### `compose-healcheck-review.py`

```bash
python compose-healcheck-review.py --compose-file docker-compose.yml
```

### `autoheal`

Contenedor que reinicia servicios que reportan `unhealthy`. Útil como red de seguridad, pero no sustituye diagnosticar la causa.

## Catálogo de servicios (inventario completo)

Tabla resumen (todo el stack):

| Categoría | Servicio | Para qué sirve | UI/URL | Puertos (host → cont) | Profiles |
|---|---|---|---|---|---|
| Dashboards | `heimdall` | Portal de accesos (dashboard) para tus servicios. | http://127.0.0.1:7771 | `127.0.0.1:7771` → `80/tcp` | — |
| Dashboards | `homarr` | Dashboard moderno con integración Docker (estado, enlaces, widgets). | http://127.0.0.1:7575 | `127.0.0.1:7575` → `7575/tcp` | — |
| Gestión Docker | `portainer` | UI para gestionar Docker/Compose (stacks, contenedores, volúmenes). | https://127.0.0.1:9443 | `127.0.0.1:9443` → `9443/tcp`, `127.0.0.1:9000` → `9000/tcp` | — |
| Media Server | `jellyfin` | Servidor multimedia (streaming) con bibliotecas en discos F:/. | http://192.168.1.113:8096 | `192.168.1.113:8096` → `8096/tcp`, `192.168.1.113:8920` → `8920/tcp` | — |
| Media Server | `jellystat` | Estadísticas y analíticas para Jellyfin (requiere Postgres). | http://127.0.0.1:3000 | `127.0.0.1:3000` → `3000/tcp` | — |
| Media Server | `jfa-go` | Gestión de usuarios/invitaciones para Jellyfin (accounts/registration). | http://127.0.0.1:8056 | `127.0.0.1:8056` → `8056/tcp` | — |
| Media Tools | `unmanic` | Automatiza transcodificación/optimización de tu librería (usa GPU NVIDIA en este stack). | http://127.0.0.1:8888 | `127.0.0.1:8888` → `8888/tcp` | — |
| Descargas | `qbittorrent` | Cliente BitTorrent para descargas automatizadas (se integra con *arr). | http://127.0.0.1:8081 | `127.0.0.1:8080` → `8080/tcp`, `0.0.0.0:6881` → `6881/tcp`, `0.0.0.0:6881` → `6881/udp` | — |
| Arr Stack | `bazarr` | Gestión automática de subtítulos (Radarr/Sonarr). | http://127.0.0.1:6767 | `127.0.0.1:33076` → `6767/tcp` | — |
| Arr Stack | `jackett` | Indexers/trackers bridge para apps. | http://127.0.0.1:9117 | `127.0.0.1:9117` → `9117/tcp` | — |
| Arr Stack | `jellyseerr` | Portal de peticiones (request management) para Jellyfin. | http://127.0.0.1:5055 | `127.0.0.1:33080` → `5055/tcp` | — |
| Arr Stack | `lidarr` | Automatización de música. | http://127.0.0.1:8686 | `127.0.0.1:33077` → `8686/tcp` | — |
| Arr Stack | `notifiarr` | Notificaciones/telemetría para *arr y otros (Discord/monitor). | — | `127.0.0.1:5454` → `5454/tcp` | — |
| Arr Stack | `prowlarr` | Índice central de indexers para el ecosistema *arr. | http://127.0.0.1:9696 | `127.0.0.1:33079` → `9696/tcp` | — |
| Arr Stack | `radarr` | Automatización de películas. | http://127.0.0.1:7878 | `127.0.0.1:33075` → `7878/tcp` | — |
| Arr Stack | `readarr` | Automatización de libros/ebooks (descarga/organización). | http://127.0.0.1:8787 | `127.0.0.1:33078` → `8787/tcp` | — |
| Arr Stack | `recyclarr` | Sincroniza y versiona perfiles/quality definitions de Sonarr/Radarr (TRaSH-guides). | — | — | — |
| Arr Stack | `sonarr` | Automatización de series (monitor, descarga, renombrado). | http://127.0.0.1:8989 | `127.0.0.1:33074` → `8989/tcp` | — |
| YouTube | `archivist-es` | Elasticsearch para TubeArchivist (índice/búsqueda). | — | — | — |
| YouTube | `archivist-es-audio` | Elasticsearch para la instancia de audio de TubeArchivist. | — | — | — |
| YouTube | `archivist-redis` | Redis para TubeArchivist (cache/queue). | — | — | — |
| YouTube | `archivist-redis-audio` | Redis para la instancia de audio de TubeArchivist. | — | — | — |
| YouTube | `metube` | Descargas rápidas desde YouTube con yt-dlp a carpeta persistente. | http://127.0.0.1:33120 | `127.0.0.1:8081` → `8081/tcp` | — |
| YouTube | `tubearchivist` | Archivado/gestión avanzada de YouTube (vídeo) con Redis+Elasticsearch. | http://127.0.0.1:8001 | `127.0.0.1:8001` → `8000/tcp` | — |
| YouTube | `tubearchivist-audio` | Segunda instancia de TubeArchivist (audio/podcast) con su Redis+ES. | http://127.0.0.1:8002 | `127.0.0.1:8002` → `8000/tcp` | — |
| YouTube | `youtube-podcast-exporter` | Contenedor cron que exporta audio (MP3) y metadata para Jellyfin; build no incluido. | — | — | — |
| IPTV/EPG | `webgrabplus` | WebGrab+Plus para capturar EPG desde fuentes web y generar XMLTV. | http://127.0.0.1:33111 | — | — |
| IPTV/Canales | `ersatztv` | Genera canales virtuales 24/7 (lineal) a partir de tu biblioteca. | http://127.0.0.1:8409 | `0.0.0.0:8409` → `8409/tcp` | — |
| IPTV/PVR | `nextpvr` | PVR/TV backend (grabación, timeshift) compatible con clientes. | http://127.0.0.1:8866 | `0.0.0.0:8866` → `8866/tcp`, `0.0.0.0:16891` → `16891/udp`, `0.0.0.0:8026` → `8026/udp` | — |
| IPTV/Orquestación | `dispatcharr` | Curación/orquestación de M3U/XMLTV (gestión canales, proxies, etc. | http://127.0.0.1:9191 | — | — |
| Red/VPN | `vpn-stable` | Gluetun (Mullvad WireGuard) como egress VPN estable; comparte netns con clientes (Dispatcharr). | — | `0.0.0.0:9191` → `9191/tcp`, `0.0.0.0:8000` → `8000/tcp` | — |
| Red/Proxy | `nginx-proxy-manager` | Reverse proxy con UI para dominios/SSL (NPM). | http://127.0.0.1:81 | `0.0.0.0:80` → `80/tcp`, `0.0.0.0:443` → `443/tcp`, `0.0.0.0:81` → `81/tcp` | — |
| Red/Cloudflare | `cloudflare-ddns` | DDNS: actualiza registros DNS en Cloudflare con tu IP pública. | — | — | — |
| Red/Cloudflare | `cloudflared` | Cloudflare Tunnel (Zero Trust) para exponer servicios sin abrir puertos. | — | — | — |
| Búsqueda | `searxng` | Metabuscador auto-hosted (privacy) para búsquedas federadas. | http://127.0.0.1:4080 | `127.0.0.1:4080` → `8080/tcp` | — |
| IA/Vector DB | `qdrant` | Base de datos vectorial para embeddings/RAG (compatible con Ollama/Open WebUI, etc. | http://127.0.0.1:6333 | `127.0.0.1:6333` → `6333/tcp` | — |
| IA/LLM | `ollama-cpu` | Servidor Ollama en modo CPU (perfil cpu). | http://127.0.0.1:11434 | `127.0.0.1:11000` → `11434/tcp` | cpu |
| IA/LLM | `ollama-gpu` | Servidor Ollama usando GPU NVIDIA (perfil gpu-nvidia). | http://127.0.0.1:11000 | `127.0.0.1:11000` → `11434/tcp` | gpu-nvidia |
| IA/LLM | `ollama-gpu-amd` | Servidor Ollama usando dispositivos AMD (/dev/kfd,/dev/dri) (perfil gpu-amd). | http://127.0.0.1:11001 | `127.0.0.1:11000` → `11434/tcp` | gpu-amd |
| Bases de datos | `cloudbeaver` | UI para administrar BDs (Postgres/MariaDB/etc. | http://127.0.0.1:18978 | `127.0.0.1:18978` → `8978/tcp` | — |
| Bases de datos | `pg-provisioner` | Provisionador/seed de Postgres (perfil provision). | — | — | provision |
| Bases de datos | `postgres` | PostgreSQL central para n8n/Jellystat/Guacamole (y más). | postgres://127.0.0.1:5432 | `127.0.0.1:5432` → `5432/tcp` | — |
| Acceso remoto | `guacamole` | UI web para acceso remoto unificado (RDP/VNC/SSH) vía guacd + DB. | http://127.0.0.1:33170 | `127.0.0.1:33170` → `8080/tcp` | — |
| Acceso remoto | `guacamole-initdb` | Inicializa esquema de base de datos de Apache Guacamole (perfil provision). | — | — | provision |
| Acceso remoto | `guacd` | Guacamole proxy daemon (RDP/VNC/SSH). | — | — | — |
| Gaming | `romm` | RomM: catálogo/gestor de ROMs (metadata, scraping, biblioteca). | http://127.0.0.1:33110 | `127.0.0.1:33110` → `8080/tcp` | — |
| Gaming | `romm-db` | MariaDB para RomM. | — | — | — |
| Automatización | `cronicle` | Cronicle: scheduler/runner para scripts (bash/python/rust/pwsh) + artifacts. | http://127.0.0.1:3012 | `0.0.0.0:3012` → `3012/tcp` | — |
| Automatización | `n8n` | Automatización/flows (ETL, integraciones) con persistencia en Postgres. | http://127.0.0.1:5678 | `127.0.0.1:5678` → `5678/tcp` | — |
| Podcast | `gpodder` | Servidor gPodder para sincronizar podcasts entre dispositivos. | http://127.0.0.1:33131 | `0.0.0.0:33131` → `3000/tcp` | — |
| Música | `beets` | Beets: etiquetado/organización de música (con API para automatizar). | http://127.0.0.1:8337 | `127.0.0.1:8337` → `8337/tcp` | — |
| RSS | `rss-bridge` | Generador de feeds RSS desde sitios que no ofrecen RSS. | http://127.0.0.1:33137 | `127.0.0.1:33137` → `80/tcp` | — |
| Archivos | `filestash` | Filestash UI para conectores. | http://127.0.0.1:8334 | `127.0.0.1:8334` → `8334/tcp` | — |
| Archivos | `filestash_wopi` | Servicio WOPI companion para Filestash (integración Office/OnlyOffice-like). | http://127.0.0.1:9980 | `127.0.0.1:9980` → `9980/tcp` | — |
| Archivos | `filezilla` | FileZilla web/VNC client en contenedor. | http://127.0.0.1:33161 | `127.0.0.1:33161` → `5800/tcp` | — |
| Archivos | `sftpgo` | Servidor SFTPGo (SFTP/WebAdmin). | http://192.168.1.113:33163 | `192.168.1.113:33163` → `8080/tcp`, `192.168.1.113:33222` → `2022/tcp` | — |
| Dev/Docs | `swagger-editor` | Swagger Editor: editor de OpenAPI en navegador. | http://127.0.0.1:33154 | `127.0.0.1:33154` → `80/tcp` | — |
| Dev/Docs | `swagger-ui` | Swagger UI: visor de OpenAPI specs. | http://127.0.0.1:33150 | `127.0.0.1:33150` → `8080/tcp` | — |
| Dev | `github-desktop` | GitHub Desktop en contenedor (GUI vía web/VNC). | http://127.0.0.1:33192 | `127.0.0.1:33192` → `3000/tcp`, `127.0.0.1:33193` → `3001/tcp` | — |
| Dev | `openvscode` | VS Code en navegador (OpenVSCode Server). | http://127.0.0.1:33191 | `127.0.0.1:33191` → `3000/tcp` | — |
| Seguridad | `bitwarden-lite` | Bitwarden Lite: vault minimalista auto-hosted. | http://127.0.0.1:65121 | `127.0.0.1:65121` → `8080/tcp` | — |
| Herramientas | `env-crypto` | Contenedor Alpine con OpenSSL para cifrar/descifrar . | — | — | tools |
| Diagnóstico | `test-gpu` | Contenedor de prueba que ejecuta nvidia-smi periódicamente para validar GPU. | — | — | — |
| Observabilidad | `dozzle` | Viewer de logs en tiempo real para contenedores. | http://127.0.0.1:9999 | `127.0.0.1:9999` → `8080/tcp` | — |
| Observabilidad | `netdata` | Monitorización de host/containers con métricas de alto detalle. | http://127.0.0.1:19999 | `127.0.0.1:19999` → `19999/tcp` | — |
| Observabilidad | `uptime-kuma` | Monitorización de endpoints/servicios con alertas y panel de estado. | http://127.0.0.1:3001 | `127.0.0.1:3001` → `3001/tcp` | — |
| Operación | `autoheal` | Reinicia contenedores unhealthy automáticamente (según labels/healthchecks). | — | — | — |
| Lectura | `calibre` | Calibre web/GUI en contenedor para gestionar ebooks y librerías. | http://127.0.0.1:33115 | `127.0.0.1:33084` → `8080/tcp`, `127.0.0.1:33085` → `8181/tcp`, `127.0.0.1:33087` → `8081/tcp` | — |
| Lectura | `komga` | Servidor para comics/manga (CBZ/CBR/PDF) con biblioteca en disco. | http://127.0.0.1:25600 | `127.0.0.1:33083` → `25600/tcp` | — |

## Detalle por servicio (uno por uno)

### Dashboards

### `heimdall`

**Categoría:** Dashboards

Portal de accesos (dashboard) para tus servicios.

**UI / Endpoint típico:** http://127.0.0.1:7771

**Imagen:** `linuxserver/heimdall`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:7771` → `80/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/heimdall` → `/config`

**Variables de entorno (keys):** `PGID`, `PUID`, `TZ`

### `homarr`

**Categoría:** Dashboards

Dashboard moderno con integración Docker (estado, enlaces, widgets).

**UI / Endpoint típico:** http://127.0.0.1:7575

**Imagen:** `ghcr.io/homarr-labs/homarr:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:7575/api/health/ready >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:7575/api/health/ready >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:7575` → `7575/tcp`

**Volúmenes / persistencia:**
- `/var/run/docker.sock` → `/var/run/docker.sock`
- `E:/Docker_folders/homarr/appdata` → `/appdata`

**Variables de entorno (keys):** `SECRET_ENCRYPTION_KEY`

### Gestión Docker

### `portainer`

**Categoría:** Gestión Docker

UI para gestionar Docker/Compose (stacks, contenedores, volúmenes).

**UI / Endpoint típico:** https://127.0.0.1:9443

**Imagen:** `portainer/portainer-ce:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:9000/api/status >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:9000/api/status >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:9443` → `9443/tcp`, `127.0.0.1:9000` → `9000/tcp`

**Volúmenes / persistencia:**
- `/var/run/docker.sock` → `/var/run/docker.sock`
- `E:/Docker_folders/portainer/portainer_data` → `/data`

**Variables de entorno (keys):** —

### Media Server

### `jellyfin`

**Categoría:** Media Server

Servidor multimedia (streaming) con bibliotecas en discos F:/... y aceleración GPU.

**UI / Endpoint típico:** http://192.168.1.113:8096

**Imagen:** `jellyfin/jellyfin:latest`
**Restart policy:** `unless-stopped`
**GPU/Deploy:** `{'resources': {'reservations': {'devices': [{'driver': 'nvidia', 'count': 'all', 'capabilities': ['gpu']}]}}}`
**Healthcheck:** `['CMD', 'curl', '-fsS', 'http://localhost:8096/health']` (interval=30s, retries=5)
**Puertos:** `192.168.1.113:8096` → `8096/tcp`, `192.168.1.113:8920` → `8920/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/jellifyn/jellyfin-web` → `/jellyfin/jellyfin-web`
- `F:/Animacion` → `/media/Animacion`
- `F:/Infantil` → `/media/Infantil`
- `F:/Peliculas` → `/media/Peliculas`
- `F:/Documentales` → `/media/Documentales`
- `F:/Series` → `/media/Series`
- `F:/MiniSeries` → `/media/MiniSeries`
- `F:/Anime` → `/media/Anime`
- `F:/Futbol` → `/media/Futbol`
- `F:/Baloncesto` → `/media/Baloncesto`
- `F:/NFL` → `/media/NFL`
- `F:/Combates` → `/media/Combates`
- `F:/LoL` → `/media/LoL`
- `F:/Olimpiadas` → `/media/Olimpiadas`
- `F:/Podcasts` → `/media/Podcast`
- `F:/Ambience` → `/media/Ambience`
- `E:/Animacion` → `/media/Animacion2`
- `E:/Infantil` → `/media/Infantil2`
- `E:/Peliculas` → `/media/Peliculas2`
- `E:/Series` → `/media/Series2`
- `E:/MiniSeries` → `/media/MiniSeries2`
- `E:/YouTube` → `/media/YouTube2`
- `E:/Anime` → `/media/Anime2`
- `E:/Documentales` → `/media/Documentales2`
- `E:/Futbol` → `/media/Futbol2`
- `E:/Baloncesto` → `/media/Baloncesto2`
- `E:/NFL` → `/media/NFL2`
- `E:/Combates` → `/media/Combates2`
- `E:/LoL` → `/media/LoL2`
- `E:/Olimpiadas` → `/media/Olimpiadas2`
- `E:/Podcasts` → `/media/Podcast2`
- `E:/Youtube_Podcast` → `/media/Youtube_podcast`
- `E:/YouTube_videos` → `/media/Youtube_videos`
- `E:/Ambience` → `/media/Ambience2`
- `E:/Conciertos` → `/media/Conciertos2`
- `E:/Docker_folders/jellifyn/metadata` → `/config`
- `E:/Docker_folders/jellifyn/cache` → `/cache`
- `E:/Docker_folders/jellifyn/transcodes` → `/transcode`
- `E:/Grabaciones` → `/recordings`
- `E:/Docker_folders/jellifyn/scripts` → `/scripts`

**Variables de entorno (keys):** `JELLYFIN_PublishedServerUrl`, `LOG_LEVEL`, `NVIDIA_DRIVER_CAPABILITIES`, `NVIDIA_VISIBLE_DEVICES`, `TZ`, `UMASK`

### `jellystat`

**Categoría:** Media Server

Estadísticas y analíticas para Jellyfin (requiere Postgres).

**UI / Endpoint típico:** http://127.0.0.1:3000

**Imagen:** `cyfershepard/jellystat:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:3000 >/dev/null) || (command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:3000 >/dev/null 2>&1) || exit 1']` (interval=1m, retries=3)
**Puertos:** `127.0.0.1:3000` → `3000/tcp`

**Volúmenes / persistencia:**
—

**Variables de entorno (keys):** `JELLYFIN_URL`, `JWT_SECRET`, `POSTGRES_DB`, `POSTGRES_IP`, `POSTGRES_PASSWORD`, `POSTGRES_PORT`, `POSTGRES_USER`, `TZ`

**Dependencias:** `{'postgres': {'condition': 'service_healthy'}}`

### `jfa-go`

**Categoría:** Media Server

Gestión de usuarios/invitaciones para Jellyfin (accounts/registration).

**UI / Endpoint típico:** http://127.0.0.1:8056

**Imagen:** `hrfee/jfa-go:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8056/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8056/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:8056` → `8056/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/jfa-go` → `/data`
- `E:/Docker_folders/jellifyn/metadata` → `/jf:ro`

**Variables de entorno (keys):** `JELLYFIN_URL`, `TZ`

**Dependencias:** `['jellyfin']`

### Media Tools

### `unmanic`

**Categoría:** Media Tools

Automatiza transcodificación/optimización de tu librería (usa GPU NVIDIA en este stack).

**UI / Endpoint típico:** http://127.0.0.1:8888

**Imagen:** `josh5/unmanic:latest`
**Restart policy:** `unless-stopped`
**GPU/Deploy:** `{'resources': {'reservations': {'devices': [{'driver': 'nvidia', 'count': 'all', 'capabilities': ['gpu', 'video', 'utility']}]}}}`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8888/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8888/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:8888` → `8888/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/unmanic/unmanic_config` → `/config`
- `E:/Docker_folders/unmanic/media_data` → `/watch`
- `F:/_Multimedia` → `/library`

**Variables de entorno (keys):** `NVIDIA_DRIVER_CAPABILITIES`, `NVIDIA_VISIBLE_DEVICES`, `PGID`, `PUID`, `TZ`

### Descargas

### `qbittorrent`

**Categoría:** Descargas

Cliente BitTorrent para descargas automatizadas (se integra con *arr).

**UI / Endpoint típico:** http://127.0.0.1:8081

**Imagen:** `lscr.io/linuxserver/qbittorrent:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8080/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8080/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:8080` → `8080/tcp`, `0.0.0.0:6881` → `6881/tcp`, `0.0.0.0:6881` → `6881/udp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/qbittorrent/qbittorrent_data` → `/config`
- `E:/Docker_folders/qbittorrent/media_data` → `/downloads`

**Variables de entorno (keys):** `PGID`, `PUID`, `TORRENTING_PORT`, `TZ`, `WEBUI_PORT`

### Arr Stack

### `bazarr`

**Categoría:** Arr Stack

Gestión automática de subtítulos (Radarr/Sonarr).

**UI / Endpoint típico:** http://127.0.0.1:6767

**Imagen:** `lscr.io/linuxserver/bazarr:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:6767/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:6767/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33076` → `6767/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/bazarr/bazarr_data` → `/config`
- `E:/Docker_folders/bazarr/media_data` → `/downloads`
- `F:/Peliculas` → `/movies:ro`
- `F:/Series` → `/tv:ro`

**Variables de entorno (keys):** `PGID`, `PUID`, `TZ`

### `jackett`

**Categoría:** Arr Stack

Indexers/trackers bridge para apps.

**UI / Endpoint típico:** http://127.0.0.1:9117

**Imagen:** `lscr.io/linuxserver/jackett:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:9117/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:9117/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:9117` → `9117/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/jackett/jackett_data` → `/config`
- `E:/Docker_folders/jackett/media_data` → `/downloads`

**Variables de entorno (keys):** `AUTO_UPDATE`, `PGID`, `PUID`, `TZ`

### `jellyseerr`

**Categoría:** Arr Stack

Portal de peticiones (request management) para Jellyfin.

**UI / Endpoint típico:** http://127.0.0.1:5055

**Imagen:** `fallenbagel/jellyseerr:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', 'curl -fsS http://localhost:5055/api/v1/status >/dev/null || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33080` → `5055/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/jellyseerr/jellyseerr_data` → `/app/config`
- `E:/Docker_folders/jellyseerr/raiz` → `/raiz`

**Variables de entorno (keys):** `LOG_LEVEL`, `TZ`

### `lidarr`

**Categoría:** Arr Stack

Automatización de música.

**UI / Endpoint típico:** http://127.0.0.1:8686

**Imagen:** `lscr.io/linuxserver/lidarr:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8686/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8686/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33077` → `8686/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/lidarr/lidarr_data` → `/config`
- `E:/Docker_folders/lidarr/media_data` → `/downloads`

**Variables de entorno (keys):** `PGID`, `PUID`, `TZ`

### `notifiarr`

**Categoría:** Arr Stack

Notificaciones/telemetría para *arr y otros (Discord/monitor).

**Imagen:** `golift/notifiarr:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:5454/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:5454/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:5454` → `5454/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/notifiarr/notifiarr_config` → `/config`

**Variables de entorno (keys):** `PGID`, `PUID`, `TZ`

### `prowlarr`

**Categoría:** Arr Stack

Índice central de indexers para el ecosistema *arr.

**UI / Endpoint típico:** http://127.0.0.1:9696

**Imagen:** `lscr.io/linuxserver/prowlarr:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:9696/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:9696/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33079` → `9696/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/prowlarr/prowlarr_data` → `/config`

**Variables de entorno (keys):** `PGID`, `PUID`, `TZ`

### `radarr`

**Categoría:** Arr Stack

Automatización de películas.

**UI / Endpoint típico:** http://127.0.0.1:7878

**Imagen:** `lscr.io/linuxserver/radarr:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:7878/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:7878/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33075` → `7878/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/radarr/radarr_data` → `/config`
- `E:/Docker_folders/radarr/media_data` → `/downloads`
- `F:/Peliculas` → `/movies`

**Variables de entorno (keys):** `PGID`, `PUID`, `TZ`

### `readarr`

**Categoría:** Arr Stack

Automatización de libros/ebooks (descarga/organización).

**UI / Endpoint típico:** http://127.0.0.1:8787

**Imagen:** `ghcr.io/hotio/readarr:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8787/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8787/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33078` → `8787/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/readarr/readarr_data` → `/config`
- `E:/Docker_folders/readarr/descargas` → `/downloads`
- `F:/Libros` → `/books`

**Variables de entorno (keys):** `PGID`, `PUID`, `TZ`

### `recyclarr`

**Categoría:** Arr Stack

Sincroniza y versiona perfiles/quality definitions de Sonarr/Radarr (TRaSH-guides).

**Imagen:** `ghcr.io/recyclarr/recyclarr:latest`
**Restart policy:** `unless-stopped`
**Puertos:** —

**Volúmenes / persistencia:**
- `E:/Docker_folders/recyclarr/recyclarr_config` → `/config`

**Variables de entorno (keys):** `CRON_SCHEDULE`, `TZ`

### `sonarr`

**Categoría:** Arr Stack

Automatización de series (monitor, descarga, renombrado).

**UI / Endpoint típico:** http://127.0.0.1:8989

**Imagen:** `lscr.io/linuxserver/sonarr:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8989/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8989/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33074` → `8989/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/sonarr/sonarr_data` → `/config`
- `E:/Docker_folders/sonarr/media_data` → `/downloads`
- `F:/Series` → `/tv`
- `E:/Series` → `/tv2`

**Variables de entorno (keys):** `PGID`, `PUID`, `TZ`

### YouTube

### `archivist-es`

**Categoría:** YouTube

Elasticsearch para TubeArchivist (índice/búsqueda).

**Imagen:** `bbilly1/tubearchivist-es:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v curl >/dev/null 2>&1 && curl -fsS -u elastic:"$ELASTIC_PASSWORD" "http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=2s" >/dev/null) || (command -v wget >/dev/null 2>&1 && wget -qO- --user=elastic --password="$ELASTIC_PASSWORD" "http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=2s" >/dev/null 2>&1) || exit 1']` (interval=30s, retries=10)
**Puertos:** —

**Volúmenes / persistencia:**
- `E:/Docker_folders/tubearchivist/es` → `/usr/share/elasticsearch/data`

**Variables de entorno (keys):** `ELASTIC_PASSWORD`, `ES_JAVA_OPTS`, `discovery.type`, `path.repo`, `xpack.security.enabled`

### `archivist-es-audio`

**Categoría:** YouTube

Elasticsearch para la instancia de audio de TubeArchivist.

**Imagen:** `bbilly1/tubearchivist-es:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v curl >/dev/null 2>&1 && curl -fsS -u elastic:"$ELASTIC_PASSWORD" "http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=2s" >/dev/null) || (command -v wget >/dev/null 2>&1 && wget -qO- --user=elastic --password="$ELASTIC_PASSWORD" "http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=2s" >/dev/null 2>&1) || exit 1']` (interval=30s, retries=10)
**Puertos:** —

**Volúmenes / persistencia:**
- `E:/Docker_folders/tubearchivist-audio/es` → `/usr/share/elasticsearch/data`

**Variables de entorno (keys):** `ELASTIC_PASSWORD`, `ES_JAVA_OPTS`, `discovery.type`, `path.repo`, `xpack.security.enabled`

### `archivist-redis`

**Categoría:** YouTube

Redis para TubeArchivist (cache/queue).

**Imagen:** `redis:7-alpine`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD', 'redis-cli', 'ping']` (interval=30s, retries=5)
**Puertos:** —

**Volúmenes / persistencia:**
- `E:/Docker_folders/tubearchivist/redis` → `/data`

**Variables de entorno (keys):** —

### `archivist-redis-audio`

**Categoría:** YouTube

Redis para la instancia de audio de TubeArchivist.

**Imagen:** `redis:7-alpine`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD', 'redis-cli', 'ping']` (interval=30s, retries=5)
**Puertos:** —

**Volúmenes / persistencia:**
- `E:/Docker_folders/tubearchivist-audio/redis` → `/data`

**Variables de entorno (keys):** —

### `metube`

**Categoría:** YouTube

Descargas rápidas desde YouTube con yt-dlp a carpeta persistente.

**UI / Endpoint típico:** http://127.0.0.1:33120

**Imagen:** `ghcr.io/alexta69/metube:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8081/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8081/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:8081` → `8081/tcp`

**Volúmenes / persistencia:**
- `E:/YouTube` → `/downloads`
- `E:/YouTube/Videos` → `/downloads/Videos`
- `E:/YouTube/Music` → `/downloads/Music`

**Variables de entorno (keys):** `AUDIO_DOWNLOAD_DIR`, `DOWNLOAD_DIR`, `TZ`

### `tubearchivist`

**Categoría:** YouTube

Archivado/gestión avanzada de YouTube (vídeo) con Redis+Elasticsearch.

**UI / Endpoint típico:** http://127.0.0.1:8001

**Imagen:** `bbilly1/tubearchivist:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD', 'curl', '-f', 'http://localhost:8001/api/health/']` (interval=2m, retries=3)
**Puertos:** `127.0.0.1:8001` → `8000/tcp`

**Volúmenes / persistencia:**
- `E:/YouTube_videos` → `/youtube`
- `E:/Docker_folders/tubearchivist/cache` → `/cache`

**Variables de entorno (keys):** `ELASTIC_PASSWORD`, `ES_URL`, `HOST_GID`, `HOST_UID`, `REDIS_CON`, `TA_HOST`, `TA_PASSWORD`, `TA_USERNAME`, `TZ`

**Dependencias:** `['archivist-es', 'archivist-redis']`

### `tubearchivist-audio`

**Categoría:** YouTube

Segunda instancia de TubeArchivist (audio/podcast) con su Redis+ES.

**UI / Endpoint típico:** http://127.0.0.1:8002

**Imagen:** `bbilly1/tubearchivist:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD', 'curl', '-f', 'http://localhost:8002/api/health/']` (interval=2m, retries=3)
**Puertos:** `127.0.0.1:8002` → `8000/tcp`

**Volúmenes / persistencia:**
- `E:/YouTube/Audio` → `/youtube`
- `E:/Docker_folders/tubearchivist-audio/cache` → `/cache`

**Variables de entorno (keys):** `ELASTIC_PASSWORD`, `ES_URL`, `HOST_GID`, `HOST_UID`, `REDIS_CON`, `TA_HOST`, `TA_PASSWORD`, `TA_USERNAME`, `TZ`

**Dependencias:** `['archivist-es-audio', 'archivist-redis-audio']`

### `youtube-podcast-exporter`

**Categoría:** YouTube

Contenedor cron que exporta audio (MP3) y metadata para Jellyfin; build no incluido.

**Build:** `{'context': './youtube-podcast-cron'}`
**Restart policy:** `unless-stopped`
**Puertos:** —

**Volúmenes / persistencia:**
- `E:/YouTube/Audio` → `/data/src:rw`
- `E:/Youtube_Podcast` → `/data/dest:rw`

**Variables de entorno (keys):** `CRON_SCHEDULE`, `DELETE_VIDEO`, `DEST_ROOT`, `OVERWRITE_IMAGES`, `OVERWRITE_MP3`, `RUN_ON_START`, `SRC_ROOT`, `TA_ACTION`, `TA_BASE_URL`, `TA_DRY_RUN`, `TA_TOKEN`, `TA_VERIFY_SSL`, `TZ`, `YT_API_KEY`

### IPTV/EPG

### `webgrabplus`

**Categoría:** IPTV/EPG

WebGrab+Plus para capturar EPG desde fuentes web y generar XMLTV.

**UI / Endpoint típico:** http://127.0.0.1:33111

**Imagen:** `lscr.io/linuxserver/webgrabplus:latest`
**Restart policy:** `unless-stopped`
**Puertos:** —

**Volúmenes / persistencia:**
- `E:/Docker_folders/webgrabplus/config` → `/config`
- `E:/Docker_folders/epg` → `/data`

**Variables de entorno (keys):** `DOCKER_MODS`, `PGID`, `PUID`, `TZ`

### IPTV/Canales

### `ersatztv`

**Categoría:** IPTV/Canales

Genera canales virtuales 24/7 (lineal) a partir de tu biblioteca.

**UI / Endpoint típico:** http://127.0.0.1:8409

**Imagen:** `ghcr.io/ersatztv/ersatztv:latest`
**Restart policy:** `unless-stopped`
**Puertos:** `0.0.0.0:8409` → `8409/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/ersatztv/config` → `/config`
- `F` → `/:/media_f:ro`
- `E` → `/:/media_e:ro`

**Variables de entorno (keys):** `NVIDIA_DRIVER_CAPABILITIES`, `NVIDIA_VISIBLE_DEVICES`, `TZ`

**Dependencias:** `['jellyfin']`

### IPTV/PVR

### `nextpvr`

**Categoría:** IPTV/PVR

PVR/TV backend (grabación, timeshift) compatible con clientes.

**UI / Endpoint típico:** http://127.0.0.1:8866

**Imagen:** `nextpvr/nextpvr_amd64:latest`
**Restart policy:** `unless-stopped`
**Puertos:** `0.0.0.0:8866` → `8866/tcp`, `0.0.0.0:16891` → `16891/udp`, `0.0.0.0:8026` → `8026/udp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/nextpvr/config` → `/config`
- `E:/Grabaciones` → `/recordings`
- `E:/Docker_folders/nextpvr/buffer` → `/buffer`

**Variables de entorno (keys):** `TZ`

### IPTV/Orquestación

### `dispatcharr`

**Categoría:** IPTV/Orquestación

Curación/orquestación de M3U/XMLTV (gestión canales, proxies, etc.) saliendo por VPN.

**UI / Endpoint típico:** http://127.0.0.1:9191

**Imagen:** `ghcr.io/dispatcharr/dispatcharr:latest`
**Restart policy:** `unless-stopped`
**Network mode:** `service:vpn-stable`
**Puertos:** —

**Volúmenes / persistencia:**
- `E:/Docker_folders/dispatcharr/data` → `/data`
- `E:/Docker_folders/dispatcharr/iptv` → `/data/m3us`
- `E:/Docker_folders/epg` → `/data/epgs:ro`

**Variables de entorno (keys):** `CELERY_BROKER_URL`, `DISPATCHARR_ENV`, `DISPATCHARR_LOG_LEVEL`, `NVIDIA_DRIVER_CAPABILITIES`, `NVIDIA_VISIBLE_DEVICES`, `REDIS_HOST`, `TZ`

**Dependencias:** `{'vpn-stable': {'condition': 'service_healthy'}}`

### Red/VPN

### `vpn-stable`

**Categoría:** Red/VPN

Gluetun (Mullvad WireGuard) como egress VPN estable; comparte netns con clientes (Dispatcharr).

**Imagen:** `qmcgaw/gluetun:v3.41.1`
**Restart policy:** `unless-stopped`
**cap_add:** `['NET_ADMIN']`
**Healthcheck:** `['CMD-SHELL', '/gluetun-entrypoint healthcheck']` (interval=10s, retries=3)
**Puertos:** `0.0.0.0:9191` → `9191/tcp`, `0.0.0.0:8000` → `8000/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/gluetun` → `/gluetun`

**Variables de entorno (keys):** `FIREWALL_DEBUG`, `FIREWALL_INPUT_PORTS`, `FIREWALL_OUTBOUND_SUBNETS`, `HEALTH_ICMP_TARGET_IPS`, `HEALTH_RESTART_VPN`, `HEALTH_TARGET_ADDRESSES`, `LOG_LEVEL`, `SERVER_COUNTRIES`, `TZ`, `VPN_SERVICE_PROVIDER`, `VPN_TYPE`, `WIREGUARD_ADDRESSES`, `WIREGUARD_PRIVATE_KEY`

### Red/Proxy

### `nginx-proxy-manager`

**Categoría:** Red/Proxy

Reverse proxy con UI para dominios/SSL (NPM).

**UI / Endpoint típico:** http://127.0.0.1:81

**Imagen:** `jc21/nginx-proxy-manager:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:81/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:81/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `0.0.0.0:80` → `80/tcp`, `0.0.0.0:443` → `443/tcp`, `0.0.0.0:81` → `81/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/nginx/npm_data` → `/data`
- `E:/Docker_folders/nginx/npm_letsencrypt` → `/etc/letsencrypt`

**Variables de entorno (keys):** `DISABLE_IPV6`, `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_PASSWORD`, `TZ`

### Red/Cloudflare

### `cloudflare-ddns`

**Categoría:** Red/Cloudflare

DDNS: actualiza registros DNS en Cloudflare con tu IP pública.

**Imagen:** `favonia/cloudflare-ddns:latest`
**Restart policy:** `unless-stopped`
**Puertos:** —

**Volúmenes / persistencia:**
—

**Variables de entorno (keys):** `CLOUDFLARE_API_TOKEN`, `DOMAINS`, `IP6_PROVIDER`, `PROXIED`, `UPDATE_CRON`

### `cloudflared`

**Categoría:** Red/Cloudflare

Cloudflare Tunnel (Zero Trust) para exponer servicios sin abrir puertos.

**Imagen:** `cloudflare/cloudflared:latest`
**Restart policy:** `unless-stopped`
**Puertos:** —

**Volúmenes / persistencia:**
—

**Variables de entorno (keys):** —

**Dependencias:** `['nginx-proxy-manager']`

### Búsqueda

### `searxng`

**Categoría:** Búsqueda

Metabuscador auto-hosted (privacy) para búsquedas federadas.

**UI / Endpoint típico:** http://127.0.0.1:4080

**Imagen:** `searxng/searxng:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8080/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8080/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:4080` → `8080/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/SearXNG/config` → `/etc/searxng`
- `E:/Docker_folders/SearXNG/cache` → `/var/cache/searxng`

**Variables de entorno (keys):** `SEARXNG_BASE_URL`

### IA/Vector DB

### `qdrant`

**Categoría:** IA/Vector DB

Base de datos vectorial para embeddings/RAG (compatible con Ollama/Open WebUI, etc.).

**UI / Endpoint típico:** http://127.0.0.1:6333

**Imagen:** `qdrant/qdrant`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:6333/healthz >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:6333/healthz >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:6333` → `6333/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/qdrant/qdrant_storage` → `/qdrant/storage`

**Variables de entorno (keys):** —

### IA/LLM

### `ollama-cpu`

**Categoría:** IA/LLM

Servidor Ollama en modo CPU (perfil cpu).

**UI / Endpoint típico:** http://127.0.0.1:11434

**Imagen:** `ollama/ollama:latest`
**Restart policy:** `unless-stopped`
**Compose profiles:** `['cpu']`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:11434/api/version >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:11434/api/version >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:11000` → `11434/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/ollama` → `/root/.ollama`

**Variables de entorno (keys):** —

### `ollama-gpu`

**Categoría:** IA/LLM

Servidor Ollama usando GPU NVIDIA (perfil gpu-nvidia).

**UI / Endpoint típico:** http://127.0.0.1:11000

**Imagen:** `ollama/ollama:latest`
**Restart policy:** `unless-stopped`
**Compose profiles:** `['gpu-nvidia']`
**GPU/Deploy:** `{'resources': {'reservations': {'devices': [{'driver': 'nvidia', 'count': 1, 'capabilities': ['gpu']}]}}}`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:11434/api/version >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:11434/api/version >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:11000` → `11434/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/ollama` → `/root/.ollama`

**Variables de entorno (keys):** —

### `ollama-gpu-amd`

**Categoría:** IA/LLM

Servidor Ollama usando dispositivos AMD (/dev/kfd,/dev/dri) (perfil gpu-amd).

**UI / Endpoint típico:** http://127.0.0.1:11001

**Imagen:** `ollama/ollama:latest`
**Restart policy:** `unless-stopped`
**Compose profiles:** `['gpu-amd']`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:11434/api/version >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:11434/api/version >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:11000` → `11434/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/ollama` → `/root/.ollama`

**Variables de entorno (keys):** —

### Bases de datos

### `cloudbeaver`

**Categoría:** Bases de datos

UI para administrar BDs (Postgres/MariaDB/etc.).

**UI / Endpoint típico:** http://127.0.0.1:18978

**Imagen:** `dbeaver/cloudbeaver:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8978/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8978/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:18978` → `8978/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/cloudbeaver/cloudbeaver_workspace` → `/opt/cloudbeaver/workspace`
- `E:/Docker_folders/jellifyn/metadata` → `/mnt/jellyfin:ro`

**Variables de entorno (keys):** `CB_SERVER_NAME`, `CB_SERVER_URL`

**Dependencias:** `{'postgres': {'condition': 'service_healthy'}}`

### `pg-provisioner`

**Categoría:** Bases de datos

Provisionador/seed de Postgres (perfil provision). Build fuera del repo.

**Build:** `{'context': 'E:/Docker_folders/postgres/provisioner', 'dockerfile': 'Dockerfile'}`
**Restart policy:** `no`
**Compose profiles:** `['provision']`
**Puertos:** —

**Volúmenes / persistencia:**
- `E:/Docker_folders/postgres/provisioner` → `/config:ro`
- `E:/Docker_folders/postgres/provisioner/schemas` → `/schemas:ro`

**Variables de entorno (keys):** `DB_REGISTRY`, `PGHOST`, `PGPASSWORD`, `PGPORT`, `PGUSER`

**Dependencias:** `{'postgres': {'condition': 'service_healthy'}, 'guacamole-initdb': {'condition': 'service_completed_successfully'}}`

### `postgres`

**Categoría:** Bases de datos

PostgreSQL central para n8n/Jellystat/Guacamole (y más).

**UI / Endpoint típico:** postgres://127.0.0.1:5432

**Imagen:** `postgres:16-alpine`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', 'pg_isready -h 127.0.0.1 -p 5432 -U ${POSTGRES_ADMIN_USER} -d postgres']` (interval=20s, retries=10)
**Puertos:** `127.0.0.1:5432` → `5432/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/postgres/postgres_storage` → `/var/lib/postgresql/data`

**Variables de entorno (keys):** `POSTGRES_DB`, `POSTGRES_PASSWORD`, `POSTGRES_USER`, `TZ`

### Acceso remoto

### `guacamole`

**Categoría:** Acceso remoto

UI web para acceso remoto unificado (RDP/VNC/SSH) vía guacd + DB.

**UI / Endpoint típico:** http://127.0.0.1:33170

**Imagen:** `guacamole/guacamole:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8080/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8080/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33170` → `8080/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/guacamole/extensions` → `/etc/guacamole/extensions`
- `E:/Docker_folders/guacamole/lib` → `/etc/guacamole/lib`

**Variables de entorno (keys):** `BAN_ENABLED`, `GUACD_HOSTNAME`, `GUACD_PORT`, `POSTGRESQL_DATABASE`, `POSTGRESQL_ENABLED`, `POSTGRESQL_HOSTNAME`, `POSTGRESQL_PASSWORD`, `POSTGRESQL_PORT`, `POSTGRESQL_USERNAME`, `REMOTE_IP_VALVE_ENABLED`, `WEBAPP_CONTEXT`

**Dependencias:** `{'guacd': {'condition': 'service_started'}, 'postgres': {'condition': 'service_healthy'}}`

### `guacamole-initdb`

**Categoría:** Acceso remoto

Inicializa esquema de base de datos de Apache Guacamole (perfil provision).

**Imagen:** `guacamole/guacamole:latest`
**Restart policy:** `no`
**Compose profiles:** `['provision']`
**Puertos:** —

**Volúmenes / persistencia:**
- `E:/Docker_folders/postgres/provisioner/schemas` → `/schemas`

**Variables de entorno (keys):** —

### `guacd`

**Categoría:** Acceso remoto

Guacamole proxy daemon (RDP/VNC/SSH).

**Imagen:** `guacamole/guacd:latest`
**Restart policy:** `unless-stopped`
**Puertos:** —

**Volúmenes / persistencia:**
—

**Variables de entorno (keys):** —

### Gaming

### `romm`

**Categoría:** Gaming

RomM: catálogo/gestor de ROMs (metadata, scraping, biblioteca).

**UI / Endpoint típico:** http://127.0.0.1:33110

**Imagen:** `rommapp/romm:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8080/api/heartbeat >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8080/api/heartbeat >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33110` → `8080/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/romm/romm_resources` → `/romm/resources`
- `E:/Docker_folders/romm/romm_redis_data` → `/redis-data`
- `E:/ROMM/library` → `/romm/library`
- `E:/ROMM/assets` → `/romm/assets`
- `E:/ROMM/config` → `/romm/config`

**Variables de entorno (keys):** `DB_HOST`, `DB_NAME`, `DB_PASSWD`, `DB_PORT`, `DB_USER`, `IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET`, `ROMM_AUTH_SECRET_KEY`, `SCREENSCRAPER_PASSWORD`, `SCREENSCRAPER_USER`, `STEAMGRIDDB_API_KEY`

**Dependencias:** `{'romm-db': {'condition': 'service_healthy'}}`

### `romm-db`

**Categoría:** Gaming

MariaDB para RomM.

**Imagen:** `mariadb:11`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD', 'healthcheck.sh', '--connect', '--innodb_initialized']` (interval=20s, retries=10)
**Puertos:** —

**Volúmenes / persistencia:**
- `romm_db_data` → `/var/lib/mysql`

**Variables de entorno (keys):** `MARIADB_DATABASE`, `MARIADB_PASSWORD`, `MARIADB_ROOT_PASSWORD`, `MARIADB_USER`

### Automatización

### `cronicle`

**Categoría:** Automatización

Cronicle: scheduler/runner para scripts (bash/python/rust/pwsh) + artifacts.

**UI / Endpoint típico:** http://127.0.0.1:3012

**Imagen:** `cronicle-python:0.9.80`
**Build:** `{'context': './Custom-Dockerfiles/cronicle-python', 'dockerfile': 'Dockerfile', 'args': {'CRONICLE_TAG': '0.9.80', 'WITH_RUST': 'true', 'WITH_POWERSHELL': 'true', 'PWSH_VERSION': '7.5.4'}}`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', 'curl -fsS http://localhost:3012/api/app/ping >/dev/null || exit 1']` (interval=30s, retries=5)
**Puertos:** `0.0.0.0:3012` → `3012/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/cronicle/data` → `/opt/cronicle/data`
- `E:/Docker_folders/cronicle/logs` → `/opt/cronicle/logs`
- `E:/Docker_folders/cronicle/plugins` → `/opt/cronicle/plugins`
- `E:/Docker_folders/cronicle/artifacts` → `/artifacts`
- `E:/Docker_folders/_scripts` → `/scripts`
- `D` → `/:/host/D`
- `E` → `/:/host/E`
- `F` → `/:/host/F`
- `/var/run/docker.sock` → `/var/run/docker.sock`

**Variables de entorno (keys):** `ARTIFACTS_DIR`, `CRONICLE_base_app_url`, `DOCKER_HOST`, `PYTHONUNBUFFERED`, `PY_PIP_CACHE_DIR`, `PY_RUNNER_MODE`, `PY_STATE_DIR`, `PY_VENV_DIR`, `RUST_CACHE_DIR`, `TZ`

### `n8n`

**Categoría:** Automatización

Automatización/flows (ETL, integraciones) con persistencia en Postgres.

**UI / Endpoint típico:** http://127.0.0.1:5678

**Imagen:** `n8nio/n8n:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:5678/healthz >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:5678/healthz >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:5678` → `5678/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/n8n/n8n_storage` → `/home/node/.n8n`
- `E:/Docker_folders/n8n/n8n/demo-data` → `/demo-data`
- `E:/Docker_folders/n8n/shared` → `/data/shared`

**Variables de entorno (keys):** `DB_POSTGRESDB_DATABASE`, `DB_POSTGRESDB_HOST`, `DB_POSTGRESDB_PASSWORD`, `DB_POSTGRESDB_PORT`, `DB_POSTGRESDB_USER`, `DB_TYPE`, `N8N_BLOCK_ENV_ACCESS_IN_NODE`, `N8N_DIAGNOSTICS_ENABLED`, `N8N_ENCRYPTION_KEY`, `N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS`, `N8N_GIT_NODE_DISABLE_BARE_REPOS`, `N8N_PERSONALIZATION_ENABLED`, `N8N_RUNNERS_ENABLED`, `N8N_USER_MANAGEMENT_JWT_SECRET`, `OLLAMA_HOST`

**Dependencias:** `{'postgres': {'condition': 'service_healthy'}}`

### Podcast

### `gpodder`

**Categoría:** Podcast

Servidor gPodder para sincronizar podcasts entre dispositivos.

**UI / Endpoint típico:** http://127.0.0.1:33131

**Imagen:** `xthursdayx/gpodder-docker:latest`
**Restart policy:** `unless-stopped`
**Puertos:** `0.0.0.0:33131` → `3000/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/gpodder/config` → `/config`
- `E:/Docker_folders/gpodder/downloads` → `/downloads`
- `F:/Podcasts` → `/jellyfin_podcasts`

**Variables de entorno (keys):** `PASSWORD`, `PGID`, `PUID`, `TZ`

### Música

### `beets`

**Categoría:** Música

Beets: etiquetado/organización de música (con API para automatizar).

**UI / Endpoint típico:** http://127.0.0.1:8337

**Imagen:** `lscr.io/linuxserver/beets:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8337/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8337/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:8337` → `8337/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/beets` → `/config`
- `F:/Musica` → `/music`
- `E:/YouTube/Music/downloads` → `/downloads`

**Variables de entorno (keys):** `ACOUSTID_API_KEY`, `JELLYFIN_API_KEY`, `JELLYFIN_URL`, `PGID`, `PUID`, `TZ`

### RSS

### `rss-bridge`

**Categoría:** RSS

Generador de feeds RSS desde sitios que no ofrecen RSS.

**UI / Endpoint típico:** http://127.0.0.1:33137

**Imagen:** `rssbridge/rss-bridge:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33137` → `80/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/rss-bridge` → `/config`

**Variables de entorno (keys):** —

### Archivos

### `filestash`

**Categoría:** Archivos

Filestash UI para conectores.

**UI / Endpoint típico:** http://127.0.0.1:8334

**Imagen:** `machines/filestash:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8334/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8334/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:8334` → `8334/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/filestash/state` → `/app/data/state`
- `E:/Docker_folders/filestash/files` → `/app/data/files`
- `E` → `/:/mnt/host/E`
- `F` → `/:/mnt/host/F`
- `D:/Descargas` → `/mnt/host/Descargas`

**Variables de entorno (keys):** `APPLICATION_URL`, `OFFICE_FILESTASH_URL`, `OFFICE_REWRITE_URL`, `OFFICE_URL`

**Dependencias:** `['filestash_wopi']`

### `filestash_wopi`

**Categoría:** Archivos

Servicio WOPI companion para Filestash (integración Office/OnlyOffice-like).

**UI / Endpoint típico:** http://127.0.0.1:9980

**Imagen:** `collabora/code:24.04.10.2.1`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:9980/hosting/discovery >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:9980/hosting/discovery >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:9980` → `9980/tcp`

**Volúmenes / persistencia:**
—

**Variables de entorno (keys):** `aliasgroup1`, `extra_params`

### `filezilla`

**Categoría:** Archivos

FileZilla web/VNC client en contenedor.

**UI / Endpoint típico:** http://127.0.0.1:33161

**Imagen:** `jlesage/filezilla:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:5800/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:5800/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33161` → `5800/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/filezilla` → `/config:rw`
- `E` → `/:/storage/E:rw`
- `F` → `/:/storage/F:rw`
- `D:/Descargas/` → `/storage/Descargas:rw`
- `D:/Github-zen0s4ma/zenomedia-server/` → `/storage/Github:rw`

**Variables de entorno (keys):** `SECURE_CONNECTION`, `TZ`, `WEB_AUTHENTICATION`, `WEB_AUTHENTICATION_PASSWORD`, `WEB_AUTHENTICATION_USERNAME`

### `sftpgo`

**Categoría:** Archivos

Servidor SFTPGo (SFTP/WebAdmin).

**UI / Endpoint típico:** http://192.168.1.113:33163

**Imagen:** `drakkan/sftpgo:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8080/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8080/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `192.168.1.113:33163` → `8080/tcp`, `192.168.1.113:33222` → `2022/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/sftpgo/srv` → `/srv/sftpgo`
- `E:/Docker_folders/sftpgo/home` → `/var/lib/sftpgo`
- `E` → `/:/storage/E`
- `F` → `/:/storage/F`

**Variables de entorno (keys):** `SFTPGO_LOG_LEVEL`, `TZ`

### Dev/Docs

### `swagger-editor`

**Categoría:** Dev/Docs

Swagger Editor: editor de OpenAPI en navegador.

**UI / Endpoint típico:** http://127.0.0.1:33154

**Imagen:** `swaggerapi/swagger-editor:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33154` → `80/tcp`

**Volúmenes / persistencia:**
—

**Variables de entorno (keys):** —

### `swagger-ui`

**Categoría:** Dev/Docs

Swagger UI: visor de OpenAPI specs.

**UI / Endpoint típico:** http://127.0.0.1:33150

**Imagen:** `swaggerapi/swagger-ui:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8080/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8080/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33150` → `8080/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/swagger/specs` → `/usr/share/nginx/html/specs:ro`
- `E:/Docker_folders/swagger/ui/swagger-initializer.js` → `/usr/share/nginx/html/swagger-initializer.js:ro`

**Variables de entorno (keys):** —

### Dev

### `github-desktop`

**Categoría:** Dev

GitHub Desktop en contenedor (GUI vía web/VNC).

**UI / Endpoint típico:** http://127.0.0.1:33192

**Imagen:** `lscr.io/linuxserver/github-desktop:latest`
**Restart policy:** `unless-stopped`
**cap_add:** `['IPC_LOCK']`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:3000/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:3000/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33192` → `3000/tcp`, `127.0.0.1:33193` → `3001/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/github-desktop/config` → `/config`
- `E:/Docker_folders/repos` → `/repos`

**Variables de entorno (keys):** `CUSTOM_USER`, `PASSWORD`, `PGID`, `PUID`, `TZ`

### `openvscode`

**Categoría:** Dev

VS Code en navegador (OpenVSCode Server).

**UI / Endpoint típico:** http://127.0.0.1:33191

**Imagen:** `gitpod/openvscode-server:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:3000/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:3000/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33191` → `3000/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/repos` → `/home/workspace`

**Variables de entorno (keys):** —

### Seguridad

### `bitwarden-lite`

**Categoría:** Seguridad

Bitwarden Lite: vault minimalista auto-hosted.

**UI / Endpoint típico:** http://127.0.0.1:65121

**Imagen:** `ghcr.io/bitwarden/lite:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8080/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8080/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:65121` → `8080/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/bitwarden-lite` → `/etc/bitwarden`

**Variables de entorno (keys):** `BW_DB_DATABASE`, `BW_DB_PASSWORD`, `BW_DB_PORT`, `BW_DB_PROVIDER`, `BW_DB_SERVER`, `BW_DB_USERNAME`, `BW_DOMAIN`, `BW_INSTALLATION_ID`, `BW_INSTALLATION_KEY`, `TZ`, `globalSettings__disableUserRegistration`

**Dependencias:** `{'postgres': {'condition': 'service_healthy'}}`

### Herramientas

### `env-crypto`

**Categoría:** Herramientas

Contenedor Alpine con OpenSSL para cifrar/descifrar .env (perfil tools).

**Build:** `{'context': './Custom-Dockerfiles', 'dockerfile': 'Dockerfile.env-crypto'}`
**Restart policy:** `no`
**Compose profiles:** `['tools']`
**Puertos:** —

**Volúmenes / persistencia:**
- `./` → `/work`
- `./Scripts/env-crypto.sh` → `/usr/local/bin/env-crypto.sh:ro`

**Variables de entorno (keys):** —

### Diagnóstico

### `test-gpu`

**Categoría:** Diagnóstico

Contenedor de prueba que ejecuta nvidia-smi periódicamente para validar GPU.

**Imagen:** `nvidia/cuda:12.9.0-base-ubuntu22.04`
**Restart policy:** `no`
**GPU/Deploy:** `{'resources': {'reservations': {'devices': [{'driver': 'nvidia', 'count': 1, 'capabilities': ['gpu']}]}}}`
**Puertos:** —

**Volúmenes / persistencia:**
—

**Variables de entorno (keys):** —

### Observabilidad

### `dozzle`

**Categoría:** Observabilidad

Viewer de logs en tiempo real para contenedores.

**UI / Endpoint típico:** http://127.0.0.1:9999

**Imagen:** `amir20/dozzle:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8080/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8080/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:9999` → `8080/tcp`

**Volúmenes / persistencia:**
- `/var/run/docker.sock` → `/var/run/docker.sock:ro`

**Variables de entorno (keys):** `DOZZLE_LEVEL`, `TZ`

### `netdata`

**Categoría:** Observabilidad

Monitorización de host/containers con métricas de alto detalle.

**UI / Endpoint típico:** http://127.0.0.1:19999

**Imagen:** `netdata/netdata:stable`
**Restart policy:** `unless-stopped`
**cap_add:** `['SYS_PTRACE']`
**Puertos:** `127.0.0.1:19999` → `19999/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/netdata/netdata_config` → `/etc/netdata`
- `E:/Docker_folders/netdata/netdata_lib` → `/var/lib/netdata`
- `E:/Docker_folders/netdata/netdata_cache` → `/var/cache/netdata`
- `/etc/passwd` → `/host/etc/passwd:ro`
- `/etc/group` → `/host/etc/group:ro`
- `/proc` → `/host/proc:ro`
- `/sys` → `/host/sys:ro`
- `/etc/os-release` → `/host/etc/os-release:ro`

**Variables de entorno (keys):** `TZ`

### `uptime-kuma`

**Categoría:** Observabilidad

Monitorización de endpoints/servicios con alertas y panel de estado.

**UI / Endpoint típico:** http://127.0.0.1:3001

**Imagen:** `louislam/uptime-kuma:2.1.1`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', 'wget -qO- http://localhost:3001/ >/dev/null 2>&1 || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:3001` → `3001/tcp`

**Volúmenes / persistencia:**
- `E` → `/:/host/E:ro`
- `F` → `/:/host/F:ro`
- `E:/Docker_folders/uptime_kuma` → `/app/data`
- `/var/run/docker.sock` → `/var/run/docker.sock:ro`

**Variables de entorno (keys):** `TZ`

### Operación

### `autoheal`

**Categoría:** Operación

Reinicia contenedores unhealthy automáticamente (según labels/healthchecks).

**Imagen:** `willfarrell/autoheal:latest`
**Restart policy:** `unless-stopped`
**Puertos:** —

**Volúmenes / persistencia:**
- `/var/run/docker.sock` → `/var/run/docker.sock`

**Variables de entorno (keys):** `AUTOHEAL_CONTAINER_LABEL`, `AUTOHEAL_INTERVAL`

### Lectura

### `calibre`

**Categoría:** Lectura

Calibre web/GUI en contenedor para gestionar ebooks y librerías.

**UI / Endpoint típico:** http://127.0.0.1:33115

**Imagen:** `lscr.io/linuxserver/calibre:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:8080/ >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:8080/ >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33084` → `8080/tcp`, `127.0.0.1:33085` → `8181/tcp`, `127.0.0.1:33087` → `8081/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/calibre/calibre_config` → `/config`

**Variables de entorno (keys):** `PGID`, `PUID`, `TZ`

### `komga`

**Categoría:** Lectura

Servidor para comics/manga (CBZ/CBR/PDF) con biblioteca en disco.

**UI / Endpoint típico:** http://127.0.0.1:25600

**Imagen:** `gotson/komga:latest`
**Restart policy:** `unless-stopped`
**Healthcheck:** `['CMD-SHELL', '(command -v wget >/dev/null 2>&1 && wget -qO- http://localhost:25600/actuator/health >/dev/null 2>&1) || (command -v curl >/dev/null 2>&1 && curl -fsS http://localhost:25600/actuator/health >/dev/null) || exit 1']` (interval=30s, retries=5)
**Puertos:** `127.0.0.1:33083` → `25600/tcp`

**Volúmenes / persistencia:**
- `E:/Docker_folders/komga/komga_config` → `/config`
- `E:/Docker_folders/komga/komga_data` → `/data`

**Variables de entorno (keys):** `TZ`


## Scripts y herramientas del repo

### Scripts de operación (raíz del repo)

- **`compose-healcheck-review.py`**: CLI que inspecciona contenedores de un proyecto Compose y clasifica **OK / FAIL / PENDING** según `State.Status` y `Health.Status`. Útil para detectar rápidamente qué se ha roto después de `up -d`.
  - Ejemplo: `python compose-healcheck-review.py --compose-file docker-compose.yml`
- **`stack-up.sh`**: Script Bash para levantar el stack por **grupos** y esperar readiness por healthchecks. Incluye modo tolerante a fallos (`FAIL_FAST=false`) y polling.
  - Ejemplo: `bash stack-up.sh`
- **`repair-compose.ps1`**: Normaliza `docker-compose.yml` eliminando caracteres de control y reescribiendo a UTF‑8 sin BOM. Pensado para cuando el compose se corrompe por copy/paste o encoding.
  - Ejemplo: `pwsh ./repair-compose.ps1`
- **`setup-cronicle.ps1`**: Bootstrap de Cronicle: prepara persistencia en `E:\Docker_folders\cronicle`, genera Dockerfile/runners y parchea el compose (con backups).
  - Ejemplo: `pwsh ./setup-cronicle.ps1`
- **`seed-cronicle-demo-scripts.ps1`**: Crea scripts demo (bash/python/pwsh) en `E:\Docker_folders\_scripts` para probar Cronicle + artifacts.
  - Ejemplo: `pwsh ./seed-cronicle-demo-scripts.ps1 -Force`
- **`vpn_autotest.py`**: Framework de test para servidores WireGuard (Mullvad): cambia `wg0.conf`, recrea Gluetun/Dispatcharr, mide presencia de stream (status endpoint) + métricas y puntúa; persiste en SQLite.
  - Ejemplo: `python vpn_autotest.py --only-conf ch-zrh-wg-001 --stream-url "http://.../canal" --gluetun-service vpn-stable --dispatcharr-service dispatcharr`

### `Custom-Tools-Scripts/` (utilidades ad-hoc)

- **`Custom-Tools-Scripts/Massive-copy-by-date.py`**: Copia (o mueve) archivos desde una carpeta origen a una estructura destino por **año**, usando `st_mtime` (o `st_ctime` si lo cambias). Ideal para reorganizar fotos/vídeos.
- **`Custom-Tools-Scripts/Massive-date-change.py`**: Cambia fechas de creación/modificación/acceso en Windows (usa `ctypes` + WinAPI para `creation time`). Útil para normalizar librerías o corregir metadatos de filesystem.
- **`Custom-Tools-Scripts/Massive-mp4-to-mkv-converter.py`**: Conversión batch `.mp4` → `.mkv` usando `ffmpeg` (auto-detectado vía `imageio_ffmpeg`).
- **`Custom-Tools-Scripts/Massive-rename-files.py`**: Renombrado masivo para fotos basándose en EXIF (`Pillow`) o timestamps; aplica prefijo configurable.
- **`Custom-Tools-Scripts/Mkv-Converter.py`**: Conversión de grabaciones `.ts`/`.mkv`/`.mp4` con `ffmpeg`, soportando NVENC y políticas (mantener audios, borrar original, probesize/analyzeduration).
- **`Custom-Tools-Scripts/Recortar-video.py`**: Recorta un vídeo por timestamps de inicio/fin con `ffmpeg`. Incluye modo rápido `-c copy` para evitar recodificar.
- **`Custom-Tools-Scripts/Unir-videos-secuencial.py`**: Concatena vídeos numerados `1.ext, 2.ext...` en un único archivo usando el concat demuxer de `ffmpeg` sin recodificación.
- **`Custom-Tools-Scripts/arbol-de-contenidos.py`**: Escanea rutas (ej. `F:\Anime`, `E:\Anime`) y genera un inventario de contenidos (árbol) a fichero, ignorando carpetas típicas.
- **`Custom-Tools-Scripts/filter-m3u.py`**: Filtrado avanzado de M3U → export CSV + M3U ordenado: incluye/excluye por prefijos, keywords, normaliza nombres, etc.
- **`Custom-Tools-Scripts/m3u-selection.py`**: Genera un M3U nuevo seleccionando entradas cuyo URL esté presente en un `.txt` de URLs permitidas.
- **`Custom-Tools-Scripts/png-blanco-y-negro.py`**: Convierte imágenes a blanco y negro en batch con ajustes de contraste/gamma/nitidez; guarda en subcarpeta `_BW`.
- **`Custom-Tools-Scripts/scan-m3u-to-csv.py`**: Parsea un M3U y exporta a CSV atributos típicos (`tvg-id`, `tvg-name`, `group-title`) para revisión/correcciones masivas.
- **`Custom-Tools-Scripts/tag-mp3-ons.py`**: Etiquetado batch MP3 (ID3) orientado a podcasts: género, versión ID3, portada embebida (thumb/poster/auto), album por canal/episodio.
- **`Custom-Tools-Scripts/transcode-needed-or-not.py`**: Recorre vídeos y usa `ffprobe` para decidir si requieren procesamiento/transcodificación; imprime razones.

Además incluye **`inventario_anime.txt`**: ejemplo/salida de `arbol-de-contenidos.py` (puedes borrarlo si no lo usas; es un artefacto).

### `IPTV-API/`

- **`IPTV-API/m3u-purge-fhd.py`**: Elimina entradas de un M3U cuyo `tvg-name` o `group-title` contenga tokens (ej. FHD/HEVC/4K). Puede operar in-place con backup.
- **`IPTV-API/review-channel.py`**: Script contra API de Dispatcharr: autentica, obtiene streams/canales y prueba cada stream con `ffprobe` para enviar a cuarentena si falla.

### `Youtube-tools/`

- **`Youtube-tools/export-youtube-video-to-mp3-renamed.py`**: Exporta audio a MP3 desde descargas de YouTube, renombra, descarga thumbnails y opcionalmente etiqueta para Jellyfin.
- **`Youtube-tools/list-youtube-channels-from-id.py`**: Lee IDs de canal desde una carpeta (estructura de descargas) y consulta YouTube Data API para obtener títulos/metadata.

### `Scripts/`

- **`Scripts/env-crypto.sh`**: cifra/descifra `.env` ⇄ `.env.enc` con backups automáticos en `env-backups/`.
- **`Scripts/poscast-exporter.py`**: Exportador completo de podcasts desde descargas YouTube/TubeArchivist: MP3 + thumbnails + ID3 + purga/retag + sincronización con TubeArchivist (usa API).

## Estilo Jellyfin (`Style/jellyfin-style.css`)

CSS para Jellyfin basado en **ElegantFin** con overrides (variables CSS, ajustes de layout, colores, soporte Media Bar plugin). Se suele aplicar desde:

- Jellyfin → **Dashboard** → **General** → **Custom CSS** (pegando el contenido) o referenciándolo si mantienes web assets.

## VPN y robustez de netns (`vpn-stable` + `watchdog/`)

En este stack, `dispatcharr` corre con `network_mode: service:vpn-stable`, es decir: **comparte namespace de red** con Gluetun. Ventajas:

- Todo el tráfico de Dispatcharr sale por la VPN.
- Desde el exterior, expones solo los puertos en `vpn-stable` (y no en el contenedor cliente).

Riesgo/edge case conocido: si el contenedor VPN se recrea (cambia el ID), los dependientes pueden quedar “colgados” o con el netns apuntando a un ID inexistente.

Para eso existe `watchdog/`: un contenedor Python que usa la Docker API para:

- Vigilar el estado/health de la VPN.
- Detectar reinicios/cambios de ID.
- **Recrear** los dependientes que usan `NetworkMode=container:<id>` cuando hay mismatch.

Este repo incluye el código (`watchdog/watchdog.py`, `requirements.txt`, `Dockerfile`). No está integrado en `docker-compose.yml` por defecto, pero puedes añadirlo como servicio si lo necesitas.

## Variables de entorno (.env): inventario y dónde se usan

### Variables usadas por `docker-compose.yml`

| Variable | Se usa en servicios | Comentario |
|---|---|---|
| `ACOUSTID_API_KEY` | beets | Beets: lookup por AcoustID. |
| `BW_DB_PASSWORD` | bitwarden-lite | Bitwarden Lite (instalación/DB). |
| `BW_DOMAIN` | bitwarden-lite | Bitwarden Lite (instalación/DB). |
| `BW_INSTALLATION_ID` | bitwarden-lite | Bitwarden Lite (instalación/DB). |
| `BW_INSTALLATION_KEY` | bitwarden-lite | Bitwarden Lite (instalación/DB). |
| `CF_DDNS_TOKEN` | cloudflare-ddns | Tokens Cloudflare (tunnel o ddns). |
| `CF_TUNNEL_TOKEN` | cloudflared | Tokens Cloudflare (tunnel o ddns). |
| `DISPATCHARR_LOG_LEVEL` | dispatcharr | Nivel de log. |
| `ELASTIC_PASSWORD` | archivist-es, archivist-es-audio, tubearchivist, tubearchivist-audio | Password de Elasticsearch (TubeArchivist). |
| `FILEZILLA_WEB_PASS` | filezilla | Credenciales web de FileZilla container. |
| `FILEZILLA_WEB_USER` | filezilla | Credenciales web de FileZilla container. |
| `GHD_PASS` | github-desktop | Credenciales para GitHub Desktop container. |
| `GHD_USER` | github-desktop | Credenciales para GitHub Desktop container. |
| `GLUETUN_LOG_LEVEL` | vpn-stable | Nivel de log. |
| `GPODDER_PASS` | gpodder | Password admin/usuario de gPodder. |
| `GUACAMOLE_DB_PASS` | guacamole | Password DB de Guacamole. |
| `HOMARR_SECRET_ENCRYPTION_KEY` | homarr | Clave para cifrar datos internos de Homarr. |
| `IGDB_CLIENT_ID` | romm | RomM: DB y credenciales para scrapers. |
| `IGDB_CLIENT_SECRET` | romm | RomM: DB y credenciales para scrapers. |
| `JELLYFIN_API_KEY_BEETS` | beets | — |
| `JELLYFIN_PUBLISHED_URL` | jellyfin | — |
| `JELLYSTAT_DB_PASS` | jellystat | Jellystat: password DB y JWT secret. |
| `JELLYSTAT_JWT` | jellystat | Jellystat: password DB y JWT secret. |
| `N8N_ENCRYPTION_KEY` | n8n | Secretos internos de n8n. |
| `N8N_USER_MANAGEMENT_JWT_SECRET` | n8n | Secretos internos de n8n. |
| `NPM_ADMIN_EMAIL` | nginx-proxy-manager | Credenciales iniciales de NPM. |
| `NPM_ADMIN_PASSWORD` | nginx-proxy-manager | Credenciales iniciales de NPM. |
| `OLLAMA_HOST` | n8n | Binding/host interno de Ollama (según imagen/config). |
| `POSTGRES_ADMIN_PASSWORD` | pg-provisioner, postgres | Credenciales admin de Postgres (y provisión). |
| `POSTGRES_ADMIN_USER` | pg-provisioner, postgres | Credenciales admin de Postgres (y provisión). |
| `POSTGRES_N8N_DB` | n8n | DB/usuario para n8n. |
| `POSTGRES_N8N_PASSWORD` | n8n | DB/usuario para n8n. |
| `POSTGRES_N8N_USER` | n8n | DB/usuario para n8n. |
| `ROMM_AUTH_SECRET_KEY` | romm | RomM: DB y credenciales para scrapers. |
| `ROMM_DB_PASS` | romm, romm-db | RomM: DB y credenciales para scrapers. |
| `ROMM_DB_ROOT_PASS` | romm-db | RomM: DB y credenciales para scrapers. |
| `SCREENSCRAPER_PASSWORD` | romm | RomM: DB y credenciales para scrapers. |
| `SCREENSCRAPER_USER` | romm | RomM: DB y credenciales para scrapers. |
| `SERVER_COUNTRIES` | vpn-stable | Filtro países de salida para Gluetun (Mullvad). |
| `STEAMGRIDDB_API_KEY` | romm | RomM: DB y credenciales para scrapers. |
| `TA_AUDIO_HOST` | tubearchivist-audio | TubeArchivist (host/usuario/password/token según variable). |
| `TA_AUDIO_PASSWORD` | tubearchivist-audio | TubeArchivist (host/usuario/password/token según variable). |
| `TA_AUDIO_USERNAME` | tubearchivist-audio | TubeArchivist (host/usuario/password/token según variable). |
| `TA_HOST` | tubearchivist | TubeArchivist (host/usuario/password/token según variable). |
| `TA_PASSWORD` | tubearchivist | TubeArchivist (host/usuario/password/token según variable). |
| `TA_TOKEN` | youtube-podcast-exporter | TubeArchivist (host/usuario/password/token según variable). |
| `TA_USERNAME` | tubearchivist | TubeArchivist (host/usuario/password/token según variable). |
| `TZ` | dispatcharr, tubearchivist, tubearchivist-audio | — |
| `WIREGUARD_ADDRESSES` | vpn-stable | Credenciales WireGuard (Mullvad). |
| `WIREGUARD_PRIVATE_KEY` | vpn-stable | Credenciales WireGuard (Mullvad). |
| `YT_API_KEY` | youtube-podcast-exporter | YouTube Data API key (para exportadores). |

### Variables definidas en `.env.example` pero no usadas en el compose actual

`CF_NPM_TOKEN`, `DNS_ADDRESS`, `HOST_IP`, `N8N_DEFAULT_BINARY_DATA_MODE`, `PGID`, `PIHOLE_WEBPASSWORD`, `PUID`, `TA_ES_PASSWORD`

### Variables usadas por el compose pero no presentes en `.env.example`

Añádelas a tu `.env` si vas a levantar esos servicios:

`DISPATCHARR_LOG_LEVEL`, `GLUETUN_LOG_LEVEL`, `HOMARR_SECRET_ENCRYPTION_KEY`, `NPM_ADMIN_EMAIL`, `NPM_ADMIN_PASSWORD`, `OLLAMA_HOST`, `SERVER_COUNTRIES`, `TA_AUDIO_HOST`, `TA_AUDIO_PASSWORD`, `TA_AUDIO_USERNAME`, `TA_TOKEN`, `YT_API_KEY`

## Artefactos y ficheros generados

- `vpn_autotest.sqlite`: base de datos de resultados de pruebas VPN. Se puede borrar si quieres “empezar de cero”.
- `vpn_autotest.log`: log rotatorio / acumulado del autotest.
- `env-backups/*.bkp.*`: backups automáticos de `.env`/`.env.enc` (útiles para rollback).
- `Custom-Tools-Scripts/inventario_anime.txt`: salida ejemplo del inventario.

## Backups de compose (`Docker-compose-Backups/`)

Carpeta con snapshots/variantes del `docker-compose.yml` para:

- Recuperar servicios “retirados” (por ejemplo `firefox`, `tor-browser`, `iptvnator` aparecen en backups y en `stack-up.sh`).
- Comparar configuración pre-hardening vs hardened.

## Notas de seguridad

- **No subas secretos en claro**: este repo incluye scripts con valores hardcodeados (API keys/tokens) que deberías mover a `.env`/secrets. En un README no se reproducen esos valores.
- Revisa puertos expuestos en `0.0.0.0` (tabla arriba).
- Cuando uses `docker.sock` dentro de un contenedor (Cronicle, Homarr, Portainer), recuerda que es equivalente a **root** sobre Docker: limita acceso a esas UIs.

## Troubleshooting rápido (runbook)

- **`docker compose config` falla**: usa `repair-compose.ps1` (Windows) para limpiar caracteres de control/encoding.
- **Servicios “unhealthy”**: ejecuta `python compose-healcheck-review.py ...` y luego `docker logs <container>`.
- **VPN + dependientes caídos**: revisa `vpn-stable` healthcheck y considera integrar `watchdog/` para recreación automática de netns.
- **GPU no detectada**: usa el servicio `test-gpu` y revisa drivers/NVIDIA Container Toolkit/WSL2 según tu entorno.

---

**Fecha del README generado:** 2026-02-28
