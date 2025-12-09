import subprocess
from pathlib import Path
import imageio_ffmpeg  # <- igual que en el segundo script

def unir_videos_carpeta(carpeta, salida="salida_unida.mkv", extension=".mkv"):
    """
    Une videos numerados (1..N) en una carpeta en un solo archivo MKV.
    Usa ffmpeg (concat demuxer) sin recodificar (-c copy).

    carpeta: ruta a la carpeta con los vídeos
    salida: nombre del archivo de salida (dentro de la misma carpeta)
    extension: extensión de los vídeos de entrada ('.mkv', '.mp4', etc.)
    """
    carpeta = Path(carpeta)

    if not carpeta.is_dir():
        print(f"ERROR: {carpeta} no es una carpeta válida.")
        return

    # Buscar archivos 1.ext, 2.ext, 3.ext ... ordenados
    archivos = []
    for i in range(1, 1000):  # máximo 999 por si acaso
        f = carpeta / f"{i}{extension}"
        if f.exists():
            archivos.append(f)
        else:
            # en cuanto falte uno, asumimos que ya no hay más
            break

    if not archivos:
        print("No se encontraron archivos numerados (1,2,3,...) en la carpeta.")
        return

    print("Archivos encontrados y ordenados:")
    for f in archivos:
        print("  ", f.name)

    # Intentar encontrar ffmpeg igual que en el segundo script
    try:
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    except RuntimeError as e:
        print("ERROR: no se ha encontrado ningún ffmpeg usable.")
        print("Detalle:", e)
        return

    print(f"\nUsando ffmpeg en: {ffmpeg_path}\n")

    # Crear archivo temporal de lista para ffmpeg
    lista_path = carpeta / "lista_ffmpeg.txt"
    with open(lista_path, "w", encoding="utf-8") as lista:
        for f in archivos:
            # ffmpeg concat demuxer: hay que usar esta sintaxis
            # usamos as_posix() para que las rutas lleven / en vez de \\
            lista.write(f"file '{f.as_posix()}'\n")

    salida_path = carpeta / salida

    # Comando ffmpeg usando concat demuxer
    comando = [
        ffmpeg_path,      # <- ruta absoluta de ffmpeg
        "-f", "concat",
        "-safe", "0",
        "-i", str(lista_path),
        "-c", "copy",     # no recodificar (rápido)
        str(salida_path)
    ]

    print("Ejecutando:", " ".join(comando), "\n")

    resultado = subprocess.run(
        comando,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    if resultado.returncode == 0:
        print(f"✔ Vídeo creado correctamente: {salida_path}")
    else:
        print("❌ Error al ejecutar ffmpeg. Salida completa:\n")
        print(resultado.stdout)

    # Opcional: borrar la lista temporal
    try:
        lista_path.unlink()
    except Exception:
        pass


if __name__ == "__main__":
    # Cambia esta ruta y extensión a lo que uses tú
    carpeta_videos = r"E:\zTemp-mp4-mkv"
    unir_videos_carpeta(
        carpeta=carpeta_videos,
        salida="LoL Worlds 2022 - Final - T1 VS DRX.mkv",
        extension=".mkv"        # o ".mp4", etc.
    )
