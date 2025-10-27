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

RUN chmod +x /entrypoint.sh /usr/local/bin/py-manager

ENTRYPOINT ["/usr/bin/tini","--","/entrypoint.sh"]
