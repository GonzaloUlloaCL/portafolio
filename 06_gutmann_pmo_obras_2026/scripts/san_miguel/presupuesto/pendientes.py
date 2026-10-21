import copy, re, sys, openpyxl
src, dst = sys.argv[1], sys.argv[2]
wb = openpyxl.load_workbook(src); ws = wb["Presupuesto"]
REF = re.compile(r"(\$?)([A-Z]{1,3})(\$?)(\d+)")

def mover(desde, n):
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.startswith("="):
                partes = re.split(r"('[^']*'![$A-Z0-9:]+)", c.value)
                c.value = "".join(p if p.startswith("'") else REF.sub(
                    lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{int(m.group(4)) + n if int(m.group(4)) >= desde else m.group(4)}", p) for p in partes)
    alt = {r: d.height for r, d in ws.row_dimensions.items()}
    cf = ws.conditional_formatting; nuevas = []
    for rng in list(cf):
        m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", str(rng.sqref))
        if m and int(m.group(4)) >= desde:
            nuevas.append((f"{m.group(1)}{m.group(2)}:{m.group(3)}{int(m.group(4)) + n}", rng.rules)); del cf._cf_rules[rng]
    for s, rules in nuevas:
        for rule in rules: cf.add(s, rule)
    return alt

def insertar(f, molde):
    """Inserta una fila en f con el estilo de la fila molde (antes de insertar)."""
    est = [copy.copy(ws.cell(molde, k)._style) for k in range(1, 8)]; h = ws.row_dimensions[molde].height
    alt = mover(f, 1); ws.insert_rows(f)
    for r in sorted(alt, reverse=True):
        if r >= f: ws.row_dimensions[r + 1].height = alt[r]
    for k in range(7): ws.cell(f, k + 1)._style = est[k]
    ws.row_dimensions[f].height = h

def borrar(f):
    alt = mover(f + 1, -1); ws.delete_rows(f)
    for r in sorted(alt):
        if r > f: ws.row_dimensions[r - 1].height = alt[r]

def fila(v, col=1): return next(r for r in range(1, ws.max_row + 1) if ws.cell(r, col).value == v)
def item(r, cod, des, uni, cant, obs):
    for k, v in enumerate((cod, des, uni, cant), 1): ws.cell(r, k).value = v
    ws.cell(r, 6).value = f"=D{r}*E{r}"; ws.cell(r, 7).value = obs

SV = "Sin descontar puertas ni ventanas (criterio acordado con el mandante)."
def agregar(cod_ref, des, uni, cant, obs):
    """Inserta una partida después de cod_ref, en su mismo sector, y corre los códigos siguientes."""
    sec, k = cod_ref.rsplit(".", 1); k = int(k)
    n = max(int(ws.cell(r, 1).value.rsplit(".", 1)[1]) for r in range(1, ws.max_row + 1)
            if isinstance(ws.cell(r, 1).value, str) and ws.cell(r, 1).value.startswith(sec + ".") and ws.cell(r, 1).value.count(".") == 2)
    for j in range(n, k, -1): ws.cell(fila(f"{sec}.{j}"), 1).value = f"{sec}.{j + 1}"
    r = fila(cod_ref); insertar(r + 1, r); item(r + 1, f"{sec}.{k + 1}", des, uni, cant, "§" + obs)
    return f"{sec}.{k + 1}"

H = "Alto de piso a cielo 3,30 m, el estándar de la casona (Gonza, 09-10-2026)."
def set_(cod, d, g):
    r = fila(cod); ws.cell(r, 4).value = d; ws.cell(r, 7).value = "§" + g
set_("B.5.7", "=(0.75+1.05+1.3)*3.3", f"Muros nuevos (rosados) de la Oficina 01, a escala: 0,75 + 1,05 + 1,30 = 3,10 ml × 3,30 m = 10,23 m². {H} Material por definir.")
set_("B.6.7", "=(1.65+2.05)*3.3", f"Muros nuevos (rosados) del B. Hombres 2, a escala: 1,65 + 2,05 = 3,70 ml × 3,30 m = 12,21 m². {H} Material por definir.")
set_("B.6.12", "=16.5*(3.3-2)", f"Sobre el revestimiento de B.6.11: 16,50 ml × (3,30 − 2,00 m de revestimiento) = 21,45 m². {H}")
set_("B.7.12", "=8.58*(3.3-2.14)", f"Pasillo de los baños: 8,58 ml × (3,30 − 2,14 m de revestimiento) = 9,95 m². {H}")
set_("B.7.17", "=6.98*(3.3-2)", f"B. Mujeres 1: 6,98 ml × (3,30 − 2,00 m de revestimiento) = 9,07 m². {H}")
set_("B.7.26", "=6.88*(3.3-2)", f"B. Hombres 1: 6,88 ml × (3,30 − 2,00 m de revestimiento) = 8,94 m². {H}")
r = fila("C.7.6"); ws.cell(r, 7).value = "§1 escalera a restaurar: la interior de madera, la única que sube al piso 2 (confirmado por Gonza el 09-10-2026)."
r = fila("F.3.2"); ws.cell(r, 7).value = "§1 portón: el del frente a Gran Avenida, de unos 2,70 m. El portón de la calle lateral (2,50 m) va aparte, en la partida siguiente."
agregar("F.3.2", "Reparación portón calle lateral", "UN", 1,
    "Portón de la calle lateral, 2,50 m (plano). Se deja como reparación por unidad para que Luciano decida si se repara, se cambia o se cierra.")
# Pendientes
p = wb["Pendientes"]
for txt in ("Elevaciones, cortes", "Plazo estimado", "Reparación de muro de la pieza 2", "Portón vehicular", "Escalera a restaurar", "Pilares y viga del vano"):
    rr = next(r for r in range(1, 40) if isinstance(p.cell(r, 2).value, str) and p.cell(r, 2).value.startswith(txt))
    p.delete_rows(rr)
k = 0
for r in range(4, 40):
    if isinstance(p.cell(r, 1).value, (int, float)):
        k += 1; p.cell(r, 1).value = k
    if isinstance(p.cell(r, 2).value, str) and p.cell(r, 2).value.startswith("Tabiquería interior"):
        p.cell(r, 2).value = "Tabiquería interior de la casona (Oficina 01 y B. Hombres 2): material. Largo del plano y alto 3,30 m ya cargados."
        p.cell(r, 4).value = "B.5.7 y B.6.7"; p.cell(r, 5).value = None
# ---------- remapeo de códigos citados en observaciones
orig = openpyxl.load_workbook(src)["Presupuesto"]
def lista(h):
    return [(h.cell(r, 1).value, h.cell(r, 2).value, str(h.cell(r, 4).value)) for r in range(1, h.max_row + 1)
            if isinstance(h.cell(r, 1).value, str) and re.match(r"^[A-F]\.\d+\.\d+$", h.cell(r, 1).value)]
def norm(t):
    return t
nuevos = lista(ws); usados = set(); mapa = {}
for cod, des, d in lista(orig):
    cand = [x for x in nuevos if x[0].rsplit(".", 1)[0] == cod.rsplit(".", 1)[0] and x[2] == d and x[0] not in usados
            and (x[1] == norm(des) or cod == "B.7.1")]
    if not cand: cand = [x for x in nuevos if x[0] == cod and x[1] == des]
    assert cand, (cod, des, d)
    mapa[cod] = cand[0][0]; usados.add(cand[0][0])
cambia = {a: b for a, b in mapa.items() if a != b}
print("renumeradas", len(cambia))
for r in range(1, ws.max_row + 1):
    g = ws.cell(r, 7).value
    if isinstance(g, str) and g.startswith("§"):
        ws.cell(r, 7).value = g[1:]; continue
    if isinstance(g, str) and not g.startswith("="):
        h = re.sub(r"\b([A-F]\.\d+\.\d+)\b", lambda m: cambia.get(m.group(1), m.group(1)), g)
        if h != g: print("ref", ws.cell(r, 1).value, re.findall(r"\b[A-F]\.\d+\.\d+\b", g), "->", re.findall(r"\b[A-F]\.\d+\.\d+\b", h)); ws.cell(r, 7).value = h
wb.save(dst); print("ok")
