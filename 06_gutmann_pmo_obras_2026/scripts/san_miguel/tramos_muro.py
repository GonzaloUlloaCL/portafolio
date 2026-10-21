"""Tramos de muro (existente E, nuevo N) a lo largo de líneas horizontales o verticales del plano, en metros.

Uso: python -I tramos_muro.py <carpeta_grilla> h|v <coordenada> <desde> <hasta> [h|v coord desde hasta ...]
Grilla de 5 cm de escena_3d/extraer.py + rellenar.py. Con esto se midieron los muros de la casona (08-oct).
"""
import sys

import numpy as np

D = sys.argv[1]
RES = 0.05
ex = np.load(D + "/ex.npy")
red = np.load(D + "/red.npy")
args = sys.argv[2:]
for i in range(0, len(args), 4):
    eje, c, a, b = args[i], float(args[i + 1]), float(args[i + 2]), float(args[i + 3])
    k = int(round(c / RES))
    i0, i1 = int(round(a / RES)), int(round(b / RES))
    if eje == "h":
        fe, fn = ex[k, i0:i1], red[k, i0:i1]
    else:
        fe, fn = ex[i0:i1, k], red[i0:i1, k]
    tramos, actual, ini = [], None, None
    for j in range(len(fe)):
        t = "N" if fn[j] else ("E" if fe[j] else None)
        if t != actual:
            if actual:
                tramos.append(f"{actual} {(i0 + ini) * RES:.2f}-{(i0 + j) * RES:.2f}")
            actual, ini = t, j
    if actual:
        tramos.append(f"{actual} {(i0 + ini) * RES:.2f}-{(i0 + len(fe)) * RES:.2f}")
    print(f"{eje} {c:.2f} [{a}-{b}]: " + " | ".join(tramos))
