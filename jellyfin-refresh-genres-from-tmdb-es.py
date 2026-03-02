from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

# =========================
# CONFIG DEFAULTS
# =========================
DEFAULT_TYPES = ["Movie", "Series"]
DEFAULT_PAGE_SIZE = 500
DEFAULT_TIMEOUT = 30
DEFAULT_VERIFY_TLS_JELLYFIN = False  # tu Jellyfin es http, pero lo dejo por consistencia
TMDB_VERIFY_TLS = True  # IMPORTANTÍSIMO: TMDb SIEMPRE con TLS verificado


# =========================
# HELPERS
# =========================
def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def setup_logger(log_path: str) -> logging.Logger:
    logger = logging.getLogger("genres_sync")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


def chunks(lst: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def safe_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def norm_set(xs: List[str]) -> Tuple[str, ...]:
    # comparación por contenido, ignorando orden, espacios repetidos, etc.
    cleaned = []
    for x in xs or []:
        if not isinstance(x, str):
            continue
        t = " ".join(x.strip().split())
        if t:
            cleaned.append(t)
    return tuple(sorted(set(cleaned), key=lambda s: s.casefold()))


def get_env_required(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"Falta variable de entorno obligatoria: {name}")
    return v


def jellyfin_headers(api_key: str) -> Dict[str, str]:
    # Header estilo Jellyfin (MediaBrowser)
    # Además meto X-Emby-Token como “compat” (no rompe).
    auth = (
        'MediaBrowser Token="{token}", Client="GenreSync", Device="Windows", '
        'DeviceId="genresync", Version="1.0.0"'
    ).format(token=api_key)
    return {
        "Authorization": auth,
        "X-Emby-Token": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def tmdb_headers(bearer: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {bearer}",
        "Accept": "application/json",
    }


@dataclass
class HttpCfg:
    timeout: int = DEFAULT_TIMEOUT
    verify_tls_jellyfin: bool = DEFAULT_VERIFY_TLS_JELLYFIN


class HttpClient:
    def __init__(self, logger: logging.Logger, cfg: HttpCfg):
        self.logger = logger
        self.cfg = cfg
        self.s = requests.Session()

    def request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        verify: Optional[bool] = None,
        retries: int = 5,
        backoff: float = 0.8,
    ) -> Any:
        vfy = self.cfg.verify_tls_jellyfin if verify is None else verify

        last_err: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                r = self.s.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=self.cfg.timeout,
                    verify=vfy,
                )

                # Rate limit / transient
                if r.status_code in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"HTTP {r.status_code} transient")

                if r.status_code >= 400:
                    body = r.text[:8000]
                    raise RuntimeError(f"HTTP {r.status_code} on {method} {url}. Body(first8k): {body}")

                if r.status_code == 204 or not r.content:
                    return None

                ct = (r.headers.get("Content-Type") or "").lower()
                if "application/json" in ct:
                    return r.json()
                return r.text

            except Exception as e:
                last_err = e
                if attempt < retries:
                    sleep_s = backoff * (2 ** (attempt - 1))
                    self.logger.warning(
                        f"Request error {method} {url} (attempt {attempt}/{retries}): {repr(e)} | retry {sleep_s:.1f}s"
                    )
                    time.sleep(sleep_s)
                else:
                    break
        raise last_err or RuntimeError("Unknown HTTP error")


