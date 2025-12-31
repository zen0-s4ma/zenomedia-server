import os
import re
import csv
import unicodedata
from typing import List, Dict, Any

# =========================
# CONFIG
# =========================
INPUT_M3U = r"E:\Docker_folders\_iptv\solo_canales_filtrado_por_urls.m3u"

# Si True: ignora TODOS los filtros y saca el CSV del M3U completo (y el M3U ordenado)
EXPORT_ALL = False

# Coletilla/sufijo para los ficheros resultantes
SUFFIX = "_REVIEW"  # ej: "_ES", "_PPV", "_TODO", "_CUSTOM"

# 1) Prefijos permitidos (si [] -> NO filtra por prefijo)
INCLUDE_PREFIXES: List[str] = []  # ej: ["ES", "PPV"] o [] para dejar pasar todos

# 2) Debe contener (opcional). Se busca en tvg-name Y group-title
INCLUDE_KEYWORDS: List[str] = []

# 3) NO debe contener (prioridad máxima). Se busca en tvg-name Y group-title
EXCLUDE_KEYWORDS: List[str] = [] # ej: ["FHD", "HEVC", "4K", "✦"]

# Outputs (misma ruta)
BASE = os.path.splitext(INPUT_M3U)[0]
OUTPUT_M3U = BASE + f"{SUFFIX}.m3u"
OUTPUT_CSV = BASE + f"{SUFFIX}.csv"

# =========================
# REGEX
# =========================
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')  # tvg-name="..." group-title="..." etc.


