#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sync Jellyfin BoxSets (Collections) -> ErsatzTV Manual Collections via SQLite (host-side, Windows)

Key properties (kept from your working script):
- Multi-kind resolver for MediaItemId:
  MediaFile.Path -> MediaVersion -> {MovieId/EpisodeId/SongId/...} -> MediaItemId via COALESCE
- Path candidate generation supports optional prefix maps (--path-map)

New in this version (requested):
- If a collection is deleted from Jellyfin, the corresponding MANAGED manual collection is deleted from ErsatzTV.
  This is implemented safely using a small local state file (JSON) so we only delete collections that this script
  has created/adopted previously (avoids touching unrelated manual collections).

Important behavior:
- By default, deletion sync is ENABLED (can be disabled with --no-delete-missing-collections).
- By default, existing matching collections are ADOPTED into management state (can be disabled with --no-adopt-existing).
- Deletion is best-effort:
  - Deletes CollectionItem rows first
  - Then deletes the Collection row
  - If the delete fails due to foreign key references (e.g., a schedule still references the collection),
    the script logs and SKIPS that deletion.

Usage (PowerShell):
  Dry-run:
    python ./sync_jellyfin_collections_to_ersatztv_sqlite.py --dry-run --verbose

  Apply:
    python ./sync_jellyfin_collections_to_ersatztv_sqlite.py --apply --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import requests


# ============================================================
# DEFAULTS (override via CLI or env vars)
# ============================================================

DEFAULT_JELLYFIN_URL = "http://192.168.1.113:8096"
DEFAULT_JELLYFIN_API_KEY = "23525cdf5afb497a924a801c92deb036"
DEFAULT_ETV_DB = r"E:\Docker_folders\ersatztv\config\ersatztv.sqlite3"

# Your existing defaults. You can add more maps via --path-map.
DEFAULT_PATH_MAPS: List[Tuple[str, str]] = [
    ("E:\\_Trailers\\", "/media_trailers/"),
    ("E:\\Youtube_Podcast\\", "/media_podcast1/"),
    ("F:\\Podcasts\\", "/media_podcast2/"),
    ("E:\\", "/media_e/"),
    ("F:\\", "/media_f/"),
]


# ============================================================
# Logging
# ============================================================

def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


def hr() -> None:
    print("-" * 110, flush=True)


def chunked(seq: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# ============================================================
# State file (managed collections)
# ============================================================

def default_state_file() -> Path:
    return Path(__file__).with_name(".etv_jf_collection_sync_state.json")


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "managed": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "managed": {}}
        data.setdefault("version", 1)
        data.setdefault("managed", {})
        if not isinstance(data["managed"], dict):
            data["managed"] = {}
        return data
    except Exception:
        try:
            bak = path.with_suffix(path.suffix + ".corrupt.bak")
            shutil.copy2(path, bak)
        except Exception:
            pass
        return {"version": 1, "managed": {}}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ============================================================
# Jellyfin
# ============================================================

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
            raise RuntimeError("Jellyfin: /Users returned 0 users")
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


# ============================================================
# Path mapping
# ============================================================

def normalize_slashes(p: str) -> str:
    return p.replace("\\", "/")


def norm_key(p: str) -> str:
    return normalize_slashes(p).casefold().strip()


def apply_prefix_maps(original: str, maps: List[Tuple[str, str]]) -> List[str]:
    """Apply prefix replacement rules; treated as simple string prefixes."""
    p_norm = normalize_slashes(original)
    out: List[str] = []
    for frm, to in sorted(maps, key=lambda x: len(x[0]), reverse=True):
        frm_norm = normalize_slashes(frm)
        to_norm = normalize_slashes(to)
        if p_norm.startswith(frm_norm):
            out.append(to_norm + p_norm[len(frm_norm):])
    # de-dup
    seen: Set[str] = set()
    uniq: List[str] = []
    for x in out:
        k = norm_key(x)
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq


def candidate_paths(jf_path: str, maps: List[Tuple[str, str]], enable_maps: bool) -> List[str]:
    cands = [jf_path, normalize_slashes(jf_path)]
    if enable_maps:
        cands.extend(apply_prefix_maps(jf_path, maps))
    # de-dup
    seen: Set[str] = set()
    out: List[str] = []
    for c in cands:
        k = norm_key(c)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


# ============================================================
# SQLite / ErsatzTV
# ============================================================

@dataclass
class Fk:
    from_col: str
    to_table: str
    to_col: str


