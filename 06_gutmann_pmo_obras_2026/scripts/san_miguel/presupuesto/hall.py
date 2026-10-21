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

def agregar_antes(cod_ref, des, uni, cant, obs):
    sec, k = cod_ref.rsplit(".", 1); k = int(k)
    n = max(int(ws.cell(r, 1).value.rsplit(".", 1)[1]) for r in range(1, ws.max_row + 1)
            if isinstance(ws.cell(r, 1).value, str) and ws.cell(r, 1).value.startswith(sec + ".") and ws.cell(r, 1).value.count(".") == 2)
    for j in range(n, k - 1, -1): ws.cell(fila(f"{sec}.{j}"), 1).value = f"{sec}.{j + 1}"
    r = fila(f"{sec}.{k + 1}"); insertar(r, r + 1); item(r, cod_ref, des, uni, cant, "§" + obs)

HALL = "hall 15,12 + resto de la pieza del frente fuera de la Recepción 9,25 (a escala)"
# B.8 Hall: trabajos previos al inicio
agregar_antes("B.8.1", "Retiro escombros suelo", "m²", "=27.98",
    f"Igual que el relleno (B.8.2): {HALL} + pasillo de los baños 1 3,61 = 27,98 m².")
# terminaciones después del radier (B.8.3)
c = agregar("B.8.3", "Porcelanato", "m²", "=15.12+9.25",
    f"{HALL[0].upper() + HALL[1:]} = 24,37 m². El pasillo de los baños 1 lleva su pavimento en B.7.9. Material por definir.")
c = agregar(c, "Guardapolvo", "ml", "=4.31+4.76+4.31",
    f"Muros del hall a escala: izquierdo 4,31 (fachada, con la puerta de acceso) + inferior 4,76 + derecho 4,31 = 13,38 ml; el lado superior es abierto hacia la Recepción. {SV}")
c = agregar(c, "Restauración y pintura de cielo", "m²", "=15.12+9.25", "Igual que el pavimento: 24,37 m².")
c = agregar(c, "Restauración y pintura muros", "m²", "=(4.31+4.76+4.31)*3.36",
    f"13,38 ml (igual que el guardapolvo) × 3,36 m de piso a cielo, el mismo de la Recepción porque es la misma pieza del frente (medido en terreno el 08-10-2026) = 44,96 m². {SV}")
c = agregar(c, "Puerta de acceso principal (doble hoja)", "UN", 1,
    "1 puerta de dos hojas entre el pórtico de acceso y el hall, de unos 1,15 m de ancho a escala (plano).")
# B.3 tabique provisorio al final del sector
last = max((ws.cell(r, 1).value for r in range(1, ws.max_row + 1) if isinstance(ws.cell(r, 1).value, str) and ws.cell(r, 1).value.startswith("B.3.")),
           key=lambda x: int(x.rsplit(".", 1)[1]))
agregar(last, "Tabique provisorio", "m²", "=2.5*3",
    "Tabique provisorio entre la Recepción y el Estar de Alumnos, para una presentación al cliente: 2,50 × 3,00 m (medidas de Gonza) = 7,50 m².")
# Pendientes: el hall queda resuelto
p = wb["Pendientes"]
rh = next(r for r in range(1, 40) if isinstance(p.cell(r, 2).value, str) and p.cell(r, 2).value.startswith("Hall y circulación"))
p.delete_rows(rh)
for r in range(rh, 40):
    if isinstance(p.cell(r, 1).value, (int, float)): p.cell(r, 1).value -= 1
# ---------- remapeo de códigos citados en observaciones
orig = openpyxl.load_workbook(src)["Presupuesto"]
def lista(h):
    return [(h.cell(r, 1).value, h.cell(r, 2).value, str(h.cell(r, 4).value)) for r in range(1, h.max_row + 1)
            if isinstance(h.cell(r, 1).value, str) and re.match(r"^[A-F]\.\d+\.\d+$", h.cell(r, 1).value)]
def norm(t):
    pass
    return t
nuevos = lista(ws); usados = set(); mapa = {}
for cod, des, d in lista(orig):
    cand = [x for x in nuevos if x[0].rsplit(".", 1)[0] == cod.rsplit(".", 1)[0] and x[2] == d and x[0] not in usados
            and (x[1] == norm(des) or cod == "B.7.1")]
    if cod in ("B.2.2", "B.7.1"): cand = [x for x in nuevos if x[0] == cod]
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
