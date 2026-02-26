#!/usr/bin/with-contenv bash
set -euo pipefail

: "${MULLVAD_WG_FILE:?Set MULLVAD_WG_FILE (e.g. ch-zrh-wg-001.conf)}"

SRC="/mullvad_confs/${MULLVAD_WG_FILE}"
DST="/config/wg_confs/wg0.conf"

mkdir -p /config/wg_confs
rm -f /config/wg_confs/*.conf

cp "$SRC" "$DST"
chmod 600 "$DST" || true

# Opcional: quita IPv6 default route si te diera guerra en WSL2
sed -i 's/, *::\/0//g' "$DST" || true