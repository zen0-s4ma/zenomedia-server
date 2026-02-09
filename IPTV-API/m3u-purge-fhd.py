#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import List, Tuple

# ============================================================
# CONFIG (EDITA SOLO AQUÍ)
# ============================================================

BASE_DIR = Path(r"E:\Docker_folders\dispatcharr\iptv")
INPUT_FILENAME = "Canales Seleccionados.m3u"

# Si True, sobrescribe el archivo original (crea .bak)
INPLACE = False

# Tokens a eliminar si aparecen en tvg-name="..." o group-title="..."
TOKENS = ["[BK]", "FHD", "HEVC", "4K", "✦"]

# ============================================================
# LÓGICA
# ============================================================

TVG_NAME_RE = re.compile(r'tvg-name="([^"]*)"', re.IGNORECASE)
GROUP_TITLE_RE = re.compile(r'group-title="([^"]*)"', re.IGNORECASE)


def _contains_any_token(value: str, tokens: List[str]) -> bool:
    v = value.upper()
    for t in tokens:
        if t.upper() in v:
            return True
    return False


def should_drop(extinf_line: str, tokens: List[str]) -> Tuple[bool, str, str]:
    """
    Returns (drop?, tvg_name_or_empty, group_title_or_empty)
    Checks tvg-name and group-title attributes only.
    """
    tvg_name = ""
    group_title = ""

    m1 = TVG_NAME_RE.search(extinf_line)
    if m1:
        tvg_name = m1.group(1)

    m2 = GROUP_TITLE_RE.search(extinf_line)
    if m2:
        group_title = m2.group(1)

    drop = False

    if tvg_name and _contains_any_token(tvg_name, tokens):
        drop = True

    if group_title and _contains_any_token(group_title, tokens):
        drop = True

    return drop, tvg_name, group_title


def purge_m3u(input_path: Path, output_path: Path, tokens: List[str]) -> None:
    # Read as UTF-8 (common for M3U); fall back if needed.
    try:
        text = input_path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        text = input_path.read_text(encoding="utf-8", errors="replace")

    lines = text.splitlines(keepends=True)

    kept: List[str] = []
    removed_count = 0
    kept_count = 0

    i = 0

    # Preserve any initial header/comments before first #EXTINF
    while i < len(lines) and not lines[i].lstrip().startswith("#EXTINF"):
        kept.append(lines[i])
        i += 1

    # Process entries: #EXTINF + following lines until next #EXTINF or EOF
    while i < len(lines):
        if not lines[i].lstrip().startswith("#EXTINF"):
            # Unexpected stray line; keep it
            kept.append(lines[i])
            i += 1
            continue

        extinf_line = lines[i]
        i += 1

        # Collect the rest of this entry
        entry_lines = [extinf_line]
        while i < len(lines) and not lines[i].lstrip().startswith("#EXTINF"):
            entry_lines.append(lines[i])
            i += 1

        drop, _tvg_name, _group_title = should_drop(extinf_line, tokens)
        if drop:
            removed_count += 1
        else:
            kept.extend(entry_lines)
            kept_count += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(kept), encoding="utf-8")

    print(f"Archivo entrada     : {input_path}")
    print(f"Archivo salida      : {output_path}")
    print(f"Tokens filtrado     : {tokens}")
    print(f"Canales mantenidos  : {kept_count}")
    print(f"Canales eliminados  : {removed_count}")


def main() -> None:
    in_path = BASE_DIR / INPUT_FILENAME

    if not in_path.exists():
        raise SystemExit(f"No existe el archivo de entrada: {in_path}")

    if INPLACE:
        bak_path = in_path.with_suffix(in_path.suffix + ".bak")
        shutil.copy2(in_path, bak_path)
        out_path = in_path
    else:
        out_path = in_path.with_name(in_path.stem + ".purged" + in_path.suffix)

    purge_m3u(in_path, out_path, TOKENS)


if __name__ == "__main__":
    main()
