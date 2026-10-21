"""Completa las cantidades del presupuesto de Talca (pedido de Gonza, 17-10-2026: «confío en que puedes completar
cantidad y P.U.»). Cada cantidad queda como fórmula con las cotas del plano v3, para que se pueda revisar.

Uso: python -I completar_cantidades.py <Presupuesto_Talca.xlsx>
Escribe en el mismo archivo: solo la columna D de las filas con código, el P.U. de la puerta doble (D.1.7) y las
observaciones que había que corregir (C.1.1, C.6.1, D.1.2 y D.1.3). Las medidas salen de sitio.py, que se verificó
encima del plano y contra las cadenas de cotas. Tabiques a 3,00; se descuentan las puertas y no las ventanas; ventanas
de ancho × 1,70.
"""
import copy
import sys

import openpyxl
from openpyxl.comments import Comment

RUTA = sys.argv[1]
HALL = 216.39          # a escala: planta 680,20 m² − recintos − tabiques − pilares (grilla de 5 cm sobre sitio.py)
RECINTOS = 432.19      # suma de los 13 rótulos

# sector: (sup. útil, tramos de tabique, perímetro del guardapolvo con la puerta descontada, anchos de ventana)
SECTORES = {
    "B.1": (12.38, "3.89+3+4.24+1.55", "3.89+4.24+3+3-0.8", None),
    "B.2": (12.82, "3+4.32+0.52+1.2+0.6", "4.24+4.32+3+3-0.8", "1.2"),
    "B.3": (8.61, "2.08+3.71+0.6", "4.32+4.39+2.08+2.08-0.8", None),
    "B.4": (8.74, "1.98+4.45+1.18", "4.39+4.45+1.98+1.98-0.8", None),
    "B.5": (6.41, "3.2+2.4+2.18", "2.17+2.18+3+3-0.8", None),
    "B.6": (20.64, "6.08+0.32+7.08", "3.3+3.48+6.08+6.08-0.8", None),
    "C.1": (58.88, "5.34+2.8+1.37+6.71+0.49+0.4+2+0.6+2+2.22", "9.45+9.45+6.71+6.71+0.67+0.67-0.95", "2+2"),
    "C.2": (55.81, "0.55+5.72+1.34", "9.18+8.73+6.37+6.39-0.95", None),
    "C.3": (61.24, "1.96+2.18+1.83+2+0.4+0.4+7.22", "8.72+8.17+7.88+7.9-0.95", "2"),
    "C.4": (24.80, "5.6+4.43+3.48+5.6", "5.6+5.6+4.43+4.43-0.95", "1.25"),
    "C.5": (64.93, "0.85+2+0.6+2+0.4+0.4+7.22", "8.72+8.17+7.88+7.9-0.95", "2+2"),
    "C.6": (66.52, "2.74+1.01+2+0.89+0.4+0.4+1.2+0.35", "8.58+8.58+7.88+7.88+0.66+0.66+0.66+0.66-0.95", "2+1.2"),
    "C.7": (30.41, "0.4+0.4+1.2+0.35", "8.73+8.47+3.64+3.65-0.95", "1.2"),
    "D.1": (HALL, "0.59+1.05+1.05+0.59",
            "6.68+4.55+2.44+2.2+1.8+2.37+3.29+8.62+0.56+1.84+2.18+4.63+6.25+2.35+5.84+3.48+0.42+6.93+4.36", "1.05+1.05"),
}
NOMBRE_ITEM = {"Tabiquería interior (h = 3,00 m)": "tab", "Porcelanato": "sup", "Guardapolvo": "gp",
               "Estructura cielo": "sup", "Pintura de muros": "pm", "Pintura de cielo": "sup", "Puertas": "pta",
               "Ventanas y vidrios": "ven"}
FIJAS = {"A.1.1": 1, "A.1.2": 1, "A.1.3": 1, "A.1.4": 1, "A.1.5": 1,
         "E.1.1": f"={RECINTOS}+{HALL}", "E.1.2": f"={RECINTOS}+{HALL}"}
