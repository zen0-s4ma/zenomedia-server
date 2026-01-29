#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# =========================
# CONFIGURACIÓN (EDITA AQUÍ)
# =========================

# Origen/destino
SRC_ROOT = Path(r"E:\YouTube\Audio")
DEST_ROOT = Path(r"E:\Youtube_Podcast")

# --- NUEVO: ruta adicional SOLO para retag del TITLE ---
RETAG_EXTRA_ROOT = Path(r"F:\Podcasts")

# YouTube Data API v3 (solo para obtener títulos/fechas/miniaturas)
YT_API_KEY = "AIzaSyA-9iJS0bDWXpCGPqvM4NzM-9EoBInli4A"

# Comportamiento local
DELETE_VIDEO_AFTER_MP3 = True     # borra el vídeo COPIADO (en DEST) tras crear el mp3
OVERWRITE_MP3 = False            # sobrescribe mp3 si ya existe
OVERWRITE_IMAGES = True          # sobrescribe jpg si ya existe (thumbnails + poster)

# Orden de preferencia de miniaturas de YouTube
THUMB_KEYS_ORDER = ["maxres", "standard", "high", "medium", "default"]

REQUEST_TIMEOUT = 30

# --- NUEVO: Embebido de metadatos (ID3) para Jellyfin ---
# (Jellyfin prioriza tags embebidos y si faltan puede agrupar raro en "Recientes") :contentReference[oaicite:1]{index=1}
EMBED_ID3_TAGS = True            # escribe tags ID3 en el MP3
EMBED_COVER_ART = True           # embebe la miniatura JPG como carátula dentro del MP3
TAG_EXISTING_MP3 = False         # si el MP3 ya existía y se saltó conversión, NO retaguear salvo que actives esta opción
ID3V2_VERSION = 3                # ID3v2.3 (compatibilidad alta)
WRITE_ID3V1 = True               # añade ID3v1 footer
PODCAST_GENRE = "Podcast"        # estilo gPodder (Genre="Podcast") :contentReference[oaicite:2]{index=2}

# --- TubeArchivist (pasarela) ---
# URL base de tu TA (la misma que usas en el navegador), sin barra final.
TA_BASE_URL = "https://tubeaudio.maripiflix.xyz/"
# Token: Settings -> (Application/Integrations/API Token) en tu TA.
TA_TOKEN = "3810bd0c7dca327ee44a169ed0fe5e481ddb90fb"
# Qué hacer al terminar:
#  - "none": no toca TA
#  - "delete": intenta borrar (y puede que ignore dependa de tu versión/config)
#  - "delete_ignore": intenta el equivalente de "Delete and ignore"
TA_ACTION = "delete_ignore"
TA_VERIFY_SSL = True
TA_DRY_RUN = False

# --- Purga final ---
PURGE_SHORTER_THAN_SECONDS = 5 * 60  # 5 minutos

# --- Retag final (sobrescribir TITLE=nombre de fichero sin extensión) ---
RETAG_TITLE_FROM_FILENAME = True

# =========================

YOUTUBE_API_CHANNELS = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_API_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".m4v"}


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
    """
    published_at: '2026-01-26T09:35:00Z' (UTC)
    Si no viene (privado/eliminado), usa la mtime del fichero como fallback (UTC).
    """
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


# --- NUEVO: helpers de tagging (ID3 + cover art) ---

def sanitize_tag_value(s: str, max_len: int = 500) -> str:
    """
    Limpia valores para -metadata (evita saltos raros / nulos).
    """
    s = (s or "").replace("\x00", "").replace("\r", " ").replace("\n", " ").strip()
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len].strip()
    return s


def iso_date_for_tag(published_at: Optional[object], fallback_file: Path) -> str:
    """
    Devuelve 'YYYY-MM-DD' (apto para tag 'date').
    published_at suele ser '2026-01-26T09:35:00Z'
    """
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
    """
    Reescribe el MP3 (sin reencode, -c copy) embebiendo tags ID3 y, si existe, carátula.
    Usa un temporal y luego replace para no dejar archivos rotos si falla.

    Basado en ejemplos oficiales de FFmpeg:
      - id3v2_version 3 + write_id3v1 1
      - attach picture: -i input.mp3 -i cover.png -c copy -map 0 -map 1 ... out.mp3 :contentReference[oaicite:3]{index=3}
    """
    tmp = mp3_path.with_suffix(".tagtmp.mp3")
    if tmp.exists():
        tmp.unlink(missing_ok=True)

    base_cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]

    # metadata
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
        # Adjuntar carátula (cover) siguiendo el patrón oficial de FFmpeg con -map 0 -map 1 :contentReference[oaicite:4]{index=4}
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


