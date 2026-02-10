#!/usr/bin/env python3
import os, json, random, hashlib, datetime, subprocess, platform

name = os.environ.get("NAME", "Cronicle")
out_base = os.environ.get("ARTIFACTS_DIR", "/tmp")
out_dir = os.path.join(out_base, "cronicle-demo")
os.makedirs(out_dir, exist_ok=True)

ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
nums = [random.randint(1, 10000) for _ in range(10)]
s = sum(nums)

payload = f"{name}|{ts}|{s}|{' '.join(map(str, nums))}".encode("utf-8")
sha = hashlib.sha256(payload).hexdigest()

ffprobe_line = ""
try:
    p = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True, timeout=5)
    ffprobe_line = (p.stdout.splitlines()[:1] or [""])[0]
except Exception:
    pass

data = {
    "language": "python",
    "name": name,
    "timestamp": ts,
    "randomNumbers": nums,
    "sum": s,
    "sha256": sha,
    "ffprobe": ffprobe_line,
    "platform": {
        "python": platform.python_version(),
        "system": platform.system(),
    },
}

out_path = os.path.join(out_dir, "python.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"OK python -> {out_path}")
print(f"Condemor")
