from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# ============================================================
# CONFIG (TODO JUNTO AQUÍ)
# ============================================================

# --- APIs / URLs ---
JELLYFIN_URL = "http://192.168.1.113:8096"  # Ej: http://192.168.1.34:8096
JELLYFIN_API_KEY = "0b77ac9b41fa4bc1ad4a81963eb278bb"  # Jellyfin Dashboard -> Advanced -> API Keys

# TMDb "API Read Access Token" (Bearer)
TMDB_BEARER_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiI0YmM2MDc5MTRhZmViMjY1M2ZkNTlkYmU2ZmE2MzFiZCIsIm5iZiI6MTc2MTkyMTg5Mi4zNDMwMDAyLCJzdWIiOiI2OTA0Y2I2NDg2M2I1NmRiZWRkZDc5N2QiLCJzY29wZXMiOlsiYXBpX3JlYWQiXSwidmVyc2lvbiI6MX0.cU_7V9KFjw184-MjKHvYNj_IJv18Dcg_8Ub97FCGGRk"

# --- Idioma preferido: Español (España) ---
PREFERRED_LANGUAGE = "es"  # TMDb devuelve iso_639_1 (p.ej. "es")
PREFERRED_REGION = "ES"  # TMDb devuelve iso_3166_1 (p.ej. "ES")

# --- Pool / rotación ---
TARGET_TRAILERS = 20  # Total trailers
ROTATE_COUNT = 3  # Nº trailers que rotan (por fecha de modificación más antigua)

# --- Modo FULL biblioteca ---
# Si es True: descarga TODO lo posible (todas las pelis con TMDb+trailer) sin tener en cuenta MAX ni ROTACIÓN.
FULL_BIBLIOTECA = True

# --- Ruta de descarga de trailers (POOL) ---
TRAILERS_DOWNLOAD_DIR = r"E:\_Trailers"

# --- Librerías ---
# Si lo dejas vacío, cogerá TODAS las librerías tipo "movies".
# Si lo rellenas, solo usará esas librerías (por nombre EXACTO en Jellyfin).
JELLYFIN_LIBRARY_NAMES: List[str] = []  # Ej: ["Películas", "Movies"]

# --- Calidad / formato ---
MAX_HEIGHT = 720  # quieres 720p
OUTPUT_EXT = "mp4"

# ============================================================
# PATHS
# - El script genera su STATE en la carpeta del script
# - Los trailers se guardan en TRAILERS_DOWNLOAD_DIR
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
POOL_DIR = Path(TRAILERS_DOWNLOAD_DIR)
STATE_FILE = BASE_DIR / "_trailer_pool_state.json"

# ============================================================
# HELPERS
# ============================================================


def auth_headers() -> Dict[str, str]:
    return {
        "X-Emby-Token": JELLYFIN_API_KEY,
        "X-MediaBrowser-Token": JELLYFIN_API_KEY,
        "Accept": "application/json",
    }


def die(msg: str, exit_code: int = 1) -> None:
    print(msg)
    sys.exit(exit_code)


def safe_filename(name: str, max_len: int = 140) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in (" ", ".", "_", "-", "(", ")", "[", "]"):
            keep.append(ch)
        else:
            keep.append("_")
    s = "".join(keep).strip()
    s = " ".join(s.split())
    return s[:max_len] if len(s) > max_len else s


def load_state() -> Dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            bad = STATE_FILE.with_suffix(".corrupt.json")
            shutil.copy2(STATE_FILE, bad)
            return {"by_tmdb": {}}
    return {"by_tmdb": {}}


