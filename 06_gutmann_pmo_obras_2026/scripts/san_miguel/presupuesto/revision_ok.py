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

# ---------- contradicciones
r = fila("B.2.2")
ws.cell(r, 4).value = "=5.42*3.47-1.72*4.4"
ws.cell(r, 7).value = ("Contorno interior del bow window a escala: 5,42 ml × 3,47 m de piso a cielo (medido en terreno el 08-10-2026) = 18,81 m², "
    "menos su ventana (B.2.4: 1,72 × 4,40 = 7,57 m², se descuenta por ser significativa) = 11,24 m².")
r = fila("B.7.1")
ws.cell(r, 2).value = "Cierre de vano (ventana tapiada por shaft)"
ws.cell(r, 7).value = ("2 ventanas existentes que el plano dibuja tapiadas con muro nuevo y reemplazadas por un shaft de extracción forzada: "
    "B. Hombres 1 de 0,69 m y B. Mujeres 1 de 0,74 m de ancho, a escala.")
agregar("B.6.5", "Cierre de vano (ventana tapiada por shaft)", "UN", 1,
    "1 ventana existente de 0,74 m de ancho, a escala, que el plano dibuja tapiada con muro nuevo y reemplazada por un shaft de extracción forzada.")

# ---------- muro colindante
r = fila("F.3.3")
ws.cell(r, 7).value += " Va en metros lineales a pedido de Luciano: es el largo para la excavación, cuyo ancho y profundidad completa él."
agregar("F.3.3", "Retiro de escombros muro colindante", "m²", "=8*3.5",
    "Toda la pared del deslinde derecho: cota de 8,00 m del plano × 3,50 m de alto = 28,00 m².")

# ---------- faltantes 1: pintura sobre revestimiento (alto de piso a cielo 3,50 m, criterio acordado para alturas sin medir)
agregar("B.6.11", "Pintura de muros sobre revestimiento", "m²", "=16.5*(3.5-2)",
    "Sobre el revestimiento de B.6.11: 16,50 ml × (3,50 m de alto − 2,00 m de revestimiento) = 24,75 m². Alto de piso a cielo 3,50 m (criterio acordado; no medido en este recinto).")
for ref, ml, h0, nom in (("B.7.23", 6.88, 2, "B. Hombres 1"), ("B.7.15", 6.98, 2, "B. Mujeres 1"), ("B.7.11", 8.58, 2.14, "pasillo de los baños")):
    des = "Pintura de muros sobre revestimiento del pasillo de los baños" if nom.startswith("pasillo") else f"Pintura de muros sobre revestimiento — {nom}"
    tot = ml * (3.5 - h0)
    agregar(ref, des, "m²", f"={ml}*(3.5-{h0})",
        f"{nom[0].upper() + nom[1:]}: {str(ml).replace('.', ',')} ml × (3,50 m de alto − {str(h0).replace('.', ',')} m de revestimiento) = "
        f"{f'{tot:.2f}'.replace('.', ',')} m². Alto de piso a cielo 3,50 m (criterio acordado; no medido en este recinto).")
agregar("E.1.4", "Pintura de muros sobre revestimiento", "m²", "=14.46*(3.5-2)",
    "Sobre el revestimiento de E.1.4: 14,46 ml × (3,50 m de alto de los muros nuevos − 2,00 m de revestimiento) = 21,69 m².")
agregar("E.2.4", "Pintura de muros sobre revestimiento", "m²", "=7.8*(3.5-2)",
    "Sobre el revestimiento de E.2.4: 7,80 ml × (3,50 m de alto de los muros nuevos − 2,00 m de revestimiento) = 11,70 m².")

