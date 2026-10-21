"""Arma el presupuesto de la obra San Miguel sala por sala, sobre el formato estándar de Luciano.

Uso: python -I armar_sala_por_sala.py <formato_luciano.xlsx> <salida.xlsx>
Los precios unitarios quedan vacíos: son de Luciano.
"""
import copy
import sys

import openpyxl
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

PLANTILLA, SALIDA = sys.argv[1], sys.argv[2]
tpl = openpyxl.load_workbook(PLANTILLA).active
CB = "'Cubicación base'"


def relleno(rgb):
    # Opaco y con inicio = fin: si no, Google Sheets pinta la celda negra.
    return PatternFill(fill_type="solid", start_color=rgb, end_color=rgb)


SIN_RELLENO = PatternFill(fill_type=None)


def tomar(coord):
    c = tpl[coord]
    fill = SIN_RELLENO
    if c.fill is not None and c.fill.fill_type == "solid":
        fill = relleno(c.fill.start_color.rgb)
    return dict(font=copy.copy(c.font), fill=fill, border=copy.copy(c.border),
                alignment=copy.copy(c.alignment), number_format=c.number_format)


FILAS_TIPO = {"cab": 9, "sub": 10, "item": 11, "tot": 19, "res": 222, "res_blanco": 223, "res_tot": 232}
EST = {nombre: {col: tomar(f"{col}{fila}") for col in "ABCDEF"} for nombre, fila in FILAS_TIPO.items()}
TIT = {k: tomar(k) for k in ("A1", "A3", "A4", "A6", "D7")}
FUENTE = copy.copy(tpl["B11"].font)  # Calibri Light 9


def poner(ws, coord, valor, est=None, **cambios):
    c = ws[coord]
    c.value = valor
    if est:
        c.font, c.fill, c.border = est["font"], est["fill"], est["border"]
        c.alignment, c.number_format = est["alignment"], est["number_format"]
    for k, v in cambios.items():
        setattr(c, k, v)
    return c


def n(x):
    return f"{x:.2f}".replace(".", ",")


ESTADOS = {  # relleno, color de letra
    "Plano": ("FFC6EFCE", "FF006100"),
    "Supuesto": ("FFDDEBF7", "FF1F4E79"),
    "Pendiente": ("FFFFEB9C", "FF9C5700"),
    "Confirmado": ("FFA9D08E", "FF375623"),
    "Global": ("FFEDEDED", "FF404040"),
}
FINO = Side(style="thin", color="FFBFBFBF")
BORDE_FINO = Border(left=FINO, right=FINO, top=FINO, bottom=FINO)
GRIS = relleno("FFBFBFBF")

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Sede San Miguel"
leeme = wb.create_sheet("Léeme")
cb = wb.create_sheet("Cubicación base")
pend = wb.create_sheet("Pendientes")

# ---------------------------------------------------------------- Cubicación base
f9 = Font(name=FUENTE.name, size=9)
f9b = Font(name=FUENTE.name, size=9, bold=True)
cb["A1"].value = "CUBICACIÓN BASE — medidas por recinto tomadas del plano San Miguel v5 (piso 1)"
cb["A1"].font = Font(name=FUENTE.name, size=12, bold=True)
cb["A2"].value = ("Si corriges una medida aquí, se actualiza el presupuesto. «Plano» = cota o superficie rotulada; "
                  "«a escala» = medido sobre el dibujo, sin cota: validar en terreno.")
cb["A2"].font = Font(name=FUENTE.name, size=9, italic=True)

CAB_REC = ["Recinto", "Tipo", "Sup. útil rotulada (m²)", "Largo (m)", "Ancho (m)", "Descuento (m²)",
           "Sup. calculada (m²)", "Diferencia", "Perímetro (m)", "Puertas (un)", "Ancho puerta (m)",
           "Guardapolvo (ml)", "Ventanas (un)", "Anchos de ventana (m)", "Muros nuevos (ml)",
           "Sup. para presupuesto (m²)", "Estado", "Fuente y observación"]

# clave, recinto, tipo, sup rotulada, largo, ancho, descuento, perímetro ("rect" = 2·(largo+ancho)),
# puertas, ancho puerta, ventanas, anchos, muros nuevos (ml), estado, observación, sup. calculada fija
RECINTOS = [
    ("S01", "Sala de Capacitación 01", "Obra nueva", 40.16, 10.50, 3.88, 0, "rect", 1, 0.95, 1, "2,68", 18.26, "Plano",
     "Cotas del plano. La diferencia (+1,4%) es el saliente de la chimenea de la casona. Muros nuevos: 10,50 (muro de "
     "deslinde, h = 3,50 m) + 3,88 + 3,88; el muro de abajo es el existente de la casona.", None),
    ("S02", "Sala de Capacitación 02", "Obra nueva", 38.00, 8.46, 4.60, "=1.07*0.85", "rect", 1, 0.95, 2, "2,40 + 2,40",
     25.90, "Plano", "Descuento: rebaje de la puerta (1,07 × 0,85 m). Muros nuevos: 8,46 + 4,60 + 4,60 (compartido con la "
     "Sala 03) + 7,39 + 0,85.", None),
    ("S03", "Sala de Capacitación 03", "Obra nueva", 41.40, 4.60, 9.00, 0, "rect", 1, 0.95, 1, "2,30", 22.48, "Plano",
     "Muros nuevos: 4,60 + 9,00 + 4,60 + 4,28; el tramo de arriba del muro izquierdo se cuenta en la Sala 02.", None),
    ("S04", "Sala de Capacitación 04", "Obra nueva", 38.78, 9.45, 4.20, "=1.07*0.85", "rect", 1, 0.95, 2, "2,40 + 2,40",
     27.08, "Plano", "Descuento: rebaje de la puerta. Muros nuevos: 9,45 + 4,20 (muro de deslinde, h = 3,50 m) + 4,20 + "
     "8,38 + 0,85.", None),
    ("S05", "Sala de Capacitación 05", "Obra nueva", 41.09, 10.00, 4.20, "=1.07*0.85", "rect", 1, 0.95, 2, "2,40 + 2,40",
     24.25, "Plano; muros a escala", "Descuento: rebaje de la puerta. El tramo nuevo del muro superior (6,07 m) se midió "
     "a escala; el resto de ese muro es de la casona. Muros nuevos: 6,07 + 4,20 + 8,93 + 0,85 + 4,20 (compartido con la "
     "Sala 06).", None),
    ("S06", "Sala de Capacitación 06", "Obra nueva", 41.09, 10.00, 4.20, "=1.07*0.85", "rect", 1, 0.95, 2, "2,40 + 2,40",
     13.98, "Plano", "Descuento: rebaje de la puerta. El muro superior es el existente de la casona. Muros nuevos: 4,20 "
     "(compartido con la Sala 07) + 8,93 + 0,85.", None),
    ("S07", "Sala de Capacitación 07", "Obra nueva", 18.39, 4.38, 4.30, "=1.01*1.01/2", 16.77, 1, 0.95, 1, "2,40", 12.47,
     "Plano", "Descuento: ochavo de 1,43 m (catetos de 1,01 m); el perímetro ya lo descuenta. Muros nuevos: 4,38 + 3,29 + "
     "1,43 + 3,37; el muro derecho se cuenta en la Sala 06.", None),
    ("REC", "Recepción", "Casona (remodelación)", 13.08, None, None, None, None, None, None, None, "", None, "Plano",
     "Superficie rotulada: 2,81 × 3,00 m más el bow window. El perímetro no está acotado completo: medir en terreno.", None),
    ("HAL", "Hall y circulación de la casona", "Casona (remodelación)", None, None, None, None, None, None, None, None, "",
     None, "A escala", "Medido a escala a caras interiores de muro: hall 15,12 m² (sin la escalera, 1,99 × 0,93 m), resto "
     "de la pieza del frente fuera de la Recepción 9,25 m² y pasillo de los baños 1 3,61 m². Antes se estimó en 32 m² "
     "(±15%).", 27.98),
    ("EST", "Estar de Alumnos", "Casona (remodelación)", 22.96, None, None, None, None, None, None, None, "", None, "Plano",
     "Vano nuevo de 1,46 m en el muro derecho, con refuerzo de acero (pilares 100×100×3 y viga 200×50×3).", None),
    ("OFI", "Oficina 01", "Casona (remodelación)", 10.99, None, None, None, None, 1, 0.95, None, "", None, "Plano",
     "Dentro de los muros existentes de la casona. Forma en L sin cotas completas: el perímetro se mide en terreno.", None),
    ("BM1", "B. Mujeres 1", "Casona (remodelación)", None, 1.47, 2.02, 0, "rect", 1, None, None, "", None, "A escala",
     "Medido a escala, sin cotas.", None),
    ("BH1", "B. Hombres 1", "Casona (remodelación)", None, 1.42, 2.02, 0, "rect", 1, None, None, "", None, "A escala",
     "Medido a escala, sin cotas.", None),
    ("BH2", "B. Hombres 2", "Casona (remodelación)", None, 5.35, 2.05, "=0.25*0.85", "rect", 1, None, None, "", None,
     "A escala", "Medido a escala a caras interiores, sin cotas: zona de cubículos y urinarios (3,15 m) más la de "
     "lavamanos (1,95 m), separadas por un muro con un paso de 1,20 m (el descuento). La medida anterior, 5,56 × 2,29 m, "
     "incluía un espesor de muro por lado.", None),
    ("BM2", "B. Mujeres 2", "Obra nueva", None, 4.45, 2.78, 0, "rect", 1, None, None, "", 11.68, "Plano",
     "Cotas del plano. Muros nuevos: 4,45 (muro de deslinde, h = 3,50 m) + 2,78 + 4,45; el muro izquierdo se cuenta en "
     "la Sala 01.", None),
    ("BUN", "B. Universal", "Obra nueva", None, 1.60, 2.30, 0, "rect", 1, 0.95, None, "", 3.85, "Plano; muros a escala",
     "Cotas del plano. Muros nuevos: 1,60 + 1,60 + 0,65 (a escala); el muro derecho se cuenta en la Sala 02.", None),
]