@dataclass
class ResolverSpec:
    """How to resolve MediaItemId from MediaVersion.<from_col>."""
    from_col: str
    direct: bool
    join_table: str = ""
    join_to_col: str = ""
    media_item_fk_col: str = ""


@dataclass
class Schema:
    collection_table: str
    collection_id_col: str
    collection_name_col: str

    join_table: str
    join_collection_id_col: str
    join_media_id_col: str
    join_order_col: str

    media_table: str
    media_pk_col: str

    mediafile_table: str
    mediafile_path_col: str
    mediafile_mv_fk_col: str

    mediaversion_table: str
    mediaversion_pk_col: str

    resolver_specs: List[ResolverSpec]


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
        for r in info:
            if (r["name"] or "").lower() == "id":
                return r["name"]
        return info[0]["name"]

    def _find_table_with_col(self, conn: sqlite3.Connection, prefer: str, colname: str) -> Tuple[str, str]:
        tables = self.list_tables(conn)
        if prefer in tables:
            cols = [r["name"] for r in self.table_info(conn, prefer)]
            c = next((x for x in cols if x.lower() == colname.lower()), None)
            if c:
                return prefer, c
        for t in tables:
            cols = [r["name"] for r in self.table_info(conn, t)]
            c = next((x for x in cols if x.lower() == colname.lower()), None)
            if c:
                return t, c
        raise RuntimeError(f"Could not find any table with column '{colname}'")

    def discover_schema(self, conn: sqlite3.Connection, verbose: bool) -> Schema:
        tables = self.list_tables(conn)

        # Collection table
        collection_table = "Collection" if "Collection" in tables else None
        if collection_table is None:
            for t in tables:
                cols = {c["name"].lower() for c in self.table_info(conn, t)}
                if "name" in cols and "collection" in t.lower():
                    collection_table = t
                    break
        if not collection_table:
            raise RuntimeError("Could not find Collection table")

        collection_id_col = self.pk_col(conn, collection_table)
        ccols = [r["name"] for r in self.table_info(conn, collection_table)]
        collection_name_col = next((c for c in ccols if c.lower() == "name"), None)
        if not collection_name_col:
            raise RuntimeError("Collection table has no Name column")

        # Join table CollectionItem
        join_table = "CollectionItem" if "CollectionItem" in tables else None
        if join_table is None:
            for t in tables:
                if "collection" in t.lower() and "item" in t.lower():
                    join_table = t
                    break
        if not join_table:
            raise RuntimeError("Could not find CollectionItem join table")

        join_fks = self.fk_info(conn, join_table)
        join_collection_id_col = None
        join_media_id_col = None
        media_table = None
        for fk in join_fks:
            if fk["table"] == collection_table:
                join_collection_id_col = fk["from"]
            else:
                media_table = fk["table"]
                join_media_id_col = fk["from"]
        if not (join_collection_id_col and join_media_id_col and media_table):
            raise RuntimeError("Could not interpret CollectionItem foreign keys")

        media_pk_col = self.pk_col(conn, media_table)

        # optional order col
        join_cols = {r["name"].lower(): r["name"] for r in self.table_info(conn, join_table)}
        join_order_col = ""
        for cand in ("order", "sortorder", "index", "position"):
            if cand in join_cols:
                join_order_col = join_cols[cand]
                break

        # MediaFile table
        mediafile_table = "MediaFile" if "MediaFile" in tables else None
        if not mediafile_table:
            mediafile_table, _ = self._find_table_with_col(conn, prefer="MediaFile", colname="Path")
        mf_cols = [r["name"] for r in self.table_info(conn, mediafile_table)]
        mediafile_path_col = next((c for c in mf_cols if c.lower() == "path"), None)
        if not mediafile_path_col:
            raise RuntimeError("MediaFile table has no Path column")

        # MediaFile -> MediaVersion FK
        mf_fks = self.fk_info(conn, mediafile_table)
        mv_table = None
        mediafile_mv_fk_col = None
        for fk in mf_fks:
            if "version" in fk["table"].lower():
                mv_table = fk["table"]
                mediafile_mv_fk_col = fk["from"]
                break
        if not (mv_table and mediafile_mv_fk_col):
            cand = next((c for c in mf_cols if c.lower() == "mediaversionid"), None)
            if cand:
                for fk in mf_fks:
                    if fk["from"] == cand:
                        mv_table = fk["table"]
                        mediafile_mv_fk_col = cand
                        break
        if not (mv_table and mediafile_mv_fk_col):
            raise RuntimeError("Could not find MediaFile -> MediaVersion foreign key")

        mediaversion_table = mv_table
        mediaversion_pk_col = self.pk_col(conn, mediaversion_table)

        # MediaVersion foreign keys -> build resolvers
        mv_fks = [Fk(from_col=fk["from"], to_table=fk["table"], to_col=fk["to"]) for fk in self.fk_info(conn, mediaversion_table)]
        resolver_specs = self._build_resolvers(conn, mv_fks, media_table, media_pk_col)
        if not resolver_specs:
            raise RuntimeError("Could not build any MediaVersion->MediaItem resolvers")

        if verbose:
            hr()
            log("SCHEMA DETECTED (multi-kind resolver)")
            log(f"  collection_table = {collection_table} (id={collection_id_col}, name={collection_name_col})")
            log(f"  join_table       = {join_table} (collection_fk={join_collection_id_col}, media_fk={join_media_id_col}, order={join_order_col or 'n/a'})")
            log(f"  media_table      = {media_table} (pk={media_pk_col})")
            log(f"  mediafile_table  = {mediafile_table} (path={mediafile_path_col}, mv_fk={mediafile_mv_fk_col})")
            log(f"  mediaversion     = {mediaversion_table} (pk={mediaversion_pk_col})")
            log("  resolvers (coalesce order):")
            for rs in resolver_specs:
                if rs.direct:
                    log(f"    mv.{rs.from_col}  (direct MediaItemId)")
                else:
                    log(f"    mv.{rs.from_col} -> {rs.join_table}.{rs.media_item_fk_col} (via join)")
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
            mediafile_table=mediafile_table,
            mediafile_path_col=mediafile_path_col,
            mediafile_mv_fk_col=mediafile_mv_fk_col,
            mediaversion_table=mediaversion_table,
            mediaversion_pk_col=mediaversion_pk_col,
            resolver_specs=resolver_specs,
        )

    def _build_resolvers(self, conn: sqlite3.Connection, mv_fks: List[Fk], media_table: str, media_pk: str) -> List[ResolverSpec]:
        tables = self.list_tables(conn)
        specs: List[ResolverSpec] = []

        def media_fk_col_in_table(t: str) -> Optional[str]:
            for fk in self.fk_info(conn, t):
                if fk["table"] == media_table:
                    return fk["from"]
            return None

        for fk in mv_fks:
            if fk.to_table not in tables:
                continue
            ref_table = fk.to_table
            ref_pk = self.pk_col(conn, ref_table)

            # Direct if ref_table.pk -> MediaItem.pk
            direct = False
            for rfk in self.fk_info(conn, ref_table):
                if rfk["table"] == media_table and rfk["from"] == ref_pk and rfk["to"] == media_pk:
                    direct = True
                    break
            if direct:
                specs.append(ResolverSpec(from_col=fk.from_col, direct=True))
                continue

            # Join if ref_table has a column referencing MediaItem
            mcol = media_fk_col_in_table(ref_table)
            if mcol:
                specs.append(ResolverSpec(
                    from_col=fk.from_col,
                    direct=False,
                    join_table=ref_table,
                    join_to_col=fk.to_col,
                    media_item_fk_col=mcol,
                ))

        # de-dup preserve order
        seen: Set[Tuple[str, bool, str, str]] = set()
        uniq: List[ResolverSpec] = []
        for s in specs:
            k = (s.from_col, s.direct, s.join_table, s.media_item_fk_col)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(s)

        # prefer direct
        uniq.sort(key=lambda x: (0 if x.direct else 1))
        return uniq

    # Backup helpers
    def backup_vacuum_into(self, conn: sqlite3.Connection, backup_path: Path) -> None:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        conn.execute("VACUUM INTO ?", (str(backup_path),))

    def backup_copy_file(self, backup_path: Path) -> None:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.db_path, backup_path)

    # Path resolution query
    def _build_path_resolve_query(self, s: Schema, n_paths: int) -> str:
        joins: List[str] = []
        join_map: Dict[str, str] = {}
        alias_i = 1

        for rs in s.resolver_specs:
            if rs.direct:
                continue
            if rs.join_table not in join_map:
                alias = f"t{alias_i}"
                alias_i += 1
                join_map[rs.join_table] = alias
                joins.append(f"LEFT JOIN {rs.join_table} {alias} ON mv.{rs.from_col} = {alias}.{rs.join_to_col}")

        coalesce_terms: List[str] = []
        for rs in s.resolver_specs:
            if rs.direct:
                coalesce_terms.append(f"mv.{rs.from_col}")
            else:
                alias = join_map[rs.join_table]
                coalesce_terms.append(f"{alias}.{rs.media_item_fk_col}")

        coalesce_expr = "COALESCE(" + ", ".join(coalesce_terms) + ")"
        placeholders = ",".join(["?"] * n_paths)

        return (
            f"SELECT mf.{s.mediafile_path_col} AS p, {coalesce_expr} AS mid "
            f"FROM {s.mediafile_table} mf "
            f"JOIN {s.mediaversion_table} mv ON mf.{s.mediafile_mv_fk_col} = mv.{s.mediaversion_pk_col} "
            + " ".join(joins)
            + f" WHERE mf.{s.mediafile_path_col} IN ({placeholders})"
        )

    def map_paths_to_media_ids(self, conn: sqlite3.Connection, s: Schema, paths: Sequence[str]) -> Tuple[Dict[str, int], int]:
        out: Dict[str, int] = {}
        rows_found = 0
        if not paths:
            return out, rows_found

        for chunk in chunked(list(paths), 400):
            q = self._build_path_resolve_query(s, len(chunk))
            rows = conn.execute(q, chunk).fetchall()
            rows_found += len(rows)
            for r in rows:
                p = r["p"]
                mid = r["mid"]
                if p is None or mid is None:
                    continue
                out[norm_key(str(p))] = int(mid)

        return out, rows_found

    # Collection ops
    def get_collection_id(self, conn: sqlite3.Connection, s: Schema, name: str) -> Optional[int]:
        row = conn.execute(
            f"SELECT {s.collection_id_col} AS id FROM {s.collection_table} WHERE {s.collection_name_col}=?",
            (name,),
        ).fetchone()
        return int(row["id"]) if row else None

    def create_collection(self, conn: sqlite3.Connection, s: Schema, name: str) -> int:
        info = self.table_info(conn, s.collection_table)
        template = conn.execute(f"SELECT * FROM {s.collection_table} LIMIT 1").fetchone()
        template_dict = dict(template) if template else {}

        pk_lower = s.collection_id_col.lower()
        name_lower = s.collection_name_col.lower()

        cols: List[str] = []
        vals: List[Any] = []

        def safe_value(col: str, ctype: str) -> Any:
            n = col.lower()
            t = (ctype or "").lower()
            if n == name_lower:
                return name
            if "normalized" in n and "name" in n:
                return name.casefold()
            if "guid" in n or n.endswith("uuid"):
                return str(uuid.uuid4())
            if any(x in n for x in ("created", "updated", "timestamp", "date", "time")):
                return datetime.now().isoformat(timespec="seconds")
            if "int" in t or "bool" in t:
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
                cols.append(col)
                vals.append(name)
                continue
            if dflt is not None or notnull == 0:
                continue

            if template_dict and col in template_dict and template_dict[col] is not None:
                if "guid" in col.lower() or col.lower().endswith("uuid"):
                    v = str(uuid.uuid4())
                elif any(x in col.lower() for x in ("created", "updated")):
                    v = datetime.now().isoformat(timespec="seconds")
                elif "normalized" in col.lower() and "name" in col.lower():
                    v = name.casefold()
                else:
                    v = template_dict[col]
            else:
                v = safe_value(col, ctype)

            cols.append(col)
            vals.append(v)

        if not cols:
            cols = [s.collection_name_col]
            vals = [name]

        placeholders = ",".join(["?"] * len(cols))
        conn.execute(f"INSERT INTO {s.collection_table} ({','.join(cols)}) VALUES ({placeholders})", vals)
        new_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        return int(new_id)

    def get_collection_media_ids(self, conn: sqlite3.Connection, s: Schema, collection_id: int) -> Set[int]:
        rows = conn.execute(
            f"SELECT {s.join_media_id_col} AS mid FROM {s.join_table} WHERE {s.join_collection_id_col}=?",
            (collection_id,),
        ).fetchall()
        return {int(r["mid"]) for r in rows if r["mid"] is not None}

    def apply_membership(self, conn: sqlite3.Connection, s: Schema, collection_id: int, desired_media_ids: Set[int], dry_run: bool) -> Tuple[int, int]:
        current = self.get_collection_media_ids(conn, s, collection_id)
        to_add = sorted(desired_media_ids - current)
        to_remove = sorted(current - desired_media_ids)
        if dry_run:
            return (len(to_add), len(to_remove))

        if to_remove:
            ph = ",".join(["?"] * len(to_remove))
            conn.execute(
                f"DELETE FROM {s.join_table} WHERE {s.join_collection_id_col}=? AND {s.join_media_id_col} IN ({ph})",
                [collection_id, *to_remove],
            )

        if to_add:
            if s.join_order_col:
                row = conn.execute(
                    f"SELECT MAX({s.join_order_col}) AS m FROM {s.join_table} WHERE {s.join_collection_id_col}=?",
                    (collection_id,),
                ).fetchone()
                start = int(row["m"] or 0) + 1
                for i, mid in enumerate(to_add):
                    conn.execute(
                        f"INSERT OR IGNORE INTO {s.join_table} ({s.join_collection_id_col},{s.join_media_id_col},{s.join_order_col}) VALUES (?,?,?)",
                        (collection_id, mid, start + i),
                    )
            else:
                for mid in to_add:
                    conn.execute(
                        f"INSERT OR IGNORE INTO {s.join_table} ({s.join_collection_id_col},{s.join_media_id_col}) VALUES (?,?)",
                        (collection_id, mid),
                    )
        return (len(to_add), len(to_remove))

    def delete_collection(self, conn: sqlite3.Connection, s: Schema, collection_id: int) -> bool:
        """Best-effort delete. Returns True if deleted, False if blocked by FK constraints."""
        conn.execute("SAVEPOINT sp_del;")
        try:
            conn.execute(f"DELETE FROM {s.join_table} WHERE {s.join_collection_id_col}=?", (collection_id,))
            conn.execute(f"DELETE FROM {s.collection_table} WHERE {s.collection_id_col}=?", (collection_id,))
            conn.execute("RELEASE sp_del;")
            return True
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK TO sp_del;")
            conn.execute("RELEASE sp_del;")
            return False


