"""Arma el presupuesto de la habilitación Preuniversitario Talca con el formato de San Miguel.

Uso: python -I armar_presupuesto.py <salida.xlsx>
Protocolo por obra (luciano_pmo): solo partidas del catálogo de San Miguel v2.0, con su mismo nombre; la cantidad y el
P.U. quedan vacíos (se pintan en rojo) y la «Observación» dice cómo tomar la medida. Tabiques: se recorren los
recintos desde arriba a la izquierda hacia abajo a la derecha; el tabique que comparten dos recintos se anota en el
primero. Se descuentan las puertas y no las ventanas. Nunca escribe encima de un archivo existente: el vigente lo
edita Gonza.
"""
import os
import sys

import openpyxl
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

SALIDA = sys.argv[1]
if os.path.exists(SALIDA):
    sys.exit(f"{SALIDA} ya existe: el vigente lo edita Gonza. Genera a otro nombre.")

AZUL, CELESTE, AMARILLO, ROJO_CF = "FF1F3A5F", "FFE7ECF3", "FFFFF2CC", "FFF4B6B6"
FINO = Side(style="thin", color="FFBFBFBF")
BORDE = Border(left=FINO, right=FINO, top=FINO, bottom=FINO)
H = "× 3,00"  # alto de tabique y de pintura que usa Luciano
ALTO_VENTANA = "1,70"

# ------------------------------------------------------------------------------------------------- contenido
# Cada partida: (nombre, unidad, observación). Los nombres salen del catálogo de San Miguel v2.0.


def recinto(rotulo, tabique, guardapolvo, puertas, ventanas=None):
    """Partidas de un recinto seco con el orden de San Miguel (D. Salas nuevas), sin techumbre."""
    p = [("Tabiquería interior (h = 3,00 m)", "m²", tabique),
         ("Porcelanato", "m²", f"Superficie útil rotulada en el plano: {rotulo} m²."),
         ("Guardapolvo", "ml", guardapolvo),
         ("Estructura cielo", "m²", f"Igual que el porcelanato: {rotulo} m²."),
         ("Pintura de muros", "m²", f"Perímetro del guardapolvo {H} m."),
         ("Pintura de cielo", "m²", f"Igual que el cielo: {rotulo} m²."),
         ("Puertas", "UN", puertas)]
    if ventanas:
        p.append(("Ventanas y vidrios", "m²", ventanas))
    return p


def ventana(*anchos):
    suma = " + ".join(anchos)
    return f"Ventanas hacia el hall: ({suma}) × {ALTO_VENTANA} (alto de ventana de Luciano)." if len(anchos) > 1 \
        else f"Ventana hacia el hall: {suma} × {ALTO_VENTANA} (alto de ventana de Luciano)."


TRAPECIO = " El muro de fondo va inclinado (1,7°): el recinto es más ancho abajo; suma los dos anchos."

