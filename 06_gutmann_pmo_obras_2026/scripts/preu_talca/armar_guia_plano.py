"""Guía en PDF para entender la habilitación Preuniversitario Talca: cómo leer su plano, la inclinación y zona por zona.

Uso: python -I armar_guia_plano.py <plano.pdf> <carpeta_trabajo> <salida.pdf>
Las coordenadas de recortes y números se escriben en píxeles de la vista del plano de 2000 px de ancho (la misma con
que se revisó el plano) y se pasan a puntos con P = 2551 / 2000. Escala del plano: 1:50 (56,69 pt por metro), deducida
del tamaño de la lámina (900 × 600 mm) y de los pilares de 0,80 acotados; la lámina no la indica.
Memoria: los recortes van a 150-300 dpi sobre zonas chicas; nunca rasterizar la página entera a 300 dpi.
"""
import math
import os
import sys

import pymupdf
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Flowable, Paragraph, Table, TableStyle

PLANO, TRABAJO, SALIDA = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(TRABAJO, exist_ok=True)

pdfmetrics.registerFont(TTFont("Dv", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DvB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFontFamily("Dv", normal="Dv", bold="DvB", italic="Dv", boldItalic="DvB")

ANCHO, ALTO = 842, 595  # A4 apaisado
MARGEN = 28
ROJO, AZUL, GRIS = HexColor("#C62828"), HexColor("#1F3A5F"), HexColor("#6B6B6B")
NEGRO = HexColor("#000000")
FONDO_CUENTA, FONDO_PRUEBA, FONDO_OJO = HexColor("#EAF1F8"), HexColor("#FFF4CC"), HexColor("#FBE3E3")

CUERPO = ParagraphStyle("cuerpo", fontName="Dv", fontSize=8.6, leading=11.2, alignment=TA_LEFT)
CUERPO_CH = ParagraphStyle("cuerpo_ch", parent=CUERPO, fontSize=8, leading=10.2)
SUBT = ParagraphStyle("subt", fontName="DvB", fontSize=10.5, leading=13, textColor=AZUL, spaceAfter=2)
TIT_CAJA = ParagraphStyle("tit_caja", fontName="DvB", fontSize=9, leading=11.5, textColor=AZUL)

P = 2551 / 2000  # px de la vista de 2000 -> pt del plano
ANG = 4.03  # inclinación de los ejes de pilares y de la fachada oriente, medida en los vectores del plano
CORRIMIENTO = 8.00 * math.tan(math.radians(ANG))  # lo que se corre el eje en cada fila de pilares (8,00 m)
plano = pymupdf.open(PLANO)
pagina_plano = plano[0]


def pt(*v):
    return tuple(round(x * P, 1) for x in v)


def m2(v):
    return f"{v:.2f}".replace(".", ",")


def recorte(nombre, clip, dpi):
    ruta = os.path.join(TRABAJO, f"{nombre}.png")
    pagina_plano.get_pixmap(dpi=dpi, clip=pymupdf.Rect(*clip)).save(ruta)
    return ruta


def num(n):
    return f'<font backColor="#C62828" color="#FFFFFF"><b>&nbsp;{n}&nbsp;</b></font>'


def tabla_caja(titulo, texto, fondo, ancho):
    t = Table([[Paragraph(titulo, TIT_CAJA)], [Paragraph(texto, CUERPO)]], colWidths=[ancho])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fondo), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (0, 0), 5),
                           ("BOTTOMPADDING", (0, -1), (-1, -1), 6), ("TOPPADDING", (0, 1), (-1, 1), 1)]))
    return t


class caja(Flowable):
    """Caja de color que toma el ancho de la columna donde cae."""

    def __init__(self, titulo, texto, fondo):
        super().__init__()
        self.titulo, self.texto, self.fondo, self._t = titulo, texto, fondo, None

    def wrap(self, w, h):
        self._t = tabla_caja(self.titulo, self.texto, self.fondo, w)
        self.width, self.height = self._t.wrap(w, h)
        return self.width, self.height

    def draw(self):
        self._t.drawOn(self.canv, 0, 0)


def pie(c, n):
    c.setFont("Dv", 7.5)
    c.setFillColor(GRIS)
    c.drawString(MARGEN, 14, "Guía de la obra · Habilitación Preuniversitario Talca · plano «Habilitacion - "
                 "Preuniversitario Talca» v3, planta 1:50 (escala deducida)")
    c.drawRightString(ANCHO - MARGEN, 14, f"página {n}")
    c.setFillColor(NEGRO)


def marcador(c, x, y, n, r=8):
    c.setFillColor(ROJO)
    c.setStrokeColor(white)
    c.setLineWidth(1)
    c.circle(x, y, r, stroke=1, fill=1)
    c.setFillColor(white)
    c.setFont("DvB", 9 if n < 10 else 8)
    c.drawCentredString(x, y - 3.2, str(n))


def imagen_con_numeros(c, png, clip, x, y, w, h, marcas, r=8):
    """Dibuja el recorte y encima los números. Cada marca es (n, x, y) o (n, x, y, x_objetivo, y_objetivo), en px."""
    c.drawImage(png, x, y, w, h)
    c.setStrokeColor(HexColor("#B0B0B0"))
    c.setLineWidth(0.6)
    c.rect(x, y, w, h, stroke=1, fill=0)
    s = w / (clip[2] - clip[0])

    def aca(px, py):
        return x + (px * P - clip[0]) * s, y + h - (py * P - clip[1]) * s

    for m in marcas:
        mx, my = aca(m[1], m[2])
        if len(m) == 5:
            tx, ty = aca(m[3], m[4])
            c.setStrokeColor(ROJO)
            c.setLineWidth(1.1)
            c.line(mx, my, tx, ty)
            c.setFillColor(ROJO)
            c.circle(tx, ty, 1.8, stroke=0, fill=1)
        marcador(c, mx, my, m[0], r)