# ============================================================
# CLI / Main
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Sync Jellyfin BoxSets -> ErsatzTV manual collections via SQLite (multi-kind resolver)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--jellyfin-url", default=os.getenv("JELLYFIN_URL", DEFAULT_JELLYFIN_URL))
    ap.add_argument("--jellyfin-api-key", default=os.getenv("JELLYFIN_API_KEY", DEFAULT_JELLYFIN_API_KEY))
    ap.add_argument("--jellyfin-user-id", default=os.getenv("JELLYFIN_USER_ID", ""))

    ap.add_argument("--etv-db", default=os.getenv("ETV_DB", DEFAULT_ETV_DB))

    ap.add_argument("--prefix", default=os.getenv("ETV_COLLECTION_PREFIX", ""))
    ap.add_argument("--suffix", default=os.getenv("ETV_COLLECTION_SUFFIX", ""))

    ap.add_argument("--only", default=os.getenv("ONLY_REGEX", ""))
    ap.add_argument("--skip", default=os.getenv("SKIP_REGEX", ""))
    ap.add_argument("--sync-empty", action="store_true")

    ap.add_argument("--no-path-maps", action="store_true")
    ap.add_argument("--path-map", action="append", default=[], help="Add prefix map FROM=>TO (repeatable). Example: /media/=>/media_e/")

    # Deletion sync (requested)
    ap.add_argument("--no-delete-missing-collections", action="store_true", help="Do not delete managed collections missing in Jellyfin")
    ap.add_argument("--no-adopt-existing", action="store_true", help="Do not add pre-existing ETV collections to managed state automatically")

    ap.add_argument("--state-file", default=os.getenv("STATE_FILE", ""), help="Path to state JSON (defaults next to script)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")

    ap.add_argument("--backup-dir", default=os.getenv("ETV_BACKUP_DIR", r"E:\Docker_folders\ersatztv\config\_jf_collection_sync_backups"))
    ap.add_argument("--backup-method", choices=["auto", "vacuum", "copy"], default=os.getenv("ETV_BACKUP_METHOD", "auto"))

    ap.add_argument("--max-missing-samples", type=int, default=10)
    return ap


