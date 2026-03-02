#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sync Jellyfin BoxSets (Collections) -> ErsatzTV Manual Collections via SQLite (host-side)

- Reads Jellyfin collections (BoxSet) + playable items.
- Ensures an ErsatzTV manual Collection exists with same name (or prefix/suffix).
- Adds/removes media items so the ETV collection matches Jellyfin exactly.
- DRY-RUN mode prints a full plan without writing.

Works best when:
- ErsatzTV and Jellyfin are linked, and ETV has already synced libraries from Jellyfin.
- If ETV is set to "stream from disk", ETV stores Jellyfin paths and uses "Path Replacements" at playback time.
  The script tries both: original Jellyfin path and mapped path variants.
  (Docs: Jellyfin libraries -> Streaming From Disk / Path Replacements)  :contentReference[oaicite:1]{index=1}
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import requests


# =========================
# Defaults (ajusta si quieres)
# =========================

DEFAULT_JELLYFIN_URL = "http://192.168.1.113:8096"
DEFAULT_JELLYFIN_API_KEY = "23525cdf5afb497a924a801c92deb036"  # dummy que me diste
DEFAULT_ETV_DB = r"E:\Docker_folders\ersatztv\config\ersatztv.sqlite3"

# Reglas de path-map (solo usadas como "fallback" si el path original no se encuentra)
# IMPORTANTE: orden = más específico primero.
DEFAULT_PATH_MAPS = [
    # Sub-mounts específicos que tú has definido
    (r"E:\_Trailers\", "/media_trailers/"),
    (r"E:\Youtube_Podcast\", "/media_podcast1/"),
    (r"F:\Podcasts\", "/media_podcast2/"),

    # Raíces
    (r"E:\", "/media_e/"),
    (r"F:\", "/media_f/"),
]


# =========================
# Logging
# =========================

def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)

def hr() -> None:
    print("-" * 100, flush=True)

def chunked(seq: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# =========================
# Jellyfin
# =========================

@dataclass(frozen=True)
class JfItem:
    jf_id: str
    jf_type: str
    name: str
    path: str

class JellyfinClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({
            "X-Emby-Token": api_key,
            "Accept": "application/json",
        })

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        r = self.s.get(url, params=params or {}, timeout=60)
        r.raise_for_status()
        return r.json()

    def get_users(self) -> List[Dict[str, Any]]:
        return self._get("/Users")

    def pick_user_id(self, forced_user_id: str = "") -> str:
        if forced_user_id.strip():
            return forced_user_id.strip()
        users = self.get_users()
        for u in users:
            try:
                if u.get("Policy", {}).get("IsAdministrator"):
                    return u["Id"]
            except Exception:
                pass
        if not users:
            raise RuntimeError("Jellyfin: /Users devolvió 0 usuarios.")
        return users[0]["Id"]

    def paged_items(self, user_id: str, params: Dict[str, Any], page_size: int = 500) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        start = 0
        total: Optional[int] = None

        while True:
            p = dict(params)
            p["StartIndex"] = start
            p["Limit"] = page_size
            data = self._get(f"/Users/{user_id}/Items", p)
            items = data.get("Items") or []
            out.extend(items)

            if total is None and "TotalRecordCount" in data:
                total = int(data["TotalRecordCount"])

            start += len(items)
            if not items:
                break
            if total is not None and start >= total:
                break

        return out

    def list_boxsets(self, user_id: str) -> List[Dict[str, Any]]:
        return self.paged_items(user_id, {
            "IncludeItemTypes": "BoxSet",
            "Recursive": "true",
            "Fields": "Id,Name",
            "EnableTotalRecordCount": "true",
        })

    def list_boxset_children_recursive(self, user_id: str, boxset_id: str) -> List[Dict[str, Any]]:
        return self.paged_items(user_id, {
            "ParentId": boxset_id,
            "Recursive": "true",
            "Fields": "Id,Name,Type,Path",
            "EnableTotalRecordCount": "true",
        })

    def boxset_playables(self, user_id: str, boxset_id: str) -> List[JfItem]:
        raw = self.list_boxset_children_recursive(user_id, boxset_id)
        playable_types = {"Movie", "Episode", "MusicVideo", "Video", "Audio"}

        out: List[JfItem] = []
        for it in raw:
            t = (it.get("Type") or "").strip()
            if t not in playable_types:
                continue
            jf_id = it.get("Id") or ""
            name = it.get("Name") or ""
            path = it.get("Path") or ""
            if jf_id and path:
                out.append(JfItem(jf_id=jf_id, jf_type=t, name=name, path=path))
        return out


# =========================
# Path mapping (fallback)
# =========================

def normalize_slashes(p: str) -> str:
    return p.replace("\\", "/")

def norm_key(p: str) -> str:
    return normalize_slashes(p).casefold().strip()

def ensure_trailing_sep_for_windows_prefix(prefix: str) -> str:
    # Asegura que un prefijo tipo E:\ sea realmente prefijo de directorio
    if prefix.endswith("\\") or prefix.endswith("/"):
        return prefix
    return prefix + "\\"

def apply_path_maps(original: str, maps: List[Tuple[str, str]]) -> str:
    p = original
    # Ordena por longitud del FROM, para que gane el más específico
    maps_sorted = sorted(maps, key=lambda x: len(x[0]), reverse=True)

    for frm, to in maps_sorted:
        frm2 = ensure_trailing_sep_for_windows_prefix(frm)
        if p.startswith(frm2):
            rest = p[len(frm2):]
            to2 = to if to.endswith("/") else to + "/"
            return to2 + normalize_slashes(rest).lstrip("/")
        # también prueba versión con /
        frm3 = normalize_slashes(frm2)
        if normalize_slashes(p).startswith(frm3):
            rest = normalize_slashes(p)[len(frm3):]
            to2 = to if to.endswith("/") else to + "/"
            return to2 + rest.lstrip("/")
    return p

def candidate_paths(jf_path: str, maps: List[Tuple[str, str]], enable_maps: bool) -> List[str]:
    """
    Genera variantes para casar contra ETV DB:
    - original
    - original con slashes normalizados
    - mapped (si habilitado)
    - mapped normalizado
    """
    cands: List[str] = []
    cands.append(jf_path)
    cands.append(normalize_slashes(jf_path))

    if enable_maps:
        mapped = apply_path_maps(jf_path, maps)
        if mapped != jf_path:
            cands.append(mapped)
            cands.append(normalize_slashes(mapped))

    # quita duplicados preservando orden
    seen = set()
    out: List[str] = []
    for c in cands:
        k = norm_key(c)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


# =========================
# SQLite schema discovery (con cadena Path->...->Media)
# =========================

@dataclass
class Edge:
    from_table: str
    from_col: str
    to_table: str
    to_col: str

@dataclass
class Schema:
    collection_table: str
    collection_id_col: str
    collection_name_col: str

    join_table: str
    join_collection_id_col: str
    join_media_id_col: str
    join_order_col: str  # puede ser ""

    media_table: str
    media_pk_col: str

    path_table: str
    path_col: str
    chain: List[Edge]  # desde path_table hasta media_table (joins)

class ETVDb:
    def __init__(self, db_path: Path, busy_timeout_ms: int = 60000) -> None:
        self.db_path = db_path
        self.busy_timeout_ms = busy_timeout_ms

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=max(5, self.busy_timeout_ms / 1000))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms};")
        return conn

    @staticmethod
    def list_tables(conn: sqlite3.Connection) -> List[str]:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return [r["name"] for r in rows]

    @staticmethod
    def table_info(conn: sqlite3.Connection, table: str) -> List[sqlite3.Row]:
        return conn.execute(f"PRAGMA table_info('{table}')").fetchall()

    @staticmethod
    def fk_info(conn: sqlite3.Connection, table: str) -> List[sqlite3.Row]:
        return conn.execute(f"PRAGMA foreign_key_list('{table}')").fetchall()

    @staticmethod
    def pk_col(conn: sqlite3.Connection, table: str) -> str:
        info = ETVDb.table_info(conn, table)
        pk = [r["name"] for r in info if int(r["pk"] or 0) == 1]
        if pk:
            return pk[0]
        # fallback típico
        for r in info:
            if (r["name"] or "").lower() == "id":
                return r["name"]
        # último fallback
        return info[0]["name"]

    def build_fk_graph(self, conn: sqlite3.Connection) -> Dict[str, List[Edge]]:
        graph: Dict[str, List[Edge]] = {}
        for t in self.list_tables(conn):
            graph.setdefault(t, [])
            for fk in self.fk_info(conn, t):
                graph[t].append(Edge(
                    from_table=t,
                    from_col=fk["from"],
                    to_table=fk["table"],
                    to_col=fk["to"],
                ))
        return graph

    def bfs_chain(self, graph: Dict[str, List[Edge]], start: str, target: str, max_depth: int = 4) -> Optional[List[Edge]]:
        # BFS por edges (directed: start -> ... -> target)
        from collections import deque
        q = deque([(start, [])])
        seen = {start}

        while q:
            node, path = q.popleft()
            if len(path) > max_depth:
                continue
            if node == target:
                return path

            for e in graph.get(node, []):
                nxt = e.to_table
                if nxt in seen:
                    continue
                seen.add(nxt)
                q.append((nxt, path + [e]))

        return None

    def discover_schema(self, conn: sqlite3.Connection, verbose: bool) -> Schema:
        tables = self.list_tables(conn)

        # 1) Collection table (manual) suele llamarse "Collection"
        collection_table = None
        for cand in ["Collection", "Collections", "collection", "collections"]:
            if cand in tables:
                collection_table = cand
                break
        if collection_table is None:
            # fallback por heurística
            for t in tables:
                cols = {c["name"].lower() for c in self.table_info(conn, t)}
                if "name" in cols and "collection" in t.lower():
                    collection_table = t
                    break
        if collection_table is None:
            raise RuntimeError("No se encontró tabla de Collections (esperaba 'Collection').")

        collection_id_col = self.pk_col(conn, collection_table)
        # Name col
        ccols = [r["name"] for r in self.table_info(conn, collection_table)]
        collection_name_col = next((c for c in ccols if c.lower() == "name"), None)
        if not collection_name_col:
            raise RuntimeError(f"Tabla {collection_table} no tiene columna Name.")

        # 2) Join table: FK->Collection y FK->MediaTable
        join_candidates: List[Tuple[int, str, str, str, str]] = []
        for t in tables:
            fks = self.fk_info(conn, t)
            if not fks:
                continue
            refs = [(fk["from"], fk["table"]) for fk in fks]
            coll_from_cols = [from_col for (from_col, ref_table) in refs if ref_table == collection_table]
            if not coll_from_cols:
                continue
            other = [(from_col, ref_table) for (from_col, ref_table) in refs if ref_table != collection_table]
            if not other:
                continue
            score = 0
            tl = t.lower()
            if "collection" in tl:
                score += 3
            if "item" in tl or "media" in tl:
                score += 2
            if len(fks) == 2:
                score += 2
            join_candidates.append((score, t, coll_from_cols[0], other[0][0], other[0][1]))

        if not join_candidates:
            raise RuntimeError("No se encontró tabla join Collection<->Media.")
        join_candidates.sort(reverse=True, key=lambda x: x[0])
        _, join_table, join_collection_id_col, join_media_id_col, media_table = join_candidates[0]

        # order col opcional
        join_cols = {r["name"].lower(): r["name"] for r in self.table_info(conn, join_table)}
        join_order_col = ""
        for cand in ["order", "sortorder", "index", "position"]:
            if cand in join_cols:
                join_order_col = join_cols[cand]
                break

        media_pk_col = self.pk_col(conn, media_table)

        # 3) Path table + chain hacia media_table
        #   Elegimos una tabla con columna Path y cadena corta hasta media_table
        graph = self.build_fk_graph(conn)

        path_tables: List[Tuple[int, str, str]] = []  # score, table, path_col
        for t in tables:
            cols = [r["name"] for r in self.table_info(conn, t)]
            path_col = next((c for c in cols if c.lower() == "path"), None)
            if not path_col:
                continue
            score = 0
            tl = t.lower()
            if "mediafile" in tl:
                score += 10
            if "file" in tl:
                score += 3
            if "media" in tl:
                score += 2
            path_tables.append((score, t, path_col))

        if not path_tables:
            raise RuntimeError("No se encontró ninguna tabla con columna Path (para casar items).")

        best = None  # (chain_len, -score, table, path_col, chain)
        for score, t, path_col in sorted(path_tables, key=lambda x: (-x[0], x[1])):
            chain = self.bfs_chain(graph, t, media_table, max_depth=5)
            if chain is None:
                continue
            chain_len = len(chain)
            cand = (chain_len, -score, t, path_col, chain)
            if best is None or cand < best:
                best = cand

        if best is None:
            raise RuntimeError("Encontré tablas con Path, pero ninguna conecta con la tabla media del join.")

        _, _, path_table, path_col, chain = best

        if verbose:
            hr()
            log("SCHEMA DETECTED")
            log(f"  collection_table = {collection_table} (id={collection_id_col}, name={collection_name_col})")
            log(f"  join_table       = {join_table} (collection_fk={join_collection_id_col}, media_fk={join_media_id_col}, order={join_order_col or 'n/a'})")
            log(f"  media_table      = {media_table} (pk={media_pk_col})")
            log(f"  path_table       = {path_table} (path_col={path_col})")
            log("  path->media chain:")
            for e in chain:
                log(f"    {e.from_table}.{e.from_col} -> {e.to_table}.{e.to_col}")
            hr()

        return Schema(
            collection_table=collection_table,
            collection_id_col=collection_id_col,
            collection_name_col=collection_name_col,
            join_table=join_table,
            join_collection_id_col=join_collection_id_col,
            join_media_id_col=join_media_id_col,
            join_order_col=join_order_col,
            media_table=media_table,
            media_pk_col=media_pk_col,
            path_table=path_table,
            path_col=path_col,
            chain=chain,
        )

    def vacuum_backup(self, conn: sqlite3.Connection, backup_path: Path) -> None:
        # Crea un backup consistente con VACUUM INTO (si falla, lo verás).
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        conn.execute("VACUUM INTO ?", (str(backup_path),))

    def build_path_to_media_query(self, s: Schema, n_paths: int) -> str:
        # SELECT t0.Path as p, tN.media_pk as mid FROM path_table t0 JOIN ... WHERE t0.Path IN (?,?,?)
        aliases: Dict[str, str] = {}
        aliases[s.path_table] = "t0"

        joins: List[str] = []
        current_alias = "t0"
        alias_i = 1

        # Recorremos chain y asignamos alias a cada tabla destino
        for e in s.chain:
            to_alias = aliases.get(e.to_table)
            if not to_alias:
                to_alias = f"t{alias_i}"
                alias_i += 1
                aliases[e.to_table] = to_alias

            joins.append(f"JOIN {e.to_table} {to_alias} ON {current_alias}.{e.from_col} = {to_alias}.{e.to_col}")
            current_alias = to_alias

        # current_alias ahora apunta a media_table alias
        ph = ",".join(["?"] * n_paths)
        q = (
            f"SELECT t0.{s.path_col} AS p, {current_alias}.{s.media_pk_col} AS mid "
            f"FROM {s.path_table} t0 "
            + " ".join(joins)
            + f" WHERE t0.{s.path_col} IN ({ph})"
        )
        return q

    def map_paths_to_media_ids(self, conn: sqlite3.Connection, s: Schema, paths: Sequence[str]) -> Dict[str, int]:
        """
        Devuelve mapping: norm_key(path_en_db) -> media_id
        """
        out: Dict[str, int] = {}
        if not paths:
            return out

        for chunk in chunked(list(paths), 400):
            q = self.build_path_to_media_query(s, len(chunk))
            rows = conn.execute(q, chunk).fetchall()
            for r in rows:
                p = r["p"]
                mid = r["mid"]
                if p is None or mid is None:
                    continue
                out[norm_key(str(p))] = int(mid)
        return out

    def get_collection_id(self, conn: sqlite3.Connection, s: Schema, name: str) -> Optional[int]:
        row = conn.execute(
            f"SELECT {s.collection_id_col} AS id FROM {s.collection_table} WHERE {s.collection_name_col}=?",
            (name,),
        ).fetchone()
        return int(row["id"]) if row else None

    def create_collection(self, conn: sqlite3.Connection, s: Schema, name: str) -> int:
        """
        Inserta una Collection manual (tabla Collection).
        Rellena columnas NOT NULL sin DEFAULT con valores seguros.
        """
        info = self.table_info(conn, s.collection_table)

        pk_lower = s.collection_id_col.lower()
        name_lower = s.collection_name_col.lower()

        cols_to_insert: List[str] = []
        vals: List[Any] = []

        # Intentamos coger una fila existente como "plantilla" para valores requeridos no obvios.
        template = conn.execute(f"SELECT * FROM {s.collection_table} LIMIT 1").fetchone()
        template_dict = dict(template) if template else {}

        def safe_value(col: str, ctype: str) -> Any:
            n = col.lower()
            t = (ctype or "").lower()

            if n == name_lower:
                return name
            if "normalized" in n and "name" in n:
                return name.casefold()
            if "guid" in n or n.endswith("uuid"):
                return str(uuid.uuid4())
            if "created" in n or "updated" in n or "timestamp" in n or "date" in n:
                return datetime.now().isoformat(timespec="seconds")
            if "type" in n or "kind" in n:
                # si es numérico, 0 suele ser "manual"/default; si es texto, deja vacío
                return 0 if ("int" in t) else ""
            if "int" in t:
                return 0
            if "bool" in t:
                return 0
            return ""

        for row in info:
            col = row["name"]
            if col.lower() == pk_lower:
                continue

            notnull = int(row["notnull"] or 0)
            dflt = row["dflt_value"]
            ctype = row["type"] or ""

            if col.lower() == name_lower:
                cols_to_insert.append(col)
                vals.append(name)
                continue

            # si tiene default, dejamos que aplique
            if dflt is not None:
                continue

            # si es nullable, dejamos NULL
            if notnull == 0:
                continue

            # NOT NULL sin default -> ponemos valor
            if template_dict and col in template_dict and template_dict[col] is not None:
                # cuidado con valores probablemente únicos
                if "guid" in col.lower() or col.lower().endswith("uuid"):
                    v = str(uuid.uuid4())
                elif "created" in col.lower() or "updated" in col.lower():
                    v = datetime.now().isoformat(timespec="seconds")
                elif "normalized" in col.lower() and "name" in col.lower():
                    v = name.casefold()
                else:
                    v = template_dict[col]
            else:
                v = safe_value(col, ctype)

            cols_to_insert.append(col)
            vals.append(v)

        if not cols_to_insert:
            cols_to_insert = [s.collection_name_col]
            vals = [name]

        ph = ",".join(["?"] * len(cols_to_insert))
        cols_sql = ",".join(cols_to_insert)

        try:
            conn.execute(f"INSERT INTO {s.collection_table} ({cols_sql}) VALUES ({ph})", vals)
        except sqlite3.IntegrityError:
            # Puede existir por colisión de nombre case-insensitive
            existing = self.get_collection_id(conn, s, name)
            if existing is not None:
                return existing
            raise

        new_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        return int(new_id)

    def get_collection_media_ids(self, conn: sqlite3.Connection, s: Schema, collection_id: int) -> Set[int]:
        rows = conn.execute(
            f"SELECT {s.join_media_id_col} AS mid FROM {s.join_table} WHERE {s.join_collection_id_col}=?",
            (collection_id,),
        ).fetchall()
        return {int(r["mid"]) for r in rows if r["mid"] is not None}

    def apply_membership(
        self,
        conn: sqlite3.Connection,
        s: Schema,
        collection_id: int,
        desired_media_ids: Set[int],
        dry_run: bool,
    ) -> Tuple[int, int]:
        current = self.get_collection_media_ids(conn, s, collection_id)
        to_add = sorted(desired_media_ids - current)
        to_remove = sorted(current - desired_media_ids)

        if dry_run:
            return (len(to_add), len(to_remove))

        # remove
        if to_remove:
            ph = ",".join(["?"] * len(to_remove))
            conn.execute(
                f"DELETE FROM {s.join_table} "
                f"WHERE {s.join_collection_id_col}=? AND {s.join_media_id_col} IN ({ph})",
                [collection_id, *to_remove],
            )

        # add
        if to_add:
            if s.join_order_col:
                row = conn.execute(
                    f"SELECT MAX({s.join_order_col}) AS m FROM {s.join_table} WHERE {s.join_collection_id_col}=?",
                    (collection_id,),
                ).fetchone()
                start = int(row["m"] or 0) + 1
                for i, mid in enumerate(to_add):
                    conn.execute(
                        f"INSERT OR IGNORE INTO {s.join_table} "
                        f"({s.join_collection_id_col},{s.join_media_id_col},{s.join_order_col}) VALUES (?,?,?)",
                        (collection_id, mid, start + i),
                    )
            else:
                for mid in to_add:
                    conn.execute(
                        f"INSERT OR IGNORE INTO {s.join_table} "
                        f"({s.join_collection_id_col},{s.join_media_id_col}) VALUES (?,?)",
                        (collection_id, mid),
                    )

        return (len(to_add), len(to_remove))


# =========================
# CLI / Main
# =========================

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Sync Jellyfin collections (BoxSets) to ErsatzTV manual collections via SQLite",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    ap.add_argument("--jellyfin-url", default=os.getenv("JELLYFIN_URL", DEFAULT_JELLYFIN_URL))
    ap.add_argument("--jellyfin-api-key", default=os.getenv("JELLYFIN_API_KEY", DEFAULT_JELLYFIN_API_KEY))
    ap.add_argument("--jellyfin-user-id", default=os.getenv("JELLYFIN_USER_ID", ""))

    ap.add_argument("--etv-db", default=os.getenv("ETV_DB", DEFAULT_ETV_DB))

    ap.add_argument("--prefix", default=os.getenv("ETV_COLLECTION_PREFIX", ""))
    ap.add_argument("--suffix", default=os.getenv("ETV_COLLECTION_SUFFIX", ""))

    ap.add_argument("--only", default=os.getenv("ONLY_REGEX", ""), help="Solo colecciones Jellyfin que hagan match con regex")
    ap.add_argument("--skip", default=os.getenv("SKIP_REGEX", ""), help="Saltarse colecciones Jellyfin que hagan match con regex")

    ap.add_argument("--sync-empty", action="store_true", help="Si una colección está vacía, vacía también la collection en ETV (por defecto: skip)")
    ap.add_argument("--no-path-maps", action="store_true", help="No intentar path maps (solo path original)")

    ap.add_argument("--path-map", action="append", default=[], help="Añade regla FROM=>TO (ej: E:\\\\=>/media_e/). Se pueden repetir.")
    ap.add_argument("--verbose", action="store_true")

    ap.add_argument("--dry-run", action="store_true", help="Planificar sin escribir")
    ap.add_argument("--apply", action="store_true", help="Aplicar cambios (escritura real)")

    ap.add_argument("--backup-dir", default=os.getenv("ETV_BACKUP_DIR", r"E:\Docker_folders\ersatztv\config\_jf_collection_sync_backups"))

    ap.add_argument("--max-missing-samples", type=int, default=10, help="Cuántos paths missing imprimir por collection")
    return ap

def parse_path_map_arg(x: str) -> Tuple[str, str]:
    if "=>" not in x:
        raise ValueError(f"Formato inválido '{x}'. Usa FROM=>TO")
    a, b = x.split("=>", 1)
    return (a, b)

def want(name: str, only_re: str, skip_re: str) -> bool:
    if only_re and not re.search(only_re, name, flags=re.IGNORECASE):
        return False
    if skip_re and re.search(skip_re, name, flags=re.IGNORECASE):
        return False
    return True

def main() -> int:
    args = build_parser().parse_args()

    dry_run = True
    if args.apply:
        dry_run = False
    elif args.dry_run:
        dry_run = True

    db_path = Path(args.etv_db)
    if not db_path.exists():
        raise FileNotFoundError(f"No existe la DB: {db_path}")

    # path maps: defaults + custom
    maps: List[Tuple[str, str]] = list(DEFAULT_PATH_MAPS)
    for pm in args.path_map:
        maps.append(parse_path_map_arg(pm))

    enable_maps = not args.no_path_maps

    hr()
    log(f"MODE: {'DRY-RUN' if dry_run else 'APPLY'}")
    log(f"Jellyfin URL: {args.jellyfin_url}")
    log(f"ErsatzTV DB:  {db_path}")
    log(f"Path-maps:    {'ENABLED (fallback)' if enable_maps else 'DISABLED'}; rules={len(maps)}")
    if args.verbose and enable_maps:
        for frm, to in sorted(maps, key=lambda x: len(x[0]), reverse=True):
            log(f"  map: {frm}  =>  {to}")
    hr()

    # Jellyfin pull
    jf = JellyfinClient(args.jellyfin_url, args.jellyfin_api_key)
    user_id = jf.pick_user_id(args.jellyfin_user_id)
    log(f"Jellyfin userId: {user_id}")

    boxsets = jf.list_boxsets(user_id)
    log(f"Jellyfin BoxSets found: {len(boxsets)}")
    hr()

    desired: Dict[str, List[JfItem]] = {}
    skipped_empty = 0
    skipped_regex = 0

    for bs in boxsets:
        name = (bs.get("Name") or "").strip()
        bs_id = bs.get("Id") or ""
        if not name or not bs_id:
            continue

        if not want(name, args.only, args.skip):
            skipped_regex += 1
            continue

        items = jf.boxset_playables(user_id, bs_id)
        etv_name = f"{args.prefix}{name}{args.suffix}"

        if not items and not args.sync_empty:
            skipped_empty += 1
            continue

        desired[etv_name] = items

    log(f"Collections to sync: {len(desired)} (skipped_empty={skipped_empty}, skipped_regex={skipped_regex})")
    hr()

    # SQLite sync
    etv = ETVDb(db_path)
    conn = etv.connect()
    try:
        schema = etv.discover_schema(conn, verbose=args.verbose)

        # Backup consistente SOLO en APPLY
        if not dry_run:
            backup_dir = Path(args.backup_dir)
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"ersatztv.sqlite3.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            log(f"Creating SQLite backup via VACUUM INTO: {backup_path}")
            etv.vacuum_backup(conn, backup_path)
            log("Backup OK.")
            hr()

            # Intentamos lock de escritura
            log("Acquiring write lock (BEGIN IMMEDIATE)...")
            conn.execute("BEGIN IMMEDIATE;")
            log("Write lock acquired.")
            hr()

        # Construye el set de paths candidatos a buscar en DB
        # Para cada item -> genera variantes (original + mapped)
        all_candidates: Set[str] = set()
        per_item_candidates: Dict[str, List[str]] = {}  # jf_id -> [cands]
        for items in desired.values():
            for it in items:
                cands = candidate_paths(it.path, maps, enable_maps)
                per_item_candidates[it.jf_id] = cands
                for c in cands:
                    all_candidates.add(c)

        log(f"Total Jellyfin playable items considered: {sum(len(v) for v in desired.values())}")
        log(f"Total candidate paths to lookup in ETV DB: {len(all_candidates)}")

        # Query DB for candidate paths -> media IDs
        path_map_db = etv.map_paths_to_media_ids(conn, schema, sorted(all_candidates))
        log(f"Candidate paths matched in ETV DB: {len(path_map_db)}")
        hr()

        total_created = 0
        total_add = 0
        total_remove = 0
        total_missing_items = 0  # jellyfin items not found in etv db by any candidate path

        for etv_name, items in desired.items():
            cid = etv.get_collection_id(conn, schema, etv_name)
            created = False

            if cid is None:
                if dry_run:
                    log(f"[PLAN] Would CREATE collection: '{etv_name}'")
                    cid = -1
                else:
                    cid = etv.create_collection(conn, schema, etv_name)
                    created = True
                    total_created += 1
                    log(f"[OK] Created collection '{etv_name}' (id={cid})")

            desired_media_ids: Set[int] = set()
            missing_paths: List[str] = []

            for it in items:
                cands = per_item_candidates.get(it.jf_id) or [it.path]
                mid = None
                for c in cands:
                    mid = path_map_db.get(norm_key(c))
                    if mid is not None:
                        break
                if mid is None:
                    missing_paths.append(it.path)
                else:
                    desired_media_ids.add(int(mid))

            if missing_paths:
                total_missing_items += len(missing_paths)

            # Si es dry-run y collection no existe, planificamos como “+todo”
            if dry_run and cid == -1:
                add_n = len(desired_media_ids)
                rem_n = 0
                total_add += add_n
                log(f"[PLAN] '{etv_name}': +{add_n} / -{rem_n}  (desired={len(desired_media_ids)}  missing={len(missing_paths)})")
            else:
                add_n, rem_n = etv.apply_membership(conn, schema, cid, desired_media_ids, dry_run=dry_run)
                total_add += add_n
                total_remove += rem_n
                tag = "[PLAN]" if dry_run else "[OK]"
                log(f"{tag} '{etv_name}': +{add_n} / -{rem_n}  (desired={len(desired_media_ids)}  missing={len(missing_paths)})")

            if args.verbose and missing_paths:
                for p in missing_paths[: args.max_missing_samples]:
                    log(f"      missing: {p}")
                if len(missing_paths) > args.max_missing_samples:
                    log(f"      ... +{len(missing_paths) - args.max_missing_samples} more")

            if created and args.verbose:
                log("      created new collection row in DB")

        if not dry_run:
            conn.commit()
            log("COMMIT OK.")

        hr()
        log("SUMMARY")
        log(f"  mode:            {'DRY-RUN' if dry_run else 'APPLY'}")
        log(f"  collections:      {len(desired)}")
        log(f"  created:          {total_created}")
        log(f"  adds:             {total_add}")
        log(f"  removes:          {total_remove}")
        log(f"  missing jf items: {total_missing_items}  (normal si ETV aún no ha sincronizado esos items)")
        hr()

        return 0

    finally:
        conn.close()

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("Interrupted.")
        raise SystemExit(130)
    except Exception as e:
        log(f"ERROR: {e}")
        raise SystemExit(1)