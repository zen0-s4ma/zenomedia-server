import requests
import subprocess
import time

# =========================
# Configuración
# =========================
DISPATCHARR_URL = "http://localhost:9191"  # URL base de Dispatcharr
USERNAME = "admin"                         # dummy
PASSWORD = "@RM22716ammm"                  # dummy

# Ya confirmado por tu /groups: Cuarentena = 46
QUARANTINE_GROUP_ID = 46
QUARANTINE_GROUP_NAME = "Cuarentena"

# ffprobe
FFPROBE_TIMEOUT_SEC = 10      # timeout de la prueba por canal (ffprobe)
FFPROBE_READ_SECONDS = 5      # cuantos segundos intenta leer
PAUSE_BETWEEN_CHANNELS = 0    # si quieres ser más conservador: 1 o 2

# =========================
# Helpers
# =========================
def get_token() -> str:
    """Obtiene token JWT de Dispatcharr."""
    login_url = f"{DISPATCHARR_URL}/api/accounts/token/"
    auth_data = {"username": USERNAME, "password": PASSWORD}
    resp = requests.post(login_url, json=auth_data, timeout=15)
    resp.raise_for_status()
    token = resp.json().get("access")
    if not token:
        raise RuntimeError("No se pudo obtener token de autenticación (campo 'access' vacío).")
    return token


