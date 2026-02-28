#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Tuple
from datetime import datetime


# =========================
# CONFIG: rutas a escanear
# =========================
ROOT_PATHS = [
    r"F:\Anime",
    r"E:\Anime",
]

# Fichero de salida (se guarda junto a este script)
OUTPUT_FILENAME = "inventario_anime.txt"

# Ignora carpetas típicas (edita a gusto)
SKIP_DIR_NAMES = {
    "$RECYCLE.BIN",
    "System Volume Information",
}

SHOW_FILE_SIZES = True


def bytes_human(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    val = float(n)
    i = 0
    while val >= 1024 and i < len(units) - 1:
        val /= 1024.0
        i += 1
    if i == 0:
        return f"{int(val)} {units[i]}"
    return f"{val:.2f} {units[i]}"


def print_tree(root: Path, skip_dir_names: set[str], out_lines: List[str], show_file_sizes: bool = True) -> None:
    out_lines.append("=" * 80)
    out_lines.append(f"RAÍZ: {root}")
    out_lines.append("=" * 80)

    if not root.exists():
        out_lines.append(f"[!] No existe: {root}")
        out_lines.append("")
        return

    errors: List[Tuple[Path, str]] = []
    total_files = 0
    total_dirs = 0
    total_bytes = 0

    def onerror(e: OSError):
        errors.append((Path(getattr(e, "filename", str(root))), f"{type(e).__name__}: {e}"))

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=onerror, followlinks=False):
        dpath = Path(dirpath)

        # filtra dirs ignoradas
        dirnames[:] = [d for d in dirnames if d not in skip_dir_names]

        # ordena dirs y files
        dirnames.sort(key=lambda s: s.casefold())
        filenames.sort(key=lambda s: s.casefold())

        # calcula profundidad (para sangría)
        rel = dpath.relative_to(root)
        depth = 0 if str(rel) == "." else len(rel.parts)
        indent = "  " * depth

        # imprime carpeta actual (excepto root)
        if depth > 0:
            out_lines.append(f"{indent}📁 {dpath.name}")
            total_dirs += 1

        # imprime archivos del nivel
        for fn in filenames:
            fpath = dpath / fn
            size = 0
            if show_file_sizes:
                try:
                    size = fpath.stat().st_size
                    total_bytes += size
                except OSError as e:
                    errors.append((fpath, f"{type(e).__name__}: {e}"))
                    size = 0

            total_files += 1
            if show_file_sizes:
                out_lines.append(f"{indent}  📄 {fn}  ({bytes_human(size)})")
            else:
                out_lines.append(f"{indent}  📄 {fn}")

    out_lines.append("-" * 80)
    out_lines.append(f"RESUMEN: {total_dirs} carpetas | {total_files} archivos | {bytes_human(total_bytes)} en archivos")

    if errors:
        out_lines.append("-" * 80)
        out_lines.append("ERRORES (no detienen el script):")
        for p, msg in errors:
            out_lines.append(f"  [!] {p} -> {msg}")

    out_lines.append("")


def main(paths: Iterable[str]) -> int:
    script_dir = Path(__file__).resolve().parent
    out_path = script_dir / OUTPUT_FILENAME

    lines: List[str] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"Inventario generado: {now}")
    lines.append(f"Script: {Path(__file__).name}")
    lines.append(f"Salida: {out_path}")
    lines.append("")

    for p in paths:
        print_tree(Path(p), SKIP_DIR_NAMES, lines, show_file_sizes=SHOW_FILE_SIZES)

    # Guardar a fichero (UTF-8)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    # Y también mostrar por pantalla
    print("\n".join(lines))
    print(f"\n[OK] Guardado en: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(ROOT_PATHS))