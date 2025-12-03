#!/usr/bin/env python3
import os
from pathlib import Path
from shutil import copy2, move
from datetime import datetime


# ========= CONFIGURACIÓN =========
# Cambia estas rutas a las tuyas:

RUTA_ORIGEN = Path(r"D:\__________DISCO E Completo\__almacen ordenado\_Fotos Ordenadas\_____PPENDIENTE ORDENAR")
RUTA_DESTINO = Path(r"D:\__________DISCO E Completo\__almacen ordenado\_Fotos_Años")

# Modo de operación: "copy" (copiar) o "move" (mover)
MODO = "copy"  # de momento solo copiar, como quieres

# Si DRY_RUN = True, solo muestra lo que haría, sin copiar/mover nada
DRY_RUN = False
# ================================


def obtener_anio_desde_fs(path: Path) -> int:
    """
    Devuelve el año usando la fecha de última modificación del archivo.
    Si prefieres usar la fecha de creación, cambia st_mtime por st_ctime.
    """
    ts = path.stat().st_mtime  # o st_ctime para fecha de creación (en Windows)
    dt = datetime.fromtimestamp(ts)
    return dt.year


def generar_ruta_destino(base_destino: Path, anio: int, ruta_relativa: Path, nombre_fichero: str) -> Path:
    """
    Construye la ruta completa de destino:
    base_destino / anio / ruta_relativa / nombre_fichero
    """
    return base_destino / str(anio) / ruta_relativa / nombre_fichero


def obtener_ruta_sin_colision(ruta_destino: Path) -> Path:
    """
    Si la ruta de destino ya existe, genera una nueva con sufijo __DUPLICATE_X
    para no sobrescribir nada.
    """
    if not ruta_destino.exists():
        return ruta_destino

    carpeta = ruta_destino.parent
    nombre = ruta_destino.stem
    extension = ruta_destino.suffix

    contador = 1
    while True:
        candidato = carpeta / f"{nombre}__DUPLICATE_{contador}{extension}"
        if not candidato.exists():
            return candidato
        contador += 1


def verificar_copias(archivos_origen, mapa_copiados):
    """
    Verifica que todos los archivos de origen tienen un destino asociado y que
    ese fichero de destino existe físicamente.
    """
    print("\n------ VERIFICACIÓN ------")
    total_origen = len(archivos_origen)
    total_copiados = len(mapa_copiados)

    print(f"Total archivos en origen: {total_origen}")
    print(f"Total archivos registrados como copiados: {total_copiados}")

    # Archivos que no llegaron a copiarse (no están en el mapa)
    faltan_por_copiar = [src for src in archivos_origen if src not in mapa_copiados]

    # Archivos cuyo destino no existe físicamente
    faltan_en_destino = []
    for src, dst in mapa_copiados.items():
        if not dst.exists():
            faltan_en_destino.append((src, dst))

    if not faltan_por_copiar and not faltan_en_destino:
        print("✅ Verificación OK: todos los ficheros de origen tienen su copia en destino.")
    else:
        print("⚠ ATENCIÓN: Hay incidencias en la copia.")

        if faltan_por_copiar:
            print(f" - Archivos que NO se han podido copiar: {len(faltan_por_copiar)}")
            for src in faltan_por_copiar[:20]:
                print(f"    * {src}")
            if len(faltan_por_copiar) > 20:
                print(f"    ... y {len(faltan_por_copiar) - 20} más")

        if faltan_en_destino:
            print(f" - Archivos cuyo destino esperado NO existe: {len(faltan_en_destino)}")
            for src, dst in faltan_en_destino[:20]:
                print(f"    * Origen:  {src}")
                print(f"      Destino: {dst}")
            if len(faltan_en_destino) > 20:
                print(f"    ... y {len(faltan_en_destino) - 20} más")


def organizar(origen: Path, destino: Path, modo: str = "copy", dry_run: bool = False) -> None:
    """
    Recorre la ruta origen, calcula el año de cada fichero y lo copia/mueve
    a la ruta destino en la forma:
        destino / anio / (ruta_relativa_desde_origen) / fichero
    """
    if not origen.is_dir():
        raise ValueError(f"La ruta origen no es un directorio válido: {origen}")

    if not destino.exists() and not dry_run:
        destino.mkdir(parents=True, exist_ok=True)

    # 1) Listar todos los archivos de origen primero
    archivos_origen = []
    for root, dirs, files in os.walk(origen):
        root_path = Path(root)
        for nombre_fichero in files:
            archivos_origen.append(root_path / nombre_fichero)

    total_archivos = len(archivos_origen)
    print(f"Archivos encontrados en origen: {total_archivos}")

    total_procesados = 0
    mapa_copiados = {}  # origen -> destino (solo si se copia/mueve correctamente)

    # 2) Procesar cada archivo
    for fichero_origen in archivos_origen:
        total_procesados += 1

        rel_path = fichero_origen.relative_to(origen)  # p.ej. CAMERA/foto1.jpg
        ruta_relativa = rel_path.parent               # p.ej. CAMERA
        nombre_fichero = rel_path.name                # p.ej. foto1.jpg

        try:
            anio = obtener_anio_desde_fs(fichero_origen)
        except Exception as e:
            print(f"[ADVERTENCIA] No se pudo obtener el año de '{fichero_origen}': {e}")
            continue

        ruta_destino_inicial = generar_ruta_destino(destino, anio, ruta_relativa, nombre_fichero)
        ruta_destino_final = obtener_ruta_sin_colision(ruta_destino_inicial)

        print(f"{modo.upper()} ({total_procesados}/{total_archivos}) -> {fichero_origen}")
        print(f"    ==> {ruta_destino_final}")

        if dry_run:
            # En modo simulación NO copiamos ni añadimos al mapa
            continue

        try:
            ruta_destino_final.parent.mkdir(parents=True, exist_ok=True)
            if modo == "copy":
                copy2(fichero_origen, ruta_destino_final)
            elif modo == "move":
                move(fichero_origen, ruta_destino_final)
            else:
                raise ValueError("Modo no soportado. Usa 'copy' o 'move'.")
            # Solo si todo fue bien, registramos la copia
            mapa_copiados[fichero_origen] = ruta_destino_final
        except Exception as e:
            print(f"[ERROR] No se pudo {modo} el archivo '{fichero_origen}' -> '{ruta_destino_final}': {e}")

    print("\n------ RESUMEN ------")
    print(f"Archivos en origen:        {total_archivos}")
    print(f"Archivos procesados:       {total_procesados}")
    print(f"Archivos copiados/movidos: {len(mapa_copiados)}")

    if dry_run:
        print("IMPORTANTE: dry-run activado, NO se ha copiado/movido ningún fichero,")
        print("por lo que no se puede hacer verificación real.")
    else:
        verificar_copias(archivos_origen, mapa_copiados)


if __name__ == "__main__":
    organizar(RUTA_ORIGEN, RUTA_DESTINO, modo=MODO, dry_run=DRY_RUN)
