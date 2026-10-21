"""Salas D y baños E: albañilería de 3,50 m solo en el muro colindante; el resto es tabiquería de 3,00 m,
y el cielo y la pintura bajan a 3,00 m. Recepción: sus 3 ventanas se cierran con tabiquería (Gonza, 09-oct)."""
import copy, re, sys, openpyxl
src, dst = sys.argv[1], sys.argv[2]
wb = openpyxl.load_workbook(src); ws = wb["Presupuesto"]
REF = re.compile(r"(\$?)([A-Z]{1,3})(\$?)(\d+)")
NOTA = " Sin descontar puertas ni ventanas (criterio acordado con el mandante)."
FUENTE = " (Gonza, 09-10-2026)"


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


TAB = "Tabiquería (h = 3,00 m)"
ALT = "Muros de albañilería nuevos (h = 3,50 m)"
H = " × 3,00 m (alto de la tabiquería y del cielo, Gonza 09-10-2026)"

# --- Recepción: las 3 ventanas se cierran con tabiquería ---
poner("B.1.4", des="Cierre de vanos con tabiquería", cant="=(1*1.72)+(1*1.72)+(1.82*1.05)",
      obs="Las 3 ventanas de la Recepción se cierran con tabiquería" + FUENTE + "; ya no se restauran. Medidas de las "
          "mismas ventanas: 1,00 × 1,72 + 1,00 × 1,72 + 1,82 × 1,05 = 5,35 m². El bow window (B.2) se mantiene.")
ws.cell(fila("B.1.4"), 3).value = "m²"

# --- Salas y baños: primero lo que no inserta filas ---
poner("D.1.8", cant="=28.76*3", obs="Perímetro por cotas del plano (10,50 × 3,88): 28,76 ml" + H + " = 86,28 m² (cara interior)." + NOTA)
poner("D.1.9", cant="=(3.88)*3", obs="Cara exterior del muro izquierdo, tabiquería, cota del plano: 3,88 ml (hacia el antejardín del frente); el superior es de deslinde, el inferior de la casona y el derecho compartido con el B. Mujeres 2 = 3,88 ml" + H + " = 11,64 m²." + NOTA)

poner("D.2.1", des=TAB, cant="=25.9*3", obs="La sala no tiene muro colindante (el plano la separa del deslinde por una franja): todos sus muros nuevos son tabiquería" + FUENTE + ". Cotas del plano, cada muro compartido una sola vez: 8,46 + 4,60 + 4,60 (compartido con la Sala 03) + 7,39 + 0,85 = 25,90 ml × 3,00 m = 77,70 m²." + NOTA)
poner("D.2.6", cant="=26.12*3", obs="Perímetro por cotas del plano (8,46 × 4,60): 26,12 ml" + H + " = 78,36 m² (cara interior)." + NOTA)
poner("D.2.7", cant="=(8.46+2.3+7.39+0.85)*3", obs="Caras exteriores de los tabiques, cotas del plano: superior 8,46 (hacia la franja del deslinde) + izquierdo 2,30 (a escala, el resto es compartido con el B. Universal) + inferior 7,39 + 0,85 (hacia el patio); el derecho es compartido con la Sala 03 = 19,00 ml" + H + " = 57,00 m²." + NOTA)

poner("D.3.1", des=TAB, cant="=22.48*3", obs="La sala no tiene muro colindante (el plano la separa del deslinde por una franja): todos sus muros nuevos son tabiquería" + FUENTE + ". Cotas del plano: 4,60 + 9,00 + 4,60 + 4,28; el tramo superior del muro izquierdo se cuenta en la Sala 02 = 22,48 ml × 3,00 m = 67,44 m²." + NOTA)
poner("D.3.6", cant="=27.2*3", obs="Perímetro por cotas del plano (4,60 × 9,00): 27,20 ml" + H + " = 81,60 m² (cara interior)." + NOTA)
poner("D.3.7", cant="=(4.6+9+4.6+4.28)*3", obs="Caras exteriores de los tabiques, cotas del plano: superior 4,60 + derecho 9,00 (hacia la franja del deslinde) + inferior 4,60 (pasillo hacia la Sala 04) + izquierdo 4,28 (hacia el patio) = 22,48 ml" + H + " = 67,44 m²." + NOTA)