def empacar(unidades, columnas):
    """Reparte flowables en columnas [(x, y_arriba, ancho, alto)]. Devuelve posiciones o None si no cabe."""
    pos, k = [], 0
    x, ytop, w, h = columnas[0]
    usado = 0
    for u in unidades:
        _, uh = u.wrap(w, 10_000)
        sep = 5 if usado else 0
        while usado + sep + uh > h:
            k += 1
            if k >= len(columnas):
                return None
            x, ytop, w, h = columnas[k]
            usado, sep = 0, 0
            _, uh = u.wrap(w, 10_000)
        pos.append((u, x, ytop - usado - sep - uh, w))
        usado += sep + uh
    return pos


# ------------------------------------------------------------------------------------- contenido
ZONAS = [
    {"n": 1, "titulo": "Acceso y Recepción", "sub": "Arriba al centro: la entrada desde el edificio existente.",
     "vista": (585, 330, 1065, 720), "dpi": 170,
     "marcas": [(1, 800, 352, 800, 392), (2, 770, 445, 760, 428), (3, 690, 620, 697, 600), (4, 810, 540, 790, 572),
                (5, 655, 330, 655, 352), (6, 1030, 470, 1000, 455), (7, 580 + 32, 470, 640, 470)],
     "texto": [
         (1, "Puerta doble.", "Dos arcos = dos hojas. Comunica con la zona de recepción existente (lo gris de arriba a "
          "la izquierda)."),
         (2, "Cadena de la puerta.", "0,59 · 1,05 · 2,00 · 1,05: tramos de pared y el vano de 2,00 de la puerta."),
         (3, "Recepción sin muros.", "El rectángulo es <b>segmentado</b>: marca una zona dentro del hall, no un "
          "recinto cerrado. No hay tabique que presupuestar."),
         (4, "Mesones y sillas.", "Dos mesones con 4 sillas de visita y 2 de atención. Es mobiliario."),
         (5, "Pilar con marco.", "Pilar de 0,80 dentro de un rectángulo de línea fina. Se explica en la página 4."),
         (6, "Círculo Ø1,50.", "Giro de silla de ruedas frente a la puerta de la Sala 01."),
         (7, "Tabique de las oficinas.", "Cierra la fila de oficinas hacia el hall. Es vertical; sus puertas abren hacia "
          "aquí."),
     ],
     "cuenta": "Recepción: 5,84 × 3,00 = 17,52 m² ✓ (rótulo 17,52).<br/>Ojo: esos 17,52 m² <b>son parte del hall</b>. "
               "Si mides el piso del hall completo, la Recepción ya está adentro: no la sumes dos veces.",
     "prueba": "¿Cuántas sillas dibuja la Recepción?"},
    {"n": 2, "titulo": "Oficinas 01 a 05 y Guardar", "sub": "La columna de la izquierda del plano.",
     "vista": (395, 340, 705, 1262), "dpi": 110, "alto": True,
     "marcas": [(1, 410, 800, 432, 800), (2, 408, 590), (3, 545, 498, 545, 512), (4, 545, 617, 545, 631),
                (5, 600, 700, 620, 718), (6, 690, 560, 640, 560), (7, 470, 935, 485, 905), (8, 470, 1170, 500, 1130)],
     "texto": [
         (1, "Muro de fondo inclinado.", "No es vertical: se abre 1,7° hacia abajo. Por eso cada oficina es un poco "
          "más ancha que la de arriba."),
         (2, "Cadena vertical.", "0,12 · 3,00 · 0,12 · 3,00 · 0,12 · 2,08 …: tabique, Oficina 05, tabique, Oficina 04, "
          "y así hacia abajo. Da el fondo de cada oficina."),
         (3, "Ancho arriba.", "4,24: ancho de la Oficina 04 en su borde de arriba."),
         (4, "Ancho abajo.", "4,32: el mismo ancho en su borde de abajo. 8 cm más, por la inclinación."),
         (5, "Pilar dentro de la Oficina 03.", "0,68 × 0,68: la parte del pilar que entra en la oficina. Se descuenta "
          "del piso."),
         (6, "Ventana al pasillo.", "Donde el achurado se corta y queda una línea doble: vano de 1,20 en el tabique."),
         (7, "Guardar.", "Bodega de 6,41 m² con su puerta de 0,80."),
         (8, "Oficina 01.", "La más grande de la fila: 20,64 m², con 6,08 de fondo."),
     ],
     "cuenta": "Oficina 04, que es un trapecio: (4,24 + 4,32) / 2 × 3,00 = 4,28 × 3,00 = 12,84 m² ≈ 12,82 del "
               "rótulo ✓.<br/>Oficina 03: (4,32 + 4,39) / 2 × 2,08 − 0,68 × 0,68 = 9,06 − 0,46 = 8,60 ≈ 8,61 ✓ "
               "(4,39 = 3,71 + 0,68 del pilar).",
     "prueba": "Calcula la Oficina 02: arriba mide 4,39 (3,71 + 0,68), abajo 4,45 y tiene 1,98 de fondo. ¿Te da el "
               "rótulo?"},
    {"n": 3, "titulo": "Salas de Capacitación 01 y 02", "sub": "Arriba del plano: las dos salas grandes del norte.",
     "vista": (880, 40, 1785, 380), "dpi": 110,
     "marcas": [(1, 960, 52, 990, 67), (2, 1240, 100, 1240, 86), (3, 985, 112, 1003, 112), (4, 1130, 310, 1130, 293),
                (5, 1775, 215, 1758, 215), (6, 1665, 230, 1688, 230), (7, 1346, 300, 1346, 280)],
     "texto": [
         (1, "Cadena de arriba.", "0,12 · 2,80 · 0,12 · 6,54 · 0,12 · 9,18: los anchos de las dos salas con sus "
          "tabiques."),
         (2, "Borde segmentado.", "El lado de arriba de las salas es una <b>línea segmentada</b>, no un tabique "
          "achurado. Es un límite que el plano no construye: hay que confirmar qué hay ahí."),
         (3, "Escalón de la Sala 01.", "La sala es una L: la parte izquierda llega a 4,66 y la derecha a 6,70 (el "
          "escalón mide 1,36)."),
         (4, "Ancho de la Sala 01.", "9,46 de muro a muro en su parte baja."),
         (5, "Fachada inclinada.", "La línea doble gruesa de la derecha es la fachada. Va a 4° (página 2)."),
         (6, "Fondo de la Sala 02.", "6,37 de fondo, medido en su lado derecho."),
         (7, "Tabique entre salas.", "Separa la Sala 01 de la 02; baja hasta el pilar."),
     ],
     "cuenta": "Cadena de arriba: 0,12 + 2,80 + 0,12 + 6,54 + 0,12 = 9,70 hasta el inicio de la Sala 02.<br/>"
               "La Sala 02 toca la fachada inclinada: no es un rectángulo. Para sus m² usa el rótulo (55,81).",
     "prueba": "¿Cuánto suma la cadena de arriba completa, incluida la Sala 02?"},
    {"n": 4, "titulo": "Salas de Capacitación 03, 04 y 05", "sub": "A la derecha del plano, contra la fachada "
     "inclinada.", "vista": (1270, 340, 1790, 1262), "dpi": 110, "alto": True,
     "marcas": [(1, 1752, 600, 1727, 600), (2, 1500, 760, 1500, 736), (3, 1300, 690, 1322, 712),
                (4, 1300, 1050, 1298, 1068), (5, 1500, 880, 1500, 900), (6, 1395, 455, 1395, 468),
                (7, 1290, 600, 1300, 620)],
     "texto": [
         (1, "Fachada inclinada.", f"Va a {m2(ANG).replace(',00', '')}° de la vertical. Cada sala es más ancha "
          "arriba que abajo."),
         (2, "Muro entre salas.", "7,22 de pilar a pilar. La cota se repite en cada fila porque los dos pilares se "
          "corren lo mismo."),
         (3, "Escalón en el pilar.", f"El tabique del pasillo es vertical y se corre {m2(CORRIMIENTO)} m en cada fila "
          "de pilares para seguir el eje. Así se resolvió la inclinación (página 2)."),
         (4, "Otro escalón.", "Lo mismo en la fila de abajo."),
         (5, "Rótulo.", "Sala 04, Sup. útil 64,93 m²: la más grande de este lado."),
         (6, "Entrada a la Sala 03.", "La sala hace una L hacia el pasillo (2,18 de ancho)."),
         (7, "Círculo Ø1,50.", "Giro de silla de ruedas frente a cada puerta."),
     ],
     "cuenta": f"Cuánto se corre la fachada en el fondo de una sala: fondo × tan {m2(ANG)}° = fondo × 0,0705.<br/>"
               "Sala 02 (6,37 de fondo): 6,37 × 0,0705 = 0,45 m. La sala es 45 cm más ancha en un extremo "
               "que en el otro.",
     "prueba": "La Sala 05 tiene 3,64 de fondo. ¿Cuánto se corre la fachada a lo largo de ella?"},
    {"n": 5, "titulo": "Salas 06 y 07, pasillo y muro sur", "sub": "Al centro y abajo del plano.",
     "vista": (560, 680, 1340, 1334), "dpi": 100,
     "marcas": [(1, 820, 760, 820, 790), (2, 1040, 700, 1000, 720), (3, 1160, 880, 1160, 897),
                (4, 780, 1205, 780, 1222), (5, 900, 1300, 950, 1290), (6, 990, 960, 972, 960),
                (7, 900, 1065, 938, 1080)],
     "texto": [
         (1, "Sala 07.", "La única sala interior: 5,60 × 4,43, sin fachada."),
         (2, "Pilar con mueble.", "Pilar dentro de un rectángulo de línea fina con un mesón arriba. Página 4."),
         (3, "Ancho del pasillo.", "7,78 entre la Sala 06 y la Sala 04: es un hall amplio, no un pasillo angosto."),
         (4, "Ancho de la Sala 06.", "8,58 de muro a muro; su fondo es 7,88 (cota vertical de la izquierda)."),
         (5, "Muro sur.", "Lo que se dibuja bajo la línea gruesa de la fachada no es un acceso: es muro "
          "(Gonza, 17-10-2026)."),
         (6, "Ventanas al pasillo.", "Los cortes del achurado en el muro de la Sala 06 son ventanas hacia el hall."),
         (7, "Pilar en el muro.", "Los pilares quedan dentro de la línea del tabique: el tabique se corta en el "
          "pilar."),
     ],
     "cuenta": "Sala 07: 5,60 × 4,43 = 24,81 ≈ 24,80 del rótulo ✓.<br/>Guardapolvo de la Sala 07 (sin descontar "
               "puertas): 2 × (5,60 + 4,43) = 20,06 ml.",
     "prueba": "¿Cuántos ml de guardapolvo lleva la Sala 06 (8,58 × 7,88), sin descontar puertas?"},
]
RESPUESTAS = [
    ("Zona 1. ¿Cuántas sillas dibuja la Recepción?", "6: 4 de visita arriba y 2 de atención detrás de los mesones."),
    ("Zona 2. Oficina 02.", "(4,39 + 4,45) / 2 × 1,98 = 4,42 × 1,98 = 8,75 m² ≈ 8,74 del rótulo ✓."),
    ("Zona 3. Cadena completa.", "0,12 + 2,80 + 0,12 + 6,54 + 0,12 + 9,18 = 18,88 m."),
    ("Zona 4. Sala 05.", "3,64 × 0,0705 = 0,26 m."),
    ("Zona 5. Sala 06.", "2 × (8,58 + 7,88) = 32,92 ml."),
    ("Página 2. Corrimiento por fila.", f"8,00 × 0,0705 = {m2(CORRIMIENTO)} m."),
]
PENDIENTES = [
    "<b>Baños.</b> El plano no dibuja ninguno. ¿Están en el edificio existente o faltan en esta versión?",
    "<b>Alturas.</b> No hay altura de piso a cielo ni cortes: sin ella no salen los m² de tabique ni de pintura.",
    "<b>Marcos de los pilares.</b> Los dos pilares con rectángulo de línea fina (página 4): ¿forro de tabique, "
    "mueble o shaft?",
    "<b>Borde de arriba de las Salas 01 y 02.</b> Es una línea segmentada: ¿qué muro o ventanal hay ahí hoy?",
    "<b>Muro de fondo de las oficinas.</b> Va a 1,7°, distinto de los 4° de la estructura: ¿sigue un muro existente?",
    "<b>Ventanas de la fachada.</b> No están dibujadas: ¿se mantienen las existentes?",
    "<b>Mobiliario.</b> ¿Los mesones de la Recepción y el mesón del pilar central entran en el presupuesto?",
]