ZONAS = [
    ("A", "OBRAS PRELIMINARES", [
        ("Instalación de faenas y trabajos previos", [
            ("Instalación de faena (oficina, bodega, pañol, iluminacion)", "GL",
             "Partida global. Su monto depende del plazo de la obra, aún por definir."),
            ("Empalmes provisorios de luz y agua", "GL", "Partida global."),
            ("Servicios higiénicos provisorios", "GL", "Partida global. El edificio está en uso: ver si se usan sus "
             "baños."),
            ("Letrero de obra y señalética", "UN", "Por unidad."),
            ("Trazado, replanteo y niveles", "GL", "Partida global. La estructura está girada 4,03°: los tabiques se "
             "trazan desde una línea maestra a escuadra (el tabique del hall), nunca desde la fachada, y se corren "
             "0,56 m en cada fila de pilares (guía de lectura, página 2)."),
        ]),
    ]),
    ("B", "OFICINAS Y GUARDAR", [
        ("Sector oficina 05", recinto(
            "12,38",
            f"Tramos (cotas del plano): arriba 3,89 · fondo 3,00 · hacia la Oficina 04 4,24 · hacia el hall 1,55 "
            f"(bajo el marco del pilar). La puerta de 0,80 queda fuera. Tabique = suma {H}.",
            "Perímetro interior: 3,89 + 3,00 + 4,24 + 1,55 + 0,80, menos la puerta (0,80)." + TRAPECIO,
            "1 puerta de 0,80 (plano).")),
        ("Sector oficina 04", recinto(
            "12,82",
            f"Tramos: fondo 3,00 · hacia la Oficina 03 4,32 · hacia el hall 0,52 + 1,20 (ventana, no se descuenta) + "
            f"0,60. El muro de arriba ya está en la Oficina 05. La puerta de 0,80 queda fuera. Tabique = suma {H}.",
            "Perímetro interior: 4,24 + 3,00 + 4,32 + (0,52 + 1,20 + 0,60 + 0,80), menos la puerta (0,80)." + TRAPECIO,
            "1 puerta de 0,80 (plano).", ventana("1,20"))),
        ("Sector oficina 03", recinto(
            "8,61",
            f"Tramos: fondo 2,08 · hacia la Oficina 02 3,71 (hasta el pilar) · hacia el hall 0,60 (entre la puerta y "
            f"el pilar). El muro de arriba ya está en la Oficina 04. La puerta de 0,80 queda fuera. Tabique = "
            f"suma {H}.",
            "Perímetro interior: 4,32 + 2,08 + 3,71 + 0,68 + 0,68 (el pilar entra 0,68 × 0,68) + 0,60 + 0,80, menos la "
            "puerta (0,80). El rótulo ya descuenta el pilar." + TRAPECIO,
            "1 puerta de 0,80 (plano).")),
        ("Sector oficina 02", recinto(
            "8,74",
            f"Tramos: fondo 1,98 · abajo 4,45 (hacia el Guardar y el pasillo) · hacia el hall 1,18. El muro de arriba "
            f"ya está en la Oficina 03. La puerta de 0,80 queda fuera. Tabique = suma {H}.",
            "Perímetro interior: 4,39 + 1,98 + 4,45 + 1,18 + 0,80, menos la puerta (0,80)." + TRAPECIO,
            "1 puerta de 0,80 (plano).")),
        ("Sector guardar", recinto(
            "6,41",
            f"Tramos: fondo 3,20 · muro derecho 2,40 (la puerta de 0,80 queda arriba, fuera) · abajo 2,18 (hacia la "
            f"Oficina 01). El muro de arriba ya está en la Oficina 02. Tabique = suma {H}.",
            "Perímetro interior: ancho de arriba + 3,20 + 2,18 + 2,40 + 0,80, menos la puerta (0,80)." + TRAPECIO,
            "1 puerta de 0,80 (plano).")),
        ("Sector oficina 01", recinto(
            "20,64",
            f"Tramos: fondo 6,08 · arriba lo que queda de 2,50 después de los 2,18 del Guardar · muro derecho 7,88, "
            f"compartido con la Sala 06, menos el pilar de 0,80 que lo corta: 7,08 (se anota aquí y la Sala 06 no lo repite). La puerta de 0,80 queda fuera. "
            f"Abajo es la fachada existente. Tabique = suma {H}.",
            "Perímetro interior: (2,50 + 0,80) + 6,08 + 3,48 + 6,08 por el lado del pilar, menos la puerta (0,80); "
            "a escala, porque el muro derecho no tiene cota propia." + TRAPECIO,
            "1 puerta de 0,80 (plano).")),
    ]),
    ("C", "SALAS DE CAPACITACIÓN", [
        ("Sector sala de capacitación 01", recinto(
            "58,88",
            f"Tramos: izquierda 4,66 · arriba a la izquierda 2,80 · escalón 1,36 · hacia la Sala 02 6,70 · hacia el "
            f"hall 0,49 + 0,40 + 2,00 (ventana) + 0,60 + 2,00 (ventana) + 2,22, con el pilar de 0,80 entre medio. "
            f"El borde de arriba es segmentado: no es tabique (ver «Para Luciano»). La puerta de 0,95 queda fuera. "
            f"Tabique = suma {H}.",
            "Perímetro interior de la L: 2,80 + 4,66 + 9,46 + 6,70 + 6,54 + 1,36, menos la puerta (0,95).",
            "1 puerta de 0,95 (plano).", ventana("2,00", "2,00"))),
        ("Sector sala de capacitación 02", recinto(
            "55,81",
            f"Tramos: abajo 0,55 + 5,72 (compartido con la Sala 03, se anota aquí) · arriba, el tramo achurado junto "
            f"a la fachada (medir a escala). La puerta de 0,95 queda fuera. La izquierda ya está en la Sala 01; la "
            f"fachada es existente. Tabique = suma {H}.",
            "Perímetro interior: 9,18 + 6,37 + (0,55 + 0,95 + 5,72) + 6,70, menos la puerta (0,95). Lado de la "
            "fachada inclinado 4°: su largo casi no cambia.",
            "1 puerta de 0,95 (plano).")),
        ("Sector sala de capacitación 03", recinto(
            "61,24",
            f"Tramos: hacia el vestíbulo de la Sala 02 1,96 + 2,18 · hacia el hall 1,83 + 2,00 (ventana) + 0,40 + "
            f"0,40 · hacia la Sala 04 7,22 (compartido, se anota aquí). Arriba (5,72) ya está en la Sala 02. La puerta "
            f"de 0,95 queda fuera. Tabique = suma {H}.",
            "Perímetro interior: 5,72 + 1,96 + 2,18 + 5,58 + 7,22 + lado de la fachada (a escala), menos la puerta "
            "(0,95).",
            "1 puerta de 0,95 (plano).", ventana("2,00"))),
        ("Sector sala de capacitación 07", recinto(
            "24,80",
            f"Tramos: arriba 5,60 (hacia la Recepción) · izquierda 4,43 (hacia el pasillo de las oficinas) · derecha "
            f"4,43 − 0,95 de puerta (la ventana no se descuenta) · abajo 5,60, compartido con la Sala 06 (se anota "
            f"aquí). Tabique = suma {H}.",
            "Perímetro interior: 2 × (5,60 + 4,43) = 20,06, menos la puerta (0,95).",
            "1 puerta de 0,95 (plano).",
            f"Ventana del muro derecho, hacia el hall: ancho a escala × {ALTO_VENTANA} (la cadena del hall marca 1,25; "
            "verificar).")),
        ("Sector sala de capacitación 04", recinto(
            "64,93",
            f"Tramos: hacia el hall 0,85 + 2,00 (ventana) + 0,60 + 2,00 (ventana) + 0,40 + 0,40 · hacia la Sala 05 "
            f"7,22 (compartido, se anota aquí). Arriba ya está en la Sala 03. La puerta de 0,95 queda fuera. Tabique = "
            f"suma {H}.",
            "Perímetro interior: 7,22 + 7,20 (hacia el hall) + 7,22 + lado de la fachada (a escala), menos la puerta "
            "(0,95).",
            "1 puerta de 0,95 (plano).", ventana("2,00", "2,00"))),
        ("Sector sala de capacitación 06", recinto(
            "66,52",
            f"Tramos: arriba, lo que no comparte con la Sala 07 (a escala: unos 2,5 m a la izquierda y 0,4 m a la "
            f"derecha) · hacia el hall 1,01 + 2,00 (ventana) + 0,89 + 0,40 + 0,40 + 1,20 (ventana) + 0,35, con el "
            f"pilar entre medio. La izquierda ya está en la Oficina 01; abajo es la fachada existente. La puerta de "
            f"0,95 queda fuera. Tabique = suma {H}.",
            "Perímetro interior: 2 × (8,58 + 7,88) = 32,92, menos la puerta (0,95).",
            "1 puerta de 0,95 (plano).", ventana("2,00", "1,20"))),
        ("Sector sala de capacitación 05", recinto(
            "30,41",
            f"Tramos: hacia el hall 0,40 + 0,40 + 1,20 (ventana) + 0,35, bajo el pilar. Arriba ya está en la Sala 04; "
            f"la derecha y abajo son la fachada existente. La puerta de 0,95 queda fuera. Tabique = suma {H}.",
            "Perímetro interior: 7,22 + 3,64 + 8,47 + 3,64, menos la puerta (0,95). Lado de la fachada inclinado 4°.",
            "1 puerta de 0,95 (plano).", ventana("1,20"))),
    ]),
    ("D", "RECEPCIÓN Y HALL", [
        ("Sector hall y circulación (incluye la Recepción)", [
            ("Tabiquería interior (h = 3,00 m)", "m²",
             f"Muro de la entrada, entre el pilar de la entrada y la Sala 01: 0,59 + 1,05 (ventana) + 1,05 (ventana) "
             f"+ 0,59. La puerta doble de 2,00 queda fuera. Los demás muros del hall ya están en sus recintos. "
             f"Tabique = suma {H}."),
            ("Porcelanato", "m²", "Superficie del hall a escala: el área de la planta menos los 13 recintos. La "
             "Recepción (17,52 m², zona segmentada) ya está adentro: no se suma aparte."),
            ("Guardapolvo", "ml", "Las caras hacia el hall de todos los tabiques, menos las puertas."),
            ("Estructura cielo", "m²", "Igual que el porcelanato."),
            ("Pintura de muros", "m²", f"Las caras hacia el hall (el largo del guardapolvo) {H} m."),
            ("Pintura de cielo", "m²", "Igual que el cielo."),
            ("Puertas", "UN", "1 puerta doble de 2,00 en la entrada (plano)."),
            ("Ventanas y vidrios", "m²", f"Las 2 ventanas de la entrada: (1,05 + 1,05) × {ALTO_VENTANA}."),
        ]),
    ]),
    ("E", "GENERALES", [
        ("Aseo", [
            ("Aseo durante la obra", "m²", "Superficie total: los 432,19 m² de los 13 recintos más el hall (que incluye la Recepción)."),
            ("Aseo final", "m²", "Superficie total: los 432,19 m² de los 13 recintos más el hall (que incluye la Recepción)."),
        ]),
    ]),
]

