import csv
import os
import re

# =========================
# CONFIG
# =========================
FILE_PATH = r"E:\Docker_folders\tvheadend\iptv\peliculas_series.txt"
OUTPUT_CSV = os.path.splitext(FILE_PATH)[0] + "_listado.csv"

ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
SE_RE = re.compile(r'\bS(\d{1,2})\s*E(\d{1,2})\b', re.IGNORECASE)
SE_RE2 = re.compile(r'\bS(\d{1,2})E(\d{1,2})\b', re.IGNORECASE)


def parse_extinf_line(line: str):
    left, sep, right = line.partition(",")
    display_name = right.strip() if sep else ""
    attrs = dict(ATTR_RE.findall(left))
    return attrs, display_name


def detect_type_from_url(url: str, group_title: str = "") -> str:
    u = url.lower()
    g = (group_title or "").lower()

    if "/series/" in u:
        return "series"
    if "/movie/" in u:
        return "movie"

    # fallback por group-title si la URL no ayuda
    if "series" in g:
        return "series"
    if "movie" in g or "movies" in g:
        return "movie"

    return "unknown"


def extract_season_episode(text: str):
    if not text:
        return "", ""
    m = SE_RE.search(text) or SE_RE2.search(text)
    if not m:
        return "", ""
    return m.group(1).zfill(2), m.group(2).zfill(2)


def main():
    rows = []
    pending = None

    with open(FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue

            if line.startswith("#EXTINF:"):
                attrs, display_name = parse_extinf_line(line)
                pending = {
                    "tvg_id": attrs.get("tvg-id", ""),
                    "tvg_name": attrs.get("tvg-name", ""),
                    "tvg_logo": attrs.get("tvg-logo", ""),
                    "group_title": attrs.get("group-title", ""),
                    "display_name": display_name,
                }
                continue

            # La URL suele venir justo después del EXTINF
            if pending and not line.startswith("#"):
                url = line
                content_type = detect_type_from_url(url, pending.get("group_title", ""))

                name = pending["display_name"] or pending["tvg_name"]

                season, episode = ("", "")
                if content_type == "series":
                    season, episode = extract_season_episode(pending["tvg_name"] or name)

                rows.append({
                    "type": content_type,
                    "name": name,
                    "season": season,
                    "episode": episode,
                    "group_title": pending["group_title"],
                    "tvg_name": pending["tvg_name"],
                    "tvg_logo": pending["tvg_logo"],
                    "url": url,
                })

                pending = None

    fieldnames = ["type", "name", "season", "episode", "group_title", "tvg_name", "tvg_logo", "url"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as out:
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"OK: {len(rows)} entradas exportadas a:\n{OUTPUT_CSV}")


if __name__ == "__main__":
    main()