poner("D.4.6", cant="=27.3*3", obs="Perímetro por cotas del plano (9,45 × 4,20): 27,30 ml" + H + " = 81,90 m² (cara interior)." + NOTA)
poner("D.4.7", cant="=(9.45+4.2+8.38+0.85)*3", obs="Caras exteriores de los tabiques, cotas del plano: frente 9,45 + izquierdo 4,20 (pasillo entre salas 05 y 04) + superior 8,38 + 0,85; el derecho es de deslinde = 22,88 ml" + H + " = 68,64 m²." + NOTA)

poner("D.5.1", des=TAB, cant="=24.25*3", obs="La sala no tiene muro colindante (da al patio y a la línea oficial): todos sus muros nuevos son tabiquería" + FUENTE + ". Cotas del plano: 6,07 (a escala) + 4,20 + 8,93 + 0,85 + 4,20 (compartido con la Sala 06); el resto del muro superior es de la casona = 24,25 ml × 3,00 m = 72,75 m²." + NOTA)
poner("D.5.6", cant="=28.4*3", obs="Perímetro por cotas del plano (10,00 × 4,20): 28,40 ml" + H + " = 85,20 m² (cara interior)." + NOTA)
poner("D.5.7", cant="=(6.07+4.2+8.93+0.85)*3", obs="Caras exteriores de los tabiques, cotas del plano: superior 6,07 (hacia el patio; el resto es de la casona) + derecho 4,20 (pasillo entre salas 05 y 04) + frente 8,93 + 0,85; el izquierdo es compartido con la Sala 06 = 20,05 ml" + H + " = 60,15 m²." + NOTA)

poner("D.6.1", des=TAB, cant="=13.98*3", obs="La sala no tiene muro colindante (da al patio y a la línea oficial): todos sus muros nuevos son tabiquería" + FUENTE + ". Cotas del plano: 4,20 (compartido con la Sala 07) + 8,93 + 0,85; el muro superior es el existente de la casona = 13,98 ml × 3,00 m = 41,94 m²." + NOTA)
poner("D.6.6", cant="=28.4*3", obs="Perímetro por cotas del plano (10,00 × 4,20): 28,40 ml" + H + " = 85,20 m² (cara interior)." + NOTA)
poner("D.6.7", cant="=(8.93+0.85)*3", obs="Caras exteriores de los tabiques, cotas del plano: frente 8,93 + 0,85; el superior es de la casona y los laterales compartidos con las salas 05 y 07 = 9,78 ml" + H + " = 29,34 m²." + NOTA)

poner("D.7.1", des=TAB, cant="=12.47*3", obs="La sala no tiene muro colindante (da a la línea oficial): todos sus muros nuevos son tabiquería" + FUENTE + ". Cotas del plano: 4,38 + 3,29 + 1,43 (ochavo) + 3,37; el muro derecho se cuenta en la Sala 06 = 12,47 ml × 3,00 m = 37,41 m²." + NOTA)
poner("D.7.6", cant="=16.77*3", obs="Perímetro por cotas del plano: 4,38 + 3,29 + 1,43 (ochavo) + 3,37 + 4,30 = 16,77 ml" + H + " = 50,31 m² (cara interior)." + NOTA)
poner("D.7.7", cant="=(4.38+3.29+1.43+3.37)*3", obs="Caras exteriores de los tabiques, cotas del plano: 4,38 + 3,29 + 1,43 (ochavo) + 3,37, todos hacia el exterior; el derecho es compartido con la Sala 06 = 12,47 ml" + H + " = 37,41 m²." + NOTA)

poner("E.1.5", cant="=14.46*(3-2)", obs="Sobre el revestimiento de E.1.4: 14,46 ml × (3,00 m de alto del cielo − 2,00 m de revestimiento) = 14,46 m².")
poner("E.1.7", cant="=4.45*3", obs="Cara exterior del tabique inferior (hacia la circulación), cota del plano: 4,45 ml" + H + " = 13,35 m²; el superior es de deslinde y los laterales compartidos con la Sala 01 y el B. Universal." + NOTA)

