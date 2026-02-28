# vpn-watchdog v4 (Gluetun netns self-heal)

Este watchdog está pensado para stacks donde varios contenedores usan:

```yaml
network_mode: "service:vpn-stable"
```

Cuando **gluetun/vpn-stable se recrea** (nuevo container ID), Docker deja a los dependientes apuntando al *namespace* antiguo (`container:<oldid>`). En ese estado verás errores como:

- `joining network namespace of container: No such container: <oldid>`

**Solución automática (v4):**

- Detecta que el VPN cambió de ID y/o que un dependiente está unido al netns equivocado.
- **Recrea** el contenedor dependiente copiando su configuración (imagen/env/volúmenes/healthcheck/etc.) y **reemplazando** `HostConfig.NetworkMode` por `container:<vpn_id_actual>`.

> Nota: esto evita depender de `docker compose up --force-recreate` para “arreglarlo”.

## Variables de entorno (principales)

- `DOCKER_HOST` (p.ej. `http://host.docker.internal:2375` en Docker Desktop Windows)
- `DOCKER_TIMEOUT` (segundos, recomendado 30+)
- `DOCKER_RETRIES` / `DOCKER_RETRY_SLEEP`

- `VPN_CONTAINER` (por defecto `vpn-stable`)
- `DEPENDENTS` (coma separada)

- `CHECK_INTERVAL` (s)
- `STARTUP_GRACE` (s)
- `DOWN_GRACE` (s)
- `COOLDOWN` (s)

- `RESTART_ON_VPN_RESTART` (1/0)
- `VPN_RESTART_GRACE` (s)

- `RECREATE_ON_NETNS_MISMATCH` (1/0)  **(importante)**
- `RECREATE_ON_NETNS_ERROR` (1/0)

- `STOP_TIMEOUT_S` / `RESTART_TIMEOUT_S`

## Build

Dentro de tu repo:

```bash
docker compose build vpn-watchdog
```

## Run

```bash
docker compose up -d vpn-stable dispatcharr tuliprox firefox tor-browser vpn-ip-check vpn-watchdog
```
