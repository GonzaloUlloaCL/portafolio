"""Arma el tour de la habilitación Preuniversitario Talca en formato película (base: el tour del Dren NEWAIR) con la
caminata por dentro: la geometría de sitio.py y, por capítulo, la lista «Qué incluye» con las partidas del Excel
vigente, agrupadas por nombre. Sin precios ni cantidades.

Uso: python -I armar_tour.py <Presupuesto_Talca.xlsx> <salida.html> [<salida_artifact.html>]
"""
import json
import os
import re
import sys

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sitio as S  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
excel, salida = sys.argv[1], sys.argv[2]
CLAVES = ["ANG", "TAN", "ALTO_MURO", "ALTO_PUERTA", "ALFEIZAR", "DINTEL", "CIELO", "MUROS", "MURO_INCLINADO",
          "CORTES_INCLINADO", "PUERTAS", "VENTANAS", "PILARES", "LADO_PILAR", "FILAS_EJES", "FACHADA_ORIENTE",
          "FACHADA_SUR", "Y_SUR", "LIMITE_NORTE", "RECINTOS", "SALA01_ALTA", "RECEPCION", "MESONES",
          "SILLAS", "MARCOS", "MESON_PILAR", "EXISTENTE", "ENTRADA"]
# por capítulo: qué partidas entran, por zona y nombre (el Excel repite los nombres en cada recinto)
TERMINACIONES = ("Porcelanato", "Guardapolvo", "Estructura cielo", "Pintura de muros", "Pintura de cielo")
CAPITULOS = [
    lambda c, d: False,
    lambda c, d: c.startswith("A."),
    lambda c, d: c.startswith("B.") and d.startswith("Tabiquería"),
    lambda c, d: c.startswith("C.") and d.startswith("Tabiquería"),
    lambda c, d: c.startswith("D.1.1") or d in ("Puertas", "Ventanas y vidrios"),
    lambda c, d: d in TERMINACIONES,
    lambda c, d: c.startswith("E."),
]

datos = {k: getattr(S, k) for k in CLAVES}
ws = openpyxl.load_workbook(excel, read_only=True)["Presupuesto"]
sector, filas = {}, []
for r in ws.iter_rows(values_only=True):
    b = str(r[1] or "")
    m = re.match(r"^([A-E]\.\d+) (?:Sector )?(.+)$", b)
    if m and not r[0]:
        sector[m.group(1)] = m.group(2).replace("sala de capacitación", "Sala").replace("oficina", "Oficina") \
            .replace("guardar", "Guardar")
        if sector[m.group(1)].startswith("hall"):
            sector[m.group(1)] = "Hall"
    if isinstance(r[0], str) and re.match(r"^[A-E]\.\d+\.\d+$", r[0]) and r[1]:
        filas.append((r[0], str(r[1]).strip()))
incluye = []
for regla in CAPITULOS:
    grupos = {}
    for c, d in filas:
        if regla(c, d):
            grupos.setdefault(d, []).append(sector.get(c.rsplit(".", 1)[0], ""))
    incluye.append([d + (": " + ", ".join(s for s in lugares if s) if len(lugares) > 1 else "") for d, lugares in grupos.items()])

html = open(os.path.join(AQUI, "tour_plantilla.html"), encoding="utf-8").read()
for marca, valor in (("/*SITIO*/null", datos), ("/*INCLUYE*/null", incluye)):
    assert html.count(marca) == 1, marca
    html = html.replace(marca, json.dumps(valor, ensure_ascii=False))
open(salida, "w", encoding="utf-8").write(html)
print("guardado:", salida, f"({len(html) // 1024} KB) ·", sum(map(len, incluye)), "grupos de partidas en", len(CAPITULOS),
      "capítulos")
if len(sys.argv) > 3:   # versión para publicar como artifact: el esqueleto lo pone la plataforma
    art = re.sub(r"<!doctype html>\s*|<html[^>]*>\s*|</?head>\s*|<body>\s*|</body>\s*|</html>\s*|<meta charset[^>]*>\s*"
                 r"|<meta name=\"viewport\"[^>]*>\s*", "", html)
    open(sys.argv[3], "w", encoding="utf-8").write(art)
