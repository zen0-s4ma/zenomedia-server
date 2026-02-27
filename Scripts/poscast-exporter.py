#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Set

import requests

# =========================
# CONFIGURACIÓN (EDITA AQUÍ)
# =========================

# Origen/destino (export audio -> mp3)
SRC_ROOT = Path(r"E:\YouTube\Audio")
DEST_ROOT = Path(r"E:\Youtube_Podcast")

# Ruta adicional (gPodder) — retag + purge/purge-max
RETAG_EXTRA_ROOT = Path(r"F:\Podcasts")

# Paths extra a purgar con OTRO TubeArchivist (TA2)
TA2_PURGE_PATHS: List[Path] = [
    Path(r"E:\YouTube_videos\UC9n5BQmNLHGlbHSZoGXLPdQ"),
    Path(r"E:\YouTube_videos\UCzWr3fmdEWwk6fl5dWJACIQ"),
]

# YouTube Data API v3 (misma key para todo)
YT_API_KEY = "AIzaSyA-9iJS0bDWXpCGPqvM4NzM-9EoBInli4A"

# Comportamiento local
DELETE_VIDEO_AFTER_MP3 = True
OVERWRITE_MP3 = False
OVERWRITE_IMAGES = True

# Orden de preferencia de miniaturas de YouTube
THUMB_KEYS_ORDER = ["maxres", "standard", "high", "medium", "default"]

REQUEST_TIMEOUT = 30

# --- Embebido de metadatos (ID3) para Jellyfin ---
EMBED_ID3_TAGS = True
EMBED_COVER_ART = True
TAG_EXISTING_MP3 = False
ID3V2_VERSION = 3
WRITE_ID3V1 = True
PODCAST_GENRE = "Podcast"

# --- TubeArchivist #1 (TA1) ---
TA_BASE_URL = os.environ.get("TA_BASE_URL", "https://tubeaudio.maripiflix.xyz/").strip()
TA_TOKEN = os.environ.get("TA_TOKEN", "3810bd0c7dca327ee44a169ed0fe5e481ddb90fb").strip()
TA_ACTION = "delete_ignore"  # none|delete|delete_ignore
TA_VERIFY_SSL = True
TA_DRY_RUN = False

# --- TubeArchivist #2 (TA2) ---
TA2_BASE_URL = os.environ.get("TA2_BASE_URL", TA_BASE_URL).strip()
TA2_TOKEN = os.environ.get("TA2_TOKEN", "f61fdf72627042c9bd1cf4105790b366d4f24c5e").strip()
TA2_ACTION = "delete_ignore"
TA2_VERIFY_SSL = True
TA2_DRY_RUN = False

# --- IGNORE (TubeArchivist) ---
# IMPORTANTE:
# - En tu TA, el endpoint /api/download/{id}/ acepta "ignore" (lo vemos en tu log).
# - Pero el endpoint bulk /api/download/ te respondió que "ignore" NO es válido.
# Así que probamos varios valores según endpoint.
TA_IGNORE_STATUSES_ITEM = ["ignore", "ignore-force"]      # para POST /api/download/{id}/
TA_IGNORE_STATUSES_BULK = ["ignore-force", "ignore"]      # para POST /api/download/ (bulk)

TA_VERIFY_IGNORE = True
TA_DEBUG = True

# --- Esperas / polling (TA) ---
TA_POLL_INTERVAL = 2.0
TA_WAIT_DELETE_TIMEOUT = 180.0
TA_WAIT_TASK_TIMEOUT = 900.0
TA_WAIT_DOWNLOAD_APPEAR_TIMEOUT = 180.0

# --- Purga final ---
PURGE_SHORTER_THAN_SECONDS = 5 * 60  # 5 minutos

# --- Purga final por cantidad ---
MAX_FILES_PER_CHANNEL = 15

# --- Retag final ---
RETAG_TITLE_FROM_FILENAME = True

# =========================

YOUTUBE_API_CHANNELS = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_API_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3"}

# Extensiones que se purgan en roots “audio”
PURGE_EXTS_AUDIO: Set[str] = set(AUDIO_EXTS)

# Extensiones que se purgan en TA2 paths (audio + vídeo)
PURGE_EXTS_TA2: Set[str] = set(AUDIO_EXTS) | set(VIDEO_EXTS)


# =========================
# Helpers básicos
# =========================

def chunked(items: List[str], n: int) -> List[List[str]]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def sanitize_windows(name: str, max_len: int = 140) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name)
    name = name.replace("\u200b", "").strip()
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(" .")

    if not name:
        name = "untitled"

    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    if name.upper() in reserved:
        name = "_" + name

    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")

    return name


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 1000):
        cand = path.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
    raise RuntimeError(f"Demasiados duplicados para: {path.name}")


def published_prefix(published_at: Optional[str], fallback_file: Path) -> str:
    if published_at:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    else:
        dt = datetime.fromtimestamp(fallback_file.stat().st_mtime, tz=timezone.utc)
    return dt.strftime("%Y%m%d-%H%M%S")


def ensure_ffmpeg() -> None:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as e:
        raise RuntimeError(f"No encuentro ffmpeg en PATH. Detalle: {e}")