# --- NUEVO: Retag final SOLO del TITLE (sin tocar el resto de tags) ---

def ffmpeg_overwrite_title_tag_inplace(
    mp3_path: Path,
    new_title: str,
    id3v2_version: int = 3,
    write_id3v1: bool = True,
) -> None:
    """
    Sobrescribe SOLO el tag 'title' en un MP3 SIN re-encode (-c copy),
    preservando el resto de metadatos existentes (map_metadata 0).
    Usa temporal + replace por seguridad.
    """
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
    """
    Lee el tag TITLE actual del MP3 usando ffprobe.
    Devuelve str o None si no está / falla.
    """
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
        # ffprobe puede devolver 'title' o 'TITLE' según contexto
        t = tags.get("title")
        if t is None:
            t = tags.get("TITLE")
        if t is None:
            return None
        return str(t)
    except Exception:
        return None


def retag_title_from_filename(dest_root: Path) -> Tuple[int, int]:
    """
    Recorre todas las carpetas dentro de dest_root y, para cada *.mp3,
    sobrescribe el tag TITLE con mp3.stem (nombre del fichero sin extensión)
    SOLO si el TITLE actual NO coincide ya con mp3.stem.
    Devuelve (retag_ok, retag_errors).
    """
    ensure_ffmpeg()
    ensure_ffprobe()

    retag_ok = 0
    retag_err = 0

    if not dest_root.exists():
        return (0, 0)

    channel_dirs = [d for d in dest_root.iterdir() if d.is_dir()]
    for ch_dir in channel_dirs:
        for mp3 in ch_dir.glob("*.mp3"):
            desired = sanitize_tag_value(mp3.stem)
            current_raw = ffprobe_title_tag(mp3)
            current = sanitize_tag_value(current_raw or "")

            if current == desired:
                # Ya está actualizado, no tocar
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
    """
    Intenta descargar de una lista de URLs (de mayor a menor calidad).
    Guarda como JPEG (si PIL está disponible y hace falta convertir).
    Devuelve True si guardó algo.
    """
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

            # Convertir con Pillow si está instalado
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
    """
    Devuelve: { channel_id: {"title": "...", "thumbnails": {...}} }
    """
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
    """
    Devuelve: { video_id: {"title": "...", "publishedAt": "...Z", "thumbnails": {...}} }
    """
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
# TubeArchivist API helpers
# =========================
def ta_enabled(action: str, base_url: str, token: str) -> bool:
    return action in {"delete", "delete_ignore"} and bool(base_url) and bool(token) and token != "PON_AQUI_TU_TA_TOKEN"


def ta_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def ta_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def ta_request(
    session: requests.Session,
    method: str,
    base_url: str,
    token: str,
    path: str,
    verify_ssl: bool,
    json_body: Optional[dict] = None,
) -> requests.Response:
    url = ta_url(base_url, path)
    return session.request(
        method=method.upper(),
        url=url,
        headers=ta_headers(token),
        json=json_body,
        timeout=REQUEST_TIMEOUT,
        verify=verify_ssl,
    )


