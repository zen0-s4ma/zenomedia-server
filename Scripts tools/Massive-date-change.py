import os
import time
import ctypes
from datetime import datetime

def change_file_times(path, new_datetime):
    """
    Cambia la fecha de creación, modificación y acceso de un archivo o carpeta.
    Compatible con Windows usando ctypes.
    """

    # Convertimos la fecha a timestamp
    timestamp = new_datetime.timestamp()

    # Cambiar fecha de modificación y acceso
    os.utime(path, (timestamp, timestamp))

    # ---- Cambiar fecha de CREACIÓN (Windows API) ----
    FILE_WRITE_ATTRIBUTES = 0x0100
    kernel32 = ctypes.windll.kernel32

    handle = kernel32.CreateFileW(
        path,
        FILE_WRITE_ATTRIBUTES,
        0,
        None,
        3,
        0x0200,
        None
    )

    if handle != -1:
        # Convertir timestamp a formato FILETIME
        ft = int((timestamp + 11644473600) * 10000000)
        ctime = ctypes.c_longlong(ft)

        kernel32.SetFileTime(
            handle,
            ctypes.byref(ctime),  # creation time
            None,                # last access time
            None                 # last write time
        )
        kernel32.CloseHandle(handle)


def update_all_files(base_path, date_str):
    """
    Recorre todas las carpetas y archivos en base_path
    y actualiza tiempos usando la fecha date_str.
    
    date_str debe ir en formato: 'YYYY-MM-DD HH:MM:SS'
    """
    new_datetime = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

    for root, dirs, files in os.walk(base_path):
        
        # Cambiar fecha de subcarpetas
        for d in dirs:
            full_path = os.path.join(root, d)
            change_file_times(full_path, new_datetime)

        # Cambiar fecha de archivos
        for f in files:
            full_path = os.path.join(root, f)
            change_file_times(full_path, new_datetime)

    print(f"✔ Todos los tiempos fueron actualizados a: {new_datetime}")


# -------------------------
# EJEMPLO DE USO:
# -------------------------

if __name__ == "__main__":
    ruta = r"E:\MiniSeries\David el gnomo"
    fecha = "1990-01-01 10:30:00"

    update_all_files(ruta, fecha)