def ensure_ffprobe() -> None:
    try:
        subprocess.run(
            ["ffprobe", "-version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as e:
        raise RuntimeError(f"No encuentro ffprobe en PATH (viene con ffmpeg). Detalle: {e}")


# =========================
# MP3 conversion + tagging
# =========================

def ffmpeg_to_mp3(src_video: Path, dst_mp3: Path, overwrite: bool = False) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    cmd += ["-y" if overwrite else "-n"]
    cmd += [
        "-i", str(src_video),
        "-vn", "-sn", "-dn",
        "-codec:a", "libmp3lame",
        "-qscale:a", "4",
        str(dst_mp3),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg falló con {src_video.name}:\n{(p.stderr or p.stdout).strip()}")


def sanitize_tag_value(s: str, max_len: int = 500) -> str:
    s = (s or "").replace("\x00", "").replace("\r", " ").replace("\n", " ").strip()
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len].strip()
    return s


def iso_date_for_tag(published_at: Optional[object], fallback_file: Path) -> str:
    try:
        if isinstance(published_at, str) and published_at:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        else:
            dt = datetime.fromtimestamp(fallback_file.stat().st_mtime, tz=timezone.utc)
        return dt.date().isoformat()
    except Exception:
        return ""


def ffmpeg_tag_mp3_inplace(
    mp3_path: Path,
    title: str,
    album: str,
    artist: str,
    album_artist: str,
    date_str: str,
    genre: str,
    comment: str,
    cover_jpg: Optional[Path],
    id3v2_version: int = 3,
    write_id3v1: bool = True,
) -> None:
    tmp = mp3_path.with_suffix(".tagtmp.mp3")
    if tmp.exists():
        tmp.unlink(missing_ok=True)

    base_cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]

    meta_args: List[str] = []
    title = sanitize_tag_value(title)
    album = sanitize_tag_value(album)
    artist = sanitize_tag_value(artist)
    album_artist = sanitize_tag_value(album_artist)
    genre = sanitize_tag_value(genre)
    comment = sanitize_tag_value(comment)
    date_str = sanitize_tag_value(date_str)

    meta_args += ["-metadata", f"title={title}"]
    meta_args += ["-metadata", f"artist={artist}"]
    meta_args += ["-metadata", f"album={album}"]
    meta_args += ["-metadata", f"album_artist={album_artist}"]
    if date_str:
        meta_args += ["-metadata", f"date={date_str}"]
    if genre:
        meta_args += ["-metadata", f"genre={genre}"]
    if comment:
        meta_args += ["-metadata", f"comment={comment}"]

    id3_args = ["-id3v2_version", str(id3v2_version)]
    if write_id3v1:
        id3_args += ["-write_id3v1", "1"]

    def run(cmd: List[str]) -> None:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            raise RuntimeError((p.stderr or p.stdout or "").strip())

    if cover_jpg and cover_jpg.exists():
        cmd = (
            base_cmd
            + ["-i", str(mp3_path), "-i", str(cover_jpg)]
            + ["-c", "copy"]
            + ["-map", "0", "-map", "1"]
            + id3_args
            + meta_args
            + ["-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (Front)"]
            + [str(tmp)]
        )
        run(cmd)
    else:
        cmd = (
            base_cmd
            + ["-i", str(mp3_path)]
            + ["-c", "copy"]
            + id3_args
            + meta_args
            + [str(tmp)]
        )
        run(cmd)

    os.replace(tmp, mp3_path)


def ffmpeg_overwrite_title_tag_inplace(
    mp3_path: Path,
    new_title: str,
    id3v2_version: int = 3,
    write_id3v1: bool = True,
) -> None:
    tmp = mp3_path.with_suffix(".retagtitle.tmp.mp3")
    if tmp.exists():
        tmp.unlink(missing_ok=True)

    title = sanitize_tag_value(new_title)

    cmd: List[str] = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(mp3_path),
        "-map", "0",
        "-c", "copy",
        "-map_metadata", "0",
        "-id3v2_version", str(id3v2_version),
        "-metadata", f"title={title}",
    ]
    if write_id3v1:
        cmd += ["-write_id3v1", "1"]

    cmd += [str(tmp)]

    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "").strip())

    os.replace(tmp, mp3_path)


def ffprobe_title_tag(mp3_path: Path) -> Optional[str]:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format_tags=title",
        "-of", "json",
        str(mp3_path),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        return None
    try:
        data = json.loads(p.stdout)
        tags = (data.get("format", {}) or {}).get("tags", {}) or {}
        t = tags.get("title")
        if t is None:
            t = tags.get("TITLE")
        if t is None:
            return None
        return str(t)
    except Exception:
        return None


def _root_looks_like_channel_dir(root: Path) -> bool:
    try:
        if not root.exists() or not root.is_dir():
            return False
        for p in root.iterdir():
            if p.is_file() and p.suffix.lower() in (AUDIO_EXTS | VIDEO_EXTS):
                return True
        return False
    except Exception:
        return False


def iter_channel_dirs_for_root(root: Path) -> List[Path]:
    """
    Admite dos formas:
      A) root tiene subcarpetas (cada una “canal”) -> devuelve subcarpetas
      B) root ES la carpeta del canal (contiene mp3/mp4 directamente) -> devuelve [root]
    """
    if not root.exists():
        return []
    if _root_looks_like_channel_dir(root):
        return [root]
    try:
        return sorted([d for d in root.iterdir() if d.is_dir()])
    except Exception:
        return []


def retag_title_from_filename(dest_root: Path) -> Tuple[int, int]:
    ensure_ffmpeg()
    ensure_ffprobe()

    retag_ok = 0
    retag_err = 0

    if not dest_root.exists():
        return (0, 0)

    channel_dirs = iter_channel_dirs_for_root(dest_root)
    for ch_dir in channel_dirs:
        for mp3 in ch_dir.glob("*.mp3"):
            desired = sanitize_tag_value(mp3.stem)
            current_raw = ffprobe_title_tag(mp3)
            current = sanitize_tag_value(current_raw or "")

            if current == desired:
                continue

            try:
                ffmpeg_overwrite_title_tag_inplace(
                    mp3_path=mp3,
                    new_title=mp3.stem,
                    id3v2_version=ID3V2_VERSION,
                    write_id3v1=WRITE_ID3V1,
                )
                retag_ok += 1
                print(f"[RETAG] TITLE <- {mp3.stem}")
            except Exception as e:
                retag_err += 1
                print(f"[RETAG] (Aviso) No pude retaguear TITLE en {mp3.name}: {e}")

    return (retag_ok, retag_err)


# =========================
# YouTube API helpers
# =========================

def list_channel_dirs(src_root: Path) -> List[Path]:
    return sorted([d for d in src_root.iterdir() if d.is_dir()])


def list_videos_in_channel(channel_dir: Path) -> List[Path]:
    return sorted([p for p in channel_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS])


def iter_thumb_urls(thumbnails: Dict) -> List[str]:
    urls: List[str] = []
    for k in THUMB_KEYS_ORDER:
        u = (thumbnails.get(k) or {}).get("url")
        if u:
            urls.append(u)
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def download_image_as_jpg(urls: List[str], dst_jpg: Path, overwrite: bool) -> bool:
    if dst_jpg.exists() and not overwrite:
        return True

    for url in urls:
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                continue

            content_type = (r.headers.get("Content-Type") or "").lower()
            data = r.content

            if "jpeg" in content_type or "jpg" in content_type:
                dst_jpg.parent.mkdir(parents=True, exist_ok=True)
                dst_jpg.write_bytes(data)
                return True

            try:
                from PIL import Image  # type: ignore
                img = Image.open(BytesIO(data)).convert("RGB")
                dst_jpg.parent.mkdir(parents=True, exist_ok=True)
                img.save(dst_jpg, format="JPEG", quality=92)
                return True
            except ImportError:
                dst_jpg.parent.mkdir(parents=True, exist_ok=True)
                dst_jpg.write_bytes(data)
                return True
            except Exception:
                continue

        except Exception:
            continue

    return False