def ta_delete_or_ignore_video(
    session: requests.Session,
    video_id: str,
    action: str,
    base_url: str,
    token: str,
    verify_ssl: bool,
    dry_run: bool,
) -> bool:
    if dry_run:
        print(f"    [TA] DRY-RUN: {action} {video_id}")
        return True

    try:
        r0 = ta_request(session, "GET", base_url, token, f"/api/video/{video_id}/", verify_ssl)
        if r0.status_code == 404:
            print(f"    [TA] No existe en TA (404): {video_id}")
            return False
    except Exception as e:
        print(f"    [TA] Aviso: no pude comprobar existencia de {video_id}: {e}")

    attempts: List[Tuple[str, str, Optional[dict], str]] = []
    if action == "delete_ignore":
        attempts += [
            ("POST", f"/api/video/{video_id}/delete/", {"delete_type": "delete_ignore"}, "POST /api/video/<id>/delete/ {delete_type:delete_ignore}"),
            ("POST", f"/api/video/{video_id}/delete/", {"delete_media": True, "ignore": True}, "POST /api/video/<id>/delete/ {delete_media,ignore}"),
            ("POST", f"/api/video/{video_id}/delete-ignore/", {}, "POST /api/video/<id>/delete-ignore/"),
            ("POST", f"/api/video/{video_id}/delete_ignore/", {}, "POST /api/video/<id>/delete_ignore/"),
            ("DELETE", f"/api/video/{video_id}/?ignore=1", None, "DELETE /api/video/<id>/?ignore=1"),
        ]

    attempts += [
        ("DELETE", f"/api/video/{video_id}/", None, "DELETE /api/video/<id>/"),
    ]

    for method, path, body, label in attempts:
        try:
            r = ta_request(session, method, base_url, token, path, verify_ssl, json_body=body)
            if r.status_code in (200, 201, 202, 204):
                print(f"    [TA] OK ({label}) -> {r.status_code}")
                return True
            if r.status_code in (404, 405):
                continue
            print(f"    [TA] FAIL ({label}) -> {r.status_code}: {r.text[:200]}")
        except Exception:
            continue

    print(f"    [TA] No pude aplicar '{action}' para {video_id}.")
    return False


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
    # --- NUEVO ---
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
        title = (meta.get("title") or vid)  # type: ignore[assignment]

        prefix = published_prefix(meta.get("publishedAt"), v)  # type: ignore[arg-type]
        safe_title = sanitize_windows(str(title), max_len=140 - len(prefix) - 1)

        dst_video = unique_path(dest_channel_dir / f"{prefix}-{safe_title}{v.suffix.lower()}")
        shutil.copy2(v, dst_video)

        dst_mp3 = dst_video.with_suffix(".mp3")
        dst_jpg = dst_mp3.with_suffix(".jpg")

        thumbs = meta.get("thumbnails") or {}  # type: ignore[assignment]
        thumb_urls = iter_thumb_urls(thumbs if isinstance(thumbs, dict) else {})

        work_items.append((vid, dst_video, dst_mp3, dst_jpg, thumb_urls))
        print(f"  OK: {v.name}  ->  {dst_video.name}")

    print("\nConvirtiendo a MP3...\n")
    ensure_ffmpeg()

    session = requests.Session()
    ta_ok = ta_enabled(ta_action, ta_base_url, ta_token)

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

        # --- NUEVO: Embebido de tags ID3 + carátula (sin reencode) ---
        if embed_id3_tags and dst_mp3.exists():
            do_tag = mp3_created_now or tag_existing_mp3
            if do_tag:
                meta = meta_map.get(vid, {}) or {}
                vtitle = str(meta.get("title") or dst_mp3.stem)
                vpub = meta.get("publishedAt")

                date_str = iso_date_for_tag(vpub, dst_mp3)

                # Estilo gPodder: Album=Podcast, Artist=Podcast, Genre="Podcast", publish date :contentReference[oaicite:5]{index=5}
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
            ok_ta = ta_delete_or_ignore_video(
                session=session,
                video_id=vid,
                action=ta_action,
                base_url=ta_base_url,
                token=ta_token,
                verify_ssl=ta_verify_ssl,
                dry_run=ta_dry_run,
            )
            if ok_ta:
                print(f"    [TA] Limpieza aplicada a {vid}")
            else:
                print(f"    [TA] No se pudo limpiar {vid} (se deja en TA)")

    print(f"\n[{channel_id}] Hecho.")


