#!/usr/bin/env python3
import os
from pathlib import Path
from datetime import datetime

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
except ImportError:
    print("Necesitas instalar Pillow primero:  pip install Pillow")
    raise

# 🔧 CAMBIA ESTAS DOS LÍNEAS SEGÚN LA CARPETA
FOLDER_PATH = r"D:\__________DISCO E Completo\__almacen ordenado\_Fotos Ordenadas\_____PPENDIENTE ORDENAR\X"
NAME_PREFIX = "XXX"        # "CAM", "SCREENSHOT", "VIDEO", "WHATSAPPIMAGE", "WHATSAPPSENT", "AUDIO", "AUDIOSENT", "WHATSAPPVIDEO", "WHATSAPPVIDEOSENT".


def get_exif_datetime(path: Path):
    """Devuelve un datetime a partir del EXIF o None si no hay."""
    try:
        img = Image.open(path)
        exif = img._getexif() or {}
    except Exception:
        return None

    exif_data = {TAGS.get(k, k): v for k, v in exif.items()}

    for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        value = exif_data.get(key)
        if isinstance(value, str):
            try:
                # Formato típico EXIF: 'YYYY:MM:DD HH:MM:SS'
                return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
            except ValueError:
                continue
    return None


def build_new_name(dt: datetime, ext: str) -> str:
    """Genera el nombre PREFIX_YYYYMMDD_HHMMSS.ext"""
    return f"{NAME_PREFIX}_{dt:%Y%m%d_%H%M%S}{ext.lower()}"


def unique_name(folder: Path, base_name: str) -> str:
    """
    Si ya existe base_name en folder, añade _1, _2, ...
    para evitar sobrescribir.
    """
    candidate = base_name
    stem, ext = os.path.splitext(base_name)
    counter = 1
    while (folder / candidate).exists():
        candidate = f"{stem}_{counter}{ext}"
        counter += 1
    return candidate


def already_good_name(path: Path) -> bool:
    """
    Comprueba si el nombre ya tiene formato PREFIX_YYYYMMDD_HHMMSS.ext
    usando el prefijo configurado en NAME_PREFIX.
    """
    import re
    pattern = rf"^{re.escape(NAME_PREFIX)}_\d{{8}}_\d{{6}}\.(jpe?g)$"
    return re.match(pattern, path.name, re.IGNORECASE) is not None


def rename_photos(folder: Path):
    if not folder.is_dir():
        print(f"La ruta no es una carpeta válida: {folder}")
        return

    jpg_extensions = {
        ".jpg", ".jpeg", ".png", ".3gp", ".mp4", ".bmp", ".mpg", ".mov",
        ".avi", ".3ga", ".aac", ".opus", ".m4a", ".mp3", ".amr"
    }
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in jpg_extensions]

    if not files:
        print("No se han encontrado fotos JPG/JPEG en la carpeta.")
        return

    print(f"Procesando {len(files)} ficheros en: {folder}")
    print(f"Usando prefijo: {NAME_PREFIX}\n")

    for path in sorted(files):
        if already_good_name(path):
            print(f"[SKIP] Ya tiene buen formato: {path.name}")
            continue

        # 1) Intentar EXIF
        dt = get_exif_datetime(path)

        # 2) Fallback: fecha de modificación del fichero
        if dt is None:
            ts = path.stat().st_mtime
            dt = datetime.fromtimestamp(ts)

        new_name = build_new_name(dt, path.suffix)
        new_name = unique_name(folder, new_name)

        new_path = folder / new_name

        print(f"[REN] {path.name}  ->  {new_name}")
        try:
            path.rename(new_path)
        except Exception as e:
            print(f"   ERROR al renombrar {path.name}: {e}")


def main():
    root = Path(FOLDER_PATH)

    if not root.is_dir():
        print(f"La ruta no es una carpeta válida: {root}")
        return

    # 🔁 Recorre FOLDER_PATH y TODAS sus subcarpetas
    for current_root, dirs, files in os.walk(root):
        rename_photos(Path(current_root))


if __name__ == "__main__":
    main()
