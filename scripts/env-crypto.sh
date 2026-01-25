#!/bin/sh
set -eu

cd /work

DATE="$(date +%Y%m%d%H%M%S)"

backup_if_exists() {
  SRC="$1"
  BAK="$2"

  if [ -f "$SRC" ]; then
    if [ -f "$BAK" ]; then
      echo "WARN: Backup ya existe: $BAK (no lo sobreescribo)"
    else
      cp -f "$SRC" "$BAK"
      echo "OK: Backup creado: $BAK"
    fi
  fi
}

require_file() {
  FILE="$1"
  if [ ! -f "$FILE" ]; then
    echo "ERROR: Falta el archivo requerido: $FILE"
    exit 1
  fi
}

do_decrypt() {
  require_file ".env.enc"
  backup_if_exists ".env" "./env-backups/.env.bkp.${DATE}"

  echo "==> Descifrando .env.enc -> .env (te pedirá la contraseña)"
  # SIN -pass => OpenSSL pide contraseña interactiva
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -md sha256 -a \
    -in ./.env.enc -out ./.env

  echo "OK: .env creado/actualizado."
}

do_encrypt() {
  require_file ".env"
  backup_if_exists ".env.enc" "./env-backups/.env.enc.bkp.${DATE}"

  echo "==> Cifrando .env -> .env.enc (te pedirá la contraseña + verificación)"
  # SIN -pass => OpenSSL pide contraseña interactiva
  openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 -md sha256 -a \
    -in ./.env -out ./.env.enc

  echo "OK: .env.enc creado/actualizado."
}

menu() {
  echo "=============================="
  echo " ENV CRYPTO TOOL (OpenSSL)"
  echo "=============================="
  echo "1) Descifrar:  .env.enc -> .env"
  echo "2) Cifrar:     .env     -> .env.enc"
  echo "q) Salir"
  printf "Elige una opción: "
  read -r choice

  case "$choice" in
    1) do_decrypt ;;
    2) do_encrypt ;;
    q|Q) exit 0 ;;
    *) echo "Opción inválida"; exit 1 ;;
  esac
}

ACTION="${1:-}"

case "$ACTION" in
  decrypt) do_decrypt ;;
  encrypt) do_encrypt ;;
  "" ) menu ;;
  * ) echo "Uso: decrypt | encrypt (o sin argumento para menú)"; exit 1 ;;
esac
