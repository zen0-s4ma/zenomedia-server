#!/bin/sh
set -eu

: "${SRC_ROOT:=/data/src}"
: "${DEST_ROOT:=/data/dest}"
: "${YT_API_KEY:=}"

if [ -z "$YT_API_KEY" ]; then
  echo "[run.sh] ERROR: falta YT_API_KEY (variable de entorno)." >&2
  exit 2
fi

: "${DELETE_VIDEO:=true}"
: "${OVERWRITE_MP3:=false}"
: "${OVERWRITE_IMAGES:=true}"

: "${TA_BASE_URL:=}"
: "${TA_TOKEN:=}"
: "${TA_ACTION:=none}"          # none | delete | delete_ignore
: "${TA_VERIFY_SSL:=true}"
: "${TA_DRY_RUN:=false}"

ARGS="--src-root $SRC_ROOT --dest-root $DEST_ROOT --yt-api-key $YT_API_KEY"

# Booleans del script: ojo, tu script usa store_true con default=..., por lo que
# solo pasamos el flag cuando queremos forzar True de forma explícita.
# En tu script:
# --delete-video (store_true, default=DELETE_VIDEO_AFTER_MP3)
# --overwrite-mp3 (store_true, default=OVERWRITE_MP3)
# --overwrite-images (store_true, default=OVERWRITE_IMAGES)
#
# Para hacerlo determinista, aquí lo resolvemos así:
if [ "$DELETE_VIDEO" = "true" ]; then
  ARGS="$ARGS --delete-video"
fi
if [ "$OVERWRITE_MP3" = "true" ]; then
  ARGS="$ARGS --overwrite-mp3"
fi
if [ "$OVERWRITE_IMAGES" = "true" ]; then
  ARGS="$ARGS --overwrite-images"
fi

# TubeArchivist args (si procede)
if [ -n "$TA_BASE_URL" ]; then
  ARGS="$ARGS --ta-base-url $TA_BASE_URL"
fi
if [ -n "$TA_TOKEN" ]; then
  ARGS="$ARGS --ta-token $TA_TOKEN"
fi

# Acción TA (si no se configura, por defecto 'none')
ARGS="$ARGS --ta-action $TA_ACTION"

# Verify SSL: tu script usa --ta-no-verify-ssl (store_true, default=not TA_VERIFY_SSL)
if [ "$TA_VERIFY_SSL" != "true" ]; then
  ARGS="$ARGS --ta-no-verify-ssl"
fi

if [ "$TA_DRY_RUN" = "true" ]; then
  ARGS="$ARGS --ta-dry-run"
fi

echo "[run.sh] Ejecutando: python /app/exporter.py $ARGS"
python /app/exporter.py $ARGS