def save_state(state: Dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_cmd(cmd: List[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(
            f"Fallo comando:\n{' '.join(cmd)}\n\nSTDOUT:\n{p.stdout}\n\nSTDERR:\n{p.stderr}"
        )


def check_binary(name: str) -> None:
    if shutil.which(name) is None:
        die(
            f"ERROR: no encuentro '{name}' en PATH.\n"
            f"- Instálalo y asegúrate de que el comando '{name}' funcione en tu terminal.\n"
        )


def jellyfin_get(
    path: str, params: Optional[Dict] = None, use_auth: bool = True
) -> Dict:
    url = f"{JELLYFIN_URL.rstrip('/')}{path}"
    headers = auth_headers() if use_auth else {"Accept": "application/json"}
    r = requests.get(url, headers=headers, params=params, timeout=30)

    if r.status_code == 401:
        raise PermissionError("401 Unauthorized (API key inválida o sin permisos).")
    if r.status_code == 404:
        raise FileNotFoundError(f"404 Not Found en {path} (¿URL base correcta?)")

    r.raise_for_status()
    return r.json()


def jellyfin_connectivity_check() -> None:
    # 1) ping público (sin auth)
    try:
        _ = jellyfin_get("/System/Info/Public", use_auth=False)
    except requests.exceptions.ConnectionError:
        die(
            "ERROR: No puedo conectar con Jellyfin.\n"
            f"- Revisa JELLYFIN_URL = {JELLYFIN_URL}\n"
            "- ¿Está encendido el servidor y accesible desde esta máquina?\n"
        )
    except Exception as e:
        die(
            "ERROR: Conecto, pero el endpoint público falló.\n"
            f"- URL: {JELLYFIN_URL}/System/Info/Public\n"
            f"- Detalle: {e}\n"
            "- Si usas reverse proxy, revisa que no redirija a HTML de login.\n"
        )

    # 2) prueba auth con API key
    try:
        users = jellyfin_get("/Users", use_auth=True)
        if not isinstance(users, list) or not users:
            die("ERROR: La llamada /Users devolvió algo raro o vacío. ¿Permisos?")
    except PermissionError as e:
        die(
            "ERROR: Jellyfin responde, pero tu API key no autentica.\n"
            f"- Detalle: {e}\n"
            "- Revisa JELLYFIN_API_KEY en el script.\n"
        )
    except Exception as e:
        die(
            "ERROR: Jellyfin responde, pero la llamada autenticada falló.\n"
            f"- Detalle: {e}\n"
        )

    print("OK: Conectividad con Jellyfin + API key correcta.")


def get_user_id() -> str:
    users = jellyfin_get("/Users")
    return users[0]["Id"]


def get_views(user_id: str) -> List[Dict]:
    data = jellyfin_get(f"/Users/{user_id}/Views")
    return data.get("Items", [])


def pick_movie_library_ids(views: List[Dict]) -> List[str]:
    wanted = (
        {n.strip().lower() for n in JELLYFIN_LIBRARY_NAMES}
        if JELLYFIN_LIBRARY_NAMES
        else None
    )
    ids = []
    for v in views:
        name = (v.get("Name") or "").strip()
        ctype = (v.get("CollectionType") or "").strip().lower()
        if wanted is not None:
            if name.lower() in wanted:
                ids.append(v["Id"])
        else:
            if ctype == "movies":
                ids.append(v["Id"])
    return ids


def list_movies(user_id: str, library_id: str) -> List[Dict]:
    items: List[Dict] = []
    start = 0
    limit = 500
    while True:
        page = jellyfin_get(
            f"/Users/{user_id}/Items",
            params={
                "ParentId": library_id,
                "Recursive": "true",
                "IncludeItemTypes": "Movie",
                "Fields": "ProviderIds",
                "StartIndex": start,
                "Limit": limit,
            },
        )
        batch = page.get("Items", [])
        items.extend(batch)
        if len(batch) < limit:
            break
        start += limit
    return items


def tmdb_get_trailer_youtube_key(tmdb_id: str) -> Optional[str]:
    """
    Prioriza trailer español de España (es-ES) si existe.
    Fallback: es (cualquier región) -> en -> lo mejor disponible.
    """
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/videos"
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {TMDB_BEARER_TOKEN}", "accept": "application/json"},
        timeout=30,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    results = r.json().get("results", [])

    candidates = [v for v in results if v.get("site") == "YouTube" and v.get("key")]
    if not candidates:
        return None

    def score(v: Dict) -> Tuple[int, int, int, int, int, int]:
        v_type = v.get("type") or ""
        v_official = v.get("official") is True
        v_lang = (v.get("iso_639_1") or "").lower()
        v_region = (v.get("iso_3166_1") or "").upper()

        # ranking por preferencia:
        # Trailer > Teaser/otros, Official, y luego idioma/región
        is_trailer = 1 if v_type == "Trailer" else 0
        is_official = 1 if v_official else 0
        is_es_es = 1 if (v_lang == PREFERRED_LANGUAGE and v_region == PREFERRED_REGION) else 0
        is_es = 1 if v_lang == PREFERRED_LANGUAGE else 0
        is_en = 1 if v_lang == "en" else 0

        # Extra: si el name contiene "tráiler"/"trailer" suma un poquito (muy suave)
        name = (v.get("name") or "").lower()
        name_hint = 1 if ("tráiler" in name or "trailer" in name) else 0

        return (is_trailer, is_official, is_es_es, is_es, is_en, name_hint)

    candidates.sort(key=score, reverse=True)
    return candidates[0]["key"]


def download_trailer_720p(youtube_key: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = f"bv*[height<={MAX_HEIGHT}]+ba/b[height<={MAX_HEIGHT}]"
    cmd = [
        "yt-dlp",
        "-f",
        fmt,
        "--merge-output-format",
        OUTPUT_EXT,
        "-o",
        str(out_path),
        f"https://www.youtube.com/watch?v={youtube_key}",
    ]
    run_cmd(cmd)


def current_pool_files() -> List[Path]:
    if not POOL_DIR.exists():
        return []
    return [p for p in POOL_DIR.glob(f"*.{OUTPUT_EXT}") if p.is_file()]


def delete_oldest(n: int) -> List[Path]:
    files = current_pool_files()
    files.sort(key=lambda p: p.stat().st_mtime)  # más antiguos primero
    deleted: List[Path] = []
    for p in files[:n]:
        try:
            p.unlink()
            deleted.append(p)
        except Exception:
            pass
    return deleted


def build_existing_tmdb_set(state: Dict) -> set:
    existing = set()
    to_del = []
    for tmdb_id, rec in state.get("by_tmdb", {}).items():
        f = Path(rec.get("file", ""))
        if f.exists():
            existing.add(tmdb_id)
        else:
            to_del.append(tmdb_id)
    for k in to_del:
        state["by_tmdb"].pop(k, None)
    return existing


def add_new_trailers(movies: List[Dict], state: Dict, existing_tmdb: set, count: int) -> int:
    added = 0
    random.shuffle(movies)

    for m in movies:
        if added >= count:
            break

        pids = m.get("ProviderIds") or {}
        tmdb_id = pids.get("Tmdb")
        if not tmdb_id:
            continue

        tmdb_id = str(tmdb_id)
        if tmdb_id in existing_tmdb:
            continue

        title = m.get("Name") or f"tmdb_{tmdb_id}"
        safe = safe_filename(title)
        out_file = POOL_DIR / f"tmdb_{tmdb_id}__{safe}.{OUTPUT_EXT}"

        try:
            yt_key = tmdb_get_trailer_youtube_key(tmdb_id)
            if not yt_key:
                continue

            print(f"Descargando trailer: {title} (tmdb {tmdb_id}) -> {out_file.name}")
            download_trailer_720p(yt_key, out_file)

            state.setdefault("by_tmdb", {})[tmdb_id] = {
                "title": title,
                "file": str(out_file),
                "added_at": int(time.time()),
                "preferred_lang": f"{PREFERRED_LANGUAGE}-{PREFERRED_REGION}",
            }
            existing_tmdb.add(tmdb_id)
            added += 1

        except Exception as e:
            print(f"  fallo descargando {title} (tmdb {tmdb_id}): {e}")

    return added


def main() -> None:
    check_binary("yt-dlp")
    check_binary("ffmpeg")

    jellyfin_connectivity_check()

    user_id = get_user_id()
    views = get_views(user_id)
    lib_ids = pick_movie_library_ids(views)

    if not lib_ids:
        if JELLYFIN_LIBRARY_NAMES:
            die(
                "ERROR: No encontré tus librerías por nombre.\n"
                "Nombres disponibles:\n" + "\n".join([f"- {v.get('Name')}" for v in views])
            )
        die(
            "ERROR: No encontré ninguna librería con CollectionType='movies'.\n"
            "Solución: rellena JELLYFIN_LIBRARY_NAMES con tus librerías de pelis."
        )

    movies: List[Dict] = []
    for lid in lib_ids:
        movies.extend(list_movies(user_id, lid))

    POOL_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    existing_tmdb = build_existing_tmdb_set(state)

    # ============================================================
    # MODO FULL BIBLIOTECA: descarga todo lo posible (sin max ni rotación)
    # ============================================================
    if FULL_BIBLIOTECA:
        print("MODO FULL_BIBLIOTECA = True -> Descargando TODO lo posible (sin MAX ni ROTACIÓN).")
        added_total = 0

        # Intentamos descargar para todas las pelis, evitando repetidos por TMDb (existing_tmdb)
        random.shuffle(movies)
        for m in movies:
            pids = m.get("ProviderIds") or {}
            tmdb_id = pids.get("Tmdb")
            if not tmdb_id:
                continue

            tmdb_id = str(tmdb_id)
            if tmdb_id in existing_tmdb:
                continue

            title = m.get("Name") or f"tmdb_{tmdb_id}"
            safe = safe_filename(title)
            out_file = POOL_DIR / f"tmdb_{tmdb_id}__{safe}.{OUTPUT_EXT}"

            try:
                yt_key = tmdb_get_trailer_youtube_key(tmdb_id)
                if not yt_key:
                    continue

                print(f"Descargando trailer: {title} (tmdb {tmdb_id}) -> {out_file.name}")
                download_trailer_720p(yt_key, out_file)

                state.setdefault("by_tmdb", {})[tmdb_id] = {
                    "title": title,
                    "file": str(out_file),
                    "added_at": int(time.time()),
                    "preferred_lang": f"{PREFERRED_LANGUAGE}-{PREFERRED_REGION}",
                }
                existing_tmdb.add(tmdb_id)
                added_total += 1

            except Exception as e:
                print(f"  fallo descargando {title} (tmdb {tmdb_id}): {e}")

        save_state(state)
        print("\nDONE. FULL_BIBLIOTECA añadió:", added_total, "trailers. Total en pool:", len(current_pool_files()))
        return

    # ============================================================
    # NUEVO: PURGA SI EL POOL TIENE MÁS QUE EL MÁXIMO
    # - Se borran los más antiguos (mtime) hasta dejarlo en TARGET_TRAILERS
    # - Luego el script sigue con su flujo normal (relleno -> rotación)
    # ============================================================
    current_count = len(current_pool_files())
    if current_count > TARGET_TRAILERS:
        purge_n = current_count - TARGET_TRAILERS
        print(
            f"PURGA inicial: hay {current_count} trailers pero el máximo es {TARGET_TRAILERS}. "
            f"Elimino {purge_n} más antiguos para cuadrar el pool."
        )
        deleted = delete_oldest(purge_n)

        if deleted:
            deleted_names = {p.name for p in deleted}
            to_del = []
            for tmdb_id, rec in state.get("by_tmdb", {}).items():
                f = Path(rec.get("file", ""))
                if f.name in deleted_names and not f.exists():
                    to_del.append(tmdb_id)
            for tmdb_id in to_del:
                state["by_tmdb"].pop(tmdb_id, None)
                existing_tmdb.discard(tmdb_id)

        save_state(state)

    # ============================================================
    # FASE A: rellenar hasta TARGET
    # ============================================================
    current_count = len(current_pool_files())
    if current_count < TARGET_TRAILERS:
        need = TARGET_TRAILERS - current_count
        print(f"Fase A (relleno): tengo {current_count}, añado {need} para llegar a {TARGET_TRAILERS}")
        _ = add_new_trailers(movies, state, existing_tmdb, need)
        save_state(state)

        current_count = len(current_pool_files())
        if current_count < TARGET_TRAILERS:
            print(
                f"AVISO: tras rellenar, me quedé en {current_count}/{TARGET_TRAILERS}.\n"
                "Causas típicas: pocas pelis con TMDb ID, TMDb sin trailer, o descargas fallidas.\n"
            )
    else:
        print(f"Fase A (relleno): ya estoy en {current_count}/{TARGET_TRAILERS}, no hace falta.")

    # ============================================================
    # FASE B: rotación SOLO si ya alcanzamos el tope
    # ============================================================
    current_count = len(current_pool_files())
    if current_count < TARGET_TRAILERS:
        print(
            f"SKIP rotación: aún no he alcanzado el tope ({current_count}/{TARGET_TRAILERS}).\n"
            "Primero necesito poder rellenar el pool hasta el objetivo.\n"
        )
        save_state(state)
        print("\nDONE. Pool actual:", current_count, "trailers en", POOL_DIR)
        return

    if ROTATE_COUNT > 0:
        print(f"Fase B (rotación): elimino los {ROTATE_COUNT} más antiguos y añado {ROTATE_COUNT} nuevos.")
        deleted = delete_oldest(ROTATE_COUNT)

        if deleted:
            deleted_names = {p.name for p in deleted}
            to_del = []
            for tmdb_id, rec in state.get("by_tmdb", {}).items():
                f = Path(rec.get("file", ""))
                # si su nombre estaba entre los borrados y ya no existe, limpia estado
                if f.name in deleted_names and not f.exists():
                    to_del.append(tmdb_id)
            for tmdb_id in to_del:
                state["by_tmdb"].pop(tmdb_id, None)
                existing_tmdb.discard(tmdb_id)

        added = add_new_trailers(movies, state, existing_tmdb, ROTATE_COUNT)
        save_state(state)

        if added < ROTATE_COUNT:
            print(
                f"AVISO: solo pude añadir {added}/{ROTATE_COUNT} trailers nuevos.\n"
                "Puede que no haya más trailers disponibles (o descargables) para pelis fuera de tu pool.\n"
            )

    print("\nDONE. Pool actual:", len(current_pool_files()), "trailers en", POOL_DIR)


if __name__ == "__main__":
    main()