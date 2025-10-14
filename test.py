import os

# Ruta base
ruta_base = r"F:\_Multimedia"

# Recorremos todos los directorios y archivos desde ruta_base
for carpeta, subcarpetas, archivos in os.walk(ruta_base):
    for archivo in archivos:
        if archivo.lower().endswith(".nfo"):
            ruta_archivo = os.path.join(carpeta, archivo)
            try:
                os.remove(ruta_archivo)
                print(f"Eliminado: {ruta_archivo}")
            except Exception as e:
                print(f"No se pudo eliminar {ruta_archivo}: {e}")

print("Proceso finalizado.")
