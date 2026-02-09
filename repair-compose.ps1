# repair-compose-v2.ps1
# - Backup .bkp3
# - Detecta y elimina caracteres de control C0 + C1 (except TAB/LF/CR)
# - Reescribe UTF-8 sin BOM
# - Verifica docker compose config y falla si falla

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$composePath = "D:\Github-zen0s4ma\zenomedia-server\docker-compose.yml"
if (!(Test-Path $composePath)) { throw "No existe: $composePath" }

$backup3 = "$composePath.bkp3"
Copy-Item -Force $composePath $backup3
Write-Host "Backup adicional: $backup3"

# Leer bytes crudos
[byte[]]$bytes = [System.IO.File]::ReadAllBytes($composePath)

# Intentar decodificar UTF-8 estricto (si hay bytes inválidos, lanza)
$utf8Strict = New-Object System.Text.UTF8Encoding($false, $true)
try {
  $text = $utf8Strict.GetString($bytes)
  Write-Host "Decode: UTF-8 válido (estricto)."
}
catch {
  # Si no es UTF-8 válido, decodificamos como Windows-1252 y luego lo normalizamos a UTF-8
  Write-Host "WARN: No era UTF-8 válido. Convirtiendo desde Windows-1252 -> UTF-8..."
  $cp1252 = [System.Text.Encoding]::GetEncoding(1252)
  $text = $cp1252.GetString($bytes)
}

# Detectar caracteres de control:
# - C0: U+0000–U+001F y U+007F (permitimos TAB 0x09, LF 0x0A, CR 0x0D)
# - C1: U+0080–U+009F
$pattern = '[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]'
$matches = [regex]::Matches($text, $pattern)

if ($matches.Count -gt 0) {
  Write-Host ("Encontrados {0} caracteres de control (C0/C1). Mostrando hasta 30:" -f $matches.Count)

  $max = [Math]::Min(30, $matches.Count)
  for ($i=0; $i -lt $max; $i++) {
    $m = $matches[$i]
    $ch = $m.Value[0]
    $code = [int][char]$ch

    # calcular línea/columna aproximadas
    $before = $text.Substring(0, $m.Index)
    $line = ($before -split "`n").Count
    $col = ($before.Split("`n")[-1]).Length + 1

    Write-Host ("  #{0}  U+{1:X4}  at index={2}  line={3} col={4}" -f ($i+1), $code, $m.Index, $line, $col)
  }

  Write-Host "Eliminando caracteres de control..."
  $text = [regex]::Replace($text, $pattern, '')
}
else {
  Write-Host "No se detectaron caracteres de control C0/C1 en el texto decodificado."
  Write-Host "Igualmente reescribo a UTF-8 sin BOM para normalizar."
}

# Guardar como UTF-8 sin BOM
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($composePath, $text, $utf8NoBom)
Write-Host "Guardado: UTF-8 sin BOM."

# Verificación REAL (mirando exit code)
Write-Host "Probando parseo: docker compose -f docker-compose.yml config"
& docker compose -f $composePath config | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "docker compose config sigue fallando. Revisa el output anterior. (ExitCode=$LASTEXITCODE)"
}
Write-Host "OK: docker compose config parsea correctamente."
