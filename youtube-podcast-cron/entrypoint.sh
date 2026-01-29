#!/bin/sh
set -eu

: "${TZ:=Europe/Madrid}"
: "${CRON_SCHEDULE:=0 10 * * *}"   # todos los días a las 10:00
: "${RUN_ON_START:=false}"

# Ajustar timezone del sistema (cron usa /etc/localtime en Debian) 
if [ -f "/usr/share/zoneinfo/$TZ" ]; then
  ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
  echo "$TZ" > /etc/timezone
else
  echo "[entrypoint] WARN: TZ '$TZ' no existe en /usr/share/zoneinfo, usando default del contenedor." >&2
fi

# Validaciones mínimas
if [ ! -f /app/exporter.py ]; then
  echo "[entrypoint] ERROR: no existe /app/exporter.py (¿copiaste tu script?)" >&2
  exit 2
fi

chmod +x /usr/local/bin/run.sh || true

# Cron job en /etc/cron.d (formato: incluye usuario) 
# Importante: redirigimos stdout/stderr al PID 1 para verlo en `docker logs` 
cat > /etc/cron.d/youtube-podcast-exporter <<EOF
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

$CRON_SCHEDULE root /usr/local/bin/run.sh >>/proc/1/fd/1 2>>/proc/1/fd/2
EOF

chmod 0644 /etc/cron.d/youtube-podcast-exporter

# Cargar el cron.d
# (Con Debian cron, basta con dejar el fichero; el daemon lo lee y lo monitoriza) 

if [ "$RUN_ON_START" = "true" ]; then
  echo "[entrypoint] RUN_ON_START=true -> ejecuto una vez ahora"
  /usr/local/bin/run.sh >>/proc/1/fd/1 2>>/proc/1/fd/2 || true
fi

echo "[entrypoint] Arrancando cron en foreground (mantiene el contenedor vivo)..." 
exec cron -f
