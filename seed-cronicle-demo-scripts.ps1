param(
  [string]$ScriptsDir = "E:\Docker_folders\_scripts",
  [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Utf8NoBomFile {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$Text,
    [switch]$UnixLf
  )

  $dir = Split-Path -Parent $Path
  if ($dir -and -not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
  }

  if ($UnixLf) {
    $Text = $Text -replace "`r`n", "`n"
    $Text = $Text -replace "`r", "`n"
  }

  if (-not $Text.EndsWith("`n")) { $Text += "`n" }

  if ((Test-Path -LiteralPath $Path) -and -not $Force) {
    Write-Host "SKIP (ya existe): $Path"
    return
  }

  if ((Test-Path -LiteralPath $Path) -and $Force) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    Copy-Item -LiteralPath $Path -Destination "$Path.bak_$stamp" -Force
  }

  # UTF-8 sin BOM (compatible PS 5.1 y 7)
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Text, $utf8NoBom)

  Write-Host "OK: $Path"
}

if (-not (Test-Path -LiteralPath $ScriptsDir)) {
  New-Item -ItemType Directory -Path $ScriptsDir -Force | Out-Null
}

# -----------------------
# BASH demo
# -----------------------
$bashDemo = @'
#!/usr/bin/env bash
set -euo pipefail

NAME="${1:-Cronicle}"
OUT_BASE="${ARTIFACTS_DIR:-/tmp}"
OUT_DIR="$OUT_BASE/cronicle-demo"
mkdir -p "$OUT_DIR"

ts="$(date -Iseconds)"

nums=()
sum=0
for i in {1..10}; do
  n=$(( (RANDOM % 10000) + 1 ))
  nums+=("$n")
  sum=$((sum + n))
done

payload="$NAME|$ts|$sum|${nums[*]}"
sha="$(printf "%s" "$payload" | sha256sum | awk '{print $1}')"

ffprobe_line=""
if command -v ffprobe >/dev/null 2>&1; then
  ffprobe_line="$(ffprobe -version 2>/dev/null | head -n 1 || true)"
fi

if command -v jq >/dev/null 2>&1; then
  jq -n \
    --arg language "bash" \
    --arg name "$NAME" \
    --arg timestamp "$ts" \
    --arg sha256 "$sha" \
    --arg ffprobe "$ffprobe_line" \
    --argjson numbers "$(printf '%s\n' "${nums[@]}" | jq -R 'tonumber' | jq -s '.')" \
    --argjson sum "$sum" \
    '{
      language:$language,
      name:$name,
      timestamp:$timestamp,
      randomNumbers:$numbers,
      sum:$sum,
      sha256:$sha256,
      ffprobe:$ffprobe
    }' > "$OUT_DIR/bash.json"
else
  printf '{"language":"bash","name":"%s","timestamp":"%s","sum":%d,"sha256":"%s","ffprobe":"%s"}\n' \
    "$NAME" "$ts" "$sum" "$sha" "$ffprobe_line" > "$OUT_DIR/bash.json"
fi

echo "OK bash -> $OUT_DIR/bash.json"
'@

# -----------------------
# PYTHON demo
# -----------------------
$pythonDemo = @'
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
'@

# -----------------------
# POWERSHELL demo (pwsh dentro del contenedor)
# -----------------------
$pwshDemo = @'
param(
  [string]$Name = "Cronicle"
)

$OutBase = $env:ARTIFACTS_DIR
if ([string]::IsNullOrWhiteSpace($OutBase)) { $OutBase = "/tmp" }

$OutDir = Join-Path $OutBase "cronicle-demo"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$ts = (Get-Date).ToUniversalTime().ToString("o")

$nums = 1..10 | ForEach-Object { Get-Random -Minimum 1 -Maximum 10001 }
$sum = ($nums | Measure-Object -Sum).Sum

$payload = "$Name|$ts|$sum|$($nums -join ' ')"
$shaBytes = [System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes($payload))
$sha = ($shaBytes | ForEach-Object { $_.ToString("x2") }) -join ""

$ffprobeLine = ""
if (Get-Command ffprobe -ErrorAction SilentlyContinue) {
  try { $ffprobeLine = (ffprobe -version 2>$null | Select-Object -First 1) } catch {}
}

$data = [ordered]@{
  language      = "powershell"
  name          = $Name
  timestamp     = $ts
  randomNumbers = $nums
  sum           = $sum
  sha256        = $sha
  ffprobe       = $ffprobeLine
}

$outPath = Join-Path $OutDir "powershell.json"
$data | ConvertTo-Json -Depth 6 | Set-Content -Path $outPath -Encoding UTF8
Write-Host "OK pwsh -> $outPath"
exit 0
'@

# -----------------------
# RUST demo (sin crates)
# -----------------------
$rustDemo = @'
use std::env;
use std::fs;
use std::time::{SystemTime, UNIX_EPOCH};

fn lcg(seed: &mut u64) -> u64 {
    *seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
    *seed
}

fn main() {
    let name = env::var("NAME").unwrap_or_else(|_| "Cronicle".to_string());
    let out_base = env::var("ARTIFACTS_DIR").unwrap_or_else(|_| "/tmp".to_string());
    let out_dir = format!("{}/cronicle-demo", out_base);
    let _ = fs::create_dir_all(&out_dir);

    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap();
    let ts = format!("{}.{:09}Z", now.as_secs(), now.subsec_nanos());

    let mut seed = now.as_nanos() as u64;
    let mut nums: Vec<u64> = Vec::new();
    let mut sum: u64 = 0;
    for _ in 0..10 {
        let n = (lcg(&mut seed) % 10000) + 1;
        nums.push(n);
        sum += n;
    }

    // Hash demo simple (FNV-1a 64) — NO cripto, solo demo
    let payload = format!("{}|{}|{}|{:?}", name, ts, sum, nums);
    let mut h: u64 = 1469598103934665603;
    for b in payload.as_bytes() {
        h ^= *b as u64;
        h = h.wrapping_mul(1099511628211);
    }

    let json = format!(
r#"{{
  "language": "rust",
  "name": "{name}",
  "timestamp": "{ts}",
  "randomNumbers": {nums},
  "sum": {sum},
  "hash64": "{hash:016x}"
}}"#,
        name = name,
        ts = ts,
        nums = format!("[{}]", nums.iter().map(|n| n.to_string()).collect::<Vec<_>>().join(",")),
        sum = sum,
        hash = h
    );

    let out_path = format!("{}/rust.json", out_dir);
    let _ = fs::write(&out_path, json.as_bytes());
    println!("OK rust -> {}", out_path);
}
'@

# Write files
Write-Utf8NoBomFile -Path (Join-Path $ScriptsDir "cronicle-demo-shell.sh")  -Text $bashDemo  -UnixLf
Write-Utf8NoBomFile -Path (Join-Path $ScriptsDir "cronicle-demo-python.py") -Text $pythonDemo -UnixLf
Write-Utf8NoBomFile -Path (Join-Path $ScriptsDir "cronicle-demo-pwsh.ps1")  -Text $pwshDemo
Write-Utf8NoBomFile -Path (Join-Path $ScriptsDir "cronicle-demo-rust.rs")   -Text $rustDemo  -UnixLf

Write-Host ""
Write-Host "Listo. Scripts creados en: $ScriptsDir"
Write-Host "Dentro del contenedor deberían verse en: /scripts/"
Write-Host 'Salida: $ARTIFACTS_DIR/cronicle-demo (o /tmp/cronicle-demo)'