# =========================
# TMDb
# =========================
class TmdbClient:
    def __init__(self, http: HttpClient, logger: logging.Logger, bearer: str, language: str = "es-ES"):
        self.http = http
        self.logger = logger
        self.bearer = bearer
        self.language = language
        self.base = "https://api.themoviedb.org/3"
        self.hdr = tmdb_headers(bearer)

        # maps: id -> spanish name
        self.movie_genre_map: Dict[int, str] = {}
        self.tv_genre_map: Dict[int, str] = {}

    def load_genre_maps(self) -> None:
        self.movie_genre_map = self._get_genre_map("movie")
        self.tv_genre_map = self._get_genre_map("tv")

    def _get_genre_map(self, kind: str) -> Dict[int, str]:
        data = self.http.request(
            "GET",
            f"{self.base}/genre/{kind}/list",
            headers=self.hdr,
            params={"language": self.language},
            verify=TMDB_VERIFY_TLS,
        )
        out: Dict[int, str] = {}
        for g in (data or {}).get("genres", []) or []:
            try:
                gid = int(g.get("id"))
                name = str(g.get("name", "")).strip()
                if name:
                    out[gid] = name
            except Exception:
                continue
        return out

    def find_tmdb_by_imdb(self, imdb_id: str) -> Tuple[Optional[int], Optional[str]]:
        # /find/{external_id}
        data = self.http.request(
            "GET",
            f"{self.base}/find/{imdb_id}",
            headers=self.hdr,
            params={"external_source": "imdb_id", "language": self.language},
            verify=TMDB_VERIFY_TLS,
        )
        # prefer movie if present, else tv
        movie_results = (data or {}).get("movie_results") or []
        tv_results = (data or {}).get("tv_results") or []
        if movie_results:
            return int(movie_results[0]["id"]), "movie"
        if tv_results:
            return int(tv_results[0]["id"]), "tv"
        return None, None

    def get_genres_for_tmdb(self, kind: str, tmdb_id: int) -> Tuple[List[str], bool]:
        """
        Returns (genres_in_spanish, ok)
        - Uses genre maps to force spanish names by ID.
        - If endpoint 404, caller can decide fallback.
        """
        data = self.http.request(
            "GET",
            f"{self.base}/{kind}/{tmdb_id}",
            headers=self.hdr,
            params={"language": self.language},
            verify=TMDB_VERIFY_TLS,
        )

        genres = (data or {}).get("genres") or []
        names: List[str] = []
        gmap = self.movie_genre_map if kind == "movie" else self.tv_genre_map

        for g in genres:
            gid = g.get("id")
            gname = str(g.get("name", "")).strip()
            if gid is not None:
                try:
                    gid_int = int(gid)
                    # fuerza español por ID si existe en map
                    names.append(gmap.get(gid_int, gname) or gname)
                    continue
                except Exception:
                    pass
            if gname:
                names.append(gname)

        # limpia duplicados respetando orden
        seen = set()
        out = []
        for n in names:
            n2 = " ".join(n.strip().split())
            if not n2:
                continue
            key = n2.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(n2)
        return out, True


# =========================
# Jellyfin
# =========================
class JellyfinClient:
    def __init__(self, http: HttpClient, logger: logging.Logger, base_url: str, api_key: str):
        self.http = http
        self.logger = logger
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.hdr = jellyfin_headers(api_key)

    def get_users(self) -> List[Dict[str, Any]]:
        return self.http.request("GET", f"{self.base}/Users", headers=self.hdr) or []

    def pick_user_id(self) -> str:
        # Permite override por env
        env_uid = os.environ.get("JELLYFIN_USER_ID", "").strip()
        if env_uid:
            return env_uid

        users = self.get_users()
        if not users:
            raise RuntimeError("No pude obtener /Users para seleccionar un userId (¿API key sin permisos?)")

        # prefer admin
        for u in users:
            pol = (u.get("Policy") or {})
            if pol.get("IsAdministrator") is True and u.get("Id"):
                return str(u["Id"])

        # fallback: first
        if users[0].get("Id"):
            return str(users[0]["Id"])

        raise RuntimeError("No pude determinar userId (no hay Id en /Users)")

    def iter_items(self, include_types: List[str], page_size: int) -> Iterable[Dict[str, Any]]:
        start = 0
        total = None
        fields = "ProviderIds,Genres"  # suficiente para comparar, y decidir si actualiza
        while total is None or start < total:
            params = {
                "Recursive": "true",
                "IncludeItemTypes": ",".join(include_types),
                "Fields": fields,
                "StartIndex": start,
                "Limit": page_size,
                "EnableTotalRecordCount": "true",
            }
            data = self.http.request("GET", f"{self.base}/Items", headers=self.hdr, params=params)
            items = (data or {}).get("Items") or []
            total = (data or {}).get("TotalRecordCount") or len(items)
            self.logger.info(f"Fetched items: {min(start+len(items), total)} (start={start} got={len(items)} total={total})")
            for it in items:
                yield it
            start += page_size

    def get_item_dto_for_update(self, user_id: str, item_id: str) -> Dict[str, Any]:
        # En vez de GET /Items/{itemId} (que puede dar 405), usamos el endpoint de usuario
        # y pedimos los campos “peligrosos” (listas) para que no vengan null.
        fields = ",".join(
            [
                "Genres",
                "Tags",
                "Studios",
                "People",
                "ProviderIds",
                "Taglines",
                "ProductionLocations",
                "ExternalUrls",
                "ExtraIds",
                "MediaStreams",
                "MediaSources",
                "RemoteTrailers",
            ]
        )
        params = {
            "Ids": item_id,
            "Recursive": "false",
            "Fields": fields,
            "EnableTotalRecordCount": "false",
            "EnableUserData": "false",
            "EnableImages": "false",
        }
        data = self.http.request("GET", f"{self.base}/Users/{user_id}/Items", headers=self.hdr, params=params)
        items = (data or {}).get("Items") or []
        if not items:
            raise RuntimeError(f"No pude obtener DTO para item {item_id} vía /Users/{user_id}/Items?Ids=")
        return items[0]

    def update_item(self, item_id: str, dto: Dict[str, Any]) -> None:
        # IMPORTANT: POST /Items/{itemId} con DTO “completo”
        self.http.request("POST", f"{self.base}/Items/{item_id}", headers=self.hdr, json_body=dto)