ROTULOS = [("Sala de Capacitación 01", 58.88), ("Sala de Capacitación 02", 55.81), ("Sala de Capacitación 03", 61.24),
           ("Sala de Capacitación 04", 64.93), ("Sala de Capacitación 05", 30.41), ("Sala de Capacitación 06", 66.52),
           ("Sala de Capacitación 07", 24.80), ("Oficina 01", 20.64), ("Oficina 02", 8.74), ("Oficina 03", 8.61),
           ("Oficina 04", 12.82), ("Oficina 05", 12.38), ("Guardar", 6.41), ("Recepción (dentro del hall)", 17.52)]

PARA_LUCIANO = [
    ("Baños: el plano no dibuja ninguno. ¿Hay que presupuestarlos?", "No se incluyeron."),
    ("Marcos de los dos pilares (el de la entrada y el del hall, junto a la Sala 07): ¿forro de tabique, mueble o "
     "shaft?", "No se incluyeron."),
    ("Mesón del pilar del hall: si lleva agua, ¿se suma la instalación sanitaria?", "No se incluyó."),
    ("Mesones de la Recepción: ¿entran en el presupuesto o son mobiliario del mandante?", "No se incluyeron."),
    ("Borde de arriba de las Salas 01 y 02 (línea segmentada): ¿qué hay hoy ahí?", "Muro existente: no se "
     "presupuestó."),
    ("Techumbre y fachada: el edificio existe. ¿Se interviene algo?", "No: no lleva «Techumbre»."),
    ("Piso y cielo: ¿se cambian en toda la planta o se conserva lo existente?", "Se dejaron porcelanato y cielo en "
     "cada recinto."),
]

