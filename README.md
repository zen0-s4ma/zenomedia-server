# README — *Stack multimedia + automatización + IA con Docker Compose*

> **Objetivo**  
> Este `docker-compose.yml` levanta un ecosistema completo para **multimedia** (Jellyfin, *arr, Tdarr, Komga/Kavita, Audiobookshelf, Navidrome…), **descargas/índices** (qBittorrent, Prowlarr, Jackett, Autobrr), **monitorización** (Netdata, Dozzle, Uptime Kuma), **automatización** (n8n + Postgres), **UI/gestión** (Heimdall/Homarr/Organizr/Portainer), **buscador** (SearXNG), **reverse proxy** (Nginx Proxy Manager), **IA local** (Ollama + Open WebUI + Qdrant), **docs** (OnlyOffice), y utilidades varias (CloudBeaver, Filebrowser, Threadfin, TVHeadend, jfa-go, Jellystat…).  
> Incluye **perfiles GPU** para aceleración HW (NVIDIA/AMD), **healthchecks**, y ejemplos de **comandos Docker** para operar el stack.

---

## 1) Requisitos previos

- **Docker + Docker Compose v2** actualizados.  
- **Windows (WSL2)** o Linux. En Windows con WSL2 conviene revisar compatibilidad de rutas y permisos.  
- **GPU opcional (NVIDIA/AMD)** si usarás transcodificación o IA acelerada. Compose permite **reservar GPUs** con `deploy.resources.reservations.devices` (driver, capabilities, count/device_ids). Requiere NVIDIA Container Toolkit (en Linux) o Docker Desktop con WSL2 (en Windows) y drivers al día. citeturn22search0turn22search2turn24search3
- Puertos del host disponibles (ver tabla más abajo).

---

## 2) Estructura de almacenamiento (volúmenes)

El compose declara volúmenes nombrados para **config/cache/datos** (p. ej. `jellyfin_data`, `media_data`, `netdata_*`, `npm_*`, `ollama_storage`, `qdrant_storage`, etc.).  
Algunos servicios montan **rutas absolutas del host Windows** (`F:/...`, `D:/...`, `C:/...`) que debes ajustar a tu entorno (o a rutas Linux si lo ejecutas en WSL/Ubuntu).

> **Sugerencia**: mantén **todas** las configuraciones duraderas bajo una carpeta raíz (p. ej. `F:\_Multimedia\...`) y **haz backup** periódico de los volúmenes críticos (ver comandos más abajo).

---

## 3) Variables de entorno

Crea un `.env` junto al `docker-compose.yml` con tus secretos/URLs/usuarios. Compose soporta `environment:` y `env_file:` (incluido el flag `required` para archivos opcionales) y respeta la sobreescritura por orden de carga. citeturn15search15

**Ejemplo mínimo `.env`:**
```bash
# Base
TZ=Europe/Madrid
JELLYFIN_PUBLISHED_URL=http://tu-ip:8096

# Jellystat
JELLYSTAT_DB_PASS=pon-una-clave
JELLYSTAT_JWT=pon-otra-clave

# n8n + Postgres
POSTGRES_USER=n8n
POSTGRES_PASSWORD=pon-una-clave
POSTGRES_DB=n8n

# OnlyOffice
ONLYOFFICE_JWT_SECRET=cadena-larga-y-unica

# TubeArchivist
TA_HOSTS=admin@localhost
TA_USERNAME=admin
TA_PASSWORD=cambia_esto
TA_ES_PASSWORD=otra_clave_elastic
```

---

## 4) Puesta en marcha rápida

### 4.1 Primer arranque
```bash
# Revisar el compose (modo “dry-run”)
docker compose config

# Arrancar todo en segundo plano
docker compose up -d

# Ver estado de contenedores
docker compose ps

# Seguir logs de un servicio
docker compose logs -f jellyfin
```
Referencias a comandos Compose v2: `up`, `down`, `ps`, `logs`, `exec`, `restart`, `pull`, etc. citeturn5search2

