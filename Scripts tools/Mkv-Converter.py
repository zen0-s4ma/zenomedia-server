import os
import sys
import subprocess
from pathlib import Path

# =========================
# CONFIG
# =========================
BASE_DIR = r"E:\zMkvConverter"
INPUT_TS_NAME = "NCAA Football Bowl Games_20260120_17552025_trim_trim.ts"  # o pásalo por argv

OUTPUT_CONTAINER = "mkv"  # "mkv" o "mp4"

BORRAR_ORIGINAL = False
SOBRESCRIBIR_SALIDA = True

MANTENER_TODOS_AUDIOS = True

ANALYZEDURATION = "200M"
PROBESIZE = "200M"

# NVENC (GTX 1060)
NVENC_PRESET = "p1"   # si tu ffmpeg no soporta p1..p7, usa "fast" o "hp"
NVENC_CQ = "23"

# --- NUEVO: eliminar hueco inicial saltando al primer keyframe ---
AUTO_SALTAR_HUECO_INICIAL = True
SCAN_KEYFRAMES_SEGUNDOS = 180   # cuánto escanear al principio buscando keyframe (sube si hace falta)
MIN_HUECO = 0.20                # si el primer keyframe está antes de esto, no se salta

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

def find_first_video_keyframe_pts(path: Path, scan_seconds: int) -> float:
    """
    Devuelve el PTS (segundos) del primer keyframe (IDR/I) que ffprobe ve al inicio.
    Si no encuentra, devuelve 0.0.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-read_intervals", f"0%+{scan_seconds}",
        "-show_frames",
        "-show_entries", "frame=key_frame,pkt_pts_time",
        "-of", "csv=print_section=0",
        str(path),
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="ignore")
    for line in p.stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        # Formato: key_frame,pkt_pts_time  -> "1,7.040000"
        parts = [x.strip() for x in s.split(",")]
        if len(parts) < 2:
            continue
        try:
            key = int(parts[0])
        except Exception:
            continue
        if key != 1:
            continue
        try:
            pts = float(parts[1])
        except Exception:
            continue
        return max(0.0, pts)
    return 0.0

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

    # --- NUEVO: calcula salto real al primer keyframe ---
    skip_sec = 0.0
    if AUTO_SALTAR_HUECO_INICIAL:
        kf = find_first_video_keyframe_pts(in_path, SCAN_KEYFRAMES_SEGUNDOS)
        if kf > MIN_HUECO:
            skip_sec = kf
            print(f"[INFO] El archivo no tiene vídeo decodificable hasta ~{skip_sec:.3f}s. Se saltará ese hueco.")
        else:
            print("[INFO] No se detecta hueco inicial significativo.")

    # Entrada común
    common_pre_i = [
        "-fflags", "+genpts+discardcorrupt",
        "-err_detect", "ignore_err",
        "-ignore_unknown",
        "-analyzeduration", ANALYZEDURATION,
        "-probesize", PROBESIZE,
    ]
    if skip_sec > 0:
        # Esto elimina el hueco (el contenido empieza “directo”)
        common_pre_i += ["-ss", f"{skip_sec:.3f}"]

    common_post_i = [
        "-i", str(in_path),
        "-map", "0:v:0?",
        *audio_map,
        "-sn", "-dn",
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-max_muxing_queue_size", "4096",
        "-avoid_negative_ts", "make_zero",
    ]

    # ============================================================
    # INTENTO A: VIDEO COPY + AUDIO AAC (rápido)
    # ============================================================
    cmd_a = ffmpeg_base + ["-loglevel", "error"] + common_pre_i + common_post_i + [
        "-c:v", "copy",
        "-bsf:v", "extract_extradata",
        "-c:a", "aac",
        "-b:a", "160k",
    ]
    if OUTPUT_CONTAINER.lower() == "mp4":
        cmd_a += ["-movflags", "+faststart"]
    cmd_a += [str(out_path)]

    rc_a = run(cmd_a)
    if rc_a == 0 and valid_output(out_path):
        print(f"OK: Generado (copy video + aac audio): {out_path}")
        if BORRAR_ORIGINAL:
            try:
                os.remove(in_path)
                print(f"OK: Original borrado: {in_path}")
            except Exception as e:
                print(f"AVISO: No pude borrar el original ({in_path}): {e}")
        return

    print("AVISO: Intento A falló. Fallback a NVENC (GTX 1060) + AAC...")

    # ============================================================
    # INTENTO B: NVENC VIDEO + AAC AUDIO
    # ============================================================
    cmd_b = ffmpeg_base + ["-loglevel", "error"] + common_pre_i + common_post_i + [
        "-c:v", "h264_nvenc",
        "-preset", NVENC_PRESET,
        "-rc:v", "vbr",
        "-cq:v", str(NVENC_CQ),
        "-b:v", "0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "160k",
    ]
    if OUTPUT_CONTAINER.lower() == "mp4":
        cmd_b += ["-movflags", "+faststart"]
    cmd_b += [str(out_path)]

    rc_b = run(cmd_b)
    if rc_b == 0 and valid_output(out_path):
        print(f"OK: Generado (NVENC + AAC): {out_path}")
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