# ------------------------------------------------------------------------------------------------- libro
wb = openpyxl.Workbook()
leeme = wb.active
leeme.title = "Léeme"
pl = wb.create_sheet("Para Luciano")
ws = wb.create_sheet("Presupuesto")
cb = wb.create_sheet("Cubicación base")


def fuente(**kw):
    return Font(name="Calibri", sz=kw.pop("sz", 10), **kw)


# Cabecera fija: filas 1 a 6 combinadas A:F, Calibri 10 centrado, negrita de la 2 a la 6, color automático
CABECERA = ["PRESUPUESTO DESGLOSADO POR ITEMS A SUMA ALZADA", "PIEDRA ANGULAR SPA 77.453.608-6",
            "LAS SIERVAS DE MARIA 5556, HUECHURABA", "OBRA: HABILITACIÓN PREUNIVERSITARIO TALCA",
            "DIRECCIÓN: Talca", "FECHA: 16-10-2026"]
for i, texto in enumerate(CABECERA, start=1):
    ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=6)
    c = ws.cell(i, 1, texto)
    c.font = fuente(b=i > 1)
    c.alignment = Alignment(horizontal="center")
if not str(ws["A2"].value).startswith("PIEDRA ANGULAR"):
    sys.exit("La cabecera no empieza con PIEDRA ANGULAR en A2.")