c = canvas.Canvas(SALIDA, pagesize=(ANCHO, ALTO))
c.setTitle("La habilitación Preuniversitario Talca, explicada")
c.setAuthor("Alma, para Gonza")
pagina = 0
avisos = []


def nueva_pagina():
    global pagina
    if pagina:
        c.showPage()
    pagina += 1


def titulo_pagina(t, sub=None):
    c.setFillColor(AZUL)
    c.setFont("DvB", 17)
    c.drawString(MARGEN, ALTO - MARGEN - 14, t)
    if sub:
        c.setFillColor(GRIS)
        c.setFont("Dv", 9.5)
        c.drawString(MARGEN, ALTO - MARGEN - 30, sub)
    c.setFillColor(NEGRO)


def dibujar(ubic):
    for u, ux, uy, uw in ubic:
        u.wrap(uw, 10_000)
        u.drawOn(c, ux, uy)


CLIP_MAPA = pt(150, 0, 1800, 1334)
mapa_png = recorte("mapa", CLIP_MAPA, 45)


def mapa(x, y, w, destacar=None):
    h = w * (CLIP_MAPA[3] - CLIP_MAPA[1]) / (CLIP_MAPA[2] - CLIP_MAPA[0])
    c.drawImage(mapa_png, x, y, w, h)
    c.setStrokeColor(HexColor("#B0B0B0"))
    c.setLineWidth(0.5)
    c.rect(x, y, w, h)
    s = w / (CLIP_MAPA[2] - CLIP_MAPA[0])
    for z in ZONAS:
        if destacar is not None and z["n"] != destacar:
            continue
        x0, y0, x1, y1 = pt(*z["vista"])
        c.setStrokeColor(ROJO)
        c.setLineWidth(1.4 if destacar else 1.1)
        c.rect(x + (x0 - CLIP_MAPA[0]) * s, y + h - (y1 - CLIP_MAPA[1]) * s, (x1 - x0) * s, (y1 - y0) * s,
               stroke=1, fill=0)
        if destacar is None:
            marcador(c, x + (x0 - CLIP_MAPA[0]) * s + 9, y + h - (y0 - CLIP_MAPA[1]) * s - 9, z["n"], r=7.5)
    return h