### 4.2 Perfiles GPU (opcional)
```bash
# CPU-only (Ollama CPU y pull del modelo)
docker compose --profile cpu up -d ollama-cpu ollama-pull-llama-cpu

# NVIDIA (necesita NVIDIA Container Toolkit / GPU visible)
docker compose --profile gpu-nvidia up -d ollama-gpu ollama-pull-llama-gpu

# AMD ROCm
docker compose --profile gpu-amd up -d ollama-gpu-amd ollama-pull-llama-gpu-amd
```
> **Notas**  
> - Para NVIDIA, el soporte de GPU en Compose se define con `deploy.resources.reservations.devices` (driver `nvidia`, `capabilities: [gpu]`, `count`). citeturn22search0turn22search2  
> - Puedes validar con `docker compose run --rm test-gpu` o `docker compose up test-gpu` (ejecuta `nvidia-smi`).

---

## 5) Servicios (resumen + tips clave)

### 5.1 Jellyfin (`:8096`)
Servidor multimedia. Habilita **aceleración de hardware** (Dashboard → Playback → Transcoding) si tienes GPU (NVENC para NVIDIA, VAAPI/QSV para Intel, etc.). Sigue la guía oficial para NVIDIA/Intel y revisa **issues/limitaciones** conocidas por driver/SO. citeturn16search1turn16search0turn16search5turn16search9

### 5.2 Jellix (`:3003`) y Jellyfin Vue (`:3004`)
Frontends alternativos del ecosistema Jellyfin.

### 5.3 TubeArchivist + ES + Redis (`:8001`)
Indexa/archiva YouTube localmente. Variables: `ES_URL`, `REDIS_CON`, `TA_HOST/USER/PASS`, y credenciales de Elastic (`ELASTIC_PASSWORD`). citeturn15search11

### 5.4 jfa-go (`:8056`)
Gestión de invitaciones/usuarios para Jellyfin.

### 5.5 Jellystat (`:3000`) + Postgres
Analítica/estadísticas para Jellyfin.

### 5.6 Threadfin (`:34400`) / TVHeadend (`:9981-9982`)
IPTV proxy y backend PVR (DVR, timeshift).

### 5.7 Suite *arr*  
- **qBittorrent** (WebUI `:8080`, puertos p2p 6881 TCP/UDP)  
- **Sonarr** (`:33074`), **Radarr** (`:33075`), **Bazarr** (`:33076`), **Lidarr** (`:33077`), **Readarr** (`:33078`), **Prowlarr** (`:33079`)  
Apunta todas a `media_data:/downloads` y a las rutas finales (`/movies`, `/tv`, `/books`, etc.).

### 5.8 Jellyseerr (`:33080`)
Peticiones de usuarios para nuevas pelis/series. (Tras proxy reverso, usa encabezados `X-Forwarded-*` adecuados.)

### 5.9 Tdarr (`:33081-33082`)
Transcodificación/normalización en masa. Puede usar GPU (NVIDIA), define `ffmpegVersion=7`. (Ver docs de perfiles/plugins en el repo de Tdarr).

### 5.10 Komga/Kavita/Calibre/Calibre-Web
Librerías de cómics, ebooks y frontend de Calibre.

### 5.11 Navidrome (`:4533`)
Servidor musical compatible con Subsonic.

### 5.12 Audiobookshelf (`:13378`)
Servidor de audiolibros/podcasts.

### 5.13 Unmanic (`:8888`)
Automatiza transcodificación/optimización de tu librería (puede usar GPU).

### 5.14 Recyclarr (cron 05:00), Autobrr (`:7474`), NewTrackon (`:33101`), Notifiarr (`:5454`)
Sincronización de calidades/perfiles, auto-descarga por filtros, monitor de trackers, y notificaciones integradas.

### 5.15 Homarr (`:7575`), Heimdall (`:7771`), Organizr (`:33102`)
Dashboards/portales para centralizar accesos.