for col, ancho in zip("ABCDEFG", (8.9, 60.0, 9.0, 12.0, 14.0, 16.0, 90.0)):
    ws.column_dimensions[col].width = ancho


def fila_zona(r, letra, titulo):
    for k, v in enumerate(("", f"{letra} {titulo}", "Unidad", "Cantidad", "P. Unitario", "Total", "Observación"), 1):
        c = ws.cell(r, k, v or None)
        c.font = fuente(b=True, color="FFFFFFFF")
        c.fill = PatternFill("solid", fgColor=AZUL)
        c.alignment = Alignment(horizontal="left" if k == 2 else "center")


def fila_celeste(r, texto, total=None, color_texto=None):
    for k in range(1, 8):
        c = ws.cell(r, k)
        c.fill = PatternFill("solid", fgColor=CELESTE)
        c.font = fuente()
    ws.cell(r, 2, texto).font = fuente(b=True, color=color_texto)
    if total:
        c = ws.cell(r, 6, total)
        c.font = fuente(b=True)
        c.number_format = "#,##0"


def fila_partida(r, codigo, nombre, unidad, obs):
    valores = (codigo, nombre, unidad, None, None, f"=D{r}*E{r}", obs)
    for k, v in enumerate(valores, 1):
        c = ws.cell(r, k, v)
        c.font = fuente()
        c.border = BORDE
        c.alignment = Alignment(vertical="top", wrap_text=k in (2, 7))
    for k, formato in ((4, "#,##0.00"), (5, "#,##0"), (6, "#,##0")):
        ws.cell(r, k).number_format = formato
    for k in (4, 5):
        ws.cell(r, k).fill = PatternFill("solid", fgColor=AMARILLO)


def fila_subtotal(r, texto, desde, hasta):
    for k in range(1, 8):
        c = ws.cell(r, k)
        c.border = BORDE
        c.font = fuente()
    c = ws.cell(r, 2, texto)
    c.font = fuente(b=True)
    c.alignment = Alignment(horizontal="right")
    c = ws.cell(r, 6, f"=SUM(F{desde}:F{hasta})")
    c.font = fuente(b=True)
    c.number_format = "#,##0"


r = 7
totales_zona = []
for letra, titulo, sectores in ZONAS:
    fila_zona(r, letra, titulo)
    r += 1
    subtotales = []
    for s, (nombre_sector, partidas) in enumerate(sectores, 1):
        cod = f"{letra}.{s}"
        fila_celeste(r, f"{cod} {nombre_sector}", color_texto=AZUL)
        r += 1
        desde = r
        for p, (nombre, unidad, obs) in enumerate(partidas, 1):
            fila_partida(r, f"{cod}.{p}", nombre, unidad, obs)
            r += 1
        if s == len(sectores):  # fila libre al final de cada zona, dentro de su subtotal
            fila_partida(r, None, "+ Agregar partida", None, None)
            r += 1
        fila_subtotal(r, f"Subtotal {cod}", desde, r - 1)
        subtotales.append(f"F{r}")
        r += 1
    fila_celeste(r, f"TOTAL {letra}", "=" + "+".join(subtotales))
    totales_zona.append(f"F{r}")
    r += 2

