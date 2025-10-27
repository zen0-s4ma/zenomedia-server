FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Madrid

# Paquetes base
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor cron tini bash tzdata gcc build-essential gettext-base curl \
 && rm -rf /var/lib/apt/lists/*

# venv
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip

# Estructura
RUN mkdir -p /etc/supervisor/conf.d /workspace /bootstrap

# Config/Utils
COPY supervisord.conf.tpl /bootstrap/supervisord.conf.tpl
COPY entrypoint.sh /entrypoint.sh
COPY py-manager /usr/local/bin/py-manager
COPY auto-register.sh /usr/local/bin/auto-register.sh

# Dependencias del UI (sin sesiones)
RUN /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install fastapi uvicorn jinja2

# UI bonito
COPY py_ui.py /py_ui.py

# Normaliza EOL (CRLF->LF) y permisos (incluye py_ui.py)
RUN sed -i 's/\r$//' /entrypoint.sh /usr/local/bin/py-manager /usr/local/bin/auto-register.sh /py_ui.py \
 && chmod +x /entrypoint.sh /usr/local/bin/py-manager /usr/local/bin/auto-register.sh

ENTRYPOINT ["/usr/bin/tini","--","/entrypoint.sh"]
