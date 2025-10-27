#!/bin/bash

echo "======================================="
echo "-- EJECUTANDO ENTRYPOINT DE KALI --"
echo "======================================="

echo "-- Actualizando sistema..."
apt update -y > /dev/null 2>&1 && echo "-- UPDATE OK"
apt upgrade -y > /dev/null 2>&1 && echo "-- UPGRADE OK"

echo "-- Iniciando el servicio SSH..."
service ssh restart > /dev/null 2>&1 && echo "-- SSH OK"

echo
fastfetch || echo "-- fastfetch no disponible"
echo
echo "-- Sistema Kali listo --"

# Ejecutar shell interactiva o mantener contenedor vivo
exec tail -f /dev/null