FILA_CAB_REC = 4
for i, t in enumerate(CAB_REC):
    c = cb.cell(row=FILA_CAB_REC, column=i + 1, value=t)
    c.font, c.fill, c.border = f9b, GRIS, BORDE_FINO
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
cb.row_dimensions[FILA_CAB_REC].height = 36

FILA = {}  # clave -> fila en «Cubicación base»
r = FILA_CAB_REC + 1
for (clave, nombre, tipo, sup, largo, ancho, desc, per, pu, apu, ve, anchos, muros, estado, obs, fija) in RECINTOS:
    FILA[clave] = r
    vals = {"A": nombre, "B": tipo, "C": sup, "D": largo, "E": ancho, "F": desc, "M": ve, "N": anchos or None,
            "O": muros, "Q": estado, "R": obs, "J": pu, "K": apu}
    if largo is not None and ancho is not None:
        vals["G"] = f"=D{r}*E{r}-F{r}"
    elif fija is not None:
        vals["G"] = fija
    if sup is not None and "G" in vals:
        vals["H"] = f"=G{r}/C{r}-1"
    if per == "rect":
        vals["I"] = f"=2*(D{r}+E{r})"
    elif per is not None:
        vals["I"] = per
    if "I" in vals and pu and apu:
        vals["L"] = f"=I{r}-J{r}*K{r}"
    vals["P"] = f"=C{r}" if sup is not None else f"=G{r}"
    for col, v in vals.items():
        c = cb[f"{col}{r}"]
        c.value = v
    for col in "ABCDEFGHIJKLMNOPQR":
        c = cb[f"{col}{r}"]
        c.font, c.border = f9, BORDE_FINO
        if col in "CDEFGIKLOP":
            c.number_format = "0.00"
        if col == "H":
            c.number_format = "0.0%"
    r += 1
FILA_TOT_SUP = r
cb[f"A{r}"].value = "TOTAL superficie interior (todos los recintos)"
cb[f"P{r}"].value = f"=SUM(P{FILA_CAB_REC + 1}:P{r - 1})"
FILA_TOT_CASONA = r + 1
cb[f"A{r + 1}"].value = "TOTAL casona: piso que se retira (Recepción a B. Hombres 2)"
cb[f"P{r + 1}"].value = f"=SUM(P{FILA['REC']}:P{FILA['BH2']})"
for rr in (r, r + 1):
    for col in "ABCDEFGHIJKLMNOPQR":
        c = cb[f"{col}{rr}"]
        c.font, c.border = f9b, BORDE_FINO
    cb[f"P{rr}"].number_format = "0.00"

# Artefactos por baño
FILA_CAB_ART = r + 4
cb[f"A{FILA_CAB_ART - 1}"].value = "ARTEFACTOS POR BAÑO (contados en el plano)"
cb[f"A{FILA_CAB_ART - 1}"].font = f9b
for i, t in enumerate(["Baño", "WC (un)", "Lavamanos (un)", "Urinarios (un)", "Cubículos (un)",
                       "Shafts de extracción forzada (un)", "Fuente"]):
    c = cb.cell(row=FILA_CAB_ART, column=i + 1, value=t)
    c.font, c.fill, c.border = f9b, GRIS, BORDE_FINO
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
cb.row_dimensions[FILA_CAB_ART].height = 36
ARTEFACTOS = [("BM1", "B. Mujeres 1", 1, 1, 0, 0, 1), ("BH1", "B. Hombres 1", 1, 1, 0, 0, 1),
              ("BH2", "B. Hombres 2", 2, 3, 2, 2, 1), ("BM2", "B. Mujeres 2", 5, 5, 0, 5, 0),
              ("BUN", "B. Universal", 1, 1, 0, 0, 0)]
FILA_ART = {}
r = FILA_CAB_ART + 1
for clave, nombre, wc, lav, uri, cub, sha in ARTEFACTOS:
    FILA_ART[clave] = r
    for col, v in zip("ABCDEFG", (nombre, wc, lav, uri, cub, sha, "Plano")):
        c = cb[f"{col}{r}"]
        c.value, c.font, c.border = v, f9, BORDE_FINO
    r += 1

# Áreas comunes y exteriores
FILA_CAB_EXT = r + 2
cb[f"A{FILA_CAB_EXT - 1}"].value = "ÁREAS COMUNES Y EXTERIORES"
cb[f"A{FILA_CAB_EXT - 1}"].font = f9b
for i, t in enumerate(["Elemento", "Ancho (m)", "Largo (m)", "Cantidad", "Unidad", "Estado", "Fuente y observación"]):
    c = cb.cell(row=FILA_CAB_EXT, column=i + 1, value=t)
    c.font, c.fill, c.border = f9b, GRIS, BORDE_FINO
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
EXTERIORES = [
    ("PAT", "Patio central (N.P.T. -0,20)", 8.50, 2.70, None, "m²", "Plano", "Cotas del plano."),
    ("PFR", "Pasillo frontal (frente a las salas 06, 05 y 04)", 1.60, 22.70, None, "m²", "A escala",
     "Ancho por cota; largo medido a escala."),
    ("P54", "Pasillo entre las salas 05 y 04", 1.50, 4.44, None, "m²", "Plano",
     "Ancho por cota; largo = fondo de la sala más sus muros."),
    ("PCE", "Circulación alrededor del patio", None, None, 37.10, "m²", "A escala",
     "A escala: 10,98 × 5,70 m menos el patio y la escalera."),
    ("PAC", "Circulación de acceso (entre la casona, el B. Mujeres 2 y la Sala 02, con la rampa)", None, None, 15.00,
     "m²", "A escala", "Estimación a escala (±20%)."),
    ("TPA", "TOTAL pasillos y circulaciones", None, None, "SUMA:PFR:PAC", "m²", "", ""),
    ("BPA", "Baranda del patio central", None, None, 19.70, "ml", "Plano", "8,50 + 8,50 + 2,70 (cotas)."),
    ("BPF", "Baranda del pasillo frontal", None, None, 24.30, "ml", "A escala", "22,70 a escala + 1,60 de retorno."),
    ("BES", "Baranda de la escalera junto a la Oficina 01", None, None, 2.75, "ml", "A escala",
     "A escala. La escalera baja a un cuarto subterráneo y hoy tiene una baranda de tubos rota (foto 08-oct)."),
    ("TBA", "TOTAL barandas h = 0,95 m", None, None, "SUMA:BPA:BES", "ml", "", ""),
    ("ALA", "Pórtico de acceso existente (alero con 2 pilares)", 2.59, 4.59, None, "m²", "A escala",
     "A escala; el plano marca 2 pilares. Ya existe (fotos 08-oct)."),
    ("MCO", "Muro colindante sin sala (deslinde derecho, h = 3,50 m)", None, None, 3.40, "ml", "Plano",
     "Cota de 8,00 m menos los 4,60 m que son muro de la Sala 04."),
]
FILA_EXT = {}
r = FILA_CAB_EXT + 1
for clave, *_ in EXTERIORES:
    FILA_EXT[clave] = r
    r += 1
