#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Convierte imágenes de una carpeta a blanco y negro "bonito".
- Mantiene los originales
- Guarda en una subcarpeta: _BW
- Soporta: .png .jpg .jpeg .webp
Uso:
  python bw_batch.py "D:\Imagenes\biblioteca jellyfin\portadas contenidos\PLANTILLA NUEVA"
"""

import os
import sys
from pathlib import Path

from PIL import Image, ImageOps, ImageEnhance, ImageFilter


# ---------- Ajustes "bonitos" ----------
AUTOCONTRAST_CUTOFF = 1       # recorta 1% de negros/blancos para dar punch
GAMMA = 0.92                  # <1 aclara medios; >1 oscurece medios
CONTRAST = 1.10               # contraste leve
SHARPNESS = 1.20              # claridad leve
UNSHARP_RADIUS = 1.2          # micro-contraste
UNSHARP_PERCENT = 120
UNSHARP_THRESHOLD = 2


SUPPORTED = {".png", ".jpg", ".jpeg", ".webp"}


def apply_gamma(img_l: Image.Image, gamma: float) -> Image.Image:
    # img_l debe estar en modo "L"
    if gamma == 1.0:
        return img_l
    inv = 1.0 / gamma
    lut = [int((i / 255.0) ** inv * 255.0 + 0.5) for i in range(256)]
    return img_l.point(lut)


def bw_nice(im: Image.Image) -> Image.Image:
    # 1) a escala de grises (luminancia)
    g = ImageOps.grayscale(im)

    # 2) autocontraste suave
    g = ImageOps.autocontrast(g, cutoff=AUTOCONTRAST_CUTOFF)

    # 3) gamma suave para medios tonos
    g = apply_gamma(g, GAMMA)

    # 4) contraste leve
    g = ImageEnhance.Contrast(g).enhance(CONTRAST)

    # 5) “claridad” / nitidez suave (sin pasarse)
    g = g.filter(ImageFilter.UnsharpMask(
        radius=UNSHARP_RADIUS,
        percent=UNSHARP_PERCENT,
        threshold=UNSHARP_THRESHOLD
    ))
    g = ImageEnhance.Sharpness(g).enhance(SHARPNESS)

    return g


def main(folder: str) -> int:
    src_dir = Path(folder).expanduser()
    if not src_dir.exists() or not src_dir.is_dir():
        print(f"[ERROR] La ruta no existe o no es carpeta: {src_dir}")
        return 2

    out_dir = src_dir / "_BW"
    out_dir.mkdir(parents=True, exist_ok=True)

    files = [p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED]
    if not files:
        print(f"[INFO] No encontré imágenes compatibles en: {src_dir}")
        return 0

    print(f"[INFO] Carpeta origen: {src_dir}")
    print(f"[INFO] Carpeta salida: {out_dir}")
    print(f"[INFO] Archivos a procesar: {len(files)}\n")

    ok = 0
    for p in files:
        try:
            with Image.open(p) as im:
                # Por si viene con alpha / paleta / etc.
                im = im.convert("RGBA")  # consistente
                bw = bw_nice(im)

                # Guardar: mismo nombre, sufijo _BW, formato según extensión
                out_name = f"{p.stem}_BW{p.suffix.lower()}"
                out_path = out_dir / out_name

                # Para PNG mantiene bien el resultado en L; para JPG, igual.
                save_kwargs = {}
                if p.suffix.lower() in {".jpg", ".jpeg"}:
                    save_kwargs = {"quality": 95, "optimize": True}
                elif p.suffix.lower() == ".png":
                    save_kwargs = {"optimize": True}

                bw.save(out_path, **save_kwargs)

            print(f"[OK] {p.name} -> {out_path.name}")
            ok += 1

        except Exception as e:
            print(f"[FAIL] {p.name}: {e}")

    print(f"\n[HECHO] Convertidas: {ok}/{len(files)}")
    return 0


if __name__ == "__main__":
    # Puedes pasarlo por argumento o editar la variable aquí abajo.
    if len(sys.argv) >= 2:
        folder_path = sys.argv[1]
    else:
        # Cambia esto si quieres usarlo “como variable”
        folder_path = r"D:\Imagenes\biblioteca jellyfin\portadas contenidos\PLANTILLA NUEVA"

    raise SystemExit(main(folder_path))