### 5.16 Portainer (`:9443`) y Dozzle (`:9999`)
Gestión de Docker y visor de logs.

### 5.17 Netdata (`:19999`)
Monitorización **en tiempo real** del host/contenedores. Para una cobertura amplia, monta `/etc/netdata`, `/var/lib/netdata`, `/var/cache/netdata` y los paths del host (`/proc`, `/sys`, `/etc/os-release`, etc.). citeturn17search0turn17search4

### 5.18 SearXNG (`:4080`)
Meta-buscador privado. Configuración en `/etc/searxng/settings.yml` (volumen). (Si lo pondrás tras proxy, ajusta `SEARXNG_BASE_URL`).

### 5.19 Nginx Proxy Manager (`:80`, `:443`, panel `:81`)
Reverse proxy con **Let’s Encrypt**. Por defecto publica 80/443 y gestiona el panel en el **81**. citeturn18search0turn18search2

### 5.20 Kometa
Metadatos/colecciones para Jellyfin.

### 5.21 n8n + Postgres (+ importador)
Automatización de flujos; configuración típica con `DB_TYPE=postgresdb` y `DB_POSTGRESDB_*`. citeturn4search2

### 5.22 Open WebUI (`:3002`) + Ollama (+ perfiles GPU)
- **Ollama** expone **11434** y persiste en `/root/.ollama`. Puedes **pre-cargar** modelos con un contenedor “init” que haga `ollama pull ...`. citeturn24search17  
- **Open WebUI** puede apuntar a Ollama externo con `OLLAMA_BASE_URL` (p. ej. `http://host.docker.internal:11000`). citeturn23search0

### 5.23 Qdrant (`:6333`)
Vector DB para embeddings/recuperación semántica. Por defecto REST en `:6333` y datos en `/qdrant/storage`. citeturn21view0

### 5.24 Uptime Kuma (`:3001`)
Monitor de servicios. Si va detrás de proxy, habilita **WebSocket** en el proxy. Nginx requiere `Upgrade`/`Connection` para WS. citeturn18search1

### 5.25 OnlyOffice Document Server (`:8088`)
Habilita **JWT** para evitar rotaciones aleatorias y romper integraciones: `JWT_ENABLED=true`, **fija** `JWT_SECRET` en el entorno y en tu conector (Nextcloud/Seafile, etc.). Mapea volúmenes de logs/data/lib/db. citeturn20search1turn20search0

### 5.26 DuckDNS (servicio DNS dinámico)
Mueve **TOKEN** al `.env`. Crea A/AAAA para tu subdominio `*.duckdns.org`.

### 5.27 Filebrowser (`:18080`), CloudBeaver (`:18978`)
Explorador de archivos y gestor de BBDD en el navegador.

### 5.28 Utilidades
- **moverpelis**: cron bucle que mueve archivos de eMule a Películas si no cambian hace >1 min.  
- **test-gpu**: `nvidia/cuda` ejecuta `nvidia-smi`.

---

## 6) Publicación bajo dominio con Nginx Proxy Manager (NPM)

> Recomendado: crear una red Docker dedicada (p. ej. `docker network create proxy`) y conectar **NPM + backends** a esa red. Evitas exponer puertos innecesarios en el host.

### 6.1 Pasos base
1) Levanta **NPM** (puertos **80/443** públicos y panel **81**). citeturn18search0  
2) Configura **DNS** del dominio/subdominios a la IP pública/NAT.  
3) En NPM → **Hosts > Proxy Hosts**: crea las entradas:
   - **Jellyfin** → `http://jellyfin:8096`  
   - **Open WebUI** → `http://open-webui:8080`  
   - **Uptime Kuma** → `http://uptime-kuma:3001`  
   - **SearXNG** → `http://searxng:8080`  
   Marca **Websockets** para apps que lo usen (Kuma, etc.). Para WS en Nginx: añade `Upgrade` + `Connection` headers. citeturn18search1  