def norm(s: str) -> str:
    """Normaliza unicode (4Ｋ -> 4K, ｜ -> |, NBSP -> espacio, etc.) y casefold."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00A0", " ")  # NBSP
    return s.casefold()


def parse_extinf(extinf_line: str) -> Dict[str, str]:
    left, sep, right = extinf_line.partition(",")
    display_name = right.strip() if sep else ""
    attrs = dict(ATTR_RE.findall(left))
    return {
        "tvg_name": attrs.get("tvg-name", "").strip(),
        "group_title": attrs.get("group-title", "").strip(),
        "display_name": display_name,
    }


# Pre-normalizamos listas de filtros
INCLUDE_PREFIXES_N = [norm(p).strip() for p in INCLUDE_PREFIXES if p and p.strip()]
INCLUDE_KEYWORDS_N = [norm(k).strip() for k in INCLUDE_KEYWORDS if k and k.strip()]
EXCLUDE_KEYWORDS_N = [norm(k).strip() for k in EXCLUDE_KEYWORDS if k and k.strip()]


def has_allowed_prefix(name: str) -> bool:
    """
    Comprueba prefijo tipo ES|..., PPV|..., ES ... con texto ya normalizado.
    Si INCLUDE_PREFIXES está vacío -> no filtra por prefijo (pasa todo).
    """
    if not INCLUDE_PREFIXES_N:
        return True

    n = norm(name).strip()
    for p in INCLUDE_PREFIXES_N:
        if n == p or n.startswith(p + "|") or n.startswith(p + " "):
            return True
    return False


def should_keep(extinf_line: str) -> bool:
    if EXPORT_ALL:
        return True

    info = parse_extinf(extinf_line)
    tvg_name = info["tvg_name"]
    group_title = info["group_title"]
    display_name = info["display_name"]

    # Prefijo lo validamos contra tvg-name (o display_name si falta)
    name_for_prefix = tvg_name or display_name
    if not name_for_prefix:
        return False

    # A) Prefijo (si la lista no está vacía)
    if not has_allowed_prefix(name_for_prefix):
        return False

    # B) Include/exclude se miran en tvg-name + group-title
    hay = norm(f"{tvg_name} {group_title}")

    # INCLUDE: si hay lista, debe contener AL MENOS UNA
    if INCLUDE_KEYWORDS_N and not any(k in hay for k in INCLUDE_KEYWORDS_N):
        return False

    # EXCLUDE: prioridad máxima (si contiene cualquiera, fuera)
    if EXCLUDE_KEYWORDS_N and any(k in hay for k in EXCLUDE_KEYWORDS_N):
        return False

    return True


def split_pipe(s: str) -> List[str]:
    """
    Parte por '|' de forma robusta (normaliza unicode y quita espacios).
    Ej: 'ES| CANAL ODISEA 4K' -> ['ES', 'CANAL ODISEA 4K']
    """
    s_nfkc = unicodedata.normalize("NFKC", s or "").replace("\u00A0", " ")
    return [p.strip() for p in s_nfkc.split("|")]


def main():
    with open(INPUT_M3U, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    header: List[str] = []
    kept_blocks: List[Dict[str, Any]] = []

    current_lines: List[str] = []
    current_keep = False
    current_sort_name = ""
    current_group_title = ""
    current_url = ""
    in_entries = False

    def flush_block():
        nonlocal current_lines, current_keep, current_sort_name, current_group_title, current_url
        if current_lines and current_keep:
            kept_blocks.append({
                "sort_name": current_sort_name,
                "group_title": current_group_title,
                "url": current_url,
                "lines": current_lines
            })
        current_lines = []
        current_keep = False
        current_sort_name = ""
        current_group_title = ""
        current_url = ""

    for line in lines:
        stripped = line.strip()

        if line.startswith("#EXTINF:"):
            in_entries = True
            flush_block()

            current_lines = [line]
            info = parse_extinf(line)
            current_sort_name = (info["tvg_name"] or info["display_name"]).strip()
            current_group_title = info["group_title"].strip()
            current_keep = should_keep(line)
            current_url = ""  # se rellenará cuando aparezca la URL
        else:
            if not in_entries:
                header.append(line)
            else:
                current_lines.append(line)
                # Capturamos la primera línea "no comentario" como URL del stream
                if current_keep and not current_url and stripped and not stripped.startswith("#"):
                    current_url = stripped

    flush_block()

    # ORDENAR por tvg-name (case-insensitive) usando normalización robusta
    kept_blocks.sort(key=lambda b: (norm(b["sort_name"]), b["sort_name"]))

    # 1) Guardar M3U ordenado (si EXPORT_ALL=True será el M3U completo pero ordenado)
    with open(OUTPUT_M3U, "w", encoding="utf-8", newline="") as out:
        out.writelines(header)
        for block in kept_blocks:
            out.writelines(block["lines"])

    # ===== CSV: columnas partidas por "|" =====
    # Calculamos cuántas columnas máximas necesitaremos para tvg-name y group-title
    max_tvg_parts = 0
    max_group_parts = 0
    for block in kept_blocks:
        max_tvg_parts = max(max_tvg_parts, len(split_pipe(block["sort_name"])))
        max_group_parts = max(max_group_parts, len(split_pipe(block["group_title"])))

    tvg_headers = [f"tvg_part_{i+1}" for i in range(max_tvg_parts)]
    group_headers = [f"group_part_{i+1}" for i in range(max_group_parts)]

    # 2) CSV (ordenado por tvg-name): tvg_part_* ; group_part_* ; url
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as out_csv:
        writer = csv.writer(out_csv, delimiter=";")
        writer.writerow(tvg_headers + group_headers + ["url"])

        for block in kept_blocks:
            tvg_parts = split_pipe(block["sort_name"])
            group_parts = split_pipe(block["group_title"])

            # Rellenar con "" hasta el tamaño máximo para mantener columnas fijas
            tvg_parts += [""] * (max_tvg_parts - len(tvg_parts))
            group_parts += [""] * (max_group_parts - len(group_parts))

            writer.writerow(tvg_parts + group_parts + [block["url"]])

    print(
        "OK: generado\n"
        f"{OUTPUT_M3U}\n"
        f"{OUTPUT_CSV}\n"
        f"Entradas: {len(kept_blocks)}\n"
        f"EXPORT_ALL={EXPORT_ALL} | SUFFIX={SUFFIX}"
    )

if __name__ == "__main__":
    main()