for clave, nombre, ancho, largo, cant, unidad, estado, obs in EXTERIORES:
    rr = FILA_EXT[clave]
    if isinstance(cant, str) and cant.startswith("SUMA:"):
        _, a, b = cant.split(":")
        cant = f"=SUM(D{FILA_EXT[a]}:D{FILA_EXT[b]})"
    elif cant is None:
        cant = f"=B{rr}*C{rr}"
    negrita = nombre.startswith("TOTAL")
    for col, v in zip("ABCDEFG", (nombre, ancho, largo, cant, unidad, estado or None, obs or None)):
        c = cb[f"{col}{rr}"]
        c.value, c.font, c.border = v, (f9b if negrita else f9), BORDE_FINO
        if col in "BCD":
            c.number_format = "0.00"

# Muros de la casona: largo a escala (caras interiores, grilla de 5 cm del plano) × altura de piso a cielo medida
FILA_CAB_MUR = r + 3
cb[f"A{FILA_CAB_MUR - 2}"].value = "MUROS DE LA CASONA Y ALTURAS DE PISO A CIELO (piso 1)"
cb[f"A{FILA_CAB_MUR - 2}"].font = f9b
cb[f"A{FILA_CAB_MUR - 1}"].value = (
    "Alturas medidas por Gonza en terreno el 08-10-2026, de piso a cielo. Largos de muro medidos a escala sobre el plano, a "
    "caras interiores, sin descontar puertas ni ventanas (falta su alto). Los lados abiertos entre recintos no cuentan.")
cb[f"A{FILA_CAB_MUR - 1}"].font = Font(name=FUENTE.name, size=9, italic=True)
for i, t in enumerate(["Zona", "Recinto del presupuesto", "Altura piso a cielo (m)", "Largo de muros (ml)",
                       "Puertas y vanos (ml)", "Sup. de muros (m²)", "Guardapolvo (ml)", "Fuente y observación"]):
    c = cb.cell(row=FILA_CAB_MUR, column=i + 1, value=t)
    c.font, c.fill, c.border = f9b, GRIS, BORDE_FINO
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
cb.row_dimensions[FILA_CAB_MUR].height = 36
MUROS = [  # clave, zona, recinto, altura medida, largo de muros, puertas y vanos, observación
    ("REC_A", "Recepción (sin el bow window)", "Recepción", 3.36, 8.10, 0,
     "Fachada a los lados del bow window 0,86 + 0,69; muro de la chimenea 4,65 más sus costados 2 × 0,40; machones "
     "hacia el Estar 0,30 + 0,80. El lado del hall es abierto."),
    ("REC_B", "Bow window", "Recepción", 3.47, 5.42, 0,
     "Contorno interior del bow window (4,14 m²), con sus 5 ventanas: 3,51 ml de ventana."),
    ("HAL_A", "Hall", "Hall y circulación", 3.30, 11.95, 1.85,
     "Fachada 3,65 (con la puerta de acceso de 1,15); muro de la escalera 4,65; muro hacia el pasillo y los baños 1 "
     "3,65 (con la puerta de 0,70). El lado de la Recepción es abierto."),
    ("HAL_B", "Pasillo de los baños 1", "Hall y circulación", 2.38, 8.58, 2.10,
     "Pasillo de 3,14 × 1,15 m entre el hall y los baños 1: muro del Estar, muro de los baños (2 puertas de 0,70), "
     "puerta del hall y la doble línea hacia la Oficina 01."),
    ("EST", "Estar de Alumnos", "Estar de Alumnos", 3.40, 16.25, 1.46,
     "5,55 × 4,05 m (22,48 m² contra 22,96 rotulados). Sin el vano abierto de 2,95 m hacia la Recepción; con el vano "
     "nuevo de 1,46 m."),
    ("OFI", "Oficina 01", "Oficina 01", 3.30, 18.41, 0.90,
     "Contorno de la L (10,54 m² contra 10,99 rotulados), con la puerta de 0,90 y la ventana de 0,75 del muro derecho."),
    ("B1", "Baños 1: cubículos", "B. Mujeres 1 y B. Hombres 1", 3.37, None, None,
     "Solo la altura: los baños no llevan pintura de muros (ver su revestimiento)."),
    ("BH2S", "B. Hombres 2: zona de sanitarios", "B. Hombres 2", 3.40, None, None, "Solo la altura."),
    ("BH2L", "B. Hombres 2: zona de lavamanos", "B. Hombres 2", 3.30, None, None, "Solo la altura."),
    ("SCO", "Sala pegada al Estar, al otro lado del vano nuevo", "Ninguno: no está en el plano (B 1.1)", 2.50, None, None,
     "En el plano, ahí va la circulación de acceso con la rampa. Probablemente es el agregado de 1 piso (pendiente 23)."),
]
FILA_MUR = {}
r = FILA_CAB_MUR + 1
for clave, zona, recinto, alto, largo, vanos, obs in MUROS:
    FILA_MUR[clave] = r
    vals = {"A": zona, "B": recinto, "C": alto, "D": largo, "E": vanos, "H": obs}
    if largo is not None:
        vals["F"] = f"=C{r}*D{r}"
        vals["G"] = f"=D{r}-E{r}"
    for col in "ABCDEFGH":
        c = cb[f"{col}{r}"]
        c.value, c.font, c.border = vals.get(col), f9, BORDE_FINO
        if col in "CDEFG":
            c.number_format = "0.00"
        if col in "AB":
            c.alignment = Alignment(wrap_text=True, vertical="top")
    r += 1
cb[f"A{r}"].value = "TOTAL muros de la casona (piso 1)"
cb[f"F{r}"].value = f"=SUM(F{FILA_CAB_MUR + 1}:F{r - 1})"
cb[f"G{r}"].value = f"=SUM(G{FILA_CAB_MUR + 1}:G{r - 1})"
for col in "ABCDEFGH":
    c = cb[f"{col}{r}"]
    c.font, c.border = f9b, BORDE_FINO
    if col in "FG":
        c.number_format = "0.00"

for col, w in zip("ABCDEFGHIJKLMNOPQR", (44, 20, 11, 9, 9, 10, 11, 10, 10, 9, 9, 11, 9, 13, 11, 12, 18, 110)):
    cb.column_dimensions[col].width = w


def ref(clave, col):
    return f"={CB}!{col}{FILA[clave]}"


def ref_art(clave, col):
    return f"={CB}!{col}{FILA_ART[clave]}"


def ref_ext(clave):
    return f"={CB}!D{FILA_EXT[clave]}"


# ---------------------------------------------------------------- partidas
DATOS = {r[0]: r for r in RECINTOS}