4) **SSL**: pide certificado Let’s Encrypt (HTTP-01). Para wildcard usa **DNS-01** (proveedor soportado por NPM).  
5) Opcional: **Access List**, HSTS, redirecciones, Custom Nginx Config.

### 6.2 Snippets útiles (Nginx → “Advanced”)
```nginx
# Real IP detrás de proxy
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;

# WebSockets (WS/WSS)
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection $connection_upgrade;
```
(WS es requisito en servicios como Kuma o apps “live”). citeturn18search1

### 6.3 Casos especiales

- **Open WebUI + Ollama en el host/otra máquina**: define `OLLAMA_BASE_URL` (p. ej. `https://ia.midominio.com`) y ajusta CORS si aplica. citeturn23search0  
- **OnlyOffice**: si lo publicas tras NPM, recuerda que **JWT** debe coincidir con el del conector (Nextcloud, etc.). Evita que el secreto cambie entre reinicios. citeturn20search1  
- **Qdrant**: restringe acceso público (auth/reverse proxy con ACL) o mantenlo solo en red interna Docker. citeturn21view0

---

## 7) Comandos de uso con Docker (operación diaria)

> **Tip**: todos admiten `--project-name NOMBRE` si gestionas múltiples stacks.

### 7.1 Ciclo de vida
```bash
# Arrancar todo / servicios concretos
docker compose up -d
docker compose up -d jellyfin prowlarr qbittorrent

# Arrancar todo con perfil de GPU
docker compose --profile gpu-nvidia up -d

# Parar (manteniendo contenedores)
docker compose stop

# Reiniciar todos/uno
docker compose restart
docker compose restart jellyfin

# Apagar y eliminar contenedores (conserva volúmenes)
docker compose down

# Apagar y eliminar contenedores + redes huérfanas
docker compose down --remove-orphans
```

### 7.2 Observabilidad y shell
```bash
# Listar estado
docker compose ps

# Logs (todo / un servicio)
docker compose logs --tail=200
docker compose logs -f jellyfin

# Entrar a un contenedor
docker compose exec jellyfin bash
docker compose exec qbittorrent sh
```

### 7.3 Actualizaciones
```bash
# Traer imágenes nuevas
docker compose pull

# Recrear contenedores con misma config
docker compose up -d --pull always

# Ver qué cambiaría (diff)
docker compose diff
```

### 7.4 Volúmenes y copias de seguridad
```bash
# Listar volúmenes
docker volume ls

# Crear backup de un volumen (tar.gz)
# Ej: backup de "jellyfin_data"
docker run --rm -v jellyfin_data:/vol -v %cd%:/backup alpine   sh -c "cd /vol && tar czf /backup/jellyfin_data_$(date +%F).tgz ."

# Restaurar
docker run --rm -v jellyfin_data:/vol -v %cd%:/backup alpine   sh -c "cd /vol && tar xzf /backup/jellyfin_data_YYYY-MM-DD.tgz"
```

### 7.5 Redes
```bash
# Ver redes y contenedores conectados
docker network ls
docker network inspect proxy

# Conectar/desconectar un servicio a una red
docker network connect proxy nginx-proxy-manager
docker network disconnect proxy nginx-proxy-manager
```

### 7.6 Perfiles y GPU
```bash
# Lanzar solo servicios marcados con un perfil
docker compose --profile cpu up -d
docker compose --profile gpu-nvidia up -d

# Probar GPU
docker compose up test-gpu
```
(Para NVIDIA via Compose usa `deploy.resources.reservations.devices` con `driver: nvidia` y `capabilities: [gpu]` + `count`. Requiere toolkit/driver). citeturn22search0turn22search2

### 7.7 Limpieza
```bash
# Contenedores parados + redes no usadas + imágenes dangling
docker system prune

# También volúmenes sin usar (¡ojo!)
docker system prune --volumes
```

