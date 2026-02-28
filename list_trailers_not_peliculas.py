from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests

# ============================================================
# CONFIG (keys dummy OK)
# ============================================================

JELLYFIN_URL = "http://192.168.1.113:8096"
JELLYFIN_API_KEY = "0b77ac9b41fa4bc1ad4a81963eb278bb"

# Nombre EXACTO de la librería que quieres considerar "Películas"
PELICULAS_LIBRARY_NAME = "Películas"

# Regex para tu naming: tmdb_123__Lo_que_sea.mp4
TMDB_RE = re.compile(r"tmdb_(\d+)__.*\.(mp4|mkv|avi|mov)$", re.IGNORECASE)


def auth_headers() -> Dict[str, str]:
    return {"X-Emby-Token": JELLYFIN_API_KEY, "Accept": "application/json"}


def jellyfin_get(path: str, params: Optional[Dict] = None) -> Dict:
    url = f"{JELLYFIN_URL.rstrip('/')}{path}"
    r = requests.get(url, headers=auth_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def get_user_id() -> str:
    users = jellyfin_get("/Users")
    if not users:
        raise RuntimeError("No hay usuarios o la API key no tiene permisos.")
    return users[0]["Id"]


def get_views(user_id: str) -> List[Dict]:
    data = jellyfin_get(f"/Users/{user_id}/Views")
    return data.get("Items", [])


def find_library_id_by_name(views: List[Dict], name: str) -> str:
    wanted = name.strip().lower()
    for v in views:
        if (v.get("Name") or "").strip().lower() == wanted:
            return v["Id"]
    raise RuntimeError(
        f"No encontré la librería '{name}'. Revisa el nombre exacto en Jellyfin."
    )


def list_movies_tmdb_ids(user_id: str, library_id: str) -> Set[str]:
    tmdb_ids: Set[str] = set()
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
        items = page.get("Items", [])
        for it in items:
            pids = it.get("ProviderIds") or {}
            tmdb = pids.get("Tmdb")
            if tmdb:
                tmdb_ids.add(str(tmdb))

        if len(items) < limit:
            break
        start += limit

    return tmdb_ids


def scan_trailers(folder: Path) -> List[Path]:
    # Recorre recursivo por si tienes subcarpetas
    return [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".avi", ".mov"}]


def main() -> None:
    if len(sys.argv) < 2:
        print("USO:")
        print(r"  python .\list_trailers_not_peliculas.py E:\_Trailers")
        sys.exit(1)

    trailers_dir = Path(sys.argv[1])
    if not trailers_dir.exists():
        print(f"ERROR: No existe la ruta: {trailers_dir}")
        sys.exit(1)

    # 1) Jellyfin: sacar TMDb ids de la librería Películas
    user_id = get_user_id()
    views = get_views(user_id)
    peliculas_id = find_library_id_by_name(views, PELICULAS_LIBRARY_NAME)
    peliculas_tmdb = list_movies_tmdb_ids(user_id, peliculas_id)

    # 2) Escanear archivos descargados
    files = scan_trailers(trailers_dir)

    unknown = []
    not_peliculas = []

    for f in files:
        m = TMDB_RE.match(f.name)
        if not m:
            unknown.append(f)
            continue

        tmdb_id = m.group(1)
        if tmdb_id not in peliculas_tmdb:
            not_peliculas.append(f)

    # 3) Imprimir resultados
    print(f"Total archivos de trailer encontrados: {len(files)}")
    print(f"Coinciden con patrón tmdb_<id>__: {len(files) - len(unknown)}")
    print(f"NO pertenecen a '{PELICULAS_LIBRARY_NAME}': {len(not_peliculas)}")
    print(f"NO reconocidos (nombre no coincide patrón): {len(unknown)}")
    print()

    if not_peliculas:
        print(f"=== Trailers que NO son de '{PELICULAS_LIBRARY_NAME}' ===")
        for f in sorted(not_peliculas):
            print(str(f))

    if unknown:
        print("\n=== Archivos que NO pude clasificar (nombre raro) ===")
        for f in sorted(unknown):
            print(str(f))


if __name__ == "__main__":
    main()