import json
import subprocess
from pathlib import Path

# ✅ CAMBIA AQUÍ LA RUTA
ROOT_PATH = Path(r"F:\Peliculas")

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts", ".wmv", ".webm"}

def run_ffprobe(path: Path) -> dict:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return {"_error": p.stderr.strip()}
    return json.loads(p.stdout)

def needs_processing(info: dict) -> tuple[bool, list[str], dict]:
    reasons = []
    details = {}

    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        return (False, ["NO_VIDEO_STREAM"], details)

    codec = (v.get("codec_name") or "").lower()
    width = int(v.get("width") or 0)
    height = int(v.get("height") or 0)
    pix_fmt = (v.get("pix_fmt") or "").lower()
    profile = (v.get("profile") or "").lower()

    bit_depth = None
    if v.get("bits_per_raw_sample"):
        try:
            bit_depth = int(v["bits_per_raw_sample"])
        except Exception:
            bit_depth = None

    if bit_depth is None:
        if "10le" in pix_fmt:
            bit_depth = 10
        elif "12le" in pix_fmt:
            bit_depth = 12
        else:
            bit_depth = 8  # best effort

    color_transfer = (v.get("color_transfer") or "").lower()
    color_primaries = (v.get("color_primaries") or "").lower()
    is_hdr = color_transfer in {"smpte2084", "arib-std-b67"} or color_primaries == "bt2020"

    details.update({
        "video_codec": codec,
        "width": width,
        "height": height,
        "pix_fmt": pix_fmt,
        "profile": profile,
        "bit_depth": bit_depth,
        "is_hdr_guess": is_hdr,
    })

    if codec != "h264":
        reasons.append("VIDEO_CODEC_NOT_H264")
    if height > 1080 or width > 1920:
        reasons.append("RESOLUTION_GT_1080P")
    if bit_depth > 8 or "high 10" in profile or "main 10" in profile:
        reasons.append("BIT_DEPTH_GT_8")
    if is_hdr:
        reasons.append("HDR_CONTENT_REVIEW")

    return (len(reasons) > 0, reasons, details)

def iter_video_files(root: Path):
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            yield p

def main():
    root = ROOT_PATH
    if not root.exists():
        raise SystemExit(f"Path not found: {root}")

    needs = []
    ok = []
    errors = []

    for f in iter_video_files(root):
        info = run_ffprobe(f)
        if "_error" in info:
            errors.append((str(f), info["_error"]))
            continue

        should, reasons, details = needs_processing(info)
        row = {"file": str(f), "reasons": reasons, **details}
        if should:
            needs.append(row)
            print(row)
        else:
            ok.append(row)

    print()
    print("\n=== NEEDS PROCESSING ===")
    for r in needs:
        print(f"- {r['file']}")
        print(f"  codec={r['video_codec']} {r['width']}x{r['height']} bit={r['bit_depth']} hdr={r['is_hdr_guess']}")
        print(f"  reasons={','.join(r['reasons'])}")

    print("\n=== OK / NOT REQUIRED ===")
    for r in ok:
        print(f"- {r['file']}")
        print(f"  codec={r['video_codec']} {r['width']}x{r['height']} bit={r['bit_depth']} hdr={r['is_hdr_guess']}")

    if errors:
        print("\n=== ERRORS ===")
        for f, e in errors:
            print(f"- {f}\n  {e}")

    out = {"needs": needs, "ok": ok, "errors": errors}
    out_path = Path("scan_report.json")
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nSaved: {out_path.resolve()}")

if __name__ == "__main__":
    main()