### 7.8 Salud (healthchecks) y diagnóstico
```bash
# Ver estado de healthchecks vía inspect
docker inspect --format='{{.State.Health.Status}}' jellyfin

# Consultar endpoints de salud (ejemplos)
curl -fsS http://localhost:8096/health      # Jellyfin
curl -fsS http://localhost:5055/api/v1/status # Jellyseerr
```
(Jellyfin expone `/health` para comprobar disponibilidad básica). citeturn7search0

---

## 8) Buenas prácticas y seguridad

- **.env y secretos** fuera del repo. Usa `COMPOSE_PROFILES`, `COMPOSE_PROJECT_NAME`, etc. si gestionas varios stacks. citeturn15search11  
- **NPM**: habilita HTTPS, fuerza HSTS/H2, usa Access Lists. Mantén **solo 80/443** abiertos al exterior.  
- **OnlyOffice**: fija `JWT_SECRET` y replica ese valor en el conector (Nextcloud/Seafile…) para evitar fallos tras reinicios. citeturn20search1  
- **Netdata**: monta correctamente `/etc/netdata`, `/var/lib/netdata`, `/var/cache/netdata` y paths de host para una visibilidad real del sistema. citeturn17search0  
- **Qdrant/servicios internos**: si no necesitan Internet, *no* los publiques, limítalos a red Docker interna. citeturn21view0  
- **Backups**: programa copias de los volúmenes de configuración.

---

## 9) Tabla de puertos locales (host → contenedor)

| Servicio | Host:Puerto | Contenedor |
|---|---:|---:|
| Jellyfin | 8096 / 8920 | 8096 / 8920 |
| Jellix | 3003 | 80 |
| Jellyfin-Vue | 3004 | 80 |
| TubeArchivist | 8001 | 8000 |
| jfa-go | 8056 | 8056 |
| Jellystat | 3000 | 3000 |
| Threadfin | 34400 | 34400 |
| TVHeadend | 9981/9982 | 9981/9982 |
| qBittorrent | 8080 / 6881 TCP/UDP | 8080 / 6881 |
| Sonarr | 33074 | 8989 |
| Radarr | 33075 | 7878 |
| Bazarr | 33076 | 6767 |
| Lidarr | 33077 | 8686 |
| Readarr | 33078 | 8787 |
| Prowlarr | 33079 | 9696 |
| Jellyseerr | 33080 | 5055 |
| Tdarr | 33081/33082 | 8265/8266 |
| Komga | 33083 | 25600 |
| Calibre | 33084/33085/33087 | 8080/8181/8081 |
| Kavita | 33086 | 5000 |
| Portainer | 9443/8000 | 9443/8000 |
| Dozzle | 9999 | 8080 |
| Netdata | 19999 | 19999 |
| Audiobookshelf | 13378 | 80 |
| Unmanic | 8888 | 8888 |
| Autobrr | 7474 | 7474 |
| NewTrackon | 33101 | 8080 |
| Notifiarr | 5454 | 5454 |
| Organizr | 33102 | 80 |
| CloudBeaver | 18978 | 8978 |
| SearXNG | 4080 | 8080 |
| NPM (HTTP/HTTPS/Admin) | **80 / 443 / 81** | 80 / 443 / 81 |
| Kometa | (no puerto) | — |
| Calibre-Web | 8083 | 8083 |
| Navidrome | 4533 | 4533 |
| Open WebUI | 3002 | 8080 |
| Uptime Kuma | 3001 | 3001 |
| OnlyOffice | 8088 | 80 |
| Filebrowser | 18080 | 80 |
| DuckDNS | — | — |
| Ollama | **11000 → 11434** | 11434 |
| Qdrant | 6333 | 6333 |

> Tras ponerlos detrás de NPM, esos puertos **ya no** deberían exponerse en WAN (manténlos en LAN/red Docker).

---

## 10) Flujo recomendado (de extremo a extremo)

