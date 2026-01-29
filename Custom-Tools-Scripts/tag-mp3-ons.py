#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, List


# =========================
# DEFAULTS
# =========================
DEFAULT_ROOT = Path(r"E:\Youtube_Podcast")

DEFAULT_GENRE = "Podcast"
DEFAULT_ID3V2_VERSION = 3          # ID3v2.3 (muy compatible)
DEFAULT_WRITE_ID3V1 = True         # añade ID3v1 footer
DEFAULT_EMBED_COVER = True
DEFAULT_COVER_MODE = "thumb"       # thumb|poster|auto|none
DEFAULT_ALBUM_MODE = "channel"     # channel|episode
DEFAULT_PRESERVE_MTIME = True
# =========================


@dataclass
class ParsedName:
    dt: Optional[datetime]
    title: str


def ensure_ffmpeg() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True, text=True)
    except Exception as e:
        raise RuntimeError(f"No encuentro ffmpeg en PATH. Detalle: {e}")


def sanitize_tag_value(s: str, max_len: int = 500) -> str:
    # Limpieza mínima para evitar nulos / saltos raros
    s = (s or "").replace("\x00", "").replace("\r", " ").replace("\n", " ").strip()
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len].strip()
    return s


def parse_mp3_filename(stem: str) -> ParsedName:
    """
    Ejemplo:
      20260123-180026-🚨 ARBELOA SE QUEMÓ POR LEALTAD EN UNA GUERRA PERDIDA
    """
    m = re.match(r"^(?P<prefix>\d{8}-\d{6})-(?P<title>.+)$", stem)
    if not m:
        return ParsedName(dt=None, title=stem.strip())

    prefix = m.group("prefix")
    title = m.group("title").strip()

    dt = None
    try:
        dt = datetime.strptime(prefix, "%Y%m%d-%H%M%S")
    except Exception:
        dt = None

    return ParsedName(dt=dt, title=title)


def choose_cover(mp3: Path, channel_dir: Path, cover_mode: str) -> Optional[Path]:
    """
    cover_mode:
      - thumb: usa <mp3>.jpg
      - poster: usa poster.jpg
      - auto: thumb si existe, si no poster
      - none: sin cover
    """
    thumb = mp3.with_suffix(".jpg")
    poster = channel_dir / "poster.jpg"

    if cover_mode == "none":
        return None
    if cover_mode == "thumb":
        return thumb if thumb.exists() else None
    if cover_mode == "poster":
        return poster if poster.exists() else None
    # auto
    if thumb.exists():
        return thumb
    if poster.exists():
        return poster
    return None


def build_tags(
    parsed: ParsedName,
    channel_name: str,
    album_mode: str,
    genre: str,
) -> Dict[str, str]:
    """
    Tags estilo podcast (parecido a gPodder):
      - title: episodio
      - album: podcast/canal (o episodio si album_mode=episode)
      - artist: podcast/canal
      - album_artist: podcast/canal
      - date: YYYY-MM-DD (si tenemos dt)
      - genre: Podcast
    """
    channel_name = sanitize_tag_value(channel_name)
    title = sanitize_tag_value(parsed.title)

    if album_mode == "episode":
        album = title
    else:
        album = channel_name

    tags: Dict[str, str] = {
        "title": title,
        "artist": channel_name,
        "album": album,
        "album_artist": channel_name,
        "genre": sanitize_tag_value(genre),
    }

    if parsed.dt:
        tags["date"] = parsed.dt.date().isoformat()

    return tags


def ffmpeg_tag_mp3_inplace(
    mp3_path: Path,
    tags: Dict[str, str],
    cover: Optional[Path],
    id3v2_version: int,
    write_id3v1: bool,
    preserve_mtime: bool,
    dry_run: bool,
    backup: bool,
) -> Tuple[bool, str]:
    """
    Reescribe el MP3 con -c copy, embebiendo metadata y cover art (si aplica).
    Usamos un temporal y os.replace para seguridad.

    FFmpeg doc oficial:
      - Escribir ID3v2.3 + ID3v1: -id3v2_version 3 -write_id3v1 1
      - Adjuntar picture: -i input.mp3 -i cover.png -c copy -map 0 -map 1 ... out.mp3
    :contentReference[oaicite:3]{index=3}
    """
    tmp = mp3_path.with_suffix(".tagtmp.mp3")
    if tmp.exists():
        tmp.unlink(missing_ok=True)

    original_stat = mp3_path.stat()
    mtime = original_stat.st_mtime

    if backup:
        bak = mp3_path.with_suffix(mp3_path.suffix + ".bak")  # .mp3.bak
        if not bak.exists():
            if dry_run:
                return True, f"DRY-RUN: backup -> {bak.name}"
            shutil.copy2(mp3_path, bak)

    cmd: List[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]

    if cover and cover.exists():
        # Map solo audio + imagen y marca la imagen como cover.
        cmd += ["-i", str(mp3_path), "-i", str(cover)]
        cmd += ["-map", "0:a", "-map", "1:v"]
        cmd += ["-c", "copy"]
        cmd += ["-disposition:v:0", "attached_pic"]
        cmd += ["-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (Front)"]
    else:
        cmd += ["-i", str(mp3_path)]
        cmd += ["-map", "0:a"]
        cmd += ["-c", "copy"]

    # Elimina metadata previa para que quede “limpio” y consistente.
    cmd += ["-map_metadata", "-1"]

    cmd += ["-id3v2_version", str(id3v2_version)]
    if write_id3v1:
        cmd += ["-write_id3v1", "1"]

    # Añadir tags
    for k, v in tags.items():
        v = sanitize_tag_value(v)
        if v:
            cmd += ["-metadata", f"{k}={v}"]

    cmd += [str(tmp)]

    if dry_run:
        return True, "DRY-RUN: " + " ".join(cmd)

    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        tmp.unlink(missing_ok=True)
        return False, err

    os.replace(tmp, mp3_path)

    if preserve_mtime:
        try:
            os.utime(mp3_path, (mtime, mtime))
        except Exception:
            pass

    return True, "OK"