# 1. Portada
nueva_pagina()
titulo_pagina("La habilitación Preuniversitario Talca, explicada",
              "Qué se construye y cómo leer su plano · preparada por Alma para Gonza, 16-10-2026 · entregable inicial")
col_w = 370
bloques = [
    Paragraph("Qué es la obra", SUBT),
    Paragraph("La habilitación de una planta libre para un preuniversitario: <b>7 salas de capacitación</b>, "
              "<b>5 oficinas</b>, una <b>Recepción</b> abierta y un <b>Guardar</b> (bodega), alrededor de un hall "
              "central. Los 14 rótulos suman <b>449,71 m² útiles</b>; el hall no tiene rótulo. Se entra desde la "
              "recepción existente del edificio (arriba a la izquierda, en gris).", CUERPO),
    Paragraph("Lo que ya está y lo que se hace", SUBT),
    Paragraph("• Ya están: los <b>15 pilares</b> (cuadrados grises de 0,80) en una grilla de unos 8 × 8 m, "
              "y la fachada.<br/>• Se hace: <b>todos los tabiques</b> del plano (los achurados), puertas y ventanas "
              "interiores.<br/>• Lo gris de arriba a la izquierda es el edificio en uso: queda fuera.", CUERPO),
    Paragraph("Tres reglas de este plano", SUBT),
    Paragraph("1. Las medidas están en metros y usan punto: <b>8.58</b> son 8,58 m.<br/>2. <b>Achurado = tabique "
              "nuevo.</b> Línea fina sin achurado = mueble, zona o límite: no es tabique.<br/>3. La <b>Sup. útil</b> "
              "rotulada es la superficie interior: sirve para comprobar tus cuentas.", CUERPO),
    caja("Lo que hay que entender primero", f"La estructura del edificio está <b>girada {m2(ANG)}°</b>: los ejes de "
         "los pilares y la fachada derecha no son verticales. Los tabiques sí van a escuadra. La página 2 explica "
         "cómo se resuelve.", FONDO_OJO),
    Paragraph("Cómo usar esta guía", SUBT),
    Paragraph("Página 2: la inclinación. Página 3: los símbolos. Página 4: la Recepción y los marcos de los pilares. "
              "Páginas 5 a 9: el plano zona por zona, con la cuenta y una pregunta para practicar. Página 10: lo que "
              "falta para presupuestar y las respuestas.", CUERPO),
]
ubic = empacar(bloques, [(MARGEN, ALTO - MARGEN - 48, col_w, 500)])
if ubic:
    dibujar(ubic)
