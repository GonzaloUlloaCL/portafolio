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

# --- F.2: techumbre de la circulación del patio, después de Aleros
r = fila("F.2.1")
ws.cell(r, 4).value = 52.32
ws.cell(r, 7).value = ("Según las líneas «proy. alero techumbre» del plano: pasillo frontal 23,21 × 1,60 = 37,14; vuelo hacia el patio central "
    "(8,50 × 2,70 − hueco de 8,10 × 1,90) = 7,56; borde de 0,20 m de las salas 02 y 03 (14,90 + 11,10 ml) = 5,20; borde de 0,20 m del frente "
    "de las salas fuera del pasillo (7,80 + 4,32 ml) = 2,42. Total 52,32 m². La losa del pórtico de acceso (2,60 × 4,60 = 11,96 m²) va como techumbre en B.9.2.")
for cod in ("F.2.5", "F.2.4", "F.2.3", "F.2.2"):
    ws.cell(fila(cod), 1).value = cod[:-1] + str(int(cod[-1]) + 1)
insertar(r + 1, r)
item(r + 1, "F.2.2", "Techumbre de la circulación del patio central", "m²", 37.1,
    "Pasillo de 1,50 m alrededor del patio central, techado hasta los pilares de 0,10 m con baranda: 37,10 m² a escala, la misma superficie "
    "que la «circulación alrededor del patio» de F.3.4. El vuelo de 0,40 m hacia el patio va en F.2.1. Superficie en planta, sin pendiente.")

# --- E.2 y E.1: techumbre después de los muros
for sec, area, txt in (("E.2", "=1.6*2.3", "B. Universal, cotas del plano: 1,60 × 2,30 = 3,68 m²"),
                       ("E.1", "=4.45*2.78", "B. Mujeres 2, cotas del plano: 4,45 × 2,78 = 12,37 m²")):
    for j in range(10, 1, -1):
        ws.cell(fila(f"{sec}.{j}"), 1).value = f"{sec}.{j + 1}"
    r = fila(f"{sec}.1"); insertar(r + 1, r)
    item(r + 1, f"{sec}.2", "Techumbre", "m²", area,
         f"{txt}, igual que el pavimento. Superficie en planta, mismo criterio que las salas: la cubierta real suma espesor de muros y pendiente (no hay corte del techo).")

# --- B.9: orden trabajos previos → obra gruesa → terminaciones
r1 = fila("B.9.1")
assert ws.cell(r1 + 1, 2).value.startswith("Pintura muretes") and ws.cell(r1 + 2, 2).value.startswith("Pintura pilares")
assert ws.cell(r1 + 3, 2).value is None and ws.cell(r1 + 4, 2).value is None and ws.cell(r1 + 4, 1).value == "B.9.5"
mur = [ws.cell(r1 + 1, k).value for k in range(2, 5)]; pil = [ws.cell(r1 + 2, k).value for k in range(2, 5)]
ws.cell(r1, 7).value = ("Murete derecho del pórtico, que se demuele para dar espacio a la Sala 07 (hasta antes del pilar): "
    "0,32 × 0,80 × 2,15 = 0,55 m³ (medidas tomadas en terreno).")
item(r1 + 1, "B.9.2", "Techumbre pórtico de acceso", "m²", "=2.6*4.6",
    "Losa del pórtico según la línea «proy. alero techumbre» del plano: 2,60 × 4,60 = 11,96 m² (antes contada en F.2.1 Aleros).")
item(r1 + 2, "B.9.3", *mur,
    "Murete que se conserva, 2,17 m de largo × 0,80 m de alto × 0,27 m de espesor (terreno): dos caras 2 × 2,17 × 0,80 = 3,47 + remate 2,17 × 0,27 = 0,59 = 4,06 m².")
item(r1 + 3, "B.9.4", *pil,
    "2 pilares de 0,34 m por lado (terreno) × 4 caras × 3,50 m de alto = 9,52 m².")
borrar(r1 + 4)
t = fila("TOTAL B", 2); ws.cell(t, 6).value = f"=SUM(F21:F{t - 1})"

# --- Pendientes y Léeme
p = wb["Pendientes"]; n = max(r for r in range(1, 60) if isinstance(p.cell(r, 1).value, (int, float)))
for k in range(1, 4): p.cell(n + 1, k)._style = copy.copy(p.cell(n, k)._style)
p.cell(n + 1, 1).value = p.cell(n, 1).value + 1
p.cell(n + 1, 2).value = "Techumbre de la casona (C.8.1, 117,86 m² en planta): ¿reparación o reemplazo? Su alero no figura en el plano."
p.cell(n + 1, 3).value = "Luciano"
l = wb["Léeme"]
l.cell(6, 1).value += " Única excepción: la techumbre de la casona (C.8.1) es una sola partida para todos sus recintos, porque es una sola cubierta."
l.cell(14, 1).value = "• El piso 2 de la casona está desglosado por pieza con el croquis (C.1 a C.7). Cada pieza lleva su pavimento (terciado y piso flotante); el baño del piso 2 es el sector C.6."
wb.save(dst); print("ok")