# ---------- faltantes 3: retiro del cielo viejo en el piso 2
for ref, f, txt in (("C.4.2", "=3.3*4.1", "Pieza 4: 3,30 × 4,10 = 13,53 m²"), ("C.5.2", "=3*2.5+2*2.4", "Pieza 5: 3,00 × 2,50 + 2,00 × 2,40 = 12,30 m²"),
                    ("C.6.3", "=3.2*2.2", "Baño: 3,20 × 2,20 = 7,04 m²"), ("C.7.2", "=2*1.4+1*3.6", "Pasillo: 2,00 × 1,40 + 1,00 × 3,60 = 6,40 m²")):
    agregar(ref, "Retiro escombros cielo", "m²", f, f"{txt}, igual que el cielo nuevo. Medidas interiores tomadas en terreno el 08-10-2026 (croquis del piso 2).")

# ---------- faltantes 2: baño del piso 2
c = agregar("C.6.12", "Pavimento (porcelanato o cerámica por confirmar)", "m²", "=3.2*2.2",
    "Baño: 3,20 × 2,20 = 7,04 m², igual que la cerámica de piso que se retira (C.6.3). Material por definir.")
c = agregar(c, "Artefactos sanitarios (WC, lavamanos, urinarios)", "UN", None,
    "Cantidad por contar en terreno: el croquis del piso 2 no la registra. El baño tiene 2 módulos (C.6.6).")
c = agregar(c, "Grifería", "UN", None, "Una llave por lavamanos: depende del conteo de artefactos.")
c = agregar(c, "Accesorios de baño", "GL", 1, "Partida global: dispensadores, espejos y basureros.")
c = agregar(c, "Instalación sanitaria", "GL", 1, "Partida global: agua y desagüe del baño del piso 2.")

# ---------- faltantes 4: pintura exterior de la obra nueva (caras hacia el exterior; sin muros de deslinde ni compartidos)
EXT = {
 "D.1": ("3.88", "muro izquierdo 3,88 (hacia el antejardín del frente); el superior es de deslinde, el inferior de la casona y el derecho compartido con el B. Mujeres 2"),
 "D.2": ("8.46+2.3+7.39+0.85", "superior 8,46 (hacia la franja del deslinde) + izquierdo 2,30 (a escala, el resto es compartido con el B. Universal) + inferior 7,39 + 0,85 (hacia el patio); el derecho es compartido con la Sala 03"),
 "D.3": ("4.6+9+4.6+4.28", "superior 4,60 + derecho 9,00 (hacia la franja del deslinde) + inferior 4,60 (pasillo hacia la Sala 04) + izquierdo 4,28 (hacia el patio)"),
 "D.4": ("9.45+4.2+8.38+0.85", "frente 9,45 + izquierdo 4,20 (pasillo entre salas 05 y 04) + superior 8,38 + 0,85; el derecho es de deslinde"),
 "D.5": ("6.07+4.2+8.93+0.85", "superior 6,07 (hacia el patio; el resto es de la casona) + derecho 4,20 (pasillo entre salas 05 y 04) + frente 8,93 + 0,85; el izquierdo es compartido con la Sala 06"),
 "D.6": ("8.93+0.85", "frente 8,93 + 0,85; el superior es de la casona y los laterales compartidos con las salas 05 y 07"),
 "D.7": ("4.38+3.29+1.43+3.37", "4,38 + 3,29 + 1,43 (ochavo) + 3,37, todos hacia el exterior; el derecho es compartido con la Sala 06"),
}
for s, (f, txt) in EXT.items():
    ml = eval(f)
    agregar("D.1.8" if s == "D.1" else f"{s}.6", "Pintura exterior de muros", "m²", f"=({f})*3.5",
        f"Caras exteriores de los muros nuevos, cotas del plano: {txt} = {f'{ml:.2f}'.replace('.', ',')} ml × 3,50 m = {f'{ml*3.5:.2f}'.replace('.', ',')} m². {SV}")
agregar("E.1.6", "Pintura exterior de muros", "m²", "=4.45*3.5",
    f"Cara exterior del muro inferior (hacia la circulación), cota del plano: 4,45 ml × 3,50 m = 15,58 m²; el superior es de deslinde y los laterales compartidos con la Sala 01 y el B. Universal. {SV}")