else:
    avisos.append("la portada no cabe")
mx = MARGEN + col_w + 20
mw = ANCHO - MARGEN - mx
mh = mw * (CLIP_MAPA[3] - CLIP_MAPA[1]) / (CLIP_MAPA[2] - CLIP_MAPA[0])
mapa(mx, ALTO - MARGEN - 48 - mh, mw)
c.setFont("Dv", 8)
c.setFillColor(GRIS)
c.drawCentredString(mx + mw / 2, ALTO - MARGEN - 60 - mh, "Las 5 zonas de la guía sobre el plano.")
y_ley = ALTO - MARGEN - 60 - mh - 26
c.setFillColor(AZUL)
c.setFont("DvB", 10.5)
c.drawString(mx, y_ley, "Las 5 zonas")
for k, z in enumerate(ZONAS):
    ly = y_ley - 18 - k * 15
    marcador(c, mx + 6, ly + 3, z["n"], r=6.5)
    c.setFillColor(NEGRO)
    c.setFont("Dv", 8.6)
    c.drawString(mx + 17, ly, f"{z['titulo']} (página {k + 5})")
pie(c, pagina)

# 2. La inclinación
nueva_pagina()
titulo_pagina("La inclinación: qué es y cómo se resuelve",
              "Medida en las líneas del plano. El esquema de la izquierda está a escala; el giro es el real.")

# 2a. Esquema: grilla de pilares girada, tabique del pasillo escalonado, fachada inclinada
ex, ey_top, esc = MARGEN + 30, ALTO - MARGEN - 70, 9.0  # pt por metro
filas, cols = 3, 5
t = math.tan(math.radians(ANG))


def punto(xm, ym):
    """Metros (x a la derecha, y hacia abajo desde la fila de arriba) a puntos de la hoja."""
    return ex + xm * esc, ey_top - ym * esc


c.setFont("DvB", 10.5)
c.setFillColor(AZUL)
c.drawString(MARGEN, ey_top + 22, "Planta de la estructura, a escala")
alto_m = (filas - 1) * 8 + 4
for f in range(filas):  # ejes horizontales
    y = f * 8 + 2
    c.setStrokeColor(GRIS)
    c.setDash([6, 2, 1, 2])
    c.setLineWidth(0.5)
    c.line(*punto(-2, y), *punto(cols * 8 - 4, y))
for k in range(cols):  # ejes de pilares, girados
    x_arriba = k * 8
    c.line(*punto(x_arriba + t * 0, 0), *punto(x_arriba - t * alto_m, alto_m))
c.setDash()
for f in range(filas):
    for k in range(cols):
        y = f * 8 + 2
        x = k * 8 - t * y
        c.setFillColor(HexColor("#C7C7C9"))
        c.setStrokeColor(NEGRO)
        c.setLineWidth(0.8)
        x0, y0 = punto(x - 0.4, y + 0.4)
        c.rect(x0, y0, 0.8 * esc, 0.8 * esc, stroke=1, fill=1)
# fachada: paralela a los ejes, 0,5 m a la derecha del último eje
fx = (cols - 1) * 8 + 0.5
c.setStrokeColor(NEGRO)
c.setLineWidth(2.2)
c.line(*punto(fx, 0), *punto(fx - t * alto_m, alto_m))
# tabique del pasillo: vertical por tramos, pegado a la cara izquierda del pilar del eje 3, con escalón en cada fila
c.setStrokeColor(ROJO)
c.setLineWidth(2.2)
eje = 3
for f in range(filas - 1):
    y_a, y_b = f * 8 + 2.4, (f + 1) * 8 + 1.6
    x = eje * 8 - t * (f * 8 + 2) - 0.4
    c.line(*punto(x, y_a), *punto(x, y_b))
    c.setLineWidth(1)
    c.line(*punto(x, y_b), *punto(x - CORRIMIENTO, y_b))
    c.setLineWidth(2.2)
# tabiques horizontales a escuadra que llegan a la fachada
for f in range(1, filas):
    y = f * 8 + 2
    c.line(*punto(eje * 8 - t * y + 0.4, y), *punto(fx - t * y, y))