def sala(clave):
    d = DATOS[clave]
    per = {"S07": 16.77}.get(clave, 2 * (d[4] + d[5]))
    muros_obs = d[14].split("Muros nuevos: ", 1)[1] if "Muros nuevos: " in d[14] else ""
    estado_fund = "Supuesto" if "a escala" in d[13] else "Plano"
    plural = "Ventana" if d[10] == 1 else "Ventanas"
    return [
        ("Obra gruesa", [
            ("Fundaciones y sobrecimientos", "ml", ref(clave, "O"), estado_fund,
             f"Largo de los muros nuevos que le tocan a esta sala: {muros_obs}"),
            ("Relleno y compactación bajo radier", "m³", None, "Pendiente",
             "Superficie × espesor. Falta el espesor (los niveles de piso van de +0,10 a -0,20 m)."),
            ("Radier", "m²", ref(clave, "P"), "Supuesto", "Supuesto: igual a la superficie útil."),
            ("Muros nuevos de albañilería", "m²", None, "Pendiente",
             f"Largo {n(d[12])} ml × altura, menos vanos. Falta la altura (no hay cortes)."),
            ("Techumbre", "m²", ref(clave, "P"), "Supuesto",
             "Supuesto: superficie útil. La cubierta real suma muros, aleros y pendiente; falta el corte de techumbre."),
        ]),
        ("Terminaciones", [
            ("Pavimento (material por confirmar)", "m²", ref(clave, "P"), "Plano", "Superficie útil rotulada en el plano."),
            ("Guardapolvo", "ml", ref(clave, "L"), "Plano", f"Perímetro {n(per)} m menos la puerta."),
            ("Cielo (material por confirmar)", "m²", ref(clave, "P"), "Supuesto",
             "Supuesto: cielo en toda la superficie útil."),
            ("Pintura de muros", "m²", None, "Pendiente",
             f"Perímetro {n(per)} m × altura interior, menos vanos. Falta la altura."),
            ("Pintura de cielo", "m²", ref(clave, "P"), "Supuesto", "Igual a la superficie del cielo."),
            ("Puerta de 0,95 m", "un", ref(clave, "J"), "Plano", ""),
            (f"{plural} de {d[11]} m de ancho", "un", ref(clave, "M"), "Plano",
             "Ancho según plano; falta el alto (no hay elevaciones)."),
        ]),
        ("Instalaciones", [
            ("Instalación eléctrica e iluminación", "gl", 1, "Global", "Falta plano eléctrico."),
        ]),
    ]


def casona(clave, muros, alturas, extra_obra=(), puertas=None,
           ventanas_obs="Ventanas existentes: confirmar si se cambian.", con_ventanas=True):
    escala = DATOS[clave][3] is None
    est_sup = "Supuesto" if escala else "Plano"
    obs_sup = "Medido a escala (ver «Cubicación base»)." if escala else "Superficie útil rotulada en el plano."

    def suma(col):
        return "=" + "+".join(f"{CB}!{col}{FILA_MUR[k]}" for k in muros)

    terminaciones = [
        ("Pavimento (material por confirmar)", "m²", ref(clave, "P"), est_sup, obs_sup),
        ("Guardapolvo", "ml", suma("G"), "Supuesto",
         "Largo de muros a escala menos puertas y vanos (hoja «Cubicación base», muros de la casona)."),
        ("Cielo (material por confirmar)", "m²", ref(clave, "P"), "Supuesto", "Supuesto: cielo en toda la superficie."),
        ("Pintura de muros", "m²", suma("F"), "Supuesto",
         f"Largo de muros a escala × altura de piso a cielo que midió Gonza (08-oct): {alturas}. Sin descontar puertas "
         "ni ventanas (falta su alto)."),
        ("Pintura de cielo", "m²", ref(clave, "P"), "Supuesto", "Igual a la superficie del cielo."),
        puertas or ("Puertas", "un", None, "Pendiente", "Puertas existentes: confirmar si se cambian."),
    ]
    if con_ventanas:
        terminaciones.append(("Ventanas", "un", None, "Pendiente", ventanas_obs))
    return [
        ("Obra gruesa", [
            ("Relleno compactado", "m³", None, "Pendiente",
             "El piso existente se retira (B 1.2) y se rellena. Superficie × espesor; falta el espesor."),
            ("Radier", "m²", ref(clave, "P"), "Supuesto", "Supuesto: sobre el relleno va radier. Confirmar con Luciano."),
            *extra_obra,
        ]),
        ("Terminaciones", terminaciones),
        ("Instalaciones", [
            ("Instalación eléctrica e iluminación", "gl", 1, "Global", "Falta plano eléctrico."),
        ]),
    ]


def bano(clave, nuevo, alturas=None, tabiques_obs=""):
    d = DATOS[clave]
    art = {a[0]: a for a in ARTEFACTOS}[clave]
    est_sup = "Plano" if "Plano" in d[13] else "Supuesto"
    obs_sup = "Superficie por cotas del plano." if est_sup == "Plano" else "Medido a escala, sin cotas."
    if nuevo:
        obra = ("Obra gruesa", [
            ("Fundaciones y sobrecimientos", "ml", ref(clave, "O"), "Supuesto" if "a escala" in d[13] else "Plano",
             "Largo de los muros nuevos que le tocan a este baño: " + d[14].split("Muros nuevos: ", 1)[1]),
            ("Relleno y compactación bajo radier", "m³", None, "Pendiente", "Superficie × espesor; falta el espesor."),
            ("Radier", "m²", ref(clave, "P"), "Supuesto", "Supuesto: igual a la superficie del baño."),
            ("Muros nuevos de albañilería", "m²", None, "Pendiente",
             f"Largo {n(d[12])} ml × altura, menos vanos. Falta la altura (no hay cortes)."),
            ("Techumbre", "m²", ref(clave, "P"), "Supuesto", "Supuesto: superficie del baño; falta el corte de techumbre."),
        ])
    else:
        obra = ("Obra gruesa y tabiques", [
            ("Relleno compactado", "m³", None, "Pendiente",
             "El piso existente se retira (B 1.2) y se rellena; falta el espesor."),
            ("Radier", "m²", ref(clave, "P"), "Supuesto", "Supuesto: sobre el relleno va radier."),
            ("Tabiques nuevos", "m²", None, "Pendiente",
             "Muros rosados del plano dentro de la casona: falta medir el largo y el material. Piso a cielo: "
             f"{alturas} (Gonza, 08-oct).{tabiques_obs}"),
            ("Shaft de extracción forzada", "un", ref_art(clave, "F"), "Plano", "Indicado en el plano."),
        ])
    puerta = ("Puerta de acceso de 0,95 m", "un", 1, "Plano", "") if d[9] else ("Puerta de acceso", "un", 1, "Plano", "")
    terminaciones = [
        ("Pavimento cerámico o porcelanato (por confirmar)", "m²", ref(clave, "P"), est_sup, obs_sup),
        ("Revestimiento de muros", "m²", None, "Pendiente",
         "Confirmar con Luciano si el porcelanato también va en muros y hasta qué altura."
         + (f" Piso a cielo: {alturas} (Gonza, 08-oct)." if alturas else "")),
        ("Cielo y pintura", "m²", ref(clave, "P"), "Supuesto", "Supuesto: igual a la superficie del baño."),
        puerta,
    ]
    if art[5]:
        terminaciones.append(("Puertas de cubículo", "un", ref_art(clave, "E"), "Plano", "Contadas en el plano."))
    artefactos = [
        ("WC", "un", ref_art(clave, "B"), "Plano", "Contados en el plano."),
        ("Lavamanos", "un", ref_art(clave, "C"), "Plano", "Contados en el plano."),
    ]
    if art[4]:
        artefactos.append(("Urinarios", "un", ref_art(clave, "D"), "Plano", "Contados en el plano."))
    accesorios = "Dispensadores, espejos y basureros."
    if clave == "BUN":
        accesorios = "Barras de apoyo, dispensadores, espejo y basurero (baño universal)."
    sanitaria = ("El plano indica ventilación sanitaria y descarga existentes." if not nuevo
                 else "Conexión a la red de alcantarillado (S 4.3).")
    artefactos += [
        ("Grifería de lavamanos", "un", ref_art(clave, "C"), "Supuesto", "Supuesto: una llave por lavamanos."),
        ("Accesorios de baño", "gl", 1, "Global", accesorios),
        ("Instalación sanitaria: agua y desagüe", "gl", 1, "Global", sanitaria),
        ("Instalación eléctrica e iluminación", "gl", 1, "Global", "Falta plano eléctrico."),
    ]
    return [obra, ("Terminaciones", terminaciones), ("Artefactos e instalaciones", artefactos)]


