#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Jellyfin: Add movies listed in a TXT file to a Collection, matching ONLY by filename.

How it works (robust + idempotent):
1) Reads TXT: one line per movie filename (e.g. "La guía del autoestopista galáctico (2005).mkv")
2) Builds an index of ALL Jellyfin Movie items by basename(MediaSources.Path)
   - comparison is filename-only (basename), normalized to ignore accents + case
3) Creates collection if missing
4) Adds only missing items to that collection

Important:
- No title matching, no searchTerm logic for matching.
- If a filename maps to multiple items (duplicate basenames), we SKIP and report ambiguity.

API:
- GET /Items with includeItemTypes=Movie, recursive=true, startIndex/limit, enableTotalRecordCount, fields=MediaSources  (pagination)  :contentReference[oaicite:3]{index=3}
- POST /Collections (create) and POST /Collections/{collectionId}/Items (add ids) :contentReference[oaicite:4]{index=4}
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# =========================
# CONFIG
# =========================

LIST_FILE = r"D:\Github-zen0s4ma\zenomedia-server\conf\listado-pelis-add-collection.txt"

# Cambia esto y ya:
COLLECTION_NAME = "Comedia absurda"

# Jellyfin (mejor por env vars)
JELLYFIN_URL = os.getenv("JELLYFIN_URL", "http://192.168.1.113:8096").rstrip("/")
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "")

# Identidad del cliente
CLIENT_NAME = os.getenv("JELLYFIN_CLIENT_NAME", "Zenoverso-Filename2Collection")
CLIENT_VERSION = os.getenv("JELLYFIN_CLIENT_VERSION", "1.0.0")
DEVICE_NAME = os.getenv("JELLYFIN_DEVICE_NAME", "Windows-Host")
DEVICE_ID = os.getenv("JELLYFIN_DEVICE_ID", f"zenoverso-{uuid.getnode()}")

# HTTP
VERIFY_TLS = os.getenv("JELLYFIN_VERIFY_TLS", "false").lower() in ("1", "true", "yes", "y")
TIMEOUT_SECONDS = int(os.getenv("JELLYFIN_TIMEOUT", "30"))
RETRIES = int(os.getenv("JELLYFIN_RETRIES", "5"))
RETRY_BASE_SLEEP = float(os.getenv("JELLYFIN_RETRY_BASE_SLEEP", "0.8"))

# Pagination for indexing Movies
PAGE_SIZE = int(os.getenv("JELLYFIN_PAGE_SIZE", "500"))

# Batch add ids to collection (avoid huge URLs)
ADD_BATCH_SIZE = int(os.getenv("JELLYFIN_ADD_BATCH_SIZE", "50"))


# =========================
# Output files
# =========================

SCRIPT_DIR = Path(__file__).resolve().parent
NOW_TAG = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_PATH = SCRIPT_DIR / f"jellyfin_filename2collection_{NOW_TAG}.log"
REPORT_PATH = SCRIPT_DIR / f"jellyfin_filename2collection_report_{NOW_TAG}.json"
MISSING_PATH = SCRIPT_DIR / f"jellyfin_filename2collection_missing_{NOW_TAG}.txt"
AMBIGUOUS_PATH = SCRIPT_DIR / f"jellyfin_filename2collection_ambiguous_{NOW_TAG}.txt"


# =========================
# Helpers
# =========================

def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.info("Log file: %s", LOG_PATH)


def mb_authorization_header(api_key: str) -> str:
    # Modern Authorization header
    def esc(v: str) -> str:
        return v.replace("\\", "\\\\").replace('"', '\\"')

    return (
        f'MediaBrowser Token="{esc(api_key)}", '
        f'Client="{esc(CLIENT_NAME)}", '
        f'Device="{esc(DEVICE_NAME)}", '
        f'DeviceId="{esc(DEVICE_ID)}", '
        f'Version="{esc(CLIENT_VERSION)}"'
    )


def normalize_filename(name: str) -> str:
    """
    Filename-only comparison:
    - basename only
    - ignore accents/diacritics
    - case-insensitive
    - normalize whitespace
    """
    name = Path(name).name.strip()
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = name.casefold()
    name = re.sub(r"\s+", " ", name).strip()
    return name