c.setFont("Dv", 7.5)
c.setFillColor(NEGRO)
lx, ly = punto(fx - t * 4 + 0.6, 4)
c.drawString(lx, ly, "fachada")
lx, ly = punto(-2, alto_m + 1.6)
c.drawString(lx, ly, "Gris: pilares de 0,80 y ejes (punto y raya). Rojo: tabiques nuevos. Negro grueso: fachada.")
# rótulo del ángulo sobre el eje de la izquierda
lx, ly = punto(0.3, alto_m - 1)
c.setFillColor(ROJO)
c.setFont("DvB", 8)
c.drawString(lx, ly, f"{m2(ANG)}°")
ancho_esq = (cols * 8 - 2) * esc

# 2b. Recorte real del escalón
clip_esc = pt(1280, 670, 1380, 770)
png_esc = recorte("escalon", clip_esc, 250)
ry = ey_top - alto_m * esc - 40 - 150
c.setFont("DvB", 9)
c.setFillColor(AZUL)
c.drawString(MARGEN, ry + 158, "Así se ve en el plano (Sala 04, fila del medio)")
imagen_con_numeros(c, png_esc, clip_esc, MARGEN, ry, 150, 150,
                   [(1, 1300, 680, 1347, 695), (2, 1295, 755, 1319, 755)])
txt = Paragraph(f"{num(1)} El tabique llega vertical a la cara del pilar.<br/>{num(2)} Sale del otro lado "
                f"{m2(CORRIMIENTO)} m más a la izquierda, otra vez vertical. El pilar esconde el escalón.", CUERPO_CH)
txt.wrap(150, 200)
txt.drawOn(c, MARGEN + 160, ry + 150 - txt.height)

# 2c. Texto
col_x = MARGEN + max(ancho_esq + 40, 340)
col_w2 = ANCHO - MARGEN - col_x
bloques = [
    Paragraph("Qué es", SUBT),
    Paragraph(f"Los pilares están alineados en filas horizontales cada unos 8 m, pero sus <b>ejes verticales "
              f"están girados {m2(ANG)}°</b>: cada fila de pilares está {m2(CORRIMIENTO)} m más a la izquierda que la "
              "de arriba. La fachada derecha sigue esos ejes: en sus 27,5 m de largo se corre <b>1,94 m</b>. El "
              "edificio es un romboide, no un rectángulo.", CUERPO),
    Paragraph("Cómo lo resolvió el arquitecto", SUBT),
    Paragraph(f"Los tabiques <b>no se giran</b>: van verticales y horizontales, a escuadra. Donde un tabique tiene "
              f"que seguir la línea de pilares, avanza vertical hasta un pilar y sale del otro lado corrido "
              f"{m2(CORRIMIENTO)} m: <b>el pilar esconde el escalón</b>. Solo las salas que tocan la fachada "
              "quedan con un lado inclinado.", CUERPO),
    Paragraph("Cómo se mide", SUBT),
    Paragraph("Un recinto con un lado inclinado es un <b>trapecio</b>: mide el ancho en los dos bordes y usa el "
              "promedio. Área = (ancho arriba + ancho abajo) / 2 × fondo. Con un solo ancho te equivocas en "
              "hasta 1,4 m² en una sala como la 02. El largo del muro inclinado casi no cambia: fondo / cos 4° es 0,25% más.",
              CUERPO),
    Paragraph("Cómo se traza en obra", SUBT),
    Paragraph("Nunca desde la fachada. Se elige una <b>línea maestra</b> a escuadra (el tabique del hall) y todo se "
              "mide desde ella. La escuadra se comprueba con el <b>3-4-5</b>: 3 m sobre la línea, 4 m hacia el lado; "
              "si la diagonal da 5 m, el ángulo es recto. Donde un tabique llega a la fachada el encuentro queda a "
              "86° o 94°: el último perfil se fija a la fachada y la plancha se corta en ángulo.", CUERPO),
    caja("Prueba tú", f"¿Cuánto se corre el eje entre dos filas de pilares (8,00 m), si tan {m2(ANG)}° = 0,0705?",
         FONDO_PRUEBA),
]
ubic = empacar(bloques, [(col_x, ALTO - MARGEN - 48, col_w2, ALTO - MARGEN - 48 - 30)])
if ubic:
    dibujar(ubic)
else:
    avisos.append("la página 2 no cabe")
pie(c, pagina)

# 3. Símbolos
nueva_pagina()
titulo_pagina("Los símbolos del plano", "Cada ejemplo está recortado de este mismo plano. «Para el presupuesto» dice "
              "qué hacer con lo que ves.")