def ffprobe_duration_seconds(mp3_path: Path) -> Optional[float]:
    """
    Devuelve duración en segundos usando ffprobe.
    Si falla, devuelve None.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(mp3_path),
    ]
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


def purge_short_mp3s(dest_root: Path, min_seconds: int) -> Tuple[int, int]:
    """
    Recorre todas las carpetas dentro de dest_root y borra:
      - *.mp3 con duración < min_seconds
      - su *.jpg correspondiente (mismo nombre) si existe
    Devuelve (borrados_mp3, borrados_jpg)
    """
    ensure_ffprobe()

    deleted_mp3 = 0
    deleted_jpg = 0

    if not dest_root.exists():
        return (0, 0)

    channel_dirs = [d for d in dest_root.iterdir() if d.is_dir()]
    for ch_dir in channel_dirs:
        for mp3 in ch_dir.glob("*.mp3"):
            dur = ffprobe_duration_seconds(mp3)
            if dur is None:
                # Si no podemos leer duración, no borramos por seguridad
                continue
            if dur < float(min_seconds):
                jpg = mp3.with_suffix(".jpg")
                try:
                    mp3.unlink(missing_ok=True)
                    deleted_mp3 += 1
                    print(f"[PURGE] Borrado MP3 (<{min_seconds}s): {mp3}")
                except Exception:
                    pass
                try:
                    if jpg.exists():
                        jpg.unlink(missing_ok=True)
                        deleted_jpg += 1
                        print(f"[PURGE] Borrado JPG asociado: {jpg}")
                except Exception:
                    pass

    return (deleted_mp3, deleted_jpg)


def main() -> int:
    p = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--src-root", default=str(SRC_ROOT))
    p.add_argument("--dest-root", default=str(DEST_ROOT))
    p.add_argument("--yt-api-key", default=str(YT_API_KEY))

    p.add_argument("--delete-video", action="store_true", default=DELETE_VIDEO_AFTER_MP3)
    p.add_argument("--overwrite-mp3", action="store_true", default=OVERWRITE_MP3)
    p.add_argument("--overwrite-images", action="store_true", default=OVERWRITE_IMAGES)

    # --- NUEVO: toggles de tagging/caratula ---
    p.add_argument("--no-embed-id3", action="store_true", default=not EMBED_ID3_TAGS, help="Desactiva embebido de tags ID3 en MP3")
    p.add_argument("--no-embed-cover", action="store_true", default=not EMBED_COVER_ART, help="Desactiva embebido de carátula (JPG) en MP3")
    p.add_argument("--tag-existing", action="store_true", default=TAG_EXISTING_MP3, help="Retaguea también MP3 ya existentes (aunque se haya SKIP MP3)")

    # TubeArchivist
    p.add_argument("--ta-base-url", default=str(TA_BASE_URL))
    p.add_argument("--ta-token", default=str(TA_TOKEN))
    p.add_argument("--ta-action", choices=["none", "delete", "delete_ignore"], default=str(TA_ACTION))
    p.add_argument("--ta-no-verify-ssl", action="store_true", default=not TA_VERIFY_SSL)
    p.add_argument("--ta-dry-run", action="store_true", default=TA_DRY_RUN)

    args = p.parse_args()

    src_root = Path(args.src_root)
    dest_root = Path(args.dest_root)
    api_key = args.yt_api_key

    embed_id3_tags = not args.no_embed_id3
    embed_cover_art = not args.no_embed_cover
    tag_existing_mp3 = args.tag_existing

    if not api_key or api_key == "PON_AQUI_TU_API_KEY":
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

    ta_verify_ssl = not args.ta_no_verify_ssl

    errors = 0
    for ch_dir in channel_dirs:
        cid = ch_dir.name
        cname = (ch_meta.get(cid, {}).get("title") or cid)  # type: ignore[assignment]
        cthumbs = ch_meta.get(cid, {}).get("thumbnails")  # type: ignore[assignment]

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
                ta_verify_ssl,
                args.ta_dry_run,
                # --- NUEVO ---
                embed_id3_tags,
                embed_cover_art,
                tag_existing_mp3,
            )
        except Exception as e:
            errors += 1
            print(f"\n[{cid}] ERROR: {e}\n")

    # ✅ PURGA FINAL (muy importante: al final, como pediste)
    try:
        print(f"\n[PURGE] Revisando MP3 en {dest_root} y borrando los < {PURGE_SHORTER_THAN_SECONDS}s ...\n")
        deleted_mp3, deleted_jpg = purge_short_mp3s(dest_root, PURGE_SHORTER_THAN_SECONDS)
        print(f"\n[PURGE] Hecho. Borrados: {deleted_mp3} mp3 y {deleted_jpg} jpg.\n")
    except Exception as e:
        errors += 1
        print(f"\n[PURGE] ERROR: {e}\n")

    # ✅ RETAG FINAL: sobrescribe TITLE = nombre del fichero (sin extensión)
    if embed_id3_tags and RETAG_TITLE_FROM_FILENAME:
        try:
            print(f"\n[RETAG] Sobrescribiendo tag TITLE con el nombre del fichero (sin extensión) en {dest_root} ...\n")
            retag_ok, retag_err = retag_title_from_filename(dest_root)
            print(f"\n[RETAG] Hecho. Actualizados: {retag_ok}. Errores: {retag_err}.\n")
            if retag_err:
                errors += retag_err
        except Exception as e:
            errors += 1
            print(f"\n[RETAG] ERROR: {e}\n")

        # ✅ NUEVO: repetir el retag SOLO en la ruta adicional
        try:
            print(f"\n[RETAG] Sobrescribiendo tag TITLE con el nombre del fichero (sin extensión) en {RETAG_EXTRA_ROOT} ...\n")
            retag_ok2, retag_err2 = retag_title_from_filename(RETAG_EXTRA_ROOT)
            print(f"\n[RETAG] Hecho. Actualizados: {retag_ok2}. Errores: {retag_err2}.\n")
            if retag_err2:
                errors += retag_err2
        except Exception as e:
            errors += 1
            print(f"\n[RETAG] ERROR: {e}\n")

    if errors:
        print(f"\nFinalizado con {errors} error(es).")
        return 1

    print("\nFinalizado OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