def safe_read_lines(path: Path) -> List[str]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            lines = [ln.strip() for ln in text.splitlines()]
            return [ln for ln in lines if ln and not ln.startswith("#")]
        except Exception:
            continue
    raise RuntimeError(f"No pude leer el archivo {path} (encoding raro). Guárdalo como UTF-8.")


def chunked(seq: List[str], n: int) -> List[List[str]]:
    return [seq[i : i + n] for i in range(0, len(seq), n)]


# =========================
# Jellyfin client
# =========================

@dataclass
class JellyfinClient:
    base_url: str
    api_key: str
    verify_tls: bool
    timeout: int
    retries: int
    retry_base_sleep: float

    def __post_init__(self) -> None:
        self.session = requests.Session()
        # Máxima compatibilidad: ambos headers
        self.session.headers.update(
            {
                "Authorization": mb_authorization_header(self.api_key),
                "X-Emby-Token": self.api_key,
                "Accept": "application/json",
            }
        )

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        url = f"{self.base_url}{path}"
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
                    logging.warning(
                        "HTTP %s %s -> %s (attempt %d/%d). Retry in %.1fs. Body: %s",
                        method, path, resp.status_code, attempt, self.retries, sleep_s,
                        (resp.text or "")[:300].replace("\n", " "),
                    )
                    time.sleep(sleep_s)
                    continue

                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code} on {method} {path}. Body: {(resp.text or '')[:800]}")

                return resp

            except Exception as e:
                last_err = e
                sleep_s = self.retry_base_sleep * (2 ** (attempt - 1))
                logging.warning(
                    "Request error %s %s (attempt %d/%d): %r | retry %.1fs",
                    method, path, attempt, self.retries, e, sleep_s
                )
                time.sleep(sleep_s)

        raise RuntimeError(f"Request failed after {self.retries} retries: {method} {path}. Last error: {last_err!r}")

    # ---- Build filename index (Movies) ----
    def iter_all_movies_with_mediasources(self) -> List[Dict[str, Any]]:
        """
        Fetch ALL Movie items with MediaSources (paged).
        """
        out: List[Dict[str, Any]] = []
        start = 0

        while True:
            params = {
                "includeItemTypes": "Movie",
                "recursive": "true",
                "startIndex": str(start),
                "limit": str(PAGE_SIZE),
                "enableTotalRecordCount": "true",
                "fields": "MediaSources",
            }
            r = self.request("GET", "/Items", params=params)
            data = r.json() if r.text else {}
            items = data.get("Items") or data.get("items") or []
            total = data.get("TotalRecordCount")
            if total is None:
                total = data.get("totalRecordCount")

            if not isinstance(items, list):
                raise RuntimeError(f"Unexpected /Items payload shape: {data}")

            out.extend(items)
            logging.info("Indexing Movies: fetched=%d (start=%d got=%d total=%s)", len(out), start, len(items), str(total))

            if len(items) < PAGE_SIZE:
                break

            start += len(items)
            if isinstance(total, int) and start >= total:
                break

        return out

    # ---- Collections ----
    def find_collection_by_name(self, collection_name: str) -> Optional[Dict[str, Any]]:
        # Simple search by name (NOT used for matching movies, only to locate the collection itself)
        params = {
            "includeItemTypes": "BoxSet",
            "recursive": "true",
            "searchTerm": collection_name,
            "limit": "50",
        }
        r = self.request("GET", "/Items", params=params)
        data = r.json() if r.text else {}
        items = data.get("Items") or data.get("items") or []
        if not isinstance(items, list):
            return None

        target = collection_name.strip().casefold()
        exact = [it for it in items if str(it.get("Name") or it.get("name") or "").strip().casefold() == target]
        if exact:
            return exact[0]
        return items[0] if items else None

    def create_collection(self, collection_name: str) -> str:
        params = {"name": collection_name}
        r = self.request("POST", "/Collections", params=params)
        data = r.json() if r.text else {}
        cid = data.get("Id") or data.get("id")
        if not cid:
            raise RuntimeError(f"CreateCollection devolvió algo raro: {data}")
        return str(cid)

    def list_items_in_collection(self, collection_id: str) -> List[str]:
        params = {
            "parentId": collection_id,
            "recursive": "true",
            "includeItemTypes": "Movie",
            "limit": "10000",
            "enableTotalRecordCount": "true",
        }
        r = self.request("GET", "/Items", params=params)
        data = r.json() if r.text else {}
        items = data.get("Items") or data.get("items") or []
        ids: List[str] = []
        if isinstance(items, list):
            for it in items:
                iid = it.get("Id") or it.get("id")
                if iid:
                    ids.append(str(iid))
        return ids

    def add_to_collection(self, collection_id: str, item_ids: List[str]) -> None:
        params = {"ids": ",".join(item_ids)}
        self.request("POST", f"/Collections/{collection_id}/Items", params=params)


