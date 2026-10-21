"""Deja la «Observación» del guardapolvo y de la pintura de muros escrita con la misma fórmula de la cantidad.

Uso: python -I alinear_observaciones.py <Presupuesto_Talca.xlsx>
El primer borrador traía perímetros armados con otros tramos; desde el 17-10-2026 la cantidad es la fuente.
"""
import re
import sys

import openpyxl


def legible(v):
    s = str(v).lstrip("=")
    s = re.sub(r"\d+(?:\.\d+)?", lambda m: f"{float(m.group()):.2f}".replace(".", ","), s)
    return s.replace("*", " × ").replace("+", " + ").replace("-", " − ")


RUTA = sys.argv[1]
wb = openpyxl.load_workbook(RUTA)
ws = wb["Presupuesto"]
n = 0
for f in ws.iter_rows(min_row=8):
    cod, nom, q = f[0].value, f[1].value, f[3].value
    if not (isinstance(cod, str) and cod.count(".") == 2) or cod == "D.1.3":
        continue
    if nom == "Guardapolvo":
        f[6].value = ("Perímetro interior (ancho de arriba + ancho de abajo + los dos fondos; un pilar que entra al recinto "
                      "suma sus dos caras), menos la puerta: " + legible(q) + " ml. Sin descontar ventanas.")
        n += 1
    elif nom == "Pintura de muros":
        f[6].value = "Perímetro del guardapolvo × 3,00 m: " + legible(q) + " m²."
        n += 1
wb.save(RUTA)
print(n, "observaciones alineadas en", RUTA)
