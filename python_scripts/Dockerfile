FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Madrid

RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor cron tini bash tzdata gcc build-essential \
    gettext-base curl \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip

RUN mkdir -p /etc/supervisor/conf.d /workspace /bootstrap

COPY supervisord.conf.tpl /bootstrap/supervisord.conf.tpl
COPY entrypoint.sh /entrypoint.sh
COPY py-manager /usr/local/bin/py-manager
COPY auto-register.sh /usr/local/bin/auto-register.sh

RUN /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install fastapi uvicorn jinja2 itsdangerous

# copiar el UI
COPY py_ui.py /py_ui.py

# normalizar EOL (por si editas en Windows)
RUN sed -i 's/\r$//' /entrypoint.sh /usr/local/bin/py-manager /usr/local/bin/auto-register.sh || true

# Normaliza finales de línea y permisos (evita el 'bash\r')
RUN sed -i 's/\r$//' /entrypoint.sh /usr/local/bin/py-manager /usr/local/bin/auto-register.sh \
 && chmod +x /entrypoint.sh /usr/local/bin/py-manager /usr/local/bin/auto-register.sh

ENTRYPOINT ["/usr/bin/tini","--","/entrypoint.sh"]
