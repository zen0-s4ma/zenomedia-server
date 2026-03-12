import subprocess
import json
from pathlib import Path

ROOT = Path(r"F:\Ambience")
MAX_MINUTES = 180
MAX_SECONDS = MAX_MINUTES * 60


def get_duration(file):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(file)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)

    return float(data["format"]["duration"])


def trim_video(file):

    if "__trimmed__" in file.name or "_trimmed" in file.name:
        return

    duration = get_duration(file)

    if duration <= MAX_SECONDS:
        print(f"SKIP (ya corto): {file.name}")
        return

    output = file.with_name(file.stem + ".__trimmed__.mp4")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(file),
        "-t", str(MAX_SECONDS),
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        str(output)
    ]

    subprocess.run(cmd)

    if output.exists():
        output.replace(file)
        print("RECORTADO:", file.name)
    else:
        print("ERROR recortando:", file.name)


def main():

    files = list(ROOT.rglob("*.mp4"))

    print("Archivos encontrados:", len(files))
    print()

    for file in files:
        trim_video(file)


if __name__ == "__main__":
    main()