def yt_channel_meta(api_key: str, channel_ids: List[str]) -> Dict[str, Dict[str, Optional[Dict]]]:
    out: Dict[str, Dict[str, Optional[Dict]]] = {}
    for batch in chunked(channel_ids, 50):
        params = {
            "part": "snippet",
            "id": ",".join(batch),
            "key": api_key,
            "fields": "items(id,snippet(title,thumbnails))",
            "maxResults": 50,
        }
        r = requests.get(YOUTUBE_API_CHANNELS, params=params, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            raise RuntimeError(f"YouTube API error {r.status_code}: {r.text[:500]}")
        data = r.json()
        for it in data.get("items", []):
            sn = it.get("snippet", {}) or {}
            out[it["id"]] = {
                "title": sn.get("title"),
                "thumbnails": sn.get("thumbnails"),
            }
    return out


def yt_video_meta(api_key: str, video_ids: List[str]) -> Dict[str, Dict[str, Optional[object]]]:
    out: Dict[str, Dict[str, Optional[object]]] = {}
    for batch in chunked(video_ids, 50):
        params = {
            "part": "snippet",
            "id": ",".join(batch),
            "key": api_key,
            "fields": "items(id,snippet(title,publishedAt,thumbnails))",
            "maxResults": 50,
        }
        r = requests.get(YOUTUBE_API_VIDEOS, params=params, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            raise RuntimeError(f"YouTube API error {r.status_code}: {r.text[:500]}")
        data = r.json()
        for it in data.get("items", []):
            sn = it.get("snippet", {}) or {}
            out[it["id"]] = {
                "title": sn.get("title"),
                "publishedAt": sn.get("publishedAt"),
                "thumbnails": sn.get("thumbnails"),
            }
    return out


# =========================
# TubeArchivist API helpers (robustos)
# =========================

def ta_enabled(action: str, base_url: str, token: str) -> bool:
    return action in {"delete", "delete_ignore"} and bool(base_url) and bool(token) and token != "PON_AQUI_TU_TA_TOKEN"


def ta_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _ta_auth_variants(token: str) -> List[str]:
    t = (token or "").strip()
    if not t:
        return []
    if t.lower().startswith(("token ", "bearer ")):
        return [t]
    return [f"Token {t}", t]


def ta_request(
    session: requests.Session,
    method: str,
    base_url: str,
    token: str,
    path: str,
    verify_ssl: bool,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
) -> requests.Response:
    url = ta_url(base_url, path)
    headers_base = {"Accept": "application/json", "Content-Type": "application/json"}

    last_resp: Optional[requests.Response] = None
    for auth in _ta_auth_variants(token) or [""]:
        headers = dict(headers_base)
        if auth:
            headers["Authorization"] = auth

        resp = session.request(
            method=method.upper(),
            url=url,
            headers=headers,
            json=json_body,
            params=params,
            timeout=REQUEST_TIMEOUT,
            verify=verify_ssl,
        )
        last_resp = resp

        if resp.status_code in (401, 403):
            continue
        return resp

    assert last_resp is not None
    return last_resp


def _ta_json(resp: requests.Response) -> Optional[Any]:
    try:
        if not resp.text:
            return None
        return resp.json()
    except Exception:
        return None


def ta_video_delete(
    session: requests.Session,
    video_id: str,
    base_url: str,
    token: str,
    verify_ssl: bool,
    dry_run: bool,
    log_prefix: str = "TA",
) -> bool:
    if dry_run:
        print(f"    [{log_prefix}] DRY-RUN: DELETE /api/video/{video_id}/")
        return True

    r = ta_request(session, "DELETE", base_url, token, f"/api/video/{video_id}/", verify_ssl)
    if r.status_code in (200, 202, 204):
        if TA_DEBUG:
            print(f"    [{log_prefix}] OK DELETE /api/video/{video_id}/ -> {r.status_code}")
        return True
    if r.status_code == 404:
        if TA_DEBUG:
            print(f"    [{log_prefix}] DELETE /api/video/{video_id}/ -> 404 (ya no existía)")
        return True

    if TA_DEBUG:
        print(f"    [{log_prefix}] FAIL DELETE /api/video/{video_id}/ -> {r.status_code}: {r.text[:200]}")
    return False


def ta_wait_video_gone(
    session: requests.Session,
    video_id: str,
    base_url: str,
    token: str,
    verify_ssl: bool,
    timeout_s: float,
    log_prefix: str = "TA",
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = ta_request(session, "GET", base_url, token, f"/api/video/{video_id}/", verify_ssl)
        if r.status_code == 404:
            return True
        time.sleep(TA_POLL_INTERVAL)
    if TA_DEBUG:
        print(f"    [{log_prefix}] AVISO: timeout esperando /api/video/{video_id}/ -> 404")
    return False


def ta_task_start_by_name(
    session: requests.Session,
    task_name: str,
    base_url: str,
    token: str,
    verify_ssl: bool,
    dry_run: bool,
    log_prefix: str = "TA",
) -> Optional[str]:
    if dry_run:
        print(f"    [{log_prefix}] DRY-RUN: POST /api/task/by-name/{task_name}/")
        return "dryrun-task-id"

    r = ta_request(session, "POST", base_url, token, f"/api/task/by-name/{task_name}/", verify_ssl)
    if r.status_code not in (200, 201, 202):
        if TA_DEBUG:
            print(f"    [{log_prefix}] FAIL POST /api/task/by-name/{task_name}/ -> {r.status_code}: {r.text[:200]}")
        return None

    data = _ta_json(r)
    if isinstance(data, dict):
        tid = data.get("task_id") or data.get("taskId") or data.get("id")
        if tid:
            if TA_DEBUG:
                print(f"    [{log_prefix}] OK start task '{task_name}' -> task_id={tid}")
            return str(tid)

    if TA_DEBUG:
        print(f"    [{log_prefix}] AVISO: task '{task_name}' started pero sin task_id en respuesta.")
    return None


def _task_status_str(task_json: dict) -> str:
    for k in ("status", "state", "result", "task_status"):
        v = task_json.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def ta_task_wait(
    session: requests.Session,
    task_id: str,
    base_url: str,
    token: str,
    verify_ssl: bool,
    timeout_s: float,
    log_prefix: str = "TA",
) -> Tuple[bool, Optional[dict]]:
    terminal_ok = {"success", "succeeded", "done", "finished", "completed", "complete", "ok"}
    terminal_fail = {"failed", "failure", "error", "revoked", "canceled", "cancelled"}

    deadline = time.time() + timeout_s
    last_json: Optional[dict] = None

    while time.time() < deadline:
        r = ta_request(session, "GET", base_url, token, f"/api/task/by-id/{task_id}/", verify_ssl)
        if r.status_code == 404:
            time.sleep(TA_POLL_INTERVAL)
            continue
        if r.status_code != 200:
            if TA_DEBUG:
                print(f"    [{log_prefix}] FAIL GET /api/task/by-id/{task_id}/ -> {r.status_code}: {r.text[:200]}")
            time.sleep(TA_POLL_INTERVAL)
            continue

        data = _ta_json(r)
        if isinstance(data, dict):
            last_json = data
            st = _task_status_str(data).lower()
            if st in terminal_ok:
                return (True, last_json)
            if st in terminal_fail:
                return (False, last_json)

        time.sleep(TA_POLL_INTERVAL)

    if TA_DEBUG:
        st = _task_status_str(last_json or {})
        print(f"    [{log_prefix}] AVISO: timeout esperando task {task_id}. last_status='{st}'")
    return (False, last_json)


def ta_update_subscribed_and_wait(
    session: requests.Session,
    base_url: str,
    token: str,
    verify_ssl: bool,
    dry_run: bool,
    log_prefix: str = "TA",
) -> bool:
    task_id = ta_task_start_by_name(session, "update_subscribed", base_url, token, verify_ssl, dry_run, log_prefix)
    if not task_id:
        if TA_DEBUG:
            print(f"    [{log_prefix}] Fallback: esperando 30s sin task_id...")
        time.sleep(30)
        return True

    ok, info = ta_task_wait(session, task_id, base_url, token, verify_ssl, TA_WAIT_TASK_TIMEOUT, log_prefix)
    if not ok and TA_DEBUG:
        st = _task_status_str(info or {})
        print(f"    [{log_prefix}] update_subscribed terminó MAL o timeout. status='{st}'")
    return ok


def ta_download_get(
    session: requests.Session,
    video_id: str,
    base_url: str,
    token: str,
    verify_ssl: bool,
) -> Optional[dict]:
    r = ta_request(session, "GET", base_url, token, f"/api/download/{video_id}/", verify_ssl)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        return None
    data = _ta_json(r)
    return data if isinstance(data, dict) else None


def ta_wait_download_appears(
    session: requests.Session,
    video_id: str,
    base_url: str,
    token: str,
    verify_ssl: bool,
    timeout_s: float,
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if ta_download_get(session, video_id, base_url, token, verify_ssl) is not None:
            return True
        time.sleep(TA_POLL_INTERVAL)
    return False


def _download_status_str(download_json: dict) -> str:
    for k in ("status", "state", "download_status"):
        v = download_json.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def ta_download_set_status_once(
    session: requests.Session,
    video_id: str,
    status: str,
    base_url: str,
    token: str,
    verify_ssl: bool,
    dry_run: bool,
    log_prefix: str = "TA",
) -> bool:
    """
    POST /api/download/{id}/ con un status concreto.
    """
    if dry_run:
        print(f"    [{log_prefix}] DRY-RUN: POST /api/download/{video_id}/ {{status:{status}}}")
        return True

    r = ta_request(
        session,
        "POST",
        base_url,
        token,
        f"/api/download/{video_id}/",
        verify_ssl,
        json_body={"status": status},
    )
    if r.status_code in (200, 202, 204):
        if TA_DEBUG:
            print(f"    [{log_prefix}] OK POST /api/download/{video_id}/ status={status} -> {r.status_code}")
        return True

    if TA_DEBUG:
        # OJO: algunos errores vienen en HTML (500)
        txt = (r.text or "").strip()
        print(f"    [{log_prefix}] FAIL POST /api/download/{video_id}/ -> {r.status_code}: {txt[:200]}")
    return False


def ta_download_bulk_add_ignore_once(
    session: requests.Session,
    video_id: str,
    status: str,
    base_url: str,
    token: str,
    verify_ssl: bool,
    dry_run: bool,
    log_prefix: str = "TA",
) -> bool:
    """
    POST /api/download/ (bulk) con AddDownloadItem {youtube_id,status}
    """
    if dry_run:
        print(f"    [{log_prefix}] DRY-RUN: POST /api/download/ data=[{{youtube_id:{video_id}, status:{status}}}]")
        return True

    body = {"data": [{"youtube_id": video_id, "status": status}]}
    r = ta_request(session, "POST", base_url, token, "/api/download/", verify_ssl, json_body=body)
    if r.status_code in (200, 201, 202, 204):
        if TA_DEBUG:
            print(f"    [{log_prefix}] OK POST /api/download/ (bulk add) status={status} -> {r.status_code}")
        return True

    if TA_DEBUG:
        txt = (r.text or "").strip()
        print(f"    [{log_prefix}] FAIL POST /api/download/ -> {r.status_code}: {txt[:200]}")
    return False


def ta_apply_ignore_only(
    session: requests.Session,
    video_id: str,
    base_url: str,
    token: str,
    verify_ssl: bool,
    dry_run: bool,
    log_prefix: str = "TA",
) -> bool:
    """
    APLICAR IGNORE con comportamiento robusto:
      1) Espera a que exista /api/download/{id}/
      2) Si existe -> intenta POST /api/download/{id}/ con statuses item (ignore, ignore-force)
      3) Si NO existe -> NO llama al endpoint /api/download/{id}/ (evita 500) y usa bulk-add con statuses bulk
      4) Verifica con GET /api/download/{id}/ si procede
    """
    if not dry_run:
        appeared = ta_wait_download_appears(session, video_id, base_url, token, verify_ssl, TA_WAIT_DOWNLOAD_APPEAR_TIMEOUT)
    else:
        appeared = True

    ok_set = False

    if appeared:
        # Endpoint por-id (cuando existe): prueba "ignore" primero (lo que te funcionaba) y luego "ignore-force"
        for st in TA_IGNORE_STATUSES_ITEM:
            if ta_download_set_status_once(session, video_id, st, base_url, token, verify_ssl, dry_run, log_prefix):
                ok_set = True
                break
    else:
        if TA_DEBUG:
            print(f"    [{log_prefix}] AVISO: /api/download/{video_id}/ no apareció a tiempo; NO usaré POST /api/download/{{id}} para evitar 500. Iré a bulk-add.")

    if not ok_set:
        # Fallback bulk-add: prueba ignore-force primero (tu server dijo que "ignore" no era válido aquí)
        for st in TA_IGNORE_STATUSES_BULK:
            if ta_download_bulk_add_ignore_once(session, video_id, st, base_url, token, verify_ssl, dry_run, log_prefix):
                ok_set = True
                break

    if not ok_set:
        return False

    if TA_VERIFY_IGNORE and not dry_run:
        d = ta_download_get(session, video_id, base_url, token, verify_ssl)
        if d is None:
            time.sleep(3)
            d = ta_download_get(session, video_id, base_url, token, verify_ssl)

        if isinstance(d, dict):
            st = _download_status_str(d).lower()
            if st in {"ignore", "ignore-force", "ignored"}:
                return True
            print(f"    [{log_prefix}] AVISO: verificación ignore: status='{st}' (esperaba ignore/ignore-force).")
            return False

        print(f"    [{log_prefix}] AVISO: no pude verificar el ignore por GET /api/download/{{id}}/.")
        return False

    return True


# =========================
# Export Channel (audio -> mp3) + TA1 delete/ignore
# =========================

def export_channel(
    channel_id: str,
    channel_name: str,
    channel_thumbnails: Optional[Dict],
    channel_dir: Path,
    dest_root: Path,
    api_key: str,
    delete_video_after_mp3: bool,
    overwrite_mp3: bool,
    overwrite_images: bool,
    ta_action: str,
    ta_base_url: str,
    ta_token: str,
    ta_verify_ssl: bool,
    ta_dry_run: bool,
    embed_id3_tags: bool,
    embed_cover_art: bool,
    tag_existing_mp3: bool,
) -> None:
    videos = list_videos_in_channel(channel_dir)
    if not videos:
        print(f"[{channel_id}] No hay vídeos en esa carpeta.")
        return

    safe_channel = sanitize_windows(channel_name)
    dest_channel_dir = dest_root / safe_channel
    dest_channel_dir.mkdir(parents=True, exist_ok=True)

    if channel_thumbnails:
        poster_path = dest_channel_dir / "poster.jpg"
        poster_urls = iter_thumb_urls(channel_thumbnails)
        if poster_urls:
            ok = download_image_as_jpg(poster_urls, poster_path, overwrite=overwrite_images)
            if ok:
                print(f"[{channel_id}] Poster canal: {poster_path.name}")
            else:
                print(f"[{channel_id}] (No pude descargar poster del canal)")

    video_ids = [v.stem for v in videos]
    meta_map = yt_video_meta(api_key, video_ids)

    print(f"\n[{channel_id}] Copiando, renombrando, convirtiendo y descargando miniaturas en: {dest_channel_dir}\n")

    work_items: List[Tuple[str, Path, Path, Path, List[str]]] = []
    for v in videos:
        vid = v.stem
        meta = meta_map.get(vid, {})
        title = (meta.get("title") or vid)

        prefix = published_prefix(meta.get("publishedAt"), v)
        safe_title = sanitize_windows(str(title), max_len=140 - len(prefix) - 1)

        dst_video = unique_path(dest_channel_dir / f"{prefix}-{safe_title}{v.suffix.lower()}")
        shutil.copy2(v, dst_video)

        dst_mp3 = dst_video.with_suffix(".mp3")
        dst_jpg = dst_mp3.with_suffix(".jpg")

        thumbs = meta.get("thumbnails") or {}
        thumb_urls = iter_thumb_urls(thumbs if isinstance(thumbs, dict) else {})

        work_items.append((vid, dst_video, dst_mp3, dst_jpg, thumb_urls))
        print(f"  OK: {v.name}  ->  {dst_video.name}")

    print("\nConvirtiendo a MP3...\n")
    ensure_ffmpeg()

    session = requests.Session()
    ta_ok = ta_enabled(ta_action, ta_base_url, ta_token)

    ta_deleted_ids_for_ignore: List[str] = []

    for vid, dst_video, dst_mp3, dst_jpg, thumb_urls in work_items:
        mp3_created_now = False

        if dst_mp3.exists() and not overwrite_mp3:
            print(f"  SKIP MP3 (ya existe): {dst_mp3.name}")
        else:
            ffmpeg_to_mp3(dst_video, dst_mp3, overwrite=overwrite_mp3)
            mp3_created_now = True
            print(f"  MP3: {dst_mp3.name}")

        if thumb_urls:
            ok = download_image_as_jpg(thumb_urls, dst_jpg, overwrite=overwrite_images)
            if ok:
                print(f"    THUMB: {dst_jpg.name}")
            else:
                print(f"    (No pude descargar miniatura): {dst_jpg.name}")
        else:
            print(f"    (Sin URLs de miniatura en API): {dst_jpg.name}")

        if embed_id3_tags and dst_mp3.exists():
            do_tag = mp3_created_now or tag_existing_mp3
            if do_tag:
                meta = meta_map.get(vid, {}) or {}
                vtitle = str(meta.get("title") or dst_mp3.stem)
                vpub = meta.get("publishedAt")
                date_str = iso_date_for_tag(vpub, dst_mp3)

                album = channel_name
                artist = channel_name
                album_artist = channel_name
                genre = PODCAST_GENRE
                comment = f"https://youtu.be/{vid}"

                cover = dst_jpg if (embed_cover_art and dst_jpg.exists()) else None

                try:
                    ffmpeg_tag_mp3_inplace(
                        mp3_path=dst_mp3,
                        title=vtitle,
                        album=album,
                        artist=artist,
                        album_artist=album_artist,
                        date_str=date_str,
                        genre=genre,
                        comment=comment,
                        cover_jpg=cover,
                        id3v2_version=ID3V2_VERSION,
                        write_id3v1=WRITE_ID3V1,
                    )
                    print(f"    TAGS: embebidos en {dst_mp3.name}")
                    if cover:
                        print(f"    COVER: embebida desde {cover.name}")
                except Exception as e:
                    print(f"    (Aviso) No pude embebir tags/caratula en {dst_mp3.name}: {e}")

        if delete_video_after_mp3 and dst_mp3.exists():
            dst_video.unlink(missing_ok=True)
            print(f"    Borrado vídeo (DEST): {dst_video.name}")

        if ta_ok and dst_mp3.exists():
            if ta_action == "delete_ignore":
                deleted_ok = ta_video_delete(session, vid, ta_base_url, ta_token, ta_verify_ssl, ta_dry_run, log_prefix="TA1")
                if not deleted_ok:
                    print(f"    [TA1] FAIL: no se pudo borrar {vid}")
                else:
                    if not ta_dry_run:
                        ta_wait_video_gone(session, vid, ta_base_url, ta_token, ta_verify_ssl, TA_WAIT_DELETE_TIMEOUT, log_prefix="TA1")
                    ta_deleted_ids_for_ignore.append(vid)

            elif ta_action == "delete":
                ok_del = ta_video_delete(session, vid, ta_base_url, ta_token, ta_verify_ssl, ta_dry_run, log_prefix="TA1")
                print(f"    [TA1] {'OK' if ok_del else 'FAIL'}: borrado aplicado a {vid}")

    if ta_ok and ta_action == "delete_ignore" and ta_deleted_ids_for_ignore:
        upd_ok = ta_update_subscribed_and_wait(session, ta_base_url, ta_token, ta_verify_ssl, ta_dry_run, log_prefix="TA1")
        if not upd_ok:
            print("    [TA1] AVISO: update_subscribed no confirmó OK (pero continúo con ignore).")

        for vid in ta_deleted_ids_for_ignore:
            ignored_ok = ta_apply_ignore_only(session, vid, ta_base_url, ta_token, ta_verify_ssl, ta_dry_run, log_prefix="TA1")
            if ignored_ok:
                print(f"    [TA1] OK: delete + update_subscribed + ignore aplicado a {vid}")
            else:
                print(f"    [TA1] AVISO: borrado OK pero ignore NO confirmado para {vid} (podría reaparecer).")

    print(f"\n[{channel_id}] Hecho.")


# =========================
# Purge (corto + max), genérico para mp3 + vídeo
# =========================

def ffprobe_duration_seconds(media_path: Path) -> Optional[float]:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(media_path)]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        return None
    try:
        data = json.loads(p.stdout)
        dur = data.get("format", {}).get("duration")
        if dur is None:
            return None
        return float(dur)
    except Exception:
        return None


def ffprobe_comment_tag(mp3_path: Path) -> Optional[str]:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format_tags=comment", "-of", "json", str(mp3_path)]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        return None
    try:
        data = json.loads(p.stdout)
        tags = (data.get("format", {}) or {}).get("tags", {}) or {}
        c = tags.get("comment")
        if c is None:
            c = tags.get("COMMENT")
        if c is None:
            return None
        return str(c)
    except Exception:
        return None


def extract_video_id_from_text(text: str) -> Optional[str]:
    s = (text or "").strip()
    if not s:
        return None
    m = re.search(r"(?:youtu\.be/|youtube\.com/watch\?v=|youtube\.com/shorts/)([A-Za-z0-9_-]{6,})", s)
    if not m:
        return None
    return m.group(1)


def extract_video_id_from_mp3(mp3_path: Path) -> Optional[str]:
    c = ffprobe_comment_tag(mp3_path)
    if not c:
        return None
    return extract_video_id_from_text(c)


def extract_video_id_from_filename(name: str) -> Optional[str]:
    s = (name or "").strip()
    if not s:
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = re.search(r"(?:^|[^A-Za-z0-9_-])([A-Za-z0-9_-]{11})(?:$|[^A-Za-z0-9_-])", s)
    if m:
        return m.group(1)
    return None


def extract_video_id_from_media(media_path: Path) -> Optional[str]:
    ext = media_path.suffix.lower()
    if ext == ".mp3":
        vid = extract_video_id_from_mp3(media_path)
        if vid:
            return vid
    vid = extract_video_id_from_filename(media_path.stem)
    if vid:
        return vid
    return extract_video_id_from_filename(media_path.name)


def list_media_files_in_channel_dir(ch_dir: Path, exts: Set[str]) -> List[Path]:
    out: List[Path] = []
    for ext in exts:
        out.extend(list(ch_dir.glob(f"*{ext}")))
    return sorted([p for p in out if p.is_file()])


def _parse_published_dt(published_at: Optional[str]) -> Optional[datetime]:
    if not published_at:
        return None
    try:
        return datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except Exception:
        return None


def _parse_prefix_dt_from_filename(p: Path) -> Optional[datetime]:
    stem = p.stem
    m = re.match(r"^(\d{8}-\d{6})-", stem)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def purge_short_media_files(
    root: Path,
    min_seconds: int,
    media_exts: Set[str],
    ta_action: str,
    ta_base_url: str,
    ta_token: str,
    ta_verify_ssl: bool,
    ta_dry_run: bool,
    log_prefix: str,
) -> Tuple[int, int, int]:
    ensure_ffprobe()

    deleted_media = 0
    deleted_jpg = 0
    ta_processed = 0

    if not root.exists():
        return (0, 0, 0)

    session = requests.Session()
    ta_ok = ta_enabled(ta_action, ta_base_url, ta_token)

    channel_dirs = iter_channel_dirs_for_root(root)
    for ch_dir in channel_dirs:
        ta_deleted_ids_for_ignore: List[str] = []

        media_files = list_media_files_in_channel_dir(ch_dir, media_exts)
        for mf in media_files:
            dur = ffprobe_duration_seconds(mf)
            if dur is None or dur >= float(min_seconds):
                continue

            jpg = mf.with_suffix(".jpg")
            vid = extract_video_id_from_media(mf)

            try:
                mf.unlink(missing_ok=True)
                deleted_media += 1
                print(f"[PURGE] Borrado (<{min_seconds}s): {mf}")
            except Exception:
                pass

            try:
                if jpg.exists():
                    jpg.unlink(missing_ok=True)
                    deleted_jpg += 1
                    print(f"[PURGE] Borrado JPG asociado: {jpg}")
            except Exception:
                pass

            if ta_ok and ta_action == "delete_ignore" and isinstance(vid, str) and vid:
                ok_del = ta_video_delete(session, vid, ta_base_url, ta_token, ta_verify_ssl, ta_dry_run, log_prefix=log_prefix)
                if ok_del:
                    if not ta_dry_run:
                        ta_wait_video_gone(session, vid, ta_base_url, ta_token, ta_verify_ssl, TA_WAIT_DELETE_TIMEOUT, log_prefix=log_prefix)
                    ta_deleted_ids_for_ignore.append(vid)
                    ta_processed += 1

        if ta_ok and ta_action == "delete_ignore" and ta_deleted_ids_for_ignore:
            ta_update_subscribed_and_wait(session, ta_base_url, ta_token, ta_verify_ssl, ta_dry_run, log_prefix=log_prefix)
            for vid in ta_deleted_ids_for_ignore:
                ignored_ok = ta_apply_ignore_only(session, vid, ta_base_url, ta_token, ta_verify_ssl, ta_dry_run, log_prefix=log_prefix)
                if not ignored_ok:
                    print(f"    [{log_prefix}] AVISO: borrado OK pero ignore NO confirmado para {vid} (podría reaparecer).")

    return (deleted_media, deleted_jpg, ta_processed)


def purge_max_files_per_channel(
    root: Path,
    api_key: str,
    max_keep: int,
    media_exts: Set[str],
    ta_action: str,
    ta_base_url: str,
    ta_token: str,
    ta_verify_ssl: bool,
    ta_dry_run: bool,
    log_prefix: str,
) -> Tuple[int, int, int]:
    ensure_ffprobe()

    deleted_media = 0
    deleted_jpg = 0
    ta_processed = 0

    if not root.exists():
        return (0, 0, 0)

    session = requests.Session()
    ta_ok = ta_enabled(ta_action, ta_base_url, ta_token)

    channel_dirs = iter_channel_dirs_for_root(root)
    for ch_dir in channel_dirs:
        media_files = list_media_files_in_channel_dir(ch_dir, media_exts)
        if len(media_files) <= max_keep:
            continue

        items: List[Dict[str, Any]] = []
        ids: List[str] = []

        for mf in media_files:
            vid = extract_video_id_from_media(mf)
            if vid:
                ids.append(vid)
            items.append({"media": mf, "jpg": mf.with_suffix(".jpg"), "vid": vid, "publishedAt": None})

        meta_map: Dict[str, Dict[str, Optional[object]]] = {}
        if ids:
            uniq_ids = list(dict.fromkeys(ids))
            meta_map = yt_video_meta(api_key, uniq_ids)

        for it in items:
            vid = it.get("vid")
            if isinstance(vid, str) and vid:
                meta = meta_map.get(vid, {}) or {}
                it["publishedAt"] = meta.get("publishedAt")

        dt_min = datetime.min.replace(tzinfo=timezone.utc)

        def sort_key(it: Dict[str, Any]) -> Tuple[int, datetime, str]:
            pub_raw = it.get("publishedAt") if isinstance(it.get("publishedAt"), str) else None
            dt = _parse_published_dt(pub_raw)
            if dt is None:
                dt = _parse_prefix_dt_from_filename(it["media"])
            if dt is None:
                return (0, dt_min, str(it["media"].name))
            return (1, dt, str(it["media"].name))

        items_sorted = sorted(items, key=sort_key)
        to_delete = items_sorted[: max(0, len(items_sorted) - max_keep)]
        if not to_delete:
            continue

        ta_deleted_ids_for_ignore: List[str] = []

        for it in to_delete:
            mf: Path = it["media"]
            jpg: Path = it["jpg"]
            vid = it.get("vid")

            try:
                mf.unlink(missing_ok=True)
                deleted_media += 1
                print(f"[PURGE-MAX] Borrado (>{max_keep}/canal): {mf}")
            except Exception:
                pass

            try:
                if jpg.exists():
                    jpg.unlink(missing_ok=True)
                    deleted_jpg += 1
                    print(f"[PURGE-MAX] Borrado JPG asociado: {jpg}")
            except Exception:
                pass

            if ta_ok and ta_action == "delete_ignore" and isinstance(vid, str) and vid:
                ok_del = ta_video_delete(session, vid, ta_base_url, ta_token, ta_verify_ssl, ta_dry_run, log_prefix=log_prefix)
                if ok_del:
                    if not ta_dry_run:
                        ta_wait_video_gone(session, vid, ta_base_url, ta_token, ta_verify_ssl, TA_WAIT_DELETE_TIMEOUT, log_prefix=log_prefix)
                    ta_deleted_ids_for_ignore.append(vid)
                    ta_processed += 1

        if ta_ok and ta_action == "delete_ignore" and ta_deleted_ids_for_ignore:
            ta_update_subscribed_and_wait(session, ta_base_url, ta_token, ta_verify_ssl, ta_dry_run, log_prefix=log_prefix)
            for vid in ta_deleted_ids_for_ignore:
                ignored_ok = ta_apply_ignore_only(session, vid, ta_base_url, ta_token, ta_verify_ssl, ta_dry_run, log_prefix=log_prefix)
                if not ignored_ok:
                    print(f"    [{log_prefix}] AVISO: borrado OK pero ignore NO confirmado para {vid} (podría reaparecer).")

    return (deleted_media, deleted_jpg, ta_processed)


# =========================
# Main
# =========================

def main() -> int:
    p = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--src-root", default=str(SRC_ROOT))
    p.add_argument("--dest-root", default=str(DEST_ROOT))
    p.add_argument("--yt-api-key", default=str(YT_API_KEY))

    p.add_argument("--delete-video", action="store_true", default=DELETE_VIDEO_AFTER_MP3)
    p.add_argument("--overwrite-mp3", action="store_true", default=OVERWRITE_MP3)
    p.add_argument("--overwrite-images", action="store_true", default=OVERWRITE_IMAGES)

    p.add_argument("--no-embed-id3", action="store_true", default=not EMBED_ID3_TAGS)
    p.add_argument("--no-embed-cover", action="store_true", default=not EMBED_COVER_ART)
    p.add_argument("--tag-existing", action="store_true", default=TAG_EXISTING_MP3)

    # TA1
    p.add_argument("--ta-base-url", default=str(TA_BASE_URL))
    p.add_argument("--ta-token", default=str(TA_TOKEN))
    p.add_argument("--ta-action", choices=["none", "delete", "delete_ignore"], default=str(TA_ACTION))
    p.add_argument("--ta-no-verify-ssl", action="store_true", default=not TA_VERIFY_SSL)
    p.add_argument("--ta-dry-run", action="store_true", default=TA_DRY_RUN)

    # TA2
    p.add_argument("--ta2-base-url", default=str(TA2_BASE_URL))
    p.add_argument("--ta2-token", default=str(TA2_TOKEN))
    p.add_argument("--ta2-action", choices=["none", "delete", "delete_ignore"], default=str(TA2_ACTION))
    p.add_argument("--ta2-no-verify-ssl", action="store_true", default=not TA2_VERIFY_SSL)
    p.add_argument("--ta2-dry-run", action="store_true", default=TA2_DRY_RUN)

    args = p.parse_args()

    src_root = Path(args.src_root)
    dest_root = Path(args.dest_root)
    api_key = args.yt_api_key

    embed_id3_tags = not args.no_embed_id3
    embed_cover_art = not args.no_embed_cover
    tag_existing_mp3 = args.tag_existing

    if not api_key or api_key in {"PON_AQUI_TU_API_KEY", "PON_AQUI_TU_YT_API_KEY"}:
        print("Falta API key. Edita YT_API_KEY arriba o pásala con --yt-api-key.")
        return 2

    if not src_root.exists():
        print(f"No existe: {src_root}")
        return 2

    dest_root.mkdir(parents=True, exist_ok=True)

    channel_dirs = list_channel_dirs(src_root)
    if not channel_dirs:
        print("No hay carpetas de canales en src-root.")
        return 0

    channel_ids = [d.name for d in channel_dirs]
    ch_meta = yt_channel_meta(api_key, channel_ids)

    ta1_verify_ssl = not args.ta_no_verify_ssl
    ta2_verify_ssl = not args.ta2_no_verify_ssl

    errors = 0

    # =======================
    # Export (TA1)
    # =======================
    for ch_dir in channel_dirs:
        cid = ch_dir.name
        cname = (ch_meta.get(cid, {}).get("title") or cid)
        cthumbs = ch_meta.get(cid, {}).get("thumbnails")

        try:
            export_channel(
                cid,
                str(cname),
                cthumbs if isinstance(cthumbs, dict) else None,
                ch_dir,
                dest_root,
                api_key,
                args.delete_video,
                args.overwrite_mp3,
                args.overwrite_images,
                args.ta_action,
                args.ta_base_url,
                args.ta_token,
                ta1_verify_ssl,
                args.ta_dry_run,
                embed_id3_tags,
                embed_cover_art,
                tag_existing_mp3,
            )
        except Exception as e:
            errors += 1
            print(f"\n[{cid}] ERROR: {e}\n")

    # =======================
    # PURGE + PURGE-MAX (TA1) en DEST y gPodder
    # =======================
    try:
        for purge_root in [dest_root, RETAG_EXTRA_ROOT]:
            if not purge_root.exists():
                print(f"\n[PURGE] (Skip) No existe: {purge_root}\n")
                continue

            print(f"\n[PURGE] Revisando en {purge_root} y borrando los < {PURGE_SHORTER_THAN_SECONDS}s ...\n")
            dmedia, djpg, ta_cnt = purge_short_media_files(
                root=purge_root,
                min_seconds=PURGE_SHORTER_THAN_SECONDS,
                media_exts=PURGE_EXTS_AUDIO,
                ta_action=args.ta_action,
                ta_base_url=args.ta_base_url,
                ta_token=args.ta_token,
                ta_verify_ssl=ta1_verify_ssl,
                ta_dry_run=args.ta_dry_run,
                log_prefix="TA1",
            )
            print(f"\n[PURGE] Hecho en {purge_root}. Borrados: {dmedia} media y {djpg} jpg. TA1 procesados: {ta_cnt}.\n")

            print(f"\n[PURGE-MAX] Limitando a {MAX_FILES_PER_CHANNEL} ficheros por canal/carpeta en {purge_root} ...\n")
            dmedia2, djpg2, ta_cnt2 = purge_max_files_per_channel(
                root=purge_root,
                api_key=api_key,
                max_keep=MAX_FILES_PER_CHANNEL,
                media_exts=PURGE_EXTS_AUDIO,
                ta_action=args.ta_action,
                ta_base_url=args.ta_base_url,
                ta_token=args.ta_token,
                ta_verify_ssl=ta1_verify_ssl,
                ta_dry_run=args.ta_dry_run,
                log_prefix="TA1",
            )
            print(f"\n[PURGE-MAX] Hecho en {purge_root}. Borrados: {dmedia2} media y {djpg2} jpg. TA1 procesados: {ta_cnt2}.\n")
    except Exception as e:
        errors += 1
        print(f"\n[PURGE/PURGE-MAX] ERROR: {e}\n")

    # =======================
    # RETAG
    # =======================
    if embed_id3_tags and RETAG_TITLE_FROM_FILENAME:
        try:
            print(f"\n[RETAG] TITLE <- filename en {dest_root} ...\n")
            retag_ok, retag_err = retag_title_from_filename(dest_root)
            print(f"\n[RETAG] Hecho. Actualizados: {retag_ok}. Errores: {retag_err}.\n")
            errors += retag_err
        except Exception as e:
            errors += 1
            print(f"\n[RETAG] ERROR: {e}\n")

        try:
            print(f"\n[RETAG] TITLE <- filename en {RETAG_EXTRA_ROOT} ...\n")
            retag_ok2, retag_err2 = retag_title_from_filename(RETAG_EXTRA_ROOT)
            print(f"\n[RETAG] Hecho. Actualizados: {retag_ok2}. Errores: {retag_err2}.\n")
            errors += retag_err2
        except Exception as e:
            errors += 1
            print(f"\n[RETAG] ERROR: {e}\n")

    # =======================
    # TA2: PURGE + PURGE-MAX en paths extra
    # =======================
    try:
        for purge_root in TA2_PURGE_PATHS:
            if not purge_root.exists():
                print(f"\n[TA2-PURGE] (Skip) No existe: {purge_root}\n")
                continue

            print(f"\n[TA2-PURGE] Revisando en {purge_root} y borrando los < {PURGE_SHORTER_THAN_SECONDS}s ...\n")
            dmedia, djpg, ta_cnt = purge_short_media_files(
                root=purge_root,
                min_seconds=PURGE_SHORTER_THAN_SECONDS,
                media_exts=PURGE_EXTS_TA2,
                ta_action=args.ta2_action,
                ta_base_url=args.ta2_base_url,
                ta_token=args.ta2_token,
                ta_verify_ssl=ta2_verify_ssl,
                ta_dry_run=args.ta2_dry_run,
                log_prefix="TA2",
            )
            print(f"\n[TA2-PURGE] Hecho en {purge_root}. Borrados: {dmedia} media y {djpg} jpg. TA2 procesados: {ta_cnt}.\n")

            print(f"\n[TA2-PURGE-MAX] Limitando a {MAX_FILES_PER_CHANNEL} ficheros por canal/carpeta en {purge_root} ...\n")
            dmedia2, djpg2, ta_cnt2 = purge_max_files_per_channel(
                root=purge_root,
                api_key=api_key,
                max_keep=MAX_FILES_PER_CHANNEL,
                media_exts=PURGE_EXTS_TA2,
                ta_action=args.ta2_action,
                ta_base_url=args.ta2_base_url,
                ta_token=args.ta2_token,
                ta_verify_ssl=ta2_verify_ssl,
                ta_dry_run=args.ta2_dry_run,
                log_prefix="TA2",
            )
            print(f"\n[TA2-PURGE-MAX] Hecho en {purge_root}. Borrados: {dmedia2} media y {djpg2} jpg. TA2 procesados: {ta_cnt2}.\n")

    except Exception as e:
        errors += 1
        print(f"\n[TA2] ERROR: {e}\n")

    if errors:
        print(f"\nFinalizado con {errors} error(es).")
        return 1

    print("\nFinalizado OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
