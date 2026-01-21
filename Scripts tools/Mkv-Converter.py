import os
import sys
import subprocess
from pathlib import Path

# =========================
# CONFIG
# =========================
BASE_DIR = r"E:\zMkvConverter"
INPUT_TS_NAME = "NCAA Football Bowl Games_20260120_17552025_trim.ts"  # o pásalo por argv

OUTPUT_CONTAINER = "mkv"  # "mkv" o "mp4"

BORRAR_ORIGINAL = False
SOBRESCRIBIR_SALIDA = True

MANTENER_TODOS_AUDIOS = True

ANALYZEDURATION = "200M"
PROBESIZE = "200M"

# NVENC (GTX 1060)
NVENC_PRESET = "p1"   # si tu ffmpeg no soporta p1..p7, usa "fast" o "hp"
NVENC_CQ = "23"

# =========================
# HELPERS
# =========================
def run(cmd: list[str]) -> int:
    print("\n[FFMPEG CMD]")
    print(" ".join(cmd))
    print()
    return subprocess.run(cmd).returncode

def ensure_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        print("ERROR: No encuentro ffmpeg en el PATH. Verifica 'ffmpeg -version'.")
        sys.exit(1)

def ensure_nvenc():
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore"
    )
    if "h264_nvenc" not in p.stdout.lower():
        print("ERROR: Tu FFmpeg no tiene NVENC (h264_nvenc no aparece). Instala un build con NVENC.")
        sys.exit(1)

def resolve_input_path(base_dir: str, name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.is_absolute():
        return p
    return Path(base_dir) / name_or_path

def valid_output(p: Path) -> bool:
    return p.exists() and p.is_file() and p.stat().st_size > 0

# =========================
# MAIN
# =========================
def main():
    ensure_ffmpeg()
    ensure_nvenc()

    name = sys.argv[1] if len(sys.argv) >= 2 else INPUT_TS_NAME
    in_path = resolve_input_path(BASE_DIR, name)

    if not in_path.exists():
        print(f"ERROR: No existe el archivo: {in_path}")
        sys.exit(1)

    out_path = in_path.with_suffix("." + OUTPUT_CONTAINER.lower())

    ffmpeg_base = ["ffmpeg", "-hide_banner"]
    ffmpeg_base += ["-y"] if SOBRESCRIBIR_SALIDA else ["-n"]

    audio_map = ["-map", "0:a?"] if MANTENER_TODOS_AUDIOS else ["-map", "0:a:0?"]

    # Entrada común: tolerante con streams “sucios”
    common_in = [
        "-fflags", "+genpts+discardcorrupt",
        "-err_detect", "ignore_err",
        "-ignore_unknown",
        "-analyzeduration", ANALYZEDURATION,
        "-probesize", PROBESIZE,
        "-i", str(in_path),

        "-map", "0:v:0?",
        *audio_map,
        "-sn", "-dn",
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-max_muxing_queue_size", "4096",
    ]

    # ============================================================
    # INTENTO A: VIDEO COPY + AUDIO AAC, PERO CORRIGIENDO OFFSET PTS
    #   -> clave: -copyts -start_at_zero (start_at_zero solo con copyts)
    # ============================================================
    cmd_a = ffmpeg_base + ["-loglevel", "error"] + [
        "-copyts",
        "-start_at_zero",
    ] + common_in + [
        "-c:v", "copy",
        "-bsf:v", "extract_extradata",
        "-c:a", "aac",
        "-b:a", "160k",
        "-avoid_negative_ts", "make_zero",
    ]

    if OUTPUT_CONTAINER.lower() == "mp4":
        cmd_a += ["-movflags", "+faststart"]

    cmd_a += [str(out_path)]

    rc_a = run(cmd_a)
    if rc_a == 0 and valid_output(out_path):
        print(f"OK: Generado (copy video + aac audio + start_at_zero): {out_path}")
        if BORRAR_ORIGINAL:
            try:
                os.remove(in_path)
                print(f"OK: Original borrado: {in_path}")
            except Exception as e:
                print(f"AVISO: No pude borrar el original ({in_path}): {e}")
        return

    print("AVISO: Intento A falló o no fue válido. Fallback a NVENC + reset PTS (sin hueco inicial)...")

    # ============================================================
    # INTENTO B: NVENC (GTX 1060) + AAC
    #   -> clave: setpts/asetpts para que el primer frame/sonido sea t=0
    # ============================================================
    cmd_b = ffmpeg_base + ["-loglevel", "error"] + common_in + [
        "-vf", "setpts=PTS-STARTPTS",
        "-af", "asetpts=PTS-STARTPTS",

        "-c:v", "h264_nvenc",
        "-preset", NVENC_PRESET,
        "-rc:v", "vbr",
        "-cq:v", str(NVENC_CQ),
        "-b:v", "0",
        "-pix_fmt", "yuv420p",

        "-c:a", "aac",
        "-b:a", "160k",

        "-avoid_negative_ts", "make_zero",
    ]

    if OUTPUT_CONTAINER.lower() == "mp4":
        cmd_b += ["-movflags", "+faststart"]

    cmd_b += [str(out_path)]

    rc_b = run(cmd_b)
    if rc_b == 0 and valid_output(out_path):
        print(f"OK: Generado (NVENC + reset PTS): {out_path}")
        if BORRAR_ORIGINAL:
            try:
                os.remove(in_path)
                print(f"OK: Original borrado: {in_path}")
            except Exception as e:
                print(f"AVISO: No pude borrar el original ({in_path}): {e}")
        return

    print("ERROR: No se pudo generar un archivo final estable.")
    sys.exit(1)

if __name__ == "__main__":
    main()