SECCIONES = [
    ("TRABAJOS PREVIOS", [
        ("Instalación de faenas", [
            ("Cierre perimetral provisorio", "ml", None, "Pendiente", "Medir en terreno."),
            ("Oficina técnica y de inspección", "gl", 1, "Global", "Depende del plazo de la obra."),
            ("Bodega y pañol de herramientas", "gl", 1, "Global", "Depende del plazo de la obra."),
            ("Comedor y vestidores para personal", "gl", 1, "Global", "Depende del plazo y de la dotación."),
            ("Servicios higiénicos y duchas", "gl", 1, "Global", "Depende del plazo y de la dotación."),
            ("Empalmes provisorios de servicios luz y agua", "gl", 1, "Global", ""),
            ("Letrero de obra y señalética", "gl", 1, "Global", ""),
        ]),
        ("Trazado y despeje", [
            ("Trazado, replanteo y niveles", "gl", 1, "Global", "El plano indica niveles de piso entre +0,10 y -0,20 m."),
            ("Tala y retiro de árboles", "un", None, "Pendiente",
             "El plano no muestra árboles. En terreno (08-oct) hay uno grande en el patio junto al pórtico y varios en "
             "el patio del fondo: contarlos y ver si requiere permiso municipal."),
        ]),
    ]),
    ("PAVIMENTOS: DEMOLICIÓN, RETIRO Y ASEO", [
        ("Demolición y retiro (todo lo que se saca)", [
            ("Demolición de construcciones existentes, menos la casona", "m²", None, "Pendiente",
             "Todo lo que no es la casona se demuele para hacer las salas y los baños nuevos. No hay plano de lo "
             "existente: medir en terreno. Lo que hay (fotos 08-oct): pabellón de 1 piso al fondo, caseta de dos "
             "puertas, bodega con portón gris, cierres de planchas y de listones, y pavimentos de radier y cerámica. "
             "También la sala pegada al Estar de Alumnos, al otro lado del vano nuevo, con cielo a 2,50 m (Gonza, "
             "08-oct): en el plano, ese lugar es la circulación de acceso con la rampa. Ver pendientes 21 a 23."),
            ("Retiro del piso existente de la casona", "m²", f"={CB}!P{FILA_TOT_CASONA}", "Supuesto",
             "Recepción, hall, Estar, Oficina 01 y los baños de la casona. El hall y los baños se midieron a escala. "
             "Hoy es parquet en las piezas principales y cerámica al fondo (video 08-oct)."),
            ("Retiro de cielos existentes de la casona", "m²", f"={CB}!P{FILA_TOT_CASONA}", "Supuesto",
             "Supuesto: se cambia el cielo en toda la casona (piso 1), como en las partidas de cielo de cada recinto. "
             "Al fondo hay un cielo roto (video 08-oct)."),
            ("Retiro de revestimiento cerámico de muros de la casona", "m²", None, "Pendiente",
             "Al fondo de la casona hay recintos con cerámica en los muros hasta el cielo (video 08-oct). Las alturas de "
             "piso a cielo ya están medidas («Cubicación base»); falta el largo de muro con cerámica."),
            ("Desarme de tabiques vidriados existentes de la casona", "m²", None, "Pendiente",
             "Hay tabiques de vidrio en una pieza del piso 1 (video 08-oct): medir en terreno."),
            ("Cuarto subterráneo y escalera exterior junto a la Oficina 01: tratamiento por definir", "gl", None,
             "Pendiente", "La escalera baja a un cuarto subterráneo (foto 08-oct) que no está en el plano. Falta "
             "definir si se clausura, se rellena o se recupera (pendiente 15)."),
            ("Apertura de vano en muro existente (Estar de Alumnos, 1,46 m)", "un", 1, "Plano",
             "Vano nuevo con refuerzo de acero (ver sección L)."),
            ("Retiro de escombros a botadero", "gl", 1, "Global",
             "Incluye lo demolido y lo retirado de la casona (pisos, cielos, revestimientos y tabiques)."),
        ]),
        ("Aseo", [
            ("Aseo durante la obra", "m²", f"={CB}!P{FILA_TOT_SUP}", "Supuesto",
             "Supuesto: superficie interior total de los recintos."),
            ("Aseo final y entrega", "m²", f"={CB}!P{FILA_TOT_SUP}", "Supuesto",
             "Supuesto: superficie interior total de los recintos."),
        ]),
    ]),
    *[(f"SALA DE CAPACITACIÓN {k[1:]} (OBRA NUEVA)", sala(k)) for k in ("S01", "S02", "S03", "S04", "S05", "S06", "S07")],
    ("RECEPCIÓN (CASONA)", casona("REC", ("REC_A", "REC_B"), "3,36 m y 3,47 m en el bow window", extra_obra=[
        ("Cierre de vano existente con albañilería", "un", 1, "Supuesto",
         "Tramo rosado (obra nueva) de unos 0,76 m en el muro superior. Probablemente es la ventana que se ve junto a "
         "la chimenea en el video del piso 1 (08-oct). Confirmar."),
        ("Chimenea existente: mantener, clausurar o demoler (por definir)", "gl", None, "Pendiente",
         "Está en el muro que da a la Sala 01 (video del piso 1, 08-oct). Falta definir qué se hace (pendiente 19)."),
    ], ventanas_obs="Ventanas existentes, incluido el bow window: confirmar si se cambian.")),
    ("HALL Y CIRCULACIÓN DE LA CASONA", casona("HAL", ("HAL_A", "HAL_B"), "3,30 m y 2,38 m en el pasillo de los baños 1",
                                               puertas=(
        "Puertas", "un", None, "Pendiente",
        "Puerta de acceso a la casona y puerta al pasillo de los baños 1, existentes: confirmar si se cambian."),
        con_ventanas=False)),
    ("ESTAR DE ALUMNOS (CASONA)", casona("EST", ("EST",), "3,40 m", extra_obra=[
        ("Pilares de acero 100×100×3 mm", "un", 2, "Plano",
         "Refuerzo indicado en el plano para el vano nuevo de 1,46 m del muro derecho. Falta el alto del vano (el cielo "
         "del Estar está a 3,40 m, medido por Gonza el 08-oct)."),
        ("Viga de acero 200×50×3 mm", "ml", 1.66, "Plano", "Vano de 1,46 m más los dos pilares de 0,10 m."),
        ("Cierre de vano existente con albañilería", "un", 1, "Supuesto",
         "Tramo rosado (obra nueva) de unos 0,76 m en el muro inferior: se interpreta como cierre de un vano. Confirmar."),
    ], ventanas_obs="Incluye el cierre del vano nuevo de 1,46 m: confirmar qué lleva.")),
    ("OFICINA 01 (CASONA)", casona("OFI", ("OFI",), "3,30 m", extra_obra=[
        ("Tabiques nuevos", "m²", "=(0.75+1.05+1.30+1.65+2.05)*3.30", "Supuesto",
         "Muros rosados del plano: tramo de 0,75 m, retorno de 1,05 m (cotas), su tramo de 1,30 m hasta el muro derecho y "
         "el muro con el B. Hombres 2, 1,65 + 2,05 m (a escala), × 3,30 m de piso a cielo (Gonza, 08-oct). Supone que "
         "llegan al cielo. Falta el material."),
    ], puertas=("Puerta de 0,95 m", "un", ref("OFI", "J"), "Plano", ""),
        ventanas_obs="Ventana existente en el muro derecho: confirmar si se cambia.")),
    ("BAÑO MUJERES 1 (CASONA)", bano("BM1", nuevo=False, alturas="3,37 m")),
    ("BAÑO HOMBRES 1 (CASONA)", bano("BH1", nuevo=False, alturas="3,37 m")),
    ("BAÑO HOMBRES 2 (CASONA)", bano("BH2", nuevo=False, alturas="3,40 m en la zona de sanitarios y 3,30 m en la de "
                                     "lavamanos", tabiques_obs=" El muro con la Oficina 01 se cuenta en la Oficina 01 "
                                     "(M 1.3).")),
    ("BAÑO MUJERES 2 (OBRA NUEVA)", bano("BM2", nuevo=True)),
    ("BAÑO UNIVERSAL (OBRA NUEVA)", bano("BUN", nuevo=True)),
    ("PASILLOS, PATIO Y EXTERIORES (PUNTO DE ENCUENTRO)", [
        ("Pavimentos, barandas y rampas", [
            ("Relleno y radier de pasillos y patio", "m²", f"={CB}!D{FILA_EXT['TPA']}+{CB}!D{FILA_EXT['PAT']}",
             "Supuesto", "Pasillos y circulaciones más el patio central (ver «Cubicación base»)."),
            ("Pavimento de pasillos y circulaciones", "m²", ref_ext("TPA"), "Supuesto",
             "Pasillo frontal, pasillo entre las salas 05 y 04, circulación alrededor del patio y de acceso. Parte medida "
             "a escala."),
            ("Pavimento del patio central", "m²", ref_ext("PAT"), "Plano", "8,50 × 2,70 m (cotas)."),
            ("Barandas h = 0,95 m", "ml", ref_ext("TBA"), "Supuesto",
             "Patio central (cotas), pasillo frontal y escalera exterior junto a la Oficina 01 (a escala)."),
            ("Rampas de acceso", "un", 2, "Supuesto",
             "El plano muestra dos elementos con símbolo de rampa: en el acceso de la casona (1,55 × 2,15 m) y junto a "
             "la Oficina 01 (1,25 × 1,66 m). Confirmar."),
            ("Pavimento de áreas exteriores (acceso lateral y antejardín)", "m²", None, "Pendiente",
             "Franja lateral de 3,00 m junto al portón y antejardín de 3,00 m de fondo: confirmar si se pavimentan."),
        ]),
        ("Aleros, fachada y aguas lluvia", [
            ("Reparación del pórtico de acceso existente (alero con 2 pilares)", "m²", ref_ext("ALA"), "Supuesto",
             "El pórtico ya existe (fotos 08-oct): borde del alero desprendido y grieta en la viga. Superficie medida a "
             "escala; la reparación la define Luciano (pendiente 20)."),
            ("Aleros de pasillos y patio", "m²", None, "Pendiente",
             "El plano marca aleros sobre el pasillo frontal, alrededor del patio y a 0,20 m de los muros exteriores. "
             "Falta el corte de techumbre."),
            ("Aguas lluvia: canales y bajadas", "ml", None, "Pendiente", "Falta el plano de techumbre."),
            ("Fachada de la casona (revestimiento o reparación)", "m²", None, "Pendiente",
             "Depende del alcance de la remodelación de la casona. Gonza, 08-oct: la fachada sigue sin definir. En "
             "terreno, los costados y la parte de atrás tienen pintura descascarada y estuco desprendido."),
        ]),
        ("Cierres exteriores", [
            ("Reja exterior", "ml", None, "Pendiente",
             "El frente construido mide 35,30 m (cota); el frente del terreno y el costado se miden en terreno."),
            ("Portón de acceso vehicular de dos hojas (unos 2,60 m)", "un", 1, "Supuesto",
             "En el frente a Gran Avenida (lado izquierdo del plano), donde hoy hay un portón de fierro que da al pasillo "
             "lateral (fotos 08-oct). Ancho medido a escala."),
            ("Portón vehicular existente de la calle lateral", "un", None, "Pendiente",
             "Da al patio del fondo (fotos 08-oct) y no está en el plano. Falta definir si se mantiene, se cambia o se "
             "cierra (pendiente 24)."),
            ("Puertas peatonales exteriores", "un", 2, "Supuesto", "Una en el costado izquierdo y una en el antejardín."),
            ("Muro colindante de albañilería h = 3,50 m", "ml", ref_ext("MCO"), "Supuesto",
             "Solo el tramo del deslinde derecho que no es muro de sala. Los muros de deslinde de la Sala 01, la Sala 04 "
             "y el B. Mujeres 2 van en esos recintos."),
        ]),
        ("Instalaciones generales", [
            ("Tablero eléctrico general y empalme definitivo", "gl", 1, "Global", "Falta plano eléctrico."),
            ("Red de agua potable", "gl", 1, "Global", "Falta plano de instalaciones."),
            ("Red de alcantarillado", "gl", 1, "Global", "El plano marca descargas y ventilación sanitaria existentes."),
        ]),
    ]),
    ("ADICIONAL: FRONTIS", [
        ("Letreros publicitarios", [
            ("Perforaciones en el frontis para anclar 2 letreros publicitarios", "un", 5, "Confirmado",
             "Ya ejecutadas (dato de Gonza, 08-10-2026). Si la instalación de los letreros también es de Gutmann, "
             "se agrega la partida."),
        ]),
    ]),
]
LETRAS = "ABCDEFGHIJKLMNOPQRST"
assert len(SECCIONES) == len(LETRAS) == 20
assert SECCIONES[LETRAS.index("L")][0].startswith("ESTAR")

