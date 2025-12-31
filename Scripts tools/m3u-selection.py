import os

# =========================
# CONFIG
# =========================
INPUT_M3U = r"E:\Docker_folders\_iptv\solo_canales.m3u"
URL_LIST_TXT = r"E:\Docker_folders\_iptv\canales_seleccionados_SD.txt"

# Salida en la misma carpeta
OUTPUT_M3U = os.path.splitext(INPUT_M3U)[0] + "_canales_seleccionados_SD.m3u"


def load_urls(path: str) -> set[str]:
    urls = set()
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            u = line.strip()
            if not u or u.startswith("#"):
                continue
            urls.add(u)
    return urls


def is_url_line(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith("#")


def main():
    allowed_urls = load_urls(URL_LIST_TXT)

    with open(INPUT_M3U, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    header = []
    kept_entries = []

    current_entry = []
    current_keep = False
    in_entries = False
    first_url_seen = False

    def flush_entry():
        nonlocal current_entry, current_keep, first_url_seen
        if current_entry and current_keep:
            kept_entries.extend(current_entry)
        current_entry = []
        current_keep = False
        first_url_seen = False

    for line in lines:
        if line.startswith("#EXTINF:"):
            in_entries = True
            flush_entry()
            current_entry = [line]
            # el keep se decidirá cuando veamos la URL
            continue

        if not in_entries:
            header.append(line)
            continue

        # dentro de una entrada
        current_entry.append(line)

        # la primera línea no comentada se considera la URL principal del stream
        if not first_url_seen and is_url_line(line):
            url = line.strip()
            current_keep = url in allowed_urls
            first_url_seen = True

    flush_entry()

    with open(OUTPUT_M3U, "w", encoding="utf-8", newline="") as out:
        out.writelines(header)
        out.writelines(kept_entries)

    print(f"OK: generado\n{OUTPUT_M3U}")

if __name__ == "__main__":
    main()