# =========================
# MAIN LOGIC
# =========================
def extract_provider_id(provider_ids: Dict[str, Any], key: str) -> Optional[str]:
    if not provider_ids:
        return None
    # case-insensitive
    for k, v in provider_ids.items():
        if str(k).casefold() == key.casefold():
            if v is None:
                return None
            s = str(v).strip()
            return s if s else None
    return None


def ensure_non_null_lists(dto: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evita el bug de Jellyfin UpdateItem (ToList sobre null).
    Forzamos a [] las listas típicas.
    """
    list_keys = [
        "Genres",
        "Tags",
        "Studios",
        "People",
        "Taglines",
        "ProductionLocations",
        "ExternalUrls",
        "ExtraIds",
        "RemoteTrailers",
        "MediaStreams",
        "MediaSources",
    ]
    for k in list_keys:
        if k in dto and dto[k] is None:
            dto[k] = []
        elif k not in dto:
            # No inventamos estructuras complejas, pero para listas simples es seguro poner []
            dto[k] = []

    # ProviderIds debería ser dict, no lista
    if dto.get("ProviderIds") is None:
        dto["ProviderIds"] = {}

    return dto


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Sync Genres in Jellyfin from TMDb (Spanish). Safe mode: sends full DTO to avoid Jellyfin UpdateItem null-list bug."
    )
    ap.add_argument("--yes", action="store_true", help="Aplicar cambios (por defecto es DRY-RUN)")
    ap.add_argument("--types", nargs="+", default=DEFAULT_TYPES, help="Tipos Jellyfin a procesar (Movie Series)")
    ap.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="Paginación Jellyfin")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Timeout HTTP")
    ap.add_argument("--verify-tls", action="store_true", help="Verificar TLS contra Jellyfin (si usas https válido)")
    ap.add_argument("--only-empty", action="store_true", help="Solo actualiza items que tienen Genres vacío en Jellyfin")
    args = ap.parse_args()

    stamp = now_stamp()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(base_dir, f"jellyfin_genres_sync_{stamp}.log")
    report_path = os.path.join(base_dir, f"jellyfin_genres_sync_report_{stamp}.json")
    logger = setup_logger(log_path)

    jellyfin_url = get_env_required("JELLYFIN_URL")
    jellyfin_api_key = get_env_required("JELLYFIN_API_KEY")
    tmdb_bearer = get_env_required("TMDB_BEARER_TOKEN")

    dry_run = not args.yes
    cfg = HttpCfg(timeout=args.timeout, verify_tls_jellyfin=bool(args.verify_tls))
    http = HttpClient(logger, cfg)

    jf = JellyfinClient(http, logger, jellyfin_url, jellyfin_api_key)
    tmdb = TmdbClient(http, logger, tmdb_bearer, language="es-ES")

    logger.info(f"Log file: {log_path}")
    logger.info(f"Jellyfin URL: {jellyfin_url} | DRY_RUN={dry_run} | Types={args.types} | ONLY_EMPTY={args.only_empty}")
    logger.info(f"VERIFY_TLS_JELLYFIN={cfg.verify_tls_jellyfin} | TIMEOUT={cfg.timeout}s | TMDB_TLS_VERIFY={TMDB_VERIFY_TLS}")

    # userId necesario para obtener DTO completo “safe”
    user_id = jf.pick_user_id()
    logger.info(f"UserId seleccionado: {user_id} (override posible con env:JELLYFIN_USER_ID)")

    # TMDb genre maps (para forzar español por ID)
    tmdb.load_genre_maps()
    logger.info(f"TMDb genre maps cargados: movie={len(tmdb.movie_genre_map)} tv={len(tmdb.tv_genre_map)}")

    stats = {
        "total": 0,
        "with_tmdb": 0,
        "with_imdb_fallback": 0,
        "no_external_id": 0,
        "tmdb_404_or_missing": 0,
        "unchanged": 0,
        "would_update": 0,
        "updated": 0,
        "failed": 0,
        "skipped_only_empty": 0,
    }

    report: Dict[str, Any] = {
        "timestamp": stamp,
        "dry_run": dry_run,
        "types": args.types,
        "only_empty": args.only_empty,
        "items": [],
        "stats": stats,
    }

    idx = 0
    for item in jf.iter_items(args.types, args.page_size):
        idx += 1
        stats["total"] += 1

        item_id = str(item.get("Id") or "")
        name = str(item.get("Name") or "").strip()
        itype = str(item.get("Type") or "").strip()

        provider_ids = item.get("ProviderIds") or {}
        tmdb_id_s = extract_provider_id(provider_ids, "Tmdb")
        imdb_id = extract_provider_id(provider_ids, "Imdb")

        current_genres = safe_list(item.get("Genres"))
        if args.only_empty and norm_set([str(x) for x in current_genres]) != ():
            stats["skipped_only_empty"] += 1
            continue

        tmdb_kind = "movie" if itype == "Movie" else "tv" if itype == "Series" else None

        tmdb_id: Optional[int] = None
        used_imdb_fallback = False

        if tmdb_id_s:
            try:
                tmdb_id = int(tmdb_id_s)
            except Exception:
                tmdb_id = None

        if tmdb_id is None and imdb_id:
            # fallback: /find por imdb
            fid, fkind = tmdb.find_tmdb_by_imdb(imdb_id)
            if fid is not None:
                tmdb_id = fid
                used_imdb_fallback = True
                # si fkind contradice, lo usamos (mejor que nada)
                if fkind in ("movie", "tv"):
                    tmdb_kind = fkind

        if tmdb_id is None or tmdb_kind is None:
            stats["no_external_id"] += 1
            logger.warning(f"({idx}/{stats['total']}) SKIP sin TMDb/IMDb usable: {name} [{itype}] (id={item_id})")
            continue

        if used_imdb_fallback:
            stats["with_imdb_fallback"] += 1
        else:
            stats["with_tmdb"] += 1

        # Obtener géneros en ES desde TMDb
        desired_genres: List[str] = []
        ok = False
        try:
            desired_genres, ok = tmdb.get_genres_for_tmdb(tmdb_kind, tmdb_id)
        except Exception as e:
            # si fue 404, probamos el otro endpoint (movie <-> tv)
            msg = str(e)
            if "HTTP 404" in msg:
                try:
                    other = "tv" if tmdb_kind == "movie" else "movie"
                    desired_genres, ok = tmdb.get_genres_for_tmdb(other, tmdb_id)
                    tmdb_kind = other
                except Exception:
                    ok = False
            else:
                ok = False

        if not ok or not desired_genres:
            stats["tmdb_404_or_missing"] += 1
            logger.warning(f"({idx}/{stats['total']}) SKIP TMDb sin géneros: {name} [{itype}] tmdb={tmdb_id} kind={tmdb_kind}")
            continue

        cur_norm = norm_set([str(x) for x in current_genres])
        des_norm = norm_set([str(x) for x in desired_genres])

        if cur_norm == des_norm:
            stats["unchanged"] += 1
            logger.info(f"({idx}/{stats['total']}) OK sin cambios: {name} [{itype}]")
            report["items"].append(
                {
                    "id": item_id,
                    "name": name,
                    "type": itype,
                    "tmdb_id": tmdb_id,
                    "tmdb_kind": tmdb_kind,
                    "action": "unchanged",
                    "current": list(cur_norm),
                    "desired": list(des_norm),
                }
            )
            continue

        # Cambios
        if dry_run:
            stats["would_update"] += 1
            logger.info(f"({idx}/{stats['total']}) DRY-RUN {name} [{itype}] -> {', '.join(desired_genres)}")
            report["items"].append(
                {
                    "id": item_id,
                    "name": name,
                    "type": itype,
                    "tmdb_id": tmdb_id,
                    "tmdb_kind": tmdb_kind,
                    "action": "would_update",
                    "current": list(cur_norm),
                    "desired": list(des_norm),
                }
            )
            continue

        # APPLY: necesitamos DTO completo “safe”
        try:
            dto = jf.get_item_dto_for_update(user_id, item_id)
            dto = ensure_non_null_lists(dto)

            # solo tocamos Genres (lo demás lo dejamos tal cual)
            dto["Genres"] = desired_genres

            jf.update_item(item_id, dto)
            stats["updated"] += 1
            logger.info(f"({idx}/{stats['total']}) UPDATED {name} [{itype}] -> {', '.join(desired_genres)}")

            report["items"].append(
                {
                    "id": item_id,
                    "name": name,
                    "type": itype,
                    "tmdb_id": tmdb_id,
                    "tmdb_kind": tmdb_kind,
                    "action": "updated",
                    "current": list(cur_norm),
                    "desired": list(des_norm),
                }
            )

        except Exception as e:
            stats["failed"] += 1
            logger.error(f"UPDATE ERROR: {name} [{itype}] (id={item_id}) | {repr(e)}")
            report["items"].append(
                {
                    "id": item_id,
                    "name": name,
                    "type": itype,
                    "tmdb_id": tmdb_id,
                    "tmdb_kind": tmdb_kind,
                    "action": "failed",
                    "error": repr(e),
                    "current": list(cur_norm),
                    "desired": list(des_norm),
                }
            )

    report["stats"] = stats
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info(f"Reporte JSON: {report_path}\n")
    print("\n================= RESUMEN =================")
    print(f"Dry-run: {dry_run}")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"Reporte: {report_path}")
    print(f"Log:     {log_path}")
    print("===========================================\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())