# ---------------------------------------------------------------- hoja del presupuesto
for col, w in zip("ABCDEFGHI", (5.3, 62.7, 7.4, 9.4, 13.4, 17.7, 2.4, 12, 95)):
    ws.column_dimensions[col].width = w

for coord, valor, est, rango in [
    ("A1", "PRESUPUESTO DESGLOSADO POR ITEMS A SUMA ALZADA", TIT["A1"], "A1:F1"),
    ("A3", "OBRA:       SEDE PREUNIVERSITARIO SAN MIGUEL", TIT["A3"], "A3:F3"),
    ("A4", "DIRECCIÓN:         GRAN AVDA JOSE MIGUEL CARRERA N°5670 - SAN MIGUEL", TIT["A4"], "A4:F4"),
    ("A5", "Borrador para revisión · piso 1 · recinto por recinto", TIT["A6"], "A5:F5"),
    ("A6", "Fecha 08-10-26", TIT["A6"], "A6:F6"),
    ("D7", "PRESUPUESTO GENERAL", TIT["D7"], "D7:F7"),
]:
    poner(ws, coord, valor, est)
    ws.merge_cells(rango)
ws["A5"].font = Font(name=FUENTE.name, size=10, italic=True)

fila = 9
totales, filas_item, secciones_info = [], [], []
for letra, (titulo, grupos) in zip(LETRAS, SECCIONES):
    fila_cab = fila
    for col, v in zip("ABCDEF", (letra, titulo, "Unidad", "Cantidad", "P. Unitario", "Total")):
        poner(ws, f"{col}{fila}", v, EST["cab"][col])
    for col, v in (("H", "Estado"), ("I", "Observación")):
        poner(ws, f"{col}{fila}", v, EST["cab"]["C"])
    ws[f"I{fila}"].alignment = Alignment(horizontal="left")
    fila += 1
    primera = fila
    items_sec = []
    for gi, (subtitulo, items) in enumerate(grupos, 1):
        poner(ws, f"A{fila}", gi, EST["sub"]["A"])
        poner(ws, f"B{fila}", subtitulo, EST["sub"]["B"])
        for col in "CDEF":
            poner(ws, f"{col}{fila}", None, EST["sub"][col])
        fila += 1
        for ii, (desc, unidad, cant, estado, obs) in enumerate(items, 1):
            assert estado in ESTADOS, (desc, estado)
            poner(ws, f"A{fila}", f"{gi}.{ii}", EST["item"]["A"])
            poner(ws, f"B{fila}", desc, EST["item"]["B"])
            poner(ws, f"C{fila}", unidad, EST["item"]["C"])
            poner(ws, f"D{fila}", cant, EST["item"]["D"])
            poner(ws, f"E{fila}", None, EST["item"]["E"])
            poner(ws, f"F{fila}", f"=D{fila}*E{fila}", EST["item"]["F"])
            fondo, letra_color = ESTADOS[estado]
            c = poner(ws, f"H{fila}", estado)
            c.fill, c.font, c.border = relleno(fondo), Font(name=FUENTE.name, size=9, color=letra_color), BORDE_FINO
            c.alignment = Alignment(horizontal="center")
            c = poner(ws, f"I{fila}", obs or None)
            c.font = Font(name=FUENTE.name, size=9, color="FF595959")
            filas_item.append(fila)
            items_sec.append(fila)
            fila += 1
    ultima = fila - 1
    for col in "ABCDEF":
        poner(ws, f"{col}{fila}", None, EST["tot"][col])
    ws[f"B{fila}"].value = f"TOTAL {letra}"
    ws[f"F{fila}"].value = f"=SUM(F{primera}:F{ultima})"
    totales.append(f"F{fila}")
    secciones_info.append((letra, titulo, fila_cab, primera, ultima, fila))
    fila += 3