def get_all_paginated(url: str, headers: dict) -> list:
    """
    Obtiene listas paginadas tipo DRF:
    - Si devuelve {"results":[...], "next": "..."} itera.
    - Si devuelve lista directa, la retorna tal cual.
    """
    items = []
    next_url = url
    while next_url:
        r = requests.get(next_url, headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "results" in data:
            items.extend(data["results"])
            next_url = data.get("next")
        elif isinstance(data, list):
            items.extend(data)
            next_url = None
        else:
            raise RuntimeError(f"Respuesta inesperada al paginar: {type(data)}")
    return items


def get_stream_by_id(stream_id: int, headers: dict) -> dict:
    """
    Obtiene un stream por ID.
    En tu API los channels devuelven streams como IDs (ej: [1068]).
    Probamos varias rutas típicas por compatibilidad.
    """
    candidates = [
        f"{DISPATCHARR_URL}/api/channels/streams/{stream_id}/",
        f"{DISPATCHARR_URL}/api/channels/stream/{stream_id}/",
        f"{DISPATCHARR_URL}/api/streams/{stream_id}/",
        f"{DISPATCHARR_URL}/api/streams/streams/{stream_id}/",
    ]

    last_err = None
    for url in candidates:
        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    return data
            last_err = f"{url} -> status={r.status_code}, body={r.text[:200]}"
        except Exception as e:
            last_err = f"{url} -> error={e}"

    raise RuntimeError(f"No se pudo obtener el stream {stream_id}. Último error: {last_err}")


def extract_stream_url(stream_obj: dict) -> str | None:
    """
    Diferentes builds pueden usar distinto nombre de campo para la URL.
    Probamos varios.
    """
    for key in ("url", "stream_url", "source", "source_url", "m3u_url"):
        v = stream_obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def stream_is_active(stream_url: str, timeout: int = FFPROBE_TIMEOUT_SEC) -> bool:
    """
    Usa ffprobe para ver si el stream responde.
    Retorna True si ffprobe puede abrir y leer algo, False si falla/timeout.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-timeout", str(timeout * 1_000_000),  # microsegundos
        "-read_intervals", f"%+#${FFPROBE_READ_SECONDS}".replace("$", ""),
        "-i", stream_url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception as e:
        print(f"[ffprobe] Error ejecutando ffprobe para {stream_url}: {e}")
        return False


def move_channel_to_quarantine(channel_id: int, headers: dict) -> bool:
    """
    Mueve un canal al grupo de cuarentena por API.
    Ya confirmado en tu JSON: el campo correcto del canal es 'channel_group_id'.
    """
    update_url = f"{DISPATCHARR_URL}/api/channels/channels/{channel_id}/"
    payload = {"channel_group_id": QUARANTINE_GROUP_ID}

    try:
        r = requests.patch(update_url, headers=headers, json=payload, timeout=30)
        if r.status_code not in (200, 204):
            print(f"  [!] PATCH falló (status={r.status_code}): {r.text[:200]}")
            return False

        # Verificación fuerte: re-GET del canal para confirmar cambio real
        g = requests.get(update_url, headers=headers, timeout=30)
        if g.status_code == 200:
            data = g.json()
            if data.get("channel_group_id") == QUARANTINE_GROUP_ID:
                return True
            print(f"  [!] PATCH respondió OK pero el canal sigue con channel_group_id={data.get('channel_group_id')}")
            return False

        return True

    except Exception as e:
        print(f"  [!] Error moviendo canal {channel_id} a cuarentena: {e}")
        return False


# =========================
# Main
# =========================
def main():
    # 1) Token + headers
    try:
        token = get_token()
    except Exception as e:
        print("Error al conectar o autenticar con Dispatcharr:", e)
        return

    headers = {"Authorization": f"Bearer {token}"}

    # 2) Cargar canales (en tu API streams vienen como IDs)
    channels_url = f"{DISPATCHARR_URL}/api/channels/channels/"
    try:
        channels = get_all_paginated(channels_url, headers)
    except Exception as e:
        print("Error obteniendo lista de canales:", e)
        return

    print(f"[OK] Grupo '{QUARANTINE_GROUP_NAME}' => id={QUARANTINE_GROUP_ID}")
    print(f"Total de canales obtenidos: {len(channels)}")

    quarantined_channels = []

    # --- NUEVO: contador progreso solo para canales comprobados (con id + streams) ---
    total_canales = sum(1 for c in channels if c.get("id") and (c.get("streams") or []))
    canal_idx = 0
    # ------------------------------------------------------------------------------

    # 3) Procesar canal por canal
    for channel in channels:
        channel_id = channel.get("id")
        channel_name = channel.get("name", f"ID {channel_id}")

        stream_ids = channel.get("streams") or []
        if not channel_id or not stream_ids:
            continue

        canal_idx += 1  # NUEVO: avanza solo cuando realmente se va a comprobar el canal

        # En tu JSON: "streams": [1068] => ID numérico
        primary_stream_id = int(stream_ids[0])

        # Resolver URL real del stream
        try:
            stream_obj = get_stream_by_id(primary_stream_id, headers)
        except Exception as e:
            print(f"[{channel_name}] No pude obtener stream {primary_stream_id}: {e}")
            continue

        stream_url = extract_stream_url(stream_obj)
        if not stream_url:
            print(f"[{channel_name}] Stream {primary_stream_id} no tiene campo URL conocido (url/source/etc).")
            continue

        print(f"Comprobando canal ({canal_idx}/{total_canales}) - '{channel_name}' (ch={channel_id}, stream={primary_stream_id})...")

        active = stream_is_active(stream_url, timeout=FFPROBE_TIMEOUT_SEC)
        if not active:
            print(f"  -> CAÍDO. Moviendo a '{QUARANTINE_GROUP_NAME}' (id={QUARANTINE_GROUP_ID})...")
            ok = move_channel_to_quarantine(int(channel_id), headers)
            if ok:
                quarantined_channels.append(channel_name)
        else:
            print("  -> Activo.")

        if PAUSE_BETWEEN_CHANNELS > 0:
            time.sleep(PAUSE_BETWEEN_CHANNELS)

    # 4) Resumen
    print("\n==== Comprobación finalizada ====")
    print(f"Total de canales procesados: {len(channels)}")
    if quarantined_channels:
        print(f"Canales movidos a '{QUARANTINE_GROUP_NAME}': {len(quarantined_channels)}")
        print("Listado de canales en cuarentena:")
        for name in quarantined_channels:
            print(f"  - {name}")
    else:
        print("Ningún canal fue marcado como caído en esta ejecución.")


if __name__ == "__main__":
    main()