OBS = {
    "C.1.1": "Tramos (plano v3, verificados sobre los vectores): izquierda 5,34 (de 3,30 a 8,64; la cota 4,66 llega "
             "solo hasta el pilar) · arriba a la izquierda 2,80 · escalón 1,37 · hacia la Sala 02 6,71 · hacia el hall "
             "0,49 + 0,40 + 2,00 (ventana) + 0,60 + 2,00 (ventana) + 2,22, con el pilar de 0,80 entre medio. El borde de "
             "arriba es segmentado: no es tabique. La puerta de 0,95 queda fuera. Tabique = suma × 3,00.",
    "C.6.1": "Tramos: arriba, lo que no comparte con la Sala 07: 8,58 − 5,84 = 2,74 · hacia el hall 1,01 + 2,00 "
             "(ventana) + 0,89 + 0,40 + 0,40 + 1,20 (ventana) + 0,35, con el pilar entre medio. La izquierda ya está "
             "en la Oficina 01; abajo es el muro perimetral existente. La puerta de 0,95 queda fuera. Tabique = suma × "
             "3,00.",
    "D.1.2": f"Superficie del hall a escala: planta 680,20 m² menos los 13 recintos, los tabiques y los pilares = "
             f"{HALL:.2f} m² (grilla de 5 cm sobre la geometría verificada). Incluye los 17,52 m² de la Recepción.",
    "D.1.3": "Caras hacia el hall de los tabiques y del muro sur, menos las puertas: corredor de las oficinas 6,68 · "
             "pasillos 4,55 + 2,44 + 2,20 + 1,80 + 2,37 · entrada 3,29 · Sala 01 8,62 · vestíbulo de la Sala 02 0,56 + "
             "1,84 + 2,18 · Sala 03 4,63 · Sala 04 6,25 · Sala 05 2,35 · Sala 07 5,84 + 3,48 · Sala 06 0,42 + 6,93 · "
             "muro sur 4,36 = 70,79 ml.",
}

wb = openpyxl.load_workbook(RUTA)
ws = wb["Presupuesto"]
assert str(ws["A2"].value).startswith("PIEDRA ANGULAR"), "cabecera"
hechas = 0
for fila in ws.iter_rows(min_row=8):
    cod, nombre = fila[0].value, fila[1].value
    if not (isinstance(cod, str) and cod.count(".") == 2):
        continue
    sec = cod.rsplit(".", 1)[0]
    if cod in FIJAS:
        fila[3].value = FIJAS[cod]
    elif sec in SECTORES:
        sup, tab, gp, ven = SECTORES[sec]
        tipo = NOMBRE_ITEM[nombre]
        fila[3].value = {"tab": f"=({tab})*3", "sup": f"={sup}", "gp": f"={gp}", "pm": f"=({gp})*3", "pta": 1,
                         "ven": f"=({ven})*1.7" if ven else None}[tipo]
    else:
        raise SystemExit(f"{cod} sin regla")
    if cod in OBS:
        fila[6].value = OBS[cod]
    if cod == "D.1.7":
        fila[4].value = 750000
        fila[4].comment = Comment("San Bernardo «Puerta doble», 750.000 (la de San Miguel, 150.000, es de una hoja).",
                                  "Alma")
    hechas += 1

leeme = wb["Léeme"]
r = leeme.max_row + 2
leeme.cell(r, 1, "Cantidades").font = copy.copy(leeme["A5"].font)
leeme.cell(r + 1, 1, "• Calculadas por Alma el 17-10-2026, a pedido de Gonza, con la geometría del plano v3 "
               "verificada sobre los vectores y contra las cadenas de cotas. Cada cantidad es una fórmula con sus "
               "cotas. Hall a escala (216,39 m², incluye la Recepción).").font = copy.copy(leeme["A6"].font)
for c in (leeme.cell(r, 1), leeme.cell(r + 1, 1)):
    c.alignment = copy.copy(leeme["A6"].alignment)
wb.save(RUTA)
print(f"{hechas} cantidades escritas en {RUTA}")
