import subprocess
from pathlib import Path
import imageio_ffmpeg  # <- nuevo

# Ruta fija donde están los .mp4
RUTA_CARPETA = r"E:\zTemp-mp4-mkv"

def convertir_carpeta(ruta_carpeta: str):
    carpeta = Path(ruta_carpeta)

    if not carpeta.is_dir():
        print(f"ERROR: {carpeta} no es una carpeta válida.")
        return

    # Intentamos localizar un ffmpeg usable
    try:
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    except RuntimeError as e:
        print("ERROR: no se ha encontrado ningún ffmpeg usable.")
        print("Detalle:", e)
        return

    mp4_files = list(carpeta.glob("*.mp4"))
    if not mp4_files:
        print("No se han encontrado archivos .mp4 en la carpeta.")
        return

    print(f"Se han encontrado {len(mp4_files)} archivos .mp4 en {carpeta}\n")
    print(f"Usando ffmpeg en: {ffmpeg_path}\n")

    for mp4 in mp4_files:
        mkv = mp4.with_suffix(".mkv")

        if mkv.exists():
            print(f"[SALTANDO] {mkv.name} ya existe.")
            continue

        print(f"[CONVIRTIENDO] {mp4.name} -> {mkv.name}")

        cmd = [
            ffmpeg_path,   # <- ruta absoluta encontrada por imageio-ffmpeg
            "-y",
            "-i", str(mp4),
            "-c", "copy",
            str(mkv),
        ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        if result.returncode != 0:
            print(f"  [ERROR] Falló la conversión de {mp4.name}")
            print("  Salida de ffmpeg:")
            print(result.stdout)
        else:
            print(f"  [OK] {mkv.name} creado correctamente.\n")

if __name__ == "__main__":
    convertir_carpeta(RUTA_CARPETA)