def iter_mp3s(root: Path) -> List[Path]:
    # Solo 1 nivel: root/<canal>/*.mp3
    mp3s: List[Path] = []
    for ch in sorted([d for d in root.iterdir() if d.is_dir()]):
        mp3s.extend(sorted(ch.glob("*.mp3")))
    return mp3s


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tagger one-shot para E:\\Youtube_Podcast\\<canal>\\*.mp3 (ID3 + cover art via ffmpeg)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Carpeta raíz (E:\\Youtube_Podcast)")
    parser.add_argument("--only-channel", default="", help="Procesa solo este canal (nombre exacto de carpeta)")
    parser.add_argument("--dry-run", action="store_true", help="No modifica nada, solo imprime lo que haría")
    parser.add_argument("--backup", action="store_true", help="Crea .mp3.bak antes de modificar (una vez por archivo)")

    parser.add_argument("--genre", default=DEFAULT_GENRE, help="Valor para tag Genre")
    parser.add_argument("--album-mode", choices=["channel", "episode"], default=DEFAULT_ALBUM_MODE,
                        help="album=canal (recomendado) o album=episodio (si quieres ‘más separación’)")
    parser.add_argument("--cover-mode", choices=["thumb", "poster", "auto", "none"], default=DEFAULT_COVER_MODE,
                        help="thumb=<mp3>.jpg, poster=poster.jpg, auto=thumb->poster, none=sin cover")
    parser.add_argument("--no-embed-cover", action="store_true", help="No embebe carátula aunque exista")
    parser.add_argument("--id3v2-version", type=int, default=DEFAULT_ID3V2_VERSION, help="3 o 4 (ID3v2.3/2.4)")
    parser.add_argument("--no-write-id3v1", action="store_true", help="No escribe ID3v1 footer")
    parser.add_argument("--no-preserve-mtime", action="store_true", help="No preserva mtime original del mp3")

    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"ERROR: root no existe: {root}")
        return 2

    ensure_ffmpeg()

    embed_cover = DEFAULT_EMBED_COVER and (not args.no_embed_cover)
    write_id3v1 = DEFAULT_WRITE_ID3V1 and (not args.no_write_id3v1)
    preserve_mtime = DEFAULT_PRESERVE_MTIME and (not args.no_preserve_mtime)

    mp3s = iter_mp3s(root)
    if args.only_channel:
        mp3s = [p for p in mp3s if p.parent.name == args.only_channel]

    if not mp3s:
        print("No hay MP3 que procesar (o el canal no coincide).")
        return 0

    ok_count = 0
    fail_count = 0

    print(f"Root: {root}")
    print(f"MP3 detectados: {len(mp3s)}")
    print(f"album-mode={args.album_mode} | cover-mode={args.cover_mode} | embed_cover={embed_cover}")
    print("")

    for i, mp3 in enumerate(mp3s, start=1):
        channel_dir = mp3.parent
        channel_name = channel_dir.name

        parsed = parse_mp3_filename(mp3.stem)
        tags = build_tags(parsed, channel_name, args.album_mode, args.genre)

        cover = None
        if embed_cover:
            cover = choose_cover(mp3, channel_dir, args.cover_mode)

        success, msg = ffmpeg_tag_mp3_inplace(
            mp3_path=mp3,
            tags=tags,
            cover=cover,
            id3v2_version=args.id3v2_version,
            write_id3v1=write_id3v1,
            preserve_mtime=preserve_mtime,
            dry_run=args.dry_run,
            backup=args.backup,
        )

        prefix = f"[{i}/{len(mp3s)}] {channel_name} :: {mp3.name}"
        if success:
            ok_count += 1
            print(f"{prefix}\n  ✅ {msg}\n")
        else:
            fail_count += 1
            print(f"{prefix}\n  ❌ {msg}\n")

    print(f"Resumen: OK={ok_count} | FAIL={fail_count}")
    if fail_count:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
