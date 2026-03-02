#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Zenoverso - Jellyfin Home Extra Sections (YAML driven)

Qué hace:
- Lee un YAML con secciones (random / sports random por bibliotecas / random por colección / random por género / top rated shuffle)
- Expone un endpoint HTTP para que el Jellyfin Web (via JS inyectado) lo consuma
- NO reemplaza tu Home: el JS solo añade filas al final.

Requisitos:
  pip install requests pyyaml
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests

try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # type: ignore


# -------------------------
# Logging
# -------------------------

def setup_logging(log_path: Path, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.info("Log file: %s", log_path)


# -------------------------
# Jellyfin client
# -------------------------

def mb_authorization_header(api_key: str, client: str, device: str, device_id: str, version: str) -> str:
    def esc(v: str) -> str:
        return v.replace("\\", "\\\\").replace('"', '\\"')
    return (
        f'MediaBrowser Token="{esc(api_key)}", '
        f'Client="{esc(client)}", '
        f'Device="{esc(device)}", '
        f'DeviceId="{esc(device_id)}", '
        f'Version="{esc(version)}"'
    )


@dataclass
class JellyfinClient:
    base_url: str
    api_key: str
    verify_tls: bool
    timeout: int
    retries: int
    retry_base_sleep: float

    client_name: str = "Zenoverso-HomeExtraSections"
    client_version: str = "1.0.0"
    device_name: str = "Windows-Host"
    device_id: str = ""

    def __post_init__(self) -> None:
        if not self.device_id:
            self.device_id = f"zenohome-{uuid.getnode()}"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": mb_authorization_header(
                    self.api_key, self.client_name, self.device_name, self.device_id, self.client_version
                ),
                "X-Emby-Token": self.api_key,
                "Accept": "application/json",
            }
        )

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}{path}"
        last_err: Optional[Exception] = None

        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    timeout=self.timeout,
                    verify=self.verify_tls,
                )

                if resp.status_code in (429, 500, 502, 503, 504):
                    sleep_s = self.retry_base_sleep * (2 ** (attempt - 1))
                    logging.warning("HTTP %s %s -> %s (attempt %d/%d) retry %.1fs",
                                    method, path, resp.status_code, attempt, self.retries, sleep_s)
                    time.sleep(sleep_s)
                    continue

                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code} on {method} {path}. Body: {(resp.text or '')[:800]}")

                if not resp.text:
                    return {}
                return resp.json()

            except Exception as e:
                last_err = e
                sleep_s = self.retry_base_sleep * (2 ** (attempt - 1))
                logging.warning("Request error %s %s (attempt %d/%d): %r | retry %.1fs",
                                method, path, attempt, self.retries, e, sleep_s)
                time.sleep(sleep_s)

        raise RuntimeError(f"Request failed after {self.retries} retries: {method} {path}. Last error: {last_err!r}")

    # Views (bibliotecas) - por userId
    def get_views(self, user_id: str) -> List[Dict[str, Any]]:
        data = self.request("GET", f"/Users/{user_id}/Views", params={})
        items = data.get("Items") or data.get("items") or []
        return items if isinstance(items, list) else []

    # Buscar BoxSet por nombre
    def find_boxset_id_by_name(self, name: str) -> Optional[str]:
        params = {
            "includeItemTypes": "BoxSet",
            "recursive": "true",
            "searchTerm": name,
            "limit": "50",
        }
        data = self.request("GET", "/Items", params=params)
        items = data.get("Items") or data.get("items") or []
        if not isinstance(items, list):
            return None
        target = name.strip().casefold()
        exact = [it for it in items if str(it.get("Name") or it.get("name") or "").strip().casefold() == target]
        chosen = exact[0] if exact else (items[0] if items else None)
        if not chosen:
            return None
        cid = chosen.get("Id") or chosen.get("id")
        return str(cid) if cid else None

    # /Items helper
    def get_items(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        data = self.request("GET", "/Items", params=params)
        items = data.get("Items") or data.get("items") or []
        return items if isinstance(items, list) else []


# -------------------------
# YAML / config
# -------------------------

def load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("Falta PyYAML. Instala con: pip install pyyaml")
    raw = path.read_text(encoding="utf-8")
    cfg = yaml.safe_load(raw)
    if not isinstance(cfg, dict):
        raise RuntimeError("YAML inválido (no es un objeto raíz).")
    return cfg


# -------------------------
# Section builders
# -------------------------

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def join_csv(values: List[str]) -> str:
    return ",".join([v for v in values if v])


def join_pipe(values: List[str]) -> str:
    return "|".join([v for v in values if v])


def compact_item(it: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": it.get("Id") or it.get("id"),
        "name": it.get("Name") or it.get("name"),
        "type": it.get("Type") or it.get("type"),
        "year": it.get("ProductionYear") or it.get("productionYear"),
        "communityRating": it.get("CommunityRating") or it.get("communityRating"),
        "primaryImageTag": (it.get("ImageTags") or {}).get("Primary") if isinstance(it.get("ImageTags"), dict) else None,
    }


@dataclass
class CacheEntry:
    expires_at: float
    payload: Dict[str, Any]


class SectionEngine:
    def __init__(self, jf: JellyfinClient, cfg: Dict[str, Any]) -> None:
        self.jf = jf
        self.cfg = cfg
        self.cache: Dict[str, CacheEntry] = {}
        self.rng = random.Random()

    def _cached(self, key: str) -> Optional[Dict[str, Any]]:
        ent = self.cache.get(key)
        if not ent:
            return None
        if time.time() >= ent.expires_at:
            self.cache.pop(key, None)
            return None
        return ent.payload

    def _store(self, key: str, ttl: int, payload: Dict[str, Any]) -> None:
        self.cache[key] = CacheEntry(expires_at=time.time() + max(1, ttl), payload=payload)

    def resolve_library_ids(self, user_id: str) -> Dict[str, str]:
        views = self.jf.get_views(user_id)
        out: Dict[str, str] = {}
        for v in views:
            vid = v.get("Id") or v.get("id")
            name = v.get("Name") or v.get("name")
            if vid and name:
                out[str(name)] = str(vid)
        return out

    def build_section(self, section: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        sid = str(section.get("id") or "")
        ttl = int(section.get("ttl_seconds") or 0)
        if ttl > 0:
            cached = self._cached(sid)
            if cached:
                return cached

        stype = str(section.get("type") or "").strip()
        title = str(section.get("title") or sid)

        include_types = section.get("include_item_types") or []
        if not isinstance(include_types, list):
            include_types = [str(include_types)]
        include_types = [str(x) for x in include_types if str(x).strip()]

        limit = int(section.get("limit") or 30)
        fields = "PrimaryImageAspectRatio"  # ligero

        payload: Dict[str, Any] = {"id": sid, "title": title, "type": stype, "items": [], "generatedAt": now_iso()}

        if stype == "random":
            params = {
                "includeItemTypes": join_csv(include_types) if include_types else None,
                "recursive": "true",
                "sortBy": "Random",
                "limit": str(limit),
                "fields": fields,
            }
            params = {k: v for k, v in params.items() if v is not None}
            items = self.jf.get_items(params)
            payload["items"] = [compact_item(i) for i in items]

        elif stype == "random_mix_libraries":
            libs = section.get("libraries") or []
            if not isinstance(libs, list):
                libs = [str(libs)]
            libs = [str(x) for x in libs if str(x).strip()]
            per_pool = int(section.get("per_library_pool") or 120)

            name_to_id = self.resolve_library_ids(user_id)
            pool: List[Dict[str, Any]] = []
            for libname in libs:
                pid = name_to_id.get(libname)
                if not pid:
                    logging.warning("Sección %s: biblioteca no encontrada: %s", sid, libname)
                    continue
                params = {
                    "parentId": pid,
                    "includeItemTypes": join_csv(include_types) if include_types else None,
                    "recursive": "true",
                    "sortBy": "Random",
                    "limit": str(per_pool),
                    "fields": fields,
                }
                params = {k: v for k, v in params.items() if v is not None}
                pool.extend(self.jf.get_items(params))

            # baraja + recorta
            self.rng.shuffle(pool)
            payload["items"] = [compact_item(i) for i in pool[:limit]]

        elif stype == "random_from_collection":
            cname = str(section.get("collection_name") or "").strip()
            if not cname:
                raise RuntimeError(f"Sección {sid}: collection_name vacío")
            boxset_id = self.jf.find_boxset_id_by_name(cname)
            if not boxset_id:
                logging.warning("Sección %s: NO encontrada colección %r", sid, cname)
                payload["items"] = []
            else:
                params = {
                    "parentId": boxset_id,
                    "includeItemTypes": join_csv(include_types) if include_types else None,
                    "recursive": "true",
                    "sortBy": "Random",
                    "limit": str(limit),
                    "fields": fields,
                }
                params = {k: v for k, v in params.items() if v is not None}
                items = self.jf.get_items(params)
                payload["items"] = [compact_item(i) for i in items]

        elif stype == "random_from_genre":
            genre = str(section.get("genre") or "").strip()
            if not genre:
                raise RuntimeError(f"Sección {sid}: genre vacío")
            params = {
                "includeItemTypes": join_csv(include_types) if include_types else None,
                "recursive": "true",
                "genres": join_pipe([genre]),   # Jellyfin espera pipe-delimited
                "sortBy": "Random",
                "limit": str(limit),
                "fields": fields,
            }
            params = {k: v for k, v in params.items() if v is not None}
            items = self.jf.get_items(params)
            payload["items"] = [compact_item(i) for i in items]

        elif stype == "top_rated_shuffle":
            min_rating = float(section.get("min_community_rating") or 7.5)
            pool_limit = int(section.get("pool_limit") or 300)
            params = {
                "includeItemTypes": join_csv(include_types) if include_types else None,
                "recursive": "true",
                "minCommunityRating": str(min_rating),
                "sortBy": "CommunityRating",
                "sortOrder": "Descending",
                "limit": str(pool_limit),
                "fields": fields,
            }
            params = {k: v for k, v in params.items() if v is not None}
            pool = self.jf.get_items(params)
            self.rng.shuffle(pool)
            payload["items"] = [compact_item(i) for i in pool[:limit]]

        else:
            raise RuntimeError(f"Tipo de sección no soportado: {stype}")

        if ttl > 0:
            self._store(sid, ttl, payload)

        return payload

    def build_all(self, user_id: str, force_refresh: bool = False) -> Dict[str, Any]:
        if force_refresh:
            self.cache.clear()

        sections = self.cfg.get("sections") or []
        if not isinstance(sections, list):
            raise RuntimeError("cfg.sections debe ser una lista")

        out_sections: List[Dict[str, Any]] = []
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            try:
                out_sections.append(self.build_section(sec, user_id=user_id))
            except Exception as e:
                sid = sec.get("id")
                logging.exception("Error generando sección %r: %r", sid, e)
                out_sections.append({
                    "id": sid,
                    "title": sec.get("title") or sid,
                    "type": sec.get("type"),
                    "items": [],
                    "error": str(e),
                    "generatedAt": now_iso(),
                })

        return {
            "generatedAt": now_iso(),
            "userId": user_id,
            "sections": out_sections,
        }


# -------------------------
# HTTP server
# -------------------------

class Handler(BaseHTTPRequestHandler):
    engine: SectionEngine
    cors_allow_origin: str

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", self.cors_allow_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.cors_allow_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path in ("/health", "/healthz"):
            return self._send_json({"ok": True, "ts": now_iso()})

        if parsed.path == "/api/sections":
            user_id = (qs.get("userId") or [""])[0].strip()
            if not user_id:
                return self._send_json(
                    {"error": "Missing userId query param. Call /api/sections?userId=<JellyfinUserId>"},
                    status=400,
                )

            force = ((qs.get("refresh") or ["0"])[0] == "1")
            data = self.engine.build_all(user_id=user_id, force_refresh=force)
            return self._send_json(data)

        return self._send_json({"error": "not_found"}, status=404)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Ruta al YAML")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"No existe el config: {cfg_path}")
        return 2

    cfg = load_yaml(cfg_path)

    # server cfg
    s = cfg.get("server") or {}
    jellyfin_url = str(s.get("jellyfin_url") or "").rstrip("/")
    jellyfin_key = str(s.get("jellyfin_api_key") or "")
    verify_tls = bool(s.get("verify_tls") or False)
    timeout = int(s.get("timeout_seconds") or 30)
    retries = int(s.get("retries") or 5)
    retry_sleep = float(s.get("retry_base_sleep") or 0.8)

    if not jellyfin_url or not jellyfin_key:
        print("Falta server.jellyfin_url o server.jellyfin_api_key en el YAML")
        return 2

    # http cfg
    h = cfg.get("http") or {}
    bind = str(h.get("bind") or "127.0.0.1")
    port = int(h.get("port") or 8787)
    cors = str(h.get("cors_allow_origin") or "*")

    script_dir = Path(__file__).resolve().parent
    log_path = script_dir / f"jellyfin_home_extra_sections_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    setup_logging(log_path, args.verbose)

    jf = JellyfinClient(
        base_url=jellyfin_url,
        api_key=jellyfin_key,
        verify_tls=verify_tls,
        timeout=timeout,
        retries=retries,
        retry_base_sleep=retry_sleep,
    )
    engine = SectionEngine(jf=jf, cfg=cfg)

    Handler.engine = engine
    Handler.cors_allow_origin = cors

    httpd = ThreadingHTTPServer((bind, port), Handler)
    logging.info("Serving on http://%s:%d  (CORS allow origin=%s)", bind, port, cors)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logging.info("Stopping...")
    finally:
        httpd.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())