fila_celeste(r, "RESUMEN")
r += 1
fila_celeste(r, "COSTO DIRECTO", "=" + "+".join(totales_zona))
f_cd = r
r += 2
filas_resumen = {}
for nombre, tasa in (("GASTOS GENERALES", 0.05), ("UTILIDADES", 0.15)):
    ws.cell(r, 2, nombre).font = fuente()
    c = ws.cell(r, 5, tasa)
    c.font = fuente(color="FF000000")
    c.fill = PatternFill("solid", fgColor=AMARILLO)
    c.number_format = "0%"
    c = ws.cell(r, 6, f"=F{f_cd}*E{r}")
    c.font = fuente()
    c.number_format = "#,##0"
    filas_resumen[nombre] = r
    r += 2
ws.cell(r, 2, "SUBTOTAL NETO").font = fuente()
c = ws.cell(r, 6, f"=F{f_cd}+F{filas_resumen['GASTOS GENERALES']}+F{filas_resumen['UTILIDADES']}")
c.font = fuente()
c.number_format = "#,##0"
f_neto = r
r += 2
ws.cell(r, 2, "IVA").font = fuente()
c = ws.cell(r, 5, 0.19)
c.font = fuente(color="FF000000")
c.fill = PatternFill("solid", fgColor=AMARILLO)
c.number_format = "0%"
c = ws.cell(r, 6, f"=F{f_neto}*E{r}")
c.font = fuente()
c.number_format = "#,##0"
f_iva = r
r += 2
ws.cell(r, 2, "TOTAL").font = fuente(b=True)
c = ws.cell(r, 6, f"=F{f_neto}+F{f_iva}")
c.font = fuente(b=True)
c.number_format = "#,##0"
ultima = r

roja = PatternFill("solid", fgColor=ROJO_CF, bgColor=ROJO_CF)
ws.conditional_formatting.add(f"D8:D{ultima}", FormulaRule(formula=["AND(LEN($A8)>4,ISBLANK($D8))"], fill=roja))
ws.conditional_formatting.add(f"E8:E{ultima}", FormulaRule(formula=["AND(LEN($A8)>4,ISBLANK($E8))"], fill=roja))
ws.freeze_panes = "A8"

# Léeme
leeme.column_dimensions["A"].width = 130
LEEME = [
    ("t", "PRESUPUESTO DESGLOSADO POR ÍTEMS A SUMA ALZADA — BORRADOR PARA REVISIÓN"),
    ("p", "Obra: habilitación de una planta para el Preuniversitario Talca: 7 salas de capacitación, 5 oficinas, un "
          "Guardar y el hall con la Recepción."),
    ("p", "Preparado para Luciano por Gonza el 16-10-2026, con la forma de San Miguel."),
    ("", ""),
    ("s", "Cómo está ordenado"),
    ("p", "1. Hoja «Presupuesto», en el orden del plano: A Obras preliminares, B Oficinas y Guardar, C Salas de "
          "capacitación, D Recepción y hall y E Generales. Al final, el resumen con GG 5 %, utilidades 15 % e IVA."),
    ("p", "2. La cantidad y el P.U. quedan vacíos y se pintan en rojo hasta que se completen. La «Observación» dice "
          "cómo medir: qué cotas del plano y qué fórmula."),
    ("p", "3. Cada zona termina con una fila «+ Agregar partida» en blanco, ya dentro de su subtotal: se le pone "
          "código, nombre, unidad, cantidad y precio, y suma sola."),
    ("p", "4. Hoja «Para Luciano»: lo que hay que confirmar antes de ofertar. Hoja «Cubicación base»: superficies "
          "rotuladas en el plano."),
    ("", ""),
    ("s", "Reglas de medida"),
    ("p", "• Plano «Habilitacion - Preuniversitario Talca v3», escala 1:50 (deducida: la lámina no la indica). Todos "
          "los tabiques achurados son nuevos; pilares y fachada son existentes."),
    ("p", "• Tabiques a 3,00 m de alto. Se recorren los recintos desde arriba a la izquierda hacia abajo a la "
          "derecha: el tabique que comparten dos recintos se anota en el primero y no se repite. Se descuentan las "
          "puertas y no las ventanas."),
    ("p", "• Ventanas: ancho del plano × 1,70. La estructura está girada 4,03°: los recintos junto a la fachada "
          "derecha y a la izquierda de las oficinas (1,7°) son trapecios; se miden con el promedio de sus anchos."),
    ("p", "• Guía de lectura del plano: guia_lectura_plano_talca.pdf."),
]
for i, (tipo, texto) in enumerate(LEEME, 1):
    c = leeme.cell(i, 1, texto or None)
    c.alignment = Alignment(wrap_text=True, vertical="top")
    if tipo == "t":
        c.font = Font(name="Calibri", sz=13, b=True, color=AZUL)
    elif tipo == "s":
        c.font = Font(name="Calibri", sz=11, b=True, color=AZUL)
    else:
        c.font = Font(name="Calibri", sz=11, color="FF000000")