def parse_path_map_arg(x: str) -> Tuple[str, str]:
    if "=>" not in x:
        raise ValueError(f"Invalid --path-map '{x}'. Expected FROM=>TO")
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

    if not args.jellyfin_api_key:
        raise RuntimeError("Missing Jellyfin API key")

    dry_run = True
    if args.apply:
        dry_run = False
    elif args.dry_run:
        dry_run = True

    delete_missing = not args.no_delete_missing_collections
    adopt_existing = not args.no_adopt_existing

    db_path = Path(args.etv_db)
    if not db_path.exists():
        raise FileNotFoundError(f"ErsatzTV DB not found: {db_path}")

    maps: List[Tuple[str, str]] = list(DEFAULT_PATH_MAPS)
    for pm in args.path_map:
        maps.append(parse_path_map_arg(pm))
    enable_maps = not args.no_path_maps

    state_path = Path(args.state_file) if args.state_file.strip() else default_state_file()
    state = load_state(state_path)

    hr()
    log(f"MODE: {'DRY-RUN' if dry_run else 'APPLY'}")
    log(f"Jellyfin URL: {args.jellyfin_url}")
    log(f"ErsatzTV DB:  {db_path}")
    log(f"Prefix/Suffix: '{args.prefix}' / '{args.suffix}'")
    log(f"Path-maps: {'ENABLED' if enable_maps else 'DISABLED'} (rules={len(maps)})")
    log(f"Delete missing collections: {'ENABLED' if delete_missing else 'DISABLED'} (managed only)")
    log(f"Adopt existing collections: {'ENABLED' if adopt_existing else 'DISABLED'}")
    log(f"State file: {state_path}")
    if args.verbose and enable_maps:
        for frm, to in sorted(maps, key=lambda x: len(x[0]), reverse=True):
            log(f"  map: {frm} => {to}")
    hr()

    jf = JellyfinClient(args.jellyfin_url, args.jellyfin_api_key)
    user_id = jf.pick_user_id(args.jellyfin_user_id)
    log(f"Jellyfin userId: {user_id}")

    boxsets = jf.list_boxsets(user_id)
    log(f"Jellyfin BoxSets found: {len(boxsets)}")
    hr()

    desired: Dict[str, List[JfItem]] = {}
    skipped_empty = 0
    skipped_regex = 0
    total_items = 0

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
        total_items += len(items)

    desired_names = set(desired.keys())

    log(f"Collections to sync: {len(desired)} (skipped_empty={skipped_empty}, skipped_regex={skipped_regex})")
    log(f"Total playable items in scope: {total_items}")
    hr()

    etv = ETVDb(db_path)
    conn = etv.connect()
    try:
        schema = etv.discover_schema(conn, verbose=args.verbose)

        if not dry_run:
            backup_dir = Path(args.backup_dir)
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = backup_dir / f"ersatztv.sqlite3.bak_{stamp}"

            log(f"Backup method: {args.backup_method}")
            if args.backup_method in ("auto", "vacuum"):
                try:
                    log(f"Creating backup via VACUUM INTO: {backup_path}")
                    etv.backup_vacuum_into(conn, backup_path)
                    log("Backup OK (VACUUM INTO).")
                except Exception as e:
                    if args.backup_method == "vacuum":
                        raise RuntimeError(f"VACUUM INTO failed: {e}") from e
                    log(f"VACUUM INTO failed ({e}); falling back to file copy...")
                    etv.backup_copy_file(backup_path)
                    log("Backup OK (file copy).")
            else:
                log(f"Creating backup via file copy: {backup_path}")
                etv.backup_copy_file(backup_path)
                log("Backup OK (file copy).")

            hr()
            log("Acquiring write lock (BEGIN IMMEDIATE)...")
            conn.execute("BEGIN IMMEDIATE;")
            log("Write lock acquired.")
            hr()

        per_item_candidates: Dict[str, List[str]] = {}
        all_candidates: Set[str] = set()

        for items in desired.values():
            for it in items:
                cands = candidate_paths(it.path, maps, enable_maps)
                per_item_candidates[it.jf_id] = cands
                for c in cands:
                    all_candidates.add(c)

        log(f"Candidate paths to lookup in ETV DB: {len(all_candidates)}")
        path_to_mid, rows_found = etv.map_paths_to_media_ids(conn, schema, sorted(all_candidates))
        log(f"Rows found in MediaFile for candidate paths: {rows_found}")
        log(f"Candidate paths resolved to MediaItemId: {len(path_to_mid)}")
        hr()

        total_created = 0
        total_add = 0
        total_remove = 0
        total_missing = 0

        managed_now: Dict[str, int] = {}

        for etv_name, items in desired.items():
            cid = etv.get_collection_id(conn, schema, etv_name)
            created_now = False

            if cid is None:
                if dry_run:
                    log(f"[PLAN] Would CREATE collection: '{etv_name}'")
                    cid = -1
                else:
                    cid = etv.create_collection(conn, schema, etv_name)
                    created_now = True
                    total_created += 1
                    log(f"[OK] Created collection '{etv_name}' (id={cid})")

            desired_media_ids: Set[int] = set()
            missing_paths: List[str] = []

            for it in items:
                cands = per_item_candidates.get(it.jf_id) or [it.path]
                mid: Optional[int] = None
                for c in cands:
                    mid = path_to_mid.get(norm_key(c))
                    if mid is not None:
                        break
                if mid is None:
                    missing_paths.append(it.path)
                else:
                    desired_media_ids.add(int(mid))

            total_missing += len(missing_paths)

            if dry_run and cid == -1:
                add_n = len(desired_media_ids)
                rem_n = 0
                total_add += add_n
                log(f"[PLAN] '{etv_name}': +{add_n} / -{rem_n} (desired={len(desired_media_ids)} missing={len(missing_paths)})")
            else:
                add_n, rem_n = etv.apply_membership(conn, schema, cid, desired_media_ids, dry_run=dry_run)
                total_add += add_n
                total_remove += rem_n
                tag = "[PLAN]" if dry_run else "[OK]"
                log(f"{tag} '{etv_name}': +{add_n} / -{rem_n} (desired={len(desired_media_ids)} missing={len(missing_paths)})")

                if not dry_run and cid is not None and cid >= 0:
                    managed_now[etv_name] = int(cid)

            if args.verbose and missing_paths:
                for p in missing_paths[: args.max_missing_samples]:
                    log(f"      missing: {p}")
                if len(missing_paths) > args.max_missing_samples:
                    log(f"      ... +{len(missing_paths) - args.max_missing_samples} more")

            if created_now and args.verbose and not dry_run:
                log("      created new collection row in DB")

        if not dry_run:
            for name, cid in managed_now.items():
                if adopt_existing or name in state.get("managed", {}) or cid is not None:
                    state["managed"][name] = {
                        "id": cid,
                        "last_seen": datetime.now().isoformat(timespec="seconds"),
                    }

        deleted_count = 0
        blocked_count = 0

        if delete_missing:
            managed = state.get("managed", {})
            missing_names = sorted([n for n in managed.keys() if n not in desired_names])

            if missing_names:
                hr()
                log(f"Managed collections missing in Jellyfin: {len(missing_names)}")
                for name in missing_names:
                    state_id = managed.get(name, {}).get("id")
                    cid = None
                    if isinstance(state_id, int):
                        cid = state_id
                    cid_by_name = etv.get_collection_id(conn, schema, name)
                    if cid_by_name is not None:
                        cid = cid_by_name

                    if cid is None:
                        log(f"[OK] Collection '{name}' already absent in ErsatzTV; removing from state")
                        if not dry_run:
                            state["managed"].pop(name, None)
                        continue

                    if dry_run:
                        log(f"[PLAN] Would DELETE collection '{name}' (id={cid})")
                        continue

                    ok = etv.delete_collection(conn, schema, int(cid))
                    if ok:
                        deleted_count += 1
                        log(f"[OK] Deleted collection '{name}' (id={cid})")
                        state["managed"].pop(name, None)
                    else:
                        blocked_count += 1
                        log(f"[WARN] Could NOT delete '{name}' (id={cid}) due to foreign key references (e.g., schedules). Skipping.")
            else:
                if args.verbose:
                    hr()
                    log("No managed collections are missing in Jellyfin. Nothing to delete.")

        if not dry_run:
            conn.commit()
            log("COMMIT OK.")
            save_state(state_path, state)
            log(f"State saved: {state_path}")

        hr()
        log("SUMMARY")
        log(f"  mode:            {'DRY-RUN' if dry_run else 'APPLY'}")
        log(f"  collections:     {len(desired)}")
        log(f"  created:         {total_created}")
        log(f"  adds:            {total_add}")
        log(f"  removes:         {total_remove}")
        log(f"  missing jf items:{total_missing}")
        if delete_missing:
            log(f"  deleted missing: {deleted_count}")
            log(f"  delete blocked:  {blocked_count}")
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