# =========================
# Core logic
# =========================

def build_filename_index(movies: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """
    Returns:
      dict[norm_basename] = [itemId, itemId, ...]
    If duplicates exist, list length > 1.
    """
    idx: Dict[str, List[str]] = {}

    for it in movies:
        item_id = it.get("Id") or it.get("id")
        if not item_id:
            continue
        item_id = str(item_id)

        media_sources = it.get("MediaSources") or it.get("mediaSources") or []
        if not isinstance(media_sources, list):
            continue

        for ms in media_sources:
            p = ms.get("Path") or ms.get("path")
            if not p:
                continue
            base = Path(str(p)).name
            key = normalize_filename(base)
            idx.setdefault(key, [])
            if item_id not in idx[key]:
                idx[key].append(item_id)

    return idx


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Añade a una colección de Jellyfin las pelis listadas en un TXT, haciendo match SOLO por filename.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--list-file", default=LIST_FILE, help="Ruta al TXT (1 filename por línea).")
    parser.add_argument("--collection", default=COLLECTION_NAME, help="Nombre de la colección destino.")
    parser.add_argument("--yes", action="store_true", help="Aplica cambios (si no, es dry-run).")
    parser.add_argument("--verbose", action="store_true", help="Logs más detallados.")
    args = parser.parse_args()

    setup_logging(args.verbose)

    if not JELLYFIN_API_KEY.strip():
        logging.error("Falta JELLYFIN_API_KEY (ponlo en env var).")
        return 2

    list_path = Path(args.list_file)
    if not list_path.exists():
        logging.error("No existe el listado: %s", list_path)
        return 2

    collection_name = args.collection.strip()
    if not collection_name:
        logging.error("Nombre de colección vacío.")
        return 2

    jf = JellyfinClient(
        base_url=JELLYFIN_URL,
        api_key=JELLYFIN_API_KEY,
        verify_tls=VERIFY_TLS,
        timeout=TIMEOUT_SECONDS,
        retries=RETRIES,
        retry_base_sleep=RETRY_BASE_SLEEP,
    )

    dry_run = not args.yes
    logging.info("Jellyfin URL: %s | VERIFY_TLS=%s | DRY_RUN=%s", JELLYFIN_URL, VERIFY_TLS, dry_run)
    logging.info("List file: %s", list_path)
    logging.info("Collection: %s", collection_name)

    lines = safe_read_lines(list_path)
    wanted_filenames = [Path(ln).name for ln in lines]
    wanted_keys = [normalize_filename(fn) for fn in wanted_filenames]
    logging.info("Entradas en listado: %d", len(wanted_filenames))

    # 1) Build index once (ONLY filename-based)
    logging.info("Construyendo índice de filenames (Movies) desde Jellyfin...")
    movies = jf.iter_all_movies_with_mediasources()
    idx = build_filename_index(movies)
    logging.info("Índice construido: %d filenames únicos (norm).", len(idx))

    results: List[Dict[str, Any]] = []
    matched_ids: List[str] = []
    missing: List[str] = []
    ambiguous: List[str] = []

    # 2) Resolve each requested filename
    for original_line, fn, key in zip(lines, wanted_filenames, wanted_keys):
        hit = idx.get(key)
        if not hit:
            missing.append(fn)
            results.append({
                "line": original_line,
                "filename": fn,
                "matched": False,
                "reason": "not_found_by_filename",
                "item_ids": [],
            })
            logging.warning("NO MATCH (filename): %s", fn)
            continue

        if len(hit) > 1:
            ambiguous.append(fn)
            results.append({
                "line": original_line,
                "filename": fn,
                "matched": False,
                "reason": "ambiguous_duplicate_filename",
                "item_ids": hit,
            })
            logging.warning("AMBIGUO (filename dup): %s -> %s", fn, ",".join(hit))
            continue

        item_id = hit[0]
        matched_ids.append(item_id)
        results.append({
            "line": original_line,
            "filename": fn,
            "matched": True,
            "reason": "filename_exact_normalized",
            "item_ids": [item_id],
        })
        logging.info("MATCH (filename): %s -> %s", fn, item_id)

    # Dedupe matched ids
    matched_ids = list(dict.fromkeys(matched_ids))

    # 3) Ensure collection exists
    collection_item = jf.find_collection_by_name(collection_name)
    created = False
    collection_id: Optional[str] = None

    if collection_item:
        collection_id = str(collection_item.get("Id") or collection_item.get("id"))
        logging.info("Colección encontrada: %s | id=%s", collection_item.get("Name") or collection_item.get("name"), collection_id)
    else:
        if dry_run:
            logging.info("DRY-RUN: la colección no existe; se CREARÍA: %s", collection_name)
        else:
            collection_id = jf.create_collection(collection_name)
            created = True
            logging.info("Colección creada: %s | id=%s", collection_name, collection_id)

    # 4) Compute what to add (idempotent)
    already_ids: List[str] = []
    to_add: List[str] = matched_ids[:]

    if collection_id:
        already_ids = jf.list_items_in_collection(collection_id)
        already_set = set(already_ids)
        to_add = [iid for iid in matched_ids if iid not in already_set]

    added_count = 0
    if dry_run:
        logging.info("DRY-RUN: matched=%d, missing=%d, ambiguous=%d, already=%d, to_add=%d",
                     len(matched_ids), len(missing), len(ambiguous), len(already_ids), len(to_add))
    else:
        if not collection_id:
            raise RuntimeError("No hay collection_id en modo --yes (no debería pasar).")
        if not to_add:
            logging.info("Nada que añadir: todo ya estaba en la colección.")
        else:
            logging.info("Añadiendo %d items (batches de %d)...", len(to_add), ADD_BATCH_SIZE)
            for batch in chunked(to_add, ADD_BATCH_SIZE):
                jf.add_to_collection(collection_id, batch)
                added_count += len(batch)
                logging.info("Añadidos %d (batch). Total=%d/%d", len(batch), added_count, len(to_add))

    # 5) Write extra outputs
    if missing:
        MISSING_PATH.write_text("\n".join(missing), encoding="utf-8")
        logging.info("Missing list: %s", MISSING_PATH)

    if ambiguous:
        AMBIGUOUS_PATH.write_text("\n".join(ambiguous), encoding="utf-8")
        logging.info("Ambiguous list: %s", AMBIGUOUS_PATH)

    report = {
        "timestamp": NOW_TAG,
        "jellyfin_url": JELLYFIN_URL,
        "list_file": str(list_path),
        "collection_name": collection_name,
        "dry_run": dry_run,
        "collection_id": collection_id,
        "collection_created": created,
        "total_lines": len(lines),
        "matched": len(matched_ids),
        "missing": len(missing),
        "ambiguous": len(ambiguous),
        "already_in_collection": len(already_ids),
        "to_add": len(to_add),
        "added_count": 0 if dry_run else added_count,
        "items": results,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Reporte JSON: %s", REPORT_PATH)

    print("\n================= RESUMEN =================")
    print(f"Colección:  {collection_name}")
    print(f"Dry-run:    {dry_run}")
    print(f"Matched:    {len(matched_ids)}")
    print(f"Missing:    {len(missing)}  -> {MISSING_PATH if missing else '(none)'}")
    print(f"Ambiguous:  {len(ambiguous)} -> {AMBIGUOUS_PATH if ambiguous else '(none)'}")
    if collection_id:
        print(f"Coll ID:    {collection_id}")
        print(f"Ya dentro:  {len(already_ids)}")
        print(f"A añadir:   {len(to_add)}")
    else:
        print("Coll ID:    (no existe en dry-run)")
    print(f"Reporte:    {REPORT_PATH}")
    print(f"Log:        {LOG_PATH}")
    print("===========================================\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())