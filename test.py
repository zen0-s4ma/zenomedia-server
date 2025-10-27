import os

rutas = [r"E:\Peliculas", r"F:\_Multimedia\Peliculas"]

for ruta in rutas:
    print(f"\n--- Analizando: {ruta} ---")
    for raiz, _, archivos in os.walk(ruta):
        for archivo in archivos:
            print(os.path.join(raiz, archivo))