agregar("E.2.6", "Pintura exterior de muros", "m²", "=3.85*3.5",
    f"Caras exteriores de los muros del baño: 1,60 + 1,60 + 0,65 (a escala) = 3,85 ml × 3,50 m = 13,48 m². {SV}")

# ---------- faltantes 5: rampa junto al pórtico
agregar("B.9.1", "Rampa de acceso", "m²", "=1.55*2.15",
    "Rectángulo con triángulo junto al pórtico en el plano, de 1,55 × 2,15 m = 3,33 m², en el lado donde se demuele el murete de 2,15 m (B.9.1): se interpreta como rampa de acceso.")

# ---------- forma
REP = [("Restauracion", "Restauración"), ("Demolicion", "Demolición"), ("ceramica", "cerámica"), ("Ceramica", "Cerámica"),
       ("Excavacion", "Excavación"), ("portico", "pórtico"), ("balcon", "balcón"), ("tabiqueria", "tabiquería"),
       ("Separacion de modulos", "Separación de módulos"), ("Sector recepcion", "Sector recepción")]
for r in range(1, ws.max_row + 1):
    v = ws.cell(r, 2).value
    if isinstance(v, str):
        for a, b in REP: v = re.sub(rf"\b{a}\b", b, v)
        ws.cell(r, 2).value = v
r = fila("A.1.7")
ws.cell(r, 7).value = ("Superficie del terreno menos la huella de la casona, a escala en el plano San Miguel v5: terreno 39,50 × 20,00 = 790,00 m² "
    "menos el ochavo (4,00 m²) = 786,00 m²; huella de la casona piso 1 por el contorno exterior de sus muros: 14,30 × 8,35 = 119,41 − esquina abierta "
    "junto a la salida posterior 3,52 + chimenea 0,50 + bow window 4,91 = 121,30 m². Total 786,00 − 121,30 = 664,70 m².")
r = fila("A.1.8")
if not ws.cell(r, 7).value:
    ws.cell(r, 7).value = "Largo del muro de deslinde de la Sala 01, cota del plano: 10,50 ml. En metros lineales a pedido de Luciano, que completa ancho y profundidad."
for r in range(1, ws.max_row + 1):
    g = ws.cell(r, 7).value
    if isinstance(g, str) and g.startswith("=") and ws.cell(r, 1).value:
        ws.cell(r, 7).number_format = '#,##0.00 "m²"'

# ---------- Pendientes
p = wb["Pendientes"]
r6 = next(r for r in range(1, 40) if p.cell(r, 1).value == 6)
p.delete_rows(r6)
for r in range(r6, 40):
    if isinstance(p.cell(r, 1).value, (int, float)): p.cell(r, 1).value -= 1
n = max(r for r in range(1, 40) if isinstance(p.cell(r, 1).value, (int, float)))
for k in range(1, 4): p.cell(n + 1, k)._style = copy.copy(p.cell(n, k)._style)
p.cell(n + 1, 1).value = p.cell(n, 1).value + 1
p.cell(n + 1, 2).value = "Baño del piso 2 (C.6): cuántos WC, lavamanos y urinarios lleva (artefactos y grifería quedan sin cantidad)."
p.cell(n + 1, 3).value = "Gonza (terreno)"
# ---------- remapeo de códigos citados en observaciones
orig = openpyxl.load_workbook(src)["Presupuesto"]
def lista(h):
    return [(h.cell(r, 1).value, h.cell(r, 2).value, str(h.cell(r, 4).value)) for r in range(1, h.max_row + 1)
            if isinstance(h.cell(r, 1).value, str) and re.match(r"^[A-F]\.\d+\.\d+$", h.cell(r, 1).value)]
def norm(t):
    for a, b in REP: t = re.sub(rf"\b{a}\b", b, t or "")
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
