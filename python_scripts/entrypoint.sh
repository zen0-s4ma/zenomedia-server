#!/usr/bin/env bash
set -euo pipefail

: "${SCRIPTS_DIR:=/workspace/scripts}"
: "${LOGS_DIR:=/workspace/logs}"
: "${REQUIREMENTS_FILE:=/workspace/requirements.txt}"
: "${CRON_FILE:=/workspace/schedule.cron}"

mkdir -p "$SCRIPTS_DIR" "$LOGS_DIR" /etc/supervisor/conf.d

envsubst < /bootstrap/supervisord.conf.tpl > /etc/supervisor/supervisord.conf

if [ -f "$REQUIREMENTS_FILE" ]; then
  echo "[py-runner] Instalando dependencias de $REQUIREMENTS_FILE ..."
  /opt/venv/bin/pip install -r "$REQUIREMENTS_FILE" || \
    echo "[py-runner] WARNING: error instalando requirements. Continuando..."
fi

if [ -f "$CRON_FILE" ]; then
  echo "[py-runner] Cargando tareas cron desde $CRON_FILE ..."
  crontab "$CRON_FILE"
fi

# Programa auto-registrador (lo gestiona Supervisor)
cat >/etc/supervisor/conf.d/py-autoreg.ini <<'EOF'
[program:py-autoreg]
command=/usr/local/bin/auto-register.sh
autostart=true
autorestart=true
stdout_logfile=/var/log/py-autoreg.out.log
stderr_logfile=/var/log/py-autoreg.err.log
EOF

cat >/etc/supervisor/conf.d/py-ui.ini <<'EOF'
[program:py-ui]
command=/opt/venv/bin/uvicorn py_ui:app --host 0.0.0.0 --port 8000
autostart=true
autorestart=true
stdout_logfile=/var/log/py-ui.out.log
stderr_logfile=/var/log/py-ui.err.log
EOF

echo "-----------------------------------------------------------"
echo "PY-RUNNER listo"
echo "Scripts dir: $SCRIPTS_DIR"
echo "Logs dir   : $LOGS_DIR"
echo "UI         : http://localhost:9001"
echo "Usuario    : ${SUPERVISOR_USER:-admin}"
echo "-----------------------------------------------------------"

exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
