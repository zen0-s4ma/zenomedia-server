FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

# Dependencias del sistema:
# - cron: scheduler
# - ffmpeg: convertir a mp3
# - tzdata: zona horaria
# - ca-certificates: HTTPS (YouTube API / TA)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      cron ffmpeg tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Tu script (ponlo como exporter.py)
COPY exporter.py /app/exporter.py

# Wrappers
COPY run.sh /usr/local/bin/run.sh
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /usr/local/bin/run.sh /entrypoint.sh

# No exponemos puertos: es un worker/cron puro
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD pgrep -x cron >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
