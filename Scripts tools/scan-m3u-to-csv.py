import os
import re
import csv

# ========= CONFIG =========
BASE_DIR = r"E:\Docker_folders\dispatcharr\iptv"
INPUT_FILE = "canales_seleccionados_FINAL_HD.m3u"
OUTPUT_FILE = "canales_seleccionados_FINAL_HD.csv"
# ==========================

# Regex para capturar atributos dentro de #EXTINF
RE_TVG_ID = re.compile(r'tvg-id="([^"]*)"')
RE_TVG_NAME = re.compile(r'tvg-name="([^"]*)"')
RE_GROUP = re.compile(r'group-title="([^"]*)"')

def extract_attr(pattern, text):
    m = pattern.search(text)
    return m.group(1).strip() if m else ""

def main():
    in_path = os.path.join(BASE_DIR, INPUT_FILE)
    out_path = os.path.join(BASE_DIR, OUTPUT_FILE)

    if not os.path.isfile(in_path):
        raise FileNotFoundError(f"No existe el archivo de entrada: {in_path}")

    rows = []
    seq = 1

    # M3U suele venir en UTF-8, pero a veces es Latin-1/Windows-1252.
    # Intentamos UTF-8 y, si falla, caemos a cp1252.
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(in_path, "r", encoding=encoding, errors="strict") as f:
                lines = f.read().splitlines()
            break
        except UnicodeDecodeError:
            lines = None
    if lines is None:
        # Último recurso: leer reemplazando caracteres problemáticos
        with open(in_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()

    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            tvg_id = extract_attr(RE_TVG_ID, line)
            tvg_name = extract_attr(RE_TVG_NAME, line)
            group_title = extract_attr(RE_GROUP, line)

            rows.append([seq, tvg_id, tvg_name, group_title])
            seq += 1

    # Escribimos CSV con separador ;
    with open(out_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["numero", "tvg-id", "tvg-name", "group-title"])  # cabecera
        writer.writerows(rows)

    print(f"OK: {len(rows)} canales exportados a: {out_path}")

if __name__ == "__main__":
    main()