# Resumen, como en su formato: GG y utilidades sobre el costo directo
fila_cd = fila
poner(ws, f"B{fila_cd}", "COSTO DIRECTO", EST["res"]["B"])
poner(ws, f"F{fila_cd}", "=" + "+".join(totales), EST["res"]["F"])
for col in "CDE":
    poner(ws, f"{col}{fila_cd}", None, EST["res"][col])
filas_res = {}
for nombre, pct in (("GASTOS GENERALES", 0.05), ("UTILIDADES", 0.15), ("SUBTOTAL NETO", None), ("IVA", 0.19),
                    ("TOTAL", None)):
    for col in "ABCDEF":
        poner(ws, f"{col}{fila + 1}", None, EST["res_blanco"][col])
    fila += 2
    est = EST["res_tot"] if nombre == "TOTAL" else EST["res"]
    for col in "CDE":
        poner(ws, f"{col}{fila}", None, est[col])
    poner(ws, f"B{fila}", nombre, est["B"])
    if pct is not None:
        poner(ws, f"E{fila}", pct, est["E"], number_format="0%")
    filas_res[nombre] = fila
fg, fu, fs, fi, ft = (filas_res[k] for k in ("GASTOS GENERALES", "UTILIDADES", "SUBTOTAL NETO", "IVA", "TOTAL"))
poner(ws, f"F{fg}", f"=F{fila_cd}*E{fg}", EST["res"]["F"])
poner(ws, f"F{fu}", f"=F{fila_cd}*E{fu}", EST["res"]["F"])
poner(ws, f"F{fs}", f"=F{fila_cd}+F{fg}+F{fu}", EST["res"]["F"])
poner(ws, f"F{fi}", f"=F{fs}*E{fi}", EST["res"]["F"])
poner(ws, f"F{ft}", f"=F{fs}+F{fi}", EST["res_tot"]["F"])
ws[f"I{fg}"].value = "Porcentaje editable (columna E). Tu formato usa 5% sobre el costo directo."
ws[f"I{fu}"].value = "Porcentaje editable (columna E). Tu formato usa 15% sobre el costo directo."
for rr in (fg, fu):
    ws[f"I{rr}"].font = Font(name=FUENTE.name, size=9, color="FF595959")

# Precio sin cantidad = total en $0 sin aviso: la cantidad se pinta roja
ws.conditional_formatting.add(
    f"D{filas_item[0]}:D{filas_item[-1]}",
    FormulaRule(formula=[f'AND($E{filas_item[0]}<>"",$D{filas_item[0]}="")'],
                fill=relleno("FFFFC7CE"), font=Font(color="FF9C0006")))
ws.print_area = f"A1:F{ft}"
ws.page_setup.orientation = "portrait"
ws.page_setup.fitToWidth = 1
ws.page_setup.fitToHeight = 0
ws.sheet_properties.pageSetUpPr.fitToPage = True

# ---------------------------------------------------------------- Pendientes
PENDIENTES = [
    ("Elevaciones, cortes y especificaciones técnicas (EETT)", "Luciano / arquitecto",
     "Muros, pinturas, ventanas, techumbre y aleros"),
    ("Altura de los muros nuevos. El plano solo marca h = 3,50 m en los muros de deslinde", "Luciano",
     "Muros nuevos y pintura de muros de todos los recintos"),
    ("Porcelanato: ¿solo piso o también muros de baños? ¿En qué recintos va?", "Luciano",
     "Pavimentos y revestimientos"),
    ("Material de pisos y cielos de salas y casona. En la casona, ¿el cielo nuevo va a la altura del actual (3,30 a "
     "3,47 m; 2,38 m en el pasillo de los baños 1)?", "Luciano",
     "Pavimento y cielo de cada recinto; pintura de muros de la casona"),
    ("Altura y material de los tabiques de la casona (oficina y baños)", "Luciano", "Tabiques nuevos",
     "Gonza, 08-oct: midió la altura de piso a cielo de cada recinto de la casona (hoja «Cubicación base»). Falta el "
     "material y, en los baños, el largo de los tabiques."),
    ("m² a demoler (todo menos la casona) y, dentro de la casona, m² de cerámica de muros y de tabiques vidriados a "
     "retirar", "Luciano (terreno)", "B 1.1, B 1.4 y B 1.5"),
    ("Espesor de los rellenos (salas, baños nuevos y casona)", "Luciano (terreno)", "Rellenos de cada recinto"),
    ("¿Sobre el relleno de la casona va radier?", "Luciano", "Radier de los recintos de la casona"),
    ("Árboles a talar y si requiere permiso", "Luciano (terreno)", "A 2.2",
     "Terreno 08-oct: hay uno grande en el patio junto al pórtico y varios en el patio del fondo. Falta contarlos."),
    ("Plazo estimado de la obra", "Luciano", "Instalación de faenas (A 1.x) y gastos generales"),
    ("Alcance de la casona: ¿también fachada y techo?", "Luciano", "S 2.4 y techumbre de la casona",
     "Gonza, 08-oct: el techo sí; la fachada, sin definir. El piso 2 también se remodela: se agrega cuando tenga "
     "medidas."),
    ("¿Dónde llevas las instalaciones generales (tablero, empalme y redes)?", "Luciano", "S 4.x"),
    ("¿Hay climatización? No aparece en el plano", "Luciano", "Se agrega en cada recinto si aplica"),
    ("¿La instalación de los 2 letreros también la hace Gutmann?", "Luciano", "T"),
    ("Escalera exterior junto a la Oficina 01 y el cuarto subterráneo al que baja: ¿se clausuran, se rellenan o se "
     "recuperan? ¿El cuarto queda bajo el piso de la casona que se retira?", "Luciano",
     "B 1.6, S 1.4 (baranda) y rellenos de la casona",
     "Terreno 08-oct: la escalera baja a un cuarto subterráneo (no sube al piso 2) y su baranda de tubos está rota."),
    ("Rampas: ¿los dos elementos con símbolo de rampa (acceso de la casona y junto a la Oficina 01) son rampas?",
     "Luciano", "S 1.5"),
    ("Oficina 01, baños 1 y B. Hombres 2: el plano los muestra dentro de los muros existentes de la casona y se "
     "presupuestan como remodelación. ¿Es así?", "Luciano", "M, N, O y P",
     "Terreno 08-oct: hay un baño junto a la escalera interior, como los baños 1 del plano, y al fondo recintos con "
     "cerámica en los muros. Falta que Luciano lo confirme."),
    ("Medidas a escala por confirmar: hall de la casona (unos 28 m²), baños 1 y B. Hombres 2, largos de muro de la "
     "casona, pasillos (unos 95 m²) y barandas", "Luciano (terreno)", "Hojas «Sede San Miguel» y «Cubicación base»"),
    ("Chimenea de la Recepción: ¿queda, se clausura o se demuele? La Sala 01 se construye pegada a ella", "Luciano",
     "J 1.4 y Sala 01"),
    ("Pórtico de acceso existente: tiene el borde del alero desprendido y una grieta en la viga. ¿Qué reparación "
     "lleva?", "Luciano", "S 2.1"),
    ("Muros de deslinde existentes (revestido en cerámica junto a la casona, de ladrillo con pilares al fondo y de "
     "placas de hormigón con planchas): ¿se demuelen y se levantan los nuevos de 3,50 m del plano, o se aprovechan?",
     "Luciano", "B 1.1, muros de las salas 01 y 04, B. Mujeres 2 y S 3.5"),
    ("Techo del pabellón del fondo: ¿las planchas onduladas son de fibrocemento antiguo? Si tienen asbesto, su retiro "
     "lleva un procedimiento y un costo aparte", "Luciano (terreno)", "B 1.1"),
    ("Agregado de 1 piso con techo de zinc, pegado atrás de la casona: ¿cuenta como casona o se demuele?", "Luciano",
     "B 1.1 y techumbre de la casona",
     "Gonza, 08-oct: la sala pegada al Estar de Alumnos, al otro lado del vano nuevo, tiene el cielo a 2,50 m (la "
     "casona, de 3,30 a 3,47 m). Probablemente es este agregado. En el plano, ese lugar es la circulación de acceso con "
     "la rampa."),
    ("Portón vehicular existente de la calle lateral (da al patio del fondo): ¿se mantiene, se cambia o se cierra?",
     "Luciano", "S 3.3"),
    ("Piso 2: ¿el piso flotante o vinílico va sobre las tablas existentes, niveladas, o se sacan?", "Luciano",
     "Piso 2 (se agrega con sus medidas)"),
    ("Piso 2: ¿el balcón sobre el bow window entra en la restauración?", "Luciano", "Piso 2 y fachada (S 2.4)"),
    ("Piso 2: la escalera a restaurar, ¿es la interior de madera? Es la única que sube al piso 2", "Luciano",
     "Piso 2"),
]
pend["A1"].value = "PENDIENTES POR CONFIRMAR"
pend["A1"].font = Font(name=FUENTE.name, size=12, bold=True)
for i, t in enumerate(["#", "Qué falta", "Quién lo responde", "Partidas que afecta", "Respuesta"]):
    c = pend.cell(row=3, column=i + 1, value=t)
    c.font, c.fill, c.border = f9b, GRIS, BORDE_FINO