# Para Luciano
for col, ancho in zip("ABCD", (5, 75, 35, 40)):
    pl.column_dimensions[col].width = ancho
c = pl.cell(1, 1, "PARA LUCIANO — lo que hay que confirmar antes de ofertar")
c.font = Font(name="Calibri", sz=13, b=True, color=AZUL)
for k, v in enumerate(("#", "Pregunta", "Qué se supuso", "Respuesta"), 1):
    c = pl.cell(3, k, v)
    c.font = Font(name="Calibri", sz=10, b=True, color="FFFFFFFF")
    c.fill = PatternFill("solid", fgColor=AZUL)
for i, (preg, sup) in enumerate(PARA_LUCIANO, 1):
    for k, v in enumerate((i, preg, sup, None), 1):
        c = pl.cell(3 + i, k, v)
        c.font = Font(name="Calibri", sz=10, color="FF000000")
        c.border = BORDE
        c.alignment = Alignment(wrap_text=True, vertical="top")

# Cubicación base
for col, ancho in zip("ABC", (34, 16, 70)):
    cb.column_dimensions[col].width = ancho
c = cb.cell(1, 1, "Superficies útiles rotuladas en el plano v3")
c.font = Font(name="Calibri", sz=13, b=True, color=AZUL)
for k, v in enumerate(("Recinto", "Sup. útil (m²)", "Nota"), 1):
    c = cb.cell(3, k, v)
    c.font = Font(name="Calibri", sz=10, b=True, color="FFFFFFFF")
    c.fill = PatternFill("solid", fgColor=AZUL)
for i, (nombre, sup) in enumerate(ROTULOS, 4):
    cb.cell(i, 1, nombre)
    cb.cell(i, 2, sup).number_format = "#,##0.00"
    if nombre.startswith("Recepción"):
        cb.cell(i, 3, "Zona segmentada dentro del hall: no entra en la suma de recintos.")
    for k in (1, 2, 3):
        cb.cell(i, k).font = Font(name="Calibri", sz=10, color="FF000000")
        cb.cell(i, k).border = BORDE
fin = 4 + len(ROTULOS) - 2  # sin la Recepción
c = cb.cell(fin + 2, 1, "Total recintos (sin la Recepción)")
c.font = Font(name="Calibri", sz=10, b=True, color="FF000000")
c = cb.cell(fin + 2, 2, f"=SUM(B4:B{fin})")
c.font = Font(name="Calibri", sz=10, b=True, color="FF000000")
c.number_format = "#,##0.00"
c = cb.cell(fin + 3, 1, "Hall y circulación")
c.font = Font(name="Calibri", sz=10, color="FF000000")
c = cb.cell(fin + 3, 3, "Sin rótulo: medir a escala (incluye la Recepción).")
c.font = Font(name="Calibri", sz=10, color="FF000000")

wb.active = wb.sheetnames.index("Presupuesto")
wb.save(SALIDA)
print(f"{SALIDA}: filas del presupuesto 7 a {ultima}")
