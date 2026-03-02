import argparse
import re
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm"}

# Quita prefijo tmdb_12345__ (1 o más _)
PREFIX_RE = re.compile(r"^tmdb_\d+_+")

# Detecta si ya empieza por "Trailer" (con separadores comunes)
TRAILER_PREFIX_RE = re.compile(r"^trailer\s*[-_.:\s]*", re.IGNORECASE)

MULTISPACE_RE = re.compile(r"\s+")

def clean_stem(stem: str) -> str:
    # 1) quitar prefijo tmdb_12345__ (o tmdb_12345_)
    s = PREFIX_RE.sub("", stem)

    # 2) reemplazar separadores por espacios
    s = s.replace("_", " ").replace("-", " ").replace(".", " ")

    # 3) compactar espacios y recortar
    s = MULTISPACE_RE.sub(" ", s).strip()

    return s

def ensure_trailer_prefix(title: str) -> str:
    # Si ya empieza por Trailer (con variaciones), lo normalizamos a "Trailer - "
    if TRAILER_PREFIX_RE.match(title):
        title = TRAILER_PREFIX_RE.sub("", title).strip()
    return f"Trailer - {title}" if title else "Trailer"

def unique_target_path(target: Path) -> Path:
    """Si target existe, añade ' (1)', ' (2)', etc."""
    if not target.exists():
        return target

    parent = target.parent
    stem = target.stem
    suffix = target.suffix

    i = 1
    while True:
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1

def iter_files(root: Path, recursive: bool):
    if recursive:
        for p in root.rglob("*"):
            if p.is_file():
                yield p
    else:
        for p in root.glob("*"):
            if p.is_file():
                yield p

def main():
    parser = argparse.ArgumentParser(
        description="Limpia nombres de trailers: quita 'tmdb_####__', cambia _ - . por espacios y añade 'Trailer - ' al inicio."
    )
    parser.add_argument("--path", default=r"E:\_Trailers", help="Ruta de la carpeta (por defecto: E:\\_Trailers)")
    parser.add_argument("--apply", action="store_true", help="Aplicar renombrado (si no, solo muestra cambios)")
    parser.add_argument("--recursive", action="store_true", help="Procesar también subcarpetas")
    parser.add_argument("--all-files", action="store_true", help="Procesar todos los archivos (no solo vídeo)")
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        raise SystemExit(f"No existe la ruta: {root}")

    planned = []
    skipped = 0

    for f in iter_files(root, args.recursive):
        if not args.all_files and f.suffix.lower() not in VIDEO_EXTS:
            continue

        cleaned = clean_stem(f.stem)
        if not cleaned:
            skipped += 1
            continue

        final_title = ensure_trailer_prefix(cleaned)

        target = f.with_name(final_title + f.suffix)
        if target.name == f.name:
            continue

        target = unique_target_path(target)
        planned.append((f, target))

    if not planned:
        print("No hay cambios que aplicar.")
        return

    print(f"Archivos a renombrar: {len(planned)}")
    if skipped:
        print(f"Saltados por quedar vacíos tras limpiar: {skipped}")
    print()

    for src, dst in planned:
        print(f"- {src.name}  ->  {dst.name}")

    if not args.apply:
        print("\n(DRY-RUN) No se ha renombrado nada. Usa --apply para aplicar cambios.")
        return

    print("\nAplicando cambios...\n")
    ok = 0
    for src, dst in planned:
        try:
            src.rename(dst)
            ok += 1
        except Exception as e:
            print(f"ERROR: {src.name} -> {dst.name} | {e}")

    print(f"\nRenombrados correctamente: {ok}/{len(planned)}")

if __name__ == "__main__":
    main()