for k, (que, quien, partidas, *respuesta) in enumerate(PENDIENTES, 1):
    for col, v in zip("ABCDE", (k, que, quien, partidas, respuesta[0] if respuesta else None)):
        c = pend[f"{col}{k + 3}"]
        c.value, c.font, c.border = v, f9, BORDE_FINO
        c.alignment = Alignment(wrap_text=True, vertical="top")
for col, w in zip("ABCDE", (4, 70, 20, 34, 40)):
    pend.column_dimensions[col].width = w

# ---------------------------------------------------------------- Léeme
LEEME = [
    ("PRESUPUESTO RECINTO POR RECINTO — BORRADOR PARA REVISIÓN", "titulo"),
    ("Obra: Sede Preuniversitario San Miguel · Gran Avda. José Miguel Carrera N°5670, San Miguel", None),
    ("Preparado el 08-10-2026 para Luciano Guzmán (Gutmann Construcciones) por Gonza, sobre tu formato estándar.", None),
    ("", None),
    ("Cómo está ordenado", "sub"),
    ("A. Trabajos previos y B. Pavimentos: lo general de la obra. B es todo lo que se saca: demolición, retiros dentro "
     "de la casona (pisos, cielos, revestimientos y tabiques) y escombros.", None),
    ("C a R. Un bloque por recinto, como en tu formato: cada sala, la casona, la oficina y cada baño, con todo lo que se "
     "construye en él (obra gruesa, terminaciones e instalaciones).", None),
    ("S. Pasillos, patio y exteriores: lo que no es de un recinto (pavimentos, barandas, aleros, cierres e "
     "instalaciones generales).", None),
    ("T. Adicional: las 5 perforaciones del frontis, ya ejecutadas. Su total se ve aparte por si se cobra por separado.",
     None),
    ("Al final, el resumen de tu formato: costo directo + gastos generales 5% + utilidades 15% + IVA 19%. Los "
     "porcentajes están en la columna E y se pueden cambiar.", None),
    ("", None),
    ("Cómo usarlo", "sub"),
    ("1. Escribe tus precios unitarios en la columna E (hoy vacía). Los totales se calculan solos.", None),
    ("2. Si una partida tiene precio pero no tiene cantidad, la cantidad se pinta roja: ese total quedaría en $0.", None),
    ("3. Hoja «Cubicación base»: las medidas de cada recinto tomadas del plano y las alturas de piso a cielo de la "
     "casona medidas en terreno. Si corriges una ahí, se actualiza el presupuesto.", None),
    ("4. Hoja «Pendientes»: lo que falta confirmar y qué partidas afecta.", None),
    ("5. Las columnas H (Estado) e I (Observación) son de ayuda: quedan fuera del área de impresión.", None),
    ("", None),
    ("Leyenda de la columna Estado", "sub"),
    ("Plano: cantidad tomada de una cota o de una superficie rotulada en el plano.", "Plano"),
    ("Supuesto: cantidad deducida o medida a escala; el supuesto está en la observación. Hay que validarla.", "Supuesto"),
    ("Pendiente: sin dato. Falta plano, especificación o medición en terreno.", "Pendiente"),
    ("Confirmado: dato entregado por Gonza o Luciano.", "Confirmado"),
    ("Global: partida a suma alzada (cantidad 1); el precio va completo.", "Global"),
    ("", None),
    ("Alcance", "sub"),
    ("Cubre el piso 1: el plano San Miguel v5 más lo que mostró la visita a terreno del 08-10-2026 (fotos, videos y las "
     "alturas de piso a cielo de la casona, que midió Gonza). "
     "Donde el terreno contradice un supuesto, manda el terreno; esas observaciones dicen «08-oct». El piso 2 de la "
     "casona se agrega cuando tenga medidas.", None),
    ("Las 7 salas, el B. Mujeres 2 y el B. Universal son obra nueva. La casona se remodela: su piso se retira y se "
     "rellena. Según el plano, la Oficina 01, los baños 1 y el B. Hombres 2 quedan dentro de la casona.", None),
]
leeme.column_dimensions["A"].width = 125
for k, (texto, tipo) in enumerate(LEEME, 1):
    c = leeme[f"A{k}"]
    c.value = texto or None
    c.alignment = Alignment(wrap_text=True, vertical="top")
    if tipo == "titulo":
        c.font = Font(name=FUENTE.name, size=13, bold=True)
    elif tipo == "sub":
        c.font = Font(name=FUENTE.name, size=11, bold=True)
    elif tipo in ESTADOS:
        fondo, letra_color = ESTADOS[tipo]
        c.font, c.fill = Font(name=FUENTE.name, size=10, color=letra_color), relleno(fondo)
    else:
        c.font = Font(name=FUENTE.name, size=10)

wb.active = 0
wb.save(SALIDA)
print("guardado:", SALIDA)
print("secciones:", len(secciones_info), "· partidas:", len(filas_item), "· fila TOTAL:", ft)
for letra, titulo, *_ in secciones_info:
    print(" ", letra, titulo)
