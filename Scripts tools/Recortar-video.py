import os
import sys
import subprocess
from pathlib import Path

# =========================
# CONFIGURACIÓN DEL USUARIO
# =========================

# Carpeta base (variable pedida)
BASE_DIR = r"E:\zMkvConverter"

# Archivo de entrada (solo nombre o ruta completa; si es solo nombre, se busca dentro de BASE_DIR)
INPUT_TS = "NCAA Football Bowl Games_20260120_17552025.ts"  # <-- cambia esto

# Control de cortes
cortar_inicio = True
inicio_h, inicio_m, inicio_s = 0, 9, 47

cortar_fin = True
fin_h, fin_m, fin_s = 2, 36, 1

# Borrar original al terminar
borrar_original = False  # True => borra el .ts original si todo sale bien

# Si quieres intentar corte rápido sin recodificar:
# - copy suele ser más rápido pero puede no ser preciso al frame exacto y a veces da problemas con TS.
# - si falla, el script reintenta recodificando.
MODO_RAPIDO_COPY = True

# =========================
# IMPLEMENTACIÓN
# =========================

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
    # Muestra el comando para depurar
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

def resolve_input_path(base_dir: str, input_ts: str) -> Path:
    p = Path(input_ts)
    if p.is_absolute():
        return p
    return Path(base_dir) / input_ts

def build_output_path(input_path: Path) -> Path:
    # Genera: nombre_trim.ts en la misma carpeta del input
    return input_path.with_name(input_path.stem + "_trim.ts")

def main():
    ensure_ffmpeg()

    input_path = resolve_input_path(BASE_DIR, INPUT_TS)
    if not input_path.exists():
        print(f"ERROR: No existe el archivo: {input_path}")
        sys.exit(1)

    if input_path.suffix.lower() != ".ts":
        print(f"AVISO: El archivo no es .ts (es {input_path.suffix}). Intentaré igualmente.")

    start_sec = None
    end_sec = None

    if cortar_inicio:
        start_sec = hms_to_seconds(inicio_h, inicio_m, inicio_s)

    if cortar_fin:
        end_sec = hms_to_seconds(fin_h, fin_m, fin_s)

    # Validaciones de coherencia
    if cortar_inicio and cortar_fin and end_sec is not None and start_sec is not None:
        if end_sec <= start_sec:
            print("ERROR: El tiempo de fin debe ser mayor que el tiempo de inicio.")
            sys.exit(1)

    out_path = build_output_path(input_path)

    # Construcción de argumentos
    ffmpeg_base = ["ffmpeg", "-y"]  # -y sobrescribe salida si existe

    # Nota: para mayor precisión, normalmente conviene poner -ss después de -i (más lento).
    # Para copy rápido, suele ir antes de -i (más rápido). Aquí hacemos:
    # - 1er intento (si MODO_RAPIDO_COPY): copy rápido
    # - si falla: reintento recodificando (más compatible)
    def cmd_copy():
        cmd = ffmpeg_base.copy()
        if start_sec is not None:
            cmd += ["-ss", seconds_to_hhmmss(start_sec)]
        cmd += ["-i", str(input_path)]
        if end_sec is not None and start_sec is not None:
            duration = end_sec - start_sec
            cmd += ["-t", seconds_to_hhmmss(duration)]
        elif end_sec is not None:
            # si NO hay inicio, entonces el fin sí puede ir como "to" tal cual
            cmd += ["-to", seconds_to_hhmmss(end_sec)]
        cmd += ["-c", "copy", str(out_path)]
        return cmd

    def cmd_reencode():
        cmd = ffmpeg_base.copy()
        # Para precisión: -ss después de -i, y -t (duración) en lugar de -to (fin absoluto) si hay ambos
        cmd += ["-i", str(input_path)]
        if start_sec is not None:
            cmd += ["-ss", seconds_to_hhmmss(start_sec)]
        if end_sec is not None and start_sec is not None:
            duration = end_sec - start_sec
            cmd += ["-t", seconds_to_hhmmss(duration)]
        elif end_sec is not None:
            cmd += ["-to", seconds_to_hhmmss(end_sec)]

        # Reencode “razonable” (calidad buena sin ser enorme)
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path)
        ]
        return cmd

    # Ejecutar
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

    print(f"OK: Archivo generado: {out_path}")

    if borrar_original:
        try:
            os.remove(input_path)
            print(f"OK: Original borrado: {input_path}")
        except Exception as e:
            print(f"AVISO: No pude borrar el original ({input_path}): {e}")
    else:
        print("Original conservado (borrar_original=False).")

if __name__ == "__main__":
    main()