SIMBOLOS = [
    ("Tabique nuevo", (1350, 715, 1530, 740), "Doble línea con achurado diagonal fino. Todos los del plano son "
     "nuevos.", "m² = largo × alto (el alto falta)."),
    ("Pilar y eje", (1295, 675, 1375, 755), "Cuadrado gris de 0,80 y línea de punto y raya: ya existe.",
     "No se construye. Se descuenta del piso donde entra a un recinto."),
    ("Cadena de cotas", (680, 418, 880, 436), "Medidas en metros, una tras otra: 0,59 · 1,05 · 2,00 · 1,05.",
     "De aquí salen largos y anchos."),
    ("Rótulo de recinto", (1440, 535, 1600, 565), "Nombre y «Sup. útil»: la superficie interior.",
     "Va directo a piso y cielo, y comprueba tus cuentas."),
    ("Zona segmentada", (690, 545, 960, 700), "Rectángulo de línea segmentada con muebles: la Recepción.",
     "No lleva tabique. Su superficie es parte del hall."),
    ("Puerta doble", (745, 360, 860, 420), "Dos arcos: dos hojas. Vano de 2,00.", "Se cuenta por unidad."),
    ("Ventana al pasillo", (628, 495, 652, 615), "El achurado se corta y queda una línea doble.",
     "Vano en el tabique: ancho del plano × alto (falta)."),
    ("Círculo Ø1,50", (1270, 580, 1350, 660), "Giro de silla de ruedas: el espacio libre que pide la norma.",
     "No se construye; obliga a dejar ese espacio libre."),
]
cw, ch, gap = (ANCHO - 2 * MARGEN - 3 * 12) / 4, 236, 12
for i, (tit, vista, que, pres) in enumerate(SIMBOLOS):
    col, fila = i % 4, i // 4
    x = MARGEN + col * (cw + gap)
    y = ALTO - MARGEN - 48 - (fila + 1) * ch - fila * gap
    c.setStrokeColor(HexColor("#C8C8C8"))
    c.setLineWidth(0.7)
    c.roundRect(x, y, cw, ch, 6, stroke=1, fill=0)
    c.setFillColor(AZUL)
    c.setFont("DvB", 9.5)
    c.drawString(x + 9, y + ch - 17, tit)
    clip = pt(*vista)
    png = recorte(f"sim_{i}", clip, 200)
    caja_w, caja_h = cw - 18, 120
    c.setFillColor(HexColor("#F4F4F4"))
    c.rect(x + 9, y + ch - 27 - caja_h, caja_w, caja_h, stroke=0, fill=1)
    rw, rh = clip[2] - clip[0], clip[3] - clip[1]
    s = min(caja_w / rw, caja_h / rh)
    c.drawImage(png, x + 9 + (caja_w - rw * s) / 2, y + ch - 27 - caja_h + (caja_h - rh * s) / 2, rw * s, rh * s)
    p1 = Paragraph(f"<b>Qué es:</b> {que}", CUERPO_CH)
    p2 = Paragraph(f'<font color="#1F3A5F"><b>Para el presupuesto:</b> {pres}</font>', CUERPO_CH)
    _, h1 = p1.wrap(caja_w, 200)
    p1.drawOn(c, x + 9, y + ch - 33 - caja_h - h1)
    _, h2 = p2.wrap(caja_w, 200)
    p2.drawOn(c, x + 9, y + ch - 37 - caja_h - h1 - h2)
pie(c, pagina)

# 4. Recepción y pilares con marco
nueva_pagina()
titulo_pagina("Lo que llama la atención: la Recepción y los pilares con marco",
              "Medido a escala sobre el plano (no tienen cota). Qué son exactamente lo confirma Luciano.")
PIEZAS = [
    ("La Recepción", (690, 545, 960, 700), 170,
     [(1, 700, 560, 697, 580), (2, 760, 556, 760, 572), (3, 790, 640, 790, 625)],
     f"{num(1)} El borde es <b>segmentado</b>: no hay muro. Es una zona marcada dentro del hall.<br/>"
     f"{num(2)} 4 sillas de visita frente a los mesones.<br/>{num(3)} Dos mesones en T con una silla de atención "
     "cada uno.<br/><b>Para el presupuesto:</b> nada de tabique. El piso va con el hall. Los mesones son "
     "mobiliario: preguntar si entran."),
    ("Pilar de la entrada", (615, 335, 700, 405), 300,
     [(1, 612, 345, 627, 352), (2, 698, 345, 684, 360), (3, 612, 400, 640, 395)],
     f"{num(1)} El pilar gris (0,80) queda dentro de un <b>marco de línea fina</b> de unos 1,31 × 1,19 m.<br/>"
     f"{num(2)} A su derecha, un compartimento de unos 0,38 m de ancho.<br/>{num(3)} Los tabiques achurados "
     "llegan al marco, no al pilar.<br/><b>Puede ser:</b> un forro de tabique que envuelve el pilar con un "
     "espacio para ductos o tableros. El marco no lleva achurado: no está dibujado como tabique."),
    ("Pilar del hall (junto a la Sala 07)", (950, 685, 1030, 770), 300,
     [(1, 1030, 700, 1012, 702), (2, 1030, 735, 1000, 735), (3, 1030, 760, 1010, 752)],
     f"{num(1)} Arriba del pilar, una franja de unos 0,44 m con círculos y un rectángulo: parece un "
     "<b>mesón con lavaplatos</b> o un punto de agua.<br/>"
     f"{num(2)} El marco completo mide unos 1,33 × 1,60 m.<br/>{num(3)} Abajo, un mueble bajo de unos 0,92 × "
     "0,35 m.<br/><b>Puede ser:</b> un mueble de cafetería apoyado en el pilar. Si lleva agua, suma una "
     "instalación sanitaria que el plano no muestra."),
]
pw = (ANCHO - 2 * MARGEN - 2 * 16) / 3
for i, (tit, vista, dpi, marcas, texto) in enumerate(PIEZAS):
    x = MARGEN + i * (pw + 16)
    ytop = ALTO - MARGEN - 50
    c.setFillColor(AZUL)
    c.setFont("DvB", 10.5)
    c.drawString(x, ytop - 10, tit)
    clip = pt(*vista)
    png = recorte(f"pieza_{i}", clip, dpi)
    rw, rh = clip[2] - clip[0], clip[3] - clip[1]
    iw = pw
    ih = min(rh * iw / rw, 230)
    iw = rw * ih / rh
    imagen_con_numeros(c, png, clip, x + (pw - iw) / 2, ytop - 20 - ih, iw, ih, marcas)
    p = Paragraph(texto, CUERPO)
    _, h = p.wrap(pw, 400)
    p.drawOn(c, x, ytop - 32 - ih - h)
