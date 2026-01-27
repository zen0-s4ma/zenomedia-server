#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List
import requests

# =========================
# CONFIGURACIÓN (EDITA AQUÍ)
# =========================
SRC_ROOT = Path(r"E:\YouTube\Videos")
YT_API_KEY = "AIzaSyA-9iJS0bDWXpCGPqvM4NzM-9EoBInli4A"
# =========================

YOUTUBE_API = "https://www.googleapis.com/youtube/v3/channels"


def chunked(items: List[str], n: int) -> List[List[str]]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def yt_channels_titles(api_key: str, channel_ids: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for batch in chunked(channel_ids, 50):
        params = {
            "part": "snippet",
            "id": ",".join(batch),
            "key": api_key,
            "fields": "items(id,snippet(title))",
            "maxResults": 50,
        }
        r = requests.get(YOUTUBE_API, params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"YouTube API error {r.status_code}: {r.text[:500]}")
        data = r.json()
        for it in data.get("items", []):
            out[it["id"]] = it["snippet"]["title"]
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    p.add_argument("--src-root", default=str(SRC_ROOT),
                   help="Carpeta raíz donde TA guarda los canales")
    p.add_argument("--yt-api-key", default=YT_API_KEY,
                   help="API key de YouTube")
    args = p.parse_args()

    src_root = Path(args.src_root)
    api_key = args.yt_api_key

    if not api_key or api_key == "PON_AQUI_TU_API_KEY":
        print("Falta API key. Edita YT_API_KEY arriba o pásala con --yt-api-key.")
        return 2

    if not src_root.exists():
        print(f"No existe: {src_root}")
        return 2

    channel_dirs = sorted([d for d in src_root.iterdir() if d.is_dir()])
    channel_ids = [d.name for d in channel_dirs]

    titles = yt_channels_titles(api_key, channel_ids)

    for idx, d in enumerate(channel_dirs, start=1):
        cid = d.name
        title = titles.get(cid, "<no encontrado en API (privado/eliminado?)>")
        print(f"{idx}) {title}   ({cid})   -> {d}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
