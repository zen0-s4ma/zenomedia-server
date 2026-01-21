#!/usr/bin/with-contenv sh
set -e

# Necesario para chroma/fingerprint (fpcalc)
apk add --no-cache chromaprint ffmpeg

# Necesario para la WebUI (Flask) + acústid
pip install --no-cache-dir "beets[web]" pyacoustid

exit 0