regla = caja("La regla para leerlos", "En este plano <b>el tabique siempre va achurado</b>. Una línea fina sin "
             "achurado es otra cosa: mueble, zona o límite. Por eso los marcos de los pilares no se cubican como "
             "tabique hasta que Luciano diga qué son.", FONDO_OJO)
regla.wrap(ANCHO - 2 * MARGEN, 100)
regla.drawOn(c, MARGEN, 40)
pie(c, pagina)

# 5-9. Zonas
for z in ZONAS:
    nueva_pagina()
    titulo_pagina(f"Zona {z['n']} · {z['titulo']}", z["sub"])
    clip = pt(*z["vista"])
    png = recorte(f"zona_{z['n']}", clip, z["dpi"])
    rw, rh = clip[2] - clip[0], clip[3] - clip[1]
    # mapa de ubicación arriba a la derecha
    mw_ch = 100
    mapa(ANCHO - MARGEN - mw_ch, ALTO - MARGEN - 8 - mw_ch * (CLIP_MAPA[3] - CLIP_MAPA[1]) /
         (CLIP_MAPA[2] - CLIP_MAPA[0]), mw_ch, destacar=z["n"])
    textos = [Paragraph("Qué estás viendo", SUBT)] + [
        Paragraph(f"{num(n)} <b>{t}</b> {d}", CUERPO) for n, t, d in z["texto"]]
    cajas = [caja("La cuenta", z["cuenta"], FONDO_CUENTA),
             caja("Prueba tú", z["prueba"] + ' <font color="#6B6B6B">(Respuesta al final de la guía.)</font>',
                  FONDO_PRUEBA)]
    if z.get("alto"):  # recorte alto: imagen a la izquierda, texto a la derecha
        ih = ALTO - MARGEN - 48 - 30
        iw = rw * ih / rh
        imagen_con_numeros(c, png, clip, MARGEN, 30, iw, ih, z["marcas"])
        x_t = MARGEN + iw + 18
        ancho_t = ANCHO - MARGEN - x_t
        cols_t = [(x_t, ALTO - MARGEN - 48 - 75, ancho_t * 0.55, ALTO - MARGEN - 48 - 75 - 30),
                  (x_t + ancho_t * 0.55 + 12, ALTO - MARGEN - 48 - 75, ancho_t * 0.45 - 12, ALTO - MARGEN - 48 - 105)]
        ubic = empacar(textos + cajas, cols_t)
    else:
        iw = ANCHO - 2 * MARGEN - 110
        ih = rh * iw / rw
        if ih > 300:
            ih = 300
            iw = rw * ih / rh
        imagen_con_numeros(c, png, clip, MARGEN, ALTO - MARGEN - 48 - ih, iw, ih, z["marcas"])
        y_t = ALTO - MARGEN - 48 - ih - 14
        ancho_t = ANCHO - 2 * MARGEN
        a1, a3 = ancho_t * 0.33, ancho_t * 0.32
        cols_t = [(MARGEN, y_t, a1, y_t - 30), (MARGEN + a1 + 12, y_t, a1, y_t - 30),
                  (MARGEN + 2 * a1 + 24, y_t, ancho_t - 2 * a1 - 24, y_t - 30)]
        ubic_t = empacar(textos, cols_t[:2])
        ubic_c = empacar(cajas, cols_t[2:])
        ubic = (ubic_t + ubic_c) if (ubic_t and ubic_c) else None
    if ubic:
        dibujar(ubic)
    else:
        avisos.append(f"la zona {z['n']} no cabe")
    pie(c, pagina)

# 10. Lo que falta y respuestas
nueva_pagina()
titulo_pagina("Lo que falta para presupuestar", "Preguntas que decide Luciano, y las respuestas de «Prueba tú».")
mitad = (ANCHO - 2 * MARGEN - 20) / 2
izq = [Paragraph("Preguntas para Luciano", SUBT)] + [Paragraph(f"{k + 1}. {p}", CUERPO) for k, p in
                                                     enumerate(PENDIENTES)]
izq.append(caja("Lo que sí se puede cubicar hoy", "Pisos y cielos de los 14 recintos (rótulos), el hall por "
                "diferencia, los largos de todos los tabiques (cadenas de cotas), puertas y ventanas interiores por "
                "unidad y guardapolvos. Falta la altura para pasar los tabiques a m².", FONDO_CUENTA))
der = [Paragraph("Respuestas de «Prueba tú»", SUBT)] + [Paragraph(f"<b>{q}</b><br/>→ {r}", CUERPO)
                                                        for q, r in RESPUESTAS]
for bloque, x in ((izq, MARGEN), (der, MARGEN + mitad + 20)):
    ubic = empacar(bloque, [(x, ALTO - MARGEN - 48, mitad, ALTO - MARGEN - 48 - 30)])
    if ubic:
        dibujar(ubic)
    else:
        avisos.append("la página final no cabe")
pie(c, pagina)

c.save()
print(f"{SALIDA}: {pagina} páginas")
for a in avisos:
    print("AVISO:", a)
sys.exit(1 if avisos else 0)