poner("E.2.1", des=TAB, cant="=3.85*3", obs="El baño no tiene muro colindante: todos sus muros nuevos son tabiquería" + FUENTE + ". 1,60 + 1,60 + 0,65 (a escala) = 3,85 ml × 3,00 m = 11,55 m²; el muro derecho se cuenta en la Sala 02." + NOTA)
poner("E.2.5", cant="=7.8*(3-2)", obs="Sobre el revestimiento de E.2.4: 7,80 ml × (3,00 m de alto del cielo − 2,00 m de revestimiento) = 7,80 m².")
poner("E.2.7", cant="=3.85*3", obs="Caras exteriores de los tabiques del baño: 1,60 + 1,60 + 0,65 (a escala) = 3,85 ml" + H + " = 11,55 m²." + NOTA)

# --- Las tres con muro colindante: fila nueva de tabiquería y albañilería solo en el colindante (de abajo hacia arriba;
#     la observación va después de insertar, para que la renumeración no corra la cita a la fila nueva) ---
insertar_despues("E.1.1", TAB, "m²", "=(2.78+4.45)*3",
                 "Muros nuevos del baño que no son colindantes, cotas del plano: 2,78 + 4,45 = 7,23 ml × 3,00 m = 21,69 m²; el muro izquierdo se cuenta en la Sala 01" + FUENTE + "." + NOTA)
poner("E.1.1", cant="=4.45*3.5", obs="Solo el muro colindante (deslinde, rotulado «muro de albañilería h = 3,50 m» en el plano): 4,45 ml × 3,50 m = 15,58 m². El resto de los muros del baño es tabiquería (E.1.2)" + FUENTE + ".")
insertar_despues("D.4.1", TAB, "m²", "=(9.45+4.2+8.38+0.85)*3",
                 "Muros nuevos de la sala que no son colindantes, cotas del plano: 9,45 + 4,20 + 8,38 + 0,85 = 22,88 ml × 3,00 m = 68,64 m²" + FUENTE + "." + NOTA)
poner("D.4.1", cant="=4.2*3.5", obs="Solo el muro colindante (deslinde derecho, rotulado «muro de albañilería h = 3,50 m» en el plano): 4,20 ml × 3,50 m = 14,70 m². El resto de los muros de la sala es tabiquería (D.4.2)" + FUENTE + ".")
insertar_despues("D.1.3", TAB, "m²", "=(3.88+3.88)*3",
                 "Muros nuevos de la sala que no son colindantes, cotas del plano: 3,88 + 3,88 = 7,76 ml × 3,00 m = 23,28 m²; el muro inferior es el existente de la casona" + FUENTE + "." + NOTA)
poner("D.1.3", cant="=10.5*3.5", obs="Solo el muro colindante (deslinde superior, rotulado «muro de albañilería h = 3,50 m» en el plano): 10,50 ml × 3,50 m = 36,75 m². El resto de los muros de la sala es tabiquería (D.1.4)" + FUENTE + ".")

# --- Generales, Léeme y Pendientes ---
r = fila("F.1.1")
ws.cell(r, 7).value = ("Partida global; no hay plano de fundaciones. Corresponde a 159,95 ml de muros nuevos (7 salas, B. Mujeres 2 y "
                       "B. Universal): 19,15 ml de albañilería en los muros colindantes (Sala 01 10,50 + B. Mujeres 2 4,45 + Sala 04 "
                       "4,20) y 140,80 ml de tabiquería.")
le = wb["Léeme"]
assert le["A18"].value.startswith("• Muros nuevos: 3,50 m"), le["A18"].value
le["A18"].value = ("• Muros nuevos de salas y baños: albañilería de 3,50 m solo en el muro colindante (Sala 01, B. Mujeres 2 y Sala 04); "
                   "el resto de los muros rosados es tabiquería de 3,00 m, y el cielo y la pintura van a 3,00 m" + FUENTE + ". "
                   "Muros rosados del plano = obra nueva (confirmado por Luciano).")
pe = wb["Pendientes"]
assert pe["B6"].value.startswith("Tabiquería interior de la casona"), pe["B6"].value
pe["B6"].value = ("Tabiquería: material. Casona (Oficina 01 y B. Hombres 2, alto 3,30 m), salas y baños nuevos (alto 3,00 m) y "
                  "cierre de las ventanas de la Recepción. Largos y altos ya cargados.")
pe["D6"].value = "B.1.4, B.5.7, B.6.7, D.1.4, D.2.1, D.3.1, D.4.2, D.5.1, D.6.1, D.7.1, E.1.2 y E.2.1"

wb.save(dst)
print("CF", [str(x.sqref) for x in ws.conditional_formatting])
