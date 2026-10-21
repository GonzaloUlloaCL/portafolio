"""Arma el tour de San Miguel (película + caminata en dos pisos) sobre la base de Talca: la geometría de sitio.json
(armar_sitio.py, láminas A04 y A05) y, por capítulo, «Qué incluye» con las partidas del presupuesto enviado, agrupadas
por nombre. Sin precios ni cantidades. Uso: python armar_tour.py <presupuesto.xlsx> <sitio.json> <salida.html>"""
import json, os, re, sys
import openpyxl
AQUI = os.path.dirname(os.path.abspath(__file__))
excel, sitio, salida = sys.argv[1:4]
DEMO = re.compile(r"emoli|scombro|esarme|Tala|Trazado")
# un capítulo por etapa de la obra, en el orden del tour (cada partida va al primero que la toma); el último es el resumen
def z(p): return lambda c, d: c.startswith(p)
CAPITULOS = [lambda c, d: c.startswith("A.") and not DEMO.search(d), lambda c, d: bool(re.search(r"Tala|emoli|scombro|esarme", d)), lambda c, d: "Trazado" in d,
             z("F.1."), lambda c, d: c.startswith("D."), lambda c, d: c.startswith("E."),
             lambda c, d: c.startswith("F.2.") and "fachada" not in d, lambda c, d: c.startswith("B."),
             lambda c, d: c.startswith("C.") and not c.startswith("C.8."),
             lambda c, d: c.startswith("C.8.") or (c.startswith("F.2.") and "fachada" in d), z("F.4."), z("F.3."), z("F.5.")]
ws = openpyxl.load_workbook(excel, read_only=True)["Presupuesto"]
sector, filas = {}, []
for r in ws.iter_rows(values_only=True):
    b = str(r[1] or "")
    m = re.match(r"^([A-F]\.\d+) (?:Sector )?(.+)$", b)
    if m and not r[0]: sector[m.group(1)] = m.group(2)[0].upper() + m.group(2)[1:]
    if isinstance(r[0], str) and re.match(r"^[A-F]\.\d+\.\d+$", r[0]) and r[1]: filas.append((r[0], str(r[1]).strip()))
incluye = []
tomadas = set()
for regla in CAPITULOS:
    grupos = {}
    for c, d in filas:
        if c not in tomadas and regla(c, d): tomadas.add(c); grupos.setdefault(d, []).append(sector.get(c.rsplit(".", 1)[0], ""))
    incluye.append([d + (": " + ", ".join(dict.fromkeys(s for s in lugares if s)) if len(lugares) > 1 else "") for d, lugares in grupos.items()])
html = open(os.path.join(AQUI, "tour_sm_plantilla.html"), encoding="utf-8").read()
for marca, valor in (("/*SITIO*/null", json.load(open(sitio))), ("/*INCLUYE*/null", incluye)):
    assert html.count(marca) == 1, marca
    html = html.replace(marca, json.dumps(valor, ensure_ascii=False, separators=(",", ":")))
open(salida, "w", encoding="utf-8").write(html)
print("guardado:", salida, f"({len(html)//1024} KB) ·", sum(map(len, incluye)), "grupos de partidas")

print("partidas sin capítulo:", [c for c, d in filas if c not in tomadas])