1) **Búsqueda/Descarga**: Prowlarr/Jackett → qBittorrent.  
2) **Clasificación**: Sonarr/Radarr/Readarr/Lidarr mueven/renombran a librerías finales.  
3) **Transcodificación**: Unmanic/Tdarr optimizan códecs/bitrates (ideal con GPU).  
4) **Servir**: Jellyfin + Jellyseerr (peticiones).  
5) **Subtítulos**: Bazarr.  
6) **Monitoreo**: Netdata + Uptime Kuma.  
7) **Automatización**: n8n (workflows con Postgres).  
8) **IA**: Open WebUI→Ollama (+Qdrant para RAG/embeddings).  
9) **Proxy/SSL**: NPM delante de todo.

---

## 11) Solución de problemas (rápidas)

- **GPU no detectada**: confirma Toolkit/Drivers, `deploy.resources...devices` (`driver: nvidia`, `capabilities: [gpu]`, `count: 1`) y revisa `docker compose logs`. citeturn22search0turn22search2  
- **WebSockets no funcionan tras NPM**: habilita WS y añade `Upgrade/Connection`. citeturn18search1  
- **OnlyOffice 401/403 con conectores**: alinea `JWT_SECRET` en DocumentServer y en el conector; evita rotación automática del secreto. citeturn20search1  
- **Open WebUI no conecta a Ollama**: revisa `OLLAMA_BASE_URL` (si Ollama está en el host: `http://host.docker.internal:11000`). citeturn23search0  
- **Netdata sin métricas del host**: revisa montajes `/proc`, `/sys`, `/etc/os-release` y volúmenes de `/etc/netdata`, `/var/lib/netdata`, `/var/cache/netdata`. citeturn17search0  
- **Qdrant accesible públicamente**: restringe a red interna o agrega auth/proxy. citeturn21view0

---

## 12) Apéndice de comandos útiles (avanzado)

```bash
# Ejecutar un curl desde dentro de un contenedor
docker compose exec searxng curl -I http://localhost:8080

# Copiar archivos dentro/fuera de contenedor
docker cp ./config.yml kometa:/config/config.yml
docker cp jellyfin:/config/log/log.txt ./

# Health detallado
docker inspect jellyseerr | jq '.[0].State.Health'

# Ver diferencias de imagen al actualizar (pull requerido)
docker images | grep -E 'jellyfin|ollama|open-webui'

# Forzar recreación de un servicio concreto con imagen nueva
docker compose pull jellyfin && docker compose up -d jellyfin

# Pruebas HTTP básicas en un servicio
curl -fsS http://localhost:3001  # Kuma
curl -fsS http://localhost:4080  # SearXNG

# Perfiles: arrancar solo lo necesario para IA CPU
docker compose --profile cpu up -d ollama-cpu open-webui qdrant
```

---

## 13) Referencias

- **GPU en Docker Compose** (reservas de dispositivos y capacidades). citeturn22search0turn22search2  
- **Jellyfin — Aceleración HW (NVIDIA/Intel) y limitaciones**. citeturn16search1turn16search0turn16search5turn16search9  
- **Netdata en Docker** (montajes/privilegios/volúmenes). citeturn17search0turn17search4  
- **Nginx WebSocket** (Upgrade/Connection). citeturn18search1  
- **Nginx Proxy Manager** (puertos por defecto y acceso al panel). citeturn18search0turn18search2  
- **Open WebUI** (`OLLAMA_BASE_URL`). citeturn23search0  
- **Ollama Docker** (11434 y volumen `/root/.ollama`). citeturn24search17  
- **Qdrant** (puertos y almacenamiento). citeturn21view0  
- **OnlyOffice** (JWT habilitado y secreto fijo). citeturn20search1turn20search0  
- **Docker Compose CLI v2** (up/down/logs/exec…). citeturn5search2

---

### ¡Listo!
Con este README **unificado** ya tienes la guía de despliegue, exposición, operación y comandos para todo el stack. Si quieres, puedo adaptar los **dominios/subdominios**, **listas de acceso** y **certs** de NPM a tu caso concreto (o generar un `.env` modelo con tus valores).
