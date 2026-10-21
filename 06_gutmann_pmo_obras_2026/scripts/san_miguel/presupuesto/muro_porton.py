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

# --- F.3: observaciones de las partidas de Gonza
r = fila("F.3.5"); assert ws.cell(r, 2).value == "Pintura muro exterior (completo)" and ws.cell(r, 7).value is None
ws.cell(r, 7).value = ("Muro hacia la calle, del frente al portón de la calle lateral: pared baja de 37,02 ml (tramos de reja 32,97 + bajo 9 pilares 9 × 0,45) "
    "× 0,90 m de alto, por ambas caras = 66,64; remate 32,97 × 0,37 = 12,20; 9 pilares sobre la pared 9 × (4 × 0,45 × 1,70 + remate 0,45 × 0,45) = 29,36; "
    "pilar aislado junto al portón del frente 4 × 0,45 × 2,60 + remate = 4,88; subtotal 113,08 m². Tramo final, del portón de la calle lateral (2,50 m) "
    "a la esquina: 13,51 ml × 3,37 m = 45,53 m² por cara, pintado por fuera y por dentro = 91,06 m². Total 204,14 m². Largos a escala en el plano "
    "San Miguel v5; alturas y secciones medidas en terreno.")
r = fila("F.3.6"); assert ws.cell(r, 2).value.startswith("Estuco interior muro") and ws.cell(r, 7).value is None
ws.cell(r, 7).value = ("Cara interior del tramo final del muro de la calle lateral: 13,51 ml a escala × 3,37 m de alto (terreno) = 45,53 m². "
    "Lleva estuco porque hoy tiene adosado un cuarto que se demuele.")

# --- D.1: portón de la Sala 01 como trabajos previos
for j in range(12, 0, -1):
    try: ws.cell(fila(f"D.1.{j}"), 1).value = f"D.1.{j + 2}"
    except StopIteration: pass
s = fila("D.1 Sector sala de capacitación 01", 2); molde = s + 1
NUEVOS_D = [
 ("D.1.1", "Demolición muro portón existente", "m³", "=1.81*0.21*2.6",
  "Muro del portón en el costado izquierdo de la Sala 01: 1,81 × 0,21 × 2,60 m = 0,99 m³ (medidas de terreno)."),
 ("D.1.2", "Retiro escombros cortina roller", "m²", "=2.1*2.45",
  "Cortina roller del portón: 2,10 × 2,45 m = 5,15 m² (medidas de terreno)."),
]
for k, it in enumerate(NUEVOS_D):
    insertar(s + 1 + k, molde + k); item(s + 1 + k, *it)
wb.save(dst); print("ok")
