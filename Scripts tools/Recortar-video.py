import os
import sys
import subprocess
from pathlib import Path

# =========================
# CONFIGURACIÓN DEL USUARIO
# =========================

BASE_DIR = r"E:\Grabaciones\Universo Valdano Brahim"
INPUT_FILE = "Universo Valdano Brahim_20260125_00050100.ts"  # .ts / .mkv / .mp4

cortar_inicio = True
inicio_h, inicio_m, inicio_s = 0, 38, 53

cortar_fin = True
fin_h, fin_m, fin_s = 1, 29, 12

borrar_original = True  # True => reemplaza el original por el recortado (mismo nombre)
MODO_RAPIDO_COPY = True

# =========================
# IMPLEMENTACIÓN
# =========================

SUPPORTED_EXTS = {".ts", ".mkv", ".mp4"}

def hms_to_seconds(h: int, m: int, s: int) -> int:
    if h < 0 or m < 0 or s < 0:
        raise ValueError("Horas/minutos/segundos no pueden ser negativos.")
    if m >= 60 or s >= 60:
        raise ValueError("Minutos y segundos deben estar en rango 0..59.")
    return h * 3600 + m * 60 + s

def seconds_to_hhmmss(total_seconds: int) -> str:
    h = total_seconds // 3600
    rem = total_seconds % 3600
    m = rem // 60
    s = rem % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def run_ffmpeg(cmd: list[str]) -> int:
    print("\n[FFMPEG CMD]")
    print(" ".join(cmd))
    print()
    p = subprocess.run(cmd)
    return p.returncode

def ensure_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        print("ERROR: No encuentro ffmpeg en el PATH. Instala FFmpeg y asegúrate de que 'ffmpeg' funcione en consola.")
        sys.exit(1)

def resolve_input_path(base_dir: str, input_file: str) -> Path:
    p = Path(input_file)
    if p.is_absolute():
        return p
    return Path(base_dir) / input_file

def build_output_path(input_path: Path) -> Path:
    return input_path.with_name(input_path.stem + "_trim" + input_path.suffix)

def pick_backup_path(input_path: Path) -> Path:
    """
    Devuelve una ruta libre para backup en la misma carpeta.
    Ej: video.mp4.bak, video.mp4.bak1, video.mp4.bak2...
    """
    base = input_path.with_name(input_path.name + ".bak")
    if not base.exists():
        return base
    i = 1
    while True:
        cand = input_path.with_name(input_path.name + f".bak{i}")
        if not cand.exists():
            return cand
        i += 1

def main():
    ensure_ffmpeg()

    input_path = resolve_input_path(BASE_DIR, INPUT_FILE)
    if not input_path.exists():
        print(f"ERROR: No existe el archivo: {input_path}")
        sys.exit(1)

    ext = input_path.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        print(f"AVISO: Extensión {ext} no es {sorted(SUPPORTED_EXTS)}. Intentaré igualmente (FFmpeg decide).")

    start_sec = None
    end_sec = None

    if cortar_inicio:
        start_sec = hms_to_seconds(inicio_h, inicio_m, inicio_s)

    if cortar_fin:
        end_sec = hms_to_seconds(fin_h, fin_m, fin_s)

    if cortar_inicio and cortar_fin and end_sec is not None and start_sec is not None:
        if end_sec <= start_sec:
            print("ERROR: El tiempo de fin debe ser mayor que el tiempo de inicio.")
            sys.exit(1)

    out_path = build_output_path(input_path)
    out_ext = out_path.suffix.lower()

    ffmpeg_base = ["ffmpeg", "-y"]

    def cmd_copy():
        cmd = ffmpeg_base.copy()
        cmd += ["-fflags", "+genpts", "-avoid_negative_ts", "make_zero"]

        if start_sec is not None:
            cmd += ["-ss", seconds_to_hhmmss(start_sec)]

        cmd += ["-i", str(input_path)]

        if end_sec is not None and start_sec is not None:
            duration = end_sec - start_sec
            cmd += ["-t", seconds_to_hhmmss(duration)]
        elif end_sec is not None:
            cmd += ["-to", seconds_to_hhmmss(end_sec)]

        cmd += ["-c", "copy"]

        if out_ext == ".mp4":
            cmd += ["-movflags", "+faststart"]

        cmd += [str(out_path)]
        return cmd

    def cmd_reencode():
        cmd = ffmpeg_base.copy()
        cmd += ["-i", str(input_path)]

        if start_sec is not None:
            cmd += ["-ss", seconds_to_hhmmss(start_sec)]

        if end_sec is not None and start_sec is not None:
            duration = end_sec - start_sec
            cmd += ["-t", seconds_to_hhmmss(duration)]
        elif end_sec is not None:
            cmd += ["-to", seconds_to_hhmmss(end_sec)]

        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
        ]

        if out_ext == ".mp4":
            cmd += ["-movflags", "+faststart"]

        cmd += [str(out_path)]
        return cmd

    if out_path.exists():
        print(f"AVISO: La salida ya existe y se sobrescribirá: {out_path}")

    ok = False
    if MODO_RAPIDO_COPY:
        rc = run_ffmpeg(cmd_copy())
        ok = (rc == 0 and out_path.exists() and out_path.stat().st_size > 0)
        if not ok:
            print("AVISO: El modo copy falló o generó salida inválida. Reintentando recodificando...")
            rc2 = run_ffmpeg(cmd_reencode())
            ok = (rc2 == 0 and out_path.exists() and out_path.stat().st_size > 0)
    else:
        rc = run_ffmpeg(cmd_reencode())
        ok = (rc == 0 and out_path.exists() and out_path.stat().st_size > 0)

    if not ok:
        print("ERROR: No se pudo generar el archivo recortado.")
        sys.exit(1)

    print(f"OK: Archivo recortado generado: {out_path}")

    final_path = out_path

    # ==========
    # CAMBIO: si borrar_original=True, el recortado se queda con el nombre del original
    # ==========
    if borrar_original:
        backup_path = pick_backup_path(input_path)
        print(f"INFO: Reemplazando '{input_path.name}' por el recortado (backup: '{backup_path.name}')")

        # 1) mover original a backup
        try:
            input_path.replace(backup_path)  # rename/move en el mismo disco (rápido)
        except Exception as e:
            print(f"ERROR: No pude crear el backup del original. No se tocará el original. Detalle: {e}")
            print(f"El recortado se queda como: {out_path}")
            sys.exit(1)

        # 2) mover recortado al nombre original
        try:
            out_path.replace(input_path)
            final_path = input_path
        except Exception as e:
            print(f"ERROR: No pude renombrar el recortado al nombre original. Detalle: {e}")

            # Intentar restaurar original
            try:
                if not input_path.exists() and backup_path.exists():
                    backup_path.replace(input_path)
                    print("OK: Original restaurado desde el backup.")
            except Exception as e2:
                print(f"CRÍTICO: No pude restaurar el original desde el backup ({backup_path}). Detalle: {e2}")

            print(f"El recortado sigue en: {out_path}")
            sys.exit(1)

        # 3) borrar backup
        try:
            backup_path.unlink()
            print(f"OK: Backup borrado: {backup_path.name}")
        except Exception as e:
            print(f"AVISO: No pude borrar el backup ({backup_path}). Puedes borrarlo manualmente. Detalle: {e}")

        print(f"OK: Archivo final (mismo nombre que el original): {final_path}")
    else:
        print("Original conservado (borrar_original=False).")
        print(f"OK: Archivo final: {final_path}")

if __name__ == "__main__":
    main()
