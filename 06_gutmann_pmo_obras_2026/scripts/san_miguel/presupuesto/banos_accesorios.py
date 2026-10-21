"""Baños: espejo como partida aparte (uno por lavamanos) e instalación de accesorios en cada baño
(audio de Luciano, 10-oct). Los dispensadores y basureros siguen en «Accesorios de baño»."""
import copy, re, sys, openpyxl
src, dst = sys.argv[1], sys.argv[2]
wb = openpyxl.load_workbook(src); ws = wb["Presupuesto"]
REF = re.compile(r"(\$?)([A-Z]{1,3})(\$?)(\d+)")
FUENTE = " (audio de Luciano, 10-10-2026)"


def fila(cod):
    return next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == cod)


def poner(cod, cant=None, obs=None, des=None):
    r = fila(cod)
    if cant is not None: ws.cell(r, 4).value = cant
    if obs is not None: ws.cell(r, 7).value = obs
    if des is not None: ws.cell(r, 2).value = des


def insertar_despues(cod, nuevo_des, uni, cant, obs):
    """Inserta una partida después de `cod` y corre los códigos siguientes del mismo sector."""
    r0 = fila(cod) + 1
    sector = cod.rsplit(".", 1)[0]
    n_ins = int(cod.rsplit(".", 1)[1]) + 1
    # renumerar códigos del sector desde n_ins, en la columna A y en todo texto que los cite
    mapa = {}
    r = r0
    while isinstance(ws.cell(r, 1).value, str) and ws.cell(r, 1).value.startswith(sector + "."):
        viejo = ws.cell(r, 1).value; n = int(viejo.rsplit(".", 1)[1])
        mapa[viejo] = f"{sector}.{n + 1}"; r += 1
    if mapa:
        pat = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(mapa, key=len, reverse=True)) + r")\b(?!\.\d)")
        for s in wb.worksheets:
            for row in s.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and not c.value.startswith("="):
                        c.value = pat.sub(lambda m: mapa[m.group(1)], c.value)
    # correr referencias a filas >= r0
    for s in wb.worksheets:
        for row in s.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    partes = re.split(r"('[^']*'![$A-Z0-9:]+)", c.value)
                    if s.title == "Presupuesto":
                        c.value = "".join(p if p.startswith("'") else REF.sub(
                            lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{int(m.group(4)) + 1 if int(m.group(4)) >= r0 else m.group(4)}", p) for p in partes)
                    elif "Presupuesto" in c.value:
                        sys.exit(f"referencia a Presupuesto en {s.title}!{c.coordinate}")
    alturas = {r: d.height for r, d in ws.row_dimensions.items()}
    ws.insert_rows(r0, 1)
    for r in sorted(alturas, reverse=True):
        if r >= r0: ws.row_dimensions[r + 1].height = alturas[r]
    cf = ws.conditional_formatting; nuevas = []
    for rng in list(cf):
        m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", str(rng.sqref))
        if m and int(m.group(4)) >= r0:
            a = int(m.group(2)); a = a + 1 if a >= r0 else a
            nuevas.append((f"{m.group(1)}{a}:{m.group(3)}{int(m.group(4)) + 1}", rng.rules)); del cf._cf_rules[rng]
    for s, rules in nuevas:
        for rule in rules: cf.add(s, rule)
    for k in range(1, 8): ws.cell(r0, k)._style = copy.copy(ws.cell(r0 - 1, k)._style)
    ws.row_dimensions[r0].height = alturas.get(r0 - 1)
    ws.cell(r0, 1).value = f"{sector}.{n_ins}"
    ws.cell(r0, 2).value, ws.cell(r0, 3).value, ws.cell(r0, 4).value = nuevo_des, uni, cant
    ws.cell(r0, 6).value = f"=D{r0}*E{r0}"; ws.cell(r0, 7).value = obs


# (código de «Accesorios de baño», sufijo del nombre, lavamanos del sector, extra de accesorios). De abajo hacia arriba,
# para que los códigos de más arriba no se corran antes de usarlos.
BANOS = [("E.2.11", "", 1, ["barras de apoyo"]),
         ("E.1.12", "", 5, []),
         ("C.6.16", "", 2, []),
         ("B.7.30", " — B. Hombres 1", 1, []),
         ("B.7.21", " — B. Mujeres 1", 1, []),
         ("B.6.16", "", 3, [])]


def lista(xs):
    return ", ".join(xs[:-1]) + " y " + xs[-1]


for acc, suf, lav, extra in BANOS:
    assert ws.cell(fila(acc), 2).value == "Accesorios de baño" + suf, ws.cell(fila(acc), 2).value
    sector = acc.rsplit(".", 1)[0]; n = int(acc.rsplit(".", 1)[1])
    accesorios = ["dispensadores", "basureros"] + extra
    insertar_despues(acc, "Instalación de accesorios y espejos" + suf, "GL", 1,
                     "Partida global: mano de obra de instalación de " + lista(accesorios + ["espejos"]) + FUENTE + ".")
    insertar_despues(acc, "Espejos" + suf, "UN", lav,
                     f"{lav}: uno por lavamanos, igual que la grifería. Partida aparte de los accesorios" + FUENTE + ".")
    poner(acc, obs=f"Partida global: {lista(accesorios)}. Los espejos van en {sector}.{n + 1} y la instalación en "
                   f"{sector}.{n + 2}" + FUENTE + ".")

assert str(ws["A5"].value).startswith("FECHA:  09-10-2026"), ws["A5"].value
ws["A5"].value = ws["A5"].value.replace("09-10-2026", "11-10-2026")
le = wb["Léeme"]
assert le["A3"].value.endswith("Actualizado el 09-10-2026."), le["A3"].value
le["A3"].value = le["A3"].value.replace("09-10-2026", "11-10-2026")

wb.save(dst)
print("CF", [str(x.sqref) for x in ws.conditional_formatting])
